"""The message box is a small editor of numbered points (session.html): its
model and its serialiser run in Node, and these check that:

* a message with no point is its words alone; with points it is the
  "## Points (N)" form the hub reads (points._comment_items), one "**N.**"
  item per point, an empty point dropped, a point's images named inside it,
  a first line that is a fence, heading, quote, list or table line on a line
  of its own;
* the keys: Ctrl/⌘+Enter sends, Ctrl/⌘+Shift+Enter adds a point, Tab in the
  last point adds one once it has words, Backspace in an empty point removes
  it, Enter in a point is nothing (a new line); "1. " or "- " at the start of
  the head makes the first point;
* points are added, removed and moved as asked; a draft round-trips;
* the page reads such a message back as its items, strips each item's [ref]
  blocks inside it, draws it as a numbered list with an image where its
  point put it and a point chip under each item, and folds it to a count.

Skipped without Node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
ATTACH = (ROOT / "static" / "attach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def block(begin: str, end: str) -> str:
    return SRC[SRC.index(begin):SRC.index(end)]


def js_function(name: str) -> str:
    i = SRC.index(f"\nfunction {name}(") + 1
    return SRC[i:SRC.index("\n}\n", i) + 3]


def js_const(name: str) -> str:
    i = SRC.index(f"\nconst {name} = ") + 1
    return SRC[i:SRC.index("\n", i) + 1]


# The editor's model: everything above "The editor on the page".
MODEL = SRC[SRC.index("// ---- The message editor: begin"):SRC.index("// ---- The editor on the page ----")]
ITEMS = block("// ---- Numbered points: begin", "// ---- Numbered points: end")

JS = r"""
const vm = require('vm');
const { code, cases } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { ROOM: 'room-aaaa0001', console, URL,
  attOwn: (room, p) => /attachments/.test(p), esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  attSplit: t => { const lines = String(t || '').replace(/\s+$/, '').split('\n'); let n = lines.length;
    while (n && lines[n - 1].startsWith('[image] ')) n--; if (n === lines.length) return { words: String(t || ''), paths: [] };
    return { words: lines.slice(0, n).join('\n').replace(/\s+$/, ''), paths: lines.slice(n).map(l => l.slice(8).trim()) }; },
  ATT_PREFIX: '[image] ', HUB_PORT: '8765', ROOM_OBJ: null, CHAT_NAMES: {}, refOfUrl: () => null, refChipHtml: () => null, parkTaskRefs: (s, chips) => s, parkPmRefs: (s, chips) => s, pmLinkHtml: () => null,
  linkify: t => t, unlinkify: t => t, codeSpanHtml: b => '<code>' + b + '</code>', REF_A: '\uE004', REF_Z: '\uE005', REF_MARK_RE: /\uE004(\d+)\uE005/g,
  REF_URL_RE: /https?:\/\/[^\s<>()\[\]{}"'`*|\\]+/gi, TASK_BLOCK_RE: /\n\n\[ref ((?:@[A-Za-z][\w-]*@|#)(?:[A-Za-z][A-Za-z0-9]*-)?\d{1,6})\] task [^\n]*\s*$/,
  refHref: (room, mid) => '/session?room=' + room + '&msg=' + mid, lastAnswer: p => (p.answers || [])[(p.answers || []).length - 1] || null,
  PT_WORD: { open: 'waiting for an answer', answered: 'answered', acked: 'acknowledged', dropped: 'dropped' },
  ptBtn: (id, act, label) => `<button data-pt="${id}" data-pt-act="${act}">${label}</button>`, ackBtn: id => `<button data-pt="${id}" data-pt-act="ack">👍 Ack</button>`,
  ptLink: (mid, text) => `<a class="pt-link" data-mid="${mid}">${text}</a>`, canApprove: () => false,
  // The shared link block is not loaded: no "..." path resolves here.
  PATH_ABBR: null, withPathAbbrevs: (t, fn) => { ctx.PATH_ABBR = { code: new Map(), text: new Map(), n: 0 }; try { return fn(); } finally { ctx.PATH_ABBR = null; } },
};
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { edNew, edAddPoint, edRemovePoint, edMovePoint, edKeyAction, edHeadTrigger, edHeadToPoint, edSerialize, edDraft, edFromDraft, edUsed, edHas,
    pointItems, itemTail, itemBody, joinItems, stripRefBlocks, mdToHtml, itemsHtml, foldLine, pointBarHtml, ptOwn, pointItemsHtml, pointMaps };`, ctx);
const T = ctx.t;
const out = {};
// --- the serialiser ---
let m = T.edNew();
m.head = ' hello there \r\n';
out.plain = T.edSerialize(m);
T.edAddPoint(m, null, '');                       // empty: dropped
out.plainStill = T.edSerialize(m);
T.edAddPoint(m, null, 'The board is slow\nsince this morning');
const i2 = T.edAddPoint(m, null, '```js\nx()\n```');
m.points[i2].images = [{ room: 'room-aaaa0001', name: '2026-09-22 10.00.00 screenshot.png' }, { room: 'room-aaaa0001', name: 'b.png' }];
T.edAddPoint(m, null, '- a list\n- of two');
const i5 = T.edAddPoint(m, null, '   ');
m.points[i5].images = [{ room: 'room-aaaa0001', name: 'only.png' }];
out.points = T.edSerialize(m);
m.head = '';
out.noHead = T.edSerialize(m).split('\n').slice(0, 3);
// The head's images: named under the head, above the first point; alone
// when the head has no words; left to the hub when there is no point.
m.images = [{ room: 'room-aaaa0001', name: 'h.png' }];
out.headImgOnly = T.edSerialize(m).split('\n').slice(0, 4);
m.head = 'words';
out.headImg = T.edSerialize(m).split('\n').slice(0, 5);
out.headImgPlain = T.edSerialize(Object.assign(T.edNew(), { head: 'just words', images: m.images }));
// --- the keys ---
const k = (key, o = {}) => Object.assign({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, o);
const head = { part: 'head', empty: false, last: true }, pt = { part: 'point', empty: false, last: true };
out.keys = {
  send: [T.edKeyAction(k('Enter', { ctrlKey: true }), head), T.edKeyAction(k('Enter', { metaKey: true }), pt)],
  add: [T.edKeyAction(k('Enter', { ctrlKey: true, shiftKey: true }), head), T.edKeyAction(k('Enter', { metaKey: true, shiftKey: true }), pt)],
  tabLast: T.edKeyAction(k('Tab'), pt),
  tabLastEmpty: T.edKeyAction(k('Tab'), { part: 'point', empty: true, last: true }),
  tabNotLast: T.edKeyAction(k('Tab'), { part: 'point', empty: false, last: false }),
  shiftTab: T.edKeyAction(k('Tab', { shiftKey: true }), pt),
  tabHead: T.edKeyAction(k('Tab'), head),
  enter: T.edKeyAction(k('Enter'), pt),
  backspaceEmpty: T.edKeyAction(k('Backspace'), { part: 'point', empty: true, last: false }),
  backspaceWords: T.edKeyAction(k('Backspace'), pt),
  backspaceHead: T.edKeyAction(k('Backspace'), { part: 'head', empty: true, last: true }),
};
out.trigger = ['1. first thing', '- first thing', '1.no', '2. second', 'words 1. later', '1.\tx', '', '- '].map(T.edHeadTrigger);
// --- add, remove, move ---
m = T.edNew();
const a = T.edAddPoint(m, null, 'a'), b = T.edAddPoint(m, null, 'b'), c = T.edAddPoint(m, 1, 'c');
out.order1 = m.points.map(p => p.text);
out.moved = [T.edMovePoint(m, 0, 1), m.points.map(p => p.text).join(''), T.edMovePoint(m, 0, -1), T.edMovePoint(m, 2, 1), m.points.map(p => p.text).join('')];
out.removed = [T.edRemovePoint(m, 1).text, m.points.map(p => p.text).join(''), T.edRemovePoint(m, 7)];
out.ids = new Set(m.points.map(p => p.id)).size === m.points.length;
// --- the first point under a head with words: the words are point 1 ---
m = T.edNew();
out.headToPoint = [T.edHeadToPoint(m, ' first question \r\nmore '), m.points.map(p => p.text), T.edHeadToPoint(m, 'again'), m.points.length,
  T.edHeadToPoint(T.edNew(), '  \n '), T.edHeadToPoint(T.edNew(), '')];
// --- a draft ---
m = T.edNew(); m.head = 'h';
m.images = [{ room: 'r', name: 'top.png', url: 'y', state: 'ok' }, { room: '', name: 'lost.png' }];
T.edAddPoint(m, null, 'p1'); m.points[0].images = [{ room: 'r', name: 'n.png', url: 'x', state: 'ok' }];
const d = T.edDraft(m);
out.draft = d;
out.back = T.edSerialize(T.edFromDraft(JSON.parse(JSON.stringify(d))));
out.backImages = T.edFromDraft(JSON.parse(JSON.stringify(d))).images;
out.badDraft = [T.edSerialize(T.edFromDraft(null)), T.edSerialize(T.edFromDraft({ head: 3, points: [null, { text: 5, images: [{ room: 'r' }] }] })),
  T.edFromDraft({ points: 'x' }).points.length, T.edFromDraft({ images: 'x' }).images.length];
out.has = [T.edHas(T.edDraft(T.edNew())), T.edHas({ head: 'w', points: [], images: [] }), T.edHas({ head: '', points: [], images: [{ room: 'r', name: 'a.png' }] }),
  T.edHas({ head: '', points: [{ text: '', images: [] }], images: [] }), T.edHas(null)];
// --- reading it back ---
const sent = '## Points (2)\n\nAfter tests.\n\n**1.** The board is slow, see http://h/session?room=room-1&msg=m1\n\n[ref http://h/session?room=room-1&msg=m1] from claude in "Docs" at 2026-09-22 10:00:\n> Merged.\n\n[image] C:\\t\\attachments\\a.png\n\n**2.**\n\n```\n**3.** not an item\n```\n\nsee http://h/session?room=room-1&msg=m1';
const it = T.pointItems(sent);
out.items = it;
out.tail = T.itemTail(it.items[0]);
out.body = T.itemBody(it.items[1]).split('\n')[0];
out.notItems = [T.pointItems('## Points (1)\n\nno item'), T.pointItems('**1.** no head'), T.pointItems('plain')];
out.stripped = T.stripRefBlocks(sent);
out.strippedSame = T.stripRefBlocks('## Points (1)\n\n**1.** x') === '## Points (1)\n\n**1.** x';
// The head's block goes too, its image kept under the head.
out.strippedHead = T.stripRefBlocks('## Points (1)\n\nIntro http://h/session?room=room-1&msg=m1\n\n[ref http://h/session?room=room-1&msg=m1] from claude in "Docs" at 2026-09-22 10:00:\n> Merged.\n\n[image] C:\\t\\attachments\\h.png\n\n**1.** x');
out.html = T.mdToHtml('## Points (2)\n\nHead words\n\n**1.** first\n[image] C:\\t\\attachments\\a.png\n\n**2.** second\n\nmore');
out.htmlHeadImg = T.mdToHtml('## Points (2)\n\nHead words\n[image] C:\\t\\attachments\\h.png\n\n**1.** first\n\n**2.** second');
// A PO chat's images are in <state>/attachments/<room id>/.
const po = n => '[image] C:\\s\\attachments\\room-aaaa0001\\' + n + '.png';
out.htmlThree = T.mdToHtml(['## Points (5)', '**1.** one', '**2.** two', '**3.** three', po('a'), '**4.** four', po('b'), '**5.** five', po('c')].join('\n'));
out.htmlThreeGaps = T.mdToHtml(['## Points (5)', '', '**1.** one', '', '**2.** two', '', '**3.** three', po('a'), '', '**4.** four', po('b'), '',
  '**5.** five', po('c')].join('\n'));
out.htmlInline = T.mdToHtml('words\n[image] C:\\t\\attachments\\a.png\nthen more');
out.htmlNotOwn = T.mdToHtml('[image] C:\\elsewhere\\a.png\nwords');
out.fold = [T.foldLine('## Points (2)\n\nAfter tests.\n\n**1.** a\n\n**2.** b'), T.foldLine('## Points (1)\n\n**1.** the first point\n[image] C:\\t\\attachments\\a.png'),
  T.foldLine('## Review comments (3)\n\n**1.** > q\n\n**2.** r\n\n**3.** s')];
// --- the chips: one per item ---
const P = T.pointMaps({ open: 2, answered: 1, approvals: [], items: [
  { id: 'P2', state: 'open', text: 'b', createdAt: 1000, mid: 'm1', answers: [] },
  { id: 'P1', state: 'answered', text: 'a', createdAt: 1000, mid: 'm1', answers: [{ mid: 'm2', at: 1100 }] },
  { id: 'P3', state: 'open', text: 'c', createdAt: 900, mid: 'm0', followUps: ['m1'], answers: [] },
] });
const msg = { id: 'm1', from: 'user', text: '## Points (2)\n\n**1.** a\n\n**2.** b' };
out.own = T.ptOwn(msg, P).map(p => p.id);
out.perItem = T.pointItemsHtml(msg, T.pointItems(msg.text), P, mid => '#' + mid, T.mdToHtml);
out.mismatch = T.pointItemsHtml({ id: 'm1', from: 'user', text: '## Points (3)\n\n**1.** a\n\n**2.** b\n\n**3.** c' }, T.pointItems('## Points (3)\n\n**1.** a\n\n**2.** b\n\n**3.** c'), P, mid => '#' + mid, T.mdToHtml);
// Words above the list are the first point: their chip under them, above the list.
const P3 = T.pointMaps({ open: 3, answered: 0, approvals: [], items: [
  { id: 'P1', state: 'open', text: 'Head q', createdAt: 1000, mid: 'm3', answers: [] },
  { id: 'P2', state: 'open', text: 'a', createdAt: 1000, mid: 'm3', answers: [] },
  { id: 'P3', state: 'open', text: 'b', createdAt: 1000, mid: 'm3', answers: [] },
] });
const withHead = '## Points (2)\n\nHead q\n[image] C:\\t\\attachments\\h.png\n\n**1.** a\n\n**2.** b';
out.headChip = T.pointItemsHtml({ id: 'm3', from: 'user', text: withHead }, T.pointItems(withHead), P3, mid => '#' + mid, T.mdToHtml);
// Only images above the list: three points do not fit two items, the one bar.
const imgHead = '## Points (2)\n\n[image] C:\\t\\attachments\\h.png\n\n**1.** a\n\n**2.** b';
out.headImgNoChip = T.pointItemsHtml({ id: 'm3', from: 'user', text: imgHead }, T.pointItems(imgHead), P3, mid => '#' + mid, T.mdToHtml);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class Editor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = "\n".join([ATTACH, MODEL, ITEMS, js_const("REF_BLOCK_RE"), js_function("stripRefBlocks"), js_function("mdToHtml"),
                          js_function("itemsHtml"), js_function("foldLine"), js_function("pointBarHtml"), js_function("ptOwn"),
                          js_function("pointItemsHtml"), js_function("pointMaps"), js_const("headWords")])
        run = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        if run.returncode != 0:
            raise AssertionError(run.stderr)
        cls.r = json.loads(run.stdout)

    def test_plain_words_go_as_they_are(self):
        self.assertEqual(self.r["plain"], "hello there")
        self.assertEqual(self.r["plainStill"], "hello there", "an empty point is no point")

    def test_points_go_as_the_form_the_hub_reads(self):
        self.assertEqual(self.r["points"], (
            "## Points (4)\n\nhello there\n\n"
            "**1.** The board is slow\nsince this morning\n\n"
            "**2.**\n\n```js\nx()\n```\n[image] 2026-09-22 10.00.00 screenshot.png\n[image] b.png\n\n"
            "**3.**\n\n- a list\n- of two\n\n"
            "**4.**\n[image] only.png"))
        self.assertEqual(self.r["noHead"], ["## Points (4)", "", "**1.** The board is slow"])

    def test_the_heads_images_stay_above_the_first_point(self):
        self.assertEqual(self.r["headImgOnly"], ["## Points (4)", "", "[image] h.png", ""])
        self.assertEqual(self.r["headImg"], ["## Points (4)", "", "words", "[image] h.png", ""])
        self.assertEqual(self.r["headImgPlain"], "just words", "no point: the hub ends the message with them, as ever")

    def test_the_keys(self):
        k = self.r["keys"]
        self.assertEqual(k["send"], ["send", "send"])
        self.assertEqual(k["add"], ["add", "add"])
        self.assertEqual(k["tabLast"], "add")
        self.assertEqual([k["tabLastEmpty"], k["tabNotLast"], k["shiftTab"], k["tabHead"]], [None] * 4, "Tab otherwise moves focus")
        self.assertIsNone(k["enter"], "Enter in a point is a new line")
        self.assertEqual(k["backspaceEmpty"], "remove")
        self.assertEqual([k["backspaceWords"], k["backspaceHead"]], [None, None])
        self.assertEqual(self.r["trigger"], ["first thing", "first thing", None, None, None, "x", None, ""])

    def test_add_remove_move(self):
        self.assertEqual(self.r["order1"], ["a", "c", "b"])
        self.assertEqual(self.r["moved"], [1, "cab", 0, 2, "cab"])
        self.assertEqual(self.r["removed"], ["a", "cb", None])
        self.assertTrue(self.r["ids"])

    def test_the_first_point_under_a_head_with_words_makes_them_point_1(self):
        # "+ Add a point" (or Ctrl+Shift+Enter) with words in the head and no
        # point yet: the words become point 1, so the new point is point 2
        # and nothing stays above the list; once there are points, or with
        # no words, nothing moves.
        self.assertEqual(self.r["headToPoint"], [True, ["first question \nmore"], False, 1, False, False])

    def test_a_draft_round_trips(self):
        self.assertEqual(self.r["draft"], {"head": "h", "images": [{"room": "r", "name": "top.png"}],
                                           "points": [{"text": "p1", "images": [{"room": "r", "name": "n.png"}]}]},
                         "the head's stored images are kept too; one not stored is not")
        self.assertEqual(self.r["back"], "## Points (1)\n\nh\n[image] top.png\n\n**1.** p1\n[image] n.png")
        self.assertEqual(self.r["backImages"], [{"room": "r", "name": "top.png"}])
        self.assertEqual(self.r["badDraft"], ["", "", 0, 0])
        self.assertEqual(self.r["has"], [False, True, True, True, False], "a draft of only a head image is worth keeping")

    def test_a_sent_message_is_read_back_as_its_items(self):
        it = self.r["items"]
        self.assertEqual(it["title"], "## Points (2)")
        self.assertEqual(it["head"], "After tests.")
        self.assertEqual(len(it["items"]), 2)
        self.assertTrue(it["items"][1].startswith("**2.**\n\n```\n**3.** not an item\n```"))
        self.assertEqual(self.r["tail"], ["**1.** The board is slow, see http://h/session?room=room-1&msg=m1\n\n[ref http://h/session?room=room-1&msg=m1] from claude in \"Docs\" at 2026-09-22 10:00:\n> Merged.",
                                          "[image] C:\\t\\attachments\\a.png"])
        self.assertEqual(self.r["body"], "```")
        self.assertEqual(self.r["notItems"], [None, None, None])

    def test_each_items_blocks_are_stripped_inside_it(self):
        self.assertEqual(self.r["stripped"], (
            "## Points (2)\n\nAfter tests.\n\n**1.** The board is slow, see http://h/session?room=room-1&msg=m1\n\n[image] C:\\t\\attachments\\a.png\n\n"
            "**2.**\n\n```\n**3.** not an item\n```\n\nsee http://h/session?room=room-1&msg=m1"))
        self.assertTrue(self.r["strippedSame"])
        self.assertEqual(self.r["strippedHead"], "## Points (1)\n\nIntro http://h/session?room=room-1&msg=m1\n\n[image] C:\\t\\attachments\\h.png\n\n**1.** x",
                         "the head's block goes too, its image kept")

    def test_the_balloon_is_a_numbered_list_with_images_in_place(self):
        h = self.r["html"]
        self.assertTrue(h.startswith('<div class="ln">Head words</div><ol class="pt-items"><li class="pt-item">'), h)
        self.assertIn('<li class="pt-item"><div class="ln">first</div><div class="att-thumbs"><a class="att-thumb" href="/api/room/attachment?room=room-aaaa0001&amp;name=a.png"', h)
        self.assertIn('<li class="pt-item"><div class="ln">second</div>', h)
        self.assertEqual(h.count("<li"), 2)
        hh = self.r["htmlHeadImg"]
        self.assertTrue(hh.startswith('<div class="ln">Head words</div><div class="att-thumbs"><a class="att-thumb" href="/api/room/attachment?room=room-aaaa0001&amp;name=h.png"'), hh)
        self.assertEqual(hh.count("att-thumbs"), 1, "the head's image is above the list, in no item")
        self.assertIn('<ol class="pt-items"><li class="pt-item"><div class="ln">first</div></li>', hh)
        # P47: a screenshot under each of points 3 to 5, as the hub gives the balloon back.
        for three in (self.r["htmlThree"], self.r["htmlThreeGaps"]):
            items = three.split('<li class="pt-item">')[1:]
            self.assertEqual(len(items), 5, three)
            self.assertEqual([it.count('class="att-thumb"') for it in items], [0, 0, 1, 1, 1], three)
            for it, name in zip(items[2:], "abc"):
                self.assertIn(f'href="/api/room/attachment?room=room-aaaa0001&amp;name={name}.png"', it)
            self.assertNotIn("[image]", three)
        inline = self.r["htmlInline"]
        self.assertTrue(inline.startswith('<div class="ln">words</div>\n<div class="att-thumbs">'), inline)
        self.assertTrue(inline.endswith('</div>\n<div class="ln">then more</div>'), inline)
        self.assertNotIn("att-thumbs", self.r["htmlNotOwn"], "an image from elsewhere is text")

    def test_a_folded_row_counts_the_points(self):
        self.assertEqual(self.r["fold"], ["2 points · After tests.", "1 point · the first point", "3 points · q"])

    def test_each_item_gets_its_own_point_chip(self):
        self.assertEqual(self.r["own"], ["P1", "P2"], "the message's own points, in order; a follow-up is not among them")
        h = self.r["perItem"]
        self.assertEqual(h.count('<div class="pt-bar">'), 3, "one bar per item, and one for the follow-up")
        first = h[h.index('<li class="pt-item">'):h.index('<li class="pt-item">', h.index('<li class="pt-item">') + 1)]
        self.assertIn("P1 · answered", first)
        self.assertNotIn("P2", first)
        self.assertIn('data-pt="P2" data-pt-act="drop"', h)
        self.assertTrue(h.endswith('<div class="pt-bar"><span class="pt-chip open">P3 · waiting for an answer</span><button data-pt="P3" data-pt-act="drop">Drop</button></div>'), h)
        self.assertIsNone(self.r["mismatch"], "the hub read it as fewer points: the one bar under the text")

    def test_words_above_the_list_get_the_first_chip(self):
        h = self.r["headChip"]
        self.assertIsNotNone(h)
        self.assertEqual(h.count('<div class="pt-bar">'), 3, "one bar for the head, one per item")
        head_end = h.index('<ol class="pt-items">')
        self.assertIn("P1 · waiting for an answer", h[:head_end], "the head's chip stands above the list")
        self.assertNotIn("P2", h[:head_end])
        first = h[h.index('<li class="pt-item">'):]
        self.assertIn("P2 · waiting", first[:first.index("</li>")])
        self.assertIsNone(self.r["headImgNoChip"], "an image alone above the list is no point: the counts do not fit")


if __name__ == "__main__":
    unittest.main()
