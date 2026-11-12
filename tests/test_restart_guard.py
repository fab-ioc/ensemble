"""The restart guard (ED-342): before a stop of the hub's systemd unit, a
project's long-running process in its cgroups is moved into a scope of its
own, or the stop is refused, naming it.

Every test runs on a fake process table and fake seams: none reads or moves
the machine's own cgroups. The live proof (a setsid'd sleep in a throwaway
ens-test-* unit, moved out and alive after the unit's stop) is in the task's
report.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import restart_guard as rg  # noqa: E402

HUB, HOST, MINE = 100, 200, 900
CG = rg.CGROUP_ROOT / "user.slice/app.slice/ensemble.service"
SCOPE = rg.CGROUP_ROOT / "user.slice/app.slice/ensemble-detached-abc.scope"


def proc(pid, ppid, comm, age=10.0, sid=None, tty=False, nohup=False, argv=None, **kw):
    return {"pid": pid, "ppid": ppid, "pgid": pid, "sid": pid if sid is None else sid,
            "tty": tty, "comm": comm, "age": age, "argv": argv or [comm], "nohup": nohup,
            "cwd": kw.get("cwd", ""), "room": kw.get("room", ""),
            "identity": kw.get("identity", ""), "pty": kw.get("pty", ""), "cgroup": ""}


def table():
    """The hub and its tool run; the PTY host with a claude (a shell and a
    tool under it) and a codex; a project's setsid'd java (with a child)
    left by an agent; a young `cmd &` and an old one; a nohup'd one."""
    return {p["pid"]: p for p in (
        proc(HUB, 1, "python3", age=5000, sid=1),
        proc(110, HUB, "git", age=1, sid=1),
        proc(HOST, 1, "python3", age=5000),
        proc(300, HOST, "claude", age=4000, tty=True),
        proc(310, 300, "bash", age=30),                 # Claude's tool shells are setsid'd
        proc(320, 310, "pytest", age=30, sid=310),
        proc(400, HOST, "codex", age=4000, tty=True),
        proc(500, 1, "java", age=3000, argv=["/usr/bin/java", "-jar", "trader.jar"],
             room="room-trade", identity="claude", pty="pty-7", cwd="/srv/trader"),
        proc(510, 500, "java", age=3000, sid=500),
        proc(600, 1, "sleep", age=5, sid=1),
        proc(700, 1, "node", age=500, sid=1),
        proc(800, 1, "watch", age=5, sid=1, nohup=True),
        proc(850, HOST, "helper", age=5, sid=HOST),     # the PTY host starts terminals only
        proc(MINE, 1, "python3", age=1, sid=1),
    )}


