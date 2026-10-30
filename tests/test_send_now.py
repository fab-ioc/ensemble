"""Send now (#252, GitHub issue 23): a message reaches a busy agent at once.

Measured in a real ConPTY (Documents/#252): a line typed into a busy agent
already goes into its running turn at the next step. Send now does not wait
for that step:

* Claude: the line, Enter, then Claude's own send-now chord Ctrl+X Ctrl+S
  (nothing is cancelled; the send says "into-turn");
* Codex: Esc first (its only immediate way, "interrupted"), and only while
  its rollout shows the turn still open — an Esc at an idle prompt can quit
  it — and never a second Esc right after Stop sent one;
* an idle agent, or a plain Send, gets the plain line.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import sends

RID = "room-0000abcd"


class FakeSess:
    def __init__(self, agent="claude"):
        self.meta = {"agent": agent, "room": RID, "identity": agent}
        self.id = "pty-" + agent
        self.keys: list[str] = []
        self.last_input = 0.0

    def write(self, data):
        self.keys.append(data)
        return 0        # pywinpty: 0 even when delivered

    def alive(self):
        return True

    def tail(self, *a, **k):
        return ""


def part(agent="claude", ident=None):
    return {"identity": ident or agent, "agent": agent, "kind": "agent", "ptyId": "pty-" + agent}


class TypeInput(unittest.TestCase):
    def setUp(self):
        dashboard._LAST_INTERRUPT.clear()
        self.turn = "working"
        self.turn_over = False
        self.rollout = Path("x.jsonl")
        self.interrupts: list[dict] = []
        patches = [
            mock.patch.object(dashboard, "checkpoint_wait_restore", lambda *a, **k: None),
            mock.patch.object(dashboard.rotation, "reserve_hub_line", lambda sess, why: object()),
            mock.patch.object(dashboard.rotation, "release_hub_line", lambda sess, lease: None),
            mock.patch.object(dashboard.hub_typing, "record", lambda *a, **k: None),
            mock.patch.object(dashboard, "_record_typed_input_safely", lambda *a, **k: None),
            mock.patch.object(dashboard, "_codex_rollout_start", lambda sess: (self.rollout, 0)),
            mock.patch.object(dashboard.chatroom, "record_interrupt", self.record_interrupt),
            mock.patch.object(dashboard, "_verify_codex_submit", lambda *a, **k: None),
            mock.patch.object(dashboard.time, "sleep", lambda s: None),
            mock.patch.object(dashboard.attention, "turn_state", lambda p: (self.turn, "")),
            mock.patch.object(dashboard.rotation, "_transcript_of", lambda p: (self.rollout, None)),
            mock.patch.object(dashboard.rotation, "read_rollout",
                              lambda path, **k: {"turnOver": self.turn_over}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def record_interrupt(self, rid, ident, pty_id=""):
        event = {"from": "user", "identity": ident, "ptyId": pty_id, "ts": 10.0}
        self.interrupts.append(event)
        return event

    def send(self, agent, text="fix the title"):
        sess = FakeSess(agent)
        way = dashboard.send_now_way(part(agent))
        ok = (dashboard._type_input(sess, text, now=way) if way
              else dashboard._type_input(sess, text))
        self.assertTrue(ok)
        return sess.keys, way

    def test_claude_busy_gets_the_line_then_its_send_now_chord(self):
        keys, way = self.send("claude")
        self.assertEqual(way, dashboard.SEND_NOW_INTO_TURN)
        self.assertEqual(keys, ["fix the title", "\r", "\x18", "\x13"])
        self.assertEqual(dashboard.send_now_how([way]), "into-turn")

    def test_codex_busy_is_interrupted_first(self):
        keys, way = self.send("codex")
        self.assertEqual(way, dashboard.SEND_NOW_INTERRUPT)
        self.assertEqual(keys, ["\x1b", "fix the title", "\r"])
        self.assertEqual(dashboard.send_now_how([way]), "interrupted")

    def test_codex_whose_turn_is_over_gets_no_escape(self):
        self.turn_over = True       # the screen reads busy, the rollout says done
        keys, way = self.send("codex")
        self.assertEqual(way, "")
        self.assertEqual(keys, ["fix the title", "\r"])

    def test_codex_turn_ending_while_the_line_waits_gets_no_escape(self):
        # send_now_way read an open turn; it ended before the line's slot.
        sess = FakeSess("codex")
        way = dashboard.send_now_way(part("codex"))
        self.assertEqual(way, dashboard.SEND_NOW_INTERRUPT)
        self.turn_over = True
        self.assertTrue(dashboard._type_input(sess, "fix the title", now=way))
        self.assertEqual(sess.keys, ["fix the title", "\r"])
        self.assertNotIn((RID, "codex"), dashboard._LAST_INTERRUPT)
        self.assertEqual(self.interrupts, [])

    def test_codex_without_a_rollout_gets_no_escape(self):
        # No rollout to read: its turn cannot be shown open, so a plain send.
        self.rollout = None
        keys, way = self.send("codex")
        self.assertEqual(way, "")
        self.assertEqual(keys, ["fix the title", "\r"])
        # Even when asked to interrupt (the rollout gone since send_now_way).
        sess = FakeSess("codex")
        self.assertTrue(dashboard._type_input(sess, "again", now=dashboard.SEND_NOW_INTERRUPT))
        self.assertEqual(sess.keys, ["again", "\r"])
        self.assertEqual(self.interrupts, [])

    def test_send_now_escape_is_recorded_so_a_later_stop_sends_no_second(self):
        keys, way = self.send("codex")
        self.assertEqual(keys[0], "\x1b")
        self.assertEqual(self.interrupts, [{"from": "user", "identity": "codex",
                                            "ptyId": "pty-codex", "ts": 10.0}])
        # Stop two seconds later, Codex's screen still busy, no newer message
        # (the Send now text was posted before its Esc): no second Esc.
        dashboard._LAST_INTERRUPT[(RID, "codex")] -= 2
        stopped = FakeSess("codex")
        room = {"id": RID, "participants": [part("codex")], "messages": [{"from": "user", "ts": 9.0}],
                "interrupts": self.interrupts}
        with mock.patch.object(dashboard.chatroom, "get_room", return_value=room), \
                mock.patch.object(dashboard, "_room_is_live", return_value=True), \
                mock.patch.object(dashboard.ptyrun, "get", return_value=stopped):
            code, result = dashboard.interrupt_agent(RID, "codex")
        self.assertEqual(code, 200)
        self.assertTrue(result["duplicate"])
        self.assertEqual(stopped.keys, [])

    def test_an_idle_agent_gets_a_plain_send(self):
        self.turn = "idle"
        for agent in ("claude", "codex"):
            keys, way = self.send(agent)
            self.assertEqual(way, "")
            self.assertEqual(keys, ["fix the title", "\r"])

    def test_a_plain_send_to_a_busy_agent_has_no_extra_keys(self):
        sess = FakeSess("claude")
        self.assertTrue(dashboard._type_input(sess, "later"))
        self.assertEqual(sess.keys, ["later", "\r"])

    def test_no_second_escape_right_after_stop(self):
        dashboard._LAST_INTERRUPT[(RID, "codex")] = dashboard.time.monotonic()
        keys, way = self.send("codex")
        self.assertEqual(way, dashboard.SEND_NOW_INTERRUPT)
        self.assertEqual(keys, ["fix the title", "\r"])

    def test_how_of_several_agents(self):
        self.assertEqual(dashboard.send_now_how(["into-turn", "interrupt"]), "interrupted")
        self.assertEqual(dashboard.send_now_how([]), "")


class Receipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)

    def test_the_send_keeps_how_it_went_in(self):
        sends.accept(RID, "k1", "fix the title")
        sends.accept(RID, "k2", "plain")
        sends.note_how(RID, "k1", "into-turn")
        sends.note_how(RID, "k2", "")           # a plain send notes nothing
        rows = {r["key"]: r for r in sends.view(RID)}
        self.assertEqual(rows["k1"]["how"], "into-turn")
        self.assertNotIn("how", rows["k2"])
        self.assertEqual(sends.hows(RID), {})    # no balloon yet
        sends.mark(RID, ["k1"], "confirmed", mid="m-1")
        self.assertEqual(sends.hows(RID), {"m-1": "into-turn"})


class Delivery(unittest.TestCase):
    """The hub paths: a solo agent typed into, a team's recipients rung."""

    def setUp(self):
        self.typed: list[tuple] = []
        self.noted: list[tuple] = []
        patches = [
            mock.patch.object(dashboard, "_type_input", self.fake_type),
            mock.patch.object(dashboard, "send_now_way",
                              lambda p: {"claude": "into-turn", "codex": "interrupt"}[p["agent"]]),
            mock.patch.object(dashboard.sends, "note_how", lambda rid, key, how: self.noted.append((key, how))),
            mock.patch.object(dashboard, "_typed_sends", lambda *a, **k: None),
            mock.patch.object(dashboard, "note_answer", lambda *a: None),
            mock.patch.object(dashboard, "with_message_refs", lambda text, rid, proj: text),
            mock.patch.object(dashboard.rotation, "room_rotating", lambda rid: False),
            mock.patch.object(dashboard.rotation, "hold_wake", lambda *a: False),
            mock.patch.object(dashboard.restart_drain, "holding", lambda: False),
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: FakeSess()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.h = dashboard.Handler.__new__(dashboard.Handler)

    def fake_type(self, sess, text, *a, now="", **k):
        self.typed.append((text, now))
        return True

    def test_solo_send_now_goes_in_at_once_and_says_so(self):
        room = {"id": RID, "mode": "solo", "participants": [part("claude")]}
        items = [{"text": "fix it", "to": "", "key": "k1", "now": True}]
        self.assertEqual(self.h._deliver_now(room, items), [])
        self.assertEqual(self.typed, [("fix it", "into-turn")])
        self.assertEqual(self.noted, [("k1", "into-turn")])

    def test_solo_plain_send_is_unchanged(self):
        room = {"id": RID, "mode": "solo", "participants": [part("codex")]}
        self.h._deliver_now(room, [{"text": "later", "to": "", "key": "k1"}])
        self.assertEqual(self.typed, [("later", "")])
        self.assertEqual(self.noted, [])

    def test_a_team_ring_reaches_each_recipient_its_way(self):
        room = {"id": RID, "participants": [part("claude"), part("codex")]}
        with mock.patch.object(dashboard.chatroom, "get_room", lambda *a, **k: room), \
                mock.patch.object(dashboard.chatroom, "participant",
                                  lambda r, ident: next(p for p in room["participants"]
                                                        if p["identity"] == ident)):
            ways: dict = {}
            rung = self.h._ring(RID, ["claude", "codex"], "wake", ways=ways)
            self.assertEqual(rung, ["claude", "codex"])
            self.assertEqual(ways, {"claude": "into-turn", "codex": "interrupt"})
            self.assertEqual(dashboard.send_now_how(ways.values()), "interrupted")
            self.typed.clear()
            self.h._ring(RID, ["claude"], "wake")       # a plain ring
            self.assertEqual(self.typed, [("wake", "")])

    def test_resume_marks_only_the_new_text_now(self):
        room = {"id": RID, "mode": "solo", "participants": [part("claude")]}
        seen = []
        with mock.patch.object(dashboard, "_room_is_live", lambda r: True), \
                mock.patch.object(dashboard.Handler, "_deliver_now",
                                  lambda self, r, items: seen.extend(items) or []):
            self.h._resume_room(room, text="fix it", key="k1", now=True)
            self.h._resume_room(room, text="later", key="k2")
        self.assertEqual([(it["key"], bool(it.get("now"))) for it in seen],
                         [("k1", True), ("k2", False)])


if __name__ == "__main__":
    unittest.main()
