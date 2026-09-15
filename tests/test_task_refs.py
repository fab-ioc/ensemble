"""A task named by its number in the pages.

session.html's "Task references" block and its markdown run in Node against a
stand-in hub, and check that:

* #18, @codex@18 and @reviewer@ED-7 become chips once the hub has resolved
  them (one request per task, asked in this chat's room), with the task's
  number, title and state; @codex@18 opens the task at that agent, a role at
  the agent holding it;
* a number in code, in a word or a URL, or one that names no task, stays text;
* the line the hub writes under a message (message_refs.task_line) is dropped
  from the balloon, and a person's own line is not;
* a report row names its task by number.

index.html's "Task numbers" block: #18 inside a project and ED-18 where
projects mix, a link's number found on the board, a spec's #18 as a link, and
search finding a task by its number.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import message_refs as mr

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def block(src: str, begin: str, end: str) -> str:
    i = src.index(begin)
    return src[i:src.index("\n", src.index(end, i)) + 1]


def fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def const(src: str, name: str) -> str:
    m = re.search(rf"^const {name} = .*$", src, re.M)
    assert m, f"{name} not found"
    return m.group(0)


def node(code: str) -> dict:
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


SESSION_JS = r"""
globalThis.location = new URL('http://hub-host:8765/session?id=room-ctx');
const ROOM = 'room-ctx';
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
const refChipHtml = () => null;
let redraws = 0;
const refChanged = () => { redraws++; };
const agents = [{ identity: 'claude', role: 'engineer' }, { identity: 'codex', role: 'reviewer: reads the diff' }];
const TASKS = {
  '#18': { roomId: 'room-b', no: 18, label: '#18', ref: 'ED-18', title: 'Beta <b>', status: 'running', workflowName: 'In progress', agents, project: 'Ensemble Dashboard' },
  'ED-7': { roomId: 'room-c', no: 7, label: '#7', ref: 'ED-7', title: 'Gamma', status: 'not running', workflowName: 'Done', agents },
};
const fetches = [];
function fetch(url) {
  fetches.push(url);
  const t = TASKS[new URL(url, 'http://h').searchParams.get('ref')];
  return Promise.resolve(t ? { status: 200, ok: true, json: () => Promise.resolve(t) }
                           : { status: 404, ok: false, json: () => Promise.resolve({ error: 'no_such_task' }) });
}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
%s
const lines = %s;
(async () => {
  const text = 'See #18, @codex@18 and @reviewer@ED-7; not `#18` in code, not #99, not page#18, not x#18.';
  const first = mdToHtml(text);
  await settle();
  const second = mdToHtml(text);
  const again = mdToHtml('#18 once more');
  const notTasks = mdToHtml('PR #18 fixes #18, finding #18');
  const asked = fetches.length;
  // A minute on, the task has ended: the chip asks again and redraws once.
  const realNow = Date.now, redrawsBefore = redraws;
  TASKS['#18'] = Object.assign({}, TASKS['#18'], { status: 'not running', workflowName: 'Done' });
  Date.now = () => realNow() + 61000;
  mdToHtml('#18 later');
  await settle();
  const later = mdToHtml('#18 later');
  mdToHtml('#18 later');
  await settle();
  const ttl = { asked: fetches.length - asked, redraws: redraws - redrawsBefore, later };
  Date.now = realNow;
  console.log(JSON.stringify({ first, second, again, notTasks, ttl, fetches: fetches.slice(0, asked), redraws,
    stripped: lines.map(stripRefBlocks),
    report: hubLabel({ kind: 'report', taskTitle: 'Docs', taskId: '#18', reportKind: 'completed' }),
    oldReport: hubLabel({ kind: 'report', taskTitle: 'Docs', taskId: 'room-1a2b3c4d', reportKind: 'completed' }) }));
})();
"""


@unittest.skipUnless(NODE, "node is not installed")
class SessionChips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            block(SESSION, "// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            const(SESSION, "REF_URL_RE"), const(SESSION, "REF_A"), const(SESSION, "REF_MARK_RE"),
            const(SESSION, "REF_BLOCK_RE"), fn(SESSION, "stripRefBlocks"), fn(SESSION, "refOfUrl"),
            block(SESSION, "// ---- Task references: begin", "// ---- Task references: end"),
            fn(SESSION, "mdToHtml"), const(SESSION, "foldShort"),
            SESSION[SESSION.index("const HUB_KIND_LABEL"):SESSION.index("};\n", SESSION.index("const HUB_KIND_LABEL")) + 3],
            fn(SESSION, "hubLabel")])
        task = {"label": "#18", "title": 'Beta "quoted"', "status": "running", "workflowName": "In progress",
                "agents": [{"identity": "claude", "role": "engineer"}], "branch": "sess/b",
                "report": {"kind": "completed", "text": "Merged."}}
        cls.written = mr.task_line("@codex@18", task)
        cls.lines = [f"Look at @codex@18\n\n{cls.written}",
                     f"see #18 and @codex@18\n\n[ref #18] task \"B\"\n\n{cls.written}",
                     "My note\n\n[ref #18] task \"mine\" — typed by me"]
        cls.r = node(SESSION_JS % (src, json.dumps(cls.lines)))

    def test_chips_once_the_hub_has_answered(self):
        self.assertNotIn("task-chip", self.r["first"], "until the hub answers, the text stays")
        self.assertEqual(sorted(self.r["fetches"]), sorted([
            "/api/task/ref?ref=%2318&room=room-ctx", "/api/task/ref?ref=ED-7&room=room-ctx",
            "/api/task/ref?ref=%2399&room=room-ctx"]), "one request per task, in this chat's room")
        self.assertGreaterEqual(self.r["redraws"], 3)
        chips = re.findall(r'<a class="task-chip"[^>]*>.*?</a>', self.r["second"])
        self.assertEqual(len(chips), 3, self.r["second"])
        plain, codex, reviewer = chips
        self.assertIn('href="/session?id=room-b" data-task="room-b"', plain)
        self.assertIn('<span class="ref-who">#18</span><span class="ref-prev">Beta &lt;b&gt;</span>'
                      '<span class="ref-state">In progress · running</span>', plain)
        self.assertIn('href="/session?id=room-b&amp;agent=codex" data-task="room-b" data-agent="codex"', codex)
        self.assertIn('<span class="ref-who">@codex #18</span>', codex)
        self.assertIn('data-task="room-c" data-agent="codex"', reviewer, "a role opens at the agent holding it")
        self.assertIn('<span class="ref-who">@codex ED-7</span>', reviewer)
        self.assertIn('<span class="ref-state">Done</span>', reviewer, "a task that is not running shows only its column")

    def test_what_stays_text(self):
        html = self.r["second"]
        self.assertIn('<code class="ic">#18</code>', html)
        for text in ("not #99", "page#18", "x#18"):
            self.assertIn(text, html)
        self.assertEqual(len(self.r["fetches"]), 3, "a number already asked about is not asked again")
        self.assertIn('class="task-chip"', self.r["again"])
        self.assertNotIn("task-chip", self.r["notTasks"], "PR #18, fixes #18, finding #18 are not tasks")

    def test_a_chip_asks_again_after_a_minute(self):
        ttl = self.r["ttl"]
        self.assertEqual((ttl["asked"], ttl["redraws"]), (1, 1), "once, and a redraw only because it changed")
        self.assertIn('<span class="ref-state">Done</span>', ttl["later"])

    def test_the_hubs_line_is_dropped_from_the_balloon(self):
        self.assertEqual(self.r["stripped"], ["Look at @codex@18", "see #18 and @codex@18", self.lines[2]])

    def test_a_report_row_names_its_task_by_number(self):
        self.assertEqual(self.r["report"], "Completed · task #18 Docs")
        self.assertEqual(self.r["oldReport"], "Completed · task Docs")

    def test_the_page_uses_them(self):
        self.assertIn("s = parkTaskRefs(s, chips);", fn(SESSION, "mdToHtml"))
        self.assertIn("agent: a.dataset.agent", SESSION)
        refresh = fn(SESSION, "refresh")
        self.assertIn("`${tno} · ${room.title}`", refresh, "the tab title")
        self.assertIn("setText($('#tno'), tno);", refresh, "the header")


INDEX_JS = r"""
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const WORKFLOW_COLS = [{ k: 'inprogress', name: 'In progress' }, { k: 'done', name: 'Done' }];
const workflowOf = r => r.workflow || 'done';
let SELECTED_PROJECT = 'p1';
const PROJECTS = { projects: [
  { id: 'p1', key: 'ED', registered: true, sessions: [{ roomId: 'room-a', no: 18 }, { roomId: 'room-b', no: 2 }, { roomId: 'room-po' }] },
  { id: 'p2', key: 'O', registered: true, sessions: [{ roomId: 'room-x', no: 18 }, { roomId: 'room-y', no: 3 }] },
] };
const ALL_ROWS = [
  { roomId: 'room-a', sessionId: 'room-a', no: 18, label: 'Alpha', workflow: 'inprogress' },
  { roomId: 'room-b', sessionId: 'room-b', no: 2, label: 'Beta' },
  { roomId: 'room-x', sessionId: 'room-x', no: 18, label: 'Trading' },
  { roomId: 'room-y', sessionId: 'room-y', no: 3, label: 'Yield' },
  { roomId: '', sessionId: 'abc-123', label: 'An old session' },
];
%s
const out = {
  inProject: taskNoText(ALL_ROWS[0], false), mixed: taskNoText(ALL_ROWS[2], true), none: taskNoText(ALL_ROWS[4], true),
  html: taskNoHtml(ALL_ROWS[0], true),
  rows: ['#18', '18', 'ed-2', 'O-3', 'room-y', '#99', 'ZZ-1', 'nonsense', ''].map(r => taskRowId(r)),
  elsewhere: taskRowId('18', 'p2'),
  link: taskRefLinkHtml('codex', '', 18, 'room-b'),
  keyLink: taskRefLinkHtml('', 'o', 3, 'room-b'),
  nolink: taskRefLinkHtml('', '', 99, 'room-b'),
  hay: rowHaystack(ALL_ROWS[0]),
  refs: [...'see #18 and @codex@O-3, not x#4 or a/#5'.matchAll(TASK_REF_RE)].map(m => m[0]),
};
SELECTED_PROJECT = '';
out.all = taskRowId('#18');
out.allUnique = taskRowId('3');
out.skip = [notTaskRef('', '', 'see PR #4', 7), notTaskRef('', '', 'see #4', 4), notTaskRef('codex', '', 'PR @codex@4', 3)];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class IndexNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([block(INDEX, "// ---- Task numbers: begin", "// ---- Task numbers: end"), fn(INDEX, "rowHaystack")])
        cls.r = node(INDEX_JS % src)

    def test_the_number_and_the_full_form(self):
        self.assertEqual((self.r["inProject"], self.r["mixed"], self.r["none"]), ("#18", "O-18", ""))
        self.assertEqual(self.r["html"], '<span class="tno">ED-18</span>')

    def test_a_link_finds_its_task_on_the_board(self):
        self.assertEqual(self.r["rows"], ["room-a", "room-a", "room-b", "room-y", "room-y", "", "", "", ""])
        self.assertEqual(self.r["elsewhere"], "room-x")
        self.assertEqual(self.r["all"], "", "every project showing: a number two projects have names nothing")
        self.assertEqual(self.r["allUnique"], "room-y", "and a number only one has names its task")
        self.assertEqual(self.r["skip"], [True, False, False])

    def test_a_spec_names_a_task(self):
        self.assertEqual(self.r["link"], '<a href="/?task=room-a" data-task="room-a" data-agent="codex" class="task-link"'
                                         ' title="Open task #18: Alpha">@codex #18 · Alpha · In progress</a>')
        self.assertIn('data-task="room-y"', self.r["keyLink"])
        self.assertIn(">O-3 · Yield · Done</a>", self.r["keyLink"])
        self.assertIsNone(self.r["nolink"])
        self.assertEqual(self.r["refs"], ["#18", "@codex@O-3"])

    def test_search_finds_a_task_by_its_number(self):
        self.assertIn("#18", self.r["hay"])
        self.assertIn("ED-18", self.r["hay"])

    def test_the_page_shows_them(self):
        self.assertIn("taskNoHtml(r, !SELECTED_PROJECT)", fn(INDEX, "cardHtml"))
        self.assertIn("taskNoHtml(r, !SELECTED_PROJECT)", fn(INDEX, "renderHeadlessRow"))
        self.assertIn("taskNoHtml(r, false)", fn(INDEX, "detailHead"))
        self.assertIn("it.ref", fn(INDEX, "needsYouHtml"))
        self.assertIn("it.ref", fn(INDEX, "notifItemHtml"))
        self.assertIn("taskRefLinkHtml(", fn(INDEX, "mdToHtml"))
        self.assertIn("keyBtnHtml(pj)", fn(INDEX, "projectsChromeHtml"))
        self.assertIn("/api/projects/key", fn(INDEX, "keyChoose"))


if __name__ == "__main__":
    unittest.main()
