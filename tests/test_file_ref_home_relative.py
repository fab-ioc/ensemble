"""P261: a path written from the folder holding the projects, or from the
user's home, resolves as written; those folders are never walked by name."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard


class HomeRelativePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.projects = self.home / "Projects"
        self.proj = self.projects / "Trading"
        (self.proj / "task-a").mkdir(parents=True)
        self.file = self.proj / "task-a" / "mockup.html"
        self.file.write_text("<p>x</p>", encoding="utf-8")
        pj = {"id": "proj-t", "name": "Trading", "home": str(self.proj), "path": str(self.proj)}
        self.patches = [
            mock.patch.object(dashboard, "load_projects", return_value=[pj]),
            mock.patch.object(dashboard, "project_home", side_effect=lambda p, create=False: p["home"]),
            mock.patch.object(Path, "home", classmethod(lambda cls: self.home)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_from_the_projects_folder(self):
        fp, how = dashboard.resolve_file_ref_how("Trading/task-a/mockup.html")
        self.assertEqual((fp, how), (self.file, "relative"))

    def test_from_the_home(self):
        fp, how = dashboard.resolve_file_ref_how("Projects/Trading/task-a/mockup.html")
        self.assertEqual((fp, how), (self.file, "relative"))

    def test_home_is_not_walked_by_name(self):
        bases, search = dashboard._file_ref_places()
        self.assertIn(str(self.home), bases)
        self.assertNotIn(str(self.home), search)
        self.assertNotIn(str(self.projects), search)


if __name__ == "__main__":
    unittest.main()
