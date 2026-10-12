"""A task's Claude agent runs in the mode the hub launches it with, and a
session that does not, or that Claude's own safety check stopped, says so
(ED-216, GitHub issue 17).

Measured on 2026-10-12 (Claude Code 2.1.296): every hub session on this
machine was recorded in bypass mode, and all 18 denials by Claude's "auto
mode classifier" came in such sessions, on Claude in Chrome tools. The agent
in issue 17 read "denied by the Claude Code auto mode classifier" as the hub
having launched it in auto mode, and told the person to restart it in bypass.
"""
from __future__ import annotations

import json
import os
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_hooks
import attention
import chatroom
import dashboard
import permission_mode
import rotation
import stall
from backends import shared
from test_agent_models import _LaunchSeats
from test_ask_to_po import _Stall
from test_open_ask import _Bell

DENIAL = ("Permission for this action was denied by the Claude Code auto mode classifier. "
          "Reason: [Real-World Transactions]. If you have other tasks that don't depend on "
          "this action, continue working on those.")
CLICK = "mcp__claude-in-chrome__computer"


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def _mode(mode: str) -> dict:
    return {"type": "permission-mode", "permissionMode": mode, "sessionId": "s1"}


def _prompt(text: str, t: float, mode: str = "bypassPermissions") -> dict:
    return {"type": "user", "timestamp": _iso(t), "isSidechain": False, "permissionMode": mode,
            "message": {"role": "user", "content": text}}


def _call(tid: str, tool: str, t: float) -> dict:
    return {"type": "assistant", "timestamp": _iso(t), "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": tool, "input": {"action": "left_click"}}]}}


