"""Real session balloons, cards and jumps over CDP on an isolated hub."""
import json
import os
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import points
from tests import chrome_profile
from tests.test_task_numbers import Hub
from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, NODE, CHROME

CDP_JS = TASK_CDP.split('// The chips in the conversation')[0] + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = [];
  try {
    for (const width of [1280, 390]) for (const room of [A.room, A.task]) {
      const mob = width === 390;
      const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
      const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
      const send = (m, p) => c.send(m, p, sessionId);
      await send('Page.enable', {});
      await send('Emulation.setDeviceMetricsOverride', {width, height:844, deviceScaleFactor:1, mobile:mob});
      await send('Emulation.setTouchEmulationEnabled', {enabled:mob, maxTouchPoints:5});
      const ev = async expression => {
        const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
      };
      const until = async expr => { for (let i=0;i<160;i++) { if(await ev(expr)) return; await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.body.innerText.slice(-1600)')); };
      const press = async (sel, clicks=1) => {
        const p = await ev(`(() => {const a=document.querySelector(${JSON.stringify(sel)}); a.scrollIntoView({block:'center'}); const b=a.getBoundingClientRect(); return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
        if (mob) { await send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:[p]}); await send('Input.dispatchTouchEvent', {type:'touchEnd',touchPoints:[]}); }
        else for (const type of ['mousePressed','mouseReleased']) await send('Input.dispatchMouseEvent', {type,...p,button:'left',clickCount:clicks});
        await sleep(100);
      };
      const shot = async name => {if(A.shots) { const r=await send('Page.captureScreenshot',{format:'png'}); fs.writeFileSync(path.join(A.shots,`point-${width}-${room===A.room?'po':'task'}-${name}.png`),Buffer.from(r.data,'base64')); }};
      await send('Page.navigate', {url:A.base+'/session?room='+room});
      await until(`typeof CHAT_DRAWN !== 'undefined' && CHAT_DRAWN && document.querySelectorAll('#msgs .point-chip').length >= 2`);
      await until("document.querySelectorAll('#msgs li .point-chip').length >= 2 && document.querySelectorAll('#msgs strong .point-chip').length >= 2");
      const a = "#msgs .point-chip[data-ref-msg='"+A.mid+"']";
      await press(a);
      await until("!!document.querySelector('.task-card')");
      const card = await ev(`(() => {const c=document.querySelector('.task-card'),r=c.getBoundingClientRect(); return {text:c.textContent, title:c.querySelector('.tc-title').textContent, left:r.left,right:r.right,coarse:matchMedia('(pointer: coarse)').matches};})()`);
      await shot('card');
      // A second touch has no double-tap jump; the card link is the action.
      if(mob) {
        await ev(`document.querySelector(${JSON.stringify(a)}).dispatchEvent(new MouseEvent('dblclick',{bubbles:true,cancelable:true}))`);
        if(await ev('!!LANDED')) throw Error('touch double click jumped');
      }
      await press('.task-card .tc-title');
      await until(`LANDED && LANDED.mid === '${A.mid}'`);
      await shot('jump');
      const jump = await ev('({mid:LANDED.mid,part:LANDED.part,marked:!!document.querySelector(".landed")})');
      // Cross-room navigation loads a fresh resolver. Landing precedes its lookups.
      await until(`document.querySelector("#msgs .point-chip[data-ref-msg='${A.mid}']")`);
      // Render the same balloon as a folded row through the real renderer.
      const folded = await ev(`(() => {const m=LAST_ITEMS.find(m => (m.text||'').includes('Mention **P291**')); const h=foldBalloonHtml(m,0,'row',{md:mdToHtml,lineHtml:foldedPointHtml}); const box=document.createElement('div');box.id='point-fold-test';box.innerHTML=h;box.style.cssText='position:fixed;top:100px;left:8px;right:8px;z-index:1100';document.body.appendChild(box); return !!box.querySelector('.point-chip');})()`);
      await press('#point-fold-test .point-chip');
      await until("!!document.querySelector('.task-card')");
      await shot('folded');
      if(!mob) {await press('#point-fold-test .point-chip',2); await until(`LANDED && LANDED.mid === '${A.mid}'`);}
      out.push({width,room,card,jump,folded,overflow:await ev('document.documentElement.scrollWidth > innerWidth')});
      await c.send('Target.closeTarget',{targetId});
    }
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class PointPage(Hub):
    def test_chip_card_and_jump_desktop_and_touch(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        room = self.room('PO', self.ed)
        dashboard.set_project_po(self.ed, room)
        task = self.room('Task chat', self.ed)
        other = self.room('Dock PO', self.ot)
        dashboard.set_project_po(self.ot, other)
        dashboard.set_project_key(self.ot, 'DK')
        mids = {}
        for rid, text in [(room, 'Make the point context easy to find.'), (other, 'Dock point context.')]:
            m = chatroom.post_message(rid, 'user', text)['message']
            mids[rid] = m['id']
            led = points.load(rid)
            point = points._new_point(led, text, 'claude', m['ts'], '', '', n=291, mid=m['id'])
            point['state'] = 'acked'
            points._save(rid, led)
        for rid in [room, task]:
            chatroom.post_message(rid, 'claude', 'Mention **P291** here.\n\n- DK P291 is elsewhere.\n- outer\n    - P291 nested.\n\n__P291__ also bold.\n\n`P291` and P2P stay text.', to='user')
        # Keep ledger fixture exact; the test exercises HTTP and page behavior.
        p = mock.patch.object(points, 'sync', side_effect=lambda rid, **kw: points.load(rid)); p.start(); self.addCleanup(p.stop)
        self.addCleanup(points._CACHE.clear)
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:'+str(server.server_port),
                'room': room, 'task': task, 'mid': mids[room], 'shots': shots}
        script = base / 'point_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        rows = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(len(rows), 4)
        for row in rows:
            with self.subTest(width=row['width'], room=row['room']):
                self.assertEqual(row['card']['title'], 'Make the point context easy to find.')
                self.assertIn('acknowledged', row['card']['text'])
                self.assertEqual(row['card']['coarse'], row['width'] == 390)
                self.assertGreaterEqual(row['card']['left'], 0)
                self.assertLessEqual(row['card']['right'], row['width'])
                self.assertEqual(row['jump']['part'], 'pt:P291')
                self.assertTrue(row['jump']['marked'])
                self.assertTrue(row['folded'])
                self.assertFalse(row['overflow'])


if __name__ == '__main__': unittest.main()
