"""Every way that led to Needs you lands on the list's Needs you (#135).

The bell, its tray's Show all, the Needs you page and its `#needs` address are
gone; the list's first group is Needs you. index.html's goHome, swFocusNeeds,
swShowNeeds and the last-conversation helpers run here in Node over a stand-in
list (skipped without Node): on a desktop the list scrolls to Needs you and
focuses its head (by project, the first row that needs you), and an empty
middle opens the last conversation, else the first Needs you entry; on a phone
it is the list itself. The wiring (the wordmark, `#needs` at load and on
hashchange, a page saved on the old Needs you page) is read from the source.
The real page is driven in tests/test_middle.py (#needs in Chrome).
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
NODE = shutil.which("node")


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


JS = r"""
%s
const log = {};
let PHONE = false, SELECTED_PROJECT = 'p1', SELECTED_SID = null, PH_CARDS = true, ALL_ROWS = [], calls = [];
const store = new Map();
const localStorage = { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => { calls.push('save'); store.set(k, String(v)); } };
const phoneNow = () => PHONE;
const midLeave = () => calls.push('midLeave');
const renderRows = () => calls.push('renderRows');
const swRender = () => calls.push('swRender');
const openDetail = sid => { calls.push('openDetail:' + sid); body.open = true; };
const openProjectPage = pid => calls.push('project:' + pid);
const pdReveal = id => calls.push('reveal:' + id);
const PROJECTS = { p1: { id: 'p1', poRoomId: 'po-1' }, p2: { id: 'p2', poRoomId: '' } };
const projectById = id => PROJECTS[id] || null;
const body = { open: false, classList: { contains: c => c === 'detail-open' && body.open } };
// The list: a Needs you head (grouped by status) or its rows (by project).
let byProject = false, focused = null;
const node = (name, isRow) => ({ name, attrs: {}, clicked: 0,
  matches: s => s === '.sw-row' && isRow,
  setAttribute(k, v) { this.attrs[k] = v; }, scrollIntoView(o) { this.scrolled = o; },
  focus(o) { focused = this.name; this.focusOpts = o; }, click() { this.clicked++; calls.push('click:' + this.name); } });
let head, firstRow;
const reset = () => { head = node('needs-head', false); firstRow = node('needs-row', true); focused = null; calls = []; };
const list = { querySelector: s => s === '[data-group="needs"] > .sw-ghead' ? (byProject ? null : head)
                                   : s === '.sw-row.needs' ? firstRow : null };
const document = { body, getElementById: id => id === 'sw-list' ? list : null,
                   querySelector: s => s === '#sw-list .sw-row.needs' ? firstRow : null };

