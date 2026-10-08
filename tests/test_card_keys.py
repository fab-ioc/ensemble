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
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {} };
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { askKeyAction, askDraftsLoad, askDraftsSave, askCardHtml, parseAsks, pointMaps, CHAT_NAMES, ASK_DRAFT, MOD_ENTER };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const out = {};
const k = (o) => T.askKeyAction(Object.assign({ key: 'Enter', ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, o));
out.keys = {
  ctrl: k({ ctrlKey: true }), meta: k({ metaKey: true }), plain: k({}), shift: k({ shiftKey: true }),
  ctrlShift: k({ ctrlKey: true, shiftKey: true }), ctrlAlt: k({ ctrlKey: true, altKey: true }), ctrlA: k({ key: 'a', ctrlKey: true }),
};
const mem = () => { const s = new Map(); return { s, getItem: x => s.has(x) ? s.get(x) : null, setItem: (x, v) => s.set(x, String(v)), removeItem: x => s.delete(x) }; };
const st = mem();
out.emptyAtStart = T.ASK_DRAFT.size;    // no sessionStorage in Node: nothing, and no error
T.askDraftsSave(new Map([['s:1:0', 'half a thought'], ['s:1:2', '  ']]), st);
out.stored = JSON.parse(st.getItem('cd-askdraft:room-po'));
out.loaded = [...T.askDraftsLoad(st)];
T.askDraftsSave(new Map(), st);
out.afterEmpty = st.getItem('cd-askdraft:room-po');
st.setItem('cd-askdraft:room-po', JSON.stringify({ 's:1:0': 5, 's:1:1': 'ok' }));
out.badValue = [...T.askDraftsLoad(st)];
st.setItem('cd-askdraft:room-po', '{not json');
out.badJson = T.askDraftsLoad(st).size;
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

    def test_a_draft_is_kept_per_room_until_emptied(self):
        g = self.got
        self.assertEqual(g["emptyAtStart"], 0)
        self.assertEqual(g["stored"], {"s:1:0": "half a thought", "s:1:2": "  "}, "any unsent text is kept, spaces too")
        self.assertEqual(g["loaded"], [["s:1:0", "half a thought"], ["s:1:2", "  "]])
        self.assertIsNone(g["afterEmpty"], "nothing left: the key is removed")
        self.assertEqual(g["badValue"], [["s:1:1", "ok"]])
        self.assertEqual(g["badJson"], 0)

    def test_the_tooltips_name_the_shortcut(self):
        self.assertEqual(self.got["mod"], "Ctrl+Enter", "no navigator: not a Mac")
        for card in self.got["cards"]:
            self.assertRegex(card, r'class="qa-send"[^>]*title="Send your (answer|comment alone) \(Ctrl\+Enter\)"')
        self.assertIn('title="Ctrl+Enter sends"', self.got["cards"][2], "the open ask's answer box")
        self.assertIn("alone with Send comment (Ctrl+Enter)", self.got["cards"][0])


if __name__ == "__main__":
    unittest.main()
