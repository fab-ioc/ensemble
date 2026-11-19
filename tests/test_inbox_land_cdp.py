"""#396 (P414): an Inbox landing is one the person sees, at once.

The PO's live test of opTen (Documents/P414 opTen Inbox test/findings.md):

a. A row of the main chat pressed while a thread is shown lands in Main, seen;
   a landing the thread still covers is said to have failed, never done.
b. Loaded, with the chat's newest reply in a thread the newest page does not
   draw: no thread opens over Main by itself (it did 3.2 s after load).
c. The first row pressed as the chat is drawn, a card older than its newest
   page: it lands, with no false "could not open" (the 3.2 s reread replaced
   the fetch reaching back to it).
d. That first press lands quickly (it took 5 s).

In headless Chrome over CDP against a hub in a thread. ``ENSEMBLE_SHOTS``
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
  const key = async (k, code, vk, text) => {
    await send('Input.dispatchKeyEvent',{type:'keyDown',key:k,code,windowsVirtualKeyCode:vk,nativeVirtualKeyCode:vk,...(text?{text}:{})},s);
    await send('Input.dispatchKeyEvent',{type:'keyUp',key:k,code,windowsVirtualKeyCode:vk,nativeVirtualKeyCode:vk},s);
  };
  // The pointer over a part of the project's crumb: which parts are lit.
  const lit = `(() => { const bg = e => getComputedStyle(e).backgroundColor, clear = c => c === 'rgba(0, 0, 0, 0)' || c === 'transparent';
    return { name: !clear(bg(document.querySelector('#proj-switch .pj-nm'))), caret: !clear(bg(document.querySelector('#proj-switch .caret'))),
      button: !clear(bg(document.getElementById('proj-switch'))) }; })()`;
  const moveTo = async sel => {
    const p = await ev(`(()=>{const q=document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();return {x:q.x+q.width/2,y:q.y+q.height/2}})()`);
    await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:p.x,y:p.y},s);
    await sleep(150);
  };
  const tip = `({ name: document.querySelector('#proj-switch .pj-nm').getAttribute('title'), button: document.getElementById('proj-switch').title,
    desc: document.getElementById('proj-switch').getAttribute('aria-description') })`;
  const thrMenuOpen = async () => {
    await clickAt(null, '#thr-switch');
    await until(`!document.getElementById('thr-menu').hidden && !document.querySelector('#thr-menu .pm-none:not(:only-of-type)') && document.querySelectorAll('#thr-menu [data-thr-pick]').length >= 1 && !/Reading/.test(document.getElementById('thr-menu').textContent)`, 'thread menu');
  };
  // thread ▾ → Main conversation: what the bar and the chat say after.
  const onMain = `!PD.thrIn && !thrBarFrame()?.contentWindow?.sideNow?.() && document.getElementById('thr-switch-name').textContent==='Main'`;
  const viaMenuMain = async label => {
    await thrMenuOpen();
    await clickAt(null, '#thr-menu [data-thr-pick=""]');
    await until(onMain, label + ': Main');
    await sleep(300);
    return ev(BAR);
  };
  // The project's name clicked (#396): back to Main, no menu.
  const viaName = async label => {
    await clickAt(null, '#proj-switch-name');
    await until(onMain, label + ': the name, Main');
    await sleep(300);
    return ev(BAR);
  };
  // The Inbox shown, its row for the card pressed for real; the thread then in place.
  const viaInbox = async label => {
    await ev(`ibxOpen(); 0`);
    const row = `[...document.querySelectorAll('a.ibx-link[data-room=${JSON.stringify(A.po)}][data-mid=${JSON.stringify(A.card)}]')].find(a => a.getBoundingClientRect().width > 0)`;
    await until(`!!${row}`, label + ': the Inbox row');
    await ev(`document.querySelectorAll('[data-t396]').forEach(a => a.removeAttribute('data-t396')); ${row}.setAttribute('data-t396', ''); 0`);
    await clickAt(null, '[data-t396]');
    await until(inPlace(out.ta), label);
    // The card is drawn in the thread shown in place, not in the main chat.
    await until(`(() => { const has = x => !!x && [...x.querySelectorAll('.msg[data-mid]')].some(m => m.dataset.mid === ${JSON.stringify(A.card)} && m.offsetParent);
      return has(PD.thrIn?.el.querySelector('iframe')?.contentDocument) && !has(${PO}?.contentDocument?.getElementById('msgs')); })()`, label + ': the card in the thread');
    out.inboxRows = (out.inboxRows || 0) + 1;
  };
  const inPlace = tid => `PD.thrIn?.tid===${JSON.stringify(tid)} && pdThreadInShown() && document.getElementById('thr-switch-name').textContent!=='Main'`;
  const threadsIn = `[...${PO}.contentDocument.querySelectorAll('#msgs .thr-open[data-thr]')].map(b=>b.dataset.thr)`;
  const sid = room => `ALL_ROWS.find(x => x.roomId === ${JSON.stringify(room)}).sessionId`;
  const out = {};
  // The chat's own state, as its page holds it.
  const chat = expr => ev(`(() => { const w = ${PO}?.contentWindow; return w ? w.eval(${JSON.stringify(expr)}) : null; })()`);
  // Each goto-done the dashboard is told of, with when.
  const hookDone = `(() => { if (!window.__done) { window.__done = []; const real = gotoDoneHere;
    gotoDoneHere = (r, m, ok, w) => { window.__done.push({ r, m, ok, why: w, t: Date.now() }); return real(r, m, ok, w); }; } return 0; })()`;
  const pressRow = async (mid, label) => {
    await ev(`ibxOpen(); 0`);
    const row = `[...document.querySelectorAll('a.ibx-link[data-room=${JSON.stringify(A.po)}][data-mid=${JSON.stringify(mid)}]')].find(a => a.getBoundingClientRect().width > 0)`;
    await until(`!!${row}`, label + ': the Inbox row');
    await ev(`document.querySelectorAll('[data-t396]').forEach(a => a.removeAttribute('data-t396')); ${row}.setAttribute('data-t396', ''); 0`);
    const t = await ev('Date.now()');
    await clickAt(null, '[data-t396]');
    return t;
  };
  const seen = (doc, mid) => `(() => { const d = ${doc}; const box = d && d.getElementById('msgs'); const m = box && [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
    if (!m || !m.offsetParent || !box.clientHeight) return false; const b = box.getBoundingClientRect(), r = m.getBoundingClientRect(); return r.bottom > b.top + 1 && r.top < b.bottom - 1; })()`;
  // On screen: Main in front, nothing over it, the balloon in its view.
  const mainShows = mid => `(${onMain}) && PD.dock.frontOf('po-chat')==='po-chat' && document.getElementById('bar-grp').classList.contains('on') && ${seen(PO + '?.contentDocument', mid)}`;
  const toThread = async tid => { await thrMenuOpen(); await clickAt(null, `#thr-menu [data-thr-pick="${tid}"]`); await until(inPlace(tid), 'in place: ' + tid); await sleep(400); };
  const done = mid => ev(`(window.__done || []).filter(d => d.m === ${JSON.stringify(mid)})`);
  const load = async () => {
    const {identifier} = await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.ensBootOpen = false;'},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted===true`,'booted');
    await send('Page.removeScriptToEvaluateOnNewDocument',{identifier},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && !!${PO}?.contentWindow?.eval('typeof THR_DRAWN !== "undefined" && THR_DRAWN')`, 'the PO chat drawn');
    return ev('Date.now()');
  };
  try {
    await size(1728,1117);
    // b. Loaded, nothing pressed: no thread opens over Main by itself (the
    // chat's newest reply is in a thread the newest page does not draw).
    await load();
    out.base = await chat('THR_ANY && { mid: THR_ANY.mid, thread: THR_ANY.thread }');
    await sleep(5500);
    out.idle = await ev(BAR);
    out.idleChat = await chat('({ ts: TS, gen: THR_GEN })');
    await snap('land-idle');
    // c, d. A fresh load, the first row pressed as soon as the chat is drawn:
    // a card asked before the chat's newest page (older pages to fetch).
    const t0 = await load();
    await ev(hookDone);
    const t1 = await pressRow(A.cardOld, 'first press');
    await until(mainShows(A.cardOld), 'the first press: Main on the old card', 100);
    out.firstSeenMs = (await ev('Date.now()')) - t1;
    out.firstSinceLoadMs = t1 - t0;
    await until(`(window.__done || []).some(d => d.m === ${JSON.stringify(A.cardOld)})`, 'the first press: done', 100);
    // The chat's own clock past its 3.2 s watch: nothing changes after.
    await sleep(4500);
    out.firstDone = await done(A.cardOld);
    out.firstDone.forEach(d => { d.ms = d.t - t1; });
    out.firstAfter = await ev(BAR);
    out.firstStill = await ev(seen(PO + '?.contentDocument', A.cardOld));
    out.firstNote = await chat(`(() => { const n = document.querySelector('.goto-note, #goto-note'); return n && n.offsetParent ? n.textContent : ''; })()`);
    // A second press, for comparison: a card newer than the window the first
    // landed in (a row past its end was drawn there, then pushed away).
    const t2 = await pressRow(A.cardM, 'second press');
    await until(mainShows(A.cardM), 'the second press: Main on its card', 100).catch(async e => {
      const dbg = await chat(`({ goto: GOTO, asked: THR_ASKED, after: THR_AFTER, before: THR_BEFORE, fetching: THR_FETCHING,
        has: (LAST_ITEMS || []).some(m => m.id === ${JSON.stringify(A.cardM)}), n: (LAST_ITEMS || []).length,
        dom: [...document.querySelectorAll('#msgs .msg[data-mid]')].filter(m => m.dataset.mid === ${JSON.stringify(A.cardM)}).map(m => { const r = m.getBoundingClientRect(), b = document.getElementById('msgs').getBoundingClientRect(); return [!!m.offsetParent, r.top, r.bottom, b.top, b.bottom, m.className]; }),
        last: (LAST_ITEMS || []).slice(-3).map(m => m.id + ' ' + (m.text || '').slice(0, 30)) })`);
      throw Error(e.message + ' ' + JSON.stringify(dbg) + ' ' + JSON.stringify(await ev('window.__done')));
    });
    out.secondSeenMs = (await ev('Date.now()')) - t2;
    // a. A thread shown, a row in Main: Main, on the card, seen.
    await toThread(A.thread);
    const t3 = await pressRow(A.cardM, 'thread → Main row');
    await until(mainShows(A.cardM), 'thread shown, a Main row: Main on its card', 100);
    out.overThread = await ev(BAR);
    out.overThreadMs = (await ev('Date.now()')) - t3;
    await snap('land-over-thread');
    // a. The thread that stays over Main (its leaving broken on purpose):
    // the landing is not reported done; the row says it could not.
    await toThread(A.thread);
    await ev(`window.__leave = pdThreadInLeave; pdThreadInLeave = () => false; window.__done = window.__done.filter(d => d.m !== ${JSON.stringify(A.cardM)}); 0`);
    await pressRow(A.cardM, 'covered');
    await until(`(window.__done || []).some(d => d.m === ${JSON.stringify(A.cardM)})`, 'covered: done', 100);
    out.covered = await done(A.cardM);
    out.coveredBar = await ev(BAR);
    await ev(`pdThreadInLeave = window.__leave; 0`);
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class InboxLand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-inbox-land-', ignore_cleanup_errors=True)
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
        sid = 'inbox-land-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        transcript = base / 'transcripts' / 'C--inbox-land'; transcript.mkdir()
        at = int(time.time()) - 3600

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}

        def card(text, offset):
            with mock.patch.object(chatroom, '_now', lambda: at + offset):
                m = chatroom.post_message(cls.po, 'claude', text, to='user', structured_asks=dashboard.asks.validated(
                    [{'question': text, 'options': [{'label': 'Yes'}, {'label': 'No'}]}], []), ask_audience='user')
            return ((m or {}).get('message') or {}).get('id', '')
        # A thread, then 60 exchanges in the main chat (more than its newest
        # page), then a reply sent from the thread's box and its answer: the
        # chat's newest reply, in the thread, under no line of the newest page.
        lines = [turn('user', 'The topic.', 1), turn('assistant', 'The topic\'s reply.', 2)]
        for i in range(60):
            lines += [turn('user', f'Main note {i}.', 10 + 2 * i), turn('assistant', f'Main answer {i}.', 11 + 2 * i)]
        lines += [turn('user', 'More on the topic.', 301), turn('assistant', 'The thread\'s last word.', 302)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        threads.note_send(cls.po, '', '', [], False, 'The topic.', now=at + 1, new=True)
        cls.cardOld = card('An old question for the main chat?', 0.5)
        cls.cardM = card('A new question for the main chat?', 200)
        threads.note_send(cls.po, f'm:{sid}:0', '', [], False, 'More on the topic.', now=at + 301)
        built = threads.build(cls.po, show_progress=False)
        filed = (built or {}).get('cards', {})
        assert (filed.get(cls.cardOld), filed.get(cls.cardM)) == (None, None), built
        cls.thread = f'm:{sid}:0'
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj, 'po':cls.po, 'cardOld':cls.cardOld, 'cardM':cls.cardM, 'thread':cls.thread,
                'shots':os.environ.get('ENSEMBLE_SHOTS', '')}
        script = base / 'inbox_land_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('ENSEMBLE_SHOTS'): print(json.dumps({k: v for k, v in cls.got.items() if k != 'errors'}, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_the_fixture(self):
        # The chat's newest reply is in the thread: the case the watch saw.
        self.assertEqual(self.got['base']['thread'], self.thread, self.got['base'])

    def test_b_no_thread_opens_by_itself(self):
        g = self.got
        self.assertIsNone(g['idle']['thrIn'], f"a thread in place over Main by itself {g['idle']}")
        self.assertIsNone(g['idleChat']['ts'], g['idleChat'])
        self.assertEqual(g['idle']['thr'], 'Main')

    def test_c_the_first_press_lands_with_no_false_failure(self):
        g = self.got
        self.assertEqual([d['ok'] for d in g['firstDone']], [True], g['firstDone'])
        self.assertTrue(g['firstStill'], "still shown after the chat's 3.2 s watch")
        self.assertEqual(g['firstNote'], '')
        self.assertIsNone(g['firstAfter']['thrIn'])

    def test_d_the_first_press_is_quick(self):
        g = self.got
        self.assertLess(g['firstSeenMs'], 1500, g)
        self.assertLess(g['firstDone'][0]['ms'], 1500, g['firstDone'])

    def test_a_row_newer_than_a_links_window_lands(self):
        self.assertLess(self.got['secondSeenMs'], 1500, self.got)

    def test_a_a_main_row_over_a_thread_lands_in_main(self):
        g = self.got
        self.assertEqual((g['overThread']['thr'], g['overThread']['thrIn']), ('Main', None))

    def test_a_a_covered_landing_is_a_failure(self):
        g = self.got
        self.assertEqual([d['ok'] for d in g['covered']], [False], g['covered'])
        self.assertIn('under the thread', g['covered'][0]['why'])
        self.assertIsNotNone(g['coveredBar']['thrIn'], 'the thread was left over Main on purpose')

    def test_no_errors(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
