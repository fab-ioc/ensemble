"""Files open in the Workspace as tabs, remembered per task.

index.html keeps the open files of a Workspace (a task's, or a project's) as
tabs above the file viewer. Which files are open, in what order, which one
shows and how each was left live in a block of plain functions, run here in
Node the way tests/test_links.py runs the link block; skipped without Node:

* opening a file adds a tab at the end, and opening it again switches to it;
* closing the tab that shows shows its neighbour; closing another changes
  nothing else;
* what is saved comes back after a reload, in order, with the same tab showing
  and each tab's view, Wrap, marked lines and scroll position; anything
  malformed in storage is dropped rather than trusted, and a Workspace saved
  before tabs existed opens its one selected file as a tab;
* only a few viewers stay loaded, and never the one showing is unloaded;
* a tab whose file has gone says so, from its folder's listing or from the
  viewer's 404, and stops saying so when the file comes back;
* index.html and fileview.html read a tab's state the same way.

It also checks the page wires what it needs: a tab row with roles and roving
focus, and keys that act only on a focused tab.
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


def tabs_block() -> str:
    i = INDEX.index("// ---- Workspace tabs: begin")
    j = INDEX.index("// ---- Workspace tabs: end", i)
    norm = re.search(r"^const wsNorm = .*$", INDEX, re.M).group(0)
    same = re.search(r"^const wsSame = .*$", INDEX, re.M).group(0)
    return norm + "\n" + same + "\n" + INDEX[i:j]


def fileview_fn(name: str) -> str:
    i = FILEVIEW.index(f"function {name}(")
    return FILEVIEW[i:FILEVIEW.index("\n}\n", i) + 3]


def fileview_start_state() -> str:
    return fileview_fn("fvStartState") + fileview_fn("fvScrollOwner")


JS = r"""
%s
%s
const log = {};
const paths = t => t.tabs.map(x => x.path);

// Open, switch, reopen.
const v = { tabs: [], sel: '' };
wsTabOpen(v, 'C:\\p\\a.md'); wsTabOpen(v, 'C:\\p\\b.py'); wsTabOpen(v, 'C:\\p\\c.json');
log.opened = { paths: paths(v), sel: v.sel };
const again = wsTabOpen(v, 'c:/p/A.md');
log.reopened = { paths: paths(v), sel: v.sel, same: again === v.tabs[0] };

// Close: an inactive tab, then the active one in the middle, then at the end.
wsTabOpen(v, 'C:\\p\\d.txt');
wsTabShow = p => { v.sel = p; };
wsTabShow('C:\\p\\b.py');
wsTabClose(v, 'C:\\p\\a.md');
log.closeOther = { paths: paths(v), sel: v.sel };
wsTabClose(v, 'C:\\p\\b.py');
log.closeActive = { paths: paths(v), sel: v.sel };
wsTabShow('C:\\p\\d.txt');
wsTabClose(v, 'C:\\p\\d.txt');
log.closeLast = { paths: paths(v), sel: v.sel };
wsTabClose(v, 'C:\\p\\c.json');
log.closeAll = { paths: paths(v), sel: v.sel };
log.closeUnknown = wsTabClose(v, 'C:\\nope');

// The row is capped: the leftmost goes.
const cap = { tabs: [], sel: '' };
for (let i = 0; i <= WS_TABS_MAX; i++) wsTabOpen(cap, '/r/f' + i + '.md');
log.cap = { n: cap.tabs.length, first: cap.tabs[0].path, sel: cap.sel, max: WS_TABS_MAX };

// Restore: a round trip through storage.
const r = { tabs: [], sel: '' };
wsTabOpen(r, '/r/one.md').st = wsTabState({ view: 'source', wrap: false, marks: { source: 12, pretty: 3 }, top: 480.4 });
wsTabOpen(r, '/r/two.py');
wsTabOpen(r, '/r/three.json').st = wsTabState({ view: 'pretty', wrap: true, top: 9 });
r.sel = '/r/two.py';
const back = wsTabsFrom(JSON.parse(JSON.stringify(wsTabsSaved(r))));
log.restored = { paths: back.tabs.map(x => x.path), active: back.active, st: back.tabs.map(x => x.st),
                 flags: back.tabs.map(x => [x.missing, x.gone404, x.seen]) };
log.legacy = wsTabsFrom({ open: ['/r'], sel: '/r/old.md', scroll: 40 });
log.junk = [null, 7, 'x', [], { tabs: 'no' }, { tabs: [null, 5, { path: 3 }, { path: '' }, { path: '/r/x.md', st: { view: 'evil', wrap: 'yes', marks: { source: -1, pretty: 2.5, other: 4 }, top: -3 } }, { path: '/R/X.md' }], active: '/r/missing.md' }]
  .map(s => wsTabsFrom(s));
