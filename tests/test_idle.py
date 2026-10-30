"""A running task that does nothing: the PO hears of it, the board says so (#292).

The hub's own parts are real (rooms, columns, idle.json, the tool); what an
agent is doing is set per test: screen clock, transcript, a turn running.
"""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import digest
import ensemble_tools
import idle
import stall

T0 = 1_800_000_000.0
MIN = 60


class _Hub(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = root / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)
        projects_root = mock.patch.object(dashboard, "PROJECTS_ROOT", root / "projects")
        projects_root.start()
        self.addCleanup(projects_root.stop)
        self.projects = [{"id": "p-trade", "name": "Trading", "poRoomId": ""}]
        self.links: dict[str, str] = {}
        self.alive: set[str] = set()
        self.idle_s: dict[str, float] = {}      # pty -> seconds the screen is quiet
        self.working: set[str] = set()          # ptys in a turn
        self.wrote = False                      # the transcript moved
        self.lines: list[str] = []
        self.type_ok = True
        self.cards: dict[str, list] = {}
        self.minutes = 20
        self.seen_ask = True
        self.stall_rec: dict[str, dict] = {}
        self.draining = False
        self.resuming: set[str] = set()
        rot = dashboard.rotation

        def sess_of(part):
            pid = part.get("ptyId") or ""
            if pid not in self.alive:
                return None
            return types.SimpleNamespace(info=lambda: {"idleSeconds": self.idle_s.get(pid, 0)})

        def type_po(room, line):
            if self.type_ok:
                self.lines.append(line)
            return self.type_ok

        for patch in (
                mock.patch.object(dashboard, "DASHBOARD_DIR", root),
                mock.patch.object(dashboard.restart_drain, "holding", side_effect=lambda: self.draining),
                mock.patch.object(dashboard.restart_drain, "_resuming", side_effect=lambda rid: rid in self.resuming),
                mock.patch.object(idle, "_STARTED", 0.0),
                mock.patch.object(idle, "_UNSAVED", None),
                mock.patch.object(dashboard, "load_projects", side_effect=lambda: self.projects),
                mock.patch.object(dashboard, "load_session_projects", side_effect=lambda: self.links),
                mock.patch.object(dashboard, "load_archived", side_effect=set),
                mock.patch.object(dashboard, "load_settings",
                                  side_effect=lambda: {"idleAlertMin": self.minutes}),
                mock.patch.object(dashboard, "room_po_id", side_effect=lambda r: self.po_of(r)),
                mock.patch.object(dashboard, "_room_is_live", side_effect=lambda r: self.live(r)),
                mock.patch.object(dashboard, "_room_has_linked_agent", return_value=False),
                mock.patch.object(dashboard.ptyrun, "get", side_effect=lambda pid: None),
                mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=[]),
                mock.patch.object(dashboard.provider_retry, "status", return_value=None),
                mock.patch.object(dashboard.asks, "open_cards",
                                  side_effect=lambda r: self.cards.get(r.get("id"), [])),
                mock.patch.object(rot, "_pty", side_effect=sess_of),
                mock.patch.object(rot, "_idle",
                                  side_effect=lambda part, _t: part.get("ptyId") not in self.working),
                mock.patch.object(rot, "_transcript_of",
                                  return_value=(root / "t.jsonl", lambda p: [])),
                mock.patch.object(rot, "_submitted_lately", return_value=False),
                mock.patch.object(rot, "is_rotating", return_value=False),
                mock.patch.object(rot, "awaiting_handover", return_value=False),
                mock.patch.object(stall, "limit_hit", return_value=None),
                mock.patch.object(stall, "_busy", return_value=False),
                mock.patch.object(stall, "own_work", return_value=0),
                mock.patch.object(stall, "record", side_effect=lambda rid: self.stall_rec.get(rid)),
                mock.patch.object(stall, "_wrote_since", side_effect=lambda p, ts: self.wrote),
                mock.patch.object(stall, "_type_po", side_effect=type_po),
                mock.patch.object(idle, "_po_read_since", side_effect=lambda r, ts: self.seen_ask)):
            patch.start()
            self.addCleanup(patch.stop)

    def po_of(self, room):
        return "room-po" if room.get("noPo") is None else ""

    def live(self, room):
        full = chatroom.get_room(room["id"], public=False, copy_room=False, messages=False)
        return any(p.get("ptyId") in self.alive for p in (full or room).get("participants") or [])

    def task(self, title="Fills", workflow="inprogress", pty="pty-1", at=0.0, **more):
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude",
                                            "role": "engineer"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full.update(mode="solo", launched=True, workflow=workflow, workflowAt=at, **more)
        chatroom.participant(full, "claude")["ptyId"] = pty
        chatroom.update_room(full)
        self.links[rid] = "p-trade"
        if pty:
            self.alive.add(pty)
        return rid

    def tick(self, now=T0):
        return idle.tick(now)

    def column(self, rid):
        return dashboard.workflow_of(chatroom.get_room(rid, public=False))

    def state(self):
        return idle._load()


