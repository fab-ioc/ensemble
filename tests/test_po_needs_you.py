"""A PO that cannot go on reaches the bell and Needs you.

The PO is no task, so no list shows it. The one exception: blocked, waiting
for you or gone, it is counted and listed by the bell and the Needs you page,
named as the PO, and its item opens the PO's conversation instead of the task
panel. Idle or stalled it never shows. index.html's functions run here in Node
(skipped without Node); dashboard.build_projects counts by the same states.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


JS = r"""
%s
const log = {};
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const agoSpan = () => '1m';
const ATTN_LABEL = { agent_gone: 'agent gone', blocked: 'blocked', waiting_for_you: 'waiting for you', stalled: 'stalled' };
const tray = { hidden: false };
const $ = () => tray;
const po = (id, name, room, attention) => ({ id, name, registered: true, poRoomId: room,
  sessions: [{ roomId: room, attention }, { roomId: room + '-task', attention: null }] });
const PROJECTS = { projects: [
  po('p1', 'One', 'po-blocked', { state: 'blocked' }),
  po('p2', 'Two', 'po-waiting', { state: 'waiting_for_you' }),
  po('p3', 'Three', 'po-gone', { state: 'agent_gone' }),
  po('p4', 'Four', 'po-stalled', { state: 'stalled' }),
  po('p5', 'Five', 'po-idle', null),
  { id: 'p6', name: 'Six', registered: false, poRoomId: 'po-unreg', sessions: [] },
] };
PROJECTS.projects[0].sessions.push({ roomId: 'room-a', attention: { state: 'stalled' } });
const item = (roomId, state, more) => ({ roomId, state, reason: 'why', ref: 'ED-7', title: 'Run the project',
  projectId: 'px', project: 'Elsewhere', agentIdentity: 'claude', since: 5, ...more });
const ATTENTION = { items: [
  item('po-blocked', 'blocked'), item('po-waiting', 'waiting_for_you'), item('po-gone', 'agent_gone'),
  item('po-stalled', 'stalled'),
  item('room-a', 'stalled', { ref: 'ED-12', title: 'Hub: upload files', projectId: 'p1', project: 'One' }),
  item('po-unreg', 'blocked', { title: 'Not a PO here' }),
] };
const projectById = id => PROJECTS.projects.find(p => p.id === id) || null;
let CURRENT = PROJECTS.projects;
const currentPoRooms = () => poRoomIds(CURRENT);
const before = JSON.stringify(ATTENTION.items);
const shown = attentionItems();
log.untouched = JSON.stringify(ATTENTION.items) === before;
log.shown = shown.map(i => i.roomId);
log.pos = shown.filter(i => i.isPo).map(i => [i.roomId, i.title, i.ref, i.projectId, i.project]);
log.task = shown.find(i => i.roomId === 'room-a');
log.noProjects = attentionShown(ATTENTION.items, null).length;
log.needs = PROJECTS.projects.map(p => projectNeeds(p, PROJECTS.projects));
// A PO whose room is kept in another project: A's blocked PO and C's stalled PO sit in B's sessions.
const LINK = [
  { id: 'a', name: 'A', registered: true, poRoomId: 'po-a', sessions: [{ roomId: 'a-task', attention: null }] },
  { id: 'b', name: 'B', registered: true, poRoomId: 'po-b', sessions: [{ roomId: 'po-b', attention: null },
    { roomId: 'po-a', attention: { state: 'blocked' } }, { roomId: 'po-c', attention: { state: 'stalled' } }] },
  { id: 'c', name: 'C', registered: true, poRoomId: 'po-c', sessions: [] },
];
CURRENT = LINK;
log.linkNeeds = LINK.map(p => projectNeeds(p, LINK));
log.linkTasks = LINK.map(p => projectTasks(p).map(s => s.roomId));
log.linkShown = attentionShown([item('po-a', 'blocked', { projectId: 'b', project: 'B' }),
                                item('po-c', 'stalled', { projectId: 'b', project: 'B' })], LINK).map(i => [i.roomId, i.projectId, i.project]);
CURRENT = PROJECTS.projects;
log.bellPo = notifItemHtml(shown[0]);
log.bellTask = notifItemHtml(log.task);
log.page = needsYouHtml();

