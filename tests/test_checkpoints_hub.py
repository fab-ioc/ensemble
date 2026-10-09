"""Per-turn checkpoints in the hub (ED-197): a Claude Stop hook and a Codex
turn end each take one checkpoint; restore and undo are page-only, refused
while an agent works, and tell the owner."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chatroom  # noqa: E402
import checkpoints  # noqa: E402
import dashboard  # noqa: E402
from test_documents_project import Hub  # noqa: E402


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


class CheckpointHub(Hub):
    def setUp(self):
        super().setUp()
        # The project's checkout, and the task's own worktree of it.
        self.main = Path(self.tmp.name) / "main-repo"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "config", "user.email", "t@t")
        git(self.main, "config", "user.name", "t")
        (self.main / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "first")
        self.repo = Path(self.tmp.name) / "task-repo"
        git(self.main, "worktree", "add", "-q", str(self.repo), "-b", "sess/task")
        for d in (dashboard._CP_DIR, dashboard._CP_LIST, dashboard._CP_CODEX):
            d.clear()
        dashboard._CP_BASED.clear()
        for p in (mock.patch.object(dashboard, "workspace_access_ok", lambda path: True),
                  mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: "")):
            p.start()
            self.addCleanup(p.stop)

    def task(self, agent="claude"):
        rid = chatroom.create_room("Fix it", [{"identity": agent, "agent": agent, "model": "",
                                               "role": "engineer"}])["id"]
        room = chatroom.get_room(rid, public=False)
        room["cwd"] = str(self.repo)
        room["workspace"] = {"mode": "worktree", "branch": "sess/task"}
        chatroom.update_room(room)
        return rid

    def turns(self, rid):
        return checkpoints.list_checkpoints(str(self.repo), rid)["turns"]

    def wait_turns(self, rid, n, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if len(self.turns(rid)) >= n:
                return self.turns(rid)
            time.sleep(0.2)
        return self.turns(rid)


class TurnEnds(CheckpointHub):
    def test_a_claude_stop_hook_takes_one_checkpoint(self):
        rid = self.task()
        dashboard.checkpoint_turn(rid, kind="base")
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        sess = SimpleNamespace(meta={"room": rid, "identity": "claude"})
        body = {"room": rid, "identity": "claude", "ptyId": "p1", "at": time.time(),
                "event": {"hook_event_name": "Stop", "session_id": "s1"}}
        with mock.patch.object(dashboard.ptyrun, "get", return_value=sess), \
                mock.patch.object(dashboard, "_hook_turn_ended"):
            status, _ = self.json_call("POST", "/api/agent/hook", body)
        self.assertEqual(status, 200)
        turns = self.wait_turns(rid, 2)
        self.assertEqual([(t["n"], t["kind"], t["files"]) for t in turns], [(0, "base", 0), (1, "turn", 1)])
        # A reviewer's turn, or another task's, takes none.
        dashboard.checkpoint_turn(rid, "codex")
        self.assertEqual(len(self.turns(rid)), 2)

    def test_a_codex_turn_end_takes_one_checkpoint_and_the_first_tick_the_base(self):
        rid = self.task("codex")
        rollout = Path(self.tmp.name) / "rollout.jsonl"
        rollout.write_text('{"x":1}\n', encoding="utf-8")
        over = {"turnOver": True}
        sessions = [{"meta": {"room": rid, "identity": "codex"}, "alive": True}]
        with mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=sessions), \
                mock.patch.object(dashboard.rotation, "_transcript_of",
                                  return_value=(rollout, lambda p: dict(over))):
            dashboard._checkpoint_codex_tick()          # base, and the rollout as it is
            self.assertEqual([t["kind"] for t in self.turns(rid)], ["base"])
            (self.repo / "b.txt").write_text("new\n", encoding="utf-8")
            over["turnOver"] = False                     # working: the rollout grows
            rollout.write_text('{"x":1}\n{"y":2}\n', encoding="utf-8")
            dashboard._checkpoint_codex_tick()
            self.assertEqual(len(self.turns(rid)), 1)
            over["turnOver"] = True                      # its task_complete
            rollout.write_text('{"x":1}\n{"y":2}\n{"z":3}\n', encoding="utf-8")
            dashboard._checkpoint_codex_tick()
            dashboard._checkpoint_codex_tick()          # the same end again: nothing
        turns = self.turns(rid)
        self.assertEqual([(t["n"], t["files"]) for t in turns], [(0, 0), (1, 1)])

    def test_a_codex_first_turn_seen_while_working_is_checkpointed(self):
        # The usual start (and a hub restarted mid-turn): the first tick sees
        # the rollout while the turn runs.
        rid = self.task("codex")
        rollout = Path(self.tmp.name) / "rollout.jsonl"
        rollout.write_text('{"x":1}\n', encoding="utf-8")
        over = {"turnOver": False}
        sessions = [{"meta": {"room": rid, "identity": "codex"}, "alive": True}]
        with mock.patch.object(dashboard.ptyrun, "list_sessions", return_value=sessions), \
                mock.patch.object(dashboard.rotation, "_transcript_of",
                                  return_value=(rollout, lambda p: dict(over))):
            dashboard._checkpoint_codex_tick()
            (self.repo / "b.txt").write_text("new\n", encoding="utf-8")
            over["turnOver"] = True
            rollout.write_text('{"x":1}\n{"z":3}\n', encoding="utf-8")
            dashboard._checkpoint_codex_tick()
        self.assertEqual([(t["n"], t["files"]) for t in self.turns(rid)], [(0, 0), (1, 1)])

    def test_a_documents_project_or_no_git_has_none(self):
        rid = self.task()
        room = chatroom.get_room(rid, public=False)
        room["cwd"] = str(self.home)                     # the documents-free project folder, no .git
        chatroom.update_room(room)
        dashboard.checkpoint_turn(rid, kind="base")
        self.assertEqual(dashboard.checkpoint_list(rid), None)
        status, out = self.json_call("GET", "/api/room?id=" + rid)
        self.assertEqual((status, out["checkpoints"]), (200, None))

    def test_only_the_tasks_own_worktree_never_a_shared_folder(self):
        rid = self.task()
        room = chatroom.get_room(rid, public=False)
        cases = [({"mode": "inplace"}, self.main),         # in place in the project's code folder
                 ({"mode": "worktree"}, self.main),        # the main checkout
                 ({}, self.repo),                          # no workspace recorded
                 ({"mode": "copy"}, self.repo)]
        for ws, cwd in cases:
            room["workspace"], room["cwd"] = ws, str(cwd)
            chatroom.update_room(room)
            dashboard._CP_DIR.clear()
            self.assertEqual(dashboard.checkpoint_dir(room), "", (ws, cwd))
            dashboard.checkpoint_turn(rid, kind="base")
            status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": rid, "n": 0},
                                         self.page_headers())
            self.assertEqual((status, out.get("error")), (400, "no_checkpoints"), (ws, cwd))
        self.assertEqual(checkpoints.rooms_in(str(self.main)), [])
        # The project's own folder, even as a worktree task's cwd.
        room["workspace"], room["cwd"] = {"mode": "worktree"}, str(self.repo)
        with mock.patch.object(dashboard, "_room_project_id", return_value="p1"), \
                mock.patch.object(dashboard, "find_project", return_value={"path": str(self.repo)}):
            self.assertEqual(dashboard.checkpoint_dir(room), "")
        self.assertEqual(dashboard.checkpoint_dir(room), str(self.repo))

    def test_a_broken_repository_is_not_read_on_every_poll(self):
        rid = self.task()
        calls = []
        def broken(*a):
            calls.append(a)
            raise subprocess.CalledProcessError(128, ["git"])
        with mock.patch.object(checkpoints, "list_checkpoints", broken):
            self.assertIsNone(dashboard.checkpoint_list(rid))
            self.assertIsNone(dashboard.checkpoint_list(rid))
        self.assertEqual(len(calls), 1)


class Restore(CheckpointHub):
    def setUp(self):
        super().setUp()
        self.rid = self.task()
        dashboard.checkpoint_turn(self.rid, kind="base")
        (self.repo / "a.txt").write_text("turn 1\n", encoding="utf-8")
        dashboard.checkpoint_turn(self.rid, "claude")
        (self.repo / "a.txt").write_text("turn 2\n", encoding="utf-8")
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        dashboard.checkpoint_turn(self.rid, "claude")
        self.told = []
        p = mock.patch.object(dashboard, "_checkpoint_tell", lambda rid, line, *a: self.told.append(line))
        p.start()
        self.addCleanup(p.stop)

    def test_the_room_lists_them_and_the_diff_and_preview_read(self):
        status, out = self.json_call("GET", "/api/room?id=" + self.rid)
        self.assertEqual([t["n"] for t in out["checkpoints"]["turns"]], [0, 1, 2])
        status, d = self.json_call("GET", f"/api/checkpoints/diff?room={self.rid}&n=2")
        self.assertEqual(sorted(f["path"] for f in d["files"]), ["a.txt", "later.txt"])
        status, d = self.json_call("GET", f"/api/checkpoints/diff?room={self.rid}&n=2&file=a.txt")
        self.assertIn("+turn 2", d["diff"])
        status, pv = self.json_call("GET", f"/api/checkpoints/preview?room={self.rid}&n=1")
        self.assertEqual((status, sorted(f["path"] for f in pv["files"])), (200, ["a.txt", "later.txt"]))

    def test_only_the_page_and_only_while_idle(self):
        status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "n": 1},
                                     {"Authorization": "Bearer x"})
        self.assertEqual((status, out["error"]), (403, "page_only"))
        status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "n": 1})
        self.assertEqual(status, 403)
        with mock.patch.object(dashboard.rotation, "_pty", return_value=object()), \
                mock.patch.object(dashboard.attention, "turn_state", return_value=("working", "hook")):
            status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "n": 1},
                                         self.page_headers())
        self.assertEqual(status, 409)
        self.assertIn("is working", out["message"])
        self.assertEqual((self.repo / "a.txt").read_text(encoding="utf-8"), "turn 2\n")

    def test_restore_then_undo_and_the_owner_is_told(self):
        status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "n": 1},
                                     self.page_headers())
        self.assertEqual(status, 200, out)
        self.assertEqual((self.repo / "a.txt").read_text(encoding="utf-8"), "turn 1\n")
        self.assertFalse((self.repo / "later.txt").exists())
        self.assertEqual([r["m"] for r in out["checkpoints"]["restores"]], [1])
        self.assertTrue(self.told[-1].startswith("[checkpoint] "))
        self.assertIn("restored the code to checkpoint 1", self.told[-1])
        self.assertEqual(dashboard.hub_input_kind(self.told[-1]).get("kind"), "checkpoint")
        status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "undo": 1},
                                     self.page_headers())
        self.assertEqual(status, 200, out)
        self.assertEqual((self.repo / "later.txt").read_text(encoding="utf-8"), "later\n")
        self.assertIn("undid the restore", self.told[-1])

    def test_the_hub_gate_is_free_while_git_works_and_input_to_the_task_waits(self):
        seen = {}
        real = checkpoints.restore
        def watching(*a, **kw):
            got = []
            t = threading.Thread(target=lambda: got.append(dashboard.rotation.GATE.acquire(timeout=5)
                                                            and (dashboard.rotation.GATE.release() or True)))
            t.start()
            t.join()
            seen["gate_free"] = got == [True]
            seen["restoring"] = dashboard.checkpoint_restoring(self.rid)
            return real(*a, **kw)
        with mock.patch.object(checkpoints, "restore", watching):
            status, out = self.json_call("POST", "/api/checkpoints/restore", {"room": self.rid, "n": 1},
                                         self.page_headers())
        self.assertEqual(status, 200, out)
        self.assertEqual(seen, {"gate_free": True, "restoring": True})
        self.assertFalse(dashboard.checkpoint_restoring(self.rid))
        # Typing to the task waits for a restore under way, then goes.
        dashboard._CP_RESTORING[self.rid] = ev = threading.Event()
        threading.Timer(0.5, lambda: (dashboard._CP_RESTORING.pop(self.rid, None), ev.set())).start()
        t0 = time.time()
        dashboard.checkpoint_wait_restore(self.rid)
        self.assertGreaterEqual(time.time() - t0, 0.4)
        dashboard.checkpoint_wait_restore("room-other")          # another task: no wait

    def test_a_deleted_task_takes_its_refs(self):
        self.assertTrue(checkpoints.rooms_in(str(self.repo)))
        with mock.patch.object(dashboard, "stop_task"), \
                mock.patch.object(dashboard, "_delete_scratch_root", return_value=""):
            dashboard.delete_task(self.rid)
        self.assertEqual(checkpoints.rooms_in(str(self.repo)), [])


if __name__ == "__main__":
    unittest.main()
