"""#433 in Chrome, on an isolated hub: a soft link (a place named in running
text that is linked only since #433) reads as plain text until the hub finds
it. A relative folder the hub finds in the task's folder becomes a link
(file-found) and opens its listing; one it does not find stays plain text that
takes no click; a found soft link opens a tab on a ctrl click like any file
link; a folder whose last name has spaces, drawn with the words after it
("Documents/#433 Plans are ready"), shrinks to the reading the hub finds and
leaves the rest as text. A relative path or a bare name that may start at a
word before it ("My Folder/notes/", "#433 Clickable links.md", "Screen
layout.png") grows to the longest reading the hub has, a picture's thumbnail
with it; one with no longer reading there stays as drawn ("#433 Missing
plan.md": a "plan.md" link, not written yet). A middle click opens a tab too."""
import json
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import file_refs
from tests import chrome_profile
from tests.test_task_numbers import Hub
from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, NODE, CHROME

CDP_JS = TASK_CDP.split('// The chips in the conversation')[0] + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = {};
  try {
    await c.send('Target.setDiscoverTargets', {discover:true});
    const created = [];
    c.on && c.on('Target.targetCreated', p => created.push(p.targetInfo.url));
    const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
    const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
    const send = (m, p) => c.send(m, p, sessionId);
    await send('Page.enable', {});
    await send('Emulation.setDeviceMetricsOverride', {width:1280, height:900, deviceScaleFactor:1, mobile:false});
    const ev = async expression => {
      const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
    };
    const until = async (expr, n=160) => { for (let i=0;i<n;i++) { try { if(await ev(expr)) return; } catch (e) {} await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.querySelector("#msgs") && document.querySelector("#msgs").innerHTML.slice(-3000)')); };
    const links = `[...document.querySelectorAll('#msgs a.file-link')].map(a => ({text:a.textContent, href:a.getAttribute('href'),
      soft:a.classList.contains('file-soft'), found:a.classList.contains('file-found'), alts:a.dataset.alts || '',
      tab:a.getAttribute('tabindex'), events:getComputedStyle(a).pointerEvents, after:a.nextSibling ? a.nextSibling.textContent : ''}))`;
    await send('Page.navigate', {url:A.base+'/session?room='+A.room});
    await until(`(() => { const l = [...document.querySelectorAll('#msgs a.file-link')];
      return l.some(a => a.textContent === 'notes/' && a.classList.contains('file-found'))
        && l.some(a => a.textContent === 'Documents/#433 Plans' && a.classList.contains('file-found'))
        && l.some(a => a.textContent === 'My Folder/notes/' && a.classList.contains('file-found'))
        && l.some(a => a.textContent === '#433 Clickable links.md')
        && l.some(a => a.textContent === 'Screen layout.png')
        && l.some(a => a.textContent === 'plan.md' && a.classList.contains('file-missing')); })()`);
    await sleep(600);      // a link still waiting for the hub would change in this time
    out.links = await ev(links);
    out.text = await ev(`document.querySelector('#msgs').innerText`);
    // A ctrl click on the found soft link opens a tab, as on any file link.
    const at = await ev(`(() => { const a = [...document.querySelectorAll('#msgs a.file-link')].find(a => a.textContent === 'notes/');
      a.scrollIntoView({block:'center'}); const r = a.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', {type, x:at[0], y:at[1], button:'left', clickCount:1, modifiers:2});
    let tabs = [];
    for (let i = 0; i < 40 && !tabs.length; i++) {
      await sleep(150);
      tabs = (await c.send('Target.getTargets', {})).targetInfos.filter(t => t.type === 'page' && t.url.includes('/fileview?')).map(t => t.url);
    }
    out.tabs = tabs;
    // A middle click on a found link opens a tab too.
    const mid = await ev(`(() => { const a = [...document.querySelectorAll('#msgs a.file-link')].find(a => a.textContent === 'My Folder/notes/');
      a.scrollIntoView({block:'center'}); const r = a.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased'])
      await send('Input.dispatchMouseEvent', {type, x:mid[0], y:mid[1], button:'middle', buttons:4, clickCount:1});
    let midTabs = [];
    for (let i = 0; i < 40 && !midTabs.length; i++) {
      await sleep(150);
      midTabs = (await c.send('Target.getTargets', {})).targetInfos.filter(t => t.type === 'page' && t.url.includes('/fileview?') && t.url.includes('My%20Folder')).map(t => t.url);
    }
    out.midTabs = midTabs;
    out.stayed = await ev(`location.pathname`);
    // The found folder opens its listing.
    const notes = out.links.find(l => l.text === 'notes/').href;
    await send('Page.navigate', {url:A.base+notes});
    await until(`document.body.innerText.includes('here.md')`);
    out.listing = await ev(`document.body.innerText`);
    // The board page draws a picture under its thumbnail (.path-img): the thumbnail grows with the link.
    await send('Page.navigate', {url:A.base+'/'});
    await until(`typeof mdIn === 'function' && typeof FileLinks === 'object'`);
    await ev(`(() => { const d = document.createElement('div'); d.id = 't433';
      d.innerHTML = mdIn({headless:true, roomId:${JSON.stringify(A.room)}, cwd:${JSON.stringify(A.cwd)}}, 'Look at Screen layout.png here.');
      document.body.appendChild(d); FileLinks.scan(d); })()`);
    await until(`(() => { const t = document.querySelector('#t433 .path-img a.att-thumb img');
      return t && t.naturalWidth > 0 && document.querySelector('#t433 a.file-link').textContent === 'Screen layout.png'; })()`);
    out.thumbs = await ev(`[...document.querySelectorAll('#t433 .path-img a.att-thumb')].map(t => ({href:t.getAttribute('href'),
      title:t.title, loaded:!!(t.querySelector('img') || {}).naturalWidth}))`);
    out.board = await ev(`document.querySelector('#t433').textContent`);
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class SoftLinksInChrome(Hub):
    def test_found_plain_ctrl_click_and_shorter(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        file_refs.forget_indexes(); self.addCleanup(file_refs.forget_indexes)
        work = Path(dashboard.find_project(self.ed)['path']) / 'task-folder'
        (work / 'notes').mkdir(parents=True)
        (work / 'notes' / 'here.md').write_text('here', encoding='utf-8')
        (work / 'Documents' / '#433 Plans').mkdir(parents=True)
        (work / 'My Folder' / 'notes').mkdir(parents=True)
        (work / '#433 Clickable links.md').write_text('# links', encoding='utf-8')
        # a 1x1 PNG
        (work / 'Screen layout.png').write_bytes(bytes.fromhex(
            '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489'
            '0000000d49444154789c6360f8cf000000020101007bd9e1a70000000049454e44ae426082'))
        rid = self.room('Task chat', self.ed)
        full = chatroom.get_room(rid, public=False)
        full['cwd'] = str(work)
        chatroom.update_room(full)
        chatroom.post_message(rid, 'claude', 'Open notes/ first. Documents/#433 Plans are ready. '
                              'Then nothing/here/ is empty. See My Folder/notes/ now. '
                              'Read #433 Clickable links.md and #433 Missing plan.md. Look at Screen layout.png here.', to='user')
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port), 'room': rid, 'cwd': str(work)}
        script = base / 'softlinks_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        by = {l['text']: l for l in got['links']}
        self.assertEqual(list(by), ['notes/', 'Documents/#433 Plans', 'nothing/here/', 'My Folder/notes/',
                                    '#433 Clickable links.md', 'plan.md', 'Screen layout.png'], got['links'])
        # found: a link that takes clicks and the keyboard
        for name in ('notes/', 'Documents/#433 Plans'):
            self.assertTrue(by[name]['soft'] and by[name]['found'], by[name])
            self.assertEqual(by[name]['events'], 'auto', by[name])
            self.assertIsNone(by[name]['tab'], by[name])
        # not found: plain text, out of the tab order, takes no click
        self.assertFalse(by['nothing/here/']['found'])
        self.assertEqual(by['nothing/here/']['events'], 'none')
        self.assertEqual(by['nothing/here/']['tab'], '-1')
        # the spaced folder shrank to its found reading; its words stay in the sentence
        self.assertEqual(by['Documents/#433 Plans']['alts'], '')
        self.assertTrue(by['Documents/#433 Plans']['after'].startswith(' are'), by['Documents/#433 Plans'])
        self.assertIn('Documents/#433 Plans are ready.', got['text'])
        # grown to the longest reading the hub has: the words it took are the link's, not the sentence's twice
        self.assertIn('Then nothing/here/ is empty. See My Folder/notes/ now. '
                      'Read #433 Clickable links.md and #433 Missing plan.md. Look at Screen layout.png', got['text'])
        for name in ('My Folder/notes/', '#433 Clickable links.md', 'Screen layout.png'):
            self.assertEqual(by[name]['alts'], '', by[name])
            self.assertFalse(by[name]['soft'] and not by[name]['found'], by[name])
        self.assertIn('path=My%20Folder%2Fnotes%2F', by['My Folder/notes/']['href'])
        self.assertIn('path=%23433%20Clickable%20links.md', by['#433 Clickable links.md']['href'])
        # no longer reading there: the link as drawn, "not written yet"
        self.assertIn('path=plan.md', by['plan.md']['href'])
        # the picture's thumbnail shows the picture the link now names
        self.assertEqual(len(got['thumbs']), 1, got['thumbs'])
        self.assertEqual(got['thumbs'][0]['title'], 'Screen layout.png')
        self.assertIn('Screen%20layout.png', got['thumbs'][0]['href'])
        self.assertTrue(got['thumbs'][0]['loaded'], got['thumbs'])
        self.assertEqual(got['board'].strip(), 'Look at Screen layout.png here.')
        # a ctrl click opens a tab; the chat stays
        self.assertTrue(any('path=notes' in u for u in got['tabs']), got['tabs'])
        self.assertTrue(got['midTabs'], 'a middle click opens a tab')
        self.assertEqual(got['stayed'], '/session')
        self.assertIn('here.md', got['listing'])


if __name__ == '__main__': unittest.main()