class Classify(unittest.TestCase):
    def setUp(self):
        self.v = rg.classify(table(), {HUB}, {HOST}, {MINE})

    def kind(self, pid):
        return self.v[pid]["kind"]

    def test_the_hub_its_host_and_the_agents_are_expected(self):
        for pid in (HUB, 110, HOST, 300, 310, 320, 400, MINE):
            self.assertEqual(self.kind(pid), "expected", pid)
        self.assertEqual(self.v[320]["root"], 300)      # under its agent
        self.assertIn("a terminal of the PTY host (claude)", self.v[300]["why"])

    def test_a_detached_or_old_tree_is_foreign(self):
        self.assertEqual(self.kind(500), "foreign")
        self.assertIn("setsid", self.v[500]["why"])
        self.assertEqual((self.kind(510), self.v[510]["root"]), ("foreign", 500))
        self.assertEqual(self.kind(700), "foreign")
        self.assertIn("running for 8 min", self.v[700]["why"])
        self.assertEqual(self.kind(800), "foreign")
        self.assertIn("nohup", self.v[800]["why"])
        self.assertEqual(self.kind(850), "foreign")
        self.assertIn("without a terminal", self.v[850]["why"])

    def test_a_young_attached_one_is_left_alone(self):
        self.assertEqual(self.kind(600), "recent")

    def test_a_detached_app_under_a_live_tool_shell_is_foreign(self):
        # Review 1: `setsid nohup java &` while the agent's tool shell still
        # runs; `nohup node &` without setsid. The shell, its tool's own
        # children and the agent CLI's MCP servers stay expected.
        procs = table()
        procs.update({p["pid"]: p for p in (
            proc(330, 310, "java", age=3600, nohup=True),
            proc(331, 330, "java", age=3600, sid=330),
            proc(340, 310, "node", age=3600, sid=310, nohup=True),
            proc(350, 300, "mcp", age=4000, sid=300, nohup=True),
            proc(360, 320, "sleep", age=600, sid=310),
        )})
        v = rg.classify(procs, {HUB}, {HOST}, {MINE})
        self.assertEqual((v[330]["kind"], v[330]["root"]), ("foreign", 330))
        self.assertIn("setsid", v[330]["why"])
        self.assertEqual((v[331]["kind"], v[331]["root"]), ("foreign", 330))
        self.assertEqual((v[340]["kind"], v[340]["root"]), ("foreign", 340))
        self.assertIn("nohup", v[340]["why"])
        for pid in (300, 310, 320, 350, 360):
            self.assertEqual(v[pid]["kind"], "expected", pid)

    def test_a_setsid_child_of_the_hub_stays_expected(self):
        procs = table()
        procs[120] = proc(120, 110, "chrome", age=3600, nohup=True)
        self.assertEqual(rg.classify(procs, {HUB}, {HOST}, {MINE})[120]["kind"], "expected")

    def test_a_child_of_a_moved_process_follows_it(self):
        # A child forked during the move, its parent already out of the table.
        v = rg.classify({601: proc(601, 600, "sleep", age=0, sid=1)}, {HUB}, {HOST}, {MINE},
                        moved={600})
        self.assertEqual(v[601]["kind"], "foreign")

    def test_a_loop_in_the_table_ends(self):
        procs = {1: proc(1, 2, "a", sid=0), 2: proc(2, 1, "b", sid=0)}
        v = rg.classify(procs, set(), set(), set())
        self.assertEqual(set(v), {1, 2})

    def test_describe_names_who_started_it(self):
        text = rg.describe(table()[500], "ensemble.service",
                           room_name=lambda rid: "ED-77 Trading bot" if rid == "room-trade" else "")
        self.assertIn("pid 500 `/usr/bin/java -jar trader.jar`", text)
        self.assertIn("started by ED-77 Trading bot, claude, terminal pty-7", text)
        self.assertIn("in /srv/trader", text)
        self.assertIn("running 50 min", text)
        self.assertIn("in ensemble.service", text)
        self.assertIn("who started it is not known", rg.describe(table()[700]))


