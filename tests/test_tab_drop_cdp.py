"""Issue 66: a tab dragged to where the blue box shows goes there.

In headless Chrome over CDP, with real mouse presses, moves and releases (not
the dock's API): a task's tab is dragged from the top bar's row and from a stack's
own tab row onto each place the drop box offers (the left, right, top and bottom
of a stack, its middle, the dock's edges); the box shown while dragging and the
layout after the release must agree, and the layout stays after a reload.
``ENSEMBLE_SHOTS`` keeps screenshots. Skipped without Node or Chrome.
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
  const out = { runs: [] };
  const A_TASKS = A.tasks;
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
    out.start = await ev(TREE);
    out.barHead = await ev(`!!document.querySelector('.dk-head.bt-head')`);
    await snap('start');
    const [TA, TB, TC] = A_TASKS.map(r => 'chat:' + r);
    await ev(`window.__L0 = JSON.stringify(PD.dock.layout()); window.__clicks = 0; window.addEventListener('click', () => window.__clicks++); 0`);
    const restore = async () => { await ev(`PD.dock._replaceLayout(JSON.parse(__L0)); PD.dock.activate('po-chat'); 0`); await sleep(500); };
    const T = 'po-chat';
    const run = async (what, setup, from, to) => {
      await restore();
      for (const x of setup || []) await ev(x + '; 0');
      if (setup && setup.length) await sleep(600);
      const before = await ev(TREE);
      await ev(`window.__clicks = 0`);
      const r = typeof to === 'function' ? await to(from) : await drag(from, to);
      out.runs.push({ what, before, ...r, press: await ev(`document.body.classList.contains('pd-press')`), dragging: await ev(`PD_ROOT.classList.contains('dk-dragging')`) });
    };
    const body = (id, fx, fy) => ({ sel: stackOf(id), fx, fy });
    const main = (fx, fy) => ({ sel: `PD_ROOT.querySelector('.dk-main')`, fx, fy });
    const edge = side => ({ left: { sel: `PD_ROOT.querySelector('.dk-main')`, fx: 0.004, fy: 0.5 }, right: { sel: `PD_ROOT.querySelector('.dk-main')`, fx: 0.996, fy: 0.5 },
      bottom: { sel: `PD_ROOT.querySelector('.dk-main')`, fx: 0.5, fy: 0.995 } })[side];
    const ZONES = { left: [0.1, 0.5], right: [0.9, 0.5], top: [0.5, 0.15], bottom: [0.5, 0.9], center: [0.5, 0.5] };
    // From the top bar's row.
    for (const [z, [fx, fy]] of Object.entries(ZONES)) if (z !== 'center')
      await run('bar>' + z, [], { sel: tabSel(TA) }, body(T, fx, fy));
    for (const z of ['left', 'right', 'bottom']) await run('bar>edge-' + z, [], { sel: tabSel(TA) }, edge(z));
    await run('bar>other-center', [`PD.dock.dockEdge(${JSON.stringify(TB)}, 'right')`], { sel: tabSel(TA) }, body(TB, 0.5, 0.5));
    await run('bar>other-left', [`PD.dock.dockEdge(${JSON.stringify(TB)}, 'right')`], { sel: tabSel(TA) }, body(TB, 0.1, 0.5));
    await run('bar>float', [`PD.dock.dockEdge(${JSON.stringify(TB)}, 'right')`, `PD.dock.float(${JSON.stringify(TB)})`], { sel: tabSel(TA) },
      { sel: `PD_ROOT.querySelector('.dk-float')`, fx: 0.5, fy: 0.6 });
    // From a stack's own tab row: A and B in a stack on the right.
    const side2 = [`PD.dock.dockEdge(${JSON.stringify(TA)}, 'right')`, `PD.dock.moveTo(${JSON.stringify(TB)}, {kind:'stack', stack:PD.dock.layout().root.kids[1]}, 'center')`];
    for (const [z, [fx, fy]] of Object.entries(ZONES))
      await run('stack>' + z, side2, { sel: tabSel(TA) }, body(T, fx, fy));
    await run('stack>own-bottom', side2, { sel: tabSel(TA) }, body(TA, 0.5, 0.9));
    await run('stack>edge-left', side2, { sel: tabSel(TA) }, edge('left'));
    // The project's group (the conversation's own tab) does not move: no box, nothing moves.
    await run('group', [], { sel: `document.getElementById('bar-grp')`, fx: 0.3 }, body(T, 0.1, 0.5));
    // A tool docked, dragged into the conversation's stack.
    await run('tool>center', [`PD.dock.setViewMode('board', 'pinned')`], { sel: tabSel('board') }, body(T, 0.5, 0.5));
    await run('tool>left', [`PD.dock.setViewMode('board', 'pinned')`], { sel: tabSel('board') }, body(T, 0.1, 0.5));
    // A tool's release is judged where it happens: shown no box over the middle, released at the left edge.
    await run('tool>center-release-left', [`PD.dock.setViewMode('board', 'pinned')`], { sel: tabSel('board') }, async from => {
      await press(from, body(T, 0.5, 0.5));
      const box = await ev(PREVIEW);
      return { box, ...await release(await at(stackOf(T), 0.1, 0.5)) };
    });
    // After a release over the middle (nothing moves), the next drag of the tool works.
    await run('tool>center-then-left', [`PD.dock.setViewMode('board', 'pinned')`], { sel: tabSel('board') }, async from => {
      const first = await release(await press(from, body(T, 0.5, 0.5)));
      const q = await press(from, body(T, 0.1, 0.5));
      const box = await ev(PREVIEW);
      return { box, firstTree: first.tree, ...await release(q) };
    });
    // A window over the conversation: a tool dropped into it joins it (its box is the window's).
    // The window is put in the middle of the conversation's stack.
    const OVER = `(() => { const L = PD.dock.layout(), b = PD_ROOT.getBoundingClientRect(),
      r = document.querySelector('[data-dk-tab="po-chat"]').closest('.dk-stack').getBoundingClientRect();
      Object.assign(L.floats[0], { x: Math.round(r.left - b.left + r.width * 0.3), y: Math.round(r.top - b.top + r.height * 0.3),
        w: Math.round(r.width * 0.4), h: Math.round(r.height * 0.4) }); PD.dock._replaceLayout(L); })()`;
    await run('tool>float-over', [`PD.dock.setViewMode('board', 'pinned')`, `PD.dock.float(${JSON.stringify(TB)})`, OVER], { sel: tabSel('board') },
      { sel: `PD_ROOT.querySelector('.dk-float')`, fx: 0.5, fy: 0.6 });
    // A release the page missed: a move with no button down ends the drag; the release after moves nothing.
    await run('lost>move', [], { sel: tabSel(TA) }, async from => {
      const q = await press(from, body(T, 0.1, 0.5));
      const box = await ev(PREVIEW);
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: q.x + 4, y: q.y, button: 'none', buttons: 0 }, s); await sleep(150);
      const mid = { midBox: await ev(PREVIEW), midDragging: await ev(`PD_ROOT.classList.contains('dk-dragging')`), midPress: await ev(`document.body.classList.contains('pd-press')`) };
      return { box, ...mid, ...await release(q) };
    });
    // The window loses focus mid-drag: the drag ends; moves and the release after it move nothing.
    await run('lost>blur', [], { sel: tabSel(TA) }, async from => {
      const q = await press(from, body(T, 0.1, 0.5));
      const box = await ev(PREVIEW);
      await ev(`window.dispatchEvent(new Event('blur')); 0`); await sleep(150);
      const mid = { midBox: await ev(PREVIEW), midDragging: await ev(`PD_ROOT.classList.contains('dk-dragging')`), midPress: await ev(`document.body.classList.contains('pd-press')`) };
      const q2 = await at(stackOf(T), 0.9, 0.5);
      await moveTo(q, q2);
      return { box, ...mid, ...await release(q2) };
    });
    // A window being moved when the window loses focus stays where it was left.
    await restore();
    await ev(`PD.dock.float(${JSON.stringify(TB)}); 0`); await sleep(600);
    {
      const head = `PD_ROOT.querySelector('.dk-float .dk-head')`;
      const p = await at(head, 0.8, 0.5);
      const q = { x: p.x - 120, y: p.y + 80 };
      await mouse('mouseMoved', p, 0); await sleep(60);
      await mouse('mousePressed', p, 1); await sleep(60);
      await moveTo(p, q);
      await ev(`window.dispatchEvent(new Event('blur')); 0`); await sleep(150);
      const left = await ev(`JSON.stringify((({x, y}) => ({x, y}))(PD.dock.layout().floats[0]))`);
      await moveTo(q, { x: q.x - 200, y: q.y + 100 });
      await mouse('mouseReleased', { x: q.x - 200, y: q.y + 100 }, 0); await sleep(600);
      out.floatBlur = { left, after: await ev(`JSON.stringify((({x, y}) => ({x, y}))(PD.dock.layout().floats[0]))`), press: await ev(`document.body.classList.contains('pd-press')`) };
    }
    // A strip button dragged over the conversation to the left edge.
    await restore();
    out.stripBefore = await ev(`PD.dock.side('spec') + ':' + PD.dock.viewMode('spec')`);
    out.strip = await drag({ sel: `PD_ROOT.querySelector('[data-dk-auto="spec"]')` }, { sel: `PD_ROOT.querySelector('.dk-main')`, fx: 0.003, fy: 0.5 });
    out.stripAfter = await ev(`PD.dock.side('spec') + ':' + PD.dock.viewMode('spec')`);
    // Persists: a drop, then a reload.
    await restore();
    await drag({ sel: tabSel(TA) }, body(T, 0.1, 0.5));
    out.beforeReload = await ev(TREE);
    await send('Page.reload', {}, s);
    await until(`window.ensBooted===true && !!PD.dock && PD.pid===${JSON.stringify(A.proj)} && ${JSON.stringify(A_TASKS)}.every(r => PD.rt.has('chat:'+r))`, 'reloaded');
    await sleep(1500);
    out.afterReload = await ev(TREE);
    await snap('after-reload');
    out.errors = errors;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class TabDrop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-tab-drop-', ignore_cleanup_errors=True)
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
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':'drop-po','cwd':str(home)}])
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
        script = base / 'tab_drop_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=300)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('ENSEMBLE_DROP_DUMP'): print(json.dumps(cls.got, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    # What each drag shows (the box's side) and the dock after its release. A, B, C are
    # the three tasks' tabs, T the conversation (the project's group); * the tab in front.
    EXPECT = {
        # From the top bar's row: the conversation's stack, the dock's edges, another stack, a window.
        'bar>left': ('left', '[T*,A,B,C]', 'row([A*]|[T*,B,C])'),
        'bar>right': ('right', '[T*,A,B,C]', 'row([T*,B,C]|[A*])'),
        'bar>top': ('top', '[T*,A,B,C]', 'col([A*]|[T*,B,C])'),
        'bar>bottom': ('bottom', '[T*,A,B,C]', 'col([T*,B,C]|[A*])'),
        'bar>edge-left': ('left', '[T*,A,B,C]', 'row([A*]|[T*,B,C])'),
        'bar>edge-right': ('right', '[T*,A,B,C]', 'row([T*,B,C]|[A*])'),
        'bar>edge-bottom': ('bottom', '[T*,A,B,C]', 'col([T*,B,C]|[A*])'),
        'bar>other-center': ('center', 'row([T*,A,C]|[B*])', 'row([T*,C]|[B,A*])'),
        'bar>other-left': ('left', 'row([T*,A,C]|[B*])', 'row([T*,C]|[A*]|[B*])'),
        'bar>float': ('center', '[T*,A,C] ~[B*]', '[T*,C] ~[B,A*]'),
        # From a stack's own tab row (A and B in a stack on the right).
        'stack>left': ('left', 'row([T*,C]|[A,B*])', 'row([A*]|[T*,C]|[B*])'),
        'stack>right': ('right', 'row([T*,C]|[A,B*])', 'row([T*,C]|[A*]|[B*])'),
        'stack>top': ('top', 'row([T*,C]|[A,B*])', 'row(col([A*]|[T*,C])|[B*])'),
        'stack>bottom': ('bottom', 'row([T*,C]|[A,B*])', 'row(col([T*,C]|[A*])|[B*])'),
        'stack>center': ('center', 'row([T*,C]|[A,B*])', 'row([T,C,A*]|[B*])'),
        'stack>own-bottom': ('bottom', 'row([T*,C]|[A,B*])', 'row([T*,C]|col([B*]|[A*]))'),
        'stack>edge-left': ('left', 'row([T*,C]|[A,B*])', 'row([A*]|[T*,C]|[B*])'),
        # Drops the dock does not take show no box: the group (the conversation stays), a tool
        # into the conversation's stack (the page keeps tools out of it); a tool's edge does split.
        'group': (None, '[T*,A,B,C]', '[T*,A,B,C]'),
        'tool>center': (None, 'row([T*,A,B,C]|[board*])', 'row([T*,A,B,C]|[board*])'),
        'tool>left': ('left', 'row([T*,A,B,C]|[board*])', 'row([board*]|[T*,A,B,C])'),
        # A tool: no box over the middle, but a release at the left edge splits there; after a
        # release over the middle the next drag works; a window over the conversation takes it.
        'tool>center-release-left': (None, 'row([T*,A,B,C]|[board*])', 'row([board*]|[T*,A,B,C])'),
        'tool>center-then-left': ('left', 'row([T*,A,B,C]|[board*])', 'row([board*]|[T*,A,B,C])'),
        'tool>float-over': ('center', 'row([T*,A,C]|[board*]) ~[B*]', '[T*,A,C] ~[B,board*]'),
        # A release the page missed (a move with no button, the window's blur): nothing moves.
        'lost>move': ('left', '[T*,A,B,C]', '[T*,A,B,C]'),
        'lost>blur': ('left', '[T*,A,B,C]', '[T*,A,B,C]'),
    }

    def test_every_drop_the_box_offers_happens(self):
        runs = {r['what']: r for r in self.got['runs']}
        self.assertEqual(set(runs), set(self.EXPECT))
        for what, (side, before, after) in self.EXPECT.items():
            with self.subTest(what):
                r = runs[what]
                self.assertEqual(r['before'], before)
                self.assertEqual((r['box'] or {}).get('side'), side)
                self.assertEqual(r['tree'], after)

    def test_the_release_ends_the_drag(self):
        for r in self.got['runs']:
            with self.subTest(r['what']):
                self.assertIsNone(r['boxAfter'])
                self.assertFalse(r['dragging'])
                self.assertFalse(r['press'])     # the frames take the pointer again

    def test_a_drag_opens_nothing_on_release(self):
        # The release of a drag, a drop or one the page cancels, clicks nothing.
        for r in self.got['runs']:
            if r['what'] == 'group':
                continue
            with self.subTest(r['what']):
                self.assertEqual(r['clicks'], 0)

    def test_a_lost_release_ends_the_drag_at_once(self):
        for what in ('lost>move', 'lost>blur'):
            with self.subTest(what):
                r = next(x for x in self.got['runs'] if x['what'] == what)
                self.assertIsNone(r['midBox'])
                self.assertFalse(r['midDragging'])
                self.assertFalse(r['midPress'])

    def test_a_tool_released_over_the_middle_leaves_the_next_drag_working(self):
        r = next(x for x in self.got['runs'] if x['what'] == 'tool>center-then-left')
        self.assertEqual(r['firstTree'], 'row([T*,A,B,C]|[board*])')

    def test_a_window_moved_when_focus_is_lost_stays(self):
        fb = self.got['floatBlur']
        self.assertEqual(fb['after'], fb['left'])
        self.assertFalse(fb['press'])

    def test_a_strip_button_dragged_over_the_conversation_lands(self):
        self.assertEqual(self.got['stripBefore'], 'right:unpinned')
        self.assertEqual(self.got['stripAfter'], 'left:unpinned')
        self.assertIsNone(self.got['strip']['boxAfter'])

    def test_the_new_layout_survives_a_reload(self):
        self.assertEqual(self.got['beforeReload'], 'row([A*]|[T*,B,C])')
        self.assertEqual(self.got['afterReload'], self.got['beforeReload'])

    def test_no_page_errors(self):
        self.assertEqual(self.got['errors'], [])


if __name__ == '__main__':
    unittest.main()
