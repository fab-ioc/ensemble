"""The fresh owner's kind at a handover (rotation.choose_owner_kind and the
owner branch of rotation._rotate_marked)."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import rotation


class _FakeAgent:
    deleted: list = []      # Codex rollouts deleted, across instances

    def __init__(self, kind: str, installed: bool = True):
        self.display_name = kind.title()
        self._installed = installed

    def installed(self) -> bool:
        return self._installed

    def delete_session(self, sid):
        _FakeAgent.deleted.append(sid)
        return [sid]


class _FakePty:
    def __init__(self, alive: bool):
        self._alive = alive

    def alive(self) -> bool:
        return self._alive


class _FakeLauncher:
    """Stands in for the hub's Handler: records each launch; ``fail`` names
    kinds whose launch raises."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.launched = []
        self.rings = []

    def _launch_room_agent_pty(self, room, part, task, collab=True, prompt=None, cwd=None):
        self.launched.append({"identity": part["identity"], "agent": part["agent"],
                              "model": part.get("model", ""), "text": prompt or task,
                              "collab": collab})
        if part["agent"] in self.fail:
            raise OSError(f"{part['agent']} would not start")
        n = len(self.launched)
        return {"ptyId": f"pty-{n}", "cwd": cwd or room.get("cwd", ""),
                "sessionId": f"claude-sid-{n}" if part["agent"] == "claude" else ""}

    def _ring_report(self, po_rid, res, rid, title, ident, kind, line):
        self.rings.append((po_rid, line))
        return ["po"]


def _window(kind: str, percent, trusted=True):
    return {"kind": kind, "percent": percent, "trusted": trusted,
            "rolledOver": False, "resetUnknown": False}


