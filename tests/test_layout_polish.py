"""Layout A, the mockup's look (#126): the bar, the PO's header and the chat's box.

* the bar: search is "Search or jump to…" with its "/" key, not focused on
  load, sunken and about 300px on the right on a desktop; the crumbs are at
  the body size; the bell is a drawn icon that follows the text colour;
* the PO's header (``poHeadHtml``, run in Node): "Project · PO", one quiet
  line (agent and model, the project, its open tasks, the run chip, your
  asks), Resume only while it is not running, and Switch, the PO's task and
  Pop out in a ⋯ menu whose items keep their ``data-po``;
* the chat's box (session.html): one card with a mouse, + Add an ask ·
  Attach · Send (primary), a short placeholder per kind of chat; your
  balloons on the right, an agent's under its avatar; a phone keeps its own.

The list on the left is tests/test_task_switcher.py. Node parts are skipped
without Node.
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
# The word for a board (static/noun.js): the page code below calls noun()/nounText().
NOUN = (Path(__file__).resolve().parent.parent / "static" / "noun.js").read_text(encoding="utf-8")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


def media_block(src: str, head: str) -> str:
    """The text of one @media block, by brace counting."""
    i = src.index(head)
    j = src.index("{", i) + 1
    depth = 1
    while depth:
        c = src[j]
        depth += (c == "{") - (c == "}")
        j += 1
    return src[i:j]


class TheBar(unittest.TestCase):
    def test_search_says_what_it_does_and_its_key(self):
        box = re.search(r'<input id="search"[^>]*>', INDEX).group(0)
        self.assertIn('placeholder="Search or jump to…"', box)
        self.assertIn('aria-keyshortcuts="/"', box)
        self.assertNotIn("autofocus", box)            # "/" could never bring you to a box already focused
        self.assertIn('<kbd class="search-kbd" aria-hidden="true">/</kbd>', INDEX)
        # The page's real shortcut is "/", which the kbd names.
        self.assertIn("if (ev.key === '/') { ev.preventDefault(); $('#search').focus(); }", INDEX)

    def test_search_sits_right_and_quiet_on_a_desktop(self):
        self.assertIn("body.mid .search-wrap { flex: 0 1 300px;", INDEX)
        self.assertIn("body.mid .search-wrap + .bar-gap { flex: 0 0 0;", INDEX)   # only the first gap grows
        self.assertIn("body.mid #search { background: var(--surface-sunken); }", INDEX)
        # The key hint goes while you type, focus, or a search state shows.
        self.assertRegex(INDEX, r"body\.mid \.search-wrap:is\(:has\(#search:focus\), :has\(#search:not\(:placeholder-shown\)\), "
                                r":has\(\.search-state:not\(\[hidden\]\)\)\) \.search-kbd \{ display: none; \}")

    def test_crumbs_are_body_size(self):
        self.assertRegex(INDEX, r"#proj-go \{ color: var\(--fg\); font-size: var\(--fs-300\); font-weight: 400;")
        self.assertRegex(INDEX, r"#bar-crumbs \.bar-crumb \{[^}]*color: var\(--fg-subtle\); font-size: var\(--fs-300\);")
        self.assertRegex(INDEX, r"\.crumb-here \{ color: var\(--fg\); font-size: var\(--fs-300\); font-weight: 500;")

    def test_the_bar_has_no_bell_and_no_pill(self):
        # #135: the list's Needs you is the signal, and each PO is a list row.
        for gone in ('id="notif-btn"', 'id="notif-tray"', 'id="po-pill"', '<svg class="bell"'):
            self.assertNotIn(gone, INDEX)

JS = r"""
const WORKFLOW_KEYS = ['backlog', 'todo', 'inprogress', 'inreview', 'done'];
%s
let ALL_ROWS = [];
const pj = { id: 'p1', name: 'Ensemble Dashboard', poRoomId: 'po-1',
             sessions: ['po-1', 'a', 'b', 'c', 'd', 'e'].map(roomId => ({ roomId })) };
