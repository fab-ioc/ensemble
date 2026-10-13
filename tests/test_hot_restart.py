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

            def post(port, path, body):
                calls.append(path)
                if path == "/api/restart/snapshot":
                    raise OSError("snapshot unavailable")
                return {"ok": True}

            state = io.BytesIO(b'{"state":"restarting","external":true}')
            with mock.patch.object(app_update, "_post", side_effect=post), \
                 mock.patch.object(app_update.urllib.request, "urlopen", return_value=state), \
                 mock.patch.object(helper, "stop_hub") as stop:
                with self.assertRaisesRegex(RuntimeError, "snapshot unavailable"):
                    helper._swap_and_start(root, root, [], root, [], root, root)
            self.assertEqual(calls, ["/api/restart/external-drain",
                                     "/api/restart/snapshot", "/api/restart/external-cancel"])
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
            start.assert_called_once_with("")
