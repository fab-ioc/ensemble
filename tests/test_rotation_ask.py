"""The handover ask is typed while the agent is busy (rotation._check), and
only a person at its terminal holds the rotation that follows."""
from __future__ import annotations

import json
import os
import tempfile
import threading
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
        self.hook_idle = False
        self.tr = {"tokens": 250_000, "turnOver": False, "promptSince": False, "size": 1000}
        self.sinces = []
        self.rotated = []
        self.patches = [
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.sess),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
            mock.patch.object(rotation, "_hook_idle", side_effect=lambda part, since: self.hook_idle),
            mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (
                Path(self.temp.name) / "t.jsonl", self.reader)),
            mock.patch.object(rotation, "_rotate_marked", side_effect=self.rotate_marked),
            mock.patch.object(rotation, "_log"),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
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
        self.assertIn("what you have promised sam", self.sess.typed[0])
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

    def test_a_gate_that_keeps_finding_a_submit_gives_up_past_give_up(self):
        s = self.asked(ago=rotation.GIVE_UP_S + 10)
        self.idle, self.tr["promptSince"] = True, True
        self.sess._last_submit = time.time()
        out = self.check(s)
        self.assertEqual((self.rotated, out["phase"]), ([], "watching"))
        self.assertIn("lastAttempt", s["state"])

    def test_a_person_typing_before_the_ask_does_not_count(self):
        s = self.asked()
        self.sess.last_input = time.time() - 120     # before the ask
        self.idle, self.tr["promptSince"] = True, True
        self.check(s)
        self.assertEqual(len(self.rotated), 1)


class HandoverWrittenTests(_Base):
    """A handover file written after the ask answers it once the agent is idle,
    whether or not the ask shows in its transcript."""

    def write(self, s, offset, text="# Handover\nnext: run the tests\n"):
        hp = s["handover"]
        hp.write_text(text, encoding="utf-8")
        at = float(s["state"]["askedAt"]) + offset
        os.utime(hp, (at, at))

    def test_fresh_file_and_idle_rotates(self):
        s = self.asked()
        self.write(s, +30)
        self.hook_idle = True               # hooks say idle; transcript shows no ask
        out = self.check(s)
        self.assertEqual(out["result"], "rotated")
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])

    def test_fresh_file_and_idle_by_the_transcript_rotates(self):
        s = self.asked()
        self.write(s, +30)
        self.idle = True
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])

    def test_fresh_file_but_working_waits(self):
        s = self.asked()
        self.write(s, +30)
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))
        self.assertIn("waiting for the owner (claude)", out["result"])

    def test_stale_file_and_idle_waits_for_the_timeout(self):
        s = self.asked()
        self.write(s, -30)
        self.idle = self.hook_idle = True
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))
        s["state"]["askedAt"] -= rotation.ASK_TIMEOUT_S
        self.write(s, -30)                          # still older than the ask
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": False, "asked": True}])

    def test_an_emptied_file_does_not_count(self):
        s = self.asked()
        self.write(s, +30, text="  \n")
        self.hook_idle = True
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_the_po_rotates_on_its_fresh_file_too(self):
        s = self.asked("po")
        self.write(s, +30)
        self.hook_idle = True
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])

    def test_busy_again_at_the_gate_puts_it_off(self):
        s = self.asked()
        self.write(s, +30)
        self.hook_idle = True
        self.sess._last_submit = time.time()        # a doorbell this very moment
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_no_double_launch(self):
        s = self.asked()
        self.write(s, +30)
        self.hook_idle = True
        entered = threading.Event()

        def slow_rotate(s_, tr, done, answered, asked, key, flags):
            entered.set()
            time.sleep(0.3)
            # What the real one leaves: the seat on the fresh session.
            s_["part"]["sessionId"] = "sid-2"
            self.tr["tokens"] = 60_000
            return self.rotate_marked(s_, tr, done, answered, asked, key, flags)

        results = []
        with mock.patch.object(rotation, "_rotate_marked", side_effect=slow_rotate), \
                mock.patch.object(rotation, "_owner_subject", return_value=s):
            threads = [threading.Thread(target=lambda: results.append(
                rotation.check_task("room-1", "claude"))) for _ in range(2)]
            threads[0].start()
            entered.wait(2)
            threads[1].start()
            for t in threads:
                t.join(5)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])
        self.assertEqual(len(results), 2)
        self.assertEqual(self.sess.typed, [])


    def test_the_timeout_and_immediate_paths_ignore_the_hook(self):
        s = self.asked(ago=rotation.ASK_TIMEOUT_S + 10)
        self.write(s, -30)                          # not refreshed
        self.hook_idle = True
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))
        out = rotation._check(self.subject(), True, True)
        self.assertEqual(self.rotated, [])
        self.assertIn("waiting for the owner (claude) to be idle", out["result"])


