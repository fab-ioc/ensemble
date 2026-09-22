"""An open tab runs the current page after a merge, without a reload by hand.

The hub stamps every served page file (PAGE_FILES): `/api/config` lists the
stamps under `pages`, a page or shared-script response carries its own in
`X-Ensemble-Stamp`, and a served page gets every stamp written into its
`<meta name="ensemble-pages">`. The pages' shared "Page update" block compares
the two on the page's own poll and reloads where it was, or shows one line
("Ensemble was updated · Reload") while the person is in the middle of
something. Checked here:

* the hub: the stamps, cached by mtime and size, change with a byte; the
  config field; the header on pages and scripts and on nothing else; the meta
  filled at serve time and left alone in a page without it;
* the three pages carry the same block, the same line style and the meta;
* the rules, the block run in Node over a fake page: no change, nothing; a
  change while idle reloads with the place kept; typed text, a composer, a
  selection, an open dialog, a held pointer or a frame in hand each show the
  line instead; the line's click reloads; idle again for 60 s reloads; hidden
  reloads unless text is in hand; a host about to reload holds its frame; the
  Update button's hold stops it; a changed file the page does not load is
  ignored;
* headless Chrome over CDP against a hub serving a copy of the pages: editing
  a byte of session.html, index.html, fileview.html, hl.js or comments.js
  makes the open tab reload when idle and not while the box holds text, and
  each page comes back where it was. Skipped without Chrome or Node.
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
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402

NODE = shutil.which("node")
CHROME = next((p for p in (
    os.environ.get("ENSEMBLE_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
) if p and Path(p).exists()), "")
PAGES = ("index.html", "session.html", "fileview.html")
BEGIN = "// ---- Page update: begin shared block"
END = "// ---- Page update: end shared block"


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def shared_block(src: str) -> str:
    i = src.index(BEGIN)
    return src[i:src.index("\n", src.index(END, i)) + 1]


def line_css(src: str) -> str:
    i = src.index("  #ens-upd {")
    return src[i:src.index("\n", src.index("#ens-upd button:focus-visible", i)) + 1]


# ---- the hub ------------------------------------------------------------------

class HubStamps(unittest.TestCase):
    """A copy of the page files in a temp folder stands in for the repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "static").mkdir()
        for rel in dashboard.PAGE_FILES:
            (self.dir / rel).write_bytes(b"<!doctype html>\n" + dashboard.PAGE_META + b"\n<p>" + rel.encode() + b"</p>\n"
                                         if rel.endswith(".html") else b"// " + rel.encode() + b"\n")
        (self.dir / "static" / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)
        p = mock.patch.object(dashboard, "STATIC_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        dashboard._STAMP_CACHE.clear()
        self.addCleanup(dashboard._STAMP_CACHE.clear)

    def get(self, path: str, headers=None):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
        h.requestline = f"GET {path} HTTP/1.1"
        h.headers = {"Host": "127.0.0.1:8798", **(headers or {})}
        h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", 8798))
        h.log_message = lambda *a: None
        h.do_GET()
        head, _, body = h.wfile.getvalue().partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        hdrs = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        return int(lines[0].split(" ", 2)[1]), hdrs, body

    def test_every_page_file_has_a_twelve_hex_stamp_that_a_byte_changes(self):
        stamps = dashboard.page_stamps()
        self.assertEqual(set(stamps), set(dashboard.PAGE_FILES))
        for v in stamps.values():
            self.assertRegex(v, r"^[0-9a-f]{12}$")
        self.assertEqual(len(set(stamps.values())), len(stamps), "different files, different stamps")
        f = self.dir / "session.html"
        old = f.read_bytes()
        time.sleep(0.02)
        f.write_bytes(old + b"<!-- x -->")
        after = dashboard.page_stamps()
        self.assertNotEqual(after["session.html"], stamps["session.html"])
        self.assertEqual({k: v for k, v in after.items() if k != "session.html"},
                         {k: v for k, v in stamps.items() if k != "session.html"})
        f.write_bytes(old)
        self.assertEqual(dashboard.page_stamps()["session.html"], stamps["session.html"], "the bytes back, the stamp back")

    def test_a_stamp_is_hashed_once_per_mtime_and_size(self):
        with mock.patch.object(dashboard, "_stamp_of", wraps=dashboard._stamp_of) as h:
            dashboard.page_stamps()
            dashboard.page_stamps()
            dashboard.page_stamps()
        self.assertEqual(h.call_count, len(dashboard.PAGE_FILES))

    def test_a_missing_file_stamps_empty(self):
        (self.dir / "static" / "attach.js").unlink()
        self.assertEqual(dashboard.page_stamps()["static/attach.js"], "")

    def test_config_lists_the_stamps(self):
        status, hdrs, body = self.get("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["pages"], dashboard.page_stamps())
        self.assertNotIn("x-ensemble-stamp", hdrs, "the config is not a page")

    def test_a_page_carries_its_stamp_and_every_stamp_in_its_meta(self):
        for path, rel in (("/session?room=room-1", "session.html"), ("/", "index.html"),
                          ("/index.html", "index.html"), ("/fileview?path=x", "fileview.html")):
            status, hdrs, body = self.get(path)
            self.assertEqual(status, 200, path)
            stamps = dashboard.page_stamps()
            self.assertEqual(hdrs.get("x-ensemble-stamp"), stamps[rel], path)
            m = re.search(rb'<meta name="ensemble-pages" content="([^"]*)">', body)
            self.assertTrue(m, path)
            self.assertEqual(dict(kv.split("=") for kv in m.group(1).decode().split(" ")), stamps, path)
            self.assertEqual(hdrs.get("content-length"), str(len(body)), "the length is the served body's")
            self.assertEqual(hdrs.get("cache-control"), "no-store")

    def test_the_header_stamps_the_bytes_served_not_the_meta_filled_page(self):
        status, hdrs, body = self.get("/session?room=room-1")
        self.assertEqual(hdrs["x-ensemble-stamp"], dashboard._stamp_of((self.dir / "session.html").read_bytes()))
        self.assertNotEqual(hdrs["x-ensemble-stamp"], dashboard._stamp_of(body))

    def test_a_shared_script_carries_its_stamp(self):
        for rel in ("static/hl.js", "static/comments.js", "static/attach.js"):
            status, hdrs, body = self.get("/" + rel)
            self.assertEqual(status, 200)
            self.assertEqual(hdrs.get("x-ensemble-stamp"), dashboard.page_stamps()[rel])
            self.assertEqual(body, (self.dir / rel).read_bytes(), "a script is served as it is")

    def test_other_files_carry_no_stamp(self):
        status, hdrs, body = self.get("/static/pic.png")
        self.assertEqual(status, 200)
        self.assertNotIn("x-ensemble-stamp", hdrs)
        self.assertEqual(body, (self.dir / "static" / "pic.png").read_bytes())

    def test_a_page_without_the_meta_is_served_as_it_is(self):
        (self.dir / "fileview.html").write_bytes(b"<!doctype html><p>old</p>")
        status, hdrs, body = self.get("/fileview?path=x")
        self.assertEqual(body, b"<!doctype html><p>old</p>")
        self.assertEqual(hdrs.get("x-ensemble-stamp"), dashboard.page_stamps()["fileview.html"])


# ---- the pages ----------------------------------------------------------------

class SharedBlock(unittest.TestCase):
    def test_the_three_copies_match(self):
        blocks = {n: shared_block(read(n)) for n in PAGES}
        self.assertEqual(blocks["session.html"], blocks["index.html"], "session.html's update block drifted")
        self.assertEqual(blocks["fileview.html"], blocks["index.html"], "fileview.html's update block drifted")

    def test_the_line_style_matches_and_uses_tokens_only(self):
        css = {n: line_css(read(n)) for n in PAGES}
        self.assertEqual(css["session.html"], css["index.html"])
        self.assertEqual(css["fileview.html"], css["index.html"])
        self.assertNotRegex(css["index.html"], r"#[0-9a-fA-F]{3,6}\b", "no raw hex")
        self.assertNotIn("opacity", css["index.html"])
        for tok in ("--surface", "--border", "--fg", "--link", "--e-200", "--fs-200", "--lh-200", "--s-100", "--s-300", "--focus-ring"):
            self.assertIn(f"var({tok})", css["index.html"], tok)

    def test_every_page_has_the_meta_and_a_poll_that_asks(self):
        for n in PAGES:
            src = read(n)
            self.assertEqual(src.count('<meta name="ensemble-pages" content="">'), 1, n)
            self.assertIn("ensUpd.poll", src, n)
            self.assertIn("window.ensUpdBusy = ", src, n)
            self.assertIn("window.ensUpdPlace = ", src, n)

    def test_the_hub_names_the_files_the_pages_load(self):
        for n in PAGES:
            for src in re.findall(r'<script src="/static/([^"]+)"', read(n)):
                self.assertIn("static/" + src, dashboard.PAGE_FILES, f"{n} loads {src}")

    def test_the_update_button_path_reloads_once(self):
        src = read("index.html")
        fn = src[src.index("async function triggerUpdate("):src.index("function renderRuntimeConfig(")]
        self.assertIn("ensUpd.hold = true", fn)
        self.assertNotIn("location.reload()", fn, "the update block's reload, which keeps the place")
        self.assertIn("ensUpd.reload()", fn)
        self.assertEqual(fn.count("ensUpd.hold = false"), 3, "refused, timed out, reloading")
        self.assertIn("if (UPDATE_IN_PROGRESS || ensUpd.reloading) return;", fn)


# ---- the rules, in Node --------------------------------------------------------

RULES_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let now = 1_000_000;
// A page small enough to fake: a document with fields, a selection, a dialog,
// frames, and a location that records reloads.
function fakePage({ path = '/session', search = '?room=room-1', meta = 'session.html=aaa static/comments.js=bbb static/attach.js=ccc', scripts = ['/static/comments.js', '/static/attach.js'], frames = [], parent = null } = {}) {
  const listeners = {};
  const body = { children: [], appendChild(n) { this.children.push(n); n.isConnected = true; } };
  const el = (tag) => ({ tag, isConnected: true, value: '', attrs: {}, inDialog: false, matches(sel) { return sel.split(',').some(s => s.trim().startsWith(tag)); },
    closest(sel) { return sel === 'dialog' && this.inDialog ? doc.dialog : null; },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; }, addEventListener(t, f) { this.on = this.on || {}; this.on[t] = f; },
    querySelector() { return this; }, set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; } });
  const doc = {
    hidden: false, body, dialogOpen: false, dialog: { get open() { return doc.dialogOpen; } },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    fire(t, e) { (listeners[t] || []).forEach(f => f(e || {})); },
    querySelector(sel) {
      if (sel === 'meta[name="ensemble-pages"]') return { getAttribute: () => meta };
      if (sel === 'dialog[open]') return doc.dialogOpen ? {} : null;
      return null;
    },
    querySelectorAll(sel) {
      if (sel.startsWith('script')) return scripts.map(s => ({ getAttribute: () => s }));
      if (sel === 'iframe') return frames.map(w => ({ contentWindow: w }));
      return [];
    },
    createElement: el,
  };
  const store = {};
  const ctx = {
    console, JSON, Math, Object, Set, Date: { now: () => now },
    setInterval: (f, ms) => { ctx.timers.push({ f, ms }); return ctx.timers.length; }, clearInterval: () => {},
    document: doc,
    location: { pathname: path, search, reloads: 0, replaced: '', reload() { this.reloads++; }, replace(u) { this.replaced = u; this.reloads++; } },
    sessionStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } },
    selection: { isCollapsed: true, toString: () => '' },
    getSelection: () => ctx.selection,
    fetch: async () => ({ ok: true, json: async () => ({ pages: ctx.onDisk }) }),
    onDisk: null, timers: [], store,
  };
  ctx.window = ctx; ctx.parent = parent || ctx;
  vm.createContext(ctx);
  vm.runInContext(code + '\nglobalThis.ensUpd = ensUpd;', ctx);
  ctx.el = el;
  return ctx;
}
const tick = async (p, n = 1) => { for (let i = 0; i < n; i++) { await p.ensUpd.poll(); await new Promise(r => setImmediate(r)); } };
const out = {};
const same = { 'session.html': 'aaa', 'static/comments.js': 'bbb', 'static/attach.js': 'ccc', 'index.html': 'iii', 'fileview.html': 'fff', 'static/hl.js': 'hhh' };
const changed = { ...same, 'session.html': 'aab' };

