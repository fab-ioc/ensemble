"""A project's PO documents are never deleted (#317).

PO-HANDOVER.md, PO-HANDOVER-ARCHIVE.md, ROADMAP.md, project.json and the
Documents folder of a project home are refused by every deletion path, through
the one shared check in protected_paths:

* the check itself: each name in any case, the Documents folder, a folder that
  holds one; a file next to them or inside Documents is not protected;
* the Files panel: delete, move away and replace-by-move of each, refused with
  "is protected" in the reply the page shows;
* the cleanup: prune refuses each as a part to delete and keeps a task folder
  that holds one;
* a task's delete: a scratch folder that is a project's home is kept.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dashboard  # noqa: E402
import protected_paths  # noqa: E402
import prune  # noqa: E402
from test_documents_project import Hub  # noqa: E402

FILES = ("PO-HANDOVER.md", "PO-HANDOVER-ARCHIVE.md", "ROADMAP.md", "project.json")


class TheCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "Motors"
        self.docs = self.home / "Documents"
        self.docs.mkdir(parents=True)
        self.places = [(str(self.home), str(self.docs))]

    def test_each_protected_item_is_refused_in_any_case(self):
        for name in FILES:
            for spelled in (name, name.lower(), name.upper()):
                why = protected_paths.refusal(str(self.home / spelled), self.places)
                self.assertIn(f"“{name}” is protected", why, spelled)
        self.assertIn("“Documents” is protected", protected_paths.refusal(str(self.docs), self.places))

    def test_a_folder_holding_one_is_refused(self):
        why = protected_paths.refusal(str(self.home), self.places)
        self.assertIn("holds the project's", why)
        self.assertTrue(protected_paths.refusal(str(self.home.parent), self.places))

    def test_everything_else_may_go(self):
        for p in (self.home / "notes.md", self.home / "ROADMAP.md.bak", self.home / "old" / "ROADMAP.md",
                  self.docs / "#1 report.md", self.home / "Documents2"):
            self.assertEqual(protected_paths.refusal(str(p), self.places), "", p)
        self.assertEqual(protected_paths.refusal("", self.places), "")
        self.assertEqual(protected_paths.refusal(str(self.home / "ROADMAP.md"), []), "")


class TheFilesPanel(Hub):
    def setUp(self):
        super().setUp()
        ok, _ = dashboard.set_project_kind(self.pid, "documents")
        self.assertTrue(ok)
        self.proj, self.h = dashboard.files_target(self.pid)
        self.docs_name = os.path.basename(dashboard.project_documents_dir(self.proj))
        (self.home / self.docs_name).mkdir(exist_ok=True)
        (self.home / self.docs_name / "#1 report.md").write_text("r", encoding="utf-8")
        for name in FILES[:3]:
            (self.home / name).write_text(name, encoding="utf-8")
        (self.home / "notes.md").write_text("n", encoding="utf-8")

    def test_delete_refuses_each_po_document(self):
        for name in (*FILES[:3], self.docs_name):
            with self.assertRaises(dashboard.FileOpRefused) as cm:
                dashboard.files_delete(self.proj, self.h, name)
            self.assertEqual((cm.exception.status, cm.exception.code), (403, "protected"), name)
            self.assertIn("is protected", cm.exception.message)
            self.assertTrue((self.home / name).exists(), name)
        # project.json is the hub's own record: refused before the check is reached.
        with self.assertRaises(dashboard.FileOpRefused):
            dashboard.files_delete(self.proj, self.h, "project.json")
        self.assertTrue((self.home / "project.json").exists())

    def test_the_page_is_told_roadmap_is_protected(self):
        status, body = self.json_call("POST", "/api/files/delete", {"project": self.pid, "path": "ROADMAP.md"},
                                      self.page_headers())
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "protected")
        self.assertIn("“ROADMAP.md” is protected", body["message"])
        self.assertTrue((self.home / "ROADMAP.md").exists())

    def test_other_files_still_delete(self):
        dashboard.files_delete(self.proj, self.h, "notes.md")
        self.assertFalse((self.home / "notes.md").exists())
        dashboard.files_delete(self.proj, self.h, f"{self.docs_name}/#1 report.md")
        self.assertFalse((self.home / self.docs_name / "#1 report.md").exists())

    def test_move_refuses_moving_one_away_or_replacing_one(self):
        for name in (*FILES[:3], self.docs_name):
            with self.assertRaises(dashboard.FileOpRefused) as cm:
                dashboard.files_move(self.proj, self.h, name, f"old-{name}")
            self.assertEqual(cm.exception.code, "protected", name)
            self.assertTrue((self.home / name).exists(), name)
        for name in FILES[:3]:
            with self.assertRaises(dashboard.FileOpRefused) as cm:
                dashboard.files_move(self.proj, self.h, "notes.md", name, overwrite=True)
            self.assertEqual(cm.exception.code, "protected", name)
            self.assertEqual((self.home / name).read_text(encoding="utf-8"), name)
        self.assertTrue((self.home / "notes.md").exists())
        dashboard.files_move(self.proj, self.h, "notes.md", "kept.md")
        self.assertTrue((self.home / "kept.md").exists())


class TheCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.state, self.home = base / "st", base / "home"
        self.docs = self.home / "Documents"
        self.docs.mkdir(parents=True)
        self.state.mkdir()
        for name in FILES:
            (self.home / name).write_text(name, encoding="utf-8")
        self.projects = [{"id": "proj-1", "name": "P", "path": "", "poRoomId": ""}]
        self.hub = prune.Hub(
            state_dir=self.state, claude_dir=base / "cl", codex_dir=base / "cx",
            projects=lambda: self.projects, rooms=lambda: [], live_rooms=set, live_sessions=set,
            room_sessions=lambda r: [], current_sessions=lambda r: set(),
            classify=lambda a, p, s: ("own", ""), slim_room=lambda rid, meta: None,
            forget_checkpoints=lambda r: 0, checkpoint_counts=lambda *a: {},
            project_home=lambda p: str(self.home), documents_dir=lambda p: str(self.docs))

    def test_prune_refuses_each_as_a_part(self):
        for name in (*FILES, "Documents", "documents", "roadmap.md"):
            self.assertFalse(prune._allowed_path(self.hub, str(self.home / name), self.projects), name)
        self.assertTrue(prune._allowed_path(self.hub, str(self.home / "old-task"), self.projects))

    def test_prune_run_keeps_them(self):
        it = {"id": "x", "group": "housekeeping", "label": "x",
              "parts": [{"kind": "file", "path": str(self.home / n)} for n in FILES]}
        freed, errs = prune._prune_parts(self.hub, it, set(), self.projects, "manual")
        self.assertEqual(freed, 0)
        self.assertEqual(len(errs), len(FILES), errs)
        for name in FILES:
            self.assertTrue((self.home / name).exists(), name)

    def test_a_task_folder_holding_one_is_kept(self):
        folder = self.home / "sort"
        folder.mkdir()
        self.assertEqual(prune.task_folder_ok(self.hub, str(folder), self.projects[0], self.projects), "")
        (folder / "po-handover.md").write_text("x", encoding="utf-8")
        self.assertTrue(prune.task_folder_ok(self.hub, str(folder), self.projects[0], self.projects))


class ATasksDelete(Hub):
    def test_a_scratch_folder_that_is_a_project_home_is_kept(self):
        cs = Path(self.tmp.name) / "cs"
        scratch, other = cs / "07_motors", cs / "08_tmp"
        for d in (scratch, other):
            d.mkdir(parents=True)
        (scratch / "ROADMAP.md").write_text("r", encoding="utf-8")
        with mock.patch.object(dashboard, "CS_ROOT", cs), \
                mock.patch.object(dashboard, "protected_places",
                                  lambda: [(str(scratch), str(scratch / "Documents"))]):
            self.assertIsNone(dashboard._delete_scratch_root(str(scratch)))
            self.assertTrue((scratch / "ROADMAP.md").exists())
            self.assertTrue(dashboard._delete_scratch_root(str(other)))
            self.assertFalse(other.exists())

    def test_delete_task_keeps_the_home_of_a_registered_project(self):
        cs = Path(self.tmp.name) / "cs"
        scratch = cs / "09_motors"
        scratch.mkdir(parents=True)
        (scratch / "PO-HANDOVER.md").write_text("h", encoding="utf-8")
        with mock.patch.object(dashboard, "CS_ROOT", cs), \
                mock.patch.object(dashboard, "protected_places",
                                  lambda: [(str(scratch), str(scratch / "Documents"))]):
            res = dashboard.delete_task("", members=[{"agent": "claude", "sessionId": "", "cwd": str(scratch)}])
        self.assertEqual(res["folders"], [])
        self.assertTrue((scratch / "PO-HANDOVER.md").exists())


if __name__ == "__main__":
    unittest.main()