const r = (roomId, more) => ({ roomId, headless: true, workflow: 'inprogress', isLive: true, ...more });
ALL_ROWS = [r('po-1'), r('a'), r('b', { workflow: 'inreview' }), r('c', { workflow: 'done' }),
            r('d', { draft: true }), r('e', { archived: true }), r('x')];   // x: another project's
const po = { roomId: 'po-1', sessionId: 's', isLive: true, status: 'idle', label: 'Ensemble Dashboard PO',
             members: [{ agent: 'claude', model: 'claude-opus' }], points: { open: 3, delivered: 8 } };
const log = {
  live: poHeadHtml(pj, po, false),
  stopped: poHeadHtml(pj, { ...po, isLive: false, points: {} }, true),
  count: poOpenCount(pj, ALL_ROWS),
};
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class ThePoHeader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            NOUN, re.search(r"^const esc = .*$", INDEX, re.M).group(0),
            fn(INDEX, "function workflowOf("), fn(INDEX, "function pointsCountText("),
            fn(INDEX, "function pointsCountTip("), fn(INDEX, "function runChip("), fn(INDEX, "function shortModel("),
            fn(INDEX, "function poAgent("), fn(INDEX, "function poOpenCount("), fn(INDEX, "function poMenuItems("),
            re.search(r"^const DOCK_MENU_INTERIM = .*$", INDEX, re.M).group(0), fn(INDEX, "function poHeadHtml(")])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "pohead.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_open_tasks_are_the_projects_unfinished_ones(self):
        self.assertEqual(self.r["count"], 2)          # a and b: not the PO, done, draft, archived or another's

    def test_one_quiet_line_says_what_is_true(self):
        h = self.r["live"]
        self.assertIn('<span class="av claude">c</span>', h)
        self.assertIn('<div class="po-name">Ensemble Dashboard · PO</div>', h)
        # A part a span (CSS draws the " · "), so a narrow header cuts the project's name first.
        self.assertIn('<div class="po-sub"><span>claude opus</span><span class="po-sub-proj">project Ensemble Dashboard</span>'
                      '<span>2 tasks open</span><span class="run"><span class="dot idle"></span>idle</span><span class="po-pts"', h)
        self.assertIn(".po-sub > * + *::before { content: \"·\";", INDEX)
        self.assertIn(".po-sub > .po-sub-proj { flex-shrink: 1000; }", INDEX)
        self.assertNotIn("Ensemble Dashboard PO", h)  # the row's label is gone from it

    def test_resume_is_the_one_button_the_rest_is_in_a_menu(self):
        live, stopped = self.r["live"], self.r["stopped"]
        self.assertNotIn('data-po="resume"', live)
        self.assertIn('<button class="po-btn def" data-po="resume"', stopped)
        for h in (live, stopped):
            menu = re.search(r'<span class="po-menu" role="menu" hidden>(.*?)</span>', h).group(1)
            # #148: Pop out is the panel's own ⋯ (View Mode › Window); the two
            # left wait here (data-interim): the task's actions moved into
            # the panel's ⋯ with Dock v0.9.0's app items (#150), these can follow.
            self.assertEqual(re.findall(r'data-po="(\w+)"', menu), ["switch", "open"])
            self.assertEqual(menu.count('role="menuitem"'), 2)
            self.assertIn('data-po="more" data-interim="dock-menu" title="More actions · for now here: they move into the panel’s ⋯ menu, as the task’s have" aria-label="More actions for the PO" aria-haspopup="menu" aria-expanded="false">⋯</button>', h)
            self.assertNotIn('data-po="popout"', h)
        self.assertNotIn('data-po="close"', live)
        self.assertIn('data-po="close"', stopped)     # a drawer keeps its ×

    def test_the_menu_opens_closes_and_holds_its_header(self):
        click = INDEX[INDEX.index("const act = ev.target.closest('#po-panel .po-head [data-po]');"):]
        click = click[:click.index("// A peek closes when you click beside it")]
        self.assertIn("if (act) { await poHeadClick(act); return; }\n  poMenusClose();", click)   # any other click closes it
        self.assertIn("if (what === 'more') poMenuOpen(head, act.getAttribute('aria-expanded') !== 'true');", INDEX)
        # (what it holds, and for whom: tests/test_po_needs_you.py runs it)
        self.assertIn("if (head._html !== hh && (!samePo || !head.querySelector('.po-menu:not([hidden])')))", INDEX)
        # Esc closes the menu before anything else, and gives its button focus back.
        keys = INDEX[INDEX.index("// Esc closes the PO's ⋯ menu first"):]
        keys = keys[:keys.index("// Arrows move through")]
        self.assertIn("head.querySelector('[data-po=\"more\"]').focus();", keys)
        self.assertIn("ev.stopImmediatePropagation()", keys)
        self.assertIn("ev.target.closest('.pr-menu, .po-menu')", INDEX)   # and arrows move in it


