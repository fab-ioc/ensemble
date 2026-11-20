"""P409 (#398): a message typed in the box answers the open card its chip
names, and pick-any cards (several options ticked, sent together).

Hub side: the marker and ``multiSelect``, the ticks an answer takes, the
box's answer recorded on its card and typed to the agent as the card's
Comment would be, taken back when the send is refused, and Undo."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import ensemble_tools  # noqa: E402
import points  # noqa: E402
import sends  # noqa: E402
from test_points import _World, http, turn  # noqa: E402
from test_po_chat_clean import NODE, NOUN, SRC, fold_block  # noqa: E402

PICK = "Ask (pick any): Which export formats?\n- CSV\n- JSON\n- XML"


class TheMarker(unittest.TestCase):
    def test_pick_any_marks_a_card_of_two_options_or_more(self):
        a = asks.parse(PICK)[0]
        self.assertTrue(a["multi"])
        self.assertEqual([o["label"] for o in a["options"]], ["CSV", "JSON", "XML"])
        self.assertNotIn("multi", asks.parse("Ask (pick any): Which?\n- Only one")[0], "one option: a plain card")
        self.assertNotIn("multi", asks.parse("Ask: Which?\n- A\n- B")[0])

    def test_multiselect_is_validated(self):
        q = asks.validated([{"question": "Which formats?", "multiSelect": True,
                             "options": [{"label": "CSV"}, {"label": "JSON"}]}])[0]
        self.assertTrue(q["multi"])
        self.assertNotIn("multi", asks.validated([{"question": "Which?", "multiSelect": False,
                                                   "options": [{"label": "A"}, {"label": "B"}]}])[0])
        for bad in ({"question": "Ship?", "yesno": True, "multiSelect": True},
                    {"question": "Which?", "multiSelect": True, "options": [{"label": "A"}]},
                    {"question": "Which?", "multiSelect": "yes", "options": [{"label": "A"}, {"label": "B"}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                asks.validated([bad])

    def test_the_ticks_come_back_in_the_cards_order(self):
        a = asks.parse(PICK)[0]
        self.assertEqual(asks.check_many(a, ["xml", "CSV", "csv"]), ["CSV", "XML"])
        self.assertEqual(asks.check_many(a, []), [])
        self.assertIsNone(asks.check_many(a, ["PDF"]), "not one of its options")
        self.assertIsNone(asks.check_many(a, ["CSV", ""]))
        self.assertIsNone(asks.check_many(a, "CSV"), "a list only")
        self.assertIsNone(asks.check_many(asks.parse("Ask: Which?\n- A\n- B")[0], ["A"]), "not a pick-any card")


class _Cards(_World):
    def _resume(self, sent):
        def resume(h, room, text="", to="", key="", quiet=False):
            sent.append((text, to, key))
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}
        return resume

    def _ask(self, rid, questions):
        room = chatroom.get_room(rid, public=False)
        part = next(p for p in room["participants"] if p["identity"] == "claude")
        ctx = {"room": room, "identity": "claude", "part": part, "projectId": ""}
        handler = types.SimpleNamespace(_ring_recipients=lambda *a: [], _ring_report=lambda *a: [])
        with mock.patch.object(ensemble_tools, "_project_po", return_value=None):
            return ensemble_tools._ask(ctx, {"questions": questions}, handler)["messageId"]


class PickAny(_Cards):
    def test_ticks_are_typed_together_and_kept_as_a_list(self):
        rid = self.solo_room()
        mid = self._ask(rid, [{"question": "Which export formats?", "multiSelect": True,
                               "options": [{"label": "CSV"}, {"label": "JSON"}, {"label": "XML"}]}])
        msg = next(m for m in chatroom.get_room(rid)["messages"] if m["id"] == mid)
        self.assertTrue(msg["asks"][0]["multi"])
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "options": ["PDF"]})[0], 400)
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "options": []})[0], 400,
                             "no tick and no comment")
            status, r = http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "options": ["XML", "CSV"],
                                               "comment": "XML for the old reports"})
        self.assertEqual(status, 200, r)
        self.assertEqual([(t.split("\n\n[point")[0], to, k) for t, to, k in sent],
                         [("Re “Which export formats?”: CSV; XML\n\nXML for the old reports", "", f"ask:{mid}:0")])
        got = points.load(rid)["asks"][mid]["0"]
        self.assertEqual((got["option"], got["options"]), ("CSV; XML", ["CSV", "XML"]))
        self.assertEqual(asks.open_in(chatroom.get_room(rid)), [])

    def test_the_po_reads_pick_any_on_the_question(self):
        rid = self.team_room()
        room = chatroom.get_room(rid, public=False)
        ctx = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": "p"}
        got = {}
        po = {"roomId": "room-po", "identity": "po", "title": "PO"}
        with mock.patch.object(ensemble_tools, "_projects", return_value={"p": {"id": "p"}}), \
                mock.patch.object(ensemble_tools, "_project_po", return_value=po), \
                mock.patch.object(ensemble_tools, "_report", side_effect=lambda c, a, h, **k: got.update(a) or {"messageId": "m1"}):
            ensemble_tools._ask(ctx, {"questions": [{"question": "Which formats?", "multiSelect": True,
                                                     "options": [{"label": "CSV"}, {"label": "JSON"}]}]},
                                types.SimpleNamespace(headers={}))
        self.assertIn("1. Which formats? (pick any)\n   - CSV\n   - JSON", got["text"])

    def test_a_comment_alone_answers_it(self):
        rid = self.solo_room()
        mid = self._ask(rid, [{"question": "Which?", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}]}])
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            status, r = http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "options": [], "comment": "Neither yet"})
        self.assertEqual(status, 200, r)
        self.assertTrue(sent[0][0].startswith("Re “Which?”: Neither yet\n"), sent[0])

    def test_the_open_cards_say_pick_any(self):
        rid = self.solo_room()
        mid = self._ask(rid, [{"question": "Which?", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}]},
                              {"question": "Ship?", "yesno": True}])
        asks.forget()
        got = {a["n"]: a.get("multi", False) for a in asks.open_in(chatroom.get_room(rid))}
        self.assertEqual(got, {0: True, 1: False})
        self.assertTrue(mid)


class TheBoxAnswers(_Cards):
    def _po(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "[rotation] You are the PO.", self.t0))
        mid = self._ask(rid, [{"question": "Ship it tonight?", "yesno": True}, {"question": "Any notes?"}])
        return rid, mid

    def test_the_message_is_the_cards_answer(self):
        rid, mid = self._po()
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "After 22:00, and tell ops", "key": "k1",
                                                  "answers": {"mid": mid, "n": 1}})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["answered"], {"mid": mid, "n": 1})
        self.assertIn(mid, r["points"]["asks"], "the reply carries the answer for the card")
        self.assertEqual(len(sent), 1)
        text, to, key = sent[0]
        self.assertTrue(text.startswith("Re “Any notes?”: After 22:00, and tell ops"), text)
        self.assertEqual((to, key), ("", "k1"), "a single agent's chat: typed in, no addressee")
        got = points.load(rid)["asks"][mid]["1"]
        self.assertEqual((got["option"], got["comment"], got["box"], got["key"]), ("", "After 22:00, and tell ops", True, "k1"))
        self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [0], "the other card stays open")

    def test_a_reply_from_a_threads_box_answers_the_card(self):
        # #408: Reply aims a thread's box at a card; its answer goes as the
        # card's (``Re “q”:``), never with the thread's ``Re Pn`` before it.
        rid, mid = self._po()
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)), \
                mock.patch.object(dashboard.threads, "is_threaded", lambda r, projects=None: r == rid), \
                mock.patch.object(dashboard.threads, "reply_text", lambda r, room, t, text: f"Re P7: {text}"), \
                mock.patch.object(dashboard.threads, "note_send", lambda *a, **k: None):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "Ship after 22:00", "key": "kt",
                                                  "thread": "P7", "answers": {"mid": mid, "n": 0}})
            self.assertEqual(status, 200, r)
            self.assertEqual(r["answered"], {"mid": mid, "n": 0})
            status, r = http("/api/room/resume", {"roomId": rid, "text": "and a plain line", "key": "kt2",
                                                  "thread": "P7"})
            self.assertEqual(status, 200, r)
            status, r = http("/api/room/resume", {"roomId": rid, "text": "late", "key": "kt3",
                                                  "thread": "P7", "answers": {"mid": mid, "n": 0}})
            self.assertNotIn("answered", r, "answered already: a plain thread reply")
        self.assertTrue(sent[0][0].startswith("Re “Ship it tonight?”: Ship after 22:00"), sent[0])
        self.assertTrue(sent[1][0].startswith("Re P7: and a plain line"), sent[1])
        self.assertTrue(sent[2][0].startswith("Re P7: late"), sent[2])
        self.assertEqual([s["thread"] for s in sends.view(rid)], ["P7", "P7", "P7"], "the replies stay in the thread")

    def test_a_card_answered_meanwhile_leaves_a_plain_message(self):
        rid, mid = self._po()
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            self.assertEqual(http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "option": "Yes"})[0], 200)
            status, r = http("/api/room/resume", {"roomId": rid, "text": "and one more thing", "key": "k2",
                                                  "answers": {"mid": mid, "n": 0}})
            self.assertEqual(status, 200, r)
            self.assertNotIn("answered", r)
            for bad in ({"mid": mid, "n": 9}, {"mid": "nope", "n": 0}, {"mid": mid, "n": "x"}, "junk"):
                status, r = http("/api/room/resume", {"roomId": rid, "text": "plain", "key": f"k-{bad}", "answers": bad})
                self.assertEqual(status, 200, r)
                self.assertNotIn("answered", r)
        self.assertTrue(sent[1][0].startswith("and one more thing"), sent[1])
        self.assertNotIn("box", points.load(rid)["asks"][mid]["0"])

    def test_a_refused_send_opens_the_card_again(self):
        rid, mid = self._po()
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("no")):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "Yes, ship", "key": "k3",
                                                  "answers": {"mid": mid, "n": 0}})
        self.assertEqual(status, 400, r)
        self.assertNotIn(mid, points.load(rid)["asks"], "refused: the card may be answered again")
        self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [0, 1])

    def test_a_kept_send_discarded_opens_the_card_again(self):
        rid, mid = self._po()

        def fail(h, room, text="", to="", key="", quiet=False):
            sends.mark(room["id"], [key], "failed", "the agent stopped")
            raise dashboard.StartRoomError("the agent stopped")
        with mock.patch.object(dashboard.Handler, "_resume_room", fail):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "Yes, ship", "key": "k6",
                                                  "answers": {"mid": mid, "n": 0}})
        self.assertEqual((status, r.get("kept")), (400, True), r)
        self.assertTrue(points.load(rid)["asks"][mid]["0"]["box"], "kept for Retry: still the card's answer")
        self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [1])
        # Discarded (as after a hub restart, from the sends the hub keeps): never sent, the card is open again.
        self.assertTrue(dashboard.discard_pending(rid, "k6"))
        self.assertNotIn(mid, points.load(rid)["asks"])
        self.assertEqual([a["n"] for a in asks.open_in(chatroom.get_room(rid))], [0, 1])

    def test_undo_of_a_read_box_answer_says_sent_by_mistake(self):
        rid, mid = self._po()
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            self.assertEqual(http("/api/room/resume", {"roomId": rid, "text": "Yes, ship", "key": "k4",
                                                       "answers": {"mid": mid, "n": 0}})[0], 200)
            status, r = http("/api/room/ask/undo", {"roomId": rid, "mid": mid, "n": 0})
        self.assertEqual((status, r.get("followUp")), (200, True), r)
        self.assertEqual(sent[1][2], "undo:k4", "the box's own send")
        self.assertIn("I sent it by mistake.", sent[1][0])
        self.assertTrue(points.load(rid)["asks"][mid]["0"]["undone"])

    def test_undo_of_an_unread_team_box_answer_gives_its_words_back(self):
        rid = self.team_room()
        mid = self._ask(rid, [{"question": "Ship it tonight?", "yesno": True}])
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room", self._resume(sent)):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "Yes, after 22:00", "key": "k5",
                                                  "answers": {"mid": mid, "n": 0}})
        self.assertEqual(status, 200, r)
        self.assertEqual(sent[0][1], "claude", "a team: to the card's asker")
        with mock.patch.object(dashboard, "withdraw_send", return_value=True):
            status, r = http("/api/room/ask/undo", {"roomId": rid, "mid": mid, "n": 0})
        self.assertEqual((status, r.get("withdrawn"), r.get("text")), (200, True, "Yes, after 22:00"), r)
        self.assertNotIn(mid, points.load(rid)["asks"], "the card is open again")


NODE_JS = r"""
const vm = require('vm');
const { code, pick } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ctx = { esc, attSplit: t => ({ paths: [], words: t }), stripRefBlocks: t => t, withPathAbbrevs: (t, f) => f(),
  ROOM: 'room-po', SOLO_MODE: true, pointsChanged: () => {}, pointsNote: () => {}, URL,
  replyKey: () => '',
  refHref: (room, mid, part) => `/session?room=${room}&msg=${mid}${part ? '&part=' + part : ''}` };
