"""ED-164: each agent kind's 7-day allowance paced over the week.

The pace line (usage.pace_mark), a kind against it (dashboard._kind_pace), the
seat choice it feeds (choose_agent_kind_for_seat: launch, reviews and owner
handovers), the per-task pin, the settings round-trip through /api/settings,
and a replay of 2026-09-30, when Codex spent 79% of its week by 11:00.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import rotation
import usage

DAY = 24 * 3600
LOCAL = timezone(timedelta(hours=2))
# A Codex week that resets on 10-07 07:04 local, so it began on 09-30 07:04.
CODEX_RESET = datetime(2026, 10, 7, 7, 4, tzinfo=LOCAL)
WEEK_START = (CODEX_RESET - timedelta(days=7)).timestamp()
PACE_ON = {"enabled": True, "margin": 15}
PACE_OFF = {"enabled": False, "margin": 15}


class _FakeAgent:
    def __init__(self, kind: str):
        self.display_name = kind.title()

    def installed(self) -> bool:
        return True


def _weekly(percent, resets_at, *, pool=None, trusted=True):
    return {"kind": "seven_day", "label": "7-day", "percent": percent, "trusted": trusted,
            "rolledOver": False, "resetUnknown": False, "pool": pool,
            "resetsAt": resets_at.isoformat() if isinstance(resets_at, datetime) else resets_at}


def _five(percent):
    return {"kind": "five_hour", "label": "5-hour", "percent": percent, "trusted": True,
            "rolledOver": False, "resetUnknown": False, "resetsAt": None}


def _snap(claude_week, codex_week, claude_reset=CODEX_RESET, codex_reset=CODEX_RESET,
          claude_five=0, codex_five=0):
    return {"warnPercent": 80, "alarmPercent": 95, "sources": [
        {"source": "claude", "state": "ok",
         "windows": [_five(claude_five), _weekly(claude_week, claude_reset)]},
        {"source": "codex", "state": "ok",
         "windows": [_five(codex_five), _weekly(codex_week, codex_reset, pool="codex")]},
    ]}


class ThePaceLine(unittest.TestCase):
    def test_at_the_reset_it_is_the_margin(self):
        self.assertEqual(usage.pace_mark(CODEX_RESET.isoformat(), WEEK_START, 15, 80)["pace"], 15)

    def test_mid_week_it_is_the_share_gone_plus_the_margin(self):
        mark = usage.pace_mark(CODEX_RESET.isoformat(), WEEK_START + 2 * DAY, 15, 80)
        self.assertAlmostEqual(mark["pace"], 15 + 200 / 7, places=1)       # ~43.6
        self.assertAlmostEqual(mark["elapsed"], 2 / 7, places=3)

    def test_it_is_capped_at_the_warning(self):
        # 15 + 65 = 80 at about day 4.55; never above it after.
        self.assertEqual(usage.pace_mark(CODEX_RESET.isoformat(), WEEK_START + 4.6 * DAY, 15, 80)["pace"], 80)
        self.assertEqual(usage.pace_mark(CODEX_RESET.isoformat(), WEEK_START + 6.9 * DAY, 15, 80)["pace"], 80)
        # A reset already passed reads as the whole week gone.
        self.assertEqual(usage.pace_mark(CODEX_RESET.isoformat(), WEEK_START + 9 * DAY, 15, 80)["pace"], 80)

    def test_an_epoch_reset_is_read_too(self):
        self.assertEqual(usage.pace_mark(CODEX_RESET.timestamp(), WEEK_START, 0, 80)["pace"], 0)

    def test_an_unknown_reset_is_not_paced(self):
        for reset in (None, "", "not a date", True):
            self.assertIsNone(usage.pace_mark(reset, WEEK_START, 15, 80), reset)

    def test_a_kind_without_a_reset_time_has_no_pace(self):
        snap = _snap(30, 30, claude_reset=None)
        self.assertEqual(dashboard._kind_pace(snap, "claude", now=WEEK_START)["state"], "unknown")
        codex = dashboard._kind_pace(snap, "codex", now=WEEK_START + 2 * DAY)
        self.assertEqual((codex["state"], codex["ahead"], codex["pace"]), ("known", False, 43.6))

    def test_codex_is_paced_on_the_pool_it_runs_on(self):
        snap = _snap(10, 60)
        snap["sources"][1]["windows"].append(_weekly(5, CODEX_RESET, pool="base_model_inference"))
        snap["sources"][1]["pools"] = [{"id": "codex", "label": None},
                                       {"id": "base_model_inference", "label": "reserve"}]
        main = dashboard._kind_pace(snap, "codex", "", 15, WEEK_START + DAY)
        reserve = dashboard._kind_pace(snap, "codex", "gpt-reserve", 15, WEEK_START + DAY)
        self.assertEqual((main["percent"], main["ahead"]), (60, True))
        self.assertEqual((reserve["percent"], reserve["ahead"], reserve["poolLabel"]),
                         (5, False, "reserve"))


class TheSeatChoice(unittest.TestCase):
    """choose_agent_kind_for_seat at day 2 of both weeks: the pace is ~43.6%."""

    NOW = WEEK_START + 2 * DAY

    def setUp(self):
        p = mock.patch.object(dashboard.agents, "get_agent", side_effect=_FakeAgent)
        p.start()
        self.addCleanup(p.stop)

    def choose(self, snap, preferred="codex", pace=PACE_ON, **kw):
        return dashboard.choose_agent_kind_for_seat(
            preferred, snap, installed=lambda k: True, pace=pace, now=self.NOW, **kw)

    def test_only_the_preferred_kind_ahead_switches(self):
        d = self.choose(_snap(30, 52))
        self.assertEqual((d["chosenKind"], d["decision"], d["changed"]), ("claude", "switch_pace", True))
        self.assertTrue(d["pace"]["kinds"]["codex"]["ahead"])
        self.assertEqual(dashboard._pace_reason_phrase("codex", d["pace"]["kinds"]["codex"]),
                         "Codex 7-day window at 52%, ahead of pace (43.6% by today)")

    def test_only_the_other_kind_ahead_keeps_the_preferred(self):
        d = self.choose(_snap(52, 30))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "preferred_below_warning"))

    def test_both_ahead_chooses_the_one_less_far_ahead(self):
        d = self.choose(_snap(50, 70))
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "both_ahead_of_pace"))
        d = self.choose(_snap(70, 50))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "both_ahead_of_pace"))
        # A tie keeps the preferred kind; pacing never refuses a seat.
        d = self.choose(_snap(60, 60))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "both_ahead_of_pace"))

    def test_pacing_off_keeps_the_old_rule(self):
        d = self.choose(_snap(30, 52), pace=PACE_OFF)
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "preferred_below_warning"))

    def test_an_unknown_reset_is_not_paced(self):
        d = self.choose(_snap(30, 52, codex_reset=None))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "preferred_below_warning"))
        d = self.choose(_snap(30, 52, claude_reset=None))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "preferred_below_warning"))

    def test_the_warning_and_alarm_rules_come_first(self):
        d = self.choose(_snap(30, 85))
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "switch_warning"))
        d = self.choose(_snap(90, 96))
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "switch_alarm"))
        d = self.choose(_snap(96, 97))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "both_alarm"))

    def test_pace_never_moves_a_seat_to_a_kind_past_the_warning(self):
        # Codex ahead of pace, but Claude's 5-hour window is past the warning.
        d = self.choose(_snap(30, 52, claude_five=85))
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "preferred_below_warning"))

    def test_a_pinned_seat_ignores_pace_and_the_warning(self):
        d = self.choose(_snap(30, 52), pinned=True)
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "pinned"))
        d = self.choose(_snap(30, 88), pinned=True)
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "pinned"))

    def test_a_pinned_seat_never_goes_to_a_spent_kind(self):
        d = self.choose(_snap(30, 96), pinned=True)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "switch_alarm"))

    def test_first_launch_swaps_owner_and_reviewer_and_says_why(self):
        lineup = [{"agent": "codex", "model": "", "role": "engineer"},
                  {"agent": "claude", "model": "", "role": "reviewer"}]
        chosen, alloc = dashboard.choose_first_launch_allocation(
            lineup, _snap(30, 52), installed=lambda k: True, pace=PACE_ON, now=self.NOW)
        self.assertEqual([s["agent"] for s in chosen], ["claude", "codex"])
        self.assertTrue(alloc["changed"])
        self.assertIn("Owner switched to Claude: Codex 7-day window at 52%, ahead of pace "
                      "(43.6% by today), while Claude 7-day window at 30%, within pace",
                      alloc["reason"])
        self.assertEqual(alloc["usage"]["decision"], "switch_pace")
        chosen, alloc = dashboard.choose_first_launch_allocation(
            lineup, _snap(30, 52), installed=lambda k: True, pace=PACE_ON, now=self.NOW,
            pinned=True)
        self.assertEqual([s["agent"] for s in chosen], ["codex", "claude"])
        self.assertIn("keeps its agents", alloc["reason"])

    def test_the_owner_handover_is_paced_too(self):
        snap = _snap(30, 52)
        with mock.patch.object(dashboard, "pace_settings", return_value=PACE_ON), \
                mock.patch.object(dashboard.time, "time", return_value=self.NOW):
            moved = rotation.choose_owner_kind({}, {"agent": "codex", "model": ""}, snap,
                                               installed=lambda k: True)
            kept = rotation.choose_owner_kind({"keepAgents": True},
                                              {"agent": "codex", "model": ""}, snap,
                                              installed=lambda k: True)
        self.assertEqual((moved["agent"], moved["changed"]), ("claude", True))
        self.assertIn("ahead of pace", moved["reason"])
        self.assertEqual((kept["agent"], kept["changed"]), ("codex", False))
        self.assertIn("keeps its agents", kept["reason"])


class TheReplayOf0930(unittest.TestCase):
    """2026-09-30: Codex's week reset at 07:04 and it was the preferred kind of
    about 14 tasks. Codex readings as measured; Claude's week (reset 10-04
    10:00, so ~2.9 days in) assumed at 40%, under its own line all morning."""

    CLAUDE_RESET = datetime(2026, 10, 4, 10, 0, tzinfo=LOCAL)
    READINGS = [  # local time, Codex 7-day %
        ((7, 4), 0), ((8, 0), 9), ((9, 0), 27), ((10, 0), 66), ((11, 0), 79), ((15, 0), 85)]

    def setUp(self):
        p = mock.patch.object(dashboard.agents, "get_agent", side_effect=_FakeAgent)
        p.start()
        self.addCleanup(p.stop)

    def test_codex_stops_being_chosen_once_past_its_line(self):
        rows = []
        for (h, m), codex in self.READINGS:
            now = datetime(2026, 9, 30, h, m, tzinfo=LOCAL).timestamp()
            snap = _snap(40, codex, claude_reset=self.CLAUDE_RESET)
            on = dashboard.choose_agent_kind_for_seat("codex", snap, installed=lambda k: True,
                                                      pace=PACE_ON, now=now)
            off = dashboard.choose_agent_kind_for_seat("codex", snap, installed=lambda k: True,
                                                       pace=PACE_OFF, now=now)
            rows.append((f"{h:02}:{m:02}", codex, on["pace"]["kinds"]["codex"]["pace"],
                         on["chosenKind"], on["decision"], off["chosenKind"]))
        self.assertEqual([(r[0], r[3], r[4]) for r in rows], [
            ("07:04", "codex", "preferred_below_warning"),   # 0% under its 15% line
            ("08:00", "codex", "preferred_below_warning"),   # 9% under 15.6%
            ("09:00", "claude", "switch_pace"),              # 27% past 16.2%
            ("10:00", "claude", "switch_pace"),
            ("11:00", "claude", "switch_pace"),
            ("15:00", "claude", "switch_warning"),           # the 80% rule, as before
        ])
        # Without pacing, Codex took every seat until it reached the warning.
        self.assertEqual([r[5] for r in rows], ["codex"] * 5 + ["claude"])
        # Claude was under its own line throughout.
        for (h, m), _ in self.READINGS:
            now = datetime(2026, 9, 30, h, m, tzinfo=LOCAL).timestamp()
            claude = dashboard._kind_pace(_snap(40, 0, claude_reset=self.CLAUDE_RESET),
                                          "claude", now=now)
            self.assertFalse(claude["ahead"], (h, m, claude))


class TheSettingsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [mock.patch.object(dashboard, "DASHBOARD_DIR", d),
                        mock.patch.object(dashboard, "SETTINGS_FILE", d / "settings.json"),
                        mock.patch.object(dashboard.usage, "remark_codex", lambda: None),
                        mock.patch.object(dashboard.Handler, "log_message", lambda *a: None)]
        for p in self.patches:
            p.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_defaults_then_saved_then_read_back(self):
        got = self.call("GET", "/api/settings")
        self.assertEqual((got["paceWeek"], got["paceMarginPoints"]), (True, 15))
        self.call("PUT", "/api/settings", {"paceWeek": False, "paceMarginPoints": 20})
        got = self.call("GET", "/api/settings")
        self.assertEqual((got["paceWeek"], got["paceMarginPoints"]), (False, 20))
        self.assertEqual(dashboard.pace_settings(), {"enabled": False, "margin": 20.0})
        saved = json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))
        self.assertEqual((saved["paceWeek"], saved["paceMarginPoints"]), (False, 20))

    def test_a_margin_is_clamped_and_nonsense_is_ignored(self):
        self.call("PUT", "/api/settings", {"paceMarginPoints": 250})
        self.assertEqual(self.call("GET", "/api/settings")["paceMarginPoints"], 100)
        self.call("PUT", "/api/settings", {"paceMarginPoints": 12.5})
        self.assertEqual(self.call("GET", "/api/settings")["paceMarginPoints"], 12.5)
        for bad in ("lots", None, True, [3]):
            self.call("PUT", "/api/settings", {"paceMarginPoints": bad})
        self.assertEqual(self.call("GET", "/api/settings")["paceMarginPoints"], 12.5)

    def test_usage_carries_each_kind_s_pace_mark(self):
        snap = _snap(30, 52)
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            got = self.call("GET", "/api/usage")
        self.assertTrue(got["pace"]["enabled"])
        self.assertEqual(set(got["pace"]["kinds"]), {"claude", "codex"})
        self.assertEqual(got["pace"]["kinds"]["codex"]["percent"], 52)


class TheTaskPin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.tmp.name) / "rooms"

    def tearDown(self):
        chatroom.ROOMS_DIR = self.old
        self.tmp.cleanup()

    def test_set_and_cleared_on_the_room(self):
        room = chatroom.create_room("t", [{"agent": "codex"}, {"agent": "claude", "role": "reviewer"}])
        ok, updated, _ = dashboard.set_keep_agents(room["id"], True)
        self.assertTrue(ok)
        self.assertIs(chatroom.get_room(room["id"])["keepAgents"], True)
        dashboard.set_keep_agents(room["id"], False)
        self.assertIs(chatroom.get_room(room["id"])["keepAgents"], False)
        self.assertEqual(dashboard.set_keep_agents("room-nope", True)[2], "no_such_room")


if __name__ == "__main__":
    unittest.main()
