"""Freeing disk space (#260): prune.scan finds what can go, prune.run deletes
what was ticked, each part checked again; never a task that is not Done, a
running one, a PO's room, Documents, an unmerged branch. On a throwaway repo,
project home and state folder, with a fake hub."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chatroom  # noqa: E402
import checkpoints  # noqa: E402
import prune  # noqa: E402

DAY = 86400
OLD = time.time() - 60 * DAY


def git(root, *args):
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode:
        raise AssertionError(f"git {args}: {out.stderr}")
    return out.stdout.strip()


def write(p, text="x"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def age(p, when=OLD):
    os.utime(p, (when, when))


class PruneTests(unittest.TestCase):
    def setUp(self):
        # A short root: the long-path test goes past 260 characters itself.
        self.tmp = Path(tempfile.mkdtemp(prefix="pr-"))
        self.addCleanup(shutil.rmtree, prune._long(self.tmp), True)
        self.state = self.tmp / "st"
        self.old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = self.state / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", self.old_rooms)
        self.claude = self.tmp / "cl"
        self.codex = self.tmp / "cx"
        self.repo = self.tmp / "repo"
        self.home = self.tmp / "home"
        self.docs = self.home / "Documents"
        write(self.docs / "#1 report.md", "keep me")
        write(self.home / "ROADMAP.md", "roadmap")
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        write(self.repo / "a.txt", "one\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "first")
        self.project = {"id": "proj-1", "name": "P", "path": str(self.repo), "isGit": True,
                        "poRoomId": ""}
        self.live: set = set()
        self.live_sids: set = set()

    # -- the hub -------------------------------------------------------------
    def hub(self):
        def room_sessions(r):
            return [(p.get("agent") or "claude", p.get("sessionId"))
                    for p in r.get("participants") or [] if p.get("sessionId")]

        return prune.Hub(
            state_dir=self.state, claude_dir=self.claude, codex_dir=self.codex,
            projects=lambda: [self.project], rooms=lambda: chatroom.list_rooms(copy_rooms=False),
            live_rooms=lambda: set(self.live), live_sessions=lambda: set(self.live_sids),
            room_sessions=room_sessions, current_sessions=lambda r: set(),
            classify=lambda agent, path, sid: ("own", ""),
            slim_room=lambda rid, meta: chatroom.slim_room(rid, meta, "pruned"),
            forget_checkpoints=lambda r: checkpoints.delete_room(r["cwd"], r["id"]),
            checkpoint_counts=checkpoints.counts,
            project_home=lambda p: str(self.home), documents_dir=lambda p: str(self.docs))

    def task(self, name, *, workflow="done", at=OLD, branch_commit=False, worktree=True):
        """A task with a folder, a worktree on its own branch, one agent
        conversation and a chat."""
        sid = f"0000000{len(name)}-0000-0000-0000-{abs(hash(name)) % 10**12:012d}"
        folder = self.home / name
        cwd = folder / "repo"
        branch = f"sess/{name}"
        if worktree:
            folder.mkdir(parents=True)
            git(self.repo, "worktree", "add", "-q", "-b", branch, str(cwd))
            if branch_commit:
                write(cwd / "b.txt", "unmerged\n")
                git(cwd, "add", "-A")
                git(cwd, "commit", "-q", "-m", "work")
        write(folder / "task.json", "{}")
        write(folder / "TASK-HANDOVER.md", "notes")
        conv = write(self.claude / "proj" / f"{sid}.jsonl", '{"type":"user"}\n' * 50)
        age(conv)
        room = chatroom.create_room(name, [{"identity": "claude", "agent": "claude", "sessionId": sid,
                                            "cwd": str(cwd)}])
        room = chatroom.get_room(room["id"], public=False)
        room.update(projectId="proj-1", workflow=workflow, workflowAt=at, taskDir=str(folder),
                    cwd=str(cwd), spec="the spec", lastReport={"text": "done well"},
                    workspace={"mode": "worktree", "branch": branch, "taskDir": str(folder)})
        chatroom.update_room(room)
        chatroom.post_message(room["id"], "claude", "hello " * 200)
        return room["id"], folder, cwd, branch, conv

    def ids(self, preview, group=None):
        return {it["id"] for g in preview["groups"] for it in g["items"]
                if group is None or g["id"] == group}

    # -- tests ---------------------------------------------------------------
    def test_done_task_goes_whole_and_keeps_a_slim_record(self):
        rid, folder, cwd, branch, conv = self.task("a")
        deep = folder / ("d" * 60) / ("e" * 60) / ("f" * 60) / ("g" * 60) / "deep.txt"
        write(prune._long(deep), "long path")
        hub = self.hub()
        preview = prune.scan(hub, 30)
        item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
        kinds = {p["kind"] for p in item["parts"]}
        self.assertTrue({"worktree", "branch", "folder", "conversation", "chat", "room"} <= kinds, kinds)
        self.assertTrue(item["ticked"])
        self.assertGreater(item["bytes"], 0)
        res = prune.run(hub, preview, [item["id"]])
        self.assertEqual(res["skipped"], [])
        self.assertEqual(res["done"][0]["errors"], [])
        self.assertFalse(os.path.lexists(prune._long(folder)))
        self.assertFalse(conv.exists())
        self.assertEqual(git(self.repo, "branch", "--list", branch), "")
        self.assertNotIn(str(cwd), git(self.repo, "worktree", "list"))
        # Never the project's own files.
        self.assertEqual((self.docs / "#1 report.md").read_text(encoding="utf-8"), "keep me")
        self.assertTrue((self.home / "ROADMAP.md").exists())
        self.assertTrue((self.repo / "a.txt").exists())
        # The slim record: number, title, spec, report, one notice.
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["title"], "a")
        self.assertEqual(room["spec"], "the spec")
        self.assertEqual(room["lastReport"], {"text": "done well"})
        self.assertIn("pruned", room)
        self.assertEqual(len(room["messages"]), 1)
        self.assertEqual(room["messages"][0]["noticeKind"], "pruned")
        self.assertEqual(room["tokens"], {})
        self.assertNotIn("sessionId", room["participants"][0])
        # Read again from disk (the index forgotten): still the slim record.
        chatroom._INDEX.pop(rid, None)
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), 1)
        # A second preview lists it no more.
        self.assertNotIn(f"done:{rid}", self.ids(prune.scan(hub, 30)))
        lines = [json.loads(x) for x in (self.state / "logs" / "prune.log").read_text(
            encoding="utf-8").splitlines()]
        whats = " | ".join(x["what"] for x in lines)
        for word in ("worktree", "branch", "task folder", "conversation", "chat"):
            self.assertIn(word, whats)
        self.assertTrue(all("at" in x and "bytes" in x and "path" in x for x in lines))
        self.assertGreater(res["freed"], 0)

    def test_unmerged_branch_is_kept_and_named(self):
        rid, folder, cwd, branch, _ = self.task("b", branch_commit=True)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        self.assertTrue(any(k.get("branch") == branch for k in preview["kept"]))
        item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
        self.assertNotIn("branch", {p["kind"] for p in item["parts"]})
        prune.run(hub, preview, [item["id"]])
        self.assertIn(branch, git(self.repo, "branch", "--list", branch))
        self.assertIn("work", git(self.repo, "log", "--oneline", branch))

    def test_uncommitted_work_is_not_ticked(self):
        rid, folder, cwd, *_ = self.task("c")
        write(cwd / "new.txt", "never committed")
        item = next(it for g in prune.scan(self.hub(), 30)["groups"] for it in g["items"]
                    if it["id"] == f"done:{rid}")
        self.assertFalse(item["ticked"])
        self.assertIn("uncommitted", item["note"])

    def test_what_is_never_listed(self):
        running, *_ = self.task("run1", workflow="inprogress")
        recent, *_ = self.task("new1", at=time.time() - DAY)
        live, *_ = self.task("live1")
        po, *_ = self.task("po1")
        self.live.add(live)
        self.project["poRoomId"] = po
        found = self.ids(prune.scan(self.hub(), 30), "done")
        for rid in (running, recent, live, po):
            self.assertNotIn(f"done:{rid}", found)

    def test_run_checks_again(self):
        moved, *_ = self.task("m1")
        started, *_ = self.task("s1")
        hub = self.hub()
        preview = prune.scan(hub, 30)
        room = chatroom.get_room(moved, public=False)
        room["workflow"] = "review"
        chatroom.update_room(room)
        self.live.add(started)
        res = prune.run(hub, preview, [f"done:{moved}", f"done:{started}"])
        self.assertEqual(res["done"], [])
        whys = {s["id"]: s["why"] for s in res["skipped"]}
        self.assertIn("no longer Done", whys[f"done:{moved}"])
        self.assertIn("running", whys[f"done:{started}"])
        self.assertTrue((self.home / "m1" / "task.json").exists())
        self.assertGreater(len(chatroom.get_room(moved)["messages"]), 0)

    def test_only_ticked_ids_go(self):
        a, fa, *_ = self.task("t1")
        b, fb, *_ = self.task("t2")
        hub = self.hub()
        prune.run(hub, prune.scan(hub, 30), [f"done:{a}"])
        self.assertFalse(fa.exists())
        self.assertTrue(fb.exists())

    def test_task_folder_guards(self):
        hub = self.hub()
        projects = [self.project]
        self.assertTrue(prune.task_folder_ok(hub, str(self.home), self.project, projects))
        self.assertTrue(prune.task_folder_ok(hub, str(self.repo), self.project, projects))
        odd = self.home / "odd"
        write(odd / "Documents" / "x.md")
        self.assertIn("own files", prune.task_folder_ok(hub, str(odd), self.project, projects))
        ok = self.home / "fine"
        write(ok / "task.json")
        self.assertEqual(prune.task_folder_ok(hub, str(ok), self.project, projects), "")

    def test_orphan_folder_and_housekeeping(self):
        orphan = self.home / "gone_task"
        write(orphan / "task.json", "{}")
        age(orphan / "task.json")
        age(orphan)
        log = write(self.state / "logs" / "ensemble.log.1", "old log")
        current_log = write(self.state / "logs" / "ensemble.log", "today")
        sends = write(self.state / "sends" / "room-deadbeef.json", "{}")
        hub = self.hub()
        preview = prune.scan(hub, 30)
        self.assertIn(f"orphan:{prune._norm(str(orphan))}", self.ids(preview, "orphans"))
        self.assertTrue({"hk:logs", "hk:sends:sends"} <= self.ids(preview, "housekeeping"))
        prune.run(hub, preview, self.ids(preview))
        self.assertFalse(orphan.exists())
        self.assertFalse(log.exists())
        self.assertFalse(sends.exists())
        self.assertTrue(current_log.exists())
        self.assertTrue(self.docs.exists())

    def test_own_conversations_listed_unticked(self):
        mine = write(self.claude / "elsewhere" / "11111111-2222-3333-4444-555555555555.jsonl", "{}\n")
        age(mine)
        preview = prune.scan(self.hub(), 30)
        own = next(g for g in preview["groups"] if g["id"] == "own")
        self.assertFalse(own["ticked"])
        self.assertTrue(all(not it["ticked"] for it in own["items"]))
        self.assertIn("own:11111111-2222-3333-4444-555555555555", self.ids(preview, "own"))

    def test_nothing_outside_ensembles_folders(self):
        outside = write(self.tmp / "outside" / "f.txt")
        hub = self.hub()
        item = {"id": "hk:x", "group": "housekeeping", "label": "x",
                "parts": [prune._part("log", str(outside), 1)]}
        res = prune.run(hub, {"days": 30, "groups": [{"id": "housekeeping", "items": [item]}]}, ["hk:x"])
        self.assertTrue(outside.exists())
        self.assertIn("not Ensemble's", res["done"][0]["errors"][0])

    def test_auto_prune_off_by_default(self):
        rid, folder, *_ = self.task("auto1")
        self.assertEqual(prune.auto_prune(self.hub(), 0)["done"], [])
        self.assertTrue(folder.exists())
        res = prune.auto_prune(self.hub(), 30)
        self.assertEqual(len(res["done"]), 1)
        self.assertFalse(folder.exists())
        lines = (self.state / "logs" / "prune.log").read_text(encoding="utf-8")
        self.assertIn('"how": "auto"', lines)

    @unittest.skipUnless(os.name == "nt", "Windows' 260-character limit")
    def test_long_path_removal(self):
        deep = self.tmp / "long"
        p = deep
        while len(str(p)) < 320:
            p = p / ("x" * 40)
        write(prune._long(p / "f.txt"), "deep")
        self.assertGreater(prune.size_of(deep), 0)
        prune.remove(deep)
        self.assertFalse(os.path.lexists(prune._long(deep)))


if __name__ == "__main__":
    unittest.main()
