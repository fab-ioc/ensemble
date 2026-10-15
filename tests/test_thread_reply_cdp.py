"""A reply sent from a thread stays in its thread unless Also send to chat is ticked (#237).

The PO chat is watched every 50 ms from the moment Send is pressed in the
thread until the PO has read the reply and answered, so a balloon that shows
in the chat and then moves to the thread fails the test, not only one that
stays there. The hub's delivery is stubbed: the reply is typed into the PO's
transcript a second after it is sent, as a running PO would read it.
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
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests.test_thread_panel_cdp import CDP_JS as PANEL_JS, NODE  # noqa: E402

# The panel test's Chrome and CDP helpers, with this test's steps.
CDP_JS = PANEL_JS[:PANEL_JS.index('  const out={};')] + r"""
  const WATCH = doc => `(() => { const w=${doc}.defaultView; w.__seen = w.__seen || {};
    if (!w.__watch) w.__watch = setInterval(() => { const m=w.document.querySelector('#msgs'); if (!m) return;
      for (const t of ['TOKA','TOKB','TOKC']) if (m.textContent.includes(t) && !w.__seen[t]) w.__seen[t] = Date.now(); }, 50);
    return 0; })()`;
  const SEND = (doc, text, also) => `(async () => { const w=${doc}.defaultView;
    const a=w.document.getElementById('thr-also'); if (!a || w.document.getElementById('thr-also-l').hidden) throw Error('no Also box');
    a.checked=${also}; w.document.getElementById('input').value=${JSON.stringify(text)}; await w.sendNow(); return Date.now(); })()`;
  const HAS = (doc, tok) => `!!${doc}?.querySelector('#msgs')?.textContent.includes('${tok}')`;
  const ANSWERED = (doc, tok) => `[...(${doc}?.querySelectorAll('#msgs .msg[data-mid]')||[])].some(m => m.textContent.includes('Answer to ${tok}'))`;
  const out={};
  try {
    await size(1728,1117);
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`, 'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    await until(`!!PD.dock && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat');
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]') && !${THR}.contentDocument.getElementById('thr-also-l').hidden`, 'thread panel');
    await sleep(500);
    const CH = `${PO}.contentDocument`, TD = `${THR}.contentDocument`;
    await ev(WATCH(CH)); await ev(WATCH(TD));
    // Not ticked: in the thread at once, never in the chat.
    const sentA = await ev(SEND(TD, 'TOKA is this ready?', false));
    await until(ANSWERED(TD, 'TOKA'), 'TOKA answered in the thread');
    await sleep(2500);
    out.unticked = await ev(`({sent:${sentA}, chat:${PO}.contentWindow.__seen.TOKA||0, thread:${THR}.contentWindow.__seen.TOKA||0,
      threadMsgs:${HAS(TD, 'TOKA')}})`);
    // Ticked: in the thread and in the chat.
    const sentB = await ev(SEND(TD, 'TOKB also for the chat', true));
    await until(`${ANSWERED(TD, 'TOKB')} && ${HAS(CH, 'TOKB')}`, 'TOKB in both');
    await sleep(1500);
    out.ticked = await ev(`({sent:${sentB}, chat:${PO}.contentWindow.__seen.TOKB||0, thread:${THR}.contentWindow.__seen.TOKB||0,
      chatStill:${HAS(CH, 'TOKB')}})`);
    // A phone: the thread inside the chat (#ts-frame), the same.
    await ev(`PD.dock.removePanel('thread:po'); 0`);
    await size(390,844,true); await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
    await until(`PD.dock.narrow() && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'phone chat');
    await sleep(600);
    await ev(`(() => { const d=${PO}.contentDocument; if (d.getElementById('thr-side').hidden) d.querySelector('#msgs .thr-open[data-thr]').click(); return 0; })()`);
    const TS = `${PO}.contentDocument.getElementById('ts-frame')?.contentDocument`;
    await until(`!!${TS}?.querySelector('#msgs .msg[data-mid]') && !${TS}.getElementById('thr-also-l').hidden`, 'phone thread');
    await sleep(500);
    await ev(WATCH(CH)); await ev(WATCH(TS));
    const sentC = await ev(SEND(TS, 'TOKC from the phone', false));
    await until(ANSWERED(TS, 'TOKC'), 'TOKC answered in the thread');
    await sleep(2500);
    out.phone = await ev(`({sent:${sentC}, chat:${PO}.contentWindow.__seen.TOKC||0,
      thread:${PO}.contentDocument.getElementById('ts-frame').contentWindow.__seen.TOKC||0})`);
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadReply(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-thread-reply-', ignore_cleanup_errors=True)
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
        cls.sid = sid = 'reply-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':sid,'cwd':str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        (base / 'transcripts' / 'C--po').mkdir()
        cls.transcript = base / 'transcripts' / 'C--po' / f'{sid}.jsonl'
        at = time.time() - 3600
        # Sent through the hub: each makes a point (P1, P2), so a reply from
        # its thread is a "Re P1:" follow-up that makes none (the leak).
        room = chatroom.get_room(cls.po, public=False)
        said = [points.take(room, t, '', '', now=at + n)[0] for t, n in
                (('We need the chat to use the whole width of its panel.', 1), ('And the thread opens beside it.', 20))]
        lines = [cls.turn('user', said[0], at + 1),
                 cls.turn('assistant', 'Done: the chat fills its panel now.', at + 2),
                 cls.turn('user', said[1], at + 20),
                 cls.turn('assistant', 'It does: a panel of its own.', at + 21)]
        cls.transcript.write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'proj': cls.proj, 'po': cls.po}
        script = base / 'thread_reply_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=240)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nthread reply CDP: ' + json.dumps(cls.got, indent=1))

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.%03dZ' % int(ts % 1 * 1000)
        msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
        if role == 'assistant':
            msg['stop_reason'] = 'end_turn'
        return {'type': role, 'timestamp': stamp, 'sessionId': cls.sid, 'message': msg}

    @staticmethod
    def _deliver(h, room, text='', to='', key='', quiet=False, **kw):
        """Typed in at once; the PO reads it a second later and answers."""
        cls = ThreadReply
        sends.mark(room['id'], [key], 'delivered')

        def read():
            time.sleep(1.0)
            tok = next((w for w in text.split() if w.startswith('TOK')), '')
            now = time.time()
            with cls.transcript.open('a', encoding='utf-8') as f:
                f.write(json.dumps(cls.turn('user', text, now)) + '\n')
                f.write(json.dumps(cls.turn('assistant', f'{text.split(":")[0]}: Answer to {tok}.', now + .5)) + '\n')
        threading.Thread(target=read, daemon=True).start()
        return {'delivered': 1}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def check(self, name):
        g = self.got[name]
        self.assertTrue(g['thread'], ('the thread shows it', g))
        self.assertLessEqual(g['thread'] - g['sent'], 1500, ('the thread shows it at once', g))
        return g

    def test_an_unticked_reply_never_shows_in_the_chat(self):
        g = self.check('unticked')
        self.assertEqual(g['chat'], 0, g)

    def test_a_ticked_reply_shows_in_both(self):
        g = self.check('ticked')
        self.assertTrue(g['chat'] and g['chatStill'], g)

    def test_a_phone_thread_reply_never_shows_in_the_chat(self):
        g = self.check('phone')
        self.assertEqual(g['chat'], 0, g)


if __name__ == '__main__':
    unittest.main()
