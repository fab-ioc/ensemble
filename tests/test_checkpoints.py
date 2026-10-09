"""Per-turn checkpoints of a task's git workspace (ED-197): snapshot, restore
and undo on a throwaway repo."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import checkpoints  # noqa: E402

ROOM = "room-test1234"


def git(root, *args):
    out = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode:
        raise AssertionError(f"git {args}: {out.stderr}")
    return out.stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cp-test-")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "core.autocrlf", "false")
        self.write(".gitignore", "*.log\nbuild/\n")
        self.write("a.txt", "one\ntwo\n")
        self.write("b.txt", "bee\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "first")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as f:
            return f.read()

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def visible(self):
        """What the agent sees: status, log, HEAD, branch, the index's entries."""
        return (git(self.root, "status", "--porcelain=v1", "-uall"), git(self.root, "log", "--oneline"),
                git(self.root, "rev-parse", "HEAD"), git(self.root, "symbolic-ref", "HEAD"),
                git(self.root, "ls-files", "-s"))

    def take(self, **kw):
        return checkpoints.take(self.root, ROOM, **kw)


class TakeTest(Repo):
    def test_the_first_is_the_base_and_an_unchanged_turn_is_skipped(self):
        base = self.take()
        self.assertEqual((base["kind"], base["n"]), ("base", 0))
        self.assertIsNone(self.take())
        self.write("a.txt", "one\ntwo\nthree\n")
        cp = self.take(msg_id="m1", identity="claude")
        self.assertEqual((cp["kind"], cp["n"], cp["files"], cp["add"], cp["del"], cp["msgId"]),
                         ("turn", 1, 1, 1, 0, "m1"))
        self.assertIsNone(self.take())
        turns = checkpoints.list_checkpoints(self.root, ROOM)["turns"]
        self.assertEqual([t["n"] for t in turns], [0, 1])

    def test_taking_one_changes_nothing_the_agent_sees(self):
        self.write("a.txt", "changed\n")
        self.write("new/untracked.txt", "u\n")
        self.write("staged.txt", "s\n")
        git(self.root, "add", "staged.txt")
        self.write("x.log", "ignored\n")
        before = self.visible()
        index = Path(self.root, ".git", "index")
        raw, mtime = index.read_bytes(), index.stat().st_mtime_ns
        cp = self.take()
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), (raw, mtime))
        self.assertEqual(self.visible(), before)
        names = git(self.root, "ls-tree", "-r", "--name-only", cp["sha"]).split("\n")
        self.assertIn("new/untracked.txt", names)
        self.assertIn("staged.txt", names)
        self.assertNotIn("x.log", names)
        self.assertEqual(git(self.root, "log", "--oneline", "main"), git(self.root, "log", "--oneline"))

    def test_a_turn_that_only_commits_is_a_checkpoint(self):
        self.write("a.txt", "x\n")
        self.take()
        git(self.root, "commit", "-q", "-am", "c")
        cp = self.take()
        self.assertIsNotNone(cp)
        self.assertEqual(cp["files"], 0)

    def test_no_git_no_checkpoint_no_error(self):
        plain = tempfile.mkdtemp(prefix="cp-plain-")
        self.addCleanup(shutil.rmtree, plain, ignore_errors=True)
        self.assertIsNone(checkpoints.take(plain, ROOM))
        self.assertEqual(checkpoints.list_checkpoints(plain, ROOM)["turns"], [])

    def test_at_most_200_the_oldest_go(self):
        old = checkpoints.MAX_TURNS
        checkpoints.MAX_TURNS = 5
        self.addCleanup(setattr, checkpoints, "MAX_TURNS", old)
        for i in range(8):
            self.write("a.txt", f"v{i}\n")
            self.take()
        ns = [t["n"] for t in checkpoints.list_checkpoints(self.root, ROOM)["turns"]]
        self.assertEqual(ns, [3, 4, 5, 6, 7])
        self.assertEqual(checkpoints.MAX_TURNS and old, 200)

    def test_rooms_are_apart_and_deleted_with_their_task(self):
        self.take()
        checkpoints.take(self.root, "room-other")
        self.assertEqual(checkpoints.rooms_in(self.root), ["room-other", ROOM])
        self.assertEqual(checkpoints.delete_room(self.root, ROOM), 1)
        self.assertEqual(checkpoints.rooms_in(self.root), ["room-other"])

    def test_a_bad_room_id_is_refused(self):
        with self.assertRaises(ValueError):
            checkpoints.take(self.root, "../x")


