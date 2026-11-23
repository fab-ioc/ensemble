"""A Reload on an open file reloads only that file (GitHub issue 67).

* GET /api/file/version: a file's version (modification time and size) in a
  few bytes, never the file; 404 for no file or a folder, 403 outside the
  readable folders, 400 with no path.
* In a real browser (a hub in a thread, headless Chrome over CDP), the file
  view of a Markdown file, standalone and framed in another page: changed on
  disk, it says "This file changed · Reload" at the next check; with a comment
  being written it does not reload by itself, and the header's Reload draws
  the new text in place: the page and its host are not loaded again, the
  scroll place, the unsent comments and the comment being written stay, and a
  comment whose words are gone is listed as not found. With nothing in hand
  it reloads by itself. An HTML page, a code file and a picture reload too.

The browser part is skipped without Node or Chrome.
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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402

import struct  # noqa: E402
import zlib  # noqa: E402


def _png(w: int, rgb: bytes) -> bytes:
    """A w x 1 PNG of one colour, so the reloaded picture is known by its width."""
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + rgb * w)) + chunk(b"IEND", b""))


PNG_1 = _png(1, b"\xff\x00\x00")
PNG_2 = _png(2, b"\x00\x00\xff")


class Version(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ens-fver-")
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "plan.md"
        self.f.write_text("one\n", encoding="utf-8")
        p = mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True)
        p.start()
        self.addCleanup(p.stop)

    def test_the_version_follows_the_modification_time_and_size(self):
        code, a = dashboard.file_version_answer(str(self.f))
        self.assertEqual(code, 200)
        self.assertEqual(set(a), {"version", "mtime", "size"})
        self.assertEqual(a["size"], 4)
        self.assertEqual(dashboard.file_version_answer(str(self.f))[1]["version"], a["version"], "the same file, the same version")
        self.f.write_text("two\n", encoding="utf-8")        # same size, a later time
        os.utime(self.f, ns=(time.time_ns() + 10**9,) * 2)
        b = dashboard.file_version_answer(str(self.f))[1]
        self.assertNotEqual(b["version"], a["version"])
        st = self.f.stat()
        os.utime(self.f, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.f.write_bytes(b"two!\n")                       # a different size
        os.utime(self.f, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotEqual(dashboard.file_version_answer(str(self.f))[1]["version"], b["version"], "same time, another size")

    def test_no_file_a_folder_no_path_and_a_forbidden_one(self):
        self.assertEqual(dashboard.file_version_answer(str(self.f.with_name("nope.md")))[0], 404)
        self.assertEqual(dashboard.file_version_answer("")[0], 400)
        with mock.patch.object(dashboard, "resolve_file_ref", lambda *a, **k: Path(self.tmp.name)):
            self.assertEqual(dashboard.file_version_answer(self.tmp.name)[0], 404, "a folder has no version")
        with mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: False):
            code, body = dashboard.file_version_answer(str(self.f))
            self.assertEqual((code, body), (403, {"error": "path_not_allowed"}))

    def test_the_answer_is_never_kept_by_a_browser(self):
        self.assertIn("/api/file/version", dashboard.NO_STORE_GETS)


CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--use-mock-keychain', '--password-store=basic', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1100,700', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c + ' ' + buf))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function page(c) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  return { ev, until, go: async (url, ready) => { await c.send('Page.navigate', { url }, sessionId); await until(ready); }, close: () => c.send('Target.closeTarget', { targetId }) };
}
// A change on disk with a later modification time than the one shown.
let bump = 2;
const write = (f, data) => { fs.writeFileSync(f, data); const t = Date.now() / 1000 + (bump++); fs.utimesSync(f, t, t); };
// The checks run every 30 s; a test asks at once.
const CHECK = W => `(async () => { ${W}.fvChg.checkedAt = 0; await ${W}.fvChg.tick(); return 1; })()`;

// The Markdown file, in window W ('window' standalone, the frame's window framed).
async function markdown(p, W, ready, out) {
  write(A.md, A.mdOld);
  await p.go(ready.url, ready.expr);
  await p.until(`${W}.document.querySelector('main .md h2') && ${W}.fvChg.seen`);
  const r = {};
  r.button = await p.ev(`(() => { const b = ${W}.document.getElementById('fv-reload'); const h = [...${W}.document.querySelectorAll('header .btn')].filter(x => !x.hidden).map(x => x.id);
    return { tag: b.tagName, title: b.title, label: b.getAttribute('aria-label'), next: b.nextElementSibling.id, tab: b.tabIndex, order: h }; })()`);
  // A place in the file, two unsent comments (one on words the change takes out), a comment being written.
  await p.ev(`(() => { const w = ${W}; const t = w.eval('_cRoot').textContent; const q = 'The gearbox runs at seven to one.'; const s = t.indexOf(q);
    w.eval('FVC').put({ cid: w.eval('FVC').id(), at: w.eval('FVC').now(), note: 'Is this right?', view: w.eval('CUR_VIEW'), start: s, end: s + q.length, quote: q });
    w.eval('FVC').put({ cid: w.eval('FVC').id(), at: w.eval('FVC').now(), note: 'Whole file note', view: w.eval('CUR_VIEW'), start: -1, end: -1, quote: '' });
    w.clearHl(); w.remarkAll(); w.renderTray();
    w.document.getElementById('cmt-file').click();
    const ta = w.document.querySelector('.cmt-composer textarea'); ta.value = 'half a thought'; ta.dispatchEvent(new w.InputEvent('input', { bubbles: true }));
    w.frameMark = 'kept'; window.hostMark = 'kept'; return 1; })()`);
  await p.ev(`(() => { const s = ${W}.document.scrollingElement; s.scrollTop = 600; return s.scrollTop; })()`);
  r.before = await p.ev(`(() => { const w = ${W}; return { top: w.document.scrollingElement.scrollTop, line: !w.document.getElementById('fv-chg').hidden, marks: w.document.querySelectorAll('mark.cmt-hl').length,
    lost: w.document.querySelectorAll('#cmt-tray .cmt-lost').length }; })()`);
  // Changed on disk: the line at the next check; the comment being written holds the reload off.
  write(A.md, A.mdNew);
  await p.ev(CHECK(W));
  r.line = await p.ev(`(() => { const n = ${W}.document.getElementById('fv-chg'); return { shown: !n.hidden, text: n.textContent, top: ${W}.document.scrollingElement.scrollTop,
    below: Math.round(n.getBoundingClientRect().top - ${W}.document.querySelector('header').getBoundingClientRect().bottom) }; })()`);
  await sleep(6500);
  r.held = await p.ev(`(() => ({ old: !!${W}.document.querySelector('main .md h2') && /seven to one/.test(${W}.document.querySelector('main').textContent) }))()`);
  // The header's Reload, as a person presses it.
  await p.ev(`${W}.document.getElementById('fv-reload').click(), 1`);
  await p.until(`/eight to one/.test(${W}.document.querySelector('main').textContent) && ${W}.document.getElementById('fv-chg').hidden`);
  r.after = await p.ev(`(() => { const w = ${W}; const ta = w.document.querySelector('.cmt-composer textarea');
    return { top: w.document.scrollingElement.scrollTop, old: /seven to one/.test(w.document.querySelector('main').textContent),
      composer: ta ? ta.value : null, tray: w.document.querySelectorAll('#cmt-tray .cmt-item').length,
      lost: w.document.querySelectorAll('#cmt-tray .cmt-lost').length, frameMark: w.frameMark || null, hostMark: window.hostMark || null,
      line: !w.document.getElementById('fv-chg').hidden, bodyCls: w.document.body.classList.contains('fv-chg') }; })()`);
  // Nothing in hand: the next change reloads by itself.
  await p.ev(`(() => { ${W}.document.querySelector('.cmt-composer .cmt-cancel').click(); return 1; })()`);
  write(A.md, A.mdNew.replace('eight to one', 'nine to one'));
  await p.ev(CHECK(W));
  const t0 = Date.now();
  await p.until(`/nine to one/.test(${W}.document.querySelector('main').textContent)`, 20000);
  r.auto = { ms: Date.now() - t0, line: await p.ev(`!${W}.document.getElementById('fv-chg').hidden`), frameMark: await p.ev(`${W}.frameMark || null`),
             top: await p.ev(`${W}.document.scrollingElement.scrollTop`) };
  await p.ev(`(() => { const w = ${W}; w.eval('FVC').remove(w.eval('CMTS').map(c => c.cid)); return 1; })()`);
  out[ready.name] = r;
}

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const p = await page(c);
    await markdown(p, 'window', { name: 'alone', url: A.mdUrl, expr: `typeof fvChg !== 'undefined' && document.readyState === 'complete'` }, out);
    const F = `document.querySelector('iframe').contentWindow`;
    await markdown(p, F, { name: 'framed', url: A.hostUrl, expr: `(() => { try { return ${F}.document.readyState === 'complete' && ${F}.fvChg; } catch (e) { return false; } })()` }, out);

    // An HTML page: its frame's text and scroll place.
    write(A.html, A.htmlOld);
    await p.go(A.htmlUrl, `typeof fvChg !== 'undefined' && fvChg.seen && document.querySelector('iframe.file')`);
    await p.until(`/Old page/.test(document.querySelector('iframe.file').contentDocument.body.textContent)`);
    await p.ev(`(() => { const d = document.querySelector('iframe.file').contentDocument; d.scrollingElement.scrollTop = 500; window.frameMark = 1; return 1; })()`);
    write(A.html, A.htmlNew);
    await p.ev(CHECK('window'));
    out.htmlLine = await p.ev(`!document.getElementById('fv-chg').hidden`);
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`/New page/.test(document.querySelector('iframe.file').contentDocument.body.textContent) && document.querySelector('iframe.file').contentDocument.scrollingElement.scrollTop > 0`);
    out.html = await p.ev(`({ top: document.querySelector('iframe.file').contentDocument.scrollingElement.scrollTop, mark: window.frameMark || null })`);

    // A code file: the code box's scroll place.
    write(A.code, A.codeOld);
    await p.go(A.codeUrl, `typeof fvChg !== 'undefined' && fvChg.seen && document.querySelector('.code .ln-row')`);
    await p.ev(`(() => { document.querySelector('.code').scrollTop = 400; window.frameMark = 1; return 1; })()`);
    write(A.code, A.codeNew);
    await p.ev(CHECK('window'));
    out.codeLine = await p.ev(`(() => { const n = document.getElementById('fv-chg'); return { shown: !n.hidden, top: document.querySelector('.code').scrollTop,
      below: Math.round(n.getBoundingClientRect().bottom - document.querySelector('.code').getBoundingClientRect().top) }; })()`);
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`/CHANGED/.test(document.querySelector('.code').textContent)`);
    out.code = await p.ev(`({ top: document.querySelector('.code').scrollTop, mark: window.frameMark || null })`);

    // A picture.
    write(A.png, Buffer.from(A.png1, 'hex'));
    await p.go(A.pngUrl, `typeof fvChg !== 'undefined' && fvChg.seen && document.querySelector('img.file') && document.querySelector('img.file').naturalWidth`);
    out.pngBefore = await p.ev(`document.querySelector('img.file').naturalWidth`);
    write(A.png, Buffer.from(A.png2, 'hex'));
    await p.ev(CHECK('window'));
    out.pngLine = await p.ev(`!document.getElementById('fv-chg').hidden`);
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`document.querySelector('img.file').naturalWidth === 2`);
    out.png = await p.ev(`({ w: document.querySelector('img.file').naturalWidth, line: !document.getElementById('fv-chg').hidden })`);

    // An HTML page with a form: what is typed in it holds the reload off, and a
    // Reload puts it into the new page.
    const FD = `document.querySelector('iframe.file').contentDocument`;
    const form = v => `<h1>Form ${v}</h1><form><input id="q"><input name="who"><textarea name="t"></textarea>
      <input type="checkbox" id="c"><select id="s"><option>a</option><option>b</option></select></form><div id="ed" contenteditable>edit me</div>`;
    write(A.html, form('one'));
    await p.go(A.htmlUrl, `typeof fvChg !== 'undefined' && fvChg.seen && document.querySelector('iframe.file')`);
    await p.until(`${FD}.getElementById('q') && /Form one/.test(${FD}.body.textContent)`);
    out.formFresh = await p.ev(`fvChg.frameHolds()`);
    await p.ev(`(() => { const d = ${FD}, w = d.defaultView, put = (el, f) => { f(el); el.dispatchEvent(new w.Event('input', { bubbles: true })); };
      put(d.getElementById('q'), e => { e.value = 'typed words'; }); put(d.querySelector('[name=who]'), e => { e.value = 'fab'; });
      put(d.querySelector('textarea'), e => { e.value = 'a longer note'; }); put(d.getElementById('c'), e => { e.checked = true; });
      put(d.getElementById('s'), e => { e.selectedIndex = 1; }); put(d.getElementById('ed'), e => { e.innerHTML = 'edited <b>here</b>'; }); return 1; })()`);
    out.formHolds = await p.ev(`fvChg.frameHolds()`);
    write(A.html, form('two'));
    await p.ev(CHECK('window'));
    await sleep(5600);                       // quiet longer than the idle rule
    await p.ev(`(() => { ensUpd.idleFor = () => 1e9; fvChg.maybe(); return 1; })()`);
    await sleep(800);
    out.formHeld = await p.ev(`({ old: /Form one/.test(${FD}.body.textContent), line: !document.getElementById('fv-chg').hidden })`);
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`/Form two/.test(${FD}.body.textContent) && ${FD}.getElementById('q').value`);
    out.formAfter = await p.ev(`(() => { const d = ${FD}; return { q: d.getElementById('q').value, who: d.querySelector('[name=who]').value, t: d.querySelector('textarea').value,
      c: d.getElementById('c').checked, s: d.getElementById('s').selectedIndex, ed: d.getElementById('ed').innerHTML, holds: fvChg.frameHolds(),
      line: !document.getElementById('fv-chg').hidden }; })()`);

    // A JSON file: Formatted is there while it parses, and comes back.
    const jsonState = `(() => { const seg = document.getElementById('views'); return { views: [...seg.children].map(b => b.dataset.view), segHidden: seg.hidden,
      view: CUR_VIEW, text: document.querySelector('main').textContent }; })()`;
    // Formatted is offered for a long minified line only (jsonLayout).
    const big = k => JSON.stringify(Object.fromEntries([[k, 1], ...Array.from({ length: 12 }, (_, i) => ['field' + i, 'value number ' + i])]));
    write(A.json, big('alpha'));
    await p.go(A.jsonUrl, `typeof fvChg !== 'undefined' && fvChg.seen && document.querySelector('.code .ln-row')`);
    out.json = [await p.ev(jsonState)];
    write(A.json, big('alpha').slice(0, -1) + ',');
    await p.ev(CHECK('window'));
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`!document.body.classList.contains('fv-chg') && /alpha/.test(document.querySelector('main').textContent) && CUR_VIEW === 'source'`);
    out.json.push(await p.ev(jsonState));
    write(A.json, big('beta'));
    await p.ev(CHECK('window'));
    await p.ev(`document.getElementById('fv-reload').click(), 1`);
    await p.until(`/beta/.test(document.querySelector('main').textContent)`);
    out.json.push(await p.ev(jsonState));
    await p.ev(`(() => { document.querySelector('#views [data-view=pretty]').click(); return 1; })()`);
    out.json.push(await p.ev(jsonState));

    // The first ask for the version fails: it is asked again, and a change is still seen.
    await p.ev(`(async () => { const f = window.fetch; window.fetch = (u, o) => String(u).includes('/api/file/version') ? Promise.reject(new Error('down')) : f(u, o);
      fvChg.seen = 'x'; await fvChg.start(); window.__failedSeen = fvChg.seen; window.fetch = f; return 1; })()`);
    out.retry = { failed: await p.ev(`window.__failedSeen`) };
    await p.ev(`(async () => { await fvChg.tick(); return 1; })()`);
    out.retry.after = await p.ev(`!!fvChg.seen`);
    write(A.json, big('gamma'));
    await p.ev(CHECK('window'));
    out.retry.line = await p.ev(`fvChg.pending && !document.getElementById('fv-chg').hidden`);
    await p.close();
  } finally {
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _md(word: str) -> str:
    body = "\n\n".join(f"Paragraph {i} of the plan, long enough to fill a line or two of the view." for i in range(60))
    return f"# Plan\n\n## Gearbox\n\nThe gearbox runs at {word} to one.\n\n{body}\n"


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-frl-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, files = base / "state", base / "files"
        state.mkdir()
        files.mkdir()
        for p in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard, "workspace_access_ok", lambda *a, **k: True),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        md, html, code, png = files / "plan.md", files / "page.html", files / "run.py", files / "pic.png"

        class Handler(dashboard.Handler):
            def do_GET(self):
                if self.path == "/__host":
                    # Another page framing the file view, as the dashboard does.
                    body = (f'<!doctype html><title>host</title><body style="margin:0">'
                            f'<iframe style="width:900px;height:600px;border:0" src="/fileview?path={quote(str(md))}"></iframe>').encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                super().do_GET()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        b = f"http://127.0.0.1:{cls.server.server_address[1]}"
        tall = "".join(f"<p>Line {i} of the page.</p>" for i in range(120))
        rows = "".join(f"x{i} = {i}\n" for i in range(200))
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "md": str(md), "mdOld": _md("seven"), "mdNew": _md("eight"),
                "html": str(html), "htmlOld": f"<h1>Old page</h1>{tall}", "htmlNew": f"<h1>New page</h1>{tall}",
                "code": str(code), "codeOld": rows, "codeNew": "# CHANGED\n" + rows,
                "png": str(png), "png1": PNG_1.hex(), "png2": PNG_2.hex(),
                "json": str(files / "data.json"), "jsonUrl": f"{b}/fileview?path={quote(str(files / 'data.json'))}",
                "mdUrl": f"{b}/fileview?path={quote(str(md))}", "hostUrl": f"{b}/__host",
                "htmlUrl": f"{b}/fileview?path={quote(str(html))}", "codeUrl": f"{b}/fileview?path={quote(str(code))}",
                "pngUrl": f"{b}/fileview?path={quote(str(png))}"}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_reload_button_sits_before_point_and_says_what_it_does(self):
        b = self.got["alone"]["button"]
        self.assertEqual(b["tag"], "BUTTON", "a button: Tab reaches it, Enter and Space press it")
        self.assertEqual(b["next"], "cmt-pick")
        self.assertEqual(b["label"], "Reload this file")
        self.assertEqual(b["title"], "Reload this file · keeps your place and your comments")
        self.assertGreaterEqual(b["tab"], 0)

    def _place_kept(self, name):
        g = self.got[name]
        self.assertGreater(g["before"]["top"], 500, g)
        self.assertFalse(g["before"]["line"], "no line before the file changed")
        self.assertEqual(g["before"]["marks"], 1, g)
        self.assertTrue(g["line"]["shown"], g)
        self.assertEqual(g["line"]["text"].strip(), "This file changed · Reload")
        self.assertEqual(g["line"]["below"], 0, "the line hangs right under the header")
        self.assertTrue(g["held"]["old"], "a comment being written: no reload by itself")
        a = g["after"]
        self.assertFalse(a["old"])
        self.assertEqual(a["top"], g["before"]["top"], "the scroll place is kept")
        self.assertEqual(a["composer"], "half a thought", "the comment being written stays")
        self.assertEqual(a["tray"], 2, "the unsent comments stay")
        self.assertEqual(a["lost"], 1, "the comment on words that are gone is marked")
        self.assertEqual(a["frameMark"], "kept", "the file view was not loaded again")
        self.assertEqual(a["hostMark"], "kept", "nor the page around it")
        self.assertFalse(a["line"] or a["bodyCls"], "the line goes")
        au = g["auto"]
        self.assertLess(au["ms"], 15000, au)
        self.assertFalse(au["line"], au)
        self.assertEqual(au["frameMark"], "kept", au)
        self.assertEqual(au["top"], g["before"]["top"], au)

    def test_standalone_markdown(self):
        self._place_kept("alone")

    def test_framed_markdown(self):
        self._place_kept("framed")

    def test_an_html_page(self):
        self.assertTrue(self.got["htmlLine"])
        self.assertEqual(self.got["html"], {"top": 500, "mark": 1})

    def test_a_code_file(self):
        self.assertTrue(self.got["codeLine"]["shown"])
        self.assertEqual(self.got["codeLine"]["below"], 0, "the code starts under the line")
        self.assertEqual(self.got["code"], {"top": 400, "mark": 1})

    def test_a_picture(self):
        self.assertEqual(self.got["pngBefore"], 1)
        self.assertTrue(self.got["pngLine"])
        self.assertEqual(self.got["png"], {"w": 2, "line": False})

    def test_an_html_pages_form_holds_the_reload_and_comes_back(self):
        self.assertFalse(self.got["formFresh"], "an untouched form holds nothing")
        self.assertTrue(self.got["formHolds"])
        self.assertEqual(self.got["formHeld"], {"old": True, "line": True}, "typed into the page's form: no reload by itself")
        self.assertEqual(self.got["formAfter"], {"q": "typed words", "who": "fab", "t": "a longer note", "c": True, "s": 1,
                                                 "ed": "edited <b>here</b>", "holds": True, "line": False})

    def test_json_formatted_comes_and_goes_with_whether_it_parses(self):
        valid, broken, again, pretty = self.got["json"]
        self.assertEqual(valid["views"], ["pretty", "source"])
        self.assertEqual(valid["view"], "pretty")
        self.assertEqual((broken["views"], broken["segHidden"], broken["view"]), ([], True, "source"), "no Formatted for a file that does not parse")
        self.assertEqual((again["views"], again["segHidden"], again["view"]), (["pretty", "source"], False, "source"), "back once it parses; the view stays")
        self.assertEqual(pretty["view"], "pretty")
        self.assertIn("beta", pretty["text"])

    def test_a_failed_first_version_is_asked_again(self):
        r = self.got["retry"]
        self.assertEqual(r["failed"], "", "the failed ask leaves no version")
        self.assertTrue(r["after"], "the next tick asks again")
        self.assertTrue(r["line"], "and a change after that is seen")


if __name__ == "__main__":
    unittest.main()
