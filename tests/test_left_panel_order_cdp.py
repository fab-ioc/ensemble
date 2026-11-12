"""The left panel's projects in a steady order, full names on hover (#337, P309).
In headless Chrome over CDP, against a hub in a thread serving the pages.

Five projects with a PO each: Bravo, Gamma and Echo with an answer to check
(one Inbox row each; a PO chat's answers fold into one row), conversations in
that order, Zero and Delta with none (Zero's conversation the newest of all,
Delta's the oldest and its name too long for the panel). Two tasks in no
project with long titles. Checked:

* at 1440x900 and 1728x1117 the projects go by Inbox count, then latest
  conversation: Echo, Gamma, Bravo, Zero, Delta (Zero, the newest, under
  every project with a count);
* a click on the lowest project opens it and no row moves;
* a count change while the pointer is over the list waits until the pointer
  has left and 2 s have passed since the latest change;
* ties go by the latest conversation: a state change with nothing said moves
  nothing, a project's terminal session counts;
* an Inbox card whose text is cut at its lines shows whole, downwards;
* a cut project, task, session and the Inbox's title show whole after the
  hover delay (nothing at 150 ms), the copy as wide as the name;
* a cut PO tab, a cut chat panel tab and a cut file tab show whole (a file
  tab's tip stays off when it is drawn again while raised); a drag from the
  raised copy is Dock's; the tab's own tooltip is
  dropped while the setting is on; a click on the raised copy of a tab behind
  another brings it to the front; its x closes it;
* the setting off (Settings > Preferences): nothing shows whole and the tab's
  tooltip is back;
* a phone (390x844, touch) raises nothing and has no sideways scroll;
* the page threw nothing meanwhile.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or
Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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
DELTA = "Delta zero older but with a very long name that does not fit the panel"
# name, answers to check; registered in this order, conversations in SEED_ORDER (oldest first)
SEED = [("Bravo one older", 1), ("Zero newest", 0), ("Gamma one", 1), (DELTA, 0), ("Echo one more recent", 1)]
SEED_ORDER = [DELTA, "Bravo one older", "Gamma one", "Echo one more recent", "Zero newest"]
WANT = ["Echo one more recent", "Gamma one", "Bravo one older", "Zero newest", DELTA]
LONG_FILE = "Delta notes with a long file name that does not fit its tab.md"
LONG_ASK = ("Should the winter timetable keep the late trains on the coastal line while the bridge works run, "
            "or move them to the inland line for those weeks, and who tells the stations about it, and when? "
            "Say which one and the date the change starts.")
LOOSE = ["Loose task with a really long title that will be cut in the left panel",
         "Second loose task whose long title is also cut in its tab"]

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  // a mouse and hover, as a computer's: headless Chrome reports neither by default
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1728,1117',
    '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
const ROWS = `[...document.querySelectorAll('#ld-projs .ldp-row')].map(b => { const r = b.getBoundingClientRect(), n = b.querySelector('.ldp-name');
  return { name: n.textContent.trim(), proj: b.dataset.ldpProj || '', room: b.dataset.ldpRoom || '', sid: b.dataset.ldpSid || '', y: Math.round(r.top),
    cx: Math.round(r.left + 30), cy: Math.round(r.top + r.height / 2), sw: n.scrollWidth, cw: n.clientWidth, n: ((b.querySelector('.ldp-n') || {}).textContent || '').trim() }; })`;
// the raised copy, if any: its width and what it says
const EXP = `(() => { const c = document.querySelector('.row-expanded'); if (!c) return null; const r = c.getBoundingClientRect();
  const cs = getComputedStyle(c);
  return { solid: cs.backgroundImage !== 'none' || !(cs.backgroundColor.startsWith('rgba(') || cs.backgroundColor.includes(' / ') || cs.backgroundColor === 'transparent'), w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.left), right: Math.round(r.right), text: c.textContent.trim(),
    titles: [c, ...c.querySelectorAll('[title]')].filter(e => e.hasAttribute('title') && !e.matches('.dk-tab-x, .fd-tab-drag')).length, ids: c.querySelectorAll('[id]').length,
    titled: [c, ...c.querySelectorAll('[title]')].filter(e => e.hasAttribute('title')).map(e => e.className + '=' + e.getAttribute('title')) }; })()`;
const TAB = id => `(() => { const t = document.querySelector('#po-dock .dk-tab[data-dk-tab="${id}"]'); if (!t) return null; const r = t.getBoundingClientRect();
  const w = t.closest('.dk-tab-wrap').getBoundingClientRect();
  return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), sw: t.scrollWidth, cw: t.clientWidth, on: t.classList.contains('on'),
    title: t.getAttribute('title'), h: Math.round(w.height), sib: [...t.closest('.dk-tabs').querySelectorAll('.dk-tab')].map(x => x.dataset.dkTab) }; })()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {}; global.OUT = out;
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const mouse = (type, x, y) => c.send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: 1 }, sessionId);
    const move = (x, y) => mouse('mouseMoved', x, y);
    const clickAt = async (x, y) => { await move(x, y); await mouse('mousePressed', x, y); await mouse('mouseReleased', x, y); };
    const tap = async (x, y) => { for (const type of ['touchStart', 'touchEnd']) await c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] }, sessionId); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 4 && window.ensBooted === true', 30000);
    return { evalIn, until, move, clickAt, tap, shot, sessionId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const toPo = async (p, pid) => {
    await p.evalIn(`(() => { SELECTED_PROJECT = ${JSON.stringify(pid)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
    await p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!LD.dock && PD.pid === ' + JSON.stringify(pid), 30000);
  };
  // hover (from outside), the copy at 150 ms and at 450 ms
  const hover = async (p, x, y) => {
    await p.move(5, 5);
    // an earlier copy raised by focus (a clicked row) stays until the focus goes: clear it first
    await p.evalIn('(document.activeElement && document.activeElement !== document.body && document.activeElement.blur(), rowExpandHide(), 0)');
    await sleep(300);
    await p.move(x - 20, y); await p.move(x, y); await sleep(150);
    const at150 = await p.evalIn(EXP); await sleep(300);
    return { at150, at450: await p.evalIn(EXP) };
  };
  const away = async (p) => { await p.move(900, 600); await sleep(300); };
  const P = A.ids;
  try {
    out.widths = {};
    for (const [w, h] of A.widths) {
      const p = await page(w, h, false);
      await toPo(p, P['Bravo one older']);
      await p.until(`document.querySelectorAll('#ld-projs .ldp-row[data-ldp-proj]').length >= 5 && document.querySelectorAll('#ld-projs .ldp-n').length >= 3`, 20000);
      await sleep(1500);
      const o = out.widths[w] = {};
      o.before = await p.evalIn(ROWS);
      await p.shot(`order-before-click-${w}`);
      const low = o.before.filter(r => r.proj).pop();
      await p.clickAt(low.cx, low.cy);
      await p.until('PD.pid === ' + JSON.stringify(low.proj), 15000).catch(() => null);
      o.clicked = low.proj;
      await sleep(1500);
      o.afterClick = await p.evalIn(ROWS);
      o.pid = await p.evalIn('PD.pid');
      await away(p); await sleep(2500);
      o.afterLeave = await p.evalIn(ROWS);
      await p.shot(`order-after-click-${w}`);
      if (w !== 1440) {
        const d = (await p.evalIn(ROWS)).find(r => r.proj === P[A.delta]);
        o.delta = { row: d, ...(await hover(p, d.cx, d.cy)) };
        await p.close(); continue;
      }

      // ---- a count change under the pointer waits
      const two = o.afterLeave.find(r => r.proj === P['Bravo one older']);
      await p.move(two.cx, two.cy); await sleep(200);
      const y0 = await p.evalIn(ROWS);
      await p.evalIn(`(() => { IBX.busy = true; const r = IBX.data.rows.find(x => x.projectId === ${JSON.stringify(P['Gamma one'])});
        window._ibx337 = IBX.data; IBX.data = { ...IBX.data, rows: IBX.data.rows.concat([1, 2, 3].map(i => ({ ...r, id: r.id + 'x' + i }))) }; ibxRepaint(); return 0; })()`);
      await sleep(600);
      o.held = { y0: y0.map(r => r.name + '@' + r.y), over: (await p.evalIn(ROWS)).map(r => r.name + '@' + r.y) };
      await p.move(900, 600); await sleep(600);
      o.held.left600 = (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name);
      await sleep(2000);
      o.held.after = (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name);
      await p.evalIn(`(() => { IBX.data = window._ibx337; IBX.busy = false; ibxRepaint(); return 0; })()`);
      await sleep(2500);

      // ---- two changes under the pointer: the 2 s count from the second
      const more = (pid, k) => `(() => { IBX.busy = true; const r = window._ibx337.rows.find(x => x.projectId === ${JSON.stringify(pid)});
        IBX.data = { ...IBX.data, rows: IBX.data.rows.concat(Array.from({ length: ${k} }, (_, i) => ({ ...r, id: r.id + 'y' + i }))) }; ibxRepaint(); return 0; })()`;
      const bravo = (await p.evalIn(ROWS)).find(r => r.proj === P['Bravo one older']);
      await p.move(bravo.cx, bravo.cy); await sleep(200);
      await p.evalIn(more(P['Gamma one'], 3)); await sleep(1500);
      await p.evalIn(more(P['Bravo one older'], 5)); await sleep(900);
      o.twice = { over: (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name) };
      await p.move(900, 600); await sleep(400);
      o.twice.left400 = (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name);
      await sleep(2200);
      o.twice.after = (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name);
      await p.evalIn(`(() => { IBX.data = window._ibx337; IBX.busy = false; ibxRepaint(); return 0; })()`);
      await sleep(2500);

      // ---- latest conversation: a state change is none; a project's session is one
      const order = async () => { await p.evalIn('LDP.sig = "", ldpPaint(), 0'); return (await p.evalIn(ROWS)).filter(r => r.proj).map(r => r.name); };
      o.conv = { poUpdatedAt: await p.evalIn(`(() => { const r = ALL_ROWS.find(x => x.roomId === ${JSON.stringify(A.deltaPo)}); return r ? [r.updatedAt, r.convAt] : null; })()`) };
      await p.evalIn(`(() => { const now = Date.now() / 1000; for (const r of ALL_ROWS) if (r.roomId === ${JSON.stringify(A.deltaPo)}) { r.updatedAt = now; r.newsAt = r.newsAt || 0; } return 0; })()`);
      o.conv.metaOnly = await order();
      await p.evalIn(`(() => { const pj = projectById(${JSON.stringify(P[A.delta])});
        const r = { sessionId: 'sess-delta-337', label: 'A terminal session in Delta', headless: false, updatedAt: Date.now() / 1000 + 5, isLive: false, agent: 'claude' };
        ALL_ROWS.push(r); pj.sessions = (pj.sessions || []).concat([r]); return 0; })()`);
      o.conv.session = await order();
      await p.evalIn(`(() => { const pj = projectById(${JSON.stringify(P[A.delta])});
        ALL_ROWS = ALL_ROWS.filter(r => r.sessionId !== 'sess-delta-337'); pj.sessions = pj.sessions.filter(r => r.sessionId !== 'sess-delta-337'); return 0; })()`);
      o.conv.back = await order();
      await p.until('!_refreshInFlight', 10000);
      o.conv.past300 = await p.evalIn(`(() => { _refreshInFlight = true; window._rows337 = ALL_ROWS; ALL_ROWS = ALL_ROWS.filter(r => !r.headless); return 0; })()`);
      o.conv.past300 = await order();
      await p.evalIn('(ALL_ROWS = window._rows337, _refreshInFlight = false, LDP.sig = "", ldpPaint(), 0)');
      await sleep(300);

      // ---- full names on hover: a project, a task, a session, the Inbox's title
      let rows = await p.evalIn(ROWS);
      const delta = rows.find(r => r.proj === P[A.delta]);
      o.delta = { row: delta, ...(await hover(p, delta.cx, delta.cy)) };
      await p.shot('hover-project');
      const loose = rows.find(r => r.room === A.loose[0]);
      o.loose = { row: loose, ...(await hover(p, loose.cx, loose.cy)) };
      await away(p);
      // the list poll would replace ALL_ROWS and drop the injected session: hold it for this step
      await p.until('!_refreshInFlight', 10000);
      await p.evalIn(`(() => { _refreshInFlight = true;
        const un = projectById(UNASSIGNED_ID);
        const r = { sessionId: 'sess-337', label: 'A terminal session about the long winter timetable review', headless: false, updatedAt: Date.now() / 1000, isLive: false, agent: 'claude' };
        ALL_ROWS.push(r);
        if (un) un.sessions = (un.sessions || []).concat([{ sessionId: r.sessionId }]);
        LDP.sig = ''; ldpPaint(); return 0; })()`);
      rows = await p.evalIn(ROWS);
      const sess = rows.find(r => r.sid === 'sess-337');
      o.session = sess ? { row: sess, ...(await hover(p, sess.cx, sess.cy)) } : null;
      await away(p);
      await p.evalIn('_refreshInFlight = false, 0');
      await p.evalIn(`(() => { ldpScope('project'); ldSelSet(${JSON.stringify(P[A.delta])}); ibxRepaint(); return 0; })()`);
      await sleep(600);
      const t = await p.evalIn(`(() => { const n = document.querySelector('#ld-ibx-body .ibx-title-listed'); const r = n.getBoundingClientRect(); return { text: n.textContent.trim(), sw: n.scrollWidth, cw: n.clientWidth, cx: Math.round(r.left + 30), cy: Math.round(r.top + r.height / 2) }; })()`);
      o.ibxTitle = { row: t, ...(await hover(p, t.cx, t.cy)) };
      await p.shot('hover-inbox-title');
      await away(p);

      // ---- an Inbox card whose text is cut at its lines shows whole, downwards
      await p.evalIn(`(() => { ldpScope('project'); ldSelSet(${JSON.stringify(P['Gamma one'])}); IBX.busy = true; window._ibx337b = IBX.data;
        const r = IBX.data.rows.find(x => x.projectId === ${JSON.stringify(P['Gamma one'])});
        IBX.data = { ...IBX.data, rows: [{ ...r, id: 'q337', kind: 'answer', n: 1, items: undefined, reason: '', line: ${JSON.stringify(A.longAsk)} }].concat(IBX.data.rows) };
        ibxRepaint(); return 0; })()`);
      await sleep(600);
      const card = await p.evalIn(`(() => { const a = document.querySelector('#ld-ibx-body .ibx-row[data-ibx="q337"] a.ibx-link'); if (!a) return null; const n = a.querySelector('.ibx-line'), r = a.getBoundingClientRect();
        return { sh: n.scrollHeight, ch: n.clientHeight, w: Math.round(r.width), cx: Math.round(r.left + 30), cy: Math.round(r.top + 8) }; })()`);
      o.card = card ? { row: card, ...(await hover(p, card.cx, card.cy)) } : null;
      await p.shot('hover-inbox-card');
      // the card's Close sits under the copy: shown, and the first click there closes the card (no focus before)
      if (card) {
        o.cardClose = await p.evalIn(`(() => { const b = document.querySelector('#ld-ibx-body .ibx-row[data-ibx="q337"] [data-ibx-close]'); if (!b) return null; const r = b.getBoundingClientRect();
          const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2), top = document.elementFromPoint(x, y);
          window._clk337 = []; for (const t of ['pointermove', 'pointerdown', 'click']) document.addEventListener(t, e => window._clk337.push(t + ':' + (e.target.className || e.target.tagName) + ':' + e.isTrusted + ':' + !!document.querySelector('.row-expanded')), true);
          window._acts337 = []; window._act337 = ibxRowAct; ibxRowAct = (...a) => { window._acts337.push(a.slice(0, 2)); };
          return { w: Math.round(r.width), h: Math.round(r.height), x, y, underCopy: !!(top && top.closest('.row-expanded')), raised: !!document.querySelector('.row-expanded'), focus: document.activeElement === document.body }; })()`);
        if (o.cardClose) {
          await p.clickAt(o.cardClose.x, o.cardClose.y); await sleep(300);
          o.cardClose.dbg = await p.evalIn('window._clk337.slice(-12)');
          o.cardClose.acts = await p.evalIn('(() => { const a = window._acts337; ibxRowAct = window._act337; return a; })()');
        }
      }
      await away(p);
      await p.evalIn(`(() => { IBX.data = window._ibx337b; IBX.busy = false; ibxRepaint(); return 0; })()`);

      // ---- tabs: the PO's (Delta's long name), two chat panels in one stack
      await toPo(p, P[A.delta]);
      await sleep(1500);
      let pt = await p.evalIn(TAB('po-chat'));
      o.poTab = { tab: pt, ...(await hover(p, pt.x, pt.y)) };
      o.poTab.titleWhileOn = await p.evalIn(`document.querySelector('#po-dock .dk-tab[data-dk-tab="po-chat"]').getAttribute('title')`);
      await p.shot('hover-po-tab');
      await away(p);
      for (const r of A.loose) { await p.evalIn(`pdChatOpen(${JSON.stringify(r)}), 0`); await sleep(700); }
      const c1 = 'chat:' + A.loose[0], c2 = 'chat:' + A.loose[1];
      await p.until(`!!document.querySelector('#po-dock .dk-tab[data-dk-tab="${c2}"]')`, 10000);
      // the second as a tab of the first's stack, in front
      await p.evalIn(`(() => { const find = n => !n ? null : n.t === 'stack' ? (n.panels.includes(${JSON.stringify(c1)}) ? n : null) : (n.kids || []).map(find).find(Boolean);
        const s = find(PD.dock.layout().root); if (s && !s.panels.includes(${JSON.stringify(c2)})) PD.dock.moveTo(${JSON.stringify(c2)}, { kind: 'stack', stack: s }, 'center');
        PD.dock.activate(${JSON.stringify(c2)}); return 0; })()`);
      await sleep(800);
      let ct = await p.evalIn(TAB(c1));
      o.chatTab = { tab: ct, ...(await hover(p, ct.x, ct.y)) };
      await p.shot('hover-chat-tab');
      await p.clickAt(ct.x + 3, ct.y); await sleep(700);
      o.chatTab.afterClick = await p.evalIn(TAB(c1));
      ct = await p.evalIn(TAB(c1));
      await hover(p, ct.x, ct.y);
      o.chatTab.x = await p.evalIn(`(() => { const x = document.querySelector('.row-expanded .dk-tab-x'); if (!x) return null; const r = x.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
      // a drag from the raised copy is Dock's: the tab goes after the other one
      {
        const t2 = await p.evalIn(TAB(c2));
        await hover(p, ct.x, ct.y);
        const raised = await p.evalIn(EXP);
        const send = (type, x, y, b) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: b, clickCount: 1 }, p.sessionId);
        const tx = t2.x + 40;
        await send('mousePressed', ct.x, ct.y, 1);
        for (let i = 1; i <= 10; i++) await send('mouseMoved', ct.x + (tx - ct.x) * i / 10, ct.y, 1);
        await send('mouseReleased', tx, ct.y, 0);
        await sleep(800);
        o.chatTab.drag = { raised: !!raised, before: t2.sib, after: (await p.evalIn(TAB(c1))).sib, copy: await p.evalIn(EXP) };
        ct = await p.evalIn(TAB(c1));
        await hover(p, ct.x, ct.y);
        o.chatTab.x = await p.evalIn(`(() => { const x = document.querySelector('.row-expanded .dk-tab-x'); if (!x) return null; const r = x.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
      }
      if (o.chatTab.x) { await p.clickAt(o.chatTab.x.x, o.chatTab.x.y); await sleep(1000); }
      o.chatTab.afterX = await p.evalIn(TAB(c1));

      // ---- a file tab: its tip stays off while it is raised, even when it is drawn again
      await away(p);
      await p.evalIn(`(() => { const v = [...WS_VIEWS.values()].find(x => x.el && x.el.isConnected); wsOpenTab(v, ${JSON.stringify(A.longFile)}); return 0; })()`);
      const FT = `[...document.querySelectorAll('#po-dock .dk-tab[data-dk-tab^="file:"]')].find(t => t.textContent.includes('fit its tab'))`;
      await p.until(`!!(${FT})`, 15000);
      await sleep(800);
      const ftab = await p.evalIn(`(() => { const t = ${FT}; const r = t.getBoundingClientRect(); return { id: t.dataset.dkTab, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), sw: t.scrollWidth, cw: t.clientWidth }; })()`);
      o.fileTab = { tab: ftab, ...(await hover(p, ftab.x, ftab.y)) };
      await p.evalIn('pdTabTips(), 0'); await sleep(300);
      o.fileTab.redrawn = await p.evalIn(`(() => { const t = ${FT}; return { title: t.getAttribute('title'), kept: t.dataset.rxTitle || '', raised: !!document.querySelector('.row-expanded') }; })()`);
      await away(p);

      // ---- workspace tabs (files in the Workspace's own row, not as panels): click and x through the copy
      await p.evalIn(`(() => { window._pdOn337 = pdPanelsOn; pdPanelsOn = () => false;
        const v = [...WS_VIEWS.values()].find(x => x.el && x.el.isConnected); window._v337 = v;
        wsOpenTab(v, ${JSON.stringify(A.longFile)}); wsOpenTab(v, ${JSON.stringify(A.shortFile)}); wsPaintTabs(v); return 0; })()`);
      await sleep(800);
      const WT = `[...document.querySelectorAll('.wst-tab')].find(t => t.textContent.includes('fit its tab'))`;
      const wtab = async () => p.evalIn(`(() => { const t = ${WT}; if (!t) return null; const r = t.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), sw: t.querySelector('.wst-nm').scrollWidth, cw: t.querySelector('.wst-nm').clientWidth, on: t.classList.contains('on'), n: document.querySelectorAll('.wst-tab').length, top: (document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)) || {}).className, ok: rowExpandAllowed(t, { ...TAB_ROW_EXPAND, root: document.body }) }; })()`);
      let wt = await wtab();
      o.wsTab = { tab: wt };
      if (wt && wt.x) {
        Object.assign(o.wsTab, await hover(p, wt.x, wt.y));
        await p.shot('hover-workspace-tab');
        if (o.wsTab.at450) { await p.clickAt(wt.x - 20, wt.y); await sleep(600); }
        o.wsTab.clicked = await wtab();
        wt = o.wsTab.clicked;
        if (wt) {
          await hover(p, wt.x, wt.y);
          const x = await p.evalIn(`(() => { const x = document.querySelector('.row-expanded .wst-x'); if (!x) return null; const r = x.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
          if (x) { await p.clickAt(x.x, x.y); await sleep(600); }
          o.wsTab.x = !!x;
          o.wsTab.afterX = await wtab();
        }
      }
      await p.evalIn('(pdPanelsOn = window._pdOn337, wsPaintTabs(window._v337), 0)');

      // ---- the setting off
      await away(p);
      await p.evalIn(`(() => { const c = document.getElementById('pref-full-names'); c.checked = false; c.dispatchEvent(new Event('change')); return 0; })()`);
      o.off = { stored: await p.evalIn(`localStorage.getItem('cd-switcher-full-names')`), sw: await p.evalIn(`document.getElementById('sw-hover-names').checked`) };
      pt = await p.evalIn(TAB('po-chat'));
      o.off.poTab = (await hover(p, pt.x, pt.y)).at450;
      o.off.poTitle = await p.evalIn(`document.querySelector('#po-dock .dk-tab[data-dk-tab="po-chat"]').getAttribute('title')`);
      o.off.fileTitle = await p.evalIn(`(() => { const t = ${FT}; return t ? t.getAttribute('title') : null; })()`);
      rows = await p.evalIn(ROWS);
      const d2 = rows.find(r => r.proj === P[A.delta]);
      o.off.delta = (await hover(p, d2.cx, d2.cy)).at450;
      await p.evalIn(`(() => { const c = document.getElementById('pref-full-names'); c.checked = true; c.dispatchEvent(new Event('change')); return 0; })()`);
      o.scroll = await p.evalIn(`[document.documentElement.scrollWidth, document.documentElement.clientWidth]`);
      await p.close();
    }
    // ---- a phone: touch, nothing raised, no sideways scroll
    {
      const ph = await page(390, 844, true);
      await toPo(ph, P[A.delta]).catch(() => null);
      await sleep(1000);
      const t = await ph.evalIn(TAB('po-chat'));
      if (t) { await ph.tap(t.x, t.y); await sleep(600); }
      out.phone = { tab: !!t, exp: await ph.evalIn(EXP), scroll: await ph.evalIn(`[document.documentElement.scrollWidth, document.documentElement.clientWidth]`) };
      await ph.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.errors = c.errors;
  if (A.shots) fs.writeFileSync(path.join(A.shots, 'left-panel-order.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); console.error(JSON.stringify(global.OUT || {}).slice(0, 3000)); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class LeftPanelOrderInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-lpo-", ignore_cleanup_errors=True)
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
            # the Inbox rows stay: a page in front marks an answer seen
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        members = lambda home: [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                {"identity": "codex", "agent": "codex", "cwd": str(home)}]
        cls.ids, made = {}, {}
        for name, n in SEED:
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            cls.ids[name] = proj["id"]
            made[name] = (proj, n)
        for name in SEED_ORDER:
            proj, n = made[name]
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", members(home))
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            asked = []  # every point first, then the answers: a later message of theirs would read an earlier answer
            for i in range(n):
                text, pids = points.take(chatroom.get_room(po["id"], public=False), f"Point {i}", to="claude", key=f"k{i}")
                chatroom.post_message(po["id"], "user", text, to="claude")
                asked += pids
            for pid in asked:
                chatroom.post_message(po["id"], "claude", f"Re {pid}: done.", to="user")
            if not n:
                chatroom.post_message(po["id"], "user", "hello", to="claude")
            points.sync(po["id"], force=True)
            time.sleep(1.1)  # the next project's conversation the later one
            if name == DELTA:
                cls.delta_po = po["id"]
                (home / LONG_FILE).write_text("# Notes\n", encoding="utf-8")
                cls.long_file = str(home / LONG_FILE)
                (home / "a.md").write_text("# A\n", encoding="utf-8")
                cls.short_file = str(home / "a.md")
        # Delta's PO changed after every conversation, with nothing said: no conversation
        room = chatroom.get_room(cls.delta_po, public=False)
        chatroom.update_room(room)
        cls.loose = [chatroom.create_room(t, members(base))["id"] for t in LOOSE]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "ids": cls.ids, "delta": DELTA, "loose": cls.loose, "deltaPo": cls.delta_po,
                "longFile": cls.long_file, "shortFile": cls.short_file, "longAsk": LONG_ASK, "widths": WIDTHS, "shots": shots}
        script = base / "left_panel_order_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=480)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_LPO_DUMP"):
            Path(os.environ["ENSEMBLE_LPO_DUMP"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def w(self, w=1440):
        return self.got["widths"][str(w)]

    def assertWhole(self, o, want_w):
        self.assertIsNone(o["at150"], "nothing before the hover delay")
        self.assertIsNotNone(o["at450"], "shown whole after the delay")
        self.assertGreaterEqual(o["at450"]["w"], want_w, "the copy as wide as the name")
        self.assertEqual(o["at450"]["titles"], 0, f"no name tooltip in the copy (a tab's x keeps its own): {o['at450'].get('titled')}")
        self.assertEqual(o["at450"]["ids"], 0, "no ids copied")
        self.assertTrue(o["at450"]["solid"], "nothing shows through the copy")

    def test_projects_by_inbox_count_then_latest_conversation(self):
        for w, _ in WIDTHS:
            with self.subTest(w=w):
                names = [r["name"] for r in self.w(w)["before"] if r["proj"]]
                self.assertEqual(names, WANT)

    def test_a_click_moves_no_row(self):
        for w, _ in WIDTHS:
            o = self.w(w)
            with self.subTest(w=w):
                self.assertEqual(o["pid"], o["clicked"], "the click opened it")
                ys = lambda rows: [(r["name"], r["y"]) for r in rows]
                self.assertEqual(ys(o["afterClick"]), ys(o["before"]))
                self.assertEqual(ys(o["afterLeave"]), ys(o["before"]))

    def test_a_count_change_waits_until_the_pointer_has_left(self):
        h = self.w()["held"]
        self.assertEqual(h["over"], h["y0"], "nothing moves under the pointer")
        self.assertEqual(h["left600"], WANT, "nor just after it left")
        self.assertEqual(h["after"][0], "Gamma one", "then the new order")

    def test_two_changes_under_the_pointer_wait_2_s_from_the_second(self):
        t = self.w()["twice"]
        self.assertEqual(t["over"], WANT, "held under the pointer")
        self.assertEqual(t["left400"], WANT, "the first change is 2.4 s old, the second 1.3 s: still held")
        self.assertEqual(t["after"][:2], ["Bravo one older", "Gamma one"], "then the new order")

    def test_ties_go_by_the_latest_conversation_not_a_state_change(self):
        c = self.w()["conv"]
        updated, conv = c["poUpdatedAt"]
        self.assertGreater(updated, conv + 1, "the hub: Delta's PO changed after its last message")
        self.assertEqual(c["metaOnly"], WANT, "a state change moves nothing")
        self.assertEqual(c["session"], WANT[:3] + [DELTA, "Zero newest"], "a session in Delta newer than Zero's chat")
        self.assertEqual(c["back"], WANT)
        self.assertEqual(c["past300"], WANT, "tasks past the task list's 300 rows: the projects' own rows carry convAt")

    def test_a_cut_inbox_card_shows_whole_downwards(self):
        o = self.w()["card"]
        self.assertIsNotNone(o, "the card is listed")
        self.assertGreater(o["row"]["sh"], o["row"]["ch"] + 1, "cut at its lines")
        self.assertIsNone(o["at150"])
        self.assertIsNotNone(o["at450"])
        self.assertIn(LONG_ASK, o["at450"]["text"])
        self.assertGreater(o["at450"]["h"], o["row"]["ch"], "taller than the cut text")
        self.assertLessEqual(o["at450"]["w"], o["row"]["w"] + 1, "in the card's column")
        self.assertTrue(o["at450"]["solid"])

    def test_a_raised_card_s_close_takes_the_first_click(self):
        c = self.w()["cardClose"]
        self.assertIsNotNone(c, "the card has Close")
        self.assertTrue(c["raised"] and c["underCopy"], "Close sits under the raised copy")
        self.assertTrue(c["focus"], "nothing focused before")
        self.assertGreater(c["w"] * c["h"], 0, "Close stays shown while the copy is up")
        self.assertEqual(c["acts"], [["q337", "Closed"]], "the first click closed the card")

    def test_a_cut_file_tab_keeps_its_tip_off_when_drawn_again(self):
        o = self.w()["fileTab"]
        self.assertGreater(o["tab"]["sw"], o["tab"]["cw"] + 1, "cut")
        self.assertWhole(o, o["tab"]["sw"])
        r = o["redrawn"]
        self.assertIsNone(r["title"], "no native tip while raised")
        self.assertIn("drag the tab to move its panel", r["kept"], "its tip kept aside")
        self.assertTrue(self.w()["off"]["fileTitle"], "the setting off: its tip is back")

    def test_a_cut_workspace_tab_clicks_and_closes_through_its_copy(self):
        o = self.w()["wsTab"]
        self.assertIsNotNone(o["tab"], "the Workspace's own tab row shows the file")
        self.assertFalse(o["tab"]["on"], "behind the other file")
        self.assertGreater(o["tab"]["sw"], o["tab"]["cw"] + 1, "cut")
        self.assertWhole(o, o["tab"]["sw"])
        self.assertTrue(o["clicked"]["on"], "a click on the copy brings it to the front")
        self.assertTrue(o["x"], "the copy has its x")
        self.assertIsNone(o["afterX"], "x on the copy closed it")

    def test_a_drag_from_a_raised_tab_moves_it(self):
        # pressed on the copy, dragged past the other tab: Dock takes the panel there
        d = self.w()["chatTab"]["drag"]
        self.assertTrue(d["raised"], "the copy was up when pressed")
        self.assertNotEqual(d["after"], d["before"], "Dock's drag moved the tab")
        self.assertIsNone(d["copy"], "the copy went once it moved")

    def test_a_long_project_shows_whole_at_1728(self):
        o = self.w(1728)["delta"]
        self.assertGreater(o["row"]["sw"], o["row"]["cw"])
        self.assertWhole(o, o["row"]["sw"])

    def test_a_long_project_task_session_and_inbox_title_show_whole(self):
        o = self.w()
        for k in ("delta", "loose", "session", "ibxTitle"):
            with self.subTest(k=k):
                self.assertIsNotNone(o[k])
                self.assertGreater(o[k]["row"]["sw"], o[k]["row"]["cw"], "cut in the panel")
                self.assertWhole(o[k], o[k]["row"]["sw"])
        self.assertIn(DELTA, o["delta"]["at450"]["text"])
        self.assertIn(LOOSE[0], o["loose"]["at450"]["text"])
        self.assertIn("A terminal session about the long winter timetable review", o["session"]["at450"]["text"])
        self.assertIn(DELTA, o["ibxTitle"]["at450"]["text"])

    def test_a_cut_po_tab_shows_whole(self):
        o = self.w()["poTab"]
        self.assertGreater(o["tab"]["sw"], o["tab"]["cw"] + 1, "cut")
        self.assertWhole(o, o["tab"]["sw"])
        self.assertEqual(o["at450"]["h"], o["tab"]["h"], "the tab's height")
        self.assertIsNone(o["titleWhileOn"], "its own tooltip dropped")

    def test_a_cut_chat_tab_behind_another_shows_whole_click_and_x_work(self):
        o = self.w()["chatTab"]
        self.assertEqual(len(o["tab"]["sib"]), 2, o["tab"])
        self.assertFalse(o["tab"]["on"], "behind the other")
        self.assertWhole(o, o["tab"]["sw"])
        self.assertTrue(o["afterClick"]["on"], "a click on the copy brings it to the front")
        self.assertIsNotNone(o["x"], "the copy has the x")
        self.assertIsNone(o["afterX"], "the x closes it")

    def test_the_setting_off(self):
        o = self.w()["off"]
        self.assertEqual(o["stored"], "false")
        self.assertFalse(o["sw"], "the task list's switch follows")
        self.assertIsNone(o["poTab"])
        self.assertIsNone(o["delta"])
        self.assertTrue(o["poTitle"], "the tab's tooltip is back")

    def test_no_sideways_scroll(self):
        sw, cw = self.w()["scroll"]
        self.assertLessEqual(sw, cw)

    def test_a_phone_raises_nothing(self):
        ph = self.got["phone"]
        self.assertIsNone(ph["exp"])
        self.assertLessEqual(ph["scroll"][0], ph["scroll"][1])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
