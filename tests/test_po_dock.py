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

* the default layout on screen at 1440x900, the chat lying exactly over its
  panel's place, the chat's own points line hidden while Points is shown;
* a Points arrow scrolls the PO chat to that balloon and marks it;
* the roadmap opens from the Workspace's Documents list, in its own view;
* Reset layout brings the default back after a panel was hidden;
* Ack in Points acknowledges the point, in the panel and in the chat;
* a panel popped out into its own window (the Board, the PO chat) keeps its
  live updates, takes clicks and typing, follows the theme, and comes back
  when its window closes;
* at 1440x900, 1920x1080 and a 390 px phone, in Light, Dark and High
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
                    "static/dock/src/host.js", "static/dock/css/dock.css", "static/dock/src/popout.html"):
            self.assertIn(rel, dashboard.PAGE_FILES)
            self.assertIn(f'href="/{rel}"', INDEX, f"the page lists {rel} for Page update")
        self.assertIn("import('/static/dock/src/index.js')", INDEX)
        self.assertNotIn("dock/css/theme.css", INDEX, "the --dk-* tokens read Ensemble's own")
        for tok in ("--dk-bg: var(--bg)", "--dk-accent: var(--accent)", "--dk-focus: var(--focus-ring)", "--dk-fg3: var(--fg-muted)"):
            self.assertIn(tok, INDEX)


# ---- the page in headless Chrome -----------------------------------------------

