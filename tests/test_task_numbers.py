"""Every task has a number: #18 in its project, ED-18 anywhere.

* task_numbers: a project's key from its name, unique and deterministic; a
  number a person put in a title; the plan for tasks without a number; what a
  task address says; the tasks a chat text names;
* the hub: a new task and a task moved in get the project's next number (the
  old one kept), a copy of a room read before its number never loses it, the
  backfill on start numbers old tasks (titles like the trading project's) and
  a second start changes nothing, the PO gets no number;
* every place a task is addressed: the ensemble_* tools, /api/room,
  /api/room/workflow, /api/room/resume, /session, /api/task/ref, and the
  project key endpoint;
* what the hub writes: the [ref #18] line under a message, the report's wake
  line, the review brief and the digest.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import digest  # noqa: E402
import ensemble_tools  # noqa: E402
import message_refs as mr  # noqa: E402
import task_numbers as tn  # noqa: E402

PORT = 8798


class Keys(unittest.TestCase):
    def test_initials_of_up_to_three_words(self):
        for name, key in (("Ensemble Dashboard", "ED"), ("OPtionTradingENgine", "O"), ("Motors", "M"),
                          ("Inheritance", "I"), ("a b c d", "ABC"), ("my-new_project 2", "MNP"), ("x 2 y", "X2Y"),
                          ("2026 plans", "P"), ("", "P"), ("  ", "P"), ("Été à Paris", "TP")):
            self.assertEqual(tn.derive_key(name), key, name)

    def test_unique_and_deterministic(self):
        projects = [
            {"id": "p3", "name": "Motors", "createdAt": 30},
            {"id": "p1", "name": "Ensemble Dashboard", "createdAt": 10},
            {"id": "p2", "name": "Money", "createdAt": 20},
            {"id": "p4", "name": "Mansions", "createdAt": 40},
            {"id": "p5", "name": "Every Day", "createdAt": 50},
        ]
        keys = tn.project_keys(projects)
        self.assertEqual(keys, {"p1": "ED", "p2": "M", "p3": "M2", "p4": "M3", "p5": "ED2"})
        self.assertEqual(tn.project_keys(list(reversed(projects))), keys, "the order given does not matter")

    def test_a_stored_key_is_kept_and_a_bad_one_is_not(self):
        projects = [
            {"id": "p1", "name": "Motors", "createdAt": 10},
            {"id": "p2", "name": "Mansions", "key": "m", "createdAt": 20},
            {"id": "p3", "name": "Other", "key": "not a key", "createdAt": 30},
            {"id": "p4", "name": "Other two", "key": "M", "createdAt": 40},
        ]
        self.assertEqual(tn.project_keys(projects), {"p1": "M2", "p2": "M", "p3": "O", "p4": "OT"})
        for bad in ("", "1A", "A-B", "TOOLONG", None, 12):
            self.assertEqual(tn.normalize_key(bad), "", bad)
        self.assertEqual(tn.normalize_key(" ot2 "), "OT2")


class Titles(unittest.TestCase):
    def test_a_number_a_person_gave(self):
        for title, n in (("18. 0DTE management", 18), ("20 One New York day", 20), ("#7 fix", 7), ("#7", 7),
                         ("3R. CP0 shared rules", None), ("3R2. finish", None), ("TWS NonBlocking API", None),
                         ("18.5 release", 18), ("2026-09 plan", None), ("0. zero", None), ("12345 big", None),
                         ("", None), ("  5. spaced", 5), ("#12a", None)):
            self.assertEqual(tn.title_no(title), n, title)

    def test_the_trading_projects_titles_mixed_with_unnumbered_ones(self):
        tasks = [
            {"id": "tws", "title": "TWS NonBlocking API", "createdAt": 1},
            {"id": "t5", "title": "5. One broker connection per account", "createdAt": 2},
            {"id": "t6", "title": "6. Butterfly engine", "createdAt": 3},
            {"id": "t1", "title": "1. Show each option chain", "createdAt": 4},
            {"id": "t3", "title": "3. Agree the shared rules", "createdAt": 5},
            {"id": "t3r", "title": "3R. CP0 shared rules takeover", "createdAt": 6},
            {"id": "t18", "title": "18. 0DTE management", "createdAt": 7},
            {"id": "t20", "title": "20. One New York trading day", "createdAt": 8},
            {"id": "d1", "title": "9. Prove it on paper", "createdAt": 9},
            {"id": "d2", "title": "9. Prove it again", "createdAt": 10},
            {"id": "late", "title": "Plain late task", "createdAt": 11},
        ]
        plan, nxt = tn.plan_numbers(tasks, 0)
        self.assertEqual(plan, {"t5": 5, "t6": 6, "t1": 1, "t3": 3, "t18": 18, "t20": 20,
                                "tws": 21, "t3r": 22, "d1": 23, "d2": 24, "late": 25})
        self.assertEqual(nxt, 26)
        numbered = [dict(t, no=plan[t["id"]]) for t in tasks]
        self.assertEqual(tn.plan_numbers(numbered, nxt), ({}, 26), "a second run changes nothing")

    def test_a_number_is_never_reused(self):
        tasks = [{"id": "a", "title": "Old", "createdAt": 1, "no": 1},
                 {"id": "b", "title": "2. Once deleted", "createdAt": 2},
                 {"id": "c", "title": "9. Ahead", "createdAt": 3}]
        # Once a project has a counter, titles are not read: #2 may have been given before.
        self.assertEqual(tn.plan_numbers(tasks, 5), ({"b": 5, "c": 6}, 7))
        self.assertEqual(tn.plan_numbers([{"id": "x", "title": "1. taken", "createdAt": 1},
                                          {"id": "y", "title": "Other", "createdAt": 0, "no": 1}], 0),
                         ({"x": 2}, 3), "a title's number another task has is not taken")

    def test_a_year_in_a_title_is_not_its_number(self):
        tasks = [{"id": "a", "title": "Plain", "createdAt": 1},
                 {"id": "y", "title": "2026 roadmap", "createdAt": 2},
                 {"id": "b", "title": "53. Just within reach", "createdAt": 3},
                 {"id": "c", "title": "Later", "createdAt": 4}]
        self.assertEqual(tn.plan_numbers(tasks, 0), ({"b": 53, "a": 54, "y": 55, "c": 56}, 57))
        tasks[2]["title"] = "55. Too far"
        self.assertEqual(tn.plan_numbers(tasks, 0)[0], {"a": 1, "y": 2, "b": 3, "c": 4})


class Refs(unittest.TestCase):
    def test_what_an_address_says(self):
        self.assertEqual(tn.parse_ref("room-1a2b3c4d"), {"room": "room-1a2b3c4d"})
        self.assertEqual(tn.parse_ref("#18"), {"key": "", "no": 18})
        self.assertEqual(tn.parse_ref(" 18 "), {"key": "", "no": 18})
        self.assertEqual(tn.parse_ref(18), {"key": "", "no": 18})
        self.assertEqual(tn.parse_ref("ed-18"), {"key": "ED", "no": 18})
        self.assertEqual(tn.parse_ref("#OT2-7"), {"key": "OT2", "no": 7})
        for bad in ("", "#", "18a", "E D-1", "-18", None, True, "room"):
            self.assertIsNone(tn.parse_ref(bad), bad)

    def test_the_tasks_a_chat_text_names(self):
        text = ("See #18 and @codex@18, then @reviewer@ED-7 (and #ED-7 again); "
                "not `#19`, not page#20, not &#21; nor\n```\n#22\n```\nbut #23.")
        got = [(r["token"], r["who"], r["key"], r["no"]) for r in tn.find_text_refs(text)]
        self.assertEqual(got, [("#18", "", "", 18), ("@reviewer@ED-7", "reviewer", "ED", 7), ("#23", "", "", 23)])
        self.assertEqual(tn.find_text_refs("#18a #18-19 x#18 @claude@ no refs"), [])

    def test_someone_elses_numbers_are_not_tasks(self):
        text = ("PR #12, Issues #4, finding #2, step #5, commit #6, prefix#7; "
                "but please review #13, fix #14, close #15, a line ending in step\n#16, the task #18 and @codex@8 are.")
        got = [r["token"] for r in tn.find_text_refs(text)]
        self.assertEqual(got, ["#13", "#14", "#15", "#16", "#18", "@codex@8"])
        self.assertEqual([r["token"] for r in tn.find_text_refs("PR #ED-7")], ["#ED-7"], "a key makes it a task")
        self.assertEqual(tn.find_text_refs("[Image #2] [Image #3]\n\nsee image #4, Images #5"), [],
                         "an agent's image placeholder is not a task")
        self.assertEqual([r["token"] for r in tn.find_text_refs("Mapper #9 and suffix #10")], ["#9", "#10"],
                         "a word only ending in one of them does not count")

    def test_finding_a_task(self):
        rooms = [{"id": "room-a", "no": 18, "noProjectId": "p1"},
                 {"id": "room-b", "no": 18, "noProjectId": "p2", "previousNos": [{"projectId": "p1", "no": 3}]},
                 {"id": "room-c", "no": 5, "noProjectId": "p2"},
                 {"id": "room-po"}]
        keys, names = {"p1": "ED", "p2": "O"}, {"p1": "Ensemble Dashboard"}
        find = lambda ref, pid="", **kw: tn.find_task(rooms, ref, pid, keys, names, **kw)
        self.assertEqual(find("#18", "p1"), ("room-a", ""))
        self.assertEqual(find("18", "p2"), ("room-b", ""))
        self.assertEqual(find("O-18", "p1"), ("room-b", ""))
        self.assertEqual(find("ed-18"), ("room-a", ""))
        self.assertEqual(find("#3", "p1"), ("room-b", ""), "an old number in the project it left")
        self.assertEqual(find("room-c"), ("room-c", ""))
        self.assertEqual(find("5", any_project=True), ("room-c", ""), "a number only one project has")
        self.assertEqual(find("#99", "p1"), ("", "no task #99 in Ensemble Dashboard"))
        self.assertIn("no project has the key ZZ", find("ZZ-1")[1])
        self.assertIn("several projects (ED-18, O-18)", find("18", any_project=True)[1])
        self.assertIn("give its key", find("18")[1])
        self.assertIn("not a task", find("hello", "p1")[1])
        self.assertEqual(find("room-nope")[0], "")


class Hub(unittest.TestCase):
    """A throwaway projects root, state dir and rooms dir, with two projects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "EnsembleProjects"
        self.root.mkdir()
        state = base / "state"
        state.mkdir()
        for p in (mock.patch.object(dashboard, "PROJECTS_ROOT", self.root),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                  mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                  mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                  mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
                  mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(dashboard, "load_sessions", lambda *a, **k: [])):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.ed = self.project("Ensemble Dashboard")
        self.ot = self.project("OPtionTradingENgine")

    def project(self, name: str) -> str:
        ok, proj, _ = dashboard.register_project(name)
        self.assertTrue(ok)
        return proj["id"]

    def meta(self, pid: str) -> dict:
        return json.loads((Path(dashboard.find_project(pid)["path"]) / "project.json").read_text(encoding="utf-8"))

    def room(self, title: str, pid: str = "", created: float | None = None, numbered: bool = False) -> str:
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"},
                                           {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer"}])["id"]
        full = chatroom.get_room(rid, public=False)
        if created is not None:
            full["createdAt"] = created
        full["projectId"] = pid
        chatroom.update_room(full)
        if pid:
            dashboard.assign_session_project(rid, pid)
            if numbered:
                dashboard.assign_task_number(rid, pid)
        return rid

    def no(self, rid: str):
        return (chatroom.get_room(rid) or {}).get("no")

    def call(self, method, path, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}"}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        hdrs = dict(ln.split(": ", 1) for ln in head.decode("latin-1").split("\r\n")[1:] if ": " in ln)
        return int(head.split(b" ", 2)[1]), hdrs, payload


class Numbering(Hub):
    def test_projects_get_their_keys_when_registered(self):
        self.assertEqual((self.meta(self.ed)["key"], self.meta(self.ot)["key"]), ("ED", "O"))
        self.assertEqual(self.meta(self.project("Ops Tools"))["key"], "OT")
        self.assertEqual(self.meta(self.project("Other"))["key"], "O2", "a key another project has is not given")

    def test_a_new_task_takes_the_next_number(self):
        a = self.room("First", self.ed, numbered=True)
        b = self.room("Second", self.ed, numbered=True)
        c = self.room("Elsewhere", self.ot, numbered=True)
        self.assertEqual((self.no(a), self.no(b), self.no(c)), (1, 2, 1))
        self.assertEqual(self.meta(self.ed)["nextTaskNo"], 3)
        self.assertEqual(dashboard.assign_task_number(a, self.ed), 1, "asked again, it keeps its number")
        self.assertEqual(self.meta(self.ed)["nextTaskNo"], 3)
        chatroom.delete_room(b)
        d = self.room("After a delete", self.ed, numbered=True)
        self.assertEqual(self.no(d), 3, "a deleted task's number is not given again")

    def test_create_task_numbers_the_task_it_returns(self):
        with mock.patch.object(dashboard, "setup_session_workspace",
                               lambda proj, ws, title: (True, str(self.root), {"mode": "empty"}, "")):
            ok, room_full, err = dashboard.create_task("Built by a tool", "spec", self.ed,
                                                       [{"agent": "claude"}], "empty")
        self.assertTrue(ok, err)
        self.assertEqual((room_full["no"], self.no(room_full["id"])), (1, 1))
        # The caller writes its copy back (a launch does): the number stays.
        room_full.pop("no")
        chatroom.update_room(room_full)
        self.assertEqual(self.no(room_full["id"]), 1)

    def test_a_copy_read_before_the_number_never_loses_it(self):
        rid = self.room("Racing", self.ed)
        stale = chatroom.get_room(rid, public=False)
        dashboard.assign_task_number(rid, self.ed)
        stale["status"] = "paused"
        chatroom.update_room(stale)
        room = chatroom.get_room(rid)
        self.assertEqual((room["no"], room["noProjectId"], room["status"]), (1, self.ed, "paused"))

    def test_a_copy_read_before_a_move_never_undoes_it(self):
        rid = self.room("Resuming", self.ed, numbered=True)
        stale = chatroom.get_room(rid, public=False)       # a resume read it, then started the terminals
        self.assertTrue(dashboard.move_task(rid, self.ot))
        stale["status"] = "active"
        chatroom.update_room(stale)
        room = chatroom.get_room(rid)
        self.assertEqual((room["no"], room["noProjectId"], room["previousNos"], room["status"]),
                         (1, self.ot, [{"projectId": self.ed, "no": 1}], "active"))
        self.assertEqual(dashboard.resolve_task_ref("O-1"), (rid, ""))

    def test_an_adopted_session_takes_the_next_number(self):
        self.room("Already", self.ed, numbered=True)
        home = Path(dashboard.find_project(self.ed)["path"])
        with mock.patch.object(dashboard.Handler, "_resume_room_agent_pty", lambda *a, **k: {"ptyId": 7}, create=True):
            status, _, body = self.call("POST", "/api/session/adopt", {
                "label": "Old work", "members": [{"agent": "claude", "sessionId": "s1", "cwd": str(home / "old" / "claude")},
                                                 {"agent": "codex", "sessionId": "s2", "cwd": str(home / "old" / "codex")}]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.no(json.loads(body)["room"]["id"]), 2)

    def test_the_po_gets_no_number(self):
        po = self.room("The PO", self.ed)
        self.assertEqual(dashboard.set_project_po(self.ed, po), (True, "ok"))
        self.assertIsNone(dashboard.assign_task_number(po, self.ed))
        dashboard.backfill_task_numbers()
        self.assertIsNone(self.no(po))

    def test_moving_a_task_gives_it_the_next_number_there(self):
        for t in ("One", "Two", "Three"):
            self.room(t, self.ot, numbered=True)
        rid = self.room("Moving", self.ed, numbered=True)
        task_dir = self.root / "moving_task"
        full = chatroom.get_room(rid, public=False)
        full["taskDir"] = str(task_dir)
        chatroom.update_room(full)
        self.assertTrue(dashboard.move_task(rid, self.ot))
        room = chatroom.get_room(rid)
        self.assertEqual((room["no"], room["noProjectId"], room["previousNos"]),
                         (4, self.ot, [{"projectId": self.ed, "no": 1}]))
        on_disk = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertEqual((on_disk["no"], on_disk["previousNos"]), (4, [{"projectId": self.ed, "no": 1}]))
        # Old links still land; the new number works; the old project's counter moved on.
        self.assertEqual(dashboard.resolve_task_ref("#1", self.ed), (rid, ""))
        self.assertEqual(dashboard.resolve_task_ref("ED-1"), (rid, ""))
        self.assertEqual(dashboard.resolve_task_ref("O-4"), (rid, ""))
        self.assertEqual(dashboard.resolve_task_ref("4", self.ot), (rid, ""))
        self.assertEqual(self.no(self.room("Next in ED", self.ed, numbered=True)), 2)
        # Moved through the page (the project link alone) it is numbered too.
        other = self.room("Page move", self.ed, numbered=True)
        status, _, _ = self.call("POST", "/api/projects/assign", {"roomId": other, "projectId": self.ot})
        self.assertEqual((status, self.no(other)), (200, 5))

    def test_unknown_numbers(self):
        self.room("Only", self.ed, numbered=True)
        self.assertEqual(dashboard.resolve_task_ref("#2", self.ed), ("", "no task #2 in Ensemble Dashboard"))
        self.assertEqual(dashboard.resolve_task_ref("X-1")[0], "")
        self.assertIn("give its key", dashboard.resolve_task_ref("#1")[1])


class Backfill(Hub):
    def test_old_tasks_are_numbered_once(self):
        titles = ["TWS NonBlocking API", "5. One broker connection", "1. Show each option chain",
                  "3R. CP0 shared rules takeover", "18. 0DTE management", "20. One New York trading day"]
        trading = [self.room(t, self.ot, created=100 + i) for i, t in enumerate(titles)]
        ours = [self.room(t, self.ed, created=200 + i) for i, t in enumerate(("Name it", "Codex only", "MCP"))]
        po = self.room("opten", self.ot, created=50)
        loose = self.room("No project", "", created=10)
        dashboard.set_project_po(self.ot, po)
        for p in (self.ed, self.ot):                       # as before numbers: no key, no counter
            meta = self.meta(p)
            meta.pop("key", None)
            (Path(dashboard.find_project(p)["path"]) / "project.json").write_text(json.dumps(meta), encoding="utf-8")
        out = dashboard.backfill_task_numbers()
        self.assertEqual(out, {"numbered": 9, "keys": 2})
        self.assertEqual([self.no(r) for r in trading], [21, 5, 1, 22, 18, 20])
        self.assertEqual([self.no(r) for r in ours], [1, 2, 3])
        self.assertEqual((self.no(po), self.no(loose)), (None, None))
        self.assertEqual((self.meta(self.ot)["nextTaskNo"], self.meta(self.ed)["nextTaskNo"]), (23, 4))
        self.assertEqual((self.meta(self.ot)["key"], self.meta(self.ed)["key"]), ("O", "ED"))

        files = {p: p.read_bytes() for p in list(chatroom.ROOMS_DIR.glob("*.json")) + list(self.root.glob("*/project.json"))}
        time.sleep(0.02)
        self.assertEqual(dashboard.backfill_task_numbers(), {"numbered": 0, "keys": 0})
        self.assertEqual({p: p.read_bytes() for p in files}, files, "a second start changes nothing")
        # A task created after the backfill continues the counter.
        self.assertEqual(self.no(self.room("21. New after restart", self.ot, numbered=True)), 23)

    def test_a_task_moved_while_the_hub_was_off_is_renumbered(self):
        rid = self.room("Moved by hand", self.ed, numbered=True)
        self.room("There already", self.ot, numbered=True)
        dashboard.assign_session_project(rid, self.ot)     # only the link changed
        dashboard.backfill_task_numbers()
        room = chatroom.get_room(rid)
        self.assertEqual((room["no"], room["noProjectId"], room["previousNos"]),
                         (2, self.ot, [{"projectId": self.ed, "no": 1}]))


class Addresses(Hub):
    def setUp(self):
        super().setUp()
        self.a = self.room("Alpha", self.ed, numbered=True)
        self.b = self.room("Beta", self.ed, numbered=True)
        self.x = self.room("Trading one", self.ot, numbered=True)
        self.po = self.room("PO", self.ed)
        dashboard.set_project_po(self.ed, self.po)

    def tool(self, name, args, room=None):
        handler = SimpleNamespace(_resume_room=lambda r: None, _ring_recipients=lambda *a: None,
                                  _ring_report=lambda *a: [])
        text, err = ensemble_tools.call(name, args, room or self.po, "claude", handler)
        return (json.loads(text) if not err else text), err

    def test_get_task_keeps_the_real_report_an_update_followed(self):
        chatroom.record_report(self.b, "codex", "question", "Which motor?")
        out, _ = self.tool("ensemble_get_task", {"taskId": "#2"})
        self.assertEqual(out["lastReport"]["kind"], "question")
        self.assertNotIn("lastRealReport", out)
        time.sleep(0.002)
        chatroom.record_report(self.b, "codex", "update", "Working again")
        out, _ = self.tool("ensemble_get_task", {"taskId": "#2"})
        self.assertEqual(out["lastReport"]["kind"], "update")
        self.assertEqual((out["lastRealReport"]["kind"], out["lastRealReport"]["text"]),
                         ("question", "Which motor?"))

    def test_tools_take_a_number(self):
        for ref in ("#2", "2", 2, "ED-2", "ed-2", self.b):
            out, err = self.tool("ensemble_get_task", {"taskId": ref})
            self.assertFalse(err, (ref, out))
            self.assertEqual((out["id"], out["no"], out["ref"]), (self.b, 2, "ED-2"), ref)
        out, err = self.tool("ensemble_get_task", {"taskId": "#1", "projectId": self.ot})
        self.assertEqual(out["id"], self.x)
        out, err = self.tool("ensemble_get_task", {"taskId": "O-1"})
        self.assertEqual(out["id"], self.x)
        out, err = self.tool("ensemble_get_task", {"taskId": "#9"})
        self.assertTrue(err)
        self.assertEqual(out, "error: no task #9 in Ensemble Dashboard")
        out, err = self.tool("ensemble_update_task", {"taskId": "#1", "priority": "high"})
        self.assertFalse(err, out)
        self.assertEqual(out["taskId"], self.a)
        out, err = self.tool("ensemble_stop_task", {"taskId": "ED-2"})
        self.assertEqual((err, out["taskId"]), (False, self.b))

    def test_the_projects_po_sets_done_whatever_its_seat_is_called(self):
        # The PO here holds an "engineer" seat, as a documents project's PO or a
        # past session made PO does: the project naming its room is what counts.
        out, err = self.tool("ensemble_update_task", {"taskId": "#1", "workflow": "done"})
        self.assertFalse(err, out)
        self.assertEqual(chatroom.get_room(self.a).get("workflow"), "done")     # stored, not derived
        # Not the task's own owner, not the PO room's reviewer seat.
        out, err = self.tool("ensemble_update_task", {"taskId": "#2", "workflow": "done"}, room=self.b)
        self.assertTrue(err)
        text, err = ensemble_tools.call("ensemble_update_task", {"taskId": "#2", "workflow": "done"},
                                        self.po, "codex", SimpleNamespace())
        self.assertTrue(err, text)
        self.assertNotEqual(chatroom.get_room(self.b).get("workflow"), "done")

    def test_rows_carry_the_number_first(self):
        out, _ = self.tool("ensemble_list_tasks", {})
        rows = {r["id"]: r for r in out["tasks"]}
        self.assertEqual(list(rows[self.a])[:2], ["no", "id"])
        self.assertEqual((rows[self.a]["no"], rows[self.b]["no"]), (1, 2))
        self.assertNotIn("no", rows[self.po], "the PO has no number")
        out, _ = self.tool("ensemble_list_tasks", {"projectId": "*"})
        self.assertEqual({r["id"]: r.get("ref") for r in out["tasks"]}[self.x], "O-1", "the full form across projects")
        me, _ = self.tool("ensemble_whoami", {}, room=self.a)
        self.assertEqual((me["taskNo"], me["taskId"]), (1, self.a))

    def test_http_endpoints_take_a_number(self):
        status, _, body = self.call("GET", f"/api/room?id=%232&project={self.ed}")
        self.assertEqual((status, json.loads(body)["id"]), (200, self.b))
        status, _, body = self.call("GET", f"/api/room?task=2&project={self.ed}")
        self.assertEqual((status, json.loads(body)["id"]), (200, self.b))
        status, _, body = self.call("GET", "/api/room?id=ED-1")
        self.assertEqual((status, json.loads(body)["id"]), (200, self.a))
        status, _, body = self.call("GET", f"/api/room?id=%239&project={self.ed}")
        self.assertEqual((status, json.loads(body)), (404, {"error": "no_such_task",
                                                            "message": "no task #9 in Ensemble Dashboard"}))
        status, _, body = self.call("GET", "/api/room?id=room-nope")
        self.assertEqual((status, json.loads(body)), (404, {"error": "no_such_room"}), "a room id as before")
        status, _, body = self.call("POST", "/api/room/workflow", {"roomId": "#2", "projectId": self.ed, "workflow": "inreview"})
        self.assertEqual(status, 200, body)
        self.assertEqual(chatroom.get_room(self.b)["workflow"], "inreview")
        status, _, body = self.call("POST", "/api/room/workflow", {"task": "ED-9", "workflow": "done"})
        self.assertEqual((status, json.loads(body)["error"]), (404, "no_such_task"))
        status, _, body = self.call("POST", "/api/room/resume", {"roomId": "O-7"})
        self.assertEqual((status, json.loads(body)["message"]), (404, "no task #7 in OPtionTradingENgine"))

    def test_the_session_page_goes_to_the_rooms_address(self):
        status, hdrs, _ = self.call("GET", f"/session?room=%232&project={self.ed}&msg=abc")
        self.assertEqual((status, hdrs.get("Location")), (302, f"/session?room={self.b}&msg=abc"))
        status, hdrs, _ = self.call("GET", "/session?id=ED-1&embed=1")
        self.assertEqual((status, hdrs.get("Location")), (302, f"/session?id={self.a}&embed=1"))
        status, hdrs, _ = self.call("GET", "/session?task=O-1")
        self.assertEqual((status, hdrs.get("Location")), (302, f"/session?room={self.x}"))
        status, _, body = self.call("GET", "/session?room=ED-99")
        self.assertEqual((status, body.decode("utf-8").strip()), (404, "no task #99 in Ensemble Dashboard"))
        status, hdrs, _ = self.call("GET", f"/session?room={self.a}")
        self.assertEqual((status, hdrs.get("Location")), (200, None), "a room id is served as before")
        status, hdrs, _ = self.call("GET", "/session?id=0f3c2a9e-legacy")
        self.assertEqual((status, hdrs.get("Location")), (200, None), "and anything that is not a number")
        status, _, body = self.call("GET", "/api/room?id=not-a-number")
        self.assertEqual((status, json.loads(body)), (404, {"error": "no_such_room"}))

    def test_a_chip_asks_for_its_task(self):
        status, _, body = self.call("GET", f"/api/task/ref?ref=%232&room={self.a}")
        info = json.loads(body)
        self.assertEqual((status, info["roomId"], info["label"], info["ref"], info["title"], info["status"],
                          info["workflowName"]), (200, self.b, "#2", "ED-2", "Beta", "not running", "Done"))
        status, _, body = self.call("GET", f"/api/task/ref?ref=O-1&room={self.a}")
        self.assertEqual(json.loads(body)["roomId"], self.x)
        status, _, body = self.call("GET", f"/api/task/ref?ref=%231&room={self.x}")
        self.assertEqual(json.loads(body)["roomId"], self.x, "read in the chat's own project")
        status, _, body = self.call("GET", f"/api/task/ref?ref=%235&room={self.a}")
        self.assertEqual(status, 404)

    def test_a_projects_key_is_set_on_its_settings(self):
        status, _, body = self.call("POST", "/api/projects/key", {"projectId": self.ot, "key": "ot"})
        self.assertEqual((status, json.loads(body)), (200, {"ok": True, "key": "OT"}))
        self.assertEqual(dashboard.resolve_task_ref("OT-1"), (self.x, ""))
        status, _, body = self.call("POST", "/api/projects/key", {"projectId": self.ot, "key": "ED"})
        self.assertEqual((status, json.loads(body)["error"]), (400, "key_taken"))
        status, _, body = self.call("POST", "/api/projects/key", {"projectId": self.ot, "key": "1x"})
        self.assertEqual((status, json.loads(body)["error"]), (400, "bad_key"))
        status, _, body = self.call("POST", "/api/projects/key", {"projectId": "proj-nope", "key": "ZZ"})
        self.assertEqual(status, 404)
        status, _, body = self.call("GET", "/api/projects")
        self.assertEqual({p["id"]: p["key"] for p in json.loads(body)["projects"]}, {self.ed: "ED", self.ot: "OT"})


class WhatTheHubWrites(Addresses):
    def test_a_line_under_a_message_for_each_task_it_names(self):
        full = chatroom.get_room(self.b, public=False)
        full["workspace"] = {"branch": "sess/beta"}
        full["lastReport"] = {"kind": "completed", "text": "\n  Merged and tested.\nMore detail."}
        chatroom.update_room(full)
        text = "Check #2 with @codex@2, and O-1 is not a ref but #O-1 is; #9 is nobody."
        out = dashboard.with_message_refs(text, self.a)
        self.assertEqual(out, text + "\n\n"
                         '[ref #2] task "Beta" — not running, Done; claude (engineer), codex (reviewer); '
                         "branch sess/beta; last report (completed): Merged and tested.\n\n"
                         '[ref #O-1] task #1 "Trading one" — not running, Done; claude (engineer), codex (reviewer)')
        self.assertEqual(mr.strip_message_refs(out), text)
        self.assertEqual(dashboard.with_message_refs("#1 here", self.x).split("\n\n")[1][:30], '[ref #1] task "Trading one" — ')
        self.assertEqual(dashboard.with_message_refs("no refs #9", self.a), "no refs #9")
        with mock.patch.object(dashboard, "_task_index", side_effect=AssertionError("read")):
            self.assertEqual(dashboard.with_message_refs("names no task", self.a), "names no task",
                             "a message naming no task reads nothing")

    def test_task_lines_come_after_balloon_blocks_and_strip_together(self):
        url = f"http://127.0.0.1:8765/session?room={self.a}&msg=aaaaaaaaaaaa"
        msgs = chatroom.post_message(self.a, "user", "hello there")
        url = url.replace("aaaaaaaaaaaa", msgs["message"]["id"])
        text = f"see {url} and #2"
        out = dashboard.with_message_refs(text, self.a)
        blocks = out.split("\n\n")
        self.assertTrue(blocks[1].startswith(f"[ref {url}] from "), blocks[1])
        self.assertIn('in "#1 Alpha" at', blocks[1], "a balloon's task is named by its number")
        self.assertTrue(blocks[2].startswith('[ref #2] task "Beta"'), blocks[2])
        self.assertEqual(mr.strip_message_refs(out), text)
        own = "My note about #2\n\n[ref #3] task \"mine\" — typed by me"
        self.assertEqual(mr.strip_message_refs(own), own, "a person's own line whose number is not above stays")

    def test_the_report_wake_names_the_task_by_number(self):
        typed = []
        h = dashboard.Handler.__new__(dashboard.Handler)
        h._ring = lambda room, idents, wake: typed.append(wake) or idents
        h._ring_report(self.po, {"recipients": ["claude"]}, self.b, "Beta", "codex", "completed", "done")
        self.assertEqual(typed, ["[report] completed from task 'Beta' (#2, codex): done — read it in full with "
                                 "ensemble_get_task taskId=#2 messages=0."])
        self.assertEqual(dashboard.hub_input_kind(typed[0])["taskId"], "#2")
        typed.clear()
        loose = self.room("No project")
        h._ring_report(self.po, {"recipients": ["claude"]}, loose, "No project", "codex", "completed", "done")
        self.assertIn(f"({loose}, codex)", typed[0], "a task without a number keeps its id")

    def test_the_review_brief_and_the_digest(self):
        room = chatroom.get_room(self.b, public=False)
        part = chatroom.participant(room, "codex")
        with mock.patch.object(dashboard, "review_log_path", lambda r: "REVIEW-LOG.md"):
            brief = dashboard.review_brief(room, part, {"id": "m", "from": "user", "text": "look at #1"}, 1, {}, "")
        self.assertIn(f'the reviewer on the task #2 "Beta" ({self.b})', brief)
        self.assertIn('> [ref #1] task "Alpha"', brief)
        facts = [{"id": self.b, "label": "#2", "title": "Beta", "status": "running", "column": "inprogress",
                  "idleSeconds": 5, "attention": "", "branch": "", "reportKind": ""}]
        text = digest.plain_facts({"id": self.ed, "name": "ED"}, facts,
                                  [{"id": self.b, "label": "#2", "title": "Beta", "what": ["new commits"], "finished": False}], 0)
        self.assertIn("- Beta (#2): new commits", text)
        self.assertIn("- Beta (#2): running", text)
        self.assertNotIn(self.b, text)
        self.assertEqual(digest._task_facts(chatroom.get_room(self.a), {}, {}, time.time())["label"], "#1")


if __name__ == "__main__":
    unittest.main()
