"""A comment on a file is always sendable (#297), in a real browser: a hub in
a thread with throwaway state, headless Chrome over CDP.

* /fileview?path=<a project's Document> with no room: the tray's button is
  "Send to “Motors PO”", Copy is beside it, and a sent comment reaches the
  PO's room (POST /api/room/resume);
* opened from a task's chat (room=): the task stays the default and the
  chooser offers the PO beside it; switching sends there;
* a file outside every project: "This file belongs to no task or project",
  Copy, and a PO to pick; picked, it sends to that PO;
* the comment message (CEO point P257): its heading's name is a link to the
  file's full path, never marked "not written yet"; a bare name the message
  also gives in full is the same link.

Skipped without Node or Chrome.
"""
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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1280,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c + ' ' + buf))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function page(c) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  return { ev, until, go: async url => { await c.send('Page.navigate', { url }, sessionId); await until('typeof FVC !== "undefined" && document.readyState === "complete"'); }, close: () => c.send('Target.closeTarget', { targetId }) };
}
// One comment on the file as a whole, in the tray; then what the tray shows
// once the hub has said where it goes.
const ADD = `(() => { FVC.put({ cid: FVC.id(), at: FVC.now(), note: 'Please check this.', view: CUR_VIEW, start: -1, end: -1, quote: '' }); renderTray(); return 1; })()`;
const TRAY = `(() => { const t = document.getElementById('cmt-tray'); if (!t) return null;
  const b = t.querySelector('.cmt-submit'), s = t.querySelector('.cmt-dest-pick'), n = t.querySelector('.cmt-dest-none');
  return { send: b ? b.textContent : null, copy: !!t.querySelector('.cmt-copy'), none: n ? n.textContent : null,
    opts: s ? [...s.options].map(o => [o.value, o.textContent, o.selected]) : null }; })()`;
