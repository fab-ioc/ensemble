"""Review comments in the chat page (session.html) are never lost and never
flicker.

The comment code runs in Node against a small stand-in for the page, several
times over one shared localStorage (a reload, or the same chat open twice),
and checks that:

* the tray is drawn once and not again while polls re-render with the same
  comments;
* unsent comments come back after a reload;
* a stopped session's Submit is off, says why and sends nothing;
* a send the hub refused (a stopped terminal, a handover, no answer, Enter
  not taken) keeps every comment and says so in the tray;
* a send that went drops exactly what went, and a comment added meanwhile stays;
* storage that will not write, or will not read, loses nothing in the page;
* two copies of the chat, one of them out of date, never drop each other's.

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
PREFIX = "cd-comment:room-test0001:"


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def comments_block(src: str) -> str:
    return src[src.index("// ---- In-place review comments -"):src.index("// ---- In-place review comments: end")]


JS = r"""
const vm = require('vm');
const { code, prefix } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const store = new Map();
const broken = { set: false, get: false };
const localStorage = {
  get length() { if (broken.get) throw new Error('SecurityError'); return store.size; },
  key: i => { if (broken.get) throw new Error('SecurityError'); return [...store.keys()][i] ?? null; },
  getItem: k => { if (broken.get) throw new Error('SecurityError'); return store.has(k) ? store.get(k) : null; },
  setItem: (k, v) => { if (broken.set) throw new Error('QuotaExceededError'); store.set(k, String(v)); },
  removeItem: k => { if (broken.set) throw new Error('QuotaExceededError'); store.delete(k); },
};
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
    console, setTimeout, localStorage,
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
      add: note => cmtAdd({ cid: cmtId(), at: cmtNow(), mid: 's1', from: 'claude', start: 0, end: 5, quote: 'hello', note }),
      applyComments, renderCmtTray, submitComments,
      tray: () => document.getElementById('cmt-tray'),
    };`, ctx);
  ctx.t.storage = storage;
  ctx.t.hear = () => storage.forEach(f => f({ key: null }));
  return ctx.t;
}
const stored = () => [...store].filter(([k]) => k.startsWith(prefix)).map(([, v]) => JSON.parse(v))
  .sort((a, b) => a.at - b.at).map(c => c.note);
const html = T => T.tray() ? T.tray().innerHTML : '';
const tick = () => new Promise(r => setTimeout(r, 5));
(async () => {
  const out = {};
  const A = page();
  A.add('one'); await tick(); A.add('two');
  const w = A.tray().writes;
  for (let i = 0; i < 10; i++) { A.applyComments(); A.renderCmtTray(); }   // what each poll does
  out.pollWrites = A.tray().writes - w;
  out.stored = stored();

  const B = page();                                   // the page loaded again
  out.reloaded = B.notes();
  out.reloadedItems = (html(B).match(/class="cmt-item"/g) || []).length;

  B.set('ROOM_LIVE', false); B.renderCmtTray();
  out.stoppedHtml = html(B);
  let n0 = posts.length; await B.submitComments();
  out.stoppedPosts = posts.length - n0; out.stoppedKept = B.notes();
  B.set('ROOM_LIVE', true);

  const fail = async reply => {
    replies = reply; await B.submitComments(); replies = [];
    return { kept: B.notes(), stored: stored(), html: html(B) };
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
  out.errorCleared = !/Not sent/.test(html(B));

  A.storage.forEach(f => f({ key: prefix + 'x' }));   // the other copy hears of it
  out.otherCopy = A.notes();

  B.set('SOLO_MODE', false);
  replies = [[200, { ok: true }]];
  await B.submitComments();
  out.roomSent = B.notes(); out.storeEmpty = stored().length === 0; out.roomPost = posts[posts.length - 1];
  out.trayGone = !B.tray();
  A.hear();

  // Storage that will not write: nothing is lost in the page, and it says a
  // reload would lose them; once storage works again they are stored.
  broken.set = true;
  const W = page();
  W.add('first'); W.add('second');
  out.writeFails = { notes: W.notes(), stored: stored(), html: html(W) };
  broken.set = false;
  W.add('third');
  out.writeRecovers = { notes: W.notes(), stored: stored(), html: html(W) };
  replies = [[200, { ok: true }], [200, { ok: true }]];
  await W.submitComments();
  out.writeCleared = stored();

  // Storage that will not read: the page's own list stands.
  broken.get = true;
  const R = page();
  R.add('x'); R.add('y');
  out.readFails = R.notes();
  broken.get = false;
  replies = [[200, { ok: true }], [200, { ok: true }]];
  await R.submitComments();
  out.readCleared = { notes: R.notes(), stored: stored() };

  // The same chat open twice, and Q never hears of P's changes: P sends while
  // Q adds, then Q adds again from its out-of-date list.
  const P = page(), Q = page();
  P.add('one'); await tick(); P.add('two'); await tick();
  replies = [[200, { ok: true }], [200, { ok: true }]];
  const pSending = P.submitComments();
  Q.add('three');
  out.midSend = stored();
  await pSending;
  await tick(); Q.add('four');
  out.twoCopies = { p: P.notes(), q: Q.notes(), stored: stored() };
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ReviewComments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = "\n".join(js_function(SRC, n) for n in ("sendSolo", "postOk", "sendErrorText")) + comments_block(SRC)
        out = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code, "prefix": PREFIX}), capture_output=True,
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
        self.assertTrue(self.r["storeEmpty"])
        self.assertTrue(self.r["trayGone"])

    def test_storage_that_will_not_write_loses_nothing(self):
        r = self.r["writeFails"]
        self.assertEqual(r["notes"], ["first", "second"], "a comment the browser would not store was dropped")
        self.assertEqual(r["stored"], [])
        self.assertIn("reloading this page would lose", r["html"])
        r = self.r["writeRecovers"]
        self.assertEqual(r["notes"], ["first", "second", "third"])
        self.assertEqual(r["stored"], ["first", "second", "third"], "not stored once storage worked again")
        self.assertNotIn("reloading this page would lose", r["html"])
        self.assertEqual(self.r["writeCleared"], [])

    def test_storage_that_will_not_read_loses_nothing(self):
        self.assertEqual(self.r["readFails"], ["x", "y"])
        self.assertEqual(self.r["readCleared"], {"notes": [], "stored": []})

    def test_two_copies_never_drop_each_others(self):
        self.assertEqual(self.r["midSend"], ["one", "two", "three"], "an out-of-date copy overwrote the other's")
        r = self.r["twoCopies"]
        self.assertEqual(r["stored"], ["three", "four"], "the send's clean-up or a stale add dropped a comment")
        self.assertEqual(r["p"], ["three"])
        self.assertEqual(r["q"], ["three", "four"])

    def test_only_render_cmt_tray_writes_the_tray(self):
        self.assertEqual(len(re.findall(r"tray\.innerHTML\s*=", SRC)), 1)


class SendBeforeTheSessionLoads(unittest.TestCase):
    """Before the first refresh the page cannot tell a solo session (typed
    into its terminal) from a room (said in the chat), so Send starts off and
    refuses, keeping the text, until the session has loaded."""

    def test_send_starts_off_and_waits_for_the_session(self):
        self.assertRegex(SRC, r'<button id="send" disabled>')
        handler = SRC[SRC.index("$('#send').onclick"):]
        handler = handler[:handler.index("\n};\n")]
        self.assertIn("if (!ROOM_OBJ)", handler, "Send does not wait for the session to load")
        guard = handler.index("if (!ROOM_OBJ)")
        self.assertLess(guard, handler.index("if (SOLO_MODE)"))
        self.assertNotIn("value = ''", handler[guard:handler.index("if (SOLO_MODE)")])


class StoppedTerminalInput(unittest.TestCase):
    """Typing into a terminal whose process has ended is refused (410) rather
    than answered 200 for a write that went nowhere: whether it is still listed
    until it is reaped, or it ended just as the input was written."""

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

            def __init__(self, alive, takes=True):
                self._alive, self._takes = alive, takes

            def alive(self):
                return self._alive

            def write(self, data):
                self.got = data
                return self._takes

        dead, ended, live = Term(False), Term(True, takes=False), Term(True)
        self.assertEqual(self.post(None), b"404")
        self.assertEqual(self.post(dead), b"410")
        self.assertIsNone(dead.got)
        self.assertEqual(self.post(ended), b"410")
        self.assertEqual(self.post(live), b"200")
        self.assertEqual(live.got, "hi")

    def test_a_terminal_write_says_whether_it_went(self):
        from backends import ptyrun

        from unittest import mock

        class Proc:
            """Returns what the real adapters return: ptyprocess the bytes it
            wrote (`took` of them), pywinpty always 0."""
            def __init__(self, error=None, took="all"):
                self.error, self.took, self.got = error, took, []

            def write(self, payload):
                if self.error:
                    raise self.error
                self.got.append(payload)
                if ptyrun.IS_WINDOWS:
                    return 0
                return len(payload) if self.took == "all" else self.took

        def session(proc):
            s = ptyrun.PtySession.__new__(ptyrun.PtySession)
            s._proc = proc
            return s

        for windows in (True, False):
            with mock.patch.object(ptyrun, "IS_WINDOWS", windows):
                for error in (EOFError("Pty is closed"), OSError("broken pipe")):
                    with self.subTest(windows=windows, error=type(error).__name__):
                        s = session(Proc(error))
                        self.assertFalse(s.write("\r"))
                        self.assertEqual(s.last_submit(), 0.0, "a failed Enter counted as an answer")
                with self.subTest(windows=windows, case="all taken"):
                    s = session(Proc())
                    self.assertTrue(s.write("hé\r"))
                    self.assertEqual(len(s._proc.got), 1)
                    self.assertGreater(s.last_submit(), 0.0)
        with mock.patch.object(ptyrun, "IS_WINDOWS", False):
            for took in (0, 2, None):
                with self.subTest(windows=False, took=took):
                    s = session(Proc(took=took))
                    self.assertFalse(s.write("hé\r"), "a terminal that took %r of 4 bytes counted as sent" % took)
                    self.assertEqual(s.last_submit(), 0.0)


if __name__ == "__main__":
    unittest.main()
