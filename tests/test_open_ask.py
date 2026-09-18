"""An ask to the person stays up until it is answered.

Measured on 2026-09-18 (Motors #1, "Selling X5"): the task reported ``blocked``
at 15:55 (a site had logged it out), reported an unrelated ``update`` at 18:07,
and the block left the bell although nobody had logged in; while its agent was
re-checking after each hub restart it read as working, and the 16:51 progress
check told the PO it was "unblocked".

* ``attention._open_to_human``: an update about something else closes nothing;
  the person speaking, a ``completed`` or a clearing report does.
* ``/api/attention`` (``attention.snapshot``): the room stays listed, with the
  ask's text and the time it was asked, whatever its agent's terminal is doing.
* ``digest``: a task is never told as unblocked because its agent went busy or
  stopped.
* ``ensemble_report`` takes ``clears`` on an update.
"""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_hooks
import attention
import chatroom
import dashboard
import digest
import ensemble_tools
from test_digest_news import _Checks

ROOT = Path(__file__).resolve().parent.parent
BLOCKED = "AutoScout24 is logged out.\n\nfabio must log in before I can renew the listing."
IDLE_SCREEN = "● ok\n❯ \n"
BUSY_SCREEN = "● Checking the listings\n\n✻ Working… (12s · esc to interrupt)\n"


class _Room(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)
        self.rid = chatroom.create_room("Selling X5", [{"identity": "claude", "agent": "claude",
                                                         "role": "engineer"}])["id"]

    def report(self, kind, text, **kw):
        time.sleep(0.002)
        return chatroom.record_report(self.rid, "claude", kind, text, **kw)

    def ask(self):
        return attention.open_ask(chatroom.get_room(self.rid, public=False))


class WhatClosesAnAsk(_Room):
    def test_an_update_about_something_else_leaves_the_block_open(self):
        first = self.report("blocked", BLOCKED, heading="**Report — blocked**, sent to the PO (*Motors PO*)")
        self.report("update", "A Facebook group approved the post.")
        ask = self.ask()
        self.assertEqual((ask["kind"], ask["id"], ask["ts"]), ("blocked", first["id"], first["ts"]))
        self.assertTrue(ask["text"].startswith("AutoScout24 is logged out. fabio must log in"))
        self.assertEqual(ask["line"], "AutoScout24 is logged out.")      # never the hub's heading

    def test_the_person_speaking_in_the_chat_closes_it(self):
        self.report("blocked", BLOCKED)
        self.report("update", "Something else.")
        chatroom.post_message(self.rid, "user", "Logged in, go on.")
        self.assertIsNone(self.ask())

    def test_a_completed_report_closes_it(self):
        self.report("question", "Which price?")
        self.report("completed", "Sold.")
        self.assertEqual(self.ask()["kind"], "completed")
        self.assertIsNone(attention.open_ask_now(chatroom.get_room(self.rid, public=False)))

    def test_a_report_that_says_so_closes_it(self):
        self.report("blocked", BLOCKED)
        self.report("update", "The session came back by itself: renewing now.", clears=True)
        self.assertIsNone(self.ask())
        room = chatroom.get_room(self.rid, public=False)
        self.assertTrue(room["messages"][-1]["clears"])
        self.assertNotIn("clears", room["messages"][-2])

    def test_a_question_too_and_a_new_ask_takes_its_place(self):
        self.report("question", "Which price?")
        self.report("update", "Photos uploaded meanwhile.")
        self.assertEqual((self.ask()["kind"], self.ask()["text"]), ("question", "Which price?"))
        self.report("blocked", BLOCKED)
        self.assertEqual(self.ask()["kind"], "blocked")

    def test_a_message_to_the_person_survives_an_update_and_never_hides_a_block(self):
        chatroom.post_message(self.rid, "claude", "Ready to merge?", to="user")
        self.report("update", "Rebased meanwhile.")
        self.assertEqual((self.ask()["kind"], self.ask()["text"]), ("message", "Ready to merge?"))
        self.report("blocked", BLOCKED)
        chatroom.post_message(self.rid, "claude", "By the way, the photos are up.", to="user")
        self.assertEqual(self.ask()["kind"], "blocked")

    def test_a_completed_is_still_ended_by_working_again(self):
        self.report("completed", "Sold.")
        self.report("update", "Tidying the listing up.")
        self.assertIsNone(self.ask())

    def test_an_agent_from_before_clears_existed_just_leaves_it_open(self):
        self.report("blocked", BLOCKED)
        for n in range(3):
            self.report("update", f"milestone {n}")
        self.assertEqual(self.ask()["kind"], "blocked")


