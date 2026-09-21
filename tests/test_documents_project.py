"""Documents projects: a folder of files instead of code, its files in its Workspace tab.

* the kind: project.json holds it, /api/projects and ensemble_list_projects
  show it, it switches back and forth, with or without a PO, and each refusal
  has a plain sentence;
* a documents project with a PO: it stays documents, its tasks and PO work in
  its folder, reports and the digest reach its PO, its files stay open;
* the hub side of the file history: the tree marks task folders and never
  lists .history, a task's report or chat message asks for a snapshot credited
  to it, a turn's end is seen from the PTYs, the /api/history/* endpoints are
  refused for a code project, and restore is for the dashboard page only;
* a new task in a documents project works in its folder unless told otherwise;
* the page (index.html's "Documents project" block and the functions around
  it, run in Node; skipped without Node): the Overview shows no files; without
  a PO it is the board with no PO note or pill but a Choose the PO… button;
  with a PO, the PO beside the board as in a code project; the Workspace tab
  mounts the Files panel; a code project's Overview is unchanged, the tree
  hides only real task folders unless "Task folders" is checked (never in a
  task's Workspace), and the history's lists read as they should.
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import ensemble_tools  # noqa: E402
import history  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")
GIT = shutil.which("git")
PORT = 8798


class Hub(unittest.TestCase):
    """A throwaway projects root, state dir and rooms dir."""

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
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(history._take_requests)
        history._take_requests()
        ok, proj, _ = dashboard.register_project("Motors")
        self.assertTrue(ok)
        self.pid = proj["id"]
        self.home = self.root / "Motors"

    def meta(self) -> dict:
        return json.loads((self.home / "project.json").read_text(encoding="utf-8"))

    def room(self, title="Sort the leasing papers") -> str:
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"}])["id"]
        dashboard.assign_session_project(rid, self.pid)
        return rid

    def call(self, method, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b""
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                     "Host": f"127.0.0.1:{PORT}", **(headers or {})}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        hdrs = dict(ln.split(": ", 1) for ln in head.decode("latin-1").split("\r\n")[1:] if ": " in ln)
        return status, hdrs, payload

    def json_call(self, method, path, body=None, headers=None):
        status, _, payload = self.call(method, path, body, headers)
        return status, json.loads(payload)

    def page_headers(self):
        return {"Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}", "Origin": f"http://127.0.0.1:{PORT}"}


class ProjectKind(Hub):
    def test_round_trip_through_project_json_api_and_tool(self):
        self.assertEqual(dashboard.find_project(self.pid)["kind"], "code")
        status, body = self.json_call("POST", "/api/projects/kind", {"projectId": self.pid, "kind": "documents"})
        self.assertEqual((status, body), (200, {"ok": True, "kind": "documents"}))
        self.assertEqual(self.meta()["kind"], "documents")
        self.assertEqual(dashboard.find_project(self.pid)["kind"], "documents")
        status, projects = self.json_call("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual([p["kind"] for p in projects["projects"] if p["id"] == self.pid], ["documents"])
        listed = ensemble_tools._list_projects({"projectId": ""}, {}, None)["projects"]
        self.assertEqual([p["kind"] for p in listed if p["id"] == self.pid], ["documents"])
        self.assertEqual(ensemble_tools._project_view(dashboard.find_project(self.pid))["kind"], "documents")
        status, body = self.json_call("POST", "/api/projects/kind", {"projectId": self.pid, "kind": "code"})
        self.assertEqual(status, 200)
        self.assertNotIn("kind", self.meta())
        self.assertEqual(dashboard.find_project(self.pid)["kind"], "code")
        self.assertFalse((self.home / ".git").exists())
        self.assertFalse(dashboard.find_project(self.pid)["isGit"])

    def test_refusals_say_why(self):
        status, body = self.json_call("POST", "/api/projects/kind", {"projectId": self.pid, "kind": "poster"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "kind_must_be_code_or_documents")
        self.assertEqual(self.json_call("POST", "/api/projects/kind", {"projectId": "proj-nope", "kind": "code"})[0], 404)
        outside = Path(self.tmp.name) / "code-elsewhere"
        ok, ext, _ = dashboard.register_project(str(outside))
        status, body = self.json_call("POST", "/api/projects/kind", {"projectId": ext["id"], "kind": "documents"})
        self.assertEqual((status, body["error"]), (400, "files_outside_projects_root"))
        self.assertIn("projects folder", body["message"])

    def test_a_documents_project_can_have_a_po(self):
        rid = self.room("PO")
        po = lambda chosen: self.json_call("POST", "/api/projects/po", {"projectId": self.pid, "roomId": chosen})
        self.assertEqual(po(rid), (200, {"ok": True}))
        status, body = self.json_call("POST", "/api/projects/kind", {"projectId": self.pid, "kind": "documents"})
        self.assertEqual((status, body), (200, {"ok": True, "kind": "documents"}), "a project with a PO may become one")
        self.assertEqual((self.meta()["kind"], self.meta()["poRoomId"]), ("documents", rid))
        # Clearing and choosing the PO again leaves the kind alone.
        self.assertEqual(po(""), (200, {"ok": True}))
        self.assertEqual(self.meta()["kind"], "documents")
        self.assertEqual(po(rid), (200, {"ok": True}))
        self.assertEqual((self.meta()["kind"], self.meta()["poRoomId"]), ("documents", rid))
        self.assertEqual(dashboard.find_project(self.pid)["kind"], "documents")
        self.assertNotIn("project_has_po", dashboard.KIND_REFUSALS)
        self.assertFalse(hasattr(dashboard, "SWITCHED_TO_CODE"))
        # Back to code keeps the PO too.
        self.assertEqual(self.json_call("POST", "/api/projects/kind", {"projectId": self.pid, "kind": "code"})[0], 200)
        self.assertEqual((self.meta().get("kind"), self.meta()["poRoomId"]), (None, rid))

    def test_a_po_that_no_longer_exists_is_no_po(self):
        dashboard._set_project_meta(self.pid, "poRoomId", "room-gone")
        self.assertEqual(dashboard.set_project_kind(self.pid, "documents"), (True, "ok"))
        self.assertNotIn("poRoomId", self.meta())

    def test_new_project_as_documents(self):
        status, body = self.json_call("POST", "/api/projects/new", {"path": "Inheritance", "name": "Inheritance", "kind": "documents"})
        self.assertEqual(status, 200)
        self.assertEqual(body["project"]["kind"], "documents")
        meta = json.loads((self.root / "Inheritance" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["kind"], "documents")

    def test_a_new_task_works_in_the_folder_unless_told(self):
        dashboard.set_project_kind(self.pid, "documents")
        seen = []

        def fake(project, mode, title):
            seen.append(mode)
            return False, "", {}, "stop here"
        with mock.patch.object(dashboard, "setup_session_workspace", fake):
            dashboard.create_task("t", "s", self.pid, [{"agent": "claude"}], "")
            dashboard.create_task("t", "s", self.pid, [{"agent": "claude"}], "empty")
            dashboard.set_project_kind(self.pid, "code")
            dashboard.create_task("t", "s", self.pid, [{"agent": "claude"}], "")
        self.assertEqual(seen, ["inplace", "empty", "empty"])


class ADocumentsProjectsPO(Hub):
    """A documents project with a PO keeps its files and routes like a code project."""

    def setUp(self):
        super().setUp()
        self.assertEqual(dashboard.set_project_kind(self.pid, "documents"), (True, "ok"))
        self.po = self.room("Motors PO")
        self.assertEqual(dashboard.set_project_po(self.pid, self.po), (True, "ok"))
        self.task = self.room("Sell the X5")

    def test_it_stays_documents_and_its_tasks_and_po_work_in_the_folder(self):
        proj = dashboard.find_project(self.pid)
        self.assertEqual((proj["kind"], proj["poRoomId"]), ("documents", self.po))
        seen = []

        def fake(project, mode, title):
            seen.append(mode)
            return False, "", {}, "stop here"
        with mock.patch.object(dashboard, "setup_session_workspace", fake):
            dashboard.create_task("t", "s", self.pid, [{"agent": "claude"}], "")
        self.assertEqual(seen, ["inplace"])
        # The PO itself: a task in the folder, no worktree (there is no repository).
        ok, base, meta, _ = dashboard.setup_session_workspace(proj, "inplace", "Motors PO")
        self.assertTrue(ok)
        self.assertTrue(os.path.samefile(base, self.home))
        self.assertEqual(meta["mode"], "inplace")
        ok, _, _, msg = dashboard.setup_session_workspace(proj, "worktree", "Motors PO 2")
        self.assertFalse(ok, msg)

    def test_a_task_report_goes_to_its_po_not_the_user(self):
        rung = []
        handler = SimpleNamespace(_ring_report=lambda *a: rung.append(a) or ["claude"])
        ctx = {"room": chatroom.get_room(self.task, public=False), "identity": "claude", "projectId": self.pid}
        res = ensemble_tools._report(ctx, {"kind": "completed", "text": "The ad is in Selling/ad.md"}, handler)
        self.assertEqual(res["deliveredTo"], {"roomId": self.po, "identity": "claude", "title": "Motors PO"})
        self.assertTrue(res["poWoken"])
        self.assertEqual([a[0] for a in rung], [self.po])
        self.assertIn("The ad is in Selling/ad.md", rung[0][-1])
        # The PO's own report still goes to the user.
        ctx = {"room": chatroom.get_room(self.po, public=False), "identity": "claude", "projectId": self.pid}
        self.assertEqual(ensemble_tools._report(ctx, {"kind": "update", "text": "x"}, handler)["deliveredTo"], "user")
        self.assertTrue(ensemble_tools.is_admin_caller(chatroom.get_room(self.po), "claude"))
        self.assertFalse(ensemble_tools.is_admin_caller(chatroom.get_room(self.task), "claude"))

    def test_the_digest_goes_to_its_po(self):
        import digest
        proj = dashboard.find_project(self.pid)
        with mock.patch.object(dashboard, "_room_is_live", lambda room: True):
            room, ident, why = digest._po_target(proj)
        self.assertEqual((room["id"], ident, why), (self.po, "claude", ""))
        self.assertEqual([t["id"] for t in digest.gather(proj)], [self.task], "the PO is not one of its tasks")

    def test_its_files_and_history_are_still_open(self):
        proj, home = dashboard.files_target(self.pid)
        self.assertEqual(proj["id"], self.pid)
        self.assertTrue(os.path.samefile(home, self.home))
        proj, home, bad = dashboard._history_target(self.pid)
        self.assertIsNone(bad)
        dashboard._history_nudge(self.task, "report")
        self.assertEqual([r["who"] for r in history._take_requests()][-1:], [[{"id": self.task, "title": "Sell the X5"}]])


class TreeAndSnapshots(Hub):
    def test_tree_marks_task_folders_and_hides_the_history(self):
        dashboard.set_project_kind(self.pid, "documents")
        (self.home / "Leasing").mkdir()
        (self.home / "sort_papers").mkdir()
        (self.home / "sort_papers" / "task.json").write_text("{}", encoding="utf-8")
        (self.home / ".history").mkdir()
        (self.home / ".history" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        status, body = dashboard.list_dir(str(self.home))
        self.assertEqual(status, 200)
        ents = {e["name"]: e for e in body["entries"]}
        self.assertNotIn(".history", ents)
        self.assertNotIn("task", ents["Leasing"])
        self.assertTrue(ents["sort_papers"]["task"])

    def test_report_and_chat_ask_for_a_snapshot_credited_to_the_task(self):
        rid = self.room()
        dashboard._history_nudge(rid, "report")
        self.assertEqual(history._take_requests(), [], "a code project keeps no history")
        dashboard.set_project_kind(self.pid, "documents")
        history._take_requests()
        dashboard._history_nudge(rid, "report")
        reqs = history._take_requests()
        self.assertEqual(len(reqs), 1)
        self.assertTrue(os.path.samefile(reqs[0]["home"], self.home))
        self.assertEqual(reqs[0]["who"], [{"id": rid, "title": "Sort the leasing papers"}])
        self.assertEqual(reqs[0]["reason"], "report")

    def test_a_turn_end_is_seen_from_the_ptys(self):
        dashboard.set_project_kind(self.pid, "documents")
        rid = self.room()
        other = chatroom.create_room("Elsewhere", [{"identity": "claude", "agent": "claude", "model": "", "role": ""}])["id"]
        dashboard._HISTORY_BUSY.clear()
        sessions = lambda idle: [{"alive": True, "idleSeconds": idle, "meta": {"room": rid}},
                                 {"alive": True, "idleSeconds": idle, "meta": {"room": other}}]
        with mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: sessions(0.5)):
            self.assertEqual(dashboard._history_turn_ends(), [])
            self.assertEqual(dashboard._history_running(self.pid), [{"id": rid, "title": "Sort the leasing papers"}])
        with mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: sessions(5)):
            self.assertEqual(dashboard._history_turn_ends(), [], "not quiet long enough yet")
        with mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: sessions(12)):
            ends = dashboard._history_turn_ends()
            self.assertEqual(len(ends), 1, "only the documents project's task")
            self.assertTrue(os.path.samefile(ends[0][0], self.home))
            self.assertEqual(ends[0][1], [{"id": rid, "title": "Sort the leasing papers"}])
            self.assertEqual(dashboard._history_turn_ends(), [], "one end per turn")


@unittest.skipUnless(GIT, "git is not installed")
class HistoryEndpoints(Hub):
    def setUp(self):
        super().setUp()
        dashboard.set_project_kind(self.pid, "documents")
        (self.home / "Leasing").mkdir()
        (self.home / "Leasing" / "offer.md").write_text("price 300\n", encoding="utf-8", newline="\n")
        self.v1 = history.snapshot(str(self.home))["rev"]
        (self.home / "Leasing" / "offer.md").write_text("price 280\n", encoding="utf-8", newline="\n")
        self.v2 = history.snapshot(str(self.home), [{"id": "room-1", "title": "Haggle"}], "turn")["rev"]
        (self.home / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
        history.snapshot(str(self.home))

    def q(self, **kw):
        from urllib.parse import urlencode
        return urlencode({"project": self.pid, **kw})

    def test_a_code_project_has_no_history_endpoints(self):
        dashboard.set_project_kind(self.pid, "code")
        for path in ("/api/history/log", "/api/history/status", "/api/history/diff", "/api/history/file"):
            status, body = self.json_call("GET", f"{path}?{self.q(path='x')}")
            self.assertEqual((status, body["error"]), (400, "not_a_documents_project"), path)
        status, body = self.json_call("POST", "/api/history/snapshot", {"projectId": self.pid})
        self.assertEqual(status, 400)
        self.assertEqual(self.json_call("GET", "/api/history/log?project=proj-nope")[0], 404)

    def test_log_file_diff_status(self):
        status, body = self.json_call("GET", f"/api/history/log?{self.q(path='Leasing/offer.md')}")
        self.assertEqual(status, 200)
        self.assertEqual([e["rev"] for e in body["entries"]], [self.v2, self.v1])
        self.assertEqual(body["entries"][0]["who"]["label"], "Haggle")
        self.assertEqual(body["entries"][0]["files"][0]["added"], 1)
        self.assertEqual(body["maxFileBytes"], history.MAX_FILE_BYTES)
        self.assertEqual(self.json_call("GET", f"/api/history/log?{self.q(path='../x')}")[0], 400)
        status, body = self.json_call("GET", f"/api/history/file?{self.q(rev=self.v1, path='Leasing/offer.md')}")
        self.assertEqual((status, body["text"], body["binary"]), (200, "price 300\n", False))
        status, body = self.json_call("GET", f"/api/history/diff?{self.q(rev=self.v1, path='Leasing/offer.md')}")
        self.assertIn("-price 300\n+price 280", body["diff"])
        (self.home / "Leasing" / "offer.md").write_text("price 250\n", encoding="utf-8", newline="\n")
        status, body = self.json_call("GET", f"/api/history/status?{self.q()}")
        self.assertEqual([f["path"] for f in body["files"]], ["Leasing/offer.md"])
        status, body = self.json_call("GET", f"/api/history/diff?{self.q(path='Leasing/offer.md')}")
        self.assertIn("-price 280\n+price 250", body["diff"])

    def test_raw_versions_never_run_as_pages(self):
        status, hdrs, data = self.call("GET", f"/api/history/file?{self.q(rev=self.v1, path='Leasing/offer.md', raw='1')}")
        self.assertEqual((status, data), (200, b"price 300\n"))
        self.assertTrue(hdrs["Content-Type"].startswith("text/plain"))
        self.assertTrue(hdrs["Content-Disposition"].startswith("inline"))
        rev = history.log(str(self.home))["entries"][0]["rev"]
        status, hdrs, data = self.call("GET", f"/api/history/file?{self.q(rev=rev, path='page.html', raw='1')}")
        self.assertEqual(hdrs["Content-Type"], "application/octet-stream")
        self.assertTrue(hdrs["Content-Disposition"].startswith("attachment"))
        self.assertEqual(hdrs["X-Content-Type-Options"], "nosniff")

    def test_snapshot_now_is_queued(self):
        status, body = self.json_call("POST", "/api/history/snapshot", {"projectId": self.pid})
        self.assertEqual((status, body), (200, {"ok": True, "queued": True}))
        reqs = history._take_requests()
        self.assertEqual([r["reason"] for r in reqs], ["snapshot now"])

    def test_restore_is_for_the_page_only(self):
        body = {"projectId": self.pid, "rev": self.v1, "path": "Leasing/offer.md"}
        status, res = self.json_call("POST", "/api/history/restore", body)
        self.assertEqual((status, res["error"]), (403, "page_only"))
        rid = self.room()
        token = next(iter(chatroom.get_room(rid, public=False)["tokens"]))
        status, res = self.json_call("POST", "/api/history/restore", body,
                                     {"Authorization": f"Bearer {token}", **self.page_headers()})
        self.assertEqual(status, 403, "a task's agent may not, even with the page's cookie")
        status, res = self.json_call("POST", "/api/history/restore", body,
                                     {**self.page_headers(), "Origin": "http://127.0.0.1:9999"})
        self.assertEqual(status, 403, "another origin may not")
        with mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: "it came from an agent's process (42)"):
            status, res = self.json_call("POST", "/api/history/restore", body, self.page_headers())
        self.assertEqual((status, res["error"]), (403, "page_only"), "an agent's process may not, cookie or not")
        self.assertIn("agent's process", res["message"])
        self.assertEqual((self.home / "Leasing" / "offer.md").read_text(encoding="utf-8"), "price 280\n")
        with mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""):
            status, res = self.json_call("POST", "/api/history/restore", body, self.page_headers())
            self.assertEqual(status, 200, res)
            self.assertTrue(res["ok"] and res["committed"])
            self.assertEqual((self.home / "Leasing" / "offer.md").read_text(encoding="utf-8"), "price 300\n")
            top = history.log(str(self.home))["entries"][0]
            self.assertTrue(top["subject"].startswith("Restored Leasing/offer.md from"))
            status, res = self.json_call("POST", "/api/history/restore", {**body, "rev": "HEAD"}, self.page_headers())
            self.assertEqual(status, 400)
            real = history.snapshot

            def failing(home, who=None, reason="scan", message="", allow_empty=False):
                if reason == "restore":
                    return {"ok": False, "committed": False, "rev": "", "files": 0, "skipped": [], "msg": "disk full"}
                return real(home, who, reason, message, allow_empty)
            with mock.patch.object(history, "snapshot", failing):
                status, res = self.json_call("POST", "/api/history/restore", {**body, "rev": self.v2}, self.page_headers())
            self.assertEqual(status, 500, "written but not recorded is not a success")
            self.assertTrue(res["written"])
            self.assertIn("could not record the restore", res["message"])

    def test_paths_are_taken_as_written_and_deleted_files_page(self):
        (self.home / " leading.txt").write_text("one\n", encoding="utf-8", newline="\n")
        (self.home / "old1.md").write_text("1\n", encoding="utf-8")
        (self.home / "old2.md").write_text("2\n", encoding="utf-8")
        rev = history.snapshot(str(self.home))["rev"]
        status, body = self.json_call("GET", f"/api/history/file?{self.q(rev=rev, path=' leading.txt')}")
        self.assertEqual((status, body.get("text")), (200, "one\n"))
        status, body = self.json_call("GET", f"/api/history/log?{self.q(path=' leading.txt')}")
        self.assertEqual((status, body["path"]), (200, " leading.txt"))
        (self.home / "old1.md").unlink()
        (self.home / "old2.md").unlink()
        history.snapshot(str(self.home))
        status, first = self.json_call("GET", f"/api/history/log?{self.q(deleted='1', limit='1')}")
        self.assertEqual((status, len(first["files"]), first["more"]), (200, 1, True))
        row = first["files"][0]
        status, rest = self.json_call("GET", f"/api/history/log?{self.q(deleted='1', limit='1', after=row['rev'] + ':' + row['path'])}")
        self.assertEqual((len(rest["files"]), rest["more"]), (1, False))
        status, both = self.json_call("GET", f"/api/history/log?{self.q(deleted='1', through=rest['files'][0]['rev'] + ':' + rest['files'][0]['path'])}")
        self.assertEqual((len(both["files"]), both["more"]), (2, False))
        self.assertEqual({first["files"][0]["path"], rest["files"][0]["path"]}, {"old1.md", "old2.md"})


# A task's agent with an ordinary HTTP client: it fetches the page as a browser
# would, keeps the cookie it is given, and replays it with same-origin headers.
AGENT_CLIENT = r'''
import http.client, json, sys
port, pid, rev = int(sys.argv[1]), sys.argv[2], sys.argv[3]
c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
c.request("GET", "/", headers={"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none",
                               "Accept": "text/html", "Upgrade-Insecure-Requests": "1"})
r = c.getresponse()
r.read()
cookie = next((v.split(";")[0] for k, v in r.getheaders()
               if k.lower() == "set-cookie" and v.startswith("ensemble_ui_%d=" % port)), "")
body = json.dumps({"projectId": pid, "rev": rev, "path": "offer.md"})
c.request("POST", "/api/history/restore", body=body,
          headers={"Cookie": cookie, "Origin": "http://127.0.0.1:%d" % port, "Sec-Fetch-Site": "same-origin",
                   "Sec-Fetch-Mode": "cors", "Content-Type": "application/json"})
r = c.getresponse()
print(json.dumps({"cookie": bool(cookie), "status": r.status, "body": json.loads(r.read() or b"{}")}))
'''


@unittest.skipUnless(GIT, "git is not installed")
class AnAgentCannotPoseAsThePage(Hub):
    """Over a real socket: the process that sent the request decides, not the
    headers or the cookie it replays."""

    def test_an_agent_that_fetches_the_page_still_cannot_restore(self):
        import threading
        dashboard.set_project_kind(self.pid, "documents")
        offer = self.home / "offer.md"
        offer.write_text("price 300\n", encoding="utf-8", newline="\n")
        v1 = history.snapshot(str(self.home))["rev"]
        offer.write_text("price 280\n", encoding="utf-8", newline="\n")
        history.snapshot(str(self.home))
        quiet = mock.patch.object(dashboard.Handler, "log_message", lambda *a: None)
        quiet.start()
        self.addCleanup(quiet.stop)
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def attempt(agent_pids):
            with mock.patch.object(dashboard, "_agent_pids", lambda: set(agent_pids)):
                r = subprocess.run([sys.executable, "-c", AGENT_CLIENT, str(port), self.pid, v1],
                                   capture_output=True, text=True, encoding="utf-8", timeout=90)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout.strip().splitlines()[-1])

        # This test process stands in for the agent's PTY: the client is its child.
        res = attempt({os.getpid()})
        self.assertTrue(res["cookie"], "the page's cookie is handed to anyone who asks like a browser")
        self.assertEqual((res["status"], res["body"].get("error")), (403, "page_only"), res)
        self.assertIn("agent's process", res["body"]["message"])
        self.assertEqual(offer.read_text(encoding="utf-8"), "price 280\n")
        # The same request from a program that is not an agent's (the browser) restores.
        res = attempt(set())
        self.assertEqual(res["status"], 200, res)
        self.assertEqual(offer.read_text(encoding="utf-8"), "price 300\n")


# ---- the page ----------------------------------------------------------------

def js_function(name: str) -> str:
    """One top-level function (or const) of index.html, by name."""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", INDEX, re.M)
    if not m:
        m = re.search(rf"^const {re.escape(name)} = .*$", INDEX, re.M)
        return m.group(0)
    i = INDEX.index("{", m.end())
    depth, j = 0, i
    while True:
        c = INDEX[j]
        depth += (c == "{") - (c == "}")
        j += 1
        if depth == 0:
            return INDEX[m.start():j]


def docs_block() -> str:
    i = INDEX.index("// ---- Documents project: begin")
    return INDEX[i:INDEX.index("// ---- Documents project: end", i)]


PAGE = r"""
const out = {};
let SELECTED_PROJECT = null, PROJECT_TAB = 'tasks', SB_DEST = '', SELECTED_SID = null, PO_LAST = '', PO_PEEK = false;
const UNASSIGNED_ID = '__unassigned__', DR_CHUNK = 500;
const docsPath = 'D:\\projects\\Motors', codePath = 'D:\\projects\\Opten';
let ALL_ROWS = [{ roomId: 'room-po', sessionId: 'room-po', label: 'PO task' }, { roomId: 'room-d1', sessionId: 'room-d1', label: 'Sort papers', taskDir: docsPath + '\\sort_papers' },
                { roomId: 'room-dpo', sessionId: 'room-dpo', label: 'Motors PO', agent: 'claude' }];
