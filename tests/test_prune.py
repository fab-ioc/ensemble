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
from unittest import mock

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

    def test_no_project_task_artifact_folder_is_prunable(self):
        scratch = self.tmp / "cs" / "01_report"
        write(scratch / "report.md", "keep with task")
        room = chatroom.create_room("No project report", [{"identity": "claude", "agent": "claude"}])
        room = chatroom.get_room(room["id"], public=False)
        room.update(id=room["id"], projectId="", workflow="done", workflowAt=OLD,
                    cwd=str(scratch), taskDir=str(scratch), workspace={"mode": "empty", "taskDir": str(scratch)})
        chatroom.update_room(room)
        hub = self.hub()
        hub.scratch_root = lambda folder: str(self.tmp / "cs") if prune.within(folder, self.tmp / "cs") else ""
        item = next(it for g in prune.scan(hub, 30)["groups"] for it in g["items"] if it["id"] == f"done:{room['id']}")
        folder = next(p for p in item["parts"] if p["kind"] == "folder")
        self.assertFalse(item["ticked"], "a no-project task folder requires explicit selection")
        self.assertEqual(folder["path"], str(scratch))
        result = prune.run(hub, {"groups": [{"id": "done", "items": [item]}], "kept": [], "bytes": item["bytes"]}, [item["id"]])
        self.assertEqual(result["skipped"], [])
        self.assertFalse(scratch.exists())

    def test_orphan_folder_and_housekeeping(self):
        orphan = self.home / "gone_task"
        write(orphan / "task.json", "{}")
        age(orphan / "task.json")
        age(orphan)
        log = write(self.state / "logs" / "ensemble.log.1", "old log")
        age(log, time.time() - 2 * DAY)
        rotated_today = write(self.state / "logs" / "ensemble.log.2", "rotated just now")
        current_log = write(self.state / "logs" / "ensemble.log", "today")
        sends = write(self.state / "sends" / "room-deadbeef.json", "{}")
        kept_sends = write(self.state / "sends" / "room-cafebabe.json", "{}")
        age(sends)
        age(kept_sends)
        old_spool = write(self.state / "pty-spool" / "pty-00000001.bin", "x")
        listed_spool = write(self.state / "pty-spool" / "pty-00000002.bin", "x")
        for f in (old_spool, listed_spool):
            age(f)
        hub = self.hub()
        hub.pty_ids = lambda: {"pty-00000002"}
        # Days = 0: still nothing written today.
        zero = prune.scan(hub, 0, groups=("housekeeping",))
        logs0 = [p["path"] for g in zero["groups"] for it in g["items"] if it["id"] == "hk:logs" for p in it["parts"]]
        self.assertNotIn(str(rotated_today), logs0)
        self.assertNotIn(str(current_log), logs0)
        preview = prune.scan(hub, 30)
        spools = [p["path"] for g in preview["groups"] for it in g["items"] if it["id"] == "hk:spool" for p in it["parts"]]
        self.assertEqual(spools, [str(old_spool)])
        # A task with that id came back since the preview: its sends stay.
        for g in preview["groups"]:
            for it in g["items"]:
                if it["id"] == "hk:sends:sends":
                    it["parts"].append(prune._part("sends", str(kept_sends), 2, room="room-cafebabe"))
        real_rooms = hub.rooms
        hub.rooms = lambda: real_rooms() + [{"id": "room-cafebabe"}]
        self.assertIn(f"orphan:{prune._norm(str(orphan))}", self.ids(preview, "orphans"))
        self.assertTrue({"hk:logs", "hk:sends:sends"} <= self.ids(preview, "housekeeping"))
        prune.run(hub, preview, self.ids(preview))
        self.assertFalse(orphan.exists())
        self.assertFalse(log.exists())
        self.assertFalse(sends.exists())
        self.assertTrue(kept_sends.exists())
        self.assertFalse(old_spool.exists())
        self.assertTrue(listed_spool.exists())
        self.assertTrue(rotated_today.exists())
        self.assertTrue(current_log.exists())
        self.assertTrue(self.docs.exists())

    def orphan(self, name):
        folder = self.home / name
        write(folder / "task.json", "{}")
        return folder

    def aged(self, *folders):
        for f in folders:
            for dirpath, _dirs, files in os.walk(f):
                for x in files:
                    age(Path(dirpath) / x)
            age(f)

    def test_an_orphan_clone_with_its_own_commits_stays(self):
        # A clone in repo/ (a project with no git link, or a branch only the
        # clone has): its commits are nowhere else, so its folder is kept.
        clone_home = self.orphan("cloned")
        git(self.tmp, "clone", "-q", str(self.repo), str(clone_home / "repo"))
        git(clone_home / "repo", "config", "user.email", "t@t")
        git(clone_home / "repo", "config", "user.name", "t")
        git(clone_home / "repo", "checkout", "-q", "-b", "only-here")
        write(clone_home / "repo" / "c.txt", "committed only in the clone\n")
        git(clone_home / "repo", "add", "-A")
        git(clone_home / "repo", "commit", "-q", "-m", "only here")
        git(clone_home / "repo", "checkout", "-q", "main")       # HEAD is in the project; a branch is not
        plain = self.orphan("plain_clone")                        # a clone with nothing of its own goes
        git(self.tmp, "clone", "-q", str(self.repo), str(plain / "repo"))
        self.aged(clone_home, plain)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        ids = self.ids(preview, "orphans")
        self.assertNotIn(f"orphan:{prune._norm(str(clone_home))}", ids)
        self.assertTrue(any(k.get("path") == str(clone_home) and "does not have" in k["why"]
                            for k in preview["kept"]), preview["kept"])
        self.assertIn(f"orphan:{prune._norm(str(plain))}", ids)
        # With isGit off the project's repository is not asked: any commit keeps it.
        self.project["isGit"] = False
        self.assertNotIn(f"orphan:{prune._norm(str(plain))}", self.ids(prune.scan(hub, 30), "orphans"))

    def test_an_orphan_with_uncommitted_work_is_unticked_and_checked_at_run(self):
        dirty_one = self.orphan("dirty_clone")
        git(self.tmp, "clone", "-q", str(self.repo), str(dirty_one / "repo"))
        write(dirty_one / "repo" / "new.txt", "never committed")
        late = self.orphan("late_clone")
        git(self.tmp, "clone", "-q", str(self.repo), str(late / "repo"))
        self.aged(dirty_one, late)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        items = {it["id"]: it for g in preview["groups"] for it in g["items"]}
        d = items[f"orphan:{prune._norm(str(dirty_one))}"]
        self.assertFalse(d["ticked"])
        self.assertIn("uncommitted", d["note"])
        self.assertTrue(items[f"orphan:{prune._norm(str(late))}"]["ticked"])
        # Dirty since the preview: kept, even ticked. Unticked for it and
        # ticked by hand: it goes.
        write(late / "repo" / "late.txt", "after the preview")
        res = prune.run(hub, preview, [f"orphan:{prune._norm(str(late))}"], how="auto")
        self.assertTrue((late / "repo" / "late.txt").exists())
        self.assertIn("uncommitted", " ".join(res["done"][0]["errors"]))
        prune.run(hub, preview, [d["id"]])
        self.assertFalse(dirty_one.exists())

    def test_a_failing_status_keeps_an_orphan(self):
        broken = self.orphan("broken")
        write(broken / "repo" / ".git", "gitdir: C:/nowhere/at/all")   # a worktree whose record is gone
        self.aged(broken)
        preview = prune.scan(self.hub(), 30)
        self.assertNotIn(f"orphan:{prune._norm(str(broken))}", self.ids(preview, "orphans"))
        self.assertTrue(any(k.get("path") == str(broken) for k in preview["kept"]))

    def test_a_room_that_does_not_parse_is_not_gone(self):
        rid = "room-0badf00d"
        sid = "abcdef01-2345-6789-abcd-ef0123456789"
        folder = self.orphan("unreadable_task")
        conv = write(self.claude / "proj" / f"{sid}.jsonl", '{"type":"user"}\n')
        log_ = write(self.state / "rooms" / "messages" / f"{rid}.jsonl", "{}")
        sends = write(self.state / "sends" / f"{rid}.json", "{}")
        write(self.state / "rooms" / f"{rid}.json",
              '{"id": "%s", "taskDir": %s, "participants": [{"sessionId": "%s"}], BROKEN'
              % (rid, json.dumps(str(folder)), sid))
        self.aged(folder)
        for f in (conv, log_, sends):
            age(f)
        hub = self.hub()
        hub.classify = lambda agent, path, s: ("hub", "")
        preview = prune.scan(hub, 30)
        paths = {p["path"] for g in preview["groups"] for it in g["items"] for p in it["parts"]}
        for f in (folder, conv, log_, sends):
            self.assertNotIn(str(f), paths)
        self.assertTrue(any(k.get("room") == rid for k in preview["kept"]))
        # Truly gone (no record on disk), but written lately: not yet.
        fresh = write(self.state / "sends" / "room-0000aaaa.json", "{}")
        self.assertNotIn(str(fresh), {p["path"] for g in prune.scan(hub, 30)["groups"]
                                      for it in g["items"] for p in it["parts"]})
        # Its record back on disk between preview and run: kept.
        gone = write(self.state / "sends" / "room-0000bbbb.json", "{}")
        age(gone)
        preview = prune.scan(hub, 30)
        write(self.state / "rooms" / "room-0000bbbb.json", "not json either")
        prune.run(hub, preview, ["hk:sends:sends"])
        self.assertTrue(gone.exists())

    def test_a_copy_mode_task_is_unticked(self):
        rid, folder, cwd, *_ = self.task("copy1", worktree=False)
        write(cwd / "work.py", "the only copy")
        room = chatroom.get_room(rid, public=False)
        room["workspace"] = {"mode": "copy", "taskDir": str(folder)}
        chatroom.update_room(room)
        hub = self.hub()
        item = next(it for g in prune.scan(hub, 30)["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
        self.assertFalse(item["ticked"])
        self.assertIn("copy with no git", item["note"])
        prune.auto_prune(hub, 30)
        self.assertTrue((cwd / "work.py").exists())

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

    def test_uncommitted_work_never_goes_by_itself(self):
        clean, fc, cwd_c, *_ = self.task("u1")
        dirty, fd, cwd_d, *_ = self.task("u2")
        write(cwd_d / "new.txt", "never committed")
        hub = self.hub()
        res = prune.auto_prune(hub, 30)
        self.assertEqual([d["id"] for d in res["done"]], [f"done:{clean}"])
        self.assertTrue((cwd_d / "new.txt").exists())
        # Clean at the preview, dirty by the run: kept.
        later, fl, cwd_l, *_ = self.task("u3")
        preview = prune.scan(hub, 30)
        write(cwd_l / "late.txt", "written after the preview")
        res = prune.run(hub, preview, [f"done:{later}", f"done:{dirty}"])
        whys = {s["id"]: s["why"] for s in res["skipped"]}
        self.assertIn("uncommitted", whys[f"done:{later}"])
        self.assertTrue((cwd_l / "late.txt").exists())
        # Unticked in the preview for it, ticked by hand: it goes.
        self.assertEqual([d["id"] for d in res["done"]], [f"done:{dirty}"])
        self.assertFalse(fd.exists())

    def test_a_checkout_git_does_not_list_keeps_its_folder(self):
        # A worktree git cannot list (the listing failed): its work was never
        # checked, so the folder that holds it stays, even by auto.
        rid, folder, cwd, *_ = self.task("n1")
        write(cwd / "new.txt", "never committed")
        hub = self.hub()
        with mock.patch.object(prune, "worktrees", lambda repo: []):
            preview = prune.scan(hub, 30)
            item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
            self.assertNotIn("folder", {p["kind"] for p in item["parts"]})
            self.assertTrue(any(k.get("path") == str(folder) and "git does not list" in k["why"]
                                for k in preview["kept"]), preview["kept"])
            prune.run(hub, preview, [item["id"]], how="auto")
        self.assertTrue((cwd / "new.txt").exists())
        # A leftover repo/ with no .git is only files: it goes with the folder.
        rid2, folder2, cwd2, *_ = self.task("n2", worktree=False)
        write(cwd2 / "algo" / "x.txt", "leftover")
        preview = prune.scan(hub, 30)
        item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{rid2}")
        self.assertIn("folder", {p["kind"] for p in item["parts"]})

    def test_a_status_that_fails_reads_as_dirty(self):
        self.assertTrue(prune.dirty(str(self.tmp / "not-a-repo")))
        self.assertFalse(prune.dirty(str(self.repo)))
        write(self.repo / "b.txt", "x")
        self.assertTrue(prune.dirty(str(self.repo)))

    def test_slim_drops_the_kept_original(self):
        rid, *_ = self.task("o1")
        original = write(self.state / "rooms" / "before-message-log" / f"{rid}.json", "x" * 5000)
        hub = self.hub()
        prune.run(hub, prune.scan(hub, 30), [f"done:{rid}"])
        self.assertFalse(original.exists())

    def test_a_cwd_outside_is_never_a_worktree(self):
        outside = self.tmp / "elsewhere"
        write(outside / "keep.txt", "not Ensemble's")
        room = chatroom.create_room("x1", [])
        room = chatroom.get_room(room["id"], public=False)
        room.update(projectId="proj-1", workflow="done", workflowAt=OLD, cwd=str(outside), taskDir="",
                    workspace={"mode": "worktree", "branch": "", "taskDir": ""})
        chatroom.update_room(room)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{room['id']}")
        self.assertNotIn("worktree", {p["kind"] for p in item["parts"]})
        prune.run(hub, preview, [item["id"]])
        self.assertTrue((outside / "keep.txt").exists())

    def test_a_pos_past_conversation_goes_only_once_its_text_is_kept(self):
        """#265: the PO chats' history went with their files; the hub keeps
        each one's text first, and one it cannot keep (Codex) stays."""
        po = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude"}])["id"]
        self.project["poRoomId"] = po
        cl = write(self.claude / "po" / "11111111-2222-3333-4444-000000000001.jsonl", "{}\n" * 20)
        cx = write(self.codex / "2026" / "10" / "01" / "rollout-2026-10-01T10-00-00-11111111-2222-3333-4444-000000000002.jsonl", "{}\n" * 20)
        age(cl), age(cx)
        kept = []
        hub = self.hub()
        hub.classify = lambda agent, path, sid: ("po", po)
        hub.keep_text = lambda agent, sid, path: kept.append((agent, sid, os.path.basename(path))) or agent == "claude"
        preview = prune.scan(hub, 30)
        self.assertIn(f"po:{po}", self.ids(preview, "po"))
        res = prune.run(hub, preview, [f"po:{po}"])
        self.assertFalse(cl.exists(), "its text kept: the file goes")
        self.assertTrue(cx.exists(), "its text could not be kept: it stays")
        self.assertIn("could not be kept", " ".join(res["done"][0]["errors"]))
        self.assertEqual(sorted(a for a, _s, _n in kept), ["claude", "codex"])

    def test_a_conversation_used_since_the_preview_stays(self):
        mine = write(self.claude / "elsewhere" / "11111111-2222-3333-4444-666666666666.jsonl", "{}\n")
        age(mine)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        os.utime(mine, None)                       # resumed outside Ensemble
        res = prune.run(hub, preview, ["own:11111111-2222-3333-4444-666666666666"])
        self.assertTrue(mine.exists())
        self.assertIn("in use", res["done"][0]["errors"][0])

    def test_labels(self):
        rid, *_ = self.task("l1")
        room = chatroom.get_room(rid, public=False)
        room.update(no=18, title="18. Fix the parser")
        chatroom.update_room(room)
        item = next(it for g in prune.scan(self.hub(), 30)["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
        self.assertEqual(item["label"], "#18 Fix the parser")

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
