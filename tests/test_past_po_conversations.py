"""A conversation a task's seat left behind (a rotation, a PO switch, an
owner's handover, an earlier review, an agent taken off the task) is the
task's, not a card of its own on the board: it is hidden from the sessions
list and counted on its task (pastConversations), and GET /api/room/past lists
it. A PO's conversations from before its room recorded them are found by their
folder and first words (old_po_room). A conversation nobody named is titled by
its first words (firstWords)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from test_session_window import Bench  # noqa: E402

PO_BRIEF = "[rotation] You are the product owner (PO) of the project 'Ensemble'. Carry on."


def plain(rows: list[dict]) -> list[str]:
    return sorted(r["sessionId"] for r in rows if not r.get("headless"))


def task_row(rows: list[dict], rid: str) -> dict:
    return next(r for r in rows if r.get("roomId") == rid and r.get("headless"))


class RecordedPast(unittest.TestCase):
    """What the room record says a seat left behind."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.elsewhere = str(self.b.base / "elsewhere")
        self.task_cwd = str(self.b.base / "tasks" / "t1")

    def test_rotated_switched_reviewed_and_retired_sessions_are_the_tasks(self):
        # The seat now runs elsewhere, so its folder hides nothing: only the record does.
        self.b.room("room-t1", "claude", self.task_cwd, session_id="now")
        part = self.b.rooms[0]["participants"][0]
        part["rotations"] = [{"fromSessionId": "rotated", "at": 10},
                             {"fromSessionId": "switched", "at": 20, "fromAgent": "claude"}]
        part["reviews"] = [{"sessionId": "reviewed", "startedAt": 15}]
        self.b.rooms[0]["retiredSessions"] = [{"sessionId": "retired", "identity": "codex",
                                               "agent": "claude", "at": 30}]
        for sid in ("now", "rotated", "switched", "reviewed", "retired", "mine"):
            self.b.claude(sid, self.elsewhere, age_days=1)
        rows = self.b.load(50)
        self.assertEqual(plain(rows), ["mine"])
        self.assertEqual(task_row(rows, "room-t1")["pastConversations"], 4)
        past = dashboard.room_past_sessions(self.b.rooms[0])
        self.assertEqual([(x["sessionId"], x["via"]) for x in past],
                         [("rotated", "rotation"), ("reviewed", "review"),
                          ("switched", "rotation"), ("retired", "retired")])

    def test_the_current_conversation_is_not_a_past_one(self):
        self.b.room("room-t1", "claude", self.task_cwd, session_id="now")
        self.b.rooms[0]["participants"][0]["rotations"] = [{"fromSessionId": "now", "at": 1}]
        self.assertEqual(dashboard.room_past_sessions(self.b.rooms[0]), [])


class SetAgentsKeepsTheDropped(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)

    def test_a_dropped_agents_conversations_are_kept(self):
        room = chatroom.create_room("t", [
            {"identity": "claude", "agent": "claude", "sessionId": "c-now", "cwd": "x"},
            {"identity": "codex", "agent": "codex", "sessionId": "x-now", "cwd": "y"}])
        stored = chatroom.get_room(room["id"], public=False)
        codex = next(p for p in stored["participants"] if p.get("identity") == "codex")
        codex["rotations"] = [{"fromSessionId": "x-old", "at": 1}]
        codex["sessionKinds"] = {"x-old": "claude"}
        chatroom._write(stored)
        chatroom.set_agents(room["id"], [{"identity": "claude", "agent": "claude"}])
        after = chatroom.get_room(room["id"], public=False)
        got = {(x["sessionId"], x["identity"], x["agent"]) for x in after["retiredSessions"]}
        self.assertEqual(got, {("x-now", "codex", "codex"), ("x-old", "codex", "claude")})
        # The staying agent keeps its own conversation; nothing is kept twice.
        chatroom.set_agents(room["id"], [{"identity": "claude", "agent": "claude"}])
        again = chatroom.get_room(room["id"], public=False)
        self.assertEqual(len(again["retiredSessions"]), 2)
        self.assertEqual({x["sessionId"] for x in dashboard.room_past_sessions(again)},
                         {"x-now", "x-old"})


