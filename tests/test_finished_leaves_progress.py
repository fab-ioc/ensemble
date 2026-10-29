"""A finished task leaves In progress by itself (#289).

Measured on 2026-10-29 17:00: the trading board held 8 tasks in In progress
and none had a live agent: 5 had reported ``completed``, 1 a ``question``, 1
``blocked``, 1 an ``update``. Only a PO ever moved a card, so In progress
meant "not yet accepted", not "running".

* ``ensemble_report`` kind ``completed`` moves the task from In progress to
  In review (``dashboard.settle_finished``), never from any other column,
  never a PO's own chat, never over a move made after the report, and never
  touching ``workflowAt`` (a merge still moves the card to Done).
* At hub start (``settle_finished_at_start``) every stopped task in In
  progress whose last report is completed moves; a question, a blocked, an
  update after a question and a running task stay.
"""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools


class _Board(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)
        self.projects = [{"id": "p-trade", "name": "Trading", "poRoomId": ""}]
        self.links: dict[str, str] = {}
        self.alive: set[str] = set()
        sess = lambda pid: (types.SimpleNamespace(alive=lambda: True)    # noqa: E731
                            if pid in self.alive else None)
        for patch in (
                mock.patch.object(dashboard, "load_projects", side_effect=lambda: self.projects),
                mock.patch.object(dashboard, "load_session_projects", side_effect=lambda: self.links),
                mock.patch.object(dashboard.ptyrun, "get", side_effect=sess),
                mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[])):
            patch.start()
            self.addCleanup(patch.stop)

    def task(self, title, workflow="inprogress", project="p-trade", pty=""):
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude",
                                            "role": "engineer"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full.update(mode="solo", launched=True, workflow=workflow)
        chatroom.participant(full, "claude")["ptyId"] = pty
        chatroom.update_room(full)
        if project:
            self.links[rid] = project
        return rid

    def report(self, rid, kind, text="x"):
        time.sleep(0.002)
        chatroom.record_report(rid, "claude", kind, text)

    def call_report(self, rid, kind, text="Done, on the branch."):
        room = chatroom.get_room(rid, public=False)
        ctx = {"room": room, "identity": "claude", "part": chatroom.participant(room, "claude"),
               "projectId": "p-trade"}
        with mock.patch.object(ensemble_tools, "_projects", return_value={}), \
                mock.patch.object(ensemble_tools, "_project_po", return_value=None):
            return ensemble_tools._report(ctx, {"kind": kind, "text": text}, None)

    def column(self, rid):
        return dashboard.workflow_of(chatroom.get_room(rid, public=False))


class ACompletedReportMovesIt(_Board):
    def test_completed_moves_in_progress_to_in_review(self):
        rid = self.task("Fills", pty="pty-1")
        self.alive.add("pty-1")                      # still running as it reports
        self.call_report(rid, "completed")
        self.assertEqual(self.column(rid), "inreview")
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workflowAuto"]["why"], "completed")
        self.assertNotIn("workflowAt", room)         # a person's move only: a merge still moves it

    def test_a_failed_move_still_reports(self):
        rid = self.task("Fills")
        with mock.patch.object(dashboard, "settle_finished", side_effect=OSError("disk")), \
                mock.patch("builtins.print"):
            out = self.call_report(rid, "completed")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.column(rid), "inprogress")
        self.assertEqual(chatroom.last_real_report(chatroom.get_room(rid, public=False))["kind"],
                         "completed")

    def test_other_reports_leave_it(self):
        for kind in ("question", "blocked", "update"):
            rid = self.task(f"Task {kind}")
            self.call_report(rid, kind, "Which price?")
            self.assertEqual(self.column(rid), "inprogress", kind)

    def test_only_from_in_progress(self):
        for col in ("todo", "backlog", "inreview", "done"):
            rid = self.task(f"In {col}", workflow=col)
            self.call_report(rid, "completed")
            self.assertEqual(self.column(rid), col)

    def test_never_a_pos_own_chat(self):
        rid = self.task("Trading PO")
        self.projects[0]["poRoomId"] = rid
        self.report(rid, "completed")
        self.assertFalse(dashboard.settle_finished(chatroom.get_room(rid, public=False)))
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_po_moving_it_back_is_kept_until_the_next_completed(self):
        rid = self.task("Fills")
        self.call_report(rid, "completed")
        time.sleep(0.002)
        ok, _, why = dashboard.update_task(rid, workflow="inprogress")
        self.assertTrue(ok, why)
        self.assertFalse(dashboard.settle_finished(chatroom.get_room(rid, public=False)))
        self.assertEqual(dashboard.settle_finished_at_start(), {})
        self.assertEqual(self.column(rid), "inprogress")
        self.call_report(rid, "completed", "Rework done.")
        self.assertEqual(self.column(rid), "inreview")


class TheCatchUpAtStart(_Board):
    def test_stopped_completed_tasks_move_and_are_counted_per_project(self):
        done1, done2 = self.task("Done one"), self.task("Done two")
        other = self.task("Elsewhere", project="")
        asked, blocked, upd = self.task("Asked"), self.task("Blocked"), self.task("Update after ask")
        running = self.task("Running", pty="pty-r")
        self.alive.add("pty-r")
        for rid in (done1, done2, other, running):
            self.report(rid, "completed")
        self.report(asked, "question", "Which price?")
        self.report(blocked, "blocked", "Logged out.")
        self.report(upd, "question", "Which?")
        self.report(upd, "update", "Photos uploaded meanwhile.")
        self.assertEqual(dashboard.settle_finished_at_start(), {"Trading": 2, "no project": 1})
        for rid in (done1, done2, other):
            self.assertEqual(self.column(rid), "inreview")
        for rid in (asked, blocked, upd, running):
            self.assertEqual(self.column(rid), "inprogress")
        self.assertEqual(dashboard.settle_finished_at_start(), {})       # once

    def test_a_completed_before_an_update_still_counts(self):
        rid = self.task("Rotated after finishing")
        self.report(rid, "completed")
        self.report(rid, "update", "Handover written.")
        self.assertEqual(dashboard.settle_finished_at_start(), {"Trading": 1})

    def test_the_hub_runs_it_at_start(self):
        src = Path(dashboard.__file__).read_text(encoding="utf-8")
        main = src[src.index("def main():"):]
        self.assertLess(main.index("attach_pty_host()"), main.index("settle_finished_at_start()"))
        self.assertLess(main.index("settle_finished_at_start()"), main.index("    startup.booted()\n"))


if __name__ == "__main__":
    unittest.main()
