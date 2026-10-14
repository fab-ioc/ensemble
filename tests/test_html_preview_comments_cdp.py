"""Comments on an HTML page's preview in the file view (#233), in a real
browser: a hub in a thread with throwaway state, headless Chrome over CDP with
real mouse, key and touch events, at 1728x1117 and at a phone's 390x844.

* a selection inside the page's frame brings up the same Comment · Copy bar,
  over the selection on screen; Comment → the composer quotes it;
* Point, then a click on a button: the comment names that button (locator,
  CSS path that finds it again, its HTML, its line in the source); Esc leaves;
  the page's own button does nothing (its link is not followed);
* both are kept: after a reload they are marked on the page (a highlight and
  numbered pins) and listed; after the file changed, the one whose words are
  gone is listed as not found, never dropped;
* Send delivers them in one message to the task (POST /api/room/resume),
  which says what the agent needs to find each spot;
* the page's own scripts never run: a script and an inline handler that try
  to post a comment to the hub post nothing;
* on a phone: a selection made in the frame shows the bar inside the screen,
  and Point works with a tap.

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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402

# A mockup as agents write them: boxes and buttons with little text, its own
# styles, and scripts of its own that must never run.
MOCKUP = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Mockup</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 sans-serif; background: #f4f5f7; }
  .bar { display: flex; gap: 8px; padding: 12px 16px; background: #fff; border-bottom: 1px solid #ddd; }
  .btn { padding: 6px 14px; border: 1px solid #bbb; border-radius: 6px; background: #fff; }
  .btn.primary { background: #0c66e4; color: #fff; border-color: #0c66e4; }
  .panel { margin: 24px; padding: 16px; background: #fff; border-radius: 8px; }
  div { outline: 0; }
</style>
<script>
  fetch('/api/room/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ roomId: 'ROOMID', text: 'FAKE comment from the page', to: '', key: 'fake' }) });
  try { parent.__mockupRan = true; } catch (e) {}
</script>
</head>
<body>
<div class="bar">
  <button class="btn">Cancel</button>
  <a class="btn" href="other.html">Other page</a>
  <button class="btn primary" id="save">Save changes</button>
</div>
<div class="panel">
  <h2>Gearbox settings</h2>
  <p>The motor drives the gearbox at a fixed ratio of seven to one.</p>
  <p>Torque limits are applied before the brake engages.</p>
  <img src="nowhere.png" onerror="parent.__mockupRan = true" alt="diagram">
</div>
<div class="panel">
  <p>A second panel with a short note.</p>
</div>
</body>
</html>
"""

