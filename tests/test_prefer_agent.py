"""Settings › Agent models › Prefer (P205/P206): every seat the hub chooses goes
to the preferred kind while it is usable, else the other kind with a reason."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_models
import attention
import chatroom
import dashboard
import rotation


def _window(kind, percent, trusted=True):
    return {"kind": kind, "percent": percent, "trusted": trusted,
            "rolledOver": False, "resetUnknown": False}


def _snap(claude, codex, kind="seven_day"):
    return {"warnPercent": 80, "alarmPercent": 95, "checkedAt": 1.0, "sources": [
        {"source": "claude", "state": "ok", "windows": [_window(kind, claude)]},
        {"source": "codex", "state": "ok", "windows": [_window(kind, codex)]},
    ]}


def _pace(prefer="claude"):
    return {"enabled": True, "prefer": prefer, "margin": 15, "hysteresis": 25,
            "weights": {"claude": 1, "codex": 1}, "labels": {}, "sources": {}}


_ALL = lambda kind: kind in ("claude", "codex")


class SeatTests(unittest.TestCase):
    def choose(self, claude, codex, blocked=None, installed=_ALL, **kw):
        return dashboard.choose_agent_kind_for_seat(
            "codex", _snap(claude, codex), installed=installed, pace=_pace(),
            blocked=blocked or {}, **kw)

    def test_claude_preferred_below_100_stays_on_claude(self):
        for percent in (81, 95, 99):
            d = self.choose(percent, 0)
            self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "prefer_setting"))
            self.assertEqual(dashboard._prefer_reason(d, "Owner"),
                             f"Owner switched to Claude: Claude preferred by Settings "
                             f"(Claude 7-day window at {percent}%).")

    def test_spent_claude_goes_to_codex_with_reason(self):
        d = self.choose(100, 10)
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "prefer_spent"))
        reason = dashboard._prefer_reason(d, "Reviewer")
        self.assertTrue(reason.startswith("Reviewer kept on Codex: Claude preferred by "
                                          "Settings, but Claude 7-day window at 100%"), reason)
        self.assertIn("back to Claude at its next start", reason)

    def test_blocked_or_login_or_uninstalled_goes_to_codex(self):
        # A frozen reading (≥70%) does not outweigh the block a session shows.
        snap = _snap(70, 10)
        snap["sources"][0]["windows"][0]["trusted"] = False
        for blocked, phrase in (({"claude": "usage_limit"}, "blocked by its usage limit"),
                                ({"claude": "auth"}, "needs a login")):
            d = dashboard.choose_agent_kind_for_seat(
                "codex", snap, installed=_ALL, pace=_pace(), blocked=blocked)
            self.assertEqual(d["chosenKind"], "codex")
            self.assertIn(phrase, dashboard._prefer_reason(d, "Owner"))
        d = self.choose(10, 10, installed=lambda k: k == "codex")
        self.assertEqual(d["chosenKind"], "codex")
        self.assertIn("Claude is not installed", dashboard._prefer_reason(d, "Owner"))

    def test_trusted_reading_below_limit_outweighs_a_stale_block(self):
        d = dashboard.choose_agent_kind_for_seat(
            "claude", _snap(20, 10), installed=_ALL, pace=_pace(),
            blocked={"claude": "usage_limit"})
        self.assertEqual(d["chosenKind"], "claude")

    def test_unknown_reading_stays_on_preferred(self):
        snap = _snap(10, 10)
        snap["sources"][0] = {"source": "claude", "state": "error"}
        d = dashboard.choose_agent_kind_for_seat("codex", snap, installed=_ALL,
                                                 pace=_pace(), blocked={})
        self.assertEqual(d["chosenKind"], "claude")

    def test_both_spent_keeps_preferred(self):
        d = self.choose(100, 100)
        self.assertEqual((d["chosenKind"], d["decision"]), ("claude", "prefer_both_spent"))
        self.assertIn("both kinds are spent", dashboard._prefer_reason(d, "Owner"))

    def test_back_on_claude_once_usable(self):
        d = dashboard.choose_agent_kind_for_seat(
            "claude", _snap(30, 10), installed=_ALL, current_kind="codex",
            pace=_pace(), blocked={})
        self.assertEqual(d["chosenKind"], "claude")
        self.assertTrue(d["changed"])

    def test_keep_agents_wins_over_the_preference(self):
        d = dashboard.choose_agent_kind_for_seat(
            "codex", _snap(30, 10), installed=_ALL, pace=_pace(), keep_agents=True,
            blocked={})
        self.assertEqual((d["chosenKind"], d["decision"]), ("codex", "keep_agents"))

    def test_balanced_is_todays_rule(self):
        d = dashboard.choose_agent_kind_for_seat(
            "claude", _snap(86, 10, "five_hour"), installed=_ALL,
            pace={**_pace(""), "enabled": False}, blocked={"claude": "usage_limit"})
        self.assertFalse(d["decision"].startswith("prefer_"))
        self.assertEqual(d["chosenKind"], "codex")

    def test_review_cross_check_leaves_a_preference_alone(self):
        d = self.choose(81, 0)
        self.assertIs(dashboard._review_cross_check(d, "claude", "codex", _ALL, False), d)
        self.assertEqual(dashboard._review_allocation_reason(d, {"agent": "claude"}),
                         "Reviewer switched to Claude: Claude preferred by Settings "
                         "(Claude 7-day window at 81%).")


class BlockedKindsTests(unittest.TestCase):
    def test_reads_usage_limit_and_auth_from_attention(self):
        items = [{"state": "blocked", "agent": "claude", "cause": "auth"},
                 {"state": "blocked", "agent": "claude", "cause": "usage_limit"},
                 {"state": "blocked", "agent": "codex", "cause": "model_limit"},
                 {"state": "waiting_for_you", "agent": "codex", "cause": "usage_limit"}]
        with mock.patch.object(attention, "cached_items", return_value=items):
            self.assertEqual(dashboard._blocked_kinds(), {"claude": "usage_limit"})


class LaunchTests(unittest.TestCase):
    preferred = [{"agent": "codex", "model": "", "role": "engineer"},
                 {"agent": "codex", "model": "", "role": "reviewer"}]

    def test_launch_puts_every_seat_on_claude(self):
        chosen, alloc = dashboard.choose_first_launch_allocation(
            self.preferred, _snap(90, 0), installed=_ALL, pace=_pace(), blocked={})
        self.assertEqual([s["agent"] for s in chosen], ["claude", "claude"])
        self.assertIn("Owner switched to Claude: Claude preferred by Settings "
                      "(Claude 7-day window at 90%).", alloc["reason"])
        self.assertIn("Reviewer switched to Claude: Claude preferred by Settings",
                      alloc["reason"])
        self.assertEqual(alloc["usage"]["decision"], "prefer_setting")

    def test_launch_with_claude_spent_uses_codex(self):
        chosen, alloc = dashboard.choose_first_launch_allocation(
            self.preferred, _snap(100, 0), installed=_ALL, pace=_pace(), blocked={})
        self.assertEqual([s["agent"] for s in chosen], ["codex", "codex"])
        self.assertIn("but Claude 7-day window at 100%", alloc["reason"])

    def test_launch_keep_agents_unchanged(self):
        chosen, alloc = dashboard.choose_first_launch_allocation(
            self.preferred, _snap(10, 0), installed=_ALL, pace=_pace(), keep_agents=True,
            blocked={})
        self.assertEqual([s["agent"] for s in chosen], ["codex", "codex"])
        self.assertNotIn("preferred by Settings", alloc["reason"])


class _Settings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(dashboard, "SETTINGS_FILE",
                                          Path(self.temp.name) / "settings.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()


class SettingsTests(_Settings):
    def test_default_is_balanced_and_bad_values_are_dropped(self):
        self.assertEqual(dashboard.load_settings()["preferAgent"], "balanced")
        self.assertEqual(dashboard.hub_preference(), "")
        dashboard.save_settings({"preferAgent": "claude"})
        self.assertEqual(dashboard.hub_preference(), "claude")
        dashboard.save_settings({"preferAgent": "gpt"})
        self.assertEqual(dashboard.load_settings()["preferAgent"], "claude")
        self.assertEqual(dashboard.pace_settings({})["prefer"], "claude")


class _FakeAgent:
    def __init__(self, kind):
        self.display_name = kind.title()

    def installed(self):
        return True


class HandoverTests(_Settings):
    def setUp(self):
        super().setUp()
        self.old_rooms_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.patches += [
            mock.patch.object(dashboard.agents, "get_agent", side_effect=_FakeAgent),
            mock.patch.object(attention, "cached_items", return_value=[]),
        ]
        for p in self.patches[1:]:
            p.start()
        dashboard.save_settings({"preferAgent": "claude"})

    def tearDown(self):
        chatroom.ROOMS_DIR = self.old_rooms_dir
        super().tearDown()

    def owner(self, kind):
        created = chatroom.create_room("prefer test", [
            {"identity": "eng", "agent": kind, "model": "", "role": "engineer"}])
        room = chatroom.get_room(created["id"], public=False)
        return room, chatroom.agent_participants(room)[0]

    def test_codex_owner_goes_back_to_claude(self):
        room, part = self.owner("codex")
        c = rotation.choose_owner_kind(room, part, snapshot=_snap(95, 10))
        self.assertTrue(c["changed"])
        self.assertEqual(c["agent"], "claude")
        self.assertTrue(c["reason"].startswith(
            "Owner switched to Claude: Claude preferred by Settings (Claude 7-day window "
            "at 95%)."), c["reason"])

    def test_spent_claude_owner_goes_to_codex(self):
        room, part = self.owner("claude")
        c = rotation.choose_owner_kind(room, part, snapshot=_snap(100, 10))
        self.assertEqual(c["agent"], "codex")
        self.assertIn("Claude 7-day window at 100%, its limit", c["why"])


class NewestTierTests(unittest.TestCase):
    def test_highest_major_version_is_marked(self):
        models = [{"id": "gpt-6-sol"}, {"id": "gpt-5.6-sol"}, {"id": "gpt-5.3-codex"},
                  {"id": "nameless"}]
        marked = agent_models.mark_newest(models)
        self.assertEqual([m.get("tier") for m in marked], ["newest", None, None, None])
        self.assertNotIn("tier", models[0])

    def test_one_version_marks_nothing(self):
        marked = agent_models.mark_newest([{"id": "gpt-5.6-sol"}, {"id": "gpt-5.3-codex"}])
        self.assertFalse(any(m.get("tier") for m in marked))


if __name__ == "__main__":
    unittest.main()
