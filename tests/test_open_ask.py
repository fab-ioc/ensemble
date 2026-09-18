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

import json
import tempfile
import threading
import time
import types
import unittest
import urllib.request
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
BLOCKED = "AutoScout24 is logged out.\n\nthe operator must log in before I can renew the listing."
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
        self.assertTrue(ask["text"].startswith("AutoScout24 is logged out. the operator must log in"))
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


class _Bell(_Room):
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
            write=lambda data: self.written.append(data) or True,
            meta={"room": self.rid, "identity": "claude"})
        self.submitted = 0.0
        self.written: list[str] = []
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


class TheBellKeepsIt(_Bell):
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
        self.assertTrue(dashboard.note_answer(self.rid, "claude"))
        self.assertIsNone(self.item())
        self.assertNotIn("openAsk", dashboard._annotate_room_liveness(chatroom.get_room(self.rid)))
        # With nothing open there is nothing to answer: a person types many lines.
        self.assertFalse(dashboard.note_answer(self.rid, "claude"))

    def test_a_prompt_on_its_terminal_does_not_hide_the_asks_text_and_time(self):
        asked = self.report("blocked", BLOCKED)
        self.report("update", "Something else.")
        agent_hooks.record({"room": self.rid, "identity": "claude", "ptyId": "pty-1",
                            "event": {"hook_event_name": "PermissionRequest", "session_id": "s1"}},
                           lambda pid: (self.rid, "claude"))
        it = self.item()
        self.assertEqual((it["state"], it["cause"]), ("waiting_for_you", "reported"))
        self.assertEqual((it["askedAt"], it["askId"]), (asked["ts"], asked["id"]))
        self.assertIn("AutoScout24 is logged out", it["quote"])
        self.assertIn("waiting on your answer to a prompt", it["reason"])
        self.assertIn("its blocked report is still open: “AutoScout24 is logged out", it["reason"])

    def test_a_prompt_read_off_the_screen_does_not_hide_it_either(self):
        asked = self.report("question", "Which price?")
        self.screen = "● Edit file?\n❯ 1. Yes\n  2. No\n\nDo you want to proceed?\n"
        self.assertTrue(attention.analyse(self.screen)["prompt"])
        it = self.item()
        self.assertEqual((it["state"], it["askedAt"], it["quote"]),
                         ("waiting_for_you", asked["ts"], "Which price?"))
        self.assertIn("its question is still open", it["reason"])

    def test_a_wall_on_its_screen_does_not_replace_the_report(self):
        asked = self.report("blocked", BLOCKED)
        wall = ("hit its usage limit", "usage_limit", "You've hit your limit")
        with mock.patch.object(attention, "_analyse_live",
                               return_value=("", {"block": wall, "busy": False, "prompt": False})):
            it = self.item()
        self.assertEqual((it["state"], it["cause"]), ("blocked", "usage_limit"))      # the wall is said
        self.assertIn("hit its usage limit: “You've hit your limit”", it["reason"])
        self.assertIn("its blocked report is still open: “AutoScout24", it["reason"])  # and so is the ask
        self.assertEqual((it["askedAt"], it["askId"]), (asked["ts"], asked["id"]))
        self.assertIn("AutoScout24 is logged out", it["quote"])
        # Without an ask the wall is what it was.
        chatroom.post_message(self.rid, "user", "Logged in.")
        with mock.patch.object(attention, "_analyse_live",
                               return_value=("", {"block": wall, "busy": False, "prompt": False})):
            it = self.item()
        self.assertEqual((it["quote"], it["reason"].count("still open")), ("You've hit your limit", 0))
        self.assertNotIn("askedAt", it)

    def test_a_prompt_after_a_completed_is_just_the_prompt(self):
        self.report("completed", "Sold.")
        agent_hooks.record({"room": self.rid, "identity": "claude", "ptyId": "pty-1",
                            "event": {"hook_event_name": "PermissionRequest", "session_id": "s1"}},
                           lambda pid: (self.rid, "claude"))
        it = self.item()
        self.assertEqual(it["state"], "waiting_for_you")
        self.assertNotIn("quote", it)


