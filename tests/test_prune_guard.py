"""#260: a pruned task never runs again.

Its folder, worktree and conversations are gone. A resume would make the
removed folder again, empty, and start an agent with no history in it; a move
out of Done would ask the board to start it. All three are refused, with a
message that says why and what to do instead.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
from test_quiet_restart import _Hub


class PrunedTaskStaysDone(_Hub):
    def setUp(self):
        super().setUp()
        self.rid = self.room(spec="Sell the X5.")
        full = chatroom.get_room(self.rid, public=False)
        full.update(workflow="done", pruned={"at": time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1)),
                                             "bytes": 1000})
        chatroom.update_room(full)

    def test_resume_is_refused_with_the_reason(self):
        code, out = self.handler()._room_resume({"roomId": self.rid, "text": "carry on"})
        self.assertEqual(code, 409)
        self.assertEqual(out["error"], "pruned")
        self.assertIn("pruned on 2026-10-01", out["message"])
        self.assertIn("Make a new task from its spec", out["message"])
        self.assertEqual(self.starts, 0)

    def test_no_terminal_is_launched_or_resumed(self):
        h = object.__new__(dashboard.Handler)
        full = chatroom.get_room(self.rid, public=False)
        part = chatroom.participant(full, "claude")
        with mock.patch.object(dashboard.os, "makedirs") as mk:
            with self.assertRaisesRegex(RuntimeError, "cannot be resumed"):
                dashboard.Handler._launch_room_agent_pty(h, full, part, "Do it.")
            with self.assertRaisesRegex(RuntimeError, "cannot be resumed"):
                dashboard.Handler._resume_room_agent_pty(h, full, part)
        mk.assert_not_called()

    def test_it_cannot_leave_done(self):
        ok, _room, err = dashboard.update_task(self.rid, workflow="inprogress")
        self.assertFalse(ok)
        self.assertEqual(err, "pruned")
        self.assertEqual(chatroom.get_room(self.rid, public=False)["workflow"], "done")
        ok, _room, err = dashboard.update_task(self.rid, workflow="done", priority=1)
        self.assertTrue(ok, err)

    def test_the_board_tool_says_why(self):
        ctx = {"room": chatroom.get_room(self.rid, public=False), "identity": "claude"}
        room = chatroom.get_room(self.rid, public=False)
        with mock.patch.object(ensemble_tools, "_load_target", lambda *a: room), \
                mock.patch.object(ensemble_tools, "_check_write_scope", lambda *a: None), \
                mock.patch.object(ensemble_tools, "is_admin_caller", lambda *a: True):
            with self.assertRaisesRegex(ensemble_tools.ToolError, "pruned on 2026-10-01"):
                ensemble_tools._update_task(ctx, {"taskId": self.rid, "workflow": "todo"}, mock.Mock())


if __name__ == "__main__":
    unittest.main()
