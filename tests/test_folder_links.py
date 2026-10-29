"""Folder links after #282 (#285): every walker that stops at a link still
says it is there. The Files tree shows a linked folder as a row of its own,
never opened; git_roots lists a linked checkout after the task's own and never
walks through a link; the backup keeps every link out (git for Windows walks
into a junction and adds what it points to); the transcript table follows a
linked project folder, its walk being one level deep. Real junctions, in
folders under the temp folder only."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backup  # noqa: E402
import dashboard  # noqa: E402
import filetable  # noqa: E402
import fslinks  # noqa: E402
import prune  # noqa: E402
from test_prune import write  # noqa: E402
from test_prune_links import junction  # noqa: E402

WIN = os.name == "nt"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


@unittest.skipUnless(WIN, "junctions are Windows'")
class FolderLinkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ens-links-", ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # Links go first: a cleanup that walked into one would empty its target.
        self.addCleanup(lambda: fslinks.unlink_links(self.tmp))
        self.out = self.tmp / "elsewhere"
        write(self.out / "marker.txt", "must survive")

    def open_paths(self):
        p = mock.patch.object(dashboard, "workspace_access_ok", lambda _p: True)
        p.start()
        self.addCleanup(p.stop)

    # -- Files tree ---------------------------------------------------------
    def test_files_tree_shows_a_linked_folder_as_a_link_row(self):
        self.open_paths()
        d = self.tmp / "work"
        write(d / "own.txt", "own")
        (d / "sub").mkdir()
        junction(d / "j", self.out)
        junction(d / "loop", d)
        code, res = dashboard.list_dir(str(d))
        self.assertEqual(code, 200)
        by = {e["name"]: e for e in res["entries"]}
        self.assertEqual(set(by), {"own.txt", "sub", "j", "loop"})
        for n, to in (("j", self.out), ("loop", d)):
            self.assertTrue(by[n]["link"], n)
            self.assertEqual(by[n]["type"], "dir")
            self.assertEqual(by[n]["size"], 0)
            self.assertEqual(os.path.normcase(by[n]["target"]), os.path.normcase(str(to)))
        self.assertNotIn("link", by["sub"])
        self.assertNotIn("link", by["own.txt"])

    def test_link_target_reads_as_a_plain_path(self):
        junction(self.tmp / "j", self.out)
        self.assertFalse(fslinks.target(self.tmp / "j").startswith("\\"))
        self.assertEqual(fslinks.target(self.out), "", "a folder is not a link")

    # -- git_roots ----------------------------------------------------------
    def test_git_roots_lists_a_linked_checkout_last_and_never_walks_through_a_link(self):
        self.open_paths()
        task = self.tmp / "task"
        own = task / "zz-own"
        own.mkdir(parents=True)
        _git(own, "init", "-q")
        shared = self.tmp / "shared-checkout"
        shared.mkdir()
        _git(shared, "init", "-q")
        deep = self.out / "inner"
        deep.mkdir(parents=True)
        _git(deep, "init", "-q")
        junction(task / "aa-linked", shared)        # a checkout itself: listed
        junction(task / "bb-through", self.out)     # a repo behind it: not walked
        junction(task / "cc-loop", task)
        code, res = dashboard.git_roots(str(task), 3)
        self.assertEqual(code, 200)
        self.assertEqual([r["name"] for r in res["roots"]], ["zz-own", "aa-linked"])
        self.assertTrue(res["roots"][1]["link"])
        self.assertNotIn("link", res["roots"][0])

    # -- backup -------------------------------------------------------------
    def test_git_for_windows_follows_a_junction_so_the_backup_keeps_links_out(self):
        root = self.tmp / "projects"
        write(root / "Proj" / "notes.md", "n")
        behind = self.out / "home"
        write(behind / "small.txt", "behind a link")
        write(behind / "repo" / "a.txt", "a")
        _git(behind / "repo", "init", "-q")          # a nested repo with no commit
        with open(behind / "big.bin", "wb") as fh:
            fh.truncate(2 * 1024 * 1024)
        junction(root / "Proj" / "task" / "shadow", behind)
        nested, oversize, links = backup.scan(root, max_file_mb=1)
        self.assertEqual((nested, oversize, links), ([], [], ["Proj/task/shadow"]))
        # What a backup without the exclude would do: take in the link's
        # files, or, with a nested repo behind it, nothing at all.
        plain = self.tmp / "plain"
        write(plain / "small.txt", "behind a link")
        for target, ok in ((plain, True), (behind, False)):
            bare = self.tmp / ("bare-ok" if ok else "bare-nested")
            write(bare / "keep.txt", "k")
            junction(bare / "j", target)
            _git(bare, "init", "-q")
            add = _git(bare, "add", "-A")
            if ok:
                self.assertEqual(add.returncode, 0, add.stderr)
                self.assertIn("j/small.txt", _git(bare, "ls-files").stdout, "git for Windows walks into a junction")
            else:
                self.assertNotEqual(add.returncode, 0, "a nested repo behind a junction sinks the add")
        old = backup.MAX_FILE_MB
        backup.MAX_FILE_MB = 1
        try:
            r = backup.run_backup(root)
        finally:
            backup.MAX_FILE_MB = old
        self.assertTrue(r["ok"], r["msg"])
        self.assertIn("1 link(s) left out", r["msg"])
        tracked = _git(root, "ls-files").stdout.splitlines()
        self.assertIn("Proj/notes.md", tracked)
        self.assertFalse([t for t in tracked if t.startswith("Proj/task/shadow")], tracked)
        self.assertTrue((self.out / "marker.txt").is_file())

    def test_a_folder_replaced_by_a_link_leaves_the_backup_index(self):
        root = self.tmp / "projects"
        write(root / "Proj" / "notes.md", "n")
        write(root / "Proj" / "data" / "old.txt", "was a folder")
        self.assertTrue(backup.run_backup(root)["ok"])
        self.assertIn("Proj/data/old.txt", _git(root, "ls-files").stdout)
        fslinks.remove_tree(root / "Proj" / "data")
        write(self.out / "data" / "old.txt", "moved away")
        junction(root / "Proj" / "data", self.out / "data")
        r = backup.run_backup(root)
        self.assertTrue(r["ok"], r["msg"])
        self.assertNotIn("Proj/data", _git(root, "ls-files").stdout)
        self.assertEqual((self.out / "data" / "old.txt").read_text(encoding="utf-8"), "moved away")

    def test_a_link_that_cannot_leave_the_index_stops_the_backup(self):
        root = self.tmp / "projects"
        write(root / "Proj" / "notes.md", "n")
        junction(root / "Proj" / "shadow", self.out)
        real = backup._git

        def git(r, *args, **kw):
            if "rm" in args:
                return subprocess.CompletedProcess(args, 128, "", "index.lock exists")
            return real(r, *args, **kw)
        with mock.patch.object(backup, "_git", git):
            r = backup.run_backup(root)
        self.assertFalse(r["ok"])
        self.assertIn("links not taken out of the index: index.lock exists", r["msg"])
        self.assertNotIn("Proj/shadow/marker.txt", _git(root, "ls-files").stdout)

    # -- transcripts ---------------------------------------------------------
    def test_transcripts_in_a_linked_project_folder_are_found_and_a_loop_ends(self):
        root = self.tmp / "projects"
        write(root / "C--own" / "a.jsonl", "{}")
        write(self.out / "moved" / "b.jsonl", "{}")
        junction(root / "C--moved", self.out / "moved")
        junction(root / "C--back", root)              # one level deep: no loop
        t = filetable.FileTable(lambda: root, 1, lambda n: n.endswith(".jsonl"))
        t0 = time.monotonic()
        got = {os.path.relpath(p, root) for p in t.files()}
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual(got, {os.path.join("C--own", "a.jsonl"), os.path.join("C--moved", "b.jsonl")})

    # -- Free disk space -----------------------------------------------------
    def test_a_measure_out_of_time_in_its_last_folder_is_too_slow_not_partial(self):
        d = self.tmp / "many"
        for i in range(2100):
            write(d / f"f{i}.txt", "x")
        clock = iter([0.0] + [100.0] * 10_000)
        with mock.patch.object(prune.time, "monotonic", lambda: next(clock)):
            with self.assertRaises(prune.TooSlow):
                prune.size_of(d, until=50.0)

    def test_unlink_links_stops_at_a_folder_it_cannot_read(self):
        top = self.tmp / "wt"
        write(top / "locked" / "x.txt", "x")
        junction(top / "j", self.out)
        real = os.scandir

        def scandir(p):
            if os.path.basename(os.fspath(p)) == "locked":
                raise PermissionError(13, "denied", os.fspath(p))
            return real(p)
        with mock.patch.object(fslinks.os, "scandir", scandir):
            with self.assertRaises(PermissionError):
                fslinks.unlink_links(top)
        self.assertTrue((self.out / "marker.txt").is_file())


if __name__ == "__main__":
    unittest.main()
