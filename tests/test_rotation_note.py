"""A task owner's rotation puts one line in the PO room without waking the PO,
and leaves the task's last real report alone (rotation._report_to_po)."""
from __future__ import annotations

import time
import unittest
from unittest import mock

import chatroom
import dashboard
import digest
import rotation
from test_rotation_kind import _Base, _FakeLauncher, _FakePty, _snap


class RotationNoteTests(_Base):
    def setUp(self):
        super().setUp()
        self.launcher = _FakeLauncher()
        for p in [
            mock.patch.object(dashboard, "hub_launcher", side_effect=lambda: self.launcher),
            mock.patch.object(dashboard.ptyrun, "kill"),
            mock.patch.object(dashboard.ptyrun, "get",
                              side_effect=lambda pid: _FakePty(True) if pid else None),
            mock.patch.object(rotation, "_await_death"),
            mock.patch.object(rotation, "_spawn", side_effect=lambda fn, *a: fn(*a)),
            mock.patch.object(dashboard.usage, "snapshot", return_value=_snap(40, 10)),
        ]:
            p.start()
            self.patches.append(p)
        po = chatroom.create_room(
            "PO", [{"identity": "claude", "agent": "claude", "role": "ProductOwner"}])
        po_room = chatroom.get_room(po["id"], public=False)
        chatroom.participant(po_room, "claude").update(ptyId="po-pty", sessionId="po-sid")
        chatroom.update_room(po_room)
        self.po = po["id"]
        self.patches.append(mock.patch.object(
            dashboard, "find_project",
            return_value={"id": "p1", "poRoomId": self.po}))
        self.patches[-1].start()

    def rotate(self, room):
        s = {"kind": "owner", "name": "t / claude", "project": None, "room": room,
             "part": chatroom.participant(room, "claude"), "why": "",
             "state": {"phase": "asked", "tokensAtAsk": 331_000, "handoverAtAsk": 0},
             "limit": 200_000, "setting": "taskRotateTokens", "who": "the owner (claude)",
             "whose": "claude's", "handoverName": rotation.TASK_HANDOVER_NAME,
             "handover": rotation.Path(self.temp.name) / rotation.TASK_HANDOVER_NAME,
             "ids": {"roomId": room["id"], "identity": "claude"}}
        return rotation._rotate_marked(
            s, {"tokens": 331_000}, lambda r, quiet=False, **x: {"result": r, **x},
            True, True, (room["id"], "claude"), {"stopped": False})

    def test_the_note_is_posted_rings_nobody_and_keeps_the_last_report(self):
        room = self.room()
        room["projectId"] = "p1"
        chatroom.update_room(room)
        chatroom.record_report(room["id"], "claude", "question", "Which motor?")
        before = chatroom.get_room(room["id"], public=False)["lastReport"]
        out = self.rotate(chatroom.get_room(room["id"], public=False))
        self.assertEqual(self.launcher.rings, [], "the PO was rung")
        self.assertIs(out["rotation"]["poTold"], True)
        note = chatroom.get_room(self.po, public=False)["messages"][-1]
        self.assertEqual((note["kind"], note["reportKind"], note["noticeKind"]),
                         ("report", "update", "rotation"))
        self.assertEqual(note["rang"], [], "the PO room would read as waiting on the PO")
        self.assertIn("claude was handed to a fresh session at 331k tokens", note["text"])
        # The task's last real report survives the rotation.
        self.assertEqual(chatroom.get_room(room["id"], public=False)["lastReport"], before)

    def test_the_rotation_sends_no_digest(self):
        room = self.room()
        room["projectId"] = "p1"
        chatroom.update_room(room)
        chatroom.record_report(room["id"], "claude", "question", "Which motor?")
        facts = lambda: digest._task_facts(chatroom.get_room(room["id"], public=False),
                                           {room["id"]: {"state": "waiting_for_you"}},
                                           {}, time.time())
        with mock.patch.object(digest, "_git_facts", return_value={}):
            base = digest.told_baseline({}, [facts()])
            self.rotate(chatroom.get_room(room["id"], public=False))
            self.assertEqual(digest.diff(base, [facts()]), [])

    def test_a_post_report_that_wakes_still_rings(self):
        res = chatroom.post_report(self.po, "codex@t", "claude", "**completed**", {})
        self.assertEqual(res["recipients"], ["claude"])
        res = chatroom.post_report(self.po, "codex@t", "claude", "**update**", {}, wake=False)
        self.assertEqual(res["recipients"], [])


if __name__ == "__main__":
    unittest.main()
