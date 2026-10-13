"""Room index and cursor safety on a growing chat."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import chatroom
import attention
import dashboard


class RoomIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.rooms = patch.object(chatroom, "ROOMS_DIR", self.path)
        self.rooms.start()
        self.addCleanup(self.rooms.stop)
        chatroom._INDEX.clear()
        chatroom._CHANGES.clear()

    def room(self, rid="room-example", **fields):
        return {**{"id": rid, "projectId": "proj-1", "workflow": "inprogress",
                   "updatedAt": time.time(), "messages": []}, **fields}

    def test_index_reuses_unchanged_room_and_detects_external_change(self):
        room = self.room()
        chatroom._write(room)
        with patch.object(chatroom, "_read", wraps=chatroom._read) as read:
            one = chatroom.room_index()[0]
            chatroom.room_index()
            self.assertEqual(read.call_count, 0)
        self.assertEqual({"id", "project", "workflow", "updatedAt", "size", "mtime"}
                         - one.keys(), set())
        room["workflow"] = "done"
        (self.path / "room-example.json").write_text(json.dumps(room), encoding="utf-8")
        self.assertEqual(chatroom.room_index()[0]["workflow"], "done")
        self.assertIsNone(chatroom.room_delta(room["id"], one["signature"]))

    def test_cold_room_reopens_when_updated_or_live(self):
        room = self.room(updatedAt=time.time() - 90000, workflow="done")
        self.assertTrue(chatroom.room_is_cold(room))
        self.assertFalse(chatroom.room_is_cold(room, lambda _: True))
        room["workflow"] = "inprogress"
        self.assertFalse(chatroom.room_is_cold(room))

    def test_delta_collects_appends_and_edits_or_requires_refetch(self):
        room = self.room()
        chatroom._write(room)
        start = chatroom.room_revision(room["id"])
        room["messages"].append({"id": "m1", "text": "first"})
        chatroom._write(room)
        room["messages"][0]["text"] = "edited"
        room["messages"].append({"id": "m2", "text": "second"})
        chatroom._write(room)
        self.assertEqual(chatroom.room_delta(room["id"], start),
                         (chatroom.room_revision(room["id"]), {"m1", "m2"}))
        room["messages"].pop(0)
        chatroom._write(room)
        self.assertIsNone(chatroom.room_delta(room["id"], start))

    def test_snapshot_revision_matches_room(self):
        room = self.room()
        chatroom._write(room)
        snapshot, revision = chatroom.get_room_snapshot(room["id"])
        self.assertEqual(snapshot["id"], room["id"])
        self.assertEqual(revision, chatroom.room_revision(room["id"]))
        snapshot["messages"].append({"id": "local"})
        self.assertEqual(chatroom.get_room(room["id"])["messages"], [])

    def test_attention_summary_omits_old_done_room_and_restores_reopen(self):
        room = self.room(updatedAt=time.time() - 90000, workflow="done",
                         participants=[], status="active")
        chatroom._write(room)
        attention._SUMMARY_CACHE.clear()
        with patch.object(dashboard, "load_projects", return_value=[]), \
                patch.object(dashboard, "load_session_projects", return_value={}), \
                patch.object(dashboard, "_room_is_live", return_value=False):
            self.assertEqual(attention._room_summaries(), [])
            room["workflow"] = "inprogress"
            room["updatedAt"] = time.time()
            chatroom._write(room)
            self.assertEqual([r["id"] for r in attention._room_summaries()], [room["id"]])

    def test_attention_keeps_old_po_and_room_waiting_for_person(self):
        old = time.time() - 90000
        po = self.room("room-po", updatedAt=old, workflow="done",
                       participants=[], status="active")
        waiting = self.room("room-waiting", updatedAt=old, workflow="done",
                            participants=[], status="waiting_human")
        chatroom._write(po)
        chatroom._write(waiting)
        attention._SUMMARY_CACHE.clear()
        attention._COLD_HUMAN.clear()
        with patch.object(dashboard, "load_projects", return_value=[
                {"id": "proj-1", "name": "Project", "poRoomId": po["id"]}]), \
                patch.object(dashboard, "load_session_projects", return_value={}), \
                patch.object(dashboard, "_room_is_live", return_value=False):
            self.assertEqual({r["id"] for r in attention._room_summaries()},
                             {po["id"], waiting["id"]})


if __name__ == "__main__":
    unittest.main()
