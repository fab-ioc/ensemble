"""Messages between two projects' POs (po_messages.py, ensemble_message_po and
ensemble_read_message): only a PO may write, to another project's PO; the
message is kept whole in both PO chats and the target is typed one line; the
answer goes back the same way; quiet kinds wait, a pair of projects wakes at
most WAKES_PER_HOUR times an hour and the rest is held for the CEO's bell; a
PO that is not running is told when it runs and is idle, never started."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
import po_messages
import rotation

T0 = 1_790_000_000.0


class _FakePty:
    def __init__(self):
        self.typed = []

    def alive(self) -> bool:
        return True

    def send_line(self, text):
        self.typed.append(text)
        return 0


class _World(unittest.TestCase):
    """Three projects: opten and Dock with a running PO each, Lonely without
    one; opten also has a task with an engineer."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        d = Path(self.temp.name)
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = d / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)

        def room(title, pid, role, pty):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude",
                                                "role": role}])["id"]
            chatroom.patch_room(rid, projectId=pid)
            chatroom.patch_participant(rid, "claude", {"ptyId": pty})
            return rid

        self.opten = room("opten PO", "proj-opten", "Product owner", "pty-opten")
        self.dock = room("Dock PO", "proj-dock", "Product owner", "pty-dock")
        self.task = room("A task", "proj-opten", "engineer", "pty-task")
        self.projects = [
            {"id": "proj-opten", "name": "opten", "poRoomId": self.opten},
            {"id": "proj-dock", "name": "Dock", "poRoomId": self.dock},
            {"id": "proj-lonely", "name": "Lonely", "poRoomId": ""},
        ]
        self.ptys = {"pty-opten": _FakePty(), "pty-dock": _FakePty(), "pty-task": _FakePty()}
        self.idle = {"pty-opten": True, "pty-dock": True, "pty-task": True}
        self.now = T0
        po_messages._HELD_CACHE = (0.0, 0.0, {})
        patches = [
            mock.patch.object(dashboard, "DASHBOARD_DIR", d / "state"),
            mock.patch.object(dashboard, "load_projects", side_effect=lambda: [dict(p) for p in self.projects]),
            mock.patch.object(dashboard, "load_session_projects", return_value={}),
            mock.patch.object(dashboard, "load_labels", return_value={}),
            mock.patch.object(dashboard, "operator_name", return_value="fabio"),
            mock.patch.object(dashboard, "may_restart_hub", return_value=False),
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.ptys.get(part.get("ptyId") or "")),
            mock.patch.object(rotation, "_transcript_of", return_value=(None, lambda p: {"turnOver": True})),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle.get(part.get("ptyId"), False)),
            mock.patch.object(rotation, "_submitted_lately", return_value=False),
            mock.patch.object(rotation, "is_rotating", return_value=False),
            mock.patch.object(rotation, "awaiting_handover", return_value=False),
            mock.patch.object(po_messages.time, "time", side_effect=lambda: self.now),
            mock.patch.object(po_messages, "_log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # -- helpers -------------------------------------------------------------

    def call(self, room_id, name, **args):
        text, err = ensemble_tools.call(name, args, room_id, "claude", None)
        return text, err

    def ok(self, room_id, name, **args):
        text, err = self.call(room_id, name, **args)
        self.assertFalse(err, text)
        return json.loads(text)

    def refused(self, room_id, name, **args):
        text, err = self.call(room_id, name, **args)
        self.assertTrue(err, text)
        return text

    def pomsgs(self, rid):
        return [m for m in chatroom.get_room(rid)["messages"] if m.get("kind") == "pomsg"]

    def typed(self, pty):
        return self.ptys[pty].typed

    def queue(self):
        return po_messages._load()["pending"]


class Scope(_World):
    def names(self, rid):
        room = chatroom.get_room(rid, public=False)
        return {t["name"] for t in ensemble_tools.tool_schemas(room, "claude")}

    def test_only_a_po_is_offered_the_tools(self):
        self.assertTrue({"ensemble_message_po", "ensemble_read_message"} <= self.names(self.opten))
        self.assertFalse({"ensemble_message_po", "ensemble_read_message"} & self.names(self.task))

    def test_a_task_is_refused(self):
        err = self.refused(self.task, "ensemble_message_po", projectId="Dock", text="x")
        self.assertIn("only for a project's PO", err)
        self.assertEqual(self.pomsgs(self.dock), [])

    def test_a_project_without_a_po_yourself_and_nobody(self):
        self.assertIn("has no PO", self.refused(self.opten, "ensemble_message_po",
                                                projectId="Lonely", text="x"))
        self.assertIn("your own project", self.refused(self.opten, "ensemble_message_po",
                                                       projectId="proj-opten", text="x"))
        self.assertIn("no project 'Nope'", self.refused(self.opten, "ensemble_message_po",
                                                        projectId="Nope", text="x"))
        self.assertIn("text is required", self.refused(self.opten, "ensemble_message_po",
                                                       projectId="Dock", text="  "))
        self.assertIn("kind must be", self.refused(self.opten, "ensemble_message_po",
                                                   projectId="Dock", text="x", kind="rant"))
        # A PO room that is gone is no PO.
        self.projects[1]["poRoomId"] = "room-gone"
        self.assertIn("has no PO", self.refused(self.opten, "ensemble_message_po",
                                                projectId="Dock", text="x"))

    def test_by_id_or_by_name_in_any_case(self):
        self.assertEqual(self.ok(self.opten, "ensemble_message_po", projectId="proj-dock", text="a")["to"], "Dock PO")
        self.assertEqual(self.ok(self.opten, "ensemble_message_po", projectId="dock", text="b")["to"], "Dock PO")


class Delivery(_World):
    TEXT = ("## The splitter jumps on drop\n\nDragging a panel's splitter and dropping it "
            "moves the panel 40px left.\n\n" + "Steps and logs. " * 800 + "\n\nThe last sentence.")

    def test_stored_whole_in_both_chats_and_one_line_typed(self):
        res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text=self.TEXT)
        self.assertTrue(res["delivered"])
        mid = res["id"]
        got, sent = self.pomsgs(self.dock), self.pomsgs(self.opten)
        self.assertEqual(len(got), 1)
        self.assertEqual(len(sent), 1)
        for m, direction in ((got[0], "received"), (sent[0], "sent")):
            self.assertEqual(m["id"], mid)
            self.assertEqual(m["text"], self.TEXT.strip())     # whole, the last sentence too
            self.assertEqual(m["direction"], direction)
            self.assertEqual((m["fromProjectName"], m["toProjectName"], m["poKind"]), ("opten", "Dock", "bug"))
            self.assertEqual((m["fromProjectId"], m["toProjectId"]), ("proj-opten", "proj-dock"))
            self.assertEqual((m["fromRoomId"], m["toRoomId"]), (self.opten, self.dock))
            self.assertEqual(m["rang"], [])             # nobody is rung by the chat itself
            self.assertNotEqual(m["from"], "user")      # not the CEO's: no points
        self.assertEqual(got[0]["from"], f"claude@{self.opten}")
        self.assertEqual(got[0]["to"], "claude")
        [line] = self.typed("pty-dock")
        self.assertEqual(line, f"[from the opten PO] bug: The splitter jumps on drop — read it in full "
                               f"with ensemble_read_message id={mid}.")
        self.assertEqual(self.typed("pty-opten"), [])
        self.assertEqual(self.queue(), [])
        # The typed line is hub input, not the CEO's words.
        info = dashboard.hub_input_kind(line)
        self.assertEqual(info, {"kind": "pomsg", "fromProject": "opten", "poKind": "bug"})
        self.assertFalse(dashboard.typed_by_person(line))
        self.assertEqual(dashboard.hub_input_kind("[from the PO] carry on")["kind"], "human")

    def test_read_in_full_and_listed(self):
        mid = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text=self.TEXT)["id"]
        got = self.ok(self.dock, "ensemble_read_message", id=mid)
        self.assertEqual(got["text"], self.TEXT.strip())
        self.assertEqual((got["from"], got["to"], got["direction"], got["kind"]),
                         ("opten PO", "Dock PO", "received", "bug"))
        self.assertIn(f"replyTo={mid}", got["howToAnswer"])
        self.assertEqual(self.ok(self.opten, "ensemble_read_message", id=mid)["direction"], "sent")
        listed = self.ok(self.dock, "ensemble_read_message")["messages"]
        self.assertEqual([(m["id"], m["firstLine"]) for m in listed], [(mid, "The splitter jumps on drop")])
        self.assertIn("no PO message", self.refused(self.dock, "ensemble_read_message", id="pm-nope"))
        self.assertIn("only for a project's PO", self.refused(self.task, "ensemble_read_message", id=mid))

    def test_a_reply_goes_back_to_the_sender(self):
        mid = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="question", text="Is 2.1 out?")["id"]
        res = self.ok(self.dock, "ensemble_message_po", replyTo=mid, text="Yes, tagged v2.1.0.")
        self.assertEqual((res["to"], res["kind"]), ("opten PO", "answer"))
        self.assertTrue(res["delivered"])      # an answer to a question wakes its asker
        back = self.pomsgs(self.opten)[-1]
        self.assertEqual((back["direction"], back["replyTo"], back["fromProjectName"], back["text"]),
                         ("received", mid, "Dock", "Yes, tagged v2.1.0."))
        self.assertTrue(self.typed("pty-opten")[0].startswith(
            f"[from the Dock PO] answer: Yes, tagged v2.1.0. (a reply to {mid}) — read it in full"))
        self.assertEqual(self.ok(self.dock, "ensemble_read_message", id=mid)["replies"], [res["id"]])
        # A reply names the other side itself; another project is refused.
        self.projects.append({"id": "proj-x", "name": "X", "poRoomId": self.task})
        self.assertIn("a reply goes there", self.refused(self.dock, "ensemble_message_po", replyTo=mid,
                                                        projectId="X", text="?"))
        self.assertIn("no PO message", self.refused(self.dock, "ensemble_message_po", replyTo="pm-zz", text="?"))


