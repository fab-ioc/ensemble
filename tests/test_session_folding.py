"""Chat balloons say who writes to whom in task terms, and a task's older
messages fold under its latest (session.html).

The page's own functions run in Node and these check that:

* a header names the task, the PO and the person: "#26 claude → PO",
  "sam → PO", "PO → #26 claude", "Hub → PO", with no "@";
* a review report says its round, and the row chip its verdict;
* between two of the person's messages a task's messages are one row, a
  task's latest completed report stays in view below it, progress checks are
  one row, and fewer than two to hide make no row;
* a message the reader has in view is never folded into a row;
* a superseded report points at the task's latest report.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def sends_block(src: str) -> str:
    return src[src.index("// ---- What you sent, until the conversation shows it: begin"):
               src.index("// ---- What you sent, until the conversation shows it: end")]


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])) };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, whoName, whoHtml, senderName, recipientName, kindName, hubLabel, foldChips,
    chatGroups, supersededMap, supersededText, soloItems, titleNo, taskName, groupRowHtml, answerChip };`, ctx);
const T = ctx.t;
const out = {};
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: id => ({ 'room-0804cfef': 26 })[id] || null });

// A PO's room, as the hub keeps it.
const R = 'claude@room-0804cfef', S = 'claude@room-11112222';
const rep = (from, reportKind, text, extra) => Object.assign({ from, to: 'claude', kind: 'report', reportKind,
  taskId: from.split('@')[1], text }, extra || {});
const room = [
  { id: 'm0', from: 'user', to: 'all', text: 'Go on with both.', ts: 1000 },
  rep(R, 'update', 'Started.', { id: 'm1', ts: 1010 }),
  rep(R, 'review', '**review 1 — changes requested** of task …', { id: 'm2', verdict: 'changes_requested', ts: 1020 }),
  { id: 'm3', from: 'claude', to: R, text: 'Fix the finding first.', ts: 1030 },
  rep(S, 'update', 'Halfway.', { id: 'm4', ts: 1035 }),
  { id: 'm5', from: 'ensemble', to: 'claude', kind: 'report', reportKind: 'digest', text: 'Progress: …', ts: 1040 },
  rep(R, 'review', '**review 2 — approved** of task …', { id: 'm6', verdict: 'approve', ts: 1050 }),
  { id: 'm7', from: 'ensemble', to: 'claude', kind: 'report', reportKind: 'digest', text: 'Progress: …', ts: 1060 },
  rep(R, 'completed', 'Merged.', { id: 'm8', ts: 1070 }),
  { id: 'm9', from: 'user', to: '', text: 'Thanks.', ts: 1080 },
  rep(R, 'update', 'One more.', { id: 'm10', ts: 1090 }),
];
out.senders = room.map(m => T.senderName(m));
out.recipients = room.map(m => T.recipientName(m));
out.kinds = room.map(m => T.kindName(m));
out.head = [T.whoHtml(room[1]), T.whoHtml(room[5]), T.whoHtml(room[0])];
out.reviewChip = T.foldChips(room[2]);
out.groups = T.chatGroups(room).map(g => ({ task: g.task, hidden: g.hidden, stand: g.stand, at: g.at }));
out.kept = T.chatGroups(room, i => i === 2).map(g => ({ task: g.task, hidden: g.hidden }));
const g = T.chatGroups(room)[0];
out.row = T.groupRowHtml(g, room, false);
out.sup = [...T.supersededMap(room)].sort((a, b) => a[0] - b[0]);
out.supText = [T.supersededText(room[8]).replace(/\d\d?[:.]\d\d.*$/, 'T'), T.supersededText(room[7]).replace(/\d\d?[:.]\d\d.*$/, 'T')];
out.names = [T.titleNo('26. The screen'), T.titleNo('#7 Docs'), T.titleNo('Docs 2. x'),
  T.taskName('room-0804cfef'), T.taskName('room-9abcdef0'), T.taskName('#60'), T.taskName('ED-18'),
  T.whoName('user'), T.whoName('ensemble'), T.whoName('claude'), T.whoName('codex'), T.whoName('codex-2@room-0804cfef')];

// Not a PO's room: nobody is "PO", an unaddressed message has no recipient.
T.CHAT_NAMES.po = '';
out.task = [T.senderName({ from: 'claude', text: 'x' }), T.recipientName({ from: 'user', to: 'all' }),
  T.recipientName({ from: 'user', to: 'claude' })];

// A solo PO's transcript: what the hub typed in names the task it came from.
T.CHAT_NAMES.po = 'claude';
const turns = [
  { role: 'user', kind: 'report', reportKind: 'review 2 (approved)', taskId: '#60', reporter: 'codex', taskTitle: 'Docs', text: '[report] …', timestamp: '2026-09-16T10:00:00Z' },
  { role: 'assistant', answers: { kind: 'report', taskId: '#60', reporter: 'codex' }, text: 'Good.' },
  { role: 'user', kind: 'report', reportKind: 'completed', taskId: '#60', reporter: 'claude', taskTitle: 'Docs', text: '[report] …' },
  { role: 'user', kind: 'digest', text: '[digest] …' },
];
const solo = T.soloItems(turns, 'S', 'claude', 120);
out.solo = solo.map(m => [T.senderName(m), T.recipientName(m), T.kindName(m)]);
out.soloTs = solo[0].ts;
out.soloChip = T.answerChip(solo[1].answers).text;
out.soloLabel = [T.hubLabel(solo[0]), T.hubLabel(solo[2]), T.hubLabel(solo[3])];
out.soloGroups = T.chatGroups(solo).map(g => ({ task: g.task, hidden: g.hidden, stand: g.stand }));
console.log(JSON.stringify(out));
"""


