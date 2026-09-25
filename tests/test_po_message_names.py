"""A PO message's id (pm-1a2b3c4d) is for the tools; a balloon names it.

session.html's "PO message names" block and its markdown run in Node: an id of
one of this chat's PO messages reads as its name ("opten PO's question of
09-25 18:57"), a link to its balloon with the id in the tooltip, in prose, in
a heading and in code; a folded row's line says the name; an id the chat does
not hold, and a tool's argument (id=pm-…), stay as written.

Skipped without Node.
"""
from __future__ import annotations

import re
import shutil
import unittest

from tests.test_task_refs import ATTACH, SESSION, block, const, fn, node

NODE = shutil.which("node")

JS = r"""
globalThis.location = new URL('http://hub-host:8765/session?id=room-po');
const ROOM = 'room-po';
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
const refChipHtml = () => null;
const refChanged = () => {};
const fetch = () => new Promise(() => {});
const refHref = (room, msg) => '/session?room=' + encodeURIComponent(room) + '&msg=' + encodeURIComponent(msg);
%s
const at = new Date(2026, 8, 25, 18, 57).getTime() / 1000;
pmIndex([
  { kind: 'pomsg', id: 'pm-e00a1c63', name: "opten PO's question of 09-25 18:57", fromProjectName: 'opten', poKind: 'question', ts: at },
  { kind: 'pomsg', id: 'pm-0000abcd', fromProjectName: 'Strats', poKind: 'answer', ts: at },
  { kind: 'human', id: 'pm-11112222', text: 'not a PO message' },
]);
const first = ({
  heading: mdToHtml('## Strats answers to pm-e00a1c63 (0DTE Slab + 14DTE)'),
  code: mdToHtml('Re `pm-e00a1c63`: done.'),
  made: mdToHtml('See pm-0000abcd.'),
  unknown: mdToHtml('Old one: pm-deadbeef, and pm-11112222.'),
  toolArg: mdToHtml('I ran ensemble_read_message id=pm-e00a1c63 and replyTo=pm-e00a1c63.'),
  plain: pmPlain('Strats answers to pm-e00a1c63; pm-deadbeef stays'),
  labelled: mdToHtml('[pm-e00a1c63](https://example.com/report)'),
  target: mdToHtml('[both](https://example.com/?ids=pm-e00a1c63,pm-0000abcd) and https://x.org/a,pm-0000abcd'),
  rowLink: pmPlain('[pm-e00a1c63](https://example.com/?ids=pm-e00a1c63,pm-0000abcd)'),
});
// Only the answer is drawn (the question is older than the turns shown): the room still knows it.
const question = { kind: 'pomsg', id: 'pm-aaaa0001', name: "opten PO's question of 09-24 10:00" };
const answer = { kind: 'pomsg', id: 'pm-bbbb0002', name: "Strats PO's answer of 09-25 09:00", text: 'Re pm-aaaa0001' };
pmIndex([answer], { messages: [question, answer] });
const older = { html: mdToHtml(answer.text), sig: PM_SIG };
// The same number of messages known, but others: the cache's key changes.
pmIndex([question]); const sigA = PM_SIG;
pmIndex([answer]); const sigB = PM_SIG;
console.log(JSON.stringify(Object.assign(first, { older, sigA, sigB })));
"""