def _snap(claude, codex, codex_state="ok"):
    return {"warnPercent": 80, "alarmPercent": 95, "checkedAt": 1.0, "sources": [
        {"source": "claude", "state": "ok", "windows": [_window("five_hour", claude)]},
        {"source": "codex", "state": codex_state, "windows": [_window("five_hour", codex)]},
    ]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_rooms_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.installed = {"claude": True, "codex": True}
        self.patches = [
            mock.patch.object(dashboard.agents, "get_agent", side_effect=lambda k: _FakeAgent(
                k, self.installed.get(k, False)) if k in ("claude", "codex") else None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        chatroom.ROOMS_DIR = self.old_rooms_dir
        self.temp.cleanup()

    def room(self, owner="claude", owner_model="opus", reviewer="codex",
             preference=None):
        members = [{"identity": owner, "agent": owner, "model": owner_model,
                    "role": "engineer"}]
        if reviewer:
            members.append({"identity": reviewer if reviewer != owner else f"{reviewer}-2",
                            "agent": reviewer, "model": "", "role": "reviewer"})
        created = chatroom.create_room("kind test", members)
        room = chatroom.get_room(created["id"], public=False)
        room.update({"mode": "collab" if reviewer else "solo", "launched": True,
                     "spec": "Do the work.", "cwd": self.temp.name})
        if preference is not None:
            room["agentPreference"] = preference
        own = chatroom.participant(room, owner)
        own.update(sessionId="old-sid", ptyId="old-pty", cwd=self.temp.name)
        chatroom.update_room(room)
        return chatroom.get_room(room["id"], public=False)


class ChooseOwnerKindTests(_Base):
    def choose(self, room, snap, **kw):
        part = chatroom.agent_participants(room)[0]
        return rotation.choose_owner_kind(room, part, snapshot=snap, **kw)

    def test_stays_below_warning(self):
        c = self.choose(self.room(), _snap(60, 10))
        self.assertFalse(c["changed"])
        self.assertEqual((c["agent"], c["model"]), ("claude", "opus"))
        self.assertIn("below the 80% warning", c["reason"])

    def test_switches_claude_to_codex_with_alt_model(self):
        pref = [{"agent": "claude", "model": "opus", "role": "engineer",
                 "alt": {"agent": "codex", "model": "gpt-owner"}},
                {"agent": "codex", "model": "", "role": "reviewer"}]
        c = self.choose(self.room(preference=pref), _snap(86, 20))
        self.assertTrue(c["changed"])
        self.assertEqual((c["agent"], c["model"]), ("codex", "gpt-owner"))
        self.assertEqual(c["reason"], "Owner switched to Codex: Claude 5-hour window at 86%.")

    def test_switches_codex_to_claude_default_model_without_preference(self):
        room = self.room(owner="codex", owner_model="gpt-5", reviewer="claude")
        c = self.choose(room, _snap(10, 91))
        self.assertTrue(c["changed"])
        self.assertEqual((c["agent"], c["model"]), ("claude", ""))

    def test_switch_back_takes_the_preferred_model(self):
        # Preferred Claude/opus, swapped to Codex at first launch: back on
        # Claude it runs opus again.
        pref = [{"agent": "claude", "model": "opus", "role": "engineer"}]
        room = self.room(owner="codex", owner_model="", reviewer=None, preference=pref)
        c = self.choose(room, _snap(5, 85))
        self.assertEqual((c["agent"], c["model"]), ("claude", "opus"))

    def test_unknown_reading_stays(self):
        c = self.choose(self.room(), _snap(90, 10, codex_state="error"))
        self.assertFalse(c["changed"])
        self.assertIn("unavailable", c["reason"])

    def test_both_past_warning_stays(self):
        c = self.choose(self.room(), _snap(88, 84))
        self.assertFalse(c["changed"])
        self.assertFalse(c["alarm"])

    def test_both_past_alarm_stays_and_notes_the_alarm(self):
        c = self.choose(self.room(), _snap(96, 99))
        self.assertFalse(c["changed"])
        self.assertTrue(c["alarm"])

    def test_other_kind_not_installed_stays(self):
        self.installed["codex"] = False
        c = self.choose(self.room(), _snap(90, 10))
        self.assertFalse(c["changed"])
        self.assertIn("not installed", c["reason"])

    def test_a_failing_check_stays_and_never_raises(self):
        c = self.choose(self.room(), _snap("not-a-number", 10))
        self.assertFalse(c["changed"])
        self.assertIn("allowance check failed", c["reason"])
        with mock.patch.object(dashboard.usage, "snapshot", side_effect=OSError("cache")):
            part = chatroom.agent_participants(self.room())[0]
            c = rotation.choose_owner_kind({}, part)
        self.assertFalse(c["changed"])
        self.assertEqual(c["usage"]["error"], "OSError")

    def test_a_human_picked_one_agent_task_keeps_its_kind(self):
        room = self.room(reviewer=None,
                         preference=[{"agent": "claude", "model": "opus", "role": "engineer"}])
        room["lineupPickedByHuman"] = True
        c = self.choose(room, _snap(90, 10))
        self.assertFalse(c["changed"])
        self.assertIn("kept as picked", c["reason"])
        room["lineupPickedByHuman"] = False
        self.assertTrue(self.choose(room, _snap(90, 10))["changed"])


class RotateOwnerTests(_Base):
    def setUp(self):
        super().setUp()
        self.launcher = _FakeLauncher()
        self.dead = set()       # terminals that ended as they started
        self.spawned = []       # the switch watches, run inline unless held
        self.hold_watch = False
        _FakeAgent.deleted = []
        self.delete_session = mock.patch.object(dashboard, "delete_session").start()
        for p in [
            mock.patch.object(dashboard, "hub_launcher", side_effect=lambda: self.launcher),
            mock.patch.object(dashboard.ptyrun, "kill"),
            mock.patch.object(dashboard.ptyrun, "get", side_effect=lambda pid: _FakePty(
                pid not in self.dead) if pid else None),
            mock.patch.object(rotation, "_await_death"),
            mock.patch.object(rotation, "_LAUNCH_SETTLE_S", 0.0),
            mock.patch.object(rotation, "_spawn", side_effect=self.spawn),
            mock.patch.object(rotation, "_await_codex_session", return_value="codex-sid-new"),
            mock.patch.object(dashboard, "find_project", return_value=None),
        ]:
            p.start()
            self.patches.append(p)
        self.patches.append(self.delete_session)

    def spawn(self, fn, *args):
        self.spawned.append((fn, args))
        if not self.hold_watch:
            fn(*args)

    def tearDown(self):
        self.assertFalse(rotation._ROTATING)
        self.assertFalse(rotation._WATCHING)
        super().tearDown()

    def subject(self, room, ident="claude"):
        return {"kind": "owner", "name": f"t / {ident}", "project": None, "room": room,
                "part": chatroom.participant(room, ident), "why": "",
                "state": {"phase": "asked", "tokensAtAsk": 212_000, "handoverAtAsk": 0},
                "limit": 200_000, "setting": "taskRotateTokens",
                "who": f"the owner ({ident})", "whose": f"{ident}'s",
                "handoverName": rotation.TASK_HANDOVER_NAME,
                "handover": Path(self.temp.name) / rotation.TASK_HANDOVER_NAME,
                "ids": {"roomId": room["id"], "identity": ident}}

    def rotate(self, room, snap, ident="claude"):
        s = self.subject(room, ident)
        key = (room["id"], ident)
        with mock.patch.object(dashboard.usage, "snapshot", return_value=snap):
            return rotation._rotate_marked(
                s, {"tokens": 212_000}, lambda r, quiet=False, **x: {"result": r, **x},
                True, True, key, {"stopped": False})

    def saved(self, room):
        return chatroom.get_room(room["id"], public=False)

    def test_stays_on_the_same_kind(self):
        room = self.room()
        out = self.rotate(room, _snap(40, 10))
        own = chatroom.participant(self.saved(room), "claude")
        self.assertEqual((own["agent"], own["model"]), ("claude", "opus"))
        self.assertEqual(own["sessionId"], "claude-sid-1")
        self.assertNotIn("sessionKinds", own)
        rec = out["rotation"]
        self.assertFalse(rec["allocation"]["changed"])
        self.assertEqual((rec["agent"], rec["fromAgent"]), ("claude", "claude"))
        self.assertNotIn("previous session ran on", self.launcher.launched[0]["text"])

    def test_switches_claude_to_codex(self):
        room = self.room()      # claude engineer, codex reviewer on mention
        out = self.rotate(room, _snap(86, 20))
        saved = self.saved(room)
        own = chatroom.participant(saved, "claude")
        # Same identity and token; the new kind, its default model, its id.
        self.assertEqual((own["agent"], own["model"]), ("codex", ""))
        self.assertEqual(own["sessionId"], "codex-sid-new")
        self.assertIn("claude", saved["tokens"].values())
        self.assertEqual(own["sessionKinds"], {"old-sid": "claude"})
        self.assertEqual(dashboard.session_agent(own, "old-sid"), "claude")
        self.assertEqual(dashboard.session_agent(own, "codex-sid-new"), "codex")
        # The reviewer is left to its next review's own choice.
        self.assertEqual(chatroom.participant(saved, "codex")["agent"], "codex")
        # The first prompt is still the handover prompt, never the spec.
        text = self.launcher.launched[0]["text"]
        self.assertIn(rotation.TASK_HANDOVER_NAME, text)
        self.assertIn("previous session ran on Claude; you run on Codex", text)
        self.assertNotIn("Do the work.", text)
        rec = out["rotation"]
        self.assertEqual((rec["agent"], rec["fromAgent"]), ("codex", "claude"))
        self.assertTrue(rec["allocation"]["changed"])
        # Kept on the task for the panel, beside the first-launch record.
        self.assertEqual(saved["allocation"]["handover"]["reason"],
                         "Owner switched to Codex: Claude 5-hour window at 86%.")
        notice = saved["messages"][-1]["text"]
        self.assertIn("a fresh Codex session (Claude 5-hour window at 86%)", notice)

    def test_switches_codex_to_claude(self):
        room = self.room(owner="codex", owner_model="gpt-5", reviewer="claude")
        out = self.rotate(room, _snap(10, 92), ident="codex")
        saved = self.saved(room)
        own = chatroom.participant(saved, "codex")
        self.assertEqual((own["agent"], own["sessionId"]), ("claude", "claude-sid-1"))
        self.assertEqual(own["sessionKinds"], {"old-sid": "codex"})
        self.assertEqual(out["rotation"]["toSessionId"], "claude-sid-1")

    def test_reviewer_of_the_other_kind_is_left_alone(self):
        room = self.room(owner="claude", reviewer="claude")   # claude + claude-2 reviewer
        self.rotate(room, _snap(86, 20))
        saved = self.saved(room)
        self.assertEqual(chatroom.participant(saved, "claude")["agent"], "codex")
        self.assertEqual(chatroom.participant(saved, "claude-2")["agent"], "claude")

    def test_unknown_reading_stays(self):
        room = self.room()
        out = self.rotate(room, _snap(90, 10, codex_state="error"))
        self.assertEqual(chatroom.participant(self.saved(room), "claude")["agent"], "claude")
        self.assertFalse(out["rotation"]["allocation"]["changed"])

    def test_launch_failure_falls_back_and_the_handover_completes(self):
        self.launcher.fail = {"codex"}
        room = self.room()
        out = self.rotate(room, _snap(86, 20))
        saved = self.saved(room)
        own = chatroom.participant(saved, "claude")
        self.assertEqual([l["agent"] for l in self.launcher.launched], ["codex", "claude"])
        self.assertEqual((own["agent"], own["model"], own["sessionId"]),
                         ("claude", "opus", "claude-sid-2"))
        self.assertEqual(chatroom.participant(saved, "codex")["agent"], "codex")
        self.assertIn("rotated at", out["result"])
        alloc = out["rotation"]["allocation"]
        self.assertFalse(alloc["changed"])
        self.assertIn("codex would not start", alloc["switchFailed"])
        self.assertNotIn("previous session ran on", self.launcher.launched[1]["text"])
        self.assertIn("switching to Codex failed", saved["messages"][-1]["text"])

    def test_a_codex_terminal_that_ends_at_once_falls_back_after_the_handover(self):
        self.dead = {"pty-1"}
        room = self.room()
        out = self.rotate(room, _snap(86, 20))
        # The handover completed as Codex; the watch then undid it.
        self.assertEqual(out["rotation"]["agent"], "codex")
        self.assertEqual([l["agent"] for l in self.launcher.launched], ["codex", "claude"])
        saved = self.saved(room)
        own = chatroom.participant(saved, "claude")
        self.assertEqual((own["agent"], own["model"], own["sessionId"], own["ptyId"]),
                         ("claude", "opus", "claude-sid-2", "pty-2"))
        self.assertNotIn("sessionKinds", own)
        # The Codex rollout it had already written is deleted, not orphaned.
        self.assertEqual(_FakeAgent.deleted, ["codex-sid-new"])
        self.assertEqual(dashboard.participant_session_ids(own), ["claude-sid-2", "old-sid"])
        rot = own["rotations"][-1]
        self.assertEqual((rot["agent"], rot["toSessionId"]), ("claude", "claude-sid-2"))
        self.assertEqual(rot["allocation"]["switchFailed"], "its terminal ended as it started")
        self.assertFalse(rot["allocation"]["changed"])
        self.assertEqual(saved["allocation"]["handover"]["switchFailed"],
                         "its terminal ended as it started")
        self.assertIn("**claude's Codex session ended as it started** — the hub started a "
                      "fresh Claude session (switching to Codex failed",
                      saved["messages"][-1]["text"])
        self.assertNotIn("previous session ran on", self.launcher.launched[1]["text"])

    def test_a_claude_terminal_that_ends_at_once_falls_back_to_codex(self):
        self.dead = {"pty-1"}
        room = self.room(owner="codex", owner_model="gpt-5", reviewer="claude")
        self.rotate(room, _snap(10, 92), ident="codex")
        own = chatroom.participant(self.saved(room), "codex")
        self.assertEqual((own["agent"], own["model"], own["sessionId"]),
                         ("codex", "gpt-5", "codex-sid-new"))
        self.delete_session.assert_called_once_with("claude-sid-1")

    def test_a_failed_cleanup_still_falls_back(self):
        self.dead = {"pty-1"}
        room = self.room()
        with mock.patch.object(_FakeAgent, "delete_session", side_effect=OSError("locked")):
            self.rotate(room, _snap(86, 20))
        own = chatroom.participant(self.saved(room), "claude")
        self.assertEqual((own["agent"], own["ptyId"]), ("claude", "pty-2"))

    def test_the_po_line_names_the_kind_after_a_fallback(self):
        self.dead = {"pty-1"}
        po_created = chatroom.create_room(
            "PO", [{"identity": "claude", "agent": "claude", "role": "ProductOwner"}])
        room = self.room()
        room["projectId"] = "p1"
        chatroom.update_room(room)
        with mock.patch.object(dashboard, "find_project",
                               return_value={"id": "p1", "poRoomId": po_created["id"]}):
            self.rotate(self.saved(room), _snap(86, 20))
        self.assertEqual(self.launcher.rings, [], "a rotation does not wake the PO")
        line = chatroom.get_room(po_created["id"], public=False)["messages"][-1]["text"]
        self.assertIn(
            "claude was handed to a fresh Claude session (switching to Codex failed: "
            "its terminal ended as it started)", line)
        self.assertTrue(line.split("\n\n", 1)[1].startswith(
            "claude was handed to a fresh Claude session (switching to Codex failed: "
            "its terminal ended as it started)"), line)

    def test_a_live_switch_is_left_alone(self):
        room = self.room()
        self.rotate(room, _snap(86, 20))
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(len(self.launcher.launched), 1)
        self.assertEqual(chatroom.participant(self.saved(room), "claude")["agent"], "codex")

    def test_a_stop_during_the_watch_is_not_taken_for_a_failed_start(self):
        self.hold_watch = True
        room = self.room()
        self.rotate(room, _snap(86, 20))
        rotation.note_stopped(room["id"])
        self.dead = {"pty-1"}           # the Stop ended it
        fn, args = self.spawned[0]
        fn(*args)
        self.assertEqual(len(self.launcher.launched), 1)
        self.assertEqual(chatroom.participant(self.saved(room), "claude")["agent"], "codex")
        self.assertEqual(_FakeAgent.deleted, [])

    def test_the_next_review_runs_on_the_kind_the_owner_left(self):
        # main's per-review choice takes the kind other than the owner's
        # current one; the reviewer's earlier session keeps its own kind.
        room = self.room()
        rev = chatroom.participant(room, "codex")
        rev.update(sessionId="rev-sid")
        chatroom.update_room(room)
        self.rotate(self.saved(room), _snap(86, 20))
        with mock.patch.object(dashboard.usage, "snapshot", return_value=_snap(50, 20)):
            _, part, alloc = dashboard.apply_review_allocation(self.saved(room), "codex")
        self.assertTrue(alloc["changed"])
        self.assertEqual(part["agent"], "claude")
        rev = chatroom.participant(self.saved(room), "codex")
        self.assertEqual((rev["agent"], rev["sessionKinds"]), ("claude", {"rev-sid": "codex"}))
        self.assertEqual(dashboard.session_agent(rev, "rev-sid"), "codex")

    def test_po_is_told_in_one_line(self):
        po_created = chatroom.create_room(
            "PO", [{"identity": "claude", "agent": "claude", "role": "ProductOwner"}])
        room = self.room()
        room["projectId"] = "p1"
        chatroom.update_room(room)
        with mock.patch.object(dashboard, "find_project",
                               return_value={"id": "p1", "poRoomId": po_created["id"]}):
            self.rotate(self.saved(room), _snap(86, 20))
        self.assertEqual(self.launcher.rings, [], "a rotation does not wake the PO")
        line = chatroom.get_room(po_created["id"], public=False)["messages"][-1]["text"]
        line = line.split("\n\n", 1)[1]
        self.assertTrue(line.startswith(
            "claude was handed to a fresh Codex session (Claude 5-hour window at 86%) "
            "at 212k tokens"), line)


class PoRotationUnchangedTests(_Base):
    def test_po_rotation_never_reads_the_allowance(self):
        launcher = _FakeLauncher()
        created = chatroom.create_room(
            "PO", [{"identity": "claude", "agent": "claude", "model": "opus",
                    "role": "ProductOwner"}])
        room = chatroom.get_room(created["id"], public=False)
        room.update(mode="solo", launched=True, spec="Be the PO.", cwd=self.temp.name)
        room["participants"][0].update(sessionId="old-sid", ptyId="old-pty")
        chatroom.update_room(room)
        project = {"id": "p1", "name": "P", "poRoomId": room["id"]}
        s = {"kind": "po", "name": "P", "project": project, "room": room,
             "part": chatroom.participant(room, "claude"), "why": "",
             "state": {"phase": "asked", "tokensAtAsk": 300_000, "handoverAtAsk": 0},
             "limit": 200_000, "setting": "poRotateTokens", "who": "the PO",
             "whose": "the PO's", "handoverName": rotation.HANDOVER_NAME,
             "handover": Path(self.temp.name) / rotation.HANDOVER_NAME,
             "ids": {"projectId": "p1"}}
        with mock.patch.object(dashboard, "hub_launcher", return_value=launcher), \
                mock.patch.object(dashboard.ptyrun, "kill"), \
                mock.patch.object(rotation, "_await_death"), \
                mock.patch.object(dashboard, "roadmap_path",
                                  return_value=Path(self.temp.name) / "ROADMAP.md"), \
                mock.patch.object(dashboard.usage, "snapshot",
                                  side_effect=AssertionError("PO read the allowance")):
            out = rotation._rotate_marked(
                s, {"tokens": 300_000}, lambda r, quiet=False, **x: {"result": r, **x},
                True, True, (room["id"], "claude"), {"stopped": False})
        part = chatroom.participant(chatroom.get_room(room["id"], public=False), "claude")
        self.assertEqual((part["agent"], part["model"]), ("claude", "opus"))
        self.assertEqual([l["agent"] for l in launcher.launched], ["claude"])
        self.assertNotIn("allocation", out["rotation"])
        self.assertNotIn("fromAgent", out["rotation"])

    def test_codex_po_rotates_from_handover_in_same_room(self):
        launcher = _FakeLauncher()
        created = chatroom.create_room(
            "PO", [{"identity": "po", "agent": "codex", "model": "gpt-5.6-sol",
                    "role": "ProductOwner"}])
        room = chatroom.get_room(created["id"], public=False)
        room.update(mode="solo", launched=True, spec="Be the PO.", cwd=self.temp.name)
        room["participants"][0].update(sessionId="old-sid", ptyId="old-pty")
        chatroom.update_room(room)
        project = {"id": "p1", "name": "P", "poRoomId": room["id"]}
        self.assertEqual(rotation._po(project)[1]["agent"], "codex")
        s = {"kind": "po", "name": "P", "project": project, "room": room,
             "part": chatroom.participant(room, "po"), "why": "",
             "state": {"phase": "asked", "tokensAtAsk": 300_000, "handoverAtAsk": 0},
             "limit": 200_000, "setting": "poRotateTokens", "who": "the PO",
             "whose": "the PO's", "handoverName": rotation.HANDOVER_NAME,
             "handover": Path(self.temp.name) / rotation.HANDOVER_NAME,
             "ids": {"projectId": "p1"}}
        with mock.patch.object(dashboard, "hub_launcher", return_value=launcher), \
                mock.patch.object(dashboard.ptyrun, "kill"), \
                mock.patch.object(rotation, "_await_death"), \
                mock.patch.object(rotation, "_await_codex_session", return_value="codex-new"), \
                mock.patch.object(dashboard, "roadmap_path",
                                  return_value=Path(self.temp.name) / "ROADMAP.md"):
            rotation._rotate_marked(
                s, {"tokens": 300_000}, lambda r, quiet=False, **x: {"result": r, **x},
                True, True, (room["id"], "po"), {"stopped": False})
        saved = chatroom.get_room(room["id"], public=False)
        part = chatroom.participant(saved, "po")
        self.assertEqual((part["agent"], part["model"], part["sessionId"]),
                         ("codex", "gpt-5.6-sol", "codex-new"))
        self.assertEqual(part["rotations"][-1]["toSessionId"], "codex-new")
        self.assertEqual(chatroom.po_identity(saved), "po")


class PoUsageFailoverTests(_Base):
    def setUp(self):
        super().setUp()
        self.launcher = _FakeLauncher()
        self.dead = set()
        self.killed = []
        self.snap = _snap(100, 20)
        for p in [
            mock.patch.object(dashboard, "hub_launcher", return_value=self.launcher),
            mock.patch.object(dashboard, "project_home", return_value=self.temp.name),
            mock.patch.object(dashboard, "roadmap_path",
                              return_value=Path(self.temp.name) / "ROADMAP.md"),
            mock.patch.object(dashboard.ptyrun, "get", side_effect=lambda pid: _FakePty(
                pid not in self.dead) if pid else None),
            mock.patch.object(dashboard.ptyrun, "kill", side_effect=self.killed.append),
            mock.patch.object(rotation, "_await_death"),
            mock.patch.object(rotation, "_await_codex_session", return_value="codex-new"),
            mock.patch.object(rotation, "_LAUNCH_SETTLE_S", 0),
            mock.patch.object(dashboard.usage, "snapshot", side_effect=lambda: self.snap),
        ]:
            p.start()
            self.patches.append(p)
        rotation._STATE.clear()

    def po(self, kind="claude", model="opus", handover=True, preference=None):
        made = chatroom.create_room("PO", [{"identity": "po", "agent": kind,
                                            "model": model, "role": "ProductOwner"}])
        room = chatroom.get_room(made["id"], public=False)
        room.update(mode="solo", launched=True, status="active", projectId="p1",
                    spec="Start this project from scratch.", cwd=self.temp.name)
        if preference:
            room["agentPreference"] = preference
        room["participants"][0].update(sessionId="old-sid", ptyId="old-pty",
                                        cwd=self.temp.name)
        chatroom.update_room(room)
        if handover:
            (Path(self.temp.name) / "PO-HANDOVER.md").write_text("Carry on #1.", encoding="utf-8")
        (Path(self.temp.name) / "ROADMAP.md").write_text("Next milestone.", encoding="utf-8")
        project = {"id": "p1", "name": "Project", "poRoomId": made["id"]}
        item = {"roomId": made["id"], "state": "blocked", "cause": "usage_limit",
                "agentIdentity": "po", "sessionId": "old-sid"}
        return project, item

    def saved(self, project):
        return chatroom.get_room(project["poRoomId"], public=False)

    def test_claude_failover_preserves_room_chat_token_points_and_routing(self):
        project, item = self.po(preference=[{"agent": "claude", "model": "opus",
                                             "role": "ProductOwner",
                                             "alt": {"agent": "codex", "model": "gpt-custom"}}])
        original = self.saved(project)
        chatroom.post_notice(project["poRoomId"], "human", "Existing chat", {})
        with mock.patch.object(dashboard.points, "prompt_block", return_value="Open point P1"):
            result = rotation.failover_po(project, item)
        saved = self.saved(project)
        part = chatroom.participant(saved, "po")
        self.assertEqual(result["result"], "switched")
        self.assertEqual((part["agent"], part["model"], part["sessionId"]),
                         ("codex", "gpt-custom", "codex-new"))
        self.assertEqual((saved["id"], saved["projectId"], saved["tokens"]),
                         (original["id"], "p1", original["tokens"]))
        self.assertEqual(project["poRoomId"], saved["id"])
        self.assertEqual(chatroom.po_identity(saved), "po")
        self.assertTrue(any(m["text"] == "Existing chat" for m in saved["messages"]))
        self.assertEqual(part["sessionKinds"], {"old-sid": "claude"})
        self.assertEqual(part["rotations"][-1]["fromSessionId"], "old-sid")
        self.assertEqual(saved["poFailover"]["cause"], "usage_limit")
        self.assertEqual(self.killed, ["old-pty"])
        self.assertIn("Open point P1", self.launcher.launched[0]["text"])
        self.assertIn("PO-HANDOVER.md", self.launcher.launched[0]["text"])
        self.assertIn("ROADMAP.md", self.launcher.launched[0]["text"])
        self.assertIn("do not restart", self.launcher.launched[0]["text"])
        self.assertIn("gpt-custom", saved["messages"][-1]["text"])

    def test_codex_to_claude_uses_preference_and_never_switches_back(self):
        self.snap = _snap(15, 100)
        project, item = self.po("codex", "gpt-5.6-sol", preference=[
            {"agent": "codex", "model": "gpt-5.6-sol", "role": "ProductOwner",
             "alt": {"agent": "claude", "model": "sonnet"}}])
        self.assertEqual(rotation.failover_po(project, item)["result"], "switched")
        part = chatroom.participant(self.saved(project), "po")
        self.assertEqual((part["agent"], part["model"]), ("claude", "sonnet"))
        self.assertEqual(part["sessionKinds"], {"old-sid": "codex"})
        again = {**item, "sessionId": part["sessionId"]}
        self.snap = _snap(100, 15)
        self.assertEqual(rotation.failover_po(project, again)["result"], "already handled")
        self.assertEqual(len(self.launcher.launched), 1)

    def test_default_codex_fallback_model(self):
        project, item = self.po()
        rotation.failover_po(project, item)
        self.assertEqual(self.launcher.launched[0]["model"], "gpt-5.6-sol")

    def test_configured_fallback_model(self):
        project, item = self.po()
        with mock.patch.object(dashboard, "load_settings", return_value={
                "poFallbackModels": {"codex": "gpt-custom", "claude": "sonnet"}}):
            rotation.failover_po(project, item)
        self.assertEqual(self.launcher.launched[0]["model"], "gpt-custom")

    def test_over_warning_and_unknown_allowance_alert_once(self):
        stale = _snap(100, 10)
        stale["sources"][1]["windows"][0]["trusted"] = False
        mixed = _snap(100, 20)
        mixed["sources"][1]["windows"].append(_window("seven_day", 10, trusted=False))
        for snap in (_snap(100, 80), _snap(100, 10, codex_state="error"), stale, mixed):
            with self.subTest(snap=snap):
                self.snap = snap
                project, item = self.po()
                for _ in range(2):
                    rotation.failover_po(project, item)
                saved = self.saved(project)
                self.assertEqual(len([m for m in saved["messages"]
                                      if m.get("noticeKind") == "poFailoverAlert"]), 1)
                self.assertEqual(chatroom.participant(saved, "po")["agent"], "claude")
                self.assertEqual(self.launcher.launched, [])

    def test_missing_handover_blocks_then_existing_handover_allows_switch(self):
        project, item = self.po(handover=False)
        for _ in range(2):
            rotation.failover_po(project, item)
        self.assertEqual(len(self.saved(project)["messages"]), 1)
        (Path(self.temp.name) / "PO-HANDOVER.md").write_text("Existing state", encoding="utf-8")
        self.assertEqual(rotation.failover_po(project, item)["result"], "switched")

    def test_repeated_polls_and_restart_do_not_repeat_switch(self):
        project, item = self.po()
        with mock.patch.object(dashboard, "load_projects", return_value=[project]), \
                mock.patch.object(dashboard.attention, "snapshot", return_value={"items": [item]}), \
                mock.patch.object(rotation, "threshold", return_value=0), \
                mock.patch.object(rotation, "task_threshold", return_value=0):
            rotation._tick()
            rotation._STATE.clear()  # a hub restart loses this memory
            rotation._tick()
        self.assertEqual(len(self.launcher.launched), 1)
        self.assertEqual(len(chatroom.participant(self.saved(project), "po")["rotations"]), 1)

    def test_attention_failure_does_not_stop_task_owner_checks(self):
        with mock.patch.object(dashboard, "load_projects", return_value=[]), \
                mock.patch.object(dashboard.attention, "snapshot", side_effect=OSError("cache")), \
                mock.patch.object(rotation, "task_threshold", return_value=200_000), \
                mock.patch.object(rotation, "running_owners", return_value=[("room-t", "eng")]), \
                mock.patch.object(rotation, "check_task") as check_task:
            rotation._tick()
        check_task.assert_called_once_with("room-t", "eng")

    def test_failed_start_keeps_old_po_and_alerts_once(self):
        project, item = self.po()
        self.dead.add("pty-1")
        for _ in range(2):
            rotation.failover_po(project, item)
        saved = self.saved(project)
        part = chatroom.participant(saved, "po")
        self.assertEqual((part["agent"], part["sessionId"], part["ptyId"]),
                         ("claude", "old-sid", "old-pty"))
        self.assertNotIn("poFailover", saved)
        self.assertEqual(len(self.launcher.launched), 1)
        self.assertEqual(self.killed, ["pty-1"])
        self.assertEqual(len(saved["messages"]), 1)

    def test_ordinary_block_and_waiting_do_not_trigger(self):
        project, item = self.po()
        self.assertEqual(rotation.failover_po(project, {**item, "cause": "login"})["result"],
                         "no reliable usage-limit block")
        self.assertEqual(rotation.failover_po(project, {**item, "state": "waiting_for_you"})["result"],
                         "no reliable usage-limit block")
        self.assertEqual(self.launcher.launched, [])


class SessionAgentTests(unittest.TestCase):
    def test_delete_uses_each_session_s_own_kind(self):
        part = {"kind": "agent", "agent": "codex", "sessionId": "new",
                "rotations": [{"fromSessionId": "old"}], "sessionKinds": {"old": "claude"}}
        self.assertEqual([(s, dashboard.session_agent(part, s))
                          for s in dashboard.participant_session_ids(part)],
                         [("new", "codex"), ("old", "claude")])


if __name__ == "__main__":
    unittest.main()
