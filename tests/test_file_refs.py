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


class WhatAWalkCosts(unittest.TestCase):
    """The PO's cost review: a folder is walked once and reused, by one request
    at a time; one request walks at most MAX_ENTRIES entries over all its
    folders; the cache drops its oldest folder, not all of them."""

    def setUp(self):
        file_refs.forget_indexes()
        self._tmp = tempfile.TemporaryDirectory()
        self.t = Path(self._tmp.name)

    def tearDown(self):
        file_refs.forget_indexes()
        self._tmp.cleanup()

    def test_a_cached_index_is_reused(self):
        a = write(self.t / "a" / "deep" / "x.md")
        w = file_refs.WALKS
        self.assertEqual(file_refs.find_by_tail("x.md", [str(self.t)]), a)
        self.assertEqual(file_refs.find_by_tail("deep/x.md", [str(self.t)]), a)
        file_refs.suggest("y.md", [str(self.t)])
        self.assertEqual(file_refs.WALKS - w, 1)

    def test_one_walk_of_a_folder_at_a_time(self):
        import threading
        from unittest import mock
        for i in range(20):
            write(self.t / f"d{i}" / f"f{i}.md")
        real, started, go = file_refs._walk, threading.Event(), threading.Event()

        def slow(top, cap):
            started.set()
            go.wait(5)
            return real(top, cap)

        got = []
        with mock.patch.object(file_refs, "_walk", slow):
            w = file_refs.WALKS
            ts = [threading.Thread(target=lambda: got.append(file_refs.name_index(str(self.t))[1]))
                  for _ in range(3)]
            ts[0].start()
            self.assertTrue(started.wait(5))
            for x in ts[1:]:
                x.start()
            time.sleep(0.2)
            go.set()
            for x in ts:
                x.join(5)
        self.assertEqual(file_refs.WALKS - w, 1, "the others waited for the one walk")
        self.assertEqual(len(got), 3)
        self.assertEqual(len(set(got)), 1)

    def test_the_budget_is_per_request_over_all_folders(self):
        from unittest import mock
        one, two = self.t / "one", self.t / "two"
        for i in range(30):
            write(one / f"f{i}.md")
        hit = write(two / "only-here.md")
        with mock.patch.object(file_refs, "MAX_ENTRIES", 20):
            w = file_refs.WALKS
            self.assertIsNone(file_refs.find_by_tail("only-here.md", [str(one), str(two)]))
            self.assertEqual(file_refs.WALKS - w, 1, "the first folder spent it: the second is not walked")
            self.assertEqual(file_refs.find_by_tail("only-here.md", [str(two), str(one)]), hit)
            b = file_refs.Budget()
            self.assertEqual(b.left, 20)
            self.assertIsNone(file_refs.resolve("x/only-here.md", [str(one), str(two)], budget=b)[0])
            self.assertLessEqual(b.left, 0, "a later check of the same request has nothing left")
            self.assertIsNone(file_refs.resolve("x/only-here.md", [str(two)], budget=b)[0])
            self.assertEqual(file_refs.resolve("only-here.md", [str(two)], budget=b)[0], hit,
                             "what is written is still found without a walk")

    def test_the_oldest_folder_goes_first(self):
        from unittest import mock
        dirs = []
        for i in range(4):
            dirs.append(self.t / f"r{i}")
            write(dirs[-1] / "a.md")
        with mock.patch.object(file_refs, "INDEX_KEEP", 3):
            for d in dirs:
                file_refs.name_index(str(d))
                time.sleep(0.01)
            keys = set(file_refs._INDEX)
        self.assertEqual(len(keys), 3)
        self.assertNotIn(os.path.normcase(os.path.normpath(str(dirs[0]))), keys)
        self.assertIn(os.path.normcase(os.path.normpath(str(dirs[3]))), keys)


RECHECK_JS = r"""
const asked = [];
let answer = [false];
globalThis.fetch = async (url) => {
  const q = JSON.parse(decodeURIComponent(url.split('?q=')[1]));
  asked.push(q);
  return { status: 200, ok: true, json: async () => ({ there: q.items.map(() => answer.shift() ?? false) }) };
};
const link = { getAttribute: () => '/fileview?path=' + encodeURIComponent('Documents/later.md') + '&room=r1',
               classList: { contains: () => false, add() {}, remove() {} }, dataset: {}, removeAttribute() {} };
(async () => {
  FileLinks.scan({ querySelectorAll: () => [link] });
  await new Promise(r => setTimeout(r, 20));
  FileLinks.recheck();
  await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify(asked));
})();
"""


class PageRecheck(unittest.TestCase):
    def test_a_recheck_asks_without_a_search_by_name(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        src = (ROOT / "static" / "filelinks.js").read_text(encoding="utf-8") + RECHECK_JS
        with tempfile.TemporaryDirectory() as t:
            js = Path(t) / "recheck.cjs"
            js.write_text(src, encoding="utf-8")
            out = subprocess.run([node, str(js)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        first, again = __import__("json").loads(out.stdout)
        self.assertNotIn("tail", first, "a first ask may search by name")
        self.assertEqual(again["tail"], False)
        self.assertEqual(again["items"], [["Documents/later.md", 0]])


if __name__ == "__main__":
    unittest.main()
