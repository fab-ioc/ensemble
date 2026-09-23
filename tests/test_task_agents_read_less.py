"""Task agents read less (report #78, caps 1 and 2 and the two hub-side items).

* Codex: a task owner and a reviewer are launched with
  ``-c tool_output_token_limit=<setting>``; a PO room, an adopted session and a
  cap of 0 get no flag; the flag goes before Codex's ``resume`` subcommand;
* Claude: the RTK task settings file registers task_tool_hook.py for Read
  (before the tool) and Read|Bash (after it, not in the background) with the
  caps from the settings; the file for agents outside the pilot does not;
* ensemble_get_task: the whole spec on a session's first read of a task, on
  ``spec: true`` and after the spec changed; a preview and how to get it
  otherwise; a second identity in the room is its own session; a task's
  allocation comes without its usage snapshot.
"""
from __future__ import annotations

import json
import shlex
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import ensemble_tools  # noqa: E402
import task_tool_hook  # noqa: E402
from test_task_numbers import Hub  # noqa: E402

FLAG = ["-c", "tool_output_token_limit=4000"]


def _settings(**over) -> dict:
    return {**dashboard._SETTINGS_DEFAULTS, **over}


class CodexArgs(unittest.TestCase):
    def setUp(self):
        for p in (mock.patch.object(dashboard, "load_settings", return_value=_settings()),
                  mock.patch.object(dashboard, "load_projects",
                                    return_value=[{"id": "p1", "poRoomId": "room-po"}])):
            p.start()
            self.addCleanup(p.stop)

    def test_a_task_gets_the_cap_and_a_po_or_adopted_room_does_not(self):
        self.assertEqual(dashboard._codex_task_args({"id": "room-t", "launched": True}), FLAG)
        self.assertEqual(dashboard._codex_task_args({"id": "room-t", "taskDir": "C:/x"}), FLAG)
        self.assertEqual(dashboard._codex_task_args({"id": "room-po", "launched": True}), [])
        self.assertEqual(dashboard._codex_task_args({"id": "room-t", "launched": True,
                                                     "adopted": True}), [])
        self.assertEqual(dashboard._codex_task_args({"id": "room-adhoc"}), [])

    def test_the_cap_is_a_setting_and_0_is_off(self):
        with mock.patch.object(dashboard, "load_settings", return_value=_settings(codexToolOutputTokens=2500)):
            self.assertEqual(dashboard._codex_task_args({"id": "room-t", "launched": True}),
                             ["-c", "tool_output_token_limit=2500"])
        with mock.patch.object(dashboard, "load_settings", return_value=_settings(codexToolOutputTokens=0)):
            self.assertEqual(dashboard._codex_task_args({"id": "room-t", "launched": True}), [])
        self.assertEqual(dashboard._SETTINGS_DEFAULTS["codexToolOutputTokens"], 4000)

    def test_the_setting_is_validated_like_the_others(self):
        tmp = Path(tempfile.mkdtemp())
        with mock.patch.object(dashboard, "SETTINGS_FILE", tmp / "settings.json"), \
                mock.patch.object(dashboard, "DASHBOARD_DIR", tmp):
            saved = dashboard.save_settings({"codexToolOutputTokens": "3000", "readCapBytes": -5,
                                             "readCapLines": 0})
            self.assertEqual((saved["codexToolOutputTokens"], saved["readCapBytes"], saved["readCapLines"]),
                             (3000, 0, 1))
            saved = dashboard.save_settings({"codexToolOutputTokens": "lots"})
            self.assertEqual(saved["codexToolOutputTokens"], 3000)


