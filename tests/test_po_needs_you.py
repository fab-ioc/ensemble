"""A PO that cannot go on reaches the list's Needs you.

The PO is no task, so no board shows it. The one exception: blocked, waiting
for you or gone, it is counted and listed by the list's Needs you (#135: the
bell and the Needs you page are gone), named as the PO. Idle or stalled it
never shows. index.html's functions run here in Node
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

// Opening: the page's own poContext, poSplitProject, poRowOf and renderPo run
// over a stand-in for #po-panel, so what is read is the conversation shown.
let PO_PEEK = false, PO_PIN = '', PO_LAST = '', SELECTED_PROJECT = '', PROJECT_TAB = 'changes', WS_SCOPE = 'x', SELECTED_SID = '';
let PHONE = false, calls = [], ALL_ROWS = [];
const UNASSIGNED_ID = '__unassigned__', VIEW_MODE = 'board';
const ROWS = PROJECTS.projects.flatMap(p => p.sessions.map(s => ({ roomId: s.roomId, sessionId: s.roomId })));
const registeredProjects = () => PROJECTS.projects.filter(p => p.registered);
const isPhone = () => PHONE;
// The PO screen's panels, loaded: a project with a PO is a Dock with the PO chat among them.
var PD = { failed: '', lib: {} };
const pdReveal = id => calls.push('reveal:' + id), pdPointsFrame = () => {}, pdPaintPoints = () => {}, pdPlaceChat = () => {}, pdTaskWanted = () => false;
const el = () => ({ hidden: false, dataset: {}, kids: [], className: '',
  querySelectorAll() { return this.kids; }, querySelector() { return null; }, appendChild(k) { this.kids.push(k); }, remove() {} });
let head, frames, panel;
// A header with the ⋯ menu in it (#126): writing it closes the menu.
const menuHead = () => { const h = el(); h.written = 0; h.shown = '';
  h.menu = { hidden: true, querySelector: () => null, closest: () => h, contains: () => false };
  h.btn = { setAttribute(k, v) { h.expanded = v; } };
  Object.defineProperty(h, 'innerHTML', { set(v) { h.written++; h.shown = v; h.menu.hidden = true; } });
  h.querySelector = sel => (sel === '.po-menu' ? h.menu : sel === '[data-po="more"]' ? h.btn
    : sel === '.po-menu:not([hidden])' ? (h.menu.hidden ? null : h.menu) : null);
  return h; };
const document = { getElementById: id => (id === 'po-panel' ? panel : null), createElement: el,
  querySelectorAll: sel => (sel === '#po-panel .po-head' && head && head.menu ? [head]
    : sel === '#po-panel .po-menu:not([hidden])' && head && head.menu && !head.menu.hidden ? [head.menu] : []),
  body: { classList: { on: new Set(), toggle(c, v) { v ? this.on.add(c) : this.on.delete(c); }, contains(c) { return this.on.has(c); } } } };
const focusKeyIn = () => null, restoreFocus = () => {}, swRender = () => {};
let HEAD_TAIL = '';
const poHeadHtml = (pj, row) => pj.id + ':' + row.roomId + HEAD_TAIL;
const renderRows = () => { calls.push('renderRows'); renderPo(); };
const openDetail = id => calls.push('openDetail:' + id);
const open = (rid, setup) => {
  PO_PEEK = false; PO_PIN = ''; SELECTED_PROJECT = ''; PROJECT_TAB = 'changes'; SELECTED_SID = '';
  PHONE = true; PD.failed = ''; ALL_ROWS = ROWS; calls = []; tray.hidden = false;
  head = menuHead(); frames = el(); document.body.classList.on.clear();
  panel = { hidden: true, built: false, set innerHTML(v) { this.built = true; },
            hasAttribute() { return false; }, removeAttribute() {}, classList: { remove() {} },
            querySelector(sel) { return !this.built ? null : sel === '.po-frames' ? frames : head; } };
  if (setup) { setup(); renderPo(); }
};
const overview = id => () => { SELECTED_PROJECT = id; PROJECT_TAB = 'tasks'; };
// The ⋯ menu (#126): the header waits while it is open and is written when it
// closes; another PO is written at once, its menu closed; leaving the page
// (a click into the conversation's frame) closes it.
const mh = () => [head.shown, head.menu.hidden, head.dataset.room || ''];
open('po-blocked', () => { PHONE = false; overview('p1')(); });
log.menu = [mh()];
poMenuOpen(head, true); HEAD_TAIL = ' +1'; renderPo(); log.menu.push(mh());
poMenuOpen(head, false); log.menu.push(mh());
poMenuOpen(head, true); overview('p2')(); renderPo(); log.menu.push(mh());
HEAD_TAIL = ' +2'; poMenuOpen(head, true); renderPo(); poMenusClose(); log.menu.push(mh());
// A control clicked while a change is held acts on its PO before the header is
// written; a real control is detached by that write (closest() finds nothing).
const acted = [];
const poSwitchFlow = p => acted.push('switch:' + p), poClosePeek = () => acted.push('close');
const api = (u, o) => { acted.push('api:' + u + ' ' + o.body); return new Promise(() => {}); };
const toast = () => {}, refresh = () => {};
const control = po => { const w = head.written; return { dataset: { po }, disabled: false, getAttribute: () => 'false',
  closest: () => (head.written === w ? head : null) }; };
log.clicks = [];
for (const po of ['open', 'switch', 'resume']) {
  open('po-blocked', () => { PHONE = false; overview('p1')(); });
  head.dataset.sid = 'sid-p1'; poMenuOpen(head, true); HEAD_TAIL = ' +' + po; renderPo(); acted.length = 0; calls = [];
  poHeadClick(control(po));
  log.clicks.push([po, acted.concat(calls.filter(c => c.startsWith('openDetail'))), head.shown, head.menu.hidden]);
}
HEAD_TAIL = '';
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PoNeedsYou(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Task search: begin")
        heads = ("function sinceClock(",
                 "function attentionItems(", "function openPoOf(", "function poRowOf(",
                 "function projectOfRoom(", "function poDockProject(", "function poSplitProject(", "function poContext(", "function renderPo(", "function poMenuOpen(", "function poMenusClose(", "async function poHeadClick(", "function poMidGo(", "function midLeave(")
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

    def test_a_tasks_item_is_unchanged(self):
        self.assertEqual(self.r["task"]["title"], "Hub: upload files")
        self.assertEqual(self.r["task"]["state"], "stalled", "a stalled task still shows")
        self.assertNotIn("isPo", self.r["task"])

    def test_the_po_menu_holds_its_header_only_for_the_same_po(self):
        self.assertEqual(self.r["menu"], [
            ["p1:po-blocked", True, "po-blocked"],
            ["p1:po-blocked", False, "po-blocked"],        # open: the change waits
            ["p1:po-blocked +1", True, "po-blocked"],      # closed: it is written
            ["p2:po-waiting +1", True, "po-waiting"],      # another PO: written at once, menu closed
            ["p2:po-waiting +2", True, "po-waiting"],      # leaving the page closes it and writes it
        ])
        self.assertIn("window.addEventListener('blur', poMenusClose);", INDEX)
        self.assertIn("if (more && ev.relatedTarget && !more.contains(ev.relatedTarget)) poMenuOpen(more.closest('.po-head'), false);", INDEX)

    def test_a_control_acts_before_the_held_header_is_written(self):
        self.assertEqual(self.r["clicks"], [
            ["open", ["openDetail:sid-p1"], "p1:po-blocked +open", True],
            ["switch", ["switch:p1"], "p1:po-blocked +switch", True],
            ["resume", ['api:/api/room/resume {"roomId":"po-blocked"}'], "p1:po-blocked +resume", True],
        ])

    def test_a_projects_count_agrees_with_needs_you(self):
        self.assertEqual(self.r["needs"], [2, 1, 1, 0, 0, 0], "p1: its blocked PO and its stalled task")

    def test_a_po_belongs_to_the_project_that_names_it(self):
        self.assertEqual(self.r["linkShown"], [["po-a", "a", "A"]], "Needs you lists it under A")
        self.assertEqual(self.r["linkNeeds"], [1, 0, 0], "A's menu counts it; B's counts neither PO kept there")
        self.assertEqual(self.r["linkTasks"], [["a-task"], [], []], "another project's PO is no task of B's")

    def test_the_page_uses_it(self):
        self.assertIn("attentionShown(ATTENTION.items", fn(INDEX, "function attentionItems("))
        self.assertEqual(INDEX.count("PO_NEEDS_STATES"), 2, "one place decides which PO items pass")
        self.assertIn("openPoOf(pj);", fn(INDEX, "function openMsgLink("), "one way to open a project's PO")
        self.assertNotIn("PO_PEEK = true", fn(INDEX, "function openMsgLink("))
        self.assertIn("projectNeeds(pj, list)", INDEX[INDEX.index("$('#proj-switch').addEventListener('click'"):])
        self.assertIn("if (pj) openPoConv(pj);", INDEX[INDEX.index("$('#sw-list').addEventListener('click'"):])
        self.assertIn("openProjectPage(pj.id);\n  pdReveal('po-chat');", fn(INDEX, "function openPoConv("),
                      "a PO's row in Needs you opens its project's screen, the PO chat revealed")
        self.assertIn("if (PD.failed) { openPoOf(pj); return; }", fn(INDEX, "function openPoConv("),
                      "where the dock failed, its drawer")
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
