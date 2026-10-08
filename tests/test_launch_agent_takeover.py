"""#186: a checkout's LaunchAgent has the app's label (install-launchd.sh writes
com.ensemble.dashboard too). It is told apart by its program, never
overwritten or deleted, only moved aside, and the app's own takes its place."""
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app_launch  # noqa: E402
import app_setup  # noqa: E402
import app_update  # noqa: E402
import app_version  # noqa: E402

APP_EXE = "/Applications/Ensemble.app/Contents/MacOS/Ensemble"


def _checkout_plist(path: Path, script: str = "/Users/me/ensemble/dashboard.py") -> bytes:
    data = plistlib.dumps({"Label": app_setup.LAUNCHD_LABEL,
                           "ProgramArguments": ["/usr/bin/python3", script, "--port", "8765"],
                           "KeepAlive": True})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


class LaunchAgentKindTest(unittest.TestCase):
    def test_the_app_is_told_from_a_checkout_by_its_program(self):
        self.assertTrue(app_setup.is_app_program(APP_EXE))
        self.assertTrue(app_setup.is_app_program("/Users/me/Applications/Ensemble.app/Contents/MacOS/Ensemble"))
        for program in ("/usr/bin/python3", "/opt/homebrew/bin/python3.12", "",
                        "/Applications/Ensemble.app/Contents/MacOS/Ensemble-helper"):
            self.assertFalse(app_setup.is_app_program(program), program)

    def test_program_of_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.plist"
            self.assertIsNone(app_setup.launch_agent_program(p))
            _checkout_plist(p)
            self.assertEqual(app_setup.launch_agent_program(p), "/usr/bin/python3")
            p.write_bytes(plistlib.dumps({"Label": "x", "Program": APP_EXE}))
            self.assertEqual(app_setup.launch_agent_program(p), APP_EXE)
            p.write_text("not a plist")
            self.assertIsNone(app_setup.launch_agent_program(p))

    def test_moving_aside_never_overwrites_an_earlier_backup(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            plist = home / "Library" / "LaunchAgents" / "com.ensemble.dashboard.plist"
            first = _checkout_plist(plist, "/a/dashboard.py")
            a = app_setup.move_launch_agent_aside(plist, home)
            second = _checkout_plist(plist, "/b/dashboard.py")
            b = app_setup.move_launch_agent_aside(plist, home)
            self.assertNotEqual(a, b)
            self.assertEqual(a.read_bytes(), first)
            self.assertEqual(b.read_bytes(), second)
            self.assertFalse(plist.exists())


class SetAutostartTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.plist = self.home / "Library" / "LaunchAgents" / "com.ensemble.dashboard.plist"
        self.log = self.home / "Library" / "Logs" / "ensemble.log"
        patches = [mock.patch.object(Path, "home", classmethod(lambda cls: self.home))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_turning_it_on_moves_a_checkouts_agent_aside_and_writes_the_apps(self):
        old = _checkout_plist(self.plist)
        res = app_setup.set_autostart(True, PurePosixPath(APP_EXE), 8765, self.log, platform="darwin")
        self.assertTrue(res["thisApp"])
        self.assertEqual(app_setup.launch_agent_program(self.plist), APP_EXE)
        aside = Path(res["movedAside"])
        self.assertEqual(aside.parent, self.home / ".ensemble" / "old-launch-agents")
        self.assertEqual(aside.read_bytes(), old)

    def test_turning_it_off_leaves_a_checkouts_agent_alone(self):
        old = _checkout_plist(self.plist)
        app_setup.set_autostart(False, PurePosixPath(APP_EXE), 8765, self.log, platform="darwin")
        self.assertEqual(self.plist.read_bytes(), old)

    def test_the_apps_own_agent_is_replaced_or_removed_without_a_backup(self):
        app_setup.set_autostart(True, PurePosixPath("/Old/Ensemble.app/Contents/MacOS/Ensemble"), 8765, self.log,
                                platform="darwin")
        res = app_setup.set_autostart(True, PurePosixPath(APP_EXE), 8765, self.log, platform="darwin")
        self.assertNotIn("movedAside", res)
        self.assertFalse((self.home / ".ensemble" / "old-launch-agents").exists())
        app_setup.set_autostart(False, PurePosixPath(APP_EXE), 8765, self.log, platform="darwin")
        self.assertFalse(self.plist.exists())


class TakeOverSignInTest(unittest.TestCase):
    def test_the_app_writes_its_own_agent_after_the_older_one_was_moved_aside(self):
        did = ["stopped its sign-in service com.ensemble.dashboard", "moved a to b (move it back ...)"]
        with mock.patch.object(app_launch.sys, "platform", "darwin"), \
                mock.patch.object(app_version, "packaged", return_value=True), \
                mock.patch.object(app_version, "app_executable", return_value=Path(APP_EXE)), \
                mock.patch.object(app_setup, "set_autostart", return_value={"thisApp": True}) as sa:
            out = app_launch.take_over_sign_in(did, 8765, Path("/Users/me"))
        sa.assert_called_once()
        self.assertTrue(sa.call_args.args[0])
        self.assertEqual(sa.call_args.args[1], Path(APP_EXE))
        self.assertTrue(any("starts at sign-in" in line for line in out))

    def test_nothing_is_written_when_no_agent_was_moved_or_from_a_checkout(self):
        for did, packaged in ((["sent SIGTERM to process 9"], True), (["moved a to b"], False)):
            with mock.patch.object(app_launch.sys, "platform", "darwin"), \
                    mock.patch.object(app_version, "packaged", return_value=packaged), \
                    mock.patch.object(app_setup, "set_autostart") as sa:
                self.assertEqual(app_launch.take_over_sign_in(did, 8765, Path("/Users/me")), [])
            sa.assert_not_called()


class UpdaterLaunchdTest(unittest.TestCase):
    def _helper(self, svc):
        with tempfile.TemporaryDirectory() as d:
            h = app_update.Helper({"launchd": svc, "args": [], "port": 1, "log": str(Path(d) / "u.log")})
        return h

    def test_a_checkouts_agent_file_is_not_started_again_after_the_update(self):
        svc = {"target": "gui/501/com.ensemble.dashboard", "domain": "gui/501",
               "plist": "/x.plist", "plistIsApp": False}
        h = self._helper(svc)
        with mock.patch.object(app_update.subprocess, "run") as run, \
                mock.patch.object(app_update, "_start", return_value=mock.Mock(pid=7)) as start:
            h.start_hub(Path(APP_EXE), True)
        run.assert_not_called()
        start.assert_called_once()

    def test_the_apps_agent_file_is_started_through_launchd(self):
        svc = {"target": "gui/501/com.ensemble.dashboard", "domain": "gui/501",
               "plist": "/x.plist", "plistIsApp": True}
        h = self._helper(svc)
        with mock.patch.object(app_update.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(app_update, "_start") as start:
            h.start_hub(Path(APP_EXE), True)
        self.assertEqual(run.call_args.args[0][:2], ["launchctl", "bootstrap"])
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
