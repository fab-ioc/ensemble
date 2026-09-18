"""A link to a balloon lands on it in the chat page (session.html), and the
hub's write-out of a link is hidden from the balloon without hiding a person's
own words.

The page's landing and solo-transcript code runs in Node against a small
stand-in for the chat and the hub, and checks that:

* a link to a solo turn older than the drawn window widens the window and
  lands, even though the link is found in the middle of the first render;
* the same for a turn of the session a PO was rotated from;
* a turn that does not exist says "not found" once the wider window is drawn;
* a redraw that replaces the landed balloon's node keeps it marked;
* stripRefBlocks removes exactly the hub's blocks.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
ATTACH = (ROOT / "static" / "attach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", SRC, re.M)
    assert m, f"{name} not found in session.html"
    return SRC[m.start():SRC.index("\n}\n", m.start()) + 3]


def js_const(name: str) -> str:
    m = re.search(rf"^const {name} = .*$", SRC, re.M)
    assert m, f"{name} not found in session.html"
    return m.group(0)


HARNESS = r"""
const out = { fetches: [], notes: [] };
let GOTO = '', SOLO_WANT = null, LANDED = null, _landing = false, CHAT_DRAWN = false, SOLO_AGAIN = false, SOLO_FIRST_TRIES = 0;
let SOLO_TURN_COUNT = -1, SOLO_FETCHING = false, SOLO_SID = 's-new', SOLO_AGENT = 'claude', SOLO_ROT = null, SOLO_ROTS = [];
const SOLO_PREV = new Map();
const PREV_TURNS_SHOWN = 60;
let _cmtComposerOpen = false, _selBtn = null, STICK = false, LAST_ITEMS = [];
const FOLD = { open: new Set() };
let GROUP_OF_MID = new Map();
// A retry of the transcript runs at once; the landed mark's fade never ends.
const setTimeout = (f, ms, ...a) => { if (f === renderSolo) setImmediate(() => f(...a)); return 0; };
const clearTimeout = () => {};
function showGotoNote(t) { if (t) out.notes.push(t); }
function nearEnd() { return false; }
function showLatest() {}
function withHubRows(items) { return items; }
const ROOM_OBJ = null;
let nodes = new Map(), drawn = [];
function node(id) {
  if (!nodes.has(id)) {
    const s = new Set();
    nodes.set(id, { dataset: { mid: id }, offsetTop: 0, offsetHeight: 20, offsetWidth: 1, style: {},
                    classList: { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c) } });
  }
  return nodes.get(id);
}
const box = { clientHeight: 400, scrollTop: 0,
              querySelectorAll: sel => sel === '.msg[data-mid]' ? drawn.map(node) : [] };
const $ = s => s === '#msgs' ? box : null;
function renderBubbles(items) { LAST_ITEMS = items; drawn = items.filter(m => !m.divider).map(m => m.id); markLanded(); landPending(); }
const turns = n => Array.from({ length: n }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: 'turn ' + i }));
let TURNS = {};
let FAILS = [];   // {sid, skip, times}: after `skip` good fetches of sid, `times` fail
let _soloStatKey = '';
function fetch(url) {
  // The transcript's size and time never change: a poll never asks for it again.
  if (url.endsWith('?stat=1')) return Promise.resolve({ json: () => Promise.resolve({ size: 1000, mtime: 1 }) });
  out.fetches.push(url);
  const sid = decodeURIComponent(url.split('/api/session/')[1].split('?')[0]);
  const f = FAILS.find(x => x.sid === sid);
  if (f && f.skip) f.skip--;
  else if (f && f.times > 0) { f.times--; return Promise.reject(new Error('network')); }
  return Promise.resolve({ json: () => Promise.resolve({ turns: TURNS[sid] || [] }) });
}
const settle = async () => { for (let i = 0; i < 80; i++) await new Promise(r => setImmediate(r)); };
"""

SCENARIOS = {
    "current": r"""
TURNS = { 's-new': turns(300) };
GOTO = 's-new:5';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "rotated": r"""
TURNS = { 's-new': turns(4), 's-old': turns(200) };
SOLO_ROT = { toSessionId: 's-new', fromSessionId: 's-old', n: 1 }; SOLO_ROTS = [SOLO_ROT];
GOTO = 's-old:3';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "two-rotations": r"""
TURNS = { 's-new': turns(4), 's-mid': turns(200), 's-old': turns(200) };
SOLO_ROTS = [{ toSessionId: 's-mid', fromSessionId: 's-old', n: 1 },
             { toSessionId: 's-new', fromSessionId: 's-mid', n: 2 }];
SOLO_ROT = SOLO_ROTS[1];
GOTO = 's-old:3';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "missing": r"""
TURNS = { 's-new': turns(300) };
GOTO = 's-new:999';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "fails-once": r"""
TURNS = { 's-new': turns(300) };
FAILS = [{ sid: 's-new', skip: 1, times: 1 }];
GOTO = 's-new:5';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "fails-always": r"""
TURNS = { 's-new': turns(300) };
FAILS = [{ sid: 's-new', skip: 1, times: 99 }];
GOTO = 's-new:5';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "first-fails-once": r"""
TURNS = { 's-new': turns(300) };
FAILS = [{ sid: 's-new', skip: 0, times: 1 }];
GOTO = 's-new:5';
checkSolo().then(() => checkSolo());
""",
    "first-fails-always": r"""
