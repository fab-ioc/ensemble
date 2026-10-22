"""A test's in-process hub never starts a real agent (#257).

On 2026-10-22 a suite run started real Codex agents with an empty CODEX_HOME
through a test's hub; each opened a ChatGPT sign-in page in the browser and
the run hung on them. agent_launch.py refuses every agent start in a process
that is not a hub (dashboard.main() alone allows it), and a hub refuses to
start an agent that has no login.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import agent_launch  # noqa: E402
import dashboard  # noqa: E402
from backends import ptyrun  # noqa: E402
import agent_processes  # noqa: E402


class _Agent:
    def ensure_trusted(self, cwd):
        pass


class WhatIsAnAgent(unittest.TestCase):
    def test_agent_commands(self):
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "pty-x.ps1"
            script.write_text("Set-Location -LiteralPath 'C:\\w'\n& 'codex' '-c' 'x' @'\nhi\n'@\n",
                              encoding="utf-8")
            plain = Path(d) / "other.ps1"
            plain.write_text("& 'py' 'receiver.py'\n# claude\n", encoding="utf-8")
            for argv, want in (
                (["codex", "resume", "s"], "codex"),
                (["C:\\npm\\claude.cmd", "--resume", "s"], "claude"),
                (["/usr/local/bin/claude"], "claude"),
                ("codex -c x", "codex"),
                (["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)], "agent"),
                (["wt", "-w", "0", "nt", "pwsh", "-File", str(script)], "agent"),
                (["cmd", "/c", "codex"], "codex"),
                (["powershell", "-File", str(plain)], ""),
                ([sys.executable, "-c", "print('claude')"], ""),
                (["git", "commit", "-m", "claude"], ""),
                (["node", "x.js"], ""),
                ([], ""),
            ):
                with self.subTest(argv=argv):
                    self.assertEqual(agent_launch.agent_in(argv), want)


class RefusedUnderTests(unittest.TestCase):
    def test_a_test_process_is_not_a_hub(self):
        self.assertFalse(agent_launch.allowed())

    def test_popen_and_pty_spawn_of_an_agent_are_refused_and_recorded(self):
        n = len(agent_launch.REFUSED)
        with self.assertRaises(agent_launch.LaunchRefused):
            subprocess.Popen(["codex", "--version"])
        with self.assertRaises(OSError):     # what a missing program raises too
            subprocess.Popen(["claude", "--version"])
        with self.assertRaises(agent_launch.LaunchRefused):
            ptyrun._LOCAL_API["create"](["codex"])
        got = agent_launch.REFUSED[n:]
        self.assertEqual([r["agent"] for r in got], ["codex", "claude", "codex"])
        self.assertEqual({r["test"] for r in got}, {self.id()})
        # What is refused is said so in the run, and kept for the last check.
        del agent_launch.REFUSED[n:]

    def test_other_programs_still_run(self):
        out = subprocess.run([sys.executable, "-c", "print('claude')"], capture_output=True,
                             text=True, encoding="utf-8").stdout
        self.assertEqual(out.strip(), "claude")

    def test_the_pty_host_is_refused_an_agent_too(self):
        client = mock.Mock()
        client.create.return_value = types.SimpleNamespace(id="pty-1", _info=None, _info_at=0)
        proxy = dashboard.OneProxyPerPty(client)
        n = len(agent_launch.REFUSED)
        with self.assertRaises(agent_launch.LaunchRefused):
            proxy.create(["claude", "--resume", "x"], cwd=".")
        client.create.assert_not_called()
        del agent_launch.REFUSED[n:]
        self.assertEqual(proxy.create([sys.executable, "-V"]).id, "pty-1")

    def test_a_test_may_opt_in(self):
        with agent_launch.opted_in():
            self.assertTrue(agent_launch.allowed())
            agent_launch.guard_spawn(["codex"], "test")     # no refusal
        self.assertFalse(agent_launch.allowed())


class TheHubPathsAreRefused(unittest.TestCase):
    """The launch code itself, with nothing faked below it: no agent starts,
    and its launch script lands in the test's state dir."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ens-nolaunch-", ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = {"taskAgentTools": "ensemble", "agentModels": dashboard.agent_models.normalise(None),
                    "rtkForTasks": False, "codexToolOutputTokens": 0,
                    "readCapBytes": 0, "readCapLines": 1}
        for p in (
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.root),
            mock.patch.object(dashboard, "load_projects", return_value=[]),
            mock.patch.object(dashboard, "load_session_projects", return_value={}),
            mock.patch.object(dashboard, "load_settings", side_effect=lambda: dict(settings)),
            mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
            mock.patch.object(dashboard.agents, "get_agent", return_value=_Agent()),
            mock.patch.object(dashboard.Handler, "_codex_ensemble_only_args", return_value=[]),
            # The in-process PTYs, whichever the run attached before.
            mock.patch.object(dashboard.ptyrun, "create", ptyrun._LOCAL_API["create"]),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "codex")}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
        self.n = len(agent_launch.REFUSED)
        self.addCleanup(lambda: agent_launch.REFUSED.__delitem__(slice(self.n, None)))

    def room(self, kind):
        part = {"identity": kind, "agent": kind, "kind": "agent", "sessionId": "",
                "model": "", "cwd": str(self.root), "role": "engineer"}
        return {"id": "room-x", "title": "task", "cwd": str(self.root), "sharedCwd": True,
                "projectId": "", "mode": "collab", "tokens": {"tok": kind},
                "participants": [part]}, part

    def test_start_and_resume_refused_for_both_agents(self):
        for kind in ("codex", "claude"):
            for how in ("start", "resume"):
                with self.subTest(kind=kind, how=how):
                    room, part = self.room(kind)
                    with self.assertRaises(agent_launch.LaunchRefused):
                        if how == "start":
                            self.handler._launch_room_agent_pty(room, part, "do it")
                        else:
                            part["sessionId"] = "s-old"
                            self.handler._resume_room_agent_pty(room, part)
        self.assertEqual([r["agent"] for r in agent_launch.REFUSED[self.n:]], ["agent"] * 4
                         if sys.platform == "win32" else ["codex", "codex", "claude", "claude"])

    @unittest.skipUnless(sys.platform == "win32", "launch scripts are Windows-only")
    def test_launch_scripts_go_to_the_tests_state_dir(self):
        real = dashboard.HOME / ".ensemble" / "_launch"
        before = set(os.listdir(real)) if real.is_dir() else set()
        room, part = self.room("codex")
        with self.assertRaises(agent_launch.LaunchRefused):
            self.handler._launch_room_agent_pty(room, part, "do it")
        mine = list((self.root / "_launch").glob("pty-*.ps1"))
        self.assertEqual(len(mine), 1)
        self.assertEqual(agent_launch.agent_in(["powershell", "-File", str(mine[0])]), "agent")
        after = set(os.listdir(real)) if real.is_dir() else set()
        self.assertNotIn(mine[0].name, after - before)


