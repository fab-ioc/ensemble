"""#199 in Chrome, on an isolated hub, at 1728x1117 and 390x844: a balloon's
link to a file not written yet carries the "not written yet" chip (P162), the
chip goes once the file is there, a missing file opens a panel that lists the
files of that name, and a link to a file that moved opens it with a note."""
import json
import os
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
    for (const [width, height] of [[1728, 1117], [390, 844]]) {
      const mob = width === 390, row = out[width] = {};
      const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
      const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
      const send = (m, p) => c.send(m, p, sessionId);
      await send('Page.enable', {});
      await send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor:1, mobile:mob});
      const ev = async expression => {
        const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
      };
      const until = async (expr, n=160) => { for (let i=0;i<n;i++) { try { if(await ev(expr)) return; } catch (e) {} await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.body.innerText.slice(-1600)')); };
      const shot = async name => {if(A.shots) { const r=await send('Page.captureScreenshot',{format:'png'}); fs.writeFileSync(path.join(A.shots,`filelinks-${width}-${name}.png`),Buffer.from(r.data,'base64')); }};
      const links = `[...document.querySelectorAll('#msgs a.file-link')].map(a => {const r=a.getBoundingClientRect(); return {
        text:a.textContent, href:a.getAttribute('href'), missing:a.classList.contains('file-missing'), title:a.title,
        chip:getComputedStyle(a,'::after').content, right:r.right};})`;
      await send('Page.navigate', {url:A.base+'/session?room='+A.room});
      await until(`document.querySelectorAll('#msgs a.file-link').length >= 4 && document.querySelectorAll('#msgs a.file-link.file-missing').length >= ${mob ? 2 : 3}`);
      await sleep(300);
      row.balloon = await ev(links);
      row.overflow = await ev('document.documentElement.scrollWidth > innerWidth');
      await shot('balloon');
      if (!mob) {
        // The task writes the file: the link loses its chip without a reload.
        fs.mkdirSync(path.dirname(A.later), {recursive:true}); fs.writeFileSync(A.later, '# Later\n\nwritten by the task\n');
        await until(`(FileLinks.recheck(), ![...document.querySelectorAll('#msgs a.file-link')].find(a => a.textContent.includes('Later')).classList.contains('file-missing'))`, 120);
        row.written = await ev(links);
        await shot('written');
        // and it opens the file
        const later = row.written.find(l => l.text.includes('Later')).href;
        await send('Page.navigate', {url:A.base+later});
        await until(`document.body.innerText.includes('written by the task') && !document.querySelector('.missing')`);
        row.opened = await ev('document.body.innerText');
        await shot('opened');
      }
      const hrefOf = word => row.balloon.find(l => l.text.includes(word)).href;
      // A missing file: the panel, with the files of that name.
      await send('Page.navigate', {url:A.base+hrefOf('report.md')});
      await until(`!!document.querySelector('.missing .dir-row')`);
      row.panel = await ev(`(() => {const m=document.querySelector('.missing'),r=m.getBoundingClientRect(); return {head:m.querySelector('.missing-h').textContent,
        rows:[...m.querySelectorAll('.dir-row')].map(a => a.title), again:!!m.querySelector('#miss-again'), right:r.right,
        overflow:document.documentElement.scrollWidth > innerWidth};})()`);
      await shot('panel');
      // A file that moved: the one file of that name opens, with a note.
      await send('Page.navigate', {url:A.base+hrefOf('moved.md')});
      await until(`!!document.getElementById('moved-note') && location.search.includes('moved=')`);
      row.moved = await ev(`({note:document.getElementById('moved-note').textContent, path:new URLSearchParams(location.search).get('path'),
        text:document.body.innerText.includes('moved here')})`);
      await shot('moved');
      await c.send('Target.closeTarget',{targetId});
    }
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class FileLinksInChrome(Hub):
    def test_chip_written_panel_and_moved(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        file_refs.forget_indexes(); self.addCleanup(file_refs.forget_indexes)
        work = Path(dashboard.find_project(self.ed)['path']) / 'task-folder'   # a task's folder in the project
        for rel, text in [('notes/here.md', 'here'), ('archive/report.md', 'a'), ('old/report.md', 'b'),
                          ('archive/moved.md', 'moved here')]:
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text, encoding='utf-8')
        rid = self.room('Task chat', self.ed)
        full = chatroom.get_room(rid, public=False)
        full['cwd'] = str(work)
        chatroom.update_room(full)
        chatroom.post_message(rid, 'claude', 'Wrote `notes/here.md`. The report will be `Documents\\#5 Later cover notes.md`, '
                              'see `docs/report.md` and `x/moved.md`.', to='user')
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        later = work / 'Documents' / '#5 Later cover notes.md'
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port),
                'room': rid, 'later': str(later), 'shots': shots}
        script = base / 'filelinks_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        for width in ('1728', '390'):
            row = got[width]
            with self.subTest(width=width):
                missing = {l['text']: l['missing'] for l in row['balloon']}
                # the desktop run writes the report: the phone sees it there
                self.assertEqual(missing, {'notes/here.md': False, 'Documents\\#5 Later cover notes.md': width == '1728',
                                           'docs/report.md': True, 'x/moved.md': True})
                for l in row['balloon']:
                    self.assertEqual(l['chip'], '"not written yet"' if l['missing'] else 'none', l)
                    self.assertEqual(l['title'] == 'This file does not exist yet', l['missing'], l)
                    self.assertLessEqual(l['right'], int(width))
                self.assertFalse(row['overflow'])
                self.assertEqual(row['panel']['head'], 'report.md does not exist (yet)')
                self.assertEqual(sorted(row['panel']['rows']), sorted([str(work / 'archive' / 'report.md'),
                                                                     str(work / 'old' / 'report.md')]))
                self.assertTrue(row['panel']['again'])
                self.assertFalse(row['panel']['overflow'])
                self.assertLessEqual(row['panel']['right'], int(width))
                self.assertEqual(Path(row['moved']['path']), work / 'archive' / 'moved.md')
                self.assertIn('x/moved.md', row['moved']['note'])
                self.assertTrue(row['moved']['text'])
        written = {l['text']: l['missing'] for l in got['1728']['written']}
        self.assertFalse(written['Documents\\#5 Later cover notes.md'])
        self.assertIn('written by the task', got['1728']['opened'])


