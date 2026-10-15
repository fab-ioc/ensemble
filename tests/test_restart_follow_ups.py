"""#241: follow-ups to #240 (Update now brings sessions back).

* A hub that is still running after an update or restart that did not
  replace it retires the snapshot it took: an app update whose helper gave
  up, and a restore of this process's own snapshot.
* A review that finished between the snapshot and the stop is not run again.
* restart.log names only the agents a line was typed into.
* The running-rooms heartbeat starts before a left snapshot's restore waits.
* A PTY host that dies under a running hub is replaced and its rooms come back.
* Lease purposes are dropped when the lease ends.
* ptyhost.version() finds its sources by resource path, not ``__file__``.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from backends import ptyhost
from test_update_brings_back import _Left

ROOT = Path(__file__).resolve().parent.parent


class OwnSnapshotRetired(_Left):
    def test_a_restore_of_this_processs_own_snapshot_retires_it(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        dashboard.take_restart_snapshot(self.lease(), keep=False)
        out = self.handler()._restore_after_restart("lease-1")
        self.assertIn("this hub process took the snapshot", out["note"])
        self.assertEqual(self.starts, 0)
        self.assertFalse((self.dir / "restart-snapshot.json").exists())
        self.assertTrue((self.dir / "restart-snapshot.last.json").exists())
        self.assertIn("the snapshot retired", self.log())


class FailedAppUpdate(_Left):
    """The update helper started, then gave up while this hub ran on."""

    def run_update(self, alive):
        lease = self.lease("lease-u")
        dashboard._LEASE_PURPOSE[lease] = "update"
        self.running(self.room(), "claude", "idle")
        dashboard.take_restart_snapshot(lease, keep=False)
        update_dir = self.dir / "updates"
        update_dir.mkdir()
        helper_started = threading.Event()

        def start_detached(argv, log):
            helper_started.set()
            Path(argv[-1]).unlink()         # the helper read its plan
            return {"started": True, "helperPid": 4242}
        patches = [
            mock.patch.object(dashboard, "UPDATE_DIR", update_dir),
            mock.patch.object(dashboard, "RESTART_HELPER_POLL_S", 0.05),
            mock.patch.object(dashboard.app_update, "stage", lambda rel, d: d / "new"),
            mock.patch.object(dashboard.app_update, "copy_helper", lambda root, d: d / "helper"),
            mock.patch.object(dashboard, "update_plan", lambda rel, new, lease: {}),
            mock.patch.object(dashboard.BACKEND, "start_detached", side_effect=start_detached),
            mock.patch.object(dashboard.BACKEND, "process_alive", lambda pid: alive()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        dashboard._run_app_update({"version": "9.9.9", "assetName": "x"}, lease)
        self.assertTrue(helper_started.is_set())
        return lease

    def wait_retired(self):
        end = time.time() + 5
        while time.time() < end and (self.dir / "restart-snapshot.json").exists():
            time.sleep(0.05)
        self.assertFalse((self.dir / "restart-snapshot.json").exists())

    def test_the_helper_drops_the_lease_and_the_hub_runs_on(self):
        lease = self.run_update(alive=lambda: True)
        self.assertTrue((self.dir / "restart-snapshot.json").exists())    # under way
        (self.dir / "restart.lease").unlink()       # the helper gave up ("did not stop")
        self.wait_retired()
        self.assertNotIn(lease, dashboard._LEASE_PURPOSE)

    def test_the_helper_ends_with_the_lease_held(self):
        gone = threading.Event()
        lease = self.run_update(alive=lambda: not gone.is_set())
        gone.set()
        self.wait_retired()
        self.assertFalse((self.dir / "restart.lease").exists())
        self.assertIn("HELPER GONE - the update helper (pid 4242)", self.update_log())
        self.assertNotIn(lease, dashboard._LEASE_PURPOSE)


class FinishedReview(_Left):
    def reviewed_room(self, owner_runs=True):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"], title="rev")
        full = chatroom.get_room(rid, public=False)
        full["messages"].append({"id": "m7", "from": "claude", "to": "codex",
                                 "text": "@codex review abc", "ts": time.time()})
        chatroom.update_room(full)
        owner = self.running(rid, "claude", "idle") if owner_runs else None
        self.running(rid, "codex", "working", "screen")
        chatroom.patch_participant(rid, "codex", {"review": {"n": 2, "messageId": "m7"}})
        return rid, owner

    def only(self, pid):
        self.listed[:] = [s for s in self.listed if s["id"] == pid]
        self.ptys = {pid: self.ptys[pid]} if pid else {}

    def test_a_review_ended_by_review_done_is_not_run_again(self):
        rid, owner = self.reviewed_room()
        self.left(keep=True)
        chatroom.patch_participant(rid, "codex", {"review": {"n": 2, "messageId": "m7",
                                                             "endedAt": time.time()}})
        self.only(owner)
        out = self.restore()
        self.assertEqual(out["rooms"][0]["outcome"], "already running: left alone")
        self.assertEqual((self.reviews, self.starts), ([], 0))

    def test_a_review_whose_verdict_is_in_the_log_is_not_run_again(self):
        rid, owner = self.reviewed_room()
        self.left(keep=True)
        room = chatroom.get_room(rid, public=False)
        dashboard.append_review_log(room, "## Review 1\nok\n\n## Review 2\napprove")
        self.only(owner)
        self.restore()
        self.assertEqual(self.reviews, [])

    def test_a_newer_review_since_does_not_rerun_the_old_one(self):
        rid, owner = self.reviewed_room()
        self.left(keep=True)
        chatroom.patch_participant(rid, "codex", {"review": {"n": 3, "messageId": "m9"}})
        self.only(owner)
        self.restore()
        self.assertEqual(self.reviews, [])

    def test_a_review_still_under_way_is_run_again(self):
        rid, owner = self.reviewed_room()
        self.left(keep=True)
        self.only(owner)
        self.restore()
        self.assertEqual(self.reviews, [(rid, "codex", "m7")])

    def test_a_room_where_only_a_finished_review_ran_is_not_counted(self):
        rid, _ = self.reviewed_room(owner_runs=False)
        self.left(keep=True)
        chatroom.patch_participant(rid, "codex", {"review": {"n": 2, "messageId": "m7",
                                                             "endedAt": time.time()}})
        self.only(None)
        out = self.restore()
        self.assertEqual(out["rooms"][0]["outcome"],
                         "review finished since the snapshot: nothing to bring back")
        self.assertEqual((self.reviews, self.starts), ([], 0))
        self.assertIn("0 of 0 room(s) running again; 1 whose review finished since",
                      out["note"])


class LogNamesWhoWasTyped(_Left):
    def test_a_mid_turn_agent_that_kept_running_is_not_named(self):
        rid = self.room(agents=("claude", "claude-b"), title="pair")
        kept = self.running(rid, "claude", "working")
        self.running(rid, "claude-b", "idle")
        self.left(keep=True)
        self.listed[:] = [s for s in self.listed if s["id"] == kept]
        self.ptys = {kept: self.ptys[kept]}
        out = self.restore()
        outcome = out["rooms"][0]["outcome"]
        self.assertEqual(outcome, "brought back; nothing typed; claude kept running")

    def test_a_mid_turn_agent_brought_back_is_named(self):
        rid = self.room(agents=("claude", "claude-b"), title="pair")
        kept = self.running(rid, "claude", "idle")
        self.running(rid, "claude-b", "working")
        self.left(keep=True)
        self.listed[:] = [s for s in self.listed if s["id"] == kept]
        self.ptys = {kept: self.ptys[kept]}
        out = self.restore()
        self.assertEqual(out["rooms"][0]["outcome"],
                         "brought back; one line to claude-b, nothing typed into the rest; "
                         "claude kept running")


class HeartbeatFirst(unittest.TestCase):
    def test_the_heartbeat_starts_before_a_left_snapshots_restore(self):
        order = []
        done = threading.Event()
        with mock.patch.object(dashboard, "BOOT_RESTORE_DELAY_S", 0), \
                mock.patch.object(dashboard, "restore_after_boot", lambda: order.append("boot")), \
                mock.patch.object(dashboard, "restore_left_snapshot", lambda: order.append("left")), \
                mock.patch.object(dashboard.running_rooms, "start_heartbeat",
                                  lambda: order.append("heartbeat")), \
                mock.patch.object(dashboard, "start_pty_host_watch",
                                  lambda: (order.append("watch"), done.set())):
            dashboard.start_boot_restore()
            self.assertTrue(done.wait(5))
        self.assertEqual(order, ["boot", "heartbeat", "left", "watch"])


class FakeClient:
    def __init__(self, pid):
        self.pid, self.port, self.token, self.version = pid, 1, "t", "v"


class PtyHostDies(_Left):
    def setUp(self):
        super().setUp()
        self.old = FakeClient(111)
        self.new = None
        self.host_dead = False
        self.attached = []
        real_list = dashboard.ptyrun.list_sessions

        def listing():
            if self.host_dead:
                raise ConnectionError("PTY host closed the connection")
            return real_list()

        def connect(state_dir, backend=None):
            self.new = FakeClient(222)
            return self.new

        def use_host(client):
            self.attached.append(client)
            self.host_dead = False

        for p in (mock.patch.object(dashboard, "_PTY_HOST_MODE", "attached"),
                  mock.patch.object(dashboard, "_PTY_HOST_CLIENT", self.old),
                  mock.patch.object(dashboard, "_PTY_HOST_LIVE", None),
                  mock.patch.object(dashboard, "BOOT_RESTORE_GAP_S", 0),
                  mock.patch.object(dashboard.ptyrun, "list_sessions", listing),
                  mock.patch.object(dashboard.ptyrun, "use_host", use_host),
                  mock.patch.object(dashboard.ptyhost, "connect", connect),
                  mock.patch.object(dashboard.ptyhost, "_pid_alive",
                                    lambda pid: not (self.host_dead and pid == 111)),
                  mock.patch.object(dashboard.ptyhost, "_call",
                                    side_effect=ConnectionRefusedError("refused")),
                  mock.patch.object(dashboard, "hub_launcher", self.handler)):
            p.start()
            self.addCleanup(p.stop)

    def die(self):
        """The host and every terminal on it end; the hub runs on."""
        self.ptys.clear()
        self.listed.clear()
        self.host_dead = True

    def test_a_new_host_and_the_rooms_back_and_the_po_told_once(self):
        po, idle, busy = self.room(title="PO"), self.room(title="Idle"), self.room(title="Busy")
        self.projects = [{"id": "p1", "name": "P", "poRoomId": po}]
        for rid in (idle, busy):
            room = chatroom.get_room(rid, public=False)
            room["projectId"] = "p1"
            chatroom.update_room(room)
        self.running(idle, "claude", "idle")
        self.running(busy, "claude", "working")
        dashboard.running_rooms.refresh()
        self.assertIsNone(dashboard.check_pty_host())       # alive: nothing to do
        self.die()
        with mock.patch.object(dashboard.ensemble_tools, "_project_of_room",
                               lambda room, links=None: room.get("projectId", "")):
            out = dashboard.check_pty_host()
            self.join()
        self.assertEqual(len(self.attached), 1)
        self.assertIs(dashboard._PTY_HOST_CLIENT._client, self.new)
        by = {r["roomId"]: r["outcome"] for r in out["rooms"]}
        self.assertEqual(set(by), {idle, busy})
        self.assertTrue(all(o.startswith("brought back") for o in by.values()), by)
        self.assertEqual(self.typed(idle), {"claude": []})
        self.assertEqual(self.typed(busy), {"claude": [dashboard.RESTART_NOTE]})
        log = self.log()
        self.assertIn("PTY host (pid 111) has gone while the hub runs", log)
        self.assertIn("PTY host attached: pid 222", log)
        self.assertIn("PTY host: 2 of 2 room(s) running again", log)
        notices = [m for m in chatroom.get_room(po, public=False)["messages"]
                   if "terminal host stopped" in m.get("text", "")]
        self.assertEqual(len(notices), 1)
        self.assertIn("Idle", notices[0]["text"])
        self.assertIn("Busy", notices[0]["text"])
        # The new host answers: a later look does nothing more.
        self.assertIsNone(dashboard.check_pty_host())
        self.assertEqual(len(self.attached), 1)

    def test_a_room_not_running_at_the_last_check_is_not_brought_back(self):
        a, b = self.room(title="A"), self.room(title="B")
        self.running(a, "claude", "idle")
        self.running(b, "claude", "idle")
        dashboard.running_rooms.refresh()
        self.listed[:] = [s for s in self.listed if s["meta"]["room"] == a]
        dashboard.check_pty_host()
        self.die()
        out = dashboard.check_pty_host()
        self.assertEqual([r["roomId"] for r in out["rooms"]], [a])

    def test_a_host_alive_but_not_answering_is_left_alone(self):
        self.running(self.room(), "claude", "idle")
        dashboard.running_rooms.refresh()
        self.die()
        with mock.patch.object(dashboard.ptyhost, "_pid_alive", lambda pid: True):
            self.assertIsNone(dashboard.check_pty_host())
        self.assertEqual((self.attached, self.starts), ([], 0))

    def test_not_while_a_restart_is_under_way(self):
        self.running(self.room(), "claude", "idle")
        dashboard.running_rooms.refresh()
        self.lease()
        self.die()
        self.assertIsNone(dashboard.check_pty_host())
        self.assertEqual(self.attached, [])

    def test_a_resume_that_failed_against_the_dead_host_is_taken_over(self):
        rid = self.room(title="Sent to")
        self.running(rid, "claude", "idle")
        dashboard.running_rooms.refresh()
        dashboard.check_pty_host()
        self.die()
        # A person's send resumed the room against the dead host and failed:
        # its send is kept for Retry.
        failed = dashboard._Resume(rid)
        failed.queue.append({"text": "hello", "to": "", "at": time.time(), "key": "k1"})
        failed.fail("PTY host closed the connection")
        dashboard._RESUMES[rid] = failed
        out = dashboard.check_pty_host()
        self.join()
        self.assertTrue(out["rooms"][0]["outcome"].startswith("brought back"), out)
        self.assertEqual(self.starts, 1)
        res = dashboard._RESUMES.get(rid)
        self.assertNotEqual(getattr(res, "state", ""), "failed")
        # Its send came along and was typed once the agent was up.
        self.assertTrue(any("hello" in t for t in self.typed(rid)["claude"]), self.typed(rid))

    def test_a_restart_asked_for_during_the_recovery_is_refused(self):
        self.running(self.room(), "claude", "idle")
        dashboard.running_rooms.refresh()
        self.die()
        taken = []

        def connect(state_dir, backend=None):
            taken.append(dashboard._take_restart_lease()[0])    # a restart asked for now
            self.new = FakeClient(222)
            return self.new

        real = self.handler

        def launcher():
            h = real()
            start = h._bring_back_rooms

            def between(*a, **k):
                taken.append(dashboard._take_restart_lease()[0])
                return start(*a, **k)
            h._bring_back_rooms = between
            return h
        with mock.patch.object(dashboard.ptyhost, "connect", connect), \
                mock.patch.object(dashboard, "hub_launcher", launcher):
            out = dashboard.check_pty_host()
        self.assertTrue(out["ok"])
        self.assertEqual(taken, ["", ""])                       # refused both times
        self.assertEqual(self.starts, 1)
        self.assertFalse((self.dir / "restart.lease").exists())   # let go after
        self.assertTrue(dashboard._take_restart_lease()[0])

    def test_not_while_a_drain_holds_what_it_would_start(self):
        self.running(self.room(), "claude", "idle")
        dashboard.running_rooms.refresh()
        self.die()
        with mock.patch.object(dashboard.restart_drain, "holding", lambda: True):
            self.assertIsNone(dashboard.check_pty_host())
        self.assertEqual(self.attached, [])

    def test_a_host_that_cannot_start_is_tried_again(self):
        self.running(self.room(), "claude", "idle")
        dashboard.running_rooms.refresh()
        self.die()
        with mock.patch.object(dashboard.ptyhost, "connect", side_effect=RuntimeError("no")):
            out = dashboard.check_pty_host()
        self.assertFalse(out["ok"])
        self.assertIs(dashboard._PTY_HOST_CLIENT, self.old)
        self.assertIn("a new one could not be started", self.log())
        self.assertTrue(dashboard.check_pty_host()["ok"])
        self.assertEqual(self.starts, 1)


class LeasePurposes(unittest.TestCase):
    def test_dropped_when_the_lease_ends(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(dashboard, "DASHBOARD_DIR", Path(d)):
            lease, _ = dashboard._take_restart_lease()
            dashboard._LEASE_PURPOSE[lease] = "update"
            dashboard._drop_restart_lease(lease)
            self.assertNotIn(lease, dashboard._LEASE_PURPOSE)
            dashboard._LEASE_PURPOSE["L2"] = "restart"
            dashboard.retire_own_snapshot("L2")
            self.assertNotIn("L2", dashboard._LEASE_PURPOSE)


class RenewalAfterTheDrop(unittest.TestCase):
    """A renewal never brings back a lease dropped while it ran."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(dashboard, "DASHBOARD_DIR", Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)
        self.lease, _ = dashboard._take_restart_lease()
        self.path = dashboard._restart_lease_path()
        self.done = threading.Event()

    def test_a_drop_waits_for_a_renewal_between_its_read_and_its_write(self):
        real = dashboard.app_update.renew_lease
        read, go = threading.Event(), threading.Event()

        def slow_renew(path, lease_id):
            json.loads(Path(path).read_text(encoding="utf-8"))
            read.set()
            go.wait(5)
            real(path, lease_id)
        with mock.patch.object(dashboard.app_update, "renew_lease", slow_renew):
            renewer = threading.Thread(target=dashboard._renew_restart_lease,
                                       args=(self.lease, self.done, 0.01))
            renewer.start()
            self.assertTrue(read.wait(5))
            self.done.set()
            dropper = threading.Thread(target=dashboard._drop_restart_lease, args=(self.lease,))
            dropper.start()
            dropper.join(0.3)
            self.assertTrue(dropper.is_alive())     # waits for the renewal's write
            go.set()
            renewer.join(5)
            dropper.join(5)
        self.assertFalse(self.path.exists())
        lease, _ = dashboard._take_restart_lease()  # the next restart gets it
        self.assertTrue(lease)
        dashboard._drop_restart_lease(lease)

    def test_a_renewal_due_as_the_recovery_ends_writes_nothing(self):
        calls = []
        with mock.patch.object(dashboard.app_update, "renew_lease",
                               lambda *a: calls.append(a)):
            with dashboard._RESTART_LOCK:
                renewer = threading.Thread(target=dashboard._renew_restart_lease,
                                           args=(self.lease, self.done, 0.01))
                renewer.start()
                time.sleep(0.1)                     # due, waiting for the lock
                self.done.set()
                self.path.unlink()
            renewer.join(5)
        self.assertFalse(renewer.is_alive())
        self.assertEqual(calls, [])
        self.assertFalse(self.path.exists())