// Opening: the page's own poContext, poSplitProject, poRowOf and renderPo run
// over a stand-in for #po-panel, so what is read is the conversation shown.
let PO_PEEK = false, PO_PIN = '', PO_LAST = '', SELECTED_PROJECT = '', PROJECT_TAB = 'changes', WS_SCOPE = 'x', SB_DEST = 'needsyou', SELECTED_SID = '';
let PHONE = false, calls = [], ALL_ROWS = [];
const UNASSIGNED_ID = '__unassigned__', VIEW_MODE = 'board';
const ROWS = PROJECTS.projects.flatMap(p => p.sessions.map(s => ({ roomId: s.roomId, sessionId: s.roomId })));
const registeredProjects = () => PROJECTS.projects.filter(p => p.registered);
const isPhone = () => PHONE;
// The PO screen's panels, loaded: a project with a PO is a Dock with the PO chat among them.
var PD = { failed: '', lib: {} };
const pdReveal = id => calls.push('reveal:' + id), pdPointsFrame = () => {}, pdPaintPoints = () => {}, pdPlaceChat = () => {};
const el = () => ({ hidden: false, dataset: {}, kids: [], className: '',
  querySelectorAll() { return this.kids; }, appendChild(k) { this.kids.push(k); }, remove() {} });
let head, frames, panel;
const document = { getElementById: id => (id === 'po-panel' ? panel : null), createElement: el,
  body: { classList: { on: new Set(), toggle(c, v) { v ? this.on.add(c) : this.on.delete(c); } } } };
const renderPoPill = () => {}, focusKeyIn = () => null, restoreFocus = () => {};
const poHeadHtml = (pj, row) => pj.id + ':' + row.roomId;
const renderRows = () => { calls.push('renderRows'); renderPo(); };
const openDetail = id => calls.push('openDetail:' + id);
const poFocusComposer = () => calls.push('focus');
const seen = () => ({ peek: PO_PEEK, pin: PO_PIN, drawer: document.body.classList.on.has('po-peek'),
  leads: document.body.classList.on.has('po-dock'), calls: calls.slice(), room: panel.hidden ? '' : head.dataset.room,
  lit: panel.hidden ? [] : frames.kids.filter(f => !f.hidden).map(f => f.dataset.room) });