(async () => {
  // Nothing changed: no reload, no line, and no fetch before 30 s.
  let p = fakePage(); p.onDisk = same; p.fetches = 0;
  const f0 = p.fetch; p.fetch = async () => { p.fetches++; return f0(); };
  await tick(p); out.noFetchYet = p.fetches;
  now += 30_000; await tick(p, 3);
  out.unchanged = { fetches: p.fetches, reloads: p.location.reloads, pending: p.ensUpd.pending, line: !!p.ensUpd.line };
  out.loaded = p.ensUpd.loaded; out.deps = p.ensUpd.deps();

  // A change while idle (no input for 5 s): reload, with the page's place kept.
  p = fakePage(); p.onDisk = changed; p.ensUpdPlace = () => ({ key: 'm7', off: 12, stick: false });
  now += 30_000; await tick(p);
  out.idleReload = { reloads: p.location.reloads, line: !!p.ensUpd.line, saved: JSON.parse(p.store['cd-upd-place:/session?room=room-1']) };
  // The next page finds it once, then it is gone.
  out.restored = [p.ensUpd.restore(), p.ensUpd.restore()];

  // A change 2 s after the last input: not yet; 5 s on: reload.
  p = fakePage(); p.onDisk = changed; now += 30_000; p.ensUpd.lastInput = now - 2_000;
  await tick(p); out.recentInput = { reloads: p.location.reloads, line: !!p.ensUpd.line, pending: p.ensUpd.pending };
  now += 3_100; p.ensUpd.maybe(); out.quietAgain = p.location.reloads;

  // Text typed and still there: the line, no reload; cleared and idle 60 s: reload.
  p = fakePage(); p.onDisk = changed;
  const box = p.el('textarea'); box.value = 'half a thou';
  p.document.fire('input', { target: box });
  now += 30_000; p.ensUpd.lastInput = now - 10_000; await tick(p);
  out.typed = { reloads: p.location.reloads, line: p.ensUpd.line && p.ensUpd.line.html, busy: p.ensUpd.busy() };
  box.value = '';
  now += 10_000; p.ensUpd.maybe(); out.typedClearedSoon = p.location.reloads;
  now += 50_000; p.ensUpd.maybe(); out.typedClearedIdle = p.location.reloads;

  // A one-line field typed in and left (blur fires change): done with, not in hand;
  // a textarea holds until it is empty.
  p = fakePage(); p.onDisk = changed;
  const one = p.el('input'); one.value = 'opus'; p.document.fire('input', { target: one }); p.document.fire('change', { target: one });
  now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p); out.oneLineLeft = p.location.reloads;
  p = fakePage(); p.onDisk = changed;
  const ta = p.el('textarea'); ta.value = 'a longer thought'; p.document.fire('input', { target: ta }); p.document.fire('change', { target: ta });
  now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p); out.textareaLeft = { reloads: p.location.reloads, line: !!p.ensUpd.line };

  // What the page names as in hand (the message box, a composer).
  p = fakePage(); p.onDisk = changed; p.ensUpdBusy = () => true; now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p);
  out.pageBusy = { reloads: p.location.reloads, line: !!p.ensUpd.line };
  p.ensUpdBusy = () => false; p.ensUpd.maybe(); out.pageFree = p.location.reloads;

  // A selection, a dialog, a held pointer: each holds the reload.
  for (const [name, hold, free] of [
    ['selection', q => { q.selection = { isCollapsed: false, toString: () => 'words' }; }, q => { q.selection = { isCollapsed: true, toString: () => '' }; }],
    ['dialog', q => { q.document.dialogOpen = true; }, q => { q.document.dialogOpen = false; }],
    ['held', q => q.document.fire('pointerdown'), q => q.document.fire('pointerup')],
    ['drag', q => q.document.fire('dragstart'), q => q.document.fire('dragend')],
  ]) {
    p = fakePage(); p.onDisk = changed; hold(p); now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p);
    const heldR = p.location.reloads, line = !!p.ensUpd.line;
    free(p); p.ensUpd.lastInput = now - 100_000; p.ensUpd.maybe();
    out[name] = { heldR, line, freed: p.location.reloads };
  }

  // The line's click reloads at once, text in hand or not.
  p = fakePage(); p.onDisk = changed; p.ensUpdBusy = () => true; now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p);
  p.ensUpd.line.on.click(); out.click = p.location.reloads;

  // Hidden: reload at once, unless text is in hand.
  p = fakePage(); p.onDisk = changed; p.document.dialogOpen = true; now += 30_000; await tick(p);
  out.hiddenBusy = p.location.reloads; p.document.hidden = true; p.document.fire('visibilitychange'); out.hiddenReload = p.location.reloads;
  p = fakePage(); p.onDisk = changed; p.ensUpdBusy = () => true; p.document.hidden = true; now += 30_000; await tick(p);
  out.hiddenTyped = p.location.reloads;

  // A frame in hand holds its host; a host about to reload holds its frame.
  const host = fakePage({ path: '/', search: '', meta: 'index.html=iii static/hl.js=hhh static/comments.js=bbb static/attach.js=ccc', scripts: ['/static/hl.js', '/static/comments.js', '/static/attach.js'] });
  const frame = fakePage({ parent: host });
  host.document.querySelectorAll = (sel) => sel === 'iframe' ? [{ contentWindow: frame }] : sel.startsWith('script') ? ['/static/hl.js', '/static/comments.js', '/static/attach.js'].map(s => ({ getAttribute: () => s })) : [];
  host.onDisk = { ...same, 'index.html': 'iij' }; frame.onDisk = host.onDisk;
  frame.ensUpdBusy = () => true;
  now += 30_000; host.ensUpd.lastInput = now - 100_000; frame.ensUpd.lastInput = now - 100_000;
  await tick(host); out.hostHeldByFrame = { reloads: host.location.reloads, line: !!host.ensUpd.line };
  frame.ensUpdBusy = () => false; host.ensUpd.maybe(); out.hostFreed = host.location.reloads;
  // The frame's own file changed too, and the host's: the frame waits for the host.
  const host2 = fakePage({ path: '/', search: '', meta: 'index.html=iii static/hl.js=hhh static/comments.js=bbb static/attach.js=ccc', scripts: ['/static/hl.js', '/static/comments.js', '/static/attach.js'] });
  const frame2 = fakePage({ parent: host2 });
  frame2.onDisk = { ...changed, 'index.html': 'iij' }; host2.onDisk = frame2.onDisk;
  now += 30_000; frame2.ensUpd.lastInput = now - 100_000; await tick(frame2);
  out.frameDefers = { reloads: frame2.location.reloads, pending: frame2.ensUpd.pending };
  // Only the frame's file changed: it reloads itself.
  const host3 = fakePage({ path: '/', search: '', meta: 'index.html=iii static/hl.js=hhh static/comments.js=bbb static/attach.js=ccc', scripts: ['/static/hl.js', '/static/comments.js', '/static/attach.js'] });
  const frame3 = fakePage({ parent: host3 });
  frame3.onDisk = changed; host3.onDisk = changed;
  now += 30_000; frame3.ensUpd.lastInput = now - 100_000; await tick(frame3); await tick(host3);
  out.frameAlone = { frame: frame3.location.reloads, host: host3.location.reloads };
  // The frame's own input counts as the host's.
  const host4 = fakePage({ path: '/', search: '', meta: 'index.html=iii static/hl.js=hhh static/comments.js=bbb static/attach.js=ccc', scripts: ['/static/hl.js', '/static/comments.js', '/static/attach.js'] });
  const frame4 = fakePage({ parent: host4 });
  host4.document.querySelectorAll = (sel) => sel === 'iframe' ? [{ contentWindow: frame4 }] : sel.startsWith('script') ? ['/static/hl.js', '/static/comments.js', '/static/attach.js'].map(s => ({ getAttribute: () => s })) : [];
  host4.onDisk = { ...same, 'index.html': 'iij' };
  now += 30_000; host4.ensUpd.lastInput = now - 100_000; frame4.ensUpd.lastInput = now - 1_000; await tick(host4);
  out.frameInput = host4.location.reloads; now += 6_000; host4.ensUpd.maybe(); out.frameQuiet = host4.location.reloads;

  // The Update button's hold; a reload already under way is one reload.
  p = fakePage(); p.onDisk = changed; p.ensUpd.hold = true; now += 30_000; await tick(p); out.updateHold = p.location.reloads;
  p.ensUpd.hold = false; p.ensUpd.maybe(); p.ensUpd.maybe(); p.ensUpd.reload(); out.once = p.location.reloads;

  // A file this page does not load changed: nothing. A page served without stamps: nothing.
  p = fakePage(); p.onDisk = { ...same, 'index.html': 'iij', 'static/hl.js': 'hhi' }; now += 30_000; await tick(p);
  out.otherFile = { reloads: p.location.reloads, pending: p.ensUpd.pending };
  p = fakePage({ meta: '' }); p.onDisk = changed; now += 30_000; await tick(p);
  out.noStamps = { reloads: p.location.reloads, pending: p.ensUpd.pending };
  // A url to come back to (fileview) is followed instead of a plain reload.
  p = fakePage({ path: '/fileview', search: '?path=a', meta: 'fileview.html=fff static/hl.js=hhh', scripts: ['/static/hl.js'] });
  p.onDisk = { ...same, 'static/hl.js': 'hhi' }; p.ensUpdPlace = () => ({ url: '/fileview?path=a&st=%7B%7D' }); now += 30_000; await tick(p);
  out.url = { replaced: p.location.replaced, reloads: p.location.reloads };
  // The poll is one request at a time and every 30 s, whatever the tick.
  p = fakePage(); p.onDisk = same; p.fetches = 0; const f1 = p.fetch; p.fetch = async () => { p.fetches++; return f1(); };
  now += 30_000; await Promise.all([p.ensUpd.poll(), p.ensUpd.poll()]); out.singleFlight = p.fetches;
  now += 29_000; await tick(p); now += 1_000; await tick(p); out.cadence = p.fetches;

  // A one-line field in a dialog (a task's title) left for another field (blur
  // fires change) is still in hand: the line, no reload, hidden or not; the
  // dialog closed, the words are let go and the hidden tab reloads.
  p = fakePage(); p.onDisk = changed;
  const title = p.el('input'); title.inDialog = true; title.value = 'a task title';
  p.document.fire('input', { target: title }); p.document.fire('change', { target: title });
  p.document.dialogOpen = true; now += 30_000; p.ensUpd.lastInput = now - 100_000; await tick(p);
  out.dialogField = { holding: p.ensUpd.holdingText(), line: !!p.ensUpd.line, reloads: p.location.reloads };
  p.document.hidden = true; p.document.fire('visibilitychange'); out.dialogFieldHidden = p.location.reloads;
  p.document.dialogOpen = false; p.ensUpd.maybe(); out.dialogClosed = { holding: p.ensUpd.holdingText(), reloads: p.location.reloads };
  // The same field outside a dialog: done with on change (the earlier case), even hidden.
  p = fakePage(); p.onDisk = changed; p.document.hidden = true;
  const one2 = p.el('input'); one2.value = 'opus'; p.document.fire('input', { target: one2 }); p.document.fire('change', { target: one2 });
  now += 30_000; await tick(p); out.oneLineHidden = p.location.reloads;

  // A host reloading saves its frames' places with its own (each under its own
  // url); a place kept longer than 30 min is not put back.
  const host5 = fakePage({ path: '/', search: '', meta: 'index.html=iii static/hl.js=hhh static/comments.js=bbb static/attach.js=ccc', scripts: ['/static/hl.js', '/static/comments.js', '/static/attach.js'] });
  const frame5 = fakePage({ parent: host5 });
  host5.document.querySelectorAll = (sel) => sel === 'iframe' ? [{ contentWindow: frame5 }] : sel.startsWith('script') ? ['/static/hl.js', '/static/comments.js', '/static/attach.js'].map(s => ({ getAttribute: () => s })) : [];
  host5.ensUpdPlace = () => ({ proj: 'p1', tab: 'tasks' }); frame5.ensUpdPlace = () => ({ key: 'm12', off: 3, stick: false });
  host5.onDisk = { ...same, 'index.html': 'iij' }; frame5.onDisk = host5.onDisk;
  now += 30_000; host5.ensUpd.lastInput = now - 100_000; frame5.ensUpd.lastInput = now - 100_000; await tick(host5);
  out.hostSaves = { host: JSON.parse(host5.store['cd-upd-place:/']), frame: JSON.parse(frame5.store['cd-upd-place:/session?room=room-1']), reloads: [host5.location.reloads, frame5.location.reloads] };
  out.frameRestored = frame5.ensUpd.restore();
  frame5.store['cd-upd-place:/session?room=room-1'] = JSON.stringify({ key: 'm12', off: 3, stick: false, at: now - 31 * 60_000 });
  out.staleRestore = [frame5.ensUpd.restore(), 'cd-upd-place:/session?room=room-1' in frame5.store];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, "-e", RULES_JS], input=json.dumps({"code": shared_block(read("session.html"))}),
                             capture_output=True, encoding="utf-8", timeout=60)
        assert out.returncode == 0, out.stderr
        cls.got = json.loads(out.stdout)

    def test_nothing_changed_nothing_happens_and_no_request_before_30s(self):
        self.assertEqual(self.got["noFetchYet"], 0)
        self.assertEqual(self.got["unchanged"], {"fetches": 1, "reloads": 0, "pending": False, "line": False})
        self.assertEqual(self.got["loaded"], {"session.html": "aaa", "static/comments.js": "bbb", "static/attach.js": "ccc"})
        self.assertEqual(self.got["deps"], ["session.html", "static/comments.js", "static/attach.js"])

    def test_a_change_while_idle_reloads_with_the_place_kept(self):
        got = self.got["idleReload"]
        self.assertIsInstance(got["saved"].pop("at"), int, "when it was kept")
        self.assertEqual(got, {"reloads": 1, "line": False, "saved": {"key": "m7", "off": 12, "stick": False}})
        first, second = self.got["restored"]
        self.assertIsInstance(first.pop("at"), int)
        self.assertEqual((first, second), ({"key": "m7", "off": 12, "stick": False}, None))

    def test_a_dialogs_field_holds_while_the_dialog_is_open_blurred_or_hidden(self):
        self.assertEqual(self.got["dialogField"], {"holding": True, "line": True, "reloads": 0})
        self.assertEqual(self.got["dialogFieldHidden"], 0, "hidden with a title typed: no reload")
        self.assertEqual(self.got["dialogClosed"], {"holding": False, "reloads": 1})
        self.assertEqual(self.got["oneLineHidden"], 1, "a search box left with words, outside a dialog, is done with")

    def test_a_host_saves_its_frames_places_and_a_stale_place_is_dropped(self):
        got = self.got["hostSaves"]
        self.assertEqual(got["reloads"], [1, 0])
        self.assertIsInstance(got["host"].pop("at"), int)
        self.assertIsInstance(got["frame"].pop("at"), int)
        self.assertEqual(got["host"], {"proj": "p1", "tab": "tasks"})
        self.assertEqual(got["frame"], {"key": "m12", "off": 3, "stick": False})
        self.assertEqual(self.got["frameRestored"]["key"], "m12")
        self.assertEqual(self.got["staleRestore"], [None, False], "dropped, and gone from the store")

    def test_input_in_the_last_five_seconds_waits_without_a_line(self):
        self.assertEqual(self.got["recentInput"], {"reloads": 0, "line": False, "pending": True})
        self.assertEqual(self.got["quietAgain"], 1)

    def test_typed_text_shows_the_line_and_reloads_once_cleared_and_idle_60s(self):
        self.assertEqual(self.got["typed"], {"reloads": 0, "line": "Ensemble was updated · <button type=\"button\">Reload</button>", "busy": True})
        self.assertEqual(self.got["typedClearedSoon"], 0, "10 s of quiet is not idle once the line shows")
        self.assertEqual(self.got["typedClearedIdle"], 1)

    def test_a_one_line_field_left_with_words_is_done_with_a_textarea_is_not(self):
        self.assertEqual(self.got["oneLineLeft"], 1)
        self.assertEqual(self.got["textareaLeft"], {"reloads": 0, "line": True})

    def test_what_the_page_names_as_in_hand_holds_the_reload(self):
        self.assertEqual(self.got["pageBusy"], {"reloads": 0, "line": True})
        self.assertEqual(self.got["pageFree"], 1)

    def test_a_selection_a_dialog_a_held_pointer_or_a_drag_holds_it(self):
        for k in ("selection", "dialog", "held", "drag"):
            self.assertEqual(self.got[k], {"heldR": 0, "line": True, "freed": 1}, k)

    def test_the_line_click_reloads_at_once(self):
        self.assertEqual(self.got["click"], 1)

    def test_hidden_reloads_unless_text_is_in_hand(self):
        self.assertEqual(self.got["hiddenBusy"], 0)
        self.assertEqual(self.got["hiddenReload"], 1)
        self.assertEqual(self.got["hiddenTyped"], 0)

    def test_a_host_and_its_frame_reload_as_one(self):
        self.assertEqual(self.got["hostHeldByFrame"], {"reloads": 0, "line": True})
        self.assertEqual(self.got["hostFreed"], 1)
        self.assertEqual(self.got["frameDefers"], {"reloads": 0, "pending": True})
        self.assertEqual(self.got["frameAlone"], {"frame": 1, "host": 0})
        self.assertEqual(self.got["frameInput"], 0)
        self.assertEqual(self.got["frameQuiet"], 1)

    def test_the_update_buttons_hold_and_one_reload_only(self):
        self.assertEqual(self.got["updateHold"], 0)
        self.assertEqual(self.got["once"], 1)

    def test_other_files_and_missing_stamps_are_ignored(self):
        self.assertEqual(self.got["otherFile"], {"reloads": 0, "pending": False})
        self.assertEqual(self.got["noStamps"], {"reloads": 0, "pending": False})

    def test_a_url_to_come_back_to_is_followed(self):
        self.assertEqual(self.got["url"], {"replaced": "/fileview?path=a&st=%7B%7D", "reloads": 1})

    def test_one_request_at_a_time_every_30s(self):
        self.assertEqual(self.got["singleFlight"], 1)
        self.assertEqual(self.got["cadence"], 2)


