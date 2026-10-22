"""A question card with links in its words (#238), over CDP on an isolated
hub at 1728 and 390 px: a link to a message (localhost, the https address)
is the balloon's chip in the question and in an option's note, a link after
the point chip naming the same message is not shown again, no "http" is in
the card's text, the chip in an option lands on its message without
answering the card, and an option's links sit beside its answer button, not
in it: Tab reaches each, Enter on the button answers."""
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
    for (const width of [1728, 390]) {
      const mob = width === 390;
      const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
      const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
      const send = (m, p) => c.send(m, p, sessionId);
      await send('Page.enable', {});
      await send('Emulation.setDeviceMetricsOverride', {width, height: mob ? 844 : 1000, deviceScaleFactor:1, mobile:mob});
      await send('Emulation.setTouchEmulationEnabled', {enabled:mob, maxTouchPoints:5});
      const ev = async expression => {
        const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
      };
      const until = async expr => { for (let i=0;i<160;i++) { if(await ev(expr)) return; await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.body.innerText.slice(-1600)')); };
      const press = async sel => {
        const p = await ev(`(() => {const a=document.querySelector(${JSON.stringify(sel)}); a.scrollIntoView({block:'center'}); const b=a.getBoundingClientRect(); return {x:b.x+Math.min(b.width/2, 30),y:b.y+b.height/2};})()`);
        await sleep(400);
        const q = await ev(`(() => {const a=document.querySelector(${JSON.stringify(sel)}); const b=a.getBoundingClientRect(); return {x:b.x+Math.min(b.width/2, 30),y:b.y+b.height/2};})()`);
        if (mob) { await send('Input.dispatchTouchEvent', {type:'touchStart',touchPoints:[q]}); await send('Input.dispatchTouchEvent', {type:'touchEnd',touchPoints:[]}); }
        else for (const type of ['mousePressed','mouseReleased']) await send('Input.dispatchMouseEvent', {type,...q,button:'left',clickCount:1});
        await sleep(200);
      };
      const shot = async name => {if(A.shots) { const r=await send('Page.captureScreenshot',{format:'png'}); fs.writeFileSync(path.join(A.shots,`cardlinks-${width}-${name}.png`),Buffer.from(r.data,'base64')); }};
      await send('Page.navigate', {url:A.base+'/session?room='+A.room});
      const card = `#msgs .msg[data-mid='${A.card}'] .qa`;
      // The chips come once the hub has said what each link and point is.
      await until(`typeof CHAT_DRAWN !== 'undefined' && CHAT_DRAWN && document.querySelectorAll(${JSON.stringify(card + ' a.ref-chip')}).length >= 2 && document.querySelector(${JSON.stringify(card + ' .point-chip')})`);
      await ev(`document.querySelector(${JSON.stringify(card)}).scrollIntoView({block:'center'})`);
      await sleep(300);
      await shot('card');
      const seen = await ev(`(() => {const q=document.querySelector(${JSON.stringify(card)});
        return {text:q.innerText, qt:q.querySelector('.qa-qt').innerHTML,
          chips:[...q.querySelectorAll('a.ref-chip')].map(a => ({msg:a.dataset.refMsg, where:a.closest('.qa-qt') ? 'question' : a.closest('.qa-od') ? 'note' : a.closest('.qa-oa') ? 'label' : 'other'})),
          nested:q.querySelectorAll('button a, button [tabindex]').length,
          words:[...q.querySelectorAll('.qa-opt .qa-ol')].map(e => e.textContent),
          boxes:[...q.querySelectorAll('.qa-optw')].map(e => e.innerText),
          points:[...q.querySelectorAll('.point-chip')].map(a => a.textContent.trim()),
          wide:[...q.querySelectorAll('a.ref-chip')].every(a => a.getBoundingClientRect().right <= q.getBoundingClientRect().right + 1)};})()`);
      // The chip in an option's note opens its message; the card stays open.
      await press(card + ' .qa-od a.ref-chip');
      await until(`LANDED && LANDED.mid === '${A.target}'`);
      await shot('landed');
      const after = await ev(`({open: !document.querySelector(${JSON.stringify(card)}).classList.contains('done'), mid: LANDED.mid})`);
      // By keyboard: Tab from the first answer goes through each option's
      // button and its links in turn; Enter on a button answers with it.
      const tab = async () => {
        await send('Input.dispatchKeyEvent', {type:'rawKeyDown', key:'Tab', code:'Tab', windowsVirtualKeyCode:9});
        await send('Input.dispatchKeyEvent', {type:'keyUp', key:'Tab', code:'Tab', windowsVirtualKeyCode:9});
        await sleep(80);
      };
      const focused = `(() => {const e=document.activeElement; return e.matches('.qa-opt') ? 'button:' + e.dataset.opt : e.closest('.qa-od') ? 'note' : e.closest('.qa-oa') ? 'label' : e.tagName + '.' + e.className;})()`;
      await ev(`document.querySelector(${JSON.stringify(card + ' .qa-opt')}).focus()`);
      const keys = [await ev(focused)];
      for (let i = 0; i < 5; i++) { await tab(); keys.push(await ev(focused)); }
      let answered = null;
      if (mob) {
        await ev(`document.querySelectorAll(${JSON.stringify(card + ' .qa-opt')})[1].focus()`);
        await send('Input.dispatchKeyEvent', {type:'rawKeyDown', key:'Enter', code:'Enter', windowsVirtualKeyCode:13});
        await send('Input.dispatchKeyEvent', {type:'char', key:'Enter', text:'\r'});
        await send('Input.dispatchKeyEvent', {type:'keyUp', key:'Enter', code:'Enter', windowsVirtualKeyCode:13});
        await until(`(q => q.classList.contains('done') && !q.classList.contains('busy'))(document.querySelector(${JSON.stringify(card)}))`);
        answered = await ev(`[...document.querySelectorAll(${JSON.stringify(card + ' .qa-opt[aria-pressed="true"]')})].map(b => b.dataset.opt)`);
      }
      out.push({width, seen, after, keys, answered, overflow:await ev('document.documentElement.scrollWidth > innerWidth')});
      await c.send('Target.closeTarget',{targetId});
    }
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class CardLinksPage(Hub):
    def test_a_card_shows_chips_and_no_url(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(points, 'sync', side_effect=lambda rid, **kw: points.load(rid)); p.start(); self.addCleanup(p.stop)
        self.addCleanup(points._CACHE.clear)
        room = self.room('PO', self.ed)
        dashboard.set_project_po(self.ed, room)
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = self.port = server.server_port
        self.room_id = room
        his = chatroom.post_message(room, 'user', 'Should the test set-up let you choose the browser profile?')['message']
        led = points.load(room)
        point = points._new_point(led, his['text'], 'claude', his['ts'], '', '', n=215, mid=his['id'])
        point['state'] = 'open'
        points._save(room, led)
        filler = '\n\n'.join(f'Line {i} of the plan, long enough to take some room in the chat.' for i in range(8))
        target = chatroom.post_message(room, 'claude', 'The profile notes.\n\n' + filler, to='user')['message']
        for i in range(3):
            chatroom.post_message(room, 'claude', f'Progress note {i}.\n\n' + filler, to='user')
        his_url = f'http://127.0.0.1:{port}/session?room={room}&msg={his["id"]}&part=pt:P215'
        asks_ = [{'n': 0, 'kind': 'decision', 'recommended': 0,
                  'question': f'Should the TEST set-up let you choose the profile (P215)? {his_url} '
                              f'The notes: http://localhost:{port}/session?room={room}&msg={target["id"]}',
                  'options': [{'label': 'Choose it', 'recommended': True,
                               'detail': f'as in https://hub.example.net/session?room={room}&msg={target["id"]}'},
                              {'label': f'Keep the default, see http://localhost:{port}/session?room={room}&msg={target["id"]}',
                               'detail': '', 'recommended': False},
                              {'label': 'Use (P215)', 'detail': '', 'recommended': False}]}]
        card = chatroom.post_message(room, 'claude', 'Re P215: one decision.', to='user')['message']
        full = chatroom.get_room(room, public=False)
        full['messages'] = [{**m, 'asks': asks_, 'askAudience': 'user'} if m['id'] == card['id'] else m
                            for m in full['messages']]
        chatroom.update_room(full)
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': f'http://127.0.0.1:{port}',
                'room': room, 'card': card['id'], 'target': target['id'], 'shots': shots}
        script = base / 'cardlinks_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        rows = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([row['width'] for row in rows], [1728, 390])
        for row in rows:
            with self.subTest(width=row['width']):
                seen = row['seen']
                self.assertNotIn('http', seen['text'], seen['text'])
                self.assertNotIn('localhost', seen['text'])
                self.assertEqual(seen['points'], ['P215', 'P215'], 'in the question and in the third option')
                self.assertEqual(sorted((c['where'], c['msg']) for c in seen['chips']),
                                 [('label', target['id']), ('note', target['id']), ('question', target['id'])],
                                 'the point\'s own link is not shown again after its chip')
                self.assertTrue(seen['wide'], 'chips stay inside the card')
                self.assertEqual(seen['nested'], 0, 'no link inside an answer button')
                second = f'Keep the default, see http://localhost:{self.port}/session?room={self.room_id}&msg={target["id"]}'
                self.assertEqual(row['keys'][:6], ['button:Choose it', 'note', 'button:' + second, 'label', 'button:Use (P215)', 'label'])
                self.assertEqual(seen['words'], ['Choose it', 'Keep the default', 'Use'], 'a button says its words; its link is beside it')
                self.assertEqual(seen['boxes'][2].count('P215'), 1, 'the point shows once: as its chip')
                self.assertEqual(row['after'], {'open': True, 'mid': target['id']})
                self.assertFalse(row['overflow'])
        self.assertEqual(rows[1]['answered'], [second], 'Enter on an answer button answers with it')


if __name__ == '__main__':
    unittest.main()