const PROJECTS = { projects: [
  { id: 'p-docs', name: 'Motors', kind: 'documents', registered: true, path: docsPath, home: docsPath, poRoomId: '', sessions: [{ roomId: 'room-d1', taskDir: docsPath + '\\sort_papers' }] },
  { id: 'p-code', name: 'Opten', kind: 'code', registered: true, path: codePath, home: codePath, poRoomId: '', sessions: [{ roomId: 'room-c1' }] },
  { id: 'p-po', name: 'Hub', kind: 'code', registered: true, path: 'C:\\x\\Hub', home: 'C:\\x\\Hub', poRoomId: 'room-po', sessions: [{ roomId: 'room-po' }] },
  { id: 'p-dpo', name: 'Cars', kind: 'documents', registered: true, path: 'C:\\x\\Cars', home: 'C:\\x\\Cars', poRoomId: 'room-dpo', sessions: [{ roomId: 'room-dpo' }] },
] };
const pill = { hidden: true, _html: '', title: '', classList: { toggle() {} }, setAttribute() {}, set innerHTML(v) { this._inner = v; } };
const document = { getElementById: id => id === 'po-pill' ? pill : null };
const HL = undefined;
let DOCS_TASKS = false;
%(deps)s
%(block)s
const at = (id, tab) => { SELECTED_PROJECT = id; PROJECT_TAB = tab || 'tasks'; };

