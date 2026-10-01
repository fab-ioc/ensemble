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
* **Unassigned** (after Projects, folded) is every session in no project, as
  /api/projects groups them: your own terminal sessions and tasks started
  without one, newest first, the newest ten until "Show all (N)"; each opens
  in the middle like any row, where its actions hold Make PO of a new
  project…; it is left out while one project is chosen;
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
# The word for a board (static/noun.js): the page code below calls noun()/nounText().
NOUN = (Path(__file__).resolve().parent.parent / "static" / "noun.js").read_text(encoding="utf-8")
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
  // In no project: twelve of your own sessions (u1 live, u3 named), a task
  // started without a project that never ran, and an archived one.
  ...Array.from({ length: 12 }, (_, i) => ({ sessionId: 'u' + (i + 1), agent: 'claude', headless: false,
    isLive: i === 0, status: i === 0 ? 'busy' : 'idle', updatedAt: T0 - 100 * (i + 1),
    firstWords: 'Question number ' + (i + 1), label: i === 2 ? 'Named one' : '', cwd: 'D:\\code\\work' + (i + 1),
    makePo: { ok: true, code: '' } })),
  row('rx', 3, 'No project task', { isLive: false, workflow: 'todo', hasConversation: false, updatedAt: T0 - 50, cwd: '' }),
  { sessionId: 'ua', archived: true, headless: false, updatedAt: T0 },
  row('rd', 21, 'A draft in no project', { draft: true, workflow: 'backlog', isLive: false, updatedAt: T0 }),
];
// The Unassigned group as /api/projects has it (build_projects' `keep`
// fields: no archived, no agent). u-grp is past the rows the page holds (the
// group reaches back 500, the rows 300): it would open as "Session not found".
const kept = r => ({ sessionId: r.sessionId, roomId: r.roomId || null, label: r.label || '', status: r.status,
                     isLive: !!r.isLive, updatedAt: r.updatedAt, headless: r.headless, cwd: r.cwd, firstWords: r.firstWords || null });
const unassigned = [...rows.filter(r => /^u\d|^rx$|^ua$|^rd$/.test(r.sessionId)).map(kept),
                    { sessionId: 'u-grp', roomId: null, label: '', firstWords: 'Only in the group', updatedAt: T0 - 5000, isLive: false }];
