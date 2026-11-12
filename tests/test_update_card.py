"""#330: the hub's side of the page's Update now card.

* no restart grace when the page itself pressed Update now (a source update,
  hub-only or after a drain); a PO's update keeps it;
* a source update's restart says where it stands in /api/update-status
  (``restart``: checking, restarting, failed with why), the page's steps;
* a fetch that fails answers 500 with why and the log.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import restart_drain  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = {k: v for k, v in os.environ.items()
               if k not in ("ENSEMBLE_RESTART_DRY_RUN", "ENSEMBLE_UPDATE_DRY_RUN", "ENSEMBLE_RESTART_GRACE")}
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir),
                  mock.patch.dict(os.environ, env, clear=True),
                  mock.patch.object(dashboard, "pty_host_compatible", return_value=False),
                  mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[]),
                  mock.patch.object(dashboard, "take_restart_snapshot", return_value={"rooms": []}),
                  mock.patch.dict(dashboard._SRC_UPDATE, {"stage": "idle"}, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def stage(self) -> dict:
        return dashboard.update_status()["restart"]


class Grace(Base):
    def test_a_given_grace_wins_over_the_setting(self):
        with mock.patch.dict(os.environ, {"ENSEMBLE_RESTART_GRACE": "30"}):
            self.assertEqual(dashboard.restart_plan(grace=0)["graceSeconds"], 0)
            self.assertEqual(dashboard.restart_plan()["graceSeconds"], 30)
        self.assertEqual(dashboard.restart_plan()["graceSeconds"], dashboard.RESTART_GRACE_S)
        self.assertEqual(dashboard.restart_plan(grace=999)["graceSeconds"], 300)

    def test_trigger_passes_it_to_the_helper(self):
        with mock.patch.object(dashboard.BACKEND, "self_restart", return_value={"started": True}) as real:
            res = dashboard.trigger_restart(purpose="update", grace=0)
        self.assertEqual(real.call_args[0][0]["graceSeconds"], 0)
        self.assertEqual(res["graceSeconds"], 0)

    def hub_only(self, by):
        with mock.patch.object(restart_drain, "holding", return_value=False), \
             mock.patch.object(dashboard.app_version, "packaged", return_value=False), \
             mock.patch.object(dashboard, "source_update_keeps_host", return_value=True), \
             mock.patch.object(dashboard, "pull_checkout", return_value=""), \
             mock.patch.object(dashboard, "trigger_restart", return_value={"started": True}) as tr:
            restart_drain.request_restart("an update", by, "update")
        return tr.call_args.kwargs

    def test_the_page_update_has_no_grace(self):
        self.assertEqual(self.hub_only({"label": "the page"}).get("grace"), 0)

    def test_a_po_update_keeps_the_grace(self):
        kw = self.hub_only({"roomId": "room-po", "identity": "claude", "label": "PO"})
        self.assertNotIn("grace", kw)

    def launch(self, requested_by):
        st = {"state": "restarting", "hubPid": os.getpid(), "action": "update",
              "requestedBy": requested_by}
        with mock.patch.object(restart_drain, "current", return_value=st), \
             mock.patch.object(dashboard.app_version, "packaged", return_value=False), \
             mock.patch.object(dashboard, "pull_checkout", return_value=""), \
             mock.patch.object(restart_drain, "end_drain"), \
             mock.patch.object(restart_drain, "save"), \
             mock.patch.object(dashboard, "trigger_restart",
                               return_value={"started": False, "status": 500}) as tr:
            restart_drain._launch()
        return tr.call_args.kwargs

    def test_after_a_drain_the_page_update_has_no_grace(self):
        kw = self.launch([{"label": "the page"}])
        self.assertEqual((kw.get("grace"), kw.get("purpose")), (0, "update"))

    def test_after_a_drain_a_po_update_keeps_the_grace(self):
        kw = self.launch([{"label": "PO", "roomId": "room-po"}])
        self.assertNotIn("grace", kw)


class Stage(Base):
    def test_idle_until_an_update(self):
        self.assertEqual(self.stage()["stage"], "idle")

    def test_checking_once_the_helper_starts(self):
        seen = {}

        def start(plan):
            seen["during"] = dict(dashboard._SRC_UPDATE)   # set before the helper can call back
            return {"started": True}
        with mock.patch.object(dashboard.BACKEND, "self_restart", side_effect=start):
            res = dashboard.trigger_restart(purpose="update")
        self.assertEqual(seen["during"]["stage"], "checking")
        st = self.stage()
        self.assertEqual((st["stage"], st["lease"]), ("checking", res["leaseId"]))

    def test_a_plain_restart_leaves_it(self):
        with mock.patch.object(dashboard.BACKEND, "self_restart", return_value={"started": True}):
            dashboard.trigger_restart()
        self.assertEqual(self.stage()["stage"], "idle")

    def test_restarting_for_its_own_lease_only(self):
        dashboard.src_update_stage("checking", "L1")
        dashboard.src_update_stage("restarting", "L0")
        self.assertEqual(self.stage()["stage"], "checking")
        dashboard.src_update_stage("restarting", "L1")
        self.assertEqual(self.stage()["stage"], "restarting")

    def test_a_helper_that_could_not_start_is_a_failure(self):
        with mock.patch.object(dashboard.BACKEND, "self_restart",
                               return_value={"started": False, "error": "no helper"}):
            dashboard.trigger_restart(purpose="update")
        st = self.stage()
        self.assertEqual(st["stage"], "failed")
        self.assertIn("no helper", st["error"])

    def test_a_helper_that_gave_up_while_this_hub_runs_is_a_failure(self):
        dashboard.src_update_stage("checking", "L1")
        (self.dir / "logs").mkdir()
        (self.dir / "logs" / "restart.log").write_text(
            "2026-11-11T10:00:00 [restart] PREFLIGHT FAILED - port 9000 never answered\n", encoding="utf-8")
        with mock.patch.object(dashboard, "RESTART_HELPER_POLL_S", 0), \
             mock.patch.object(dashboard, "_current_restart_lease", return_value=""), \
             mock.patch.object(dashboard, "retire_own_snapshot"):
            dashboard._watch_restart_helper(1, "L1", purpose="update")
        st = self.stage()
        self.assertEqual(st["stage"], "failed")
        self.assertEqual(st["error"], "The new code did not start: PREFLIGHT FAILED - port 9000 never answered")

    def test_a_new_request_forgets_the_last_outcome(self):
        dashboard.src_update_stage("failed", "L1", error="old")
        with mock.patch.object(restart_drain, "holding", return_value=False), \
             mock.patch.object(dashboard.app_version, "packaged", return_value=False), \
             mock.patch.object(dashboard, "source_update_keeps_host", return_value=True), \
             mock.patch.object(dashboard, "pull_checkout", return_value=""), \
             mock.patch.object(dashboard, "trigger_restart", return_value={"started": True}):
            restart_drain.request_restart("an update", {"label": "the page"}, "update")
        st = self.stage()
        self.assertEqual(st["stage"], "fetching", "under way, for a page whose request got no answer")
        self.assertNotIn("error", st)

    def test_an_update_joining_a_drain_forgets_the_last_outcome(self):
        dashboard.src_update_stage("failed", "L1", error="old")
        with mock.patch.object(restart_drain, "holding", return_value=True), \
             mock.patch.object(dashboard, "_current_restart_lease", return_value=None), \
             mock.patch.object(restart_drain, "current", return_value={"state": "restarting"}):
            restart_drain.request_restart("an update", {"label": "the page"}, "update")
        self.assertEqual(self.stage()["stage"], "idle")

    def test_a_failed_fetch_says_why_and_where(self):
        with mock.patch.object(restart_drain, "holding", return_value=False), \
             mock.patch.object(dashboard.app_version, "packaged", return_value=False), \
             mock.patch.object(dashboard, "source_update_keeps_host", return_value=True), \
             mock.patch.object(dashboard, "pull_checkout",
                               return_value="git fetch failed: Could not resolve host: github.com"), \
             mock.patch.object(dashboard, "trigger_restart") as tr:
            res = restart_drain.request_restart("an update", {"label": "the page"}, "update")
        tr.assert_not_called()
        self.assertEqual((res["status"], res["started"]), (500, False))
        self.assertEqual(res["error"], "git fetch failed: Could not resolve host: github.com")
        self.assertEqual(Path(res["log"]), self.dir / "logs" / "restart.log")
        self.assertIn("Could not resolve host", (self.dir / "logs" / "restart.log").read_text(encoding="utf-8"))
        st = self.stage()
        self.assertEqual((st["stage"], st["error"]), ("failed", res["error"]))
        self.assertEqual(st["advice"], "Check the connection, then Install now again.")


if __name__ == "__main__":
    unittest.main()
