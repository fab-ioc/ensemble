"""The burn rate, its seat rule and the brake on new work (#291)."""
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import board
import dashboard
import ensemble_tools
import pacing
import po_usage

H = 3600
NOW = 1_800_000_000.0
END = NOW + 100 * H


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def source(kind, percent, end=END, **window):
    return {"source": kind, "state": "ok", "windows": [{
        "kind": "seven_day", "percent": percent, "resetsAt": iso(end), "trusted": True,
        "ageSeconds": 0, "windowMinutes": 10080, "label": "7-day",
        **({"pool": "codex"} if kind == "codex" else {}), **window}]}


def snapshot(claude=50.0, codex=50.0, now=NOW):
    return {"checkedAt": now, "warnPercent": 80, "alarmPercent": 95, "plans": {},
            "sources": [source("claude", claude), source("codex", codex)]}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name, value in (("DASHBOARD_DIR", self.dir),
                            ("SETTINGS_FILE", self.dir / "settings.json")):
            p = mock.patch.object(dashboard, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(pacing._CACHE.update, path=None, data=None)
        pacing._CACHE.update(path=None, data=None)
        p = mock.patch.object(dashboard, "_blocked_kinds", return_value={})
        p.start()
        self.addCleanup(p.stop)

    def history(self, key, rise_per_hour, start=30.0, hours=30, end=END, now=NOW):
        for h in range(hours, -1, -1):
            pacing.note(key, now - h * H, start + (hours - h) * rise_per_hour, end)

    def engaged_state(self, why="Claude heading for 120%", until="the projection falls"):
        pacing._state()["brake"].update(on=True, since=NOW - 7 * H, episode=1, why=why, until=until)


class History(Base):
    def test_too_few_samples_and_short_history_are_unknown(self):
        self.assertEqual(pacing.burn("claude", 40, END, NOW)["state"], "unknown")
        for h in (2, 1, 0):
            pacing.note("claude", NOW - h * H, 40.0, END)
        self.assertEqual(pacing.burn("claude", 40, END, NOW)["why"], "too little history")

    def test_a_floor_is_unknown(self):
        self.history("claude", 1)
        self.assertEqual(pacing.burn("claude", 60, END, NOW, True)["state"], "unknown")

    def test_rate_blends_the_day_with_the_week(self):
        self.history("claude", 1.0)     # 30 -> 60 over 30 h: 24 points in the last day
        seen = pacing.burn("claude", 60.0, END, NOW)
        average = 60.0 / (168 - 100)
        rate = 0.75 * 1.0 + 0.25 * average
        self.assertAlmostEqual(seen["ratePerHour"], rate, places=2)
        self.assertAlmostEqual(seen["projected"], 60 + rate * 100, places=0)

    def test_a_quiet_night_does_not_swing_it(self):
        self.history("claude", 1.0, hours=24)
        busy = pacing.burn("claude", 54.0, END, NOW)["projected"]
        quiet = pacing.burn("claude", 54.0, END, NOW + 6 * H)["projected"]
        self.assertLess(quiet, busy)
        self.assertGreater(quiet, busy * 0.7)

    def test_a_falling_reading_restarts_the_history(self):
        self.history("codex:codex", 1.0, start=60)
        self.assertEqual(pacing.burn("codex:codex", 90, END, NOW)["state"], "known")
        self.assertTrue(pacing.note("codex:codex", NOW + 60, 48.0, END))
        self.assertEqual(len(pacing._state()["series"]["codex:codex"]["samples"]), 1)
        self.assertEqual(pacing.burn("codex:codex", 48, END, NOW + 60)["state"], "unknown")

    def test_a_one_point_dip_and_reset_drift_do_not_restart(self):
        self.history("claude", 0.0, hours=4)
        pacing.note("claude", NOW + 1000, 29.0, END + 600)
        self.assertGreater(len(pacing._state()["series"]["claude"]["samples"]), 1)

    def test_a_moved_reset_restarts(self):
        self.history("claude", 0.0, hours=4)
        pacing.note("claude", NOW + 1000, 30.0, END + 3 * H)
        self.assertEqual(len(pacing._state()["series"]["claude"]["samples"]), 1)

    def test_same_reading_is_sampled_every_15_minutes(self):
        pacing.note("claude", NOW, 10.0, END)
        self.assertFalse(pacing.note("claude", NOW + 60, 10.0, END))
        self.assertTrue(pacing.note("claude", NOW + 901, 10.0, END))

    def test_history_keeps_one_sample_older_than_it_trims(self):
        self.history("claude", 0.1, hours=60)
        samples = pacing._state()["series"]["claude"]["samples"]
        self.assertLess(samples[1][0] - (NOW - 36 * H), H + 1)
        self.assertLessEqual(samples[0][0], NOW - 36 * H)

    def test_survives_a_restart(self):
        self.history("claude", 1.0)
        pacing._save()
        before = pacing.burn("claude", 60.0, END, NOW)
        pacing._CACHE.update(path=None, data=None)
        self.assertEqual(pacing.burn("claude", 60.0, END, NOW), before)

    def test_a_bad_file_reads_as_no_history(self):
        (self.dir / "pace-history.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(pacing.burn("claude", 40, END, NOW)["state"], "unknown")

    def test_sample_skips_untrusted_and_reads_each_codex_pool(self):
        snap = snapshot()
        reserve = dict(snap["sources"][1]["windows"][0], pool="reserve", percent=5)
        snap["sources"][1]["windows"].append(reserve)
        snap["sources"][0]["windows"][0]["trusted"] = False
        pacing.sample(snap)
        self.assertEqual(set(pacing._state()["series"]), {"codex:codex", "codex:reserve"})

    def test_kind_pace_carries_the_burn(self):
        self.history("claude", 1.0)
        paced = dashboard._kind_pace(snapshot(60), "claude", now=NOW)
        self.assertEqual(paced["burn"]["state"], "known")
        self.assertEqual(dashboard._kind_pace(snapshot(60), "codex", now=NOW)["burn"]["state"],
                         "unknown")


class SeatChoice(Base):
    def decide(self, preferred, claude=50, codex=50):
        pace = {"enabled": True, "prefer": "", "margin": 10, "hysteresis": 25,
                "weights": {"claude": 20, "codex": 5}, "labels": {}, "sources": {}}
        return dashboard.choose_agent_kind_for_seat(
            preferred, snapshot(claude, codex), installed=lambda k: True, pace=pace, now=NOW)

    def test_a_kind_past_its_week_gives_the_seat_to_the_other(self):
        self.history("claude", 1.0, start=20)      # 50 now, heading well past 110
        self.history("codex:codex", 0.0, start=50)
        d = self.decide("claude")
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "switch_projection"))
        self.assertIn("heading for", dashboard._projection_reason(d))

    def test_balance_cannot_move_a_seat_onto_the_kind_running_out(self):
        self.history("claude", 0.0, start=50)
        self.history("codex:codex", 1.0, start=20)
        d = self.decide("codex", claude=50, codex=50)
        self.assertEqual(d["decision"], "switch_projection")
        d = self.decide("claude", claude=50, codex=50)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "preferred_projection"))

    def test_unknown_leaves_the_old_rule(self):
        self.history("claude", 1.0, start=20)
        self.assertNotIn("projection", self.decide("claude")["decision"])

    def test_the_warning_guard_stays_first(self):
        self.history("claude", 1.0, start=55)
        self.history("codex:codex", 0.0, start=50)
        self.assertEqual(self.decide("claude", claude=85)["decision"], "switch_warning")

    def test_prefer_is_untouched(self):
        self.history("claude", 1.0, start=20)
        self.history("codex:codex", 0.0, start=50)
        pace = {"enabled": True, "prefer": "claude", "margin": 10, "hysteresis": 25,
                "weights": {}, "labels": {}, "sources": {}}
        d = dashboard.choose_agent_kind_for_seat("claude", snapshot(), installed=lambda k: True,
                                                 pace=pace, now=NOW)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "prefer_setting"))


