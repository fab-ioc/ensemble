"""#228: a chat frame the dashboard keeps hidden polls once a minute, not every
2 s; it polls at once when it comes back into view; and the observer that
tells it so goes with the page, so a frame reloaded many times holds one.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_hidden_chat_polling
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

HOST = """<!doctype html><meta charset="utf-8"><title>host</title>
<body style="margin:0"><iframe id="f" style="width:800px;height:600px;border:0"></iframe></body>"""

# Counts the host's live IntersectionObservers: observe() adds one, disconnect() takes it away.
COUNT = r"""
if (window === window.top) {
  const IO = window.IntersectionObserver;
  window.__liveIO = 0;
  window.IntersectionObserver = class extends IO {
    observe(t) { if (!this.__on) { this.__on = true; window.__liveIO++; } return super.observe(t); }
    disconnect() { if (this.__on) { this.__on = false; window.__liveIO--; } return super.disconnect(); }
  };
}
"""

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[process.argv.length - 1]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1200,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  try {
    const wsUrl = await new Promise((res, rej) => { let b = ''; ch.stderr.on('data', d => { b += d; const m = b.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
      setTimeout(() => rej(new Error('no devtools')), 20000); });
    const ws = new WebSocket(wsUrl); await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
    let id = 0; const waits = new Map();
    ws.onmessage = ev => { const m = JSON.parse(ev.data); const w = m.id && waits.get(m.id); if (w) { waits.delete(m.id); m.error ? w.j(new Error(m.error.message)) : w.r(m.result); } };
    const send = (method, params = {}, sessionId) => new Promise((r, j) => { const i = ++id; waits.set(i, { r, j }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    await send('Page.enable', {}, sessionId);
    await send('Page.addScriptToEvaluateOnNewDocument', { source: A.count }, sessionId);
    const ev = async e => { const r = await send('Runtime.evaluate', { expression: e, awaitPromise: true, returnByValue: true }, sessionId);
      if (r.exceptionDetails) throw new Error(e.slice(0, 100) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
    const until = async (e, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(e); } catch (x) {} if (v) return v; await sleep(100); } throw new Error('timeout: ' + e); };
    const W = 'document.getElementById("f").contentWindow';
    const loaded = () => until(`(() => { try { const w = ${W}; return w.document.readyState === 'complete' && typeof w.chatOffScreen === 'function' && w.chatOffScreen.inView === true; } catch (e) { return false; } })()`);
    const rooms = `${W}.performance.getEntriesByType('resource').filter(e => e.name.includes('/api/room?')).length`;
    await send('Page.navigate', { url: A.base + '/static/t228-host.html' }, sessionId);
    await until('!!document.getElementById("f")');
    await ev(`document.getElementById('f').src = ${JSON.stringify('/session?room=' + A.room)}, 0`);
    await loaded();
    const out = { first: await ev(`({ off: ${W}.chatOffScreen(), live: window.__liveIO })`) };
    // Hidden by the dashboard (display:none): off screen at once.
    await ev(`document.getElementById('f').style.display = 'none', 0`);
    out.none = await ev(`${W}.chatOffScreen()`);
    await ev(`document.getElementById('f').style.display = '', 0`);
    // Out of the viewport: the observer says so.
    await ev(`Object.assign(document.getElementById('f').style, { position: 'absolute', top: '5000px' }), 0`);
    await until(`${W}.chatOffScreen.inView === false`);
    out.away = await ev(`${W}.chatOffScreen()`);
    const before = await ev(rooms);
    await ev(`Object.assign(document.getElementById('f').style, { position: '', top: '' }), 0`);
    await until(`${W}.chatOffScreen.inView === true`);
    // Back in view: it polls at once, not on its next 2 s tick.
    await until(`${rooms} > ${before}`, 1500).then(() => { out.pollAtOnce = true; }, () => { out.pollAtOnce = false; });
    out.back = await ev(`${W}.chatOffScreen()`);
    // Reloaded five times: one observer, the page's own.
    for (let i = 0; i < 5; i++) {
      await ev(`${W}.chatOffScreen.inView = 'old', document.getElementById('f').src = document.getElementById('f').src, 0`);
      await until(`(() => { try { return ${W}.chatOffScreen.inView !== 'old'; } catch (e) { return false; } })()`);
      await loaded();
    }
    out.afterReloads = await ev('window.__liveIO');
    console.log(JSON.stringify(out));
  } finally { ch.kill(); }
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


@unittest.skipUnless(NODE and chrome_profile.CHROME, "node and Chrome are needed")
class HiddenChatPolling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-228-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        static = base / "site"
        (static / "static").mkdir(parents=True)
        for rel in dashboard.PAGE_FILES:
            (static / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, static / rel)
        for sub in ("vendor",):
            if (ROOT / "static" / sub).is_dir():
                shutil.copytree(ROOT / "static" / sub, static / "static" / sub, dirs_exist_ok=True)
        (static / "static" / "t228-host.html").write_text(HOST, encoding="utf-8")
        state = base / "state"
        state.mkdir()
        for d in ("transcripts", "cs", "EnsembleProjects"):
            (base / d).mkdir()
        patches = [
            mock.patch.object(dashboard, "STATIC_DIR", static),
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
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
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        dashboard._STAMP_CACHE.clear()
        cls.addClassCleanup(dashboard._STAMP_CACHE.clear)
        cwd = str(base / "EnsembleProjects")
        room = chatroom.create_room("Talk", [{"identity": "claude", "agent": "claude", "cwd": cwd},
                                             {"identity": "codex", "agent": "codex", "cwd": cwd}])
        for i in range(6):
            chatroom.post_message(room["id"], "user" if i % 2 else "claude", f"message {i}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{server.server_address[1]}",
                "room": room["id"], "count": COUNT}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=240)
        assert out.returncode == 0, out.stderr[-3000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.addClassCleanup(cls.tmp.cleanup)

    def test_a_shown_chat_is_on_screen_and_holds_one_observer(self):
        self.assertEqual(self.got["first"], {"off": False, "live": 1})

    def test_hidden_or_out_of_view_is_off_screen(self):
        self.assertTrue(self.got["none"], "display:none")
        self.assertTrue(self.got["away"], "out of the viewport")
        self.assertFalse(self.got["back"], "back in view")

    def test_back_in_view_it_polls_at_once(self):
        self.assertTrue(self.got["pollAtOnce"])

    def test_reloads_leave_one_observer(self):
        self.assertEqual(self.got["afterReloads"], 1)


if __name__ == "__main__":
    unittest.main()
