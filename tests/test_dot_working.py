"""The left list tells the truth (#193, GitHub issue 13, point P157).

Measured on 2026-10-09: every live row was green (all seven PO rows and Cyber
Sec) though only about two agents were doing anything, because a row was
"busy" whenever its terminal printed in the last 2.5 s and its dot was green
whenever a terminal ran at all. And the Music PO's question, read on the
CEO's Windows Chrome at 06:11, stayed in Waiting for you: the page's read
point was refused (``403 page_only``, 1502 times since the 01:03 restart)
because the hub draws a new page key at each restart and the page's chat
frames, iframes, never got it.

The rules, each a fixture below:

* a row is ``busy`` only while one of its agents is in a turn, as attention
  reads it (``_RUN``, ``run_by_room``); a terminal that runs and does nothing
  is ``idle``;
* the list's dot (index.html ``swState``) is green (``working``) only then;
  live and idle is a ring (``idle``), else off ("stopped");
* Waiting for you keeps one rule for a PO row and a task row: an ask read and
  a day old leaves; Done with this (``dealtAt``) takes it out at once, read or
  not, and an ask made later is back;
* a frame of the page's own gets today's key, so a page left open across a
  restart heals (``/ui-key``, static/pagekey.js); one from another site does not.
"""
from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import attention
import chatroom
import dashboard
from test_needs_you_real import DAY, _Needs, _js_fn

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


class TheRunState(_Needs):
    def run_state(self):
        attention._ANALYSIS.clear()
        return attention.snapshot(max_age=-1)["run"].get(self.rid)

    def test_a_terminal_doing_nothing_is_idle(self):
        self.assertEqual(self.run_state(), "idle")
        self.assertEqual(attention.run_by_room(max_age=-1).get(self.rid), "idle")

    def test_an_agent_in_a_turn_is_working(self):
        self.busy()
        self.assertEqual(self.run_state(), "working")

    def test_a_stopped_terminal_has_no_run_state(self):
        self.sess.alive = lambda: False
        self.sess.death = lambda: {"killed": True, "endedAt": time.time()}
        self.assertIsNone(self.run_state())

    def test_the_rows_busy_is_the_run_state_not_recent_output(self):
        src = inspect.getsource(dashboard._load_sessions_uncached)
        self.assertIn('busy = run_state.get(rm["id"]) == "working"', src)
        self.assertNotIn("if isec < 2.5:", src)


