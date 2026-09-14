"""The automatic file history of a documents project (history.py).

Run for real against git in a temp folder laid out like a projects root:

* a snapshot records who changed what (a task, or you), and a second one with
  nothing changed commits nothing;
* the log lists versions with added/removed line counts, renames and deletions,
  for the project and for one file;
* a version's content, its diff against the file now and against its parent;
* restore puts an old version back, snapshots what was there first, and adds a
  "Restored … from …" snapshot of its own; a deleted file is listed and restored;
* what is not kept: task folders' records and chats (their notes are), files
  over the size limit, .history itself;
* never a .git in the project folder, and the projects backup still commits the
  project's files as plain files, without the history's database;
* the scheduler's tick: requests are taken, a scan runs on its interval only.
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

import backup  # noqa: E402
import history  # noqa: E402

GIT = shutil.which("git")
TASK = [{"id": "room-abc", "title": "Sort the leasing papers"}]


@unittest.skipUnless(GIT, "git is not installed")
class FileHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "EnsembleProjects"
        self.home = self.root / "Motors"
        (self.home / "Leasing").mkdir(parents=True)
        (self.home / "project.json").write_text(json.dumps({"id": "proj-1", "kind": "documents"}), encoding="utf-8")
        self.h = str(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def test_snapshot_log_and_who(self):
        self.write("Leasing/offer.md", "one\ntwo\nthree\n")
        first = history.snapshot(self.h, None, reason="manual")
        self.assertTrue(first["ok"] and first["committed"], first)
        self.assertFalse((self.home / ".git").exists())
        again = history.snapshot(self.h, TASK)
        self.assertTrue(again["ok"])
        self.assertFalse(again["committed"], "nothing changed, nothing committed")
        self.write("Leasing/offer.md", "one\nTWO\nthree\nfour\n")
        second = history.snapshot(self.h, TASK, reason="turn")
        self.assertTrue(second["committed"], second)
        log = history.log(self.h)["entries"]
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["who"]["kind"], "task")
        self.assertEqual(log[0]["who"]["tasks"], TASK)
        self.assertEqual(log[0]["who"]["reason"], "turn")
        self.assertEqual(log[0]["files"], [{"path": "Leasing/offer.md", "status": "M", "added": 2, "removed": 1, "binary": False}])
        self.assertEqual(log[1]["who"]["kind"], "you")
        self.assertEqual(log[1]["files"][0]["status"], "A")
        self.assertEqual(log[1]["files"][0]["added"], 3)
        self.assertEqual(history.log(self.h, "Leasing/offer.md")["entries"][0]["rev"], log[0]["rev"])

    def test_rename_delete_and_restore_deleted(self):
        self.write("a.md", "alpha\n")
        self.write("b.txt", "beta\n")
        history.snapshot(self.h)
        os.replace(self.home / "a.md", self.home / "Leasing" / "a-renamed.md")
        (self.home / "b.txt").unlink()
        history.snapshot(self.h, TASK)
        files = {f["path"]: f for f in history.log(self.h)["entries"][0]["files"]}
        self.assertEqual(files["Leasing/a-renamed.md"]["status"], "R")
        self.assertEqual(files["Leasing/a-renamed.md"]["from"], "a.md")
        self.assertEqual(files["b.txt"]["status"], "D")
        gone = history.deleted(self.h)["files"]
        self.assertEqual([g["path"] for g in gone], ["b.txt"], "a rename is not a deletion")
        self.assertEqual(gone[0]["who"]["tasks"], TASK)
        res = history.restore(self.h, gone[0]["from"], "b.txt")
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.home / "b.txt").read_text(encoding="utf-8"), "beta\n")
        self.assertEqual(history.deleted(self.h)["files"], [])
        top = history.log(self.h)["entries"][0]
        self.assertTrue(top["subject"].startswith("Restored b.txt from "), top["subject"])
        self.assertEqual(top["who"]["kind"], "you")
        self.assertEqual(top["who"]["reason"], "restore")

    def test_file_diff_and_restore_snapshot_first(self):
        self.write("letter.md", "Dear sir\nold line\n")
        v1 = history.snapshot(self.h)["rev"]
        self.write("letter.md", "Dear sir\nnew line\n")
        v2 = history.snapshot(self.h, TASK)["rev"]
        code, data = history.file_at(self.h, v1, "letter.md")
        self.assertEqual((code, data), (200, b"Dear sir\nold line\n"))
        self.assertEqual(history.file_at(self.h, v1, "../escape.md")[0], 400)
        self.assertEqual(history.file_at(self.h, "HEAD", "letter.md")[0], 400, "only commit ids")
        self.assertEqual(history.file_at(self.h, v1, "nope.md")[0], 404)
        code, d = history.diff(self.h, v1, "letter.md", "current")
        self.assertEqual(code, 200)
        self.assertIn("-old line\n+new line\n", d["diff"])
        self.assertIn("@@ -1,2 +1,2 @@", d["diff"])
        code, d = history.diff(self.h, v2, "letter.md", "parent")
        self.assertIn("-old line\n+new line\n", d["diff"])
        code, d = history.diff(self.h, v1, "letter.md", "parent")
        self.assertIn("--- /dev/null", d["diff"])
        # An unsaved edit is kept as a snapshot before the old version goes back.
        self.write("letter.md", "Dear sir\nedit nobody snapshotted\n")
        self.assertEqual(history.status(self.h)["files"], [{"path": "letter.md", "status": "M", "staged": False}])
        code, d = history.diff(self.h, "", "letter.md")
        self.assertIn("+edit nobody snapshotted", d["diff"])
        res = history.restore(self.h, v1, "letter.md", who_now=TASK)
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.home / "letter.md").read_text(encoding="utf-8"), "Dear sir\nold line\n")
        ents = history.log(self.h, "letter.md")["entries"]
        self.assertEqual(len(ents), 4)
        self.assertEqual(ents[1]["who"]["tasks"], TASK)
        self.assertEqual(ents[1]["who"]["reason"], "before restore")
        self.assertEqual(history.file_at(self.h, ents[1]["rev"], "letter.md")[1], b"Dear sir\nedit nobody snapshotted\n")
        self.assertEqual(history.status(self.h)["files"], [])

    def test_binary_diff_says_so(self):
        (self.home / "scan.pdf").write_bytes(b"%PDF\x00\x01")
        v1 = history.snapshot(self.h)["rev"]
        (self.home / "scan.pdf").write_bytes(b"%PDF\x00\x02")
        history.snapshot(self.h)
        self.assertTrue(history.diff(self.h, v1, "scan.pdf")[1]["binary"])
        self.assertTrue(history.log(self.h)["entries"][0]["files"][0]["binary"])

    def test_what_is_not_kept(self):
        task = self.home / "sort_the_leasing_papers"
        (task / ".claude").mkdir(parents=True)
        (task / "task.json").write_text("{}", encoding="utf-8")
        (task / "chat.json").write_text("[]", encoding="utf-8")
        (task / "run.jsonl").write_text("{}", encoding="utf-8")
        (task / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (task / "NOTES.md").write_text("notes\n", encoding="utf-8")
        (task / "draft.docx").write_bytes(b"PK\x03\x04")
        # A folder of the project's own that happens to hold a chat.json is not a task folder.
        self.write("Leasing/chat.json", "kept\n")
        self.write("_linked/room-x/chat.json", "[]")
        self.write("big.bin", "")
        with mock.patch.object(history, "MAX_FILE_BYTES", 10):
            self.write("big.bin", "x" * 11)
            res = history.snapshot(self.h)
        self.assertEqual(res["skipped"], [{"path": "big.bin", "size": 11}])
        tracked = subprocess.run(["git", f"--git-dir={self.h}/.history", "ls-files"], capture_output=True,
                                 text=True, encoding="utf-8").stdout.split()
        self.assertEqual(sorted(tracked), ["Leasing/chat.json", "sort_the_leasing_papers/NOTES.md",
                                           "sort_the_leasing_papers/draft.docx"])

    def test_paths(self):
        h = self.h
        self.assertEqual(history.rel_path(h, "Leasing\\offer.md"), "Leasing/offer.md")
        self.assertEqual(history.rel_path(h, os.path.join(h, "Leasing", "x.md")), "Leasing/x.md")
        for bad in ("", ".", "..", "../x", "a/../../x", ".history/HEAD", os.path.dirname(h)):
            self.assertEqual(history.rel_path(h, bad), "", bad)

    def test_a_name_starting_with_a_space_is_that_file(self):
        self.write(" leading.txt", "one\n")
        v1 = history.snapshot(self.h)["rev"]
        self.assertEqual(history.rel_path(self.h, " leading.txt"), " leading.txt")
        self.assertEqual(history.rel_path(self.h, "   "), "")
        self.assertEqual(history.log(self.h, " leading.txt")["entries"][0]["rev"], v1)
        self.assertEqual(history.file_at(self.h, v1, " leading.txt"), (200, b"one\n"))
        self.write(" leading.txt", "two\n")
        history.snapshot(self.h)
        res = history.restore(self.h, v1, " leading.txt")
        self.assertTrue(res["ok"], res)
        self.assertEqual((self.home / " leading.txt").read_text(encoding="utf-8"), "one\n")

    def test_restoring_the_version_already_there_is_still_a_snapshot(self):
        self.write("a.md", "same\n")
        v1 = history.snapshot(self.h)["rev"]
        res = history.restore(self.h, v1, "a.md")
        self.assertTrue(res["ok"] and res["committed"] and res["rev"], res)
        top = history.log(self.h)["entries"][0]
        self.assertEqual(top["rev"], res["rev"])
        self.assertTrue(top["subject"].startswith("Restored a.md from "), top["subject"])
        self.assertEqual((top["who"]["kind"], top["who"]["reason"]), ("you", "restore"))

    def test_a_restore_the_history_cannot_record_is_not_a_success(self):
        self.write("a.md", "old\n")
        v1 = history.snapshot(self.h)["rev"]
        self.write("a.md", "new\n")
        history.snapshot(self.h)
        real = history.snapshot

        def failing(home, who=None, reason="scan", message="", allow_empty=False):
            if reason == "restore":
                return {"ok": False, "committed": False, "rev": "", "files": 0, "skipped": [], "msg": "disk full"}
            return real(home, who, reason, message, allow_empty)
        with mock.patch.object(history, "snapshot", failing):
            res = history.restore(self.h, v1, "a.md")
        self.assertFalse(res["ok"])
        self.assertTrue(res["written"])
        self.assertIn("could not record the restore: disk full", res["error"])
        self.assertEqual((self.home / "a.md").read_text(encoding="utf-8"), "old\n")

    @staticmethod
    def data(text: str) -> str:
        return f"data {len(text.encode('utf-8'))}\n{text}\n"

    def fast_import(self, changes):
        """Commits made straight into the history: [(message, [file commands])]."""
        history.ensure(self.h)
        out = []
        for i, (msg, cmds) in enumerate(changes, 1):
            out.append(f"commit refs/heads/main\nmark :{i}\ncommitter you <{history.EMAIL}> {1790000000 + i} +0000\n"
                       + self.data(f"{msg}\n\nEnsemble-Who: you"))
            if i > 1:
                out.append(f"from :{i - 1}\n")
            out.extend(cmds)
        r = subprocess.run(["git", f"--git-dir={self.h}/.history", "fast-import", "--quiet"],
                           input="".join(out).encode("utf-8"), capture_output=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_every_version_and_every_deleted_file_can_be_reached(self):
        changes = [(f"v{i}", ["M 100644 inline notes.txt\n" + self.data(f"version {i}")]) for i in range(1, 106)]
        changes.append(("add", [f"M 100644 inline gone/{n:03d}.txt\n" + self.data(str(n)) for n in range(205)]))
        changes.append(("delete", [f"D gone/{n:03d}.txt\n" for n in range(205)]))
        self.fast_import(changes)
        self.write("notes.txt", "version 105\n")
        revs, skip = [], 0
        while True:
            page = history.log(self.h, "notes.txt", limit=100, skip=skip)
            revs += [e["rev"] for e in page["entries"]]
            skip += len(page["entries"])
            if not page["more"]:
                break
        self.assertEqual(len(set(revs)), 105)
        self.assertEqual(history.file_at(self.h, revs[-1], "notes.txt"), (200, b"version 1"))
        first = history.deleted(self.h)
        self.assertEqual((len(first["files"]), first["more"]), (200, True))
        last = first["files"][-1]
        rest = history.deleted(self.h, after=(last["rev"], last["path"]))
        self.assertEqual((len(rest["files"]), rest["more"]), (5, False))
        self.assertEqual(len({f["path"] for f in first["files"] + rest["files"]}), 205)

    def test_deleted_files_page_from_a_place_so_a_restore_between_pages_skips_nothing(self):
        self.fast_import([("add", [f"M 100644 inline gone/{n:03d}.txt\n" + self.data(str(n)) for n in range(205)]),
                          ("delete", [f"D gone/{n:03d}.txt\n" for n in range(205)])])
        first = history.deleted(self.h)["files"]
        place = (first[-1]["rev"], first[-1]["path"])
        self.write(first[0]["path"], "back\n")           # restored between two pages
        again = history.deleted(self.h, through=place)
        self.assertEqual((len(again["files"]), again["more"]), (199, True), "the restored file drops out")
        rest = history.deleted(self.h, after=place)
        self.assertEqual((len(rest["files"]), rest["more"]), (5, False))
        paths = [f["path"] for f in again["files"] + rest["files"]]
        self.assertEqual(len(set(paths)), 204)
        self.assertNotIn(first[0]["path"], paths)
        self.assertIn("gone/201.txt", paths)
        # The page after a place that has itself come back still starts there.
        self.write(place[1], "back\n")
        self.assertEqual(len(history.deleted(self.h, after=place)["files"]), 5)

    def test_both_log_reads_see_the_same_commits(self):
        self.write("a.md", "1\n")
        history.snapshot(self.h)
        self.write("a.md", "1\n2\n")
        history.snapshot(self.h)
        real, landed = history._git, []

        def git(home, *args, **kw):
            r = real(home, *args, **kw)
            if args[:1] == ("log",) and "--name-status" in args and not landed:
                landed.append(True)
                for text in ("1\n2\n3\n", "1\n2\n3\n4\n"):   # two snapshots between the reads
                    self.write("a.md", text)
                    history.snapshot(self.h)
            return r
        with mock.patch.object(history, "_git", git):
            ents = history.log(self.h, limit=1)["entries"]
        self.assertTrue(landed)
        self.assertEqual(len(ents), 1)
        self.assertEqual((ents[0]["files"][0]["added"], ents[0]["files"][0]["removed"]), (1, 0))

    def test_backup_keeps_plain_files_after_history(self):
        self.write("Leasing/offer.md", "one\n")
        history.snapshot(self.h)
        self.assertTrue(history.exists(self.h))
        res = backup.run_backup(self.root)
        self.assertTrue(res["ok"], res)
        out = subprocess.run(["git", "-C", str(self.root), "ls-files", "-s"], capture_output=True,
                             text=True, encoding="utf-8").stdout
        self.assertIn("100644", out)
        self.assertIn("Motors/Leasing/offer.md", out)
        self.assertNotIn("160000", out, "no gitlink")
        self.assertNotIn(".history", out)
        self.assertFalse((self.home / ".git").exists())

    def test_tick_takes_requests_and_scans_on_interval(self):
        self.write("a.md", "a\n")
        homes = {"proj-1": self.h}
        who = mock.Mock(return_value=[])
        last = {}
        done = history.tick(homes, who, last, now=1000.0)
        self.assertEqual([d["committed"] for d in done], [True], "first tick scans")
        self.write("a.md", "b\n")
        self.assertEqual(history.tick(homes, who, last, now=1010.0), [], "not due yet")
        history.request(self.h, TASK, "report")
        history.request(os.path.join(self.tmp.name, "elsewhere"), TASK)     # not a documents project: dropped
        done = history.tick(homes, who, last, now=1020.0)
        self.assertEqual(len(done), 1)
        self.assertEqual(history.log(self.h)["entries"][0]["who"]["tasks"], TASK)
        self.write("a.md", "c\n")
        who.return_value = [{"id": "room-2", "title": "Other"}]
        done = history.tick(homes, who, last, now=1020.0 + history.SCAN_INTERVAL_S)
        self.assertEqual(history.log(self.h)["entries"][0]["who"]["tasks"], [{"id": "room-2", "title": "Other"}])
        ends = mock.Mock(return_value=[(self.h, TASK)])
        self.write("a.md", "d\n")
        history.tick(homes, who, last, turn_ends=ends, now=1030.0 + history.SCAN_INTERVAL_S)
        self.assertEqual(history.log(self.h)["entries"][0]["who"]["reason"], "turn")


if __name__ == "__main__":
    unittest.main()