at('p-docs');
out.docsPoSplit = poSplitProject();
out.docsNote = poNoteHtml();
out.docsFrame = overviewFrameHtml({ poSplit: false, chrome: '[chrome]', poNote: poNoteHtml(), inner: '[board]', edges: '' });
out.docsKind = kindBtnHtml(projectById('p-docs'));
out.docsChoose = poChooseBtnHtml(projectById('p-docs'));
out.docsDialog = kindDialogHtml(projectById('p-docs'));
renderPoPill(projectById('p-docs')); out.docsPill = pill.hidden;
const panel = docsPanelHtml(projectById('p-docs'));
out.panelOrder = [panel.indexOf('>Files<'), panel.indexOf('>Recent changes<')];
out.panelHasTree = panel.includes('class="wsp-tree"') && panel.includes('wsp-hist"');
out.panelBox = [panel.indexOf('> Task folders</label>'), panel.indexOf('class="wsp-tree"'), panel.includes('dcs-tasks-cb" checked')];

at('p-code');
out.codeNote = poNoteHtml();
out.codeFrame = overviewFrameHtml({ poSplit: false, chrome: '[chrome]', poNote: poNoteHtml(), inner: '[board]', edges: '' });
out.codeKind = kindBtnHtml(projectById('p-code'));
out.codeChoose = poChooseBtnHtml(projectById('p-code'));
pill.hidden = true; renderPoPill(projectById('p-code')); out.codePill = pill.hidden;
at('p-po');
out.poSplit = (poSplitProject() || {}).id || null;
out.poDialog = kindDialogHtml(projectById('p-po'));
out.poChoose = poChooseBtnHtml(projectById('p-po'));

