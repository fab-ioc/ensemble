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
    def __init__(self, kind: str, installed: bool = True):
        self.display_name = kind.title()
        self._installed = installed

    def installed(self) -> bool:
        return self._installed


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


class RotateOwnerTests(_Base):
    def setUp(self):
        super().setUp()
        self.launcher = _FakeLauncher()
        self.alive = True
        for p in [
            mock.patch.object(dashboard, "hub_launcher", side_effect=lambda: self.launcher),
            mock.patch.object(dashboard.ptyrun, "kill"),
            mock.patch.object(rotation, "_await_death"),
            mock.patch.object(rotation, "_launch_alive", side_effect=lambda pid: self.alive),
            mock.patch.object(rotation, "_await_codex_session", return_value="codex-sid-new"),
            mock.patch.object(dashboard, "delete_session"),
            mock.patch.object(dashboard, "find_project", return_value=None),
        ]:
            p.start()
            self.patches.append(p)

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

    def test_switches_claude_to_codex_and_moves_the_reviewer(self):
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
        rev = chatroom.participant(saved, "codex")
        self.assertEqual(rev["agent"], "claude")
        self.assertTrue(chatroom.is_on_mention(saved, rev))
        # The first prompt is still the handover prompt, never the spec.
        text = self.launcher.launched[0]["text"]
        self.assertIn(rotation.TASK_HANDOVER_NAME, text)
        self.assertIn("previous session ran on Claude; you run on Codex", text)
        self.assertNotIn("Do the work.", text)
        rec = out["rotation"]
        self.assertEqual((rec["agent"], rec["fromAgent"]), ("codex", "claude"))
        self.assertTrue(rec["allocation"]["changed"])
        self.assertTrue(rec["allocation"]["reviewer"]["moved"])
        # Kept on the task for the panel, beside the first-launch record.
        self.assertEqual(saved["allocation"]["handover"]["reason"],
                         "Owner switched to Codex: Claude 5-hour window at 86%.")
        notice = saved["messages"][-1]["text"]
        self.assertIn("a fresh Codex session (Claude 5-hour window at 86%; its reviewer "
                      "codex now runs on Claude)", notice)

    def test_switches_codex_to_claude(self):
        room = self.room(owner="codex", owner_model="gpt-5", reviewer="claude")
        out = self.rotate(room, _snap(10, 92), ident="codex")
        saved = self.saved(room)
        own = chatroom.participant(saved, "codex")
        self.assertEqual((own["agent"], own["sessionId"]), ("claude", "claude-sid-1"))
        self.assertEqual(own["sessionKinds"], {"old-sid": "codex"})
        self.assertEqual(chatroom.participant(saved, "claude")["agent"], "codex")
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

    def test_a_terminal_that_ends_at_once_falls_back(self):
        self.alive = False
        room = self.room()
        out = self.rotate(room, _snap(86, 20))
        own = chatroom.participant(self.saved(room), "claude")
        self.assertEqual(own["agent"], "claude")
        self.assertEqual(out["rotation"]["allocation"]["switchFailed"],
                         "its terminal ended as it started")

    def test_po_is_told_in_one_line(self):
        po_created = chatroom.create_room(
            "PO", [{"identity": "claude", "agent": "claude", "role": "ProductOwner"}])
        room = self.room()
        room["projectId"] = "p1"
        chatroom.update_room(room)
        with mock.patch.object(dashboard, "find_project",
                               return_value={"id": "p1", "poRoomId": po_created["id"]}):
            self.rotate(self.saved(room), _snap(86, 20))
        line = self.launcher.rings[-1][1]
        self.assertTrue(line.startswith(
            "claude was handed to a fresh Codex session (Claude 5-hour window at 86%; "
            "its reviewer codex now runs on Claude) at 212k tokens"), line)


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


class SessionAgentTests(unittest.TestCase):
    def test_delete_uses_each_session_s_own_kind(self):
        part = {"kind": "agent", "agent": "codex", "sessionId": "new",
                "rotations": [{"fromSessionId": "old"}], "sessionKinds": {"old": "claude"}}
        self.assertEqual([(s, dashboard.session_agent(part, s))
                          for s in dashboard.participant_session_ids(part)],
                         [("new", "codex"), ("old", "claude")])


if __name__ == "__main__":
    unittest.main()
