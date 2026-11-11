"""The Linux hub's plain restart and Update now (ED-336), subprocess stubbed.

* the backend finds, once, whether a systemd user unit runs the hub as its
  main process (cgroup leaf + MainPID), and puts what must outlive the hub
  (the restart helper, the PTY host, `ensemble update`) in a scope of its own
  only then;
* self_restart writes the plan (this user only) and starts restart_helper.py;
* the helper stops and starts the hub through the unit, makes a transient
  unit again under its name, and starts the hub itself when systemd fails;
* the plan names the unit; install-systemd.sh runs the hub from its clone.

The live proof (port 8798 under a transient unit) is in the task's report.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backends import linux  # noqa: E402
import restart_helper  # noqa: E402

V2 = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/ensemble.service\n"


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class CgroupUnit(TestCase):
    def test_the_leaf_service_is_the_unit(self):
        self.assertEqual(linux._cgroup_unit(V2), "ensemble.service")

    def test_a_terminal_scope_is_no_unit(self):
        # The user manager (user@1000.service) further up is not the hub's.
        self.assertEqual(linux._cgroup_unit(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "app-org.gnome.Terminal.slice/vte-spawn-1.scope\n"), "")
        self.assertEqual(linux._cgroup_unit(""), "")


class SystemdService(TestCase):
    def backend(self, cgroup=V2, show=None, which="/usr/bin/systemctl"):
        be = linux.LinuxBackend()
        show = show if show is not None else done(0, f"MainPID={os.getpid()}\nTransient=no\n")
        patches = [mock.patch.object(linux.Path, "read_text", return_value=cgroup),
                   mock.patch.object(linux.shutil, "which", return_value=which),
                   mock.patch.object(linux.subprocess, "run", return_value=show)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return be

    def test_the_unit_whose_main_process_is_this_hub(self):
        be = self.backend()
        self.assertEqual(be.systemd_service(), {"unit": "ensemble.service", "transient": False})
        args = linux.subprocess.run.call_args[0][0]
        self.assertEqual(args[:4], ["systemctl", "--user", "show", "ensemble.service"])

    def test_read_once(self):
        be = self.backend()
        be.systemd_service()
        be.systemd_service()
        self.assertEqual(linux.subprocess.run.call_count, 1)

    def test_transient(self):
        be = self.backend(show=done(0, f"MainPID={os.getpid()}\nTransient=yes\n"))
        self.assertTrue(be.systemd_service()["transient"])

    def test_a_process_inside_the_unit_that_is_not_its_main_one(self):
        # A hub an agent started inside the live unit's cgroup.
        be = self.backend(show=done(0, "MainPID=1\nTransient=no\n"))
        self.assertIsNone(be.systemd_service())

    def test_started_by_hand_or_without_systemd(self):
        self.assertIsNone(self.backend(cgroup="0::/user.slice/x.scope\n").systemd_service())
        self.assertIsNone(self.backend(which=None).systemd_service())
        self.assertIsNone(self.backend(show=done(1)).systemd_service())


class Detached(TestCase):
    def test_under_a_unit_in_a_scope_of_its_own(self):
        be = linux.LinuxBackend()
        with mock.patch.object(be, "systemd_service", return_value={"unit": "u.service"}), \
             mock.patch.object(linux.shutil, "which", return_value="/usr/bin/systemd-run"):
            argv = be._outside_unit(["python3", "x.py"])
        self.assertEqual(argv[:5], ["/usr/bin/systemd-run", "--user", "--scope", "--quiet",
                                    "--collect"])
        self.assertTrue(argv[5].startswith("--unit=ensemble-detached-"))
        self.assertEqual(argv[6:], ["--", "python3", "x.py"])

    def test_by_hand_as_it_is(self):
        be = linux.LinuxBackend()
        with mock.patch.object(be, "systemd_service", return_value=None):
            self.assertEqual(be._outside_unit(["a"]), ["a"])

    def test_start_detached_uses_its_own_session(self):
        be = linux.LinuxBackend()
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(be, "systemd_service", return_value=None), \
             mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 42
            res = be.start_detached(["a"], Path(td) / "logs" / "x.log")
        self.assertEqual(res, {"started": True, "helperPid": 42})
        self.assertTrue(popen.call_args.kwargs["start_new_session"])


class SelfRestart(TestCase):
    def test_writes_the_plan_and_starts_the_helper(self):
        be = linux.LinuxBackend()
        with tempfile.TemporaryDirectory() as td:
            plan = {"repo": str(ROOT), "log": str(Path(td) / "logs" / "restart.log"),
                    "env": {"SECRET": "x"}}
            with mock.patch.object(be, "start_detached",
                                   return_value={"started": True, "helperPid": 7}) as start:
                self.assertEqual(be.self_restart(plan), {"started": True, "helperPid": 7})
            argv, log = start.call_args[0]
            self.assertEqual(argv[:2], [sys.executable, str(ROOT / "restart_helper.py")])
            path = Path(argv[2])
            self.assertEqual(path.parent, Path(td) / "_launch")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), plan)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(log, Path(plan["log"]))

    def test_a_helper_that_did_not_start_leaves_no_plan(self):
        be = linux.LinuxBackend()
        with tempfile.TemporaryDirectory() as td:
            plan = {"repo": str(ROOT), "log": str(Path(td) / "logs" / "restart.log"), "env": {}}
            with mock.patch.object(be, "start_detached",
                                   return_value={"started": False, "error": "no"}):
                self.assertFalse(be.self_restart(plan)["started"])
            self.assertEqual(list((Path(td) / "_launch").iterdir()), [])


class SelfUpdate(TestCase):
    def run_update(self, unit):
        be = linux.LinuxBackend()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "ensemble").write_text("#!/bin/bash\n", encoding="utf-8")
            with mock.patch.object(be, "systemd_service", return_value=unit), \
                 mock.patch.object(linux.shutil, "which",
                                   side_effect=lambda n: f"/usr/bin/{n}"), \
                 mock.patch.object(linux.subprocess, "Popen") as popen:
                res = be.self_update(d, port=8798, log_file=d / "hub.log")
            return res, popen.call_args, d

    def test_under_a_unit_it_names_the_unit_and_leaves_its_cgroup(self):
        res, call, d = self.run_update({"unit": "ensemble.service", "transient": False})
        self.assertTrue(res["started"])
        argv = call[0][0]
        self.assertEqual(argv[:3], ["/usr/bin/systemd-run", "--user", "--scope"])
        self.assertEqual(argv[-6:], ["--", "/usr/bin/bash", str(d / "ensemble"), "update",
                                     "--port", "8798"])
        self.assertEqual(call.kwargs["env"]["ENSEMBLE_SYSTEMD_UNIT"], "ensemble.service")
        self.assertTrue(call.kwargs["start_new_session"])

    def test_by_hand_the_script_stops_and_starts(self):
        with mock.patch.dict(os.environ, {"ENSEMBLE_SYSTEMD_UNIT": "stale.service"}):
            res, call, d = self.run_update(None)
        self.assertEqual(call[0][0], ["/usr/bin/bash", str(d / "ensemble"), "update",
                                      "--port", "8798"])
        self.assertNotIn("ENSEMBLE_SYSTEMD_UNIT", call.kwargs["env"])

    def test_no_script(self):
        with tempfile.TemporaryDirectory() as td:
            res = linux.LinuxBackend().self_update(Path(td))
        self.assertFalse(res["started"])


class HelperUnderSystemd(TestCase):
    """restart_helper._run with the preflight stubbed to pass."""

    def run_helper(self, unit, results):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            for prefix, res in results:
                if argv[:len(prefix)] == prefix:
                    return res
            return done(0)

        with tempfile.TemporaryDirectory(prefix="ensemble-linux-stop-") as td:
            root = Path(td)
            plan = {"python": "python3", "script": "dashboard.py", "repo": td,
                    "port": 8798, "preflightPort": 8799, "hubPid": 123,
                    "log": str(root / "restart.log"), "preflightLog": str(root / "pf.log"),
                    "args": ["--port", "8798"], "env": {"HOME": td, "INVOCATION_ID": "old"},
                    "keepAgents": True, "graceSeconds": 0, "systemd": unit,
                    "leaseId": "ours", "leasePath": str(root / "lease.json")}
            pf = mock.Mock()
            pf.poll.return_value = None
            port = mock.MagicMock()
            port.__enter__.return_value.connect_ex.return_value = 1

            def kill(pid, sig):
                if sig == 0:
                    raise ProcessLookupError()

            with mock.patch.object(restart_helper.subprocess, "Popen", return_value=pf) as spawn, \
                 mock.patch.object(restart_helper.subprocess, "run", side_effect=run), \
                 mock.patch.object(restart_helper.urllib.request, "urlopen",
                                   side_effect=lambda *a, **kw: io.BytesIO(b'{"ok":true}')), \
                 mock.patch.object(restart_helper.os, "kill", side_effect=kill) as killed, \
                 mock.patch.object(restart_helper.socket, "socket", return_value=port), \
                 mock.patch.object(restart_helper.time, "sleep"):
                rc = restart_helper._run(plan)
            log = (root / "restart.log").read_text(encoding="utf-8")
        return rc, calls, spawn, killed, log

    def test_stop_and_start_through_the_unit(self):
        rc, calls, spawn, killed, log = self.run_helper(
            {"unit": "ensemble.service", "transient": False}, [])
        self.assertEqual(rc, 0, log)
        self.assertIn(["systemctl", "--user", "stop", "ensemble.service"], calls)
        self.assertIn(["systemctl", "--user", "start", "ensemble.service"], calls)
        self.assertLess(calls.index(["systemctl", "--user", "stop", "ensemble.service"]),
                        calls.index(["systemctl", "--user", "start", "ensemble.service"]))
        self.assertEqual(spawn.call_count, 1)     # the preflight only
        self.assertNotIn(mock.call(123, restart_helper.signal.SIGTERM), killed.call_args_list)
        self.assertIn("systemctl --user stop ensemble.service: 0", log)
        self.assertIn("restart complete", log)

    def test_a_transient_unit_is_made_again_under_its_name(self):
        rc, calls, spawn, killed, log = self.run_helper(
            {"unit": "ens-test-8798.service", "transient": True},
            [(["systemctl", "--user", "start"], done(5, err="not found"))])
        self.assertEqual(rc, 0, log)
        made = next(c for c in calls if c[0] == "systemd-run")
        self.assertIn("--unit=ens-test-8798.service", made)
        self.assertTrue(any(a.startswith("--setenv=HOME=") for a in made))
        self.assertFalse(any(a.startswith("--setenv=INVOCATION_ID=") for a in made))
        hub = made[made.index("--") + 1:]
        self.assertEqual(hub[:2], ["python3", "dashboard.py"])
        self.assertIn("--port", hub)
        self.assertEqual(spawn.call_count, 1)
        self.assertIn("systemd-run --user --unit=ens-test-8798.service: 0", log)

    def test_systemd_fails_the_helper_starts_the_hub(self):
        rc, calls, spawn, killed, log = self.run_helper(
            {"unit": "ensemble.service", "transient": False},
            [(["systemctl", "--user", "stop"], done(1)),
             (["systemctl", "--user", "start"], done(1))])
        self.assertEqual(rc, 0, log)
        killed.assert_any_call(123, restart_helper.signal.SIGTERM)
        self.assertEqual(spawn.call_count, 2)     # preflight, then the hub itself
        self.assertIn("systemd start failed; starting hub directly", log)

    def test_without_a_unit_no_systemctl(self):
        rc, calls, spawn, killed, log = self.run_helper(None, [])
        self.assertEqual(rc, 0, log)
        self.assertFalse(any(c[0] in ("systemctl", "systemd-run") for c in calls))
        self.assertEqual(spawn.call_count, 2)


class PlanAndInstall(TestCase):
    def test_the_plan_names_the_unit(self):
        import dashboard
        with mock.patch.object(dashboard.BACKEND, "systemd_service",
                               return_value={"unit": "ensemble.service", "transient": False}):
            plan = dashboard.restart_plan()
        self.assertEqual(plan["systemd"], {"unit": "ensemble.service", "transient": False})

    def test_other_platforms_name_none(self):
        from backends.base import Backend
        self.assertIsNone(Backend().systemd_service())

    def test_install_runs_the_hub_from_the_clone(self):
        text = (ROOT / "install-systemd.sh").read_text(encoding="utf-8")
        self.assertIn('DIR="$(cd "$(dirname "$0")" && pwd)"', text)
        self.assertIn('ExecStart="$PYTHON" "$SCRIPT" --port $PORT', text)
        self.assertIn("WorkingDirectory=$DIR", text)
        self.assertIn('UNIT="ensemble.service"', text)
        self.assertIn("Restart=on-failure", text)

    def test_ensemble_update_restarts_through_the_named_unit(self):
        text = (ROOT / "ensemble").read_text(encoding="utf-8")
        body = text[text.index("cmd_update() {"):]
        self.assertIn('systemctl --user restart "$ENSEMBLE_SYSTEMD_UNIT"', body)
        self.assertLess(body.index("ENSEMBLE_SYSTEMD_UNIT"), body.index("launchctl print"))
