"""Old threads (#319): the Threads list is in order of each thread's latest
message; a thread can be put away (archived: out of the lists and the
channel until a message comes to it) or deleted (gone from the lists and
the channel for good, its transcripts and points kept as they are)."""
from __future__ import annotations

import io
import json
from unittest import mock

import dashboard
import points
import threads
from tests import test_points as tp
from tests.test_threads import http_get


class OldThreads(tp._World):
    def setUp(self):
        super().setUp()
        threads.forget()
        self.addCleanup(threads.forget)
        self.rid = self.solo_room()
        p = mock.patch.object(threads, "is_threaded", lambda rid, projects=None: rid == self.rid)
        p.start()
        self.addCleanup(p.stop)
        t = self.t0
        one, _ = self.send(self.rid, "Make the chart wider", at=t)
        self.add("sid-1", tp.turn("user", one, t + 1), tp.turn("assistant", "Re P1: done, it is wider.", t + 2))
        two, _ = self.send(self.rid, "Fix the login page", at=t + 100)
        self.add("sid-1", tp.turn("user", two, t + 101), tp.turn("assistant", "Re P2: fixed.", t + 102))
        # A later answer in P1: P1 is the thread updated last, though started first.
        self.add("sid-1", tp.turn("assistant", "Re P1: and taller now.", t + 300))
        points.sync(self.rid, force=True)
        threads.build(self.rid)

    def channel(self, **q):
        extra = "".join(f"&{k}={v}" for k, v in q.items())
        status, ch = http_get(f"/api/room/channel?id={self.rid}{extra}")
        self.assertEqual(status, 200, ch)
        return ch

    def rebuild(self):
        threads.forget()            # memory dropped: what is kept is read from disk
        points.sync(self.rid, force=True)
        threads.build(self.rid)

    def test_the_threads_list_is_in_order_of_latest_message(self):
        ch = self.channel(threads=1)
        self.assertEqual([t["id"] for t in ch["threadList"]], ["P1", "P2"])
        self.assertEqual(ch["threadList"][0]["updatedAt"], int(self.t0 + 300))
        self.assertEqual(ch["archivedThreads"], [])
        self.assertEqual(ch["threadCount"], 2)

    def test_archive_hides_restore_brings_back_a_new_message_returns_it(self):
        self.assertEqual(threads.archive(self.rid, ["P2"], now=self.t0 + 500), ["P2"])
        self.rebuild()
        ch = self.channel(threads=1)
        self.assertEqual([t["id"] for t in ch["threadList"]], ["P1"])
        self.assertEqual([t["id"] for t in ch["archivedThreads"]], ["P2"])
        self.assertEqual(ch["threadCount"], 1)
        self.assertNotIn("sid-1:2", [i["mid"] for i in self.channel()["items"]], "its first message left the chat")
        threads.restore(self.rid, ["P2"])
        self.rebuild()
        self.assertEqual([t["id"] for t in self.channel(threads=1)["threadList"]], ["P1", "P2"])
        # Put away again, then a message comes to it: back by itself, on top.
        threads.archive(self.rid, ["P2"], now=self.t0 + 500)
        self.add("sid-1", tp.turn("assistant", "Re P2: one more fix.", self.t0 + 600))
        self.rebuild()
        ch = self.channel(threads=1)
        self.assertEqual([t["id"] for t in ch["threadList"]], ["P2", "P1"])
        self.assertEqual(ch["archivedThreads"], [])

    def test_an_archived_thread_does_not_ring_the_bell(self):
        threads.archive(self.rid, ["P1"], now=self.t0 + 500)
        with mock.patch.dict(threads._INDEX):
            self.rebuild()
            idx = threads.build(self.rid)
            idx["threads"]["P1"]["unread"] = 3       # replies not seen
            self.assertFalse(threads._unread(idx["threads"]["P1"]))

    def test_delete_lasts_and_keeps_transcripts_and_points(self):
        led_file = points._path(self.rid)
        before = led_file.read_bytes()
        turns = [dict(t) for t in self.turns["sid-1"]]
        self.assertEqual(threads.delete(self.rid, None, ["P1"], now=self.t0 + 700), ["P1"])
        self.rebuild()
        ch = self.channel(threads=1)
        self.assertEqual([t["id"] for t in ch["threadList"]], ["P2"])
        self.assertEqual(ch["archivedThreads"], [])
        mids = [i["mid"] for i in self.channel()["items"]]
        self.assertNotIn("sid-1:0", mids)
        self.assertNotIn("sid-1:4", mids)
        self.assertEqual(http_get(f"/api/room/thread?id={self.rid}&t=P1")[0], 404)
        self.assertEqual(led_file.read_bytes(), before, "the points ledger is not touched")
        self.assertEqual(self.turns["sid-1"], turns, "the transcript is not touched")
        self.assertEqual({p["id"] for p in points.load(self.rid)["points"]} >= {"P1", "P2"}, True)
        # A follow-up after the delete starts it again from that message.
        self.add("sid-1", tp.turn("assistant", "Re P1: one last thing.", self.t0 + 800))
        self.rebuild()
        th = http_get(f"/api/room/thread?id={self.rid}&t=P1")[1]
        self.assertEqual([i["mid"] for i in th["items"]], ["sid-1:5"])
        self.assertEqual(th["thread"]["root"], "sid-1:5")

    def post(self, path, body):
        raw = json.dumps(body).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
        h.requestline = f"POST {path} HTTP/1.1"
        h.headers = {"Host": "127.0.0.1", "Content-Type": "application/json", "Content-Length": str(len(raw))}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")

    def test_the_threads_endpoint_archives_restores_and_deletes(self):
        status, out = self.post("/api/room/threads", {"roomId": self.rid, "action": "archive", "threads": ["P2"]})
        self.assertEqual((status, out["threads"]), (200, ["P2"]), out)
        self.assertIn("P2", threads.load_side(self.rid)["archived"])
        status, out = self.post("/api/room/threads", {"roomId": self.rid, "action": "restore", "threads": ["P2"]})
        self.assertEqual(out["threads"], ["P2"])
        status, out = self.post("/api/room/threads", {"roomId": self.rid, "action": "delete", "threads": ["P2", "P9"]})
        self.assertEqual(out["threads"], ["P2"], "a thread the chat does not have is not deleted")
        self.assertEqual(self.post("/api/room/threads", {"roomId": self.rid, "action": "burn", "threads": ["P1"]})[0], 400)
        self.assertEqual(self.post("/api/room/threads", {"roomId": self.rid, "action": "archive", "threads": "P1"})[0], 400)
        self.assertEqual(self.post("/api/room/threads", {"roomId": "room-0", "action": "archive", "threads": ["P1"]})[0], 404)

    def test_a_topic_archived_or_deleted_takes_its_threads_with_it(self):
        project = {"id": "proj-abcdef99", "poRoomId": self.rid}
        dashboard.topics._CACHE.pop(project["id"], None)
        dashboard.topics._STATE.pop(project["id"], None)
        with mock.patch.object(dashboard, "load_projects", return_value=[project]):
            grouping = dashboard.topics._build_view(project, threads.build(self.rid), dashboard.chatroom.ROOMS_DIR)
            one = next(r["id"] for r in grouping["topics"] if "P1" in r["threads"])
            status, out = self.post("/api/room/topics", {"roomId": self.rid, "action": "archive", "topics": [one]})
            self.assertEqual(status, 200, out)
            self.assertIn("P1", out["threads"])
            self.assertIn("P1", threads.load_side(self.rid)["archived"])
            status, out = self.post("/api/room/topics", {"roomId": self.rid, "action": "restore", "topics": [one]})
            self.assertNotIn("P1", threads.load_side(self.rid)["archived"])
            status, out = self.post("/api/room/topics", {"roomId": self.rid, "action": "delete", "topics": [one]})
            self.assertEqual(status, 200, out)
            self.assertIn("P1", threads.load_side(self.rid)["deleted"])
            self.assertEqual(self.post("/api/room/topics", {"roomId": self.rid, "action": "archive",
                                                            "topics": "x"})[0], 400)
        # The handler starts a background topics rebuild; let it finish before
        # the temp directory goes away.
        import threading as _th
        for t in _th.enumerate():
            if t.name.startswith("topics-"):
                t.join(10)

    def test_open_points_are_named(self):
        ch = self.channel(threads=1)
        p1 = next(t for t in ch["threadList"] if t["id"] == "P1")
        open_ = {p["id"] for p in points.load(self.rid)["points"] if p["state"] in points.LIVE}
        self.assertEqual(set(p1["openPoints"]), open_ & {"P1"})


if __name__ == "__main__":
    import unittest
    unittest.main()
