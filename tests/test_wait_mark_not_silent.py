"""A task whose owner marked a wait (``ensemble_mark_wait``) is not silent.

On 11-18 #17 had a wait marked (kind time, then background, until 19:10) and
its card read Waiting, yet the attention list called it stalled (cause
silent) and the PO's digest said "#17 silent since …" every 30 minutes. While
a mark is open, any kind, before its ``until``, the silent check skips the
task; once the mark has ended it counts again.
"""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

import attention
import chatroom
import dashboard
import stall
from test_ask_to_po import _Stall

_REAL_PLACES = (Path("/Users/fab/ensemble"), Path.home() / ".ensemble")


class _Silent(_Stall):
    """The owner has produced nothing for 40 minutes, with nothing open."""

    QUIET = 40 * 60

    def setUp(self):
        super().setUp()
        for where in (chatroom.ROOMS_DIR, dashboard.DASHBOARD_DIR):
            w = Path(where).resolve()
            for real in _REAL_PLACES:
                self.assertFalse(w == real.resolve() or real.resolve() in w.parents,
                                 f"the test writes into {w}")
        self.sess.last_output = time.time() - self.QUIET
        self.idle = 5.0                     # only the silent check can speak
        self.sess.info = lambda: {"idleSeconds": self.idle}
        full = self.room()
        chatroom.participant(full, "claude")["resumedAt"] = time.time() - self.QUIET
        full["messages"] = []
        chatroom.update_room(full)

    def mark(self, kind, until):
        full = self.room()
        full["waitMark"] = {"kind": kind, "at": time.time() - self.QUIET, "until": until,
                            "what": "the suite"}
        chatroom.update_room(full)

    def silent_item(self):
        it = self.item()
        return it if it and it.get("cause") == "silent" else None

    def silent_lines(self):
        return [m for m in chatroom.get_room(self.po_rid, public=False)["messages"]
                if "silent since" in (m.get("text") or "")]

    def tick(self):
        with mock.patch.object(dashboard.rotation, "_idle", return_value=False):
            return stall.tick(time.time()).get(self.rid, "")


class AMarkedWaitIsNotSilent(_Silent):
    def test_without_a_mark_it_is_silent(self):
        it = self.silent_item()
        self.assertIsNotNone(it)
        self.assertEqual(it["state"], "stalled")
        self.assertIn("produced nothing for 40 min", it["reason"])

    def test_an_open_mark_of_any_kind_keeps_it_off_the_list_and_out_of_the_digest(self):
        for kind in ("time", "background", "card", "task"):
            with self.subTest(kind=kind):
                self.mark(kind, time.time() + 3600)
                self.assertIsNone(self.silent_item())
                self.assertNotIn("silent", self.tick())
                self.assertEqual(self.silent_lines(), [])

    def test_a_background_mark_with_no_time_is_open(self):
        self.mark("background", 0)
        self.assertIsNone(self.silent_item())

    def test_it_comes_back_once_the_mark_has_ended(self):
        self.mark("background", time.time() + 3600)
        self.assertIsNone(self.silent_item())
        self.mark("background", time.time() - 60)           # its time has passed
        self.assertIsNotNone(self.silent_item())
        full = self.room()
        full["waitMark"] = None                             # idle.py cleared it
        chatroom.update_room(full)
        self.assertIsNotNone(self.silent_item())
        self.assertIn("silent", self.tick())
        self.assertEqual(len(self.silent_lines()), 1)


class TheRule(unittest.TestCase):
    def test_wait_open(self):
        now = 1000.0
        self.assertFalse(stall.wait_open({}, now))
        self.assertTrue(stall.wait_open({"waitMark": {"kind": "time", "until": 1001}}, now))
        self.assertFalse(stall.wait_open({"waitMark": {"kind": "time", "until": 999}}, now))
        self.assertTrue(stall.wait_open({"waitMark": {"kind": "card"}}, now))


if __name__ == "__main__":
    unittest.main()