// A documents project with a PO: the PO beside the board, as in a code project.
at('p-dpo');
out.dpoSplit = (poSplitProject() || {}).id || null;
out.dpoNote = poNoteHtml();
out.dpoFrame = overviewFrameHtml({ poSplit: true, chrome: '[chrome]', poNote: '', inner: '[board]', edges: '[edges]' });
at('p-dpo', 'workspace'); out.dpoSplitOnWorkspace = poSplitProject();
out.dpoChoose = poChooseBtnHtml(projectById('p-dpo'));
out.dpoDialog = kindDialogHtml(projectById('p-dpo'));
pill.hidden = true; pill._inner = ''; renderPoPill(projectById('p-dpo')); out.dpoPill = [pill.hidden, pill._inner || ''];

// The tree: a documents project shows its own folders, hides real task folders.
const entries = [
  { name: 'Leasing', type: 'dir' }, { name: 'sort_papers', type: 'dir', task: true },
  { name: '_linked', type: 'dir' }, { name: 'old-task-no-json', type: 'dir' }, { name: 'README.md', type: 'file', size: 10 },
];
const view = (ctx, root) => ({ ctx, roots: [{ path: root, kind: 'project', label: 'P' }], dirs: new Map([[root, { entries }]]),
                               open: new Set(), mark: '', sel: '', hide: new Set(), hideAllDirs: false, hideReady: true });
