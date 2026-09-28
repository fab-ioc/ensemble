"""Several projects on one code folder.

* registering: a second project on a folder that is already a project gets its
  own id, key and home; the same name again (any case, or a name that makes
  the same home) is the project that is there; no name is the folder's first
  project; a folder in the projects root keeps its own project.json;
* the New project dialog's note (po_setup_folder): every project on the folder,
  oldest first, as information;
* task branches: ``sess/<KEY>-<n>-<slug>``, so same-titled tasks of the two
  projects get different branches; a branch that is there already is never
  taken over (``-2``);
* a session with no link in a shared folder: its project is the oldest one,
  and one in a project's home is that project's.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402

GIT = shutil.which("git")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(cwd, *args) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                          "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                         creationflags=NO_WINDOW, check=True)
    return out.stdout.strip()


class Hub(unittest.TestCase):
    """A throwaway projects root, state dir and rooms dir, and a code folder
    outside the root."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        for p in (mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                  mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                  mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
                  mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(dashboard, "load_sessions", lambda *a, **k: [])):
            p.start()
            self.addCleanup(p.stop)
        self.code = base / "work" / "repo"
        self.code.mkdir(parents=True)

    def register(self, name, path=None, kind="code"):
        ok, proj, msg = dashboard.register_project(str(path or self.code), name, kind)
        self.assertTrue(ok, msg)
        return proj, msg


class Registering(Hub):

    def test_a_second_project_on_a_registered_folder(self):
        a, msg_a = self.register("Checkout")
        b, msg_b = self.register("Search")
        self.assertEqual((msg_a, msg_b), ("ok", "ok"))
        self.assertNotEqual(a["id"], b["id"])
        projects = dashboard.load_projects()
        self.assertEqual(sorted(p["name"] for p in projects), ["Checkout", "Search"])
        keys = dashboard.project_keys(projects)
        self.assertNotEqual(keys[a["id"]], keys[b["id"]])
        homes = {p["id"]: dashboard.project_home(p) for p in projects}
        self.assertEqual({os.path.basename(h) for h in homes.values()}, {"Checkout", "Search"},
                         "each home is named after its project, never after the code folder")
        # Both still found after their homes were made (the scan of the root).
        again = dashboard.load_projects()
        self.assertEqual(len(again), 2)
        for p in again:
            self.assertTrue(dashboard._same_folder(p["path"], str(self.code)))
            self.assertTrue(dashboard._same_folder(p["home"], homes[p["id"]]))

    def test_the_same_name_again_is_the_same_project(self):
        a, _ = self.register("Checkout")
        self.register("Search")
        for name in ("Checkout", "checkout", " CHECKOUT "):
            again, msg = self.register(name)
            self.assertEqual((again["id"], msg), (a["id"], "already registered"), name)
        # A name that makes the same home ("a:b" and "a_b" are both a_b).
        c, _ = self.register("a:b")
        again, msg = self.register("a_b")
        self.assertEqual((again["id"], msg), (c["id"], "already registered"))
        self.assertEqual(len(dashboard.load_projects()), 3)

    def test_no_name_is_the_folders_first_project(self):
        self.register("Checkout")
        self.register("Search")
        # The oldest; two registered in one second, the smaller id.
        first = min(dashboard.load_projects(), key=dashboard._registered_order)
        ok, proj, msg = dashboard.register_project(str(self.code))
        self.assertEqual((ok, proj["id"], msg), (True, first["id"], "already registered"))
        self.assertEqual(len(dashboard.load_projects()), 2)

    def test_a_folder_in_the_projects_root_keeps_its_project(self):
        a, _ = self.register("Motors", path=self.root / "Motors")
        mine = json.loads((self.root / "Motors" / "project.json").read_text(encoding="utf-8"))
        b, _ = self.register("Motors notes", path=self.root / "Motors")
        self.assertEqual(json.loads((self.root / "Motors" / "project.json").read_text(encoding="utf-8")), mine,
                         "the project whose home the folder is keeps its project.json")
        self.assertTrue(dashboard._same_folder(dashboard.project_home(a), str(self.root / "Motors")))
        self.assertTrue(dashboard._same_folder(dashboard.project_home(b), str(self.root / "Motors notes")))
        self.assertEqual(sorted(p["name"] for p in dashboard.load_projects()), ["Motors", "Motors notes"])

    def test_a_leftover_entry_for_a_folder_that_is_its_own_project(self):
        # projects.json naming a folder in the root that is another project's home.
        a, _ = self.register("Motors", path=self.root / "Motors")
        entries = json.loads(dashboard.PROJECTS_FILE.read_text(encoding="utf-8"))
        entries.append({"id": "proj-stale", "name": "Old", "path": str(self.root / "Motors")})
        dashboard.PROJECTS_FILE.write_text(json.dumps(entries), encoding="utf-8")
        self.assertEqual([p["id"] for p in dashboard.load_projects()], [a["id"]])

    def test_a_leftover_entry_for_an_external_project(self):
        # projects.json naming, under another id, an external project whose
        # home the root already has: by that home, or by its folder and name.
        a, _ = self.register("Checkout")
        b, _ = self.register("Search")
        home = dashboard.project_home(a)
        dashboard.project_home(b)
        entries = json.loads(dashboard.PROJECTS_FILE.read_text(encoding="utf-8"))
        entries += [{"id": "proj-stale1", "name": "Old", "path": str(self.code), "home": home},
                    {"id": "proj-stale2", "name": "checkout", "path": str(self.code)}]
        dashboard.PROJECTS_FILE.write_text(json.dumps(entries), encoding="utf-8")
        self.assertEqual(sorted(p["id"] for p in dashboard.load_projects()), sorted([a["id"], b["id"]]))


