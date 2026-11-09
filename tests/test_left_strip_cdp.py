"""Tasks and the Inbox share one left strip (#322). On a computer both are
panels of the list dock, its left strip holding Tasks then Inbox, as the
tools sit on the PO dock's right one, on every screen. In headless Chrome over
CDP, against a hub in a thread serving the pages.

Three projects: Motors (a PO with an answer to check, so one Inbox row),
Wheels (a PO) and Alpha (none), and a task in no project. Checked:

* at 1440x900 and 1728x1117 a fresh browser shows Tasks then Inbox on the left
  strip; the Inbox pinned open at the left of the page, the list closed on its
  strip; the PO dock has no Inbox; the bar's Inbox button gives way; the strip
  button carries the count;
* the Tasks button slides the list out, a click elsewhere or Esc puts it back;
  a list row opens its conversation;
* the Inbox on another project's PO screen: All shows Motors' row, and a click
  opens Motors' PO;
* the Inbox pinned, on its strip, floating and in its window; on the other
  side and first on the strip;
* Panels ▾ › Tasks and › Inbox; a reload keeps the layout; Reset layout puts
  the default back;
* a layout saved before #322 (the list hidden, the Inbox in the PO dock,
  docked or on its strip) opens with both on the left strip, the Inbox as the
  person kept it;
* 800x600 overflows nothing; a phone (390x844) has no list dock and keeps the
  Inbox as a tab of the PO dock;
* the page threw nothing meanwhile.

Measures go to $ENSEMBLE_SHOTS/left-strip.json and screenshots there too when
it is set. Skipped without Node or Chrome.
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
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
WIDTHS = [(1440, 900), (1728, 1117)]
# Layouts saved by main at 1bcc2bd (before #322) at 1440x900: the list dock's
# #306 default (the list hidden) and the PO dock with its Inbox docked, on its
# strip and floating.
OLD = {
 "ld": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"page\"],\"active\":\"page\"},\"floats\":[],\"auto\":[],\"hidden\":[{\"id\":\"list\",\"was\":{\"kind\":\"dock\",\"home\":null}}],\"out\":{}}",
 "pinned": "{\"v\":1,\"root\":{\"t\":\"split\",\"dir\":\"row\",\"kids\":[{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\",\"size\":280},{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"}]},\"floats\":[],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480}],\"hidden\":[],\"out\":{}}",
 "unpinned": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"},\"floats\":[],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480},{\"id\":\"inbox\",\"edge\":\"left\",\"size\":280,\"home\":{\"peers\":[],\"index\":0,\"near\":\"po-chat\",\"side\":\"left\",\"size\":280}}],\"hidden\":[],\"out\":{}}",
 "float": "{\"v\":1,\"root\":{\"t\":\"stack\",\"panels\":[\"po-chat\"],\"active\":\"po-chat\"},\"floats\":[{\"stack\":{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\"},\"x\":460,\"y\":107,\"w\":520,\"h\":420,\"home\":{\"peers\":[],\"index\":0,\"near\":\"po-chat\",\"side\":\"left\",\"size\":280},\"strip\":{\"id\":\"inbox\",\"edge\":\"left\",\"size\":280,\"peers\":[],\"index\":0}}],\"auto\":[{\"id\":\"changes\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"workspace\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"board\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":760},{\"id\":\"spec\",\"edge\":\"right\",\"home\":{\"peers\":[],\"near\":\"po-chat\",\"side\":\"right\"},\"size\":480}],\"hidden\":[],\"out\":{}}"
}

# Layouts holding the Tasks panel (#322, P287 drops it): #322 step 1's default
# (Tasks on the left strip, the Inbox pinned) and the list docked beside a
# floating Inbox.
WITH_LIST = {
 "stripList": "{\"v\":1,\"root\":{\"t\":\"split\",\"dir\":\"row\",\"kids\":[{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\",\"size\":280},{\"t\":\"stack\",\"panels\":[\"page\"],\"active\":\"page\"}]},\"floats\":[],\"auto\":[{\"id\":\"list\",\"edge\":\"left\",\"size\":300,\"home\":{\"peers\":[],\"near\":\"page\",\"side\":\"left\"}}],\"hidden\":[],\"out\":{}}",
 "dockedList": "{\"v\":1,\"root\":{\"t\":\"split\",\"dir\":\"row\",\"kids\":[{\"t\":\"stack\",\"panels\":[\"list\"],\"active\":\"list\",\"size\":300},{\"t\":\"stack\",\"panels\":[\"page\"],\"active\":\"page\"}]},\"floats\":[{\"stack\":{\"t\":\"stack\",\"panels\":[\"inbox\"],\"active\":\"inbox\"},\"x\":460,\"y\":107,\"w\":420,\"h\":420,\"home\":{\"peers\":[],\"index\":0,\"near\":\"page\",\"side\":\"left\",\"size\":280},\"strip\":{\"id\":\"inbox\",\"edge\":\"left\",\"size\":280,\"peers\":[],\"index\":1}}],\"auto\":[],\"hidden\":[],\"out\":{}}",
}

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.errors = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data);
      if (m.method === 'Runtime.exceptionThrown') this.errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
      if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// Where things are on the screen.
const GEOM = `(() => {
  const R = e => { if (!e || !e.isConnected || e.ownerDocument !== document) return null; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden' ? { x: Math.round(r.left), r: Math.round(r.right), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) } : null; };
  const d = window.LD && LD.dock;
  const strip = s => [...document.querySelectorAll('#list-dock .dk-strip-' + s + ' .dk-strip-btn[data-dk-auto]')].map(b => b.dataset.dkAuto);
  const sb = id => document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="' + id + '"]');
  const wideEls = [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > document.documentElement.clientWidth + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('[hidden], .dk-off') && !(e.closest('#detail-panel') && !document.body.classList.contains('detail-open')); });
  const wide = wideEls.length, wideWhat = wideEls.slice(0, 4).map(e => e.tagName + '#' + e.id + '.' + String(e.className).slice(0, 40) + '@' + Math.round(e.getBoundingClientRect().right));
  const get = k => { try { return localStorage.getItem(k); } catch (e) { return 'x'; } };
  return { W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth, wide, wideWhat,
    ld: !!d, left: d ? strip('left') : [], right: d ? strip('right') : [],
    inboxMode: d ? d.viewMode('inbox') : '', fly: d ? d.flyOpen() : null, inboxOn: d ? ldOnScreen('inbox') : false,
    ghost: document.querySelectorAll('#list-dock [data-dk-tab="list"], #list-dock [data-dk-auto="list"], #list-dock [data-dk-panel="list"]').length,
    ldp: [...document.querySelectorAll('#ld-projs .ldp-row')].map(b => ({ t: b.textContent.trim(), cur: b.getAttribute('aria-current') === 'true', all: b.dataset.ldp === 'all', proj: b.dataset.ldpProj || '', room: b.dataset.ldpRoom || '', sid: b.dataset.ldpSid || '' })),
    ldpBox: R(document.getElementById('ld-projs')), ibxTitle: ((document.querySelector('#ld-ibx-body .ibx-title') || {}).textContent || '').trim(),
    scopeSw: !!document.querySelector('#ld-inbox .ibx-scope'), detail: document.body.classList.contains('detail-open'), sel: SELECTED_PROJECT || '',
    frames: [...document.querySelectorAll('iframe')].filter(f => f.getBoundingClientRect().width > 0).map(f => f.dataset.room || f.src.slice(-60)),
    list: R(SW_EL), ldInbox: R(document.getElementById('ld-inbox')), inboxOut: !!d && d.isOut('inbox'),
    pdInbox: !!PD.dock && PD.dock.title('inbox') != null, pdInboxEl: R(PD.els.inbox),
    pdInboxTab: !!document.querySelector('#po-dock [data-dk-tab="inbox"], #po-dock .dk-strip-btn[data-dk-auto="inbox"]'),
    bar: R(document.getElementById('inbox-btn')), ldIbx: document.body.classList.contains('ld-ibx'),
    badge: ((sb('inbox') || document).querySelector('.dk-strip-badge') || { textContent: '' }).textContent.trim(),
    rows: document.querySelectorAll('#ld-inbox a.ibx-link[data-room]').length,
    po: document.body.classList.contains('po-dock'), pid: (PD && PD.pid) || '', narrow: !!PD.dock && PD.dock.narrow(),
    chat: R(PD.els['po-chat']), a5: get('cd-list-dock-a5'), stored: get('cd-list-dock'), pdStored: get('cd-tool-strip') };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  // A page; seed: localStorage keys set (a string) or removed (null) before the page's first script, once.
  const page = async (w, h, mobile, seed) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const clickAt = async (x, y) => { for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(); return [r.left + Math.min(40, r.width / 2), r.top + r.height / 2]; })()`);
      await clickAt(x, y);
    };
    const key = async (k) => { for (const type of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type, key: k, code: k, windowsVirtualKeyCode: k === 'Escape' ? 27 : 0 }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = Object.assign({ 'cd-list-dock': null, 'cd-list-dock-a4': null, 'cd-list-dock-a5': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-phone-tabs': null, 'cd-last-conv': null }, seed || {});
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 2 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.po) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, click, clickAt, key, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = async (p, pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + JSON.stringify(pid), 30000);
    await sleep(500);
  };
  const G = p => p.evalIn(GEOM);
  const P = A.ids;
  const LBTN = id => `document.querySelector('#list-dock .dk-strip-btn[data-dk-auto="${id}"]')`;
  try {
    // ---- the default, at each width, a fresh browser
    out.widths = {};
    for (const [w, h] of A.widths) {
      const p = await page(w, h, false);
      await toPo(p, P.Motors);
      await p.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
      out.widths[w] = await G(p);
      await p.shot(`left-strip-${w}x${h}`);
      await p.close();
    }
    // ---- 1140x1000 at 2x, beside the P287 drawing (2280x2000)
    if (A.shots) {
      const q = await page(1140, 1000, false);
      await toPo(q, P.Motors);
      await q.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1140, height: 1000, deviceScaleFactor: 2, mobile: false }, q.sessionId);
      await sleep(500);
      await q.shot('left-strip-1140x1000');
      out.w1140 = await G(q);
      await q.close();
    }
    const p = await page(1440, 900, false);
    await toPo(p, P.Motors);
    await p.until('document.querySelectorAll("#ld-inbox a.ibx-link[data-room]").length > 0', 15000).catch(() => null);
    out.fresh = await G(p);
    // ---- the project list on top of the Inbox (#322, P287)
    const LDP = sel => `document.querySelector('#ld-projs ${sel}')`;
    await p.shot('left-strip-1440x900-projects');
    // a project with no PO: its page (the Board), the Inbox scoped to it
    await p.click(LDP(`[data-ldp-proj="${P.Alpha}"]`)); await sleep(700);
    out.alpha = await G(p);
    // a task in no project: its conversation, the Inbox its own
    await p.click(LDP(`.ldp-row[data-ldp-room="${A.loose}"]`)); await sleep(900);
    out.loose = await G(p);
    // its ⋯ and its right click: Open and Make PO
    const MENU = `(() => { const m = document.querySelector('body > .dcm'); return m ? { items: [...m.querySelectorAll('.dcm-item')].map(b => ({ t: b.textContent.trim(), makepo: b.classList.contains('makepo-btn'), room: b.dataset.room || '', disabled: b.disabled })), expanded: !!document.querySelector('#ld-projs [data-ldp-menu][aria-expanded="true"]') } : null; })()`;
    await p.evalIn(`(() => { const b = ${LDP(`[data-ldp-menu][data-ldp-room="${A.loose}"]`)}; b.click(); return 0; })()`); await sleep(300);
    out.dotsMenu = await p.evalIn(MENU);
    await p.key('Escape'); await sleep(200);
    out.dotsClosed = await p.evalIn(MENU);
    const [cx, cy] = await p.evalIn(`(() => { const r = ${LDP(`.ldp-row[data-ldp-room="${A.loose}"]`)}.getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; })()`);
    await c.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx, y: cy, button: 'right', clickCount: 1 }, p.sessionId);
    await c.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx, y: cy, button: 'right', clickCount: 1 }, p.sessionId);
    await sleep(300);
    out.ctxMenu = await p.evalIn(MENU);
    await p.clickAt(900, 500); await sleep(200);
    out.ctxClosed = await p.evalIn(MENU);
    // a session in no project (a plain terminal session): a row of its own; a click opens it
    out.session = await p.evalIn(`(() => {
      const un = projectById(UNASSIGNED_ID);
      const r = { sessionId: 'sess-loose-1', label: 'Notes on the trip', headless: false, updatedAt: Date.now() / 1000, isLive: false, agent: 'claude' };
      ALL_ROWS.push(r);
      if (un) un.sessions = (un.sessions || []).concat([{ sessionId: r.sessionId }]);
      const seen = [];
      const od = openDetail; openDetail = sid => { seen.push(sid); };
      LDP.sig = ''; ldpPaint();
      const b = document.querySelector('#ld-projs .ldp-row[data-ldp-sid="sess-loose-1"]');
      const t = b ? b.textContent.trim() : null;
      if (b) b.click();
      openDetail = od;
      return { un: !!un, row: t, opened: seen };
    })()`);
    // back to Motors' PO from its row
    await p.click(LDP(`[data-ldp-proj="${P.Motors}"]`));
    await p.until('PD.pid === ' + JSON.stringify(P.Motors) + ' && document.body.classList.contains("po-dock")', 15000).catch(() => null);
    await sleep(500);
    out.motors = await G(p);
    // ---- the Board out on the right follows the project picked in the list (P287)
    const BOARD = `(() => ({ fly: PD.dock ? PD.dock.flyOpen() : null, sel: SELECTED_PROJECT, pid: PD.pid, gear: !!PD.els.board && PD.els.board.textContent.includes('Gearbox noise'), w: PD.els.board ? Math.round(PD.els.board.getBoundingClientRect().width) : 0 }))()`;
    await p.click(`document.querySelector('#po-dock .dk-strip-btn[data-dk-auto="board"]')`);
    await p.until('PD.dock.flyOpen() === "board"', 5000).catch(() => null); await sleep(600);
    out.boardMotors = await p.evalIn(BOARD);
    await p.click(LDP(`[data-ldp-proj="${P.Wheels}"]`));
    await p.until('PD.pid === ' + JSON.stringify(P.Wheels), 15000).catch(() => null); await sleep(800);
    out.boardWheels = await p.evalIn(BOARD);
    await p.evalIn('PD.dock.closeFly(); 0'); await sleep(300);
    await p.click(LDP(`[data-ldp-proj="${P.Motors}"]`));
    await p.until('PD.pid === ' + JSON.stringify(P.Motors), 15000).catch(() => null); await sleep(500);
    // ---- the Inbox on Wheels' PO: All has Motors' row, its click opens Motors
    await p.click(LDP(`[data-ldp-proj="${P.Wheels}"]`));
    await p.until('PD.pid === ' + JSON.stringify(P.Wheels), 15000).catch(() => null); await sleep(500);
    out.wheels = await G(p);
    await p.click(LDP('[data-ldp="all"]')); await sleep(500);
    out.wheelsAll = await G(p);
    await p.click(`document.querySelector('#ld-inbox a.ibx-link[data-room]')`);
    await p.until('PD.pid === ' + JSON.stringify(P.Motors), 15000).catch(() => null);
    await sleep(500);
    out.ibxOpened = await G(p);
    await p.click(LDP(`[data-ldp-proj="${P.Motors}"]`)); await sleep(500);
    // ---- the modes
    const L = 'LD.dock';
    await p.evalIn(`${L}.setViewMode('inbox', 'unpinned'); 0`); await sleep(400);
    out.unpinned = await G(p);
    await p.click(LBTN('inbox')); await sleep(500);
    out.inboxFly = await G(p);
    await p.shot('left-strip-1440x900-inbox-out');
    await p.key('Escape'); await sleep(300);
    await p.evalIn(`${L}.setViewMode('inbox', 'float'); 0`); await sleep(400);
    out.float = await G(p);
    await p.evalIn(`${L}.setViewMode('inbox', 'pinned'); 0`); await sleep(400);
    out.pinned = await G(p);
    await p.evalIn(`${L}.popOut('inbox'); 0`);
    await p.until(`!!${L}.popWindow('inbox')`, 15000).catch(() => null); await sleep(600);
    out.window = await G(p);
    await p.evalIn(`${L}.popIn('inbox'); 0`); await sleep(500);
    out.popIn = await G(p);
    await p.evalIn(`${L}.setViewMode('inbox', 'unpinned'); ${L}.moveSide('inbox', 'right'); 0`); await sleep(400);
    out.right = await G(p);
    await p.evalIn(`${L}.moveSide('inbox', 'left'); ${L}.moveStrip('inbox', 0, 'left'); 0`); await sleep(400);
    out.first = await G(p);
    // ---- a reload keeps it
    await p.evalIn('location.reload(); 0'); await sleep(1500);
    await p.until('window.ensBooted === true && !!LD.dock', 30000);
    await toPo(p, P.Motors);
    out.reloaded = await G(p);
    // ---- Panels ▾ › Tasks and › Inbox
    const menu = async () => { await p.click('document.querySelector("#bar-here .pd-panels")'); await sleep(250); };
    const rows = () => p.evalIn(`[...document.querySelectorAll('.pd-menu [data-pd-ld]')].map(b => ({ id: b.dataset.pdLd, checked: b.getAttribute('aria-checked') === 'true', disabled: b.disabled, text: b.textContent.trim() }))`);
    await menu(); out.menu1 = await rows();
    await p.click('document.querySelector(".pd-menu [data-pd-ld=inbox]")'); await sleep(500);
    out.menuInbox = await G(p);
    await p.key('Escape'); await sleep(300);
    // the Inbox pinned: its row is checked and kept
    await p.evalIn(`${L}.pin('inbox'); 0`); await sleep(300);
    await menu(); out.menu2 = await rows();
    await p.evalIn('pdMenuClose(false); 0');
    // ---- Reset layout
    await p.evalIn(`${L}.setViewMode('inbox', 'float'); 0`); await sleep(300);
    out.beforeReset = await G(p);
    await menu();
    await p.click('document.querySelector(".pd-menu [data-pd-reset]")'); await sleep(700);
    out.reset = await G(p);
    // the old layouts, built from this one's own parts
    const L0 = JSON.parse(out.reset.stored || '{}');
    out.layoutSample = L0;
    await p.close();
    // ---- a layout saved before #322: the list hidden (#306), the PO dock's Inbox docked / on its strip
    out.migr = {};
    for (const was of ['pinned', 'unpinned', 'float']) {
      const seeds = { ld: A.old.ld, pd: A.old[was] };
      const m = await page(1440, 900, false, { 'cd-list-dock': seeds.ld, 'cd-list-dock-a4': '1', 'cd-tool-strip': seeds.pd });
      await toPo(m, P.Motors);
      out.migr[was] = await G(m);
      await m.shot(`left-strip-migrated-${was}`);
      await m.close();
    }
    // ---- a layout saved with the Tasks panel (#322 step 1, or the list docked): the list goes, the Inbox stays as kept
    for (const [k, ld] of Object.entries(A.withList)) {
      const m = await page(1440, 900, false, { 'cd-list-dock': ld, 'cd-list-dock-a4': '1', 'cd-list-dock-a5': '1' });
      await toPo(m, P.Motors);
      out.migr[k] = await G(m);
      await m.shot(`left-strip-migrated-${k}`);
      await m.close();
    }
    // ---- 800x600: nothing wider than the screen
    const sm = await page(800, 600, false);
    await toPo(sm, P.Motors);
    out.small = await G(sm);
    await sm.shot('left-strip-800x600');
    await sm.close();
    // ---- a phone: no list dock, the PO dock keeps its Inbox tab
    const ph = await page(390, 844, true);
    await toPo(ph, P.Motors).catch(() => null);
    await sleep(600);
    out.phone = await ph.evalIn(`(() => ({ ld: !!LD.dock, narrow: !!PD.dock && PD.dock.narrow(), pdInbox: !!PD.dock && PD.dock.title('inbox') != null, W: document.documentElement.clientWidth, scrollW: document.documentElement.scrollWidth }))()`);
    await ph.shot('left-strip-phone');
    // ---- the same page made a computer's: the Inbox leaves the PO dock, onto the list dock's strip
    const RS = `({ tabs: [...document.querySelectorAll('#po-dock .dk-tab')].map(t => t.dataset.dkTab), narrow: PD.dock.narrow(), pdInbox: PD.dock.title('inbox') != null, lay: JSON.stringify(PD.dock.layout()), left: LD.dock ? [...document.querySelectorAll('#list-dock .dk-strip-left .dk-strip-btn[data-dk-auto]')].map(b => b.dataset.dkAuto) : [] })`;
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, ph.sessionId);
    await c.send('Emulation.setTouchEmulationEnabled', { enabled: false }, ph.sessionId);
    await ph.until('!PD.dock.narrow() && !!LD.dock', 10000); await sleep(600);
    out.phoneToWide = await ph.evalIn(RS);
    await ph.close();
    // ---- a computer's PO screen made a phone's and back: the Inbox is a tab there, the wide layout as it was
    const rz = await page(1440, 900, false);
    await toPo(rz, P.Motors);
    out.rzWide = await rz.evalIn(RS);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true }, rz.sessionId);
    await rz.until('PD.dock.narrow()', 10000); await sleep(600);
    out.rzPhone = await rz.evalIn(RS);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, rz.sessionId);
    await rz.until('!PD.dock.narrow()', 10000); await sleep(600);
    out.rzBack = await rz.evalIn(RS);
    out.rzStoredWide = await rz.evalIn(`localStorage.getItem('cd-tool-strip')`);
    await rz.close();
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'left-strip.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class LeftStripInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ls-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
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
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        cls.ids = {}
        cls.rooms = {}
        for name in ("Motors", "Wheels", "Alpha"):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            if name in ("Motors", "Wheels"):
                home = Path(proj.get("home") or proj["path"])
                po = chatroom.create_room(f"{name} PO", members(home))
                dashboard.assign_session_project(po["id"], proj["id"])
                ok, why = dashboard.set_project_po(proj["id"], po["id"])
                assert ok, why
                cls.rooms[name] = po["id"]
                if name == "Motors":
                    cls.po = po["id"]
                    task = chatroom.create_room("Gearbox noise", members(home))
                    dashboard.assign_session_project(task["id"], proj["id"])
                    cls.task = task["id"]
                    text, ids = points.take(chatroom.get_room(po["id"], public=False), "Wider board", to="claude", key="k1")
                    assert ids == ["P1"], ids
                    chatroom.post_message(po["id"], "user", text, to="claude")
                    chatroom.post_message(po["id"], "claude", "Re P1: it is wider now.", to="user")
                    points.sync(po["id"], force=True)
        cls.loose = chatroom.create_room("Loose carriage inspection", members(base))["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "po": cls.po, "widths": WIDTHS, "shots": shots, "old": OLD,
                "loose": cls.loose, "withList": WITH_LIST}
        script = base / "left_strip_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=480)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_LS_DUMP"):
            Path(os.environ["ENSEMBLE_LS_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def g(self, k):
        return self.got[k]

    def assertNoOverflow(self, g):
        self.assertLessEqual(g["scrollW"], g["W"])
        self.assertEqual(g["wide"], 0, g["wideWhat"])

    def test_the_default_the_inbox_alone_on_the_left_strip(self):
        for w, _ in WIDTHS:
            g = self.got["widths"][str(w)]
            with self.subTest(w=w):
                self.assertEqual(g["left"], ["inbox"], "no Tasks on a computer (#322, P287)")
                self.assertEqual(g["right"], [])
                self.assertEqual(g["ghost"], 0)
                self.assertIsNone(g["list"], "no task list")
                self.assertEqual(g["inboxMode"], "pinned")
                self.assertEqual(g["ldInbox"]["x"], 52, "docked right of the strip")
                self.assertEqual(g["ldInbox"]["w"], 260)
                self.assertFalse(g["pdInbox"], "the PO dock has no Inbox on a computer")
                self.assertIsNone(g["pdInboxEl"])
                self.assertFalse(g["pdInboxTab"])
                self.assertIsNone(g["bar"], "the bar's Inbox gives way")
                self.assertEqual(g["badge"], "1", "the strip button carries the count")
                self.assertGreater(g["rows"], 0)
                self.assertGreaterEqual(g["chat"]["x"], g["ldInbox"]["r"])
                self.assertLessEqual(g["chat"]["x"] - g["ldInbox"]["r"], 8)
                self.assertNoOverflow(g)

    def test_the_project_list_on_top_of_the_inbox(self):
        g = self.got["fresh"]
        self.assertIsNotNone(g["ldpBox"])
        self.assertLess(g["ldpBox"]["y"], g["ldInbox"]["y"] + 40, "on top of the panel")
        rows = g["ldp"]
        self.assertTrue(rows[0]["all"])
        self.assertEqual(rows[0]["t"], "All1")
        projs = [r["proj"] for r in rows if r["proj"]]
        self.assertEqual(sorted(projs), sorted(self.ids.values()), "every project, by name")
        motors = next(r for r in rows if r["proj"] == self.ids["Motors"])
        self.assertTrue(motors["cur"], "the PO screen's project is selected")
        self.assertEqual(motors["t"], "Motors1", "with its Inbox count")
        self.assertEqual(sum(r["cur"] for r in rows), 1)
        loose = [r for r in rows if r["room"] == self.loose]
        self.assertEqual(len(loose), 1, "the task in no project, a row of its own")
        self.assertIn("task", loose[0]["t"])
        self.assertFalse(g["scopeSw"], "the list chooses: no This/All switch")
        self.assertTrue(g["ibxTitle"].startswith("Inbox · Motors"), g["ibxTitle"])

    def test_a_project_row_without_a_po_opens_its_page(self):
        a = self.g("alpha")
        self.assertFalse(a["po"])
        self.assertEqual(a["sel"], self.ids["Alpha"])
        self.assertTrue(next(r for r in a["ldp"] if r["proj"] == self.ids["Alpha"])["cur"])
        self.assertEqual(a["rows"], 0)
        self.assertEqual(a["left"], ["inbox"])

    def test_a_task_in_no_project_opens_its_conversation(self):
        o = self.g("loose")
        self.assertTrue(o["detail"], o)
        self.assertIn(self.loose, o["frames"])
        self.assertTrue(next(r for r in o["ldp"] if r["room"] == self.loose)["cur"])
        self.assertIn("Loose carriage inspection", o["ibxTitle"])

    def test_make_po_from_the_dots_and_the_right_click(self):
        for k in ("dotsMenu", "ctxMenu"):
            m = self.g(k)
            with self.subTest(k=k):
                self.assertIsNotNone(m)
                self.assertEqual(m["items"][0]["t"], "Open")
                mp = [i for i in m["items"] if i["makepo"]]
                self.assertEqual(len(mp), 1)
                self.assertEqual(mp[0]["room"], self.loose)
                self.assertTrue(mp[0]["t"].startswith("Make PO of a new"))
        self.assertTrue(self.g("dotsMenu")["expanded"])
        self.assertIsNone(self.g("dotsClosed"), "Esc closes it")
        self.assertIsNone(self.g("ctxClosed"), "a click elsewhere closes it")

    def test_a_session_in_no_project(self):
        s = self.g("session")
        self.assertTrue(s["un"])
        self.assertIsNotNone(s["row"])
        self.assertIn("Notes on the trip", s["row"])
        self.assertIn("session", s["row"])
        self.assertEqual(s["opened"], ["sess-loose-1"])

    def test_a_project_row_with_a_po_opens_it(self):
        m = self.g("motors")
        self.assertTrue(m["po"])
        self.assertEqual(m["pid"], self.ids["Motors"])
        self.assertGreater(m["rows"], 0)

    def test_the_board_follows_the_project_picked(self):
        m, w = self.g("boardMotors"), self.g("boardWheels")
        self.assertEqual(m["fly"], "board", m)
        self.assertTrue(m["gear"], "Motors' Board shows its task")
        self.assertEqual(w["fly"], "board", "the Board stays out")
        self.assertEqual(w["pid"], self.ids["Wheels"])
        self.assertEqual(w["sel"], self.ids["Wheels"])
        self.assertFalse(w["gear"], "Wheels' Board, not Motors'")
        self.assertGreater(w["w"], 0)

    def test_the_inbox_on_another_projects_po(self):
        w = self.g("wheels")
        self.assertEqual(w["pid"], self.ids["Wheels"])
        self.assertEqual(w["rows"], 0, "nothing waits in Wheels")
        self.assertEqual(w["badge"], "")
        a = self.g("wheelsAll")
        self.assertGreater(a["rows"], 0, "All: Motors' row")
        self.assertTrue(a["ldp"][0]["cur"], "All selected")
        o = self.g("ibxOpened")
        self.assertEqual(o["pid"], self.ids["Motors"])
        self.assertTrue(o["po"])

    def test_the_modes(self):
        u = self.g("unpinned")
        self.assertEqual(u["inboxMode"], "unpinned")
        self.assertEqual(u["left"], ["inbox"])
        f = self.g("inboxFly")
        self.assertIsNone(f["ldInbox"], "back on its strip")
        self.assertEqual(f["chat"]["x"], 52, "the conversation from the strip")
        self.assertIsNone(f["bar"], "the strip still has it")
        fl = self.g("float")
        self.assertEqual(fl["inboxMode"], "float")
        self.assertIsNotNone(fl["ldInbox"])
        self.assertEqual(fl["chat"]["x"], 52)
        pi = self.g("pinned")
        self.assertEqual(pi["inboxMode"], "pinned")
        self.assertEqual(pi["ldInbox"]["x"], 52)
        wn = self.g("window")
        self.assertEqual(wn["inboxMode"], "window")
        self.assertTrue(wn["inboxOut"])
        self.assertIsNone(wn["ldInbox"])
        self.assertIsNotNone(wn["bar"], "in its window: the bar's Inbox is back")
        self.assertEqual(self.g("popIn")["inboxMode"], "pinned")
        r = self.g("right")
        self.assertEqual((r["left"], r["right"]), ([], ["inbox"]))
        self.assertGreater(r["ldInbox"]["x"], r["chat"]["x"])
        self.assertEqual(self.g("first")["left"], ["inbox"])
        for k in ("unpinned", "inboxFly", "float", "pinned", "window", "popIn", "right", "first"):
            with self.subTest(k=k):
                self.assertNoOverflow(self.g(k))

    def test_a_reload_keeps_the_layout(self):
        r = self.g("reloaded")
        self.assertEqual(r["left"], ["inbox"])
        self.assertEqual(r["inboxMode"], "unpinned")
        self.assertIsNone(r["ldInbox"])
        self.assertEqual(r["chat"]["x"], 52)

    def test_the_panels_menu(self):
        self.assertEqual([(m["id"], m["text"], m["checked"]) for m in self.g("menu1")],
                         [("inbox", "Inbox", False)], "no Panels › Tasks on a computer")
        self.assertEqual(self.g("menuInbox")["fly"], "inbox")
        m2 = {m["id"]: m for m in self.g("menu2")}
        self.assertTrue(m2["inbox"]["checked"] and m2["inbox"]["disabled"], "a docked Inbox stays")

    def test_reset_layout(self):
        b = self.g("beforeReset")
        self.assertEqual((b["left"], b["inboxMode"]), (["inbox"], "float"))
        r = self.g("reset")
        self.assertEqual(r["left"], ["inbox"])
        self.assertEqual(r["inboxMode"], "pinned")
        self.assertEqual(r["ldInbox"]["x"], 52)
        self.assertEqual(r["chat"]["x"], self.got["fresh"]["chat"]["x"])

    def test_a_layout_saved_before_moves_onto_the_strip(self):
        want = {"pinned": "pinned", "unpinned": "unpinned", "float": "float"}
        for was, mode in want.items():
            g = self.got["migr"][was]
            with self.subTest(was=was):
                self.assertEqual(g["left"], ["inbox"])
                self.assertEqual(g["ghost"], 0)
                self.assertEqual(g["inboxMode"], mode, "the Inbox as the PO dock kept it")
                self.assertFalse(g["pdInbox"])
                self.assertEqual(g["a5"], "1")
                self.assertNoOverflow(g)

    def test_a_layout_with_the_tasks_panel_drops_it(self):
        for k, mode in (("stripList", "pinned"), ("dockedList", "float")):
            g = self.got["migr"][k]
            with self.subTest(k=k):
                self.assertEqual(g["ghost"], 0, "no Tasks anywhere")
                self.assertIsNone(g["list"])
                self.assertEqual(g["left"], ["inbox"] if mode == "float" else [], g["left"])
                self.assertEqual(g["inboxMode"], mode, "the Inbox as the person kept it")
                self.assertIsNotNone(g["ldInbox"])
                self.assertLessEqual(g["chat"]["x"], 52 + 280 + 8, "nothing takes the list's place")
                self.assertNoOverflow(g)

    def test_a_resize_moves_the_inbox_between_the_docks(self):
        g = self.got
        wide, phone, back = g["rzWide"], g["rzPhone"], g["rzBack"]
        self.assertFalse(wide["pdInbox"])
        self.assertEqual(wide["left"], ["inbox"])
        self.assertTrue(phone["narrow"])
        self.assertIn("inbox", phone["tabs"], "a phone's PO dock has the Inbox as a tab")
        self.assertEqual(phone["tabs"][0], "po-chat")
        self.assertFalse(back["narrow"])
        self.assertFalse(back["pdInbox"])
        self.assertNotIn("inbox", back["tabs"])
        self.assertEqual(back["left"], ["inbox"])
        self.assertEqual(json.loads(back["lay"]), json.loads(wide["lay"]), "the wide layout as it was")
        self.assertNotIn('"inbox"', g["rzStoredWide"] or "", "the wide layout saved has no Inbox")
        p2w = g["phoneToWide"]
        self.assertFalse(p2w["narrow"])
        self.assertFalse(p2w["pdInbox"])
        self.assertNotIn("inbox", p2w["tabs"])
        self.assertNotIn('"inbox"', p2w["lay"])
        self.assertEqual(p2w["left"], ["inbox"])

    def test_800_wide(self):
        g = self.g("small")
        self.assertEqual(g["left"], ["inbox"])
        self.assertGreaterEqual(g["chat"]["w"], 360)
        self.assertNoOverflow(g)

    def test_a_phone_keeps_the_inbox_tab(self):
        g = self.g("phone")
        self.assertFalse(g["ld"], "no list dock on a phone")
        self.assertTrue(g["narrow"])
        self.assertTrue(g["pdInbox"], "the PO dock's Inbox tab")
        self.assertLessEqual(g["scrollW"], g["W"])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
