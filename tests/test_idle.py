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

    def test_a_failed_save_does_not_repeat_the_line(self):
        rid = self.task()
        self.idle_s["pty-1"] = 25 * MIN
        idle._state_file().mkdir()      # replace refuses, as a reader's open file does
        with mock.patch.object(idle.time, "sleep"):
            self.tick()
            self.assertEqual(len(self.lines), 1)
            self.tick(T0 + 120)
        self.assertEqual(len(self.lines), 1)
        idle._state_file().rmdir()
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

    def test_note_answer_moves_it(self):
        rid = self.task(workflow="done")
        dashboard.note_answer(rid, "claude")
        self.assertEqual(self.column(rid), "inprogress")

    def _handler(self):
        return dashboard.Handler.__new__(dashboard.Handler)

    def test_resume_moves_it_a_quiet_return_does_not(self):
        class Stop(Exception):
            pass
        rid = self.task(workflow="done")
        room = chatroom.get_room(rid, public=False)
        with mock.patch.object(dashboard.restart_drain, "holding", side_effect=Stop):
            with self.assertRaises(Stop):
                self._handler()._resume_room(room, quiet=True)
            self.assertEqual(self.column(rid), "done")
            with self.assertRaises(Stop):
                self._handler()._resume_room(room, text="go", origin="hub")
            self.assertEqual(self.column(rid), "done")
            with self.assertRaises(Stop):
                self._handler()._resume_room(room, text="go", origin="po")
            self.assertEqual(self.column(rid), "inprogress")

    def test_start_moves_it(self):
        class Stop(Exception):
            pass
        rid = self.task(workflow="done")
        room = chatroom.get_room(rid, public=False)
        with mock.patch.object(dashboard, "apply_first_launch_allocation", side_effect=Stop):
            with self.assertRaises(Stop):
                self._handler()._start_room_now(room)
        self.assertEqual(self.column(rid), "inprogress")

    def test_a_chat_message_moves_it_a_hub_line_or_report_does_not(self):
        h = self._handler()
        with mock.patch.object(chatroom, "get_room", wraps=chatroom.get_room):
            for msg, col in (({"from": "ensemble"}, "done"),
                             ({"from": "claude", "kind": "report"}, "done"),
                             ({"from": "po"}, "inprogress")):
                with self.subTest(msg):
                    rid = self.task(workflow="done", pty="")
                    try:
                        h._ring_recipients(rid, {"recipients": ["nobody"], "message": msg})
                    except Exception:   # noqa: BLE001 — only the column is looked at
                        pass
                    self.assertEqual(self.column(rid), col)

    def test_no_recipients_moves_nothing(self):
        rid = self.task(workflow="done", pty="")
        self._handler()._ring_recipients(rid, {"recipients": [], "message": {"from": "po"}})
        self.assertEqual(self.column(rid), "done")

    def test_the_start_pass(self):
        ran_on = self.task("Ran on", workflow="done", pty="pty-a", at=T0)
        left = self.task("Left", workflow="done", pty="pty-b", at=T0, mergedAt=T0 + 5)
        stopped = self.task("Stopped", workflow="done", pty="", at=T0)
        for rid in (ran_on, left, stopped):
            chatroom.patch_room(rid, specAt=T0 + 1)
        chatroom.patch_room(ran_on, specAt=T0 + 10)
        moved = dashboard.reopen_done_at_start()
        self.assertEqual(moved, {"Trading": 1})
        self.assertEqual([self.column(r) for r in (ran_on, left, stopped)],
                         ["inprogress", "done", "done"])


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

    def test_a_clock_time_past_today_means_tomorrow(self):
        now = T0
        at = time.strftime("%H:%M", time.localtime(now - 3600))
        ts = ensemble_tools._wait_until({"until": at}, now)
        self.assertGreater(ts, now)
        self.assertLessEqual(ts - now, 86400)

    def test_another_task_needs_the_po(self):
        rid, other = self.task(), self.task("Other", pty="")
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

    def test_a_broken_state_file_is_empty(self):
        idle._state_file().write_text("{nope", encoding="utf-8")
        self.assertEqual(idle._load(), {"tasks": {}, "projects": {}, "resolved": {}, "stopped": {}})


if __name__ == "__main__":
    unittest.main()
