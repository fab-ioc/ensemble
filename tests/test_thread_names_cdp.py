"""Naming a thread and renaming it later (issue 51), in a real Chrome.

* At creation: the box's thread icon (#new-thread) shows an optional name
  field on the To line (#compose-thread-name); turned off, the field hides and
  forgets what was typed. Sent, the hub's ``sent`` row carries the name, and
  once the PO answers the new thread's title is that name (its row in the
  chat, the side panel's header, the To line).
* From the header of the thread beside the chat: its pencil (#ts-pen) or its
  title opens a field in the title's place; Enter saves (kept in the chat's
  thread record, ``names``), and the To line follows.
* From the Threads list: a row's pencil (.tl-pen) opens a field in the row;
  Enter saves, clicking away saves.
* Esc cancels (header and list): the title as it was, nothing sent or kept.
* An empty name is the automatic title (its first words) again, also over a
  name given at creation.
* From a thread's own window (?thread=...): its pencil (#title-pen).
* No message is rewritten: the transcript on disk is what was written to it.

Real mouse clicks and keys (CDP Input), so focus, hit testing and Enter/Esc
are what a person gets.
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

NAME_NEW = 'Colour palette'
NAME_HEAD = 'Export plan'
NAME_LIST = 'Upload page'
NAME_BLUR = 'Upload flow'
NAME_OWN = 'Upload screen'
TYPED_NOT_KEPT = ('Typed then dropped', 'Something else', 'Nope')

# The panel test's Chrome and CDP helpers, with this test's steps, on the PO
# chat's own page.
CDP_JS = PANEL_JS[:PANEL_JS.index('  const PO = ')] + r"""
  const fs = require('fs');
  const out = {};
  const J = JSON.stringify;
  const {NAME_NEW, NAME_HEAD, NAME_LIST, NAME_BLUR, NAME_OWN} = A.names;
  // The chat's thread record as the hub keeps it, read from disk.
  const side = () => { try { return JSON.parse(fs.readFileSync(A.side, 'utf8')); } catch (e) { return {}; } };
  const names = () => side().names || {};
  // True once expression holds (false after the wait, for the asserts to say what was there).
  const holds = async (expression, tries = 80) => { for (let i = 0; i < tries; i++) { try { if (await ev(expression)) return true; } catch (e) {} await sleep(150); } return false; };
  const fileHolds = async (fn, tries = 80) => { for (let i = 0; i < tries; i++) { if (fn()) return true; await sleep(150); } return false; };
  // A real click at the middle of what expr finds, in the page or in the
  // thread frame beside it (frame: its offset added); hit: what is at that
  // point is the element (nothing covers it).
  const click = async (expr, what, inFrame = false) => {
    const r = await ev(`(() => { const e=${expr}; if (!e) return null; e.scrollIntoView({block:'nearest'}); const r=e.getBoundingClientRect();
      const f=${inFrame ? "document.getElementById('ts-frame')" : 'null'}, o=f ? f.getBoundingClientRect() : {x:0, y:0};
      const doc=e.ownerDocument, at=doc.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
      return {x:o.x+r.x+r.width/2, y:o.y+r.y+r.height/2, w:r.width, h:r.height, hit:!!at && (at===e || e.contains(at))}; })()`);
    if (!r || !r.w || !r.h) throw Error('not shown: ' + what + ' ' + J(r));
    await send('Input.dispatchMouseEvent', {type:'mouseMoved', x:r.x, y:r.y}, s);
    await send('Input.dispatchMouseEvent', {type:'mousePressed', x:r.x, y:r.y, button:'left', clickCount:1}, s);
    await send('Input.dispatchMouseEvent', {type:'mouseReleased', x:r.x, y:r.y, button:'left', clickCount:1}, s);
    return r.hit;
  };
  const CODES = {Enter:13, Escape:27, Backspace:8};
  const key = async k => {
    await send('Input.dispatchKeyEvent', {type:'rawKeyDown', key:k, code:k, windowsVirtualKeyCode:CODES[k]}, s);
    await send('Input.dispatchKeyEvent', {type:'keyUp', key:k, code:k, windowsVirtualKeyCode:CODES[k]}, s);
  };
  const type = text => send('Input.insertText', {text}, s);
  // What this page sent to name a thread.
  const SPY = `(() => { if (window.__named) return 0; window.__named=[]; const f0=window.fetch;
    window.fetch=function(u, o) { if (String(u).includes('/api/room/thread/name')) window.__named.push(JSON.parse(o && o.body || '{}')); return f0.apply(this, arguments); }; return 0; })()`;
  const NAMED = `(window.__named || []).length`;
  const BAL = tok => `[...document.querySelectorAll('#msgs .msg[data-mid]')].find(m => m.textContent.includes(${J(tok)}))`;
  // The thread row under the balloon holding tok.
  const LINE = tok => `(() => { const b=${BAL(tok)}; const l=b && b.nextElementSibling && b.nextElementSibling.matches('.thr-line') ? b.nextElementSibling : null;
    return l ? {tid:l.dataset.thrLine, label:l.querySelector('.thr-open')?.getAttribute('aria-label')||'', text:l.textContent.replace(/\\s+/g,' ').trim()} : null; })()`;
  const FIELD = `(() => { const t=document.getElementById('new-thread'), n=document.getElementById('compose-thread-name');
    return {pressed:t?.getAttribute('aria-pressed')||'', exists:!!n, hidden:!n || n.hidden, shown:!!n?.offsetParent, value:n ? n.value : null,
      placeholder:n?.placeholder||'', to:document.getElementById('compose-destination-link')?.textContent||''}; })()`;
  // The header of the thread beside the chat.
  const HEAD = `(() => { const h=document.querySelector('#thr-side .ts-head'), l=document.getElementById('ts-title'), p=document.getElementById('ts-pen');
    const i=h?.querySelector('input.thr-name.head'), fd=document.getElementById('ts-frame')?.contentDocument;
    return {open:!document.getElementById('thr-side').hidden, title:l?.textContent||'', labelHidden:!!l?.hidden, penHidden:!!p?.hidden, penShown:!!p?.offsetParent, penLabel:p?.getAttribute('aria-label')||'',
      fields:h ? h.querySelectorAll('.thr-name').length : 0, field:!!i, value:i ? i.value : null, placeholder:i?.placeholder||'',
      focused:!!i && document.activeElement===i, selected:i ? i.selectionEnd - i.selectionStart : null, afterLabel:!!i && l.nextElementSibling===i,
      frameTo:fd?.getElementById('compose-destination-link')?.textContent||'', mainTo:document.getElementById('compose-destination-link')?.textContent||'',
      chip:document.querySelector('#compose-target:not([hidden]) span')?.textContent||''}; })()`;
  // A row of the Threads list.
  const ROW = tid => `(() => { const r=[...document.querySelectorAll('#msgs .tl-row')].find(r => r.querySelector('.tl-open')?.dataset.thr===${J(tid)}); if (!r) return null;
    const o=r.querySelector('.tl-open'), p=r.querySelector('.tl-pen'), i=r.querySelector('input.thr-name.row');
    return {title:r.querySelector('.tl-title')?.textContent||'', label:o.getAttribute('aria-label')||'', openHidden:o.hidden,
      pen:!!p, penHidden:p ? p.hidden : null, penShown:!!p?.offsetParent, penLabel:p?.getAttribute('aria-label')||'',
      field:!!i, value:i ? i.value : null, focused:!!i && document.activeElement===i, selected:i ? i.selectionEnd - i.selectionStart : null,
      keys:r.querySelector('.tl-keys')?.textContent||'', fields:r.querySelectorAll('.thr-name').length}; })()`;
  const PEN = tid => `[...document.querySelectorAll('#msgs .tl-pen')].find(b => b.dataset.thrRename===${J(tid)})`;
  const OPEN = async (tid, what) => {
    await ev(`(() => { const b=document.querySelector('#msgs .thr-line[data-thr-line=${J(tid)}] .thr-open'); b.click(); return 0; })()`);
    await until(`(() => { const d=document.getElementById('ts-frame')?.contentDocument; return !!d?.querySelector('#msgs .msg[data-mid]')
      && d.defaultView.eval("THR_INFO?.id||''")===${J(tid)}; })()`, what);
  };
  try {
    await size(1500, 1000);
    await send('Emulation.setFocusEmulationEnabled', {enabled:true}, s).catch(() => {});
    await send('Page.navigate', {url:A.base + '/session?id=' + encodeURIComponent(A.po)}, s);
    await until(`typeof THREADED !== 'undefined' && THREADED && !!(${BAL('TOKT')}) && !!(${BAL('TOKU')})`, 'PO chat');
    await until(`!!(${LINE('TOKT')}) && !!(${LINE('TOKU')})`, 'thread rows');
    await ev(SPY);
    out.tidT = (await ev(LINE('TOKT'))).tid;
    out.tidU = (await ev(LINE('TOKU'))).tid;

    // 1: named as it starts. The field: hidden, shown with the icon, hidden
    // and emptied when the icon is turned off.
    out.field0 = await ev(FIELD);
    await click(`document.getElementById('new-thread')`, 'thread icon');
    out.field1 = await ev(FIELD);
    await click(`document.getElementById('compose-thread-name')`, 'name field');
    await type('Typed then dropped');
    out.field1typed = await ev(FIELD);
    await click(`document.getElementById('new-thread')`, 'thread icon off');
    out.field2 = await ev(FIELD);
    await click(`document.getElementById('new-thread')`, 'thread icon on again');
    out.field3 = await ev(FIELD);
    await click(`document.getElementById('compose-thread-name')`, 'name field again');
    await type(NAME_NEW);
    // Enter in the name field goes on to the message, it does not send.
    out.sentBeforeEnter = await ev(`document.querySelectorAll('#msgs .msg[data-mid]').length`);
    await key('Enter');
    await sleep(300);
    out.nameEnter = {active:await ev(`document.activeElement?.id || ''`), field:await ev(FIELD),
                     msgs:await ev(`document.querySelectorAll('#msgs .msg[data-mid]').length`)};
    await ev(`document.getElementById('input').value='TOKN a thread about colours'; document.getElementById('input').dispatchEvent(new Event('input',{bubbles:true})); 0`);
    await until(`document.getElementById('send').disabled===false`, 'send ready');
    out.field4 = await ev(FIELD);
    await ev(`sendNow().then(() => 0)`);
    await holds(`document.getElementById('new-thread').getAttribute('aria-pressed')==='false'`, 40);
    out.field5 = await ev(FIELD);
    await until(`!!(${LINE('TOKN')})`, 'TOKN thread row');
    out.tidN = (await ev(LINE('TOKN'))).tid;
    // The PO answers: the thread is built with its name.
    await holds(`(() => { const l=${LINE('TOKN')}; return !!l && l.label.includes(${J(NAME_NEW)}) && /1 repl/.test(l.text); })()`, 120);
    out.madeLine = await ev(LINE('TOKN'));
    await OPEN(out.tidN, 'TOKN opened');
    await holds(`(() => { const h=${HEAD}; return h.title===${J(NAME_NEW)} && h.frameTo===${J(NAME_NEW)}; })()`);
    out.madeHead = await ev(HEAD);

    // 2: renamed from the header of the thread beside the chat.
    await OPEN(out.tidT, 'TOKT opened');
    await holds(`document.getElementById('ts-title').textContent.startsWith('TOKT')`);
    out.head0 = await ev(HEAD);
    out.headPenHit = await click(`document.getElementById('ts-pen')`, 'header pencil');
    await holds(`!!document.querySelector('#thr-side .ts-head input.thr-name.head')`, 20);
    out.headOpen = await ev(HEAD);
    await type(NAME_HEAD);
    out.headTyped = await ev(HEAD);
    await key('Enter');
    await holds(`(() => { const h=${HEAD}; return !h.field && h.title===${J(NAME_HEAD)}; })()`);
    out.headSavedFile = await fileHolds(() => names()[out.tidT] === NAME_HEAD);
    out.headSaved = await ev(HEAD);
    // The To line follows (the frame's own box, or the chat's box aimed at it).
    await holds(`(() => { const h=${HEAD}; return h.frameTo===${J(NAME_HEAD)} && (h.mainTo===${J(NAME_HEAD)} || h.mainTo===${J(out.head0.mainTo)}); })()`);
    out.headTo = await ev(HEAD);
    await holds(`(${LINE('TOKT')})?.label.includes(${J(NAME_HEAD)})`);
    out.headLine = await ev(LINE('TOKT'));
    // A poll later it is still the name (the next build has it too).
    await sleep(2500);
    out.headLater = await ev(HEAD);

    // 4 (header): Esc cancels. Opened by clicking the title this time.
    out.namedBeforeEsc = await ev(NAMED);
    out.namesBeforeEsc = names();
    await click(`document.getElementById('ts-title')`, 'header title');
    await holds(`!!document.querySelector('#thr-side .ts-head input.thr-name.head')`, 20);
    out.headEscOpen = await ev(HEAD);
    await type('Something else');
    await key('Escape');
    await sleep(600);
    out.headEsc = await ev(HEAD);
    out.headEscNamed = await ev(NAMED);
    out.headEscNames = names();

    // 5 (header): empty is the automatic title again.
    await click(`document.getElementById('ts-pen')`, 'header pencil again');
    await holds(`!!document.querySelector('#thr-side .ts-head input.thr-name.head')`, 20);
    await key('Backspace');
    out.headEmptyTyped = await ev(HEAD);
    await key('Enter');
    await holds(`(() => { const h=${HEAD}; return !h.field && h.title===${J(out.head0.title)}; })()`);
    out.headEmptyFile = await fileHolds(() => names()[out.tidT] === '');
    out.headEmpty = await ev(HEAD);
    await holds(`(() => { const h=${HEAD}; return h.frameTo===${J(out.head0.title)}; })()`);
    out.headEmptyTo = await ev(HEAD);

    // 3: renamed from the Threads list.
    await click(`document.querySelector('#chat-filters [data-filter="threads"]')`, 'Threads filter');
    await until(`!!(${ROW(out.tidU)}) && !!(${ROW(out.tidN)})`, 'Threads list');
    out.list0 = await ev(ROW(out.tidU));
    out.listN0 = await ev(ROW(out.tidN));
    out.listPenHit = await click(PEN(out.tidU), 'list pencil');
    await holds(`!!(${ROW(out.tidU)})?.field`, 20);
    out.listOpen = await ev(ROW(out.tidU));
    out.listOpenThreadSide = await ev(`(() => { try { return document.getElementById('ts-frame').contentWindow.eval("THR_INFO?.id||''"); } catch (e) { return ''; } })()`);
    await type(NAME_LIST);
    // A poll while the field is open keeps it (and what is typed in it).
    await ev(`refresh && refresh(); 0`).catch(() => {});
    await sleep(1500);
    out.listTypedAfterPoll = await ev(ROW(out.tidU));
    await key('Enter');
    await holds(`(() => { const r=${ROW(out.tidU)}; return !!r && !r.field && r.title===${J(NAME_LIST)}; })()`);
    out.listSavedFile = await fileHolds(() => names()[out.tidU] === NAME_LIST);
    out.listSaved = await ev(ROW(out.tidU));

    // 4 (list): Esc cancels.
    out.listNamedBeforeEsc = await ev(NAMED);
    out.listNamesBeforeEsc = names();
    await click(PEN(out.tidU), 'list pencil again');
    await holds(`!!(${ROW(out.tidU)})?.field`, 20);
    await type('Nope');
    out.listEscTyped = await ev(ROW(out.tidU));
    await key('Escape');
    await sleep(600);
    out.listEsc = await ev(ROW(out.tidU));
    out.listEscNamed = await ev(NAMED);
    out.listEscNames = names();

    // Clicking away saves.
    await click(PEN(out.tidU), 'list pencil for blur');
    await holds(`!!(${ROW(out.tidU)})?.field`, 20);
    await type(NAME_BLUR);
    await ev(`document.querySelector('#msgs .tl-row input.thr-name.row').blur(); 0`);
    await holds(`(() => { const r=${ROW(out.tidU)}; return !!r && !r.field && r.title===${J(NAME_BLUR)}; })()`);
    out.listBlurFile = await fileHolds(() => names()[out.tidU] === NAME_BLUR);
    out.listBlur = await ev(ROW(out.tidU));

    // 5 (list): empty over a name given at creation: its first words again.
    await click(PEN(out.tidN), 'list pencil TOKN');
    await holds(`!!(${ROW(out.tidN)})?.field`, 20);
    out.listNOpen = await ev(ROW(out.tidN));
    await key('Backspace');
    await key('Enter');
    await holds(`(() => { const r=${ROW(out.tidN)}; return !!r && !r.field && r.title.startsWith('TOKN'); })()`);
    out.listNEmptyFile = await fileHolds(() => names()[out.tidN] === '');
    out.listNEmpty = await ev(ROW(out.tidN));
    // The chat's own pills back to All: the thread rows read the new titles.
    await click(`document.querySelector('#chat-filters [data-filter="all"]')`, 'All filter');
    await until(`!!(${LINE('TOKU')}) && !!(${LINE('TOKN')})`, 'thread rows again');
    await holds(`(${LINE('TOKU')}).label.includes(${J(NAME_BLUR)}) && (${LINE('TOKN')}).label.includes('TOKN')`);
    out.linesAfter = {U: await ev(LINE('TOKU')), N: await ev(LINE('TOKN'))};

    // A thread's own window: the pencil after its title.
    await send('Page.navigate', {url:A.base + '/session?id=' + encodeURIComponent(A.po) + '&thread=' + encodeURIComponent(out.tidU)}, s);
    await until(`typeof THREAD_MODE !== 'undefined' && THREAD_MODE && (THR_INFO?.id||'')===${J(out.tidU)} && !!document.querySelector('#msgs .msg[data-mid]')`, 'thread window');
    await holds(`!document.getElementById('title-pen').hidden`, 40);
    await ev(SPY);
    const OWN = `(() => { const l=document.getElementById('title'), p=document.getElementById('title-pen'), i=document.querySelector('header h1 input.thr-name.head');
      return {title:l.textContent, labelHidden:l.hidden, penHidden:p.hidden, penShown:!!p.offsetParent, field:!!i, value:i ? i.value : null,
        focused:!!i && document.activeElement===i, to:document.getElementById('compose-destination-link')?.textContent||'', doc:document.title}; })()`;
    out.own0 = await ev(OWN);
    out.ownPenHit = await click(`document.getElementById('title-pen')`, 'own window pencil');
    await holds(`!!document.querySelector('header h1 input.thr-name.head')`, 20);
    out.ownOpen = await ev(OWN);
    await type(NAME_OWN);
    await key('Enter');
    await holds(`(() => { const o=${OWN}; return !o.field && o.title===${J(NAME_OWN)}; })()`);
    out.ownFile = await fileHolds(() => names()[out.tidU] === NAME_OWN);
    await holds(`(${OWN}).to===${J(NAME_OWN)}`);
    out.ownSaved = await ev(OWN);
    // The browser tab's title follows too.
    await holds(`document.title===${J('Thread · ' + NAME_OWN)}`, 40);
    out.ownDoc = await ev(`document.title`);
    out.ownNamed = await ev(`window.__named`);
    out.namesEnd = names();
    console.log(JSON.stringify(out));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ThreadNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-thread-names-', ignore_cleanup_errors=True)
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
        cls.sid = sid = 'thread-names-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner',
                                               'sessionId': sid, 'cwd': str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        assert threads.is_threaded(cls.po)
        (base / 'transcripts' / 'C--po').mkdir()
        cls.transcript = base / 'transcripts' / 'C--po' / f'{sid}.jsonl'
        cls.written = []        # every line written to the transcript, in order
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
                   cls.turn('assistant', f'Re {pt}: TOKT-answer The export takes a day.', at + 2),
                   cls.turn('user', tu, at + 10),
                   cls.turn('assistant', f'Re {pu}: TOKU-answer The upload page is next.', at + 11)])
        points.sync(cls.po, force=True)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'proj': cls.proj, 'po': cls.po,
                'side': str(threads._path(cls.po)),
                'names': {'NAME_NEW': NAME_NEW, 'NAME_HEAD': NAME_HEAD, 'NAME_LIST': NAME_LIST,
                          'NAME_BLUR': NAME_BLUR, 'NAME_OWN': NAME_OWN}}
        script = base / 'thread_names_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.side = threads.load_side(cls.po)
        print('\nthread names CDP: ' + json.dumps(cls.got, indent=1))

    @classmethod
    def turn(cls, role, text, ts):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(ts)) + '.%03dZ' % int(ts % 1 * 1000)
        msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
        if role == 'assistant':
            msg['stop_reason'] = 'end_turn'
        return {'type': role, 'timestamp': stamp, 'sessionId': cls.sid, 'message': msg}

    @classmethod
    def write(cls, turns):
        lines = [json.dumps(x) + '\n' for x in turns]
        with cls.lock, cls.transcript.open('a', encoding='utf-8') as f:
            f.write(''.join(lines))
            cls.written.extend(lines)

    @staticmethod
    def _deliver(h, room, text='', to='', key='', quiet=False, **kw):
        """Typed in at once; the PO reads it a second later and answers its point."""
        cls = ThreadNames
        sends.mark(room['id'], [key], 'delivered')
        pid = next((p['id'] for p in points.load(room['id']).get('points') or [] if p.get('key') == key), '')

        def read():
            time.sleep(1.0)
            tok = next((w for w in text.split() if w.startswith('TOK')), '')
            now = time.time()
            cls.write([cls.turn('user', text, now), cls.turn('assistant', f'Re {pid}: Answer to {tok}.', now + .5)])
        threading.Thread(target=read, daemon=True).start()
        return {'delivered': 1}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    # 1 -----------------------------------------------------------------
    def test_the_name_field_shows_only_with_the_thread_icon_and_forgets_when_off(self):
        g = self.got
        self.assertTrue(g['field0']['exists'], g['field0'])
        self.assertEqual((g['field0']['pressed'], g['field0']['hidden'], g['field0']['shown']), ('false', True, False), g['field0'])
        self.assertEqual((g['field1']['pressed'], g['field1']['hidden'], g['field1']['shown']), ('true', False, True), g['field1'])
        self.assertEqual(g['field1']['to'], 'a new thread', g['field1'])
        self.assertTrue(g['field1']['placeholder'], g['field1'])
        self.assertEqual(g['field1typed']['value'], 'Typed then dropped', g['field1typed'])
        self.assertEqual((g['field2']['pressed'], g['field2']['hidden'], g['field2']['value']), ('false', True, ''), g['field2'])
        self.assertEqual((g['field3']['hidden'], g['field3']['value']), (False, ''), g['field3'])
        self.assertEqual(g['field4']['value'], NAME_NEW, g['field4'])
        ne = g['nameEnter']
        self.assertEqual((ne['active'], ne['field']['pressed'], ne['field']['value'], ne['msgs']),
                         ('input', 'true', NAME_NEW, g['sentBeforeEnter']), 'Enter in the name field moves on, sends nothing')
        # Sent: the icon off, the field hidden and empty.
        self.assertEqual((g['field5']['pressed'], g['field5']['hidden'], g['field5']['value']), ('false', True, ''), g['field5'])

    def test_a_name_given_at_creation_is_kept_with_the_send_and_is_the_threads_title(self):
        rows = [a for a in self.side.get('sent') or [] if a.get('new') and 'TOKN' in ' '.join(a.get('words') or [])]
        if not rows:    # older side records: the newest send
            rows = [self.side['sent'][-1]]
        self.assertEqual(rows[-1].get('name'), NAME_NEW, rows)
        self.assertFalse([a for a in self.side.get('sent') or [] if a.get('name') == 'Typed then dropped'])
        made = self.got['madeLine']
        self.assertIn(NAME_NEW, made['label'], made)
        self.assertIn('1 repl', made['text'], made)
        head = self.got['madeHead']
        self.assertEqual(head['title'], NAME_NEW, head)
        self.assertEqual(head['frameTo'], 'PO in ' + NAME_NEW, head)   # #408: the To line names the thread's agent, then the thread
        self.assertEqual(self.got['listN0']['title'], NAME_NEW, self.got['listN0'])

    # 2 -----------------------------------------------------------------
    def test_the_header_pencil_renames_the_thread_beside_the_chat(self):
        g = self.got
        h0, op = g['head0'], g['headOpen']
        self.assertTrue(h0['title'].startswith('TOKT'), h0)
        self.assertEqual((h0['penHidden'], h0['penShown'], h0['fields']), (False, True, 0), h0)
        self.assertTrue(g['headPenHit'], 'the header pencil is covered')
        self.assertEqual((op['field'], op['fields'], op['labelHidden'], op['penHidden'], op['afterLabel']), (True, 1, True, True, True), op)
        self.assertEqual(op['value'], h0['title'], op)
        self.assertTrue(op['focused'], op)
        self.assertEqual(op['selected'], len(h0['title']), 'the old name is selected, typing replaces it')
        self.assertEqual(op['placeholder'], h0['title'], 'the placeholder is the automatic title')
        self.assertEqual(g['headTyped']['value'], NAME_HEAD, g['headTyped'])
        sv = g['headSaved']
        self.assertTrue(g['headSavedFile'], 'kept in the thread record')
        self.assertEqual((sv['title'], sv['field'], sv['labelHidden'], sv['penHidden']), (NAME_HEAD, False, False, False), sv)
        to = g['headTo']
        self.assertEqual((to['frameTo'], to['mainTo']), ('PO in ' + NAME_HEAD, 'PO in ' + NAME_HEAD), to)
        self.assertIn(NAME_HEAD, to['chip'], to)
        self.assertIn(NAME_HEAD, g['headLine']['label'], g['headLine'])
        self.assertEqual(g['headLater']['title'], NAME_HEAD, g['headLater'])
        self.assertEqual(g['headLater']['frameTo'], 'PO in ' + NAME_HEAD, g['headLater'])

    # 3 -----------------------------------------------------------------
    def test_the_threads_list_pencil_renames_a_row(self):
        g = self.got
        r0, op = g['list0'], g['listOpen']
        self.assertTrue(r0['title'].startswith('TOKU'), r0)
        self.assertEqual((r0['pen'], r0['penHidden'], r0['penShown']), (True, False, True), r0)
        self.assertIn(r0['title'], r0['penLabel'], r0)
        self.assertTrue(g['listPenHit'], 'the list pencil is covered')
        self.assertEqual((op['field'], op['fields'], op['openHidden'], op['penHidden']), (True, 1, True, True), op)
        self.assertEqual(op['value'], r0['title'], op)
        self.assertTrue(op['focused'], op)
        self.assertEqual(op['selected'], len(r0['title']), op)
        self.assertIn('Esc', op['keys'], op)
        # The pencil does not open the thread.
        self.assertNotEqual(g['listOpenThreadSide'], g['tidU'])
        tp = g['listTypedAfterPoll']
        self.assertEqual((tp['field'], tp['value']), (True, NAME_LIST), 'a poll keeps the field and what is typed')
        sv = g['listSaved']
        self.assertTrue(g['listSavedFile'], 'kept in the thread record')
        self.assertEqual((sv['title'], sv['field'], sv['openHidden'], sv['penHidden']), (NAME_LIST, False, False, False), sv)
        self.assertIn(NAME_LIST, sv['label'], sv)

    def test_clicking_away_saves(self):
        g = self.got
        self.assertTrue(g['listBlurFile'], g['listBlur'])
        self.assertEqual((g['listBlur']['title'], g['listBlur']['field']), (NAME_BLUR, False), g['listBlur'])
        self.assertIn(NAME_BLUR, g['linesAfter']['U']['label'], g['linesAfter'])

    # 4 -----------------------------------------------------------------
    def test_escape_cancels_in_the_header(self):
        g = self.got
        self.assertTrue(g['headEscOpen']['field'], g['headEscOpen'])
        e = g['headEsc']
        self.assertEqual((e['title'], e['field'], e['fields'], e['labelHidden'], e['penHidden']), (NAME_HEAD, False, 0, False, False), e)
        self.assertTrue(e['open'], 'Esc closes the field, not the thread')
        self.assertEqual(g['headEscNamed'], g['namedBeforeEsc'], 'nothing sent')
        self.assertEqual(g['headEscNames'], g['namesBeforeEsc'], 'nothing kept')
        self.assertEqual(g['headEscNames'][g['tidT']], NAME_HEAD)

    def test_escape_cancels_in_the_threads_list(self):
        g = self.got
        self.assertEqual(g['listEscTyped']['value'], 'Nope', g['listEscTyped'])
        e = g['listEsc']
        self.assertEqual((e['title'], e['field'], e['fields'], e['openHidden'], e['penHidden']), (NAME_LIST, False, 0, False, False), e)
        self.assertEqual(g['listEscNamed'], g['listNamedBeforeEsc'], 'nothing sent')
        self.assertEqual(g['listEscNames'], g['listNamesBeforeEsc'], 'nothing kept')

    # 5 -----------------------------------------------------------------
    def test_an_empty_name_is_the_automatic_title_again(self):
        g = self.got
        self.assertEqual(g['headEmptyTyped']['value'], '', g['headEmptyTyped'])
        self.assertTrue(g['headEmptyFile'], 'an empty name kept as the automatic title')
        self.assertEqual((g['headEmpty']['title'], g['headEmpty']['field']), (g['head0']['title'], False), g['headEmpty'])
        self.assertEqual(g['headEmptyTo']['frameTo'], 'PO in ' + g['head0']['title'], g['headEmptyTo'])
        # Over a name given at creation too.
        self.assertTrue(g['listNOpen']['field'] and g['listNOpen']['value'] == NAME_NEW, g['listNOpen'])
        self.assertTrue(g['listNEmptyFile'], 'empty over the name given at creation')
        n = g['listNEmpty']
        self.assertTrue(n['title'].startswith('TOKN a thread about colours'), n)
        self.assertNotIn(NAME_NEW, g['linesAfter']['N']['label'], g['linesAfter'])
        names = self.side.get('names') or {}
        self.assertEqual((names.get(g['tidT']), names.get(g['tidN'])), ('', ''), names)

    # Own window ----------------------------------------------------------
    def test_a_threads_own_window_renames_from_its_header(self):
        g = self.got
        o0, op, sv = g['own0'], g['ownOpen'], g['ownSaved']
        self.assertEqual(o0['title'], NAME_BLUR, o0)
        self.assertEqual((o0['penHidden'], o0['penShown']), (False, True), o0)
        self.assertTrue(g['ownPenHit'], 'the window pencil is covered')
        self.assertEqual((op['field'], op['labelHidden'], op['penHidden'], op['value'], op['focused']), (True, True, True, NAME_BLUR, True), op)
        self.assertTrue(g['ownFile'], 'kept in the thread record')
        self.assertEqual((sv['title'], sv['field'], sv['to']), (NAME_OWN, False, 'PO in ' + NAME_OWN), sv)
        self.assertEqual(g['ownDoc'], 'Thread · ' + NAME_OWN, 'the tab title follows the rename')
        self.assertEqual(g['ownNamed'], [{'roomId': self.po, 'thread': g['tidU'], 'name': NAME_OWN}])
        self.assertEqual((self.side.get('names') or {}).get(g['tidU']), NAME_OWN)

    # No message rewritten --------------------------------------------------
    def test_no_message_is_rewritten(self):
        on_disk = self.transcript.read_text(encoding='utf-8')
        self.assertEqual(on_disk, ''.join(self.written))
        for name in (NAME_NEW, NAME_HEAD, NAME_LIST, NAME_BLUR, NAME_OWN) + TYPED_NOT_KEPT:
            self.assertNotIn(name, on_disk, name)
        # The names are only in the thread record.
        self.assertEqual(set(self.side.get('names') or {}), {self.got['tidT'], self.got['tidU'], self.got['tidN']}, self.side.get('names'))


if __name__ == '__main__':
    unittest.main()
