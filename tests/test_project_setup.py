"""Set up a project from a folder and a conversation already had.

* who may become a PO: make_po_verdict, one code per reason, for Claude and
  Codex conversations and for tasks; the hub's words and the page's
  (makePoWords) are the same text;
* GET /api/projects/po-candidates: the folder New project would register, and
  the conversations offered for a new or an existing project, folder first,
  with a verdict each; another project's tasks only when they work in the
  folder; refused to a page on another site;
* POST /api/projects/po-from-session: a conversation open in a terminal is
  refused, one written to recently only once the person says it is closed; a
  fresh PO, and a fresh PO that does not start leaving nothing; the code
  folder is left as it was; no task number is taken;
* the page (Node): the folder's line, the groups, the dialog, what confirming
  asks of the hub, and the menu item's reason.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import chatroom
import dashboard
from tests.test_make_po import INDEX, NODE, PORT, Hub

V = dashboard.make_po_verdict
NOW = 1_000_000.0


def session_facts(**over):
    return {"kind": "session", "agents": ["claude"], "installed": ("claude", "codex"), "cwd": "C:\\w",
            "cwdOk": True, "isLive": False, "seen": True, "updatedAt": NOW - 7200, **over}


def task_facts(**over):
    return {"kind": "task", "agents": ["claude"], "installed": ("claude", "codex"), "draft": False,
            "conversation": True, "poOf": "", "project": None, **over}


class TheVerdict(unittest.TestCase):
    def code(self, f, target=None):
        return V(f, target, NOW)["code"]

    def test_a_session_of_either_agent(self):
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                self.assertEqual(V(session_facts(agents=[agent]), None, NOW), {"ok": True, "code": ""})
                self.assertEqual(self.code(session_facts(agents=[agent], isLive=True)), "live")
                v = V(session_facts(agents=[agent], updatedAt=NOW - 125), None, NOW)
                self.assertEqual((v["ok"], v["code"], v["confirm"], v["min"]), (True, "recent", "closed", 2))
                other = tuple(a for a in ("claude", "codex") if a != agent)
                self.assertEqual(self.code(session_facts(agents=[agent], installed=other)), "not_installed")
                self.assertEqual(self.code(session_facts(agents=[agent], cwdOk=False)), "no_cwd")
                self.assertEqual(self.code(session_facts(agents=[agent], heldBy="Research")), "held")
                self.assertEqual(self.code(session_facts(agents=[agent], poOf="Opten")), "is_po")

    def test_the_first_reason_that_applies_is_given(self):
        self.assertEqual(self.code({"kind": "orphan"}), "orphan")
        self.assertEqual(self.code(session_facts(agents=["gemini"])), "unsupported")
        self.assertEqual(self.code(session_facts(isLive=True, heldBy="Research")), "held", "the task, not the terminal")
        self.assertEqual(self.code(session_facts(cwd="", cwdOk=False)), "no_cwd")
        self.assertEqual(self.code(session_facts(isLive=True), {"id": "p", "name": "Motors", "po": "Motors PO"}),
                         "has_po")
        self.assertEqual(V(session_facts(updatedAt=NOW - 15 * 60), None, NOW)["code"], "", "15 minutes on, it asks nothing")

    def test_a_task(self):
        self.assertEqual(self.code(task_facts()), "")
        self.assertEqual(self.code(task_facts(agents=["codex"])), "")
        self.assertEqual(self.code(task_facts(draft=True)), "draft")
        self.assertEqual(V(task_facts(agents=["claude", "codex"]), None, NOW)["n"], 2)
        self.assertEqual(self.code(task_facts(conversation=False)), "no_conversation")
        own = {"id": "p1", "name": "Motors"}
        self.assertEqual(self.code(task_facts(project=own)), "other_project", "not for a new project")
        self.assertEqual(self.code(task_facts(project=own), {"id": "p1", "name": "Motors", "po": ""}), "",
                         "its own project may take it")
        self.assertEqual(self.code(task_facts(project=own), {"id": "p2", "name": "Cars", "po": ""}), "other_project")
        self.assertEqual(self.code(task_facts(isLive=True)), "", "a task the hub runs is not split by a terminal")

    def test_a_machine_with_neither_agent(self):
        self.assertEqual(self.code(session_facts(installed=())), "not_installed")
        self.assertEqual(self.code(session_facts(agents=["codex"], installed=())), "not_installed")
        self.assertEqual(self.code(task_facts(installed=())), "not_installed")
        self.assertEqual(self.code(session_facts(installed=None)), "", "not said: taken as both there")

    def test_a_session_the_hub_cannot_see_needs_the_persons_word(self):
        for f in (session_facts(seen=False), {k: v for k, v in session_facts().items() if k != "seen"},
                  session_facts(agents=["codex"], seen=False, updatedAt=NOW - 86400)):
            v = V(f, None, NOW)
            self.assertEqual((v["ok"], v["code"], v.get("confirm")), (True, "unseen", "closed"), f)
        self.assertEqual(self.code(session_facts(seen=False, isLive=True)), "live")
        self.assertEqual(self.code(task_facts(seen=False)), "", "a task runs in the hub, which sees it")
        # The listing: nothing shows whether a Codex started in a terminal is open.
        facts = dashboard._PoFacts.__new__(dashboard._PoFacts)
        facts.installed, facts._dirs = ("claude", "codex"), {}
        codex = facts.of_row({"sessionId": "s", "agent": "codex", "cwd": "C:\\w", "updatedAt": NOW - 86400})
        claude = facts.of_row({"sessionId": "s", "agent": "claude", "cwd": "C:\\w", "updatedAt": NOW - 86400})
        self.assertEqual((codex["seen"], claude["seen"]), (False, True))

    def test_every_refusal_says_why_and_what_to_do(self):
        for v in VERDICTS:
            with self.subTest(code=v["code"]):
                w = dashboard.make_po_words(v)
                if v["code"] not in ("", "what"):
                    self.assertTrue(w["reason"] and w["fix"], w)
                self.assertNotIn("None", w["reason"] + w["fix"])


VERDICTS = [
    {"ok": True, "code": ""}, {"ok": False, "code": "orphan"}, {"ok": False, "code": "held", "task": "Research <x>"},
    {"ok": False, "code": "is_po", "project": "Opten"}, {"ok": False, "code": "draft"},
    {"ok": False, "code": "agents", "n": 2}, {"ok": False, "code": "unsupported", "agent": "gemini"},
    {"ok": False, "code": "not_installed", "agent": "codex"}, {"ok": False, "code": "other_project", "project": "Cars"},
    {"ok": False, "code": "no_conversation"}, {"ok": False, "code": "no_cwd", "cwd": "D:\\gone"},
    {"ok": False, "code": "no_cwd", "cwd": ""}, {"ok": False, "code": "has_po", "project": "Motors", "po": "Motors PO"},
    {"ok": False, "code": "live"}, {"ok": True, "code": "unseen", "confirm": "closed"}, {"ok": True, "code": "recent", "confirm": "closed", "min": 1},
    {"ok": True, "code": "recent", "confirm": "closed", "min": 12}, {"ok": False, "code": "what"},
]


class TheHub(Hub):
    def setUp(self):
        super().setUp()
        self.rows = []
        p = mock.patch.object(dashboard, "load_sessions", lambda *a, **k: self.rows)
        p.start()
        self.addCleanup(p.stop)

    def get(self, url, origin=""):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = url, "GET", "HTTP/1.1"
        h.requestline = f"GET {url} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}"}
        if origin:
            h.headers["Origin"] = origin
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h.do_GET()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")

    def candidates(self, **q):
        from urllib.parse import urlencode
        status, out = self.get("/api/projects/po-candidates?" + urlencode(q))
        self.assertEqual(status, 200, out)
        return out

    def row(self, sid, cwd, **over):
        return {"sessionId": sid, "cwd": str(cwd), "agent": "claude", "label": sid, "updatedAt": time.time() - 3600, **over}

    def task(self, title="Research", cwd=None, sid="sid-task-1"):
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"] = str(cwd or self.work), "solo"
        part = chatroom.agent_participants(full)[0]
        part["sessionId"], part["cwd"] = sid, str(cwd or self.work)
        chatroom.update_room(full)
        return rid

    def task_row(self, rid, cwd, **over):
        return {"sessionId": rid, "roomId": rid, "cwd": str(cwd), "label": "task", "updatedAt": time.time() - 60,
                "members": [{"agent": "claude"}], "hasConversation": True, **over}


class WhatTheHubReadsNow(unittest.TestCase):
    """_session_live_now: by the session's id, never by the folder a request names."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def rollout(self, cwd):
        f = self.dir / "rollout-x-sid-c.jsonl"
        f.write_text(json.dumps({"type": "session_meta", "payload": {"id": "sid-c", "cwd": cwd}}) + "\n", encoding="utf-8")
        return f

    def read(self, sid, files=(), records=(), transcript=None, pids=()):
        ag = SimpleNamespace(rollouts_for_session=lambda s: list(files) if s == sid else [])
        with mock.patch.object(dashboard.agents, "get_agent", lambda k: ag if k == "codex" else None), \
                mock.patch.object(dashboard, "_read_agent_session_files", lambda: list(records)), \
                mock.patch.object(dashboard, "_read_session_files", lambda: [{"sessionId": s} for s in pids]), \
                mock.patch.object(dashboard, "find_transcript", lambda s: transcript if s == sid else None):
            now = dashboard._session_live_now(sid)
        if transcript is not None:
            dashboard._CWD_CACHE.pop(str(transcript), None)
        return now

    def codex(self, files, records=()):
        return self.read("sid-c", files, records)

    def transcript(self, cwd):
        t = self.dir / "sid-a.jsonl"
        t.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n", encoding="utf-8")
        return t

    def test_a_codex_session_is_never_seen_closed(self):
        now = self.codex([self.rollout("C:\\work\\engine")])
        self.assertEqual((now["agent"], now["live"], now["seen"], now["cwd"]),
                         ("codex", False, False, "C:\\work\\engine"))
        self.assertGreater(now["written"], 0)

    def test_a_codex_launched_by_the_hub_in_its_folder_is_live(self):
        now = self.codex([self.rollout("C:\\work\\engine")], [{"agent": "codex", "cwd": "c:\\work\\engine\\"}])
        self.assertTrue(now["live"])

    def test_a_failed_read_is_unseen(self):
        def boom(k):
            raise RuntimeError("no codex")
        t = self.transcript("C:\\work\\engine")
        with mock.patch.object(dashboard.agents, "get_agent", boom), \
                mock.patch.object(dashboard, "_read_session_files", lambda: []), \
                mock.patch.object(dashboard, "find_transcript", lambda s: t):
            self.assertEqual(dashboard._session_live_now("sid-a")["seen"], False)
        dashboard._CWD_CACHE.pop(str(t), None)
        with mock.patch.object(dashboard, "_read_session_files", side_effect=OSError("denied")):
            self.assertEqual(dashboard._session_live_now("sid-a")["seen"], False)

    def test_a_claude_session_by_its_pid_file_and_transcript(self):
        now = self.read("sid-a", transcript=self.transcript("C:\\work\\engine"), pids=["sid-a"])
        self.assertEqual((now["agent"], now["live"], now["seen"], now["cwd"]), ("claude", True, True, "C:\\work\\engine"))
        now = self.read("sid-a", transcript=self.transcript("C:\\work\\engine"))
        self.assertEqual((now["agent"], now["live"], now["seen"]), ("claude", False, True))

    def test_no_transcript_or_two_is_no_agent(self):
        now = self.read("sid-a")
        self.assertEqual((now["agent"], now["seen"], now["cwd"]), ("", False, ""))
        now = self.read("sid-a", pids=["sid-a"])
        self.assertEqual((now["agent"], now["live"]), ("", True), "open, even with no transcript yet")
        f = self.dir / "rollout-x-sid-a.jsonl"
        f.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "C:\\w"}}) + "\n", encoding="utf-8")
        now = self.read("sid-a", files=[f], transcript=self.transcript("C:\\w"))
        self.assertEqual((now["agent"], now["seen"]), ("", False), "an id in both agents' transcripts")


