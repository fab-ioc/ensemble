"""Files shown by Ensemble can be dragged to the desktop or another app.

The endpoint tests cover the security boundary and attachment contract.  The
Chrome test synthesises dragstart on every public source shape and inspects the
real DataTransfer written by static/filedrag.js; it never logs a token.
"""
from __future__ import annotations

import io
import json
import os
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
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((ok, no) => { this.ws = new WebSocket(this.url); this.ws.onopen = ok; this.ws.onerror = no;
    this.ws.onmessage = e => { const m = JSON.parse(e.data), w = this.waits.get(m.id); if (!w) return; this.waits.delete(m.id); m.error ? w.no(new Error(m.error.message)) : w.ok(m.result); }; }); }
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
          internal:dt.getData(FileDrag.INTERNAL), effect:dt.effectAllowed, draggable:el.draggable}; };
      await Promise.all(Object.values(sources).map(FileDrag.prepare));
      const got = Object.fromEntries(Object.entries(sources).map(([k, el]) => [k, drag(el)]));
      const externalResponse = await fetch(got.workspace.uri, {credentials:'omit'});
      const external = {status:externalResponse.status, redirected:externalResponse.redirected, body:await externalResponse.text()};
      Object.defineProperty(navigator, 'userAgent', {value:'Mozilla/5.0 Firefox/140.0', configurable:true});
      const fallback = drag(sources.workspace);
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
      const failedDrag = drag(retry), failedFallback = failedDrag.plain === failedDrag.uri;
      await FileDrag.prepare(retry); const retried = drag(retry).plain;
      Date.now = oldNow;
      window.fetch = () => new Promise(() => {});
      const slow = marked('div', 'workspace'); FileDrag.mark(slow, path + '.not-ready', {kind:'workspace'});
      slow.dispatchEvent(new PointerEvent('pointerover', {bubbles:true}));
      const then = performance.now(), slowDrag = drag(slow), elapsed = performance.now() - then;
      window.fetch = oldFetch;
      const safe = d => { const uri = new URL(d.uri), internal = JSON.parse(d.internal); delete internal.download;
        return {types:d.types, downloadHead:d.download ? d.download.slice(0, d.download.indexOf('http')) : '', uriPath:uri.pathname,
          uriKeys:[...uri.searchParams.keys()], plain:d.plain === d.uri ? '[uri]' : d.plain, internal, effect:d.effect, draggable:d.draggable}; };
      return {dropped:ta.value, bodyDraggable:document.getElementById('main').draggable,
        safeGot:Object.fromEntries(Object.entries(got).map(([k,d]) => [k,safe(d)])), safeFallback:safe(fallback),
        cache:{old:cacheOld, newer:cacheNew, failedFallback, retried, calls}, slow:{...safe(slowDrag), elapsed},
        external, href:location.href, dragMeta:!!document.querySelector('meta[name="ensemble-file-drag"]').content};
    })()`);
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
        args = {
            **chrome_profile.node_args(),
            "tmp": cls.tmp.name,
            "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
            "file": str(cls.file),
            "root": str(project),
            "rel": cls.file.name,
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

    def test_every_source_writes_file_url_text_and_internal_data(self):
        for name, drag in self.got["safeGot"].items():
            with self.subTest(source=name):
                self.assertTrue(drag["draggable"], drag)
                # Chromium normalises a custom DataTransfer type to lower case
                # when enumerating it, while getData remains case-insensitive.
                self.assertIn("downloadurl", [x.lower() for x in drag["types"]], drag)
                self.assertIn("text/uri-list", drag["types"], drag)
                self.assertIn("text/plain", drag["types"], drag)
                self.assertIn("application/x-ensemble-file", drag["types"], drag)
                self.assertEqual(drag["downloadHead"], "text/plain:drag me.txt:")
                self.assertEqual(drag["uriPath"], "/api/file/download")
                self.assertIn("drag", drag["uriKeys"])
                self.assertEqual(drag["plain"], "hello drag\n")
                internal = drag["internal"]
                self.assertEqual(internal["path"], str(self.file))
                self.assertIn("/fileview?path=", internal["view"])

    def test_an_internal_text_drop_is_a_file_view_link(self):
        self.assertIn("/fileview?path=", self.got["dropped"])

    def test_firefox_falls_back_to_url_and_text(self):
        drag = self.got["safeFallback"]
        self.assertNotIn("downloadurl", [x.lower() for x in drag["types"]])
        self.assertIn("text/uri-list", drag["types"])
        self.assertEqual(drag["plain"], "hello drag\n")

    def test_tailnet_drag_url_is_a_direct_cookie_less_download(self):
        self.assertNotIn("token=", self.got["href"])
        self.assertTrue(self.got["dragMeta"])
        self.assertEqual(self.got["external"], {"status": 200, "redirected": False, "body": "hello drag\n"})

    def test_unready_metadata_never_blocks_dragstart(self):
        drag = self.got["slow"]
        self.assertLess(drag["elapsed"], 100)
        self.assertEqual(drag["plain"], "[uri]")

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


if __name__ == "__main__":
    unittest.main()
