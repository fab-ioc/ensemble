"""The test hub script's stop finds processes by their environment, never by
their command line, and never ends itself or the processes that started it."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outside_test_hub as hub  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "reads environments with macOS ps")
class FindByEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="made-up-test-home-")
        self.addCleanup(os.rmdir, self.folder)

    def child(self, env_value=None, in_command=False):
        env = dict(os.environ)
        env.pop(hub.DATA_HOME_ENV, None)
        if env_value:
            env[hub.DATA_HOME_ENV] = env_value
        code = "import time; time.sleep(30)"
        if in_command:
            code += f"  # {hub.DATA_HOME_ENV}={self.folder}"
        p = subprocess.Popen([sys.executable, "-c", code], env=env)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p.pid

    def test_found_by_environment_only(self):
        has = self.child(env_value=self.folder)
        mentions = self.child(in_command=True)
        other = self.child(env_value=self.folder + "-other")
        found = hub.pids_with_env(hub.DATA_HOME_ENV, self.folder)
        self.assertIn(has, found)
        self.assertNotIn(mentions, found)
        self.assertNotIn(other, found)

    def test_never_itself_or_its_callers(self):
        mine = hub.ancestors()
        self.assertIn(os.getpid(), mine)
        self.assertIn(os.getppid(), mine)

    def test_has_env_is_exact(self):
        line = f" PATH=/bin {hub.DATA_HOME_ENV}={self.folder} LANG=C"
        self.assertTrue(hub.has_env(line, hub.DATA_HOME_ENV, self.folder))
        self.assertFalse(hub.has_env(line, hub.DATA_HOME_ENV, self.folder + "/x"))
        self.assertFalse(hub.has_env(line, hub.DATA_HOME_ENV, self.folder[:-1]))


@unittest.skipUnless(sys.platform == "darwin", "reads environments with macOS ps")
class EndOnlyItsOwnTest(unittest.TestCase):
    """end() leaves alone a process that is not the test hub's, even when its
    pid comes from the hub's own files."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="made-up-test-home-"))
        self.addCleanup(os.rmdir, self.folder)

    def child(self, code, env_value=None):
        env = dict(os.environ)
        env.pop(hub.DATA_HOME_ENV, None)
        if env_value:
            env[hub.DATA_HOME_ENV] = env_value
        p = subprocess.Popen([sys.executable, "-c", code], env=env)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def test_a_hub_without_the_test_folder_in_its_environment_is_left_alone(self):
        p = self.child("import time; time.sleep(30)  # dashboard")
        hub.end(p.pid, "hub", must_match="dashboard", folder=self.folder)
        self.assertIsNone(p.poll())

    def test_the_test_hub_is_ended(self):
        p = self.child("import time; time.sleep(30)  # dashboard", env_value=str(self.folder))
        hub.end(p.pid, "hub", must_match="dashboard", folder=self.folder)
        self.assertIsNotNone(p.wait(timeout=10))

    def test_a_pid_from_an_old_host_file_is_left_alone(self):
        p = self.child("import time; time.sleep(30)")
        hub.end(p.pid, "PTY host", must_match=str(self.folder / ".ensemble" / "pty-host-start.json"))
        self.assertIsNone(p.poll())
