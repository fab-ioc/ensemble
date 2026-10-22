"""#228 step 2: a chat opens with its latest messages and loads older ones as
it scrolls up; the room is never sent whole to the chat page.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_room_window
"""
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


def msg(i, **kw):
    return {"id": f"m{i}", "from": "claude", "to": "user", "text": f"message {i}", "ts": 1000 + i, **kw}


class RoomWindowHttp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(chatroom, "ROOMS_DIR", Path(tmp.name)),
                  patch.object(dashboard, "_annotate_room_liveness", side_effect=lambda room, **_: room),
                  patch.object(dashboard, "checkpoint_list", return_value=[]),
                  patch.object(dashboard, "room_po_id", return_value=""),
                  patch.object(dashboard, "make_po_verdict", return_value={}),
                  patch.object(dashboard, "make_po_answer", return_value={})):
            p.start()
            self.addCleanup(p.stop)
        chatroom._INDEX.clear()
        chatroom._CHANGES.clear()
        msgs = [msg(i) for i in range(300)]
        msgs[0] = {**msgs[0], "from": "user", "text": "Do the thing"}
        msgs[5] = msg(5, kind="pomsg", name="opten PO's question of 09-25 18:57", fromProjectName="opten", poKind="question",
                      text="a long PO message " * 50)
        self.room = {"id": "room-12345678", "title": "t", "spec": "Do the thing", "messages": msgs,
                     "participants": [], "updatedAt": 1}
        chatroom._write(self.room)
        server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}/api/room?id={self.room['id']}"

    def get(self, q=""):
        with urlopen(self.base + q, timeout=5) as r:
            return json.loads(r.read())

    def test_without_n_the_room_is_whole_as_before(self):
        d = self.get()
        self.assertEqual(len(d["messages"]), 300)
        self.assertNotIn("window", d)

    def test_n_sends_the_latest_and_a_summary_of_the_rest(self):
        d = self.get("&n=50")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(250, 300)])
        self.assertEqual(d["window"], {"total": 300, "before": 250})
        self.assertEqual(d["pmIndex"], [{"id": "m5", "kind": "pomsg", "name": "opten PO's question of 09-25 18:57",
                                         "ts": 1005, "from": "claude", "fromProjectName": "opten",
                                         "poKind": "question", "to": "user"}], "named, without its text")

    def test_n_larger_than_the_room_is_the_whole_room_and_nothing_before(self):
        d = self.get("&n=1000")
        self.assertEqual(len(d["messages"]), 300)
        self.assertEqual(d["window"], {"total": 300, "before": 0})
        self.assertNotIn("pmIndex", d)

    def test_the_page_before_and_down_to_a_balloon(self):
        d = self.get("&n=100&before=m250")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(150, 250)])
        self.assertEqual(d["before"], 150)
        d = self.get("&n=100&before=m150&upto=m20")
        self.assertEqual(d["messages"][0]["id"], "m15", "five before the balloon, for context")
        self.assertEqual(d["messages"][-1]["id"], "m149")
        self.assertEqual(d["before"], 15)
        d = self.get("&n=100&before=m30")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(30)])
        self.assertEqual(d["before"], 0)

    def test_a_link_far_back_gets_a_window_around_it(self):
        # #247 review 4: a link far above the chat's oldest sends its balloon
        # with a few before and ROOM_LINK_BELOW after, not everything between.
        d = self.get("&n=100&before=m250&upto=m20")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(15, 61)])
        self.assertEqual((d["before"], d["gap"]), (15, 189))
        d = self.get("&n=100&before=m250&after=m60")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(61, 161)])
        self.assertEqual(d["gap"], 89)
        d = self.get("&n=100&before=m250&after=m160")
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(161, 250)])
        self.assertEqual(d["gap"], 0, "joined to the chat's oldest")
        d = self.get("&n=100&before=m250&after=nope")
        self.assertEqual((d["messages"], d["missing"]), ([], True))

    def test_a_message_not_in_the_room_says_so(self):
        d = self.get("&n=100&before=nope")
        self.assertEqual((d["messages"], d["missing"]), ([], True))

    def test_a_delta_carries_the_total(self):
        d = self.get("&n=50")
        self.room["messages"].append(msg(300))
        chatroom._write(self.room)
        p = self.get("&n=50&cursor=" + quote(d["revision"]) + "&statusSig=" + quote(d["statusSig"]))
        self.assertTrue(p["delta"])
        self.assertEqual([m["id"] for m in p["messages"]], ["m300"])
        self.assertEqual(p["total"], 301)
        self.assertTrue(p["statusSame"], "the window summary is not part of the status")

    def test_the_first_read_fits_100_kb_and_its_pm_index_follows(self):
        # #247: messages of 20 KB each; the first read keeps the newest that
        # fit, never fewer than three, and names no older PO message yet.
        self.room["messages"][-60:] = [{**m, "text": "x" * 20_000} for m in self.room["messages"][-60:]]
        chatroom._write(self.room)
        raw = urlopen(self.base + "&n=50&lite=1", timeout=5).read()
        d = json.loads(raw)
        self.assertLessEqual(len(raw), 100 * 1024)
        self.assertTrue(d["lite"])
        self.assertEqual([m["id"] for m in d["messages"]], [f"m{i}" for i in range(296, 300)])
        self.assertEqual(d["window"], {"total": 300, "before": 296, "pm": False})
        self.assertNotIn("pmIndex", d)
        p = self.get("&n=50&cursor=" + quote(d["revision"]) + "&statusSig=" + quote(d["statusSig"]) + "&pm=m296")
        self.assertEqual([x["id"] for x in p["pmIndex"]], ["m5"])
        self.assertEqual(p["messages"], [])
        self.room["messages"] = [{**m, "text": "x" * 50_000} for m in self.room["messages"]]
        chatroom._write(self.room)
        d = self.get("&n=50&lite=1")
        self.assertEqual(len(d["messages"]), dashboard.LITE_MIN_MESSAGES, "at least three, however big")

    def test_the_window_is_small(self):
        whole = len(json.dumps(self.get()))
        win = len(json.dumps(self.get("&n=50")))
        self.assertLess(win, whole / 4)


if __name__ == "__main__":
    unittest.main()