class Brake(Base):
    def setUp(self):
        super().setUp()
        self.snap = snapshot(50, 50)
        for target, value in ((dashboard.usage, self.snap),):
            p = mock.patch.object(target, "snapshot", return_value=value)
            p.start()
            self.addCleanup(p.stop)
        agent = mock.Mock(installed=lambda: True, display_name="Agent")
        p = mock.patch.object(dashboard.agents, "get_agent", return_value=agent)
        p.start()
        self.addCleanup(p.stop)
        self.projects = [{"id": "p", "name": "P", "poRoomId": "po"}]
        p = mock.patch.object(dashboard, "load_projects", side_effect=lambda: self.projects)
        p.start()
        self.addCleanup(p.stop)
        self.delivered = []
        self.deliver_ok = True
        def deliver(project, text):
            self.delivered.append(text)
            return self.deliver_ok
        p = mock.patch.object(board, "deliver", side_effect=deliver)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(pacing, "_note_use", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.rooms = {}
        def patch_room(rid, **fields):
            self.rooms[rid].update(fields)
            return self.rooms[rid]
        for name, fn in (("patch_room", patch_room),
                         ("list_rooms", lambda copy_rooms=True: list(self.rooms.values())),
                         ("get_room", lambda rid, **kw: self.rooms.get(rid))):
            p = mock.patch.object(dashboard.chatroom, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        self.started = []
        launcher = mock.Mock()
        launcher._resume_room.side_effect = self.start
        p = mock.patch.object(dashboard, "hub_launcher", return_value=launcher)
        p.start()
        self.addCleanup(p.stop)
        for name, value in (("_room_is_live", lambda r: False),
                            ("workflow_of", lambda r: r.get("workflow", "todo"))):
            p = mock.patch.object(dashboard, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        self.setting("hold")

    def start(self, room):
        self.started.append(room["id"])
        pacing.clear_hold(room)

    def setting(self, value):
        self.mode = value
        (self.dir / "settings.json").write_text(json.dumps({"paceBrake": value}), encoding="utf-8")

    def room(self, rid, priority, no=1, **fields):
        self.rooms[rid] = {"id": rid, "priority": priority, "no": no, "title": rid, **fields}
        return self.rooms[rid]

    def both_over(self):
        self.history("claude", 1.0, start=20)
        self.history("codex:codex", 1.0, start=20)

    def calm(self):
        pacing._state()["series"].clear()
        self.history("claude", 0.0, start=50)
        self.history("codex:codex", 0.0, start=50)

    def test_engages_only_when_every_usable_kind_is_over(self):
        self.history("claude", 1.0, start=20)
        self.history("codex:codex", 0.0, start=50)
        pacing.tick(NOW)
        self.assertFalse(pacing._state()["brake"]["on"])
        pacing._state()["series"].clear()
        self.both_over()
        pacing.tick(NOW)
        self.assertTrue(pacing._state()["brake"]["on"])

    def test_a_spent_kind_does_not_count(self):
        self.history("claude", 1.0, start=20)
        self.snap["sources"][1]["windows"][0]["percent"] = 100
        pacing.tick(NOW)
        self.assertTrue(pacing._state()["brake"]["on"])

    def test_a_usable_kind_unknown_is_no_brake(self):
        self.history("claude", 1.0, start=20)
        pacing.tick(NOW)
        self.assertFalse(pacing._state()["brake"]["on"])

    def test_no_usable_kind_is_no_brake(self):
        self.both_over()
        for s in self.snap["sources"]:
            s["windows"][0]["percent"] = 100
        pacing.tick(NOW)
        self.assertFalse(pacing._state()["brake"]["on"])

    def test_a_week_about_to_reset_is_never_out(self):
        end = NOW + 6 * H
        self.snap["sources"][0]["windows"][0]["resetsAt"] = iso(end)
        self.history("claude", 2.0, start=20, end=end)
        self.history("codex:codex", 1.0, start=20)
        pacing.tick(NOW)
        self.assertFalse(pacing._state()["brake"]["on"])

    def test_priority_split_and_po_room(self):
        self.both_over()
        pacing.tick(NOW)
        self.assertIsNotNone(pacing.hold(self.room("m", 3), "agent", NOW))
        self.assertIsNotNone(pacing.hold(self.room("l", 4), "agent", NOW))
        self.assertIsNone(pacing.hold(self.room("h", 2), "agent", NOW))
        self.assertIsNone(pacing.hold(self.room("x", 1), "agent", NOW))
        self.assertIsNone(pacing.hold(self.room("po", 3), "agent", NOW))
        self.assertIn("heading for", self.rooms["m"]["paceHold"]["why"])

    def test_a_failed_read_never_blocks(self):
        self.both_over()
        pacing.tick(NOW)
        with mock.patch.object(dashboard.usage, "snapshot", side_effect=OSError("no")):
            self.assertIsNone(pacing.hold(self.room("m", 3), "agent", NOW))
        with mock.patch.object(pacing, "_judge", return_value={"kinds": {}, "lowest": None}):
            self.assertIsNone(pacing.hold(self.room("m", 3), "agent", NOW))

    def test_tick_survives_unreadable_settings_by_starting(self):
        with mock.patch.object(dashboard, "load_settings", side_effect=ValueError("bad")):
            self.assertIsNone(pacing.hold(self.room("m", 3), "agent", NOW))

    def test_off_and_tell_only_never_hold(self):
        self.both_over()
        pacing.tick(NOW)
        for mode in ("tell", "off"):
            self.setting(mode)
            self.assertIsNone(pacing.hold(self.room("m", 3), "agent", NOW))

    def test_release_order_and_one_a_tick(self):
        self.both_over()
        pacing.tick(NOW)
        self.room("late", 4, no=1)
        self.room("first", 3, no=9)
        self.room("second", 3, no=10)
        for rid in ("late", "first", "second"):
            pacing.hold(self.rooms[rid], "gate", NOW)
        self.calm()
        pacing.tick(NOW + 7 * H)
        self.assertEqual(self.started, ["first"])
        pacing.tick(NOW + 7 * H + 60)
        pacing.tick(NOW + 7 * H + 120)
        self.assertEqual(self.started, ["first", "second", "late"])
        self.assertFalse(self.rooms["late"]["paceHold"])

    def test_dwell_and_hysteresis_keep_it_on(self):
        self.both_over()
        pacing.tick(NOW)
        self.calm()
        pacing.tick(NOW + H)
        self.assertTrue(pacing._state()["brake"]["on"])
        pacing.tick(NOW + 7 * H)
        self.assertFalse(pacing._state()["brake"]["on"])

    def test_a_task_raised_to_high_starts_at_once(self):
        self.both_over()
        pacing.tick(NOW)
        room = self.room("m", 3)
        pacing.hold(room, "agent", NOW)
        pacing.tick(NOW + 60)
        self.assertEqual(self.started, [])
        room["priority"] = 2
        pacing.tick(NOW + 120)
        self.assertEqual(self.started, ["m"])

    def test_a_short_gap_keeps_held_work_then_lets_go(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        pacing._state()["series"].clear()
        pacing.tick(NOW + 60)
        self.assertEqual(self.started, [])
        self.assertTrue(pacing._state()["brake"]["on"])
        pacing.tick(NOW + 60 + pacing.UNKNOWN_RELEASE_S)
        self.assertEqual(self.started, ["m"])

    def test_turning_it_off_releases_and_says_so_once(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        self.delivered.clear()
        self.setting("off")
        pacing.tick(NOW + 60)
        pacing.tick(NOW + 120)
        self.assertEqual(self.started, ["m"])
        self.assertEqual(len(self.delivered), 1)
        self.assertIn("turned off in Settings", self.delivered[0])

    def test_a_release_for_lost_readings_says_that(self):
        self.both_over()
        pacing.tick(NOW)
        pacing._state()["series"].clear()
        pacing.tick(NOW + 60)
        pacing.tick(NOW + 60 + pacing.UNKNOWN_RELEASE_S)
        self.assertIn("readings are not available", self.delivered[-1])
        self.assertNotIn("recovered", self.delivered[-1])

    def test_new_work_stays_held_through_the_dwell(self):
        self.both_over()
        pacing.tick(NOW)
        self.calm()
        pacing.tick(NOW + H)
        self.assertTrue(pacing._state()["brake"]["on"])
        self.assertIsNotNone(pacing.hold(self.room("m", 3), "agent", NOW + H))

    def test_a_failing_stage_never_stops_the_release(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        self.calm()
        for target, name in ((pacing, "_tell"), (pacing, "_save"), (pacing, "sample"),
                             (pacing, "_note_use"), (pacing, "_judge")):
            self.started.clear()
            self.rooms["m"]["paceHold"] = {"at": NOW}
            pacing._state()["brake"].update(on=False)
            with mock.patch.object(target, name, side_effect=OSError("no")):
                pacing.tick(NOW + 7 * H)
            self.assertEqual(self.started, ["m"], name)

    def test_a_failing_usage_reading_releases_after_the_grace(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        with mock.patch.object(dashboard.usage, "snapshot", side_effect=OSError("no")):
            pacing.tick(NOW + 60)
            self.assertEqual(self.started, [])
            pacing.tick(NOW + 60 + pacing.UNKNOWN_RELEASE_S)
        self.assertEqual(self.started, ["m"])

    def test_no_lock_over_a_delivery_and_usage_reads_never_wait(self):
        seen = []
        def free():
            got = pacing._LOCK.acquire(blocking=False)
            if got:
                pacing._LOCK.release()
            seen.append(got)
        def deliver(project, text):
            thread = threading.Thread(target=free)
            thread.start()
            thread.join()
            return True
        self.both_over()
        with mock.patch.object(board, "deliver", side_effect=deliver):
            pacing.tick(NOW)
        self.assertEqual(seen, [True])
        out = []
        with pacing._LOCK:
            thread = threading.Thread(target=lambda: out.extend(
                [pacing.view(), pacing.burn("claude", 50.0, END, NOW)]))
            thread.start()
            thread.join(5)
        self.assertEqual(len(out), 2)

    def gated(self, satisfied):
        self.both_over()
        pacing.tick(NOW)
        room = self.room("g", 3, launched=False, after=[{"task": "t"}], paceHold={"at": NOW})
        self.calm()
        gate = (["#1 merged"], NOW) if satisfied else ([], 0)
        with (mock.patch.object(board, "gate_state", return_value=gate),
              mock.patch.object(dashboard.ensemble_tools, "_project_of_room", return_value="p"),
              mock.patch.object(dashboard, "task_label", return_value="#2")):
            pacing.tick(NOW + 7 * H)
        return room

    def test_a_gate_already_claimed_is_not_claimed_twice(self):
        room = self.room("g", 3, launched=False, after=[{"task": "t"}],
                         gateAction={"result": "started", "notice": "first"})
        stale = dict(room, gateAction=None)
        with mock.patch.object(board, "gate_state", return_value=(["#1 merged"], NOW)):
            board.start_gated(stale, NOW)
        self.assertEqual(room["gateAction"]["notice"], "first")
        self.assertEqual(self.started, [])

    def test_a_room_that_cannot_be_handled_gives_way_to_the_next(self):
        self.room("bad", 3, no=1, paceHold={"at": NOW})
        self.room("good", 3, no=2, paceHold={"at": NOW})
        real = pacing.clear_hold
        def clear(room):
            if room["id"] == "bad":
                raise OSError("no")
            real(room)
        dashboard.hub_launcher.return_value._resume_room.side_effect = None
        def start(room):
            if room["id"] == "bad":
                raise RuntimeError("no")
            self.started.append(room["id"])
        dashboard.hub_launcher.return_value._resume_room.side_effect = start
        with (mock.patch.object(pacing, "clear_hold", side_effect=clear),
              mock.patch.object(dashboard.ensemble_tools, "_project_of_room", return_value="p")):
            pacing._release("hold")
        self.assertEqual(self.started, ["good"])

    def test_a_waiting_card_follows_the_brake(self):
        self.both_over()
        pacing.tick(NOW)
        room = self.room("m", 3)
        pacing.hold(room, "agent", NOW)
        pacing._state()["brake"].update(why="new why", until="new until")
        pacing._release("hold")
        self.assertEqual((room["paceHold"]["why"], room["paceHold"]["until"]),
                         ("new why", "new until"))
        self.assertEqual(self.started, [])

    def test_a_setting_that_cannot_be_read_says_so_not_turned_off(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        with mock.patch.object(pacing, "mode", side_effect=ValueError("bad")):
            pacing.tick(NOW + 60)
            self.assertEqual(self.started, [])
            pacing.tick(NOW + 60 + pacing.UNKNOWN_RELEASE_S)
        self.assertEqual(self.started, ["m"])
        self.assertIn("readings are not available", self.delivered[-1])

    def test_a_gated_task_goes_the_gates_way(self):
        room = self.gated(True)
        self.assertEqual(self.started, ["g"])
        self.assertEqual(room["gateAction"]["result"], "started")
        self.assertIn("started", room["gateAction"]["notice"])

    def test_a_gate_that_reopened_while_held_does_not_start(self):
        room = self.gated(False)
        self.assertEqual(self.started, [])
        self.assertFalse(room.get("gateAction"))
        self.assertFalse(room["paceHold"])

    def test_one_tell_per_engage_and_release(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.tick(NOW + 60)
        self.assertEqual(len(self.delivered), 1)
        self.assertTrue(self.delivered[0].startswith("Pacing brake on"))
        self.calm()
        pacing.tick(NOW + 7 * H)
        pacing.tick(NOW + 7 * H + 60)
        self.assertEqual(len(self.delivered), 2)
        self.assertTrue(self.delivered[1].startswith("Pacing brake off"))

    def test_a_failed_delivery_is_retried_once_it_lands(self):
        self.both_over()
        self.deliver_ok = False
        pacing.tick(NOW)
        self.deliver_ok = True
        pacing.tick(NOW + 60)
        pacing.tick(NOW + 120)
        self.assertEqual(len(self.delivered), 2)

    def test_tell_only_tells_and_holds_nothing(self):
        self.setting("tell")
        self.both_over()
        pacing.tick(NOW)
        self.assertIn("Tell only", self.delivered[0])

    def test_off_tells_nothing(self):
        self.setting("off")
        self.both_over()
        pacing.tick(NOW)
        self.assertEqual(self.delivered, [])
        self.assertFalse(pacing._state()["brake"]["on"])

    def test_tells_survive_a_restart_once(self):
        self.both_over()
        pacing.tick(NOW)
        pacing._CACHE.update(path=None, data=None)
        pacing.tick(NOW + 60)
        self.assertEqual(len(self.delivered), 1)

    def test_a_failed_start_goes_to_the_po_and_is_not_retried(self):
        self.both_over()
        pacing.tick(NOW)
        pacing.hold(self.room("m", 3), "agent", NOW)
        self.calm()
        dashboard.hub_launcher.return_value._resume_room.side_effect = RuntimeError("no")
        with mock.patch.object(dashboard.ensemble_tools, "_project_of_room", return_value="p"):
            pacing.tick(NOW + 7 * H)
        self.assertFalse(self.rooms["m"]["paceHold"])
        self.assertIn("could not start", self.delivered[-1])

    def test_a_done_or_running_task_loses_its_mark(self):
        self.both_over()
        pacing.tick(NOW)
        self.room("m", 3, paceHold={"at": NOW}, workflow="done")
        pacing.tick(NOW + 60)
        self.assertFalse(self.rooms["m"]["paceHold"])
        self.assertEqual(self.started, [])

    def test_agent_start_is_held_and_says_why(self):
        self.both_over()
        pacing.tick(NOW)
        room = self.room("m", 3, launched=False)
        handler = mock.Mock()
        ctx = {"projectId": "p", "room": {"id": "other"}, "identity": "x"}
        with (mock.patch.object(ensemble_tools, "_load_target", return_value=room),
              mock.patch.object(ensemble_tools, "_check_write_scope"),
              mock.patch.object(ensemble_tools, "_not_self"),
              mock.patch.object(ensemble_tools, "_status", return_value="draft"),
              mock.patch.object(pacing.time, "time", return_value=NOW)):
            out = ensemble_tools._start_task(ctx, {"taskId": "m"}, handler)
        self.assertTrue(out["held"])
        self.assertIn("pacing brake", out["note"])
        handler._resume_room.assert_not_called()

    def test_a_high_agent_start_goes_through(self):
        self.both_over()
        pacing.tick(NOW)
        room = self.room("h", 2, launched=True)
        handler = mock.Mock()
        handler._resume_room.return_value = {}
        ctx = {"projectId": "p", "room": {"id": "other"}, "identity": "x"}
        with (mock.patch.object(ensemble_tools, "_load_target", return_value=room),
              mock.patch.object(ensemble_tools, "_check_write_scope"),
              mock.patch.object(ensemble_tools, "_not_self"),
              mock.patch.object(ensemble_tools, "_agents_view", return_value=[]),
              mock.patch.object(pacing.time, "time", return_value=NOW)):
            ensemble_tools._start_task(ctx, {"taskId": "h"}, handler)
        handler._resume_room.assert_called_once()

    def test_a_persons_start_clears_the_mark_and_is_never_asked(self):
        room = self.room("m", 3, paceHold={"at": NOW})
        pacing.clear_hold(room)
        self.assertFalse(self.rooms["m"]["paceHold"])
        src = Path(dashboard.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("pacing.hold("), 0)      # no page route consults it

    def test_a_gate_is_not_used_up_and_the_board_stays_quiet(self):
        self.both_over()
        pacing.tick(NOW)
        project = self.projects[0]
        room = self.room("g", 3, launched=False, after=[{"task": "t"}], projectId="p")
        out = {}
        with (mock.patch.object(dashboard.chatroom, "_listed_copy", side_effect=lambda r: r),
              mock.patch.object(dashboard, "load_session_projects", return_value={}),
              mock.patch.object(dashboard.ensemble_tools, "_project_of_room", return_value="p"),
              mock.patch.object(board, "gate_state", return_value=(["#1 merged"], NOW)),
              mock.patch.object(board, "_save", side_effect=out.update),
              mock.patch.object(board, "_load", return_value={}),
              mock.patch.object(pacing.time, "time", return_value=NOW)):
            board.check(project, NOW)
            board.check(project, NOW + 3600)
        self.assertFalse(room.get("gateAction"))
        self.assertTrue(room["paceHold"])
        self.assertEqual(self.started, [])
        self.assertEqual([t for t in self.delivered if "idle" in t.lower()], [])

    def test_the_setting_is_validated_and_defaults_to_hold(self):
        (self.dir / "settings.json").unlink()
        self.assertEqual(dashboard.load_settings()["paceBrake"], "hold")
        self.assertEqual(pacing.mode(), "hold")
        (self.dir / "settings.json").write_text('{"paceBrake": "x"}', encoding="utf-8")
        self.assertEqual(pacing.mode(), "hold")

    def test_pacing_off_turns_the_brake_off(self):
        (self.dir / "settings.json").write_text('{"paceWeek": false}', encoding="utf-8")
        self.assertEqual(pacing.mode(), "off")


class PoLine(Base):
    def test_head_carries_rate_projection_and_top_project(self):
        self.history("claude", 1.0)
        state = pacing._state()
        state["use"] = [[NOW - 20 * H, {"a": 100, "b": 0}], [NOW, {"a": 200, "b": 300}]]
        projects = [{"id": "p1", "name": "One"}, {"id": "p2", "name": "Two"}]
        with (mock.patch.object(dashboard, "load_projects", return_value=projects),
              mock.patch.object(dashboard, "load_session_projects", return_value={}),
              mock.patch.object(dashboard.chatroom, "get_room",
                                side_effect=lambda rid, **k: {"id": rid}),
              mock.patch.object(dashboard.ensemble_tools, "_project_of_room",
                                side_effect=lambda r, l=None: "p1" if r["id"] == "a" else "p2")):
            head = po_usage.head(snapshot(60), NOW)
        self.assertIn("/h to", head)
        self.assertIn("top Two 75%", head)

    def test_a_task_first_seen_now_is_not_its_whole_life(self):
        pacing._state()["use"] = [[NOW - 20 * H, {"a": 100}], [NOW - H, {"a": 150, "old": 9000}],
                                  [NOW, {"a": 200, "old": 9010}]]
        projects = [{"id": "p1", "name": "One"}, {"id": "p2", "name": "Two"}]
        with (mock.patch.object(dashboard, "load_projects", return_value=projects),
              mock.patch.object(dashboard, "load_session_projects", return_value={}),
              mock.patch.object(dashboard.chatroom, "get_room",
                                side_effect=lambda rid, **k: {"id": rid}),
              mock.patch.object(dashboard.ensemble_tools, "_project_of_room",
                                side_effect=lambda r, l=None: "p1" if r["id"] == "a" else "p2")):
            self.assertEqual(pacing.top_project(), ("One", 91))

    def test_a_failure_leaves_the_line_out(self):
        with mock.patch.object(dashboard, "pace_settings", side_effect=ValueError("x")):
            self.assertNotIn("/h", po_usage.head(snapshot(60), NOW))
        self.assertTrue(po_usage.head(snapshot(60), NOW).startswith("usage:"))


if __name__ == "__main__":
    unittest.main()