class LoopGuard(_World):
    def test_quiet_kinds_wait_then_go_with_the_next_line_or_when_idle(self):
        q = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="question", text="Q?")["id"]
        a = self.ok(self.dock, "ensemble_message_po", replyTo=q, text="A.")["id"]
        self.assertEqual(len(self.typed("pty-opten")), 1)
        # Thanks for the answer: an answer to an answer does not wake.
        res = self.ok(self.opten, "ensemble_message_po", replyTo=a, text="Thanks.")
        self.assertFalse(res["delivered"])
        self.assertIn("does not wake", res["note"])
        info = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="info", text="FYI: moved.")["id"]
        self.assertEqual(len(self.typed("pty-dock")), 1)     # only the question
        self.assertEqual(len(self.pomsgs(self.dock)), 4)     # all in its chat
        # Not yet: nothing typed while they wait for company.
        self.now += po_messages.QUIET_WAIT_S - 60
        self.assertEqual(po_messages.tick(), [])
        # A bug takes them with it, in one line.
        bug = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")["id"]
        line = self.typed("pty-dock")[-1]
        self.assertTrue(line.startswith(f"[from the opten PO] bug: Crash. — read it in full with "
                                        f"ensemble_read_message id={bug}. Also waiting for you: "))
        self.assertIn(f"id={info}", line)
        self.assertEqual(len(self.typed("pty-dock")), 2)
        self.assertEqual(self.queue(), [])

    def test_a_quiet_one_alone_is_told_when_idle_after_the_wait(self):
        mid = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="info", text="FYI.")["id"]
        self.now += po_messages.QUIET_WAIT_S
        self.idle["pty-dock"] = False
        self.assertEqual(po_messages.tick(), [])             # busy: later
        self.idle["pty-dock"] = True
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertIn(f"id={mid}", self.typed("pty-dock")[0])
        self.assertEqual(self.queue(), [])

    def test_at_most_n_wakes_an_hour_per_pair_then_held_for_the_bell(self):
        n = po_messages.WAKES_PER_HOUR
        for i in range(n):
            self.now += 60
            # Both directions count for the pair.
            src = self.opten if i % 2 == 0 else self.dock
            self.assertTrue(self.ok(src, "ensemble_message_po",
                                    projectId="Dock" if src == self.opten else "opten",
                                    kind="bug", text=f"bug {i}")["delivered"])
        self.now += 60
        res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="one too many")
        self.assertFalse(res["delivered"])
        self.assertTrue(res["held"])
        self.assertIn("held", res["note"])
        self.assertEqual(len(self.pomsgs(self.dock)), n + 1)   # in the chat all the same
        held = po_messages.held_by_room()
        self.assertEqual(list(held), [self.dock])
        self.assertEqual(held[self.dock]["count"], 1)
        self.assertIn("from the opten PO held", held[self.dock]["reason"])
        typed_before = len(self.typed("pty-dock"))
        self.assertEqual(po_messages.tick(), [])
        # An hour after the first line the pair may wake again.
        self.now = T0 + 60 + po_messages.WINDOW_S
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(len(self.typed("pty-dock")), typed_before + 1)
        self.assertIn("one too many", self.typed("pty-dock")[-1])
        self.assertEqual(po_messages.held_by_room(), {})

    def test_the_bell_lists_the_po_with_held_messages(self):
        import attention
        for i in range(po_messages.WAKES_PER_HOUR + 1):
            self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text=f"bug {i}")
        with mock.patch.object(attention, "_classify_agent", return_value=None), \
                mock.patch.object(attention, "_duplicate_ptys", return_value=None), \
                mock.patch.object(attention, "_room_level", return_value=None), \
                mock.patch.object(attention, "_claude_status_by_session", return_value={}), \
                mock.patch.object(attention, "_evidence", return_value={"alive": True}), \
                mock.patch.object(dashboard, "project_keys", return_value={}):
            items = attention._items()
        [it] = [i for i in items if i["roomId"] == self.dock]
        self.assertEqual(it["state"], "waiting_for_you")
        self.assertEqual(it["heldPoMessages"], 1)
        self.assertIn("held", it["reason"])
        self.assertEqual([i for i in items if i["roomId"] == self.opten], [])


