"""Update now brings the hub back on the port it ran on (P72).

* `start` records the port in ~/.ensemble/server.port; `restart`/`update`
  without a port use it (--port / -Port / ENSEMBLE_PORT still win), and a
  refused recorded port says so on stderr and in the log, exiting 1;
* the launchers are executable in git, and the hub runs `ensemble update`
  through bash with its own port;
* the update's output goes to the hub's log, never to DEVNULL.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backends import macos, windows  # noqa: E402


def _real_bash():
    b = shutil.which("bash")
    # System32\bash.exe is WSL, which would not see these Windows paths.
    if b and "system32" in b.lower():
        return None
    return b


BASH = _real_bash()
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
OLD_CMD = "/usr/bin/python3 /home/u/.claude/dashboard/dashboard.py --port 8765"


class GitModes(unittest.TestCase):
    def test_launchers_are_executable_in_git(self):
        out = subprocess.run(["git", "ls-files", "-s", "ensemble", "install-launchd.sh"],
                             cwd=ROOT, capture_output=True, encoding="utf-8").stdout
        modes = {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}
        self.assertEqual(modes, {"ensemble": "100755", "install-launchd.sh": "100755"})


def _stub(bin_dir: Path, name: str, body: str):
    p = bin_dir / name
    p.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
    p.chmod(0o755)


@unittest.skipUnless(BASH, "bash not on PATH")
class BashLauncher(unittest.TestCase):
    """`ensemble` run for real with lsof/ps/curl/python3/git/launchctl on PATH
    replaced. `python3` records its arguments and sleeps (the "hub")."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        (self.home / ".ensemble").mkdir()
        self.log = self.home / "Library" / "Logs" / "ensemble.log"
        self.args_file = self.home / "hub-args"
        _stub(self.bin, "python3",
              'if [[ "$1" == "-c" || "$1" == "-m" ]]; then exit 0; fi\n'
              f'echo "$*" >> "{self.args_file.as_posix()}"\nexec sleep 20')
        _stub(self.bin, "git", "exit 0")
        _stub(self.bin, "launchctl", "exit 1")
        self.foreign(False)

    def tearDown(self):
        # End any "hub" the test started.
        pid = self.home / ".ensemble" / "server.pid"
        if pid.exists() and pid.read_text().strip():
            subprocess.run([BASH, "-c", f"kill {pid.read_text().strip()} 2>/dev/null"])
        self.tmp.cleanup()

    def foreign(self, held: bool):
        # Whether another program (pid 4242, not Ensemble) holds every port.
        _stub(self.bin, "lsof", "echo 4242" if held else "exit 0")
        _stub(self.bin, "ps", f'echo "{OLD_CMD}"' if held else "exit 0")
        _stub(self.bin, "curl", 'printf "HTTP/1.0 200 OK\\r\\nServer: old\\r\\n\\r\\n"')

    def run_cli(self, *args, env=None):
        e = dict(os.environ, HOME=self.home.as_posix())
        e.pop("ENSEMBLE_PORT", None)
        e.pop("ENSEMBLE_STDIO_IS_LOG", None)
        e.update(env or {})
        path = self.bin.as_posix()
        if len(path) > 1 and path[1] == ":":  # Git Bash: C:/x -> /c/x (PATH splits on ':')
            path = f"/{path[0].lower()}{path[2:]}"
        cmd = f'PATH="{path}:$PATH"; exec bash "{(ROOT / "ensemble").as_posix()}" ' + " ".join(args)
        return subprocess.run([BASH, "-c", cmd], capture_output=True, encoding="utf-8",
                              errors="replace", env=e, timeout=60)

    def record(self, port):
        (self.home / ".ensemble" / "server.port").write_text(f"{port}\n")

    def hub_ports(self):
        lines = self.args_file.read_text().splitlines() if self.args_file.exists() else []
        return [ln.split("--port ")[1].split()[0] for ln in lines]

    def test_start_records_the_port(self):
        r = self.run_cli("start", "--port", "8770")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("http://127.0.0.1:8770", r.stdout)
        self.assertEqual((self.home / ".ensemble" / "server.port").read_text().strip(), "8770")
        # The pid file still holds only the pid, for every older reader.
        self.assertRegex((self.home / ".ensemble" / "server.pid").read_text().strip(), r"^\d+$")

    def test_restart_reuses_the_recorded_port(self):
        self.record(8770)
        r = self.run_cli("restart")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub_ports(), ["8770"])

    def test_plain_start_keeps_the_default(self):
        self.record(8770)
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub_ports(), ["8765"])

    def test_explicit_port_and_env_win(self):
        self.record(8770)
        self.run_cli("restart", "--port", "8771")
        self.assertEqual(self.hub_ports(), ["8771"])
        self.run_cli("stop")
        self.run_cli("restart", env={"ENSEMBLE_PORT": "8772"})
        self.assertEqual(self.hub_ports(), ["8771", "8772"])

    def test_a_bad_recorded_port_is_ignored(self):
        self.record("junk")
        self.run_cli("restart")
        self.assertEqual(self.hub_ports(), ["8765"])

    def test_update_without_the_service_reuses_the_port(self):
        self.record(8770)
        r = self.run_cli("update")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub_ports(), ["8770"])
        self.assertIn("Done.", r.stdout)

    def test_update_takes_the_hubs_port(self):
        self.record(8770)
        r = self.run_cli("update", "--port", "8780")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hub_ports(), ["8780"])

    def test_a_refused_recorded_port_says_so_and_logs_it(self):
        self.record(8770)
        self.foreign(True)
        r = self.run_cli("restart")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("port 8770 is held by another program (pid 4242", r.stderr)
        self.assertIn("recorded in", r.stderr)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("port 8770 is held by another program", log)
        self.assertIn("Ensemble was not started", log)
        self.assertEqual(self.hub_ports(), [])

    def test_update_refused_exits_nonzero_without_logging_twice(self):
        # Spawned by the hub, stderr already is the log: no second copy.
        self.record(8770)
        self.foreign(True)
        r = self.run_cli("update", env={"ENSEMBLE_STDIO_IS_LOG": "1"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("port 8770 is held by another program", r.stderr)
        self.assertNotIn("Done.", r.stdout)
        self.assertFalse(self.log.exists() and "held by" in self.log.read_text(encoding="utf-8"))


@unittest.skipUnless(POWERSHELL and os.name == "nt", "Windows PowerShell only")
class PowerShellLauncher(unittest.TestCase):
    """ensemble.ps1 dot-sourced with an action (so the port is resolved as for
    that action) and the process, port and Python lookups replaced."""

    def run_ps(self, args: str, body: str, held=False, env=None, record=None):
        with tempfile.TemporaryDirectory() as home:
            e = dict(os.environ, USERPROFILE=home)
            e.pop("ENSEMBLE_PORT", None)
            e.pop("ENSEMBLE_STDIO_IS_LOG", None)
            e.update(env or {})
            if record is not None:
                Path(home, ".ensemble").mkdir()
                Path(home, ".ensemble", "server.port").write_text(f"{record}\n", encoding="ascii")
            conn = "[pscustomobject]@{ OwningProcess = 4242 }" if held else "throw 'none'"
            script = f"""
. '{ROOT / "ensemble.ps1"}' {args}
function Get-NetTCPConnection {{ {conn} }}
function Get-CommandLine($ProcId) {{ 'D:\\u\\.claude\\dashboard\\dashboard.py' }}
function Invoke-WebRequest {{ throw 'no' }}
function FakePy {{ $global:LASTEXITCODE = 0 }}
function Resolve-Python {{ 'FakePy' }}
function Resolve-PythonW {{ 'fakepythonw.exe' }}
function Start-Process {{ [CmdletBinding()] param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
  Write-Host "HUB $($ArgumentList -join ' ')"; [pscustomobject]@{{ Id = 777 }} }}
function Get-Process {{ [CmdletBinding()] param($Id) if ($Id -eq 777) {{ [pscustomobject]@{{ Id = 777 }} }} }}
function Start-Sleep {{ }}
{body}
"""
            r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-Command", script], capture_output=True, encoding="utf-8",
                               errors="replace", env=e, timeout=120)
            port_file = Path(home, ".ensemble", "server.port")
            log = Path(home, ".ensemble", "logs", "ensemble.log")
            return (r, port_file.read_text().strip() if port_file.exists() else None,
                    log.read_text(encoding="utf-8") if log.exists() else "")

    def test_start_records_the_port(self):
        r, port, _ = self.run_ps("start -Port 8770", "Start-Dashboard")
        self.assertIn("HUB", r.stdout, r.stderr)
        self.assertIn("--port 8770", r.stdout)
        self.assertEqual(port, "8770")

    def test_restart_reuses_the_recorded_port(self):
        r, _, _ = self.run_ps("restart", "Start-Dashboard", record=8770)
        self.assertIn("--port 8770", r.stdout, r.stderr)

    def test_start_keeps_the_default_and_explicit_ports_win(self):
        r, _, _ = self.run_ps("start", "Start-Dashboard", record=8770)
        self.assertIn("--port 8765", r.stdout, r.stderr)
        r, _, _ = self.run_ps("restart -Port 8771", "Start-Dashboard", record=8770)
        self.assertIn("--port 8771", r.stdout, r.stderr)
        r, _, _ = self.run_ps("restart", "Start-Dashboard", record=8770,
                              env={"ENSEMBLE_PORT": "8772"})
        self.assertIn("--port 8772", r.stdout, r.stderr)

    def test_a_refused_recorded_port_says_so_and_logs_it(self):
        r, _, log = self.run_ps("restart", "Start-Dashboard", held=True, record=8770)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("port 8770 is held by another program (pid 4242", r.stderr)
        self.assertIn("recorded in", r.stderr)
        self.assertIn("port 8770 is held by another program", log)
        self.assertNotIn("HUB", r.stdout)

    def test_no_second_copy_when_stderr_is_the_log(self):
        r, _, log = self.run_ps("restart", "Start-Dashboard", held=True, record=8770,
                                env={"ENSEMBLE_STDIO_IS_LOG": "1"})
        self.assertIn("held by another program", r.stderr)
        self.assertEqual(log, "")


class SelfUpdateSpawn(unittest.TestCase):
    def test_macos_runs_through_bash_with_the_port_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ensemble").write_text("#!/usr/bin/env bash\n")
            log = Path(d) / "logs" / "ensemble.log"
            with mock.patch.object(macos.subprocess, "Popen") as popen:
                r = macos.MacBackend.self_update(object(), d, port=8770, log_file=log)
            self.assertTrue(r["started"], r)
            self.assertEqual(r["log"], str(log))
            argv = popen.call_args.args[0]
            self.assertTrue(Path(argv[0]).name.startswith("bash"), argv)
            self.assertEqual(argv[1:], [str(Path(d) / "ensemble"), "update", "--port", "8770"])
            kw = popen.call_args.kwargs
            self.assertNotEqual(kw["stdout"], subprocess.DEVNULL)
            self.assertEqual(Path(kw["stdout"].name), log)
            self.assertEqual(kw["stderr"], subprocess.STDOUT)
            self.assertEqual(kw["env"]["ENSEMBLE_STDIO_IS_LOG"], "1")

    def test_macos_default_log_is_the_readme_one(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ensemble").write_text("")
            with mock.patch.object(macos, "HOME", Path(d)), \
                    mock.patch.object(macos.subprocess, "Popen") as popen:
                macos.MacBackend.self_update(object(), d)
            self.assertEqual(Path(popen.call_args.kwargs["stdout"].name),
                             Path(d) / "Library" / "Logs" / "ensemble.log")
            self.assertNotIn("--port", popen.call_args.args[0])

    def test_windows_restarts_on_the_port_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "logs" / "ensemble.log"
            with mock.patch.object(windows, "LAUNCH_DIR", Path(d) / "launch"), \
                    mock.patch.object(windows.shutil, "which", return_value="powershell.exe"), \
                    mock.patch.object(windows.subprocess, "Popen") as popen:
                r = windows.WindowsBackend.self_update(object(), Path(d) / "repo",
                                                       port=8770, log_file=log)
            self.assertTrue(r["started"], r)
            script = Path(popen.call_args.args[0][-1]).read_text(encoding="utf-8")
            self.assertIn("ensemble.ps1' restart -Port 8770", script)
            self.assertIn("the hub did not come back", script)
            self.assertNotIn("2>$null }", script)  # git reset's errors reach the log
            kw = popen.call_args.kwargs
            self.assertEqual(Path(kw["stdout"].name), log)
            self.assertEqual(kw["stderr"], subprocess.STDOUT)
            self.assertEqual(kw["env"]["ENSEMBLE_STDIO_IS_LOG"], "1")

    def test_hub_passes_its_port_and_log(self):
        import dashboard
        with mock.patch.object(dashboard, "BACKEND") as be, \
                mock.patch.object(dashboard, "HUB_PORT", 8770), \
                mock.patch.object(dashboard, "_LOG_FILE", Path("x.log")), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENSEMBLE_UPDATE_DRY_RUN", None)
            be.self_update.return_value = {"started": True}
            dashboard.trigger_update()
        be.self_update.assert_called_once_with(dashboard.STATIC_DIR, port=8770,
                                               log_file=Path("x.log"))


class Page(unittest.TestCase):
    def test_update_page_names_the_log_when_the_hub_stays_down(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("The hub did not come back", html)
        self.assertIn("function showUpdateFailed(logPath)", html)
        self.assertIn('id="update-close"', html)
        self.assertNotIn("update timed out", html)


if __name__ == "__main__":
    unittest.main()
