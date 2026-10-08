"""The built app's start (app_launch): what holds the port, what the person is
told, stopping an older hub, and a hub that stops while starting."""
import errno
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_launch  # noqa: E402
import app_version  # noqa: E402

OLD_HUB = ROOT / "tests" / "fixtures" / "old_hub" / "dashboard.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_listening(port: int, timeout: float = 10) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if app_launch._listening(port):
            return
        time.sleep(0.05)
    raise AssertionError(f"nothing listens on {port}")


class _Server:
    """An HTTP server on a free port, answering ``routes`` {path: (status, body)}."""

    def __init__(self, routes: dict):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, b"<html>404</html>"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def ensemble_server(version: str, pid: int = 4242) -> _Server:
    body = json.dumps({"version": version, "packaged": True, "pid": pid,
                       "executable": "/Applications/Ensemble.app/Contents/MacOS/Ensemble"}).encode()
    return _Server({"/api/version": (200, body)})


class PortStatusTest(unittest.TestCase):
    def setUp(self):
        self.servers = []
        p = mock.patch.object(app_launch, "_holder", lambda port: None)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        for s in self.servers:
            s.close()

    def serve(self, server):
        self.servers.append(server)
        return server.port

    def test_free(self):
        self.assertEqual(app_launch.port_status(free_port())["state"], "free")

    def test_an_ensemble_hub(self):
        port = self.serve(ensemble_server("0.9.0", pid=77))
        st = app_launch.port_status(port)
        self.assertEqual((st["state"], st["version"], st["pid"]), ("ensemble", "0.9.0", 77))

    def test_an_older_hub_without_api_version_by_its_page(self):
        port = self.serve(_Server({"/": (200, b"<html><head><title>Ensemble</title></head></html>")}))
        self.assertEqual(app_launch.port_status(port)["state"], "old")

    def test_an_older_hub_by_its_command_line(self):
        port = self.serve(_Server({}))
        holder = {"pid": 924, "command": f"/usr/bin/python3 {OLD_HUB} --port {port}", "cwd": "/"}
        with mock.patch.object(app_launch, "_holder", lambda p: holder):
            st = app_launch.port_status(port)
        self.assertEqual((st["state"], st["pid"]), ("old", 924))
        self.assertIn("older Ensemble hub", app_launch.describe_status(st, "0.9.2"))
        self.assertIn("process 924", app_launch.describe_status(st, "0.9.2"))

    def test_a_web_server_serving_a_checkout_is_not_an_older_hub(self):
        # python3 -m http.server in a checkout serves its index.html, titled Ensemble.
        port = self.serve(_Server({"/": (200, b"<html><head><title>Ensemble</title></head></html>")}))
        holder = {"pid": 31, "command": f"/usr/bin/python3 -m http.server {port}", "cwd": str(ROOT)}
        with mock.patch.object(app_launch, "_holder", lambda p: holder):
            self.assertEqual(app_launch.port_status(port)["state"], "other")

    def test_another_web_server(self):
        port = self.serve(_Server({"/": (200, b"<html><title>Something else</title></html>")}))
        st = app_launch.port_status(port)
        self.assertEqual(st["state"], "other")
        self.assertIn("another program", app_launch.describe_status(st, "0.9.2"))

    def test_something_that_does_not_speak_http(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(5)
        self.addCleanup(s.close)
        self.assertEqual(app_launch.port_status(s.getsockname()[1])["state"], "other")


class EnsembleCommandTest(unittest.TestCase):
    def test_the_built_app(self):
        self.assertTrue(app_launch.is_ensemble_command(
            "/Users/x/Applications/Ensemble.app/Contents/MacOS/Ensemble --port 8765 --background"))
        self.assertTrue(app_launch.is_ensemble_command(r"C:\Program Files\Ensemble\Ensemble.exe --port 8765"))

    def test_a_checkout(self):
        self.assertTrue(app_launch.is_ensemble_command(f"/usr/bin/python3 {OLD_HUB} --port 8765"))
        # Relative to the process's working directory.
        self.assertTrue(app_launch.is_ensemble_command("python3 dashboard.py --port 8765", str(OLD_HUB.parent)))
        self.assertTrue(app_launch.is_ensemble_command(f"python {ROOT / 'dashboard.py'}"))

    def test_any_other_dashboard_py_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "dashboard.py").write_text("", encoding="utf-8")
            self.assertFalse(app_launch.is_ensemble_command(f"python3 {d}/dashboard.py --port 8765"))
        self.assertFalse(app_launch.is_ensemble_command("/usr/sbin/httpd -D FOREGROUND"))
        self.assertFalse(app_launch.is_ensemble_command(""))


