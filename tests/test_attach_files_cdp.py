"""Any file goes with a message, in a real browser (#318): in a PO chat at
1728x1117 and on a phone at 390x844, a PDF and a CSV pasted into the box (a
synthetic DataTransfer), dropped on it, or chosen with the picker (the Attach
button, on a phone the + menu's "Attach a file") show as chips with their
kind, name and size; Send delivers a message ending with a "[file] <path>"
line each, and the balloon shows them as file chips with their size."""
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
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0',
    '--user-data-dir=' + chromeProfile(A), '--no-first-run', '--disable-gpu', 'about:blank'],
    {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(), chooser = []; let seq=0;
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  c.onmessage = ev => { const m=JSON.parse(ev.data);
    if (m.method === 'Page.fileChooserOpened') { chooser.push(m.params); return; }
    const w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  await send('Page.setInterceptFileChooserDialog',{enabled:true},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<120;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);} throw Error('timeout '+label); };
  const snap = async name => { if (!A.shots) return; const r=await send('Page.captureScreenshot',{format:'png'},s);
    require('fs').writeFileSync(A.shots+'/'+name+'.png',Buffer.from(r.data,'base64')); };
  const FILES = `(() => { const dt = new DataTransfer();
    dt.items.add(new File(['%PDF-1.7 ' + 'x'.repeat(3000)], 'Quarterly report.pdf', {type:'application/pdf'}));
    dt.items.add(new File(['a,b;1,2;'], 'rows.csv', {type:'text/csv'}));
    return dt; })()`;
  const chips = `[...document.querySelectorAll('#att-chips .att-chip')].map(x => ({cls:x.className, ico:x.querySelector('.att-ico')?.textContent||'',
    name:x.querySelector('.att-name')?.textContent||'', size:x.querySelector('.att-size')?.textContent||''}))`;
  const boxHeight = `Math.round(document.getElementById('input').getBoundingClientRect().height)`;
  const sendIt = async (words, label) => {
    await ev(`(()=>{const i=document.getElementById('input');i.value=${JSON.stringify(words)};i.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('send').click();return 0})()`);
    await until(`!document.getElementById('input').value && !document.querySelector('#att-chips .att-chip')`, 'sent ' + label);
  };
  const out = {};
  try {
    for (const [w, h] of [[1728, 1117], [390, 844]]) {
      const tag = w + 'x' + h, phone = w < 720, r = out[tag] = {};
      await send('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:phone},s);
      await send('Emulation.setTouchEmulationEnabled',phone?{enabled:true,maxTouchPoints:5}:{enabled:false},s);
      await send('Page.navigate',{url:A.base+'/session?id='+A.po},s);
      await until(`typeof ATT!=='undefined' && !!document.getElementById('input') && document.getElementById('send')`,'page ' + tag);
      await sleep(400);
      r.heightBefore = await ev(boxHeight);
      // ---- paste
      r.pastePrevented = await ev(`(()=>{const e=new ClipboardEvent('paste',{clipboardData:${FILES},bubbles:true,cancelable:true});
        document.getElementById('input').dispatchEvent(e);return e.defaultPrevented})()`);
      await until(`ATT.list.length===2 && !ATT.busy()`, 'paste upload ' + tag);
      r.pasteChips = await ev(chips);
      r.heightWith = await ev(boxHeight);
      await snap('paste-' + tag);
      await sendIt('paste ' + tag, 'paste ' + tag);
      // ---- drop
      await ev(`(()=>{const dt=${FILES};const z=document.getElementById('compose');
        z.dispatchEvent(new DragEvent('dragover',{dataTransfer:dt,bubbles:true,cancelable:true}));
        z.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));return 0})()`);
      await until(`ATT.list.length===2 && !ATT.busy()`, 'drop upload ' + tag);
      r.dropChips = await ev(chips);
      await sendIt('drop ' + tag, 'drop ' + tag);
      // ---- the picker: the Attach button, on a phone the + menu's item
      chooser.length = 0;
      if (phone) await ev(`(()=>{const d=document.querySelector('.phone-compose-tools');d.open=true;const b=d.querySelector('[data-phone-action="attach"]');return b.textContent})()`).then(t => { r.phoneLabel = t; });
      await ev(phone ? `document.querySelector('[data-phone-action="attach"]').click(),0` : `document.getElementById('attach').click(),0`);
      for (let i = 0; i < 40 && !chooser.length; i++) await sleep(100);
      r.chooser = chooser.length ? {multiple: chooser[0].mode === 'selectMultiple'} : null;
      if (chooser.length) await send('DOM.setFileInputFiles',{files:[A.pdf, A.csv], backendNodeId:chooser[0].backendNodeId},s);
      await until(`ATT.list.length===2 && !ATT.busy()`, 'picker upload ' + tag);
      r.pickChips = await ev(chips);
      await sendIt('pick ' + tag, 'pick ' + tag);
      // ---- the balloon of the last message: file chips with their size
      await ev(`refresh(),0`);
      await until(`[...document.querySelectorAll('#msgs .att-file')].length>=2 && [...document.querySelectorAll('#msgs .att-file .att-size')].every(x=>x.textContent)`, 'balloon ' + tag);
      r.balloon = await ev(`[...document.querySelectorAll('#msgs .att-file')].slice(-2).map(a=>({name:a.querySelector('.att-name').textContent,
        size:a.querySelector('.att-size').textContent, href:a.getAttribute('href'), download:a.hasAttribute('download'), target:a.getAttribute('target')||''}))`);
      await snap('balloon-' + tag);
    }
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class AttachFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-attach-files-', ignore_cleanup_errors=True)
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
            mock.patch.object(dashboard.Handler, '_agent_peer', lambda h: ''),
            mock.patch.object(dashboard.Handler, 'log_message', lambda *a, **k: None),
            mock.patch.dict(os.environ, {'CODEX_HOME': str(base / 'codex')}),
        ]
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project('Motors'); assert ok, proj
        home = Path(proj.get('home') or proj['path'])
        sid = 'attach-po'
        po = chatroom.create_room('PO talk', [{'identity': 'claude', 'agent': 'claude', 'role': 'Product owner', 'sessionId': sid, 'cwd': str(home)}])
        room = chatroom.get_room(po['id'], public=False); room['mode'] = 'solo'; chatroom.update_room(room)
        dashboard.assign_session_project(po['id'], proj['id'])
        dashboard.set_project_po(proj['id'], po['id']); cls.po = po['id']
        transcript = base / 'transcripts' / 'C--attach'; transcript.mkdir()
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 600)) + '.000Z'
        first = [{'type': 'user', 'timestamp': stamp, 'sessionId': sid, 'message': {'role': 'user', 'content': 'Hello.'}},
                 {'type': 'assistant', 'timestamp': stamp, 'sessionId': sid,
                  'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Hi.'}], 'stop_reason': 'end_turn'}}]
        path = transcript / f'{sid}.jsonl'
        path.write_text(''.join(json.dumps(x) + '\n' for x in first), encoding='utf-8')
        cls.sent = []
        lock = threading.Lock()

        def resume(h, room, text='', to='', key='', quiet=False, **kwargs):
            dashboard._typed_sends(room['id'], [{'key': key, 'text': text}])
            cls.sent.append(text)
            item = {'type': 'user', 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + '.000Z',
                    'sessionId': sid, 'message': {'role': 'user', 'content': text}}
            with lock, path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(item) + '\n')
            return {'delivered': 1}
        patch = mock.patch.object(dashboard.Handler, '_resume_room', resume)
        patch.start(); cls.addClassCleanup(patch.stop)
        pick = base / 'pick'; pick.mkdir()
        (pick / 'Picked plan.pdf').write_bytes(b'%PDF-1.7\n' + b'y' * 5000)
        (pick / 'picked.csv').write_bytes(b'x,y\n3,4\n')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}', 'po': cls.po,
                'pdf': str(pick / 'Picked plan.pdf'), 'csv': str(pick / 'picked.csv'),
                'shots': os.environ.get('ATTACH_SCREENSHOT_DIR', '')}
        script = base / 'attach_files_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=180)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_paste_drop_and_pick_send_files(self):
        pasted = [{'cls': 'att-chip file', 'ico': 'PDF', 'name': 'Quarterly report.pdf', 'size': '3 KB'},
                  {'cls': 'att-chip file', 'ico': 'CSV', 'name': 'rows.csv', 'size': '8 B'}]
        for tag in ('1728x1117', '390x844'):
            with self.subTest(tag=tag):
                r = self.got[tag]
                self.assertTrue(r['pastePrevented'])
                # The phone pass uses the same chat: its copies are numbered.
                num = (lambda n: n) if tag == '1728x1117' else (lambda n: n.replace('.', '-3.', 1))
                num2 = (lambda n: n) if tag == '1728x1117' else (lambda n: n.replace('.', '-2.', 1))
                self.assertEqual(r['pasteChips'], [dict(x, name=num(x['name'])) for x in pasted])
                # The same files again: stored beside the first under numbered names.
                self.assertEqual([x['ico'] for x in r['dropChips']], ['PDF', 'CSV'])
                self.assertTrue(r['dropChips'][0]['name'].startswith('Quarterly report') and r['dropChips'][0]['name'].endswith('.pdf'), r['dropChips'])
                self.assertNotEqual(r['dropChips'][0]['name'], 'Quarterly report.pdf')
                self.assertEqual(r['chooser'], {'multiple': True})
                self.assertEqual([(x['ico'], x['name'], x['size']) for x in r['pickChips']],
                                 [('PDF', num2('Picked plan.pdf'), '5 KB'), ('CSV', num2('picked.csv'), '8 B')])
                # The one-row box keeps its height with chips above it.
                self.assertEqual(r['heightBefore'], r['heightWith'])
                names = [b['name'] for b in r['balloon']]
                self.assertEqual(names, [num2('Picked plan.pdf'), num2('picked.csv')])
                self.assertTrue(all(b['size'] for b in r['balloon']))
                pdf, csv = r['balloon']
                self.assertTrue(pdf['href'].startswith('/fileview?path='), pdf)
                self.assertEqual(pdf['target'], '_blank')
                self.assertTrue(csv['href'].startswith('/fileview?path='), csv)
        self.assertEqual(self.got['390x844']['phoneLabel'], 'Attach a file')
        # What the agent is given: the words, then a "[file] <path>" line per file.
        sent = [t for t in self.sent if t.startswith(('paste ', 'drop ', 'pick '))]
        self.assertEqual(len(sent), 6, self.sent)
        for t in sent:
            lines = t.split('\n')
            files = [l for l in lines if l.startswith('[file] ')]
            self.assertEqual(len(files), 2, t)
            for l in files:
                self.assertTrue(Path(l[len('[file] '):]).is_file(), l)
            self.assertFalse(any(l.startswith('[image] ') for l in lines), t)


if __name__ == '__main__':
    unittest.main()
