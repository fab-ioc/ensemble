"""A stopped worktree task can swap its owner and on-mention reviewer."""

import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools


class TaskOwnerSwap(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(chatroom, "ROOMS_DIR", self.root / "rooms")
        patch.start()
        self.addCleanup(patch.stop)
        self.worktree = self.root / "task" / "repo"
        self.worktree.mkdir(parents=True)

    def test_one_update_swaps_owner_and_a_never_run_reviewer_starts_fresh(self):
        made = chatroom.create_room("task", [
            {"identity": "codex", "agent": "codex", "role": "engineer",
             "sessionId": "old-owner-session", "cwd": str(self.worktree)},
            {"identity": "claude", "agent": "claude", "role": "reviewer",
             "sessionId": "", "cwd": ""},
        ])
        rid = made["id"]
        room = chatroom.get_room(rid, public=False)
        room.update(cwd=str(self.worktree), sharedCwd=True, launched=True,
                    mode="collab", spec="Build the task.", stoppedAt=1)
        chatroom.update_room(room)
        planner = chatroom.create_room("planner", [
            {"identity": "planner", "agent": "claude", "role": "planner"}])
        caller = chatroom.get_room(planner["id"], public=False)
        ctx = {"room": caller, "identity": "planner",
               "part": chatroom.participant(caller, "planner"), "projectId": ""}

        # The identity-free list is one ensemble_update_task call. Matching by
        # agent kind retains both seats, including the reviewer's empty session.
        ensemble_tools._update_task(ctx, {"taskId": rid, "agents": [
            {"agent": "claude", "role": "engineer"},
            {"agent": "codex", "role": "reviewer"},
        ]}, None)
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(chatroom.participant(room, "claude")["sessionId"], "")
        self.assertNotIn("fresh", chatroom.participant(room, "claude"))
        self.assertEqual(chatroom.participant(room, "codex")["sessionId"],
                         "old-owner-session")
        self.assertTrue(chatroom.is_on_mention(room, chatroom.participant(room, "codex")))

        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
        used = []

        def create(cmd, cwd=None, **kwargs):
            used.append(cwd)
            self.assertTrue(Path(cwd).is_dir())
            return types.SimpleNamespace(id="new-terminal")

        with (mock.patch.object(dashboard, "_pty_alive", return_value=False),
              mock.patch.object(dashboard, "load_projects", return_value=[]),
              mock.patch.object(dashboard, "hub_launch_model", return_value=("", "")),
              mock.patch.object(dashboard.agents, "get_agent", return_value=object()),
              mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
              mock.patch.object(dashboard, "_agent_hook_env", return_value={}),
              mock.patch.object(dashboard, "_record_typed_input_safely"),
              mock.patch.object(dashboard.BACKEND, "headless_launch",
                                side_effect=lambda cwd, argv, prompt: argv),
              mock.patch.object(dashboard.ptyrun, "create", side_effect=create),
              mock.patch.object(handler, "_task_agent_tools", return_value=[]),
              mock.patch.object(handler, "_mcp_wiring", return_value=([], [], {})),
              mock.patch.object(handler, "_resume_room_agent_pty",
                                side_effect=AssertionError("empty session resumed")),
              mock.patch.object(handler, "_deliver_after_resume")):
            started = handler._start_or_resume_room_now(room)
        self.assertEqual(used, [str(self.worktree)])
        self.assertEqual(started, [{"identity": "claude", "ptyId": "new-terminal"}])
        saved = chatroom.get_room(rid, public=False)
        self.assertEqual(chatroom.participant(saved, "claude")["cwd"], str(self.worktree))
        self.assertTrue(chatroom.participant(saved, "claude")["sessionId"])

    def test_missing_recorded_cwd_resumes_in_task_worktree(self):
        missing = self.root / "removed"
        room = {"id": "room-test", "title": "task", "cwd": str(self.worktree),
                "sharedCwd": True, "tokens": {}, "participants": []}
        part = {"identity": "claude", "agent": "claude", "kind": "agent",
                "model": "", "sessionId": "prior-session", "cwd": str(missing)}
        used = []
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))

        def create(cmd, cwd=None, **kwargs):
            used.append(cwd)
            self.assertTrue(Path(cwd).is_dir())
            return types.SimpleNamespace(id="resumed-terminal")

        with (mock.patch.object(dashboard, "hub_launch_model", return_value=("", "")),
              mock.patch.object(dashboard.agents, "get_agent", return_value=object()),
              mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
              mock.patch.object(dashboard, "_agent_hook_env", return_value={}),
              mock.patch.object(dashboard.BACKEND, "headless_launch",
                                side_effect=lambda cwd, argv, prompt: argv),
              mock.patch.object(dashboard.ptyrun, "create", side_effect=create),
              mock.patch.object(handler, "_task_agent_tools", return_value=[]),
              mock.patch.object(handler, "_mcp_wiring", return_value=([], [], {}))):
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                info = handler._resume_room_agent_pty(room, part)
        self.assertEqual(used, [str(self.worktree)])
        self.assertEqual(info["cwd"], str(self.worktree))
        self.assertEqual(len(log.getvalue().splitlines()), 1)
        self.assertIn("cwd missing", log.getvalue())
        self.assertFalse(missing.exists())

    def test_codex_with_unbackfilled_session_resumes_after_stop_or_restart(self):
        made = chatroom.create_room("task", [
            {"identity": "codex", "agent": "codex", "role": "engineer",
             "sessionId": "", "cwd": str(self.worktree)},
        ])
        rid = made["id"]
        room = chatroom.get_room(rid, public=False)
        room.update(cwd=str(self.worktree), sharedCwd=True, launched=True,
                    mode="solo", spec="Build the task.", stoppedAt=1)
        chatroom.update_room(room)
        for restart in (None, {"codex": "working"}):
            with self.subTest(restart=restart):
                room = chatroom.get_room(rid, public=False)
                resumed = []
                handler = dashboard.Handler.__new__(dashboard.Handler)

                def resume(room_full, part, collab=True, seed="", human=False):
                    resumed.append((part["identity"], part["cwd"], seed))
                    return {"ptyId": "resumed-terminal", "sessionId": "",
                            "cwd": part["cwd"], "prompted": False}

                with (mock.patch.object(dashboard, "_pty_alive", return_value=False),
                      mock.patch.object(dashboard, "load_projects", return_value=[]),
                      mock.patch.object(handler, "_resume_room_agent_pty", side_effect=resume),
                      mock.patch.object(handler, "_launch_room_agent_pty",
                                        side_effect=AssertionError("ran Codex relaunched")),
                      mock.patch.object(handler, "_deliver_after_resume")):
                    handler._start_or_resume_room_now(room, restart=restart)
                self.assertEqual(resumed, [("codex", str(self.worktree), "Build the task.")])


if __name__ == "__main__":
    unittest.main()
