"""#158: Dock v0.12.0 vendored; ⋯ › Take Screenshot copies a PNG of the panel
without Chrome asking to share the tab.

Dock v0.12.0 (Dock #28, the CEO's P23) draws the panel from its own page
content (``src/draw.js``) and copies the PNG. Its ``screenshotMode`` stays at
the default ``'auto'``: it asks to share the tab only for what drawing cannot
read (a cross-origin iframe, a tainted image, a read over 3 s). Every panel
Ensemble docks is a same-origin page (the conversation's ``/session`` frame,
the file viewer's ``/fileview`` frame, the tools and the task list are the
page's own elements), and Ensemble gives Dock no ``screenshot`` hook.

In headless Chrome over CDP (a seeded profile), against a hub in a thread, on
a project with a PO, two tasks and a file:

* a real click on ⋯ › Take Screenshot of the PO chat, of a task's chat panel
  and of a file panel puts a non-empty PNG on the clipboard, the size of the
  panel's stack, and never calls ``getDisplayMedia`` (the share prompt);
* every other panel of both docks (the tools, the task list, the page) and
  the PO chat in a window of its own draw too (``dock.screenshot``), with no
  prompt either;
* the page threw no exception meanwhile.
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
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
DOCK = ROOT / "static" / "dock"


class TheLibrary(unittest.TestCase):
    def test_current_dock_is_vendored_with_draw_js(self):
        self.assertRegex((DOCK / "VERSION").read_text(encoding="utf-8"),
                         r"^fab-ioc/dock v0\.13\.0 23363ebfa934c5c1d83346a018e9cdd9d6e65604 \(tag v0\.13\.0, 2026-10-05\)")
        dock_js = (DOCK / "src" / "dock.js").read_text(encoding="utf-8")
        self.assertIn("import { canDraw, drawPanel } from './draw.js';", dock_js)
        self.assertIn("screenshotMode = 'auto'", dock_js)
        self.assertIn("export async function drawPanel(", (DOCK / "src" / "draw.js").read_text(encoding="utf-8"))

    def test_draw_js_is_a_page_file_and_preloaded(self):
        self.assertIn("static/dock/src/draw.js", dashboard.PAGE_FILES)
        self.assertIn('<link rel="modulepreload" href="/static/dock/src/draw.js">', INDEX)

    def test_ensemble_keeps_auto_and_gives_no_screenshot_hook(self):
        # Neither dock passes screenshot, screenshotMode or screenshotItem: the library's 'auto' draws every panel.
        for opt in ("screenshot:", "screenshotMode", "screenshotItem"):
            self.assertNotIn(opt, INDEX)


# The probe, in a window: getDisplayMedia (the share prompt) counted and refused; what reaches the clipboard decoded
# (its size, and how many colours a sample of its pixels has: a blank picture has one).
PROBE = r"""(w => { if (w.__shot) return 0; const P = w.__shot = { prompts: 0, clips: [] };
  const md = w.navigator.mediaDevices;
  if (md && md.getDisplayMedia) md.getDisplayMedia = async () => { P.prompts++; throw new w.DOMException('probe', 'NotAllowedError'); };
  w.__shotInfo = async b => { const bmp = await w.createImageBitmap(b); const cv = w.document.createElement('canvas'); cv.width = bmp.width; cv.height = bmp.height;
    const g = cv.getContext('2d'); g.drawImage(bmp, 0, 0); w.__shotLast = g; const d = g.getImageData(0, 0, cv.width, cv.height).data; const cols = new Set();
    for (let i = 0; i < d.length; i += 4 * 13) cols.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
    return { type: b.type, bytes: b.size, w: bmp.width, h: bmp.height, colors: cols.size }; };
  const cb = w.navigator.clipboard;
  cb.write = async items => { try { P.clips.push(await w.__shotInfo(await items[0].getType('image/png'))); } catch (e) { P.clips.push({ error: String(e) }); } };
  return 0; })"""

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const PROBE = A.probe;
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.exceptions = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); }
      if (m.method === 'Runtime.exceptionThrown') { const d = m.params.exceptionDetails; this.exceptions.push((d.text || '') + ' ' + (d.exception && d.exception.description || '') + ' @' + (d.url || '') + ':' + d.lineNumber); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { real: {}, api: {} };
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Runtime.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
  await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
  const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  // A real click, as a person's, on the element an expression gives.
  const clickEl = async (expr) => {
    const [x, y] = await evalIn(`(() => { const e = ${expr}; if (!e) throw new Error('no element: ' + ${JSON.stringify(expr)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
  };
  const box = expr => evalIn(`(() => { const r = (${expr}).getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; })()`);
  // ⋯ › Take Screenshot by two real clicks on the panel's stack; what reached the clipboard.
  const shoot = async (id) => {
    const stack = `(PD.rt.has(${JSON.stringify(id)}) ? PD.rt.get(${JSON.stringify(id)}).el : PD.els[${JSON.stringify(id)}]).closest('.dk-stack')`;
    // A tool slid out goes back first (a click on its strip button), as a person would put it away.
    const fly = await evalIn('PD.dock.flyOpen()');
    if (fly && fly !== id) { await clickEl(`document.querySelector('#po-dock .dk-strip-btn[data-dk-auto="${fly}"]')`); await until('!PD.dock.flyOpen()', 5000); }
    await evalIn(`PD.dock.activate(${JSON.stringify(id)}); 0`); await sleep(300);
    const n = await evalIn('window.__shot.clips.length');
    const menuBtn = `${stack}.querySelector(':scope > .dk-head [data-dk-act="menu"]')`;
    await clickEl(menuBtn); await sleep(300);
    if (!(await evalIn(`!!document.querySelector('.dk-menu.dk-options [data-dk-menu="screenshot"]')`))) { await clickEl(menuBtn); await sleep(300); }
    const item = await evalIn(`(() => { const b = document.querySelector('.dk-menu.dk-options [data-dk-menu="screenshot"]'); return b ? { text: b.textContent, tip: b.title } : null; })()`);
    if (!item) throw new Error('no Take Screenshot in ' + id + "'s menu: " + await evalIn(`(() => { const m = document.querySelector('.dk-menu'); return m ? m.outerHTML.slice(0, 1500) : 'no menu; ' + (${menuBtn} || {}).outerHTML; })()`));
    await clickEl(`document.querySelector('.dk-menu.dk-options [data-dk-menu="screenshot"]')`);
    await until(`window.__shot.clips.length > ${n}`, 15000);
    // The colours in the panel's frame area alone (its iframe), so a frame drawn blank under a drawn title bar fails.
    const frame = await evalIn(`(() => { const s = ${stack}.getBoundingClientRect(), f = [...${stack}.querySelectorAll('iframe')].find(x => x.offsetWidth > 0);
      if (!f) return null; const r = f.getBoundingClientRect(); const x = Math.round(r.left - s.left), y = Math.round(r.top - s.top), w = Math.round(r.width), h = Math.round(r.height);
      const d = window.__shotLast.getImageData(x, y, w, h).data; const cols = new Set(); for (let i = 0; i < d.length; i += 4 * 7) cols.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
      return { w, h, colors: cols.size }; })()`);
    return { item, frame, stack: await box(stack), clip: await evalIn('window.__shot.clips.at(-1)'), prompts: await evalIn('window.__shot.prompts') };
  };
  // dock.screenshot(id): the Blob the item copies, drawn in the panel's own window.
  const api = (dock, id, win = 'window') => evalIn(`(async () => { const d = ${dock}, w = ${win}; ${PROBE}(w);
    const before = w.__shot.prompts; const b = await d.screenshot(${JSON.stringify(id)});
    const url = ${!!A.dump} && b ? await new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(b); }) : null;
    return { info: b ? await w.__shotInfo(b) : null, prompts: w.__shot.prompts - before, url }; })()`);
  try {
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.tasks[0]) + ')', 30000);
    await evalIn(`(() => { try { ['cd-list-dock', 'cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
      SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
    await until('!!PD.dock && document.body.classList.contains("po-dock") && !!SW_EL.querySelector(".sw-row") && PD.restored && !!PD.els.workspace.querySelector(".wsp-tree .wse.file[data-path]")', 30000);
    await evalIn(`${PROBE}(window)`);
    out.canCapture = await evalIn('!!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia && window.CropTarget)');
    // The PO chat, its frame loaded.
    await until(`(() => { const f = pdChatFrame(); try { return !!f && f.contentDocument.readyState === 'complete'; } catch (e) { return false; } })()`, 20000);
    await sleep(1500);
    out.real.po = await shoot('po-chat');
    // A task's chat panel.
    await evalIn(`pdChatOpen(${JSON.stringify(A.tasks[1])}); 0`);
    await until(`(() => { const e = PD.rt.get('chat:' + ${JSON.stringify(A.tasks[1])}); const f = e && e.el.querySelector('iframe.dp-session'); try { return !!f && f.contentDocument.readyState === 'complete'; } catch (x) { return false; } })()`, 20000);
    await sleep(1500);
    out.real.chat = await shoot('chat:' + A.tasks[1]);
    // A file panel, from the Files tree.
    await evalIn(`pdReveal('workspace'); 0`); await sleep(400);
    await evalIn(`[...PD.els.workspace.querySelectorAll('.wsp-tree .wse.file[data-path]')].find(x => x.dataset.path.endsWith('notes.md')).click(); 0`);
    const fid = `[...PD.rt.keys()].find(k => k.startsWith('file:') && k.endsWith('notes.md'))`;
    await until(`(() => { const id = ${fid}; const e = id && PD.rt.get(id); const f = e && e.el.querySelector('iframe.wsp-frame.on'); try { return !!f && f.contentDocument.readyState === 'complete' && f.contentWindow.location.pathname === '/fileview'; } catch (x) { return false; } })()`, 20000);
    await evalIn('document.body.click(); 0'); await sleep(1200);
    out.real.file = await shoot(await evalIn(fid));
    // Every other panel through the API: the tools (each slid out), the task list and the page.
    for (const id of ['points', 'board', 'workspace', 'changes', 'spec']) {
      await evalIn(`pdReveal(${JSON.stringify(id)}); 0`); await sleep(700);
      try { out.api[id] = await api('PD.dock', id); } catch (e) { out.api[id] = { error: String(e) }; }
    }
    await evalIn('document.body.click(); 0'); await sleep(300);
    out.ld = await evalIn('!!(typeof LD !== "undefined" && LD.dock)');
    for (const id of ['list', 'page']) {
      if (!out.ld) break;
      await evalIn(`LD.dock.reveal(${JSON.stringify(id)}); 0`); await sleep(500);
      try { out.api['ld:' + id] = await api('LD.dock', id); } catch (e) { out.api['ld:' + id] = { error: String(e) }; }
    }
    await evalIn('document.body.click(); 0'); await sleep(300);
    // The PO chat in a window of its own (its own document).
    await evalIn('PD.dock.setViewMode("po-chat", "window"); 0');
    await until('!!PD.dock.popWindow("po-chat") && PD.els["po-chat"].ownerDocument !== document && !!PD.els["po-chat"].querySelector("iframe.pd-own")', 15000);
    await sleep(1500);
    try { out.api.window = await api('PD.dock', 'po-chat', 'PD.dock.popWindow("po-chat")'); } catch (e) { out.api.window = { error: String(e) }; }
    if (A.dump) { const fs = require('fs'); for (const [k, v] of Object.entries(out.api)) if (v && v.url) { fs.writeFileSync(A.dump + '/' + k.replace(':', '_') + '.png', Buffer.from(v.url.split(',')[1], 'base64')); delete v.url; } }
    out.ldHead = await evalIn(`(() => { const s = LD_PAGE.closest('.dk-stack'); const h = s && s.querySelector(':scope > .dk-head'); return h ? { shown: getComputedStyle(h).display, h: h.getBoundingClientRect().height, html: h.outerHTML.slice(0, 300) } : null; })()`);
    out.prompts = await evalIn('window.__shot.prompts');
    out.errors = c.exceptions;
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TakeScreenshotInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-shot-", ignore_cleanup_errors=True)
        cls.addClassCleanup(cls.tmp.cleanup)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()

        def resumed(self, room_full, text="", to="", key="", quiet=False):
            if text:
                chatroom.post_message(room_full["id"], "user", text, to=to or "")
            return {"resumed": False, "queued": 0, "delivered": 1 if text else 0}

        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", cls.root),
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
            mock.patch.object(dashboard.Handler, "_resume_room", resumed),
            mock.patch.object(dashboard, "push_label_to_iterm", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        (home / "notes.md").write_text("# Notes\n\nThe brakes squeal *only* when cold.\n\n- pads\n- discs\n", encoding="utf-8")
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        chatroom.post_message(po["id"], "user", "Hello PO", to="claude")
        cls.tasks = []
        team = [{"identity": "claude", "agent": "claude", "cwd": str(home)}, {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        for title in ("Brakes that squeal", "Wipers that smear"):
            t = chatroom.create_room(title, team)
            chatroom.post_message(t["id"], "user", "About: " + title)
            dashboard.assign_session_project(t["id"], cls.proj)
            cls.tasks.append(t["id"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "tasks": cls.tasks, "probe": PROBE, "dump": os.environ.get("ENSEMBLE_SHOT_DUMP", "")}
        script = base / "shot_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=600)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_SHOT_REPORT"):
            print(json.dumps(cls.got, indent=1), file=sys.stderr)

    def a_real_picture(self, info, stack=None, where=""):
        self.assertIsNotNone(info, where)
        self.assertNotIn("error", info, where)
        self.assertEqual(info["type"], "image/png", where)
        self.assertGreater(info["bytes"], 1000, where)
        self.assertGreater(info["colors"], 3, f"{where}: a blank picture ({info})")
        if stack:   # the panel's stack, at the page's DPR of 1
            self.assertLessEqual(abs(info["w"] - stack["w"]), 2, f"{where}: {info} vs {stack}")
            self.assertLessEqual(abs(info["h"] - stack["h"]), 2, f"{where}: {info} vs {stack}")

    def test_po_chat_task_chat_and_file_panel_copy_a_png_without_the_prompt(self):
        for name in ("po", "chat", "file"):
            r = self.got["real"][name]
            self.assertEqual(r["item"]["text"], "Take Screenshot", name)
            self.assertEqual(r["item"]["tip"], "Copy this panel as a PNG", name)
            self.a_real_picture(r["clip"], r["stack"], name)
            # The same-origin frame itself was drawn (text in it), not only the title bar around it.
            self.assertIsNotNone(r["frame"], name)
            self.assertGreater(r["frame"]["colors"], 10, f"{name}: its frame drawn blank ({r['frame']})")
        # Region Capture is there, so 'auto' could have fallen back to the share prompt: none is a real result.
        self.assertTrue(self.got["canCapture"])
        self.assertEqual(self.got["prompts"], 0, "getDisplayMedia was called: Chrome would ask to share the tab")

    def test_every_other_panel_and_a_window_draw_without_the_prompt(self):
        api = self.got["api"]
        self.assertTrue(self.got["ld"], "the task list's dock")
        for name in ("points", "board", "workspace", "changes", "spec", "ld:list", "window"):
            self.assertIn(name, api)
            self.assertNotIn("error", api[name], name)
            self.a_real_picture(api[name]["info"], where=name)
            self.assertEqual(api[name]["prompts"], 0, name)
        # The list dock's page (the whole PO screen, the dock's fill) has no title bar, so no ⋯ and no Take
        # Screenshot a person can reach. Through the API it draws without a prompt (a blank picture today: the
        # nested dock; a Dock question, not Ensemble's).
        self.assertEqual(self.got["ldHead"]["shown"], "none", self.got["ldHead"])
        self.assertEqual(api["ld:page"]["prompts"], 0)

    def test_the_page_threw_no_exception_meanwhile(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