class Protect(unittest.TestCase):
    """Guard.protect on the fake table with fake seams."""

    def guard(self, start_errors=None, stays=(), force=False):
        procs = table()
        g = rg.Guard(units=["ensemble.service"], hub_pids=[HUB], state_dirs=["x"],
                     log_path=self.log)
        cgroups = {pid: str(CG.relative_to(rg.CGROUP_ROOT)) for pid in procs}
        g.unit_cgroups = lambda units, detached: {
            "ensemble.service": {"cgroup": CG, "mainPid": HUB},
            "ensemble-detached-abc.scope": {"cgroup": SCOPE, "mainPid": 0}}
        g.cgroup_pids = lambda cg: [p for p in procs if p not in (HOST, 300, 310, 320, 400, 850)] \
            if cg == CG else [HOST, 300, 310, 320, 400, 850]
        # Gone from the hub's cgroups once moved: not listed again.
        g.read_proc = lambda pid: procs.get(pid) if cgroups.get(pid, "").startswith(
            "user.slice/app.slice/ensemble") else None
        g.ancestry = lambda: {MINE}
        g.hosts = lambda dirs: {HOST}
        self.started = []

        def start(name, pids, description):
            self.started.append((name, pids))
            err = (start_errors or {}).get(pids[0], "")
            if not err:
                for p in pids:
                    if p not in stays:
                        cgroups[p] = f"user.slice/app.slice/{name}"
            return err
        g.start = start
        g.where = lambda pid: cgroups.get(pid)
        g.sleep = lambda s: None
        return g.protect(force=force)

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.log = Path(td.name) / "restart.log"

    def test_every_foreign_tree_moves_into_a_scope_of_its_own(self):
        with mock.patch.object(rg, "MOVE_WAIT_S", 0):
            res = self.guard()
        self.assertTrue(res["ok"], res)
        self.assertIn(("kept-java-500.scope", [500, 510]), self.started)
        self.assertEqual(sorted(m["pid"] for m in res["moved"]), [500, 700, 800, 850])
        self.assertFalse(any(300 in pids or HUB in pids for _, pids in self.started))
        self.assertIn("Moved out before the restart", res["message"])
        self.assertIn("kept-java-500.scope", res["message"])
        self.assertEqual(len(res["recent"]), 1)          # the young sleep, left in place
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("[restart] guard: found pid 500", log)
        self.assertIn("moved pid 500 and 1 child(ren) from ensemble.service into "
                      "kept-java-500.scope", log)

    def test_one_that_cannot_move_refuses_naming_it(self):
        with mock.patch.object(rg, "MOVE_WAIT_S", 0):
            res = self.guard(start_errors={500: "busctl: Access denied"})
        self.assertFalse(res["ok"])
        self.assertEqual([f["pid"] for f in res["failed"]], [500])
        self.assertIn("Refused: the restart would stop these processes", res["message"])
        self.assertIn("pid 500 `/usr/bin/java -jar trader.jar`", res["message"])
        self.assertIn("busctl: Access denied", res["message"])
        self.assertIn("systemd-run --user --unit=", res["message"])
        self.assertIn("guard: refused: the restart would stop pid 500",
                      self.log.read_text(encoding="utf-8"))

    def test_one_still_in_place_after_the_call_refuses_too(self):
        with mock.patch.object(rg, "MOVE_WAIT_S", 0):
            res = self.guard(stays={510})
        self.assertFalse(res["ok"])
        self.assertIn("still in place after the move: pid 510", res["failed"][0]["error"])

    def test_force_goes_ahead_and_says_so(self):
        with mock.patch.object(rg, "MOVE_WAIT_S", 0):
            res = self.guard(start_errors={500: "no"}, force=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["forced"])
        self.assertIn("Forced: the restart goes ahead and stops these", res["message"])

    def test_nothing_foreign_nothing_said(self):
        res = rg.message({"moved": [], "failed": []})
        self.assertEqual(res, "")


