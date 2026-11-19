"""Clear Needs you in one action (#279); rows the conversation moved past stay
until each is dealt with (P396).

* Clear all: ``POST /api/attention/done {roomIds}`` records Done with this
  for every room in one page-only write (the guard, a cap of 200, room ids
  only, a gone room listed as missing) and answers each room's marks before;
  ``POST /api/attention/undo {restore}`` puts them back, only ever back;
* no supersede rule any more (P396): the person's words after an ask, or an
  agent's report, leave it waiting until it is answered or closed;
* the chat's Decisions: ``POST /api/room/points/decisions`` closes every
  open card (Clear all: none from before it is listed) and undoes it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asks  # noqa: E402
import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests.test_asks import THREE  # noqa: E402
from tests.test_points import _World, http, turn  # noqa: E402

PAGE = "Only the dashboard page can do this."


class _Page(_World):
    def setUp(self):
        super().setUp()
        self.refusal = ""
        p = mock.patch.object(dashboard.Handler, "_page_refusal", lambda h: self.refusal)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(attention, "snapshot")
        p.start()
        self.addCleanup(p.stop)

    def asked(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "[rotation] You are the PO.", self.t0), turn("assistant", THREE, self.t0 + 2))
        return rid


class ClearAll(_Page):
    def test_guard_cap_and_bad_ids(self):
        rid = self.asked()
        self.refusal = PAGE
        for path, body in (("/api/attention/done", {"roomIds": [rid]}),
                           ("/api/attention/undo", {"restore": {rid: {"dealtAt": 0, "seenAt": 0}}}),
                           ("/api/room/points/decisions", {"roomId": rid, "action": "clear"})):
            with self.subTest(path=path):
                self.assertEqual(http(path, body)[0], 403)
        self.assertFalse(chatroom.dealt_marks(rid)["dealtAt"], "a refused Clear all marks nothing")
        self.refusal = ""
        for ids in ([], "room-1", [f"room-{i}" for i in range(201)], [rid, "../x"], [rid, 7]):
            with self.subTest(ids=str(ids)[:40]):
                self.assertEqual(http("/api/attention/done", {"roomIds": ids})[0], 400)
        self.assertFalse(chatroom.dealt_marks(rid)["dealtAt"], "refused whole: not one room marked")
        self.assertEqual(http("/api/attention/undo", {"restore": {"../x": {}}})[0], 400)
        self.assertEqual(http("/api/attention/undo", {"restore": []})[0], 400)

    def test_clear_all_then_undo(self):
        a, b = self.asked(), self.solo_room("sid-2")
        room = chatroom.get_room(b, public=False)
        room["seenAt"] = self.t0
        chatroom.update_room(room)
        status, r = http("/api/attention/done", {"roomIds": [a, b, a, "room-gone"]})
        self.assertEqual(status, 200, r)
        self.assertEqual((r["done"], r["missing"]), ([a, b], ["room-gone"]))
        self.assertEqual(r["prev"][a], {"dealtAt": 0.0, "seenAt": 0.0})
        self.assertEqual(r["prev"][b]["seenAt"], self.t0)
        self.assertTrue(chatroom.dealt_marks(a)["dealtAt"])
        asks.forget()
        self.assertEqual(asks.open_in(chatroom.get_room(a)), [], "cleared: off Needs you")
        self.assertEqual([c["n"] for c in asks.open_cards(chatroom.get_room(a))], [0, 1, 2], "nothing answered")
        status, u = http("/api/attention/undo", {"restore": r["prev"]})
        self.assertEqual((status, sorted(u["restored"])), (200, sorted([a, b])))
        self.assertEqual(chatroom.dealt_marks(a), {"dealtAt": 0.0, "seenAt": 0.0})
        self.assertEqual(chatroom.dealt_marks(b)["seenAt"], self.t0)
        self.assertNotIn("dealtAt", chatroom.get_room(a, public=False))
        asks.forget()
        self.assertEqual([x["n"] for x in asks.open_in(chatroom.get_room(a))], [0, 1, 2], "back on Needs you")

    def test_undo_never_moves_a_mark_forward(self):
        rid = self.asked()
        self.assertEqual(http("/api/attention/done", {"roomIds": [rid]})[0], 200)
        now = chatroom.dealt_marks(rid)
        self.assertFalse(chatroom.restore_dealt(rid, {"dealtAt": now["dealtAt"] + 999, "seenAt": now["seenAt"] + 999}))
        self.assertEqual(chatroom.dealt_marks(rid), now)


class Supersede(_Page):
    def test_words_after_an_ask_leave_it_open(self):
        # P396: "Writing past a card doesn't mean anything." The person's
        # words in the chat, or the agent's report, leave its asks waiting
        # until each is answered or closed; Done with this sets them aside.
        rid = self.asked()
        room = chatroom.get_room(rid)
        self.assertEqual([a["n"] for a in asks.open_in(room)], [0, 1, 2])
        self.assertEqual(len(asks.open_in({**room, "personAt": self.t0 + 5})), 3, "the person wrote since")
        self.assertEqual(len(asks.open_in({**room, "askClosedAt": self.t0 + 5})), 3, "the agent reported")
        led = points.load(rid)
        led["asksSettledAt"] = self.t0 + 5
        points._save(rid, led)
        asks.forget()
        self.assertEqual(len(asks.open_in(chatroom.get_room(rid))), 3, "settled by words: still open")
        self.assertEqual([c["n"] for c in asks.open_cards(room)], [0, 1, 2], "the cards stay open in the chat")


class Decisions(_Page):
    def test_clear_and_undo_on_the_chat(self):
        rid = self.asked()
        bad = [({"roomId": rid, "action": "drop"}, 400), ({"roomId": "x", "action": "clear"}, 400),
               ({"roomId": "room-gone", "action": "clear"}, 404)]
        for body, code in bad:
            with self.subTest(body=body):
                self.assertEqual(http("/api/room/points/decisions", body)[0], code)
        room = chatroom.get_room(rid)
        sid = lambda: [c["n"] for c in asks.decisions(room, points.load(rid))]   # noqa: E731
        self.assertEqual(sid(), [0, 1, 2])
        status, r = http("/api/room/points/decisions", {"roomId": rid, "action": "clear"})
        self.assertEqual((status, r["before"]), (200, 0), r)
        self.assertEqual(r["points"]["needs"]["decisions"], [], "Clear all closed every card (P396)")
        self.assertNotIn("olderCount", r["points"]["needs"])
        self.assertEqual(sid(), [])
        self.assertTrue(points.load(rid).get("decisionsClearedAt"))
        status, u = http("/api/room/points/decisions", {"roomId": rid, "action": "undo", "before": r["before"]})
        self.assertEqual((status, u["restored"]), (200, True), u)
        self.assertNotIn("decisionsClearedAt", points.load(rid))
        self.assertEqual(len(u["points"]["needs"]["decisions"]), 3, "Undo: back on top")
        self.assertEqual(sid(), [0, 1, 2])

    def test_the_chats_own_view_keeps_cards_written_past(self):
        # P396: the person's message after the cards moves nothing: the chat's
        # Needs you still lists all three, as the Inbox does.
        rid = self.asked()
        p = mock.patch.object(asks, "cards_ready", lambda room: True)   # read now, not in the background
        p.start()
        self.addCleanup(p.stop)
        view = lambda: points._needs_view(rid, None, points.load(rid))   # noqa: E731
        self.assertEqual(len(view()["decisions"]), 3)
        chatroom.post_message(rid, "user", "Let's leave those, look at the brakes instead.")
        v = view()
        self.assertEqual(len(v["decisions"]), 3, v)
        self.assertNotIn("older", v)


if __name__ == "__main__":
    unittest.main()
