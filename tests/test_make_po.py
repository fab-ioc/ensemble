"""A past session made the PO of a project, from the page.

* the hub (POST /api/projects/po-from-session): a session the hub lists but
  does not own becomes the PO of a new code or documents project, or of an
  existing project that has none, in one request: the project, the adopted
  room (no task number: a PO is not a task), the PO, and the first input held
  for the resume; a one-agent task in no project can take the same way, a
  project's task cannot; what is refused, and that a refusal or a failed start
  leaves nothing behind, a project.json that was in the folder included; two
  requests at once for one session, or one project, make one PO;
* what the hub types: one line that the chat reads as the hub's (``madepo``),
  not the person's;
* rotation: a PO with no written handover is asked for it and never replaced
  by a fresh session; being made a PO starts the cool-down; the fresh session
  of its first rotation starts where a PO works;
* the page: which sessions and tasks are offered, the note on a session that
  may still be open in a terminal, the dialog and the request's body (Node).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import rotation  # noqa: E402

PORT = 8798
NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
URL = "/api/projects/po-from-session"


class Hub(unittest.TestCase):
    """A throwaway projects root, state dir and rooms dir; the agents are
    installed and a resume starts nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.base = base
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        self.started = []
        self.start_error = None

        def start(handler, room_full):
            if self.start_error:
                raise self.start_error
            self.started.append(room_full["id"])
            return [{"identity": "claude", "ptyId": 7}]

        for p in (mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                  mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                  mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
                  mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
                  mock.patch.object(dashboard, "operator_name", lambda: "sam"),
                  mock.patch.object(dashboard.agents, "get_agent",
                                    lambda k: SimpleNamespace(installed=lambda: True) if k in ("claude", "codex") else None),
                  mock.patch.object(dashboard.Handler, "_start_or_resume_room", start),
                  mock.patch.dict(dashboard._RESUMES, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.work = base / "work" / "engine"            # where the past session was started
        self.work.mkdir(parents=True)

    def call(self, body, origin=""):
        return self.call_url(URL, body, origin)

    def call_url(self, url, body, origin=""):
        raw = json.dumps(body).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = url, "POST", "HTTP/1.1"
        h.requestline = f"POST {url} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"}
        if origin:
            h.headers["Origin"] = origin
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")

    def session(self, **over):
        return {"sessionId": "sid-past-1", "cwd": str(self.work), "agent": "claude", "label": "Engine notes", **over}

    def rooms(self):
        return chatroom.list_rooms()

    def nothing_left(self, projects=0):
        self.assertEqual(len(dashboard.load_projects()), projects)
        self.assertEqual(self.rooms(), [])
        self.assertEqual(dashboard.load_session_projects(), {})
        self.assertEqual(dict(dashboard._RESUMES), {})


class NewProject(Hub):
    def test_a_code_project_with_the_session_as_its_po(self):
        code = self.base / "code" / "engine"
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(code)})
        self.assertEqual(status, 200, out)
        proj, rid = out["project"], out["room"]["id"]
        self.assertEqual((proj["name"], proj.get("kind", "code"), proj["poRoomId"]), ("Engine", "code", rid))
        self.assertTrue(code.is_dir(), "the code folder is created if missing")
        room = chatroom.get_room(rid, public=False)
        part = chatroom.agent_participants(room)[0]
        self.assertEqual((room["mode"], room["adopted"], room["projectId"], room["sharedCwd"]),
                         ("solo", True, proj["id"], True))
        self.assertEqual((part["sessionId"], part["cwd"]), ("sid-past-1", str(self.work)),
                         "continued in the folder it was started in")
        self.assertEqual(part["nextCwd"], os.path.normpath(str(code)), "its first fresh session works in the code")
        self.assertAlmostEqual(part["madePoAt"], time.time(), delta=30)
        self.assertIsNone(room.get("no"), "a PO is not one of the project's tasks")
        self.assertFalse(dashboard.find_project(proj["id"]).get("nextTaskNo"))
        self.assertEqual(dashboard.load_session_projects()[rid], proj["id"])
        self.assertIn("product owner (PO) of the project 'Engine'", room["spec"])
        self.assertNotIn("PO-HANDOVER.md first", room["spec"], "the charter holds no one-off step")
        # Resumed once, its first input held for when it is up.
        self.assertEqual(self.started, [rid])
        queue = dashboard._RESUMES[rid].queue
        self.assertEqual(len(queue), 1)
        self.assertTrue(queue[0]["text"].startswith(dashboard.MADE_PO_PREFIX))
        self.assertNotIn("\n", queue[0]["text"], "one line, as every hub input")
        for word in ("PO-HANDOVER.md", "ROADMAP.md", "Running a project as its PO", "sam"):
            self.assertIn(word, queue[0]["text"])

    def test_a_documents_project(self):
        status, out = self.call({**self.session(), "name": "Family papers", "kind": "documents", "path": "ignored"})
        self.assertEqual(status, 200, out)
        proj, rid = out["project"], out["room"]["id"]
        home = self.root / "Family papers"
        self.assertEqual((proj["kind"], Path(proj["path"])), ("documents", home))
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["workspace"], {"mode": "inplace"})
        self.assertEqual(chatroom.agent_participants(room)[0]["nextCwd"], os.path.normpath(str(home)))
        self.assertIn("documents project", room["spec"])
        self.assertIn("paragraph on a documents project", dashboard._RESUMES[rid].queue[0]["text"])

    def test_in_the_sessions_own_folder_there_is_nowhere_to_move(self):
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)
        part = chatroom.agent_participants(chatroom.get_room(out["room"]["id"], public=False))[0]
        self.assertNotIn("nextCwd", part)

    def test_what_is_refused_leaves_nothing(self):
        (self.base / "a-file").write_text("x", encoding="utf-8")
        for body, status, words in (
                ({**self.session(), "name": "", "label": "", "kind": "code", "path": str(self.work)}, 400, "name"),
                ({**self.session(), "name": "E", "kind": "code", "path": "relative/path"}, 400, "full path"),
                ({**self.session(), "name": "E", "kind": "code", "path": str(self.base / "a-file")}, 400, "is a file"),
                ({**self.session(), "name": "E", "kind": "spreadsheet", "path": str(self.work)}, 400, ""),
                ({**self.session(), "name": "a/b", "kind": "documents"}, 400, "folder's name"),
                ({**self.session(cwd=str(self.base / "gone")), "name": "E", "kind": "code", "path": str(self.work)}, 400, "no longer exists"),
                ({**self.session(agent="cursor"), "name": "E", "kind": "code", "path": str(self.work)}, 400, "not installed"),
                ({"sessionId": "", "cwd": "", "name": "E"}, 400, "session and its folder"),
        ):
            got, out = self.call(body)
            self.assertEqual((got, out.get("error")), (status, "cannot_make_po"), (body, out))
            self.assertIn(words, out["message"])
            self.nothing_left()
        self.assertFalse((self.root / "a").exists())
        self.assertEqual(self.started, [])

    def test_a_folder_that_is_a_project_already(self):
        ok, existing, _ = dashboard.register_project(str(self.work), "Engine")
        status, out = self.call({**self.session(), "name": "Engine 2", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 409, out)
        self.assertIn("already the project “Engine”", out["message"])
        self.assertEqual([p["id"] for p in dashboard.load_projects()], [existing["id"]], "and it is left as it was")
        self.assertTrue(self.work.is_dir())

    def test_a_conversation_the_hub_already_holds(self):
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)
        status, out = self.call({**self.session(), "name": "Other", "kind": "code", "path": str(self.base / "other")})
        self.assertEqual(status, 409, out)
        self.assertIn("already the task", out["message"])
        self.assertEqual(len(dashboard.load_projects()), 1)
        self.assertFalse((self.base / "other").exists())

    def test_a_page_on_another_site_is_refused(self):
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)},
                                origin="https://elsewhere.example")
        self.assertEqual((status, out["error"]), (403, "cross_origin"))
        self.nothing_left()
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)},
                                origin=f"http://127.0.0.1:{PORT}")
        self.assertEqual(status, 200, out)

    def test_a_session_that_does_not_start_leaves_nothing(self):
        self.start_error = dashboard.StartRoomError("claude could not be started")
        code = self.base / "code" / "engine"
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(code)})
        self.assertEqual((status, out.get("error")), (400, "cannot_make_po"), out)
        self.assertIn("could not be started", out["message"])
        self.nothing_left()
        self.assertFalse(code.exists(), "the folder this request made is gone")
        self.assertEqual(list(self.root.iterdir()), [], "and the project's home")
        # The same for a documents project; a folder that was there stays, without a project.json.
        kept = self.root / "Papers"
        kept.mkdir()
        (kept / "will.txt").write_text("mine", encoding="utf-8")
        status, out = self.call({**self.session(), "name": "Papers", "kind": "documents"})
        self.assertEqual(status, 400, out)
        self.nothing_left()
        self.assertEqual([p.name for p in kept.iterdir()], ["will.txt"])


    def test_a_project_json_that_was_in_the_folder_is_put_back(self):
        self.start_error = RuntimeError("no terminal")
        # A readable one makes the folder a project already (the root is scanned): refused, untouched.
        # An unreadable one is overwritten by the new project, and put back when that is undone.
        for name, was, status, projects in (
                ("Old hub", b'{"id": "proj-elsewhere", "name": "Old hub", "poRoomId": "room-x"}', 409, 1),
                ("Broken", b"{not json", 400, 1)):
            folder = self.root / name
            folder.mkdir()
            (folder / "project.json").write_bytes(was)
            got, out = self.call({**self.session(), "name": name, "kind": "documents"})
            self.assertEqual(got, status, out)
            self.assertEqual((folder / "project.json").read_bytes(), was, name)
            self.nothing_left(projects=projects)

    def test_two_requests_at_once_for_one_session_make_one_po(self):
        gate, results = threading.Event(), []
        real = dashboard.Handler._start_or_resume_room

        def slow(handler, room_full):
            gate.wait(5)
            return real(handler, room_full)

        def ask(name):
            results.append(self.call({**self.session(), "name": name, "kind": "code",
                                      "path": str(self.base / "code" / name)}))

        with mock.patch.object(dashboard.Handler, "_start_or_resume_room", slow):
            threads = [threading.Thread(target=ask, args=(n,)) for n in ("One", "Two")]
            for t in threads:
                t.start()
            time.sleep(0.3)             # the second is asked while the first is starting the session
            gate.set()
            for t in threads:
                t.join(20)
        self.assertEqual(sorted(s for s, _ in results), [200, 409], results)
        self.assertEqual((len(self.rooms()), len(dashboard.load_projects()), len(self.started)), (1, 1, 1))
        loser = next(o for s, o in results if s == 409)
        self.assertIn("already the task", loser["message"])
        self.assertEqual(len(list((self.base / "code").iterdir())), 1, "the refused request's folder is not left")


