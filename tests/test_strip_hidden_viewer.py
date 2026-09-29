"""#132: a file open in Files never shows while another tool is open.

Dock keeps one flyout per strip tool, each laid out beside the conversation,
and hides the closed ones with ``visibility: hidden``. A Files viewer that is
showing says ``visibility: visible`` itself (.wsp-frame.on), which beats a
hidden ancestor: with a file open in Files and Your asks open instead, the
closed Files flyout's viewer was painted over Your asks (P79).

In headless Chrome over CDP, against a hub in a thread, with a project that has
a PO and a README in its folder: open the README in the strip's Files, then
Your asks, Changes, Board, Spec, none; reload; pop Files out and dock it back;
a narrower window. After each step only the open tool's content is painted:
no Files viewer is visible outside an open Files, and every point of the open
tool hits that tool.

Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
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
// What is painted: the Files viewers that are visible and whether each is in an
// open Files; and, over a grid of the open tool (or the middle), what a point hits.
const PAINT = `(() => {
  const fly = PD.dock.flyOpen(), open = document.querySelector('#po-dock .dk-flyout.open');
  const frames = [...document.querySelectorAll('iframe.wsp-frame')].filter(f => f.checkVisibility({ visibilityProperty: true }))
    .map(f => { const b = f.getBoundingClientRect(); return { inOpenFiles: fly === 'workspace' && !!open && open.contains(f), x: Math.round(b.left), w: Math.round(b.width) }; });
  const area = open || PD.els['po-chat'], r = area.getBoundingClientRect(), strays = [];
  for (let i = 1; i < 8; i++) for (let j = 1; j < 8; j++) {
    const x = r.left + r.width * i / 8, y = r.top + r.height * j / 8, h = document.elementFromPoint(x, y);
    if (h && !area.contains(h)) strays.push((h.className && h.className.baseVal === undefined ? h.tagName + '.' + h.className : h.tagName).slice(0, 60));
  }
  return { fly, frames, strays };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  const size = (w, h) => c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: false }, sessionId);
  await size(1440, 900);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
  const click = async (sel) => {
    const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
  };
  const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
  const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0', 30000);
  const toPo = (keep) => evalIn(`(() => { if (!${!!keep}) try { ['cd-tool-strip', 'cd-tool-open'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
  const poReady = () => until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn") && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
  const fileOn = '(() => { const f = PD.els.workspace.querySelector("iframe.wsp-frame.on"); try { return !!f && f.contentDocument.readyState === "complete" && f.contentWindow.location.pathname === "/fileview"; } catch (e) { return false; } })()';
  const openTool = async (id) => { if (await evalIn('PD.dock.flyOpen()') !== id) { await click(tool(id)); } await until(`PD.dock.flyOpen() === ${JSON.stringify(id)}`, 5000); await sleep(350); };
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready(); await toPo(); await poReady(); await sleep(400);
    // A file open in Files.
    await openTool('workspace');
    await until('!!PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path$=\\"README.md\\"]")');
    await evalIn('PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path$=\\"README.md\\"]").click(); 0');
    await until(fileOn, 20000); await sleep(300);
    out.files = await evalIn(PAINT);
    // Each other tool in turn, then none.
    for (const id of ['points', 'changes', 'board', 'spec']) {
      await openTool(id);
      out[id] = await evalIn(PAINT);
      if (id === 'points') await shot('132-asks-over-files');
    }
    await click(tool('spec')); await until('PD.dock.flyOpen() === null', 5000); await sleep(300);
    out.none = await evalIn(PAINT);
    // A reload with Your asks open: the file comes back in Files, still unseen.
    await openTool('points');
    await evalIn('ensUpd.reload(); 0'); await sleep(1500);
    await ready(); await toPo(true); await poReady();
    await until('PD.dock.flyOpen() === "points"', 20000);
    await openTool('workspace');
    await until('!!PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path$=\\"README.md\\"]")');
    if (!(await evalIn(fileOn))) await evalIn('PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path$=\\"README.md\\"]").click(); 0');
    await until(fileOn, 20000);
    await openTool('points');
    out.reload = await evalIn(PAINT);
    // Pop Files out and dock it back, then Your asks.
    out.popped = await evalIn('PD.dock.popOut("workspace")');
    await sleep(1500);
    await evalIn('PD.dock.dockBack("workspace"); 0'); await sleep(800);
    out.backAuto = await evalIn('PD.dock.isAuto("workspace")');
    if (out.backAuto) await openTool('workspace');
    await openTool('points');
    out.dockBack = await evalIn(PAINT);
    // A narrower window, where a tool lies over the middle.
    await size(1024, 768); await sleep(600);
    out.narrow = await evalIn(PAINT);
    await shot('132-asks-1024');
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class TheGuard(unittest.TestCase):
    """The rule that hides a closed flyout's viewer (static check)."""

    def test_a_closed_flyout_hides_its_viewer(self):
        self.assertTrue(".dk-flyout:not(.open) .wsp-frame.on" in INDEX)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class OnlyTheOpenToolIsPainted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-hidview-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
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
        (home / "README.md").write_text("# Motors\n\n" + "A line of the file.\n\n" * 60, encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": proj["id"], "shots": shots}
        script = base / "hidview_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_the_file_shows_in_files(self):
        g = self.got["files"]
        self.assertEqual(g["fly"], "workspace")
        self.assertEqual([f["inOpenFiles"] for f in g["frames"]], [True], g)
        self.assertEqual(g["strays"], [])

    def test_only_the_open_tool_is_painted(self):
        for step, fly in (("points", "points"), ("changes", "changes"), ("board", "board"), ("spec", "spec"), ("none", None),
                          ("reload", "points"), ("dockBack", "points"), ("narrow", "points")):
            g = self.got[step]
            with self.subTest(step=step):
                self.assertEqual(g["fly"], fly)
                self.assertEqual(g["frames"], [], "no Files viewer painted outside an open Files")
                self.assertEqual(g["strays"], [], "every point of the open tool hits that tool")

    def test_files_popped_out(self):
        self.assertTrue(self.got["popped"])


if __name__ == "__main__":
    unittest.main()