# ---- headless Chrome over CDP ---------------------------------------------------

CDP_JS = r"""
// Drive headless Chrome over CDP: open each page on the hub, edit a byte of a
// file it runs, and watch it reload (or not) under the rules. Steps and
// outcomes are printed as JSON; the Python side asserts.
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[1]);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch() {
  const udd = fs.mkdtempSync(path.join(A.tmp, 'chrome-'));
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1400,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c + ' ' + buf))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); this.events = []; }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } else this.events.push(m); }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const page = async (url, width) => {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: width || A.width || 1400, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
      const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      await c.send('Page.navigate', { url }, sessionId);
      await until('!!window.ensUpd && Object.keys(ensUpd.loaded).length > 0');
      return { evalIn, until, sessionId, targetId, close: () => c.send('Target.closeTarget', { targetId }) };
    };
    const poke = (rel) => { const f = path.join(A.static, rel); fs.appendFileSync(f, rel.endsWith('.js') ? '\n// poke\n' : '\n<!-- poke -->\n'); };
    const stampOf = async (p, rel) => (await (await fetch(A.base + '/api/config')).json()).pages[rel];

    // ---- session.html: a chat with many messages, scrolled up; typed text holds; idle reloads at the same balloon.
    {
      const p = await page(A.base + '/session?room=' + A.room);
      await p.until('document.querySelectorAll("#msgs > [data-key]").length >= 20');
      await p.evalIn('window.__mark = 1; (() => { const b = document.querySelector("#msgs"); b.scrollTop = Math.max(0, b.scrollHeight / 2); })(); 0');
      await sleep(300);
      const before = await p.evalIn('(() => { const b = document.querySelector("#msgs"); const k = readingPlace(b); return { key: k.key, off: k.off, top: b.scrollTop, stick: STICK, stamp: ensUpd.loaded["session.html"] }; })()');
      out.sessionBefore = before;
      // Words in the box: the line, no reload.
      await p.evalIn('(() => { const i = document.querySelector("#input"); i.value = "half a thought"; i.dispatchEvent(new Event("input", { bubbles: true })); })(); 0');
      poke('session.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('!!document.querySelector("#ens-upd")', 10000);
      await sleep(2500);
      out.sessionHeld = await p.evalIn('({ mark: window.__mark, line: document.querySelector("#ens-upd").textContent, pending: ensUpd.pending, box: document.querySelector("#input").value })');
      out.sessionLineBox = await p.evalIn('(() => { const r = document.querySelector("#ens-upd").getBoundingClientRect(); const cs = getComputedStyle(document.querySelector("#ens-upd")); return { top: r.top, left: r.left, width: r.width, height: r.height, fontSize: cs.fontSize, bg: cs.backgroundColor, color: cs.color, link: getComputedStyle(document.querySelector("#ens-upd button")).color }; })()');
      // Cleared and idle again for 60 s: the page reloads by itself, and is back at the balloon.
      await p.evalIn('(() => { const i = document.querySelector("#input"); i.value = ""; i.dispatchEvent(new Event("input", { bubbles: true })); ensUpd.lastInput = Date.now() - 61000; })(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelectorAll("#msgs > [data-key]").length >= 20 && UPD_PLACE === null', 15000);
      await sleep(400);
      out.sessionAfter = await p.evalIn('(() => { const b = document.querySelector("#msgs"); const k = readingPlace(b); return { key: k.key, off: k.off, top: b.scrollTop, stick: STICK, stamp: ensUpd.loaded["session.html"], line: !!document.querySelector("#ens-upd"), box: document.querySelector("#input").value }; })()');
      out.sessionDisk = await stampOf(p, 'session.html');
      // A click on the line reloads at once, even with words in the box; the draft is back after.
      await p.evalIn('(() => { const i = document.querySelector("#input"); i.value = "kept words"; i.dispatchEvent(new Event("input", { bubbles: true })); window.__mark = 2; })(); 0');
      await sleep(400);
      poke('static/comments.js');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('!!document.querySelector("#ens-upd")', 10000);
      await sleep(1500);
      out.sessionClickHeld = await p.evalIn('window.__mark');
      await p.evalIn('document.querySelector("#ens-upd button").click(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('document.querySelector("#input") && document.querySelector("#input").value === "kept words"', 10000);
      out.sessionClick = { box: await p.evalIn('document.querySelector("#input").value'), stamp: await p.evalIn('ensUpd.loaded["static/comments.js"]'), disk: await stampOf(p, 'static/comments.js') };
      await p.close();
    }
    // ---- session.html with a read marker up the chat: the catch-up line draws, and the place is put back over it.
    {
      const p = await page(A.base + '/session?room=' + A.room);
      await p.until('document.querySelectorAll("#msgs > [data-key]").length >= 20');
      await p.evalIn('window.__mark = 6; (() => { const b = document.querySelector("#msgs"); b.scrollTop = Math.max(0, b.scrollHeight * 0.6); const m = LAST_ITEMS.filter(x => x && x.id && x.id !== "task")[4]; localStorage.setItem(READ_KEY(), JSON.stringify({ id: m.id, ts: m.ts })); })(); 0');
      await sleep(300);
      out.cuBefore = await p.evalIn('(() => { const b = document.querySelector("#msgs"); const k = readingPlace(b); return { key: k.key, off: k.off, top: b.scrollTop, stick: STICK }; })()');
      poke('session.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN && document.querySelectorAll("#msgs > [data-key]").length >= 20 && UPD_PLACE === null', 15000);
      await sleep(400);
      out.cuAfter = await p.evalIn('(() => { const b = document.querySelector("#msgs"); const k = readingPlace(b); return { key: k.key, off: k.off, top: b.scrollTop, stick: STICK, line: !!b.querySelector(":scope > .catchup"), point: !!CU_POINT }; })()');
      await p.close();
    }
    // ---- fileview.html: the same file, line and scroll after the reload.
    {
      const p = await page(A.base + '/fileview?path=' + encodeURIComponent(A.file) + '&line=120');
      await p.until('!!document.querySelector("#main .code")');
      await p.evalIn('window.__mark = 1; (() => { const s = fvScrollOwner(document.querySelector("#main")); s.scrollTop = 900; })(); 0');
      await sleep(400);
      out.fileBefore = await p.evalIn('(() => { const s = fvScrollOwner(document.querySelector("#main")); return { top: s.scrollTop, line: AT_LINE, url: location.pathname + location.search }; })()');
      poke('static/hl.js');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 20000);
      await p.until('!!document.querySelector("#main .code")');
      await sleep(600);
      out.fileAfter = await p.evalIn('(() => { const s = fvScrollOwner(document.querySelector("#main")); return { top: s.scrollTop, line: AT_LINE, url: location.pathname + location.search, stamp: ensUpd.loaded["static/hl.js"], marked: !!document.querySelector(".ln-row.mark, .mark, [data-marked]") }; })()');
      out.fileDisk = await stampOf(p, 'static/hl.js');
      // A comment being written holds it.
      await p.evalIn('window.__mark = 3; openComposer({ start: -1, end: -1, quote: "" }); 0');
      poke('fileview.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('!!document.querySelector("#ens-upd")', 40000);
      out.fileHeld = await p.evalIn('window.__mark');
      await p.close();
    }
    // The PO drawer's visible frame, its chat drawn and put back, and where it is.
    const poFrame = 'document.querySelector("#po-panel iframe.po-session:not([hidden])")';
    const poDrawn = '(() => { const f = ' + poFrame + '; const w = f && f.contentWindow; return !!(w && w.document.querySelectorAll("#msgs > [data-key]").length >= 20 && w.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN && UPD_PLACE === null")); })()';
    const poPlace = '(() => { const f = ' + poFrame + '; const w = f.contentWindow; const b = w.document.querySelector("#msgs"); const k = w.readingPlace(b); return { peek: PO_PEEK, pin: PO_PIN, shown: !document.querySelector("#po-panel").hidden && document.body.classList.contains("po-peek"), room: f.dataset.room, proj: SELECTED_PROJECT, tab: PROJECT_TAB, key: k.key, off: k.off, top: b.scrollTop, stick: w.eval("STICK") }; })()';
    // ---- index.html: a project drilled into, on its Workspace tab, with a task open.
    {
      const p = await page(A.base + '/');
      await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.length > 0');
      await p.evalIn('window.__mark = 1; SELECTED_PROJECT = PROJECTS.projects[0].id; PROJECT_TAB = "workspace"; renderRows(); 0');
      await sleep(300);
      out.indexBefore = await p.evalIn('({ proj: SELECTED_PROJECT, tab: PROJECT_TAB, stamp: ensUpd.loaded["index.html"] })');
      // A diff comment being written is in hand: its box open and empty, typed
      // in, and after a Shift-click extends the range (drPaint rewrites the
      // textarea then: the words move over without an input event).
      out.indexDiff = await p.evalIn(`(() => {
        const box = document.createElement('div'); document.body.appendChild(box);
        const rv = drReview('cdp-test', { open: null, target: () => null });
        drShow(rv, box, 'root', 'a.py', ${JSON.stringify(A.diff)});
        const rows = [...box.querySelectorAll('.drv .dr')].filter(r => r.querySelector('.dg[data-o]'));
        const gutter = (i, shift) => rows[i].querySelector('.dg').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: !!shift }));
        const r = { rows: rows.length, free: ensUpd.busy() };
        gutter(1);
        r.emptyOpen = { busy: ensUpd.busy(), open: !!box.querySelector('.dcx textarea') };
        const ta = box.querySelector('.dcx textarea'); ta.value = 'unsent diff comment'; ta.dispatchEvent(new Event('input', { bubbles: true }));
        r.typed = ensUpd.busy();
        gutter(3, true);
        const ta2 = box.querySelector('.dcx textarea');
        r.ranged = { busy: ensUpd.busy(), sameField: ta2 === ta, note: ta2 ? ta2.value : null, draft: rv.view.draft ? rv.view.draft.note : null, span: rv.view.draft ? [rv.view.draft.a, rv.view.draft.b] : null };
        box.querySelector('.dcx-cancel').click();
        r.cancelled = { busy: ensUpd.busy(), open: !!box.querySelector('.dcx') };
        gutter(2); ta.value = 'kept'; box.querySelector('.dcx textarea').value = 'words'; box.querySelector('.dcx textarea').dispatchEvent(new Event('input', { bubbles: true }));
        r.typedAgain = ensUpd.busy();
        box.remove(); rv.view = null;
        r.gone = ensUpd.busy();
        return r;
      })()`);
      // A task's title typed in the New task dialog and left for the
      // specification (blur fires change): the line, no reload, hidden or not.
      await p.evalIn('window.__changed = 0; document.addEventListener("change", () => { window.__changed++; }, true); showNewSessionModal(""); document.querySelector("#ns-title").focus(); 0');
      await c.send('Input.insertText', { text: 'a task title' }, p.sessionId);
      await p.evalIn('document.querySelector("#ns-task").focus(); 0');
      await sleep(200);
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('!!document.querySelector("#ens-upd")', 10000);
      await sleep(1500);
      out.indexHeld = await p.evalIn('({ mark: window.__mark, busy: ensUpd.busy(), holding: ensUpd.holdingText(), changed: window.__changed, title: document.querySelector("#ns-title").value, open: document.querySelector("#modal").open, focused: document.activeElement && document.activeElement.id })');
      await p.evalIn('Object.defineProperty(document, "hidden", { get: () => true, configurable: true }); document.dispatchEvent(new Event("visibilitychange")); ensUpd.maybe(); 0');
      await sleep(1000);
      out.indexHiddenTitle = await p.evalIn('({ mark: window.__mark, title: document.querySelector("#ns-title").value, open: document.querySelector("#modal").open })');
      // Cancel closes the dialog: nothing in hand, and the hidden tab reloads at once.
      await p.evalIn('document.querySelector("#ns-cancel").click(); ensUpd.maybe(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof SELECTED_PROJECT !== "undefined" && !!SELECTED_PROJECT', 15000);
      out.indexAfter = await p.evalIn('({ proj: SELECTED_PROJECT, tab: PROJECT_TAB, stamp: ensUpd.loaded["index.html"], line: !!document.querySelector("#ens-upd") })');
      out.indexDisk = await stampOf(p, 'index.html');
      // The chat framed in the page (the task panel's) comes back where it was
      // when the page reloads for itself: the host saves the frame's place, and
      // the chat loaded again under the same url puts it back.
      const frameJs = 'const f = document.createElement("iframe"); f.id = "cdp-frame"; f.className = "dp-session"; f.style.cssText = "width:600px;height:400px;display:block"; f.src = "/session?id=" + encodeURIComponent(' + JSON.stringify(A.room) + ') + "&embed=1"; document.body.appendChild(f);';
      const frameKey = '"cd-upd-place:/session?id=" + encodeURIComponent(' + JSON.stringify(A.room) + ') + "&embed=1"';
      const frameDrawn = '(() => { const f = document.querySelector("#cdp-frame"); const w = f && f.contentWindow; return !!(w && w.document.querySelectorAll("#msgs > [data-key]").length >= 20 && w.eval("typeof CHAT_DRAWN !== typeof void 0 && CHAT_DRAWN && UPD_PLACE === null")); })()';
      const framePlace = '(() => { const w = document.querySelector("#cdp-frame").contentWindow; const b = w.document.querySelector("#msgs"); const k = w.readingPlace(b); return { key: k.key, off: k.off, top: b.scrollTop, stick: w.eval("STICK") }; })()';
      await p.evalIn('window.__mark = 5; ' + frameJs + ' 0');
      await p.until(frameDrawn, 15000);
      await p.evalIn('(() => { const w = document.querySelector("#cdp-frame").contentWindow; const b = w.document.querySelector("#msgs"); b.scrollTop = Math.max(0, b.scrollHeight / 2); })(); 0');
      await sleep(300);
      out.frameBefore = await p.evalIn(framePlace);
      // Hidden (display:none), a frame has no place to keep; shown again it is scrolled back for the reload.
      out.frameHiddenPlace = await p.evalIn('(() => { const f = document.querySelector("#cdp-frame"); f.style.display = "none"; const k = f.contentWindow.ensUpdPlace(); f.style.display = "block"; return { key: k.key, stick: k.stick }; })()');
      await p.evalIn('(() => { const w = document.querySelector("#cdp-frame").contentWindow; w.document.querySelector("#msgs").scrollTop = ' + out.frameBefore.top + '; })(); 0');
      await sleep(300);
      out.frameBefore2 = await p.evalIn(framePlace);
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; document.querySelector("#cdp-frame").contentWindow.ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof SELECTED_PROJECT !== "undefined" && !!SELECTED_PROJECT', 15000);
      out.frameSaved = await p.evalIn('!!sessionStorage.getItem(' + frameKey + ')');
      await p.evalIn(frameJs + ' 0');
      await p.until(frameDrawn, 15000);
      await sleep(400);
      out.frameAfter = await p.evalIn(framePlace);
      out.frameKept = await p.evalIn('!!sessionStorage.getItem(' + frameKey + ')');
      await p.evalIn('document.querySelector("#cdp-frame").remove(); 0');
      // The PO drawer open over the page, its conversation scrolled up: after
      // the reload it is open again on the same conversation at the same place.
      await p.evalIn('window.__mark = 7; PO_PIN = SELECTED_PROJECT; PO_PEEK = true; renderPo(); 0');
      await p.until(poDrawn, 15000);
      await p.evalIn('(() => { const b = ' + poFrame + '.contentWindow.document.querySelector("#msgs"); b.scrollTop = Math.max(0, b.scrollHeight / 2); })(); 0');
      await sleep(300);
      out.poBefore = await p.evalIn(poPlace);
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ' + poFrame + '.contentWindow.ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until(poDrawn, 15000);
      await sleep(400);
      out.poAfter = await p.evalIn(poPlace);
      // Closed before the reload: closed after it.
      await p.evalIn('window.__mark = 8; poClosePeek(); 0');
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof SELECTED_PROJECT !== "undefined" && !!SELECTED_PROJECT', 15000);
      await sleep(600);
      out.poClosed = await p.evalIn('({ peek: PO_PEEK, hidden: document.querySelector("#po-panel").hidden, proj: SELECTED_PROJECT, tab: PROJECT_TAB })');
      // A file index.html does not load (session.html) changes: nothing.
      await p.evalIn('window.__mark = 4; 0');
      poke('session.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await sleep(4000);
      out.indexOther = await p.evalIn('({ mark: window.__mark, pending: ensUpd.pending })');
      // The line at 900 and 500 px, light and dark.
      out.lineSizes = {};
      for (const w of [1400, 900, 500]) {
        await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: 900, deviceScaleFactor: 1, mobile: false }, p.sessionId);
        for (const theme of ['light', 'dark']) {
          await p.evalIn(`document.documentElement.setAttribute("data-theme", "${theme}"); ensUpd.showLine(); 0`);
          await sleep(100);
          out.lineSizes[w + '/' + theme] = await p.evalIn('(() => { const n = document.querySelector("#ens-upd"); const r = n.getBoundingClientRect(); const cs = getComputedStyle(n); const b = n.querySelector("button").getBoundingClientRect(); return { top: r.top, left: r.left, width: r.width, height: r.height, bg: cs.backgroundColor, color: cs.color, fontSize: cs.fontSize, lineHeight: cs.lineHeight, btnIn: b.left >= r.left && b.right <= r.right && b.top >= r.top && b.bottom <= r.bottom, scrollW: document.documentElement.scrollWidth, innerW: innerWidth }; })()');
        }
      }
      await p.close();
    }
    // ---- index.html on a phone (500 px): the PO drawer opened by the pill over
    // an open task, on Overview and on Workspace, is over the task again after
    // the reload (opening a task by hand closes the drawer there; a put-back is
    // not that).
    out.phone = {};
    for (const tab of ['tasks', 'workspace']) {
      const p = await page(A.base + '/', 500);
      await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.length > 0');
      await p.evalIn('window.__mark = 9; SELECTED_PROJECT = PROJECTS.projects[0].id; PROJECT_TAB = ' + JSON.stringify(tab) + '; renderRows(); openDetail(ALL_ROWS.find(r => r.roomId === ' + JSON.stringify(A.room) + ').sessionId); poPillClick(); 0');
      await p.until(poDrawn, 15000);
      await p.evalIn('(() => { const b = ' + poFrame + '.contentWindow.document.querySelector("#msgs"); b.scrollTop = Math.max(0, b.scrollHeight / 2); })(); 0');
      await sleep(300);
      const state = '(() => { const s = ' + poPlace + '; s.phone = isPhone(); s.detail = document.body.classList.contains("detail-open"); s.sid = SELECTED_SID; return s; })()';
      const before = await p.evalIn(state);
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; document.querySelectorAll("iframe").forEach(f => { try { f.contentWindow.ensUpd.lastInput = Date.now() - 100000; } catch (e) {} }); ensUpd.poll(); 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until(poDrawn, 15000);
      await sleep(400);
      out.phone[tab] = { before, after: await p.evalIn(state) };
      await p.close();
    }
  } finally {
    try { c.ws.close(); } catch (e) {}
    const gone = new Promise(r => ch.on('exit', r));
    ch.kill();
    await Promise.race([gone, sleep(5000)]);
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,5 +1,6 @@
 line1 = 1
-old = 2
+new = 2
+new2 = 22
 line3 = 3
 line4 = 4
 line5 = 5
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    """A hub in a thread serving a copy of the pages, with one room of many
    messages and one project with a long file; headless Chrome opens each page."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-upd-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        cls.static = base / "site"
        (cls.static / "static").mkdir(parents=True)
        for rel in dashboard.PAGE_FILES:
            shutil.copyfile(ROOT / rel, cls.static / rel)
        state = base / "state"
        state.mkdir()
        cls.root = base / "EnsembleProjects"
        cls.root.mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "STATIC_DIR", cls.static),
            mock.patch.object(dashboard, "PROJECTS_ROOT", cls.root),
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
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
        dashboard._STAMP_CACHE.clear()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        # One project with one long file, and one task room with many messages.
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.file = cls.root / "Motors" / "long.py"
        cls.file.write_text("".join(f"line_{i} = {i}\n" for i in range(1, 401)), encoding="utf-8")
        # Two agents: a one-agent room draws its transcript, not its messages.
        room = chatroom.create_room("Long talk", [{"identity": "claude", "agent": "claude", "cwd": str(cls.root / "Motors")},
                                                  {"identity": "codex", "agent": "codex", "cwd": str(cls.root / "Motors")}])
        cls.room = room["id"]
        for i in range(40):
            chatroom.post_message(cls.room, "user" if i % 2 else "claude", f"message {i}\n\n" + "words " * 40)
        try:
            dashboard.assign_session_project(cls.room, proj["id"])
        except Exception:
            pass
        # The project's PO: another room of many messages, for the drawer.
        po = chatroom.create_room("PO talk", [{"identity": "claude", "agent": "claude", "cwd": str(cls.root / "Motors")},
                                              {"identity": "codex", "agent": "codex", "cwd": str(cls.root / "Motors")}])
        cls.po_room = po["id"]
        for i in range(40):
            chatroom.post_message(cls.po_room, "user" if i % 2 else "claude", f"po message {i}" + chr(10) * 2 + "words " * 40)
        try:
            dashboard.assign_session_project(cls.po_room, proj["id"])
        except Exception:
            pass
        ok, why = dashboard.set_project_po(proj["id"], cls.po_room)
        assert ok, why
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        args = {"chrome": CHROME, "tmp": cls.tmp.name, "static": str(cls.static), "base": f"http://127.0.0.1:{cls.port}",
                "room": cls.room, "file": str(cls.file), "diff": DIFF}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        cls.err = out.stderr
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        dashboard._STAMP_CACHE.clear()
        cls.tmp.cleanup()

    def test_session_holds_while_the_box_has_words_then_reloads_at_the_same_balloon(self):
        g = self.got
        self.assertEqual(g["sessionHeld"]["mark"], 1, "no reload with words in the box")
        self.assertEqual(g["sessionHeld"]["line"], "Ensemble was updated · Reload")
        self.assertTrue(g["sessionHeld"]["pending"])
        self.assertIsNone(g["sessionAfter"].get("mark"))
        self.assertNotEqual(g["sessionAfter"]["stamp"], g["sessionBefore"]["stamp"])
        self.assertEqual(g["sessionAfter"]["stamp"], g["sessionDisk"], "the reloaded page is the one on disk")
        self.assertFalse(g["sessionBefore"]["stick"])
        self.assertEqual(g["sessionAfter"]["key"], g["sessionBefore"]["key"], "the same balloon at the top of the view")
        self.assertLessEqual(abs(g["sessionAfter"]["off"] - g["sessionBefore"]["off"]), 2)
        self.assertFalse(g["sessionAfter"]["line"])
        self.assertEqual(g["sessionAfter"]["box"], "")

    def test_the_line_click_reloads_and_the_draft_survives(self):
        g = self.got
        self.assertEqual(g["sessionClickHeld"], 2)
        self.assertEqual(g["sessionClick"]["box"], "kept words")
        self.assertEqual(g["sessionClick"]["stamp"], g["sessionClick"]["disk"])

    def test_fileview_comes_back_to_the_same_line_and_scroll(self):
        g = self.got
        self.assertEqual(g["fileBefore"]["line"], 120)
        self.assertEqual(g["fileAfter"]["line"], 120)
        self.assertLessEqual(abs(g["fileAfter"]["top"] - g["fileBefore"]["top"]), 2)
        self.assertEqual(g["fileAfter"]["stamp"], g["fileDisk"])
        self.assertIn("st=", g["fileAfter"]["url"])
        self.assertEqual(g["fileHeld"], 3, "a comment being written holds the reload")

    def test_a_read_marker_up_the_chat_draws_the_line_and_the_place_is_still_put_back(self):
        g = self.got
        self.assertFalse(g["cuBefore"]["stick"])
        self.assertEqual(g["cuAfter"]["key"], g["cuBefore"]["key"], "the same balloon at the top, not the catch-up line")
        self.assertLessEqual(abs(g["cuAfter"]["off"] - g["cuBefore"]["off"]), 2)
        self.assertTrue(g["cuAfter"]["line"], "the catch-up line is drawn, up the chat")
        self.assertTrue(g["cuAfter"]["point"])
        self.assertFalse(g["cuAfter"]["stick"])

    def test_a_diff_comment_being_written_is_in_hand_through_a_repaint(self):
        d = self.got["indexDiff"]
        self.assertGreaterEqual(d["rows"], 6)
        self.assertFalse(d["free"])
        self.assertEqual(d["emptyOpen"], {"busy": True, "open": True}, "an open composer, empty, holds")
        self.assertTrue(d["typed"])
        ranged = dict(d["ranged"])
        a, b = ranged.pop("span")
        self.assertEqual(b - a, 2, "the range covers the three rows between the two clicks")
        self.assertEqual(ranged, {"busy": True, "sameField": False, "note": "unsent diff comment", "draft": "unsent diff comment"},
                         "the range extended: a new textarea with the words, still in hand")
        self.assertEqual(d["cancelled"], {"busy": False, "open": False})
        self.assertTrue(d["typedAgain"])
        self.assertFalse(d["gone"], "the diff box gone from the page: nothing in hand")

    def test_index_holds_on_a_dialogs_typed_title_then_comes_back_to_the_same_project_and_tab(self):
        g = self.got
        held = dict(g["indexHeld"])
        self.assertEqual(held.pop("focused"), "ns-task", "the title was left for the specification")
        self.assertGreaterEqual(held.pop("changed"), 1, "leaving the title fired change")
        self.assertEqual(held, {"mark": 1, "busy": True, "holding": True, "title": "a task title", "open": True})
        self.assertEqual(g["indexHiddenTitle"], {"mark": 1, "title": "a task title", "open": True}, "hidden with a title typed: no reload")
        self.assertEqual((g["indexAfter"]["proj"], g["indexAfter"]["tab"]), (g["indexBefore"]["proj"], "workspace"))
        self.assertEqual(g["indexAfter"]["stamp"], g["indexDisk"])
        self.assertFalse(g["indexAfter"]["line"])
        self.assertEqual(g["indexOther"], {"mark": 4, "pending": False}, "session.html is not index.html's")

    def test_the_framed_chat_comes_back_where_it_was_after_its_host_reloads(self):
        g = self.got
        self.assertFalse(g["frameBefore"]["stick"])
        self.assertTrue(g["frameBefore"]["key"])
        self.assertEqual(g["frameHiddenPlace"], {"key": "", "stick": False}, "a hidden frame has no place to keep")
        self.assertEqual(g["frameBefore2"]["key"], g["frameBefore"]["key"])
        self.assertTrue(g["frameSaved"], "the host saved the frame's place under the frame's url")
        self.assertEqual(g["frameAfter"]["key"], g["frameBefore2"]["key"], "the same balloon at the top of the framed chat")
        self.assertLessEqual(abs(g["frameAfter"]["off"] - g["frameBefore2"]["off"]), 2)
        self.assertFalse(g["frameAfter"]["stick"])
        self.assertFalse(g["frameKept"], "put back once")

    def test_the_po_drawer_is_open_again_on_the_same_conversation_after_the_reload(self):
        g = self.got
        before, after = dict(g["poBefore"]), dict(g["poAfter"])
        self.assertEqual(before["room"], self.po_room)
        self.assertTrue(before["peek"] and before["shown"] and not before["stick"] and before["key"])
        self.assertEqual(after["room"], before["room"], "the same PO conversation")
        self.assertEqual((after["peek"], after["shown"], after["pin"]), (True, True, before["pin"]))
        self.assertEqual((after["proj"], after["tab"]), (before["proj"], before["tab"]))
        self.assertEqual(after["key"], before["key"], "the same balloon at the top of the drawer's chat")
        self.assertLessEqual(abs(after["off"] - before["off"]), 2)
        self.assertFalse(after["stick"])
        self.assertEqual(g["poClosed"], {"peek": False, "hidden": True, "proj": before["proj"], "tab": before["tab"]}, "closed before: closed after")

    def test_on_a_phone_the_po_drawer_is_over_the_restored_task_again(self):
        for tab in ("tasks", "workspace"):
            b, a = self.got["phone"][tab]["before"], self.got["phone"][tab]["after"]
            self.assertTrue(b["phone"] and b["peek"] and b["shown"] and b["detail"] and b["sid"] and b["key"] and not b["stick"], (tab, b))
            self.assertEqual((b["room"], b["tab"]), (self.po_room, tab))
            for k in ("phone", "peek", "shown", "detail", "sid", "room", "proj", "tab", "pin", "key"):
                self.assertEqual(a[k], b[k], (tab, k))
            self.assertLessEqual(abs(a["off"] - b["off"]), 2, tab)
            self.assertFalse(a["stick"], tab)

    def test_the_line_fits_the_top_at_1400_900_500_light_and_dark(self):
        s = self.got["lineSizes"]
        self.assertEqual(set(s), {f"{w}/{t}" for w in (1400, 900, 500) for t in ("light", "dark")})
        for k, m in s.items():
            self.assertEqual((m["top"], m["left"]), (0, 0), k)
            self.assertEqual(m["width"], m["innerW"], k)
            self.assertLessEqual(m["scrollW"], m["innerW"], f"{k}: no sideways scroll")
            self.assertTrue(28 <= m["height"] <= 32, (k, m["height"]))
            self.assertEqual(m["fontSize"], "12px", k)
            self.assertTrue(m["btnIn"], k)
        self.assertNotEqual(s["1400/light"]["bg"], s["1400/dark"]["bg"], "the line follows the theme")
        self.assertNotEqual(s["1400/light"]["color"], s["1400/dark"]["color"])


if __name__ == "__main__":
    unittest.main()
