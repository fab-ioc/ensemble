"""The change count and the unread dot on a task's card and row (index.html).

index.html's "Cost chip" block, the card and the task row run here in Node
with a stand-in localStorage:

* ``+N −N`` follows the cost, in the same quiet words; nothing without a
  count, at +0 −0, or with nonsense in it;
* the dot shows when the hub's newsAt is later than the read point the chat's
  catch-up line keeps for the room (the later of its ts and at), and never for
  anything from before this browser first showed dots;
* the project menu marks a project by its PO's chat.

The hub's side is tests/test_card_signals.py. Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")

JS = r"""
const store = new Map();
const localStorage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) };
%s
const log = {};
log.chips = [
  { changes: { add: 512, del: 4, files: 7 } },
  { changes: { add: 3, del: 0, files: 1 } },
  { changes: { add: 0, del: 0, files: 0 } },
  { changes: null },
  {},
  { changes: { add: -2, del: 'x' } },
].map(r => changesChipHtml(r, 'ccost cchg'));
log.rowChip = changesChipHtml({ changes: { add: 1, del: 2, files: 1 } });
log.huge = [[9999, 10000], [12345, 123456]].map(([add, del]) => changesChipHtml({ changes: { add, del } }));

// Unread: newsAt against the room's read point and the first time dots showed.
const now = Date.now() / 1000;
log.firstSince = unreadSince() > now - 5;
store.set('cd-unread-since', String(1000));
UNREAD_SINCE = 0;
const row = (rid, newsAt) => ({ roomId: rid, newsAt });
log.never = [isUnread(row('room-a', 900)), isUnread(row('room-a', 1100)), isUnread(row('room-a', 0)), isUnread(row('', 5000)), isUnread(null)];
store.set('cd-chat-read:room-b', JSON.stringify({ id: 'm9', ts: 1500 }));
log.byTs = [isUnread(row('room-b', 1500)), isUnread(row('room-b', 1501))];
store.set('cd-chat-read:room-c', JSON.stringify({ id: 'm9', ts: 1500, at: 1800 }));
log.byAt = [isUnread(row('room-c', 1700)), isUnread(row('room-c', 1801))];
store.set('cd-chat-read:room-d', '{broken');
log.broken = isUnread(row('room-d', 1200));
log.dot = unreadDotHtml(row('room-a', 1100));
log.noDot = unreadDotHtml(row('room-b', 1500));

// A board card and a task row, the rest of them stubbed.
const ATTENTION_BY_ROOM = new Map(), ATTN_LABEL = {}, SELECTED_PROJECT = '', DETAIL_MODE = true;
const prioIcon = () => '<i class="prio-ico"></i>', prioOf = () => 3, runChip = () => '<span class="run">Idle</span>';
const cardAgents = () => '', fmtAgo = () => '1m', taskNoHtml = () => '<span class="tno">#7</span>';
const prioCell = () => '<td></td>', agentBadges = () => '';
log.card = cardHtml({ roomId: 'room-a', sessionId: 's1', label: 'Task', updatedAt: 0, cost: 2.5, costTokens: null,
                      newsAt: 1100, changes: { add: 512, del: 4, files: 7 } }, 60);
log.cardQuiet = cardHtml({ roomId: 'room-b', sessionId: 's2', label: 'Task', updatedAt: 0, cost: 0, costTokens: null,
                           newsAt: 1500, changes: { add: 0, del: 0, files: 0 } }, 60);
log.row = renderHeadlessRow({ roomId: 'room-a', sessionId: 's1', label: 'Task', headless: true, newsAt: 1100,
                              changes: { add: 9, del: 1, files: 2 } }, {}, 60);
log.rowQuiet = renderHeadlessRow({ roomId: 'room-b', sessionId: 's2', label: 'Task', headless: true, newsAt: 1, changes: null }, {}, 60);
console.log(JSON.stringify(log));
"""


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


@unittest.skipUnless(NODE, "node is not installed")
class CardSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Cost chip: begin")
        src = "\n".join([re.search(r"^const esc = .*$", INDEX, re.M).group(0),
                         INDEX[INDEX.index("const fmtCost = "):INDEX.index("const fmtInt = ")],
                         INDEX[i:INDEX.index("// ---- Cost chip: end", i)], fn(INDEX, "function rowTitle("),
                         fn(INDEX, "function cardHtml("), fn(INDEX, "function renderHeadlessRow(")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "signals.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_change_count(self):
        big, small, zero, none, missing, junk = self.r["chips"]
        self.assertIn('class="ccost cchg"', big)
        self.assertIn(">+512 −4</span>", big)
        self.assertIn("512 lines added, 4 removed, in 7 files", big)
        self.assertIn(">+3 −0</span>", small)
        self.assertIn("in 1 file.", small)
        self.assertEqual([zero, none, missing, junk], ["", "", "", ""])
        self.assertIn('class="row-cost-chip"', self.r["rowChip"])

    def test_five_digits_read_as_thousands(self):
        # A big branch still fits a card; the tooltip keeps the exact count.
        four, five = self.r["huge"]
        self.assertIn(">+9999 −10k</span>", four)
        self.assertIn(">+12.3k −123k</span>", five)
        self.assertIn("12,345 lines added, 123,456 removed", five)

    def test_unread_against_the_read_point(self):
        r = self.r
        self.assertTrue(r["firstSince"])
        # Never read: only what came after dots first showed.
        self.assertEqual(r["never"], [False, True, False, False, False])
        self.assertEqual(r["byTs"], [False, True])
        self.assertEqual(r["byAt"], [False, True])
        self.assertTrue(r["broken"])
        self.assertIn('class="unread-dot"', r["dot"])
        self.assertIn('aria-label="New messages"', r["dot"])
        self.assertEqual(r["noDot"], "")

    def test_on_the_card_and_the_row(self):
        r = self.r
        self.assertRegex(r["card"], r'<div class="ctitle"><span class="unread-dot"[^>]*></span><span class="tno">#7</span>Task</div>')
        self.assertRegex(r["card"], r'\$2\.50</span> <span class="ccost cchg"[^>]*>\+512 −4</span>')
        self.assertNotIn("unread-dot", r["cardQuiet"])
        self.assertNotIn("cchg", r["cardQuiet"])
        self.assertRegex(r["row"], r'<td class="topic"[^>]*><span class="unread-dot"[^>]*></span><span class="tno">')
        self.assertIn('<span class="row-cost-chip"', r["row"])
        self.assertIn(">+9 −1</span></td>", r["row"])
        self.assertNotIn("unread-dot", r["rowQuiet"])
        self.assertNotIn("row-cost-chip", r["rowQuiet"])

    def test_the_project_menu_marks_a_project_by_its_po_chat(self):
        self.assertRegex(INDEX, r"<span class=\"pm-name\">\$\{esc\(pj\.name\)\}</span>\$\{unreadDotHtml\(poRowOf\(pj\)\)\}")

    def test_a_chat_read_elsewhere_repaints(self):
        i = INDEX.index("window.addEventListener('storage', e => {\n  if (e.key !== null && !String(e.key).startsWith('cd-chat-read:'))")
        self.assertIn("setTimeout(renderRows, 150)", INDEX[i:i + 300])


if __name__ == "__main__":
    unittest.main()