const items = [
  { roomId: 'r9', state: 'waiting_for_you', askKind: 'question', askedAt: T0 + 200, since: T0 + 200, reason: 'claude asked: “Which?”', project: 'Motors', projectId: 'p2' },
  { roomId: 'r1', state: 'blocked', askKind: 'blocked', askedAt: T0 + 100, since: T0 + 100, reason: 'logged out', project: 'Ensemble Dashboard', projectId: 'p1', agentIdentity: 'codex', lastLines: 'Please log in' },
  { roomId: 'r6', state: 'waiting_for_you', askKind: 'completed', askedAt: T0 + 30, since: T0 + 30, reason: 'finished', project: 'Motors', projectId: 'p2' },
  { roomId: 'r13', state: 'waiting_for_you', askedAt: T0 + 60, since: T0 + 60, reason: 'claude reported the work is finished: “Done.”', project: 'Ensemble Dashboard', projectId: 'p1' },
  { roomId: 'po-2', isPo: true, title: 'PO', ref: '', state: 'agent_gone', since: T0 + 400, reason: 'died', project: 'Motors', projectId: 'p2', agentIdentity: 'claude' },
  { roomId: 'r-old', state: 'stalled', since: T0 + 10, reason: 'quiet', title: 'Past the newest 300', ref: 'MO-40', project: 'Motors', projectId: 'p2' },
  { roomId: 'r7', state: 'blocked', since: T0, reason: 'a draft', project: 'Ensemble Dashboard', projectId: 'p1' },
];
store.set('cd-unread-since', String(T0 - 1000));   // dots showed before any of this
const opts = { dayStart: T0 - 100, unassigned };
const keys = g => Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.key)]));
const P = PROJECTS.projects;
const log = {};
const g = swGroups(rows, items, P, opts);
log.groups = keys(g);
log.projects = Object.fromEntries(Object.entries(g).map(([k, l]) => [k, l.map(e => e.project)]));
log.html = swListHtml(g, { sid: 'r2' }, false);
SW_DIAG.add('r1'); log.diagOpen = swListHtml(g, { sid: 'r2' }, false); SW_DIAG.clear();
log.longWhy = swRowHtml({ key: 'r-long', row: null, it: { state: 'blocked', title: 'Long', reason: 'x'.repeat(81) }, pid: 'p2', project: 'Motors' }, 'needs', false);
log.unOpen = swListHtml(g, { sid: 'u3' }, false, { open: true });
log.unAll = swListHtml(g, {}, false, { open: true, all: true });
log.unSel = swListHtml(g, { sid: 'u12' }, false, { open: true });
log.unFew = swListHtml(swGroups(rows, items, P, { ...opts, unassigned: unassigned.slice(0, 3) }), {}, false, { open: true });
// A row opened from Unassigned has the task panel's bar, Make PO in its menu.
log.bar = actionsCell(rows.find(r => r.sessionId === 'u2'), false);
log.poSel = swListHtml(g, { po: 'p2' }, true);
log.empty = swListHtml(swGroups([], [], [], opts), {}, false);
log.byProject = swListHtml(g, { po: 'p2' }, false, {}, 'project');
log.by = swGroupByHtml('project');
// A running team task names who is on it.
log.teamRow = swListHtml(swGroups([row('r2', 7, 'Team run', { status: 'busy', members: [{ agent: 'claude' }, { agent: 'codex' }] })], [], P, opts), {}, false);
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
// A stopped engineer + reviewer task as the hub sends it: hasConversation is
// set only for one-agent tasks, so its messages (turns) show it ran.
const team = (rid, no, turns) => row(rid, no, 'Team', { isLive: false, hasConversation: false, turns,
                                                        members: [{ agent: 'claude' }, { agent: 'codex' }] });