TURNS = { 's-new': turns(300) };
FAILS = [{ sid: 's-new', skip: 0, times: 99 }];
GOTO = 's-new:5';
checkSolo().then(() => checkSolo());
""",
    "prev-fails": r"""
TURNS = { 's-new': turns(4), 's-old': turns(200) };
FAILS = [{ sid: 's-old', skip: 0, times: 99 }];
SOLO_ROT = { toSessionId: 's-new', fromSessionId: 's-old', n: 1 }; SOLO_ROTS = [SOLO_ROT];
GOTO = 's-old:3';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
}

TAIL = r"""
(async () => {
  await settle();
  out.drawn = drawn.length;
  out.first = drawn[0];
  out.goto = GOTO;
  out.landed = LANDED && LANDED.mid;
  out.marked = drawn.filter(id => node(id).classList.contains('landed'));
  // A redraw that puts new nodes in place (a chip resolved) keeps the mark.
  LANDED && (LANDED.at -= 500);
  nodes = new Map();
  renderBubbles(LAST_ITEMS);
  out.remarked = drawn.filter(id => node(id).classList.contains('landed'));
  out.delay = out.remarked.length ? node(out.remarked[0]).style.animationDelay : '';
  console.log(JSON.stringify(out));
})();
"""


def run_scenario(name: str) -> dict:
    code = "\n".join([HARNESS, ATTACH, js_const("REF_BLOCK_RE"), js_const("TASK_BLOCK_RE")] + [js_function(n) for n in (
        "stripRefBlocks", "soloItems", "prevSessionItems", "soloOwns", "soloWantFailed", "checkSolo",
        "renderSolo", "openGroupOf", "landPending", "markLanded")]
        + [SCENARIOS[name], TAIL])
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is not installed")
class LandOnALinkedBalloon(unittest.TestCase):
    def test_an_older_turn_than_the_window_widens_it_and_lands(self):
        out = run_scenario("current")
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["landed"], "s-new:5")
        self.assertEqual(out["marked"], ["s-new:5"])
        self.assertGreater(out["drawn"], 120)
        self.assertEqual(len(out["fetches"]), 2, out["fetches"])

    def test_an_older_turn_of_the_rotated_session_lands(self):
        out = run_scenario("rotated")
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["marked"], ["s-old:3"])

    def test_a_turn_from_two_rotations_ago_lands(self):
        out = run_scenario("two-rotations")
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["marked"], ["s-old:3"])
        self.assertEqual(out["first"], "s-old:3")

    def test_a_failed_fetch_of_the_wider_window_is_tried_again(self):
        out = run_scenario("fails-once")
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["marked"], ["s-new:5"])
        self.assertEqual(len(out["fetches"]), 3, out["fetches"])

    def test_a_window_that_never_loads_says_so_and_stops(self):
        for name in ("fails-always", "prev-fails"):
            out = run_scenario(name)
            self.assertEqual(out["notes"], ["The linked message could not be loaded. Reload the page to try again."], name)
            self.assertEqual(out["goto"], "", name)
            self.assertIsNone(out["landed"], name)
            self.assertLessEqual(len(out["fetches"]), 8, (name, out["fetches"]))

    def test_a_chat_whose_first_load_fails_is_loaded_again_for_its_link(self):
        # The chat is opened through the stat poll, twice, with an unchanged
        # transcript: only the page's own retry fetches it again.
        out = run_scenario("first-fails-once")
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["marked"], ["s-new:5"])
        out = run_scenario("first-fails-always")
        self.assertEqual(out["notes"], ["The linked message could not be loaded. Reload the page to try again."])
        self.assertEqual(out["goto"], "")
        self.assertEqual(len(out["fetches"]), 3, out["fetches"])

    def test_a_turn_that_is_not_there_is_not_found_after_widening(self):
        out = run_scenario("missing")
        self.assertEqual(out["notes"], ["The linked message was not found in this conversation."])
        self.assertEqual(out["goto"], "")
        self.assertIsNone(out["landed"])

    def test_a_redraw_keeps_the_landed_mark_where_the_fade_is(self):
        out = run_scenario("current")
        self.assertEqual(out["remarked"], ["s-new:5"])
        self.assertRegex(out["delay"], r"^-\d+ms$")


@unittest.skipUnless(NODE, "node is not installed")
class HideTheHubsWriteOut(unittest.TestCase):
    def strip(self, texts):
        code = "\n".join([ATTACH, js_const("REF_BLOCK_RE"), js_const("TASK_BLOCK_RE"), js_function("stripRefBlocks"),
                          f"console.log(JSON.stringify({json.dumps(texts)}.map(stripRefBlocks)));"])
        res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_the_blocks_go_and_a_persons_own_lines_stay(self):
        url = "http://hub-host:8765/session?room=room-1a2b3c4d&msg=0123456789ab"
        gone = "http://h/session?room=room-deadbeef&msg=ffffffffffff"
        written = (f"see {url} and {gone}\n\n[ref {url}] from claude in \"Docs\" at 2026-09-14 10:00:\n"
                   f"> Merged.\n> \n> Tests pass.\n\n[ref {gone}] not found: no message ffffffffffff in room-deadbeef on this hub")
        own = [f"My own note\n\n[ref {url}] this is ordinary text\n> keep this",
               f"My own note\n\n[ref {url}] from claude in \"Docs\" at 2026-09-14 10:00:\n> keep this"]
        self.assertEqual(self.strip([written] + own), [f"see {url} and {gone}"] + own)


if __name__ == "__main__":
    unittest.main()
