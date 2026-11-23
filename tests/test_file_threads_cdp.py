"""The file view's tray in Chrome (#414, the CEO's P444): opened from a
thread of a PO chat, its comments go to that thread by default, as one typed
in the thread's box would (the send names the thread; a thread with no
point yet goes with its first message's link); another thread picked is
kept for these comments across a reload; a new thread is named after the
file; a thread closed since is said, its main chat standing in.

/api/file/owner is answered here as the hub answers it (its own tests are
tests/test_file_threads.py), so this is the page's side only."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import test_file_owner_cdp as foc  # noqa: E402

# launch(), Cdp and page() of the file owner's test; this test's own steps.
HEAD = foc.CDP_JS.split("// One comment on the file as a whole")[0]
CDP_JS = HEAD + r"""
const ADD = note => `(() => { FVC.put({ cid: FVC.id(), at: FVC.now(), note: ${JSON.stringify(note)}, view: CUR_VIEW, start: -1, end: -1, quote: '' }); renderTray(); return 1; })()`;
const TRAY = `(() => { const t = document.getElementById('cmt-tray'); if (!t) return null;
  const b = t.querySelector('.cmt-submit'), s = t.querySelector('.cmt-dest-pick');
  const gone = t.querySelector('.cmt-dest-gone'), hint = t.querySelector('.cmt-dest-hint');
  return { send: b ? b.textContent : null, title: b ? b.title : null, cut: b ? b.scrollWidth > b.clientWidth : null,
    groups: s ? [...s.querySelectorAll('optgroup')].map(g => [g.label, [...g.querySelectorAll('option')].map(o => o.textContent)]) : null,
    picked: s ? s.value : null, items: t.querySelectorAll('.cmt-item').length,
    gone: gone ? gone.textContent : null, hint: hint ? hint.textContent : null }; })()`;
