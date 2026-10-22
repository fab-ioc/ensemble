"""Topic grouping and manual corrections on an isolated project store."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import threads
import topics
from tests.test_threads import T0, human, po, point, side


class TopicTest(unittest.TestCase):
    def setUp(self):
        with topics._CACHE_LOCK:
            topics._CACHE.pop("proj-abcdef12", None)
        with topics._LOCK:
            topics._STATE.pop("proj-abcdef12", None)
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

    def test_only_ceo_task_numbers_group_different_questions(self):
        self.recs[1]["turn"]["text"] = "Re P1: #42 is ready"
        self.recs[5]["turn"]["text"] = "Re P3: #42 is ready"
        rows = self.view()["topics"]
        self.assertNotIn({"P1", "P3"}, [set(row["threads"]) for row in rows])
        self.idx["by_mid"]["s:6"] = human("s:6", 6, "The follow-up is for #42")
        self.idx["threads"]["P1"]["members"].append("s:6")
        self.recs[4]["turn"]["text"] += " for #42"
        topics._path(self.project["id"], self.rooms).unlink()
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
        def ready():
            for _ in range(200):
                got = topics.view(self.project, {}, self.rooms)
                if not got.get("pending") and not got.get("building"):
                    return got
                time.sleep(.01)
            self.fail("topic build did not finish")

        with mock.patch.object(threads, "ready", return_value=self.idx), \
             mock.patch.object(topics, "_auto_groups", wraps=topics._auto_groups) as grouping:
            self.assertTrue(topics.view(self.project, {}, self.rooms)["pending"])
            first = ready()
            self.assertIs(first, topics.view(self.project, {}, self.rooms))
            self.assertEqual(grouping.call_count, 1)
            topics.change(self.project, self.rooms, "rename", topic=first["topics"][0]["id"], name="New label")
            stale = topics.view(self.project, {}, self.rooms)
            self.assertTrue(stale["building"])
            self.assertIs(stale["topics"], first["topics"])
            self.assertIn("New label", [t["name"] for t in ready()["topics"]])
            self.assertEqual(grouping.call_count, 2)

    def test_topic_rebuild_never_blocks_a_thread_open(self):
        entered, release = threading.Event(), threading.Event()
        original = topics._auto_groups

        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(*args)

        try:
            with mock.patch.object(threads, "ready", return_value=self.idx), \
                 mock.patch.object(topics, "_auto_groups", side_effect=slow):
                self.assertTrue(topics.view(self.project, {}, self.rooms)["pending"])
                self.assertTrue(entered.wait(1))
                start = time.perf_counter()
                opened = threads.thread_view(self.project["poRoomId"], {}, "P1")
                during = topics.view(self.project, {}, self.rooms)
                elapsed = time.perf_counter() - start
                self.assertEqual(opened["thread"]["id"], "P1")
                self.assertTrue(during["pending"])
                self.assertLess(elapsed, .2)
        finally:
            release.set()
            for _ in range(200):
                with topics._CACHE_LOCK:
                    if self.project["id"] not in topics._BUILDING:
                        break
                time.sleep(.01)


class IncrementalTopicTest(unittest.TestCase):
    """A build keeps the last one's features and pairs: a thread change scores
    only what it can move, and every build equals one made from scratch."""
    ASKS = ["Backtest the skew strategy on the replay window",
            "What did the backtested skew trades show in the replay?",
            "Make the volatility panel wider on the phone",
            "The volatility panel overlaps the order book on the phone",
            "Export the replay fills to a spreadsheet",
            "Why does the order book lag after reconnecting?",
            "Order book reconnect lag is back after the update"]

    def setUp(self):
        self.pid = "proj-abcdef34"
        self.addCleanup(self.forget)
        self.forget()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rooms = Path(self.tmp.name) / "rooms"
        self.rooms.mkdir()
        self.project = {"id": self.pid, "poRoomId": "room-abcdef34"}
        self.recs, self.pts = [], []
        for i, ask in enumerate(self.ASKS):
            self.recs += [human(f"s:{2 * i}", 2 * i, ask),
                          po(f"s:{2 * i + 1}", 2 * i + 1, f"Re P{i + 1}: on it", re_=[f"P{i + 1}"])]
            self.pts.append(point(f"P{i + 1}", f"s:{2 * i}", ask, 2 * i))
        self.assign("start")

    def assign(self, sig):
        self.idx = {**threads.assign(self.recs, {"points": self.pts}, side()),
                    "by_mid": {r["mid"]: r for r in self.recs}, "recs": self.recs, "sig": sig}
        for th in self.idx["threads"].values():
            th.update(unread=0, openQuestions=0, state="", rootWho="you")

    def forget(self):
        with topics._CACHE_LOCK:
            topics._CACHE.pop(self.pid, None)
        with topics._LOCK:
            topics._STATE.pop(self.pid, None)

    def view(self):
        with mock.patch.object(threads, "ready", return_value=self.idx):
            return topics.view(self.project, {}, self.rooms, now=T0 + 100)

    def scored(self):
        """The view and how many pairs it scored."""
        with mock.patch.object(topics, "_similarity", wraps=topics._similarity) as sim:
            got = self.view()
        return got, sim.call_count

    def assert_same_as_from_scratch(self, got):
        with topics._LOCK:
            kept = topics._STATE[self.pid]
        features = kept["candidates"]
        self.assertEqual(kept["pairs"], topics._auto_groups(features)[2])
        self.assertEqual(features, {tid: topics._features(self.idx["threads"][tid], self.idx) for tid in features})
        self.forget()
        fresh = self.view()
        self.assertEqual(got["topics"], fresh["topics"])
        self.assertEqual(got["suggestions"], fresh["suggestions"])

    def reply(self, tid, mid, text, ts):
        self.idx["by_mid"][mid] = po(mid, ts, text)
        self.idx["threads"][tid]["members"].append(mid)
        self.idx["sig"] = mid

    def add_thread(self, pid, mid, text, ts):
        self.recs.append(human(mid, ts, text))
        self.pts.append(point(pid, mid, text, ts))
        self.assign(mid)

    def drop_thread(self, pid):
        mids = {p["mid"] for p in self.pts if p["id"] == pid}
        self.recs = [r for r in self.recs if r["mid"] not in mids and pid not in r.get("re", ())]
        self.pts = [p for p in self.pts if p["id"] != pid]
        self.assign("without " + pid)

    def test_first_build_scores_every_pair_once(self):
        got, calls = self.scored()
        n = len(self.ASKS)
        self.assertEqual(calls, n * (n - 1) // 2)
        self.assertIn({"P3", "P4"}, [set(row["threads"]) for row in got["topics"]])
        self.assert_same_as_from_scratch(got)

    def test_a_reply_scores_only_its_own_thread_again(self):
        self.view()
        self.reply("P5", "s:50", "The spreadsheet export of replay fills now covers the skew trades", 50)
        self.assertEqual(self.idx["threads"]["P5"]["members"][-1], "s:50")
        got, calls = self.scored()
        self.assertEqual(calls, len(self.ASKS) - 1)
        self.assert_same_as_from_scratch(got)

    def test_an_unchanged_thread_index_scores_nothing_again(self):
        self.view()
        self.idx["sig"] = "same threads, new signature"
        _got, calls = self.scored()
        self.assertEqual(calls, 0)

    def test_a_new_thread_scores_every_pair_again(self):
        # Rarity depends on how many threads there are, so every pair moves.
        self.view()
        self.add_thread("P8", "s:60", "Order book reconnect lag on the phone", 60)
        got, calls = self.scored()
        n = len(self.ASKS) + 1
        self.assertEqual(calls, n * (n - 1) // 2)
        self.assertIn({"P6", "P7", "P8"}, [set(row["threads"]) for row in got["topics"]])
        self.assert_same_as_from_scratch(got)

    def test_a_deleted_thread_leaves_no_pair_behind(self):
        self.view()
        self.drop_thread("P4")
        got, _calls = self.scored()
        self.assertNotIn("P4", [tid for row in got["topics"] for tid in row["threads"]])
        with topics._LOCK:
            pairs = topics._STATE[self.pid]["pairs"]
        self.assertNotIn("P4", pairs)
        self.assertFalse(any("P4" in row for row in pairs.values()))
        self.assert_same_as_from_scratch(got)

    def test_a_thread_replaced_at_the_same_count_rescores_only_what_it_reaches(self):
        self.view()
        self.drop_thread("P5")
        self.add_thread("P8", "s:60", "Rename the invoice folders every quarter", 60)
        got, calls = self.scored()
        n = len(self.ASKS)
        self.assertLess(calls, n * (n - 1) // 2)
        with topics._LOCK:
            pairs = topics._STATE[self.pid]["pairs"]
        self.assertFalse(any("P5" in row for row in pairs.values()))
        self.assert_same_as_from_scratch(got)

    def test_a_retitled_root_rescores_the_threads_its_rarity_reaches(self):
        self.view()
        self.idx["threads"]["P2"]["title"] = "Volatility panel width"
        self.idx["sig"] = "retitled"
        got, _calls = self.scored()
        self.assert_same_as_from_scratch(got)

    def test_a_restart_rebuilds_pairs_and_keeps_corrections(self):
        before = self.view()
        backtest = next(row["id"] for row in before["topics"] if "P1" in row["threads"])
        topics.change(self.project, self.rooms, "move", thread="P5", topic=backtest)
        topics.change(self.project, self.rooms, "rename", topic=backtest, name="Replay work")
        kept = self.view()
        self.forget()       # a hub start: no features or pairs in memory
        got, calls = self.scored()
        n = len(self.ASKS)
        self.assertEqual(calls, n * (n - 1) // 2)
        self.assertEqual(got["topics"], kept["topics"])
        row = next(row for row in got["topics"] if row["id"] == backtest)
        self.assertEqual(row["name"], "Replay work")
        self.assertIn("P5", row["threads"])

    def test_a_model_change_scores_every_pair_again(self):
        self.view()
        with topics._LOCK:
            topics._STATE[self.pid]["model"] = topics._MODEL - 1
        _got, calls = self.scored()
        n = len(self.ASKS)
        self.assertEqual(calls, n * (n - 1) // 2)


if __name__ == "__main__":
    unittest.main()
