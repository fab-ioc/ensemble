"""Moving from claude-dashboard (the predecessor) to Ensemble.

* legacy_install.migrate() copies the old state files into ~/.ensemble, only
  where Ensemble has none, leaves ~/.claude/dashboard (program and all) as it
  was, writes a marker and does nothing on a second run -- whether or not a
  launcher made ~/.ensemble first;
* every way of starting the hub reaches it through dashboard.main();
* the `ensemble` / `ensemble.ps1` launchers never take another program on the
  port (the old dashboard used 8765 too) for Ensemble: no "Already running",
  no killing it.
"""
from __future__ import annotations

import json
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

import legacy_install  # noqa: E402

PROGRAM = {
    "dashboard.py": "print('old')\n",
    "index.html": "<html></html>\n",
    "install-launchd.sh": "#!/bin/sh\n",
    "com.claude-code.dashboard.plist.template": "<plist/>\n",
    ".git/HEAD": "ref: refs/heads/main\n",
    "rooms/r1.json": "{}",
    "server.pid": "123",
}
STATE = {
    "labels.json": {"s1": "old label"},
    "pinned.json": ["s1"],
    "categories.json": {"s1": "work"},
    "archived.json": ["s2"],
    "parents.json": {"s3": "s1"},
    "geometries.json": {"s1": [0, 0, 800, 600]},
    "favorite_themes.json": ["Solarized"],
    "jira_links.json": {"s1": ["AB-1"]},
    "jira_unlinks.json": {},
    "known_categories.json": ["work"],
    "editors.json": {"python": "code"},
    "settings.json": {"openMode": "tab", "defaultModel": "opus"},
}


def _tree(root: Path) -> dict:
    """Every file under root: relative path -> (bytes, mtime)."""
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


class Migrate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.old = self.home / ".claude" / "dashboard"
        self.new = self.home / ".ensemble"
        for rel, text in PROGRAM.items():
            (self.old / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.old / rel).write_text(text, encoding="utf-8")
        for name, data in STATE.items():
            (self.old / name).write_text(json.dumps(data), encoding="utf-8")
        self.before = _tree(self.old)
        # No LaunchAgent / task probe on the test machine.
        p = mock.patch.object(legacy_install, "old_service", return_value=None)
        self.service = p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def migrate(self):
        return legacy_install.migrate(self.home, self.new)

    def assert_migrated(self):
        self.assertEqual(_tree(self.old), self.before, "the old install changed")
        for name, data in STATE.items():
            self.assertEqual(json.loads((self.new / name).read_text(encoding="utf-8")), data, name)
        for rel in PROGRAM:
            self.assertFalse((self.new / rel).exists(), f"{rel} is the old program's, not state")
        self.assertTrue((self.new / legacy_install.MARKER_NAME).is_file())

    def test_fresh_state_dir(self):
        note = self.migrate()
        self.assert_migrated()
        self.assertIn("copied 12 state file(s)", note)
        self.assertIn("the old program is still at ~/.claude/dashboard", note)
        self.assertIn("no LaunchAgent or scheduled task", note)

    def test_state_dir_made_first_by_the_launcher(self):
        # `ensemble start`, ensemble.ps1 and install-task.ps1 mkdir ~/.ensemble
        # before the hub runs: that must not skip the copy.
        (self.new / "logs").mkdir(parents=True)
        self.migrate()
        self.assert_migrated()

    def test_second_run_does_nothing(self):
        self.migrate()
        after = _tree(self.new)
        (self.new / "labels.json").unlink()
        self.assertIsNone(self.migrate())
        self.assertFalse((self.new / "labels.json").exists(), "a second run copied again")
        del after["labels.json"]
        self.assertEqual(_tree(self.new), after)

    def test_ensembles_own_files_are_untouched(self):
        self.new.mkdir()
        (self.new / "labels.json").write_text('{"s9": "mine"}', encoding="utf-8")
        (self.new / "settings.json").write_text('{"openMode": "window", "theme": "dark"}', encoding="utf-8")
        labels_before = (self.new / "labels.json").stat().st_mtime_ns
        note = self.migrate()
        self.assertEqual((self.new / "labels.json").read_text(encoding="utf-8"), '{"s9": "mine"}')
        self.assertEqual((self.new / "labels.json").stat().st_mtime_ns, labels_before)
        # settings: only the old key Ensemble does not set yet is added.
        self.assertEqual(json.loads((self.new / "settings.json").read_text(encoding="utf-8")),
                         {"openMode": "window", "theme": "dark", "defaultModel": "opus"})
        self.assertIn("kept Ensemble's own labels.json", note)
        self.assertEqual(_tree(self.old), self.before)

    def test_settings_with_nothing_to_add_are_not_rewritten(self):
        self.new.mkdir()
        s = self.new / "settings.json"
        s.write_text('{"openMode": "window", "defaultModel": ""}', encoding="utf-8")
        self.migrate()
        self.assertEqual(s.read_text(encoding="utf-8"), '{"openMode": "window", "defaultModel": ""}')

    def test_a_failed_copy_is_retried_next_start(self):
        real = shutil.copy2

        def flaky(src, dst):
            if Path(src).name == "pinned.json":
                raise PermissionError("locked")
            return real(src, dst)

        with mock.patch.object(legacy_install.shutil, "copy2", side_effect=flaky):
            note = self.migrate()
        self.assertIn("could not copy pinned.json", note)
        self.assertFalse((self.new / legacy_install.MARKER_NAME).exists())
        self.migrate()
        self.assert_migrated()

    def test_no_old_install_no_change(self):
        shutil.rmtree(self.home / ".claude")
        self.assertIsNone(self.migrate())
        self.assertFalse(self.new.exists())

    def test_old_autostart_is_named_with_its_remove_command(self):
        self.service.return_value = ("its LaunchAgent com.claude-code.dashboard is loaded",
                                     "launchctl bootout gui/$(id -u)/com.claude-code.dashboard")
        note = self.migrate()
        self.assertIn("is loaded; remove it with: launchctl bootout", note)
        self.assertNotIn("\n", note)
        # Later starts say it again while it is still loaded, and only then.
        self.assertIn("remove it with", self.migrate())
        self.service.return_value = None
        self.assertIsNone(self.migrate())