_RUN = None


def run() -> dict:
    global _RUN
    if _RUN is None:
        code = fold_block(SRC) + js_function(SRC, "soloItems")
        out = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        _RUN = json.loads(out.stdout)
    return _RUN


@unittest.skipUnless(NODE, "node is not installed")
class WhoWritesToWhom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run()

    def test_headers_name_the_task_the_po_and_the_person(self):
        self.assertEqual(self.r["senders"], ["sam", "#26 claude", "#26 claude · review 1", "PO", "11112222 claude",
                                             "Hub", "#26 claude · review 2", "Hub", "#26 claude", "sam", "#26 claude"])
        self.assertEqual(self.r["recipients"], ["PO", "PO", "PO", "#26 claude", "PO", "PO", "PO", "PO", "PO", "PO", "PO"])
        self.assertEqual(self.r["head"][0], '<span class="who">#26 claude</span> <span class="to">→ PO</span>')
        self.assertEqual(self.r["head"][1], '<span class="who hub-who">Hub</span> <span class="to">→ PO</span>')
        self.assertNotIn("@", "".join(self.r["head"]))

    def test_names(self):
        self.assertEqual(self.r["names"], [26, 7, None, "#26", "9abcdef0", "#60", "ED-18",
                                           "sam", "Hub", "PO", "codex", "#26 codex-2"])
        self.assertEqual(self.r["task"], ["claude", "", "claude"])

    def test_what_each_message_is(self):
        self.assertEqual(self.r["kinds"], ["sam", "update", "review 1 changes requested", "PO ruling", "update",
                                           "progress check", "review 2 approved", "progress check", "completed",
                                           "sam", "update"])
        self.assertIn('>Changes requested</span>', self.r["reviewChip"])

    def test_a_solo_transcript(self):
        self.assertEqual(self.r["solo"], [["#60 codex · review 2", "PO", "review 2 approved"],
                                          ["PO", "", "PO reply"],
                                          ["#60 claude", "PO", "completed"],
                                          ["Hub", "PO", "progress check"]])
        self.assertEqual(self.r["soloTs"], 1789552800)
        self.assertEqual(self.r["soloChip"], "on a report from #60 codex")
        self.assertEqual(self.r["soloLabel"], ["Approved", "Completed", "Progress check"])
        # A review, the PO's reply and the completed report: the completed one stays.
        self.assertEqual(self.r["soloGroups"], [{"task": "#60", "hidden": [0, 1], "stand": 2}])


