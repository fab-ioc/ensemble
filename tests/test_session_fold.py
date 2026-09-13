"""A long conversation in the chat page (session.html) folds its history.

The folding logic runs in Node, taken from the page, and these check that:

* the latest exchange is drawn in full: at least the last 10 balloons, and back
  to the person's own last message when that is within 25;
* rotation lines are landmarks, never folded and never counted;
* scrolled up, the fold does not move when messages arrive, so the balloon
  being read never folds under the reader; at the end it moves down;
* a balloon the reader opened, or one holding a comment's passage, stays open;
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
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
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
out.landmarkPlan = foldPlan(withLine, { key: null, open: new Set() }, true, null);

const fold = { key: null, open: new Set() };
const count = p => p.filter(x => x === 'full').length;
out.first = count(foldPlan(msgs(30), fold, true, null));
out.firstKey = fold.key;
out.scrolledUp = count(foldPlan(msgs(35), fold, false, null));
out.scrolledUpKey = fold.key;
out.atEnd = count(foldPlan(msgs(35), fold, true, null));
out.atEndKey = fold.key;
// The first full balloon left a sliding window: the fold is worked out again.
out.slid = count(foldPlan(msgs(60).slice(40), { key: 'm5', open: new Set() }, false, null));
fold.open.add('m3');
const opened = foldPlan(msgs(35), fold, true, m => m.id === 'm7');
out.opened = [opened[3], opened[7], opened[8]];

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
        out = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True,
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
        self.assertEqual((self.r["first"], self.r["firstKey"]), (10, "m20"))
        self.assertEqual((self.r["scrolledUp"], self.r["scrolledUpKey"]), (15, "m20"),
                         "a balloon folded while the reader was scrolled up")
        self.assertEqual((self.r["atEnd"], self.r["atEndKey"]), (10, "m25"))
        self.assertEqual(self.r["slid"], 10)

    def test_opened_and_commented_balloons_stay_open(self):
        self.assertEqual(self.r["opened"], ["full", "full", "row"])

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


if __name__ == "__main__":
    unittest.main()
