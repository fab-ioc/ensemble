"""P215 in a real page: Settings is a dialog with a tab per section on the left.

In headless Chrome over CDP, against a hub in a thread, at 1728x1117,
1440x900 and a phone's 390x844 and 360x740:

* the avatar menu's Settings opens a dialog over the page (centred on a
  desktop, the whole screen on a phone) with one tab per .settings-section;
* each tab shows its section alone, the page scrolls inside itself while the
  tab list stays where it is, nothing runs past the dialog or the screen;
* a phone shows the list first, a tap opens a page with Back to the list;
* Up/Down move along the tabs, Enter opens one, Tab stays in the dialog,
  Esc closes it and the focus goes back to the avatar;
* every way in (openSettings, #settings-<tab> in the address, a link to it,
  data-settings-tab) opens on its tab, and the Disk space preview opens above;
* the selected tab reads in every theme (4.5:1).

Without a browser, the source is checked: every section has a tab key and
every caller names a section that exists.

Screenshots of each tab at 1728 and 390 go to $ENSEMBLE_SHOTS when it is set.
Skipped (the browser part) without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import re
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

PAGES = [ROOT / "index.html", ROOT / "session.html", ROOT / "fileview.html", *sorted((ROOT / "static").glob("*.js"))]


def sections() -> list[str]:
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    return re.findall(r'class="settings-section" id="settings-([a-z]+)"', src)


def callers() -> list[tuple[str, str]]:
    """(file, tab) for every place that opens Settings on a named tab."""
    out = []
    pats = [r'href="#settings-([a-z]+)"', r'data-settings-tab="([a-z]+)"',
            r"openSettings\('(?:settings-)?([a-z]+)'", r"openSettingsPanel\([^,]*,\s*'(?:settings-)?([a-z]+)'",
            r"#settings-([a-z]+)\b(?![-\w])"]
    for p in PAGES:
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        for pat in pats:
            for m in re.finditer(pat, src):
                out.append((p.name, m.group(1)))
    return out


class SettingsTabsCoverEverySection(unittest.TestCase):
    def test_every_section_has_a_tab_and_every_caller_a_section(self):
        secs = sections()
        self.assertGreaterEqual(len(secs), 8)
        self.assertEqual(len(secs), len(set(secs)), "a section id is used twice")
        src = (ROOT / "index.html").read_text(encoding="utf-8")
        block = src[src.index("const SP_ICONS = {"):src.index("};", src.index("const SP_ICONS = {"))]
        # Tabs are built from #sp-pages' sections: one outside it gets no tab.
        pages = src[src.index('id="sp-pages"'):src.index("<main>")]
        self.assertEqual(re.findall(r'class="settings-section" id="settings-([a-z]+)"', pages), secs,
                         "a section outside #sp-pages has no tab")
        icons = set(re.findall(r"^\s*([a-z]+):", block, re.M))
        self.assertEqual(sorted(set(secs) - icons), [], "a section without its tab's icon")
        # #settings-panel is the dialog itself; any other #settings-x names a tab.
        bad = [(f, t) for f, t in callers() if t not in secs and t != "panel"]
        self.assertEqual(bad, [], "a caller names a section with no tab")
        self.assertIn(("index.html", "models"), callers(), "the usage tray's link to Agent models")


CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const key = async (k, code, vk, shift) => {
      const base = { key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: shift ? 8 : 0 };
      await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...base }, sessionId);
      if (k === 'Enter') await c.send('Input.dispatchKeyEvent', { type: 'char', text: '\r', ...base }, sessionId);
      await c.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base }, sessionId);
      await sleep(60);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!PREFS', 30000);
    return { evalIn, until, key, shot, targetId };
  };
  // What shows: the open page, the list, the dialog's box and anything cut off.
  const STATE = `(() => {
    const p = document.getElementById('settings-panel'), d = p.querySelector('.sp-dlg'), nav = document.getElementById('sp-nav'), pages = document.getElementById('sp-pages');
    const R = e => { const r = e.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) }; };
    const vis = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
    const shown = [...pages.querySelectorAll(':scope > .settings-section')].filter(vis).map(s => s.id);
    const dr = d.getBoundingClientRect();
    const cut = [...d.querySelectorAll('*')].filter(e => vis(e) && !e.closest('select, datalist, option')).filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > dr.right + 1 || r.left < dr.left - 1); }).map(e => e.tagName + '#' + e.id + '.' + e.className).slice(0, 5);
    const tabs = [...nav.querySelectorAll('.sp-tab')];
    return { open: !p.hidden, dlg: R(d), vw: innerWidth, vh: innerHeight, docScroll: document.documentElement.scrollWidth, pageScrollX: pages.scrollWidth - pages.clientWidth,
      nav: vis(nav) ? R(nav) : null, pages: vis(pages) ? R(pages) : null, shown, cut,
      tabs: tabs.map(b => b.dataset.tab), labels: tabs.map(b => b.textContent.replace('›', '').trim()),
      selected: (nav.querySelector('.sp-tab[aria-selected="true"]') || {}).dataset?.tab || '',
      sections: [...document.querySelectorAll('.settings-section')].map(s => s.id),
      back: vis(document.getElementById('sp-back')), title: document.getElementById('sp-title').textContent,
      focusIn: p.contains(document.activeElement), focus: document.activeElement && (document.activeElement.id || document.activeElement.className) };
  })()`;
  const SCROLL = `(() => { const pages = document.getElementById('sp-pages'), nav = document.getElementById('sp-nav');
    const sec = [...pages.querySelectorAll(':scope > .settings-section')].find(s => s.getClientRects().length);
    const before = nav.getClientRects().length ? nav.getBoundingClientRect().top : null;
    pages.scrollTop = pages.scrollHeight;
    const kids = [...sec.querySelectorAll('*')].filter(e => e.getClientRects().length);
    const lastB = Math.max(...kids.map(e => e.getBoundingClientRect().bottom));
    const r = { scrollH: pages.scrollHeight, clientH: pages.clientHeight, scrolled: pages.scrollTop, lastBottom: Math.round(lastB),
      pagesBottom: Math.round(pages.getBoundingClientRect().bottom), navTopBefore: before, navTopAfter: nav.getClientRects().length ? nav.getBoundingClientRect().top : null };
    pages.scrollTop = 0; return r; })()`;
  // A phone's controls on the shown page under 44 px either way (a checkbox's
  // target is its label).
  const TARGETS = `(() => { const sec = [...document.getElementById('sp-pages').querySelectorAll(':scope > .settings-section')].find(s => s.getClientRects().length);
    return [...sec.querySelectorAll('input, select, textarea, button')].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
      .map(e => { const t = /^(checkbox|radio)$/.test(e.type) ? (e.closest('label') || e) : e, r = t.getBoundingClientRect();
        return { id: e.id || e.textContent.trim().slice(0, 20) || e.className, w: Math.round(r.width), h: Math.round(r.height) }; })
      .filter(x => x.w < 44 || x.h < 44); })()`;
  // A canvas resolves any colour (color-mix computes to color(srgb …)) to bytes.
  const rgb = `const cx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
    const rgb = c => { cx.fillStyle = '#fff'; cx.fillRect(0, 0, 1, 1); cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); return [...cx.getImageData(0, 0, 1, 1).data].slice(0, 3); };
    const lum = ([r, g, b]) => { const f = v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const ratio = (a, b) => { const x = lum(rgb(a)), y = lum(rgb(b)); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };`;
  try {
    for (const [name, w, h, mobile] of [['d1728', 1728, 1117, false], ['d1440', 1440, 900, false], ['p390', 390, 844, true], ['p360', 360, 740, true]]) {
      const { evalIn, until, key, shot, targetId } = await page(w, h, mobile);
      const o = out[name] = { mobile };
      // From the avatar menu, as a person opens it.
      await evalIn('document.getElementById("me-btn").click(); 0');
      await evalIn('document.querySelector("#me-menu [data-me=settings]").click(); 0');
      await until('!document.getElementById("settings-panel").hidden');
      await sleep(150);
      o.opened = await evalIn(STATE);
      o.tabs = {};
      for (const tab of o.opened.tabs) {
        await evalIn(`document.querySelector('#sp-nav .sp-tab[data-tab="${tab}"]').click(); 0`);
        await sleep(80);
        const t = o.tabs[tab] = await evalIn(STATE);
        t.scroll = await evalIn(SCROLL);
        if (name === 'd1728' || name === 'p390') await shot(`settings-${w}-${tab}`);
        if (mobile) {
          t.small = await evalIn(TARGETS);
          await evalIn('document.getElementById("sp-back").click(); 0');
          t.backed = await evalIn(STATE);
        }
      }
      if (mobile) await shot(`settings-${w}-list`);
      // Keyboard: Up/Down move along the list, Enter opens, Tab stays inside.
      await evalIn(`document.querySelector('#sp-nav .sp-tab').focus(); 0`);
      await key('ArrowDown', 'ArrowDown', 40);
      o.keyDown = await evalIn(`document.activeElement.dataset.tab || ''`);
      await key('ArrowUp', 'ArrowUp', 38); await key('ArrowUp', 'ArrowUp', 38);
      o.keyWrap = await evalIn(`document.activeElement.dataset.tab || ''`);
      await key('Enter', 'Enter', 13);
      o.keyEnter = await evalIn(STATE);
      o.trap = [];
      for (let i = 0; i < 40; i++) { await key('Tab', 'Tab', 9, i >= 20); o.trap.push(await evalIn(`document.getElementById('settings-panel').contains(document.activeElement)`)); }
      // Global shortcuts do not fire from inside: / would focus search.
      await evalIn(`document.querySelector('#sp-nav .sp-tab').focus(); 0`);
      await key('/', 'Slash', 191);
      o.slashStays = await evalIn(`document.getElementById('settings-panel').contains(document.activeElement)`);
      await key('Escape', 'Escape', 27);
      o.esc = await evalIn(`({ hidden: document.getElementById('settings-panel').hidden, focus: document.activeElement.id })`);
      // Remembered per browser: it opens again on the tab last shown.
      o.remembered = await evalIn(`(() => { openSettings('backup'); closeSettings(); openSettings(); const s = document.querySelector('#sp-nav .sp-tab[aria-selected=true]').dataset.tab; closeSettings(); return { s, stored: localStorage.getItem('cd-settings-tab') }; })()`);
      // Deep links: every way in names its tab.
      o.links = {};
      o.links.api = await evalIn(`(() => { openSettings('prune'); const r = ${STATE}; closeSettings(); return r; })()`);
      o.links.id = await evalIn(`(() => { openSettingsPanel(document.getElementById('me-btn'), 'settings-models'); const r = ${STATE}; closeSettings(); return r; })()`);
      await evalIn(`location.hash = '#settings-runtime'; 0`);
      await until('!document.getElementById("settings-panel").hidden');
      o.links.hash = await evalIn(`(() => { const r = ${STATE}; r.hashLeft = location.hash; closeSettings(); return r; })()`);
      o.links.anchor = await evalIn(`(() => { const a = document.createElement('a'); a.href = '#settings-models'; a.textContent = 'x'; document.body.appendChild(a); a.click(); const r = ${STATE}; closeSettings(); r.focusBack = document.activeElement === a; a.remove(); return r; })()`);
      o.links.data = await evalIn(`(() => { const b = document.createElement('button'); b.dataset.settingsTab = 'feedback'; document.body.appendChild(b); b.click(); const r = ${STATE}; closeSettings(); b.remove(); return r; })()`);
      // The usage tray's "chosen in Settings" link (the plan chip's tray).
      o.links.usage = await evalIn(`(() => { const t = document.getElementById('usage-tray'); t.hidden = false;
        const box = document.createElement('div'); box.className = 'usage-why'; box.innerHTML = 'model x, chosen in <a href="#settings-models" class="usage-set">Settings › Agent models</a>'; t.appendChild(box);
        box.querySelector('a').click(); const r = ${STATE}; r.trayHidden = t.hidden; closeSettings(); box.remove(); return r; })()`);
      // A press on the dimmed page closes it.
      o.backdrop = await evalIn(`(() => { openSettings('app'); const p = document.getElementById('settings-panel');
        p.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); return p.hidden; })()`);
      if (name === 'd1440' || name === 'p390') {
        // Settings > Send feedback: closing Feedback (button or Esc) gives the
        // focus back to the avatar, not to the closed dialog.
        const fbOpen = `document.getElementById('feedback-title').closest('dialog').open`;
        o.feedback = {};
        for (const how of ['button', 'esc']) {
          await evalIn('document.getElementById("me-btn").click(); 0');
          await evalIn('document.querySelector("#me-menu [data-me=settings]").click(); 0');
          await evalIn(`openSettings('feedback'); document.getElementById('feedback-open').click(); 0`);
          await until(fbOpen);
          if (how === 'button') await evalIn(`document.getElementById('feedback-close').click(); 0`);
          else await key('Escape', 'Escape', 27);
          await sleep(100);
          o.feedback[how] = await evalIn(`({ open: ${fbOpen}, settings: !document.getElementById('settings-panel').hidden, focus: document.activeElement.id })`);
        }
        // A section hidden while Settings is open: its tab goes, the first
        // tab left is shown and focused; shown again, its tab comes back.
        await evalIn(`openSettings('backup'); 0`);
        await evalIn(`document.getElementById('settings-backup').style.display = 'none'; 0`);
        await sleep(50);
        o.hidden = await evalIn(STATE);
        await evalIn(`document.getElementById('settings-backup').style.display = ''; 0`);
        await sleep(50);
        o.unhidden = await evalIn(STATE);
        // A hidden first section is not the fallback either.
        o.firstHidden = await evalIn(`(() => { const a = document.getElementById('settings-app'); a.style.display = 'none';
          spShow('nope', true); const r = ${STATE}; a.style.display = ''; return r; })()`);
        await sleep(50);
        await evalIn('closeSettings(); 0');
      }
      // Disk space's preview opens above Settings.
      if (name === 'd1440') {
        await evalIn(`openSettings('prune'); document.getElementById('prune-preview-btn').click(); 0`);
        try {
          await until(`document.querySelector('#modal[open] .pr-dlg')`, 30000);
          o.prune = await evalIn(`(() => { const m = document.getElementById('modal'), r = m.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + 20);
            return { open: m.open, above: m.contains(top), settingsOpen: !document.getElementById('settings-panel').hidden }; })()`);
          await shot('settings-1440-prune-preview');
          await evalIn(`document.getElementById('modal').close(); 0`);
        } catch (e) { o.prune = { error: String(e).slice(0, 300) }; }
        await evalIn('closeSettings(); 0');
      }
      // The selected tab and a plain one read in every theme.
      if (name === 'd1728') {
        o.themes = await evalIn(`(() => { ${rgb} const res = []; openSettings('models');
          const sel = document.querySelector('#sp-nav .sp-tab[aria-selected=true]'), plain = document.querySelector('#sp-nav .sp-tab[aria-selected=false]'), nav = document.getElementById('sp-nav');
          for (const t of cdTheme.themes) { document.documentElement.dataset.theme = t;
            const cs = getComputedStyle(sel), cp = getComputedStyle(plain), cn = getComputedStyle(nav), h4 = getComputedStyle(document.querySelector('#settings-models h4')), sub = getComputedStyle(document.querySelector('#settings-models .sp-sub')), dlg = getComputedStyle(document.querySelector('#settings-panel .sp-dlg'));
            res.push({ theme: t, sel: ratio(cs.color, cs.backgroundColor), plain: ratio(cp.color, cn.backgroundColor), h4: ratio(h4.color, dlg.backgroundColor), sub: ratio(sub.color, dlg.backgroundColor) }); }
          document.documentElement.dataset.theme = 'light'; closeSettings(); return res; })()`);
        // Focus is visible on a tab.
        await evalIn(`openSettings('app'); 0`);
        await key('ArrowDown', 'ArrowDown', 40);
        o.focusRing = await evalIn(`getComputedStyle(document.activeElement).boxShadow`);
        await evalIn('closeSettings(); 0');
      }
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
class SettingsDialogInAPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-sd-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("root", "transcripts", "cs"):
            (base / d).mkdir()
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
            cls.addClassCleanup(p.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "shots": shots,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "settings_dialog_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=420)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if shots:
            (Path(shots) / "settings-dialog.json").write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_opens_as_a_dialog_with_a_tab_per_section(self):
        for name, g in self.got.items():
            s = g["opened"]
            with self.subTest(name):
                self.assertTrue(s["open"])
                self.assertEqual(s["tabs"], [x.replace("settings-", "") for x in s["sections"]], "a tab per section, in order")
                self.assertTrue(all(s["labels"]), s["labels"])
                self.assertTrue(s["focusIn"], "the focus moves into the dialog")
                d = s["dlg"]
                if g["mobile"]:
                    self.assertEqual((d["l"], d["t"], d["w"]), (0, 0, s["vw"]), "a phone's is the whole screen")
                    self.assertIsNotNone(s["nav"], "the list is the first screen")
                    self.assertIsNone(s["pages"])
                    self.assertFalse(s["back"])
                else:
                    self.assertEqual(d["w"], min(1100, round(s["vw"] * .92)))
                    self.assertEqual(d["h"], min(760, round(s["vh"] * .88)))
                    self.assertLessEqual(abs(d["l"] - (s["vw"] - d["r"])), 1, "centred")
                    self.assertEqual(len(s["shown"]), 1)

    def test_each_tab_shows_its_section_alone_and_scrolls_inside(self):
        for name, g in self.got.items():
            for tab, s in g["tabs"].items():
                with self.subTest(f"{name} {tab}"):
                    self.assertEqual(s["shown"], ["settings-" + tab])
                    self.assertEqual(s["selected"], tab)
                    self.assertLessEqual(s["docScroll"], s["vw"], "no sideways scroll")
                    self.assertLessEqual(s["pageScrollX"], 1, "the page does not scroll sideways")
                    self.assertEqual(s["cut"], [], "nothing runs past the dialog")
                    d = s["dlg"]
                    self.assertTrue(d["l"] >= 0 and d["r"] <= s["vw"] and d["t"] >= 0 and d["b"] <= s["vh"], d)
                    sc = s["scroll"]
                    self.assertLessEqual(sc["lastBottom"], sc["pagesBottom"] + 1, "the page's end can be scrolled to")
                    if sc["scrollH"] > sc["clientH"] + 1:
                        self.assertGreater(sc["scrolled"], 0)
                    if g["mobile"]:
                        self.assertIsNone(s["nav"], "a phone's page replaces the list")
                        self.assertTrue(s["back"])
                        self.assertTrue(s["title"] and s["title"] != "Settings")
                        self.assertIsNotNone(s["backed"]["nav"], "Back shows the list")
                        self.assertIsNone(s["backed"]["pages"])
                    else:
                        self.assertIsNotNone(s["nav"])
                        self.assertEqual(sc["navTopBefore"], sc["navTopAfter"], "the list stays put")
                        self.assertFalse(s["back"])

    def test_keyboard_trap_and_escape(self):
        for name, g in self.got.items():
            with self.subTest(name):
                tabs = g["opened"]["tabs"]
                self.assertEqual(g["keyDown"], tabs[1])
                self.assertEqual(g["keyWrap"], tabs[-1], "Up wraps to the last")
                self.assertEqual(g["keyEnter"]["selected"], tabs[-1], "Enter opens it")
                self.assertEqual(g["keyEnter"]["shown"], ["settings-" + tabs[-1]])
                self.assertTrue(all(g["trap"]), "Tab stays in the dialog")
                self.assertTrue(g["slashStays"], "the page's / shortcut does not fire")
                self.assertEqual(g["esc"], {"hidden": True, "focus": "me-btn"})
                self.assertEqual(g["remembered"], {"s": "backup", "stored": "backup"})
                self.assertTrue(g["backdrop"])

    def test_deep_links_open_their_tab(self):
        want = {"api": "prune", "id": "models", "hash": "runtime", "anchor": "models", "data": "feedback", "usage": "models"}
        for name, g in self.got.items():
            for how, tab in want.items():
                s = g["links"][how]
                with self.subTest(f"{name} {how}"):
                    self.assertTrue(s["open"])
                    self.assertEqual(s["selected"], tab)
                    self.assertEqual(s["shown"], ["settings-" + tab], "a link opens its page, on a phone too")
            self.assertEqual(g["links"]["hash"]["hashLeft"], "")
            self.assertTrue(g["links"]["anchor"]["focusBack"])
            self.assertTrue(g["links"]["usage"]["trayHidden"])

    def test_phone_controls_are_finger_sized(self):
        for name, g in self.got.items():
            if not g["mobile"]:
                continue
            for tab, s in g["tabs"].items():
                with self.subTest(f"{name} {tab}"):
                    self.assertEqual(s["small"], [])

    def test_feedback_gives_the_focus_back(self):
        for name in ("d1440", "p390"):
            for how, f in self.got[name]["feedback"].items():
                with self.subTest(f"{name} {how}"):
                    self.assertEqual(f, {"open": False, "settings": False, "focus": "me-btn"})

    def test_a_section_hidden_while_open(self):
        for name in ("d1440", "p390"):
            g = self.got[name]
            with self.subTest(name):
                h = g["hidden"]
                self.assertNotIn("backup", h["tabs"])
                self.assertEqual(h["selected"], h["tabs"][0])
                self.assertEqual(h["shown"], ["settings-" + h["tabs"][0]])
                self.assertTrue(h["focusIn"])
                self.assertIn("backup", g["unhidden"]["tabs"])
                f = g["firstHidden"]
                self.assertNotIn("app", f["tabs"])
                self.assertEqual(f["selected"], f["tabs"][0])
                self.assertEqual(f["shown"], ["settings-" + f["tabs"][0]])

    def test_prune_preview_opens_above(self):
        p = self.got["d1440"]["prune"]
        self.assertEqual(p, {"open": True, "above": True, "settingsOpen": True})

    def test_tabs_read_in_every_theme(self):
        g = self.got["d1728"]
        self.assertEqual(len(g["themes"]), 7)
        for t in g["themes"]:
            with self.subTest(t["theme"]):
                for k in ("sel", "plain", "h4", "sub"):
                    self.assertGreaterEqual(t[k], 4.5, (k, t))
        self.assertIn("0px 0px 0px 4px", g["focusRing"])


if __name__ == "__main__":
    unittest.main()
