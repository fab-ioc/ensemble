"""One activity strip per window, a Send box that switches target in place, and its height by hand (#274)."""
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
  const evIn=async (expression,sess) => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sess);
    if(r.exceptionDetails) throw Error(expression.slice(0,160)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const ev=e => evIn(e,s);
  const until=async (expression,label,sess=s) => { for(let i=0;i<120;i++){try{if(await evIn(expression,sess))return;}catch(e){} await sleep(150);} throw Error('timeout '+label); };
  const snap = async name => { if (!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},s);
    require('fs').writeFileSync(A.shots+'/'+name+'.png',Buffer.from(r.data,'base64')); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.rt.get('thread:po')?.el.querySelector('iframe')`;
  const BOX = `document.getElementById('workspace-compose-frame')`;
  // A working agent in every chat page of a window: what the hub's poll says
  // while the PO works, held against the page's own polls.
  const WORKING = `(()=>{const all=[];const walk=w=>{for(const f of w.document.querySelectorAll('iframe')){try{const x=f.contentWindow;
      if(x&&x.document&&typeof x.renderLiveTail==='function'&&!x.document.body.classList.contains('workspace-compose')){all.push(x)}walk(x)}catch(e){}}};
    if(typeof renderLiveTail==='function')all.push(window);walk(window);
    for(const x of all){if(x.__working)continue;x.__working=1;
      const real=x.renderLiveTail;x.renderLiveTail=function(){x.eval("ROOM_LIVE=true;TAIL_WORKING=[{identity:'claude',ptyId:'pty-test'}]");return real.apply(this,arguments)};
      x.renderLiveTail();}
    return all.length})()`;
  // Strips a reader can see in this window: shown, with height, not under a panel.
  const STRIPS_FN = `()=>{const out=[];const walk=(w,label)=>{const b=w.document.getElementById('livetail');
      if(b&&!b.hidden&&b.getBoundingClientRect().height>0){let ok=true,el=b,win=w;
        while(ok&&win){const r=el.getBoundingClientRect();if(!(r.width>0&&r.height>0)){ok=false;break}
          const x=Math.min(Math.max(r.left+r.width/2,0),win.innerWidth-1),y=Math.min(Math.max(r.top+r.height/2,0),win.innerHeight-1);
          const hit=win.document.elementFromPoint(x,y);if(!hit||!(el===hit||el.contains(hit))){ok=false;break}
          if(win===window)break;el=win.frameElement;win=win.parent;}
        if(ok)out.push(label);}
      for(const f of w.document.querySelectorAll('iframe')){try{if(f.contentWindow?.document)walk(f.contentWindow,f.className||f.id||'frame')}catch(e){}}};
    walk(window,'top');return out}`;
  const STRIPS = `(${STRIPS_FN})()`;
  // Every frame over a switch: the box, its editor, and the strips.
  const RECORD = `(()=>{const box=document.getElementById('workspace-compose'),f=${BOX};
    f.contentWindow.__mark=(f.contentWindow.__mark||0)+1;const mark=f.contentWindow.__mark,input=f.contentDocument.getElementById('input');
    window.__rec=[];window.__muts=0;
    const mo=new MutationObserver(l=>{window.__muts+=l.length});mo.observe(box,{attributes:true,subtree:true});window.__mo=mo;
    const strips=${STRIPS_FN};
    const tick=()=>{if(!window.__rec)return;const b=box.getBoundingClientRect(),d=f.contentDocument,cmp=d?.getElementById('compose');
      window.__rec.push({hidden:box.hidden,top:Math.round(b.top*10)/10,h:Math.round(b.height*10)/10,
        doc:f.contentWindow?.__mark===mark,input:d?.getElementById('input')===input,
        compose:cmp?getComputedStyle(cmp).display:'',ch:cmp?Math.round(cmp.getBoundingClientRect().height):0,
        focus:d?.activeElement===input,to:document.getElementById('workspace-compose-link').textContent,
        strips:strips().length});
      requestAnimationFrame(tick)};requestAnimationFrame(tick);return 0})()`;
  const STOP = `(()=>{const r=window.__rec;window.__rec=null;window.__mo.disconnect();return {frames:r,muts:window.__muts}})()`;
  const pickThread = `${THR}.contentDocument.getElementById('msgs').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))`;
  const pickPo = `${PO}.contentDocument.getElementById('msgpane').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'PO chat');
    await until(`${BOX}?.contentDocument?.getElementById('send') && document.getElementById('workspace-compose').hidden===false`,'workspace composer');
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#thread-reply') && !${THR}.contentDocument.getElementById('thread-reply').hidden`,'thread panel');
    await sleep(600);
    // ---- Switching the Send box between the PO chat and its thread ----
    await ev(`(()=>{const f=${BOX},i=f.contentDocument.getElementById('input');i.focus();return 0})()`);
    await ev(WORKING);
    await sleep(300);
    out.stripsDesktop=await ev(STRIPS);
    await snap('strip-desktop-1728x1117');
    await ev(RECORD);
    const switches=[];
    for(let i=0;i<10;i++){
      const toThread=i%2===0;
      await ev(`(()=>{const f=${BOX};f.contentDocument.getElementById('input').focus();${toThread?pickThread:pickPo};return 0})()`);
      await until(`WC.target?.kind===${JSON.stringify(toThread?'thread':'po')} && ${BOX}.contentDocument?.readyState==='complete' && !!${BOX}.contentDocument.getElementById('input')`,'switch '+i);
      await sleep(900);
      switches.push(await ev(`({to:document.getElementById('workspace-compose-link').textContent,strips:${STRIPS}})`));
    }
    out.switch=await ev(STOP);
    out.switches=switches;
    // Reply puts the box on the thread too.
    await ev(`${pickPo};0`); await until(`WC.target?.kind==='po'`,'po before reply'); await sleep(600);
    await ev(RECORD);
    await ev(`${THR}.contentDocument.getElementById('thread-reply').click();0`);
    await until(`WC.target?.kind==='thread'`,'reply'); await sleep(900);
    out.reply=await ev(STOP);
    out.replyStrips=await ev(STRIPS);
    // ---- The box's height by hand ----
    const ED = `${BOX}.contentDocument.getElementById('editor').getBoundingClientRect().height`;
    const mouse=(type,x,y,clickCount=1)=>send('Input.dispatchMouseEvent',{type,x,y,button:'left',buttons:type==='mouseReleased'?0:1,clickCount},s);
    const barAt=async()=>ev(`(()=>{const f=${BOX},r=f.getBoundingClientRect(),b=f.contentDocument.getElementById('compose-size').getBoundingClientRect();
      return {x:r.left+b.left+b.width/2,y:r.top+b.top+b.height/2,shown:!f.contentDocument.getElementById('compose-size').hidden}})()`);
    const atEnd = `(()=>{const m=${PO}.contentDocument.getElementById('msgs');return m.scrollHeight-m.scrollTop-m.clientHeight<4})()`;
    await ev(`${pickPo};0`); await until(`WC.target?.kind==='po'`,'po before resize'); await sleep(500);
    out.autoEditor=await ev(ED);
    out.endBefore=await ev(atEnd);
    let bar=await barAt(); out.barShown=bar.shown;
    const h0=out.autoEditor;
    await mouse('mousePressed',bar.x,bar.y);
    for(let k=1;k<=8;k++){await mouse('mouseMoved',bar.x,bar.y-(400-h0)*k/8);await sleep(30);}
    await mouse('mouseReleased',bar.x,bar.y-(400-h0));
    await sleep(500);
    out.dragged={editor:await ev(ED),stored:await ev(`localStorage.getItem('cd-compose-h')`),end:await ev(atEnd),
      box:await ev(`document.getElementById('workspace-compose').getBoundingClientRect().height`)};
    await send('Page.reload',{},s);
    await until(`!!${PO}?.contentDocument?.getElementById('msgs') && ${BOX}?.contentDocument?.getElementById('send') && !!${THR}?.contentDocument?.getElementById('thread-reply')`,'reload after resize');
    await sleep(900);
    out.reloaded=await ev(ED);
    await ev(WORKING); await sleep(300);
    await ev(`(()=>{const f=${BOX};f.contentDocument.getElementById('input').focus();return 0})()`);
    await ev(RECORD);
    for(let i=0;i<4;i++){
      await ev(`(()=>{const f=${BOX};f.contentDocument.getElementById('input').focus();${i%2===0?pickThread:pickPo};return 0})()`);
      await until(`WC.target?.kind===${JSON.stringify(i%2===0?'thread':'po')}`,'sized switch '+i); await sleep(700);
    }
    out.sizedSwitch=await ev(STOP);
    out.sizedAfterSwitch=await ev(ED);
    // Send works on the first click with the box set by hand.
    out.firstClick=await ev(`(()=>{const f=${BOX},d=f.contentDocument,i=d.getElementById('input');i.focus();i.value='Probe after resize';i.dispatchEvent(new Event('input',{bubbles:true}));
      const w=f.contentWindow;window.__sent=[];w.__old=w.postOk;w.postOk=async(path,b)=>{if(path==='/api/room/resume')window.__sent.push(b);return {}};
      const r=f.getBoundingClientRect(),b=d.getElementById('send').getBoundingClientRect();return {x:r.left+b.left+b.width/2,y:r.top+b.top+b.height/2}})()`);
    await mouse('mousePressed',out.firstClick.x,out.firstClick.y); await mouse('mouseReleased',out.firstClick.x,out.firstClick.y);
    await sleep(600);
    out.sent=await ev(`(()=>{const w=${BOX}.contentWindow;w.postOk=w.__old;return window.__sent.map(b=>({room:b.roomId,thread:b.thread||'',text:b.text}))})()`);
    bar=await barAt();
    await mouse('mousePressed',bar.x,bar.y,1); await mouse('mouseReleased',bar.x,bar.y,1);
    await mouse('mousePressed',bar.x,bar.y,2); await mouse('mouseReleased',bar.x,bar.y,2);
    await sleep(500);
    out.reset={editor:await ev(ED),stored:await ev(`localStorage.getItem('cd-compose-h')`)};
    // The keys: End to the most, Home to one row, then automatic again.
    await ev(`${BOX}.contentDocument.getElementById('compose-size').focus();0`);
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:'ArrowUp',code:'ArrowUp',windowsVirtualKeyCode:38},s);
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:'ArrowUp',code:'ArrowUp',windowsVirtualKeyCode:38},s);
    await sleep(300); out.keyUp=await ev(ED);
    await ev(`${BOX}.contentWindow.composeHeightSet(0);0`); await sleep(300);
    // ---- The strip follows the panels ----
    await ev(`${pickPo};0`); await until(`WC.target?.kind==='po'`,'po again'); await sleep(600);
    out.stripsPo=await ev(STRIPS);
    await ev(`PD.dock.toggleMin('po-chat');0`); await sleep(700);
    out.stripsChatHidden=await ev(STRIPS);
    await snap('strip-chat-minimised-1728x1117');
    await ev(`PD.dock.toggleMin('po-chat');0`); await sleep(700);
    out.stripsChatBack=await ev(STRIPS);
    await ev(`PD.dock.setViewMode('thread:po','window');0`);
    await until(`!!PD.dock.popWindow('thread:po')`,'thread window');
    let pop;
    for(let i=0;i<40&&!pop;i++){const targets=(await send('Target.getTargets')).targetInfos;
      pop=targets.find(x=>x.type==='page'&&x.targetId!==targetId&&x.url.startsWith(A.base));if(!pop)await sleep(100);}
    if(!pop)throw Error('no thread window');
    const {sessionId:ps}=await send('Target.attachToTarget',{targetId:pop.targetId,flatten:true});
    await until(`!!document.querySelector('iframe.po-thread')?.contentDocument?.getElementById('compose')`,'popped thread',ps);
    await evIn(WORKING,ps); await ev(WORKING); await sleep(700);
    out.stripsPopped=await evIn(STRIPS,ps);
    out.barPopped=await evIn(`!document.querySelector('iframe.po-thread').contentDocument.getElementById('compose-size').hidden`,ps);
    out.stripsMainWhilePopped=await ev(STRIPS);
    await ev(`PD.dock.setViewMode('thread:po','pinned');0`);
    await until(`!!${THR}?.contentDocument?.getElementById('thread-reply')`,'thread docked');
    // ---- Phone: the thread over the chat ----
    await size(390,844); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow() && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'phone chat');
    await ev(`(()=>{const d=${PO}.contentDocument;if(d.getElementById('thr-side').hidden)d.querySelector('#msgs .thr-open[data-thr]').click();return 0})()`);
    await until(`!!${PO}.contentDocument.getElementById('ts-frame')?.contentDocument?.querySelector('#compose')`,'phone thread');
    await ev(WORKING); await sleep(700);
    out.stripsPhone=await ev(STRIPS);
    out.barPhone=await ev(`!${PO}.contentDocument.getElementById('ts-frame').contentDocument.getElementById('compose-size').hidden`);
    await snap('strip-phone-390x844');
    await ev(`${PO}.contentDocument.getElementById('ts-back').click();0`); await sleep(700);
    out.stripsPhoneChat=await ev(STRIPS);
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