class Platform(unittest.TestCase):
    def test_off_linux_a_no_op(self):
        with mock.patch.object(rg.sys, "platform", "darwin"), \
             mock.patch.object(rg, "Guard") as guard:
            res = rg.protect()
        guard.assert_not_called()
        self.assertEqual((res["ok"], res["supported"], res["message"]), (True, False, ""))

    def test_without_systemctl_a_no_op(self):
        with mock.patch.object(rg.sys, "platform", "linux"), \
             mock.patch.object(rg.shutil, "which", return_value=None), \
             mock.patch.object(rg, "Guard") as guard:
            self.assertTrue(rg.protect()["ok"])
        guard.assert_not_called()

    def test_a_guard_that_breaks_refuses(self):
        with mock.patch.object(rg, "supported", return_value=True), \
             mock.patch.object(rg.Guard, "protect", side_effect=RuntimeError("boom")):
            res = rg.protect(log_path=Path(os.devnull))
            forced = rg.protect(log_path=Path(os.devnull), force=True)
        self.assertFalse(res["ok"])
        self.assertIn("could not tell what the restart would stop (RuntimeError: boom)", res["message"])
        self.assertTrue(forced["ok"])

    def test_a_scan_that_cannot_read_refuses(self):
        # Review 1: a user manager that does not answer is not "nothing runs".
        err = rg.ScanError("systemctl --user show: Failed to connect to bus")
        with mock.patch.object(rg, "supported", return_value=True), \
             mock.patch.object(rg, "unit_cgroups", side_effect=err):
            res = rg.protect(log_path=Path(os.devnull))
            forced = rg.protect(log_path=Path(os.devnull), force=True)
        self.assertFalse(res["ok"])
        self.assertIn("could not tell what the restart would stop (systemctl --user show: "
                      "Failed to connect to bus); refused.", res["message"])
        self.assertTrue((forced["ok"], forced["forced"]) == (True, True))

    def test_the_cli_exits_3_on_a_refusal(self):
        refused = {"ok": False, "message": "Refused: …"}
        with mock.patch.object(rg, "protect", return_value=refused) as p, \
             mock.patch("sys.stderr"):
            self.assertEqual(rg.main(["--unit", "ens-test-x.service", "--no-detached",
                                      "--force"]), rg.REFUSED_EXIT)
        kw = p.call_args.kwargs
        self.assertEqual((kw["units"], kw["detached"], kw["force"]),
                         (["ens-test-x.service"], False, True))
        with mock.patch.object(rg, "protect", return_value={"ok": True, "message": ""}):
            self.assertEqual(rg.main([]), 0)


class Machine(unittest.TestCase):
    """The readers, on this process and files of the test's own."""

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_read_proc_reads_this_process(self):
        me = rg.read_proc(os.getpid())
        self.assertEqual((me["pid"], me["ppid"]), (os.getpid(), os.getppid()))
        self.assertIsNone(rg.read_proc(2 ** 22 + 12345))

    def test_pty_host_pids_from_the_state_dir(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "pty-host.json").write_text(json.dumps({"pid": 4321}), encoding="utf-8")
            self.assertEqual(rg.pty_host_pids([td, "/nonexistent"]), {4321})

    def test_unit_cgroups_reads_systemctl(self):
        outs = {"list-units": "ensemble-detached-abc.scope loaded active running x\n",
                "show": "Id=ensemble.service\nControlGroup=/u/ensemble.service\nMainPID=77\n\n"
                        "Id=ensemble-detached-abc.scope\nControlGroup=/u/d.scope\nMainPID=0\n"}
        with mock.patch.object(rg, "_systemctl", side_effect=lambda *a, **kw: outs[a[0]]) as sc:
            got = rg.unit_cgroups(["ensemble.service"])
            self.assertEqual(got["ensemble.service"],
                             {"cgroup": rg.CGROUP_ROOT / "u/ensemble.service", "mainPid": 77})
            self.assertIn("ensemble-detached-abc.scope", got)
            sc.reset_mock()
            rg.unit_cgroups(["ensemble.service"], detached=False)
            self.assertEqual(sc.call_args_list[0].args[0], "show")


    def test_a_failed_systemctl_raises(self):
        done = mock.Mock(returncode=1, stdout="", stderr="Failed to connect to bus")
        with mock.patch.object(rg.subprocess, "run", return_value=done):
            with self.assertRaisesRegex(rg.ScanError, "Failed to connect to bus"):
                rg.unit_cgroups(["ensemble.service"])
        with mock.patch.object(rg.subprocess, "run", side_effect=rg.subprocess.TimeoutExpired("x", 20)):
            with self.assertRaises(rg.ScanError):
                rg.unit_cgroups(["ensemble.service"], detached=False)

    def test_a_unit_that_is_not_running_has_no_cgroup(self):
        outs = {"show": "Id=ensemble.service\nControlGroup=\nMainPID=0\n"}
        with mock.patch.object(rg, "_systemctl", side_effect=lambda *a, **kw: outs[a[0]]):
            self.assertEqual(rg.unit_cgroups(["ensemble.service"], detached=False), {})

    def test_cgroup_pids_gone_is_empty_unreadable_raises(self):
        with tempfile.TemporaryDirectory() as td:
            cg = Path(td) / "u.service"
            self.assertEqual(rg.cgroup_pids(cg), [])           # ended since it was listed
            (cg / "sub").mkdir(parents=True)
            (cg / "cgroup.procs").write_text("11\n12\n")
            (cg / "sub" / "cgroup.procs").write_text("13\n")
            self.assertEqual(sorted(rg.cgroup_pids(cg)), [11, 12, 13])
            (cg / "sub" / "cgroup.procs").unlink()
            (cg / "sub" / "cgroup.procs").mkdir()             # unreadable: not a file
            with self.assertRaises(rg.ScanError):
                rg.cgroup_pids(cg)