def _result(tid: str, text: str, t: float) -> dict:
    return {"type": "user", "timestamp": _iso(t), "isSidechain": False,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": True}]}}


def _lines(*entries) -> list[str]:
    return [json.dumps(e) for e in entries]


# ---------------------------------------------------------------------------
# Every launch path passes the hub's mode
# ---------------------------------------------------------------------------

def _mode_of(argv) -> str | None:
    argv = list(argv)
    if argv.count("--permission-mode") != 1:
        return None if "--permission-mode" not in argv else "twice"
    return argv[argv.index("--permission-mode") + 1]


class EveryLaunchPath(_LaunchSeats):
    """Owner, reviewer, PO, resume, owner handover and PO switch (the rotation
    and Make PO paths go through the same two launchers), a conversation
    brought in from the history, and a resume the person asked for."""

    def test_every_hub_seat_is_launched_in_bypass_mode(self):
        for seat, argv in self.seats("claude").items():
            with self.subTest(seat=seat):
                self.assertEqual(_mode_of(argv), "bypassPermissions")
        self.made.clear()
        room, part = self.room("claude", adopted=True)
        part["sessionId"] = "0199-sid"
        self.handler._resume_room_agent_pty(room, part, collab=False)
        room.pop("adopted")
        self.handler._resume_room_agent_pty(room, part, collab=False, human=True)
        for argv in self.made:
            self.assertEqual(_mode_of(argv), "bypassPermissions")

    def test_no_flag_when_the_hub_is_told_to_pass_none(self):
        with mock.patch.object(shared, "PERMISSION_MODE", ""):
            for seat, argv in self.seats("claude").items():
                with self.subTest(seat=seat):
                    self.assertIsNone(_mode_of(argv))

    def test_either_tools_setting_keeps_the_mode(self):
        """Settings > "Ensemble + my own tools" only decides --strict-mcp-config."""
        for tools, strict in ((dashboard.TASK_AGENT_TOOLS_ENSEMBLE, True),
                              (dashboard.TASK_AGENT_TOOLS_OWN, False)):
            with mock.patch.object(dashboard.Handler, "_task_agent_tools", return_value=tools):
                for seat, argv in self.seats("claude").items():
                    with self.subTest(tools=tools, seat=seat):
                        self.assertEqual(_mode_of(argv), "bypassPermissions")
                        self.assertEqual("--strict-mcp-config" in argv, strict)

    def test_the_agent_registry_builds_the_same(self):
        from agents.claude import ClaudeAgent
        a = ClaudeAgent()
        self.assertEqual(_mode_of(a.launch_argv("/w", "hi", ["--mcp-config", "x"])), "bypassPermissions")
        self.assertEqual(_mode_of(a.resume_argv("sid", ["--mcp-config", "x"])), "bypassPermissions")


class TheReleaseApp(unittest.TestCase):
    """The built app (Ensemble.exe, Ensemble.app) starts the hub and nothing
    else: no mode of its own, so a hub it starts launches in bypass mode."""

    def test_the_app_sets_no_mode_and_the_hub_defaults_to_bypass(self):
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        for name in ("ensemble_app.py", "app_launch.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("ENSEMBLE_PERMISSION_MODE", text, name)
            self.assertNotIn("--permission-mode", text, name)
        env = {k: v for k, v in os.environ.items() if k != "ENSEMBLE_PERMISSION_MODE"}
        out = subprocess.run([sys.executable, "-c", "from backends.shared import claude_cmd_args;"
                              "print(' '.join(claude_cmd_args('--resume', 's')))"],
                             cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                             timeout=60)
        self.assertEqual(out.stdout.strip(), "claude --resume s --permission-mode bypassPermissions")


class TerminalWindows(unittest.TestCase):
    """A task opened in a terminal window, and a resume from the list."""

    def test_macos_passes_the_mode_and_the_launch_flags(self):
        from backends import macos
        runs = []

        def run(args, **kw):
            runs.append(args)
            return types.SimpleNamespace(returncode=0, stdout="/dev/ttys001", stderr="")
        b = macos.MacBackend.__new__(macos.MacBackend)
        with mock.patch.object(macos.subprocess, "run", side_effect=run), \
                mock.patch.object(macos, "load_geometries", return_value={}), \
                mock.patch.object(macos.MacBackend, "_after_open", create=True):
            try:
                b.open_new("/w", "brief", session_id="sid", extra_args=["--mcp-config", "/tmp/a b.json"])
            except Exception:
                pass                    # what happens after the window opened is not under test
            try:
                b.open_resume("/w", "sid")
            except Exception:
                pass
        cmds = [r[4] for r in runs if r and r[0] == "osascript"]
        self.assertEqual(len(cmds), 2)
        new, resume = cmds
        self.assertIn("--permission-mode bypassPermissions", new)
        self.assertIn("--mcp-config '/tmp/a b.json'", new)
        self.assertIn("--resume sid --permission-mode bypassPermissions", resume)

    @unittest.skipUnless(os.name == "nt", "the Windows Terminal launcher")
    def test_windows_passes_the_mode(self):
        from backends import windows
        b = windows.WindowsBackend.__new__(windows.WindowsBackend)
        made = []
        with mock.patch.object(windows.shutil, "which", side_effect=lambda n: n), \
                mock.patch.object(windows.subprocess, "Popen"), \
                mock.patch.object(windows.WindowsBackend, "current_theme_for_cwd", return_value=""), \
                mock.patch.object(windows.WindowsBackend, "_write_launch_script",
                                  side_effect=lambda cwd, argv, *a, **k: made.append(argv) or "x.ps1"):
            b.open_new("C:/w", "brief", session_id="sid", extra_args=["--mcp-config", "c.json"])
            b.open_resume("C:/w", "sid")
        self.assertEqual([_mode_of(a) for a in made], ["bypassPermissions"] * 2)
        self.assertIn("--mcp-config", made[0])


# ---------------------------------------------------------------------------
# Reading the real mode, and Claude's safety check
# ---------------------------------------------------------------------------

class TheTranscript(unittest.TestCase):
    def test_the_newest_mode_counts(self):
        t = time.time()
        got = permission_mode.parse(_lines(_mode("bypassPermissions"), _prompt("go", t)))
        self.assertEqual({k: got[k] for k in ("mode", "denial")},
                         {"mode": "bypassPermissions", "denial": None})
        got = permission_mode.parse(_lines(_mode("bypassPermissions"), _prompt("go", t, "auto")))
        self.assertEqual(got["mode"], "auto")
        self.assertEqual(permission_mode.parse(_lines(_mode("default")))["mode"], "default")

    def test_a_denial_names_the_tool_and_the_reason(self):
        t = time.time()
        got = permission_mode.parse(_lines(_mode("bypassPermissions"), _prompt("buy it", t - 9),
                                           _call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4)))
        self.assertEqual(got["mode"], "bypassPermissions")
        self.assertEqual({k: got["denial"][k] for k in ("tool", "reason", "toolUseId")},
                         {"tool": CLICK, "reason": "Real-World Transactions", "toolUseId": "tu1"})
        self.assertAlmostEqual(got["denial"]["at"], int(t - 4), delta=1)

    def test_a_person_or_the_po_settles_it_a_hub_line_does_not(self):
        t = time.time()
        base = (_call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4))
        nudged = permission_mode.parse(_lines(*base, _prompt(stall.NUDGE, t - 3)))
        self.assertIsNotNone(nudged["denial"])
        for words in ("I bought it myself, carry on", "[from the PO] done by hand, carry on"):
            with self.subTest(words=words):
                self.assertIsNone(permission_mode.parse(_lines(*base, _prompt(words, t - 2)))["denial"])

    def test_the_same_words_quoted_are_not_a_denial(self):
        t = time.time()
        quoted = "The issue says: " + DENIAL
        got = permission_mode.parse(_lines(_call("tu1", "Read", t - 2), _result("tu1", quoted, t - 1)))
        self.assertIsNone(got["denial"])

    def test_a_tool_that_succeeded_with_those_words_is_not_a_denial(self):
        t = time.time()
        ok = _result("tu1", DENIAL, t - 1)
        ok["message"]["content"][0]["is_error"] = False
        got = permission_mode.parse(_lines(_call("tu1", "Read", t - 2), ok))
        self.assertIsNone(got["denial"])
        # Claude's own wording with its "Error: " head is one.
        err = _result("tu2", "Error: " + DENIAL, t)
        self.assertEqual(permission_mode.parse(_lines(_call("tu2", CLICK, t - 1), err))["denial"]["tool"],
                         CLICK)

    def test_read_from_a_file_and_cached(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "s.jsonl"
        t = time.time()
        p.write_text("\n".join(_lines(_mode("auto"), _prompt("go", t, "auto"))) + "\n", encoding="utf-8")
        self.assertEqual(permission_mode.from_transcript(p)["mode"], "auto")
        with mock.patch.object(permission_mode, "parse", side_effect=AssertionError("read again")):
            self.assertEqual(permission_mode.from_transcript(p)["mode"], "auto")
        self.assertEqual(permission_mode.from_transcript(None)["mode"], "")
        self.assertIsNone(permission_mode.from_transcript(None)["denial"])


class TheScreen(unittest.TestCase):
    def test_the_footer_names_the_mode(self):
        for foot, mode in (("⏵⏵ bypass permissions on (shift+tab to cycle)", "bypassPermissions"),
                           ("⏵⏵ auto mode on (shift+tab to cycle)", "auto"),
                           ("? for shortcuts", "")):
            with self.subTest(foot=foot):
                self.assertEqual(permission_mode.from_screen(f"● Done.\n>\n{foot}\n")["mode"], mode)

    def test_the_bypass_warning(self):
        screen = ("WARNING: Claude Code running in Bypass Permissions mode\n"
                  "In Bypass Permissions mode, Claude Code will not ask for your approval\n"
                  "❯ 1. No, exit\n  2. Yes, I accept\n")
        self.assertTrue(permission_mode.from_screen(screen)["consent"])
        self.assertFalse(permission_mode.from_screen("● bypass permissions mode is what we run\n>")["consent"])
        got = permission_mode.problem({"agent": "claude"}, None, screen, {"mode": "", "denial": None})
        self.assertEqual(got["cause"], "consent")


class WhatItAmountsTo(unittest.TestCase):
    def problem(self, mode="bypassPermissions", denial=None, hook=None, agent="claude"):
        return permission_mode.problem({"agent": agent}, hook, "", {"mode": mode, "denial": denial})

    def test_bypass_with_nothing_denied_is_fine(self):
        self.assertIsNone(self.problem())
        self.assertIsNone(self.problem(mode=""))
        self.assertIsNone(self.problem(mode="plan"))          # the agent's own plan mode
        self.assertIsNone(self.problem(mode="auto", agent="codex"))

    def test_another_mode_in_plain_words(self):
        got = self.problem(mode="auto")
        self.assertEqual((got["cause"], got["key"]), ("mode", "mode:auto"))
        self.assertIn("runs in auto mode, not in bypass mode as the hub launched it", got["why"])
        got = self.problem(mode="default")
        self.assertIn("runs in Manual mode", got["why"])
        self.assertIn("stops for approval", got["why"])
        # The agent's own hooks name the mode as it is now.
        self.assertEqual(self.problem(mode="", hook={"mode": "acceptEdits"})["key"], "mode:acceptEdits")

    def test_a_denial_in_plain_words(self):
        den = {"tool": CLICK, "reason": "Real-World Transactions", "at": time.time() - 60,
               "toolUseId": "tu1"}
        got = self.problem(denial=den)
        self.assertEqual((got["cause"], got["key"]), ("denied", "denied:tu1"))
        self.assertEqual(got["why"], "was stopped by Claude's own safety check: it blocked a browser "
                                     "action (a click or key press) (Claude's reason: Real-World "
                                     "Transactions); the agent runs in bypass mode. A person has to "
                                     "do this step.")
        self.assertNotIn("auto mode", got["why"])
        self.assertIsNone(self.problem(denial={**den, "at": time.time() - 2 * 86400}))

    def test_the_newest_mode_wins_over_an_older_hook(self):
        """Shift+Tab writes a mode record (and redraws the footer) before any
        hook names the new mode."""
        got = self.problem(mode="auto", hook={"mode": "bypassPermissions"})
        self.assertEqual(got["key"], "mode:auto")
        got = permission_mode.problem({"agent": "claude"}, {"mode": "bypassPermissions"},
                                      "> \n⏵⏵ auto mode on (shift+tab to cycle)\n",
                                      {"mode": "bypassPermissions", "denial": None})
        self.assertEqual(got["key"], "mode:auto")
        # Back in bypass on screen: whatever an older record said.
        self.assertIsNone(permission_mode.problem(
            {"agent": "claude"}, {"mode": "auto"}, "> \n⏵⏵ bypass permissions on\n",
            {"mode": "auto", "denial": None}))

    def test_a_denial_only_the_hook_has_seen(self):
        t = time.time()
        hden = {"tool": CLICK, "reason": "Real-World Transactions", "toolUseId": "tu9", "at": t - 5}
        got = self.problem(hook={"mode": "bypassPermissions", "denied": hden})
        self.assertEqual((got["cause"], got["key"]), ("denied", "denied:tu9"))
        # Settled in the transcript since (a person typed), or already read there.
        settled = {"mode": "bypassPermissions", "denial": None, "denials": [], "settledAt": t - 1}
        self.assertIsNone(permission_mode.problem({"agent": "claude"}, {"denied": hden}, "", settled))
        seen = {"mode": "bypassPermissions", "denial": None, "denials": ["tu9"], "settledAt": 0.0}
        self.assertIsNone(permission_mode.problem({"agent": "claude"}, {"denied": hden}, "", seen))
        old = {**hden, "at": t - 2 * 86400}
        self.assertIsNone(self.problem(hook={"denied": old}))

    def test_an_approval_prompt_in_bypass_mode(self):
        hook = {"state": "waiting", "event": "PermissionRequest", "detail": CLICK, "at": 123.0,
                "mode": "bypassPermissions"}
        got = self.problem(hook=hook)
        self.assertEqual((got["cause"], got["key"]), ("prompt", "prompt:123.0"))
        self.assertIn("waiting for a person to approve a browser action", got["why"])
        self.assertIsNone(self.problem(hook={**hook, "event": "PreToolUse"}))

    def test_an_agents_question_is_not_a_permission_problem(self):
        hook = {"state": "waiting", "event": "PermissionRequest", "at": 123.0,
                "mode": "bypassPermissions"}
        for detail in ("AskUserQuestion", "ExitPlanMode", "permission_prompt", ""):
            with self.subTest(detail=detail):
                self.assertIsNone(self.problem(hook={**hook, "detail": detail}))

    def test_settled_by_its_own_report(self):
        perm = {"cause": "denied", "at": 100.0}
        room = {"lastReport": {"identity": "claude", "ts": 101.0}}
        self.assertTrue(permission_mode.settled(perm, room, "claude"))
        self.assertFalse(permission_mode.settled(perm, room, "claude-2"))
        self.assertFalse(permission_mode.settled({**perm, "at": 102.0}, room, "claude"))
        self.assertFalse(permission_mode.settled({"cause": "mode"}, room, "claude"))

    def test_an_open_ask_takes_precedence_over_a_prompt(self):
        room = {"openToHuman": {"from": "claude", "kind": "question", "ts": 90.0}}
        self.assertTrue(permission_mode.settled({"cause": "prompt", "at": 100.0}, room, "claude"))
        self.assertFalse(permission_mode.settled({"cause": "prompt", "at": 100.0}, room, "codex"))

    def test_action_words(self):
        self.assertEqual(permission_mode.action_words("mcp__claude-in-chrome__navigate"),
                         "a browser action (opening a page)")
        self.assertEqual(permission_mode.action_words("Bash"), "an action (Bash)")
        self.assertEqual(permission_mode.action_words(""), "an action")


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------

class TheHooks(unittest.TestCase):
    def setUp(self):
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)

    def post(self, **event):
        return agent_hooks.record({"room": "r", "identity": "claude", "ptyId": "p", "event": event},
                                  lambda pid: ("r", "claude"))

    def test_the_mode_and_a_denial_are_kept(self):
        self.post(hook_event_name="UserPromptSubmit", session_id="s", permission_mode="auto")
        self.assertEqual(agent_hooks.state_for("p")["mode"], "auto")
        res = self.post(hook_event_name="PermissionDenied", tool_name=CLICK, tool_use_id="tu1",
                        reason="Real-World Transactions", permission_mode="auto")
        st = agent_hooks.state_for("p")
        self.assertEqual((st["state"], res["changed"]), ("working", False))   # news, not a state
        self.assertEqual({k: st["denied"][k] for k in ("tool", "reason", "toolUseId")},
                         {"tool": CLICK, "reason": "Real-World Transactions", "toolUseId": "tu1"})

    def test_ask_user_question_in_bypass_is_not_a_permission_problem(self):
        self.post(hook_event_name="PreToolUse", tool_name="AskUserQuestion", tool_use_id="tuq",
                  permission_mode="bypassPermissions")
        self.post(hook_event_name="PermissionRequest", tool_name="AskUserQuestion",
                  permission_mode="bypassPermissions")
        got = permission_mode.problem({"agent": "claude"}, agent_hooks.state_for("p"), "",
                                      {"mode": "bypassPermissions", "denial": None})
        self.assertIsNone(got)

    def test_a_subagent_does_not_name_the_mode(self):
        self.post(hook_event_name="UserPromptSubmit", session_id="s", permission_mode="bypassPermissions")
        self.post(hook_event_name="PostToolUse", tool_name="Read", agent_id="sub",
                  permission_mode="default")
        self.assertEqual(agent_hooks.state_for("p")["mode"], "bypassPermissions")

    def test_the_hook_is_registered(self):
        self.assertIn(("PermissionDenied", None, True), dashboard._AGENT_HOOK_EVENTS)
        self.assertIn("PermissionDenied", dashboard._agent_hooks())


