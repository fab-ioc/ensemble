"""Messages between two projects' POs (po_messages.py, ensemble_message_po and
ensemble_read_message): only a PO may write, to another project's PO; the
message is kept whole in both PO chats and the target is typed one line; the
answer goes back the same way; quiet kinds wait, a pair of projects wakes at
most WAKES_PER_HOUR times an hour and the rest is held for the CEO's bell; a
stopped PO is resumed for a message that wakes, with its line as the first
input (a PO with nothing to resume is flagged and told when it runs)."""
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


class _FakeLauncher:
    """The hub's resume of a stopped room (Handler._resume_room) and what it
    holds for the room until the line is typed (pending_input)."""

    def __init__(self):
        self.calls = []
        self.held = {}
        self.fail_with = ""

    def _resume_room(self, room, text="", to="", key="", quiet=False):
        self.calls.append((room["id"], text, key))
        state = "failed" if self.fail_with else "resuming"
        self.held[room["id"]] = {"state": state, "error": self.fail_with,
                                 "items": [{"text": text, "key": key}]}
        if self.fail_with:
            raise dashboard.StartRoomError(self.fail_with)
        return {"resumed": [{"identity": "claude", "ptyId": "pty-new"}], "queued": 1,
                "delivered": 0}

    def _start_or_resume_room(self, room):
        self.calls.append((room["id"], None, None))
        return [{"identity": "claude", "ptyId": "pty-new"}]

    def pending_input(self, room_id):
        return self.held.get(room_id)

    def discard_pending(self, room_id, key=""):
        return self.held.pop(room_id, None) is not None

    def typed_in(self, room_id):
        self.held.pop(room_id)

    def stopped_after_start(self, room_id, why):
        self.held[room_id].update(state="failed", error=why)


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
        self.launcher = _FakeLauncher()
        patches = [
            mock.patch.object(dashboard, "hub_launcher", return_value=self.launcher),
            mock.patch.object(dashboard, "pending_input", side_effect=self.launcher.pending_input),
            mock.patch.object(dashboard, "discard_pending", side_effect=self.launcher.discard_pending),
            mock.patch.object(dashboard, "DASHBOARD_DIR", d / "state"),
            mock.patch.object(dashboard, "load_projects", side_effect=lambda: [dict(p) for p in self.projects]),
            mock.patch.object(dashboard, "load_session_projects", return_value={}),
            mock.patch.object(dashboard, "load_labels", return_value={}),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
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

    def test_held_messages_show_on_a_po_already_waiting_for_you(self):
        import attention
        for i in range(po_messages.WAKES_PER_HOUR + 1):
            self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text=f"bug {i}")
        asked = ("waiting_for_you", "asked: which release?", {"since": T0})
        with mock.patch.object(attention, "_classify_agent", return_value=None), \
                mock.patch.object(attention, "_duplicate_ptys", return_value=None), \
                mock.patch.object(attention, "_room_level",
                                  side_effect=lambda room, live: asked if room["id"] == self.dock else None), \
                mock.patch.object(attention, "_claude_status_by_session", return_value={}), \
                mock.patch.object(attention, "_evidence", return_value={"alive": True}), \
                mock.patch.object(dashboard, "project_keys", return_value={}):
            items = attention._items()
        [it] = [i for i in items if i["roomId"] == self.dock]
        self.assertEqual(it["state"], "waiting_for_you")
        self.assertEqual(it["heldPoMessages"], 1)
        self.assertIn("which release?", it["reason"])
        self.assertIn("held", it["reason"])


