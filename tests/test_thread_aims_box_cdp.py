"""Opening a thread aims the message box at it, without a click into its panel (#363)."""
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
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0',
    '--user-data-dir=' + chromeProfile(A), '--no-first-run', '--disable-gpu', '--disable-popup-blocking', 'about:blank'],
    {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  const size=(w,h) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:w<720},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<120;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);} throw Error('timeout '+label); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.rt.get('thread:po')?.el.querySelector('iframe')`;
  const snap = async name => { if (!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},s);
    require('fs').writeFileSync(A.shots+'/'+name+'.png',Buffer.from(r.data,'base64')); };
  const count = `(() => { const main=${PO}, thread=${THR}, m=main?.contentDocument, t=thread?.contentDocument;
    const shown=d=>!!d && getComputedStyle(d.getElementById('compose')).display!=='none';
    return {main:shown(m),thread:shown(t),reply:!!t&&!t.getElementById('thread-reply').hidden,
      chip:!!m&&!m.getElementById('compose-target').hidden,
      mainDraft:m?.getElementById('input').value||'',threadDraft:t?.getElementById('input').value||''}; })()`;
  const workspace = `(()=>{const b=document.getElementById('workspace-compose'),f=document.getElementById('workspace-compose-frame');
    return {shown:!!b&&!b.hidden, target:document.getElementById('workspace-compose-link')?.textContent||'',
      editor:!!f?.contentDocument?.getElementById('compose')&&getComputedStyle(f.contentDocument.getElementById('compose')).display!=='none',
      width:b?.getBoundingClientRect().width||0,host:document.getElementById('po-dock-host')?.getBoundingClientRect().width||0}})()`;
  const W = `document.getElementById('workspace-compose-frame')`;
  const out={};
  // A real press and release at an element inside a frame of the page.
  const clickAt = async (frameExpr, selector) => {
    const p = await ev(`(()=>{const f=${frameExpr},r=f.getBoundingClientRect(),e=f.contentDocument.querySelector(${JSON.stringify(selector)});
      e.scrollIntoView({block:'center'});const q=e.getBoundingClientRect();return {x:r.x+q.x+q.width/2,y:r.y+q.y+q.height/2}})()`);
    for (const type of ['mouseMoved','mousePressed','mouseReleased'])
      await send('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1},s);
  };
  const boxRoute = async () => ev(`(async()=>{const w=${W}.contentWindow,old=w.postOk;let body;
    w.postOk=async(_path,b)=>{body=b;return {}};try{await w.sendResuming('probe','', 'probe-key',[]);return {room:body.roomId,thread:body.thread||''};}
    finally{w.postOk=old}})()`);
  const boxReady = `${W}?.contentDocument?.getElementById('send')?.disabled===false`;
  const typeBox = text => ev(`(()=>{const f=${W},i=f.contentDocument.getElementById('input');i.value=${JSON.stringify(text)};
    i.dispatchEvent(new Event('input',{bubbles:true}));f.contentWindow.edStoreNow();return 0})()`);
  const boxDraft = `${W}.contentDocument.getElementById('input').value`;
  const boxFocused = `document.activeElement===${W} && ${W}.contentDocument.activeElement===${W}.contentDocument.getElementById('input')`;
  const threads = `[...${PO}.contentDocument.querySelectorAll('#msgs .thr-open[data-thr]')].map(b=>b.dataset.thr)`;
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`${threads}.length===2`,'PO chat with two threads');
    await until(`document.getElementById('workspace-compose')?.hidden===false && WC.target?.kind==='po' && ${boxReady}`,'workspace box on the PO');
    const [ta, tb] = await ev(threads);
    out.ta = ta; out.tb = tb;
    out.roleLabels = await ev(`(() => {
      const frame=${PO}, head=document.querySelector('#po-panel .po-head'), line=frame.contentDocument.querySelector('#msgs .thr-line');
      line.scrollIntoView({block:'center'});
      const tag=head?.querySelector('.rl.po');
      const labels=[...(line?.querySelectorAll('.thr-who .rl')||[])];
      const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
      const hr=rect(head), lr=rect(line), fr=rect(frame);
      return {head:{label:tag?.textContent||'', title:tag?.title||'', circles:head?.querySelectorAll('.av').length||0,
          name:head?.querySelector('.po-name')?.textContent||''},
        replies:{labels:labels.map(e=>({text:e.textContent,cls:e.className,width:e.getBoundingClientRect().width,
          height:e.getBoundingClientRect().height})), circles:line?.querySelectorAll('.thr-av').length||0},
        rects:{head:hr, replies:{x:fr.x+lr.x,y:fr.y+lr.y,width:lr.width,height:lr.height}}};
    })()`);
    if (A.shots) {
      await snap('role-labels');
      require('fs').writeFileSync(A.shots+'/role-labels.json',JSON.stringify(out.roleLabels));
    }
    await typeBox('PO draft kept');
    // 1. A click on a thread's link: the box is that thread's at once, caret in it.
    await clickAt(PO, `#msgs .thr-open[data-thr="${ta}"]`);
    await until(`!!${THR}`,'thread panel');
    out.first = await ev(`({target:WC.target, link:document.getElementById('workspace-compose-link').textContent, title:PD.rt.get('thread:po').title})`);
    await until(`WC.target?.thread===${JSON.stringify(ta)} && ${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(ta)} && ${boxReady}`,'box on thread A');
    await sleep(300);
    out.firstStill = await ev(`WC.target`);
    out.firstFocus = await ev(boxFocused);
    out.firstRoute = await boxRoute();
    out.firstDraft = await ev(boxDraft);
    await typeBox('Thread A draft');
    // 2. Another thread, opened without a click (no caret move): the box follows.
    await ev(`(()=>{const i=document.createElement('input');i.id='probe-field';document.body.appendChild(i);i.focus();return 0})()`);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr="${tb}"]').click();0`);
    out.second = await ev(`WC.target`);
    await until(`${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(tb)} && ${boxReady}`,'box on thread B');
    out.secondRoute = await boxRoute();
    out.secondDraft = await ev(boxDraft);
    out.secondFocus = await ev(`document.activeElement.id`);
    // 2b. A click with the caret in a field: the box follows, the caret stays.
    await ev(`document.getElementById('probe-field').focus();0`);
    await send('Runtime.evaluate',{expression:`${PO}.contentWindow.openThread(${JSON.stringify(ta)},'');0`,userGesture:true,returnByValue:true},s);
    out.typing = await ev(`({target:WC.target?.thread, active:document.activeElement.id})`);
    try { await until(`${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(ta)} && ${boxReady}`,'box back on thread A'); }
    catch (e) { throw Error(e.message+' '+JSON.stringify(await ev(`({t:WC.target,box:${W}.contentWindow.liveTailBoxThread?.(),send:${W}.contentDocument.getElementById('send')?.disabled,url:${W}.contentDocument.URL,e:PD.rt.get('thread:po')&&{tid:PD.rt.get('thread:po').tid,mid:PD.rt.get('thread:po').mid},typing:${JSON.stringify(out.typing)}})`))); }
    out.backDraft = await ev(boxDraft);
    await ev(`document.getElementById('probe-field').remove();0`);
    // 3. Back to the PO by a click in its chat: its draft is there.
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='po' && !${W}.contentWindow.liveTailBoxThread?.() && ${boxReady}`,'box on the PO');
    out.poDraft = await ev(boxDraft);
    // 4. Reply catch-up opening a thread is not the person's: the box stays.
    await ev(`${PO}.contentWindow.sideOpen({tid:${JSON.stringify(tb)},auto:true});0`);
    await until(`PD.rt.get('thread:po')?.tid===${JSON.stringify(tb)}`,'auto thread');
    await sleep(500);
    out.auto = await ev(`WC.target`);
    // 5. A reload with the thread kept: the remembered target (the PO) stays.
    await send('Page.reload',{},s);
    await until(`typeof PD!=='undefined' && !!${THR}?.contentDocument?.getElementById('thread-reply') && ${boxReady}`,'reload');
    await sleep(1500);
    out.reloadPo = await ev(`({target:WC.target, thread:PD.rt.get('thread:po')?.tid})`);
    // ... and a remembered thread target stays the thread.
    await ev(`${THR}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='thread'`,'thread picked');
    await send('Page.reload',{},s);
    await until(`typeof PD!=='undefined' && !!${THR}?.contentDocument?.getElementById('thread-reply') && ${boxReady}`,'reload 2');
    await sleep(1500);
    out.reloadThread = await ev(`WC.target`);
    // 6. Another PO's chat shown and this one again: its thread comes back quietly.
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='po'`,'PO before follow');
    await ev(`PD.thrRoom='other-room';pdThreadFollow(${JSON.stringify(A.po)},${PO});0`);
    await sleep(1200);
    out.follow = await ev(`({target:WC.target, thread:PD.rt.get('thread:po')?.tid||''})`);
    // 7. Closing the thread gives the box back to the PO.
    await ev(`${THR}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='thread'`,'thread before close');
    await ev(`PD.dock.removePanel('thread:po');0`);
    out.closed = await ev(`WC.target`);
    // 8. A thread started on a balloon (no id yet): the box takes it by its
    // balloon, then by the id its page finds.
    out.balloon = await ev(`(()=>{pdThreadPanel(${PO},{room:${JSON.stringify(A.po)},tid:'',mid:${JSON.stringify(A.balloon)},title:'Thread'});return WC.target})()`);
    await until(`!!PD.rt.get('thread:po')?.tid && WC.target?.thread===PD.rt.get('thread:po').tid`,'promoted target');
    await until(`${W}.contentWindow.liveTailBoxThread?.()===PD.rt.get('thread:po').tid && ${boxReady}`,'promoted editor');
    out.promoted = await ev(`({target:WC.target, tid:PD.rt.get('thread:po').tid})`);
    out.promotedRoute = await boxRoute();
    // The PO chat is the workspace's guest: the workspace's editor moves the
    // draft; the chat's promotion moves its target only.
    out.guest = await ev(`${PO}.contentWindow.eval(\`(()=>{const k='cd-draft:'+ROOM+':thread:', c=sessionStorage.getItem(SHARED_CHOICE_KEY), was=SHARED_THREAD;
      sessionStorage.setItem(k+'m:zz','{"head":"stale"}');SHARED_THREAD={thread:'m:zz',title:'T',info:null};sharedThreadPromote('zz','Tzz');
      const r={guest:document.body.classList.contains('workspace-guest'),thread:SHARED_THREAD.thread,old:sessionStorage.getItem(k+'m:zz'),
        moved:sessionStorage.getItem(k+'Tzz'),alias:sessionStorage.getItem('cd-thread-alias:'+ROOM+':zz')};
      SHARED_THREAD=was;sessionStorage.removeItem(k+'m:zz');if(c===null)sessionStorage.removeItem(SHARED_CHOICE_KEY);else sessionStorage.setItem(SHARED_CHOICE_KEY,c);sharedTargetPaint();return r})()\`)`);
    // 10. P340: the way back to the PO while the box is on a thread.
    const toLine = `(()=>{const b=document.getElementById('workspace-compose-back');${W}.contentWindow.edStoreNow();
      return {to:document.getElementById('workspace-compose-link').textContent, back:!!b&&!b.hidden, kind:WC.target?.kind||'',
        placeholder:${W}.contentDocument.getElementById('input').placeholder, box:${W}.contentDocument.getElementById('input').value,
        draft:JSON.parse(sessionStorage.getItem('cd-draft:'+${JSON.stringify(A.po)}+':thread:'+${JSON.stringify(ta)})||'{}').head||''}})()`;
    const onPo = `WC.target?.kind==='po' && !${W}.contentWindow.liveTailBoxThread?.() && ${boxReady}`;
    const openA = async (label, wait = 800) => {
      await clickAt(PO, `#msgs .thr-open[data-thr="${ta}"]`);
      await until(`WC.target?.thread===${JSON.stringify(ta)} && ${W}.contentWindow.liveTailBoxThread?.()===${JSON.stringify(ta)} && ${boxReady}`,'P340 thread '+label);
      await sleep(wait);
    };
    const clickTop = async selector => {
      const p = await ev(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});const q=e.getBoundingClientRect();return {x:q.x+q.width/2,y:q.y+q.height/2}})()`);
      for (const type of ['mouseMoved','mousePressed','mouseReleased'])
        await send('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1},s);
    };
    out.p340 = {};
    // Step 9 reads thread A's draft back: it is put back after this step.
    const aDraftKey = JSON.stringify('cd-draft:'+A.po+':thread:'+ta);
    const aDraft = await ev(`sessionStorage.getItem(${aDraftKey})`);
    await openA('beside');
    out.p340.thread = await ev(toLine);
    await typeBox('P340 thread draft');
    await snap('p340-thread');
    // (a) The PO chat's tab, the thread beside it.
    await clickTop('.dk-tab[data-dk-tab="po-chat"]');
    await until(onPo,'P340 tab beside');
    out.p340.tabBeside = await ev(toLine);
    await snap('p340-po');
    // (a') The PO chat's tab, the thread a tab of its stack in front of it.
    await ev(`PD.dock.removePanel('thread:po');PD.dock.forget('thread:po');window.__fits=pdFits;pdFits=()=>false;0`);
    await openA('stacked');
    out.p340.stacked = await ev(`PD.dock.frontOf('po-chat')==='thread:po' && !PD.dock.isShown('po-chat')`);
    await ev(`pdFits=window.__fits;0`);
    await clickTop('.dk-tab[data-dk-tab="po-chat"]');
    await until(onPo,'P340 tab stacked');
    out.p340.tabStacked = await ev(toLine);
    // (a'') The PO chat's tab clicked at once after the thread opened: a click of its own.
    await openA('quick', 0);
    out.p340.quickAt = await ev(`Date.now()-WC.aimAt`);
    await clickTop('.dk-tab[data-dk-tab="po-chat"]');
    await sleep(100);
    out.p340.tabQuick = await ev(toLine);
    await until(onPo,'P340 tab quick');
    // (a''') A key on the tabs at once after the thread opened, stacked in
    // front of the PO chat: Dock brings the PO chat forward, the box follows.
    await ev(`PD.dock.removePanel('thread:po');PD.dock.forget('thread:po');pdFits=()=>false;0`);
    await openA('key', 0);
    await ev(`pdFits=window.__fits;0`);
    const key = await ev(`(()=>{const t=document.querySelector('.dk-tab[data-dk-tab="thread:po"]');t.focus();
      const l=[...t.closest('.dk-tabs').querySelectorAll('[data-dk-tab]')].map(x=>x.dataset.dkTab);
      return l.indexOf('po-chat')===0?'Home':'End'})()`);
    out.p340.keyAt = await ev(`Date.now()-WC.aimAt`);
    for (const type of ['keyDown','keyUp'])
      await send('Input.dispatchKeyEvent',{type,key,code:key,windowsVirtualKeyCode:key==='Home'?36:35},s);
    await sleep(100);
    out.p340.keyQuick = {...await ev(toLine), front: await ev(`PD.dock.isShown('po-chat')`)};
    await until(onPo,'P340 key quick');
    // (b) The To line's ×.
    await openA('x');
    await clickTop('#workspace-compose-back');
    await until(onPo,'P340 x');
    out.p340.x = {...await ev(toLine), focus: await ev(boxFocused)};
    // (c) The thread's tab closed by its own ×.
    await openA('close');
    await ev(`PD.dock.reveal('thread:po');0`);
    await clickTop('.dk-tab-x[data-dk-close="thread:po"]');
    await until(onPo,'P340 close');
    out.p340.close = {...await ev(toLine), panel: await ev(`PD.rt.has('thread:po')`)};
    // (d) A reload with the box on a thread whose panel is gone.
    await ev(`sessionStorage.setItem('cd-workspace-compose-target',JSON.stringify({kind:'thread',room:${JSON.stringify(A.po)},thread:${JSON.stringify(ta)},mid:''}));0`);
    await send('Page.reload',{},s);
    await until(`typeof WC!=='undefined' && typeof PD!=='undefined' && !!PD.dock`,'P340 reload');
    const t0 = Date.now();
    await until(onPo,'P340 reload on PO');
    out.p340.reloadMs = Date.now() - t0;
    out.p340.reload = {...await ev(toLine), panel: await ev(`PD.rt.has('thread:po')`)};
    // (e) A phone: the thread in the chat, its Back one tap from the PO.
    await size(390,844);
    await until(`PD.dock.narrow() && !!document.querySelector('iframe.po-session:not([hidden])')?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'P340 phone');
    const PH = `document.querySelector('iframe.po-session:not([hidden])')`;
    await clickAt(PH, `#msgs .thr-open[data-thr="${ta}"]`);
    // A phone's side thread is a layer over the chat with its box of its own
    // (never paired): the box shown is the side's, the chat's is under it.
    const TS = `${PH}.contentDocument.getElementById('ts-frame')`;
    await until(`!${PH}.contentDocument.getElementById('thr-side').hidden && ${TS}.contentDocument?.getElementById('send')?.disabled===false`,'P340 phone thread');
    await sleep(400);
    const routeIn = f => `(async()=>{const w=${f}.contentWindow,old=w.postOk;let body;w.postOk=async(_path,b)=>{body=b;return {}};
      try{await w.sendResuming('probe','', 'probe-key',[]);return body.thread||'';}finally{w.postOk=old}})()`;
    const phRoute = routeIn(PH);
    out.p340.phoneThread = {route: await ev(routeIn(TS)),
      side: await ev(`${TS}.contentDocument.getElementById('input').placeholder`)};
    await clickAt(PH, '#ts-back');
    await until(`${PH}.contentDocument.getElementById('thr-side').hidden`,'P340 phone back');
    out.p340.phoneBack = {route: await ev(phRoute), placeholder: await ev(`${PH}.contentDocument.getElementById('input').placeholder`)};
    await size(1728,1117);
    await ev(`sessionStorage.setItem(${aDraftKey},${JSON.stringify(aDraft)});0`);
    // 9. The chat in a window of its own: its box follows the thread it opens beside it.
    await send('Page.navigate',{url:A.base+'/session?id='+encodeURIComponent(A.po)},s);
    await until(`document.querySelectorAll('#msgs .thr-open[data-thr]').length===2 && document.getElementById('send')?.disabled===false`,'own window chat');
    const route = `(async()=>{const old=postOk;let body;postOk=async(_path,b)=>{body=b;return {}};
      try{await sendResuming('probe','', 'probe-key',[]);return body.thread||'';}finally{postOk=old}})()`;
    const paired = t => `document.getElementById('ts-frame').contentWindow?.sharedPairedNow?.() && document.getElementById('ts-frame').contentWindow.sharedThreadTarget().thread===${JSON.stringify(t)}`;
    const i = `document.getElementById('input')`;
    await ev(`(()=>{${i}.value='Own PO draft';${i}.dispatchEvent(new Event('input',{bubbles:true}));edStoreNow();return 0})()`);
    const p = await ev(`(()=>{const e=document.querySelector('#msgs .thr-open[data-thr="${ta}"]');e.scrollIntoView({block:'center'});const q=e.getBoundingClientRect();return {x:q.x+q.width/2,y:q.y+q.height/2}})()`);
    for (const type of ['mouseMoved','mousePressed','mouseReleased'])
      await send('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1},s);
    await until(paired(ta),'own window thread A paired');
    await sleep(400);
    out.ownA = {thread: await ev(route), draft: await ev(`${i}.value`), focus: await ev(`document.activeElement===${i}`)};
    await ev(`(()=>{${i}.value='Own thread A draft';${i}.dispatchEvent(new Event('input',{bubbles:true}));edStoreNow();return 0})()`);
    await ev(`document.querySelector('#msgs .thr-open[data-thr="${tb}"]').click();0`);
    await until(paired(tb),'own window thread B paired');
    await sleep(400);
    out.ownB = {thread: await ev(route), draft: await ev(`${i}.value`)};
    await ev(`document.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    out.ownPo = {thread: await ev(route), draft: await ev(`${i}.value`)};
    // The open thread's link again: the box goes back to it at once.
    await ev(`document.querySelector('#msgs .thr-open[data-thr="${tb}"]').click();0`);
    out.ownSame = await ev(route);
    // Reply catch-up: the box stays where it is.
    await ev(`document.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));sideOpen({tid:${JSON.stringify(ta)},auto:true});0`);
    await until(paired(ta),'own window auto paired');
    await sleep(400);
    out.ownAuto = await ev(route);
    // A thread started on a balloon, opened here: its id known already (the
    // alias the dashboard kept in this tab), then found only by its page.
    const tid = out.promoted.tid, side = `document.getElementById('ts-frame').contentWindow`;
    const balloonOpen = async () => {
      await ev(`sideClose();document.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
      await send('Runtime.evaluate',{expression:`openThread('',${JSON.stringify(A.balloon)});0`,userGesture:true,returnByValue:true},s);
    };
    out.ownAlias = await ev(`sessionStorage.getItem('cd-thread-alias:'+ROOM+':'+${JSON.stringify(A.balloon)})`);
    await balloonOpen();
    await until(paired(tid),'own window balloon (alias) paired');
    await sleep(400);
    out.ownBalloonKnown = {thread: await ev(route), aim: await ev(`SIDE_AIM`)};
    const idDraft = `JSON.parse(sessionStorage.getItem('cd-draft:'+ROOM+':thread:'+${JSON.stringify(tid)})||'{}').head||''`;
    const unalias = `sessionStorage.removeItem('cd-thread-alias:'+ROOM+':'+${JSON.stringify(A.balloon)});0`;
    const promoted = `${side}?.sharedPairedNow?.() && ${side}.sharedThreadTarget().thread===${JSON.stringify(tid)} && SHARED_THREAD?.thread===${JSON.stringify(tid)}`;
    // The id has a draft of its own, the alias is gone: the box shows that draft once the id is found.
    await ev(`(()=>{${i}.value='Existing ID draft';${i}.dispatchEvent(new Event('input',{bubbles:true}));edStoreNow();return 0})()`);
    await ev(unalias);
    await balloonOpen();
    await until(promoted,'own window balloon (id draft) promoted');
    await sleep(400);
    out.ownIdDraft = {thread: await ev(route), draft: await ev(`${i}.value`)};
    await ev(`edStoreNow();0`);
    out.ownIdDraft.stored = await ev(idDraft);
    await ev(`(()=>{${i}.value='';${i}.dispatchEvent(new Event('input',{bubbles:true}));edStoreNow();return 0})()`);
    await ev(unalias);
    await balloonOpen();
    await until(`${side}?.sharedPairedNow?.() && new URLSearchParams(${side}.location.search).get('tmid')===${JSON.stringify(A.balloon)}`,'own window balloon paired');
    await ev(`(()=>{${i}.value='Balloon draft';${i}.dispatchEvent(new Event('input',{bubbles:true}));edStoreNow();return 0})()`);
    await until(promoted,'own window balloon promoted');
    await sleep(400);
    out.ownBalloonLater = {thread: await ev(route), draft: await ev(`${i}.value`), aim: await ev(`SIDE_AIM`),
      stored: await ev(idDraft),
      old: await ev(`sessionStorage.getItem('cd-draft:'+ROOM+':thread:m:'+${JSON.stringify(A.balloon)})`)};
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadAimsBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-thread-aim-', ignore_cleanup_errors=True)
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
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        sid = 'shared-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        task = chatroom.create_room('Task chat', [{'identity':'claude','agent':'claude','sessionId':'shared-task','cwd':str(home)}])
        dashboard.assign_session_project(task['id'], cls.proj)
        dashboard.assign_task_number(task['id'], cls.proj)
        chatroom.post_message(task['id'], 'user', 'A task message')
        cls.task = task['id']
        transcript = base / 'transcripts' / 'C--shared'; transcript.mkdir()
        at = time.time() - 3600
        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','The first topic.',1), turn('assistant','The first reply.',2),
                 turn('user','The second topic.',3), turn('assistant','The second reply.',4),
                 turn('user','A new topic without replies.',5)]
        cls.balloon = f'{sid}:4'
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        cls.transcript_lock = threading.Lock()
        task_transcript = base / 'transcripts' / 'C--task'; task_transcript.mkdir()
        (task_transcript / 'shared-task.jsonl').write_text('', encoding='utf-8')
        def resume(h, room, text='', to='', key='', quiet=False, **kwargs):
            dashboard._typed_sends(room['id'], [{'key':key,'text':text}])
            sent_sid = sid if room['id'] == cls.po else 'shared-task'
            path = transcript / f'{sid}.jsonl' if room['id'] == cls.po else task_transcript / 'shared-task.jsonl'
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + '.000Z'
            item = {'type':'user','timestamp':stamp,'sessionId':sent_sid,'message':{'role':'user','content':text}}
            with cls.transcript_lock, path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(item) + '\n')
            return {'delivered':1}
        patch = mock.patch.object(dashboard.Handler, '_resume_room', resume)
        patch.start(); cls.addClassCleanup(patch.stop)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj,'po':cls.po,'task':cls.task,'balloon':cls.balloon,
                'shots':os.environ.get('ENSEMBLE_SHOTS', '')}
        script = base / 'thread_aim_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=200)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_opening_a_thread_aims_the_box(self):
        g, ta, tb = self.got, self.got['ta'], self.got['tb']
        self.assertNotEqual(ta, tb)
        self.assertEqual(g['first']['target'], {'kind':'thread','room':self.po,'thread':ta,'mid':''})
        self.assertEqual(g['first']['link'], g['first']['title'])
        self.assertEqual(g['firstStill']['thread'], ta, 'the press on the PO chat does not take it back')
        self.assertTrue(g['firstFocus'], 'a click puts the caret in the box')
        self.assertEqual(g['firstRoute'], {'room':self.po,'thread':ta})
        self.assertEqual(g['firstDraft'], '')

    def test_role_labels_replace_the_header_and_replies_circles(self):
        labels = self.got['roleLabels']
        self.assertEqual(labels['head']['label'], 'PO')
        self.assertIn('claude', labels['head']['title'].lower(), labels)
        self.assertEqual(labels['head']['circles'], 0)
        self.assertNotIn('PO', labels['head']['name'], labels)
        self.assertEqual(labels['replies']['circles'], 0)
        self.assertEqual([x['text'] for x in labels['replies']['labels']], ['PO'])
        self.assertTrue(all('rl po' in x['cls'] and x['width'] > x['height'] for x in labels['replies']['labels']), labels)

    def test_another_thread_and_drafts(self):
        g, ta, tb = self.got, self.got['ta'], self.got['tb']
        self.assertEqual(g['second']['thread'], tb)
        self.assertEqual(g['secondRoute'], {'room':self.po,'thread':tb})
        self.assertEqual(g['secondDraft'], '')
        self.assertEqual(g['secondFocus'], 'probe-field', 'no click, no caret move')
        self.assertEqual(g['typing'], {'target':ta,'active':'probe-field'}, 'a field being typed in keeps the caret')
        self.assertEqual(g['backDraft'], 'Thread A draft')
        self.assertEqual(g['poDraft'], 'PO draft kept')

    def test_not_on_restore_or_catch_up(self):
        g = self.got
        self.assertEqual(g['auto']['kind'], 'po')
        self.assertEqual(g['reloadPo']['target']['kind'], 'po', g['reloadPo'])
        self.assertTrue(g['reloadPo']['thread'])
        self.assertEqual(g['reloadThread']['kind'], 'thread')
        self.assertEqual(g['follow']['target']['kind'], 'po', g['follow'])

    def test_close_gives_the_po_back(self):
        self.assertEqual(self.got['closed']['kind'], 'po')

    def test_balloon_thread_promoted(self):
        g = self.got
        self.assertEqual(g['balloon'], {'kind':'thread','room':self.po,'thread':'','mid':self.balloon})
        self.assertEqual(g['promoted']['target']['thread'], g['promoted']['tid'])
        self.assertEqual(g['promotedRoute'], {'room':self.po,'thread':g['promoted']['tid']})

    def test_chat_in_its_own_window(self):
        g, ta, tb = self.got, self.got['ta'], self.got['tb']
        # The same tab: the draft typed for thread A in the workspace box is its draft here too.
        self.assertEqual(g['ownA'], {'thread':ta,'draft':'Thread A draft','focus':True})
        self.assertEqual(g['ownB'], {'thread':tb,'draft':''})
        self.assertEqual(g['ownPo'], {'thread':'','draft':'Own PO draft'})
        self.assertEqual(g['ownSame'], tb)
        self.assertEqual(g['ownAuto'], '')

    def test_balloon_thread_in_its_own_window(self):
        g, tid = self.got, self.got['promoted']['tid']
        self.assertEqual(g['ownAlias'], tid, 'the dashboard kept the alias in this tab')
        self.assertEqual(g['ownBalloonKnown'], {'thread':tid,'aim':None}, 'an id known at once: the aim is taken')
        later = g['ownBalloonLater']
        self.assertEqual(later['thread'], tid, 'an id found later: the box follows it')
        self.assertEqual(later['aim'], None)
        self.assertEqual(later['draft'], 'Balloon draft')
        self.assertEqual(later['stored'], 'Balloon draft', 'the draft moves to the id')
        self.assertIsNone(later['old'])
        self.assertEqual(g['ownIdDraft'], {'thread':tid,'draft':'Existing ID draft','stored':'Existing ID draft'},
                         "the id's own draft is shown and kept")

    def test_p340_back_to_the_po(self):
        p, title = self.got['p340'], 'Motors PO'
        self.assertEqual(p['thread']['kind'], 'thread')
        self.assertTrue(p['thread']['back'], 'the To line offers the way back')
        self.assertEqual(p['thread']['placeholder'], 'Reply in thread…')
        self.assertTrue(p['stacked'], 'the thread was a tab in front of the PO chat')
        for case in ('tabBeside', 'tabStacked', 'x', 'close', 'reload'):
            with self.subTest(case):
                got = p[case]
                self.assertEqual((got['kind'], got['to'], got['back']), ('po', title, False), got)
                self.assertEqual(got['placeholder'], 'Message the PO…')
                self.assertEqual(got['draft'], 'P340 thread draft', 'the thread keeps its draft')
                self.assertEqual(got['box'], 'PO draft kept', "the PO's draft is in the box")
        self.assertTrue(p['x']['focus'], 'the × leaves the caret in the box')
        self.assertFalse(p['close']['panel'])
        self.assertFalse(p['reload']['panel'])
        self.assertLess(p['quickAt'], 600, 'the quick click came within the 600 ms hold')
        self.assertLess(p['keyAt'], 600, 'the key came within the 600 ms hold')
        k = p['keyQuick']
        self.assertTrue(k['front'], 'the key brought the PO chat forward')
        self.assertEqual((k['kind'], k['placeholder']), ('po', 'Message the PO…'),
                         'a key on the tabs at once after a thread opened takes the box')
        self.assertEqual((p['tabQuick']['kind'], p['tabQuick']['placeholder']), ('po', 'Message the PO…'),
                         'a click on the PO tab right after a thread opened takes the box')
        self.assertLess(p['reloadMs'], 4000, 'no wait for a thread that is not coming back')

    def test_p340_phone(self):
        p, ta = self.got['p340'], self.got['ta']
        self.assertEqual(p['phoneThread'], {'route': ta, 'side': 'Reply in thread…'})
        self.assertEqual(p['phoneBack'], {'route': '', 'placeholder': 'Message the PO…'})

    def test_guest_chat_leaves_the_draft_to_the_workspace(self):
        self.assertEqual(self.got['guest'], {'guest':True,'thread':'Tzz','old':'{"head":"stale"}','moved':None,'alias':None})


if __name__ == '__main__':
    unittest.main()
