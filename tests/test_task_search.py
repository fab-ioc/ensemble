"""The PO is not a task, and the list's filter reads what a row shows.

index.html's "Task search" block runs here in Node (skipped without Node): the
PO's room is left out of every task list, and a row's words for the list's
filter (rowHaystack) are what a person reads, not the folder every task in a
project shares. The top bar's search has its own drop-down
(tests/test_global_find.py). dashboard.attach_search_rooms names the task an
old /api/search hit belongs to.
"""
from __future__ import annotations

import json
import re
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
const taskNoText = r => (r && r.no ? '#' + r.no : '');   // tests/test_task_refs.py covers the real one
const PROJECTS = { projects: [
  { id: 'p1', registered: true, poRoomId: 'room-po', sessions: [{ roomId: 'room-po' }, { roomId: 'room-a' }, { roomId: 'room-b' }, { roomId: 'room-c' }] },
  { id: 'p2', registered: true, poRoomId: 'room-gone', sessions: [{ roomId: 'room-x' }] },
  { id: 'p3', registered: false, poRoomId: 'room-unreg', sessions: [] },
] };
const currentPoRooms = () => poRoomIds(PROJECTS.projects);
const projectById = id => PROJECTS.projects.find(p => p.id === id) || null;
let SELECTED_PROJECT = 'p1', SELECTED_CAT = '__all__';
const path = n => 'D:\\projects\\Ensemble Dashboard\\' + n + '\\repo';
const ALL = [
  { roomId: 'room-po', sessionId: 'room-po', label: 'Ensemble', cwd: path('po') },
  { roomId: 'room-a', sessionId: 'room-a', label: 'Hub: upload files', cwd: path('hub_upload') },
  { roomId: 'room-b', sessionId: 'room-b', label: 'Files panel: drag and drop', cwd: path('files_panel') },
  { roomId: 'room-c', sessionId: 'room-c', label: 'Board columns', cwd: path('board'),
    first: 'Please make the board render faster', last: 'Done: the renderer is quicker' },
  { roomId: 'room-x', sessionId: 'room-x', label: 'Other project task', cwd: path('other') },
  { roomId: '', sessionId: 'abc-123', label: 'An old session', cwd: 'C:\\work\\sigmatrader' },
];
log.pos = [...poRoomIds(PROJECTS.projects)].sort();
log.tasks = PROJECTS.projects.map(p => projectTasks(p).map(s => s.roomId));
log.noProject = projectTasks(null);
log.poIn = ALL.filter(inScope).map(r => r.roomId);
SELECTED_PROJECT = ''; log.poAll = ALL.filter(inScope).map(r => r.sessionId);
SELECTED_PROJECT = 'p1';
const scoped = ALL.filter(inScope);
const find = q => scoped.filter(r => haystackMatches(rowHaystack(r), parseSearchQuery(q))).map(r => r.roomId);
log.two = find('re');          // every task's path has "repo" in it
log.en = find('en');           // and "Ensemble"
log.up = find('up');
log.files = find('files');
log.chat = find('render');
log.title = find('board col');
log.none = find('zzqx');
log.clear = find('');
SELECTED_PROJECT = ''; log.oldPath = ALL.filter(inScope).filter(r => haystackMatches(rowHaystack(r), parseSearchQuery('sigma'))).map(r => r.sessionId);
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TaskSearchPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Task search: begin")
        src = "\n".join([fn(INDEX, "function parseSearchQuery("), fn(INDEX, "function haystackMatches("),
                         INDEX[i:INDEX.index("// ---- Task search: end", i)], fn(INDEX, "function inScope(")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "search.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_po_is_no_task_in_any_list(self):
        self.assertEqual(self.r["pos"], ["room-gone", "room-po"], "registered projects' POs only")
        self.assertEqual(self.r["poIn"], ["room-a", "room-b", "room-c"])
        self.assertNotIn("room-po", self.r["poAll"], "nor in the list of every project")
        self.assertIn("abc-123", self.r["poAll"])

    def test_a_projects_tasks_leave_out_its_po(self):
        self.assertEqual(self.r["tasks"], [["room-a", "room-b", "room-c"], ["room-x"], []])
        self.assertEqual(self.r["noProject"], [])
        for head in ("function scopeOptions(", "function chUseContext(", "function projectsLandingHtml("):
            body = fn(INDEX, head)
            self.assertIn("projectTasks(", body, head)
            self.assertNotIn(".sessions", body, head + " lists the PO as a task")
        switch = INDEX[INDEX.index("$('#proj-switch').addEventListener('click'"):]
        self.assertIn("projectNeeds(pj, list)", switch[:switch.index("\n});\n")], "the project menu's needs count")
        self.assertIn("projectTasks(pj).filter(x => x.attention)", fn(INDEX, "function projectNeeds("))

    def test_the_po_is_not_a_search_result(self):
        self.assertNotIn("room-po", self.r["en"])

    def test_a_quick_search_narrows_to_what_a_person_reads(self):
        self.assertEqual(self.r["two"], [], "a folder every task shares is not a match")
        self.assertEqual(self.r["en"], [], "nor is the project's name in that folder")
        self.assertEqual(self.r["up"], ["room-a"])
        self.assertEqual(self.r["files"], ["room-a", "room-b"])
        self.assertEqual(self.r["chat"], [], "a task's chat is not on its row")
        self.assertEqual(self.r["title"], ["room-c"])
        self.assertEqual(self.r["none"], [])
        self.assertEqual(self.r["clear"], ["room-a", "room-b", "room-c"], "clearing brings every task back")
        self.assertEqual(self.r["oldPath"], ["abc-123"], "a session outside a task by its folder")

    def test_the_page_uses_it(self):
        rows = fn(INDEX, "function renderRows(")
        self.assertIn("const filtered = ALL_ROWS.filter(passesFilter);", rows)
        self.assertIn("const pool = ALL_ROWS.filter(r => inScope(r)", rows)
        self.assertNotIn("SEARCH_QUERY", INDEX, "the top bar's search no longer narrows the board (#161)")
        self.assertIn("attentionItems()", fn(INDEX, "function swRender("))
        self.assertIn("attentionItems()", fn(INDEX, "function renderAttention("))
        self.assertIn("rowHaystack(r)", fn(INDEX, "function swHay("), "the list's filter reads a row as the board did")

    def test_the_po_stays_reachable(self):
        self.assertIn("ALL_ROWS.find(r => r.roomId === rid)", fn(INDEX, "function poRowOf("),
                      "the pill and pane find the PO among every row, not the listed ones")


class ProjectCountsLeaveOutThePo(unittest.TestCase):
    def build(self, rows):
        reg = [{"id": "p1", "name": "One", "path": "", "poRoomId": "room-po"}]
        links = {r["roomId"]: "p1" for r in rows}
        with mock.patch.object(dashboard, "load_projects", return_value=reg), \
                mock.patch.object(dashboard, "load_session_projects", return_value=links), \
                mock.patch.object(dashboard, "load_sessions", return_value=rows), \
                mock.patch.object(dashboard, "project_home", return_value=""):
            return dashboard.build_projects()

    # tests/test_po_needs_you.py covers the PO that cannot go on, which does count as waiting.
    def test_a_po_alone_is_nothing_live_or_waiting(self):
        out = self.build([{"roomId": "room-po", "sessionId": "room-po", "isLive": True,
                           "status": "waiting", "attention": {"state": "stalled"}, "updatedAt": 5}])
        p = out["projects"][0]
        self.assertEqual((p["live"], p["waiting"], out["summary"]["needsYou"]), (0, 0, 0))
        self.assertEqual([s["roomId"] for s in p["sessions"]], ["room-po"], "the page still finds the PO")
        self.assertEqual(p["updatedAt"], 5)

    def test_its_tasks_still_count(self):
        out = self.build([
            {"roomId": "room-po", "sessionId": "room-po", "isLive": True, "status": "waiting_human"},
            {"roomId": "room-a", "sessionId": "room-a", "isLive": True, "status": "waiting_human",
             "attention": {"state": "waiting_for_you"}},
            {"roomId": "room-b", "sessionId": "room-b", "attention": {"state": "agent_gone"}},
        ])
        p = out["projects"][0]
        self.assertEqual((p["live"], p["waiting"], out["summary"]["needsYou"]), (1, 2, 2))


class AttachSearchRooms(unittest.TestCase):
    def test_hits_name_their_task(self):
        rooms = [
            {"id": "room-a", "participants": [
                {"kind": "user"},
                {"kind": "agent", "sessionId": "own-2", "rotations": [{"fromSessionId": "own-1"}]},
                {"kind": "agent", "sessionId": "", "reviews": [{"sessionId": "rev-1"}]},
            ]},
            {"id": "room-b", "participants": [{"kind": "agent", "sessionId": "b-1"}]},
        ]
        hits = [{"sessionId": s, "hits": 1, "snippet": ""} for s in ("own-1", "own-2", "rev-1", "b-1", "loose")]
        out = dashboard.attach_search_rooms(hits, rooms)
        self.assertEqual([h.get("roomId") for h in out], ["room-a", "room-a", "room-a", "room-b", None])

    def test_no_rooms_leaves_hits_as_they_were(self):
        hits = [{"sessionId": "x", "hits": 1, "snippet": ""}]
        self.assertEqual(dashboard.attach_search_rooms(hits, []), [{"sessionId": "x", "hits": 1, "snippet": ""}])


if __name__ == "__main__":
    unittest.main()