class ExistingProject(Hub):
    def setUp(self):
        super().setUp()
        ok, proj, _ = dashboard.register_project("Motors")
        self.pid = proj["id"]

    def test_a_project_without_a_po_takes_the_session(self):
        first = chatroom.create_room("A task", [{"identity": "claude", "agent": "claude"}])["id"]
        dashboard.assign_session_project(first, self.pid)
        dashboard.assign_task_number(first, self.pid)
        status, out = self.call({**self.session(), "projectId": self.pid})
        self.assertEqual(status, 200, out)
        rid = out["room"]["id"]
        self.assertEqual((out["project"]["id"], out["project"]["poRoomId"]), (self.pid, rid))
        self.assertEqual(len(dashboard.load_projects()), 1, "no project is made")
        self.assertIsNone(chatroom.get_room(rid).get("no"))
        self.assertEqual(dashboard.find_project(self.pid)["nextTaskNo"], 2, "the project's counter is left alone")

    def test_a_project_with_a_po_is_refused(self):
        po = chatroom.create_room("The PO", [{"identity": "claude", "agent": "claude"}])["id"]
        dashboard.assign_session_project(po, self.pid)
        self.assertEqual(dashboard.set_project_po(self.pid, po), (True, "ok"))
        status, out = self.call({**self.session(), "projectId": self.pid})
        self.assertEqual(status, 409, out)
        self.assertIn("already has a PO", out["message"])
        self.assertEqual([r["id"] for r in self.rooms()], [po])
        self.assertEqual(dashboard.find_project(self.pid)["poRoomId"], po)
        self.assertEqual(self.started, [])

    def test_two_sessions_at_once_for_one_project_make_one_po(self):
        gate, results = threading.Event(), []
        real = dashboard.Handler._start_or_resume_room

        def slow(handler, room_full):
            gate.wait(5)
            return real(handler, room_full)

        def ask(sid):
            results.append(self.call({**self.session(sessionId=sid), "projectId": self.pid}))

        with mock.patch.object(dashboard.Handler, "_start_or_resume_room", slow):
            threads = [threading.Thread(target=ask, args=(s,)) for s in ("sid-a", "sid-b")]
            for t in threads:
                t.start()
            time.sleep(0.3)             # the second is asked while the first is starting the session
            gate.set()
            for t in threads:
                t.join(20)
        self.assertEqual(sorted(s for s, _ in results), [200, 409], results)
        winner = next(o for s, o in results if s == 200)
        self.assertEqual([r["id"] for r in self.rooms()], [winner["room"]["id"]], "the refused session is nobody's task")
        self.assertEqual(dashboard.find_project(self.pid)["poRoomId"], winner["room"]["id"])

    def test_choosing_a_po_waits_for_a_session_being_made_one(self):
        self.assertTrue(dashboard._MAKE_PO_LOCK.acquire(timeout=1))
        try:
            done = []
            h = threading.Thread(target=lambda: done.append(
                self.call_url("/api/projects/po", {"projectId": self.pid, "roomId": ""})))
            h.start()
            h.join(0.5)
            self.assertEqual(done, [], "held while the other request runs")
        finally:
            dashboard._MAKE_PO_LOCK.release()
        h.join(10)
        self.assertEqual(done[0][0], 200)

    def test_a_po_that_no_longer_exists_does_not_count(self):
        po = chatroom.create_room("The PO", [{"identity": "claude", "agent": "claude"}])["id"]
        dashboard.set_project_po(self.pid, po)
        chatroom.delete_room(po)
        status, out = self.call({**self.session(), "projectId": self.pid})
        self.assertEqual(status, 200, out)

    def test_a_failed_start_gives_the_project_back_as_it_was(self):
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({**self.session(), "projectId": self.pid})
        self.assertEqual(status, 400, out)
        self.assertEqual(len(dashboard.load_projects()), 1)
        self.assertFalse(dashboard.find_project(self.pid).get("poRoomId"))
        self.assertTrue((self.root / "Motors").is_dir(), "a project that was there is not removed")
        self.nothing_left(projects=1)

    def test_a_project_that_is_gone(self):
        status, out = self.call({**self.session(), "projectId": "proj-nope"})
        self.assertEqual(status, 404, out)


