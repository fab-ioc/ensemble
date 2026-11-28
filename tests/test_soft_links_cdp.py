"""#433 in Chrome, on an isolated hub: a soft link (a place named in running
text that is linked only since #433) reads as plain text until the hub finds
it. A relative folder the hub finds in the task's folder becomes a link
(file-found) and opens its listing; one it does not find stays plain text that
takes no click; a found soft link opens a tab on a ctrl click like any file
link; a folder whose last name has spaces, drawn with the words after it
("Documents/#433 Plans are ready"), shrinks to the reading the hub finds and
leaves the rest as text."""
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
        && l.some(a => a.textContent === 'Documents/#433 Plans' && a.classList.contains('file-found')); })()`);
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
    out.stayed = await ev(`location.pathname`);
    // The found folder opens its listing.
    const notes = out.links.find(l => l.text === 'notes/').href;
    await send('Page.navigate', {url:A.base+notes});
    await until(`document.body.innerText.includes('here.md')`);
    out.listing = await ev(`document.body.innerText`);
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
        rid = self.room('Task chat', self.ed)
        full = chatroom.get_room(rid, public=False)
        full['cwd'] = str(work)
        chatroom.update_room(full)
        chatroom.post_message(rid, 'claude', 'Open notes/ first. Documents/#433 Plans are ready. '
                              'Then nothing/here/ is empty.', to='user')
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port), 'room': rid}
        script = base / 'softlinks_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        by = {l['text']: l for l in got['links']}
        self.assertEqual(list(by), ['notes/', 'Documents/#433 Plans', 'nothing/here/'], got['links'])
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
        self.assertEqual(by['Documents/#433 Plans']['alts'], '[["Documents/#433",14]]')
        self.assertTrue(by['Documents/#433 Plans']['after'].startswith(' are'), by['Documents/#433 Plans'])
        self.assertIn('Documents/#433 Plans are ready.', got['text'])
        # a ctrl click opens a tab; the chat stays
        self.assertTrue(any('path=notes' in u for u in got['tabs']), got['tabs'])
        self.assertEqual(got['stayed'], '/session')
        self.assertIn('here.md', got['listing'])


if __name__ == '__main__': unittest.main()
