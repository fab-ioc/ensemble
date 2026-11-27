"""File comments in a dock panel go to the conversation on screen (#429,
GitHub issue 72). In headless Chrome over CDP, against a hub in a thread.

Motors has a PO chat with three threads and a file in its folder. Checked:

* a file opened in its own tab, a thread picked in its tray: that pick is
  kept for the file there (``cd-fvd:``), as before;
* the same file as a panel beside the PO's main chat: its tray sends to the
  main chat, not to the pick kept for its own tab;
* a thread shown in place: the panel's tray goes to that thread at once,
  and to the next one picked; Main chat back: the main chat;
* a place picked by hand in the panel stays across a thread switch, and is
  not kept for the file's own tab;
* the hub was asked with the conversation on screen (``at`` and ``thread``);
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
TOPICS = ['Wheel sizes', 'Brake pads', 'Paint colours']
OWNER_ASKS = []

CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--hide-scrollbars', 'about:blank'], {stdio:['ignore','ignore','pipe']});
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
  await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,140)+' '+JSON.stringify(r.exceptionDetails).slice(0,600)); return r.result.value; };
  const until=async (e, what, n=160) => { for(let i=0;i<n;i++){ try {if(await ev(e)) return;}catch(x){} await sleep(150); } throw Error('timeout '+(what||e)); };
  const shot=async name => { if(!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png'},s); fs.writeFileSync(path.join(A.shots,name+'.png'),Buffer.from(r.data,'base64')); };
  const P=JSON.stringify(A.proj), PO=JSON.stringify(A.po);
  // The file panel's viewer, and where its tray sends.
  const VIEWER = `(() => { const id = [...PD.rt.keys()].find(k => k.startsWith('file:') && k.endsWith('a.md')); const e = id && PD.rt.get(id); return e && e.el.querySelector('iframe.wsp-frame'); })()`;
  const inV = js => `(() => { const f = ${VIEWER}; return f.contentWindow.eval(${JSON.stringify(js)}); })()`;
  const DEST = inV(`JSON.stringify({ state: DEST_STATE, room: DEST.room || '', thread: DEST.thread || '', kind: DEST.kind || '', screen: typeof SCREEN === 'undefined' || SCREEN === undefined ? null : SCREEN })`);
  const dest = async () => JSON.parse(await ev(DEST));
  const destIs = (room, thread) => `(() => { try { const d = JSON.parse(${DEST}); return d.state === 'ok' && d.room === ${JSON.stringify(room)} && d.thread === ${JSON.stringify(thread)}; } catch (e) { return false; } })()`;
  const out={};
  try {
    // 1. The file in its own tab, opened from the PO's chat: a thread picked.
    const own = '/fileview?path=' + encodeURIComponent(A.file) + '&room=' + encodeURIComponent(A.po);
    await send('Page.navigate',{url:A.base+own},s);
    await until(`typeof FVC !== 'undefined' && document.readyState === 'complete' && DEST_STATE === 'ok'`, 'own tab');
    await ev(`(() => { const c = DEST_CHOICES.find(x => x.thread === ${JSON.stringify(A.tids[0])}); setDest(destKey(c)); return 1; })()`);
    out.own = await ev(`({ panel: SCREEN !== undefined, thread: DEST.thread || '', kept: localStorage.getItem('cd-fvd:' + fvList()) })`);
    // 2. The dashboard: the file as a panel beside the PO's main chat.
    await send('Page.addScriptToEvaluateOnNewDocument',{source:"try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {} window.ensBootOpen = false;"},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted === true && typeof PROJECTS !== 'undefined' && !!PROJECTS && PROJECTS.projects.length > 0`, 'boot');
    await ev(`SW_DONE_OPEN = true; SELECTED_PROJECT=${P}; PROJECT_TAB='tasks'; SB_DEST=''; renderRows(); 0`);
    await until(`!!PD.dock && document.body.classList.contains('po-dock') && PD.restored && !!PD.els.workspace.querySelector('.wsp-tree .wse.file[data-path]')`, 'PO screen');
    await until(`!!PO_PANEL.querySelector('iframe.po-session:not([hidden])') && typeof PO_PANEL.querySelector('iframe.po-session:not([hidden])').contentWindow.sideOpen === 'function'`, 'PO chat');
    await ev(`[...PD.els.workspace.querySelectorAll('.wsp-tree .wse.file[data-path]')].find(x => x.dataset.path.endsWith('a.md')).click(); 0`);
    await until(`(() => { const f = ${VIEWER}; try { return !!f && f.contentWindow.eval("typeof DEST_STATE !== 'undefined' && DEST_STATE") === 'ok'; } catch (e) { return false; } })()`, 'panel viewer')
      .catch(async e => { throw Error(e.message + ' ' + await ev(`JSON.stringify({ rt: [...PD.rt.entries()].map(([k, v]) => [k, v.kind]), frames: [...document.querySelectorAll('iframe.wsp-frame')].map(f => f.src.slice(0, 200)), panels: wsPanelsMode && wsPanelsMode() })`)); });
    await sleep(400);
    out.main = await dest();
    out.mainKept = await ev(inV(`localStorage.getItem('cd-fvd:' + fvList())`));
    // A comment made and dropped in the panel leaves the own tab's kept pick alone (review 1).
    await ev(inV(`(() => { const cid = FVC.id(); FVC.put({ cid, at: FVC.now(), note: 'gone soon', view: CUR_VIEW, start: -1, end: -1, quote: '' }); renderTray(); dropCmts([cid]); renderTray(); return 1; })()`));
    out.clearKept = await ev(inV(`localStorage.getItem('cd-fvd:' + fvList())`));
    // One kept, so the tray shows in the pictures.
    await ev(inV(`(() => { FVC.put({ cid: FVC.id(), at: FVC.now(), note: 'Seven to one is too low for the hill route.', view: CUR_VIEW, start: -1, end: -1, quote: '' }); renderTray(); return 1; })()`));
    out.box = await ev(`({ shown: !document.getElementById('workspace-compose').hidden, target: WC.target })`);
    // 3. A thread in place, then another: the tray follows.
    await ev(`ldtOpen(${P}, ${JSON.stringify(A.tids[1])}); 0`);
    await until(`!!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(A.tids[1])}`, 'thread in place');
    await until(destIs(A.po, A.tids[1]), 'the tray on the thread', 80).catch(() => {});
    out.t1 = await dest();
    await sleep(500);
    await shot(A.shotName || 'fps-thread');
    await ev(`ldtOpen(${P}, ${JSON.stringify(A.tids[2])}); 0`);
    await until(`!!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(A.tids[2])}`, 'next thread');
    await until(destIs(A.po, A.tids[2]), 'the tray on the next thread', 80).catch(() => {});
    out.t2 = await dest();
    // 4. Main chat back.
    await ev(`ldtOpen(${P}, ''); 0`);
    await until(`!(PD.thrIn && pdThreadInShown())`, 'main');
    await until(destIs(A.po, ''), 'the tray on the main chat', 80).catch(() => {});
    out.back = await dest();
    // 5. A pick by hand (the first thread) made while the hub's answer for a
    // switch is still on its way: the pick wins (review 1).
    await ev(inV(`(() => { const of = window.fetch; window.__held = 0; window.__hold = true;
      window.fetch = async (u, o) => { if (String(u).includes('/api/file/owner') && window.__hold) { window.__held++; await new Promise(r => { window.__rel = r; }); } return of(u, o); }; return 1; })()`));
    await ev(`ldtOpen(${P}, ${JSON.stringify(A.tids[1])}); 0`);
    await until(`!!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(A.tids[1])}`, 'thread again');
    await until(inV(`window.__held > 0`), 'an ask held');
    await ev(inV(`(() => { const c = DEST_CHOICES.find(x => x.thread === ${JSON.stringify(A.tids[0])}); setDest(destKey(c)); window.__hold = false; window.__rel(); return 1; })()`));
    await sleep(1500);
    out.race = await dest();
    // 6. That pick stays across another switch, and is not kept for the file.
    await ev(`ldtOpen(${P}, ${JSON.stringify(A.tids[2])}); 0`);
    await until(`!!PD.thrIn && pdThreadInShown() && PD.thrIn.tid === ${JSON.stringify(A.tids[2])}`, 'thread once more');
    await sleep(1500);
    out.hand = await dest();
    out.handKept = await ev(inV(`localStorage.getItem('cd-fvd:' + fvList())`));
    // 7. A box under each chat (#407), a thread in a tab of its own: the tray
    // goes to that tab while it is in front, to the project's tab once that is.
    // The hub's saved choice wins over the page's cache: set both, as a save does.
    await ev(`ldtOpen(${P}, ''); if (PREFS) PREFS.sendBox = 'pane'; localStorage.setItem('cd-send-box', 'pane'); apLook(); 0`);
    // Forget step 6's pick by hand, as a fresh panel would.
    await ev(inV(`HAND = null; loadDest(); 1`));
    await until(`document.documentElement.dataset.sendBox === 'pane' && !(PD.thrIn && pdThreadInShown())`, 'pane boxes');
    await until(destIs(A.po, ''), 'the tray on the main chat, pane boxes', 80).catch(() => {});
    out.paneMain = await dest();
    await ev(`ldtOpen(${P}, ${JSON.stringify(A.tids[1])}, { tab: true, title: 'Brake pads' }); 0`);
    const TAB = `pdThreadTabId(${PO}, ${JSON.stringify(A.tids[1])}, '')`;
    await until(`PD.dock.panels().some(x => x.id === ${TAB}) && PD.dock.frontOf(${TAB}) === ${TAB}`, 'thread tab');
    await until(destIs(A.po, A.tids[1]), 'the tray on the thread tab', 80).catch(() => {});
    out.paneTab = await dest();
    out.paneTabShown = await ev(`pdConvShownId()`);
    // The person presses the project's tab (it may sit in another stack, both shown).
    out.sameStack = await ev(`(() => { try { return PD.dock.stackOf ? PD.dock.stackOf('po-chat') === PD.dock.stackOf(${TAB}) : null; } catch (e) { return null; } })()`);
    await ev(`PD_ROOT.querySelector('.dk-tab[data-dk-tab="po-chat"]').click(); 0`);
    await until(destIs(A.po, ''), 'the tray back on the main chat', 80).catch(() => {});
    out.paneBack = await dest();
    out.paneDbg = await ev(`JSON.stringify({ last: WC.convLast, shown: pdConvIds().filter(pdConvShown), ids: pdConvIds(), tab: !!PD_ROOT.querySelector('.dk-tab[data-dk-tab="po-chat"]'), t: wcPanelTarget('po-chat'), front: PD.dock.frontOf('po-chat') })`);
    out.errors = errors;
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, 'node and Chrome are needed')
class FilePanelScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-fps-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        orig_get = dashboard.Handler.do_GET

        def counted(self):
            if self.path.startswith('/api/file/owner'):
                OWNER_ASKS.append(self.path)
            return orig_get(self)

        for p in [
            mock.patch.object(dashboard, 'PROJECTS_ROOT', root),
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
            mock.patch.object(dashboard.Handler, 'do_GET', counted),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        cls.file = home / 'a.md'
        cls.file.write_text('# A\n\nThe gearbox runs at seven to one.\n', encoding='utf-8')
        po_sid = 'fps-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': po_sid, 'cwd': str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        transcript = base / 'transcripts' / 'C--po'
        transcript.mkdir()
        at = time.time() - 3 * 3600

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
            if role == 'assistant':
                msg['stop_reason'] = 'end_turn'
            return {'type': role, 'timestamp': stamp, 'sessionId': po_sid, 'message': msg}
        lines, sends = [], []
        for i, topic in enumerate(TOPICS):
            off = i * 600
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
        base_url = f'http://127.0.0.1:{cls.server.server_address[1]}'
        # The threads' ids, oldest first, as the chat lists them.
        import urllib.request
        tids = []
        for _ in range(60):
            with urllib.request.urlopen(f'{base_url}/api/room/channel?id={cls.po}&threads=1&n=1&lite=1') as r:
                j = json.loads(r.read().decode('utf-8'))
            lst = [t for t in (j.get('threadList') or []) if t.get('id') and t['id'] != 'team:activity']
            if len(lst) >= 3:
                tids = [t['id'] for t in sorted(lst, key=lambda t: t.get('createdAt') or t.get('updatedAt') or 0)]
                break
            time.sleep(0.2)
        assert len(tids) == 3, tids
        cls.tids = tids
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'shots': shots, 'base': base_url,
                'proj': cls.proj, 'po': cls.po, 'file': str(cls.file), 'tids': tids,
                'shotName': os.environ.get('ENSEMBLE_SHOT_NAME', '')}
        script = base / 'fps_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_its_own_tab_keeps_the_pick_for_the_file(self):
        o = self.got['own']
        self.assertFalse(o['panel'])
        self.assertEqual(o['thread'], self.tids[0])
        self.assertIn(self.tids[0], o['kept'] or '')

    def test_a_panel_beside_the_main_chat_sends_there_not_to_the_kept_pick(self):
        m = self.got['main']
        self.assertEqual((m['room'], m['thread'], m['kind']), (self.po, '', 'po'), m)
        self.assertEqual(m['screen'], {'room': self.po, 'thread': '', 'title': m['screen']['title']})
        self.assertIn(self.tids[0], self.got['mainKept'] or '')   # still there, for its own tab

    def test_the_tray_follows_the_thread_on_screen(self):
        self.assertEqual((self.got['t1']['room'], self.got['t1']['thread'], self.got['t1']['kind']), (self.po, self.tids[1], 'thread'), self.got['t1'])
        self.assertEqual(self.got['t1']['screen']['thread'], self.tids[1])
        self.assertEqual((self.got['t2']['room'], self.got['t2']['thread']), (self.po, self.tids[2]), self.got['t2'])
        self.assertEqual((self.got['back']['room'], self.got['back']['thread'], self.got['back']['kind']), (self.po, '', 'po'), self.got['back'])

    def test_a_dropped_comment_in_the_panel_keeps_the_own_tabs_pick(self):
        self.assertIsNotNone(self.got['mainKept'])
        self.assertEqual(self.got['clearKept'], self.got['mainKept'])

    def test_a_pick_made_while_an_answer_is_on_its_way_wins(self):
        r = self.got['race']
        self.assertEqual((r['room'], r['thread']), (self.po, self.tids[0]), r)
        self.assertEqual(r['screen']['thread'], self.tids[1])

    def test_a_pick_by_hand_stays_and_is_not_kept_for_the_file(self):
        h = self.got['hand']
        self.assertEqual((h['room'], h['thread']), (self.po, self.tids[0]), h)
        self.assertEqual(h['screen']['thread'], self.tids[2])   # the viewer knows what is on screen
        # The own tab's kept pick is the one it made itself, untouched by the panel.
        self.assertEqual(self.got['handKept'], self.got['mainKept'])

    def test_the_hub_was_asked_with_the_conversation_on_screen(self):
        from urllib.parse import unquote
        asks = [unquote(a) for a in OWNER_ASKS if '&at=' in a]
        self.assertTrue(any(f'thread={self.tids[1]}' in a for a in asks), OWNER_ASKS)
        self.assertTrue(any(f'thread={self.tids[2]}' in a for a in asks), OWNER_ASKS)

    def test_with_a_box_under_each_chat_a_thread_tab_in_front_is_the_place(self):
        self.assertEqual((self.got['paneMain']['room'], self.got['paneMain']['thread']), (self.po, ''), self.got['paneMain'])
        self.assertEqual((self.got['paneTab']['room'], self.got['paneTab']['thread'], self.got['paneTab']['kind']), (self.po, self.tids[1], 'thread'), (self.got['paneTab'], self.got['paneTabShown']))
        self.assertEqual((self.got['paneBack']['room'], self.got['paneBack']['thread']), (self.po, ''), (self.got['paneBack'], self.got['paneDbg'], self.got['sameStack']))

    def test_the_page_threw_nothing(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