class ReviewOne(_Hub):
    """Review 1: a restart, a drain, a hang, a save that fails, the setting off."""

    def refused(self):
        """The disk refuses idle.json alone, as a reader's open file does."""
        real = idle.durable.write_tmp

        def write_tmp(tmp, text):
            if tmp.name.startswith("idle."):
                raise OSError("refused")
            return real(tmp, text)
        return mock.patch.object(idle.durable, "write_tmp", side_effect=write_tmp)

    def marked(self):
        rid = self.task(pty="")
        chatroom.patch_room(rid, waitMark={"kind": "time", "until": T0 + 3600, "at": T0 - 600})
        idle._save({"tasks": {}, "projects": {}, "resolved": {}, "stopped": {rid: T0 - 3600}})
        return rid

    def mark_of(self, rid):
        return chatroom.get_room(rid, public=False).get("waitMark")

    def test_a_look_from_before_the_restart_does_not_stop_a_room(self):
        rid = self.marked()
        with mock.patch.object(idle, "_STARTED", T0 - 60):
            self.tick()
        self.assertTrue(self.mark_of(rid))
        self.assertEqual(self.column(rid), "inprogress")
        self.assertNotIn(rid, self.state()["stopped"])

    def test_a_room_resuming_is_not_stopped(self):
        rid = self.marked()
        self.resuming.add(rid)
        self.tick()
        self.assertTrue(self.mark_of(rid))
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_restore_outlasting_the_grace_stops_no_room(self):
        rid = self.marked()
        with mock.patch.object(dashboard, "_ROOMS_RESTORING", 1):
            self.tick()
        self.assertTrue(self.mark_of(rid))
        self.assertEqual(self.column(rid), "inprogress")
        self.assertNotIn(rid, self.state()["stopped"])

    def test_a_restore_counts_while_it_runs_even_if_it_fails(self):
        h = dashboard.Handler.__new__(dashboard.Handler)
        seen = []

        def now(*a, **k):
            seen.append(dashboard._ROOMS_RESTORING)
            raise ValueError("no")
        with mock.patch.object(dashboard.Handler, "_bring_back_rooms_now", now), \
                self.assertRaises(ValueError):
            h._bring_back_rooms([], fresh=True, wake_room="", po_rooms=set())
        self.assertEqual((seen, dashboard._ROOMS_RESTORING), ([1], 0))

    def test_after_the_grace_two_looks_still_stop_it(self):
        rid = self.marked()
        self.tick()
        self.assertIsNone(self.mark_of(rid))
        self.assertEqual(self.column(rid), "waiting")

    def test_a_drain_is_not_idle(self):
        rid = self.task()
        self.idle_s["pty-1"] = 60 * MIN
        self.draining = True
        self.assertEqual(self.tick(), {})
        self.assertEqual(self.lines, [])
        self.assertEqual(self.column(rid), "inprogress")
        self.draining = False
        self.tick()
        self.assertEqual(len(self.lines), 1)

    def test_a_turn_that_never_closed_alerts_after_an_hour(self):
        rid = self.task()
        self.working.add("pty-1")
        self.idle_s["pty-1"] = 59 * MIN
        self.tick()
        self.assertEqual(self.lines, [])
        self.idle_s["pty-1"] = 61 * MIN
        self.tick()
        self.assertEqual(len(self.lines), 1)
        self.assertIn("turn never ended", self.lines[0])
        self.assertEqual(self.column(rid), "waiting")

    def test_with_a_limit_over_the_hour_the_limit_decides(self):
        self.minutes = 90
        self.task()
        self.working.add("pty-1")
        self.idle_s["pty-1"] = 80 * MIN
        self.tick()
        self.assertEqual(self.lines, [])
        self.idle_s["pty-1"] = 95 * MIN
        self.tick()
        self.assertEqual(len(self.lines), 1)

    def test_the_board_reads_a_state_the_disk_refused(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        with mock.patch.object(idle.time, "sleep"), \
                self.refused():
            self.tick()
        self.assertEqual(idle.records()[rid]["alerts"], 1)

    def test_the_board_reads_a_refused_state_a_tick_is_changing(self):
        tasks = {}

        class Grows(dict):
            def get(self, *a):
                tasks["room-new"] = {}      # a tick adds a task during the read
                return super().get(*a)

        tasks["room-old"] = Grows(current=True)
        with mock.patch.object(idle, "_UNSAVED", {"tasks": tasks}):
            self.assertEqual(list(idle.records()), ["room-old"])

    def test_a_failed_save_does_not_repeat_the_line(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        with mock.patch.object(idle.time, "sleep"), \
                self.refused():
            self.tick()
            self.assertEqual(len(self.lines), 1)
            self.tick(T0 + 120)
        self.assertEqual(len(self.lines), 1)
        self.tick(T0 + 180)
        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.state()["tasks"][rid]["alerts"], 1)

    def test_off_drops_a_wait_that_ended(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        chatroom.patch_room(rid, waitMark={"kind": "time", "until": T0 - 1, "at": T0 - 600})
        self.minutes = 0
        self.tick()
        self.assertEqual(self.state()["resolved"], {})
        self.minutes = 20
        self.tick(T0 + 120)
        self.assertEqual(self.lines, [])


class WhatIsIdle(_Hub):
    def test_an_agent_idle_past_the_limit_alerts_once(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        self.tick()
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("[idle] "))
        self.assertIn("idle since", self.lines[0])
        self.assertIn("Send work, stop the task, or mark the wait.", self.lines[0])
        self.assertEqual(self.state()["tasks"][rid]["alerts"], 1)

    def test_short_of_the_limit_nothing_happens(self):
        rid = self.task()
        self.idle_s["pty-1"] = 19 * MIN
        self.tick()
        self.assertEqual(self.lines, [])
        self.assertEqual(self.column(rid), "inprogress")

    def test_each_reason_it_is_not_idle(self):
        rid = self.task()
        self.idle_s["pty-1"] = 50 * MIN
        rot = dashboard.rotation
        for name, patch in (
                ("working", mock.patch.object(rot, "_idle", return_value=False)),
                ("just typed", mock.patch.object(rot, "_submitted_lately", return_value=True)),
                ("handover", mock.patch.object(rot, "is_rotating", return_value=True)),
                ("retry", mock.patch.object(dashboard.provider_retry, "status", return_value={"x": 1})),
                ("command", mock.patch.object(stall, "own_work", return_value=1)),
                ("limit", mock.patch.object(stall, "limit_hit", return_value={"x": 1}))):
            with self.subTest(name), patch:
                self.tick()
                self.assertEqual(self.lines, [])

    def test_a_held_task_is_not_idle(self):
        for held in ({"status": "paused"}, {"paceHold": {"at": T0}}):
            with self.subTest(held):
                self.lines.clear()
                rid = self.task(pty=f"pty-{list(held)[0]}", **held)
                self.idle_s[f"pty-{list(held)[0]}"] = 60 * MIN
                self.tick()
                self.assertEqual(self.lines, [])
                self.assertEqual(self.column(rid), "inprogress")

    def test_a_stopped_task_never_alerts(self):
        self.task(pty="")
        self.tick()
        self.assertEqual(self.lines, [])

    def test_a_live_agent_in_done_or_review_alerts(self):
        for col in ("done", "inreview"):
            with self.subTest(col):
                self.lines.clear()
                self.projects[0]["x"] = col
                rid = self.task(workflow=col, pty=f"pty-{col}")
                self.idle_s[f"pty-{col}"] = 30 * MIN
                self.tick(T0 + (0 if col == "done" else 2 * 3600))
                self.assertEqual(len(self.lines), 1, self.state())
                self.assertEqual(self.column(rid), col)

    def test_an_ask_the_po_read_is_quiet_an_unread_one_alerts(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        chatroom.record_report(rid, "claude", "question", "Which plan?")
        self.tick(time.time())
        self.assertEqual(self.lines, [])
        self.seen_ask = False
        self.tick(time.time() + 3700)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("its question is not read yet", self.lines[0])

    def test_an_open_card_is_quiet(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.cards[rid] = [{"mid": f"{rid}:1"}]
        self.tick()
        self.assertEqual(self.lines, [])

    def test_a_finished_report_alerts_after_4_hours(self):
        rid = self.task(workflow="inreview")
        self.idle_s["pty-1"] = 30 * MIN
        chatroom.record_report(rid, "claude", "completed", "Done.")
        t = time.time()
        self.tick(t + 3 * 3600)
        self.assertEqual(self.lines, [])
        self.tick(t + 4 * 3600 + 60)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("finished report not acted on", self.lines[0])

    def test_settings_0_is_off(self):
        rid = self.task()
        self.idle_s["pty-1"] = 300 * MIN
        self.minutes = 0
        self.tick()
        self.assertEqual(self.lines, [])
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_failed_read_says_nothing(self):
        self.task()
        self.idle_s["pty-1"] = 300 * MIN
        with mock.patch.object(dashboard.rotation, "_transcript_of", side_effect=OSError("gone")):
            self.tick()
        self.assertEqual(self.lines, [])

    def test_no_po_no_line_but_the_column_moves(self):
        rid = self.task(noPo=True)
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        self.assertEqual(self.lines, [])
        self.assertEqual(self.column(rid), "waiting")


class TheLine(_Hub):
    def test_tasks_due_together_share_a_line(self):
        a, b = self.task("A", pty="pty-a"), self.task("B", pty="pty-b")
        self.idle_s.update({"pty-a": 30 * MIN, "pty-b": 40 * MIN})
        self.tick()
        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.lines[0].count("idle since"), 2)
        self.assertEqual({self.state()["tasks"][r]["alerts"] for r in (a, b)}, {1})

    def test_one_line_an_hour_per_project(self):
        self.task("A", pty="pty-a")
        self.idle_s["pty-a"] = 30 * MIN
        self.tick()
        self.task("B", pty="pty-b")
        self.idle_s["pty-b"] = 30 * MIN
        self.tick(T0 + 10 * MIN)
        self.assertEqual(len(self.lines), 1)
        self.idle_s.update({"pty-a": 100 * MIN, "pty-b": 90 * MIN})
        self.tick(T0 + 61 * MIN)
        self.assertEqual(len(self.lines), 2)
        self.assertIn("idle since", self.lines[1])

    def test_repeats_back_off(self):
        rid = self.task()
        now, gaps, last = T0, [], None
        for _ in range(24 * 60):            # a day, a minute a tick
            self.idle_s["pty-1"] = 21 * MIN + (now - T0)
            before = len(self.lines)
            self.tick(now)
            if len(self.lines) > before:
                if last:
                    gaps.append(round((now - last) / MIN))
                last = now
            now += MIN
        self.assertGreaterEqual(gaps[0], 60)    # the floor
        self.assertEqual(gaps[1:3], [120, 360])
        self.assertEqual(self.state()["tasks"][rid]["alerts"], len(gaps) + 1)

    def test_a_failed_type_stays_pending(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.type_ok = False
        self.tick()
        self.assertEqual(self.state()["tasks"][rid]["alerts"], 0)
        self.type_ok = True
        self.tick(T0 + MIN)
        self.assertEqual(len(self.lines), 1)

    def test_too_many_for_one_line_wait_for_the_next(self):
        for i in range(30):
            self.task("Long title " * 3, pty=f"pty-{i}")
            self.idle_s[f"pty-{i}"] = 30 * MIN
        self.tick()
        self.assertLessEqual(len(self.lines[0]), idle._LINE_MAX + 60)
        told = sum(1 for r in self.state()["tasks"].values() if r["alerts"])
        self.assertLess(told, 30)

    def test_the_stalled_line_counts_as_the_first_alert(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.stall_rec[rid] = {"idleSince": T0 - 30 * MIN, "toldAt": T0 - 5 * MIN}
        self.tick()
        self.assertEqual(self.lines, [])
        rec = self.state()["tasks"][rid]
        self.assertEqual(rec["alerts"], 1)
        self.assertEqual(rec["nextAt"], T0 - 5 * MIN + 30 * MIN)

    def test_a_stalled_echo_does_not_restart_the_clock(self):
        rid = self.task()
        self.idle_s["pty-1"] = 2 * MIN              # the nudge reset the screen
        self.stall_rec[rid] = {"idleSince": T0 - 40 * MIN}
        self.tick()
        self.assertEqual(len(self.lines), 1)

    def test_a_restart_repeats_nothing(self):
        self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        idle._LAST = 0.0                            # a new process, same file
        self.idle_s["pty-1"] = 1 * MIN              # the screen clock started over
        self.tick(T0 + 2 * MIN)
        self.assertEqual(len(self.lines), 1)

    def test_a_new_stretch_starts_over(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        self.working.add("pty-1")
        self.tick(T0 + 5 * MIN)
        self.assertNotIn(rid, self.state()["tasks"])
        self.working.clear()
        self.idle_s["pty-1"] = 25 * MIN
        self.tick(T0 + 70 * MIN)
        self.assertEqual(len(self.lines), 2)

    def test_merged_and_left_running_gets_one_alert(self):
        rid = self.task(workflow="done", mergedAt=T0 - 3 * 3600)
        now = T0
        for _ in range(48):
            self.idle_s["pty-1"] = 30 * MIN + (now - T0)
            self.tick(now)
            now += 30 * MIN
        self.assertEqual(len(self.lines), 1)
        self.assertIn("merged, agent left running: stop it", self.lines[0])
        self.assertEqual(self.column(rid), "done")


class Marks(_Hub):
    def mark(self, rid, **m):
        chatroom.patch_room(rid, waitMark={"at": T0, "by": "po", "what": "", "until": 0, **m})

    def idle_tick(self, now):
        self.idle_s["pty-1"] = 300 * MIN
        return self.tick(now)

    def test_a_mark_goes_to_waiting_at_once_and_alerts_nothing(self):
        rid = self.task()
        self.mark(rid, kind="time", until=T0 + 3 * 3600)
        self.idle_s["pty-1"] = 1 * MIN
        self.tick()
        self.assertEqual(self.column(rid), "waiting")
        self.idle_tick(T0 + 3600)
        self.assertEqual(self.lines, [])

    def test_the_time_comes_wakes_once(self):
        rid = self.task()
        self.mark(rid, kind="time", until=T0 + 3600)
        self.idle_tick(T0)
        self.idle_tick(T0 + 3660)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("wait over", self.lines[0])
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.idle_tick(T0 + 3720)
        self.assertEqual(len(self.lines), 1)

    def test_the_other_task_done_wakes_once(self):
        other = self.task("Other", pty="", workflow="inprogress")
        rid = self.task()
        self.mark(rid, kind="task", taskId=other, what="#301")
        self.idle_tick(T0)
        self.assertEqual(self.lines, [])
        chatroom.patch_room(other, workflow="done")
        self.idle_tick(T0 + 60)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("#301 is done", self.lines[0])

    def test_the_card_answered_wakes_once(self):
        rid = self.task()
        self.cards[rid] = [{"mid": "m1"}]
        self.mark(rid, kind="card", mids=["m1"])
        self.idle_tick(T0)
        self.assertEqual(self.lines, [])
        self.cards[rid] = []
        self.idle_tick(T0 + 60)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("the card was answered", self.lines[0])

    def test_background_ends_silently_when_the_owner_works(self):
        rid = self.task()
        self.mark(rid, kind="background")
        self.working.add("pty-1")           # the turn that marked it runs on
        self.tick(T0)
        self.assertIsNotNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.working.clear()
        self.idle_tick(T0 + 60)
        self.working.add("pty-1")
        self.idle_s["pty-1"] = 0
        self.tick(T0 + 120)
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_background_without_a_time_never_goes_silent(self):
        rid = self.task()
        self.mark(rid, kind="background")
        self.idle_tick(T0)
        self.idle_tick(T0 + idle.BACKGROUND_CAP_S + 60)
        self.assertEqual(len(self.lines), 1)
        self.assertIn("wait over", self.lines[0])
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))

    def test_a_mark_survives_a_restart(self):
        rid = self.task()
        self.mark(rid, kind="time", until=T0 + 3600)
        self.idle_tick(T0)
        idle._LAST = 0.0
        self.idle_tick(T0 + 60)
        self.assertEqual(chatroom.get_room(rid, public=False)["waitMark"]["kind"], "time")
        self.assertEqual(self.lines, [])

    def test_a_stopped_task_loses_its_mark_with_no_alert(self):
        rid = self.task()
        self.mark(rid, kind="time", until=T0 + 60)
        self.alive.clear()
        self.tick(T0)
        self.tick(T0 + idle.STOP_LOOK_S + 1)
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_the_stall_check_leaves_a_marked_task_alone(self):
        rid = self.task()
        self.mark(rid, kind="time", until=T0 + 3600)
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(stall.why_not(room, "claude"), "a marked wait")


class TheColumn(_Hub):
    def test_idle_goes_to_waiting_and_back_when_it_works(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        self.assertEqual(self.column(rid), "waiting")
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workflowAuto"]["why"], "idle")
        self.assertEqual(float(room.get("workflowAt") or 0), 0)     # as settle_finished
        self.working.add("pty-1")
        self.tick(T0 + 60)
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_drag_in_the_stretch_wins(self):
        rid = self.task(at=T0 - 5 * MIN)        # moved after the agent went quiet
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_card_dragged_to_waiting_stays(self):
        rid = self.task(workflow="waiting", at=T0)
        self.working.add("pty-1")
        self.tick(T0 + 60)
        self.assertEqual(self.column(rid), "waiting")

    def test_stopped_goes_after_two_looks_in_every_project(self):
        self.projects.append({"id": "p-b", "name": "B", "poRoomId": ""})
        rid = self.task(pty="")
        other = self.task("Other", pty="")
        self.links[other] = "p-b"
        self.tick(T0)
        self.assertEqual(self.column(rid), "inprogress")
        self.tick(T0 + idle.STOP_LOOK_S + 1)
        self.assertEqual((self.column(rid), self.column(other)), ("waiting", "waiting"))
        self.assertEqual(self.lines, [])

    def test_a_stopped_task_with_a_paced_hold_stays(self):
        rid = self.task(pty="", paceHold={"at": T0})
        self.tick(T0)
        self.tick(T0 + idle.STOP_LOOK_S + 1)
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_stopped_finished_task_is_not_waiting(self):
        rid = self.task(pty="")
        chatroom.record_report(rid, "claude", "completed", "Done.")
        self.tick(T0)
        self.tick(T0 + idle.STOP_LOOK_S + 1)
        self.assertEqual(self.column(rid), "inprogress")      # In review's to take

    def test_completed_in_waiting_goes_to_in_review(self):
        rid = self.task(workflow="waiting", pty="")
        chatroom.record_report(rid, "claude", "completed", "Done.")
        self.assertTrue(dashboard.settle_finished(chatroom.get_room(rid, public=False)))
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workflow"], "inreview")
        self.assertEqual(room["workflowAuto"]["from"], "waiting")

    def test_board_news_ignores_the_swap(self):
        base = {"id": "t", "title": "T", "status": "running", "attention": "", "merged": False,
                "head": "", "reportKind": "", "reportTs": 0}
        before = {"t": {**base, "column": "inprogress"}}
        self.assertEqual(digest.diff(before, [{**base, "column": "waiting"}]), [])
        self.assertTrue(digest.diff(before, [{**base, "column": "done"}]))


class RunningNeverReadsAsDone(_Hub):
    def test_each_source_moves_it(self):
        rid = self.task(workflow="done", at=1000)
        self.assertTrue(dashboard.reopen_done(rid, "x"))
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workflow"], "inprogress")
        self.assertGreater(room["workflowAt"], 1000)
        self.assertEqual(room["workflowAuto"]["from"], "done")

    def test_only_done_moves_and_never_a_pruned_task(self):
        rid = self.task(workflow="inreview")
        self.assertFalse(dashboard.reopen_done(rid, "x"))
        rid = self.task(workflow="done", pty="", pruned=True)
        self.assertFalse(dashboard.reopen_done(rid, "x"))

    def test_a_line_typed_moves_it_a_hub_line_does_not(self):
        rid = self.task(workflow="done")
        with mock.patch.object(dashboard.sends, "mark"):
            dashboard.note_answer(rid, "claude")    # rotation.py's held line: no origin
            dashboard._typed_sends(rid, [{"text": "x", "key": "k", "origin": "hub"}])
            self.assertEqual(self.column(rid), "done")
            dashboard._typed_sends(rid, [{"text": "x", "key": "k", "origin": "po"}])
        self.assertEqual(self.column(rid), "inprogress")

    def _handler(self):
        return dashboard.Handler.__new__(dashboard.Handler)

    def _resume(self, started, **kw):
        rid = self.task(workflow="done", pty="")
        h = self._handler()
        h._start_or_resume_room = mock.Mock(**started)
        self.addCleanup(dashboard._RESUMES.pop, rid, None)
        try:
            h._resume_room(chatroom.get_room(rid, public=False), **kw)
        except dashboard.StartRoomError:
            pass
        return self.column(rid)

    def test_a_resume_moves_it_once_an_agent_is_up(self):
        up = {"return_value": [{"identity": "claude"}]}
        self.assertEqual(self._resume(up, text="go", origin="po"), "inprogress")
        self.assertEqual(self._resume(up), "inprogress")
        self.assertEqual(self._resume(up, quiet=True), "done")
        self.assertEqual(self._resume(up, text="go", origin="hub"), "done")

    def test_a_refused_resume_leaves_it(self):
        refused = {"side_effect": dashboard.StartRoomError("no")}
        self.assertEqual(self._resume(refused, text="go", origin="po"), "done")
        self.assertEqual(self._resume({"return_value": []}, text="go", origin="po"), "done")

    def test_a_resume_held_for_a_restart_leaves_it(self):
        self.draining = True
        with mock.patch.object(dashboard.restart_drain, "try_hold", return_value=True):
            self.assertEqual(self._resume({"return_value": [{}]}, text="go", origin="po"), "done")

    def test_a_start_moves_it_once_launched(self):
        rid = self.task(workflow="done")
        room = chatroom.get_room(rid, public=False)
        h = self._handler()
        info = {"sessionId": "s", "cwd": "c", "ptyId": "pty-1"}
        with mock.patch.object(dashboard, "apply_first_launch_allocation"):
            h._launch_room_agent_pty = mock.Mock(side_effect=OSError("no"))
            with self.assertRaises(OSError):
                h._start_room_now(room)
            self.assertEqual(self.column(rid), "done")
            h._launch_room_agent_pty = mock.Mock(return_value=info)
            h._start_room_now(chatroom.get_room(rid, public=False))
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_chat_message_moves_it_only_when_it_reached_someone(self):
        for msg, rung, col in (({"from": "ensemble"}, ["claude"], "done"),
                               ({"from": "claude", "kind": "report"}, ["claude"], "done"),
                               ({"from": "claude"}, ["reviewer"], "done"),  # the owner's own
                               ({"from": "reviewer"}, [], "done"),          # a late verdict, stopped
                               ({"from": "po"}, ["claude"], "inprogress")):
            with self.subTest(msg):
                rid = self.task(workflow="done", pty="")
                h = self._handler()
                h._ring = lambda *a, **k: rung
                h._ring_recipients(rid, {"recipients": ["claude"], "message": msg})
                self.assertEqual(self.column(rid), col)

    def test_a_reviewer_held_or_refused_is_not_reached(self):
        for review, col in (({"held": True}, "done"), ({"limited": True}, "done"),
                            (None, "done"), ({"n": 1}, "inprogress")):
            with self.subTest(review):
                rid = self.task(workflow="done", pty="")
                h = self._handler()
                h._start_review = lambda *a: review
                with mock.patch.object(chatroom, "is_on_mention", return_value=True), \
                        mock.patch.object(dashboard, "_pty_alive", return_value=False):
                    h._ring_recipients(rid, {"recipients": ["reviewer"], "message": {"from": "po"}})
                self.assertEqual(self.column(rid), col)

    def test_no_recipients_moves_nothing(self):
        rid = self.task(workflow="done", pty="")
        self._handler()._ring_recipients(rid, {"recipients": [], "message": {"from": "po"}})
        self.assertEqual(self.column(rid), "done")


class Tool(_Hub):
    def ctx(self, rid, identity="claude"):
        room = chatroom.get_room(rid, public=False)
        return {"room": room, "identity": identity, "projectId": "p-trade",
                "part": chatroom.participant(room, identity)}

    def mark(self, rid, ident="claude", **args):
        return ensemble_tools._mark_wait(self.ctx(rid, ident), args, None)

    def test_it_is_a_common_tool(self):
        self.assertIn("ensemble_mark_wait", ensemble_tools.COMMON_TOOL_NAMES)
        self.assertIn("ensemble_mark_wait", ensemble_tools._IMPL)
        self.assertIn("ensemble_mark_wait", [t["name"] for t in ensemble_tools.COMMON_TOOLS])

    def test_an_owner_marks_its_own_with_each_kind(self):
        rid = self.task()
        self.assertEqual(self.mark(rid, kind="time", minutes=30)["waitMark"]["kind"], "time")
        self.assertEqual(self.mark(rid, kind="background")["reads"], "Waiting on background work")
        self.cards[rid] = [{"mid": "m1"}]
        self.assertEqual(self.mark(rid, kind="card")["waitMark"]["mids"], ["m1"])
        other = self.task("Other", pty="")
        self.assertEqual(self.mark(rid, kind="task", task=other)["waitMark"]["taskId"], other)
        self.assertIsNone(self.mark(rid, kind="clear")["waitMark"])
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))

    def test_bad_input_raises(self):
        rid = self.task()
        for args in ({"kind": "nap"}, {"kind": "time"}, {"kind": "time", "minutes": -1},
                     {"kind": "time", "until": "soon"}, {"kind": "task"},
                     {"kind": "task", "task": rid}, {"kind": "card"},
                     {"kind": "time", "until": "2001-01-01T00:00:00+00:00"}):
            with self.subTest(args), self.assertRaises(ensemble_tools.ToolError):
                self.mark(rid, **args)

    def test_a_stopped_task_is_refused_and_its_mark_can_still_be_cleared(self):
        rid = self.task(pty="")
        with self.assertRaisesRegex(ensemble_tools.ToolError, "stopped"):
            self.mark(rid, kind="time", minutes=30)
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertTrue(self.mark(rid, kind="clear")["ok"])

    def test_an_unknown_card_is_refused(self):
        rid = self.task()
        self.cards[rid] = [{"mid": "m1"}]
        with self.assertRaisesRegex(ensemble_tools.ToolError, "no open card m2"):
            self.mark(rid, kind="card", card="m2")
        self.assertEqual(self.mark(rid, kind="card", card="m1")["waitMark"]["mids"], ["m1"])
        self.cards["room-po"] = [{"mid": "m9"}]
        with mock.patch.object(chatroom, "get_room", side_effect=self._po_room()):
            self.assertEqual(self.mark(rid, kind="card", card="m9")["waitMark"]["mids"], ["m9"])

    def _po_room(self):
        real = chatroom.get_room
        return lambda rid, *a, **k: ({"id": rid} if rid == "room-po" else real(rid, *a, **k))

    def test_a_clock_time_past_today_means_tomorrow(self):
        now = T0
        at = time.strftime("%H:%M", time.localtime(now - 3600))
        ts = ensemble_tools._wait_until({"until": at}, now)
        self.assertGreater(ts, now)
        self.assertLessEqual(ts - now, 86400)

    def test_another_task_needs_the_po(self):
        rid, other = self.task(), self.task("Other", pty="pty-2")
        with self.assertRaises(ensemble_tools.ToolError):
            self.mark(rid, taskId=other, kind="background")
        with mock.patch.object(ensemble_tools, "_is_project_po", return_value=True):
            self.assertTrue(self.mark(rid, taskId=other, kind="background")["ok"])

    def test_a_po_stays_in_its_project(self):
        self.projects.append({"id": "p-b", "name": "B", "poRoomId": ""})
        rid, other = self.task(), self.task("Other", pty="")
        self.links[other] = "p-b"
        with mock.patch.object(ensemble_tools, "_is_project_po", return_value=True), \
                self.assertRaises(ensemble_tools.ToolError):
            self.mark(rid, taskId=other, kind="background")

    def test_the_po_view_and_the_attention_list_carry_idle(self):
        rid = self.task()
        self.idle_s["pty-1"] = 30 * MIN
        self.tick()
        rows = ensemble_tools._idle_rows("p-trade", {})
        self.assertEqual([r["taskId"] for r in rows], [rid])
        self.assertEqual(ensemble_tools._idle_rows("p-none", {}), [])
        view = ensemble_tools._po_wait_view(chatroom.get_room(rid, public=False),
                                            ({}, {}, idle.records()))
        self.assertEqual(view["idle"]["alerts"], 1)


class Parts(_Hub):
    def test_the_input_kind(self):
        self.assertIn(("[idle] ", "idle"), dashboard.HUB_INPUT_KINDS)

    def test_the_setting_clamps(self):
        self.assertEqual(dashboard._SETTINGS_DEFAULTS["idleAlertMin"], 20)
        f = Path(self.temp.name) / "settings.json"
        with mock.patch.object(dashboard, "SETTINGS_FILE", f):
            for given, want in ((0, 0), (-3, 0), (2, 5), (20, 20), (9999, 1440)):
                with self.subTest(given):
                    self.assertEqual(dashboard.save_settings({"idleAlertMin": given})["idleAlertMin"], want)

    def test_the_column_exists(self):
        self.assertEqual(ensemble_tools.WORKFLOW_NAMES.index("waiting"),
                         ensemble_tools.WORKFLOW_NAMES.index("inprogress") + 1)
        self.assertEqual(dashboard.WORKFLOW_LABELS["waiting"], "Waiting")
        self.assertEqual(dashboard.normalize_workflow("Waiting"), "waiting")

    def test_no_state_file_is_empty(self):
        self.assertEqual(idle._load(), {"tasks": {}, "projects": {}, "resolved": {}, "stopped": {}})


class PoFindings(_Hub):
    """The PO's review of 10-30 (PO-FINDINGS-292.md)."""

    def alerted(self, **more):
        rid = self.task(**more)
        self.idle_s["pty-1"] = 25 * MIN
        self.tick()
        self.assertEqual(len(self.lines), 1)
        return rid

    LATER = T0 + 2 * 3600       # past the floor and the back-off

    def test_stale_current_a_task_stopped_before_it_is_due(self):
        self.alerted()
        self.alive.clear()
        self.tick(self.LATER)
        self.assertEqual(len(self.lines), 1)

    def test_stale_current_rooms_a_restart_has_not_brought_back(self):
        rid = self.alerted()
        self.alive.clear()
        with mock.patch.object(idle, "_STARTED", self.LATER - 60):
            self.tick(self.LATER)
        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.state()["tasks"][rid]["alerts"], 1)   # the back-off is kept

    def test_stale_current_a_stopped_room_under_a_pacing_hold(self):
        rid = self.alerted()
        chatroom.patch_room(rid, paceHold={"at": T0})
        self.alive.clear()
        self.tick(self.LATER)
        self.assertEqual(len(self.lines), 1)

    def test_stale_current_a_failed_read_on_a_recorded_task(self):
        self.alerted()
        with mock.patch.object(dashboard.rotation, "_transcript_of", side_effect=OSError("no")):
            self.tick(self.LATER)
        self.assertEqual(len(self.lines), 1)

    def test_a_reworked_merged_task_is_no_merged_task(self):
        for more, stop_it in (({"specAt": T0 - 3600}, False),   # work after the merge
                              ({"mergedHead": "aaa"}, False),   # a commit after it
                              ({"mergedHead": "bbb"}, True)):   # nothing since
            with self.subTest(more):
                self.lines.clear()
                idle._save({"tasks": {}, "projects": {}, "resolved": {}, "stopped": {}})
                rid = self.task(workflow="done", at=T0 - 7200, mergedAt=T0 - 7200, **more)
                self.idle_s["pty-1"] = 25 * MIN
                with mock.patch.object(dashboard, "_git_out", return_value="bbb"):
                    self.tick()
                self.assertEqual("agent left running" in self.lines[0], stop_it)
                chatroom.patch_room(rid, archived=True)

    def test_a_reworked_merged_task_alerts_again(self):
        self.alerted(workflow="done", at=T0 - 7200, mergedAt=T0 - 7200, specAt=T0 - 3600)
        self.tick(self.LATER)
        self.assertEqual(len(self.lines), 2)

    def _unreadable(self, rid):
        real = chatroom.get_room
        return mock.patch.object(chatroom, "get_room", side_effect=lambda r, *a, **k: (
            None if r == rid else real(r, *a, **k)))

    def _wait_on_task(self):
        other = self.task("Other", pty="")
        rid = self.task()
        chatroom.patch_room(rid, waitMark={"kind": "task", "taskId": other, "what": "#301",
                                           "at": T0, "until": 0})
        self.idle_s["pty-1"] = 300 * MIN
        return rid, other

    def test_a_failed_read_keeps_a_wait_on_a_task(self):
        rid, other = self._wait_on_task()
        with self._unreadable(other):
            self.tick()
        self.assertTrue(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_a_task_really_gone_ends_the_wait(self):
        rid, other = self._wait_on_task()
        chatroom._room_path(other).unlink()
        self.tick()
        self.assertIsNone(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(len(self.lines), 1)

    def test_a_failed_read_keeps_a_wait_on_a_card(self):
        rid = self.task()
        chatroom.patch_room(rid, waitMark={"kind": "card", "mids": ["m1"], "at": T0, "until": 0})
        self.idle_s["pty-1"] = 300 * MIN
        chatroom._room_path("room-po").write_text("{nope", encoding="utf-8")
        self.tick()
        self.assertTrue(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_a_damaged_state_skips_the_tick_and_is_not_written(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        idle._state_file().write_text("{nope", encoding="utf-8")
        self.assertEqual(self.tick(), {})
        self.assertEqual(self.lines, [])
        self.assertEqual(self.column(rid), "inprogress")
        self.assertEqual(idle._state_file().read_text(encoding="utf-8"), "{nope")
        self.assertEqual(idle.records(), {})

    def test_a_background_wait_with_its_command_running_stays_in_waiting(self):
        rid = self.task()
        chatroom.patch_room(rid, waitMark={"kind": "background", "at": T0, "until": 0, "what": ""})
        self.idle_s["pty-1"] = 5 * MIN
        with mock.patch.object(stall, "own_work", return_value=1):
            self.tick(T0 + 60)
            self.assertEqual(self.column(rid), "waiting")
            self.tick(T0 + 120)
        self.assertEqual(self.column(rid), "waiting")

    def test_a_teammate_turn_leaves_a_marked_card_in_waiting(self):
        rid = self.task()
        chatroom.patch_room(rid, waitMark={"kind": "time", "at": T0, "until": T0 + 3600, "what": ""})
        self.idle_s["pty-1"] = 5 * MIN
        self.tick(T0 + 60)
        with mock.patch.object(chatroom, "agent_participants", return_value=[{"identity": "rev"}]), \
                mock.patch.object(stall, "_busy", return_value=True):
            self.tick(T0 + 120)
        self.assertEqual(self.column(rid), "waiting")

    def test_working_leaves_done_with_no_work_newer_than_the_move(self):
        rid = self.task(workflow="done", at=T0 - 60, specAt=T0 - 600)
        self.working.add("pty-1")
        self.tick()
        self.assertEqual(self.column(rid), "inprogress")

    def test_working_in_done_stays_when_merged(self):
        rid = self.task(workflow="done", at=T0 - 600, specAt=T0 - 300, mergedAt=T0 - 60)
        self.working.add("pty-1")
        self.tick()
        self.assertEqual(self.column(rid), "done")

    def test_an_archived_task_is_no_longer_listed(self):
        rid = self.alerted()
        self.assertIn(rid, idle.records())
        chatroom.patch_room(rid, archived=True)
        self.tick(T0 + 60)
        self.assertNotIn(rid, idle.records())
        self.assertEqual(ensemble_tools._idle_rows("*", {}), [])


class PoFindingsTwo(_Hub):
    """The PO's last read (PO-FINDINGS-292-2.md)."""

    def test_a_merged_card_sent_work_is_no_merged_task(self):
        # A: a solo send leaves no trace in the room but the reopening.
        rid = self.task(workflow="done", at=T0 - 7200, mergedAt=T0 - 7200)
        self.idle_s["pty-1"] = 25 * MIN
        dashboard.reopen_done(rid, "work sent")
        self.assertFalse(idle._merged(chatroom.get_room(rid, public=False)))
        self.tick()
        self.assertNotIn("agent left running", self.lines[0])
        self.tick(T0 + 2 * 3600)
        self.assertEqual(len(self.lines), 2)    # the repeats go on

    def test_merged_needs_the_card_still_where_the_merge_left_it(self):
        for more, want in (({"workflow": "done", "workflowAt": T0}, True),
                           ({"workflow": "done", "workflowAt": T0 + 200}, True),    # allowance
                           ({"workflow": "done", "workflowAt": T0 + 400}, False),
                           ({"workflow": "done", "workflowAt": T0 + 1800,           # the digest
                             "mergeMoveAt": T0 + 1800}, True),                      # came later
                           ({"workflow": "done", "workflowAt": T0 + 1900,
                             "mergeMoveAt": T0 + 1800}, False),
                           ({"workflow": "inprogress", "workflowAt": T0}, False)):
            with self.subTest(more):
                self.assertEqual(idle._merged({"mergedAt": T0, **more}), want)

    def test_the_merge_move_stamps_itself(self):
        rid = self.task(at=T0 - 7200, workflowPerson="inprogress")
        with mock.patch.object(digest, "_merged_at", return_value=T0 - 3600):
            digest._settle_merges([{"id": rid, "merged": True, "sha": "abc", "base": "main",
                                    "column": "inprogress", "title": "x"}])
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workflowAt"], room["mergeMoveAt"])
        self.assertIsNone(room["workflowPerson"])
        with mock.patch.object(dashboard, "_git_out", return_value="abc"):
            self.assertTrue(idle._merged(room))

    def test_only_the_page_marks_a_person(self):
        rid = self.task(workflow="inprogress")
        dashboard.update_task(rid, workflow="done", by_person=True)
        self.assertEqual(chatroom.get_room(rid, public=False)["workflowPerson"], "done")
        dashboard.update_task(rid, workflow="done")     # the tool's way, a PO's
        self.assertIsNone(chatroom.get_room(rid, public=False)["workflowPerson"])

    def test_a_card_a_person_put_in_done_stays(self):
        person = self.task(workflow="inprogress")
        po = self.task(workflow="inprogress", pty="pty-2")
        dashboard.update_task(person, workflow="done", by_person=True)
        dashboard.update_task(po, workflow="done")
        self.working.update(("pty-1", "pty-2"))
        self.tick()
        self.assertEqual([self.column(person), self.column(po)], ["done", "inprogress"])

    def test_a_person_mark_from_another_column_does_not_hold_done(self):
        rid = self.task(workflow="done", workflowPerson="inreview")
        self.working.add("pty-1")
        self.tick()
        self.assertEqual(self.column(rid), "inprogress")

    def _card_wait(self):
        rid = self.task()
        chatroom.patch_room(rid, waitMark={"kind": "card", "mids": ["m1"], "at": T0, "until": 0})
        self.idle_s["pty-1"] = 300 * MIN
        return rid

    def _project_file(self, text):
        d = dashboard.PROJECTS_ROOT / "broken"
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.json").write_text(text, encoding="utf-8")

    def test_an_unreadable_project_keeps_a_wait_on_a_card(self):
        rid = self._card_wait()
        self._project_file("{nope")
        with mock.patch.object(dashboard, "room_po_id", return_value=""):
            self.tick()
        self.assertTrue(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_a_po_that_cannot_be_found_keeps_a_wait_on_a_card(self):
        rid = self._card_wait()
        self.projects[0]["poRoomId"] = "room-lost"
        with mock.patch.object(dashboard, "room_po_id", return_value=""), \
                mock.patch.object(dashboard, "load_session_projects", return_value={rid: "p-trade"}):
            self.tick()
        self.assertTrue(chatroom.get_room(rid, public=False).get("waitMark"))
        self.assertEqual(self.lines, [])

    def test_a_task_with_no_po_still_reads_its_own_cards(self):
        rid = self.task()
        with mock.patch.object(dashboard, "room_po_id", return_value=""), \
                mock.patch.object(dashboard, "load_session_projects", return_value={rid: "p-trade"}):
            self.assertEqual(idle.open_mids(chatroom.get_room(rid, public=False)), set())

    def _open_ask(self, rid):
        return mock.patch.object(dashboard.attention, "open_ask", return_value={"from": "claude"})

    def _answered(self, rid):
        part = chatroom.participant(chatroom.get_room(rid, public=False), "claude")
        return float(part.get("answeredAt") or 0)

    def test_a_held_hub_line_answers_nothing(self):
        rid = self.task(workflow="done")
        with self._open_ask(rid), mock.patch.object(dashboard.sends, "mark"):
            dashboard._typed_sends(rid, [{"text": "x", "key": "k", "origin": "hub"}])
            self.assertFalse(dashboard.note_answer(rid, "claude"))
            self.assertEqual(self._answered(rid), 0)
            self.assertEqual(self.column(rid), "done")
            self.assertTrue(dashboard.note_answer(rid, "claude"))   # only that one line

    def test_a_held_line_of_a_person_still_answers(self):
        rid = self.task()
        with self._open_ask(rid), mock.patch.object(dashboard.sends, "mark"):
            dashboard._typed_sends(rid, [{"text": "x", "key": "k", "origin": "hub"},
                                         {"text": "y", "key": "l", "origin": "po"}])
            self.assertTrue(dashboard.note_answer(rid, "claude"))
        self.assertGreater(self._answered(rid), 0)

    def test_a_caller_that_filters_leaves_no_flag(self):
        rid = self.task()
        with self._open_ask(rid), mock.patch.object(dashboard.sends, "mark"):
            dashboard._typed_sends(rid, [{"text": "x", "key": "k", "origin": "hub"}], noted=True)
            self.assertTrue(dashboard.note_answer(rid, "claude"))   # a person typing later

    def test_a_damaged_key_is_a_damaged_file(self):
        for text in ('{"tasks": []}', '{"tasks": {}, "resolved": null}'):
            with self.subTest(text):
                idle._state_file().write_text(text, encoding="utf-8")
                self.assertIsNone(idle._load())
                self.assertEqual(self.tick(), {})
                self.assertEqual(idle._state_file().read_text(encoding="utf-8"), text)

    def test_a_missing_key_is_no_damage(self):
        idle._state_file().write_text('{"tasks": {}}', encoding="utf-8")
        self.assertEqual(idle._load(), {"tasks": {}, "projects": {}, "resolved": {}, "stopped": {}})

    def test_no_owner_gives_work(self):
        room = {"participants": [], "specAt": 0, "messages": [
            {"from": "claude", "ts": 500}, {"from": "codex", "ts": 600}]}
        with mock.patch.object(chatroom, "owners", return_value=["claude", "codex"]):
            self.assertEqual(dashboard._last_real_work(room), 0)
        with mock.patch.object(chatroom, "owners", return_value=["claude"]):
            self.assertEqual(dashboard._last_real_work(room), 600)


if __name__ == "__main__":
    unittest.main()
