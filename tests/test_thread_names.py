"""Issue 51: a thread can be named when it is started (the box's name field
with "To: a new thread") and renamed later. The name is kept in the chat's
thread record, never by rewriting a message; an empty name is the automatic
title (its first words) again."""
from __future__ import annotations

import copy
import json
import sys
from unittest import mock

import chatroom
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
        with self.assertRaises(KeyError):
            threads.rename(self.rid, "team:activity", "Mine")
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "", "name": "A"})
        self.assertEqual(status, 400, out)
        status, out = self.post("/api/room/thread/name", {"roomId": "room-00000000", "thread": "P1", "name": "A"})
        self.assertEqual(status, 404, out)

    def test_only_a_thread_the_chat_has_is_named(self):
        # Review 1: an id the chat has no thread for is refused, not kept for
        # a later thread that happens to get it.
        names = lambda: threads.load_side(self.rid)["names"]
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "P999", "name": "Ghost"})
        self.assertEqual((status, out.get("error")), (404, "no_such_thread"))
        with self.assertRaises(KeyError):
            threads.rename(self.rid, "m:sid-9:9", "Ghost")
        self.assertEqual(names(), {})
        # A thread started on a balloon from its menu, not written in yet.
        mid = next(m for m in threads.build(self.rid)["by_mid"] if m.endswith(":1"))
        self.assertEqual(threads.rename(self.rid, "m:" + mid, "From its menu"), "From its menu")
        # A name or thread that is not text is refused, the name kept as it was.
        threads.rename(self.rid, "P2", "Login")
        for body in ({"thread": "P2", "name": 42}, {"thread": "P2", "name": ["x"]}, {"thread": 2, "name": "A"}):
            status, out = self.post("/api/room/thread/name", {"roomId": self.rid, **body})
            self.assertEqual(status, 400, (body, out))
        self.assertEqual(names()["P2"], "Login")
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "P2", "name": None})
        self.assertEqual((status, out), (200, {"ok": True, "name": ""}), "no name: the automatic title")

    def test_a_deleted_thread_started_again_has_its_own_words(self):
        # Review 1: neither a rename nor the name given at creation outlives
        # a delete; a message after it starts a thread named by its words.
        threads.rename(self.rid, "P2", "Login")
        self.assertEqual(threads.delete(self.rid, None, ["P1", "P2"], now=self.t0 + 700), ["P1", "P2"])
        self.assertEqual(threads.load_side(self.rid)["names"], {})
        status, out = self.post("/api/room/thread/name", {"roomId": self.rid, "thread": "P2", "name": "Late"})
        self.assertEqual(status, 404, "a deleted thread is not named")
        self.add("sid-1", tp.turn("assistant", "Re P1: one last thing on the chart.", self.t0 + 800),
                 tp.turn("assistant", "Re P2: one last thing on the login.", self.t0 + 801))
        self.rebuild()
        self.assertEqual(self.titles(), {
            "P1": ("Re P1: one last thing on the chart.", False, "Re P1: one last thing on the chart."),
            "P2": ("Re P2: one last thing on the login.", False, "Re P2: one last thing on the login."),
        })
        # Named again once it is back: the name holds.
        threads.rename(self.rid, "P2", "Login again")
        self.rebuild()
        self.assertEqual(self.titles()["P2"][0], "Login again")

    def test_a_delete_landing_during_a_rename_wins(self):
        # Review 2: a delete saved between the rename's look at the chat and
        # its save: the name is refused, not kept for a thread started later.
        build, fired = threads.build, []

        def build_then_delete(rid, room=None, **kw):
            idx = build(rid, room, **kw)
            if not fired:
                fired.append(1)
                self.assertEqual(threads.delete(rid, None, ["P2"], now=self.t0 + 700), ["P2"])
            return idx
        with mock.patch.object(threads, "build", build_then_delete), self.assertRaises(KeyError):
            threads.rename(self.rid, "P2", "Late")
        self.assertTrue(fired)
        self.assertNotIn("P2", threads.load_side(self.rid)["names"])
        self.add("sid-1", tp.turn("assistant", "Re P2: one last thing on the login.", self.t0 + 800))
        self.rebuild()
        self.assertEqual(self.titles()["P2"][0], "Re P2: one last thing on the login.")
        # Started again by that message, it is named as any thread is.
        self.assertEqual(threads.rename(self.rid, "P2", "Login again"), "Login again")

    def test_a_rename_saved_while_a_build_runs_is_not_lost(self):
        # Review 1: a build stamps the record's signature of the names it
        # applied, so the index served after it still takes a rename saved
        # between its read of the record and its end.
        threads.forget()
        load, fired = threads.load_side, []

        def load_then_rename(rid):
            side = load(rid)
            if not fired and sys._getframe(1).f_code.co_name == "build":
                fired.append(1)
                late = load(rid)
                late["names"]["P2"] = "Login"
                threads._save_side(rid, late)
            return side
        with mock.patch.object(threads, "load_side", load_then_rename):
            idx = threads.build(self.rid)
        self.assertTrue(fired)
        self.assertEqual(idx["threads"]["P2"]["title"], "Fix the login page", "built from the names it read")
        self.assertNotEqual(idx["namesSig"], threads._side_sig(self.rid))
        self.assertEqual(threads._names_now(self.rid, idx)["threads"]["P2"]["title"], "Login")

    def test_a_rename_shows_before_the_chat_is_read_again(self):
        threads.ready(self.rid, chatroom.get_room(self.rid, public=False))
        with mock.patch.object(threads.threading, "Thread") as started:
            threads.rename(self.rid, "P2", "Login")
            idx = threads.ready(self.rid, chatroom.get_room(self.rid, public=False))
        self.assertTrue(started.called, "a build was started")
        self.assertEqual(idx["threads"]["P2"]["title"], "Login")
        threads.rename(self.rid, "P2", "")
        with mock.patch.object(threads.threading, "Thread"):
            threads._BUILDING.discard(self.rid)
            idx = threads.ready(self.rid, chatroom.get_room(self.rid, public=False))
        self.assertEqual(idx["threads"]["P2"]["title"], "Fix the login page")

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
