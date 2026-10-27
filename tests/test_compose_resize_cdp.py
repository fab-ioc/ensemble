"""Dragging the Send box's height handle (#274) is smooth (P226, #275).

A real mouse drag over CDP (30 steps up 300 px, 30 back down) on the
workspace's box, with the PO chat and a thread open above it, and on a task
chat's own box. Every animation frame after every step is sampled: the box
height never reverses against the pointer, the handle stays under the pointer,
the frame holds the whole box, the chats above keep their last line in view,
and the height kept afterwards is the one dropped.
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
  const size=(w,h) => send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<120;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);} throw Error('timeout '+label); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.rt.get('thread:po')?.el.querySelector('iframe')`;
  const FRAME = `document.getElementById('workspace-compose-frame')`;
  // One sample: where the handle is on the page, the box and frame heights,
  // and each chat's distance from its end. `box` is the page holding the
  // Send box (the workspace frame's, or this page for a task chat).
  const SAMPLE = `(() => {
    const ws = !!document.getElementById('workspace-compose-frame') && !document.getElementById('workspace-compose').hidden;
    const f = ws ? document.getElementById('workspace-compose-frame') : null, d = f ? f.contentDocument : document;
    const off = f ? f.getBoundingClientRect().top : 0, bar = d.getElementById('compose-size').getBoundingClientRect();
    const end = fr => { const m = fr?.contentDocument?.getElementById('msgs'); return m ? Math.round(m.scrollHeight - m.scrollTop - m.clientHeight) : null; };
    const top = fr => fr ? Math.round(fr.getBoundingClientRect().height) : null;
    const chats = ws ? [${PO}, ${THR}] : [{contentDocument:document, getBoundingClientRect:()=>document.getElementById('msgs').getBoundingClientRect()}];
    return {bar: Math.round(off + bar.top), ed: Math.round(d.getElementById('editor').getBoundingClientRect().height),
      box: Math.ceil(d.getElementById('compose').getBoundingClientRect().height), frame: f ? Math.round(f.getBoundingClientRect().height) : null,
      ends: chats.map(end), heights: chats.map(top)};
  })()`;
  // After a step, a sample of each of the next three frames as painted: a
  // task queued from requestAnimationFrame runs once that frame's rendering
  // (layout, resize observers, paint) is done.
  const frames = `new Promise(r => { const out = [];
    const tick = () => { const ch = new MessageChannel(); ch.port1.onmessage = () => { out.push(${SAMPLE}); if (out.length < 3) requestAnimationFrame(tick); else r(out); }; ch.port2.postMessage(0); };
    requestAnimationFrame(tick); })`;
  const mouse = (type, x, y) => send('Input.dispatchMouseEvent', {type, x, y, button:'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount:1}, s);
  async function drag(label) {
    const at = await ev(`(() => { const ws = !document.getElementById('workspace-compose')?.hidden && !!document.getElementById('workspace-compose-frame');
      const f = ws ? document.getElementById('workspace-compose-frame') : null, d = f ? f.contentDocument : document;
      const b = d.getElementById('compose-size').getBoundingClientRect(), o = f ? f.getBoundingClientRect() : {left:0, top:0};
      return {x: Math.round(o.left + b.left + b.width / 2), y: Math.round(o.top + b.top + b.height / 2)}; })()`);
    const start = await ev(SAMPLE), steps = [];
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:at.x, y:at.y, buttons:0}, s);
    await mouse('mousePressed', at.x, at.y);
    let y = at.y;
    for (let i = 0; i < 60; i++) {
      const dir = i < 30 ? -1 : 1;
      y += dir * 10;
      await mouse('mouseMoved', at.x, y);
      steps.push({y, dir, want: start.bar + (y - at.y), frames: await ev(frames)});
    }
    await mouse('mouseReleased', at.x, y);
    await sleep(300);
    const done = await ev(SAMPLE);
    const kept = await ev(`localStorage.getItem('cd-compose-h')`);
    return {label, start, steps, done, kept};
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
      await until(`${FRAME}?.contentDocument?.getElementById('compose-size') && !${FRAME}.contentDocument.getElementById('compose-size').hidden`,'workspace box');
      await sleep(900);
      out['workspace' + w] = await drag('workspace ' + w + 'x' + h);
    }
    // A task chat's own box (a window of its own), where the page is the chat.
    await size(1440, 900);
    await send('Page.navigate',{url:A.base+'/session?id='+encodeURIComponent(A.task)},s);
    await until(`!!document.getElementById('compose-size') && !document.getElementById('compose-size').hidden && document.getElementById('msgs').scrollHeight > document.getElementById('msgs').clientHeight + 200`,'task chat').catch(async e => { throw Error(e.message + ' ' + JSON.stringify(await ev(`({url:location.href, bar:!!document.getElementById('compose-size'), hidden:document.getElementById('compose-size')?.hidden, sh:document.getElementById('msgs')?.scrollHeight, ch:document.getElementById('msgs')?.clientHeight, n:document.getElementById('msgs')?.children.length})`))); });
    await ev(`localStorage.removeItem('cd-compose-h'); dispatchEvent(new Event('resize')); 0`);
    await sleep(600);
    out.task = await drag('task chat 1440x900');
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


def analyse(run):
    """Per drag: height reversals, worst handle-pointer gap, frame-box gap, chat end moves."""
    steps = run['steps']
    reversals = 0
    worst_track = 0
    worst_frame = 0
    worst_end = 0
    prev = run['start']['ed']
    # Only the box and what is above it change size: each chat's height plus
    # the box's (its frame's, in the workspace) stays what it was.
    size = lambda f: [h + (f['frame'] if f['frame'] is not None else f['box']) for h in f['heights']]
    base = size(run['start'])
    worst_layout = 0
    for st in steps:
        for f in st['frames']:
            delta = f['ed'] - prev
            if delta * st['dir'] > 0:      # the box grew while the pointer went down, or the reverse
                reversals += 1
            prev = f['ed']
            if f['frame'] is not None:
                worst_frame = max(worst_frame, abs(f['frame'] - f['box']))
            worst_layout = max([worst_layout] + [abs(x - y) for x, y in zip(size(f), base)])
            for e in f['ends']:
                if e is not None:
                    worst_end = max(worst_end, e)
        last = st['frames'][-1]
        worst_track = max(worst_track, abs(last['bar'] - st['want']))
    peak = max(f['ed'] for st in steps for f in st['frames']) - run['start']['ed']
    return {'reversals': reversals, 'track': worst_track, 'frameGap': worst_frame, 'chatEnd': worst_end, 'layout': worst_layout,
            'peak': peak, 'final': run['done']['ed'], 'kept': int(run['kept'] or 0)}


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ComposeResize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-compose-resize-', ignore_cleanup_errors=True)
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
        sid = 'resize-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id']); cls.po = po['id']
        task = chatroom.create_room('Task chat', [{'identity':'claude','agent':'claude','sessionId':'resize-task','cwd':str(home)}])
        dashboard.assign_session_project(task['id'], cls.proj)
        dashboard.assign_task_number(task['id'], cls.proj)
        cls.task = task['id']
        transcript = base / 'transcripts' / 'C--resize'; transcript.mkdir()
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
        lines = [turn('user' if n % 2 else 'assistant', f'Task message {n}: enough words to take a line or two in the chat.', n + 1, 'resize-task')
                 for n in range(40)]
        (base / 'transcripts' / 'C--task' / 'resize-task.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj':cls.proj,'po':cls.po,'task':cls.task}
        script = base / 'compose_resize_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=240)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        dump = os.environ.get('COMPOSE_RESIZE_DUMP', '')
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_drag_is_smooth(self):
        for key in ('workspace1728', 'workspace1440', 'task'):
            with self.subTest(key=key):
                a = analyse(self.got[key])
                print(key, a)
                self.assertEqual(a['reversals'], 0, a)
                self.assertLessEqual(a['track'], 4, a)
                if key != 'task':
                    self.assertLessEqual(a['frameGap'], 1, a)
                self.assertLessEqual(a['chatEnd'], 2, a)
                self.assertGreaterEqual(a['peak'], 290, a)
                self.assertLessEqual(a['layout'], 1, a)
                self.assertEqual(a['kept'], a['final'], a)


if __name__ == '__main__':
    unittest.main()