class RestoreTest(Repo):
    def test_restore_brings_back_the_files_branch_and_index_then_undo(self):
        self.take()                                       # 0
        self.write("a.txt", "turn1\n")
        self.write("untracked.txt", "kept from turn 1\n")
        self.write("old.txt", "to be renamed\n")
        git(self.root, "add", "old.txt")
        cp1 = self.take()                                 # 1
        at1 = self.visible()
        # Turn 2: a commit, a rename, a deletion, new files, an ignored file.
        git(self.root, "mv", "old.txt", "renamed.txt")
        git(self.root, "commit", "-q", "-am", "turn 2 commit")
        os.remove(os.path.join(self.root, "b.txt"))
        os.remove(os.path.join(self.root, "untracked.txt"))
        self.write("later.txt", "made after\n")
        self.write("deep/dir/later2.txt", "made after\n")
        self.write("x.log", "ignored, keep me\n")
        self.write("build/out.bin", "ignored too\n")
        self.take()                                       # 2
        at2 = self.visible()
        p = checkpoints.preview(self.root, ROOM, n=1)
        self.assertEqual([c["subject"] for c in p["commits"]], ["turn 2 commit"])
        self.assertIn("later.txt", [f["path"] for f in p["files"]])
        res = checkpoints.restore(self.root, ROOM, n=1, by="you")
        self.assertEqual(res["n"], 1)
        self.assertEqual(self.visible(), at1)
        self.assertEqual(self.read("a.txt"), "turn1\n")
        self.assertEqual(self.read("untracked.txt"), "kept from turn 1\n")
        self.assertTrue(self.exists("b.txt") and self.exists("old.txt"))
        self.assertFalse(self.exists("renamed.txt") or self.exists("later.txt") or self.exists("deep"))
        self.assertEqual(self.read("x.log"), "ignored, keep me\n")
        self.assertTrue(self.exists("build/out.bin"))
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), cp1["head"])
        # Undo: back to just before the restore.
        checkpoints.restore(self.root, ROOM, undo=res["restore"], by="you")
        self.assertEqual(self.visible(), at2)
        self.assertEqual(self.read("later.txt"), "made after\n")
        self.assertFalse(self.exists("b.txt"))
        self.assertTrue(self.exists("renamed.txt"))
        lst = checkpoints.list_checkpoints(self.root, ROOM)
        self.assertEqual([(r["m"], r["toKind"]) for r in lst["restores"]], [(1, "checkpoint"), (2, "undo")])

    def test_the_turn_after_a_restore_counts_from_the_restored_state(self):
        self.take()
        self.write("a.txt", "1\n")
        self.take()
        self.write("a.txt", "2\n")
        self.take()
        checkpoints.restore(self.root, ROOM, n=1)
        self.assertIsNone(self.take())                   # nothing changed since the restore
        self.write("c.txt", "c\n")
        cp = self.take()
        self.assertEqual((cp["n"], cp["files"]), (3, 1))
        d = checkpoints.diff(self.root, ROOM, 3)
        self.assertEqual([f["path"] for f in d["files"]], ["c.txt"])

    def test_diff_of_a_turn(self):
        self.take()
        self.write("a.txt", "one\nTWO\n")
        self.write("n.txt", "new\n")
        self.take()
        d = checkpoints.diff(self.root, ROOM, 1)
        self.assertEqual(sorted((f["path"], f["status"]) for f in d["files"]), [("a.txt", "M"), ("n.txt", "A")])
        one = checkpoints.diff(self.root, ROOM, 1, "a.txt")["diff"]
        self.assertIn("-two", one)
        self.assertIn("+TWO", one)

    def test_a_same_size_edit_in_the_index_second_is_seen(self):
        # The agent's index and its next edit share a timestamp and a size:
        # only git's racy-clean check catches it, and that needs the
        # temporary index to keep the real one's mtime.
        self.take()
        a = os.path.join(self.root, "a.txt")
        index = os.path.join(self.root, ".git", "index")
        st = os.stat(a)    # the stat the index has cached for a.txt
        self.write("a.txt", "one\nTWO\n")
        os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.utime(index, ns=(st.st_atime_ns, st.st_mtime_ns))
        time.sleep(1.1)
        self.take()
        d = checkpoints.diff(self.root, ROOM, 1)
        self.assertEqual([(f["path"], f["status"]) for f in d["files"]], [("a.txt", "M")])

    def test_an_unknown_checkpoint_is_a_plain_refusal(self):
        self.take()
        with self.assertRaises(checkpoints.CheckpointError):
            checkpoints.restore(self.root, ROOM, n=9)
        with self.assertRaises(checkpoints.CheckpointError):
            checkpoints.restore(self.root, ROOM, undo=3)

    def test_a_busy_index_is_refused_and_nothing_moves(self):
        self.take()
        self.write("a.txt", "x\n")
        self.take()
        lock = os.path.join(self.root, ".git", "index.lock")
        open(lock, "w").close()
        self.addCleanup(lambda: os.path.exists(lock) and os.remove(lock))
        before = self.read("a.txt")
        with self.assertRaises(checkpoints.CheckpointError):
            checkpoints.restore(self.root, ROOM, n=0)
        self.assertEqual(self.read("a.txt"), before)

    def test_the_branch_never_points_at_a_checkpoint_commit(self):
        self.take()
        self.write("a.txt", "x\n")
        self.write("u.txt", "untracked\n")
        self.take()
        res = checkpoints.restore(self.root, ROOM, n=0)
        checkpoints.restore(self.root, ROOM, undo=res["restore"])
        ours = {r["sha"] for r in checkpoints._records(self.root, ROOM)}
        seen = set(git(self.root, "reflog", "--format=%H", "refs/heads/main").split())
        self.assertFalse(ours & seen)
        self.assertEqual(git(self.root, "log", "--format=%s", "main"), "first")

    def test_a_restore_that_fails_is_rolled_back_and_leaves_no_record(self):
        self.take()
        self.write("a.txt", "turn1\n")
        self.take()
        self.write("a.txt", "now\n")
        real = checkpoints._apply
        calls = []
        def failing(root, rec):
            calls.append(rec)
            if len(calls) == 1:
                real(root, rec)          # half done, then git gives up
                raise subprocess.CalledProcessError(1, ["git"], "", "boom")
            real(root, rec)
        checkpoints._apply = failing
        self.addCleanup(setattr, checkpoints, "_apply", real)
        with self.assertRaises(checkpoints.CheckpointError):
            checkpoints.restore(self.root, ROOM, n=0)
        self.assertEqual(self.read("a.txt"), "now\n")
        self.assertEqual(checkpoints.list_checkpoints(self.root, ROOM)["restores"], [])

    def test_a_file_git_cannot_add_fails_the_snapshot(self):
        self.take()
        self.write("a.txt", "turn1\n")
        real = checkpoints._git
        def git_add_fails(root, *args, **kw):
            if args[:1] == ("add",):
                raise subprocess.CalledProcessError(128, ["git", "add"], "", "unable to read a.txt")
            return real(root, *args, **kw)
        checkpoints._git = git_add_fails
        self.addCleanup(setattr, checkpoints, "_git", real)
        self.assertIsNone(self.take())                   # a turn: no mark, no error
        with self.assertRaisesRegex(checkpoints.CheckpointError, "nothing was changed"):
            checkpoints.restore(self.root, ROOM, n=0)    # a restore: refused
        self.assertEqual(self.read("a.txt"), "turn1\n")
        self.assertEqual(len(checkpoints._records(self.root, ROOM)), 1)

    def test_another_branch_checked_out_is_refused_and_never_moved(self):
        self.take()
        self.write("a.txt", "x\n")
        self.take()
        git(self.root, "checkout", "-q", "-b", "other")
        git(self.root, "commit", "-q", "-am", "on other")
        tips = git(self.root, "rev-parse", "main", "other")
        with self.assertRaisesRegex(checkpoints.CheckpointError, "branch other, not branch main"):
            checkpoints.restore(self.root, ROOM, n=0)
        self.assertEqual(git(self.root, "rev-parse", "main", "other"), tips)
        self.assertEqual(git(self.root, "symbolic-ref", "--short", "HEAD"), "other")
        self.assertEqual(checkpoints.list_checkpoints(self.root, ROOM)["restores"], [])

    def test_a_checkpoint_before_the_first_commit_never_deletes_the_branch(self):
        root = tempfile.mkdtemp(prefix="cp-unborn-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        git(root, "init", "-q", "-b", "main")
        git(root, "config", "user.email", "t@t")
        git(root, "config", "user.name", "t")
        with open(os.path.join(root, "f.txt"), "w", encoding="utf-8") as f:
            f.write("f\n")
        checkpoints.take(root, ROOM)                     # 0: no commit yet
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "first")
        checkpoints.take(root, ROOM, force=True)
        with self.assertRaisesRegex(checkpoints.CheckpointError, "first commit"):
            checkpoints.restore(root, ROOM, n=0)
        self.assertEqual(git(root, "log", "--format=%s", "main"), "first")

    def test_own_worktree_only_a_linked_worktree_top(self):
        wt = os.path.join(tempfile.mkdtemp(prefix="cp-wt-"), "task")
        self.addCleanup(shutil.rmtree, os.path.dirname(wt), ignore_errors=True)
        git(self.root, "worktree", "add", "-q", wt, "-b", "task")
        self.assertTrue(checkpoints.own_worktree(wt))
        self.assertFalse(checkpoints.own_worktree(self.root))      # the main checkout
        os.makedirs(os.path.join(wt, "sub"))
        self.assertFalse(checkpoints.own_worktree(os.path.join(wt, "sub")))

    def test_pruning_counts_only_the_kind_just_added(self):
        old = checkpoints.MAX_TURNS, checkpoints.MAX_RESTORES
        checkpoints.MAX_TURNS, checkpoints.MAX_RESTORES = 3, 2
        self.addCleanup(lambda: (setattr(checkpoints, "MAX_TURNS", old[0]),
                                 setattr(checkpoints, "MAX_RESTORES", old[1])))
        for i in range(3):
            self.write("a.txt", f"v{i}\n")
            self.take()                                  # turns 0, 1, 2: full
        checkpoints.restore(self.root, ROOM, n=1)
        checkpoints.restore(self.root, ROOM, n=2)        # restores 1, 2: full
        lst = checkpoints.list_checkpoints(self.root, ROOM)
        self.assertEqual([t["n"] for t in lst["turns"]], [0, 1, 2])
        self.write("a.txt", "v9\n")
        self.take()                                      # a turn drops a turn, not a restore
        lst = checkpoints.list_checkpoints(self.root, ROOM)
        self.assertEqual([t["n"] for t in lst["turns"]], [1, 2, 3])
        self.assertEqual([r["m"] for r in lst["restores"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
