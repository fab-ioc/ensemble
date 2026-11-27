"""A visual fits the width of its pane (#427, GitHub issue 70).

In a real browser (a hub in a thread, headless Chrome over CDP), the file
view of an HTML page, a picture wider than any pane, a small picture and the
hub's computed plan page (plan_picture), at pane widths from under the least
(300) to a wide window (1920):

* a rendered page takes the pane's width, held between --visual-min (320)
  and --visual-max (1600); under the least it keeps 320 and scrolls sideways
  in main, never the page; over the most it is centred;
* a picture shrinks with the pane to the least, never grows past its own
  size, and is centred; zoomed, its 100% is that fit;
* the width follows a resize at once, with no reload;
* the plan page's graph scales with the page between its bounds, and its
  task list goes under the graph when narrow and beside it when wide.

The browser part is skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import plan_picture  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402


def _png(w: int, h: int) -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _plan(cols: int) -> str:
    """A plan page of `cols` columns: a chain of gates, one task a column,
    and a second task beside the first gate."""
    def row(i, gates):
        return {"id": f"t{i}", "no": f"#{i}", "title": f"Task {i} of the plan", "priority": 2,
                "owner": "claude", "state": "gate" if gates else "running",
                "word": "waits" if gates else "running", "points": [], "days": 1.0, "ask": "",
                "merged": False, "doneAt": 0,
                "gates": [{"id": g, "label": "#" + g[1:], "when": "merged", "met": False} for g in gates]}
    rows = [row(1, [])] + [row(i, [f"t{i - 1}"]) for i in range(2, cols + 1)] + [row(cols + 1, ["t1"])]
    return plan_picture.render({"project": "Recipe Box", "rows": rows, "earlier": [], "earlierAt": 0,
                                "drawnAt": time.time()})


CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--window-size=1100,700', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
  // The pane's width: the viewport the file view is drawn in (a side panel, a tab, a window).
  const width = async w => { await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: 800, deviceScaleFactor: 1, mobile: false }, sessionId); await ev(`new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(1))))`); };
  return { ev, until, width, go: async (url, ready) => { await c.send('Page.navigate', { url }, sessionId); await until(ready); } };
}
const PAGE_READY = `(() => { const f = document.querySelector('iframe.file'); return !!(f && f.contentDocument && f.contentDocument.readyState === 'complete' && f.contentDocument.body && f.contentDocument.body.childElementCount && typeof FVZ !== 'undefined' && FVZ.kind === 'page'); })()`;
const IMG_READY = `(() => { const i = document.querySelector('img.file'); return !!(i && i.complete && i.naturalWidth && typeof FVZ !== 'undefined' && FVZ.kind === 'img'); })()`;
// The visual, its pane's room (main's content box) and who scrolls sideways.
const MEASURE = sel => `(() => { const m = document.getElementById('main'), cs = getComputedStyle(m), el = m.querySelector('${sel}'), r = el.getBoundingClientRect();
  const pl = parseFloat(cs.paddingLeft), room = m.clientWidth - pl - parseFloat(cs.paddingRight);
  return { w: Math.round(r.width * 10) / 10, room, offset: Math.round((r.left + m.scrollLeft - m.getBoundingClientRect().left - pl) * 10) / 10,
    wider: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('#main')).slice(0, 4).map(e => e.tagName + '#' + e.id + '.' + e.className + ':' + Math.round(e.getBoundingClientRect().right)),
    mainScrolls: m.scrollWidth > m.clientWidth,
    // The visual never widens the page: main (which holds it) ends inside the
    // viewport. (The header's buttons at 300 to 420 are another matter, #427's report.)
    pageScrolls: m.getBoundingClientRect().right > innerWidth + 0.5,
    vw: innerWidth, mark: window.__mark || 0 }; })()`;
const PLAN = `(() => { const f = document.querySelector('iframe.file'), d = f.contentDocument, sv = d.querySelector('.graph svg'), g = d.querySelector('.graph'), l = d.querySelector('.list');
  const sr = sv.getBoundingClientRect(), gr = g.getBoundingClientRect(), lr = l.closest('.panel').getBoundingClientRect();
  const gs = getComputedStyle(g);
  return { frame: Math.round(f.getBoundingClientRect().width), svg: Math.round(sr.width), room: Math.round(g.clientWidth - parseFloat(gs.paddingLeft) - parseFloat(gs.paddingRight)), graphScrolls: g.scrollWidth > g.clientWidth,
    docScrolls: d.scrollingElement.scrollWidth > d.scrollingElement.clientWidth,
    listBeside: lr.left >= gr.right - 1, listUnder: lr.top >= gr.bottom - 1 }; })()`;

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { page: {}, big: {}, small: {}, plan4: {}, plan2: {}, plan8: {} };
  try {
    const p = await page(c);
    // ---- A rendered page ------------------------------------------------------
    for (const w of [300, 420, 900, 1920]) {
      await p.width(w);
      await p.go(A.htmlUrl, PAGE_READY);
      out.page[w] = await p.ev(MEASURE('iframe.file'));
    }
    // Live: the pane resized with the page open, no reload.
    await p.width(900); await p.go(A.htmlUrl, PAGE_READY); await p.ev(`window.__mark = 7, 1`);
    await p.width(1300); out.page.live1300 = await p.ev(MEASURE('iframe.file'));
    await p.width(1920); out.page.live1920 = await p.ev(MEASURE('iframe.file'));
    await p.width(420); out.page.live420 = await p.ev(MEASURE('iframe.file'));
    out.page.tokens = await p.ev(`(() => { const s = getComputedStyle(document.documentElement); return [s.getPropertyValue('--visual-min').trim(), s.getPropertyValue('--visual-max').trim()]; })()`);

    // ---- A picture wider than any pane (2000 px) --------------------------------
    await p.ev(`localStorage.removeItem('cd-zoom'), 1`);
    for (const w of [300, 900, 1920]) {
      await p.width(w);
      await p.go(A.bigUrl, IMG_READY);
      out.big[w] = await p.ev(MEASURE('img.file'));
    }
    await p.ev(`window.__mark = 8, 1`);
    await p.width(1300); out.big.live1300 = await p.ev(MEASURE('img.file'));
    // Zoomed: 110% of the fit within the bounds, at 1920.
    await p.width(1920);
    await p.ev(`(() => { document.querySelector('#fv-zoom [data-pz=in]').click(); return 1; })()`);
    out.big.z110 = await p.ev(MEASURE('img.file'));
    out.big.edge = await p.ev(`(() => { const c = getComputedStyle(document.querySelector('img.file')); return parseFloat(c.borderLeftWidth) + parseFloat(c.borderRightWidth); })()`);
    await p.ev(`(() => { document.querySelector('#fv-zoom [data-pz=out]').click(); document.querySelector('#fv-zoom [data-pz=out]').click(); return 1; })()`);
    await p.width(300);
    out.big.z90at300 = await p.ev(MEASURE('img.file'));
    await p.ev(`(() => { document.querySelector('#fv-zoom [data-pz=reset]').click(); return 1; })()`);
    out.big.back = await p.ev(MEASURE('img.file'));

    // ---- A small picture (300 px) ---------------------------------------------
    for (const w of [300, 1920]) {
      await p.width(w);
      await p.go(A.smallUrl, IMG_READY);
      out.small[w] = await p.ev(MEASURE('img.file'));
    }

    // ---- The computed plan page --------------------------------------------------
    for (const w of [420, 900, 1300]) {
      await p.width(w);
      await p.go(A.plan4Url, PAGE_READY);
      out.plan4[w] = await p.ev(PLAN);
    }
    await p.width(1920); out.plan4.live1920 = await p.ev(PLAN);
    for (const w of [900, 1920]) {
      await p.width(w);
      await p.go(A.plan2Url, PAGE_READY);
      out.plan2[w] = await p.ev(PLAN);
    }
    // Eight columns (3120 drawn): the most wins over the 60% floor.
    for (const w of [900, 1920]) {
      await p.width(w);
      await p.go(A.plan8Url, PAGE_READY);
      out.plan8[w] = await p.ev(PLAN);
    }
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
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-vw-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, files = base / "state", base / "files"
        state.mkdir()
        files.mkdir()
        for p in [
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
            mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        (files / "page.html").write_text("<h1>A page</h1>" + "".join(f"<p>Line {i} of the page.</p>" for i in range(40)), encoding="utf-8")
        (files / "big.png").write_bytes(_png(2000, 200))
        (files / "small.png").write_bytes(_png(300, 120))
        (files / "plan4.html").write_text(_plan(4), encoding="utf-8")
        (files / "plan2.html").write_text(_plan(2), encoding="utf-8")
        (files / "plan8.html").write_text(_plan(8), encoding="utf-8")

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        url = lambda n: f"{b}/fileview?path={quote(str(files / n))}"  # noqa: E731
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": b,
                "htmlUrl": url("page.html"), "bigUrl": url("big.png"), "smallUrl": url("small.png"),
                "plan4Url": url("plan4.html"), "plan2Url": url("plan2.html"), "plan8Url": url("plan8.html")}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    LO, HI = 320, 1600

    def fits(self, m, want, msg=""):
        self.assertAlmostEqual(m["w"], want, delta=1, msg=(msg, m))
        self.assertFalse(m["pageScrolls"], ("the visual never makes the page scroll sideways", msg, m))
        if want > m["room"] + 1:
            self.assertTrue(m["mainScrolls"], ("under the least the visual scrolls in its pane", msg, m))
            self.assertAlmostEqual(m["offset"], 0, delta=1, msg=m)
        else:
            self.assertFalse(m["mainScrolls"], (msg, m))
            self.assertAlmostEqual(m["offset"], (m["room"] - m["w"]) / 2, delta=1, msg=("centred", msg, m))

    def test_the_bounds_are_tokens(self):
        self.assertEqual(self.got["page"]["tokens"], ["320px", "1600px"])

    def test_a_page_takes_the_panes_width_within_the_bounds(self):
        pg = self.got["page"]
        for w in ("300", "420", "900", "1920"):
            m = pg[w]
            self.fits(m, min(self.HI, max(self.LO, m["room"])), w)
        self.assertEqual(pg["300"]["w"], self.LO)
        self.assertEqual(pg["1920"]["w"], self.HI)
        self.assertGreater(pg["1920"]["offset"], 100, "a wide window centres it")

    def test_a_page_follows_a_resize_at_once(self):
        pg = self.got["page"]
        for key in ("live1300", "live1920", "live420"):
            m = pg[key]
            self.assertEqual(m["mark"], 7, "no reload")
            self.fits(m, min(self.HI, max(self.LO, m["room"])), key)
        self.assertGreater(pg["live1300"]["w"], 1100, "past the old 1040 cap")

    def test_a_wide_picture_shrinks_to_its_pane_between_the_bounds(self):
        bg = self.got["big"]
        for w in ("300", "900", "1920"):
            m = bg[w]
            self.fits(m, min(self.HI, max(self.LO, m["room"])), w)
        self.assertEqual(bg["1920"]["w"], self.HI)
        self.assertEqual(bg["live1300"]["mark"], 8, "no reload")
        self.fits(bg["live1300"], bg["live1300"]["room"], "live")

    def test_zoom_starts_from_the_fit_within_the_bounds(self):
        bg, e = self.got["big"], self.got["big"]["edge"]
        self.assertAlmostEqual(bg["z110"]["w"], round((self.HI - e) * 1.1 + e), delta=1, msg=bg["z110"])
        self.assertFalse(bg["z110"]["pageScrolls"])
        # 90% at a 300 pane: 90% of the least, the zoom on top of the bounds.
        self.assertAlmostEqual(bg["z90at300"]["w"], round((self.LO - e) * 0.9 + e), delta=1, msg=bg["z90at300"])
        self.fits(bg["back"], self.LO, "back at 100%")

    def test_a_small_picture_never_grows_past_its_size(self):
        sm = self.got["small"]
        self.fits(sm["1920"], 302, "its own 300 and its border")
        self.fits(sm["300"], 302, "under its own size it keeps it")

    def test_the_plan_graph_scales_with_the_page(self):
        p4 = self.got["plan4"]
        drawn = plan_picture.PAD * 2 + 4 * plan_picture.BOX_W + 3 * plan_picture.GAP
        floor = round(drawn * plan_picture.GRAPH_SHRINK)
        for w in ("420", "900"):
            self.assertEqual(p4[w]["svg"], floor, ("never under 60% of its drawn size", w, p4[w]))
            self.assertTrue(p4[w]["graphScrolls"], "then it scrolls sideways in its own panel")
            self.assertFalse(p4[w]["docScrolls"], "never the page")
        self.assertTrue(floor < p4["1300"]["svg"] < drawn, p4["1300"])
        self.assertFalse(p4["1300"]["graphScrolls"])
        self.assertGreater(p4["live1920"]["svg"], p4["1300"]["svg"], "a resize grows it at once")
        self.assertEqual(p4["live1920"]["frame"], self.HI)

    def test_the_plan_list_goes_under_the_graph_when_narrow(self):
        p2 = self.got["plan2"]
        self.assertTrue(p2["900"]["listUnder"], p2["900"])
        self.assertTrue(p2["1920"]["listBeside"], p2["1920"])
        drawn = plan_picture.PAD * 2 + 2 * plan_picture.BOX_W + plan_picture.GAP
        self.assertGreater(p2["1920"]["svg"], drawn, "and grows past its drawn size in a wide one")
        self.assertFalse(p2["1920"]["graphScrolls"])

    def test_a_very_wide_plan_graph_fits_a_wide_window(self):
        p8 = self.got["plan8"]
        self.assertEqual(p8["1920"]["frame"], self.HI)
        self.assertEqual(p8["1920"]["svg"], p8["1920"]["room"], ("the graph panel's whole width", p8))
        self.assertGreaterEqual(p8["1920"]["svg"], plan_picture.GRAPH_ROOM)
        self.assertFalse(p8["1920"]["graphScrolls"], "no scroll at 1920")
        self.assertEqual(p8["900"]["svg"], plan_picture.GRAPH_ROOM, p8)
        self.assertTrue(p8["900"]["graphScrolls"], "then it scrolls in its panel")
        self.assertFalse(p8["900"]["docScrolls"])


if __name__ == "__main__":
    unittest.main()
