"""#132 (P80): a Dock library that fails to load is tried again, and
a page that gives up says so.

Before, one failed load of the panels' library (static/dock/src/index.js or
dock.css) set PD.failed for the rest of the page's life: a project's Overview
went back to the old board with tabs and the PO in the pill's drawer, with
nothing on screen and only a console.warn.

In headless Chrome over CDP, against a hub in a thread whose static files can
be made to fail (503) a given number of times, with PD_RETRY_MS shortened:

* the library failing twice: the page says it is trying again, loads it on the
  third try, and the panels show; the hub's log has each failure and the load;
* an inner module (dock.js) failing once: the next try loads it;
* the library failing every time: after the last try the page says so above
  the bar with Reload, and the hub's log says it gave up; Reload, with the hub
  answering again, brings the panels back.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// What the page shows: the panels or the old board, and the line above the bar.
const STATE = `(() => { const b = document.getElementById('dock-banner'), go = document.getElementById('dock-banner-go');
  const tabs = document.querySelector('#view .ptabs');
  return { lib: !!PD.lib, failed: PD.failed || '', tries: PD.tries || 0, dock: document.body.classList.contains('po-dock') && !!document.querySelector('#po-dock .dk-strip-btn'),
    tabs: !!tabs && tabs.getBoundingClientRect().height > 0,
    banner: !b.hidden && b.getBoundingClientRect().height > 0 ? document.getElementById('dock-banner-text').textContent : '',
    reload: !!go && !b.hidden && go.getBoundingClientRect().height > 0 && go.textContent.trim(),
    barBelow: document.querySelector('header, #bar, .top') ? Math.round(document.querySelector('header, #bar, .top').getBoundingClientRect().top) >= Math.round(b.hidden ? 0 : b.getBoundingClientRect().bottom) : true }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const fail = (path, n) => fetch(A.base + '/__fail?path=' + encodeURIComponent(path) + '&n=' + n).then(r => r.json());
  const page = async () => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Network.enable', {}, sessionId);
    await c.send('Network.setCacheDisabled', { cacheDisabled: true }, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.PD_RETRY_MS = [700, 700, 700];' }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(100); } throw new Error('timeout: ' + expr); };
    const open = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0', 30000);
      await evalIn(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); 0`);
    };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    return { evalIn, until, open, click, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  try {
    // ---- the library failing twice
    {
      await fail('/static/dock/src/index.js', 2);
      const p = await page();
      await p.open();
      out.retrying = await p.until(`(() => { const s = ${STATE}; return s.banner ? s : null; })()`, 10000);
      await p.until('!!PD.lib && document.body.classList.contains("po-dock")', 20000);
      await sleep(300);
      out.recovered = await p.evalIn(STATE);
      out.left1 = await fail('/static/dock/src/index.js', 0);
      await p.close();
    }
    // ---- an inner module failing once
    {
      await fail('/static/dock/src/dock.js', 1);
      const p = await page();
      await p.open();
      await p.until('!!PD.lib || !!PD.failed', 20000);
      await sleep(300);
      out.inner = await p.evalIn(STATE);
      out.left2 = await fail('/static/dock/src/dock.js', 0);
      await p.close();
    }
    // ---- failing every time: the page gives up and says so; Reload brings the panels
    {
      await fail('/static/dock/src/index.js', 1000);
      const p = await page();
      await p.open();
      await p.until('!!PD.failed', 20000);
      await sleep(300);
      out.gaveUp = await p.evalIn(STATE);
      await fail('/static/dock/src/index.js', 0);
      await p.click('#dock-banner-go');
      await sleep(1500);
      await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0', 30000);
      await p.evalIn(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); 0`);
      await p.until('!!PD.lib && document.body.classList.contains("po-dock")', 20000);
      await sleep(300);
      out.reloaded = await p.evalIn(STATE);
      await p.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

FAIL: dict[str, int] = {}
FAIL_LOCK = threading.Lock()
LOG: list[str] = []


class FlakyHandler(dashboard.Handler):
    """The hub, with static files that answer 503 a set number of times."""

    def do_GET(self):
        u = urlparse(self.path)
        # The library under a new folder (/static/dock@N/) is the same files.
        path = re.sub(r"^/static/dock@\d+/", "/static/dock/", u.path)
        if u.path == "/__fail":
            q = parse_qs(u.query)
            with FAIL_LOCK:
                path = q["path"][0]
                left = FAIL.get(path, 0)
                FAIL[path] = int(q["n"][0])
            self._send_json(200, {"left": left})
            return
        with FAIL_LOCK:
            n = FAIL.get(path, 0)
            if n > 0:
                FAIL[path] = n - 1
        if n > 0:
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        super().do_GET()


class TheWiring(unittest.TestCase):
    def test_a_failed_load_is_tried_again_and_logged(self):
        load = INDEX[INDEX.index("function pdLoad()"):INDEX.index("// A panel's own window shows the app's icon")]
        self.assertIn("PD_RETRY[n - 1]", load)
        self.assertIn("'?try=' + n", load, "a new try asks under another address")
        self.assertIn("fetch('/api/page-log'", load)
        self.assertIn('<div id="dock-banner" role="status" hidden>', INDEX)

    def test_the_hub_logs_a_pages_line(self):
        lines = []
        h = FlakyHandler.__new__(FlakyHandler)
        h.headers = {}
        sent = []
        h._send_json = lambda code, obj: sent.append((code, obj))
        with mock.patch.object(dashboard, "print", create=True, new=lambda *a, **k: lines.append(" ".join(map(str, a)))):
            # The same lines as _do_POST's, with a body of more than one line.
            data = {"what": "po-dock", "msg": "the panels did not load\n(try 1 of 5):\x1b[31m x" + "y" * 900}
            h.command = "POST"
            h.path = "/api/page-log"
            body = json.dumps(data).encode()
            h.headers = {"Content-Length": str(len(body))}
            import io
            h.rfile = io.BytesIO(body)
            h._gate = lambda: True
            h._do_POST()
        self.assertEqual(sent, [(200, {"ok": True})])
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[page] po-dock: the panels did not load (try 1 of 5):[31m xyyy"), lines[0])
        self.assertNotIn("\x1b", lines[0], "no terminal escapes in the log")
        self.assertLessEqual(len(lines[0]), len("[page] po-dock: ") + 500)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ALibraryThatFailsToLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dockretry-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        LOG.clear()

        def log(*a, **k):
            s = " ".join(map(str, a))
            if s.startswith("[page]"):
                LOG.append(s)
        cls.patches = [
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
            mock.patch.object(dashboard, "print", create=True, new=log),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        chatroom.post_message(po["id"], "user", "Hello")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": proj["id"]}
        script = base / "dockretry_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.log = list(LOG)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_two_failures_then_the_panels(self):
        r = self.got["retrying"]
        self.assertFalse(r["lib"])
        self.assertIn("trying again", r["banner"], "said on screen while it tries again")
        self.assertEqual(r["reload"], "Reload")
        g = self.got["recovered"]
        self.assertTrue(g["lib"] and g["dock"], g)
        self.assertEqual((g["failed"], g["banner"]), ("", ""), "loaded; the line goes")
        self.assertGreaterEqual(g["tries"], 2)
        self.assertEqual(self.got["left1"]["left"], 0, "both failures were served")
        page1 = self.log[:self.log.index(next(x for x in self.log if "loaded on try" in x)) + 1]
        for t in range(1, g["tries"]):
            self.assertEqual(sum(f"did not load (try {t} of 4)" in x for x in page1), 1, page1)
        self.assertIn(f"loaded on try {g['tries']}", page1[-1])

    def test_an_inner_module_failing_once(self):
        g = self.got["inner"]
        self.assertEqual(self.got["left2"]["left"], 0, "the failure was served")
        self.assertTrue(g["lib"] and g["dock"], g)
        self.assertEqual((g["tries"], g["banner"]), (2, ""), "the next try, under a new folder, loads it")

    def test_giving_up_is_said_with_reload(self):
        g = self.got["gaveUp"]
        self.assertFalse(g["lib"] or g["dock"])
        self.assertTrue(g["failed"])
        self.assertEqual(g["tries"], 4)
        self.assertTrue(g["tabs"], "the old project page stands in")
        self.assertIn("could not load", g["banner"])
        self.assertEqual(g["reload"], "Reload")
        self.assertTrue(g["barBelow"], "the line is above the bar, not over it")
        self.assertEqual(sum("gave up" in x for x in self.log), 1, self.log)
        r = self.got["reloaded"]
        self.assertTrue(r["lib"] and r["dock"], r)
        self.assertEqual((r["failed"], r["banner"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