class AnsweredInTheTerminal(_Bell):
    """POST /api/pty/input on a real handler: the page's terminal and the PO's
    tell answer a one-agent task's ask; the restart helper's note does not;
    and the answer outlives the terminal."""

    @classmethod
    def setUpClass(cls):
        cls.quiet = mock.patch.object(dashboard.Handler, "log_message", lambda *a: None)
        cls.quiet.start()
        cls.server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.quiet.stop()

    def type(self, data, **more):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_address[1]}/api/pty/input",
            data=json.dumps({"id": "pty-1", "data": data, **more}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)

    def facts(self):
        with mock.patch.object(digest, "_git_facts", return_value={}):
            return digest._task_facts(chatroom.get_room(self.rid, public=False), {}, {}, time.time())

    def test_the_restart_helpers_note_and_its_enter_answer_nothing(self):
        helper = (ROOT / "restart-hub.ps1").read_text(encoding="utf-8")
        sends = [ln for ln in helper.splitlines() if "Post '/api/pty/input'" in ln]
        self.assertEqual(len(sends), 2)
        self.assertTrue(all("hub = $true" in ln for ln in sends), sends)
        self.report("blocked", BLOCKED)
        note = "[from the restart helper, not a person] The hub was restarted at 16:51."
        self.assertEqual(dashboard.hub_input_kind(note)["kind"], "helper")
        # As the helper sends it, and as one from before `hub` would: the note, then the Enter.
        for flag in ({"hub": True}, {}):
            self.type(note, **flag)
            self.type("\r", **flag)
            self.assertEqual(self.item()["state"], "blocked")
            self.assertIn("openAsk", dashboard._annotate_room_liveness(chatroom.get_room(self.rid)))
        self.assertEqual(self.written, [note, "\r"] * 2)
        self.assertNotIn("answeredAt", chatroom.participant(chatroom.get_room(self.rid, public=False), "claude"))

    def test_a_person_or_the_po_typing_into_it_answers_it_for_the_bell_the_chat_and_the_digest(self):
        for body in ("Logged in, go on.", dashboard.PO_MESSAGE_PREFIX + " he logged in, carry on"):
            self.report("blocked", BLOCKED)
            self.assertEqual(self.facts()["ask"], "blocked")
            self.type(body)                 # the text, then its Enter, as the page and tell.py send them
            self.assertEqual(self.item()["state"], "blocked")
            self.type("\r")
            self.assertIsNone(self.item())
            self.assertNotIn("openAsk", dashboard._annotate_room_liveness(chatroom.get_room(self.rid)))
            self.assertEqual(self.facts()["ask"], "")

    def test_the_progress_check_tells_the_end_of_a_block_answered_in_the_terminal(self):
        self.report("blocked", BLOCKED)
        told = {"toldAttention": "blocked"}
        self.assertEqual(digest._attention_news(told, {**self.facts(), "attention": ""}), "")
        self.type("Logged in, go on.\r")
        self.assertEqual(digest._attention_news(told, {**self.facts(), "attention": ""}),
                         "attention blocked → none")

    def test_an_answered_ask_stays_answered_across_a_restart_and_a_rotation(self):
        self.report("question", "Which price?")
        self.type("9,500\r")
        self.assertIsNone(self.item())
        # The hub restarts, or the task is handed to a fresh session: another
        # terminal, another session id, nothing of the old one in memory.
        chatroom.patch_participant(self.rid, "claude", {"ptyId": "pty-2", "sessionId": "fresh",
                                                        "rotatedAt": 0})
        fresh = types.SimpleNamespace(**{**vars(self.sess), "id": "pty-2"})
        with mock.patch.object(dashboard.ptyrun, "get",
                               side_effect=lambda pid: fresh if pid == "pty-2" else None):
            self.assertIsNone(self.item())
            self.assertNotIn("openAsk", dashboard._annotate_room_liveness(chatroom.get_room(self.rid)))
            self.assertEqual(self.facts()["ask"], "")
            # What it asks next is as visible as ever.
            again = self.report("blocked", BLOCKED)
            self.assertEqual((self.item()["state"], self.item()["askedAt"]), ("blocked", again["ts"]))
            self.assertEqual(self.facts()["ask"], "blocked")

    def test_in_a_team_room_the_terminal_answers_nothing(self):
        full = chatroom.get_room(self.rid, public=False)
        full["mode"] = "collab"
        chatroom.update_room(full)
        self.report("question", "Which price?")
        self.type("9,500\r")
        self.assertEqual(self.item()["state"], "waiting_for_you")


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
