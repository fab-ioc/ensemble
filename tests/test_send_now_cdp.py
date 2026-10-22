"""Send now in a real Chrome (#252): the chat offers it beside Send only while
the agent the message goes to works, names it for that agent's kind, sends
``now`` with Alt+Enter or the menu, and says how a send went in. Measured at
1728 and 390 (touch), in Light and Dark."""
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
import sends  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
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
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async expression => { for(let i=0;i<100;i++){ try {if(await ev(expression)) return;}catch(e){} await sleep(150); } throw Error('timeout '+expression); };
  const size=async (w,h,touch) => {
    await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:touch},s);
    await send('Emulation.setTouchEmulationEnabled',{enabled:touch,...(touch?{maxTouchPoints:5}:{})},s);
    await send('Emulation.setEmitTouchEventsForMouse',{enabled:touch},s);
  };
  const altEnter=async (code='Enter') => {
    for (const type of ['rawKeyDown','keyUp'])
      await send('Input.dispatchKeyEvent',{type,modifiers:1,key:'Enter',code,windowsVirtualKeyCode:13},s);
  };
  // What the page shows: the split part, its menu's words, its geometry and colours.
  const look = `(() => {
    const b=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,r:r.right,b:r.bottom};};
    const lum=c=>{const v=c.match(/[\\d.]+/g).slice(0,3).map(x=>{x/=255;return x<=.03928?x/12.92:Math.pow((x+.055)/1.055,2.4)});return .2126*v[0]+.7152*v[1]+.0722*v[2];};
    const ratio=(f,g)=>{const a=lum(f),b2=lum(g);return (Math.max(a,b2)+.05)/(Math.min(a,b2)+.05);};
    const more=document.getElementById('send-more'), menu=document.getElementById('send-menu'), item=document.getElementById('send-now');
    const out={shown:!more.hidden && more.getClientRects().length>0, name:item.querySelector('.sm-name').textContent,
      sub:item.querySelector('.sm-sub').textContent, coarse:matchMedia('(pointer: coarse)').matches,
      overflow:document.documentElement.scrollWidth-innerWidth, send:b(document.getElementById('send')),
      compose:b(document.getElementById('compose')), how:[...document.querySelectorAll('.send-how')].map(e=>e.textContent),
      pend:[...document.querySelectorAll('.msg.user .pend-state')].map(e=>e.textContent.trim())};
    if (out.shown) {
      out.more=b(more);
      more.click();
      out.menuOpen=!menu.hidden; out.menu=b(menu); out.focus=document.activeElement===item;
      const bg=getComputedStyle(menu).backgroundColor;
      out.nameContrast=ratio(getComputedStyle(item.querySelector('.sm-name')).color,bg);
      out.subContrast=ratio(getComputedStyle(item.querySelector('.sm-sub')).color,bg);
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      out.escClosed=menu.hidden && document.activeElement===more;
    }
    return out;
  })()`;
  const open=async (room, theme) => {
    await send('Page.navigate',{url:A.base+'/session?id='+room+'&embed=1'},s);
    await until(`typeof ROOM_OBJ!=='undefined' && !!ROOM_OBJ && document.getElementById('send') && !document.getElementById('send').disabled`);
    await ev(`document.documentElement.setAttribute('data-theme', ${JSON.stringify(theme)}); 0`);
    await sleep(300);
  };
  const out={};
  try {
    for (const [w,h,touch] of [[1728,1117,false],[390,844,true]]) {
      for (const theme of ['light','dark']) {
        await size(w,h,touch);
        const k=w+'-'+theme;
        await open(A.claude, theme); out[k+'-claude']=await ev(look);
        await open(A.codex, theme); out[k+'-codex']=await ev(look);
        await open(A.idle, theme); out[k+'-idle']=await ev(look);
        await open(A.team, theme); out[k+'-team']=await ev(look);
        await ev(`(() => { const i=document.getElementById('input'); i.value='@codex look at this'; i.dispatchEvent(new Event('input',{bubbles:true})); return 0; })()`);
        out[k+'-team-codex']=await ev(look);
      }
    }
    // Alt+Enter and the menu send ``now``; Send, Ctrl+Enter do not; idle sends plainly.
    await size(1728,1117,false);
    const capture = `(() => { window.__bodies=[]; window.fetch=(u,o)=>{ if(String(u).includes('/api/room/resume')) {
      window.__bodies.push(JSON.parse(o.body)); return Promise.resolve(new Response(JSON.stringify({ok:true}),{status:200})); }
      return Promise.resolve(new Response('{}',{status:200})); }; return 0; })()`;
    const type = t => ev(`(() => { const i=document.getElementById('input'); i.focus(); i.value=${JSON.stringify(t)}; i.dispatchEvent(new Event('input',{bubbles:true})); return 0; })()`);
    out.sent={};
    await open(A.claude,'light'); await ev(capture);
    await type('alt enter'); await altEnter(); await sleep(300);
    await type('keypad'); await altEnter('NumpadEnter'); await sleep(300);
    await type('plain'); await ev(`document.getElementById('send').click(); 0`); await sleep(300);
    await type('from menu'); await ev(`document.getElementById('send-more').click(); document.getElementById('send-now').click(); 0`); await sleep(300);
    out.sent.claude=await ev('window.__bodies');
    await open(A.idle,'light'); await ev(capture);
    await type('idle alt'); await altEnter(); await sleep(300);
    out.sent.idle=await ev('window.__bodies');
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class SendNowPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-send-now-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        working = lambda p, *a, **k: ('idle' if p.get('ptyId') == 'pty-idle' else 'working', '')  # noqa: E731
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
            mock.patch.object(dashboard, '_room_is_live', lambda room: True),
            mock.patch.object(dashboard.attention, 'turn_state', working),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)

        def room(title, parts, solo):
            r = chatroom.create_room(title, parts)
            data = chatroom.get_room(r['id'], public=False)
            data['mode'] = 'solo' if solo else 'collab'
            chatroom.update_room(data)
            for part in parts:      # a running terminal (create_room keeps none)
                chatroom.patch_participant(r['id'], part['identity'], {'ptyId': part['ptyId']})
            return r['id']
        claude = {'identity': 'claude', 'agent': 'claude', 'ptyId': 'pty-claude', 'cwd': str(root)}
        codex = {'identity': 'codex', 'agent': 'codex', 'ptyId': 'pty-codex', 'cwd': str(root)}
        cls.claude = room('Solo claude', [claude], True)
        cls.codex = room('Solo codex', [codex], True)
        cls.idle = room('Idle claude', [{**claude, 'ptyId': 'pty-idle'}], True)
        cls.team = room('Team', [{**claude, 'role': 'engineer'}, {**codex, 'role': 'designer'}], False)
        # A delivered Send now in the solo chat; a confirmed one in the team's.
        sends.accept(cls.claude, 'k-solo', 'please fix the title')
        sends.mark(cls.claude, ['k-solo'], 'delivered')
        sends.note_how(cls.claude, 'k-solo', 'into-turn')
        posted = chatroom.post_message(cls.team, 'user', 'stop and look at the diff')
        mid = posted['message']['id']
        sends.accept(cls.team, 'k-team', 'stop and look at the diff')
        sends.mark(cls.team, ['k-team'], 'confirmed', mid=mid)
        sends.note_how(cls.team, 'k-team', 'interrupted')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'claude': cls.claude, 'codex': cls.codex, 'idle': cls.idle, 'team': cls.team}
        script = base / 'send_now_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nsend now CDP: ' + json.dumps({k: {x: v[x] for x in ('shown', 'name', 'more', 'menu', 'nameContrast',
                                                                       'subContrast', 'overflow') if x in v}
                                               for k, v in cls.got.items() if k != 'sent'}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def views(self, kind):
        return {k: v for k, v in self.got.items() if k.endswith('-' + kind)}

    def test_offered_only_while_its_agent_works_named_for_its_kind(self):
        for kind, name in (('claude', 'Send now'), ('codex', 'Interrupt and send'),
                           ('team', 'Send now'), ('team-codex', 'Interrupt and send')):
            for k, v in self.views(kind).items():
                with self.subTest(k):
                    self.assertTrue(v['shown'], v)
                    self.assertEqual(v['name'], name)
                    self.assertTrue(v['menuOpen'] and v['focus'] and v['escClosed'], v)
        for k, v in self.views('idle').items():
            with self.subTest(k):
                self.assertFalse(v['shown'], v)

    def test_fits_and_reads_at_both_widths(self):
        for k, v in self.got.items():
            if k == 'sent' or not v.get('shown'):
                continue
            with self.subTest(k):
                w = 1728 if k.startswith('1728') else 390
                self.assertEqual(v['coarse'], w == 390)
                self.assertLessEqual(v['overflow'], 0, v)
                self.assertLessEqual(v['menu']['r'], w, v)
                self.assertGreaterEqual(v['menu']['x'], 0, v)
                self.assertLessEqual(v['more']['r'], v['compose']['r'], v)
                self.assertAlmostEqual(v['more']['y'], v['send']['y'], delta=1)
                self.assertGreaterEqual(v['more']['h'], 44 if w == 390 else 32, v)
                if w == 390:
                    self.assertGreaterEqual(v['more']['w'], 44, v)
                self.assertGreaterEqual(v['nameContrast'], 4.5, v)
                self.assertGreaterEqual(v['subContrast'], 4.5, v)

    def test_the_balloon_says_how_it_went_in(self):
        for k, v in self.views('claude').items():
            with self.subTest(k):
                self.assertTrue(any('Sent into the running turn; not yet read by the agent.' in t for t in v['pend']), v)
        for k, v in self.views('team').items():
            with self.subTest(k):
                self.assertIn('· Interrupted, then sent', v['how'])

    def test_alt_enter_and_the_menu_send_now(self):
        claude = self.got['sent']['claude']
        self.assertEqual([(b['text'], b.get('now', False)) for b in claude],
                         [('alt enter', True), ('keypad', True), ('plain', False), ('from menu', True)])
        self.assertEqual([(b['text'], b.get('now', False)) for b in self.got['sent']['idle']],
                         [('idle alt', False)])


if __name__ == '__main__':
    unittest.main()
