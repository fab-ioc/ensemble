"""Needs you shows only what really waits on the person (GitHub issue 11, #188).

Measured on 2026-10-08: a one-agent task in no project ("Cyber Sec") that
reported it had finished sat in the attention list as ``waiting_for_you`` for
17 hours; two tasks with a question the person had long seen (11 and 22 days
old) were held back only because their agents were stopped; a PO's every plain
message to the person ("#205 is live") kept it waiting until they typed in its
chat; and asks marked in a message (asks.py) counted for seven days whether
read or not. The rules, each a fixture below:

* a ``completed`` report is never Needs you: it is ``reported`` (Ready for
  your check) until the person reads that chat past it, writes in it, or the
  task is Done or stopped;
* a ``question`` / ``blocked`` stays until it is answered, whatever the agent
  does (ROADMAP item 32), and unread it stays however old it is;
* what the person has read (``seenAt``, the page's read point kept on the
  room) leaves once it is a day old: a report's ask, a message to them, a room
  waiting on them, an ask marked in a message;
* a PO is Needs you only for what it asks (marked asks, reports, a prompt),
  never for a plain message or its room waiting on the person by that alone;
* a project's needs count goes by attention alone for its tasks.

The page side (index.html ``swGroups``: a row's ``reported`` files it under
Ready for your check, never Needs you) runs in Node at the end.
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
from unittest import mock

import asks
import attention
import chatroom
import dashboard
from test_open_ask import _Bell

ROOT = Path(__file__).resolve().parent.parent
DAY = 24 * 3600
NODE = shutil.which("node")


class _Needs(_Bell):
    def setUp(self):
        super().setUp()
        asks.forget()
        self.addCleanup(asks.forget)

    def item(self):
        asks.forget()
        return super().item()

    def age_last(self, seconds):
        """Make the room's newest message (and the room's own clock) older."""
        full = chatroom.get_room(self.rid, public=False)
        m = full["messages"][-1]
        m["ts"] = float(m["ts"]) - seconds
        chatroom.update_room(full)
        return m["ts"]

    def seen(self, ts):
        return chatroom.record_seen(self.rid, ts)


class AFinishedReport(_Needs):
    def test_is_ready_for_a_check_never_needs_you(self):
        self.report("completed", "Advised the operator; nothing left to do.")
        self.assertIsNone(self.item())
        rep = self.reported()
        self.assertEqual((rep["line"], rep["agent"]), ("Advised the operator; nothing left to do.", "claude"))

    def test_leaves_when_the_chat_is_read_past_it(self):
        done = self.report("completed", "Done.")
        self.seen(done["ts"] - 1)
        self.assertIsNotNone(self.reported())          # read up to before it: still new
        self.seen(done["ts"])
        self.assertIsNone(self.reported())
        self.assertIsNone(self.item())

    def test_leaves_when_the_person_writes_in_it(self):
        self.report("completed", "Done.")
        chatroom.post_message(self.rid, "user", "Thanks.")
        self.assertIsNone(self.reported())
        self.assertIsNone(self.item())

    def test_leaves_when_the_task_is_done_or_stopped(self):
        self.report("completed", "Done.")
        full = chatroom.get_room(self.rid, public=False)
        full["workflow"] = "done"
        chatroom.update_room(full)
        self.assertIsNone(self.reported())
        full["workflow"] = "inprogress"
        chatroom.update_room(full)
        self.assertIsNotNone(self.reported())
        self.sess.alive = lambda: False                 # stopped: no agent running
        self.sess.death = lambda: {"killed": True, "endedAt": time.time()}
        self.assertIsNone(self.reported())
        self.assertIsNone(self.item())

    def test_an_idle_finished_task_is_not_stalled_either(self):
        self.report("completed", "Done.")
        self.age_last(3 * DAY)
        with mock.patch.object(attention, "_stall_seconds", return_value=60):
            self.assertIsNone(self.item())


class AnAsk(_Needs):
    def test_unread_it_stays_however_old(self):
        self.report("question", "Which price?")
        self.age_last(5 * DAY)
        self.busy()                                     # ROADMAP 32: busy hides nothing
        self.assertEqual(self.item()["state"], "waiting_for_you")

    def test_read_it_stays_for_a_day(self):
        asked = self.report("question", "Which price?")
        self.seen(asked["ts"])
        self.assertEqual(self.item()["askKind"], "question")

    def test_read_and_a_day_old_it_leaves(self):
        self.report("blocked", "The login is gone.")
        ts = self.age_last(DAY + 60)
        self.assertEqual(self.item()["state"], "blocked")      # old, never read
        self.seen(ts - 1)
        self.assertEqual(self.item()["state"], "blocked")      # read only up to before it
        self.seen(ts)
        self.assertIsNone(self.item())

    def test_read_and_old_is_not_stalled(self):
        self.report("question", "Which price?")
        self.seen(self.age_last(3 * DAY))
        with mock.patch.object(attention, "_stall_seconds", return_value=60):
            self.assertIsNone(self.item())

    def test_a_newer_ask_after_the_read_point_is_back(self):
        self.report("question", "Which price?")
        self.seen(self.age_last(2 * DAY))
        self.assertIsNone(self.item())
        self.report("question", "And which colour?")
        self.assertEqual(self.item()["quote"], "And which colour?")

    def test_a_message_to_the_person_read_a_day_ago_leaves(self):
        chatroom.post_message(self.rid, "claude", "Shall I renew it?", to="user")
        self.assertEqual(self.item()["askKind"], "message")
        self.seen(self.age_last(DAY + 60))
        self.assertIsNone(self.item())

    def test_a_marked_ask_read_a_day_ago_leaves(self):
        full = chatroom.get_room(self.rid, public=False)
        full["mode"] = "collab"
        chatroom.update_room(full)
        chatroom.post_message(self.rid, "claude", "Ask (yes/no): Renew the licence?", to="user")
        it = self.item()
        self.assertEqual(it["state"], "waiting_for_you")
        ts = self.age_last(2 * DAY)
        asks.forget()
        self.assertTrue(asks.open_by_room(attention._room_summaries()).get(self.rid))
        self.seen(ts)
        asks.forget()
        self.assertFalse(asks.open_by_room(attention._room_summaries()).get(self.rid))
        self.assertIsNone(self.item())


class APO(_Needs):
    def setUp(self):
        super().setUp()
        projects = [{"id": "p1", "name": "Trading", "poRoomId": self.rid, "registered": True}]
        for patch in (mock.patch.object(dashboard, "load_projects", return_value=projects),
                      mock.patch.object(dashboard, "load_session_projects",
                                        return_value={self.rid: "p1"})):
            patch.start()
            self.addCleanup(patch.stop)
        attention._SUMMARY_CACHE.clear()

    def test_a_plain_message_is_its_news_not_an_ask(self):
        chatroom.post_message(self.rid, "claude", "#205 is merged and live.", to="user")
        self.assertEqual(chatroom.get_room(self.rid, public=False)["status"], "waiting_human")
        self.assertIsNone(self.item())

    def test_what_it_asks_is_needs_you(self):
        chatroom.post_message(self.rid, "claude",
                              "#205 is live.\n\nAsk: How should I check the P&L in the console?", to="user")
        it = self.item()
        self.assertEqual((it["state"], it["askKind"]), ("waiting_for_you", "asks"))

    def test_a_report_it_makes_is_still_an_ask(self):
        self.report("question", "Which broker first?")
        self.assertEqual(self.item()["askKind"], "question")

    def test_what_it_asks_unread_stays_past_a_week(self):
        # Review 1: asks.py once dropped every marked ask after seven days,
        # and a PO has nothing else that keeps it in Needs you.
        chatroom.post_message(self.rid, "claude", "Ask: Which broker first?", to="user")
        ts = self.age_last(9 * DAY)
        self.assertEqual(self.item()["askKind"], "asks")
        self.seen(ts)
        self.assertIsNone(self.item())


class TheReadPoint(_Needs):
    def test_moves_forward_only_and_never_past_now(self):
        self.assertEqual(self.seen(100.0), 100.0)
        self.assertEqual(self.seen(50.0), 100.0)
        now = time.time()
        self.assertLessEqual(self.seen(now + DAY), time.time())
        self.assertIsNone(chatroom.record_seen("room-gone", 1.0))
        self.assertIsNone(self.seen("soon"))

    def test_reading_is_not_news(self):
        before = chatroom.get_room(self.rid, public=False)["updatedAt"]
        self.seen(time.time())
        self.assertEqual(chatroom.get_room(self.rid, public=False)["updatedAt"], before)

    def test_only_the_page_may_set_it(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p == "/api/attention/seen":')
        body = src[i:src.index("return\n", src.index("record_seen", i))]
        self.assertIn("self._page_refusal()", body)
        self.assertIn('re.fullmatch(r"room-[A-Za-z0-9_-]{1,64}", rid)', body)
        page = (ROOT / "session.html").read_text(encoding="utf-8")
        self.assertIn("seenTell(Math.max(ts, DRAWN_ROOM_TS));", page)
        self.assertIn("'/api/attention/seen'", page)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_a_report_later_in_the_last_turn_counts_as_read(self):
        # A turn is dated by its start: the report made in it is newer than
        # the chat's last line, and still read with it (measured 10-08: 2 of 7
        # finished tasks).
        page = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        line = re.search(r"^const roomLatestTs = .*$", page, re.M).group(0)
        js = line + "\nconsole.log(JSON.stringify([roomLatestTs(null), roomLatestTs({messages: " \
            "[{ts: 5}, {ts: 9.5}, {}]})]));\n"
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "ts.cjs"
            script.write_text(js, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), [0, 9.5])
        # Review 1: the bound is what the room held before the transcript now
        # drawn was fetched; a redraw of older items (renderBubbles on a click,
        # a newer ROOM_OBJ) moves nothing.
        draw = _js_fn(page, "function renderBubbles(")
        self.assertNotIn("DRAWN_ROOM_TS", draw)
        solo = _js_fn(page, "async function renderSolo(")
        took, fetched = solo.index("const roomTs = roomLatestTs(ROOM_OBJ);"), solo.index("await fetch(")
        self.assertLess(took, fetched)
        self.assertLess(solo.index("DRAWN_ROOM_TS = roomTs;"), solo.index("renderBubbles(items);"))

    @unittest.skipUnless(NODE, "node is not installed")
    def test_a_failed_read_notice_is_sent_again(self):
        page = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        decl = re.search(r"^let SEEN_TOLD = .*$", page, re.M).group(0)
        js = "\n".join([
            "let ROOM = 'room-abc', calls = [], answer = [];",
            "globalThis.fetch = (url, opt) => { calls.push(JSON.parse(opt.body).ts);",
            "  const a = answer.shift(); return a === 'net' ? Promise.reject(new Error('down'))",
            "    : Promise.resolve({ ok: a === 200, status: a }); };",
            decl, _js_fn(page, "function seenTell("),
            "const tick = () => new Promise(r => setTimeout(r, 5));",
            "(async () => {",
            "  answer = ['net', 500, 200];",
            "  seenTell(10); seenTell(10); await tick();",   # one in flight: not twice
            "  seenTell(10); await tick();",                  # failed: sent again
            "  seenTell(10); await tick();",                  # refused: sent again, taken
            "  seenTell(10); seenTell(9); await tick();",     # taken: nothing more
            "  console.log(JSON.stringify({ calls, told: SEEN_TOLD }));",
            "})();"])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "seen.cjs"
            script.write_text(js, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"calls": [10, 10, 10], "told": 10})


class TheProjectCount(unittest.TestCase):
    def test_a_task_room_waiting_without_attention_does_not_count(self):
        rows = [{"roomId": "room-a", "sessionId": "room-a", "isLive": True, "status": "waiting_human",
                 "cwd": "C:/x/a"},
                {"sessionId": "s-plain", "isLive": True, "status": "waiting", "cwd": "C:/x/b"}]
        reg = [{"id": "p1", "name": "P", "path": "C:/x", "registered": True}]
        with mock.patch.object(dashboard, "load_projects", return_value=reg), \
                mock.patch.object(dashboard, "load_session_projects",
                                  return_value={"room-a": "p1", "s-plain": "p1"}), \
                mock.patch.object(dashboard, "load_sessions", return_value=rows), \
                mock.patch.object(dashboard, "project_home", return_value=""):
            out = dashboard.build_projects()
        # The plain terminal session at a prompt counts; the task room does not.
        self.assertEqual(out["summary"]["needsYou"], 1)


INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")

PAGE_JS = r"""
const WORKFLOW_COLS = [];
%s
const T0 = 1790500000;
const row = (rid, more) => ({ sessionId: rid, roomId: rid, headless: true, workflow: 'inprogress',
                              isLive: true, status: 'idle', updatedAt: T0, hasConversation: true, ...more });