# The real mockup the comments were asked for on (#115's screen layouts): used
# when this machine has the project's Documents, copied into the test state.
COMPARE = Path.home() / "EnsembleProjects" / "Ensemble Dashboard" / "Documents" / "Screen layout" / "compare.html"

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
const READY = 'typeof FC !== "undefined" && FC.on() && !!document.querySelector("iframe.file")';
async function page(c, W, H, phone) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: !!phone }, sessionId);
  if (phone) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  const mouse = (type, x, y, clickCount = 1) => c.send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount }, sessionId);
  const dmouse = (type, x, y) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1 }, sessionId);
  const p = {
    ev, until, close: () => c.send('Target.closeTarget', { targetId }),
    go: async url => { await c.send('Page.navigate', { url }, sessionId); await until(READY); await sleep(500); },
    reload: async () => { await c.send('Page.reload', {}, sessionId); await sleep(300); await until(READY); await sleep(500); },
    // The point (in the file view's window) at the `off`-th letter of `needle` in the frame's page.
    at: (needle, off = 0, side = 'l') => ev(`(() => {
      const f = document.querySelector('iframe.file'), d = f.contentDocument, fr = f.getBoundingClientRect();
      const w = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) { const i = n.nodeValue.indexOf(${JSON.stringify(needle)}); if (i < 0) continue;
        const r = d.createRange(); r.setStart(n, i + ${off}); r.setEnd(n, i + ${off} + 1);
        const b = r.getBoundingClientRect(); return [fr.left + f.clientLeft + ${side === 'l' ? 'b.left + 1' : 'b.right - 1'}, fr.top + f.clientTop + b.top + b.height / 2]; }
      return null; })()`),
    // The centre of an element of the frame's page, in the file view's window.
    inFrame: sel => ev(`(() => { const f = document.querySelector('iframe.file'), fr = f.getBoundingClientRect();
      const r = f.contentDocument.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
      return [fr.left + f.clientLeft + r.left + r.width / 2, fr.top + f.clientTop + r.top + r.height / 2]; })()`),
    centre: sel => ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`),
    drag: async (a, b) => {
      await dmouse('mousePressed', a[0], a[1]);
      for (let s = 1; s <= 8; s++) await dmouse('mouseMoved', a[0] + (b[0] - a[0]) * s / 8, a[1] + (b[1] - a[1]) * s / 8);
      await dmouse('mouseReleased', b[0], b[1]); await sleep(500);
    },
    move: async pt => { await mouse('mouseMoved', pt[0], pt[1]); await sleep(150); },
    click: async pt => { await dmouse('mousePressed', pt[0], pt[1]); await dmouse('mouseReleased', pt[0], pt[1]); await sleep(350); },
    tap: async pt => {
      const tp = [{ x: pt[0], y: pt[1], radiusX: 2, radiusY: 2, force: 1, id: 1 }];
      await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: tp }, sessionId);
      await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
      await sleep(500);
    },
    key: async (key, code, vk) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key, code, windowsVirtualKeyCode: vk }, sessionId); await sleep(200); },
    type: async text => { await c.send('Input.insertText', { text }, sessionId); await sleep(100); },
    // The bar and the selection in the frame, both as the file view's window sees them.
    bar: () => ev(`(() => { const b = document.querySelector('.sel-bar'), f = document.querySelector('iframe.file');
      const fr = f.getBoundingClientRect(), s = f.contentDocument.getSelection();
      const box = r => ({ l: r.left, t: r.top, r: r.right, b: r.bottom });
      let sr = null; if (s.rangeCount && !s.isCollapsed) { const r = s.getRangeAt(0).getBoundingClientRect(); sr = { l: r.left + fr.left + f.clientLeft, t: r.top + fr.top + f.clientTop, r: r.right + fr.left + f.clientLeft, b: r.bottom + fr.top + f.clientTop }; }
      return { shown: !!b && !b.hidden, inHost: !!b && b.ownerDocument === document, comment: !!b && !b.querySelector('.sel-cmt').hidden,
               copy: !!b && !b.querySelector('.sel-copy').hidden, bar: b && !b.hidden ? box(b.getBoundingClientRect()) : null,
               sel: s.toString(), selRect: sr, vw: innerWidth, vh: innerHeight }; })()`),
    layer: () => ev(`(() => { const f = document.querySelector('iframe.file'), d = f.contentDocument, h = d.querySelector('ens-comments');
      const sr = h && h.shadowRoot; const hov = sr && sr.querySelector('.hov');
      return { pins: sr ? [...sr.querySelectorAll('.pin')].map(p => ({ n: p.textContent, cid: p.dataset.cid })) : [],
               marks: sr ? sr.querySelectorAll('.mk').length : 0, hov: hov && hov.style.display === 'block' ? hov.querySelector('.tag').textContent : '',
               highlight: (() => { try { const H = d.defaultView.CSS.highlights.get('ens-cmt'); return H ? [...H].map(r => r.toString()) : null; } catch (e) { return 'err ' + e; } })(),
               bodyText: d.body.textContent.includes('ens-comments'), picking: FC.picking(),
               pick: document.getElementById('cmt-pick').getAttribute('aria-pressed'), pickHidden: document.getElementById('cmt-pick').hidden,
               tray: [...document.querySelectorAll('#cmt-tray .cmt-item')].map(i => ({ q: i.querySelector('.cmt-item-q').textContent, n: i.querySelector('.cmt-item-n').textContent, lost: !!i.querySelector('.cmt-lost') })),
               ran: !!window.__mockupRan }; })()`),
    // A picture of the page, when the run was asked for them (ENS_SHOTS).
    shot: async name => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
      require('fs').writeFileSync(require('path').join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); },
    composer: () => ev('(() => { const q = document.querySelector(".cmt-composer .cmt-quote"); return q ? q.textContent : null; })()'),
    add: async note => { await ev('document.querySelector(".cmt-composer .cmt-note").focus(); 0'); await p.type(note);
      await p.click(await p.centre('.cmt-composer .cmt-add')); },
  };
  return p;
}

async function desktop(c) {
  const out = {};
  const p = await page(c, 1728, 1117, false);
  await p.go(A.url);
  out.start = await p.layer();

  // Select words in the page: the bar, over them, in the file view.
  await p.drag(await p.at('fixed ratio', 0), await p.at('seven to one', 11, 'r'));
  out.bar = await p.bar();
  await p.shot('desktop-1-selection');
  await p.click(await p.centre('.sel-bar .sel-cmt'));
  out.quote = await p.composer();
  await p.add('Say which ratio is the default.');
  out.afterText = await p.layer();

  // Point at the Save button and click it.
  await p.click(await p.centre('#cmt-pick'));
  out.pickOn = await p.layer();
  await p.move(await p.inFrame('#save'));
  out.hover = await p.layer();
  await p.shot('desktop-2-point');
  await p.click(await p.inFrame('#save'));
  out.elQuote = await p.composer();
  await p.shot('desktop-3-composer');
  out.notFollowed = await p.ev('document.querySelector("iframe.file").contentDocument.querySelector("#save") !== null');
  await p.add('Make this the only primary button.');
  // The link: picked, not followed.
  await p.click(await p.inFrame('a.btn'));
  out.linkQuote = await p.composer();
  out.stillHere = await p.ev('location.search.includes("mockup.html")');
  await p.key('Escape', 'Escape', 27);          // closes the composer
  out.afterEsc1 = await p.layer();
  await p.key('Escape', 'Escape', 27);          // leaves Point
  out.afterEsc2 = await p.layer();

  // Kept: a reload shows both, marked.
  await p.reload();
  await sleep(400);
  out.reloaded = await p.layer();
  await p.shot('desktop-4-reloaded');
  out.stored = await p.ev('Object.keys(localStorage).filter(k => k.startsWith("cd-fvc:")).length');

  // The file changes: the passage is gone, the button is still there.
  await p.ev(`fetch('/__edit', { method: 'POST' }).then(r => r.status)`);
  await p.reload();
  await sleep(400);
  out.changed = await p.layer();
  await p.shot('desktop-5-changed');

  // Send: one message to the task.
  await p.click(await p.centre('#cmt-tray .cmt-submit'));
  await sleep(600);
  out.afterSend = await p.layer();
  out.storedAfter = await p.ev('Object.keys(localStorage).filter(k => k.startsWith("cd-fvc:")).length');
  // The CSS path the message gave finds the same button again.
  out.sent = await p.ev('fetch("/__sent").then(r => r.json())');
  const path = ((out.sent[out.sent.length - 1] || {}).text || '').match(/CSS path: `([^`]+)`/);
  out.pathFinds = path ? await p.ev(`(() => { const e = document.querySelector('iframe.file').contentDocument.querySelector(${JSON.stringify(path[1])}); return e ? e.id : null; })()`) : null;
  out.ranFinal = await p.ev('!!window.__mockupRan');
  await p.close();
  return out;
}

async function phone(c) {
  const out = {};
  const p = await page(c, 390, 844, true);
  await p.go(A.url2);
  out.start = await p.layer();
  // A long press selects words; here the selection is made as the press leaves it.
  await p.ev(`(() => { const d = document.querySelector('iframe.file').contentDocument; const w = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) { const i = n.nodeValue.indexOf('Torque limits'); if (i >= 0) { d.getSelection().setBaseAndExtent(n, i, n, i + 13); break; } } })(); 0`);
  await sleep(700);
  out.bar = await p.bar();
  await p.shot('phone-1-selection');
  await p.tap(await p.centre('.sel-bar .sel-cmt'));
  out.quote = await p.composer();
  await p.ev('(() => { const x = document.querySelector(".cmt-composer .cmt-cancel"); if (x) x.click(); })(); 0');
  await p.ev('document.querySelector("iframe.file").contentDocument.getSelection().removeAllRanges(); 0');
  await sleep(400);
  await p.tap(await p.centre('#cmt-pick'));
  out.pickOn = await p.layer();
  await p.tap(await p.inFrame('#save'));
  out.elQuote = await p.composer();
  await p.shot('phone-2-point');
  out.composerBox = await p.ev('(() => { const r = document.querySelector(".cmt-composer").getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, vw: innerWidth }; })()');
  out.pickBtn = await p.ev('(() => { const r = document.getElementById("cmt-pick").getBoundingClientRect(); return { w: r.width, h: r.height }; })()');
  out.scrollW = await p.ev('document.documentElement.scrollWidth');
  await p.close();
  return out;
}

// A real mockup (the screen layouts compared), when this machine has it.
async function real(c) {
  const out = {};
  const p = await page(c, 1728, 1117, false);
  await p.go(A.url3);
  await p.ev(`document.querySelector('iframe.file').contentDocument.querySelector('table').scrollIntoView({ block: 'center' }); 0`);
  await sleep(300);
  await p.drag(await p.at('Needs you, across projects', 0), await p.at('Needs you, across projects', 25, 'r'));
  out.bar = await p.bar();
  await p.click(await p.centre('.sel-bar .sel-cmt'));
  out.quote = await p.composer();
  await p.add('Is this the first screen on a phone too?');
  await p.ev(`[...document.querySelector('iframe.file').contentDocument.querySelectorAll('figure')].find(f => /A: the list is home/.test(f.textContent)).querySelector('img').scrollIntoView({ block: 'center' }); 0`);
  await sleep(300);
  await p.click(await p.centre('#cmt-pick'));
  const img = await p.ev(`(() => { const f = document.querySelector('iframe.file'), fr = f.getBoundingClientRect(); const r = [...f.contentDocument.querySelectorAll('figure')].find(x => /A: the list is home/.test(x.textContent)).querySelector('img').getBoundingClientRect();
    return [fr.left + f.clientLeft + r.left + r.width / 2, fr.top + f.clientTop + r.top + r.height / 2]; })()`);
  await p.move(img);
  await p.shot('compare-1-point');
  await p.click(img);
  out.elQuote = await p.composer();
  await p.add('This phone layout reads best.');
  await p.key('Escape', 'Escape', 27);
  await p.reload();
  await sleep(500);
  out.offScreen = await p.layer();             // both further down: no pins drawn yet
  await p.ev(`document.querySelector('iframe.file').contentDocument.querySelector('table').scrollIntoView({ block: 'center' }); 0`);
  await sleep(300);
  out.reloaded = await p.layer();
  await p.shot('compare-2-reloaded');
  out.body = await p.ev('buildBody()');
  await p.close();
  return out;
}

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    out.desktop = await desktop(c);
    out.phone = await phone(c);
    if (A.url3) out.real = await real(c);
  } finally {
    try { c.ws.close(); } catch (e) {}
    const gone = new Promise(r => ch.on('exit', r));
    ch.kill();
    await Promise.race([gone, sleep(5000)]);
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-hpc-", ignore_cleanup_errors=True)
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
            cls.addClassCleanup(p.stop)
        work = root / "Motors"
        work.mkdir()
        room = chatroom.create_room("Mockups", [{"identity": "claude", "agent": "claude", "cwd": str(work)}])
        rid = room["id"]
        chatroom.patch_room(rid, cwd=str(work))
        mockup = work / "mockup.html"
        mockup.write_text(MOCKUP.replace("ROOMID", rid), encoding="utf-8")
        phone = work / "phone.html"
        phone.write_text(MOCKUP.replace("ROOMID", rid), encoding="utf-8")
        cls.sent = sent = []

        # What reaches the hub: every POST to /api/room/resume is recorded (the
        # page's Send and anything the mockup might post), and answered as held.
        class Handler(dashboard.Handler):
            def do_POST(self):
                if self.path.startswith("/api/room/resume"):
                    n = int(self.headers.get("Content-Length") or 0)
                    sent.append(json.loads(self.rfile.read(n) or b"{}"))
                    self._send_json(200, {"ok": True})
                    return
                if self.path == "/__edit":
                    mockup.write_text(MOCKUP.replace("ROOMID", rid).replace(
                        "The motor drives the gearbox at a fixed ratio of seven to one.", "The motor drives the gearbox directly."), encoding="utf-8")
                    self._send_json(200, {"ok": True})
                    return
                super().do_POST()

            def do_GET(self):
                if self.path == "/__sent":
                    self._send_json(200, sent)
                    return
                super().do_GET()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        q = lambda f: f"{b}/fileview?path={quote(str(f))}&room={rid}&roomName=Mockups"  # noqa: E731
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "url": q(mockup), "url2": q(phone), "shots": os.environ.get("ENS_SHOTS", "")}
        if COMPARE.is_file():
            shutil.copy(COMPARE, work / "compare.html")
            args["url3"] = q(work / "compare.html")
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        cls.err = out.stderr
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_a_selection_in_the_page_brings_the_bar_over_it(self):
        g = self.got["desktop"]["bar"]
        self.assertTrue(g["shown"] and g["inHost"] and g["comment"] and g["copy"], g)
        self.assertEqual(g["sel"], "fixed ratio of seven to one")
        self.assertLess(abs(g["bar"]["t"] - g["selRect"]["b"] - 8), 3, "under the words, as for Markdown")
        self.assertLess(abs(g["bar"]["l"] - g["selRect"]["l"]), 3)
        self.assertEqual(self.got["desktop"]["quote"], "“fixed ratio of seven to one”")
        a = self.got["desktop"]["afterText"]
        self.assertEqual(a["highlight"], ["fixed ratio of seven to one"])
        self.assertEqual([p["n"] for p in a["pins"]], ["1"])
        self.assertFalse(a["bodyText"], "the layer is outside the body: offsets unchanged")

    def test_point_names_the_element_clicked(self):
        g = self.got["desktop"]
        self.assertTrue(g["start"]["pickHidden"] is False)
        self.assertEqual(g["pickOn"]["pick"], "true")
        self.assertEqual(g["hover"]["hov"], "button#save.btn.primary")
        self.assertEqual(g["elQuote"], "On <button#save.btn.primary> “Save changes”")
        self.assertEqual(g["linkQuote"], "On <a.btn> “Other page”")
        self.assertTrue(g["stillHere"], "the page's link was picked, not followed")
        self.assertTrue(g["afterEsc1"]["picking"], "the first Esc closes the composer")
        self.assertFalse(g["afterEsc2"]["picking"], "the second leaves Point")
        self.assertEqual(g["afterEsc2"]["pick"], "false")

    def test_comments_are_kept_and_marked_after_a_reload(self):
        g = self.got["desktop"]
        self.assertEqual(g["stored"], 2)
        r = g["reloaded"]
        self.assertEqual([p["n"] for p in r["pins"]], ["1", "2"])
        self.assertEqual(r["marks"], 1, "the element is outlined")
        self.assertEqual(r["highlight"], ["fixed ratio of seven to one"])
        self.assertEqual([t["lost"] for t in r["tray"]], [False, False])

    def test_a_spot_gone_after_a_change_is_listed_not_lost(self):
        r = self.got["desktop"]["changed"]
        self.assertEqual(len(r["tray"]), 2)
        self.assertEqual([t["lost"] for t in r["tray"]], [True, False])
        self.assertEqual([p["n"] for p in r["pins"]], ["2"], "the button keeps its pin and number")

    def test_send_tells_the_agent_where_each_comment_is(self):
        g = self.got["desktop"]
        mine = [s for s in g["sent"] if s.get("key") != "fake"]
        self.assertEqual(len(mine), 1, g["sent"])
        t = mine[0]["text"]
        self.assertIn("## Review comments on `mockup.html` (2)", t)
        self.assertIn("File: `", t)
        self.assertIn("**1.** > fixed ratio of seven to one", t)
        self.assertIn("(around line 29 of the source)", t)
        self.assertIn("The file changed since: this spot is not in it now.", t)
        self.assertIn("Say which ratio is the default.", t)
        self.assertIn("**2.** On the element `button#save.btn.primary` “Save changes” at line 25 of the page", t)
        self.assertIn('<button class="btn primary" id="save">Save changes</button>', t)
        self.assertIn("Make this the only primary button.", t)
        self.assertEqual(g["pathFinds"], "save")
        self.assertEqual(g["afterSend"]["tray"], [])
        self.assertEqual(g["afterSend"]["pins"], [])
        self.assertEqual(g["storedAfter"], 0)

    def test_the_pages_own_scripts_post_nothing(self):
        g = self.got["desktop"]
        self.assertFalse(any("FAKE" in (s.get("text") or "") for s in g["sent"]), g["sent"])
        self.assertFalse(g["ranFinal"])
        self.assertFalse(g["start"]["ran"])

    def test_on_a_phone(self):
        g = self.got["phone"]
        b = g["bar"]
        self.assertTrue(b["shown"] and b["comment"], b)
        self.assertGreaterEqual(b["bar"]["l"], 7.5)
        self.assertLessEqual(b["bar"]["r"], b["vw"] - 7.5)
        self.assertEqual(g["quote"], "“Torque limits”")
        self.assertEqual(g["pickOn"]["pick"], "true")
        self.assertEqual(g["elQuote"], "On <button#save.btn.primary> “Save changes”")
        self.assertGreaterEqual(g["pickBtn"]["w"], 44)
        self.assertGreaterEqual(g["pickBtn"]["h"], 44)
        self.assertLessEqual(g["composerBox"]["r"], g["composerBox"]["vw"])
        self.assertLessEqual(g["scrollW"], 390)


    def test_a_real_mockup(self):
        g = self.got.get("real")
        if not g:
            self.skipTest("the screen layouts mockup is not on this machine")
        self.assertTrue(g["bar"]["shown"] and g["bar"]["comment"], g["bar"])
        self.assertEqual(g["quote"], "“Needs you, across projects”")
        self.assertEqual(g["elQuote"], "On <img> in “A: the list is home”", "a picture with no words is named by its caption")
        self.assertEqual(g["offScreen"]["pins"], [], "pins only where their place is on screen")
        self.assertEqual([p["n"] for p in g["reloaded"]["pins"]], ["1", "2"])
        self.assertEqual(g["reloaded"]["highlight"], ["Needs you, across projects"])
        b = g["body"]
        self.assertIn("**2.** On the element `img` at line 304 of the page, in “A: the list is home”", b)
        self.assertIn("CSS path: `body > div > div:nth-of-type(2) > figure:nth-of-type(3) > img`", b)
        self.assertIn('src="data:image/webp;base64,', b)
        self.assertLess(len(b), 2500, "a picture's data is cut, not sent whole")


class TheSandbox(unittest.TestCase):
    """The preview's frame stays without scripts: allow-same-origin only."""

    def test_the_frame_runs_no_scripts(self):
        src = (ROOT / "fileview.html").read_text(encoding="utf-8")
        self.assertIn("ifr.setAttribute('sandbox', 'allow-same-origin');", src)
        self.assertNotIn("allow-scripts", src)


if __name__ == "__main__":
    unittest.main()
