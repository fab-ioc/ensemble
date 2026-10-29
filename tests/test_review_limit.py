"""A task's review-round limit and the CEO's ruling past it (#288):
dashboard.review_rounds / review_limit / Handler._review_limit_check,
ensemble_tools._review_done / _report / _update_task reviewLimit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
import quiet_reports


def log_of(rounds: int, numbers=None) -> str:
    numbers = numbers or list(range(1, rounds + 1))
    return "# Review log — T\n\n" + "".join(
        f"## Review {k} — changes requested — 2026-10-29 10:0{i}\n\n- Reviewer: codex (codex)\n"
        f"- Reviewed: branch `sess/t` at `abc123{i}`\n\n- finding\n\n"
        for i, k in enumerate(numbers[:rounds]))


class Rounds(unittest.TestCase):
    def test_a_round_is_a_verdict_heading_not_its_number(self):
        self.assertEqual(dashboard.review_rounds(""), 0)
        self.assertEqual(dashboard.review_rounds(log_of(3)), 3)
        # An old long-lived reviewer numbered its own: "Review 3" was round 2.
        self.assertEqual(dashboard.review_rounds(log_of(2, [1, 3])), 2)
        self.assertEqual(dashboard.review_rounds(
            "## Review 1 — approved — d\n\nok\n## Review 2 — comments — d\n\n"
            "## Review notes\n\nnot a verdict\n"), 2)

    def test_the_limit_is_the_task_own_else_the_setting(self):
        with mock.patch.object(dashboard, "load_settings", return_value={"reviewRoundsPerTask": 7}):
            self.assertEqual(dashboard.review_limit({}), 7)
            self.assertEqual(dashboard.review_limit({"reviewLimit": 9}), 9)
            self.assertEqual(dashboard.review_limit({"reviewLimit": 0}), 0, "0 = no limit")
        with mock.patch.object(dashboard, "load_settings", return_value={}):
            self.assertEqual(dashboard.review_limit({}), dashboard.REVIEW_ROUNDS_DEFAULT)
        with mock.patch.object(dashboard, "load_settings", return_value={"reviewRoundsPerTask": "x"}):
            self.assertEqual(dashboard.review_limit({}), dashboard.REVIEW_ROUNDS_DEFAULT)

    def test_clamp(self):
        self.assertEqual(dashboard.clamp_review_rounds("4"), 4)
        self.assertEqual(dashboard.clamp_review_rounds(-3), 0)
        self.assertEqual(dashboard.clamp_review_rounds(10_000), dashboard.REVIEW_ROUNDS_MAX)
        self.assertIsNone(dashboard.clamp_review_rounds(True))
        self.assertIsNone(dashboard.clamp_review_rounds("five"))
        self.assertEqual(dashboard._SETTINGS_DEFAULTS["reviewRoundsPerTask"], 5)


class _Handler:
    def __init__(self):
        self.rang: list = []        # (room, idents, line)
        self.reports: list = []     # (po room, kind, text)
        self.recipients: list = []

    def _ring(self, room_id, idents, wake, ways=None):
        self.rang.append((room_id, list(idents), wake))
        return list(idents)

    def _ring_recipients(self, room_id, res):
        self.recipients.append((room_id, res["recipients"]))
        return res["recipients"]

    def _ring_report(self, po_room_id, result, task_id, title, reporter, kind, text):
        self.reports.append((po_room_id, kind, text))
        return (result or {}).get("recipients") or []


class _Task(unittest.TestCase):
    """A PO room and a task (claude owner, codex reviewer) whose log has
    ``self.rounds`` rounds; the setting is 3 rounds."""
    rounds = 3

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        tmp = Path(self.temp.name)
        self.po_rid = None
        for p in (mock.patch.object(chatroom, "ROOMS_DIR", tmp / "rooms"),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", tmp / "hub"),
                  mock.patch.object(dashboard, "load_settings",
                                    return_value={"reviewRoundsPerTask": 3}),
                  mock.patch.object(dashboard, "_room_is_live", return_value=True),
                  mock.patch.object(dashboard, "room_po_id", side_effect=lambda r, *a, **k: self.po_rid),
                  mock.patch.object(ensemble_tools, "_projects", side_effect=lambda: self.projects)):
            p.start()
            self.addCleanup(p.stop)
        (tmp / "hub").mkdir()
        quiet_reports.reset()
        self.addCleanup(quiet_reports.reset)
        self.po_rid = chatroom.create_room("Motors PO", [{"identity": "po", "agent": "claude",
                                                          "role": "ProductOwner"}])["id"]
        self.rid = chatroom.create_room(
            "Motor spec", [{"identity": "claude", "agent": "claude", "role": "engineer"},
                           {"identity": "codex", "agent": "codex", "role": "reviewer"}])["id"]
        self.task_dir = tmp / "task"
        self.task_dir.mkdir()
        full = chatroom.get_room(self.rid, public=False)
        full.update(mode="collab", cwd=str(self.task_dir), taskDir=str(self.task_dir))
        chatroom.update_room(full)
        self.projects = {"p1": {"id": "p1", "name": "Motors", "poRoomId": self.po_rid}}
        self.set_rounds(self.rounds)
        self.handler = _Handler()

    def set_rounds(self, n: int) -> None:
        (self.task_dir / dashboard.REVIEW_LOG_NAME).write_text(log_of(n), encoding="utf-8")

    def room(self) -> dict:
        return chatroom.get_room(self.rid, public=False)

    def ctx(self, ident: str) -> dict:
        full = self.room()
        return {"room": full, "identity": ident, "part": chatroom.participant(full, ident),
                "projectId": "p1"}

    def po_ctx(self) -> dict:
        po = chatroom.get_room(self.po_rid, public=False)
        return {"room": po, "identity": "po", "part": chatroom.participant(po, "po"),
                "projectId": ""}

    def check(self, sender: str = "claude"):
        return dashboard.Handler._review_limit_check(self.handler, self.rid, "codex",
                                                     {"id": "m1", "from": sender, "text": "review"})

    def po_messages(self, kind: str = "report") -> list:
        return [m for m in chatroom.get_room(self.po_rid, public=False).get("messages", [])
                if m.get("kind") == kind]

    def done(self, verdict: str) -> dict:
        full = self.room()
        part = chatroom.participant(full, "codex")
        n = dashboard.review_count(dashboard.read_review_log(full)) + 1
        part["review"] = {"n": n, "askedBy": "claude", "branch": "sess/t", "head": "abc1234",
                          "startedAt": 1.0}
        chatroom.update_room(full)
        with mock.patch.object(dashboard, "finish_review"):
            return ensemble_tools._review_done(
                self.ctx("codex"), {"verdict": verdict, "summary": "still two bugs",
                                    "findings": "- a.py:3 leaks"}, self.handler)


class StartRefused(_Task):
    def test_an_agent_asking_past_the_limit_starts_no_reviewer(self):
        res = self.check()
        self.assertEqual((res["limited"], res["rounds"], res["limit"]), (True, 3, 3))
        self.assertEqual(len(self.handler.rang), 1)
        room_id, idents, line = self.handler.rang[0]
        self.assertEqual((room_id, idents), (self.rid, ["claude"]))
        self.assertTrue(line.startswith(dashboard.REVIEW_LIMIT_PREFIX))
        self.assertIn("forCeo=true", line)
        self.assertIn("Only your PO can raise the limit", line)
        notices = [m for m in self.room()["messages"] if m.get("noticeKind") == "reviewLimit"]
        self.assertEqual(len(notices), 1)
        # The PO is told once, without a wake; it rides its next wake.
        po = self.po_messages()
        self.assertEqual((len(po), po[0]["rang"], po[0]["reportKind"]), (1, [], "reviewLimit"))
        self.assertEqual(len(quiet_reports.pending(self.po_rid)), 1)
        self.check()
        self.assertEqual(len(self.po_messages()), 1, "told once per limit")

    def test_below_the_limit_or_the_person_asking_it_starts(self):
        self.assertIsNone(self.check("user"))
        self.set_rounds(2)
        self.assertIsNone(self.check())
        self.assertEqual(self.handler.rang, [])

    def test_no_limit(self):
        chatroom.patch_room(self.rid, reviewLimit=0)
        self.set_rounds(40)
        self.assertIsNone(self.check())


class VerdictAtTheLimit(_Task):
    rounds = 2

    def test_the_last_round_changes_requested_tells_the_owner_and_holds_the_po_note(self):
        res = self.done("changes_requested")      # round 3 of 3
        self.assertEqual(res["reviewLimit"], {"rounds": 3, "limit": 3})
        self.assertFalse(res["po"]["woken"])
        self.assertIn("owner's question wakes the PO", res["po"]["why"])
        owner_msg = self.room()["messages"][-1]
        self.assertEqual(owner_msg["to"], "claude")
        self.assertIn("Review limit reached (3 of 3 rounds)", owner_msg["text"])
        po = self.po_messages()[-1]
        self.assertEqual(po["rang"], [])
        self.assertIn("reached its review limit", po["text"])
        self.assertEqual(self.handler.reports, [])
        self.assertIn("review limit reached", quiet_reports.pending(self.po_rid)[-1]["line"])
        self.assertEqual(self.room().get("reviewLimitTold"), 3)
        # A later refused request does not tell the PO a second time.
        self.check()
        self.assertEqual(len(self.po_messages()), 1)

    def test_an_approval_at_the_limit_is_only_quiet(self):
        res = self.done("approve")
        self.assertNotIn("reviewLimit", res)
        self.assertFalse(res["po"]["woken"])
        self.assertIn("approval", res["po"]["why"])
        self.assertNotIn("Review limit", self.room()["messages"][-1]["text"])


class QuestionAtTheLimit(_Task):
    def report(self, kind: str, **extra) -> dict:
        with mock.patch.object(ensemble_tools, "_d", dashboard):
            return ensemble_tools._report(self.ctx("claude"), {"kind": kind, "text": "F1 a.py:3", **extra},
                                          self.handler)

    def test_it_is_for_the_ceo_and_carries_the_ruling_note(self):
        res = self.report("question")
        self.assertTrue(res["poWoken"])
        po = self.po_messages()[-1]
        self.assertTrue(po.get("reviewLimit"))
        self.assertIn("decision", po["text"])
        self.assertIn("Reviewer is right: fix it", po["text"])
        self.assertIn("Owner is right: keep as is", po["text"])
        self.assertEqual(self.handler.reports[-1][1], "question at the review limit")
        self.assertIn("the CEO rules on these findings", self.handler.reports[-1][2])

    def test_below_the_limit_a_question_is_an_ordinary_one(self):
        self.set_rounds(1)
        self.report("question")
        po = self.po_messages()[-1]
        self.assertFalse(po.get("reviewLimit"))
        self.assertNotIn("Reviewer is right", po["text"])
        self.assertEqual(self.handler.reports[-1][1], "question")


class TaskLimitByThePO(_Task):
    def test_the_po_raises_it_with_a_reason_and_the_owner_may_ask_again(self):
        with self.assertRaises(ensemble_tools.ToolError):
            ensemble_tools._update_task(self.po_ctx(), {"taskId": self.rid, "reviewLimit": 5},
                                        self.handler)
        res = ensemble_tools._update_task(self.po_ctx(), {
            "taskId": self.rid, "reviewLimit": 5, "reviewLimitReason": "the CEO wants one more look"},
            self.handler)
        self.assertIn("a review-round limit of 5 (3 used so far)", res["note"])
        self.assertIn("may ask for a review again", res["note"])
        room = self.room()
        self.assertEqual(room["reviewLimit"], 5)
        self.assertEqual(room["reviewLimitSet"]["why"], "the CEO wants one more look")
        rid, idents, line = self.handler.rang[-1]
        self.assertEqual((rid, idents), (self.rid, ["claude"]))
        self.assertTrue(line.startswith("[from the PO] "))
        self.assertIsNone(self.check())

    def test_a_lower_limit_tells_nobody_to_ask_again(self):
        ensemble_tools._update_task(self.po_ctx(), {
            "taskId": self.rid, "reviewLimit": 2, "reviewLimitReason": "enough"}, self.handler)
        self.assertEqual(self.handler.rang, [])

    def test_the_owner_cannot_set_it(self):
        with self.assertRaises(ensemble_tools.ToolError):
            ensemble_tools._update_task(self.ctx("claude"), {
                "taskId": self.rid, "reviewLimit": 9, "reviewLimitReason": "more"}, self.handler)
        self.assertNotIn("reviewLimit", self.room())


class EndToEnd(_Task):
    """The last round asks for changes → the owner's next review request is
    refused → its question reaches the PO for the CEO's ruling → the ruling
    → the owner reports completed, which wakes the PO."""
    rounds = 2

    def test_limit_question_ruling_completed(self):
        self.done("changes_requested")
        self.assertIn("Review limit reached", self.room()["messages"][-1]["text"])
        self.assertEqual(self.check()["limited"], True)
        q = ensemble_tools._report(self.ctx("claude"), {
            "kind": "question", "text": "F1 a.py:3 — reviewer: leaks; owner: closed by caller"},
            self.handler)
        self.assertTrue(q["poWoken"])
        self.assertEqual(self.handler.reports[-1][1], "question at the review limit")
        # The ruling comes back as a chat message from the PO to the owner;
        # nothing reopens reviews, so a further agent request is still refused.
        chatroom.post_message(self.rid, chatroom.HUMAN_IDENTITY,
                              "[from the PO] Ruling: F1 Owner is right: keep as is", to="claude")
        self.assertEqual(self.check()["limited"], True)
        c = ensemble_tools._report(self.ctx("claude"), {"kind": "completed", "text": "done as ruled"},
                                   self.handler)
        self.assertTrue(c["poWoken"])
        self.assertEqual(self.handler.reports[-1][1], "completed")
        kinds = [m.get("reportKind") for m in self.po_messages()]
        self.assertEqual(kinds, ["review", "question", "completed"])


if __name__ == "__main__":
    unittest.main()