class OldService(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_macos_loaded(self):
        with mock.patch.object(legacy_install, "_quiet", return_value=0) as q, \
                mock.patch.object(legacy_install.os, "getuid", create=True, return_value=501):
            what, how = legacy_install.old_service(self.home, "darwin")
        q.assert_called_once_with(["launchctl", "print", "gui/501/com.claude-code.dashboard"])
        self.assertIn("is loaded", what)
        self.assertIn("launchctl bootout gui/$(id -u)/com.claude-code.dashboard", how)
        self.assertIn("rm ~/Library/LaunchAgents/com.claude-code.dashboard.plist", how)

    def test_macos_plist_left_but_not_loaded(self):
        plist = self.home / "Library" / "LaunchAgents" / "com.claude-code.dashboard.plist"
        plist.parent.mkdir(parents=True)
        plist.write_text("<plist/>")
        with mock.patch.object(legacy_install, "_quiet", return_value=113), \
                mock.patch.object(legacy_install.os, "getuid", create=True, return_value=501):
            what, _ = legacy_install.old_service(self.home, "darwin")
        self.assertIn("installed but not loaded", what)

    def test_macos_none(self):
        with mock.patch.object(legacy_install, "_quiet", return_value=113), \
                mock.patch.object(legacy_install.os, "getuid", create=True, return_value=501):
            self.assertIsNone(legacy_install.old_service(self.home, "darwin"))

    def test_windows_task(self):
        with mock.patch.object(legacy_install, "_quiet", return_value=0):
            what, how = legacy_install.old_service(self.home, "win32")
        self.assertIn("ClaudeDashboard", what)
        self.assertEqual(how, "schtasks /Delete /TN ClaudeDashboard /F")
        with mock.patch.object(legacy_install, "_quiet", return_value=1):
            self.assertIsNone(legacy_install.old_service(self.home, "win32"))


class EveryEntryPoint(unittest.TestCase):
    def test_launchers_all_run_dashboard_main(self):
        for rel in ("ensemble", "install-launchd.sh", "ensemble.ps1", "install-task.ps1"):
            text = (ROOT / rel).read_text(encoding="utf-8-sig")
            self.assertIn("dashboard.py", text, rel)
        # The plist runs __SCRIPT__, which install-launchd.sh sets to dashboard.py.
        self.assertIn('SCRIPT="$DIR/dashboard.py"', (ROOT / "install-launchd.sh").read_text(encoding="utf-8"))
        self.assertIn("<string>__SCRIPT__</string>",
                      (ROOT / "com.ensemble.dashboard.plist.template").read_text(encoding="utf-8"))

    def test_no_migration_at_import_time(self):
        # The old move lived in backends/base.py at import, where `mkdir -p`
        # by a launcher decided whether it ran.
        src = (ROOT / "backends" / "base.py").read_text(encoding="utf-8")
        self.assertNotIn("shutil.move", src)
        self.assertNotIn('".claude" / "dashboard"', src)

    def test_main_migrates_before_serving(self):
        import dashboard

        class Stop(Exception):
            pass

        calls = []
        with mock.patch.object(sys, "argv", ["dashboard.py", "--port", "8765"]), \
                mock.patch.object(dashboard.ptyrun, "ensure_windows_console"), \
                mock.patch.object(dashboard.legacy_install, "migrate",
                                  side_effect=lambda h, d: calls.append((h, d)) or "claude-dashboard: note"), \
                mock.patch.object(dashboard, "cleanup_rename_artifacts", side_effect=Stop), \
                mock.patch("builtins.print") as pr:
            with self.assertRaises(Stop):
                dashboard.main()
        self.assertEqual(calls, [(dashboard.HOME, dashboard.DASHBOARD_DIR)])
        pr.assert_any_call("claude-dashboard: note", flush=True)

    def test_main_survives_a_migration_error(self):
        import dashboard

        class Stop(Exception):
            pass

        with mock.patch.object(sys, "argv", ["dashboard.py"]), \
                mock.patch.object(dashboard.ptyrun, "ensure_windows_console"), \
                mock.patch.object(dashboard.legacy_install, "migrate", side_effect=RuntimeError("boom")), \
                mock.patch.object(dashboard, "cleanup_rename_artifacts", side_effect=Stop), \
                mock.patch("builtins.print") as pr:
            with self.assertRaises(Stop):
                dashboard.main()
        pr.assert_any_call("claude-dashboard migration skipped: boom", flush=True)


def _real_bash():
    b = shutil.which("bash")
    # System32\bash.exe is WSL, which would not see these Windows paths.
    if b and "system32" in b.lower():
        return None
    return b


BASH = _real_bash()
OLD_CMD = "/usr/bin/python3 /home/u/.claude/dashboard/dashboard.py --port 8765"


@unittest.skipUnless(BASH, "bash not on PATH")
class BashLauncherPort(unittest.TestCase):
    """`ensemble` sourced with lsof/ps/curl/kill replaced: pid 4242 holds the
    port; `ensemble_hdr` says whether it answers as Ensemble."""

    def run_sh(self, body: str, ensemble: bool):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home.replace("\\", "/"),
                       ENS_SCRIPT=str(ROOT / "ensemble").replace("\\", "/"),
                       KILL_LOG=(Path(home) / "kills").as_posix())
            env.pop("ENSEMBLE_PORT", None)
            script = f"""
source "$ENS_SCRIPT"
lsof() {{ echo 4242; }}
ps() {{ echo "{OLD_CMD}"; }}
curl() {{ {'printf "HTTP/1.0 200 OK\\r\\nX-Ensemble-Stamp: abc\\r\\n\\r\\n"' if ensemble else 'printf "HTTP/1.0 200 OK\\r\\nServer: old\\r\\n\\r\\n"'}; }}
kill() {{ echo "$*" >> "$KILL_LOG"; }}
{body}
"""
            r = subprocess.run([BASH, "-c", script], capture_output=True, encoding="utf-8",
                               errors="replace", env=env, timeout=60)
            kills = Path(home, "kills").read_text() if Path(home, "kills").exists() else ""
            return r, kills

    def test_foreign_process_is_not_running(self):
        r, _ = self.run_sh('echo "pid=[$(running_pid)]"', ensemble=False)
        self.assertIn("pid=[]", r.stdout, r.stderr)

    def test_start_refuses_with_a_clear_message(self):
        r, kills = self.run_sh("cmd_start", ensemble=False)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Already running", r.stdout)
        self.assertIn("port 8765 is held by another program (pid 4242: " + OLD_CMD, r.stderr)
        self.assertIn("old claude-dashboard", r.stderr)
        self.assertIn("launchctl bootout gui/$(id -u)/com.claude-code.dashboard", r.stderr)
        self.assertIn("--port", r.stderr)
        self.assertEqual(kills, "")

    def test_stop_leaves_it_alone(self):
        r, kills = self.run_sh("cmd_stop", ensemble=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not Ensemble: left alone", r.stdout)
        self.assertEqual(kills, "")

    def test_an_ensemble_on_the_port_is_found_and_stopped(self):
        r, _ = self.run_sh('echo "pid=[$(running_pid)]"; cmd_start', ensemble=True)
        self.assertIn("pid=[4242]", r.stdout, r.stderr)
        self.assertIn("Already running (pid 4242)", r.stdout)
        r, kills = self.run_sh("cmd_stop", ensemble=True)
        self.assertIn("Stopped (was pid 4242)", r.stdout, r.stderr)
        self.assertIn("4242", kills)

    def test_a_stale_pid_file_of_another_program_is_ignored(self):
        body = 'mkdir -p "$HOME/.ensemble"; echo 4242 > "$HOME/.ensemble/server.pid"; ' \
               'lsof() { :; }; echo "pid=[$(running_pid)]"'
        r, _ = self.run_sh(body, ensemble=False)
        self.assertIn("pid=[]", r.stdout, r.stderr)


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL and os.name == "nt", "Windows PowerShell only")
class PowerShellLauncherPort(unittest.TestCase):
    """ensemble.ps1 dot-sourced with the port lookup, the command line and the
    HTTP probe replaced: pid 4242 holds the port."""

    OLD = r"D:\py\python.exe D:\u\.claude\dashboard\dashboard.py --port 8765"

    def run_ps(self, body: str, ensemble: bool):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, USERPROFILE=home)
            env.pop("ENSEMBLE_PORT", None)
            probe = ("return [pscustomobject]@{ Headers = @{ 'X-Ensemble-Stamp' = 'abc' } }"
                     if ensemble else "throw 'no'")
            script = f"""
. '{ROOT / "ensemble.ps1"}'
function Get-NetTCPConnection {{ [pscustomobject]@{{ OwningProcess = 4242 }} }}
function Get-CommandLine($ProcId) {{ '{self.OLD}' }}
function Invoke-WebRequest {{ {probe} }}
function Stop-Process {{ [CmdletBinding()] param($Id, [switch]$Force) Write-Host "KILLED $Id" }}
{body}
"""
            return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                   "-Command", script], capture_output=True, encoding="utf-8",
                                  errors="replace", env=env, timeout=120)

    def test_start_refuses_and_stop_leaves_it_alone(self):
        r = self.run_ps("Write-Host \"pid=[$(Get-RunningPid)]\"; Stop-Dashboard; Start-Dashboard", ensemble=False)
        out = r.stdout + r.stderr
        self.assertIn("pid=[]", out)
        self.assertIn("not Ensemble: left alone", out)
        self.assertIn("port 8765 is held by another program (pid 4242:", out)
        self.assertIn("schtasks /Delete /TN ClaudeDashboard /F", out)
        self.assertIn("-Port", out)
        self.assertNotIn("KILLED", out)
        self.assertNotIn("Already running", out)
        self.assertEqual(r.returncode, 1, out)

    def test_an_ensemble_on_the_port_is_found_and_stopped(self):
        r = self.run_ps("Start-Dashboard; Stop-Dashboard", ensemble=True)
        out = r.stdout + r.stderr
        self.assertIn("Already running (pid 4242)", out)
        self.assertIn("KILLED 4242", out)


if __name__ == "__main__":
    unittest.main()
