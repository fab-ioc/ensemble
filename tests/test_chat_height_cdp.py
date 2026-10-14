"""Chat and tool panels use their full height, including after Dock changes size."""
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
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--disable-popup-blocking', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((res,rej) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited '+c))); setTimeout(() => rej(new Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((res,rej) => { c.onopen=res; c.onerror=rej; });
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((res,rej) => { const id=++seq; waits.set(id,[res,rej]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  const size=(w,h,touch=false) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:touch},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async expression => { for(let i=0;i<100;i++){ try {if(await ev(expression)) return;}catch(e){} await sleep(150); } throw Error('timeout '+expression); };
  const measure = `(() => {
    const b=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom};};
    const rid=${JSON.stringify(A.task)};
    const panel=PD.rt.get('chat:'+rid)?.el || null;
    const host=PD.els['po-chat'];
    const frame=panel?.querySelector('iframe.dp-session') || (document.body.classList.contains('detail-open') && host.querySelector('#detail-panel iframe.dp-session')) || host.querySelector('#po-panel iframe.po-session:not([hidden])');
    const d=frame?.contentDocument, list=d?.querySelector('#msgs'), compose=d?.querySelector('#compose');
    const parent=panel || (frame?.closest('#detail-panel, #po-panel'));
    const pb=b(parent), fb=b(frame), lb=b(list), cb=b(compose);
    return {panel:pb,frame:fb,list:lb,box:cb,body:b(d?.body),main:b(d?.querySelector('main')),
      listHeight:lb?.h, bottomGap:cb&&fb?fb.h-cb.b:null, emptyBelow:cb&&pb?pb.b-(fb?.y||0)-cb.b:null,
      scrollRemaining:list?Math.max(0,list.scrollHeight-list.clientHeight-list.scrollTop):null,
      ratio:lb&&pb?lb.h/pb.h:null, bodyHeight:d?.body?.getBoundingClientRect().height,
      frameClientHeight:frame?.clientHeight, mode:panel?PD.dock.viewMode('chat:'+rid):'middle',
      frameInnerHeight:frame?.contentWindow?.innerHeight, frameVisualHeight:frame?.contentWindow?.visualViewport?.height,
      activityFlex:host.querySelector('#detail-panel .dp-pane[data-pane="activity"]') && getComputedStyle(host.querySelector('#detail-panel .dp-pane[data-pane="activity"]')).flexGrow,
      parentStyle:parent?getComputedStyle(parent).height:null};
  })()`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.task)})`);
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`document.body.classList.contains('po-dock') && !!PD.dock`);
    await ev(`pdChatOpen(${JSON.stringify(A.task)}); 0`);
    await until(`!!PD.rt.get('chat:'+${JSON.stringify(A.task)})?.el.querySelector('iframe')?.contentDocument?.querySelector('#compose')`);
    await sleep(500);
    out.dockedLaptop=await ev(measure);
    await size(1728,2000); await sleep(400); out.dockedTall=await ev(measure);
    await send('Page.reload',{},s);
    await until(`!!PD.rt.get('chat:'+${JSON.stringify(A.task)})?.el.querySelector('iframe')?.contentDocument?.querySelector('#compose')`);
    await sleep(500); out.dockedReload=await ev(measure);
    await ev(`PD.dock.toggleMax('chat:'+${JSON.stringify(A.task)}); 0`);
    await sleep(300); out.maximised=await ev(measure);
    await ev(`PD.dock.restoreMax(); 0`);
    await sleep(300); out.maxRestored=await ev(measure);
    await size(1728,900); await sleep(400); out.dockedShort=await ev(measure);
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'window'); 0`);
    await until(`!!PD.dock.popWindow('chat:'+${JSON.stringify(A.task)})`);
    let popId;
    for(let i=0;i<30;i++) { const t=await send('Target.getTargets'); popId=t.targetInfos.find(x=>x.type==='page'&&x.targetId!==targetId&&x.url.startsWith(A.base))?.targetId; if(popId)break; await sleep(100); }
    if(!popId) throw Error('popup target absent');
    const {sessionId:ps}=await send('Target.attachToTarget',{targetId:popId,flatten:true});
    await send('Emulation.setDeviceMetricsOverride',{width:625,height:2000,deviceScaleFactor:1,mobile:false},ps);
    await sleep(600); out.popTall=await ev(measure);
    await send('Emulation.setPageScaleFactor',{pageScaleFactor:5},ps);
    await sleep(300); out.popScaled=await ev(measure);
    await send('Emulation.setPageScaleFactor',{pageScaleFactor:1},ps);
    await send('Emulation.setDeviceMetricsOverride',{width:625,height:900,deviceScaleFactor:1,mobile:false},ps);
    await sleep(400); out.popShort=await ev(measure);
    await send('Emulation.setDeviceMetricsOverride',{width:625,height:2000,deviceScaleFactor:1,mobile:false},ps);
    await send('Page.reload',{},s);
    await until(`!!PD.dock && !!PD.rt.get('chat:'+${JSON.stringify(A.task)})?.el.querySelector('iframe')?.contentDocument?.querySelector('#compose')`);
    await sleep(600); out.popReload=await ev(measure);
    await ev(`PD.dock.setViewMode('chat:'+${JSON.stringify(A.task)},'pinned'); 0`);
    await sleep(500); out.restored=await ev(measure);
    await ev(`(() => { const id='chat:'+${JSON.stringify(A.task)}; const find=n=>n?.t==='stack'&&n.panels.includes('po-chat')?n:(n?.kids||[]).map(find).find(Boolean);
      PD.dock.moveTo(id,{kind:'stack',stack:find(PD.dock.layout().root)},'center'); PD.dock.activate('po-chat'); return 0; })()`);
    await size(1728,2000); await sleep(300);
    await ev(`PD.dock.activate('chat:'+${JSON.stringify(A.task)}); 0`);
    await sleep(400); out.backTab=await ev(measure);
    await ev(`(() => { const id='chat:'+${JSON.stringify(A.task)}; const find=n=>n?.t==='stack'&&n.panels.includes('po-chat')?n:(n?.kids||[]).map(find).find(Boolean);
      PD.dock.moveTo(id,{kind:'stack',stack:find(PD.dock.layout().root)},'right'); PD.dock.toggleMin(id); return 0; })()`);
    await size(1728,900); await sleep(300);
    await size(1728,2000); await sleep(300);
    await ev(`PD.dock.toggleMin('chat:'+${JSON.stringify(A.task)}); 0`);
    await sleep(400); out.minRestored=await ev(measure);
    await size(390,844,true); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await sleep(600); out.phone=await ev(measure);
    await ev(`PD.dock.removePanel('chat:'+${JSON.stringify(A.task)}); 0`);
    await ev(`openDetail(ALL_ROWS.find(r=>r.roomId===${JSON.stringify(A.task)}).sessionId); 0`);
    await until(`!!PD.els['po-chat'].querySelector('#detail-panel iframe.dp-session')?.contentDocument?.querySelector('#compose')`);
    await sleep(400); out.phoneMiddle=await ev(measure);
    await ev('closeDetail(); 0'); await sleep(300); out.phonePo=await ev(measure);
    await size(1728,1117); await send('Emulation.setTouchEmulationEnabled',{enabled:false},s);
    await ev(`openDetail(ALL_ROWS.find(r=>r.roomId===${JSON.stringify(A.task)}).sessionId); 0`);
    await until(`!!document.querySelector('#detail-panel iframe.dp-session')?.contentDocument?.querySelector('#compose')`);
    await sleep(500); out.middleLaptop=await ev(measure);
    await size(1728,2000); await sleep(500); out.middleTall=await ev(measure);
    // A stale value from an earlier layout must not determine the live chat's height.
    // This reproduces the 200px chat on a tall panel even if resize timing varies.
    await ev(`document.querySelector('#detail-panel').style.setProperty('--dp-fill','220px'); 0`);
    await sleep(100); out.middleStaleTall=await ev(measure);
    await size(1728,1117); await sleep(300);
    await ev(`PD.dock.setViewMode('po-chat','window'); 0`);
    await until(`!!PD.dock.popWindow('po-chat')`);
    let mainPop;
    for(let i=0;i<30;i++) { const t=await send('Target.getTargets'); mainPop=t.targetInfos.find(x=>x.type==='page'&&x.targetId!==targetId&&x.targetId!==popId&&x.url.startsWith(A.base))?.targetId; if(mainPop)break; await sleep(100); }
    if(!mainPop) throw Error('main popup target absent');
    const {sessionId:ms}=await send('Target.attachToTarget',{targetId:mainPop,flatten:true});
    await send('Emulation.setDeviceMetricsOverride',{width:625,height:2000,deviceScaleFactor:1,mobile:false},ms);
    await sleep(500); out.middlePopTall=await ev(measure);
    await ev(`PD.els['po-chat'].querySelector('#detail-panel').style.setProperty('--dp-fill','220px'); 0`);
    await sleep(100); out.middlePopStaleTall=await ev(measure);
    await ev(`PD.dock.setViewMode('po-chat','pinned'); 0`);
    await size(1728,2000); await sleep(400); out.middlePopRestore=await ev(measure);
    out.tools={};
    for(const id of ['spec','changes','points','board','workspace']) {
      await ev(`PD.dock.setViewMode(${JSON.stringify(id)},'pinned'); PD.dock.reveal(${JSON.stringify(id)}); 0`);
      await sleep(250);
      out.tools[id]=await ev(`(() => { const e=PD.els[${JSON.stringify(id)}]; const r=e.getBoundingClientRect();
        const body=e.closest('.dk-body')?.getBoundingClientRect(); return {panelHeight:r.height,hostHeight:body?.height,
          bottomGap:body?body.bottom-r.bottom:null, scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}; })()`);
    }
    await ev(`(() => { const f=[...PD.els.workspace.querySelectorAll('.wse.file[data-path]')].find(e=>e.dataset.path.endsWith('README.md')); if(!f)throw Error('fixture file absent'); f.click(); return 0; })()`);
    await until(`[...PD.rt.values()].some(e=>e.kind==='file')`);
    await sleep(300);
    out.tools.file=await ev(`(() => { const e=[...PD.rt.values()].find(x=>x.kind==='file').el;
      const r=e.getBoundingClientRect(), host=e.closest('.dk-body')?.getBoundingClientRect(), viewer=e.querySelector('.wsp-frames').getBoundingClientRect();
      return {panelHeight:r.height,hostHeight:host?.height,bottomGap:host?host.bottom-r.bottom:null,
        viewerHeight:viewer.height,viewerBottomGap:r.bottom-viewer.bottom}; })()`);
    await ev('closeDetail(); 0'); await sleep(400); out.poTall=await ev(measure);
    const {browserContextId:ctx}=await send('Target.createBrowserContext');
    const {targetId:tt}=await send('Target.createTarget',{url:'about:blank',browserContextId:ctx});
    const {sessionId:ts}=await send('Target.attachToTarget',{targetId:tt,flatten:true});
    await send('Page.enable',{},ts);
    await send('Emulation.setDeviceMetricsOverride',{width:1728,height:1117,deviceScaleFactor:1,mobile:false},ts);
    const evT=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},ts); if(r.exceptionDetails)throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
    const untilT=async expression => { for(let i=0;i<100;i++){try{if(await evT(expression))return;}catch(e){}await sleep(150);}throw Error('timeout '+expression);};
    await send('Page.navigate',{url:A.base+'/'},ts);
    await untilT(`typeof ALL_ROWS!=='undefined' && !!PROJECTS`);
    await evT(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await untilT(`!!PD.dock && !!PD.els['po-chat'].querySelector('iframe.po-session')?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`);
    // A PO chat's thread opens beside its channel, in the chat's own page (#234):
    // the side column, its frame and the thread's box fill the chat's height.
    const sideMeasure = `(() => {
      const b=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom};};
      const po=PD.els['po-chat'].querySelector('iframe.po-session'), pd=po?.contentDocument;
      const side=pd?.getElementById('thr-side'), f=pd?.getElementById('ts-frame'), d=f?.contentDocument;
      const list=d?.querySelector('#msgs'), compose=d?.querySelector('#compose');
      const pb=b(po), sb=b(side), fb=b(f), lb=b(list), cb=b(compose), main=b(pd?.querySelector('main'));
      return {panel:sb, frame:fb, list:lb, box:cb, chat:pb, listHeight:lb?.h, bottomGap:cb&&fb?fb.h-cb.b:null,
        emptyBelow:cb&&sb&&fb?sb.b-fb.y-cb.b:null, sideOfMain:sb&&main?sb.h/main.h:null,
        scrollRemaining:list?Math.max(0,list.scrollHeight-list.clientHeight-list.scrollTop):null,
        ratio:lb&&sb?lb.h/sb.h:null, layer:!!side?.classList.contains('layer')};
    })()`;
    const sideReady = `!!PD.els['po-chat'].querySelector('iframe.po-session')?.contentDocument?.getElementById('ts-frame')?.contentDocument?.querySelector('#msgs .msg[data-mid]')`;
    await evT(`(() => { const t=PD.els['po-chat'].querySelector('iframe.po-session').contentDocument.querySelector('#msgs .thr-open[data-thr]').dataset.thr;
      if(!pdThreadOpen(${JSON.stringify(A.po)},t))throw Error('thread not opened'); return t; })()`);
    await untilT(sideReady);
    await sleep(400); out.threadLaptop=await evT(sideMeasure);
    await send('Emulation.setDeviceMetricsOverride',{width:1728,height:2000,deviceScaleFactor:1,mobile:false},ts);
    await sleep(300); out.threadTall=await evT(sideMeasure);
    // A reload shows it again by itself (kept per browser and chat).
    await evT('window.__chatHeightBeforeReload = 1; 0');
    await send('Page.reload',{},ts);
    await untilT(`!window.__chatHeightBeforeReload && !!PD.dock`);
    await untilT(sideReady);
    await sleep(400); out.threadReload=await evT(sideMeasure);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ChatHeight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-chat-height-', ignore_cleanup_errors=True)
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
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        po_sid = 'height-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':po_sid,'cwd':str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        transcript = base / 'transcripts' / 'C--po'
        transcript.mkdir()
        at = time.time() - 3600
        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':po_sid,'message':msg}
        lines = [turn('user', 'We need to add this chat thread and keep the conversation readable.', 1)]
        lines += [turn('assistant', f'Answer {i}: ' + 'Conversation details. ' * 12, i + 2) for i in range(30)]
        (transcript / f'{po_sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        task = chatroom.create_room('Tall chat', [{'identity':'claude','agent':'claude','cwd':str(home)}])
        chatroom.post_message(task['id'], 'user', 'A message above the composer')
        dashboard.assign_session_project(task['id'], cls.proj)
        cls.task = task['id']
        (home / 'README.md').write_text('# A file panel\n', encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj': cls.proj, 'task': cls.task, 'po': cls.po}
        script = base / 'chat_height_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=120)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nchat height CDP: '+json.dumps({name: {
            'panel': [round(g['panel']['w']), round(g['panel']['h'])] if g['panel'] else None,
            'listHeight': round(g['listHeight']) if g['listHeight'] is not None else None,
            'bottomGap': round(g['bottomGap']) if g['bottomGap'] is not None else None,
            'emptyBelow': round(g['emptyBelow']) if g['emptyBelow'] is not None else None,
            'ratio': round(g['ratio'], 3) if g['ratio'] is not None else None,
        } for name, g in cls.got.items() if name != 'tools'}, indent=2))
        print('tool panels CDP: '+json.dumps(cls.got['tools']))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_chat_fills_height(self):
        for name, g in self.got.items():
            if name == 'tools':
                continue
            with self.subTest(name=name):
                self.assertLessEqual(g['emptyBelow'], 16, g)
                self.assertGreaterEqual(g['ratio'], .7, g)
        for name in ('threadLaptop', 'threadTall', 'threadReload'):
            with self.subTest(name=name):
                g = self.got[name]
                self.assertLessEqual(g['scrollRemaining'], 16, g)
                self.assertFalse(g['layer'], 'a laptop: the thread beside the chat')
                self.assertGreaterEqual(g['sideOfMain'], .99, g)

    def test_live_chat_grows_with_flex(self):
        self.assertEqual(self.got['middleStaleTall']['activityFlex'], '1')

    def test_related_panels_fill_their_dock_body(self):
        for name, g in self.got['tools'].items():
            with self.subTest(name=name):
                self.assertLessEqual(abs(g['bottomGap']), 2, g)
                self.assertGreater(g['panelHeight'], 1000, g)


if __name__ == '__main__':
    unittest.main()