@unittest.skipUnless(NODE, "node is not installed")
class ATasksMessagesFold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run()

    def test_one_row_per_task_between_the_persons_messages(self):
        self.assertEqual(self.r["groups"], [
            # progress checks: one row where the latest was
            {"task": "~digests", "hidden": [5, 7], "stand": -1, "at": 7},
            # #26: its completed report stays in view, the row goes just above it
            {"task": "room-0804cfef", "hidden": [1, 2, 3, 6], "stand": 8, "at": 8},
        ])
        # the other task has one message, and after "Thanks." only one: no rows

    def test_what_the_reader_has_in_view_does_not_fold(self):
        self.assertEqual(self.r["kept"], [{"task": "~digests", "hidden": [5, 7]},
                                          {"task": "room-0804cfef", "hidden": [1, 3, 6]}])

    def test_the_row_says_what_is_in_it(self):
        row = self.r["row"]
        self.assertIn('data-group="g:~digests:m5"', row)
        self.assertIn('aria-expanded="false"', row)
        self.assertIn(">Hub</span>", row)
        self.assertIn("2 progress checks", row)

    def test_superseded_points_at_the_latest(self):
        self.assertEqual(self.r["sup"], [[1, 10], [2, 10], [5, 7], [6, 10], [8, 10]])
        self.assertEqual(self.r["supText"], ["Superseded: #26 reported completed at T",
                                             "Superseded: a later progress check at T"])


RENDER_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
// Just enough of a DOM for renderBubbles and patchChildren: an element is its
// markup's first tag, attributes parsed from it.
class El {
  constructor(h) {
    this.attrs = new Map();
    const tag = h.match(/^<(\w+)([^>]*)>/);
    this.tagName = tag[1].toUpperCase();
    for (const a of tag[2].matchAll(/([\w-]+)="([^"]*)"/g)) this.attrs.set(a[1], a[2]);
    this.innerHTML = h.slice(tag[0].length);
    this.parent = null;
  }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  hasAttribute(n) { return this.attrs.has(n); }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  removeAttribute(n) { this.attrs.delete(n); }
  get dataset() {
    const d = {};
    this.attrs.forEach((v, k) => { if (k.startsWith('data-')) d[k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase())] = v; });
    return d;
  }
  get isConnected() { return !!this.parent; }
  get offsetTop() { return 0; }
  get offsetHeight() { return 10; }
  get nextElementSibling() { const k = this.parent.kids; return k[k.indexOf(this) + 1] || null; }
  remove() { const k = this.parent.kids; k.splice(k.indexOf(this), 1); this.parent = null; }
}
const box = { kids: [], scrollTop: 0, clientHeight: 400, scrollHeight: 4000,
  get children() { return this.kids.slice(); },
  get firstElementChild() { return this.kids[0] || null; },
  contains: () => false,
  insertBefore(n, ref) { if (n.parent) n.remove(); n.parent = this; const i = ref ? this.kids.indexOf(ref) : this.kids.length; this.kids.splice(i, 0, n); } };
const ctx = {
  esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  window: { getSelection: () => null },
  document: { createElement: () => ({ set innerHTML(h) { this.content = { firstElementChild: new El(h) }; } }) },
  box,
};
vm.createContext(ctx);
vm.runInContext(code + `
  let FOLD = { base: null, open: new Set(), groups: new Set(), hid: null };
  let GROUP_OF_MID = new Map(), CHAT_VIEW = null, LAST_ITEMS = null, STICK = true, SEEN = new Set(), NEW_N = 0, DEC_N = 0;
  let COMMENTS = [], JUST_US = false, SOLO_IDLE = false, ROOM_OBJ = null, ROOM_PENDING = null;
  let MD_CACHE = new Map(), MD_CTX = '', _cmtComposerOpen = false, _selBtn = null, PATH_ABBR = null;
  const THUMB_GONE = new Set();
  const ROOM_NOS = new Map(), HUB_PORT = '', ROOM = 'room-po', REF_GEN = 0;
  const $ = () => box, fileBase = () => '', mdToHtml = t => t, copyLinkHtml = () => '', refPlain = t => t;
  const withTaskBubble = items => items, rotationText = () => '', readingPlace = () => null, keepPlace = () => {};
  const showChatBar = () => {}, applyComments = () => {}, showLatest = () => {}, markLanded = () => {}, landPending = () => {};
  const showAskLine = () => {};
  let PM_IDX = new Map(), PM_SIG = ''; const pmIndex = () => PM_IDX, pmPlain = t => t;
  let CU_POINT = null, CU_SHOWN = false, CU_OPENED = true, CU_SNAP = null;
  const readTick = () => {}, catchUpOpen = () => {}, refHref = (room, mid) => '?msg=' + mid;
  globalThis.t = {
    CHAT_NAMES, render: items => renderBubbles(items), fold: () => FOLD,
    set: (k, v) => eval(k + ' = v'), get: k => eval(k),
    row: key => box.kids.find(k => k.dataset.group === key),
  };`, ctx);
