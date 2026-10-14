"""A cold start brings back what was running, and lines the hub types that do
not arrive raise an alarm (P190).

* ``running_rooms``: the record of the rooms whose agents run, kept on start,
  stop and a heartbeat; ``dashboard.restore_after_boot`` brings them back when
  the PTY host has none of them (the machine booted, the host died).
* ``hub_typing``: the outcome of every hub-typed line per agent; repeated
  refusals to an idle agent, or one that should never happen, raise an alarm
  once per cause, cleared when lines arrive again.
* ``dashboard.preflight_typing_check``: the restart preflight types a line into
  a throwaway agent and sees it arrive; the 10-13 fault fails it.
"""
from __future__ import annotations

import json
import os
import time
import types
import unittest
from unittest import mock

import chatroom
import dashboard
import hub_typing
import running_rooms
from test_quiet_restart import _Hub
from test_send_resumes import FakePty


class _Boot(_Hub):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(running_rooms, "_ACTIVE", True),
                  mock.patch.object(dashboard, "_BOOT_HOST_ROOMS", None),
                  mock.patch.object(dashboard, "_RESTART_RESTORING", False)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("ENSEMBLE_PTY_PREFLIGHT", None)

    def record(self) -> dict:
        return running_rooms.read()["rooms"]

    def boot(self):
        out = dashboard.restore_after_boot(handler=self.handler(), gap=0)
        self.join()
        return out