_REAL_HOOK_IDLE = rotation._hook_idle
DONE = "● Wrote TASK-HANDOVER.md\n"
WORKING = "● Running the suite\n\n✻ Pondering… (212s · esc to interrupt)\n"


class HookIdleTests(_Base):
    """What _hook_idle believes, through attention's real reading of the hook
    and the screen; a fresh handover must wait unless it says idle, at the
    first check and at the gate."""

    def setUp(self):
        super().setUp()
        self.evs = []
        self.invalidated = []
        for p in (
            mock.patch.object(rotation, "_hook_idle", side_effect=_REAL_HOOK_IDLE),
            mock.patch.object(dashboard.attention, "_evidence", side_effect=self.evidence),
            mock.patch.object(dashboard.attention, "_claude_status_by_session", return_value={}),
            mock.patch.object(dashboard.agent_hooks, "invalidate",
                              side_effect=lambda *a, **k: self.invalidated.append(a)),
        ):
            p.start()
            self.patches.append(p)

    def evidence(self, part, statuses):
        return self.evs.pop(0) if len(self.evs) > 1 else self.evs[0]

    def ev(self, tail, hook_state=None, hook_at=0.0, quiet=120.0):
        now = time.time()
        hook = ({"state": hook_state, "at": hook_at, "event": "Stop", "detail": "",
                 "sessionId": "sid-1", "waits": 0} if hook_state else None)
        return {"ptyId": "pty-1", "alive": True, "tail": tail, "idleSeconds": quiet,
                "lastSubmit": 0.0, "death": None, "scan": dashboard.attention.analyse(tail),
                "claudeStatus": "", "claudeStatusAt": 0.0, "hook": hook,
                "lastOutput": now - quiet}

    def fresh(self):
        s = self.asked()
        hp = s["handover"]
        hp.write_text("# Handover\n", encoding="utf-8")
        at = s["state"]["askedAt"] + 30
        os.utime(hp, (at, at))
        return s

    def test_an_idle_hook_after_the_ask_rotates(self):
        s = self.fresh()
        self.evs = [self.ev(DONE, "idle", s["state"]["askedAt"] + 40)]
        self.check(s)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])

    def test_a_busy_screen_quiet_past_a_minute_is_not_idle(self):
        # A long silent tool call: attention's turn_state calls it idle.
        s = self.fresh()
        self.evs = [self.ev(WORKING, quiet=120.0)]
        self.assertEqual(dashboard.attention.turn_state({"ptyId": "pty-1", "agent": "codex"})[0],
                         "idle")
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_a_stale_idle_hook_is_not_idle(self):
        s = self.fresh()
        for tail in (DONE, WORKING):
            self.evs = [self.ev(tail, "idle", s["state"]["askedAt"] - 5)]
            out = self.check(s)
            self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_an_idle_hook_under_a_busy_screen_is_not_idle(self):
        # The working hook after it was lost; the screen shows the turn.
        s = self.fresh()
        self.evs = [self.ev(WORKING, "idle", s["state"]["askedAt"] + 40)]
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_just_printed_is_not_idle(self):
        s = self.fresh()
        self.evs = [self.ev(DONE, "idle", s["state"]["askedAt"] + 40, quiet=1.0)]
        out = self.check(s)
        self.assertEqual((out["phase"], self.rotated), ("asked", []))

    def test_busy_again_at_the_gate_waits(self):
        s = self.fresh()
        at = s["state"]["askedAt"] + 40
        self.evs = [self.ev(DONE, "idle", at), self.ev(WORKING, "idle", at, quiet=2.0)]
        out = self.check(s)
        self.assertEqual(self.rotated, [])
        self.assertEqual(out["phase"], "asked")
        self.assertIn("rotating once it is idle", out["result"])


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
                                                       "commandMode": "prompt",
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

    def test_a_background_task_notification_is_not_the_ask(self):
        self.add({"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "task-notification",
            "prompt": "<task-notification>tests done: grep found [handover] 3 times"}},
            self.assistant("end_turn"))
        self.assertFalse(self.read()["promptSince"])

    def test_another_line_read_after_the_ask_is_not_the_ask(self):
        # The ask's Enter did not submit, say: a doorbell read later is not it.
        self.add({"type": "attachment", "attachment": {
            "type": "queued_command", "commandMode": "prompt", "prompt": "[relay] New message"}},
            {"type": "user", "message": {"role": "user", "content": "[report] completed"}},
            self.assistant("end_turn"))
        self.assertFalse(self.read()["promptSince"])

    def test_a_tool_result_quoting_the_ask_is_not_the_ask(self):
        self.add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "[handover] Your conversation"}]}})
        self.assertFalse(self.read()["promptSince"])

    def test_the_ask_typed_with_a_doorbell_in_one_input_is_the_ask(self):
        self.add({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "[relay] New message\n[handover] Your conversation"}]}})
        self.assertTrue(self.read()["promptSince"])

    def test_a_line_read_before_the_ask_does_not_count(self):
        self.since = 0
        self.add({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "x"}})
        self.since = self.path.stat().st_size
        self.add(self.tool_result())
        self.assertFalse(self.read()["promptSince"])


