"""#247: the hub reads the chats likeliest to be opened as it starts, PO
chats first, so the first open after a restart is a warm one.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_warm_rooms
"""
import unittest
from unittest import mock

import chatroom
import dashboard
import threads


class WarmRooms(unittest.TestCase):
    def test_po_chats_first_then_the_latest_changed_and_a_failure_skipped(self):
        rows = [{"id": f"room-{i}", "updatedAt": i} for i in range(10)]
        seen = []

        def warm(rid):
            seen.append(rid)
            if rid == "room-8":
                raise OSError("locked")
        with mock.patch.object(chatroom, "room_index", return_value=rows), \
                mock.patch.object(threads, "_po_rooms", return_value=frozenset({"room-2"})), \
                mock.patch.object(dashboard, "warm_room", warm), \
                mock.patch.object(dashboard, "WARM_ROOM_PAUSE_S", 0):
            done = dashboard.warm_rooms(limit=4)
        self.assertEqual(seen, ["room-2", "room-9", "room-8", "room-7"])
        self.assertEqual(done, ["room-2", "room-9", "room-7"])

    def test_a_po_chat_is_built_once_while_a_poll_builds_it(self):
        room = {"id": "room-po", "messages": [], "participants": []}
        builds = []
        with mock.patch.object(chatroom, "get_room", return_value=room), \
                mock.patch.object(threads, "is_threaded", return_value=True), \
                mock.patch.object(threads, "build", lambda rid, r, **k: builds.append(rid)), \
                mock.patch.object(dashboard, "_annotate_room_liveness", return_value=room):
            dashboard.warm_room("room-po")
            with threads._LOCK:
                threads._BUILDING.add("room-po")
            try:
                dashboard.warm_room("room-po")
            finally:
                threads._BUILDING.discard("room-po")
        self.assertEqual(builds, ["room-po"])
        self.assertNotIn("room-po", threads._BUILDING)


if __name__ == "__main__":
    unittest.main()