const names = html => [...html.matchAll(/class="nm">([^<]*)</g)].map(m => m[1]);
out.docsTree = names(wsRowsHtml(view({ kind: 'project', projectId: 'p-docs', docs: true }, docsPath), docsPath));
out.codeTree = names(wsRowsHtml(view({ kind: 'project', projectId: 'p-code' }, codePath), codePath));
const tv = view({ kind: 'task', sid: 'room-d1' }, docsPath);
out.docsTaskTree = names(wsRowsHtml(tv, docsPath));
out.keys = [wsCtxKey({ kind: 'project', projectId: 'p-docs', docs: true }), wsCtxKey({ kind: 'project', projectId: 'p-docs' })];
// "Task folders" checked: the Workspace tab shows them; a task's Workspace does not.
docsTasksSet(true);
out.docsTreeShown = names(wsRowsHtml(view({ kind: 'project', projectId: 'p-docs', docs: true }, docsPath), docsPath));
out.docsTaskTreeShown = names(wsRowsHtml(view({ kind: 'task', sid: 'room-d1' }, docsPath), docsPath));
out.codeTreeShown = names(wsRowsHtml(view({ kind: 'project', projectId: 'p-code' }, codePath), codePath));
docsTasksSet(false);

// The history's words.
out.what = [histWhat({ status: 'M', added: 3, removed: 1 }), histWhat({ status: 'A', added: 12, removed: 0 }), histWhat({ status: 'D', added: 0, removed: 4 }),
            histWhat({ status: 'R', from: 'a/old.md', added: 0, removed: 0 }), histWhat({ status: 'M', added: null, removed: null, binary: true })];
