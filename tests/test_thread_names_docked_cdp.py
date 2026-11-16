"""Naming a thread as it starts, in the docked dashboard's shared box (issue 51).

In the docked dashboard (body.po-dock) the message box is shared: the To line
is the dashboard's (#workspace-compose .wc-to), the box a session.html frame
(#workspace-compose-frame, ?workspace-compose) whose own To line is hidden.
The frame's thread icon (#new-thread) still starts a thread, and the name
field lives on the dashboard's To line (#workspace-compose-thread-name):

1. icon off: the field hidden, To: the PO;
2. icon on: To: "a new thread", the field shown, empty, "Name (optional)",
   on the To line's row;
3. icon off forgets what was typed; on again it is still empty;
4. Enter in the field goes on to the box's message (nothing sent);
5. Ctrl+Enter in the field sends: one /api/room/resume with newThread and the
   trimmed threadName; then the icon is off, the field hidden and empty, To:
   the PO again;
6. the hub keeps the name with the send, and once the PO answers the thread's
   title is that name;
7. no script errors in the dashboard or the box;
8. the box's page reloaded: the field is hidden and empty again.

Real mouse clicks and keys (CDP Input). A light-theme picture of the To line
and box, the icon on and the field holding a name, is written to
$THREAD_NAMES_DOCKED_SHOT (by default the issue's Documents folder, when it
exists).
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
from tests.test_comment_tray_once_cdp import CDP_JS as ONCE_JS, NODE  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NAME = 'Colour palette'
TYPED_NOT_KEPT = 'Typed then dropped'
SHOT = os.environ.get('THREAD_NAMES_DOCKED_SHOT') or str(
    Path.home() / 'EnsembleProjects' / 'Ensemble Dashboard' / 'Documents' / 'Issue 51 Thread names' / 'docked.png')

# The comment tray test's Chrome and CDP helpers (the docked dashboard at
# 1440x900, real presses through the frames), with this test's steps.
CDP_JS = ONCE_JS[:ONCE_JS.index('  const PO = ')] + r"""
  const J = JSON.stringify;
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const WCF = `document.getElementById('workspace-compose-frame')`;
  const FD = `${WCF}.contentDocument`;
  const SENT = `fetch('/test/sent').then(x => x.json())`;
  const boxReady = `!document.getElementById('workspace-compose').hidden && ${FD}?.getElementById('send')?.disabled === false`;
  const CODES = { Enter: 13 };
  const key = async (k, modifiers = 0) => {
    await send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code: k, windowsVirtualKeyCode: CODES[k], modifiers }, s);
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code: k, windowsVirtualKeyCode: CODES[k], modifiers }, s);
    await sleep(300);
  };
  const type = async text => { await send('Input.insertText', { text }, s); await sleep(200); };
  // The dashboard's To line, its name field and the box's thread icon.
  const FIELD = `(() => { const n = document.getElementById('workspace-compose-thread-name'), l = document.getElementById('workspace-compose-link');
    const t = ${FD}?.getElementById('new-thread'), nr = n.getBoundingClientRect(), lr = l.getBoundingClientRect();
    return { hidden: n.hidden, shown: n.getClientRects().length > 0 && nr.width > 0, value: n.value, placeholder: n.placeholder,
      link: l.textContent, title: WC.target ? wcTitle(WC.target) : '', target: WC.target?.kind || '', pressed: t?.getAttribute('aria-pressed') || '',
      sameRow: n.parentElement === l.parentElement && n.parentElement.classList.contains('wc-to'),
      overlap: Math.min(nr.bottom, lr.bottom) - Math.max(nr.top, lr.top), fieldTop: nr.top, linkTop: lr.top,
      ownTo: (() => { const o = ${FD}?.getElementById('compose-destination'); return !!o && o.getClientRects().length > 0 && !o.closest('[hidden]'); })(),
      active: document.activeElement?.id || '', frameActive: ${FD}?.activeElement?.id || '' }; })()`;
  const ICON = `${FD}.getElementById('new-thread')`;
  const NAMEF = `document.getElementById('workspace-compose-thread-name')`;
  const INPUT = `${FD}.getElementById('input')`;
  const iconTo = async on => { const p = await pressOn(ICON, 'thread icon'); await until(`${ICON}.getAttribute('aria-pressed') === ${J(on ? 'true' : 'false')}`, 'icon ' + on, 5000).catch(() => null); return p.hits; };
  const out = {};
  const step = async (k, fn) => { try { out[k] = await fn(); } catch (e) { out[k] = { error: String(e && e.message || e).slice(0, 800) }; } };
  try {
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false }, s);
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] }, s);
    await send('Emulation.setFocusEmulationEnabled', { enabled: true }, s).catch(() => {});
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('cd-theme', 'light'); } catch (e) {}` }, s);
    await send('Page.navigate', { url: A.base + '/' }, s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r => r.roomId === ${J(A.po)})`, 'rows', 40000);
    await ev(`SELECTED_PROJECT = ${J(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); 0`);
    await until(`document.body.classList.contains('po-dock') && !!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat');
    await until(`WC.target?.kind === 'po' && ${boxReady} && ${FD}.defaultView.eval('THREADED') === true`, 'box on the PO');
    await sleep(1000);
    out.theme = await ev(`({ top: document.documentElement.getAttribute('data-theme') || '', frame: ${FD}.documentElement.getAttribute('data-theme') || '',
      dark: matchMedia('(prefers-color-scheme: dark)').matches })`);

    // 1. Icon off.
    await step('off', () => ev(FIELD));
    // 2. Icon on.
    await step('on', async () => { const hit = await iconTo(true); return { hit, ...(await ev(FIELD)) }; });
    // 3. Typed, icon off, on again.
    await step('forget', async () => {
      const r = {};
      r.fieldHit = (await pressOn(NAMEF, 'name field')).hits;
      await type(A.typedNotKept);
      r.typed = await ev(FIELD);
      r.offHit = await iconTo(false);
      r.off = await ev(FIELD);
      r.onHit = await iconTo(true);
      r.on = await ev(FIELD);
      return r;
    });
    // 4. Enter in the field: on to the box's message, nothing sent. Then the
    // picture, the field holding the name.
    await step('enter', async () => {
      const r = {};
      r.sent0 = (await ev(SENT)).length;
      r.msgs0 = await ev(`${PO}.contentDocument.querySelectorAll('#msgs .msg[data-mid]').length`);
      r.fieldHit = (await pressOn(NAMEF, 'name field')).hits;
      await type(A.name);
      r.typed = await ev(FIELD);
      await key('Enter');
      await sleep(1200);
      r.after = await ev(FIELD);
      r.sent = (await ev(SENT)).length;
      r.msgs = await ev(`${PO}.contentDocument.querySelectorAll('#msgs .msg[data-mid]').length`);
      if (A.shot) {
        await ev(`(document.activeElement?.blur?.(), ${FD}.activeElement?.blur?.(), 0)`);
        await sleep(400);
        const b = await ev(`(() => { const r = document.getElementById('workspace-compose').getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height }; })()`);
        r.shotState = await ev(FIELD);
        const shot = await send('Page.captureScreenshot', { format: 'png', clip: { ...b, scale: 1 }, captureBeyondViewport: false }, s);
        fs.mkdirSync(path.dirname(A.shot), { recursive: true });
        fs.writeFileSync(A.shot, Buffer.from(shot.data, 'base64'));
        r.shot = { file: A.shot, clip: b };
      }
      return r;
    });
    // 5. A message, the icon on, a name with spaces around it, Ctrl+Enter in the field.
    await step('send', async () => {
      const r = {};
      r.offHit = await iconTo(false);
      r.inputHit = (await pressOn(INPUT, 'box input')).hits;
      await ev(`(${INPUT}.value = '', ${INPUT}.dispatchEvent(new Event('input', { bubbles: true })), 0)`);
      await type('TOKN a thread about colours');
      await until(`${FD}.getElementById('send').disabled === false`, 'send ready', 5000);
      r.onHit = await iconTo(true);
      r.fieldHit = (await pressOn(NAMEF, 'name field')).hits;
      await type('  ' + A.name + '  ');
      r.before = await ev(FIELD);
      r.sent0 = (await ev(SENT)).length;
      await key('Enter', 2);     // Ctrl
      await until(`${SENT}.then(l => l.length > ${r.sent0})`, 'sent', 10000).catch(() => null);
      await sleep(2500);     // a second send would be in by now
      r.sent = (await ev(SENT)).slice(r.sent0).map(b => ({ room: b.roomId, thread: b.thread || '', text: b.text || '',
        newThread: b.newThread, threadName: b.threadName === undefined ? null : b.threadName }));
      await until(`${ICON}.getAttribute('aria-pressed') === 'false'`, 'icon off after the send', 5000).catch(() => null);
      r.after = await ev(FIELD);
      r.input = await ev(`${INPUT}.value`);
      return r;
    });
    // 6. The PO answers: the thread's line in the PO chat has the name.
    await step('made', async () => {
      const LINE = `(() => { const d = ${PO}.contentDocument; const b = [...d.querySelectorAll('#msgs .msg[data-mid]')].find(m => m.textContent.includes('TOKN'));
        const l = b && b.nextElementSibling && b.nextElementSibling.matches('.thr-line') ? b.nextElementSibling : null;
        return l ? { tid: l.dataset.thrLine, label: l.querySelector('.thr-open')?.getAttribute('aria-label') || '', text: l.textContent.replace(/\\s+/g, ' ').trim() } : null; })()`;
      await until(`(() => { const l = ${LINE}; return !!l && l.label.includes(${J(A.name)}) && /1 repl/.test(l.text); })()`, 'named thread line', 30000).catch(() => null);
      return { line: await ev(LINE) };
    });
    // 8. The box's page reloaded with a name typed: the field hidden and empty again.
    await step('reload', async () => {
      const r = {};
      r.onHit = await iconTo(true);
      r.fieldHit = (await pressOn(NAMEF, 'name field')).hits;
      await type('Lost on reload');
      r.before = await ev(FIELD);
      await ev(`(${WCF}.contentWindow.__old = 1, ${WCF}.contentWindow.location.reload(), 0)`);
      await until(`!${WCF}.contentWindow.__old && ${FD}.readyState === 'complete' && ${boxReady}`, 'box reloaded', 30000);
      await sleep(1000);
      r.after = await ev(FIELD);
      return r;
    });
    out.errors = errs.slice();
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadNamesDocked(unittest.TestCase):
    """A hub with one project whose PO chat has two threads the person
    started. /api/room/resume runs as the hub's and records what it was given
    (/test/sent); the PO reads a send a second later and answers its point."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix='ens-thread-names-docked-', ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        sent = cls.sent = []
        real_room_resume = dashboard.Handler._room_resume

        def room_resume(h, data):
            sent.append(dict(data))
            return real_room_resume(h, data)
        for patch in [
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
            mock.patch.object(dashboard, '_send_left_behind', lambda *a, **k: ''),
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, '_room_resume', room_resume),
            mock.patch.object(dashboard.Handler, '_resume_room', cls._deliver),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        ok, proj, _ = dashboard.register_project('Motors')
        assert ok, proj
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        cls.sid = sid = 'thread-names-docked-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner',
                                               'sessionId': sid, 'cwd': str(home)}])
        cls.po = po['id']
        room = chatroom.get_room(cls.po, public=False)
        room['mode'] = 'solo'
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.po, cls.proj)
        ok, why = dashboard.set_project_po(cls.proj, cls.po)
        assert ok, why
        assert threads.is_threaded(cls.po)
        (base / 'transcripts' / 'C--po').mkdir()
        cls.transcript = base / 'transcripts' / 'C--po' / f'{sid}.jsonl'
        cls.lock = threading.Lock()
        at = time.time() - 3600
        room = chatroom.get_room(cls.po, public=False)
        # Two threads the person started (the box's icon), each answered.
        said = [points.take(room, t, '', k, now=at + n) for t, k, n in
                (('TOKT start a thread about the export', 'send:t', 1), ('TOKU a thread about the upload page', 'send:u', 10))]
        (tt, (pt,)), (tu, (pu,)) = said
        threads.note_send(cls.po, '', 'send:t', [pt], False, tt, now=at + 1, new=True)
        threads.note_send(cls.po, '', 'send:u', [pu], False, tu, now=at + 10, new=True)
        cls.write([cls.turn('user', tt, at + 1),
                   cls.turn('assistant', f'Re {pt}: The export takes a day.', at + 2),
                   cls.turn('user', tu, at + 10),
                   cls.turn('assistant', f'Re {pu}: The upload page is next.', at + 11)])
        points.sync(cls.po, force=True)

        class Handler(dashboard.Handler):
            def do_GET(self):
                if self.path == '/test/sent':
                    return self._send_json(200, sent)
                super().do_GET()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': f'http://127.0.0.1:{server.server_address[1]}',
                'shots': '', 'proj': cls.proj, 'po': cls.po, 'name': NAME, 'typedNotKept': TYPED_NOT_KEPT,
                'shot': SHOT if Path(SHOT).parent.parent.is_dir() else ''}
        run = subprocess.run([NODE, '-e', CDP_JS, json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        cls.side = threads.load_side(cls.po)
        cls.index = threads.build(cls.po) or {}
        dump = os.environ.get('THREAD_NAMES_DOCKED_DUMP', '')
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1, ensure_ascii=False), encoding='utf-8')

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.%03dZ' % int(ts % 1 * 1000)
        msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
        if role == 'assistant':
            msg['stop_reason'] = 'end_turn'
        return {'type': role, 'timestamp': stamp, 'sessionId': cls.sid, 'message': msg}

    @classmethod
    def write(cls, turns):
        with cls.lock, cls.transcript.open('a', encoding='utf-8') as f:
            f.write(''.join(json.dumps(x) + '\n' for x in turns))

    @staticmethod
    def _deliver(h, room, text='', to='', key='', quiet=False, **kw):
        """Typed in at once; the PO reads it a second later and answers its point."""
        cls = ThreadNamesDocked
        sends.mark(room['id'], [key], 'delivered')
        pid = next((p['id'] for p in points.load(room['id']).get('points') or [] if p.get('key') == key), '')

        def read():
            time.sleep(1.0)
            now = time.time()
            cls.write([cls.turn('user', text, now), cls.turn('assistant', f'Re {pid}: Noted, the colours.', now + .5)])
        threading.Thread(target=read, daemon=True).start()
        return {'delivered': 1}

    def part(self, k):
        r = self.got[k]
        self.assertNotIn('error', r, r)
        return r

    def assertOff(self, f):
        self.assertEqual((f['hidden'], f['shown'], f['value'], f['pressed']), (True, False, '', 'false'), f)
        self.assertEqual(f['link'], f['title'], ('To: the PO', f))
        self.assertEqual(f['target'], 'po', f)

    def test_1_icon_off_no_field_to_the_po(self):
        f = self.part('off')
        self.assertOff(f)
        self.assertIn('PO', f['link'], f)
        self.assertFalse(f['ownTo'], ("the box's own To line stays hidden", f))
        self.assertEqual(self.got['theme']['dark'], False, self.got['theme'])

    def test_2_icon_on_shows_the_field_on_the_to_line(self):
        f = self.part('on')
        self.assertTrue(f['hit'], 'the thread icon is pressed where it shows')
        self.assertEqual((f['pressed'], f['link'], f['hidden'], f['shown'], f['value']),
                         ('true', 'a new thread', False, True, ''), f)
        self.assertEqual(f['placeholder'], 'Name (optional)', f)
        self.assertTrue(f['sameRow'], ('in the To line, beside the link', f))
        self.assertGreater(f['overlap'], 4, ('on the link\'s row', f))
        self.assertFalse(f['ownTo'], f)

    def test_3_icon_off_forgets_the_typed_name(self):
        r = self.part('forget')
        self.assertTrue(r['fieldHit'] and r['offHit'] and r['onHit'], r)
        self.assertEqual(r['typed']['value'], TYPED_NOT_KEPT, r['typed'])
        self.assertEqual(r['typed']['active'], 'workspace-compose-thread-name', r['typed'])
        self.assertOff(r['off'])
        self.assertEqual((r['on']['pressed'], r['on']['hidden'], r['on']['value'], r['on']['link']),
                         ('true', False, '', 'a new thread'), r['on'])

    def test_4_enter_in_the_field_goes_on_to_the_message(self):
        r = self.part('enter')
        self.assertTrue(r['fieldHit'], r)
        self.assertEqual(r['typed']['value'], NAME, r['typed'])
        a = r['after']
        self.assertEqual((a['active'], a['frameActive']), ('workspace-compose-frame', 'input'), a)
        self.assertEqual((a['pressed'], a['hidden'], a['value']), ('true', False, NAME), a)
        self.assertEqual(r['sent'], r['sent0'], ('nothing sent', r))
        self.assertEqual(r['msgs'], r['msgs0'], r)

    def test_5_ctrl_enter_in_the_field_sends_the_trimmed_name(self):
        r = self.part('send')
        self.assertTrue(r['offHit'] and r['inputHit'] and r['onHit'] and r['fieldHit'], r)
        self.assertEqual(r['before']['value'], f'  {NAME}  ', r['before'])
        self.assertEqual(r['before']['active'], 'workspace-compose-thread-name', r['before'])
        self.assertEqual(len(r['sent']), 1, ('exactly one send', r['sent']))
        b = r['sent'][0]
        self.assertEqual((b['room'], b['thread'], b['newThread'], b['threadName']), (self.po, '', True, NAME), b)
        self.assertIn('TOKN a thread about colours', b['text'], b)
        self.assertOff(r['after'])
        self.assertEqual(r['input'], '', ('the box is emptied once sent', r))

    def test_6_the_hub_keeps_the_name_and_it_is_the_threads_title(self):
        rows = [a for a in self.side.get('sent') or [] if a.get('new') and 'TOKN' in ' '.join(a.get('words') or [])]
        if not rows:    # older side records: the newest send
            rows = [self.side['sent'][-1]]
        self.assertEqual(rows[-1].get('name'), NAME, rows)
        self.assertFalse([a for a in self.side.get('sent') or [] if a.get('name') in (TYPED_NOT_KEPT, 'Lost on reload')])
        line = self.part('made')['line']
        self.assertIsNotNone(line, 'the TOKN thread line in the PO chat')
        self.assertIn(NAME, line['label'], line)
        self.assertIn('1 repl', line['text'], line)
        th = (self.index.get('threads') or {}).get(line['tid']) or {}
        self.assertEqual(th.get('title'), NAME, th)
        self.assertTrue(th.get('named'), th)

    def test_7_no_script_errors(self):
        self.assertEqual(self.got['errors'], [])

    def test_8_a_reload_of_the_box_resets_the_field(self):
        r = self.part('reload')
        self.assertTrue(r['onHit'] and r['fieldHit'], r)
        self.assertEqual((r['before']['hidden'], r['before']['value']), (False, 'Lost on reload'), r['before'])
        self.assertOff(r['after'])


if __name__ == '__main__':
    unittest.main()
