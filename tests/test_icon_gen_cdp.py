"""#335 Generate in the project settings, in headless Chrome over CDP against
a hub in a thread; the CLI is stubbed (icon_gen.generate answers from a mode
file the browser script writes: ok, slow:<s>, timeout, nocli).

At 1440x900:

* idle: Generate… beside Upload image…, the hint names it, no box;
* busy: four dashed tiles, "Drawing 4 icons… N s" counting up, Cancel;
* candidates: four 64px tiles of 44px icons as a radiogroup, who draws
  (claude · haiku), the name and the roadmap's line, focus on the first;
* picked: the tile is on, the Image radio is on with "drawn by claude haiku,
  not saved yet", the sample row shows it; arrows move the pick;
* More ideas then Cancel brings the last four back; a timeout says so
  neutrally with Close and Try again; Save keeps the picked SVG through
  /api/projects/icon and the list shows it at 12x12;
* no CLI: from the POST, the box says so and Generate greys; clicked again
  once one is there, it draws; from the GET at open, Generate is grey at once;
* a phone (390x844): Upload… and Generate… on a row under the Image choice,
  the tiles spread across the box, targets 44px, no sideways scroll;
* the page threw nothing meanwhile.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
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

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import icon_gen  # noqa: E402
import points  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"'
SVGS = [
    f'<svg {NS}><rect x="0" y="0" width="48" height="48" rx="10" fill="#0F766E"/><path d="M27 7 13 27h10l-3 14 15-21H25z" fill="#FFFFFF"/></svg>',
    f'<svg {NS}><rect x="0" y="0" width="48" height="48" rx="10" fill="#EA580C"/><g stroke="#FFFFFF" stroke-width="4" stroke-linecap="round">'
    '<line x1="24" y1="9" x2="24" y2="39"/><line x1="9" y1="24" x2="39" y2="24"/><line x1="13" y1="13" x2="35" y2="35"/><line x1="35" y1="13" x2="13" y2="35"/></g>'
    '<circle cx="24" cy="24" r="9" fill="#FFFFFF"/><circle cx="24" cy="24" r="4" fill="#EA580C"/></svg>',
    f'<svg {NS}><rect x="0" y="0" width="48" height="48" rx="10" fill="#1E3A8A"/><circle cx="24" cy="24" r="13" fill="none" stroke="#FACC15" stroke-width="4"/>'
    '<path d="M11 22h26M24 24v13" stroke="#FACC15" stroke-width="4"/><circle cx="24" cy="24" r="4" fill="#FACC15"/></svg>',
    f'<svg {NS}><rect x="0" y="0" width="48" height="48" rx="10" fill="#7C3AED"/><rect x="9" y="16" width="27" height="16" rx="3" fill="none" stroke="#FFFFFF" stroke-width="3.5"/>'
    '<rect x="36" y="21" width="4" height="6" rx="1" fill="#FFFFFF"/><rect x="13" y="20" width="7" height="8" fill="#FFFFFF"/><rect x="22" y="20" width="7" height="8" fill="#FFFFFF"/></svg>',
]
META = {"agent": "claude", "model": "haiku"}

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const mode = m => fs.writeFileSync(A.mode, m);
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {}; global.OUT = out;
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(120); } throw new Error('timeout: ' + expr); };
    const clickAt = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('nothing to click: ${sel}'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      await clickAt(x, y);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: { Escape: 27, ArrowRight: 39, ArrowLeft: 37 }[k] || 0 }, sessionId); };
    const shot = async (name) => {
      if (!A.shots) return;
      for (const th of ['light', 'dark']) {
        await evalIn(`(() => { document.documentElement.setAttribute('data-theme', '${th}'); const b = document.querySelector('#modal #ps-gen-box') || document.querySelector('#modal .ps-choice-img'); if (b) b.scrollIntoView({ block: 'nearest' }); return 0; })()`);
        await sleep(150);
        const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
        fs.writeFileSync(path.join(A.shots, `live-${w}x${h}-${name}-${th}.png`), Buffer.from(r.data, 'base64'));
      }
      await evalIn(`(() => { document.documentElement.removeAttribute('data-theme'); return 0; })()`);
    };
    const s = { 'cd-list-dock': null, 'cd-ld-sel': null, 'cd-noun': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, clickAt, key, shot, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const P = A.ids;
  const R = `(e => { if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10, r: Math.round(r.right), b: Math.round(r.bottom) }; })`;
  const GEN = `(() => { const d = document.getElementById('modal'); if (!d || !d.open || !d.classList.contains('ps-dlg')) return null;
    const q = s => d.querySelector(s), t = s => ((q(s) || {}).textContent || '').trim();
    const g = q('#ps-gen'), box = q('#ps-gen-box');
    const ae = document.activeElement;
    return { btn: g && g.textContent.trim(), off: !!g && g.classList.contains('off'), ariaOff: g && g.getAttribute('aria-disabled'), expanded: g && g.getAttribute('aria-expanded'),
      up: t('#ps-up'), box: !!box, head: t('.ps-gen-h b'), who: t('.ps-gen-who'), sub: t('.ps-gen-sub'), foot: t('.ps-gen-foot'), msg: t('.ps-gen-msg'),
      msgSmall: t('.ps-gen-msg small'), status: [...d.querySelectorAll('#ps-gen-box [role=status]')].map(e => e.textContent.trim()),
      waits: d.querySelectorAll('.ps-tile.wait').length, group: (q('.ps-tiles[role=radiogroup]') || {}).getAttribute ? q('.ps-tiles[role=radiogroup]').getAttribute('aria-label') : null,
      tiles: [...d.querySelectorAll('button.ps-tile')].map(e => { const i = e.querySelector('img'); const r = e.getBoundingClientRect(), ir = i.getBoundingClientRect();
        return { on: e.classList.contains('on'), checked: e.getAttribute('aria-checked'), role: e.getAttribute('role'), tab: e.tabIndex, w: Math.round(r.width), h: Math.round(r.height),
          img: Math.round(ir.width), alt: i.alt, loaded: i.complete && i.naturalWidth > 0, svg: i.getAttribute('src').startsWith('data:image/svg+xml;base64,'), x: Math.round(r.left), r: Math.round(r.right) }; }),
      btns: [...d.querySelectorAll('#ps-gen-box button.ps-btn')].map(e => e.textContent.trim()),
      focus: ae && (ae.id || ae.className), choice: (q('input[name="ps-icon"]:checked') || {}).value, img: t('.ps-choice-img small'),
      bigSrc: q('.ps-choice-img .ps-big img') ? q('.ps-choice-img .ps-big img').getAttribute('src').slice(0, 26) : '',
      sampleSrc: q('.ps-srow .ldp-ico img') ? q('.ps-srow .ldp-ico img').getAttribute('src').slice(0, 26) : '',
      hint: [...d.querySelectorAll('.ps-hint')].map(e => e.textContent).slice(-1)[0], save: !!q('#ps-save') && !q('#ps-save').disabled,
      dlg: ${R}(d), boxR: ${R}(box), upR: ${R}(q('#ps-up')), genR: ${R}(g), tR: ${R}(q('.ps-choice-img .ps-t')), tilesR: ${R}(q('.ps-tiles')),
      scrollW: d.scrollWidth, clientW: d.clientWidth, docW: document.documentElement.scrollWidth, W: document.documentElement.clientWidth,
      targets: [...d.querySelectorAll('#ps-gen-box button, #ps-up, #ps-gen')].map(e => [e.id || e.className, Math.round(e.getBoundingClientRect().height)]) };
  })()`;
  const open = async (p, pid) => {
    await p.evalIn(`(() => { projSettings(${JSON.stringify(pid)}); return 0; })()`);
    await p.until(GEN, 5000);
    await sleep(400);   // the GET
  };
  try {
    mode('ok');
    // ---- desktop
    const q = await page(1440, 900, false);
    await open(q, P.Motors);
    out.idle = await q.evalIn(GEN);
    await q.shot('gen-idle');
    mode('slow:3.5');
    await q.click('#modal #ps-gen');
    await sleep(1300);
    out.busy = await q.evalIn(GEN);
    await sleep(1000);
    out.busyLater = await q.evalIn(GEN);
    await q.shot('gen-busy');
    await q.until(`document.querySelectorAll('#modal button.ps-tile').length === 4`, 8000);
    await sleep(200);
    out.cands = await q.evalIn(GEN);
    await q.shot('gen-cands');
    await q.click('#modal #ps-gt-1');
    await sleep(200);
    out.picked = await q.evalIn(GEN);
    await q.shot('gen-picked');
    await q.key('ArrowRight');
    await sleep(150);
    out.arrow = await q.evalIn(GEN);
    await q.key('ArrowLeft');
    await sleep(150);
    out.arrowBack = await q.evalIn(GEN);
    // More ideas, then Cancel: the four come back
    mode('slow:3');
    await q.click('#modal #ps-gen-more');
    await sleep(500);
    out.moreBusy = await q.evalIn(GEN);
    await q.click('#modal #ps-gen-cancel');
    await sleep(200);
    out.cancelled = await q.evalIn(GEN);
    await sleep(3200);   // the stub's thread runs out; nothing changes
    out.afterCancel = await q.evalIn(GEN);
    // a timeout
    mode('timeout');
    await q.click('#modal #ps-gen-more');
    await q.until(`!!document.querySelector('#modal #ps-gen-again')`, 5000);
    await sleep(150);
    out.failed = await q.evalIn(GEN);
    await q.shot('gen-failed');
    await q.click('#modal #ps-gen-close');
    await sleep(150);
    out.closed = await q.evalIn(GEN);
    // Save keeps the picked one
    await q.click('#modal #ps-save');
    await q.until(`!document.getElementById('modal').open && !!(projectById(${JSON.stringify(P.Motors)}) || {}).icon`, 8000);
    await sleep(500);
    out.saved = await q.evalIn(`(() => { const pj = projectById(${JSON.stringify(P.Motors)}); const e = document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${P.Motors}"] .ldp-ico');
      const i = e && e.querySelector('img'); const r = e && e.getBoundingClientRect();
      return { icon: pj.icon, w: r && Math.round(r.width), h: r && Math.round(r.height), img: !!i, loaded: !!i && i.complete && i.naturalWidth > 0 }; })()`);
    // no CLI, said by the POST; then one is there
    await open(q, P.Motors);
    mode('nocli');
    await q.click('#modal #ps-gen');
    await q.until(`!!document.querySelector('#modal #ps-gen-box') && !document.querySelector('#modal #ps-gen-box[aria-busy]')`, 5000);
    await sleep(150);
    out.nocli = await q.evalIn(GEN);
    await q.shot('gen-nocli');
    mode('ok');
    await q.click('#modal #ps-gen');
    await q.until(`document.querySelectorAll('#modal button.ps-tile').length === 4`, 8000);
    out.cliBack = await q.evalIn(GEN);
    await q.key('Escape');
    await sleep(200);
    // no CLI, said by the GET at open
    await q.evalIn(`(() => { window.__f = window.fetch; window.fetch = (u, o) => String(u).startsWith('/api/projects/icon-gen?')
      ? Promise.resolve(new Response(JSON.stringify({ agent: '', model: '', name: 'Motors', about: '' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      : window.__f(u, o); return 0; })()`);
    await open(q, P.Wheels);
    out.getOff = await q.evalIn(GEN);
    await q.click('#modal #ps-gen');
    await sleep(300);
    out.getOffClicked = await q.evalIn(GEN);
    await q.evalIn(`(() => { window.fetch = window.__f; return 0; })()`);
    await q.key('Escape');
    await q.close();
    // ---- a phone
    const ph = await page(390, 844, true);
    await open(ph, P.Wheels);
    out.phIdle = await ph.evalIn(GEN);
    await ph.shot('gen-idle');
    mode('slow:2');
    await ph.click('#modal #ps-gen');
    await sleep(800);
    out.phBusy = await ph.evalIn(GEN);
    await ph.shot('gen-busy');
    await ph.until(`document.querySelectorAll('#modal button.ps-tile').length === 4`, 8000);
    await sleep(200);
    out.phCands = await ph.evalIn(GEN);
    await ph.shot('gen-cands');
    await ph.click('#modal #ps-gt-1');
    await sleep(200);
    out.phPicked = await ph.evalIn(GEN);
    await ph.shot('gen-picked');
    mode('timeout');
    await ph.click('#modal #ps-gen-more');
    await ph.until(`!!document.querySelector('#modal #ps-gen-again')`, 5000);
    await sleep(150);
    out.phFailed = await ph.evalIn(GEN);
    await ph.shot('gen-failed');
    await ph.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); try { console.error(JSON.stringify(global.OUT || {}).slice(0, 3000)); } catch (x) {} process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class GenerateInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-gen-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        mode_file = base / "mode"
        mode_file.write_text("ok")
        cls.calls = []

        def fake_generate(key, name, about, timeout=60):
            m = mode_file.read_text().strip()
            cls.calls.append((key, name, about, m))
            if m.startswith("slow:"):
                time.sleep(float(m[5:]))
            if m == "timeout":
                return False, "timeout", dict(META)
            if m == "nocli":
                return False, "no_cli", {}
            return True, "ok", {**META, "svgs": [icon_gen.sanitize(s) for s in SVGS], "ms": 1200}
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
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.object(icon_gen, "which_cli", lambda: {**META, "bin": "/usr/bin/claude"}),
            mock.patch.object(icon_gen, "generate", fake_generate),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        cls.ids = {}
        for name in ("Motors", "Wheels"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
        ok, _ = dashboard.write_roadmap(dashboard.find_project(cls.ids["Motors"]),
                                        "# Motors — roadmap\n\nElectric drivetrain for the cargo bike, from motor to battery.\n", "")
        assert ok
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "shots": shots, "mode": str(mode_file)}
        script = base / "icon_gen_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.home = Path(dashboard.project_home(dashboard.find_project(cls.ids["Motors"]), create=False))
        if os.environ.get("ENSEMBLE_GEN_DUMP"):
            Path(os.environ["ENSEMBLE_GEN_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def g(self, k):
        return self.got[k]

    def test_idle(self):
        d = self.g("idle")
        self.assertEqual(d["btn"], "Generate…")
        self.assertEqual(d["up"], "Upload image…")
        self.assertFalse(d["off"])
        self.assertEqual(d["expanded"], "false")
        self.assertFalse(d["box"])
        self.assertTrue(d["hint"].startswith("PNG, JPEG, SVG or WebP, up to 1 MB, or Generate. A picture that is not square is cropped"), d["hint"])
        self.assertEqual(d["genR"]["y"], d["upR"]["y"], "beside Upload image…")

    def test_busy(self):
        d = self.g("busy")
        self.assertTrue(d["box"])
        self.assertEqual(d["expanded"], "true")
        self.assertEqual(d["waits"], 4)
        self.assertRegex(d["foot"], r"^Drawing 4 icons… 1 s\s*Cancel$")
        self.assertRegex(self.g("busyLater")["foot"], r"Drawing 4 icons… 2 s")
        self.assertEqual(d["status"], ["Drawing 4 icons…"], "the seconds are not announced")
        self.assertEqual(d["head"], "Generate an icon")
        self.assertEqual(d["who"], "claude · haiku")

    def test_candidates(self):
        d = self.g("cands")
        self.assertEqual(d["group"], "Generated icons")
        self.assertEqual(len(d["tiles"]), 4)
        for i, t in enumerate(d["tiles"]):
            self.assertEqual((t["w"], t["h"], t["img"]), (64, 64, 44), t)
            self.assertTrue(t["loaded"] and t["svg"], t)
            self.assertEqual((t["role"], t["checked"], t["alt"]), ("radio", "false", f"Icon {i + 1}"))
        self.assertEqual([t["tab"] for t in d["tiles"]], [0, -1, -1, -1])
        self.assertEqual(d["focus"], "ps-gt-0")
        self.assertRegex(d["foot"], r"^Pick one\. Nothing changes until you save\.\s*More ideas$")
        self.assertEqual(d["sub"], "From the name Motors and its roadmap, “Electric drivetrain for the cargo bike, from motor to batte…”",
                         "the about text is cut at 60 characters")
        self.assertEqual(d["choice"], "square", "nothing changes before a pick")
        self.assertEqual(self.calls[0][:3], (self.ids["Motors"], "Motors", "Electric drivetrain for the cargo bike, from motor to battery."))

    def test_picked(self):
        d = self.g("picked")
        self.assertEqual([t["on"] for t in d["tiles"]], [False, True, False, False])
        self.assertEqual(d["tiles"][1]["checked"], "true")
        self.assertRegex(d["foot"], r"^Icon 2 picked: it becomes the image when you save\.")
        self.assertEqual(d["choice"], "image")
        self.assertEqual(d["img"], "drawn by claude haiku, not saved yet")
        self.assertEqual(d["bigSrc"], "data:image/svg+xml;base64,")
        self.assertEqual(d["sampleSrc"], "data:image/svg+xml;base64,")
        self.assertEqual(d["focus"], "ps-gt-1")
        self.assertTrue(d["save"])
        a = self.g("arrow")
        self.assertEqual((a["focus"], [t["on"] for t in a["tiles"]]), ("ps-gt-2", [False, False, True, False]))
        self.assertEqual(self.g("arrowBack")["focus"], "ps-gt-1")

    def test_cancel_brings_the_last_four_back(self):
        self.assertEqual(self.g("moreBusy")["waits"], 4)
        self.assertEqual(self.g("moreBusy")["focus"], "ps-gen-cancel")
        for k in ("cancelled", "afterCancel"):
            c = self.g(k)
            self.assertEqual([t["on"] for t in c["tiles"]], [False, True, False, False], k)
            self.assertEqual(c["img"], "drawn by claude haiku, not saved yet", k)
        self.assertEqual(self.g("cancelled")["focus"], "ps-gen-more")

    def test_a_timeout_is_said_plainly(self):
        d = self.g("failed")
        self.assertTrue(d["msg"].startswith("No icons this time: claude did not answer within 60 s."), d["msg"])
        self.assertEqual(d["msgSmall"], "Nothing was changed. It may be busy or signed out; try again in a minute, or upload an image.")
        self.assertEqual(d["btns"], ["Close", "Try again"])
        self.assertEqual(d["focus"], "ps-gen-again")
        self.assertEqual(d["img"], "drawn by claude haiku, not saved yet", "the picked one stays")
        c = self.g("closed")
        self.assertFalse(c["box"])
        self.assertEqual(c["focus"], "ps-gen")

    def test_save_keeps_the_picked_svg(self):
        s = self.g("saved")
        self.assertEqual((s["icon"]["file"], s["icon"]["name"]), ("project-icon.svg", "generated-icon.svg"))
        self.assertEqual((s["w"], s["h"], s["img"], s["loaded"]), (12, 12, True, True))
        kept = (self.home / "project-icon.svg").read_text(encoding="utf-8")
        self.assertEqual(kept, icon_gen.sanitize(SVGS[1]))

    def test_no_cli(self):
        d = self.g("nocli")
        self.assertTrue(d["off"])
        self.assertEqual(d["ariaOff"], "true")
        self.assertEqual(d["msg"], "Generating needs claude or codex on the hub, and neither is installed there."
                                   "Upload an image instead. Once either is installed, Generate works without a restart.")
        self.assertEqual(d["tiles"], [])
        b = self.g("cliBack")
        self.assertFalse(b["off"])
        self.assertEqual(len(b["tiles"]), 4, "once a CLI is there, the grey Generate draws")
        o = self.g("getOff")
        self.assertTrue(o["off"])
        self.assertFalse(o["box"])
        c = self.g("getOffClicked")
        self.assertTrue(c["msg"].startswith("Generating needs claude or codex"), c)

    def test_a_phone(self):
        for k in ("phIdle", "phBusy", "phCands", "phPicked", "phFailed"):
            d = self.g(k)
            self.assertLessEqual(d["scrollW"], d["clientW"], k)
            self.assertLessEqual(d["docW"], d["W"], k)
            for what, h in d["targets"]:
                self.assertGreaterEqual(h, 44, (k, what))
        d = self.g("phIdle")
        self.assertEqual((d["up"], d["btn"]), ("Upload…", "Generate…"))
        self.assertGreaterEqual(d["upR"]["y"], d["tR"]["b"] - 4, "the buttons sit on a row under the Image choice")
        self.assertEqual(d["upR"]["y"], d["genR"]["y"])
        self.assertEqual(d["hint"], "PNG, JPEG, SVG or WebP, up to 1 MB, or Generate. Cropped to a square.")
        c = self.g("phCands")
        tiles, row = c["tiles"], c["tilesR"]
        self.assertEqual(len(tiles), 4)
        self.assertEqual((tiles[0]["x"], tiles[-1]["r"]), (row["x"], row["r"]), "the tiles spread across the box")
        self.assertEqual(self.g("phPicked")["img"], "drawn by claude haiku, not saved yet")
        self.assertEqual(self.g("phFailed")["btns"], ["Close", "Try again"])

    def test_no_errors(self):
        self.assertEqual(self.g("errors"), [])


if __name__ == "__main__":
    unittest.main()
