"""Review comments in the chat page (session.html) are never lost and never
flicker.

The comment code runs in Node against a small stand-in for the page, twice
over one shared localStorage (a reload), and checks that:

* the tray is drawn once and not again while polls re-render with the same
  comments;
* unsent comments come back after a reload;
* a stopped session's Submit is off, says why and sends nothing;
* a send the hub refused (a stopped terminal, a handover, no answer, Enter
  not taken) keeps every comment and says so in the tray;
* a send that went drops exactly what went, and a comment added meanwhile stays.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")
KEY = "cd-comments:room-test0001"


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def comments_block(src: str) -> str:
    return src[src.index("// ---- In-place review comments -"):src.index("// ---- In-place review comments: end")]


JS = r"""
const vm = require('vm');
const { code, key } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const store = new Map();
let replies = [];
const posts = [];
function page() {
  const els = {};
  class El {
    constructor() { this.dataset = {}; this.writes = 0; this._h = ''; }
    set innerHTML(h) { this._h = h; this.writes++; }
    get innerHTML() { return this._h; }
    querySelector() { return null; }
    addEventListener() {}
    remove() { delete els[this.id]; }
  }
  const storage = [];
  const ctx = {
    console, setTimeout,
    localStorage: { getItem: k => store.has(k) ? store.get(k) : null,
                    setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k) },
    document: { getElementById: id => els[id] || null, createElement: () => new El(),
                querySelector: () => null, querySelectorAll: () => [],
                body: { appendChild(n) { els[n.id] = n; } } },
    window: { addEventListener: (t, f) => { if (t === 'storage') storage.push(f); }, getSelection: () => null },
    fetch: async (url, o) => {
      posts.push([url, JSON.parse(o.body)]);
      const r = replies.shift();
      if (r === 'down') throw new TypeError('Failed to fetch');
      const [status, body] = r || [200, { ok: true }];
      return { ok: status < 400, status, json: async () => body };
    },
  };
  els.chatcol = { insertBefore(n) { els[n.id] = n; } };
  els.compose = {};
  vm.createContext(ctx);
  vm.runInContext(`
    const ROOM = 'room-test0001';
    let ROOM_OBJ = { id: ROOM }, ROOM_LIVE = true, SOLO_MODE = true, SOLO_PTY = 'pty-1';
    const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const checkSolo = () => {}, refresh = () => {};
    ${code}
    globalThis.t = {
      set: (k, v) => eval(k + ' = v'),
      notes: () => COMMENTS.map(c => c.note),
      add: note => cmtChange(() => COMMENTS.push({ cid: cmtId(), mid: 's1', from: 'claude', start: 0, end: 5, quote: 'hello', note })),
      applyComments, renderCmtTray, submitComments,
      tray: () => document.getElementById('cmt-tray'),
    };`, ctx);
  ctx.t.storage = storage;
  return ctx.t;
}
const stored = () => JSON.parse(store.get(key) || '[]').map(c => c.note);
(async () => {
  const out = {};
  const A = page();
  A.add('one'); A.add('two');
  const w = A.tray().writes;
  for (let i = 0; i < 10; i++) { A.applyComments(); A.renderCmtTray(); }   // what each poll does
  out.pollWrites = A.tray().writes - w;
  out.stored = stored();

  const B = page();                                   // the page loaded again
  out.reloaded = B.notes();
  out.reloadedItems = (B.tray().innerHTML.match(/class="cmt-item"/g) || []).length;

  B.set('ROOM_LIVE', false); B.renderCmtTray();
  out.stoppedHtml = B.tray().innerHTML;
  let n0 = posts.length; await B.submitComments();
  out.stoppedPosts = posts.length - n0; out.stoppedKept = B.notes();
  B.set('ROOM_LIVE', true);

  const fail = async reply => {
    replies = reply; await B.submitComments(); replies = [];
    return { kept: B.notes(), stored: stored(), html: B.tray() ? B.tray().innerHTML : '' };
  };
  out.gone = await fail([[404, { error: 'no_such_pty' }]]);
  out.dead = await fail([[410, { error: 'the session has stopped' }]]);
  out.handover = await fail([[409, { error: 'handing over to a fresh session, try again shortly' }]]);
  out.typedOnly = await fail([[200, { ok: true }], [410, { error: 'the session has stopped' }]]);
  out.down = await fail(['down']);
  B.set('SOLO_MODE', false);
  out.roomFail = await fail([[500, { error: 'boom' }]]);
  B.set('SOLO_MODE', true);

  replies = [[200, { ok: true }], [200, { ok: true }]];
  n0 = posts.length;
  const sending = B.submitComments();
  B.add('three');                                      // added while it goes
  await sending;
  out.afterSend = B.notes(); out.storedAfterSend = stored();
  out.sentTyped = posts.slice(n0).map(p => p[1].data);
  out.errorCleared = !/Not sent/.test(B.tray().innerHTML);

  A.storage.forEach(f => f({ key }));                  // the other copy hears of it
  out.otherCopy = A.notes();

  B.set('SOLO_MODE', false);
  replies = [[200, { ok: true }]];
  await B.submitComments();
  out.roomSent = B.notes(); out.keyGone = !store.has(key); out.roomPost = posts[posts.length - 1];
  out.trayGone = !B.tray();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ReviewComments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = "\n".join(js_function(SRC, n) for n in ("sendSolo", "postOk", "sendErrorText")) + comments_block(SRC)
        out = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code, "key": KEY}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.r = json.loads(out.stdout)

    def test_a_poll_does_not_redraw_the_tray(self):
        self.assertEqual(self.r["pollWrites"], 0, "the tray was redrawn by a render with unchanged comments")

    def test_unsent_comments_survive_a_reload(self):
        self.assertEqual(self.r["stored"], ["one", "two"])
        self.assertEqual(self.r["reloaded"], ["one", "two"])
        self.assertEqual(self.r["reloadedItems"], 2)

    def test_a_stopped_session_keeps_them_and_says_why(self):
        html = self.r["stoppedHtml"]
        self.assertRegex(html, r'class="cmt-submit" disabled')
        self.assertIn("not running", html)
        self.assertEqual(self.r["stoppedPosts"], 0)
        self.assertEqual(self.r["stoppedKept"], ["one", "two"])

    def test_a_refused_send_keeps_every_comment(self):
        for case, words in (("gone", "has stopped"), ("dead", "has stopped"), ("handover", "handing over"),
                            ("typedOnly", "not submitted"), ("down", "did not answer"), ("roomFail", "boom")):
            with self.subTest(case):
                r = self.r[case]
                self.assertEqual(r["kept"], ["one", "two"], "comments dropped after a failed send")
                self.assertEqual(r["stored"], ["one", "two"], "stored comments dropped after a failed send")
                self.assertIn("Not sent", r["html"])
                self.assertIn(words, r["html"])

    def test_a_send_that_went_drops_only_what_went(self):
        self.assertEqual(self.r["afterSend"], ["three"])
        self.assertEqual(self.r["storedAfterSend"], ["three"])
        self.assertIn("## Review comments (2)", self.r["sentTyped"][0])
        self.assertEqual(self.r["sentTyped"][1], "\r")
        self.assertTrue(self.r["errorCleared"])
        self.assertEqual(self.r["otherCopy"], ["three"], "the other copy of the chat kept a sent comment")

    def test_a_room_message(self):
        url, body = self.r["roomPost"]
        self.assertEqual(url, "/api/room/say")
        self.assertEqual(body["to"], "claude")
        self.assertEqual(self.r["roomSent"], [])
        self.assertTrue(self.r["keyGone"])
        self.assertTrue(self.r["trayGone"])

    def test_only_render_cmt_tray_writes_the_tray(self):
        self.assertEqual(len(re.findall(r"tray\.innerHTML\s*=", SRC)), 1)


class StoppedTerminalInput(unittest.TestCase):
    """Typing into a terminal whose process has ended, but which is still
    listed until it is reaped, is refused (410) rather than answered 200 for a
    write that went nowhere."""

    def post(self, sess):
        import io
        from unittest import mock
        import dashboard
        from backends import ptyrun

        body = json.dumps({"id": "pty-x", "data": "hi"}).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/pty/input", "POST", "HTTP/1.1"
        h.requestline = "POST /api/pty/input HTTP/1.1"
        h.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json", "Host": "127.0.0.1"}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        with mock.patch.object(ptyrun, "get", lambda _id: sess):
            h.do_POST()
        return h.wfile.getvalue().split(b" ", 2)[1]

    def test_stopped_and_live(self):
        class Term:
            meta: dict = {}
            got = None

            def __init__(self, alive):
                self._alive = alive

            def alive(self):
                return self._alive

            def write(self, data):
                self.got = data

        dead, live = Term(False), Term(True)
        self.assertEqual(self.post(None), b"404")
        self.assertEqual(self.post(dead), b"410")
        self.assertIsNone(dead.got)
        self.assertEqual(self.post(live), b"200")
        self.assertEqual(live.got, "hi")


if __name__ == "__main__":
    unittest.main()
