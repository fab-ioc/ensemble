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

    def test_general_subject_overlap_groups_replies_and_keeps_latest_answer(self):
        rows = self.view()["topics"]
        backtest = next(row for row in rows if "P1" in row["threads"])
        skew = next(row for row in rows if "P3" in row["threads"])
        self.assertEqual(backtest["threadCount"], 2)
        self.assertEqual([t["id"] for t in backtest["threadOptions"]], ["P1", "P2"])
        self.assertEqual(backtest["latest"]["mid"], "s:3")
        self.assertEqual(skew["latest"]["mid"], "s:5")

    def test_manual_move_rename_and_archive_survive_reload(self):
        rows = self.view()["topics"]
        backtest = next(row["id"] for row in rows if "P1" in row["threads"])
        skew = next(row["id"] for row in rows if "P3" in row["threads"])
        topics.change(self.project, self.rooms, "move", thread="P3", topic=backtest)
        topics.change(self.project, self.rooms, "rename", topic=backtest, name="Replay results")
        moved = self.view()["topics"]
        self.assertEqual(next(row for row in moved if row["id"] == backtest)["threadCount"], 3)
        self.assertEqual(next(row for row in moved if row["id"] == backtest)["name"], "Replay results")
        topics.change(self.project, self.rooms, "archive", topic=skew)
        self.assertNotIn(skew, [row["id"] for row in self.view()["topics"]])
        self.assertIn("P3", json.loads(topics._path(self.project["id"], self.rooms).read_text())["assign"])

    def test_task_number_in_replies_groups_different_questions(self):
        self.recs[1]["turn"]["text"] = "Re P1: #42 is ready"
        self.recs[5]["turn"]["text"] = "Re P3: #42 is ready"
        rows = self.view()["topics"]
        self.assertIn({"P1", "P3"}, [set(row["threads"]) for row in rows])

    def test_uncertain_subject_stays_separate_until_chip_is_accepted(self):
        recs = [human("a:0", 0, "App mobile alerts settings screen"),
                human("a:1", 1, "App mobile alerts notifications screen"),
                human("a:2", 2, "App mobile alerts settings screen")]
        assigned = threads.assign(recs, {"points": [point("P1", "a:0", "App mobile alerts settings screen"),
                                                   point("P2", "a:1", "App mobile alerts notifications screen", 1),
                                                   point("P3", "a:2", "App mobile alerts settings screen", 2)]}, side())
        self.idx = {**assigned, "by_mid": {r["mid"]: r for r in recs}, "recs": recs, "sig": "uncertain"}
        for th in self.idx["threads"].values():
            th.update(unread=0, openQuestions=0, state="", rootWho="you")
        before = self.view()
        self.assertEqual(len(before["topics"]), 2)
        suggestion = before["suggestions"]["P2"]
        topics.change(self.project, self.rooms, "move", thread="P2", topic=suggestion["topic"])
        after = self.view()
        self.assertEqual(len(after["topics"]), 1)
        self.assertEqual(after["topics"][0]["threadCount"], 3)

    def test_rejected_suggestion_stays_dismissed_and_archive_wakes_on_new_answer(self):
        recs = [human("a:0", 0, "App mobile alerts settings screen"),
                human("a:1", 1, "App mobile alerts notifications screen"),
                human("a:2", 2, "App mobile alerts settings screen")]
        assigned = threads.assign(recs, {"points": [point("P1", "a:0", "App mobile alerts settings screen"),
                                                   point("P2", "a:1", "App mobile alerts notifications screen", 1),
                                                   point("P3", "a:2", "App mobile alerts settings screen", 2)]}, side())
        self.idx = {**assigned, "by_mid": {r["mid"]: r for r in recs}, "recs": recs, "sig": "uncertain"}
        for th in self.idx["threads"].values():
            th.update(unread=0, openQuestions=0, state="", rootWho="you")
        before = self.view()
        suggestion = before["suggestions"]["P2"]
        topics.change(self.project, self.rooms, "reject", thread="P2", topic=suggestion["topic"])
        self.assertNotIn("P2", self.view()["suggestions"])
        topics.change(self.project, self.rooms, "archive", topic=suggestion["topic"])
        self.assertIn(suggestion["topic"], [t["id"] for t in self.view()["archived"]])
        self.idx["by_mid"]["a:3"] = po("a:3", 100, "A new answer")
        self.idx["threads"]["P1"]["members"].append("a:3")
        self.idx["threads"]["P1"]["unread"] = 1
        self.idx["sig"] = "new answer"
        self.assertIn(suggestion["topic"], [t["id"] for t in self.view()["topics"]])

    def test_cached_view_rebuilds_only_when_threads_or_topic_file_changes(self):
        with mock.patch.object(threads, "ready", return_value=self.idx), \
             mock.patch.object(topics, "_auto_groups", wraps=topics._auto_groups) as grouping:
            first = topics.view(self.project, {}, self.rooms)
            self.assertIs(first, topics.view(self.project, {}, self.rooms))
            self.assertEqual(grouping.call_count, 1)
            topics.change(self.project, self.rooms, "rename", topic=first["topics"][0]["id"], name="New label")
            topics.view(self.project, {}, self.rooms)
            self.assertEqual(grouping.call_count, 2)


if __name__ == "__main__":
    unittest.main()
