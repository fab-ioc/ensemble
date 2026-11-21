"""A task's report and the PO's note on it show in its thread's open panel without a reload (#411, P441).

A thread the person started (P1); the task works on it, named in its title
("Wider chart (P1)"). With the thread open in place over the PO chat, the
report and the PO's note on it are written to the PO's transcript: the open
thread shows both within a few seconds, and the main chat does not.
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
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread in place');
    await sleep(1500);
    const TD = `${THR}.contentDocument`;
    out.before = await ev(`${TD}.querySelector('#msgs').textContent.includes('TOKR')`);
    const go = Date.now();
    fs.writeFileSync(path.join(A.tmp, 'go'), String(go));
    await until(`${TD}.querySelector('#msgs').textContent.includes('TOKR') && ${TD}.querySelector('#msgs').textContent.includes('TOKN')`, 'report in the open thread');
    out.shown = Date.now() - go;
    await sleep(2500);
    out.chatReport = await ev(IN_CHAT('TOKR'));
    out.chatNote = await ev(IN_CHAT('TOKN'));
    out.threadStill = await ev(`${TD}.querySelector('#msgs').textContent.includes('TOKR')`);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadWorkLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-thread-work-', ignore_cleanup_errors=True)
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
        cls.sid = sid = 'work-po'
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
        threading.Thread(target=cls._report_on_go, args=(base / 'go',), daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'proj': cls.proj, 'po': cls.po}
        script = base / 'thread_work_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nthread work CDP: ' + json.dumps(cls.got, indent=1))

    @classmethod
    def _report_on_go(cls, go: Path):
        """The task reports once the thread is open; the PO notes it."""
        for _ in range(2400):
            if go.exists():
                break
            time.sleep(0.05)
        now = time.time()
        with cls.transcript.open('a', encoding='utf-8') as f:
            f.write(json.dumps(cls.turn('user', "[report] completed from task 'Wider chart (P1)' (#12, claude): "
                                                "TOKR the chart is wider", now)) + '\n')
            f.write(json.dumps(cls.turn('assistant', 'TOKN Merged; it is live.', now + .5)) + '\n')

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

    def test_the_open_thread_shows_the_report_and_the_po_note_without_a_reload(self):
        g = self.got
        self.assertFalse(g['before'], g)
        self.assertLessEqual(g['shown'], 8000, ('shown while the thread is open', g))
        self.assertTrue(g['threadStill'], g)

    def test_the_main_chat_does_not_show_them(self):
        g = self.got
        self.assertEqual((g['chatReport'], g['chatNote']), (0, 0), g)


if __name__ == '__main__':
    unittest.main()