out.rel = [histRel(docsPath, docsPath + '\\Leasing\\offer.md'), histRel(docsPath, 'D:\\projects\\Motors2\\x.md'), histRel(docsPath, docsPath)];
const st = { recent: [{ rev: 'a1', time: 1790000000, subject: '1 file changed', who: { kind: 'task', label: 'Sort papers', tasks: [{ id: 'room-d1' }] },
                        files: [{ path: 'Leasing/offer.md', status: 'M', added: 2, removed: 1 }] },
                      { rev: 'a0', time: 1789990000, subject: 'Restored Leasing/offer.md from 2026-09-14 10:00', who: { kind: 'you', reason: 'restore' },
                        files: [{ path: 'Leasing/offer.md', status: 'M', added: 1, removed: 1 }] }],
             deleted: [{ path: 'Leasing/old.pdf', from: 'b2^', time: 1789000000, who: { kind: 'you' } }], view: 'recent', err: '' };
out.recent = histRecentHtml(st);
st.view = 'deleted'; out.deleted = histRecentHtml(st);
out.emptyRecent = histRecentHtml({ recent: [], deleted: [], view: 'recent', err: '' });
out.top = histTopHtml({ rel: 'Leasing/offer.md', entries: st.recent, sel: 'a1', more: false, err: '' });
out.topMore = histTopHtml({ rel: 'Leasing/offer.md', entries: st.recent, sel: '', more: true, err: '' });
out.deletedMore = histRecentHtml({ ...st, view: 'deleted', delMore: true });
out.text = histTextHtml('one\ntwo\n', 'x.txt');
out.diff = histDiffHtml('--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n', 'x.txt');