const sent = async p => p.ev(`fetch('/__sent').then(r => r.json())`);
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const p = await page(c);
    // 1. A Document, no room: its PO.
    await p.go(A.doc);
    await p.until(`DEST_CHOICES.length > 0`);
    await p.ev(ADD);
    out.doc = await p.ev(TRAY);
    await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await p.until(`!document.getElementById('cmt-tray')`);
    out.docSent = await sent(p);
    // The message as a chat renders it: the heading's name links the full path.
    const body = out.docSent[0].text;
    out.rendered = await p.ev(`(async () => {
      const box = document.createElement('div'); box.className = 'md';
      box.innerHTML = withPathAbbrevs(${JSON.stringify(body)}, () => mdToHtml(${JSON.stringify(body)}));
      document.body.appendChild(box);
      // The old heading: a bare name with its full path on the File: line.
      const old = '## Review comments on \`plan.md\` (1)\\nFile: \`' + ${JSON.stringify(A.docPath)} + '\`\\n\\n**1.** x';
      const box2 = document.createElement('div'); box2.className = 'md';
      box2.innerHTML = withPathAbbrevs(old, () => mdToHtml(old));
      document.body.appendChild(box2);
      // CEO P258: the message as it sits in the trading PO's chat, sent before the fix.
      const p258 = '## Review comments on \`lighter-snapshots.html\` (1)\\nFile: \`' + ${JSON.stringify(A.snapPath)} + '\`\\n\\n**1.** > b. At the save, write only marked items\\n\\n(around line 58 of the source)\\n\\nexplain how in details';
      const box3 = document.createElement('div'); box3.className = 'md';
      box3.innerHTML = withPathAbbrevs(p258, () => mdToHtml(p258));
      document.body.appendChild(box3);
      await new Promise(r => setTimeout(r, 1500));
      const links = b => [...b.querySelectorAll('a.file-link')].map(a => ({ text: a.textContent, path: new URLSearchParams(a.getAttribute('href').split('?')[1]).get('path'), title: a.title, missing: a.classList.contains('file-missing') }));
      return { now: links(box), old: links(box2), p258: links(box3), h2: (box.querySelector('h2') || {}).textContent || '' };
    })()`);
    // 2. Opened from the task's chat: the task first, the PO beside it.
    await p.go(A.docFromTask);
    await p.until(`DEST_CHOICES.length > 1`);
    await p.ev(ADD);
    out.fromTask = await p.ev(TRAY);
    await p.ev(`(() => { const s = document.querySelector('.cmt-dest-pick'); s.value = ${JSON.stringify(A.po)}; s.dispatchEvent(new Event('change')); return 1; })()`);
    out.switched = await p.ev(TRAY);
    await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await p.until(`!document.getElementById('cmt-tray')`);
    out.switchSent = await sent(p);
    // 3. Outside every project: say so, Copy, a PO to pick.
    await p.go(A.outside);
    await p.until(`DEST_CHOICES.length > 0`);
    await p.ev(ADD);
    out.outside = await p.ev(TRAY);
    await p.ev(`(() => { const s = document.querySelector('.cmt-dest-pick'); s.value = ${JSON.stringify(A.po)}; s.dispatchEvent(new Event('change')); return 1; })()`);
    out.picked = await p.ev(TRAY);
    await p.ev(`document.querySelector('#cmt-tray .cmt-submit').click(), 1`);
    await p.until(`!document.getElementById('cmt-tray')`);
    out.pickSent = await sent(p);
    // 4. Opened from the task's chat while the hub is slow to say where, then
    // failing: no Send before it answers, and none when it cannot (review 1).
    await p.ev(`fetch('/__owner?mode=hold').then(r => r.ok)`);
    await p.go(A.docFromTask);
    await p.ev(ADD);
    out.pending = await p.ev(TRAY);
    await p.ev(`fetch('/__owner?mode=fail').then(r => r.ok)`);
    await p.until(`DEST_STATE === 'failed'`);
    out.failed = await p.ev(TRAY);
    out.failedReacts = await p.ev(`!!DEST.room`);
    await p.close();
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-foc-", ignore_cleanup_errors=True)
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
        tdir = home / "gearbox"
        (tdir / "repo").mkdir(parents=True)
        task = chatroom.create_room("Gearbox", [{"identity": "claude", "agent": "claude", "cwd": str(tdir / "repo")}])["id"]
        chatroom.patch_room(task, cwd=str(tdir / "repo"), taskDir=str(tdir))
        dashboard.save_projects([{"id": "proj-m", "name": "Motors", "path": str(code), "poRoomId": po,
                                  "createdAt": 1, "home": str(home)}])
        dashboard.save_session_projects({task: "proj-m"})
        doc = home / "Documents" / "plan.md"
        doc.write_text("# Plan\n\nThe gearbox runs at seven to one.\n", encoding="utf-8")
        # CEO P258: a folder name with spaces and a # (the trading PO's message).
        snap = home / "Documents" / "#385 Options Advanced use" / "lighter-snapshots.html"
        snap.parent.mkdir()
        snap.write_text("<p>b. At the save, write only marked items</p>\n", encoding="utf-8")
        cls.snap = snap
        outside = base / "elsewhere" / "notes.md"
        outside.parent.mkdir()
        outside.write_text("# Notes\n\nNothing owns this.\n", encoding="utf-8")
        cls.po, cls.task, cls.doc = po, task, doc
        cls.sent = sent = []
        owner_mode, owner_go = {"m": "pass"}, threading.Event()

        class Handler(dashboard.Handler):
            def do_POST(self):
                if self.path.startswith("/api/room/resume"):
                    n = int(self.headers.get("Content-Length") or 0)
                    sent.append(json.loads(self.rfile.read(n) or b"{}"))
                    self._send_json(200, {"ok": True})
                    return
                super().do_POST()

            def do_GET(self):
                if self.path == "/__sent":
                    self._send_json(200, sent)
                    return
                if self.path.startswith("/__owner?mode="):
                    owner_mode["m"] = self.path.split("=", 1)[1]
                    (owner_go.clear if owner_mode["m"] == "hold" else owner_go.set)()
                    self._send_json(200, {"ok": True})
                    return
                if self.path.startswith("/api/file/owner") and owner_mode["m"] != "pass":
                    owner_go.wait(20)
                    if owner_mode["m"] == "fail":
                        self._send_json(500, {"error": "boom"})
                        return
                super().do_GET()

        # The pages' readable folders: the test's own, as the live hub has the projects'.
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "po": po, "docPath": str(doc), "snapPath": str(snap),
                "doc": f"{b}/fileview?path={quote(str(doc))}",
                "docFromTask": f"{b}/fileview?path={quote(str(doc))}&room={task}&roomName=Gearbox",
                "outside": f"{b}/fileview?path={quote(str(outside))}"}
        with mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True):
            out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_a_document_with_no_room_is_sent_to_its_po(self):
        g = self.got["doc"]
        self.assertEqual(g["send"], "Send to “Motors PO”")
        self.assertTrue(g["copy"], "Copy stays beside Send")
        self.assertIsNone(g["opts"], "one place: no chooser")
        s = self.got["docSent"]
        self.assertEqual(len(s), 1, s)
        self.assertEqual(s[0]["roomId"], self.po)

    def test_the_heading_links_the_full_path(self):
        t = self.got["docSent"][0]["text"]
        self.assertTrue(t.startswith(f"## Review comments on [plan.md](<{self.doc}>) (1)\n\n"), t)
        self.assertNotIn("File: ", t)
        r = self.got["rendered"]
        self.assertEqual(r["now"][0]["text"], "plan.md")
        self.assertEqual(r["now"][0]["path"], str(self.doc))
        self.assertFalse(r["now"][0]["missing"], r)

    def test_a_bare_name_the_message_gives_in_full_is_that_file(self):
        old = self.got["rendered"]["old"]
        self.assertEqual([a["path"] for a in old], [str(self.doc)] * 2, old)
        self.assertFalse(any(a["missing"] for a in old), old)
        self.assertTrue(old[0]["title"].startswith(str(self.doc.parent)), "its folder in the title")

    def test_a_message_sent_before_the_fix_links_its_heading_name(self):
        # CEO P258: the heading's name, in a folder with spaces and a #.
        got = self.got["rendered"]["p258"]
        self.assertEqual([(a["text"], a["path"]) for a in got],
                         [("lighter-snapshots.html", str(self.snap)), (str(self.snap), str(self.snap))], got)
        self.assertFalse(any(a["missing"] for a in got), got)

    def test_opened_from_a_task_it_stays_the_tasks_with_the_po_beside_it(self):
        g = self.got["fromTask"]
        self.assertEqual(g["send"], "Send to “Gearbox”")
        self.assertEqual([(o[0], o[2]) for o in g["opts"]], [(self.task, True), (self.po, False)])
        self.assertEqual(self.got["switched"]["send"], "Send to “Motors PO”")
        self.assertEqual(self.got["switchSent"][-1]["roomId"], self.po)

    def test_no_send_until_the_hub_says_where_and_none_when_it_cannot(self):
        self.assertIsNone(self.got["pending"]["send"], self.got["pending"])
        self.assertIn("Finding", self.got["pending"]["none"])
        self.assertIsNone(self.got["failed"]["send"], self.got["failed"])
        self.assertIn("Could not find", self.got["failed"]["none"])
        self.assertTrue(self.got["failed"]["copy"])
        self.assertFalse(self.got["failedReacts"], "a room= the hub never confirmed was used")

    def test_a_file_outside_every_project(self):
        g = self.got["outside"]
        self.assertIsNone(g["send"], "nowhere chosen yet: no Send")
        self.assertTrue(g["copy"])
        self.assertEqual(g["none"], "This file belongs to no task or project.")
        self.assertEqual(g["opts"][0], ["", "Choose a PO…", True])
        self.assertIn(self.po, [o[0] for o in g["opts"]])
        self.assertEqual(self.got["picked"]["send"], "Send to “Motors PO”")
        self.assertEqual(self.got["pickSent"][-1]["roomId"], self.po)


if __name__ == "__main__":
    unittest.main()
