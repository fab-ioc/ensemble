"""A task owner hands over by size (#290): it is asked at the limit however
young the session is, and the hub hands over by itself once it has gone
``MARGIN_TOKENS`` further."""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import rotation
from test_rotation_ask import _Base
from test_rotation_kind import RotateOwnerTests, _snap


class AskByAge(_Base):
    def young(self, first, **state):
        s = self.subject(firstTokens=first, **state)
        return s, mock.patch.object(rotation, "_session_started", return_value=time.time() - 300)

    def test_a_young_session_that_grew_is_asked_at_the_limit(self):
        s, started = self.young(50_000)
        with started:
            out = self.check(s)
        self.assertEqual(out["phase"], "asked")
        self.assertIn("If it is not written by 280k, the hub ends this session.",
                      self.sess.typed[0])

    def test_a_young_session_that_started_over_the_limit_is_left_alone(self):
        s, started = self.young(240_000)
        with started:
            out = self.check(s)
        self.assertEqual((out["phase"], self.sess.typed), ("watching", []))
        self.assertIn("the session started 5 min ago at 240k", out["result"])

    def test_it_is_asked_once_it_has_grown(self):
        s, started = self.young(240_000)
        self.tr["tokens"] = 240_000 + rotation.MIN_GROWTH
        with started:
            self.assertEqual(self.check(s)["phase"], "asked")

    def test_past_the_cool_down_growth_does_not_matter(self):
        s = self.subject(firstTokens=250_000)
        with mock.patch.object(rotation, "_session_started",
                               return_value=time.time() - rotation.COOLDOWN_S - 1):
            self.assertEqual(self.check(s)["phase"], "asked")

    def test_a_manual_ask_under_limit_0_promises_no_end(self):
        s = self.subject()
        s["limit"] = 0
        rotation._check(s, True, False)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertNotIn("the hub ends this session", self.sess.typed[0])

    def test_a_dropped_attempt_still_waits(self):
        s = self.subject(firstTokens=50_000, lastAttempt=time.time() - 60)
        self.assertEqual((self.check(s)["phase"], self.sess.typed), ("watching", []))

    def test_a_new_session_forgets_the_first_size(self):
        s = self.subject(firstTokens=250_000)
        s["part"]["sessionId"] = "sid-2"
        self.check(s)
        self.assertEqual(s["state"]["firstTokens"], 250_000)
        self.assertEqual(s["state"]["sessionId"], "sid-2")
        s["part"]["sessionId"] = "sid-3"
        self.tr["tokens"] = 60_000
        self.check(s)
        self.assertEqual(s["state"]["firstTokens"], 60_000)


class Fallback(_Base):
    def over(self, tokens, kind="owner"):
        s = self.asked(kind)
        s["state"]["tokensAtAsk"] = 205_000
        self.tr["tokens"] = tokens
        return s

    def test_below_the_margin_it_waits(self):
        out = self.check(self.over(rotation.DEFAULT_TOKENS + rotation.MARGIN_TOKENS + 4_000))
        self.assertEqual(self.rotated, [])
        self.assertIn("waiting for the owner", out["result"])

    def test_past_it_and_idle_rotates_without_the_twenty_minutes(self):
        self.idle = self.tr["turnOver"] = True
        self.check(self.over(240_000))
        self.assertEqual(self.rotated, [{"answered": False, "asked": True}])

    def test_past_it_and_working_is_ended_mid_turn(self):
        s = self.over(240_000)
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": False, "asked": True}])
        self.assertTrue(s["forced"])
        self.assertEqual(self.sess.typed, [])

    def test_the_margin_counts_from_a_late_ask(self):
        # Asked at 300k (a hub restart, a lower limit): it gets its margin too.
        s = self.over(320_000)
        s["state"]["tokensAtAsk"] = 300_000
        self.check(s)
        self.assertEqual(self.rotated, [])
        self.tr["tokens"] = 330_000
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_a_person_at_the_terminal_holds_it(self):
        s = self.over(240_000)
        self.sess.last_input = time.time()
        with mock.patch.object(rotation, "_typed_since", return_value=True):
            out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertEqual(out["phase"], "watching")
        self.assertIn("lastAttempt", s["state"])

    def test_a_po_is_never_ended_mid_turn(self):
        s = self.over(240_000, "po")
        s["handover"].write_text("# PO\n", encoding="utf-8")
        out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertIn("waiting for the PO", out["result"])

    def test_a_po_idle_past_it_rotates(self):
        s = self.over(240_000, "po")
        s["handover"].write_text("# PO\n", encoding="utf-8")
        self.idle = self.tr["turnOver"] = True
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_a_written_handover_is_not_ended_within_its_extra_margin(self):
        s = self.over(240_000)
        with mock.patch.object(rotation, "_handover_refreshed", return_value=True):
            out = self.check(s)
            self.assertEqual(self.rotated, [])
            self.assertIn("waiting for the owner", out["result"])
            self.tr["tokens"] = 270_000
            self.check(s)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])
        self.assertTrue(s["forced"])

    def test_a_session_at_a_prompt_is_not_ended(self):
        s = self.over(240_000)
        with mock.patch.object(rotation, "_at_prompt", return_value=True):
            out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertIn("waiting for the owner", out["result"])
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_at_prompt_reads_the_screen(self):
        att = dashboard.attention
        for scan, want in (({"prompt": "Allow?"}, True), ({}, False)):
            with mock.patch.object(att, "_claude_status_by_session", return_value={}), \
                    mock.patch.object(att, "_evidence", return_value={"scan": scan}):
                self.assertEqual(rotation._at_prompt({}), want)

    def test_the_fresh_session_hears_whether_it_was_written(self):
        room = {"id": "room-1", "title": "t"}
        unwritten = rotation.task_first_prompt(room, "s", 240_000, Path("h"), False, forced=True)
        written = rotation.task_first_prompt(room, "s", 270_000, Path("h"), False,
                                             forced=True, written=True)
        self.assertIn("had not written its handover", unwritten)
        self.assertIn("after it had written its handover", written)
        self.assertNotIn("had not written", written)
        self.assertIn(".git/index.lock", written)

    def test_off_means_off(self):
        s = self.over(240_000)
        s["limit"] = 0
        self.check(s)
        self.assertEqual(self.rotated, [])


