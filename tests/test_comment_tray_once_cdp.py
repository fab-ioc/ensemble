"""A pending comment's tray shows once in the workspace, in the Send box (P360, #373).

In the dashboard's workspace a chat's own box is hidden and the shared Send
box (#workspace-compose-frame) sends for it. Both pages kept the same comments
(one store per room), so before P360 the "1 comment · Submit · Clear" tray
showed twice: in the chat above its hidden box and in the box under To: ….

Driven in headless Chrome at 1440x900, the PO chat docked, as a person does
it (the selection bar's 💬 Comment, Add, Submit, Clear pressed where the top
page hits, through the frames), with the box

  a. on the PO's main chat,
  b. on a thread (shown in place over the chat, #393; To: <thread> with ×),
  c. on the PO again, the thread closed (thread ▾'s Main).

Each time: exactly one tray on screen across the top page and all its frames,
in the box's frame; Submit hands the hub exactly one "## Review comments (1)"
message, sent where the box sends (in b, the thread); Clear leaves no tray
anywhere and nothing in the room's comment store.

$ENSEMBLE_SHOTS: a screenshot of case b with its one pending comment.
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
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which("node")
CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + chromeProfile(A), '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map(); const errs = [];
  // The box's page held back this long while Fetch is on (the switching case).
  let FETCH_DELAY = 0, PAUSED = 0, BOX_FRAME = '';
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data);
    if (m.method === 'Fetch.requestPaused') { const rq = m.params.requestId, sid = m.sessionId;
      // The box's page, or what it loads before its load event (the page's own
      // request, a subframe's navigation, is not paused here).
      const box = m.params.frameId === BOX_FRAME || (m.params.request.url.includes('workspace-compose=1') && m.params.resourceType === 'Document');
      if (box) PAUSED++;
      setTimeout(() => send('Fetch.continueRequest', { requestId: rq }, sid).catch(() => {}), box ? FETCH_DELAY : 0); }
    if (m.method === 'Runtime.exceptionThrown') errs.push(String(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).slice(0, 300));
    if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId: s } = await send('Target.attachToTarget', { targetId, flatten: true });
  await send('Page.enable', {}, s); await send('Runtime.enable', {}, s);
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
  const ev = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s);
    if (r.exceptionDetails) throw new Error(expr.slice(0, 160) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
  const until = async (expr, label, ms = 25000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + (label || expr)); };
  const press = async (x, y) => {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, buttons: 0 }, s);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount: 1 }, s);
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount: 1 }, s);
    await sleep(450);
  };
  // A press on a control that sends, as a person aims it: the pointer on it
  // first, still for longer than the page's click guard waits (#198: a click
  // on such a control that moved or appeared under the pointer within 400 ms
  // is dropped), and again where it is if it moved meanwhile.
  // Nothing is scrolled: the control is pressed where the person sees it.
  const aimAndPress = async (el, label) => {
    let p = await at(el, true), q = null;
    for (let i = 0; p && i < 5; i++) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p.x, y: p.y, buttons: 0 }, s);
      await sleep(600);
      q = await at(el, true);
      if (q && Math.abs(q.x - p.x) < 1 && Math.abs(q.y - p.y) < 1) break;
      p = q;
    }
    if (!p) return null;
    await press(p.x, p.y);
    return p;
  };
  // `el` (an expression for an element, in any frame of the page): its centre
  // on the top page, and whether a press there reaches it.
  const at = (el, still = false) => ev(`(() => { const e = ${el}; if (!e) return null; ${still ? '' : "e.scrollIntoView({ block: 'nearest' });"}
    let x = 0, y = 0, w = e.ownerDocument.defaultView; const r = e.getBoundingClientRect();
    x = r.left + r.width / 2; y = r.top + r.height / 2;
    const chain = []; for (let f = w.frameElement; f; f = f.ownerDocument.defaultView.frameElement) chain.push(f);
    for (const f of chain) { const b = f.getBoundingClientRect(); x += b.left + f.clientLeft; y += b.top + f.clientTop; }
    let hit = document.elementFromPoint(x, y), lx = x, ly = y;
    for (const f of chain.reverse()) { if (hit !== f) break; const b = f.getBoundingClientRect(); lx -= b.left + f.clientLeft; ly -= b.top + f.clientTop; hit = f.contentDocument.elementFromPoint(lx, ly); }
    return { x, y, hits: !!hit && (hit === e || e.contains(hit)) }; })()`);
  const pressOn = async (el, label) => { const p = await at(el); if (!p) throw new Error('missing: ' + label); await press(p.x, p.y); return p; };
  const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png' }, s); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };

  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.thrIn?.el.querySelector('iframe')`;
  const WCF = `document.getElementById('workspace-compose-frame')`;
  // Every #cmt-tray on screen: in the top page and every same-origin frame,
  // recursively, through frames that are themselves on screen. Each by the
  // frames it is in (id, else first class), top first.
  const TRAYS = `(() => { const out = [];
    const shown = e => e.getClientRects().length > 0 && !e.hidden && !e.closest('[hidden]');
    const walk = (d, where) => {
      for (const t of d.querySelectorAll('#cmt-tray')) if (shown(t))
        out.push({ where, text: t.querySelector('.cmt-tray-head')?.textContent.replace(/\\s+/g, ' ').trim() || '' });
      for (const f of d.querySelectorAll('iframe')) { if (!shown(f)) continue; let cd = null; try { cd = f.contentDocument; } catch (e) {}
        if (cd) walk(cd, where.concat(f.id || String(f.className).split(' ')[0] || 'iframe')); }
    };
    walk(document, []); return out; })()`;
  // The tray to press: the box's, else the first on screen.
  const TRAY_EL = `(() => { const shown = e => !!e && e.getClientRects().length > 0 && !e.closest('[hidden]');
    const box = ${WCF}?.contentDocument?.getElementById('cmt-tray'); if (shown(box)) return box;
    const all = []; const walk = d => { for (const t of d.querySelectorAll('#cmt-tray')) if (shown(t)) all.push(t);
      for (const f of d.querySelectorAll('iframe')) { if (!shown(f)) continue; try { if (f.contentDocument) walk(f.contentDocument); } catch (e) {} } };
    walk(document); return all[0] || null; })()`;
  // Each of `items` (an expression for {name: element}), as it is now, nothing
  // scrolled: wholly inside its frame's viewport and the top window's at
  // once, and what a press at its centre reaches, through the frames.
  const VIS = items => `(() => { const res = {};
    for (const [k, e] of Object.entries(${items})) {
      if (!e) { res[k] = null; continue; }
      const w = e.ownerDocument.defaultView, r = e.getBoundingClientRect();
      const inFrame = r.width > 0 && r.height > 0 && r.left >= -0.5 && r.top >= -0.5 && r.right <= w.innerWidth + 0.5 && r.bottom <= w.innerHeight + 0.5;
      const chain = []; for (let f = w.frameElement; f; f = f.ownerDocument.defaultView.frameElement) chain.push(f);
      let x = 0, y = 0; for (const f of chain) { const b = f.getBoundingClientRect(); x += b.left + f.clientLeft; y += b.top + f.clientTop; }
      const T = { l: r.left + x, t: r.top + y, r: r.right + x, b: r.bottom + y };
      const inTop = T.l >= -0.5 && T.t >= -0.5 && T.r <= innerWidth + 0.5 && T.b <= innerHeight + 0.5;
      const cx = (T.l + T.r) / 2, cy = (T.t + T.b) / 2;
      let hit = document.elementFromPoint(cx, cy), lx = cx, ly = cy;
      for (const f of [...chain].reverse()) { if (hit !== f) break; const b = f.getBoundingClientRect(); lx -= b.left + f.clientLeft; ly -= b.top + f.clientTop; hit = f.contentDocument.elementFromPoint(lx, ly); }
      res[k] = { inFrame, inTop, hits: !!hit && (hit === e || e.contains(hit)), top: Math.round(T.t), bottom: Math.round(T.b), h: Math.round(r.height),
        hit: hit ? (hit.id || String(hit.className) || hit.tagName).slice(0, 40) : null };
    }
    return res; })()`;
  // The box's tray and the box in its frame: the tray's measured height and
  // the height its content needs (each part at its full height: the list
  // without scrolling), the list's, the frame's and the window's.
  const ITEMS = words => `(() => { const d = ${WCF}.contentDocument, t = d.getElementById('cmt-tray');
    const item = ${JSON.stringify(words || '')} ? [...(t?.querySelectorAll('.cmt-item') || [])].find(i => i.textContent.includes(${JSON.stringify(words || '')})) : null;
    const o = { head: t?.querySelector('.cmt-tray-head strong') || null, submit: t?.querySelector('.cmt-submit') || null,
      clear: t?.querySelector('.cmt-clear') || null, input: d.getElementById('input') };
    if (${JSON.stringify(words || '')}) o.text = item?.querySelector('.cmt-item-n') || null;
    return o; })()`;
  const TRAYM = `(() => { const f = ${WCF}, w = f.contentWindow, d = f.contentDocument, t = d.getElementById('cmt-tray');
    if (!t) return null;
    const cs = w.getComputedStyle(t), list = t.querySelector('.cmt-list');
    const natural = [...t.children].reduce((a, c) => a + Math.max(c.offsetHeight, c.scrollHeight + c.offsetHeight - c.clientHeight), 0)
      + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    return { tray: Math.round(t.getBoundingClientRect().height * 10) / 10, natural, listScroll: list ? list.scrollHeight : 0, listClient: list ? list.clientHeight : 0,
      frame: Math.round(f.getBoundingClientRect().height), compose: Math.round(d.getElementById('compose').getBoundingClientRect().height),
      win: innerHeight, frameScroll: d.scrollingElement ? d.scrollingElement.scrollTop : 0, items: t.querySelectorAll('.cmt-item').length }; })()`;
  // The PO room's trays on screen, in any frame whose page is that room's.
  const PO_TRAYS_FN = `() => { const out = { n: 0, where: [] };
    const shown = e => e.getClientRects().length > 0 && !e.hidden && !e.closest('[hidden]');
    const walk = (d, where) => {
      let room = ''; try { const q = new URLSearchParams(d.defaultView.location.search); room = q.get('id') || q.get('room') || ''; } catch (e) {}
      if (room === ${JSON.stringify(A.po)}) for (const t of d.querySelectorAll('#cmt-tray')) if (shown(t)) { out.n++; out.where.push(where.join('>')); }
      for (const f of d.querySelectorAll('iframe')) { if (!shown(f)) continue; let cd = null; try { cd = f.contentDocument; } catch (e) {}
        if (cd) walk(cd, where.concat(f.id || String(f.className).split(' ')[0] || 'iframe')); }
    };
    walk(document, []);
    const po = ${PO}; out.poShown = !!po && shown(po);
    return out; }`;
  const PO_TRAYS = `(${PO_TRAYS_FN})()`;
  const STORE = `Object.keys(localStorage).filter(k => k.startsWith(${JSON.stringify('cd-comment:' + A.po + ':')})).length`;
  const SENT = `fetch('/test/sent').then(x => x.json())`;
  const BOX = `({ target: WC.target ? { kind: WC.target.kind, room: WC.target.room, thread: WC.target.thread || '' } : null,
    to: document.getElementById('workspace-compose-link')?.textContent || '', back: !document.getElementById('workspace-compose-back').hidden,
    shown: !document.getElementById('workspace-compose').hidden })`;
  const boxReady = `!document.getElementById('workspace-compose').hidden && ${WCF}?.contentDocument?.getElementById('send')?.disabled === false`;
  // The words `needle` selected in a balloon of the chat in `frame`.
  const choose = (frame, needle) => `(() => { const d = ${frame}.contentDocument, w = d.defaultView; w.getSelection().removeAllRanges();
    const t = [...d.querySelectorAll('#msgs .msg .text.md')].find(e => e.textContent.includes(${JSON.stringify(needle)}));
    if (!t) return false; t.scrollIntoView({ block: 'center' });
    const wk = d.createTreeWalker(t, NodeFilter.SHOW_TEXT); let x;
    while ((x = wk.nextNode())) { const i = x.nodeValue.indexOf(${JSON.stringify(needle)}); if (i >= 0) { w.getSelection().setBaseAndExtent(x, i, x, i + ${JSON.stringify(needle)}.length); return true; } }
    return false; })()`;
  // A comment as the person makes one: words selected, 💬 Comment, typed, Add.
  async function addComment(frame, needle, words) {
    const r = {};
    r.selected = await ev(choose(frame, needle));
    await until(`!!${frame}.contentDocument.querySelector('.sel-bar:not([hidden]) .sel-cmt:not([hidden])')`, 'selection bar');
    r.commentHit = (await pressOn(`${frame}.contentDocument.querySelector('.sel-bar .sel-cmt')`, 'Comment')).hits;
    await until(`!!${frame}.contentDocument.querySelector('.cmt-composer textarea')`, 'comment box', 5000);
    await send('Input.insertText', { text: words }, s);
    r.addHit = (await pressOn(`${frame}.contentDocument.querySelector('.cmt-composer .cmt-add')`, 'Add')).hits;
    await until(`${STORE} > 0`, 'stored', 5000).catch(() => null);
    await sleep(700);
    return r;
  }
  async function runCase(name, frame, needle, opts = {}) {
    const r = { box: await ev(BOX) };
    const words = 'Comment ' + name + ' one';
    r.add = await addComment(frame, needle, words);
    r.boxAfterAdd = await ev(BOX);
    r.trays = await ev(TRAYS);
    r.store = await ev(STORE);
    r.vis = await ev(VIS(ITEMS(words)));
    r.traym = await ev(TRAYM);
    if (opts.shot) await shot(opts.shot);
    // Submit, in the tray on screen.
    const sent0 = (await ev(SENT)).length;
    r.submitState = await ev(`(() => { const b = ${TRAY_EL}?.querySelector('.cmt-submit'); return b ? { disabled: b.disabled, label: b.textContent,
      note: b.closest('#cmt-tray').querySelector('.cmt-tray-note, .cmt-note')?.textContent || '' } : null; })()`);
    const t = await aimAndPress(TRAY_EL + `?.querySelector('.cmt-submit')`);
    r.submitHit = !!t && t.hits;
    await until(`${SENT}.then(l => l.length > ${sent0})`, 'sent', 10000).catch(() => null);
    await sleep(2500);   // a second send would be in by now
    r.sent = (await ev(SENT)).slice(sent0).map(b => ({ room: b.roomId, thread: b.thread || '', text: (b.text || '').slice(0, 300) }));
    if (opts.lands) {
      r.inThread = !!(await until(`!!${THR}?.contentDocument?.querySelector('#msgs')?.textContent.includes(${JSON.stringify(words)})`, 'in the thread', 10000).catch(() => false));
      r.inChat = await ev(`!!${PO}.contentDocument.querySelector('#msgs')?.textContent.includes(${JSON.stringify(words)})`);
    }
    r.traysAfterSubmit = await ev(TRAYS);
    r.storeAfterSubmit = await ev(STORE);
    // Another, then Clear.
    r.add2 = await addComment(frame, needle, 'Comment ' + name + ' two');
    r.trays2 = await ev(TRAYS);
    const c = await aimAndPress(TRAY_EL + `?.querySelector('.cmt-clear')`);
    r.clearHit = !!c && c.hits;
    await sleep(800);
    r.traysAfterClear = await ev(TRAYS);
    r.storeAfterClear = await ev(STORE);
    const sentAll = await ev(SENT);
    r.sentAfterClear = sentAll.length - sent0 - r.sent.length;
    return r;
  }
  const out = {};
  try {
    await send('Page.navigate', { url: A.base + '/' }, s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.po)})`, 'rows', 40000);
    await ev(`SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); 0`);
    await until(`document.body.classList.contains('po-dock') && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat');
    await until(`WC.target?.kind === 'po' && ${boxReady}`, 'box on the PO');
    await sleep(1000);
    // The first topic's thread: its reply has the thread's words.
    const THREAD = out.threadId = await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').dataset.thr`);
    out.guest = await ev(`${PO}.contentDocument.body.classList.contains('workspace-guest')`);
    const step = async (k, fn) => { try { out[k] = await fn(); } catch (e) { out[k] = { error: String(e && e.message || e).slice(0, 800) }; } };
    // a. The box on the PO's main chat.
    await step('po', () => runCase('po', PO, A.poWords));
    // b. A thread opened from its reply line: the box is the thread's.
    await step('thread', async () => {
      await pressOn(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr=${JSON.stringify(THREAD)}]')`, 'thread link');
      await until(`WC.target?.thread === ${JSON.stringify(THREAD)} && ${WCF}.contentWindow.liveTailBoxThread?.() === ${JSON.stringify(THREAD)} && ${boxReady}
        && !!${THR}?.contentDocument?.querySelector('#msgs .msg .text.md')`, 'box on the thread');
      await sleep(1000);
      return runCase('thread', THR, A.threadWords, { shot: 'thread-tray', lands: true });
    });
    // c. The thread closed (thread ▾'s Main: the thread in place goes): the box is the PO's again.
    await step('closed', async () => {
      await ev(`pdThreadInClose(false); 0`);
      await until(`WC.target?.kind === 'po' && !${WCF}.contentWindow.liveTailBoxThread?.() && ${boxReady}`, 'box back on the PO');
      await sleep(1000);
      const r = await runCase('closed', PO, A.poWords);
      r.panel = await ev(`!!PD.thrIn`);
      return r;
    });
    // d. A long tray: eight comments. It stays capped, its list scrolls, the box stays whole.
    await step('long', async () => {
      const r = { adds: [] };
      for (let i = 1; i <= 8; i++) r.adds.push(await addComment(PO, A.poWords, 'Comment long ' + i));
      await sleep(800);
      r.store = await ev(STORE);
      r.trays = await ev(TRAYS);
      r.vis = await ev(VIS(ITEMS('')));
      r.traym = await ev(TRAYM);
      const c = await aimAndPress(TRAY_EL + `?.querySelector('.cmt-clear')`);
      r.clearHit = !!c && c.hits;
      await sleep(800);
      r.traysAfterClear = await ev(TRAYS);
      r.storeAfterClear = await ev(STORE);
      return r;
    });
    // e. The box aimed at a task chat (another room) and back, the PO's
    // comments pending, the box's page held back 1.5 s each time it loads:
    // sampled every 10 ms, the PO chat on screen, its comments always show.
    await step('switch', async () => {
      const r = {};
      r.add = await addComment(PO, A.poWords, 'Comment switch one');
      await sleep(600);
      r.before = await ev(PO_TRAYS);
      FETCH_DELAY = 1500; PAUSED = 0;
      const tree = (await send('Page.getFrameTree', {}, s)).frameTree;
      const frames = []; (function walk(n) { frames.push(n.frame); (n.childFrames || []).forEach(walk); })(tree);
      BOX_FRAME = (frames.find(f => (f.url || '').includes('workspace-compose=1')) || {}).id || '';
      r.boxFrame = !!BOX_FRAME;
      await send('Network.enable', {}, s); await send('Network.setCacheDisabled', { cacheDisabled: true }, s);
      await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] }, s);
      await ev(`(() => { window.__samp = []; const f = ${PO_TRAYS_FN};
        window.__sampT = setInterval(() => { try { const x = f(), b = document.getElementById('workspace-compose-frame');
          __samp.push({ t: Math.round(performance.now()), n: x.n, where: x.where, poShown: x.poShown, target: WC.target?.room || '',
            box: new URLSearchParams(b.contentWindow.location.search).get('id') || '', rs: b.contentDocument?.readyState || '' }); }
          catch (e) { __samp.push({ err: String(e).slice(0, 120) }); } }, 10); return 0; })()`);
      const TASKF = `document.querySelector('iframe.dp-session[data-room=${JSON.stringify(A.task)}]')`;
      const boxOn = room => `${WCF}.contentWindow.location.search.includes(${JSON.stringify('id=' + encodeURIComponent(room))}) && ${WCF}.contentDocument.readyState === 'complete' && ${boxReady}`;
      await ev(`pdChatOpen(${JSON.stringify(A.task)}); 0`);
      await until(`WC.target?.room === ${JSON.stringify(A.task)} && !!${TASKF}?.contentDocument?.querySelector('#msgs') && ${boxOn(A.task)}`, 'box on the task', 30000);
      await sleep(1500);
      r.onTask = await ev(PO_TRAYS);
      r.onTaskTarget = await ev(`WC.target?.room || ''`);
      await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); 0`);
      await until(`WC.target?.room === ${JSON.stringify(A.po)}`, 'box aimed back', 3000)
        .catch(() => ev(`wcChoose({ kind: 'po', room: ${JSON.stringify(A.po)} }); 0`));
      await until(`WC.target?.room === ${JSON.stringify(A.po)} && ${boxOn(A.po)}`, 'box back on the PO', 30000);
      await sleep(1500);
      r.onPo = await ev(PO_TRAYS);
      const samp = await ev(`(() => { clearInterval(window.__sampT); return window.__samp; })()`);
      await send('Fetch.disable', {}, s); FETCH_DELAY = 0;
      r.paused = PAUSED;
      const seen = samp.filter(x => !x.err && x.poShown);
      r.samples = samp.length; r.samplesShown = seen.length; r.sampleErrors = samp.filter(x => x.err).slice(0, 3);
      r.zero = seen.filter(x => x.n === 0).slice(0, 5);
      r.zeroCount = seen.filter(x => x.n === 0).length;
      r.maxN = Math.max(0, ...seen.map(x => x.n));
      r.twice = seen.filter(x => x.n > 1).length;
      r.twiceAt = seen.filter(x => x.n > 1).slice(0, 5);
      r.boxes = [...new Set(samp.map(x => x.box + ':' + x.rs))];
      const c = await aimAndPress(TRAY_EL + `?.querySelector('.cmt-clear')`);
      r.clearHit = !!c && c.hits;
      await sleep(800);
      r.storeAfterClear = await ev(STORE);
      r.afterClear = await ev(PO_TRAYS);
      return r;
    });
    // f. The box sized by hand to its limit at 900 px tall, a comment in its
    // tray, then the window 600 px tall: box and tray are fitted again (P360
    // review 2), the frame and its To: line within 70% of the window.
    await step('resize', async () => {
      const r = {};
      const FIT = `(() => { const f = ${WCF}, b = f.getBoundingClientRect(), to = f.parentElement.querySelector('.wc-to');
        const d = f.contentDocument, c = d.getElementById('compose');
        return { win: innerHeight, frame: Math.round(b.height * 10) / 10, frameBottom: Math.round(b.bottom * 10) / 10, to: to ? to.offsetHeight : 0,
          compose: Math.round(c.getBoundingClientRect().height), sized: c.classList.contains('sized'),
          tray: d.getElementById('cmt-tray') ? Math.round(d.getElementById('cmt-tray').getBoundingClientRect().height) : 0,
          max: f.contentWindow.composeHeightLimits?.().max ?? null }; })()`;
      await ev(`localStorage.setItem('cd-compose-h', '5000'); 0`);
      await ev('window.__before = 1; 0');
      await send('Page.reload', {}, s);
      await until(`!window.__before && typeof WC !== 'undefined' && !!${PO}?.contentDocument?.querySelector('#msgs .msg .text.md') && WC.target?.kind === 'po' && ${boxReady}`, 'reloaded', 40000);
      await sleep(1500);
      r.add = await addComment(PO, A.poWords, 'Comment resize one');
      await sleep(1000);
      r.at900 = await ev(FIT);
      r.trays900 = await ev(TRAYS);
      await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 600, deviceScaleFactor: 1, mobile: false }, s);
      await sleep(2000);
      r.at600 = await ev(FIT);
      r.trays600 = await ev(TRAYS);
      r.vis600 = await ev(VIS(`(() => { const o = ${ITEMS('')}; return { head: o.head, submit: o.submit, input: o.input }; })()`));
      const c = await aimAndPress(TRAY_EL + `?.querySelector('.cmt-clear')`);
      r.clearHit = !!c && c.hits;
      await sleep(800);
      r.storeAfterClear = await ev(STORE);
      await ev(`localStorage.removeItem('cd-compose-h'); 0`);
      await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
      return r;
    });
    out.errors = errs.slice();
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

PO_WORDS = "I'm the PO for Motors"
THREAD_WORDS = "The board is wider now"


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class CommentTrayOnce(unittest.TestCase):
    """A hub with one project whose PO chat has two threads. /api/room/resume
    runs as the hub's (the PO's delivery stubbed: the text goes into its
    transcript), and records what it was given; /test/sent lists it."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-cmt-once-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"; state.mkdir()
        root = base / "EnsembleProjects"; root.mkdir()
        (base / "transcripts").mkdir(); (base / "cs").mkdir()
        sent = cls.sent = []
        real_room_resume = dashboard.Handler._room_resume

        def room_resume(h, data):
            sent.append(dict(data))
            return real_room_resume(h, data)
        transcript = base / "transcripts" / "C--po"; transcript.mkdir()
        sid = "once-po"
        path = transcript / f"{sid}.jsonl"
        lock = threading.Lock()

        def turn(role, text, ts):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".%03dZ" % int(ts % 1 * 1000)
            msg = {"role": role, "content": text if role == "user" else [{"type": "text", "text": text}]}
            if role == "assistant":
                msg["stop_reason"] = "end_turn"
            return {"type": role, "timestamp": stamp, "sessionId": sid, "message": msg}

        def resume(h, room, text="", to="", key="", quiet=False, **kw):
            # Typed into the PO's conversation at once, as a running PO reads it.
            dashboard._typed_sends(room["id"], [{"key": key, "text": text}])
            with lock, path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn("user", text, time.time())) + "\n")
            return {"delivered": 1}
        for patch in [
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
            mock.patch.object(dashboard, "_send_left_behind", lambda *a, **k: ""),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "_room_resume", room_resume),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj["id"]
        home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid, "cwd": str(home)}])
        cls.po = po["id"]
        room = chatroom.get_room(cls.po, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        t0 = time.time() - 3600
        path.write_text("".join(json.dumps(x) + "\n" for x in [
            turn("assistant", f"{PO_WORDS}: ask me anything about the board.", t0),
            turn("user", "Make the board wider.", t0 + 10),
            turn("assistant", f"{THREAD_WORDS}, and the panels fit.", t0 + 11),
            turn("user", "And the motors faster.", t0 + 20),
            turn("assistant", "The motors are twice as fast.", t0 + 21)]), encoding="utf-8")
        # A task chat of the project: another room for the box to be aimed at.
        task = chatroom.create_room("Faster motors", [{"identity": "claude", "agent": "claude", "sessionId": "once-task", "cwd": str(home)}])
        cls.task = task["id"]
        dashboard.assign_session_project(cls.task, cls.proj)
        dashboard.assign_task_number(cls.task, cls.proj)
        chatroom.post_message(cls.task, "user", "Please double the motor speeds")
        # Each topic sent as a new thread (#367: only the person starts one).
        for text, at in (("Make the board wider.", t0 + 10), ("And the motors faster.", t0 + 20)):
            threads.note_send(cls.po, "", "", [], False, text, now=at, new=True)

        class Handler(dashboard.Handler):
            def do_GET(self):
                if self.path == "/test/sent":
                    return self._send_json(200, sent)
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": base_url, "shots": shots,
                "proj": cls.proj, "po": cls.po, "task": cls.task, "poWords": PO_WORDS, "threadWords": THREAD_WORDS}
        run = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        cls.thread = cls.got["threadId"]
        dump = os.environ.get("COMMENT_TRAY_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1, ensure_ascii=False), encoding="utf-8")

    def check_case(self, name, thread=""):
        r = self.got[name]
        self.assertNotIn("error", r, r)
        a = r["add"]
        self.assertTrue(a["selected"] and a["commentHit"] and a["addHit"], ("the comment is made as a person makes it", a))
        self.assertEqual(r["store"], 1, ("one comment pending", r))
        # Once on screen, in the Send box's frame.
        self.assertEqual(len(r["trays"]), 1, ("exactly one tray on screen", r["trays"]))
        self.assertEqual(r["trays"][0]["where"][-1], "workspace-compose-frame", r["trays"])
        self.assertIn("1 comment", r["trays"][0]["text"], r["trays"])
        # Readable and usable as it is, nothing scrolled (review 1 of P360).
        self.whole(r["vis"], ("head", "text", "submit", "clear", "input"))
        m = r["traym"]
        self.assertGreaterEqual(m["tray"] + 1, m["natural"], ("the tray is as tall as its comment needs", m))
        self.assertLessEqual(m["listScroll"], m["listClient"] + 1, ("one comment needs no scrolling", m))
        # Submit: one message, where the box sends.
        self.assertTrue(r["submitHit"], r)
        self.assertEqual(len(r["sent"]), 1, ("exactly one message", r["sent"]))
        sent = r["sent"][0]
        text = sent["text"]
        if thread:
            # A thread with no point of its own: the box sends its first
            # message's link ahead of the words (sendResuming, #210).
            link, _, text = text.partition("\n\n")
            self.assertIn("msg=", link, sent)
        self.assertTrue(text.startswith("## Review comments (1)"), sent)
        self.assertEqual(sent["text"].count("## Review comments"), 1, sent)
        self.assertIn(f"Comment {name} one", sent["text"], sent)
        self.assertEqual(sent["room"], self.po, sent)
        self.assertEqual(sent["thread"], thread, sent)
        self.assertEqual(r["traysAfterSubmit"], [], ("the tray lets a sent comment go", r))
        self.assertEqual(r["storeAfterSubmit"], 0, r)
        # Another, then Clear: nothing on screen, nothing kept, nothing sent.
        self.assertEqual(len(r["trays2"]), 1, r["trays2"])
        self.assertEqual(r["trays2"][0]["where"][-1], "workspace-compose-frame", r["trays2"])
        self.assertTrue(r["clearHit"], r)
        self.assertEqual(r["traysAfterClear"], [], r)
        self.assertEqual(r["storeAfterClear"], 0, r)
        self.assertEqual(r["sentAfterClear"], 0, r)
        return r

    def whole(self, vis, names):
        for k in names:
            with self.subTest(part=k):
                v = vis.get(k)
                self.assertIsNotNone(v, (k, vis))
                self.assertTrue(v["inFrame"] and v["inTop"], (k, "wholly inside its frame's viewport and the window's", v))
                self.assertTrue(v["hits"], (k, "a press at its centre reaches it", v))

    def test_box_on_the_po_shows_the_tray_once(self):
        self.assertTrue(self.got["guest"], "the PO chat is the workspace's guest")
        r = self.check_case("po")
        self.assertEqual(r["boxAfterAdd"]["target"]["kind"], "po", r)

    def test_box_on_a_thread_shows_the_tray_once_and_sends_in_the_thread(self):
        r = self.check_case("thread", self.thread)
        box = r["boxAfterAdd"]
        self.assertEqual(box["target"], {"kind": "thread", "room": self.po, "thread": self.thread}, box)
        self.assertTrue(box["back"], ("To: <thread> with its ×", box))
        self.assertTrue(r["inThread"], ("the comments land in the thread", r))
        self.assertFalse(r["inChat"], ("and not in the PO's main chat", r))

    def test_thread_closed_box_back_on_the_po_shows_the_tray_once(self):
        r = self.check_case("closed")
        self.assertFalse(r["panel"], r)
        self.assertEqual(r["boxAfterAdd"]["target"]["kind"], "po", r)
        self.assertFalse(r["boxAfterAdd"]["back"], r)

    def test_a_long_tray_is_capped_and_scrolls_inside(self):
        r = self.got["long"]
        self.assertNotIn("error", r, r)
        self.assertTrue(all(a["selected"] and a["commentHit"] and a["addHit"] for a in r["adds"]), r["adds"])
        self.assertEqual(r["store"], 8, r)
        self.assertEqual(len(r["trays"]), 1, r["trays"])
        self.assertEqual(r["trays"][0]["where"][-1], "workspace-compose-frame", r["trays"])
        self.assertIn("8 comments", r["trays"][0]["text"], r["trays"])
        m = r["traym"]
        self.assertEqual(m["items"], 8, m)
        self.assertLessEqual(m["tray"], m["win"] * .3 + 3, ("capped at about 30% of the window", m))
        self.assertGreater(m["listScroll"], m["listClient"] + 10, ("its list scrolls inside it", m))
        self.whole(r["vis"], ("head", "submit", "clear", "input"))
        self.assertTrue(r["clearHit"], r)
        self.assertEqual(r["traysAfterClear"], [], r)
        self.assertEqual(r["storeAfterClear"], 0, r)

    def test_a_switch_of_the_box_never_leaves_the_comments_unshown(self):
        r = self.got["switch"]
        self.assertNotIn("error", r, r)
        self.assertEqual(r["before"]["n"], 1, r["before"])
        self.assertGreaterEqual(r["paused"], 2, ("the box's page was held back on both loads", r))
        self.assertGreater(r["samplesShown"], 100, r)
        self.assertEqual(r["sampleErrors"], [], r)
        self.assertEqual(r["zeroCount"], 0, ("never unshown while the PO chat is on screen", r["zero"]))
        self.assertEqual(r["twice"], 0, ("never shown twice, the box's page loading or not", r["twiceAt"]))
        # Settled: once, in the PO chat while the box is the task's, in the box once it is the PO's.
        self.assertEqual(r["onTaskTarget"], self.task, r)
        self.assertEqual(r["onTask"]["n"], 1, r["onTask"])
        self.assertEqual(r["onTask"]["where"], ["po-session"], r["onTask"])
        self.assertEqual(r["onPo"]["n"], 1, r["onPo"])
        self.assertEqual(r["onPo"]["where"], ["workspace-compose-frame"], r["onPo"])
        self.assertTrue(r["clearHit"], r)
        self.assertEqual(r["storeAfterClear"], 0, r)
        self.assertEqual(r["afterClear"]["n"], 0, r)

    def test_a_smaller_window_fits_the_hand_sized_box_and_its_tray_again(self):
        r = self.got["resize"]
        self.assertNotIn("error", r, r)
        a, b = r["at900"], r["at600"]
        self.assertTrue(a["sized"], ("the box is sized by hand", a))
        self.assertGreater(a["tray"], 0, a)
        self.assertEqual(len(r["trays900"]), 1, r["trays900"])
        self.assertEqual(b["win"], 600, b)
        self.assertLessEqual(b["frame"] + b["to"], .7 * 600 + 2, ("frame and To: line within 70% of the window", b))
        self.assertLessEqual(b["frameBottom"], b["win"], ("the frame ends inside the window", b))
        self.assertEqual(len(r["trays600"]), 1, r["trays600"])
        self.whole(r["vis600"], ("head", "submit", "input"))
        self.assertTrue(r["clearHit"], r)
        self.assertEqual(r["storeAfterClear"], 0, r)

    def test_no_script_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
