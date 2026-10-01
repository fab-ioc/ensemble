"""#148: the task panel runs on Dock.

The middle of a PO screen (the Chat panel, the dock's fill) has the tools'
title bar: its ⋯ offers View Mode (Float, Window), Take Screenshot, Maximise
and Hide, and − minimises. An open task is that panel's content (pdPlaceTask),
so it goes with the panel into a window of its own, and the panel's window is
named after the task. The hand-made controls are gone: no ⧉ Pop out in the
task's header, no Pop out in the PO's ⋯, no ⧉ Dock in session.html, no
/session window. Until Dock takes app items in its ⋯ menu (need 14), the
task's actions stay in the action bar's ⋯ and the PO's two in its header's ⋯,
both marked data-interim="dock-menu".

In headless Chrome over CDP, against a hub in a thread serving the pages, with
a project that has a PO (Motors) and a task in it:

* a desktop (1440×900): the open task's middle has a title bar with ⋯, and no
  ⧉ Pop out; the action bar's ⋯ is the one interim control;
* ⋯ › View Mode › Window: the task panel is in the panel's window, with its
  chat, and the window is named "Brakes that squeal · Motors"; no PO chat of
  the window's own; the interim ⋯ there opens its menu in that window;
* × there closes the task: the window shows the PO's chat; a row clicked in
  the list puts the task back in the window;
* closing the window hides the panel (the task waits, parked); Panels reopens
  the window; its ⋯ › View Mode › Dock Pinned brings it back to the middle;
* Maximise and Restore, Float and Dock Pinned again; Move To is not offered;
* every action of the model is in the bar's ⋯;
* the PO's conversation: its ⋯ holds Switch agent and the PO's task only; the
  panel's ⋯ › Window pops the PO chat out (a chat of the window's own);
* a phone (430×932): no ⧉ Pop out, no ⋯ over the Chat tab (narrow dock), the
  interim ⋯ present.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import re
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
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8")
ACTIONS = (ROOT / "static" / "actions.js").read_text(encoding="utf-8")


CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
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
// Where the task panel and the conversation's panel are, and what is over them.
const STATE = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const d = PD.dock, ph = PD.els['po-chat'], dp = pdById('detail-panel');
  const w = d ? d.popWindow('po-chat') : null;
  const docs = pdHostDocs();
  const count = sel => docs.reduce((n, x) => n + x.querySelectorAll(sel).length, 0);
  const head = document.querySelector('#po-dock .dk-stack:has(> .dk-body > .pd-chat) > .dk-head');
  const menuBtn = head && head.querySelector('[data-dk-act="menu"]');
  const frame = ph.querySelector('iframe.dp-session');
  const bar = dp && dp.querySelector('.am-bar');
  const r = SELECTED_SID ? rowBySid(SELECTED_SID) : null;
  const model = r ? SessionActions.sessionActions(actionState(r, !!r.isLive), actionEnv()) : null;
  return { dock: !!d, mode: d ? d.viewMode('po-chat') : null, out: d ? d.isOut('po-chat') : null, visible: d ? d.isVisible('po-chat') : null,
    max: d ? !!d.maximised() : null, narrow: d ? d.narrow() : null,
    headShown: !!head && getComputedStyle(head).display !== 'none' && head.getBoundingClientRect().height > 0, head: box(head), menuBtn: !!menuBtn,
    anyMenuBtn: count('#po-dock [data-dk-act="menu"]'),
    popout: count('.dp-popout'), interimTask: count('.am-more[data-interim="dock-menu"]'), interimPo: count('[data-po="more"][data-interim="dock-menu"]'),
    sid: SELECTED_SID || null, open: document.body.classList.contains('detail-open'), task: pdTask(),
    dpWhere: !dp ? null : dp.ownerDocument === document ? 'page' : w && dp.ownerDocument === w.document ? 'window' : 'elsewhere',
    dpInPanel: !!dp && dp.parentNode === ph, dpParked: !!dp && !!dp.closest('.dk-parking'), dpHome: !!dp && dp.parentNode === document.getElementById('detail-panel-resize').parentNode,
    phWhere: ph.ownerDocument === document ? 'page' : w && ph.ownerDocument === w.document ? 'window' : 'elsewhere',
    frame: frame ? frame.dataset.room : null, own: !!ph.querySelector('iframe.pd-own'),
    winTitle: w ? w.document.title : null, winBody: w ? [...w.document.body.classList] : null, docked: document.body.classList.contains('dp-docked'),
    poMenu: [...document.querySelectorAll('#po-panel .po-menu [data-po]')].map(b => b.dataset.po),
    barActs: bar ? [...bar.querySelectorAll('.am-item')].map(b => b.dataset.act) : null,
    modelActs: model ? model.groups.flat().map(i => i.id) : null,
    barBox: box(bar), dp: box(dp), float: box(document.querySelector('#po-dock .dk-float')),
    vw: innerWidth, scrollW: document.documentElement.scrollWidth };
})()`;
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
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    // The panel's own window (the library's popout.html), as a picture.
    const shotWin = async (name) => {
      if (!A.shots) return;
      const { targetInfos } = await c.send('Target.getTargets');
      const t = targetInfos.find(x => x.type === 'page' && /popout\.html/.test(x.url));
      if (!t) return;
      const { sessionId: s2 } = await c.send('Target.attachToTarget', { targetId: t.targetId, flatten: true });
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1000, height: 760, deviceScaleFactor: 1, mobile: false }, s2);
      await sleep(400);
      const r = await c.send('Page.captureScreenshot', { format: 'png' }, s2);
      fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64'));
      await c.send('Target.detachFromTarget', { sessionId: s2 });
    };
    // A real click, as a person's.
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready();
    return { evalIn, until, shot, shotWin, click, ready, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, proj) => p.evalIn(`(() => { try { ['cd-list-dock', 'cd-list-dock-axis', 'cd-tool-strip', 'cd-tool-open'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
  const pdReady = p => p.until('!!PD.dock && document.body.classList.contains("po-dock") && !!SW_EL.querySelector(".sw-row")', 30000);
  const row = `.sw-row[data-room="${A.task}"]`;
  // Dock's title bar: ⋯, then a submenu (View Mode: 'mode'), then an item, with real clicks.
  const menuPick = async (p, head, sub, item) => {
    await p.click(`${head} [data-dk-act="menu"]`);
    await sleep(200);
    if (!(await p.evalIn('!!document.querySelector(".dk-menu.dk-options")'))) throw new Error('no menu under ' + head);
    if (sub) await p.click(`.dk-menu.dk-options [data-dk-sub="${sub}"]`);
    await p.click(`.dk-menu.${sub ? 'dk-submenu' : 'dk-options'} [data-dk-menu="${item}"]`);
    await sleep(500);
  };
  // The same from inside the panel's window.
  const winPick = (p, sub, item) => p.evalIn(`(() => {
    const d = PD.dock.popWindow('po-chat').document;
    d.querySelector('[data-dk-act="menu"]').click();
    ${sub ? `d.querySelector('[data-dk-sub="${sub}"]').click();` : ''}
    d.querySelector('[data-dk-menu="${item}"]').click();
    return 0; })()`);
  const HEAD = '#po-dock .dk-stack:has(> .dk-body > .pd-chat)', FLOAT = '#po-dock .dk-float';
  const inWindow = '!!PD.dock.popWindow("po-chat") && pdById("detail-panel").ownerDocument === PD.dock.popWindow("po-chat").document && !!PD.els["po-chat"].querySelector("iframe.dp-session")';
  try {
    const p = await page(1440, 900);
    await go(p, A.proj); await pdReady(p); await sleep(600);
    out.po = await p.evalIn(STATE);
    // The task opens in the middle, which has a title bar now.
    await p.click(row);
    await p.until('!!SELECTED_SID && pdTask() && !!PD.els["po-chat"].querySelector("iframe.dp-session")', 15000); await sleep(600);
    out.docked = await p.evalIn(STATE);
    await p.shot('task-1440-docked');
    // The middle's ⋯: View Mode, no Move To, Maximise, Hide.
    await p.click(`${HEAD} [data-dk-act="menu"]`); await sleep(200);
    out.menu = await p.evalIn(`(() => { const m = document.querySelector('.dk-menu.dk-options'); return m ? { subs: [...m.querySelectorAll('[data-dk-sub]')].map(b => b.dataset.dkSub), items: [...m.querySelectorAll('[data-dk-menu]')].map(b => b.dataset.dkMenu) } : null; })()`);
    await p.click('.dk-menu.dk-options [data-dk-sub="mode"]'); await sleep(200);
    out.modes = await p.evalIn(`[...document.querySelectorAll('.dk-menu.dk-submenu [data-dk-menu]')].map(b => b.dataset.dkMenu)`);
    await p.shot('task-1440-menu');
    // Window: the task goes with the panel.
    await p.click('.dk-menu.dk-submenu [data-dk-menu="mode:window"]');
    await p.until(inWindow, 15000); await sleep(800);
    out.window = await p.evalIn(STATE);
    await p.shotWin('task-window');
    await p.shot('task-1440-window-page');
    // The interim ⋯ opens its menu in the window.
    out.interimMenu = await p.evalIn(`(async () => {
      const w = PD.dock.popWindow('po-chat'), d = w.document;
      const more = d.querySelector('.am-more[data-interim="dock-menu"]');
      if (!more) return { more: false };
      more.click(); await new Promise(r => setTimeout(r, 300));
      const m = d.querySelector('.am-menu:not([hidden])');
      const res = { more: true, open: !!m, inWindow: !!m && m.ownerDocument === d && m.parentNode === d.body, inPage: !!document.querySelector('.am-menu:not([hidden])'),
        acts: m ? [...m.querySelectorAll('.am-item')].map(b => b.dataset.act) : [], title: more.title, expanded: more.getAttribute('aria-expanded') };
      const r = m && m.getBoundingClientRect(); if (r) res.box = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height), w.innerWidth, w.innerHeight];
      return res; })()`);
    await p.shotWin('task-window-menu');
    await p.evalIn('SessionActions.closeMenu(); 0');
    // × there closes the task: the window shows the PO's chat.
    await p.evalIn(`PD.dock.popWindow('po-chat').document.querySelector('.dp-close').click(); 0`);
    await p.until('!SELECTED_SID && !!PD.els["po-chat"].querySelector("iframe.pd-own")', 10000); await sleep(500);
    out.closedThere = await p.evalIn(STATE);
    await p.shotWin('po-window');
    // A row clicked in the list puts the task in the window again.
    await p.click(row);
    await p.until(inWindow, 15000); await sleep(600);
    out.reopened = await p.evalIn(STATE);
    // Closing the window hides the panel; the task waits. Panels reopens it.
    await p.evalIn('PD.dock.popWindow("po-chat").close(); 0');
    await p.until('!PD.dock.isOut("po-chat") && !PD.dock.isVisible("po-chat")', 10000); await sleep(500);
    out.hidden = await p.evalIn(STATE);
    await p.shot('task-1440-hidden');
    // A task opened from the list while the panel is hidden brings its window back.
    await p.evalIn('closeDetail(); 0'); await sleep(300);
    out.hiddenClosed = await p.evalIn(STATE);
    await p.click(row);
    await p.until(inWindow, 15000); await sleep(600);
    out.hiddenRowOpens = await p.evalIn(STATE);
    await p.evalIn('PD.dock.popWindow("po-chat").close(); 0');
    await p.until('!PD.dock.isOut("po-chat") && !PD.dock.isVisible("po-chat")', 10000); await sleep(500);
    await p.click('.pd-panels'); await p.click('[data-pd-toggle="po-chat"]');
    await p.until(inWindow, 15000); await sleep(600);
    out.reopenedByPanels = await p.evalIn(STATE);
    // The window's ⋯ › View Mode › Dock Pinned: back in the middle.
    await p.evalIn('pdMenuClose(false); 0');
    await winPick(p, 'mode', 'mode:pinned');
    await p.until('PD.dock.viewMode("po-chat") === "pinned" && pdById("detail-panel").ownerDocument === document && pdById("detail-panel").parentNode === PD.els["po-chat"]', 15000); await sleep(600);
    out.pinnedAgain = await p.evalIn(STATE);
    // Maximise and Restore.
    await menuPick(p, HEAD, null, 'max');
    out.max = await p.evalIn(STATE);
    await p.shot('task-1440-max');
    await menuPick(p, HEAD, null, 'max');
    out.restored = await p.evalIn(STATE);
    // Float, and Dock Pinned again.
    await menuPick(p, HEAD, 'mode', 'mode:float');
    out.float = await p.evalIn(STATE);
    await p.shot('task-1440-float');
    await menuPick(p, FLOAT, 'mode', 'mode:pinned');
    out.pinnedFromFloat = await p.evalIn(STATE);
    // − minimises; the title bar's click brings it back.
    await p.click(`${HEAD} [data-dk-act="hide"]`); await sleep(400);
    out.min = await p.evalIn(`(() => { const s = document.querySelector(${JSON.stringify(HEAD)}); return { min: !!s && s.classList.contains('dk-min'), h: s ? Math.round(s.getBoundingClientRect().height) : null, task: pdTask() }; })()`);
    await p.click(`${HEAD} [data-dk-act="hide"]`); await sleep(400);
    out.unmin = await p.evalIn(STATE);
    // The PO's conversation: its ⋯, and the panel's Window with a chat of its own.
    await p.evalIn('closeDetail(); 0'); await sleep(500);
    out.poAgain = await p.evalIn(STATE);
    await menuPick(p, HEAD, 'mode', 'mode:window');
    await p.until('!!PD.dock.popWindow("po-chat") && !!PD.els["po-chat"].querySelector("iframe.pd-own")', 15000); await sleep(600);
    out.poWindow = await p.evalIn(STATE);
    await winPick(p, 'mode', 'mode:pinned');
    await p.until('PD.dock.viewMode("po-chat") === "pinned" && PD.els["po-chat"].ownerDocument === document', 15000); await sleep(400);
    out.poBack = await p.evalIn(STATE);
    await p.close();
    // A phone: the Chat tab, no ⋯ over it, no ⧉ Pop out, the interim ⋯.
    const q = await page(430, 932, true);
    await go(q, A.proj); await pdReady(q); await sleep(600);
    await q.evalIn(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.task)}); openDetail(r.sessionId); return 0; })()`);
    await q.until('!!SELECTED_SID && pdTask() && !!PD.els["po-chat"].querySelector("iframe.dp-session")', 15000); await sleep(800);
    out.phone = await q.evalIn(STATE);
    await q.shot('task-430-phone');
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class TheWiring(unittest.TestCase):
    """What the page asks of the library, and what is gone (static checks)."""

    def test_the_conversation_has_the_tools_controls_but_is_not_moved(self):
        can = INDEX[INDEX.index("can: (id, a) => (isPhone() && !(id === 'po-chat' && a === 'hide' && pdTask()))"):]
        can = can[:can.index("popName:")]
        self.assertIn("(a === 'float' || a === 'pop' || a === 'max' || a === 'min' || (a === 'hide' && PD.dock?.viewMode(id) === 'window'))", can)
        self.assertNotIn("a === 'move'", can)
        self.assertNotIn("#po-dock:not(.dk-narrow) .dk-stack:has(> .dk-body > .pd-chat) > .dk-head { display: none; }", INDEX)

    def test_the_pos_menu_and_the_chats_fit_follow_the_panel_into_its_window(self):
        # Review 1: the PO's header may be in the conversation's window; its menu closes from there too.
        self.assertIn("for (const m of pdQueryAll('#po-panel .po-menu:not([hidden])')) poMenuOpen(m.closest('.po-head'), false);", INDEX)
        self.assertIn("const inMenu = menu.contains(head.ownerDocument.activeElement), focus = focusKeyIn(head);", INDEX)
        # The chat's ResizeObserver lives in this page; the window's own resize refits it while this tab is in the background.
        self.assertIn("pdOnResize(() => { if (pdById('detail-panel')) fitLiveChat(); });", INDEX)

    def test_the_old_controls_are_gone(self):
        for old in ("dp-popout", "openSessionWindow", "dock-session", 'data-po="popout"', "Open in a floating window"):
            self.assertNotIn(old, INDEX, old)
        for old in ('id="dock"', "dock-session", "window.opener.postMessage"):
            self.assertNotIn(old, SESSION, old)

    def test_the_interim_controls_are_marked_and_the_switch_is_in_one_place(self):
        self.assertEqual(INDEX.count("Dock need 14 (begin)"), 1)
        self.assertEqual(INDEX.count("Dock need 14 (end)"), 1)
        self.assertIn("const DOCK_MENU_INTERIM = 'More actions · for now here: they move into the panel’s ⋯ menu once Dock takes app items';", INDEX)
        self.assertIn("{ interim: DOCK_MENU_INTERIM }", INDEX)
        self.assertIn('data-po="more" data-interim="dock-menu"', INDEX)
        self.assertIn("function poMenuItems(row)", INDEX)
        self.assertIn("""${interim ? ' data-interim="dock-menu"' : ''}""", ACTIONS)

    def test_the_menu_opens_in_the_triggers_window(self):
        self.assertIn("trigger.ownerDocument.body.appendChild(menu);", ACTIONS)
        self.assertIn("const winOf = el => (el && el.ownerDocument && el.ownerDocument.defaultView) || window;", ACTIONS)
        self.assertIn("popupPlace(rect, size, viewport(winOf(el)), o)", ACTIONS)
        # Its document listeners are among the page's, so a panel's window gets them.
        self.assertLess(INDEX.index("window.DOC_LISTENERS = [];"), INDEX.index('<script src="/static/actions.js"></script>'))

    def test_the_task_follows_the_panel_and_the_window_is_named_after_it(self):
        self.assertIn("const DP_EL = pdById('detail-panel')", INDEX)
        self.assertNotIn("on = !!on && ph.ownerDocument === document;", INDEX)
        self.assertIn("if (!out || pdTask()) { if (own) own.remove(); return; }", INDEX)
        self.assertIn("popTitle: p => `${p.id === 'po-chat' ? pdChatTitle() : p.title}", INDEX)
        self.assertIn("function pdPopTitle()", INDEX)
        self.assertIn("bodyMo.observe(document.body, { attributes: true, attributeFilter: ['class'] });", INDEX)
        # Nothing looks for the task panel in this document alone any more.
        self.assertEqual(re.findall(r"document\.(?:getElementById\('detail-panel'\)|querySelector\('#detail-panel)", INDEX), [])

    def test_a_list_row_opens_the_task_in_the_page(self):
        self.assertIn('<button class="room-open" data-room="${esc(r.roomId)}" title="Open">▶ Open</button>', INDEX)
        self.assertIn("const row = ALL_ROWS.find(x => x.roomId === roomOpen.dataset.room);\n    if (row) openDetail(row.sessionId);", INDEX)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheTaskPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-tdp-", ignore_cleanup_errors=True)
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
        members = [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                   {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        po = chatroom.create_room("PO talk", members)
        dashboard.assign_session_project(po["id"], cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, po["id"])
        assert ok, why
        chatroom.post_message(po["id"], "user", "Hello PO", to="claude")
        task = chatroom.create_room("Brakes that squeal", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
        dashboard.assign_session_project(task["id"], cls.proj)
        cls.task = task["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "shots": shots}
        script = base / "tdp_cdp.js"
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

    def task_in(self, g, where, what):
        self.assertTrue(g["sid"] and g["open"] and g["task"], f"{what}: the task is open")
        self.assertEqual((g["dpWhere"], g["dpInPanel"], g["phWhere"]), (where, True, where), f"{what}: the task panel is in the conversation's panel, {where}")
        self.assertEqual(g["frame"], self.task, f"{what}: with its chat")
        self.assertFalse(g["own"], f"{what}: no PO chat of the window's own while the task is there")
        self.assertEqual(g["popout"], 0, f"{what}: no ⧉ Pop out")
        self.assertEqual(g["interimTask"], 1, f"{what}: the action bar's ⋯ is the one interim control")

    def test_the_open_task_has_the_middles_title_bar_and_no_pop_out_of_its_own(self):
        g = self.got["docked"]
        self.task_in(g, "page", "docked")
        self.assertTrue(g["headShown"] and g["menuBtn"], "the middle's title bar with its ⋯")
        self.assertEqual((g["mode"], g["docked"]), ("pinned", True))
        self.assertLessEqual(g["scrollW"], g["vw"], "no sideways scroll")
        m = self.got["menu"]
        self.assertEqual(m["subs"], ["mode"], "View Mode, no Move To: the middle is on no side")
        self.assertIn("max", m["items"])
        self.assertIn("hide", m["items"])
        self.assertEqual(self.got["modes"], ["mode:pinned", "mode:float", "mode:window"], "the conversation docks, floats or has its window; no strip for it")

    def test_window_mode_takes_the_task_with_it(self):
        g = self.got["window"]
        self.assertEqual((g["mode"], g["out"]), ("window", True))
        self.task_in(g, "window", "window")
        self.assertEqual(g["winTitle"], "Brakes that squeal · Motors", "the window is named after the task (a numbered task would say #12 first)")
        self.assertIn("dp-docked", g["winBody"], "the page's body classes hold in the window (the panel's rules key on them)")
        self.assertIn("mid", g["winBody"])
        i = self.got["interimMenu"]
        self.assertTrue(i["more"] and i["open"] and i["inWindow"], i)
        self.assertFalse(i["inPage"], "the menu is in the window, not under this page")
        self.assertEqual(i["expanded"], "true")
        self.assertTrue(i["title"].startswith("More actions · for now here"), i["title"])
        self.assertIn("rename", i["acts"])
        self.assertIn("delete", i["acts"])
        x, y, w, h, vw, vh = i["box"]
        self.assertTrue(x >= 0 and y >= 0 and x + w <= vw and y + h <= vh, f"placed inside the window: {i['box']}")

    def test_closing_the_task_there_shows_the_po_chat_and_a_row_brings_it_back(self):
        g = self.got["closedThere"]
        self.assertIsNone(g["sid"])
        self.assertEqual((g["mode"], g["phWhere"], g["own"]), ("window", "window", True), "the window shows the PO's chat")
        self.assertEqual((g["dpWhere"], g["dpHome"]), ("page", True), "the task panel is home")
        self.assertEqual(g["winTitle"], "PO chat · Motors")
        r = self.got["reopened"]
        self.task_in(r, "window", "reopened from the list")
        self.assertEqual(r["winTitle"], "Brakes that squeal · Motors")

    def test_closing_the_window_hides_the_panel_and_panels_reopens_it(self):
        g = self.got["hidden"]
        self.assertEqual((g["visible"], g["out"], g["mode"]), (False, False, "window"))
        self.assertTrue(g["task"] and g["sid"], "the task is still open")
        self.assertEqual((g["dpWhere"], g["dpInPanel"], g["dpParked"]), ("page", True, True), "it waits in the parked panel")
        c = self.got["hiddenClosed"]
        self.assertEqual((c["sid"], c["visible"], c["dpHome"]), (None, False, True), "closed while hidden: the panel stays hidden, the task panel goes home")
        o = self.got["hiddenRowOpens"]
        self.task_in(o, "window", "a row clicked while the panel is hidden")
        self.assertEqual((o["mode"], o["out"]), ("window", True), "its window again")
        r = self.got["reopenedByPanels"]
        self.task_in(r, "window", "reopened by Panels")
        p = self.got["pinnedAgain"]
        self.assertEqual((p["mode"], p["out"]), ("pinned", False))
        self.task_in(p, "page", "Dock Pinned from the window")
        self.assertTrue(p["headShown"])

    def test_maximise_float_and_minimise(self):
        m = self.got["max"]
        self.assertTrue(m["max"])
        self.task_in(m, "page", "maximised")
        self.assertGreaterEqual(m["dp"]["w"], m["vw"] - 310, "the task takes the dock's whole width")
        r = self.got["restored"]
        self.assertFalse(r["max"])
        f = self.got["float"]
        self.assertEqual(f["mode"], "float")
        self.task_in(f, "page", "floating")
        self.assertTrue(f["float"] and f["float"]["w"] > 0, "in a float of the dock")
        self.assertEqual(self.got["pinnedFromFloat"]["mode"], "pinned")
        mn = self.got["min"]
        self.assertTrue(mn["min"] and mn["task"], mn)
        self.assertEqual(self.got["unmin"]["mode"], "pinned")
        self.task_in(self.got["unmin"], "page", "after −")

    def test_every_action_of_the_model_is_in_the_bars_menu(self):
        g = self.got["docked"]
        self.assertTrue(g["barActs"] and g["modelActs"], g)
        self.assertEqual(g["barActs"], g["modelActs"])
        for act in ("rename", "delete", "agents"):
            self.assertIn(act, g["barActs"])

    def test_the_po_conversation(self):
        for what in ("po", "poAgain"):
            g = self.got[what]
            self.assertIsNone(g["sid"], what)
            self.assertEqual(g["poMenu"], ["switch", "open"], f"{what}: Switch agent and the PO's task; Pop out is the panel's")
            self.assertEqual((g["popout"], g["interimPo"]), (0, 1), what)
            self.assertTrue(g["headShown"] and g["menuBtn"], f"{what}: the middle's title bar")
        self.assertEqual(self.got["po"]["interimTask"], 0, "no task panel drawn yet")
        w = self.got["poWindow"]
        self.assertEqual((w["mode"], w["phWhere"], w["own"], w["winTitle"]), ("window", "window", True, "PO chat · Motors"))
        self.assertEqual(w["interimPo"], 1, "the PO's header, with its ⋯, is in the window")
        b = self.got["poBack"]
        self.assertEqual((b["mode"], b["phWhere"], b["own"]), ("pinned", "page", False))

    def test_a_phone(self):
        g = self.got["phone"]
        self.assertTrue(g["narrow"], "a narrow dock")
        self.task_in(g, "page", "phone")
        self.assertEqual(g["anyMenuBtn"], 0, "no ⋯ over a phone's tabs")
        self.assertLessEqual(g["scrollW"], g["vw"])


if __name__ == "__main__":
    unittest.main()
