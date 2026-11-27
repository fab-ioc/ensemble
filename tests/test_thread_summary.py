"""The left panel's threads under a project (#420): the hub tells each
project's row how many threads its PO chat lists and a signature of them
(``threads.summary``, in ``/api/projects``), read from the index it holds,
never building one."""
from __future__ import annotations

import unittest
from unittest import mock

import dashboard
import threads


def _idx(threads_: dict, sig=("s", 1)) -> dict:
    return {"threads": threads_, "sig": sig}


class ASummary(unittest.TestCase):
    def setUp(self):
        self.saved = dict(threads._INDEX)
        self.addCleanup(lambda: (threads._INDEX.clear(), threads._INDEX.update(self.saved)))

    def test_none_while_the_chat_has_no_index(self):
        threads._INDEX.pop("room-none", None)
        with mock.patch.object(threads, "build") as build:
            self.assertIsNone(threads.summary("room-none"))
            build.assert_not_called()
        self.assertIsNone(threads.summary(""))

    def test_it_counts_what_the_threads_filter_lists(self):
        ts = {"a": {"members": ["m1", "m2"]},              # replied
              "b": {"members": ["m3"]},                    # one balloon: not a thread yet
              "c": {"members": ["m4"], "chosen": True},     # the person started it
              "d": {"members": ["m5", "m6"], "archived": 5},  # put away
              "team:activity": {"members": ["m7", "m8"]}}
        threads._INDEX["room-x"] = (("s", 1), _idx(ts))
        out = threads.summary("room-x")
        self.assertEqual(out["n"], 2)
        self.assertEqual(out["sig"], threads._sig_text(_idx(ts)))

    def test_its_sig_moves_with_the_index(self):
        threads._INDEX["room-x"] = (("s", 1), _idx({}, ("s", 1)))
        a = threads.summary("room-x")["sig"]
        threads._INDEX["room-x"] = (("s", 2), _idx({}, ("s", 2)))
        self.assertNotEqual(a, threads.summary("room-x")["sig"])
        self.assertEqual(threads.summary("room-x")["n"], 0)


class AProjectRow(unittest.TestCase):
    def build(self, reg):
        with mock.patch.object(dashboard, "load_projects", return_value=reg), \
                mock.patch.object(dashboard, "load_session_projects", return_value={}), \
                mock.patch.object(dashboard, "load_sessions", return_value=[]), \
                mock.patch.object(dashboard, "project_home", return_value=""), \
                mock.patch.object(threads, "summary", side_effect=lambda r: {"n": 3, "sig": "z" + r}):
            return {g["id"]: g for g in dashboard.build_projects()["projects"]}

    def test_a_project_with_a_po_carries_its_threads(self):
        g = self.build([{"id": "p1", "name": "One", "path": "", "poRoomId": "room-po"},
                        {"id": "p2", "name": "Two", "path": ""}])
        self.assertEqual(g["p1"]["threads"], {"n": 3, "sig": "zroom-po"})
        self.assertNotIn("threads", g["p2"])


if __name__ == "__main__":
    unittest.main()
