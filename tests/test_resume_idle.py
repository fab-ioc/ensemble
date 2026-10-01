"""#149 Fewer tokens, round 4: a task started again does not redo its last turn.

Measured over a week (tools/measure_wakes.py): 62 ``[resumed]`` owner turns
cost 326M tokens, 58 of them a PO stopping and starting a task within minutes
(28 to make it read a ruling written into the spec, 26 bare, 3 to change the
reviewer's seat), and about a third of the owners were idle when stopped and
then redid a whole check pass. Three cuts, each kept honest here:

* **cut 1** — a spec amendment reaches a RUNNING owner as one ``[spec]`` line
  with the change inline (``ensemble_update_task``), so no one stops and
  starts a task for it; a stopped task's owner is told at its next start;
* **cut 2** — ``stop_task`` records whether each agent's turn was over; the
  next start types an idle owner a note that says only what is new and not to
  redo its last turn, a mid-turn owner the note it always had (the real
  resume), and an owner whose state nobody recorded the same: never lost;
* **cut 3** — a seat nobody is sitting in (the on-mention reviewer, a stopped
  seat) can change under a live task; a running agent's own seat cannot.
"""
from __future__ import annotations

import time
import unittest
from unittest import mock

import chatroom
import dashboard
import ensemble_tools
from test_quiet_restart import _Hub, FakePty


class _Stops(_Hub):
    """The harness, plus a stop that kills only fake terminals."""

    def setUp(self):
        super().setUp()
        self.killed: list[str] = []
        for p in (mock.patch.object(dashboard.ptyrun, "kill", self.kill),
                  mock.patch.object(dashboard.ptyrun, "forget_death", lambda pid: None),
                  mock.patch.object(dashboard, "load_session_projects", lambda: {})):
            p.start()
            self.addCleanup(p.stop)

    def kill(self, pid):
        self.killed.append(pid)
        if pid in self.ptys:
            self.ptys[pid]._alive = False
        self.listed[:] = [x for x in self.listed if x["id"] != pid]

    def stop(self, rid):
        self.assertTrue(dashboard.stop_task(rid))

    def part(self, rid, ident="claude"):
        return chatroom.participant(chatroom.get_room(rid, public=False), ident)

    def start(self, rid, text="", to=""):
        h = self.handler()
        out = h._resume_room(chatroom.get_room(rid, public=False), text=text, to=to)
        self.join()
        return out


