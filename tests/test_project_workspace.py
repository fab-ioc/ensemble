"""Each project keeps its own workspace (#386, P374, P376): the store.

In Node, static/workspace.js (PdWorkspace) alone: what a switch of context
puts away and brings back, pins, the 8-tab cap (least recently shown,
unpinned, goes), and a stored state it cannot use. The page's use of it is
tested in Chrome (tests/test_project_workspace_cdp.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")

JS = r"""
const W = require(process.argv[1]);
const chat = (room) => ({ id: 'chat:' + room, kind: 'chat', room });
const file = (p, pid) => ({ id: 'file:' + p, kind: 'file', path: p, ctx: { kind: 'project', projectId: pid } });
const o = (item, owner) => ({ item, owner });
const out = {};

// Ensemble open with #1 and a file; switching to Trading puts both away.
let s = W.load(null);
const e1 = chat('r1'), ef = file('/e/README.md', 'E'), t1 = chat('t1');
out.first = W.settle(s, 'E', [o(e1, 'E'), o(ef, 'E')], 1);
out.toT = W.settle(s, 'T', [o(e1, 'E'), o(ef, 'E')], 2);
out.toTagain = W.settle(s, 'T', [], 3);              // settled again: nothing to do
// In Trading, #t1 opens; back to Ensemble: t1 goes, e1 and the file come back.
out.toE = W.settle(s, 'E', [o(t1, 'T')], 4);
out.afterE = { cur: s.cur, E: W.putAway(s, 'E'), T: W.putAway(s, 'T') };

// A pinned one stays open everywhere and is never put away.
W.pin(s, ef.id, true);
out.pinned = W.settle(s, 'T', [o(e1, 'E'), o(ef, 'E')], 5);
W.pin(s, ef.id, false);
out.unpinned = W.settle(s, 'T', [o(ef, 'E')], 6);
out.unpinnedE = W.putAway(s, 'E').map(x => x.id).sort();

// A panel whose owner is not known yet is left where it is; no context: nothing.
out.unknown = W.settle(s, 'T', [o(chat('x'), null)], 7);
out.none = W.settle(s, '', [o(e1, 'E')], 8);

// Opened on Trading's screen though Ensemble's (adopt): it stays there, settled
// again, and goes with Ensemble's at the next switch.
const s5 = W.load(null);
W.settle(s5, 'T', [], 1);
const ex = file('/e/NOTES.md', 'E');
W.adopt(s5, ex.id);
out.adoptStays = W.settle(s5, 'T', [o(ex, 'E')], 2);
out.adoptCounts = W.overflow(s5, 'T', [o(ex, 'E')], 'chat:new');
out.adoptSwitch = W.settle(s5, 'U', [o(ex, 'E')], 3);
out.adoptAfter = { here: s5.here, E: W.putAway(s5, 'E').map(x => x.id) };
out.adoptBack = W.settle(s5, 'T', [o(ex, 'E')], 4);

// A window's item keeps out: true, a stack's front tab front: true; a reload's state comes back whole.
const s2 = W.load(null);
W.settle(s2, 'E', [], 1);
W.settle(s2, 'T', [o({ ...e1, out: true }, 'E'), o({ ...chat('r2'), front: true }, 'E')], 2);
const back = W.load(JSON.stringify(s2));
out.reload = { cur: back.cur, E: W.putAway(back, 'E') };

