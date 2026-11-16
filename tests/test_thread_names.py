"""Issue 51: a thread can be named when it is started (the box's name field
with "To: a new thread") and renamed later. The name is kept in the chat's
thread record, never by rewriting a message; an empty name is the automatic
title (its first words) again."""
from __future__ import annotations

import copy
import json
from unittest import mock

import points
import threads
from tests import test_points as tp
from tests.test_threads import http_get
from tests import test_threads_archive as ta


class ThreadNames(tp._World):
    def setUp(self):
        super().setUp()
        threads.forget()
        self.addCleanup(threads.forget)
        self.rid = self.solo_room()
        p = mock.patch.object(threads, "is_threaded", lambda rid, projects=None: rid == self.rid)
        p.start()
        self.addCleanup(p.stop)
        t = self.t0
        one, ids = self.send(self.rid, "Make the chart wider", key="k1", at=t)
        threads.note_send(self.rid, "", "k1", ids, False, one, now=t, new=True, name="  Chart\n width  ")
        self.add("sid-1", tp.turn("user", one, t + 1), tp.turn("assistant", "Re P1: done, it is wider.", t + 2))
        two, ids = self.send(self.rid, "Fix the login page", key="k2", at=t + 100)
        threads.note_send(self.rid, "", "k2", ids, False, two, now=t + 100, new=True)
        self.add("sid-1", tp.turn("user", two, t + 101), tp.turn("assistant", "Re P2: fixed.", t + 102))
        points.sync(self.rid, force=True)
        threads.build(self.rid)

    post = ta.OldThreads.post

    def rebuild(self):
        threads.forget()            # memory dropped: what is kept is read from disk
        points.sync(self.rid, force=True)
        return threads.build(self.rid)

    def titles(self):
        status, ch = http_get(f"/api/room/channel?id={self.rid}&threads=1")
        self.assertEqual(status, 200, ch)
        return {t["id"]: (t["title"], t.get("named"), t.get("autoTitle")) for t in ch["threadList"]}

    def test_a_name_given_at_creation_is_the_title(self):
        self.assertEqual(self.titles(), {
            "P1": ("Chart width", True, "Make the chart wider"),
            "P2": ("Fix the login page", False, "Fix the login page"),
        })
        self.assertEqual(self.rebuild()["threads"]["P1"]["title"], "Chart width")

    def test_rename_is_kept_and_shown_in_the_thread_view(self):
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "P2", "name": "Login"})
        self.assertEqual((status, out), (200, {"ok": True, "name": "Login"}))
        self.assertEqual(threads.load_side(self.rid)["names"], {"P2": "Login"})
        self.rebuild()
        self.assertEqual(self.titles()["P2"], ("Login", True, "Fix the login page"))
        status, th = http_get(f"/api/room/thread?id={self.rid}&t=P2")
        self.assertEqual(th["thread"]["title"], "Login")
        # A rename wins over the name given at creation.
        threads.rename(self.rid, "P1", "Wide chart")
        self.rebuild()
        self.assertEqual(self.titles()["P1"][0], "Wide chart")

    def test_an_empty_name_is_the_automatic_title_again(self):
        threads.rename(self.rid, "P2", "Login")
        threads.rename(self.rid, "P2", "   ")
        threads.rename(self.rid, "P1", "")      # over the name given at creation too
        self.rebuild()
        self.assertEqual(self.titles(), {
            "P1": ("Make the chart wider", False, "Make the chart wider"),
            "P2": ("Fix the login page", False, "Fix the login page"),
        })

    def test_the_messages_and_points_are_untouched(self):
        turns = copy.deepcopy(dict(self.turns))
        ledger = json.dumps(points.load(self.rid), sort_keys=True)
        threads.rename(self.rid, "P1", "Wide chart")
        self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "P2", "name": "Login"})
        self.rebuild()
        self.assertEqual(dict(self.turns), turns)
        self.assertEqual(json.dumps(points.load(self.rid), sort_keys=True), ledger)
        self.assertNotIn("Wide chart", json.dumps(self.turns))

    def test_names_are_cleaned_and_team_activity_cannot_be_named(self):
        self.assertEqual(threads.rename(self.rid, "P1", "x" * 200), "x" * threads.NAME_MAX)
        with self.assertRaises(ValueError):
            threads.rename(self.rid, "team:activity", "Mine")
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "", "name": "A"})
        self.assertEqual(status, 400, out)
        status, out = self.post("/api/room/thread/name", {"roomId": "room-00000000", "thread": "P1", "name": "A"})
        self.assertEqual(status, 404, out)

    def test_search_shows_the_name(self):
        threads.rename(self.rid, "P2", "Login")
        self.rebuild()
        found = threads.search_balloons(self.rid, {}, [["login"]])
        self.assertTrue(found and found.get("messages"), found)
        self.assertEqual({m["threadTitle"] for m in found["messages"]}, {"Login"})
        self.assertEqual(found["latest"]["threadTitle"], "Login")


if __name__ == "__main__":
    import unittest
    unittest.main()
