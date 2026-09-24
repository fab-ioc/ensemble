"""A project's Workspace opens on its documents; its Changes list what landed.

index.html's Documents list and the project Changes tab's "Landed on main"
list are pure functions, run here in Node (skipped without it):

* the Documents list: loading, unreadable, empty (saying what would fill it and
  where), and the rows newest first as the hub sends them: title, folder, the
  task's chip linking to it (or "in the code"), date and size; a capped list
  says so;
* it is what a project's Workspace shows when no file tab is: the fixed
  first tab, which never closes;
* "Show task folders": a code project's tree leaves its tasks' folders out of
  its home until the switch is on; other folders are untouched;
* Landed on main: each commit's task chip, subject without "Merge #NN: ", its
  files (the first 8, then "+N more"), the file showing marked by file and
  commit, and an empty main line says what would fill it.

It also checks the page wires them: the Workspace opens on Documents, the
switch sits above the tree, a commit's file diff asks for that commit and has
a review of its own.
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
const wsDocsProjectOf = () => null;
const docsInTask = () => false;
const histFolder = p => { const i = p.lastIndexOf('/'); return i > 0 ? p.slice(0, i) : ''; };
%(deps)s
%(docs)s
%(changes)s
const out = {};

// The Documents list.
out.loading = wsDocsHtml(null);
out.failed = wsDocsHtml({ data: null, err: '500 boom' });
out.empty = wsDocsHtml({ data: { dir: 'C:\\home\\Documents', items: [], key: 'ED' } });
const items = [
  { path: 'C:\\home\\Documents\\#40 Design\\notes.md', rel: '#40 Design/notes.md', title: 'notes', no: 40, group: 'Design', mtime: 1790000000, size: 2048, inCode: false },
  { path: 'C:\\code\\docs\\arch.md', rel: 'docs/arch.md', title: 'arch', no: null, group: '', mtime: 1780000000, size: 10, inCode: true },
  { path: 'C:\\home\\Documents\\Loose.md', rel: 'Loose.md', title: 'Loose <b>', no: null, group: '', mtime: 1770000000, size: 5, inCode: false },
];
out.list = wsDocsHtml({ data: { dir: 'C:\\home\\Documents', codeDocs: 'C:\\code\\docs', items, key: 'ED', truncated: false } });
out.capped = wsDocsHtml({ data: { dir: 'C:\\home\\Documents', items: items.slice(0, 1), key: '', truncated: true } });

// What shows: the list when no file tab is.
const pv = { ctx: { kind: 'project', projectId: 'p1' }, tabs: [{ path: 'C:\\home\\a.md' }], sel: '' };
out.showing = [wsDocsShowing(pv)];
pv.sel = 'C:\\home\\a.md'; out.showing.push(wsDocsShowing(pv));
pv.sel = 'C:\\home\\gone.md'; out.showing.push(wsDocsShowing(pv));
out.showing.push(wsDocsShowing({ ctx: { kind: 'task', sid: 's' }, tabs: [], sel: '' }));

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
            "wsNorm", "wsSame", "wsJoin", "wsFmtSize", "wsTabName", "docsApart", "wsHidden", "wsRowsHtml"))
        src = JS % {
            "deps": deps,
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

    def test_loading_failed_and_empty_say_what_they_are(self):
        o = self.o
        self.assertIn("Loading the documents…", o["loading"])
        self.assertIn("could not be read: 500 boom", o["failed"])
        self.assertIn("No documents yet", o["empty"])
        self.assertIn("<code>C:\\home\\Documents</code>", o["empty"], "says where they go")
        self.assertIn("“#12 Title.md”", o["empty"], "and how they are named")

    def test_rows_in_the_order_given_with_task_chip_date_and_size(self):
        h = self.o["list"]
        self.assertIn(">Documents</h3>", h)
        self.assertIn("3 documents, newest first", h)
        self.assertIn("and in the code’s <code>docs</code> folder", h)
        rows = re.findall(r'<li class="wsd-row">.*?</li>', h)
        self.assertEqual(len(rows), 3)
        self.assertIn('data-path="C:\\home\\Documents\\#40 Design\\notes.md"', rows[0])
        self.assertIn('<span class="wsd-f">#40 Design</span>', rows[0])
        self.assertIn('<a class="tno wsd-no" href="#" data-task="ED-40"', rows[0])
        self.assertIn('>2.0 KB<', rows[0])
        self.assertRegex(rows[0], r'<span class="wsd-when" title="[^"]+">[^<]+</span>')
        self.assertIn('<span class="loz neutral">in the code</span>', rows[1])
        self.assertNotIn("data-task", rows[1])
        self.assertIn("Loose &lt;b&gt;", rows[2], "titles are escaped")
        self.assertNotIn("data-task", rows[2])

    def test_a_capped_list_says_so_and_a_keyless_chip_is_the_number(self):
        h = self.o["capped"]
        self.assertIn("1+ document", h)
        self.assertIn("The 1 newest only; the rest are in the tree.", h)
        self.assertIn('data-task="#40"', h)

    def test_the_list_shows_when_no_file_tab_does(self):
        self.assertEqual(self.o["showing"], [True, False, True, False])

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
const document = { createElement: () => (btn = { style: {}, addEventListener() {}, remove() {} }), body: { appendChild() {} } };
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
    def test_a_project_workspace_opens_on_documents_the_fixed_first_tab(self):
        mount = js_function("wsMount")
        self.assertIn("if (wsDocsCtx(v)) v.sel = '';", mount)
        self.assertIn("if (doc) { wsOpenTab(v, doc.dataset.path); return; }", mount)
        tabs = js_function("wsPaintTabs")
        self.assertIn('class="wst-tab wst-docs', tabs)
        docs_tab = tabs[tabs.index("wst-docs"):tabs.index("v.tabs.map")]
        self.assertNotIn("wst-x", docs_tab, "the Documents tab has no ×")
        self.assertIn("strip.hidden = !docs && !v.tabs.length;", tabs)
        self.assertIn("if (!path) return;", js_function("wsCloseTab"), "Delete or a middle click leave it")
        self.assertIn("wsDocsHtml(v.docsList)", js_function("wsPaintView"))
        self.assertIn("if (wsDocsShowing(v)) wsDocsLoad(v, false);", js_function("wsSync"))

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
