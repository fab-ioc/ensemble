"""A PO chat at its end stays still, and stays at its end (#387, P379).

A solo PO chat of 100 turns with pictures; the last message holds one the hub
is slow to serve, so it loads after the draw has put the chat at its end. Then
scrolled up and back down, by the wheel and by Latest, each recorded for 40 s:
from 1 s after the end nothing writes scrollTop and nothing shifts, and on the way back
nothing draws or writes scrollTop while the scroll still moves. A new message arriving
at the end is followed; a picture arriving while scrolled up moves nothing.
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
SLOW_MS = 4000      # how long the hub takes to serve a "late" picture
RECORD_MS = 40000   # how long each return to the end is recorded (the spec's 40 s)
CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', 'about:blank'], {stdio:['ignore','ignore','pipe']});
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
  await send('Emulation.setDeviceMetricsOverride',{width:760,height:1000,deviceScaleFactor:1.5,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (expression, n=150) => { for(let i=0;i<n;i++){ try {if(await ev(expression)) return;}catch(e){} await sleep(100); } throw Error('timeout '+expression); };
  // Every scrollTop write (with its caller), those that moved the chat, and every layout shift in it.
  // Each write also says whether the reader's scroll was still moving then (#387: it must not be),
  // and every draw of the chat says who asked for it.
  const REC = `(() => { const box=$('#msgs'), L=window.__rec={writes:[],moves:[],shifts:[],renders:[],t0:performance.now()};
    const rb=window.renderBubbles; window.renderBubbles=function(...a){ L.renders.push({t:Math.round(performance.now()-L.t0),
      by:(new Error().stack||'').split('\\n').slice(2,4).map(x=>x.trim()).join(' < ')}); return rb.apply(this,a); };
    const d=Object.getOwnPropertyDescriptor(Element.prototype,'scrollTop');
    Object.defineProperty(box,'scrollTop',{configurable:true,get(){return d.get.call(this);},set(v){
      const b=d.get.call(this); d.set.call(this,v); const a=d.get.call(this);
      L.writes.push({t:Math.round(performance.now()-L.t0),v:Math.round(+v),from:Math.round(b),to:Math.round(a),
        moving:typeof scrollMoving==='function'?scrollMoving():null,
        by:(new Error().stack||'').split('\\n').slice(2,4).map(x=>x.trim()).join(' < ')});
      if(Math.abs(a-b)>=1) L.moves.push({t:Math.round(performance.now()-L.t0),from:Math.round(b),to:Math.round(a)}); }});
    new PerformanceObserver(l=>{ for(const e of l.getEntries()) { const inBox=(e.sources||[]).some(x=>x.node&&box.contains(x.node));
      if(inBox) L.shifts.push({t:Math.round(e.startTime-L.t0),v:+e.value.toFixed(4)}); } }).observe({type:'layout-shift'});
    let prev=null; const tick=()=>{ const top=Math.round(d.get.call(box)); if(prev!==null&&top!==prev) L.tops=(L.tops||[]).concat([{t:Math.round(performance.now()-L.t0),top}]); prev=top; requestAnimationFrame(tick); }; tick();
    return 1; })()`;
  const state = `(() => { const b=$('#msgs'); const rows=[...b.children].filter(k=>k.offsetParent);
    const last=rows[rows.length-1]; return {top:Math.round(b.scrollTop), remaining:Math.round(b.scrollHeight-b.clientHeight-b.scrollTop),
      sh:b.scrollHeight, stick:STICK, latestShown:!$('#to-latest').hidden, lastText:(last&&last.textContent||'').slice(-80), lastHas1:!!last&&/Fresh news 1:/.test(last.textContent),
      lastImgs:last?[...last.querySelectorAll('img')].map(i=>({h:i.offsetHeight,done:i.complete&&i.naturalWidth>0})):[]}; })()`;
  const out={};
  try {
    await send('Page.navigate',{url:A.base+'/session?id='+A.room+'&embed=1'},s);
    await until(`document.querySelectorAll('#msgs .msg[data-mid]').length > 10`);
    // The late picture of the last message: the draw put the chat at its end before it loaded.
    out.beforeLoad = await ev(state);
    await until(`(() => { const r=[...$('#msgs').children].filter(k=>k.offsetParent).pop(); const i=r&&r.querySelector('img'); return i&&i.complete&&i.naturalWidth>0; })()`, 100);
    await sleep(400);
    out.afterLoad = await ev(state);
    const box = await ev(`(() => { const r=$('#msgs').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
    const wheel = dy => send('Input.dispatchMouseEvent',{type:'mouseWheel',x:box.x,y:box.y,deltaX:0,deltaY:dy},s);
    const up = async () => { for(let i=0;i<8;i++){ await wheel(-500); await sleep(120); } await sleep(1500); };
    for (const how of ['wheel','latest']) {
      await ev(REC);
      await up();
      const away = await ev(state), awayT = await ev(`Math.round(performance.now()-__rec.t0)`);
      if (how === 'wheel') { for(let i=0;i<80;i++){ await wheel(500); await sleep(60); if(await ev(`(() => { const b=$('#msgs'); return b.scrollHeight-b.clientHeight-b.scrollTop<2; })()`)) break; } }
      else { await until(`!$('#to-latest').hidden`, 30); await ev(`$('#to-latest').click(), 0`); }
      const bottom = await ev(`Math.round(performance.now()-__rec.t0)`);
      await sleep(RECORD_MS);
      const L = await ev(`__rec`);
      const after = x => (x||[]).filter(e => e.t > bottom + 1000);
      const back = x => (x||[]).filter(e => e.t > awayT);
      out[how] = {away, bottom, end:await ev(state), writesAfter:after(L.writes), movesAfter:after(L.moves), shiftsAfter:after(L.shifts), topsAfter:after(L.tops),
        movingWrites:back(L.writes).filter(w => w.moving !== false), scrollRenders:back(L.renders).filter(r => /msgsScrolled/.test(r.by)),
        settleRenders:back(L.renders).filter(r => /msgsSettle/.test(r.by))};
    }
    // A new message at the end: followed.
    await fetch(A.base + '/__test/append?n=1');
    await until(`[...$('#msgs').children].some(k=>/Fresh news 1/.test(k.textContent))`, 150);
    await sleep(2500);
    out.newAtEnd = await ev(state);
    // Scrolled up, a new message with a late picture: the reader stays where they are.
    await up();
    const upState = await ev(state);
    await fetch(A.base + '/__test/append?n=2&pic=1');
    await until(`!!LAST_ITEMS && LAST_ITEMS.some(m=>/Fresh news 2/.test(m.text||''))`, 150);
    await sleep(${SLOW}+2000);
    out.newWhileUp = {before:upState, after:await ev(state)};
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
""".replace('${SLOW}', str(SLOW_MS)).replace('RECORD_MS', str(RECORD_MS))


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ChatSettle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-chat-settle-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        orig_get = dashboard.Handler.do_GET
        cls.transcript = None

        def do_get(handler):
            # The hub is slow to serve the "late" pictures; a test hook adds turns.
            if handler.path.startswith('/api/file') and 'late' in handler.path:
                time.sleep(SLOW_MS / 1000)
            if handler.path.startswith('/__test/append'):
                n = handler.path.split('n=')[1].split('&')[0]
                text = f'Fresh news {n}: ' + 'a new line of the plan. ' * 6
                if 'pic=1' in handler.path:
                    text += f'\n\nThe drawing: {cls.pics["late2"]}\n'
                with cls.transcript.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(cls.turn('assistant', text, 7200 + int(n))) + '\n')
                handler.send_response(200); handler.send_header('Content-Length', '0'); handler.end_headers()
                return
            return orig_get(handler)
        cls.patches = [
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
            mock.patch.object(dashboard.Handler, 'do_GET', do_get),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        home = Path(proj.get('home') or proj['path'])
        from PIL import Image, ImageDraw
        cls.pics = {}
        for name in ('drawing0', 'drawing1', 'late1', 'late2'):
            im = Image.new('RGB', (900, 560), (240, 236, 220)); ImageDraw.Draw(im).rectangle([40, 40, 860, 520], outline=(20, 20, 20), width=6)
            f = home / f'{name}.png'; im.save(f); cls.pics[name] = str(f)
        sid = 'settle-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': sid, 'cwd': str(home)}])
        data = chatroom.get_room(po['id'], public=False); data['mode'] = 'solo'; chatroom.update_room(data)
        dashboard.assign_session_project(po['id'], proj['id']); dashboard.set_project_po(proj['id'], po['id'])
        cls.room = po['id']
        at = time.time() - 7300

        def turn(role, text, off):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + off)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type': role, 'timestamp': stamp, 'sessionId': sid, 'message': msg}
        cls.turn = staticmethod(turn)
        lines = []
        for i in range(50):
            lines.append(turn('user', f'Question {i}: what is next on the plan?', 120 * i))
            body = f'## Plan {i}\n\n' + 'Some details of the plan and its steps. ' * 10 + '\n\n- one\n- two\n'
            if i % 6 == 2:
                body += f'\n\nThe drawing: {cls.pics["drawing%d" % (i % 2)]}\n'
            if i == 49:
                body += f'\n\nThe drawing: {cls.pics["late1"]}\n\nRe P375: done, see the drawing above.\n'
            lines.append(turn('assistant', body, 120 * i + 30))
        tdir = base / 'transcripts' / 'C--po'; tdir.mkdir()
        cls.transcript = tdir / f'{sid}.jsonl'
        cls.transcript.write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        threads.note_send(po['id'], '', 'send:settle', [], False, 'Question 0', now=at, new=True)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'room': cls.room}
        script = base / 'chat_settle_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nchat settle CDP: ' + json.dumps(cls.got, indent=1)[:4000])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_a_late_picture_at_the_end_keeps_the_end(self):
        before, after = self.got['beforeLoad'], self.got['afterLoad']
        self.assertTrue(before['stick'], before)
        # The picture was still on its way when the draw put the chat at its end, and it grew the row.
        self.assertTrue(before['lastImgs'] and not before['lastImgs'][0]['done'], before)
        self.assertLess(before['lastImgs'][0]['h'], 20, before)
        self.assertGreater(after['sh'], before['sh'] + 50, (before, after))
        self.assertTrue(after['lastImgs'] and after['lastImgs'][0]['done'], after)
        self.assertGreater(after['lastImgs'][0]['h'], 50, after)
        self.assertLessEqual(after['remaining'], 2, after)

    def test_back_at_the_end_nothing_moves(self):
        for how in ('wheel', 'latest'):
            with self.subTest(how=how):
                g = self.got[how]
                self.assertGreater(g['away']['remaining'], 1000, g['away'])   # it did scroll up
                self.assertLessEqual(g['end']['remaining'], 2, g['end'])
                self.assertEqual(g['writesAfter'], [], g)
                self.assertEqual(g['movesAfter'], [], g)
                self.assertEqual(g['topsAfter'], [], g)
                self.assertEqual(g['shiftsAfter'], [], g)

    def test_the_way_back_is_not_cut_short(self):
        # #387: reaching the end while the reader's scroll still moves draws nothing
        # and writes no scrollTop (a write stops Chrome's smooth scroll with a jump);
        # the end's one draw comes once the scroll has settled.
        for how in ('wheel', 'latest'):
            with self.subTest(how=how):
                g = self.got[how]
                self.assertEqual(g['scrollRenders'], [], g)
                self.assertEqual(g['movingWrites'], [], g)
        self.assertEqual(len(self.got['wheel']['settleRenders']), 1, self.got['wheel'])

    def test_a_new_message_at_the_end_is_followed(self):
        g = self.got['newAtEnd']
        self.assertTrue(g['lastHas1'], g)
        self.assertLessEqual(g['remaining'], 2, g)

    def test_scrolled_up_a_new_picture_moves_nothing(self):
        before, after = self.got['newWhileUp']['before'], self.got['newWhileUp']['after']
        self.assertFalse(before['stick'], before)
        self.assertEqual(after['top'], before['top'], (before, after))
        self.assertGreater(after['remaining'], 1000, after)
        self.assertTrue(after['latestShown'], after)


if __name__ == '__main__':
    unittest.main()
