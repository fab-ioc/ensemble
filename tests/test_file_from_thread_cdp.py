"""End to end in Chrome (#414, the CEO's P444): a file opened from a thread's
balloon, commented on and sent, lands in that thread, not in the PO's main
chat.

The thread page (/session?room=<PO>&thread=P1) shows the PO's answer naming a
Document; its file link carries the thread; the file view opened from it
defaults to that thread (the hub's real /api/file/owner); Send goes through
the hub's real /api/room/resume. The hub's delivery is stubbed as in
tests/test_thread_reply_cdp.py: the message is typed into the PO's transcript
and answered, as a running PO would. Then the thread holds the comments and
the main chat does not."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
import sends  # noqa: E402
import threads  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests import test_file_owner_cdp as foc  # noqa: E402

HEAD = foc.CDP_JS.split("async function page(c)")[0]
CDP_JS = HEAD + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s);
    const ev = async e => { const r = await c.send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(e.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
    const until = async (e, what, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(e); } catch (x) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + (what || e)); };
    // 1. The thread, as the CEO has it open: the PO's answer names the plan.
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.po + '&thread=P1' }, s);
    out.href = await until(`(() => { const a = [...document.querySelectorAll('#msgs a.file-link')].find(a => a.textContent.includes('plan.md')); return a ? a.getAttribute('href') : ''; })()`, 'the file link in the thread');
    // 2. The file opened from it: the tray's place, then Send.
    await c.send('Page.navigate', { url: A.base + out.href }, s);
    await until(`typeof FVC !== 'undefined' && document.readyState === 'complete' && DEST_STATE === 'ok'`, 'the file view');
    await ev(`(() => { FVC.put({ cid: FVC.id(), at: FVC.now(), note: 'TOKZ seven is too many', view: CUR_VIEW, start: -1, end: -1, quote: '' }); renderTray(); return 1; })()`);
    out.tray = await ev(`(() => { const t = document.getElementById('cmt-tray'), b = t.querySelector('.cmt-submit'), s = t.querySelector('.cmt-dest-pick');
      return { send: b ? b.textContent : null, title: b ? b.title : null, picked: s ? s.value : null }; })()`);
    await ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await until(`!document.getElementById('cmt-tray')`, 'sent');
    // 3. In the thread (the PO's answer too), not in the main chat.
    const THREAD = `fetch('/api/room/thread?id=${A.po}&t=P1', { cache: 'no-store' }).then(r => r.json())`;
    await until(`${THREAD}.then(j => JSON.stringify(j.items || []).includes('Answer to TOKZ'))`, 'the PO answered in the thread');
    out.thread = await ev(`${THREAD}.then(j => (j.items || []).map(i => ({ mid: i.mid, text: String(i.text || '').slice(0, 300) })))`);
    out.channel = await ev(`fetch('/api/room/channel?id=${A.po}', { cache: 'no-store' }).then(r => r.json()).then(j => (j.items || []).map(i => ({ mid: i.mid, text: String(i.text || '').slice(0, 300), inThr: i.inThr || '' })))`);
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class FileFromThread(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-fft-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
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
            mock.patch.object(dashboard, "_send_left_behind", lambda *a, **k: ""),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "_resume_room", cls._deliver),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        (home / "Documents").mkdir(parents=True, exist_ok=True)
        cls.doc = home / "Documents" / "plan.md"
        cls.doc.write_text("# Plan\n\nThe gearbox runs at seven to one.\n", encoding="utf-8")
        cls.sid = sid = "fft-po"
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "role": "Product owner",
                                               "sessionId": sid, "cwd": str(home)}])
        room = chatroom.get_room(po["id"], public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(po["id"], proj["id"])
        dashboard.set_project_po(proj["id"], po["id"])
        cls.po = po["id"]
        (base / "transcripts" / "C--po").mkdir()
        cls.transcript = base / "transcripts" / "C--po" / f"{sid}.jsonl"
        at = time.time() - 3600
        room = chatroom.get_room(cls.po, public=False)
        said = []
        for t, n in (("Write the gearbox plan.", 1), ("And the motor list.", 20)):
            q, ids = points.take(room, t, "", "", now=at + n)
            threads.note_send(cls.po, "", "", ids, False, q, now=at + n, new=True)
            said.append(q)
        lines = [cls.turn("user", said[0], at + 1),
                 cls.turn("assistant", f"Re P1: the plan is in {cls.doc}", at + 2),
                 cls.turn("user", said[1], at + 20),
                 cls.turn("assistant", "Re P2: listed.", at + 21)]
        cls.transcript.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "po": cls.po}
        with mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True):
            out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".%03dZ" % int(ts % 1 * 1000)
        msg = {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}
        if role == "assistant":
            msg["stop_reason"] = "end_turn"
        return {"type": role, "timestamp": stamp, "sessionId": cls.sid, "message": msg}

    @staticmethod
    def _deliver(h, room, text="", to="", key="", quiet=False, **kw):
        """Typed in at once; the PO reads it a moment later and answers in the same thread."""
        cls = FileFromThread
        sends.mark(room["id"], [key], "delivered")

        def read():
            time.sleep(0.5)
            now = time.time()
            with cls.transcript.open("a", encoding="utf-8") as f:
                f.write(json.dumps(cls.turn("user", text, now)) + "\n")
                f.write(json.dumps(cls.turn("assistant", f'{text.split(":")[0]}: Answer to TOKZ.', now + .5)) + "\n")
        threading.Thread(target=read, daemon=True).start()
        return {"delivered": 1}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_file_link_in_a_thread_names_the_thread(self):
        self.assertIn("&thread=P1", self.got["href"])
        self.assertIn(f"&room={self.po}", self.got["href"])

    def test_the_file_view_opened_from_it_sends_to_that_thread(self):
        t = self.got["tray"]
        self.assertEqual((t["send"], t["picked"]), ("Send to thread", f"{self.po}|P1|"), t)

    def test_the_comments_land_in_the_thread_not_the_main_chat(self):
        mine = [i for i in self.got["thread"] if "TOKZ seven is too many" in i["text"]]
        self.assertEqual(len(mine), 1, self.got["thread"])
        self.assertTrue(mine[0]["text"].startswith("Re P1:"), mine[0])
        self.assertTrue(any("Answer to TOKZ" in i["text"] for i in self.got["thread"]), "the PO answered there")
        leaked = [i for i in self.got["channel"] if "TOKZ" in i["text"]]
        self.assertEqual(leaked, [], "nothing of it in the main chat")


if __name__ == "__main__":
    unittest.main()
