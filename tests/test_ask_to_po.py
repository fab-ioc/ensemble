"""Task → PO → CEO (ED-138).

Measured on 2026-09-30 (OP-140, a trading task with an owner and a reviewer on
mention): at 03:16 it sent its PO a ``question``; the PO answered at 03:17 (a
spec amendment and a line typed into the owner); at 05:00 the owner told the
chat "starting phase (b)" ``to: "user"`` and then sat idle. The CEO's list
said "Waiting for you" for hours, and nobody noticed the stall.

* ``attention._open_to_human``: in a project with a PO an ask is to the PO,
  never on the CEO's bell / Needs you / digest; it closes when the PO or the
  CEO answers (a typed line, in a solo task and a team alike; a spec
  amendment; a chat message) or when the owner carries on (an update, a word
  to the person). A message to the person asks nobody. Without a PO nothing
  changes.
* ``stall.py``: an idle owner with nothing open is nudged once, then its PO is
  told once, per episode.
"""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import attention
import chatroom
import dashboard
import digest
import ensemble_tools
import stall
from test_open_ask import _Bell, BLOCKED

PO_ROUTE = {"roomId": "", "identity": "po", "project": "Trading"}


class _PoBell(_Bell):
    """A task whose project has a PO (its own room), solo or a team."""
    solo = True

    def setUp(self):
        super().setUp()
        self.po_rid = chatroom.create_room("Planning", [{"identity": "po", "agent": "claude",
                                                          "role": "ProductOwner"}])["id"]
        self.po = self.po_rid
        full = chatroom.get_room(self.rid, public=False)
        if not self.solo:
            full["mode"] = "collab"
            full["participants"].append({"identity": "claude-2", "kind": "agent", "agent": "claude",
                                         "role": "reviewer", "ptyId": ""})
        chatroom.update_room(full)
        p = mock.patch.object(dashboard, "room_po_id",
                              side_effect=lambda room, *a, **k: self.po if (room or {}).get("id") == self.rid else "")
        p.start()
        self.addCleanup(p.stop)

    def ask_po(self, kind="question", text="Amendment 73: which of the three answers?"):
        time.sleep(0.002)
        return chatroom.record_report(self.rid, "claude", kind, text, {**PO_ROUTE, "roomId": self.po_rid},
                                      heading=f"**Report — {kind}**, sent to the PO (*Planning*)")

    def room(self):
        return chatroom.get_room(self.rid, public=False)


class AnAskHasAnAddressee(_PoBell):
    def test_an_ask_to_the_po_is_not_the_ceos(self):
        asked = self.ask_po()
        self.assertEqual((self.ask()["to"], self.ask()["id"]), ("po", asked["id"]))
        self.assertIsNone(self.item())                       # no bell, no Needs you
        w = attention.waiting_on_po()[self.rid]
        self.assertEqual((w["kind"], w["since"]), ("question", asked["ts"]))
        self.assertEqual(ensemble_tools._po_wait_view(self.room())["waitingOn"]["who"], "po")
        with mock.patch.object(digest, "_git_facts", return_value={}):
            f = digest._task_facts(self.room(), {}, {}, time.time())
        self.assertEqual((f["ask"], f["askTo"]), ("question", "po"))
        text = digest.plain_facts({"id": "p", "name": "Trading"},
                                  [{**f, "status": "running", "column": "inprogress", "idleSeconds": 5,
                                    "attention": "", "branch": "", "reportKind": ""}], [], 0)
        self.assertIn("is waiting for you (the PO)", text)
        self.assertNotIn("open to", text)

    def test_without_a_po_it_is_the_ceos_as_before(self):
        self.po = ""
        self.ask_po()
        self.assertEqual(self.ask()["to"], "user")
        self.assertEqual(self.item()["state"], "waiting_for_you")
        self.assertNotIn(self.rid, attention.waiting_on_po())

    def test_a_blocked_report_to_the_po_is_the_pos_too(self):
        self.ask_po("blocked", BLOCKED)
        self.assertIsNone(self.item())
        self.assertEqual(attention.waiting_on_po()[self.rid]["kind"], "blocked")

    def test_a_completed_goes_to_the_po_and_waits_for_nobody(self):
        self.ask_po("completed", "Done, on the branch.")
        self.assertIsNone(self.item())
        self.assertNotIn(self.rid, attention.waiting_on_po())


