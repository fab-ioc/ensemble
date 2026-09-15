"""The handover ask is typed while the agent is busy (rotation._check), and
only a person at its terminal holds the rotation that follows."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import rotation


class _FakePty:
    def __init__(self):
        self.typed = []
        self._last_submit = 0.0
        self.last_input = 0.0

    def alive(self) -> bool:
        return True

    def send_line(self, text):
        self.typed.append(text)
        self._last_submit = time.time()
        return True

    def last_submit(self) -> float:
        return self._last_submit


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sess = _FakePty()
        self.idle = False
        self.tr = {"tokens": 250_000, "turnOver": False, "promptSince": False, "size": 1000}
        self.sinces = []
        self.rotated = []
        self.patches = [
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.sess),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
            mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (
                Path(self.temp.name) / "t.jsonl", self.reader)),
            mock.patch.object(rotation, "_rotate_marked", side_effect=self.rotate_marked),
            mock.patch.object(rotation, "_log"),
            mock.patch.object(dashboard, "operator_name", return_value="ceo"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.assertFalse(rotation._ROTATING)
        self.temp.cleanup()

    def reader(self, path, since=-1):
        self.sinces.append(since)
        return dict(self.tr)

    def rotate_marked(self, s, tr, done, answered, asked, key, flags):
        self.rotated.append({"answered": answered, "asked": asked})
        s["state"]["phase"] = "watching"
        return done("rotated")

    def subject(self, kind="owner", **state):
        part = {"identity": "claude", "agent": "claude", "sessionId": "sid-1", "ptyId": "pty-1"}
        st = {"phase": "watching", "sessionId": "sid-1", **state}
        base = {"room": {"id": "room-1"}, "part": part, "why": "", "state": st,
                "handover": Path(self.temp.name) / "HANDOVER.md"}
        if kind == "po":
            return {**base, "kind": "po", "name": "P", "project": {"id": "p1"},
                    "limit": 150_000, "setting": "poRotateTokens", "who": "the PO",
                    "whose": "the PO's", "handoverName": rotation.HANDOVER_NAME,
                    "ids": {"projectId": "p1"}}
        return {**base, "kind": "owner", "name": "t / claude", "project": None,
                "limit": 200_000, "setting": "taskRotateTokens",
                "who": "the owner (claude)", "whose": "claude's",
                "handoverName": rotation.TASK_HANDOVER_NAME,
                "ids": {"roomId": "room-1", "identity": "claude"}}

    def check(self, s):
        return rotation._check(s, False, False)

    def asked(self, kind="owner", ago=60.0):
        """A subject asked ``ago`` seconds ago, the ask's own submit then."""
        at = time.time() - ago
        self.sess._last_submit = at
        return self.subject(kind, phase="asked", askedAt=at, askSize=900,
                            askPath=str(Path(self.temp.name) / "t.jsonl"),
                            askSubmit=at, tokensAtAsk=250_000, handoverAtAsk=0)


class AskTests(_Base):
    def test_over_the_limit_and_busy_asks_once(self):
        s = self.subject()
        out = self.check(s)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertTrue(self.sess.typed[0].startswith("[handover] Your conversation has reached 250k"))
        self.assertIn("HANDOVER.md now", self.sess.typed[0])
        self.assertEqual(out["phase"], "asked")
        self.assertEqual(s["state"]["askSize"], 1000)
        self.assertEqual(s["state"]["askSubmit"], self.sess._last_submit)
        self.assertIn("asked the owner (claude) to update its handover (it was busy; it reads "
                      "the ask at its next pause)", out["result"])
        # Still busy a minute later: it waits, and never asks twice.
        s["state"]["askedAt"] -= 60
        out = self.check(s)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertEqual(out["phase"], "asked")
        self.assertIn("waiting for the owner (claude) to update its handover", out["result"])
        self.assertEqual(self.sinces[-1], 1000)
        self.assertEqual(self.rotated, [])

    def test_over_the_limit_and_idle_asks_once(self):
        self.idle = True
        s = self.subject()
        out = self.check(s)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertEqual(out["phase"], "asked")
        self.assertTrue(out["result"].endswith("asked the owner (claude) to update its handover"))
        self.assertEqual(self.rotated, [])

    def test_under_the_limit_does_nothing(self):
        self.tr["tokens"] = 150_000
        s = self.subject()
        out = self.check(s)
        self.assertEqual(self.sess.typed, [])
        self.assertEqual(out["phase"], "watching")

    def test_the_po_is_asked_while_busy_with_its_own_text(self):
        s = self.subject("po")
        out = self.check(s)
        self.assertEqual(out["phase"], "asked")
        self.assertEqual(len(self.sess.typed), 1)
        self.assertIn("start a fresh PO session", self.sess.typed[0])
        self.assertIn("what you have promised ceo", self.sess.typed[0])
        self.assertIn("(it was busy;", out["result"])

    def test_a_young_session_is_not_asked(self):
        s = self.subject(lastAttempt=time.time() - 60)
        out = self.check(s)
        self.assertEqual(self.sess.typed, [])
        self.assertEqual(out["phase"], "watching")

    def test_immediate_still_waits_for_idle(self):
        s = self.subject()
        out = rotation._check(s, True, True)
        self.assertEqual((self.sess.typed, self.rotated), ([], []))
        self.assertIn("waiting for the owner (claude) to be idle", out["result"])

    def test_a_busy_agent_past_give_up_is_asked_again_after_the_cool_down(self):
        s = self.asked(ago=rotation.GIVE_UP_S + 10)
        out = self.check(s)
        self.assertEqual(out["phase"], "watching")
        self.assertIn("gave up waiting", out["result"])
        self.assertEqual(self.sess.typed, [])
        out = self.check(s)                         # inside the cool-down
        self.assertEqual((out["phase"], self.sess.typed), ("watching", []))
        s["state"]["lastAttempt"] = time.time() - rotation.COOLDOWN_S - 10
        out = self.check(s)                         # still busy: asked anyway
        self.assertEqual(out["phase"], "asked")
        self.assertEqual(len(self.sess.typed), 1)