class StopOldHubTest(unittest.TestCase):
    def start_old_hub(self, *extra):
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(OLD_HUB), "--port", str(port), *extra],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        wait_listening(port)
        holder = {"pid": proc.pid, "command": f"{sys.executable} {OLD_HUB} --port {port}", "cwd": ""}
        return port, proc, holder

    def test_stops_an_older_hub(self):
        port, proc, holder = self.start_old_hub("--no-title")
        with tempfile.TemporaryDirectory() as home, \
                mock.patch.object(app_launch, "_holder", lambda p: holder if app_launch._listening(p) else None), \
                mock.patch.object(app_launch, "stop_launch_agents", return_value=[]):
            res = app_launch.stop_old_hub(port, "0.9.2", Path(home))
        self.assertTrue(res["ok"], res)
        proc.wait(10)
        self.assertFalse(app_launch._listening(port))
        self.assertEqual(app_launch.port_status(port)["state"], "free")

    def test_never_stops_another_program(self):
        port, proc, holder = self.start_old_hub("--no-title")
        holder["command"] = f"{sys.executable} -m http.server {port}"
        with tempfile.TemporaryDirectory() as home, mock.patch.object(app_launch, "_holder", lambda p: holder):
            res = app_launch.stop_old_hub(port, "0.9.2", Path(home))
        self.assertFalse(res["ok"])
        self.assertIn("not Ensemble", res["error"])
        self.assertIsNone(proc.poll())

    def test_an_older_hub_whose_process_is_unknown_is_not_stopped(self):
        port, proc, _ = self.start_old_hub()
        with tempfile.TemporaryDirectory() as home, mock.patch.object(app_launch, "_holder", lambda p: None):
            res = app_launch.stop_old_hub(port, "0.9.2", Path(home))
        self.assertFalse(res["ok"])
        self.assertIsNone(proc.poll())

    def test_this_version_or_a_newer_one_is_not_stopped(self):
        for v in ("0.9.2", "0.10.0"):
            s = ensemble_server(v, pid=999999)
            self.addCleanup(s.close)
            with tempfile.TemporaryDirectory() as home:
                res = app_launch.stop_old_hub(s.port, "0.9.2", Path(home))
            self.assertFalse(res["ok"], v)
            self.assertIn("not older", res["error"])

    def test_the_launch_agent_running_it_is_booted_out_and_moved_aside(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            agents = home / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            import plistlib
            for label in ("com.ensemble.dashboard", "com.example.other"):
                (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label}))
            calls = []
            with mock.patch.object(app_launch, "_agent_pid",
                                   lambda label: 924 if label == "com.ensemble.dashboard" else 5), \
                    mock.patch.object(app_launch, "_out", lambda cmd, timeout=5: calls.append(cmd) or ""):
                did = app_launch.stop_launch_agents(924, home)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][:2], ["launchctl", "bootout"])
            self.assertTrue(calls[0][2].endswith("/com.ensemble.dashboard"))
            self.assertFalse((agents / "com.ensemble.dashboard.plist").exists())
            self.assertTrue((home / ".ensemble" / "old-launch-agents" / "com.ensemble.dashboard.plist").exists())
            self.assertTrue((agents / "com.example.other.plist").exists())
            self.assertTrue(any("moved" in line for line in did))


