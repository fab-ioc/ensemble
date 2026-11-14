"""#366: the project-list square and PO labels agree in a real framed chat.

Three throwaway projects supply two differently coloured squares and one icon.
The light screenshot is assembled from the two live project views when
ENSEMBLE_PO_COLOUR_SHOT names a destination. Skipped without Node or Chrome.
"""
from __future__ import annotations

import base64
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NODE = shutil.which('node')
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><rect width="48" height="48" fill="#8367BB"/></svg>'


def rgb(color: str) -> tuple[int, int, int]:
    if color.startswith('#'):
        value = color[1:]
        if len(value) == 3:
            value = ''.join(c * 2 for c in value)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(map(int, re.findall(r'\d+', color)[:3]))


def contrast(a: str, b: str) -> float:
    def luminance(color: str) -> float:
        channels = [v / 255 for v in rgb(color)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(x * y for x, y in zip(linear, (.2126, .7152, .0722)))
    x, y = luminance(a), luminance(b)
    return (max(x, y) + .05) / (min(x, y) + .05)

CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0',
    '--user-data-dir=' + chromeProfile(A), '--no-first-run', '--disable-gpu',
    '--window-size=1728,1117', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  c.onmessage = ev => { const m=JSON.parse(ev.data), w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s);
  await send('Emulation.setDeviceMetricsOverride',{width:1728,height:1117,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails).slice(0,500)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<150;i++){try{if(await ev(expression))return;}catch(e){} await sleep(150);}
    const state=await ev(`(() => { const f=document.querySelector('#po-panel iframe.po-session:not([hidden])'); return {
      selected:SELECTED_PROJECT, head:document.querySelector('#po-panel .po-head')?.dataset.proj,
      frame:f?.dataset.room, loaded:!!f?.contentDocument, room:f?.contentWindow?.ROOM_OBJ?.projectId,
      colours:!!f?.contentWindow?.PO_PROJECTS, poLabels:f?.contentDocument?.querySelectorAll('#msgs .rl.po').length,
      msg:(f?.contentDocument?.querySelector('#msgs')?.textContent||'').slice(0,200),
      err:f?.contentDocument?.querySelector('#hint')?.textContent||'',
      href:f?.contentWindow?.location.href, ready:f?.contentDocument?.readyState,
      title:f?.contentDocument?.title, body:(f?.contentDocument?.body?.innerText||'').slice(0,250),
      scripts:[...(f?.contentDocument?.querySelectorAll('script[src]')||[])].map(x=>x.src).filter(x=>x.includes('po-colour'))}; })()`);
    throw Error('timeout '+label+' '+JSON.stringify(state)); };
  const PO = `document.querySelector('#po-panel iframe.po-session:not([hidden])')`;
  const inspect = `(() => {
    const pid=SELECTED_PROJECT, f=${PO}, d=f?.contentDocument, head=document.querySelector('#po-panel .po-head .rl.po');
    const balloon=d?.querySelector('#msgs .msg .from .rl.po');
    const square=document.querySelector('#ld-projs [data-ldp-proj="'+pid+'"] .ldp-ico');
    const value=e=>e?{fill:getComputedStyle(e).backgroundColor,ink:getComputedStyle(e).color,text:e.textContent.trim(),token:e.dataset.poFill||''}:null;
    const swatch=square?.querySelector('rect'), listToken=ldpColour(pid), kind=listToken.match(/--c-(\\w+)-bold/)?.[1];
    const vars=getComputedStyle(document.documentElement);
    return {pid, head:value(head), balloon:value(balloon), swatch:swatch?getComputedStyle(swatch).fill:null,
      listToken, bold:kind?vars.getPropertyValue('--c-'+kind+'-bold').trim():null,
      fg:kind?vars.getPropertyValue('--c-'+kind+'-fg').trim():null,
      violet:vars.getPropertyValue('--c-discovery-fg').trim(),
      icon:!!square?.querySelector('img'), own:d?.defaultView?.eval('ROOM_OBJ && ROOM_OBJ.projectId')||'',
      frame:f?.dataset.room||'', projects:(PROJECTS?.projects||[]).map(p=>({id:p.id,name:p.name,registered:p.registered,icon:!!p.icon?.v})),
      listVisible:!!square&&square.getBoundingClientRect().width>0,
      headerVisible:!!head&&head.getBoundingClientRect().width>0,
      balloonVisible:!!balloon&&balloon.getBoundingClientRect().width>0};
  })()`;
  const ratios = `(() => {
    const f=${PO}, d=f?.contentDocument, els=[document.querySelector('#po-panel .po-head .rl.po'),d?.querySelector('#msgs .msg .from .rl.po')];
    const lum=c=>{const m=c.match(/[\\d.]+/g).map(Number),g=v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4};return .2126*g(m[0])+.7152*g(m[1])+.0722*g(m[2])};
    const ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
    return els.map(e=>e?ratio(getComputedStyle(e).color,getComputedStyle(e).backgroundColor):0);
  })()`;
  try {
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof PROJECTS!=='undefined' && !!PROJECTS && PROJECTS.projects.length===3 &&
      typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.rooms[0])})`,'projects and rooms');
    const out={light:[],dark:[],allThemes:{}};
    for (let i=0;i<3;i++) {
      const pid=A.ids[i], room=A.rooms[i];
      await ev(`SELECTED_PROJECT=${JSON.stringify(pid)};PROJECT_TAB='tasks';renderRows();0`);
      await until(`document.querySelector('#po-panel .po-head')?.dataset.proj===${JSON.stringify(pid)} &&
        ${PO}?.dataset.room===${JSON.stringify(room)} && !!${PO}?.contentDocument?.querySelector('#msgs .msg .from .rl.po') &&
        !!${PO}?.contentWindow?.eval('PO_PROJECTS')`,'PO header and balloon '+i);
      await sleep(150);
      out.light.push(await ev(inspect));
      out.light[i].ratios=await ev(ratios);
      if (A.shots && i<2) {
        const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},s);
        fs.writeFileSync(path.join(A.shots, 'po-colour-'+i+'.png'),Buffer.from(shot.data,'base64'));
      }
    }
    for (const theme of ['dark','dim','paper','contrast','fjord','intellij-dark']) {
      await ev(`localStorage.setItem('cd-theme',${JSON.stringify(theme)});cdTheme.apply();
        document.querySelectorAll('iframe.po-session').forEach(f=>f.contentWindow?.cdTheme?.apply());0`);
      const per=[];
      for (let i=0;i<2;i++) {
        const pid=A.ids[i], room=A.rooms[i];
        await ev(`SELECTED_PROJECT=${JSON.stringify(pid)};PROJECT_TAB='tasks';renderRows();0`);
        await until(`document.querySelector('#po-panel .po-head')?.dataset.proj===${JSON.stringify(pid)} && ${PO}?.dataset.room===${JSON.stringify(room)}`,'theme '+theme+' project '+i);
        await sleep(70);
        per.push({view:await ev(inspect),ratios:await ev(ratios)});
      }
      out.allThemes[theme]=per;
    }
    out.dark=out.allThemes.dark;
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class PoColourCdp(unittest.TestCase):
    def check_coloured_view(self, view: dict, ratios: list[float]):
        bold_reads = max(contrast(view['bold'], '#ffffff'), contrast(view['bold'], '#1D2125')) >= 4.5
        expected_token = view['listToken'] if bold_reads else view['listToken'].replace('-bold', '-fg')
        expected_fill = view['bold'] if bold_reads else view['fg']
        self.assertEqual(view['head']['token'], expected_token, view)
        self.assertEqual(rgb(view['head']['fill']), rgb(expected_fill), view)
        self.assertEqual(view['head']['fill'], view['balloon']['fill'], view)
        self.assertEqual(rgb(view['swatch']), rgb(view['bold']), view)
        self.assertTrue(all(r >= 4.5 for r in ratios), (ratios, view))

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-po-colour-', ignore_cleanup_errors=True)
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
        ids, rooms = [], []
        for i, name in enumerate(('Orion', 'Lyra', 'Icon Studio')):
            ok, proj, _ = dashboard.register_project(name); assert ok, proj
            pid = proj['id']; ids.append(pid)
            sid = f'po-colour-{i}'
            room = chatroom.create_room('PO talk', [{'identity':'claude','agent':'claude','role':'Product owner',
                'sessionId':sid,'cwd':str(proj.get('home') or proj['path'])}])
            full = chatroom.get_room(room['id'], public=False); full['mode'] = 'solo'; chatroom.update_room(full)
            dashboard.assign_session_project(room['id'], pid)
            ok, _ = dashboard.set_project_po(pid, room['id']); assert ok
            transcript = base / 'transcripts' / f'C--po-colour-{i}'
            transcript.mkdir()
            at = time.time() - 3600
            def turn(role: str, text: str, offset: int):
                stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at + offset)) + '.000Z'
                msg = {'role':role,'content':text if role == 'user' else [{'type':'text','text':text}]}
                if role == 'assistant': msg['stop_reason'] = 'end_turn'
                return {'type':role,'timestamp':stamp,'sessionId':sid,'message':msg}
            lines = [turn('assistant', f'{name} is ready for review.', 2)]
            (transcript / f'{sid}.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in lines), encoding='utf-8')
            rooms.append(room['id'])
        svg = 'data:image/svg+xml;base64,' + base64.b64encode(SVG.encode()).decode()
        ok, _, icon = dashboard.set_project_icon(ids[2], svg, 'icon.svg'); assert ok and icon
        cls.ids, cls.rooms = ids, rooms
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp':cls.tmp.name,
                'base':f'http://127.0.0.1:{cls.server.server_address[1]}',
                'ids':ids,'rooms':rooms,'shots':cls.tmp.name if os.environ.get('ENSEMBLE_PO_COLOUR_SHOT') else ''}
        script = base / 'po_colour_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE,str(script),json.dumps(args)],capture_output=True,encoding='utf-8',timeout=180)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        if os.environ.get('ENSEMBLE_PO_COLOUR_SHOT'):
            from PIL import Image
            left, right = (Image.open(base / f'po-colour-{i}.png').convert('RGB') for i in range(2))
            # Keep the list, header and balloon without the empty lower chat area.
            width = min(850, left.width, right.width)
            height = min(450, left.height, right.height)
            out = Image.new('RGB', (width * 2, height), '#ffffff')
            out.paste(left.crop((0, 0, width, out.height)), (0, 0))
            out.paste(right.crop((0, 0, width, out.height)), (width, 0))
            dest = Path(os.environ['ENSEMBLE_PO_COLOUR_SHOT'])
            dest.parent.mkdir(parents=True, exist_ok=True)
            out.save(dest)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_square_header_and_balloon_agree(self):
        first, second, icon = self.got['light']
        self.assertNotEqual(first['swatch'], second['swatch'])
        for view in (first, second):
            self.assertTrue(view['listVisible'] and view['headerVisible'] and view['balloonVisible'], view)
            self.check_coloured_view(view, view['ratios'])
        self.assertTrue(icon['icon'], icon)
        self.assertEqual(icon['head']['fill'], icon['balloon']['fill'])
        self.assertEqual(icon['head']['token'], '')
        self.assertEqual(rgb(icon['head']['fill']), rgb(icon['violet']), icon)

    def test_contrast_and_fill_in_every_theme(self):
        for theme, cases in self.got['allThemes'].items():
            for case in cases:
                view, ratios = case['view'], case['ratios']
                self.check_coloured_view(view, ratios)


if __name__ == '__main__':
    unittest.main()
