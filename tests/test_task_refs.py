"""A task named by its number in the pages.

session.html's "Task references" block and its markdown run in Node, with
static/taskcard.js (TaskCard, #152), against a stand-in hub, and check that:

* #18, @codex@18 and @reviewer@ED-7 become chips once the hub has resolved
  them (one request per task, asked in this chat's room): a chip shows the
  number alone (#18, @codex #18, ED-7 as written) and carries the card's words
  (title, column and state, agents, the project when it is another's) in
  data-card; @codex@18 opens the task at that agent, a role at the agent
  holding it;
* a number in code, in a word or a URL, or one that names no task, stays text;
* under a PO's message from another project (pomsg) a bare number is asked in
  that project (&project=) and cached apart from this chat's;
* the line the hub writes under a message (message_refs.task_line) is dropped
  from the balloon, and a person's own line is not;
* a report row names its task by number.

index.html's "Task numbers" block: #18 inside a project and ED-18 where
projects mix, a link's number found on the board, a spec's #18 as a chip (its
card naming another project's task), and search finding a task by its number.

The chip and its card in a browser: tests/test_task_chip_page.py.
Skipped without Node.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import message_refs as mr
import task_numbers as tn

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
ATTACH = (ROOT / "static" / "attach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
TASKCARD = (ROOT / "static" / "taskcard.js").read_text(encoding="utf-8").replace("\r\n", "\n")
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


def chips(h: str) -> list[str]:
    return re.findall(r'<a class="task-chip[^"]*"[^>]*>.*?</a>', h)


def card(chip: str) -> dict:
    m = re.search(r'data-card="([^"]*)"', chip)
    assert m, chip
    return json.loads(html.unescape(m.group(1)))


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
  '#18': { roomId: 'room-b', no: 18, label: '#18', ref: 'ED-18', title: 'Beta <b>', status: 'running', workflowName: 'In progress', agents, project: 'Ensemble Dashboard', inProject: true },
  'ED-7': { roomId: 'room-c', no: 7, label: '#7', ref: 'ED-7', title: 'Gamma', status: 'not running', workflowName: 'Done', agents, project: 'Ensemble Dashboard', inProject: true },
  '#18@proj-dock': { roomId: 'room-d', no: 18, label: '#18', ref: 'D-18', title: 'Dock eighteen', status: 'not running', workflowName: 'In progress', agents, project: 'Dock', inProject: false },
};
// The hub's project list (/api/task/projects): this chat is Ensemble Dashboard's; Dock is called Dock.
const PROJECTS_CTX = { own: 'proj-ed', nouns: ['project', 'projects'], projects: [
  { id: 'proj-ed', key: 'ED', name: 'Ensemble Dashboard', aliases: ['Ensemble Dashboard', 'Ensemble'] },
  { id: 'proj-dock', key: 'D', name: 'Dock', aliases: ['Dock'] } ] };
const fetches = [];
function fetch(url) {
  fetches.push(url);
  if (url.startsWith('/api/task/projects')) return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(PROJECTS_CTX) });
  const q = new URL(url, 'http://h').searchParams;
  const t = TASKS[q.get('ref') + (q.get('project') ? '@' + q.get('project') : '')];
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
  const notTasks = mdToHtml('PR #18, issue #18, finding #18, [Image #18]');
  const asked = fetches.length;
  // A PO's message from another project: its #18 is that project's task.
  TASK_REF_PID = 'proj-dock';
  mdToHtml('#18 by the Dock PO');
  await settle();
  const dock = mdToHtml('#18 by the Dock PO');
  TASK_REF_PID = '';
  const home = mdToHtml('#18 by the Dock PO');
  const dockAsked = fetches.slice(asked);
  const projects = [taskRefProject({ kind: 'pomsg', fromProjectId: 'proj-dock' }), taskRefProject({ kind: 'human', fromProjectId: 'proj-dock' }), taskRefProject({ from: 'user' })];
  // #156: a bare number right after another project's name is that project's;
  // one with the name earlier in its sentence stays this chat's, and when
  // Dock has the number too its card says the project was assumed.
  const namedBefore = fetches.length;
  const namedText = "Dock released #18 as v0.11.0. Answered Dock about #18, and Dock's #7 is nobody's. Plain #18 again.";
  mdToHtml(namedText);
  await settle();
  const named = mdToHtml(namedText);
  await settle();                 // Dock's #7 asked, then ours: both answered before the next count
  const namedAsked = fetches.slice(namedBefore);
  const preview = edRefsHtml('Dock #18 and #18');
  // A minute on, the task has ended: the chip asks again and redraws once.
  const realNow = Date.now, redrawsBefore = redraws, askedBefore = fetches.length;
  TASKS['#18'] = Object.assign({}, TASKS['#18'], { status: 'not running', workflowName: 'Done' });
  Date.now = () => realNow() + 61000;
  mdToHtml('#18 later');
  await settle();
  const later = mdToHtml('#18 later');
  mdToHtml('#18 later');
  await settle();
  const ttl = { asked: fetches.length - askedBefore, redraws: redraws - redrawsBefore, later };
  Date.now = realNow;
  console.log(JSON.stringify({ first, second, again, notTasks, ttl, fetches: fetches.slice(0, asked), redraws, dock, home, dockAsked, projects, named, namedAsked, preview,
    stripped: lines.map(stripRefBlocks),
    report: [hubLabel, senderName].map(f => f({ from: 'user', kind: 'report', taskTitle: 'Docs', taskId: '#18', reporter: 'claude', reportKind: 'completed' })),
    oldReport: [hubLabel, senderName].map(f => f({ from: 'user', kind: 'report', taskTitle: 'Docs', taskId: 'room-1a2b3c4d', reporter: 'claude', reportKind: 'completed' })) }));
})();
"""


