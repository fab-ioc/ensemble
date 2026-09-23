"""Point-link routing and the landing cue's restart and reduced-motion CSS in
session.html.

* a click on a balloon link — a point's "your message ↑" / "answer ↓" chip
  (pt-link) as much as a ref-chip, sup-link or ask-link — lands in this room
  through gotoMsg, asks the host to open another room's link when embedded in
  an iframe, and falls back to a plain navigation on its own; a modifier key
  or a non-primary button leaves it as an ordinary link;
* landing on the same balloon twice in a row (no redraw in between) restarts
  the cue: the class comes off and is re-added after a reflow, not just left
  on, so the CSS animation actually replays;
* the .msg.landed rule and its prefers-reduced-motion override use only the
  existing --selected-bg / --accent design tokens, never a hard-coded colour,
  and reduced motion sets a static look with no @keyframes animation.

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


def run_node(script: str) -> dict:
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is not installed")
class PointLinkRouting(unittest.TestCase):
    """refClickHandler is the one delegated click handler behind ref-chip,
    sup-link, cu-link, ask-link and pt-link — the point summary's "your
    message ↑" / "answer ↓" links and the ↑/↓ chips under a balloon."""

    PREAMBLE = r"""
let ROOM = 'room-current';
const out = { prevented: false, goto: null, posted: null };
function gotoMsg(mid) { out.goto = mid; }
let location = { origin: 'http://hub:8765', href: '' };
let window = {};
window.parent = window;   // top-level by default; a scenario may override
"""

    def run_click(self, scenario: str) -> dict:
        script = "\n".join([self.PREAMBLE, js_function("refClickHandler"), scenario,
                             "out.href = location.href; console.log(JSON.stringify(out));"])
        return run_node(script)

    def test_a_same_room_link_lands_without_leaving_the_page(self):
        out = self.run_click(r"""
const a = { dataset: { refMsg: 'P3-answer' }, href: 'https://hub/session?room=room-current&msg=P3-answer' };
const ev = { target: { closest: () => a }, button: 0, preventDefault: () => { out.prevented = true; } };
refClickHandler(ev);
""")
        self.assertTrue(out["prevented"])
        self.assertEqual(out["goto"], "P3-answer")
        self.assertIsNone(out["posted"])
        self.assertEqual(out["href"], "")

    def test_the_question_chip_and_the_answer_chip_route_the_same_way(self):
        # "answer ↓" (on the question) and "your message ↑" (on the answer)
        # are the same pt-link machinery in both directions: only the mid
        # decides where it lands, not which chip it came from.
        for mid in ("P3-question", "P3-answer"):
            out = self.run_click(f"""
const a = {{ dataset: {{ refMsg: '{mid}' }}, href: 'https://hub/session?room=room-current&msg={mid}' }};
const ev = {{ target: {{ closest: () => a }}, button: 0, preventDefault: () => {{ out.prevented = true; }} }};
refClickHandler(ev);
""")
            self.assertEqual(out["goto"], mid)

    def test_a_cross_room_link_inside_an_iframe_asks_the_host_to_open_it(self):
        out = self.run_click(r"""
const host = { postMessage: (msg, origin) => { out.posted = { msg, origin }; },
               location: { origin: 'http://hub:8765' } };
window.parent = host;
const a = { dataset: { refRoom: 'room-other', refMsg: 'm9' }, href: 'https://hub/session?room=room-other&msg=m9' };
const ev = { target: { closest: () => a }, button: 0, preventDefault: () => { out.prevented = true; } };
refClickHandler(ev);
""")
        self.assertIsNone(out["goto"])
        self.assertEqual(out["posted"], {"msg": {"type": "open-msg", "roomId": "room-other", "msg": "m9"},
                                          "origin": "http://hub:8765"})

    def test_a_cross_room_link_on_its_own_page_navigates_there(self):
        out = self.run_click(r"""
const a = { dataset: { refRoom: 'room-other', refMsg: 'm9' }, href: 'https://hub/session?room=room-other&msg=m9' };
const ev = { target: { closest: () => a }, button: 0, preventDefault: () => { out.prevented = true; } };
refClickHandler(ev);
""")
        self.assertIsNone(out["goto"])
        self.assertIsNone(out["posted"])
        self.assertEqual(out["href"], "https://hub/session?room=room-other&msg=m9")

    def test_a_modifier_key_or_a_secondary_button_is_left_as_an_ordinary_link(self):
        for extra in ("button: 1", "ctrlKey: true", "metaKey: true", "shiftKey: true", "altKey: true"):
            out = self.run_click(f"""
const a = {{ dataset: {{ refMsg: 'm1' }}, href: 'https://hub/session?room=room-current&msg=m1' }};
const ev = {{ target: {{ closest: () => a }}, button: 0, {extra}, preventDefault: () => {{ out.prevented = true; }} }};
refClickHandler(ev);
""")
            self.assertFalse(out["prevented"], extra)
            self.assertIsNone(out["goto"], extra)
            self.assertIsNone(out["posted"], extra)


