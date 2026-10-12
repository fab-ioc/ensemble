"""ED-212: an owner waiting on a command of its own is not stalled.

On 2026-10-12 the progress check told the PO twice that a task was stalled
while its owner waited for its own test suite: Claude ended the turn with
"done 3:45 PM · 1 shell still running" and wakes the agent itself when the
shell ends, so the hub's nudge was noise and the PO's line wrong.

The fixtures are real screens, as ``ptyrun.tail`` reads them:

* ``own_work_one_shell_211.txt``: #211 at 15:45, "The full suite is running
  again (~30 min)" — one of the two examples in the task.
* ``own_work_two_shells.txt``: the same shape with two shells (a task's
  dying screen kept on its room, 10:52), the hub's nudge typed into it and the
  agent going back to waiting. #209's own screen of 13:47 had already left
  the hub's buffer when this was built.
* ``own_work_two_shells_relay.txt``: two shells, a relay line typed under the
  footer, and the agent waiting again with letters lost in the repaint.
* ``own_work_shell_ended.txt``: the footer without a count, then
  "● Background command … completed": nothing of its own still runs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import attention
import chatroom
import dashboard
import ensemble_tools
import stall
from test_ask_to_po import _Stall
from test_needs_you_real import _js_fn
from test_open_ask import _Bell, IDLE_SCREEN

FIX = Path(__file__).parent / "fixtures"
INDEX = (Path(__file__).parent.parent / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def _fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


ONE_SHELL = _fixture("own_work_one_shell_211.txt")
TWO_SHELLS = _fixture("own_work_two_shells.txt")
TWO_SHELLS_RELAY = _fixture("own_work_two_shells_relay.txt")
SHELL_ENDED = _fixture("own_work_shell_ended.txt")
CAP = attention.OWN_WORK_CAP_S


class ReadingTheScreen(unittest.TestCase):
    def test_the_real_screens(self):
        self.assertEqual(attention.own_work(ONE_SHELL), 1)
        self.assertEqual(attention.own_work(TWO_SHELLS), 2)
        self.assertEqual(attention.own_work(TWO_SHELLS_RELAY), 2)
        self.assertEqual(attention.own_work(SHELL_ENDED), 0)
        self.assertEqual(attention.own_work(IDLE_SCREEN), 0)
        for tail in (ONE_SHELL, TWO_SHELLS, TWO_SHELLS_RELAY):
            scan = attention.analyse(tail)
            self.assertFalse(scan["busy"] or scan["prompt"])      # idle at its prompt

    def test_a_later_footer_without_the_count_is_newer(self):
        later = ONE_SHELL + "● Done.\n✻ Worked for 2s · done 4:20 PM\n>\n"
        self.assertEqual(attention.own_work(later), 0)

    def test_prose_about_shells_is_not_a_footer(self):
        self.assertEqual(attention.own_work("● Ran 2 shell commands\n● The suite is still running.\n>"), 0)

    def test_codex_counts_its_background_terminals(self):
        tail = "• Ran the focused tests\n  · 2 background terminals running · /ps to view · /stop to close\n› "
        self.assertEqual(attention.own_work(tail), 2)


class _OwnWork(_Stall):
    def show(self, screen: str, idle: float):
        self.screen, self.idle = screen, idle
        attention._ANALYSIS.clear()


class NoNudgeWhileItWaitsOnItsOwnCommand(_OwnWork):
    def test_211_and_the_two_shell_screen(self):
        for screen in (ONE_SHELL, TWO_SHELLS, TWO_SHELLS_RELAY):
            with self.subTest(screen=screen[-60:]):
                t = time.time()
                self.show(screen, 700.0)                  # 13:35 → the 13:46 look
                self.assertEqual(stall.tick(t), {})
                self.show(screen, 20 * 60.0)             # 13:57: no PO line either
                self.assertEqual(stall.tick(t + stall.GRACE_S + 1), {})
                self.show(screen, CAP - 60)
                self.assertEqual(stall.tick(t + 2 * stall.GRACE_S), {})
                self.assertEqual((self.typed, self.told()), ([], []))
                self.assertNotIn("stalled", ensemble_tools._po_wait_view(self.room()))

    def test_the_2_h_cap_reports_the_command_as_the_reason(self):
        t = time.time()
        self.show(ONE_SHELL, CAP + 1)
        self.assertEqual(stall.tick(t), {self.rid: "nudged"})
        self.assertEqual(self.typed, [stall.NUDGE_COMMAND])
        self.assertEqual(dashboard.hub_input_kind(stall.NUDGE_COMMAND)["kind"], "stalled")
        self.show(ONE_SHELL, CAP + stall.GRACE_S + 2)
        self.assertIn("PO told", stall.tick(t + stall.GRACE_S + 1)[self.rid])
        told = self.told()
        self.assertEqual(len(told), 1)
        self.assertIn("Background command running for 2 h", told[0]["text"])
        self.assertNotIn("idle with nothing open", told[0]["text"])
        view = ensemble_tools._po_wait_view(self.room())["stalled"]
        self.assertEqual(view["reason"], "background command running for 2 h")

    def test_a_plain_idle_owner_is_nudged_and_reported_as_today(self):
        t = time.time()
        self.show(SHELL_ENDED, 700.0)                    # its shell ended: plain idle
        self.assertEqual(stall.tick(t), {self.rid: "nudged"})
        self.assertEqual(self.typed, [stall.NUDGE])
        self.assertIn("PO told", stall.tick(t + stall.GRACE_S + 1)[self.rid])
        self.assertIn("Its owner is idle with nothing open", self.told()[0]["text"])
        self.assertNotIn("reason", ensemble_tools._po_wait_view(self.room())["stalled"])

    def test_the_po_wake_line_names_the_command(self):
        t = time.time()
        self.show(TWO_SHELLS, CAP + 1)
        self.assertEqual(stall.tick(t), {self.rid: "nudged"})
        rec = stall._load()[self.rid]
        self.assertEqual(rec["command"], 2)
        typed = []
        po_room = chatroom.get_room(self.po_rid, public=False)
        chatroom.participant(po_room, "po")["ptyId"] = "pty-po"
        chatroom.update_room(po_room)
        po_sess = type(self.sess)(alive=lambda: True, last_submit=lambda: 0.0,
                                  info=lambda: {"idleSeconds": 30.0})
        owner_pty = dashboard.rotation._pty.side_effect
        dashboard.rotation._pty.side_effect = lambda p: po_sess if p.get("ptyId") == "pty-po" else owner_pty(p)
        dashboard._type_input.side_effect = lambda s, txt: typed.append(txt) or True
        self.assertIn("woken", stall.tick(t + stall.GRACE_S + 1)[self.rid])
        self.assertIn("background command running for 2 h", typed[-1])
        self.assertIn("did not carry on after the hub's nudge", typed[-1])


class TheSilentLookExcusesIt(unittest.TestCase):
    def test_excused(self):
        room = {"owners": ["claude"], "workflow": "inprogress"}
        part = {"identity": "claude"}
        ev = {"alive": True, "scan": attention.analyse(ONE_SHELL)}
        self.assertEqual(stall.silence_excuse(room, part, ev), "a background run")
        ev["scan"] = attention.analyse(SHELL_ENDED)
        self.assertEqual(stall.silence_excuse(room, part, ev), "")


class TheRowSaysRunningACommand(_Bell):
    """A task with no PO: the run state, and the CEO's bell at the cap."""

    def setUp(self):
        super().setUp()
        full = chatroom.get_room(self.rid, public=False)
        full.update(mode="collab", workflow="inprogress")
        chatroom.update_room(full)
        self.idle = 700.0
        self.sess.info = lambda: {"idleSeconds": self.idle}
        self.sess.last_output = time.time() - self.idle

    def run_state(self):
        attention._ANALYSIS.clear()
        snap = attention.snapshot(max_age=-1)
        return snap["run"].get(self.rid), snap["runSince"].get(self.rid)

    def test_running_a_command_since_its_turn_ended(self):
        self.screen = ONE_SHELL
        self.assertEqual(self.run_state(), ("command", self.sess.last_output))
        self.assertIsNone(self.item())
        self.screen = SHELL_ENDED
        self.assertEqual(self.run_state(), ("idle", None))

    def test_the_cap_rings_the_bell_with_the_command_as_the_reason(self):
        self.screen = TWO_SHELLS
        self.idle = CAP + 5
        it = self.item()
        self.assertEqual((it["state"], it["cause"]), ("stalled", "command"))
        self.assertIn("background command running for 2h", it["reason"].replace(" h", "h"))