log.states = [wsTabState({ view: 'table', wrap: false, marks: { source: 7 }, top: Infinity }), wsTabState('nope'),
              wsTabState({ view: 'preview', top: 1e3 })];

// Loaded viewers: the least recently shown go, never the one showing.
const live = [];
log.live = [['a', 2], ['b', 2], ['a', 2], ['c', 2], ['d', 2], ['d', 2], ['e', 0]].map(([k, max]) => ({ drop: wsLiveTouch(live, k, max), live: live.slice() }));

// Missing files.
const dirs = new Map([
  ['C:\\p', { entries: [{ name: 'a.md', type: 'file' }, { name: 'sub', type: 'dir' }], error: '' }],
  ['C:\\gone', { entries: [], error: '404 {"error": "not_found"}' }],
  ['C:\\locked', { entries: [], error: '403 {"error": "path_not_allowed"}' }],
  ['C:\\big', { entries: Array.from({ length: 2000 }, (_, i) => ({ name: 'f' + i, type: 'file' })), error: '' }],
]);
log.present = ['C:\\p\\a.md', 'c:/P/a.md', 'C:\\p\\b.md', 'C:\\p\\sub', 'C:\\gone\\x.md', 'C:\\locked\\x.md', 'C:\\big\\zz.md', 'C:\\unread\\x.md', 'x.md']
  .map(p => wsFilePresent(dirs, p));
const m = { tabs: [wsNewTab('C:\\p\\a.md'), wsNewTab('C:\\p\\b.md'), wsNewTab('C:\\unread\\x.md')], sel: '' };
const steps = [];
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
m.tabs[2].gone404 = true;                                   // its viewer said 404
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
dirs.get('C:\\p').entries.push({ name: 'b.md', type: 'file' });  // b.md came back
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
dirs.set('C:\\unread', { entries: [], error: '' });           // x.md's folder read: not there
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
dirs.get('C:\\unread').entries.push({ name: 'x.md', type: 'file' });  // and back
steps.push([wsTabsCheck(m, dirs), m.tabs.map(x => x.missing)]);
log.missing = steps;

// A 404 while the listing still shows the file: gone, and no reload every
// poll while the listing shows the same version; back once it is written again.
const g = { tabs: [wsNewTab('C:\\q\\f.md')], sel: '' };
const gd = new Map([['C:\\q', { entries: [{ name: 'f.md', type: 'file', mtime: 100, size: 5 }], error: '' }]]);
const gchk = () => [wsTabsCheck(g, gd), g.tabs[0].missing];
const gs = [gchk()];
wsTabGone(g.tabs[0], gd); gs.push(gchk(), gchk(), gchk());
gd.get('C:\\q').entries = [{ name: 'f.md', type: 'file', mtime: 160, size: 5 }];   // deleted and written again between polls
gs.push(gchk(), gchk());
wsTabGone(g.tabs[0], gd); gs.push(gchk());
gd.get('C:\\q').entries = []; gs.push(gchk());                                    // a poll sees it gone
gd.get('C:\\q').entries = [{ name: 'f.md', type: 'file', mtime: 160, size: 5 }]; gs.push(gchk());   // and back, same version
log.gone = gs;
// A 404 before the folder was read: the first listing showing the file lets it
// load once more; a second 404 against that listing stands.
const u = { tabs: [wsNewTab('C:\\u\\f.md')], sel: '' };
const ud = new Map();
const uchk = () => [wsTabsCheck(u, ud), u.tabs[0].missing];
const us = [];
wsTabGone(u.tabs[0], ud); us.push(uchk());
ud.set('C:\\u', { entries: [{ name: 'f.md', type: 'file', mtime: 1, size: 1 }], error: '' }); us.push(uchk());
wsTabGone(u.tabs[0], ud); us.push(uchk(), uchk());
log.goneUnread = us;
// Written again within the second, same size: the listing shows the same
// version. A reading made after the 404 lets it load once more; if that 404s
// too, it stands until the listing shows the file absent or changed.
const s = { tabs: [wsNewTab('C:\\s\\f.md')], sel: '' };
const sd = new Map();
const sread = entries => sd.set('C:\\s', { entries, error: '', gen: (sd.get('C:\\s') || { gen: 0 }).gen + 1 });
const same = [{ name: 'f.md', type: 'file', mtime: 100, size: 5 }];
const schk = () => [wsTabsCheck(s, sd), s.tabs[0].missing];
const ss = [];
sread(same); ss.push(schk());
wsTabGone(s.tabs[0], sd); ss.push(schk(), schk());          // no new reading: stays gone
sread(same); ss.push(schk());                               // a new reading still shows it: loads once more
wsTabGone(s.tabs[0], sd); ss.push(schk());                  // and 404s again
sread(same); ss.push(schk()); sread(same); ss.push(schk()); // no second chance for the same version
sread([]); ss.push(schk());                                 // seen absent
sread(same); ss.push(schk());                               // and back
log.sameSig = ss;

