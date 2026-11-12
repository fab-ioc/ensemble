"""An "Ask:" line that holds only a card's link reminds of that card (P321, #351).

The hub and the page make no card of it (tests/test_asks.py holds both to the
same parse); here, the page's markdown draws the line as the link's chip with
no "Ask:" before it, the way a pasted reference already reads, and keeps a
real question's line as written. The two copies of the links-only pattern in
session.html are the same.

Skipped without Node.
"""
from __future__ import annotations

import re
import shutil
import unittest

from tests.test_task_refs import ATTACH, SESSION, TASKCARD, block, const, fn, node

NODE = shutil.which("node")
URL = "http://127.0.0.1:8765/session?room=room-8d56cd21&msg=d6801a1e8ab0&part=ask:0"

JS = r"""
globalThis.location = new URL('http://127.0.0.1:8765/session?id=room-8d56cd21');
const ROOM = 'room-8d56cd21';
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
const refChipHtml = ref => ref.part.startsWith('ask:') ? '<a class="ref-chip card-jump">Question · PO</a>' : null;
const refChanged = () => {};
const fetch = () => new Promise(() => {});
const refHref = (room, msg) => '/session?room=' + encodeURIComponent(room) + '&msg=' + encodeURIComponent(msg);
%s
const U = %r;
console.log(JSON.stringify({
  plain: mdToHtml('Still waiting on you.\n\nAsk: ' + U),
  yesno: mdToHtml('**Ask (yes/no):** ' + U),
  listed: mdToHtml('1. **Ask:** ' + U),
  real: mdToHtml('Ask: Is ' + U + ' still right?'),
  md: mdToHtml('Ask: [the card](' + U + ')'),
  angle: mdToHtml('Ask: <' + U + '>.'),
  other: mdToHtml('Ask: https://example.org/help'),
}));
"""


@unittest.skipUnless(NODE, "node is not installed")
class AskReminderLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            TASKCARD, ATTACH, "const parkPointRefs = s => s;",
            block(SESSION, "// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            const(SESSION, "REF_URL_RE"), const(SESSION, "REF_A"), const(SESSION, "REF_MARK_RE"),
            const(SESSION, "REF_BLOCK_RE"), fn(SESSION, "stripRefBlocks"), fn(SESSION, "refOfUrl"),
            block(SESSION, "// ---- Task references: begin", "// ---- Task references: end"),
            block(SESSION, "// ---- Numbered points: begin", "// ---- Numbered points: end"),
            block(SESSION, "// ---- PO message names: begin", "// ---- PO message names: end"),
            fn(SESSION, "mdToHtml"), fn(SESSION, "itemsHtml")])
        cls.r = node(JS % (src, URL))

    def test_the_reminder_is_the_cards_chip_alone(self):
        for k in ("plain", "yesno", "listed", "md", "angle"):
            with self.subTest(form=k):
                html = self.r[k]
                self.assertIn('class="ref-chip card-jump"', html)
                self.assertNotRegex(html, r"(?i)\bask\b[^<]*:", "no Ask: marker before the chip")
                self.assertNotIn("http://", html)

    def test_a_question_with_a_link_in_it_keeps_its_words(self):
        self.assertIn("Ask:", self.r["real"])
        self.assertIn("still right?", self.r["real"])

    def test_a_link_alone_that_is_no_hub_message_stays_asked(self):
        self.assertIn("Ask:", self.r["other"])
        self.assertNotIn("card-jump", self.r["other"])

    def test_the_pages_two_patterns_are_the_same(self):
        lit = re.search(r"^  link: (/.*?/gi),\r?$", SESSION, re.M).group(1)
        self.assertIn(".replace(" + lit + ",", fn(SESSION, "mdToHtml"))
        gap = re.search(r"^  linkGap: (/.*?/i),\r?$", SESSION, re.M).group(1)
        self.assertIn(gap + ".test(rest)", fn(SESSION, "mdToHtml"))


if __name__ == "__main__":
    unittest.main()
