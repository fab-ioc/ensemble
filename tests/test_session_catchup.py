"""Coming back to a chat, one line says what changed since the reader last
read (session.html).

The page's own functions run in Node and these check that:

* the read point is the one kept for the room, and without one the person's
  own last message;
* the line names the decisions waiting, each task whose last word is done,
  blocked or a question, the answers to the person, and counts the rest;
* there is no line when nothing came after the point, or when fewer than
  three came and all are for the person;
* Mark read keeps the latest message as the point, and the line goes;
* in Just us only decisions and answers are named, the rest counted as hidden.

Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    assert m, f"{name} not found in session.html"
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def js_line(src: str, start: str) -> str:
    at = src.index(start)
    return src[at:src.index("\n", at) + 1]


def fold_block(src: str) -> str:
    return src[src.index("// ---- Folding a long conversation -"):src.index("// ---- Folding a long conversation: end")]


JS = r"""
const vm = require('vm');
const { code } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const store = new Map();
const ctx = {
  esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  localStorage: { getItem: k => store.has(k) ? store.get(k) : null, setItem: (k, v) => store.set(k, String(v)) },
  Date, JSON, console, clearTimeout: () => {},
};
vm.createContext(ctx);
vm.runInContext(code + `
  const ROOM = 'room-po';
  let LAST_ITEMS = null, CU_POINT = null, READ_MEM = null, READ_TIMER = 0, DRAWS = 0;
  const renderBubbles = () => { DRAWS++; };
  globalThis.t = { CHAT_NAMES, readPoint, catchUp, catchUpHtml, markRead, loadRead,
    set: (items, point) => { LAST_ITEMS = items; CU_POINT = point; },
    state: () => ({ point: CU_POINT, draws: DRAWS }) };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'ceo', po: 'claude', taskNo: id => ({ 'room-0804cfef': 26, 'room-11112222': 25, 'room-3333aaaa': 24 })[id] || null });
const out = {};

// A PO's transcript as the hub types it: the person's turns, hub inputs and the PO's replies.
const hub = (id, reportKind, taskId, ts, extra) => Object.assign({ id, from: 'user', to: '', kind: reportKind === 'digest' ? 'digest' : 'report',
  reportKind, taskId, reporter: 'claude', text: '[report] …', ts }, extra || {});
const po = (id, kind, text, ts, taskId) => ({ id, from: 'claude', to: '', text, ts, answers: { kind, taskId } });
const items = [
  { id: 's:0', from: 'user', to: '', kind: 'human', text: 'Go on.', ts: 100 },
  po('s:1', 'human', 'On it.', 110),
  hub('s:2', 'digest', '', 120), po('s:3', 'digest', 'Nothing new.', 121),
  hub('s:4', 'update', 'room-0804cfef', 130), po('s:5', 'report', 'Noted.', 131, 'room-0804cfef'),
  hub('s:6', 'completed', 'room-0804cfef', 140), po('s:7', 'report', 'Merged.', 141, 'room-0804cfef'),
  hub('s:8', 'question', 'room-11112222', 150), po('s:9', 'report', 'Decision needed: which layout?', 151, 'room-11112222'),
  hub('s:10', 'blocked', 'room-3333aaaa', 155), hub('s:11', 'update', 'room-3333aaaa', 156),
  po('s:12', 'human', 'The earlier answer, again.', 160),
  hub('s:13', 'digest', '', 170), po('s:14', 'digest', 'Still going.', 171),
];
const strip = cu => cu && Object.assign({}, cu, { ts: undefined });

// The point: kept for the room, else the person's own last message.
out.kept = T.readPoint(items, { id: 's:3', ts: 121 });
out.keptByTime = T.readPoint(items, { id: 'gone:9', ts: 145 });
out.fallback = T.readPoint(items, null);
out.none = T.readPoint(items.slice(1), null);

out.line = strip(T.catchUp(items, T.readPoint(items, { id: 's:3', ts: 121 }), false));
out.html = T.catchUpHtml(T.catchUp(items, { i: 3, ts: 0, mine: false }, false), mid => '/session?room=room-po&msg=' + mid);
out.justUs = strip(T.catchUp(items, { i: 3, ts: 0, mine: false }, true));
out.justUsHtml = T.catchUpHtml(T.catchUp(items, { i: 3, ts: 0, mine: true }, true), mid => '#' + mid);
// Answered: a decision the person replied to after it is an answer, not waiting.
const answered = items.concat([{ id: 's:15', from: 'user', to: '', kind: 'human', text: 'Layout B.', ts: 180 }]);
out.answered = strip(T.catchUp(answered, { i: 3, ts: 0, mine: false }, false));

// No line: nothing after the point; two balloons, both for the person.
out.nothing = T.catchUp(items, { i: items.length - 1, ts: 0, mine: false }, false);
out.fewForYou = T.catchUp([items[0], po('a', 'human', 'Yes.', 1), po('b', 'report', 'Decision needed: ok?', 2)], { i: 0, ts: 0, mine: true }, false);
out.threeForYou = !!T.catchUp([items[0], po('a', 'human', 'Yes.', 1), po('b', 'human', 'And.', 2), po('c', 'human', 'Also.', 3)], { i: 0, ts: 0, mine: true }, false);
out.twoWithTask = !!T.catchUp([items[0], po('a', 'human', 'Yes.', 1), hub('b', 'completed', 'room-0804cfef', 2)], { i: 0, ts: 0, mine: true }, false);

// A room: a PO's unaddressed reply and one to the person are answers; a report is a task's.
const room = [
  { id: 'r0', from: 'user', to: 'all', text: 'Go.', ts: 1 },
  { id: 'r1', from: 'claude@room-0804cfef', to: 'claude', kind: 'report', reportKind: 'completed', taskId: 'room-0804cfef', text: 'Done.', ts: 2 },
  { id: 'r2', from: 'claude', to: '', text: 'Merged it.', ts: 3 },
  { id: 'r3', from: 'claude', to: 'claude@room-0804cfef', text: 'Thanks.', ts: 4 },
];
out.room = strip(T.catchUp(room, T.readPoint(room, null), false));

// Mark read: the latest message is the point, kept for the room, and the line goes.
T.set(items, { id: 's:3', ts: 121 });
T.markRead();
out.marked = T.loadRead();
out.markedState = T.state();
T.set(items.concat([{ id: 'rot1', divider: {} }]), null);
T.markRead();
out.markedSkipsLandmark = T.loadRead();
out.store = [...store.keys()];
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class CatchUpLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = fold_block(SRC) + js_line(SRC, "const READ_KEY = ") + js_function(SRC, "loadRead") + js_function(SRC, "markRead")
        run = subprocess.run([NODE, "-e", JS], input=json.dumps({"code": code}), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        assert run.returncode == 0, run.stderr
        cls.out = json.loads(run.stdout)

    def test_the_read_point(self):
        o = self.out
        self.assertEqual(o["kept"], {"i": 3, "ts": 121, "mine": False})
        # A kept message no longer shown: found by its time.
        self.assertEqual(o["keptByTime"]["i"], 7)
        # None kept: the person's own last message.
        self.assertEqual(o["fallback"], {"i": 0, "ts": 100, "mine": True})
        self.assertIsNone(o["none"])

    def test_the_line_names_what_changed(self):
        line = self.out["line"]
        self.assertEqual(line["first"], 4)
        self.assertEqual(line["parts"], [
            {"text": "1 decision waiting", "mid": "s:9"},
            {"text": "#26 done", "mid": "s:6"},
            {"text": "#25 asks", "mid": "s:8"},
            {"text": "#24 blocked", "mid": "s:10"},
            {"text": "1 answer to you", "mid": "s:12"},
        ])
        # Eleven after the point, five named: s:4 s:5 s:7 s:11 s:13 s:14 are counted.
        self.assertEqual(line["rest"], 6)
        html = self.out["html"]
        self.assertIn("Since you last read", html)
        self.assertIn('<a class="cu-link" href="/session?room=room-po&amp;msg=s:9" data-ref-msg="s:9">1 decision waiting</a>', html)
        self.assertIn("#26 done</a> · <a", html)
        self.assertIn("checks and reports folded.", html)
        self.assertIn('class="cu-read"', html)

    def test_an_answered_decision_is_an_answer(self):
        parts = [p["text"] for p in self.out["answered"]["parts"]]
        self.assertEqual(parts, ["#26 done", "#25 asks", "#24 blocked", "2 answers to you"])

    def test_no_line(self):
        self.assertIsNone(self.out["nothing"])
        self.assertIsNone(self.out["fewForYou"])
        self.assertTrue(self.out["threeForYou"])
        self.assertTrue(self.out["twoWithTask"])

    def test_a_room(self):
        room = self.out["room"]
        self.assertEqual([p["text"] for p in room["parts"]], ["#26 done", "1 answer to you"])
        self.assertEqual(room["rest"], 1)

    def test_just_us(self):
        ju = self.out["justUs"]
        self.assertEqual([p["text"] for p in ju["parts"]], ["1 decision waiting", "1 answer to you"])
        self.assertEqual(ju["rest"], 9)
        self.assertIn("Since your last message", self.out["justUsHtml"])
        self.assertIn("+ 9 task messages hidden.", self.out["justUsHtml"])

    def test_mark_read(self):
        o = self.out
        self.assertEqual(o["marked"], {"id": "s:14", "ts": 171})
        self.assertIsNone(o["markedState"]["point"])
        self.assertEqual(o["markedState"]["draws"], 1)
        self.assertEqual(o["markedSkipsLandmark"], {"id": "s:14", "ts": 171})
        self.assertEqual(o["store"], ["cd-chat-read:room-po"])


if __name__ == "__main__":
    unittest.main()
