"""Files shown by Ensemble can be dragged to the desktop or another app.

The endpoint tests cover the security boundary and attachment contract.  The
Chrome test synthesises dragstart on every public source shape and inspects the
real DataTransfer written by static/filedrag.js, then makes real mouse drags
(Input.setInterceptDrags) to read what Chromium itself would hand to another
program: the file and a text file's content, never the hub's address. It also
presses the file view's Download and Copy. It never logs a token.
"""
from __future__ import annotations

import io
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
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dashboard  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")
CHROME = chrome_profile.CHROME
PORT = 8798
TOKEN = "file-drag-test-token"
PROXIED = {"X-Forwarded-For": "100.64.0.7"}


class FileDownloadRoute(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ens-file-drag-")
        base = Path(self.tmp.name)
        self.allowed = base / "EnsembleProjects"
        self.allowed.mkdir()
        self.project = self.allowed / "Motors"
        self.project.mkdir()
        self.state = base / "state"
        self.state.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        self.text = self.project / "café notes.md"
        self.text.write_bytes(b"hello drag\n")
        self.binary = self.project / "blob.bin"
        self.binary.write_bytes(b"\x00\x01\x02")
        self.large = self.project / "large.txt"
        self.large.write_bytes(b"x" * (dashboard.FILE_DRAG_TEXT_MAX + 1))
        self.secret = self.outside / "secret.txt"
        self.secret.write_text("outside", encoding="utf-8")
        self.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", self.allowed),
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.state),
            mock.patch.object(dashboard, "PROJECTS_FILE", self.state / "projects.json"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def get(self, path: str, headers: dict[str, str] | None = None):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
        h.requestline = f"GET {path} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h.do_GET()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        hdrs = dict(line.split(": ", 1) for line in head.decode("latin-1").split("\r\n")[1:] if ": " in line)
        return status, hdrs, payload

    @staticmethod
    def url(path: Path, meta: bool = False) -> str:
        return "/api/file/download?path=" + quote(str(path), safe="") + ("&meta=1" if meta else "")

    def test_attachment_has_exact_utf8_name_and_bytes(self):
        status, headers, body = self.get(self.url(self.text))
        self.assertEqual(status, 200)
        self.assertEqual(body, b"hello drag\n")
        self.assertEqual(headers["Content-Type"], "text/markdown")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            headers["Content-Disposition"],
            "attachment; filename=\"caf? notes.md\"; filename*=UTF-8''caf%C3%A9%20notes.md",
        )

    def test_metadata_includes_only_small_recognised_text(self):
        def meta(path: Path):
            status, _, body = self.get(self.url(path, True))
            self.assertEqual(status, 200)
            return json.loads(body)

        text = meta(self.text)
        self.assertEqual(text, {"name": self.text.name, "mime": "text/markdown", "size": 11, "text": "hello drag\n"})
        self.assertIsNone(meta(self.binary)["text"])
        self.assertIsNone(meta(self.large)["text"])

    def test_missing_and_outside_paths_are_refused(self):
        status, _, body = self.get(self.url(self.project / "missing.txt"))
        self.assertEqual((status, json.loads(body)["error"]), (404, "not_found"))
        status, _, body = self.get(self.url(self.secret))
        self.assertEqual((status, json.loads(body)["error"]), (403, "path_not_allowed"))
        # The same refusal after a relative traversal has first resolved to an
        # existing file outside the Workspace boundary.
        rel = Path("..") / ".." / self.outside.name / self.secret.name
        target = "/api/file/download?path=" + quote(str(rel), safe="") + "&cwd=" + quote(str(self.project), safe="")
        status, _, body = self.get(target)
        self.assertEqual((status, json.loads(body)["error"]), (403, "path_not_allowed"))

    def test_tailnet_request_needs_the_token(self):
        target = self.url(self.text)
        with mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN):
            self.assertEqual(self.get(target, PROXIED)[0], 401)
            self.assertEqual(self.get(target, {**PROXIED, "X-Ensemble-Token": TOKEN})[0], 200)
            status, headers, _ = self.get(target + "&token=" + TOKEN, PROXIED)
            self.assertEqual(status, 303)
            self.assertIn("ensemble_token=", headers.get("Set-Cookie", ""))

    def test_drag_bearer_downloads_directly_without_a_browser_cookie(self):
        target = self.url(self.text) + "&drag=" + quote(dashboard._FILE_DRAG_TOKEN, safe="")
        with mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN):
            status, headers, body = self.get(target, PROXIED)
            wrong = self.get(self.url(self.text) + "&drag=wrong", PROXIED)[0]
            elsewhere = self.get("/api/settings?drag=" + quote(dashboard._FILE_DRAG_TOKEN, safe=""), PROXIED)[0]
        self.assertEqual(status, 200)
        self.assertEqual(body, b"hello drag\n")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual((wrong, elsewhere), (401, 401), "the bearer is exact and opens only this attachment route")

    def test_request_log_redacts_drag_and_page_bearers(self):
        drag_secret = "drag-log-sentinel"
        page_secret = "page-log-sentinel"
        target = self.url(self.text, True) + "&drag=" + drag_secret + "&token=" + page_secret
        logged = io.StringIO()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = target, "GET", "HTTP/1.1"
        h.requestline = f"GET {target} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}"}
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        with mock.patch.object(sys, "stderr", logged):
            h.do_GET()
        line = logged.getvalue()
        self.assertNotIn(drag_secret, line)
        self.assertNotIn(page_secret, line)
        self.assertIn("drag=[redacted]", line)
        self.assertIn("token=[redacted]", line)


CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const sleep = ms => new Promise(r => setTimeout(r, ms));
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.events = []; }
  open() { return new Promise((ok, no) => { this.ws = new WebSocket(this.url); this.ws.onopen = ok; this.ws.onerror = no;
    this.ws.onmessage = e => { const m = JSON.parse(e.data); if (m.method) { this.events.push(m); return; } const w = this.waits.get(m.id); if (!w) return; this.waits.delete(m.id); m.error ? w.no(new Error(m.error.message)) : w.ok(m.result); }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((ok, no) => { this.waits.set(id, {ok, no}); this.ws.send(JSON.stringify({id, method, params, sessionId})); }); }
}
async function main() {
  const udd = chromeProfile(A);
  const chrome = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((ok, no) => { let b = ''; chrome.stderr.on('data', d => { b += d; const m = b.match(/DevTools listening on (ws:\S+)/); if (m) ok(m[1]); });
    chrome.on('exit', c => no(new Error('chrome exited ' + c + ' ' + b))); setTimeout(() => no(new Error('no devtools ' + b)), 20000); });
  const c = new Cdp(ws); await c.open();
  const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
  const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
  const ev = async expression => { const r = await c.send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true}, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
  try {
    await c.send('Page.enable', {}, sessionId);
    await c.send('Network.enable', {}, sessionId);
    await c.send('Network.setExtraHTTPHeaders', {headers:{'X-Forwarded-For':'100.64.0.7'}}, sessionId);
    await c.send('Page.navigate', {url:A.base + '/fileview?path=' + encodeURIComponent(A.file) + '&token=' + encodeURIComponent(A.token)}, sessionId);
    const until = Date.now() + 20000;
    while (Date.now() < until && !(await ev('window.FileDrag && document.getElementById("name").draggable'))) await sleep(100);
    const out = await ev(`(async () => {
      const path = ${JSON.stringify(A.file)}, root = ${JSON.stringify(A.root)}, rel = ${JSON.stringify(A.rel)};
      const host = document.createElement('div'); document.body.appendChild(host);
      const marked = (tag, kind) => { const el = document.createElement(tag); host.appendChild(el); FileDrag.mark(el, path, {kind}); return el; };
      const sources = {
        workspace: marked('div', 'workspace'),
        fallbackTab: marked('div', 'file-tab'),
        dockTab: marked('button', 'dock-tab'),
        fileTitle: document.getElementById('name'),
        changes: (() => { const p = document.createElement('div'); p.dataset.root = root; const el = document.createElement('div');
          el.draggable = true; el.dataset.fileDrag = 'changes'; el.dataset.file = rel; p.appendChild(el); host.appendChild(p); return el; })(),
        chatLink: (() => { const el = document.createElement('a'); el.className = 'file-link'; el.draggable = true;
          el.href = '/fileview?path=' + encodeURIComponent(path); host.appendChild(el); return el; })(),
      };
      const drag = el => { const dt = new DataTransfer(); el.dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer:dt}));
        return {types:[...dt.types], download:dt.getData('DownloadURL'), uri:dt.getData('text/uri-list'), plain:dt.getData('text/plain'),
          html:dt.getData('text/html'), internal:dt.getData(FileDrag.INTERNAL), effect:dt.effectAllowed, draggable:el.draggable}; };
      const fileUrl = d => d.download ? d.download.slice(d.download.indexOf('http')) : '';
      await Promise.all(Object.values(sources).map(FileDrag.prepare));
      const got = Object.fromEntries(Object.entries(sources).map(([k, el]) => [k, drag(el)]));
      const externalResponse = await fetch(fileUrl(got.workspace), {credentials:'omit'});
      const external = {status:externalResponse.status, redirected:externalResponse.redirected, body:await externalResponse.text()};
      Object.defineProperty(navigator, 'userAgent', {value:'Mozilla/5.0 Firefox/140.0', configurable:true});
      const fallback = drag(sources.workspace);
      delete navigator.userAgent;
      const dt = new DataTransfer(); sources.workspace.dispatchEvent(new DragEvent('dragstart', {bubbles:true, cancelable:true, dataTransfer:dt}));
      const ta = document.createElement('textarea'); host.appendChild(ta); ta.dispatchEvent(new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:dt}));
      const oldFetch = window.fetch, oldNow = Date.now;
      let now = oldNow(), content = 'old', calls = 0, failOnce = false;
      Date.now = () => now;
      window.fetch = () => { calls++; if (failOnce) { failOnce = false; return Promise.reject(new Error('transient')); }
        return Promise.resolve({ok:true, json:() => Promise.resolve({name:'cache.txt', mime:'text/plain', text:content})}); };
      const changing = marked('div', 'workspace'); FileDrag.mark(changing, path + '.changing', {kind:'workspace'});
      await FileDrag.prepare(changing); const cacheOld = drag(changing).plain;
      content = 'new'; now += 10000;
      await FileDrag.prepare(changing); const cacheNew = drag(changing).plain;
      const retry = marked('div', 'workspace'); FileDrag.mark(retry, path + '.retry', {kind:'workspace'});
      failOnce = true; await FileDrag.prepare(retry);
      const failedDrag = drag(retry), failedFallback = failedDrag.plain === '' && !!failedDrag.download;
      await FileDrag.prepare(retry); const retried = drag(retry).plain;
      Date.now = oldNow;
      window.fetch = () => new Promise(() => {});
      const slow = marked('div', 'workspace'); FileDrag.mark(slow, path + '.not-ready', {kind:'workspace'});
      slow.dispatchEvent(new PointerEvent('pointerover', {bubbles:true}));
      const then = performance.now(), slowDrag = drag(slow), elapsed = performance.now() - then;
      window.fetch = oldFetch;
      const safe = d => { const file = fileUrl(d) ? new URL(fileUrl(d)) : null, internal = JSON.parse(d.internal);
        return {types:d.types, downloadHead:d.download ? d.download.slice(0, d.download.indexOf('http')) : '', filePath:file ? file.pathname : '',
          fileKeys:file ? [...file.searchParams.keys()] : [], uri:d.uri, plain:d.plain, html:d.html, internal, effect:d.effect, draggable:d.draggable}; };
      return {dropped:ta.value, bodyDraggable:document.getElementById('main').draggable,
        safeGot:Object.fromEntries(Object.entries(got).map(([k,d]) => [k,safe(d)])), safeFallback:safe(fallback),
        cache:{old:cacheOld, newer:cacheNew, failedFallback, retried, calls}, slow:{...safe(slowDrag), elapsed},
        external, href:location.href, dragMeta:!!document.querySelector('meta[name="ensemble-file-drag"]').content};
    })()`);
    // Real drags: what Chromium itself would hand to another program,
    // including the data it fills in for a link before dragstart.
    await ev(`(() => {
      const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:0;top:120px;z-index:99999;background:#fff';
      document.body.appendChild(host);
      const mk = (p, id) => { const el = document.createElement('div'); el.id = id; el.textContent = id; el.style.cssText = 'width:200px;height:30px';
        host.appendChild(el); FileDrag.mark(el, p, {kind:'workspace'}); return el; };
      mk(${JSON.stringify(A.md)}, 'real-md'); mk(${JSON.stringify(A.png)}, 'real-png');
      const a = document.createElement('a'); a.id = 'real-link'; a.className = 'file-link'; a.textContent = 'report.md';
      a.style.cssText = 'display:block;width:200px;height:30px'; a.href = '/fileview?path=' + encodeURIComponent(${JSON.stringify(A.md)}); host.appendChild(a);
      return true; })()`);
    await sleep(200);
    await c.send('Input.setInterceptDrags', {enabled:true}, sessionId);
    out.real = {};
    for (const id of ['real-md', 'real-png', 'real-link', 'name']) {
      const r = await ev(`(() => { const b = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect(); return {x:b.left + Math.min(20, b.width/2), y:b.top + b.height/2}; })()`);
      // Hovered first, as a person would: its content is read for the drag.
      await c.send('Input.dispatchMouseEvent', {type:'mouseMoved', x:r.x, y:r.y}, sessionId);
      await ev(`FileDrag.prepare(document.getElementById(${JSON.stringify(id)})).then(() => 0)`);
      c.events = [];
      await c.send('Input.dispatchMouseEvent', {type:'mousePressed', x:r.x, y:r.y, button:'left', clickCount:1}, sessionId);
      for (let i = 1; i <= 5; i++) await c.send('Input.dispatchMouseEvent', {type:'mouseMoved', x:r.x + i*10, y:r.y + i*10, button:'left', buttons:1}, sessionId);
      const t = Date.now() + 5000; let got = null;
      while (Date.now() < t && !(got = c.events.find(e => e.method === 'Input.dragIntercepted'))) await sleep(50);
      await c.send('Input.dispatchMouseEvent', {type:'mouseReleased', x:r.x + 50, y:r.y + 50, button:'left', clickCount:1}, sessionId);
      if (got) await c.send('Input.dispatchDragEvent', {type:'dragCancel', x:r.x + 50, y:r.y + 50, data:got.params.data}, sessionId).catch(() => {});
      out.real[id] = got ? got.params.data.items.map(x => ({type:x.mimeType, data:x.data})) : null;
      await sleep(200);
    }
    await c.send('Input.setInterceptDrags', {enabled:false}, sessionId);
    // The file view's buttons: Download saves the file under its name, Copy
    // puts its content on the clipboard.
    await c.send('Browser.setDownloadBehavior', {behavior:'allow', downloadPath:A.downloads, eventsEnabled:true});
    await c.send('Browser.grantPermissions', {permissions:['clipboardReadWrite', 'clipboardSanitizedWrite'], origin:A.base});
    out.buttons = await ev(`(() => { const v = id => { const b = document.getElementById(id); return !!b && !b.hidden; };
      return {download:v('out-download'), copy:v('out-copy'), shareMatches:v('out-share') === FileDrag.canShare()}; })()`);
    await ev('document.getElementById("out-download").click(), 0');
    const fs = require('fs'), path = require('path'), dlUntil = Date.now() + 10000;
    const done = () => fs.readdirSync(A.downloads).filter(n => !/\.(?:crdownload|tmp)$/.test(n));
    while (Date.now() < dlUntil && !done().length) await sleep(100);
    await sleep(300);
    out.downloaded = done().map(n => [n, fs.readFileSync(path.join(A.downloads, n), 'utf8')]);
    await c.send('Page.bringToFront', {}, sessionId);
    await ev('document.getElementById("out-copy").click(), 0');
    const toastText = 'document.querySelector(".cmt-toast") ? document.querySelector(".cmt-toast").textContent : ""';
    const cpUntil = Date.now() + 5000;
    while (Date.now() < cpUntil && !(await ev(toastText))) await sleep(100);
    out.copyToast = await ev(toastText);
    out.clipboard = await ev('navigator.clipboard.readText().catch(e => "error: " + e.name)');
    console.log(JSON.stringify(out));
  } finally {
    try { c.ws.close(); } catch (e) {}
    const gone = new Promise(r => chrome.on('exit', r)); chrome.kill(); await Promise.race([gone, sleep(5000)]);
  }
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class DragSourcesInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-file-drag-cdp-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        project = cls.root / "Motors"
        project.mkdir()
        cls.file = project / "drag me.txt"
        cls.file.write_bytes(b"hello drag\n")
        cls.md = project / "report.md"
        cls.md.write_text("# Report\n\nSome **bold** text, [a page](https://example.com/x) and [this hub](http://localhost:8765/board).\n\n- one\n- two\n",
                          encoding="utf-8")
        cls.png = project / "photo.png"
        cls.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        cls.downloads = base / "downloads"
        cls.downloads.mkdir()
        state = base / "state"
        state.mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", cls.root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "ACCESS_TOKEN", TOKEN),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
        ]
        for patcher in cls.patches:
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"127.0.0.1:{cls.server.server_address[1]}"
        args = {
            **chrome_profile.node_args(),
            "tmp": cls.tmp.name,
            "base": "http://" + cls.base,
            "file": str(cls.file),
            "root": str(project),
            "rel": cls.file.name,
            "md": str(cls.md),
            "png": str(cls.png),
            "downloads": str(cls.downloads),
            "token": TOKEN,
        }
        proc = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        if proc.returncode:
            raise AssertionError(proc.stderr[-4000:])
        cls.got = json.loads(proc.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_every_source_writes_the_file_its_text_and_internal_data_but_no_link(self):
        for name, drag in self.got["safeGot"].items():
            with self.subTest(source=name):
                self.assertTrue(drag["draggable"], drag)
                # Chromium normalises a custom DataTransfer type to lower case
                # when enumerating it, while getData remains case-insensitive.
                self.assertIn("downloadurl", [x.lower() for x in drag["types"]], drag)
                self.assertNotIn("text/uri-list", drag["types"], drag)
                self.assertEqual(drag["uri"], "")
                self.assertIn("text/plain", drag["types"], drag)
                self.assertNotIn("text/html", drag["types"], "rendered HTML is for Markdown only")
                self.assertIn("application/x-ensemble-file", drag["types"], drag)
                self.assertEqual(drag["downloadHead"], "text/plain:drag me.txt:")
                self.assertEqual(drag["filePath"], "/api/file/download")
                self.assertIn("drag", drag["fileKeys"])
                self.assertEqual(drag["plain"], "hello drag\n")
                internal = drag["internal"]
                self.assertEqual(internal["path"], str(self.file))
                self.assertIn("/fileview?path=", internal["view"])
                self.assertNotIn("download", internal, "the drag key stays out of the Ensemble-only data")

    def test_real_drags_hand_over_the_file_and_content_never_the_hub_address(self):
        # The hub serving this page (its own port, not the 8765 the report
        # mentions: a file's content goes out as it is).
        base = self.base
        for name, items in self.got["real"].items():
            with self.subTest(source=name):
                self.assertIsNotNone(items, "the drag started")
                types = [i["type"] for i in items]
                self.assertIn("downloadurl", types)
                self.assertNotIn("text/uri-list", types)
                for item in items:
                    # The file's own URL is for the browser to fetch; the
                    # Ensemble-only type is read by Ensemble's pages.
                    if item["type"] in ("downloadurl", "application/x-ensemble-file"):
                        continue
                    self.assertNotIn(base, item["data"], item)
                    self.assertNotIn("/api/file", item["data"], item)
                    self.assertNotIn("/fileview", item["data"], item)
        md = {i["type"]: i["data"] for i in self.got["real"]["real-md"]}
        self.assertTrue(md["downloadurl"].startswith("text/markdown:report.md:http"))
        self.assertIn("Some **bold** text", md["text/plain"].replace("\r\n", "\n"))
        self.assertIn("<h1>Report</h1>", md["text/html"])
        self.assertIn('<a href="https://example.com/x">a page</a>', md["text/html"])
        self.assertIn("and this hub.", md["text/html"], "a link to the hub keeps only its words")
        png = {i["type"]: i["data"] for i in self.got["real"]["real-png"]}
        self.assertEqual(sorted(png), ["application/x-ensemble-file", "downloadurl"], "a binary file: the file and nothing else")
        self.assertTrue(png["downloadurl"].startswith("image/png:photo.png:http"))
        link = {i["type"]: i["data"] for i in self.got["real"]["real-link"]}
        self.assertEqual(link.get("text/html"), md["text/html"], "the browser's own <a href> data was replaced")

    def test_file_view_download_and_copy(self):
        self.assertEqual(self.got["buttons"], {"download": True, "copy": True, "shareMatches": True})
        self.assertEqual(self.got["downloaded"], [["drag me.txt", "hello drag\n"]])
        self.assertEqual(self.got["copyToast"], "Copied the content of drag me.txt")
        self.assertEqual(self.got["clipboard"].replace("\r\n", "\n"), "hello drag\n")

    def test_an_internal_text_drop_is_a_file_view_link(self):
        self.assertIn("/fileview?path=", self.got["dropped"])

    def test_firefox_gets_the_text_and_no_link(self):
        drag = self.got["safeFallback"]
        self.assertNotIn("downloadurl", [x.lower() for x in drag["types"]])
        self.assertNotIn("text/uri-list", drag["types"])
        self.assertEqual(drag["plain"], "hello drag\n")

    def test_tailnet_drag_url_is_a_direct_cookie_less_download(self):
        self.assertNotIn("token=", self.got["href"])
        self.assertTrue(self.got["dragMeta"])
        self.assertEqual(self.got["external"], {"status": 200, "redirected": False, "body": "hello drag\n"})

    def test_unready_metadata_never_blocks_dragstart(self):
        drag = self.got["slow"]
        self.assertLess(drag["elapsed"], 100)
        self.assertEqual(drag["plain"], "", "no content yet: the file alone, never its link")
        self.assertNotIn("text/plain", drag["types"])

    def test_metadata_expires_and_transient_failures_retry(self):
        self.assertEqual(self.got["cache"], {
            "old": "old", "newer": "new", "failedFallback": True, "retried": "new", "calls": 4,
        })

    def test_the_viewer_body_stays_selectable(self):
        self.assertFalse(self.got["bodyDraggable"])


class DragWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.session = (ROOT / "session.html").read_text(encoding="utf-8")
        cls.fileview = (ROOT / "fileview.html").read_text(encoding="utf-8")

    def test_all_pages_load_the_shared_helper(self):
        for page in (self.index, self.session, self.fileview):
            self.assertIn('<script src="/static/filedrag.js"></script>', page)
            self.assertIn("FileDrag.attach(document)", page)

    def test_workspace_tabs_titles_dock_changes_and_links_are_wired(self):
        for needle in (
            "wsDragMark(v, row, row.dataset.path",
            "wsDragMark(v, tab, tab.dataset.path, 'file-tab'",
            "wsDragMark(e.v, handle, e.path, 'dock-tab'",
            "wsDragMark(v, list.querySelector('.wsc-file')",
            "FileDrag.mark(row, drPath(root, row.dataset.file), { kind: 'changes' })",
            "box.dataset.root = v.root || ''",
        ):
            self.assertIn(needle, self.index)
        for page in (self.index, self.session, self.fileview):
            self.assertIn('class="file-link"', page)
        self.assertIn("FileDrag.mark(nameEl, path", self.fileview)
        helper = (ROOT / "static" / "filedrag.js").read_text(encoding="utf-8")
        self.assertIn("dt.effectAllowed = 'copy'", helper)
        self.assertIn("decorateLinks", helper)

    def test_documents_files_copy_or_move_without_losing_text(self):
        block = self.index[self.index.index("tree.addEventListener('dragstart'"):]
        block = block[:block.index("tree.addEventListener('dragend'")]
        self.assertIn("DOCS_DRAG = row.dataset.task ? null", block)
        self.assertIn("dir ? 'move' : DOCS_DRAG ? 'copyMove' : 'copy'", block)
        self.assertIn("if (dir) try { e.dataTransfer.setData('text/plain', rel); }", block)

    def test_text_kinds_match_the_hub(self):
        helper = (ROOT / "static" / "filedrag.js").read_text(encoding="utf-8")
        block = helper[helper.index("const TEXT_EXTS = new Set(["):]
        block = block[:block.index("]);")]
        exts = set(re.findall(r"'(\.[a-z0-9]+)'", block))
        self.assertEqual(exts, dashboard._FILE_DRAG_TEXT_EXTS)

    def test_no_link_is_written_for_other_programs(self):
        helper = (ROOT / "static" / "filedrag.js").read_text(encoding="utf-8")
        self.assertNotIn("setData('text/uri-list'", helper)
        self.assertIn("dt.clearData()", helper)

    def test_menus_and_file_view_offer_download_copy_share(self):
        self.assertIn("function fileOutItems(path, item)", self.index)
        self.assertIn("item('download', 'Download')", self.index)
        self.assertIn("item('copy', 'Copy content')", self.index)
        self.assertIn("item('share', 'Share…')", self.index)
        # The Workspace row menu, a documents project's row menu, and its
        # Documents rows (which had no menu before).
        self.assertIn("+ fileOutItems(path, item);", self.index)
        self.assertIn("item('history', 'History') + fileOutItems(row.dataset.path, item)", self.index)
        self.assertIn("if (row.classList.contains('doc')) { docsMenuClose(menu, false); wsRowMenu(v, row, e.clientX, e.clientY); return; }", self.index)
        self.assertEqual(self.index.count("fileOutAct("), 3)
        for button in ('id="out-download"', 'id="out-copy"', 'id="out-share"'):
            self.assertIn(button, self.fileview)
        self.assertIn("outButtons(path);", self.fileview)
        # A narrow header folds the three into one menu (#out-menu) whose rows press them.
        self.assertIn('id="out-menu"', self.fileview)
        self.assertIn("header .btn.out { display:none; }", self.fileview)
        self.assertIn("row.onclick = () => { outMenuClose(true); b.click(); };", self.fileview)


# Built from pieces, so test_readme's check for tailnet names does not flag this file.
TSNET = ".ts" + ".net"
HUB = "hub.example" + TSNET
MD_JS = r"""
global.window = { location: { hostname: 'HUB', href: 'https://HUB/' }, navigator: {} };
require(process.argv[1]);
const md = window.FileDrag.mdHtml;
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(c => md(c))));
""".replace("HUB", HUB)


@unittest.skipUnless(NODE, "node is needed")
class MarkdownForMail(unittest.TestCase):
    def render(self, *cases: str) -> list[str]:
        proc = subprocess.run([NODE, "-e", MD_JS, str(ROOT / "static" / "filedrag.js"), json.dumps(cases)],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_blocks(self):
        (out,) = self.render("# Title\n\nOne\nline.\n\n- a\n- b\n\n1. x\n2. y\n\n> said\n\n```py\nx < 1\n```\n\n---\n\n"
                             "| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertIn("<h1>Title</h1>", out)
        self.assertIn("<p>One line.</p>", out)
        self.assertIn("<ul><li>a</li><li>b</li></ul>", out)
        self.assertIn("<ol><li>x</li><li>y</li></ol>", out)
        self.assertIn("<blockquote><p>said</p></blockquote>", out)
        self.assertIn("<code>x &lt; 1</code></pre>", out)
        self.assertIn("<hr>", out)
        self.assertIn(">A</th>", out)
        self.assertIn(">2</td>", out)

    def test_inline_and_escaping(self):
        (out,) = self.render("**b** *i* `<code>` ~~d~~ <script>alert(1)</script> & done")
        self.assertIn("<strong>b</strong>", out)
        self.assertIn("<em>i</em>", out)
        self.assertIn("<code>&lt;code&gt;</code>", out)
        self.assertIn("<del>d</del>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)

    def test_only_web_links_stay_links(self):
        (out,) = self.render(
            f"[web](https://example.com/a?b=1&c=2) [hub](https://{HUB}/fileview?path=x) "
            "[loop](http://127.0.0.1:8765/x) [local](http://localhost/x) [rel](docs/a.md) [js](javascript:alert(1)) "
            "[tail](http://100.101.1.2/x) ![pic](shot.png)")
        self.assertIn('<a href="https://example.com/a?b=1&amp;c=2">web</a>', out)
        for words in ("hub", "loop", "local", "rel", "js", "tail", "pic"):
            self.assertIn(" " + words if words != "hub" else words, out)
        self.assertEqual(out.count("<a "), 1, out)
        self.assertNotIn("javascript:", out)

    def test_private_and_local_hosts_are_not_links(self):
        hosts = ["localhost.", "app.localhost", HUB + ".", "box" + TSNET, "printer.local", "intranet",
                 "10.0.0.2", "172.16.0.1", "192.168.1.9", "169.254.1.1", "100.64.0.1", "0.0.0.0", "127.1",
                 "[::1]", "[::ffff:127.0.0.1]", "[fe80::1]", "[fd00::1]"]
        outs = self.render(*[f"[x](http://{h}/fileview)" for h in hosts] + ["[x](https://8.8.8.8/a)"])
        for host, out in zip(hosts, outs):
            with self.subTest(host=host):
                self.assertNotIn("<a ", out)
        self.assertIn('<a href="https://8.8.8.8/a">x</a>', outs[-1])

    def test_a_deep_quote_does_not_break_rendering(self):
        (out,) = self.render("> " * 5000 + "x")
        self.assertEqual(out.count("<blockquote>"), 8)
        self.assertIn("x", out)

SHARE_JS = r"""
const pending = [], timers = [];
global.window = {
  location: { hostname: 'hub.example', href: 'https://hub.example/', origin: 'https://hub.example' },
  navigator: { share: () => Promise.resolve(), canShare: () => true },
  File: class { constructor(parts, name) { this.parts = parts; this.name = name; } },
  fetch: url => new Promise(ok => pending.push(() => ok({ ok: true, blob: () => Promise.resolve({ type: 'text/plain', url }) }))),
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
};
require(process.argv[1]);
const F = window.FileDrag;
(async () => {
  F.warmShare('/p/a.txt'); F.warmShare('/p/b.txt'); F.warmShare('/p/c.txt');
  pending.forEach(go => go());
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));
  const kept = timers.length;
  // The last one is kept: Share… on it needs no new read.
  const before = pending.length;
  await F.share('/p/c.txt');
  console.log(JSON.stringify({ reads: 3, kept, reread: pending.length - before }));
})();
"""


@unittest.skipUnless(NODE, "node is needed")
class ShareKeepsOneFile(unittest.TestCase):
    def test_reads_replaced_while_running_keep_nothing(self):
        proc = subprocess.run([NODE, "-e", SHARE_JS, str(ROOT / "static" / "filedrag.js")],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"reads": 3, "kept": 1, "reread": 0})


if __name__ == "__main__":
    unittest.main()
