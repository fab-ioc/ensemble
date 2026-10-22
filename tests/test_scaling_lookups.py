"""#256 step B: lookups that went over every room or re-read files per room.

An agent's tool call finds its room from the index, the project files are
parsed again only when they change, the stall check looks only at running
rooms, and old launch files are removed."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import stall


class _Rooms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        p = mock.patch.object(chatroom, "ROOMS_DIR", self.path)
        p.start()
        self.addCleanup(p.stop)
        chatroom._INDEX.clear()
        chatroom._CHANGES.clear()
        chatroom._TOKEN_ROOMS.clear()
        self.addCleanup(chatroom._INDEX.clear)
        self.addCleanup(chatroom._TOKEN_ROOMS.clear)


class ResolveToken(_Rooms):
    def test_finds_the_room_without_parsing_room_files(self):
        rooms = [chatroom.create_room(f"t{i}", [{"agent": "claude"}]) for i in range(5)]
        token, ident = next(iter(rooms[3]["tokens"].items()))
        chatroom.room_index()
        with mock.patch.object(chatroom, "_read", wraps=chatroom._read) as read:
            self.assertEqual(chatroom.resolve_token(token), (rooms[3]["id"], ident))
            self.assertEqual(chatroom.resolve_token(token), (rooms[3]["id"], ident))
            self.assertEqual(read.call_count, 0)
        self.assertIsNone(chatroom.resolve_token("nobody"))
        self.assertIsNone(chatroom.resolve_token(""))

    def test_a_token_taken_away_no_longer_resolves(self):
        room = chatroom.create_room("t", [{"agent": "claude"}])
        token = next(iter(room["tokens"]))
        self.assertIsNotNone(chatroom.resolve_token(token))
        # The agent leaves the line-up: its token goes with it.
        chatroom.set_agents(room["id"], [{"agent": "codex"}])
        self.assertIsNone(chatroom.resolve_token(token))
        new = next(iter(chatroom._read(room["id"])["tokens"]))
        self.assertEqual(chatroom.resolve_token(new), (room["id"], "codex"))

    def test_an_external_edit_is_seen(self):
        room = chatroom.create_room("t", [{"agent": "claude"}])
        token = next(iter(room["tokens"]))
        self.assertIsNotNone(chatroom.resolve_token(token))
        disk = chatroom._read(room["id"])
        disk["tokens"] = {"other": "claude"}
        (self.path / f"{room['id']}.json").write_text(json.dumps(disk), encoding="utf-8")
        self.assertIsNone(chatroom.resolve_token(token))
        self.assertEqual(chatroom.resolve_token("other"), (room["id"], "claude"))


class ProjectFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "root"
        self.root.mkdir()
        for name, val in (("PROJECTS_ROOT", self.root), ("PROJECTS_FILE", base / "projects.json"),
                          ("SESSION_PROJECTS_FILE", base / "session_projects.json")):
            p = mock.patch.object(dashboard, name, val)
            p.start()
            self.addCleanup(p.stop)
        dashboard._PROJECTS_CACHE.clear()
        dashboard._SESSION_PROJECTS_CACHE.clear()
        self.addCleanup(dashboard._PROJECTS_CACHE.clear)
        self.addCleanup(dashboard._SESSION_PROJECTS_CACHE.clear)

    def _age(self, path: Path, secs: float = 60):
        t = time.time() - secs
        os.utime(path, (t, t))

    def project(self, name, **meta):
        d = self.root / name
        d.mkdir(exist_ok=True)
        pj = d / "project.json"
        pj.write_text(json.dumps({"id": f"p-{name}", "name": name, **meta}), encoding="utf-8")
        self._age(pj)
        self._age(self.root)
        return pj

    def test_parsed_once_while_nothing_changes(self):
        self.project("a")
        self.assertEqual([p["id"] for p in dashboard.load_projects()], ["p-a"])
        with mock.patch.object(dashboard, "_load_projects_uncached",
                               wraps=dashboard._load_projects_uncached) as full:
            for _ in range(3):
                self.assertEqual([p["id"] for p in dashboard.load_projects()], ["p-a"])
            self.assertEqual(full.call_count, 0)

    def test_a_changed_or_added_project_is_read_again(self):
        pj = self.project("a")
        dashboard.load_projects()
        pj.write_text(json.dumps({"id": "p-a", "name": "renamed"}), encoding="utf-8")
        self._age(pj, 30)
        self.assertEqual(dashboard.load_projects()[0]["name"], "renamed")
        self.project("b")
        self.assertEqual([p["id"] for p in dashboard.load_projects()], ["p-a", "p-b"])
        # A project.json written into a folder that had none.
        (self.root / "c").mkdir()
        self._age(self.root)
        dashboard.load_projects()
        self.project("c")
        self.assertEqual(len(dashboard.load_projects()), 3)

    def test_a_repo_made_later_is_seen(self):
        self.project("a")
        self.assertFalse(dashboard.load_projects()[0]["isGit"])
        (self.root / "a" / ".git").mkdir()
        # Folders are looked at again every few seconds, not on every call.
        with mock.patch.object(dashboard, "PROJECTS_FOLDERS_RECHECK_S", 0):
            self.assertTrue(dashboard.load_projects()[0]["isGit"])

    def test_a_caller_changing_its_copy_changes_nothing(self):
        self.project("a")
        dashboard.load_projects()[0]["name"] = "mine"
        self.assertEqual(dashboard.load_projects()[0]["name"], "a")

    def test_a_file_written_just_now_is_not_trusted_by_its_stat(self):
        pj = self.project("a")
        pj.write_text(json.dumps({"id": "p-a", "name": "now"}), encoding="utf-8")
        dashboard.load_projects()
        self.assertIsNone(dashboard._PROJECTS_CACHE.get("hit"))

    def test_session_links_follow_the_file(self):
        f = dashboard.SESSION_PROJECTS_FILE
        self.assertEqual(dashboard.load_session_projects(), {})
        f.write_text(json.dumps({"room-1": "p-a"}), encoding="utf-8")
        self._age(f, 60)
        self.assertEqual(dashboard.load_session_projects(), {"room-1": "p-a"})
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("parsed again")):
            links = dashboard.load_session_projects()
        links["room-2"] = "p-b"                 # the caller's own copy
        self.assertEqual(dashboard.load_session_projects(), {"room-1": "p-a"})
        dashboard.assign_session_project("room-2", "p-b")
        self.assertEqual(dashboard.load_session_projects(), {"room-1": "p-a", "room-2": "p-b"})


class StallLooksAtRunningRooms(unittest.TestCase):
    def test_a_room_not_running_is_not_looked_at(self):
        rooms = {"room-run": {"id": "room-run"}, "room-old": {"id": "room-old"}}
        looks = ("_look_provider", "_look", "_look_limit", "_look_silent", "_look_permission")
        with mock.patch.object(stall, "_load", return_value={}), \
                mock.patch.object(stall, "_save"), \
                mock.patch.object(dashboard.chatroom, "ROOMS_DIR") as rd, \
                mock.patch.object(dashboard.provider_retry, "prune_rooms"), \
                mock.patch.object(dashboard.chatroom, "get_room",
                                  side_effect=lambda rid, **kw: rooms.get(rid)) as get, \
                mock.patch.object(dashboard, "_room_is_live",
                                  side_effect=lambda r: r["id"] == "room-run"):
            rd.glob.return_value = [Path("room-run.json"), Path("room-old.json"), Path("room-gone.json")]
            mocks = [mock.patch.object(stall, n, return_value="").start() for n in looks]
            try:
                stall.tick(1000)
            finally:
                mock.patch.stopall()
        for m in mocks:
            self.assertEqual([c.args[0] for c in m.call_args_list], ["room-run"])
        for c in get.call_args_list:
            self.assertFalse(c.kwargs.get("copy_room", True), "the stall check copies no room")


class LaunchFiles(unittest.TestCase):
    def test_old_launch_files_go_and_new_ones_stay(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(dashboard, "DASHBOARD_DIR", Path(tmp)):
            now = time.time()
            made = {}
            for d in ("_launch", "_mcp"):
                (Path(tmp) / d).mkdir()
                for name, age in (("old", 31 * 86400), ("mid", 8 * 86400), ("new", 86400)):
                    f = Path(tmp) / d / f"{name}.x"
                    f.write_text("x", encoding="utf-8")
                    os.utime(f, (now - age, now - age))
                    made[(d, name)] = f
            # A script goes after a week, an MCP config after 30 days.
            self.assertEqual(dashboard.prune_launch_files(now), 3)
            self.assertEqual({k for k, f in made.items() if f.exists()},
                             {("_launch", "new"), ("_mcp", "mid"), ("_mcp", "new")})


if __name__ == "__main__":
    unittest.main()