class StopRecordsTheState(_Stops):
    def test_stop_writes_each_agents_turn_state_before_killing_it(self):
        rid = self.room(agents=("claude", "codex"))
        a = self.running(rid, "claude", "idle")
        b = self.running(rid, "codex", "working", "screen")
        self.stop(rid)
        self.assertEqual(self.part(rid, "claude")["stoppedState"], "idle")
        self.assertEqual(self.part(rid, "codex")["stoppedState"], "working")
        self.assertEqual(sorted(self.killed), sorted([a, b]))
        self.assertTrue(chatroom.get_room(rid, public=False).get("stoppedAt"))

    def test_an_agent_without_a_terminal_gets_no_state(self):
        rid = self.room()
        self.stop(rid)
        self.assertNotIn("stoppedState", self.part(rid))

    def test_a_state_that_cannot_be_read_is_left_unrecorded(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        with mock.patch.object(dashboard.attention, "turn_state",
                               side_effect=RuntimeError("no status file")):
            self.stop(rid)
        self.assertNotIn("stoppedState", self.part(rid))
        self.assertEqual(len(self.killed), 1, "the stop went ahead")


class TheResumeNote(_Stops):
    def test_an_owner_stopped_between_turns_is_told_not_to_redo_its_last_one(self):
        rid = self.room(spec="Sell the X5.")
        self.running(rid, "claude", "idle")
        self.stop(rid)
        self.start(rid)
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE_IDLE]})
        note = dashboard.RESUME_NOTE_IDLE
        self.assertIn("do not redo it", note)
        self.assertIn("read it again with ensemble_get_task", note)
        self.assertIn("act only on what changed", note)
        self.assertIn("If you had reported, or are waiting for an answer, you still are", note)
        self.assertEqual(dashboard.hub_input_kind(note), {"kind": "resumed"})
        self.assertNotIn("stoppedState", self.part(rid), "the state outlived the start it was for")
        self.assertTrue(self.part(rid).get("resumedAt"))

    def test_an_idle_owner_whose_session_has_read_the_spec_is_not_sent_to_read_it(self):
        rid = self.room(spec="Sell the X5.")
        chatroom.patch_participant(rid, "claude", dashboard.spec_seen(self.part(rid), "Sell the X5."))
        self.running(rid, "claude", "idle")
        self.stop(rid)
        self.start(rid)
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE_IDLE_SAME_SPEC]})
        self.assertIn("do not read it again", dashboard.RESUME_NOTE_IDLE_SAME_SPEC)
        self.assertNotIn("ensemble_get_task", dashboard.RESUME_NOTE_IDLE_SAME_SPEC
                         .replace("Report with ensemble_report", ""))

    def test_a_spec_changed_while_stopped_sends_an_idle_owner_to_read_it(self):
        rid = self.room(spec="Sell the X5.")
        chatroom.patch_participant(rid, "claude", dashboard.spec_seen(self.part(rid), "Sell the X5."))
        self.running(rid, "claude", "idle")
        self.stop(rid)
        ok, _room, err = dashboard.update_task(rid, spec="Sell the X5, and the winter tyres.")
        self.assertTrue(ok, err)
        self.start(rid)
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE_IDLE]})

    def test_an_owner_stopped_in_the_middle_of_a_turn_carries_on_as_before(self):
        for state in ("working", "waiting"):
            rid = self.room(title=state)
            self.running(rid, "claude", state)
            self.stop(rid)
            self.start(rid)
            self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE]}, state)
            self.assertNotIn("stoppedState", self.part(rid))

    def test_no_recorded_state_reads_as_mid_turn(self):
        # A room stopped by a hub that did not keep the state, or by a crash.
        rid = self.room()
        self.start(rid)
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE]})
        room = chatroom.get_room(rid, public=False)
        self.assertEqual(dashboard.resume_note_for(room, {**self.part(rid), "stoppedState": "unknown"}),
                         dashboard.RESUME_NOTE)
        self.assertEqual(dashboard.resume_note_for(room, {**self.part(rid), "stoppedState": "idle"}),
                         dashboard.RESUME_NOTE_IDLE)

    def test_a_message_sent_with_the_start_follows_the_note_in_one_input(self):
        for state, note in (("idle", dashboard.RESUME_NOTE_IDLE), ("working", dashboard.RESUME_NOTE)):
            rid = self.room(title=state)
            self.running(rid, "claude", state)
            self.stop(rid)
            self.start(rid, text="and the winter tyres too")
            [typed] = self.typed(rid)["claude"]
            self.assertEqual(typed, "\x1b[200~" + note + "\n\nand the winter tyres too\x1b[201~", state)

    def test_a_po_and_a_fresh_seat_get_no_note(self):
        rid = self.room()
        self.running(rid, "claude", "idle")
        self.stop(rid)
        self.projects = [{"id": "p", "poRoomId": rid}]
        self.start(rid, text="status?")
        self.assertEqual(self.typed(rid), {"claude": ["status?"]})

    def test_a_review_asked_for_with_the_start_still_starts(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.running(rid, "claude", "idle")
        self.stop(rid)
        full = chatroom.get_room(rid, public=False)
        full["messages"].append({"id": "m7", "from": "user", "to": "codex",
                                 "text": "@codex review abc", "ts": time.time()})
        chatroom.update_room(full)
        h = self.handler()
        h._ring_recipients(rid, {"recipients": ["codex"], "message": {"id": "m7", "from": "user"}})
        self.assertEqual(self.reviews, [(rid, "codex", "m7")])

    def test_every_note_typed_with_a_message_is_two_turns(self):
        for note in dashboard.RESUME_NOTES:
            turns = dashboard.classify_turns([{"role": "user", "text": note + "\n\nand step 2 please"}])
            self.assertEqual([t["text"] for t in turns], [note, "and step 2 please"])
            self.assertEqual(dashboard.first_words(note), "Resumed task")


class _Ring:
    """A handler that records what it is asked to type into whom."""

    def __init__(self, takes=True):
        self.rung: list[tuple] = []
        self.takes = takes

    def _ring(self, room_id, idents, wake):
        self.rung.append((room_id, list(idents), wake))
        return list(idents) if self.takes else []


class ASpecAmendmentReachesARunningOwner(_Stops):
    def caller(self):
        made = chatroom.create_room("planner", [{"identity": "claude", "agent": "claude",
                                                 "role": "planner"}])
        room = chatroom.get_room(made["id"], public=False)
        return {"room": room, "identity": "claude",
                "part": chatroom.participant(room, "claude"), "projectId": ""}

    def amend(self, rid, spec, handler):
        return ensemble_tools._update_task(self.caller(), {"taskId": rid, "spec": spec}, handler)

    def test_the_line_is_a_hub_kind_of_its_own(self):
        line, inlined = dashboard.spec_change_line({"id": "room-1"}, "Sell the X5.\nBy Friday.",
                                                   "Sell the X5.\nBy Monday.", by="Your PO")
        self.assertTrue(inlined)
        self.assertEqual(dashboard.hub_input_kind(line), {"kind": "spec"})
        self.assertEqual(dashboard.first_words(line), "Spec amendment")
        self.assertIn("-By Friday.\n+By Monday.", line)
        self.assertIn("do not redo what you have done, act only on what changed", line)
        self.assertNotIn("ensemble_get_task", line)
        long, inlined = dashboard.spec_change_line({"id": "room-1"}, "a", "b\n" * 400)
        self.assertFalse(inlined)
        self.assertIn("ensemble_get_task taskId=room-1 spec=true", long)
        self.assertLess(len(long), 400)

    def test_a_running_owner_is_typed_the_change_and_has_seen_the_spec(self):
        rid = self.room(spec="Sell the X5.\nBy Friday.")
        self.running(rid, "claude", "working")
        h = _Ring()
        out = self.amend(rid, "Sell the X5.\nBy Monday.", h)
        self.assertEqual(len(h.rung), 1)
        room_id, idents, line = h.rung[0]
        self.assertEqual((room_id, idents), (rid, ["claude"]))
        self.assertTrue(line.startswith("[spec] claude amended your spec at "), line)
        self.assertIn("-By Friday.\n+By Monday.", line)
        self.assertIn("the owner has been told what changed (claude)", out["note"])
        self.assertNotIn("will not re-read", out["note"])
        seen = self.part(rid)["specSeen"]
        self.assertEqual(seen, {"rev": dashboard._spec_rev("Sell the X5.\nBy Monday."),
                                "sessionId": "sid-claude"})

    def test_the_po_is_named_as_the_po(self):
        rid = self.room(spec="Sell the X5.")
        self.running(rid, "claude", "idle")
        ctx = self.caller()
        self.projects = [{"id": "p", "poRoomId": ctx["room"]["id"]}]
        with mock.patch.object(ensemble_tools._d, "load_session_projects",
                               lambda: {rid: "p", ctx["room"]["id"]: "p"}):
            h = _Ring()
            ensemble_tools._update_task(ctx, {"taskId": rid, "spec": "Sell the X3."}, h)
        self.assertTrue(h.rung[0][2].startswith("[spec] Your PO amended your spec at "), h.rung[0][2])

    def test_a_long_change_sends_the_owner_to_read_the_spec(self):
        rid = self.room(spec="Sell the X5.")
        self.running(rid, "claude", "idle")
        h = _Ring()
        out = self.amend(rid, "Sell the X5.\n" + "\n".join(f"Step {i}: do the thing." for i in range(80)), h)
        self.assertIn("ensemble_get_task taskId=", h.rung[0][2])
        self.assertIn("too long to inline", out["note"])
        self.assertNotIn("specSeen", self.part(rid), "it has not seen it: the line did not carry it")

    def test_a_terminal_that_does_not_take_the_line_is_said_so(self):
        rid = self.room(spec="Sell the X5.")
        self.running(rid, "claude", "idle")
        out = self.amend(rid, "Sell the X3.", _Ring(takes=False))
        self.assertIn("could not be told", out["note"])
        self.assertNotIn("specSeen", self.part(rid))

    def test_a_stopped_task_is_not_rung_and_its_next_start_says_the_spec_changed(self):
        rid = self.room(spec="Sell the X5.")
        chatroom.patch_participant(rid, "claude", dashboard.spec_seen(self.part(rid), "Sell the X5."))
        self.running(rid, "claude", "idle")
        self.stop(rid)
        h = _Ring()
        out = self.amend(rid, "Sell the X3.", h)
        self.assertEqual(h.rung, [])
        self.assertEqual(out["note"], "")
        self.start(rid)
        self.assertEqual(self.typed(rid), {"claude": [dashboard.RESUME_NOTE_IDLE]})

    def test_the_same_spec_again_rings_nobody(self):
        rid = self.room(spec="Sell the X5.")
        self.running(rid, "claude", "idle")
        h = _Ring()
        self.amend(rid, "Sell the X5.", h)
        self.assertEqual(h.rung, [])

    def test_the_tool_descriptions_say_so(self):
        by_name = {t["name"]: t["description"] for t in ensemble_tools.TOOLS}
        self.assertIn("never stop and start a task to make it read an amendment", by_name["ensemble_update_task"])
        self.assertNotIn("does not re-read the spec", by_name["ensemble_update_task"])
        self.assertIn("not to redo its last turn", by_name["ensemble_start_task"])


class ReassigningASeatUnderALiveTask(_Stops):
    def lineup(self, reviewer_model="", owner_model="", owner_role="engineer"):
        return [{"identity": "claude", "agent": "claude", "model": owner_model, "role": owner_role},
                {"identity": "codex", "agent": "codex", "model": reviewer_model, "role": "reviewer"}]

    def setUp(self):
        super().setUp()
        installed = mock.Mock()
        installed.installed.return_value = True
        p = mock.patch.object(dashboard.agents, "get_agent", lambda key: installed if key in ("claude", "codex") else None)
        p.start()
        self.addCleanup(p.stop)

    def test_the_reviewers_seat_changes_while_the_owner_runs_on(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        pid = self.running(rid, "claude", "working")
        ok, room, err = dashboard.reassign_task(rid, self.lineup(reviewer_model="gpt-5-high"))
        self.assertTrue(ok, err)
        owner = chatroom.participant(room, "claude")
        self.assertEqual(owner.get("ptyId"), pid, "the running owner lost its terminal")
        self.assertEqual(chatroom.participant(room, "codex")["model"], "gpt-5-high")
        self.assertTrue(dashboard._room_is_live(chatroom.get_room(rid, public=False)))

    def test_a_new_reviewer_seat_can_be_added_to_a_live_solo_task(self):
        rid = self.room()
        pid = self.running(rid, "claude", "idle")
        ok, room, err = dashboard.reassign_task(rid, [self.lineup()[0], {"agent": "codex", "role": "reviewer"}])
        self.assertTrue(ok, err)
        self.assertEqual(chatroom.participant(room, "claude").get("ptyId"), pid)
        self.assertEqual(room["mode"], "collab")
        self.assertTrue(chatroom.participant(room, "codex").get("fresh"))

    def test_a_running_agents_own_seat_cannot_change(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.running(rid, "claude", "working")
        for lineup in (self.lineup(owner_model="opus"),
                       self.lineup(owner_role="designer"),
                       [self.lineup()[1]],                                # the owner dropped
                       [{"agent": "codex", "role": "engineer"}, self.lineup()[1]]):   # its kind changed
            ok, _room, err = dashboard.reassign_task(rid, lineup)
            self.assertEqual((ok, err), (False, "task_is_running"), lineup)
        self.assertEqual(self.part(rid)["model"], "")

    def test_a_reviewer_in_the_middle_of_a_review_cannot_change_either(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.running(rid, "claude", "idle")
        self.running(rid, "codex", "working")
        ok, _room, err = dashboard.reassign_task(rid, self.lineup(reviewer_model="gpt-5-high"))
        self.assertEqual((ok, err), (False, "task_is_running"))

    def test_a_terminal_the_record_does_not_name_refuses(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.listed.append({"id": "stray", "alive": True, "meta": {"room": rid}})
        self.ptys["stray"] = FakePty("stray")
        ok, _room, err = dashboard.reassign_task(rid, self.lineup(reviewer_model="gpt-5-high"))
        self.assertEqual((ok, err), (False, "task_is_running"))

    def test_a_stopped_task_reassigns_as_before(self):
        rid = self.room(agents=("claude", "codex"), roles=["engineer", "reviewer"])
        self.running(rid, "claude", "idle")
        self.stop(rid)
        ok, room, err = dashboard.reassign_task(rid, self.lineup(owner_model="opus"))
        self.assertTrue(ok, err)
        self.assertEqual(chatroom.participant(room, "claude")["model"], "opus")
        self.assertNotIn("ptyId", chatroom.participant(room, "claude"))

    def test_the_error_names_what_can_change(self):
        text = ensemble_tools._reassign_error("task_is_running")
        self.assertIn("reviewer's seat and stopped seats can, without stopping the task", text)


if __name__ == "__main__":
    unittest.main()
