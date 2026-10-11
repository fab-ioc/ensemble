"""Dock v0.22.0 in Ensemble (#205): its states read Ensemble's tokens, and a
right-click on a tab is Dock's menu while Ensemble's own right-clicks stay.

In headless Chrome over CDP, against a hub in a thread (test_tool_strip's),
with each state forced (CSS.forcePseudoState), in all seven themes:

* an inactive tab rests at --fg-subtle and hovered reads --fg, at 4.5:1 or
  more on its bar (v0.19's `.dk-tab:not(.on):hover` read --dk-fg3, dimmer
  than at rest, until the page set --dk-tab-hover-fg);
* the active tab is --selected-fg on --selected-bg over an --accent line;
* a title-bar button hovered is --fg on --hover; a strip button hovered is
  --fg on --hover with a --border-strong edge; a menu row hovered is --hover;
* a focused tab has the --focus-ring; a sash hovered is --border-strong.

A right-click on an inactive tab opens Dock's tab menu and leaves the tab as
it was; a file panel's tab menu has "Open in new window". A right-click on a
file in Files is Ensemble's menu, not Dock's; inside the PO's chat frame Dock
opens nothing.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests.test_middle import contrast
from tests.test_page_update import CHROME
from tests.test_tool_strip import NODE, start_hub
from tests import chrome_profile

THEMES = ["light", "dark", "dim", "paper", "contrast", "fjord", "intellij-dark"]

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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown' || (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')) this.errors.push(m.params); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// The page's helpers: a token's colour as computed, an element's first painted background, its colours.
// Colours come back as rgb(a) or color(srgb ...) (colour-mixed tokens): rgba() turns either into [r, g, b, a], css() into rgb(a) text.
const HELP = `window.__t = {
  rgba: s => { const m = (s.match(/[0-9.]+/g) || []).map(Number); if (s.startsWith('color(')) { m[0] *= 255; m[1] *= 255; m[2] *= 255; }
    return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1]; },
  css: s => { const [r, g, b, a] = __t.rgba(s).map((v, i) => i < 3 ? Math.round(v) : Math.round(v * 1000) / 1000);
    return a < 1 ? 'rgba(' + [r, g, b, a].join(', ') + ')' : 'rgb(' + [r, g, b].join(', ') + ')'; },
  tok: (name, prop = 'color') => { const d = document.createElement('div'); d.style.setProperty(prop, 'var(' + name + ')'); document.body.appendChild(d);
    const v = getComputedStyle(d).getPropertyValue(prop); d.remove(); return __t.css(v); },
  // The background as painted: translucent layers composited down to the first solid one.
  bg: e => { const layers = [];
    for (let x = e; x; x = x.parentElement) { const c = __t.rgba(getComputedStyle(x).backgroundColor); if (c[3] > 0) layers.push(c); if (c[3] >= 1) break; }
    let c = [255, 255, 255]; for (const [r, g, b, a] of layers.reverse()) c = [r * a + c[0] * (1 - a), g * a + c[1] * (1 - a), b * a + c[2] * (1 - a)];
    return 'rgb(' + c.map(Math.round).join(', ') + ')'; },
  look: sel => { const e = document.querySelector(sel); if (!e) return null; const s = getComputedStyle(e);
    return { color: __t.css(s.color), bg: __t.bg(e), ownBg: __t.css(s.backgroundColor), border: __t.css(s.borderTopColor), under: __t.css(s.borderBottomColor), shadow: s.boxShadow }; },
}; 0`;
const out = {};
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    const S = (m, p) => c.send(m, p, sessionId);
    await S('Page.enable'); await S('Runtime.enable');
    await S('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    const evalIn = async (expr) => { const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const at = sel => evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    const press = async (sel, button = 'left') => { const [x, y] = await at(sel);
      await S('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      for (const type of ['mousePressed', 'mouseReleased']) await S('Input.dispatchMouseEvent', { type, x, y, button, buttons: button === 'right' ? 2 : 1, clickCount: 1 }); };
    const key = async (k, code, vk) => { for (const type of ['keyDown', 'keyUp']) await S('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: vk }); };
    await S('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' });
    await S('Page.navigate', { url: A.base + '/' });
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1', 30000);
    await until('window.ensBooted === true', 30000);
    await evalIn(`(() => { ['cd-tool-strip', 'cd-tool-open', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k));
      SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn") && !!document.querySelector("#po-panel iframe.po-session:not([hidden])")', 30000);
    await sleep(500);
    await evalIn(HELP);
    // A file panel beside the conversation: its tab and the conversation's.
    await press('#po-dock .dk-strip-btn[data-dk-auto="workspace"]');
    await until('!!PD.els.workspace.querySelector(".wse.file[data-path$=\\"README.md\\"]")');
    await evalIn('PD.els.workspace.querySelector(".wse.file[data-path$=\\"README.md\\"]").click(); 0');
    await until('[...PD.rt.keys()].some(k => k.startsWith("file:"))', 20000);
    await until('!!PD.els.workspace.querySelector(".wse.file[data-path$=\\"NOTES.md\\"]")');
    await evalIn('PD.els.workspace.querySelector(".wse.file[data-path$=\\"NOTES.md\\"]").click(); 0');
    await until('[...PD.rt.keys()].filter(k => k.startsWith("file:")).length === 2', 20000);
    await press('#po-panel iframe.po-session:not([hidden])'); await sleep(500);
    out.tabs = await evalIn(`[...document.querySelectorAll('#po-dock .dk-tab')].filter(e => e.getBoundingClientRect().width > 0).map(e => ({ id: e.dataset.dkTab, on: e.classList.contains('on') }))`);
    // Marks the elements each state is forced on.
    const mark = await evalIn(`(() => { const vis = e => e && e.getBoundingClientRect().width > 0;
      const tabs = [...document.querySelectorAll('#po-dock .dk-tab')].filter(vis);
      const off = tabs.find(e => !e.classList.contains('on')), on = tabs.find(e => e.classList.contains('on'));
      const btn = [...document.querySelectorAll('#po-dock .dk-head .dk-btn')].find(vis);
      const strip = document.querySelector('#po-dock .dk-strip-btn[data-dk-auto="board"]');
      const bar = [...document.querySelectorAll('#po-dock .dk-bar')].find(vis);
      for (const [n, e] of [['off', off], ['on', on], ['btn', btn], ['strip', strip], ['bar', bar]]) if (e) e.dataset.t = n;
      return { off: !!off, on: !!on, btn: !!btn, strip: !!strip, bar: !!bar }; })()`);
    out.mark = mark;
    await S('DOM.enable'); await S('CSS.enable');
    const forced = new Set();
    const force = async (sel, states) => { const { root } = await S('DOM.getDocument', { depth: 0 });
      const { nodeId } = await S('DOM.querySelector', { nodeId: root.nodeId, selector: sel }); if (!nodeId) throw new Error('no node ' + sel);
      await S('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: states }); if (states.length) forced.add(sel); else forced.delete(sel); };
    const unforce = async () => { for (const s of [...forced]) await force(s, []); };
    const state = async (sel, states) => { await force(sel, states); await sleep(60); const v = await evalIn(`__t.look(${JSON.stringify(sel)})`); await force(sel, []); return v; };
    out.themes = {};
    for (const t of A.themes) {
      await evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(t)}; 0`); await sleep(200);
      const g = out.themes[t] = { tok: await evalIn(`({ fg: __t.tok('--fg'), subtle: __t.tok('--fg-subtle'), muted: __t.tok('--fg-muted'), accent: __t.tok('--accent'),
        hover: __t.tok('--hover', 'background-color'), selFg: __t.tok('--selected-fg'), selBg: __t.tok('--selected-bg', 'background-color'), strong: __t.tok('--border-strong'), ring: __t.tok('--focus-ring') })`) };
      g.offRest = await evalIn(`__t.look('[data-t="off"]')`);
      g.offHover = await state('[data-t="off"]', ['hover']);
      g.on = await evalIn(`__t.look('[data-t="on"]')`);
      g.onHover = await state('[data-t="on"]', ['hover']);
      g.focus = await state('[data-t="off"]', ['focus', 'focus-visible']);
      g.btnHover = await state('[data-t="btn"]', ['hover']);
      g.stripHover = await state('[data-t="strip"]', ['hover']);
      g.barHover = mark.bar ? await state('[data-t="bar"]', ['hover']) : null;
    }
    // A menu row hovered (the conversation's ⋯), in each theme.
    await press('#po-dock .dk-head:has([data-t="on"]) [data-dk-act="menu"], #po-dock .dk-head:has([data-t="off"]) [data-dk-act="menu"]'); await sleep(300);
    await evalIn(`(() => { const b = document.querySelector('.dk-menu.dk-options button:not([aria-disabled="true"])'); if (b) b.dataset.t = 'row'; return 0; })()`);
    for (const t of A.themes) {
      await evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(t)}; 0`); await sleep(150);
      out.themes[t].rowHover = await state('[data-t="row"]', ['hover']);
    }
    await unforce();
    await evalIn('document.documentElement.dataset.theme = "light"; 0');
    await key('Escape', 'Escape', 27); await sleep(300);
    // Right-click: an inactive tab, then the file panel's tab.
    const menu = () => evalIn(`(() => { const m = document.querySelector('.dk-menu.dk-options'); return m ? [...m.querySelectorAll('button')].map(b => b.textContent.trim()) : null; })()`);
    const onTab = () => evalIn(`[...document.querySelectorAll('#po-dock .dk-tab.on')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.dataset.dkTab)`);
    const before = await onTab();
    await press('[data-t="off"]', 'right'); await sleep(400);
    out.rcOff = { before, after: await onTab(), menu: await menu(), tab: await evalIn('document.querySelector(\'[data-t="off"]\').dataset.dkTab') };
    await key('Escape', 'Escape', 27); await sleep(300);
    const fileTab = await evalIn(`(() => { const e = [...document.querySelectorAll('#po-dock .dk-tab')].find(e => (e.dataset.dkTab || '').startsWith('file:') && e.getBoundingClientRect().width > 0); if (e) e.dataset.t = 'file'; return !!e; })()`);
    out.rcFile = fileTab ? (await press('[data-t="file"]', 'right'), await sleep(400), { menu: await menu() }) : null;
    await key('Escape', 'Escape', 27); await sleep(300);
    // Ensemble's own: a file in Files, and the PO's chat frame.
    if (await evalIn('PD.dock.flyOpen()') !== 'workspace') await press('#po-dock .dk-strip-btn[data-dk-auto="workspace"]');
    await until('!!PD.els.workspace.querySelector(".wse.file[data-path$=\\"README.md\\"]") && PD.dock.flyOpen() === "workspace"');
    await sleep(300);
    await press('#po-dock .wse.file[data-path$="README.md"]', 'right'); await sleep(400);
    out.rcFiles = await evalIn(`({ dcm: !!document.querySelector('.dcm:not([hidden])'), dock: !!document.querySelector('.dk-menu.dk-options') })`);
    await key('Escape', 'Escape', 27); await sleep(300);
    await evalIn(`document.querySelectorAll('.dcm').forEach(m => m.hidden = true); 0`);
    await press('#po-panel iframe.po-session:not([hidden])', 'right'); await sleep(400);
    out.rcChat = await evalIn(`({ dock: !!document.querySelector('.dk-menu.dk-options') })`);
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e, JSON.stringify(out).slice(0, 3000)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class DockV022(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-dk22-")
        home = next(f.parent for f in cls.root.rglob("README.md") if "Motors" in f.read_text(encoding="utf-8"))
        (home / "NOTES.md").write_text("# Notes\n", encoding="utf-8")
        base = Path(cls.tmp.name)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "themes": THEMES}
        script = base / "dk22_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_DK22_PRINT"):
            print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_states_are_there_to_force(self):
        self.assertEqual(self.got["mark"], {"off": True, "on": True, "btn": True, "strip": True, "bar": True}, self.got.get("tabs"))

    def test_an_inactive_tab_rests_subtle_and_hovered_reads_fg(self):
        for t in THEMES:
            g = self.got["themes"][t]
            with self.subTest(theme=t):
                self.assertEqual(g["offRest"]["color"], g["tok"]["subtle"])
                self.assertEqual(g["offHover"]["color"], g["tok"]["fg"], "hovered: brighter than at rest, never dimmer")
                self.assertGreaterEqual(contrast(g["offHover"]["color"], g["offHover"]["bg"]), 4.5)
                self.assertGreaterEqual(contrast(g["offRest"]["color"], g["offRest"]["bg"]), 4.5)

    def test_the_active_tab_is_selected_over_an_accent_line(self):
        for t in THEMES:
            g = self.got["themes"][t]
            with self.subTest(theme=t):
                self.assertEqual((g["on"]["color"], g["on"]["ownBg"]), (g["tok"]["selFg"], g["tok"]["selBg"]))
                self.assertIn(g["tok"]["accent"], g["on"]["shadow"])
                self.assertEqual(g["onHover"]["color"], g["tok"]["selFg"], "hovered: as at rest")
                self.assertGreaterEqual(contrast(g["on"]["color"], g["on"]["bg"]), 4.5)

    def test_buttons_rows_focus_and_sash_read_ensemble_s_tokens(self):
        for t in THEMES:
            g = self.got["themes"][t]
            with self.subTest(theme=t):
                self.assertEqual((g["btnHover"]["color"], g["btnHover"]["ownBg"]), (g["tok"]["fg"], g["tok"]["hover"]))
                self.assertEqual((g["stripHover"]["color"], g["stripHover"]["ownBg"], g["stripHover"]["border"]),
                                 (g["tok"]["fg"], g["tok"]["hover"], g["tok"]["strong"]))
                self.assertEqual(g["rowHover"]["ownBg"], g["tok"]["hover"])
                self.assertIn(g["tok"]["ring"], g["focus"]["shadow"])
                self.assertEqual(g["barHover"]["ownBg"], g["tok"]["strong"])

    def test_a_right_click_on_a_tab_is_dock_s_menu_and_switches_nothing(self):
        rc = self.got["rcOff"]
        self.assertTrue(rc["menu"], rc)
        self.assertEqual(rc["after"], rc["before"], "the tab right-clicked stays inactive")
        self.assertIsNotNone(self.got["rcFile"], self.got.get("tabs"))
        self.assertIn("Open in new window", self.got["rcFile"]["menu"] or [])

    def test_ensemble_s_own_right_clicks_stay_its_own(self):
        self.assertEqual(self.got["rcFiles"], {"dcm": True, "dock": False})
        self.assertEqual(self.got["rcChat"], {"dock": False})

    def test_no_console_errors(self):
        self.assertEqual(self.got["consoleErrors"], [])


if __name__ == "__main__":
    unittest.main()
