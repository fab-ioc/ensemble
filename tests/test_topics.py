"""Topic grouping and manual corrections on an isolated project store."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import threads
import topics
from tests.test_threads import T0, human, po, point, side


class TopicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rooms = Path(self.tmp.name) / "rooms"
        self.rooms.mkdir()
        self.project = {"id": "proj-abcdef12", "poRoomId": "room-abcdef12"}
        self.recs = [human("s:0", 0, "Backtest the strategy"),
                     po("s:1", 1, "Re P1: the replay is ready", re_=["P1"]),
                     human("s:2", 2, "What did the backtested trades show?"),
                     po("s:3", 3, "Re P2: the fills matched", re_=["P2"]),
                     human("s:4", 4, "Make the Skew 8 panel wider"),
                     po("s:5", 5, "Re P3: done", re_=["P3"])]
        assigned = threads.assign(self.recs, {"points": [point("P1", "s:0", "Backtest the strategy"),
                                                       point("P2", "s:2", "Backtested trades", 2),
                                                       point("P3", "s:4", "Skew panel", 4)]}, side())
        self.idx = {**assigned, "by_mid": {r["mid"]: r for r in self.recs}, "recs": self.recs, "sig": "fixture"}
        for th in self.idx["threads"].values():
            th.update(unread=0, openQuestions=0, state="answered", rootWho="you")

    def view(self):
        with mock.patch.object(threads, "ready", return_value=self.idx):
            return topics.view(self.project, {}, self.rooms, now=T0 + 10)

    def test_subject_aliases_group_replies_and_keep_latest_po_answer(self):
        rows = {row["name"]: row for row in self.view()["topics"]}
        self.assertEqual(set(rows), {"Backtesting", "Skew chart"})
        self.assertEqual(rows["Backtesting"]["threadCount"], 2)
        self.assertEqual([t["id"] for t in rows["Backtesting"]["threadOptions"]], ["P1", "P2"])
        self.assertEqual(rows["Backtesting"]["latest"]["mid"], "s:3")
        self.assertEqual(rows["Skew chart"]["latest"]["mid"], "s:5")

    def test_manual_move_rename_and_archive_survive_reload(self):
        rows = {row["name"]: row for row in self.view()["topics"]}
        backtest, skew = rows["Backtesting"]["id"], rows["Skew chart"]["id"]
        topics.change(self.project, self.rooms, "move", thread="P3", topic=backtest)
        topics.change(self.project, self.rooms, "rename", topic=backtest, name="Replay results")
        moved = self.view()["topics"]
        self.assertEqual(next(row for row in moved if row["id"] == backtest)["threadCount"], 3)
        self.assertEqual(next(row for row in moved if row["id"] == backtest)["name"], "Replay results")
        topics.change(self.project, self.rooms, "archive", topic=skew)
        self.assertNotIn(skew, [row["id"] for row in self.view()["topics"]])
        self.assertIn("P3", json.loads(topics._path(self.project["id"], self.rooms).read_text())["assign"])

    def test_uncertain_subject_stays_separate_until_chip_is_accepted(self):
        recs = [human("a:0", 0, "Payment alert settings"),
                human("a:1", 1, "Payment alert notifications")]
        assigned = threads.assign(recs, {"points": [point("P1", "a:0", "Payment alert settings"),
                                                   point("P2", "a:1", "Payment alert notifications", 1)]}, side())
        self.idx = {**assigned, "by_mid": {r["mid"]: r for r in recs}, "recs": recs, "sig": "uncertain"}
        for th in self.idx["threads"].values():
            th.update(unread=0, openQuestions=0, state="", rootWho="you")
        before = self.view()
        self.assertEqual(len(before["topics"]), 2)
        suggestion = before["suggestions"]["P2"]
        topics.change(self.project, self.rooms, "move", thread="P2", topic=suggestion["topic"])
        after = self.view()
        self.assertEqual(len(after["topics"]), 1)
        self.assertEqual(after["topics"][0]["threadCount"], 2)


if __name__ == "__main__":
    unittest.main()
