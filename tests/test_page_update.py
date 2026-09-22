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
  const el = (tag) => ({ tag, isConnected: true, value: '', attrs: {}, matches(sel) { return sel.split(',').some(s => s.trim().startsWith(tag)); },
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; }, addEventListener(t, f) { this.on = this.on || {}; this.on[t] = f; },
    querySelector() { return this; }, set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; } });
  const doc = {
    hidden: false, body, dialogOpen: false,
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
        self.assertEqual(self.got["idleReload"], {"reloads": 1, "line": False, "saved": {"key": "m7", "off": 12, "stick": False}})
        self.assertEqual(self.got["restored"], [{"key": "m7", "off": 12, "stick": False}, None])

    def test_input_in_the_last_five_seconds_waits_without_a_line(self):
        self.assertEqual(self.got["recentInput"], {"reloads": 0, "line": False, "pending": True})
        self.assertEqual(self.got["quietAgain"], 1)

    def test_typed_text_shows_the_line_and_reloads_once_cleared_and_idle_60s(self):
        self.assertEqual(self.got["typed"], {"reloads": 0, "line": "Ensemble was updated · <button type=\"button\">Reload</button>", "busy": True})
        self.assertEqual(self.got["typedClearedSoon"], 0, "10 s of quiet is not idle once the line shows")
        self.assertEqual(self.got["typedClearedIdle"], 1)

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
    const page = async (url) => {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: A.width || 1400, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
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
    // ---- index.html: a project drilled into, on its Workspace tab, with a task open.
    {
      const p = await page(A.base + '/');
      await p.until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 0 && typeof ALL_ROWS !== "undefined" && ALL_ROWS.length > 0');
      await p.evalIn('window.__mark = 1; SELECTED_PROJECT = PROJECTS.projects[0].id; PROJECT_TAB = "workspace"; renderRows(); 0');
      await sleep(300);
      out.indexBefore = await p.evalIn('({ proj: SELECTED_PROJECT, tab: PROJECT_TAB, stamp: ensUpd.loaded["index.html"] })');
      // A dialog open holds it.
      await p.evalIn('document.querySelector("#modal").setAttribute("open", ""); 0');
      poke('index.html');
      await p.evalIn('ensUpd.checkedAt = 0; ensUpd.lastInput = Date.now() - 100000; ensUpd.poll(); 0');
      await p.until('!!document.querySelector("#ens-upd")', 10000);
      await sleep(1500);
      out.indexHeld = await p.evalIn('({ mark: window.__mark, busy: ensUpd.busy() })');
      await p.evalIn('document.querySelector("#modal").removeAttribute("open"); ensUpd.lastInput = Date.now() - 61000; 0');
      await p.until('window.__mark === undefined && !!window.ensUpd && Object.keys(ensUpd.loaded).length > 0', 15000);
      await p.until('typeof SELECTED_PROJECT !== "undefined" && !!SELECTED_PROJECT', 15000);
      out.indexAfter = await p.evalIn('({ proj: SELECTED_PROJECT, tab: PROJECT_TAB, stamp: ensUpd.loaded["index.html"], line: !!document.querySelector("#ens-upd") })');
      out.indexDisk = await stampOf(p, 'index.html');
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
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        args = {"chrome": CHROME, "tmp": cls.tmp.name, "static": str(cls.static), "base": f"http://127.0.0.1:{cls.port}",
                "room": cls.room, "file": str(cls.file)}
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

    def test_index_holds_on_a_dialog_then_comes_back_to_the_same_project_and_tab(self):
        g = self.got
        self.assertEqual(g["indexHeld"], {"mark": 1, "busy": True})
        self.assertEqual((g["indexAfter"]["proj"], g["indexAfter"]["tab"]), (g["indexBefore"]["proj"], "workspace"))
        self.assertEqual(g["indexAfter"]["stamp"], g["indexDisk"])
        self.assertFalse(g["indexAfter"]["line"])
        self.assertEqual(g["indexOther"], {"mark": 4, "pending": False}, "session.html is not index.html's")

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
