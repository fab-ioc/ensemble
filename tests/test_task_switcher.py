"""The task list on the left of every desktop page (layout A, step 1; index.html).

index.html's "Task switcher" block runs here in Node, with the page's own
cost chip, task number, age and workflow code around it:

* **Needs you** is what the bell lists (a PO only when it cannot go on), less
  a finished report, oldest first, each with its lozenge and its project;
* **Running** is every live task not waiting for a check, by project and
  number, so it holds still while agents take turns;
* **Ready for your check** is a task In review, reported finished (by
  ``askKind``, or by the reason on a hub from before it) or paused part way,
  not yet merged (Done), oldest first, with its change count;
* **Projects** is each registered project's PO, latest first, with its unread
  dot and "N answers to check · M asks open";
* **Done today** is folded, and holds only what was done since midnight;
* drafts, archived tasks, a PO's room and other sessions are in no task group;
* "All projects" can be narrowed to one project, and every group follows;
* the open task, or the PO on screen, is the selected row;
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
    sessions: ['po-1', 'r1', 'r2', 'r3', 'r4', 'r11', 'r12', 'r13', 'r14', 'r15'].map(roomId => ({ roomId })) },
  { id: 'p2', key: 'MO', name: 'Motors', registered: true, poRoomId: 'po-2',
    sessions: ['po-2', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10'].map(roomId => ({ roomId })) },
  { id: 'p3', key: 'PL', name: 'Plain', registered: true, sessions: [] },                  // no PO
  { id: 'p4', key: 'GH', name: 'Unregistered', registered: false, poRoomId: 'po-4', sessions: [] },
] };
%s
const T0 = 1790500000;
const row = (rid, no, label, more) => ({ sessionId: rid, roomId: rid, headless: true, no, label, workflow: 'inprogress',
                                         isLive: true, status: 'idle', updatedAt: T0, hasConversation: true, ...more });
const rows = [
  row('po-1', 0, 'PO', { status: 'busy', newsAt: T0 + 500, points: { open: 3, answered: 8, delivered: 8, planned: 0 } }),
  row('po-2', 0, 'PO', { isLive: false, newsAt: T0 - 2000 }),
  row('r1', 12, 'Blocked on a login', {}),
  row('r2', 7, 'Busy one', { status: 'busy', changes: { add: 40, del: 2, files: 3 }, newsAt: T0 + 50 }),
  row('r3', 3, 'Waiting in review', { isLive: false, workflow: 'inreview', updatedAt: T0 + 300 }),
  row('r4', 9, 'Merged today', { isLive: false, workflow: 'done', updatedAt: T0 + 20 }),
  row('r5', 2, 'Motors busy', { status: 'busy' }),
  row('r6', 4, 'Reported finished', { changes: { add: 1200, del: 40 } }),
  row('r7', 5, 'A draft', { draft: true, workflow: 'backlog', isLive: false, status: 'busy' }),
  row('r8', 6, 'Archived busy', { archived: true, status: 'busy' }),
  row('r9', 1, 'Asked a question', {}),
  row('r11', 11, 'Stopped part way', { isLive: false, updatedAt: T0 + 50 }),
  row('r12', 10, 'Merged yesterday', { isLive: false, workflow: 'done', updatedAt: T0 - 90000 }),
  row('r13', 13, 'Finished, old hub', {}),
  row('r14', 15, 'Idle between turns', {}),
  row('r15', 16, 'Parked, never run', { isLive: false, workflow: 'todo', hasConversation: false }),
  { sessionId: 'ext-1', label: 'A terminal session', isLive: true, status: 'busy', headless: false },
];
const items = [
  { roomId: 'r9', state: 'waiting_for_you', askKind: 'question', askedAt: T0 + 200, since: T0 + 200, reason: 'claude asked: “Which?”', project: 'Motors', projectId: 'p2' },
  { roomId: 'r1', state: 'blocked', askKind: 'blocked', askedAt: T0 + 100, since: T0 + 100, reason: 'logged out', project: 'Ensemble Dashboard', projectId: 'p1' },
  { roomId: 'r6', state: 'waiting_for_you', askKind: 'completed', askedAt: T0 + 30, since: T0 + 30, reason: 'finished', project: 'Motors', projectId: 'p2' },
  { roomId: 'r13', state: 'waiting_for_you', askedAt: T0 + 60, since: T0 + 60, reason: 'claude reported the work is finished: “Done.”', project: 'Ensemble Dashboard', projectId: 'p1' },
  { roomId: 'po-2', isPo: true, title: 'PO', ref: '', state: 'agent_gone', since: T0 + 400, reason: 'died', project: 'Motors', projectId: 'p2' },
  { roomId: 'r-old', state: 'stalled', since: T0 + 10, reason: 'quiet', title: 'Past the newest 300', ref: 'MO-40', project: 'Motors', projectId: 'p2' },
  { roomId: 'r7', state: 'blocked', since: T0, reason: 'a draft', project: 'Ensemble Dashboard', projectId: 'p1' },
];
store.set('cd-unread-since', String(T0 - 1000));   // dots showed before any of this
const opts = { dayStart: T0 - 100 };
const keys = g => Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.key)]));
const P = PROJECTS.projects;
const log = {};
const g = swGroups(rows, items, P, opts);
log.groups = keys(g);
log.projects = Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.project)]));
log.html = swListHtml(g, { sid: 'r2' }, false);
log.poSel = swListHtml(g, { po: 'p2' }, true);
log.empty = swListHtml(swGroups([], [], [], opts), {}, false);
log.one = keys(swGroups(rows, items, P, { ...opts, project: 'p2' }));
log.filter = swFilterHtml(P, 'p2');
log.filterAll = swFilterHtml(P, '');
// Frozen under the pointer: r2 stopped working and is now In review, r1 was answered.
const later = rows.map(r => r.roomId === 'r2' ? { ...r, status: 'idle', workflow: 'inreview' } : r);
const g2 = swGroups(later, items.filter(i => i.roomId !== 'r1'), P, opts);
log.thawed = keys(g2);
log.frozen = keys(swFreeze(g2, log.groups));
log.frozenRun = swFreeze(g2, log.groups).running.map(e => runChip(e.row));
log.noFreeze = keys(swFreeze(g2, null));
// A newcomer while frozen goes at the end of its group.
const g3 = swGroups([...rows, row('r10', 20, 'New busy', { status: 'busy' })], items, P, opts);
log.newcomer = keys(swFreeze(g3, log.groups)).running;
// A finished report that also hit a wall is blocked: it needs you.
log.wall = keys(swGroups(rows, [{ ...items[2], state: 'blocked' }], P, opts)).needs;
// A row whose own attention says finished, with no bell item for it.
log.fromRow = keys(swGroups([row('r6', 4, 'Reported', { attention: { state: 'waiting_for_you', askKind: 'completed', since: T0 } })], [], P, opts)).review;
console.log(JSON.stringify(log));
"""


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


