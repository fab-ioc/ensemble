"""The projects backup keeps nested repositories and agent state out of the
commit, and copes with long paths on Windows (the hourly backup had failed on
both for six days)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import backup


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _tracked(root):
    return (_git(root, "ls-files", "-s").stdout or "").splitlines()


@unittest.skipUnless(shutil.which("git"), "git not installed")
class BackupExcludes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bk-"))
        self.root = self.tmp / "projects"
        (self.root / "Proj" / "task1").mkdir(parents=True)
        (self.root / "Proj" / "task1" / "notes.md").write_text("hi", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        r = backup.run_backup(self.root, "")
        self.assertTrue(r["ok"], r["msg"])
        return r

    def test_nested_repo_without_commit_is_left_out(self):
        empty = self.root / "Proj" / "task1" / "scratch" / "EnsembleProjects"
        empty.mkdir(parents=True)
        self.assertEqual(_git(empty, "init").returncode, 0)
        (empty / "x.txt").write_text("x", encoding="utf-8")
        r = self._run()
        self.assertTrue(r["committed"])
        names = "\n".join(_tracked(self.root))
        self.assertNotIn("scratch", names)
        self.assertIn("Proj/task1/notes.md", names)
        self.assertIn("/Proj/task1/scratch/EnsembleProjects/",
                      (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8"))

    def test_nested_repo_with_commits_gives_no_gitlink(self):
        inner = self.root / "Proj" / "task2" / "clone"
        inner.mkdir(parents=True)
        self.assertEqual(_git(inner, "init").returncode, 0)
        (inner / "a.txt").write_text("a", encoding="utf-8")
        _git(inner, "add", "-A")
        self.assertEqual(_git(inner, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c").returncode, 0)
        self._run()
        for line in _tracked(self.root):
            self.assertFalse(line.startswith("160000"), line)
            self.assertNotIn("clone", line)

    def test_agent_state_under_a_task_folder_is_not_tracked(self):
        home = self.root / "Proj" / "task1" / "scratch-hub"
        (home / ".claude" / "projects").mkdir(parents=True)
        (home / ".claude" / "projects" / "s.jsonl").write_text("{}", encoding="utf-8")
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
        (home / ".ensemble").mkdir()
        (home / ".ensemble" / "rooms.json").write_text("{}", encoding="utf-8")
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".local" / "bin" / "claude.exe").write_text("", encoding="utf-8")
        (home / "AppData" / "Local").mkdir(parents=True)
        (home / "AppData" / "Local" / "x.db").write_text("", encoding="utf-8")
        (home / "keep.txt").write_text("keep", encoding="utf-8")
        self._run()
        names = "\n".join(_tracked(self.root))
        for bad in (".claude", ".codex", ".ensemble", "AppData", ".local"):
            self.assertNotIn(bad, names)
        self.assertIn("scratch-hub/keep.txt", names)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_longpaths_enabled_on_windows(self):
        self._run()
        self.assertEqual((_git(self.root, "config", "core.longpaths").stdout or "").strip(), "true")

    def test_root_itself_is_not_nested(self):
        self._run()
        self.assertEqual(backup.nested_repos(self.root), [])
        (self.root / "Proj" / "t" / "r").mkdir(parents=True)
        _git(self.root / "Proj" / "t" / "r", "init")
        self.assertEqual(backup.nested_repos(self.root), ["Proj/t/r"])

    def test_files_over_the_cap_are_left_out_and_counted(self):
        big = self.root / "Proj" / "task [1] #x" / "capture.cq4"
        big.parent.mkdir(parents=True)
        with open(big, "wb") as fh:
            fh.truncate(2 * 1024 * 1024)
        (big.parent / "small.txt").write_text("s", encoding="utf-8")
        nested, oversize = backup.scan(self.root, max_file_mb=1)
        self.assertEqual(oversize, ["Proj/task [1] #x/capture.cq4"])
        self.assertEqual(backup._exclude_pattern(oversize[0]), "/Proj/task \\[1\\] \\#x/capture.cq4")
        old = backup.MAX_FILE_MB
        backup.MAX_FILE_MB = 1
        try:
            r = self._run()
        finally:
            backup.MAX_FILE_MB = old
        self.assertEqual(r["skipped"], 1)
        self.assertIn("1 nested repo(s) or file(s) over 1 MB left out", r["msg"])
        names = "\n".join(_tracked(self.root))
        self.assertNotIn("capture.cq4", names)
        self.assertIn("small.txt", names)

    def test_walk_skips_excluded_folders(self):
        deep = self.root / "Proj" / "task1" / ".history"
        deep.mkdir()
        _git(deep, "init")
        self.assertEqual(backup.nested_repos(self.root), [])


if __name__ == "__main__":
    unittest.main()