class TheFolderNote(Hub):

    def test_every_project_on_the_folder(self):
        self.assertEqual(dashboard.po_setup_folder(str(self.code), "code")["projects"], [])
        self.register("Checkout")
        self.register("Search")
        oldest_first = sorted(dashboard.load_projects(), key=dashboard._registered_order)
        f = dashboard.po_setup_folder(str(self.code), "code")
        self.assertEqual(f["projects"], [{"id": p["id"], "name": p["name"]} for p in oldest_first])
        self.assertEqual(sorted(p["name"] for p in f["projects"]), ["Checkout", "Search"])
        self.assertEqual(f["project"], f["projects"][0])


class TheProjectOfAFolder(Hub):

    def test_oldest_on_a_shared_folder_and_a_home_is_its_own(self):
        self.register("Checkout")
        self.register("Search")
        projects = dashboard.load_projects()
        for p in projects:
            dashboard.project_home(p)
        projects = dashboard.load_projects()
        first = min(projects, key=dashboard._registered_order)["id"]
        self.assertEqual(dashboard._project_for_cwd(str(self.code / "src"), projects), first)
        self.assertEqual(dashboard._project_for_cwd(str(self.code), list(reversed(projects))), first,
                         "the same answer whatever the order")
        for p in projects:
            inside = os.path.join(p["home"], "a_task", "repo")
            self.assertEqual(dashboard._project_for_cwd(inside, projects), p["id"])


class PastPoConversations(unittest.TestCase):
    """An unrecorded PO conversation in a shared folder: the PO of the project
    its brief names, else the oldest project's."""

    def test_by_the_brief_then_the_oldest(self):
        code = os.path.normpath("/w/repo")
        projects = [{"id": "p2", "name": "Search", "path": code, "poRoomId": "room-s", "createdAt": 20},
                    {"id": "p1", "name": "Checkout", "path": code, "poRoomId": "room-c", "createdAt": 10}]
        rooms = [{"id": "room-s", "cwd": code}, {"id": "room-c", "cwd": code}]
        folders = dashboard.po_seat_folders(projects, rooms)
        brief = "[rotation] You are the product owner (PO) of the project '{}', taking over."
        self.assertEqual(dashboard.old_po_room(code, brief.format("Search"), folders), "room-s")
        self.assertEqual(dashboard.old_po_room(code, brief.format("checkout"), folders), "room-c")
        self.assertEqual(dashboard.old_po_room(code, brief.format("Gone"), folders), "room-c")
        self.assertEqual(dashboard.old_po_room(code, "hello", folders), "")

    def test_a_name_with_an_apostrophe(self):
        code = os.path.normpath("/w/repo")
        projects = [{"id": "p1", "name": "Core", "path": code, "poRoomId": "room-c", "createdAt": 10},
                    {"id": "p2", "name": "CEO's tools", "path": code, "poRoomId": "room-t", "createdAt": 20},
                    {"id": "p3", "name": "CEO", "path": code, "poRoomId": "room-e", "createdAt": 30}]
        rooms = [{"id": r, "cwd": code} for r in ("room-c", "room-t", "room-e")]
        folders = dashboard.po_seat_folders(projects, rooms)
        self.assertEqual(dashboard.old_po_room(
            code, "[rotation] You are the product owner (PO) of the project 'CEO's tools', taking over.", folders),
            "room-t")
        self.assertEqual(dashboard.old_po_room(
            code, "You are the product owner (PO) of the project 'CEO' in Ensemble", folders), "room-e")


