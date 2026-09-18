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
const before = JSON.stringify(ATTENTION.items);
const shown = attentionItems();
log.untouched = JSON.stringify(ATTENTION.items) === before;
log.shown = shown.map(i => i.roomId);
log.pos = shown.filter(i => i.isPo).map(i => [i.roomId, i.title, i.ref, i.projectId, i.project]);
log.task = shown.find(i => i.roomId === 'room-a');
log.noProjects = attentionShown(ATTENTION.items, null).length;
log.needs = PROJECTS.projects.map(projectNeeds);
log.bellPo = notifItemHtml(shown[0]);
log.bellTask = notifItemHtml(log.task);
log.page = needsYouHtml();

// Opening: what the page would do, written down instead of done.
let PO_PEEK = false, PO_PIN = '', SELECTED_PROJECT = '', PROJECT_TAB = 'changes', WS_SCOPE = 'x', SB_DEST = 'needsyou', SELECTED_SID = '';
let SPLIT = null, WIDE = false, PHONE = false, ROWS = true, calls = [];
const poSplitProject = () => SPLIT;
const boardWide = () => WIDE;
const isPhone = () => PHONE;
const poRowOf = pj => (ROWS && pj && pj.poRoomId ? { roomId: pj.poRoomId } : null);
const renderPo = () => calls.push('renderPo');
const renderRows = () => calls.push('renderRows');
const openDetail = id => calls.push('openDetail:' + id);
const open = (rid, setup) => {
  PO_PEEK = false; PO_PIN = ''; SELECTED_PROJECT = ''; PROJECT_TAB = 'changes'; SB_DEST = 'needsyou'; SELECTED_SID = '';
  SPLIT = null; WIDE = false; PHONE = false; ROWS = true; calls = []; tray.hidden = false;
  if (setup) setup();
  openAttentionItem(rid);
  return { calls, peek: PO_PEEK, pin: PO_PIN, project: SELECTED_PROJECT, tab: PROJECT_TAB, dest: SB_DEST, tray: tray.hidden };
};
log.openPo = open('po-blocked');
log.openPoLeading = open('po-blocked', () => { SPLIT = projectById('p1'); SELECTED_PROJECT = 'p1'; PROJECT_TAB = 'tasks'; SB_DEST = ''; });
log.openPoOtherLeading = open('po-gone', () => { SPLIT = projectById('p1'); SELECTED_PROJECT = 'p1'; PROJECT_TAB = 'tasks'; SB_DEST = ''; });
log.openPoWide = open('po-waiting', () => { SPLIT = projectById('p2'); WIDE = true; });
log.openPoNoRow = open('po-blocked', () => { ROWS = false; });
log.openTask = open('room-a');
log.openHidden = open('po-stalled');
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PoNeedsYouPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Task search: begin")
        heads = ("function attentionItems(", "function notifItemHtml(", "function needsYouHtml(",
                 "function openAttentionItem(", "function openPoOf(")
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
        self.assertEqual((o["calls"], o["peek"], o["pin"]), (["renderPo"], True, "p1"),
                         "the drawer over the page you are on")
        self.assertEqual((o["dest"], o["tray"]), ("needsyou", True), "Needs you stays under it; the tray closes")
        lead = self.r["openPoLeading"]
        self.assertEqual((lead["calls"], lead["peek"]), ([], False), "already leading the Overview")
        other = self.r["openPoOtherLeading"]
        self.assertEqual((other["calls"], other["project"], other["tab"], other["peek"]),
                         (["renderRows"], "p3", "tasks", False), "another project's PO: its Overview")
        wide = self.r["openPoWide"]
        self.assertEqual((wide["calls"], wide["peek"], wide["pin"]), (["renderPo"], True, "p2"),
                         "Board wide: the PO is the drawer")
        for k in ("openPo", "openPoLeading", "openPoOtherLeading", "openPoWide", "openPoNoRow"):
            self.assertFalse([c for c in self.r[k]["calls"] if c.startswith("openDetail")], k)

    def test_a_po_whose_row_has_not_loaded_opens_its_project(self):
        o = self.r["openPoNoRow"]
        self.assertEqual((o["calls"], o["project"], o["tab"], o["dest"], o["peek"]),
                         (["renderRows"], "p1", "tasks", "", False))

    def test_a_task_still_opens_its_panel(self):
        o = self.r["openTask"]
        self.assertEqual(o["calls"], ["renderRows", "openDetail:room-a"])
        self.assertEqual((o["project"], o["tab"], o["peek"]), ("p1", "tasks", False))

    def test_a_hidden_po_item_opens_nothing(self):
        self.assertEqual(self.r["openHidden"]["calls"], [])

    def test_a_projects_count_agrees_with_the_bell(self):
        self.assertEqual(self.r["needs"], [2, 1, 1, 0, 0, 0], "p1: its blocked PO and its stalled task")

    def test_the_page_uses_it(self):
        self.assertIn("attentionShown(ATTENTION.items", fn(INDEX, "function attentionItems("))
        self.assertEqual(INDEX.count("PO_NEEDS_STATES"), 2, "one place decides which PO items pass")
        self.assertIn("openPoOf(pj);", fn(INDEX, "function openMsgLink("), "one way to open a project's PO")
        self.assertNotIn("PO_PEEK = true", fn(INDEX, "function openMsgLink("))
        self.assertIn("projectNeeds(pj)", INDEX[INDEX.index("$('#proj-switch').addEventListener('click'"):])
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

    def test_the_page_and_the_hub_name_the_same_states(self):
        i = INDEX.index("const PO_NEEDS_STATES = [")
        page = json.loads(INDEX[INDEX.index("[", i):INDEX.index("]", i) + 1].replace("'", '"'))
        self.assertEqual(tuple(page), dashboard.PO_NEEDS_STATES)


if __name__ == "__main__":
    unittest.main()
