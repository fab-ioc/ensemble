"""A project's Workspace leads with its documents; its Changes list what landed.

index.html's Documents node and the project Changes tab's "Landed on main"
list are pure functions, run here in Node (skipped without it):

* the Documents node, first in a project's tree (never a task's), open by
  default: the roadmap first in every state (loading, unreadable, empty,
  listed), then the documents newest first as the hub sends them, each a file
  row with its title, its task's number (or "docs" for the code's) and date,
  the rest in its tooltip; empty says what would fill it, a capped list says
  so; closed it is its heading alone;
* the find box finds them: Go to file ranks the documents outside the listed
  folder with its files, and a text search of the Documents folder is merged
  with the root's, the documents first;
* "Show task folders": a code project's tree leaves its tasks' folders out of
  its home until the switch is on; other folders are untouched;
* Landed on main: each commit's task chip, subject without "Merge #NN: ", its
  files (the first 8, then "+N more"), the file showing marked by file and
  commit, and an empty main line says what would fill it.

It also checks the page wires them: the node is first in the tree and opens
and closes, the roadmap's tab is its own view, the switch sits above the tree,
a commit's file diff asks for that commit and has a review of its own.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_documents_project import INDEX, NODE, js_function


def block(begin: str, end: str) -> str:
    i = INDEX.index(begin)
    return INDEX[i:INDEX.index(end, i)]


JS = r"""
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tildeify = p => p;
const agoSpan = ts => `<span data-ago="${ts}"></span>`;
let SHOWN = false;
const docsTasksShown = () => SHOWN;
const PROJECTS = { 'p1': { id: 'p1', path: 'C:\\code', home: 'C:\\home', key: 'ED' } };
const projectById = id => PROJECTS[id] || null;
const RM = new Map([['p1', { data: { exists: true, mtime: 1790000000 } }]]);
const rmState = id => RM.get(id);
const wsDocsProjectOf = () => null;
const docsInTask = () => false;
const histFolder = p => { const i = p.lastIndexOf('/'); return i > 0 ? p.slice(0, i) : ''; };
%(deps)s
%(find)s
%(docs)s
%(changes)s
const out = {};

// The Documents node.
const pv = { ctx: { kind: 'project', projectId: 'p1' }, docsOpen: true, mark: '', sel: '' };
const dir = 'C:\\home\\Documents';
out.loading = wsDocsNodeHtml(pv, null, null);
out.failed = wsDocsNodeHtml(pv, { data: null, err: '500 boom' }, { exists: false, mtime: 0 });
out.empty = wsDocsNodeHtml(pv, { data: { dir, items: [], key: 'ED' } }, { exists: true, mtime: 1790000000 });
const items = [
  { path: 'C:\\home\\Documents\\#40 Design\\notes.md', rel: '#40 Design/notes.md', title: 'notes', no: 40, group: 'Design', mtime: 1790000000, size: 2048, inCode: false },
  { path: 'C:\\code\\docs\\arch.md', rel: 'docs/arch.md', title: 'arch', no: null, group: '', mtime: 1780000000, size: 10, inCode: true },
  { path: 'C:\\home\\Documents\\Loose.md', rel: 'Loose.md', title: 'Loose <b>', no: null, group: '', mtime: 1770000000, size: 5, inCode: false },
];
out.list = wsDocsNodeHtml({ ...pv, sel: items[0].path }, { data: { dir, codeDocs: 'C:\\code\\docs', items, key: 'ED', truncated: false } }, { exists: true, mtime: 1790000000 });
out.capped = wsDocsNodeHtml(pv, { data: { dir, items: items.slice(0, 1), key: '', truncated: true } }, null);
out.closed = wsDocsNodeHtml({ ...pv, docsOpen: false }, { data: { dir, items } }, null);
out.task = wsDocsNodeHtml({ ctx: { kind: 'task', sid: 's' }, docsOpen: true }, { data: { dir, items } }, null);
out.roadmap = [wsRoadmapPath(pv), wsIsRoadmap(pv, 'c:/home/roadmap.md'), wsIsRoadmap(pv, 'C:\\home\\a.md'), wsIsRoadmap({ ctx: { kind: 'task', sid: 's' } }, 'C:\\home\\ROADMAP.md')];
// First in the tree, above the code folders.
const tree = { ...pv, roots: [{ path: 'C:\\code', label: 'Motors', kind: 'project' }, { path: 'C:\\home', label: 'Tasks & notes', kind: 'home' }],
  open: new Set(), dirs: new Map(), docsList: { data: { dir, items, exists: true } } };
