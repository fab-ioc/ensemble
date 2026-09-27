"""The task switcher: every project's live tasks in three groups (index.html).

index.html's "Task switcher" block runs here in Node, with the page's own
cost chip, task number and workflow code around it:

* **Needs you** is what the bell lists (a PO only when it cannot go on), less
  a finished report, oldest first, each with its lozenge and "since HH:MM";
* **Running** is every task working now, by project and number;
* **Ready to review** is a task In review or with a finished report, not yet
  merged (Done), oldest first;
* drafts, archived tasks, a PO's room and other sessions are in none;
* a row carries its number, title, project, run state, change count and
  unread dot; an empty group says so in one line;
* while the pointer is over the list nothing changes group or place.

The hub's side (``askKind`` on the bell's item and the row's attention) is in
tests/test_open_ask.py. Skipped without Node.
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
const ATTN_LABEL = { agent_gone: 'agent gone', blocked: 'blocked', waiting_for_you: 'waiting for you', stalled: 'stalled' };
let PROJECTS = { projects: [
  { id: 'p1', key: 'ED', name: 'Ensemble Dashboard', registered: true, poRoomId: 'po-1',
    sessions: [{ roomId: 'po-1' }, { roomId: 'r1' }, { roomId: 'r2' }, { roomId: 'r3' }, { roomId: 'r4' }] },
  { id: 'p2', key: 'MO', name: 'Motors', registered: true, poRoomId: 'po-2',
    sessions: [{ roomId: 'po-2' }, { roomId: 'r5' }, { roomId: 'r6' }, { roomId: 'r7' }, { roomId: 'r8' }, { roomId: 'r9' }] },
] };
%s
const projOf = rid => PROJECTS.projects.find(p => p.sessions.some(s => s.roomId === rid)) || null;
const poRooms = new Set(['po-1', 'po-2']);
const T0 = 1790500000;
const row = (rid, no, label, more) => ({ sessionId: rid, roomId: rid, headless: true, no, label, workflow: 'inprogress',
                                         isLive: true, status: 'idle', updatedAt: T0, ...more });
const rows = [
  row('po-1', 0, 'PO', { status: 'busy' }),                                   // a PO: never a task
  row('r1', 12, 'Blocked on a login', {}),
  row('r2', 7, 'Busy one', { status: 'busy', changes: { add: 40, del: 2, files: 3 }, newsAt: T0 + 50 }),
  row('r3', 3, 'Waiting in review', { isLive: false, workflow: 'inreview', updatedAt: T0 + 300 }),
  row('r4', 9, 'Merged already', { isLive: false, workflow: 'done' }),
  row('r5', 2, 'Motors busy', { status: 'busy' }),
  row('r6', 4, 'Reported finished', {}),
  row('r7', 5, 'A draft', { draft: true, workflow: 'backlog', isLive: false, status: 'busy' }),
  row('r8', 6, 'Archived busy', { archived: true, status: 'busy' }),
  row('r9', 1, 'Asked a question', {}),
  { sessionId: 'ext-1', label: 'A terminal session', isLive: true, status: 'busy', headless: false },
];
const items = [
  { roomId: 'r9', state: 'waiting_for_you', askKind: 'question', askedAt: T0 + 200, since: T0 + 200, reason: 'claude asked: “Which?”', project: 'Motors', projectId: 'p2' },
  { roomId: 'r1', state: 'blocked', askKind: 'blocked', askedAt: T0 + 100, since: T0 + 100, reason: 'logged out', project: 'Ensemble Dashboard', projectId: 'p1' },
  { roomId: 'r6', state: 'waiting_for_you', askKind: 'completed', askedAt: T0 + 30, since: T0 + 30, reason: 'finished', project: 'Motors', projectId: 'p2' },
  { roomId: 'po-2', isPo: true, title: 'PO', ref: '', state: 'agent_gone', since: T0 + 400, reason: 'died', project: 'Motors', projectId: 'p2' },
  { roomId: 'r-old', state: 'stalled', since: T0 + 10, reason: 'quiet', title: 'Past the newest 300', ref: 'MO-40', project: 'Motors', projectId: 'p2' },
  { roomId: 'r7', state: 'blocked', since: T0, reason: 'a draft', project: 'Ensemble Dashboard', projectId: 'p1' },
];
store.set('cd-unread-since', String(T0 - 1000));   // dots showed before any of this
const keys = g => Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.key)]));
const log = {};
const g = swGroups(rows, items, poRooms, projOf);
log.groups = keys(g);
log.projects = Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.project)]));
log.html = swListHtml(g, 'r2');
log.empty = swListHtml(swGroups([], [], poRooms, projOf), '');
// Frozen under the pointer: r2 stopped working and r5 is now In review, r1 was answered.
const later = rows.map(r => r.roomId === 'r2' ? { ...r, status: 'idle', workflow: 'inreview' } : r);
const g2 = swGroups(later, items.filter(i => i.roomId !== 'r1'), poRooms, projOf);
log.thawed = keys(g2);
log.frozen = keys(swFreeze(g2, log.groups));
log.frozenRun = swFreeze(g2, log.groups).running.map(e => runChip(e.row));
log.noFreeze = keys(swFreeze(g2, null));
// A newcomer while frozen goes at the end of its group.
const g3 = swGroups([...rows, row('r10', 20, 'New busy', { status: 'busy' })], items, poRooms, projOf);
log.newcomer = keys(swFreeze(g3, log.groups)).running;
// A finished report that also hit a wall is blocked: it needs you.
log.wall = keys(swGroups(rows, [{ ...items[2], state: 'blocked' }], poRooms, projOf)).needs;
// A row whose own attention says finished, with no bell item for it.
log.fromRow = keys(swGroups([row('r6', 4, 'Reported', { attention: { state: 'waiting_for_you', askKind: 'completed', since: T0 } })], [], poRooms, projOf)).review;
console.log(JSON.stringify(log));
"""


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


