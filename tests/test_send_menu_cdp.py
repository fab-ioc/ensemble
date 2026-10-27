"""Every menu that opens from the Send box shows whole (P227, #276).

The workspace's Send box is a frame as tall as the box (#271), so a menu
opening upward from it was cut at the frame's top edge. Over CDP, at
1728x1117 and 1440x900 (the workspace box, PO chat and a thread above it),
in a task chat's own window and at a phone's 390x844, each menu - the box's
+ menu, Send's ▾ menu, Stop's choice of agent and the / command list - is
opened with a real click (the list by its own drawing code), and measured:
every item lies inside what can be seen (the window, and the frame in the
workspace) and is what a click at its centre hits. The chats above do not
move while it is open, and closing it gives the frame back its exact height.
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
  const size=async (w,h,phone) => {
    await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:!!phone},s);
    await send('Emulation.setTouchEmulationEnabled',{enabled:!!phone, maxTouchPoints:phone ? 5 : 1},s);
  };
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<120;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);} throw Error('timeout '+label); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.rt.get('thread:po')?.el.querySelector('iframe')`;
  // The frame holding the box in the workspace (null in a chat's own window),
  // its page, and that page's window: where a menu's own code runs.
  const BOX = `(() => { const f = document.getElementById('workspace-compose-frame');
    const ws = !!f && !document.getElementById('workspace-compose').hidden;
    return ws ? {f, d:f.contentDocument, w:f.contentWindow} : {f:null, d:document, w:window}; })()`;
  // Where the chats above are: each one's place on the page and its scroll.
  const CHATS = `(() => { const {f} = ${BOX}; const list = f ? [${PO}, ${THR}] : [];
    return list.map(fr => { const r = fr.getBoundingClientRect(), m = fr.contentDocument.getElementById('msgs');
      return [Math.round(r.top), Math.round(r.height), m ? Math.round(m.scrollTop) : null]; }); })()`;
  const STATE = `(() => { const {f} = ${BOX}; return {frame: f ? Math.round(f.getBoundingClientRect().height * 10) / 10 : null,
    frameTop: f ? Math.round(f.getBoundingClientRect().top * 10) / 10 : null, chats: ${CHATS}}; })()`;
  // The menu's items as seen from the top page: inside the window and the
  // frame, and hit at their centres (through the frame, to its page).
  const MEASURE = sel => `(() => { const {f, d} = ${BOX}; const o = f ? f.getBoundingClientRect() : {left:0, top:0};
    const area = {top: Math.max(0, f ? o.top : 0), bottom: Math.min(innerHeight, f ? o.bottom : innerHeight),
      left: Math.max(0, f ? o.left : 0), right: Math.min(innerWidth, f ? o.right : innerWidth)};
    const menu = d.querySelector(${JSON.stringify(sel)}); if (!menu) return {missing: true};
    const mr = menu.getBoundingClientRect(), view = {top: mr.top + menu.clientTop, bottom: mr.top + menu.clientTop + menu.clientHeight};
    const items = [...menu.querySelectorAll('button')].filter(b => b.getClientRects().length)
      .filter(b => { const r = b.getBoundingClientRect(), y = r.top + r.height / 2; return y > view.top && y < view.bottom; });   // in the list's own scroll view
    const one = r => ({top: Math.round((o.top + r.top) * 10) / 10, bottom: Math.round((o.top + r.bottom) * 10) / 10,
      left: Math.round(o.left + r.left), right: Math.round(o.left + r.right)});
    return {area, menu: one(mr), open: !menu.hidden && menu.getClientRects().length > 0, items: items.map(b => {
      const r = b.getBoundingClientRect(), x = o.left + r.left + r.width / 2, y = o.top + r.top + r.height / 2;
      let hit = document.elementFromPoint(x, y);
      if (f) hit = hit === f ? d.elementFromPoint(x - o.left, y - o.top) : null;
      const box = one(r);
      return {text: b.textContent.trim().slice(0, 30), ...box,
        inside: box.top >= area.top - .5 && box.bottom <= area.bottom + .5 && box.left >= area.left && box.right <= area.right,
        hit: !!hit && (hit === b || b.contains(hit))};
    })}; })()`;
  const mouse = async (x, y) => {
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x, y, buttons:0}, s);
    await send('Input.dispatchMouseEvent', {type:'mousePressed', x, y, button:'left', buttons:1, clickCount:1}, s);
    await send('Input.dispatchMouseEvent', {type:'mouseReleased', x, y, button:'left', buttons:0, clickCount:1}, s);
  };
  const tap = async (x, y) => {
    await send('Input.dispatchTouchEvent', {type:'touchStart', touchPoints:[{x, y}]}, s);
    await send('Input.dispatchTouchEvent', {type:'touchEnd', touchPoints:[]}, s);
  };
  const centre = sel => ev(`(() => { const {f, d} = ${BOX}; const o = f ? f.getBoundingClientRect() : {left:0, top:0};
    const r = d.querySelector(${JSON.stringify(sel)}).getBoundingClientRect();
    return {x: Math.round(o.left + r.left + r.width / 2), y: Math.round(o.top + r.top + r.height / 2)}; })()`);
  const escape = async () => {
    await send('Input.dispatchKeyEvent', {type:'rawKeyDown', key:'Escape', code:'Escape', windowsVirtualKeyCode:27}, s);
    await send('Input.dispatchKeyEvent', {type:'keyUp', key:'Escape', code:'Escape', windowsVirtualKeyCode:27}, s);
  };
  const painted = () => ev(`new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(0))))`);
  // Each menu: how it is opened and closed, and what it is.
  const MENUS = [
    {key:'plus', sel:'.phone-tools-menu', trigger:'.phone-compose-tools summary', close:'trigger',
     prep:`document.querySelectorAll('[data-phone-action]').forEach(b => b.hidden = false)`},
    // Send now is offered while the agent works; Stop asks which agent when two do.
    {key:'send', sel:'#send-menu', trigger:'#send-more', close:'escape',
     prep:`updateStopControl = () => {}; updateSendNow = () => {}; SEND_NOW_WAY = 'into-turn'; document.getElementById('send-more').hidden = false`},
    {key:'stop', sel:'#stop-choices', trigger:'#stop-agent', close:'escape',
     prep:`updateStopControl = () => {}; STOP_TARGETS = [{identity:'claude', agent:'claude'}, {identity:'codex', agent:'codex'}, {identity:'claude-2', agent:'claude'}];
       STOP_LAST = -Infinity; STOP_PENDING = false; document.getElementById('stop-wrap').hidden = false; document.getElementById('stop-agent').disabled = false`},
    {key:'slash', sel:'#slashpop', close:'escape',
     prep:`STOP_TARGETS = []; document.getElementById('stop-wrap').hidden = true; document.getElementById('send-more').hidden = true`,
     open:`document.getElementById('input').value = '/'; document.getElementById('input').focus(); SL_POP = {items: Array.from({length: 14}, (_, i) => ({name: 'command-' + i, description: 'What command ' + i + ' does', source: 'skill'})), sel: 0, ident: ''}; slDraw()`},
  ];
  async function menus(label, phone) {
    const out = {};
    for (const m of MENUS) {
      if (m.prep) await ev(`${BOX}.w.eval(${JSON.stringify(m.prep)}); 0`);
      await sleep(250); await painted();
      const before = await ev(STATE);
      if (m.trigger) { const at = await centre(m.trigger); await (phone ? tap : mouse)(at.x, at.y); }
      else await ev(`${BOX}.w.eval(${JSON.stringify(m.open)}); 0`);
      await painted(); await sleep(150); await painted();
      const during = await ev(STATE), got = await ev(MEASURE(m.sel));
      if (m.close === 'escape') await escape();
      else { const at = await centre(m.trigger); await (phone ? tap : mouse)(at.x, at.y); }
      await painted(); await sleep(250); await painted();
      const after = await ev(STATE);
      const closed = await ev(`(() => { const el = ${BOX}.d.querySelector(${JSON.stringify(m.sel)}); const dt = el.closest('details'); return dt ? !dt.open : !el.getClientRects().length; })()`);
      // Again, closed by a press beside it: above the box, left of the menu
      // (in the workspace, the frame's room over the chats).
      if (m.trigger) { const at = await centre(m.trigger); await (phone ? tap : mouse)(at.x, at.y); }
      else await ev(`${BOX}.w.eval(${JSON.stringify(m.open)}); 0`);
      await painted(); await sleep(150); await painted();
      const beside = await ev(`(() => { const {f, d} = ${BOX}; const o = f ? f.getBoundingClientRect() : {left:0, top:0};
        const r = d.querySelector(${JSON.stringify(m.sel)}).getBoundingClientRect();
        return {x: Math.round(o.left + 4), y: Math.round(o.top + r.top + 4), open: !!r.height}; })()`);
      await (phone ? tap : mouse)(beside.x, beside.y);
      await painted(); await sleep(250); await painted();
      const besideAfter = await ev(STATE);
      const besideClosed = await ev(`(() => { const el = ${BOX}.d.querySelector(${JSON.stringify(m.sel)}); const dt = el.closest('details'); return dt ? !dt.open : !el.getClientRects().length; })()`);
      out[m.key] = {before, during, after, closed, beside: {...beside, closed: besideClosed, after: besideAfter}, ...got};
    }
    return out;
  }
  const out = {};
  try {
    for (const [w, h] of [[1728,1117],[1440,900]]) {
      await size(w, h);
      await send('Page.navigate',{url:A.base+'/'},s);
      await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
      await ev(`localStorage.removeItem('cd-compose-h'); SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
      await until(`!!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`,'PO chat');
      await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
      await until(`!!${THR}?.contentDocument?.querySelector('#thread-reply')`,'thread panel');
      await until(`${BOX}.f?.contentDocument?.getElementById('compose-size') && typeof ${BOX}.w.slDraw === 'function'`,'workspace box');
      await sleep(900);
      out['workspace' + w] = await menus('workspace ' + w, false);
    }
    // A task chat's own window, then the same chat on a phone.
    for (const [key, w, h, phone] of [['task', 1440, 900, false], ['phone', 390, 844, true]]) {
      await size(w, h, phone);
      await send('Page.navigate',{url:A.base+'/session?id='+encodeURIComponent(A.task)},s);
      await until(`!!document.getElementById('compose') && document.getElementById('msgs').children.length > 10 && typeof slDraw === 'function'`,'task chat');
      await sleep(600);
      out[key] = await menus(key, phone);
      // The height bar (P229): a faded grip at rest, the accent across the box held, in every theme.
      if (!phone) out.bar = await ev(`(() => {
        const lum = c => { const m = c.match(/[\\d.]+/g).map(Number), f = v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
          return .2126 * f(m[0]) + .7152 * f(m[1]) + .0722 * f(m[2]); };
        const ratio = (a, b) => { const x = lum(a), y = lum(b); return Math.round((Math.max(x, y) + .05) / (Math.min(x, y) + .05) * 100) / 100; };
        const bar = document.getElementById('compose-size'), box = document.getElementById('compose'), out = {};
        for (const t of ['light', 'dark', 'dim', 'paper', 'contrast', 'fjord', 'intellij-dark']) {
          document.documentElement.dataset.theme = t;
          const rest = getComputedStyle(bar, '::after'), bg = getComputedStyle(box).backgroundColor;
          const r = {shown: !bar.hidden, bg, rest: rest.backgroundColor, restW: parseFloat(rest.width), ratio: ratio(rest.backgroundColor, bg)};
          bar.classList.add('held');
          const held = getComputedStyle(bar, '::after');
          r.held = held.backgroundColor; r.heldW = parseFloat(held.width); r.boxW = bar.getBoundingClientRect().width;
          r.accent = getComputedStyle(document.body).getPropertyValue('--accent').trim();
          bar.classList.remove('held');
          out[t] = r;
        }
        return out; })()`);
    }
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class SendMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-send-menu-', ignore_cleanup_errors=True)
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
        static = os.environ.get('BASELINE_STATIC_DIR', '')
        if static:
            cls.patches.append(mock.patch.object(dashboard, 'STATIC_DIR', Path(static)))
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        sid = 'menu-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        task = chatroom.create_room('Task chat', [{'identity':'claude','agent':'claude','sessionId':'menu-task','cwd':str(home)}])
        dashboard.assign_session_project(task['id'], cls.proj)
        dashboard.assign_task_number(task['id'], cls.proj)
        cls.task = task['id']
        transcript = base / 'transcripts' / 'C--menu'; transcript.mkdir()
        at = time.time() - 7200
        def turn(role, text, offset, sid=sid):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
        lines = []
        for n in range(30):
            lines += [turn('user', f'Question {n} with a few words in it.', 10 * n + 1),
                      turn('assistant', f'Answer {n}: a reply long enough to wrap onto a second line in a narrow panel of the dock.', 10 * n + 2)]
        lines += [turn('user', 'A conversation with a thread.', 400), turn('assistant', 'The thread reply.', 401)]
        (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        (base / 'transcripts' / 'C--task').mkdir()
        lines = [turn('user' if n % 2 else 'assistant', f'Task message {n}: enough words to take a line or two in the chat.', n + 1, 'menu-task')
                 for n in range(40)]
        (base / 'transcripts' / 'C--task' / 'menu-task.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj,'po':cls.po,'task':cls.task}
        script = base / 'send_menu_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=240)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        dump = os.environ.get('SEND_MENU_DUMP', '')
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_every_menu_shows_whole(self):
        for key in ('workspace1728', 'workspace1440', 'task', 'phone'):
            for menu, m in self.got[key].items():
                with self.subTest(at=key, menu=menu):
                    self.assertTrue(m.get('open'), m)
                    self.assertGreaterEqual(len(m['items']), 3 if menu != 'send' else 1, m)
                    for item in m['items']:
                        self.assertTrue(item['inside'], (item, m['area']))
                        self.assertTrue(item['hit'], (item, m['area']))
                    if menu == 'slash':      # the list keeps its height: several commands at once, not one row
                        self.assertGreaterEqual(len(m['items']), 5, m)

    def test_height_bar_shows_at_rest(self):
        for theme, b in self.got['bar'].items():
            with self.subTest(theme=theme):
                print(theme, b)
                self.assertTrue(b['shown'], b)
                self.assertEqual(b['restW'], 32, b)
                # Seen but quiet: about 2:1 on the box; High contrast's quietest mark is 4.5:1.
                if theme == 'contrast':
                    self.assertTrue(3 <= b['ratio'] <= 5, b)
                else:
                    self.assertTrue(1.5 <= b['ratio'] <= 3, b)
                self.assertNotEqual(b['held'], b['rest'], b)
                self.assertEqual(b['heldW'], b['boxW'], b)

    def test_chats_stay_and_frame_comes_back(self):
        for key in ('workspace1728', 'workspace1440', 'task', 'phone'):
            for menu, m in self.got[key].items():
                with self.subTest(at=key, menu=menu):
                    self.assertTrue(m['closed'], m)
                    self.assertEqual(m['during']['chats'], m['before']['chats'], m)
                    self.assertEqual(m['after']['chats'], m['before']['chats'], m)
                    self.assertEqual(m['after']['frame'], m['before']['frame'], m)
                    self.assertEqual(m['after']['frameTop'], m['before']['frameTop'], m)
                    # A press beside the menu closes it too and gives the room back.
                    # (A phone's / list stays through a tap on the chat, as it did
                    # before: nothing of it reaches over the chat there.)
                    self.assertTrue(m['beside']['open'], m['beside'])
                    if (key, menu) != ('phone', 'slash'):
                        self.assertTrue(m['beside']['closed'], m['beside'])
                    self.assertEqual(m['beside']['after']['frame'], m['before']['frame'], m['beside'])
                    self.assertEqual(m['beside']['after']['frameTop'], m['before']['frameTop'], m['beside'])
                    self.assertEqual(m['beside']['after']['chats'], m['before']['chats'], m['beside'])


if __name__ == '__main__':
    unittest.main()