ctx.cardTextHtml = esc; ctx.hubUrlWords = s => String(s ?? ''); ctx.cardText = s => ({ html: esc(s), words: String(s ?? ''), links: [] }); ctx.cardWords = s => String(s ?? '');
vm.createContext(ctx);
vm.runInContext(code + `
  globalThis.t = { parseAsks, askCardHtml, pointMaps, CHAT_NAMES, ASK_TICKS, boxAnswerStrip, askBoxMid,
    setItems: v => { LAST_ITEMS = v; }, setPoints: v => { POINTS = v; } };`, ctx);
const T = ctx.t;
Object.assign(T.CHAT_NAMES, { operator: 'sam', po: 'claude', taskNo: () => null });
const m = { id: 's:1', from: 'claude', kind: 'human', text: pick, ts: Date.now() / 1000 };
const a = T.parseAsks(pick)[0];
const none = T.pointMaps(null);
const out = { multi: !!a.multi };
out.fresh = T.askCardHtml(m, a, 1, none);
T.ASK_TICKS.set('s:1:0', new Set(['CSV', 'XML']));
out.ticked = T.askCardHtml(m, a, 1, none);
const led = (got) => T.pointMaps({ open: 0, planned: 0, delivered: 0, items: [], approvals: [], asks: { 's:1': { '0': got } } });
out.picked = T.askCardHtml(m, a, 1, led({ option: 'CSV; XML', options: ['CSV', 'XML'], comment: 'for the old reports', at: 1 }));
const yn = T.parseAsks('Ask (yes/no): Ship it tonight?')[0];
const box = { option: '', comment: 'After 22:00', box: true, key: 'k1', question: 'Ship it tonight?', at: 1 };
const said = { id: 's:2', from: 'user', text: 'Re “Ship it tonight?”: After 22:00\n\n[point P3]' };
T.setItems([m, said]);
T.setPoints({ asks: { 's:1': { '0': box } } });
out.box = T.askCardHtml({ ...m, text: 'Ask (yes/no): Ship it tonight?' }, yn, 1, led(box));
T.setPoints({ asks: { 's:1': { '0': box } } });
out.strip = T.boxAnswerStrip(said);
out.plain = T.boxAnswerStrip({ id: 's:3', from: 'user', text: 'Re “Ship it tonight?”: something else' });
// The same answer to the same question twice, and one answer the start of
// another: each card has its own balloon, each balloon its own card.
const q = 'Ship it tonight?', A = (comment, at, key) => ({ option: '', comment, box: true, key, question: q, at });
T.setPoints({ asks: { 'a:1': { '0': A('After 22:00', 100, 'k1') }, 'a:5': { '0': A('After 22:00', 500, 'k5') },
  'a:7': { '0': A('Yes', 700, 'k7') }, 'a:8': { '0': A('Yes, but later', 800, 'k8') }, 'a:9': { '0': A('Yes', 900, 'k9') } } });