@unittest.skipUnless(NODE, "node is not installed")
class ThePageSaysIt(unittest.TestCase):
    def test_the_run_chip_and_the_switcher_dot(self):
        since = time.mktime((2026, 10, 12, 15, 45, 0, 0, 0, -1))
        src = "\n".join([
            "const esc = s => String(s);",
            "const ATTN_LABEL = {};",
            "const attnDot = () => null;",
            "const isUnread = () => false;",
            _js_fn(INDEX, "function commandWords("),
            _js_fn(INDEX, "function commandTip("),
            _js_fn(INDEX, "function runChip("),
            _js_fn(INDEX, "function swState("),
            f"const cmd = {{isLive: true, status: 'idle', runState: 'command', runSince: {since}}};",
            "const idle = {isLive: true, status: 'idle'};",
            "console.log(JSON.stringify({chip: runChip(cmd), idle: runChip(idle),"
            " dot: swState({row: cmd}, 'recent'), busy: runChip({...cmd, status: 'busy'})}));",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "own.cjs"
            script.write_text(src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        at = re.search(r"running a command since (\d\d:\d\d)", got["chip"])     # the page's locale
        self.assertTrue(at and at.group(1) in ("15:45", "03:45"), got["chip"])
        self.assertIn('class="dot idle"', got["chip"])               # the idle ring, not green
        self.assertNotIn(">idle<", got["chip"])
        self.assertTrue(got["idle"].endswith("idle</span>"))
        self.assertEqual(got["dot"]["cls"], "idle")
        self.assertTrue(got["dot"]["tip"].startswith("running a command since " + at.group(1)))
        self.assertIn("working</span>", got["busy"])


if __name__ == "__main__":
    unittest.main()
