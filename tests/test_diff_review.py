"""Diffs in the Changes tab read as in an IDE, and their lines take comments.

index.html shows a task's diff with the Workspace viewer's highlighter
(static/hl.js) and keeps line comments with the chat's comment store
(static/comments.js). The diff review's functions run here in Node, the way
tests/test_workspace_tabs.py runs the tab block; skipped without Node:

* a unified diff becomes rows with old and new line numbers, and inside a hunk
  a removed "-- comment" or an added "++ x" stays a changed line, not a header;
* Python, JavaScript and Markdown diffs come out highlighted, every row's text
  exactly its line, and a string opened on a context line keeps its colour on
  the added line below it;
* a 5,000-line diff is parsed, highlighted and written as rows well under 1 s;
* a comment keeps its lines' code, says "line 12" or "lines 12–14", and finds
  its line again in a diff that has moved, but not a line whose code changed;
* one Submit sends one "## Review comments (N)" message to the task's chat
  with each comment's file, lines and quoted code; what went is marked sent and
  stays, a comment added meanwhile waits, a refused send keeps everything;
* a reload finds unsent and sent comments as they were, and drops anything
  malformed in storage;
* a task that is not running is sent nothing, and the tray says why.

It also checks the pages load the shared scripts, and that fileview.html no
longer carries its own copy of the highlighter.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
FILEVIEW = (ROOT / "fileview.html").read_text(encoding="utf-8").replace("\r\n", "\n")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
HL = (ROOT / "static" / "hl.js").read_text(encoding="utf-8")
STORE = (ROOT / "static" / "comments.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def review_block() -> str:
    return INDEX[INDEX.index("// ---- Diff review: begin"):INDEX.index("// ---- Diff review: end")]


JS = r"""
const vm = require('vm');
const { hl, store, block } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const mem = new Map();
const localStorage = {
  get length() { return mem.size; }, key: i => [...mem.keys()][i] ?? null,
  getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k),
};
let replies = [];
const posts = [];
// A send held open until released, the way a slow hub holds one.
let hold = null;
const gate = () => { let open; hold = new Promise(r => { open = r; }); return () => { hold = null; open(); }; };
const until = async f => { while (!f()) await new Promise(r => setTimeout(r, 5)); };
function page(extra) {
  const ctx = {
    console, performance, localStorage, setTimeout, clearTimeout, setInterval, clearInterval,
    CSS: { escape: s => s }, matchMedia: () => ({ matches: false }),
    document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null },
    window: { addEventListener() {}, matchMedia: () => ({ matches: false }), getSelection: () => null, innerWidth: 1000, innerHeight: 800 },
    toast() {}, writeSlot() {}, wsJoin: (a, b) => a + '/' + b,
    ...(extra || {}),
    fetch: async (url, o) => {
      posts.push([url, JSON.parse(o.body)]);
      const h = hold;
      if (h) await h;
      const r = replies.shift() || [200, { ok: true }];
      if (r === 'down') throw new TypeError('Failed to fetch');
      return { ok: r[0] < 400, status: r[0], json: async () => r[1] };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(`const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
${hl}
${store}
${block}
globalThis.T = { drParse, drHighlight, drRange, drLoc, drLineOf, drAnchor, drMessage, drValid, drStale,
                 drSubmitState, drReview, drSubmit, drRowHtml };`, ctx);
  return ctx.T;
}
const text = h => h.replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const plain = rows => rows.map(r => ({ k: r.k, o: r.o, n: r.n, t: r.t }));
(async () => {
  const out = {};
  const P = page();

  out.parse = plain(P.drParse([
    'diff --git a/q.sql b/q.sql', 'index 1..2 100644', '--- a/q.sql', '+++ b/q.sql',
    '@@ -10,3 +10,3 @@ SELECT', '--- old comment', ' kept', '+++ new', ' tail', '\\ No newline at end of file', '',
  ].join('\n')));

  const lang = { 'app.py': 'python', 'app.js': 'javascript', 'README.md': 'markdown' };
  const highlight = (file, diff) => {
    const rows = P.drParse(diff);
    P.drHighlight(rows, lang[file]);
    return { rows: rows.map(r => ({ k: r.k, t: r.t, h: r.h })), same: rows.every(r => text(r.h) === r.t) };
  };
  out.py = highlight('app.py', ['@@ -1,3 +1,5 @@', ' s = \"\"\"first', '+inside the string', ' end\"\"\"', "-x = 'gone'  # old", '+def g(): return 1', ''].join('\n'));
  out.js = highlight('app.js', ['@@ -1,1 +1,2 @@', ' let n = 1;', "+const a = fetch('/x'); // note", ''].join('\n'));
  out.md = highlight('README.md', ['@@ -1,1 +1,3 @@', ' Intro', '+# Title', '+Some **bold** and `code`', ''].join('\n'));

  // 5,000 lines: parsed, highlighted and written as rows.
  const big = ['@@ -1,4000 +1,4000 @@'];
  for (let i = 0; i < 5000; i++) {
    const s = i % 5 === 0 ? '+' : i % 7 === 0 ? '-' : ' ';
    big.push(s + `    value_${i} = compute("item ${i}", ${i} * 2)  # step ${i}`);
  }
  const t0 = performance.now();
  const brows = P.drParse(big.join('\n'));
  P.drHighlight(brows, 'python');
  const bh = brows.map((r, i) => P.drRowHtml(r, i)).join('');
  out.big = { ms: performance.now() - t0, rows: brows.length, coloured: (bh.match(/tk-str/g) || []).length };

  // Ranges, labels, the line to open, and finding a comment's line again.
  const rows = P.drParse(['@@ -5,4 +5,5 @@', ' a', '-b', '+B', '+C', ' d', ''].join('\n'));
  out.loc = {
    one: P.drLoc(P.drRange(rows, 1, 1).rows), range: P.drLoc(P.drRange(rows, 3, 4).rows),
    removed: P.drLoc(P.drRange(rows, 2, 2).rows), mixed: P.drLoc(P.drRange(rows, 2, 4).rows),
    lineOfRemoved: P.drLineOf(rows, 2, 2), lineOfRange: P.drLineOf(rows, 1, 5), quote: P.drRange(rows, 1, 3).rows,
  };
  const long = P.drParse(['@@ -1,100 +1,100 @@'].concat(Array.from({ length: 100 }, (_, i) => ' line ' + i)).join('\n'));
  const cap = P.drRange(long, 0, 100);
  out.cap = { kept: cap.rows.length, span: cap.span, last: cap.rows[cap.rows.length - 1].t };
  const c = { rows: P.drRange(rows, 4, 4).rows };
  const moved = P.drParse(['@@ -1,1 +1,4 @@', '+x', '+y', '+z', ' w', '@@ -5,4 +8,5 @@', ' a', '-b', '+B', '+C', ' d', ''].join('\n'));
  const changed = P.drParse(['@@ -5,4 +5,5 @@', ' a', '-b', '+B', '+C2', ' d', ''].join('\n'));
  const far = P.drParse(['@@ -85,4 +85,5 @@', ' a', '-b', '+B', '+C', ' d', ''].join('\n'));
  out.anchor = { same: P.drAnchor(rows, c), moved: P.drAnchor(moved, c), movedRow: plain([moved[P.drAnchor(moved, c)]])[0],
                 changed: P.drAnchor(changed, c), far: P.drAnchor(far, c) };

  out.fence = P.drMessage([{ root: 'C:/r', file: 'doc.md', rows: [{ k: 'add', n: 3, t: 'use ```js fences' }], note: 'ok' }], '');
  out.more = P.drMessage([{ root: 'C:/r', file: 'a.py', rows: cap.rows, span: cap.span, note: 'long' }], '');

  // Comments round the whole way: kept, reloaded, refused, sent, sent again.
  let live = true;
  const opts = { target: () => ({ roomId: 'room-1', live, name: 'The task' }), what: () => '`sess/x` against `main`' };
  const mk = (rv, a, b, note) => {
    const q = P.drRange(rows, a, b);
    rv.store.put({ cid: rv.store.id(), at: rv.store.now(), root: 'C:/r', file: 'app.py', rows: q.rows, span: q.span, note, sent: 0 });
  };
  const rv = P.drReview('task:room-1', opts);
  mk(rv, 1, 1, 'first'); mk(rv, 3, 4, 'second');
  localStorage.setItem('cd-diffcmt:task:room-1:bad', JSON.stringify({ cid: 'bad', note: 'no rows' }));
  const notes = r => r.store.list.map(x => [x.note, !!x.sent]);

  const P2 = page(), rv2 = P2.drReview('task:room-1', opts);
  out.reloaded = notes(rv2);
  live = false;
  out.stopped = P2.drSubmitState(rv2);
  let n0 = posts.length;
  await P2.drSubmit(rv2);
  out.stoppedPosts = posts.length - n0;
  live = true;
  replies = [[404, { error: 'no_such_room' }]];
  await P2.drSubmit(rv2);
  out.refused = { notes: notes(rv2), error: rv2.error, state: P2.drSubmitState(rv2) };
  replies = ['down'];
  await P2.drSubmit(rv2);
  out.down = { notes: notes(rv2), error: rv2.error };
  replies = [[200, { ok: true }]];
  let release = gate();
  n0 = posts.length;
  const sending = P2.drSubmit(rv2);
  await until(() => posts.length > n0);
  mk(rv2, 5, 5, 'third, added while it went');
  release();
  await sending;
  out.sent = { post: posts[posts.length - 1], notes: notes(rv2), error: rv2.error };

  const P3 = page(), rv3 = P3.drReview('task:room-1', opts);
  out.reloadedAfterSend = notes(rv3);
  replies = [[200, { ok: true }]];
  n0 = posts.length;
  await P3.drSubmit(rv3);
  out.second = { count: posts.length - n0, text: posts[posts.length - 1][1].text, notes: notes(rv3) };
  n0 = posts.length;
  await P3.drSubmit(rv3);
  out.nothingLeft = posts.length - n0;

  // Two copies of the page submit the same comments at the same moment: with
  // the claim in storage (plain http), and with the browser's lock (https).
  let locked = Promise.resolve();
  const locks = { request: (name, fn) => { const run = locked.then(() => fn()); locked = run.catch(() => {}); return run; } };
  out.twoTabs = {};
  for (const [how, extra] of [['storage', null], ['lock', { navigator: { locks } }]]) {
    const key = 'task:two-' + how;
    const A = page(extra), B = page(extra), ra = A.drReview(key, opts), rb = B.drReview(key, opts);
    const q = P.drRange(rows, 1, 1);
    ra.store.put({ cid: 'one', at: 1, root: 'C:/r', file: 'app.py', rows: q.rows, span: q.span, note: 'only once', sent: 0 });
    rb.store.sync();
    replies = [[200, { ok: true }], [200, { ok: true }]];
    n0 = posts.length;
    release = gate();
    const both = Promise.all([A.drSubmit(ra), B.drSubmit(rb)]);
    await until(() => posts.length > n0);
    await new Promise(r => setTimeout(r, 400));     // time enough for the other copy to send too, if it could
    release();
    await both;
    rb.store.sync();
    out.twoTabs[how] = { posts: posts.length - n0, a: notes(ra), b: notes(rb), errors: [ra.error, rb.error] };
    replies = [];
  }

  // A comment edited (or removed) in another copy while the send is on its way
  // was not delivered as it now reads: it is not marked sent.
  {
    const A = page(), B = page(), ra = A.drReview('task:edit', opts), rb = B.drReview('task:edit', opts);
    for (const [cid, note, a] of [['e1', 'old words', 1], ['e2', 'unchanged', 2], ['e3', 'removed meanwhile', 3]]) {
      const q = P.drRange(rows, a, a);
      ra.store.put({ cid, at: a, root: 'C:/r', file: 'app.py', rows: q.rows, span: q.span, note, sent: 0 });
    }
    rb.store.sync();
    replies = [[200, { ok: true }]];
    n0 = posts.length;
    release = gate();
    const going = A.drSubmit(ra);
    await until(() => posts.length > n0);
    rb.store.put({ ...rb.store.list.find(c => c.cid === 'e1'), note: 'edited while sending' });
    rb.store.remove(['e3']);
    release();
    await going;
    const C = page(), rc = C.drReview('task:edit', opts);
    out.editRace = { text: posts[posts.length - 1][1].text, notes: notes(ra), reloaded: notes(rc) };
  }

  const sent = Array.from({ length: 105 }, (_, i) => ({ cid: 's' + i, sent: 1000 + i }));
  out.stale = P.drStale(sent.concat([{ cid: 'u', sent: 0 }]));
  out.valid = [P.drValid({ root: '', file: 'a', note: 'n', rows: [{ k: 'add', t: 'x' }] }), P.drValid({ root: '', file: 'a', note: 'n', rows: [] }),
               P.drValid({ root: '', file: 'a', note: 'n', rows: [{ k: 'hunk', t: '@@' }] })];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class DiffReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "review.cjs"
            script.write_text(JS, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], input=json.dumps({"hl": HL, "store": STORE, "block": review_block()}),
                                  capture_output=True, text=True, encoding="utf-8", timeout=120)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_a_diff_becomes_numbered_rows(self):
        rows = self.r["parse"]
        self.assertEqual([x["k"] for x in rows], ["meta"] * 4 + ["hunk", "del", "ctx", "add", "ctx", "meta"])
        self.assertEqual(rows[5], {"k": "del", "o": 10, "t": "-- old comment"})
        self.assertEqual(rows[6], {"k": "ctx", "o": 11, "n": 10, "t": "kept"})
        self.assertEqual(rows[7], {"k": "add", "n": 11, "t": "++ new"})
        self.assertEqual(rows[8], {"k": "ctx", "o": 12, "n": 12, "t": "tail"})

    def test_python_js_and_markdown_are_highlighted_and_keep_their_text(self):
        for name in ("py", "js", "md"):
            with self.subTest(name):
                self.assertTrue(self.r[name]["same"], "a row's text is not its line")
        py = {x["t"]: x["h"] for x in self.r["py"]["rows"]}
        self.assertIn('class="tk-str"', py["inside the string"], "a string opened above lost its colour")
        self.assertIn('class="tk-kw"', py["def g(): return 1"])
        self.assertIn('class="tk-str"', py["x = 'gone'  # old"], "a removed line is not highlighted")
        self.assertIn('class="tk-com"', py["x = 'gone'  # old"])
        js = {x["t"]: x["h"] for x in self.r["js"]["rows"]}["const a = fetch('/x'); // note"]
        for cls in ("tk-kw", "tk-str", "tk-com"):
            self.assertIn(cls, js)
        md = {x["t"]: x["h"] for x in self.r["md"]["rows"]}
        self.assertIn("tk-hd", md["# Title"])
        self.assertIn("tk-strong", md["Some **bold** and `code`"])

    def test_a_5000_line_diff_is_fast(self):
        big = self.r["big"]
        self.assertEqual(big["rows"], 5001)
        self.assertGreater(big["coloured"], 4000)
        self.assertLess(big["ms"], 1000)

    def test_what_a_comment_is_on(self):
        loc = self.r["loc"]
        self.assertEqual(loc["one"], "line 5")
        self.assertEqual(loc["range"], "lines 6–7")
        self.assertEqual(loc["removed"], "removed line 6")
        self.assertEqual(loc["mixed"], "lines 6–7")
        self.assertEqual(loc["lineOfRemoved"], 6)
        self.assertEqual(loc["lineOfRange"], 5)
        self.assertEqual([q["t"] for q in loc["quote"]], ["a", "b", "B"])
        self.assertEqual(self.r["cap"], {"kept": 40, "span": 100, "last": "line 99"})

    def test_a_comment_finds_its_line_again(self):
        a = self.r["anchor"]
        self.assertEqual(a["same"], 4)
        self.assertEqual(a["movedRow"], {"k": "add", "n": 10, "t": "C"})
        self.assertEqual(a["changed"], -1)
        self.assertEqual(a["far"], -1)

    def test_the_message_quotes_code_safely(self):
        self.assertIn("````diff\n+use ```js fences\n````", self.r["fence"])
        self.assertIn("… 88 more lines", self.r["more"])

    def test_unsent_comments_survive_a_reload(self):
        self.assertEqual(self.r["reloaded"], [["first", False], ["second", False]])

    def test_a_task_that_is_not_running_is_sent_nothing(self):
        self.assertFalse(self.r["stopped"]["can"])
        self.assertIn("not running", self.r["stopped"]["note"])
        self.assertEqual(self.r["stoppedPosts"], 0)

    def test_a_refused_send_keeps_everything(self):
        for case, words in (("refused", "no longer exists"), ("down", "did not answer")):
            with self.subTest(case):
                self.assertEqual(self.r[case]["notes"], [["first", False], ["second", False]])
                self.assertIn("Not sent", self.r[case]["error"])
                self.assertIn(words, self.r[case]["error"])

    def test_one_submit_sends_one_message_and_keeps_them_as_sent(self):
        url, body = self.r["sent"]["post"]
        self.assertEqual(url, "/api/room/say")
        self.assertEqual(body["roomId"], "room-1")
        self.assertEqual(body["to"], "")
        text = body["text"]
        self.assertTrue(text.startswith("## Review comments (2)\n\nOn `sess/x` against `main`.\n\n"), text)
        self.assertIn("**1.** `app.py` line 5\n\n```diff\n a\n```\n\nfirst", text)
        self.assertIn("**2.** `app.py` lines 6–7\n\n```diff\n+B\n+C\n```\n\nsecond", text)
        self.assertEqual(self.r["sent"]["notes"], [["first", True], ["second", True], ["third, added while it went", False]])
        self.assertEqual(self.r["sent"]["error"], "")

    def test_sent_comments_stay_sent_after_a_reload_and_are_not_sent_again(self):
        self.assertEqual(self.r["reloadedAfterSend"], [["first", True], ["second", True], ["third, added while it went", False]])
        self.assertEqual(self.r["second"]["count"], 1)
        self.assertTrue(self.r["second"]["text"].startswith("## Review comments (1)"))
        self.assertIn("third, added while it went", self.r["second"]["text"])
        self.assertNotIn("first", self.r["second"]["text"])
        self.assertEqual(self.r["nothingLeft"], 0)

    def test_two_copies_submitting_at_once_send_one_message(self):
        for how in ("storage", "lock"):
            with self.subTest(how):
                r = self.r["twoTabs"][how]
                self.assertEqual(r["posts"], 1, "the same comments were sent twice")
                self.assertEqual(r["a"], [["only once", True]])
                self.assertEqual(r["b"], [["only once", True]])
                self.assertEqual(r["errors"], ["", ""])

    def test_a_comment_changed_during_a_send_is_not_marked_sent(self):
        e = self.r["editRace"]
        self.assertIn("old words", e["text"])
        self.assertNotIn("edited while sending", e["text"])
        expected = [["edited while sending", False], ["unchanged", True]]
        self.assertEqual(e["notes"], expected)
        self.assertEqual(e["reloaded"], expected)

    def test_a_line_is_a_finger_sized_target_on_a_phone(self):
        phone = INDEX[INDEX.index("/* Diffs: a line is a finger-sized target"):]
        self.assertIn(".dr { min-height: var(--touch-min);", phone[:600])
        self.assertIn("matchMedia(MOBILE_MQ).matches", review_block())

    def test_only_the_newest_sent_comments_are_kept(self):
        self.assertEqual(sorted(self.r["stale"]), sorted(["s0", "s1", "s2", "s3", "s4"]))
        self.assertEqual(self.r["valid"], [True, False, False])


class SharedScriptsAreWired(unittest.TestCase):
    def test_pages_load_the_shared_scripts(self):
        self.assertIn('<script src="/static/hl.js"></script>', INDEX)
        self.assertIn('<script src="/static/comments.js"></script>', INDEX)
        self.assertIn('<script src="/static/hl.js"></script>', FILEVIEW)
        self.assertIn('<script src="/static/comments.js"></script>', SESSION)

    def test_no_page_carries_its_own_copy(self):
        for name, page in (("index.html", INDEX), ("fileview.html", FILEVIEW), ("session.html", SESSION)):
            with self.subTest(name):
                self.assertNotIn("const HL = (() =>", page)
                self.assertNotIn("function cmtStore(", page)
                self.assertNotIn("localStorage.setItem(CMT_PREFIX", page)

    def test_the_hub_serves_scripts_as_javascript(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('".js": "text/javascript; charset=utf-8"', src)

    def test_the_old_floating_tray_is_gone(self):
        for old in ("#ch-tray", "chRenderTray", "chCommentPrompt", "renderDiff("):
            self.assertNotIn(old, INDEX)


if __name__ == "__main__":
    unittest.main()