class Candidates(TheHub):
    def test_for_a_new_project_its_folder_comes_first(self):
        inside = self.work / "docs"
        inside.mkdir()
        (self.work / ".git").mkdir()
        self.rows = [self.row("far", self.base, updatedAt=time.time() - 60),
                     self.row("in", inside, updatedAt=time.time() - 50),
                     self.row("here-old", self.work, updatedAt=time.time() - 9000),
                     self.row("here-new", self.work, updatedAt=time.time() - 100),
                     self.row("open", self.work, isLive=True)]
        out = self.candidates(path=str(self.work), kind="code", name="Engine")
        self.assertEqual([c["sessionId"] for c in out["candidates"]], ["here-new", "open", "here-old", "in", "far"])
        self.assertEqual([c["relation"] for c in out["candidates"]], ["same", "same", "same", "inside", "elsewhere"])
        self.assertEqual(out["candidates"][1]["verdict"]["code"], "live", "shown, with why")
        f = out["folder"]
        self.assertEqual((f["exists"], f["isGit"], f["project"], f["relative"]), (True, True, None, False))
        self.assertIsNone(out["project"])
        self.assertEqual(out["installed"], ["claude", "codex"])
        self.assertEqual(dashboard.load_projects(), [], "reads only")

    def test_what_the_folder_is(self):
        self.assertTrue(self.candidates(path="relative\\dir", kind="code")["folder"]["relative"])
        gone = self.candidates(path=str(self.base / "new"), kind="code")["folder"]
        self.assertEqual((gone["exists"], gone["isFile"]), (False, False))
        (self.base / "a.txt").write_text("x")
        self.assertTrue(self.candidates(path=str(self.base / "a.txt"), kind="code")["folder"]["isFile"])
        ok, proj, _ = dashboard.register_project(str(self.work), "Engine")
        self.assertEqual(self.candidates(path=str(self.work), kind="code")["folder"]["project"],
                         {"id": proj["id"], "name": "Engine"})
        docs = self.candidates(kind="documents", name="Motors")["folder"]
        self.assertEqual(docs["folder"], str(self.root / "Motors"))
        self.assertEqual(self.candidates(kind="documents", name="a/b")["folder"]["folder"], "")
        self.assertFalse((self.root / "Motors").exists(), "nothing is made")

    def test_for_an_existing_project(self):
        ok, proj, _ = dashboard.register_project(str(self.work), "Engine")
        ok, other, _ = dashboard.register_project(str(self.base / "cars"), "Cars")
        own = self.task("Own")
        dashboard.assign_session_project(own, proj["id"])
        theirs_here = self.task("Theirs here")
        dashboard.assign_session_project(theirs_here, other["id"])
        theirs_away = self.task("Theirs away", cwd=self.base / "cars")
        dashboard.assign_session_project(theirs_away, other["id"])
        self.rows = [self.task_row(own, self.work), self.task_row(theirs_here, self.work),
                     self.task_row(theirs_away, self.base / "cars"), self.row("past", self.base)]
        out = self.candidates(project=proj["id"])
        by = {c["roomId"] or c["sessionId"]: c for c in out["candidates"]}
        self.assertNotIn(theirs_away, by, "another project's task elsewhere is simply its task")
        self.assertEqual(by[own]["verdict"], {"ok": True, "code": ""})
        self.assertTrue(by[own]["inProject"])
        self.assertEqual(by[theirs_here]["verdict"]["code"], "other_project")
        self.assertEqual(by["past"]["verdict"]["code"], "")
        self.assertEqual((out["project"]["id"], out["project"]["po"]), (proj["id"], ""))
        self.assertIsNone(out["folder"])
        # With a PO, nothing may take it.
        dashboard.set_project_po(proj["id"], own)
        out = self.candidates(project=proj["id"])
        codes = {c["roomId"] or c["sessionId"]: c["verdict"]["code"] for c in out["candidates"]}
        self.assertEqual((codes["past"], codes[theirs_here]), ("has_po", "other_project"))

    def test_a_project_that_is_gone_and_a_page_on_another_site(self):
        status, out = self.get("/api/projects/po-candidates?project=nope")
        self.assertEqual((status, out["error"]), (404, "no_such_project"))
        status, out = self.get("/api/projects/po-candidates?path=C:%5Cx", origin="https://elsewhere.example")
        self.assertEqual((status, out["error"]), (403, "cross_origin"))
        status, _ = self.get("/api/projects/po-candidates?path=C:%5Cx", origin=f"http://127.0.0.1:{PORT}")
        self.assertEqual(status, 200)