class PtyInputTests(_Base):
    """/api/pty/input's stamp, the way the PO's po-tools/tell.py sends a note:
    the text, then a lone Enter, from a process under the PO's agent."""

    def handler(self, agent_pid):
        test = self

        class H(dashboard.Handler):
            def __init__(self):
                pass

            def _agent_sender(self):
                test.lookups += 1
                return agent_pid

        return H()

    def setUp(self):
        super().setUp()
        self.lookups = 0
        self.sess.meta = {"room": "room-1", "identity": "claude"}
        self.states = mock.patch.dict(rotation._TASK_STATE, {})
        self.states.start()

    def tearDown(self):
        self.states.stop()
        super().tearDown()

    def type_like_tell(self, h):
        for data in ("[from the PO] Review 2: fix the finding", "\r"):
            if h._pty_input_by_person(self.sess):
                self.sess.last_input = time.time()

    def test_a_po_note_does_not_drop_an_asked_rotation(self):
        s = self.asked()
        rotation._TASK_STATE["room-1/claude"] = s["state"]
        s["state"].update(askRoom="room-1", askIdentity="claude")
        self.assertTrue(rotation.awaiting_handover("room-1", "claude"))
        self.type_like_tell(self.handler(agent_pid=4242))
        self.assertEqual(self.sess.last_input, 0.0)
        self.idle, self.tr["promptSince"] = True, True
        self.sess._last_submit = time.time() - 30
        self.check(s)
        self.assertEqual(len(self.rotated), 1)

    def test_the_page_typing_drops_it(self):
        s = self.asked()
        rotation._TASK_STATE["room-1/claude"] = s["state"]
        s["state"].update(askRoom="room-1", askIdentity="claude")
        self.type_like_tell(self.handler(agent_pid=None))
        self.assertGreater(self.sess.last_input, 0.0)
        self.idle, self.tr["promptSince"] = True, True
        self.check(s)
        self.assertEqual((self.rotated, s["state"]["phase"]), ([], "watching"))

    def test_the_sender_is_looked_up_only_while_a_handover_is_awaited(self):
        self.type_like_tell(self.handler(agent_pid=4242))
        self.assertEqual(self.lookups, 0)
        self.assertGreater(self.sess.last_input, 0.0)

    def test_the_ask_records_whom_it_awaits(self):
        s = self.subject()
        rotation._TASK_STATE["room-1/claude"] = s["state"]
        self.assertFalse(rotation.awaiting_handover("room-1", "claude"))
        self.check(s)
        self.assertTrue(rotation.awaiting_handover("room-1", "claude"))
        self.assertFalse(rotation.awaiting_handover("room-1", "codex"))

    def test_an_unknown_sender_is_not_an_agent(self):
        import peer_process
        with mock.patch.object(peer_process, "owner", side_effect=peer_process.Unknown("no table")):
            self.assertIsNone(peer_process.agent_sender(("127.0.0.1", 1), ("127.0.0.1", 2), {1}))
        with mock.patch.object(peer_process, "owner", return_value=None):
            self.assertIsNone(peer_process.agent_sender(("127.0.0.1", 1), ("127.0.0.1", 2), {1}))


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