class Defaults(unittest.TestCase):
    def test_each_seat_has_its_own_default_and_value(self):
        with mock.patch.object(dashboard, "load_settings", return_value={}):
            self.assertEqual((rotation.threshold(), rotation.task_threshold()),
                             (150_000, 200_000))
        with mock.patch.object(dashboard, "load_settings",
                               return_value={"taskRotateTokens": 120_000, "poRotateTokens": 0}):
            self.assertEqual((rotation.threshold(), rotation.task_threshold()), (0, 120_000))


class SettingsPage(unittest.TestCase):
    def test_both_limits_sit_in_agent_models(self):
        src = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
        start = src.index('id="settings-models"')
        section = src[start:src.index('class="settings-section"', start)]
        for ident, label in (("pref-task-rotate", "Hand a task session over at"),
                             ("pref-po-rotate", "Hand the PO over at")):
            self.assertEqual(src.count(f'id="{ident}"'), 1)
            self.assertIn(f'for="{ident}">{label}<', section)


def _turn(n, ctx, stop="tool_use"):
    return json.dumps({"type": "assistant", "message": {
        "id": f"m{n}", "stop_reason": stop,
        "usage": {"input_tokens": 1, "cache_read_input_tokens": ctx - 1,
                  "cache_creation_input_tokens": 0}}}) + "\n"


class GrowingRoom(RotateOwnerTests):
    """A fake room whose real transcript grows past the limit, read by the
    real reader, through the ask to a forced hand-over and a fresh session."""

    def _fake(self):
        return type("P", (), {"alive": lambda s: True, "last_submit": lambda s: time.time() - 500,
                              "last_input": 0.0, "send_line": lambda s, t: self.typed.append(t) or True,
                              "info": lambda s: {"idleSeconds": 0.0}})()

    def test_the_hub_hands_over_by_size(self):
        self.typed = []
        room = self.room()
        path = Path(self.temp.name) / "t.jsonl"
        path.write_text(_turn(0, 50_000), encoding="utf-8")
        s = self.subject(room)
        s["state"] = {"phase": "watching"}
        fake = self._fake()
        check = lambda: rotation._check(s, False, False)
        with mock.patch.object(rotation, "_pty", return_value=fake), \
                mock.patch.object(rotation, "_transcript_of",
                                  return_value=(path, rotation.read_transcript)), \
                mock.patch.object(rotation, "_session_started", return_value=time.time() - 120), \
                mock.patch.object(rotation, "_typed_since", return_value=False), \
                mock.patch.object(dashboard, "_type_input",
                                  side_effect=lambda sess, text: fake.send_line(text)), \
                mock.patch.object(dashboard.usage, "snapshot", return_value=_snap(40, 10)):
            self.assertIn("under the 200k limit", check()["result"])
            self.assertEqual(s["state"]["firstTokens"], 50_000)
            with path.open("a", encoding="utf-8") as f:
                f.write(_turn(1, 204_000))
            self.assertEqual(check()["phase"], "asked", "asked at the limit, two minutes old")
            self.assertEqual(len(self.typed), 1)
            with path.open("a", encoding="utf-8") as f:
                f.write(_turn(2, 220_000))
            self.assertIn("waiting for", check()["result"])
            with path.open("a", encoding="utf-8") as f:
                f.write(_turn(3, 236_000))
            out = check()
        self.assertIn("in mid-turn", out["result"])
        rec = out["rotation"]
        self.assertEqual((rec["tokens"], rec["tokensAtEnd"], rec["forced"]),
                         (204_000, 236_000, True))
        text = self.launcher.launched[0]["text"]
        self.assertIn("ended it in mid-turn", text)
        self.assertIn(rotation.TASK_HANDOVER_NAME, text)
        self.assertNotIn("Do the work.", text)
        saved = chatroom.participant(self.saved(room), "claude")
        self.assertEqual(saved["sessionId"], "claude-sid-1")
        self.assertEqual(saved["rotations"][-1]["forced"], True)
        self.assertEqual(s["state"]["phase"], "watching")
        self.assertNotIn("firstTokens", s["state"])


# Only the new test of the class above, not the inherited ones again.
for name in list(vars(RotateOwnerTests)):
    if name.startswith("test_"):
        setattr(GrowingRoom, name, None)
del RotateOwnerTests


if __name__ == "__main__":
    unittest.main()
