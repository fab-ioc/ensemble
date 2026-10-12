"""#208 (CEO point P177): an HTML page in a project's Documents folder, opened
from a chat link, shows its pictures and styles, and its links open the pages
they name.

An agent writes the link relative ("Documents\\#9 Some page\\compare.html"),
so fileview.html's folder for the page's <base> was relative too and
fileAtBase() gave none: every picture broken, and each click opened one more
new tab of a page just as broken. Now:

* fvFound: the hub's resolved path (X-Ensemble-Path) becomes the page's folder;
* a plain click on a relative link opens that file in this viewer, carrying
  ?back= to the viewer before it; the header's Back returns there (a tab's own
  history, or the address itself in a panel's frame);
* a page opened that way says nothing to the Workspace tab it is in;
* fileAtBase folds "." and ".." (a "../x.html" link), which the hub refuses.

The node tests run fileview.html's own functions; the Chrome test opens the
fixture on an isolated hub by a relative chat-link address, an absolute one and
inside a same-origin frame (the Dock panel), at 1440x900 and 390x844 (touch).
"""
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import file_refs
from tests import chrome_profile
from tests.test_file_at import PAGE, at_tail, js_function
from tests.test_task_numbers import Hub
from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, NODE, CHROME


def png(w: int, h: int) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xcc\x44\x22" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


COMPARE = """<!doctype html>
<html><head><title>Compare</title><link rel="stylesheet" href="mock.css"></head>
<body><h1>Compare</h1>
<a href="a.html"><img src="a.png" alt="A"></a>
<img src="shots/b.png" alt="B">
<p><a href="a.html#top">The A page</a> and <a href="a.png">the A picture</a>.</p>
<p><a href="//example.invalid/page">Elsewhere</a></p>
<div style="height:2400px"></div></body></html>
"""
A_PAGE = """<!doctype html>
<html><head><title>A</title><link rel="stylesheet" href="mock.css"></head>
<body><h1 id="top">A page</h1><img src="a.png" alt="A">
<p><a href="../%239%20Some%20page/compare.html">Back to compare by ..</a></p></body></html>
"""


