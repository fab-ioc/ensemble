"""The selection bar (static/selbar.js) in a real browser: the chat
(session.html) and the file view (fileview.html), served by a hub in a thread,
driven by headless Chrome over CDP with real mouse and key events.

* Comment shows however the words were selected: a drag ending on the composer
  (outside the chat), a drag starting on a balloon's header, a double and a
  triple click, Shift+arrows, a selection made with no mouse at all;
* Copy sits beside Comment; a selection across two balloons is Copy only;
* Comment takes the words selected when it is clicked (a selection widened by
  the keyboard after the bar showed), cut to the one balloon;
* the bar survives the chat's poll while the selection is there, and shows
  again for a balloon drawn after a new message came in;
* it follows a scroll, hidden while the words are scrolled out of the chat;
* Copy writes plain text by navigator.clipboard, and by execCommand where the
  page is not secure (plain http), keeps the selection and says "Copied";
* in the file view, a drag ending outside the file is cut to it, and at the
  foot of the window the bar opens above the selection, inside the window.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
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
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_page_update import CHROME, NODE  # noqa: E402
from tests import chrome_profile  # noqa: E402

CDP_JS = chrome_profile.JS + r"""
const A = JSON.parse(process.argv[1]);
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1400,900', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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

async function page(c, url, ready, W, H) {
  const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false }, sessionId);
  const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 200) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 500)); return r.result.value; };
  const until = async (expr, ms = 15000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
  await c.send('Page.navigate', { url }, sessionId);
  await until(ready);
  await sleep(600);
  const mouse = (type, x, y, clickCount = 1) => c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount }, sessionId);
  const p = {
    ev, until, close: () => c.send('Target.closeTarget', { targetId }),
    // The point at the `off`-th letter of the first `needle` under `scope`, its left or right edge.
    at: (scope, needle, off = 0, side = 'l') => ev(`(() => {
      const w = document.createTreeWalker(document.querySelector(${JSON.stringify(scope)}), NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) { const i = n.nodeValue.indexOf(${JSON.stringify(needle)}); if (i < 0) continue;
        const r = document.createRange(); r.setStart(n, i + ${off}); r.setEnd(n, i + ${off} + 1);
        const b = r.getBoundingClientRect(); return [${side === 'l' ? 'b.left + 1' : 'b.right - 1'}, b.top + b.height / 2]; }
      return null; })()`),
    centre: sel => ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + Math.min(20, r.width / 2), r.top + r.height / 2]; })()`),
    drag: async (a, b) => {
      await mouse('mousePressed', a[0], a[1]);
      for (let s = 1; s <= 8; s++) await mouse('mouseMoved', a[0] + (b[0] - a[0]) * s / 8, a[1] + (b[1] - a[1]) * s / 8);
      await mouse('mouseReleased', b[0], b[1]); await sleep(450);
    },
    clicks: async (pt, n) => { for (let k = 1; k <= n; k++) { await mouse('mousePressed', pt[0], pt[1], k); await mouse('mouseReleased', pt[0], pt[1], k); } await sleep(450); },
    click: async pt => { await mouse('mousePressed', pt[0], pt[1]); await mouse('mouseReleased', pt[0], pt[1]); await sleep(300); },
    shiftRight: async n => { for (let k = 0; k < n; k++) for (const type of ['keyDown', 'keyUp'])
      await c.send('Input.dispatchKeyEvent', { type, key: 'ArrowRight', code: 'ArrowRight', windowsVirtualKeyCode: 39, modifiers: 8 }, sessionId); await sleep(450); },
    reset: async () => { await mouse('mousePressed', 2, 2); await mouse('mouseReleased', 2, 2); await ev('getSelection().removeAllRanges(); 0'); await sleep(350); },
    bar: () => ev(`(() => { const b = document.querySelector('.sel-bar'); const s = getSelection();
      const box = r => ({ l: r.left, t: r.top, r: r.right, b: r.bottom });
      const sr = s.rangeCount && !s.isCollapsed ? s.getRangeAt(0).getBoundingClientRect() : null;
      return { shown: !!b && !b.hidden, comment: !!b && !b.querySelector('.sel-cmt').hidden, copy: !!b && !b.querySelector('.sel-copy').hidden,
               copyText: b ? b.querySelector('.sel-copy').textContent : '', placement: b ? b.dataset.placement : '',
               bar: b && !b.hidden ? box(b.getBoundingClientRect()) : null, sel: s.toString(), selRect: sr && box(sr), vw: innerWidth, vh: innerHeight }; })()`),
  };
  return p;
}

async function chat(c) {
  const out = {};
  const p = await page(c, A.base + '/session?room=' + encodeURIComponent(A.room), 'document.querySelectorAll("#msgs .msg .text.md").length > 10', 1000, 700);
  const M = '#msgs';
  const show = async needle => { await p.ev(`[...document.querySelectorAll("#msgs .msg")].find(m => m.textContent.includes(${JSON.stringify(needle)})).scrollIntoView({ block: 'center' }); 0`); await sleep(300); };
  const bottom = async () => { await p.ev('(() => { const m = document.querySelector("#msgs"); m.scrollTop = m.scrollHeight; })(); 0'); await sleep(300); };
  // What a click on Comment opens: the composer's quote.
  const comment = async () => { if (!(await p.ev('!!document.querySelector(".sel-bar .sel-cmt")'))) return null; await p.click(await p.centre('.sel-bar .sel-cmt'));
    const q = await p.ev('(() => { const q = document.querySelector(".cmt-composer .cmt-quote"); return q ? q.textContent : null; })()');
    await p.ev('(() => { const x = document.querySelector(".cmt-composer .cmt-cancel"); if (x) x.click(); })(); 0'); return q; };

  await bottom(); await p.reset();
  await p.drag(await p.at(M, 'Last balloon', 5), await p.centre('#input'));
  out.toComposer = await p.bar(); out.toComposerQuote = await comment();

  await show('Charlie reply'); await p.reset();
  await p.clicks(await p.at(M, 'Charlie reply', 2), 3);
  out.triple = await p.bar(); out.tripleQuote = await comment();

  await show('Charlie reply'); await p.reset();
  { const hdr = await p.ev('(() => { const t = [...document.querySelectorAll("#msgs .msg")].find(m => m.textContent.includes("Charlie reply")); const r = t.querySelector(".from").getBoundingClientRect(); return [r.right - 4, r.top + r.height / 2]; })()');
    await p.drag(hdr, await p.at(M, 'Charlie reply', 12, 'r')); }
  out.fromHeader = await p.bar(); out.fromHeaderQuote = await comment();

  await show('Charlie reply'); await p.reset();
  await p.clicks(await p.at(M, 'Charlie reply', 2), 2);
  out.double = await p.bar();
  await p.shiftRight(6);
  out.widened = await p.bar(); out.widenedQuote = await comment();

  // No mouse at all: a selection made the way the keyboard makes one.
  await show('Charlie reply'); await p.reset();
  await p.ev(`(() => { const t = [...document.querySelectorAll("#msgs .msg .text.md")].find(e => e.textContent.includes("Charlie reply"));
    const w = document.createTreeWalker(t, NodeFilter.SHOW_TEXT); const n = w.nextNode(); getSelection().setBaseAndExtent(n, 0, n, 13); })(); 0`);
  await sleep(500);
  out.keyboard = await p.bar();

  await bottom(); await p.reset();
  await p.drag(await p.at(M, 'Filler message number 11', 0), await p.at(M, 'Last balloon', 4, 'r'));
  out.twoBalloons = await p.bar();

  // Copy: by navigator.clipboard, then by execCommand as over plain http.
  await bottom(); await p.reset();
  await p.drag(await p.at(M, 'Last balloon', 0), await p.at(M, 'Last balloon', 11, 'r'));
  await p.ev('window.__clip = null; navigator.clipboard.writeText = t => { window.__clip = t; return Promise.resolve(); }; 0');
  await p.click(await p.centre('.sel-bar .sel-copy'));
  out.copied = { clip: await p.ev('window.__clip'), bar: await p.bar() };
  await p.ev(`window.__clip = null; Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
    document.addEventListener('copy', e => { const a = document.activeElement; window.__clip = a && a.tagName === 'TEXTAREA' ? a.value.slice(a.selectionStart, a.selectionEnd) : '?'; }, { once: true }); 0`);
  await sleep(1700);
  await p.click(await p.centre('.sel-bar .sel-copy'));
  out.copiedPlain = { clip: await p.ev('window.__clip'), bar: await p.bar(), leftover: await p.ev('document.querySelectorAll("body > textarea").length') };

  // The poll: a message comes in while the words are selected.
  await p.ev('fetch("/__post?text=" + encodeURIComponent("Zulu arrives while selected"), { method: "POST" }).catch(() => 0); 0');
  await sleep(200);
  out.polledBefore = await p.bar();
  await sleep(6000);
  out.polled = await p.bar();
  await p.reset();
  await p.until('[...document.querySelectorAll("#msgs .msg .text.md")].some(e => e.textContent.includes("Zulu arrives"))', 20000);
  await bottom();
  await p.drag(await p.at(M, 'Zulu arrives', 0), await p.at(M, 'Zulu arrives', 11, 'r'));
  out.afterRender = await p.bar();

  // A scroll: hidden while the words are out of the chat, back after.
  await p.ev('document.querySelector("#msgs").scrollTop -= 400; 0'); await sleep(300);
  out.scrolledAway = await p.bar();
  await bottom(); await sleep(200);
  out.scrolledBack = await p.bar();
  await p.close();
  return out;
}

async function fileview(c) {
  const out = {};
  const p = await page(c, A.base + '/fileview?path=' + encodeURIComponent(A.file) + '&room=' + encodeURIComponent(A.room),
                       'typeof _cRoot !== "undefined" && !!_cRoot && _cRoot.textContent.includes("line 3 of the notes")', 900, 500);
  await p.reset();
  const hdr = await p.ev('(() => { const r = document.querySelector("header").getBoundingClientRect(); return [r.left + 30, r.top + r.height / 2]; })()');
  await p.drag(await p.at('body', 'line 3 of the notes', 5, 'r'), hdr);
  out.toHeader = await p.bar();
  await p.click(await p.centre('.sel-bar .sel-cmt'));
  out.toHeaderQuote = await p.ev('(() => { const q = document.querySelector(".cmt-composer .cmt-quote"); return q ? q.textContent : null; })()');
  await p.ev('(() => { const x = document.querySelector(".cmt-composer .cmt-cancel"); if (x) x.click(); })(); 0');
  // The foot of the window: the last line in view, selected.
  await p.reset();
  const last = await p.ev(`(() => { const w = document.createTreeWalker(_cRoot, NodeFilter.SHOW_TEXT); let n, best = null;
    while ((n = w.nextNode())) { if (!n.nodeValue.trim()) continue; const r = document.createRange(); r.selectNodeContents(n); const b = r.getBoundingClientRect();
      if (b.height && b.bottom < innerHeight - 4) best = n.nodeValue.trim(); }
    return best; })()`);
  await p.drag(await p.at('body', last, 0), await p.at('body', last, 8, 'r'));
  out.foot = await p.bar();
  await p.close();
  return out;
}

async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    out.chat = await chat(c);
    out.file = await fileview(c);
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
    """A hub in a thread with a two-agent chat of eighteen balloons, one with
    paragraphs, a code block and a list, and a text file in its folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-sel-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
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
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        work = root / "Motors"
        work.mkdir()
        notes = work / "notes.txt"
        notes.write_text("".join(f"This is line {i} of the notes, with some words.\n" for i in range(1, 80)), encoding="utf-8")
        room = chatroom.create_room("Selection", [{"identity": "claude", "agent": "claude", "cwd": str(work)},
                                                  {"identity": "codex", "agent": "codex", "cwd": str(work)}])
        rid = cls.room = room["id"]
        chatroom.patch_room(rid, cwd=str(work))
        chatroom.post_message(rid, "user", "First question about the motor gearbox and its ratios.")
        chatroom.post_message(rid, "claude", "Alpha paragraph one has several words.\n\nBravo paragraph two follows it.\n\n"
                                             "```python\nprint('code line')\n```\n\n- item one\n- item two", to="user")
        chatroom.post_message(rid, "codex", "Charlie reply from the reviewer with some words.", to="claude")
        for i in range(12):
            chatroom.post_message(rid, "claude", f"Filler message number {i} with text " * 3, to="user")
        chatroom.post_message(rid, "claude", "Last balloon: delta echo foxtrot golf hotel india juliet kilo lima.", to="user")

        # A message posted from the page's side mid-test: the hub's own poll then brings it in.
        class Handler(dashboard.Handler):
            def do_POST(self):
                if self.path.startswith("/__post?text="):
                    from urllib.parse import unquote
                    chatroom.post_message(rid, "claude", unquote(self.path.split("=", 1)[1]), to="user")
                    self.send_response(204)
                    self.end_headers()
                    return
                super().do_POST()

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": rid, "file": str(notes)}
        out = subprocess.run([NODE, "-e", CDP_JS, json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        cls.err = out.stderr
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def inside(self, g, margin=8):
        r = g["bar"]
        self.assertIsNotNone(r, g)
        self.assertGreaterEqual(r["l"], margin - 0.5, g)
        self.assertGreaterEqual(r["t"], margin - 0.5, g)
        self.assertLessEqual(r["r"], g["vw"] - margin + 0.5, g)
        self.assertLessEqual(r["b"], g["vh"] - margin + 0.5, g)

    def assertCommentAndCopy(self, g):
        self.assertTrue(g["shown"], g)
        self.assertTrue(g["comment"], g)
        self.assertTrue(g["copy"], g)
        self.inside(g)

    def test_a_drag_that_ends_on_the_composer_still_offers_comment(self):
        g = self.got["chat"]
        self.assertCommentAndCopy(g["toComposer"])
        self.assertEqual(g["toComposerQuote"], "“balloon: delta echo foxtrot golf hotel india juliet kilo lima.”",
                         "cut to the balloon's own words")

    def test_a_triple_click_and_a_drag_from_the_header(self):
        g = self.got["chat"]
        self.assertCommentAndCopy(g["triple"])
        self.assertEqual(g["tripleQuote"], "“Charlie reply from the reviewer with some words.”")
        self.assertCommentAndCopy(g["fromHeader"])
        self.assertEqual(g["fromHeaderQuote"], "“Charlie reply”", "the header's words are not quoted")

    def test_keyboard_selections(self):
        g = self.got["chat"]
        self.assertCommentAndCopy(g["double"])
        self.assertCommentAndCopy(g["widened"])
        self.assertEqual(g["widened"]["sel"], "Charlie reply ")
        self.assertEqual(g["widenedQuote"], "“Charlie reply ”", "Comment takes the words selected now")
        self.assertCommentAndCopy(g["keyboard"])
        self.assertEqual(g["keyboard"]["placement"], "below")

    def test_two_balloons_are_copy_only(self):
        g = self.got["chat"]["twoBalloons"]
        self.assertTrue(g["shown"], g)
        self.assertFalse(g["comment"], g)
        self.assertTrue(g["copy"], g)
        self.inside(g)

    def test_copy_writes_plain_text_and_keeps_the_selection(self):
        g = self.got["chat"]
        self.assertEqual(g["copied"]["clip"], "Last balloon")
        self.assertEqual(g["copied"]["bar"]["copyText"], "✓ Copied")
        self.assertEqual(g["copied"]["bar"]["sel"], "Last balloon")
        self.assertEqual(g["copiedPlain"]["clip"], "Last balloon", "over plain http: execCommand")
        self.assertEqual(g["copiedPlain"]["bar"]["sel"], "Last balloon", "the selection is put back")
        self.assertEqual(g["copiedPlain"]["bar"]["copyText"], "✓ Copied")
        self.assertEqual(g["copiedPlain"]["leftover"], 0)

    def test_it_survives_the_poll_and_shows_after_a_redraw(self):
        g = self.got["chat"]
        self.assertCommentAndCopy(g["polledBefore"])
        self.assertCommentAndCopy(g["polled"])
        self.assertEqual(g["polled"]["sel"], "Last balloon")
        self.assertCommentAndCopy(g["afterRender"])

    def test_it_follows_a_scroll(self):
        g = self.got["chat"]
        self.assertFalse(g["scrolledAway"]["shown"], "the words are out of the chat")
        self.assertCommentAndCopy(g["scrolledBack"])
        self.assertLess(abs(g["scrolledBack"]["bar"]["t"] - g["scrolledBack"]["selRect"]["b"]), 16, g["scrolledBack"])

    def test_the_file_view(self):
        g = self.got["file"]
        self.assertCommentAndCopy(g["toHeader"])
        self.assertTrue(g["toHeaderQuote"].startswith("“This is line 1 of the notes"), g["toHeaderQuote"])
        self.assertTrue(g["toHeaderQuote"].endswith("This is line 3”"), g["toHeaderQuote"])
        self.assertCommentAndCopy(g["foot"])
        self.assertEqual(g["foot"]["placement"], "above", g["foot"])
        self.assertLessEqual(g["foot"]["bar"]["b"], g["foot"]["selRect"]["t"], g["foot"])


class TheWiring(unittest.TestCase):
    """Every surface with selection comments uses the one bar, and a phone gets
    finger-sized targets."""

    def test_every_surface_loads_and_mounts_it(self):
        for page, mount in (("session.html", "SelBar.mount({ resolve: selResolve"),
                            ("fileview.html", "SelBar.mount({ resolve: selResolve"),
                            ("index.html", "SelBar.mount({ resolve: drSelResolve")):
            src = (ROOT / page).read_text(encoding="utf-8")
            with self.subTest(page):
                self.assertIn('<script src="/static/selbar.js"></script>', src)
                self.assertIn(mount, src)
                self.assertNotIn("cmt-selbtn", src)
                # In the page's own phone block (pointer: coarse, or index.html's MOBILE_MQ).
                rule = src.index(".sel-bar button { height: var(--touch-min); min-width: var(--touch-min);")
                block = src[src.rindex("(pointer: coarse) {", 0, rule):rule].replace("\r\n", "\n")
                self.assertNotIn("\n  }\n", block, "no block closes between the query and the rule")
        self.assertIn("static/selbar.js", dashboard.PAGE_FILES)


if __name__ == "__main__":
    unittest.main()