class TheBriefing(unittest.TestCase):
    def test_task_agents_are_told_not_to_advise_a_restart_in_bypass(self):
        self.assertIn(dashboard.SAFETY_CHECK_NOTE, dashboard.SOLO_REPORT_NOTE)
        with mock.patch.object(dashboard, "operator_name", return_value="the person"):
            text = dashboard.collab_briefing("claude", "engineer",
                                             [{"identity": "codex", "role": "reviewer"}], "t")
        self.assertIn(dashboard.SAFETY_CHECK_NOTE, text)
        self.assertIn("do not tell anyone to restart you in bypass mode", dashboard.SAFETY_CHECK_NOTE)


# ---------------------------------------------------------------------------
# The bell, and the PO told once
# ---------------------------------------------------------------------------

class _Transcript:
    def write(self, *entries):
        permission_mode._SEEN.clear()
        self.tpath.write_text("\n".join(_lines(*entries)) + "\n", encoding="utf-8")


class TheBell(_Bell, _Transcript):
    def setUp(self):
        super().setUp()
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tpath = Path(d.name) / "s.jsonl"
        p = mock.patch.object(dashboard.rotation, "_transcript_of",
                              return_value=(self.tpath, rotation.read_transcript))
        p.start()
        self.addCleanup(p.stop)

    def test_a_denial_is_blocked_in_plain_words(self):
        t = time.time()
        self.write(_mode("bypassPermissions"), _prompt("buy it", t - 9),
                   _call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4))
        it = self.item()
        self.assertEqual((it["state"], it["cause"], it["permission"]), ("blocked", "permission", "denied"))
        self.assertIn("Claude's own safety check", it["reason"])
        self.assertIn("A person has to do this step", it["reason"])

    def test_its_own_report_since_takes_over(self):
        t = time.time()
        self.write(_call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4))
        self.report("blocked", "Claude's safety check blocked the purchase click.")
        it = self.item()
        self.assertEqual(it["cause"], "reported")

    def test_another_mode_is_blocked(self):
        self.write(_mode("auto"), _prompt("go", time.time() - 5, "auto"))
        it = self.item()
        self.assertEqual((it["state"], it["permission"]), ("blocked", "mode"))
        self.assertIn("runs in auto mode", it["reason"])

    def test_an_approval_prompt_waits_for_a_person(self):
        prompt = {"cause": "prompt", "key": "prompt:123", "at": 123.0,
                  "why": "is waiting for approval"}
        with mock.patch.object(permission_mode, "problem", return_value=prompt):
            it = self.item()
        self.assertEqual((it["state"], it["cause"], it["permission"]),
                         ("waiting_for_you", "permission", "prompt"))

    def test_bypass_and_nothing_denied_is_quiet(self):
        self.write(_mode("bypassPermissions"), _prompt("go", time.time() - 5))
        self.assertIsNone(self.item())