@unittest.skipUnless(NODE, "node is not installed")
class ViewerFunctions(unittest.TestCase):
    """fileAtBase, frameLinkPath, resolvePath and fvFound, run in Node."""

    @classmethod
    def setUpClass(cls):
        code = "\n".join(js_function(n) for n in ("resolvePath", "fileAtBase", "frameLinkPath", "fvFound"))
        abs_re = re.search(r"^const ABS_PATH_RE = .*$", PAGE, re.M).group(0)
        script = f"""
{abs_re}
let FILE_BASE = 'Documents\\\\#9 Some page';
{code}
const out = {{}};
out.base = fileAtBase('C:\\\\P\\\\Documents\\\\#9 Some page');
out.dots = fileAtBase('C:\\\\P\\\\Documents\\\\#9 Some page\\\\..\\\\.\\\\#10 Other');
out.dotsTop = fileAtBase('C:\\\\..\\\\..\\\\x');
out.posixDots = fileAtBase('/home/a/docs/../pics');
out.link1 = frameLinkPath('a%20b.html#x');
out.link2 = frameLinkPath('%239 x/p.html?q=1');
out.link3 = frameLinkPath('bad%zz.html');
out.relBefore = resolvePath('a.html', FILE_BASE);
fvFound('Documents\\\\x.html');            // not absolute: ignored
out.ignored = FILE_BASE;
fvFound('C:\\\\P\\\\Documents\\\\#9 Some page\\\\compare.html');
out.found = FILE_BASE;
out.relAfter = resolvePath('a.html', FILE_BASE);
out.atAfter = fileAtBase(FILE_BASE);
console.log(JSON.stringify(out));
"""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.cjs"
            f.write_text(script, encoding="utf-8")
            proc = subprocess.run([NODE, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.out = json.loads(proc.stdout)

    def test_a_hash_and_spaces_in_a_folder_name_are_encoded(self):
        self.assertEqual(self.out["base"], "/api/file-at/w/C%3A/P/Documents/%239%20Some%20page/")

    def test_dot_segments_are_folded(self):
        self.assertEqual(self.out["dots"], "/api/file-at/w/C%3A/P/Documents/%2310%20Other/")
        self.assertEqual(self.out["dotsTop"], "/api/file-at/w/C%3A/x/")   # never above the drive
        self.assertEqual(self.out["posixDots"], "/api/file-at/p/home/a/pics/")

    def test_a_link_loses_its_query_and_fragment_and_is_decoded(self):
        self.assertEqual(self.out["link1"], "a b.html")
        self.assertEqual(self.out["link2"], "#9 x/p.html")
        self.assertEqual(self.out["link3"], "bad%zz.html")

    def test_the_hubs_absolute_path_becomes_the_folder(self):
        self.assertEqual(self.out["relBefore"], "Documents\\#9 Some page\\a.html")
        self.assertEqual(self.out["ignored"], "Documents\\#9 Some page")
        self.assertEqual(self.out["found"], "C:\\P\\Documents\\#9 Some page")
        self.assertEqual(self.out["relAfter"], "C:\\P\\Documents\\#9 Some page\\a.html")
        self.assertEqual(self.out["atAfter"], "/api/file-at/w/C%3A/P/Documents/%239%20Some%20page/")


class ConnectionsWhileBusy(unittest.TestCase):
    """A page's ten pictures, while the hub has not accepted yet: the stdlib's
    queue of 5 refused the rest (measured on the live hub as
    ERR_CONNECTION_REFUSED on pictures and scripts); the hub's queue holds them."""

    def test_forty_connections_wait_for_a_busy_hub(self):
        import socket
        from http.server import BaseHTTPRequestHandler
        srv = dashboard.HubServer(('127.0.0.1', 0), BaseHTTPRequestHandler)   # listening, nothing accepted
        self.addCleanup(srv.server_close)
        socks = []
        try:
            for _ in range(40):
                socks.append(socket.create_connection(('127.0.0.1', srv.server_address[1]), timeout=3))
        finally:
            for s in socks:
                s.close()
        self.assertEqual(len(socks), 40)

    def test_main_serves_with_the_hub_server(self):
        src = Path(dashboard.__file__).read_text(encoding='utf-8')
        main = src[src.index('\ndef main():'):]
        self.assertNotIn('ThreadingHTTPServer(', main)
        self.assertEqual(main.count('HubServer(('), 4)


class FolderWithAHash(Hub):
    def test_resolve_file_at_serves_a_picture_in_a_hash_folder(self):
        folder = Path(dashboard.find_project(self.ed)["path"]) / "Documents" / "#9 Some page"
        folder.mkdir(parents=True)
        (folder / "a.png").write_bytes(png(2, 2))
        segs = [folder.drive] + list(folder.parts[1:])
        self.assertEqual(dashboard.resolve_file_at(at_tail("w", segs + ["a.png"])), folder / "a.png")


CDP_JS = TASK_CDP.split('// The chips in the conversation')[0] + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = {};
  const tabs = [], urls = new Map();   // every page target opened, and where each is
  c.ws.addEventListener('message', e => { const m = JSON.parse(e.data);
    if (m.method === 'Target.targetCreated' && m.params.targetInfo.type === 'page') tabs.push(m.params.targetInfo.targetId);
    if (/^Target\.target(?:Created|InfoChanged)$/.test(m.method) && m.params.targetInfo.type === 'page') urls.set(m.params.targetInfo.targetId, m.params.targetInfo.url); });
  await c.send('Target.setDiscoverTargets', {discover: true});
  try {
    for (const [width, height, mob] of [[1440, 900, false], [390, 844, true]]) {
      for (const [mode, url] of Object.entries(A.urls)) {
        const row = out[width + ' ' + mode] = {};
        const {targetId} = await c.send('Target.createTarget', {url: 'about:blank'});
        const {sessionId: s} = await c.send('Target.attachToTarget', {targetId, flatten: true});
        await c.send('Page.enable', {}, s);
        await c.send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: mob}, s);
        if (mob) await c.send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5}, s);
        const ev = async e => { const r = await c.send('Runtime.evaluate', {expression: e, awaitPromise: true, returnByValue: true}, s);
          if (r.exceptionDetails) throw Error(e.slice(0, 80) + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
        const until = async (e, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { try { const v = await ev(e); if (v) return v; } catch (x) {} await sleep(150); } throw Error('timeout ' + e.slice(0, 200)); };
        const FV = mode === 'panel' ? `(document.querySelector('#panel') || {}).contentWindow` : 'window';
        const state = () => ev(`(() => { const w = ${FV}; const f = w.document.querySelector('iframe.file'), img = w.document.querySelector('main img.file');
          const q = new URLSearchParams(w.location.search), b = w.document.getElementById('back');
          const r = {path: q.get('path'), back: q.get('back') || '', backBtn: !b.hidden, backTitle: b.title};
          if (img) { r.image = img.naturalWidth; return r; }
          const d = f.contentDocument; r.title = d.title; r.imgs = d.images.length; r.imgsOk = [...d.images].filter(i => i.naturalWidth > 0).length;
          r.sheets = [...d.styleSheets].map(x => { try { return x.cssRules.length; } catch (e) { return -1; } });
          r.color = getComputedStyle(d.body).color;
          d.scrollingElement.scrollTop = 300; r.scrolls = d.scrollingElement.scrollTop > 0; d.scrollingElement.scrollTop = 0;
          r.overflow = w.document.documentElement.scrollWidth > w.innerWidth; return r; })()`);
        const ready = () => until(`(() => { const w = ${FV}; if (!w || w.location.pathname !== '/fileview' || w.document.readyState !== 'complete') return false;
          const img = w.document.querySelector('main img.file'); if (img) return img.complete && img.naturalWidth > 0;
          const f = w.document.querySelector('iframe.file'); if (!f) return false; const d = f.contentDocument;
          return d && d.readyState === 'complete' && d.images.length > 0 && [...d.images].every(i => i.complete) && d.styleSheets.length > 0; })()`);
        const clickAt = async (x, y) => { if (mob) { await c.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x, y}]}, s); await c.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []}, s); }
          else for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', {type, x, y, button: 'left', clickCount: 1}, s); };
        const pointOf = sel => ev(`(() => { const w = ${FV}; const off = win => { let x = 0, y = 0; while (win !== window) { const r = win.frameElement.getBoundingClientRect(); x += r.x; y += r.y; win = win.parent; } return [x, y]; };
          const el = (${sel})(w); if (!el) return null; el.scrollIntoView({block: 'center'}); const b = el.getBoundingClientRect(); const [ox, oy] = off(el.ownerDocument.defaultView);
          return {x: ox + b.x + Math.min(20, b.width / 2), y: oy + b.y + Math.min(10, b.height / 2)}; })()`);
        const click = async sel => { const p = await pointOf(sel); if (!p) throw Error('nothing at ' + sel); const before = await ev(`${FV}.location.href`);
          await clickAt(p.x, p.y); await until(`(() => { try { return ${FV}.location.href !== ${JSON.stringify(before)}; } catch (e) { return false; } })()`); await ready(); await sleep(250); };
        const link = test => `w => [...w.document.querySelector('iframe.file').contentDocument.links].find(a => ${test})`;
        const BACK = `w => w.document.getElementById('back')`;
        if (mode === 'panel') {
          // the Dock's file panel: the viewer in a same-origin frame of a dashboard page
          await c.send('Page.navigate', {url: A.base + '/fileview'}, s);
          await until(`document.readyState === 'complete'`);
          await ev(`(() => { document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'panel';
            f.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;border:0'; f.src = ${JSON.stringify(url)}; document.body.appendChild(f);
            window.HOST = []; addEventListener('message', e => HOST.push(e.data && e.data.type)); })()`);
        } else await c.send('Page.navigate', {url: A.base + url}, s);
        await ready(); await sleep(300);
        row.start = await state();
        // a link to another site ("//host/x"): a tab of its own, this viewer stays
        { const here = await ev(`${FV}.location.href`), t0 = tabs.length, p = await pointOf(link(`a.getAttribute('href').startsWith('//')`));
          await clickAt(p.x, p.y);
          let opened = [];
          for (let i = 0; i < 60; i++) { opened = tabs.slice(t0); if (opened.length && opened.every(id => /^https?:/.test(urls.get(id) || ''))) break; await sleep(100); }
          row.external = {opened: opened.map(id => urls.get(id)), want: await ev(`new URL('//example.invalid/page', ${FV}.location.href).href`),
                          same: await ev(`${FV}.location.href`) === here};
          for (const id of opened) await c.send('Target.closeTarget', {targetId: id}).catch(() => {}); }
        const tabsAtStart = tabs.length;
        await click(link(`a.querySelector('img')`));
        const hostAtA = mode === 'panel' ? await ev('HOST.length') : 0;
        row.afterPic = await state(); await sleep(600);
        if (mode === 'panel') row.hostWhileA = (await ev('HOST')).slice(hostAtA).filter(t => t === 'fv-state' || t === 'fv-missing');
        await click(BACK); row.afterBack = await state();
        await click(link(`!a.querySelector('img') && a.getAttribute('href').startsWith('a.html')`)); row.afterText = await state();
        await click(link(`a.getAttribute('href').startsWith('../')`)); row.afterDots = await state();
        await click(BACK); row.afterBack2 = await state();
        await click(BACK); row.afterBack3 = await state();
        await click(link(`a.getAttribute('href').endsWith('.png')`)); row.afterPng = await state();
        row.newTabs = tabs.length - tabsAtStart;
        if (A.shots) { const r = await c.send('Page.captureScreenshot', {format: 'png'}, s); fs.writeFileSync(path.join(A.shots, `html208-${width}-${mode}.png`), Buffer.from(r.data, 'base64')); }
        await c.send('Target.closeTarget', {targetId});
      }
    }
  } finally { try { await c.send('Browser.close'); } catch (e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || e); process.exit(1); });
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class HtmlPageInChrome(Hub):
    def test_pictures_styles_and_links(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        file_refs.forget_indexes(); self.addCleanup(file_refs.forget_indexes)
        proj = Path(dashboard.find_project(self.ed)['path'])
        page = proj / 'Documents' / '#9 Some page'
        (page / 'shots').mkdir(parents=True)
        (page / 'compare.html').write_text(COMPARE, encoding='utf-8')
        (page / 'a.html').write_text(A_PAGE, encoding='utf-8')
        (page / 'mock.css').write_text('body { color: rgb(1, 2, 3); }\nh1 { margin: 0; }\n', encoding='utf-8')
        (page / 'a.png').write_bytes(png(40, 30))
        (page / 'shots' / 'b.png').write_bytes(png(20, 10))
        rid = self.room('Task chat', self.ed)
        full = chatroom.get_room(rid, public=False)
        full['cwd'] = str(proj)
        chatroom.update_room(full)
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        from urllib.parse import quote
        rel = '/fileview?path=' + quote('Documents\\#9 Some page\\compare.html', safe='') + '&room=' + rid
        absolute = '/fileview?path=' + quote(str(page / 'compare.html'), safe='')
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port),
                'urls': {'chat': rel, 'alone': absolute, 'panel': rel}, 'shots': shots}
        script = base / 'html208_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=400)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        same = lambda p: Path(p) == page / 'compare.html' or p == 'Documents\\#9 Some page\\compare.html'
        for key, row in got.items():
            with self.subTest(key):
                s = row['start']
                self.assertTrue(same(s['path']), s)
                self.assertEqual((s['imgs'], s['imgsOk']), (2, 2), s)
                self.assertEqual(s['sheets'], [2], s)
                self.assertEqual(s['color'], 'rgb(1, 2, 3)', s)
                self.assertTrue(s['scrolls'], s)
                self.assertFalse(s['backBtn'], s)
                self.assertFalse(s['overflow'], s)
                # a picture link: the A page in this viewer, with the way back
                a = row['afterPic']
                self.assertEqual(Path(a['path']), page / 'a.html', a)
                self.assertEqual((a['title'], a['imgsOk'], a['sheets']), ('A', 1, [2]), a)
                self.assertTrue(a['backBtn'], a)
                self.assertEqual(a['backTitle'], 'Back to compare.html')
                self.assertFalse(a['overflow'], a)
                for k in ('afterBack', 'afterBack3'):
                    b = row[k]
                    self.assertTrue(same(b['path']), (k, b))
                    self.assertEqual((b['title'], b['imgsOk'], b['backBtn']), ('Compare', 2, False), (k, b))
                self.assertEqual(Path(row['afterText']['path']), page / 'a.html')
                # "../#9 Some page/compare.html" from the A page: dots folded, two steps back
                d = row['afterDots']
                self.assertEqual((d['title'], d['imgsOk'], d['sheets']), ('Compare', 2, [2]), d)
                self.assertTrue(d['backBtn'], d)
                self.assertEqual(Path(row['afterBack2']['path']), page / 'a.html')
                self.assertTrue(row['afterBack2']['backBtn'])
                self.assertEqual(row['afterPng'].get('image'), 40, row['afterPng'])
                self.assertEqual(row['newTabs'], 0, 'no new tab opened')
                self.assertTrue(row['external']['same'], row['external'])
                self.assertEqual(row['external']['want'], 'http://example.invalid/page')
                self.assertEqual(row['external']['opened'], [row['external']['want']], 'one tab, at that address')
                if 'hostWhileA' in row:
                    self.assertEqual(row['hostWhileA'], [], 'a page opened from a link says nothing to the panel host')


if __name__ == '__main__': unittest.main()