class OldPoConversations(unittest.TestCase):
    """Before the record: the PO's folder and its first words."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.proj = str(self.b.base / "Ensemble")
        self.po_cwd = str(self.b.base / "Ensemble" / "po")
        self.b.room("room-po", "claude", str(Path(self.po_cwd) / "claude-2"), session_id="po-now")
        self.b.rooms[0]["cwd"] = self.po_cwd
        self.b.rooms[0]["participants"][0]["identity"] = "claude-2"
        self.b.projects = [{"id": "ens", "name": "Ensemble", "path": self.proj, "poRoomId": "room-po"}]

    def test_old_briefs_and_seat_folders_are_the_pos_the_persons_own_stay(self):
        self.b.claude("old-brief", self.proj, age_days=5, text=PO_BRIEF)
        self.b.claude("old-brief-po-folder", self.po_cwd, age_days=5, text="[product owner] hi")
        self.b.claude("old-seat", str(Path(self.po_cwd) / "claude"), age_days=6, text="anything")
        self.b.codex("old-codex-seat", str(Path(self.po_cwd) / "codex"), age_days=6)
        self.b.claude("persons-own", self.proj, age_days=4, text="Fix the header please")
        self.b.claude("elsewhere-brief", str(self.b.base / "other"), age_days=4, text=PO_BRIEF)
        rows = self.b.load(50)
        self.assertEqual(plain(rows), ["elsewhere-brief", "persons-own"])
        self.assertEqual(task_row(rows, "room-po")["pastConversations"], 4)
        self.assertEqual(sorted(x["sessionId"] for x in dashboard._PAST_FOUND["room-po"]),
                         ["old-brief", "old-brief-po-folder", "old-codex-seat", "old-seat"])
        own = next(r for r in rows if r["sessionId"] == "persons-own")
        self.assertEqual(own["firstWords"], "Fix the header please 0")

    def test_the_list_of_past_conversations(self):
        self.b.rooms[0]["participants"][0]["rotations"] = [{"fromSessionId": "rotated", "at": 50}]
        self.b.claude("rotated", str(self.b.base / "gone"), age_days=1, text=PO_BRIEF)
        self.b.claude("old-brief", self.proj, age_days=5, text=PO_BRIEF)
        self.b.load(50)
        with mock.patch.object(dashboard.chatroom, "get_room", return_value=self.b.rooms[0]), \
                mock.patch.object(dashboard, "load_sessions", return_value=[]), \
                mock.patch.object(dashboard, "load_labels", return_value={"old-brief": "Named"}), \
                mock.patch.object(dashboard, "PROJ_DIR", self.b.transcripts), \
                mock.patch.object(dashboard, "compute_session_cost", return_value={"dollars": 1.5}):
            got = dashboard.past_conversations("room-po")
        rows = got["sessions"]
        self.assertEqual([(r["sessionId"], r["via"]) for r in rows],
                         [("rotated", "rotation"), ("old-brief", "before")])
        self.assertTrue(all(r["pastOf"] == "room-po" for r in rows))
        self.assertEqual(rows[0]["firstWords"], "Past PO conversation")
        self.assertEqual(rows[1]["label"], "Named")
        self.assertEqual(rows[0]["cost"], 1.5)
        with mock.patch.object(dashboard.chatroom, "get_room", return_value=None):
            self.assertIsNone(dashboard.past_conversations("room-nope"))


class FirstWords(unittest.TestCase):
    def test_cases(self):
        fw = dashboard.first_words
        self.assertEqual(fw(""), "")
        self.assertEqual(fw("  Fix   the\theader \n and more"), "Fix the header")
        self.assertEqual(fw(PO_BRIEF), "Past PO conversation")
        self.assertEqual(fw("You are the product owner (PO) of the project X"), "Past PO conversation")
        self.assertEqual(fw("You are 'claude', the engineer"), "Task agent conversation")
        long = "word " * 40
        got = fw(long)
        self.assertTrue(got.endswith("…"))
        self.assertLessEqual(len(got), 81)
        self.assertNotIn("wor…", got.replace("word…", ""))


if __name__ == "__main__":
    unittest.main()
