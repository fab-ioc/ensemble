"""Room index and cursor safety on a growing chat."""
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

import chatroom
import attention
import dashboard


class RoomIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.rooms = patch.object(chatroom, "ROOMS_DIR", self.path)
        self.rooms.start()
        self.addCleanup(self.rooms.stop)
        chatroom._INDEX.clear()
        chatroom._CHANGES.clear()

    def room(self, rid="room-example", **fields):
        return {**{"id": rid, "projectId": "proj-1", "workflow": "inprogress",
                   "updatedAt": time.time(), "messages": []}, **fields}

    def test_index_reuses_unchanged_room_and_detects_external_change(self):
        room = self.room()
        chatroom._write(room)
        with patch.object(chatroom, "_read", wraps=chatroom._read) as read:
            one = chatroom.room_index()[0]
            chatroom.room_index()
            self.assertEqual(read.call_count, 0)
        self.assertEqual({"id", "project", "workflow", "updatedAt", "size", "mtime"}
                         - one.keys(), set())
        room["workflow"] = "done"
        (self.path / "room-example.json").write_text(json.dumps(room), encoding="utf-8")
        self.assertEqual(chatroom.room_index()[0]["workflow"], "done")
        self.assertIsNone(chatroom.room_delta(room["id"], one["signature"]))

    def test_cold_room_reopens_when_updated_or_live(self):
        room = self.room(updatedAt=time.time() - 90000, workflow="done")
        self.assertTrue(chatroom.room_is_cold(room))
        self.assertFalse(chatroom.room_is_cold(room, lambda _: True))
        room["workflow"] = "inprogress"
        self.assertFalse(chatroom.room_is_cold(room))

    def test_old_stop_does_not_hide_a_resumed_open_task(self):
        old = time.time() - 3 * 86400
        room = self.room(updatedAt=old + 3600, workflow="inprogress",
                         stoppedAt=old, participants=[{"resumedAt": old + 300}])
        self.assertFalse(chatroom.room_is_cold(room, lambda _: False))
        room["participants"][0]["resumedAt"] = 0
        room["launchedAt"] = old + 200
        self.assertFalse(chatroom.room_is_cold(room, lambda _: False))
        room["launchedAt"] = old - 200
        self.assertTrue(chatroom.room_is_cold(room, lambda _: False))

    def test_delta_collects_appends_and_edits_or_requires_refetch(self):
        room = self.room()
        chatroom._write(room)
        start = chatroom.room_revision(room["id"])
        room["messages"].append({"id": "m1", "text": "first"})
        chatroom._write(room)
        room["messages"][0] = {**room["messages"][0], "text": "edited"}
        room["messages"].append({"id": "m2", "text": "second"})
        chatroom._write(room)
        self.assertEqual(chatroom.room_delta(room["id"], start),
                         (chatroom.room_revision(room["id"]), {"m1", "m2"}))
        room["messages"].pop(0)
        chatroom._write(room, removed=1)
        self.assertIsNone(chatroom.room_delta(room["id"], start))

    def test_snapshot_revision_matches_room(self):
        room = self.room()
        chatroom._write(room)
        snapshot, revision = chatroom.get_room_snapshot(room["id"])
        self.assertEqual(snapshot["id"], room["id"])
        self.assertEqual(revision, chatroom.room_revision(room["id"]))
        snapshot["messages"].append({"id": "local"})
        self.assertEqual(chatroom.get_room(room["id"])["messages"], [])

    def test_attention_summary_omits_old_done_room_and_restores_reopen(self):
        room = self.room(updatedAt=time.time() - 90000, workflow="done",
                         participants=[], status="active")
        chatroom._write(room)
        attention._SUMMARY_CACHE.clear()
        with patch.object(dashboard, "load_projects", return_value=[]), \
                patch.object(dashboard, "load_session_projects", return_value={}), \
                patch.object(dashboard, "_room_is_live", return_value=False):
            self.assertEqual(attention._room_summaries(), [])
            room["workflow"] = "inprogress"
            room["updatedAt"] = time.time()
            chatroom._write(room)
            self.assertEqual([r["id"] for r in attention._room_summaries()], [room["id"]])

    def test_attention_keeps_old_po_and_room_waiting_for_person(self):
        old = time.time() - 90000
        po = self.room("room-po", updatedAt=old, workflow="done",
                       participants=[], status="active")
        waiting = self.room("room-waiting", updatedAt=old, workflow="done",
                            participants=[], status="waiting_human")
        chatroom._write(po)
        chatroom._write(waiting)
        attention._SUMMARY_CACHE.clear()
        attention._COLD_HUMAN.clear()
        with patch.object(dashboard, "load_projects", return_value=[
                {"id": "proj-1", "name": "Project", "poRoomId": po["id"]}]), \
                patch.object(dashboard, "load_session_projects", return_value={}), \
                patch.object(dashboard, "_room_is_live", return_value=False):
            self.assertEqual({r["id"] for r in attention._room_summaries()},
                             {po["id"], waiting["id"]})

    def test_stopped_backlog_room_remains_in_sessions_and_project_board(self):
        old = time.time() - 2 * 86400
        room = self.room("room-backlog", title="Stopped backlog task", no=18,
                         workflow="backlog", updatedAt=old, stoppedAt=old,
                         cwd=str(self.path), workspace={"mode": "worktree"},
                         participants=[{"kind": "agent", "identity": "codex",
                                        "agent": "codex", "sessionId": ""}])
        chatroom._write(room)
        done = self.room("room-done", title="Older done task", no=19,
                         workflow="done", updatedAt=old - 60,
                         cwd=str(self.path), participants=[])
        chatroom._write(done)
        project = {"id": "proj-1", "name": "Project", "path": str(self.path),
                   "home": str(self.path), "isGit": False}
        with ExitStack() as stack:
            patches = [
                patch.object(dashboard, "load_projects", return_value=[project]),
                patch.object(dashboard, "load_session_projects", return_value={
                    room["id"]: project["id"], done["id"]: project["id"]}),
                patch.object(dashboard, "project_keys", return_value={project["id"]: "P"}),
                patch.object(dashboard, "ref_context", return_value={"projects": [
                    {"id": project["id"], "aliases": []}]}),
                patch.object(dashboard, "PROJ_DIR", self.path / "transcripts"),
                patch.object(dashboard, "CS_ROOT", self.path / "cs"),
                patch.object(dashboard, "load_live", return_value=[]),
                patch.object(dashboard, "_read_agent_session_files", return_value=[]),
                patch.object(dashboard, "_room_is_live", return_value=False),
                patch.object(dashboard, "load_labels", return_value={}),
                patch.object(dashboard, "load_parents", return_value={}),
                patch.object(dashboard, "load_archived", return_value=set()),
                patch.object(dashboard, "load_jira_links", return_value={}),
                patch.object(dashboard, "load_jira_unlinks", return_value={}),
                patch.object(dashboard, "compute_room_cost", side_effect=AssertionError("cold cost")),
                patch.object(dashboard, "branch_changes", side_effect=AssertionError("cold Git")),
                patch.object(attention, "by_room", return_value={}),
                patch.object(attention, "waiting_on_po", return_value={}),
                patch.object(attention, "reported_by_room", return_value={}),
                patch.object(attention, "run_by_room", return_value={}),
                patch.dict(os.environ, {"CODEX_HOME": str(self.path / "codex")}),
            ]
            for item in patches:
                stack.enter_context(item)
            dashboard._SESS_CACHE.clear()
            server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/api/sessions?n=200", timeout=10) as response:
                    sessions = json.loads(response.read())
                row = next(r for r in sessions if r.get("roomId") == room["id"])
                self.assertEqual(row["workflow"], "backlog")
                self.assertEqual(row["no"], 18)
                self.assertIn("reason", row["makePo"])
                self.assertIn("fix", row["makePo"])
                self.assertIn(done["id"], [r.get("roomId") for r in sessions])
                with urlopen(base + "/api/projects", timeout=10) as response:
                    projects = json.loads(response.read())["projects"]
                board_rows = next(p for p in projects if p["id"] == project["id"])["sessions"]
                self.assertIn(room["id"], [r.get("roomId") for r in board_rows])
                self.assertIn(done["id"], [r.get("roomId") for r in board_rows])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
