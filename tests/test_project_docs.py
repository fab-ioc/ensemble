"""A project's Documents folder, its list, the migration, and what landed on main.

* the folder's name: ``Documents`` for a code project (an existing one reused
  unless it is a task's folder), a name not there yet for a documents project;
  chosen once and kept in project.json; never used as a new task's folder;
* exposed as ``documentsDir`` to agents (ensemble_get_task's project);
* the list: newest first, pictures left out, the task a ``#12 Title.md`` or a
  ``#12 Title/`` folder names, the code's docs/ Markdown marked inCode;
* the migration (tools/migrate_reports.py): copies only, keeps times, leaves
  working notes, checkouts, repositories, task folders and browser profiles,
  never overwrites, and a second run copies nothing;
* git: a commit's task number from its message, its branch or its title; the
  main line's log with each commit's files; one commit's diff of a file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import ensemble_tools
import project_docs

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")


def write(p: Path, text: str = "x", mtime: int | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def task(home: Path, name: str, no: int, title: str) -> Path:
    d = home / name
    write(d / "task.json", json.dumps({"no": no, "title": title}))
    return d


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class TheName(Tmp):
    def test_a_code_project_takes_documents_and_reuses_its_own(self):
        self.assertEqual(project_docs.choose_name(str(self.tmp), False), "Documents")
        (self.tmp / "Documents").mkdir()
        self.assertEqual(project_docs.choose_name(str(self.tmp), False), "Documents")

    def test_a_task_folder_named_documents_is_not_taken(self):
        task(self.tmp, "Documents", 4, "Documents")
        self.assertEqual(project_docs.choose_name(str(self.tmp), False), "Task documents")

    def test_a_documents_project_never_takes_a_name_already_there(self):
        (self.tmp / "Documents").mkdir()
        self.assertEqual(project_docs.choose_name(str(self.tmp), True), "Task documents")
        (self.tmp / "Task documents").mkdir()
        self.assertEqual(project_docs.choose_name(str(self.tmp), True), "Ensemble documents")

    def test_valid_names(self):
        for ok in ("Documents", "Task documents"):
            self.assertTrue(project_docs.valid_name(ok), ok)
        for bad in ("", "..", ".hidden", "a/b", "a\\b", "c:x"):
            self.assertFalse(project_docs.valid_name(bad), bad)

    def test_the_hub_keeps_the_name_in_project_json(self):
        pj = {"id": "p1", "kind": "code"}
        with mock.patch.object(dashboard, "project_home", return_value=str(self.tmp)), \
             mock.patch.object(dashboard, "_set_project_meta", return_value=(True, "")) as meta:
            got = dashboard.project_documents_dir(pj)
        self.assertEqual(got, os.path.join(str(self.tmp), "Documents"))
        meta.assert_called_once_with("p1", "documentsDir", "Documents")
        self.assertEqual(pj["documentsDir"], "Documents")
        # Once kept, it is used as it is, even if the folder is now a task's.
        task(self.tmp, "Documents", 3, "x")
        with mock.patch.object(dashboard, "project_home", return_value=str(self.tmp)), \
             mock.patch.object(dashboard, "_set_project_meta") as meta2:
            self.assertEqual(dashboard.project_documents_dir(pj), os.path.join(str(self.tmp), "Documents"))
        meta2.assert_not_called()

    def test_a_home_not_made_yet_is_not_written(self):
        pj = {"id": "p1", "kind": "code"}
        with mock.patch.object(dashboard, "project_home", return_value=str(self.tmp / "nope")), \
             mock.patch.object(dashboard, "_set_project_meta") as meta:
            dashboard.project_documents_dir(pj)
        meta.assert_not_called()

    def test_a_new_task_folder_is_never_the_documents_folder(self):
        pj = {"id": "p1", "kind": "code", "documentsDir": "Documents"}
        with mock.patch.object(dashboard, "project_home", return_value=str(self.tmp)):
            self.assertEqual(dashboard._task_dir_for(pj, "Documents").name, "documents-2")
            self.assertEqual(dashboard._task_dir_for(pj, "Fix the list").name, "fix_the_list")

    def test_agents_are_told_where_it_is(self):
        pj = {"id": "p1", "name": "P", "path": "C:\\code", "kind": "code", "documentsDir": "Documents"}
        with mock.patch.object(dashboard, "project_home", return_value=str(self.tmp)):
            view = ensemble_tools._project_view(pj)
        self.assertEqual(view["documentsDir"], os.path.join(str(self.tmp), "Documents"))


class TheList(Tmp):
    def test_newest_first_with_task_numbers_and_the_codes_docs(self):
        docs, code = self.tmp / "Documents", self.tmp / "code" / "docs"
        write(docs / "#12 Old report.md", mtime=1_000)
        write(docs / "#40 Design" / "notes.md", mtime=3_000)
        write(docs / "#40 Design" / "shot.png", mtime=4_000)
        write(docs / "Loose note.md", mtime=2_000)
        write(code / "arch.md", mtime=2_500)
        write(code / "script.py", mtime=5_000)
        out = project_docs.list_documents(str(docs), str(code))
        rows = [(i["rel"], i["title"], i["no"], i["group"], i["inCode"]) for i in out["items"]]
        self.assertEqual(rows, [
            ("#40 Design/notes.md", "notes", 40, "Design", False),
            ("docs/arch.md", "arch", None, "", True),
            ("Loose note.md", "Loose note", None, "", False),
            ("#12 Old report.md", "Old report", 12, "", False),
        ])
        self.assertTrue(out["exists"])
        self.assertEqual(out["codeDocs"], str(code))
        self.assertFalse(out["truncated"])

    def test_no_folder_yet(self):
        out = project_docs.list_documents(str(self.tmp / "Documents"), "")
        self.assertEqual((out["exists"], out["items"], out["codeDocs"]), (False, [], ""))

    def test_the_list_is_capped(self):
        for i in range(5):
            write(self.tmp / f"n{i}.md", mtime=1_000 + i)
        out = project_docs.list_documents(str(self.tmp), "", limit=3)
        self.assertEqual([i["rel"] for i in out["items"]], ["n4.md", "n3.md", "n2.md"])
        self.assertTrue(out["truncated"])

    def test_titles(self):
        self.assertEqual(project_docs.doc_title("#12 Short title.md"), (12, "Short title"))
        self.assertEqual(project_docs.doc_title("#7.md"), (7, "#7"))
        self.assertEqual(project_docs.doc_title("plain.md"), (None, "plain"))
        self.assertEqual(project_docs.doc_title("#40 Design"), (40, "Design"))


class TheMigration(Tmp):
    def setUp(self):
        super().setUp()
        h = self.home = self.tmp / "home"
        t = task(h, "fix-it", 7, "Fix it: now")
        write(t / "REPORT.md", "report ![shot](shots/linked.png) ![web](https://x/y.png)", mtime=1_500)
        write(t / "TASK-HANDOVER.md")
        write(t / "REVIEW-LOG.md")
        write(t / "PO-notes.md")
        write(t / "mockup.html")
        write(t / "shots" / "a.png", "png", mtime=1_600)
        write(t / "shots" / "linked.png", "png")
        write(t / "samples" / "fixture.py")
        write(t / "evidence" / "notes.md", "notes ![p](pic.png)")
        write(t / "evidence" / "pic.png", "png")
        write(t / "evidence" / "data.json", "{}")
        write(t / "repo" / "README.md")
        write(t / "claude" / "x.md")
        write(t / "clone" / ".git" / "HEAD")
        write(t / "clone" / "a.md")
        write(t / "chrome-profile" / "Local State")
        task(t, "inner", 8, "Inner")
        write(h / "no-number" / "task.json", "{}")
        write(h / "no-number" / "R.md")
        self.docs = h / "Documents"

    def test_what_it_copies_and_leaves(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        dest = self.docs / "#7 Fix it now"
        self.assertEqual(sorted(i["dst"] for i in plan["copy"]),
                         sorted([str(dest / "REPORT.md"), str(dest / "shots" / "linked.png"),
                                 str(dest / "evidence" / "notes.md"), str(dest / "evidence" / "pic.png")]),
                         "a report folder's documents and pictures, and a picture a report links to")
        why = {Path(s["path"]).name: s["why"] for s in plan["skipped"]}
        for name in ("TASK-HANDOVER.md", "REVIEW-LOG.md", "PO-notes.md"):
            self.assertIn("working notes", why[name])
        self.assertEqual(why["repo"], "a checkout or an agent's folder")
        self.assertEqual(why["claude"], "a checkout or an agent's folder")
        self.assertEqual(why["clone"], "a git repository")
        self.assertEqual(why["chrome-profile"], "a browser profile")
        self.assertEqual(why["inner"], "a task's folder")
        for name in ("shots", "samples"):
            self.assertEqual(why[name], "no document in it (screenshots, samples, data)", name)
        self.assertFalse(self.docs.exists(), "a plan writes nothing")

    def test_copies_keep_times_move_nothing_and_run_again_copies_nothing(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        done = project_docs.apply_migration(plan)
        self.assertEqual(len(done), 4)
        rep = self.docs / "#7 Fix it now" / "REPORT.md"
        self.assertTrue(rep.read_text(encoding="utf-8").startswith("report "))
        self.assertEqual(int(rep.stat().st_mtime), 1_500)
        self.assertTrue((self.home / "fix-it" / "REPORT.md").is_file(), "copied, not moved")
        again = project_docs.plan_migration(str(self.home), str(self.docs))
        self.assertEqual((len(again["copy"]), len(again["same"])), (0, 4))
        self.assertEqual(project_docs.apply_migration(again), [])

    def test_a_file_there_with_other_content_is_left_alone(self):
        write(self.docs / "#7 Fix it now" / "REPORT.md", "mine, edited")
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        self.assertEqual([Path(i["dst"]).name for i in plan["differs"]], ["REPORT.md"])
        project_docs.apply_migration(plan)
        self.assertEqual((self.docs / "#7 Fix it now" / "REPORT.md").read_text(encoding="utf-8"), "mine, edited")

    def test_same_size_and_time_but_other_bytes_differs(self):
        src = self.home / "fix-it" / "REPORT.md"
        st = src.stat()
        dst = write(self.docs / "#7 Fix it now" / "REPORT.md", "x" * st.st_size, mtime=int(st.st_mtime))
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        self.assertIn(str(dst), [i["dst"] for i in plan["differs"]])
        self.assertNotIn(str(dst), [i["dst"] for i in plan["same"]])

    def test_a_file_that_appears_just_before_the_copy_is_not_overwritten(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        target = Path(next(i["dst"] for i in plan["copy"] if i["dst"].endswith("REPORT.md")))
        real_mkdir = Path.mkdir

        def racing_mkdir(self_, *a, **k):
            real_mkdir(self_, *a, **k)
            if self_ == target.parent and not target.exists():
                target.write_text("someone else's", encoding="utf-8")
        with mock.patch.object(Path, "mkdir", racing_mkdir):
            done = project_docs.apply_migration(plan)
        self.assertEqual(target.read_text(encoding="utf-8"), "someone else's")
        self.assertNotIn(str(target), [i["dst"] for i in done])

    def test_a_copy_whose_times_fail_is_removed_and_a_second_run_finishes_it(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        with mock.patch.object(project_docs.shutil, "copystat", side_effect=OSError("no times")):
            with self.assertRaises(OSError):
                project_docs.apply_migration(plan)
        first = Path(plan["copy"][0]["dst"])
        self.assertFalse(first.exists(), "no copy is left without its times")
        again = project_docs.plan_migration(str(self.home), str(self.docs))
        self.assertIn(str(first), [i["dst"] for i in again["copy"]])
        project_docs.apply_migration(again)
        src = Path(plan["copy"][0]["src"])
        self.assertEqual(int(first.stat().st_mtime), int(src.stat().st_mtime))

    def test_a_failed_copy_never_removes_a_file_someone_else_put_there(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        target = Path(plan["copy"][0]["dst"])

        def theirs_then_fail(src, dst, *a, **k):
            target.write_text("someone else's", encoding="utf-8")
            raise OSError("no times")
        with mock.patch.object(project_docs.shutil, "copystat", side_effect=theirs_then_fail):
            with self.assertRaises(OSError):
                project_docs.apply_migration(plan)
        self.assertEqual(target.read_text(encoding="utf-8"), "someone else's")
        self.assertEqual([p.name for p in target.parent.iterdir()], [target.name], "no hidden copy left")

    def test_a_file_that_appears_while_copying_is_kept_and_nothing_is_left_behind(self):
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        target = Path(plan["copy"][0]["dst"])
        real = shutil.copystat

        def theirs_meanwhile(src, dst, *a, **k):
            if not target.exists():
                target.write_text("someone else's", encoding="utf-8")
            return real(src, dst, *a, **k)
        with mock.patch.object(project_docs.shutil, "copystat", side_effect=theirs_meanwhile):
            done = project_docs.apply_migration(plan)
        self.assertEqual(target.read_text(encoding="utf-8"), "someone else's")
        self.assertNotIn(str(target), [i["dst"] for i in done])
        self.assertEqual(len(done), len(plan["copy"]) - 1)
        leftovers = [p for p in self.docs.rglob(".~migrate-*")]
        self.assertEqual(leftovers, [])

    def test_picture_paths_the_viewer_renders_are_followed(self):
        t = self.home / "fix-it"
        write(t / "gallery" / "LOOK.md", "![a](shots/foo_(1).png) ![b](<shots/b.png> 'B') "
                                         "![c](shots/c.png \"C\")")
        for n in ("foo_(1).png", "b.png", "c.png"):
            write(t / "gallery" / "shots" / n, "png")
        write(t / "gallery" / "shots" / "not-linked.png", "png")
        plan = project_docs.plan_migration(str(self.home), str(self.docs))
        got = {Path(i["dst"]).name for i in plan["copy"] if "gallery" in i["dst"]}
        self.assertEqual(got, {"LOOK.md", "foo_(1).png", "b.png", "c.png", "not-linked.png"})
        linked = {p.name for p in project_docs._linked_media(t / "gallery" / "LOOK.md", t)}
        self.assertEqual(linked, {"foo_(1).png", "b.png", "c.png"})

    def test_the_tool_is_a_dry_run_unless_told(self):
        write(self.home / "project.json", json.dumps({"id": "p-x", "name": "X"}))
        tool = str(ROOT / "tools" / "migrate_reports.py")
        dry = subprocess.run(["py" if shutil.which("py") else "python", tool, "X", "--root", str(self.tmp)],
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(self.docs.exists())
        self.assertNotIn("documentsDir", json.loads((self.home / "project.json").read_text(encoding="utf-8")))
        real = subprocess.run(["py" if shutil.which("py") else "python", tool, "X", "--root", str(self.tmp), "--apply"],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(real.returncode, 0, real.stderr)
        self.assertTrue((self.docs / "#7 Fix it now" / "REPORT.md").is_file())
        self.assertEqual(json.loads((self.home / "project.json").read_text(encoding="utf-8"))["documentsDir"], "Documents")


class TheTaskOfACommit(unittest.TestCase):
    IDX = {"branch": {"sess/fix": 12}, "title": {"make it faster": 30}}

    def test_from_its_message_its_branch_or_its_title(self):
        n = dashboard.commit_task_no
        self.assertEqual(n("Merge #90: hand an owner over", self.IDX), 90)
        self.assertEqual(n("Fix the list (#41)", self.IDX), 41)
        self.assertEqual(n("Merge branch 'sess/fix'", self.IDX), 12)
        self.assertEqual(n("Make it faster", self.IDX), 30)
        self.assertIsNone(n("Tidy up", self.IDX))

    def test_edge_cases(self):
        n = dashboard.commit_task_no
        self.assertEqual(n("#91: the Documents list", self.IDX), 91)
        self.assertEqual(n("Merge remote-tracking branch 'origin/sess/fix'", self.IDX), 12)
        self.assertEqual(n("Merge pull request #999 from org/sess/fix", self.IDX), 12,
                         "a pull request's number is not the task's; its branch is")
        self.assertIsNone(n("Merge pull request #999 from org/other", self.IDX))
        self.assertIsNone(n("Bump the version to 2 #3", self.IDX), "a stray #N is not a task")
        self.assertIsNone(n("Fix (#41) in the middle", self.IDX))
        self.assertIsNone(n("", self.IDX))


@unittest.skipUnless(GIT, "git is not installed")
class TheLog(Tmp):
    def git(self, *a):
        subprocess.run(["git", "-C", str(self.repo), *a], check=True, capture_output=True, text=True, encoding="utf-8")

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "r"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        write(self.repo / "a.txt", "one\n")
        self.git("add", "."); self.git("commit", "-qm", "First")
        self.git("checkout", "-qb", "sess/x")
        write(self.repo / "b.txt", "two\n")
        self.git("add", "."); self.git("commit", "-qm", "On the branch")
        write(self.repo / "a.txt", "one\nmore\n")
        self.git("commit", "-qam", "Again")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "sess/x", "-m", "Merge #5: the thing")
        self.ok = mock.patch.object(dashboard, "workspace_access_ok", return_value=True)
        self.ok.start()
        self.addCleanup(self.ok.stop)

    def test_the_main_line_newest_first_a_merge_as_one_with_its_files(self):
        code, out = dashboard.git_log(str(self.repo), 10)
        self.assertEqual(code, 200)
        self.assertEqual(out["ref"], "main")
        got = [(c["subject"], c["no"], sorted(f["path"] for f in c["files"])) for c in out["commits"]]
        self.assertEqual(got, [("Merge #5: the thing", 5, ["a.txt", "b.txt"]), ("First", None, ["a.txt"])])
        self.assertEqual({f["path"]: f["status"] for f in out["commits"][0]["files"]}, {"a.txt": "M", "b.txt": "A"})

    def test_one_commits_diff_of_a_file(self):
        _, log = dashboard.git_log(str(self.repo), 10)
        sha = log["commits"][0]["sha"]
        code, d = dashboard.git_diff(str(self.repo), "a.txt", commit=sha)
        self.assertEqual(code, 200)
        self.assertIn("+more", d["diff"])
        self.assertNotIn("b.txt", d["diff"])
        self.assertEqual(dashboard.git_diff(str(self.repo), "a.txt", commit="nope")[0], 400)

    def test_not_a_repository(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out = dashboard.git_log(str(plain), 10)
        self.assertEqual((code, out["commits"]), (200, []))


if __name__ == "__main__":
    unittest.main()
