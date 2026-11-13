"""Stop means stopped, also for a task that is not running (P324).

A task's start was refused because its folder is gone (``folderMissing``,
as seat_folders writes it), and nothing runs for it. Its PO stops it with
``ensemble_stop_task``: before, the tool answered "it was not running" and
changed nothing, so the Inbox kept the could-not-start card for ever (OP-431,
11-12). Now, against a hub in a thread:

* the stop answers that nothing was running, records the task as stopped (a
  cold start leaves it) and clears its refusal; /api/inbox has no waiting row;
* a paused task, a task stopped after its refusal and a Done task whose work
  is merged make no card; a refusal after a stop (a Start since) does;
* a Start after the stop still launches it, and checks the folder again: the
  refusal and its card come back;
* the task's action bar offers Stop while its refused start stands.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import ensemble_tools  # noqa: E402
import inbox  # noqa: E402


class StopNotRunning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-stopnr-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.noted_stopped = []
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.running_rooms, "note_stopped",
                              lambda rid: cls.noted_stopped.append(rid)),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)       # tests/test_inbox.py leaves it bound to its fake hub
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        cls.proj = proj
        cls.home = Path(proj.get("home") or proj["path"])
        po = chatroom.create_room("Motors PO", cls.members(cls.home))
        dashboard.assign_session_project(po["id"], proj["id"])
        ok, why = dashboard.set_project_po(proj["id"], po["id"])
        assert ok, why
        cls.po = po["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.n = 10

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    @staticmethod
    def members(cwd):
        return [{"identity": "claude", "agent": "claude", "cwd": str(cwd)},
                {"identity": "codex", "agent": "codex", "cwd": str(cwd)}]

    def task(self, since_ago=120, **fields):
        """A task whose seat's folder is gone and whose start was refused."""
        type(self).n += 1
        no = self.n
        gone = self.home / f"task_{no}" / "repo"           # never made
        tk = chatroom.create_room(f"Task {no}", self.members(gone))
        dashboard.assign_session_project(tk["id"], self.proj["id"])
        chatroom.patch_room(tk["id"], cwd=str(gone), no=no, noProjectId=self.proj["id"], folderMissing={
            "path": str(gone), "identity": "claude", "kind": "missing", "since": time.time() - since_ago,
            "reason": f"claude's folder {gone} is missing: recreate it, or move the task "
                      f"to a folder that exists, then start it again.",
            "roomCwd": str(gone), "seatCwd": str(gone)}, **fields)
        return tk["id"]

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def waiting(self, rid):
        """The task's waiting rows in /api/inbox (attention read afresh)."""
        dashboard.attention._result = (0.0, {})
        inbox.forget()
        return [r for r in self.get("/api/inbox")["rows"]
                if r.get("roomId") == rid and r.get("kind") == "waiting"]

    def tool(self, name, args, handler=None):
        handler = handler or SimpleNamespace(_resume_room=lambda r: None)
        text, err = ensemble_tools.call(name, args, self.po, "claude", handler)
        return (json.loads(text) if not err else text), err

    # --- Stop ---------------------------------------------------------------

    def test_stop_of_a_not_running_refused_task_stops_it_and_its_card_goes(self):
        rid = self.task()
        self.assertEqual(len(self.waiting(rid)), 1, "the refusal shows before the stop")
        out, err = self.tool("ensemble_stop_task", {"taskId": f"#{self.n}"})
        self.assertFalse(err, out)
        self.assertEqual((out["taskId"], out["status"], out["note"]), (rid, "stopped", "it was not running"))
        room = chatroom.get_room(rid, public=False)
        self.assertFalse(room.get("folderMissing"), "the refusal is cleared")
        self.assertGreater(float(room.get("stoppedAt") or 0), 0, "recorded as stopped")
        self.assertIn(rid, self.noted_stopped, "a cold start leaves it")
        self.assertEqual(self.waiting(rid), [])

    def test_the_pages_stop_of_a_not_running_task_does_the_same(self):
        rid = self.task()
        req = urllib.request.Request(self.base + "/api/room/close", data=json.dumps({"roomId": rid}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            self.assertTrue(json.loads(r.read())["ok"])
        self.assertFalse(chatroom.get_room(rid, public=False).get("folderMissing"))
        self.assertEqual(self.waiting(rid), [])

    def test_a_start_after_the_stop_launches_it_and_checks_the_folder_again(self):
        rid = self.task()
        self.tool("ensemble_stop_task", {"taskId": f"#{self.n}"})
        started = []
        out, err = self.tool("ensemble_start_task", {"taskId": f"#{self.n}"},
                             SimpleNamespace(_resume_room=lambda r: started.append(r["id"]) or {}))
        self.assertFalse(err, out)
        self.assertEqual(started, [rid], "a Start still launches it")
        # The real start: its folder is still gone, so it is refused anew.
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.server = self.server
        out, err = self.tool("ensemble_start_task", {"taskId": f"#{self.n}"}, handler)
        self.assertTrue(err, out)
        self.assertIn("missing", out)
        room = chatroom.get_room(rid, public=False)
        self.assertGreater(room["folderMissing"]["since"], float(room["stoppedAt"]))
        self.assertEqual(len(self.waiting(rid)), 1, "someone asked it to run: its card is back")

    def test_the_room_read_says_whether_its_refused_start_stands(self):
        rid = self.task()
        self.assertIs(self.get(f"/api/room?id={rid}&lite=1")["couldNotStart"], True,
                      "the pop-out offers Stop from this")
        self.tool("ensemble_stop_task", {"taskId": f"#{self.n}"})
        self.assertIs(self.get(f"/api/room?id={rid}&lite=1")["couldNotStart"], False)

    # --- A Stop and a start under way (review 1) ------------------------------

    def test_a_stop_waits_for_a_start_under_way_and_ends_what_it_started(self):
        rid = self.task()
        lock = dashboard.rotation.launch_lock(rid)
        held, release = threading.Event(), threading.Event()

        def launcher():         # a start: holds the lock to its write of the room
            with lock:
                held.set()
                release.wait(20)
                chatroom.patch_participant(rid, "claude", {"ptyId": "pty-started"})

        killed = []
        with mock.patch.object(dashboard.ptyrun, "kill", lambda pid: killed.append(pid)), \
                mock.patch.object(dashboard, "room_ptys", lambda r: []):
            lt = threading.Thread(target=launcher, daemon=True)
            lt.start()
            self.assertTrue(held.wait(10))
            done = []
            st = threading.Thread(target=lambda: done.append(dashboard.stop_task(rid)), daemon=True)
            st.start()
            st.join(0.5)
            self.assertTrue(st.is_alive(), "the Stop waits for the start")
            self.assertTrue(chatroom.get_room(rid, public=False).get("folderMissing"))
            release.set()
            st.join(20)
            lt.join(20)
        self.assertEqual(done, [True])
        self.assertIn("pty-started", killed, "the terminal the start recorded is ended")
        self.assertFalse(chatroom.get_room(rid, public=False).get("folderMissing"))

    def test_a_stop_goes_on_when_a_start_holds_the_lock_too_long(self):
        rid = self.task()
        lock = dashboard.rotation.launch_lock(rid)
        held, release = threading.Event(), threading.Event()

        def stuck():
            with lock:
                held.set()
                release.wait(20)

        t = threading.Thread(target=stuck, daemon=True)
        t.start()
        self.assertTrue(held.wait(10))
        try:
            with mock.patch.object(dashboard, "STOP_LAUNCH_WAIT_S", 0.2), \
                    mock.patch.object(dashboard, "room_ptys", lambda r: []):
                self.assertTrue(dashboard.stop_task(rid))
        finally:
            release.set()
            t.join(20)
        self.assertFalse(chatroom.get_room(rid, public=False).get("folderMissing"))

    def test_a_stop_from_the_thread_that_holds_the_lock_does_not_wait(self):
        rid = self.task()
        with dashboard.rotation.launch_lock(rid), \
                mock.patch.object(dashboard, "room_ptys", lambda r: []):
            t0 = time.monotonic()
            self.assertTrue(dashboard.stop_task(rid))       # a rotation stopping its task
            self.assertLess(time.monotonic() - t0, 5)

    # --- No card for a task nobody asked to run -----------------------------

    def test_a_paused_task_makes_no_card(self):
        rid = self.task(status="paused")
        self.assertEqual(self.waiting(rid), [])

    def test_a_task_stopped_after_its_refusal_makes_no_card(self):
        rid = self.task(since_ago=600, stoppedAt=time.time() - 60)
        self.assertEqual(self.waiting(rid), [])
        rid = self.task(since_ago=60, stoppedAt=time.time() - 600)     # refused at a Start since
        self.assertEqual(len(self.waiting(rid)), 1)

    def test_a_done_task_whose_work_is_merged_makes_no_card(self):
        rid = self.task(workflow="done", mergedHead="4430d0e8e", mergedAt=time.time() - 3600)
        self.assertEqual(self.waiting(rid), [])
        rid = self.task(workflow="inreview", mergedHead="4430d0e8e", mergedAt=time.time() - 3600)
        self.assertEqual(len(self.waiting(rid)), 1, "merged but not Done: still shown")

    def test_the_card_says_what_to_do(self):
        rid = self.task()
        [r] = self.waiting(rid)
        self.assertRegex(r["line"], rf"^(#|[A-Z]+-){self.n} could not start: its work folder is missing$")
        self.assertEqual(r["reason"], "Its agent cannot start because its folder was removed. "
                                      "Press Done, or open the task and Stop it.")
        self.assertNotIn(str(self.home), r["line"] + r["reason"])
        self.assertEqual(r["dismiss"], "dealt")


class StopItem(unittest.TestCase):
    """The action bar offers Stop for a task whose refused start stands."""

    def labels(self, **state):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("needs node")
        st = {"kind": "room", "roomId": "r1", "live": False, **state}
        js = (f"const A = require({json.dumps(str(ROOT / 'static' / 'actions.js'))});"
              f"const m = A.sessionActions({json.dumps(st)}, {{features: {{}}}});"
              "console.log(JSON.stringify([m.primary && m.primary.label, ...m.groups.flat().map(i => [i.label, i.cls || ''])]));")
        out = subprocess.run([node, "-e", js], capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_stop_is_offered_only_while_a_refused_start_stands(self):
        got = self.labels(couldNotStart=True)
        self.assertEqual(got[0], "Resume")
        self.assertIn(["Stop", "room-end"], got[1:])
        self.assertNotIn(["Stop", "room-end"], self.labels()[1:])
        self.assertNotIn(["Stop", "room-end"], self.labels(couldNotStart=True, draft=True)[1:])


    def test_the_pop_out_reads_it_and_stays_open_for_a_not_running_task(self):
        page = (ROOT / "session.html").read_text(encoding="utf-8")
        state = page[page.index("function actionState()"):page.index("function actionEnv()")]
        self.assertIn("couldNotStart: room.couldNotStart === true", state)
        end = page[page.index("  async end() {"):]
        end = end[:end.index("window.close()")]
        self.assertIn("ROOM_OBJ.live === false", end, "a not-running task's Stop keeps the window")


if __name__ == "__main__":
    unittest.main()