// The tree's saved folders and scroll.
log.tree = [null, 'x', { open: [1, '', 'C:\\a', 'C:\\a', 'x'.repeat(5000), {}, ['C:\\n'], 'C:\\b'], scroll: 'x' },
            { open: 'C:\\a', scroll: -5 }, { open: ['/r'], scroll: 40.6 }, { scroll: Infinity }]
  .map(s => wsTreeFrom(s));
log.treeCap = wsTreeFrom({ open: Array.from({ length: 600 }, (_, i) => '/d' + i) }).open.length;
log.treeMax = WS_OPEN_MAX;

// What scrolls in each view of the viewer.
globalThis.document = { scrollingElement: { id: 'page' } };
const fakeMain = found => ({ querySelector: sel => { for (const s of sel.split(',')) if (found[s.trim()]) return found[s.trim()]; return null; } });
log.owner = [
  fvScrollOwner(fakeMain({ '.code': { id: 'code' } })),
  fvScrollOwner(fakeMain({ '.csv-wrap': { id: 'table' } })),
  fvScrollOwner(fakeMain({ 'iframe.file': { contentDocument: { scrollingElement: { id: 'rendered' } } } })),
  fvScrollOwner(fakeMain({ 'iframe.file': { get contentDocument() { throw new Error('cross-origin'); } } })),
  fvScrollOwner(fakeMain({ 'iframe.file': { contentDocument: null } })),
  fvScrollOwner(fakeMain({})),
].map(o => o ? o.id : null);

// The two pages read a tab's state alike.
const samples = [null, 'x', { view: 'source', wrap: true, marks: { source: 4, pretty: 0 }, top: 12.6 }, { view: 'raw', wrap: 1, marks: 'm', top: '5' },
                 { view: 'pretty', marks: { pretty: 9 } }, { top: -1, marks: { source: 1.5 } },
                 { view: 'source', marks: { source: 3 }, hit: { line: 3, col: 4, len: 6 } }, { hit: { line: 3, col: -1, len: 6 } },
                 { hit: { line: 0, col: 0, len: 1 } }, { hit: { line: 2, col: 1.5, len: 1 } }, { hit: { line: 2, col: 0, len: 0 } }, { hit: 'x' }];