class DoneWithThis(_Needs):
    def dealt(self, back=1.0):
        """Done with this, a moment ago (so an ask made now is later)."""
        got = chatroom.record_dealt(self.rid)
        full = chatroom.get_room(self.rid, public=False)
        full["dealtAt"] = got - back
        chatroom.update_room(full)
        return got

    def test_an_unread_ask_leaves_at_once_and_a_later_one_is_back(self):
        self.report("question", "Which price?")
        it = self.item()
        self.assertTrue(it["canDismiss"])
        self.dealt(back=0)
        self.assertIsNone(self.item())
        self.report("question", "And which colour?")
        self.assertEqual(self.item()["quote"], "And which colour?")

    def test_it_counts_as_read_too(self):
        got = chatroom.record_dealt(self.rid)
        room = chatroom.get_room(self.rid, public=False)
        self.assertEqual((room["dealtAt"], room["seenAt"]), (got, got))
        self.assertIsNone(chatroom.record_dealt("room-gone"))

    def test_a_marked_ask_leaves(self):
        full = chatroom.get_room(self.rid, public=False)
        full["mode"] = "collab"
        chatroom.update_room(full)
        chatroom.post_message(self.rid, "claude", "Ask (yes/no): Renew the licence?", to="user")
        self.assertTrue(self.item()["canDismiss"])
        self.dealt(back=0)
        self.assertIsNone(self.item())

    def test_a_dead_agent_cannot_be_put_aside(self):
        self.sess.alive = lambda: False
        self.sess.death = lambda: {"killed": False, "exitCode": 1, "endedAt": time.time()}
        it = self.item()
        if it is not None:                       # a death is shown, never dismissable
            self.assertNotIn("canDismiss", it)

    def test_only_the_page_may_say_it(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p == "/api/attention/done":')
        body = src[i:src.index("return\n", src.index("record_dealt", i))]
        self.assertIn("self._page_refusal()", body)
        self.assertIn('re.fullmatch(r"room-[A-Za-z0-9_-]{1,64}", rid)', body)


class APORowKeepsTheSameRule(_Needs):
    def setUp(self):
        super().setUp()
        projects = [{"id": "p1", "name": "Music", "poRoomId": self.rid, "registered": True}]
        for patch in (mock.patch.object(dashboard, "load_projects", return_value=projects),
                      mock.patch.object(dashboard, "load_session_projects",
                                        return_value={self.rid: "p1"})):
            patch.start()
            self.addCleanup(patch.stop)
        attention._SUMMARY_CACHE.clear()

    def test_its_old_read_ask_leaves_and_unread_stays(self):
        chatroom.post_message(self.rid, "claude", "Ask: Which label first?", to="user")
        ts = self.age_last(9 * DAY)
        self.assertEqual(self.item()["askKind"], "asks")      # unread: stays however old
        self.seen(ts)
        self.assertIsNone(self.item())

    def test_done_with_this_takes_its_ask_out(self):
        chatroom.post_message(self.rid, "claude", "Ask: Which label first?", to="user")
        self.age_last(60)
        self.assertTrue(self.item()["canDismiss"])
        chatroom.record_dealt(self.rid)
        self.assertIsNone(self.item())


class _H:
    """Just the headers of a request, for the Handler's own checks."""
    _is_page_navigation = dashboard.Handler._is_page_navigation

    def __init__(self, **h):
        self.headers = {k.replace("_", "-"): v for k, v in h.items()}


class ThePageKey(unittest.TestCase):
    def nav(self, **h):
        return _H(Sec_Fetch_Mode="navigate", **h)._is_page_navigation()

    def test_a_frame_of_the_page_gets_it(self):
        self.assertTrue(self.nav(Sec_Fetch_Dest="document", Sec_Fetch_Site="none"))
        self.assertTrue(self.nav(Sec_Fetch_Dest="iframe", Sec_Fetch_Site="same-origin"))
        self.assertFalse(self.nav(Sec_Fetch_Dest="iframe", Sec_Fetch_Site="cross-site"))
        self.assertFalse(self.nav(Sec_Fetch_Dest="iframe", Sec_Fetch_Site="same-site"))
        self.assertFalse(_H(Sec_Fetch_Mode="cors", Sec_Fetch_Dest="empty",
                            Sec_Fetch_Site="same-origin")._is_page_navigation())

    def test_ui_key_hands_it_over(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p == "/ui-key":')
        body = src[i:src.index("return\n", i)]
        self.assertIn("self._ui_key_headers()", body)
        self.assertIn('"no-store"', body)

    def test_the_read_point_and_done_go_through_page_write(self):
        session = (ROOT / "session.html").read_text(encoding="utf-8")
        self.assertIn('src="/static/pagekey.js"', session)
        self.assertIn("pageWrite('/api/attention/seen'", session)
        self.assertIn('src="/static/pagekey.js"', INDEX)
        self.assertIn("pageWrite('/api/attention/done'", INDEX)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_a_stale_key_is_fetched_once_and_the_write_sent_again(self):
        js = (ROOT / "static" / "pagekey.js").read_text(encoding="utf-8")
        harness = """
const calls = []; let key = 'old';
global.window = global;
global.document = { body: { appendChild(f) { calls.push('frame ' + f.src.split('?')[0]);
  key = 'new'; setTimeout(() => f.fire('load'), 0); } },
  createElement() { const l = {}; return { setAttribute() {}, remove() {},
    addEventListener(n, fn) { l[n] = fn; }, fire(n) { l[n] && l[n](); } }; } };
const res = (s, b) => ({ status: s, clone() { return { json: async () => b }; } });
global.fetch = async (url) => { calls.push('post ' + key);
  return key === 'old' ? res(403, { error: 'page_only' }) : res(200, { ok: true }); };
""" + js + """
(async () => {
  const r = await pageWrite('/api/attention/seen', { roomId: 'room-a' });
  const r2 = await pageWrite('/api/attention/seen', { roomId: 'room-a' });
  console.log(JSON.stringify({ calls, status: [r.status, r2.status] }));
})();
"""
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "key.cjs"
            script.write_text(harness, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got["calls"], ["post old", "frame /ui-key", "post new", "post new"])
        self.assertEqual(got["status"], [200, 200])


@unittest.skipUnless(NODE, "node is not installed")
class TheDot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            "const ATTN_LABEL = {waiting_for_you: 'waiting for you'};",
            "const swTone = s => s === 'blocked' ? 'danger' : 'warning';",
            "const isUnread = r => !!r.unread;",
            _js_fn(INDEX, "function swState("),
            "const cases = " + json.dumps({
                "working": [{"row": {"isLive": True, "status": "busy"}}, "recent"],
                "idle": [{"row": {"isLive": True, "status": "idle"}}, "recent"],
                "draft": [{"row": {"isLive": True, "draft": True, "status": "busy"}}, "recent"],
                "stopped": [{"row": {"isLive": False}}, "recent"],
                "done": [{"row": {"isLive": False}}, "done"],
                "unread": [{"row": {"isLive": False, "unread": True}}, "recent"],
                "po_working": [{"row": {"isLive": True, "status": "idle"},
                                "sig": {"needs": [], "working": 2, "running": 3}}, "recent"],
                "po_idle": [{"row": {"isLive": False},
                             "sig": {"needs": [], "working": 0, "running": 1}}, "recent"],
                "po_needs": [{"row": {"isLive": True, "status": "busy"},
                              "sig": {"needs": [{"it": {"state": "blocked"}}], "working": 1}}, "recent"],
            }) + ";",
            "const out = {}; for (const k in cases) out[k] = swState(...cases[k]);",
            "console.log(JSON.stringify(out));",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "dot.cjs"
            script.write_text(src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, encoding="utf-8", timeout=60)
        assert proc.returncode == 0, proc.stderr
        cls.got = json.loads(proc.stdout)

    def dot(self, k):
        return (self.got[k]["cls"], self.got[k]["tip"])

    def test_green_is_working_and_nothing_else(self):
        self.assertEqual(self.dot("working"), ("working", "working"))
        self.assertEqual(self.dot("idle"), ("idle", "idle: running, not working"))
        self.assertEqual(self.dot("draft")[0], "off")
        self.assertEqual(self.dot("stopped"), ("off", "stopped"))
        self.assertEqual(self.dot("done"), ("off", "done, stopped"))
        self.assertEqual(self.dot("unread")[0], "new")

    def test_a_po_row_speaks_for_its_tasks(self):
        self.assertEqual(self.dot("po_working"), ("working", "working: 2 of its tasks"))
        self.assertEqual(self.dot("po_idle"), ("idle", "idle: 1 of its tasks running, none working"))
        self.assertEqual(self.dot("po_needs"), ("danger", "1 of its tasks needs you"))

    def test_an_idle_dot_is_a_ring_and_green_is_working(self):
        self.assertIn("--run-working: var(--c-success-bold)", INDEX)
        self.assertIn(".sw-st.idle", INDEX)
        self.assertIn("inset 0 0 0 2px var(--run-idle)", INDEX)


if __name__ == "__main__":
    unittest.main()