class HelperDropsAfterARenewal(unittest.TestCase):
    """A failed update: the helper (another process, without the hub's lock)
    drops the lease while a renewal, the hub's or its own, is under way."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "restart.lease"
        self.path.write_text(json.dumps({"id": "L1", "at": time.time() - 30}), encoding="utf-8")
        self.read, self.go = threading.Event(), threading.Event()
        real_time = time.time

        def paused_time():  # renew_lease's stamp: after its read, before its write
            if threading.current_thread().name == "renewal":
                self.read.set()
                self.go.wait(5)
            return real_time()
        clock = types.SimpleNamespace(time=paused_time, sleep=time.sleep)
        p = mock.patch.object(dashboard.app_update, "time", clock)
        p.start()
        self.addCleanup(p.stop)

    def renewal(self, target):
        t = threading.Thread(target=target, name="renewal")
        t.start()
        self.assertTrue(self.read.wait(5))
        return t

    def test_a_renewal_between_its_read_and_its_write_does_not_bring_the_lease_back(self):
        renewer = self.renewal(lambda: dashboard.app_update.renew_lease(str(self.path), "L1"))
        dropper = threading.Thread(target=dashboard.app_update.drop_lease_file,
                                   args=(str(self.path), "L1"))
        dropper.start()
        time.sleep(0.2)                             # the drop, mid-renewal
        self.go.set()
        renewer.join(5)
        dropper.join(5)
        self.assertFalse(dropper.is_alive())
        self.assertFalse(self.path.exists())

    def test_a_renewal_never_creates_a_dropped_lease(self):
        self.go.set()
        self.path.unlink()
        dashboard.app_update.renew_lease(str(self.path), "L1")
        self.assertFalse(self.path.exists())

    def test_a_renewal_leaves_another_lease_alone(self):
        self.go.set()
        self.path.write_text(json.dumps({"id": "L2", "at": 1.0}), encoding="utf-8")
        dashboard.app_update.renew_lease(str(self.path), "L1")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"id": "L2", "at": 1.0})

    def test_the_helper_stops_and_joins_its_heartbeat_before_it_drops(self):
        helper = dashboard.app_update.Helper.__new__(dashboard.app_update.Helper)
        helper.plan = {"leasePath": str(self.path), "leaseId": "L1"}
        done = threading.Event()
        beat = self.renewal(helper.renew)           # its heartbeat, mid-renewal
        helper._beat = (done, beat)
        dropped = []
        real_drop = dashboard.app_update.drop_lease_file

        def drop(*a):
            dropped.append(beat.is_alive())
            real_drop(*a)
        threading.Timer(0.2, self.go.set).start()
        with mock.patch.object(dashboard.app_update, "drop_lease_file", drop):
            helper.drop_lease()
        self.assertEqual(dropped, [False])          # the heartbeat had ended
        self.assertTrue(done.is_set())
        self.assertFalse(self.path.exists())


class HostVersionByResourcePath(unittest.TestCase):
    def setUp(self):
        self.real = ptyhost.version()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def test_a_frozen_module_whose_file_is_a_pyc_reads_the_shipped_sources(self):
        shipped = self.dir / "_internal" / "backends"
        shipped.mkdir(parents=True)
        for name in ("ptyhost.py", "ptyrun.py"):
            shutil.copy(ROOT / "backends" / name, shipped / name)
        elsewhere = self.dir / "lib" / "backends"
        elsewhere.mkdir(parents=True)
        with mock.patch.object(ptyhost, "__file__", str(elsewhere / "ptyhost.pyc")), \
                mock.patch.object(sys, "_MEIPASS", str(self.dir / "_internal"), create=True):
            self.assertEqual(ptyhost.version(), self.real)

    def test_a_frozen_module_with_no_file_reads_the_shipped_sources(self):
        shipped = self.dir / "_internal" / "backends"
        shipped.mkdir(parents=True)
        for name in ("ptyhost.py", "ptyrun.py"):
            shutil.copy(ROOT / "backends" / name, shipped / name)
        own = ptyhost.__dict__.pop("__file__")
        self.addCleanup(setattr, ptyhost, "__file__", own)
        with mock.patch.object(sys, "_MEIPASS", str(self.dir / "_internal"), create=True):
            self.assertEqual(ptyhost.version(), self.real)
        with self.assertRaises(OSError):
            ptyhost.version()

    def test_a_pyc_beside_the_sources_reads_them(self):
        with mock.patch.object(ptyhost, "__file__",
                               str(ROOT / "backends" / "ptyhost.cpython-312.pyc")):
            self.assertEqual(ptyhost.version(), self.real)

    def test_no_sources_anywhere_is_an_oserror(self):
        with mock.patch.object(ptyhost, "__file__", str(self.dir / "ptyhost.pyc")):
            with self.assertRaises(OSError):
                ptyhost.version()


if __name__ == "__main__":
    unittest.main()
