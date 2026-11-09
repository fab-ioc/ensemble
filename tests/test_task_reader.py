"""The cheap reader for task owners (#320).

* ``readerForTasks`` is off by default and saved as a bool;
* with it on, a hub task's owner gets the reader: Claude a ``--plugin-dir``
  whose agent runs on Haiku with Read, Grep and Glob only, plus the brief that names
  it; Codex the brief with the headless ``claude -p --model haiku`` line (only
  when ``claude`` is on PATH);
* a reviewer, a PO room, an adopted session, and every launch with the
  setting off, get neither;
* the role reaches the wiring from a launch, and a resume keeps the plugin.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402


def _settings(**over) -> dict:
    return {**dashboard._SETTINGS_DEFAULTS, **over}


class Reader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.settings = _settings(readerForTasks=True)
        self.load = mock.patch.object(dashboard, "load_settings", side_effect=lambda: self.settings)
        for p in (self.load,
                  mock.patch.object(dashboard, "load_projects",
                                    return_value=[{"id": "p1", "poRoomId": "room-po"}]),
                  mock.patch.object(dashboard, "_rtk_task_room", return_value=False),
                  mock.patch.object(dashboard, "READER_PLUGIN_DIR", self.tmp / "reader" / "ensemble-reader"),
                  mock.patch.object(dashboard, "TASK_CLAUDE_SETTINGS", self.tmp / "task.json"),
                  mock.patch.object(dashboard, "USAGE_CLAUDE_SETTINGS", self.tmp / "usage.json"),
                  mock.patch.object(dashboard.shutil, "which", return_value="claude")):
            p.start()
            self.addCleanup(p.stop)
        self.task = {"id": "room-t", "launched": True}

    def test_off_by_default_and_saved_as_a_bool(self):
        self.assertIs(dashboard._SETTINGS_DEFAULTS["readerForTasks"], False)
        self.settings = _settings()
        args, _, brief = dashboard._rtk_task_wiring(self.task, "claude", "engineer")
        self.assertNotIn("--plugin-dir", args)
        self.assertNotIn(dashboard.READER_AGENT, brief)
        self.load.stop()
        with mock.patch.object(dashboard, "SETTINGS_FILE", self.tmp / "settings.json"), \
                mock.patch.object(dashboard, "DASHBOARD_DIR", self.tmp):
            self.assertIs(dashboard.save_settings({"readerForTasks": 1})["readerForTasks"], True)
            self.assertIs(dashboard.save_settings({"readerForTasks": ""})["readerForTasks"], False)
        self.load.start()

    def test_a_claude_owner_gets_the_plugin_and_the_brief(self):
        args, env, brief = dashboard._rtk_task_wiring(self.task, "claude", "engineer")
        self.assertEqual(args[0], "--settings")
        i = args.index("--plugin-dir")
        plugin = Path(args[i + 1])
        self.assertEqual(plugin, dashboard.READER_PLUGIN_DIR)
        manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"] + ":reader", dashboard.READER_AGENT)
        agent = (plugin / "agents" / "reader.md").read_text(encoding="utf-8")
        self.assertIn("\nname: reader\n", agent)
        self.assertIn("\nmodel: haiku\n", agent)
        self.assertIn("\ntools: Read, Grep, Glob\n", agent)
        self.assertNotIn("Bash", agent.split("---")[1])   # no shell: read-only by its tools
        self.assertNotIn("Edit", agent.split("---")[1])
        self.assertEqual(env, {})
        self.assertEqual(brief, dashboard.READER_BRIEF["claude"])
        self.assertIn(f"subagent_type `{dashboard.READER_AGENT}`", brief)
        self.assertIn("Never use it for a review", brief)

    def test_a_codex_owner_gets_the_headless_line(self):
        args, _, brief = dashboard._rtk_task_wiring(self.task, "codex", "engineer")
        self.assertEqual(args, [])
        self.assertTrue(brief.endswith("\n\n" + dashboard.READER_BRIEF["codex"]))
        self.assertIn("claude -p --model haiku --tools Read,Grep,Glob", brief)
        self.assertIn(dashboard.CODEX_CAP_BRIEF.format(tokens=4000), brief)   # the cap's line stays
        with mock.patch.object(dashboard.shutil, "which", return_value=None):
            _, _, brief = dashboard._rtk_task_wiring(self.task, "codex", "engineer")
        self.assertNotIn("claude -p", brief)

    def test_reviewers_pos_and_adopted_sessions_get_nothing(self):
        po = {"id": "room-po", "launched": True}
        adopted = {"id": "room-a"}
        for room, role in ((self.task, "reviewer"), (self.task, "Reviewer 2"),
                           (po, ""), (adopted, "engineer")):
            for agent in ("claude", "codex"):
                args, _, brief = dashboard._rtk_task_wiring(room, agent, role)
                self.assertNotIn("--plugin-dir", args, (room, role, agent))
                self.assertNotIn("cheap reader", brief, (room, role, agent))
        self.assertFalse(dashboard.READER_PLUGIN_DIR.exists())

    def test_the_role_comes_from_the_launch_and_a_resume_keeps_the_plugin(self):
        argvs: list[list[str]] = []
        prompts: list[str] = []
        with mock.patch.object(dashboard, "DASHBOARD_DIR", self.tmp), \
                mock.patch.object(dashboard.agents, "get_agent", return_value=object()), \
                mock.patch.object(dashboard.BACKEND, "headless_launch",
                                  side_effect=lambda cwd, argv, prompt: (argvs.append(argv), prompts.append(prompt))[0] or argv), \
                mock.patch.object(dashboard.ptyrun, "create",
                                  side_effect=lambda *a, **k: types.SimpleNamespace(id="pty-new")):
            handler = dashboard.Handler.__new__(dashboard.Handler)
            handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
            owner = {"identity": "claude", "agent": "claude", "kind": "agent", "sessionId": "",
                     "cwd": str(self.tmp), "role": "engineer"}
            reviewer = {"identity": "rev", "agent": "claude", "kind": "agent", "sessionId": "",
                        "cwd": str(self.tmp), "role": "reviewer"}
            room = {"id": "room-t", "title": "t", "cwd": str(self.tmp), "sharedCwd": True, "launched": True,
                    "tokens": {"tok": "claude", "tok2": "rev"}, "participants": [owner, reviewer]}
            handler._launch_room_agent_pty(room, owner, "do it", collab=True)
            handler._launch_room_agent_pty(room, reviewer, "", collab=True, prompt="review this")
        self.assertIn("--plugin-dir", argvs[0])
        self.assertIn(dashboard.READER_BRIEF["claude"], prompts[0])
        self.assertNotIn("--plugin-dir", argvs[1])
        self.assertNotIn(dashboard.READER_AGENT, prompts[1])
        # Both launch paths and the resume pass the seat's role (a resume
        # recomputes the argv, so the plugin stays; its brief is not resent).
        src = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count('_rtk_task_wiring(room_full, agent_key, part.get("role", ""))'), 3)
        self.assertEqual(src.count("_rtk_task_wiring(room_full, agent_key)"), 0)


if __name__ == "__main__":
    unittest.main()
