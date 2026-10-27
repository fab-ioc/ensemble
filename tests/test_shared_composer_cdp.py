"""One composer follows a PO conversation and its open thread in Dock and on a phone."""
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
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'PO chat');
    out.mainAlone=await ev(count); out.workspaceAlone=await ev(workspace);
    await ev(`(()=>{const f=${PO},tid=f.contentDocument.querySelector('#msgs .thr-open[data-thr]').dataset.thr;
      f.contentWindow.sideOpen({tid,auto:true});return 0})()`);
    await until(`!!${THR}?.contentDocument?.querySelector('#thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'automatic thread panel');
    out.autoOpen=await ev(`(async()=>{const f=${PO},w=f.contentWindow,old=w.postOk;let body;
      w.postOk=async(_path,b)=>{body=b;return {}};
      try{await w.sendResuming('probe','', 'auto-probe-key',[]);return {thread:body.thread||'',chip:!f.contentDocument.getElementById('compose-target').hidden};}
      finally{w.postOk=old}})()`);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'thread panel');
    await sleep(500); out.desktop=await ev(count); out.workspaceDesktop=await ev(workspace); await snap('after-1728x1117');
    if (A.baseline) {
      await ev(`pdChatOpen(${JSON.stringify(A.task)});0`);
      await until(`!!PD.rt.get('chat:'+${JSON.stringify(A.task)})?.el.querySelector('iframe.dp-session')?.contentDocument?.getElementById('compose')`,'baseline task');
      await sleep(500); await snap('before-three-1728x1117');
      await size(1440,900); await sleep(500); await snap('before-three-1440x900');
      await size(390,844); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
      await sleep(700); await snap('before-390x844');
      console.log(JSON.stringify({baseline:true})); return;
    }
    // A click in the thread selects its destination; a click in the channel
    // clears it. Test the actual request body without posting a message.
    out.threadRoute=await ev(`(async()=>{const f=${PO}, t=${THR}; t.contentDocument.querySelector('#msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));
      const w=f.contentWindow, old=w.postOk; let body; w.postOk=async(_path,b)=>{body=b;return {}};
      try{await w.sendResuming('probe','', 'probe-key',[]);return {thread:body.thread||'',also:body.also||false,chip:!f.contentDocument.getElementById('compose-target').hidden};}
      finally{w.postOk=old}})()`);
    out.mainRoute=await ev(`(async()=>{const f=${PO}, d=f.contentDocument,w=f.contentWindow,old=w.postOk;let body;
      d.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));w.postOk=async(_path,b)=>{body=b;return {}};
      try{await w.sendResuming('probe','', 'probe-key',[]);return {thread:body.thread||'',chip:!d.getElementById('compose-target').hidden};}
      finally{w.postOk=old}})()`);
    out.replyRoute=await ev(`(async()=>{const f=${PO}, t=${THR};t.contentDocument.getElementById('thread-reply').click();
      const w=f.contentWindow,old=w.postOk;let body;w.postOk=async(_path,b)=>{body=b;return {}};
      try{await w.sendResuming('probe','', 'probe-key',[]);return {thread:body.thread||'',focus:f.contentDocument.activeElement===f.contentDocument.getElementById('input')};}
      finally{w.postOk=old}})()`);
    out.draft=await ev(`(()=>{const f=${PO},t=${THR},w=f.contentWindow,i=f.contentDocument.getElementById('input');w.edAddLast();
      i.value='A draft kept through both views';i.dispatchEvent(new Event('input',{bubbles:true}));
      const p=f.contentDocument.querySelector('#ed-points textarea.ed-text');p.value='First numbered point';p.dispatchEvent(new Event('input',{bubbles:true}));
      f.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));t.contentDocument.getElementById('thread-reply').click();return i.value})()`);
    await size(1440,900); await sleep(650); out.medium=await ev(count); out.workspaceMedium=await ev(workspace); await snap('after-1440x900');
    await size(1728,1117);
    await ev(`pdChatOpen(${JSON.stringify(A.task)});0`);
    await until(`WC.target?.room===${JSON.stringify(A.task)} && document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes(${JSON.stringify('id='+A.task)}) && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('send')?.disabled===false`,'task target');
    const TASK = `PD.rt.get('chat:'+${JSON.stringify(A.task)})?.el.querySelector('iframe.dp-session')`;
    await until(`!!${TASK}?.contentDocument?.body.classList.contains('workspace-guest')`,'task box hidden');
    const route = async () => ev(`(async()=>{const w=document.getElementById('workspace-compose-frame').contentWindow,old=w.postOk;let body;
      w.postOk=async(_path,b)=>{body=b;return {}};try{await w.sendResuming('probe','', 'probe-key',[]);return {room:body.roomId,thread:body.thread||'',title:document.getElementById('workspace-compose-link').textContent};}
      finally{w.postOk=old}})()`);
    const sendProbe = async name => ev(`(async()=>{const f=document.getElementById('workspace-compose-frame'),d=f.contentDocument,w=f.contentWindow,old=w.postOk;let body,count=0;
      w.postOk=async(path,b)=>{if(path==='/api/room/resume'){body=b;count++}return old(path,b)};
      try{const i=d.getElementById('input');i.value=${JSON.stringify('Send probe ')}+${JSON.stringify(name)};i.dispatchEvent(new Event('input',{bubbles:true}));d.getElementById('send').click();
        for(let n=0;n<80&&(!body||i.value||d.getElementById('send').textContent!=='Send');n++)await new Promise(r=>setTimeout(r,50));
        return {room:body?.roomId||'',thread:body?.thread||'',count};}
      finally{w.postOk=old}})()`);
    out.taskRoute=await route();
    out.taskSend=await sendProbe('task');
    await ev(`${TASK}.contentWindow.refresh();0`);
    await until(`${TASK}.contentDocument.getElementById('msgs')?.textContent.includes('Send probe task')`,'task message landed');
    await ev(`(()=>{const f=document.getElementById('workspace-compose-frame'),i=f.contentDocument.getElementById('input');i.value='Task draft';i.dispatchEvent(new Event('input',{bubbles:true}));f.contentWindow.eval('ATT.keep([{room:${JSON.stringify(A.task)},name:"sample.png"}])');f.contentWindow.edStoreNow();return 0})()`);
    out.taskGuest=await ev(`(()=>{const f=${TASK};return {class:f.contentDocument.body.className,display:getComputedStyle(f.contentDocument.getElementById('compose')).display,outer:f.getBoundingClientRect().toJSON()}})()`);
    await sleep(300);
    out.threeDesktop=await ev(workspace); await snap('after-three-1728x1117');
    await ev(`${THR}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='thread'`,'thread target');
    await until(`document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes('thread=') && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('send')?.disabled===false`,'thread editor');
    out.threadWorkspaceRoute=await route();
    out.threadSend=await sendProbe('thread');
    await ev(`${THR}.contentWindow.refresh();0`);
    await until(`${THR}.contentDocument.getElementById('msgs')?.textContent.includes('Send probe thread')`,'thread message landed');
    await ev(`(()=>{const f=document.getElementById('workspace-compose-frame'),w=f.contentWindow,d=f.contentDocument,i=d.getElementById('input');
      w.edAddLast();i.value='Thread draft';i.dispatchEvent(new Event('input',{bubbles:true}));const p=d.querySelector('#ed-points textarea');p.value='Numbered thread point';p.dispatchEvent(new Event('input',{bubbles:true}));w.edStoreNow();return 0})()`);
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='po'`,'PO target');
    await until(`!document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes('thread=') && document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes(${JSON.stringify('id='+A.po)}) && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('send')?.disabled===false`,'PO editor');
    out.poWorkspaceRoute=await route();
    out.poSend=await sendProbe('PO');
    await ev(`${PO}.contentWindow.refresh();0`);
    await until(`${PO}.contentDocument.getElementById('msgs')?.textContent.includes('Send probe PO')`,'PO message landed');
    await ev(`(()=>{const f=document.getElementById('workspace-compose-frame'),i=f.contentDocument.getElementById('input');i.value='PO draft';i.dispatchEvent(new Event('input',{bubbles:true}));f.contentWindow.edStoreNow();return 0})()`);
    await ev(`${TASK}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='task'`,'task picked');
    await until(`document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes(${JSON.stringify('id='+A.task)}) && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('send')?.disabled===false`,'task editor again');
    out.taskPickedRoute=await route();
    await size(1440,900); await sleep(650); out.threeMedium=await ev(workspace); await snap('after-three-1440x900');
    await ev(`${THR}.contentDocument.getElementById('thread-reply').click();0`);
    await until(`WC.target?.kind==='thread'`,'Reply target');
    await until(`document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes('thread=')`,'thread editor again');
    await until(`document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value==='Thread draft'`,'thread draft switch');
    out.replyWorkspaceRoute=await route();
    out.threadDraft=await ev(`(()=>{const d=document.getElementById('workspace-compose-frame').contentDocument;return {head:d.getElementById('input').value,point:d.querySelector('#ed-points textarea')?.value||''}})()`);
    await send('Page.reload',{},s);
    await sleep(500);
    await until(`WC.target?.kind==='thread' && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value==='Thread draft'`,'thread draft reload');
    out.threadReload=await ev(`(()=>{const d=document.getElementById('workspace-compose-frame').contentDocument;return {head:d.getElementById('input').value,point:d.querySelector('#ed-points textarea')?.value||''}})()`);
    await ev(`${TASK}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='task' && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value==='Task draft'`,'task draft reload');
    out.taskReload=await ev(`(()=>{const d=document.getElementById('workspace-compose-frame').contentDocument;return {head:d.getElementById('input').value,image:d.querySelector('#att-chips .att-name')?.textContent||''}})()`);
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'window');0`);
    await until(`!!PD.dock.popWindow('chat:'+${JSON.stringify(A.task)})`,'task window');
    let taskPop;
    for(let i=0;i<40&&!taskPop;i++){const targets=(await send('Target.getTargets')).targetInfos;
      taskPop=targets.find(x=>x.type==='page'&&x.targetId!==targetId&&x.url.startsWith(A.base));if(!taskPop)await sleep(100);}
    if(!taskPop)throw Error('no task window');
    const {sessionId:tps}=await send('Target.attachToTarget',{targetId:taskPop.targetId,flatten:true});
    for(let i=0;i<100;i++){try{const r=await send('Runtime.evaluate',{expression:`!!document.querySelector('iframe.dp-session')?.contentDocument?.getElementById('compose')`,returnByValue:true},tps);
      if(r.result.value)break;}catch(e){}await sleep(100);}
    out.taskWindow=(await send('Runtime.evaluate',{expression:`(()=>{const f=document.querySelector('iframe.dp-session'),d=f?.contentDocument;
      const before={draft:d?.getElementById('input')?.value||'',image:d?.querySelector('#att-chips .att-name')?.textContent||''};
      f.contentWindow.edAddLast();const point=d.querySelector('#ed-points textarea');point.value='Point from popped task';point.dispatchEvent(new Event('input',{bubbles:true}));
      const i=d?.getElementById('input');i.value='Changed in popped task';i.dispatchEvent(new Event('input',{bubbles:true}));
      return {box:!!d&&getComputedStyle(d.getElementById('compose')).display!=='none',target:d?.getElementById('compose-destination-link')?.textContent||'',before};})()`,returnByValue:true},tps)).result.value;
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'pinned');0`);
    await until(`!PD.dock.popWindow('chat:'+${JSON.stringify(A.task)})`,'task back');
    try { await until(`WC.target?.room===${JSON.stringify(A.task)} && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value==='Changed in popped task'`,'popped task draft restored'); }
    catch(e) { throw Error(e.message+' '+JSON.stringify(await ev(`({target:WC.target,popped:[...WC.popped],seq:WC.pickSeq,stored:sessionStorage.getItem('cd-draft:'+${JSON.stringify(A.task)}),editor:document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value})`))); }
    out.taskBack=await ev(`(()=>{const d=document.getElementById('workspace-compose-frame').contentDocument;return {target:document.getElementById('workspace-compose-link').textContent,draft:d.getElementById('input').value,point:d.querySelector('#ed-points textarea')?.value||'',image:d.querySelector('#att-chips .att-name')?.textContent||''}})()`);
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'window');0`);
    await until(`!!PD.dock.popWindow('chat:'+${JSON.stringify(A.task)})`,'task popped twice');
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='po'`,'PO picked while task out');
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'pinned');0`);
    await until(`!PD.dock.popWindow('chat:'+${JSON.stringify(A.task)})`,'task back after PO pick');
    await sleep(200);out.pickWhileOut=await ev(`WC.target?.kind`);
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await until(`WC.target?.kind==='po' && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input')?.value==='PO draft'`,'PO draft reload');
    out.poReload=await ev(`document.getElementById('workspace-compose-frame').contentDocument.getElementById('input').value`);
    await ev(`PD.dock.removePanel('chat:'+${JSON.stringify(A.task)});0`);
    await size(390,844); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow() && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'phone chat');
    await ev(`(()=>{const d=${PO}.contentDocument;if(d.getElementById('thr-side').hidden)d.querySelector('#msgs .thr-open[data-thr]').click();return 0})()`);
    await until(`!!${PO}.contentDocument.getElementById('ts-frame')?.contentDocument?.querySelector('#compose')`,'phone thread');
    await sleep(600);
    out.phone=await ev(`(()=>{const f=${PO}, d=f?.contentDocument, side=d?.getElementById('thr-side'),t=side?.querySelector('iframe')?.contentDocument;
      return {side:!!side&&!side.hidden, main:!!d&&getComputedStyle(d.getElementById('compose')).display!=='none'&&!side?.classList.contains('layer'),
        thread:!!t?.getElementById('compose')&&getComputedStyle(t.getElementById('compose')).display!=='none',draft:t?.getElementById('input')?.value||'',
        point:t?.querySelector('#ed-points textarea.ed-text')?.value||''}})()`);
    out.workspacePhone=await ev(workspace);
    await snap('after-390x844');
    out.phoneBack=await ev(`(()=>{const d=${PO}.contentDocument,t=d.getElementById('ts-frame').contentDocument;
      const input=t.getElementById('input');input.value='Changed on phone';input.dispatchEvent(new Event('input',{bubbles:true}));
      d.getElementById('ts-back').click();return {draft:d.getElementById('input').value,stored:JSON.parse(sessionStorage.getItem('cd-draft:${A.po}')).head}})()`);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click();0`);
    await until(`!!${PO}.contentDocument.getElementById('ts-frame')?.contentDocument?.getElementById('input')`,'phone thread reopened');
    await ev(`(()=>{const f=${PO}.contentDocument.getElementById('ts-frame'),w=f.contentWindow;
      w.eval('ATT.keep([{room:${JSON.stringify(A.po)},name:"sample.png"}]);edSave()');return 0})()`);
    await sleep(350);
    await size(1728,1117); await sleep(900);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click();0`);
    await until(`!!${THR}?.contentDocument?.getElementById('thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'thread again');
    await ev(`document.getElementById('po-panel').hidden=true;0`); await sleep(550); out.threadAlone=await ev(count);
    await ev(`(()=>{const i=${THR}.contentDocument.getElementById('input');i.value='Changed in hidden Dock thread';i.dispatchEvent(new Event('input',{bubbles:true}));
      PD.dock.removePanel('thread:po');document.getElementById('po-panel').hidden=false;return 0})()`);
    await sleep(550); out.closedThread=await ev(count); out.closedTarget=await ev(workspace);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click();0`);
    await until(`!!${THR}?.contentDocument?.getElementById('thread-reply')`,'thread reopened after close');
    await sleep(550); out.returned=await ev(count);
    await send('Page.reload',{},s);
    await until(`!!${PO}?.contentDocument?.getElementById('input') && !!${THR}?.contentDocument?.getElementById('thread-reply')`,'reload');
    await sleep(650); out.reload=await ev(count);
    out.reload.image=await ev(`${PO}.contentDocument.querySelector('#att-chips .att-name')?.textContent||''`);
    await ev(`PD.dock.setViewMode('thread:po','window');0`);
    await until(`!!PD.dock.popWindow('thread:po')`,'thread window');
    let pop;
    for(let i=0;i<40&&!pop;i++){const targets=(await send('Target.getTargets')).targetInfos;
      pop=targets.find(x=>x.type==='page'&&x.targetId!==targetId&&x.url.startsWith(A.base));if(!pop)await sleep(100);}
    if(!pop)throw Error('no thread window');
    const {sessionId:ps}=await send('Target.attachToTarget',{targetId:pop.targetId,flatten:true});
    for(let i=0;i<100;i++){try{const r=await send('Runtime.evaluate',{expression:`!!document.querySelector('iframe.po-thread')?.contentDocument?.getElementById('compose')`,returnByValue:true},ps);
      if(r.result.value)break;}catch(e){}await sleep(100);}
    const pr=await send('Runtime.evaluate',{expression:`(()=>{const d=document.querySelector('iframe.po-thread')?.contentDocument;return !!d&&getComputedStyle(d.getElementById('compose')).display!=='none'})()`,returnByValue:true},ps);
    out.threadWindow=pr.result.value;
    await ev(`PD.dock.setViewMode('thread:po','pinned');0`);
    await until(`!!${THR}?.contentDocument?.getElementById('thread-reply')`,'thread docked');
    await ev(`${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));0`);
    await send('Page.reload',{},s);
    await until(`!!${PO}?.contentDocument?.getElementById('input') && !!${THR}?.contentDocument?.getElementById('thread-reply')`,'main choice reload');
    await sleep(650); out.mainChoiceReload=await ev(count);
    // Exercise the pre-ID balloon state and its title event in one turn, as
    // a real first reply can resolve the ID before a polling test observes it.
    out.promotedFrom=await ev(`(()=>{const button=[...${PO}.contentDocument.querySelectorAll('#msgs .thr-reply[data-mid]')].find(x=>x.dataset.mid==='shared-po:2');
      button.click();const e=PD.rt.get('thread:po'),tid=e.tid,mid=button.dataset.mid;
      e.tid='';e.mid=mid;wcChoose({kind:'thread',room:${JSON.stringify(A.po)},mid});
      pdThreadTitle(${JSON.stringify(A.po)},'A new topic without replies.',tid);
      return {mid,tid,target:WC.target};})()`);
    await until(`WC.target?.kind==='thread' && WC.target.thread===PD.rt.get('thread:po')?.tid`,'promoted thread target');
    await until(`document.getElementById('workspace-compose-frame')?.contentDocument?.URL.includes('thread='+${JSON.stringify(out.promotedFrom.tid)}) && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('input') && document.getElementById('workspace-compose-frame')?.contentDocument?.getElementById('send')?.disabled===false`,'promoted thread editor');
    await sleep(300);
    out.newThreadFirst=await sendProbe('new thread first');
    out.newThreadSecond=await sendProbe('new thread second');
    out.promotedTarget=await ev(workspace);
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class SharedComposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-shared-composer-', ignore_cleanup_errors=True)
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
        baseline = os.environ.get('BASELINE_STATIC_DIR', '')
        if baseline:
            cls.patches.append(mock.patch.object(dashboard, 'STATIC_DIR', Path(baseline)))
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
        lines = [turn('user','A conversation with a thread.',1), turn('assistant','The thread reply.',2),
                 turn('user','A new topic without replies.',3)]
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
                'proj':cls.proj,'po':cls.po,'task':cls.task,'shots':os.environ.get('SHARED_SCREENSHOT_DIR',''),
                'baseline':bool(baseline)}
        script = base / 'shared_composer_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=150)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_one_box_and_destinations(self):
        g = self.got
        if os.environ.get('BASELINE_STATIC_DIR'):
            self.assertTrue(g['baseline'])
            return
        self.assertEqual(g['autoOpen'], {'thread':'', 'chip':False})
        for key in ('workspaceAlone','workspaceDesktop','workspaceMedium'):
            with self.subTest(key=key):
                box = g[key]
                self.assertTrue(box['shown'] and box['editor'], box)
                self.assertAlmostEqual(box['width'], box['host'], delta=3)
                self.assertNotIn('http', box['target'])
                self.assertNotIn('room-', box['target'])
        self.assertEqual(g['workspaceAlone']['target'], 'Motors PO')
        self.assertEqual(g['workspaceMedium']['target'], 'A conversation with a thread.')
        self.assertFalse(g['desktop']['main'])
        self.assertFalse(g['desktop']['thread'])
        self.assertTrue(g['desktop']['reply'])
        self.assertFalse(g['workspacePhone']['shown'])
        self.assertTrue(g['threadWindow'])
        self.assertTrue(g['phone']['side'],g['phone'])
        self.assertTrue(g['phone']['thread'],g['phone'])
        self.assertEqual(g['phone']['draft'], 'Thread draft')
        self.assertEqual(g['phone']['point'], 'Numbered thread point')
        self.assertEqual(g['phoneBack'], {'draft':'PO draft','stored':'PO draft'})
        self.assertEqual(g['taskRoute']['room'], self.task)
        self.assertEqual(g['taskGuest']['display'], 'none', g['taskGuest'])
        self.assertEqual(g['taskSend'], {'room':self.task,'thread':'','count':1})
        self.assertEqual(g['poSend'], {'room':self.po,'thread':'','count':1})
        self.assertEqual(g['threadSend']['room'], self.po)
        self.assertEqual(g['threadSend']['thread'], g['threadWorkspaceRoute']['thread'])
        self.assertEqual(g['threadSend']['count'], 1)
        self.assertEqual(g['taskRoute']['title'], '#1 Task chat')
        self.assertEqual(g['taskPickedRoute']['room'], self.task)
        self.assertEqual(g['poWorkspaceRoute']['room'], self.po)
        self.assertEqual(g['poWorkspaceRoute']['thread'], '')
        self.assertEqual(g['poWorkspaceRoute']['title'], 'Motors PO')
        self.assertEqual(g['threadWorkspaceRoute']['room'], self.po)
        self.assertTrue(g['threadWorkspaceRoute']['thread'])
        self.assertEqual(g['threadWorkspaceRoute']['title'], 'A conversation with a thread.')
        self.assertEqual(g['replyWorkspaceRoute']['thread'], g['threadWorkspaceRoute']['thread'])
        self.assertEqual(g['threadDraft'], {'head':'Thread draft','point':'Numbered thread point'})
        self.assertEqual(g['threadReload'], g['threadDraft'])
        self.assertEqual(g['taskReload'], {'head':'Task draft','image':'sample.png'})
        self.assertEqual(g['taskWindow'], {'box':True,'target':'#1 Task chat','before':{'draft':'Task draft','image':'sample.png'}})
        self.assertEqual(g['taskBack'], {'target':'#1 Task chat','draft':'Changed in popped task','point':'Point from popped task','image':'sample.png'})
        self.assertEqual(g['pickWhileOut'], 'po')
        self.assertEqual(g['newThreadFirst']['room'], self.po)
        self.assertEqual(g['promotedFrom']['target']['thread'], g['promotedFrom']['tid'])
        self.assertEqual(g['newThreadFirst']['thread'], g['promotedFrom']['tid'])
        self.assertEqual(g['newThreadSecond']['thread'], g['promotedFrom']['tid'])
        self.assertNotIn('http', g['promotedTarget']['target'])
        self.assertEqual(g['closedTarget']['target'], 'Motors PO')
        self.assertEqual(g['poReload'], 'PO draft')
        for key in ('threeDesktop','threeMedium'):
            box = g[key]
            self.assertTrue(box['shown'] and box['editor'], box)
            self.assertAlmostEqual(box['width'], box['host'], delta=3)