out.tree = wsTreeHtml(tree);
out.rm = wsDocsRm(pv);
// Go to file and Text search.
out.has = [wsDocsHas(tree, items[0].path), wsDocsHas(tree, 'C:\\home\\ROADMAP.md'), wsDocsHas(tree, 'C:\\home\\x.md')];
out.pool = wsDocsFindPool(tree, 'C:\\code');   // the code elsewhere: the roadmap and the home's documents
out.poolIn = wsDocsFindPool(tree, 'C:\\home');  // all under the listed folder: nothing more
out.ranked = wsFuzzyRank('design', out.pool.map(x => x.path), 10).items.map(x => x.path);
out.searchDir = [wsDocsSearchDir(tree, 'C:\\code'), wsDocsSearchDir(tree, 'C:\\home'), wsDocsSearchDir({ ...tree, docsList: { data: { dir, items, exists: false } } }, 'C:\\code')];
out.merged = wsfMerge({ root: 'C:\\code', files: [{ path: 'a.py', matches: [{ line: 1, ranges: [[0, 1]] }] }], matches: 1, filesSearched: 10, limit: 1000, skipped: { binary: 1 } },
  { files: [{ path: '#40 Design/notes.md', matches: [{ line: 2, ranges: [[0, 1]] }, { line: 3, ranges: [[0, 1]] }] }], matches: 2, filesSearched: 3, truncated: true, skipped: { binary: 2, large: 1 } }, dir);
out.mergedHits = wsHitRows(out.merged).hits.map(h => [h.path, h.abs || '']);

// Show task folders.
const listing = { entries: [
  { name: 'Documents', type: 'dir' }, { name: 'fix-it', type: 'dir', task: true },
  { name: 'ROADMAP.md', type: 'file', size: 3 }] };
const tv = { ctx: { kind: 'project', projectId: 'p1' }, open: new Set(), dirs: new Map([['C:\\home', listing], ['C:\\code', listing]]), roots: [], mark: '', sel: '' };
out.hiddenHome = wsRowsHtml(tv, 'C:\\home');
out.otherRoot = wsRowsHtml(tv, 'C:\\code');
tv.dirs.set('C:\\home\\only', { entries: [{ name: 't', type: 'dir', task: true }] });
out.onlyTasks = wsRowsHtml(tv, 'C:\\home');
const only = { ...tv, dirs: new Map([['C:\\home', { entries: [{ name: 't', type: 'dir', task: true }] }]]) };
out.onlyTasks = wsRowsHtml(only, 'C:\\home');
SHOWN = true;
out.shownHome = wsRowsHtml(tv, 'C:\\home');