const T = ctx.t;
T.CHAT_NAMES.po = 'claude';
const out = {};
const R = 'claude@room-aaaa1111';
const rep = (id, reportKind, text) => ({ id, from: R, to: 'claude', kind: 'report', reportKind, taskId: 'room-aaaa1111', text });
const groupRows = () => box.kids.filter(k => k.dataset.group);
const base = [{ id: 'u0', from: 'user', to: '', text: 'Go.' }, rep('r1', 'update', 'one'), rep('r2', 'update', 'two')];

// At the end: everything seen. Scrolled up, an update joins the closed group.
T.render(base);
const key = groupRows()[0].dataset.group, before = T.row(key);
T.set('STICK', false);
const more = base.concat([rep('r3', 'update', 'three')]);
T.render(more);
out.joined = { newN: T.get('NEW_N'), sameButton: T.row(key) === before,
  summary: T.row(key).innerHTML.includes('3 messages'), groups: groupRows().length };
T.render(more);
out.joined.again = T.get('NEW_N');

// An opened group with a completed report standing: a comment holding one of
// its messages does not close it, nor does the comment going.
const done = base.concat([rep('r3', 'completed', 'done')]);
const comment = [{ cid: 'c1', mid: 'r1', from: R, quote: 'one', note: 'n' }];
T.set('FOLD', { base: null, open: new Set(), groups: new Set(), hid: null });
T.set('STICK', true);
T.render(done);
const g2 = groupRows()[0].dataset.group;
T.fold().groups.add(g2);
T.render(done);
out.held = [T.row(g2).getAttribute('aria-expanded')];
T.set('COMMENTS', comment); T.set('STICK', false);
T.render(done);
out.held.push(T.fold().groups.has(g2), T.row(g2) ? T.row(g2).getAttribute('aria-expanded') : null);
T.set('COMMENTS', []); T.set('STICK', true);
T.render(done);
out.held.push(T.fold().groups.has(g2), T.row(g2) ? T.row(g2).getAttribute('aria-expanded') : null);
// Closed, the same comment keeps its message out of the row: one left, no row.
T.fold().groups.delete(g2);
T.set('COMMENTS', comment);
T.render(done);
out.closedHeld = groupRows().length;
T.set('COMMENTS', []);
T.render(done);
out.closedBack = groupRows().length;

// The PO's ruling as a task's last word stays in view.
T.set('FOLD', { base: null, open: new Set(), groups: new Set(), hid: null });
T.render(base.concat([{ id: 'p1', from: 'claude', to: R, text: 'Use the safe path.' }]));
out.ruling = box.kids.map(k => k.dataset.group ? 'row' : k.dataset.key);

