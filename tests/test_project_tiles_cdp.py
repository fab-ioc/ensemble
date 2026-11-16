"""#370 (P356): the project tile with initials, Flat and Glossy, in a real page.

Four throwaway projects: three get a tile, one an uploaded icon. Each look is
measured in Light and Dark: the tile's letters, its fill and ink (the PO
label's, at least 4.5:1, and in Glossy at both ends of the gradient), the
shine, and that an uploaded icon takes the tile's place in both. The setting is
then chosen in Settings, kept by the hub over a reload with no cache, and taken
from another device's change when the page comes back to the front.
Skipped without Node or Chrome.
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
NAMES = ('opTen QA', 'Tasse Ticino', 'my-app_x', 'Icon Studio')


def channels(color: str) -> tuple[float, float, float]:
    """rgb(r, g, b) or color(srgb r g b) as 0..1 channels."""
    nums = [float(x) for x in re.findall(r'[\d.]+', color)]
    if color.startswith('color('):
        return tuple(nums[:3])
    return tuple(v / 255 for v in nums[:3])


def contrast(a: str, b: str) -> float:
    def lum(color: str) -> float:
        lin = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels(color)]
        return sum(x * y for x, y in zip(lin, (.2126, .7152, .0722)))
    x, y = lum(a), lum(b)
    return (max(x, y) + .05) / (min(x, y) + .05)


CDP_JS = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0',
    '--user-data-dir=' + chromeProfile(A), '--no-first-run', '--disable-gpu', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((resolve,reject) => { let buf=''; ch.stderr.on('data', d => {
    buf += d; const m=buf.match(/DevTools listening on (ws:\S+)/); if(m) resolve(m[1]);
  }); ch.on('exit', c => reject(Error('chrome exited '+c))); setTimeout(() => reject(Error('no CDP '+buf)), 20000); });
  const c = new WebSocket(ws), waits = new Map(); let seq=0;
  await new Promise((resolve,reject) => { c.onopen=resolve; c.onerror=reject; });
  const errors = [];
  c.onmessage = ev => { const m=JSON.parse(ev.data);
    if (m.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(m.params.exceptionDetails).slice(0, 400));
    const w=waits.get(m.id); if(w) { waits.delete(m.id); m.error?w[1](Error(m.error.message)):w[0](m.result); } };
  const send=(method,params={},sessionId) => new Promise((resolve,reject) => { const id=++seq; waits.set(id,[resolve,reject]); c.send(JSON.stringify({id,method,params,sessionId})); });
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},s); await send('Runtime.enable',{},s);
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false},s);
  const ev=async expression => { const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);
    if(r.exceptionDetails) throw Error(expression.slice(0,120)+' '+JSON.stringify(r.exceptionDetails).slice(0,500)); return r.result.value; };
  const until=async (expression,label) => { for(let i=0;i<150;i++){try{if(await ev(expression))return;}catch(e){} await sleep(120);}
    throw Error('timeout '+label); };
  const load = async () => {
    await send('Page.navigate',{url:A.base+'/'},s);
    await until(`typeof PROJECTS!=='undefined' && !!PROJECTS && PROJECTS.projects.length===4 && !!PREFS &&
      document.querySelectorAll('#ld-projs [data-ldp-proj]').length===4`, 'projects');
  };
  // Every row's square, as drawn now.
  const measure = `(() => {
    const probe = document.body.appendChild(document.createElement('i'));
    const resolve = x => { probe.style.color = ''; probe.style.color = x; return getComputedStyle(probe).color; };
    const stops = g => { const out = [], s = String(g || ''); let depth = 0, cur = '';
      const inner = s.slice(s.indexOf('(') + 1, s.lastIndexOf(')'));
      for (const ch of inner) { if (ch === '(') depth++; if (ch === ')') depth--;
        if (ch === ',' && !depth) { out.push(cur.trim()); cur = ''; } else cur += ch; }
      out.push(cur.trim());
      return out.slice(1).map(x => resolve(x.replace(/\\s+[\\d.]+%$/, ''))); };
    const root = getComputedStyle(document.documentElement);
    const rows = [...document.querySelectorAll('#ld-projs [data-ldp-proj]')].map(row => {
      const sq = row.querySelector('.ldp-ico'), cs = getComputedStyle(sq), k = sq.dataset.k || '';
      const before = getComputedStyle(sq, '::before'), after = getComputedStyle(sq, '::after');
      return { name: row.querySelector('.ldp-name').textContent, rowText: row.textContent,
        tile: sq.classList.contains('ptile'), img: !!sq.querySelector('img'), tag: sq.tagName.toLowerCase(),
        w: sq.getBoundingClientRect().width, h: sq.getBoundingClientRect().height,
        letters: before.content, fill: cs.backgroundColor, image: cs.backgroundImage, ink: cs.color,
        shine: after.content, shadow: cs.boxShadow, shineImage: after.backgroundImage,
        letterZ: before.zIndex, shineZ: after.zIndex,
        want: k ? { fill: resolve(root.getPropertyValue('--pt-' + k + '-f')), ink: resolve(root.getPropertyValue('--pt-' + k + '-i')),
          stops: stops(root.getPropertyValue('--pt-' + k + '-g')) } : null };
    });
    probe.remove();
    return { look: document.documentElement.dataset.ptiles, theme: document.documentElement.dataset.theme, rows };
  })()`;
  try {
    await load();
    const out = { views: [], errors };
    // The bar switcher: a tile row and an uploaded-icon row are the same height, also with the
    // pinned row's grip standing in for the icon.
    await ev(`openProjMenu(); 0`); await sleep(300);
    out.switcher = await ev(`[...document.querySelectorAll('#proj-menu .pm-item[data-proj]')].map(e => {
      const r = e.closest('.pm-row'), h = e.getBoundingClientRect().height; r.classList.add('pm-lifted');
      const lifted = e.getBoundingClientRect().height; r.classList.remove('pm-lifted');
      return [e.querySelector('.pm-name').textContent, h, lifted]; })`);
    await ev(`closeProjMenu(); 0`);
    out.initials = await ev(`${JSON.stringify(A.cases)}.map(n => ProjectColour.initials(n))`);
    for (const theme of ['light', 'dark']) {
      await ev(`localStorage.setItem('cd-theme', ${JSON.stringify(theme)}); cdTheme.apply(); 0`);
      for (const look of ['flat', 'glossy']) {
        await ev(`ptileLook(${JSON.stringify(look)}); 0`); await sleep(80);
        out.views.push(await ev(measure));
      }
    }
    await ev(`localStorage.setItem('cd-theme', 'light'); cdTheme.apply(); ptileLook('flat'); 0`);
    // Chosen in Settings › Preferences: the hub keeps it.
    await ev(`openSettings('prefs'); 0`); await sleep(300);
    out.before = await ev(`[...document.querySelectorAll('#pref-ptiles button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`);
    await ev(`document.querySelector('#pref-ptiles button[data-val="glossy"]').click(); 0`);
    await until(`fetch('/api/settings').then(r => r.json()).then(j => j.projectIcons === 'glossy')`, 'saved');
    out.clicked = await ev(`document.documentElement.dataset.ptiles`);
    // A fresh page with no cache of its own draws the hub's choice.
    await ev(`localStorage.removeItem('cd-ptiles'); 0`);
    await load();
    await until(`document.documentElement.dataset.ptiles === 'glossy'`, 'reloaded glossy');
    out.reloaded = await ev(measure);
    await ev(`openSettings('prefs'); 0`); await sleep(300);
    out.after = await ev(`[...document.querySelectorAll('#pref-ptiles button')].map(b => [b.dataset.val, b.getAttribute('aria-pressed')])`);
    // Another device sets Flat; this page takes it when it comes back to the front.
    await ev(`fetch('/api/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({projectIcons: 'flat'})}).then(r => r.ok)`);
    await ev(`document.dispatchEvent(new Event('visibilitychange')); 0`);
    await until(`document.documentElement.dataset.ptiles === 'flat'`, 'other device');
    out.otherDevice = await ev(`document.documentElement.dataset.ptiles`);
    // Two quick clicks whose saves would cross: the first one's PUT is held back. The last click wins
    // on the page and in the hub.
    await ev(`openSettings('prefs'); 0`); await sleep(300);
    await ev(`(() => { const real = api; window.api = async (url, opts) => {
      if (opts && opts.method === 'PUT' && String(opts.body).includes('glossy')) await new Promise(r => setTimeout(r, 600));
      return real(url, opts); }; 0; })()`);
    await ev(`document.querySelector('#pref-ptiles button[data-val="glossy"]').click();
      document.querySelector('#pref-ptiles button[data-val="flat"]').click(); 0`);
    await sleep(1500);
    out.quick = await ev(`fetch('/api/settings').then(r => r.json()).then(j => ({ hub: j.projectIcons,
      look: document.documentElement.dataset.ptiles, prefs: PREFS.projectIcons,
      pressed: [...document.querySelectorAll('#pref-ptiles button[aria-pressed="true"]')].map(b => b.dataset.val) }))`);
    console.log(JSON.stringify(out));
  } finally { c.close(); ch.kill(); }
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class ProjectTilesInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='ens-ptiles-', ignore_cleanup_errors=True)
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
        for p in cls.patches:
            p.start(); cls.addClassCleanup(p.stop)
        ids = []
        for name in NAMES:
            ok, proj, _ = dashboard.register_project(name); assert ok, proj
            ids.append(proj['id'])
        svg = 'data:image/svg+xml;base64,' + base64.b64encode(SVG.encode()).decode()
        ok, _, icon = dashboard.set_project_icon(ids[3], svg, 'icon.svg'); assert ok and icon
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        cls.server.daemon_threads = True; cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), 'tmp': cls.tmp.name,
                'base': f'http://127.0.0.1:{cls.server.server_address[1]}',
                'cases': ['opTen', 'opTen QA', 'Tasse Ticino', 'my-app_x', 'élan vital', '3d print shop', '  ', '#!']}
        script = base / 'ptiles_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        result = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=180)
        assert result.returncode == 0, result.stderr[-5000:]
        cls.got = json.loads(result.stdout.strip().splitlines()[-1])
        cls.settings = json.loads((state / 'settings.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def test_initials(self):
        self.assertEqual(self.got['initials'], ['O', 'OQ', 'TT', 'MA', 'ÉV', '3P', '', ''])

    def test_four_views_are_measured(self):
        self.assertEqual([(v['theme'], v['look']) for v in self.got['views']],
                         [('light', 'flat'), ('light', 'glossy'), ('dark', 'flat'), ('dark', 'glossy')])

    def test_a_tile_shows_the_initials_and_adds_no_text(self):
        for view in self.got['views']:
            tiles = {r['name']: r for r in view['rows'] if r['tile']}
            self.assertEqual({n: r['letters'] for n, r in tiles.items()},
                             {'opTen QA': '"OQ"', 'Tasse Ticino': '"TT"', 'my-app_x': '"MA"'}, view['look'])
            for r in view['rows']:
                self.assertEqual(r['rowText'], r['name'], 'the initials are drawn, not text')
                self.assertEqual((r['w'], r['h']), (22, 22), r)

    def test_flat_is_the_po_label_colour_at_4_5_to_1(self):
        for view in self.got['views']:
            if view['look'] != 'flat':
                continue
            for r in (r for r in view['rows'] if r['tile']):
                self.assertEqual(r['image'], 'none', r)
                self.assertEqual(r['shine'], 'none', r)
                self.assertEqual(channels(r['fill']), channels(r['want']['fill']), (view['theme'], r))
                self.assertEqual(channels(r['ink']), channels(r['want']['ink']), (view['theme'], r))
                self.assertGreaterEqual(contrast(r['ink'], r['fill']), 4.5, (view['theme'], r))

    def test_glossy_has_the_shine_and_keeps_4_5_to_1_over_the_whole_tile(self):
        for view in self.got['views']:
            if view['look'] != 'glossy':
                continue
            for r in (r for r in view['rows'] if r['tile']):
                self.assertIn('linear-gradient', r['image'], r)
                self.assertNotEqual(r['shine'], 'none', r)
                self.assertNotEqual(r['shadow'], 'none', r)
                self.assertEqual(channels(r['ink']), channels(r['want']['ink']), (view['theme'], r))
                self.assertTrue(r['want']['stops'], r)
                for stop in r['want']['stops']:
                    self.assertGreaterEqual(contrast(r['ink'], stop), 4.5, (view['theme'], stop, r))

    def test_glossy_shine_lies_under_the_letters_and_keeps_4_5_to_1(self):
        # What the eye sees under the shine: each gradient stop with the shine's white over it.
        for view in self.got['views']:
            if view['look'] != 'glossy':
                continue
            for r in (r for r in view['rows'] if r['tile']):
                self.assertGreater(int(r['letterZ']), int(r['shineZ']) if r['shineZ'] != 'auto' else 0, r)
                alpha = float(re.search(r'rgba\([^)]*,\s*([\d.]+)\)', r['shineImage']).group(1))
                self.assertLessEqual(alpha, .36, r)
                for stop in r['want']['stops']:
                    c = channels(stop)
                    seen = 'rgb(%f, %f, %f)' % tuple(255 * (v + (1 - v) * alpha) for v in c)
                    self.assertGreaterEqual(contrast(r['ink'], seen), 4.5, (view['theme'], alpha, stop, r))
                if r['want']['ink'] != 'rgb(255, 255, 255)':
                    self.assertEqual(alpha, .36, ('a dark letter takes the full shine', r))

    def test_switcher_rows_are_one_height(self):
        self.assertEqual(sorted(n for n, _, _ in self.got['switcher']), sorted(NAMES))
        for name, h, lifted in self.got['switcher']:
            self.assertEqual((h, lifted), (30, 30), name)

    def test_the_last_of_two_quick_clicks_wins(self):
        self.assertEqual(self.got['quick'], {'hub': 'flat', 'look': 'flat', 'prefs': 'flat', 'pressed': ['flat']})

    def test_an_uploaded_icon_wins_over_both_looks(self):
        for view in self.got['views']:
            icon = next(r for r in view['rows'] if r['name'] == 'Icon Studio')
            self.assertTrue(icon['img'], (view['look'], icon))
            self.assertFalse(icon['tile'], (view['look'], icon))
            self.assertEqual((icon['w'], icon['h']), (22, 22), icon)

    def test_the_choice_is_kept_by_the_hub(self):
        self.assertEqual(self.got['before'], [['flat', 'true'], ['glossy', 'false']])
        self.assertEqual(self.got['clicked'], 'glossy')
        self.assertEqual(self.got['reloaded']['look'], 'glossy')
        self.assertEqual(self.got['after'], [['flat', 'false'], ['glossy', 'true']])
        self.assertEqual(self.got['otherDevice'], 'flat')
        self.assertEqual(self.settings.get('projectIcons'), 'flat')

    def test_no_page_errors(self):
        self.assertEqual(self.got['errors'], [])


class ProjectIconsSetting(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ens-ptiles-set-', ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', Path(tmp.name) / 'settings.json')
        p.start(); self.addCleanup(p.stop)

    def test_flat_by_default(self):
        self.assertEqual(dashboard.load_settings()['projectIcons'], 'flat')

    def test_flat_or_glossy_only(self):
        self.assertEqual(dashboard.save_settings({'projectIcons': 'glossy'})['projectIcons'], 'glossy')
        for bad in ('shiny', '', 'Glossy', 1, None, ['flat']):
            self.assertEqual(dashboard.save_settings({'projectIcons': bad})['projectIcons'], 'glossy', bad)
        self.assertEqual(dashboard.save_settings({'projectIcons': 'flat'})['projectIcons'], 'flat')


if __name__ == '__main__':
    unittest.main()