// The cap: 8 of Ensemble's open, a 9th closes the least recently shown unpinned.
const s3 = W.load(null);
const open = [];
for (let i = 1; i <= 8; i++) { const c = chat('c' + i); open.push(o(c, 'E')); W.touch(s3, c.id, 100 + i); }
W.touch(s3, 'chat:c1', 500);                         // c1 shown last: c2 is the oldest
W.pin(s3, 'chat:c2', true);                          // pinned: c3 goes instead
out.capNone = W.overflow(s3, 'E', open.slice(0, 7), 'chat:new');
out.cap = W.overflow(s3, 'E', open, 'chat:new');
out.capAgain = W.overflow(s3, 'E', open, 'chat:c4');   // already open: no 9th
out.capOther = W.overflow(s3, 'E', [...open, o(chat('t9'), 'T')], 'chat:c5');  // another project's: not counted
out.capKeep = W.overflow(s3, 'E', open, 'chat:new', ['chat:c3']);
// Only tabs count: 7 tabs and a window, then one more tab: no 9th tab, nothing
// closes; 8 tabs and a window: a tab goes, never the window.
const win = o({ ...chat('w1'), out: true }, 'E');
W.touch(s3, 'chat:w1', 1);                           // the oldest of all
out.capWinNone = W.overflow(s3, 'E', [...open.slice(0, 7), win], 'chat:new');
out.capWin = W.overflow(s3, 'E', [...open, win], 'chat:new');
// Review 2: the PO's tab counts (fixed: never closed); a floating panel is no
// tab; with nothing being added (after a switch), more than 8 still closes one.
out.capFixed = W.overflow(s3, 'E', open.slice(0, 7), 'chat:new', null, 1);
const flo = o({ ...chat('f1'), float: true }, 'E');
W.touch(s3, 'chat:f1', 2);
out.capFloatNone = W.overflow(s3, 'E', [...open.slice(0, 7), flo], 'chat:new');
out.capFloat = W.overflow(s3, 'E', [...open, flo], 'chat:new');
W.pin(s3, 'chat:t8', true);                          // another project's, pinned: counts, stays
out.capFitNone = W.overflow(s3, 'E', open.slice(0, 6), null, null, 1);
out.capFit = W.overflow(s3, 'E', [...open, o(chat('t8'), 'T')], null, null, 1);
// Review 3: a closed window (hidden) neither counts nor closes; seven pinned
// tabs of other projects, the PO's and one of this project's: that one goes.
const hid = o({ ...chat('h1'), hidden: true }, 'E');
W.touch(s3, 'chat:h1', 1);
out.capHiddenNone = W.overflow(s3, 'E', [...open.slice(0, 7), hid], 'chat:new');
out.capHidden = W.overflow(s3, 'E', [...open, hid], 'chat:new');
const others = [];
for (let i = 1; i <= 7; i++) { const c = chat('p' + i); others.push(o(c, 'T')); W.pin(s3, c.id, true); W.touch(s3, c.id, 1); }
out.capFitPinned = W.overflow(s3, 'E', [...others, o(chat('c4'), 'E')], null, null, 1);
for (let i = 1; i <= 7; i++) W.pin(s3, 'chat:p' + i, false);
W.pin(s3, 'chat:t8', false);
for (let i = 1; i <= 8; i++) W.pin(s3, 'chat:c' + i, true);
out.capAllPinned = W.overflow(s3, 'E', open, 'chat:new');

// Bounded: contexts past MAX_CTXS go, the least recently left first; seen
// keeps only what is open or remembered; drop forgets an item.
const s4 = W.load(null);
for (let i = 0; i < W.MAX_CTXS + 5; i++) W.settle(s4, 'p' + (i + 1), [o(chat('k' + i), 'p' + i)], i + 1);
out.ctxCount = Object.keys(s4.ctxs).length;
out.oldestGone = !('p0' in s4.ctxs) && ('p' + (W.MAX_CTXS + 3)) in s4.ctxs;
W.touch(s4, 'chat:ghost', 1); W.prune(s4, []);
out.ghostGone = !('chat:ghost' in s4.seen);
W.drop(s4, 'chat:k' + (W.MAX_CTXS + 3));
out.dropped = W.putAway(s4, 'p' + (W.MAX_CTXS + 3)).length;

// What cannot be used is left out.
out.bad = [W.load('not json'), W.load({ v: 2 }), W.load({ v: 1, cur: 3, pins: [1, 'a', 'a'], seen: { a: 'x', b: 2 },
  ctxs: { E: { at: 'x', items: [{ id: 'chat:a', kind: 'chat', room: 'a' }, { id: 'chat:a', kind: 'chat', room: 'a' }, { id: 'f', kind: 'file', path: 'p' }, null] }, '': {} } })];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs node")
class ProjectWorkspaceStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", JS, str(ROOT / "static" / "workspace.js")],
                           capture_output=True, text=True, timeout=30)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.o = json.loads(r.stdout)

    def test_a_switch_puts_away_and_brings_back(self):
        o = self.o
        self.assertEqual(o["first"], {"away": [], "back": []})
        self.assertEqual(o["toT"]["away"], ["chat:r1", "file:/e/README.md"])
        self.assertEqual(o["toT"]["back"], [])
        self.assertEqual(o["toTagain"], {"away": [], "back": []})
        self.assertEqual(o["toE"]["away"], ["chat:t1"])
        self.assertEqual([x["id"] for x in o["toE"]["back"]], ["chat:r1", "file:/e/README.md"])
        self.assertEqual(o["toE"]["back"][1]["ctx"], {"kind": "project", "projectId": "E"})
        # What came back is open now, not remembered twice; Trading keeps t1.
        self.assertEqual(o["afterE"]["cur"], "E")
        self.assertEqual(o["afterE"]["E"], [])
        self.assertEqual([x["id"] for x in o["afterE"]["T"]], ["chat:t1"])

    def test_a_pin_stays_open_in_every_context(self):
        self.assertEqual(self.o["pinned"]["away"], ["chat:r1"])
        self.assertEqual(self.o["unpinned"]["away"], ["file:/e/README.md"])
        self.assertEqual(self.o["unpinnedE"], ["chat:r1", "file:/e/README.md"])

    def test_unknown_owner_and_no_context_change_nothing(self):
        self.assertEqual(self.o["unknown"], {"away": [], "back": []})
        self.assertEqual(self.o["none"], {"away": [], "back": []})

    def test_a_panel_opened_here_stays_until_the_switch(self):
        o = self.o
        self.assertEqual(o["adoptStays"], {"away": [], "back": []})
        self.assertIsNone(o["adoptCounts"])
        self.assertEqual(o["adoptSwitch"]["away"], ["file:/e/NOTES.md"])
        self.assertEqual(o["adoptAfter"], {"here": [], "E": ["file:/e/NOTES.md"]})
        # Back on Trading, nothing of Ensemble's comes along; open again, it goes.
        self.assertEqual(o["adoptBack"]["away"], ["file:/e/NOTES.md"])

    def test_a_window_and_a_reload(self):
        self.assertEqual(self.o["reload"], {"cur": "T", "E": [{"id": "chat:r1", "kind": "chat", "room": "r1", "out": True},
                                                              {"id": "chat:r2", "kind": "chat", "room": "r2", "front": True}]})

    def test_the_ninth_tab_closes_the_least_recently_shown_unpinned(self):
        o = self.o
        self.assertIsNone(o["capNone"])
        self.assertEqual(o["cap"], "chat:c3")
        self.assertIsNone(o["capAgain"])
        self.assertIsNone(o["capOther"])
        self.assertEqual(o["capKeep"], "chat:c4")
        self.assertIsNone(o["capAllPinned"])

    def test_a_window_is_no_tab_for_the_cap(self):
        # Review 1: a window neither counts nor closes for the 8 tabs.
        self.assertIsNone(self.o["capWinNone"])
        self.assertEqual(self.o["capWin"], "chat:c3")

    def test_the_po_tab_counts_and_a_float_does_not(self):
        # Review 2: 7 tasks' tabs and the PO's, then one more: one closes (not
        # the PO's); a floating panel neither counts nor closes; a pinned tab
        # of another project brought along counts, and the fit after a switch
        # closes one of this project's.
        o = self.o
        self.assertEqual(o["capFixed"], "chat:c3")
        self.assertIsNone(o["capFloatNone"])
        self.assertEqual(o["capFloat"], "chat:c3")
        self.assertIsNone(o["capFitNone"])
        self.assertEqual(o["capFit"], "chat:c3")

    def test_a_closed_window_is_no_tab_and_a_full_switch_still_fits(self):
        # Review 3: a window the browser closed (hidden) neither counts nor
        # closes; 7 pinned tabs brought along, the PO's and one of this
        # project's: that one goes, even in front.
        o = self.o
        self.assertIsNone(o["capHiddenNone"])
        self.assertEqual(o["capHidden"], "chat:c3")
        self.assertEqual(o["capFitPinned"], "chat:c4")

    def test_it_stays_bounded(self):
        o = self.o
        self.assertLessEqual(o["ctxCount"], 60)
        self.assertTrue(o["oldestGone"])
        self.assertTrue(o["ghostGone"])
        self.assertEqual(o["dropped"], 0)

    def test_what_it_cannot_use_is_left_out(self):
        a, b, c = self.o["bad"]
        self.assertEqual(a, {"v": 1, "cur": "", "pins": [], "here": [], "seen": {}, "ctxs": {}})
        self.assertEqual(b, a)
        self.assertEqual(c["cur"], "")
        self.assertEqual(c["pins"], ["a"])
        self.assertEqual(c["seen"], {"b": 2})
        self.assertEqual(c["ctxs"], {"E": {"at": 0, "items": [{"id": "chat:a", "kind": "chat", "room": "a"}]}})


if __name__ == "__main__":
    unittest.main()
