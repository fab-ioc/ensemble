"""#136/#142 in a real page: Settings scrolls; Theme and Accent are submenus.

In headless Chrome over CDP, against a hub in a thread, at a short desktop
window and at a phone's width:

* the Settings panel, opened from the avatar menu, stays on screen and its own
  scrollbar brings the last section into view (it once hung off a page that no
  longer scrolls);
* the avatar menu's Theme item opens a list of every theme the hub allows
  (dashboard._SETTINGS_ALLOWED_VALUES["theme"], less the empty "default"), with
  the current one ticked and a swatch each; on a phone the list is inside the
  menu and on screen, not a flyout off it;
* choosing a theme or accent applies it at once and the hub saves it;
  Settings contains neither appearance control.

Skipped without Node or Chrome; launches go through tests/chrome_profile.py.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!document.querySelector("#me-themes .theme-opt")', 30000);
    return { evalIn, until, targetId };
  };
  const RECT = `(e => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom }; })`;
  try {
    for (const [name, w, h, mobile] of [['desktop', 1100, 420, false], ['wide1280', 1280, 720, false], ['wide1440', 1440, 900, false], ['phone', 390, 600, true], ['phone360', 360, 800, true], ['phone430', 430, 932, true], ['short', 844, 390, true]]) {
      const { evalIn, until, targetId } = await page(w, h, mobile);
      const o = out[name] = {};
      // Settings: from the avatar menu, scrolled to its last section.
      await evalIn('document.getElementById("me-btn").click(); 0');
      await evalIn('[...document.querySelectorAll("#me-menu .me-item")].find(b => /^Settings/.test(b.textContent)).click(); 0');
      await until('!document.getElementById("settings-panel").hidden');
      o.settings = await evalIn(`(() => { const p = document.getElementById('settings-panel');
        const last = [...p.querySelectorAll('.settings-section')].pop();
        const before = { scroll: p.scrollHeight, client: p.clientHeight, bottom: p.getBoundingClientRect().bottom, vh: innerHeight, lastBefore: last.getBoundingClientRect().bottom };
        p.scrollTop = p.scrollHeight;
        const lr = last.getBoundingClientRect(), pr = p.getBoundingClientRect();
        const pr0 = p.getBoundingClientRect(), fields = [...p.querySelectorAll('input, button')].filter(e => e.getBoundingClientRect().width);
        before.horizontal = { l: pr0.left, r: pr0.right, vw: innerWidth, fieldsLeft: Math.min(...fields.map(e => e.getBoundingClientRect().left)), fieldsRight: Math.max(...fields.map(e => e.getBoundingClientRect().right)) };
        return { ...before, scrolled: p.scrollTop, lastId: last.id, lastBottom: lr.bottom, panelBottom: pr.bottom, lastInPanel: lr.bottom <= pr.bottom + 1 }; })()`);
      o.settingsAppearance = await evalIn(`document.querySelectorAll('#settings-panel .theme-opt, #settings-panel #accent-input, #settings-panel .swatch, #settings-themes').length`);
      o.settingsHeadings = await evalIn(`[...document.querySelectorAll('#settings-panel h4')].map(e => e.textContent)`);
      await evalIn('document.getElementById("settings-panel").hidden = true; 0');
      // Theme submenu.
      await evalIn('document.getElementById("me-btn").click(); 0');
      o.closedBefore = await evalIn('document.getElementById("me-themes").hidden');
      await evalIn('document.getElementById("me-theme-btn").click(); 0');
      await until('!document.getElementById("me-themes").hidden');
      o.sub = await evalIn(`(() => { const l = document.getElementById('me-themes'), R = ${RECT}; const lr = R(l), mr = R(document.getElementById('me-menu'));
        return { names: [...l.querySelectorAll('.theme-opt')].map(b => b.dataset.appearance),
                 labels: [...l.querySelectorAll('.theme-opt .theme-name')].map(b => b.textContent),
                 swatches: [...l.querySelectorAll('.theme-opt')].every(b => { const s = b.querySelector('.theme-sw'); return s && s.getBoundingClientRect().width > 0 && !!s.querySelector('i'); }),
                 ticked: [...l.querySelectorAll('.theme-opt[aria-checked=true]')].map(b => b.dataset.appearance),
                 expanded: document.getElementById('me-theme-btn').getAttribute('aria-expanded'),
                 onScreen: lr.l >= 0 && lr.r <= innerWidth && lr.t >= 0 && lr.b <= innerHeight, listRect: lr, menuRect: mr,
                 inside: lr.l >= mr.l - 1 && lr.r <= mr.r + 1, vw: innerWidth, vh: innerHeight }; })()`);
      // Every account action stays reachable with Theme open, however short the screen.
      o.menuReach = await evalIn(`(() => { const m = document.getElementById('me-menu'); m.scrollTop = m.scrollHeight;
        const items = [...m.querySelectorAll('.me-item, .theme-opt')].filter(e => e.getBoundingClientRect().height);
        const last = m.querySelector('[data-me=settings]').getBoundingClientRect(), sys = m.querySelector('.theme-opt[data-appearance=system]').getBoundingClientRect();
        const mr = m.getBoundingClientRect(); m.scrollTop = 0;
        return { settingsBottom: last.bottom, sysBottom: sys.bottom, menuBottom: mr.bottom, vh: innerHeight, n: items.length }; })()`);
      // Esc folds Theme and nothing under it: an open task stays open.
      o.esc = await evalIn(`(() => { window.__closed = 0; const was = window.closeTask; window.closeTask = () => { window.__closed++; };
        SELECTED_SID = 'x'; const f = document.querySelector('#me-themes .theme-opt'); f.focus();
        f.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
        const r = { folded: document.getElementById('me-themes').hidden, closed: window.__closed, menuOpen: !document.getElementById('me-menu').hidden };
        SELECTED_SID = null; window.closeTask = was; return r; })()`);
      await evalIn('document.getElementById("me-theme-btn").click(); 0');
      // Choose one: at once on the page, then on the hub.
      await evalIn('document.querySelector("#me-themes .theme-opt[data-appearance=intellij-dark]").click(); 0');
      o.applied = await evalIn('document.documentElement.dataset.theme');
      o.ground = await evalIn(`getComputedStyle(document.documentElement).getPropertyValue('--bg').trim().toUpperCase()`);
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.theme === 'intellij-dark')`);
      o.saved = await evalIn(`fetch('/api/settings').then(r => r.json()).then(s => s.theme)`);
      o.menuClosed = await evalIn('document.getElementById("me-menu").hidden');
      await evalIn('document.getElementById("me-btn").click(); document.getElementById("me-theme-btn").click(); 0');
      o.tickedAfter = await evalIn(`[...document.querySelectorAll('#me-themes .theme-opt[aria-checked=true]')].map(b => b.dataset.appearance)`);
      o.cur = await evalIn('document.getElementById("me-theme-cur").textContent');
      // Back to light for the next width (the hub keeps the choice).
      await evalIn(`document.querySelector('#me-themes .theme-opt[data-appearance=light]').click(); 0`);
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.theme === 'light')`);
      // Accent uses the same component; opening it folds Theme.
      await evalIn('document.getElementById("me-btn").click(); document.getElementById("me-theme-btn").click(); 0');
      o.accentClosedBefore = await evalIn('document.getElementById("me-accents").hidden');
      await evalIn('document.getElementById("me-accent-btn").click(); 0');
      o.accent = await evalIn(`(() => { const l = document.getElementById('me-accents'), R = ${RECT}; const lr = R(l), mr = R(document.getElementById('me-menu'));
        const opts = [...l.querySelectorAll('.theme-opt')];
        return { colors: opts.map(b => b.dataset.color), labels: opts.map(b => b.querySelector('.theme-name').textContent),
          swatches: opts.every(b => b.querySelector('.theme-sw').getBoundingClientRect().width === 18),
          ticked: opts.filter(b => b.getAttribute('aria-checked') === 'true').map(b => b.dataset.color),
          themeFolded: document.getElementById('me-themes').hidden, expanded: document.getElementById('me-accent-btn').getAttribute('aria-expanded'),
          inside: lr.l >= mr.l - 1 && lr.r <= mr.r + 1, onScreen: lr.l >= 0 && lr.r <= innerWidth && lr.t >= 0 && lr.b <= innerHeight,
          minTarget: Math.min(...opts.map(b => b.getBoundingClientRect().height)), position: getComputedStyle(l).position };
      })()`);
      o.accentReach = await evalIn(`(() => { const m = document.getElementById('me-menu'), l = document.getElementById('me-accents');
        l.scrollTop = l.scrollHeight; m.scrollTop = m.scrollHeight;
        const input = document.getElementById('accent-input').getBoundingClientRect(), settings = m.querySelector('[data-me=settings]').getBoundingClientRect();
        const result = { inputBottom: input.bottom, settingsBottom: settings.bottom, vh: innerHeight };
        l.scrollTop = 0; m.scrollTop = 0; return result; })()`);
      // Keyboard fold returns focus, does not close the task, and reopens with Right.
      o.accentKeys = await evalIn(`(() => { let closed = 0; const was = window.closeTask; window.closeTask = () => closed++;
        SELECTED_SID = 'x'; const b = document.getElementById('me-accent-btn'), first = document.querySelector('#me-accents .theme-opt'); first.focus();
        first.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}));
        const folded = document.getElementById('me-accents').hidden && document.activeElement === b;
        b.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}));
        const focused = document.activeElement === first;
        first.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true, cancelable: true}));
        const left = document.getElementById('me-accents').hidden && document.activeElement === b;
        b.click(); SELECTED_SID = null; window.closeTask = was; return {folded, focused, left, closed}; })()`);
      await evalIn(`document.querySelector('#me-accents [data-color="#6E5DC6"]').click(); 0`);
      o.accentApplied = await evalIn(`document.documentElement.style.getPropertyValue('--accent')`);
      o.accentMenuClosed = await evalIn('document.getElementById("me-menu").hidden');
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.accent === '#6E5DC6')`);
      await evalIn('document.getElementById("me-btn").click(); document.getElementById("me-accent-btn").click(); 0');
      o.accentTicked = await evalIn(`[...document.querySelectorAll('#me-accents [aria-checked=true]')].map(e => e.dataset.color)`);
      o.accentCur = await evalIn('document.getElementById("me-accent-cur").textContent');
      // The default swatch follows the theme even with a custom accent applied.
      o.defaultSwatches = await evalIn(`(() => { const result = [];
        for (const t of cdTheme.themes) { localStorage.setItem('cd-theme', t); cdTheme.apply();
          result.push({ theme: t, color: document.querySelector('#me-accents [data-color=""] .theme-sw').style.background, expected: THEME_INFO[t][2] }); }
        localStorage.setItem('cd-theme', 'light'); cdTheme.apply(); return result; })()`);
      // Preserve custom input and its tick, including after it is chosen on another device.
      await evalIn(`const inp = document.getElementById('accent-input'); inp.value = 'rebeccapurple'; inp.dispatchEvent(new Event('input', {bubbles: true})); 0`);
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.accent === 'rebeccapurple')`);
      o.customTicked = await evalIn(`[...document.querySelectorAll('#me-accents [aria-checked=true]')].map(e => e.dataset.color)`);
      // New presets need no separate menu edit.
      o.futureAccent = await evalIn(`(() => { ACCENT_CHOICES.push(['#123456', 'Future']); renderAccentList();
        const found = !!document.querySelector('#me-accents [data-color="#123456"] .theme-sw');
        ACCENT_CHOICES.pop(); renderAccentList(); return found; })()`);
      await evalIn(`document.querySelector('#me-accents [data-color=""]').click(); 0`);
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.accent === 'default')`);
      o.defaultApplied = await evalIn(`document.documentElement.style.getPropertyValue('--accent') === '' && !localStorage.getItem('cd-accent')`);
      await evalIn('document.getElementById("me-btn").click(); document.getElementById("me-accent-btn").click(); 0');
      o.defaultTicked = await evalIn(`[...document.querySelectorAll('#me-accents [aria-checked=true]')].map(e => e.dataset.color)`);
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class SettingsScrollsAndThemeSubmenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-st-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "root").mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "root"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "shots": "",
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "settings_themes_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_settings_scrolls_to_its_last_section(self):
        for name, g in self.got.items():
            s = g["settings"]
            with self.subTest(name):
                self.assertGreaterEqual(s["scroll"], s["client"])
                self.assertLessEqual(s["bottom"], s["vh"], "the panel stays on screen")
                if s["scroll"] > s["client"]:
                    self.assertGreater(s["scrolled"], 0, "it scrolls when needed")
                self.assertTrue(s["lastInPanel"], s)
                self.assertLessEqual(s["lastBottom"], s["vh"], "the last section is on screen")
                self.assertEqual(s["lastId"], "settings-runtime")
                h = s["horizontal"]
                self.assertGreaterEqual(h["l"], 0, "no part of the panel is left of the screen")
                self.assertLessEqual(h["r"], h["vw"])
                self.assertGreaterEqual(h["fieldsLeft"], 0)
                self.assertLessEqual(h["fieldsRight"], h["vw"])

    def test_theme_submenu_lists_every_allowed_theme(self):
        allowed = sorted(dashboard._SETTINGS_ALLOWED_VALUES["theme"] - {""})
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertTrue(g["closedBefore"], "folded until Theme is chosen")
                self.assertEqual(sorted(g["sub"]["names"]), allowed)
                self.assertEqual(g["settingsAppearance"], 0)
                self.assertFalse(any(h in {"Theme", "Accent", "Accent color", "Accent colour"} for h in g["settingsHeadings"]))
                self.assertEqual(len(set(g["sub"]["labels"])), len(allowed))
                self.assertTrue(g["sub"]["swatches"])
                self.assertEqual(g["sub"]["ticked"], ["light"], "the current theme is ticked")
                self.assertEqual(g["sub"]["expanded"], "true")
                if name != "short":     # a short screen scrolls the menu instead (test below)
                    self.assertTrue(g["sub"]["onScreen"], g["sub"])
        self.assertTrue(self.got["phone"]["sub"]["inside"] and self.got["short"]["sub"]["inside"], "on a phone the list is inside the menu")

    def test_theme_menu_stays_reachable_and_escape_only_folds_it(self):
        for name, g in self.got.items():
            with self.subTest(name):
                r = g["menuReach"]
                self.assertLessEqual(r["settingsBottom"], r["vh"], "Settings, the last action, can be scrolled to")
                self.assertLessEqual(r["menuBottom"], r["vh"])
                e = g["esc"]
                self.assertTrue(e["folded"])
                self.assertEqual(e["closed"], 0, "Esc did not also close the task")
                self.assertTrue(e["menuOpen"], "the menu stays until asked")

    def test_choosing_a_theme_applies_and_saves_it(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertEqual(g["applied"], "intellij-dark")
                self.assertEqual(g["ground"], "#1E1F22")
                self.assertEqual(g["saved"], "intellij-dark")
                self.assertTrue(g["menuClosed"])
                self.assertEqual(g["tickedAfter"], ["intellij-dark"])
                self.assertEqual(g["cur"], "IntelliJ Dark")

    def test_accent_submenu_choices_layout_and_keyboard(self):
        colors = ["", "#0055CC", "#1F845A", "#1D7AFC", "#6E5DC6", "#943D73", "#C25D3C", "#44546F"]
        for name, g in self.got.items():
            with self.subTest(name):
                a = g["accent"]
                self.assertTrue(g["accentClosedBefore"])
                self.assertEqual(a["colors"], colors)
                self.assertEqual(len(set(a["labels"])), len(colors))
                self.assertTrue(a["swatches"])
                self.assertEqual(a["ticked"], [""])
                self.assertEqual(a["expanded"], "true")
                self.assertTrue(a["themeFolded"])
                if name.startswith("phone") or name == "short":
                    self.assertTrue(a["inside"])
                    self.assertEqual(a["position"], "static")
                    self.assertGreaterEqual(a["minTarget"], 44)
                else:
                    self.assertTrue(a["onScreen"], a)
                for bottom in ("inputBottom", "settingsBottom"):
                    self.assertLessEqual(g["accentReach"][bottom], g["accentReach"]["vh"])
                self.assertEqual(g["accentKeys"], {"folded": True, "focused": True, "left": True, "closed": 0})
                self.assertTrue(g["futureAccent"])

    def test_accent_applies_saves_and_resets_to_theme_default(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertEqual(g["accentApplied"], "#6E5DC6")
                self.assertTrue(g["accentMenuClosed"])
                self.assertEqual(g["accentTicked"], ["#6E5DC6"])
                self.assertEqual(g["accentCur"], "Violet")
                self.assertEqual(g["customTicked"], ["rebeccapurple"])
                self.assertTrue(g["defaultApplied"])
                self.assertEqual(g["defaultTicked"], [""])
                for sw in g["defaultSwatches"]:
                    hex_color = sw["expected"].lstrip("#")
                    rgb = ", ".join(str(int(hex_color[i:i + 2], 16)) for i in (0, 2, 4))
                    self.assertEqual(sw["color"], f"rgb({rgb})", sw["theme"])


if __name__ == "__main__":
    unittest.main()
