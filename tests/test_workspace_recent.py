"""Recent files, and where the file showing is, in a Workspace.

index.html's "Workspace recent and where" block is plain data and pure
functions, run here in Node the way tests/test_workspace_tabs.py runs the tabs
block; skipped without Node:

* Recent lists the files shown, most recent first, closed tabs included, capped,
  each with how it was left; it survives a reload, a Workspace saved before it
  existed starts from its tabs, and junk in storage is dropped;
* a file opened again after its tab closed opens as it was left;
* a filter narrows the list without reordering it, and each file shows its
  folder under its root;
* a path as crumbs (root, folders, file), and the folders to open so a file or
  a folder shows in the tree, spelled as the tree spells them;
* the shortcut is Alt+R, and nothing like it.

It also checks the page wires what it needs: the Recent button, one tab path,
the shortcut acting only inside the pane (and from the viewer), the breadcrumb
and its phone rules.
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
FILEVIEW = (ROOT / "fileview.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def block(name: str) -> str:
    i = INDEX.index(f"// ---- {name}: begin")
    return INDEX[i:INDEX.index(f"// ---- {name}: end", i)]


def line(pattern: str) -> str:
    return re.search(pattern, INDEX, re.M).group(0)


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


JS = r"""
%s
const log = {};
const W = 'C:\\t';

// Shown, most recent first; again moves it to the front; capped.
const r = [];
wsRecentTouch(r, W + '\\a.md', { top: 10 });
wsRecentTouch(r, W + '\\b.py', null);
wsRecentTouch(r, W + '\\c.js', { marks: { source: 4 } });
wsRecentTouch(r, 'c:/T/A.md', null);
log.order = r.map(x => x.path);
log.keptState = r[0].st;
wsRecentNote(r, W + '\\c.js', { marks: { source: 9 }, top: 300 });
log.noted = { order: r.map(x => x.path), st: r[1].st };
log.noteUnknown = wsRecentNote(r, W + '\\nope', { top: 1 });
log.dropped = [wsRecentDrop(r, W + '\\B.PY'), wsRecentDrop(r, W + '\\b.py'), r.map(x => x.path)];
const cap = [];
for (let i = 0; i < WS_RECENT_MAX + 5; i++) wsRecentTouch(cap, '/r/f' + i, null);
log.cap = { n: cap.length, first: cap[0].path, last: cap[cap.length - 1].path, max: WS_RECENT_MAX };
log.touchEmpty = wsRecentTouch([], '', null);

// Through storage and back.
const saved = JSON.parse(JSON.stringify({ recent: r.map(x => ({ path: x.path, st: x.st })) }));
log.restored = wsRecentFrom(saved, [], '');
log.legacy = wsRecentFrom({ tabs: [] }, [wsNewTab('/r/one.md', { top: 5 }), wsNewTab('/r/two.md'), wsNewTab('/r/three.md')], '/r/two.md')
  .map(x => [x.path, x.st.top]);
log.junk = [null, 'x', { recent: 'no' }, { recent: [null, 4, { path: 3 }, { path: '' }, { path: 'x'.repeat(5000) },
  { path: '/r/ok.md', st: { view: 'evil', top: -4, marks: { source: 2.5 } } }, { path: '/R/OK.md' }] }]
  .map(s => wsRecentFrom(s, [], ''));
log.junkCap = wsRecentFrom({ recent: Array.from({ length: 80 }, (_, i) => ({ path: '/j/' + i })) }, [], '').length;

// A closed tab opened again opens as it was left; one never shown opens fresh.
const v = { tabs: [], sel: '', recent: [{ path: '/r/x.py', st: wsTabState({ view: 'source', marks: { source: 42 }, top: 900 }) }] };
const reopened = wsTabOpen(v, '/R/X.py');
const fresh = wsTabOpen(v, '/r/new.py');
log.reopen = { st: reopened.st, fresh: fresh.st, copy: reopened.st !== v.recent[0].st };
log.noRecent = wsTabOpen({ tabs: [], sel: '' }, '/r/y.py').st;

