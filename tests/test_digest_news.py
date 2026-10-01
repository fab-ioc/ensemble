"""The PO's progress digest is sent only for news (digest.diff / digest.check):
a report alone, or "waiting for you" flapping, sends nothing — and since #147
only news that needs the PO rings (a problem, a first wait for the CEO); quiet
news (commits, moves, statuses, merges) waits for the next digest that does."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard  # noqa: F401  (binds digest to the dashboard module)
import digest

PROJECT = {"id": "p1", "name": "Motors", "poRoomId": "room-po"}


def _task(**kw) -> dict:
    t = {"id": "room-4", "label": "#4", "title": "Motor spec", "status": "running",
         "column": "inprogress", "attention": "", "attentionReason": "", "idleSeconds": 30,
         "branch": "sess/motor", "base": "main", "commits": 1, "merged": False,
         "sha": "abc1234567", "lastCommit": "Draft (1 hour ago)", "head": "abc1234",
         "report": 0.0, "reportKind": "", "reportText": ""}
    t.update(kw)
    return t


class _Checks(unittest.TestCase):
    """Runs digest.check against a task whose facts the test sets, with the
    baseline kept in memory and every digest recorded instead of delivered."""

    def setUp(self):
        self.store: dict = {}
        self.sent: list = []
        self.task = _task()
        self.po_running = True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)
        self.room = chatroom.create_room(
            "Motor spec", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
        patches = [
            mock.patch.object(digest, "_load_baselines", side_effect=lambda: self.store),
            mock.patch.object(digest, "_save_baseline",
                              side_effect=lambda pid, e: self.store.__setitem__(pid, e)),
            mock.patch.object(digest, "gather", side_effect=lambda p: [dict(self.task)]),
            mock.patch.object(digest, "_settle_merges", return_value=[]),
            mock.patch.object(digest, "_po_target", side_effect=lambda p: (
                ({"id": "room-po"}, "claude", "") if self.po_running
                else (None, "claude", "the PO is not running"))),
            mock.patch.object(digest, "write_up", side_effect=lambda facts: (facts, "plain")),
            mock.patch.object(digest, "_deliver", side_effect=self.deliver),
            mock.patch.object(digest, "_log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.check()                        # the first check records the baseline
        self.assertEqual(self.sent, [])

    def deliver(self, project, room, ident, text, how, changes):
        self.sent.append({"text": text, "changes": changes})
        return True

    def check(self, force: bool = False, **facts) -> bool:
        """Set the task's facts, run a check; True when a digest went out."""
        self.task.update(facts)
        n = len(self.sent)
        self.last = digest.check(PROJECT, force=force)
        return len(self.sent) > n

    def report(self, kind: str, text: str) -> None:
        """The task reports, as ensemble_report records it; the facts take its
        report the way digest._task_facts reads it."""
        time.sleep(0.002)                   # reports are told apart by their time
        chatroom.record_report(self.room, "claude", kind, text)
        rep = chatroom.last_real_report(chatroom.get_room(self.room, public=False))
        self.task.update(report=float(rep.get("ts") or 0), reportKind=rep.get("kind", ""),
                         reportText=rep.get("text", ""))

    def last_what(self) -> list:
        return self.sent[-1]["changes"][0]["what"]


class ReportsAreNotTriggers(_Checks):
    def test_a_report_alone_sends_nothing(self):
        self.assertFalse(self.check(report=100.0, reportKind="question", reportText="Which motor?"))
        self.assertFalse(self.check(report=200.0, reportKind="update", reportText="Still on it"))
        self.assertFalse(self.check(report=300.0, reportKind="completed", reportText="Done"))

    def test_a_report_is_context_when_a_digest_goes_out_for_another_reason(self):
        self.check(report=100.0, reportKind="completed", reportText="Spec written.")
        self.assertFalse(self.check(column="inreview"))      # quiet: pending
        self.assertTrue(self.check(attention="waiting_for_you"))
        change = self.sent[-1]["changes"][0]
        self.assertIn("moved inprogress → inreview", change["what"])
        self.assertIn("reported completed", change["what"])
        self.assertTrue(change["finished"])
        self.assertIn("report (completed): Spec written.", self.sent[-1]["text"])
        # Told once: the next digest does not repeat it.
        self.assertTrue(self.check(attention="blocked", head="def5678"))
        self.assertIn("new commits", self.last_what())
        self.assertNotIn("reported completed", self.last_what())
        self.assertNotIn("report (completed)", self.sent[-1]["text"])

    def test_an_update_report_is_never_mentioned(self):
        self.report("update", "Handed to a fresh session")
        self.assertFalse(self.check())
        self.assertTrue(self.check(head="def5678", attention="stalled"))
        self.assertEqual(self.last_what(), ["attention none → stalled", "new commits"])
        self.assertNotIn("Handed to a fresh session", self.sent[-1]["text"])

    def test_an_update_does_not_replace_the_last_real_report(self):
        self.report("question", "Which motor?")
        self.report("update", "Working again")
        room = chatroom.get_room(self.room, public=False)
        self.assertEqual(room["lastReport"]["kind"], "update")
        self.assertEqual(chatroom.last_real_report(room)["text"], "Which motor?")
        # A task that reported before lastRealReport was kept: its last report
        # counts unless it is an update.
        del room["lastRealReport"]
        self.assertEqual(chatroom.last_real_report(room), {})
        self.assertEqual(chatroom.last_real_report({"lastReport": {"kind": "question"}}),
                         {"kind": "question"})