// Landed on main.
const files = n => Array.from({ length: n }, (_, i) => ({ path: 'f' + i + '.py', status: i ? 'M' : 'A' }));
const log = { ref: 'main', commits: [
  { sha: 'a'.repeat(40), time: 1790000000, author: 'fab', subject: 'Merge #90: hand an owner over', no: 90, files: files(10), more: 0 },
  { sha: 'b'.repeat(40), time: 1780000000, author: 'fab', subject: 'Tidy up', no: null, files: files(1), more: 0 },
] };
out.log = chLogHtml(log, 'ED', new Set(), { file: 'f0.py', sha: 'b'.repeat(40) });
out.logOpen = chLogHtml(log, 'ED', new Set(['a'.repeat(40)]), { file: '', sha: '' });
out.logEmpty = chLogHtml({ ref: 'HEAD', commits: [] }, 'ED', new Set(), {});
out.subject = [chSubject('Merge #90: hand over'), chSubject('Merge branch x'), chSubject('Merge #5:')];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class ThePureParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        deps = "\n".join(js_function(n) for n in (
            "wsNorm", "wsSame", "wsJoin", "wsFmtSize", "wsTabName", "docsApart", "wsHidden", "wsRowsHtml", "wsTreeHtml"))
        src = JS % {
            "deps": deps,
            "find": block("// ---- Workspace find: begin", "// ---- Workspace find: end"),
            "docs": block("// ---- Workspace Documents: begin", "// ---- Workspace Documents: end"),
            "changes": "const CH_FILES_SHOWN = 8;\n" + block("// ---- Changes, landed on main: begin", "// ---- Changes, landed on main: end"),
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.js"
            p.write_text(src, encoding="utf-8")
            proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.o = json.loads(proc.stdout)

    def test_the_roadmap_is_first_in_every_state(self):
        o = self.o
        for k in ("loading", "failed", "empty", "list", "capped"):
            body = o[k][o[k].index('<div class="wsk" data-docs="1">'):]
            first = re.search(r'<div class="wse[^"]*"[^>]*>', body[len('<div class="wsk" data-docs="1">'):]).group(0)
            self.assertIn('data-roadmap="1"', first, k)
            self.assertIn('data-path="C:\\home\\ROADMAP.md"', first, k)
        self.assertIn('<span class="nm">Roadmap</span>', o["list"])
        self.assertIn("not written yet", o["failed"])
        self.assertRegex(o["empty"], r'data-roadmap="1" title="ROADMAP.md, always first[^"]* · saved [^"]+"')
        self.assertEqual(o["roadmap"], ["C:\\home\\ROADMAP.md", True, False, False])
        self.assertEqual(o["rm"], {"exists": True, "mtime": 1790000000})

    def test_loading_failed_and_empty_say_what_they_are(self):
        o = self.o
        self.assertIn("Loading the documents…", o["loading"])
        self.assertIn("The documents could not be read: 500 boom", o["failed"])
        self.assertIn("No documents yet", o["empty"])
        self.assertIn("C:\\home\\Documents", o["empty"], "says where they go")
        self.assertIn("“#12 Title.md”", o["empty"], "and how they are named")

    def test_rows_in_the_order_given_with_task_date_and_tooltip(self):
        h = self.o["list"]
        self.assertIn('<div class="wse dir wsroot documents" data-docs="1"', h)
        self.assertIn('<span class="tw">▾</span><span class="nm">Documents</span>', h, "open")
        rows = re.findall(r'<div class="wse file doc[^"]*" data-path="([^"]+)"', h)
        self.assertEqual(rows, ["C:\\home\\ROADMAP.md", "C:\\home\\Documents\\#40 Design\\notes.md", "C:\\code\\docs\\arch.md",
                                "C:\\home\\Documents\\Loose.md"], "the roadmap, then newest first as sent")
        first = re.search(r'<div class="wse file doc[^"]*" data-path="C:\\home\\Documents\\#40[^>]*>.*?</div>', h).group(0)
        self.assertIn("wse file doc on", first, "the file showing is marked")
        self.assertIn('<span class="nm">notes</span><span class="wse-tag">#40</span>', first)
        self.assertRegex(first, r'title="#40 Design/notes.md · from task #40 · [^"]+ · 2.0 KB"')
        self.assertIn('<span class="wse-tag">docs</span>', h, "a code document says where it is")
        self.assertIn("Loose &lt;b&gt;", h, "titles are escaped")
        self.assertNotIn("<a ", h, "a tree row is one thing to click")

    def test_a_capped_list_says_so(self):
        self.assertIn("The 1 newest only; the rest are in C:\\home\\Documents.", self.o["capped"])

    def test_closed_it_is_its_heading_and_a_task_has_none(self):
        self.assertIn('<span class="tw">▸</span><span class="nm">Documents</span>', self.o["closed"])
        self.assertIn('<div class="wsk" data-docs="1" hidden></div>', self.o["closed"])
        self.assertNotIn("data-path", self.o["closed"])
        self.assertEqual(self.o["task"], "")

    def test_it_is_the_first_node_of_the_tree(self):
        t = self.o["tree"]
        self.assertTrue(t.startswith('<div class="wse dir wsroot documents" data-docs="1"'), t[:200])
        self.assertLess(t.index('data-docs="1"'), t.index('data-path="C:\\code"'), "above the code folders")

    def test_the_find_box_finds_the_documents(self):
        o = self.o
        self.assertEqual(o["has"], [True, True, False])
        self.assertEqual([x["path"] for x in o["pool"]], ["Documents/ROADMAP.md", "Documents/#40 Design/notes.md", "Documents/Loose.md"],
                         "the roadmap and the home's documents; the code's are in the listed folder already")
        self.assertEqual(o["pool"][1]["abs"], "C:\\home\\Documents\\#40 Design\\notes.md")
        self.assertEqual(o["poolIn"], [{"abs": "C:\\code\\docs\\arch.md", "path": "Documents/docs/arch.md"}],
                         "under the listed folder they are found there; only the code's is outside it")
        self.assertEqual(o["ranked"], ["Documents/#40 Design/notes.md"], "a query narrows them")
        self.assertEqual(o["searchDir"], ["C:\\home\\Documents", "", ""])
        m = o["merged"]
        self.assertEqual([f["path"] for f in m["files"]], ["Documents/#40 Design/notes.md", "a.py"], "the documents first")
        self.assertEqual((m["matches"], m["filesSearched"], m["truncated"], m["skipped"]), (3, 13, True, {"binary": 3, "large": 1}))
        self.assertEqual(m["root"], "C:\\code")
        self.assertEqual(o["mergedHits"], [["Documents/#40 Design/notes.md", "C:\\home\\Documents\\#40 Design\\notes.md"]] * 2 + [["a.py", ""]])

    def test_task_folders_are_hidden_in_the_home_until_shown(self):
        o = self.o
        self.assertIn('data-path="C:\\home\\Documents"', o["hiddenHome"])
        self.assertIn('ROADMAP.md', o["hiddenHome"])
        self.assertNotIn("fix-it", o["hiddenHome"])
        self.assertIn("fix-it", o["otherRoot"], "only the home's task folders are left out")
        self.assertIn("fix-it", o["shownHome"])
        self.assertIn("Only task folders here: “Show task folders” shows them.", o["onlyTasks"])

    def test_landed_on_main(self):
        h = self.o["log"]
        self.assertIn(">Landed on main</h4>", h)
        commits = re.findall(r'<div class="chc".*?(?=<div class="chc"|$)', h, re.S)
        self.assertEqual(len(commits), 2)
        a = commits[0]
        self.assertIn('<a class="tno" href="#" data-task="ED-90"', a)
        self.assertIn(">hand an owner over</span>", a)
        self.assertIn('title="Merge #90: hand an owner over · aaaaaaaaaa · fab"', a)
        self.assertEqual(a.count('class="chf'), 8, "the first 8 files")
        self.assertIn('>+2 more</button>', a)
        self.assertIn('data-file="f0.py" data-commit="' + "a" * 40 + '"', a)
        self.assertNotIn('chf on', a, "the same file of another commit is not the one showing")
        b = commits[1]
        self.assertNotIn("data-task", b)
        self.assertIn('<div class="chf on" data-file="f0.py" data-commit="' + "b" * 40 + '"', b)
        opened = re.findall(r'<div class="chc".*?(?=<div class="chc"|$)', self.o["logOpen"], re.S)[0]
        self.assertEqual(opened.count('class="chf'), 10)
        self.assertNotIn("chc-more", opened)
        self.assertIn(">Landed on the main line</h4>", self.o["logEmpty"])
        self.assertIn("Nothing has landed yet", self.o["logEmpty"])
        self.assertEqual(self.o["subject"], ["hand over", "Merge branch x", "Merge #5:"])


SELECT_JS = r"""
// One box, three reviews: commit A's diff, then B's, then A's again. The rows on
// screen are A's second drawing; B's view still holds the same box.
const row = i => { const el = { nodeType: 1, dataset: { i: String(i) } }; el.closest = () => el; return el; };
const box = { contains: () => true };
const draw = () => [row(0), row(1), row(2)];
const viewA1 = { box, els: draw(), rows: [] }, viewB = { box, els: draw(), rows: [] }, viewA2 = { box, els: draw(), rows: [] };
const DR_REVIEWS = new Map([['A', { view: viewA2 }], ['B', { view: viewB }]]);
viewA1.rv = null;
const drRange = () => ({ rows: [1] });
let painted = null;
const drPaint = v => { painted = v; };
let btn = null;
const document = { createElement: () => (btn = { style: {}, addEventListener() {}, remove() {} }), body: { appendChild() {} },
  getSelection: () => window.getSelection(), get defaultView() { return window; } };   // the selection is its document's
const window = { innerHeight: 800, innerWidth: 1200,
  getSelection: () => ({ isCollapsed: false, rangeCount: 1, removeAllRanges() {},
    getRangeAt: () => ({ startContainer: viewA2.els[0], endContainer: viewA2.els[2], endOffset: 1, getBoundingClientRect: () => ({ bottom: 10, left: 10 }) }) }) };
let DR_SELBTN = null;
%(fns)s
drOnSelect();
btn.onclick();
console.log(JSON.stringify({ paintedA2: painted === viewA2, draft: viewA2.draft ? [viewA2.draft.a, viewA2.draft.b] : null, bTouched: !!viewB.draft }));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TheSelection(unittest.TestCase):
    def test_a_selection_comments_on_the_diff_on_screen_after_a_to_b_to_a(self):
        fns = js_function("drHideSel") + "\n" + js_function("drOnSelect")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.js"
            p.write_text(SELECT_JS % {"fns": fns}, encoding="utf-8")
            proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"paintedA2": True, "draft": [0, 2], "bTouched": False})


class TheWiring(unittest.TestCase):
    def test_the_node_opens_closes_and_is_loaded_while_open(self):
        mount = js_function("wsMount")
        self.assertIn("if (e.target.closest('.wse[data-docs]')) {", mount)
        self.assertIn("v.docsOpen = v.docsOpen === false;", mount)
        self.assertIn("if (v.docsOpen !== false || wsfOn(v.find)) wsDocsLoad(v, false);", js_function("wsSync"))
        self.assertIn("v.docsOpen = !(s && s.docs === false);", js_function("wsView"), "open unless it was closed")
        self.assertIn("docs: v.docsOpen", js_function("wsPersist"))
        self.assertIn("wsDocsNodeHtml(v, v.docsList, wsDocsRm(v))", js_function("wsTreeHtml"))

    def test_the_roadmaps_tab_is_its_own_view(self):
        view = js_function("wsPaintView")
        self.assertIn("const rmTab = !!t && wsIsRoadmap(v, t.path)", view)
        self.assertIn("wsRoadmapPaint(v, stage, rmTab);", view)
        self.assertIn("rmPark(panel);", js_function("wsRenderInto"), "its draft outlives a rebuilt Workspace")
        self.assertIn("rmPark(el);", js_function("docsPanelRender"))
        self.assertIn("it.abs || wsfAbs(", js_function("wsfOpen"))
        self.assertIn("res = wsfMerge(res, extra, docsDir)", js_function("wsfSearch"))

    def test_the_switch_sits_above_a_code_projects_tree(self):
        self.assertIn("panel.innerHTML = wsPanelHtml({ tasks: true });", INDEX)
        fn = js_function("wsPanelHtml")
        self.assertIn('<input type="checkbox" class="wsp-tasks-cb"', fn)
        self.assertIn("> Show task folders</label>", fn)
        self.assertLess(fn.index("${tasks}"), fn.index('class="wsp-tree"'))
        self.assertIn("docsTasksSet(tasksCb.checked)", js_function("wsMount"))

    def test_a_commits_file_opens_that_commits_diff_in_a_review_of_its_own(self):
        od = js_function("chOpenDiff")
        self.assertIn("(sha ? '&commit=' + encodeURIComponent(sha) : '')", od)
        self.assertIn("chUseContext(files.dataset.project, files.dataset.root, sha)", od)
        self.assertIn("drReview('project:' + pid + '|' + root + (sha ? '@' + sha : '')", js_function("chUseContext"))
        load = js_function("chLoadStatus")
        self.assertIn("/api/git/log?path=", load)
        self.assertIn("files.length ? '<h4 class=\"chp-sec\">Uncommitted", load, "uncommitted only when there are any")
        wire = js_function("wireChangesPanel")
        self.assertIn("chOpenDiff(f.dataset.file, '', f.dataset.commit || '')", wire)


if __name__ == "__main__":
    unittest.main()
