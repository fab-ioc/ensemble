"""A spare-port hub reads a throwaway profile without repairing its files."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PreflightReadOnly(unittest.TestCase):
    def test_preflight_keeps_fixture_bytes_and_normal_start_repairs(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-preflight-") as td:
            home = Path(td)
            state = home / ".ensemble"
            state.mkdir()
            env = dict(os.environ, HOME=str(home), ENSEMBLE_NO_MOVE_CHECK="1")
            env.pop("ENSEMBLE_PTY_PREFLIGHT", None)
            # Build a real room using the same save format as the application.
            seed = '''import chatroom
r = chatroom.create_room("Finished", [{"identity":"claude", "agent":"claude", "role":"engineer"}])
chatroom.patch_room(r["id"], mode="solo", launched=True, workflow="inprogress")
chatroom.record_report(r["id"], "claude", "completed", "Done")
print(r["id"])
'''
            rid = subprocess.check_output([sys.executable, "-c", seed], cwd=ROOT, env=env,
                                          text=True, timeout=20).strip().splitlines()[-1]
            project_home = home / "EnsembleProjects" / "Fixture"
            code = project_home / "code"
            code.mkdir(parents=True)
            project_json = project_home / "project.json"
            project_json.write_bytes(b'{"id":"proj-fixture","name":"Fixture"}\n')
            fixtures = {
                state / "projects.json": (json.dumps([{"id": "proj-fixture", "name": "Fixture",
                                                      "path": str(code), "home": str(project_home)}])
                                          + "\n").encode(),
                state / "settings.json": b"{}\n",
                state / "points" / f"{rid}.json": b'{"asks":{},"points":[]}\n',
                state / "restart.lease": b'{"id":"test-lease","at":1}\n',
            }
            for path, body in fixtures.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            # A predecessor profile would be migrated by a normal start.
            old = home / ".claude" / "dashboard"
            old.mkdir(parents=True)
            (old / "settings.json").write_bytes(b'{"projectNoun":"fixture"}\n')
            before = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
            project_before = project_json.read_bytes()
            pf_env = dict(env, ENSEMBLE_PTY_PREFLIGHT="1")
            with self.hub(home, pf_env) as port:
                for endpoint in ("/api/sessions?n=5", "/?startup=skip", "/api/projects"):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{endpoint}", timeout=30) as response:
                        self.assertEqual(response.status, 200)
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/projects/new",
                                                 data=b"{}", headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(denied.exception.code, 503)
                denied.exception.close()
            after = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()
                     and not str(p.relative_to(state)).startswith(("pty-preflight/", "logs/"))}
            self.assertEqual(before, after)
            self.assertEqual(project_json.read_bytes(), project_before)
            room_file = state / "rooms" / f"{rid}.json"
            self.assertEqual(json.loads(room_file.read_text())["workflow"], "inprogress")
            with self.hub(home, env):
                end = time.monotonic() + 20
                while time.monotonic() < end:
                    if json.loads(room_file.read_text())["workflow"] == "inreview":
                        break
                    time.sleep(0.1)
                self.assertEqual(json.loads(room_file.read_text())["workflow"], "inreview")

    from contextlib import contextmanager

    @contextmanager
    def hub(self, home: Path, env: dict):
        port = free_port()
        log = home / f"hub-{port}.log"
        with log.open("wb") as output:
            # The normal-start fixture disables only the usage poller, whose
            # external CLI can outlive the test hub and write into HOME.
            argv = ([sys.executable, "dashboard.py"] if env.get("ENSEMBLE_PTY_PREFLIGHT")
                    else [sys.executable, "-c", "import dashboard; "
                          "dashboard.usage.start_scheduler = lambda: None; dashboard.main()"])
            proc = subprocess.Popen([*argv, "--port", str(port)],
                                    cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                end = time.monotonic() + 35
                while time.monotonic() < end:
                    if proc.poll() is not None:
                        self.fail(f"hub exited {proc.returncode}: {log.read_text()[-3000:]}")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                            if json.load(r).get("phase") != "starting":
                                break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.1)
                else:
                    self.fail(f"hub did not boot: {log.read_text()[-3000:]}")
                yield port
            finally:
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/restart/host-stop",
                        data=b'{"lease":"test-lease"}',
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(request, timeout=3).close()
                except OSError:
                    pass
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                # The detached host is ours, but it outlives the web process.
                from backends import ptyhost
                host_dir = home / ".ensemble"
                if env.get("ENSEMBLE_PTY_PREFLIGHT"):
                    host_dir = host_dir / "pty-preflight" / str(port)
                try:
                    expected = ptyhost.descriptor(host_dir)
                    if expected and not ptyhost.stop(host_dir, timeout=2, expected=expected):
                        ptyhost.force_stop(expected)
                except (OSError, ValueError):
                    pass
