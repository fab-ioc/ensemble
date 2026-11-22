"""An outside sender's line carrying a thread opens that thread in an open PO chat without a reload (#412, P442).

With the PO chat open, a Discord request comes in as the Swann bot would
post it (its thread recorded, its turn journalled with the thread key) and
the PO answers it: within a few seconds the chat shows the line with its
thread's reply line, the PO's answer is in the thread and not in the main
chat, and the thread is named by the request's title and rings.
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
import input_provenance  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_thread_panel_cdp import CDP_JS as PANEL_JS, NODE  # noqa: E402

CDP_JS = PANEL_JS[:PANEL_JS.index('  const out={};')] + r"""
  const fs = require('fs'), path = require('path');
  const IN_CHAT = tok => `[...(${PO}?.contentDocument?.querySelectorAll('#msgs .msg[data-mid]')||[])]
    .filter(m => !m.querySelector('.msg[data-mid]') && (m.querySelector('.body,.text')||m).textContent.includes('${tok}')).length`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`, 'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat');
    await sleep(1500);
    const D = `${PO}.contentDocument`;
    out.before = await ev(`${D}.querySelector('#msgs').textContent.includes('TOKQ')`);
    const go = Date.now();
    fs.writeFileSync(path.join(A.tmp, 'go'), String(go));
    const LINE = `${D}.querySelector('#msgs .thr-line[data-thr-line=${JSON.stringify(A.tid)}]')`;
    await until(`${D}.querySelector('#msgs').textContent.includes('TOKQ') && !!${LINE}`, 'the outside thread in the open chat');
    out.shown = Date.now() - go;
    await sleep(2500);
    out.chatLine = await ev(IN_CHAT('TOKQ'));
    out.chatReply = await ev(IN_CHAT('TOKA'));
    out.title = await ev(`${PO}.contentWindow.eval('THR_META')[${JSON.stringify(A.tid)}]?.title || ''`);
    out.unread = await ev(`${PO}.contentWindow.eval('THR_META')[${JSON.stringify(A.tid)}]?.unread || 0`);
    await ev(`${LINE}.querySelector('.thr-open').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]') && ${THR}.contentDocument.querySelector('#msgs').textContent.includes('TOKA')`, 'its thread open');
    out.threadLine = await ev(`${THR}.contentDocument.querySelector('#msgs').textContent.includes('TOKQ')`);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class OutsideThreadLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-outside-thread-', ignore_cleanup_errors=True)
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
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        cls.sid = sid = 'outside-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        (base / 'transcripts' / 'C--po').mkdir()
        cls.transcript = base / 'transcripts' / 'C--po' / f'{sid}.jsonl'
        at = time.time() - 3600
        room = chatroom.get_room(cls.po, public=False)
        q, ids = points.take(room, 'Make the chart wider.', '', '', now=at + 1)
        threads.note_send(cls.po, '', '', ids, False, q, now=at + 1, new=True)
        lines = [cls.turn('user', q, at + 1),
                 cls.turn('assistant', 'I made a task for it.', at + 2),
                 cls.turn('user', '[digest] all quiet', at + 10),
                 cls.turn('assistant', 'Nothing to do.', at + 11)]
        cls.transcript.write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        threading.Thread(target=cls._line_on_go, args=(base / 'go',), daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'proj': cls.proj, 'po': cls.po, 'tid': f'm:{sid}:4'}
        script = base / 'outside_thread_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\noutside thread CDP: ' + json.dumps(cls.got, indent=1))

    @classmethod
    def _line_on_go(cls, go: Path):
        """The Swann bot's request comes in once the chat is open; the PO answers it."""
        for _ in range(2400):
            if go.exists():
                break
            time.sleep(0.05)
        now = time.time()
        sender = 'Swann bot (Discord)'
        threads.open_outside(cls.po, '1234567890', 'Swann: SPX puts', sender, now)
        line = f'[{sender}] TOKQ SPX 0DTE puts at 10 delta?'
        input_provenance.record(cls.po, 'claude', line, dashboard._input_sender_info(
            line, {'kind': 'external', 'senderLabel': sender, 'outsideThread': '1234567890'}), session_id=cls.sid)
        with cls.transcript.open('a', encoding='utf-8') as f:
            f.write(json.dumps(cls.turn('user', line, now)) + '\n')
            f.write(json.dumps(cls.turn('assistant', 'TOKA Looking into it.', now + .5)) + '\n')

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.%03dZ' % int(ts % 1 * 1000)
        msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
        if role == 'assistant':
            msg['stop_reason'] = 'end_turn'
        return {'type': role, 'timestamp': stamp, 'sessionId': cls.sid, 'message': msg}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_the_open_chat_shows_the_new_thread_without_a_reload(self):
        g = self.got
        self.assertFalse(g['before'], g)
        self.assertLessEqual(g['shown'], 8000, ('shown while the chat is open', g))
        self.assertEqual(g['chatLine'], 1, g)
        self.assertEqual(g['title'], 'Swann: SPX puts', g)
        self.assertGreaterEqual(g['unread'], 1, g)

    def test_the_pos_answer_is_in_the_thread_not_the_main_chat(self):
        g = self.got
        self.assertEqual(g['chatReply'], 0, g)
        self.assertTrue(g['threadLine'], g)

if __name__ == '__main__':
    unittest.main()
