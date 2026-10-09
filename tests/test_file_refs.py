"""file_refs (#199): the one resolver behind every file link. A path as an
agent writes it (either slash, a #, spaces, %-escapes, a line on its end, a
"..." for the middle) finds its file, in the documented order; a missing one
is missing, and a moved or renamed one is offered."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import file_refs  # noqa: E402


def write(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class Resolve(unittest.TestCase):
    def setUp(self):
        file_refs.forget_indexes()
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.cwd, self.home, self.code = t / "task" / "repo", t / "home", t / "code"
        self.docs = self.home / "Documents"
        for d in (self.cwd, self.docs, self.code):
            d.mkdir(parents=True)
        self.bases = [str(self.cwd), str(self.home), str(self.docs), str(self.code)]

    def tearDown(self):
        file_refs.forget_indexes()
        self._tmp.cleanup()

    def res(self, raw):
        return file_refs.resolve(raw, self.bases)

    def test_either_slash_and_relative_to_each_folder_in_order(self):
        a = write(self.cwd / "docs" / "a.md")
        write(self.home / "docs" / "a.md")
        self.assertEqual(self.res("docs/a.md"), (a, "relative"))
        self.assertEqual(self.res("docs\\a.md"), (a, "relative"))
        b = write(self.home / "notes" / "b.md")
        self.assertEqual(self.res("notes/b.md"), (b, "relative"))
        c = write(self.docs / "c.md")
        self.assertEqual(self.res("c.md"), (c, "relative"))
        d = write(self.code / "src" / "d.py")
        self.assertEqual(self.res("src/d.py"), (d, "relative"))

    def test_hash_spaces_and_percent_escapes(self):
        f = write(self.docs / "#3 My plan.md")
        self.assertEqual(self.res("Documents\\#3 My plan.md"), (f, "relative"))
        self.assertEqual(self.res("Documents/%233%20My%20plan.md"), (f, "relative"))
        self.assertEqual(self.res("`Documents/#3 My plan.md`"), (f, "relative"))
        self.assertEqual(self.res(str(f)), (f, "absolute"))

    def test_a_line_on_the_end(self):
        f = write(self.code / "src" / "X.java")
        for raw in ("src/X.java:887", "src/X.java:887:3", "src/X.java#L887", "src/X.java#L10-L20"):
            self.assertEqual(self.res(raw), (f, "relative"), raw)
        self.assertEqual(file_refs.spellings("notes:12"), ["notes:12"], "no file name before the line: as written")

    def test_by_name_when_folders_were_dropped(self):
        f = write(self.code / "a" / "b" / "c" / "deep.md")
        self.assertEqual(self.res("deep.md"), (f, "tail"))
        self.assertEqual(self.res("c/deep.md"), (f, "tail"))
        self.assertEqual(self.res("x/deep.md"), (None, ""), "a folder that is not there does not match")
        write(self.code / "data.md")
        self.assertEqual(self.res("a.md"), (None, ""), "a.md is not data.md")

    def test_java_depth(self):
        f = write(self.code / "common" / "src" / "main" / "java" / "com" / "stl" / "opten" / "common" / "tws" / "Conn.java")
        self.assertEqual(self.res("Conn.java"), (f, "tail"))

    def test_an_ellipsis_for_the_middle(self):
        f = write(self.code / "common" / "src" / "main" / "java" / "tws" / "Conn.java")
        write(self.code / "other" / "src" / "tws" / "Conn2.java")
        self.assertEqual(self.res("common/.../tws/Conn.java"), (f, "tail"))
        self.assertEqual(self.res("common/…/tws/Conn.java"), (f, "tail"))
        self.assertEqual(self.res("other/.../tws/Conn.java"), (None, ""), "the folders before it must be on the path")

    def test_the_first_folder_that_has_one(self):
        # the task's own file, deep in its cwd, before another task's newer one under the home
        own = write(self.cwd / "docs" / "notes.md")
        other = write(self.home / "taskB" / "notes.md")
        os.utime(own, (time.time() - 100, time.time() - 100))
        self.assertEqual(self.res("notes.md"), (own, "tail"))
        self.assertTrue(other.is_file())

    def test_shallowest_then_newest(self):
        write(self.code / "a" / "b" / "n.md")
        old = write(self.code / "x" / "n.md")
        new = write(self.code / "y" / "n.md")
        os.utime(old, (time.time() - 100, time.time() - 100))
        self.assertEqual(self.res("n.md"), (new, "tail"))

    def test_missing_is_missing(self):
        self.assertEqual(self.res("Documents/#9 Not yet.md"), (None, ""))
        self.assertEqual(self.res(str(self.docs / "gone.md")), (None, ""))
        self.assertEqual(self.res(""), (None, ""))
        self.assertEqual(self.res("../outside.md"), (None, ""))


class Suggest(unittest.TestCase):
    def test_moved_and_renamed(self):
        file_refs.forget_indexes()
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            moved = write(t / "archive" / "#3 Plan.md")
            renamed = write(t / "Documents" / "#3 The plan v2.md")
            close = write(t / "Documents" / "plam.md")
            write(t / "Documents" / "plan.txt")
            s = file_refs.suggest("Documents/#3 Plan.md", [str(t)])
            self.assertEqual(s["name"], "#3 Plan.md")
            self.assertEqual(s["same"], [str(moved)])
            self.assertIn(str(renamed), s["close"], "the same task number in front")
            s = file_refs.suggest("plan.md", [str(t)])
            self.assertIn(str(close), s["close"])
            self.assertNotIn(str(t / "Documents" / "plan.txt"), s["close"], "another kind of file")
        file_refs.forget_indexes()


if __name__ == "__main__":
    unittest.main()