class ATask(Hub):
    def task(self, agents=("claude",), sid="sid-task-1"):
        rid = chatroom.create_room("Research", [{"identity": a, "agent": a} for a in agents])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"], full["spec"] = str(self.work), "solo", "Look into engines."
        for part in chatroom.agent_participants(full):
            part["sessionId"], part["cwd"] = sid, str(self.work)
        chatroom.update_room(full)
        return rid

    def test_a_one_agent_task_in_no_project(self):
        rid = self.task()
        status, out = self.call({"roomId": rid, "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)
        self.assertEqual((out["room"]["id"], out["project"]["poRoomId"]), (rid, rid))
        self.assertEqual(len(self.rooms()), 1, "the task itself, not a copy")
        self.assertEqual(self.started, [rid])

    def test_a_failed_start_gives_the_task_back_as_it_was(self):
        rid = self.task()
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({"roomId": rid, "name": "Engine", "kind": "code", "path": str(self.base / "code")})
        self.assertEqual(status, 400, out)
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(room["spec"], "Look into engines.")
        for gone in ("projectId", "sharedCwd"):
            self.assertFalse(room.get(gone), gone)
        part = chatroom.agent_participants(room)[0]
        self.assertNotIn("madePoAt", part)
        self.assertNotIn("nextCwd", part)
        self.assertEqual((dashboard.load_projects(), dashboard.load_session_projects()), ([], {}))
        self.assertFalse((self.base / "code").exists())

    def test_a_failed_start_keeps_what_the_task_held_for_retry(self):
        rid = self.task()
        held = dashboard._Resume()
        held.queue.append({"text": "carry on with the pistons", "to": "", "at": 1.0, "key": "k-1"})
        held.fail("no terminal")
        dashboard._RESUMES[rid] = held
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({"roomId": rid, "name": "Engine", "kind": "code", "path": str(self.base / "code")})
        self.assertEqual(status, 400, out)
        pending = dashboard.pending_input(rid)
        self.assertEqual([it["text"] for it in pending["items"]], ["carry on with the pistons"])
        self.assertEqual(pending["state"], "failed")

    def test_a_folder_that_became_another_projects_is_not_deleted(self):
        folder = self.root / "Engine"

        def start(handler, room_full):
            # New project took the same name while the session was starting.
            (folder / "project.json").write_text(json.dumps({"id": "proj-theirs"}), encoding="utf-8")
            (folder / "notes.md").write_text("theirs", encoding="utf-8")
            raise RuntimeError("no terminal")

        with mock.patch.object(dashboard.Handler, "_start_or_resume_room", start):
            status, out = self.call({**self.session(), "name": "Engine", "kind": "documents"})
        self.assertEqual(status, 400, out)
        self.assertEqual((folder / "notes.md").read_text(encoding="utf-8"), "theirs")
        self.assertEqual(json.loads((folder / "project.json").read_text(encoding="utf-8"))["id"], "proj-theirs")

    def test_a_projects_task_is_not_taken_from_it(self):
        ok, proj, _ = dashboard.register_project("Motors")
        ok, other, _ = dashboard.register_project("Boats")
        linked, recorded, inside = self.task(sid="s-1"), self.task(sid="s-2"), self.task(sid="s-3")
        dashboard.assign_session_project(linked, proj["id"])
        dashboard.assign_task_number(linked, proj["id"])
        full = chatroom.get_room(recorded, public=False)
        full["projectId"] = proj["id"]
        chatroom.update_room(full)
        full = chatroom.get_room(inside, public=False)
        full["cwd"] = str(Path(proj["path"]) / "sub")            # its folder is in the project's
        chatroom.update_room(full)
        for rid in (linked, recorded, inside):
            for target in ({"name": "New", "kind": "code", "path": str(self.base / "new")}, {"projectId": other["id"]}):
                status, out = self.call({"roomId": rid, **target})
                self.assertEqual(status, 409, (rid, target, out))
                self.assertIn("belongs to the project “Motors”", out["message"])
        self.assertEqual(len(dashboard.load_projects()), 2)
        self.assertFalse((self.base / "new").exists())
        self.assertEqual((dashboard.load_session_projects(), chatroom.get_room(linked)["no"]), ({linked: proj["id"]}, 1))
        self.assertEqual(self.started, [])
        # Its own project's PO it may become, keeping its number there.
        status, out = self.call({"roomId": linked, "projectId": proj["id"]})
        self.assertEqual(status, 200, out)
        self.assertEqual((out["project"]["poRoomId"], chatroom.get_room(linked)["no"]), (linked, 1))

    def test_a_failed_start_in_its_own_project_changes_nothing(self):
        ok, proj, _ = dashboard.register_project("Motors")
        rid = self.task()
        dashboard.assign_session_project(rid, proj["id"])
        dashboard.assign_task_number(rid, proj["id"])
        meta = Path(proj["path"]) / "project.json"
        was_meta, was_room = meta.read_bytes(), chatroom.get_room(rid, public=False)
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({"roomId": rid, "projectId": proj["id"]})
        self.assertEqual(status, 400, out)
        now = chatroom.get_room(rid, public=False)
        for k in ("no", "noProjectId", "previousNos", "projectId", "spec", "sharedCwd", "workspace", "cwd"):
            self.assertEqual(now.get(k), was_room.get(k), k)
        self.assertEqual(json.loads(meta.read_bytes()), json.loads(was_meta), "the counter and the PO as they were")
        self.assertEqual(dashboard.load_session_projects(), {rid: proj["id"]})

    def test_which_tasks_are_refused(self):
        team = self.task(agents=("claude", "codex"))
        status, out = self.call({"roomId": team, "name": "E", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 400, out)
        self.assertIn("one agent", out["message"])
        blank = self.task(sid="")
        status, out = self.call({"roomId": blank, "name": "E", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 400, out)
        self.assertIn("no conversation yet", out["message"])
        status, out = self.call({"roomId": "room-nope", "name": "E", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 404, out)
        self.assertEqual(dashboard.load_projects(), [])
        po = self.task()
        ok, proj, _ = dashboard.register_project("Motors")
        dashboard.set_project_po(proj["id"], po)
        status, out = self.call({"roomId": po, "name": "E", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 409, out)


class TheSessionsFiles(Hub):
    """``bringFiles``: the session's files copied into the documents project
    it becomes the PO of (bringfiles.py has its own tests for the walk)."""

    FILES_URL = URL + "/files"

    def setUp(self):
        super().setUp()
        for rel, text in (("strategy.md", "# Strategy"), ("notes/2026/track record.csv", "a,b\n1,2\n"),
                          ("notes/ideas.txt", "ideas"), (".git/config", "[core]"), (".claude/settings.json", "{}"),
                          ("node_modules/x/index.js", "x"), ("__pycache__/a.pyc", "x"), ("lib/cache.pyc", "x"),
                          ("vendor/tool/.git/HEAD", "ref"), ("vendor/tool/main.py", "print()"),
                          ("Thumbs.db", "x"), ("project.json", '{"id": "proj-other"}')):
            f = self.work / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        self.kept = ["notes/2026/track record.csv", "notes/ideas.txt", "strategy.md"]

    def tree(self, folder):
        skip = (".history", "project.json")
        return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*")
                      if p.is_file() and p.relative_to(folder).parts[0] not in skip)

    def source_as_it_was(self):
        self.assertEqual(len([p for p in self.work.rglob("*") if p.is_file()]), 12, "copied, never moved")
        self.assertEqual((self.work / "strategy.md").read_text(encoding="utf-8"), "# Strategy")

    def test_a_new_documents_project_gets_the_files(self):
        status, seen = self.call_url(self.FILES_URL, {**self.session(), "kind": "documents"})
        self.assertEqual(status, 200, seen)
        self.assertEqual((seen["offer"], seen["files"], seen["folder"], seen["documents"]),
                         (True, 3, str(self.work), True))
        self.assertEqual(seen["bytes"], sum((self.work / k).stat().st_size for k in self.kept))
        self.assertFalse((self.root / "Strats").exists(), "the count makes nothing")
        status, out = self.call({**self.session(), "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual(status, 200, out)
        home = self.root / "Strats"
        self.assertEqual(self.tree(home), self.kept, "what a project never keeps is left out")
        self.assertEqual((home / "notes/2026/track record.csv").read_text(encoding="utf-8"), "a,b\n1,2\n")
        files = out["files"]
        self.assertEqual((files["ok"], files["copied"], files["alreadyThere"], files["from"]),
                         (True, 3, 0, str(self.work)))
        self.assertGreaterEqual(files["leftOut"], 7)
        self.assertNotIn("undo", files)
        self.assertNotIn("written", files)
        self.source_as_it_was()
        # One snapshot, credited to the person.
        log = dashboard.file_history.log(str(home))["entries"]
        self.assertEqual(len(log), 1, log)
        self.assertEqual(log[0]["who"]["kind"], "user")
        self.assertEqual(sorted(f["path"] for f in log[0]["files"]), self.kept)
        self.assertIn("3 files brought from", log[0]["subject"])
        # The PO is told, in its one first input.
        text = dashboard._RESUMES[out["room"]["id"]].queue[0]["text"]
        self.assertNotIn("\n", text)
        self.assertIn(f"copied to the project's folder {home} (3 files)", text)
        self.assertIn("that copy is the one to work on", text)
        self.assertIn(str(self.work), text)

    def test_not_asked_nothing_is_brought(self):
        status, out = self.call({**self.session(), "name": "Strats", "kind": "documents"})
        self.assertEqual(status, 200, out)
        self.assertNotIn("files", out)
        self.assertEqual(self.tree(self.root / "Strats"), [])
        self.assertNotIn("copied to", dashboard._RESUMES[out["room"]["id"]].queue[0]["text"])

    def test_a_code_project_is_untouched(self):
        status, seen = self.call_url(self.FILES_URL, {**self.session(), "kind": "code"})
        self.assertEqual((status, seen["offer"], seen["documents"], seen["reason"]), (200, False, False, ""))
        code = self.base / "code" / "engine"
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(code),
                                 "bringFiles": True})
        self.assertEqual(status, 200, out)
        self.assertFalse(out["files"]["ok"])
        self.assertIn("only into a documents project", out["files"]["message"])
        self.assertEqual(self.tree(code), [])
        self.assertEqual(self.tree(dashboard.project_home(out["project"], create=False)), [])
        self.assertNotIn("copied to", dashboard._RESUMES[out["room"]["id"]].queue[0]["text"])

    def existing(self, kind="documents"):
        ok, proj, _ = dashboard.register_project("Papers")
        if kind == "documents":
            self.assertEqual(dashboard.set_project_kind(proj["id"], "documents"), (True, "ok"))
        home = Path(dashboard.project_home(proj, create=False))
        (home / "strategy.md").write_text("the project's own", encoding="utf-8")
        (home / "will.txt").write_text("mine", encoding="utf-8")
        return proj["id"], home

    def test_an_existing_documents_project_keeps_what_it_has(self):
        pid, home = self.existing()
        status, seen = self.call_url(self.FILES_URL, {**self.session(), "projectId": pid})
        self.assertEqual((status, seen["offer"], seen["files"]), (200, True, 3))
        status, out = self.call({**self.session(), "projectId": pid, "bringFiles": True})
        self.assertEqual(status, 200, out)
        self.assertEqual((out["files"]["copied"], out["files"]["alreadyThere"]), (2, 1))
        self.assertEqual((home / "strategy.md").read_text(encoding="utf-8"), "the project's own", "never overwritten")
        self.assertEqual(self.tree(home), sorted(self.kept + ["will.txt"]))
        self.assertIn("(2 files)", dashboard._RESUMES[out["room"]["id"]].queue[0]["text"])

    def test_an_existing_code_project_is_not_offered_them(self):
        pid, home = self.existing(kind="code")
        status, seen = self.call_url(self.FILES_URL, {**self.session(), "projectId": pid})
        self.assertEqual((status, seen["offer"], seen["documents"]), (200, False, False))
        status, out = self.call({**self.session(), "projectId": pid, "bringFiles": True})
        self.assertEqual((status, out["files"]["ok"]), (200, False), out)
        self.assertEqual(self.tree(home), ["strategy.md", "will.txt"])

    def test_a_failed_start_takes_the_copy_back_with_the_new_project(self):
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({**self.session(), "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual(status, 400, out)
        self.nothing_left()
        self.assertEqual(list(self.root.iterdir()), [], "the home, its files and its history are gone")
        self.source_as_it_was()

    def test_a_failed_start_takes_back_only_what_it_copied_into_an_existing_project(self):
        pid, home = self.existing()
        (home / "notes").mkdir()
        (home / "notes" / "mine.txt").write_text("mine", encoding="utf-8")
        self.start_error = RuntimeError("no terminal")
        status, out = self.call({**self.session(), "projectId": pid, "bringFiles": True})
        self.assertEqual(status, 400, out)
        self.assertEqual(self.tree(home), ["notes/mine.txt", "strategy.md", "will.txt"])
        self.assertFalse((home / "notes" / "2026").exists(), "a folder it made goes; one that was there stays")
        self.assertEqual((home / "strategy.md").read_text(encoding="utf-8"), "the project's own")
        self.assertFalse(dashboard.find_project(pid).get("poRoomId"))
        self.nothing_left(projects=1)
        self.source_as_it_was()

    def failing_write(self, after):
        """open() as bringfiles sees it: the disk is full from the file after ``after``."""
        made = []

        def fake(path, mode="r", *a, **k):
            if "x" in mode:
                made.append(path)
                if len(made) > after:
                    raise OSError(28, "No space left on device")
            return open(path, mode, *a, **k)
        return mock.patch.object(dashboard.bringfiles, "open", fake, create=True)

    def test_a_copy_that_fails_half_way_leaves_nothing(self):
        with self.failing_write(after=1):
            status, out = self.call({**self.session(), "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual((status, out.get("error")), (400, "cannot_make_po"), out)
        self.assertIn("No space left", out["message"])
        self.assertIn("No PO was made", out["message"])
        self.nothing_left()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.started, [])
        # Into an existing project: only what this request wrote goes.
        pid, home = self.existing()
        with self.failing_write(after=1):
            status, out = self.call({**self.session(), "projectId": pid, "bringFiles": True})
        self.assertEqual(status, 400, out)
        self.assertEqual(self.tree(home), ["strategy.md", "will.txt"])
        self.assertFalse((home / "notes").exists())
        self.nothing_left(projects=1)
        self.source_as_it_was()

    def test_a_home_folder_or_a_drive_is_not_offered(self):
        with mock.patch.object(dashboard.bringfiles, "user_home", lambda: str(self.work)):
            status, seen = self.call_url(self.FILES_URL, {**self.session(), "kind": "documents"})
            self.assertEqual((status, seen["offer"], seen["files"]), (200, False, 0))
            self.assertIn("your home folder", seen["reason"])
            self.assertNotIn("\n", seen["reason"])
            # Asked all the same: the project is made, without the files, and the reply says why.
            status, out = self.call({**self.session(), "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["files"]["ok"], False)
        self.assertIn("your home folder", out["files"]["message"])
        self.assertEqual(self.tree(self.root / "Strats"), [])
        self.assertNotIn("copied to", dashboard._RESUMES[out["room"]["id"]].queue[0]["text"])
        with mock.patch.object(dashboard.bringfiles, "user_home", lambda: str(self.work / "deeper" / "me")):
            self.assertIn("home folder", dashboard.bringfiles.refusal(str(self.work)), "a folder above it too")
        drive = os.path.abspath(os.sep)
        self.assertIn("a whole drive", dashboard.bringfiles.refusal(drive))
        # The projects folder itself, and a folder already in the project's.
        self.assertIn("projects folder", dashboard.bringfiles.refusal(str(self.base), "", str(self.root)))
        home = self.root / "Strats"
        (home / "sub").mkdir()
        self.assertIn("already in the project's folder", dashboard.bringfiles.refusal(str(home / "sub"), str(home)))

    def test_past_the_bound_the_project_is_made_without_them(self):
        for patch, words in ((mock.patch.object(dashboard.bringfiles, "MAX_FILES", 2), "more than 2 files"),
                             (mock.patch.object(dashboard.bringfiles, "MAX_BYTES", 12), "more than 0.0 MB")):
            with patch:
                status, seen = self.call_url(self.FILES_URL, {**self.session(), "kind": "documents"})
                self.assertEqual((status, seen["offer"]), (200, False))
                self.assertIn(words, seen["reason"])
                name = f"Strats {len(dashboard.load_projects())}"
                status, out = self.call({**self.session(sessionId=name), "name": name, "kind": "documents",
                                         "bringFiles": True})
            self.assertEqual(status, 200, out)
            self.assertEqual(out["files"]["ok"], False)
            self.assertIn(words, out["files"]["message"])
            self.assertIn("by hand", out["files"]["message"])
            self.assertEqual(self.tree(self.root / name), [])
            self.assertEqual(out["project"]["poRoomId"], out["room"]["id"], "the project is still made")

    def test_a_folder_that_cannot_be_counted_in_time(self):
        def slow(src, out, stop):
            while not stop():
                time.sleep(0.02)
            out["why"] = "time"

        with mock.patch.object(dashboard.bringfiles, "_walk", slow), \
                mock.patch.object(dashboard.bringfiles, "PREVIEW_S", 0.3):
            began = time.monotonic()
            seen = dashboard.bringfiles.preview(str(self.work), seconds=0.3)
            self.assertLess(time.monotonic() - began, 2.0)
        self.assertEqual(seen["offer"], False)
        self.assertIn("could not be counted in 0.3 seconds", seen["reason"])

        def stuck(src, out, stop):          # one directory read that never comes back
            time.sleep(3)

        with mock.patch.object(dashboard.bringfiles, "_walk", stuck):
            began = time.monotonic()
            found = dashboard.bringfiles.scan(str(self.work), 0.2)
            self.assertLess(time.monotonic() - began, 1.5)
        self.assertEqual((found["why"], found["files"]), ("time", []))

    def test_a_path_too_long_is_skipped_and_counted(self):
        long_name = "a file with a rather long name " * 3 + ".txt"
        (self.work / "notes" / long_name).write_text("x", encoding="utf-8")
        home = self.root / "Strats"
        room_for = len(os.path.abspath(home / "notes" / "2026" / "track record.csv"))    # the longest that fits
        with mock.patch.object(dashboard.bringfiles, "PATH_MAX", room_for):
            status, out = self.call({**self.session(), "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual(status, 200, out)
        self.assertEqual((out["files"]["copied"], out["files"]["tooLong"]), (3, 1))
        self.assertEqual(self.tree(home), self.kept)

    def test_a_task_brings_the_folder_its_agent_works_in(self):
        rid = chatroom.create_room("Research", [{"identity": "claude", "agent": "claude"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"] = str(self.work), "solo"
        part = chatroom.agent_participants(full)[0]
        part["sessionId"], part["cwd"] = "sid-task-9", str(self.work)
        chatroom.update_room(full)
        status, seen = self.call_url(self.FILES_URL, {"roomId": rid, "kind": "documents"})
        self.assertEqual((status, seen["offer"], seen["files"], seen["folder"]), (200, True, 3, str(self.work)))
        status, out = self.call({"roomId": rid, "name": "Strats", "kind": "documents", "bringFiles": True})
        self.assertEqual(status, 200, out)
        self.assertEqual(self.tree(self.root / "Strats"), self.kept)

    def test_a_page_on_another_site_is_refused_the_count_too(self):
        status, out = self.call_url(self.FILES_URL, {**self.session(), "kind": "documents"},
                                    origin="https://elsewhere.example")
        self.assertEqual((status, out["error"]), (403, "cross_origin"))


class WhatTheHubTypes(unittest.TestCase):
    PROJECT = {"id": "p1", "name": "Engine", "kind": "code", "path": "/code/engine"}

    def test_the_prefix_is_a_hub_input_kind(self):
        self.assertIn((dashboard.MADE_PO_PREFIX, "madepo"), dashboard.HUB_INPUT_KINDS)

    def test_the_chat_reads_it_as_the_hubs(self):
        with mock.patch.object(dashboard, "operator_name", lambda: "sam"):
            text = dashboard.made_po_first_input(self.PROJECT)
        self.assertNotIn("\n", text)
        self.assertFalse(dashboard.typed_by_person(text))
        turns = dashboard.classify_turns([{"role": "user", "text": text}, {"role": "assistant", "text": "Understood."},
                                          {"role": "user", "text": "Go on"}, {"role": "assistant", "text": "Ok"}])
        self.assertEqual([t.get("kind") or t["answers"]["kind"] for t in turns], ["madepo", "madepo", "human", "human"])

    def test_the_session_page_shows_it_and_keeps_its_answer_for_the_person(self):
        self.assertIn("madepo: 'Made PO'", SESSION)
        self.assertIn("madepo: ['on becoming PO'", SESSION)
        self.assertIn("const forPersonKind = k => k === 'human' || k === 'madepo';", SESSION)
        self.assertIn("!forPersonKind(m.answers.kind) && !isDecision(m)", SESSION, "Just us does not fold the answer")
        self.assertIn("forPersonKind(m.answers.kind) ? 'answer' : ''", SESSION)


class _FakePty:
    def __init__(self):
        self.typed, self._last_submit, self.last_input = [], 0.0, 0.0

    def alive(self):
        return True

    def send_line(self, text):
        self.typed.append(text)
        self._last_submit = time.time()
        return True

    def last_submit(self):
        return self._last_submit


class NotWithoutAHandover(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hp = Path(self.temp.name) / rotation.HANDOVER_NAME
        self.sess, self.idle, self.rotated, self.notices = _FakePty(), True, [], []
        self.tr = {"tokens": 250_000, "turnOver": True, "promptSince": False, "size": 1000}
        for p in (mock.patch.object(rotation, "_pty", side_effect=lambda part: self.sess),
                  mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
                  mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (
                      Path(self.temp.name) / "t.jsonl", lambda path, since=-1: dict(self.tr))),
                  mock.patch.object(rotation, "_rotate_marked", side_effect=self.rotate_marked),
                  mock.patch.object(rotation, "_log"),
                  mock.patch.object(dashboard.chatroom, "post_notice",
                                    side_effect=lambda rid, sender, text, meta: self.notices.append(text)),
                  mock.patch.object(dashboard, "operator_name", return_value="sam")):
            p.start()
            self.addCleanup(p.stop)

    def rotate_marked(self, s, tr, done, answered, asked, key, flags):
        self.rotated.append({"answered": answered, "asked": asked})
        s["state"]["phase"] = "watching"
        return done("rotated")

    def po(self, **state):
        part = {"identity": "claude", "agent": "claude", "sessionId": "sid-1", "ptyId": "pty-1"}
        return {"room": {"id": "room-1"}, "part": part, "why": "", "handover": self.hp,
                "state": {"phase": "watching", "sessionId": "sid-1", **state},
                "kind": "po", "name": "P", "project": {"id": "p1"}, "limit": 150_000,
                "setting": "poRotateTokens", "who": "the PO", "whose": "the PO's",
                "handoverName": rotation.HANDOVER_NAME, "ids": {"projectId": "p1"}}

    def asked(self, ago=60.0):
        at = time.time() - ago
        self.sess._last_submit = at
        return self.po(phase="asked", askedAt=at, askSize=900, askPath=str(Path(self.temp.name) / "t.jsonl"),
                       askSubmit=at, tokensAtAsk=250_000, handoverAtAsk=0)

    def test_handover_written(self):
        self.assertFalse(rotation.handover_written(self.hp))
        self.assertFalse(rotation.handover_written(None))
        self.hp.write_text("  \n", encoding="utf-8")
        self.assertFalse(rotation.handover_written(self.hp), "an empty file says nothing")
        self.hp.write_text("# Engine\n", encoding="utf-8")
        self.assertTrue(rotation.handover_written(self.hp))

    def test_the_ask_says_the_file_is_missing(self):
        out = rotation._check(self.po(), False, False)
        self.assertEqual(out["phase"], "asked")
        self.assertIn("does not exist yet, or is empty, and you are not rotated without it", self.sess.typed[0])
        self.hp.write_text("# Engine\n", encoding="utf-8")
        self.sess.typed.clear()
        rotation._check(self.po(), False, False)
        self.assertIn(f"Bring {self.hp} up to date now", self.sess.typed[0])

    def test_an_answer_without_the_file_does_not_rotate(self):
        s = self.asked()
        self.tr["promptSince"] = True
        out = rotation._check(s, False, False)
        self.assertEqual((self.rotated, out["phase"]), ([], "watching"))
        self.assertIn("not rotated", out["result"])
        self.assertIn("lastAttempt", s["state"], "asked again after the cool-down")
        self.assertEqual(len(self.notices), 1)
        self.assertIn("was not replaced by a fresh session", self.notices[0])
        # Asked again later, still nothing written: the room is told once per session.
        s["state"].update(phase="asked", askedAt=time.time() - 60)
        rotation._check(s, False, False)
        self.assertEqual((self.rotated, len(self.notices)), ([], 1))

    def test_no_answer_until_the_timeout_does_not_rotate_either(self):
        s = self.asked(ago=rotation.ASK_TIMEOUT_S + 30)
        out = rotation._check(s, False, False)
        self.assertEqual((self.rotated, out["phase"]), ([], "watching"))

    def test_rotate_now_does_not_either(self):
        out = rotation._check(self.po(), True, True)
        self.assertEqual(self.rotated, [])
        self.assertIn("not rotated", out["result"])

    def test_with_the_file_written_it_rotates(self):
        self.hp.write_text("# Engine\n", encoding="utf-8")
        s = self.asked()
        self.tr["promptSince"] = True
        rotation._check(s, False, False)
        self.assertEqual(self.rotated, [{"answered": True, "asked": True}])

    def test_a_task_owner_is_not_held_by_this(self):
        s = self.asked()
        s.update(kind="owner", project=None, limit=200_000, setting="taskRotateTokens",
                 handoverName=rotation.TASK_HANDOVER_NAME, ids={"roomId": "room-1", "identity": "claude"})
        self.tr["promptSince"] = True
        rotation._check(s, False, False)
        self.assertEqual(len(self.rotated), 1)

    def test_being_made_po_starts_the_cool_down(self):
        now = time.time()
        self.assertEqual(rotation._session_started({"sessionId": "s", "madePoAt": now}), now)
        self.assertEqual(rotation._session_started({"sessionId": "s2", "madePoAt": now - 50, "rotations": [
            {"toSessionId": "s2", "at": now}]}), now, "a later rotation counts from itself")
        out = rotation._check({**self.po(), "part": {**self.po()["part"], "madePoAt": now - 60}}, False, False)
        self.assertEqual((out["phase"], self.sess.typed), ("watching", []))


class TheFirstFreshSession(unittest.TestCase):
    """Made a PO, the conversation went on where it was started; the fresh
    session of its first rotation starts where a PO works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.was, self.code = base / "was", base / "code"
        self.was.mkdir()
        self.code.mkdir()
        self.launches = []

        def launch(room, part, task, collab=True, prompt=None, cwd=None):
            self.launches.append({"cwd": cwd, "prompt": prompt})
            return {"ptyId": "pty-new", "cwd": cwd or room.get("cwd", ""), "sessionId": "sid-new"}

        for p in (mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
                  mock.patch.object(dashboard, "hub_launcher",
                                    lambda: SimpleNamespace(_launch_room_agent_pty=launch)),
                  mock.patch.object(dashboard.ptyrun, "kill"),
                  mock.patch.object(rotation, "_await_death"),
                  mock.patch.object(rotation, "_log")):
            p.start()
            self.addCleanup(p.stop)

    def rotate(self, next_cwd):
        rid = chatroom.create_room("Engine notes", [{"identity": "claude", "agent": "claude"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"] = str(self.was), "solo"
        part = chatroom.agent_participants(full)[0]
        part.update(sessionId="sid-old", cwd=str(self.was), ptyId="pty-old", nextCwd=next_cwd)
        chatroom.update_room(full)
        project = {"id": "p1", "name": "Engine", "path": str(self.code)}
        s = {"kind": "po", "name": "Engine", "project": project, "room": full, "part": part, "why": "",
             "state": {"phase": "asked", "tokensAtAsk": 250_000, "handoverAtAsk": 0}, "limit": 150_000,
             "setting": "poRotateTokens", "who": "the PO", "whose": "the PO's",
             "handoverName": rotation.HANDOVER_NAME, "handover": self.code / rotation.HANDOVER_NAME,
             "ids": {"projectId": "p1"}}
        out = rotation._rotate_marked(s, {"tokens": 250_000}, lambda r, quiet=False, **x: {"result": r, **x},
                                      True, True, (rid, "claude"), {"stopped": False})
        self.assertIn("rotated at 250k", out["result"])
        room = chatroom.get_room(rid, public=False)
        return room, chatroom.agent_participants(room)[0]

    def test_it_starts_in_the_pos_folder(self):
        room, part = self.rotate(str(self.code))
        self.assertEqual(self.launches[0]["cwd"], str(self.code))
        self.assertEqual((room["cwd"], part["cwd"], part["sessionId"]), (str(self.code), str(self.code), "sid-new"))
        self.assertNotIn("nextCwd", part, "once")

    def test_a_task_that_went_away_meanwhile_is_not_moved(self):
        rid = chatroom.create_room("Engine notes", [{"identity": "claude", "agent": "claude"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"] = str(self.was), "solo"
        part = chatroom.agent_participants(full)[0]
        part.update(sessionId="sid-old", cwd=str(self.was), ptyId="pty-old", nextCwd=str(self.code))
        chatroom.update_room(full)
        s = {"kind": "po", "name": "Engine", "project": {"id": "p1", "name": "Engine", "path": str(self.code)},
             "room": full, "part": part, "why": "", "state": {"phase": "asked"}, "limit": 150_000,
             "setting": "poRotateTokens", "who": "the PO", "whose": "the PO's",
             "handoverName": rotation.HANDOVER_NAME, "handover": self.code / rotation.HANDOVER_NAME,
             "ids": {"projectId": "p1"}}
        patches = []
        with mock.patch.object(chatroom, "patch_participant", return_value=None), \
                mock.patch.object(chatroom, "patch_room", side_effect=lambda *a, **k: patches.append(k)), \
                mock.patch.object(rotation, "_discard_fresh") as discard:
            out = rotation._rotate_marked(s, {"tokens": 250_000}, lambda r, quiet=False, **x: {"result": r, **x},
                                          True, True, (rid, "claude"), {"stopped": False})
        self.assertIn("went away while rotating", out["result"])
        self.assertEqual(patches, [], "the room's folder is left as it was")
        self.assertTrue(discard.called)
        self.assertEqual(chatroom.get_room(rid, public=False)["cwd"], str(self.was))

    def test_a_folder_that_is_gone_is_not_moved_to(self):
        room, part = self.rotate(str(self.code / "gone"))
        self.assertEqual(self.launches[0]["cwd"], str(self.was))
        self.assertEqual((room["cwd"], part["cwd"]), (str(self.was), str(self.was)))
        self.assertNotIn("nextCwd", part)


@unittest.skipUnless(NODE, "node is not installed")
class ThePage(unittest.TestCase):
    JS = r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
%s
const now = 1_000_000;
const rows = [
  { sessionId: 'old', cwd: 'C:\\work\\engine', agent: 'claude', label: 'Engine notes', updatedAt: now - 7200 },
  { sessionId: 'new', cwd: 'C:\\work\\papers', agent: 'codex', label: '', updatedAt: now - 120 },
  { sessionId: 'live', cwd: 'C:\\work\\x', isLive: true, updatedAt: now - 5 },
  { sessionId: 'nofolder', cwd: '', updatedAt: now },
  { sessionId: 'lost', cwd: 'C:\\work\\y', orphan: true, updatedAt: now },
  { sessionId: 'room-1', roomId: 'room-1', cwd: 'C:\\work\\z', updatedAt: now },
];
const solo = { roomId: 'room-1', members: [{ agent: 'Claude' }] };
const out = {
  offered: makePoSessions(rows).map(r => r.sessionId),
  none: makePoSessions(null),
  task: [makePoTaskOk(solo, false, false), makePoTaskOk(solo, true, false), makePoTaskOk(solo, false, true),
         makePoTaskOk({ ...solo, draft: true }, false, false),
         makePoTaskOk({ roomId: 'r', members: [{ agent: 'claude' }, { agent: 'codex' }] }, false, false),
         makePoTaskOk({ roomId: 'r', members: [{ agent: 'gemini' }] }, false, false), makePoTaskOk(rows[0], false, false)],
  live: [makePoLiveNote(rows[0], now), makePoLiveNote(rows[1], now), makePoLiveNote({ ...rows[1], updatedAt: now - 20 }, now),
         makePoLiveNote({ roomId: 'room-1', updatedAt: now }, now)],
  dialog: makePoDialogHtml(rows[0], now, ''),
  named: makePoDialogHtml(rows[1], now, ''),
  titles: [makePoTitle(rows[0]), makePoTitle({ sessionId: 'abc', first: ' ' + 'We plan a boat trip. '.repeat(5) }), makePoTitle({ sessionId: 'abc' })],
  again: makePoDialogHtml(rows[0], now, 'The folder <x> is already a project.', { name: 'My "papers"', kind: 'documents', path: 'D:\\else' }),
  bodies: [makePoBody(rows[0], { name: 'Engine', kind: 'code', path: 'C:\\code' }), makePoBody(rows[5], { projectId: 'p1' }),
           makePoBody({ sessionId: 's', cwd: 'c' }, { projectId: 'p1' }),
           makePoBody(rows[0], { name: 'Strats', kind: 'documents', path: '', bringFiles: true })],
};
const info = { documents: true, offer: true, reason: '', folder: 'C:\\cs\\01 <opts>', files: 56, bytes: 38 * 1048576,
               notInBackup: 0, backupCapBytes: 20 * 1048576 };
const docs = { name: 'Strats', kind: 'documents', path: '' };
out.files = {
  sizes: [makePoSize(0), makePoSize(2048), makePoSize(1.26 * 1048576), makePoSize(38.4 * 1048576)],
  box: makePoBringHtml(info),
  off: makePoBringHtml(info, false),
  big: makePoBringHtml({ ...info, files: 1, notInBackup: 1 }),
  refused: makePoBringHtml({ ...info, offer: false, reason: 'The session was started in D:\\me, which is your home folder <x>.' }),
  code: makePoBringHtml({ ...info, documents: false, offer: false }),
  unknown: makePoBringHtml(null),
  dialogDocs: makePoDialogHtml(rows[0], now, '', docs, info),
  dialogCode: makePoDialogHtml(rows[0], now, '', { ...docs, kind: 'code' }, info),
  dialogOff: makePoDialogHtml(rows[0], now, 'refused', { ...docs, bringFiles: false }, info),
  dialogNoCount: makePoDialogHtml(rows[0], now, '', docs),
  notes: [makePoBroughtNote(undefined),
          makePoBroughtNote({ ok: true, copied: 56, bytes: 38 * 1048576, alreadyThere: 0, tooLong: 0, unreadable: 0, leftOut: 0, notInBackup: [] }),
          makePoBroughtNote({ ok: true, copied: 1, bytes: 2048, alreadyThere: 2, tooLong: 1, unreadable: 1, leftOut: 4, notInHistory: 1,
                              notInBackup: [{ path: 'data/ticks.bin', size: 60 * 1048576, inHistory: false }], notInBackupCount: 3 }),
          makePoBroughtNote({ ok: false, message: 'The files in C:\\x are not brought: it holds more than 2,000 files.' }),
          makePoBroughtNote({ ok: true, copied: 0, bytes: 0, alreadyThere: 3 })],
  more: [makePoBroughtMore(undefined), makePoBroughtMore({ ok: true, copied: 5, leftOut: 2 }), makePoBroughtMore({ ok: true, copied: 5, alreadyThere: 1 }),
         makePoBroughtMore({ ok: false, message: 'x' }), makePoBroughtMore({ ok: true, copied: 1, notInBackup: [{ path: 'a', size: 1 }] })],
};
console.log(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- A past session made a PO: begin")
        block = INDEX[i:INDEX.index("// ---- A past session made a PO: end", i)]
        r = subprocess.run([NODE, "-"], input=cls.JS % block, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_which_sessions_are_offered(self):
        self.assertEqual(self.out["offered"], ["new", "old"], "the hub's own, a live one, a lost one and one "
                                                              "without a folder are not; newest first")
        self.assertEqual(self.out["none"], [])

    def test_which_tasks_are_offered(self):
        self.assertEqual(self.out["task"], [True, False, False, False, False, False, False])

    def test_a_session_that_may_still_be_open_in_a_terminal(self):
        old, recent, moment, task = self.out["live"]
        self.assertEqual((old, task), ("", ""))
        self.assertIn("written to 2 minutes ago", recent)
        self.assertIn("close it there first", recent)
        self.assertIn("1 minute ago", moment)

    def test_the_dialog(self):
        d = self.out["dialog"]
        self.assertIn('id="mp-name" value="Engine notes"', d)
        self.assertIn('id="mp-path" value="C:\\work\\engine"', d)
        self.assertIn('value="code" checked', d)
        self.assertNotIn('role="note"', d, "an old session carries no warning")
        self.assertNotIn('role="alert"', d)
        self.assertIn('id="mp-name" value="papers"', self.out["named"], "an unnamed session takes its folder's name")
        self.assertIn('role="note"', self.out["named"])

    def test_a_session_is_named_as_the_person_knows_it(self):
        label, first, bare = self.out["titles"]
        self.assertEqual((label, bare), ("Engine notes", "abc"))
        self.assertTrue(first.startswith("We plan a boat trip.") and first.endswith("…") and len(first) == 60, first)
        self.assertIn("“Engine notes” becomes the PO", self.out["dialog"])
        self.assertIn("dialog .im-field .kind-opt input { width: auto;", INDEX, "a radio is not a full-width text box")

    def test_a_refusal_reopens_the_dialog_as_it_was_left(self):
        d = self.out["again"]
        self.assertIn('role="alert">The folder &lt;x&gt; is already a project.', d)
        self.assertIn('value="My &quot;papers&quot;"', d)
        self.assertIn('value="documents" checked', d)
        self.assertNotIn('value="code" checked', d)
        self.assertIn('id="mp-path-field" hidden', d)
        self.assertIn('value="D:\\else"', d)

    def test_what_the_hub_is_asked(self):
        new, task, bare, bringing = self.out["bodies"]
        self.assertEqual((bringing["kind"], bringing["bringFiles"], bringing["cwd"]), ("documents", True, "C:\\work\\engine"))
        self.assertEqual(new, {"sessionId": "old", "cwd": "C:\\work\\engine", "agent": "claude", "label": "Engine notes",
                               "name": "Engine", "kind": "code", "path": "C:\\code"})
        self.assertEqual(task, {"roomId": "room-1", "projectId": "p1"})
        self.assertEqual(bare, {"sessionId": "s", "cwd": "c", "agent": "claude", "label": "", "projectId": "p1"})

    def test_the_box_that_brings_the_sessions_files(self):
        f = self.out["files"]
        self.assertEqual(f["sizes"], ["1 KB", "2 KB", "1.3 MB", "38 MB"])
        self.assertIn('<input type="checkbox" id="mp-bring" checked> Bring the files from C:\\cs\\01 &lt;opts&gt; '
                      'into the project (56 files, 38 MB)', f["box"])
        self.assertIn("left as it is", f["box"])
        self.assertNotIn("backup", f["box"])
        self.assertIn('id="mp-bring">', f["off"], "switched off, it re-opens off")
        self.assertIn("(1 file, 38 MB)", f["big"])
        self.assertIn("1 file over 20 MB is copied too, but will not be in the backup.", f["big"])
        # Not offered: one line saying why, and no box; nothing at all for a code project or before the count.
        self.assertNotIn("mp-bring", f["refused"])
        self.assertIn("your home folder &lt;x&gt;.", f["refused"])
        self.assertEqual((f["code"], f["unknown"]), ("", ""))

    def test_the_box_shows_for_a_documents_project_only(self):
        f = self.out["files"]
        self.assertIn('id="mp-bring-field"><label class="kind-opt"><input type="checkbox" id="mp-bring" checked>', f["dialogDocs"])
        self.assertIn('id="mp-bring-field" hidden><label', f["dialogCode"], "there, and hidden until the kind changes")
        self.assertIn('id="mp-bring">', f["dialogOff"])
        self.assertIn('id="mp-bring-field" hidden></div>', f["dialogNoCount"])
        self.assertIn('id="mp-bring-field" hidden></div>', self.out["dialog"])
        # The page: the field follows the kind, the count is asked once, and Make PO waits for it.
        self.assertIn("$('#mp-bring-field').hidden = !docs || !$('#mp-bring-field').firstChild;", INDEX)
        self.assertIn("if (kind === 'documents') { $('#mp-ok').disabled = true; await counted; }", INDEX)
        self.assertIn("...(bring ? { bringFiles: kind === 'documents' && bring.checked } : {})", INDEX)
        self.assertIn("makePoFlow(r, histErr(e), target, info);", INDEX, "a refusal re-opens it without counting again")
        self.assertIn("if (!chosen || !isDocsProject(pj)) { counted = Promise.resolve(); return; }", INDEX,
                      "Choose the PO offers it to a documents project only")
        self.assertIn("'/api/projects/po-from-session/files'", INDEX)

    def test_the_notice_says_what_became_of_the_files(self):
        none, plain, mixed, refused, nothing = self.out["files"]["notes"]
        self.assertEqual(none, "")
        self.assertEqual(plain, "56 files (38 MB) were copied into the project’s folder.")
        for words in ("1 file (2 KB) was copied", "2 files already there were left as they are and not copied.",
                      "1 with a path too long to open was skipped.", "1 that could not be read was skipped.",
                      "4 left out: what a project never keeps",
                      "Too large for the backup, so only in the project’s folder: data/ticks.bin (60 MB) and 2 more.",
                      "1 file too large for the file history as well."):
            self.assertIn(words, mixed)
        self.assertIn("more than 2,000 files", refused)
        self.assertTrue(nothing.startswith("No files were copied"))
        self.assertEqual(self.out["files"]["more"], [False, False, True, True, True],
                         "it stays until dismissed only when it says more than copied")
        self.assertIn("makePoBroughtMore(resp.files) ? 0 : (note ? 8000 : 3000)", INDEX)

    def test_where_the_page_offers_it(self):
        self.assertRegex(INDEX, r'\(!isLive && r\.cwd && !r\.roomId && !r\.orphan\)\s*\? `<button class="makepo-btn" data-sid=')
        self.assertIn('class="ov-item makepo-btn" data-room=', INDEX)
        self.assertIn("ev.target.closest('.makepo-btn')", INDEX)
        self.assertIn("dialog .im-field[hidden] { display: none; }", INDEX, "display:flex would defeat hidden")


if __name__ == "__main__":
    unittest.main()