const open = (rid, setup) => {
  PO_PEEK = false; PO_PIN = ''; SELECTED_PROJECT = ''; PROJECT_TAB = 'changes'; SB_DEST = 'needsyou'; SELECTED_SID = '';
  PHONE = false; ALL_ROWS = ROWS; calls = []; tray.hidden = false;
  head = el(); frames = el(); document.body.classList.on.clear();
  panel = { hidden: true, built: false, set innerHTML(v) { this.built = true; },
            hasAttribute() { return false; }, removeAttribute() {}, classList: { remove() {} },
            querySelector(sel) { return !this.built ? null : sel === '.po-frames' ? frames : head; } };
  if (setup) { setup(); renderPo(); }
  openAttentionItem(rid);
  const lit = frames.kids.filter(f => !f.hidden).map(f => f.dataset.room);
  return { calls, peek: PO_PEEK, pin: PO_PIN, project: SELECTED_PROJECT, tab: PROJECT_TAB, dest: SB_DEST, tray: tray.hidden,
           drawer: document.body.classList.on.has('po-peek'), leads: document.body.classList.on.has('po-dock'),
           room: panel.hidden ? '' : head.dataset.room, lit };
};
const overview = id => () => { SELECTED_PROJECT = id; PROJECT_TAB = 'tasks'; SB_DEST = ''; };
log.openPo = open('po-blocked');
log.openPoLeading = open('po-blocked', overview('p1'));
log.openPoOtherLeading = open('po-gone', overview('p1'));
log.openPoOtherPhoneTask = open('po-gone', () => { overview('p1')(); PHONE = true; SELECTED_SID = 'room-a'; });
// Three's PO opened over Needs you; then One's PO screen; its pill there; the pill elsewhere, twice.
open('po-gone');
log.pillSteps = [seen()];
overview('p1')(); calls = []; renderRows(); log.pillSteps.push(seen());
calls = []; poPillClick(); log.pillSteps.push(seen());
SELECTED_PROJECT = ''; SB_DEST = 'needsyou'; renderRows(); calls = []; poPillClick(); log.pillSteps.push(seen());
poPillClick(); log.pillSteps.push(seen());
// A drawer the phone layout closes leaves no pin either.
open('po-gone'); PO_PEEK = false; renderPo(); log.pinAfterClose = PO_PIN;
log.openPoNoRow = open('po-blocked', () => { ALL_ROWS = []; });
log.openTask = open('room-a');
log.openHidden = open('po-stalled');
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PoNeedsYouPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Task search: begin")
        heads = ("function sinceClock(", "function attnWhen(",
                 "function attentionItems(", "function notifItemHtml(", "function needsYouHtml(",
                 "function openAttentionItem(", "function openPoOf(", "function poRowOf(",
                 "function projectOfRoom(", "function poDockProject(", "function poSplitProject(", "function poContext(", "function renderPo(", "function poPillClick(")
        src = "\n".join([INDEX[i:INDEX.index("// ---- Task search: end", i)]] + [fn(INDEX, h) for h in heads])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "po_needs_you.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_a_po_shows_in_three_states_only(self):
        self.assertEqual(self.r["shown"], ["po-blocked", "po-waiting", "po-gone", "room-a", "po-unreg"],
                         "a stalled PO and an idle one stay out; an unregistered project names no PO")
        self.assertEqual(self.r["noProjects"], 6, "before the projects are known nothing is dropped")
        self.assertTrue(self.r["untouched"], "the hub's items are not rewritten")

    def test_it_is_named_as_the_po_under_its_project(self):
        self.assertEqual(self.r["pos"], [["po-blocked", "PO", "", "p1", "One"],
                                         ["po-waiting", "PO", "", "p2", "Two"],
                                         ["po-gone", "PO", "", "p3", "Three"]])
        bell = self.r["bellPo"]
        self.assertIn('<span class="notif-title">PO</span>', bell)
        self.assertNotIn("Run the project", bell)
        self.assertNotIn("tno", bell, "no task number")
        self.assertIn('<div class="notif-proj">One · claude</div>', bell)
        self.assertIn('<span class="notif-chip blocked">blocked</span>', bell)
        page = self.r["page"]
        self.assertIn('<span class="dest-title">PO</span>', page)
        self.assertIn('<div class="dest-group-head">One<span class="dest-group-n">2</span></div>', page,
                      "the PO's item sits with its project's tasks")
        self.assertIn('<span class="dest-count">5</span>', page)
        self.assertNotIn("po-stalled", page)
        self.assertNotIn("Elsewhere", page.replace('<div class="dest-group-head">Elsewhere', "", 1),
                         "only the item that is no PO keeps the hub's project")

    def test_a_tasks_item_is_unchanged(self):
        self.assertEqual(self.r["task"]["title"], "Hub: upload files")
        self.assertEqual(self.r["task"]["state"], "stalled", "a stalled task still shows")
        self.assertNotIn("isPo", self.r["task"])
        self.assertIn('<span class="notif-title"><span class="tno">ED-12</span>Hub: upload files</span>',
                      self.r["bellTask"])

    def test_the_click_opens_the_po_not_the_task_panel(self):
        o = self.r["openPo"]
        self.assertEqual((o["drawer"], o["room"], o["lit"], o["pin"]), (True, "po-blocked", ["po-blocked"], "p1"),
                         "the drawer over the page you are on")
        self.assertEqual((o["dest"], o["tray"]), ("needsyou", True), "Needs you stays under it; the tray closes")
        lead = self.r["openPoLeading"]
        self.assertEqual((lead["calls"], lead["drawer"], lead["leads"], lead["room"]), (["reveal:po-chat"], False, True, "po-blocked"),
                         "already leading its PO screen: the PO chat panel comes on screen")
        for k, o in self.r.items():
            if k.startswith("openPo"):
                self.assertFalse([c for c in o["calls"] if c.startswith("openDetail")], k)

    def test_another_projects_po_is_the_one_that_opens(self):
        other = self.r["openPoOtherLeading"]
        self.assertEqual((other["project"], other["tab"], other["leads"], other["drawer"], other["room"], other["lit"]),
                         ("p3", "tasks", True, False, "po-gone", ["po-gone"]), "a PO leads the page: go to the one asked for")
        phone = self.r["openPoOtherPhoneTask"]
        self.assertEqual((phone["project"], phone["drawer"], phone["room"], phone["lit"]),
                         ("p1", True, "po-gone", ["po-gone"]), "a phone's open task on One's Overview: the same")

    def test_the_pill_opens_the_po_it_names_after_a_pinned_drawer_ended(self):
        opened, screen, there, pill, again = self.r["pillSteps"]
        self.assertEqual((opened["drawer"], opened["room"], opened["pin"]), (True, "po-gone", "p3"))
        self.assertEqual((screen["leads"], screen["drawer"], screen["room"], screen["pin"]),
                         (True, False, "po-blocked", ""), "One's PO screen: its PO leads, the drawer and its pin end")
        self.assertEqual((there["calls"], there["drawer"], there["leads"]), (["reveal:po-chat", "focus"], False, True),
                         "the pill on the PO screen brings the PO chat panel forward and its box takes the keys")
        self.assertEqual((pill["drawer"], pill["room"], pill["lit"], pill["pin"]),
                         (True, "po-blocked", ["po-blocked"], ""), "One's pill opens One's PO, not the one pinned before")
        self.assertEqual((again["drawer"], again["peek"]), (False, False), "and closes it")
        self.assertEqual(self.r["pinAfterClose"], "", "however a drawer ends, its pin goes with it")
        self.assertIn("if (pill) { poPillClick(); return; }", INDEX)

    def test_a_po_whose_row_has_not_loaded_opens_its_project(self):
        o = self.r["openPoNoRow"]
        self.assertEqual((o["calls"], o["project"], o["tab"], o["dest"], o["peek"], o["room"]),
                         (["renderRows", "reveal:po-chat"], "p1", "tasks", "", False, ""))

    def test_a_task_still_opens_its_panel(self):
        o = self.r["openTask"]
        self.assertEqual(o["calls"], ["renderRows", "openDetail:room-a"])
        self.assertEqual((o["project"], o["tab"], o["peek"]), ("p1", "tasks", False))

    def test_a_hidden_po_item_opens_nothing(self):
        self.assertEqual(self.r["openHidden"]["calls"], [])

    def test_a_projects_count_agrees_with_the_bell(self):
        self.assertEqual(self.r["needs"], [2, 1, 1, 0, 0, 0], "p1: its blocked PO and its stalled task")

    def test_a_po_belongs_to_the_project_that_names_it(self):
        self.assertEqual(self.r["linkShown"], [["po-a", "a", "A"]], "the bell lists it under A")
        self.assertEqual(self.r["linkNeeds"], [1, 0, 0], "A's menu counts it; B's counts neither PO kept there")
        self.assertEqual(self.r["linkTasks"], [["a-task"], [], []], "another project's PO is no task of B's")

    def test_the_page_uses_it(self):
        self.assertIn("attentionShown(ATTENTION.items", fn(INDEX, "function attentionItems("))
        self.assertEqual(INDEX.count("PO_NEEDS_STATES"), 2, "one place decides which PO items pass")
        self.assertIn("openPoOf(pj);", fn(INDEX, "function openMsgLink("), "one way to open a project's PO")
        self.assertNotIn("PO_PEEK = true", fn(INDEX, "function openMsgLink("))
        self.assertIn("projectNeeds(pj, list)", INDEX[INDEX.index("$('#proj-switch').addEventListener('click'"):])
        self.assertIn("if (rid && ATTENTION_BY_ROOM.has(rid)) { openAttentionItem(rid); return; }", INDEX,
                      "a Needs you row opens through the same function as the bell's")
        self.assertRegex(INDEX, r"PO_PEEK && !ev\.target\.closest\('[^']*#notif-tray[^']*'\)\) poClosePeek\(\)",
                         "the click in the tray that opened the drawer does not close it")
        self.assertIn("if (isPoRoom(r.roomId, currentPoRooms())) return false;", fn(INDEX, "function inScope("),
                      "the PO is still no row")