// Roots, crumbs, and the folders to open.
const roots = [{ path: 'C:\\p\\tasks\\t1', label: 'This task', kind: 'task' }, { path: 'C:\\p', label: 'Proj', kind: 'project' }];
log.rootOf = ['C:\\p\\tasks\\t1\\repo\\a.py', 'c:/P/TASKS/t1', 'C:\\p\\README.md', 'C:\\pp\\x', 'D:\\x', ''].map(p => { const x = wsRootOf(roots, p); return x ? x.label : null; });
log.crumbs = wsCrumbs(roots, 'C:\\p\\tasks\\t1\\repo\\static\\hl.js');
log.crumbsProject = wsCrumbs(roots, 'C:\\p\\docs\\a.md').map(c => [c.kind, c.name, c.path]);
log.crumbsNone = wsCrumbs(roots, 'D:\\elsewhere\\a.md');
log.crumbsSlash = wsCrumbs([{ path: '/home/u/t/', label: 'T' }], '/home/u/t/src/a.ts').map(c => c.path);
const dirs = new Map([
  ['C:\\p\\tasks\\t1', { entries: [{ name: 'repo', type: 'dir' }], error: '' }],
  ['C:\\p\\tasks\\t1\\repo', { entries: [{ name: 'Static', type: 'dir' }, { name: 'static.txt', type: 'file' }], error: '' }],
]);
log.reveal = [
  wsRevealDirs(new Map(), 'C:\\p\\tasks\\t1', 'C:\\p\\tasks\\t1\\repo\\static\\deep\\er\\hl.js', false),
  wsRevealDirs(dirs, 'C:\\p\\tasks\\t1', 'c:\\p\\tasks\\t1\\REPO\\static\\hl.js', false),
  wsRevealDirs(dirs, 'C:\\p\\tasks\\t1', 'C:\\p\\tasks\\t1\\repo\\static', true),
  wsRevealDirs(dirs, 'C:\\p\\tasks\\t1', 'C:\\p\\tasks\\t1', true),
];

// The list: order kept under a filter, folders under their root.
const rec = ['C:\\p\\tasks\\t1\\repo\\index.html', 'C:\\p\\docs\\plan.md', 'C:\\p\\tasks\\t1\\repo\\tests\\test_links.py', 'D:\\x\\y.txt']
  .map(p => ({ path: p, st: wsTabState(null) }));
log.items = wsRecentItems(rec, roots, '').map(x => [x.abs, x.path]);
log.filtered = wsRecentItems(rec, roots, 'i').map(x => x.path);
log.filteredHits = wsRecentItems(rec, roots, 'tlinks').map(x => [x.path, x.hits]);
log.html = wsGotoHtml('u', wsRecentItems(rec, roots, 'plan'));
log.notes = [wsRecentNoteText(0, 0, '', 'in this task'), wsRecentNoteText(4, 4, '  ', 'in this task'),
             wsRecentNoteText(4, 2, 'i', 'in this task'), wsRecentNoteText(1, 0, ' zz ', 'in this project'),
             wsRecentNoteText(1, 1, '', 'in this task')];