@unittest.skipUnless(NODE, "node is not installed")
class PoMessageNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            ATTACH,
            block(SESSION, "// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            const(SESSION, "REF_URL_RE"), const(SESSION, "REF_A"), const(SESSION, "REF_MARK_RE"),
            const(SESSION, "REF_BLOCK_RE"), fn(SESSION, "stripRefBlocks"), fn(SESSION, "refOfUrl"),
            block(SESSION, "// ---- Task references: begin", "// ---- Task references: end"),
            block(SESSION, "// ---- Numbered points: begin", "// ---- Numbered points: end"),
            block(SESSION, "// ---- PO message names: begin", "// ---- PO message names: end"),
            fn(SESSION, "mdToHtml"), fn(SESSION, "itemsHtml")])
        cls.r = node(JS % src)

    def links(self, html):
        return re.findall(r'<a class="pm-link"[^>]*>(.*?)</a>', html)

    def test_a_known_id_is_its_name_linking_to_its_balloon(self):
        html = self.r["heading"]
        self.assertTrue(html.startswith("<h4>Strats answers to <a class=\"pm-link\""), html)
        self.assertIn('href="/session?room=room-po&amp;msg=pm-e00a1c63"', html)
        self.assertIn('data-ref-room="room-po" data-ref-msg="pm-e00a1c63"', html)
        self.assertIn("(pm-e00a1c63)", html, "the id is in the tooltip")
        self.assertEqual(self.links(html), ["opten PO's question of 09-25 18:57"])
        self.assertIn("(0DTE Slab + 14DTE)", html)

    def test_in_code_too(self):
        self.assertEqual(self.links(self.r["code"]), ["opten PO's question of 09-25 18:57"])
        self.assertNotIn("<code", self.r["code"])

    def test_a_message_without_a_stored_name_is_named_the_same_way(self):
        self.assertEqual(self.links(self.r["made"]), ["Strats PO's answer of 09-25 18:57"])

    def test_what_stays_as_written(self):
        self.assertEqual(self.links(self.r["unknown"]), [])
        self.assertIn("pm-deadbeef", self.r["unknown"])
        self.assertIn("pm-11112222", self.r["unknown"], "only a PO message is named")
        self.assertEqual(self.links(self.r["toolArg"]), [], "a tool's argument keeps its id")

    def test_a_folded_row_says_the_name(self):
        self.assertEqual(self.r["plain"], "Strats answers to opten PO's question of 09-25 18:57; pm-deadbeef stays")

    def test_a_markdown_link_keeps_its_target_and_nests_no_link(self):
        self.assertEqual(self.links(self.r["labelled"]), [])
        self.assertIn('href="https://example.com/report"', self.r["labelled"])
        self.assertIn(">opten PO's question of 09-25 18:57</a>", self.r["labelled"])
        self.assertEqual(self.r["labelled"].count("<a "), 1)
        target = self.r["target"]
        self.assertEqual(self.links(target), [])
        self.assertIn("ids=pm-e00a1c63,pm-0000abcd", target)
        self.assertIn("https://x.org/a,pm-0000abcd", target)
        self.assertNotIn("pm-link", target)
        self.assertEqual(self.r["rowLink"],
                         "[opten PO's question of 09-25 18:57](https://example.com/?ids=pm-e00a1c63,pm-0000abcd)")

    def test_an_older_question_than_the_turns_drawn_is_named_from_the_room(self):
        self.assertEqual(self.links(self.r["older"]["html"]), ["opten PO's question of 09-24 10:00"])
        self.assertIn("pm-aaaa0001", self.r["older"]["sig"])

    def test_the_cache_key_changes_with_which_messages_are_known(self):
        self.assertNotEqual(self.r["sigA"], self.r["sigB"])
        self.assertIn("+ '|' + PM_SIG;", fn(SESSION, "renderBubbles"))

    def test_a_link_to_an_older_po_message_draws_the_whole_transcript(self):
        land = fn(SESSION, "landPending")
        self.assertIn("const pm = !t && SOLO_SID && PM_IDX.has(mid);", land)
        self.assertIn("SOLO_WANT = pm ? { sid: SOLO_SID, n: 0, mid }", land)

    def test_the_page_uses_them(self):
        md = fn(SESSION, "mdToHtml")
        self.assertIn("s = parkPmRefs(s, chips);", md)
        self.assertIn("pmLinkHtml(body.trim())", md)
        render = fn(SESSION, "renderBubbles")
        self.assertIn("pmIndex(items, ROOM_OBJ);", render)
        self.assertIn("plain: t => pmPlain(refPlain(t))", render)
        self.assertIn("a.pm-link')", SESSION, "a click lands on the balloon")


if __name__ == "__main__":
    unittest.main()
