"""A task's agents start in the task's own folder and nowhere else (ED-344).

* ``seat_folders.launch_folder``: a folder that is there is used; a missing
  worktree whose branch is still in the project is made again (and the chat
  says so); anything else is a refused start that names the folder, marks the
  task, and leaves the seat's recorded folder as it was.
* ``Handler._resume_room_agent_pty`` never resumes in another folder (the old
  fallback to the room folder or ``~/cs`` is gone).
* ``resume_folder_check``: a Claude conversation recorded under another
  folder is refused, naming that folder.
* ``repair_rooms`` / ``repair_at_start``: a seat an older hub moved to ``~/cs``
  gets the task's folder back once, with a backup; a seat in another folder of
  the task (``repo-linux``) and a running seat are left alone.
* attention: the refused start is a "could not start" item, not "agent gone".
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import attention
import chatroom
import dashboard
import seat_folders


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class _Stop(Exception):
    """Raised by the stubbed agent once the launch has chosen its folder."""


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name).resolve()
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = self.dir / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)
        self.projects: dict[str, dict] = {}
        self.ptys: dict[str, object] = {}
        self.created: list = []
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir),
                  mock.patch.object(dashboard, "PROJ_DIR", self.dir / "claude-projects"),
                  mock.patch.object(dashboard, "find_project", lambda pid: self.projects.get(pid)),
                  mock.patch.object(dashboard, "_room_project_id",
                                    lambda rid, room, links=None: (room or {}).get("projectId", "")),
                  mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.ptys.get(pid)),
                  mock.patch.object(dashboard.ptyrun, "create",
                                    lambda *a, **k: self.created.append((a, k))),
                  mock.patch.object(dashboard.lost_sessions, "claude_config_dir",
                                    lambda: self.dir / "claude-config")):
            p.start()
            self.addCleanup(p.stop)

    # -- fixtures ---------------------------------------------------------
    def project_repo(self, branch="sess/ED-1-x") -> Path:
        repo = self.dir / "project"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
             "--allow-empty", "-m", "init")
        _git(repo, "branch", branch)
        self.projects["p1"] = {"id": "p1", "path": str(repo)}
        return repo

    def room(self, cwd: Path, task_dir: Path, mode="worktree", branch="sess/ED-1-x",
             seats=("claude",)) -> str:
        parts = [{"identity": s, "agent": s.split("-")[0], "model": "", "role": "engineer"}
                 for s in seats]
        rid = chatroom.create_room("t", parts)["id"]
        full = chatroom.get_room(rid, public=False)
        full.update(launched=True, cwd=str(cwd), taskDir=str(task_dir), sharedCwd=True,
                    projectId="p1",
                    workspace={"mode": mode, "branch": branch, "taskDir": str(task_dir)})
        for p in full["participants"]:
            if p.get("kind") == "agent":
                p["cwd"] = str(cwd)
                p["sessionId"] = "sid-" + p["identity"]
        chatroom.update_room(full)
        return rid

    def full(self, rid) -> dict:
        return chatroom.get_room(rid, public=False)

    def raw(self, rid) -> dict:
        return json.loads((chatroom.ROOMS_DIR / f"{rid}.json").read_text(encoding="utf-8"))

    def seat(self, room, ident="claude") -> dict:
        return next(p for p in room["participants"] if p.get("identity") == ident)


class LaunchFolder(_Base):
    def test_a_folder_that_is_there_is_used_as_it_is(self):
        task = self.dir / "task"
        (task / "repo").mkdir(parents=True)
        (task / "repo" / "f.txt").write_text("x")
        rid = self.room(task / "repo", task)
        room = self.full(rid)
        self.assertEqual(seat_folders.launch_folder(room, "claude", str(task / "repo")),
                         str(task / "repo"))
        self.assertNotIn("folderMissing", self.raw(rid))

    def test_a_missing_worktree_on_a_branch_that_exists_is_made_again_and_said(self):
        self.project_repo()
        task = self.dir / "task"
        task.mkdir()
        wt = task / "repo"
        rid = self.room(wt, task)
        room = self.full(rid)
        got = seat_folders.launch_folder(room, "claude", str(wt))
        self.assertEqual(got, str(wt))
        self.assertTrue((wt / ".git").exists())
        head = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "sess/ED-1-x")
        notes = [m for m in self.full(rid)["messages"] if m.get("noticeKind") == "folder"]
        self.assertEqual(len(notes), 1)
        self.assertIn(str(wt), notes[0]["text"])
        # On the caller's copy too, so its own later write keeps it.
        self.assertTrue(any(m.get("noticeKind") == "folder" for m in room["messages"]))

    def test_an_empty_folder_left_at_the_worktrees_place_is_made_a_worktree(self):
        self.project_repo()
        task = self.dir / "task"
        (task / "repo").mkdir(parents=True)          # an older hub's makedirs
        rid = self.room(task / "repo", task)
        seat_folders.launch_folder(self.full(rid), "claude", str(task / "repo"))
        self.assertTrue((task / "repo" / ".git").exists())

    def test_a_missing_worktree_whose_branch_is_gone_is_refused_and_marked(self):
        self.project_repo()
        task = self.dir / "task"
        task.mkdir()
        wt = task / "repo"
        rid = self.room(wt, task, branch="sess/gone")
        before = self.seat(self.raw(rid))["cwd"]
        with self.assertRaises(dashboard.MissingFolderError) as cm:
            seat_folders.launch_folder(self.full(rid), "claude", str(wt))
        text = str(cm.exception)
        self.assertIn(f"claude's folder {wt} is missing", text)
        self.assertIn("sess/gone", text)
        self.assertIn("move the task to a folder that exists", text)
        self.assertFalse(wt.exists())
        raw = self.raw(rid)
        self.assertEqual(self.seat(raw)["cwd"], before)
        self.assertEqual(raw["cwd"], str(wt))
        self.assertEqual(raw["folderMissing"]["path"], str(wt))
        self.assertEqual(raw["folderMissing"]["identity"], "claude")
        self.assertIsInstance(dashboard.MissingFolderError("x"), dashboard.StartRoomError)

    def test_a_folder_that_comes_back_clears_the_mark(self):
        task = self.dir / "task"
        task.mkdir()
        rid = self.room(task / "repo", task, mode="inplace")
        with self.assertRaises(dashboard.MissingFolderError):
            seat_folders.launch_folder(self.full(rid), "claude", str(task / "repo"))
        self.assertTrue(self.raw(rid).get("folderMissing"))
        (task / "repo").mkdir()
        seat_folders.launch_folder(self.full(rid), "claude", str(task / "repo"))
        self.assertFalse(self.raw(rid).get("folderMissing"))

    def test_the_hubs_own_scratch_folder_is_made_as_at_a_first_launch(self):
        task = self.dir / "task"
        task.mkdir()
        rid = self.room(task, task, mode="empty")
        own = str(task / "claude")
        self.assertEqual(seat_folders.launch_folder(self.full(rid), "claude", own, own), own)
        self.assertTrue(os.path.isdir(own))

    def test_scratch_under_a_missing_task_folder_is_refused(self):
        task = self.dir / "gone"
        rid = self.room(task, task, mode="empty")
        own = str(task / "claude")
        with self.assertRaises(dashboard.MissingFolderError):
            seat_folders.launch_folder(self.full(rid), "claude", own, own)
        self.assertFalse(task.exists())


class ResumeNeverMoves(_Base):
    def handler(self, seen: list):
        class Agent:
            def ensure_trusted(self, cwd):
                seen.append(cwd)
                raise _Stop

        class H(dashboard.Handler):
            def __init__(self):
                pass

        p = mock.patch.object(dashboard.agents, "get_agent", lambda key: Agent())
        p.start()
        self.addCleanup(p.stop)
        return H()

    def test_a_missing_recorded_folder_is_refused_not_moved_to_cs(self):
        task = self.dir / "task"
        task.mkdir()
        rid = self.room(task / "repo", task, branch="sess/gone")
        self.project_repo()
        room = self.full(rid)
        seen: list = []
        h = self.handler(seen)
        with self.assertRaises(dashboard.MissingFolderError):
            h._resume_room_agent_pty(room, self.seat(room))
        self.assertEqual(seen, [])
        self.assertEqual(self.created, [])
        self.assertEqual(self.seat(self.raw(rid))["cwd"], str(task / "repo"))
        self.assertFalse((task / "repo").exists())
        self.assertNotEqual(self.seat(room)["cwd"], str(dashboard.CS_ROOT))

    def test_a_missing_worktree_is_resumed_in_its_own_folder_made_again(self):
        self.project_repo()
        task = self.dir / "task"
        task.mkdir()
        rid = self.room(task / "repo", task)
        room = self.full(rid)
        seen: list = []
        h = self.handler(seen)
        with self.assertRaises(_Stop):
            h._resume_room_agent_pty(room, self.seat(room))
        self.assertEqual(seen, [str(task / "repo")])

    def test_a_conversation_recorded_under_another_folder_is_refused(self):
        task = self.dir / "task"
        (task / "repo").mkdir(parents=True)
        (task / "repo" / "f.txt").write_text("x")
        rid = self.room(task / "repo", task)
        other = self.dir / "claude-projects" / dashboard.claude_project_slug("/home/x/cs")
        other.mkdir(parents=True)
        (other / "sid-claude.jsonl").write_text(
            json.dumps({"type": "user", "cwd": "/home/x/cs"}) + "\n", encoding="utf-8")
        room = self.full(rid)
        seen: list = []
        h = self.handler(seen)
        with self.assertRaises(dashboard.MissingFolderError) as cm:
            h._resume_room_agent_pty(room, self.seat(room))
        self.assertIn("recorded in /home/x/cs", str(cm.exception))
        self.assertEqual(seen, [])
        self.assertEqual(self.raw(rid)["folderMissing"]["kind"], "elsewhere")

    def test_a_conversation_under_its_own_folder_or_nowhere_resumes(self):
        task = self.dir / "task"
        (task / "repo").mkdir(parents=True)
        (task / "repo" / "f.txt").write_text("x")
        rid = self.room(task / "repo", task)
        room = self.full(rid)
        seen: list = []
        h = self.handler(seen)
        with self.assertRaises(_Stop):          # nowhere on disk: the lost-session path
            h._resume_room_agent_pty(room, self.seat(room))
        own = self.dir / "claude-projects" / dashboard.claude_project_slug(str(task / "repo"))
        own.mkdir(parents=True)
        (own / "sid-claude.jsonl").write_text("{}\n", encoding="utf-8")
        elsewhere = self.dir / "claude-projects" / "-home-x-cs"
        elsewhere.mkdir(parents=True)
        (elsewhere / "sid-claude.jsonl").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(_Stop):          # also under its own folder: fine
            h._resume_room_agent_pty(room, self.seat(room))
        self.assertEqual(seen, [str(task / "repo")] * 2)


class Repair(_Base):
    def damaged(self):
        task = self.dir / "task"
        (task / "repo").mkdir(parents=True)
        (task / "repo-linux").mkdir()
        rid = self.room(task / "repo", task, seats=("claude", "codex", "claude-2"))
        room = self.full(rid)
        self.seat(room, "claude")["cwd"] = str(dashboard.CS_ROOT)
        self.seat(room, "codex")["cwd"] = str(task / "repo-linux")
        chatroom.update_room(room)
        return rid, task

    def test_a_seat_moved_to_cs_is_put_back_once_with_a_backup(self):
        rid, task = self.damaged()
        backups = self.dir / "bk"
        rows = seat_folders.repair_rooms(chatroom.ROOMS_DIR, backups)
        self.assertEqual([(r["identity"], r["now"]) for r in rows],
                         [("claude", str(task / "repo"))])
        raw = self.raw(rid)
        self.assertEqual(self.seat(raw, "claude")["cwd"], str(task / "repo"))
        self.assertEqual(self.seat(raw, "codex")["cwd"], str(task / "repo-linux"))
        self.assertEqual(len(list(backups.glob(f"{rid}.*.json"))), 1)
        self.assertEqual(seat_folders.repair_rooms(chatroom.ROOMS_DIR, backups), [])
        self.assertEqual(len(list(backups.glob(f"{rid}.*.json"))), 1)

    def test_a_running_seat_is_left_and_said(self):
        rid, _ = self.damaged()
        rows = seat_folders.repair_rooms(chatroom.ROOMS_DIR, self.dir / "bk",
                                         is_live=lambda room, p: True)
        self.assertEqual(rows[0]["skipped"], "running now")
        self.assertEqual(self.seat(self.raw(rid), "claude")["cwd"], str(dashboard.CS_ROOT))

    def test_a_task_that_is_not_a_worktree_is_left_alone(self):
        task = self.dir / "task"
        task.mkdir()
        rid = self.room(task, task, mode="inplace")
        room = self.full(rid)
        self.seat(room)["cwd"] = str(dashboard.CS_ROOT)
        chatroom.update_room(room)
        self.assertEqual(seat_folders.repair_rooms(chatroom.ROOMS_DIR, self.dir / "bk"), [])

    def test_at_hub_start_through_the_room_store(self):
        rid, task = self.damaged()
        rows = seat_folders.repair_at_start()
        self.assertEqual([r["identity"] for r in rows], ["claude"])
        self.assertEqual(self.seat(self.full(rid), "claude")["cwd"], str(task / "repo"))
        self.assertEqual(len(list((self.dir / "backups" / "seat-folders").glob("*.json"))), 1)
        self.assertEqual(seat_folders.repair_at_start(), [])

    def test_at_hub_start_a_live_seat_is_left(self):
        rid, _ = self.damaged()
        chatroom.patch_participant(rid, "claude", {"ptyId": "live-1"})
        self.ptys["live-1"] = types.SimpleNamespace(alive=lambda: True)
        rows = seat_folders.repair_at_start()
        self.assertEqual(rows[0]["skipped"], "running now")
        self.assertEqual(self.seat(self.full(rid), "claude")["cwd"], str(dashboard.CS_ROOT))


class Attention(unittest.TestCase):
    def room(self, path, kind="missing"):
        return {"id": "room-1", "participants": [{"identity": "claude", "kind": "agent"}],
                "folderMissing": {"path": path, "identity": "claude", "kind": kind,
                                  "reason": "claude's folder /x is missing", "since": time.time()}}

    def test_a_refused_start_is_a_could_not_start_item(self):
        hit = attention._folder_missing(self.room("/no/such/folder"))
        self.assertEqual(hit[0], "blocked")
        self.assertTrue(hit[1].startswith("could not start: claude's folder"))
        self.assertEqual(hit[2]["cause"], "folder_missing")
        self.assertEqual(hit[3]["identity"], "claude")

    def test_it_goes_once_the_folder_is_there_unless_the_conversation_is_elsewhere(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(attention._folder_missing(self.room(d)))
            self.assertIsNotNone(attention._folder_missing(self.room(d, kind="elsewhere")))
        self.assertIsNone(attention._folder_missing({"id": "r"}))


if __name__ == "__main__":
    unittest.main()
