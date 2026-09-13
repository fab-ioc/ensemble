"""A plain hub restart for the PO: stop and start on the code on disk.

* restart-hub.ps1 never runs git fetch, reset or pull (only rev-parse, for its
  log), is ASCII (Windows PowerShell 5.1 reads it in the ANSI code page) and
  keeps the preflight, grace, stop/start and resume steps;
* the plan says how this hub was started, whom to resume and what to tell the
  PO; a dry run answers with it; a second request soon after is refused;
* the lock: only the PO of a restart room gets ensemble_restart_hub, anyone
  else is refused, and /api/restart asks the same as /api/update.

The live proof (an unpushed commit survives, the hub answers again, a non-PO
caller gets 403) runs on a spare-port hub; see the task's report.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import ensemble_tools  # noqa: E402

SCRIPT = (ROOT / "restart-hub.ps1").read_bytes()


class HelperScript(unittest.TestCase):
    def test_no_git_that_changes_the_checkout(self):
        text = SCRIPT.decode("ascii")
        gits = re.findall(r"\bgit\b[^\n]*", text)
        self.assertTrue(gits)
        for line in gits:
            self.assertNotRegex(line, r"\b(fetch|reset|pull|checkout|merge|clean|stash)\b", line)
        self.assertRegex(text, r"rev-parse HEAD")

    def test_ascii(self):
        SCRIPT.decode("ascii")

    def test_keeps_the_steps(self):
        text = SCRIPT.decode("ascii")
        order = ["PREFLIGHT FAILED", "preflight passed", "graceSeconds", "Stop-ScheduledTask",
                 "Start-ScheduledTask", "/api/room/resume", "/api/pty/input"]
        at = [text.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        # A test hub (noTask) or a hub not on the task's port never touches the task.
        self.assertIn("if (-not $cfg.noTask)", text)
        self.assertRegex(text, r"--port\\s\+\$port")


class Plan(unittest.TestCase):
    def test_how_the_hub_started(self):
        env = {"ENSEMBLE_RESTART_NO_TASK": "1", "ENSEMBLE_RESTART_GRACE": "7"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(dashboard, "HUB_PORT", 8798), \
             mock.patch.object(sys, "argv", ["dashboard.py", "--port", "8798"]), \
             mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[]):
            plan = dashboard.restart_plan()
        self.assertEqual(plan["args"], ["--port", "8798"])
        self.assertEqual(plan["port"], 8798)
        self.assertEqual(Path(plan["script"]), ROOT / "dashboard.py")
        self.assertEqual(Path(plan["repo"]), ROOT)
        self.assertEqual(plan["python"], sys.executable)
        self.assertTrue(plan["noTask"])
        self.assertEqual(plan["graceSeconds"], 7)
        self.assertNotEqual(plan["preflightPort"], 8798)
        self.assertEqual(plan["env"]["ENSEMBLE_RESTART_GRACE"], "7")
        self.assertEqual((plan["resumeRooms"], plan["wakeRoom"], plan["wakeText"]), ([], "", ""))

    def test_the_page_resumes_live_restart_rooms(self):
        live = [{"alive": True, "meta": {"room": "room-po"}},
                {"alive": False, "meta": {"room": "room-po2"}},
                {"alive": True, "meta": {"room": "room-other"}}]
        with mock.patch.object(dashboard, "HUB_RESTART_ROOMS", frozenset({"room-po", "room-po2"})), \
             mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=live):
            self.assertEqual(dashboard.restart_plan()["resumeRooms"], ["room-po"])

    def test_the_po_is_resumed_and_told(self):
        with mock.patch.object(dashboard.chatroom, "get_room", return_value={"id": "room-po"}), \
             mock.patch.object(dashboard, "load_projects", return_value=[]):
            plan = dashboard.restart_plan("room-po")
        self.assertEqual(plan["resumeRooms"], ["room-po"])
        self.assertEqual(plan["wakeRoom"], "room-po")
        self.assertIn("nothing was fetched, reset or pulled", plan["wakeText"])
        self.assertIn("Do not restart the hub again", plan["wakeText"])
        plan["wakeText"].encode("ascii")        # typed into a terminal by PowerShell

    def test_grace_is_clamped(self):
        for raw, want in (("-5", 0), ("9999", 300), ("x", dashboard.RESTART_GRACE_S)):
            with mock.patch.dict(os.environ, {"ENSEMBLE_RESTART_GRACE": raw}):
                self.assertEqual(dashboard.restart_plan()["graceSeconds"], want, raw)


class Trigger(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(dashboard, "_RESTART_STARTED", 0.0)
        p.start()
        self.addCleanup(p.stop)

    def test_dry_run_answers_with_the_plan(self):
        with mock.patch.dict(os.environ, {"ENSEMBLE_RESTART_DRY_RUN": "1"}), \
             mock.patch.object(dashboard.BACKEND, "self_restart") as real:
            res = dashboard.trigger_restart()
        real.assert_not_called()
        self.assertEqual((res["status"], res["dryRun"]), (202, True))
        self.assertNotIn("env", res["plan"])

    def test_started_then_busy(self):
        env = {k: v for k, v in os.environ.items() if k != "ENSEMBLE_RESTART_DRY_RUN"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(dashboard.BACKEND, "self_restart",
                               return_value={"started": True, "helperPid": 1}) as real:
            first = dashboard.trigger_restart()
            second = dashboard.trigger_restart()
        self.assertEqual(real.call_count, 1)
        self.assertEqual(first["status"], 202)
        self.assertIn("Restart started", first["message"])
        self.assertEqual((second["status"], second["started"]), (409, False))

    def test_a_failed_start_is_not_busy(self):
        env = {k: v for k, v in os.environ.items() if k != "ENSEMBLE_RESTART_DRY_RUN"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(dashboard.BACKEND, "self_restart",
                               return_value={"started": False, "error": "no"}) as real:
            self.assertEqual(dashboard.trigger_restart()["status"], 500)
            self.assertEqual(dashboard.trigger_restart()["status"], 500)
        self.assertEqual(real.call_count, 2)


class Lock(unittest.TestCase):
    ROOM = {"id": "room-po", "participants": []}

    def names(self, allowed: bool) -> set[str]:
        with mock.patch.object(dashboard, "may_restart_hub", return_value=allowed), \
             mock.patch.object(ensemble_tools, "is_admin_caller", return_value=True):
            return {t["name"] for t in ensemble_tools.tool_schemas(self.ROOM, "claude")}

    def test_only_the_restart_po_gets_the_tool(self):
        self.assertIn("ensemble_restart_hub", self.names(True))
        self.assertNotIn("ensemble_restart_hub", self.names(False))

    def test_anyone_else_is_refused(self):
        with mock.patch.object(dashboard, "may_restart_hub", return_value=False), \
             mock.patch.object(ensemble_tools, "_caller",
                               return_value={"room": self.ROOM, "identity": "claude",
                                             "part": {}, "projectId": ""}), \
             mock.patch.object(dashboard, "trigger_restart") as trig:
            text, is_err = ensemble_tools.call("ensemble_restart_hub", {}, "room-po", "claude", None)
        trig.assert_not_called()
        self.assertTrue(is_err)
        self.assertIn(dashboard.RESTART_REFUSED, text)

    def test_the_po_starts_it(self):
        with mock.patch.object(dashboard, "may_restart_hub", return_value=True), \
             mock.patch.object(ensemble_tools, "_caller",
                               return_value={"room": self.ROOM, "identity": "claude",
                                             "part": {}, "projectId": ""}), \
             mock.patch.object(dashboard, "trigger_restart",
                               return_value={"started": True, "status": 202, "message": "m"}) as trig:
            text, is_err = ensemble_tools.call("ensemble_restart_hub", {}, "room-po", "claude", None)
        trig.assert_called_once_with("room-po")
        self.assertFalse(is_err, text)
        self.assertNotIn("status", text)

    def test_the_route_asks_what_update_asks(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p == "/api/restart":')
        block = src[i:src.index("return\n", src.index("trigger_restart(", i))]
        self.assertIn("self._restart_refusal()", block)
        self.assertLess(block.index("_restart_refusal"), block.index("trigger_restart("))

    def test_refusal_without_proof(self):
        class H:
            _restart_refusal = dashboard.Handler._restart_refusal
            _bearer_token = dashboard.Handler._bearer_token
            _cookie = dashboard.Handler._cookie
            _ui_cookie = dashboard.Handler._ui_cookie

            def __init__(self, headers):
                self.headers = headers
                self.server = mock.Mock(server_address=("127.0.0.1", 8798))
        self.assertEqual(H({})._restart_refusal(), dashboard.RESTART_REFUSED)
        with mock.patch.object(dashboard.chatroom, "resolve_token", return_value=("room-x", "claude")), \
             mock.patch.object(dashboard, "may_restart_hub", return_value=False):
            self.assertEqual(H({"Authorization": "Bearer t"})._restart_refusal(),
                             dashboard.RESTART_REFUSED)
        with mock.patch.object(dashboard.chatroom, "resolve_token", return_value=("room-po", "claude")), \
             mock.patch.object(dashboard, "may_restart_hub", return_value=True):
            self.assertEqual(H({"Authorization": "Bearer t"})._restart_refusal(), "")


@unittest.skipUnless(sys.platform == "win32", "Windows backend")
class WindowsBackend(unittest.TestCase):
    def test_missing_helper_is_an_error_not_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("subprocess.run") as run:
            res = dashboard.BACKEND.self_restart({"repo": tmp})
        run.assert_not_called()
        self.assertFalse(res["started"])
        self.assertIn("restart-hub.ps1", res["error"])


if __name__ == "__main__":
    unittest.main()