class HubWiring(unittest.TestCase):
    """dashboard.trigger_restart and restart_drain run the guard first."""

    def setUp(self):
        import dashboard
        self.d = dashboard
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        env = {k: v for k, v in os.environ.items() if k != "ENSEMBLE_RESTART_DRY_RUN"}
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", Path(td.name)),
                  mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[]),
                  mock.patch.object(dashboard.BACKEND, "systemd_service",
                                    return_value={"unit": "ensemble.service"})):
            p.start()
            self.addCleanup(p.stop)

    def test_no_unit_no_guard(self):
        with mock.patch.object(self.d.BACKEND, "systemd_service", return_value=None), \
             mock.patch.object(self.d.restart_guard, "protect") as p:
            self.assertIsNone(self.d.guard_foreign())
        p.assert_not_called()

    def test_a_refusal_starts_nothing_and_holds_no_lease(self):
        refused = {"ok": False, "message": "Refused: the restart would stop pid 5.",
                   "failed": [{"pid": 5, "text": "pid 5 `java`", "error": "no"}]}
        with mock.patch.object(self.d.restart_guard, "protect", return_value=refused), \
             mock.patch.object(self.d.BACKEND, "self_restart") as real:
            res = self.d.trigger_restart()
        real.assert_not_called()
        self.assertEqual((res["status"], res["started"], res["refused"]), (409, False, True))
        self.assertIn("pid 5", res["error"])
        self.assertIn("stopForeign: true", res["error"])
        self.assertFalse((self.d.DASHBOARD_DIR / "restart.lease").exists())

    def test_moved_ones_are_named_and_force_reaches_the_helper(self):
        moved = {"ok": True, "message": "Moved out before the restart: pid 5 → kept-java-5.scope.",
                 "failed": []}
        with mock.patch.object(self.d.restart_guard, "protect", return_value=moved) as p, \
             mock.patch.object(self.d.BACKEND, "self_restart",
                               return_value={"started": True}) as real:
            res = self.d.trigger_restart(stop_foreign=True)
        self.assertEqual(res["status"], 202)
        self.assertIn("kept-java-5.scope", res["message"])
        self.assertTrue(p.call_args.kwargs["force"])
        self.assertEqual(p.call_args.kwargs["units"], ["ensemble.service"])
        self.assertTrue(real.call_args.args[0]["stopForeign"])

    def test_a_drain_is_refused_before_anyone_is_told(self):
        rd = self.d.restart_drain
        rd.reset()
        self.addCleanup(rd.reset)
        refused = {"ok": False, "message": "Refused: x.", "failed": []}
        with mock.patch.object(self.d, "pty_host_compatible", return_value=False), \
             mock.patch.object(self.d.restart_guard, "protect", return_value=refused):
            res = rd.request_restart("merged #1", {"label": "the page"})
        self.assertEqual((res["status"], res.get("refused")), (409, True))
        self.assertIsNone(rd.current())

    def test_the_drain_keeps_stop_foreign_for_its_restart(self):
        rd = self.d.restart_drain
        rd.reset()
        self.addCleanup(rd.reset)
        ok = {"ok": True, "message": "", "failed": []}
        with mock.patch.object(self.d, "pty_host_compatible", return_value=False), \
             mock.patch.object(self.d, "load_settings", return_value={}), \
             mock.patch.object(self.d.restart_guard, "protect", return_value=ok):
            res = rd.request_restart("merged #1", {"label": "the page"}, stop_foreign=True)
        self.assertEqual(res["status"], 202)
        self.assertTrue(rd.current()["stopForeign"])

    def _restarting_drain(self, action="restart"):
        rd = self.d.restart_drain
        rd.reset()
        self.addCleanup(rd.reset)
        ok = {"ok": True, "message": "", "failed": []}
        with mock.patch.object(self.d, "pty_host_compatible", return_value=False), \
             mock.patch.object(self.d, "source_update_keeps_host", return_value=False), \
             mock.patch.object(self.d.app_version, "packaged", return_value=False), \
             mock.patch.object(self.d, "load_settings", return_value={}), \
             mock.patch.object(self.d.restart_guard, "protect", return_value=ok):
            self.assertEqual(rd.request_restart("merged #1", {"label": "the page"}, action)["status"], 202)
        with rd.LOCK:
            st = rd.current()
            st.update(state="restarting", hubPid=os.getpid())
            rd.save(st)
        return rd

    def test_a_source_update_is_refused_before_the_checkout_changes(self):
        # Review 1: the fast hub-only update fetched and reset first.
        rd = self.d.restart_drain
        rd.reset()
        self.addCleanup(rd.reset)
        refused = {"ok": False, "message": "Refused: x.", "failed": []}
        with mock.patch.object(self.d.app_version, "packaged", return_value=False), \
             mock.patch.object(self.d, "source_update_keeps_host", return_value=True) as keeps, \
             mock.patch.object(self.d, "pull_checkout") as pull, \
             mock.patch.object(self.d.restart_guard, "protect", return_value=refused):
            res = rd.request_restart("update", {"label": "the page"}, "update")
        self.assertEqual((res["status"], res.get("refused")), (409, True))
        pull.assert_not_called()
        keeps.assert_not_called()
        self.assertIsNone(rd.current())

    def test_a_drained_update_is_refused_before_the_checkout_changes(self):
        rd = self._restarting_drain("update")
        refused = {"ok": False, "message": "Refused: x.", "failed": []}
        with mock.patch.object(self.d.app_version, "packaged", return_value=False), \
             mock.patch.object(self.d, "pull_checkout") as pull, \
             mock.patch.object(self.d.restart_guard, "protect", return_value=refused):
            rd._launch()
        pull.assert_not_called()
        self.assertFalse(rd.holding())
        self.assertIn("Refused: x.", rd.current().get("error") or rd.current().get("outcome", ""))

    def test_a_refused_restart_now_lets_the_drain_go(self):
        # Review 1: it stayed held until a later tick.
        rd = self._restarting_drain()
        refused = {"ok": False, "message": "Refused: x.", "failed": []}
        with mock.patch.object(self.d.restart_guard, "protect", return_value=refused), \
             mock.patch.object(self.d.BACKEND, "self_restart") as real:
            res = rd.restart_now("")
        real.assert_not_called()
        self.assertTrue(res.get("refused"))
        self.assertFalse(rd.holding())

    def test_a_refused_helper_is_why_the_drain_failed(self):
        log = self.d.DASHBOARD_DIR / "logs" / "restart.log"
        log.parent.mkdir(parents=True)
        log.write_text("t [restart] === plain restart\n"
                       "t [restart] guard: refused: the restart would stop pid 5 `java`\n"
                       "t [restart] guard: refused; hub left running\n", encoding="utf-8")
        self.assertEqual(self.d.restart_drain._preflight_why(),
                         "the restart was refused: the restart would stop pid 5 `java`")


if __name__ == "__main__":
    unittest.main()
