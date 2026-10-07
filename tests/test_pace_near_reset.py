"""ED-181: allowance that is about to reset is used, not paced.

10-07 09:15, measured: Claude's 7-day window at 90%, resetting at 14:00 local
(0.97 of the week gone). The pace line was capped at the 80% warning, so Claude
counted as ahead and the 80% rule refused it too: every new owner, review and
handover went to Codex while Claude's last 10% was about to be lost.

In a week's last 12 hours the pace line is no longer capped and the kind is
held to the 95% alarm instead of the warning; its worst window (a spent 5-hour
one too) still refuses it.

Times are built in this machine's local zone so the reasons read "14:00" here
as on the hub.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock

import dashboard
import rotation
import usage

PACE_ON = {"enabled": True, "margin": 15}
PACE_OFF = {"enabled": False, "margin": 15}
CLAUDE_RESET = datetime(2026, 10, 7, 14, 0).astimezone()
CODEX_RESET = datetime(2026, 10, 14, 7, 4).astimezone()   # Codex's week began 10-07 07:04
NOW = datetime(2026, 10, 7, 9, 15).astimezone().timestamp()   # 0.97 of Claude's week
MID_WEEK = (CLAUDE_RESET - timedelta(days=3.5)).timestamp()   # 0.5 of it


class _FakeAgent:
    def __init__(self, kind: str):
        self.display_name = kind.title()

    def installed(self) -> bool:
        return True


def _window(kind, percent, resets_at, pool=None):
    return {"kind": kind, "label": "7-day" if kind == "seven_day" else "5-hour",
            "percent": percent, "trusted": True, "rolledOver": False,
            "resetUnknown": False, "pool": pool,
            "resetsAt": resets_at.isoformat() if resets_at else None}


def _snap(claude_week=90, codex_week=30, claude_five=0, codex_five=0):
    return {"warnPercent": 80, "alarmPercent": 95, "sources": [
        {"source": "claude", "state": "ok",
         "windows": [_window("five_hour", claude_five, None),
                     _window("seven_day", claude_week, CLAUDE_RESET)]},
        {"source": "codex", "state": "ok",
         "windows": [_window("five_hour", codex_five, None),
                     _window("seven_day", codex_week, CODEX_RESET, pool="codex")]},
    ]}


class ThePaceLineNearTheReset(unittest.TestCase):
    def mark(self, now, cap=80):
        return usage.pace_mark(CLAUDE_RESET.isoformat(), now, 15, cap)

    def test_mid_week_it_is_capped_as_before(self):
        mid = self.mark(MID_WEEK)
        self.assertEqual((mid["pace"], mid["elapsed"], mid["nearReset"]), (65.0, 0.5, False))
        day6 = self.mark(CLAUDE_RESET.timestamp() - 13 * 3600)
        self.assertEqual((day6["pace"], day6["nearReset"]), (80, False))

    def test_in_the_last_12_hours_it_is_not_capped(self):
        m = self.mark(NOW)
        self.assertTrue(m["nearReset"])
        self.assertAlmostEqual(m["elapsed"], 0.9717, places=3)
        self.assertAlmostEqual(m["pace"], 97.2 + 15, places=0)          # ~112: never ahead
        self.assertTrue(self.mark(CLAUDE_RESET.timestamp() - 12 * 3600)["nearReset"])

    def test_a_passed_reset_is_not_near_and_stays_capped(self):
        m = self.mark(CLAUDE_RESET.timestamp() + 60)
        self.assertEqual((m["pace"], m["nearReset"]), (80, False))

    def test_the_kind_reading_carries_it(self):
        claude = dashboard._kind_pace(_snap(), "claude", now=NOW)
        codex = dashboard._kind_pace(_snap(), "codex", now=NOW)
        self.assertEqual((claude["nearReset"], claude["ahead"]), (True, False))
        self.assertEqual((codex["nearReset"], codex["ahead"]), (False, True))   # 30% vs ~16%
        before = dashboard._kind_pace(_snap(), "claude", now=MID_WEEK)
        self.assertEqual((before["nearReset"], before["ahead"], before["pace"]), (False, True, 65.0))


class TheSeatChoiceNearTheReset(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(dashboard.agents, "get_agent", side_effect=_FakeAgent)
        p.start()
        self.addCleanup(p.stop)

    def choose(self, snap, preferred, now=NOW, pace=PACE_ON, **kw):
        d = dashboard.choose_agent_kind_for_seat(
            preferred, snap, installed=lambda k: True, pace=pace, now=now, **kw)
        return d["chosenKind"], d["decision"]

    def test_mid_week_90_percent_is_refused(self):
        self.assertEqual(self.choose(_snap(), "claude", MID_WEEK), ("codex", "switch_warning"))
        self.assertEqual(self.choose(_snap(), "codex", MID_WEEK),
                         ("codex", "preferred_below_warning"))

    def test_near_the_reset_90_percent_is_used(self):
        # Before ED-181 both were Codex (switch_warning / preferred_below_warning).
        self.assertEqual(self.choose(_snap(), "claude"), ("claude", "use_before_reset"))
        # Codex is ahead of its fresh week and Claude is no longer refused.
        self.assertEqual(self.choose(_snap(), "codex"), ("claude", "switch_pace"))

    def test_the_warning_rule_follows_it_with_pacing_off(self):
        self.assertEqual(self.choose(_snap(), "claude", pace=PACE_OFF),
                         ("claude", "use_before_reset"))
        self.assertEqual(self.choose(_snap(codex_week=85), "codex", pace=PACE_OFF),
                         ("claude", "switch_warning"))

    def test_a_spent_week_is_refused_near_the_reset(self):
        self.assertEqual(self.choose(_snap(claude_week=96), "claude"), ("codex", "switch_warning"))
        self.assertEqual(self.choose(_snap(claude_week=96), "codex"),
                         ("codex", "preferred_below_warning"))
        # Codex past its warning but not spent: the spent Claude is still not seated.
        self.assertEqual(self.choose(_snap(claude_week=96, codex_week=85), "claude"),
                         ("codex", "switch_alarm"))

    def test_a_spent_5_hour_window_is_refused_near_the_reset(self):
        self.assertEqual(self.choose(_snap(claude_five=96), "claude"), ("codex", "switch_warning"))
        self.assertEqual(self.choose(_snap(claude_five=96), "codex"),
                         ("codex", "preferred_below_warning"))
        # A 5-hour window past the warning only: the week's last hours still count.
        self.assertEqual(self.choose(_snap(claude_five=85), "claude"),
                         ("claude", "use_before_reset"))

    def test_both_spent_is_unchanged(self):
        self.assertEqual(self.choose(_snap(96, 97), "claude"), ("claude", "both_alarm"))

    def test_launch_says_why_in_plain_words(self):
        lineup = [{"agent": "claude", "model": "", "role": "engineer"},
                  {"agent": "codex", "model": "", "role": "reviewer"}]
        chosen, alloc = dashboard.choose_first_launch_allocation(
            lineup, _snap(), installed=lambda k: True, pace=PACE_ON, now=NOW)
        self.assertEqual([s["agent"] for s in chosen], ["claude", "codex"])
        self.assertEqual(alloc["reason"],
                         "Preferred line-up kept: Claude 7-day window at 90%, and its week "
                         "resets 14:00: what is left is used up to the 95% alarm.")
        swapped = [dict(lineup[1], role="engineer"), dict(lineup[0], role="reviewer")]
        chosen, alloc = dashboard.choose_first_launch_allocation(
            swapped, _snap(), installed=lambda k: True, pace=PACE_ON, now=NOW)
        self.assertEqual([s["agent"] for s in chosen], ["claude", "codex"])
        self.assertIn("while Claude 7-day window at 90%, its week resets 14:00",
                      alloc["reason"])

    def test_the_review_says_why(self):
        d = dashboard.choose_agent_kind_for_seat("claude", _snap(), installed=lambda k: True,
                                                 current_kind="claude", pace=PACE_ON, now=NOW)
        self.assertEqual(
            dashboard._review_allocation_reason(d, {"agent": "codex"}),
            "Reviewer kept on Claude, different from owner Codex: Claude 7-day window at 90%, "
            "and its week resets 14:00: what is left is used up to the 95% alarm.")
        # The owner on a Claude week about to reset: its limit is named as such.
        d = dashboard.choose_agent_kind_for_seat("codex", _snap(codex_week=85),
                                                 installed=lambda k: True, current_kind="codex",
                                                 pace=PACE_ON, now=NOW)
        self.assertIn("Claude 7-day window at 90% is below the 95% alarm (its week resets "
                      "14:00)", dashboard._review_allocation_reason(d, {"agent": "claude"}))

    def test_the_owner_handover_follows_it(self):
        with mock.patch.object(dashboard, "pace_settings", return_value=PACE_ON), \
                mock.patch.object(dashboard.time, "time", return_value=NOW):
            kept = rotation.choose_owner_kind({}, {"agent": "claude", "model": ""}, _snap(),
                                              installed=lambda k: True)
            moved = rotation.choose_owner_kind({}, {"agent": "claude", "model": ""},
                                               _snap(claude_five=96), installed=lambda k: True)
        self.assertEqual((kept["agent"], kept["changed"]), ("claude", False))
        self.assertIn("Owner kept on Claude: Claude 7-day window at 90%, and its week resets "
                      "14:00", kept["reason"])
        self.assertEqual((moved["agent"], moved["changed"]), ("codex", True))


class TheResetClock(unittest.TestCase):
    def test_today_tomorrow_and_later(self):
        at = CLAUDE_RESET.isoformat()
        self.assertEqual(dashboard._reset_clock(at, NOW), "14:00")
        evening = datetime(2026, 10, 6, 22, 0).astimezone().timestamp()
        self.assertEqual(dashboard._reset_clock(at, evening), "tomorrow 14:00")
        self.assertEqual(dashboard._reset_clock(None, NOW), "soon")


if __name__ == "__main__":
    unittest.main()