class Requests(TheHub):
    def test_open_in_a_terminal_is_refused(self):
        self.live["sid-past-1"] = {"live": True, "written": time.time()}
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 409, out)
        self.assertIn("open in a terminal right now", out["message"])
        self.nothing_left()

    def test_written_to_recently_it_waits_for_the_person(self):
        self.live["sid-past-1"] = {"written": time.time() - 180}
        body = {**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)}
        status, out = self.call(body)
        self.assertEqual(status, 409, out)
        self.assertIn("written to 3 minutes ago", out["message"])
        self.assertIn("confirm it is closed", out["message"])
        self.nothing_left()
        status, out = self.call({**body, "confirmClosed": True})
        self.assertEqual(status, 200, out)

    def test_a_folder_the_transcript_does_not_name_is_refused(self):
        other = self.base / "other"
        other.mkdir()
        self.live["sid-past-1"] = {"cwd": str(other)}
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work),
                                 "confirmClosed": True})
        self.assertEqual(status, 409, out)
        self.assertIn(f"started in {other}", out["message"])
        self.nothing_left()
        self.live["sid-past-1"] = {"cwd": str(self.work)}
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)

    def test_the_agent_the_page_names_must_be_the_transcripts(self):
        # A live Codex posted as Claude, and a live Claude posted as Codex.
        for real, said in (("codex", "claude"), ("claude", "codex")):
            with self.subTest(real=real):
                self.live["sid-past-1"] = {"agent": real, "live": True, "seen": real == "claude"}
                status, out = self.call({**self.session(agent=said), "name": "Engine", "kind": "code",
                                         "path": str(self.work), "confirmClosed": True})
                self.assertEqual(status, 409, out)
                self.assertIn("open in a terminal right now", out["message"])
                self.live["sid-past-1"] = {"agent": real, "seen": real == "claude"}
                status, out = self.call({**self.session(agent=said), "name": "Engine", "kind": "code",
                                         "path": str(self.work), "confirmClosed": True})
                self.assertEqual(status, 409, out)
                self.assertIn(f"That is a {real.capitalize()} conversation", out["message"])
                self.nothing_left()

    def test_a_session_with_no_transcript_is_refused(self):
        for gone in ({"agent": ""}, {"cwd": ""}, {"agent": "", "seen": False}):
            with self.subTest(gone=gone):
                self.live["sid-past-1"] = gone
                status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work),
                                         "confirmClosed": True})
                self.assertEqual(status, 409, out)
                self.assertIn("cannot find that conversation's transcript", out["message"])
                self.nothing_left()

    def test_a_codex_the_hub_cannot_see_waits_for_the_person(self):
        self.live["sid-past-1"] = {"agent": "codex", "seen": False, "written": time.time() - 86400}
        body = {**self.session(agent="codex"), "name": "Engine", "kind": "code", "path": str(self.work)}
        status, out = self.call(body)
        self.assertEqual(status, 409, out)
        self.assertIn("cannot see whether it is still open", out["message"])
        self.nothing_left()
        status, out = self.call({**body, "confirmClosed": True})
        self.assertEqual(status, 200, out)

    def test_the_code_folder_is_left_as_it_was(self):
        (self.work / "main.py").write_text("print(1)\n")
        (self.work / ".git").mkdir()
        before = sorted(p.relative_to(self.work) for p in self.work.rglob("*"))
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 200, out)
        self.assertEqual(sorted(p.relative_to(self.work) for p in self.work.rglob("*")), before)
        self.assertEqual((self.work / "main.py").read_text(), "print(1)\n")

    def test_a_fresh_po_for_a_new_project(self):
        code = self.base / "code" / "engine"
        code.mkdir(parents=True)
        (code / "main.py").write_text("x")
        status, out = self.call({"fresh": "codex", "name": "Engine", "kind": "code", "path": str(code)})
        self.assertEqual(status, 200, out)
        proj, rid = out["project"], out["room"]["id"]
        self.assertEqual(proj["poRoomId"], rid)
        room = chatroom.get_room(rid, public=False)
        part = chatroom.agent_participants(room)[0]
        self.assertEqual((room["title"], part["agent"], room["projectId"]), ("Engine PO", "codex", proj["id"]))
        self.assertFalse(part.get("sessionId"), "a new conversation, not a resumed one")
        self.assertEqual(os.path.normcase(part["cwd"]), os.path.normcase(dashboard.po_work_folder(proj)))
        self.assertIsNone(room.get("no"))
        self.assertFalse(dashboard.find_project(proj["id"]).get("nextTaskNo"), "no task number is taken")
        self.assertEqual(self.started, [rid])
        text = dashboard._RESUMES[rid].queue[0]["text"]
        self.assertIn("started fresh", text)
        self.assertEqual(sorted(os.listdir(code)), ["main.py"], "the code folder is left as it was")

    def test_a_fresh_po_for_a_project_that_has_none(self):
        ok, proj, _ = dashboard.register_project("Motors")
        first = self.task("A task")
        dashboard.assign_session_project(first, proj["id"])
        dashboard.assign_task_number(first, proj["id"])
        status, out = self.call({"fresh": "claude", "projectId": proj["id"]})
        self.assertEqual(status, 200, out)
        self.assertEqual(dashboard.find_project(proj["id"])["nextTaskNo"], 2, "the counter is left alone")
        status, out = self.call({"fresh": "claude", "projectId": proj["id"]})
        self.assertEqual(status, 409, out)
        self.assertIn("already has a PO", out["message"])

    def test_a_fresh_po_that_does_not_start_leaves_nothing(self):
        self.start_error = dashboard.StartRoomError("codex could not be started")
        code = self.base / "code" / "engine"
        status, out = self.call({"fresh": "codex", "name": "Engine", "kind": "code", "path": str(code)})
        self.assertEqual(status, 400, out)
        self.assertIn("could not be started", out["message"])
        self.nothing_left()
        self.assertFalse(code.exists(), "the folder it made is gone again")

    def test_a_fresh_po_of_an_agent_that_is_not_there(self):
        status, out = self.call({"fresh": "gemini", "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 400, out)
        self.assertIn("only a Claude or a Codex conversation", out["message"])
        with mock.patch.object(dashboard, "_agent_installed", lambda k: k == "claude"):
            status, out = self.call({"fresh": "codex", "name": "Engine", "kind": "code", "path": str(self.work)})
        self.assertEqual(status, 400, out)
        self.assertIn("Codex is not installed", out["message"])
        self.nothing_left()

    def test_a_page_on_another_site_cannot_start_one(self):
        status, out = self.call({"fresh": "claude", "name": "Engine", "kind": "code", "path": str(self.work)},
                                origin="https://elsewhere.example")
        self.assertEqual((status, out["error"]), (403, "cross_origin"))
        self.nothing_left()

    def test_the_sessions_list_carries_the_verdict(self):
        # On every row, with its words: the action menu takes a row without it as not known.
        self.assertIn('r["makePo"] = make_po_answer(make_po_verdict(po_facts.of_row(r), None, now))',
                      Path(dashboard.__file__).read_text(encoding="utf-8"))

    def test_the_room_carries_it_for_the_pop_out(self):
        rid = self.task()
        status, out = self.get(f"/api/room?id={rid}")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["makePo"], {"ok": True, "code": "", "reason": "", "fix": ""})
        dashboard.assign_session_project(rid, dashboard.register_project("Motors")[1]["id"])
        status, out = self.get(f"/api/room?id={rid}")
        self.assertEqual((out["makePo"]["ok"], out["makePo"]["code"]), (False, "other_project"))
        self.assertIn("Move it out of that project first", out["makePo"]["fix"])


class ThePhoneRules(unittest.TestCase):
    def test_a_hidden_choice_stays_hidden_on_a_phone(self):
        # "It is closed in its terminal" is hidden unless the verdict asks for
        # it: a phone rule that shows every choice must not show that one.
        import re
        rules = re.findall(r"^\s*(dialog#setup-modal \.kind-opt[^{,]*)\{[^}]*display:\s*flex", INDEX, re.M)
        self.assertTrue(rules)
        for sel in rules:
            self.assertIn(":not([hidden])", sel)


class ACodeFolderInTheProjectsFolder(TheHub):
    """A code folder that is in the projects root with files in it is used in
    place: nothing is written into it, the project's own files go beside it."""

    def setUp(self):
        super().setUp()
        self.code = self.root / "engine"
        (self.code / "src").mkdir(parents=True)
        (self.code / "src" / "main.py").write_text("print(1)\n")
        (self.code / "project.json").write_text('{"name": "the code folder\'s own"}')
        self.before = self.listing()

    def listing(self):
        return sorted((str(p.relative_to(self.code)), p.read_bytes() if p.is_file() else b"")
                      for p in self.code.rglob("*"))

    def kept_apart(self, pid):
        # Found again from the projects root alone, as on a restored machine,
        # straight after it was registered: its home is made then, not later.
        with mock.patch.object(dashboard, "PROJECTS_FILE", self.base / "nothing.json"):
            again = [p for p in dashboard.load_projects() if p["id"] == pid]
        self.assertEqual(os.path.normcase(again[0]["path"]), os.path.normcase(str(self.code)))
        proj = dashboard.find_project(pid)
        home = dashboard.project_home(proj)
        self.assertEqual(os.path.normcase(proj["path"]), os.path.normcase(str(self.code)))
        self.assertNotEqual(os.path.normcase(home), os.path.normcase(str(self.code)))
        self.assertEqual(json.loads((Path(home) / "project.json").read_text(encoding="utf-8"))["id"], pid)
        self.assertEqual(Path(dashboard._task_dir_for(proj, "A task")).parent, Path(home), "tasks go in its home")
        self.assertEqual(dashboard.set_project_kind(pid, "documents"), (False, "code_kept_apart"))
        self.assertEqual(self.listing(), self.before, "the code folder is as it was")
        return proj

    def test_made_the_po_of_a_new_project(self):
        status, out = self.call({**self.session(), "name": "Engine", "kind": "code", "path": str(self.code)})
        self.assertEqual(status, 200, out)
        self.kept_apart(out["project"]["id"])

    def test_a_fresh_po(self):
        status, out = self.call({"fresh": "claude", "name": "Engine", "kind": "code", "path": str(self.code)})
        self.assertEqual(status, 200, out)
        self.kept_apart(out["project"]["id"])

    def test_registered_with_no_po(self):
        status, out = self.call_url("/api/projects/new", {"path": str(self.code), "name": "Engine", "kind": "code"})
        self.assertEqual(status, 200, out)
        self.kept_apart(out["project"]["id"])

    def test_a_failed_start_leaves_the_folder_and_no_home(self):
        self.start_error = dashboard.StartRoomError("claude could not be started")
        status, out = self.call({"fresh": "claude", "name": "Engine", "kind": "code", "path": str(self.code)})
        self.assertEqual(status, 400, out)
        self.assertEqual(self.listing(), self.before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["engine"], "the home it would have had is gone")

    def test_an_empty_code_folder_is_kept_apart_too(self):
        for p in sorted(self.code.rglob("*"), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        self.before = self.listing()
        self.assertEqual(self.before, [])
        status, out = self.call_url("/api/projects/new", {"path": str(self.code), "name": "Engine", "kind": "code"})
        self.assertEqual(status, 200, out)
        self.kept_apart(out["project"]["id"])
        self.assertEqual(os.listdir(self.code), [])

    def test_a_new_folder_is_its_own_home_as_before(self):
        ok, proj, _ = dashboard.register_project("Motors")
        self.assertTrue((self.root / "Motors" / "project.json").is_file())
        self.assertFalse(dashboard._home_apart(dashboard.find_project(proj["id"])))

    def test_a_documents_folder_with_files_is_its_own_home(self):
        docs = self.root / "Letters"
        docs.mkdir()
        (docs / "a.txt").write_text("x")
        ok, proj, _ = dashboard.register_project(str(docs), "Letters", "documents")
        self.assertTrue((docs / "project.json").is_file())
        self.assertEqual(dashboard.set_project_kind(proj["id"], "documents"), (True, "ok"))


@unittest.skipUnless(NODE, "node is not installed")
class ThePage(unittest.TestCase):
    JS = r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtAgo = s => Math.round(s / 60) + ' min';
%s
const V = %s;
const out = { words: V.map(makePoWords) };
const now = 1_000_000;
const c = (sid, rel, over) => ({ sessionId: sid, roomId: '', label: sid, first: '', agent: 'claude', cwd: 'C:\\w\\' + sid,
                                 relation: rel, updatedAt: now - 600, verdict: { ok: true, code: '' }, ...over });
const cands = [c('here', 'same'), c('open', 'same', { verdict: { ok: false, code: 'live' } }),
               c('recent', 'inside', { verdict: { ok: true, code: 'recent', confirm: 'closed', min: 3 } }),
               ...[1, 2, 3, 4, 5, 6, 7].map(i => c('far' + i, 'elsewhere')),
               c('task', 'elsewhere', { sessionId: 'room-9', roomId: 'room-9', no: 9, agent: 'claude,codex', verdict: { ok: false, code: 'agents', n: 2 } })];
const base = { mode: 'new', inline: false, now, project: null, kind: 'code', path: 'C:\\w', name: 'Engine',
               folder: { kind: 'code', folder: 'C:\\w', exists: true, isFile: false, isGit: true, project: null, relative: false },
               cands, candsErr: '', installed: ['claude', 'codex'], choice: '', sel: '', fresh: 'claude', filter: '', more: false,
               closed: false, bring: null, bringOn: true, err: '' };
const S = o => ({ ...base, ...o });
const g = st => setupGroups(st).groups.map(x => [x.title, x.items.map(i => i.sessionId)]);
out.groups = g(base);
out.hidden = setupGroups(base).hidden;
out.more = g(S({ more: true }));
out.keepChosen = g(S({ choice: 'conv', sel: 'far7' }));
out.filtered = g(S({ filter: 'FAR3' }));
out.noFolder = g(S({ folder: { ...base.folder, folder: '' } })).at(-1)[0];
out.list = setupListHtml(base);
out.listNone = setupListHtml(S({ filter: 'zzz <x>' }));
out.listEmpty = setupListHtml(S({ cands: [] }));
out.listLoading = setupListHtml(S({ cands: null }));
out.notes = [
  setupFolderNote(base),
  setupFolderNote(S({ folder: { ...base.folder, isGit: false } })),
  setupFolderNote(S({ folder: { ...base.folder, exists: false, isGit: false } })),
  setupFolderNote(S({ folder: { ...base.folder, relative: true, folder: '' } })),
  setupFolderNote(S({ folder: { ...base.folder, isFile: true, exists: false } })),
  setupFolderNote(S({ folder: { ...base.folder, project: { id: 'p1', name: 'Engine' } } })),
  setupFolderNote(S({ kind: 'documents', name: 'a/b', folder: { kind: 'documents', folder: '' } })),
  setupFolderNote(S({ kind: 'documents', name: 'Motors', folder: { kind: 'documents', folder: 'C:\\P\\Motors', exists: false } })),
  setupFolderNote(S({ mode: 'existing', project: { id: 'p1', name: 'Engine', path: 'C:\\w' } })),
];
out.dialog = setupDialogHtml(base);
out.dialogPre = setupDialogHtml(S({ choice: 'conv', sel: 'here' }));
out.dialogRecent = setupDialogHtml(S({ choice: 'conv', sel: 'recent' }));
out.dialogInline = setupDialogHtml(S({ inline: true }));
out.dialogExisting = setupDialogHtml(S({ mode: 'existing', project: { id: 'p1', name: 'Motors <x>', kind: 'documents', path: 'C:\\P\\Motors' } }));
out.dialogNoAgents = setupDialogHtml(S({ installed: [] }));
const docsInfo = { documents: true, offer: true, folder: 'C:\\w\\here', files: 2, bytes: 10 };
out.submit = {
  noName: setupSubmit(S({ name: ' ' })),
  noPath: setupSubmit(S({ path: '' })),
  isProject: setupSubmit(S({ folder: { ...base.folder, project: { id: 'p1', name: 'Engine' } } })),
  noChoice: setupSubmit(base),
  noConv: setupSubmit(S({ choice: 'conv' })),
  conv: setupSubmit(S({ choice: 'conv', sel: 'here' })),
  refused: setupSubmit(S({ choice: 'conv', sel: 'open' })),
  recent: setupSubmit(S({ choice: 'conv', sel: 'recent' })),
  recentClosed: setupSubmit(S({ choice: 'conv', sel: 'recent', closed: true })),
  fresh: setupSubmit(S({ choice: 'fresh', fresh: 'codex' })),
  none: setupSubmit(S({ choice: 'none' })),
  inline: setupSubmit(S({ inline: true })),
  docsNone: setupSubmit(S({ choice: 'none', kind: 'documents', name: 'Motors', folder: { kind: 'documents', folder: 'C:\\P\\Motors', exists: false } })),
  docsConv: setupSubmit(S({ choice: 'conv', sel: 'here', kind: 'documents', name: 'Motors', bring: docsInfo, bringOn: false,
                            folder: { kind: 'documents', folder: 'C:\\P\\Motors', exists: false } })),
  existing: setupSubmit(S({ mode: 'existing', project: { id: 'p1', name: 'Motors', kind: 'code', path: 'C:\\w' }, choice: 'conv', sel: 'here' })),
  existingNone: setupSubmit(S({ mode: 'existing', project: { id: 'p1', name: 'Motors', kind: 'code', path: 'C:\\w' }, choice: 'none' })),
  existingFresh: setupSubmit(S({ mode: 'existing', project: { id: 'p1', name: 'Motors', kind: 'code', path: 'C:\\w' }, choice: 'fresh' })),
};
out.submit.conv.cand = out.submit.conv.cand && out.submit.conv.cand.sessionId;
out.submit.existing.cand = out.submit.existing.cand && out.submit.existing.cand.sessionId;
if (out.submit.recentClosed.cand) out.submit.recentClosed.cand = out.submit.recentClosed.cand.sessionId;
if (out.submit.docsConv.cand) out.submit.docsConv.cand = out.submit.docsConv.cand.sessionId;
console.log(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        def block(name):
            i = INDEX.index(f"// ---- {name}: begin")
            return INDEX[i:INDEX.index(f"// ---- {name}: end", i)]
        src = cls.JS % (block("A past session made a PO") + block("Set up a project"), json.dumps(VERDICTS))
        r = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_page_words_a_verdict_as_the_hub_does(self):
        for v, words in zip(VERDICTS, self.out["words"]):
            with self.subTest(code=v["code"], v=v):
                self.assertEqual(words, dashboard.make_po_words(v))

    def test_the_menu_item_says_why_it_is_off(self):
        # The shared action menu (static/actions.js) on what the hub puts on a
        # row: every row carries its answer, with its words.
        answers = [dashboard.make_po_answer(v) for v in (
            {"ok": True, "code": ""}, {"ok": False, "code": "live"},
            {"ok": True, "code": "recent", "confirm": "closed", "min": 2})]
        js = (f"const A = require({json.dumps(str(Path(dashboard.__file__).parent / 'static' / 'actions.js'))});"
              f"console.log(JSON.stringify({json.dumps(answers)}.map(m => A.makePoItem({{kind: 'raw', sessionId: 's', makePo: m}}))))")
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        plain, live, recent = json.loads(r.stdout)
        self.assertFalse(plain.get("disabled"))
        self.assertTrue(live["disabled"])
        self.assertIn("open in a terminal right now", live["reason"])
        self.assertIn("Close it in that terminal first", live["reason"])
        self.assertFalse(recent.get("disabled"), "offered: the dialog asks about the terminal")

    def test_the_groups(self):
        o = self.out
        self.assertEqual(o["groups"], [["Started in this folder", ["here", "open"]],
                                       ["Started in a folder inside it", ["recent"]],
                                       ["Started elsewhere", ["far1", "far2", "far3", "far4", "far5"]]])
        self.assertEqual(o["hidden"], 3)
        self.assertEqual(len(o["more"][2][1]), 8)
        self.assertEqual(o["keepChosen"][2][1][-1], "far7", "the chosen one stays in view")
        self.assertEqual(o["filtered"], [["Started elsewhere", ["far3"]]])
        self.assertEqual(o["noFolder"], "Your conversations")

    def test_the_list(self):
        l = self.out["list"]
        self.assertIn(">Show 3 more</button>", l)
        self.assertIn('value="open" disabled>', l)
        self.assertIn("It is open in a terminal right now.", l, "an unfit one says why, and what to do")
        self.assertIn("It was written to 3 minutes ago", l)
        self.assertNotIn(" checked", l, "nothing is chosen for the person")
        self.assertIn("None matches “zzz &lt;x&gt;”.", self.out["listNone"])
        self.assertIn("start a fresh PO", self.out["listEmpty"])
        self.assertIn('role="status"', self.out["listLoading"])

    def test_the_line_under_the_folder(self):
        git, folder, missing, relative, file, project, badName, docs, existing = self.out["notes"]
        self.assertTrue(git["text"].startswith("A git repository. Registering it changes nothing in it."))
        self.assertIn("show under the project, because they work there", git["text"])
        self.assertFalse(git.get("alert"))
        self.assertTrue(folder["text"].startswith("A folder that is there already."))
        self.assertIn("made when you confirm", missing["text"])
        for n in (relative, file, project, badName):
            self.assertTrue(n.get("alert"), n)
        self.assertEqual(project["project"], {"id": "p1", "name": "Engine"})
        self.assertIn("C:\\P\\Motors", docs["text"])
        self.assertEqual(existing, {"text": ""})

    def test_the_dialog(self):
        d = self.out["dialog"]
        self.assertIn("<h3>New project</h3>", d)
        self.assertIn("Nothing is registered until you confirm.", d)
        self.assertNotRegex(d, r'name="su-po" value="\w+" checked', "no way to get a PO is chosen for the person")
        self.assertIn('id="su-conv-field" hidden', d)
        self.assertIn('value="none"', d)
        self.assertIn('<option value="codex">Codex</option>', d)
        self.assertIn('id="su-closed-field" hidden', d)
        self.assertIn('>Create project</button>', d)
        self.assertIn('name="su-po" value="conv" checked', self.out["dialogPre"])
        self.assertIn('value="here" checked', self.out["dialogPre"])
        self.assertIn('id="su-closed-field"><input type="checkbox" id="su-closed">', self.out["dialogRecent"], "a recent one asks about its terminal")
        self.assertNotIn('name="su-po"', self.out["dialogInline"], "from another dialog it registers only")
        e = self.out["dialogExisting"]
        self.assertIn("Set up the PO of “Motors &lt;x&gt;”", e)
        self.assertNotIn('value="none"', e)
        self.assertNotIn('id="su-path"', e)
        self.assertIn(">Set up the PO</button>", e)
        self.assertIn("Neither Claude nor Codex is installed", self.out["dialogNoAgents"])

    def test_what_confirming_asks(self):
        s = self.out["submit"]
        self.assertEqual(s["noName"], {"error": "Give the project a name."})
        self.assertEqual(s["noPath"], {"error": "Give the code folder."})
        self.assertIn("already the project “Engine”", s["isProject"]["error"])
        self.assertEqual(s["noChoice"], {"error": "Choose how the project gets its PO."})
        self.assertEqual(s["noConv"], {"error": "Choose a conversation."})
        self.assertEqual(s["conv"], {"how": "conv", "cand": "here", "target": {"name": "Engine", "kind": "code", "path": "C:\\w"}})
        self.assertIn("open in a terminal", s["refused"]["error"])
        self.assertIn("It is closed in its terminal", s["recent"]["error"])
        self.assertTrue(s["recentClosed"]["target"]["confirmClosed"])
        self.assertEqual(s["fresh"], {"how": "fresh", "body": {"fresh": "codex", "name": "Engine", "kind": "code", "path": "C:\\w"}})
        self.assertEqual(s["none"], {"how": "none", "body": {"name": "Engine", "kind": "code", "path": "C:\\w"}})
        self.assertEqual(s["inline"], s["none"])
        self.assertEqual(s["docsNone"]["body"], {"name": "Motors", "kind": "documents", "path": "C:\\P\\Motors"})
        self.assertEqual(s["docsConv"]["target"], {"name": "Motors", "kind": "documents", "path": "", "bringFiles": False})
        self.assertEqual(s["existing"], {"how": "conv", "cand": "here", "target": {"projectId": "p1"}})
        self.assertEqual(s["existingNone"], {"error": "Choose how the project gets its PO."})
        self.assertEqual(s["existingFresh"], {"how": "fresh", "body": {"fresh": "claude", "projectId": "p1"}})

    def test_where_it_opens(self):
        self.assertIn("if (np) np.onclick = () => projectSetupFlow();", INDEX)
        self.assertIn("if (choose) { projectSetupFlow({ projectId: choose.dataset.proj }); return; }", INDEX)
        self.assertIn("return projectSetupFlow({ path: prefillPath || '', inline: true });", INDEX)
        self.assertNotIn("prompt('Project folder", INDEX, "no browser prompts")
        self.assertNotIn("function poChoose(", INDEX)


if __name__ == "__main__":
    unittest.main()
