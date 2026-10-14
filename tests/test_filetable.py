"""#228: the file table re-lists only folders that moved and re-stats only
recent or named files; a full walk catches the rest."""
import os
import tempfile
import time
import unittest
from unittest import mock

import filetable


def _jsonl(name):
    return name.endswith(".jsonl")


class FileTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "p"))
        self.table = filetable.FileTable(lambda: self.root, 1, _jsonl)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, text, age=0.0):
        p = os.path.join(self.root, "p", name)
        with open(p, "a", encoding="utf-8") as f:
            f.write(text)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return p

    def _settle(self):
        """The folders' last change well before the next listing."""
        t = time.time() - 10
        for d in (self.root, os.path.join(self.root, "p")):
            os.utime(d, (t, t))

    def _append(self, p, text):
        # an append keeps the folder time, as on NTFS: put it back to be sure
        d = os.path.dirname(p)
        dm = os.stat(d).st_mtime_ns
        with open(p, "a", encoding="utf-8") as f:
            f.write(text)
        os.utime(d, ns=(dm, dm))

    def test_new_file_seen(self):
        self.assertEqual(self.table.files(), {})
        p = self._write("a.jsonl", "x")
        self._write("skip.txt", "x")
        got = self.table.files()
        self.assertEqual(list(got), [p])
        self.assertEqual(got[p][1], 1)

    def test_append_to_hot_file_seen(self):
        p = self._write("a.jsonl", "x")
        self.table.files()
        self._append(p, "yy")
        self.assertEqual(self.table.files()[p][1], 3)

    def test_cold_file_waits_for_full_walk_unless_named(self):
        p = self._write("old.jsonl", "x", age=filetable.HOT_S + 100)
        self._settle()
        self.table.files()
        self._append(p, "yy")
        self.assertEqual(self.table.files()[p][1], 1)          # cold: as it was
        self.assertEqual(self.table.files(hot_names={"old"})[p][1], 3)

    def test_cold_file_seen_after_full_walk(self):
        p = self._write("old.jsonl", "x", age=filetable.HOT_S + 100)
        self._settle()
        self.table.files()
        self._append(p, "yy")
        os.utime(p, (time.time() - filetable.HOT_S - 50,) * 2)
        self.assertEqual(self.table.files()[p][1], 1)
        with mock.patch.object(filetable.time, "time",
                               return_value=time.time() + filetable.FULL_S + 1):
            self.assertEqual(self.table.files()[p][1], 3)
        self.table.forget()
        self.assertEqual(self.table.files()[p][1], 3)

    def test_file_added_in_the_listing_tick_seen(self):
        # the folder's time does not move when a file lands in the same tick
        d = os.path.join(self.root, "p")
        self.assertEqual(self.table.files(), {})
        dm = os.stat(d).st_mtime_ns
        p = self._write("a.jsonl", "x")
        os.utime(d, ns=(dm, dm))
        self.assertEqual(list(self.table.files()), [p])

    def test_settled_folder_not_listed_again(self):
        self._write("a.jsonl", "x")
        self._settle()
        self.table.files()
        with mock.patch.object(filetable.os, "scandir", side_effect=AssertionError("listed")):
            self.assertEqual(len(self.table.files()), 1)

    def test_replaced_cold_file_seen(self):
        # the hub writes rooms by replacing them: the folder moves
        p = self._write("old.jsonl", "x", age=filetable.HOT_S + 100)
        self._settle()
        self.table.files()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("xyz")
        d = os.path.dirname(p)
        before = os.stat(d).st_mtime_ns
        os.replace(tmp, p)
        if os.stat(d).st_mtime_ns == before:      # coarse folder clock
            os.utime(d, ns=(before + 10**9,) * 2)
        self.assertEqual(self.table.files()[p][1], 3)

    def test_removed_file_gone(self):
        p = self._write("a.jsonl", "x")
        q = self._write("b.jsonl", "x", age=filetable.HOT_S + 100)
        self.assertEqual(set(self.table.files()), {p, q})
        os.remove(q)
        self.assertEqual(set(self.table.files()), {p})

    def test_root_change_resets(self):
        p = self._write("a.jsonl", "x")
        self.assertIn(p, self.table.files())
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        os.mkdir(os.path.join(other.name, "q"))
        r = os.path.join(other.name, "q", "b.jsonl")
        open(r, "w").close()
        self.root = other.name
        self.assertEqual(list(self.table.files()), [r])

    def test_fresh_answer_reused(self):
        self.table.files()
        p = self._write("a.jsonl", "x")
        self.assertEqual(self.table.files(fresh_s=60), {})      # the last look
        self.assertIn(p, self.table.files(fresh_s=60, hot_names={"a"}))
        self.assertIn(p, self.table.files(fresh_s=60))

    def test_newest_first(self):
        a = self._write("a.jsonl", "x", age=100)
        b = self._write("b.jsonl", "x")
        rows = filetable.newest_first(self.table.files())
        self.assertEqual([str(p) for _, p in rows], [b, a])


if __name__ == "__main__":
    unittest.main()
