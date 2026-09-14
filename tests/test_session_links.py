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
let GOTO = '', SOLO_WANT = null, LANDED = null, _landing = false, CHAT_DRAWN = false, SOLO_AGAIN = false;
let SOLO_TURN_COUNT = -1, SOLO_FETCHING = false, SOLO_SID = 's-new', SOLO_AGENT = 'claude', SOLO_ROT = null;
let SOLO_PREV = { sid: '', items: [] };
const PREV_TURNS_SHOWN = 60;
let _cmtComposerOpen = false, _selBtn = null, STICK = false, LAST_ITEMS = [];
const FOLD = { open: new Set() };
const setTimeout = () => 0, clearTimeout = () => {};
function showGotoNote(t) { if (t) out.notes.push(t); }
function nearEnd() { return false; }
function showLatest() {}
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
function fetch(url) {
  out.fetches.push(url);
  const sid = decodeURIComponent(url.split('/api/session/')[1].split('?')[0]);
  return Promise.resolve({ json: () => Promise.resolve({ turns: TURNS[sid] || [] }) });
}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
"""

SCENARIOS = {
    "current": r"""
TURNS = { 's-new': turns(300) };
GOTO = 's-new:5';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "rotated": r"""
TURNS = { 's-new': turns(4), 's-old': turns(200) };
SOLO_ROT = { toSessionId: 's-new', fromSessionId: 's-old', n: 1 };
GOTO = 's-old:3';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
    "missing": r"""
TURNS = { 's-new': turns(300) };
GOTO = 's-new:999';
renderSolo(SOLO_SID, SOLO_AGENT, true);
""",
}

TAIL = r"""
(async () => {
  await settle();
  out.drawn = drawn.length;
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
    code = "\n".join([HARNESS, js_const("REF_BLOCK_RE")] + [js_function(n) for n in (
        "stripRefBlocks", "soloItems", "prevSessionItems", "renderSolo", "landPending", "markLanded")]
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
        code = "\n".join([js_const("REF_BLOCK_RE"), js_function("stripRefBlocks"),
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