const gt = swGroups([team('r1', 12, 2), team('r2', 7, 0)], [], P, opts);
log.team = keys(gt);
console.log(JSON.stringify(log));
"""

# The task panel's action bar, as the page builds it (static/actions.js).
BAR = r"""
const CHAT_SCHEME_ON = {}, PLATFORM = { features: {} };
const onHubMachine = () => false, T = () => 'terminal', FM = () => 'file manager';
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
            NOUN, re.search(r"^const esc = .*$", INDEX, re.M).group(0),
            INDEX[INDEX.index("const fmtAgo = "):INDEX.index("const withoutAgo = ")],
            INDEX[INDEX.index("const fmtCost = "):INDEX.index("const fmtInt = ")],
            INDEX[INDEX.index("const WORKFLOW_COLS = "):INDEX.index("const DONE_AGE_DAYS")],
            (ROOT / "static" / "actions.js").read_text(encoding="utf-8"), BAR,
            fn(INDEX, "function actionState("), fn(INDEX, "function actionEnv("), fn(INDEX, "function actionsCell("),
            fn(INDEX, "function workflowOf("), fn(INDEX, "function rowTitle("), fn(INDEX, "function detailTitle("),
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
            # newest first; not archived, not a draft, not one past the rows
            "unassigned": ["rx", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10", "u11", "u12"],
            "done": ["r4"],                          # not yesterday's r12
        })
        self.assertEqual(self.r["projects"]["running"], ["Ensemble Dashboard", "Ensemble Dashboard", "Motors"])
        self.assertEqual(self.r["projects"]["needs"], ["Motors", "Ensemble Dashboard", "Motors", "Motors"])

    def test_a_stopped_team_task_is_paused_once_it_has_run(self):
        # review 1: hasConversation is false for every multi-agent task
        self.assertEqual(self.r["team"]["review"], ["r1"])
        self.assertNotIn("r2", sum(self.r["team"].values(), []))   # never ran

    def test_a_task_row_says_what_a_card_says(self):
        h = self.r["html"]
        busy = row_of(h, 'data-sid="r2"')
        self.assertIn('aria-current="true"', busy)
        self.assertIn('class="sw-row on unread"', busy)                  # open in the panel; news unread
        self.assertIn('aria-current="true"', busy)
        self.assertNotIn('unread-dot', busy)                             # bold title + dot say it now
        self.assertIn('<span class="tno">ED-7</span> Busy one', busy)    # the number with its project's key
        self.assertIn('<span class="sw-st working" role="img" aria-label="working"></span>', busy)
        self.assertIn('>+40 −2</span>', busy)                            # #110's change count
        self.assertIn('<span class="sw-sub">Ensemble Dashboard</span>', busy)
        self.assertIn(f'data-ago="{1790500000 + 50}"', busy)             # its last news, as an age
        blocked = row_of(h, 'data-sid="r1"')
        self.assertIn('<span class="loz danger" title="logged out">blocked</span>', blocked)
        self.assertIn('<span class="sw-st danger" role="img" aria-label="blocked"></span>', blocked)
        self.assertIn('<span class="sw-sub">Ensemble Dashboard · codex</span>', blocked)   # its project and agent, second line
        self.assertNotIn('class="sw-row on', blocked)
        old = row_of(h, 'data-sid="r-old"')
        self.assertIn('<span class="tno">MO-40</span> Past the newest 300', old)
        self.assertIn('<span class="sw-sub">Motors</span>', old)
        self.assertIn('class="sw-st neutral"', old)                      # stalled
        review = row_of(h, 'data-sid="r6"')
        self.assertNotIn('class="loz', review)                           # the group says it
        self.assertIn('<span class="sw-sub">Motors · reported</span>', review)
        self.assertIn('>+1200 −40</span>', review)
        self.assertIn('<span class="sw-sub">Ensemble Dashboard · paused</span>', row_of(h, 'data-sid="r11"'))
        self.assertIn('<span class="sw-sub">Ensemble Dashboard · in review</span>', row_of(h, 'data-sid="r3"'))
        self.assertIn('class="sw-st idle"', row_of(h, 'data-sid="r14"'))
        self.assertIn('class="sw-st success"', row_of(h, 'data-sid="r4"'))  # done today
        self.assertIn('class="sw-st off"', row_of(h, 'data-sid="r3"'))
        self.assertNotIn('class="dot ', h)                               # no run chip in a row
        team = row_of(self.r["teamRow"], 'data-sid="r2"')
        self.assertIn('<span class="sw-sub">Ensemble Dashboard · claude, codex</span>', team)

    def test_a_po_row_names_its_project_and_your_asks(self):
        h = self.r["html"]
        po1 = row_of(h, 'data-po="p1"')
        self.assertIn(">Ensemble Dashboard · PO</span>", po1)
        self.assertIn('<span class="sw-sub">8 answers to check · 3 asks open</span>', po1)
        self.assertIn('class="sw-row unread"', po1)
        self.assertIn('class="sw-st working"', po1)                       # busy beats unread
        self.assertNotIn('class="sw-row on', po1)
        po2 = re.findall(r'<button type="button" class="sw-row[^"]*" data-po="p2".*?</button>', h, re.S)
        self.assertEqual(len(po2), 2)                                     # in Needs you and in Projects
        self.assertIn("agent gone", po2[0])
        self.assertIn(">Motors · PO</span>", po2[0])
        self.assertIn('<span class="sw-sub">not running</span>', po2[1])
        self.assertNotIn('unread', po2[1])                                # its news predates the dots
        self.assertIn('class="sw-st off"', po2[1])
        # The PO on screen is selected in Projects only.
        sel = self.r["poSel"]
        self.assertEqual(sel.count('class="sw-row on"'), 1)
        self.assertIn('class="sw-row on" data-po="p2"', sel[sel.index('data-group="projects"'):])

    def test_done_today_is_folded(self):
        h = self.r["html"]
        self.assertRegex(h, r'<details class="sw-group" data-group="done">\s*<summary class="sw-ghead"><span class="sw-gname">Done today</span><span class="sw-n">1</span></summary>')
        self.assertIn('<details class="sw-group" data-group="done" open>', self.r["poSel"])
        self.assertIn('data-sid="r4"', h)

    def test_an_empty_group_is_its_heading_alone(self):
        # The list keeps its shape: every group stays, quiet, with its 0.
        e = self.r["empty"]
        for k, name in (("needs", "Needs you"), ("running", "Running"), ("review", "Ready for your check"),
                        ("projects", "Projects")):
            self.assertRegex(e, rf'<section class="sw-group empty" data-group="{k}" aria-label="{name}">\s*'
                                rf'<h3 class="sw-ghead"><span class="sw-gname">{name}</span><span class="sw-n">0</span></h3></section>')
        self.assertIn('<details class="sw-group empty" data-group="done">', e)
        self.assertIn('<details class="sw-group empty" data-group="unassigned">', e)
        self.assertEqual(e.count('<span class="sw-n">0</span>'), 6)
        self.assertNotIn("sw-none", e)
        self.assertNotIn(" empty", self.r["html"][:self.r["html"].index('data-group="done"')])

    def test_a_needs_you_row_says_since_when_who_and_why(self):
        # #135: what only the bell showed is on the row.
        r1 = row_of(self.r["html"], 'data-sid="r1"')
        self.assertIn('class="sw-row needs"', r1)
        self.assertRegex(r1, r'<span class="sw-age">since [^<]+</span>', "an ask its agent reported: since when")
        self.assertIn('<span class="sw-sub">Ensemble Dashboard · codex</span>', r1, "the agent it is waiting on")
        self.assertIn('<span class="sw-why">logged out</span>', r1, "the reason, a third line")
        self.assertIn('title="ED-12 · ', r1)
        self.assertIn('\nlogged out\n\nPlease log in"', r1, "the tooltip: the reason and the last screen")
        old = row_of(self.r["html"], 'data-sid="r-old"')
        self.assertIn('<span class="sw-age"><span data-ago=', old, "no ask time: its age")
        po = row_of(self.r["html"], 'data-po="p2"')
        self.assertNotIn("sw-why", row_of(self.r["html"], 'data-sid="r5"'), "only Needs you has a third line")
        self.assertIn(".sw-why {", INDEX)
        self.assertTrue(po)
        self.assertIn('<span class="sw-sub">Motors · claude</span>', po, "a PO's agent too")

    def test_what_the_tooltip_holds_is_reachable_without_hover(self):
        # #135 review 1: a touch screen has no tooltip.
        html, r1 = self.r["html"], 'data-room="r1"'
        d = re.search(r'<button type="button" class="sw-diag-btn" ' + r1 + r'[^>]*>.*?</button><div class="sw-diag"[^>]*>.*?</div>', html, re.S)
        self.assertTrue(d, "a Details line under the row")
        d = d.group(0)
        self.assertIn('aria-expanded="false" aria-controls="sw-diag-r1" aria-label=', d, "a last screen: always shown")
        self.assertIn('<div class="sw-diag" id="sw-diag-r1" hidden>', d, "folded by default")
        self.assertIn('<p class="sw-diag-why">logged out</p><pre class="sw-diag-lines">Please log in</pre>', d)
        # #135 R2: a reason alone waits hidden until the page measures it cut (tests/test_middle.py).
        self.assertIn('aria-controls="sw-diag-r-long" data-clip hidden aria-label=', self.r["longWhy"])
        self.assertIn('aria-controls="sw-diag-r9" data-clip hidden aria-label=', html)
        self.assertIn(".sw-diag-btn[hidden] { display: none; }", INDEX)
        self.assertIn("swDiagFit(box);", fn(INDEX, "function swRender("))
        o = self.r["diagOpen"]
        self.assertIn('aria-expanded="true" aria-controls="sw-diag-r1"', o)
        self.assertIn('<div class="sw-diag" id="sw-diag-r1">', o, "open, it stays open across redraws")

    def test_the_list_can_group_by_project(self):
        h = self.r["byProject"]
        self.assertEqual(re.findall(r'data-group="project" data-proj="(\w*)"', h), ["p1", "p2"])   # by name
        motors = h[h.index('data-proj="p2"'):h.index('data-group="unassigned"')]
        # Its PO first (selected: it is on screen), then Needs you, Running, Ready.
        self.assertEqual(re.findall(r'class="sw-row[^"]*" data-(?:po|sid)="[^"]*" data-room="([\w-]+)"', motors), ["po-2", "r-old", "r9", "r5", "r6"])
        self.assertEqual(motors.count('data-po="p2"'), 1)                 # once, with its lozenge
        self.assertIn('class="sw-row needs on" data-po="p2"', motors)
        self.assertIn('<span class="sw-gname">Motors</span><span class="sw-n">4</span>', motors)   # tasks, not the PO
        # Unassigned holds the tasks in no project (rx), then Done today, both below.
        self.assertNotIn('data-proj=""', h)
        self.assertLess(h.index('data-group="unassigned"'), h.index('data-group="done"'))
        self.assertIn('data-sid="rx"', h[h.index('data-group="unassigned"'):])
        self.assertEqual(self.r["by"], '<option value="status">Group: status</option>'
                                       '<option value="project" selected>Group: project</option>')

    def test_the_filter_keeps_one_project(self):
        # One project: no Unassigned group at all.
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
                                            "unassigned": self.r["groups"]["unassigned"], "done": ["r4"]})
        self.assertIn("idle", self.r["frozenRun"][0])
        self.assertEqual(self.r["noFreeze"], self.r["thawed"])
        self.assertEqual(self.r["newcomer"], ["r2", "r14", "r5", "r10"])

    def test_a_finished_report_behind_a_wall_needs_you(self):
        self.assertEqual(self.r["wall"], ["r6"])
        self.assertEqual(self.r["fromRow"], ["r6"])

    def test_unassigned_lists_the_sessions_in_no_project(self):
        h = self.r["html"]
        # After Projects, before Done today, folded, with its count.
        self.assertLess(h.index('data-group="projects"'), h.index('data-group="unassigned"'))
        self.assertLess(h.index('data-group="unassigned"'), h.index('data-group="done"'))
        self.assertRegex(h, r'<details class="sw-group" data-group="unassigned">\s*<summary class="sw-ghead"[^>]*>'
                            r'<span class="sw-gname">Unassigned</span><span class="sw-n">13</span></summary>')
        un = h[h.index('data-group="unassigned"'):h.index('data-group="done"')]
        # The newest ten, then the way to the rest.
        self.assertEqual(re.findall(r'data-sid="([^"]+)"', un), ["rx", "u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9"])
        self.assertIn('<button type="button" class="sw-more" data-more="unassigned" aria-expanded="false">Show all (13)</button>', un)
        self.assertIn('<details class="sw-group" data-group="unassigned" open>', self.r["unOpen"])
        al = self.r["unAll"]
        al = al[al.index('data-group="unassigned"'):al.index('data-group="done"')]
        self.assertEqual(len(re.findall(r'class="sw-row', al)), 13)
        self.assertIn('aria-expanded="true">Show the newest 10</button>', al)
        few = self.r["unFew"]
        self.assertNotIn('sw-more', few)
        self.assertIn('<span class="sw-n">3</span>', few)
        # A row: its name (or first words), where it ran, whether it runs (the dot, #126).
        live = row_of(h, 'data-sid="u1"')
        self.assertIn('>Question number 1</span>', live)
        self.assertIn('<span class="sw-sub">work1</span>', live)
        self.assertIn('<span class="sw-st working" role="img" aria-label="working"></span>', live)
        self.assertIn('title="Question number 1 · D:\\code\\work1"', live)
        self.assertIn('<span class="sw-sub">work2 · past session</span>', row_of(h, 'data-sid="u2"'))
        self.assertIn('>Named one</span>', row_of(h, 'data-sid="u3"'))
        task = row_of(h, 'data-sid="rx"')
        self.assertIn('<span class="tno">#3</span> No project task', task)
        self.assertIn('<span class="sw-sub">task, not running</span>', task)
        # Every row listed opens: none the page has no row for (review 1).
        for gone in ('data-sid="u-grp"', 'data-sid="ua"', 'data-sid="rd"'):
            self.assertNotIn(gone, self.r["unAll"])
        # Folded to ten, the open one (the 13th) still shows, after them.
        sel = self.r["unSel"]
        sel = sel[sel.index('data-group="unassigned"'):sel.index('data-group="done"')]
        self.assertEqual(re.findall(r'data-sid="([^"]+)"', sel)[-1], "u12")
        self.assertEqual(len(re.findall(r'class="sw-row', sel)), 11)
        self.assertIn('class="sw-row on" data-sid="u12"', sel)
        # The open one is selected.
        self.assertIn('class="sw-row on" data-sid="u3"', self.r["unOpen"])

    def test_an_unassigned_row_opens_in_the_middle_with_make_po(self):
        # The list's one click handler: a row that is not a PO opens with
        # openDetail, the middle on a desktop; its panel's bar is actionsCell.
        i = INDEX.index("// The task switcher's column")
        wiring = INDEX[i:INDEX.index("\n// Poll while the tab is in front.", i)]
        click = wiring[wiring.index("$('#sw-list').addEventListener('click'"):]
        click = click[:click.index("\n});")]
        self.assertIn("if (ev.target.closest('.sw-more[data-more=\"unassigned\"]')) { SW_UN.all = !SW_UN.all; swRender(); return; }", click)
        # (A Ctrl/⌘ click opens its chat as a panel instead, #150.)
        self.assertIn("else openDetail(row.dataset.sid);", click)
        self.assertIn("(ev.ctrlKey || ev.metaKey) && pdRowPanel(row.dataset.sid)", click)
        self.assertIn("SW_UN.open = ev.target.open", wiring)
        # Focus goes back to the row in the group it was in (a task can be in two).
        self.assertIn("box.querySelector(`${inGroup}${cls}[data-room=\"${CSS.escape(had)}\"]`)", wiring)
        self.assertIn("let back = had && at(hadCls);", wiring)
        self.assertIn("unassigned: (un && un.sessions) || []", wiring)
        # The task bar's actions are the same as a row's; in the dock the bar keeps the primary alone, the rest are the panel's ⋯ (#150).
        self.assertIn("function detailActions(r, isLive) {\n  const bar = SessionActions.actionBarHtml(SessionActions.sessionActions(actionState(r, isLive), actionEnv()), { menu: !pdTask() });\n"
                      "  return `<div class=\"dp-actions\">${bar}</div>`;", INDEX)
        bar = self.r["bar"]
        m = re.search(r'<button[^>]*class="[^"]*makepo-btn[^"]*"[^>]*>', bar)
        self.assertTrue(m, bar)
        self.assertNotIn("disabled", m.group(0))
        self.assertIn('data-sid="u2"', m.group(0))
        self.assertIn("Make PO of a new project…", bar)

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
        self.assertIn("if (pj) openPoConv(pj);", click)
        self.assertIn("openProjectPage(pj.id);", fn(INDEX, "function openPoConv("))
        # The filter is remembered per browser.
        self.assertIn("localStorage.setItem(SW_KEY + '-project'", wiring)
        self.assertIn("localStorage.setItem(SW_KEY + '-group'", wiring)    # and the grouping
        self.assertIn('<label class="sw-pick sw-pick-proj"><select id="sw-proj"', INDEX)
        self.assertIn('<label class="sw-pick sw-pick-by"><select id="sw-by"', INDEX)
        # Always there on a desktop, never on a phone; the page makes room.
        self.assertIn("function swOn() { try { return !isPhone() || phList(); }", wiring)
        self.assertIn("body.sw-on main { padding-left: calc(var(--list-w) + 20px); }", INDEX)
        self.assertIn("body { --sw-w: 300px; }", INDEX)
        # #114's button, pin and close are gone: the list is not a panel you open.
        for gone in ('id="sw-btn"', 'id="sw-pin"', 'id="sw-close"', "sw-pinned"):
            self.assertNotIn(gone, INDEX)


if __name__ == "__main__":
    unittest.main()
