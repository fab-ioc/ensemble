"""A link to a chat message or card opens it in place (#235).

An agent writes such a link with the hub's address as it knows it
(http://127.0.0.1:8765/session?room=…&msg=…[&part=ask:N]). Read on the
dashboard at another address (localhost here), a click on it must land on the
message in the chat that holds it, never open a second Ensemble window or a
file view; Ctrl-click opens one tab, at the reader's own address. On a chat
page of its own (no dashboard), the same click stays in that window.
"""
from __future__ import annotations

import json
import os
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--disable-gpu', '--disable-popup-blocking', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((res,rej) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) res(m[1]);
  }); ch.on('exit', c => rej(new Error('chrome exited '+c))); setTimeout(() => rej(new Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((res,rej) => { c.onopen=res; c.onerror=rej; });
  const created = [];
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id);
    if (w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); }
    if (m.method === 'Target.targetCreated' && m.params.targetInfo.type === 'page') created.push(m.params.targetInfo.targetId); };
  const send=(method,params={},sessionId) => new Promise((res,rej) => { const id=++seq; waits.set(id,[res,rej]); c.send(JSON.stringify({id,method,params,sessionId})); });
  await send('Target.setDiscoverTargets', {discover:true});
  const out = {};
  const pages = async except => (await send('Target.getTargets')).targetInfos.filter(t => t.type === 'page' && t.targetId !== except && t.url !== 'about:blank');
  async function tab(url, fn) {
    const {targetId}=await send('Target.createTarget',{url:'about:blank'});
    const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
    await send('Page.enable',{},s);
    await send('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false},s);
    const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(expression.slice(0,100)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
    const until=async (expression, what) => { let le; for(let i=0;i<150;i++){ try {if(await ev(expression)) return;}catch(e){le=e;} await sleep(150); } throw Error('timeout: '+(what||expression)+' '+(le||'')); };
    const click = async (x, y, modifiers=0) => {
      await send('Input.dispatchMouseEvent', {type:'mouseMoved', x, y}, s);
      for (const type of ['mousePressed','mouseReleased']) await send('Input.dispatchMouseEvent', {type, x, y, button:'left', clickCount:1, modifiers}, s);
    };
    await send('Page.navigate',{url},s);
    try { await fn({ev, until, click, targetId}); }
    finally {
      for (const t of await pages(targetId)) { try { await send('Target.closeTarget', {targetId: t.targetId}); } catch (e) {} }
      await send('Target.closeTarget', {targetId});
    }
  }
  // Every frame's .landed balloon, as it comes and goes (the mark lasts 2.6 s).
  const RECORD = `(() => { window.__landed = []; const walk = (d, src) => { for (const e of d.querySelectorAll('.landed')) { const m = e.closest('.msg[data-mid]'); const k = src + ' ' + (m ? m.dataset.mid : '?'); if (!window.__landed.includes(k)) window.__landed.push(k); }
      for (const f of d.querySelectorAll('iframe')) { try { if (f.contentDocument) walk(f.contentDocument, f.src); } catch (e) {} } };
    setInterval(() => walk(document, location.href), 50); return 0; })()`;
  const FILEVIEW = `[...document.querySelectorAll('iframe')].some(f => /fileview/.test(f.src))`;
  const PO = `document.querySelector('iframe.po-session:not([hidden])')`;
  const where = (frame, text) => `(() => { const f = ${frame}; const a = [...f.contentDocument.querySelectorAll('#msgs .msg a')].find(a => a.textContent.includes(${JSON.stringify(text)}));
    if (!a) return null; a.scrollIntoView({block:'center'}); const b = a.getBoundingClientRect(), fb = f.getBoundingClientRect();
    return { x: fb.left + b.left + Math.min(8, b.width / 2), y: fb.top + b.top + b.height / 2, cls: a.className, href: a.getAttribute('href'), target: a.target }; })()`;
  // The link's place once the chat holds still (a late redraw moves it), and
  // the link is what is at that point.
  const aim = async (ev, frame, text) => {
    let last = null;
    for (let i = 0; i < 40; i++) {
      const a = await ev(where(frame, text));
      const hit = a && await ev(`(() => { const f = ${frame}, fb = f.getBoundingClientRect(); const e = f.contentDocument.elementFromPoint(${a.x} - fb.left, ${a.y} - fb.top); return !!(e && e.closest('a') && e.closest('a').textContent.includes(${JSON.stringify(text)})) && document.elementFromPoint(${a.x}, ${a.y}) === f; })()`);
      if (a && hit && last && last.x === a.x && last.y === a.y) return a;
      last = a; await sleep(250);
    }
    const top = last && await ev(`(() => { const e = document.elementFromPoint(${last.x}, ${last.y}); return e ? e.tagName + '.' + e.className + ' in ' + ((e.closest('[id]') || {}).id || '') : null; })()`);
    throw Error('link not still or covered: ' + text + ' ' + JSON.stringify(last) + ' top=' + top);
  };
  try {
    // In the dashboard: the PO chat docked, its whole chat shown.
    const dock = async ({ev, until}) => {
      await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r => r.roomId === ${JSON.stringify(A.po)})`, 'rows');
      await ev(`SELECTED_PROJECT=${JSON.stringify(A.proj)}; PROJECT_TAB='tasks'; renderRows(); 0`);
      await until(`document.body.classList.contains('po-dock') && ${PO} && ${PO}.contentWindow.toggleWhole && ${PO}.contentDocument.body.innerText.includes('Start.')`, 'PO chat');
      // A task chat an earlier tab opened comes back beside it: closed, so each tab starts alike.
      await ev(`document.body.classList.contains('detail-open') && closeDetail(), 0`);
      await ev(`${PO}.contentWindow.toggleWhole(); 0`);
      await until(`${PO}.contentDocument.querySelectorAll('#msgs .msg a.msg-link').length >= 3`, 'links drawn');
      await sleep(500);
    };
    for (const [key, text] of [['card', 'auto or copilot'], ['task', 'the task message'], ['unknown', '26e81eed'], ['chip', 'Filler 3']]) {
      await tab(A.base + '/', async ({ev, until, click, targetId}) => {
        await dock({ev, until});
        let a = await aim(ev, PO, text);
        await ev(RECORD);
        created.length = 0;
        // A busy machine can let a late redraw take the click: once more if nothing came of it.
        for (let tries = 0; tries < 2; tries++) {
          await click(a.x, a.y);
          for (let i = 0; i < 40; i++) { await sleep(100); if ((await ev('window.__landed')).length || created.length) break; }
          if ((await ev('window.__landed')).length || created.length || key === 'unknown') break;
          a = await aim(ev, PO, text);
        }
        await sleep(1000);
        out[key] = { link: a, created: created.length, open: (await pages(targetId)).map(t => t.url), landed: await ev('window.__landed'),
          top: await ev('location.href'), fileview: await ev(FILEVIEW) };
      });
    }
    // Ctrl-click: one tab, at this page's own address.
    await tab(A.base + '/', async ({ev, until, click, targetId}) => {
      await dock({ev, until});
      let a = await aim(ev, PO, 'auto or copilot');
      created.length = 0;
      for (let tries = 0; tries < 2 && !created.length; tries++) {
        if (tries) a = await aim(ev, PO, 'auto or copilot');
        await click(a.x, a.y, 2);
        for (let i = 0; i < 30 && !created.length; i++) await sleep(100);
      }
      await sleep(1500);
      out.ctrl = { created: created.length, open: (await pages(targetId)).map(t => t.url), top: await ev('location.href') };
    });
    // A chat page of its own: the click stays in this window.
    await tab(A.base + '/session?id=' + A.po, async ({ev, until, click, targetId}) => {
      await until(`typeof toggleWhole === 'function' && document.body.innerText.includes('Start.')`, 'standalone chat');
      await ev('toggleWhole(); 0');
      await until(`document.querySelectorAll('#msgs .msg a.msg-link').length >= 3`, 'standalone links');
      await sleep(500);
      const a = await ev(`(() => { const a = [...document.querySelectorAll('#msgs .msg a')].find(a => a.textContent.includes('auto or copilot')); a.scrollIntoView({block:'center'}); const b = a.getBoundingClientRect(); return { x: b.left + Math.min(8, b.width / 2), y: b.top + b.height / 2 }; })()`);
      created.length = 0;
      await click(a.x, a.y);
      let landed = null;
      for (let i = 0; i < 60 && !landed; i++) { await sleep(100); try { landed = await ev(`(() => { const e = document.querySelector('.landed'); const m = e && e.closest('.msg[data-mid]'); return m ? m.dataset.mid : null; })()`); } catch (e) {} }
      out.standalone = { created: created.length, open: (await pages(targetId)).map(t => t.url), top: await ev('location.href'), landed };
    });
    // A link in an HTML preview (fileview's frame): the chat at this origin, one tab, never a blank one.
    await tab(A.base + '/fileview?path=' + encodeURIComponent(A.html), async ({ev, until, click, targetId}) => {
      const F = `document.querySelector('iframe.file')`;
      await until(`${F} && ${F}.contentDocument && ${F}.contentDocument.querySelector('a#m')`, 'preview');
      await sleep(500);
      const a = await ev(`(() => { const f = ${F}, b = f.contentDocument.querySelector('a#m').getBoundingClientRect(), fb = f.getBoundingClientRect(); return { x: fb.left + b.left + 4, y: fb.top + b.top + b.height / 2 }; })()`);
      created.length = 0;
      await click(a.x, a.y);
      let open = [];
      for (let i = 0; i < 40 && !open.length; i++) { await sleep(150); open = (await pages(targetId)).map(t => t.url); }
      await sleep(500);
      out.preview = { created: created.length, open: (await pages(targetId)).map(t => t.url), top: await ev('location.href') };
    });
  } finally { try { await send('Browser.close'); } catch (e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class MessageLinksInPlace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-235-', ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / 'state'; state.mkdir()
        root = base / 'EnsembleProjects'; root.mkdir()
        (base / 'transcripts').mkdir(); (base / 'cs').mkdir()
        cls.patches = [
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
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches: p.start()
        ok, proj, _ = dashboard.register_project('Motors')
        home = Path(proj.get('home') or proj['path'])
        sid = 'linkpo'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': sid, 'cwd': str(home)}])
        d = chatroom.get_room(po['id'], public=False); d['mode'] = 'solo'; chatroom.update_room(d)
        dashboard.assign_session_project(po['id'], proj['id']); dashboard.set_project_po(proj['id'], po['id'])
        task = chatroom.create_room('Linked task', [{'identity': 'claude', 'agent': 'claude', 'cwd': str(home)},
                                                    {'identity': 'claude-2', 'agent': 'claude', 'role': 'Reviewer', 'cwd': str(home)}])
        chatroom.post_message(task['id'], 'claude', 'The task message the link names.', to='user')
        dashboard.assign_session_project(task['id'], proj['id'])
        cls.tmid = chatroom.get_room(task['id'], public=False)['messages'][-1]['id']
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler); cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        port = cls.server.server_address[1]
        at = time.time() - 3600

        def turn(role, text, off):
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + off)) + '.000Z'
            msg = {'role': role, 'content': text if role == 'user' else [{'type': 'text', 'text': text}]}
            if role == 'assistant': msg['stop_reason'] = 'end_turn'
            return {'type': role, 'timestamp': stamp, 'sessionId': sid, 'message': msg}
        rid = po['id']
        # What the agent writes: the hub's loopback address, read here at localhost.
        last = (f'One question is still open, on this card: [auto or copilot](http://127.0.0.1:{port}/session?room={rid}&msg={sid}:1&part=ask:1). I recommend auto.\n\n'
                f'Task: [the task message](http://127.0.0.1:{port}/session?room={task["id"]}&msg={cls.tmid})\n\n'
                f'Bare self: http://127.0.0.1:{port}/session?room={rid}&msg={sid}:3\n\n'
                f'Unknown: http://127.0.0.1:{port}/session?room={rid}&msg=26e81eed-f553-491c-b2ad-f63fe5311680:67')
        lines = [turn('user', 'Start.', 0), turn('assistant', 'The question card balloon.', 1)]
        lines += [turn('user' if i % 2 else 'assistant', f'Filler {i} ' + 'words ' * 30, i + 2) for i in range(2, 30)]
        lines += [turn('assistant', last, 40)]
        # Below the links, so they can scroll clear of the message box.
        lines += [turn('user' if i % 2 else 'assistant', f'After {i} ' + 'words ' * 30, 41 + i) for i in range(6)]
        tdir = base / 'transcripts' / 'C--po'; tdir.mkdir()
        (tdir / f'{sid}.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in lines), encoding='utf-8')
        cls.sid, cls.po, cls.task, cls.port = sid, rid, task['id'], port
        html = home / 'links.html'
        html.write_text(f'<!doctype html><html><head><title>x</title></head><body><p><a id="m" href="http://127.0.0.1:{port}/session?room={rid}&amp;msg={sid}:3">the balloon</a></p></body></html>', encoding='utf-8')
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': f'http://localhost:{port}', 'proj': proj['id'], 'po': rid, 'html': str(html)}
        script = base / 'links.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', errors='replace', timeout=400)
        if r.returncode:
            raise AssertionError(r.stderr[-4000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
        for p in reversed(cls.patches): p.stop()
        cls.tmp.cleanup()

    def assert_in_place(self, o):
        self.assertEqual(o['created'], 0, o)
        self.assertEqual(o['open'], [], o)
        self.assertFalse(o.get('fileview'), o)

    def test_links_are_drawn_as_message_links(self):
        for key in ('card', 'task', 'unknown'):
            link = self.out[key]['link']
            self.assertTrue(link, key)
            self.assertIn('msg-link', link['cls'], key)
            self.assertEqual(link['target'], '', key)
            self.assertTrue(link['href'].startswith('/session?room='), (key, link['href']))
        self.assertIn('card-jump', self.out['card']['link']['cls'])

    def test_card_lands_in_its_thread(self):
        o = self.out['card']
        self.assert_in_place(o)
        self.assertTrue(o['top'].startswith(f'http://localhost:{self.port}/'), o)
        self.assertTrue(any(k.endswith(f' {self.sid}:1') for k in o['landed']), o)

    def test_task_message_lands_in_the_task_chat(self):
        o = self.out['task']
        self.assert_in_place(o)
        self.assertTrue(any(self.task in k and k.endswith(' ' + self.tmid) for k in o['landed']), o)

    def test_balloon_chip_lands(self):
        o = self.out['chip']
        self.assert_in_place(o)
        self.assertTrue(any(k.endswith(f' {self.sid}:3') for k in o['landed']), o)

    def test_unknown_message_opens_no_window(self):
        self.assert_in_place(self.out['unknown'])

    def test_ctrl_click_opens_one_tab_here(self):
        o = self.out['ctrl']
        self.assertEqual(o['created'], 1, o)
        self.assertEqual(len(o['open']), 1, o)
        # At this page's address; on its own, the tab goes on to the card's thread.
        self.assertTrue(o['open'][0].startswith(f'http://localhost:{self.port}/session?'), o)
        self.assertIn(self.po, o['open'][0])
        self.assertIn('ask', o['open'][0])
        self.assertEqual(o['top'], f'http://localhost:{self.port}/', o)

    def test_link_in_an_html_preview_opens_the_chat(self):
        o = self.out['preview']
        self.assertEqual(o['created'], 1, o)
        self.assertEqual(len(o['open']), 1, o)
        self.assertTrue(o['open'][0].startswith(f'http://localhost:{self.port}/session?'), o)
        self.assertIn(self.po, o['open'][0])
        self.assertTrue(o['top'].startswith(f'http://localhost:{self.port}/fileview?'), o)

    def test_standalone_chat_stays_in_its_window(self):
        o = self.out['standalone']
        self.assertEqual(o['created'], 0, o)
        self.assertEqual(o['open'], [], o)
        self.assertTrue(o['top'].startswith(f'http://localhost:{self.port}/session?'), o)
        self.assertEqual(o['landed'], f'{self.sid}:1', o)


if __name__ == '__main__':
    unittest.main()
