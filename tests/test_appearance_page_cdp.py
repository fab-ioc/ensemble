"""#407 in a real page: Settings › Appearance, every look choice in one place.

In headless Chrome over CDP, against a hub in a thread, at desktop sizes and
at a phone's:

* the account menu has Appearance… (naming the theme and accent in effect),
  Settings, What's new, Send feedback…, About Ensemble; Appearance… and About
  Ensemble open their own pages; About's head line names the version;
* Appearance comes before Preferences; its theme tiles (every theme the hub allows
  and Match system, four a row) and accent swatches are radios, and the
  rows moved out of Preferences (Chat as a log, PO team activity, Project
  icons, Full names, Inbox highlight) are here, not there;
* a click previews at once, on the page and in a chat frame, and stores
  nothing: another tab and the hub keep the saved look; the bar shows only
  while the preview differs, and names what is previewed;
* Save writes every change to the hub in one go and the caches after it;
  Cancel, Esc and another tab drop the preview; a failed save keeps the
  draft and puts the accent back;
* a choice saved elsewhere reaches the page within 5 seconds (P434);
* Settings still scrolls to its last section;
* on a phone: four tiles a row, 44px swatches, the bar fixed at the bottom,
  computer-only rows saying so.

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
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return Date.now() - t || 1; await sleep(150); } throw new Error('timeout: ' + expr); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!PREFS && !!document.querySelector("#ap-themes .ap-tile")', 30000);
    const shot = async (file) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); require('fs').writeFileSync(A.shots + '/' + file, Buffer.from(r.data, 'base64')); };
    return { evalIn, until, targetId, shot };
  };
  const hub = `fetch('/api/settings', {cache: 'no-store'}).then(r => r.json())`;
  const put = body => `fetch('/api/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(${JSON.stringify(body)})}).then(r => r.ok)`;
  const RESET = { theme: 'light', accent: 'default', chatLog: 'off', poTeamActivity: 'shown', sendBox: 'workspace', projectIcons: 'flat', inboxHighlightSec: 3.5 };
  const sizes = [['desktop', 1280, 800, false], ['short', 1100, 420, false], ['phone', 390, 844, true], ['phone360', 360, 800, true]];
  try {
    for (const [name, w, h, mobile] of sizes) {
      const o = out[name] = {};
      const P0 = await page(1280, 800, false);
      await P0.evalIn(put(RESET));
      await P0.evalIn(`localStorage.clear(); 0`);
      await c.send('Target.closeTarget', { targetId: P0.targetId });
      const { evalIn, until, targetId, shot } = await page(w, h, mobile);
      // Settings: scrolled to its last section.
      await evalIn('document.getElementById("me-btn").click(); 0');
      await evalIn('[...document.querySelectorAll("#me-menu .me-item")].find(b => /^Settings/.test(b.textContent)).click(); 0');
      await until('!document.getElementById("settings-panel").hidden');
      o.settings = await evalIn(`(() => { const last = [...document.querySelectorAll('#sp-pages .settings-section')].pop();
        openSettings(last.id.replace(/^settings-/, ''));
        const p = document.getElementById('sp-pages'), dlg = document.querySelector('#settings-panel .sp-dlg');
        const before = { scroll: p.scrollHeight, client: p.clientHeight, bottom: p.getBoundingClientRect().bottom, vh: innerHeight };
        p.scrollTop = p.scrollHeight;
        const lr = last.getBoundingClientRect(), pr = p.getBoundingClientRect();
        const pr0 = dlg.getBoundingClientRect(), fields = [...dlg.querySelectorAll('input, button')].filter(e => e.getBoundingClientRect().width);
        before.horizontal = { l: pr0.left, r: pr0.right, vw: innerWidth, fieldsLeft: Math.min(...fields.map(e => e.getBoundingClientRect().left)), fieldsRight: Math.max(...fields.map(e => e.getBoundingClientRect().right)) };
        return { ...before, scrolled: p.scrollTop, lastId: last.id, lastBottom: lr.bottom, panelBottom: pr.bottom, lastInPanel: lr.bottom <= pr.bottom + 1 }; })()`);
      await evalIn('closeSettings(false); 0');
      // The account menu.
      await evalIn('document.getElementById("me-btn").click(); 0');
      o.menu = await evalIn(`[...document.querySelectorAll('#me-menu > *')].filter(e => e.getClientRects().length).map(e => e.classList.contains('me-rule') ? '—' : e.firstChild.textContent.trim())`);
      o.menuCur = await evalIn(`document.getElementById('me-appearance-cur').textContent`);
      o.flyouts = await evalIn(`document.querySelectorAll('#me-menu .me-sub, #me-menu .theme-opt, #me-themes, #me-accents').length`);
      await shot(name + '-menu.png');
      await evalIn(`document.querySelector('#me-menu [data-me="about"]').click(); 0`);
      o.about = await evalIn(`({ tab: SP.tab, open: !document.getElementById('settings-panel').hidden, head: document.querySelector('#settings-runtime .ab-head').textContent.trim(), ver: document.getElementById('about-ver').textContent, upd: document.getElementById('about-upd').hidden, h4: document.querySelector('#settings-runtime h4').textContent })`);
      await shot(name + '-about.png');
      await evalIn('closeSettings(false); document.getElementById("me-btn").click(); 0');
      await evalIn(`document.querySelector('#me-menu [data-me="appearance"]').click(); 0`);
      await until(`SP.tab === 'appearance' && !document.getElementById('settings-panel').hidden`);
      if (mobile) o.phoneTitle = await evalIn(`document.getElementById('sp-title').textContent`);
      await shot(name + '-page.png');
      o.tabs = await evalIn(`[...document.querySelectorAll('#sp-nav .sp-tab')].map(b => b.dataset.tab)`);
      o.sections = await evalIn(`[...document.querySelectorAll('#sp-pages > .settings-section')].map(s => s.id.replace('settings-', ''))`);
      o.moved = await evalIn(`['pref-chat-log', 'pref-po-team', 'pref-ptiles', 'pref-full-names', 'pref-inbox-hl', 'ap-send-box', 'accent-input'].map(id => { const e = document.getElementById(id); return { id, n: document.querySelectorAll('#' + id).length, in: e && e.closest('.settings-section').id }; })`);
      o.tiles = await evalIn(`(() => { const t = [...document.querySelectorAll('#ap-themes .ap-tile')];
        const tops = new Set(t.map(e => Math.round(e.getBoundingClientRect().top)));
        const firstRow = t.filter(e => Math.round(e.getBoundingClientRect().top) === Math.min(...tops)).length;
        return { names: t.map(e => e.dataset.theme), labels: t.map(e => e.textContent.replace('✓', '').trim()), role: t.map(e => e.getAttribute('role')),
          checked: t.filter(e => e.getAttribute('aria-checked') === 'true').map(e => e.dataset.theme), tabbable: t.filter(e => e.tabIndex === 0).map(e => e.dataset.theme), firstRow,
          sys: !!document.querySelector('#ap-themes [data-theme="system"] .ap-sys .ap-mc.right'),
          darkGround: getComputedStyle(document.querySelector('#ap-themes [data-theme="dark"] .ap-mc')).backgroundColor,
          fjordPill: getComputedStyle(document.querySelector('#ap-themes [data-theme="fjord"] .ap-mc .p')).backgroundColor }; })()`);
      o.accents = await evalIn(`(() => { const a = [...document.querySelectorAll('#ap-accents .ap-acc')];
        return { colors: a.map(e => e.dataset.color), checked: a.filter(e => e.getAttribute('aria-checked') === 'true').map(e => e.dataset.color),
          size: Math.round(a[1].querySelector('b').getBoundingClientRect().width), def: a[0].style.getPropertyValue('--c') }; })()`);
      o.barAtStart = await evalIn(`document.getElementById('ap-bar').hidden`);
      if (mobile) {
        o.phoneRows = await evalIn(`(() => { const pc = document.querySelector('#settings-appearance .ap-pc');
          return { co: getComputedStyle(pc.querySelector('.ap-co')).display, ctl: getComputedStyle(pc.querySelector('.ap-ctl')).display, coText: pc.querySelector('.ap-co').textContent }; })()`);
      }
      // A chat frame, which follows the preview.
      await evalIn(`(() => { const f = document.createElement('iframe'); f.id = 'ap-test-frame'; f.src = '/session?room=nope'; f.style.cssText = 'position:fixed;left:0;top:0;width:200px;height:120px'; document.body.append(f); return 0; })()`);
      await until(`(() => { const d = document.getElementById('ap-test-frame').contentDocument; return d && d.readyState === 'complete' && d.documentElement.dataset.theme === 'light'; })()`);
      const other = await page(1280, 800, false);
      // Preview: a tile.
      await evalIn(`document.querySelector('#ap-themes [data-theme="dark"]').click(); 0`);
      o.preview = await evalIn(`(async () => ({ page: document.documentElement.dataset.theme, frame: document.getElementById('ap-test-frame').contentDocument.documentElement.dataset.theme,
        cache: localStorage.getItem('cd-theme'), hub: (await ${hub}).theme, bar: !document.getElementById('ap-bar').hidden, what: document.getElementById('ap-what').textContent,
        checked: [...document.querySelectorAll('#ap-themes [aria-checked="true"]')].map(e => e.dataset.theme),
        def: document.querySelector('#ap-accents [data-color=""]').style.getPropertyValue('--c'), menuCur: document.getElementById('me-appearance-cur').textContent }))()`);
      o.otherDuring = await other.evalIn(`document.documentElement.dataset.theme`);
      if (mobile) o.phoneBar = await evalIn(`(() => { const b = document.getElementById('ap-bar'), r = b.getBoundingClientRect();
        return { pos: getComputedStyle(b).position, bottom: Math.round(r.bottom), vh: innerHeight, saved: getComputedStyle(b.querySelector('.ap-saved')).display }; })()`);
      await shot(name + '-preview.png');
      // Back to the saved choice: the bar goes.
      await evalIn(`document.querySelector('#ap-themes [data-theme="light"]').click(); 0`);
      o.backToSaved = await evalIn(`({ bar: document.getElementById('ap-bar').hidden, draft: Object.keys(AP.draft), preview: window.CD_PREVIEW })`);
      // Several choices, then Save: one write, caches after it.
      await evalIn(`document.querySelector('#ap-themes [data-theme="dark"]').click();
        document.querySelector('#ap-accents [data-color="#6E5DC6"]').click();
        document.querySelector('#pref-chat-log [data-val="on"]').click();
        document.querySelector('#pref-po-team [data-val="hidden"]').click();
        document.querySelector('#ap-send-box [data-val="pane"]').click();
        document.querySelector('#pref-ptiles [data-val="glossy"]').click();
        const fn = document.getElementById('pref-full-names'); fn.checked = false; fn.dispatchEvent(new Event('change', {bubbles: true}));
        const hl = document.getElementById('pref-inbox-hl'); hl.value = '5'; hl.dispatchEvent(new Event('input', {bubbles: true})); 0`);
      o.many = await evalIn(`({ draft: Object.keys(AP.draft).sort(), what: document.getElementById('ap-what').textContent,
        accent: document.documentElement.style.getPropertyValue('--accent'), accentCache: localStorage.getItem('cd-accent'),
        chatLogFrame: document.getElementById('ap-test-frame').contentDocument.documentElement.dataset.chatLog, chatLogCache: localStorage.getItem('cd-chat-log'),
        ptiles: document.documentElement.dataset.ptiles, ptilesCache: localStorage.getItem('cd-ptiles'), sendBox: document.documentElement.dataset.sendBox,
        names: ROW_EXPAND_NAMES, namesCache: localStorage.getItem('cd-switcher-full-names'), hl: inboxHlSec(), hlSaved: inboxHlSec(true) })`);
      o.otherMany = await other.evalIn(`({ theme: document.documentElement.dataset.theme, accent: document.documentElement.style.getPropertyValue('--accent'), ptiles: document.documentElement.dataset.ptiles })`);
      await evalIn(`document.getElementById('ap-save').click(); 0`);
      await until(`!AP.saving && !Object.keys(AP.draft).length`);
      o.saved = await evalIn(`(async () => { const s = await ${hub}; return { hub: { theme: s.theme, accent: s.accent, chatLog: s.chatLog, poTeamActivity: s.poTeamActivity, sendBox: s.sendBox, projectIcons: s.projectIcons, inboxHighlightSec: s.inboxHighlightSec },
        caches: { theme: localStorage.getItem('cd-theme'), accent: localStorage.getItem('cd-accent'), chatLog: localStorage.getItem('cd-chat-log'), poTeam: localStorage.getItem('cd-po-team'), sendBox: localStorage.getItem('cd-send-box'), ptiles: localStorage.getItem('cd-ptiles'), names: localStorage.getItem('cd-switcher-full-names') },
        page: document.documentElement.dataset.theme, preview: window.CD_PREVIEW, bar: document.getElementById('ap-bar').hidden, menuCur: document.getElementById('me-appearance-cur').textContent }; })()`);
      o.otherAfter = await other.until(`document.documentElement.dataset.theme === 'dark' && document.documentElement.style.getPropertyValue('--accent') === '#6E5DC6'`, 8000);
      // Cancel and Esc put back what is saved; another tab drops the preview.
      await evalIn(`document.querySelector('#ap-accents [data-color="#1F845A"]').click(); 0`);
      o.cancelBefore = await evalIn(`document.documentElement.style.getPropertyValue('--accent')`);
      await evalIn(`document.getElementById('ap-cancel').click(); 0`);
      o.cancel = await evalIn(`({ accent: document.documentElement.style.getPropertyValue('--accent'), bar: document.getElementById('ap-bar').hidden, draft: Object.keys(AP.draft) })`);
      await evalIn(`document.querySelector('#pref-ptiles [data-val="flat"]').click(); 0`);
      o.escBefore = await evalIn(`document.documentElement.dataset.ptiles`);
      await evalIn(`document.querySelector('#ap-themes .ap-tile').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true})); 0`);
      o.esc = await evalIn(`({ ptiles: document.documentElement.dataset.ptiles, closed: document.getElementById('settings-panel').hidden, draft: Object.keys(AP.draft) })`);
      await evalIn(`openSettings('appearance'); document.querySelector('#ap-themes [data-theme="dim"]').click(); 0`);
      o.tabBefore = await evalIn(`document.documentElement.dataset.theme`);
      await evalIn(`spShow('prefs', true); 0`);
      o.tabAway = await evalIn(`({ theme: document.documentElement.dataset.theme, bar: document.getElementById('ap-bar').hidden, draft: Object.keys(AP.draft) })`);
      await evalIn(`spShow('appearance', true); 0`);
      // Arrow keys move the choice.
      o.keys = await evalIn(`(() => { const t = document.querySelector('#ap-themes [aria-checked="true"]'); t.focus();
        t.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true, cancelable: true}));
        const r = { checked: document.querySelector('#ap-themes [aria-checked="true"]').dataset.theme, focused: document.activeElement.dataset.theme };
        apCancel(); return r; })()`);
      // A custom colour, typed.
      await evalIn(`const inp = document.getElementById('accent-input'); inp.focus(); inp.value = 'rebeccapurple'; inp.dispatchEvent(new Event('input', {bubbles: true})); 0`);
      o.custom = await evalIn(`({ accent: document.documentElement.style.getPropertyValue('--accent'), checked: [...document.querySelectorAll('#ap-accents [aria-checked="true"]')].map(e => e.dataset.color + ':' + e.textContent.trim()), cache: localStorage.getItem('cd-accent') })`);
      // A save that fails keeps the draft and puts the accent back.
      await evalIn(`window.__fetch = window.fetch; window.fetch = (u, o) => o && o.method === 'PUT' ? Promise.resolve(new Response('{}', {status: 500})) : window.__fetch(u, o);
        document.getElementById('ap-save').click(); 0`);
      await until(`!AP.saving`);
      await sleep(200);
      o.failed = await evalIn(`({ draft: Object.keys(AP.draft), cache: localStorage.getItem('cd-accent'), accent: document.documentElement.style.getPropertyValue('--accent'), bar: !document.getElementById('ap-bar').hidden, toast: [...document.querySelectorAll('.toast, #toast')].map(e => e.textContent).join(' ') })`);
      await evalIn(`window.fetch = window.__fetch; document.getElementById('ap-save').click(); 0`);
      await until(`!AP.saving && !Object.keys(AP.draft).length`);
      o.customSaved = (await evalIn(hub)).accent;
      // P434: a choice saved on another device shows here within 5 seconds
      // (a page in the background waits for its next look at the front).
      await c.send('Target.closeTarget', { targetId: other.targetId });
      await c.send('Target.activateTarget', { targetId });
      o.visible = await until(`!document.hidden`, 5000);
      await evalIn(put({ chatLog: 'off', projectIcons: 'flat' }));
      o.syncMs = await until(`PREFS.chatLog === 'off' && document.querySelector('#pref-chat-log [data-val="off"]').getAttribute('aria-pressed') === 'true' && document.documentElement.dataset.ptiles === 'flat' && localStorage.getItem('cd-chat-log') === 'off'`, 9000);
      await evalIn(put({ theme: 'paper' }));
      o.syncThemeMs = await until(`document.documentElement.dataset.theme === 'paper' && document.querySelector('#ap-themes [aria-checked="true"]').dataset.theme === 'paper'`, 9000);
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
class AppearancePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ap-", ignore_cleanup_errors=True)
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
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "shots": os.environ.get("AP_SHOTS", ""),
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}"}
        script = base / "appearance_cdp.js"
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

    def test_account_menu_rows_and_their_pages(self):
        for name, g in self.got.items():
            with self.subTest(name):
                want = ["Appearance…", "Settings", "—", "Send feedback…", "About Ensemble"]
                if name.startswith("phone"):
                    want.insert(3, "Plan allowance")
                self.assertEqual(g["menu"], want)
                self.assertEqual(g["menuCur"], "Light")
                self.assertEqual(g["flyouts"], 0)
                a = g["about"]
                self.assertEqual((a["tab"], a["open"], a["h4"]), ("runtime", True, "About"))
                self.assertTrue(a["ver"].startswith("Ensemble"), a)
                self.assertTrue(a["upd"], "no update, no tag")

    def test_appearance_comes_before_preferences_and_holds_the_moved_rows(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertLess(g["tabs"].index("appearance"), g["tabs"].index("prefs"))
                self.assertLess(g["sections"].index("appearance"), g["sections"].index("prefs"))
                for m in g["moved"]:
                    self.assertEqual((m["n"], m["in"]), (1, "settings-appearance"), m)
                if name.startswith("phone"):
                    self.assertEqual(g["phoneTitle"], "Appearance")

    def test_tiles_and_swatches(self):
        allowed = sorted(dashboard._SETTINGS_ALLOWED_VALUES["theme"] - {"", "system"})
        colors = ["", "#0055CC", "#1F845A", "#1D7AFC", "#6E5DC6", "#943D73", "#C25D3C", "#44546F"]
        for name, g in self.got.items():
            with self.subTest(name):
                t = g["tiles"]
                self.assertEqual(sorted(t["names"][:-1]), allowed)
                self.assertEqual(t["names"][-1], "system")
                self.assertEqual(len(set(t["labels"])), len(t["names"]))
                self.assertEqual(set(t["role"]), {"radio"})
                self.assertEqual(t["checked"], ["light"])
                self.assertEqual(t["tabbable"], ["light"])
                self.assertEqual(t["firstRow"], 4)
                self.assertTrue(t["sys"])
                self.assertEqual(t["darkGround"], "rgb(29, 33, 37)", "a tile is drawn in its theme's colours")
                self.assertNotEqual(t["fjordPill"], "rgb(12, 102, 228)", "and its own accent")
                a = g["accents"]
                self.assertEqual(a["colors"], colors)
                self.assertEqual(a["checked"], [""])
                self.assertEqual(a["def"], "#0C66E4", "Theme default is the shown theme's accent")
                self.assertEqual(a["size"], 44 if name.startswith("phone") else 28)
                self.assertTrue(g["barAtStart"])

    def test_preview_stores_nothing(self):
        for name, g in self.got.items():
            with self.subTest(name):
                p = g["preview"]
                self.assertEqual((p["page"], p["frame"]), ("dark", "dark"))
                self.assertEqual(p["cache"], None)
                self.assertIn(p["hub"], ("", "light"))
                self.assertEqual(g["otherDuring"], "light", "another tab keeps the saved look")
                self.assertTrue(p["bar"])
                self.assertIn("Previewing Dark", p["what"])
                self.assertIn("(saved: Light)", p["what"])
                self.assertEqual(p["checked"], ["dark"])
                self.assertEqual(p["def"], "#579DFF", "Theme default follows the previewed theme")
                b = g["backToSaved"]
                self.assertEqual((b["bar"], b["draft"], b["preview"]), (True, [], None))
                m = g["many"]
                self.assertEqual(m["draft"], sorted(["theme", "accent", "chatLog", "poTeamActivity", "sendBox", "projectIcons", "fullNames", "inboxHighlightSec"]))
                self.assertEqual(m["accent"], "#6E5DC6")
                self.assertEqual(m["chatLogFrame"], "on")
                self.assertEqual((m["ptiles"], m["sendBox"], m["names"], m["hl"], m["hlSaved"]), ("glossy", "pane", False, 5, 3.5))
                # The caches hold the saved look (or nothing), never the preview.
                self.assertIsNone(m["accentCache"])
                self.assertIn(m["chatLogCache"], (None, "off"))
                self.assertIn(m["namesCache"], (None, "true"))
                self.assertEqual(m["ptilesCache"], "flat")
                for word in ("Dark", "Violet", "Chat as a log on", "Team activity hidden", "A send box under each chat", "Glossy icons", "Full names on hover off", "Inbox highlight 5 s"):
                    self.assertIn(word, m["what"])
                self.assertEqual(g["otherMany"], {"theme": "light", "accent": "", "ptiles": "flat"})
                if name.startswith("phone"):
                    b = g["phoneBar"]
                    self.assertEqual((b["pos"], b["bottom"], b["saved"]), ("fixed", b["vh"], "none"))
                    r = g["phoneRows"]
                    self.assertEqual((r["co"] != "none", r["ctl"], r["coText"]), (True, "none", "Computer only"))

    def test_save_writes_the_hub_then_the_caches(self):
        for name, g in self.got.items():
            with self.subTest(name):
                s = g["saved"]
                self.assertEqual(s["hub"], {"theme": "dark", "accent": "#6E5DC6", "chatLog": "on", "poTeamActivity": "hidden",
                                            "sendBox": "pane", "projectIcons": "glossy", "inboxHighlightSec": 5})
                self.assertEqual(s["caches"], {"theme": "dark", "accent": "#6E5DC6", "chatLog": "on", "poTeam": "hidden",
                                               "sendBox": "pane", "ptiles": "glossy", "names": "false"})
                self.assertEqual((s["page"], s["preview"], s["bar"]), ("dark", None, True))
                self.assertEqual(s["menuCur"], "Dark · Violet")
                self.assertTrue(g["otherAfter"])

    def test_cancel_esc_and_another_page_drop_the_preview(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertEqual(g["cancelBefore"], "#1F845A")
                self.assertEqual(g["cancel"], {"accent": "#6E5DC6", "bar": True, "draft": []})
                self.assertEqual(g["escBefore"], "flat")
                self.assertEqual(g["esc"], {"ptiles": "glossy", "closed": True, "draft": []})
                self.assertEqual(g["tabBefore"], "dim")
                self.assertEqual(g["tabAway"], {"theme": "dark", "bar": True, "draft": []})
                self.assertEqual(g["keys"], {"checked": "dim", "focused": "dim"})

    def test_custom_colour_and_a_failed_save(self):
        for name, g in self.got.items():
            with self.subTest(name):
                c = g["custom"]
                self.assertEqual(c["accent"], "rebeccapurple")
                self.assertEqual(c["checked"], ["rebeccapurple:Custom"])
                self.assertEqual(c["cache"], "#6E5DC6")
                f = g["failed"]
                self.assertEqual(f["draft"], ["accent"])
                self.assertEqual(f["cache"], "#6E5DC6", "the saved accent's cache is put back")
                self.assertEqual(f["accent"], "rebeccapurple", "the preview stays")
                self.assertTrue(f["bar"])
                self.assertEqual(g["customSaved"], "rebeccapurple")

    def test_a_choice_saved_elsewhere_arrives_within_5_seconds(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertLess(g["syncMs"], 6500)
                self.assertLess(g["syncThemeMs"], 6500)


if __name__ == "__main__":
    unittest.main()