// The catch-up line goes above what is drawn first after the read point, a
// group's row when the first is folded in it; none once the point is the end.
T.set('FOLD', { base: null, open: new Set(), groups: new Set(), hid: null });
const back = base.concat([{ id: 'p2', from: 'claude', to: '', text: 'Answer.' }, rep('r4', 'completed', 'done')]);
T.set('CU_POINT', { id: 'u0', ts: 0 });
T.render(back);
out.catchup = [box.kids.map(k => k.dataset.group ? 'row' : k.dataset.key), T.get('CU_SHOWN')];
// A poll with a new message keeps the drawn line: the same element, the same words.
const lineNode = () => box.kids.find(k => k.dataset.key === 'catchup');
const was = lineNode(), wasHtml = was.innerHTML;
T.render(back.concat([{ id: 'p3', from: 'claude', to: '', text: 'More.' }]));
out.catchupKept = [lineNode() === was, lineNode().innerHTML === wasHtml];
T.set('CU_SNAP', null);
T.set('CU_POINT', { id: 'r2', ts: 0 });
T.render(back.slice(0, 3).concat([rep('r4', 'completed', 'done'), { id: 'p2', from: 'claude', to: '', text: 'Answer.' }]));
out.catchup.push(box.kids.map(k => k.dataset.group ? 'row' : k.dataset.key));
T.set('CU_POINT', { id: 'r4', ts: 0 });
T.render(back);
out.catchup.push(box.kids.map(k => k.dataset.group ? 'row' : k.dataset.key), T.get('CU_SHOWN'));
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class WhileThePageRedraws(unittest.TestCase):
    """renderBubbles itself, polled: groups that change, open, or hold a comment."""

    @classmethod
    def setUpClass(cls):
        code = fold_block(SRC) + sends_block(SRC) + js_function(SRC, "renderBubbles") + js_function(SRC, "patchChildren")
        out = subprocess.run([NODE, "-e", RENDER_JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.r = json.loads(out.stdout)

    def test_a_message_joining_a_closed_group_counts_as_new_once(self):
        j = self.r["joined"]
        self.assertEqual(j["newN"], 1, "the group a new update joined was not counted")
        self.assertEqual(j["again"], 1, "an unchanged poll changed the count")
        self.assertTrue(j["summary"])
        self.assertEqual(j["groups"], 1)
        self.assertTrue(j["sameButton"], "the poll replaced the group's button under the pointer")

    def test_an_opened_group_stays_open_through_a_comment(self):
        self.assertEqual(self.r["held"], ["true", True, "true", True, "true"])

    def test_a_comment_keeps_its_message_out_of_a_closed_group(self):
        self.assertEqual((self.r["closedHeld"], self.r["closedBack"]), (0, 1))

    def test_the_pos_ruling_stays_in_view(self):
        self.assertEqual(self.r["ruling"], ["u0", "row", "p1"])

    def test_the_catch_up_line_sits_above_the_first_unread(self):
        first, shown, folded, none, gone = self.r["catchup"]
        self.assertEqual(first, ["u0", "catchup", "p2", "row", "r4"])
        self.assertTrue(shown)
        self.assertEqual(folded, ["u0", "catchup", "row", "r4", "p2"])
        self.assertEqual(none, ["u0", "p2", "row", "r4"])
        self.assertFalse(gone)

    def test_a_poll_keeps_the_catch_up_line(self):
        self.assertEqual(self.r["catchupKept"], [True, True])


REF_JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const ctx = { esc: s => String(s) };
vm.createContext(ctx);
vm.runInContext(code + `
  const ROOM = 'room-here';
  globalThis.t = { CHAT_NAMES, refWho };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: '', taskNo: id => ({ 'room-0804cfef': 26 })[id] || null });
const other = { roomId: 'room-other-po', isPo: true };
console.log(JSON.stringify([
  T.refWho(Object.assign({ from: 'claude@room-0804cfef', who: 'claude@room-0804cfef' }, other)),
  T.refWho(Object.assign({ from: 'claude', who: 'claude' }, other)),
  T.refWho(Object.assign({ from: 'ensemble', who: 'ensemble' }, other)),
  T.refWho(Object.assign({ from: 'user', who: 'sam' }, other)),
  T.refWho({ roomId: 'room-task', isPo: false, from: 'claude', who: 'claude' }),
]));
"""


@unittest.skipUnless(NODE, "node is not installed")
class LinkChipsNameTheWriter(unittest.TestCase):
    def test_a_link_into_another_pos_room(self):
        code = fold_block(SRC) + js_function(SRC, "refWho")
        out = subprocess.run([NODE, "-e", REF_JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        # a task's report there, the PO's own message, the hub, the person, a task room's agent
        self.assertEqual(json.loads(out.stdout), ["#26 claude", "PO", "Hub", "sam", "claude"])


if __name__ == "__main__":
    unittest.main()