class TheChatBox(unittest.TestCase):
    def test_the_row_under_the_words(self):
        row = SESSION[SESSION.index('<div class="ed-row">'):]
        row = row[:row.index("</div>")]
        self.assertEqual(re.findall(r'<button[^>]* id="(\w[\w-]*)"', row), ["add-point", "attach", "send"])
        self.assertIn('<input type="file" id="attach-input" accept="image/*" multiple hidden>', row)
        wire = SESSION[SESSION.index("$('#attach').addEventListener"):]
        wire = wire[:wire.index("$('#att-chips')")]
        self.assertIn("$('#attach-input').click()", wire)
        self.assertIn("ATT.add(files, false)", wire)                 # the pasted and dropped images' own path

    def test_a_short_placeholder_per_chat(self):
        body = fn(SESSION, "function composePlaceholder(")
        self.assertIn("composePlaceholder(!!po, solo);", fn(SESSION, "function chatNamesFor("))
        js = body + "\nconst out = [[true, false], [false, false], [false, true], [true, true]].map(a => { composePlaceholder(...a); return I.placeholder; });\nconsole.log(JSON.stringify(out));"
        if not NODE:
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "ph.cjs"
            p.write_text("const I = {}; const $ = () => I;\n" + js, encoding="utf-8")
            out = json.loads(subprocess.run([NODE, str(p)], capture_output=True, text=True, encoding="utf-8", timeout=60).stdout)
        self.assertEqual(out, [
            "Message the PO — @codex or @claude to direct it · #18 links a task · Ctrl+Enter sends",
            "Message the agents — @codex or @claude to direct it · #18 links a task · Ctrl+Enter sends",
            "Message the agent — #18 links a task · Ctrl+Enter sends",
            "Message the PO — #18 links a task · Ctrl+Enter sends",
        ])

    def test_the_card_and_the_balloons_are_a_mouse_only(self):
        css = media_block(SESSION, "@media not all and (pointer: coarse) {\n    #compose")
        self.assertIn("border:1px solid var(--border-strong); border-radius:var(--r-300);", css)
        self.assertIn("#compose #input { border:0;", css)
        self.assertIn("field-sizing:content", css)
        self.assertIn("#send { height:32px; padding:0 var(--s-400); border-color:var(--accent); background:var(--accent); color:var(--accent-fg);", css)
        self.assertIn(".msg.user:not(.hub) { align-self:flex-end; max-width:85%;", css)
        self.assertIn(".msg.user.pending { border:1px dashed var(--border-strong); }", css)   # a send on its way keeps its edge
        self.assertIn('.msg.claude:not(.user) > .from::before { content:"C"; background:var(--agent-claude-bg); color:var(--agent-claude-fg); }', css)
        self.assertIn('.msg.codex:not(.user) > .from::before { content:"X"; background:var(--agent-codex-bg); color:var(--agent-codex-fg); }', css)
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,6}\b")             # tokens only
        # The phone's block is untouched by it.
        phone = media_block(SESSION, "@media (pointer: coarse) {")
        self.assertNotIn("align-self:flex-end", phone)


if __name__ == "__main__":
    unittest.main()