class TheChangesTab(unittest.TestCase):

    def test_another_projects_merge_is_not_this_ones_task(self):
        idx = {"branch": {"sess/A-3-fix": 3}, "title": {}, "foreign": {"sess/B-3-fix", "sess/fix"}}
        self.assertEqual(dashboard.commit_task_no("Merge branch 'sess/A-3-fix'", idx), 3)
        self.assertIsNone(dashboard.commit_task_no("Merge branch 'sess/B-3-fix'", idx))
        self.assertIsNone(dashboard.commit_task_no("Merge remote-tracking branch 'origin/sess/fix'", idx))
        self.assertEqual(dashboard.commit_task_no("Merge #7: the thing", idx), 7)


@unittest.skipUnless(GIT, "git is not installed")
class TaskBranches(Hub):

    def setUp(self):
        super().setUp()
        git(self.code, "init", "-q", "-b", "main")
        (self.code / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.code, "add", ".")
        git(self.code, "commit", "-q", "-m", "first")
        self.a, _ = self.register("Checkout")
        self.b, _ = self.register("Search")
        keys = dashboard.project_keys()
        self.ka, self.kb = keys[self.a["id"]], keys[self.b["id"]]

    def task(self, pid, title="Fix login"):
        ok, room, err = dashboard.create_task(title, "spec", pid, [{"agent": "claude"}], "worktree")
        self.assertTrue(ok, err)
        return room

    def test_the_name(self):
        self.assertEqual(dashboard.task_branch_name("ED", 7, "fix_login"), "sess/ED-7-fix_login")
        self.assertEqual(dashboard.task_branch_name("", 7, "fix_login"), "sess/7-fix_login")
        self.assertEqual(dashboard.task_branch_name("ED", None, "fix_login"), "sess/ED-fix_login")
        self.assertEqual(dashboard.task_branch_name("", None, ""), "sess/session")

    def test_same_titled_tasks_of_two_projects(self):
        ra, rb = self.task(self.a["id"]), self.task(self.b["id"])
        ba, bb = ra["workspace"]["branch"], rb["workspace"]["branch"]
        self.assertEqual(ba, f"sess/{self.ka}-{ra['no']}-fix_login")
        self.assertEqual(bb, f"sess/{self.kb}-{rb['no']}-fix_login")
        self.assertNotEqual(ba, bb)
        self.assertEqual(git(ra["cwd"], "rev-parse", "--abbrev-ref", "HEAD"), ba)
        self.assertEqual(git(rb["cwd"], "rev-parse", "--abbrev-ref", "HEAD"), bb)
        # The number in the branch is the number the task got.
        self.assertEqual(ra["no"], 1)
        r2 = self.task(self.a["id"], "Other")
        self.assertEqual(r2["workspace"]["branch"], f"sess/{self.ka}-2-other")

    def test_a_branch_that_is_there_already_is_not_taken_over(self):
        foreign = f"sess/{self.ka}-1-fix_login"
        git(self.code, "branch", foreign)
        room = self.task(self.a["id"])
        self.assertEqual(room["workspace"]["branch"], foreign + "-2")
        self.assertEqual(git(room["cwd"], "rev-parse", "--abbrev-ref", "HEAD"), foreign + "-2")
        # The branch that was there is checked out nowhere.
        self.assertNotIn(f"branch refs/heads/{foreign}\n",
                         git(self.code, "worktree", "list", "--porcelain") + "\n")

    def test_an_existing_tasks_branch_keeps_its_name(self):
        room = self.task(self.a["id"])
        full = chatroom.get_room(room["id"], public=False)
        self.assertEqual(full["workspace"]["branch"], room["workspace"]["branch"])


if __name__ == "__main__":
    unittest.main()
