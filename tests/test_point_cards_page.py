"""A point chip whose point waits on a question card (#206), over CDP on an
isolated hub at 1440 and 390 px: the chip's card offers the question first
and his message second, and each lands centred on what it names."""
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
from tests.test_point_cards import ASK_A
from tests.test_task_numbers import Hub
from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, NODE, CHROME

CDP_JS = TASK_CDP.split('// The chips in the conversation')[0] + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = [];
  try {
    for (const width of [1440, 390]) {
      const mob = width === 390;
      const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
      const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
      const send = (m, p) => c.send(m, p, sessionId);
      await send('Page.enable', {});
      await send('Emulation.setDeviceMetricsOverride', {width, height: mob ? 844 : 900, deviceScaleFactor:1, mobile:mob});
      await send('Emulation.setTouchEmulationEnabled', {enabled:mob, maxTouchPoints:5});
      const ev = async expression => {
        const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
      };
      const chip = "#msgs .msg[data-mid='"+A.status+"'] .point-chip";
      const until = async expr => { for (let i=0;i<160;i++) { if(await ev(expr)) return; await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.body.innerText.slice(-1600)')); };
      const press = async sel => {
        const p = await ev(`(() => {const a=document.querySelector(${JSON.stringify(sel)}); if(!a.closest('.task-card')) a.scrollIntoView({block:'center'}); const b=a.getBoundingClientRect(); return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
        if (mob) { await send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:[p]}); await send('Input.dispatchTouchEvent', {type:'touchEnd',touchPoints:[]}); }
        else for (const type of ['mousePressed','mouseReleased']) await send('Input.dispatchMouseEvent', {type,...p,button:'left',clickCount:1});
        await sleep(150);
      };
      // A click on a control that just moved is not taken: wait
      // for the chip to settle, and press again if its card did not open.
      const openCard = async () => {
        for (let k = 0; k < 4; k++) {
          await ev(`document.querySelector(${JSON.stringify(chip)}).scrollIntoView({block:'center'})`);
          await sleep(700);
          await press(chip);
          for (let i = 0; i < 20; i++) { if (await ev("!!document.querySelector('.task-card .tc-link')")) return; await sleep(150); }
        }
        throw Error('the chip never opened its card');
      };
      const shot = async name => {if(A.shots) { const r=await send('Page.captureScreenshot',{format:'png'}); fs.writeFileSync(path.join(A.shots,`pointcard-${width}-${name}.png`),Buffer.from(r.data,'base64')); }};
      // Where the landed mark is, against the chat's middle.
      const landed = () => ev(`(() => {const box=document.querySelector('#msgs'), el=document.querySelector('#msgs .landed'); if(!el) return null;
        const b=box.getBoundingClientRect(), r=el.getBoundingClientRect();
        return {mid:LANDED.mid, part:LANDED.part, qa:el.matches('.qa'), ask:el.dataset.ask||'', off:Math.round((r.top+r.height/2)-(b.top+b.height/2)), seen:r.top>=b.top-1 && r.bottom<=b.bottom+1};})()`);
      await send('Page.navigate', {url:A.base+'/session?room='+A.room});
      await until(`typeof CHAT_DRAWN !== 'undefined' && CHAT_DRAWN && document.querySelectorAll(${JSON.stringify(chip)}).length >= 2`);
      const chips = await ev(`[...document.querySelectorAll(${JSON.stringify(chip)})].map(a => ({msg:a.dataset.refMsg, part:a.dataset.refPart}))`);
      await openCard();
      await shot('card');
      const card = await ev(`(() => {const c=document.querySelector('.task-card'),r=c.getBoundingClientRect();
        return {links:[...c.querySelectorAll('.tc-link')].map(a => ({text:a.textContent, msg:a.dataset.refMsg, part:a.dataset.refPart, h:Math.round(a.getBoundingClientRect().height)})),
                left:r.left, right:r.right, coarse:matchMedia('(pointer: coarse)').matches};})()`);
      await press('.task-card .tc-link');
      await until(`LANDED && LANDED.mid === '${A.card}' && document.querySelector('#msgs .landed')`);
      await shot('question');
      const toCard = await landed();
      await sleep(2700);
      await openCard();
      await press('.task-card .tc-link:nth-child(2)');
      await until(`LANDED && LANDED.mid === '${A.his}' && document.querySelector('#msgs .landed')`);
      await shot('his');
      const toHis = await landed();
      out.push({width, chips, card, toCard, toHis, overflow:await ev('document.documentElement.scrollWidth > innerWidth')});
      await c.send('Target.closeTarget',{targetId});
    }
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class PointCardPage(Hub):
    def test_chip_lands_on_the_question_and_card_offers_both(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        room = self.room('PO', self.ed)
        dashboard.set_project_po(self.ed, room)
        his = chatroom.post_message(room, 'user', 'Why up to 4 legs, and what is a future leg?')['message']
        led = points.load(room)
        point = points._new_point(led, his['text'], 'claude', his['ts'], '', '', n=499, mid=his['id'])
        point['state'] = 'delivered'
        points._save(room, led)
        filler = '\n\n'.join(f'Line {i} of the answer, long enough to take some room in the chat.' for i in range(12))
        chatroom.post_message(room, 'claude', 'Re P499: why 4.\n\n' + filler, to='user')
        card = chatroom.post_message(room, 'claude', 'Re P499 (the leg limit).', to='user')['message']
        full = chatroom.get_room(room, public=False)
        full['messages'] = [{**m, 'asks': ASK_A, 'askAudience': 'user'} if m['id'] == card['id'] else m
                            for m in full['messages']]
        chatroom.update_room(full)
        for i in range(4):
            chatroom.post_message(room, 'claude', f'Progress note {i}.\n\n' + filler, to='user')
        status = chatroom.post_message(room, 'claude', '**Waiting on your decisions:**\n- The P499 card: 4 legs vs up to 8.\n- P499 again.', to='user')['message']
        p = mock.patch.object(points, 'sync', side_effect=lambda rid, **kw: points.load(rid)); p.start(); self.addCleanup(p.stop)
        self.addCleanup(points._CACHE.clear)
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port),
                'room': room, 'his': his['id'], 'card': card['id'], 'status': status['id'], 'shots': shots}
        script = base / 'pointcard_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        rows = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([row['width'] for row in rows], [1440, 390])
        for row in rows:
            with self.subTest(width=row['width']):
                self.assertEqual(row['chips'], [{'msg': card['id'], 'part': 'ask:0'}] * 2,
                                 'the open card takes both chips, "the P499 card" and a bare P499')
                self.assertEqual([(l['text'], l['msg'], l['part']) for l in row['card']['links']],
                                 [('Answer the question ↓', card['id'], 'ask:0'), ('His message', his['id'], 'pt:P499')])
                self.assertEqual(row['card']['coarse'], row['width'] == 390)
                if row['width'] == 390:
                    self.assertTrue(all(l['h'] >= 44 for l in row['card']['links']), row['card']['links'])
                self.assertGreaterEqual(row['card']['left'], 0)
                self.assertLessEqual(row['card']['right'], row['width'])
                self.assertTrue(row['toCard']['qa'], 'the question card itself is marked')
                self.assertEqual(row['toCard']['ask'], card['id'] + ':0')
                self.assertLessEqual(abs(row['toCard']['off']), 4, 'centred in the chat')
                self.assertEqual(row['toHis']['mid'], his['id'])
                self.assertTrue(row['toHis']['seen'], 'his message (the first in the chat: as near the middle as it goes) in view')
                self.assertFalse(row['overflow'])


if __name__ == '__main__':
    unittest.main()