class BootRestore(_Boot):
    def test_an_empty_host_brings_back_the_recorded_rooms(self):
        idle, busy = self.room(title="Idle one"), self.room(title="Busy one")
        self.running(idle, "claude", "idle")
        self.running(busy, "claude", "working")
        self.assertTrue(running_rooms.refresh())
        self.hub_stops()                        # the machine went down
        out = self.boot()
        self.assertEqual(sorted(r["roomId"] for r in out["rooms"]), sorted([idle, busy]))
        self.assertEqual(self.typed(idle), {"claude": []})
        self.assertEqual(self.typed(busy), {"claude": [dashboard.RESTART_NOTE]})
        for rid in (idle, busy):
            self.assertTrue(dashboard._room_is_live(chatroom.get_room(rid, public=False)))
        log = self.log()
        self.assertIn("boot: the PTY host runs none of the 2 room(s) that were running", log)
        self.assertIn(f"restore {idle} 'Idle one' (was: claude idle/hook): brought back; "
                      f"nothing typed", log)
        self.assertIn(f"restore {busy} 'Busy one' (was: claude working/hook): brought back; "
                      f"one line to claude", log)
        self.assertIn("boot: 2 of 2 room(s) brought back", log)

    def test_the_hub_s_own_restore_knows_its_port(self):
        # The boot restore has no request behind it; the agents' hook env
        # reads the hub's port from self.server (the 10-14 e2e failure).
        rid = self.room()
        self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        seen = []

        def bring_back(h, entries, **kw):
            seen.append(h.server.server_address[1])
            return []
        with mock.patch.object(dashboard, "HUB_PORT", 8797), \
                mock.patch.object(dashboard.Handler, "_bring_back_rooms", bring_back):
            dashboard.restore_after_boot(gap=0)
        self.assertEqual(seen, [8797])

    def test_a_room_stopped_on_purpose_stays_stopped(self):
        kept, stopped = self.room(title="kept"), self.room(title="stopped")
        self.running(kept, "claude", "idle")
        self.running(stopped, "claude", "idle")
        running_rooms.refresh()
        with mock.patch.object(dashboard.ptyrun, "kill", lambda pid: True), \
                mock.patch.object(dashboard.ptyrun, "forget_death", lambda pid: None, create=True):
            self.assertTrue(dashboard.stop_task(stopped))
        self.assertEqual(sorted(self.record()), [kept])
        self.hub_stops()
        out = self.boot()
        self.assertEqual([r["roomId"] for r in out["rooms"]], [kept])
        self.assertFalse(dashboard._room_is_live(chatroom.get_room(stopped, public=False)))
        self.assertNotIn(stopped, self.log())

    def test_a_start_puts_the_room_in_the_record(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.hub_stops()
        with mock.patch.object(dashboard.attention, "turn_state",
                               lambda part, statuses=None: ("working", "hook")
                               if part.get("ptyId") in self.ptys else ("stopped", "")):
            self.handler()._start_or_resume_room(chatroom.get_room(rid, public=False), restart={})
        self.join()
        self.assertEqual(list(self.record()), [rid])
        # Just started: as far as a cold start goes, nobody is mid-turn yet.
        self.assertEqual(self.record()[rid]["agents"][0]["state"], "idle")

    def test_a_hub_only_restart_brings_nothing_back(self):
        rid = self.room()
        self.running(rid, "claude", "working")
        running_rooms.refresh()
        out = self.boot()                       # the host kept it running
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertIn("the PTY host ran 1 room(s) when the hub started", out["note"])

    def test_a_room_started_before_the_restore_does_not_stop_the_others(self):
        """A replayed page action or a message starts one recorded room in the
        hub's first seconds: the host was empty at attach, so the others still
        come back (review 1, finding 1)."""
        early, other = self.room(title="early"), self.room(title="other")
        self.running(early, "claude", "idle")
        self.running(other, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        dashboard.note_host_at_attach()             # main: the host is empty
        self.running(early, "claude", "idle")       # started by a message meanwhile
        out = self.boot()
        self.assertEqual([r["roomId"] for r in out["rooms"]], [other])
        self.assertEqual(self.starts, 1)
        self.assertIn(f"1 recorded room(s) already started again since the hub came up: "
                      f"left as they are ({early})", self.log())
        self.assertEqual(sorted(self.record()), sorted([early, other]))

    def test_a_host_that_outlived_the_hub_is_a_warm_start(self):
        """At attach the host runs a room the (stale) record does not name:
        the host did not die, so nothing is brought back."""
        rec, unrec = self.room(title="recorded"), self.room(title="unrecorded")
        self.running(rec, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        self.running(unrec, "claude", "idle")
        dashboard.note_host_at_attach()
        out = self.boot()
        self.assertEqual((out["rooms"], self.starts), ([], 0))

    def test_a_restart_under_way_is_left_to_its_helper(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        self.lease()
        out = self.boot()
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertIn("a restart is under way", self.log())

    def test_a_restart_whose_restore_began_is_left_to_it(self):
        """The lease aged past RESTART_BUSY_S and the snapshot was already
        renamed by /api/restart/restore: still not brought back twice."""
        rid = self.room()
        self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        with mock.patch.object(dashboard, "_RESTART_RESTORING", True):
            out = self.boot()
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertIn("the restart's restore is bringing the rooms back", self.log())

    def test_the_first_start_with_this_code_only_starts_the_record(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        out = self.boot()
        self.assertEqual((out["rooms"], self.starts), ([], 0))
        self.assertEqual(list(self.record()), [rid])

    def test_a_preflight_hub_neither_restores_nor_records(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        with mock.patch.dict(os.environ, {"ENSEMBLE_PTY_PREFLIGHT": "1"}):
            self.assertEqual(self.boot()["rooms"], [])
            running_rooms.note_stopped(rid)
            self.assertFalse(running_rooms.refresh())
        self.assertEqual(list(self.record()), [rid])

    def test_an_imported_module_keeps_no_record(self):
        with mock.patch.object(running_rooms, "_ACTIVE", False):
            rid = self.room()
            self.running(rid, "claude", "idle")
            running_rooms.refresh()
            running_rooms.note_started(rid)
        self.assertFalse((self.dir / running_rooms.NAME).exists())

    def test_the_record_is_replaced_whole(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.assertFalse((self.dir / (running_rooms.NAME + ".tmp")).exists())
        self.assertIn(rid, json.loads((self.dir / running_rooms.NAME).read_text(encoding="utf-8"))["rooms"])


class Heartbeat(_Boot):
    def test_everything_gone_at_once_is_kept(self):
        a, b = self.room(), self.room()
        self.running(a, "claude", "idle")
        self.running(b, "claude", "idle")
        running_rooms.refresh()
        self.hub_stops()
        for _ in range(3):
            running_rooms.refresh()
        self.assertEqual(sorted(self.record()), sorted([a, b]))

    def test_a_room_that_ended_while_others_ran_leaves_after_two_beats(self):
        a, b = self.room(), self.room()
        self.running(a, "claude", "idle")
        pid = self.running(b, "claude", "idle")
        running_rooms.refresh()
        self.listed[:] = [x for x in self.listed if x["id"] != pid]
        self.ptys.pop(pid)
        running_rooms.refresh()
        self.assertIn(b, self.record())         # one beat could be the machine going down
        running_rooms.refresh()
        self.assertEqual(list(self.record()), [a])

    def test_it_keeps_who_is_mid_turn(self):
        rid = self.room()
        pid = self.running(rid, "claude", "idle")
        running_rooms.refresh()
        self.states[pid] = ("working", "hook")
        self.assertTrue(running_rooms.refresh())
        self.assertEqual(self.record()[rid]["agents"][0]["state"], "working")
        self.assertFalse(running_rooms.refresh())       # nothing changed: not written


class _Seat:
    """A session as the PTY host client hands it out."""

    def __init__(self, pty_id, room="room-x", identity="claude", idle=True):
        self.id = pty_id
        self.meta = {"room": room, "identity": identity}
        self.last_output = time.time() - (60 if idle else 0)
        self.typed = []

    def alive(self):
        return True

    def send_line(self, text):
        self.typed.append(text)
        return True


class TypingAlarm(_Hub):
    def setUp(self):
        super().setUp()
        hub_typing.reset()
        self.addCleanup(hub_typing.reset)
        p = mock.patch.object(dashboard.rotation, "IDLE_S", 5.0)
        p.start()
        self.addCleanup(p.stop)
        self.po = self.room(title="PO")
        self.task = self.room(title="Task")
        self.projects.append({"id": "p1", "name": "P", "poRoomId": self.po})
        chatroom.patch_room(self.task, projectId="p1")

    def notices(self, rid):
        return [m["text"] for m in chatroom.get_room(rid, public=False)["messages"]
                if m.get("noticeKind") == hub_typing.NOTICE_KIND]

    def refuse(self, cause, idle=True, n=1):
        for _ in range(n):
            hub_typing.record(_Seat("p1", self.task, idle=idle), False, cause)

    def test_repeated_refusals_to_an_idle_agent_raise_it_once_and_it_clears(self):
        self.refuse("busy", n=2)
        self.assertEqual(hub_typing.by_room(), {})
        self.refuse("busy")
        alarm = hub_typing.by_room()[self.task]
        self.assertIn("still marked as being typed", alarm["reason"])
        self.assertEqual(alarm["identity"], "claude")
        self.refuse("busy", n=3)                         # once per cause
        self.assertEqual(len(self.notices(self.po)), 1)
        self.assertIn("Hub lines are not reaching claude", self.notices(self.po)[0])
        log = (self.dir / "logs" / "hub-typing.log").read_text(encoding="utf-8")
        self.assertEqual(log.count("ALARM (busy)"), 1)
        hub_typing.record(_Seat("p1", self.task), True)
        self.assertEqual(hub_typing.by_room(), {})
        self.assertIn("reach the agents again", self.notices(self.po)[-1])
        self.assertIn("cleared (busy)",
                      (self.dir / "logs" / "hub-typing.log").read_text(encoding="utf-8"))

    def test_an_alarm_on_an_agent_that_is_gone_clears(self):
        self.refuse("busy", n=3)
        self.assertIn(self.task, hub_typing.by_room())
        self.refuse("gone")                             # its terminal ended
        self.assertEqual(hub_typing.by_room(), {})
        self.assertIn("no longer running", self.notices(self.po)[-1])
        self.assertIn("cleared (busy): the agents it named are no longer running",
                      (self.dir / "logs" / "hub-typing.log").read_text(encoding="utf-8"))

    def test_a_stopped_task_leaves_the_alarm(self):
        self.refuse("own-seat")
        self.assertIn(self.task, hub_typing.by_room())
        with mock.patch.object(dashboard.ptyrun, "kill", lambda pid: True), \
                mock.patch.object(dashboard.ptyrun, "forget_death", lambda pid: None, create=True):
            dashboard.stop_task(self.task)
        self.assertEqual(hub_typing.by_room(), {})
        self.assertEqual(hub_typing.view()["alarms"], [])
        self.assertIn("no longer running", self.notices(self.po)[-1])

    def test_old_refusals_age_out(self):
        self.refuse("busy", n=2)
        real = time.time
        with mock.patch.object(hub_typing.time, "time", lambda: real() + 700):
            self.refuse("busy")
        self.assertEqual(hub_typing.by_room(), {})

    def test_what_is_not_a_fault_is_not_counted(self):
        self.refuse("gone", n=5)
        self.refuse("rotating", n=5)
        self.refuse("busy", idle=False, n=5)            # it was working: not idle
        self.assertEqual(hub_typing.by_room(), {})
        self.assertEqual(self.notices(self.po), [])

    def test_the_10_13_fault_raises_it_at_the_first_line(self):
        """A lookup of the agent's own terminal that does not give it back:
        reserve_hub_line refuses its own seat, as every line did from 10-13
        18:11 to 10-14 07:31."""
        chatroom.patch_participant(self.task, "claude", {"ptyId": "p1"})
        sess = _Seat("p1", self.task)
        self.ptys.clear()                               # the lookup finds nothing
        self.assertFalse(dashboard._type_input(sess, "[digest] hello", record=False))
        self.assertEqual(sess.typed, [])
        alarm = hub_typing.by_room()[self.task]
        self.assertIn("rejected the agent's own terminal", alarm["reason"])
        self.assertEqual(len(self.notices(self.po)), 1)
        self.assertEqual(hub_typing.view()["agents"][0]["refused"], 1)

    def test_a_line_that_arrives_is_counted(self):
        chatroom.patch_participant(self.task, "claude", {"ptyId": "p1"})
        sess = _Seat("p1", self.task)
        self.ptys["p1"] = sess
        self.assertTrue(dashboard._type_input(sess, "hello", record=False))
        self.assertEqual(sess.typed, ["hello"])
        self.assertEqual(hub_typing.view()["agents"][0]["ok"], 1)

    def test_it_is_a_needs_you_item(self):
        src = (dashboard.Path(dashboard.__file__).parent / "attention.py").read_text(encoding="utf-8")
        self.assertIn("_d.hub_typing.by_room()", src)


class PreflightTypingCheck(unittest.TestCase):
    """A real throwaway agent on in-process PTYs."""

    def test_a_hub_line_reaches_the_throwaway_agent(self):
        out = dashboard.preflight_typing_check(timeout=30)
        self.assertEqual(out, {"ok": True, "detail": "a hub line reached a throwaway agent"})

    def test_a_fresh_session_object_per_lookup_fails_it(self):
        real_get = dashboard.ptyrun.get

        def fresh(pid):
            sess = real_get(pid)
            return types.SimpleNamespace(**{k: getattr(sess, k) for k in ("id", "alive", "tail")}) \
                if sess else None
        with mock.patch.object(dashboard.ptyrun, "get", fresh):
            out = dashboard.preflight_typing_check(timeout=30)
        self.assertFalse(out["ok"])
        self.assertIn("two session objects", out["detail"])

    def test_a_seat_check_that_refuses_the_own_terminal_fails_it(self):
        # The lookup itself is sound; the seat match is what regressed.
        with mock.patch.object(dashboard.rotation, "_pty", lambda part: None):
            out = dashboard.preflight_typing_check(timeout=30)
        self.assertFalse(out["ok"])
        self.assertIn("seat check refuses the agent's own terminal (own-seat)", out["detail"])


if __name__ == "__main__":
    unittest.main()
