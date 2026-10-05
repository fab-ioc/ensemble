"""Task-agent MCP scope: persistence, inheritance and every launch path."""
from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import dashboard

CODEX_ENSEMBLE_ONLY_ARGS = dashboard.Handler._codex_ensemble_only_args


class _Agent:
    display_name = "Agent"

    def ensure_trusted(self, _cwd):
        return None


class SettingsAndProjects(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ens-tools-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.projects = root / "projects"
        self.projects.mkdir()
        self.state = root / "state"
        self.state.mkdir()
        for p in (
            mock.patch.object(dashboard, "PROJECTS_ROOT", self.projects),
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.state),
            mock.patch.object(dashboard, "SETTINGS_FILE", self.state / "settings.json"),
            mock.patch.object(dashboard, "PROJECTS_FILE", self.state / "projects.json"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def assert_project_launch_isolated(self, project_id):
        dashboard.save_settings({"taskAgentTools": "own"})
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
        room = {"id": "room-task", "projectId": project_id}
        tools = handler._task_agent_tools(room)
        with mock.patch.object(
                dashboard.Handler, "_codex_ensemble_only_args",
                return_value=["-c", 'mcp_servers."fake-user".enabled=false',
                              "-c", "features.apps=false"]):
            codex, claude, _ = handler._mcp_wiring(
                "token", True, tools=tools, cwd=str(self.state))
        self.assertIn("--strict-mcp-config", claude)
        self.assertIn('mcp_servers."fake-user".enabled=false', codex)
        self.assertIn("features.apps=false", codex)

    def test_global_default_and_persistence(self):
        self.assertEqual(dashboard.load_settings()["taskAgentTools"], "ensemble")
        saved = dashboard.save_settings({"taskAgentTools": "own"})
        self.assertEqual(saved["taskAgentTools"], "own")
        self.assertEqual(dashboard.load_settings()["taskAgentTools"], "own")
        dashboard.save_settings({"taskAgentTools": "not-a-scope"})
        self.assertEqual(dashboard.load_settings()["taskAgentTools"], "own")
        for bad in ([], {}, 1, None):
            with self.subTest(bad=bad):
                dashboard.save_settings({"taskAgentTools": bad})
                self.assertEqual(dashboard.load_settings()["taskAgentTools"], "own")

    def test_invalid_persisted_global_values_fail_closed(self):
        for bad in ("ensembl", [], {}, 1, None):
            with self.subTest(bad=bad):
                dashboard.SETTINGS_FILE.write_text(
                    json.dumps({"taskAgentTools": bad}), encoding="utf-8")
                self.assertEqual(dashboard.load_settings()["taskAgentTools"], "ensemble")

    def test_project_override_round_trip_and_inherit(self):
        ok, project, _ = dashboard.register_project("Tools")
        self.assertTrue(ok)
        pid = project["id"]
        self.assertEqual(dashboard.set_project_task_agent_tools(pid, "own"), (True, "ok"))
        got = dashboard.find_project(pid)
        self.assertEqual(got["taskAgentTools"], "own")
        meta = json.loads((Path(got["home"]) / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["taskAgentTools"], "own")
        self.assertEqual(dashboard.set_project_task_agent_tools(pid, None), (True, "ok"))
        self.assertNotIn("taskAgentTools", dashboard.find_project(pid))
        self.assertNotIn("taskAgentTools", json.loads(
            (Path(got["home"]) / "project.json").read_text(encoding="utf-8")))
        for bad in ("wide", [], {}, 1):
            with self.subTest(persisted=bad):
                path = Path(got["home"]) / "project.json"
                meta = json.loads(path.read_text(encoding="utf-8"))
                meta["taskAgentTools"] = bad
                path.write_text(json.dumps(meta), encoding="utf-8")
                self.assertEqual(dashboard.find_project(pid)["taskAgentTools"], "ensemble")
                self.assert_project_launch_isolated(pid)
        self.assertEqual(dashboard.set_project_task_agent_tools(pid, "wide"),
                         (False, "task_agent_tools_must_be_ensemble_or_own"))
        for bad in ([], {}, 1):
            with self.subTest(bad=bad):
                self.assertEqual(dashboard.set_project_task_agent_tools(pid, bad),
                                 (False, "task_agent_tools_must_be_ensemble_or_own"))

    def test_external_project_invalid_override_fails_closed_but_absent_inherits(self):
        code = self.state / "external-code"
        code.mkdir()
        base = {"id": "external", "name": "External", "path": str(code)}
        dashboard.PROJECTS_FILE.write_text(json.dumps([base]), encoding="utf-8")
        self.assertNotIn("taskAgentTools", dashboard.find_project("external"))
        for bad in ("wide", ["own"], {}):
            with self.subTest(persisted=bad):
                dashboard.PROJECTS_FILE.write_text(
                    json.dumps([{**base, "taskAgentTools": bad}]), encoding="utf-8")
                self.assertEqual(
                    dashboard.find_project("external")["taskAgentTools"], "ensemble")
                self.assert_project_launch_isolated("external")


class LaunchArguments(unittest.TestCase):
    """Owners, reviewers, resumes and handovers use the resolved task scope."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ens-tools-launch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = [{"id": "p1", "poRoomId": "room-po"}]
        self.settings = {"taskAgentTools": "ensemble", "agentModels": dashboard.agent_models.normalise(None),
                         "rtkForTasks": False, "codexToolOutputTokens": 0,
                         "readCapBytes": 0, "readCapLines": 1}
        self.made: list[list[str]] = []

        def create(cmd, **_kw):
            self.made.append(list(cmd))
            return types.SimpleNamespace(id="pty-new")

        for p in (
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.root),
            mock.patch.object(dashboard, "load_projects", side_effect=lambda: [dict(x) for x in self.projects]),
            mock.patch.object(dashboard, "load_session_projects", return_value={}),
            mock.patch.object(dashboard, "load_settings", side_effect=lambda: dict(self.settings)),
            mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
            mock.patch.object(dashboard.agents, "get_agent", return_value=_Agent()),
            mock.patch.object(dashboard.BACKEND, "headless_launch",
                              side_effect=lambda cwd, argv, prompt: argv),
            mock.patch.object(dashboard.ptyrun, "create", side_effect=create),
            mock.patch.object(dashboard.Handler, "_codex_ensemble_only_args",
                              return_value=["-c", 'mcp_servers."fake-user".enabled=false',
                                            "-c", "features.apps=false"]),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))

    def room(self, kind: str, rid="room-task", project="p1", role="engineer", mode="collab"):
        part = {"identity": "agent", "agent": kind, "kind": "agent", "sessionId": "",
                "model": "", "cwd": str(self.root), "role": role}
        room = {"id": rid, "title": "task", "cwd": str(self.root), "sharedCwd": True,
                "projectId": project, "mode": mode, "tokens": {"tok": "agent"},
                "participants": [part]}
        return room, part

    def launch(self, kind: str, path: str):
        if path in ("po", "po_switch"):
            room, part = self.room(kind, rid="room-po", role="ProductOwner", mode="solo")
        elif path == "unassigned":
            room, part = self.room(kind, project="")
        elif path == "solo":
            room, part = self.room(kind, mode="solo")
        elif path == "reviewer":
            room, part = self.room(kind, role="reviewer")
        else:
            room, part = self.room(kind)
        if path in ("resume", "human"):
            part["sessionId"] = "session-old"
            self.handler._resume_room_agent_pty(
                room, part, collab=path != "human", human=path == "human")
        else:
            prompt = {"reviewer": "review", "handover": "task handover",
                      "po_switch": "PO handover"}.get(path)
            self.handler._launch_room_agent_pty(
                room, part, "task", collab=room["mode"] != "solo", prompt=prompt)
        return self.made.pop()

    @staticmethod
    def isolated(kind: str, argv: list[str]) -> bool:
        return ("--strict-mcp-config" in argv if kind == "claude"
                else 'mcp_servers."fake-user".enabled=false' in argv)

    def test_every_task_path_by_agent_kind_and_global_setting(self):
        paths = ("owner", "reviewer", "resume", "handover", "solo", "unassigned")
        for setting, expected in (("ensemble", True), ("own", False)):
            self.settings["taskAgentTools"] = setting
            for kind in ("claude", "codex"):
                for path in paths:
                    with self.subTest(setting=setting, kind=kind, path=path):
                        argv = self.launch(kind, path)
                        self.assertEqual(self.isolated(kind, argv), expected)
                        if kind == "codex":
                            self.assertEqual("features.apps=false" in argv, expected)
                            self.assertEqual("mcp_servers.ensemble.enabled=true" in argv, expected)

    def test_project_override_wins_and_unassigned_inherits_global(self):
        self.settings["taskAgentTools"] = "ensemble"
        self.projects[0]["taskAgentTools"] = "own"
        for kind in ("claude", "codex"):
            self.assertFalse(self.isolated(kind, self.launch(kind, "owner")))
            self.assertTrue(self.isolated(kind, self.launch(kind, "unassigned")))
        self.settings["taskAgentTools"] = "own"
        self.projects[0]["taskAgentTools"] = "ensemble"
        for kind in ("claude", "codex"):
            self.assertTrue(self.isolated(kind, self.launch(kind, "owner")))
            self.assertFalse(self.isolated(kind, self.launch(kind, "unassigned")))

    def test_invalid_resolved_global_value_fails_closed(self):
        self.settings["taskAgentTools"] = ["own"]
        for kind in ("claude", "codex"):
            self.assertTrue(self.isolated(kind, self.launch(kind, "unassigned")))

    def test_malformed_project_object_fails_closed_with_global_own(self):
        self.settings["taskAgentTools"] = "own"
        self.projects[0]["taskAgentTools"] = ["own"]
        for kind in ("claude", "codex"):
            self.assertTrue(self.isolated(kind, self.launch(kind, "owner")))

    def test_pos_switches_and_human_driven_history_keep_own_tools(self):
        self.settings["taskAgentTools"] = "ensemble"
        self.projects[0]["taskAgentTools"] = "ensemble"
        for kind in ("claude", "codex"):
            for path in ("po", "po_switch", "human"):
                with self.subTest(kind=kind, path=path):
                    self.assertFalse(self.isolated(kind, self.launch(kind, path)))

    def test_codex_ensemble_only_disables_effective_servers_by_launch_flag(self):
        proc = types.SimpleNamespace(returncode=0, stdout=json.dumps([
            {"name": "fake-user", "enabled": True},
            {"name": "with.dot", "enabled": True},
            {"name": "ensemble", "enabled": True},
        ]), stderr="")
        with mock.patch.object(dashboard.subprocess, "run", return_value=proc) as run:
            args = CODEX_ENSEMBLE_ONLY_ARGS(str(self.root))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][:2], ["codex", "-C"])
        self.assertIn('mcp_servers."fake-user".enabled=false', args)
        self.assertIn('mcp_servers."with.dot".enabled=false', args)
        self.assertNotIn('mcp_servers."ensemble".enabled=false', args)
        self.assertIn("features.apps=false", args)
        self.assertIn("features.remote_plugin=false", args)

    def test_codex_ensemble_only_fails_closed_when_discovery_is_unusable(self):
        cases = (
            OSError("missing"),
            types.SimpleNamespace(returncode=1, stdout="", stderr="no"),
            types.SimpleNamespace(returncode=0, stdout="not json", stderr=""),
            types.SimpleNamespace(returncode=0, stdout="{}", stderr=""),
            types.SimpleNamespace(returncode=0, stdout="[{}]", stderr=""),
        )
        for result in cases:
            with self.subTest(result=result):
                kwargs = ({"side_effect": result} if isinstance(result, BaseException)
                          else {"return_value": result})
                with mock.patch.object(dashboard.subprocess, "run", **kwargs):
                    with self.assertRaises(dashboard.StartRoomError):
                        CODEX_ENSEMBLE_ONLY_ARGS(str(self.root))


class PageCopy(unittest.TestCase):
    def test_global_and_project_choices_are_visible_and_say_when_they_apply(self):
        page = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("Task agents' tools", page)
        self.assertGreaterEqual(page.count("Ensemble + my own tools"), 2)
        self.assertIn("Use global default (currently ${global})", page)
        self.assertIn("Running agents keep the tools they started with", page)
        self.assertIn("new agent launches use the choice", page)
        self.assertIn('nounText("The {project}\'s PO keeps your own tools.")', page)
        self.assertIn('<div class="pref-row" style="margin-top: 16px;">\n'
                      '      <label class="pref-label" for="pref-task-agent-tools">', page)


if __name__ == "__main__":
    unittest.main()
