"""The move check (#331): paths from another machine, broken worktrees and
conversations under an old slug. Throwaway state only: a fake hub module, a
temp projects root, temp git repositories."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import move_check as mc  # noqa: E402


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    git(path, "config", "core.autocrlf", "false")
    (path / "a.py").write_text("print(1)\n")
    (path / "sub").mkdir()
    (path / "sub" / "b.txt").write_text("b\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "one")
    return path


class FakeChat:
    def __init__(self):
        self.rooms, self.notices = {}, []

    def list_rooms(self):
        return [dict(r) for r in self.rooms.values()]

    def get_room(self, rid, public=True):
        r = self.rooms.get(rid)
        return json.loads(json.dumps(r)) if r else None

    def patch_room(self, rid, **fields):
        self.rooms[rid].update(fields)

    def post_notice(self, rid, sender, text, meta=None):
        self.notices.append((rid, text))


def fake_hub(tmp: Path, projects: list, live=()):
    chat = FakeChat()
    hub = types.SimpleNamespace(
        HOME=str(tmp / "home"), PROJECTS_ROOT=str(tmp / "home" / "EnsembleProjects"),
        DASHBOARD_DIR=tmp / "state", PROJECTS_FILE=str(tmp / "state" / "projects.json"),
        PROJ_DIR=str(tmp / "home" / ".claude" / "projects"),
        _PROJECTS_CACHE_LOCK=threading.Lock(), _PROJECTS_CACHE={},
        chatroom=chat, digest=types.SimpleNamespace(SENDER="hub"),
        load_projects=lambda: projects, save_projects=lambda ps: None,
        load_archived=lambda: set(),
        normalize_workflow=lambda w: w or "inprogress",
        task_label=lambda r: r.get("title", ""),
        ensemble_tools=types.SimpleNamespace(_project_of_room=lambda r: r.get("projectId", "")),
        _room_is_live=lambda r: r["id"] in live,
        agents=types.SimpleNamespace(get_agent=lambda name: None),
    )
    (tmp / "state").mkdir(parents=True, exist_ok=True)
    Path(hub.PROJECTS_ROOT).mkdir(parents=True, exist_ok=True)
    mc.bind(hub)
    mc.log = lambda m: None
    return hub, chat


class MapOldPath(unittest.TestCase):
    def test_windows_paths_on_this_system(self):
        home, root = Path("/home/x"), Path("/home/x/EnsembleProjects")
        self.assertEqual(mc.map_old_path(r"C:\Users\u\EnsembleProjects\P\t\repo", home, root)[0],
                         root / "P" / "t" / "repo")
        self.assertEqual(mc.map_old_path("C:/Users/u/IdeaProjects/app", home, root),
                         [home / "IdeaProjects" / "app"])
        self.assertEqual(mc.map_old_path(r"\\?\C:\Users\u\dev\x", home, root), [home / "dev" / "x"])
        self.assertEqual(mc.map_old_path(r"D:\Code\x", home, root), [])
        self.assertEqual(mc.map_old_path("relative/x", home, root), [])

    def test_posix_paths_for_a_windows_home(self):
        home = Path("C:/Users/w")
        root = home / "EnsembleProjects"
        self.assertEqual(mc.map_old_path("/Users/m/EnsembleProjects/P", home, root)[0], root / "P")
        self.assertEqual(mc.map_old_path("/home/l/dev/ensemble", home, root),
                         [home / "dev" / "ensemble"])

    def test_projects_root_elsewhere(self):
        home, root = Path("/home/x"), Path("/data/EnsembleProjects")
        got = mc.map_old_path(r"C:\Users\u\EnsembleProjects\P", home, root)
        self.assertEqual(got, [root / "P", home / "EnsembleProjects" / "P"])

    def test_mapped_dir_wants_a_repo_for_code(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            (home / "dev" / "x").mkdir(parents=True)
            self.assertIsNone(mc.mapped_dir(r"C:\Users\u\dev\x", home, home / "EP", git=True))
            (home / "dev" / "x" / ".git").mkdir()
            self.assertEqual(mc.mapped_dir(r"C:\Users\u\dev\x", home, home / "EP", git=True),
                             home / "dev" / "x")


class Gitdir(unittest.TestCase):
    def test_broken_and_whole(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t) / "wt"
            wt.mkdir()
            self.assertEqual(mc.gitdir_broken(wt), "")            # no .git
            (wt / ".git").write_text("gitdir: C:/Users/u/r/.git/worktrees/repo1\n")
            if os.name != "nt":
                self.assertEqual(mc.gitdir_broken(wt), "C:/Users/u/r/.git/worktrees/repo1")
            (Path(t) / "gd").mkdir()
            (wt / ".git").write_text(f"gitdir: {Path(t) / 'gd'}\n")
            self.assertEqual(mc.gitdir_broken(wt), "")
            (wt / ".git").write_text(f"gitdir: {Path(t) / 'gone'}\n")
            self.assertEqual(mc.gitdir_broken(wt), str(Path(t) / "gone"))

    def test_long_slug(self):
        with tempfile.TemporaryDirectory() as t:
            pd = Path(t)
            cwd = "/" + "a" * 250
            self.assertIsNone(mc.claude_dir_for(pd, cwd))
            (pd / (mc.claude_slug(cwd)[:200] + "-abc123")).mkdir()
            self.assertEqual(mc.claude_dir_for(pd, cwd).name, mc.claude_slug(cwd)[:200] + "-abc123")


class Check(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.repo = make_repo(self.tmp / "home" / "dev" / "app")
        mc._TREES.clear()

    def tearDown(self):
        self._t.cleanup()

    def _task(self, hub, chat, rid, branch, files=None, exec_bit=False):
        """A task whose worktree was copied from Windows: its files, a .git file
        naming a C:/ git folder."""
        tdir = Path(hub.PROJECTS_ROOT) / "P" / rid
        wt = tdir / "repo"
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt))
        for name, text in (files or {}).items():
            (wt / name).write_text(text)
        if exec_bit:
            for f in wt.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    f.chmod(0o755)
        (wt / ".git").write_text("gitdir: C:/Users/u/app/.git/worktrees/repo9\n")
        # The repository was cloned again on the new machine: it knows no worktree.
        shutil.rmtree(self.repo / ".git" / "worktrees")
        git(self.repo, "worktree", "prune")
        chat.rooms[rid] = {"id": rid, "title": rid, "projectId": "p1", "cwd": str(wt),
                           "taskDir": str(tdir), "createdAt": 0, "participants": [],
                           "workspace": {"mode": "worktree", "branch": branch,
                                         "taskDir": str(tdir)}}
        return wt

    @unittest.skipIf(os.name == "nt", "a C:/ git folder exists-check is Windows-shaped there")
    def test_worktree_equal_to_a_commit_is_made_again(self):
        hub, chat = fake_hub(self.tmp, [{"id": "p1", "path": str(self.repo)}])
        wt = self._task(hub, chat, "r1", "sess/one", exec_bit=True)
        r = mc.run_check(repair=False, notify=False)
        self.assertEqual(r["found"]["worktree"], 1)
        self.assertIn("(repairable)", r["open"][0]["detail"])   # exec bits are no change
        r = mc.run_check(repair=True, notify=False)
        self.assertEqual([i["kind"] for i in r["repaired"]], ["worktree"])
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD"), "sess/one")
        self.assertTrue(list(wt.parent.glob("repo.moved-*")))
        self.assertEqual(mc.run_check(repair=False, notify=False)["found"]["worktree"], 0)

    @unittest.skipIf(os.name == "nt", "a C:/ git folder exists-check is Windows-shaped there")
    def test_worktree_with_own_work_is_kept_with_a_patch(self):
        hub, chat = fake_hub(self.tmp, [{"id": "p1", "path": str(self.repo), "poRoomId": "po"}])
        chat.rooms["po"] = {"id": "po", "workflow": "done"}         # not itself checked
        wt = self._task(hub, chat, "r2", "sess/two", files={"new.py": "x = 1\n"})
        r = mc.run_check(repair=True, notify=True)
        self.assertEqual(r["repaired"], [])
        item = r["open"][0]
        self.assertEqual(item["kind"], "worktree")
        self.assertEqual((wt / "new.py").read_text(), "x = 1\n")    # left as it is
        patch = Path(item["patch"])
        self.assertTrue(patch.is_file())
        self.assertIn("new.py", patch.read_text())
        self.assertEqual(item["branch"], f"sess/two-moved-{mc.time.strftime('%Y%m%d')}")
        self.assertEqual(git(self.repo, "show", f"{item['branch']}:new.py"), "x = 1")
        self.assertEqual(len(chat.notices), 1)
        # A second run says nothing new to the PO and writes no second patch.
        mc.run_check(repair=True, notify=True)
        self.assertEqual(len(chat.notices), 1)
        self.assertEqual(len(list(wt.parent.glob("moved-*.patch"))), 1)

    @unittest.skipIf(os.name == "nt", "a C:/ git folder exists-check is Windows-shaped there")
    def test_kept_without_its_branch_gets_no_commit(self):
        hub, chat = fake_hub(self.tmp, [{"id": "p1", "path": str(self.repo)}])
        self._task(hub, chat, "r3", "sess/three", files={"n.py": "y\n"})
        git(self.repo, "branch", "-D", "sess/three")
        item = mc.run_check(repair=True, notify=False)["open"][0]
        self.assertEqual(item["branch"], "")
        self.assertTrue(Path(item["patch"]).is_file())

    @unittest.skipIf(os.name == "nt", "a C:/ git folder exists-check is Windows-shaped there")
    def test_running_task_is_not_touched(self):
        hub, chat = fake_hub(self.tmp, [{"id": "p1", "path": str(self.repo)}], live={"r4"})
        wt = self._task(hub, chat, "r4", "sess/four")
        r = mc.run_check(repair=True, notify=False)
        self.assertEqual(r["repaired"], [])
        self.assertIn("running", r["open"][0]["detail"])
        self.assertFalse(list(wt.parent.glob("repo.moved-*")))

    def test_transcript_is_copied_not_moved(self):
        hub, chat = fake_hub(self.tmp, [])
        cwd = Path(hub.PROJECTS_ROOT) / "P" / "t5" / "repo"
        cwd.mkdir(parents=True)
        old = Path(hub.PROJ_DIR) / "C--Users-u-EnsembleProjects-P-t5-repo"
        (old / "s1").mkdir(parents=True)
        (old / "s1.jsonl").write_text("{}\n")
        (old / "s1" / "agent.jsonl").write_text("{}\n")
        chat.rooms["r5"] = {"id": "r5", "title": "t5", "cwd": str(cwd), "sharedCwd": True,
                            "participants": [{"identity": "claude", "agent": "claude",
                                              "sessionId": "s1"}]}
        r = mc.run_check(repair=True, notify=False)
        self.assertEqual([i["kind"] for i in r["repaired"]], ["transcript"])
        new = Path(hub.PROJ_DIR) / mc.claude_slug(str(cwd))
        self.assertTrue((new / "s1.jsonl").is_file())
        self.assertTrue((new / "s1" / "agent.jsonl").is_file())
        self.assertTrue((old / "s1.jsonl").is_file())
        self.assertEqual(mc.run_check(repair=False, notify=False)["found"]["transcript"], 0)

    def test_code_folder_and_task_folders(self):
        hub, chat = fake_hub(self.tmp, [])
        home = Path(hub.PROJECTS_ROOT) / "P"
        (home / "t6").mkdir(parents=True)
        (home / "project.json").write_text(json.dumps(
            {"id": "p1", "name": "P", "code": r"C:\Users\u\dev\app"}))
        q = Path(hub.PROJECTS_ROOT) / "Q"
        q.mkdir()
        (q / "project.json").write_text(json.dumps(
            {"id": "p2", "name": "Q", "code": r"C:\Users\u\cs\gone"}))
        chat.rooms["r6"] = {"id": "r6", "title": "t6",
                            "cwd": r"C:\Users\u\EnsembleProjects\P\t6",
                            "taskDir": r"C:\Users\u\EnsembleProjects\P\t6", "participants": []}
        r = mc.run_check(repair=True, notify=False)
        self.assertEqual(json.loads((home / "project.json").read_text())["code"], str(self.repo))
        self.assertEqual(chat.rooms["r6"]["cwd"], str(home / "t6"))
        self.assertEqual(sorted(i["kind"] for i in r["repaired"]), ["code", "taskFolder"])
        self.assertEqual([(i["kind"], i["what"]) for i in r["open"]], [("code", "Q")])
        self.assertEqual(mc.last()["open"][0]["what"], "Q")


if __name__ == "__main__":
    unittest.main()
