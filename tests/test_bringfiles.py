"""A session's files brought into a documents project: the walk and the copy.

What is left out (the backup's and the history's excludes, agent state, links
and junctions, nested repositories, task folders, the hub's own records), what
is never overwritten, what a copy takes back, and paths Windows cannot open.
The request itself is tested in test_make_po.py.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backup  # noqa: E402
import bringfiles  # noqa: E402
import history  # noqa: E402


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.src, self.home, self.away = base / "src", base / "home", base / "away"
        for d in (self.src, self.home, self.away):
            d.mkdir()

    def put(self, folder, rel, text="x"):
        f = folder / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f

    def found(self):
        got = bringfiles.scan(str(self.src))
        return sorted(rel for rel, _ in got["files"]), got

    def tree(self, folder):
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


class WhatIsLeftOut(Case):
    def test_everything_the_backup_and_the_history_never_keep(self):
        self.put(self.src, "keep.md")
        for line in [*backup.GITIGNORE.splitlines(), *backup.LOCAL_EXCLUDES]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.replace("**/", "").strip("/").replace("*", "x")
            self.put(self.src, f"sub/{name}/inside.txt" if line.endswith("/") else f"sub/{name}")
        names = sorted({".idea", ".claude", ".codex", ".ensemble", ".history", "node_modules", "__pycache__",
                        "AppData", *backup._NO_WALK} - {".git"})        # a folder holding .git goes whole
        for name in names:
            self.put(self.src, f"deep/{name}/inside.txt")
        for name in ("desktop.ini", "~$report.docx", ".~lock.sheet.ods#", "upload.tmp"):
            self.assertTrue(history.not_kept(name))
            self.put(self.src, f"deep/{name}")
        files, got = self.found()
        self.assertEqual(files, ["keep.md"], files)
        self.assertEqual(got["leftOut"], 13 + len(names) + 4)

    def test_a_folder_name_in_another_case(self):
        self.put(self.src, "Node_Modules/x.js")
        self.put(self.src, "APPDATA/y.txt")
        self.assertEqual(self.found()[0], [])

    def test_a_nested_repository_and_a_task_folder(self):
        self.put(self.src, "tool/.git/HEAD")
        self.put(self.src, "tool/main.py")
        self.put(self.src, "worktree/.git", "gitdir: elsewhere")       # a worktree's .git is a file
        self.put(self.src, "worktree/main.py")
        self.put(self.src, "a task/task.json", "{}")
        self.put(self.src, "a task/notes.md")
        self.put(self.src, "mine/notes.md")
        self.assertEqual(self.found()[0], ["mine/notes.md"])

    def test_the_folder_itself_may_be_a_repository(self):
        self.put(self.src, ".git/HEAD")
        self.put(self.src, "doc.md")
        self.assertEqual(self.found()[0], ["doc.md"])

    def test_a_tasks_own_folder_leaves_its_records(self):
        for rel in ("task.json", "chat.json", "claude.jsonl", ".wt-scheme", "project.json", "_linked/x.txt",
                    "notes.md", "data/rows.jsonl", "data/project.json"):
            self.put(self.src, rel, "{}")
        self.assertEqual(self.found()[0], ["data/project.json", "data/rows.jsonl", "notes.md"])
        (self.src / "task.json").unlink()
        self.assertIn("claude.jsonl", self.found()[0], "a .jsonl is a record only in a task's folder")

    def test_links_are_never_followed(self):
        self.put(self.away, "secret.txt")
        self.put(self.src, "doc.md")
        made = 0
        try:
            os.symlink(self.away, self.src / "linked", target_is_directory=True)
            os.symlink(self.away / "secret.txt", self.src / "linked.txt")
            made += 2
        except OSError:
            pass                        # Windows without the right to make symlinks
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.src / "junction"), str(self.away)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            made += 1 if r.returncode == 0 else 0
        if not made:
            self.skipTest("no link could be made here")
        files, got = self.found()
        self.assertEqual(files, ["doc.md"])
        self.assertEqual(got["leftOut"], made)

    @unittest.skipUnless(os.name == "nt", "Windows' 260 characters")
    def test_a_real_path_past_260_characters(self):
        self.put(self.src, "doc.md")
        deep = self.src / ("d" * 120) / ("e" * 120)
        os.makedirs("\\\\?\\" + str(deep))
        with open("\\\\?\\" + str(deep / "far.txt"), "w", encoding="utf-8") as fh:
            fh.write("far")
        self.addCleanup(lambda: subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", "\\\\?\\" + str(self.src / ("d" * 120))],
                                               capture_output=True))
        files, got = self.found()
        self.assertEqual(files, ["doc.md"])
        self.assertEqual(got["tooLong"], 1, "the folder that cannot be opened, counted once")
        res = bringfiles.bring(str(self.src), str(self.home), got["files"])
        self.assertEqual((res["copied"], self.tree(self.home)), (1, ["doc.md"]))


class TheCopy(Case):
    def bring(self, **kw):
        return bringfiles.bring(str(self.src), str(self.home), bringfiles.scan(str(self.src))["files"], **kw)

    def test_copied_with_their_times_and_the_source_left(self):
        f = self.put(self.src, "a/b/doc.md", "hello")
        os.utime(f, (1_700_000_000, 1_700_000_000))
        res = self.bring()
        self.assertEqual((res["copied"], res["bytes"]), (1, 5))
        self.assertEqual((self.home / "a/b/doc.md").read_text(encoding="utf-8"), "hello")
        self.assertEqual(int((self.home / "a/b/doc.md").stat().st_mtime), 1_700_000_000)
        self.assertTrue(f.is_file())

    def test_nothing_there_is_replaced(self):
        self.put(self.src, "doc.md", "theirs")
        self.put(self.src, "notes/a.txt", "theirs")
        self.put(self.src, "plan", "a file")
        self.put(self.src, "plans/q3.md", "theirs")
        self.put(self.home, "DOC.md" if os.name == "nt" else "doc.md", "mine")
        self.put(self.home, "plan/real.md", "mine")           # a folder where a file would go
        self.put(self.home, "plans", "mine")                  # a file where a folder would go
        res = self.bring()
        self.assertEqual((res["copied"], res["alreadyThere"]), (1, 3))
        self.assertEqual(self.tree(self.home), sorted(["DOC.md" if os.name == "nt" else "doc.md",
                                                       "notes/a.txt", "plan/real.md", "plans"]))
        self.assertEqual((self.home / "doc.md").read_text(encoding="utf-8"), "mine")

    def test_a_tasks_folder_of_the_same_name_is_not_written_into(self):
        self.put(self.src, "research/notes.md")
        self.put(self.home, "research/task.json", "{}")
        res = self.bring()
        self.assertEqual((res["copied"], res["alreadyThere"]), (0, 1))
        self.assertEqual(self.tree(self.home), ["research/task.json"])

    def test_a_link_in_the_project_is_not_written_through(self):
        self.put(self.src, "out/notes.md")
        try:
            os.symlink(self.away, self.home / "out", target_is_directory=True)
        except OSError:
            if os.name != "nt" or subprocess.run(["cmd", "/c", "mklink", "/J", str(self.home / "out"), str(self.away)],
                                                 capture_output=True).returncode != 0:
                self.skipTest("no link could be made here")
        res = self.bring()
        self.assertEqual((res["copied"], res["alreadyThere"]), (0, 1))
        self.assertEqual(self.tree(self.away), [])

    def test_a_file_that_cannot_be_read_is_skipped(self):
        self.put(self.src, "a.md")
        self.put(self.src, "b.md")
        real = open

        def fake(path, mode="r", *a, **k):
            if mode == "rb" and str(path).endswith("a.md"):
                raise PermissionError(13, "in use")
            return real(path, mode, *a, **k)

        with mock.patch.object(bringfiles, "open", fake, create=True):
            res = self.bring()
        self.assertEqual((res["copied"], res["unreadable"], self.tree(self.home)), (1, 1, ["b.md"]))

    def test_over_the_backups_cap_it_is_copied_and_named(self):
        self.put(self.src, "big.bin", "x" * 64)
        self.put(self.src, "small.md", "x")
        with mock.patch.object(bringfiles, "BACKUP_CAP", 32):
            res = self.bring()
            seen = bringfiles.preview(str(self.src))
        self.assertEqual(res["copied"], 2)
        self.assertEqual(res["notInBackup"], [{"path": "big.bin", "size": 64, "inHistory": True}])
        self.assertEqual((seen["offer"], seen["files"], seen["notInBackup"]), (True, 2, 1))

    def test_undo_takes_back_only_what_it_wrote(self):
        f = self.put(self.src, "new/deep/ro.md")
        os.chmod(f, stat.S_IREAD)
        self.addCleanup(os.chmod, f, stat.S_IWRITE | stat.S_IREAD)
        self.put(self.src, "was/added.md")
        self.put(self.home, "was/mine.md", "mine")
        res = self.bring()
        self.assertEqual(res["copied"], 2)
        self.put(self.home, "new/theirs.md", "written meanwhile")
        bringfiles.undo(res)
        self.assertEqual(self.tree(self.home), ["new/theirs.md", "was/mine.md"])
        self.assertFalse((self.home / "new" / "deep").exists(), "a folder it made, empty again, goes")

    def test_a_copy_out_of_time_is_taken_back(self):
        self.put(self.src, "a.md")
        self.put(self.src, "b.md")
        with self.assertRaises(bringfiles.BringFailed) as ctx:
            self.bring(seconds=-1)
        self.assertIn("took more than", str(ctx.exception))
        self.assertEqual(self.tree(self.home), [])

    def test_an_empty_folder_is_not_offered(self):
        self.put(self.src, ".git/HEAD")
        seen = bringfiles.preview(str(self.src))
        self.assertEqual(seen["offer"], False)
        self.assertIn("holds no files of its own", seen["reason"])
        gone = bringfiles.preview(str(self.src / "nope"))
        self.assertIn("no longer exists", gone["reason"])

    def test_the_project_inside_the_sessions_folder(self):
        inner = self.src / "projects" / "Strats"
        inner.mkdir(parents=True)
        self.assertIn("inside the session's folder", bringfiles.refusal(str(self.src), str(inner)))
        self.assertEqual(bringfiles.refusal(str(self.src), str(self.home), str(self.home.parent / "root")), "")


if __name__ == "__main__":
    unittest.main()
