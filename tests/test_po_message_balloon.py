"""A message between two projects' POs draws as a balloon of its own in each
PO's chat (session.html): "opten PO → Dock PO" with a "PO to PO · bug" chip,
never folded as hub traffic nor into a task's row, and in a one-agent PO chat
it is taken from the room and put among the transcript's turns by its time.

The page's own functions run in Node. Skipped without Node.
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
const ctx = { esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  attSplit: t => ({ paths: [], words: t }) };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { CHAT_NAMES, whoHtml, senderName, recipientName, kindName, foldChips, foldBalloonHtml,
    quietItem, taskOfMsg, chatGroups, withHubRows, soloItems, isHubInput, hubLabel, answerChip };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const out = {};
const pm = (id, direction, ts, extra) => Object.assign({ id, kind: 'pomsg', direction, ts, rang: [],
  from: direction === 'received' ? 'claude@room-0pten000' : 'claude',
  to: direction === 'received' ? 'claude' : 'claude@room-d0c00000',
  fromProjectName: 'opten', toProjectName: 'Dock', poKind: 'bug', text: '## Splitter jumps\n\nThe whole report.' }, extra || {});
const got = pm('pm-1', 'received', 1010);
const sent = pm('pm-2', 'sent', 1020, { fromProjectName: 'Dock', toProjectName: 'opten', poKind: 'answer', from: 'claude', to: 'claude@room-0pten000' });
out.head = [T.whoHtml(got), T.whoHtml(sent)];
out.kind = T.kindName(got);
out.chip = T.foldChips(got);
out.full = T.foldBalloonHtml(got, 0, 'full', { md: t => t });
out.row = T.foldBalloonHtml(got, 0, 'row', { md: t => t });
out.quiet = [T.quietItem(got, false), T.quietItem(got, true)];
out.task = T.taskOfMsg(got);
// Among a task's reports it is no part of the task's row.
const R = 'claude@room-aaaa1111';
const rep = (id, ts) => ({ id, from: R, to: 'claude', kind: 'report', reportKind: 'update', taskId: 'room-aaaa1111', text: 'x', ts });
const room = [{ id: 'u', from: 'user', text: 'Go.', ts: 900 }, rep('r1', 1000), got, rep('r2', 1030), rep('r3', 1040)];
out.groups = T.chatGroups(room).map(g => g.hidden);
// A one-agent PO chat: the room's PO messages go among the turns by time.
const turns = [
  { role: 'user', kind: 'human', text: 'Look at Dock.', timestamp: new Date(1000 * 1000).toISOString() },
  { role: 'assistant', text: 'Writing to the Dock PO.', timestamp: new Date(1015 * 1000).toISOString() },
  { role: 'user', kind: 'pomsg', text: '[from the Dock PO] answer: Fixed …', timestamp: new Date(1025 * 1000).toISOString() },
  { role: 'assistant', answers: { kind: 'pomsg' }, text: 'Dock fixed it.', timestamp: new Date(1030 * 1000).toISOString() },
];
const items = T.withHubRows(T.soloItems(turns, 'S', 'claude', 120), { messages: [got, sent, { id: 'x', from: 'user', text: 'not a row', ts: 1012 }] });
out.solo = items.map(m => m.id);
out.typed = [T.isHubInput(items[4]), T.quietItem(items[4], false), T.hubLabel(items[4])];
out.answer = T.answerChip(items[5].answers).text;
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class APoMessageBalloon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = fold_block(SRC) + js_function(SRC, "soloItems") + js_function(SRC, "withHubRows")
        res = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.r = json.loads(res.stdout)

    def test_it_says_which_po_writes_to_which(self):
        self.assertEqual(self.r["head"], [
            '<span class="who">opten PO</span> <span class="to">→ Dock PO</span>',
            '<span class="who">Dock PO</span> <span class="to">→ opten PO</span>'])
        self.assertEqual(self.r["kind"], "PO message")

    def test_it_is_marked_apart_from_a_report(self):
        self.assertIn(">PO to PO · bug</span>", self.r["chip"])
        self.assertIn("not a task’s report", self.r["chip"])
        self.assertIn("opten PO", self.r["full"])
        self.assertIn("PO to PO · bug", self.r["full"])
        self.assertIn("The whole report.", self.r["full"])
        self.assertIn("Splitter jumps", self.r["row"])

    def test_it_is_never_hub_noise_nor_a_tasks(self):
        self.assertEqual(self.r["quiet"], [False, False])
        self.assertEqual(self.r["task"], "")
        self.assertEqual(self.r["groups"], [[1, 3, 4]])

    def test_a_solo_po_chat_shows_both_among_its_turns(self):
        self.assertEqual(self.r["solo"], ["S:0", "pm-1", "S:1", "pm-2", "S:2", "S:3"])
        # The line the hub typed is a quiet row; the balloon holds the words.
        self.assertEqual(self.r["typed"], [True, True, "From another PO"])
        self.assertEqual(self.r["answer"], "on a PO message")


if __name__ == "__main__":
    unittest.main()
