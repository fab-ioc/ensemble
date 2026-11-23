"""A file's comments go to the thread it was opened from (#414, the CEO's
P444): GET /api/file/owner offers, besides the chats, the threads of the PO
chat (the one the file was opened from first, its main chat next, then the
others latest first, then a new thread named after the file); a thread gone
since is said; and a send into a thread from the file view is that thread's
reply, as one typed in the thread's own box."""
from __future__ import annotations

import time
from urllib.parse import quote
from unittest import mock

import chatroom
import dashboard
import points
import threads
from tests import test_points as tp
from tests.test_threads import http_get


class FileThreads(tp._World):
    def setUp(self):
        super().setUp()
        threads.forget()
        self.addCleanup(threads.forget)
        base = dashboard.DASHBOARD_DIR
        root, code = base / "EnsembleProjects", base / "code"
        home = root / "Motors"
        (home / "Documents").mkdir(parents=True)
        code.mkdir()
        for p in [mock.patch.object(dashboard, "PROJECTS_ROOT", root),
                  mock.patch.object(dashboard, "PROJECTS_FILE", base / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", base / "session_projects.json"),
                  mock.patch.object(dashboard, "SETTINGS_FILE", base / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        self.rid = self.solo_room()
        p = mock.patch.object(threads, "is_threaded", lambda rid, projects=None: rid == self.rid)
        p.start()
        self.addCleanup(p.stop)
        self.task = chatroom.create_room("Gearbox", [{"identity": "claude", "agent": "claude", "cwd": str(code)}])["id"]
        tdir = home / "gearbox"
        (tdir / "repo").mkdir(parents=True)
        chatroom.patch_room(self.task, cwd=str(tdir / "repo"), taskDir=str(tdir))
        dashboard.save_projects([{"id": "proj-m", "name": "Motors", "path": str(code), "poRoomId": self.rid,
                                  "createdAt": 1, "home": str(home)}])
        dashboard.save_session_projects({self.task: "proj-m"})
        self.doc = home / "Documents" / "plan.md"
        self.doc.write_text("# Plan\n\nSeven to one.\n", encoding="utf-8")
        t = self.t0
        self.thread("Make the chart wider", "k1", t)                       # P1
        self.thread("Fix the login page", "k2", t + 100)                   # P2
        self.add("sid-1", tp.turn("assistant", "Re P1: and taller now.", t + 300))  # P1 latest
        self.rebuild()

    def thread(self, text, key, at):
        n = len(self.turns["sid-1"])
        out, ids = self.send(self.rid, text, key=key, at=at)
        threads.note_send(self.rid, "", key, ids, False, out, now=at, new=True)
        self.add("sid-1", tp.turn("user", out, at + 1), tp.turn("assistant", f"Re {ids[0]}: on it.", at + 2))
        return ids[0], n

    def rebuild(self):
        threads.forget()
        points.sync(self.rid, force=True)
        threads.build(self.rid)

    def owner(self, **q):
        extra = "".join(f"&{k}={quote(str(v))}" for k, v in q.items())
        status, j = http_get(f"/api/file/owner?path={quote(str(self.doc))}{extra}")
        self.assertEqual(status, 200, j)
        return j

    @staticmethod
    def places(j):
        return [(c["kind"], c.get("thread", ""), c["why"]) for c in j["choices"]]

    # ---- what is offered -------------------------------------------------

    def test_opened_from_a_thread_it_is_the_default_its_main_chat_next(self):
        j = self.owner(room=self.rid, thread="P2", threadName="Fix the login page")
        self.assertEqual(self.places(j), [("thread", "P2", "opener"), ("po", "", "main"),
                                          ("thread", "P1", "thread"), ("new", "", "new")])
        self.assertEqual((j["room"], j["thread"], j["threadRoom"], j["gone"]), (self.rid, "P2", self.rid, []))
        c = j["choices"][0]
        self.assertEqual((c["room"], c["name"], c["hasPoints"]), (self.rid, "Fix the login page", True))
        self.assertEqual(j["choices"][-1]["name"], "plan.md", "a new thread is named after the file")

    def test_opened_from_the_main_chat_the_threads_follow_latest_first(self):
        j = self.owner(room=self.rid)
        self.assertEqual(self.places(j), [("po", "", "opener"), ("thread", "P1", "thread"),
                                          ("thread", "P2", "thread"), ("new", "", "new")])
        self.assertEqual(j["thread"], "")

    def test_opened_from_a_task_the_task_leads_its_pos_threads_after(self):
        j = self.owner(room=self.task, thread="P2")
        self.assertEqual(self.places(j), [("task", "", "opener"), ("po", "", "owner"),
                                          ("thread", "P1", "thread"), ("thread", "P2", "thread"),
                                          ("new", "", "new")])
        self.assertEqual((j["room"], j["thread"]), (self.task, ""), "a task chat has no threads to open from")

    def test_opened_from_nowhere_the_owner_and_its_threads(self):
        j = self.owner()
        self.assertEqual(self.places(j)[0], ("po", "", "owner"))
        self.assertEqual([c.get("thread") for c in j["choices"] if c["kind"] == "thread"], ["P1", "P2"])
        self.assertEqual(j["threadRoom"], self.rid)

    def test_a_chat_without_threads_offers_none(self):
        with mock.patch.object(threads, "is_threaded", lambda rid, projects=None: False):
            j = self.owner(room=self.rid, thread="P2")
        self.assertEqual(self.places(j), [("po", "", "opener")])
        self.assertEqual((j["thread"], j["threadRoom"]), ("", ""))

    # ---- a thread gone since ---------------------------------------------

    def test_a_closed_thread_is_said_and_its_main_chat_stands_in(self):
        threads.archive(self.rid, ["P2"], now=self.t0 + 500)
        self.rebuild()
        j = self.owner(room=self.rid, thread="P2")
        self.assertEqual(self.places(j), [("po", "", "main"), ("thread", "P1", "thread"), ("new", "", "new")])
        self.assertEqual(j["gone"], [{"id": "P2", "title": "Fix the login page", "why": "closed"}])
        self.assertEqual((j["room"], j["thread"]), (self.rid, ""))

    def test_a_deleted_or_unknown_thread_is_gone(self):
        j = self.owner(room=self.rid, thread="P9", pick="m:nope")
        self.assertEqual(sorted((g["id"], g["why"]) for g in j["gone"]), [("P9", "gone"), ("m:nope", "gone")])
        self.assertEqual(self.places(j)[0], ("po", "", "main"))
        self.assertEqual((j["room"], j["thread"]), (self.rid, ""))

    def test_the_thread_last_sent_to_is_offered_beyond_the_five_listed(self):
        for i in range(6):
            self.thread(f"Task number {i}", f"k{10 + i}", self.t0 + 400 + 10 * i)
        self.rebuild()
        j = self.owner(room=self.rid)
        listed = [c["thread"] for c in j["choices"] if c["kind"] == "thread"]
        self.assertEqual(len(listed), threads.FILE_THREADS)
        self.assertNotIn("P2", listed)
        j = self.owner(room=self.rid, pick="P2")
        listed = [c["thread"] for c in j["choices"] if c["kind"] == "thread"]
        self.assertEqual(listed[-1], "P2", "last, after the latest five")
        self.assertEqual(len(listed), threads.FILE_THREADS + 1)
        self.assertEqual(j["gone"], [])

    # ---- sent there ------------------------------------------------------

    def settle(self):
        for _ in range(500):
            with threads._LOCK:
                if self.rid not in threads._BUILDING:
                    return
            time.sleep(0.01)
        self.fail("the build did not end")

    def poll(self):
        dashboard._annotate_room_liveness(chatroom.get_room(self.rid), with_points=True)
        self.settle()
        return dashboard._annotate_room_liveness(chatroom.get_room(self.rid), with_points=True)

    def resume(self, body):
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            status, _ = tp.http("/api/room/resume", {"roomId": self.rid, "to": "", **body})
        self.assertEqual(status, 200)
        return sent

    def test_comments_sent_to_a_thread_are_its_reply_not_the_chats(self):
        body = f"## Review comments on [plan.md](<{self.doc}>) (1)\n\n**1.** Seven is too many."
        sent = self.resume({"text": body, "key": "fv:c1", "thread": "P2"})
        self.assertTrue(sent[0].startswith("Re P2:"), sent[0])
        self.assertIn("Seven is too many.", sent[0])
        now = time.time()
        n = len(self.turns["sid-1"])
        self.add("sid-1", tp.turn("user", sent[0], now + 5), tp.turn("assistant", "Re P2: six then.", now + 6))
        self.poll()
        mid = f"sid-1:{n}"
        _, ch = http_get(f"/api/room/channel?id={self.rid}")
        self.assertNotIn(mid, {i["mid"] for i in ch["items"]}, "not in the main chat")
        _, th = http_get(f"/api/room/thread?id={self.rid}&t=P2")
        self.assertIn(mid, [i["mid"] for i in th["items"]])

    def test_comments_sent_to_a_new_thread_start_one_named_after_the_file(self):
        sent = self.resume({"text": "## Review comments on plan.md (1)\n\n**1.** x", "key": "fv:c2",
                            "newThread": True, "threadName": "plan.md"})
        now = time.time()
        self.add("sid-1", tp.turn("user", sent[0], now + 5), tp.turn("assistant", "Looking.", now + 6))
        self.poll()
        j = self.owner(room=self.rid)
        first = next(c for c in j["choices"] if c["kind"] == "thread")
        self.assertEqual(first["name"], "plan.md", "the new thread leads the list, by the file's name")
