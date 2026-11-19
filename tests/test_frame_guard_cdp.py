"""A panel whose page never came is given its address once more (ED-399, GitHub issue 65).

The hub here holds a chosen first request for a chat page and never answers
it, which leaves the frame as the reported one was: the right address, an
empty document. static/frame-guard.js must set the address again once, and
the panel then loads. A frame that got its page (loaded, slow to stream, its
address changed by the page itself, given another address meanwhile) is
never touched, and a draft typed in it stays.

Run with the frame-guard.js script tag taken out of index.html, the held
panels stay blank and the "loads" tests fail.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import threads  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
# The guard's wait here (8 s in use). Not under ~2.5 s: Chrome ignores a
# second navigation of a frame to the address it is still loading when it
# comes soon after the first (measured: at 2.5 s the guard's set did nothing,
# at 5 s it loaded).
WAIT_MS = 5000

CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
// The hub's test switch: hold (never answer) or stream slowly the requests
// whose path matches m, n times; hits counts the requests it saw.
// A held request is paused in the browser (CDP Fetch) and never let go, so
// it never reaches the hub: the frame keeps its empty document, as in the
// report. (Held in the hub instead, Chrome's cache lock would queue the
// guard's second request behind it.) asked counts the requests the browser
// made for a pattern, held or not.
const holds = [], asked = [], askedT = []; let T0 = Date.now();
const path = u => { const x = new URL(u); return x.pathname + x.search; };
const rule = async (m, kind, n) => {
  // Held: every request the browser makes for it in the next n ms (opening a
  // task puts its frame in twice, so its page is asked twice).
  if (kind === 'hold') { holds.push({re: new RegExp(m), until: Date.now() + n}); return {ok: true}; }
  return fetch(A.base + '/__fg/rule?' + new URLSearchParams({m, kind, n: String(n)})).then(r => r.json());
};
const askedFor = m => asked.filter(p => new RegExp(m).test(p)).length;
const hits = m => fetch(A.base + '/__fg/hits?' + new URLSearchParams({m})).then(r => r.json());
const enc = s => encodeURIComponent(s);
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--disable-popup-blocking', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((res,rej) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited '+c))); setTimeout(() => rej(new Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((res,rej) => { c.onopen=res; c.onerror=rej; });
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id);
    if (m.method === 'Fetch.requestPaused') { paused(m.params, m.sessionId); return; }
    if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((res,rej) => { const id=++seq; waits.set(id,[res,rej]); c.send(JSON.stringify({id,method,params,sessionId})); });
  function paused(p, sid) {
    const at = path(p.request.url);
    asked.push(at); askedT.push([Date.now() - T0, at]);
    if (holds.some(h => Date.now() < h.until && h.re.test(at))) return;
    send('Fetch.continueRequest', {requestId: p.requestId}, sid).catch(() => {});
  }
  await send('Fetch.enable', {patterns: [{urlPattern: '*/session?*', requestStage: 'Request'}]});
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  await send('Page.addScriptToEvaluateOnNewDocument',{source:'window.FRAME_GUARD_MS=' + A.wait + `; window.__log = []; window.__t0 = Date.now();
    new MutationObserver(rs => { for (const r of rs) {
      if (r.type === 'attributes' && r.target.localName === 'iframe') __log.push([Date.now() - __t0, 'src', r.target.getAttribute('src')]);
      for (const n of r.addedNodes || []) if (n.nodeType === 1) for (const f of (n.localName === 'iframe' ? [n] : n.querySelectorAll('iframe'))) __log.push([Date.now() - __t0, 'in', f.getAttribute('src')]);
      for (const n of r.removedNodes || []) if (n.nodeType === 1) for (const f of (n.localName === 'iframe' ? [n] : n.querySelectorAll('iframe'))) __log.push([Date.now() - __t0, 'out', f.getAttribute('src')]);
    } }).observe(document, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});`},s);
  await send('Emulation.setDeviceMetricsOverride',{width:1728,height:1117,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (e, what, n=200) => { for(let i=0;i<n;i++){ try {if(await ev(e)) return;}catch(x){} await sleep(100); } throw Error('timeout '+(what||e)); };
  const PO = `PD.els['po-chat'].querySelector('iframe.po-session:not([hidden])')`;
  const THR = `PD.thrIn?.el.querySelector('iframe')`;
  const DET = `document.querySelector('#detail-panel iframe.dp-session')`;
  const loaded = f => `!!${f}?.contentDocument?.querySelector('#compose #input')`;
  // The guard's retries of the pages whose path matches m.
  const retries = m => ev(`(window.frameGuard?.retries || []).filter(r => { const u = new URL(r.url); return new RegExp(${JSON.stringify(m)}).test(u.pathname + u.search); }).length`);
  const openTask = room => ev(`openDetail(ALL_ROWS.find(r=>r.roomId===${JSON.stringify(room)}).sessionId); 0`);
  const out = {};
  try {
    // 1. The PO chat's first page is never answered.
    const poRe = '^/session\\?id=' + enc(A.po) + '&embed=1$';
    await rule(poRe, 'hold', A.wait / 2);
    T0 = Date.now();
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.po)})`, 'rows');
    await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
    const t0 = Date.now();
    await until(`!!PD.dock && !!${PO}`, 'PO frame');
    await until(`!!${PO}?.contentDocument?.querySelector('#msgs .thr-open[data-thr]')`, 'PO chat loads', 300);
    out.po = {ms: Date.now() - t0, retries: await retries(poRe), hits: await hits(poRe), asked: askedFor(poRe), waitMs: await ev('window.frameGuard?.waitMs()')};

    // 2. A task's chat opened in the middle (openDetail), first page held.
    const taskRe = '^/session\\?id=' + enc(A.task) + '&embed=1$';
    await rule(taskRe, 'hold', A.wait / 2);
    await openTask(A.task);
    await until(loaded(DET), 'task chat loads', 300);
    out.task = {retries: await retries(taskRe), hits: await hits(taskRe), asked: askedFor(taskRe)};
    await ev('closeDetail(); 0'); await sleep(300);

    // 3. A thread shown in place over the PO chat, first page held.
    const thrRe = '^/session\\?id=' + enc(A.po) + '&embed=1&side=1&thread=';
    await rule(thrRe, 'hold', A.wait / 2);
    await ev(`${PO}.contentDocument.querySelector('#msgs .thr-open[data-thr]').click(); 0`);
    await until(`!!${THR}?.contentDocument?.querySelector('#msgs .msg[data-mid]')`, 'thread loads', 300);
    out.thread = {retries: await retries(thrRe), hits: await hits(thrRe), asked: askedFor(thrRe)};

    // 4. Loaded frames are left alone: a mark in each page and a draft stay.
    await ev(`(() => { for (const f of [${PO}, ${THR}]) f.contentWindow.__mark = 1;
      const i = ${PO}.contentDocument.querySelector('#compose #input'); i.value = 'a draft kept'; i.dispatchEvent(new Event('input', {bubbles:true})); return 0; })()`);
    const before = await ev('frameGuard.retries.length');
    await sleep(3 * A.wait);
    out.kept = await ev(`({po: ${PO}.contentWindow.__mark === 1, thread: ${THR}.contentWindow.__mark === 1,
      draft: ${PO}.contentDocument.querySelector('#compose #input').value, more: frameGuard.retries.length - ${before}})`);

    // 5. Every request for one page is held: one more try, never a third.
    const holdRe = '^/session\\?id=' + enc(A.held) + '&embed=1$';
    await rule(holdRe, 'hold', 1e9);
    await openTask(A.held);
    await sleep(4 * A.wait);
    out.held = {retries: await retries(holdRe), hits: await hits(holdRe), asked: askedFor(holdRe), blank: await ev(`!${DET}?.contentDocument?.querySelector('#compose')`)};
    await ev('closeDetail(); 0'); await sleep(300);

    // 6. A page slow to stream (half its HTML, then a pause longer than the
    //    wait) is not asked again.
    const slowRe = '^/session\\?id=' + enc(A.slow) + '&embed=1$';
    await rule(slowRe, 'slow', 1000);
    await openTask(A.slow);
    await until(loaded(DET), 'slow chat loads', 300);
    await sleep(A.wait);
    out.slow = {retries: await retries(slowRe), hits: await hits(slowRe)};
    await ev('closeDetail(); 0'); await sleep(300);

    // 7. A page that changes its own address (replaceState) is not asked
    //    again. (The task's frame is kept from step 2: it is given an address
    //    of its own here.)
    const rsRe = '^/session\\?id=' + enc(A.task) + '&embed=1&rs=1$';
    await openTask(A.task);
    await until(`!!${DET}`, 'task frame');
    await ev(`${DET}.src = '/session?id=' + ${JSON.stringify(enc(A.task))} + '&embed=1&rs=1'; 0`);
    await until(`/rs=1/.test(${DET}?.contentDocument?.URL || '') && ${loaded(DET)}`, 'rs page', 300);
    await ev(`(() => { const w = ${DET}.contentWindow; w.history.replaceState(null, '', '/session?id=' + ${JSON.stringify(enc(A.task))} + '&embed=1&moved=1'); w.__mark = 1; return 0; })()`);
    await sleep(3 * A.wait);
    out.replaced = {mark: await ev(`${DET}.contentWindow.__mark === 1`), url: await ev(`${DET}.contentDocument.URL`),
      hits: (await hits(rsRe)).n, retries: await retries(rsRe)};
    await ev('closeDetail(); 0'); await sleep(300);

    // 8. Another address set during the wait: the guard follows the new one.
    const nextRe = '^/session\\?id=' + enc(A.next) + '&embed=1$';
    await rule(nextRe, 'hold', A.wait / 2);
    await openTask(A.next);
    await until(`!!${DET}`, 'next frame');
    await sleep(A.wait / 3);
    await ev(`${DET}.src = '/session?id=' + ${JSON.stringify(enc(A.task))} + '&embed=1&other=1'; 0`);
    await until(loaded(DET), 'the new address loads', 300);
    await sleep(2 * A.wait);
    out.followed = {src: await ev(`${DET}.getAttribute('src')`),
      retriesOld: await retries(nextRe), retriesNew: await retries('&other=1$'), hitsOld: await hits(nextRe), askedOld: askedFor(nextRe)};
    out.all = await ev('frameGuard.retries.map(r => [r.at - __t0, r.url.split("?")[1]])');
    out.log = await ev('__log.filter(x => /room/.test(x[2] || ""))');
    out.asked = askedT;
    console.log(JSON.stringify(out));
  } catch (e) {
    // What the frames showed when a wait ran out.
    const diag = await ev(`JSON.stringify({retries: window.frameGuard?.retries, frames: [...document.querySelectorAll('iframe')].map(f => {
      let u = ''; try { u = f.contentDocument?.URL; } catch (x) { u = 'x'; } return {src: f.getAttribute('src'), url: u, cls: f.className}; })})`).catch(x => String(x));
    throw Error(e.message + ' ' + diag + ' asked ' + JSON.stringify(asked) + ' live ' + JSON.stringify(await fetch(A.base + '/__fg/live').then(r => r.json())));
  } finally { await send('Browser.close').catch(()=>{}); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


class _Rules:
    """What the test hub does with the chat pages: per pattern, how many
    requests to hold or to stream slowly, and the requests it saw."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rules = {}   # pattern -> [kind, n left]
        self.seen = []    # every path asked
        self.release = threading.Event()
        self.live = {}

    def take(self, path):
        with self.lock:
            self.seen.append(path)
            for m, r in self.rules.items():
                if r[1] > 0 and re.search(m, path):
                    r[1] -= 1
                    return r[0]
        return ''

    def hits(self, m):
        with self.lock:
            return sum(1 for p in self.seen if re.search(m, p))


