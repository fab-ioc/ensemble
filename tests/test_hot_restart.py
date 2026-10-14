import tempfile
import json
import io
from pathlib import Path
from unittest import TestCase, mock

import dashboard
import restart_drain
import restart_helper
import app_update


class HotRestartTest(TestCase):
    def test_windows_restart_force_stop_precedes_hub_start(self):
        script = (Path(__file__).resolve().parent.parent / "restart-hub.ps1").read_text(encoding="utf-8")
        force = script.index("'--force-stop'")
        hub = script.index("$up = $false", force)
        self.assertLess(force, hub)
        self.assertNotIn("exit 1", script[force:hub])

    def test_session_page_new_lines_use_crlf(self):
        page = (Path(__file__).resolve().parent.parent / "session.html").read_bytes()
        for token in (b"/static/hub-resilience.js", b"hub:online"):
            at = page.index(token)
            self.assertTrue(page[at:page.index(b"\n", at) + 1].endswith(b"\r\n"))

    def test_mac_helper_forces_old_host_then_starts_new_hub(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-mac-stop-") as td:
            root = Path(td)
            plan = {"python": "python", "script": "dashboard.py", "repo": td,
                    "port": 8798, "preflightPort": 8799, "hubPid": 123,
                    "log": str(root / "restart.log"), "preflightLog": str(root / "pf.log"),
                    "args": [], "env": {}, "keepAgents": False, "graceSeconds": 0,
                    "leaseId": "ours", "leasePath": str(root / "lease.json"),
                    "ptyHostVersion": "old"}
            pf = mock.Mock()
            pf.poll.return_value = None     # running
            port = mock.MagicMock()
            port.__enter__.return_value.connect_ex.return_value = 1
            def kill(pid, sig):
                if sig == 0:
                    raise ProcessLookupError()
            with mock.patch.object(restart_helper.subprocess, "Popen", return_value=pf) as spawn, \
                 mock.patch.object(restart_helper.urllib.request, "urlopen",
                                   side_effect=lambda *a, **kw: io.BytesIO(b'{"ok":true}')), \
                 mock.patch.object(restart_helper.os, "kill", side_effect=kill), \
                 mock.patch.object(restart_helper.socket, "socket", return_value=port), \
                 mock.patch.object(restart_helper.ptyhost, "descriptor",
                                   return_value={"pid": 456, "port": 789}), \
                 mock.patch.object(restart_helper.ptyhost, "stop", return_value=False) as stop, \
                 mock.patch.object(restart_helper.ptyhost, "force_stop", return_value=False) as force, \
                 mock.patch.object(restart_helper.time, "sleep"):
                self.assertEqual(restart_helper._run(plan), 0)
            self.assertEqual(spawn.call_count, 2)  # preflight and replacement hub
            stop.assert_called_once_with(root, expected={"pid": 456, "port": 789})
            force.assert_called_once_with({"pid": 456, "port": 789})

    def test_helper_preflight_fails_when_a_start_up_step_kills_the_hub(self):
        # The hub listens before its steps (ED-227): /api/platform answers,
        # then a step raises and the process exits. Not a pass.
        with tempfile.TemporaryDirectory(prefix="ensemble-pf-starting-") as td:
            root = Path(td)
            plan = {"python": "python", "script": "dashboard.py", "repo": td,
                    "port": 8798, "preflightPort": 8799, "hubPid": 123,
                    "log": str(root / "restart.log"), "preflightLog": str(root / "pf.log"),
                    "args": [], "env": {}, "keepAgents": False, "graceSeconds": 0,
                    "leaseId": "ours", "leasePath": str(root / "lease.json"),
                    "ptyHostVersion": "old"}
            pf = mock.Mock()
            pf.poll.side_effect = [None, None, 1]
            with mock.patch.object(restart_helper.subprocess, "Popen", return_value=pf) as spawn, \
                 mock.patch.object(restart_helper.urllib.request, "urlopen",
                                   side_effect=lambda *a, **kw: io.BytesIO(b'{"phase":"starting"}')), \
                 mock.patch.object(restart_helper.time, "sleep"):
                self.assertEqual(restart_helper._run(plan), 1)
            self.assertEqual(spawn.call_count, 1)  # the hub was left running
            self.assertIn("preflight failed", (root / "restart.log").read_text(encoding="utf-8"))

    def test_update_keeps_old_host_when_hub_stop_fails(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-update-stop-") as td:
            root = Path(td)
            helper = app_update.Helper({"port": 8798, "ptyHostVersion": "old",
                                        "leaseId": "ours", "leasePath": str(root / "lease.json"),
                                        "log": str(root / "update.log")})
            helper.new_host_version = "new"
            helper.external_drain = True
            calls = []
            with mock.patch.object(app_update, "_post", side_effect=lambda p, e, b, **kw: calls.append(e) or {"ok": True, "rooms": []}), \
                 mock.patch.object(app_update.urllib.request, "urlopen",
                                   return_value=io.BytesIO(b'{"state":"restarting","external":true}')), \
                 mock.patch("backends.ptyhost.descriptor", return_value={"pid": 123, "port": 456}), \
                 mock.patch.object(helper, "stop_hub", return_value=(False, False)), \
                 mock.patch.object(helper, "stop_pty_host") as stop_host, \
                 mock.patch.object(helper, "ensure_serving"), \
                 mock.patch.object(helper, "restore_rooms"), \
                 mock.patch.object(helper, "record", return_value={"ok": False}):
                self.assertFalse(helper._swap_and_start(root, root, [], root, [], root, root)["ok"])
            stop_host.assert_not_called()
            self.assertEqual(helper.pinned_host, {"pid": 123, "port": 456})
            self.assertIn("/api/restart/external-cancel", calls)

    def test_update_forces_old_host_then_starts_new_hub(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-update-host-") as td:
            root = Path(td)
            helper = app_update.Helper({"port": 8798, "ptyHostVersion": "old", "toVersion": "new",
                                        "leaseId": "ours", "leasePath": str(root / "lease.json"),
                                        "log": str(root / "update.log")})
            helper.new_host_version = "new"
            with mock.patch.object(app_update, "_post", return_value={"ok": True, "rooms": []}), \
                 mock.patch.object(app_update.urllib.request, "urlopen",
                                   return_value=io.BytesIO(b'{"state":"restarting","external":true}')), \
                 mock.patch("backends.ptyhost.descriptor", return_value={"pid": 123, "port": 456}), \
                 mock.patch.object(helper, "stop_hub", return_value=(False, True)), \
                 mock.patch.object(helper, "stop_pty_host", return_value=False), \
                 mock.patch("backends.ptyhost.force_stop", return_value=False) as force, \
                 mock.patch.object(helper, "_try_start", return_value=True) as start, \
                 mock.patch.object(app_update, "wait_served", return_value={"version": "new"}), \
                 mock.patch.object(app_update, "decide", return_value="keep"), \
                 mock.patch.object(helper, "restore_rooms"), \
                 mock.patch.object(helper, "record", side_effect=lambda **kw: kw), \
                 mock.patch.object(app_update, "swap_in") as swap:
                result = helper._swap_and_start(root, root, [], root, [], root, root)
            self.assertEqual(result["stage"], "done")
            swap.assert_called_once()
            force.assert_called_once_with({"pid": 123, "port": 456})
            start.assert_called_once()

    def test_mac_helper_keeps_bind_and_original_flags(self):
        plan = {"python": "python", "script": "dashboard.py", "port": 8765,
                "log": "hub.log", "preflightLog": "preflight.log",
                "args": ["--bind", "tailscale", "--port", "8765", "--background",
                         "--log=old.log"]}
        self.assertEqual(restart_helper.hub_args(plan, 8798),
                         ["python", "dashboard.py", "--bind", "tailscale",
                          "--port", "8798", "--log", "preflight.log", "--background"])

    def test_mac_helper_releases_lease_after_failed_preflight(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-helper-") as td:
            folder = Path(td)
            lease = folder / "lease.json"
            lease.write_text(json.dumps({"id": "ours"}), encoding="utf-8")
            plan = folder / "plan.json"
            plan.write_text(json.dumps({"leasePath": str(lease), "leaseId": "ours"}),
                            encoding="utf-8")
            with mock.patch.object(restart_helper, "_run", return_value=1):
                self.assertEqual(restart_helper.run(str(plan)), 1)
            self.assertFalse(lease.exists())

    def test_update_snapshot_failure_cancels_drain_before_hub_stop(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-update-drain-") as td:
            root = Path(td)
            helper = app_update.Helper({"port": 8798, "ptyHostVersion": "old",
                                        "leaseId": "ours", "log": str(root / "update.log")})
            helper.new_host_version = "new"
            calls = []

            def post(port, path, body, **kwargs):
                calls.append(path)
                if path == "/api/restart/snapshot":
                    raise OSError("snapshot unavailable")
                return {"ok": True}

            state = io.BytesIO(b'{"state":"restarting","external":true}')
            with mock.patch.object(app_update, "_post", side_effect=post), \
                 mock.patch.object(app_update.urllib.request, "urlopen", return_value=state), \
                 mock.patch.object(helper, "stop_hub") as stop, \
                 mock.patch.object(app_update.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
                    helper._swap_and_start(root, root, [], root, [], root, root)
            self.assertEqual(calls, ["/api/restart/external-drain"] +
                             ["/api/restart/snapshot"] * 3 +
                             ["/api/restart/external-cancel"])
            stop.assert_not_called()

    def test_matching_host_skips_drain_and_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-hot-plan-") as td, \
             mock.patch.object(dashboard, "DASHBOARD_DIR", Path(td)), \
             mock.patch.object(dashboard, "pty_host_compatible", return_value=True), \
             mock.patch.object(dashboard, "HUB_PORT", 8798), \
             mock.patch.object(dashboard, "take_restart_snapshot") as snapshot, \
             mock.patch.object(dashboard.BACKEND, "self_restart", return_value={"started": True}):
            result = dashboard.trigger_restart()
            self.assertTrue(result["keepAgents"])
            self.assertEqual(result["resumeRooms"], [])
            snapshot.assert_not_called()

    def test_safe_tool_request_uses_hot_mode_without_a_drain_record(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-hot-request-") as td, \
             mock.patch.object(dashboard, "DASHBOARD_DIR", Path(td)), \
             mock.patch.object(dashboard, "pty_host_compatible", return_value=True), \
             mock.patch.object(dashboard, "trigger_restart", return_value={"started": True,
                 "status": 202, "keepAgents": True}) as start:
            restart_drain.reset()
            result = restart_drain.request_restart("test", {"label": "the dashboard"})
            self.assertTrue(result["keepAgents"])
            self.assertIsNone(restart_drain.current())
            start.assert_called_once_with("", identity="")


class RestartHelperWatchTest(TestCase):
    """ED-227: a helper that ends with its lease still held stopped before the
    hub. The hub says so, lets go of the lease and tells whoever asked."""

    def run_watch(self, alive, lease_after=None):
        with tempfile.TemporaryDirectory(prefix="ensemble-helper-watch-") as td:
            state = Path(td)
            (state / "logs").mkdir()
            (state / "logs" / "restart.log").write_text(
                "2026-10-14T08:36:19 [restart] preflight passed on port 63571\n"
                "2026-10-14T08:36:20 [restart] PTY host attached: pid 1, version x\n", encoding="utf-8")
            (state / "restart.lease").write_text(json.dumps({"id": "ours", "at": 0}), encoding="utf-8")
            logged, woken = [], []
            room = {"id": "room-po", "participants": [{"identity": "claude"}]}
            with mock.patch.object(dashboard, "DASHBOARD_DIR", state), \
                 mock.patch.object(dashboard, "RESTART_HELPER_POLL_S", 0), \
                 mock.patch.object(dashboard.BACKEND, "process_alive", side_effect=alive), \
                 mock.patch.object(dashboard, "_restart_log", side_effect=logged.append), \
                 mock.patch.object(dashboard.chatroom, "get_room", return_value=room), \
                 mock.patch.object(restart_drain, "release", side_effect=woken.extend):
                if lease_after:
                    lease_after(state)
                dashboard._watch_restart_helper(4242, "ours", "room-po")
                return logged, woken, (state / "restart.lease").exists()

    def test_a_helper_gone_mid_restart_is_said_and_lets_go(self):
        logged, woken, lease = self.run_watch(alive=[True, False])
        self.assertFalse(lease)
        self.assertEqual(len(logged), 1)
        self.assertIn("HELPER GONE", logged[0])
        self.assertIn("preflight passed on port 63571", logged[0])
        self.assertEqual([(w["roomId"], w["identity"]) for w in woken], [("room-po", "claude")])
        self.assertIn("did not happen", woken[0]["text"])

    def test_a_helper_that_finished_is_left_alone(self):
        # It dropped (or handed on) its lease: nothing to say.
        logged, woken, _ = self.run_watch(
            alive=[False], lease_after=lambda st: (st / "restart.lease").unlink())
        self.assertEqual((logged, woken), ([], []))