T.setItems([
  { id: 'u:2', from: 'user', ts: 101, text: 'Re “Ship it tonight?”: After 22:00\n\n[point P3]' },
  { id: 'u:6', from: 'user', ts: 501, text: 'Re “Ship it tonight?”: After 22:00\n\n[point P4]' },
  { id: 'u:71', from: 'user', ts: 701, text: 'Re “Ship it tonight?”: Yes' },
  { id: 'u:81', from: 'user', ts: 801, text: 'Re “Ship it tonight?”: Yes, but later' }]);
out.pairs = { a1: T.askBoxMid('a:1', 0), a5: T.askBoxMid('a:5', 0), a7: T.askBoxMid('a:7', 0), a8: T.askBoxMid('a:8', 0), a9: T.askBoxMid('a:9', 0) };
const mid = t => { const r = T.boxAnswerStrip(t); return r ? /data-ref-msg="([^"]*)"/.exec(r.html)[1] : null; };
out.back = { u6: mid({ id: 'u:6', from: 'user', text: 'Re “Ship it tonight?”: After 22:00\n\n[point P4]' }),
  u81: mid({ id: 'u:81', from: 'user', text: 'Re “Ship it tonight?”: Yes, but later' }),
  pending: mid({ from: 'user', text: 'Re “Ship it tonight?”: Yes\n\n[point P9]' }),
  // A send still pending whose balloon is drawn already: its own key's answer.
  keyed: mid({ from: 'user', sendKey: 'k7', text: 'Re “Ship it tonight?”: Yes\n\n[point P7]' }),
  wrongKey: mid({ from: 'user', sendKey: 'k8', text: 'Re “Ship it tonight?”: Yes' }) };
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TheCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        import subprocess
        r = subprocess.run([NODE, "-e", NODE_JS], input=json.dumps({"code": NOUN + "\n" + fold_block(SRC), "pick": PICK}),
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode:
            raise AssertionError(r.stderr[-3000:])
        cls.o = json.loads(r.stdout.strip().splitlines()[-1])

    def test_check_boxes_and_send_with_the_count(self):
        o = self.o
        self.assertTrue(o["multi"])
        self.assertIn("Ask · pick any", o["fresh"])
        self.assertEqual(o["fresh"].count('role="checkbox"'), 3)
        self.assertEqual(o["fresh"].count('data-tick="1"'), 3)
        self.assertRegex(o["fresh"], r'<button type="button" class="qa-send"[^>]*data-multi="1" disabled[^>]*>Send</button>')
        # #408: no comment field; the card's Reply sits after its Send.
        self.assertNotIn("<textarea", o["fresh"])
        self.assertNotIn("qa-cm", o["fresh"])
        self.assertRegex(o["fresh"], r'>Send</button><button type="button" class="qa-reply" data-ask="s:1:0" aria-pressed="false"')
        self.assertRegex(o["ticked"], r'class="qa-send primary"[^>]*>Send \(2\)</button>')
        self.assertEqual(o["ticked"].count('aria-checked="true"'), 2)

    def test_answered_it_lists_the_ticks(self):
        p = self.o["picked"]
        self.assertIn('<span class="qa-dw">You picked:</span> <strong>CSV</strong> · <strong>XML</strong>', p)
        self.assertIn("for the old reports", p)
        self.assertNotIn("<textarea", p)

    def test_a_box_answer_shows_on_the_card_and_on_the_message(self):
        b = self.o["box"]
        self.assertIn('You answered in <a class="msg-link"', b)
        self.assertIn('data-ref-msg="s:2">your message ↓</a>', b)
        self.assertIn("After 22:00", b)
        s = self.o["strip"]
        self.assertTrue(s["text"].startswith("After 22:00"), s)
        self.assertIn('Replies to <a class="card-jump"', s["html"])
        self.assertIn("Ship it tonight? ↑", s["html"])
        self.assertIsNone(self.o["plain"], "words of its own: not the box's answer")

    def test_the_same_words_twice_link_each_to_its_own(self):
        self.assertEqual(self.o["pairs"], {"a1": "u:2", "a5": "u:6", "a7": "u:71", "a8": "u:81", "a9": ""},
                         "in turn, by the whole words: a repeat and a prefix each find their own balloon")
        self.assertEqual(self.o["back"], {"u6": "a:5", "u81": "a:8", "pending": "a:9", "keyed": "a:7", "wrongKey": None},
                         "a balloon names its own card; a send not drawn yet the answer still without one")


if __name__ == "__main__":
    unittest.main()