CDP_JS = r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[1]);
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
        fly: PD.dock.flyOpen(), chat: R(PD.els['po-chat']), po: R(PO_PANEL), poOff: PO_PANEL.classList.contains('pd-off'),
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
      const theme = document.documentElement.dataset.theme; document.documentElement.dataset.theme = 'dark';
      await new Promise(r => setTimeout(r, 200)); const followed = d.documentElement.dataset.theme; document.documentElement.dataset.theme = theme;
      await new Promise(r => setTimeout(r, 200));
      d.querySelector('.viewsw button[data-view="list"]').click(); await new Promise(r => setTimeout(r, 200));
      const list = d.querySelectorAll('tr.row').length; d.querySelector('.viewsw button[data-view="board"]').click(); await new Promise(r => setTimeout(r, 200));
      // A frame in the window talks to its parent, the window: the page hears it.
      let heard = null; const hear = e => { if (e.data && e.data.type === 'dock-relay-check') heard = e.source === w; };
      window.addEventListener('message', hear); new w.Function("postMessage({ type: 'dock-relay-check' }, location.origin)")();
      await new Promise(r => setTimeout(r, 200)); window.removeEventListener('message', hear);
      return { live, followed, back: d.documentElement.dataset.theme === theme, list, title: d.title, styled: getComputedStyle(d.querySelector('.card')).borderRadius, heard };
    })()`);
    await p.evalIn('PD.dock.popWindow("board").close(); 0');
    await p.until('!PD.dock.isOut("board") && PD.els.board.ownerDocument === document', 10000);
    out.boardBack = await p.evalIn('({ inDock: !!PD.els.board.closest("#po-dock"), cards: PD.els.board.querySelectorAll(".card").length })');
    await p.click('PD.els["po-chat"].closest(".dk-stack").querySelector(\'[data-dk-act="pop"]\')');
    await p.until('!!PD.dock.popWindow("po-chat") && PD.els["po-chat"].ownerDocument !== document', 15000);
    await p.until('(() => { const f = PD.els["po-chat"].querySelector("iframe.po-session"); return !!(f && f.contentWindow && f.contentWindow.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN") && f.contentDocument.getElementById("input")); })()', 20000);
    out.popChat = await p.evalIn(`(async () => {
      const f = PD.els['po-chat'].querySelector('iframe.po-session'), d = f.contentDocument, w = f.contentWindow;
      const inp = d.getElementById('input'); inp.focus(); d.execCommand('insertText', false, 'typed in its own window');
      const a = PD.els.points.querySelector('.pdp-row[data-pt="P2"] a.pdp-link'); a.click();
      for (let i = 0; i < 40 && w.eval('GOTO'); i++) await new Promise(r => setTimeout(r, 100));
      const el = [...d.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === a.dataset.mid);
      const r = { typed: inp.value, mainHidden: PO_PANEL.classList.contains('pd-off'), line: d.getElementById('points-line').hidden, landed: !!el && el.classList.contains('landed'), size: [f.offsetWidth > 300, f.offsetHeight > 200] };
      inp.value = ''; inp.dispatchEvent(new Event('input'));
      return r;
    })()`);
    await p.evalIn('PD.dock.popWindow("po-chat").document.querySelector(\'[data-dk-pop="back"]\').click(); 0');
    await p.until('!PD.dock.isOut("po-chat") && PD.els["po-chat"].ownerDocument === document', 10000);
    await sleep(400);
    out.chatBack = await p.evalIn(`(() => { ${rect} return { own: !!PD.els['po-chat'].querySelector('iframe'), shown: !PO_PANEL.classList.contains('pd-off'), same: JSON.stringify(R(PO_PANEL)) === JSON.stringify(R(PD.els['po-chat'])) }; })()`);
    await p.close();

    // ---- Sizes and themes: nothing wider than the screen
    out.sizes = {};
    for (const [w, h, mob] of [[1440, 900, false], [1920, 1080, false], [390, 844, true]]) {
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
          return { phone: PD.phone, scrollX: document.documentElement.scrollWidth - innerWidth, scrollY: document.documentElement.scrollHeight - innerHeight, over,
            tabs: [...document.querySelectorAll('.dk-tab')].map(t => t.dataset.dkTab), shown: PD_IDS.filter(id => PD.dock.frontOf(id) === id),
            ctl: [...document.querySelectorAll('.dk-ctl')].filter(x => x.offsetWidth).length, host: R(PD_HOST), po: R(PO_PANEL), chat: R(PD.els['po-chat']),
            poOff: PO_PANEL.classList.contains('pd-off'), bg, fg, accent: getComputedStyle(document.querySelector('.dk-tab.on')).borderBottomColor, want: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() };
        })()`);
        await q.shot(`po-dock-${w}x${h}-${theme}`);
      }
      if (mob) {
        await q.evalIn('document.querySelector(\'.dk-tab[data-dk-tab="points"]\').click(); 0');
        await sleep(300);
        out.phoneTab = await q.evalIn('({ front: PD.dock.frontOf("points"), chatHidden: PO_PANEL.classList.contains("pd-off"), width: Math.round(PD.els.points.getBoundingClientRect().width) })');
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
        members = lambda: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                           {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        task = chatroom.create_room("Wider board", members())
        cls.task = task["id"]
        dashboard.assign_session_project(cls.task, cls.proj)
        po = chatroom.create_room("PO talk", members())
        cls.po = po["id"]
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
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
                "po": cls.po, "task": cls.task, "shots": shots}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
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
        self.assertEqual(f["po"], f["chat"], "the chat lies exactly over its panel's place")
        self.assertFalse(f["poOff"])
        self.assertEqual(f["rows"], ["P1", "P2"])
        self.assertEqual(f["badge"], "2")
        self.assertTrue(f["elsewhere"] and f["pointsLine"], "the chat's own points line steps aside for the panel")
        self.assertFalse(f["tabs"], "no tab row: the panels are the tabs")
        self.assertTrue(f["panels"])

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
        self.assertEqual((b["followed"], b["back"]), ("dark", True), "the theme follows")
        self.assertGreater(b["list"], 0, "a click there switches the view")
        self.assertEqual(b["title"], "Board · Motors")
        self.assertNotEqual(b["styled"], "0px", "the page's styles came along")
        self.assertTrue(b["heard"], "a message to the window reaches the page, with its source")
        self.assertTrue(self.got["boardBack"]["inDock"] and self.got["boardBack"]["cards"] > 0)

    def test_a_popped_out_po_chat_takes_typing_and_comes_back(self):
        c = self.got["popChat"]
        self.assertEqual(c["typed"], "typed in its own window")
        self.assertTrue(c["mainHidden"], "the chat over the empty place steps aside")
        self.assertTrue(c["line"], "Points is still shown: no points line there either")
        self.assertTrue(c["landed"], "an arrow goes to the chat in the window")
        self.assertEqual(c["size"], [True, True])
        self.assertEqual(self.got["chatBack"], {"own": False, "shown": True, "same": True})

    def test_no_size_or_theme_overflows(self):
        s = self.got["sizes"]
        self.assertEqual(set(s), {f"{w}/{t}" for w in (1440, 1920, 390) for t in ("light", "dark", "contrast")})
        for k, v in s.items():
            self.assertEqual((v["scrollX"], v["scrollY"], v["over"]), (0, 0, []), k)
            self.assertFalse(v["poOff"], k)
            self.assertEqual(v["po"], v["chat"], k)
            self.assertNotEqual(v["bg"], v["fg"], k)
        for t in ("light", "dark", "contrast"):
            self.assertEqual(s[f"1440/{t}"]["shown"], ["po-chat", "points"])
            self.assertEqual(s[f"1920/{t}"]["shown"], ["po-chat", "points", "board"])
            p = s[f"390/{t}"]
            self.assertTrue(p["phone"])
            self.assertEqual(p["tabs"], ["po-chat", "points", "board", "workspace", "changes"], "one column of tabs")
            self.assertEqual(p["ctl"], 0, "no float, strip or pop-out controls on a phone")
            self.assertEqual(p["host"][2], 390)
        self.assertEqual(self.got["phoneTab"], {"front": "points", "chatHidden": True, "width": 390})


if __name__ == "__main__":
    unittest.main()
