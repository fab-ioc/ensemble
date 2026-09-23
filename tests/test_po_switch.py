"""Switching a project's PO to the other agent kind (rotation.request_switch,
the one switch path rotation._switch_po), and the one-terminal-per-seat
invariant around it: the launch guard, Stop, the detector, the room write."""
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import rotation  # noqa: E402
import test_rotation_kind as _kind  # noqa: E402

_snap = _kind._snap
PORT = 8798
_REAL_ASK = rotation._ask_before_switch


class _SyncThread:
    """threading.Thread that runs its target on start(), in the test's thread."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._run = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self._run()


class _Switch(_kind.PoUsageFailoverTests):
    """The failover fixture (fake launcher, ptyrun, allowance), without its tests."""

    def setUp(self):
        super().setUp()
        rotation._LAST_SWITCH.clear()
        self.asked = {"asked": True, "answered": True, "handoverUpdated": True}
        for p in [
            mock.patch.object(rotation.threading, "Thread", _SyncThread),
            mock.patch.object(rotation, "_ask_before_switch",
                              side_effect=lambda *a: dict(self.asked)),
            mock.patch.object(dashboard, "pending_input", return_value=None),
        ]:
            p.start()
            self.patches.append(p)

    def notices(self, project, kind):
        return [m for m in self.saved(project)["messages"] if m.get("noticeKind") == kind]


for _name in [n for n in vars(_kind.PoUsageFailoverTests) if n.startswith("test_")]:
    setattr(_Switch, _name, None)


class ManualSwitchTests(_Switch):
    def test_claude_to_codex_keeps_the_room_and_ends_the_old_terminal(self):
        project, _ = self.po()
        original = self.saved(project)
        chatroom.post_notice(project["poRoomId"], "human", "Existing chat", {})
        out = rotation.request_switch(project, "codex")
        self.assertEqual((out["agent"], out["model"]), ("codex", "gpt-5.6-sol"))
        saved = self.saved(project)
        part = chatroom.participant(saved, "po")
        self.assertEqual((part["agent"], part["sessionId"], part["ptyId"]),
                         ("codex", "codex-new", "pty-1"))
        self.assertEqual((saved["id"], saved["tokens"], chatroom.po_identity(saved)),
                         (original["id"], original["tokens"], "po"))
        self.assertTrue(any(m["text"] == "Existing chat" for m in saved["messages"]))
        self.assertEqual(self.killed, ["old-pty"])
        self.assertEqual(len(self.launcher.launched), 1)
        self.assertIn("switched this PO to Codex", self.launcher.launched[0]["text"])
        self.assertIn("PO-HANDOVER.md", self.launcher.launched[0]["text"])
        self.assertEqual(saved["poSwitch"]["cause"], "manual")
        self.assertEqual(part["sessionKinds"], {"old-sid": "claude"})
        self.assertEqual(len(self.notices(project, "poSwitch")), 1)
        self.assertIn("brought its handover up to date",
                      self.notices(project, "poSwitch")[0]["text"])
        self.assertFalse(rotation.switching(project["poRoomId"]))
        self.assertEqual(rotation._LAST_SWITCH[project["poRoomId"]]["result"], "switched")

    def test_codex_to_claude_and_back(self):
        self.snap = _snap(15, 15)
        project, _ = self.po("codex", "gpt-5.6-sol")
        rotation.request_switch(project, "claude", "sonnet")
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual((part["agent"], part["model"]), ("claude", "sonnet"))
        rotation.request_switch(project, "codex")
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual(part["agent"], "codex")
        self.assertEqual(len(part["rotations"]), 2)
        self.assertEqual(self.killed, ["old-pty", "pty-1"])

    def test_a_manual_switch_rearms_the_usage_failover(self):
        project, _ = self.po()
        chatroom.patch_room(project["poRoomId"], poFailover={"at": 1.0},
                            poFailoverAlert={"reason": "x"})
        rotation.request_switch(project, "codex")
        saved = self.saved(project)
        self.assertFalse(saved.get("poFailover"))
        self.assertFalse(saved.get("poFailoverAlert"))

    def test_refusals(self):
        project, _ = self.po()
        cases = [("claude", "same_kind"), ("gemini", "bad_agent")]
        for agent, code in cases:
            with self.subTest(agent=agent), self.assertRaises(rotation.SwitchRefused) as cm:
                rotation.request_switch(project, agent)
            self.assertEqual(cm.exception.code, code)
        self.installed["codex"] = False
        with self.assertRaises(rotation.SwitchRefused) as cm:
            rotation.request_switch(project, "codex")
        self.assertEqual(cm.exception.code, "not_installed")
        self.assertEqual(self.launcher.launched, [])

    def test_a_second_switch_is_refused_while_one_runs(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.assertTrue(rotation._reserve(rid, {"trigger": "manual"}))
        try:
            with self.assertRaises(rotation.SwitchRefused) as cm:
                rotation.request_switch(project, "codex")
            self.assertEqual(cm.exception.code, "busy")
            self.assertFalse(rotation.switch_info(project)["targets"]["codex"]["available"])
            item = {"roomId": rid, "state": "blocked", "cause": "usage_limit",
                    "agentIdentity": "po", "sessionId": "old-sid"}
            self.assertEqual(rotation.failover_po(project, item)["result"],
                             "a PO switch is already running")
        finally:
            rotation._unreserve(rid, None)
        self.assertEqual(self.launcher.launched, [])

    def test_over_the_warning_needs_a_confirm(self):
        self.snap = _snap(20, 90)
        project, _ = self.po()
        with self.assertRaises(rotation.SwitchRefused) as cm:
            rotation.request_switch(project, "codex")
        self.assertEqual(cm.exception.code, "needs_confirm")
        self.assertTrue(cm.exception.extra["needsConfirm"])
        self.assertEqual(self.launcher.launched, [])
        self.assertIn("warning", rotation.switch_info(project)["targets"]["codex"]["warning"])
        rotation.request_switch(project, "codex", confirm=True)
        self.assertEqual(chatroom.participant(self.saved(project), "po")["agent"], "codex")

    def test_a_replacement_that_dies_at_startup_leaves_the_old_po(self):
        project, _ = self.po()
        self.dead.add("pty-1")
        rotation.request_switch(project, "codex")
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual((part["agent"], part["ptyId"], part["sessionId"]),
                         ("claude", "old-pty", "old-sid"))
        self.assertEqual(self.killed, ["pty-1"])
        self.assertEqual(len(self.notices(project, "poSwitchFailed")), 1)
        self.assertEqual(rotation._LAST_SWITCH[project["poRoomId"]]["result"], "failed")
        self.assertFalse(rotation.switching(project["poRoomId"]))

    def test_a_stop_while_asking_cancels(self):
        project, _ = self.po()
        def stopped(*a):
            dashboard.stop_task(project["poRoomId"])
            return dict(self.asked)
        with mock.patch.object(rotation, "_ask_before_switch", side_effect=stopped):
            rotation.request_switch(project, "codex")
        self.assertEqual(self.launcher.launched, [])
        self.assertEqual(chatroom.participant(self.saved(project), "po")["agent"], "claude")

    def test_a_po_that_changed_while_asked_is_not_switched(self):
        project, _ = self.po()
        def moved(*a):
            chatroom.patch_participant(project["poRoomId"], "po", {"ptyId": "other-pty"})
            return dict(self.asked)
        with mock.patch.object(rotation, "_ask_before_switch", side_effect=moved):
            rotation.request_switch(project, "codex")
        self.assertEqual(self.launcher.launched, [])
        self.assertEqual(rotation._LAST_SWITCH[project["poRoomId"]]["result"], "cancelled")

    def test_a_stopped_po_switches_from_the_handover_without_asking(self):
        project, _ = self.po()
        self.dead.add("old-pty")
        ask = rotation._ask_before_switch
        rotation.request_switch(project, "codex")
        ask.assert_not_called()
        self.assertEqual(chatroom.participant(self.saved(project), "po")["agent"], "codex")
        self.assertIn("was not running", self.notices(project, "poSwitch")[0]["text"])

    def test_the_ask_waits_for_the_turn_to_end(self):
        project, _ = self.po()
        room = self.saved(project)
        part = chatroom.participant(room, "po")
        typed = []
        sess = SimpleNamespace(alive=lambda: True, send_line=typed.append,
                               info=lambda: {"idleSeconds": 99})
        reads = iter([{"size": 5, "turnOver": True, "promptSince": False},
                      {"size": 9, "turnOver": False, "promptSince": True},
                      {"size": 9, "turnOver": True, "promptSince": True}])
        with mock.patch.object(dashboard.ptyrun, "get", return_value=sess), \
                mock.patch.object(rotation, "_transcript_of",
                                  return_value=("t.jsonl", lambda p, since=-1: next(reads))), \
                mock.patch.object(rotation, "SWITCH_POLL_S", 0):
            out = _REAL_ASK(project, room, part, "codex", {"stopped": False})
        self.assertEqual(out, {"asked": True, "answered": True, "handoverUpdated": False})
        self.assertEqual(len(typed), 1)
        self.assertTrue(typed[0].startswith("[handover]"))
        self.assertIn("from Claude to Codex", typed[0])


class LaunchGuardTests(_Switch):
    """dashboard.Handler._launch_guarded: the room's start and resume."""

    def setUp(self):
        super().setUp()
        self.ptys = []      # live terminals the fake registry lists
        for p in [
            mock.patch.object(dashboard.ptyrun, "list_sessions",
                              side_effect=lambda: [dict(x) for x in self.ptys]),
            mock.patch.object(dashboard.ptyrun, "kill", side_effect=self.kill),
        ]:
            p.start()
            self.patches.append(p)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)

    def kill(self, pid):
        self.killed.append(pid)
        self.ptys = [x for x in self.ptys if x["id"] != pid]
        return True

    def pty(self, rid, pid, ident="po", age=60.0):
        self.ptys.append({"id": pid, "alive": True, "created": time.time() - age,
                          "meta": {"room": rid, "identity": ident, "agent": "claude"}})

    def test_the_0923_failed_write_ends_its_terminal_and_the_retry_starts_one(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        self.kill("old-pty")            # the PO has stopped: a resume brings it back
        self.killed.clear()
        n = {"calls": 0}
        def resume(room_full, **kw):
            n["calls"] += 1
            self.pty(rid, f"pty-r{n['calls']}")
            if n["calls"] == 1:
                raise PermissionError(13, "Access is denied")
            chatroom.patch_participant(rid, "po", {"ptyId": f"pty-r{n['calls']}"})
            return [{"identity": "po", "ptyId": f"pty-r{n['calls']}"}]
        with self.assertRaises(PermissionError):
            self.handler._launch_guarded(self.saved(project), resume, self.saved(project))
        self.assertEqual(self.killed, ["pty-r1"])
        self.handler._launch_guarded(self.saved(project), resume, self.saved(project))
        self.assertEqual([x["id"] for x in self.ptys], ["pty-r2"])
        self.assertIsNone(attention._duplicate_ptys(self.saved(project), time.time()))

    def test_an_unrecorded_live_terminal_refuses_a_start(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        self.pty(rid, "orphan")
        called = []
        with self.assertRaises(dashboard.StartRoomError) as cm:
            self.handler._launch_guarded(self.saved(project), lambda *a, **k: called.append(1))
        self.assertIn("orphan", str(cm.exception))
        self.assertEqual(called, [])

    def test_a_start_is_refused_while_the_po_is_being_switched(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        with rotation.GATE:
            rotation._ROTATING[(rid, "po")] = {"stopped": False}
        try:
            with self.assertRaises(dashboard.StartRoomError):
                self.handler._launch_guarded(self.saved(project), lambda *a, **k: [])
        finally:
            rotation._release((rid, "po"))

    def test_the_guard_works_from_the_room_on_disk(self):
        project, _ = self.po()
        stale = self.saved(project)
        chatroom.patch_participant(project["poRoomId"], "po", {"ptyId": "pty-new"})
        seen = []
        self.handler._launch_guarded(stale, lambda r: seen.append(
            chatroom.participant(r, "po")["ptyId"]) or [], stale)
        self.assertEqual(seen, ["pty-new"])

    def test_stop_ends_unrecorded_terminals_too(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        self.pty(rid, "orphan")
        self.pty("room-other", "elsewhere")
        dashboard.stop_task(rid)
        self.assertEqual(sorted(self.killed), ["old-pty", "orphan"])
        self.assertEqual([x["id"] for x in self.ptys], ["elsewhere"])

    def test_detector_flags_two_terminals_for_one_seat(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        room = self.saved(project)
        self.assertIsNone(attention._duplicate_ptys(room, time.time()))
        self.pty(rid, "pty-young", age=1)
        self.assertIsNone(attention._duplicate_ptys(room, time.time()))
        self.pty(rid, "pty-dup", age=40)
        state, reason, extra, part = attention._duplicate_ptys(room, time.time())
        self.assertEqual((state, extra["cause"]), ("blocked", "duplicate_pty"))
        self.assertEqual(extra["ptyIds"], ["old-pty", "pty-dup"])
        self.assertIn("2 running terminals for po", reason)
        self.assertEqual(part.get("identity"), "po")
        with rotation.GATE:
            rotation._ROTATING[(rid, "po")] = {"stopped": False}
        try:
            self.assertIsNone(attention._duplicate_ptys(room, time.time()))
        finally:
            rotation._release((rid, "po"))
        self.assertEqual(sorted(self.killed), [], "the detector never ends a terminal")

    def test_an_orphan_refuses_a_manual_switch(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        self.pty(rid, "orphan")
        self.assertIn("orphan", rotation.switch_info(project)["targets"]["codex"]["why"])
        with self.assertRaises(rotation.SwitchRefused) as cm:
            rotation.request_switch(project, "codex")
        self.assertEqual(cm.exception.code, "busy")
        self.assertEqual((self.launcher.launched, self.killed), ([], []))

    def test_an_orphan_refuses_a_failover(self):
        project, item = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        self.pty(rid, "orphan")
        out = rotation.failover_po(project, item)
        self.assertNotEqual(out["result"], "switched")
        self.assertEqual((self.launcher.launched, self.killed), ([], []))
        self.assertEqual(chatroom.participant(self.saved(project), "po")["ptyId"], "old-pty")

    def test_an_orphan_that_appears_while_the_replacement_starts_cancels(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "old-pty")
        with mock.patch.object(rotation, "_await_codex_session",
                               side_effect=lambda *a, **k: self.pty(rid, "orphan") or "codex-new"):
            rotation.request_switch(project, "codex")
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual((part["agent"], part["ptyId"]), ("claude", "old-pty"))
        self.assertIn("pty-1", self.killed)
        self.assertNotIn("old-pty", self.killed)
        self.assertEqual(rotation._LAST_SWITCH[rid]["result"], "failed")

    def test_a_codex_replacement_that_dies_while_its_id_is_found_is_not_recorded(self):
        for trigger in ("manual", "usage_limit"):
            with self.subTest(trigger=trigger):
                self.killed.clear()
                self.dead.discard("pty-1")
                project, item = self.po()
                self.launcher.launched.clear()
                def dies(*a, **k):
                    self.dead.add("pty-1")
                    return "codex-new"
                with mock.patch.object(rotation, "_await_codex_session", side_effect=dies):
                    if trigger == "manual":
                        rotation.request_switch(project, "codex")
                    else:
                        self.assertNotEqual(rotation.failover_po(project, item)["result"],
                                            "switched")
                part = chatroom.participant(self.saved(project), "po")
                self.assertEqual((part["agent"], part["ptyId"], part["sessionId"]),
                                 ("claude", "old-pty", "old-sid"))
                self.assertNotIn("old-pty", self.killed)

    def test_the_0923_failed_write_on_a_review_launch_ends_its_terminal(self):
        made = chatroom.create_room("T", [
            {"identity": "claude", "agent": "claude", "role": "Engineer"},
            {"identity": "codex", "agent": "codex", "role": "Reviewer"}])
        rid = made["id"]
        n = {"calls": 0}
        def launch(room_full, part, *a, **k):
            n["calls"] += 1
            pid = f"pty-v{n['calls']}"
            self.pty(rid, pid, ident="codex")
            return {"ptyId": pid, "sessionId": "", "cwd": self.temp.name}
        real_patch = chatroom.patch_participant
        def patch(room_id, ident, fields, **kw):
            if "review" in fields and n["calls"] == 1:
                raise PermissionError(13, "Access is denied")
            return real_patch(room_id, ident, fields, **kw)
        self.handler._launch_room_agent_pty = launch
        msg = {"from": "claude", "id": "m1", "text": "@codex review"}
        with mock.patch.object(dashboard, "apply_review_allocation",
                               side_effect=lambda r, i: (r, chatroom.participant(r, i), None)), \
                mock.patch.object(dashboard, "read_review_log", return_value=""), \
                mock.patch.object(dashboard, "review_repo", return_value=""), \
                mock.patch.object(dashboard, "review_git_context", return_value={}), \
                mock.patch.object(dashboard, "review_brief", return_value="brief"), \
                mock.patch.object(dashboard.chatroom, "patch_participant", side_effect=patch):
            with self.assertRaises(PermissionError):
                self.handler._start_review(rid, "codex", msg)
            self.assertEqual(self.killed, ["pty-v1"])
            self.handler._start_review(rid, "codex", msg)
            self.assertEqual([x["id"] for x in self.ptys], ["pty-v2"])
            self.assertEqual(chatroom.participant(chatroom.get_room(rid, public=False),
                                                  "codex")["ptyId"], "pty-v2")
            # A live terminal of the seat that no one records refuses a launch.
            self.dead.add("gone")
            chatroom.patch_participant(rid, "codex", {"ptyId": "gone"})
            with self.assertRaises(dashboard.StartRoomError):
                self.handler._start_review(rid, "codex", msg)
            self.assertEqual(n["calls"], 2)

    def test_detector_flags_an_unrecorded_terminal(self):
        project, _ = self.po()
        rid = project["poRoomId"]
        self.pty(rid, "stray", ident="po")
        chatroom.patch_participant(rid, "po", {"ptyId": "gone-pty"})
        hit = attention._duplicate_ptys(self.saved(project), time.time())
        self.assertEqual(hit[2]["ptyIds"], ["stray"])


class TokenRotationGuardTests(unittest.TestCase):
    """rotation._rotate_marked (a PO's or owner's token rotation) under the
    same one-terminal discipline as a start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.ptys, self.killed, self.launches = [], [], []

        def launch(room, part, task, collab=True, prompt=None, cwd=None):
            pid = f"pty-new{len(self.launches) + 1}"
            self.launches.append(pid)
            self.ptys.append({"id": pid, "alive": True,
                              "meta": {"room": room["id"], "identity": "claude"}})
            return {"ptyId": pid, "cwd": cwd or str(base), "sessionId": "sid-new"}

        def kill(pid):
            self.killed.append(pid)
            self.ptys = [x for x in self.ptys if x["id"] != pid]

        for p in (mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
                  mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
                  mock.patch.object(dashboard, "hub_launcher",
                                    lambda: SimpleNamespace(_launch_room_agent_pty=launch)),
                  mock.patch.object(dashboard.ptyrun, "list_sessions",
                                    side_effect=lambda: [dict(x) for x in self.ptys]),
                  mock.patch.object(dashboard.ptyrun, "kill", side_effect=kill),
                  mock.patch.object(rotation, "_await_death"),
                  mock.patch.object(rotation, "_discard_fresh",
                                    side_effect=lambda info, *a: kill(info["ptyId"])),
                  mock.patch.object(rotation, "_log")):
            p.start()
            self.addCleanup(p.stop)
        self.base = base

    def rotate(self, orphan=False):
        rid = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude"}])["id"]
        full = chatroom.get_room(rid, public=False)
        full["cwd"], full["mode"] = str(self.base), "solo"
        part = chatroom.agent_participants(full)[0]
        part.update(sessionId="sid-old", cwd=str(self.base), ptyId="pty-old")
        chatroom.update_room(full)
        for pid in ["pty-old"] + (["orphan"] if orphan else []):
            self.ptys.append({"id": pid, "alive": True,
                              "meta": {"room": rid, "identity": "claude"}})
        project = {"id": "p1", "name": "Engine", "path": str(self.base)}
        s = {"kind": "po", "name": "Engine", "project": project, "room": full, "part": part,
             "why": "", "state": {"phase": "asked", "tokensAtAsk": 250_000, "handoverAtAsk": 0},
             "limit": 150_000, "setting": "poRotateTokens", "who": "the PO",
             "whose": "the PO's", "handoverName": rotation.HANDOVER_NAME,
             "handover": self.base / rotation.HANDOVER_NAME, "ids": {"projectId": "p1"}}
        out = rotation._rotate_marked(s, {"tokens": 250_000},
                                      lambda r, quiet=False, **x: {"result": r, **x},
                                      True, True, (rid, "claude"), {"stopped": False})
        return rid, out

    def test_an_orphan_of_the_seat_stops_the_rotation(self):
        rid, out = self.rotate(orphan=True)
        self.assertIn("orphan", out["result"])
        self.assertEqual((self.killed, self.launches), ([], []))
        part = chatroom.agent_participants(chatroom.get_room(rid, public=False))[0]
        self.assertEqual(part["ptyId"], "pty-old")

    def test_a_failed_write_ends_the_fresh_terminal(self):
        with mock.patch.object(chatroom, "patch_participant",
                               side_effect=PermissionError(13, "Access is denied")), \
                self.assertRaises(PermissionError):
            self.rotate()
        self.assertEqual(sorted(set(self.killed)), ["pty-new1", "pty-old"])
        self.assertEqual(self.ptys, [])

    def test_a_clean_rotation_leaves_one_terminal(self):
        rid, out = self.rotate()
        self.assertIn("rotated at 250k", out["result"])
        self.assertEqual([x["id"] for x in self.ptys], ["pty-new1"])


class RoomWriteTests(unittest.TestCase):
    def test_a_briefly_locked_room_file_is_retried(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(chatroom, "ROOMS_DIR", Path(d)), \
                mock.patch.object(chatroom.time, "sleep"):
            real = Path.replace
            fails = {"n": 2}
            def flaky(self, target):
                if fails["n"]:
                    fails["n"] -= 1
                    raise PermissionError(13, "Access is denied")
                return real(self, target)
            with mock.patch.object(Path, "replace", flaky):
                chatroom._write({"id": "room-x", "v": 1})
            self.assertEqual(json.loads((Path(d) / "room-x.json").read_text())["v"], 1)
            fails["n"] = 99
            with mock.patch.object(Path, "replace", flaky), self.assertRaises(PermissionError):
                chatroom._write({"id": "room-x", "v": 2})


class SwitchEndpointTests(_Switch):
    def request(self, method, path, body=None, headers=None, page=True):
        h = dashboard.Handler.__new__(dashboard.Handler)
        raw = json.dumps(body or {}).encode()
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json",
                     "Content-Length": str(len(raw))}
        if page:
            h.headers.update({"Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}",
                              "Origin": f"http://127.0.0.1:{PORT}"})
        h.headers.update(headers or {})
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    def setUp(self):
        super().setUp()
        self.project, _ = self.po()
        for p in [
            mock.patch.object(dashboard, "find_project",
                              side_effect=lambda pid: self.project if pid == "p1" else None),
            mock.patch.object(dashboard, "load_projects", return_value=[self.project]),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
        ]:
            p.start()
            self.patches.append(p)

    def test_the_page_switches_and_reads_the_state(self):
        status, info = self.request("GET", "/api/po/switch?projectId=p1", page=False)
        self.assertEqual(status, 200)
        self.assertEqual((info["agent"], info["targets"]["codex"]["available"],
                          info["targets"]["claude"]["available"]), ("claude", True, False))
        status, out = self.request("POST", "/api/po/switch", {"projectId": "p1", "agent": "codex"})
        self.assertEqual((status, out["started"]), (202, True))
        self.assertEqual(chatroom.participant(self.saved(self.project), "po")["agent"], "codex")
        status, out = self.request("POST", "/api/po/switch", {"projectId": "p1", "agent": "codex"})
        self.assertEqual((status, out["error"]), (409, "same_kind"))

    def test_a_foreign_origin_or_no_page_is_refused(self):
        status, out = self.request("POST", "/api/po/switch", {"projectId": "p1", "agent": "codex"},
                                   headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/po/switch", {"projectId": "p1", "agent": "codex"},
                                 page=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.launcher.launched, [])

    def test_a_po_cannot_switch_its_own_seat(self):
        token = next(t for t, who in self.saved(self.project)["tokens"].items() if who == "po")
        with mock.patch.object(dashboard, "may_restart_hub", return_value=True):
            status, out = self.request("POST", "/api/po/switch",
                                       {"projectId": "p1", "agent": "codex"},
                                       headers={"Authorization": f"Bearer {token}"}, page=False)
        self.assertEqual(status, 403)
        self.assertIn("own seat", out["message"])
        self.assertEqual(self.launcher.launched, [])


if __name__ == "__main__":
    unittest.main()