// The deleted list against a hub that pages by place (as history.deleted does):
// 205 deleted files, restored ones between pages.
const GONE = Array.from({ length: 205 }, (_, n) => ({ rev: 'd1', path: 'gone/' + String(n).padStart(3, '0') + '.txt' }));
const BACK = new Set();
function hubDeleted(p) {
  const at = m => { const i = m.indexOf(':'); return GONE.findIndex(r => r.rev === m.slice(0, i) && r.path === m.slice(i + 1)); };
  const end = p.get('through') ? at(p.get('through')) : Infinity, cap = p.get('through') ? 1e9 : 200;
  const files = [];
  let j = p.get('after') ? at(p.get('after')) + 1 : 0;
  for (; j < GONE.length && j <= end && files.length < cap; j++) if (!BACK.has(GONE[j].path)) files.push(GONE[j]);
  return { files, more: GONE.slice(j).some(r => !BACK.has(r.path)) };
}
const api = async url => { const u = new URL(url, 'http://hub'); return u.searchParams.get('deleted') === '1' ? hubDeleted(u.searchParams) : { entries: [], more: false }; };
const toast = () => {};
const offPage = { isConnected: false, dataset: {} };
const gone = s => s.deleted.map(d => d.path);
(async () => {
  // A restore before older files are shown, then Show older.
  const a = histState('pa');
  await histRecentLoad(offPage, 'pa', true);
  BACK.add('gone/000.txt');
  await histRecentLoad(offPage, 'pa', true);
  await histDeletedMore(offPage, 'pa', { disabled: false });
  out.pagedAfterRestore = [a.deleted.length, a.delMore, new Set(gone(a)).size, gone(a).includes('gone/201.txt'), gone(a).includes('gone/000.txt')];
  // A restore after older files are shown, then the refresh.
  BACK.add('gone/150.txt');
  await histRecentLoad(offPage, 'pa', true);
  out.refreshAfterPaging = [a.deleted.length, a.delMore, gone(a).includes('gone/150.txt'), gone(a).includes('gone/204.txt')];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

DEPS = ["esc", "agoSpan", "wsNorm", "wsSame", "wsJoin", "wsTabName", "wsFmtSize", "projectById", "registeredProjects",
        "poRowOf", "poSplitProject", "poNoteHtml", "makePoSessions", "renderPoPill", "poAgent", "wsProjectForRow", "wsTaskFolder",
        "wsHidden", "wsRowsHtml", "wsCtxKey", "drParse", "drContent", "drHighlight", "drRowHtml",
        "WS_EMPTY", "WS_MAC", "WS_RECENT_KEY", "WS_ICON_TREE", "wsPanelHtml",
        "DOCS_TASKS_KEY", "docsTasksShown", "docsTasksSet", "docsApart", "docsInTask",
        "pointsCountText", "pointsCountTip"]


@unittest.skipUnless(NODE, "node is not installed")
class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = PAGE % {"deps": "\n".join(js_function(n) for n in DEPS), "block": docs_block()}
        r = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_documents_overview_is_the_board_and_has_no_po(self):
        o = self.out
        self.assertIsNone(o["docsPoSplit"])
        self.assertEqual(o["docsNote"], "")
        self.assertTrue(o["docsPill"], "no PO pill")
        self.assertEqual(o["docsFrame"], "[chrome][board]", "no files above the board, no note asking for a PO")
        self.assertIn(">Documents project<", o["docsKind"])
        self.assertIn('class="po-btn po-choose" data-proj="p-docs"', o["docsChoose"])
        self.assertIn(">Choose the PO…<", o["docsChoose"])
        self.assertNotRegex(o["docsDialog"], r"has a PO|Choosing a PO|stays a code project|disabled",
                            "the Kind dialog no longer mentions the PO exclusion")
        self.assertTrue(0 <= o["panelOrder"][0] < o["panelOrder"][1], "Files come before Recent changes")
        self.assertTrue(o["panelHasTree"])
        box, tree, checked = o["panelBox"]
        self.assertTrue(0 <= box < tree, "the Task folders box is above the tree")
        self.assertFalse(checked, "unchecked by default")

    def test_the_workspace_tab_of_a_documents_project_is_its_files_panel(self):
        i = INDEX.index("if (SELECTED_PROJECT && PROJECT_TAB === 'workspace') {")
        branch = INDEX[i:INDEX.index("\n    return;\n  }", i)]
        self.assertIn("const docs = !!(pj && pj.path && isDocsProject(pj));", branch)
        self.assertIn("wsCtxKey({ kind: 'project', projectId: SELECTED_PROJECT, docs })", branch)
        self.assertIn("delete panel.dataset.proj;", branch, "a code project's Workspace is never taken for the Files panel")
        self.assertIn("if (docs) docsPanelRender(panel, pj);", branch)
        self.assertIn("wsMount(panel.querySelector('.wsp'), { kind: 'project', projectId: SELECTED_PROJECT });", branch,
                      "a code project's Workspace mounts as before")
        self.assertIn("document.querySelector('#ws-panel:not([hidden]) .dcs-files')", INDEX)
        self.assertIn("const dp = document.getElementById('ws-panel');", js_function("histClick"))
        self.assertIn("docs: isDocsProject(projectById(pid))", js_function("chOpenInWorkspace"),
                      "the Changes tab's Open file lands in the Files panel's viewer")

    def test_a_code_project_is_unchanged(self):
        o = self.out
        self.assertIn("This project has no PO.", o["codeNote"])
        self.assertEqual(o["codeFrame"], "[chrome]" + o["codeNote"] + "[board]")
        self.assertFalse(o["codePill"], "the No PO pill still shows")
        self.assertIn(">Code project<", o["codeKind"])
        self.assertEqual(o["codeChoose"], "", "a code project says how to get a PO in its note")
        self.assertEqual(o["poSplit"], "p-po")
        self.assertEqual(o["poChoose"], "")
        self.assertNotRegex(o["poDialog"], r"has a PO|Choosing a PO|stays a code project")
        self.assertNotIn("disabled", o["poDialog"], "a project with a PO may become a documents project")
        self.assertIn("Its Overview leads with the PO and the board.", o["poDialog"], "the code option reads as before")

    def test_a_documents_project_with_a_po_shows_the_po_beside_the_board(self):
        o = self.out
        self.assertEqual(o["dpoSplit"], "p-dpo")
        self.assertIsNone(o["dpoSplitOnWorkspace"])
        self.assertEqual(o["dpoNote"], "")
        self.assertEqual(o["dpoFrame"], '<div class="po-chrome">[chrome]</div><div class="po-board">[board]</div>[edges]')
        self.assertEqual(o["dpoChoose"], "")
        self.assertNotRegex(o["dpoDialog"], r"has a PO|Choosing a PO|stays a code project|disabled")
        self.assertFalse(o["dpoPill"][0], "the PO pill shows")
        self.assertIn('<span class="po-pill-t">PO</span><span class="po-pill-p">Cars</span>', o["dpoPill"][1])
        # The code project's layout, nothing of its own: no files row in main's grid.
        self.assertNotIn("docs-split", INDEX)
        self.assertNotRegex(INDEX, r"grid-template-areas:[^;]*\bfiles\b")

    def test_the_tree_hides_only_real_task_folders(self):
        o = self.out
        self.assertEqual(o["docsTree"], ["Leasing", "old-task-no-json", "README.md"])
        self.assertEqual(o["docsTaskTree"], ["Leasing", "old-task-no-json", "README.md"])
        self.assertEqual(o["codeTree"], ["Leasing", "sort_papers", "_linked", "old-task-no-json", "README.md"])
        self.assertEqual(o["keys"], ["docs:p-docs", "project:p-docs"])
        self.assertEqual(o["docsTreeShown"], ["Leasing", "sort_papers", "old-task-no-json", "README.md"], "_linked still hidden")
        self.assertEqual(o["docsTaskTreeShown"], o["docsTaskTree"], "a task's Workspace keeps its own rules")
        self.assertEqual(o["codeTreeShown"], o["codeTree"])

    def test_the_history_reads_plainly(self):
        o = self.out
        self.assertEqual(o["what"], ["+3 −1", "added · +12 −0", "deleted", "renamed from old.md", "changed"])
        self.assertEqual(o["rel"], ["Leasing/offer.md", "", ""])
        self.assertIn("Sort papers", o["recent"])
        self.assertIn("Restored Leasing/offer.md from 2026-09-14 10:00", o["recent"])
        self.assertIn('data-path="Leasing/offer.md"', o["recent"])
        self.assertIn("+2 −1", o["recent"])
        self.assertIn('data-rev="b2^"', o["deleted"])
        self.assertIn("deleted by you", o["deleted"])
        self.assertIn("No snapshots yet", o["emptyRecent"])
        self.assertIn('class="wsh-ver on" data-rev="a1"', o["top"])
        self.assertIn("Pick a version", o["top"])
        self.assertNotIn("wsh-older", o["top"])
        self.assertIn(">Show older versions<", o["topMore"])
        self.assertNotIn("dch-older", o["deleted"])
        self.assertIn(">Show older deleted files<", o["deletedMore"])
        self.assertEqual(o["text"].count('class="dr k-ctx"'), 2)
        self.assertIn('class="drv one"', o["text"])
        self.assertIn("The file now: +1 −1 against this version", o["diff"])
        self.assertIn('class="dr k-add"', o["diff"])

    def test_deleted_files_page_from_a_place(self):
        # [shown, more, distinct, the 202nd file is reachable, the restored one is gone]
        self.assertEqual(self.out["pagedAfterRestore"], [204, False, 204, True, False])
        self.assertEqual(self.out["refreshAfterPaging"], [203, False, False, True])

    def test_layout_and_safari_rules(self):
        self.assertNotIn('id="docs-panel"', INDEX)
        self.assertNotIn("docsOverviewProject", INDEX)
        self.assertIn("a.rm-btn { display: inline-flex;", INDEX, "a link button is a box, so its touch height applies")
        # The panel fills the Workspace tab as a Workspace does, and stacks on a narrow screen.
        self.assertIn("height: calc(100vh - var(--chrome-h)); min-height: 420px; }", INDEX[INDEX.index("  .dcs {"):])
        self.assertIn(".dcs { grid-template-columns: minmax(0, 1fr); gap: var(--s-300); height: auto; min-height: 0; }", INDEX)
        block = docs_block()
        css = INDEX[INDEX.index("  .dcs {"):INDEX.index(".drv.one .dr .dg::before")]
        for text in (block, css):
            self.assertNotIn(":has(", text)
        self.assertNotRegex(block, r"^await ", "no top-level await")


if __name__ == "__main__":
    unittest.main()
