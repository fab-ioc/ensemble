"""A message sent to an agent is never dropped (#192).

What was seen, and is checked here from fixtures shaped like the real records
(anonymised):

* P438 (2026-10-08): the CEO's message with an image was read by a busy PO
  mid-turn. Claude Code logged it as a ``queued_command`` whose prompt is a
  list of content blocks, which the reader skipped: the send stayed
  "delivered", and the handover that followed failed it ("the session
  stopped before it read this") though the PO had answered it;
* a send the agent never took in is typed in again once, when its agent is
  idle (or Enter is pressed, when it still sits in the input box), and failed
  with Retry the next time, the PO told;
* messages sent while a session is handed over are held for the fresh one,
  and unread ones are carried to it;
* a long one-line text reached Claude cut: it goes as a paste; a Codex paste
  left in its box gets one more Enter.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import rotation
import sends
from tests import test_send_receipts as base

SID = base.SID
iso = base.iso


class Pty(base.FakePty):
    """A terminal the hub writes to: what was written, its screen, how long
    it has been quiet."""

    def __init__(self, pty_id, alive=True, agent="codex"):
        super().__init__(pty_id, alive)
        self.writes: list[str] = []
        self.screen = "> "
        self.idle = 60.0
        self.meta = {"agent": agent}

    def write(self, data):
        if not self._alive:
            return False
        self.writes.append(data)
        return 0

    def tail(self, *a, **k):
        return self.screen

    def info(self):
        return {"idleSeconds": self.idle}


class Harness(unittest.TestCase):
    setUp = base.Receipts.setUp
    tearDown = base.Receipts.tearDown
    spawn = base.Receipts.spawn
    stat = base.Receipts.stat
    join = base.Receipts.join
    post = base.Receipts.post
    payload = base.Receipts.payload
    typed = base.Receipts.typed

    def room(self, **kw):
        rid = base.Receipts.room(self, **kw)
        for pid in list(self.ptys):
            old = self.ptys[pid]
            self.ptys[pid] = Pty(pid, old._alive)
        self.extra = [
            mock.patch.object(sends, "STARTED_AT", 0),
            mock.patch.object(rotation, "_transcript_of", lambda part: (None, lambda p: {"turnOver": self.turn_over})),
            mock.patch.object(dashboard.attention, "looks_busy", lambda tail: False),
            mock.patch.object(dashboard, "_type_input", self.type_input),
            mock.patch.object(dashboard, "operator_name", lambda: "Fab"),
        ]
        for p in self.extra:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.extra])
        self.turn_over = True
        return rid

    def type_input(self, sess, text, provenance=None, *, record=True, parts=None):
        """What the hub types, as one input (no pauses)."""
        if not sess.alive():
            return False
        sess.typed.append(text)
        self.parts = parts
        return True

    def delivered(self, rid, key, text, at):
        sends.accept(rid, key, text, now=at)
        sends.mark(rid, [key], "delivered", now=at)


class QueuedCommandWithAnImage(unittest.TestCase):
    """P438: the line read mid-turn is logged as content blocks."""

    def test_the_reader_takes_a_list_prompt_and_the_send_matches(self):
        sent = "running strategies only on the live account?\n\n[point P438]\n\n[image] C:\\att\\shot.png"
        rows = [
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-10-08T15:18:16.000Z"},
            {"type": "attachment", "timestamp": "2026-10-08T15:18:20.000Z", "attachment": {
                "type": "queued_command", "commandMode": "prompt",
                "prompt": [{"type": "text", "text": "[Image #4]running strategies only on the live account?"
                                                    "\n\n[point P438]\n\n[image]"},
                           {"type": "image", "source": {"type": "base64", "data": "AAAA"}}]}},
            {"type": "assistant", "timestamp": "2026-10-08T15:18:22.000Z", "message": {
                "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Re P438: yes, only the live account."}]}},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            turns = dashboard._claude_text_turns(p)
        users = [t for t in turns if t["role"] == "user"]
        self.assertEqual(len(users), 1)
        self.assertIn("[point P438]", users[0]["text"])
        self.assertTrue(sends.matches(sent, users[0]["text"]))

    def test_its_turn_is_counted_apart_so_later_ids_hold(self):
        """Points and sends recorded before #192 name the later queued turns
        ``:q<n>``: a turn read from blocks must not shift them."""
        raw = [{"role": "user", "text": "a", "queued": "blocks"},
               {"role": "user", "text": "b", "queued": True},
               {"role": "assistant", "text": "c"}]
        self.assertEqual([m for m, _ in dashboard.page_turn_ids("s", raw)], ["s:qb0", "s:q0", "s:0"])

    def test_a_string_prompt_still_reads(self):
        self.assertEqual(dashboard._queued_prompt_text("hello"), "hello")
        self.assertIsNone(dashboard._queued_prompt_text([{"type": "image"}]))


class GoneOnlyWhenGone(Harness):
    def test_not_failed_while_rotating_or_just_after_a_hub_start(self):
        rid = self.room(running=False)
        at = time.time() - 600
        self.delivered(rid, "send:r", "read me after the handover", at)
        rotation._ROTATING[(rid, "codex")] = {"stopped": False}
        try:
            sends.sync(rid, force=True)
            self.assertEqual(sends.get(rid, "send:r")["state"], "delivered")
        finally:
            rotation._ROTATING.pop((rid, "codex"), None)
        with mock.patch.object(sends, "STARTED_AT", time.time() - 10):
            sends.sync(rid, force=True)
        self.assertEqual(sends.get(rid, "send:r")["state"], "delivered")
        sends.sync(rid, force=True)
        self.assertEqual(sends.get(rid, "send:r")["state"], "failed")

    def test_read_by_the_session_before_a_rotation_confirms(self):
        rid = self.room(running=False)
        at = time.time() - 300
        self.delivered(rid, "send:x", "the message the old session read\n\n[point P1]", at)
        full = chatroom.get_room(rid, public=False)
        part = next(p for p in full["participants"] if p.get("kind") == "agent")
        old = "019a0000-0000-7000-8000-0000000000aa"
        part["rotations"] = [{"fromSessionId": old, "toSessionId": SID}]
        chatroom.update_room(full)
        self.turns[old] = [{"role": "user", "timestamp": iso(at + 5), "queued": True,
                            "text": "the message the old session read\n\n[point P1]"}]
        sends.sync(rid, force=True)
        self.assertEqual(sends.get(rid, "send:x")["state"], "confirmed")


class TypedAgainOnce(Harness):
    def setUp(self):
        super().setUp()
        self.rid = self.room()
        self.pty = self.ptys["pty-live-0"]
        self.at = time.time() - sends.REDELIVER_AFTER_S - 5
        self.delivered(self.rid, "send:u", "please check the branch\n\n[point P7]", self.at)

    def test_once_then_failed_and_shown_as_waiting_for_the_person(self):
        sends.redeliver_tick(now=time.time())
        [typed] = self.pty.typed
        self.assertTrue(typed.startswith(sends.AGAIN_NOTE))
        self.assertIn("please check the branch", typed)
        self.assertEqual(self.parts[0][1]["kind"], "again")
        self.assertEqual(self.parts[1][1]["kind"], "human")
        s = sends.get(self.rid, "send:u")
        self.assertEqual((s["state"], s["redelivered"]), ("delivered", 1))
        # Its clock started again: not twice in a row.
        sends.redeliver_tick(now=time.time())
        self.assertEqual(len(self.pty.typed), 1)
        # Still not read at the next idle: failed, with Retry.
        sends.redeliver_tick(now=time.time() + sends.REDELIVER_AFTER_S + 5)
        self.assertEqual(len(self.pty.typed), 1)
        s = sends.get(self.rid, "send:u")
        self.assertEqual((s["state"], s["error"]), ("failed", sends.NOT_TAKEN))
        self.assertEqual(sends.not_taken_by_room()[self.rid]["count"], 1)
        self.assertEqual(sends.view(self.rid)[0]["redeliveredAt"], s["redeliveries"][-1]["at"])
        # Retry is a fresh start.
        sends.accept(self.rid, "send:u", s["text"])
        self.assertNotIn("redelivered", sends.get(self.rid, "send:u"))

    def test_enter_only_when_it_still_sits_in_the_box(self):
        self.pty.screen = "› [Pasted Content 1204 chars]\n\n  ? for shortcuts"
        sends.redeliver_tick(now=time.time())
        self.assertEqual((self.pty.typed, self.pty.writes), ([], ["\r"]))
        self.assertEqual(sends.get(self.rid, "send:u")["redelivered"], 1)

    def test_not_while_busy_or_on_a_prompt_or_asked_for_its_handover(self):
        self.turn_over = False
        sends.redeliver_tick(now=time.time())
        self.turn_over, self.pty.idle = True, 3
        sends.redeliver_tick(now=time.time())
        self.pty.idle = 60
        with mock.patch.object(dashboard.attention, "looks_like_prompt", lambda tail: True):
            sends.redeliver_tick(now=time.time())
        with mock.patch.object(rotation, "awaiting_handover", lambda rid, ident: True):
            sends.redeliver_tick(now=time.time())
        self.assertEqual((self.pty.typed, self.pty.writes), ([], []))

    def test_never_when_it_was_read(self):
        self.turns[SID].append({"role": "user", "timestamp": iso(self.at + 30),
                                "text": "please check the branch\n\n[point P7]"})
        sends.redeliver_tick(now=time.time())
        self.assertEqual(self.pty.typed, [])
        self.assertEqual(sends.get(self.rid, "send:u")["state"], "confirmed")

    def test_a_tasks_po_is_told(self):
        posted = []
        self.extra.append(mock.patch.object(dashboard, "room_project", lambda rid: "proj"))
        self.extra.append(mock.patch.object(dashboard, "load_projects",
                                            lambda: [{"id": "proj", "poRoomId": "room-0000beef"}]))
        self.extra.append(mock.patch.object(chatroom, "post_notice",
                                            lambda *a: posted.append(a)))
        for p in self.extra[-3:]:
            p.start()
        sends.redeliver_tick(now=time.time())
        sends.redeliver_tick(now=time.time() + sends.REDELIVER_AFTER_S + 5)
        [(po_rid, _sender, text, meta)] = posted
        self.assertEqual(po_rid, "room-0000beef")
        self.assertIn("please check the branch", text)
        self.assertEqual(meta["taskRoomId"], self.rid)


class HandedOver(Harness):
    def setUp(self):
        super().setUp()
        self.rid = self.room()
        self.key = (self.rid, "codex")
        rotation._ROTATING[self.key] = {"stopped": False}
        self.addCleanup(lambda: (rotation._ROTATING.pop(self.key, None), rotation._HELD.pop(self.key, None)))

    def test_a_send_while_rotating_is_held_not_refused(self):
        status, out = self.post({"roomId": self.rid, "text": "sent during the handover", "key": "send:h"})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["send"]["state"], "queued")
        self.assertTrue(rotation.holds_send(self.rid, "send:h"))
        later = time.time() + dashboard.ORPHAN_AFTER_S + 5
        with mock.patch.object(sends.time, "time", lambda: later):
            dashboard._fail_orphaned_sends(self.rid)
        self.assertEqual(sends.get(self.rid, "send:h")["state"], "queued")
        self.assertEqual(self.pty_typed(), [])
        # The fresh session gets it, as the person's.
        rotation._release(self.key)
        self.wait_replay()
        self.assertEqual(self.pty_typed(), ["sent during the handover\n\n[point P1]"])
        self.assertEqual(sends.get(self.rid, "send:h")["state"], "delivered")
        self.assertEqual(self.parts[0][1]["kind"], "human")

    def test_unread_ones_are_carried_first_and_the_brief_says_so(self):
        self.delivered(self.rid, "send:c", "never read by the old session", time.time() - 60)
        room = chatroom.get_room(self.rid)
        self.assertIn("never read", rotation._carried_line(room))
        self.assertIn(rotation._carried_line(room),
                      rotation.task_first_prompt(room, "old", 1000, Path("H.md"), True))
        rotation._carry_unread(self.key)
        rotation._HELD[self.key].append("[due] something due")
        rotation._release(self.key)
        self.wait_replay()
        self.assertEqual(self.pty_typed(), ["never read by the old session", "[due] something due"])
        s = sends.get(self.rid, "send:c")
        self.assertEqual((s["state"], s["redeliveries"][0]["how"]), ("delivered", "carried to the fresh session"))

    def test_no_session_after_the_handover_fails_them_with_retry(self):
        status, out = self.post({"roomId": self.rid, "text": "into the void", "key": "send:v"})
        self.ptys["pty-live-0"]._alive = False
        rotation._release(self.key)
        self.wait_replay()
        s = sends.get(self.rid, "send:v")
        self.assertEqual(s["state"], "failed")

    def pty_typed(self):
        return self.ptys["pty-live-0"].typed

    def wait_replay(self):
        for t in threading.enumerate():
            if t.name.startswith("rotation-replay-"):
                t.join(10)


class Typing(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(dashboard, "_record_typed_input_safely", lambda *a, **k: None)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_a_long_line_goes_to_claude_as_a_paste(self):
        long = "te " * 200
        claude = Pty("p1", agent="claude")
        with mock.patch.object(dashboard.time, "sleep", lambda s: None):
            self.assertTrue(dashboard._type_input(claude, long))
            self.assertTrue(dashboard._type_input(claude, "short"))
        self.assertEqual(claude.writes, ["\x1b[200~" + long + "\x1b[201~", "\r", "short", "\r"])
        codex = Pty("p2", agent="codex")
        with mock.patch.object(dashboard.time, "sleep", lambda s: None):
            dashboard._type_input(codex, long)
        self.assertEqual(codex.writes, [long, "\r"])

    def test_a_codex_paste_left_in_the_box_gets_one_more_enter(self):
        codex = Pty("p2", agent="codex")
        codex.screen = "› [Pasted Content 1330 chars]"
        with mock.patch.object(dashboard.time, "sleep", lambda s: None):
            dashboard._type_input(codex, "two\nlines")
        self.assertEqual(codex.writes, ["\x1b[200~two\nlines\x1b[201~", "\r", "\r"])
        codex.writes, codex.screen = [], "› "
        with mock.patch.object(dashboard.time, "sleep", lambda s: None):
            dashboard._type_input(codex, "two\nlines")
        self.assertEqual(codex.writes, ["\x1b[200~two\nlines\x1b[201~", "\r"])

    def test_typed_again_is_a_hub_kind(self):
        self.assertEqual(dashboard.hub_input_kind(sends.AGAIN_NOTE)["kind"], "again")


if __name__ == "__main__":
    unittest.main()
