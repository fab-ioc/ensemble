"""A documents project's files from the page: upload, mkdir, move, delete.

* each endpoint's happy path, and each change is a snapshot credited to the
  person by name ("sam"), with the reason, that Recent changes shows;
* every refusal: a code project, a path outside the folder or into what the
  hub keeps there (.history, _linked, project.json, a task's folder), a name
  already taken, a folder into itself, a file over 50 MB, not the page;
* an upload is written under a hidden temp name and renamed, a 60 MB body is
  refused before a byte of it is read and nothing lands on disk;
* a deleted file shows in the Deleted view and can be restored, even one no
  snapshot had seen; a moved folder keeps its files' history;
* a snapshot that fails does not fail the change.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import history  # noqa: E402
import workspace_search  # noqa: E402

GIT = shutil.which("git")
NODE = shutil.which("node")
PORT = 8798
MB = 1024 * 1024


class Body(io.RawIOBase):
    """A request body served lazily; notes what the reply held at the first read."""

    def __init__(self, size: int, fill: bytes = b"x", on_read=None):
        self.left, self.fill, self.on_read, self.read_bytes = size, fill, on_read, 0

    def readable(self):
        return True

    def read(self, n=-1):
        if self.on_read:
            self.on_read(self)
            self.on_read = None
        n = self.left if n is None or n < 0 else min(n, self.left)
        self.left -= n
        self.read_bytes += n
        return (self.fill * (n // len(self.fill) + 1))[:n]


@unittest.skipUnless(GIT, "git is not installed")
class FilesApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard.getpass, "getuser", lambda: "sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(history._take_requests)
        ok, proj, _ = dashboard.register_project("Motors")
        self.assertTrue(ok)
        self.pid = proj["id"]
        self.home = self.root / "Motors"
        self.h = str(self.home)
        self.assertTrue(dashboard.set_project_kind(self.pid, "documents")[0])
        history._take_requests()

    # ---- requests ----

    def request(self, path, body: bytes | io.RawIOBase = b"", headers=None, page=True, length=True):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
        h.requestline = f"POST {path} HTTP/1.1"
        size = body.left if isinstance(body, Body) else len(body)
        h.headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/octet-stream", **(headers or {})}
        if length:
            h.headers["Content-Length"] = str(size)
        if page:
            h.headers.update({"Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}", "Origin": f"http://127.0.0.1:{PORT}"})
        h.rfile = body if isinstance(body, Body) else io.BytesIO(body)
        h.wfile = io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        self.handler = h
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    def upload(self, rel, data: bytes | io.RawIOBase, overwrite=False, project=None, **kw):
        q = {"project": self.pid if project is None else project, "path": rel}
        if overwrite:
            q["overwrite"] = "1"
        return self.request(f"/api/files/upload?{urlencode(q)}", data, **kw)

    def op(self, name, page=True, **body):
        return self.request(f"/api/files/{name}", json.dumps({"project": self.pid, **body}).encode(),
                            {"Content-Type": "application/json"}, page=page)

    # ---- reading back ----

    def top(self, path=""):
        return history.log(self.h, path)["entries"][0]

    def listed(self):
        return workspace_search.list_files(self.h)[0]

    def leftovers(self):
        return [str(p) for p in self.home.rglob("*.upload.tmp")]

    # ---- upload ----

    def test_upload_lands_the_file_and_credits_the_person(self):
        status, res = self.upload("ads/2026/photo.jpg", b"\xff\xd8jpeg\x00bytes")
        self.assertEqual(status, 200, res)
        self.assertEqual((res["path"], res["size"]), ("ads/2026/photo.jpg", 12))
        self.assertTrue(res["snapshot"]["ok"] and res["snapshot"]["committed"], res)
        self.assertEqual((self.home / "ads" / "2026" / "photo.jpg").read_bytes(), b"\xff\xd8jpeg\x00bytes")
        self.assertIn("ads/2026/photo.jpg", self.listed())
        top = self.top()
        self.assertEqual(top["rev"], res["snapshot"]["rev"])
        self.assertEqual(top["who"], {"kind": "user", "tasks": [], "name": "sam", "label": "sam", "reason": "upload"})
        self.assertEqual([f["path"] for f in top["files"]], ["ads/2026/photo.jpg"])
        status, _ = dashboard.ws_files(self.h)
        self.assertEqual(status, 200)
        self.assertEqual(self.leftovers(), [])

    def test_upload_over_an_existing_file_needs_overwrite_and_keeps_the_old_version(self):
        self.assertEqual(self.upload("offer.md", b"price 300\n")[0], 200)
        status, res = self.upload("offer.md", b"price 280\n")
        self.assertEqual((status, res["error"]), (409, "exists"))
        self.assertIn("offer.md", res["message"])
        self.assertEqual((self.home / "offer.md").read_bytes(), b"price 300\n")
        status, res = self.upload("offer.md", b"price 280\n", overwrite=True)
        self.assertEqual(status, 200, res)
        self.assertEqual((self.home / "offer.md").read_bytes(), b"price 280\n")
        self.assertEqual(len(history.log(self.h, "offer.md")["entries"]), 2)
        (self.home / "folder").mkdir()
        status, res = self.upload("folder", b"x", overwrite=True)
        self.assertEqual((status, res["error"]), (409, "exists"), "a file never replaces a folder")
        (self.home / "a.txt").write_bytes(b"a")
        status, res = self.upload("a.txt/b.txt", b"x")
        self.assertEqual((status, res["error"]), (409, "not_a_folder"))

    def test_upload_is_written_under_a_temp_name_then_renamed(self):
        seen = {}

        def midway(body):
            seen["final"] = (self.home / "ads" / "photo.jpg").exists()
            seen["tmp"] = [p.name for p in (self.home / "ads").iterdir()]
            seen["listed"] = self.listed()
            seen["status"] = history.status(self.h)["files"]
        status, res = self.upload("ads/photo.jpg", Body(3 * MB + 5, b"ab", midway))
        self.assertEqual(status, 200, res)
        self.assertFalse(seen["final"], "the file is not under its name while it arrives")
        self.assertEqual(len(seen["tmp"]), 1)
        self.assertTrue(re.fullmatch(r"\.photo\.jpg\..+\.upload\.tmp", seen["tmp"][0]), seen["tmp"])
        self.assertEqual(seen["listed"], ["project.json"], "nor in the tree")
        self.assertEqual(seen["status"], [], "nor taken by a snapshot")
        self.assertEqual((self.home / "ads" / "photo.jpg").stat().st_size, 3 * MB + 5)
        self.assertEqual(self.leftovers(), [])

    def test_a_task_folder_made_while_the_file_arrives_is_still_refused(self):
        (self.home / "safe").mkdir()

        def midway(body):
            (self.home / "safe" / "task.json").write_text("{}", encoding="utf-8")
        status, res = self.upload("safe/new.txt", Body(1000, b"n", midway))
        self.assertEqual((status, res["error"]), (400, "path_not_allowed"))
        self.assertFalse((self.home / "safe" / "new.txt").exists())
        self.assertEqual(self.leftovers(), [])

    def test_the_path_is_checked_under_the_lock_for_every_change(self):
        (self.home / "a.txt").write_bytes(b"a")
        seen = []
        real = dashboard.files_path

        def spy(home, path):
            seen.append(history.lock(home)._is_owned())
            return real(home, path)
        with mock.patch.object(dashboard, "files_path", spy):
            self.op("mkdir", path="x")
            self.op("move", **{"from": "a.txt", "to": "b.txt"})
            self.op("delete", path="b.txt")
            self.upload("c.txt", b"c")
        self.assertEqual(seen, [True, True, True, True, False, True],
                         "mkdir, move (from and to), delete; an upload before its body and again before its rename")

    def test_a_body_that_stops_short_is_not_saved(self):
        class Short(Body):
            def read(self, n=-1):
                return b"" if self.read_bytes >= 100 else super().read(min(n, 100))
        status, res = self.upload("ads/cut.pdf", Short(10_000))
        self.assertEqual((status, res["error"]), (400, "incomplete"))
        self.assertFalse((self.home / "ads" / "cut.pdf").exists())
        self.assertEqual(self.leftovers(), [])

    def test_a_60_mb_body_is_refused_before_it_is_read(self):
        at_first_read = {}
        body = Body(60 * MB, b"\x00", lambda b: at_first_read.setdefault("reply", self.handler.wfile.getvalue()))
        status, res = self.upload("ads/huge.mov", body)
        self.assertEqual((status, res["error"]), (413, "too_large"))
        self.assertIn("50 MB", res["message"])
        self.assertIn(b"413", at_first_read["reply"], "the reply was sent before any of the body was read")
        self.assertTrue(self.handler.close_connection)
        self.assertEqual(list(self.home.rglob("huge*")), [])
        self.assertFalse((self.home / "ads").exists(), "nothing is made for a refused file")
        self.assertEqual(self.leftovers(), [])
        status, res = self.upload("ads/ok.mov", Body(history.MAX_FILE_BYTES, b"\x01"))
        self.assertEqual(status, 200, "exactly 50 MB is allowed")
        # Far past the limit, the body is not read at all.
        status, res = self.upload("ads/huger.mov", Body(1024 * MB))
        self.assertEqual(status, 413)
        self.assertEqual(self.handler.rfile.read_bytes, 0)

    def test_an_upload_without_a_length_is_refused(self):
        status, res = self.upload("a.txt", b"abc", length=False)
        self.assertEqual((status, res["error"]), (411, "length_required"))
        self.assertFalse((self.home / "a.txt").exists())

    # ---- mkdir ----

    def test_mkdir(self):
        status, res = self.op("mkdir", path="Leasing/2026/Q3")
        self.assertEqual(status, 200, res)
        self.assertEqual((res["path"], res["created"]), ("Leasing/2026/Q3", True))
        self.assertTrue((self.home / "Leasing" / "2026" / "Q3").is_dir())
        self.assertIn("ok", res["snapshot"])
        status, res = self.op("mkdir", path="Leasing/2026")
        self.assertEqual((status, res["created"]), (200, False), "a folder already there is fine")
        (self.home / "Leasing" / "note.md").write_text("n", encoding="utf-8")
        status, res = self.op("mkdir", path="Leasing/note.md")
        self.assertEqual((status, res["error"]), (409, "exists"))
        self.assertIn("note.md", res["message"])

    # ---- move ----

    def test_move_a_file_and_refusals(self):
        self.upload("a.txt", b"one\ntwo\n")
        self.upload("b.txt", b"bee\n")
        status, res = self.op("move", **{"from": "a.txt", "to": "docs/renamed.txt"})
        self.assertEqual(status, 200, res)
        self.assertEqual((res["from"], res["to"]), ("a.txt", "docs/renamed.txt"))
        self.assertFalse((self.home / "a.txt").exists())
        self.assertEqual((self.home / "docs" / "renamed.txt").read_bytes(), b"one\ntwo\n")
        top = self.top()
        self.assertEqual((top["who"]["label"], top["who"]["reason"]), ("sam", "move"))
        self.assertEqual(top["files"], [{"path": "docs/renamed.txt", "status": "R", "from": "a.txt",
                                         "added": 0, "removed": 0, "binary": False}])
        status, res = self.op("move", **{"from": "b.txt", "to": "docs/renamed.txt"})
        self.assertEqual((status, res["error"]), (409, "exists"))
        self.assertEqual((self.home / "b.txt").read_bytes(), b"bee\n")
        status, res = self.op("move", **{"from": "b.txt", "to": "docs/renamed.txt", "overwrite": True})
        self.assertEqual(status, 200, res)
        self.assertEqual((self.home / "docs" / "renamed.txt").read_bytes(), b"bee\n")
        status, res = self.op("move", **{"from": "gone.txt", "to": "x.txt"})
        self.assertEqual((status, res["error"]), (404, "not_found"))
        status, res = self.op("move", **{"from": "docs", "to": "docs/inner/docs"})
        self.assertEqual((status, res["error"]), (400, "into_itself"))
        status, res = self.op("move", **{"from": "docs", "to": "DOCS/x"})
        self.assertEqual((status, res["error"]), (400, "into_itself"), "whatever the case")
        status, res = self.op("move", **{"from": "docs/renamed.txt", "to": "docs", "overwrite": True})
        self.assertEqual((status, res["error"]), (409, "exists"), "never the folder it is in")
        self.assertTrue((self.home / "docs" / "renamed.txt").is_file())

    def test_every_accepted_name_is_listed_and_restorable(self):
        for name in ("notes.tmpl", "a.tmp.txt", "gitignore", ".gitkeep", "tmp/x.md", "~draft.md",
                     "node_modules.md", "my_node_modules/x.md"):
            status, res = self.upload(name, b"kept\n")
            self.assertEqual(status, 200, (name, res))
            self.assertTrue(res["snapshot"]["committed"], name)
            self.assertIn(name, self.listed())
            self.assertEqual(self.op("delete", path=name)[0], 200)
            self.assertIn(name, [d["path"] for d in history.deleted(self.h)["files"]])

    def test_a_folder_the_tree_shows_stays_usable_whatever_its_case(self):
        # The tree skips exactly "node_modules"; "Node_Modules" is an ordinary folder it lists.
        (self.home / "Node_Modules").mkdir()
        (self.home / "Node_Modules" / "x.md").write_bytes(b"x\n")
        self.assertIn("Node_Modules/x.md", self.listed())
        status, res = self.upload("Node_Modules/y.md", b"y\n")
        self.assertEqual(status, 200, res)
        self.assertIn("Node_Modules/y.md", self.listed())
        status, res = self.op("move", **{"from": "Node_Modules/y.md", "to": "Node_Modules/z.md"})
        self.assertEqual(status, 200, res)
        status, res = self.op("delete", path="Node_Modules/x.md")
        self.assertEqual(status, 200, res)
        self.assertEqual(sorted(self.listed()), ["Node_Modules/z.md", "project.json"])

    def test_moving_onto_itself_does_not_credit_pending_work_to_the_person(self):
        self.upload("a.txt", b"a")
        (self.home / "pending.txt").write_bytes(b"a task wrote this\n")
        status, res = self.op("move", **{"from": "a.txt", "to": "a.txt"})
        self.assertEqual(status, 200, res)
        self.assertFalse(res["snapshot"]["committed"])
        top = self.top()
        self.assertEqual((top["who"]["kind"], top["who"]["reason"], [f["path"] for f in top["files"]]),
                         ("you", "before move", ["pending.txt"]))

    @unittest.skipUnless(os.name == "nt", "a case-insensitive folder")
    def test_a_rename_that_only_changes_case(self):
        self.upload("report.pdf", b"%PDF")
        status, res = self.op("move", **{"from": "report.pdf", "to": "Report.pdf"})
        self.assertEqual(status, 200, res)
        self.assertEqual([p.name for p in self.home.iterdir() if p.suffix == ".pdf"], ["Report.pdf"])

    def test_moving_a_folder_keeps_its_files_history(self):
        self.upload("ads/one.md", b"first\n")
        self.upload("ads/one.md", b"first\nsecond\n", overwrite=True)
        self.upload("ads/sub/two.md", b"two\n")
        (self.home / "ads" / "sub" / "unseen.md").write_bytes(b"not yet snapshotted\n")
        status, res = self.op("move", **{"from": "ads", "to": "archive/ads-2026"})
        self.assertEqual(status, 200, res)
        self.assertTrue(res["snapshot"]["committed"])
        self.assertFalse((self.home / "ads").exists())
        self.assertEqual((self.home / "archive" / "ads-2026" / "sub" / "two.md").read_bytes(), b"two\n")
        top = self.top()
        self.assertEqual(top["who"]["label"], "sam")
        self.assertEqual(sorted((f["from"], f["path"]) for f in top["files"]),
                         [("ads/one.md", "archive/ads-2026/one.md"), ("ads/sub/two.md", "archive/ads-2026/sub/two.md"),
                          ("ads/sub/unseen.md", "archive/ads-2026/sub/unseen.md")])
        self.assertEqual(len(history.log(self.h, "archive/ads-2026/one.md")["entries"]), 3, "both versions and the move")
        before = history.log(self.h)["entries"][1]
        self.assertEqual((before["who"]["reason"], [f["path"] for f in before["files"]]),
                         ("before move", ["ads/sub/unseen.md"]), "what no snapshot had seen is kept first")

    # ---- delete ----

    def test_delete_a_file_and_restore_it_from_the_deleted_view(self):
        self.upload("ads/photo.jpg", b"\xff\xd8photo")
        status, res = self.op("delete", path="ads/photo.jpg")
        self.assertEqual(status, 200, res)
        self.assertEqual(res["path"], "ads/photo.jpg")
        self.assertFalse((self.home / "ads" / "photo.jpg").exists())
        self.assertEqual((self.top()["who"]["label"], self.top()["who"]["reason"]), ("sam", "delete"))
        gone = history.deleted(self.h)["files"]
        self.assertEqual([(d["path"], d["who"]["label"]) for d in gone], [("ads/photo.jpg", "sam")])
        back = history.restore(self.h, gone[0]["from"], "ads/photo.jpg")
        self.assertTrue(back["ok"], back)
        self.assertEqual((self.home / "ads" / "photo.jpg").read_bytes(), b"\xff\xd8photo")
        status, res = self.op("delete", path="ads/photo.jpg/x")
        self.assertEqual((status, res["error"]), (404, "not_found"))

    def test_delete_a_folder_even_what_no_snapshot_had_seen(self):
        self.upload("old/a.md", b"a\n")
        (self.home / "old" / "deep").mkdir()
        (self.home / "old" / "deep" / "fresh.md").write_bytes(b"dropped in a moment ago\n")
        (self.home / "old" / "deep" / "locked.md").write_bytes(b"read-only\n")
        os.chmod(self.home / "old" / "deep" / "locked.md", 0o444)
        status, res = self.op("delete", path="old")
        self.assertEqual(status, 200, res)
        self.assertFalse((self.home / "old").exists())
        self.assertEqual(sorted(d["path"] for d in history.deleted(self.h)["files"]),
                         ["old/a.md", "old/deep/fresh.md", "old/deep/locked.md"])
        fresh = next(d for d in history.deleted(self.h)["files"] if d["path"] == "old/deep/fresh.md")
        self.assertTrue(history.restore(self.h, fresh["from"], fresh["path"])["ok"])
        self.assertEqual((self.home / "old" / "deep" / "fresh.md").read_bytes(), b"dropped in a moment ago\n")

    # ---- refusals common to all ----

    def test_a_code_project_is_refused(self):
        dashboard.set_project_kind(self.pid, "code")
        (self.home / "a.txt").write_bytes(b"a")
        for status, res in (self.upload("b.txt", b"b"), self.op("mkdir", path="x"),
                            self.op("move", **{"from": "a.txt", "to": "c.txt"}), self.op("delete", path="a.txt")):
            self.assertEqual((status, res["error"]), (400, "documents_only"))
            self.assertTrue(res["message"])
        self.assertTrue((self.home / "a.txt").exists())
        self.assertFalse((self.home / "b.txt").exists())
        self.assertEqual(self.upload("b.txt", b"b", project="proj-nope")[0], 404)

    def test_a_plain_request_is_enough(self):
        # The contract is a plain API: no page cookie, no Origin (like the spec's curl).
        (self.home / "a.txt").write_bytes(b"a")
        status, res = self.upload("b.txt", b"b", page=False)
        self.assertEqual(status, 200, res)
        status, res = self.request("/api/files/delete", json.dumps({"project": self.pid, "path": "a.txt"}).encode(),
                                   {"Content-Type": "application/json"}, page=False)
        self.assertEqual(status, 200, res)
        self.assertFalse((self.home / "a.txt").exists())
        self.assertEqual((self.home / "b.txt").read_bytes(), b"b")

    def test_a_request_from_another_site_is_refused(self):
        # A page on another site, through the person's own browser, sends its
        # own Origin: refused before anything is read or changed. A program
        # sends no Origin and passes (the test above).
        (self.home / "a.txt").write_bytes(b"a")
        status, res = self.upload("b.txt", b"b", page=False, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403, res)
        self.assertEqual(res["error"], "cross_origin")
        self.assertFalse((self.home / "b.txt").exists())
        status, res = self.request("/api/files/delete", json.dumps({"project": self.pid, "path": "a.txt"}).encode(),
                                   {"Content-Type": "application/json", "Origin": "http://evil.example"}, page=False)
        self.assertEqual(status, 403, res)
        self.assertTrue((self.home / "a.txt").exists())

    def test_a_path_through_a_linked_folder_is_refused(self):
        target = self.home / "target"
        target.mkdir()
        link = self.home / "link"
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            made = r.returncode == 0
        else:
            try:
                os.symlink(target, link, target_is_directory=True)
                made = True
            except OSError:
                made = False
        if not made:
            self.skipTest("cannot make a folder link here")
        self.addCleanup(lambda: os.path.lexists(link) and (os.rmdir(link) if os.name == "nt" else os.unlink(link)))
        for status, res in (self.upload("link/new.txt", b"x"), self.op("mkdir", path="link/sub"),
                            self.op("delete", path="link")):
            self.assertEqual((status, res["error"]), (400, "path_not_allowed"))
            self.assertIn("link to another folder", res["message"])
        self.assertEqual(list(target.iterdir()), [])
        status, res = self.upload("target/new.txt", b"x")
        self.assertEqual(status, 200, res)
        self.assertIn("target/new.txt", self.listed())

    def test_paths_outside_or_into_what_the_hub_keeps_are_refused(self):
        task = self.home / "sort-papers"
        task.mkdir()
        (task / "task.json").write_text("{}", encoding="utf-8")
        (task / "notes.md").write_text("n", encoding="utf-8")
        (self.home / "_linked").mkdir()
        (self.home / "ok.txt").write_bytes(b"ok")
        outside = self.root / "Other"
        outside.mkdir()
        self.assertTrue(history.snapshot(self.h)["ok"])
        bad = ["", " ", "../Other/x.txt", "a/../../x.txt", "/ok2.txt", "a\\b.txt", "C:/x.txt", "a//b.txt", "./a.txt",
               "a/./b.txt", ".history/HEAD", ".HISTORY/x", "_linked/chat.md", "_Linked", "project.json", "Project.JSON",
               "sort-papers", "sort-papers/notes.md", "sort-papers/new/x.md", "SORT-PAPERS/x.md", "docs/task.json",
               "con.txt", "a/NUL", "x:stream", "trailing.", "trailing ", "a?.txt", "x" * 1100,
               # names the history never keeps: they could not be put back
               "draft.tmp", "DRAFT.TMP", ".visible.upload.tmp", "folder.tmp/in.txt", ".git/config", "a/.GIT/x",
               ".DS_Store", "photos/Thumbs.db", "Desktop.ini", "~$offer.docx", ".~lock.offer.odt#",
               # folders the Files panel never shows: a file there could not be seen
               "node_modules/readme.txt", "a/node_modules",
               # a .history folder anywhere: the tree hides one holding a HEAD
               "docs/.history/HEAD", "a/.History"]
        for p in bad:
            for status, res in (self.upload(p, b"x"), self.op("mkdir", path=p), self.op("delete", path=p),
                                self.op("move", **{"from": "ok.txt", "to": p}), self.op("move", **{"from": p, "to": "moved.txt"})):
                self.assertEqual((status, res.get("error")), (400, "path_not_allowed"), repr(p))
                self.assertTrue(res["message"])
        for p in (None, 7, ["a"]):
            self.assertEqual(self.op("delete", path=p)[1]["error"], "path_not_allowed")
        self.assertTrue((task / "notes.md").exists() and (self.home / "project.json").exists() and (self.home / "ok.txt").exists())
        self.assertTrue((self.home / ".history" / "HEAD").exists())
        self.assertEqual(sorted(p.name for p in self.home.iterdir()),
                         [".history", "_linked", "ok.txt", "project.json", "sort-papers"])
        self.assertEqual(list(outside.iterdir()), [])
        # A name starting with a space is that file.
        self.assertEqual(self.upload(" lead.txt", b"x")[0], 200)

    @unittest.skipUnless(os.name == "nt", "8.3 short names are a Windows thing")
    def test_a_short_name_for_the_history_is_refused(self):
        self.assertTrue(history.snapshot(self.h)["ok"])
        r = subprocess.run(["cmd", "/c", "dir", "/x", "/a"], cwd=self.h, capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"(\S+~\d)\s+\.history", r.stdout or "")
        if not m:
            self.skipTest("short names are off on this drive")
        status, res = self.op("delete", path=m.group(1))
        self.assertEqual((status, res["error"]), (400, "path_not_allowed"))
        self.assertTrue((self.home / ".history" / "HEAD").exists())

    # ---- the snapshot ----

    def test_a_failed_snapshot_does_not_fail_the_change(self):
        real = history.snapshot

        def failing(home, who=None, reason="scan", message="", allow_empty=False):
            if reason == "upload":
                return {"ok": False, "committed": False, "rev": "", "files": 0, "skipped": [], "msg": "disk full"}
            return real(home, who, reason, message, allow_empty)
        with mock.patch.object(history, "snapshot", failing), mock.patch("builtins.print") as said:
            status, res = self.upload("a.txt", b"a")
        self.assertEqual(status, 200, res)
        self.assertEqual((res["snapshot"]["ok"], res["snapshot"]["msg"]), (False, "disk full"))
        self.assertTrue((self.home / "a.txt").exists())
        self.assertIn("disk full", " ".join(str(a) for c in said.call_args_list for a in c.args))

    def test_the_change_and_its_snapshot_hold_the_history_lock(self):
        held = []
        real = history.snapshot

        def spy(home, who=None, reason="scan", message="", allow_empty=False):
            lk = history.lock(home)
            held.append((reason, lk._is_owned()))
            return real(home, who, reason, message, allow_empty)
        with mock.patch.object(history, "snapshot", spy):
            self.upload("a.txt", b"a")
            self.op("delete", path="a.txt")
        self.assertEqual(held, [("before upload", True), ("upload", True), ("before delete", True), ("delete", True)])

    def test_who_is_written_and_read_back(self):
        (self.home / "x.md").write_bytes(b"x")
        history.snapshot(self.h, {"kind": "user", "name": "fa\nbio"}, reason="upload")
        self.assertEqual(self.top()["who"]["label"], "fa bio")
        (self.home / "y.md").write_bytes(b"y")
        history.snapshot(self.h, {"kind": "user", "name": ""}, reason="upload")
        self.assertEqual(self.top()["who"]["kind"], "you")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_recent_changes_name_the_person(self):
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        line = re.search(r"^const histWho = .*;$", index, re.M).group(0)
        js = line + "\nconsole.log(JSON.stringify([histWho({kind:'user',label:'sam'}), histWho({kind:'task',label:'Sort'}), histWho({kind:'you',label:'x'}), histWho(null)]));"
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(json.loads(r.stdout), ["sam", "Sort", "you", "you"], r.stderr)


if __name__ == "__main__":
    unittest.main()