class ProjectCountsThePoThatCannotGoOn(unittest.TestCase):
    def build(self, po: dict, tasks: list | None = None):
        rows = [{"roomId": "room-po", "sessionId": "room-po", **po}] + (tasks or [])
        reg = [{"id": "p1", "name": "One", "path": "", "poRoomId": "room-po"}]
        links = {r["roomId"]: "p1" for r in rows}
        with mock.patch.object(dashboard, "load_projects", return_value=reg), \
                mock.patch.object(dashboard, "load_session_projects", return_value=links), \
                mock.patch.object(dashboard, "load_sessions", return_value=rows), \
                mock.patch.object(dashboard, "project_home", return_value=""):
            out = dashboard.build_projects()
        p = out["projects"][0]
        return p["live"], p["waiting"], out["summary"]["needsYou"], out["summary"]["agentsLive"]

    def test_each_of_the_three_states_counts_once(self):
        for state in ("blocked", "waiting_for_you", "agent_gone"):
            self.assertEqual(self.build({"isLive": True, "status": "waiting", "attention": {"state": state}}),
                             (0, 1, 1, 0), state)

    def test_an_idle_waiting_po_counts_zero(self):
        for status in ("waiting", "waiting_human", "idle"):
            self.assertEqual(self.build({"isLive": True, "status": status, "attention": None}), (0, 0, 0, 0), status)

    def test_a_stalled_po_counts_zero(self):
        self.assertEqual(self.build({"isLive": True, "status": "waiting", "attention": {"state": "stalled"}}),
                         (0, 0, 0, 0))

    def test_beside_its_tasks(self):
        tasks = [{"roomId": "room-a", "sessionId": "room-a", "isLive": True, "status": "waiting_human"},
                 {"roomId": "room-b", "sessionId": "room-b", "attention": {"state": "stalled"}}]
        self.assertEqual(self.build({"isLive": True, "attention": {"state": "blocked"}}, tasks), (1, 3, 3, 1))
        self.assertEqual(self.build({"isLive": True, "status": "waiting"}, tasks), (1, 2, 2, 1))

    def test_a_po_kept_in_another_project_counts_for_the_one_that_names_it(self):
        reg = [{"id": "a", "name": "A", "path": "", "poRoomId": "po-a"},
               {"id": "b", "name": "B", "path": "", "poRoomId": "po-b"},
               {"id": "c", "name": "C", "path": "", "poRoomId": "po-c"}]
        rows = [{"roomId": "po-a", "sessionId": "po-a", "isLive": True, "attention": {"state": "blocked"}},
                {"roomId": "po-c", "sessionId": "po-c", "isLive": True, "status": "waiting", "attention": {"state": "stalled"}},
                {"roomId": "po-b", "sessionId": "po-b", "isLive": True, "status": "waiting"}]
        links = {r["roomId"]: "b" for r in rows}
        with mock.patch.object(dashboard, "load_projects", return_value=reg), \
                mock.patch.object(dashboard, "load_session_projects", return_value=links), \
                mock.patch.object(dashboard, "load_sessions", return_value=rows), \
                mock.patch.object(dashboard, "project_home", return_value=""):
            out = dashboard.build_projects()
        by = {p["id"]: p for p in out["projects"]}
        self.assertEqual([(by[k]["waiting"], by[k]["live"]) for k in "abc"], [(1, 0), (0, 0), (0, 0)])
        self.assertEqual(out["summary"]["needsYou"], 1)
        self.assertEqual(sorted(s["roomId"] for s in by["b"]["sessions"]), ["po-a", "po-b", "po-c"],
                         "the rooms stay where they are kept")

    def test_the_page_and_the_hub_name_the_same_states(self):
        i = INDEX.index("const PO_NEEDS_STATES = [")
        page = json.loads(INDEX[INDEX.index("[", i):INDEX.index("]", i) + 1].replace("'", '"'))
        self.assertEqual(tuple(page), dashboard.PO_NEEDS_STATES)


if __name__ == "__main__":
    unittest.main()