class WaitingForYou(_Checks):
    def test_flapping_sends_nothing_after_the_first_time(self):
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertEqual(self.last_what(), ["attention none → waiting_for_you"])
        for _ in range(4):
            # The hub typed into it (a doorbell, the rotation ask): it takes a
            # turn and is waiting again.
            self.assertFalse(self.check(attention=""))
            self.assertFalse(self.check(attention="waiting_for_you"))
        self.assertEqual(len(self.sent), 1)

    def test_a_new_report_followed_by_waiting_sends_once(self):
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(report=100.0, reportKind="question", reportText="Which motor?"))
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertIn("reported question", self.last_what())
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(attention="waiting_for_you"))
        self.assertEqual(len(self.sent), 2)

    def test_an_update_report_does_not_make_waiting_news_again(self):
        self.report("question", "Which motor?")
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertFalse(self.check(attention=""))
        self.report("update", "Rotated")
        self.assertFalse(self.check())
        self.assertFalse(self.check(attention="waiting_for_you"))
        self.assertEqual(len(self.sent), 1)

    def test_a_question_survives_an_update_while_the_po_is_down(self):
        # Already told as waiting; a new question is pending while the PO is
        # not running; the agent then reports an update. The question is still
        # delivered, with its text, once the PO is back.
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.po_running = False
        self.report("question", "Which motor?")
        self.assertFalse(self.check())
        self.report("update", "Working on the other part meanwhile")
        self.assertFalse(self.check(attention=""))
        self.po_running = True
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertIn("reported question", self.last_what())
        self.assertIn("report (question): Which motor?", self.sent[-1]["text"])
        self.assertNotIn("Working on the other part", self.sent[-1]["text"])

    def test_a_completion_survives_an_update_as_context(self):
        self.po_running = False
        self.report("completed", "Spec written.")
        self.report("update", "Tidying up")
        self.assertFalse(self.check(column="inreview"))
        self.po_running = True
        self.assertFalse(self.check())                       # quiet news only
        self.assertTrue(self.check(attention="stalled"))
        change = self.sent[-1]["changes"][0]
        self.assertIn("moved inprogress → inreview", change["what"])
        self.assertIn("reported completed", change["what"])
        self.assertTrue(change["finished"])
        self.assertIn("report (completed): Spec written.", self.sent[-1]["text"])

    def test_waiting_is_news_again_after_a_commit_or_a_move(self):
        self.check(attention="waiting_for_you")
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(head="def5678"))         # quiet on its own
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertEqual(self.last_what(), ["attention waiting_for_you again", "new commits"])
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(column="inreview"))
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertIn("moved inprogress → inreview", self.last_what())
        self.assertEqual(len(self.sent), 3)

    def test_waiting_while_a_digest_goes_out_is_told(self):
        # Waiting at the moment a commit is digested: that digest told it.
        self.assertTrue(self.check(head="def5678", attention="waiting_for_you"))
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(attention="waiting_for_you"))