@unittest.skipUnless(NODE, "node is not installed")
class SessionChips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            TASKCARD, ATTACH, "const parkPointRefs = s => s;",
            block(SESSION, "// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            const(SESSION, "REF_URL_RE"), const(SESSION, "REF_A"), const(SESSION, "REF_MARK_RE"),
            const(SESSION, "REF_BLOCK_RE"), fn(SESSION, "stripRefBlocks"), fn(SESSION, "refOfUrl"),
            block(SESSION, "// ---- Task references: begin", "// ---- Task references: end"),
            block(SESSION, "// ---- Numbered points: begin", "// ---- Numbered points: end"),
            block(SESSION, "// ---- PO message names: begin", "// ---- PO message names: end"),
            fn(SESSION, "mdToHtml"), fn(SESSION, "itemsHtml"), fn(SESSION, "edRefsHtml"),
            SESSION[SESSION.index("const HUB_KIND_LABEL"):SESSION.index("};\n", SESSION.index("const HUB_KIND_LABEL")) + 3],
            SESSION[SESSION.index("const isHubInput"):SESSION.index("// The chip on an agent's balloon")],
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
            "/api/task/projects?room=room-ctx",
            "/api/task/ref?ref=%2318&room=room-ctx", "/api/task/ref?ref=ED-7&room=room-ctx",
            "/api/task/ref?ref=%2399&room=room-ctx"]), "one request per task, in this chat's room, and the project list once")
        self.assertGreaterEqual(self.r["redraws"], 3)
        found = chips(self.r["second"])
        self.assertEqual(len(found), 3, self.r["second"])
        plain, codex, reviewer = found
        # The chip: the number alone, a dot while the task runs, the card's words in data-card.
        self.assertIn('class="task-chip tc-run" href="/session?id=room-b" data-task="room-b"', plain)
        self.assertIn('aria-haspopup="dialog" aria-expanded="false"', plain)
        self.assertTrue(plain.endswith('<span class="tc-dot" aria-hidden="true"></span><span class="ref-who">#18</span></a>'), plain)
        self.assertNotIn("Beta", plain.split("data-card")[0], "the title is not inline")
        self.assertEqual(card(plain), {"ref": "#18", "title": "Beta <b>", "state": "In progress · running", "agents": ["claude", "codex"],
                                       "project": "", "href": "/session?id=room-b", "task": "room-b", "agent": ""})
        self.assertIn('href="/session?id=room-b&amp;agent=codex" data-task="room-b" data-agent="codex"', codex)
        self.assertIn('<span class="ref-who">@codex #18</span>', codex)
        self.assertEqual(card(codex)["agent"], "codex")
        self.assertIn('data-task="room-c" data-agent="codex"', reviewer, "a role opens at the agent holding it")
        self.assertIn('<span class="ref-who">@codex ED-7</span>', reviewer, "as written")
        self.assertNotIn("tc-dot", reviewer, "no dot on a task that is not running")
        self.assertEqual(card(reviewer)["state"], "Done", "a task that is not running shows only its column")

    def test_what_stays_text(self):
        html_ = self.r["second"]
        self.assertIn('<code class="ic">#18</code>', html_)
        for text in ("not #99", "page#18", "x#18"):
            self.assertIn(text, html_)
        self.assertEqual(len(self.r["fetches"]), 4, "a number already asked about is not asked again")
        self.assertIn('class="task-chip', self.r["again"])
        self.assertNotIn("task-chip", self.r["notTasks"], "PR #18, issue #18, finding #18 and an image placeholder are not tasks")

    def test_a_pos_message_from_another_project_reads_its_numbers_there(self):
        self.assertEqual(self.r["projects"], ["proj-dock", "", ""], "only a PO's message (pomsg) carries its project")
        self.assertEqual(self.r["dockAsked"], ["/api/task/ref?ref=%2318&room=room-ctx&project=proj-dock"], "asked once, in that project")
        dock = chips(self.r["dock"])
        self.assertEqual(len(dock), 1, self.r["dock"])
        self.assertIn('data-task="room-d"', dock[0])
        self.assertIn('<span class="ref-who">D-18</span>', dock[0], "another project's task reads in full")
        self.assertEqual((card(dock[0])["project"], card(dock[0])["ref"]), ("Dock", "D-18"), "and its card names the project")
        home = chips(self.r["home"])
        self.assertIn('data-task="room-b"', home[0], "the same words in this chat's own context: this project's #18, from its own cache")
        self.assertIn('<span class="ref-who">#18</span>', home[0])

    def test_a_chip_asks_again_after_a_minute(self):
        ttl = self.r["ttl"]
        self.assertEqual((ttl["asked"], ttl["redraws"]), (1, 1), "once, and a redraw only because it changed")
        later = chips(ttl["later"])
        self.assertEqual(card(later[0])["state"], "Done")
        self.assertNotIn("tc-dot", later[0])

    def test_the_hubs_line_is_dropped_from_the_balloon(self):
        self.assertEqual(self.r["stripped"], ["Look at @codex@18", "see #18 and @codex@18", self.lines[2]])

    def test_a_report_row_names_its_task_by_number(self):
        self.assertEqual(self.r["report"], ["Completed", "#18 claude"])
        # A room id the page has no number for yet: its short id.
        self.assertEqual(self.r["oldReport"], ["Completed", "1a2b3c4d claude"])

    def test_the_page_uses_them(self):
        self.assertIn("s = parkTaskRefs(s, chips);", fn(SESSION, "mdToHtml"))
        self.assertIn("agent: a.dataset.agent", SESSION)
        self.assertIn('<script src="/static/taskcard.js"></script>', SESSION)
        self.assertIn("TaskCard.init({ open: openTaskFrom, openPoint:", SESSION)
        self.assertIn("TASK_REF_PID = taskRefProject(m);", fn(SESSION, "renderBubbles"), "each balloon is drawn in its own project")
        self.assertIn("TaskCard.refsIn(text, taskRefCtx())", fn(SESSION, "edRefsHtml"), "the box preview reads the words the same way")
        self.assertIn("const h = taskChipHtml(r);", fn(SESSION, "edRefsHtml"), "the box preview shows the chips too")
        refresh = fn(SESSION, "refreshRoom")
        self.assertIn("`${tno} · ${room.title}`", refresh, "the tab title")
        self.assertIn("setText($('#tno'), tno);", refresh, "the header")

    def test_a_number_after_another_projects_name_is_that_projects(self):
        # #156: "Dock released #18" is Dock's #18, asked in Dock; "Answered Dock
        # about #18" is this chat's, and as Dock has an #18 too the card says
        # this project was assumed; "Dock's #7" names no task in Dock and
        # none here: text. A plain #18 is this chat's, from its own cache.
        found = chips(self.r["named"])
        self.assertEqual(len(found), 3, self.r["named"])
        released, about, plain = found
        self.assertIn('data-task="room-d"', released)
        self.assertIn('<span class="ref-who">D-18</span>', released, "another project's task reads in full")
        self.assertEqual((card(released)["project"], card(released).get("assumed")), ("Dock", None))
        self.assertIn('data-task="room-b"', about, "a name earlier in the sentence does not move the number")
        self.assertIn('<span class="ref-who">#18</span>', about)
        self.assertEqual((card(about)["project"], card(about)["assumed"]), ("Ensemble Dashboard", True), "its card says which project was assumed")
        self.assertIn("assumed Ensemble Dashboard", html.unescape(about))
        self.assertIn('data-task="room-b"', plain)
        self.assertEqual(card(plain)["project"], "", "a plain number: nothing to say")
        self.assertEqual(sorted(self.r["namedAsked"]), ["/api/task/ref?ref=%237&room=room-ctx", "/api/task/ref?ref=%237&room=room-ctx&project=proj-dock"],
                         "Dock's #18 was asked before (the PO message); #7 is asked in Dock, then here")
        preview = chips(self.r["preview"])
        self.assertEqual([re.search(r'data-task="([^"]*)"', c).group(1) for c in preview], ["room-d", "room-b"], "the box preview: one chip per task, Dock's and ours")

    def test_the_page_reads_names_as_the_hub_does(self):
        # The same texts through task_numbers.all_text_refs (the hub) and
        # TaskCard.refsIn (the pages) give the same references, projects and
        # weak names: the rule lives in two languages and must not drift.
        projects = tn.project_names(
            [{"id": "dock", "key": "D", "name": "Dock"}, {"id": "ed", "key": "ED", "name": "Ensemble Dashboard"},
             {"id": "op", "key": "OP", "name": "OPtionTradingENgine"}, {"id": "st", "key": "S", "name": "Strats"}],
            {"dock": "Dock PO", "ed": "claude-dashboard windows port iterm2 to windows terminal", "op": "opten",
             "st": "Deeper understanding of my option trading strategies"})
        ctx = {"projects": projects, "own": "op", "nouns": ["initiative", "initiatives"]}
        cases = [
            "Re P230: no wait was needed. Dock released #27 as v0.11.0 while our task was running, so it's in.",
            "Dock's #27 (`setTitle` and `bodyAttrs`) is running in the Dock project.",
            "Opten PO is posting something like this \nDock released \n#27\n27. Amend a tranche by hand\nDone\n as v0.11.0 \n\nby the 27 resolve wrong",
            "#27 is ours. Dock is next.", "in Dock: #27 and ED-3", "project Dock #27", "the Dock initiative’s #27",
            "Dock released #27 so opTen can upgrade.", "- Dock v0.11.0 is out\n- #27 is next", "PR #27 in Dock", "`Dock #27` in code",
            "#fff and #112233 are colours; ## 27 heading", "| Dock | #27 | Strats #4 |", "Dock PO's @codex@27 and @reviewer@ED-2",
            "ensemble dashboard #5! Strats? #6", "see page#27 and &#27; and x-#27 and D-27 and #D-27 and #ZZ-9",
            "```\nDock #1\n```\nDock #2 after the fence. Then #3 (Ensemble).", "Answered Ensemble about #11",
            "Ensemble's 8 needs (#4) has 6 new commits", "Dock **v0.11.0** (#27) was tagged", "Dock v0.11.0 with #27 is tagged",
            "Console on Dock v0.3.3 (#98) has new commits", "You don't have to wait for Dock: #100 already works",
            "Dock task #27 and the Dock project's #28; told Strats that #9 is done. Then #3?", "Ensemble and Dock: #5",
        ]
        keys = ("token", "who", "key", "no", "start", "end", "project", "how", "others")
        py = [[{k: r[k] for k in keys} for r in tn.all_text_refs(c, ctx)] for c in cases]
        got = node(TASKCARD + "\nconst [cases, ctx] = %s;\nconsole.log(JSON.stringify(cases.map(c => TaskCard.refsIn(c, ctx))));\n"
                   % json.dumps([cases, ctx]))
        self.assertEqual(got, py)
        # What the rule says, pinned: the real sentence is Dock's; a name earlier in the sentence is only a weak one.
        self.assertEqual([(r["token"], r["project"], r["how"], r["others"]) for r in py[0]], [("#27", "dock", "name", [])])
        self.assertEqual([(r["project"], r["how"]) for r in py[2]], [("dock", "name")], "the CEO's paste: the name right before, a line break between")
        self.assertEqual([(r["project"], r["how"], r["others"]) for r in py[17]], [("op", "", ["ed"])])
        self.assertEqual([(r["project"], r["how"], r["others"]) for r in py[18]], [("op", "", ["ed"])], "a title before a bracketed number")
        self.assertEqual([r["project"] for r in py[23]], ["dock", "dock", "op", "op"])
        self.assertEqual(py[23][2]["others"], ["dock", "st"])