class ThePoIsToldOnce(_Stall, _Transcript):
    def setUp(self):
        super().setUp()
        self.tpath = Path(dashboard.DASHBOARD_DIR) / "owner.jsonl"
        dashboard.rotation._transcript_of.side_effect = \
            lambda part: (self.tpath, rotation.read_transcript) if part.get("identity") == "claude" \
            else (None, lambda p: {"turnOver": True})
        self.idle = 30.0                # not idle long enough to be nudged

    def told(self):
        return [m for m in chatroom.get_room(self.po_rid, public=False)["messages"]
                if (m.get("meta") or m).get("permissionTask") == self.rid
                or "permission check" in (m.get("text") or "")]

    def test_once_per_denial(self):
        t = time.time()
        self.write(_call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4))
        self.assertIn("PO told (denied)", stall.tick(t)[self.rid])
        self.assertEqual(len(self.told()), 1)
        self.assertIn("Claude's own safety check", self.told()[0]["text"])
        self.assertIn("restarting the agent does not change it", self.told()[0]["text"])
        self.assertNotIn("PO told", stall.tick(t + 60).get(self.rid, ""))
        self.assertEqual(len(self.told()), 1)
        # A new denial is news again.
        self.write(_call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4),
                   _call("tu2", "mcp__claude-in-chrome__navigate", t + 61),
                   _result("tu2", DENIAL, t + 62))
        self.assertIn("PO told (denied)", stall.tick(t + 120)[self.rid])
        self.assertEqual(len(self.told()), 2)

    def test_not_after_its_own_report(self):
        t = time.time()
        self.write(_call("tu1", CLICK, t - 5), _result("tu1", DENIAL, t - 4))
        time.sleep(0.002)
        self.ask_po("blocked", "Claude's safety check blocked the purchase click.")
        self.assertNotIn("PO told", stall.tick(t).get(self.rid, ""))
        self.assertEqual(self.told(), [])

    def test_another_mode_says_a_restart_helps(self):
        self.write(_mode("auto"), _prompt("go", time.time() - 5, "auto"))
        self.assertIn("PO told (mode)", stall.tick()[self.rid])
        text = self.told()[0]["text"]
        self.assertIn("runs in auto mode", text)
        self.assertIn("A restart from the hub launches it in bypass mode again", text)
        self.assertNotIn("restarting the agent does not change it", text)

    def test_nothing_without_a_problem(self):
        self.write(_mode("bypassPermissions"), _prompt("go", time.time() - 5))
        self.assertNotIn("PO told", stall.tick().get(self.rid, ""))
        self.assertEqual(self.told(), [])


if __name__ == "__main__":
    unittest.main()
