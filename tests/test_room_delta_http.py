"""The room poll never silently loses a changed message."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from unittest.mock import patch

import chatroom
import dashboard


class RoomDeltaHttpTests(unittest.TestCase):
    def test_full_delta_and_refetch_after_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(chatroom, "ROOMS_DIR", Path(tmp)), \
                patch.object(dashboard, "_annotate_room_liveness", side_effect=lambda room, **_: room), \
                patch.object(dashboard, "checkpoint_list", return_value=[]), \
                patch.object(dashboard, "room_po_id", return_value=""), \
                patch.object(dashboard, "make_po_verdict", return_value={}), \
                patch.object(dashboard, "make_po_answer", return_value={}):
            chatroom._INDEX.clear()
            chatroom._CHANGES.clear()
            room = {"id": "room-12345678", "title": "test", "messages": [],
                    "participants": [], "updatedAt": 1}
            chatroom._write(room)
            server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}/api/room?id={room['id']}"

            def get(url):
                with urlopen(url, timeout=5) as response:
                    return json.loads(response.read())

            full = get(base)
            self.assertEqual(full["messages"], [])
            room["messages"].append({"id": "m1", "text": "first"})
            chatroom._write(room)
            delta = get(base + "&cursor=" + quote(full["revision"])
                        + "&statusSig=" + quote(full["statusSig"]))
            self.assertTrue(delta["delta"])
            self.assertEqual(delta["messages"], room["messages"])
            room["messages"].clear()
            chatroom._write(room)
            self.assertTrue(get(base + "&cursor=" + quote(delta["revision"]))["refetch"])


if __name__ == "__main__":
    unittest.main()