class Problems(_Checks):
    def test_blocked_then_over_then_not_repeated(self):
        self.assertTrue(self.check(attention="blocked"))
        self.assertEqual(self.last_what(), ["attention none → blocked"])
        self.assertFalse(self.check(attention="blocked"))
        self.assertFalse(self.check(attention="blocked"))
        # Over: quiet (the PO made that happen, or the next digest says so).
        self.assertFalse(self.check(attention=""))
        self.assertIn("1 quiet change(s)", self.last["result"])
        self.assertFalse(self.check(attention=""))
        self.assertIn("1 quiet change(s)", self.last["result"])
        # Blocked again: news again, even though the end was never sent.
        self.assertTrue(self.check(attention="blocked"))
        self.assertEqual(self.last_what(), ["attention blocked again"])
        self.assertFalse(self.check(attention="blocked"))
        self.assertEqual(len(self.sent), 2)

    def test_a_problem_that_is_over_rides_the_next_digest(self):
        self.check(attention="stalled")
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(head="def5678"))
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertEqual(self.last_what(),
                         ["attention stalled → waiting_for_you", "new commits"])
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(attention="waiting_for_you"))

    def test_a_stall_coming_back_is_news(self):
        self.assertTrue(self.check(attention="stalled"))
        self.assertFalse(self.check(attention=""))
        self.assertTrue(self.check(attention="stalled"))
        self.assertEqual(self.last_what(), ["attention stalled again"])
        self.assertFalse(self.check(attention=""))
        self.assertTrue(self.check(attention="agent_gone"))
        self.assertEqual(self.last_what(), ["attention stalled → agent_gone"])

    def test_every_problem_rings(self):
        for problem in ("blocked", "agent_gone", "stalled"):
            with self.subTest(problem=problem):
                self.assertTrue(self.check(attention=problem))
                self.assertTrue(self.sent[-1]["changes"][0]["wakes"])
                self.assertFalse(self.check(attention=""))

    def test_a_problem_that_turns_into_waiting_is_over(self):
        self.check(attention="stalled")
        self.assertTrue(self.check(attention="waiting_for_you"))
        self.assertEqual(self.last_what(), ["attention stalled → waiting_for_you"])
        self.assertFalse(self.check(attention=""))
        self.assertFalse(self.check(attention="waiting_for_you"))

    def test_one_problem_turning_into_another_is_news(self):
        self.check(attention="stalled")
        self.assertTrue(self.check(attention="agent_gone"))
        self.assertEqual(self.last_what(), ["attention stalled → agent_gone"])


class QuietNews(_Checks):
    def test_commits_moves_and_status_wait_for_a_ringing_digest(self):
        self.assertFalse(self.check(head="def5678"))
        self.assertEqual(self.last["result"],
                         "1 quiet change(s) — nothing that needs the PO, kept for the next digest")
        self.assertEqual(self.last["pending"], 1)
        self.assertFalse(self.check(column="inreview"))
        self.assertFalse(self.check(status="stopped"))
        self.assertFalse(self.check())
        self.assertEqual(self.last["pending"], 1)
        self.assertTrue(self.check(attention="blocked"))
        self.assertEqual(self.last_what(), ["status running → stopped", "moved inprogress → inreview",
                                            "attention none → blocked", "new commits"])
        self.assertEqual(self.last["pending"], 0)
        self.assertFalse(self.check())
        self.assertEqual(self.last["result"], "nothing new — skipped, PO not woken")

    def test_a_merge_is_quiet(self):
        self.assertFalse(self.check(merged=True, column="done"))
        self.assertIn("quiet", self.last["result"])

    def test_check_now_sends_quiet_news(self):
        self.assertFalse(self.check(head="def5678"))
        self.assertTrue(self.check(force=True))
        self.assertEqual(self.last_what(), ["new commits"])
        self.assertFalse(self.sent[-1]["changes"][0]["wakes"])
        self.assertFalse(self.check())
        self.assertEqual(self.last["result"], "nothing new — skipped, PO not woken")

    def test_a_new_task_is_quiet(self):
        self.store["p1"]["tasks"] = {}
        self.assertFalse(self.check())
        self.assertIn("1 quiet change(s)", self.last["result"])
        self.assertTrue(self.check(attention="blocked"))
        self.assertEqual(self.last_what(), ["new task"])

    def test_nothing_new_is_logged_as_such(self):
        out = digest.check(PROJECT)
        self.assertEqual(out["result"], "nothing new — skipped, PO not woken")


class OldBaselines(unittest.TestCase):
    """A baseline written before toldAttention / toldWaiting were kept."""

    def test_an_old_waiting_baseline_does_not_retell_waiting(self):
        old = {"status": "running", "column": "inprogress", "attention": "waiting_for_you",
               "report": 100.0, "head": "abc1234", "title": "Motor spec", "merged": False}
        t = _task(attention="waiting_for_you", report=100.0, reportKind="question")
        self.assertEqual(digest.diff({"room-4": old}, [t]), [])
        t2 = _task(attention="waiting_for_you", report=100.0, reportKind="question")
        self.assertEqual(digest.diff({"room-4": dict(old, attention="")}, [t2])[0]["what"],
                         ["attention none → waiting_for_you"])

    def test_an_old_blocked_baseline_does_not_retell_blocked(self):
        old = {"status": "running", "column": "inprogress", "attention": "blocked",
               "report": 0, "head": "abc1234", "title": "Motor spec", "merged": False}
        self.assertEqual(digest.diff({"room-4": old}, [_task(attention="blocked")]), [])


if __name__ == "__main__":
    unittest.main()
