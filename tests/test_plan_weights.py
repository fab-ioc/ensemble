"""Subscription detection and weighted seat selection for ED-203."""
import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import dashboard
import plan_weights
import usage


def _jwt(plan):
    payload = {"https://api.openai.com/auth": {"chatgpt_plan_type": plan}}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"fake.{body}.fake"


class PlanDetection(unittest.TestCase):
    def test_usage_refresh_caches_only_safe_plan_metadata(self):
        safe = {"claude": {"weight": 20, "label": "Claude Max 20x"},
                "codex": {"weight": 5, "label": "Codex Pro 5x"}}
        with mock.patch.dict(usage._STATE, {}, clear=False), \
                mock.patch.object(usage, "read_claude", return_value={"source": "claude", "state": "unavailable"}), \
                mock.patch.object(usage, "read_codex", return_value={"source": "codex", "state": "unavailable"}), \
                mock.patch.object(usage.plan_weights, "detect", return_value=safe) as detect:
            result = usage.refresh()
            self.assertEqual(result["plans"], safe)
            self.assertEqual(usage.snapshot()["plans"], safe)
            self.assertNotIn("id_token", str(result))
            detect.assert_called_once_with()

    def test_known_tiers_from_fake_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude, codex = root / "claude.json", root / "codex.json"
            for tier, subscription, weight in (("default_claude_max_20x", "max", 20),
                                                ("default_claude_max_5x", "max", 5),
                                                ("", "pro", 1)):
                claude.write_text(json.dumps({"claudeAiOauth": {
                    "subscriptionType": subscription, "rateLimitTier": tier}}))
                codex.write_text(json.dumps({"tokens": {"id_token": _jwt("prolite")}}))
                plans = plan_weights.detect(claude, codex)
                self.assertEqual(plans["claude"]["weight"], weight)
                self.assertEqual(plans["codex"]["weight"], 5)
                self.assertNotIn("fake.", str(plans))
            for tier, weight in (("plus", 1), ("prolite", 5), ("pro", 20)):
                codex.write_text(json.dumps({"tokens": {"id_token": _jwt(tier)}}))
                self.assertEqual(plan_weights.detect(claude, codex)["codex"]["weight"], weight)

    def test_missing_garbled_and_unknown_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude, codex = root / "claude.json", root / "codex.json"
            self.assertIsNone(plan_weights.detect(claude, codex)["claude"]["weight"])
            claude.write_text("not json")
            codex.write_text(json.dumps({"tokens": {"id_token": "bad.jwt"}}))
            plans = plan_weights.detect(claude, codex)
            self.assertIsNone(plans["claude"]["weight"])
            self.assertIsNone(plans["codex"]["weight"])
            codex.write_text(json.dumps({"tokens": {"id_token": _jwt("enterprise")}}))
            self.assertIsNone(plan_weights.detect(claude, codex)["codex"]["weight"])

    def test_override_wins_and_can_be_cleared(self):
        plans = {"claude": {"weight": 20, "label": "Claude Max 20x"},
                 "codex": {"weight": 5, "label": "Codex Pro 5x"}}
        with mock.patch.object(dashboard, "load_settings", return_value={
                "paceWeek": True, "claudePlanWeight": 3, "codexPlanWeight": ""}):
            cfg = dashboard.pace_settings({"plans": plans})
        self.assertEqual(cfg["weights"], {"claude": 3.0, "codex": 5.0})
        self.assertEqual(cfg["sources"], {"claude": "manual override", "codex": "detected"})
        with mock.patch.object(dashboard, "load_settings", return_value={}):
            cfg = dashboard.pace_settings({"plans": {}})
        self.assertEqual(cfg["weights"], {"claude": 1.0, "codex": 1.0})
        self.assertEqual(cfg["sources"]["codex"], "equal fallback")
        with mock.patch.object(dashboard, "load_settings", return_value={}):
            cfg = dashboard.pace_settings({"plans": {"claude": plans["claude"]}})
        self.assertEqual(cfg["weights"], {"claude": 1.0, "codex": 1.0})