class StoppedPo(_World):
    def test_not_started_told_when_it_runs_and_is_idle(self):
        self.ptys.pop("pty-dock")                         # the Dock PO is not running
        res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        self.assertFalse(res["delivered"])
        self.assertFalse(res["held"])
        self.assertIn("not running", res["note"])
        self.assertEqual(len(self.pomsgs(self.dock)), 1)   # waits in its chat
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.queue()), 1)
        # It runs again, busy at first: still waiting; idle: told once.
        self.ptys["pty-dock"] = _FakePty()
        self.idle["pty-dock"] = False
        self.assertEqual(po_messages.tick(), [])
        self.idle["pty-dock"] = True
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(len(self.typed("pty-dock")), 1)
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.typed("pty-dock")), 1)

    def test_a_po_room_that_is_gone_leaves_the_queue(self):
        self.ptys.pop("pty-dock")
        self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        chatroom.delete_room(self.dock)
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(self.queue(), [])
        self.assertEqual(len(self.pomsgs(self.opten)), 1)   # the sender keeps its copy


class WakeLine(unittest.TestCase):
    def test_long_first_lines_and_many_messages_stay_one_line(self):
        items = [{"id": f"pm-{i:08d}", "fromName": "opten", "kind": "info",
                  "firstLine": po_messages.first_line("x" * 500)} for i in range(20)]
        line, told = po_messages.wake_line(items)
        self.assertLessEqual(len(line), po_messages._WAKE_MAX)
        self.assertNotIn("\n", line)
        self.assertGreaterEqual(len(told), 1)
        self.assertLess(len(told), 20)
        self.assertTrue(line.startswith("[from the opten PO] info: xxx"))

    def test_first_line_skips_markdown(self):
        self.assertEqual(po_messages.first_line("\n## **Bug**: the `x`\nmore"), "Bug: the x")
        self.assertEqual(po_messages.first_line("---\n- item"), "item")


if __name__ == "__main__":
    unittest.main()