INDEX_JS = r"""
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const WORKFLOW_COLS = [{ k: 'inprogress', name: 'In progress' }, { k: 'done', name: 'Done' }];
const workflowOf = r => r.workflow || 'done';
let SELECTED_PROJECT = 'p1';
const PROJECTS = { projects: [
  { id: 'p1', key: 'ED', name: 'Ensemble Dashboard', registered: true, sessions: [{ roomId: 'room-a', no: 18 }, { roomId: 'room-b', no: 2 }, { roomId: 'room-po' }] },
  { id: 'p2', key: 'O', name: 'Opten', registered: true, sessions: [{ roomId: 'room-x', no: 18 }, { roomId: 'room-y', no: 3 }] },
] };
const ALL_ROWS = [
  { roomId: 'room-a', sessionId: 'room-a', no: 18, label: 'Alpha', workflow: 'inprogress', isLive: true, status: 'busy', members: [{ identity: 'claude', agent: 'claude', role: 'engineer' }, { identity: 'codex', agent: 'codex' }] },
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
  fromOther: taskRefLinkHtml('', '', 18, 'room-y'),
  words: taskChipsIn('fix #18 & #2, not PR #18', 'room-b'),
  hay: rowHaystack(ALL_ROWS[0]),
  refs: [...'see #18 and @codex@O-3, not x#4 or a/#5'.matchAll(TASK_REF_RE)].map(m => m[0]),
};
SELECTED_PROJECT = '';
out.all = taskRowId('#18');
out.allUnique = taskRowId('3');
out.skip = [notTaskRef('', '', 'see PR #4', 7), notTaskRef('', '', 'see #4', 4), notTaskRef('codex', '', 'PR @codex@4', 3), notTaskRef('', '', '[Image #4]', 7)];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class IndexNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([TASKCARD, block(INDEX, "// ---- Task numbers: begin", "// ---- Task numbers: end"), fn(INDEX, "rowHaystack")])
        cls.r = node(INDEX_JS % src)

    def test_the_number_and_the_full_form(self):
        self.assertEqual((self.r["inProject"], self.r["mixed"], self.r["none"]), ("#18", "O-18", ""))
        self.assertEqual(self.r["html"], '<span class="tno">ED-18</span>')

    def test_a_link_finds_its_task_on_the_board(self):
        self.assertEqual(self.r["rows"], ["room-a", "room-a", "room-b", "room-y", "room-y", "", "", "", ""])
        self.assertEqual(self.r["elsewhere"], "room-x")
        self.assertEqual(self.r["all"], "", "every project showing: a number two projects have names nothing")
        self.assertEqual(self.r["allUnique"], "room-y", "and a number only one has names its task")
        self.assertEqual(self.r["skip"], [True, False, False, True])

    def test_a_spec_names_a_task(self):
        link = self.r["link"]
        self.assertTrue(link.startswith('<a class="task-chip tc-run" href="/?task=room-a" data-task="room-a" data-agent="codex" data-card="'), link)
        self.assertTrue(link.endswith('<span class="ref-who">@codex #18</span></a>'), link)
        self.assertEqual(card(link), {"ref": "#18", "title": "Alpha", "state": "In progress · running", "agents": ["claude", "codex"],
                                      "project": "", "href": "/?task=room-a", "task": "room-a", "agent": "codex"})
        key = self.r["keyLink"]
        self.assertIn('href="/?task=room-y" data-task="room-y"', key)
        self.assertIn('<span class="ref-who">O-3</span>', key)
        self.assertEqual((card(key)["project"], card(key)["state"]), ("Opten", "Done"), "another project's task: its card names the project")
        self.assertIsNone(self.r["nolink"])
        other = self.r["fromOther"]
        self.assertIn('data-task="room-x"', other, "a text of a task in another project reads #18 in that project")
        self.assertIn('<span class="ref-who">#18</span>', other)
        self.assertEqual(card(other)["project"], "", "which is its home")
        self.assertEqual(len(chips(self.r["words"])), 2, self.r["words"])
        self.assertIn("not PR #18", self.r["words"])
        self.assertEqual(self.r["refs"], ["#18", "@codex@O-3"])

    def test_search_finds_a_task_by_its_number(self):
        self.assertIn("#18", self.r["hay"])
        self.assertIn("ED-18", self.r["hay"])

    def test_the_page_shows_them(self):
        self.assertIn("taskNoHtml(r, !SELECTED_PROJECT)", fn(INDEX, "cardHtml"))
        self.assertIn("taskNoHtml(r, !SELECTED_PROJECT)", fn(INDEX, "renderHeadlessRow"))
        self.assertIn("taskNoHtml(r, false)", fn(INDEX, "detailHead"))
        self.assertIn("it.ref", fn(INDEX, "swRowHtml"), "Needs you names a task by its key (#135)")
        self.assertIn("taskRefLinkHtml(", fn(INDEX, "mdToHtml"))
        self.assertIn("taskChipsIn(", INDEX[INDEX.index("function pdPointsHtml"):], "Your asks' words show the chips")
        self.assertIn('<script src="/static/taskcard.js"></script>', INDEX)
        self.assertIn("TaskCard.init({ open: a => openTaskLink(a.dataset.task, a.dataset.agent) });", INDEX)
        # A panel popped out into a window gets the card there: init on the
        # window's document where the window is set up (onEveryWindow), after
        # the page's listeners are mirrored into it.
        pop = INDEX[INDEX.index("lib.onEveryWindow(w => {"):]
        pop = pop[:pop.index("return () => {")]
        self.assertIn("TaskCard.init({ doc: d, open: a => openTaskLink(a.dataset.task, a.dataset.agent) });", pop)
        self.assertLess(pop.index("PD_POP_DOCS.set(d, ls);"), pop.index("TaskCard.init({ doc: d"))
        self.assertNotIn("pdCardsInWindows", INDEX)
        # The home bar's Panels menu is painted when the list dock arrives, not at
        # the next poll: the dock library may land after the first render (the
        # extra script in the head made test_top_bar's home probe miss it).
        self.assertIn("barPaint();", fn(INDEX, "ldMake"))
        self.assertIn("barPaint();", fn(INDEX, "ldDrop"))
        self.assertIn("key-btn", fn(INDEX, "projMenuSettingsHtml"))
        self.assertIn("/api/projects/key", fn(INDEX, "keyChoose"))


if __name__ == "__main__":
    unittest.main()
