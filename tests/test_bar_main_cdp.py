"""#396 (P403): in a thread, the bar's way back to Main.

The bar on a computer reads ENSEMBLE / project ▾ / thread ▾. In a thread the
group reads as a breadcrumb, so the project's name is the way back: one click on
it while a thread is shown shows the main conversation. thread ▾ → Main
conversation goes back too, in every way a thread is shown: picked in thread ▾,
a thread line clicked in the chat, an Inbox link (the chat's sideOpen), a task
in no project (pdMid). thread ▾ names the thread shown, never Main while one is.

In headless Chrome over CDP, against a hub in a thread: Motors (a PO whose
conversation has two threads) and a task in no project. ``ENSEMBLE_SHOTS``
keeps screenshots. Skipped without Node or Chrome.
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
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0',
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
  // The bar as one sees it, and what the conversation shows.
  const BAR = `(() => {
    const vis = e => !!e && e.getBoundingClientRect().width > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden';
    let side = null; try { side = thrBarFrame()?.contentWindow?.sideNow?.() || null; } catch (e) {}
    let sideShown = false; try { const d = thrBarFrame()?.contentDocument; sideShown = !!d && !!d.getElementById('thr-side') && !d.getElementById('thr-side').hidden; } catch (e) {}
    return { on: document.body.classList.contains('bar-tabs'),
      proj: document.getElementById('proj-switch-name').textContent,
      thr: vis(document.getElementById('thr-switch')) ? document.getElementById('thr-switch-name').textContent : null,
      projMenu: !document.getElementById('proj-menu').hidden, thrMenu: !document.getElementById('thr-menu').hidden,
      front: PD.dock ? PD.dock.frontOf('po-chat') : '', grpOn: document.getElementById('bar-grp').classList.contains('on'),
      thrIn: PD.thrIn ? { tid: PD.thrIn.tid, mid: PD.thrIn.mid, shown: pdThreadInShown() } : null,
      side: side ? side.tid || ('m:' + side.mid) : null, sideShown,
      kept: (() => { try { return localStorage.getItem('cd-thread-open:' + thrBarRoom()); } catch (e) { return 'err'; } })(),
      threadTabs: [...PD.rt].filter(([, e]) => e.kind === 'thread').map(([id]) => id),
      top: barTopTask() ? barTopTask().roomId : null, target: WC.target ? WC.target.kind + ':' + (WC.target.thread || '') : null };
  })()`;
  // A real press and release at an element of the page, or of a frame of it.
  const clickAt = async (frameExpr, selector, modifiers = 0) => {
    const p = await ev(`(()=>{const f=${frameExpr || 'null'},r=f?f.getBoundingClientRect():{x:0,y:0},d=f?f.contentDocument:document,e=d.querySelector(${JSON.stringify(selector)});
      if(!e) throw Error('no '+${JSON.stringify(selector)}); e.scrollIntoView({block:'nearest'});const q=e.getBoundingClientRect();return {x:r.x+q.x+q.width/2,y:r.y+q.y+q.height/2}})()`);
    for (const type of ['mouseMoved','mousePressed','mouseReleased'])
      await send('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1,modifiers},s);
  };
  const closeMenus = `closeProjMenu(); closeThrMenu(); 0`;
  const thrMenuOpen = async () => {
    await clickAt(null, '#thr-switch');
    await until(`!document.getElementById('thr-menu').hidden && !document.querySelector('#thr-menu .pm-none:not(:only-of-type)') && document.querySelectorAll('#thr-menu [data-thr-pick]').length >= 1 && !/Reading/.test(document.getElementById('thr-menu').textContent)`, 'thread menu');
  };
  // thread ▾ → Main conversation: what the bar and the chat say after.
  const viaMenuMain = async label => {
    await thrMenuOpen();
    await clickAt(null, '#thr-menu [data-thr-pick=""]');
    await until(`!PD.thrIn && !thrBarFrame()?.contentWindow?.sideNow?.() && document.getElementById('thr-switch-name').textContent==='Main'`, label + ': Main');
    await sleep(300);
    return ev(BAR);
  };
  const inPlace = tid => `PD.thrIn?.tid===${JSON.stringify(tid)} && pdThreadInShown() && document.getElementById('thr-switch-name').textContent!=='Main'`;
  const threadsIn = `[...${PO}.contentDocument.querySelectorAll('#msgs .thr-open[data-thr]')].map(b=>b.dataset.thr)`;
  const sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  const out = {};
  try {
    await size(1728,1117);
    const {identifier:noBootOpen} = await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.ensBootOpen = false;'},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted===true`,'booted');
    await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:noBootOpen},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)}) && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.loose)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && PD.restored && ${threadsIn}.length===2`,'PO chat with two threads');
    await sleep(500);
    const [ta, tb] = await ev(threadsIn);
    out.ta = ta; out.tb = tb;
    out.start = await ev(BAR);

    // 1. In a thread picked in thread ▾, the project's name clicked.
    await thrMenuOpen();
    await clickAt(null, `#thr-menu [data-thr-pick="${ta}"]`);
    await until(inPlace(ta), 'thread in place from thread ▾');
    await sleep(400);
    out.menuIn = await ev(BAR);
    await snap('main-thread-in-place');
    await clickAt(null, '#proj-switch-name');
    await sleep(500);
    out.nameClick = await ev(BAR);
    await snap('main-name-click');
    await ev(closeMenus);
    await sleep(200);
    // 2a. thread ▾ → Main, the thread picked in thread ▾.
    if (!out.nameClick.thrIn) { await thrMenuOpen(); await clickAt(null, `#thr-menu [data-thr-pick="${ta}"]`); await until(inPlace(ta), 'again in place'); await sleep(300); }
    out.menuMain = await viaMenuMain('thread ▾');
    // 2b. The chat's own thread line clicked (its sideOpen).
    await clickAt(PO, `#msgs .thr-open[data-thr="${tb}"]`);
    await until(inPlace(tb), 'thread in place from its line');
    await sleep(400);
    out.lineIn = await ev(BAR);
    out.lineMain = await viaMenuMain('thread line');
    // 2c. An Inbox link to the thread (pdThreadOpen → goto-msg → the chat's gotoThread → sideOpen).
    await ev(`pdThreadOpen(${JSON.stringify(A.po)}, ${JSON.stringify(ta)}, '', null, 'inbox'); 0`);
    await until(inPlace(ta), 'thread in place from an Inbox link');
    await sleep(400);
    out.inboxIn = await ev(BAR);
    out.inboxMain = await viaMenuMain('Inbox link');
    // 2d. The chat reloaded after Main: no thread comes back.
    await ev(`${PO}.contentWindow.location.reload(); 0`);
    await sleep(800);
    await until(`${PO}?.contentDocument?.readyState==='complete' && ${threadsIn}.length===2`, 'chat reloaded');
    await sleep(800);
    out.afterReload = await ev(BAR);

    // 3. A task in no project (pdMid): thread ▾ → Main conversation.
    await ev(`openDetail(${sid(A.loose)}); 0`);
    await until(`!!barTopTask() && barTopTask().roomId===${JSON.stringify(A.loose)}`, 'top level task');
    await sleep(600);
    out.top = await ev(BAR);
    await thrMenuOpen();
    out.topMenu = await ev(`[...document.querySelectorAll('#thr-menu [data-thr-pick]')].map(b => b.dataset.thrPick)`);
    await clickAt(null, '#thr-menu [data-thr-pick=""]');
    await sleep(400);
    out.topMain = await ev(BAR);
    await clickAt(null, '#proj-switch-name');
    await sleep(400);
    out.topName = await ev(BAR);
    await ev(closeMenus);
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class BarMain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-bar-main-', ignore_cleanup_errors=True)
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
        inbox.bind(dashboard)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        sid = 'bar-main-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        loose_home = base / 'loose'; loose_home.mkdir()
        t = chatroom.create_room('Loose ends', [{'identity':'claude','agent':'claude','cwd':str(loose_home)}])
        chatroom.post_message(t['id'], 'user', 'About loose ends')
        full = chatroom.get_room(t['id'], public=False)
        full.update(mode='solo', launched=True, workflow='inprogress', cwd=str(loose_home))
        chatroom.update_room(full)
        cls.loose = t['id']
        transcript = base / 'transcripts' / 'C--bar-main'; transcript.mkdir()
        at = time.time() - 3600

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','The first topic.',1), turn('assistant','The first reply.',2),
                 turn('user','The second topic.',3), turn('assistant','The second reply.',4)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        for text, offset in (('The first topic.', 1), ('The second topic.', 3)):
            threads.note_send(cls.po, '', '', [], False, text, now=at + offset, new=True)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj, 'po':cls.po, 'loose':cls.loose,
                'shots':os.environ.get('ENSEMBLE_SHOTS', '')}
        script = base / 'bar_main_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def back_on_main(self, g, what):
        self.assertIsNone(g['thrIn'], f'{what}: no thread in place')
        self.assertIsNone(g['side'], f'{what}: the chat shows no thread of its own')
        self.assertFalse(g['sideShown'], f'{what}: the chat\'s side column is closed')
        self.assertIsNone(g['kept'], f'{what}: no thread kept to come back')
        self.assertEqual((g['thr'], g['front'], g['threadTabs']), ('Main', 'po-chat', []), what)

    def test_the_thread_is_named_in_each_way_it_is_shown(self):
        g = self.got
        self.assertEqual(g['start']['thr'], 'Main')
        for k, tid, title in (('menuIn', g['ta'], 'The first topic.'), ('lineIn', g['tb'], 'The second topic.'),
                              ('inboxIn', g['ta'], 'The first topic.')):
            self.assertEqual(g[k]['thr'], title, f'{k}: thread ▾ names the thread {g[k]}')
            self.assertEqual(g[k]['thrIn'], {'tid': tid, 'mid': '', 'shown': True}, k)

    def test_thread_menu_main_goes_back_in_each_way(self):
        for k in ('menuMain', 'lineMain', 'inboxMain', 'afterReload'):
            self.back_on_main(self.got[k], k)

    def test_a_task_in_no_project(self):
        g = self.got
        self.assertEqual(g['top']['top'], self.loose)
        self.assertEqual(g['top']['thr'], 'Main')
        self.assertEqual(g['topMenu'], [''], 'a task\'s chat: Main alone')
        self.assertEqual((g['topMain']['thr'], g['topMain']['top']), ('Main', self.loose))

    def test_reported_case(self):
        print('\nname click in a thread:', json.dumps(self.got['nameClick']))
        print('task name click:', json.dumps(self.got['topName']))

    def test_no_errors(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