class StoppedPo(_World):
    def test_not_started_told_when_it_runs_and_is_idle(self):
        self.ptys.pop("pty-dock")                         # the Dock PO is not running
        res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        self.assertFalse(res["delivered"])
        self.assertFalse(res["held"])
        self.assertIn("could not be started", res["note"])
        self.assertEqual(len(self.pomsgs(self.dock)), 1)   # waits in its chat
        self.assertEqual(self.launcher.calls, [])          # nothing to resume: not started
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

    def test_a_queue_that_cannot_be_saved_promises_nothing(self):
        self.ptys.pop("pty-dock")
        with mock.patch.object(po_messages, "_save", return_value=False):
            res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        self.assertFalse(res["delivered"])
        self.assertFalse(res["queued"])
        self.assertIn("NOT told", res["note"])
        self.assertEqual(len(self.pomsgs(self.dock)), 1)   # in its chat all the same
        self.assertEqual(self.queue(), [])

    def test_not_typed_when_the_mark_cannot_be_saved(self):
        self.ptys.pop("pty-dock")
        self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        self.ptys["pty-dock"] = _FakePty()
        with mock.patch.object(po_messages, "_save", return_value=False):
            self.assertEqual(po_messages.tick(), [])
        self.assertEqual(self.typed("pty-dock"), [])
        self.assertEqual(po_messages.tick(), [self.dock])  # the disk works again: told
        self.assertEqual(len(self.typed("pty-dock")), 1)

    def test_a_failed_save_after_typing_never_types_it_twice(self):
        self.ptys.pop("pty-dock")
        self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        self.ptys["pty-dock"] = _FakePty()
        real = po_messages._save
        calls = []

        def second_fails(state):
            calls.append(1)
            return real(state) if len(calls) == 1 else False
        with mock.patch.object(po_messages, "_save", side_effect=second_fails):
            self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(len(self.typed("pty-dock")), 1)
        self.assertEqual(self.queue(), [])                 # the mark reads as told
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.typed("pty-dock")), 1)

    def test_a_failed_save_after_typing_still_counts_the_wake(self):
        n = po_messages.WAKES_PER_HOUR
        for i in range(n - 1):
            self.now += 60
            self.assertTrue(self.ok(self.opten, "ensemble_message_po", projectId="Dock",
                                    kind="bug", text=f"bug {i}")["delivered"])
        real = po_messages._save
        calls = []

        def last_fails(state):
            calls.append(1)
            return real(state) if len(calls) < 3 else False   # enqueue, mark ok; cleanup fails
        self.now += 60
        with mock.patch.object(po_messages, "_save", side_effect=last_fails):
            self.assertTrue(self.ok(self.opten, "ensemble_message_po", projectId="Dock",
                                    kind="bug", text="the last one")["delivered"])
        self.assertEqual(self.queue(), [])
        self.now += 60
        res = self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="one too many")
        self.assertFalse(res["delivered"])
        self.assertTrue(res["held"])
        self.assertEqual(len(self.typed("pty-dock")), n)

    def test_a_po_room_that_is_gone_leaves_the_queue(self):
        self.ptys.pop("pty-dock")
        self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind="bug", text="Crash.")
        chatroom.delete_room(self.dock)
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(self.queue(), [])
        self.assertEqual(len(self.pomsgs(self.opten)), 1)   # the sender keeps its copy