class OnlyWhatTheHubMayRead(Hub):
    """The cwd a page sends is the client's word: no folder outside the ones
    the hub may read is walked for a name. What is written is answered the
    way /api/file answers it (it opens such a file), so a file there is
    never drawn as not written yet."""

    def test_outside_is_not_walked(self):
        file_refs.forget_indexes(); self.addCleanup(file_refs.forget_indexes)
        outside = Path(self.tmp.name) / 'outside'
        inside = Path(dashboard.find_project(self.ed)['path']) / 'docs'
        for d in (outside, inside):
            d.mkdir(parents=True)
            (d / 'secret.md').write_text('x', encoding='utf-8')
        q = {'ctx': [['', str(outside)], ['', str(inside)]],
             'items': [['secret.md', 0], [str(outside / 'secret.md'), 0], ['x/secret.md', 0],
                       ['secret.md', 1], ['docs/secret.md', 1], ['nope.md', 1]]}
        self.assertEqual(dashboard.files_check(q), {'there': [True, True, False, True, True, False]})
        self.assertEqual(dashboard.file_ref_suggestions('a/secret.md', cwd=str(outside))['same'], [])
        self.assertEqual(dashboard.file_ref_suggestions('a/secret.md', cwd=str(inside))['same'], [str(inside / 'secret.md')])
        self.assertIsNone(dashboard.resolve_file_ref('x/secret.md', cwd=str(outside)))


if __name__ == '__main__': unittest.main()
