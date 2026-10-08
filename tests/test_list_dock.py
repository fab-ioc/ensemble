"""#137 (P84): the task list on the left is a Dock panel.

In headless Chrome over CDP, against a hub in a thread serving the pages, with
a project that has a PO (Motors), a task in it, and a session in no project
(Unassigned):

* a desktop's list is a panel of its own dock (#list-dock), docked at the left
  and pinned: today's column, 300px, the page beside it, the dock's ⋯ and −
  over its own head's free end, nothing covered;
* in each View Mode (Dock Pinned, Dock Unpinned, Undock, Float, Window) a row
  still opens its conversation in the middle; once the list has a strip place,
  its active button stays there in Float, Window and Dock Pinned as well as the
  two unpinned modes, and the middle takes the room left by that strip;
* slid out, it goes back once a row is opened;
* popped out into its own window, a row clicked there opens the task in the
  main window, and Unassigned still folds there; closing hides it, Panels
  reopens its window, and the window's View Mode brings it back;
* Move To › Right puts it at the right, the page following;
* its width and mode are kept over a reload;
* a phone keeps the list home (no list dock), also when a desktop window is
  made phone-sized and back.

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
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")


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
// Where the list and the middle are.
const GEOM = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const d = LD.dock, sw = SW_EL, mid = document.querySelector('main');
  const strip = document.querySelector('#list-dock .dk-strip');
  const stripBtn = document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="list"]');
  const menu = document.querySelector('#list-dock [data-dk-act="menu"]'), by = sw.querySelector('#sw-proj');
  const cs = getComputedStyle(document.body);
  return { visible: d ? d.isVisible('list') : null, notice: !!document.querySelector('#list-dock .dk-outnote'), dock: !!d, mode: d ? d.viewMode('list') : null, side: d ? d.side('list') : null, fly: d ? d.flyOpen() : null, out: d ? d.isOut('list') : null,
    here: sw.ownerDocument === document, home: sw.parentNode === document.body, rootHidden: document.getElementById('list-dock').hidden,
    list: sw.ownerDocument === document ? box(sw) : null, main: box(mid), dockHost: box(document.getElementById('po-dock-host')),
    strip: box(strip), stripBtn: box(stripBtn), stripActive: !!stripBtn && stripBtn.classList.contains('on'),
    stripExpanded: stripBtn && stripBtn.getAttribute('aria-expanded'), stripPressed: stripBtn && stripBtn.getAttribute('aria-pressed'),
    flyBox: box(document.querySelector('#list-dock .dk-flyout.open')), stripSide: strip ? [...strip.classList].filter(c => /^dk-strip-(left|right|top|bottom)$/.test(c)).join('') : null,
    listW: cs.getPropertyValue('--list-w').trim(), listR: cs.getPropertyValue('--list-r').trim(),
    menuBtn: box(menu), bySel: sw.ownerDocument === document ? box(by) : null,
    sid: SELECTED_SID || null, open: document.body.classList.contains('detail-open'),
    vw: innerWidth, scrollW: document.documentElement.scrollWidth, saved: localStorage.getItem('cd-list-dock') };
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
    // A real click, as a person's.
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const key = async (k, code) => {
      for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code, windowsVirtualKeyCode: code === 'ArrowRight' ? 39 : 0 }, sessionId);
    };
    const ready = () => until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await ready();
    return { evalIn, until, shot, click, key, ready, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = (p, proj, keep) => p.evalIn(`(() => { try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {} // its rows are a project's tasks: the grouped view (#189)
    if (!${!!keep}) try { ['cd-list-dock', 'cd-list-dock-axis', 'cd-tool-strip', 'cd-tool-open'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SW_DONE_OPEN = true; SELECTED_PROJECT = ${JSON.stringify(proj)}; PROJECT_TAB = 'tasks'; SB_DEST = ''; renderRows(); return 0; })()`);
  const ldReady = p => p.until('!!LD.dock && !!PD.dock && document.body.classList.contains("po-dock") && !!SW_EL.querySelector(".sw-row")', 30000);
  const row = `.sw-row[data-room="${A.task}"]`;
  const row2 = `.sw-row[data-room="${A.task2}"]`;
  const mouseTo = async (p, sel) => {
    const [x, y] = await p.evalIn(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x + 2, y }, p.sessionId);
  };
  const expansion = p => p.evalIn(`(() => {
    const e = document.querySelector('.sw-expanded'), r = document.querySelector(${JSON.stringify(row)});
    const b = e && e.getBoundingClientRect(), a = r && r.getBoundingClientRect();
    const t = r.querySelector('.sw-title'), mid = r.getBoundingClientRect();
    return { shown: !!e, full: !!e && e.querySelector('.sw-title').textContent === t.textContent,
      box: b && { left: b.left, right: b.right, width: b.width }, row: a && { left: a.left, right: a.right, width: a.width },
      z: e && getComputedStyle(e).zIndex, pageWidth: document.documentElement.scrollWidth, viewport: innerWidth,
      hit: b && document.elementFromPoint(b.right - 4, b.top + 8)?.closest('.sw-expanded') === e,
      allowed: swExpandAllowed(r), timer: !!SW_EXPAND_TIMER, titleWidth: [t.scrollWidth, t.clientWidth],
      mouseHit: document.elementFromPoint(mid.left + mid.width / 2, mid.top + mid.height / 2)?.closest('.sw-row')?.dataset.room || null };
  })()`);
  // Dock v0.5's title bar: ⋯, then a submenu (View Mode: 'mode', Move To: 'side'), then an item, with real clicks.
  const menuPick = async (p, head, sub, item) => {
    await p.click(`${head} [data-dk-act="menu"]`);
    await sleep(200);
    if (!(await p.evalIn('!!document.querySelector(".dk-menu.dk-options")'))) throw new Error('no menu: ' + JSON.stringify(await p.evalIn(`(() => { const b = document.querySelector(${JSON.stringify(head + ' [data-dk-act="menu"]')}); const r = b.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return { r: [r.left, r.top, r.width, r.height], hit: e && (e.outerHTML || '').slice(0, 200), menus: [...document.querySelectorAll('.dk-menu')].map(m => m.className) }; })()`)));
    await p.click(`.dk-menu.dk-options [data-dk-sub="${sub}"]`);
    await p.click(`.dk-menu.dk-submenu [data-dk-menu="${item}"]`);
    await sleep(500);
  };
  const HEAD = '#list-dock .dk-stack:has(#switcher)', FLY = '#list-dock .dk-flyout.open', FLOAT = '#list-dock .dk-float';
  // A row opens its task in the middle: closed first, then clicked.
  const opens = async (p, sel) => {
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(300);
    const before = await p.evalIn(`(() => { const r = document.querySelector(${JSON.stringify(sel || row)}).getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2; const h = document.elementFromPoint(x, y); return { x, y, hit: h && h.outerHTML.slice(0, 250), overlay: !!document.querySelector('.sw-expanded') }; })()`);
    await p.click(sel || row);
    await p.until('!!SELECTED_SID && document.body.classList.contains("detail-open")', 10000).catch(() => null);
    await sleep(400);
    return { ...(await p.evalIn(GEOM)), before };
  };
  try {
    const p = await page(1440, 900);
    await go(p, A.proj); await ldReady(p); await sleep(600);
    out.pinned = await p.evalIn(GEOM);
    await p.shot('list-1440-pinned');
    out.expand = [];
    for (const width of [1280, 1728]) for (const theme of ['light', 'dark', 'fjord']) {
      await c.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: width / 2, y: 80 }, p.sessionId);
      await sleep(40);
      await p.evalIn(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}; swExpandHide(); SW_EXPAND_LAST = 0; 0`);
      await sleep(120);
      await mouseTo(p, row);
      await sleep(160);
      const early = await expansion(p);
      await sleep(220);
      const long = await expansion(p);
      await mouseTo(p, row2); await sleep(60);
      const next = await p.evalIn(`(() => { const r = document.querySelector(${JSON.stringify(row2)}), t = r.querySelector('.sw-title'), b = r.getBoundingClientRect(), h = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2); return { shown: document.querySelector('.sw-expanded')?.dataset.room || null, truncated: t.scrollWidth > t.clientWidth + 1, hit: h?.closest('.sw-row')?.dataset.room || null, timer: !!SW_EXPAND_TIMER, allowed: swExpandAllowed(r) }; })()`);
      await mouseTo(p, '#sw-list .sw-row[data-po]'); await sleep(50);
      const short = await expansion(p);
      out.expand.push({ width, theme, early, long, short, next });
    }
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId);
    await p.evalIn(`(() => { const t = document.querySelector(${JSON.stringify(row)}).querySelector('.sw-title'); window.__wrapTitle = t.innerHTML; t.textContent += ' detailed inspection across every carriage before the winter timetable'.repeat(40); swExpandHide(); SW_EXPAND_LAST = 0; return 0; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 800, y: 80 }, p.sessionId);
    await mouseTo(p, row); await sleep(380);
    out.expandWrapped = await p.evalIn(`(() => { const e = document.querySelector('.sw-expanded'), n = document.querySelector(${JSON.stringify(row2)}); return { shown: !!e, height: e?.getBoundingClientRect().height || 0, rowHeight: document.querySelector(${JSON.stringify(row)}).getBoundingClientRect().height, coversNext: !!e && e.getBoundingClientRect().bottom > n.getBoundingClientRect().top + n.getBoundingClientRect().height / 2 }; })()`);
    await mouseTo(p, row2); await sleep(60);
    out.expandWrappedNext = await p.evalIn(`document.querySelector('.sw-expanded')?.dataset.room === ${JSON.stringify(A.task2)}`);
    await p.click('.sw-expanded');
    out.expandWrappedClick = await p.evalIn(`SELECTED_SID === document.querySelector(${JSON.stringify(row2)}).dataset.sid`);
    await p.evalIn(`closeDetail(); const t = document.querySelector(${JSON.stringify(row)}).querySelector('.sw-title'); t.innerHTML = window.__wrapTitle; swExpandHide(); 0`);
    await mouseTo(p, row); await sleep(370);
    await p.click('.sw-expanded');
    out.expandClick = await p.evalIn(`SELECTED_SID === document.querySelector(${JSON.stringify(row)}).dataset.sid`);
    await p.evalIn('closeDetail(); document.querySelector("#sw-hover-names").click(); 0');
    await mouseTo(p, row); await sleep(400);
    out.expandOff = await expansion(p);
    await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready();
    await go(p, A.proj, true); await ldReady(p);
    out.expandPersist = await p.evalIn('!document.querySelector("#sw-hover-names").checked');
    await p.evalIn('document.querySelector("#sw-hover-names").click(); 0');
    await p.key('ArrowDown', 'ArrowDown');
    out.expandFocus = await p.evalIn(`(() => { const r = document.querySelector(${JSON.stringify(row)}); r.focus(); const shown = !!SW_EXPAND && SW_EXPAND.row === r && SW_EXPAND.copy.classList.contains('sw-expand-focus'); swExpandHide(); r.blur(); return shown; })()`);
    await mouseTo(p, row); await sleep(370);
    await p.click(`${HEAD} [data-dk-act="menu"]`);
    out.expandMenu = await p.evalIn('!!document.querySelector(".dk-menu.dk-options") && !document.querySelector(".sw-expanded")');
    await p.key('Escape', 'Escape');
    out.pinnedOpens = await opens(p);
    // Dock Unpinned: the strip; a click slides it out beside the middle; a row opens its task and it goes back.
    await menuPick(p, HEAD, 'mode', 'mode:unpinned');
    out.unpinnedJust = await p.evalIn(GEOM);
    // A click elsewhere (the conversation's header) puts it back on its strip.
    await p.click('#po-dock-host'); await sleep(500);
    out.unpinned = await p.evalIn(GEOM);
    await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500);
    out.unpinnedOut = await p.evalIn(GEOM);
    await p.shot('list-1440-unpinned-out');
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(300);
    if (!(await p.evalIn('LD.dock.flyOpen()'))) { await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500); }
    await p.click(row);
    await p.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(500);
    out.unpinnedOpens = await p.evalIn(GEOM);
    // Undock: over the middle.
    await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500);
    await menuPick(p, FLY, 'mode', 'mode:undock');
    if (!(await p.evalIn('LD.dock.flyOpen()'))) { await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500); }
    out.undockOut = await p.evalIn(GEOM);
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(300);
    if (!(await p.evalIn('LD.dock.flyOpen()'))) { await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500); }
    await p.click(row);
    await p.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(500);
    out.undockOpens = await p.evalIn(GEOM);
    // Float: a window in the page; the middle has the whole width.
    await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500);
    await menuPick(p, FLY, 'mode', 'mode:float');
    out.float = await p.evalIn(GEOM);
    await p.shot('list-1440-float');
    out.floatOpens = await opens(p);
    // Window: its own browser window. A row clicked there opens the task here.
    await menuPick(p, FLOAT, 'mode', 'mode:window');
    await p.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document && !!SW_EL.querySelector(".sw-row")', 15000);
    await sleep(600);
    out.window = await p.evalIn(GEOM);
    out.expandWindow = await p.evalIn(`(() => { const r = SW_EL.querySelector(${JSON.stringify(row)}), t = r.querySelector('.sw-title'); t.textContent += ' additional safety review before the next winter timetable'.repeat(30); swExpandShow(r); const e = SW_EL.ownerDocument.querySelector('.sw-expanded'); const v = SW_EL.ownerDocument.defaultView; const result = { shown: !!e, sameWindow: e?.ownerDocument === SW_EL.ownerDocument, within: !!e && e.getBoundingClientRect().right <= v.innerWidth, full: !!e && e.querySelector('.sw-title').textContent === t.textContent, allowed: swExpandAllowed(r), titleWidth: [t.scrollWidth, t.clientWidth], hover: v.matchMedia('(hover: hover) and (pointer: fine)').matches, width: v.innerWidth }; swExpandHide(); return result; })()`);
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(300);
    await p.evalIn(`SW_EL.querySelector(${JSON.stringify(row)}).click(); 0`);
    await p.until('!!SELECTED_SID', 10000).catch(() => null); await sleep(400);
    out.windowOpens = await p.evalIn(GEOM);
    // Unassigned folds there too, and its row opens its conversation here.
    out.windowUnassigned = await p.evalIn(`(async () => {
      const box = SW_EL.querySelector('details[data-group="unassigned"]');
      if (!box) return { box: false };
      const was = box.open; box.querySelector('summary').click();
      await new Promise(r => setTimeout(r, 300));
      const now = SW_EL.querySelector('details[data-group="unassigned"]').open;
      if (!now) SW_EL.querySelector('details[data-group="unassigned"] summary').click();
      await new Promise(r => setTimeout(r, 300));
      closeDetail(); await new Promise(r => setTimeout(r, 300));
      const r = SW_EL.querySelector('details[data-group="unassigned"] .sw-row[data-room=${JSON.stringify(A.loose)}]');
      if (r) r.click();
      await new Promise(r => setTimeout(r, 600));
      return { box: true, was, now, folded: SW_UN.open, row: !!r, opened: !!SELECTED_SID && (rowBySid(SELECTED_SID) || {}).roomId === ${JSON.stringify(A.loose)} };
    })()`);
    // Closing hides it; Panels reopens Window mode, also after a reload.
    await p.evalIn('LD.dock.popWindow("list").close(); 0');
    await p.until('!LD.dock.isOut("list") && SW_EL.ownerDocument === document', 10000); await sleep(500);
    out.windowBack = await p.evalIn(GEOM);
    await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready();
    await go(p, A.proj, true); await ldReady(p); await sleep(600);
    await p.until('!!LD.dock && !!document.querySelector(".pd-panels")');
    out.hiddenReload = await p.evalIn(GEOM);
    await p.click('.pd-panels'); await p.click('[data-pd-list]');
    await p.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document');
    out.windowReopened = await p.evalIn(GEOM);
    await p.evalIn(`(() => {
      pdMenuClose(false);
      const d = LD.dock.popWindow('list').document;
      d.querySelector('[data-dk-act="menu"]').click();
      d.querySelector('[data-dk-sub="mode"]').click();
      d.querySelector('[data-dk-menu="mode:float"]').click();
      return 0;
    })()`);
    await p.until('LD.dock.viewMode("list") === "float" && SW_EL.ownerDocument === document');
    // Dock Pinned again, then Move To › Right: the page follows; and back to the left.
    await menuPick(p, FLOAT, 'mode', 'mode:pinned');
    out.pinnedAgain = await p.evalIn(GEOM);
    await menuPick(p, HEAD, 'side', 'side:right');
    out.right = await p.evalIn(GEOM);
    await mouseTo(p, row); await sleep(370);
    out.expandRight = await expansion(p);
    await p.evalIn('swExpandHide(); 0');
    const [barX, barY] = await p.evalIn(`(() => { const b = document.querySelector('#list-dock .dk-bar').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: barX, y: barY, button: 'left', clickCount: 1 }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 580, y: barY, button: 'left', buttons: 1 }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 580, y: barY, button: 'left', clickCount: 1 }, p.sessionId);
    await sleep(400);
    await p.evalIn(`(() => { const t = document.querySelector(${JSON.stringify(row)}).querySelector('.sw-title'); window.__rightTitle = t.innerHTML; t.textContent += ' detailed inspection across every carriage before the winter timetable'.repeat(30); swExpandHide(); SW_EXPAND_LAST = 0; return 0; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 400, y: 80 }, p.sessionId);
    await mouseTo(p, row); await sleep(380);
    out.expandWideRight = await expansion(p);
    await p.evalIn(`(() => { const t = document.querySelector(${JSON.stringify(row)}).querySelector('.sw-title'); t.innerHTML = window.__rightTitle; swExpandHide(); return 0; })()`);
    const [wideBarX, wideBarY] = await p.evalIn(`(() => { const b = document.querySelector('#list-dock .dk-bar').getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: wideBarX, y: wideBarY, button: 'left', clickCount: 1 }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: barX, y: wideBarY, button: 'left', buttons: 1 }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: barX, y: wideBarY, button: 'left', clickCount: 1 }, p.sessionId);
    await sleep(400);
    out.rightRestored = await p.evalIn(GEOM);
    await p.shot('list-1440-right');
    out.rightOpens = await opens(p);
    await p.evalIn('if (SELECTED_SID) closeDetail(); 0'); await sleep(300);
    await menuPick(p, HEAD, 'side', 'side:left');
    out.left = await p.evalIn(GEOM);
    // Maximise: the list takes the dock's whole area; Restore: its column again.
    const maxBy = async () => { await p.click(`${HEAD} [data-dk-act="menu"]`); await sleep(200); await p.click('.dk-menu.dk-options [data-dk-menu="max"]'); await sleep(500); };
    await maxBy();
    out.max = await p.evalIn(GEOM);
    await p.shot('list-1440-max');
    await maxBy();
    out.restored = await p.evalIn(GEOM);
    // At the top and at the bottom, unpinned: slid out, it spans the width.
    for (const side of ['top', 'bottom']) {
      await menuPick(p, HEAD, 'side', 'side:' + side);
      out['at_' + side] = await p.evalIn(GEOM);
      await menuPick(p, HEAD, 'mode', 'mode:unpinned');
      if (!(await p.evalIn('LD.dock.flyOpen()'))) { await p.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500); }
      out['fly_' + side] = await p.evalIn(GEOM);
      await p.shot('list-1440-unpinned-' + side);
      await menuPick(p, FLY, 'mode', 'mode:pinned');
      await menuPick(p, HEAD, 'side', 'side:left');
    }
    out.leftAgain = await p.evalIn(GEOM);
    // Wider by the keys on its splitter (16px a press), kept over a reload.
    await p.evalIn(`document.querySelector('#list-dock .dk-bar').focus(); 0`);
    await p.key('ArrowRight', 'ArrowRight'); await p.key('ArrowRight', 'ArrowRight'); await sleep(400);
    out.wider = await p.evalIn(GEOM);
    await p.evalIn(`(() => { const r = document.querySelector(${JSON.stringify(row)}), t = r.querySelector('.sw-title'); window.__resizedTitle = t.innerHTML; t.textContent += ' detailed inspection across every carriage before the winter timetable'.repeat(30); swExpandShow(r); return 0; })()`);
    out.expandResized = await expansion(p);
    await p.evalIn(`(() => { document.querySelector(${JSON.stringify(row)}).querySelector('.sw-title').innerHTML = window.__resizedTitle; swExpandHide(); return 0; })()`);
    // Its resized width comes back after a spell at the top.
    await menuPick(p, HEAD, 'side', 'side:top');
    await menuPick(p, HEAD, 'side', 'side:left');
    out.widerKept = await p.evalIn(GEOM);
    // v0.14 keeps the list's original 300px strip depth across that axis
    // round-trip. Resize it again so the reload check remains about persisting
    // a user-resized dock, independently of the retained strip place.
    await p.evalIn(`document.querySelector('#list-dock .dk-bar').focus(); 0`);
    await p.key('ArrowRight', 'ArrowRight'); await p.key('ArrowRight', 'ArrowRight'); await sleep(400);
    await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready();
    await go(p, A.proj, true); await ldReady(p); await sleep(600);
    out.reloadWide = await p.evalIn(GEOM);
    // And over a reload taken at the top.
    await menuPick(p, HEAD, 'side', 'side:top');
    await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready();
    await go(p, A.proj, true); await ldReady(p); await sleep(600);
    await menuPick(p, HEAD, 'side', 'side:left');
    out.widerKeptReload = await p.evalIn(GEOM);
    // Unpinned, kept over a reload.
    await menuPick(p, HEAD, 'mode', 'mode:unpinned');
    await p.evalIn('location.reload(); 0'); await sleep(1500); await p.ready();
    await go(p, A.proj, true); await ldReady(p).catch(() => null); await p.until('!!LD.dock', 20000); await sleep(600);
    out.reloadUnpinned = await p.evalIn(GEOM);
    // Made phone-sized: the list goes home; wide again, back in its dock.
    await c.send('Emulation.setDeviceMetricsOverride', { width: 400, height: 860, deviceScaleFactor: 1, mobile: true }, p.sessionId); await sleep(800);
    out.toPhone = await p.evalIn(GEOM);
    out.expandTouch = await p.evalIn(`(() => { swExpandShow(SW_EL.querySelector(${JSON.stringify(row)})); return !SW_EXPAND; })()`);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId); await sleep(800);
    out.toDesk = await p.evalIn(GEOM);
    await p.close();
    // A short window: slid out at the top or the bottom, the list is cut to
    // leave the middle 200px, and the middle starts where the list ends.
    const s = await page(1440, 450);
    await go(s, A.proj); await ldReady(s);
    await s.evalIn('LD.dock.reset(); 0'); await sleep(600);   // the browser's layout, from the page before, as it was first
    for (const side of ['top', 'bottom']) {
      await menuPick(s, HEAD, 'side', 'side:' + side);
      await menuPick(s, HEAD, 'mode', 'mode:unpinned');
      if (!(await s.evalIn('LD.dock.flyOpen()'))) { await s.click('#list-dock .dk-strip-btn[data-dk-auto="list"]'); await sleep(500); }
      await sleep(400);
      out['short_' + side] = await s.evalIn(GEOM);
      await s.shot('list-1440x450-unpinned-' + side);
      await menuPick(s, FLY, 'mode', 'mode:pinned');
      await menuPick(s, HEAD, 'side', 'side:left');
    }
    await s.close();
    // A page with no dock for the conversation yet: the list goes into its
    // window, a row clicked there makes that dock; the window stays the list's.
    const h = await page(1440, 900);
    // Nothing to come back to (#135 reopens the last conversation or PO).
    await h.evalIn(`['cd-last-conv', 'cd-po-last'].forEach(k => localStorage.removeItem(k)); location.reload(); 0`); await sleep(1500); await h.ready();
    await go(h, ''); await h.until('!!LD.dock && !PD.dock && !!SW_EL.querySelector(".sw-row")', 30000); await sleep(400);
    await h.evalIn('LD.dock.popOut("list"); 0');
    await h.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document && !!SW_EL.querySelector(".sw-row")', 15000); await sleep(600);
    await h.evalIn(`SW_EL.querySelector(${JSON.stringify(row)}).click(); 0`);
    await h.until('!!PD.dock && !!SELECTED_SID', 15000).catch(() => null); await sleep(1500);
    out.popThenDock = await h.evalIn(`(() => { const w = LD.dock.popWindow('list'); return { pd: !!PD.dock, sid: !!SELECTED_SID, out: LD.dock.isOut('list'), open: !!w && !w.closed,
      there: SW_EL.ownerDocument !== document, sameKey: !!w && w.__dockPopKey === window.__dockPopKey }; })()`);
    // A reload leaves it out, its window gone: the notice offering it back
    // cannot be waved away, and brings it back.
    await h.evalIn('location.reload(); 0'); await sleep(1500); await h.ready();
    await h.until('!!LD.dock && !!document.querySelector("#list-dock .dk-outnote")', 20000).catch(() => null); await sleep(400);
    out.note = await h.evalIn(`(() => { const n = document.querySelector('#list-dock .dk-outnote'); const x = n && n.querySelector('.dk-outnote-x');
      return { out: LD.dock.isOut('list'), note: !!n, back: !!(n && n.querySelector('[data-dk-pop="back"]')), x: !!x && getComputedStyle(x).display !== 'none' }; })()`);
    if (out.note.back) { await h.click('#list-dock .dk-outnote [data-dk-pop="back"]'); await sleep(600); }
    out.noteBack = await h.evalIn(GEOM);
    // No former strip place: a window from the default pinned list still has Panels.
    await h.evalIn('LD.dock.reset(); LD.dock.popOut("list"); 0');
    await h.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document');
    await h.evalIn('LD.dock.popWindow("list").close(); 0');
    await h.until('!LD.dock.isVisible("list")');
    out.pinnedHidden = await h.evalIn(GEOM);
    await h.click('.pd-panels'); await h.click('[data-pd-list]');
    await h.until('!!LD.dock.popWindow("list") && SW_EL.ownerDocument !== document');
    out.pinnedReopened = await h.evalIn(GEOM);
    await h.close();
    // A phone: the list is home.
    const q = await page(430, 932, true);
    await q.evalIn(`(() => { SELECTED_PROJECT = ''; SB_DEST = ''; renderRows(); return 0; })()`);
    await sleep(1500);
    out.phone = await q.evalIn(`({ dock: !!LD.dock, home: SW_EL.parentNode === document.body, shown: SW_EL.getBoundingClientRect().width > 0, lib: !!PD.lib })`);
    await q.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


class TheWiring(unittest.TestCase):
    """What the page asks of the library (static checks)."""

    def test_the_list_is_a_panel_of_a_dock_of_its_own(self):
        block = INDEX[INDEX.index("// ---- The list as a dock panel (#137): begin"):INDEX.index("// ---- The list as a dock panel: end")]
        for used in ("el: SW_EL", "fill: 'page'", "stripHover: false", "stripOpen: 'beside'", "strip: 44",
                     "can: (id, a) => id === 'list' && (a !== 'hide' || LD.dock?.viewMode(id) === 'window')", "storageKey: LD_KEY",
                     "L.split('row', [L.stack(['list'], { size: LD_W }), L.stack(['page'])])"):
            self.assertIn(used, block, used)
        self.assertIn("const LD_KEY = 'cd-list-dock';", block)

    def test_a_phone_has_no_list_dock(self):
        self.assertIn("function ldWanted() { return !!PD.lib && !PD.failed && !isPhone(); }", INDEX)


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheListDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ld-", ignore_cleanup_errors=True)
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
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
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
        task2 = chatroom.create_room("Inspect every wheel bearing and the braking system before the next winter timetable", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        chatroom.post_message(task2["id"], "user", "Check the bearings")
        dashboard.assign_session_project(task2["id"], cls.proj)
        cls.task2 = task2["id"]
        task = chatroom.create_room("Brakes that squeal whenever the train crosses the northern bridge at dawn", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
        chatroom.post_message(task["id"], "user", "Why do the brakes squeal?")
        dashboard.assign_session_project(task["id"], cls.proj)
        cls.task = task["id"]
        loose = chatroom.create_room("A loose idea", [{"identity": "claude", "agent": "claude", "cwd": str(base)}])
        chatroom.post_message(loose["id"], "user", "Just thinking")
        cls.loose = loose["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "task2": cls.task2, "loose": cls.loose, "shots": shots}
        script = base / "ld_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def opened(self, g, what):
        self.assertTrue(g["open"] and g["sid"], f"{what}: the row opened its conversation {g.get('before')}")
        self.assertLessEqual(g["scrollW"], g["vw"], f"{what}: no sideways scroll")

    def retained_active(self, g, side):
        self.assertIsNotNone(g["strip"], f"{g['mode']}: the retained strip is visible")
        self.assertIsNotNone(g["stripBtn"], f"{g['mode']}: the list keeps its strip button")
        self.assertEqual(g["stripSide"], f"dk-strip-{side}")
        self.assertTrue(g["stripActive"], f"{g['mode']}: the retained button looks active")
        self.assertEqual((g["stripExpanded"], g["stripPressed"]), ("true", "true"))

    def test_full_names_expand_in_three_themes_and_both_widths(self):
        for sample in self.got["expand"]:
            with self.subTest(width=sample["width"], theme=sample["theme"]):
                self.assertFalse(sample["early"]["shown"], "wait before showing")
                long = sample["long"]
                self.assertTrue(long["shown"] and long["full"] and long["hit"], long)
                self.assertGreater(long["box"]["width"], long["row"]["width"])
                self.assertEqual(long["box"]["left"], long["row"]["left"])
                self.assertLessEqual(long["pageWidth"], long["viewport"])
                self.assertFalse(sample["short"]["shown"], "a short name stays in its row")
                self.assertEqual(sample["next"]["shown"], self.task2, sample["next"])
        self.assertTrue(self.got["expandClick"], "the expanded row opens its task")
        self.assertFalse(self.got["expandOff"]["shown"], "the checkbox disables expansion")
        self.assertTrue(self.got["expandPersist"], "the checkbox survives a reload")
        self.assertTrue(self.got["expandFocus"], "keyboard focus expands the row")
        self.assertTrue(self.got["expandMenu"], "an open Dock menu stays above the expansion")
        wrapped = self.got["expandWrapped"]
        self.assertTrue(wrapped["shown"] and wrapped["coversNext"] and wrapped["height"] > wrapped["rowHeight"], wrapped)
        self.assertTrue(self.got["expandWrappedNext"], "a covered following row expands immediately")
        self.assertTrue(self.got["expandWrappedClick"], "a covered following row opens its own task")
        right = self.got["expandRight"]
        self.assertTrue(right["shown"] and right["full"] and right["hit"], right)
        self.assertLess(right["box"]["left"], right["row"]["left"])
        self.assertAlmostEqual(right["box"]["right"], right["row"]["right"])
        self.assertLessEqual(right["pageWidth"], right["viewport"])
        wide = self.got["expandWideRight"]
        self.assertGreater(wide["row"]["width"], wide["viewport"] / 2)
        self.assertTrue(wide["shown"] and wide["full"], wide)
        self.assertLess(wide["box"]["left"], wide["row"]["left"])
        self.assertAlmostEqual(wide["box"]["right"], wide["row"]["right"])
        self.assertTrue(all(self.got["expandWindow"][k] for k in ("shown", "sameWindow", "within", "full")), self.got["expandWindow"])
        self.assertTrue(self.got["expandResized"]["shown"], self.got["expandResized"])
        self.assertTrue(self.got["expandTouch"], "touch emulation has no expansion")

    def test_pinned_at_the_left_by_default_as_before(self):
        g = self.got["pinned"]
        self.assertTrue(g["dock"] and g["here"] and not g["home"])
        self.assertEqual((g["mode"], g["side"]), ("pinned", "left"))
        self.assertEqual((g["list"]["x"], g["list"]["w"]), (0, 300), "today's 300px column")
        self.assertEqual(g["listW"], "305px", "the page starts after the list and its splitter")
        self.assertEqual(g["dockHost"]["x"], 305)
        self.assertIsNone(g["strip"], "no strip while it is pinned")
        self.assertIsNone(g["stripBtn"], "a panel that never had a strip place gains no button")
        m, s = g["menuBtn"], g["bySel"]
        self.assertTrue(m and s)
        self.assertLessEqual(s["r"], m["x"], "the dock's ⋯ sits past the list's own controls")
        self.assertLessEqual(m["r"], g["list"]["r"], "inside the list")
        self.opened(self.got["pinnedOpens"], "pinned")

    def test_unpinned_the_middle_takes_the_room(self):
        self.assertEqual(self.got["unpinnedJust"]["mode"], "unpinned")
        g = self.got["unpinned"]
        self.assertIsNone(g["fly"], "a click elsewhere slides it back")
        self.assertEqual((g["mode"], g["stripSide"]), ("unpinned", "dk-strip-left"))
        self.assertEqual(g["strip"]["w"], 44)
        self.assertEqual(g["listW"], "44px")
        self.assertEqual(g["dockHost"]["x"], 44, "the middle starts at the strip")
        o = self.got["unpinnedOut"]
        self.assertEqual(o["fly"], "list")
        self.assertGreaterEqual(o["list"]["w"], 296, "its width, inside the flyout's borders")
        self.assertGreaterEqual(o["dockHost"]["x"], o["list"]["r"], "slid out beside the middle: nothing covered")
        g = self.got["unpinnedOpens"]
        self.opened(g, "unpinned")
        self.assertIsNone(g["fly"], "the list goes back once a row is opened")
        self.assertEqual(g["dockHost"]["x"], 44)

    def test_undock_lies_over_the_middle(self):
        o = self.got["undockOut"]
        self.assertEqual((o["mode"], o["fly"]), ("undock", "list"))
        self.assertEqual(o["dockHost"]["x"], 44, "over the middle, which keeps its width")
        g = self.got["undockOpens"]
        self.opened(g, "undock")
        self.assertIsNone(g["fly"])

    def test_floating_keeps_the_active_strip_button(self):
        g = self.got["float"]
        self.assertEqual(g["mode"], "float")
        self.assertEqual((g["listW"], g["dockHost"]["x"]), ("44px", 44))
        self.assertEqual(g["strip"]["w"], 44)
        self.retained_active(g, "left")
        self.opened(self.got["floatOpens"], "float")

    def test_in_its_own_window_it_drives_the_main_window(self):
        g = self.got["window"]
        self.assertEqual((g["mode"], g["out"], g["here"]), ("window", True, False))
        self.assertEqual((g["listW"], g["dockHost"]["x"]), ("44px", 44), "the middle starts after the retained strip")
        self.retained_active(g, "left")
        o = self.got["windowOpens"]
        self.opened(o, "a row clicked in the list's window")
        u = self.got["windowUnassigned"]
        self.assertTrue(u["box"] and u["row"], u)
        self.assertNotEqual(u["was"], u["now"], "Unassigned folds and unfolds there")
        self.assertTrue(u["folded"], "the page knows it is open")
        self.assertTrue(u["opened"], "its row opens that conversation here")
        b = self.got["windowBack"]
        self.assertEqual((b["out"], b["here"], b["mode"]), (False, True, "window"))
        self.assertFalse(b["visible"] or b["notice"])
        h = self.got["hiddenReload"]
        self.assertEqual((h["visible"], h["out"], h["mode"], h["notice"]), (False, False, "window", False))
        self.assertEqual((h["listW"], h["dockHost"]["x"]), ("44px", 44), "its former strip button takes space")
        self.assertTrue(self.got["windowReopened"]["out"])
        p = self.got["pinnedHidden"]
        self.assertEqual((p["visible"], p["mode"], p["listW"], p["notice"]), (False, "window", "0px", False))
        self.assertTrue(self.got["pinnedReopened"]["out"])

    def test_move_to_right_and_back(self):
        p = self.got["pinnedAgain"]
        self.assertEqual((p["mode"], p["list"]["x"], p["listW"]), ("pinned", 44, "349px"))
        self.retained_active(p, "left")
        r = self.got["right"]
        self.assertEqual((r["mode"], r["side"]), ("pinned", "right"))
        self.assertEqual(r["list"]["r"], r["strip"]["x"])
        self.assertEqual(r["listW"], "0px")
        self.assertEqual(r["listR"], "349px")
        self.retained_active(r, "right")
        self.assertLessEqual(r["dockHost"]["r"], r["list"]["x"], "the page ends where the list starts")
        self.opened(self.got["rightOpens"], "at the right")
        l = self.got["left"]
        self.assertEqual((l["side"], l["listW"], l["listR"]), ("left", "349px", "0px"))
        self.retained_active(l, "left")

    def test_maximise_and_restore(self):
        m = self.got["max"]
        self.assertEqual(m["dock"] and m["mode"], "pinned")
        self.assertEqual((m["list"]["x"], m["list"]["w"]), (0, m["vw"]), "the list takes the dock's whole width")
        self.assertGreaterEqual(m["menuBtn"]["x"], 0)
        r = self.got["restored"]
        self.assertEqual((r["list"]["x"], r["list"]["w"], r["listW"]), (44, 300, "349px"))
        self.retained_active(r, "left")

    def test_unpinned_at_the_top_and_bottom_spans_the_width(self):
        depth = {"top": 240, "bottom": 300}
        for side in ("top", "bottom"):
            g = self.got["fly_" + side]
            a = self.got["at_" + side]
            self.assertEqual((a["side"], a["mode"], a["list"]["h"], a["list"]["w"]), (side, "pinned", depth[side], a["vw"]), f"{side}: its retained strip depth")
            self.retained_active(a, side)
            self.assertEqual((g["side"], g["mode"], g["fly"]), (side, "unpinned", "list"), side)
            self.assertGreaterEqual(g["list"]["w"], g["vw"] - 4, f"{side}: the whole width, inside the flyout's borders")
        l = self.got["leftAgain"]
        self.assertEqual((l["side"], l["mode"], l["listW"]), ("left", "pinned", "349px"), "back at the left: retained strip plus its column")

    def test_a_window_stays_the_lists_when_the_other_dock_is_made(self):
        g = self.got["popThenDock"]
        self.assertTrue(g["pd"] and g["sid"], g)
        self.assertTrue(g["out"] and g["open"] and g["there"], g)
        self.assertTrue(g["sameKey"], "the window has the page's key")

    def test_the_notice_offering_the_list_back_stays(self):
        n = self.got["note"]
        self.assertTrue(n["out"] and n["note"] and n["back"], n)
        self.assertTrue(n["x"], "Panels provides recovery after dismissing the notice")
        b = self.got["noteBack"]
        self.assertTrue(b["here"] and not b["out"], b)
        self.assertGreater(b["list"]["w"], 0)

    def test_the_retained_strip_depth_comes_back_from_the_top(self):
        self.assertEqual(self.got["widerKept"]["list"]["w"], 300)
        self.assertEqual(self.got["widerKept"]["side"], "left")
        g = self.got["widerKeptReload"]
        self.assertEqual((g["side"], g["list"]["w"], g["listW"]), ("left", 300, "349px"), "also over a reload")

    def test_a_short_window_slid_out_at_the_top_or_bottom(self):
        for side in ("top", "bottom"):
            g = self.got["short_" + side]
            self.assertEqual((g["side"], g["mode"], g["fly"]), (side, "unpinned", "list"), side)
            f, m = g["flyBox"], g["dockHost"]
            gap = m["y"] - f["b"] if side == "top" else f["y"] - m["b"]
            self.assertTrue(-1 <= gap <= 1, f"{side}: the middle meets the list ({gap}px)")
            self.assertGreaterEqual(m["h"], 199, f"{side}: the middle keeps its 200px")

    def test_width_and_mode_survive_a_reload(self):
        self.assertEqual(self.got["wider"]["list"]["w"], 332, "two presses of → on its splitter")
        g = self.got["reloadWide"]
        self.assertEqual((g["mode"], g["list"]["w"], g["listW"]), ("pinned", 332, "381px"))
        u = self.got["reloadUnpinned"]
        self.assertEqual((u["mode"], u["listW"]), ("unpinned", "44px"))

    def test_a_phone_keeps_the_list_home(self):
        g = self.got["phone"]
        self.assertTrue(g["home"] and g["shown"], g)
        self.assertFalse(g["dock"])
        t = self.got["toPhone"]
        self.assertFalse(t["dock"])
        self.assertTrue(t["home"] and t["rootHidden"])
        self.assertEqual(t["listW"], "0px" if not t["list"] else t["listW"])
        d = self.got["toDesk"]
        self.assertTrue(d["dock"] and not d["home"])
        self.assertEqual(d["mode"], "unpinned", "wide again: its layout as it was")


if __name__ == "__main__":
    unittest.main()
