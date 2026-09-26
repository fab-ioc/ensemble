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

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
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

    def test_sessions_probe_gets_its_own_longer_timeout(self):
        # A cold /api/sessions walks every held transcript (measured 30-32s live,
        # see the task report) - right on the old 30s cap, which is exactly why a
        # healthy hub failed preflight on its first try. Only that probe gets the
        # longer, overridable limit; / and /api/projects (always fast) keep the
        # plain 30s default so a truly broken probe still fails in bounded time.
        text = SCRIPT.decode("ascii")
        self.assertRegex(text, r"function Ok\(\$u, \[int\]\$timeoutSec = 30\)")
        self.assertRegex(text, r"\$sessTimeoutSec = 90")
        self.assertIn("ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S", text)
        i = text.index("$pfOk = ")
        line = text[i:text.index("\n", i)]
        self.assertIn('Ok "http://127.0.0.1:$pf/api/sessions?n=5" $sessTimeoutSec', line)
        self.assertIn('(Ok "http://127.0.0.1:$pf/") -and (Ok "http://127.0.0.1:$pf/api/projects")', line)


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
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = {k: v for k, v in os.environ.items() if k != "ENSEMBLE_RESTART_DRY_RUN"}
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir),
                  mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[])):
            p.start()
            self.addCleanup(p.stop)
        self.lease = self.dir / "restart.lease"

    def started(self, **kw):
        return mock.patch.object(dashboard.BACKEND, "self_restart",
                                 return_value={"started": True, "helperPid": 1}, **kw)

    def test_dry_run_answers_with_the_plan(self):
        with mock.patch.dict(os.environ, {"ENSEMBLE_RESTART_DRY_RUN": "1"}), \
             mock.patch.object(dashboard.BACKEND, "self_restart") as real:
            res = dashboard.trigger_restart()
            again = dashboard.trigger_restart()
        real.assert_not_called()
        self.assertEqual((res["status"], res["dryRun"]), (202, True))
        self.assertEqual(again["status"], 202)          # a dry run holds no lease
        self.assertNotIn("env", res["plan"])
        self.assertFalse(self.lease.exists())

    def test_started_then_busy(self):
        with self.started() as real:
            first = dashboard.trigger_restart()
            second = dashboard.trigger_restart()
        self.assertEqual(real.call_count, 1)
        self.assertEqual(first["status"], 202)
        self.assertIn("Restart started", first["message"])
        self.assertEqual((second["status"], second["started"]), (409, False))
        plan = real.call_args[0][0]
        self.assertEqual(Path(plan["leasePath"]), self.lease)
        self.assertEqual(json.loads(self.lease.read_text(encoding="utf-8"))["id"], plan["leaseId"])

    def test_requests_at_once_start_one_helper(self):
        gate = threading.Barrier(6)
        results = []

        def slow(plan):
            time.sleep(0.2)
            return {"started": True}

        def call():
            gate.wait()
            results.append(dashboard.trigger_restart()["status"])
        with mock.patch.object(dashboard.BACKEND, "self_restart", side_effect=slow) as real:
            ts = [threading.Thread(target=call) for _ in range(6)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(real.call_count, 1)
        self.assertEqual(sorted(results), [202, 409, 409, 409, 409, 409])

    def test_the_hub_that_comes_back_still_refuses(self):
        # Nothing is kept in memory: the lease file another process wrote refuses.
        self.lease.write_text(json.dumps({"id": "earlier", "at": time.time() - 30, "pid": 1}),
                              encoding="utf-8")
        with self.started() as real:
            res = dashboard.trigger_restart()
        real.assert_not_called()
        self.assertEqual(res["status"], 409)
        self.assertIn("30s ago", res["error"])

    def test_an_expired_lease_is_taken_over(self):
        self.lease.write_text(json.dumps({"id": "old", "at": time.time() - dashboard.RESTART_BUSY_S - 1}),
                              encoding="utf-8")
        with self.started() as real:
            self.assertEqual(dashboard.trigger_restart()["status"], 202)
        self.assertEqual(json.loads(self.lease.read_text(encoding="utf-8"))["id"],
                         real.call_args[0][0]["leaseId"])

    def test_a_lease_being_written_counts_as_held(self):
        self.lease.write_text("", encoding="utf-8")
        with self.started() as real:
            self.assertEqual(dashboard.trigger_restart()["status"], 409)
        real.assert_not_called()

    def test_a_failed_start_is_not_busy(self):
        with mock.patch.object(dashboard.BACKEND, "self_restart",
                               return_value={"started": False, "error": "no"}) as real:
            self.assertEqual(dashboard.trigger_restart()["status"], 500)
            self.assertEqual(dashboard.trigger_restart()["status"], 500)
        self.assertEqual(real.call_count, 2)
        self.assertFalse(self.lease.exists())

    def test_a_crash_drops_the_lease(self):
        with mock.patch.object(dashboard.BACKEND, "self_restart", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                dashboard.trigger_restart()
        self.assertFalse(self.lease.exists())

    def test_only_its_own_lease_is_dropped(self):
        self.lease.write_text(json.dumps({"id": "newer", "at": time.time()}), encoding="utf-8")
        dashboard._drop_restart_lease("older")
        self.assertTrue(self.lease.exists())
        dashboard._drop_restart_lease("newer")
        self.assertFalse(self.lease.exists())


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

    def test_the_helper_renews_only_its_own_lease(self):
        shell = __import__("shutil").which("powershell")
        if not shell:
            self.skipTest("powershell not found")
        text = SCRIPT.decode("ascii").replace("\r\n", "\n")
        i = text.index("function RenewLease {")
        func = text[i:text.index("\n}\n", i) + 3]
        # Every wait up to the hub being back renews it: no plain sleeps left there.
        main = text[text.index("# --- 1. Preflight"):text.index("# --- 4. Bring the rooms back")]
        self.assertNotIn("Start-Sleep -Seconds 2;", main)
        self.assertIn("Nap 2; $pfUp", main)
        self.assertIn("if ($up) { RenewLease }", main)
        with tempfile.TemporaryDirectory() as tmp:
            lease = Path(tmp) / "restart.lease"
            for owner, want_renewed in (("mine", True), ("someone-else", False)):
                lease.write_text(json.dumps({"id": owner, "at": time.time() - 170}), encoding="utf-8")
                ps = (f"$cfg = [pscustomobject]@{{ leasePath = '{lease}'; leaseId = 'mine' }}\n"
                      f"{func}\nRenewLease\n")
                out = subprocess.run([shell, "-NoProfile", "-Command", ps], capture_output=True,
                                     text=True, encoding="utf-8", timeout=60)
                self.assertEqual(out.returncode, 0, out.stderr)
                with mock.patch.object(dashboard, "DASHBOARD_DIR", Path(tmp)):
                    age = dashboard._restart_lease_age(lease)
                self.assertEqual(age < 30, want_renewed, (owner, age))
                self.assertEqual(json.loads(lease.read_text(encoding="utf-8"))["id"], owner)

    def test_a_failed_preflight_drops_only_its_own_lease(self):
        # The real helper, with a Python that does not exist: the preflight fails
        # before anything else, the hub is left alone and the lease is dropped.
        shell = __import__("shutil").which("powershell")
        if not shell:
            self.skipTest("powershell not found")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for lease_id, want_left in (("mine", False), ("someone-else", True)):
                lease = d / "restart.lease"
                lease.write_text(json.dumps({"id": lease_id, "at": time.time()}), encoding="utf-8")
                script = d / "restart-copy.ps1"
                script.write_bytes(SCRIPT)
                plan = d / "plan.json"
                plan.write_text(json.dumps({
                    "repo": str(d), "python": str(d / "no-such-python.exe"), "script": "x.py",
                    "args": [], "port": 1, "hubPid": 0, "preflightPort": 1, "taskName": "none",
                    "noTask": True, "graceSeconds": 0, "resumeRooms": [], "wakeRoom": "",
                    "wakeText": "", "log": str(d / "restart.log"),
                    "preflightLog": str(d / "preflight.log"), "env": {},
                    "leasePath": str(lease), "leaseId": "mine"}), encoding="utf-8")
                out = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                      str(script), "-Plan", str(plan)],
                                     capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertEqual(lease.exists(), want_left, lease_id)
                self.assertFalse(script.exists())
                self.assertFalse(plan.exists())
                self.assertIn("PREFLIGHT FAILED", (d / "restart.log").read_text(encoding="utf-8-sig"))
                lease.unlink(missing_ok=True)

    def test_a_slow_sessions_probe_fails_only_past_its_configured_timeout(self):
        # A stand-in for the real /api/sessions cold scan: instant on every path
        # except /api/sessions, which sleeps a fixed, known time. Proves the
        # sessions timeout is really wired into the preflight gate (not just
        # present as a variable): too small for the sleep -> PREFLIGHT FAILED,
        # exactly the reported bug, and the hub is left alone, same as any other
        # preflight failure. (The pass case is proven live against the real hub
        # and real transcripts - see the task report - rather than here, since a
        # passing preflight makes this script go on to stop/start a "hub".)
        shell = __import__("shutil").which("powershell")
        if not shell:
            self.skipTest("powershell not found")
        fake_server = '''
import os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
port = int(sys.argv[sys.argv.index("--port") + 1])
sleep_s = float(os.environ.get("TEST_SESSIONS_SLEEP_S", "0"))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path == "/api/sessions":
            time.sleep(sleep_s)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")
    def log_message(self, *a):
        pass
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            pf_port = s.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "fake_server.py").write_text(fake_server, encoding="utf-8")
            lease = d / "restart.lease"
            lease.write_text(json.dumps({"id": "mine", "at": time.time()}), encoding="utf-8")
            script = d / "restart-copy.ps1"
            script.write_bytes(SCRIPT)
            plan = d / "plan.json"
            plan.write_text(json.dumps({
                "repo": str(d), "python": sys.executable, "script": str(d / "fake_server.py"),
                "args": [], "port": 1, "hubPid": 0, "preflightPort": pf_port, "taskName": "none",
                "noTask": True, "graceSeconds": 0, "resumeRooms": [], "wakeRoom": "",
                "wakeText": "", "log": str(d / "restart.log"),
                "preflightLog": str(d / "preflight.log"),
                # A 3s /api/sessions is what the old hardcoded 30s cap would have
                # passed easily; a 1s configured limit must still catch it.
                "env": {"ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S": "1", "TEST_SESSIONS_SLEEP_S": "3"},
                "leasePath": str(lease), "leaseId": "mine"}), encoding="utf-8")
            out = subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  str(script), "-Plan", str(plan)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertFalse(script.exists())
            self.assertFalse(plan.exists())
            self.assertFalse(lease.exists())          # its own lease is dropped
            log = (d / "restart.log").read_text(encoding="utf-8-sig")
            self.assertIn("PREFLIGHT FAILED", log)
            self.assertNotIn("preflight passed", log)


if __name__ == "__main__":
    unittest.main()
