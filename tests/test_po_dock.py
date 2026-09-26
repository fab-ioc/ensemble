"""A project's PO screen as panels on the Dock library (index.html, "The PO
screen as panels"; the library is vendored in static/dock).

Checked in Node, with the page's own functions and the library's layout model:

* the default layout: at about 1440 wide the PO chat and Points side by side,
  and the Board, Workspace and Changes on a strip at the right edge, none of
  them on screen; from 1800 wide the Board beside them too; on a phone one
  column with every panel a tab;
* the Points list: answers to acknowledge first, then what waits, oldest
  first; both ends linked by message id; Ack or Drop; an answer given by doing
  shows its summary; nothing listed says why;
* the roadmap's row, first in the Documents list.

Checked in headless Chrome over CDP, against a hub in a thread serving the
pages, with one project whose PO room holds points:

* the default layout on screen at 1440x900, the chat in its panel, the
  chat's own points line hidden while Points is shown;
* layout changes (another panel floated and back, a stack maximised, the
  Workspace and the PO chat themselves floated and back, Reset layout) keep
  the PO chat's page and an open Workspace file's page (no reload);
* F6 and Shift+F6 go between the stacks, also from inside the PO chat's
  composer (a frame), the arrow keys along a stack's tabs;
* a Points arrow scrolls the PO chat to that balloon and marks it;
* the roadmap opens from the Workspace's Documents list, in its own view;
* Reset layout brings the default back after a panel was hidden;
* Ack in Points acknowledges the point, in the panel and in the chat;
* a panel popped out into its own window (the Board, the PO chat) keeps its
  live updates, takes clicks and typing, follows the theme, and comes back
  when its window closes; the window's own chat is gone before the panel is
  back, and the chat here was never reloaded; the window's page is the
  library's popout.html as this hub serves it (inside the manifest's scope,
  so an installed app opens it as an app window, #101), and its base is this
  page's, so a relative link or frame there is this hub's;
* a resize to a phone's width turns the same dock narrow (one column of
  tabs), and back to the wide layout as it was, the saved layout unchanged;
* at 1400x900, 1800x1000 and a 390 px phone, in Light, Dark and High
  contrast: nothing wider than the screen, and the phone is one column of tabs.

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
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests.test_documents_project import INDEX, js_function  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
LAYOUT = (ROOT / "static" / "dock" / "src" / "layout.js").as_uri()


# ---- the page's functions in Node ------------------------------------------------

PURE_JS = r"""
const out = {};
%(fns)s
(async () => {
  const L = await import(%(layout)s);
  const lay = (w, phone) => {
    const cfg = L.makeConfig({ ids: PD_IDS, fill: 'po-chat', defaultLayout: ctx => pdDefaultLayout(L, { ...ctx, phone }) });
    const l = L.normalizeLayout(null, { cfg, viewportPx: w });
    const docked = L.panelsUnder(l.root);
    const front = [];
    (function walk(n) { if (!n) return; if (n.t === 'stack') front.push(n.active); else n.kids.forEach(walk); })(l.root);
    return { docked, front, auto: l.auto.map(a => [a.id, a.edge]), floats: l.floats.length, hidden: l.hidden.length,
             stacks: (function count(n) { return !n ? 0 : n.t === 'stack' ? 1 : n.kids.reduce((a, k) => a + count(k), 0); })(l.root),
             sizes: l.root.t === 'split' ? l.root.kids.map(k => k.size || null) : null };
  };
  out.at1440 = lay(1440, false);
  out.at1366 = lay(1366, false);
  out.at1920 = lay(1920, false);
  out.phone = lay(390, true);
  // A pinned strip panel goes back beside Points.
  {
    const cfg = L.makeConfig({ ids: PD_IDS, fill: 'po-chat', defaultLayout: ctx => pdDefaultLayout(L, ctx) });
    const l = L.normalizeLayout(null, { cfg, viewportPx: 1440 });
    L.pinPanel(l, 'board', { cfg, viewportPx: 1440 });
    out.pinned = L.panelsUnder(l.root);
  }
  const now = 1000000;
  const told = { room: 'room-po', words: { P1: 'make the board wider', P2: 'and the chat taller', P3: 'old one', P4: 'start the task' },
    points: { open: 1, answered: 2, items: [
      { id: 'P2', state: 'open', mid: 'room-po:m5', createdAt: now - 600, answers: [] },
      { id: 'P1', state: 'answered', mid: 'room-po:m1', createdAt: now - 7200, answers: [{ mid: 'room-po:m2' }] },
      { id: 'P3', state: 'acked', mid: 'room-po:m0', createdAt: now - 9000, answers: [{ mid: 'room-po:m1b' }] },
      { id: 'P4', state: 'answered', mid: 'room-po:m6', createdAt: now - 300, answers: [{ mid: '', summary: 'started as #75' }] },
    ] } };
  out.list = pdPointsHtml(told, now);
  out.none = pdPointsHtml({ room: 'room-po', words: {}, points: { open: 0, answered: 0, items: [told.points.items[2]] } }, now);
  out.before = pdPointsHtml(null, now);
  out.summary = pdPointsSummary(told.points);
  out.pinRead = wsDocsPinHtml({ exists: true, mtime: 1790000000 });
  out.pinNone = wsDocsPinHtml({ exists: false, mtime: 0 });
  out.pinUnknown = wsDocsPinHtml(null);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

PURE_FNS = ["esc", "PD_IDS", "PD_WIDE", "pdDefaultLayout", "PD_PT_WORD", "pdLastAnswer", "pdPtAge", "pdPointsSummary",
            "pdPointsHtml", "wsDocsDate", "wsDocsWhen", "wsDocsPinHtml"]


@unittest.skipUnless(NODE, "node is not installed")
class ThePanels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = PURE_JS % {"fns": "\n".join(js_function(n) for n in PURE_FNS), "layout": json.dumps(LAYOUT)}
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "po_dock.mjs"
            script.write_text(src, encoding="utf-8")
            r = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_laptop_opens_on_the_chat_and_points_the_rest_one_click_away(self):
        for key in ("at1440", "at1366"):
            a = self.o[key]
            self.assertEqual(a["docked"], ["po-chat", "points"], key)
            self.assertEqual(a["front"], ["po-chat", "points"], "side by side, both on screen")
            self.assertEqual(a["auto"], [["board", "right"], ["workspace", "right"], ["changes", "right"]],
                             "the others wait on the right edge's strip, slid in")
            self.assertEqual((a["floats"], a["hidden"]), (0, 0))
            self.assertEqual(a["sizes"], [None, 340], "the chat takes what Points leaves")

    def test_a_wide_screen_shows_the_board_too(self):
        a = self.o["at1920"]
        self.assertEqual(a["docked"], ["po-chat", "points", "board"])
        self.assertEqual(a["front"], ["po-chat", "points", "board"])
        self.assertEqual(a["auto"], [["workspace", "right"], ["changes", "right"]])

    def test_a_phone_is_one_column_of_tabs(self):
        a = self.o["phone"]
        self.assertEqual(a["stacks"], 1)
        self.assertEqual(a["docked"], ["po-chat", "points", "board", "workspace", "changes"])
        self.assertEqual(a["front"], ["po-chat"], "the chat first")
        self.assertEqual((a["auto"], a["floats"]), ([], 0), "nothing on a strip, nothing floating")

    def test_a_strip_panel_pinned_goes_beside_points(self):
        self.assertEqual(self.o["pinned"], ["po-chat", "points", "board"])

    def test_points_answers_first_then_waiting_oldest_first(self):
        html = self.o["list"]
        ids = [html.index(f'data-pt="{p}"') for p in ("P1", "P4", "P2")]
        self.assertEqual(ids, sorted(ids), "P1 then P4 (answered, oldest first), then P2 (waiting)")
        self.assertNotIn('data-pt="P3"', html, "an acknowledged point is not listed")
        self.assertIn("Your points: 1 waiting for an answer · 2 answered, not yet acknowledged", html)
        self.assertEqual(self.o["summary"], "1 waiting for an answer · 2 answered, not yet acknowledged")

    def test_each_point_links_both_ends_and_has_its_one_click(self):
        html = self.o["list"]
        self.assertIn('data-mid="room-po:m1" title="Go to your message in the PO chat">your message ↑</a>', html)
        self.assertIn('data-mid="room-po:m2" title="Go to the answer in the PO chat">answer ↓</a>', html)
        self.assertIn('href="/session?room=room-po&amp;msg=room-po:m2"', html, "a real link: it opens in a tab too")
        self.assertIn('data-pt="P1" data-pt-act="ack"', html)
        self.assertIn('data-pt="P2" data-pt-act="drop"', html)
        self.assertIn('<span class="pdp-said">started as #75</span>', html, "an answer given by doing shows what was done")
        self.assertIn('aria-label="Acknowledge the answer to P1: nothing is sent to the agent"', html)
        self.assertIn('<span class="pdp-age" data-at="992800">2h</span>', html)

    def test_no_points_and_not_yet_loaded_say_why(self):
        self.assertIn("No open points.", self.o["none"])
        self.assertIn("waits here for its answer", self.o["none"])
        self.assertIn("once it has loaded", self.o["before"])

    def test_the_roadmap_row_is_first_in_documents(self):
        self.assertIn('data-roadmap="1"', self.o["pinRead"])
        self.assertIn('<span class="wsd-t">Roadmap</span><span class="wsd-f">ROADMAP.md · always first</span>', self.o["pinRead"])
        self.assertIn('class="wsd-when" title="', self.o["pinRead"], "when it was saved, in full in its tooltip")
        self.assertIn("not written yet", self.o["pinNone"])
        self.assertIn('data-roadmap="1"', self.o["pinUnknown"])
        wsd = js_function("wsDocsHtml")
        self.assertEqual(wsd.count("${pin}"), 3, "loading, empty and listed: the roadmap first in each")
        self.assertNotIn('data-ptab="roadmap"', INDEX, "the Roadmap tab is gone")

    def test_the_library_is_vendored_and_served(self):
        self.assertTrue((ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8").startswith("fab-ioc/dock "))
        for rel in ("static/dock/src/index.js", "static/dock/src/dock.js", "static/dock/src/layout.js",
                    "static/dock/src/host.js", "static/dock/css/dock.css", "static/dock/src/popout-page.js",
                    "static/dock/src/theme-picker.js", "static/dock/src/install.js"):
            self.assertIn(rel, dashboard.PAGE_FILES)
            self.assertIn(f'href="/{rel}"', INDEX, f"the page lists {rel} for Page update")
        self.assertIn("import('/static/dock/src/index.js')", INDEX)
        self.assertNotIn("dock/css/theme.css", INDEX, "the --dk-* tokens read Ensemble's own")
        self.assertNotIn("static/dock/src/popout.html", dashboard.PAGE_FILES, "an inert page: nothing to update in it")
        self.assertRegex((ROOT / "static" / "dock" / "VERSION").read_text(encoding="utf-8"), r"^fab-ioc/dock v0\.3\.4 [0-9a-f]{40}")

    def test_the_library_does_what_the_workarounds_did(self):
        # Dock v0.3.3 has each of Ensemble's needs (the Dock project's ENSEMBLE-NEEDS.md); the page uses them.
        dock = INDEX[INDEX.index("// ---- The PO screen as panels: begin"):INDEX.index("// ---- The PO screen as panels: end")]
        for gone in ("<base href", "POP_HTML", "pdSyncChat", "pdSchedule", "pdOpenWindow", "document.write", "phoneOnly", "unscroll", "PD.phone",
                     "pd-phone", "popHtml", "ResizeObserver"):
            self.assertNotIn(gone, dock, gone)
        # #101: the served page, not a blob: one, which an installed app shows under Chrome's address strip.
        for used in ("popUrl: '/static/dock/src/popout.html',", "narrow: isPhone()", "narrowKey: PD_KEYS.phone", "PD.dock.setNarrow(isPhone())",
                     "PD.dock.onPopIn(", "parent.moveBefore(el"):
            self.assertIn(used, dock, used)
        css = INDEX[INDEX.index("/* ---- The PO screen as panels"):INDEX.index("</style>", INDEX.index("/* ---- The PO screen as panels"))]
        self.assertIn("body.po-dock #po-dock { position: absolute; inset: 0; background: var(--bg); }", css, "the dock is its own stacking context again")
        for gone in ("dk-popped", "body.pd-phone", "text-transform", "letter-spacing"):
            self.assertNotIn(gone, css, gone)
        for tok in ("--dk-font-chrome: var(--font-sans)", "--dk-fs-tab: var(--fs-200)", "--dk-tab-case: none",
                    "--dk-badge-border: 0", "--dk-radius: var(--r-300)", "--dk-float-shadow: var(--e-200)"):
            self.assertIn(tok, css, tok)
        for tok in ("--dk-bg: var(--bg)", "--dk-accent: var(--accent)", "--dk-focus: var(--focus-ring)", "--dk-fg3: var(--fg-muted)"):
            self.assertIn(tok, INDEX)


# ---- the page in headless Chrome -----------------------------------------------

CDP_JS = r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = fs.mkdtempSync(path.join(A.tmp, 'chrome-'));
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', '--window-size=1440,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {   // a real click (a window opens only from one)
      const [x, y] = await evalIn(`(() => { const r = (${expr}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    return { evalIn, until, click, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, theme) => p.evalIn(`(() => {
    try { localStorage.removeItem('cd-po-dock'); localStorage.removeItem('cd-po-dock-phone'); localStorage.setItem('cd-view', 'board'); } catch (e) {}
    VIEW_MODE = 'board'; SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
  const ready = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!PD.told && !!PD.told.points && (() => { const f = pdChatFrame(); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN")); })()', 30000);
  const rect = 'const R = el => { const b = el.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };';
  try {
    // ---- 1440 x 900: the default, the arrows, the roadmap, reset, Ack
    const p = await page(1440, 900);
    await go(p); await ready(p); await sleep(600);
    out.first = await p.evalIn(`(() => { ${rect}
      const f = pdChatFrame(), d = f.contentDocument;
      return { onScreen: PD_IDS.filter(id => { const w = PD.dock.frontOf(id); return w === id; }), strip: [...document.querySelectorAll('.dk-strip-btn')].map(b => b.dataset.dkAuto),
        fly: PD.dock.flyOpen(), chat: R(PD.els['po-chat']), po: R(PO_PANEL), poOff: PO_PANEL.classList.contains('pd-off'), inPanel: PO_PANEL.parentNode === PD.els['po-chat'],
        pointsLine: d.getElementById('points-line').hidden, elsewhere: f.hasAttribute('data-points-elsewhere'),
        rows: [...PD.els.points.querySelectorAll('.pdp-row')].map(r => r.dataset.pt), badge: (document.querySelector('[data-dk-tab="points"] .dk-badge') || {}).textContent || '',
        tabs: !!document.querySelector('.ptabs'), panels: !!document.querySelector('.pd-panels') };
    })()`);
    await p.shot('po-dock-1440x900');
    // An arrow in Points: the chat scrolls to that balloon and marks it.
    await p.evalIn('(() => { const b = pdChatFrame().contentDocument.getElementById("msgs"); b.scrollTop = b.scrollHeight; })(); 0');
    await sleep(300);
    out.arrow = await p.evalIn(`(async () => {
      const f = pdChatFrame(), w = f.contentWindow, box = f.contentDocument.getElementById('msgs');
      const a = PD.els.points.querySelector('.pdp-row[data-pt="P1"] a.pdp-link[title^="Go to the answer"]');
      const before = box.scrollTop; a.click();
      for (let i = 0; i < 40 && w.eval('GOTO'); i++) await new Promise(r => setTimeout(r, 100));
      await new Promise(r => setTimeout(r, 200));
      const el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === a.dataset.mid);
      const er = el.getBoundingClientRect(), br = box.getBoundingClientRect();
      return { moved: before - box.scrollTop, inView: er.top >= br.top - 1 && er.top < br.bottom, landed: el.classList.contains('landed'), text: el.innerText.slice(0, 400) };
    })()`);
    // The roadmap: first in the Workspace's Documents, opening in its own view.
    await p.evalIn('document.querySelector(\'.dk-strip-btn[data-dk-auto="workspace"]\').click(); 0');
    await p.until('!!PD.els.workspace.querySelector(".wsd-open[data-roadmap]") && PD.els.workspace.querySelectorAll(".wsd-row").length >= 1', 20000);
    out.docs = await p.evalIn(`(() => { const rows = [...PD.els.workspace.querySelectorAll('.wsd-row')]; return { first: rows[0].querySelector('.wsd-t').textContent, n: rows.length, flyScroll: PD_ROOT.scrollLeft + PD_HOST.scrollLeft }; })()`);
    await p.evalIn('PD.els.workspace.querySelector(".wsd-open[data-roadmap]").click(); 0');
    await p.until('(() => { const rm = document.getElementById("rm-panel"); return !rm.hidden && rm.closest(".pd-ws") && /Motors roadmap/.test(rm.innerText); })()', 15000);
    out.roadmap = await p.evalIn(`(() => { const rm = document.getElementById('rm-panel'); return { inWs: !!rm.closest('.pd-ws'), edit: !!rm.querySelector('button[data-rm="edit"]'), back: !!PD.els.workspace.querySelector('.wsd-back'), note: rm.querySelector('.rm-note').offsetHeight }; })()`);
    await p.shot('po-dock-roadmap');
    await p.evalIn('PD.els.workspace.querySelector(".wsd-back").click(); PD.dock.closeFly(); 0');
    // Reset layout, from the Panels menu: Points hidden, then the default again.
    await p.evalIn('document.querySelector(".pd-panels").click(); 0');
    await p.evalIn('document.querySelector(".pd-menu [data-pd-toggle=\\"points\\"]").click(); 0');
    await sleep(300);
    out.hidden = await p.evalIn('({ points: PD.dock.isVisible("points"), line: pdChatFrame().contentDocument.getElementById("points-line").hidden, saved: !!localStorage.getItem("cd-po-dock") })');
    await p.evalIn('document.querySelector(".pd-menu [data-pd-reset]").click(); 0');
    await sleep(400);
    out.reset = await p.evalIn('({ points: PD.dock.isVisible("points"), front: PD_IDS.filter(id => PD.dock.frontOf(id) === id), auto: PD.dock.layout().auto.map(a => a.id), line: pdChatFrame().contentDocument.getElementById("points-line").hidden, menu: !!document.querySelector(".pd-menu") })');
    // Ack in Points: the point is acknowledged, in the panel and in the chat.
    await p.evalIn('PD.els.points.querySelector(\'[data-pt="P1"][data-pt-act="ack"]\').click(); 0');
    await p.until('!PD.els.points.querySelector(\'.pdp-row[data-pt="P1"]\')', 10000);
    out.acked = await p.evalIn('({ rows: [...PD.els.points.querySelectorAll(".pdp-row")].map(r => r.dataset.pt), chat: pdChatFrame().contentWindow.eval("POINTS.items.find(p => p.id === \'P1\').state") })');

    // Layout changes keep every iframe's page: the PO chat's and an open file's.
    await p.evalIn('PD.dock.pin("workspace"); 0'); await sleep(300);
    await p.until('!!PD.els.workspace.querySelector(".wsp-tree .wse[data-path]")', 20000);
    await p.evalIn('[...PD.els.workspace.querySelectorAll(".wsp-tree .wse[data-path]")].find(x => x.dataset.path.endsWith("ROADMAP.md")).click(); 0');
    await p.until('(() => { const f = PD.els.workspace.querySelector("iframe.wsp-frame"); try { return !!f && f.contentDocument.readyState === "complete" && f.contentWindow.location.pathname === "/fileview"; } catch (e) { return false; } })()', 20000);
    out.kept = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const file = () => PD.els.workspace.querySelector('iframe.wsp-frame');
      file().contentWindow.__kept = 1; pdChatFrame().contentWindow.__kept = 1;
      const alive = () => { const f = file(), c = pdChatFrame(); return [!!(f && f.contentWindow && f.contentWindow.__kept), !!(c && c.contentWindow && c.contentWindow.__kept)]; };
      const r = { moveBefore: typeof Element.prototype.moveBefore === 'function' };
      PD.dock.float('points'); await sleep(300); r.floatOther = alive();
      PD.dock.dockBack('points'); await sleep(300); r.dockOther = alive();
      PD.dock.toggleMax('workspace'); await sleep(300); r.max = alive();
      PD.dock.restoreMax(); await sleep(300);
      PD.dock.float('workspace'); await sleep(300); r.floatFile = alive();
      PD.dock.dockBack('workspace'); await sleep(300); r.dockFile = alive();
      PD.dock.float('po-chat'); await sleep(300); r.floatChat = alive(); r.chatIn = PO_PANEL.parentNode === PD.els['po-chat'];
      PD.dock.dockBack('po-chat'); await sleep(300); r.dockChat = alive();
      PD.dock.reset(); await sleep(400); r.reset = alive();
      // The file closed again: the Documents list is what the Workspace shows next.
      const x = PD.els.workspace.querySelector('.wst-tab.on .wst-x'); if (x) x.click(); await sleep(200);
      return r;
    })()`);
    // Keys: F6 between the stacks, the arrows along a stack's tabs.
    out.keys = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const key = (k, shift) => document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: k, shiftKey: !!shift, bubbles: true, cancelable: true }));
      const at = () => document.activeElement && document.activeElement.dataset.dkTab;
      const r = {};
      document.querySelector('.dk-tab[data-dk-tab="po-chat"]').focus();
      key('F6'); r.f6 = at(); key('F6', true); r.back = at();
      // From the chat's composer, inside its frame: the frame's own key event reaches the dock.
      const inp = pdChatFrame().contentDocument.getElementById('input'), fw = pdChatFrame().contentWindow;
      inp.focus();
      inp.dispatchEvent(new fw.KeyboardEvent('keydown', { key: 'F6', bubbles: true, cancelable: true })); r.frameF6 = at();
      inp.focus();
      inp.dispatchEvent(new fw.KeyboardEvent('keydown', { key: 'F6', shiftKey: true, bubbles: true, cancelable: true })); r.frameBack = at();
      const stackOf = id => { let s = null; (function walk(n) { if (!n || s) return; if (n.t === 'stack') { if (n.panels.includes(id)) s = n; } else n.kids.forEach(walk); })(PD.dock.layout().root); return s; };
      PD.dock.moveTo('board', { kind: 'stack', stack: stackOf('points') }, 'center'); PD.dock.activate('points'); await sleep(300);
      document.querySelector('.dk-tab[data-dk-tab="points"]').focus();
      key('ArrowRight'); await sleep(100); r.right = at(); r.front = PD.dock.frontOf('board');
      r.roving = [...document.querySelectorAll('#po-dock .dk-main .dk-tab')].filter(t => t.tabIndex === 0).map(t => t.dataset.dkTab);
      PD.dock.reset(); await sleep(300);
      return r;
    })()`);

    // ---- Pop-out: the Board, then the PO chat
    await p.evalIn('PD.dock.pin("board"); 0'); await sleep(300);
    await p.click('PD.els.board.closest(".dk-stack").querySelector(\'[data-dk-act="pop"]\')');
    await p.until('!!PD.dock.popWindow("board") && PD.els.board.ownerDocument !== document && PD.els.board.querySelectorAll(".card").length > 0', 15000);
    out.popBoard = await p.evalIn(`(async () => {
      const w = PD.dock.popWindow('board'), d = w.document, r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)});
      const card = PD.els.board.querySelector('.card[data-sid="' + CSS.escape(r.sessionId) + '"]');
      const was = r.label; r.label = 'Renamed while out'; renderRows();
      const live = card.isConnected && card.ownerDocument === d && card.querySelector('.ctitle').textContent.includes('Renamed while out');
      r.label = was; renderRows();
      const count = (d.getElementById('viewcount') || {}).textContent || '';
      const theme = document.documentElement.dataset.theme; document.documentElement.dataset.theme = 'dark';
      await new Promise(r => setTimeout(r, 200)); const followed = d.documentElement.dataset.theme; document.documentElement.dataset.theme = theme;
      await new Promise(r => setTimeout(r, 200));
      d.querySelector('.viewsw button[data-view="list"]').click(); await new Promise(r => setTimeout(r, 200));
      const list = d.querySelectorAll('tr.row').length; d.querySelector('.viewsw button[data-view="board"]').click(); await new Promise(r => setTimeout(r, 200));
      // A frame in the window talks to its parent, the window: the page hears it.
      let heard = null; const hear = e => { if (e.data && e.data.type === 'dock-relay-check') heard = e.source === w; };
      window.addEventListener('message', hear); new w.Function("postMessage({ type: 'dock-relay-check' }, location.origin)")();
      await new Promise(r => setTimeout(r, 200)); window.removeEventListener('message', hear);
      return { live, count, followed, back: d.documentElement.dataset.theme === theme, list, title: d.title, styled: getComputedStyle(d.querySelector('.card')).borderRadius, heard };
    })()`);
    await p.evalIn('PD.dock.popWindow("board").close(); 0');
    await p.until('!PD.dock.isOut("board") && PD.els.board.ownerDocument === document', 10000);
    out.boardBack = await p.evalIn('({ inDock: !!PD.els.board.closest("#po-dock"), cards: PD.els.board.querySelectorAll(".card").length })');
    await p.click('PD.els["po-chat"].closest(".dk-stack").querySelector(\'[data-dk-act="pop"]\')');
    await p.until('!!PD.dock.popWindow("po-chat") && PD.els["po-chat"].ownerDocument !== document', 15000);
    await p.until('(() => { const f = PD.els["po-chat"].querySelector("iframe.pd-own"); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN") && f.contentDocument.getElementById("input")); })()', 20000);
    out.popChat = await p.evalIn(`(async () => {
      const f = PD.els['po-chat'].querySelector('iframe.pd-own'), d = f.contentDocument, w = f.contentWindow;
      const inp = d.getElementById('input'); inp.focus(); d.execCommand('insertText', false, 'typed in its own window');
      const a = PD.els.points.querySelector('.pdp-row[data-pt="P2"] a.pdp-link'); a.click();
      for (let i = 0; i < 40 && w.eval('GOTO'); i++) await new Promise(r => setTimeout(r, 100));
      const el = [...d.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === a.dataset.mid);
      const r = { typed: inp.value, mainHidden: PO_PANEL.classList.contains('pd-off') && getComputedStyle(PO_PANEL).visibility === 'hidden' && PO_PANEL.parentNode === PD_HOST, page: PD.dock.popWindow('po-chat').location.pathname, line: d.getElementById('points-line').hidden, landed: !!el && el.classList.contains('landed'), size: [f.offsetWidth > 300, f.offsetHeight > 200] };
      inp.value = ''; inp.dispatchEvent(new Event('input'));
      return r;
    })()`);
    await p.evalIn('PD.els["po-chat"].addEventListener("dock-popin", () => setTimeout(() => { window.__popinOwn = !PD.els["po-chat"].querySelector("iframe.pd-own"); }), { once: true }); 0');
    await p.evalIn('PD.dock.popWindow("po-chat").document.querySelector(\'[data-dk-pop="back"]\').click(); 0');
    await p.until('!PD.dock.isOut("po-chat") && PD.els["po-chat"].ownerDocument === document', 10000);
    await sleep(400);
    out.chatBack = await p.evalIn(`(() => { ${rect} return { own: !!PD.els['po-chat'].querySelector('iframe.pd-own'), shown: !PO_PANEL.classList.contains('pd-off'), same: JSON.stringify(R(PO_PANEL)) === JSON.stringify(R(PD.els['po-chat'])), inPanel: PO_PANEL.parentNode === PD.els['po-chat'], kept: !!pdChatFrame().contentWindow.__kept, gone: window.__popinOwn }; })()`);

    // Points in the layout but not on screen: the chat keeps its own points line.
    out.pointsSeen = await p.evalIn(`(async () => {
      const line = () => pdChatFrame().contentDocument.getElementById('points-line').hidden;
      const w = () => new Promise(r => setTimeout(r, 400));
      const stackOf = id => { let s = null; (function walk(n) { if (!n || s) return; if (n.t === 'stack') { if (n.panels.includes(id)) s = n; } else n.kids.forEach(walk); })(PD.dock.layout().root); return s; };
      const r = {};
      PD.dock.reset(); await w(); r.side = line();
      PD.dock.moveTo('points', { kind: 'stack', stack: stackOf('po-chat') }, 'center'); PD.dock.activate('po-chat'); await w(); r.behindTab = line();
      PD.dock.activate('points'); await w(); r.inFront = line();
      PD.dock.reset(); await w(); PD.dock.unpin('points'); await w(); r.slidIn = line();
      PD.dock.openFly('points'); await w(); r.slidOut = line();
      PD.dock.closeFly(); PD.dock.reset(); await w(); r.back = line();
      return r;
    })()`);

    // Changes in its own window: a diff, a selection across its lines, a dialog and a toast there.
    await p.evalIn('PD.dock.pin("changes"); 0'); await sleep(300);
    await p.until('!!PD.els.changes.querySelector(".chf[data-file]")', 20000);
    await p.click('PD.els.changes.closest(".dk-stack").querySelector("[data-dk-act=pop]")');
    await p.until('!!PD.dock.popWindow("changes") && PD.els.changes.ownerDocument !== document', 15000);
    out.popChanges = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const w = PD.dock.popWindow('changes'), d = w.document, r = {};
      PD.els.changes.querySelector('.chf[data-file]').click();
      for (let i = 0; i < 60 && !PD.els.changes.querySelector('.drv .dr .dg[data-n]'); i++) await sleep(100);
      const rows = [...PD.els.changes.querySelectorAll('.drv .dr')].filter(x => x.querySelector('.dg[data-n]'));
      r.rows = rows.length;
      d.getSelection().setBaseAndExtent(rows[0].querySelector('.dt'), 0, rows[2].querySelector('.dt'), 0);
      await sleep(500);
      const btn = d.querySelector('.cmt-selbtn');
      r.selInWindow = !!btn; r.selInMain = !!document.querySelector('.cmt-selbtn');
      if (btn) { btn.click(); await sleep(300); }
      const ta = PD.els.changes.querySelector('.dcx textarea');
      r.commentBox = !!ta && ta.ownerDocument === d;
      const cancel = PD.els.changes.querySelector('.dcx-cancel'); if (cancel) cancel.click();
      const press = doc => { const v = doc.defaultView; doc.body.dispatchEvent(new v.PointerEvent('pointerdown', { bubbles: true })); doc.body.dispatchEvent(new v.PointerEvent('pointerup', { bubbles: true })); };
      press(d);
      const ask = showConfirmDialog({ title: 'Check', message: 'Asked where you are working' });
      await sleep(150);
      r.dialogInWindow = MODAL_EL.ownerDocument === d && MODAL_EL.open && !!d.activeElement && d.activeElement.id === 'cf-cancel';
      d.getElementById('cf-cancel').click();
      r.answer = await ask;
      toast('shown where you are working');
      r.toastInWindow = !!d.getElementById('status') && !d.getElementById('status').hidden;
      press(document);
      const ask2 = showConfirmDialog({ title: 'Check', message: 'Back in the main window' });
      await sleep(150);
      r.dialogInMain = MODAL_EL.ownerDocument === document && MODAL_EL.open;
      document.getElementById('cf-cancel').click(); await ask2;
      // Its scope, changed twice: the panel redraws in its window and the new selector still works.
      r.scope = [];
      const pj = projectById(SELECTED_PROJECT), opts = scopeOptions(pj).map(o => o.path);
      for (const i of [1, 0, 1]) {
        const s = PD.els.changes.querySelector('.scope-sel');
        if (!s || !opts[i]) { r.scope.push({ options: opts.length }); break; }
        s.value = opts[i]; s.dispatchEvent(new w.Event('change'));
        await sleep(400);
        const s2 = PD.els.changes.querySelector('.scope-sel');
        r.scope.push({ now: scopePath(pj) === opts[i], redrawn: s2 !== s, there: !!s2 && s2.ownerDocument === d });
      }
      return r;
    })()`);
    await p.evalIn('PD.dock.popWindow("changes").close(); 0');
    await p.until('!PD.dock.isOut("changes") && PD.els.changes.ownerDocument === document', 10000);

    // The Workspace in its own window: the roadmap opens there, a file's viewer reports to its tab.
    await p.evalIn('PD.dock.pin("workspace"); 0'); await sleep(300);
    await p.until('!!PD.els.workspace.querySelector(".wsp-tree .wse[data-path]")', 20000);
    await p.click('PD.els.workspace.closest(".dk-stack").querySelector("[data-dk-act=pop]")');
    await p.until('!!PD.dock.popWindow("workspace") && PD.els.workspace.ownerDocument !== document', 15000);
    out.popWs = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const w = PD.dock.popWindow('workspace'), d = w.document, r = {};
      PD.els.workspace.querySelector('.wsd-open[data-roadmap]').click();
      for (let i = 0; i < 50 && !/Motors roadmap/.test((d.getElementById('rm-panel') || {}).innerText || ''); i++) await sleep(100);
      const rm = d.getElementById('rm-panel');
      r.roadmapInWindow = !!rm && !rm.hidden && rm.ownerDocument === d;
      PD.els.workspace.querySelector('.wsd-back').click();
      const file = [...PD.els.workspace.querySelectorAll('.wsp-tree .wse[data-path]')].find(x => x.dataset.path.endsWith('ROADMAP.md'));
      file.click();
      let f = null;
      for (let i = 0; i < 80; i++) {
        f = PD.els.workspace.querySelector('iframe.wsp-frame');
        try { if (f && f.contentDocument && f.contentDocument.readyState === 'complete' && f.contentWindow.location.pathname === '/fileview') break; } catch (e) {}
        await sleep(100);
      }
      r.viewerInWindow = !!f && f.ownerDocument === d;
      try { r.viewerLoaded = f.contentWindow.location.pathname === '/fileview'; } catch (e) { r.viewerLoaded = String(e); }
      new f.contentWindow.Function("parent.postMessage({ type: 'fv-state', st: { view: 'source', wrap: true, marks: {} } }, location.origin)")();
      await sleep(300);
      const v = [...WS_VIEWS.values()].find(x => x.el && PD.els.workspace.contains(x.el));
      const tab = v && v.tabs.find(x => x.path.endsWith('ROADMAP.md'));
      r.tabHeard = !!tab && tab.st.view === 'source' && tab.st.wrap === true;
      // The window's base is this page's (its one <base href>, put there by the page): a relative link there is this hub's.
      const a = d.createElement('a'); a.href = 'fileview?x=1';
      r.base = { n: d.querySelectorAll('base').length, first: d.head.firstElementChild.tagName, uri: d.baseURI === document.baseURI,
        link: a.href === new URL('fileview?x=1', document.baseURI).href };
      return r;
    })()`);
    await p.evalIn('PD.dock.popWindow("workspace").close(); 0');
    await p.until('!PD.dock.isOut("workspace") && PD.els.workspace.ownerDocument === document', 10000);

    // A phone's width: the same dock turns narrow, and wide again as it was.
    await p.evalIn('window.__saved = localStorage.getItem("cd-po-dock"); window.__dock = PD.dock; window.__wide = (() => { const L = PD.dock.layout(); return JSON.stringify([L.root, L.auto.map(a => [a.id, a.edge]), L.floats, L.hidden]); })(); pdChatFrame().contentWindow.__kept = 1; 0');
    await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, p.sessionId);
    await p.until('PD.dock.narrow()', 10000); await sleep(400);
    out.toPhone = await p.evalIn(`({ same: window.__dock === PD.dock, narrow: PD_ROOT.classList.contains('dk-narrow'),
      tabs: [...document.querySelectorAll('#po-dock .dk-tab')].map(t => t.dataset.dkTab), front: PD_IDS.filter(id => PD.dock.frontOf(id) === id),
      ctl: [...document.querySelectorAll('#po-dock [data-dk-act]')].filter(x => x.offsetWidth).length, strip: document.querySelectorAll('.dk-strip-btn').length,
      chatIn: PO_PANEL.parentNode === PD.els['po-chat'], kept: !!pdChatFrame().contentWindow.__kept, scrollX: document.documentElement.scrollWidth - innerWidth })`);
    // Points in front, then a task over the PO screen and the pill's drawer over the task:
    // the Points panel is covered, so the chat shows its own points line again.
    await p.evalIn('pdReveal("points"); 0'); await sleep(300);
    out.peekBefore = await p.evalIn('pdPointsOnScreen()');
    await p.evalIn(`(() => { openDetail(${JSON.stringify(A.task)}); PO_PEEK = true; renderPo(); return 0; })()`);
    await p.until('document.body.classList.contains("po-peek") && PO_PANEL.parentNode === PD_HOST', 10000); await sleep(400);
    out.peek = await p.evalIn(`(() => { const f = pdChatFrame(); return { onScreen: pdPointsOnScreen(), elsewhere: f.hasAttribute('data-points-elsewhere'),
      line: !f.contentDocument.getElementById('points-line').hidden, drawer: !PO_PANEL.hidden && !PO_PANEL.classList.contains('pd-off') }; })()`);
    await p.evalIn('PO_PEEK = false; closeDetail(); pdReveal("po-chat"); 0');
    await p.until('!document.body.classList.contains("po-peek") && PO_PANEL.parentNode === PD.els["po-chat"]', 10000); await sleep(300);
    out.peekAfter = await p.evalIn('pdPointsFrame(), pdChatFrame().hasAttribute("data-points-elsewhere")');
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId);
    await p.until('!PD.dock.narrow()', 10000); await sleep(400);
    out.toWide = await p.evalIn(`({ same: window.__dock === PD.dock, asWas: (() => { const L = PD.dock.layout(); return JSON.stringify([L.root, L.auto.map(a => [a.id, a.edge]), L.floats, L.hidden]); })() === window.__wide,
      chatIn: PO_PANEL.parentNode === PD.els['po-chat'], kept: !!pdChatFrame().contentWindow.__kept,
      saved: !!window.__saved && localStorage.getItem('cd-po-dock') === window.__saved,
      homes: JSON.parse(localStorage.getItem('cd-po-dock') || '{}') })`);

    // A documents project's Files panel in its own window: a row's menu closes on a click
    // elsewhere, a scroll and a resize of that window.
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(A.notes)}; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock', 20000);
    await p.evalIn('PD.dock.pin("workspace"); 0'); await sleep(300);
    await p.until('!!PD.els.workspace.querySelector(".dcs-files .wsp-tree .wse[data-path] .wse-more")', 20000);
    await p.click('PD.els.workspace.closest(".dk-stack").querySelector("[data-dk-act=pop]")');
    await p.until('!!PD.dock.popWindow("workspace") && PD.els.workspace.ownerDocument !== document', 15000);
    out.popFiles = await p.evalIn(`(async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const w = PD.dock.popWindow('workspace'), d = w.document, r = {};
      await sleep(300);
      const menu = PD.els.workspace.querySelector('.dcs-files .dcm');
      const open = async () => { PD.els.workspace.querySelector('.dcs-files .wsp-tree .wse[data-path] .wse-more').click(); await sleep(150); return !menu.hidden; };
      r.opened = await open();
      d.body.click(); await sleep(150); r.click = menu.hidden;
      await open();
      PD.els.workspace.querySelector('.wsp-tree').dispatchEvent(new w.Event('scroll')); await sleep(150); r.scroll = menu.hidden;
      await open();
      w.dispatchEvent(new w.Event('resize')); await sleep(150); r.resize = menu.hidden;
      r.inWindow = menu.ownerDocument === d;
      return r;
    })()`);
    await p.evalIn('PD.dock.popWindow("workspace").close(); 0');
    await p.until('!PD.dock.isOut("workspace") && PD.els.workspace.ownerDocument === document', 10000);
    await p.close();

    // ---- Sizes and themes: nothing wider than the screen
    out.sizes = {};
    for (const [w, h, mob] of [[1400, 900, false], [1800, 1000, false], [390, 844, true]]) {
      const q = await page(w, h, mob);
      await go(q); await ready(q);
      for (const theme of ['light', 'dark', 'contrast']) {
        // As the avatar menu does: stored (the chat hears it by a storage event) and applied here.
        await q.evalIn(`(() => { const s = ({ light: 'light', dark: 'dark', contrast: 'light' })['${theme}']; localStorage.setItem('cd-theme', '${theme}'); document.documentElement.dataset.theme = '${theme}'; document.documentElement.dataset.scheme = s; return 0; })()`);
        await q.until(`pdChatFrame().contentDocument.documentElement.dataset.theme === '${theme}'`, 5000);
        await sleep(500);
        out.sizes[w + '/' + theme] = await q.evalIn(`(() => { ${rect}
          const over = [...document.querySelectorAll('body *')].filter(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > innerWidth + 1 && !e.closest('.po-board, .dk-flyout, .dk-parking, .wst, .dk-head, dialog, #detail-panel, #notif-tray, #settings-panel, #usage-tray') && getComputedStyle(e).position !== 'fixed'; }).map(e => e.tagName + '.' + e.className).slice(0, 5);
          const bg = getComputedStyle(PD.els.points).backgroundColor, fg = getComputedStyle(PD.els.points.querySelector('.pdp-words') || PD.els.points).color;
          return { phone: PD.dock.narrow(), scrollX: document.documentElement.scrollWidth - innerWidth, scrollY: document.documentElement.scrollHeight - innerHeight, over,
            tabs: [...document.querySelectorAll('.dk-tab')].map(t => t.dataset.dkTab), shown: PD_IDS.filter(id => PD.dock.frontOf(id) === id),
            ctl: [...document.querySelectorAll('#po-dock [data-dk-act]')].filter(x => x.offsetWidth).length, host: R(PD_HOST), po: R(PO_PANEL), chat: R(PD.els['po-chat']),
            poOff: PO_PANEL.classList.contains('pd-off'), bg, fg, accent: getComputedStyle(document.querySelector('.dk-tab.on')).borderBottomColor, want: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() };
        })()`);
        await q.shot(`po-dock-${w}x${h}-${theme}`);
      }
      if (mob) {
        await q.evalIn('document.querySelector(\'.dk-tab[data-dk-tab="points"]\').click(); 0');
        await sleep(300);
        out.phoneTab = await q.evalIn('({ front: PD.dock.frontOf("points"), chatHidden: getComputedStyle(PO_PANEL).visibility === "hidden", width: Math.round(PD.els.points.getBoundingClientRect().width) })');
        await q.shot('po-dock-390-points');
      }
      await q.close();
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
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
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dock-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
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
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        (home / "ROADMAP.md").write_text("# Motors roadmap\n\nFirst the panels.\n", encoding="utf-8")
        # A repository with an uncommitted change, for the Changes panel's diff.
        code = Path(proj["path"])

        def git(*a):
            subprocess.run(["git", "-C", str(code), *a], capture_output=True, check=True, encoding="utf-8")
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        (code / "motor.py").write_text("".join(f"speed_{i} = {i}\n" for i in range(12)), encoding="utf-8")
        git("add", "motor.py")
        git("commit", "-q", "-m", "motors")
        (code / "motor.py").write_text("".join(f"speed_{i} = {i * 2}\n" for i in range(12)), encoding="utf-8")
        members = lambda: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                           {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        # The task works in a folder of its own: the Changes scope offers it beside the project.
        wt = base / "wider-board"
        wt.mkdir()
        task = chatroom.create_room("Wider board", [{**m, "cwd": str(wt)} for m in members()])
        cls.task = task["id"]
        chatroom.update_room({**chatroom.get_room(cls.task, public=False), "cwd": str(wt)})
        dashboard.assign_session_project(cls.task, cls.proj)
        po = chatroom.create_room("PO talk", members())
        cls.po = po["id"]
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        # A documents project with a PO, for the Files panel in its own window.
        ok, notes, _ = dashboard.register_project("Notes")
        assert ok, notes
        cls.notes = notes["id"]
        assert dashboard.set_project_kind(cls.notes, "documents") == (True, "ok")
        (Path(notes["path"]) / "minutes.md").write_text("# Minutes\n", encoding="utf-8")
        npo = chatroom.create_room("Notes PO", members())
        dashboard.assign_session_project(npo["id"], cls.notes)
        ok, why = dashboard.set_project_po(cls.notes, npo["id"])
        assert ok, why
        # Two points: P1 answered, P2 waiting; then enough talk that both are far up.
        room = chatroom.get_room(cls.po, public=False)
        text, ids = points.take(room, "Please make the board wider", to="claude", key="k1")
        assert ids == ["P1"], ids
        chatroom.post_message(cls.po, "user", text, to="claude")
        chatroom.post_message(cls.po, "claude", "Re P1: it is wider now.", to="user")
        text, ids = points.take(chatroom.get_room(cls.po, public=False), "And the chat taller", to="claude", key="k2")
        assert ids == ["P2"], ids
        chatroom.post_message(cls.po, "user", text, to="claude")
        for i in range(30):
            chatroom.post_message(cls.po, "codex", f"note {i}\n\n" + "words " * 60, to="claude")
        points.sync(cls.po, force=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {"chrome": CHROME, "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.port}", "proj": cls.proj,
                "po": cls.po, "task": cls.task, "notes": cls.notes, "shots": shots}
        # From a file: the script is longer than a Windows command line may be (32767 characters).
        script = base / "po_dock_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def test_the_default_at_1440_is_the_chat_and_points(self):
        f = self.got["first"]
        self.assertEqual(f["onScreen"], ["po-chat", "points"])
        self.assertEqual(f["strip"], ["board", "workspace", "changes"])
        self.assertIsNone(f["fly"], "nothing slid out")
        self.assertEqual(f["po"], f["chat"], "the chat fills its panel")
        self.assertTrue(f["inPanel"], "the chat is in its panel, not laid over it")
        self.assertFalse(f["poOff"])
        self.assertEqual(f["rows"], ["P1", "P2"])
        self.assertEqual(f["badge"], "2")
        self.assertTrue(f["elsewhere"] and f["pointsLine"], "the chat's own points line steps aside for the panel")
        self.assertFalse(f["tabs"], "no tab row: the panels are the tabs")
        self.assertTrue(f["panels"])

    def test_layout_changes_reload_no_iframe(self):
        k = self.got["kept"]
        self.assertTrue(k.pop("moveBefore"), "this Chrome has moveBefore")
        self.assertTrue(k.pop("chatIn"), "a floated chat is still in its panel")
        for step, alive in k.items():
            self.assertEqual(alive, [True, True], f"{step}: [open file, PO chat] kept their pages")

    def test_f6_and_the_arrows_move_along_the_panels(self):
        k = self.got["keys"]
        self.assertEqual((k["f6"], k["back"]), ("points", "po-chat"), "F6 to the next stack, Shift+F6 back")
        self.assertEqual((k["right"], k["front"]), ("board", "board"), "Right: the next tab, brought to the front")
        self.assertEqual(k["roving"], ["po-chat", "board"], "one tab of each docked stack in the Tab order")

    def test_f6_works_from_inside_the_chat_composer(self):
        k = self.got["keys"]
        self.assertEqual(k["frameF6"], "points", "F6 in the composer's frame: the next stack")
        self.assertEqual(k["frameBack"], "points", "Shift+F6 there: the other stack (two in all)")

    def test_a_phone_width_turns_the_same_dock_narrow_and_back(self):
        n = self.got["toPhone"]
        self.assertTrue(n["same"] and n["narrow"], "setNarrow, not a second dock")
        self.assertEqual(n["tabs"], ["po-chat", "points", "board", "workspace", "changes"])
        self.assertEqual(n["front"], ["po-chat"])
        self.assertEqual((n["ctl"], n["strip"], n["scrollX"]), (0, 0, 0), "nothing that moves a panel, no strip")
        self.assertTrue(n["chatIn"] and n["kept"], "the chat moved with its panel, keeping its page")
        w = self.got["toWide"]
        self.assertTrue(w["same"] and w["chatIn"] and w["kept"])
        self.assertTrue(w["asWas"], "the wide layout as it was")
        self.assertTrue(w["saved"], "the saved layout is byte for byte what it was: no home gained index/near/side")

    def test_the_phone_drawer_over_a_task_brings_the_chats_points_line_back(self):
        self.assertTrue(self.got["peekBefore"], "Points in front on the phone")
        k = self.got["peek"]
        self.assertTrue(k["drawer"], "the drawer is open over the task")
        self.assertFalse(k["onScreen"] or k["elsewhere"], "the Points panel is covered")
        self.assertTrue(k["line"], "the chat shows its own points line")

    def test_a_points_arrow_scrolls_the_chat_to_its_balloon_and_marks_it(self):
        a = self.got["arrow"]
        self.assertGreater(a["moved"], 200, "the chat scrolled up to it")
        self.assertTrue(a["inView"] and a["landed"], a)
        self.assertIn("it is wider now", a["text"])

    def test_the_roadmap_is_the_first_document_and_opens_in_its_view(self):
        self.assertEqual(self.got["docs"]["first"], "Roadmap")
        self.assertEqual(self.got["docs"]["flyScroll"], 0, "sliding the panel out does not shift the dock")
        r = self.got["roadmap"]
        self.assertTrue(r["inWs"] and r["edit"] and r["back"], r)
        self.assertEqual(r["note"], 0, "no empty notice bar")

    def test_reset_layout_brings_the_default_back(self):
        self.assertEqual(self.got["hidden"], {"points": False, "line": False, "saved": True},
                         "Points hidden: the chat shows its own points line again; the layout is remembered")
        self.assertEqual(self.got["reset"], {"points": True, "front": ["po-chat", "points"], "auto": ["board", "workspace", "changes"],
                                             "line": True, "menu": False})

    def test_ack_in_points_acknowledges_it_everywhere(self):
        self.assertEqual(self.got["acked"], {"rows": ["P2"], "chat": "acked"})

    def test_a_popped_out_board_stays_live_and_comes_back(self):
        b = self.got["popBoard"]
        self.assertTrue(b["live"], "a card patched in its own window")
        self.assertRegex(b["count"], r"\d+ tasks?|\d+ of \d+", "the task count redrawn there")
        self.assertEqual((b["followed"], b["back"]), ("dark", True), "the theme follows")
        self.assertGreater(b["list"], 0, "a click there switches the view")
        self.assertEqual(b["title"], "Board · Motors")
        self.assertNotEqual(b["styled"], "0px", "the page's styles came along")
        self.assertTrue(b["heard"], "a message to the window reaches the page, with its source")
        self.assertTrue(self.got["boardBack"]["inDock"] and self.got["boardBack"]["cards"] > 0)

    def test_a_popped_out_po_chat_takes_typing_and_comes_back(self):
        c = self.got["popChat"]
        self.assertEqual(c["typed"], "typed in its own window")
        self.assertTrue(c["mainHidden"], "the chat here waits at home, out of sight")
        self.assertEqual(c["page"], "/static/dock/src/popout.html", "the window's page is the library's, as the hub serves it (#101)")
        self.assertTrue(c["line"], "Points is still shown: no points line there either")
        self.assertTrue(c["landed"], "an arrow goes to the chat in the window")
        self.assertEqual(c["size"], [True, True])
        self.assertEqual(self.got["chatBack"], {"own": False, "shown": True, "same": True, "inPanel": True, "kept": True, "gone": True},
                         "the window's chat went before the panel came back; the chat here was never reloaded")

    def test_the_chats_points_line_follows_whether_points_is_on_screen(self):
        self.assertEqual(self.got["pointsSeen"], {"side": True, "behindTab": False, "inFront": True, "slidIn": False,
                                                 "slidOut": True, "back": True},
                         "hidden only while the Points panel is on screen")

    def test_a_popped_out_changes_panel_works_in_its_own_window(self):
        c = self.got["popChanges"]
        self.assertGreaterEqual(c["rows"], 3)
        self.assertEqual((c["selInWindow"], c["selInMain"]), (True, False), "a selection there offers its Comment there")
        self.assertTrue(c["commentBox"])
        self.assertTrue(c["dialogInWindow"], "a question asked while working there opens there, Cancel focused")
        self.assertIs(c["answer"], False)
        self.assertTrue(c["toastInWindow"])
        self.assertTrue(c["dialogInMain"], "back in the main window, it opens here again")

    def test_a_popped_out_changes_panel_keeps_its_scope_selector(self):
        self.assertEqual(self.got["popChanges"]["scope"], [{"now": True, "redrawn": True, "there": True}] * 3,
                         "every scope change, not only the first, is heard from the window")

    def test_a_popped_out_files_menu_closes_as_in_the_page(self):
        self.assertEqual(self.got["popFiles"], {"opened": True, "click": True, "scroll": True, "resize": True, "inWindow": True})

    def test_a_popped_out_workspace_works_in_its_own_window(self):
        ws = dict(self.got["popWs"])
        base = ws.pop("base")
        self.assertEqual(ws, {"roadmapInWindow": True, "viewerInWindow": True, "viewerLoaded": True, "tabHeard": True},
                         "a relative frame address in the window is this page's (its base), not popout.html's")
        self.assertEqual(base, {"n": 1, "first": "BASE", "uri": True, "link": True},
                         "one <base href>, this page's, first in the head: a relative link there is this hub's")

    def test_no_size_or_theme_overflows(self):
        s = self.got["sizes"]
        self.assertEqual(set(s), {f"{w}/{t}" for w in (1400, 1800, 390) for t in ("light", "dark", "contrast")})
        for k, v in s.items():
            self.assertEqual((v["scrollX"], v["scrollY"], v["over"]), (0, 0, []), k)
            self.assertFalse(v["poOff"], k)
            self.assertEqual(v["po"], v["chat"], k)
            self.assertNotEqual(v["bg"], v["fg"], k)
        for t in ("light", "dark", "contrast"):
            self.assertEqual(s[f"1400/{t}"]["shown"], ["po-chat", "points"])
            self.assertEqual(s[f"1800/{t}"]["shown"], ["po-chat", "points", "board"])
            p = s[f"390/{t}"]
            self.assertTrue(p["phone"])
            self.assertEqual(p["tabs"], ["po-chat", "points", "board", "workspace", "changes"], "one column of tabs")
            self.assertEqual(p["ctl"], 0, "no float, strip or pop-out controls on a phone")
            self.assertEqual(p["host"][2], 390)
        self.assertEqual(self.got["phoneTab"], {"front": "points", "chatHidden": True, "width": 390})


if __name__ == "__main__":
    unittest.main()
