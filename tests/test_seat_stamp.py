"""Each agent message records who sat in its seat when it was written (#361).

chatroom.post_message and chatroom.record_report keep ``seat`` = {agent,
model, role} of the sending agent's seat at that moment (seat_stamp), so the
chat's label can still say who wrote it after a handover moved the seat to
another kind. The person, the hub and a sender outside the room get none.
"""
from __future__ import annotations

import unittest

import chatroom
from test_points import _World


class SeatStamp(_World):
    def room(self):
        rid = chatroom.create_room("Task", [
            {"identity": "claude", "agent": "codex", "model": "gpt-5.5", "role": "engineer: owns the task"},
            {"identity": "codex", "agent": "codex", "role": "reviewer"}])["id"]
        return rid

    def last(self, rid):
        return chatroom.get_room(rid)["messages"][-1]

    def test_a_message_keeps_its_seat_as_it_was(self):
        rid = self.room()
        chatroom.post_message(rid, "claude", "Started.", to="user")
        first = self.last(rid)
        self.assertEqual(first["seat"], {"agent": "codex", "model": "gpt-5.5", "role": "engineer: owns the task"})
        # A handover puts another kind in the seat: new messages say so, the old one keeps its own.
        room = chatroom.get_room(rid, public=False)
        chatroom.participant(room, "claude").update(agent="claude", model="opus")
        chatroom.update_room(room)
        chatroom.post_message(rid, "claude", "Taking over.", to="user")
        self.assertEqual(self.last(rid)["seat"], {"agent": "claude", "model": "opus", "role": "engineer: owns the task"})
        msgs = {m["id"]: m for m in chatroom.get_room(rid)["messages"]}
        self.assertEqual(msgs[first["id"]]["seat"]["agent"], "codex")
        # No model known: the stamp leaves it out rather than keeping an empty one.
        chatroom.post_message(rid, "codex", "One finding.", to="claude")
        self.assertEqual(self.last(rid)["seat"], {"agent": "codex", "role": "reviewer"})

    def test_the_person_and_outsiders_have_no_seat(self):
        rid = self.room()
        chatroom.post_message(rid, "user", "Go on.")
        self.assertNotIn("seat", self.last(rid))
        chatroom.post_message(rid, "ensemble", "Progress.")
        self.assertNotIn("seat", self.last(rid))
        chatroom.post_message(rid, "stranger", "Hello.")
        self.assertNotIn("seat", self.last(rid))
        room = chatroom.get_room(rid, public=False)
        self.assertIsNone(chatroom.seat_stamp(room, "user"))
        self.assertIsNone(chatroom.seat_stamp(room, "nobody"))

    def test_a_report_keeps_its_seat(self):
        rid = self.room()
        chatroom.record_report(rid, "claude", "completed", "Merged.", heading="## Done")
        m = self.last(rid)
        self.assertEqual(m["kind"], "report")
        self.assertEqual(m["seat"], {"agent": "codex", "model": "gpt-5.5", "role": "engineer: owns the task"})
        chatroom.record_report(rid, "codex", "review", "Approved.")
        self.assertEqual(self.last(rid)["seat"], {"agent": "codex", "role": "reviewer"})


if __name__ == "__main__":
    unittest.main()