// The wordmark on a desktop: the middle empties, the list shows Needs you.
reset(); goHome();
log.homeDesk = { calls, focused, tab: head.attrs.tabindex, scrolled: head.scrolled, project: SELECTED_PROJECT, cards: PH_CARDS };
// On a phone: the list, nothing to scroll to.
reset(); PHONE = true; SELECTED_PROJECT = 'p1'; goHome();
log.homePhone = { calls, focused, project: SELECTED_PROJECT };
// #needs, the old Show all and a page saved on the old Needs you page, on a phone: the list.
reset(); SELECTED_PROJECT = 'p1'; PH_CARDS = true; swShowNeeds();
log.needsPhone = { calls, project: SELECTED_PROJECT, cards: PH_CARDS };
PHONE = false;
// A desktop with a conversation open keeps it; Needs you comes into view.
reset(); SELECTED_PROJECT = 'p1'; swShowNeeds();
log.needsKeeps = { calls, focused };
// A desktop's empty middle: the last conversation, remembered per browser.
reset(); SELECTED_PROJECT = null; ALL_ROWS = [{ sessionId: 's1' }]; rememberConv({ sid: 's1' }); rememberConv({ sid: 's1' });
log.saves = calls.filter(c => c === 'save').length;
calls = []; swShowNeeds();
log.needsLast = { calls, focused };
// Its task gone: the first Needs you entry.
reset(); body.open = false; ALL_ROWS = []; swShowNeeds();
log.needsFirst = { calls, focused };
// A PO remembered: its project's screen, the PO chat revealed.
reset(); rememberConv({ po: 'p1' }); calls = []; swShowNeeds();
log.needsPo = calls;
// A project whose PO is gone: the first Needs you entry.
reset(); rememberConv({ po: 'p2' }); calls = []; openDefaultConv();
log.poGone = calls;
// Grouped by project: the first row that needs you, focused as it is.
reset(); byProject = true; SELECTED_PROJECT = 'p1'; swShowNeeds();
log.byProject = { focused, tab: firstRow.attrs.tabindex, scrolled: firstRow.scrolled };
// Nothing remembered, or garbage.
store.set(LAST_CONV_KEY, '{nope'); log.garbage = lastConv();
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class NeedsYouEntryPoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        heads = ("function goHome(", "function swFocusNeeds(", "function swShowNeeds(", "function lastConv(",
                 "function rememberConv(", "function openConv(", "function openDefaultConv(")
        src = "\n".join([re.search(r"^const LAST_CONV_KEY = .*$", INDEX, re.M).group(0)] + [fn(INDEX, h) for h in heads])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "needs_entry.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_the_wordmark_on_a_desktop_empties_the_middle_and_shows_needs_you(self):
        h = self.r["homeDesk"]
        self.assertEqual(h["calls"][:2], ["midLeave", "renderRows"])
        self.assertEqual((h["project"], h["cards"]), (None, False))
        self.assertEqual(h["focused"], "needs-head")
        self.assertEqual(h["tab"], "-1", "a head takes the focus without joining the Tab order")
        self.assertEqual(h["scrolled"], {"block": "start"})

    def test_on_a_phone_every_way_is_the_list(self):
        self.assertEqual(self.r["homePhone"], {"calls": ["midLeave", "renderRows"], "focused": None, "project": None})
        n = self.r["needsPhone"]
        self.assertEqual((n["calls"], n["project"], n["cards"]), (["midLeave", "renderRows"], None, False))

    def test_a_desktop_keeps_what_it_shows(self):
        n = self.r["needsKeeps"]
        self.assertEqual(n["focused"], "needs-head")
        self.assertFalse([c for c in n["calls"] if c.startswith(("openDetail", "project:", "click:", "midLeave"))])

    def test_an_empty_middle_opens_the_last_conversation_else_the_first_needs_you(self):
        self.assertEqual(self.r["saves"], 1, "written only when it changes")
        self.assertIn("openDetail:s1", self.r["needsLast"]["calls"])
        self.assertEqual(self.r["needsLast"]["focused"], "needs-head")
        self.assertIn("click:needs-row", self.r["needsFirst"]["calls"], "its task gone: the first Needs you entry")
        self.assertEqual(self.r["needsPo"][:2], ["project:p1", "reveal:po-chat"])
        self.assertIn("click:needs-row", self.r["poGone"], "a project with no PO any more")
        self.assertEqual(self.r["garbage"], {})

    def test_by_project_the_first_row_that_needs_you(self):
        b = self.r["byProject"]
        self.assertEqual(b["focused"], "needs-row")
        self.assertIsNone(b.get("tab"), "a row is focusable already")
        self.assertEqual(b["scrolled"], {"block": "start"})

    def test_every_former_entry_point_is_wired(self):
        home = INDEX[INDEX.index("document.getElementById('bar-home').addEventListener('click'"):]
        self.assertIn("e.preventDefault(); goHome();", home[:400], "the wordmark")
        hc = INDEX[INDEX.index("window.addEventListener('hashchange'"):]
        self.assertIn("if (location.hash !== '#needs') return;", hc[:300], "#needs while the page is open")
        self.assertIn("swShowNeeds();", hc[:300])
        boot = INDEX[INDEX.index("const back = deepTask ? null : ensUpd.restore();"):]
        self.assertIn("if (back.sb === 'needsyou') swShowNeeds();", boot, "a page saved on the old Needs you page")
        self.assertIn("} else if (location.hash === '#needs') {", boot, "#needs at load")
        self.assertRegex(boot, r"else if \(!deepTask && !phoneNow\(\) && window\.ensBootOpen !== false\) \{[\s\S]*?openDefaultConv\(\);",
                         "a desktop opens on the last conversation")
        self.assertIn("if (!phone) rememberConv(sel);", fn(INDEX, "function swRender("))
        # Nothing left of the old ways in.
        for gone in ("SB_DEST", "needsYouHtml", "notifItemHtml", "openAttentionItem", "notif-btn", "notif-tray",
                     "po-pill", "poPillClick", "renderPoPill", "PROJ_GROUPBY", "notif-all"):
            self.assertNotIn(gone, INDEX, gone)


if __name__ == "__main__":
    unittest.main()