class _SlowWriter:
    """The page's body written as half, a pause, the rest."""

    def __init__(self, raw, pause):
        self.raw, self.pause = raw, pause

    def write(self, b):
        if len(b) > 20000:
            half = len(b) // 2
            self.raw.write(b[:half]); self.raw.flush()
            time.sleep(self.pause)
            return self.raw.write(b[half:])
        return self.raw.write(b)

    def __getattr__(self, name):
        return getattr(self.raw, name)


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class FrameGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-frame-guard-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        cls.root = base / 'EnsembleProjects'; cls.root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        rules = cls.rules = _Rules()
        orig_get = dashboard.Handler.do_GET

        def do_get(h):
            u = urlparse(h.path)
            if u.path == '/__fg/live':
                with rules.lock:
                    h._send_json(200, {'live': list(rules.live.values())})
                return
            if u.path.startswith('/__fg/'):
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == '/__fg/rule':
                    with rules.lock:
                        rules.rules[q['m']] = [q['kind'], int(q['n'])]
                    body = {'ok': True}
                else:
                    body = {'n': rules.hits(q['m'])}
                h._send_json(200, body)
                return
            kind = rules.take(h.path) if u.path == '/session' else ''
            key = object()
            with rules.lock:
                rules.live[key] = h.path
            try:
                return _serve(h, kind)
            finally:
                with rules.lock:
                    rules.live.pop(key, None)

        def _serve(h, kind):
            if kind == 'hold':
                # Never answered: the browser gives up on it when the frame
                # is given its address again (or the test ends).
                rules.release.wait(120)
                h.close_connection = True
                return
            if kind == 'slow':
                h.wfile = _SlowWriter(h.wfile, 3 * WAIT_MS / 1000)
            return orig_get(h)

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
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, 'do_GET', do_get),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        cls.proj = proj['id']
        home = Path(proj.get('home') or proj['path'])
        po_sid = 'guard-po'
        po = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner','sessionId':po_sid,'cwd':str(home)}])
        po_data = chatroom.get_room(po['id'], public=False)
        po_data['mode'] = 'solo'; chatroom.update_room(po_data)
        dashboard.assign_session_project(po['id'], cls.proj)
        dashboard.set_project_po(cls.proj, po['id'])
        cls.po = po['id']
        transcript = base / 'transcripts' / 'C--po'
        transcript.mkdir()
        at = time.time() - 3600

        def turn(role, text, offset):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type':'text','text':text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type':role,'timestamp':stamp,'sessionId':po_sid,'message':msg}
        lines = [turn('user', 'The panel stays blank.', 1)]
        lines += [turn('assistant', f'Answer {i}: a line of the thread.', i + 2) for i in range(3)]
        (transcript / f'{po_sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
        threads.note_send(cls.po, '', '', [], False, 'The panel stays blank.', now=at + 1, new=True)
        rooms = {}
        for key in ('task', 'held', 'slow', 'next'):
            r = chatroom.create_room('Task ' + key, [{'identity':'claude','agent':'claude','cwd':str(home)}])
            chatroom.post_message(r['id'], 'user', 'A message in ' + key)
            dashboard.assign_session_project(r['id'], cls.proj)
            rooms[key] = r['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name, 'wait': WAIT_MS,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'proj': cls.proj, 'po': cls.po, **rooms}
        cls.rooms = rooms
        script = base / 'frame_guard_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        try:
            out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        finally:
            rules.release.set()
        assert out.returncode == 0, out.stderr[-4000:]
        Path(os.environ.get('FG_DUMP', os.devnull)).write_text(out.stdout)
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        print('\nframe guard CDP: ' + json.dumps({k: v for k, v in cls.got.items() if k not in ('log', 'asked')}, indent=1))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_the_guard_runs_with_the_test_wait(self):
        self.assertEqual(self.got['po']['waitMs'], WAIT_MS)

    def assert_loaded_after_one_retry(self, g):
        # Asked by the browser (held) and once more by the guard: only that
        # one reached the hub.
        self.assertEqual(g['retries'], 1, g)
        self.assertEqual(g['hits']['n'], 1, g)
        self.assertGreaterEqual(g['asked'], 2, g)

    def test_a_po_chat_never_answered_loads(self):
        self.assert_loaded_after_one_retry(self.got['po'])

    def test_a_task_chat_never_answered_loads(self):
        self.assert_loaded_after_one_retry(self.got['task'])

    def test_a_thread_never_answered_loads(self):
        self.assert_loaded_after_one_retry(self.got['thread'])

    def test_loaded_frames_and_a_draft_are_left_alone(self):
        g = self.got['kept']
        self.assertTrue(g['po'] and g['thread'], g)
        self.assertEqual(g['draft'], 'a draft kept')
        self.assertEqual(g['more'], 0, g)

    def test_a_page_never_answered_is_asked_once_more_only(self):
        g = self.got['held']
        self.assertEqual(g['retries'], 1, g)
        self.assertEqual(g['hits']['n'], 0, g)
        self.assertTrue(g['blank'], g)

    def test_a_page_slow_to_stream_is_not_asked_again(self):
        g = self.got['slow']
        self.assertEqual(g['retries'], 0, g)
        self.assertGreaterEqual(g['hits']['n'], 1, g)

    def test_a_page_that_changed_its_own_address_is_left_alone(self):
        g = self.got['replaced']
        self.assertTrue(g['mark'], g)
        self.assertIn('moved=1', g['url'])
        self.assertEqual((g['hits'], g['retries']), (1, 0), g)

    def test_a_new_address_during_the_wait_is_followed(self):
        g = self.got['followed']
        self.assertIn('&other=1', g['src'])
        self.assertEqual((g['retriesOld'], g['retriesNew']), (0, 0), g)
        self.assertEqual(g['hitsOld']['n'], 0, g)


if __name__ == '__main__':
    unittest.main()