class AskedTests(_Base):
    def test_idle_and_answered_rotates(self):
        s = self.asked()
        self.idle, self.tr["promptSince"], self.tr["turnOver"] = True, True, True
        out = self.check(s)
        self.assertEqual(out["result"], "rotated")
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])
        self.assertEqual(self.sess.typed, [])

    def test_idle_but_not_answered_waits_until_the_timeout(self):
        s = self.asked()
        self.idle = True
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))
        s["state"]["askedAt"] -= rotation.ASK_TIMEOUT_S
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": False, "asked": True}])

    def test_a_hub_line_during_the_ask_does_not_drop_it(self):
        s = self.asked()
        # A [report] doorbell after the ask: the agent took a turn for it and
        # is idle again. Only last_submit moved.
        self.sess._last_submit = time.time() - 30
        self.idle, self.tr["promptSince"] = True, True
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_a_hub_line_just_before_the_gate_puts_the_rotation_off(self):
        s = self.asked()
        self.idle, self.tr["promptSince"] = True, True
        self.sess._last_submit = time.time()        # a doorbell this very moment
        out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertEqual(out["phase"], "asked")
        self.assertNotIn("lastAttempt", s["state"])
        self.assertIn("rotating once it is idle", out["result"])
        self.sess._last_submit = time.time() - 30
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_a_person_typing_drops_the_attempt(self):
        s = self.asked()
        self.sess.last_input = time.time() - 20      # after the ask
        self.idle, self.tr["promptSince"] = True, True
        out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertEqual(out["phase"], "watching")
        self.assertIn("attempt is dropped", out["result"])
        self.assertIn("lastAttempt", s["state"])

    def test_a_person_typing_before_the_ask_does_not_count(self):
        s = self.asked()
        self.sess.last_input = time.time() - 120     # before the ask
        self.idle, self.tr["promptSince"] = True, True
        self.check(s)
        self.assertEqual(len(self.rotated), 1)


class TranscriptPromptTests(unittest.TestCase):
    """What counts as the ask having reached a Claude transcript."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "t.jsonl"
        self.add({"type": "user", "message": {"role": "user", "content": "do the work"}},
                 self.assistant("tool_use"))
        self.since = self.path.stat().st_size

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def assistant(stop):
        return {"type": "assistant", "message": {
            "role": "assistant", "stop_reason": stop, "content": [{"type": "text", "text": "x"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 250_000}}}

    @staticmethod
    def tool_result():
        return {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}

    def add(self, *entries):
        with self.path.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def read(self):
        return rotation.read_transcript(self.path, self.since)

    def test_tool_results_of_a_busy_agent_are_not_the_ask(self):
        self.add(self.tool_result(), self.assistant("tool_use"), self.tool_result(),
                 {"type": "user", "isMeta": True, "message": {"role": "user", "content": "caveat"}})
        out = self.read()
        self.assertFalse(out["promptSince"])
        self.assertEqual(out["tokens"], 250_010)

    def test_a_line_read_mid_turn_is_the_ask(self):
        self.add(self.tool_result(),
                 {"type": "queue-operation"},
                 {"type": "attachment", "attachment": {"type": "queued_command",
                                                       "prompt": "[handover] Your conversation"}},
                 self.assistant("end_turn"), {"type": "last-prompt"})
        out = self.read()
        self.assertTrue(out["promptSince"])
        self.assertTrue(out["turnOver"])

    def test_a_typed_prompt_is_the_ask(self):
        self.add(self.assistant("end_turn"),
                 {"type": "user", "message": {"role": "user", "content": "[handover] Your"}},
                 self.assistant("end_turn"))
        self.assertTrue(self.read()["promptSince"])

    def test_a_line_read_before_the_ask_does_not_count(self):
        self.since = 0
        self.add({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "x"}})
        self.since = self.path.stat().st_size
        self.add(self.tool_result())
        self.assertFalse(self.read()["promptSince"])


class WhatCountsAsAPersonTests(unittest.TestCase):
    def test_typed_by_person(self):
        self.assertTrue(dashboard.typed_by_person("please stop and look at #12"))
        self.assertTrue(dashboard.typed_by_person("[wip] brackets are a person's too"))
        for hub in ("[report] completed from task 'x' (#3, claude): done",
                    "[digest] P: nothing new", dashboard._relay_wake("codex"),
                    dashboard.RESUME_NOTE, "[handover] Your conversation",
                    "[from the PO] Review 2: fix the finding"):
            self.assertFalse(dashboard.typed_by_person(hub), hub)

    def test_delivering_to_a_solo_agent_marks_only_a_persons_line(self):
        class H(dashboard.Handler):
            def __init__(self):
                pass

        sess = _FakePty()
        room = {"id": "room-solo", "mode": "solo",
                "participants": [{"kind": "agent", "identity": "claude", "ptyId": "p1"}]}
        with mock.patch.object(dashboard.ptyrun, "get", return_value=sess), \
                mock.patch.object(dashboard, "with_message_refs", side_effect=lambda t, rid: t):
            H()._deliver_now(room, [{"text": "[from the PO] Review 2: fix it", "to": ""}])
            self.assertEqual(sess.last_input, 0.0)
            self.assertEqual(len(sess.typed), 1)
            H()._deliver_now(room, [{"text": "hold on, I am testing", "to": ""}])
            self.assertGreater(sess.last_input, 0.0)


if __name__ == "__main__":
    unittest.main()
