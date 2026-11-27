"""Threads under each project in the left panel (#420, GitHub issue 68,
drawing option A). In headless Chrome over CDP, against a hub in a thread.

Motors has a PO chat with seven threads, Garden a PO chat with none. Checked:

* the hub's /api/projects says how many threads each PO chat lists
  (``threads``), and Garden, once read, has no caret;
* a click on Motors selects it and unfolds Main chat, the 5 latest threads
  (latest first) and All 7 threads…;
* a poll patches the rows in place: the same nodes;
* a click on a thread shows it in place in the PO chat and marks its row;
  Main chat goes back to the conversation; Ctrl-click opens a tab; All 7
  threads… turns on the chat's Threads filter;
* a thread of Motors clicked while Garden is selected picks Motors first;
* the caret folds without changing the selection, and the fold is kept
  over a reload;
* the keyboard tree: ← folds, → unfolds, → again goes to Main chat, ↓ moves,
  ← goes back to the project, Enter opens;
* a new order waits while the pointer is over the list, and comes 2 s after
  it left;
* a pinned project keeps its rows inside its row (they go with a drag);
* a phone's project menu has the current project's rows, 44px each, and a
  tap shows the thread;
* the page threw nothing.
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
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
TOPICS = ['Wheel sizes', 'Brake pads', 'Paint colours', 'Seat height', 'Chain length', 'Bell volume', 'Light mounts']

def _age(ts):
    # thrAge: whole calendar days in local time; 'today' for none.
    import datetime
    n = (datetime.date.today() - datetime.date.fromtimestamp(ts)).days
    return 'today' if n <= 0 else f'{n} d'


CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--hide-scrollbars',
    '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((res,rej) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited '+c))); setTimeout(() => rej(new Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0; const errors=[];
  await new Promise((res,rej) => { c.onopen=res; c.onerror=rej; });
  c.onmessage = ev => { const m=JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 600));
    const w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((res,rej) => { const id=++seq; waits.set(id,[res,rej]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  await send('Runtime.enable',{},s);
  const size=(w,h,touch=false) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:touch},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,140)+' '+JSON.stringify(r.exceptionDetails).slice(0,600)); return r.result.value; };
  const until=async (e, what, n=140) => { for(let i=0;i<n;i++){ try {if(await ev(e)) return;}catch(x){} await sleep(150); } throw Error('timeout '+(what||e)); };
  const mouse=(type,x,y,o={}) => send('Input.dispatchMouseEvent',{type,x,y,button:type==='mouseMoved'?'none':(o.button||'left'),clickCount:1,modifiers:o.modifiers||0},s);
  const click=async (x,y,o) => { await mouse('mouseMoved',x,y); await mouse('mousePressed',x,y,o); await mouse('mouseReleased',x,y,o); };
  const key=async (k, code, vk) => { for (const type of ['rawKeyDown','keyUp']) await send('Input.dispatchKeyEvent',{type,key:k,code,windowsVirtualKeyCode:vk},s); };
  const KEYS={ArrowDown:['ArrowDown',40],ArrowUp:['ArrowUp',38],ArrowLeft:['ArrowLeft',37],ArrowRight:['ArrowRight',39],Enter:['Enter',13]};
  const press=k => key(k, KEYS[k][0], KEYS[k][1]);
  const shot=async name => { if(!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png'},s); fs.writeFileSync(path.join(A.shots,name+'.png'),Buffer.from(r.data,'base64')); };
  const P=JSON.stringify(A.proj), P2=JSON.stringify(A.proj2);
  const at=sel => ev(`(() => { const e=document.querySelector(${JSON.stringify(sel)}); if(!e) return null; e.scrollIntoView({block:'nearest'}); const r=e.getBoundingClientRect(); return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}; })()`);
  const GROUP = `#ld-projs [data-ldp-thr=${P}]`;
  const ROWS = `[...document.querySelectorAll('${GROUP} > button')].map(b => ({key:b.dataset.ldpTkey, name:b.querySelector('.ldp-name')?.textContent||'',
    cur:b.getAttribute('aria-current')==='true', unread:b.hasAttribute('data-unread'), pill:(b.querySelector('.ldp-n:not([hidden])')||{}).textContent||'', age:(b.querySelector('.ldp-age')||{}).textContent||''}))`;
  const PO = `PO_PANEL.querySelector('iframe.po-session:not([hidden])')`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.ensBootOpen = false;'},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted === true && typeof PROJECTS !== 'undefined' && !!PROJECTS && PROJECTS.projects.length > 1`, 'boot');
    await ev(`SELECTED_PROJECT=${P2}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!LD.dock && !!document.querySelector('#ld-projs .ldp-row[data-ldp-proj=${P}]')`, 'left panel');
    // Garden's chat read once: the hub then says it has none, and its caret goes.
    await ev(`fetch('/api/room/channel?id=${A.po2}&threads=1&n=1&lite=1').then(r => r.status)`);
    await ev(`fetch('/api/room/channel?id=${A.po}&threads=1&n=1&lite=1').then(r => r.status)`);
    await until(`projectById(${P2}).threads && projectById(${P}).threads && (refreshProjects ? (refreshProjects(), true) : true) && projectById(${P2}).threads.n === 0`, 'hub summary', 200);
    out.summary = await ev(`({motors: projectById(${P}).threads, garden: projectById(${P2}).threads})`);
    await sleep(300);
    out.carets = await ev(`({motors: !!document.querySelector('#ld-projs [data-ldp-car=${P}]'), garden: !!document.querySelector('#ld-projs [data-ldp-car=${P2}]')})`);
    // A click on Motors: selected and unfolded.
    let p = await at(`#ld-projs .ldp-row[data-ldp-proj=${P}]`);
    await click(p.x, p.y);
    await until(`PD.pid === ${P} && document.querySelectorAll('${GROUP} > .ldp-trow[data-ldp-trow]').length === 6`, 'unfolded');
    await mouse('mouseMoved', 900, 600); await sleep(2300);
    out.unfolded = {rows: await ev(ROWS), expanded: await ev(`document.querySelector('#ld-projs [data-ldp-car=${P}]').getAttribute('aria-expanded')`),
      selected: await ev(`!!document.querySelector('#ld-projs .ldp-row[data-ldp-proj=${P}][aria-current]')`),
      stored: await ev(`localStorage.getItem('cd-ldp-open')`)};
    await shot('ldt-unfolded');
    // A poll: the same nodes.
    await ev(`window.__rows=[...document.querySelectorAll('${GROUP} > button')]; LDT.data.get(${P}).at = 0; window.__f = LDT.data.get(${P}); 0`);
    await until(`LDT.data.get(${P}) !== window.__f`, 'refetched');
    await ev(`LDP.sig=''; ldpPaint(); 0`);
    out.same = await ev(`(() => { const now=[...document.querySelectorAll('${GROUP} > button')]; return now.length === __rows.length && now.every((b,i) => b === __rows[i]); })()`);
    // A click on the second thread: in place, its row marked.
    const second = out.unfolded.rows[2];
    p = await at(`${GROUP} > [data-ldp-tkey="${second.key}"]`);
    await click(p.x, p.y);
    const tid = second.key.slice(2);
    await until(`!!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(tid)}`, 'thread in place');
    await sleep(1200);
    out.picked = {tid, rows: await ev(ROWS)};
    await shot('ldt-picked');
    // Main chat: back.
    p = await at(`${GROUP} > [data-ldp-tkey="main"]`);
    await click(p.x, p.y);
    await until(`!(PD.thrIn && pdThreadInShown())`, 'main');
    await sleep(1200);
    out.main = await ev(ROWS);
    // Ctrl-click: a tab of its own.
    const third = out.unfolded.rows[3];
    p = await at(`${GROUP} > [data-ldp-tkey="${third.key}"]`);
    await click(p.x, p.y, {modifiers: 2});
    await until(`PD.dock.panels().some(x => x.id === pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(third.key.slice(2))}, ''))`, 'thread tab');
    out.tab = {inPlace: !!(await ev(`!!PD.thrIn && pdThreadInShown()`))};
    await ev(`PD.dock.removePanel(pdThreadTabId(${JSON.stringify(A.po)}, ${JSON.stringify(third.key.slice(2))}, '')); PD.dock.activate('po-chat'); 0`);
    // All 7 threads…: the chat's Threads filter.
    p = await at(`${GROUP} > [data-ldp-tkey="all"]`);
    await click(p.x, p.y);
    await until(`${PO}.contentWindow.document.querySelector('#chat-filters [data-filter=threads]')?.getAttribute('aria-pressed') === 'true'`, 'threads filter');
    out.filter = true;
    await ev(`${PO}.contentWindow.chatFilterSet({kind:'all'}); 0`);
    // Garden selected, then a Motors thread: Motors first, then the thread.
    p = await at(`#ld-projs .ldp-row[data-ldp-proj=${P2}]`);
    await click(p.x, p.y);
    await until(`PD.pid === ${P2}`, 'garden');
    await sleep(500);
    p = await at(`${GROUP} > [data-ldp-tkey="${second.key}"]`);
    await click(p.x, p.y);
    await until(`PD.pid === ${P} && !!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(tid)}`, 'cross-project thread');
    out.cross = await ev(`({pid: PD.pid, scope: IBX.scope, lp: ibxLdPid()})`);
    await ev(`pdThreadInClose(false); 0`);
    // The caret folds; the selection stays; a reload keeps it folded.
    p = await at(`#ld-projs [data-ldp-car=${P}]`);
    await click(p.x, p.y);
    await sleep(300);
    out.folded = {group: await ev(`!!document.querySelector('${GROUP}')`), pid: await ev('PD.pid'),
      expanded: await ev(`document.querySelector('#ld-projs [data-ldp-car=${P}]').getAttribute('aria-expanded')`)};
    await ev('window.__before = 1; 0');
    await send('Page.reload',{},s);
    await until(`!window.__before && window.ensBooted === true && !!LD.dock && !!document.querySelector('#ld-projs [data-ldp-car=${P}]')`, 'reload');
    await sleep(800);
    out.reload = await ev(`!!document.querySelector('${GROUP}')`);
    p = await at(`#ld-projs [data-ldp-car=${P}]`);
    await click(p.x, p.y);
    await until(`document.querySelectorAll('${GROUP} > .ldp-trow').length >= 6`, 'unfolded by the caret');
    // The keyboard tree.
    const FOCUS = `(() => { const a=document.activeElement; return a ? (a.dataset.ldpProj ? 'proj:' + a.dataset.ldpProj : a.dataset.ldpTkey || a.className) : null; })()`;
    await mouse('mouseMoved', 900, 600);
    await ev(`document.querySelector('#ld-projs .ldp-row[data-ldp-proj=${P}]').focus(); 0`);
    const k = [];
    await press('ArrowLeft'); await sleep(200); k.push({key:'left', group: await ev(`!!document.querySelector('${GROUP}')`), focus: await ev(FOCUS)});
    await press('ArrowRight'); await sleep(200);
    await until(`document.querySelectorAll('${GROUP} > .ldp-trow').length >= 6`, 'right unfolds');
    k.push({key:'right', group: true, focus: await ev(FOCUS)});
    await press('ArrowRight'); await sleep(100); k.push({key:'right2', focus: await ev(FOCUS)});
    await press('ArrowDown'); await sleep(100); k.push({key:'down', focus: await ev(FOCUS)});
    await press('ArrowLeft'); await sleep(100); k.push({key:'left2', focus: await ev(FOCUS)});
    await press('ArrowDown'); await press('ArrowDown'); await sleep(100);
    k.push({key:'down2', focus: await ev(FOCUS), ring: await ev(`getComputedStyle(document.activeElement).boxShadow`)});
    await press('Enter');
    await until(`!!PD.thrIn && pdThreadInShown()`, 'enter opens');
    k.push({key:'enter', tid: await ev('PD.thrIn.tid')});
    out.keys = k;
    out.firstThread = out.unfolded.rows[1].key.slice(2);
    await ev(`pdThreadInClose(false); document.activeElement && document.activeElement.blur(); 0`);
    // Held while the pointer is over the list: a new order waits till 2 s after it left.
    await sleep(2300);
    const order = () => ev(`[...document.querySelectorAll('${GROUP} > button')].map(b => b.dataset.ldpTkey)`);
    const o0 = await order();
    p = await at(`${GROUP} > [data-ldp-tkey="main"]`);
    await mouse('mouseMoved', p.x, p.y);
    await ev(`(() => { const d=LDT.data.get(${P}); const l=d.list.slice(); const last=l[l.length-1]; last.updatedAt = Date.now()/1000 + 60; LDT.data.set(${P}, {...d, list:l, at:Date.now()}); ldtPaintAll(); return 0; })()`);
    await sleep(600);
    const over = await order();
    await mouse('mouseMoved', 900, 600);
    await sleep(800);
    const left800 = await order();
    await sleep(2000);
    out.held = {o0, over, left800, after: await order()};
    // Pinned: the rows inside its row.
    await ev(`pinToggle(${P}); 0`);
    await until(`!!document.querySelector('#ld-projs .ldp-pins .ldp-hand[data-pin-row=${P}] > [data-ldp-thr]')`, 'pinned');
    out.pinned = await ev(`document.querySelectorAll('#ld-projs .ldp-pins .ldp-hand[data-pin-row=${P}] > [data-ldp-thr] > .ldp-trow').length`);
    await shot('ldt-pinned');
    // No sideways scroll in the panel.
    out.scroll = await ev(`(() => { const l=document.getElementById('ld-list'); return [l.scrollWidth, l.clientWidth]; })()`);
    // A phone: the project menu.
    await size(390,844,true); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`phoneNow() && PD.dock && PD.dock.narrow()`, 'phone');
    await sleep(600);
    await ev(`openProjMenu(false); 0`);
    await until(`document.querySelectorAll('#proj-menu .pm-thr > .ldp-trow').length >= 4`, 'phone rows');
    await sleep(300);
    out.phone = await ev(`(() => { const g=document.querySelector('#proj-menu .pm-thr'); const row=g.previousElementSibling;
      return {after: row && row.dataset.pinRow, rows:[...g.children].map(b => ({key:b.dataset.ldpTkey, h:Math.round(b.getBoundingClientRect().height)})),
        scroll:[document.documentElement.scrollWidth, document.documentElement.clientWidth]}; })()`);
    await shot('ldt-phone');
    const ph = out.phone.rows[1].key;
    p = await at(`#proj-menu .pm-thr > [data-ldp-tkey="${ph}"]`);
    for (const type of ['touchStart','touchEnd']) await send('Input.dispatchTouchEvent',{type,touchPoints:type==='touchEnd'?[]:[{x:p.x,y:p.y}]},s);
    await until(`document.getElementById('proj-menu').hidden && !${PO}.contentDocument.getElementById('thr-side').hidden`, 'phone thread');
    out.phoneOpened = await ev(`${PO}.contentWindow.sideNow()?.tid || ''`);
    out.phoneWant = ph.slice(2);
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class LeftPanelThreads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-ldt-', ignore_cleanup_errors=True)
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
        po_sid = 'ldt-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': po_sid, 'cwd': str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        ok, proj2, _ = dashboard.register_project('Garden'); assert ok, proj2
        cls.proj2 = proj2['id']
        po2 = chatroom.create_room('Garden PO', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': 'ldt-po2',
                                                  'cwd': str(Path(proj2.get('home') or proj2['path']))}])
        chatroom.post_message(po2['id'], 'user', 'Another PO')
        dashboard.assign_session_project(po2['id'], cls.proj2)
        dashboard.set_project_po(cls.proj2, po2['id'])
        cls.po2 = po2['id']
        transcript = base / 'transcripts' / 'C--po'
        transcript.mkdir()
        at = cls.at = time.time() - (len(TOPICS) - 1) * 72000 - 120   # the latest: 2 min ago

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
            if role == 'assistant':
                msg['stop_reason'] = 'end_turn'
            return {'type': role, 'timestamp': stamp, 'sessionId': po_sid, 'message': msg}
        lines, sends = [], []
        for i, topic in enumerate(TOPICS):
            off = i * 72000           # 20 h apart: ages from 6 d to today
            text = f'{topic}: what should we pick?'
            lines += [turn('user', text, off), turn('assistant', f'For {topic.lower()}, the plain one.', off + 5)]
            sends.append((text, off))
        (transcript / f'{po_sid}.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        for text, off in sends:
            threads.note_send(cls.po, '', '', [], False, text, now=at + off, new=True)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'shots': shots,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj': cls.proj, 'po': cls.po, 'proj2': cls.proj2, 'po2': cls.po2}
        script = base / 'ldt_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_the_hub_counts_each_po_chat_s_threads(self):
        self.assertEqual(self.got['summary']['motors']['n'], 7)
        self.assertEqual(self.got['summary']['garden']['n'], 0)
        self.assertEqual(self.got['carets'], {'motors': True, 'garden': False})

    def test_a_click_selects_and_unfolds_main_chat_the_5_latest_and_all(self):
        u = self.got['unfolded']
        self.assertTrue(u['selected'])
        self.assertEqual(u['expanded'], 'true')
        self.assertIn(self.proj, json.loads(u['stored']))
        names = [r['name'] for r in u['rows']]
        self.assertEqual(names, ['Main chat'] + [f'{t}: what should we pick?' for t in reversed(TOPICS)][:5] + ['All 7 threads…'])
        self.assertEqual(u['rows'][1]['age'], _age(self.at + (len(TOPICS) - 1) * 72000))
        self.assertEqual(u['rows'][5]['age'], _age(self.at + (len(TOPICS) - 5) * 72000))
        self.assertEqual(u['rows'][0]['age'], '')

    def test_a_poll_keeps_the_same_nodes(self):
        self.assertTrue(self.got['same'])

    def test_a_thread_shows_in_place_and_its_row_is_marked(self):
        rows = {r['key']: r for r in self.got['picked']['rows']}
        self.assertTrue(rows['t:' + self.got['picked']['tid']]['cur'])
        self.assertFalse(rows['main']['cur'])
        self.assertEqual(sum(r['cur'] for r in self.got['picked']['rows']), 1)

    def test_main_chat_goes_back(self):
        rows = {r['key']: r for r in self.got['main']}
        self.assertTrue(rows['main']['cur'])
        self.assertEqual(sum(r['cur'] for r in self.got['main']), 1)

    def test_ctrl_click_opens_a_tab_and_all_opens_the_threads_filter(self):
        self.assertFalse(self.got['tab']['inPlace'])
        self.assertTrue(self.got['filter'])

    def test_a_thread_of_another_project_picks_that_project(self):
        self.assertEqual(self.got['cross']['pid'], self.proj)
        self.assertEqual(self.got['cross']['lp'], self.proj)

    def test_the_caret_folds_without_selecting_and_the_fold_is_kept(self):
        f = self.got['folded']
        self.assertFalse(f['group'])
        self.assertEqual(f['expanded'], 'false')
        self.assertEqual(f['pid'], self.proj)
        self.assertFalse(self.got['reload'], 'still folded after a reload')

    def test_the_keyboard_tree(self):
        k = {x['key']: x for x in self.got['keys']}
        self.assertFalse(k['left']['group'], '← folds')
        self.assertEqual(k['left']['focus'], 'proj:' + self.proj)
        self.assertEqual(k['right']['focus'], 'proj:' + self.proj, '→ unfolds, the focus stays')
        self.assertEqual(k['right2']['focus'], 'main', '→ again: Main chat')
        self.assertTrue(k['down']['focus'].startswith('t:'))
        self.assertEqual(k['left2']['focus'], 'proj:' + self.proj, '← on a thread: its project')
        self.assertEqual(k['down2']['focus'], 't:' + self.got['firstThread'])
        self.assertNotEqual(k['down2']['ring'], 'none')
        self.assertEqual(k['enter']['tid'], self.got['firstThread'])

    def test_a_new_order_waits_while_the_pointer_is_over_the_list(self):
        h = self.got['held']
        self.assertEqual(h['over'], h['o0'])
        self.assertEqual(h['left800'], h['o0'])
        self.assertNotEqual(h['after'], h['o0'])
        self.assertEqual(h['after'][1], 't:' + h['after'][1][2:])

    def test_a_pinned_project_keeps_its_rows_in_its_row(self):
        self.assertGreaterEqual(self.got['pinned'], 6)

    def test_no_sideways_scroll(self):
        sw, cw = self.got['scroll']
        self.assertLessEqual(sw, cw)

    def test_a_phone_menu_lists_the_current_project_s_threads(self):
        ph = self.got['phone']
        self.assertEqual(ph['after'], self.proj)
        self.assertEqual([r['key'] for r in ph['rows']][0], 'main')
        self.assertEqual(ph['rows'][-1]['key'], 'all')
        self.assertEqual(len(ph['rows']), 5, 'Main chat, 3 threads, All')
        for r in ph['rows']:
            self.assertGreaterEqual(r['h'], 44)
        self.assertLessEqual(ph['scroll'][0], ph['scroll'][1])
        self.assertEqual(self.got['phoneOpened'], self.got['phoneWant'])

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