log.alike = samples.map(s => [JSON.stringify(wsTabState(s)), JSON.stringify(fvStartState(JSON.stringify(s)))]);
log.badJson = fvStartState('{not json');
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class WorkspaceTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "tabs.cjs"
            script.write_text(JS % (tabs_block(), fileview_start_state()), encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_open_adds_at_the_end_and_reopening_switches(self):
        r = self.r
        self.assertEqual(r["opened"], {"paths": ["C:\\p\\a.md", "C:\\p\\b.py", "C:\\p\\c.json"], "sel": "C:\\p\\c.json"})
        self.assertEqual(r["reopened"]["paths"], ["C:\\p\\a.md", "C:\\p\\b.py", "C:\\p\\c.json"], "a file already open is not opened twice")
        self.assertEqual(r["reopened"]["sel"], "C:\\p\\a.md", "the tab keeps the path it was opened with")
        self.assertTrue(r["reopened"]["same"])
        self.assertEqual(r["cap"]["n"], r["cap"]["max"])
        self.assertEqual(r["cap"]["first"], "/r/f1.md")
        self.assertEqual(r["cap"]["sel"], f"/r/f{r['cap']['max']}.md")

    def test_close(self):
        r = self.r
        self.assertEqual(r["closeOther"], {"paths": ["C:\\p\\b.py", "C:\\p\\c.json", "C:\\p\\d.txt"], "sel": "C:\\p\\b.py"})
        self.assertEqual(r["closeActive"], {"paths": ["C:\\p\\c.json", "C:\\p\\d.txt"], "sel": "C:\\p\\c.json"}, "the right-hand neighbour shows")
        self.assertEqual(r["closeLast"], {"paths": ["C:\\p\\c.json"], "sel": "C:\\p\\c.json"}, "at the end, the left-hand one")
        self.assertEqual(r["closeAll"], {"paths": [], "sel": ""})
        self.assertEqual(r["closeUnknown"], "")

    def test_restore_after_reload(self):
        r = self.r["restored"]
        self.assertEqual(r["paths"], ["/r/one.md", "/r/two.py", "/r/three.json"])
        self.assertEqual(r["active"], "/r/two.py")
        self.assertEqual(r["st"][0], {"view": "source", "wrap": False, "marks": {"source": 12, "pretty": 3}, "top": 480})
        self.assertEqual(r["st"][1], {"view": "", "wrap": None, "marks": {}, "top": 0})
        self.assertEqual(r["st"][2], {"view": "pretty", "wrap": True, "marks": {}, "top": 9})
        self.assertEqual(r["flags"], [[False, False, None]] * 3, "whether a file is gone is found out again, not remembered")

    def test_a_workspace_saved_before_tabs_opens_its_file_as_a_tab(self):
        legacy = self.r["legacy"]
        self.assertEqual([t["path"] for t in legacy["tabs"]], ["/r/old.md"])
        self.assertEqual(legacy["active"], "/r/old.md")

    def test_malformed_storage_is_dropped(self):
        junk = self.r["junk"]
        for j in junk[:5]:
            self.assertEqual(j, {"tabs": [], "active": ""})
        last = junk[5]
        self.assertEqual([t["path"] for t in last["tabs"]], ["/r/x.md"], "bad entries and a second copy of a path go")
        self.assertEqual(last["tabs"][0]["st"], {"view": "", "wrap": None, "marks": {}, "top": 0})
        self.assertEqual(last["active"], "/r/x.md", "an active tab that is not open falls back to the first")
        self.assertEqual(self.r["states"][0], {"view": "table", "wrap": False, "marks": {"source": 7}, "top": 0})
        self.assertEqual(self.r["states"][1], {"view": "", "wrap": None, "marks": {}, "top": 0})
        self.assertEqual(self.r["states"][2], {"view": "preview", "wrap": None, "marks": {}, "top": 1000})

    def test_only_a_few_viewers_stay_loaded(self):
        steps = self.r["live"]
        self.assertEqual(steps[0], {"drop": [], "live": ["a"]})
        self.assertEqual(steps[1], {"drop": [], "live": ["a", "b"]})
        self.assertEqual(steps[2], {"drop": [], "live": ["b", "a"]})
        self.assertEqual(steps[3], {"drop": ["b"], "live": ["a", "c"]})
        self.assertEqual(steps[4], {"drop": ["a"], "live": ["c", "d"]})
        self.assertEqual(steps[5], {"drop": [], "live": ["c", "d"]})
        self.assertEqual(steps[6], {"drop": ["c", "d"], "live": ["e"]}, "the one showing always stays")

    def test_a_tab_whose_file_has_gone_says_so(self):
        self.assertEqual(self.r["present"], [True, True, False, False, False, None, None, None, None])
        steps = self.r["missing"]
        self.assertEqual(steps[0], [True, [False, True, False]], "b.md is not in its folder")
        self.assertEqual(steps[1], [False, [False, True, False]], "nothing changed, nothing to repaint")
        self.assertEqual(steps[2], [True, [False, True, True]], "the viewer's 404 counts while the folder is unread")
        self.assertEqual(steps[3], [True, [False, False, True]], "b.md came back")
        self.assertEqual(steps[4], [False, [False, False, True]])
        self.assertEqual(steps[5], [True, [False, False, False]], "x.md came back, and the 404 is forgotten")

    def test_a_404_against_a_listing_that_shows_the_file(self):
        self.assertEqual(self.r["gone"], [
            [False, False],
            [True, True], [False, True], [False, True],   # same version listed: stays gone, no reload each poll
            [True, False], [False, False],                # written again: back
            [True, True], [False, True], [True, False],   # gone from the listing, then back
        ])
        self.assertEqual(self.r["goneUnread"], [[True, True], [True, False], [True, True], [False, True]])

    def test_a_file_written_again_within_the_same_second(self):
        self.assertEqual(self.r["sameSig"], [
            [False, False],
            [True, True], [False, True],    # gone; the old reading gives no second chance
            [True, False],                  # a reading after the 404 still lists it: loads once more
            [True, True],                   # 404 again
            [False, True], [False, True],   # same version: no reload loop
            [False, True],                  # absent: still gone
            [True, False],                  # back
        ])

    def test_malformed_tree_storage_is_dropped(self):
        empty = {"open": [], "scroll": 0}
        tree = self.r["tree"]
        self.assertEqual(tree[0], empty)
        self.assertEqual(tree[1], empty)
        self.assertEqual(tree[2], {"open": ["C:\\a", "C:\\b"], "scroll": 0}, "only bounded string paths, once each")
        self.assertEqual(tree[3], empty)
        self.assertEqual(tree[4], {"open": ["/r"], "scroll": 41})
        self.assertEqual(tree[5], empty)
        self.assertEqual(self.r["treeCap"], self.r["treeMax"])

    def test_each_view_scrolls_where_it_actually_scrolls(self):
        self.assertEqual(self.r["owner"], ["code", "table", "rendered", None, None, "page"])

    def test_both_pages_read_a_tab_state_alike(self):
        for idx, (a, b) in enumerate(self.r["alike"]):
            self.assertEqual(a, b, f"sample {idx}")
        self.assertEqual(self.r["badJson"], {"view": "", "wrap": None, "marks": {}, "top": 0})


class TabsAreWired(unittest.TestCase):
    def test_the_row_is_a_tablist_with_roving_focus(self):
        self.assertIn('class="wst" role="tablist"', INDEX)
        self.assertIn('role="tab"', INDEX)
        self.assertIn('aria-selected="${on}" tabindex="${on ? 0 : -1}"', INDEX)
        self.assertIn('class="wsp-frames" role="tabpanel"', INDEX)

    def test_keys_act_on_a_focused_tab_only(self):
        mount = INDEX[INDEX.index("function wsMount("):INDEX.index("\n}\n", INDEX.index("function wsMount("))]
        keys = mount[mount.index("strip.onkeydown"):]
        keys = keys[:keys.index("\n  };\n")]
        for k in ("'ArrowRight'", "'ArrowLeft'", "'Home'", "'End'", "'Enter'", "' '", "'Delete'"):
            self.assertIn(k, keys)
        self.assertIn("e.target.closest('.wst-tab')", keys)
        self.assertNotIn("document.addEventListener('keydown'", mount, "no page-wide shortcut")
        self.assertIn("e.button === 1", mount, "a middle click closes a tab")

    def test_viewer_is_not_reloaded_by_the_refresh(self):
        sync = INDEX[INDEX.index("async function wsSync("):INDEX.index("\nfunction wsMount(")]
        self.assertIn("if (wsTabsCheck(v, v.dirs)) wsPaintView(v);", sync, "the viewer is repainted only when a tab's file comes or goes")
        paint = INDEX[INDEX.index("function wsPaintView("):INDEX.index("\nfunction wsFrameMessage(")]
        self.assertIn("if (key && !v.frames.has(key))", paint, "a loaded viewer is shown, not loaded again")

    def test_fileview_reports_its_state_to_the_workspace(self):
        self.assertIn("type: 'fv-state'", FILEVIEW)
        self.assertIn("type: 'fv-missing'", FILEVIEW)
        self.assertIn("d.type === 'fv-state' || d.type === 'fv-missing') && ev.origin === location.origin", INDEX)
        self.assertIn("wsTabGone(t, v.dirs);", INDEX, "a 404 keeps the listing's version it was said against")
        self.assertIn("tr.open.forEach(p => v.open.add(p));", INDEX, "saved folders go through wsTreeFrom")

    def test_images_and_pdfs_say_when_missing_and_pages_follow_their_own_scroll(self):
        # Read once: the copy read is what is shown, so a file deleted in
        # between says it is missing instead of showing a broken image.
        media = FILEVIEW[FILEVIEW.index("if (MEDIA[ext]) {"):]
        media = media[:media.index("\n  }\n")]
        self.assertEqual(media.count("fetch("), 1)
        self.assertIn("if (r && r.status === 404) { notFound(); return; }", media)
        self.assertIn("URL.createObjectURL(", media)
        self.assertIn('src="${url}"', media)
        self.assertNotIn('src="${src}"', media)
        self.assertIn("ifr.contentDocument.addEventListener('scroll', onScroll, true)", FILEVIEW)
        self.assertIn("{ view, wrap: WRAP, marks: { ...marked }, ...(hitNow() ? { hit: hitNow() } : {}) }", FILEVIEW)

    def test_closing_a_missing_files_tab_keeps_focus(self):
        mount = INDEX[INDEX.index("function wsMount("):]
        gone = mount[mount.index(".wsp-frames').onclick"):]
        gone = gone[:gone.index("\n  };\n")]
        self.assertIn("on.focus(", gone)
        self.assertIn("pane.focus(", gone)


if __name__ == "__main__":
    unittest.main()