class ResumedPo(_World):
    """A stopped PO with a conversation to resume is started for a message
    that wakes, the line its first input."""

    def setUp(self):
        super().setUp()
        self.ptys.pop("pty-dock")
        chatroom.patch_participant(self.dock, "claude", {"sessionId": "sess-dock"})

    def send(self, kind="bug", text="Crash."):
        return self.ok(self.opten, "ensemble_message_po", projectId="Dock", kind=kind, text=text)

    def logged(self, part):
        return [c for c in po_messages._log.call_args_list if part in c.args[0]]

    def test_a_waking_message_resumes_it_once_with_the_line_as_first_input(self):
        res = self.send()
        self.assertFalse(res["held"])
        self.assertIn("starting it", res["note"])
        [(rid, text, key)] = self.launcher.calls
        self.assertEqual(rid, self.dock)
        self.assertEqual(text, "[from the opten PO] bug: Crash. — read it in full with "
                               f"ensemble_read_message id={res['id']}.")
        self.assertEqual(key, f"pomsg:{res['id']}")
        # Queued until the line is in; a look meanwhile starts nothing more.
        [item] = self.queue()
        self.assertTrue(item["resuming"])
        self.now += 60
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.launcher.calls), 1)
        self.launcher.typed_in(self.dock)
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(self.queue(), [])
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertEqual(po_messages.held_by_room(), {})

    def test_a_message_sent_while_it_is_coming_up_waits_for_the_resume(self):
        self.send(text="first")
        self.send(text="second")
        self.assertEqual(len(self.launcher.calls), 1)
        self.launcher.typed_in(self.dock)
        self.now += 60
        po_messages.tick()                          # the first is in
        self.assertEqual([p["firstLine"] for p in self.queue()], ["second"])
        self.now += 60
        po_messages.tick()                          # still stopped here: resumed for it
        self.assertEqual(len(self.launcher.calls), 2)
        self.assertIn("second", self.launcher.calls[1][1])

    def test_a_quiet_message_does_not_start_it(self):
        self.send(kind="info", text="FYI.")
        self.now += po_messages.QUIET_WAIT_S + 60
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(len(self.queue()), 1)
        # It goes along with the next message that wakes.
        self.send(kind="question", text="Which release?")
        [(_rid, text, _key)] = self.launcher.calls
        self.assertIn("question: Which release?", text)
        self.assertIn("info: FYI.", text)

    def test_the_hourly_limit_holds_for_resumes(self):
        n = po_messages.WAKES_PER_HOUR
        for i in range(n):
            self.now += 60
            self.assertFalse(self.send(text=f"bug {i}")["held"])
            self.launcher.typed_in(self.dock)
            po_messages.tick()
        self.assertEqual(len(self.launcher.calls), n)
        self.now += 60
        res = self.send(text="one too many")
        self.assertTrue(res["held"])
        self.assertEqual(len(self.launcher.calls), n)
        self.now += 60
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(len(self.launcher.calls), n)
        self.now += po_messages.WINDOW_S
        po_messages.tick()
        self.assertEqual(len(self.launcher.calls), n + 1)

    def test_a_failed_resume_leaves_it_queued_and_flagged(self):
        self.launcher.fail_with = "The agent kind is not installed."
        res = self.send()
        self.assertFalse(res["delivered"])
        [item] = self.queue()
        self.assertNotIn("resuming", item)
        self.assertIn("not installed", item["undelivered"]["why"])
        self.assertEqual(self.launcher.held, {})    # not also held for the room's Retry
        self.assertFalse(any(po_messages._load()["wakes"].values()))   # the count is rolled back
        held = po_messages.held_by_room()[self.dock]
        self.assertEqual(held["count"], 1)
        self.assertIn("not received", held["reason"])
        self.assertIn("not installed", held["reason"])
        # Tried again at each look (once a minute), logged once.
        for _ in range(3):
            self.now += 60
            po_messages.tick()
        self.assertEqual(len(self.launcher.calls), 4)
        self.assertEqual(len(self.logged("not delivered to the stopped PO")), 1)
        self.launcher.fail_with = ""
        self.now += 60
        po_messages.tick()
        self.launcher.typed_in(self.dock)
        self.now += 60
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(self.queue(), [])
        self.assertEqual(po_messages.held_by_room(), {})

    def test_a_line_not_typed_after_the_start_is_queued_again(self):
        self.send()
        self.launcher.stopped_after_start(self.dock, "a prompt is on the agent's screen")
        self.now += 60
        self.assertEqual(po_messages.tick(), [])
        [item] = self.queue()
        self.assertNotIn("resuming", item)
        self.assertIn("prompt", item["undelivered"]["why"])
        self.assertEqual(self.launcher.held, {})    # taken back from the room: typed once only
        self.now += 60
        po_messages.tick()
        self.assertEqual(len(self.launcher.calls), 2)

    def test_nothing_to_resume_is_flagged_not_started(self):
        chatroom.patch_participant(self.dock, "claude", {"sessionId": ""})
        self.send()
        self.assertEqual(self.launcher.calls, [])
        self.assertIn("no conversation", self.queue()[0]["undelivered"]["why"])

    def test_a_message_already_pending_is_delivered_by_the_first_tick(self):
        # As pm-e00a1c63 sat in the queue before this: no resuming mark.
        state = {"pending": [{"id": "pm-e00a1c63", "toRoomId": self.dock,
                              "fromProjectId": "proj-opten", "toProjectId": "proj-dock",
                              "fromName": "opten", "kind": "question",
                              "firstLine": "Our open questions", "wake": True, "at": T0 - 3600}],
                 "wakes": {}}
        self.assertTrue(po_messages._save(state))
        self.assertEqual(po_messages.tick(), [self.dock])
        [(rid, text, _key)] = self.launcher.calls
        self.assertEqual(rid, self.dock)
        self.assertIn("question: Our open questions", text)
        self.assertIn("id=pm-e00a1c63", text)
        self.launcher.typed_in(self.dock)
        self.now += 60
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(self.queue(), [])

    def test_a_restart_during_the_resume_counts_it_as_told(self):
        self.send()
        self.launcher.held.clear()                  # the hub restarted: nothing held
        self.now += 60
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(self.queue(), [])
        self.assertEqual(len(self.launcher.calls), 1)

    def test_a_po_rotating_awaiting_its_handover_or_replaced_is_not_resumed(self):
        for name in ("is_rotating", "awaiting_handover", "room_rotating", "switching"):
            with self.subTest(name), mock.patch.object(rotation, name, return_value=True):
                self.now += 60
                self.send(text=name)
                po_messages.tick()
                self.assertEqual(self.launcher.calls, [])
        self.now += 60
        po_messages.tick()
        self.assertEqual(len(self.launcher.calls), 1)   # all four, in one line
        self.assertEqual(len(self.queue()), 4)

    def test_a_running_busy_po_is_not_resumed(self):
        self.ptys["pty-dock"] = _FakePty()
        self.idle["pty-dock"] = False
        with mock.patch.object(po_messages, "_target", return_value=None):
            self.send()                             # not typed at once (busy being replaced)
        self.assertEqual(self.launcher.calls, [])
        self.now += 60
        self.assertEqual(po_messages.tick(), [])
        self.assertEqual(self.launcher.calls, [])
        self.idle["pty-dock"] = True
        self.assertEqual(po_messages.tick(), [self.dock])
        self.assertEqual(len(self.typed("pty-dock")), 1)
        self.assertEqual(self.launcher.calls, [])

    def test_a_po_in_a_team_is_resumed_and_typed_the_line_when_idle(self):
        # A line given to a team's resume would be posted as the CEO's words.
        room = chatroom.get_room(self.dock, public=False)
        room["mode"] = "collab"
        room["participants"].append({"identity": "codex", "agent": "codex", "kind": "agent"})
        chatroom.update_room(room)
        self.send()
        self.assertEqual(self.launcher.calls, [(self.dock, None, None)])
        self.assertEqual(self.launcher.held, {})
        [item] = self.queue()
        self.assertNotIn("resuming", item)
        # Up, and idle at the next look: typed the line, not resumed again.
        chatroom.patch_participant(self.dock, "claude", {"ptyId": "pty-dock"})
        self.ptys["pty-dock"] = _FakePty()
        self.now += 60
        self.assertEqual(po_messages.tick(), [self.dock])
        [line] = self.typed("pty-dock")
        self.assertTrue(line.startswith("[from the opten PO] bug: Crash."))
        self.assertEqual(len(self.launcher.calls), 1)
        self.assertEqual(self.queue(), [])

    def test_the_bell_shows_a_po_that_cannot_receive(self):
        import attention
        self.launcher.fail_with = "no agent of this task could be started"
        self.send()
        with mock.patch.object(attention, "_classify_agent", return_value=None), \
                mock.patch.object(attention, "_duplicate_ptys", return_value=None), \
                mock.patch.object(attention, "_room_level", return_value=None), \
                mock.patch.object(attention, "_claude_status_by_session", return_value={}), \
                mock.patch.object(attention, "_evidence", return_value={"alive": False}), \
                mock.patch.object(dashboard, "project_keys", return_value={}):
            items = attention._items()
        [it] = [i for i in items if i["roomId"] == self.dock]
        self.assertEqual(it["state"], "waiting_for_you")
        self.assertIn("not received", it["reason"])


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