class CodexLaunch(unittest.TestCase):
    """The flag reaches Codex's argv at every headless launch of a task agent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.made: list[list[str]] = []

        def create(cmd, cwd=None, env=None, label="", meta=None, **kw):
            self.made.append(list(cmd))
            return types.SimpleNamespace(id="pty-new")
        for patch in (
                mock.patch.object(dashboard, "DASHBOARD_DIR", self.tmp),
                mock.patch.object(dashboard, "load_settings", return_value=_settings()),
                mock.patch.object(dashboard, "load_projects",
                                  return_value=[{"id": "p1", "poRoomId": "room-po"}]),
                mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
                mock.patch.object(dashboard.agents, "get_agent", return_value=object()),
                mock.patch.object(dashboard.BACKEND, "headless_launch",
                                  side_effect=lambda cwd, argv, prompt: argv),
                mock.patch.object(dashboard.ptyrun, "create", side_effect=create)):
            patch.start()
            self.addCleanup(patch.stop)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))

    def room(self, rid: str, identity: str = "codex", role: str = "engineer") -> tuple[dict, dict]:
        part = {"identity": identity, "agent": "codex", "kind": "agent", "sessionId": "",
                "cwd": str(self.tmp), "role": role}
        return {"id": rid, "title": "t", "cwd": str(self.tmp), "sharedCwd": True, "launched": True,
                "tokens": {"tok": identity}, "participants": [part]}, part

    def test_an_owner_a_reviewer_and_a_resume(self):
        room, part = self.room("room-t")
        self.handler._launch_room_agent_pty(room, part, "do it", collab=True)
        room, reviewer = self.room("room-t", "codex-2", "reviewer")
        self.handler._launch_room_agent_pty(room, reviewer, "", collab=True, prompt="review this")
        room, part = self.room("room-t")
        part["sessionId"] = "0199-codex-sid"
        self.handler._resume_room_agent_pty(room, part, collab=True)
        self.assertEqual(len(self.made), 3)
        for argv in self.made:
            self.assertEqual(argv[0], "codex")
            self.assertIn("tool_output_token_limit=4000", argv, argv)
            self.assertEqual(argv[argv.index("tool_output_token_limit=4000") - 1], "-c")
        self.assertEqual(self.made[2][-2:], ["resume", "0199-codex-sid"])
        self.assertLess(self.made[2].index("tool_output_token_limit=4000"), self.made[2].index("resume"))

    def test_a_po_room_is_left_out(self):
        room, part = self.room("room-po")
        self.handler._launch_room_agent_pty(room, part, "watch the project", collab=True)
        self.handler._resume_room_agent_pty(room, part, collab=True)
        self.assertEqual(len(self.made), 2)
        for argv in self.made:
            self.assertNotIn("tool_output_token_limit=4000", argv, argv)
            self.assertIn("mcp_servers.ensemble.bearer_token_env_var=\"CHAT_TOKEN\"", argv)


class ClaudeSettings(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, value in (("RTK_DIR", tmp / "rtk"),
                            ("RTK_CLAUDE_SETTINGS", tmp / "rtk" / "claude-task-settings.json"),
                            ("USAGE_CLAUDE_SETTINGS", tmp / "usage" / "claude-agent-settings.json")):
            patch = mock.patch.object(dashboard, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(dashboard, "load_settings", return_value=_settings())
        patch.start()
        self.addCleanup(patch.stop)

    def settings(self, rtk: bool) -> dict:
        with mock.patch.object(dashboard, "_rtk_task_room", return_value=rtk):
            args, _, _ = dashboard._rtk_task_wiring({"id": "room-x"}, "claude")
        return json.loads(Path(args[1]).read_text(encoding="utf-8"))

    def tool_hooks(self, settings: dict, event: str) -> list[tuple[str, dict]]:
        return [(entry.get("matcher"), h) for entry in settings["hooks"].get(event, [])
                for h in entry["hooks"] if "task_tool_hook.py" in h["command"]]

    def test_task_agents_get_the_read_cap_and_the_nag_strip(self):
        settings = self.settings(True)
        pre = self.tool_hooks(settings, "PreToolUse")
        post = self.tool_hooks(settings, "PostToolUse")
        self.assertEqual([m for m, _ in pre], ["Read"])
        self.assertEqual([m for m, _ in post], ["Read|Bash"])
        for _, h in pre + post:
            self.assertEqual(h["type"], "command")
            self.assertFalse(h.get("async"), "its note must be there when the model reads the result")
            self.assertLessEqual(h["timeout"], 5)
            words = shlex.split(h["command"])
            self.assertEqual(words[1], dashboard.TASK_TOOL_HOOK_SCRIPT.as_posix())
            self.assertEqual(words[2:], ["--bytes", str(task_tool_hook.CAP_BYTES_DEFAULT),
                                         "--lines", str(task_tool_hook.CAP_LINES_DEFAULT)])
        # rtk's rewrite still runs first, and the attention hooks are all there.
        pre_entries = settings["hooks"]["PreToolUse"]
        self.assertIn("hook claude", pre_entries[0]["hooks"][0]["command"])
        self.assertEqual(pre_entries[1]["matcher"], "Read")
        self.assertEqual(pre_entries[2]["matcher"], "AskUserQuestion|ExitPlanMode")
        self.assertTrue([h for entry in settings["hooks"]["PostToolUse"] for h in entry["hooks"]
                         if "agent_hook.py" in h["command"] and h.get("async")])

    def test_the_caps_come_from_the_settings(self):
        with mock.patch.object(dashboard, "load_settings",
                               return_value=_settings(readCapBytes=8192, readCapLines=120)):
            settings = self.settings(True)
        _, h = self.tool_hooks(settings, "PreToolUse")[0]
        self.assertEqual(shlex.split(h["command"])[2:], ["--bytes", "8192", "--lines", "120"])

    def test_agents_outside_the_pilot_are_untouched(self):
        settings = self.settings(False)
        self.assertNotIn("task_tool_hook.py", json.dumps(settings))
        self.assertNotIn("hook claude", json.dumps(settings))


class GetTask(Hub):
    def setUp(self):
        super().setUp()
        ensemble_tools.forget_specs_given()
        self.addCleanup(ensemble_tools.forget_specs_given)
        self.a = self.room("Alpha", self.ed, numbered=True)
        full = chatroom.get_room(self.a, public=False)
        full["spec"] = "# Alpha\n\n" + "Do the thing, then the other thing. " * 40
        full["allocation"] = {"reason": "kept", "changed": False, "at": 1.0,
                              "chosen": [{"identity": "claude", "agent": "claude"}],
                              "usage": {"snapshotState": "ok", "kinds": {"claude": {"percent": 55}}}}
        chatroom.update_room(full)
        self.spec = full["spec"]

    def tool(self, args, identity="claude"):
        handler = types.SimpleNamespace(_resume_room=lambda r: None, _ring_recipients=lambda *a: None,
                                        _ring_report=lambda *a: [])
        text, err = ensemble_tools.call("ensemble_get_task", args, self.a, identity, handler)
        self.assertFalse(err, text)
        return json.loads(text)

    def test_the_whole_spec_once_then_a_preview(self):
        first = self.tool({"taskId": "#1"})
        self.assertEqual(first["spec"], self.spec)
        self.assertNotIn("specPreview", first)
        second = self.tool({"taskId": "#1"})
        self.assertNotIn("spec", second)
        self.assertEqual(second["specPreview"], self.spec[:300] + "…")
        self.assertEqual(second["specNote"], "full spec: pass spec=true")
        self.assertLess(len(json.dumps(second)), len(json.dumps(first)) - 1000)
        self.assertEqual(second["recentMessages"], [])           # messages still default to 0

    def test_spec_true_and_a_changed_spec_bring_it_back(self):
        self.tool({"taskId": "#1"})
        self.assertEqual(self.tool({"taskId": "#1", "spec": True})["spec"], self.spec)
        self.assertNotIn("spec", self.tool({"taskId": "#1", "spec": False}))
        full = chatroom.get_room(self.a, public=False)
        full["spec"] = self.spec + "\n\nAlso: tests."
        chatroom.update_room(full)
        self.assertEqual(self.tool({"taskId": "#1"})["spec"], full["spec"])
        self.assertIn("specPreview", self.tool({"taskId": "#1"}))

    def test_each_session_is_its_own_first_reader(self):
        self.tool({"taskId": "#1"})
        self.assertEqual(self.tool({"taskId": "#1"}, identity="codex")["spec"], self.spec)
        self.assertIn("specPreview", self.tool({"taskId": "#1"}, identity="codex"))
        # A new terminal for the same identity (a relaunch) reads it once more.
        chatroom.patch_participant(self.a, "claude", {"ptyId": "pty-2"})
        self.assertEqual(self.tool({"taskId": "#1"})["spec"], self.spec)
        self.assertIn("specPreview", self.tool({"taskId": "#1"}))
        # And after a hub restart, everyone does.
        ensemble_tools.forget_specs_given()
        self.assertEqual(self.tool({"taskId": "#1"})["spec"], self.spec)

    def test_the_allocation_comes_without_its_usage_snapshot(self):
        out = self.tool({"taskId": "#1"})
        self.assertEqual(out["allocation"], {"reason": "kept", "changed": False, "at": 1.0,
                                             "chosen": [{"identity": "claude", "agent": "claude"}]})
        rows = json.loads(ensemble_tools.call(
            "ensemble_list_tasks", {"detail": True}, self.a, "claude", None)[0])["tasks"]
        self.assertNotIn("usage", rows[0]["allocation"])

    def test_the_tool_says_so(self):
        schema = next(t for t in ensemble_tools.TOOLS if t["name"] == "ensemble_get_task")
        self.assertIn("spec", schema["inputSchema"]["properties"])
        self.assertIn("spec: true", schema["description"])
        self.assertIn("first read", schema["description"])


if __name__ == "__main__":
    unittest.main()
