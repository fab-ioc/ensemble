"""Board episodes, declarative gates, and the PO's live usage context."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import board
import chatroom
import dashboard as d
import digest
import due
import ensemble_tools as et
import po_usage
import rotation
import usage


class BoardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = {"id": "p", "name": "Project", "poRoomId": "room-po"}
        self.sent = []
        self.launch = mock.Mock(side_effect=self.start)
        self.git = {}
        for patch in (
            mock.patch.object(chatroom, "ROOMS_DIR", Path(tmp.name) / "rooms"),
            mock.patch.object(d, "DASHBOARD_DIR", Path(tmp.name)),
            mock.patch.object(d, "load_projects", return_value=[self.project]),
            mock.patch.object(d, "load_session_projects", return_value={}),
            mock.patch.object(d, "load_labels", return_value={}),
            mock.patch.object(d, "_room_is_live", side_effect=lambda r: r.get("live", False)),
            mock.patch.object(d, "hub_launcher", return_value=SimpleNamespace(_resume_room=self.launch)),
            mock.patch.object(digest, "_git_facts", side_effect=lambda r: self.git.get(r["id"], {})),
            mock.patch.object(board, "deliver", side_effect=lambda p, s: self.sent.append(s) or True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def room(self, no, **fields):
        r = chatroom.create_room(f"Task {no}", [{"identity": "codex", "agent": "codex"}])
        return chatroom.patch_room(r["id"], no=no, projectId="p", launched=False,
                                  createdAt=1000, updatedAt=1000,
                                  **fields)

    def start(self, room):
        chatroom.patch_room(room["id"], launched=True, live=True, workflow="inprogress")

    def check(self, at):
        board.check(self.project, at)

    def episode(self):
        return board._load()["p"].get("episode")

    def test_clock_starts_at_last_finish_not_first_tick(self):
        self.room(1, priority=2)
        done = self.room(2)
        chatroom.patch_room(done["id"], launched=True, workflow="done", workflowAt=1300)
        self.check(1500)
        self.assertEqual(self.episode()["since"], 1300)
        self.check(1900)
        self.assertEqual(self.sent, [])
        self.check(1901)
        self.assertIn("Board idle 10 min", self.sent[0])
        self.assertIn("#1 (high) Task 1", self.sent[0])

    def test_once_then_thirty_minutes_and_cap_three_persisted(self):
        self.room(1, priority=1)
        self.check(1000)
        for at in (1600, 1601, 1700, 3400, 3401, 5201, 7001, 10000):
            self.check(at)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.episode()["count"], 3)
        self.assertIn("Board idle 70 min", self.sent[-1])

    def test_live_waiting_ceo_and_waiting_reviewer_end_episode(self):
        task = self.room(1, priority=2)
        self.check(1601)
        for status in ("waiting_human", "active"):
            chatroom.patch_room(task["id"], launched=True, live=True, status=status)
            self.check(1700)
            self.assertIsNone(self.episode())
        chatroom.patch_room(task["id"], live=False, workflow="done", workflowAt=1800)
        self.room(2, priority=2)
        self.check(2000)
        self.assertEqual(self.episode()["since"], 1800)

    def test_task_started_and_finished_between_checks_resets_cap(self):
        self.room(1, priority=2)
        task = self.room(2)
        self.check(1601)
        chatroom.patch_room(task["id"], launched=True, live=False,
                            launchedAt=1700, workflow="done", workflowAt=1750)
        self.check(2400)
        self.assertEqual(self.episode()["since"], 1750)
        self.assertEqual(self.episode()["count"], 1)
        self.assertEqual(len(self.sent), 2)

    def test_documents_opt_out_and_no_po(self):
        self.room(1, priority=2)
        self.project["kind"] = "documents"
        self.check(1700)
        self.assertEqual(len(self.sent), 1)
        for fields in ({"idleBoardAlert": False}, {"poRoomId": ""}):
            self.project.update(fields)
            self.check(9000)
            self.assertIsNone(self.episode())
        self.assertEqual(len(self.sent), 1)

    def gate(self, when="merged", on_ready="start", **fields):
        dep = self.room(140)
        task = self.room(141, after=[{"task": dep["id"], "when": when}],
                         onReady=on_ready, **fields)
        return dep, task

    def test_merged_start_once_uses_normal_launcher(self):
        dep, task = self.gate()
        self.check(1000)
        self.launch.assert_not_called()
        self.git[dep["id"]] = {"merged": True}
        self.check(1300)
        self.check(1600)
        self.launch.assert_called_once()
        self.assertEqual(self.sent, ["#141 started: #140 merged."])
        self.assertEqual(chatroom.get_room(task["id"])["gateAction"]["result"], "started")

    def test_approved_tell_uses_last_verdict(self):
        dep, task = self.gate("approved", "tell")
        chatroom.patch_participant(dep["id"], "codex", {"reviews": [
            {"endedAt": 10, "verdict": "approve"},
            {"endedAt": 20, "verdict": "changes_requested"}]})
        self.check(1000)
        self.assertEqual(self.sent, [])
        chatroom.patch_participant(dep["id"], "codex", {"review": {"endedAt": 30, "verdict": "approve"}})
        self.check(1300)
        self.check(1500)
        self.assertEqual(self.sent, ["#141 is unblocked: #140 approved."])
        self.launch.assert_not_called()
        self.assertFalse(chatroom.get_room(task["id"])["launched"])
        self.check(2000)
        self.assertIn("#141 (medium)", self.sent[-1])

    def test_all_gates_must_be_satisfied(self):
        dep, task = self.gate()
        other = self.room(142)
        chatroom.patch_room(task["id"], after=[{"task": dep["id"]}, {"task": other["id"]}])
        self.git[dep["id"]] = {"merged": True}
        self.check(1000)
        self.launch.assert_not_called()
        self.git[other["id"]] = {"merged": True}
        self.check(1300)
        self.launch.assert_called_once()

    def test_failed_launch_is_told_and_never_retried(self):
        dep, task = self.gate()
        self.git[dep["id"]] = {"merged": True}
        self.launch.side_effect = d.StartRoomError("allocation refused")
        self.check(1000)
        self.check(1300)
        self.assertIn("allocation refused", self.sent[0])
        self.launch.assert_called_once()
        self.assertEqual(chatroom.get_room(task["id"])["gateAction"]["result"], "failed")

    def test_successful_launch_notice_retries_without_launching_again(self):
        dep, task = self.gate()
        self.git[dep["id"]] = {"merged": True}
        with mock.patch.object(board, "deliver", return_value=False):
            self.check(1000)
        self.check(1300)
        self.launch.assert_called_once()
        self.assertEqual(self.sent, ["#141 started: #140 merged."])

    def test_deleted_or_moved_dependency_never_satisfies(self):
        dep, task = self.gate()
        self.git[dep["id"]] = {"merged": True}
        chatroom.patch_room(dep["id"], projectId="other")
        self.check(1000)
        self.launch.assert_not_called()

    def test_validation_defaults_and_refusals(self):
        dep = self.room(140)
        gates = board.validate([{"task": dep["id"]}], "start", "p")
        self.assertEqual(gates, [{"task": dep["id"], "when": "merged"}])
        for gates, mode, pid in (([{"task": "room-deleted"}], "start", "p"),
                                 ([{"task": dep["id"]}], "start", "other"),
                                 ([{"task": dep["id"], "when": "done"}], "start", "p"),
                                 ([], "retry", "p"), (None, "start", "p")):
            with self.subTest(gates=gates, mode=mode, pid=pid), self.assertRaises(ValueError):
                board.validate(gates, mode, pid)
        with self.assertRaises(ValueError):
            board.validate([{"task": dep["id"]}], "start", "p", dep["id"])

    def test_tick_interval_and_disabled_digest(self):
        self.room(1, priority=2)
        self.project["digestIntervalMin"] = 0
        with mock.patch("board.time.time", return_value=1000):
            board.maybe_tick()
        with mock.patch("board.time.time", return_value=1100), mock.patch.object(board, "check") as chk:
            board.maybe_tick()
            chk.assert_not_called()
        with mock.patch("board.time.time", return_value=1300), mock.patch.object(board, "check") as chk:
            board.maybe_tick()
            chk.assert_called_once()

    def test_mcp_create_validates_before_mutation_and_exposes_defaults(self):
        dep = self.room(140)
        task = self.room(141)
        ctx = {"projectId": "p", "part": {"agent": "codex"}, "room": {"id": "po"}, "identity": "codex"}
        args = {"title": "New", "spec": "Spec", "after": [{"task": dep["id"]}]}
        with mock.patch.object(d, "create_task", return_value=(True, task, "")) as create:
            result = et._create_task(ctx, args, mock.Mock())
            self.assertEqual(result["after"], [{"task": "#140", "when": "merged"}])
            self.assertEqual(result["onReady"], "start")
            self.assertEqual(chatroom.get_room(task["id"])["after"][0]["task"], dep["id"])
            create.reset_mock()
            for extra in ({"after": [{"task": "room-deleted"}]}, {"onReady": "invalid"}, {"start": True}):
                with self.assertRaises(et.ToolError):
                    et._create_task(ctx, {**args, **extra}, mock.Mock())
            create.assert_not_called()

    def test_mcp_update_gate_only_clears_action_only_when_changed(self):
        dep, task = self.gate()
        ctx = {"projectId": "p", "part": {"agent": "codex"}, "room": {"id": "po"}, "identity": "codex"}
        chatroom.patch_room(task["id"], gateAction={"result": "failed"})
        with mock.patch.object(et, "is_admin_caller", return_value=True):
            result = et._update_task(ctx, {"taskId": task["id"], "onReady": "tell"}, mock.Mock())
            self.assertEqual(result["onReady"], "tell")
            self.assertIsNone(chatroom.get_room(task["id"])["gateAction"])
            chatroom.patch_room(task["id"], gateAction={"result": "tell"})
            et._update_task(ctx, {"taskId": task["id"], "onReady": "tell"}, mock.Mock())
            self.assertEqual(chatroom.get_room(task["id"])["gateAction"], {"result": "tell"})
            with self.assertRaises(et.ToolError):
                et._update_task(ctx, {"taskId": task["id"], "after": [{"task": "room-deleted"}]}, mock.Mock())
            et._update_task(ctx, {"taskId": task["id"], "after": []}, mock.Mock())
            self.assertEqual(chatroom.get_room(task["id"])["after"], [])

    def test_non_admin_cannot_sneak_gates_into_workflow_write(self):
        task = self.room(141)
        ctx = {"projectId": "p", "room": task, "identity": "codex"}
        with mock.patch.object(et, "is_admin_caller", return_value=False), self.assertRaises(et.ToolError):
            et._update_task(ctx, {"taskId": task["id"], "workflow": "inreview", "after": []}, mock.Mock())


class DeliveryTests(unittest.TestCase):
    def test_busy_po_queues_and_stopped_po_gets_chat(self):
        room = {"id": "po", "participants": [{"identity": "claude", "kind": "agent"}]}
        sess = mock.Mock()
        with mock.patch.object(chatroom, "get_room", return_value=room), \
             mock.patch.object(chatroom, "po_identity", return_value="claude"), \
             mock.patch.object(rotation, "_pty", return_value=sess) as pty, \
             mock.patch.object(rotation, "is_rotating", return_value=False), \
             mock.patch.object(rotation, "awaiting_handover", return_value=False), \
             mock.patch.object(rotation, "_idle", side_effect=AssertionError("must not check busy")), \
             mock.patch.object(d, "_type_input", return_value=True) as typed, \
             mock.patch.object(chatroom, "post_report", return_value=True) as posted, \
             mock.patch.object(d, "hub_launcher", side_effect=AssertionError("must never resume PO")), \
             mock.patch.object(po_usage, "head", return_value="usage unknown"):
            self.assertTrue(board.deliver({"poRoomId": "po"}, "ready"))
            typed.assert_called_once_with(sess, "[board] usage unknown | ready")
            posted.assert_not_called()
            pty.return_value = None
            self.assertTrue(board.deliver({"poRoomId": "po"}, "ready"))
            self.assertFalse(posted.call_args.kwargs["wake"])


class UsageTests(unittest.TestCase):
    def snapshot(self):
        def window(pct, minutes, pool=None):
            return {"percent": pct, "windowMinutes": minutes, "pool": pool,
                    "ageSeconds": 0, "trusted": True}
        return {"checkedAt": 1000, "sources": [
            {"source": "claude", "state": "ok", "windows": [window(12, 300), window(88, 10080)]},
            {"source": "codex", "state": "ok", "windows": [window(12, 300, usage.CODEX_MAIN_POOL),
                                                                  window(86, 300, usage.CODEX_RESERVE_POOL)]}]}

    def test_fresh_head_names_windows_and_pools(self):
        self.assertEqual(po_usage.head(self.snapshot(), 1000),
                         "usage: Claude 5h 12% · 7d 88% | Codex main 5h 12% · reserve 5h 86%")

    def test_stale_cache_and_stale_source_are_unknown(self):
        self.assertEqual(po_usage.head(self.snapshot(), 1901), "usage unknown")
        snap = self.snapshot()
        snap["sources"][1]["windows"][0]["ageSeconds"] = 850
        result = po_usage.head(snap, 1100)
        self.assertIn("Codex main 5h unknown", result)
        self.assertIn("reserve 5h 86%", result)

    def test_failed_untrusted_missing_rolled_over_readings(self):
        for fields in ({"trusted": False}, {"percent": None}, {"rolledOver": True}, {"resetUnknown": True}):
            snap = self.snapshot()
            snap["sources"][0]["windows"][0].update(fields)
            self.assertIn("Claude 5h unknown", po_usage.head(snap, 1000))
        snap["sources"][0]["state"] = "unavailable"
        self.assertIn("7d unknown", po_usage.head(snap, 1000))

    def test_fresh_and_rotation_prompts_and_due(self):
        project = {"id": "p", "name": "Project", "path": "C:/project", "home": "C:/home"}
        with mock.patch.object(po_usage, "head", return_value="usage: live"), \
             mock.patch.object(rotation, "_open_points", return_value=""):
            self.assertIn("usage: live", d.made_po_fresh_input(project))
            self.assertIn("usage: live", d.made_po_first_input(project))
            self.assertIn("usage: live", rotation.first_prompt(project, {"id": "po"}, "old", 100))
            line, _ = due.wake_line({"kind": "po", "file": "handover"},
                                    [{"due": 1000, "what": "check", "line": "check"}], 1000)
            self.assertIn("[due] usage: live", line)

    def test_digest_typed_line_has_usage_including_stall_helper(self):
        sess = mock.Mock()
        with mock.patch.object(po_usage, "head", return_value="usage: live"):
            d._type_input(sess, "[digest] project: news")
        sess.send_line.assert_called_once_with("[digest] usage: live | project: news")


if __name__ == "__main__":
    unittest.main()
