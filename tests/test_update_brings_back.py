"""#240 (GitHub issue 21): Update now brings back every session that was
running before it.

* A hub-only restart or update writes a snapshot too (marked hub-only), and
  the hub that comes back checks it itself: an agent that died anyway is
  brought back, one still running is left alone.
* A snapshot a helper was to restore and never did (an older helper, a
  restore that timed out) is restored by the hub after LEFT_SNAPSHOT_WAIT_S;
  the hub and a late helper never both restore it.
* Every restore ends with "N of M room(s) running again" and the ones that
  are not, in restart.log, and in update.log for an update.
* A room not brought back gets a Needs you item and its PO one report.
* The app ships the PTY host's sources, and /api/version answers without them.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import app_update
import chatroom
import dashboard
import restore_problems
from test_quiet_restart import _Hub

ROOT = Path(__file__).resolve().parent.parent


class _Left(_Hub):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(dashboard, "LEFT_SNAPSHOT_POLL_S", 0.02),
                  mock.patch.object(dashboard, "_BOOT_HOST_UNKNOWN", False),
                  mock.patch.object(dashboard.running_rooms, "enabled", lambda: True)):
            p.start()
            self.addCleanup(p.stop)

    def left(self, keep, purpose="restart", age=0.0, lease="lease-1"):
        """A snapshot the hub before this one wrote, then stopped."""
        dashboard._LEASE_PURPOSE[lease] = purpose
        self.addCleanup(dashboard._LEASE_PURPOSE.pop, lease, None)
        dashboard.take_restart_snapshot(self.lease(lease), wake_room="", keep=keep)
        path = self.dir / "restart-snapshot.json"
        snap = json.loads(path.read_text(encoding="utf-8"))
        snap.update(hubPid=os.getpid() + 1, at=snap["at"] - age)
        path.write_text(json.dumps(snap), encoding="utf-8")
        return snap

    def restore(self, wait=0.2):
        out = dashboard.restore_left_snapshot(self.handler(), wait=wait)
        self.join()
        return out

    def update_log(self) -> str:
        path = self.dir / "logs" / "update.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class HubOnlySnapshot(_Left):
    def test_prepare_and_trigger_mark_it_hub_only_and_a_later_write_keeps_that(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        snap = dashboard.take_restart_snapshot(self.lease(), keep=True)
        self.assertTrue(snap["keepAgents"])
        self.assertEqual([r["roomId"] for r in snap["rooms"]], [rid])
        self.assertTrue(dashboard.take_restart_snapshot("lease-1")["keepAgents"])
        self.assertFalse(dashboard.take_restart_snapshot("lease-2")["keepAgents"])

    def test_an_update_lease_is_written_down_as_one(self):
        dashboard._LEASE_PURPOSE["lease-u"] = "update"
        self.addCleanup(dashboard._LEASE_PURPOSE.pop, "lease-u", None)
        self.assertEqual(dashboard.take_restart_snapshot(self.lease("lease-u"))["purpose"], "update")
        self.assertEqual(dashboard.take_restart_snapshot(self.lease("lease-r"))["purpose"], "restart")

    def test_agents_that_died_anyway_come_back_and_live_ones_are_left_alone(self):
        dead, alive = self.room(title="died"), self.room(title="kept")
        self.running(dead, "claude", "idle")
        self.running(alive, "claude", "idle")
        self.left(keep=True)
        # launchd took the PTY host along for one of them: only `alive` runs.
        self.listed[:] = [s for s in self.listed if s["meta"]["room"] == alive]
        self.ptys = {k: v for k, v in self.ptys.items() if alive in k}
        t0 = time.time()
        out = self.restore(wait=30)
        self.assertLess(time.time() - t0, 5)                 # hub-only: no wait
        by = {r["roomId"]: r["outcome"] for r in out["rooms"]}
        self.assertTrue(by[dead].startswith("brought back"), by)
        self.assertEqual(by[alive], "already running: left alone")
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.typed(dead), {"claude": []})   # it was idle: nothing typed
        self.assertIn("2 of 2 room(s) running again", out["note"])
        self.assertIn("2 of 2 room(s) running again", self.log())
        self.assertFalse((self.dir / "restart-snapshot.json").exists())

    def test_not_when_the_host_could_not_be_listed(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.left(keep=True)
        self.hub_stops()
        with mock.patch.object(dashboard, "_BOOT_HOST_UNKNOWN", True):
            out = self.restore()
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertTrue((self.dir / "restart-snapshot.json").exists())

    def test_not_its_own_nor_an_old_one(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        dashboard.take_restart_snapshot(self.lease(), keep=True)        # this process's
        self.hub_stops()
        self.assertEqual(self.restore()["rooms"], [])
        self.left(keep=True, age=dashboard.LEFT_SNAPSHOT_MAX_AGE_S + 60)
        self.assertEqual(self.restore()["rooms"], [])
        self.assertEqual(self.starts, 0)

    def test_a_restart_that_ended_without_this_hub_stopping_retires_its_snapshot(self):
        dashboard.take_restart_snapshot(self.lease(), keep=False)
        dashboard.retire_own_snapshot("other")
        self.assertTrue((self.dir / "restart-snapshot.json").exists())
        dashboard.retire_own_snapshot("lease-1")
        self.assertFalse((self.dir / "restart-snapshot.json").exists())
        self.assertTrue((self.dir / "restart-snapshot.last.json").exists())


class HelperNeverRestored(_Left):
    def test_the_hub_restores_it_after_the_wait(self):
        rid = self.room()
        self.running(rid, "claude", "working")
        self.left(keep=False, purpose="update")
        self.hub_stops()
        t0 = time.time()
        out = self.restore(wait=0.3)
        self.assertGreaterEqual(time.time() - t0, 0.3)
        self.assertEqual([r["roomId"] for r in out["rooms"]], [rid])
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESTART_NOTE]})
        self.assertIn("no helper restored the snapshot of 1 room(s)", self.log())
        self.assertIn("restored by the hub", self.log())
        self.assertIn("after the update: 1 of 1 room(s) running again", self.update_log())

    def test_a_helper_that_restores_meanwhile_is_left_to_it(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.left(keep=False)
        self.hub_stops()
        helper = threading.Timer(0.05, lambda: self.handler()._restore_after_restart("lease-1"))
        helper.start()
        out = self.restore(wait=2)
        helper.join()
        self.join()
        self.assertEqual(out["note"], "restored by the helper")
        self.assertEqual(self.starts, 1)

    def test_a_late_helper_finds_it_restored(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.left(keep=False)
        self.hub_stops()
        self.restore(wait=0.05)
        late = self.handler()._restore_after_restart("lease-1")
        self.assertEqual((late["rooms"], self.starts), ([], 1))

    def test_a_plain_restart_writes_nothing_to_update_log(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.left(keep=False)
        self.hub_stops()
        self.restore(wait=0.05)
        self.assertEqual(self.update_log(), "")


class NotBroughtBack(_Left):
    def failing(self, rid):
        h = self.handler()
        real = h._resume_room_agent_pty

        def flaky(room_full, part, **kw):
            if room_full["id"] == rid:
                raise RuntimeError("no such session")
            return real(room_full, part, **kw)
        h._resume_room_agent_pty = flaky
        return h

    def test_a_needs_you_item_and_one_report_to_its_po(self):
        po, bad, good = self.room(title="PO"), self.room(title="Broken"), self.room(title="Fine")
        self.projects = [{"id": "p1", "name": "P", "poRoomId": po}]
        for rid in (bad, good):
            room = chatroom.get_room(rid, public=False)
            room["projectId"] = "p1"
            chatroom.update_room(room)
            self.running(rid, "claude", "idle")
        self.left(keep=False, purpose="update")
        self.hub_stops()
        rung = []
        h = self.failing(bad)
        with mock.patch.object(dashboard.ensemble_tools, "_project_of_room",
                               lambda room, links=None: room.get("projectId", "")), \
                mock.patch.object(type(h), "_ring", lambda s, rid, idents, wake: rung.append(wake) or idents,
                                  create=True):
            out = h._restore_after_restart("lease-1")
            self.join()
            self.assertIn(f"1 of 2 room(s) running again; not: {bad} 'Broken'", out["note"])
            items = restore_problems.by_room()
            self.assertEqual(list(items), [bad])
            self.assertIn("The update could not bring this task back: RuntimeError('no such session')",
                          items[bad]["reason"])
            reports = [m for m in chatroom.get_room(po, public=False)["messages"]
                       if (m.get("meta") or m).get("reportKind") == "blocked"
                       or "could not bring back" in m.get("text", "")]
            self.assertEqual(len(reports), 1)
            self.assertIn("could not bring back 1 task", reports[0]["text"])
            self.assertEqual(len(rung), 1)
            # The same restart again (a retry of the restore) tells nobody twice.
            self.assertFalse(restore_problems.record(bad, "Broken", "again", "update", "lease-1"))
            # A new restart of the same room is told again.
            self.assertTrue(restore_problems.record(bad, "Broken", "again", "update", "lease-2"))
        self.assertTrue(restore_problems.clear(bad))
        self.assertEqual(restore_problems.by_room(), {})


class HelperLogs(unittest.TestCase):
    def test_the_app_helper_waits_long_and_says_which_rooms_are_not_back(self):
        helper = app_update.Helper.__new__(app_update.Helper)
        helper.port, helper.plan = 8765, {"leaseId": "L"}
        lines = []
        helper.log = lines.append
        res = {"rooms": [{"roomId": "r1", "outcome": "brought back; nothing typed"},
                         {"roomId": "r2", "outcome": "already running: left alone"},
                         {"roomId": "r3", "outcome": "NOT brought back: boom"}]}
        with mock.patch.object(app_update, "_post", return_value=res) as post:
            helper.restore_rooms()
        self.assertEqual(post.call_args.kwargs["timeout"], app_update.RESTORE_TIMEOUT_S)
        self.assertGreaterEqual(app_update.RESTORE_TIMEOUT_S, 600)
        self.assertEqual(lines, ["rooms: 3 in the snapshot, 2 running again, "
                                 "not: r3 (NOT brought back: boom)"])

    def test_a_restore_that_does_not_answer_is_said_so(self):
        helper = app_update.Helper.__new__(app_update.Helper)
        helper.port, helper.plan = 8765, {"leaseId": "L"}
        lines = []
        helper.log = lines.append
        with mock.patch.object(app_update, "_post", side_effect=TimeoutError("timed out")):
            helper.restore_rooms()
        self.assertIn("the hub brings back what it lists by itself", lines[0])


class AppShipsTheHostSources(unittest.TestCase):
    def test_the_spec_ships_ptyhost_and_ptyrun(self):
        spec = (ROOT / "packaging" / "ensemble.spec").read_text(encoding="utf-8")
        m = re.search(r'ROOT / "backends" / name\), "backends"\) for name in \(([^)]*)\)', spec)
        self.assertIsNotNone(m, "the spec does not ship backends/*.py as data")
        self.assertEqual(set(re.findall(r'"([^"]+)"', m.group(1))), {"ptyhost.py", "ptyrun.py"})


class VersionWithoutHostSources(unittest.TestCase):
    def test_api_version_answers_with_an_empty_host_version(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.object(dashboard.ptyhost, "version",
                               side_effect=FileNotFoundError("_internal/backends/ptyhost.py")):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.request("GET", "/api/version")
            res = conn.getresponse()
            body = json.loads(res.read())
            conn.close()
        self.assertEqual(res.status, 200)
        self.assertEqual(body["ptyHostVersion"], "")
        self.assertIn("version", body)


if __name__ == "__main__":
    unittest.main()
