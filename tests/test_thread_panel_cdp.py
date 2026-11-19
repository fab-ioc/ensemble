"""The PO chat fills its Dock panel; a thread opens in place over it (#393, was a Dock panel beside it, #236),
or as a tab of its own."""
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
  const evIn=async (sid, expression) => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sid); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const ev=e => evIn(s, e);
  const untilIn=async (sid, expression, what) => { for(let i=0;i<120;i++){ try {if(await evIn(sid, expression)) return;}catch(e){} await sleep(150); } throw Error('timeout '+(what||expression)); };
  const until=(e, what) => untilIn(s, e, what);
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  // The thread shown in place over the PO chat (#393): PD.thrIn's page.
  const THR = `PD.thrIn?.el.querySelector('iframe')`;
  // Widths: the PO chat's Dock panel (its body), the chat frame, its balloon
  // column and message box; the thread shown in place over it; what is left
  // empty beside the balloons inside the chat's panel.
  const measure = `(() => {
    const b=e=>{if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,r:r.right};};
    const f=${PO}, d=f?.contentDocument, body=PD.els['po-chat'].closest('.dk-body');
    const pb=b(body), fb=b(f), lb=b(d?.querySelector('#msgs')), cb=b(d?.querySelector('#compose')), hb=b(PD.els['po-chat'].querySelector('.po-head'));
    const tf=${THR}, shown=!!PD.thrIn&&pdThreadInShown()&&!PD.thrIn.el.hidden, tb=shown?b(PD.thrIn.el):null, kb=b(PD.els['po-chat']);
    const bal=[...(d?.querySelectorAll('#msgs .msg[data-mid]')||[])].map(b).filter(x=>x&&x.w);
    const balL=bal.length?Math.min(...bal.map(x=>x.x)):null, balR=bal.length?Math.max(...bal.map(x=>x.r)):null;
    const loc=(()=>{try{return JSON.parse(f.contentWindow.localStorage.getItem('cd-thread-open:'+f.dataset.room)||'null');}catch(e){return 'err';}})();
    // What is on top in the middle of the PO chat's panel.
    const top=kb?document.elementFromPoint(kb.x+kb.w/2,kb.y+kb.h/2):null;
    return {panel:pb, frame:fb, list:lb, box:cb, head:hb, thread:tb,
      chatShare:pb&&fb?fb.w/pb.w:null, listShare:pb&&lb?lb.w/pb.w:null, boxShare:pb&&cb?cb.w/pb.w:null, headShare:pb&&hb?hb.w/pb.w:null,
      emptyLeft:pb&&balL!=null?balL+(fb?.x||0)-pb.x:null, emptyRight:pb&&balR!=null?pb.r-(fb?.x||0)-balR:null,
      cover:tb&&kb?{dx:Math.abs(tb.x-kb.x),dy:Math.abs(tb.y-kb.y),dw:Math.abs(tb.w-kb.w),dh:Math.abs(tb.h-kb.h)}:null,
      onTop:top===tf?'thread':top===f?'chat':(top?top.tagName+'.'+top.className:null),
      threadSrc:tf?.getAttribute('src')||'', inPlace:shown, inChat:!!PD.thrIn&&PD.els['po-chat'].contains(PD.thrIn.el),
      threadPanels:PD.dock.panels().map(p=>p.id).filter(id=>/^thread:/.test(id)),
      threadTitle:PD.thrIn?.title||null, barThread:document.getElementById('thr-switch-name')?.textContent||'',
      inFrame:!!d && !d.getElementById('thr-side').hidden, layer:!!d?.getElementById('thr-side').classList.contains('layer'),
      kept:loc};
  })()`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`, 'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat');
    await sleep(500);
    for (const w of [1440, 1728, 2000]) { await size(w,1117); await sleep(400); out['none'+w]=await ev(measure); }
    await size(1728,1117); await sleep(300);
    // A thread opened from its reply line (a click): in place, over the chat.
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread in place');
    await sleep(500); out.open1728=await ev(measure);
    await ev(`window.__thrEl = PD.thrIn.el; 0`);
    for (const w of [1440, 2000]) { await size(w,1117); await sleep(400); out['open'+w]=await ev(measure); }
    await size(1728,1117); await sleep(300);
    // Another thread: the same place, the same element.
    const tids=await ev(`[...${PO}.contentDocument.querySelectorAll('#msgs .thr-open[data-thr]')].map(b=>b.dataset.thr)`);
    const t2=tids[tids.length-1];
    if (t2 === tids[0]) throw Error('one thread only: ' + JSON.stringify(tids));
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr="${t2}"]').click(); 0`);
    await until(`(${THR}?.getAttribute('src')||'').includes(encodeURIComponent(${JSON.stringify(t2)})) && !!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'second thread');
    await sleep(500); out.second=await ev(measure);
    out.second.sameEl = await ev(`PD.thrIn?.el === window.__thrEl`);
    // A reload brings it back, in place.
    await ev('window.__before = 1; 0');
    await send('Page.reload',{},s);
    await until(`!window.__before && !!PD.dock && !!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread after reload');
    await sleep(600); out.reload=await ev(measure);
    // Closed (the thread's Back, thread ▾ › Main): the chat is shown again.
    await ev(`pdThreadInClose(false); 0`);
    await sleep(500); out.closed=await ev(measure);
    await ev('window.__before = 1; 0');
    await send('Page.reload',{},s);
    await until(`!window.__before && !!PD.dock && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'chat after reload');
    await sleep(1200); out.closedReload=await ev(measure);
    // A task chat in the middle fills it too.
    await ev(`openDetail(ALL_ROWS.find(r=>r.roomId===${JSON.stringify(A.task)}).sessionId); 0`);
    await until(`!!document.querySelector('#detail-panel iframe.dp-session')?.contentDocument?.querySelector('#compose')`, 'task chat');
    await sleep(500);
    out.task=await ev(`(() => { const p=document.querySelector('#detail-panel'), f=p.querySelector('iframe.dp-session'), d=f.contentDocument;
      const w=e=>e?e.getBoundingClientRect().width:null;
      return {panel:w(p), frame:w(f), list:w(d.querySelector('#msgs')), box:w(d.querySelector('#compose')), top:w(p.querySelector('.dp-top')), tabs:w(p.querySelector(':scope > .dp-tabs'))}; })()`);
    await ev('closeDetail(); 0'); await sleep(400);
    const PANELS = `PD.dock.panels().map(p=>p.id).filter(id=>/^thread:/.test(id))`;
    const IN = `({threadPanels:${PANELS}, inPlace:!!PD.thrIn&&pdThreadInShown(), tid:PD.thrIn?.tid||''})`;
    const LOADED = `!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`;
    const known = new Set([targetId]);
    const newTarget = async what => { for(let i=0;i<40;i++) { const t=await send('Target.getTargets'); const n=t.targetInfos.find(x=>x.type==='page'&&!known.has(x.targetId)&&x.url.startsWith(A.base)); if(n){ known.add(n.targetId); return n.targetId; } await sleep(100); } throw Error('no window: '+what); };
    // A thread open, then another PO shown: its thread goes; that PO again, it comes back.
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(LOADED, 'thread before the switch');
    const src0 = (await ev(`${THR}.getAttribute('src')`)).split('&msg=')[0];
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj2)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`${PO}?.dataset.room===${JSON.stringify(A.po2)}`, 'the other PO');
    await sleep(800);
    out.otherPo = await ev(IN);
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`${PO}?.dataset.room===${JSON.stringify(A.po)} && ${LOADED} && pdThreadInShown()`, 'the thread back with its PO');
    out.backPo = await ev(`({...${IN}, src:${THR}.getAttribute('src').split('&msg=')[0]})`);
    out.backPo.same = out.backPo.src === src0;
    // A thread as a tab of its own (a Ctrl/⌘-click), in a window of its own, and back.
    const tabId = await ev(`(() => { pdThreadTab({room:${JSON.stringify(A.po)}, tid:${JSON.stringify(t2)}, mid:'', title:'Second'}); return pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(t2)}, ''); })()`);
    const TAB = `PD.rt.get(${JSON.stringify(tabId)})?.el.querySelector('iframe')`;
    await until(`!!${TAB}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread tab');
    out.threadTab = await ev(`({...${IN}, id:${JSON.stringify(tabId)}})`);
    await ev(`PD.dock.setViewMode(${JSON.stringify(tabId)},'window'); 0`);
    await until(`!!PD.dock.popWindow(${JSON.stringify(tabId)})`, 'thread window');
    const tw = await newTarget('thread');
    const {sessionId:tws}=await send('Target.attachToTarget',{targetId:tw,flatten:true});
    await untilIn(tws, `!!document.querySelector('iframe.po-thread')?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread in its window');
    out.threadWindow = await evIn(tws, `({loaded:true, title:document.title})`);
    await ev(`PD.dock.setViewMode(${JSON.stringify(tabId)},'pinned'); 0`);
    await until(`!PD.dock.popWindow(${JSON.stringify(tabId)}) && !!${TAB}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread tab docked back');
    out.threadDocked = await ev(IN);
    await ev(`PD.dock.removePanel(${JSON.stringify(tabId)}); PD.dock.activate('po-chat'); 0`);
    await sleep(400);
    // A popped-out PO chat window: its thread opens in that window's chat, and
    // the one in place goes (one thread on screen); docked back, it returns.
    await ev(`PD.dock.setViewMode('po-chat','window'); 0`);
    await until(`!!PD.dock.popWindow('po-chat')`, 'pop-out');
    const popId = await newTarget('chat');
    const {sessionId:ps}=await send('Target.attachToTarget',{targetId:popId,flatten:true});
    await send('Emulation.setDeviceMetricsOverride',{width:1200,height:900,deviceScaleFactor:1,mobile:false},ps);
    const OWN = `document.querySelector('iframe.pd-own')`;
    await untilIn(ps, `!!${OWN}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'popped chat');
    await evIn(ps, `${OWN}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await untilIn(ps, `!!${OWN}.contentDocument.getElementById('ts-frame')?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'popped thread');
    await sleep(400);
    out.popped=await evIn(ps, `(() => { const d=${OWN}.contentDocument, side=d.getElementById('thr-side'), f=${OWN};
      return {inFrame:!side.hidden, side:side.getBoundingClientRect().width, chat:f.getBoundingClientRect().width, layer:side.classList.contains('layer')}; })()`);
    out.poppedMain=await ev(`({...${IN}, thrIn:!!PD.thrIn})`);
    await ev(`PD.dock.setViewMode('po-chat','pinned'); 0`);
    await until(`!PD.dock.popWindow('po-chat') && ${LOADED} && pdThreadInShown()`, 'thread back in place after the chat docks');
    out.afterPopIn=await ev(`({...${IN}, inFrame:!${PO}.contentDocument.getElementById('thr-side').hidden})`);
    await sleep(600);
    // A phone: the thread over the chat, with Back (#234), not in place in the dashboard.
    await size(390,844,true); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow() && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'phone chat');
    await sleep(600);
    await ev(`(() => { const d=${PO}.contentDocument; const s=d.getElementById('thr-side'); if (s.hidden) d.querySelector('#msgs .thr-open[data-thr]').click(); return 0; })()`);
    await until(`!!${PO}.contentDocument.getElementById('ts-frame')?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'phone thread');
    await sleep(400);
    out.phone=await ev(`(() => { const d=${PO}.contentDocument, side=d.getElementById('thr-side'), r=side.getBoundingClientRect();
      return {inFrame:!side.hidden, layer:side.classList.contains('layer'), w:r.width, back:getComputedStyle(d.getElementById('ts-back')).display !== 'none',
        threadPanels:PD.dock.panels().map(p=>p.id).filter(id=>/^thread:/.test(id)), inPlace:!!PD.thrIn&&pdThreadInShown()}; })()`);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-thread-panel-', ignore_cleanup_errors=True)
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
        po_sid = 'panel-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':po_sid,'cwd':str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        ok, proj2, _ = dashboard.register_project('Garden'); assert ok, proj2
        cls.proj2 = proj2['id']
        po2 = chatroom.create_room('Garden PO', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':'panel-po2','cwd':str(Path(proj2.get('home') or proj2['path']))}])
        chatroom.post_message(po2['id'], 'user', 'Another PO')
        dashboard.assign_session_project(po2['id'], cls.proj2)
        dashboard.set_project_po(cls.proj2, po2['id'])
        cls.po2 = po2['id']
        transcript = base / 'transcripts' / 'C--po'
        transcript.mkdir()
        at = time.time() - 3600
        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':po_sid,'message':msg}
        lines = [turn('user', 'We need the chat to use the whole width of its panel.', 1)]
        lines += [turn('assistant', f'Answer {i}: ' + 'A long line of conversation that wraps across the balloon. ' * 6, i + 2) for i in range(6)]
        lines += [turn('user', 'And the thread opens beside it.', 20)]
        lines += [turn('assistant', f'Reply {i}: ' + 'Another line of the second thread. ' * 6, i + 21) for i in range(6)]
        (transcript / f'{po_sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        # Both sent as new threads (#367: only the person starts one).
        for text, offset in (('We need the chat to use the whole width of its panel.', 1), ('And the thread opens beside it.', 20)):
            threads.note_send(cls.po, '', '', [], False, text, now=at + offset, new=True)
        task = chatroom.create_room('Wide chat', [{'identity':'claude','agent':'claude','cwd':str(home)}])
        chatroom.post_message(task['id'], 'user', 'A message above the composer')
        dashboard.assign_session_project(task['id'], cls.proj)
        cls.task = task['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj': cls.proj, 'task': cls.task, 'po': cls.po, 'proj2': cls.proj2, 'po2': cls.po2}
        script = base / 'thread_panel_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nthread panel CDP: ' + json.dumps({k: {x: (round(y, 3) if isinstance(y, float) else y) for x, y in v.items()
                                                       if not isinstance(y, dict) or x in ('thread', 'cover')} for k, v in cls.got.items()}, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_chat_fills_its_panel(self):
        for name in ('none1440', 'none1728', 'none2000', 'closed', 'closedReload'):
            g = self.got[name]
            with self.subTest(name=name):
                self.assertFalse(g['thread'], g)
                for k in ('chatShare', 'listShare', 'boxShare', 'headShare'):
                    self.assertGreaterEqual(g[k], .9, (k, g))

    def test_thread_shows_in_place_over_the_chat(self):
        # #393: no panel beside the chat; the thread covers the PO chat's panel.
        for name in ('open1440', 'open1728', 'open2000', 'second', 'reload'):
            g = self.got[name]
            with self.subTest(name=name):
                self.assertEqual(g['threadPanels'], [], g)
                self.assertTrue(g['inPlace'] and g['inChat'], g)
                self.assertFalse(g['inFrame'], 'the chat shows no thread of its own')
                self.assertEqual(g['onTop'], 'thread', g)
                for k, v in g['cover'].items():
                    self.assertLessEqual(v, 2, (k, g))
                self.assertGreaterEqual(g['chatShare'], .9, g)

    def test_another_thread_reuses_the_place_and_names_it(self):
        g, first = self.got['second'], self.got['open1728']
        self.assertNotEqual(g['threadSrc'], first['threadSrc'])
        self.assertTrue(g['sameEl'], 'the same element in place, its page moved')
        self.assertTrue(first['threadTitle'] and first['threadTitle'] != 'Thread', first)
        self.assertEqual(first['barThread'], first['threadTitle'], "thread ▾ names it")
        self.assertEqual(g['barThread'], g['threadTitle'])

    def test_reload_keeps_it_in_place(self):
        g, before = self.got['reload'], self.got['second']
        self.assertEqual(g['threadSrc'].split('&msg=')[0], before['threadSrc'].split('&msg=')[0])
        self.assertTrue(g['inPlace'], g)

    def test_closing_shows_the_chat_again(self):
        for name in ('closed', 'closedReload'):
            g = self.got[name]
            with self.subTest(name=name):
                self.assertEqual(g['threadPanels'], [], g)
                self.assertFalse(g['inPlace'] or g['inChat'], g)
                self.assertFalse(g['inFrame'], g)
                self.assertIsNone(g['kept'], g)
                self.assertEqual(g['onTop'], 'chat', g)
                self.assertEqual(g['barThread'], 'Main', g)

    def test_task_chat_fills_the_middle(self):
        g = self.got['task']
        for k in ('frame', 'list', 'box', 'top') + (('tabs',) if g['tabs'] else ()):
            self.assertGreaterEqual(g[k] / g['panel'], .9, (k, g))

    def test_popped_out_chat_keeps_its_thread_inside(self):
        self.assertTrue(self.got['popped']['inFrame'], self.got['popped'])
        g = self.got['poppedMain']
        self.assertEqual((g['threadPanels'], g['inPlace'], g['thrIn']), ([], False, False), g)

    def test_another_po_takes_the_thread_away_and_gives_it_back(self):
        g = self.got['otherPo']
        self.assertEqual((g['threadPanels'], g['inPlace']), ([], False), g)
        g = self.got['backPo']
        self.assertEqual(g['threadPanels'], [], g)
        self.assertTrue(g['inPlace'], g)
        self.assertTrue(g['same'], g)

    def test_thread_tab_pops_out_and_back(self):
        tab = self.got['threadTab']
        self.assertEqual(tab['threadPanels'], [tab['id']], tab)
        self.assertTrue(tab['id'].startswith('thread:' + self.po + ':'), tab)
        self.assertTrue(self.got['threadWindow']['loaded'], self.got['threadWindow'])
        self.assertEqual(self.got['threadDocked']['threadPanels'], [tab['id']])

    def test_popped_chat_takes_the_thread_and_gives_it_back(self):
        g = self.got['afterPopIn']
        self.assertEqual(g['threadPanels'], [], g)
        self.assertTrue(g['inPlace'], g)
        self.assertFalse(g['inFrame'], g)

    def test_phone_keeps_the_thread_over_the_chat(self):
        g = self.got['phone']
        self.assertTrue(g['inFrame'] and g['layer'] and g['back'], g)
        self.assertEqual(g['threadPanels'], [], g)
        self.assertFalse(g['inPlace'], g)


if __name__ == '__main__':
    unittest.main()