// The shortcut.
const K = o => ({ altKey: true, ctrlKey: false, metaKey: false, shiftKey: false, code: 'KeyR', key: 'r', ...o });
log.keys = [K({}), K({ key: '®' }), K({ ctrlKey: true }), K({ metaKey: true }), K({ shiftKey: true }), K({ altKey: false }),
            K({ code: 'KeyE' }), null].map(wsIsRecentKey);
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class RecentAndWhere(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([line(r"^const esc = .*$"), line(r"^const wsNorm = .*$"), line(r"^const wsSame = .*$"),
                         line(r"^function wsJoin\(.*$"), block("Workspace tabs"), block("Workspace find"),
                         block("Workspace recent and where")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "recent.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_most_recent_first_and_capped(self):
        r = self.r
        self.assertEqual(r["order"], ["C:\\t\\a.md", "C:\\t\\c.js", "C:\\t\\b.py"], "shown again moves to the front, once")
        self.assertEqual(r["keptState"]["top"], 10, "shown again without a state keeps the one it had")
        self.assertEqual(r["noted"]["order"], r["order"], "a new state does not move it")
        self.assertEqual(r["noted"]["st"], {"view": "", "wrap": None, "marks": {"source": 9}, "top": 300})
        self.assertIsNone(r["noteUnknown"])
        self.assertEqual(r["dropped"], [True, False, ["C:\\t\\a.md", "C:\\t\\c.js"]])
        self.assertEqual(r["cap"], {"n": r["cap"]["max"], "first": f"/r/f{r['cap']['max'] + 4}", "last": "/r/f5", "max": 30})
        self.assertIsNone(r["touchEmpty"])

    def test_remembered_across_a_reload(self):
        back = self.r["restored"]
        self.assertEqual([x["path"] for x in back], ["C:\\t\\a.md", "C:\\t\\c.js"])
        self.assertEqual(back[1]["st"]["marks"], {"source": 9})
        self.assertEqual(back[1]["st"]["top"], 300)
        self.assertEqual(self.r["legacy"], [["/r/two.md", 0], ["/r/one.md", 5], ["/r/three.md", 0]],
                         "saved before Recent: the tab showing first, then the others")

    def test_junk_in_storage_is_dropped(self):
        junk = self.r["junk"]
        self.assertEqual(junk[:3], [[], [], []])
        self.assertEqual(junk[3], [{"path": "/r/ok.md", "st": {"view": "", "wrap": None, "marks": {}, "top": 0}}])
        self.assertEqual(self.r["junkCap"], 30)

    def test_a_closed_tab_opens_as_it_was_left(self):
        r = self.r["reopen"]
        self.assertEqual(r["st"], {"view": "source", "wrap": None, "marks": {"source": 42}, "top": 900})
        self.assertEqual(r["fresh"], {"view": "", "wrap": None, "marks": {}, "top": 0})
        self.assertTrue(r["copy"], "the tab's state is its own copy")
        self.assertEqual(self.r["noRecent"]["top"], 0)

    def test_the_root_a_file_is_in(self):
        self.assertEqual(self.r["rootOf"], ["This task", "This task", "Proj", None, None, None],
                         "the deepest root wins; a sibling folder with the same start is not inside")

    def test_crumbs(self):
        c = self.r["crumbs"]
        self.assertEqual([(x["kind"], x["name"]) for x in c],
                         [("root", "This task"), ("dir", "repo"), ("dir", "static"), ("file", "hl.js")])
        self.assertEqual(c[2]["path"], "C:\\p\\tasks\\t1\\repo\\static", "spelled as the tree spells it")
        self.assertEqual(self.r["crumbsProject"], [["root", "Proj", "C:\\p"], ["dir", "docs", "C:\\p\\docs"], ["file", "a.md", "C:\\p\\docs\\a.md"]])
        self.assertIsNone(self.r["crumbsNone"])
        self.assertEqual(self.r["crumbsSlash"], ["/home/u/t/", "/home/u/t/src", "/home/u/t/src/a.ts"])

    def test_the_folders_to_open(self):
        unread, cased, folder, root = self.r["reveal"]
        self.assertEqual(unread["open"], ["C:\\p\\tasks\\t1", "C:\\p\\tasks\\t1\\repo", "C:\\p\\tasks\\t1\\repo\\static",
                                          "C:\\p\\tasks\\t1\\repo\\static\\deep", "C:\\p\\tasks\\t1\\repo\\static\\deep\\er"])
        self.assertEqual(unread["target"], "C:\\p\\tasks\\t1\\repo\\static\\deep\\er\\hl.js")
        self.assertEqual(cased["open"], ["C:\\p\\tasks\\t1", "C:\\p\\tasks\\t1\\repo", "C:\\p\\tasks\\t1\\repo\\Static"],
                         "names come from the listings once read: a folder, not the file of a similar name")
        self.assertEqual(cased["target"], "C:\\p\\tasks\\t1\\repo\\Static\\hl.js")
        self.assertEqual(folder["open"][-1], "C:\\p\\tasks\\t1\\repo\\Static", "a folder revealed opens too")
        self.assertEqual(root, {"open": ["C:\\p\\tasks\\t1"], "target": "C:\\p\\tasks\\t1"})

    def test_the_list_keeps_its_order_under_a_filter(self):
        self.assertEqual(self.r["items"], [
            ["C:\\p\\tasks\\t1\\repo\\index.html", "repo/index.html"],
            ["C:\\p\\docs\\plan.md", "Proj/docs/plan.md"],
            ["C:\\p\\tasks\\t1\\repo\\tests\\test_links.py", "repo/tests/test_links.py"],
            ["D:\\x\\y.txt", "D:/x/y.txt"],
        ])
        self.assertEqual(self.r["filtered"], ["repo/index.html", "repo/tests/test_links.py"], "most recent first, not best first")
        self.assertEqual(self.r["filteredHits"][0][0], "repo/tests/test_links.py")
        html = self.r["html"]
        self.assertIn('<span class="wsr-nm"><mark class="match">plan</mark>.md</span>', html, "the go-to-file list's look")
        self.assertIn('<span class="wsr-dir">Proj/docs</span>', html)

    def test_the_line_under_the_box(self):
        self.assertEqual(self.r["notes"], [
            "No files shown in this task yet. Each file you open is listed here, most recent first.",
            "4 recent files, most recent first.",
            "2 of 4 recent files, most recent first.",
            "No recent file matches “zz”.",
            "1 recent file, most recent first.",
        ])

    def test_the_shortcut_is_alt_r_alone(self):
        self.assertEqual(self.r["keys"], [True, True, False, False, False, False, False, False])


class RecentAndWhereAreWired(unittest.TestCase):
    def test_recent_is_a_button_by_the_box(self):
        self.assertIn('data-mode="recent" aria-pressed="false" aria-keyshortcuts="Alt+R"', INDEX)
        paint = fn(INDEX, "function wsfPaint(")
        self.assertIn("f.recent ? wsGotoHtml(v.uid, f.ritems)", paint, "the go-to-file list's look")

    def test_a_recent_file_opens_through_the_one_tab_path(self):
        opener = fn(INDEX, "function wsfOpen(")
        recent = opener[opener.index("if (f.recent) {"):opener.index("if (f.mode === 'files') {")]
        self.assertIn("wsOpenTabAt(v, it.abs, 0);", recent)
        self.assertIn("const was = (t.recent || []).find(x => wsSame(x.path, path));", INDEX)
        show = fn(INDEX, "function wsShowTab(")
        self.assertIn("wsRecentTouch(v.recent, t.path, t.st);", show)
        frame = fn(INDEX, "function wsFrameMessage(")
        self.assertIn("wsRecentNote(v.recent, t.path, t.st);", frame, "how it was left is kept for when its tab has closed")
        persist = fn(INDEX, "function wsPersist(")
        self.assertIn("recent: v.recent.map(x => ({ path: x.path, st: x.st })), follow: v.follow", persist)

    def test_the_shortcut_acts_only_inside_the_pane(self):
        self.assertIn('<div class="wsp" tabindex="-1">', INDEX, "a click in the pane gives it focus")
        mount = fn(INDEX, "function wsMount(")
        self.assertIn("el.onkeydown = (e) => {\n    if (!wsIsRecentKey(e)) return;", mount)
        self.assertNotIn("document.addEventListener('keydown'", mount, "no page-wide shortcut")
        self.assertIn("host.postMessage({ type: 'fv-key', key: 'recent' }, location.origin);", FILEVIEW)
        self.assertIn("ifr.contentDocument.addEventListener('keydown', fvOnKey, true)", FILEVIEW, "a rendered page too")
        self.assertIn("ev.code === 'KeyR'", FILEVIEW)
        self.assertIn("if (d.key === 'recent') wsRecentToggle(v, undefined, true);", INDEX)

    def test_breadcrumb_and_show_in_tree(self):
        self.assertIn('<nav class="wsc" aria-label="Where the file is"><ol class="wsc-list"></ol></nav>', INDEX)
        mount = fn(INDEX, "function wsMount(")
        self.assertIn("if (d) { wsReveal(v, d.dataset.path, { dir: true }); return; }", mount)
        self.assertIn("if (e.target.closest('.wsp-reveal')) { wsReveal(v, v.sel); return; }", mount)
        scroll = fn(INDEX, "function wsScrollTreeTo(")
        self.assertNotIn("scrollIntoView", scroll, "the tree scrolls, never the page")
        crumbs = fn(INDEX, "function wsPaintCrumbs(")
        self.assertIn("if (list._sig === html) return;", crumbs, "not rewritten under the pointer on every poll")

    def test_phone(self):
        i = INDEX.index("/* The path above the file: every crumb and button finger-sized")
        rules = INDEX[i:INDEX.index("\n", INDEX.index(".wsc-dir, .wsp-btn, .wsp-own", i))]
        self.assertIn("height: var(--touch-min); min-width: var(--touch-min);", rules)
        self.assertIn(".wsc { flex: 1; min-width: 0; overflow-x: auto;", INDEX, "the path scrolls within itself")
        self.assertIn("nav.scrollLeft = nav.scrollWidth;", INDEX, "cut from the left: its end stays in view")


if __name__ == "__main__":
    unittest.main()