class MainTest(unittest.TestCase):
    """app_launch.main: the port is checked before the hub changes anything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.calls = {"dialog": [], "notify": [], "browser": [], "started": 0, "stop": 0}
        self.dash = types.ModuleType("dashboard")
        self.dash.main = self._hub
        self.hub_error = None
        self.dialog_answer = None
        self.stop_result = {"ok": True, "did": ["stopped"]}
        patches = [
            mock.patch.object(Path, "home", lambda: self.home),
            mock.patch.object(app_launch, "dialog", self._dialog),
            mock.patch.object(app_launch, "notify", lambda m: self.calls["notify"].append(m)),
            mock.patch.object(app_launch, "open_browser", lambda u: self.calls["browser"].append(u) or True),
            mock.patch.object(app_launch, "_open_when_up", lambda port: None),
            mock.patch.object(app_launch, "stop_old_hub", self._stop),
            mock.patch.dict(sys.modules, {"dashboard": self.dash}),
            mock.patch.object(sys, "argv", ["Ensemble"]),
            mock.patch.object(app_version, "packaged", lambda: True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _hub(self):
        self.calls["started"] += 1
        if self.hub_error:
            raise self.hub_error

    def _dialog(self, message, buttons=("OK",), default=None):
        self.calls["dialog"].append((message, buttons))
        return self.dialog_answer

    def _stop(self, port, version, home=None):
        self.calls["stop"] += 1
        return self.stop_result

    def run_main(self, status, args=()):
        with mock.patch.object(app_launch, "port_status", lambda port: dict(status, port=port)):
            return app_launch.main(list(args))

    def test_a_normal_start(self):
        with mock.patch.object(app_launch, "_open_when_up") as up:
            rc = self.run_main({"state": "free"}, ["--port", "8799"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls["started"], 1)
        up.assert_called_once_with(8799)
        self.assertEqual(self.calls["dialog"], [])
        self.assertEqual(sys.argv[-2:], ["--log", str(app_launch.log_path(self.home))])

    def test_this_version_already_serving_opens_the_browser(self):
        rc = self.run_main({"state": "ensemble", "version": app_version.VERSION, "pid": 1})
        self.assertEqual((rc, self.calls["started"]), (0, 0))
        self.assertEqual(self.calls["browser"], ["http://127.0.0.1:8765/"])

    def test_a_newer_ensemble_serving_is_opened(self):
        self.run_main({"state": "ensemble", "version": "99.0.0", "pid": 1})
        self.assertEqual(len(self.calls["browser"]), 1)
        self.assertEqual(self.calls["dialog"], [])

    def test_another_program_on_the_port(self):
        rc = self.run_main({"state": "other", "pid": 50, "command": "/usr/sbin/httpd"})
        self.assertEqual((rc, self.calls["started"]), (1, 0))
        (msg, buttons), = self.calls["dialog"]
        self.assertIn("another program (process 50: /usr/sbin/httpd)", msg)
        self.assertEqual(buttons, ("OK",))
        self.assertEqual(self.calls["stop"], 0)
        self.assertIn("'state': 'other'", app_launch.log_path(self.home).read_text(encoding="utf-8"))

    def test_an_older_hub_quit(self):
        self.dialog_answer = "Quit"
        rc = self.run_main({"state": "old", "pid": 924, "command": "python3 dashboard.py"})
        self.assertEqual((rc, self.calls["started"], self.calls["stop"]), (1, 0, 0))
        self.assertIn("older Ensemble hub", self.calls["dialog"][0][0])

    def test_an_older_hub_stopped_then_this_one_starts(self):
        self.dialog_answer = "Stop it and start Ensemble"
        rc = self.run_main({"state": "old", "pid": 924, "command": "python3 dashboard.py"})
        self.assertEqual((rc, self.calls["started"], self.calls["stop"]), (0, 1, 1))

    def test_an_older_hub_that_does_not_stop(self):
        self.dialog_answer = "Stop it and start Ensemble"
        self.stop_result = {"ok": False, "did": [], "error": "it did not stop"}
        rc = self.run_main({"state": "old", "pid": 924, "command": "python3 dashboard.py"})
        self.assertEqual((rc, self.calls["started"]), (1, 0))
        self.assertIn("it did not stop", self.calls["dialog"][-1][0])

    def test_an_older_built_app_serving(self):
        self.dialog_answer = "Quit"
        self.run_main({"state": "ensemble", "version": "0.1.0", "pid": 3, "executable": "/x/Ensemble"})
        self.assertIn("Ensemble 0.1.0 is already running", self.calls["dialog"][0][0])
        self.assertEqual(self.calls["browser"], [])

    def test_at_sign_in_a_held_port_is_a_notification_and_exit_0(self):
        rc = self.run_main({"state": "other", "pid": 50, "command": "x"}, ["--background"])
        self.assertEqual((rc, self.calls["started"], self.calls["dialog"]), (0, 0, []))
        self.assertEqual(len(self.calls["notify"]), 1)

    def test_a_crash_at_start_is_shown(self):
        self.hub_error = RuntimeError("no module named foo")
        with mock.patch("traceback.print_exc"):
            rc = self.run_main({"state": "free"})
        self.assertEqual(rc, 1)
        msg = self.calls["dialog"][0][0]
        self.assertIn("stopped while starting: RuntimeError: no module named foo", msg)
        self.assertIn(str(app_launch.log_path(self.home)), msg)

    def test_the_port_taken_while_starting_is_shown(self):
        self.hub_error = OSError(errno.EADDRINUSE, "Address already in use")
        with mock.patch("traceback.print_exc"):
            rc = self.run_main({"state": "free"})
        self.assertEqual(rc, 1)
        self.assertIn("in use", self.calls["dialog"][0][0])

    def test_port_status_and_stop_old_hub_commands(self):
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            self.assertEqual(self.run_main({"state": "old", "pid": 924, "command": "py dashboard.py"},
                                           ["--port-status"]), 0)
        self.assertIn("state=old", out)
        self.assertIn("pid=924", out)
        self.assertTrue(any(l.startswith("message=Port 8765 is in use by an older Ensemble hub") for l in out))
        self.assertEqual(self.calls["started"], 0)
        with mock.patch("builtins.print"):
            self.assertEqual(self.run_main({"state": "free"}, ["--stop-old-hub"]), 0)
            self.stop_result = {"ok": False, "did": [], "error": "no"}
            self.assertEqual(self.run_main({"state": "free"}, ["--stop-old-hub"]), 1)


class OpenWhenUpTest(unittest.TestCase):
    def test_opens_the_browser_once_the_hub_answers(self):
        s = ensemble_server(app_version.VERSION)
        self.addCleanup(s.close)
        opened = []
        with mock.patch.object(app_launch, "open_browser", lambda u: opened.append(u) or True):
            app_launch._open_when_up(s.port, seconds=10).join(10)
        self.assertEqual(opened, [f"http://127.0.0.1:{s.port}/"])

    def test_says_so_when_the_hub_never_answers(self):
        told = []
        with mock.patch.object(app_launch, "notify", told.append), \
                mock.patch.object(app_launch, "open_browser") as ob:
            app_launch._open_when_up(free_port(), seconds=1).join(5)
        ob.assert_not_called()
        self.assertEqual(len(told), 1)
        self.assertIn("does not answer", told[0])

    def test_open_browser_logs_it(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(app_launch, "_LOG", Path(d) / "e.log"), \
                mock.patch.object(app_launch.sys, "platform", "linux"), \
                mock.patch("webbrowser.open", return_value=True):
            self.assertTrue(app_launch.open_browser("http://127.0.0.1:1/"))
            self.assertIn("opened the dashboard in the browser: http://127.0.0.1:1/",
                          (Path(d) / "e.log").read_text(encoding="utf-8"))

    def test_no_dialog_in_tests_only_logs(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(app_launch, "_LOG", Path(d) / "e.log"), \
                mock.patch.dict(os.environ, {"ENSEMBLE_NO_DIALOG": "1"}), \
                mock.patch("subprocess.run") as run:
            self.assertIsNone(app_launch.dialog("Port 1 is in use"))
            run.assert_not_called()
            self.assertIn("tells the person: Port 1 is in use", (Path(d) / "e.log").read_text(encoding="utf-8"))

    def test_applescript_text_is_quoted(self):
        self.assertEqual(app_launch._as_text('a "b" \\c'), '"a \\"b\\" \\\\c"')


class InstallerTest(unittest.TestCase):
    """packaging/install-mac.sh: checks the port before opening the app, waits
    for its hub, and says why when it does not answer."""

    def setUp(self):
        self.sh = (ROOT / "packaging" / "install-mac.sh").read_text(encoding="utf-8")

    def test_checks_the_port_before_opening(self):
        tail = self.sh[self.sh.index('if [ "${ENSEMBLE_NO_OPEN:-}" != 1 ]; then'):]
        self.assertLess(tail.index("check_port"), tail.index('open "$TARGET"'))
        self.assertIn("--port-status", self.sh)
        self.assertIn("--stop-old-hub", self.sh)

    def test_an_older_hub_is_stopped_only_when_asked(self):
        body = self.sh[self.sh.index("check_port() {"):]
        self.assertIn('ENSEMBLE_REPLACE_OLD_HUB:-}" = 1', body)
        self.assertIn("</dev/tty", body)
        self.assertIn("other) not_started", body)

    def test_claims_it_runs_only_after_the_hub_answers(self):
        tail = self.sh[self.sh.index('if [ "${ENSEMBLE_NO_OPEN:-}" != 1 ]; then'):]
        self.assertLess(tail.index("wait_hub || not_answering"), tail.index("is running"))
        self.assertNotIn("opens in your browser in a few seconds", self.sh)
        body = self.sh[self.sh.index("not_answering() {"):]
        self.assertIn('tail -n 20 "$LOG"', body)


if __name__ == "__main__":
    unittest.main()
