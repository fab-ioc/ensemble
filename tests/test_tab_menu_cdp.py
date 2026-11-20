"""Issue 66: the tab menu's Split Right / Left / Down / Up, Move to Other Stack,
Unsplit, Close Other Tabs and Close Tabs to the Right.

In headless Chrome over CDP, with real right-clicks on tabs and real clicks on the
menu's items: the menu a right-click opens is the tab's ⋯ menu (one list), each item
does what it says to the dock's layout, and an item that cannot run is greyed with
its reason. ``ENSEMBLE_SHOTS`` keeps screenshots. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
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
  const size=(w,h) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,160)+' '+JSON.stringify(r.exceptionDetails).slice(0,600)); return r.result.value; };
  const until=async (expression,label,n=150) => { for(let i=0;i<n;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);}
    throw Error('timeout '+label+' '+JSON.stringify(await ev(TREE).catch(e => String(e)))); };
  const snap = async name => { if (!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},s);
    require('fs').writeFileSync(A.shots+'/'+name+'.png',Buffer.from(r.data,'base64')); };
  // The page's dock as a string: a stack [a,b*] (* in front), a split row(..|..) or col(..|..), floats after ~.
  const TREE = `(() => { const L = PD.dock.layout();
    const nm = p => ({ 'po-chat': 'T', ['chat:' + ${JSON.stringify(A.tasks[0])}]: 'A', ['chat:' + ${JSON.stringify(A.tasks[1])}]: 'B', ['chat:' + ${JSON.stringify(A.tasks[2])}]: 'C' })[p] || p;
    const s = n => !n ? '-' : n.t === 'stack' ? '[' + n.panels.map(p => nm(p) + (p === n.active ? '*' : '')).join(',') + ']'
      : n.dir + '(' + n.kids.map(s).join('|') + ')';
    return s(L.root) + (L.floats.length ? ' ~' + L.floats.map(f => s(f.stack)).join(' ~') : ''); })()`;
  // The middle of an element (a tab: its name, not its × or pin), or the point (fx, fy) of its box.
  const at = async (sel, fx = 0.5, fy = 0.5) => ev(`(() => { const e = ${sel}; if (!e) throw Error('none: ' + ${JSON.stringify(sel)});
    const r = e.getBoundingClientRect(); return { x: Math.round(r.left + r.width * ${fx}), y: Math.round(r.top + r.height * ${fy}) }; })()`);
  const tabSel = id => `document.querySelector('[data-dk-tab=${JSON.stringify(id)}]')`;
  const stackOf = id => `document.querySelector('[data-dk-tab=${JSON.stringify(id)}]').closest('.dk-stack')`;
  const mouse = (type, p, buttons) => send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', buttons, clickCount: type === 'mouseMoved' ? 0 : 1 }, s);
  const PREVIEW = `(() => { const p = PD_ROOT.querySelector('.dk-preview'); if (!p || p.hidden) return null;
    const r = p.getBoundingClientRect(); return { side: p.dataset.side, x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) }; })()`;
  // A real press on `from` and moves there in steps (the button held); the point it ends at.
  const press = async (from, to) => {
    const p = await at(from.sel, from.fx, from.fy), q = await at(to.sel, to.fx, to.fy);
    await mouse('mouseMoved', p, 0); await sleep(60);
    await mouse('mousePressed', p, 1); await sleep(60);
    await moveTo(p, q);
    return q;
  };
  const moveTo = async (p, q) => {
    for (let i = 1; i <= 12; i++) { await mouse('mouseMoved', { x: Math.round(p.x + (q.x - p.x) * i / 12), y: Math.round(p.y + (q.y - p.y) * i / 12) }, 1); await sleep(25); }
    await sleep(120);
  };
  // What the dock is after a release at q; the clicks that reached the window since the run began.
  const release = async q => {
    await mouse('mouseReleased', q, 0);
    await sleep(900);
    return { tree: await ev(TREE), boxAfter: await ev(PREVIEW), clicks: await ev(`window.__clicks`) };
  };
  // A real drag: press on `from`, move there in steps, look at the box, release.
  const drag = async (from, to) => {
    const q = await press(from, to);
    const box = await ev(PREVIEW);
    return { box, ...await release(q) };
  };
  // The menu open now (the last one: a submenu after its menu): its rows, a disabled one marked "~", a submenu "▸".
  const MENU = `(() => { const ms = [...document.querySelectorAll('.dk-menu')]; if (!ms.length) return null;
    return ms.map(m => [...m.querySelectorAll('button[role^="menuitem"]')].map(b => (b.getAttribute('aria-disabled') === 'true' ? '~' : '')
      + b.textContent.replace('▸', '').trim() + (b.dataset.dkSub ? '▸' : '') + (b.title ? ' {' + b.title + '}' : ''))); })()`;
  const rightClick = async sel => {
    await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0`); await sleep(100);
    const p = await at(sel);
    await mouse('mouseMoved', p, 0); await sleep(40);
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'right', buttons: 2, clickCount: 1 }, s);
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'right', buttons: 0, clickCount: 1 }, s);
    await sleep(300);
    return ev(MENU);
  };
  // A real click on the row named `label` of the menu open now (the last one first).
  const item = label => `[...document.querySelectorAll('.dk-menu')].reverse().map(m => [...m.querySelectorAll('button[role^="menuitem"]')]
    .find(b => b.textContent.replace('▸', '').trim() === ${JSON.stringify(label)})).find(Boolean)`;
  const click = async label => {
    const p = await at(item(label));
    await mouse('mouseMoved', p, 0); await sleep(150);
    await mouse('mousePressed', p, 1); await mouse('mouseReleased', p, 0);
    await sleep(700);
  };
  const out = { runs: {} };
  try {
    await size(1600, 1000);
    const {identifier:noBootOpen} = await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.ensBootOpen = false;'},s);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`window.ensBooted===true`,'booted');
    await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:noBootOpen},s);
    await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`,'rows');
    await ev(`openProjectPage(${JSON.stringify(A.proj)}); 0`);
    await until(`PD.pid===${JSON.stringify(A.proj)} && !!PD.dock`, 'project');
    for (const r of A.tasks) await ev(`pdChatOpen(${JSON.stringify(r)}); 0`);
    await until(`${JSON.stringify(A.tasks)}.every(r => PD.rt.has('chat:'+r))`, 'tabs');
    await ev(`PD.dock.activate('po-chat'); 0`);
    await sleep(800);
    const [TA, TB, TC] = A.tasks.map(r => 'chat:' + r);
    await ev(`window.__L0 = JSON.stringify(PD.dock.layout()); 0`);
    // (A run that closed tabs: they open again first.)
    const restore = async () => {
      for (const r of A.tasks) if (!(await ev(`PD.rt.has('chat:' + ${JSON.stringify(r)})`))) await ev(`pdChatOpen(${JSON.stringify(r)}); 0`);
      await until(`${JSON.stringify(A.tasks)}.every(r => PD.rt.has('chat:'+r))`, 'tabs again');
      await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      PD.dock._replaceLayout(JSON.parse(__L0)); PD.dock.activate('po-chat'); 0`); await sleep(500); };
    out.names = await ev(`({ A: PD.dock.title(${JSON.stringify(TA)}), B: PD.dock.title(${JSON.stringify(TB)}), C: PD.dock.title(${JSON.stringify(TC)}) })`);
    // A run: set the dock up, right-click a tab, maybe click items; the menu seen, the layout after.
    const run = async (what, setup, tab, labels, shot) => {
      await restore();
      for (const x of setup || []) await ev(x + '; 0');
      if (setup && setup.length) await sleep(600);
      const before = await ev(TREE);
      const menu = await rightClick(tab);
      if (shot) await snap(shot);
      for (const l of labels || []) await click(l);
      if (!(labels || []).length) await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0`);
      await sleep(300);
      out.runs[what] = { before, menu, tree: await ev(TREE), open: await ev(`document.querySelectorAll('.dk-menu').length`) };
    };
    const side2 = [`PD.dock.dockEdge(${JSON.stringify(TA)}, 'right')`, `PD.dock.moveTo(${JSON.stringify(TB)}, {kind:'stack', stack:PD.dock.layout().root.kids[1]}, 'center')`];
    // The menu of a task's tab in the top bar, and the same tab's ⋯ (one list).
    await run('bar-menu', [], tabSel(TA), [], 'bar-menu');
    await restore();
    await ev(`PD.dock.activate(${JSON.stringify(TA)}); 0`); await sleep(300);
    // From a right-click and from ⋯ on a side stack: the same rows.
    await run('side-menu', side2, tabSel(TA), []);
    await restore();
    for (const x of side2) await ev(x + '; 0');
    await sleep(600);
    await ev(`PD.dock.activate(${JSON.stringify(TA)}); 0`); await sleep(300);
    {
      const dots = `document.querySelector('[data-dk-tab=${JSON.stringify(TA)}]').closest('.dk-stack').querySelector('[data-dk-act="menu"]')`;
      const p = await at(dots);
      await mouse('mouseMoved', p, 0); await sleep(60);
      await mouse('mousePressed', p, 1); await mouse('mouseReleased', p, 0); await sleep(400);
      out.dotsMenu = await ev(MENU);
      await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); 0`);
    }
    // Each split from the top bar.
    for (const l of ['Split Right', 'Split Left', 'Split Down', 'Split Up']) await run('bar:' + l, [], tabSel(TA), [l]);
    // A split from a side stack.
    await run('side:Split Down', side2, tabSel(TA), ['Split Down']);
    // Move to Other Stack: into B's stack on the right.
    await run('bar:move', [`PD.dock.dockEdge(${JSON.stringify(TB)}, 'right')`], tabSel(TA), ['Move to Other Stack', out.names.B + ', on the right']);
    // Back to the top bar from the right.
    await run('side:move-bar', side2, tabSel(TA), ['Move to Other Stack', 'Top bar, on the left']);
    // Unsplit: the stack's tabs go back to the top bar.
    await run('side:unsplit', side2, tabSel(TA), ['Unsplit']);
    // Close Other Tabs, Close Tabs to the Right; a pinned tab stays.
    await run('bar:close-others', [], tabSel(TB), ['Close Other Tabs']);
    await run('bar:close-right', [], tabSel(TB), ['Close Tabs to the Right']);
    await run('bar:close-others-pinned', [`pdWsPin(${JSON.stringify(TC)}, true)`], tabSel(TB), ['Close Other Tabs']);
    await ev(`pdWsPin(${JSON.stringify(TC)}, false); 0`);
    // A tool alone in its stack: no split, no stack to move to (the top bar is not its), Unsplit puts it on its strip.
    await run('tool-menu', [`PD.dock.setViewMode('board', 'pinned')`], tabSel('board'), []);
    await run('tool:unsplit', [`PD.dock.setViewMode('board', 'pinned')`], tabSel('board'), ['Unsplit']);
    out.toolMode = await ev(`PD.dock.viewMode('board')`);
    // A tab in a floating window: its menu has the same rows; a window holds one stack, so no split.
    const fl = id => `PD.dock.float(${JSON.stringify(id)})`;
    await run('float-menu', [fl(TA)], tabSel(TA), []);
    await run('float:unsplit', [fl(TA)], tabSel(TA), ['Unsplit']);
    await run('float:move-bar', [fl(TA)], tabSel(TA), ['Move to Other Stack', 'Top bar']);
    // From the top bar into the window.
    await run('bar:move-float', [fl(TA)], tabSel(TB), ['Move to Other Stack', out.names.A + ', a window']);
    // A minimised window's tab has them too.
    await run('min-float-menu', [fl(TA), `PD.dock.toggleMin(${JSON.stringify(TA)})`], tabSel(TA), []);
    // The conversation floated: Unsplit takes a side stack's tabs into its window; in that window it is greyed.
    await run('conv-float:unsplit', [...side2, fl('po-chat')], tabSel(TA), ['Unsplit']);
    await run('conv-float-menu', [...side2, fl('po-chat'), `PD.dock.moveTo(${JSON.stringify(TC)}, {kind:'stack', stack:PD.dock.layout().floats[0].stack}, 'center')`], tabSel(TC), []);
    await run('conv-float:move-out', [...side2, fl('po-chat'), `PD.dock.moveTo(${JSON.stringify(TC)}, {kind:'stack', stack:PD.dock.layout().floats[0].stack}, 'center')`], tabSel(TC), ['Move to Other Stack', out.names.A + ' · ' + out.names.B]);
    // The conversation's own tab, in its window: its Split and Move are greyed (the other tabs move).
    await run('conv-own-menu', [...side2, fl('po-chat')], tabSel('po-chat'), []);
    // The layout a menu made stays after a reload.
    await run('reload:split', [], tabSel(TA), ['Split Right']);
    out.beforeReload = await ev(TREE);
    await send('Page.reload', {}, s);
    await until(`window.ensBooted===true && !!PD.dock && PD.pid===${JSON.stringify(A.proj)} && ${JSON.stringify(A.tasks)}.every(r => PD.rt.has('chat:'+r))`, 'reloaded');
    await sleep(1500);
    out.afterReload = await ev(TREE);
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class TabMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-tab-menu-', ignore_cleanup_errors=True)
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
            # The hub's sessions list outlives a suite: the one before this in the same process
            # would hand the page its own rooms.
            mock.patch.object(dashboard, '_SESS_CACHE', {}),
            mock.patch.object(dashboard, '_SLIM_MEMO', {}),
            mock.patch.object(dashboard, 'SESSIONS_FEEDS', {}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']; home = Path(proj.get('home') or proj['path'])
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':'menu-po','cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']

        def task(title):
            t = chatroom.create_room(title, [{'identity':'claude','agent':'claude','cwd':str(home)}])
            chatroom.post_message(t['id'], 'user', f'About {title}')
            full = chatroom.get_room(t['id'], public=False)
            full.update(mode='solo', launched=True, workflow='inprogress', cwd=str(home))
            chatroom.update_room(full)
            dashboard.assign_session_project(t['id'], cls.proj)
            dashboard.assign_task_number(t['id'], cls.proj)
            return t['id']
        cls.tasks = [task('Brakes'), task('Wipers'), task('Lamps')]
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj, 'po':cls.po, 'tasks':cls.tasks,
                'shots':os.environ.get('ENSEMBLE_SHOTS', '')}
        script = base / 'tab_menu_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('ENSEMBLE_MENU_DUMP'): print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def runs(self, what):
        return self.got['runs'][what]

    # Move to Other Stack has no ▸ when there is no other stack: Dock draws an empty submenu as a greyed plain row.
    SPLITS = ['Split Right', 'Split Left', 'Split Down', 'Split Up', 'Move to Other Stack', 'Unsplit', 'Close Other Tabs', 'Close Tabs to the Right']

    @staticmethod
    def bare(rows):
        return [r.split(' {')[0].lstrip('~').rstrip('▸') for r in rows]

    def test_a_tabs_menu_has_the_new_rows_together(self):
        rows = self.bare(self.runs('bar-menu')['menu'][0])
        i = rows.index('Split Right')
        self.assertEqual(rows[i:i + 8], self.SPLITS)
        # Then the tab's pin, and Dock's own items with Close last.
        self.assertLess(i, rows.index('Pin: keep it in every project'))
        self.assertEqual(rows[-1], 'Close')

    def test_in_the_top_bar_unsplit_is_greyed_with_its_reason(self):
        rows = self.runs('bar-menu')['menu'][0]
        unsplit = next(r for r in rows if r.lstrip('~').startswith('Unsplit'))
        self.assertTrue(unsplit.startswith('~'))
        self.assertIn('already in the top bar', unsplit)
        self.assertFalse(next(r for r in rows if 'Split Right' in r).startswith('~'))

    def test_a_right_click_and_the_tabs_dots_show_the_same_rows(self):
        self.assertEqual(self.runs('side-menu')['menu'][0], self.got['dotsMenu'][0])

    def test_each_split_moves_the_tab_to_that_side(self):
        want = {'Split Right': 'row([T,B,C]|[A*])', 'Split Left': 'row([A*]|[T,B,C])',
                'Split Down': 'col([T,B,C]|[A*])', 'Split Up': 'col([A*]|[T,B,C])'}
        for label, tree in want.items():
            with self.subTest(label):
                r = self.runs('bar:' + label)
                self.assertEqual(r['before'], '[T*,A,B,C]')
                self.assertEqual(r['tree'].replace('*', '').replace('A', 'A*'), tree)
                self.assertEqual(r['open'], 0)

    def test_a_split_from_a_side_stack(self):
        r = self.runs('side:Split Down')
        self.assertEqual(r['before'], 'row([T*,C]|[A,B*])')
        self.assertEqual(r['tree'], 'row([T*,C]|col([B*]|[A*]))')

    def test_move_to_other_stack(self):
        r = self.runs('bar:move')
        self.assertEqual(r['before'], 'row([T*,A,C]|[B*])')
        self.assertEqual(r['tree'], 'row([T*,C]|[B,A*])')
        r = self.runs('side:move-bar')
        self.assertEqual(r['tree'], 'row([T,C,A*]|[B*])')

    def test_unsplit_puts_the_stacks_tabs_back_in_the_top_bar(self):
        r = self.runs('side:unsplit')
        self.assertEqual(r['before'], 'row([T*,C]|[A,B*])')
        self.assertEqual(r['tree'], '[T,C,A*,B]')

    def test_closing_others_keeps_the_conversation_and_pinned_tabs(self):
        self.assertEqual(self.runs('bar:close-others')['tree'].replace('*', ''), '[T,B]')
        self.assertEqual(self.runs('bar:close-right')['tree'].replace('*', ''), '[T,A,B]')
        self.assertEqual(self.runs('bar:close-others-pinned')['tree'].replace('*', ''), '[T,B,C]')

    def test_a_tool_alone_cannot_split_or_join_the_top_bar(self):
        rows = self.runs('tool-menu')['menu'][0]
        for label in ('Split Right', 'Split Left', 'Split Down', 'Split Up', 'Move to Other Stack'):
            with self.subTest(label):
                self.assertTrue(next(r for r in rows if r.lstrip('~').split(' {')[0] == label).startswith('~'))
        self.assertIn('Move to Other Stack {There is no other stack}', [r.lstrip('~') for r in rows])
        self.assertFalse(next(r for r in rows if r.lstrip('~').startswith('Unsplit')).startswith('~'))
        self.assertEqual(self.runs('tool:unsplit')['tree'], '[T*,A,B,C]')
        self.assertEqual(self.got['toolMode'], 'unpinned')

    @staticmethod
    def row(rows, label):
        return next(r for r in rows if r.lstrip('~').split(' {')[0].rstrip('▸') == label)

    def test_a_tab_in_a_window_has_the_rows_without_splits(self):
        for what in ('float-menu', 'min-float-menu'):
            with self.subTest(what):
                r = self.runs(what)
                rows = r['menu'][0]
                self.assertEqual(self.bare(rows)[self.bare(rows).index('Split Right'):][:8], self.SPLITS)
                for l in ('Split Right', 'Split Left', 'Split Down', 'Split Up'):
                    self.assertTrue(self.row(rows, l).startswith('~'))
                    self.assertIn('A window holds one stack', self.row(rows, l))
                self.assertFalse(self.row(rows, 'Move to Other Stack').startswith('~'))
                self.assertFalse(self.row(rows, 'Unsplit').startswith('~'))
        self.assertEqual(self.runs('float-menu')['before'], '[T*,B,C] ~[A*]')

    def test_a_tab_in_a_window_moves_and_unsplits(self):
        self.assertEqual(self.runs('float:unsplit')['tree'], '[T,B,C,A*]')
        self.assertEqual(self.runs('float:move-bar')['tree'], '[T,B,C,A*]')
        r = self.runs('bar:move-float')
        self.assertEqual(r['tree'].replace('*', ''), '[T,C] ~[A,B]')
        self.assertIn('B*', r['tree'])

    def test_with_the_conversation_in_a_window(self):
        r = self.runs('conv-float:unsplit')
        self.assertEqual(r['before'], 'row([C*]|[A,B*]) ~[T*]')
        self.assertEqual(r['tree'].replace('*', ''), '[C] ~[T,A,B]')
        rows = self.runs('conv-float-menu')['menu'][0]
        self.assertTrue(self.row(rows, 'Unsplit').startswith('~'))
        self.assertIn('already with the conversation', self.row(rows, 'Unsplit'))

    def test_a_tab_with_the_conversation_in_its_window_can_move_out(self):
        rows = self.runs('conv-float-menu')['menu'][0]
        self.assertFalse(self.row(rows, 'Move to Other Stack').startswith('~'))
        self.assertEqual(self.runs('conv-float:move-out')['tree'].replace('*', ''), '[A,B,C] ~[T]')

    def test_the_conversations_own_tab_stays(self):
        rows = self.runs('conv-own-menu')['menu'][0]
        for l in ('Split Right', 'Split Left', 'Split Down', 'Split Up', 'Move to Other Stack'):
            self.assertTrue(self.row(rows, l).startswith('~'), l)
            self.assertIn('The conversation stays where it is', self.row(rows, l))

    def test_the_layout_survives_a_reload(self):
        self.assertEqual(self.got['beforeReload'].replace('*', ''), 'row([T,B,C]|[A])')
        self.assertEqual(self.got['afterReload'], self.got['beforeReload'])

    def test_no_page_errors(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