class SignedIn(unittest.TestCase):
    """A hub never starts an agent with no login: it says "not signed in"."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ens-signin-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY",
                              "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                              "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")}
        clean["CODEX_HOME"] = str(self.root / "codex")
        clean["CLAUDE_CONFIG_DIR"] = str(self.root / "claude")
        p = mock.patch.dict(os.environ, clean, clear=True)
        p.start()
        self.addCleanup(p.stop)
        (self.root / "codex").mkdir()
        (self.root / "claude").mkdir()
        # The agent's own CLI, asked when there is no login file: never run here.
        self.cli = mock.patch.object(dashboard.app_setup, "_signed_in", return_value=False)
        self.asked = self.cli.start()
        self.addCleanup(self.cli.stop)
        p = mock.patch.object(agent_launch.shutil, "which", side_effect=lambda k: f"/bin/{k}")
        p.start()
        self.addCleanup(p.stop)

    def test_codex_without_auth_is_not_started(self):
        with agent_launch.opted_in():
            with self.assertRaises(dashboard.StartRoomError) as e:
                dashboard.check_signed_in("codex")
            self.assertIn("Codex is not signed in", str(e.exception))
            self.assertIn("codex login", str(e.exception))
            (self.root / "codex" / "auth.json").write_text("{}", encoding="utf-8")
            dashboard.check_signed_in("codex")
            # The seat's own CODEX_HOME is the one looked at.
            with self.assertRaises(dashboard.StartRoomError):
                dashboard.check_signed_in("codex", {"CODEX_HOME": str(self.root)})
            dashboard.check_signed_in("codex", {"OPENAI_API_KEY": "k", "CODEX_HOME": str(self.root)})

    def test_claude_without_credentials_is_not_started(self):
        with agent_launch.opted_in():
            with self.assertRaises(dashboard.StartRoomError) as e:
                dashboard.check_signed_in("claude")
            self.assertIn("Claude Code is not signed in", str(e.exception))
            (self.root / "claude" / ".credentials.json").write_text("{}", encoding="utf-8")
            dashboard.check_signed_in("claude")
            dashboard.check_signed_in("claude-2")
            dashboard.check_signed_in("claude", {"CLAUDE_CONFIG_DIR": str(self.root),
                                                 "ANTHROPIC_API_KEY": "k"})

    def test_without_a_login_file_the_cli_decides(self):
        with agent_launch.opted_in():
            self.asked.return_value = None      # it would not say: started
            dashboard.check_signed_in("claude")
            self.asked.return_value = True      # e.g. a Mac's keychain login
            dashboard.check_signed_in("codex", {"CODEX_HOME": str(self.root / "seat")})
            key, exe, run = self.asked.call_args.args
            self.assertEqual((key, exe), ("codex", "/bin/codex"))
            with mock.patch.object(agent_launch.subprocess, "run") as sub:
                run(["x"])
            self.assertEqual(sub.call_args.kwargs["env"]["CODEX_HOME"], str(self.root / "seat"))
        with agent_launch.opted_in(),                 mock.patch.object(agent_launch.shutil, "which", return_value=None):
            self.asked.reset_mock()
            dashboard.check_signed_in("codex")     # not installed: its launch says so
            self.asked.assert_not_called()

    def test_an_unknown_agent_and_a_test_process_are_not_asked(self):
        dashboard.check_signed_in("codex")      # not a hub: the spawn guard decides
        with agent_launch.opted_in():
            dashboard.check_signed_in("gemini")

    def test_a_room_launch_says_not_signed_in(self):
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
        made = []
        part = {"identity": "codex", "agent": "codex", "kind": "agent", "sessionId": "",
                "model": "", "role": "engineer"}
        room = {"id": "room-x", "title": "t", "cwd": str(self.root), "sharedCwd": True,
                "projectId": "", "mode": "collab", "tokens": {}, "participants": [part]}
        with mock.patch.object(dashboard, "DASHBOARD_DIR", self.root), \
                mock.patch.object(dashboard, "load_projects", return_value=[]), \
                mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")), \
                mock.patch.object(dashboard.agents, "get_agent", return_value=_Agent()), \
                mock.patch.object(dashboard.Handler, "_codex_ensemble_only_args", return_value=[]), \
                mock.patch.object(dashboard.Handler, "_task_agent_tools", return_value="ensemble"), \
                mock.patch.object(dashboard.ptyrun, "create", side_effect=lambda *a, **k: made.append(a)), \
                agent_launch.opted_in():
            with self.assertRaises(dashboard.StartRoomError):
                handler._launch_room_agent_pty(room, part, "do it")
        self.assertEqual(made, [])


@unittest.skipUnless(sys.platform == "win32", "launch scripts are Windows-only")
class StaleLaunchScripts(unittest.TestCase):
    def test_agent_scripts_older_than_a_day_go(self):
        from backends import windows
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            now = time.time()
            hexid = "0123456789abcdef0123456789abcdef"
            files = {"old-pty": f"pty-{hexid}.ps1", "old-wt": f"{hexid}.ps1",
                     "new-pty": f"pty-{hexid[::-1]}.ps1", "old-update": f"update-{hexid}.ps1",
                     "old-restart": "restart-x.json"}
            for key, name in files.items():
                f = d / name
                f.write_text("x", encoding="utf-8")
                age = 3600 if key.startswith("new") else 2 * 86400
                os.utime(f, (now - age, now - age))
            with mock.patch.dict(windows._pruned_at, clear=True):
                self.assertEqual(windows._prune_launch_scripts(d, now), 2)
                # At most once an hour.
                self.assertEqual(windows._prune_launch_scripts(d, now + 60), 0)
            self.assertEqual(sorted(p.name for p in d.iterdir()),
                             sorted(files[k] for k in ("new-pty", "old-update", "old-restart")))


class NoAgentProcess(unittest.TestCase):
    def test_no_agent_runs_under_this_test_process(self):
        self.assertEqual(agent_processes.agent_descendants(), [])

    def test_the_process_check_sees_an_agent(self):
        # A stand-in named like an agent, started past the guard.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            if sys.platform == "win32":
                fake = Path(d) / "codex.cmd"
                fake.write_text(f'@"{sys.executable}" -c "import time; time.sleep(30)"\r\n',
                                encoding="utf-8")
                argv = ["cmd", "/c", str(fake)]
            else:
                fake = Path(d) / "codex"
                fake.symlink_to(sys.executable)
                argv = [str(fake), "-c", "import time; time.sleep(30)"]
            with agent_launch.opted_in():
                proc = subprocess.Popen(argv)
            try:
                found = agent_processes.agent_descendants()
                self.assertIn(proc.pid, [r["pid"] for r in found])
            finally:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True)
                else:
                    proc.kill()
                proc.wait(10)


if __name__ == "__main__":
    unittest.main()