const PICK = v => `(() => { const s = document.querySelector('.cmt-dest-pick'); s.value = ${JSON.stringify(v)}; s.dispatchEvent(new Event('change')); return s.value; })()`;
const SEND = async p => { await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`); await p.until(`!document.getElementById('cmt-tray')`); };
const got = async p => p.ev(`fetch('/__got').then(r => r.json())`);
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const p = await page(c);
    // 1. Opened from thread P2 (no point yet): it is the default; sent there with its first message's link.
    await p.go(A.fromP2);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Seven is too many.'));
    out.p2 = await p.ev(TRAY);
    await SEND(p);
    out.p2Sent = (await got(p)).sent.slice(-1)[0];
    // 2. Opened from P2 again, P1 picked, the page reloaded: P1 is still the place.
    await p.go(A.fromP2);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Six then.'));
    await p.ev(PICK(A.po + '|P1|'));
    out.p1 = await p.ev(TRAY);
    await p.go(A.fromP2);
    await p.until(`DEST_STATE === 'ok'`);
    out.reloaded = await p.ev(TRAY);
    out.asked = (await got(p)).asked.slice(-1)[0];
    await SEND(p);
    out.p1Sent = (await got(p)).sent.slice(-1)[0];
    // 3. A new thread: named after the file, one message.
    await p.go(A.fromP2);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Start over.'));
    await p.ev(PICK(A.po + '||new'));
    out.fresh = await p.ev(TRAY);
    await SEND(p);
    out.freshSent = (await got(p)).sent.slice(-1)[0];
    // 4. Opened from a thread closed since: said; the main chat stands in.
    await p.go(A.fromP7);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Still there?'));
    out.closed = await p.ev(TRAY);
    await SEND(p);
    out.closedSent = (await got(p)).sent.slice(-1)[0];
    // 5. P2 closed after the tray was filled: refused, nothing sent; the tray says so and the main chat stands in.
    await p.go(A.fromP2);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Too late?'));
    out.beforeClose = await p.ev(TRAY);
    await p.ev(`fetch('/__close?t=P2').then(() => 1)`);
    await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await p.until(`!FV_SENDING && DEST.kind === 'po' && !!document.querySelector('#cmt-tray .cmt-dest-gone')`);
    out.refused = (await got(p)).sent.slice(-1)[0];
    out.afterClose = await p.ev(TRAY);
    await SEND(p);
    out.afterCloseSent = (await got(p)).sent.slice(-1)[0];
    // 6. A send to P1 whose reply is lost, then the main chat picked: the retry still goes to P1, once.
    await p.go(A.fromMain);
    await p.until(`DEST_STATE === 'ok'`);
    await p.ev(ADD('Lost on the way.'));
    await p.ev(PICK(A.po + '|P1|'));
    await p.ev(`fetch('/__drop').then(() => 1)`);
    const n = (await got(p)).sent.length;
    await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await p.until(`!FV_SENDING && !!document.querySelector('.cmt-composer .cmt-quote')`);
    await p.ev(`fetch('/__undrop').then(() => 1)`);
    out.unsure = await p.ev(`document.querySelector('.cmt-composer .cmt-quote').textContent`);
    await p.ev(`document.querySelector('.cmt-composer .cmt-cancel').click(), 1`);
    await p.ev(PICK(A.po + '||'));
    out.repicked = await p.ev(TRAY);
    await SEND(p);
    out.lost = (await got(p)).sent.slice(n);
    await p.close();
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


CLOSED: set = set()    # threads closed while the page is open (/__close)


def owner_answer(path: str, po: str, q: dict) -> dict:
    """/api/file/owner as the hub gives it for a Document of the PO chat ``po``
    whose threads are P1 (with a point), P2 (none yet) and P7 (closed)."""
    thread, pick = q.get("thread", [""])[0], q.get("pick", [""])[0]
    main = {"room": po, "name": "Motors PO", "kind": "po", "projectId": "proj-m"}
    t1 = {"room": po, "thread": "P1", "name": "Make the chart wider", "kind": "thread", "projectId": "proj-m",
          "root": "sid-1:0", "hasPoints": True}
    t2 = {"room": po, "thread": "P2", "name": "Fix the login page", "kind": "thread", "projectId": "proj-m",
          "root": "sid-1:4", "hasPoints": False}
    new = {"room": po, "kind": "new", "name": Path(path).name, "projectId": "proj-m", "why": "new"}
    titles = {"P7": "Old one", "P2": "Fix the login page", "P1": "Make the chart wider"}
    shut = {"P7"} | CLOSED
    gone = [{"id": t, "title": titles[t], "why": "closed"} for t in dict.fromkeys((thread, pick)) if t in shut]
    if thread == "P2" and "P2" in shut:
        choices = [{**main, "why": "main"}, {**t1, "why": "thread"}, new]
    elif thread == "P2":
        choices = [{**t2, "why": "opener"}, {**main, "why": "main"}, {**t1, "why": "thread"}, new]
    else:
        choices = [{**main, "why": "opener"}, {**t1, "why": "thread"}, {**t2, "why": "thread"}, new]
    choices = [c for c in choices if c.get("thread") not in CLOSED]
    first = choices[0]
    return {"path": path, "owner": main, "opener": main, "openerGone": False, "room": po,
            "thread": first.get("thread", ""), "threadRoom": po, "gone": gone, "choices": choices}


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-fth-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, root = base / "state", base / "EnsembleProjects"
        state.mkdir()
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        for p in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        home, code = root / "Motors", base / "code"
        (home / "Documents").mkdir(parents=True)
        code.mkdir()
        po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])["id"]
        dashboard.save_projects([{"id": "proj-m", "name": "Motors", "path": str(code), "poRoomId": po,
                                  "createdAt": 1, "home": str(home)}])
        doc = home / "Documents" / "plan.md"
        doc.write_text("# Plan\n\nThe gearbox runs at seven to one.\n", encoding="utf-8")
        cls.po, cls.doc = po, doc
        sent, asked, drop = [], [], []

        class Handler(dashboard.Handler):
            def do_POST(self):
                if self.path.startswith("/api/room/resume"):
                    n = int(self.headers.get("Content-Length") or 0)
                    req = json.loads(self.rfile.read(n) or b"{}")
                    sent.append(req)
                    if drop:          # the reply lost on its way back: no HTTP answer (until
                        # /__undrop: Chrome sends it again itself on a kept-alive socket)
                        self.wfile.write(b"lost\r\n\r\n")
                        self.close_connection = True
                        return
                    if req.get("thread") in CLOSED and req.get("liveThread"):
                        self._send_json(409, {"error": "thread_gone", "threadGone": "closed", "thread": req["thread"]})
                        return
                    self._send_json(200, {"ok": True})
                    return
                super().do_POST()

            def do_GET(self):
                if self.path.startswith("/__close"):
                    CLOSED.add(parse_qs(urlsplit(self.path).query)["t"][0])
                    self._send_json(200, {"ok": True})
                    return
                if self.path in ("/__drop", "/__undrop"):
                    drop[:] = [1] if self.path == "/__drop" else []
                    self._send_json(200, {"ok": True})
                    return
                if self.path == "/__got":
                    self._send_json(200, {"sent": sent, "asked": asked})
                    return
                if self.path.startswith("/api/file/owner"):
                    q = parse_qs(urlsplit(self.path).query)
                    asked.append({k: v[0] for k, v in q.items() if k in ("room", "thread", "pick")})
                    self._send_json(200, owner_answer(str(doc), po, q))
                    return
                super().do_GET()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.origin = b
        at = f"{b}/fileview?path={quote(str(doc))}&room={po}&roomName=Motors%20PO"
        args = {**foc.chrome_profile.node_args(), "tmp": cls.tmp.name, "po": po,
                "fromP2": at + "&thread=P2&threadName=" + quote("Fix the login page"),
                "fromP7": at + "&thread=P7&threadName=" + quote("Old one"), "fromMain": at}
        with mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True):
            out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_opened_from_a_thread_the_comments_go_there(self):
        g = self.got["p2"]
        self.assertEqual(g["send"], "Send to thread")
        self.assertEqual(g["title"], "Send to “Thread · Fix the login page”")
        self.assertEqual(g["picked"], f"{self.po}|P2|")
        self.assertEqual(g["groups"], [
            ["Motors PO", ["Thread · Fix the login page (opened here)", "Motors PO · main chat"]],
            ["Other threads of Motors PO, latest first", ["Thread · Make the chart wider"]]])
        s = self.got["p2Sent"]
        self.assertEqual((s["roomId"], s["thread"]), (self.po, "P2"))
        self.assertNotIn("newThread", s)
        # No point there yet: the PO is shown which thread by its first message's link.
        self.assertTrue(s["text"].startswith(f"{self.origin}/session?room={self.po}&msg=sid-1:4\n\n## Review comments on"),
                        s["text"])
        self.assertIn("Seven is too many.", s["text"])
        self.assertTrue(s["key"].startswith("fv:"))

    def test_another_thread_picked_is_kept_across_a_reload(self):
        self.assertEqual(self.got["p1"]["picked"], f"{self.po}|P1|")
        r = self.got["reloaded"]
        self.assertEqual((r["picked"], r["items"], r["send"]), (f"{self.po}|P1|", 1, "Send to thread"))
        self.assertEqual(self.got["asked"], {"room": self.po, "thread": "P2", "pick": "P1"},
                         "the hub is asked about the thread picked too")
        s = self.got["p1Sent"]
        self.assertEqual(s["thread"], "P1")
        self.assertTrue(s["text"].startswith("## Review comments on"), "a thread with points needs no link")

    def test_a_new_thread_is_named_after_the_file(self):
        g = self.got["fresh"]
        self.assertEqual(g["send"], "Send to new thread")
        self.assertFalse(g["cut"], "the longest Send word fits the tray, not cut")
        self.assertEqual(g["hint"], "Starts a thread named “plan.md” in the Motors PO chat.")
        s = self.got["freshSent"]
        self.assertEqual((s["roomId"], s.get("newThread"), s.get("threadName")), (self.po, True, "plan.md"))
        self.assertNotIn("thread", s)

    def test_a_thread_closed_since_is_said_and_the_main_chat_stands_in(self):
        g = self.got["closed"]
        self.assertEqual(g["send"], "Send to main chat")
        self.assertEqual(g["gone"], "The thread “Old one” was closed: these go to the main chat.")
        s = self.got["closedSent"]
        self.assertEqual(s["roomId"], self.po)
        self.assertNotIn("thread", s)
        self.assertNotIn("newThread", s)

    def test_a_thread_closed_after_the_tray_was_filled_sends_nothing_and_says_so(self):
        self.assertEqual(self.got["beforeClose"]["send"], "Send to thread")
        r = self.got["refused"]
        self.assertEqual((r["thread"], r.get("liveThread")), ("P2", True), "the hub is asked for it live")
        a = self.got["afterClose"]
        self.assertEqual((a["send"], a["items"]), ("Send to main chat", 1), "kept, the main chat standing in")
        self.assertEqual(a["gone"], "The thread “Fix the login page” was closed: these go to the main chat.")
        s = self.got["afterCloseSent"]
        self.assertEqual(s["roomId"], self.po)
        self.assertNotIn("thread", s)
        self.assertIn("Too late?", s["text"])

    def test_a_send_not_answered_is_sent_again_to_the_same_place_whatever_is_picked_since(self):
        self.assertIn("they go to “Thread · Make the chart wider”", self.got["unsure"])
        self.assertEqual(self.got["repicked"]["picked"], f"{self.po}||")
        lost = self.got["lost"]
        self.assertGreaterEqual(len(lost), 2)
        self.assertEqual({(s.get("thread"), s["key"]) for s in lost}, {("P1", lost[0]["key"])},
                         "the same place and key: the hub counts it once")


if __name__ == "__main__":
    unittest.main()
