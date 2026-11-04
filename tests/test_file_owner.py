"""Who a file belongs to, so a comment on it in the file view can be sent
(#297): file_owner.owner, pure, and dashboard.file_owner_info, the hub's
GET /api/file/owner over throwaway state."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import file_owner  # noqa: E402

H = r"C:\Users\me\EnsembleProjects"
DASH = {"id": "proj-dash", "name": "Dashboard", "path": r"C:\code\dash", "home": H + r"\Dashboard",
        "documentsDir": H + r"\Dashboard\Documents", "poRoomId": "room-po000001", "createdAt": 1}
TRADE = {"id": "proj-trade", "name": "Trading", "path": r"C:\code\trade", "home": H + r"\Trading",
         "documentsDir": H + r"\Trading\Documents", "poRoomId": "room-po000002", "createdAt": 2}
# A second project on the dashboard's code folder, made later.
SIDE = {"id": "proj-side", "name": "Side", "path": r"C:\code\dash", "home": H + r"\Side",
        "documentsDir": H + r"\Side\Documents", "poRoomId": "room-po000003", "createdAt": 3}
TASK = {"id": "room-task0001", "no": 12, "title": "Faster motors", "projectId": "proj-dash",
        "taskDir": H + r"\Dashboard\faster_motors", "cwd": H + r"\Dashboard\faster_motors\repo", "updatedAt": 5}
SIDE_TASK = {"id": "room-task0002", "no": 3, "title": "Side work", "projectId": "proj-side",
             "taskDir": H + r"\Side\side_work", "cwd": H + r"\Side\side_work\repo", "updatedAt": 6}
# An in-place task: it runs in the project's own code folder, which is no task's.
IN_PLACE = {"id": "room-task0003", "no": 4, "title": "In place", "projectId": "proj-trade",
            "taskDir": H + r"\Trading\in_place", "cwd": r"C:\code\trade", "updatedAt": 7}
PO_ROOM = {"id": "room-po000001", "no": None, "title": "PO", "projectId": "proj-dash",
           "taskDir": "", "cwd": H + r"\Dashboard", "updatedAt": 9}
PROJECTS = [DASH, TRADE]
ROOMS = [TASK, IN_PLACE, PO_ROOM]


def who(path, projects=PROJECTS, rooms=ROOMS):
    got = file_owner.owner(path, projects, rooms)
    o = got["owner"]
    return (o["room"], o["kind"]) if o else None, [e["room"] for e in got["others"]]


class TheOwner(unittest.TestCase):
    def test_a_file_in_a_tasks_worktree_is_the_tasks(self):
        self.assertEqual(who(H + r"\Dashboard\faster_motors\repo\dashboard.py"),
                         (("room-task0001", "task"), ["room-po000001"]))

    def test_a_file_in_a_tasks_folder_is_the_tasks(self):
        got = file_owner.owner(H + r"\Dashboard\faster_motors\TASK-HANDOVER.md", PROJECTS, ROOMS)
        self.assertEqual(got["owner"]["room"], "room-task0001")
        self.assertEqual(got["owner"]["name"], "#12 Faster motors")
        self.assertEqual(got["others"][0]["name"], "Dashboard PO")

    def test_a_document_is_the_projects_po(self):
        self.assertEqual(who(H + r"\Dashboard\Documents\#12 Plan.md"), (("room-po000001", "po"), []))
        self.assertEqual(who(H + r"\Trading\Documents\#385 x\restart-recovery.html"), (("room-po000002", "po"), []))

    def test_a_file_of_the_code_folder_is_the_projects_po(self):
        self.assertEqual(who(r"C:\code\trade\engine\run.py"), (("room-po000002", "po"), []))

    def test_an_in_place_task_owns_nothing_of_the_code_folder(self):
        self.assertEqual(who(r"C:\code\trade\README.md")[0], ("room-po000002", "po"))
        self.assertEqual(who(H + r"\Trading\in_place\notes.md")[0], ("room-task0003", "task"))

    def test_the_pos_own_room_owns_nothing(self):
        self.assertEqual(who(H + r"\Dashboard\roadmap.md"), (("room-po000001", "po"), []))

    def test_two_projects_on_one_code_folder(self):
        both, rooms = [DASH, SIDE, TRADE], ROOMS + [SIDE_TASK]
        # No task holds it: the oldest project's PO, the other offered beside it.
        self.assertEqual(who(r"C:\code\dash\index.html", both, rooms), (("room-po000001", "po"), ["room-po000003"]))
        # Its task's worktree: that task, and its own project's PO.
        self.assertEqual(who(H + r"\Side\side_work\repo\index.html", both, rooms),
                         (("room-task0002", "task"), ["room-po000003"]))

    def test_slashes_and_case_do_not_matter_on_windows(self):
        self.assertEqual(who("c:/users/ME/ensembleprojects/dashboard/FASTER_MOTORS/repo/a.py")[0], ("room-task0001", "task"))
        self.assertEqual(who("C:/code/Trade/x.py")[0], ("room-po000002", "po"))

    def test_a_folder_named_like_another_is_not_inside_it(self):
        self.assertIsNone(who(r"C:\code\trader\x.py")[0])
        self.assertEqual(who(H + r"\Dashboard\faster_motors_2\x.md")[0], ("room-po000001", "po"))

    def test_posix_paths(self):
        p = {**DASH, "path": "/home/me/dash", "home": "/home/me/EP/Dashboard", "documentsDir": "/home/me/EP/Dashboard/Documents"}
        t = {**TASK, "taskDir": "/home/me/EP/Dashboard/t", "cwd": "/home/me/EP/Dashboard/t/repo"}
        self.assertEqual(who("/home/me/EP/Dashboard/t/repo/a.py", [p], [t])[0], ("room-task0001", "task"))
        self.assertEqual(who("/home/me/dash/a.py", [p], [t])[0], ("room-po000001", "po"))
        self.assertIsNone(who("/home/me/Dash/a.py", [p], [t])[0], "POSIX keeps case")

    def test_nothing_owns_a_file_outside_every_project(self):
        self.assertEqual(who(r"D:\elsewhere\notes.md"), (None, []))
        self.assertEqual(who(""), (None, []))


class TheHubsAnswer(unittest.TestCase):
    """file_owner_info over a throwaway hub state: the opener kept, the
    owner beside it, every PO to pick from when nothing owns the file."""

    @classmethod
    def setUpClass(cls):
        import chatroom
        import dashboard
        cls.d, cls.c = dashboard, chatroom
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-fo-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state, root = base / "state", base / "EnsembleProjects"
        state.mkdir()
        root.mkdir()
        for p in [mock.patch.object(dashboard, "PROJECTS_ROOT", root),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                  mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                  mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
                  mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")})]:
            p.start()
            cls.addClassCleanup(p.stop)
        code = base / "code"
        code.mkdir()
        cls.po = chatroom.create_room("Motors PO", [{"identity": "claude", "agent": "claude", "cwd": str(root)}])["id"]
        cls.task = chatroom.create_room("Gearbox", [{"identity": "claude", "agent": "claude", "cwd": str(code)}])["id"]
        home = root / "Motors"
        (home / "Documents").mkdir(parents=True)
        tdir = home / "gearbox"
        (tdir / "repo").mkdir(parents=True)
        chatroom.patch_room(cls.task, cwd=str(tdir / "repo"), taskDir=str(tdir))
        dashboard.save_projects([{"id": "proj-m", "name": "Motors", "path": str(code), "poRoomId": cls.po,
                                  "createdAt": 1, "home": str(home)}])
        dashboard.save_session_projects({cls.task: "proj-m"})
        cls.doc = home / "Documents" / "plan.md"
        cls.doc.write_text("# plan\n", encoding="utf-8")
        cls.mine = tdir / "repo" / "a.py"
        cls.mine.write_text("x = 1\n", encoding="utf-8")
        cls.out = base / "outside.md"
        cls.out.write_text("x\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_document_with_no_room_goes_to_the_po(self):
        got = self.d.file_owner_info(str(self.doc))
        self.assertEqual(got["room"], self.po)
        self.assertEqual(got["owner"]["name"], "Motors PO")
        self.assertEqual([c["why"] for c in got["choices"]], ["owner"])

    def test_the_openers_room_is_kept_with_the_owner_beside_it(self):
        got = self.d.file_owner_info(str(self.doc), room_id=self.task)
        self.assertEqual(got["room"], self.task)
        self.assertEqual([(c["room"], c["why"]) for c in got["choices"]], [(self.task, "opener"), (self.po, "owner")])

    def test_a_tasks_file_offers_the_task_then_its_po(self):
        got = self.d.file_owner_info(str(self.mine))
        self.assertEqual(got["room"], self.task)
        self.assertEqual([c["why"] for c in got["choices"]], ["owner", "po"])

    def test_an_unknown_opener_falls_back_to_the_owner(self):
        got = self.d.file_owner_info(str(self.doc), room_id="room-00000000")
        self.assertTrue(got["openerGone"])
        self.assertEqual(got["room"], self.po)

    def test_a_file_outside_every_project_is_the_persons_pick(self):
        got = self.d.file_owner_info(str(self.out))
        self.assertIsNone(got["owner"])
        self.assertEqual(got["room"], "")
        self.assertEqual([(c["room"], c["why"]) for c in got["choices"]], [(self.po, "pick")])


if __name__ == "__main__":
    unittest.main()