@unittest.skipUnless(NODE, "node is not installed")
class LandingCueRestarts(unittest.TestCase):
    def test_navigating_to_the_same_balloon_again_restarts_the_cue(self):
        script = r"""
let GOTO = '', LANDED = null, _landing = false, CHAT_DRAWN = true, LAST_ITEMS = [];
let _cmtComposerOpen = false, _selBtn = null, STICK = false;
const FOLD = { open: new Set() };
let GROUP_OF_MID = new Map();
const setTimeout = () => 0;
const clearTimeout = () => {};
function showGotoNote() {}
function nearEnd() { return false; }
function showLatest() {}
const drawn = ['m1'];
const ops = [];
let nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    nodes.set(id, { dataset: { mid: id }, offsetTop: 0, offsetHeight: 20, offsetWidth: 1, style: {},
                    classList: { add: c => ops.push(['add', c]), remove: c => ops.push(['remove', c]),
                                 contains: c => ops.filter(o => o[1] === c).slice(-1)[0]?.[0] === 'add' } });
  }
  return nodes.get(id);
}
const box = { clientHeight: 400, scrollTop: 0,
              querySelectorAll: sel => sel === '.msg[data-mid]' ? drawn.map(node) : [] };
const $ = s => s === '#msgs' ? box : null;
""" + "\n".join([js_function(n) for n in ("openGroupOf", "landPending", "markLanded")]) + r"""
const out = {};
GOTO = 'm1';
landPending();
out.afterFirst = node('m1').classList.contains('landed');
GOTO = 'm1';   // the same link, clicked again
landPending();
out.afterSecond = node('m1').classList.contains('landed');
out.landedAdds = ops.filter(o => o[0] === 'add' && o[1] === 'landed').length;
out.landedRemoves = ops.filter(o => o[0] === 'remove' && o[1] === 'landed').length;
console.log(JSON.stringify(out));
"""
        out = run_node(script)
        self.assertTrue(out["afterFirst"])
        self.assertTrue(out["afterSecond"])
        # Two landings: each one removes the class (so a fresh reflow can
        # replay the CSS animation) before adding it back, not just a single
        # add left in place across both visits.
        self.assertEqual(out["landedAdds"], 2)
        self.assertEqual(out["landedRemoves"], 2)


class LandedCueCss(unittest.TestCase):
    """No Node needed: this checks the stylesheet text itself."""

    def block(self) -> str:
        start = SRC.index("/* Landed on from a link:")
        end = SRC.index("/* A link to another balloon,")
        return SRC[start:end]

    def test_the_cue_uses_only_existing_design_tokens(self):
        block = self.block()
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b", "a hard-coded colour crept into the landing cue")
        self.assertIn("var(--selected-bg)", block)
        self.assertIn("var(--accent)", block)

    def test_reduced_motion_is_static_with_no_keyframe_animation(self):
        block = self.block()
        i = block.index("@media (prefers-reduced-motion: reduce)")
        reduced = block[i:]
        self.assertIn("animation:none", reduced)
        self.assertIn("var(--selected-bg)", reduced)
        self.assertIn("var(--accent)", reduced)
        self.assertNotIn("@keyframes", reduced)

    def test_the_animated_rule_still_targets_the_whole_balloon(self):
        block = self.block()
        self.assertIn(".msg.landed { animation:landed", block)
        self.assertIn("@keyframes landed", block)


if __name__ == "__main__":
    unittest.main()
