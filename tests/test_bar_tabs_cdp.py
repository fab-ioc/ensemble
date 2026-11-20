"""#393 (P388, P391): on a computer the tabs are in the top bar, one row.

ENSEMBLE / project ▾ / thread ▾ is the project's tab (#bar-grp, underlined while
the project's conversation is in front), then the other tabs of its stack (Dock's
own title bar of that stack, drawn over #bar-tabs); no visible PO tab, and "+N ▾"
when the row runs out of room. A thread of the project's conversation shows in
place in the project's tab (PD.thrIn), or as a tab of its own (a Ctrl/⌘-click, in
thread ▾ or on a reply line); Main goes back. The Send box aims at the thread in
place. A project switch brings back its last thread in place and its thread tabs;
a pinned tab stays. A task in no project picked in project ▾ is the top level. A
phone keeps its own bar (no tabs in it, its trail).

In headless Chrome over CDP, against a hub in a thread: Motors (a PO whose
conversation has two threads, and tasks), Garden (a PO) and a task in no project.
``ENSEMBLE_SHOTS`` keeps screenshots. Skipped without Node or Chrome.
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
import inbox  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0',
    '--user-data-dir=' + chromeProfile(A), '--no-first-run', '--disable-gpu', '--hide-scrollbars', '--disable-popup-blocking', 'about:blank'],
    {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  const errors = [];
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); }
    if (m.method === 'Runtime.exceptionThrown') { const d=m.params.exceptionDetails; errors.push((d.exception&&d.exception.description||d.text||'').slice(0,300)); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  await send('Runtime.enable',{},s);
  const size=(w,h) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:w<720},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,160)+' '+JSON.stringify(r.exceptionDetails).slice(0,600)); return r.result.value; };
  const until=async (expression,label,n=150) => { for(let i=0;i<n;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);}
    let st=null; try { st=await ev(BAR); } catch (e) {} throw Error('timeout '+label+' '+JSON.stringify(st)); };
  const snap = async name => { if (!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},s);
    require('fs').writeFileSync(A.shots+'/'+name+'.png',Buffer.from(r.data,'base64')); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const W = `document.getElementById('workspace-compose-frame')`;
  // The box's page is the one for its target (WC.base), loaded, its Send on.
  const boxReady = `(${W}?.contentDocument?.readyState==='complete' && !!WC.base && ${W}.contentDocument.URL.includes(WC.base) && typeof ${W}.contentWindow.sendResuming==='function' && ${W}.contentDocument.getElementById('send')?.disabled===false)`;
  // The bar as one sees it: its rows, the group, the tabs drawn in it.
  const BAR = `(() => {
    const vis = e => !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden';
    const hd = document.querySelector('header').getBoundingClientRect(), head = document.querySelector('.dk-head.bt-head');
    const grp = document.getElementById('bar-grp');
    const tabs = head ? [...head.querySelectorAll(':scope > .dk-tabs > .dk-tab-wrap')].filter(vis).map(w => w.querySelector('[data-dk-tab]').dataset.dkTab) : [];
    const tr = head ? head.getBoundingClientRect() : null, gr = grp.getBoundingClientRect();
    const more = head && head.querySelector(':scope > .dk-tabs-btn');
    return { on: document.body.classList.contains('bar-tabs'), header: Math.round(hd.height),
      grpOn: grp.classList.contains('on'), grpShadow: getComputedStyle(grp).boxShadow,
      proj: document.getElementById('proj-switch-name').textContent, thr: vis(document.getElementById('thr-switch')) ? document.getElementById('thr-switch-name').textContent : null,
      tabs, poTab: !!head && vis(head.querySelector('[data-dk-tab="po-chat"]')),
      inBar: !!tr && tr.top >= hd.top - 1 && tr.bottom <= hd.bottom + 1, afterGrp: !!tr && tr.left >= gr.right - 1,
      tabsRow: tr ? [Math.round(tr.top), Math.round(tr.bottom)] : null, grpRow: [Math.round(gr.top), Math.round(gr.bottom)],
      more: more && vis(more) ? (more.dataset.n || '') : null,
      front: PD.dock ? PD.dock.frontOf('po-chat') : '', trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].filter(vis).map(e => e.dataset.crumb),
      thrIn: PD.thrIn ? { tid: PD.thrIn.tid, mid: PD.thrIn.mid, shown: pdThreadInShown(), hidden: PD.thrIn.el.hidden } : null,
      threadTabs: [...PD.rt].filter(([, e]) => e.kind === 'thread').map(([id]) => id),
      target: WC.target, top: barTopTask() ? barTopTask().roomId : null, pid: PD.pid, scrollW: document.documentElement.scrollWidth, vw: innerWidth };
  })()`;
  // A real press and release at an element of the page, or of a frame of it.
  const clickAt = async (frameExpr, selector, modifiers = 0) => {
    const p = await ev(`(()=>{const f=${frameExpr || 'null'},r=f?f.getBoundingClientRect():{x:0,y:0},d=f?f.contentDocument:document,e=d.querySelector(${JSON.stringify(selector)});
      if(!e) throw Error('no '+${JSON.stringify(selector)}); e.scrollIntoView({block:'nearest'});const q=e.getBoundingClientRect();return {x:r.x+q.x+q.width/2,y:r.y+q.y+q.height/2}})()`);
    for (const type of ['mouseMoved','mousePressed','mouseReleased'])
      await send('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1,modifiers},s);
  };
  const boxRoute = async () => ev(`(async()=>{const w=${W}.contentWindow,old=w.postOk;let body;
    w.postOk=async(_path,b)=>{body=b;return {}};try{await w.sendResuming('probe','', 'probe-key',[]);return {room:body.roomId,thread:body.thread||''};}
    finally{w.postOk=old}})()`);
  const menuOpen = async () => {
    await clickAt(null, '#thr-switch');
    await until(`!document.getElementById('thr-menu').hidden && document.querySelectorAll('#thr-menu [data-thr-pick]').length >= 3`, 'thread menu');
    return ev(`[...document.querySelectorAll('#thr-menu [data-thr-pick]')].map(b => [b.dataset.thrPick, b.querySelector('.pm-name').textContent, b.classList.contains('on')])`);
  };
  const threadsIn = `[...${PO}.contentDocument.querySelectorAll('#msgs .thr-open[data-thr]')].map(b=>b.dataset.thr)`;
  const sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  const out = {};
  try {
    await size(1728,1117);
    // The first page opens on no task (else on Wipers, the Inbox's waiting card); a reload as a person's.
    const {identifier:noBootOpen} = await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.ensBootOpen = false;'},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted===true`,'booted');
    await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:noBootOpen},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)}) && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.loose)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && PD.restored && ${threadsIn}.length===2`,'PO chat with two threads');
    await until(`document.getElementById('workspace-compose')?.hidden===false && WC.target?.kind==='po' && ${boxReady}`,'workspace box on the PO');
    await sleep(500);
    const [ta, tb] = await ev(threadsIn);
    out.ta = ta; out.tb = tb;
    // 1. One row: the project's tab, no PO tab.
    out.start = await ev(BAR);
    await snap('bar-start');
    // 2. A task from the board: a new tab after the group, the group not in front.
    await ev(`pdReveal('board'); if (VIEW_MODE !== 'board') setViewMode('board'); 0`);
    try { await until(`!!document.querySelector('.board .card[data-room=${JSON.stringify(A.brakes)}] .ctitle')`, 'board card', 60); }
    catch (e) { throw Error(e.message + JSON.stringify(await ev(`({fly:PD.dock.flyOpen(), board:[...document.querySelectorAll('.board')].length, el:(PD.els.board?.innerHTML||'').slice(0,300), conn:!!PD.els.board?.isConnected, cards:[...document.querySelectorAll('.card')].map(c=>c.dataset.room+' '+c.closest('.col')?.dataset.col), rows:ALL_ROWS.filter(r=>r.roomId===${JSON.stringify(A.brakes)}).map(r=>({p:r.projectId, wf:r.workflow, d:r.draft}))})`))); }
    await ev(`document.querySelector('.board .card[data-room=${JSON.stringify(A.brakes)}] .ctitle').click(); 0`);
    await until(`PD.rt.has('chat:'+${JSON.stringify(A.brakes)}) && PD.dock.frontOf('po-chat')==='chat:'+${JSON.stringify(A.brakes)}`, 'task tab from the board');
    await sleep(500);
    out.board = await ev(BAR);
    await snap('bar-task');
    // ... and the group clicked: the conversation in front again, underlined.
    await clickAt(null, '#bar-grp .bar-thr-sep');
    await until(`PD.dock.frontOf('po-chat')==='po-chat'`, 'group click');
    await sleep(300);
    out.grpClick = await ev(BAR);
    // 3. A task opened from an Inbox card (a waiting row: its link opens the room): a tab too.
    const IBX_LINK = `li.ibx-row[data-kind="waiting"] a.ibx-link[data-room="${A.wipers}"]`;
    await ev(`if (window.LD && LD.dock) LD.dock.reveal('inbox'); 0`);
    await until(`!!IBX.data && !!document.querySelector(${JSON.stringify(IBX_LINK)}) && !document.querySelector(${JSON.stringify(IBX_LINK)}).dataset.mid`, 'the Inbox card of Wipers');
    await clickAt(null, IBX_LINK);
    await until(`PD.rt.has('chat:'+${JSON.stringify(A.wipers)}) && PD.dock.frontOf('po-chat')==='chat:'+${JSON.stringify(A.wipers)}`, 'task tab from the Inbox');
    await sleep(400);
    out.inbox = await ev(BAR);
    await ev(`closeDetail(); PD.dock.activate('po-chat'); 0`);
    await until(`PD.dock.frontOf('po-chat')==='po-chat' && !SELECTED_SID`, 'back on the conversation');
    await sleep(400);
    // 4. thread ▾: a thread in place, then Main.
    out.menu = await menuOpen();
    await clickAt(null, `#thr-menu [data-thr-pick="${ta}"]`);
    await until(`PD.thrIn?.tid===${JSON.stringify(ta)} && pdThreadInShown() && !!PD.thrIn.el.querySelector('iframe')?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread in place');
    await until(`WC.target?.kind==='thread' && WC.target.thread===${JSON.stringify(ta)} && ${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(ta)} && ${boxReady}`, 'box on the thread in place');
    await sleep(400);
    out.inPlace = await ev(BAR);
    out.inPlace.route = await boxRoute();
    out.inPlace.cover = await ev(`(()=>{const a=PD.thrIn.el.getBoundingClientRect(),b=PD.els['po-chat'].getBoundingClientRect();
      return {w:Math.round(a.width),h:Math.round(a.height),pw:Math.round(b.width),ph:Math.round(b.height),panels:PD.dock.panels().map(p=>p.id).filter(id=>/^thread:/.test(id))}})()`);
    out.inPlace.menu = await menuOpen();
    await snap('bar-thread-in-place');
    await clickAt(null, '#thr-menu [data-thr-pick=""]');
    await until(`!PD.thrIn && WC.target?.kind==='po' && !${W}.contentWindow.liveTailBoxThread?.() && ${boxReady}`, 'Main');
    await sleep(300);
    out.main = await ev(BAR);
    out.main.side = await ev(`${PO}.contentWindow.sideNow()`);
    // 5. Ctrl-click in thread ▾: a tab of its own; the conversation stays on Main.
    await menuOpen();
    await clickAt(null, `#thr-menu [data-thr-pick="${tb}"]`, 2);
    await until(`PD.rt.has(pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(tb)}, ''))`, 'thread tab from the menu');
    await sleep(500);
    out.menuTab = await ev(BAR);
    out.menuTab.id = await ev(`pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(tb)}, '')`);
    out.menuTab.find = await ev(`pdThreadFind({room:${JSON.stringify(A.po)},thread:${JSON.stringify(tb)}})?.[0]||null`);
    out.menuTab.saved = await ev(`JSON.parse(localStorage.getItem('cd-thread-tabs')||'[]')`);
    await snap('bar-thread-tab');
    // 6. On a reply line in the conversation: a trusted Ctrl-click opens a tab,
    // a plain click shows it in place.
    await ev(`PD.dock.activate('po-chat'); 0`);
    await until(`PD.dock.frontOf('po-chat')==='po-chat'`, 'conversation in front');
    await sleep(300);
    await clickAt(PO, `#msgs .thr-open[data-thr="${ta}"]`, 2);
    await until(`PD.rt.has(pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(ta)}, ''))`, 'thread tab from a ctrl-click');
    await sleep(500);
    out.ctrlLine = await ev(BAR);
    out.ctrlLine.side = await ev(`${PO}.contentWindow.sideNow()`);
    // The tab closed by its own ×.
    const taTab = await ev(`pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(ta)}, '')`);
    await clickAt(null, `.dk-head.bt-head [data-dk-close="${taTab}"]`);
    await until(`!PD.rt.has(${JSON.stringify(taTab)})`, 'thread tab closed');
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(300);
    await clickAt(PO, `#msgs .thr-open[data-thr="${ta}"]`);
    await until(`PD.thrIn?.tid===${JSON.stringify(ta)} && pdThreadInShown()`, 'thread in place from a click');
    await until(`WC.target?.thread===${JSON.stringify(ta)} && ${boxReady}`, 'box on the clicked thread');
    await sleep(400);
    out.plainLine = await ev(BAR);
    out.plainLine.route = await boxRoute();
    // 7. A pinned tab, then a project switch and back: the thread in place and
    // the thread tab come back, the pinned task tab stays through it.
    await ev(`pdChatOpen(${JSON.stringify(A.brakes)}); 0`);
    await until(`PD.rt.has('chat:'+${JSON.stringify(A.brakes)})`, 'brakes tab');
    await sleep(300);
    await ev(`document.querySelector('[data-pd-pin="chat:'+${JSON.stringify(A.brakes)}+'"]').click(); 0`);
    await sleep(300);
    out.pinned = await ev(`PdWorkspace.isPinned ? PdWorkspace.isPinned(PD_WS, 'chat:'+${JSON.stringify(A.brakes)}) : PD_WS.pins.includes('chat:'+${JSON.stringify(A.brakes)})`);
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(300);
    await ev(`openProjectPage(${JSON.stringify(A.proj2)}); 0`);
    await until(`PD.pid===${JSON.stringify(A.proj2)} && ${PO}?.dataset.room===${JSON.stringify(A.po2)}`, 'Garden');
    await sleep(1500);
    out.garden = await ev(BAR);
    out.garden.panels = await ev(`PD.dock.panels().map(p=>p.id).filter(id=>/^(chat|thread):/.test(id))`);
    await snap('bar-garden');
    await ev(`openProjectPage(${JSON.stringify(A.proj)}); 0`);
    await until(`PD.pid===${JSON.stringify(A.proj)} && ${PO}?.dataset.room===${JSON.stringify(A.po)} && PD.thrIn?.tid===${JSON.stringify(ta)} && pdThreadInShown()`, 'Motors again');
    await until(`PD.rt.has(${JSON.stringify(out.menuTab.id)})`, 'thread tab back');
    await sleep(1200);
    out.back = await ev(BAR);
    out.back.panels = await ev(`PD.dock.panels().map(p=>p.id).filter(id=>/^(chat|thread):/.test(id))`);
    try { await until(`WC.target?.kind==='thread' && WC.target.thread===${JSON.stringify(ta)} && ${boxReady}`, 'box on the thread back', 40); } catch (e) {}
    out.back.target = await ev(`WC.target`);
    // 8. A reload: the thread tab comes back from cd-thread-tabs.
    await send('Page.reload',{},s);
    await until(`typeof PD!=='undefined' && !!PD.dock && PD.restored && PD.rt.has(${JSON.stringify(out.menuTab.id)})`, 'thread tab after a reload');
    await sleep(800);
    out.reload = await ev(BAR);
    // 8b. The box and the thread in place: a task's tab pressed takes it, the
    // project's tab pressed gives it back to the thread; a task opened and
    // closed gives it back too; a task's tab closed by its ×, likewise.
    const onThread = `WC.target?.kind==='thread' && WC.target.thread===${JSON.stringify(ta)} && ${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(ta)} && ${boxReady}`;
    const onBrakes = `WC.target?.kind==='task' && WC.target.room===${JSON.stringify(A.brakes)} && ${boxReady}`;
    out.box = {};
    await clickAt(null, `.dk-head.bt-head .dk-tab[data-dk-tab="chat:${A.brakes}"]`);
    await until(onBrakes, 'box on a task tab pressed');
    out.box.taskTab = await ev(`WC.target`);
    await sleep(700);
    await clickAt(null, '#bar-grp .bar-thr-sep');
    await until(onThread, 'box back on the thread from the group');
    out.box.grp = await ev(`WC.target`);
    out.box.grpRoute = await boxRoute();
    await sleep(700);
    await ev(`openDetail(${sid(A.brakes)}); 0`);
    await until(`SELECTED_SID===${sid(A.brakes)} && PD.dock.frontOf('po-chat')==='chat:'+${JSON.stringify(A.brakes)}`, 'task opened');
    await ev(`wcChoose({kind:'task',room:${JSON.stringify(A.brakes)}}); 0`);
    await until(onBrakes, 'box on the opened task');
    await sleep(700);
    await ev(`closeDetail(); 0`);
    await until(`!SELECTED_SID && PD.dock.frontOf('po-chat')==='po-chat'`, 'task closed');
    try { await until(onThread, 'box on the thread after the task closed', 40); } catch (e) {}
    out.box.closed = await ev(`WC.target`);
    // A task's tab closed by its × while the box is on it.
    await ev(`pdChatOpen(${JSON.stringify(A.wipers)}); 0`);
    await until(`PD.rt.has('chat:'+${JSON.stringify(A.wipers)})`, 'wipers tab');
    await sleep(400);
    await clickAt(null, `.dk-head.bt-head .dk-tab[data-dk-tab="chat:${A.wipers}"]`);
    await until(`WC.target?.kind==='task' && WC.target.room===${JSON.stringify(A.wipers)} && ${boxReady}`, 'box on wipers');
    await sleep(700);
    await clickAt(null, `.dk-head.bt-head [data-dk-close="chat:${A.wipers}"]`);
    await until(`!PD.rt.has('chat:'+${JSON.stringify(A.wipers)})`, 'wipers closed');
    try { await until(`WC.target?.kind!=='task' && ${boxReady}`, 'box after the × ', 40); } catch (e) {}
    await sleep(500);
    out.box.x = { target: await ev(`WC.target`), front: await ev(`PD.dock.frontOf('po-chat')`), thrIn: await ev(`!!PD.thrIn && pdThreadInShown()`) };
    // 8c. The keyboard from the group (review 1): the box on the thread in
    // place, project ▾ focused, ArrowRight: the first tab in front, and the box
    // goes with it (a real key, not Dock's own row).
    await clickAt(null, '#bar-grp .bar-thr-sep');
    await until(onThread, 'box on the thread before the keys');
    await sleep(700);
    await ev(`document.getElementById('proj-switch').focus(); 0`);
    const key = async (k, vk) => { for (const type of ['rawKeyDown','keyUp']) await send('Input.dispatchKeyEvent',{type,key:k,code:k,windowsVirtualKeyCode:vk},s); };
    await key('ArrowRight', 39);
    const first = await ev(`barTabIds()[0]`);
    try { await until(`PD.dock.frontOf('po-chat')===${JSON.stringify(first)} && WC.target?.kind==='task' && 'chat:'+WC.target.room===${JSON.stringify(first)} && ${boxReady}`, 'box with the key'); } catch (e) {}
    out.keys = { first, front: await ev(`PD.dock.frontOf('po-chat')`), target: await ev(`WC.target`), focus: await ev(`document.activeElement?.dataset?.dkTab||document.activeElement?.id||''`) };
    out.keys.route = await boxRoute();
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(500);
    // 8d. The conversation out of the page's dock (reviews 1, 2): floated, the
    // tabs of the stack it left stay in the bar, one row, the group not
    // underlined; minimised, the stack's tabs stay there, none underlined, and
    // one pressed opens the stack again.
    // No other title bar shows a task's tab: the bar's row is the only one.
    const OTHER_ROWS = `[...PD_ROOT.querySelectorAll('.dk-head:not(.bt-head) [data-dk-tab]')].filter(t => t.getBoundingClientRect().width > 0 && /^(chat|thread):/.test(t.dataset.dkTab)).map(t => t.dataset.dkTab)`;
    out.beforeOut = await ev(BAR);
    await ev(`PD.dock.float('po-chat'); 0`);
    await sleep(600);
    out.floated = await ev(BAR);
    out.floated.otherRows = await ev(OTHER_ROWS);
    out.floated.poFloat = await ev(`!!PD_ROOT.querySelector('.dk-float [data-dk-tab="po-chat"]')`);
    await ev(`PD.dock.dockBack('po-chat'); 0`);
    await until(`!PD_ROOT.querySelector('.dk-float [data-dk-tab="po-chat"]') && document.body.classList.contains('bar-tabs')`, 'docked back');
    await sleep(500);
    out.dockedBack = await ev(BAR);
    await ev(`PD.dock.toggleMin('po-chat'); 0`);
    await sleep(600);
    out.minimised = await ev(BAR);
    out.minimised.otherRows = await ev(OTHER_ROWS);
    out.minimised.min = await ev(`!!PD_ROOT.querySelector('.dk-stack.dk-min > .dk-head.bt-head')`);
    out.minimised.underlined = await ev(`[...document.querySelectorAll('.dk-head.bt-head .dk-tab')].map(t => [t.dataset.dkTab, t.classList.contains('on'), getComputedStyle(t).boxShadow, getComputedStyle(t).borderBottomColor]).filter(x => x[2].includes('inset') || x[3] !== 'rgba(0, 0, 0, 0)')`);
    const lastTab = out.minimised.tabs[out.minimised.tabs.length - 1];
    await clickAt(null, `.dk-head.bt-head .dk-tab[data-dk-tab="${lastTab}"]`);
    await until(`!PD_ROOT.querySelector('.dk-stack.dk-min') && PD.dock.frontOf('po-chat')===${JSON.stringify(lastTab)}`, 'a bar tab opens the minimised stack');
    await sleep(500);
    out.unminimised = await ev(BAR);
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(400);
    // 8e. Split, then float (review 4): a task moved to a stack of its own on
    // the left, then the conversation floated; the bar keeps the tabs of the
    // stack the conversation left, not the split one. Before the float, that
    // stack minimised in a row lies flat in the bar (Dock stands it on its side).
    const brakesTab = 'chat:' + A.brakes;
    out.split = { tabs: await ev(`barTabIds()`), moved: brakesTab };
    await ev(`PD.dock.dockEdge(${JSON.stringify(brakesTab)}, 'left'); 0`);
    await until(`!PD_ROOT.querySelector('.dk-head.bt-head [data-dk-tab=${JSON.stringify(brakesTab)}]')`, 'split');
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(500);
    out.split.before = await ev(BAR);
    await ev(`PD.dock.toggleMin('po-chat'); 0`);
    await sleep(600);
    out.split.minRow = await ev(`(() => { const h = document.querySelector('.dk-stack.dk-min > .dk-head.bt-head'); return h && {
      row: h.parentElement.parentElement.classList.contains('dk-row'), mode: getComputedStyle(h).writingMode, h: Math.round(h.getBoundingClientRect().height) }; })()`);
    out.split.min = await ev(BAR);
    await ev(`PD.dock.toggleMin('po-chat'); 0`);
    await until(`!PD_ROOT.querySelector('.dk-stack.dk-min')`, 'split stack open');
    await ev(`PD.dock.float('po-chat'); 0`);
    await sleep(600);
    out.split.floated = await ev(BAR);
    out.split.floated.otherRows = await ev(OTHER_ROWS);
    out.split.floated.movedHead = await ev(`PD_ROOT.querySelector('[data-dk-tab=${JSON.stringify(brakesTab)}]').closest('.dk-head').classList.contains('bt-head')`);
    await ev(`PD.dock.dockBack('po-chat'); 0`);
    await until(`!PD_ROOT.querySelector('.dk-float [data-dk-tab="po-chat"]')`, 'docked back after the split');
    await ev(`PD.dock.close(${JSON.stringify(brakesTab)}); 0`);
    await until(`!PD.rt.has(${JSON.stringify(brakesTab)})`, 'split tab closed');
    await ev(`pdChatOpen(${JSON.stringify(A.brakes)}); 0`);
    await until(`!!PD_ROOT.querySelector('.dk-head.bt-head [data-dk-tab=${JSON.stringify(brakesTab)}]')`, 'Brakes back in the bar');
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(500);
    out.split.after = await ev(BAR);
    // 9. Many tabs: "+N".
    await size(1100, 900);
    for (const r of A.many) await ev(`pdChatOpen(${JSON.stringify(r)}); 0`);
    await until(`${JSON.stringify(A.many)}.every(r => PD.rt.has('chat:'+r))`, 'many tabs');
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(800);
    out.many = await ev(BAR);
    await snap('bar-many');
    // Minimised (review 4): the "+N ▾" stays, and a tab picked in its list
    // opens the stack again with that tab in front.
    await ev(`PD.dock.toggleMin('po-chat'); 0`);
    await sleep(700);
    out.manyMin = await ev(BAR);
    out.manyMin.min = await ev(`!!PD_ROOT.querySelector('.dk-stack.dk-min > .dk-head.bt-head')`);
    await snap('bar-many-min');
    await clickAt(null, '.dk-head.bt-head > [data-dk-tabs]');
    await until(`document.querySelectorAll('.dk-tablist [data-dk-pick]').length > 1`, 'the tab list');
    out.manyMin.list = await ev(`[...document.querySelectorAll('.dk-tablist [data-dk-pick]')].map(b => b.dataset.dkPick)`);
    const lastMany = 'chat:' + A.many[A.many.length - 1];
    out.manyMin.offView = await ev(`(() => { const t = document.querySelector('.dk-head.bt-head [data-dk-tab=${JSON.stringify(lastMany)}]'), r = t.getBoundingClientRect(),
      q = t.closest('.dk-tabs').getBoundingClientRect(); return r.right > q.right + 1 || r.left < q.left - 1; })()`);
    await clickAt(null, `.dk-tablist [data-dk-pick=${JSON.stringify(lastMany)}]`);
    await until(`!PD_ROOT.querySelector('.dk-stack.dk-min') && PD.dock.frontOf('po-chat')===${JSON.stringify(lastMany)}`, 'a tab picked opens the minimised stack');
    await sleep(400);
    out.manyMin.picked = lastMany;
    out.manyMin.after = await ev(BAR);
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(400);
    for (const r of A.many) await ev(`PD.dock.close('chat:'+${JSON.stringify(r)}); 0`);
    await size(1728, 1117);
    await sleep(800);
    out.few = await ev(BAR);
    // 10. A task in no project, from project ▾: the top level.
    await clickAt(null, '#proj-switch .caret');
    try { await until(`!!document.querySelector('#proj-menu [data-pm-task]')`, 'tasks in no project', 40); }
    catch (e) { throw Error(e.message + JSON.stringify(await ev(`(()=>{const un=(PROJECTS.projects||[]).find(p=>p.id===UNASSIGNED_ID);
      const row=ALL_ROWS.find(r=>r.roomId===${JSON.stringify(A.loose)});
      return {un:un?(un.sessions||[]).map(x=>x.roomId||x.sessionId):null, row:row&&{h:row.headless,wf:row.workflow,sid:row.sessionId,pid:row.projectId}, menu:document.getElementById('proj-menu').innerHTML.slice(-600)}})()`))); }
    out.free = await ev(`[...document.querySelectorAll('#proj-menu .pm-free [data-pm-task]')].map(b => [b.dataset.pmTask, b.textContent.trim()])`);
    // More than eight (review 1): "All N tasks…" shows the rest, the focus on the ninth.
    out.freeMore = await ev(`document.querySelector('#proj-menu [data-pm-free-all]')?.textContent || null`);
    await clickAt(null, '#proj-menu [data-pm-free-all]');
    await until(`document.querySelectorAll('#proj-menu .pm-free [data-pm-task]').length > 8`, 'all tasks in no project');
    out.freeAll = await ev(`({n: document.querySelectorAll('#proj-menu .pm-free [data-pm-task]').length, more: !!document.querySelector('#proj-menu [data-pm-free-all]'),
      open: !document.getElementById('proj-menu').hidden, focus: [...document.querySelectorAll('#proj-menu .pm-free [data-pm-task]')].indexOf(document.activeElement)})`);
    await clickAt(null, `#proj-menu [data-pm-task="${await ev(sid(A.loose))}"]`);
    await until(`!!barTopTask() && barTopTask().roomId===${JSON.stringify(A.loose)}`, 'top level task');
    await sleep(600);
    out.top = await ev(BAR);
    out.top.ico = await ev(`!!document.querySelector('#proj-switch-ico .bt-hash')`);
    await snap('bar-top-task');
    await clickAt(null, '#proj-switch .caret');
    await until(`!!document.querySelector('#proj-menu [data-pm-task]')`, 'menu again');
    out.free2 = await ev(`[...document.querySelectorAll('#proj-menu .pm-free [data-pm-task].on')].map(b => b.dataset.pmTask)`);
    await ev(`closeProjMenu(); 0`);
    // 11. A phone: no tabs in the bar, its trail.
    await ev(`openProjectPage(${JSON.stringify(A.proj)}); 0`);
    await until(`PD.pid===${JSON.stringify(A.proj)} && !SELECTED_SID`, 'Motors for the phone');
    await size(390, 844); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow()`, 'phone');
    await ev(`openDetail(${sid(A.brakes)}); 0`);
    await until(`!!SELECTED_SID && !!document.querySelector('#bar-crumbs [data-crumb="task"]')`, 'phone task');
    await sleep(600);
    out.phone = await ev(BAR);
    out.phone.grpDisplay = await ev(`getComputedStyle(document.getElementById('bar-grp')).display`);
    out.phone.thrShown = await ev(`getComputedStyle(document.getElementById('thr-switch')).display`);
    await snap('bar-phone');
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class BarTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-bar-tabs-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        cls.root = base / 'EnsembleProjects'; cls.root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        cls.patches = [
            mock.patch.object(dashboard, 'PROJECTS_ROOT', cls.root),
            mock.patch.object(dashboard, 'DASHBOARD_DIR', state),
            mock.patch.object(dashboard, 'PROJECTS_FILE', state / 'projects.json'),
            mock.patch.object(dashboard, 'SESSION_PROJECTS_FILE', state / 'session_projects.json'),
            mock.patch.object(dashboard, 'SETTINGS_FILE', state / 'settings.json'),
            mock.patch.object(dashboard, 'LABELS_FILE', state / 'labels.json'),
            mock.patch.object(dashboard, 'PROJ_DIR', base / 'transcripts'),
            mock.patch.object(dashboard, 'CS_ROOT', base / 'cs'),
            mock.patch.object(chatroom, 'ROOMS_DIR', base / 'rooms'),
            mock.patch.object(dashboard, 'load_live', lambda: []),
            mock.patch.object(dashboard, '_read_agent_session_files', lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        sid = 'bar-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']

        loose_home = base / 'loose'; loose_home.mkdir()

        def task(title, pid=None, n=True):
            cwd = str(home if pid else loose_home)
            t = chatroom.create_room(title, [{'identity':'claude','agent':'claude','cwd':cwd}])
            chatroom.post_message(t['id'], 'user', f'About {title}')
            full = chatroom.get_room(t['id'], public=False)
            full.update(mode='solo', launched=True, workflow='inprogress', cwd=cwd)
            chatroom.update_room(full)
            if pid:
                dashboard.assign_session_project(t['id'], pid)
                if n: dashboard.assign_task_number(t['id'], pid)
            return t['id']
        cls.brakes = task('Brakes', cls.proj)
        cls.wipers = task('Wipers', cls.proj)
        # Wipers waits on the person (its folder is gone): a waiting Inbox card whose link opens the room.
        gone = home / 'wipers_task' / 'repo'
        chatroom.patch_room(cls.wipers, cwd=str(gone), folderMissing={
            'path': str(gone), 'identity': 'claude', 'kind': 'missing', 'since': time.time() - 120,
            'reason': f"claude's folder {gone} is missing", 'roomCwd': str(gone), 'seatCwd': str(home)})
        cls.many = [task(f'Lamp {i}', cls.proj) for i in range(4)]
        # Nine in no project before it, older: project ▾ shows eight, then "All 10 tasks…".
        cls.older = []
        for i in range(9):
            cls.older.append(task(f'Old loose {i}')); time.sleep(0.02)
        time.sleep(1.1)
        cls.loose = task('Loose ends')
        ok, proj2, _ = dashboard.register_project('Garden'); assert ok, proj2
        cls.proj2 = proj2['id']
        po2 = chatroom.create_room('Garden PO', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':'bar-po2',
                                                   'cwd':str(Path(proj2.get('home') or proj2['path']))}])
        chatroom.post_message(po2['id'], 'user', 'Another PO')
        dashboard.assign_session_project(po2['id'], cls.proj2)
        dashboard.set_project_po(cls.proj2, po2['id']); cls.po2 = po2['id']
        transcript = base / 'transcripts' / 'C--bar'; transcript.mkdir()
        at = time.time() - 3600

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','The first topic.',1), turn('assistant','The first reply.',2),
                 turn('user','The second topic.',3), turn('assistant','The second reply.',4)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        # Each topic sent as a new thread (#367: only the person starts one).
        for text, offset in (('The first topic.', 1), ('The second topic.', 3)):
            threads.note_send(cls.po, '', '', [], False, text, now=at + offset, new=True)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj, 'po':cls.po, 'proj2':cls.proj2, 'po2':cls.po2,
                'brakes':cls.brakes, 'wipers':cls.wipers, 'many':cls.many, 'loose':cls.loose,
                'shots':os.environ.get('ENSEMBLE_SHOTS', '')}
        script = base / 'bar_tabs_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        cls.loose_sid = cls.loose

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def one_row(self, g, what):
        self.assertTrue(g['on'], f'{what}: body.bar-tabs')
        self.assertLessEqual(g['header'], 50, f'{what}: the bar is one row')
        self.assertTrue(g['inBar'] and g['afterGrp'], f'{what}: the tabs are in the bar, after the group {g}')
        self.assertFalse(g['poTab'], f'{what}: no PO tab')
        self.assertEqual(g['trail'], [], f'{what}: no task › tool › file trail on a computer')
        self.assertLessEqual(g['scrollW'], g['vw'], f'{what}: no sideways scroll')

    def test_the_bar_is_one_row_with_the_project_in_front(self):
        g = self.got['start']
        self.one_row(g, 'start')
        self.assertEqual((g['proj'], g['thr'], g['tabs'], g['front']), ('Motors', 'Main', [], 'po-chat'))
        self.assertTrue(g['grpOn'], 'the project group is the tab in front')
        self.assertIn('inset', g['grpShadow'], 'underlined')
        self.assertEqual(self.got['errors'], [], 'the page threw nothing')

    def test_a_task_from_the_board_or_the_inbox_is_a_new_tab(self):
        b, i, back = self.got['board'], self.got['inbox'], self.got['grpClick']
        brakes, wipers = 'chat:' + self.brakes, 'chat:' + self.wipers
        self.one_row(b, 'board')
        self.assertEqual((b['tabs'], b['front']), ([brakes], brakes))
        self.assertFalse(b['grpOn'], 'the group is not underlined while a task is in front')
        self.assertEqual(b['target'], {'kind': 'task', 'room': self.brakes})
        self.assertEqual((back['front'], back['grpOn']), ('po-chat', True), 'a press on the group brings the conversation back')
        self.assertEqual(back['target'], {'kind': 'po', 'room': self.po})
        self.assertEqual((i['tabs'], i['front']), ([brakes, wipers], wipers))
        self.one_row(i, 'inbox')

    def test_thread_menu_shows_a_thread_in_place_and_main_goes_back(self):
        ta, tb = self.got['ta'], self.got['tb']
        self.assertEqual(self.got['menu'], [['', 'Main conversation', True], [tb, 'The second topic.', False],
                                            [ta, 'The first topic.', False]], 'Main, then the threads, latest first')
        g = self.got['inPlace']
        self.one_row(g, 'in place')
        self.assertEqual((g['thr'], g['front'], g['grpOn']), ('The first topic.', 'po-chat', True))
        self.assertEqual(g['thrIn'], {'tid': ta, 'mid': '', 'shown': True, 'hidden': False})
        self.assertEqual(g['threadTabs'], [], 'in place, not a tab')
        self.assertEqual(g['cover']['panels'], [], 'no dock panel for it')
        self.assertEqual((g['cover']['w'], g['cover']['h']), (g['cover']['pw'], g['cover']['ph']), 'it covers the conversation')
        self.assertEqual([x[0] for x in g['menu'] if x[2]], [ta], 'the menu marks the thread shown')
        m = self.got['main']
        self.assertIsNone(m['thrIn'])
        self.assertEqual(m['thr'], 'Main')
        self.assertIsNone(m['side'], "the chat's own thread is closed too")
        self.assertEqual(m['target'], {'kind': 'po', 'room': self.po})

    def test_the_send_box_aims_at_the_thread_in_place(self):
        ta = self.got['ta']
        want = {'kind': 'thread', 'room': self.po, 'thread': ta, 'mid': ''}
        for key in ('inPlace', 'plainLine'):
            with self.subTest(key):
                self.assertEqual(self.got[key]['target'], want)
                self.assertEqual(self.got[key]['route'], {'room': self.po, 'thread': ta})
        box = self.got['box']
        self.assertEqual(box['taskTab'], {'kind': 'task', 'room': self.brakes}, "a task's tab pressed takes the box")
        self.assertEqual(box['grp'], want, "the project's tab pressed: the thread in place")
        self.assertEqual(box['grpRoute'], {'room': self.po, 'thread': ta})
        self.assertEqual(box['closed'], want, 'a task closed: back to the thread in place')
        x = box['x']
        self.assertEqual((x['target'], x['front']), ({'kind': 'task', 'room': self.brakes}, 'chat:' + self.brakes),
                         "a task's tab closed by its ×: the tab now in front has the box")

    def test_a_ctrl_click_opens_a_thread_tab(self):
        ta, tb = self.got['ta'], self.got['tb']
        g = self.got['menuTab']
        tab_b = f'thread:{self.po}:{tb}'
        self.assertEqual((g['id'], g['find']), (tab_b, tab_b))
        self.assertEqual(g['threadTabs'], [tab_b])
        self.assertEqual((g['front'], g['tabs'][-1]), (tab_b, tab_b), 'a tab after the last one, in front')
        self.assertIsNone(g['thrIn'], 'the conversation stays on Main')
        self.assertEqual(g['thr'], 'Main')
        self.assertEqual(g['target'], {'kind': 'thread', 'room': self.po, 'thread': tb, 'mid': ''})
        self.assertEqual(g['saved'], [{'room': self.po, 'tid': tb, 'mid': '', 'title': 'The second topic.'}], 'kept for a reload')
        c = self.got['ctrlLine']
        tab_a = f'thread:{self.po}:{ta}'
        self.assertEqual(c['threadTabs'], [tab_b, tab_a], "a trusted Ctrl-click on a reply line: a tab")
        self.assertEqual(c['front'], tab_a)
        self.assertIsNone(c['thrIn'])
        self.assertIsNone(c['side'], 'the chat itself shows no thread')
        p = self.got['plainLine']
        self.assertEqual(p['threadTabs'], [tab_b], 'its × closed the tab')
        self.assertEqual(p['thrIn'], {'tid': ta, 'mid': '', 'shown': True, 'hidden': False}, 'a plain click: in place')
        self.assertEqual((p['front'], p['thr']), ('po-chat', 'The first topic.'))

    def test_a_project_switch_brings_back_its_thread_and_tabs(self):
        ta, tb = self.got['ta'], self.got['tb']
        brakes, wipers, tab_b = 'chat:' + self.brakes, 'chat:' + self.wipers, f'thread:{self.po}:{tb}'
        self.assertTrue(self.got['pinned'])
        g = self.got['garden']
        self.one_row(g, 'garden')
        self.assertEqual((g['proj'], g['thr'], g['thrIn'], g['threadTabs']), ('Garden', 'Main', None, []))
        self.assertEqual(g['panels'], [brakes], "the pinned tab stays; the others are Motors' and go away")
        self.assertEqual(g['tabs'], [brakes])
        b = self.got['back']
        self.one_row(b, 'back')
        self.assertEqual(b['thrIn'], {'tid': ta, 'mid': '', 'shown': True, 'hidden': False}, 'the last thread in place')
        self.assertEqual(sorted(b['panels']), sorted([brakes, wipers, tab_b]))
        self.assertEqual(b['thr'], 'The first topic.')
        self.assertEqual(b['target'], {'kind': 'thread', 'room': self.po, 'thread': ta, 'mid': ''}, 'the box follows it')
        r = self.got['reload']
        self.assertEqual(r['threadTabs'], [tab_b], 'thread tabs come back after a reload')
        self.assertEqual(r['thrIn'], {'tid': ta, 'mid': '', 'shown': True, 'hidden': False})

    def test_many_tabs_say_how_many_are_out_of_view(self):
        g, f = self.got['many'], self.got['few']
        self.one_row(g, 'many')
        want = ['chat:' + self.brakes, f"thread:{self.po}:{self.got['tb']}"] + ['chat:' + r for r in self.many]
        self.assertEqual(sorted(g['tabs']), sorted(want), 'every tab is in the row, some out of view')
        self.assertTrue(g['more'] and int(g['more']) >= 1, g)
        self.assertIsNone(f['more'], 'with room for every tab: no +N')

    def test_many_tabs_minimised_keep_the_more_list(self):
        g = self.got['manyMin']
        self.one_row(g, 'many, minimised')
        self.assertTrue(g['min'], "the bar's title bar is the minimised stack's")
        self.assertTrue(g['more'] and int(g['more']) >= 1, f'minimised: still "+N ▾" {g}')
        self.assertTrue(g['offView'], 'the tab picked is out of view in the row')
        self.assertIn(g['picked'], g['list'], "the ▾ lists the stack's every tab")
        a = g['after']
        self.one_row(a, 'after the pick')
        self.assertEqual(a['front'], g['picked'], 'the tab picked is in front, the stack open again')

    def test_split_then_float_keeps_the_stack_it_left(self):
        g = self.got['split']
        moved = g['moved']
        self.assertIn(moved, g['tabs'], 'Brakes is in the bar before the split')
        rest = [t for t in g['tabs'] if t != moved]
        self.assertTrue(rest, 'another tab stays in the stack')
        self.assertEqual(g['before']['tabs'], rest, 'split: the bar keeps the conversation\'s stack')
        f = g['floated']
        self.one_row(f, 'split, floated')
        self.assertEqual(f['tabs'], rest, 'floated: the tabs of the stack it left, not the split one')
        self.assertFalse(f['movedHead'], "the split stack's title bar is not the bar's")
        self.assertEqual(f['otherRows'], [moved], 'the split stack keeps its own row, nothing else does')
        self.assertFalse(f['grpOn'])
        m = g['minRow']
        self.assertEqual((m['row'], m['mode']), (True, 'horizontal-tb'), 'minimised in a row: the title bar lies flat in the bar')
        self.assertLessEqual(m['h'], 50, 'and is no taller than the bar')
        self.one_row(g['min'], 'split, minimised')
        self.assertEqual(g['min']['tabs'], rest)
        a = g['after']
        self.one_row(a, 'after the split')
        self.assertEqual(sorted(a['tabs']), sorted(g['tabs']), 'docked back: every tab in the bar')

    def test_a_task_in_no_project_is_the_top_level(self):
        free = [x[0] for x in self.got['free']]
        self.assertEqual(len(free), 8, 'the latest eight')
        self.assertEqual(free[0], self.loose_sid, 'latest first')
        self.assertIn('Loose ends', self.got['free'][0][1])
        self.assertIn('All 10 tasks in no project', self.got['freeMore'] or '')
        self.assertEqual(self.got['freeAll'], {'n': 10, 'more': False, 'open': True, 'focus': 8}, 'the rest shown, the focus on the ninth')
        g = self.got['top']
        self.one_row(g, 'top')
        self.assertEqual(g['top'], self.loose)
        self.assertEqual(g['proj'], 'Loose ends', 'the task is the top level: its title in project ▾')
        self.assertTrue(g['ico'])
        self.assertEqual(g['thr'], 'Main')
        self.assertFalse(g['thrIn']['shown'], "the PO's thread in place is not over another chat")
        self.assertEqual(g['target'], {'kind': 'task', 'room': self.loose})
        self.assertEqual(self.got['free2'], [self.loose_sid], 'project ▾ marks it')

    def test_the_keys_from_the_group_aim_the_box(self):
        k = self.got['keys']
        self.assertTrue(k['first'].startswith('chat:'), k)
        self.assertEqual(k['front'], k['first'], 'ArrowRight from project ▾: the first tab in front')
        self.assertEqual(k['focus'], k['first'], 'and focused')
        self.assertEqual(k['target'], {'kind': 'task', 'room': k['first'][5:]}, 'the box goes with it, not left on the thread')
        self.assertEqual(k['route'], {'room': k['first'][5:], 'thread': ''})

    def test_the_conversation_out_of_the_dock_keeps_the_tabs_in_the_bar(self):
        tabs = self.got['beforeOut']['tabs']
        self.assertTrue(tabs, 'a task tab is open before the conversation leaves the dock')
        for key in ('floated', 'minimised'):
            with self.subTest(key=key):
                g = self.got[key]
                self.one_row(g, key)
                self.assertEqual(g['tabs'], tabs, f'{key}: the same tabs, in the bar')
                self.assertEqual(g['otherRows'], [], f"{key}: no second row of tabs (Dock's own)")
                self.assertFalse(g['grpOn'], f'{key}: the group is not underlined: the conversation is not shown in place')
        self.assertTrue(self.got['floated']['poFloat'], 'the conversation is in its float')
        self.assertTrue(self.got['minimised']['min'], "the bar's title bar is the minimised stack's")
        self.assertEqual(self.got['minimised']['underlined'], [], 'minimised: no tab underlined')
        for key in ('dockedBack', 'unminimised'):
            with self.subTest(key=key):
                self.one_row(self.got[key], key)
        u = self.got['unminimised']
        self.assertEqual(u['front'], tabs[-1], 'the tab pressed in the bar is in front, the stack open again')
        self.assertTrue(u['tabs'][-1] == tabs[-1] and not u['grpOn'])

    def test_a_phone_keeps_its_own_bar(self):
        g = self.got['phone']
        self.assertFalse(g['on'], 'no body.bar-tabs on a phone')
        self.assertEqual(g['tabs'], [])
        self.assertEqual(g['trail'], ['task'], "the phone's trail is there")
        self.assertEqual(g['thrShown'], 'none', 'no thread ▾ on a phone')
        self.assertEqual(g['grpDisplay'], 'contents')


if __name__ == '__main__':
    unittest.main()
