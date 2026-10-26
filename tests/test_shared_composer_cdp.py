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
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'PO chat');
    out.mainAlone=await ev(count);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'thread panel');
    await sleep(500); out.desktop=await ev(count); await snap('after-1728x1117');
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
    await size(1440,900); await sleep(650); out.medium=await ev(count); await snap('after-1440x900');
    await size(390,844); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow() && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'phone chat');
    await ev(`(()=>{const d=${PO}.contentDocument;if(d.getElementById('thr-side').hidden)d.querySelector('#msgs .thr-open[data-thr]').click();return 0})()`);
    await until(`!!${PO}.contentDocument.getElementById('ts-frame')?.contentDocument?.querySelector('#compose')`,'phone thread');
    await sleep(600);
    out.phone=await ev(`(()=>{const f=${PO}, d=f?.contentDocument, side=d?.getElementById('thr-side'),t=side?.querySelector('iframe')?.contentDocument;
      return {side:!!side&&!side.hidden, main:!!d&&getComputedStyle(d.getElementById('compose')).display!=='none'&&!side?.classList.contains('layer'),
        thread:!!t?.getElementById('compose')&&getComputedStyle(t.getElementById('compose')).display!=='none',draft:t?.getElementById('input')?.value||'',
        point:t?.querySelector('#ed-points textarea.ed-text')?.value||''}})()`);
    await snap('after-390x844');
    await ev(`(()=>{const f=${PO}.contentDocument.getElementById('ts-frame'),w=f.contentWindow;
      w.eval('ATT.keep([{room:${JSON.stringify(A.po)},name:"sample.png"}]);edSave()');return 0})()`);
    await sleep(350);
    await size(1728,1117); await sleep(900);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click();0`);
    await until(`!!${THR}?.contentDocument?.getElementById('thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'thread again');
    await ev(`document.getElementById('po-panel').hidden=true;0`); await sleep(550); out.threadAlone=await ev(count);
    await ev(`document.getElementById('po-panel').hidden=false;0`); await sleep(550); out.returned=await ev(count);
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
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        sid = 'shared-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        transcript = base / 'transcripts' / 'C--shared'; transcript.mkdir()
        at = time.time() - 3600
        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','A conversation with a thread.',1), turn('assistant','The thread reply.',2)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj,'po':cls.po,'shots':os.environ.get('SHARED_SCREENSHOT_DIR','')}
        script = base / 'shared_composer_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=150)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_one_box_and_destinations(self):
        g = self.got
        self.assertTrue(g['mainAlone']['main'])
        for key in ('desktop','medium','returned','reload'):
            with self.subTest(key=key):
                self.assertTrue(g[key]['main'],g[key])
                self.assertFalse(g[key]['thread'],g[key])
                self.assertTrue(g[key]['reply'],g[key])
        self.assertTrue(g['threadRoute']['thread'])
        self.assertTrue(g['threadRoute']['chip'])
        self.assertFalse(g['mainRoute']['thread'])
        self.assertFalse(g['mainRoute']['chip'])
        self.assertEqual(g['replyRoute']['thread'],g['threadRoute']['thread'])
        self.assertTrue(g['replyRoute']['focus'])
        self.assertEqual(g['draft'],'A draft kept through both views')
        self.assertTrue(g['threadAlone']['thread'])
        self.assertEqual(g['returned']['mainDraft'],'A draft kept through both views')
        self.assertEqual(g['reload']['mainDraft'],'A draft kept through both views')
        self.assertEqual(g['reload']['image'],'sample.png')
        self.assertTrue(g['threadWindow'])
        self.assertFalse(g['mainChoiceReload']['chip'])
        self.assertTrue(g['phone']['side'],g['phone'])
        self.assertTrue(g['phone']['thread'],g['phone'])
        self.assertEqual(g['phone']['draft'],'A draft kept through both views')
        self.assertEqual(g['phone']['point'],'First numbered point')
