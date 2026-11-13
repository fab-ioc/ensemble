"""A PO switch or a usage-limit failover starts the replacement with the seat's
stored bearer token (ED-356: it was launched from the room's public view and
started with CHAT_TOKEN empty, so it had no Ensemble tools); a launch or a
resume of a seat that has no token is refused, never started deaf."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import rotation  # noqa: E402
import test_po_switch as _switch  # noqa: E402


class _RealLauncher:
    """Mixin: the hub's real ``Handler._launch_room_agent_pty`` behind the
    switch, with ``ptyrun.create`` recording each command's env instead of
    starting a terminal."""

    def real_launcher(self, project):
        self.created: list[dict] = []
        self.rooms_passed: list[dict] = []

        def create(cmd, cwd=None, env=None, label="", meta=None, **kw):
            self.created.append({"cmd": list(cmd), "env": dict(env or {}), "meta": meta})
            return types.SimpleNamespace(id=f"pty-{len(self.created)}")
        for p in (mock.patch.object(dashboard, "load_projects", return_value=[project]),
                  mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
                  mock.patch.object(dashboard.Handler, "_codex_ensemble_only_args",
                                    return_value=[]),
                  mock.patch.object(dashboard, "check_signed_in"),
                  mock.patch.object(dashboard.first_run, "watch"),
                  mock.patch.object(dashboard, "_record_typed_input_safely"),
                  mock.patch.object(dashboard.BACKEND, "headless_launch",
                                    side_effect=lambda cwd, argv, prompt: argv),
                  mock.patch.object(dashboard.ptyrun, "create", side_effect=create)):
            p.start()
            self.addCleanup(p.stop)
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
        real = handler._launch_room_agent_pty

        def spy(room, part, *a, **k):
            self.rooms_passed.append(room)
            return real(room, part, *a, **k)
        handler._launch_room_agent_pty = spy
        dashboard.hub_launcher.return_value = handler
        return handler

    def stored_token(self, project, ident="po"):
        tokens = chatroom.get_room(project["poRoomId"], public=False)["tokens"]
        return next(t for t, i in tokens.items() if i == ident)


class SwitchLaunchTokenTests(_RealLauncher, _switch._Switch):
    def test_a_manual_switch_starts_the_replacement_with_the_seat_token(self):
        project, _ = self.po()
        self.real_launcher(project)
        token = self.stored_token(project)
        out = rotation.request_switch(project, "codex")
        self.assertEqual(out["agent"], "codex")
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]["env"]["CHAT_TOKEN"], token)
        self.assertTrue(token)
        # The room handed to the launcher is the stored one, tokens included.
        self.assertEqual(self.rooms_passed[0].get("tokens"),
                         chatroom.get_room(project["poRoomId"], public=False)["tokens"])
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual((part["agent"], part["ptyId"]), ("codex", "pty-1"))
        self.assertEqual(self.killed, ["old-pty"])

    def test_a_codex_to_claude_switch_wires_the_seat_token_into_its_mcp_config(self):
        self.snap = _switch._snap(15, 15)
        project, _ = self.po("codex", "gpt-5.6-sol")
        self.real_launcher(project)
        token = self.stored_token(project)
        rotation.request_switch(project, "claude", "sonnet")
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]["env"]["CHAT_TOKEN"], token)
        cmd = self.created[0]["cmd"]
        cfg = Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8")
        self.assertIn(f"Bearer {token}", cfg)

    def test_a_usage_limit_failover_starts_the_replacement_with_the_seat_token(self):
        project, item = self.po()
        self.real_launcher(project)
        token = self.stored_token(project)
        out = rotation.failover_po(project, item)
        self.assertEqual(out["result"], "switched")
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.created[0]["env"]["CHAT_TOKEN"], token)
        self.assertIn("tokens", self.rooms_passed[0])

    def test_a_switch_of_a_seat_with_no_token_fails_and_leaves_the_old_po(self):
        project, _ = self.po()
        self.real_launcher(project)
        chatroom.patch_room(project["poRoomId"], tokens={})
        before = chatroom.participant(self.saved(project), "po")
        rotation.request_switch(project, "codex")
        self.assertEqual(rotation._LAST_SWITCH[project["poRoomId"]]["result"], "failed")
        self.assertEqual(self.created, [])
        self.assertEqual(self.killed, [])
        after = chatroom.participant(self.saved(project), "po")
        self.assertEqual((after["agent"], after["sessionId"], after["ptyId"]),
                         (before["agent"], before["sessionId"], before["ptyId"]))
        self.assertIn("no chat token",
                      rotation._LAST_SWITCH[project["poRoomId"]]["reason"])


class LaunchWithoutTokensTests(_RealLauncher, _switch._Switch):
    """A room passed without its tokens (a public view): the seat's stored
    token is used; with none stored either, nothing is started."""

    def setUp(self):
        super().setUp()
        self.project, _ = self.po()
        self.handler = self.real_launcher(self.project)
        self.rid = self.project["poRoomId"]

    def public_seat(self):
        room = chatroom.get_room(self.rid)
        self.assertNotIn("tokens", room)
        return room, chatroom.participant(room, "po")

    def test_a_launch_from_a_public_room_uses_the_stored_token(self):
        room, part = self.public_seat()
        self.handler._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=False,
                                            prompt="carry on")
        self.assertEqual(self.created[0]["env"]["CHAT_TOKEN"], self.stored_token(self.project))

    def test_a_resume_from_a_public_room_uses_the_stored_token(self):
        room, part = self.public_seat()
        with mock.patch.object(dashboard.seat_folders, "resume_folder_check"):
            self.handler._resume_room_agent_pty(room, part, collab=False)
        self.assertEqual(self.created[0]["env"]["CHAT_TOKEN"], self.stored_token(self.project))

    def test_a_seat_with_no_token_anywhere_is_never_started(self):
        chatroom.patch_room(self.rid, tokens={})
        room, part = self.public_seat()
        full = chatroom.get_room(self.rid, public=False)
        cases = (
            ("launch public", lambda: self.handler._launch_room_agent_pty(
                room, part, "", collab=False, prompt="carry on")),
            ("launch stored", lambda: self.handler._launch_room_agent_pty(
                full, part, "", collab=False, prompt="carry on")),
            ("resume", lambda: self.handler._resume_room_agent_pty(full, part, collab=False)),
        )
        for name, call in cases:
            with self.subTest(name), self.assertRaises(RuntimeError) as cm:
                call()
            self.assertIn("no chat token", str(cm.exception))
        self.assertEqual(self.created, [])

    def test_seat_token_never_returns_empty(self):
        self.assertEqual(dashboard.seat_token({"id": "", "tokens": {"t": "po"}}, "po"), "t")
        with self.assertRaises(RuntimeError):
            dashboard.seat_token({"id": "", "tokens": {"t": "other"}}, "po")
        with self.assertRaises(RuntimeError):
            dashboard.seat_token({"id": "room-gone"}, "po")


if __name__ == "__main__":
    unittest.main()
