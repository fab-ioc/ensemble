"""Chat balloons say who writes to whom in task terms, and a task's older
messages fold under its latest (session.html).

The page's own functions run in Node and these check that:

* a header names the task, the PO and the person: "#26 claude → PO",
  "ceo → PO", "PO → #26 claude", "Hub → PO", with no "@";
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
Object.assign(T.CHAT_NAMES, { operator: 'ceo', po: 'claude', taskNo: id => ({ 'room-0804cfef': 26 })[id] || null });

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
out.kept = T.chatGroups(room, (m, i) => i === 2).map(g => ({ task: g.task, hidden: g.hidden }));
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
        self.assertEqual(self.r["senders"], ["ceo", "#26 claude", "#26 claude · review 1", "PO", "11112222 claude",
                                             "Hub", "#26 claude · review 2", "Hub", "#26 claude", "ceo", "#26 claude"])
        self.assertEqual(self.r["recipients"], ["PO", "PO", "PO", "#26 claude", "PO", "PO", "PO", "PO", "PO", "PO", "PO"])
        self.assertEqual(self.r["head"][0], '<span class="who">#26 claude</span> <span class="to">→ PO</span>')
        self.assertEqual(self.r["head"][1], '<span class="who hub-who">Hub</span> <span class="to">→ PO</span>')
        self.assertNotIn("@", "".join(self.r["head"]))

    def test_names(self):
        self.assertEqual(self.r["names"], [26, 7, None, "#26", "9abcdef0", "#60", "ED-18",
                                           "ceo", "Hub", "PO", "codex", "#26 codex-2"])
        self.assertEqual(self.r["task"], ["claude", "", "claude"])

    def test_what_each_message_is(self):
        self.assertEqual(self.r["kinds"], ["ceo", "update", "review 1 changes requested", "PO ruling", "update",
                                           "progress check", "review 2 approved", "progress check", "completed",
                                           "ceo", "update"])
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


if __name__ == "__main__":
    unittest.main()