const projects = [{ id: 'p1', name: 'P', registered: true, poRoomId: 'po-1',
                    sessions: ['po-1', 'r-done', 'r-ask', 'r-old'].map(roomId => ({ roomId })) }];
const rows = [row('r-done', { reported: { since: T0 + 5, line: 'Done.', agent: 'codex' } }),
              row('r-ask'), row('r-old'), row('po-1')];
const items = [{ roomId: 'r-ask', state: 'waiting_for_you', askKind: 'question', since: T0 }];
const g = swGroups(rows, items, projects, {});
const keys = k => g[k].map(e => e.key);
console.log(JSON.stringify({ needs: keys('needs'), review: keys('review'), running: keys('running'),
                             at: (g.review.find(e => e.key === 'r-done') || {}).at }));
"""


def _js_fn(src, head):
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


def _fn(head):
    i = INDEX.index(head)
    return INDEX[i:INDEX.index("\n}\n", i) + 3]


@unittest.skipUnless(NODE, "node is not installed")
class TheList(unittest.TestCase):
    def test_a_reported_row_is_ready_for_a_check_not_needs_you(self):
        src = "\n".join([
            re.search(r"^const WORKFLOW_KEYS = .*$", INDEX, re.M).group(0),
            _fn("function workflowOf("),
            INDEX[INDEX.index("function swReported("):INDEX.index("function swFreeze(")]])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "list.cjs"
            script.write_text(PAGE_JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["needs"], ["r-ask"])
        self.assertEqual(out["review"], ["r-done"])
        self.assertEqual(out["running"], ["r-old"])
        self.assertEqual(out["at"], 1790500005)     # dated by its report


if __name__ == "__main__":
    unittest.main()