class ThePoAnswering(_PoBell):
    def test_a_typed_answer_closes_it(self):
        self.ask_po()
        self.assertTrue(dashboard.note_answer(self.rid, "claude"))
        self.assertIsNone(self.ask())
        self.assertNotIn(self.rid, attention.waiting_on_po())

    def test_a_spec_amendment_closes_it(self):
        self.ask_po()
        time.sleep(0.002)
        with mock.patch.object(dashboard, "_write_task_json"):
            ok, _, why = dashboard.update_task(self.rid, spec="PO RULING 2: answer (b).")
        self.assertTrue(ok, why)
        self.assertIsNone(self.ask())
        # The same text again is no amendment: a later ask stays open.
        self.ask_po()
        with mock.patch.object(dashboard, "_write_task_json"):
            dashboard.update_task(self.rid, spec="PO RULING 2: answer (b).")
        self.assertEqual(self.ask()["to"], "po")

    def test_a_chat_message_closes_it(self):
        self.ask_po()
        chatroom.post_message(self.rid, "user", "Take (b).")
        self.assertIsNone(self.ask())

    def test_the_owner_carrying_on_closes_it(self):
        self.ask_po()
        chatroom.record_report(self.rid, "claude", "update", "Phase (a) coding.", {**PO_ROUTE, "roomId": self.po_rid})
        self.assertIsNone(self.ask())
        self.ask_po()
        chatroom.post_message(self.rid, "claude", "Accepted, coding phase (a) now.", to="user",
                              wait_for_human=False)
        self.assertIsNone(self.ask())

    def test_team_chatter_that_woke_nobody_answers_nothing(self):
        # Review 1: a message to everyone whose teammate was stopped is not
        # the owner carrying on.
        self.ask_po()
        time.sleep(0.002)
        chatroom.post_message(self.rid, "claude", "Notes for whoever reviews next.", to="all",
                              wait_for_human=False)
        self.assertEqual(self.ask()["to"], "po")

    def test_an_update_leaves_an_ask_to_the_ceo_open(self):
        self.po = ""
        self.ask_po()
        chatroom.record_report(self.rid, "claude", "update", "Photos uploaded.")
        self.assertEqual(self.ask()["kind"], "question")


class TheTaskList(_PoBell):
    def test_a_list_reads_who_waits_once_not_per_row(self):
        for n in range(3):
            chatroom.create_room(f"Other {n}", [{"identity": "claude", "agent": "claude", "role": "engineer"}])
        self.ask_po()
        real = attention.waiting_on_po
        with mock.patch.object(attention, "waiting_on_po", side_effect=real) as waits, \
                mock.patch.object(ensemble_tools, "_projects", return_value={}), \
                mock.patch.object(ensemble_tools, "_attention_by_room", return_value={}):
            out = ensemble_tools._list_tasks({"projectId": ""}, {"projectId": "*"}, None)
        self.assertEqual(waits.call_count, 1)
        rows = out["tasks"] if isinstance(out, dict) else out
        mine = next(r for r in rows if r["id"] == self.rid)
        self.assertEqual(mine["waitingOn"]["who"], "po")


class TeamThePoAnswering(ThePoAnswering):
    solo = False


class TeamAnAskHasAnAddressee(AnAskHasAnAddressee):
    solo = False


class AMessageToTheUserAsksNobody(_PoBell):
    solo = False

    def send(self, text, to="user"):
        fake = types.SimpleNamespace(_ring_recipients=lambda *a: None)
        with mock.patch.object(dashboard, "_history_nudge"):
            return dashboard.Handler._mcp_tool_call(
                fake, "chat_send", {"message": text, "to": to}, self.rid, "claude",
                lambda r: r, lambda code, msg: {"error": msg})

    def test_chat_send_to_the_user_in_a_po_project(self):
        res = self.send("#140 interim: starting phase (b).")
        note = res["content"][0]["text"]
        self.assertIn("ensemble_report", note)
        self.assertNotIn("paused", note)
        room = self.room()
        self.assertEqual(room["status"], "active")
        self.assertIsNone(self.ask())
        self.assertIsNone(self.item())

    def test_without_a_po_it_still_waits_for_the_user(self):
        self.po = ""
        self.send("Ready to merge?")
        self.assertEqual(self.room()["status"], "waiting_human")
        self.assertEqual(self.ask()["kind"], "message")

    def test_a_room_left_waiting_by_an_older_hub_reads_as_running(self):
        chatroom.post_message(self.rid, "claude", "starting phase (b)", to="user")   # the old rule
        room = self.room()
        self.assertEqual(room["status"], "waiting_human")
        self.assertIsNone(self.item())
        with mock.patch.object(dashboard, "_room_is_live", return_value=True):
            self.assertEqual(ensemble_tools._status(room), "running")


