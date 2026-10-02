"""The tool icons (#166): Your asks, Changes, Files, Board, Spec and the task
list, each its own drawing and colour (toolIcon, --tool-*).

In headless Chrome over CDP, against the strip test's hub in a thread:

* at 1280, in six themes: every button of the right-hand strip and of the
  left list's strip (the list unpinned) holds its tool's icon, in its tool's
  colour, 3:1 or better on its button at rest and hovered and on the strip;
  the open tool's icon is the button's one colour (--selected-fg), as before;
* at 390 (a phone), in light, dark and fjord: the Panels menu shows the same
  icons beside the names, 3:1 or better on the menu;
* a popped-out panel's window takes its tool's icon (a tile in the theme's
  colour) for its tab, the app's icon for any other panel;
* tools/make_tool_icons.py switches the style by its one line, and every
  style draws every tool.

Screenshots (the strips, the left strip and the Panels menu, per theme and
width) go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_middle import contrast  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_tool_strip import CDP_JS as STRIP_JS, NODE, start_hub  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tools import make_tool_icons  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
TOOLS = ["points", "changes", "workspace", "board", "spec"]
THEMES = ["light", "dark", "dim", "paper", "contrast", "fjord"]
SHOT_THEMES = ["light", "dark", "fjord"]

CDP_JS = STRIP_JS[:STRIP_JS.index("async function main()")] + r"""
// A colour as [r, g, b, a]; a translucent ground laid on the one under it.
const READ = `(() => {
  // rgb()/rgba(), or color(srgb r g b / a) from a color-mix() (channels 0..1).
  const rgba = s => { s = String(s); const m = s.match(/[\\d.]+/g) || [0, 0, 0]; const k = s.startsWith('color(') ? 255 : 1;
    return [+m[0] * k, +m[1] * k, +m[2] * k, m[3] === undefined ? 1 : +m[3]]; };
  const over = (top, under) => top[3] >= 1 ? top : [0, 1, 2].map(i => Math.round(top[i] * top[3] + under[i] * (1 - top[3]))).concat(1);
  const ground = e => { const st = []; for (let x = e; x; x = x.parentElement) { const c = rgba(getComputedStyle(x).backgroundColor); if (c[3] > 0) { st.push(c); if (c[3] >= 1) break; } }
    let g = [255, 255, 255, 1]; for (let i = st.length - 1; i >= 0; i--) g = over(st[i], g); return g; };
  const css = c => 'rgb(' + c.slice(0, 3).join(', ') + ')';
  window.__ti = (sel, groundSel) => [...document.querySelectorAll(sel)].map(b => {
    const svg = b.querySelector('svg.ti');
    const g = groundSel ? b.closest(groundSel) : b;
    return { id: b.dataset.dkAuto || b.dataset.pdToggle || (b.hasAttribute('data-pd-list') ? 'list' : ''), cls: svg ? svg.getAttribute('class') : '',
      icon: svg ? css(rgba(getComputedStyle(svg).color)) : '', bg: css(ground(b)), strip: g && g !== b ? css(ground(g.parentElement || g)) : '',
      w: svg ? Math.round(svg.getBoundingClientRect().width) : 0, on: b.classList.contains('on') };
  });
  window.__tok = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  return 0;
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = { desk: {}, phone: {} };
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    // A shot of the page, or of one element's box (at 3x, to read the icons).
    const shot = async (name, sel) => { if (!A.shots) return;
      let clip;
      if (sel) { const b = await evalIn(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()`);
        clip = { x: Math.max(0, b[0] - 4), y: Math.max(0, b[1] - 4), width: b[2] + 8, height: b[3] + 8, scale: 3 }; }
      const r = await c.send('Page.captureScreenshot', clip ? { format: 'png', clip } : { format: 'png' }, sessionId);
      fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const hover = async (sel) => {
      const [x, y] = await evalIn(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, sessionId);
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1');
    await until('window.ensBooted === true');
    await evalIn(`(() => { try { ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels', 'cd-list-dock'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
      SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock');
    await evalIn(READ);
    return { evalIn, until, shot, click, hover, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const theme = async (p, t) => { await p.evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(t)}; 0`); await sleep(200); };
  try {
    // ---- a desktop: both strips
    {
      const p = await page(1280, 800);
      await p.until('!!document.querySelector("#po-dock .dk-strip-right .dk-strip-btn") && !!LD.dock');
      await p.evalIn('LD.dock.unpin("list"); 0');
      await p.until('!!document.querySelector("#list-dock .dk-strip-btn")');
      await p.hover('#po-chat, #po-panel'); await sleep(200);
      for (const t of ${THEMES}) {
        await theme(p, t);
        const right = await p.evalIn('__ti("#po-dock .dk-strip-right .dk-strip-btn", ".dk-strip")');
        const left = await p.evalIn('__ti("#list-dock .dk-strip .dk-strip-btn", ".dk-strip")');
        const tok = await p.evalIn(`Object.fromEntries(['points', 'changes', 'workspace', 'board', 'spec', 'list'].map(id => [id, __tok('--tool-' + id)]))`);
        // Hovered: each button in turn.
        const hov = [];
        for (const id of ${TOOLS}) { await p.hover(`#po-dock .dk-strip-btn[data-dk-auto="${id}"]`); await sleep(120);
          hov.push((await p.evalIn(`__ti('#po-dock .dk-strip-btn[data-dk-auto="${id}"]')`))[0]); }
        await p.hover('#list-dock .dk-strip-btn'); await sleep(120);
        hov.push((await p.evalIn('__ti("#list-dock .dk-strip .dk-strip-btn")'))[0]);
        await p.hover('#po-panel'); await sleep(120);
        // Open: the Board.
        await p.click('#po-dock .dk-strip-btn[data-dk-auto="board"]'); await sleep(400);
        const open = (await p.evalIn(`__ti('#po-dock .dk-strip-btn[data-dk-auto="board"]')`))[0];
        open.fg = await p.evalIn(`getComputedStyle(document.querySelector('#po-dock .dk-strip-btn[data-dk-auto="board"]')).color`);
        if (${SHOT_THEMES}.includes(t)) {
          await p.shot(`icons-1280-${t}`);
          await p.shot(`icons-1280-${t}-strip`, '#po-dock .dk-strip-right');
          await p.shot(`icons-1280-${t}-left`, '#list-dock .dk-strip');
        }
        await p.click('#po-dock .dk-strip-btn[data-dk-auto="board"]'); await sleep(300);
        out.desk[t] = { right, left, tok, hov, open };
      }
      await theme(p, 'light');
      out.pop = await p.evalIn(`({ board: pdPopIconHref('board'), list: pdPopIconHref('list'), chat: pdPopIconHref('po-chat'), tile: __tok('--tool-board') })`);
      out.scrollW = await p.evalIn('[document.documentElement.scrollWidth, innerWidth]');
      await p.evalIn('LD.dock.pin("list"); 0');
      await p.close();
    }
    // ---- a phone: the Panels menu
    {
      const q = await page(390, 844, true);
      await q.until('!!document.querySelector(".pd-panels")');
      for (const t of ${SHOT_THEMES}) {
        await theme(q, t);
        await q.click('.pd-panels'); await q.until('!!document.querySelector(".pd-menu")');
        out.phone[t] = { rows: await q.evalIn('__ti(".pd-menu [role=menuitemcheckbox]", ".pd-menu")'),
          menuBg: await q.evalIn('getComputedStyle(document.querySelector(".pd-menu")).backgroundColor'),
          scrollW: await q.evalIn('[document.documentElement.scrollWidth, innerWidth]'),
          names: await q.evalIn('[...document.querySelectorAll(".pd-menu [role=menuitemcheckbox]")].map(b => b.textContent.trim())') };
        await q.shot(`icons-390-${t}-panels`);
        await q.shot(`icons-390-${t}-menu`, '.pd-menu');
        await q.click('.pd-panels'); await sleep(200);
      }
      await q.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
""".replace("${THEMES}", json.dumps(THEMES)).replace("${SHOT_THEMES}", json.dumps(SHOT_THEMES)).replace("${TOOLS}", json.dumps(TOOLS))


class TheDrawings(unittest.TestCase):
    """The icons' code (static checks, and the renderer run in Node)."""

    def test_the_strip_and_the_list_take_the_drawings(self):
        self.assertIn("const PD_ICON = Object.fromEntries(PD_TOOLS.map(id => [id, toolIcon(id)]));", INDEX)
        self.assertIn("const LD_ICON = toolIcon('list');", INDEX)
        self.assertIn("${pdMenuIcon(id)}", INDEX)

    def test_every_theme_has_every_tool_colour(self):
        tok = make_tool_icons.theme_tokens()
        for t in THEMES:
            for id_ in TOOLS + ["list"]:
                with self.subTest(theme=t, tool=id_):
                    c = tok[t][f"--tool-{id_}"].strip()
                    self.assertGreaterEqual(make_tool_icons.ratio(c, tok[t]["--surface"]), 3, c)
                    self.assertGreaterEqual(make_tool_icons.ratio(c, tok[t]["--surface-sunken"]), 3, c)
                    self.assertGreaterEqual(make_tool_icons.ratio(tok[t]["--tool-ink"], c), 3, "a tile's drawing on it")

    @unittest.skipUnless(NODE, "needs Node")
    def test_every_style_draws_every_tool(self):
        js = make_tool_icons.page_code() + "\nconsole.log(JSON.stringify(['glyph', 'tile', 'dot'].map(s => "
        js += "['points', 'changes', 'workspace', 'board', 'spec', 'list'].map(id => toolIcon(id, s)))));"
        out = subprocess.run([NODE, "-e", js], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        for style, icons in zip(("glyph", "tile", "dot"), got):
            for id_, svg in zip(TOOLS + ["list"], icons):
                with self.subTest(style=style, tool=id_):
                    self.assertTrue(svg.startswith(f'<svg class="ti ti-{id_} ti-s-{style}" viewBox="0 0 24 24"'), svg[:80])
                    self.assertIn('aria-hidden="true"', svg)
        # The six drawings differ (no two tools look alike).
        self.assertEqual(len(set(got[0])), 6)

    def test_the_style_switch_rewrites_one_line(self):
        # On a copy: the real page may be served by a live hub, or read by another test meanwhile.
        orig = make_tool_icons.PAGE.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            for crlf in (False, True):
                with self.subTest(crlf=crlf):
                    src = orig.replace(b"\r\n", b"\n")
                    if crlf:
                        src = src.replace(b"\n", b"\r\n")
                    copy = Path(d) / "index.html"
                    copy.write_bytes(src)
                    with mock.patch.object(make_tool_icons, "PAGE", copy), \
                            contextlib.redirect_stdout(io.StringIO()):
                        make_tool_icons.ship("tile")
                    now = copy.read_bytes()
                    self.assertIn(b"const TOOL_ICON_STYLE = 'tile';", now)
                    self.assertEqual(now.count(b"\n"), src.count(b"\n"))
                    self.assertEqual(now.count(b"\r\n"), src.count(b"\r\n"), "its line ends kept")
                    self.assertEqual(len(now) - len(src), len("tile") - len(make_tool_icons.SHIPPED), "one word changed")
        self.assertEqual(make_tool_icons.PAGE.read_bytes(), orig, "the real page untouched")
        self.assertIn(f"const TOOL_ICON_STYLE = '{make_tool_icons.SHIPPED}';", INDEX)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-icons-")
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "shots": shots}
        script = Path(cls.tmp.name) / "icons_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_both_strips_hold_their_icons_in_their_colours(self):
        for t, g in self.got["desk"].items():
            with self.subTest(theme=t):
                self.assertEqual([b["id"] for b in g["right"]], TOOLS)
                self.assertEqual([b["id"] for b in g["left"]], ["list"])
                for b in g["right"] + g["left"]:
                    self.assertIn(f"ti ti-{b['id']} ti-s-glyph", b["cls"])
                    self.assertGreaterEqual(b["w"], 16, "it reads at strip size")
                    self.assertGreaterEqual(contrast(b["icon"], b["bg"]), 3, (t, b))
                    self.assertGreaterEqual(contrast(b["icon"], b["strip"]), 3, (t, b))
                # Each in its own colour, the token's.
                self.assertEqual(len({b["icon"] for b in g["right"] + g["left"]}), 6, g["right"])

    def test_a_hovered_button_keeps_its_icon_legible(self):
        for t, g in self.got["desk"].items():
            for b in g["hov"]:
                with self.subTest(theme=t, tool=b["id"]):
                    self.assertGreaterEqual(contrast(b["icon"], b["bg"]), 3, b)

    def test_the_open_tool_is_one_colour_as_before(self):
        for t, g in self.got["desk"].items():
            with self.subTest(theme=t):
                o = g["open"]
                self.assertTrue(o["on"])
                self.assertEqual(o["icon"], o["fg"], "the button's --selected-fg")
                self.assertGreaterEqual(contrast(o["icon"], o["bg"]), 3, o)

    def test_the_phone_panels_menu_shows_the_same_icons(self):
        for t, g in self.got["phone"].items():
            with self.subTest(theme=t):
                rows = {r["id"]: r for r in g["rows"]}
                for id_ in TOOLS:
                    self.assertIn(f"ti ti-{id_}", rows[id_]["cls"])
                    self.assertGreaterEqual(contrast(rows[id_]["icon"], rows[id_]["bg"]), 3, rows[id_])
                self.assertEqual(rows["po-chat"]["cls"], "", "the chat has no tool icon")
                self.assertLessEqual(g["scrollW"][0], g["scrollW"][1])

    def test_a_popped_out_tool_takes_its_icon(self):
        pop = self.got["pop"]
        self.assertTrue(pop["board"].startswith("data:image/svg+xml,"))
        self.assertIn("ti-board", pop["board"])
        self.assertIn(pop["tile"].replace("#", "%23"), pop["board"], "the theme's colour, baked in")
        self.assertIn("ti-list", pop["list"])
        self.assertTrue(pop["chat"].endswith("/static/icons/favicon.svg"))

    def test_nothing_overflows_and_no_errors(self):
        self.assertLessEqual(self.got["scrollW"][0], self.got["scrollW"][1])
        self.assertEqual(self.got["consoleErrors"], [])


if __name__ == "__main__":
    unittest.main()
