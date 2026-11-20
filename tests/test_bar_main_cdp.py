"""#396 (P403): in a thread, the bar's way back to Main.

The bar on a computer reads ENSEMBLE / project ▾ / thread ▾. In a thread the
group reads as a breadcrumb, so the project's name is the way back: one click on
it while a thread is shown shows the main conversation. thread ▾ → Main
conversation goes back too, in every way a thread is shown: picked in thread ▾,
a thread line clicked in the chat, an Inbox row of a card filed in it, a task
in no project (pdMid). thread ▾ names the thread shown, never Main while one is.
The name and its ▾ are two hit areas (#396): the ▾ is always the menu; on Main
with the conversation in front the name opens the menu as before; another tab
in front, the name brings the conversation to the front; Enter is the name.

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
    out.tipIn = await ev(tip);
    await moveTo('#proj-switch .pj-nm');
    out.litName = await ev(lit);
    await snap('main-hover-name');
    await moveTo('#proj-switch .caret');
    out.litCaret = await ev(lit);
    await snap('main-hover-caret');
    // The ▾ in a thread: the menu, the thread stays.
    await clickAt(null, '#proj-switch .caret');
    await sleep(400);
    out.caretIn = await ev(BAR);
    await ev(closeMenus);
    await sleep(200);
    // 1. The name in a thread: Main.
    out.nameMain = await viaName('thread ▾ then the name');
    out.tipMain = await ev(tip);
    await snap('main-name-click');
    // 2a. thread ▾ → Main, the thread picked in thread ▾.
    await thrMenuOpen(); await clickAt(null, `#thr-menu [data-thr-pick="${ta}"]`); await until(inPlace(ta), 'again in place'); await sleep(300);
    out.menuMain = await viaMenuMain('thread ▾');
    // 2b. The chat's own thread line clicked (its sideOpen).
    await clickAt(PO, `#msgs .thr-open[data-thr="${tb}"]`);
    await until(inPlace(tb), 'thread in place from its line');
    await sleep(400);
    out.lineIn = await ev(BAR);
    out.lineMain = await viaMenuMain('thread line');
    await clickAt(PO, `#msgs .thr-open[data-thr="${tb}"]`);
    await until(inPlace(tb), 'thread in place from its line, again');
    await sleep(400);
    out.lineName = await viaName('thread line then the name');
    // 2c. The Inbox row of a card filed in the thread, pressed (#394's row
    // click → openMsgLink → the thread in place, landing on the card).
    await viaInbox('thread in place from an Inbox row');
    await sleep(400);
    out.inboxIn = await ev(BAR);
    out.inboxMain = await viaMenuMain('Inbox link');
    // The keyboard: the button focused, Enter is the name.
    await viaInbox('thread in place from an Inbox row, again');
    await sleep(400);
    await ev(`document.getElementById('proj-switch').focus(); 0`);
    await key('Enter', 'Enter', 13, '\r');
    await until(onMain, 'Inbox link then Enter: Main');
    await sleep(300);
    out.inboxKey = await ev(BAR);
    // 2d. The chat reloaded after Main: no thread comes back.
    await ev(`${PO}.contentWindow.location.reload(); 0`);
    await sleep(800);
    await until(`${PO}?.contentDocument?.readyState==='complete' && ${threadsIn}.length===2`, 'chat reloaded');
    await sleep(800);
    out.afterReload = await ev(BAR);
    // On Main, the conversation in front: the name opens the menu, as before.
    await clickAt(null, '#proj-switch-name');
    await sleep(400);
    out.mainName = await ev(BAR);
    await clickAt(null, '#proj-switch-name');
    await sleep(300);
    out.mainNameAgain = await ev(BAR);
    // ArrowDown on the button: the menu.
    await ev(`document.getElementById('proj-switch').focus(); 0`);
    await key('ArrowDown', 'ArrowDown', 40);
    await sleep(300);
    out.mainArrow = await ev(BAR);
    await ev(closeMenus);
    // Ctrl/⌘-click in thread ▾: a tab of its own, in front; then the name
    // brings the conversation to the front (no menu).
    await thrMenuOpen();
    await clickAt(null, `#thr-menu [data-thr-pick="${ta}"]`, 2);
    await until(`[...PD.rt].some(([, e]) => e.kind === 'thread') && !document.getElementById('bar-grp').classList.contains('on')`, 'a thread tab in front');
    await sleep(400);
    out.ctrlTab = await ev(BAR);
    out.tipTab = await ev(tip);
    await clickAt(null, '#proj-switch-name');
    await until(`document.getElementById('bar-grp').classList.contains('on')`, 'the name: the conversation in front');
    await sleep(300);
    out.nameFront = await ev(BAR);

    // 4. P417: an Inbox row shows the right conversation from wherever one is,
    // landing on its message: Main, the right thread, from a task's tab.
    const pressRow = async (mid, label) => {
      await ev(`ibxOpen(); 0`);
      const row = `[...document.querySelectorAll('a.ibx-link[data-room=${JSON.stringify(A.po)}][data-mid=${JSON.stringify(mid)}]')].find(a => a.getBoundingClientRect().width > 0)`;
      await until(`!!${row}`, label + ': the Inbox row');
      await ev(`document.querySelectorAll('[data-t396]').forEach(a => a.removeAttribute('data-t396')); ${row}.setAttribute('data-t396', ''); 0`);
      await clickAt(null, '[data-t396]');
    };
    // The balloon drawn inside its chat's view (a frame's document).
    const seen = (doc, mid) => `(() => { const d = ${doc}; const box = d && d.getElementById('msgs'); const m = box && [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === ${JSON.stringify(mid)});
      if (!m || !m.offsetParent || !box.clientHeight) return false; const b = box.getBoundingClientRect(), r = m.getBoundingClientRect(); return r.bottom > b.top + 1 && r.top < b.bottom - 1; })()`;
    const mainShows = mid => `(${onMain}) && PD.dock.frontOf('po-chat')==='po-chat' && document.getElementById('bar-grp').classList.contains('on') && ${seen(PO + '?.contentDocument', mid)}`;
    const threadShows = (tid, mid) => `(${inPlace(tid)}) && PD.dock.frontOf('po-chat')==='po-chat' && ${seen("PD.thrIn?.el.querySelector('iframe')?.contentDocument", mid)}`;
    const toThread = async tid => { await thrMenuOpen(); await clickAt(null, `#thr-menu [data-thr-pick="${tid}"]`); await until(inPlace(tid), 'in place: ' + tid); await sleep(400); };
    out.p417 = {};
    // a. A thread shown, a row in Main: Main, on the card.
    await toThread(ta);
    await pressRow(A.cardM, 'thread → Main row');
    await until(mainShows(A.cardM), 'thread shown, a row in Main: Main on its card');
    out.p417.threadToMain = await ev(BAR);
    await snap('p417-thread-to-main');
    // b. A thread shown, a row in another thread: that thread, on the card.
    await toThread(ta);
    await pressRow(A.cardB, 'thread → other thread row');
    await until(threadShows(tb, A.cardB), 'thread shown, a row in another thread: that thread on its card');
    out.p417.threadToThread = await ev(BAR);
    await snap('p417-thread-to-thread');
    // c. Main shown, a row in a thread: the thread, on the card.
    await viaMenuMain('back to Main for P417 c');
    await pressRow(A.card, 'Main → thread row');
    await until(threadShows(ta, A.card), 'Main shown, a row in a thread: the thread on its card');
    out.p417.mainToThread = await ev(BAR);
    // d. Another task's tab in front, a row in the PO's Main: the conversation, Main, on the card
    // (here with a thread left shown in the conversation, the harder way).
    await ev(`openDetail(${sid(A.task)}); 0`);
    await until(`!document.getElementById('bar-grp').classList.contains('on') && barTabsSync && [...PD.rt].length > 0`, 'the task\'s tab in front');
    await sleep(600);
    out.p417.taskFront = await ev(BAR);
    await pressRow(A.cardM, 'task tab → Main row');
    await until(mainShows(A.cardM), 'a task\'s tab in front, a row in Main: the conversation on its card');
    out.p417.taskToMain = await ev(BAR);
    // and with Main shown behind the task's tab.
    await ev(`openDetail(${sid(A.task)}); 0`);
    await until(`!document.getElementById('bar-grp').classList.contains('on')`, 'the task\'s tab in front again');
    await sleep(600);
    await pressRow(A.cardM, 'task tab → Main row, again');
    await until(mainShows(A.cardM), 'a task\'s tab in front again: the conversation on its card');
    out.p417.taskToMainAgain = await ev(BAR);
    await snap('p417-task-to-main');

    // 3. A task in no project (pdMid): thread ▾ → Main conversation.
    await ev(`openDetail(${sid(A.loose)}); 0`);
    await until(`!!barTopTask() && barTopTask().roomId===${JSON.stringify(A.loose)}`, 'top level task');
    await sleep(600);
    out.top = await ev(BAR);
    await moveTo('#proj-switch .pj-nm');
    await snap('main-top-hover');
    await thrMenuOpen();
    out.topMenu = await ev(`[...document.querySelectorAll('#thr-menu [data-thr-pick]')].map(b => b.dataset.thrPick)`);
    await clickAt(null, '#thr-menu [data-thr-pick=""]');
    await sleep(400);
    out.topMain = await ev(BAR);
    await clickAt(null, '#proj-switch-name');
    await sleep(400);
    out.topName = await ev(BAR);
    out.tipTop = await ev(tip);
    await ev(closeMenus);
    await sleep(200);
    await clickAt(null, '#proj-switch .caret');
    await sleep(400);
    out.topCaret = await ev(BAR);
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
        at = int(time.time()) - 3600  # whole seconds, as the transcript's stamps: each at its own second, the card (+4) after the reply sent in the first thread (+3), before the second topic (+5)

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = [turn('user','The first topic.',1), turn('assistant','The first reply.',2),
                 turn('user','More on the first topic.',3),
                 turn('user','The second topic.',5), turn('assistant','The second reply.',6),
                 turn('user','More on the second topic.',7)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        for text, offset in (('The first topic.', 1), ('The second topic.', 5)):
            threads.note_send(cls.po, '', '', [], False, text, now=at + offset, new=True)
        # A reply sent from the first thread's box, then an open card: the card
        # is filed in that thread (threads: where the input before it is) and
        # is an Inbox row.
        threads.note_send(cls.po, f'm:{sid}:0', '', [], False, 'More on the first topic.', now=at + 3)
        qs = dashboard.asks.validated([{'question': 'Brakes first?', 'options': [{'label': 'Yes'}, {'label': 'No'}]}], [])
        with mock.patch.object(chatroom, '_now', lambda: at + 4):
            posted = chatroom.post_message(cls.po, 'claude', 'A question on the first topic.', to='user', structured_asks=qs, ask_audience='user')
        cls.card = ((posted or {}).get('message') or {}).get('id', ''); assert cls.card, posted
        # P417: a card in the main chat (asked before any input) and one in the
        # second thread (after a reply sent from its box).
        def card(text, offset):
            with mock.patch.object(chatroom, '_now', lambda: at + offset):
                m = chatroom.post_message(cls.po, 'claude', text, to='user', structured_asks=dashboard.asks.validated(
                    [{'question': text, 'options': [{'label': 'Yes'}, {'label': 'No'}]}], []), ask_audience='user')
            return ((m or {}).get('message') or {}).get('id', '')
        cls.cardM = card('A question for the main chat?', 0.5)
        threads.note_send(cls.po, f'm:{sid}:3', '', [], False, 'More on the second topic.', now=at + 7)
        cls.cardB = card('A question on the second topic?', 8)
        # Built before the page loads (as test_inbox_trust_cdp): the chat's first
        # draw already knows the card is in the thread.
        built = threads.build(cls.po, show_progress=False)
        filed = (built or {}).get('cards', {})
        assert (filed.get(cls.card), filed.get(cls.cardB), filed.get(cls.cardM)) == (f'm:{sid}:0', f'm:{sid}:3', None), built
        # A task of Motors: it opens as a tab of its own (#393).
        home_t = base / 'gearbox'; home_t.mkdir()
        g = chatroom.create_room('Gearbox', [{'identity':'claude','agent':'claude','cwd':str(home_t)}])
        chatroom.post_message(g['id'], 'user', 'About the gearbox')
        full = chatroom.get_room(g['id'], public=False)
        full.update(mode='solo', launched=True, workflow='inprogress', cwd=str(home_t))
        chatroom.update_room(full)
        dashboard.assign_session_project(g['id'], cls.proj)
        cls.task = g['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj, 'po':cls.po, 'loose':cls.loose, 'card':cls.card, 'cardM':cls.cardM, 'cardB':cls.cardB, 'task':cls.task,
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
        self.assertEqual(g.get('inboxRows'), 2, 'twice a real Inbox row of the card filed in the thread was pressed')
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

    def test_the_name_in_a_thread_goes_back_to_main(self):
        g = self.got
        for k in ('nameMain', 'lineName', 'inboxKey'):
            self.back_on_main(g[k], k)
            self.assertFalse(g[k]['projMenu'], f'{k}: no menu')

    def test_the_caret_is_always_the_menu(self):
        g = self.got
        self.assertTrue(g['caretIn']['projMenu'], g['caretIn'])
        self.assertEqual(g['caretIn']['thrIn'], {'tid': g['ta'], 'mid': '', 'shown': True}, 'the ▾: the thread stays')
        self.assertTrue(g['topCaret']['projMenu'], g['topCaret'])

    def test_two_hit_areas_lit_on_their_own(self):
        g = self.got
        self.assertEqual(g['litName'], {'name': True, 'caret': False, 'button': False})
        self.assertEqual(g['litCaret'], {'name': False, 'caret': True, 'button': False})

    def test_the_names_tooltip(self):
        g = self.got
        self.assertEqual(g['tipIn']['name'], 'Back to the main conversation')
        self.assertIn('Arrow down', g['tipIn']['desc'])
        self.assertIn('switch', g['tipIn']['button'])
        self.assertIsNone(g['tipMain']['name'], 'on Main in front: the switcher\'s own tooltip')
        self.assertIsNone(g['tipMain']['desc'])
        self.assertEqual(g['tipTab']['name'], 'Show the conversation')
        self.assertIsNone(g['tipTop']['name'])

    def test_on_main_the_name_is_as_before(self):
        g = self.got
        self.assertTrue(g['mainName']['projMenu'], 'in front on Main: the name opens project ▾')
        self.assertFalse(g['mainNameAgain']['projMenu'], 'and closes it')
        self.assertTrue(g['mainArrow']['projMenu'], 'ArrowDown: the menu')
        self.assertTrue(g['topName']['projMenu'], 'a task in no project, in front on Main: the menu')

    def test_ctrl_click_a_thread_then_the_name(self):
        g = self.got
        self.assertEqual(len(g['ctrlTab']['threadTabs']), 1, f"Ctrl-click in thread ▾: a tab {g['ctrlTab']}")
        self.assertFalse(g['ctrlTab']['grpOn'])
        self.assertEqual((g['nameFront']['front'], g['nameFront']['grpOn'], g['nameFront']['projMenu']), ('po-chat', True, False))

    def test_an_inbox_row_shows_its_conversation_from_anywhere(self):
        # P417: each wait above was for the right conversation on screen,
        # landed on the card; what the bar said then.
        p = self.got['p417']
        self.assertEqual((p['threadToMain']['thr'], p['threadToMain']['thrIn']), ('Main', None))
        self.assertEqual(p['threadToThread']['thr'], 'The second topic.')
        self.assertEqual(p['threadToThread']['thrIn']['tid'], self.got['tb'])
        self.assertEqual(p['mainToThread']['thrIn']['tid'], self.got['ta'])
        self.assertFalse(p['taskFront']['grpOn'], f"the task's tab was in front {p['taskFront']}")
        for k in ('taskToMain', 'taskToMainAgain'):
            self.assertEqual((p[k]['front'], p[k]['grpOn'], p[k]['thr']), ('po-chat', True, 'Main'), k)

    def test_no_errors(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