class TheBellKeepsIt(_Room):
    """attention.snapshot, which /api/attention serves, over a live terminal."""

    def setUp(self):
        super().setUp()
        agent_hooks.reset()
        self.addCleanup(agent_hooks.reset)
        full = chatroom.get_room(self.rid, public=False)
        full.update(mode="solo", launched=True)
        chatroom.participant(full, "claude")["ptyId"] = "pty-1"
        chatroom.update_room(full)
        self.screen = IDLE_SCREEN
        self.sess = types.SimpleNamespace(
            id="pty-1", alive=lambda: True, tail=lambda: self.screen, last_output=time.time(),
            info=lambda: {"idleSeconds": 2}, last_submit=lambda: self.submitted, death=lambda: None,
            last_answer=0.0, meta={"room": self.rid, "identity": "claude"})
        self.submitted = 0.0
        for patch in (
                mock.patch.object(dashboard.ptyrun, "get",
                                  side_effect=lambda pid: self.sess if pid == "pty-1" else None),
                mock.patch.object(dashboard, "_read_session_files", return_value=[]),
                mock.patch.object(dashboard, "load_session_projects", return_value={}),
                mock.patch.object(dashboard, "load_projects", return_value=[]),
                mock.patch.object(dashboard, "load_labels", return_value={})):
            patch.start()
            self.addCleanup(patch.stop)
        for cache in (attention._SUMMARY_CACHE, attention._ANALYSIS, attention._FIRST_SEEN):
            cache.clear()
            self.addCleanup(cache.clear)

    def item(self):
        attention._ANALYSIS.clear()
        return next((it for it in attention.snapshot(max_age=-1)["items"]
                     if it["roomId"] == self.rid), None)

    def busy(self):
        self.screen = BUSY_SCREEN
        self.sess.last_output = time.time()
        agent_hooks.record({"room": self.rid, "identity": "claude", "ptyId": "pty-1",
                            "event": {"hook_event_name": "UserPromptSubmit", "session_id": "s1"}},
                           lambda pid: (self.rid, "claude"))

    def test_blocked_then_an_unrelated_update_is_still_waiting_for_the_person(self):
        asked = self.report("blocked", BLOCKED)
        self.report("update", "A Facebook group approved the post.")
        it = self.item()
        self.assertEqual((it["state"], it["cause"]), ("blocked", "reported"))
        self.assertIn("AutoScout24 is logged out", it["quote"])
        self.assertIn("AutoScout24 is logged out", it["reason"])
        self.assertEqual((it["since"], it["askId"]), (asked["ts"], asked["id"]))   # "since 15:55"

    def test_an_agent_busy_re_checking_does_not_hide_its_own_ask(self):
        asked = self.report("blocked", BLOCKED)
        self.busy()
        # What the hub types (the restart line, a doorbell) submits, and answers nothing.
        self.submitted = time.time() + 5
        it = self.item()
        self.assertEqual((it["state"], it["since"]), ("blocked", asked["ts"]))
        # A wall read off the screen is another matter: a busy Claude is never blocked by one.
        self.assertIsNone(attention._classify_agent(
            {"status": "active", "mode": "solo", "participants": []},
            {"identity": "claude", "agent": "claude"},
            {"ptyId": "p", "alive": True, "tail": "", "idleSeconds": 2, "lastSubmit": 0.0, "death": None,
             "scan": {"block": ("hit its usage limit", "usage_limit", "limit"), "busy": True, "prompt": False},
             "claudeStatus": "busy", "claudeStatusAt": 0.0, "hook": None, "lastOutput": 0.0},
            900, time.time()))

    def test_it_leaves_after_a_user_message_a_completed_or_a_clearing_report(self):
        for close in (lambda: chatroom.post_message(self.rid, "user", "Logged in."),
                      lambda: self.report("update", "It came back by itself.", clears=True)):
            self.report("blocked", BLOCKED)
            self.report("update", "Something else.")
            self.assertEqual(self.item()["state"], "blocked")
            close()
            self.assertIsNone(self.item())
        self.report("blocked", BLOCKED)
        self.report("completed", "Sold.")
        it = self.item()
        self.assertEqual((it["state"], it["quote"]), ("waiting_for_you", "Sold."))

    def test_a_one_agent_task_is_answered_by_a_person_in_its_terminal(self):
        asked = self.report("question", "Which price?")
        self.assertEqual(self.item()["state"], "waiting_for_you")
        room = dashboard._annotate_room_liveness(chatroom.get_room(self.rid))
        self.assertEqual((room["openAsk"]["line"], room["openAsk"]["ts"]), ("Which price?", asked["ts"]))
        self.sess.last_answer = time.time() + 5
        self.assertIsNone(self.item())
        self.assertNotIn("openAsk", dashboard._annotate_room_liveness(chatroom.get_room(self.rid)))

    def test_the_page_and_the_po_answer_it_the_hubs_own_lines_do_not(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        i = src.index('if p == "/api/pty/input":')
        self.assertIn("sess.last_answer = time.time()", src[i:src.index('if p == "/api/pty/resize":', i)])
        # Nothing the hub types by itself goes through there, or marks an answer.
        ring = src[src.index("    def _ring(self, room_id"):src.index("    def _ring_report(")]
        self.assertNotIn("last_answer", ring)
        self.assertEqual(dashboard.hub_input_kind(dashboard.PO_MESSAGE_PREFIX + " he logged in")["kind"], "human")
        self.assertNotEqual(dashboard.hub_input_kind(dashboard.RESTART_NOTE)["kind"], "human")


class TheProgressCheck(_Checks):
    def test_a_blocked_task_whose_agent_went_busy_is_not_unblocked(self):
        # 15:55 it reports blocked; the digest tells the PO.
        self.report("blocked", BLOCKED)
        self.assertTrue(self.check(attention="blocked", ask="blocked"))
        self.assertIn("attention none → blocked", self.last_what())
        # 16:51 the hub restarted and typed into it: it is busy re-checking, so
        # nothing on its screen says blocked. The ask is as open as it was.
        self.assertFalse(self.check(attention="", ask="blocked"))
        self.assertFalse(self.check(attention="", ask="blocked"))
        # Something else is news meanwhile (a restart stopped it): the digest
        # that goes out still does not say the block is over.
        self.assertTrue(self.check(status="stopped"))
        self.assertEqual(self.last_what(), ["status running → stopped"])
        self.assertTrue(self.check(status="running", head="def5678"))
        self.assertEqual(self.last_what(), ["status stopped → running", "new commits"])
        self.assertNotIn("blocked →", self.sent[-1]["text"])
        self.assertIn("its blocked report is still open", self.sent[-1]["text"])
        # It closes (the person answered, or a report cleared it): now it is over.
        self.assertTrue(self.check(attention="", ask=""))
        self.assertEqual(self.last_what(), ["attention blocked → none"])
        self.assertFalse(self.check())

    def test_the_facts_carry_the_open_ask(self):
        self.report("blocked", BLOCKED)
        self.report("update", "A Facebook group approved the post.")
        room = chatroom.get_room(self.room, public=False)
        with mock.patch.object(digest, "_git_facts", return_value={}):
            facts = digest._task_facts(room, {}, {}, time.time())
        self.assertEqual((facts["attention"], facts["ask"]), ("", "blocked"))
        self.assertIn("AutoScout24 is logged out", facts["askText"])
        chatroom.post_message(self.room, "user", "Logged in.")
        with mock.patch.object(digest, "_git_facts", return_value={}):
            self.assertEqual(digest._task_facts(chatroom.get_room(self.room, public=False),
                                                {}, {}, time.time())["ask"], "")

    def test_a_wall_on_the_screen_is_still_over_when_it_goes(self):
        self.assertTrue(self.check(attention="blocked"))        # a usage limit: no ask
        self.assertTrue(self.check(attention=""))
        self.assertEqual(self.last_what(), ["attention blocked → none"])


class TheReportTool(_Room):
    def call(self, **args):
        room = chatroom.get_room(self.rid, public=False)
        ctx = {"room": room, "identity": "claude", "part": chatroom.participant(room, "claude"),
               "projectId": ""}
        with mock.patch.object(ensemble_tools, "_projects", return_value={}), \
                mock.patch.object(ensemble_tools, "_project_po", return_value=None):
            return ensemble_tools._report(ctx, args, None)

    def test_clears_goes_on_an_update_only(self):
        self.call(kind="blocked", text=BLOCKED)
        self.call(kind="update", text="Something else.")
        self.assertEqual(self.ask()["kind"], "blocked")
        self.call(kind="update", text="It came back by itself.", clears=True)
        self.assertIsNone(self.ask())
        self.call(kind="question", text="Which price?", clears=True)
        self.assertEqual(self.ask()["kind"], "question")
        self.assertNotIn("clears", chatroom.get_room(self.rid, public=False)["messages"][-1])

    def test_the_tool_and_the_skill_say_how(self):
        tool = next(t for t in ensemble_tools._ALL_TOOLS if t["name"] == "ensemble_report")
        self.assertEqual(tool["inputSchema"]["properties"]["clears"]["type"], "boolean")
        self.assertEqual(tool["inputSchema"]["required"], ["kind", "text"])
        self.assertIn("clears: true", tool["description"])
        skill = (ROOT / "skills" / "ensemble" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("clears: true", skill)


if __name__ == "__main__":
    unittest.main()