def summarize(rec):
    """What changed frame to frame over a recording of the Send box."""
    frames = rec['frames']
    first = frames[0]
    return {
        'frames': len(frames),
        'hidden': sum(1 for f in frames if f['hidden']),
        'noEditor': sum(1 for f in frames if f['compose'] in ('', 'none') or not f['ch']),
        'remounts': sum(1 for f in frames if not f['doc']) + sum(1 for f in frames if f['doc'] and not f['input']),
        'maxMove': max(abs(f['top'] - first['top']) for f in frames),
        'heights': sorted({f['h'] for f in frames}),
        'focusLost': sum(1 for f in frames if not f['focus']),
        'strips': sorted({f['strips'] for f in frames}),
        'mutations': rec['muts'],
    }


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ActivityStrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-activity-strip-', ignore_cleanup_errors=True)
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
        sid = 'strip-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        transcript = base / 'transcripts' / 'C--strip'; transcript.mkdir()
        at = time.time() - 3600
        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','A conversation with a thread.',1), turn('assistant','The thread reply.',2),
                 turn('user','A new topic without replies.',3)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj,'po':cls.po,'shots':os.environ.get('STRIP_SCREENSHOT_DIR','')}
        script = base / 'activity_strip_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=170)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('STRIP_DUMP'):
            Path(os.environ['STRIP_DUMP']).write_text(json.dumps(cls.got, indent=1), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_switches_keep_the_box_in_place(self):
        for key in ('switch', 'reply'):
            with self.subTest(key=key):
                m = summarize(self.got[key])
                self.assertEqual(m['hidden'], 0, m)
                self.assertEqual(m['noEditor'], 0, m)
                self.assertEqual(m['remounts'], 0, m)
                self.assertLessEqual(m['maxMove'], 2, m)
                self.assertEqual(m['focusLost'], 0, m)
                self.assertEqual(m['strips'], [1], m)
        targets = [x['to'] for x in self.got['switches']]
        self.assertEqual(targets[0::2], ['A conversation with a thread.'] * 5)
        self.assertEqual(targets[1::2], ['Motors PO'] * 5)

    def test_one_strip_per_window(self):
        g = self.got
        self.assertEqual(len(g['stripsDesktop']), 1, g['stripsDesktop'])
        for i, x in enumerate(g['switches']):
            with self.subTest(switch=i):
                want = 'po-thread' if i % 2 == 0 else 'po-session'
                self.assertEqual(len(x['strips']), 1, x)
                self.assertIn(want, x['strips'][0])
        self.assertEqual(len(g['replyStrips']), 1)
        self.assertIn('po-thread', g['replyStrips'][0])
        self.assertEqual(len(g['stripsPo']), 1); self.assertIn('po-session', g['stripsPo'][0])
        self.assertEqual(len(g['stripsChatHidden']), 1); self.assertIn('po-thread', g['stripsChatHidden'][0])
        self.assertEqual(len(g['stripsChatBack']), 1); self.assertIn('po-session', g['stripsChatBack'][0])
        self.assertEqual(len(g['stripsPopped']), 1, g['stripsPopped'])
        self.assertEqual(len(g['stripsMainWhilePopped']), 1, g['stripsMainWhilePopped'])
        self.assertEqual(len(g['stripsPhone']), 1, g['stripsPhone'])
        self.assertEqual(len(g['stripsPhoneChat']), 1, g['stripsPhoneChat'])

    def test_height_by_hand(self):
        g = self.got
        self.assertTrue(g['barShown'])
        self.assertTrue(g['barPopped'])
        self.assertFalse(g['barPhone'])
        self.assertAlmostEqual(g['dragged']['editor'], 400, delta=2, msg=g['dragged'])
        self.assertEqual(g['dragged']['stored'], str(round(g['dragged']['editor'])))
        self.assertTrue(g['endBefore'])
        self.assertTrue(g['dragged']['end'], g['dragged'])
        self.assertAlmostEqual(g['reloaded'], 400, delta=2)
        self.assertAlmostEqual(g['sizedAfterSwitch'], 400, delta=2)
        m = summarize(g['sizedSwitch'])
        self.assertEqual((m['hidden'], m['noEditor'], m['remounts'], m['focusLost']), (0, 0, 0, 0), m)
        self.assertLessEqual(m['maxMove'], 2, m)
        self.assertEqual(m['strips'], [1], m)
        self.assertEqual(g['sent'], [{'room':self.po, 'thread':'', 'text':'Probe after resize'}])
        self.assertIsNone(g['reset']['stored'])
        self.assertAlmostEqual(g['reset']['editor'], g['autoEditor'], delta=2)
        self.assertAlmostEqual(g['keyUp'], g['autoEditor'] + 16, delta=2)


if __name__ == '__main__':
    unittest.main()
