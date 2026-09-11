from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools


class _FakeAgent:
    def __init__(self, kind: str):
        self.display_name = kind.title()

    def installed(self) -> bool:
        return True


class _FakeHandler:
    _start_room = dashboard.Handler._start_room
    _start_or_resume_room = dashboard.Handler._start_or_resume_room

    def __init__(self):
        self.launched = []
        self.resumed = []
        self.resume_notes = []

    def _launch_room_agent_pty(self, room, part, task, collab=True):
        self.launched.append((part["identity"], part["agent"], part.get("model", "")))
        return {"ptyId": len(self.launched), "cwd": room.get("cwd", ""),
                "sessionId": f"session-{len(self.launched)}"}

    def _resume_room_agent_pty(self, room, part, collab=True, seed="", human=False):
        self.resumed.append((part["identity"], part["agent"], part.get("model", "")))
        return {"ptyId": 100 + len(self.resumed), "cwd": room.get("cwd", ""),
                "sessionId": part.get("sessionId", ""), "prompted": False}

    def _send_resume_note(self, room_id, identity, pty_id):
        self.resume_notes.append((room_id, identity, pty_id))


def _window(kind: str, percent, *, trusted=True, rolled_over=False,
            reset_unknown=False):
    return {"kind": kind, "percent": percent, "trusted": trusted,
            "rolledOver": rolled_over, "resetUnknown": reset_unknown}