def block(name: str) -> str:
    i = INDEX.index(f"// ---- {name}: begin")
    return INDEX[i:INDEX.index(f"// ---- {name}: end", i)]


@unittest.skipUnless(NODE, "node is not installed")
class TaskSwitcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            re.search(r"^const esc = .*$", INDEX, re.M).group(0),
            INDEX[INDEX.index("const fmtCost = "):INDEX.index("const fmtInt = ")],
            INDEX[INDEX.index("const WORKFLOW_COLS = "):INDEX.index("const DONE_AGE_DAYS")],
            fn(INDEX, "function workflowOf("), fn(INDEX, "function rowTitle("), fn(INDEX, "function sinceClock("),
            fn(INDEX, "function runChip("), block("Cost chip"), block("Task numbers"), block("Task switcher")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "switcher.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_three_groups_and_their_order(self):
        # Needs you: oldest first, the PO by its item, a task past the rows by
        # its item; not the finished report (r6), not a draft (r7).
        self.assertEqual(self.r["groups"], {
            "needs": ["r-old", "r1", "r9", "po-2"],
            "running": ["r2", "r5"],                 # by project, then number; no PO, archive, draft or terminal
            "review": ["r6", "r3"],                  # oldest first; not Done (r4)
        })
        self.assertEqual(self.r["projects"]["running"], ["Ensemble Dashboard", "Motors"])
        self.assertEqual(self.r["projects"]["needs"][-1], "Motors")

    def test_a_row_says_what_a_card_says(self):
        h = self.r["html"]
        busy = re.search(r'<button type="button" class="sw-row on" data-sid="r2".*?</button>', h, re.S).group(0)
        self.assertIn('aria-current="true"', busy)                      # the task open in the panel
        self.assertIn('class="unread-dot"', busy)                        # #110's dot
        self.assertIn('<span class="tno">ED-7</span>Busy one', busy)     # the number with its project's key
        self.assertIn('<span class="dot working"></span>working', busy)
        self.assertIn('>+40 −2</span>', busy)                            # #110's change count
        self.assertIn('<span class="sw-proj">Ensemble Dashboard</span>', busy)
        self.assertNotIn("sw-since", busy)                               # running shows no "since"
        blocked = re.search(r'data-sid="r1".*?</button>', h, re.S).group(0)
        self.assertIn('<span class="loz danger" title="logged out">blocked</span>', blocked)
        self.assertRegex(blocked, r'<span class="sw-since">since (\w+ \d+, )?\d?\d:\d\d( [AP]M)?</span>')
        po = re.search(r'data-po="p2".*?</button>', h, re.S).group(0)
        self.assertIn(">PO</span>", po.replace('">PO', '>PO'))
        self.assertIn("agent gone", po)
        old = re.search(r'data-sid="r-old".*?</button>', h, re.S).group(0)
        self.assertIn('<span class="tno">MO-40</span>Past the newest 300', old)
        review = re.search(r'data-sid="r6".*?</button>', h, re.S).group(0)
        self.assertNotIn('class="loz', review)                           # the group says it
        self.assertIn("sw-since", review)

    def test_an_empty_group_says_so(self):
        e = self.r["empty"]
        for words in ("Nothing is waiting on you.", "No task is working right now.",
                      "Nothing is waiting for your review."):
            self.assertIn(f'<p class="sw-none">{words}</p>', e)
        self.assertEqual(e.count('<span class="sw-n">0</span>'), 3)

    def test_nothing_moves_under_the_pointer(self):
        self.assertEqual(self.r["thawed"]["needs"], ["r-old", "r9", "po-2"])
        self.assertIn("r2", self.r["thawed"]["review"])
        # Frozen: r2 keeps its place in Running, its content fresh; r1 left every group.
        self.assertEqual(self.r["frozen"], {"needs": ["r-old", "r9", "po-2"], "running": ["r2", "r5"],
                                            "review": ["r6", "r3"]})
        self.assertIn("idle", self.r["frozenRun"][0])
        self.assertEqual(self.r["noFreeze"], self.r["thawed"])
        self.assertEqual(self.r["newcomer"], ["r2", "r5", "r10"])

    def test_a_finished_report_behind_a_wall_needs_you(self):
        self.assertEqual(self.r["wall"], ["r6"])
        self.assertEqual(self.r["fromRow"], ["r6"])

    def test_the_page_wires_it_to_what_it_already_polls(self):
        # No poller of its own: the rows' poll and the bell's repaint it.
        i = INDEX.index("// The task switcher's column")
        wiring = INDEX[i:INDEX.index("\n// Poll while the tab is in front.", i)]
        self.assertNotIn("setInterval", wiring)
        self.assertNotIn("api(", wiring)
        self.assertIn("function renderRows() {\n  syncProjSwitch();\n  swRender();", INDEX)
        self.assertIn("  else swRender();\n}", INDEX)
        # A task opens over the page: no project switch on the way.
        click = wiring[wiring.index("$('#switcher').addEventListener('click'"):]
        click = click[:click.index("\n});")]
        self.assertIn("openDetail(row.dataset.sid)", click)
        self.assertNotIn("SELECTED_PROJECT", click)
        # The pin is remembered per browser.
        self.assertIn("const SW_KEY = 'cd-switcher';", INDEX)


if __name__ == "__main__":
    unittest.main()
