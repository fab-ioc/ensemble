"""A long conversation in the chat page (session.html) folds its history.

The folding logic runs in Node, taken from the page, and these check that:

* the latest exchange is drawn in full: at least the last 10 balloons, and back
  to the person's own last message when that is within 25;
* rotation lines are landmarks, never folded and never counted;
* scrolled up, no full balloon folds, whether messages arrive, a long
  transcript's window slides or a comment goes; at the end the fold moves down;
* a balloon the reader opened, or one holding a comment's passage, stays open;
* sending while scrolled up leaves the view where it is;
* reopening a task panel takes its chat to the latest message;
* a folded row's first line is plain text;
* patching the chat keeps every element that did not change, even when it
  moved, so links and selections survive a poll.

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


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


JS = r"""
const vm = require('vm');
const { code, showPending } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
class Node {
  constructor(h) { this._html = h; this.parent = null; }
  get nextElementSibling() { const k = this.parent.kids; return k[k.indexOf(this) + 1] || null; }
  remove() { const k = this.parent.kids; k.splice(k.indexOf(this), 1); this.parent = null; }
}
class Box {
  constructor() { this.kids = []; this.created = 0; }
  get children() { return this.kids.slice(); }
  get firstElementChild() { return this.kids[0] || null; }
  insertBefore(n, ref) {
    if (n.parent) n.remove();
    n.parent = this;
    const i = ref ? this.kids.indexOf(ref) : this.kids.length;
    this.kids.splice(i, 0, n);
  }
}
const box = new Box();
const ctx = { document: { createElement: () => ({ set innerHTML(h) { box.created++; this.content = { firstElementChild: new Node(h) }; } }) } };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { foldStart, foldPlan, foldLine, patchChildren };`, ctx);
const { foldStart, foldPlan, foldLine, patchChildren } = ctx.t;
const msgs = (n, users = []) => Array.from({ length: n }, (_, i) => ({ id: 'm' + i, from: users.includes(i) ? 'user' : 'claude', text: 't' + i }));
const out = {};
out.noUser = foldStart(msgs(30));
out.userWithin = foldStart(msgs(30, [12]));
out.userTooFar = foldStart(msgs(30, [2]));
out.userInLatest = foldStart(msgs(30, [15, 25]));
out.short = foldStart(msgs(5));
out.empty = foldStart([]);
const withLine = msgs(30); withLine.splice(25, 0, { id: 'rot1', divider: { n: 1 } });
out.landmark = foldStart(withLine);
out.landmarkPlan = foldPlan(withLine, { base: null, open: new Set() }, true, null);

const fold = { base: null, open: new Set() };
const count = p => p.filter(x => x === 'full').length;
const firstFull = p => p.indexOf('full');
let p = foldPlan(msgs(30), fold, true, null);
out.first = [count(p), firstFull(p)];
p = foldPlan(msgs(35), fold, false, null);
out.scrolledUp = [count(p), firstFull(p)];
p = foldPlan(msgs(35), fold, true, null);
out.atEnd = [count(p), firstFull(p)];
// A long transcript's window slid while scrolled up: the first full balloon
// (m20) left it, and the full ones that are still there stay full.
const slide = { base: null, open: new Set() };
foldPlan(msgs(30), slide, true, null);
p = foldPlan(msgs(150).slice(25), slide, false, null);
out.slid = [p.slice(0, 5), count(p)];
// Nothing drawn before is still there: worked out again.
out.replaced = count(foldPlan(msgs(300).slice(200), slide, false, null));
fold.open.add('m3');
const opened = foldPlan(msgs(35), fold, true, m => m.id === 'm7');
out.opened = [opened[3], opened[7], opened[8]];
// The comment on m7 went while scrolled up: m7 stays open until the end.
out.heldGoneUp = foldPlan(msgs(35), fold, false, null)[7];
out.heldGoneEnd = foldPlan(msgs(35), fold, true, null)[7];
// Fold pressed on an opened balloon while scrolled up: it folds.
fold.open.delete('m3');
out.foldedAgain = foldPlan(msgs(35), fold, false, null)[3];

// Sending while scrolled up adds the echo below and leaves the view where it is.
const sent = {};
for (const stick of [false, true]) {
  const pbox = { html: '', scrollTop: 400, scrollHeight: 5000, insertAdjacentHTML(w, h) { this.html += h; this.scrollHeight += 100; } };
  let shown = 0;
  const sctx = { $: () => pbox, identLabel: x => x, mdToHtml: x => x, showLatest: () => { shown++; }, Date };
  vm.createContext(sctx);
  vm.runInContext(`var PENDING_USER = [], STICK = ${stick};\n` + showPending + `\nshowPendingUser('hello');`, sctx);
  sent[stick ? 'atEnd' : 'up'] = { top: pbox.scrollTop, echoed: pbox.html.includes('hello'), latest: shown };
}
out.sent = sent;

out.lines = [
  foldLine('## Review 3\n\nAll good'),
  foldLine('```js\nconst x = 1;\n```\nAfter the code'),
  foldLine('**Bold** and [a link](https://x.y/z) with `code`'),
  foldLine('| a | b |\n|---|---|\n| 1 | 2 |'),
  foldLine('- first item\n- second'),
  foldLine('```\nonly code\n```'),
  foldLine('x'.repeat(300)).length,
  foldLine(''),
];

// Patching: same rows draw nothing; a row added at the top and one removed
// keep every other element as it was.
patchChildren(box, ['a', 'b', 'c']);
const [a, b, c] = box.children;
box.created = 0;
patchChildren(box, ['a', 'b', 'c']);
out.samePatch = box.created;
patchChildren(box, ['z', 'a', 'c', 'd']);
const k = box.children;
out.moved = { order: k.map(n => n._html), created: box.created, keptA: k[1] === a, keptC: k[2] === c, bGone: b.parent === null };
patchChildren(box, ['a']);
out.shrunk = { order: box.children.map(n => n._html), keptA: box.children[0] === a };
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class FoldALongConversation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = fold_block(SRC) + js_function(SRC, "patchChildren")
        payload = {"code": code, "showPending": js_function(SRC, "showPendingUser")}
        out = subprocess.run([NODE, "-e", JS], input=json.dumps(payload), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.r = json.loads(out.stdout)

    def test_the_latest_ten_are_full(self):
        self.assertEqual(self.r["noUser"], 20)
        self.assertEqual(self.r["short"], 0, "a short conversation folds nothing")
        self.assertEqual(self.r["empty"], 0)

    def test_back_to_the_persons_last_message(self):
        self.assertEqual(self.r["userWithin"], 12)
        self.assertEqual(self.r["userTooFar"], 20, "the fold went back further than 25 balloons")
        self.assertEqual(self.r["userInLatest"], 20, "extended past a message of theirs already shown")

    def test_rotation_lines_are_landmarks(self):
        self.assertEqual(self.r["landmark"], 20, "a rotation line counted as a balloon")
        plan = self.r["landmarkPlan"]
        self.assertEqual(plan[25], "line")
        self.assertEqual(plan.count("full"), 10)
        self.assertEqual(plan.count("row"), 20)

    def test_the_fold_stays_while_scrolled_up(self):
        self.assertEqual(self.r["first"], [10, 20])
        self.assertEqual(self.r["scrolledUp"], [15, 20], "a balloon folded while the reader was scrolled up")
        self.assertEqual(self.r["atEnd"], [10, 25])

    def test_a_sliding_window_folds_nothing_under_the_reader(self):
        survivors, full = self.r["slid"]
        self.assertEqual(survivors, ["full"] * 5, "a full balloon still in the window was folded")
        self.assertEqual(full, 125, "what arrived after the known balloons is drawn in full")
        self.assertEqual(self.r["replaced"], 10, "a wholly new list is folded afresh")

    def test_opened_and_commented_balloons_stay_open(self):
        self.assertEqual(self.r["opened"], ["full", "full", "row"])
        self.assertEqual(self.r["heldGoneUp"], "full", "a commented balloon folded under the reader")
        self.assertEqual(self.r["heldGoneEnd"], "row")
        self.assertEqual(self.r["foldedAgain"], "row", "Fold did not fold while scrolled up")

    def test_sending_while_scrolled_up_keeps_the_place(self):
        up, end = self.r["sent"]["up"], self.r["sent"]["atEnd"]
        self.assertEqual(up, {"top": 400, "echoed": True, "latest": 1})
        self.assertEqual(end, {"top": 5100, "echoed": True, "latest": 1})

    def test_first_line_is_plain_text(self):
        lines = self.r["lines"]
        self.assertEqual(lines[0], "Review 3")
        self.assertEqual(lines[1], "After the code")
        self.assertEqual(lines[2], "Bold and a link with code")
        self.assertEqual(lines[3], "| a | b |", "a table's first row")
        self.assertEqual(lines[4], "first item")
        self.assertEqual(lines[5], "```", "a balloon of only code still says something")
        self.assertEqual(lines[6], 241)
        self.assertEqual(lines[7], "")

    def test_a_patch_keeps_what_did_not_change(self):
        self.assertEqual(self.r["samePatch"], 0, "an unchanged poll drew elements again")
        m = self.r["moved"]
        self.assertEqual(m["order"], ["z", "a", "c", "d"])
        self.assertEqual(m["created"], 2, "elements that only moved were drawn again")
        self.assertTrue(m["keptA"] and m["keptC"] and m["bGone"])
        self.assertEqual(self.r["shrunk"], {"order": ["a"], "keptA": True})


class ThePageUsesIt(unittest.TestCase):
    def test_wired_in(self):
        render = js_function(SRC, "renderBubbles")
        self.assertIn("foldPlan(", render)
        self.assertEqual(render.count("mdToHtml("), 1, "a balloon is drawn around the cache")
        self.assertIn("MD_CACHE.get(", render)
        self.assertIn('id="to-latest"', SRC)
        self.assertRegex(SRC, r"#msgs \{[^}]*overflow-anchor:none")

    def test_reopening_a_task_panel_goes_to_the_latest(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn("ensemble: 'shown'", js_function(index, "openDetail"))
        self.assertRegex(SRC, r"d\.ensemble === 'shown' && e\.source === window\.parent\) toLatest\(\)")


if __name__ == "__main__":
    unittest.main()