def _snapshot(claude_windows, codex_windows):
    return {
        "warnPercent": 80,
        "alarmPercent": 95,
        "sources": [
            {"source": "claude", "state": "ok", "windows": claude_windows},
            {"source": "codex", "state": "ok", "windows": codex_windows},
        ],
    }


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_rooms_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.agent_patch = mock.patch.object(
            dashboard.agents, "get_agent", side_effect=lambda kind: _FakeAgent(kind))
        self.agent_patch.start()

    def tearDown(self):
        self.agent_patch.stop()
        chatroom.ROOMS_DIR = self.old_rooms_dir
        self.temp.cleanup()

    def room(self, preference=None, *, launched=False):
        seats = preference or [
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ]
        members = [{"identity": s["agent"], "agent": s["agent"],
                    "model": s.get("model", ""), "role": s.get("role", "")}
                   for s in seats]
        created = chatroom.create_room("allocation test", members)
        room = chatroom.get_room(created["id"], public=False)
        room.update({"mode": "solo" if len(seats) == 1 else "collab",
                     "launched": launched, "spec": "Do the work.",
                     "cwd": self.temp.name})
        if preference is not None:
            room["agentPreference"] = preference
        chatroom.update_room(room)
        return room

    def test_preference_is_kept_below_warning(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        snap = _snapshot([_window("five_hour", 40), _window("seven_day", 55)],
                         [_window("five_hour", 20), _window("seven_day", 30)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertEqual([p["agent"] for p in chatroom.agent_participants(saved)],
                         ["claude", "codex"])
        self.assertFalse(saved["allocation"]["changed"])
        self.assertEqual(saved["allocation"]["usage"]["kinds"]["claude"]["percent"], 55)

    def test_swap_at_warning_uses_alternative_model_and_default(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer",
             "alt": {"agent": "codex", "model": "gpt-owner"}},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        original_tokens = dict(room["tokens"])
        snap = _snapshot([_window("five_hour", 80), _window("seven_day", 20)],
                         [_window("five_hour", 12), _window("seven_day", 18)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        chosen = saved["allocation"]["chosen"]
        self.assertEqual([(s["agent"], s["model"], s["role"]) for s in chosen], [
            ("codex", "gpt-owner", "engineer"),
            ("claude", "", "reviewer"),
        ])
        self.assertTrue(saved["allocation"]["changed"])
        self.assertEqual(saved["allocation"]["reason"],
                         "Owner switched to Codex: Claude 5-hour window at 80%.")
        self.assertEqual(saved["tokens"], original_tokens)
        self.assertEqual(chatroom.owners(saved), ["codex"])
        reviewer = next(p for p in chatroom.agent_participants(saved)
                        if p["role"] == "reviewer")
        self.assertTrue(chatroom.is_on_mention(saved, reviewer))

    def test_both_past_alarm_starts_as_preferred_and_says_so(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        snap = _snapshot([_window("five_hour", 96)], [_window("seven_day", 99)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertTrue(saved["launched"])
        self.assertFalse(saved["allocation"]["changed"])
        self.assertIn("both at or above the 95% alarm", saved["allocation"]["reason"])

    def test_unknown_reading_keeps_preference(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        snap = _snapshot(
            [_window("five_hour", None, rolled_over=True)],
            [_window("five_hour", 10)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertFalse(saved["allocation"]["changed"])
        self.assertIn("unavailable", saved["allocation"]["reason"])

    def test_malformed_cached_reading_never_blocks_start(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        snap = _snapshot([_window("five_hour", "not-a-number")],
                         [_window("five_hour", 10)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertTrue(saved["launched"])
        self.assertFalse(saved["allocation"]["changed"])
        self.assertEqual(saved["allocation"]["reason"],
                         "Preferred line-up kept because the allowance check failed.")
        self.assertEqual(saved["allocation"]["usage"]["error"], "ValueError")

    def test_snapshot_failure_never_blocks_start(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        with mock.patch.object(dashboard.usage, "snapshot", side_effect=OSError("cache")):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertTrue(saved["launched"])
        self.assertEqual(saved["allocation"]["usage"]["error"], "OSError")

    def test_untrusted_window_counts_at_its_floor(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        snap = _snapshot([_window("five_hour", 86, trusted=False)],
                         [_window("five_hour", 10)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            _FakeHandler()._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertTrue(saved["allocation"]["changed"])
        self.assertIn("at least 86%", saved["allocation"]["reason"])

    def test_uninstalled_preference_is_never_chosen(self):
        preference = [{"agent": "claude", "model": "opus", "role": "engineer",
                       "alt": {"agent": "codex", "model": "gpt-owner"}}]
        chosen, allocation = dashboard.choose_first_launch_allocation(
            preference,
            _snapshot([_window("five_hour", 10)], [_window("five_hour", 99)]),
            installed=lambda kind: kind == "codex")

        self.assertEqual(chosen, [
            {"agent": "codex", "model": "gpt-owner", "role": "engineer"}])
        self.assertTrue(allocation["changed"])
        self.assertIn("not installed", allocation["reason"])

    def test_resumed_task_keeps_its_existing_kind(self):
        preference = [{"agent": "claude", "model": "opus", "role": "engineer"}]
        room = self.room(preference, launched=True)
        room["allocation"] = {"preferred": preference, "chosen": preference,
                              "reason": "Earlier choice.", "changed": False,
                              "at": 1, "usage": {}}
        chatroom.update_room(room)
        handler = _FakeHandler()
        with mock.patch.object(dashboard.usage, "snapshot",
                               side_effect=AssertionError("resume read usage")):
            handler._start_or_resume_room(room)

        self.assertEqual(handler.resumed[0][1:], ("claude", "opus"))
        self.assertEqual(chatroom.agent_participants(room)[0]["agent"], "claude")

    def test_old_draft_without_preference_behaves_as_before(self):
        room = self.room(None, launched=False)
        handler = _FakeHandler()
        with mock.patch.object(dashboard.usage, "snapshot",
                               side_effect=AssertionError("legacy room read usage")):
            handler._start_room(room)

        saved = chatroom.get_room(room["id"], public=False)
        self.assertEqual([p["agent"] for p in chatroom.agent_participants(saved)],
                         ["claude", "codex"])
        self.assertNotIn("allocation", saved)

    def test_start_tool_result_names_chosen_agents_and_reason(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
            {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
        ])
        caller_created = chatroom.create_room(
            "caller", [{"identity": "claude", "agent": "claude", "role": "planner"}])
        caller = chatroom.get_room(caller_created["id"], public=False)
        ctx = {"room": caller, "identity": "claude",
               "part": chatroom.participant(caller, "claude"), "projectId": ""}
        snap = _snapshot([_window("five_hour", 82)], [_window("five_hour", 10)])
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            result = ensemble_tools._start_task(ctx, {"taskId": room["id"]}, _FakeHandler())

        self.assertEqual([(a["agent"], a["role"]) for a in result["chosenAgents"]],
                         [("codex", "engineer"), ("claude", "reviewer")])
        self.assertEqual(result["note"],
                         "Owner switched to Codex: Claude 5-hour window at 82%.")

    def test_start_tool_reports_unavailable_kind_cleanly(self):
        room = self.room([
            {"agent": "claude", "model": "opus", "role": "engineer"},
        ])
        caller_created = chatroom.create_room(
            "caller", [{"identity": "claude", "agent": "claude", "role": "planner"}])
        caller = chatroom.get_room(caller_created["id"], public=False)
        ctx = {"room": caller, "identity": "claude",
               "part": chatroom.participant(caller, "claude"), "projectId": ""}
        with mock.patch.object(dashboard.agents, "get_agent", return_value=None):
            with self.assertRaisesRegex(ensemble_tools.ToolError,
                                        "agent_unavailable:claude"):
                ensemble_tools._start_task(ctx, {"taskId": room["id"]}, _FakeHandler())

    def test_new_task_persists_preference_and_alternative(self):
        task_dir = Path(self.temp.name) / "task"
        agent_list = [{"agent": "claude", "model": "opus", "role": "engineer",
                       "alt": {"agent": "codex", "model": "gpt-owner"}}]
        workspace = {"mode": "empty", "taskDir": str(task_dir)}
        with mock.patch.object(dashboard, "setup_session_workspace",
                               return_value=(True, str(task_dir), workspace, "")):
            ok, room, error = dashboard.create_task(
                "new task", "Do the work.", "", agent_list, "empty")

        self.assertTrue(ok, error)
        self.assertEqual(room["agentPreference"], agent_list)
        task_json = (task_dir / "task.json").read_text(encoding="utf-8")
        self.assertIn('"agentPreference"', task_json)

    def test_create_task_start_result_names_chosen_agents_and_reason(self):
        caller_created = chatroom.create_room(
            "caller", [{"identity": "claude", "agent": "claude", "role": "planner"}])
        caller = chatroom.get_room(caller_created["id"], public=False)
        ctx = {"room": caller, "identity": "claude",
               "part": chatroom.participant(caller, "claude"), "projectId": ""}
        task_dir = Path(self.temp.name) / "started-task"
        workspace = {"mode": "empty", "taskDir": str(task_dir)}
        args = {
            "title": "start now", "spec": "Do the work.", "workspace": "empty",
            "start": True,
            "agents": [
                {"agent": "claude", "model": "opus", "role": "engineer"},
                {"agent": "codex", "model": "gpt-review", "role": "reviewer"},
            ],
        }
        snap = _snapshot([_window("five_hour", 80)], [_window("five_hour", 10)])
        with mock.patch.object(dashboard, "setup_session_workspace",
                               return_value=(True, str(task_dir), workspace, "")), \
                mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            result = ensemble_tools._create_task(ctx, args, _FakeHandler())

        self.assertEqual([(a["agent"], a["role"]) for a in result["agents"]],
                         [("codex", "engineer"), ("claude", "reviewer")])
        self.assertEqual(result["note"],
                         "Owner switched to Codex: Claude 5-hour window at 80%.")


if __name__ == "__main__":
    unittest.main()
