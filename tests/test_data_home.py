"""ENSEMBLE_HOME: where a hub keeps its own data (P9).

A hub started with ENSEMBLE_HOME set keeps ``.ensemble``, the projects root
and ``cs`` under that folder and writes nothing into the home's
``~/.ensemble`` or ``~/EnsembleProjects``, while HOME (and so the agents'
login) stays the real one. Unset, nothing changes. The agents it starts get
the variable emptied, so a hub or test an agent runs does not take this
hub's data for its own. A real hub is started on a spare port with a
stand-in home; it also shows the outside sources frame runs from the hub's
start-up."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LIVE_PORT = 8770


def free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != LIVE_PORT:
            return port


def clean_env(**extra) -> dict:
    """This process's environment without anything that points at another
    hub (a session started by a hub carries its addresses)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ENSEMBLE", "CLAUDE", "CODEX"))}
    env.update(extra)
    return env


def paths(env: dict) -> dict:
    out = subprocess.check_output(
        [sys.executable, "-c", "import json, usage; from backends import base; print(json.dumps("
         "{k: str(getattr(base, k)) for k in ('HOME', 'DATA_HOME', 'DASHBOARD_DIR', "
         "'PROJECTS_ROOT', 'CS_ROOT')} | {'own': base.OWN_DATA_HOME, "
         "'statusLine': str(usage.CLAUDE_STATUSLINE_FILE)}))"],
        cwd=ROOT, env=env, text=True, timeout=30)
    return json.loads(out)


class DataHomeSetting(unittest.TestCase):
    def test_unset_or_empty_changes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-home-") as td:
            home = Path(td).resolve()
            for env in (clean_env(HOME=str(home)), clean_env(HOME=str(home), ENSEMBLE_HOME=""),
                        clean_env(HOME=str(home), ENSEMBLE_HOME=str(home))):
                p = paths(env)
                self.assertEqual(p["DASHBOARD_DIR"], str(home / ".ensemble"))
                self.assertEqual(p["PROJECTS_ROOT"], str(home / "EnsembleProjects"))
                self.assertEqual(p["CS_ROOT"], str(home / "cs"))
                self.assertEqual(p["statusLine"],
                                 str(home / ".ensemble" / "usage" / "claude-rate-limits.json"))
                self.assertFalse(p["own"])

    def test_set_moves_the_hubs_data_and_keeps_home(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-home-") as td:
            home, data = Path(td).resolve() / "home", Path(td).resolve() / "data"
            home.mkdir()
            p = paths(clean_env(HOME=str(home), ENSEMBLE_HOME=str(data)))
            self.assertEqual(p["HOME"], str(home), "the agents keep the real home")
            self.assertEqual(p["DASHBOARD_DIR"], str(data / ".ensemble"))
            self.assertEqual(p["PROJECTS_ROOT"], str(data / "EnsembleProjects"))
            self.assertEqual(p["CS_ROOT"], str(data / "cs"))
            self.assertEqual(p["statusLine"], str(data / ".ensemble" / "usage" /
                                                  "claude-rate-limits.json"),
                             "agents are handed the path, so they write it here too")
            self.assertTrue(p["own"])

    def test_agents_get_the_variable_emptied(self):
        import dashboard
        with mock.patch.dict(os.environ, {"ENSEMBLE_HOME": "/somewhere/else"}):
            self.assertEqual(dashboard._no_data_home_env(), {"ENSEMBLE_HOME": ""})
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENSEMBLE_HOME", None)
            self.assertEqual(dashboard._no_data_home_env(), {})


class HubWithItsOwnData(unittest.TestCase):
    def test_a_hub_with_ensemble_home_writes_nothing_into_the_home(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-datahome-") as td:
            home, data = Path(td).resolve() / "home", Path(td).resolve() / "data"
            home.mkdir()
            # A configured source of a type this hub does not have, turned on:
            # the frame's first tick says so in its state file.
            outside_dir = data / ".ensemble" / "outside"
            outside_dir.mkdir(parents=True)
            (outside_dir / "sources.json").write_text(json.dumps(
                {"sources": {"probe": {"type": "nothing-here", "mode": "on"}}}))
            env = clean_env(HOME=str(home), ENSEMBLE_HOME=str(data), ENSEMBLE_NO_MOVE_CHECK="1",
                            ENSEMBLE_NO_AUTO_INSTALL="1", ENSEMBLE_NO_OPEN="1")
            port = free_port()
            log = Path(td) / "hub.log"
            with log.open("wb") as out:
                proc = subprocess.Popen(
                    [sys.executable, "-c", "import dashboard; "
                     "dashboard.usage.start_scheduler = lambda: None; dashboard.main()",
                     "--port", str(port)],
                    cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
                try:
                    self.wait_up(proc, port, log)
                    cfg = self.get(port, "/api/config")
                    self.assertEqual(cfg["stateDir"], str(data / ".ensemble"))
                    self.assertEqual(cfg["csRoot"], str(data / "cs"))
                    self.assertTrue(cfg["logFile"].startswith(str(data)), cfg["logFile"])
                    made = self.post(port, "/api/projects/new", {"name": "Made Up"})
                    self.assertTrue(made.get("ok"), made)
                    self.assertTrue((data / "EnsembleProjects" / "Made-Up").is_dir(), made)
                    state = outside_dir / "source-probe.json"
                    end = time.monotonic() + 30
                    while time.monotonic() < end and not state.exists():
                        time.sleep(0.2)
                    self.assertTrue(state.exists(), "the frame runs from the hub's start-up")
                    self.assertIn("no reader for the type nothing-here",
                                  json.loads(state.read_text())["notSetUp"])
                finally:
                    self.stop(proc, port, data)
            self.assertFalse((home / ".ensemble").exists(), log.read_text()[-2000:])
            self.assertFalse((home / "EnsembleProjects").exists())
            self.assertFalse((home / "cs").exists())
            self.assertFalse((home / ".claude" / "skills").exists(),
                             "the person's hub keeps the agents' skills")
            self.assertFalse((home / "Library" / "Logs" / "ensemble.log").exists())
            self.assertIn("agent skills not installed", log.read_text())

    def wait_up(self, proc, port: int, log: Path) -> None:
        end = time.monotonic() + 40
        while time.monotonic() < end:
            if proc.poll() is not None:
                self.fail(f"hub exited {proc.returncode}: {log.read_text()[-3000:]}")
            try:
                if self.get(port, "/api/health").get("phase") != "starting":
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.fail(f"hub did not start: {log.read_text()[-3000:]}")

    @staticmethod
    def get(port: int, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return json.load(r)

    @staticmethod
    def post(port: int, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    @staticmethod
    def stop(proc, port: int, data: Path) -> None:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/restart/host-stop",
                                         data=b"{}", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        # The terminals' host outlives the web process: stop it too.
        from backends import ptyhost
        try:
            expected = ptyhost.descriptor(data / ".ensemble")
            if expected and not ptyhost.stop(data / ".ensemble", timeout=2, expected=expected):
                ptyhost.force_stop(expected)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    unittest.main()
