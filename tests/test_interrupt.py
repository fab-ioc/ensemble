"""A chat interrupt targets only a live working agent and is a person action."""
import copy
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import chatroom
import dashboard
from backends import ptyrun


ROOM = {"id": "room-test", "participants": [
    {"kind": "agent", "identity": "codex", "agent": "codex", "role": "engineer", "ptyId": "pty-owner"},
    {"kind": "agent", "identity": "claude", "agent": "claude", "role": "reviewer", "ptyId": "pty-review"},
    {"kind": "human", "identity": "user"},
]}


class Terminal:
    def __init__(self, identity):
        self.meta = {"room": "room-test", "identity": identity}
        self.writes = []

    def alive(self):
        return True

    def write(self, data):
        self.writes.append(data)
        return True


class InterruptTests(unittest.TestCase):
    def setUp(self):
        dashboard._LAST_INTERRUPT.clear()
        self.room = copy.deepcopy(ROOM)
        self.owner, self.reviewer = Terminal("codex"), Terminal("claude")
        self.events = []
        self.patches = [
            mock.patch.object(chatroom, "get_room", return_value=self.room),
            mock.patch.object(dashboard, "_room_is_live", return_value=True),
            mock.patch.object(dashboard.attention, "turn_state", return_value=("working", "hook")),
            mock.patch.object(ptyrun, "get", side_effect=lambda pid: {
                "pty-owner": self.owner, "pty-review": self.reviewer}.get(pid)),
            mock.patch.object(chatroom, "record_interrupt", side_effect=lambda rid, ident, pty: self._event(rid, ident, pty)),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _event(self, rid, ident, pty_id):
        event = {"from": "user", "identity": ident, "ptyId": pty_id, "ts": 1}
        self.events.append(event)
        self.room.setdefault("interrupts", []).append(event)
        return event

    def test_running_reviewer_is_default_and_second_press_is_deduplicated(self):
        code, response = dashboard.interrupt_agent("room-test")
        self.assertEqual((code, response["identity"]), (200, "claude"))
        self.assertEqual(self.reviewer.writes, ["\x03"])
        self.assertEqual(self.owner.writes, [])
        self.assertEqual(self.events, [{"from": "user", "identity": "claude", "ptyId": "pty-review", "ts": 1}])
        code, response = dashboard.interrupt_agent("room-test")
        self.assertTrue(response["duplicate"])
        self.assertEqual(self.reviewer.writes, ["\x03"])

    def test_claude_can_be_stopped_again_after_a_second(self):
        self.assertEqual(dashboard.interrupt_agent("room-test", "claude")[0], 200)
        dashboard._LAST_INTERRUPT[("room-test", "claude")] -= 2
        code, result = dashboard.interrupt_agent("room-test", "claude")
        self.assertEqual(code, 200)
        self.assertFalse(result.get("duplicate", False))
        self.assertEqual(self.reviewer.writes, ["\x03", "\x03"])

    def test_second_stop_waits_for_a_new_incoming_message(self):
        self.assertEqual(dashboard.interrupt_agent("room-test", "codex")[0], 200)
        dashboard._LAST_INTERRUPT[("room-test", "codex")] -= 2
        code, result = dashboard.interrupt_agent("room-test", "codex")
        self.assertEqual(code, 200)
        self.assertTrue(result["duplicate"])
        self.assertEqual(self.owner.writes, ["\x1b"])
        self.room["messages"] = [{"from": "user", "ts": 2}]
        code, result = dashboard.interrupt_agent("room-test", "codex")
        self.assertEqual(code, 200)
        self.assertFalse(result.get("duplicate", False))
        self.assertEqual(self.owner.writes, ["\x1b", "\x1b"])

    def test_explicit_owner_and_stopped_room(self):
        self.assertEqual(dashboard.interrupt_agent("room-test", "codex")[0], 200)
        self.assertEqual(self.owner.writes, ["\x1b"])
        with mock.patch.object(dashboard, "_room_is_live", return_value=False):
            code, response = dashboard.interrupt_agent("room-test")
        self.assertEqual((code, response["error"]), (409, "room_stopped"))

    def test_idle_and_wrong_identity_refused(self):
        with mock.patch.object(dashboard.attention, "turn_state", return_value=("idle", "status")):
            self.assertEqual(dashboard.interrupt_agent("room-test")[0], 409)
        self.assertEqual(dashboard.interrupt_agent("room-test", "other")[0], 409)
        self.assertEqual(self.events, [])

    def test_agent_bearer_cannot_interrupt_other_project_room(self):
        body = json.dumps({"roomId": "room-test"}).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/room/interrupt", "POST", "HTTP/1.1"
        h.requestline = "POST /api/room/interrupt HTTP/1.1"
        h.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json",
                     "Host": "127.0.0.1", "Authorization": "Bearer another-project-agent"}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *args: None
        h._page_refusal = lambda: "Only the dashboard page can do this, not a task's agent."
        h.do_POST()
        self.assertIn(b" 403 ", h.wfile.getvalue().split(b"\r\n", 1)[0])
        self.assertEqual(self.events, [])

    def test_log_does_not_become_an_agent_message_or_close_a_card(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(chatroom, "ROOMS_DIR", Path(tmp)):
            rid = chatroom.create_room("Interrupt", [{"identity": "codex", "agent": "codex"}])["id"]
            chatroom.patch_room(rid, status="waiting_human", openAsk={"id": "ask-1"})
            before = chatroom._read(rid)
            self.patches[-1].stop()  # use the real persistent recorder
            try:
                logged = chatroom.record_interrupt(rid, "codex")
            finally:
                self.patches[-1].start()
            after = chatroom._read(rid)
            self.assertEqual(logged["from"], "user")
            self.assertEqual(after["interrupts"][-1], logged)
            self.assertEqual(after["messages"], before["messages"])
            self.assertEqual(after["status"], "waiting_human")
            self.assertEqual(after["openAsk"], {"id": "ask-1"})


if __name__ == "__main__":
    unittest.main()