class WeightedDecision(unittest.TestCase):
    def snapshot(self, claude, codex, claude_hours=100, codex_hours=100):
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        def windows(pct, hours, pool=None):
            return [{"kind": "seven_day", "percent": pct, "trusted": True,
                     "rolledOver": False, "resetUnknown": False, "pool": pool,
                     "resetsAt": (now + timedelta(hours=hours)).isoformat()}]
        snap = {"warnPercent": 80, "alarmPercent": 95, "sources": [
            {"source": "claude", "state": "ok", "windows": windows(claude, claude_hours)},
            {"source": "codex", "state": "ok", "windows": windows(codex, codex_hours, "codex")}]}
        return snap, now.timestamp()

    def choose(self, claude, codex, *, preferred="claude", claude_hours=100,
               codex_hours=100, weights=None, keep=False):
        snap, now = self.snapshot(claude, codex, claude_hours, codex_hours)
        pace = {"enabled": True, "margin": 15, "hysteresis": 25,
                "weights": weights or {"claude": 20, "codex": 5},
                "labels": {"claude": "Claude Max 20x", "codex": "Codex Pro 5x"},
                "sources": {"claude": "detected", "codex": "detected"}}
        decision = dashboard.choose_agent_kind_for_seat(
            preferred, snap, installed=lambda kind: True, pace=pace,
            keep_agents=keep, now=now)
        return decision, snap, now, pace

    def test_both_below_warning_use_larger_weighted_spare_per_hour(self):
        d, _, _, _ = self.choose(50, 0, preferred="codex")
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "switch_balance"))
        reason = dashboard._balance_reason(d)
        for fragment in ("Claude Max 20x", "Codex Pro 5x", "30% to warning",
                         "80% to warning", "50% used against", "0% used against",
                         "units/h"):
            self.assertIn(fragment, reason)

    def test_hysteresis_keeps_preferred_within_25_percent(self):
        d, _, _, _ = self.choose(62, 0, preferred="claude", weights={"claude": 4, "codex": 1})
        self.assertEqual((d["chosenKind"], d["decision"]),
                         ("claude", "preferred_within_margin"))

    def test_soon_reset_changes_relative_capacity(self):
        d, _, _, _ = self.choose(70, 0, preferred="codex", claude_hours=5)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "switch_balance"))

    def test_warning_alarm_keep_and_unknown_pace(self):
        self.assertEqual(self.choose(85, 10)[0]["decision"], "switch_warning")
        self.assertEqual(self.choose(96, 10)[0]["decision"], "switch_warning")
        self.assertEqual(self.choose(85, 10, keep=True)[0]["decision"], "keep_agents")
        d, snap, now, pace = self.choose(20, 10)
        snap["sources"][0]["windows"][0]["resetsAt"] = None
        d = dashboard.choose_agent_kind_for_seat(
            "claude", snap, installed=lambda kind: True, pace=pace, now=now)
        self.assertEqual((d["chosenKind"], d["decision"]),
                         ("claude", "preferred_below_warning"))

    def test_reviews_cross_check_only_within_other_pace(self):
        d, snap, now, pace = self.choose(20, 10, preferred="codex")
        d = dashboard._review_cross_check(d, "claude", "claude", lambda kind: True, False)
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "cross_check"))
        self.assertIn("fresh review session", dashboard._review_allocation_reason(d, {"agent": "claude"}))
        d, _, _, _ = self.choose(20, 70, preferred="codex")
        d = dashboard._review_cross_check(d, "claude", "codex", lambda kind: True, False)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "review_owner_pace"))
        self.assertIn("fresh review session", dashboard._review_allocation_reason(d, {"agent": "claude"}))
        d, _, _, _ = self.choose(20, 85, preferred="codex")
        d = dashboard._review_cross_check(d, "claude", "codex", lambda kind: True, False)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "review_owner_warning"))


if __name__ == "__main__":
    unittest.main()
