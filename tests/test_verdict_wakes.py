"""An early "changes requested" verdict is on the PO's record without a wake
(#147): ensemble_tools._review_done and dashboard.VERDICT_WAKES_FROM. Approved
and comment verdicts, and a late changes-requested, still ring the PO."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools


class _Handler:
    def __init__(self):
        self.rang: list = []
        self.reports: list = []

    def _ring_recipients(self, room_id, res):
        self.rang.append((room_id, res["recipients"]))
        return res["recipients"]

    def _ring_report(self, po_room_id, result, task_id, title, reporter, kind, text):
        self.reports.append((po_room_id, kind, (result or {}).get("recipients")))
        return (result or {}).get("recipients") or []


class VerdictWakes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)
        self.po_rid = chatroom.create_room("Planning", [{"identity": "po", "agent": "claude",
                                                          "role": "ProductOwner"}])["id"]
        self.rid = chatroom.create_room(
            "Motor spec", [{"identity": "claude", "agent": "claude", "role": "engineer"},
                           {"identity": "codex", "agent": "codex", "role": "reviewer"}])["id"]
        full = chatroom.get_room(self.rid, public=False)
        full["mode"] = "collab"
        full["cwd"] = str(Path(self.temp.name) / "task")
        chatroom.update_room(full)
        self.projects = {"p1": {"id": "p1", "name": "Motors", "poRoomId": self.po_rid}}
        for p in (mock.patch.object(ensemble_tools, "_projects", side_effect=lambda: self.projects),
                  mock.patch.object(chatroom, "is_on_mention", return_value=True),
                  mock.patch.object(dashboard, "_room_is_live", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.handler = _Handler()

    def done(self, verdict: str, n: int) -> dict:
        full = chatroom.get_room(self.rid, public=False)
        part = chatroom.participant(full, "codex")
        part["review"] = {"n": n, "askedBy": "claude", "branch": "sess/motor", "head": "abc1234",
                          "startedAt": 1.0}
        chatroom.update_room(full)
        full = chatroom.get_room(self.rid, public=False)
        ctx = {"room": full, "identity": "codex", "part": chatroom.participant(full, "codex"),
               "projectId": "p1"}
        return ensemble_tools._review_done(
            ctx, {"verdict": verdict, "summary": f"round {n}", "findings": "- one thing"}, self.handler)

    def po_reports(self) -> list:
        room = chatroom.get_room(self.po_rid, public=False)
        return [m for m in room.get("messages", []) if m.get("kind") == "report"]

    def test_an_early_changes_requested_is_posted_but_rings_nobody(self):
        for n in (1, 2):
            with self.subTest(n=n):
                res = self.done("changes_requested", n)
                self.assertFalse(res["po"]["woken"])
                self.assertIn("without a wake", res["po"]["why"])
                self.assertEqual(self.handler.reports, [])
                last = self.po_reports()[-1]
                self.assertEqual((last["to"], last["rang"], last["verdict"]),
                                 ("po", [], "changes_requested"))
                self.assertIn(f"review {n} — changes requested", last["text"])
        self.assertEqual(len(self.po_reports()), 2)
        # The engineer who asked is rung as before.
        self.assertEqual([r[1] for r in self.handler.rang], [["claude"], ["claude"]])

    def test_from_review_three_on_it_rings(self):
        res = self.done("changes_requested", dashboard.VERDICT_WAKES_FROM)
        self.assertTrue(res["po"]["woken"])
        self.assertNotIn("why", res["po"])
        self.assertEqual(self.handler.reports, [(self.po_rid, "review 3 (changes requested)", ["po"])])
        self.assertEqual(self.po_reports()[-1]["rang"], ["po"])

    def test_approved_and_comment_always_ring(self):
        for n, verdict in ((1, "approve"), (2, "comment"), (1, "comment")):
            with self.subTest(n=n, verdict=verdict):
                self.handler.reports.clear()
                res = self.done(verdict, n)
                self.assertTrue(res["po"]["woken"])
                self.assertEqual(self.handler.reports[-1][0], self.po_rid)
                self.assertEqual(self.po_reports()[-1]["rang"], ["po"])

    def test_the_log_and_the_task_chat_are_the_same_either_way(self):
        res = self.done("changes_requested", 1)
        log = Path(res["log"]).read_text(encoding="utf-8")
        self.assertIn("## Review 1 — changes requested", log)
        task_msgs = chatroom.get_room(self.rid, public=False)["messages"]
        self.assertIn("sent to the PO", task_msgs[-1]["text"])
        self.assertEqual(task_msgs[-1]["to"], "claude")


if __name__ == "__main__":
    unittest.main()