class _Stall(_PoBell):
    solo = False

    def setUp(self):
        super().setUp()
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.idle = 700.0
        self.sess.info = lambda: {"idleSeconds": self.idle}
        self.typed: list[str] = []
        self.review = None      # the reviewer's terminal, while it reviews
        rev_sess = types.SimpleNamespace(alive=lambda: True, info=lambda: {"idleSeconds": 0.5},
                                         last_submit=lambda: 0.0)
        full = self.room()
        full["workflow"] = "inprogress"
        chatroom.update_room(full)

        def pty(part):
            if part.get("ptyId") == "pty-1":
                return self.sess
            if part.get("identity") == "claude-2" and self.review:
                return rev_sess
            return None
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", Path(d.name)),
                  mock.patch.object(dashboard, "_room_is_live", return_value=True),
                  mock.patch.object(dashboard.rotation, "_pty", side_effect=pty),
                  mock.patch.object(dashboard.rotation, "_transcript_of",
                                    return_value=(None, lambda p: {"turnOver": True})),
                  mock.patch.object(dashboard, "_type_input",
                                    side_effect=lambda s, t: self.typed.append(t) or True)):
            p.start()
            self.addCleanup(p.stop)

    def told(self):
        return [m for m in chatroom.get_room(self.po_rid, public=False)["messages"]
                if m.get("from") == stall.SENDER and "stalled since" in (m.get("text") or "")]


class Stalls(_Stall):
    def test_nudged_once_then_the_po_told_once(self):
        t = time.time()
        self.assertEqual(stall.tick(t), {self.rid: "nudged"})
        self.assertEqual(self.typed, [stall.NUDGE])
        self.assertEqual(ensemble_tools._po_wait_view(self.room())["stalled"]["nudgedAt"], t)
        self.assertEqual(stall.tick(t + 60), {})             # not yet a grace period on
        self.assertIn("PO told", stall.tick(t + stall.GRACE_S + 1)[self.rid])
        self.assertEqual(len(self.told()), 1)
        self.assertEqual(stall.tick(t + 3 * stall.GRACE_S), {})
        self.assertEqual((len(self.told()), len(self.typed)), (1, 1))
        # Something new is a new episode.
        time.sleep(0.002)
        chatroom.post_message(self.rid, "user", "Any news?")
        self.assertEqual(stall.tick(t + 4 * stall.GRACE_S), {self.rid: "nudged"})

    def test_not_before_the_grace_period(self):
        self.idle = stall.GRACE_S - 5
        self.assertEqual(stall.tick(), {})

    def test_waiting_on_its_po_is_not_a_stall(self):
        self.ask_po()
        self.assertEqual(stall.tick(), {})

    def test_a_review_running_is_not_a_stall(self):
        self.review = True
        self.assertEqual(stall.tick(), {})
        self.review = None
        self.assertEqual(stall.tick(), {self.rid: "nudged"})

    def test_handed_to_a_teammate_in_review_or_without_a_po_is_not_a_stall(self):
        chatroom.post_message(self.rid, "claude", "@claude-2 please review abc123", to="claude-2")
        self.assertEqual(stall.tick(), {})
        chatroom.post_message(self.rid, "user", "go on")
        with mock.patch.object(dashboard, "workflow_of", return_value="inreview"):
            self.assertEqual(stall.tick(), {})
        self.po = ""
        self.assertEqual(stall.tick(), {})

    def test_the_nudge_is_a_hub_line(self):
        self.assertEqual(dashboard.hub_input_kind(stall.NUDGE)["kind"], "stalled")


class OP140(_Stall):
    """question → the PO's typed answer → the owner's word to the user → idle."""

    def test_it_reads_as_it_should(self):
        self.ask_po("question", "Amendment 73 draft: three answers needed.")
        self.assertIsNone(self.item())
        self.assertTrue(dashboard.note_answer(self.rid, "claude"))      # 03:17, typed by the PO
        time.sleep(0.002)
        chatroom.post_message(self.rid, "claude", "Re PO ruling 2 (#140): accepted, coding phase (a).")
        time.sleep(0.002)
        # 05:00, sent as the hub of the day handled it: the room says waiting_human.
        chatroom.post_message(self.rid, "claude", "#140 interim: starting phase (b). No reply is needed.",
                              to="user")
        self.assertIsNone(self.ask())
        self.assertIsNone(self.item())                                  # nothing for the CEO
        self.assertNotIn(self.rid, attention.waiting_on_po())
        with mock.patch.object(dashboard, "_room_is_live", return_value=True):
            self.assertEqual(ensemble_tools._status(self.room()), "running")
        t = time.time()
        self.assertEqual(stall.tick(t), {self.rid: "nudged"})
        self.assertIn("PO told", stall.tick(t + stall.GRACE_S + 1)[self.rid])
        self.assertEqual(stall.tick(t + 2 * stall.GRACE_S + 2), {})
        self.assertEqual(len(self.told()), 1)
        self.assertIn(f"stalled since", self.told()[0]["text"])
        self.assertIsNone(self.item())                                  # still nothing for the CEO


if __name__ == "__main__":
    unittest.main()