def block(name: str) -> str:
    i = INDEX.index(f"// ---- {name}: begin")
    return INDEX[i:INDEX.index(f"// ---- {name}: end", i)]


def row_of(html: str, attr: str) -> str:
    return re.search(r'<button type="button" class="sw-row[^"]*" ' + attr + r'.*?</button>', html, re.S).group(0)


@unittest.skipUnless(NODE, "node is not installed")
class TaskSwitcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            re.search(r"^const esc = .*$", INDEX, re.M).group(0),
            INDEX[INDEX.index("const fmtAgo = "):INDEX.index("function sinceClock(")],
            INDEX[INDEX.index("const fmtCost = "):INDEX.index("const fmtInt = ")],
            INDEX[INDEX.index("const WORKFLOW_COLS = "):INDEX.index("const DONE_AGE_DAYS")],
            fn(INDEX, "function workflowOf("), fn(INDEX, "function rowTitle("),
            fn(INDEX, "function runChip("), block("Cost chip"), block("Task numbers"), block("Task switcher")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "switcher.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_five_groups_and_their_order(self):
        self.assertEqual(self.r["groups"], {
            # oldest first; the PO by its item, a task past the rows by its item;
            # not the finished reports (r6, r13), not a draft (r7)
            "needs": ["r-old", "r1", "r9", "po-2"],
            # live, by project then number, busy or between turns; no PO,
            # archive, draft or terminal
            "running": ["r2", "r14", "r5"],
            # oldest first: reported (by askKind, or by the reason), paused, in
            # review; not Done, not a todo that never ran (r15)
            "review": ["r6", "r11", "r13", "r3"],
            # latest first; not a project without a PO, nor an unregistered one
            "projects": ["po-1", "po-2"],
            "done": ["r4"],                          # not yesterday's r12
        })
        self.assertEqual(self.r["projects"]["running"], ["Ensemble Dashboard", "Ensemble Dashboard", "Motors"])
        self.assertEqual(self.r["projects"]["needs"], ["Motors", "Ensemble Dashboard", "Motors", "Motors"])

    def test_a_task_row_says_what_a_card_says(self):
        h = self.r["html"]
        busy = row_of(h, 'data-sid="r2"')
        self.assertIn('class="sw-row on"', busy)                        # the task open in the panel
        self.assertIn('aria-current="true"', busy)
        self.assertIn('class="unread-dot"', busy)                        # #110's dot
        self.assertIn('<span class="tno">ED-7</span>Busy one', busy)     # the number with its project's key
        self.assertIn('<span class="dot working"></span>working', busy)
        self.assertIn('>+40 −2</span>', busy)                            # #110's change count
        self.assertIn('<span class="sw-proj">Ensemble Dashboard</span>', busy)
        self.assertIn(f'data-ago="{1790500000 + 50}"', busy)             # its last news, as an age
        blocked = row_of(h, 'data-sid="r1"')
        self.assertIn('<span class="loz danger" title="logged out">blocked</span>', blocked)
        self.assertIn('<span class="sw-proj">Ensemble Dashboard</span>', blocked)   # its project, second line
        self.assertNotIn('class="sw-row on"', blocked)
        old = row_of(h, 'data-sid="r-old"')
        self.assertIn('<span class="tno">MO-40</span>Past the newest 300', old)
        self.assertIn('<span class="sw-proj">Motors</span>', old)
        review = row_of(h, 'data-sid="r6"')
        self.assertNotIn('class="loz', review)                           # the group says it
        self.assertIn('<span class="sw-sub">reported</span>', review)
        self.assertIn('>+1200 −40</span>', review)
        self.assertIn('<span class="sw-sub">paused</span>', row_of(h, 'data-sid="r11"'))
        self.assertIn('<span class="sw-sub">in review</span>', row_of(h, 'data-sid="r3"'))
        self.assertIn('<span class="dot idle"></span>idle', row_of(h, 'data-sid="r14"'))

    def test_a_po_row_names_its_project_and_your_asks(self):
        h = self.r["html"]
        po1 = row_of(h, 'data-po="p1"')
        self.assertIn(">Ensemble Dashboard · PO</span>", po1)
        self.assertIn('<span class="sw-sub">8 answers to check · 3 asks open</span>', po1)
        self.assertIn('class="unread-dot"', po1)
        self.assertNotIn('class="sw-row on"', po1)
        po2 = re.findall(r'<button type="button" class="sw-row[^"]*" data-po="p2".*?</button>', h, re.S)
        self.assertEqual(len(po2), 2)                                     # in Needs you and in Projects
        self.assertIn("agent gone", po2[0])
        self.assertIn(">Motors · PO</span>", po2[0])
        self.assertIn('<span class="sw-sub">not running</span>', po2[1])
        self.assertNotIn('unread-dot', po2[1])                            # its news predates the dots
        # The PO on screen is selected in Projects only.
        sel = self.r["poSel"]
        self.assertEqual(sel.count('class="sw-row on"'), 1)
        self.assertIn('class="sw-row on" data-po="p2"', sel[sel.index('data-group="projects"'):])

    def test_done_today_is_folded(self):
        h = self.r["html"]
        self.assertRegex(h, r'<details class="sw-group" data-group="done">\s*<summary class="sw-ghead"><span>Done today</span><span class="sw-n">1</span></summary>')
        self.assertIn('<details class="sw-group" data-group="done" open>', self.r["poSel"])
        self.assertIn('data-sid="r4"', h)

    def test_an_empty_group_says_so(self):
        e = self.r["empty"]
        for words in ("Nothing is waiting on you.", "No task is running.", "Nothing is waiting for your check.",
                      "No project has a PO.", "Nothing was done today."):
            self.assertIn(f'<p class="sw-none">{words}</p>', e)
        self.assertEqual(e.count('<span class="sw-n">0</span>'), 5)

    def test_the_filter_keeps_one_project(self):
        self.assertEqual(self.r["one"], {"needs": ["r-old", "r9", "po-2"], "running": ["r5"], "review": ["r6"],
                                         "projects": ["po-2"], "done": []})
        f = self.r["filter"]
        self.assertTrue(f.startswith('<option value="">All projects</option>'))
        self.assertIn('<option value="p2" selected>Motors</option>', f)
        self.assertEqual(re.findall(r'<option value="(\w*)"', f), ["", "p1", "p2", "p3"])   # by name, registered only
        self.assertIn('<option value="" selected>All projects</option>', self.r["filterAll"])

    def test_nothing_moves_under_the_pointer(self):
        self.assertEqual(self.r["thawed"]["needs"], ["r-old", "r9", "po-2"])
        self.assertIn("r2", self.r["thawed"]["review"])
        self.assertEqual(self.r["thawed"]["running"], ["r1", "r14", "r5"])   # answered, it runs on
        # Frozen: r2 keeps its place in Running, its content fresh, and r1 its
        # place in Needs you.
        self.assertEqual(self.r["frozen"], {"needs": ["r-old", "r1", "r9", "po-2"], "running": ["r2", "r14", "r5"],
                                            "review": ["r6", "r11", "r13", "r3"], "projects": ["po-1", "po-2"],
                                            "done": ["r4"]})
        self.assertIn("idle", self.r["frozenRun"][0])
        self.assertEqual(self.r["noFreeze"], self.r["thawed"])
        self.assertEqual(self.r["newcomer"], ["r2", "r14", "r5", "r10"])

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
        # A task opens in its panel over the page; a PO on its project's screen.
        click = wiring[wiring.index("$('#sw-list').addEventListener('click'"):]
        click = click[:click.index("\n});")]
        self.assertIn("openDetail(row.dataset.sid)", click)
        self.assertIn("openProjectPage(pj.id)", click)
        # The filter is remembered per browser.
        self.assertIn("localStorage.setItem(SW_KEY + '-project'", wiring)
        # Always there on a desktop, never on a phone; the page makes room.
        self.assertIn("function swOn() { try { return !isPhone(); }", wiring)
        self.assertIn("body.sw-on main { padding-left: calc(var(--sw-w) + 20px); }", INDEX)
        self.assertIn("body { --sw-w: 300px; }", INDEX)
        # #114's button, pin and close are gone: the list is not a panel you open.
        for gone in ('id="sw-btn"', 'id="sw-pin"', 'id="sw-close"', "sw-pinned"):
            self.assertNotIn(gone, INDEX)


if __name__ == "__main__":
    unittest.main()
