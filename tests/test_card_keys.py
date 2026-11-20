"""A quick-answer card's keys and kept comment (#191, GitHub issue 14).

* the key: Ctrl+Enter and ⌘+Enter are the card's Send; Enter alone, or with
  Shift or Alt, types (a new line) as before;
* the draft: a comment is kept per room (sessionStorage, as the message
  box's draft), read back after a reload, and dropped once emptied or sent;
  a stored value that is not text is ignored;
* the card: its Send button's and comment's tooltips name the shortcut.

The page in Chrome (typing through redraws, Ctrl+Enter sending once, a
reload) is tests/test_card_focus_cdp.py.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from tests.test_asks import THREE
from tests.test_po_chat_clean import NODE, NOUN, SRC, fold_block

NODE_JS = r"""
const vm = require('vm');
const { code, three } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ctx = { esc, attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t, withPathAbbrevs: (t, f) => f(),
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {}, replyKey: () => '' };
// The card's words (#238, tests/test_card_text.py): plain here.
ctx.cardTextHtml = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: ctx.cardTextHtml(s), links: [] }); ctx.cardWords = s => String(s ?? '');
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { askKeyAction, askCardHtml, parseAsks, pointMaps, CHAT_NAMES, MOD_ENTER };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const out = {};
const k = (o) => T.askKeyAction(Object.assign({ key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, o));
out.keys = {
  ctrl: k({ ctrlKey: true }), meta: k({ metaKey: true }), plain: k({}), shift: k({ shiftKey: true }),
  ctrlShift: k({ ctrlKey: true, shiftKey: true }), ctrlAlt: k({ ctrlKey: true, altKey: true }), ctrlA: k({ key: 'a', ctrlKey: true }),
};
const m = { id: 's:1', from: 'claude', kind: 'human', text: three, ts: Date.now() / 1000 - 60 };
const none = T.pointMaps(null);
out.cards = T.parseAsks(three).map(a => T.askCardHtml(m, a, 3, none));
out.mod = T.MOD_ENTER;
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class CardKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, "-e", NODE_JS], input=json.dumps({"code": NOUN + "\n" + fold_block(SRC), "three": THREE}),
                           capture_output=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, r.stderr[-3000:]
        cls.got = json.loads(r.stdout.strip().splitlines()[-1])

    def test_ctrl_or_cmd_enter_sends_and_enter_types(self):
        self.assertEqual(self.got["keys"], {"ctrl": "send", "meta": "send", "plain": None, "shift": None,
                                            "ctrlShift": None, "ctrlAlt": None, "ctrlA": None})

    def test_the_tooltips_name_the_shortcut(self):
        self.assertEqual(self.got["mod"], "Ctrl+Enter", "no navigator: not a Mac")
        # #408: a card has no comment field of its own; its words go in the send box (Reply).
        for card in self.got["cards"]:
            self.assertNotIn("<textarea", card)
            self.assertNotIn("Send comment", card)
            self.assertIn('class="qa-reply"', card)

if __name__ == "__main__":
    unittest.main()
