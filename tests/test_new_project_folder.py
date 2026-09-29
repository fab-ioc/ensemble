"""#131: a new code project with a name and no folder.

* project_folder_name: the name, words joined by "-", without what a Windows
  or macOS folder name cannot hold, never empty.
* new_code_folder: that name in the projects root, -2, -3… past another
  project's folder.
* register_project(init_git=True): a code folder it creates is a git
  repository with one empty commit, kept apart from the project's home so its
  tasks can be worktrees; a folder that was there is never touched.
* Every way New project creates one (/api/projects/new, a fresh PO, a past
  conversation made its PO) takes an empty folder the same way.
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import dashboard
from tests.test_make_po import Hub


def git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


def commits(folder) -> list[str]:
    out = git(folder, "log", "--format=%s")
    return out.stdout.split("\n")[:-1] if out.returncode == 0 else []


class TheFolderName(unittest.TestCase):
    def test_the_rule(self):
        for name, folder in [
            ("My Day Job", "My-Day-Job"),
            ("  my   day\tjob ", "my-day-job"),        # the case as typed, spaces of any kind and length
            ("A - B", "A-B"),
            ("a--b", "a--b"),                           # dashes typed are kept
            ('Q3: plans/ideas? <draft> "x" |*', "Q3-plansideas-draft-x"),
            ("back\\slash", "backslash"),
            ("Café Ünïcode", "Café-Ünïcode"),
            ("trailing dots...", "trailing-dots"),
            (".hidden", "hidden"),
            ("-dash-", "dash"),
            ("", "project"),
            ("   ", "project"),
            ("???", "project"),
            ("..", "project"),
            ("CON", "CON-project"),
            ("lpt1", "lpt1-project"),
            ("Console", "Console"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(dashboard.project_folder_name(name), folder)

    def test_a_long_name_is_cut(self):
        got = dashboard.project_folder_name("word " * 40)
        self.assertLessEqual(len(got), dashboard.PROJECT_FOLDER_MAX)
        self.assertFalse(got.endswith("-"))

    def test_every_result_is_a_folder_name(self):
        for name in ("a b", "x:y", "tab\there", "new\nline", "CON", "", "é"):
            f = dashboard.project_folder_name(name)
            self.assertTrue(f)
            self.assertIsNone(dashboard._FOLDER_BAD_CHARS.search(f), f)
            self.assertNotIn(" ", f)


class TheFolder(Hub):
    def folder(self, name):
        return dashboard.new_code_folder(name)

    def test_in_the_projects_root(self):
        self.assertEqual(self.folder("My Day Job"), str(self.root / "My-Day-Job"))

    def test_past_another_projects_folder(self):
        ok, first, _ = dashboard.register_project(str(self.root / "My-Day-Job"), "Old job")
        self.assertTrue(ok)
        self.assertEqual(self.folder("My Day Job"), str(self.root / "My-Day-Job-2"))
        (self.root / "My-Day-Job-2").mkdir()
        (self.root / "My-Day-Job-2" / "project.json").write_text("{}")     # another hub's project
        self.assertEqual(self.folder("My Day Job"), str(self.root / "My-Day-Job-3"))

    def test_past_another_projects_home(self):
        # A project whose code is elsewhere keeps its tasks in <root>/<its
        # name>, made on demand: taken even before it is there.
        ok, ext, _ = dashboard.register_project(str(self.work), "Engine")
        self.assertEqual(self.folder("Engine"), str(self.root / "Engine-2"))

    def test_past_a_file(self):
        (self.root / "Notes").write_text("x")
        self.assertEqual(self.folder("Notes"), str(self.root / "Notes-2"))

    def test_past_another_projects_folder_in_another_case(self):
        # Compared without case on every system (a macOS disk too, where
        # normcase does not): a project on MY-DAY-JOB, not on disk yet.
        other = {"id": "proj-x", "name": "Old", "path": str(self.root / "MY-DAY-JOB"), "home": str(self.base / "away")}
        self.assertEqual(dashboard.new_code_folder("my day job", [other]), str(self.root / "my-day-job-2"))

    def test_a_folder_that_is_no_projects_is_used(self):
        (self.root / "Scratch").mkdir()
        self.assertEqual(self.folder("Scratch"), str(self.root / "Scratch"))


class GitInit(Hub):
    def test_a_new_folder_is_a_repository_with_one_empty_commit(self):
        folder = self.root / "Fresh"
        ok, proj, _ = dashboard.register_project(str(folder), "Fresh", init_git=True)
        self.assertTrue(ok)
        self.assertTrue(proj["isGit"])
        self.assertEqual(commits(folder), ["Initial commit"])
        self.assertEqual(git(folder, "ls-tree", "-r", "HEAD").stdout, "", "the commit is empty")
        self.assertEqual(git(folder, "status", "--porcelain").stdout, "", "nothing of the project's in it")
        # Kept apart from its home, so a task can be a worktree of it.
        found = dashboard.find_project(proj["id"])
        self.assertTrue(dashboard._home_apart(found))
        ok, base, meta, msg = dashboard.setup_session_workspace(found, "worktree", "First task", "FR", 1)
        self.assertTrue(ok, msg)
        self.assertEqual(meta["mode"], "worktree")
        self.assertTrue((Path(base) / ".git").exists())

    def test_without_a_git_identity(self):
        folder = self.root / "Anon"
        real = dashboard._run

        def run(argv, **kw):
            # This machine's git has no user.name: a plain commit fails.
            if "commit" in argv and "user.name=Ensemble" not in argv:
                return subprocess.CompletedProcess(argv, 128, "", "Please tell me who you are.")
            return real(argv, **kw)
        with mock.patch.object(dashboard, "_run", run):
            ok, proj, _ = dashboard.register_project(str(folder), "Anon", init_git=True)
        self.assertTrue(ok)
        self.assertEqual(commits(folder), ["Initial commit"])
        self.assertEqual(git(folder, "log", "--format=%an").stdout.strip(), "Ensemble")

    def test_never_a_folder_that_was_there(self):
        for name, fill in (("Empty", False), ("Full", True)):
            with self.subTest(name=name):
                folder = self.root / name
                folder.mkdir()
                if fill:
                    (folder / "a.txt").write_text("x")
                ok, proj, _ = dashboard.register_project(str(folder), name, init_git=True)
                self.assertTrue(ok)
                self.assertFalse((folder / ".git").exists())

    def test_never_outside_the_request(self):
        # Without init_git (every other caller) a new folder is as before.
        ok, proj, _ = dashboard.register_project("Motors")
        self.assertFalse((self.root / "Motors" / ".git").exists())
        self.assertFalse(dashboard._home_apart(dashboard.find_project(proj["id"])))

    def test_never_a_documents_folder(self):
        ok, proj, _ = dashboard.register_project(str(self.root / "Letters"), "Letters", "documents", init_git=True)
        self.assertTrue(ok)
        self.assertFalse((self.root / "Letters" / ".git").exists())

    def test_a_failed_commit_leaves_no_repository(self):
        # A signing setup or a failing hook: git init worked, no commit could be made.
        real = dashboard._run

        def run(argv, **kw):
            if "commit" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "gpg failed to sign the data")
            return real(argv, **kw)
        folder = self.root / "Signed"
        with mock.patch.object(dashboard, "_run", run):
            ok, proj, _ = dashboard.register_project(str(folder), "Signed", init_git=True)
        self.assertTrue(ok)
        self.assertFalse((folder / ".git").exists(), "no half-made repository")
        self.assertFalse(proj["isGit"])
        self.assertFalse(dashboard._home_apart(dashboard.find_project(proj["id"])), "as a plain new folder")

    def test_a_failed_git_still_registers(self):
        def run(argv, **kw):
            raise FileNotFoundError("git")
        with mock.patch.object(dashboard, "_run", run):
            ok, proj, _ = dashboard.register_project(str(self.root / "Nogit"), "Nogit", init_git=True)
        self.assertTrue(ok)
        self.assertFalse(proj["isGit"])


class EveryEndpoint(Hub):
    """An empty folder on each request New project sends."""

    def made(self, out, folder="My-Day-Job"):
        path = self.root / folder
        self.assertEqual(os.path.normcase(out["project"]["path"]), os.path.normcase(str(path)))
        self.assertEqual(out["project"]["name"], "My Day Job")
        self.assertEqual(commits(path), ["Initial commit"])
        return path

    def test_no_po(self):
        status, out = self.call_url("/api/projects/new", {"name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 200, out)
        self.made(out)

    def test_no_po_and_no_path_key(self):
        status, out = self.call_url("/api/projects/new", {"name": "My Day Job", "kind": "code"})
        self.assertEqual(status, 200, out)
        self.made(out)

    def test_a_fresh_po(self):
        status, out = self.call({"fresh": "claude", "name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 200, out)
        self.made(out)

    def test_a_past_conversation(self):
        status, out = self.call({**self.session(), "name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 200, out)
        self.made(out)

    def test_a_past_conversation_without_a_path_keeps_its_folder(self):
        # As before: a request with no "path" at all means the conversation's folder.
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code"})
        self.assertEqual(status, 200, out)
        self.assertEqual(os.path.normcase(out["project"]["path"]), os.path.normcase(str(self.work)))
        self.assertFalse((self.work / ".git").exists())

    def test_twice_the_same_name(self):
        status, out = self.call_url("/api/projects/new", {"name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 200, out)
        status, out = self.call({"fresh": "claude", "name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 200, out)
        self.made(out, "My-Day-Job-2")

    def test_a_typed_folder_wins(self):
        code = self.base / "work" / "mine"
        status, out = self.call_url("/api/projects/new", {"name": "My Day Job", "kind": "code", "path": str(code)})
        self.assertEqual(status, 200, out)
        self.assertEqual(os.path.normcase(out["project"]["path"]), os.path.normcase(str(code)))
        self.assertFalse((self.root / "My-Day-Job").exists())
        self.assertEqual(commits(code), ["Initial commit"], "a typed folder the hub makes is a repository too")

    def test_a_typed_folder_in_the_projects_root(self):
        code = self.root / "Typed"
        status, out = self.call({"fresh": "claude", "name": "Typed", "kind": "code", "path": str(code)})
        self.assertEqual(status, 200, out)
        self.assertEqual(commits(code), ["Initial commit"])
        proj = dashboard.find_project(out["project"]["id"])
        self.assertTrue(dashboard._home_apart(proj), "its tasks can be worktrees")

    def test_a_typed_folder_that_was_there_is_untouched(self):
        status, out = self.call_url("/api/projects/new", {"name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)
        self.assertFalse((self.work / ".git").exists())

    def test_a_failed_start_takes_the_new_folder_away(self):
        self.start_error = dashboard.StartRoomError("claude could not be started")
        status, out = self.call({"fresh": "claude", "name": "My Day Job", "kind": "code", "path": ""})
        self.assertEqual(status, 400, out)
        self.nothing_left()
        self.assertEqual(os.listdir(self.root), [])

    def test_documents_keep_their_name(self):
        # As before #131: a documents project's folder is its name, spaces and all.
        status, out = self.call_url("/api/projects/new", {"name": "My Letters", "kind": "documents", "path": "My Letters"})
        self.assertEqual(status, 200, out)
        self.assertTrue((self.root / "My Letters").is_dir())
        self.assertFalse((self.root / "My Letters" / ".git").exists())

    def test_the_dialogs_preview(self):
        info = dashboard.po_setup_folder("", "code", "My Day Job")
        self.assertEqual((info["folder"], info["derived"], info["exists"]), (str(self.root / "My-Day-Job"), True, False))
        self.assertFalse(info["relative"])
        self.assertEqual(dashboard.po_setup_folder("", "code", "")["folder"], "")
        typed = dashboard.po_setup_folder(str(self.work), "code", "My Day Job")
        self.assertFalse(typed["derived"])
        self.assertEqual(typed["folder"], str(self.work))


if __name__ == "__main__":
    unittest.main()
