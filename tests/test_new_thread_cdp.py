"""Threads only when the person starts one (#367, P346), in a real Chrome.

* The box's thread icon (#new-thread): icon only, off by default; pressed,
  the To line reads "a new thread" and the box's send carries ``newThread``;
  it turns off once sent, is not in a thread's own box, and a comment batch
  never carries it.
* Sent through the hub, the message is a thread the person made (a ``sent``
  row with ``new``), and the PO's answer makes it a thread root with its one
  thread row in the chat.
* One row: a thread's root shows its thread row and no state row of its own;
  a balloon in the main chat whose point is answered or acknowledged shows
  one state row, "done — see the answer ↓", which goes to the answer; no
  "no answer yet", no "acknowledged" word, Reopen only on hover.
* No contradictions: a thread whose points are all acknowledged never says
  it needs the person.
"""
from __future__ import annotations

import json
import os
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
import points  # noqa: E402
import sends  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_thread_panel_cdp import CDP_JS as PANEL_JS, NODE  # noqa: E402

# The panel test's Chrome and CDP helpers, with this test's steps, on the PO
# chat's own page.
CDP_JS = PANEL_JS[:PANEL_JS.index('  const PO = ')] + r"""
  const out={};
  const BAL = tok => `[...document.querySelectorAll('#msgs .msg[data-mid]')].find(m => m.textContent.includes(${JSON.stringify(tok)}))`;
  // What the balloon holding tok shows under it: its state row and its thread row.
  const ROWS = tok => `(() => { const b=${BAL(tok)}; if (!b) return null;
    const line=b.nextElementSibling && b.nextElementSibling.matches('.thr-line') ? b.nextElementSibling : null;
    const under=b.querySelector('.pt-under');
    return {mid:b.dataset.mid, under:under?under.textContent.replace(/\\s+/g,' ').trim():'', unders:b.querySelectorAll('.pt-under').length,
      lines:(() => { let n=0, e=b.nextElementSibling; while (e && e.matches('.thr-line')) { n++; e=e.nextElementSibling; } return n; })(),
      line:line?line.textContent.replace(/\\s+/g,' ').trim():'', lineState:line?.querySelector('.thr-state')?.textContent||'',
      noAnswer:!!b.querySelector('.no-answer'), text:b.textContent.replace(/\\s+/g,' ').trim(),
      reopen:[...b.querySelectorAll('[data-pt-act="reopen"]')].map(x=>x.className),
      link:b.querySelector('.pt-under a.pt-link')?.dataset.refMsg||''}; })()`;
  const TOG = `(() => { const b=document.getElementById('new-thread'); return b ? {hidden:b.hidden, shown:!!b.offsetParent,
    text:b.textContent.trim(), label:b.getAttribute('aria-label'), pressed:b.getAttribute('aria-pressed'), svg:!!b.querySelector('svg'),
    to:document.getElementById('compose-destination-link')?.textContent||''} : null; })()`;
  try {
    await size(1500,1000);
    await send('Page.navigate',{url:A.base+'/session?id='+encodeURIComponent(A.po)},s);
    await until(`typeof THREADED !== 'undefined' && THREADED && !!document.querySelector('#msgs .msg[data-mid]') && !!(${BAL('TOKT')})`, 'PO chat');
    await sleep(800);
    // 3 / 4: what the fixture's balloons show.
    out.acked = await ev(ROWS('TOKA'));
    out.delivered = await ev(ROWS('TOKD'));
    out.thread = await ev(ROWS('TOKT'));
    out.answerMid = await ev(`${BAL('TOKA-answer')}?.dataset.mid || ''`);
    // The state row's link goes to the answer.
    await ev(`window.scrollTo(0,0); document.getElementById('msgs').scrollTop=0; ${BAL('TOKA')}.querySelector('.pt-under a.pt-link').click(); 0`);
    await sleep(900);
    out.jump = await ev(`(() => { const a=${BAL('TOKA-answer')}; if (!a) return {found:false}; const r=a.getBoundingClientRect();
      return {found:true, inView:r.bottom>0 && r.top<innerHeight, landed:a.classList.contains('landed')||a.classList.contains('ibx-landed'), url:location.search}; })()`);
    // 1: the toggle.
    out.tog0 = await ev(TOG);
    await ev(`document.getElementById('new-thread').click(); 0`);
    out.tog1 = await ev(TOG);
    // A comment batch never carries it; the box's send does.
    out.routes = await ev(`(async () => { const old=window.postOk; const bodies=[];
      window.postOk=async (_p,b) => { bodies.push(b); return {}; };
      try { await sendResuming('probe batch','', 'probe-1', []); await sendResuming('probe box','', 'probe-2', [], false, true); }
      finally { window.postOk=old; }
      return bodies.map(b => ({newThread:b.newThread===true, thread:b.thread||''})); })()`);
    // The real send through the hub, with the toggle on.
    await ev(`document.getElementById('input').value='TOKN a thread about colours'; document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true})); 0`);
    await until(`document.getElementById('send').disabled===false`, 'send ready');
    await ev(`sendNow().then(()=>0)`);
    await sleep(400);
    out.tog2 = await ev(TOG);
    await until(`(() => { const b=${BAL('TOKN')}; return !!b && b.nextElementSibling?.matches('.thr-line'); })()`, 'TOKN thread row');
    out.made = await ev(ROWS('TOKN'));
    out.channelHasAnswer = await ev(`!!(${BAL('Answer to TOKN')})`);
    // Review 1: a new thread nobody answered yet has its row at once.
    await ev(`document.getElementById('new-thread').click(); 0`);
    await ev(`document.getElementById('input').value='TOKW a thread nobody answered yet'; document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true})); 0`);
    await until(`document.getElementById('send').disabled===false`, 'send ready (TOKW)');
    await ev(`sendNow().then(()=>0)`);
    await until(`(() => { const b=${BAL('TOKW')}; return !!b && b.nextElementSibling?.matches('.thr-line'); })()`, 'TOKW thread row');
    out.waiting = await ev(ROWS('TOKW'));
    // A thread's own box has no thread icon.
    await ev(`${BAL('TOKN')}.nextElementSibling.querySelector('.thr-open').click(); 0`);
    const TS = `document.getElementById('ts-frame')?.contentDocument`;
    await until(`!!${TS}?.querySelector('#msgs .msg[data-mid]') && ${TS}.querySelector('#msgs').textContent.includes('Answer to TOKN')`, 'thread opened');
    await sleep(500);
    out.inThread = await ev(`(() => { const d=${TS}, b=d.getElementById('new-thread'); return {exists:!!b, hidden:!b || b.hidden,
      to:d.getElementById('compose-destination-link')?.textContent||''}; })()`);
    out.mainWhileOpen = await ev(TOG);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class NewThread(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-new-thread-', ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard, '_send_left_behind', lambda *a, **k: ''),
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, '_resume_room', cls._deliver),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        cls.sid = sid = 'new-thread-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner',
                                               'sessionId': sid, 'cwd': str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        (base / 'transcripts' / 'C--po').mkdir()
        cls.transcript = base / 'transcripts' / 'C--po' / f'{sid}.jsonl'
        at = time.time() - 3600
        room = chatroom.get_room(cls.po, public=False)
        # Three messages typed in the main box: P1 answered and acknowledged,
        # P2 answered; P3 sent as a new thread (the box's icon), answered there
        # and acknowledged.
        said = [points.take(room, t, '', k, now=at + n) for t, k, n in
                (('TOKA make the chart wider', 'send:a', 1), ('TOKD put the totals under it', 'send:d', 10),
                 ('TOKT start a thread about the export', 'send:t', 20))]
        (ta, (pa,)), (td, (pd,)), (tt, (pt,)) = said
        threads.note_send(cls.po, '', 'send:t', [pt], False, tt, now=at + 20, new=True)
        lines = [cls.turn('user', ta, at + 1),
                 cls.turn('assistant', f'Re {pa}: TOKA-answer Done: the chart is wider.', at + 2),
                 cls.turn('user', td, at + 10),
                 cls.turn('assistant', f'Re {pd}: TOKD-answer Done: the totals are under it.', at + 11),
                 cls.turn('user', tt, at + 20),
                 cls.turn('assistant', f'Re {pt}: TOKT-answer The export takes a day.', at + 21)]
        cls.transcript.write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        points.sync(cls.po, force=True)
        for pid in (pa, pt):
            assert points.act(cls.po, pid, 'ack', now=at + 30), pid
        cls.pids = {'A': pa, 'D': pd, 'T': pt}
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'proj': cls.proj, 'po': cls.po}
        script = base / 'new_thread_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.side = threads.load_side(cls.po)
        print('\nnew thread CDP: ' + json.dumps(cls.got, indent=1))

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.%03dZ' % int(ts % 1 * 1000)
        msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
        if role == 'assistant':
            msg['stop_reason'] = 'end_turn'
        return {'type': role, 'timestamp': stamp, 'sessionId': cls.sid, 'message': msg}

    @staticmethod
    def _deliver(h, room, text='', to='', key='', quiet=False, **kw):
        """Typed in at once; the PO reads it a second later and answers its point."""
        cls = NewThread
        sends.mark(room['id'], [key], 'delivered')
        pid = next((p['id'] for p in points.load(room['id']).get('points') or [] if p.get('key') == key), '')

        def read():
            time.sleep(1.0)
            tok = next((w for w in text.split() if w.startswith('TOK')), '')
            now = time.time()
            with cls.transcript.open('a', encoding='utf-8') as f:
                f.write(json.dumps(cls.turn('user', text, now)) + '\n')
                if tok != 'TOKW':       # TOKW: not answered (yet)
                    f.write(json.dumps(cls.turn('assistant', f'Re {pid}: Answer to {tok}.', now + .5)) + '\n')
        threading.Thread(target=read, daemon=True).start()
        return {'delivered': 1}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    # 1 -----------------------------------------------------------------
    def test_the_thread_icon_is_an_icon_only_toggle_that_aims_the_box(self):
        t0, t1 = self.got['tog0'], self.got['tog1']
        self.assertEqual((t0['hidden'], t0['shown'], t0['svg'], t0['text']), (False, True, True, ''), t0)
        self.assertEqual((t0['label'], t0['pressed']), ('New thread', 'false'), t0)
        self.assertNotEqual(t0['to'], 'a new thread', t0)
        self.assertEqual((t1['pressed'], t1['to']), ('true', 'a new thread'), t1)

    def test_only_the_boxs_send_carries_new_thread(self):
        batch, box = self.got['routes']
        self.assertEqual(batch, {'newThread': False, 'thread': ''})
        self.assertEqual(box, {'newThread': True, 'thread': ''})

    def test_the_toggle_turns_off_once_sent_and_is_not_in_a_threads_box(self):
        t2 = self.got['tog2']
        self.assertEqual(t2['pressed'], 'false', t2)
        self.assertNotEqual(t2['to'], 'a new thread', t2)
        self.assertTrue(self.got['inThread']['hidden'], self.got['inThread'])
        # The chat's own box, aimed at the open thread (#364), has none either.
        self.assertTrue(self.got['mainWhileOpen']['hidden'], self.got['mainWhileOpen'])
        self.assertNotEqual(self.got['inThread']['to'], 'a new thread', self.got['inThread'])

    # 2 -----------------------------------------------------------------
    def test_a_message_sent_as_a_new_thread_starts_one(self):
        rows = [a for a in self.side.get('sent') or [] if a.get('new')]
        self.assertEqual(sorted(a.get('thread') for a in rows), ['', '', ''], rows)   # TOKT, TOKN, TOKW
        self.assertIn('send:t', [a.get('key') for a in rows])
        made = self.got['made']
        self.assertEqual(made['lines'], 1, made)
        self.assertIn('1 reply', made['line'], made)
        self.assertEqual((made['unders'], made['under']), (0, ''), made)
        self.assertFalse(self.got['channelHasAnswer'], 'the answer is in the thread, not the chat')

    def test_a_new_thread_has_its_row_before_any_reply(self):
        w = self.got['waiting']
        self.assertEqual(w['lines'], 1, w)
        self.assertIn('New thread', w['line'], w)
        self.assertIn('Started', w['line'], w)
        self.assertEqual((w['unders'], w['under']), (0, ''), w)

    # 3 -----------------------------------------------------------------
    def test_a_thread_root_has_one_row_its_threads(self):
        th = self.got['thread']
        self.assertEqual(th['lines'], 1, th)
        self.assertEqual((th['unders'], th['under']), (0, ''), th)

    def test_an_answered_balloon_in_the_chat_has_one_state_row_to_its_answer(self):
        for name in ('acked', 'delivered'):
            with self.subTest(name):
                b = self.got[name]
                self.assertEqual((b['lines'], b['unders']), (0, 1), b)
                self.assertIn('done — see the answer ↓', b['under'], b)
                self.assertFalse(b['noAnswer'], b)
                self.assertNotIn('no answer yet', b['text'], b)
                self.assertNotIn('acknowledged', b['text'], b)
        self.assertEqual(self.got['acked']['link'], self.got['answerMid'])
        self.assertTrue(self.got['acked']['reopen'] and all('pt-hover' in c for c in self.got['acked']['reopen']), self.got['acked'])
        self.assertEqual(self.got['delivered']['reopen'], [])
        jump = self.got['jump']
        self.assertTrue(jump['found'] and jump['inView'], jump)

    # 4 -----------------------------------------------------------------
    def test_a_thread_whose_points_are_all_acknowledged_never_needs_you(self):
        th = self.got['thread']
        self.assertEqual(th['lineState'], 'done', th)
        for words in ('waiting for you', 'needs you', 'no answer yet'):
            self.assertNotIn(words, th['line'] + th['text'], th)
        self.assertFalse(th['noAnswer'], th)


if __name__ == '__main__':
    unittest.main()
