"""Threads in a PO chat (threads.py, #210; #367): the hub places every
balloon of a PO chat from facts it already has, and only the person starts a
thread.

* a message sent as a new thread (the box's New thread), or written in a
  thread from its box (``sent``, ``anchors``), is in that thread, and so are
  the points it makes; everything else is in the main chat, a one-balloon
  entry each: points, ``Re Pn`` replies, card answers, PO answers;
* a PO message goes where the point it answers was last written (``Re Pn``,
  the ledger's answers), else where the input before it is; after a hub note
  it joins Team activity, a handover line outside a thread too;
* a task's report is in the thread of the person's point about the task
  (its Work log, with the PO's note on it), else in Team activity; a card
  answered in a thread is in it;
* the channel (#234) is the whole chat but a thread's replies: its first
  message, what the person typed in the chat's own box, a reply sent to the
  chat too, and the hub's and tasks' traffic no thread claimed;
* an old hub line the provenance journal did not claim is a hub line, not the
  person's; the side record keeps what was seen, anchors and sends.
"""
from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import points
import threads
from tests import test_points as tp

T0 = 1_800_000_000.0


def human(mid, ts, text, pts=(), re_=()):
    return {"mid": mid, "sid": "s", "role": "user", "kind": "human", "ts": T0 + ts,
            "turn": {"role": "user", "text": text}, "pts": list(pts), "re": list(re_),
            "words": points.norm_text(points.strip_point_lines(text))[:400]}


def po(mid, ts, text, re_=(), ask=False, live=False):
    return {"mid": mid, "sid": "s", "role": "agent", "ts": T0 + ts,
            "turn": {"role": "assistant", "text": text}, "re": list(re_), "ask": ask, "live": live}


def note(mid, ts, kind="digest"):
    return {"mid": mid, "sid": "s", "role": "user", "kind": kind, "ts": T0 + ts,
            "turn": {"role": "user", "text": f"[{kind}] something"}}


def report(mid, ts, task, title=""):
    return {"mid": mid, "sid": "s", "role": "user", "kind": "report", "ts": T0 + ts,
            "turn": {"role": "user", "text": "[report] done"}, "task": task, "taskTitle": title}


def point(pid, mid, text, ts=0, **kw):
    return {"id": pid, "mid": mid, "text": text, "createdAt": T0 + ts, "state": "open", **kw}


def side(**kw):
    return {**threads._empty_side(), **kw}


def new_row(mid, key="kn", at=0):
    """A message sent from the main box as a new thread (#367, note_send ``new``)."""
    return {"thread": "", "key": key, "mid": mid, "at": T0 + at, "also": False, "pids": [], "new": True}


class AssignTest(unittest.TestCase):
    def run_(self, recs, pts, sd=None, title=None):
        return threads.assign(recs, {"points": pts}, sd or side(), title)

    def assertNoThread(self, out):
        """No thread (#367): every entry but Team activity is one balloon."""
        self.assertEqual({t: th["members"] for t, th in out["threads"].items()
                          if t != "team:activity" and len(th["members"]) > 1}, {})

    def test_a_point_in_the_main_chat_makes_no_thread_one_sent_as_new_does(self):
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done.")]
        pts = [point("P1", "s:0", "make the chart wider")]
        out = self.run_(recs, pts)
        self.assertNoThread(out)
        self.assertEqual(out["of"], {"s:0": ["P1"], "s:1": ["m:s:1"]})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        # Sent as a new thread (the box's New thread, #367): the answer joins it.
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        th = out["threads"]["P1"]
        self.assertEqual(th["members"], ["s:0", "s:1"])
        self.assertEqual(th["title"], "make the chart wider")
        self.assertEqual(out["parent"], {"s:0": ["P1"]})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])

    def test_a_re_pn_reply_and_its_answer_in_the_main_chat_make_no_thread(self):
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Re P1: done", re_=["P1"]),
                human("s:2", 10, "Re P1: wider still", re_=["P1"]), po("s:3", 12, "Re P1: wider now", re_=["P1"])]
        out = self.run_(recs, [point("P1", "s:0", "make the chart wider", followUps=["s:2"])])
        self.assertNoThread(out)
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2", "s:3"])
        # Its answers are found for P1 all the same (quick search, #367).
        self.assertEqual(out["answered"], {"P1": ["s:1", "s:3"]})

    def test_a_hub_note_and_its_answer_live_in_team_activity(self):
        out = self.run_([human("s:0", 0, "a"), note("s:1", 5), po("s:2", 6, "Digest read.")],
                        [point("P1", "s:0", "a")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:1", "s:2"])

    def test_review_comments_are_an_entry_each_and_a_new_thread_holds_them_all(self):
        recs = [human("s:0", 0, "## Review comments (2)\n**1.** one\n**2.** two"),
                po("s:1", 5, "Re P2: yes\n\nRe P3: no", re_=["P2", "P3"])]
        pts = [point("P2", "s:0", "**1.** one"), point("P3", "s:0", "**2.** two")]
        out = self.run_(recs, pts)
        self.assertEqual(out["parent"]["s:0"], ["P2", "P3"])
        self.assertEqual(out["threads"]["P2"]["title"], "one")
        # In the main chat the answer to both is a message of its own.
        self.assertEqual(out["of"]["s:1"], ["m:s:1"])
        self.assertNoThread(out)
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        # Sent as a new thread: one thread holds both comments and the answer.
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(out["parent"]["s:0"], ["P2"])
        self.assertEqual(out["threads"]["P2"]["points"], ["P2", "P3"])
        self.assertEqual(out["of"]["s:1"], ["P2"])
        self.assertEqual(out["also"], {})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])

    def test_a_follow_up_point_and_a_re_message_typed_in_the_main_box_make_no_thread(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "answer"),
                human("s:2", 10, "> answer\n\nwhy?"), po("s:3", 12, "because"),
                human("s:4", 20, "Re P1: still wrong", re_=["P1"])]
        pts = [point("P1", "s:0", "a", followUps=["s:4"]),
               point("P4", "s:2", "> answer\n\nwhy?", 10, replyTo="P1")]
        out = self.run_(recs, pts)
        # No chain of their own (#367): each is where it was written.
        self.assertEqual(list(out["threads"]), ["P1", "m:s:1", "P4", "m:s:3", "m:s:4"])
        self.assertNoThread(out)
        self.assertEqual(threads._channel({**out, "recs": recs}), [r["mid"] for r in recs])
        # Written in P1's thread (its box, the points anchored by note_send): there.
        sd = side(sent=[new_row("s:0"), self.sent_row("> answer\n\nwhy?", 10, mid="s:2", key="k2"),
                        self.sent_row("Re P1: still wrong", 20, mid="s:4", key="k4")], anchors={"P4": "P1"})
        out = self.run_(recs, pts, sd)
        self.assertEqual(list(out["threads"]), ["P1"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2", "s:3", "s:4"])
        self.assertEqual(out["threads"]["P1"]["points"], ["P1", "P4"])

    def test_a_card_answer_is_in_the_cards_thread_only_when_the_person_made_it(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "Ask: which?\n- x\n- y", ask=True),
                human("s:2", 10, "x")]
        pts = [point("P1", "s:0", "a"), point("P2", "s:2", "x", 10, key="ask:s:1:0")]
        out = self.run_(recs, pts)
        # In the main chat: the card and its answer are messages of their own.
        self.assertNoThread(out)
        self.assertEqual(out["of"]["s:2"], ["P2"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2"])
        # In a thread the person started: the card and its answer are in it
        # (and the card in the Inbox, not repeated in the chat).
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(out["threads"]["P1"]["points"], ["P1", "P2"])
        self.assertEqual(out["also"], {})

    def test_a_reply_sent_from_a_thread_is_in_it(self):
        recs = [report("s:0", 0, "#12", "Wider chart"), human("s:1", 5, "looks good")]
        out = self.run_(recs, [point("P7", "s:1", "looks good", 5)], side(anchors={"P7": "task:#12"}))
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:0"])
        self.assertEqual(out["threads"]["P7"]["members"], ["s:1"])
        # The report nobody's thread claimed is Team activity, in the chat.
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        self.assertEqual(out["threads"]["team:activity"]["task"], "")

    def test_reply_in_thread_on_a_loose_balloon_makes_it_the_parent(self):
        recs = [note("s:0", 0), po("s:1", 1, "Digest: all quiet"), human("s:2", 5, "and #9?")]
        out = self.run_(recs, [point("P8", "s:2", "and #9?", 5)], side(anchors={"P8": "m:s:1"}))
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:0", "s:1"])
        # Also on a Team activity balloon: it becomes the thread's first message.
        self.assertEqual(out["threads"]["m:s:1"]["members"], ["s:1", "s:2"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        self.assertEqual(threads._turn(idx, "s:1")["thr"], ["m:s:1"])

    def test_a_report_goes_to_the_thread_of_the_point_about_its_task_else_team_activity(self):
        recs = [human("s:0", 0, "build it"), po("s:1", 5, "Re P1 (planned #12): started"),
                report("s:2", 50, "#12"), report("s:3", 60, "#13", "Other work"), po("s:4", 61, "ok")]
        pts = [point("P1", "s:0", "build it", state="planned", task="#12")]
        out = self.run_(recs, pts, title=lambda ref: "never asked")
        # The point is in the main chat: its task's reports are Team activity.
        self.assertNoThread(out)
        th = out["threads"]["team:activity"]
        self.assertEqual((th["title"], th["members"]), ("Team activity", ["s:2", "s:3", "s:4"]))
        self.assertEqual(out["work"], set())
        # In a thread the person started: its Work log.
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]), title=lambda ref: "never asked")
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(out["work"], {"s:2"})
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:3", "s:4"])

    def test_work_lane_keeps_reports_and_po_notes_out_of_unread(self):
        recs = [human("s:0", 0, "status of #12?"), po("s:1", 2, "Re P1: in progress", re_=["P1"]),
                report("s:2", 10, "#12"), po("s:3", 11, "Review passed."),
                po("s:4", 12, "Still working."), po("s:5", 13, "For you: ready to try."),
                human("s:6", 20, "Re P1: tried", re_=["P1"]), po("s:7", 21, "It works."),
                report("s:8", 30, "#12"), po("s:9", 31, "Re P1: shipped", re_=["P1"])]
        pts = [point("P1", "s:0", "status of #12?", followUps=["s:6"])]
        # The main chat has no Work log: the reports are Team activity.
        out = self.run_(recs, pts)
        self.assertEqual(out["work"], set())
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:2", "s:3", "s:4", "s:8"])
        # A thread the person started, replied to from its box.
        sd = side(sent=[new_row("s:0"), self.sent_row("Re P1: tried", 20, mid="s:6", key="k6")])
        out = self.run_(recs, pts, sd)
        self.assertEqual(out["threads"]["P1"]["members"], [r["mid"] for r in recs])
        self.assertEqual(out["work"], {"s:2", "s:3", "s:4", "s:8"})
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        self.assertTrue(threads._turn(idx, "s:3", "P1")["workLog"])
        self.assertNotIn("workLog", threads._turn(idx, "s:5", "P1"))
        # A thread started on one of them (from its menu, #265): its first
        # message shows, not a closed Work log row.
        self.assertNotIn("workLog", threads._turn(idx, "s:3", "m:s:3"))
        self.assertTrue(threads._turn({**idx, "threads": {**idx["threads"], "m:s:2": {"id": "m:s:2", "root": "s:2",
                                                                                      "members": ["s:2", "s:3"]}}},
                                      "s:3", "m:s:2")["workLog"])

    def test_real_case_answers_survive_handover_lane(self):
        cases = [
            ("P77", "Can the X5 ad explain the lease transfer?",
             "The handover file is up to date. Re P77, here is what I found about the ad."),
            ("P56", "The two drafts are missing their attachments.",
             "I've updated the handover. I need your answer on P56 about the drafts."),
            ("P245", "Why does ZC show no prices?",
             "I found and fixed why ZC showed no prices. The fix is ready for P245."),
            ("P729", "What should we do about the Windows update?",
             "Weekly reminder: please restart Windows for updates this week. P729 is still open."),
        ]
        for pid, question, answer in cases:
            with self.subTest(pid=pid):
                recs = [human("s:0", 0, question), note("s:1", 1, "rotation"), po("s:2", 2, answer)]
                out = self.run_(recs, [point(pid, "s:0", question)])
                # In the main chat: a message there, not Team activity.
                self.assertEqual(out["of"]["s:2"], ["m:s:2"])
                out = self.run_(recs, [point(pid, "s:0", question)], side(sent=[new_row("s:0")]))
                self.assertIn("s:2", out["threads"][pid]["members"])
                self.assertNotIn("s:2", out["work"])

    def test_handover_broadcast_without_an_open_answer_is_work(self):
        recs = [human("s:0", 0, "First topic"), po("s:1", 1, "Re P1: answered", re_=["P1"]),
                human("s:2", 2, "Second topic"), po("s:3", 3, "Re P2: answered", re_=["P2"]),
                note("s:4", 4, "rotation"), po("s:5", 5, "I've taken over as PO. Where things stand for P1.")]
        pts = [point("P1", "s:0", "First topic"), point("P2", "s:2", "Second topic", 2)]
        out = self.run_(recs, pts)
        # About a point in the main chat: the hub's traffic (Team activity).
        self.assertEqual(out["of"]["s:5"], ["team:activity"])
        out = self.run_(recs, pts, side(sent=[new_row("s:0"), new_row("s:2", key="kn2", at=2)]))
        self.assertEqual(out["of"]["s:5"], ["P1"])
        self.assertIn("s:5", out["work"])

    def test_handover_summary_listing_last_point_does_not_raise_unread(self):
        recs = [human("s:0", 0, "Please check P9"), note("s:1", 1, "rotation"),
                po("s:2", 2, "I've brought PO-HANDOVER.md up to date for the next session.\n\nDue: P9 is still planned."),
                po("s:3", 3, "I've updated the handover. Re P9, the check is done.")]
        pts = [point("P9", "s:0", "Please check P9")]
        out = self.run_(recs, pts)
        self.assertEqual((out["of"]["s:2"], out["of"]["s:3"]), (["team:activity"], ["m:s:3"]))
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertIn("s:2", out["work"])
        self.assertNotIn("s:3", out["work"])

    def test_quick_search_finds_balloon_and_newest_answer_across_threads(self):
        recs = [human("s:0", 0, "backtest the models"), po("s:1", 1, "Re P1: started", re_=["P1"]),
                human("s:2", 3, "backtest update?"), po("s:3", 4, "Re P2: 32 of 32 matched", re_=["P2"])]
        out = self.run_(recs, [point("P1", "s:0", "backtest the models"),
                               point("P2", "s:2", "backtest update?", 3)])
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        with mock.patch.object(threads, "ready", return_value=idx):
            found = threads.search_balloons("room-12345678", {}, [["backtest"]])
        self.assertEqual([m["msgId"] for m in found["messages"]], ["s:2", "s:0"])
        self.assertEqual(found["latest"]["msgId"], "s:3")
        self.assertEqual(found["latest"]["threads"], 2)

    def test_a_main_chat_answer_to_a_threaded_point_stays_out_of_its_thread(self):
        # The person started thread P1, then typed "Re P1" in the main box: the
        # PO's answer to that is in the main chat, not an answer inside P1 (#367).
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Re P1: done", re_=["P1"]),
                human("s:2", 10, "Re P1: wider still", re_=["P1"]),
                po("s:3", 12, "Re P1: wider now", re_=["P1"])]
        out = self.run_(recs, [point("P1", "s:0", "make the chart wider")], side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1"])
        self.assertNotIn("s:3", out["answered"].get("P1", []))
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        self.assertIn("s:3", threads._channel(idx))
        with mock.patch.object(threads, "ready", return_value=idx):
            found = threads.search_balloons("room-12345678", {}, [["wider"]])
        self.assertNotEqual((found["latest"]["thread"], found["latest"]["msgId"]), ("P1", "s:3"))

    def test_an_open_card_in_a_thread_wins_over_its_points_all_done(self):
        # The person acknowledged P1 in their thread, then the PO asked a card
        # there: the row waits for the person (#367).
        th = {"points": ["P1"], "members": ["s:0", "s:1", "s:2"], "task": ""}
        pts = {"P1": {"id": "P1", "state": "acked"}}
        self.assertEqual(threads._state_of(th, pts, {"s:2"}, None), "needs")
        self.assertEqual(threads._state_of(th, pts, set(), None), "done")

    def test_a_point_naming_one_task_claims_its_reports_in_a_thread(self):
        recs = [human("s:0", 0, "how is #12 going?"), report("s:1", 50, "#12")]
        pts = [point("P1", "s:0", "how is #12 going?")]
        out = self.run_(recs, pts)
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:1"])
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1"])
        self.assertEqual(out["threads"]["P1"]["task"], "#12")

    def test_a_point_naming_two_tasks_claims_the_reports_of_both(self):
        recs = [human("s:0", 0, "Status of #12 and #13?"), report("s:1", 50, "#12"), report("s:2", 60, "#13")]
        out = self.run_(recs, [point("P1", "s:0", "Status of #12 and #13?")], side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(list(out["threads"]), ["P1"])

    def test_a_message_with_no_point_is_an_entry_of_its_own(self):
        recs = [human("s:0", 0, "hello there"), po("s:1", 2, "hi")]
        out = self.run_(recs, [])
        self.assertEqual(out["of"], {"s:0": ["m:s:0"], "s:1": ["m:s:1"]})
        out = self.run_(recs, [], side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["m:s:0"]["members"], ["s:0", "s:1"])

    def test_every_message_kind_has_one_home_and_the_channel_is_all_but_replies(self):
        recs = [human("s:0", 0, "first topic"), po("s:1", 1, "answer"),
                po("s:2", 2, "a new PO topic"), note("s:3", 3),
                po("s:4", 4, "digest acknowledged"), report("s:5", 5, "#13"),
                human("s:6", 6, "Re P1: another thought", re_=["P1"]),
                po("s:7", 7, "Ask: choose one\n- A\n- B", ask=True),
                po("s:8", 8, "#13 is live", live=True)]
        pts = [point("P1", "s:0", "first topic", followUps=["s:6"])]
        out = self.run_(recs, pts)
        # No thread: the channel is the whole chat (the hub note, its answer
        # and the report are Team activity, folded by the page).
        self.assertNoThread(out)
        self.assertEqual(threads._channel({**out, "recs": recs}), [r["mid"] for r in recs])
        self.assertEqual(out["also"], {})
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:3", "s:4", "s:5"])
        self.assertEqual(set(out["of"]), {r["mid"] for r in recs})
        # P1 sent as a new thread: the answer and the PO's next words are its
        # replies; the follow-up typed in the chat's own box, the ask and the
        # live news after it are in the chat.
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:3", "s:4", "s:5", "s:6", "s:7", "s:8"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(out["also"], {})
        self.assertEqual(set(out["of"]), {r["mid"] for r in recs})

    def test_po_turn_stays_together_after_a_human_or_digest(self):
        recs = [po("s:0", 0, "Unprompted topic"), po("s:1", 1, "more on topic"),
                human("s:2", 2, "please fix"), po("s:3", 3, "working"), po("s:4", 4, "done"),
                note("s:5", 5), po("s:6", 6, "reading digest"), po("s:7", 7, "digest details")]
        pts = [point("P1", "s:2", "please fix", 2)]
        out = self.run_(recs, pts)
        self.assertNoThread(out)
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:5", "s:6", "s:7"])
        self.assertEqual(threads._channel({**out, "recs": recs}), [r["mid"] for r in recs])
        out = self.run_(recs, pts, side(sent=[new_row("s:2")]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:2", "s:3", "s:4"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:5", "s:6", "s:7"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2", "s:5", "s:6", "s:7"])

    def test_po_point_mention_after_a_digest_claims_the_answer_turn(self):
        recs = [human("s:0", 0, "three problems"), note("s:1", 1),
                po("s:2", 2, "I've started three tasks for your P5."),
                po("s:3", 3, "The first task is working now.")]
        pts = [point("P5", "s:0", "three problems")]
        out = self.run_(recs, pts)
        # In the main chat: the answer and its next words are messages there.
        self.assertEqual((out["of"]["s:2"], out["of"]["s:3"]), (["m:s:2"], ["m:s:3"]))
        self.assertEqual(out["work"], set())
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2", "s:3"])
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(out["threads"]["P5"]["members"], ["s:0", "s:2", "s:3"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:1"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        self.assertEqual(threads._point_mentions("Re P506–P509: done"),
                         ["P506", "P509", "P507", "P508"])

    def test_po_ask_or_live_after_team_activity_is_in_the_main_chat(self):
        recs = [note("s:0", 0), po("s:1", 1, "Ask: shall we deploy?\n- Yes\n- No", ask=True),
                po("s:2", 2, "The change is ready."), report("s:3", 3, "#12"),
                po("s:4", 4, "#12 is live", live=True)]
        out = self.run_(recs, [])
        self.assertNoThread(out)
        self.assertEqual([out["of"][m] for m in ("s:1", "s:2", "s:4")], [["m:s:1"], ["m:s:2"], ["m:s:4"]])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:0", "s:3"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2", "s:3", "s:4"])

    def test_live_news_shows_in_the_channel_and_an_old_re_is_ignored(self):
        recs = [human("s:0", 0, "a"), po("s:1", 1000, "Re P9: #12 is live", re_=["P9"], live=True)]
        pts = [point("P1", "s:0", "a"), point("P9", "x:9", "later", 5000)]
        out = self.run_(recs, pts)
        self.assertEqual(out["of"]["s:1"], ["m:s:1"])          # P9 came later: where the input is
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        # In a thread the person started: there, not repeated in the chat.
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertEqual(out["of"]["s:1"], ["P1"])
        self.assertEqual(out["also"], {})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])

    def test_a_reply_sent_to_the_chat_too_is_marked(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "Re P1: also this", re_=["P1"])]
        sd = side(sent=[self.sent_row("Re P1: also this", 100, also=True, mid="s:2")], anchors={"P2": "P1"})
        out = self.run_(recs, [point("P1", "s:0", "a"), point("P2", "s:2", "Re P1: also this", 100)], sd)
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(out["also"], {"s:2": "P1"})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])

    def test_a_reply_sent_from_a_thread_box_stays_out_of_the_channel(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "Re P1: only here", re_=["P1"]),
                human("s:3", 200, "Re P1: typed in the chat", re_=["P1"])]
        pts = [point("P1", "s:0", "a"), point("P2", "s:2", "Re P1: only here", 100),
               point("P3", "s:3", "Re P1: typed in the chat", 200)]
        out = self.run_(recs, pts, side(anchors={"P2": "P1"}))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        # The thread box's reply is in the thread alone; the chat's own is the chat's (#367).
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:3"])
        self.assertEqual(out["of"]["s:3"], ["P3"])
        self.assertEqual(out["also"], {})

    # #237: a reply sent from a thread's box is kept by the hub as it is
    # sent ("sent" rows, threads.note_send), whatever it holds.
    def sent_row(self, text, at, thread="P1", also=False, key="k", **kw):
        return {"thread": thread, "key": key, "at": T0 + at, "also": also, "pids": [],
                "words": threads._send_words(points.norm_text(points.strip_point_lines(text))),
                "ev": threads._d.sends.evidence(text).keep(), **kw}

    def leak_case(self, text, ts=100, **kw):
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."),
                human("s:2", ts, text, re_=["P1"] if text.startswith("Re P1") else [])]
        pts = [point("P1", "s:0", "make the chart wider", followUps=["s:2"])]
        return recs, pts

    def test_a_re_pn_follow_up_with_no_new_point_stays_in_its_thread(self):
        # The leak: "Re P1: is this ready?" from P1's box makes no point (a
        # follow-up), so nothing marked it as the thread box's reply.
        recs, pts = self.leak_case("Re P1: is this ready?")
        out = self.run_(recs, pts)
        self.assertIn("s:2", threads._channel({**out, "recs": recs}), "no record: the chat's own (control)")
        late, _ = self.leak_case("Re P1: is this ready?", ts=3000)                 # a busy agent read it late
        for recs_, row in ((recs, self.sent_row("Re P1: is this ready?", 100)),    # by what it holds
                           (recs, self.sent_row("Re P1: something else", 100, mid="s:2")),  # by its balloon
                           (late, self.sent_row("Re P1: is this ready?", 100))):
            out = self.run_(recs_, pts, side(sent=[row]))
            self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"], row)
            self.assertEqual(threads._channel({**out, "recs": recs_}), ["s:0"], row)
            self.assertEqual(out["also"], {}, row)

    def test_a_reply_naming_another_point_moves_nothing(self):
        # Review 1: "Re P2" typed in P1's box. P2 keeps its place and its
        # message; the reply is in P1, where it was sent (#367: only there).
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."),
                human("s:2", 10, "and the legend"), po("s:3", 15, "Done too."),
                human("s:4", 100, "Re P2: smaller please", re_=["P2"])]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the legend", 10, followUps=["s:4"])]
        out = self.run_(recs, pts, side(sent=[self.sent_row("Re P2: smaller please", 100)]))
        self.assertEqual(out["threads"]["P2"]["members"], ["s:2"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:4"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2", "s:3"])
        self.assertEqual(out["also"], {})

    def test_a_reply_keeps_its_place_after_400_newer_sends(self):
        # Review 1: the record of an old reply outlives the rolling cutoff
        # once its balloon is known (paired by what it holds, kept by build).
        recs, pts = self.leak_case("Re P1: is this ready?")
        row = self.sent_row("Re P1: is this ready?", 100)
        out = self.run_(recs, pts, side(sent=[row]))
        self.assertEqual(out["paired"], {"k": ("s:2", T0 + 100)})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(threads, "_dir", lambda: Path(tmp)):
            rid = "room-abc126"
            threads.forget()
            threads._save_side(rid, side(sent=[row]))
            threads.confirmed(rid, {"k": "s:2"})
            sd = threads.load_side(rid)
            sd["sent"] += [self.sent_row(f"other {n}", 200 + n, key=f"o{n}") for n in range(threads.SIDE_KEEP)]
            threads._save_side(rid, sd)
            threads.forget()
            kept = threads.load_side(rid)
            threads.forget()
        self.assertEqual(len(kept["sent"]), threads.SIDE_KEEP + 1)
        self.assertEqual(kept["sent"][0], {"mid": "s:2", "thread": "P1", "also": False})
        out = self.run_(recs, pts, kept)
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])
        # Review 2: a placement is never dropped, however many come after.
        many = [{"mid": f"x:{n}", "thread": "P1", "also": False} for n in range(25000)]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(threads, "_dir", lambda: Path(tmp)):
            threads._save_side("room-abc127", side(sent=many + kept["sent"]))
            threads.forget()
            again = threads.load_side("room-abc127")["sent"]
            threads.forget()
        self.assertEqual(len(again), 25000 + 1 + threads.SIDE_KEEP)
        self.assertEqual(again[0]["mid"], "x:0")

    def test_the_same_words_in_the_chat_before_a_pending_reply_are_the_chats(self):
        # Review 2: "Re P1: ship it" typed in the chat at 90, the same sent
        # from P1's box at 100 and read at 120.
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."),
                human("s:2", 90, "Re P1: ship it [point P1]", re_=["P1"])]
        pts = [point("P1", "s:0", "make the chart wider", followUps=["s:2"])]
        row = self.sent_row("Re P1: ship it [point P1]", 100)
        # The hub found the chat's own send in s:2: no guess takes it.
        out = threads.assign(recs, {"points": pts}, side(sent=[row]), None, claimed={"kc": "s:2"})
        self.assertEqual(out["paired"], {})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])
        # Not known to the hub (typed in the terminal): the balloon after the send wins once it comes.
        recs.append(human("s:3", 120, "Re P1: ship it [point P1]", re_=["P1"]))
        pts[0]["followUps"].append("s:3")
        out = self.run_(recs, pts, side(sent=[row]))
        self.assertEqual(out["paired"], {"k": ("s:3", T0 + 100)})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])

    def test_a_ticked_follow_up_is_in_the_thread_and_the_chat(self):
        recs, pts = self.leak_case("Re P1: is this ready?")
        out = self.run_(recs, pts, side(sent=[self.sent_row("Re P1: is this ready?", 100, also=True)]))
        self.assertIn("s:2", out["threads"]["P1"]["members"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])
        self.assertEqual(out["also"], {"s:2": "P1"})

    def test_a_reply_that_made_a_point_is_found_by_its_key(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "a new ask from the thread")]
        pts = [point("P1", "s:0", "a"), point("P2", "s:2", "a new ask from the thread", 100, key="k")]
        row = self.sent_row("unrelated words", 99999, pids=["P2"])
        out = self.run_(recs, pts, side(sent=[row], anchors={"P2": "P1"}))
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])

    def test_quotes_links_images_and_codex_turns_stay_in_their_thread(self):
        img = "[image] D:/shots/shot.png"
        quote = "> Done." + chr(10) * 2 + "not quite: wider"
        for sent, turn in ((quote, quote),
                           ("see [the spec](https://example.com/x) again", "see [the spec](https://example.com/x) again"),
                           (img, img),                                                   # an image alone
                           ("look at this" + chr(10) + img, "look at this [Image #1]")):  # Codex names it
            recs = [human("s:0", 0, "a"), po("s:1", 5, "Done."), human("s:2", 100, turn)]
            out = self.run_(recs, [point("P1", "s:0", "a")], side(sent=[self.sent_row(sent, 100)]))
            self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"], sent)
            self.assertIn("s:2", out["threads"]["P1"]["members"], sent)

    def test_an_also_record_from_before_237_keeps_its_thread(self):
        # #367 review 1: a record from before #237 is the person's reply from
        # P1's box (to the chat too): P1 is theirs and stays; P2, the hub's, goes.
        recs, pts = self.leak_case("Re P1: ship it")
        recs += [human("s:3", 200, "and the legend"), po("s:4", 205, "Re P2: Done.")]
        pts.append(point("P2", "s:3", "and the legend"))
        out = self.run_(recs, pts, side(also=[{"thread": "P1", "key": "k", "at": T0 + 100, "words": "ship it"}]))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertTrue(out["threads"]["P1"].get("chosen"))
        self.assertEqual(out["also"], {"s:2": "P1"})
        self.assertEqual(out["threads"]["P2"]["members"], ["s:3"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2", "s:3", "s:4"])
        # Without the record: no thread at all.
        self.assertNoThread(self.run_(recs, pts, side()))

    def test_a_new_thread_stays_the_persons_after_400_newer_sends(self):
        # #367 review 1: a compacted record keeps "new".
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done.")]
        pts = [point("P1", "s:0", "make the chart wider")]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(threads, "_dir", lambda: Path(tmp)):
            rid = "room-abc128"
            threads.forget()
            sd = side(sent=[new_row("s:0")])
            sd["sent"] += [self.sent_row(f"other {n}", 200 + n, key=f"o{n}") for n in range(threads.SIDE_KEEP)]
            threads._save_side(rid, sd)
            threads.forget()
            kept = threads.load_side(rid)
            threads.forget()
        self.assertEqual(kept["sent"][0], {"mid": "s:0", "thread": "", "also": False, "new": True})
        out = self.run_(recs, pts, kept)
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])

    def test_a_new_thread_is_listed_before_any_reply(self):
        # #367 review 1: the person's thread has its row from the start.
        recs = [human("s:0", 0, "make the chart wider"), human("s:1", 10, "and the legend")]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:1", "and the legend")]
        out = self.run_(recs, pts, side(sent=[new_row("s:0")]))
        self.assertTrue(out["threads"]["P1"].get("chosen"))
        self.assertFalse(out["threads"]["P2"].get("chosen"))
        self.assertEqual(threads._replied(out), {"P1"})
        self.assertTrue(threads._meta({**out["threads"]["P1"], "replies": 0})["chosen"])

    def test_a_reply_with_no_point_from_a_thread_box_joins_it(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "ok thanks")]
        sd = side(sent=[{"thread": "P1", "key": "k", "at": T0 + 100, "words": "ok thanks", "also": False}])
        out = self.run_(recs, [point("P1", "s:0", "a")], sd)
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"])
        # A word alike in the chat's own box, later, is its own message.
        recs.append(human("s:3", 5000, "ok thanks"))
        out = self.run_(recs, [point("P1", "s:0", "a")], sd)
        self.assertIn("s:3", threads._channel({**out, "recs": recs}))

    def test_a_reply_with_no_point_in_a_team_activity_balloons_thread_stays_there(self):
        # A digest's side thread (m:s:0): the reply is in that thread, not
        # back in Team activity.
        recs = [note("s:0", 0), human("s:1", 100, "ok thanks")]
        sd = side(sent=[{"thread": "m:s:0", "key": "k", "at": T0 + 100, "words": "ok thanks", "also": False}])
        out = self.run_(recs, [], sd)
        self.assertEqual(out["of"]["s:1"], ["m:s:0"])
        self.assertEqual(out["threads"]["m:s:0"]["members"], ["s:0", "s:1"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:0"])
        self.assertNotIn("s:1", threads._channel({**out, "recs": recs}))

    def test_a_word_said_again_in_the_chat_is_not_the_thread_reply_before_it(self):
        # Review 1 (#234): "ok" from P1's box, then "ok" typed in the chat 3
        # min later. A send is one balloon: the second "ok" is in the chat.
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."), human("s:2", 100, "ok"),
                po("s:3", 120, "Separately: deploy is live?"), human("s:4", 280, "ok")]
        sd = side(sent=[{"thread": "P1", "key": "k", "at": T0 + 101, "words": "ok", "also": False}])
        out = self.run_(recs, [point("P1", "s:0", "make the chart wider")], sd)
        self.assertIn("s:2", out["threads"]["P1"]["members"])
        self.assertNotIn("s:4", out["threads"]["P1"]["members"])
        self.assertIn("s:4", threads._channel({**out, "recs": recs}))
        self.assertNotIn("s:2", threads._channel({**out, "recs": recs}))

    def test_a_link_to_an_older_message_gets_the_channel_back_to_it(self):
        # Review 1 (#234): a link to a message before the last 150 lands in
        # the channel; the hub sends as many as reach it, and says how many.
        recs = [human(f"s:{i}", i * 10, f"message {i}") for i in range(400)]
        out = self.run_(recs, [], side())
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}, "sig": 1,
               "channel": threads._channel({**out, "recs": recs})}
        # Review 4, finding 2: a window around it, not all that came after.
        with mock.patch.object(threads, "ready", return_value=idx):
            plain = threads.channel_view("r", {})
            linked = threads.channel_view("r", {}, 40, upto="s:50")
            gone = threads.channel_view("r", {}, upto="s:nope")
            near = threads.channel_view("r", {}, 40, upto="s:340")
            down = threads.channel_view("r", {}, 100, after=linked["items"][-1]["mid"])
            poll = threads.channel_view("r", {}, 0, frm="s:30", to=linked["items"][-1]["mid"])
        mids = lambda v: [t["mid"] for t in v["items"]]      # noqa: E731
        self.assertEqual((len(plain["items"]), plain["n"], plain["after"]), (threads.CHANNEL_MAX, threads.CHANNEL_MAX, 0))
        self.assertEqual((mids(linked)[0], mids(linked)[-1]), ("s:30", "s:90"))
        self.assertEqual((linked["before"], linked["after"]), (30, 400 - 91))
        self.assertEqual(gone["n"], threads.CHANNEL_MAX)
        self.assertEqual((mids(near)[0], mids(near)[-1], near["after"]), ("s:320", "s:399", 0), "near the end: to the end")
        self.assertEqual((mids(down)[0], mids(down)[-1], down["after"]), ("s:91", "s:190", 209))
        self.assertNotIn("unreadThreads", down, "a page below carries its entries only")
        self.assertEqual(mids(poll), mids(linked), "a window's poll keeps to the window")

    def test_the_threads_filter_shows_only_balloons_of_a_thread_and_counts_the_whole_chat(self):
        # #304: the chat's Threads filter (&threads=1) keeps a thread's
        # balloons; the counts on the filter are the whole chat's, either way.
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."), human("s:2", 100, "ok"),
                po("s:3", 120, "Separately: deploy is live?"), human("s:4", 280, "ok"), note("s:5", 300)]
        sd = side(sent=[{"thread": "P1", "key": "k", "at": T0 + 101, "words": "ok", "also": False}])
        out = self.run_(recs, [point("P1", "s:0", "make the chart wider")], sd)
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}, "sig": 1,
               "channel": threads._channel({**out, "recs": recs})}
        with mock.patch.object(threads, "ready", return_value=idx):
            plain = threads.channel_view("r", {}, 40)
            only = threads.channel_view("r", {}, 40, threads_only=True)
        mids = lambda v: [t["mid"] for t in v["items"]]      # noqa: E731
        self.assertIn("s:4", mids(plain))
        # s:4 nobody answered: a thread of its own (m:s:4), not one with replies.
        self.assertEqual(out["parent"].get("s:4"), ["m:s:4"])
        self.assertEqual(mids(only), ["s:0"], "only the balloon a thread with replies hangs on")
        self.assertNotEqual(plain["sig"], only["sig"], "a filtered view is never the plain one's delta")
        for v in (plain, only):
            self.assertEqual(v["threadCount"], 1, "P1; not m:s:4 nor Team activity")
            self.assertIsInstance(v["unreadCount"], int)
        with mock.patch.object(threads, "ready", return_value=None):
            self.assertTrue(threads.channel_view("r", {}, 40, threads_only=True).get("pending"),
                            "a chat not read yet has no threads to show")

    def test_a_cursor_older_than_the_cap_gets_a_window_from_it(self):
        # Review 4, finding 3: 6,000 entries, a poll or a page from the first.
        recs = [human(f"s:{i}", i * 10, f"message {i}") for i in range(6000)]
        out = self.run_(recs, [], side())
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}, "sig": 1,
               "channel": threads._channel({**out, "recs": recs})}
        with mock.patch.object(threads, "ready", return_value=idx):
            poll = threads.channel_view("r", {}, 0, frm="s:0")
            linked = threads.channel_view("r", {}, 40, upto="s:3")
            up = threads.channel_view("r", {}, 100, before="s:10")
            span = threads.channel_view("r", {}, 40, frm="s:2", to="s:5")
        mids = lambda v: [t["mid"] for t in v["items"]]      # noqa: E731
        self.assertEqual((mids(poll)[0], len(mids(poll)), poll["before"], poll["after"]),
                         ("s:0", threads.CHANNEL_LIMIT, 0, 6000 - threads.CHANNEL_LIMIT))
        self.assertEqual((mids(linked)[0], linked["before"], len(mids(linked))), ("s:0", 0, 44))
        self.assertEqual((mids(up)[0], len(mids(up)), up["before"]), ("s:0", 10, 0))
        self.assertEqual(mids(span), ["s:2", "s:3", "s:4", "s:5"])

    def test_the_channel_comes_in_pages_from_the_newest_up(self):
        # #247: the latest 40 first; older ones a page at a time as the
        # reader scrolls up; a poll asks from the first entry it holds.
        recs = [human(f"s:{i}", i * 10, f"message {i}") for i in range(400)]
        out = self.run_(recs, [], side())
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}, "sig": 1,
               "channel": threads._channel({**out, "recs": recs})}
        with mock.patch.object(threads, "ready", return_value=idx):
            first = threads.channel_view("r", {}, 40)
            up = threads.channel_view("r", {}, 100, before=first["items"][0]["mid"])
            poll = threads.channel_view("r", {}, 0, frm=first["items"][0]["mid"])
            top = threads.channel_view("r", {}, 100, before="s:50")
        mids = lambda v: [t["mid"] for t in v["items"]]      # noqa: E731
        self.assertEqual((mids(first)[0], mids(first)[-1], first["before"]), ("s:360", "s:399", 360))
        self.assertEqual((mids(up)[0], mids(up)[-1], up["before"]), ("s:260", "s:359", 260))
        self.assertNotIn("unreadThreads", up, "a page above carries its entries only")
        self.assertEqual(mids(poll), mids(first))
        self.assertEqual((mids(top)[0], len(top["items"]), top["before"]), ("s:0", 50, 0))

    def test_a_chat_not_read_yet_shows_its_newest_balloons_at_once(self):
        # Review 4, finding 1: opened before its first build is done, the
        # channel's first page is the newest balloons as the transcripts hold
        # them (no threads yet, hub lines left out), never pending with
        # nothing; its polls, a page above and a link wait for the build.
        turns = {"old": [{"role": "user", "text": "first", "timestamp": "2026-01-01T00:00:00Z"}],
                 "now": [{"role": "user" if i % 2 else "assistant", "text": f"turn {i}",
                          "timestamp": f"2026-02-01T00:{i:02d}:00Z"} for i in range(50)]
                        + [{"role": "user", "kind": "human", "text": "[digest] usage: fine",
                            "timestamp": "2026-02-01T01:00:00Z"}]}
        with mock.patch.object(threads, "ready", return_value=None),                 mock.patch.object(threads, "_sessions", return_value=["now", "old"]),                 mock.patch.object(threads.points, "load", return_value={}),                 mock.patch.object(dashboard, "read_session_turns", side_effect=lambda sid: turns[sid]):
            first = threads.channel_view("r", {}, 40)
            few = threads.channel_view("r", {}, 3)
            later = [threads.channel_view("r", {}, 0, frm=first["items"][0]["mid"]),
                     threads.channel_view("r", {}, 100, before=first["items"][0]["mid"]),
                     threads.channel_view("r", {}, 40, upto="old:0")]
        texts = [t["text"] for t in first["items"]]
        self.assertTrue(first["quick"])
        self.assertEqual((len(texts), texts[0], texts[-1]), (40, "turn 10", "turn 49"))
        self.assertEqual((first["before"], first["channel"], first["threads"]), (11, 51, {}))
        self.assertEqual([t["text"] for t in few["items"]], ["turn 47", "turn 48", "turn 49"])
        self.assertEqual(len({t["mid"] for t in first["items"]}), 40)
        for v in later:
            self.assertTrue(v.get("pending"), v)
        with mock.patch.object(threads, "ready", return_value=None),                 mock.patch.object(threads, "_sessions", return_value=["now"]),                 mock.patch.object(threads.points, "load", return_value={}),                 mock.patch.object(dashboard, "read_session_turns",
                                  return_value=[{"role": "assistant", "text": str(i) * 30_000} for i in range(10)]):
            big = threads.channel_view("r", {}, 40)
        self.assertEqual(len(big["items"]), 2, "a first page fits its bytes")

    def test_a_chat_not_read_yet_reads_only_the_sessions_its_newest_balloons_are_in(self):
        # A PO chat rotated many times, opened with no turn cache: ~10 s to
        # read every session for its newest 40. Read newest written first,
        # and none that ended before those read began.
        day = lambda d, i: f"2026-0{d}-01T00:{i:02d}:00Z"      # noqa: E731
        epoch = lambda d: dashboard._turn_epoch(day(d, 59))     # noqa: E731
        turns = {f"s{d}": [{"role": "assistant", "text": f"s{d} turn {i}", "timestamp": day(d, i)}
                           for i in range(30)] for d in (1, 2, 3, 4)}
        stat = {"s1": [1, epoch(1)], "s2": [1, epoch(2)], "s3": [1, epoch(3)], "s4": [1, epoch(4)]}
        reads = []
        with mock.patch.object(threads, "ready", return_value=None), \
                mock.patch.object(threads, "_sessions", return_value=["s1", "s3", "s2", "s4"]), \
                mock.patch.object(threads.points, "load", return_value={}), \
                mock.patch.object(threads.points, "_session_stat", side_effect=stat.get), \
                mock.patch.object(dashboard, "read_session_turns",
                                  side_effect=lambda sid: reads.append(sid) or turns[sid]):
            first = threads.channel_view("r", {}, 40)
        texts = [t["text"] for t in first["items"]]
        self.assertEqual(reads, ["s4", "s3"])
        self.assertEqual((len(texts), texts[0], texts[-1]), (40, "s3 turn 20", "s4 turn 29"))
        self.assertEqual(first["before"], 20)


    def test_real_case_p342_p343_a_card_answered_in_a_thread_stays_in_it(self):
        # #367, the Ensemble PO chat on 11-15 (live a37af22): P342 is a thread
        # the person wrote into from its box; P343 answers the card the PO put
        # in that thread; the PO's note on #366's report was P343's "1 reply";
        # P346, a card answered in the main chat, became a thread of its own.
        recs = [human("x:8", 0, "the PO label should take its project's colour"),
                po("x:9", 5, "Re P342 (planned #365): started", re_=["P342"]),
                human("x:15", 100, "and in the replies row too"),
                po("x:17", 110, "Re P342: one question first: "
                                "[the card](/session?id=room-8d56cd21&msg=ad7832cbd10d)", re_=["P342"]),
                human("x:18", 120, "ok"),
                human("x:20", 200, "Project colour"),
                po("x:21", 210, "Re P342: #366 is running with the project colours.", re_=["P342"]),
                report("x:22", 300, "#366"),
                po("x:23", 305, "Review passed; merging #366."),
                po("x:26", 400, "Re P342: #366 is live.", re_=["P342"], live=True),
                human("x:28", 500, "why there are 2 links under this message?"),
                po("x:29", 510, "Re P344: one choice for you: "
                                "[the card](/session?id=room-8d56cd21&msg=0123456789ab)", re_=["P344"]),
                human("x:31", 600, "Thread creation should be a user choice"),
                po("x:32", 610, "Re P346: noted", re_=["P346"])]
        pts = [point("P342", "x:8", "the PO label should take its project's colour", key="send:mwgeyo7q",
                     answers=[{"mid": "x:9"}, {"mid": "x:21"}, {"mid": "x:26"}]),
               point("P343", "x:20", "Project colour", 200, key="ask:ad7832cbd10d:0", state="planned",
                     task="#366", answers=[{"mid": "x:21"}]),
               point("P344", "x:28", "why there are 2 links under this message?", 500, answers=[{"mid": "x:29"}]),
               point("P346", "x:31", "Thread creation should be a user choice", 600,
                     key="ask:0123456789ab:0", answers=[{"mid": "x:32"}])]
        sd = side(sent=[self.sent_row("and in the replies row too", 100, thread="P342", mid="x:15", key="k15"),
                        self.sent_row("ok", 120, thread="P342", mid="x:18", key="k18")])
        out = self.run_(recs, pts, sd)
        self.assertEqual(out["threads"]["P342"]["members"],
                         ["x:8", "x:9", "x:15", "x:17", "x:18", "x:20", "x:21", "x:22", "x:23", "x:26"])
        self.assertEqual(out["threads"]["P342"]["points"], ["P342", "P343"])
        self.assertNotIn("P343", out["threads"])
        self.assertEqual(out["work"], {"x:22", "x:23"}, "the note on #366's report is its Work log, no reply")
        self.assertEqual(out["of"]["x:31"], ["P346"])
        self.assertEqual(out["threads"]["P346"]["members"], ["x:31"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["x:8", "x:28", "x:29", "x:31", "x:32"])
        self.assertEqual(out["answered"], {"P344": ["x:29"], "P346": ["x:32"]})

    def test_old_hub_made_threads_are_main_chat_and_box_written_ones_stay(self):
        recs = [human("s:0", 0, "first"), po("s:1", 5, "Re P1: ok", re_=["P1"]),
                human("s:2", 10, "second"), po("s:3", 15, "Re P2: ok", re_=["P2"]),
                human("s:4", 20, "more on it"), po("s:5", 25, "Re P2: sure", re_=["P2"])]
        pts = [point("P1", "s:0", "first"), point("P2", "s:2", "second", 10)]
        # P1's thread was the hub's (nothing in the record); P2's the person
        # wrote into from its box.
        out = self.run_(recs, pts, side(sent=[self.sent_row("more on it", 20, thread="P2", mid="s:4")]))
        self.assertEqual((out["threads"]["P1"]["members"], out["of"]["s:1"]), (["s:0"], ["m:s:1"]))
        self.assertEqual(out["threads"]["P2"]["members"], ["s:2", "s:3", "s:4", "s:5"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:2"])
        # One anchored there (Reply in thread on its balloon) stays too.
        out = self.run_(recs, pts + [point("P3", "s:4", "more on it", 20)], side(anchors={"P3": "P2"}))
        self.assertEqual(out["threads"]["P2"]["members"], ["s:2", "s:3", "s:4", "s:5"])


class ThreadStaysTest(unittest.TestCase):
    """#379: a PO's answer to a point in a thread stays in that thread, its
    question cards, its later replies and the hub's notices about them too."""

    def run_(self, recs, pts, sd=None, cards=None, claimed=None, asks=None):
        return threads.assign(recs, {"points": pts, "asks": asks or {}}, sd or side(), None,
                              claimed=claimed, cards=cards or [])

    @staticmethod
    def card(cid, ts, text="Which one?", pts=(), q="Which one?"):
        return {"id": cid, "ts": T0 + ts, "text": text, "points": list(pts), "questions": [q],
                "tags": [threads.card_tag(cid, 0)]}

    @staticmethod
    def notice(mid, ts, verb="closed"):
        text = ('[asks] The CEO closed your question "Which one?" without an answer.' if verb == "closed"
                else '[asks] The CEO reopened your question "Which one?".')
        return {"mid": mid, "sid": "s", "role": "user", "kind": "hub", "ts": T0 + ts,
                "turn": {"role": "user", "text": text}}

    def two_threads_same_question(self):
        """P1 and P2 each sent as a new thread, each with a card asking "Which one?"."""
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "On it."),
                human("s:2", 50, "and the colours?"), po("s:3", 55, "Looking.")]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the colours?", 50)]
        sd = side(sent=[new_row("s:0"), new_row("s:2", key="k2", at=50)])
        return recs, pts, sd, [self.card("c1", 6), self.card("c2", 56)]

    def test_a_close_and_a_reopen_go_to_their_own_cards_thread_when_two_cards_ask_the_same(self):
        recs, pts, sd, cards = self.two_threads_same_question()
        key = f"ask-close:{threads.card_tag('c1', 0)}:0123456789abcdef"
        recs = recs + [self.notice("s:4", 100), self.notice("s:5", 110, "reopened")]
        out = self.run_(recs, pts, sd, cards, claimed={key: "s:4", "reopen:" + key: "s:5"})
        self.assertEqual(out["cards"], {"c1": "P1", "c2": "P2"})
        for mid in ("s:4", "s:5"):
            self.assertIn(mid, out["threads"]["P1"]["members"])
            self.assertNotIn(mid, out["threads"]["P2"]["members"])
            self.assertNotIn(mid, self.channel(out, recs))
        self.assertNotIn("team:activity", out["threads"])

    def test_an_older_close_key_is_found_in_the_ledger(self):
        recs, pts, sd, cards = self.two_threads_same_question()
        recs = recs + [self.notice("s:4", 100)]
        out = self.run_(recs, pts, sd, cards, claimed={"ask-close:feedbeef": "s:4"},
                        asks={"c1": {"0": {"closed": True, "noticeKey": "ask-close:feedbeef"}}})
        self.assertIn("s:4", out["threads"]["P1"]["members"])

    def test_a_notice_without_its_key_and_two_cards_asking_it_is_in_no_thread(self):
        recs, pts, sd, cards = self.two_threads_same_question()
        recs = recs + [self.notice("s:4", 100)]
        out = self.run_(recs, pts, sd, cards)
        self.assertNotIn("s:4", out["threads"]["P1"]["members"])
        self.assertNotIn("s:4", out["threads"]["P2"]["members"])
        self.assertIn("s:4", out["threads"]["team:activity"]["members"])

    @staticmethod
    def channel(out, recs):
        return threads._channel({**out, "recs": recs})

    def thread_case(self):
        """P1 sent as a new thread and answered there; P2 in the main chat."""
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "On it."),
                human("s:2", 50, "and the colours?"), po("s:3", 55, "Re P2: noted", re_=["P2"])]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the colours?", 50)]
        return recs, pts, side(sent=[new_row("s:0")])

    def test_a_card_asked_while_answering_a_thread_is_in_it_and_a_hub_woken_one_is_not(self):
        recs, pts, sd = self.thread_case()
        recs = recs[:2] + [note("s:9", 20)] + recs[2:]
        cards = [self.card("c1", 6), self.card("c2", 21, q="Other?")]
        out = self.run_(recs, pts, sd, cards)
        self.assertEqual(out["cards"], {"c1": "P1"}, "c2 was woken by a digest: the main chat's")
        self.assertEqual(out["threads"]["P1"]["cards"], ["c1"])

    def test_a_card_naming_a_threaded_point_is_in_its_thread_by_points_or_by_re(self):
        recs, pts, sd = self.thread_case()
        # Asked after the main-chat P2 answer: the input before it is the main chat.
        by_points = self.card("c1", 60, pts=["P1"])
        by_re = self.card("c2", 61, text="Re P1: which width?", q="Which width?")
        plain = self.card("c3", 62, q="Anything else?")
        out = self.run_(recs, pts, sd, [by_points, by_re, plain])
        self.assertEqual(out["cards"], {"c1": "P1", "c2": "P1"})
        # A card naming a main-chat point stays in the main chat.
        out = self.run_(recs, pts, sd, [self.card("c4", 60, pts=["P2"])])
        self.assertEqual(out["cards"], {})

    def test_a_link_to_a_card_never_takes_it_out_of_its_thread(self):
        recs, pts, sd = self.thread_case()
        recs = recs + [po("s:4", 70, "See [the question](/session?room=r&msg=c1abcdef01)")]
        out = self.run_(recs, pts, sd, [self.card("c1abcdef01", 6)])
        self.assertEqual(out["cards"], {"c1abcdef01": "P1"})

    def test_a_later_re_pn_after_a_report_stays_in_the_thread(self):
        recs, pts, sd = self.thread_case()
        recs = recs + [report("s:4", 100, "#77"), po("s:5", 101, "Re P1: it is live", re_=["P1"])]
        out = self.run_(recs, pts, sd)
        self.assertIn("s:5", out["threads"]["P1"]["members"])
        self.assertNotIn("s:5", self.channel(out, recs))
        self.assertEqual(out["also"], {})

    def test_answers_to_a_threaded_and_a_main_chat_point_are_split_never_shown_twice(self):
        recs, pts, sd = self.thread_case()
        both = "Here is where we are.\n\nRe P1: wider now\n\nRe P2: colours next"
        recs = recs + [po("s:4", 100, both, re_=["P1", "P2"])]
        out = self.run_(recs, pts, sd)
        self.assertEqual(out["split"]["s:4"], {"P1": "Here is where we are.\n\nRe P1: wider now",
                                               "": "Re P2: colours next"})
        self.assertIn("s:4", out["threads"]["P1"]["members"])
        self.assertIn("s:4", self.channel(out, recs), "its main chat part")
        self.assertEqual(out["also"], {})
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        self.assertEqual(threads._turn(idx, "s:4", "P1")["text"], "Here is where we are.\n\nRe P1: wider now")
        self.assertEqual(threads._turn(idx, "s:4")["text"], "Re P2: colours next")
        self.assertNotIn("alsoIn", threads._turn(idx, "s:4", "P1"))

    def test_answers_to_points_in_two_threads_are_split_and_left_out_of_the_chat(self):
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "On it."),
                human("s:2", 50, "and the colours?"), po("s:3", 55, "Looking.")]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the colours?", 50)]
        sd = side(sent=[new_row("s:0"), new_row("s:2", key="k2", at=50)])
        recs = recs + [po("s:4", 100, "Re P1: wider\n\nRe P2: blue", re_=["P1", "P2"])]
        out = self.run_(recs, pts, sd)
        self.assertEqual(out["split"]["s:4"], {"P1": "Re P1: wider", "P2": "Re P2: blue"})
        self.assertNotIn("s:4", self.channel(out, recs))
        self.assertIn("s:4", out["threads"]["P1"]["members"])
        self.assertIn("s:4", out["threads"]["P2"]["members"])

    def test_without_re_heads_a_two_place_answer_goes_whole_to_the_first(self):
        recs, pts, sd = self.thread_case()
        recs = recs + [po("s:4", 100, "P1 and P2 are both done.")]
        out = self.run_(recs, pts, sd)
        self.assertNotIn("s:4", out["split"])
        self.assertIn("s:4", out["threads"]["P1"]["members"])
        self.assertNotIn("s:4", self.channel(out, recs))

    def test_a_send_named_by_a_point_inside_a_thread_goes_to_that_thread(self):
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "On it."),
                human("s:2", 10, "## Review comments (1)\n**1.** the axis"), po("s:3", 15, "Re P5: fixed", re_=["P5"])]
        pts = [point("P1", "s:0", "make the chart wider"), point("P5", "s:2", "**1.** the axis", 10)]
        sd = side(sent=[new_row("s:0"), {"thread": "P1", "key": "k2", "mid": "s:2", "at": T0 + 10,
                                         "also": False, "pids": []}],
                  anchors={"P5": "P1"})
        recs = recs + [human("s:4", 20, "## Review comments (1)\n**1.** still off"), po("s:5", 25, "Re P6: fixed now", re_=["P6"])]
        pts = pts + [point("P6", "s:4", "**1.** still off", 20)]
        sd["sent"].append({"thread": "P5", "key": "k4", "mid": "s:4", "at": T0 + 20, "also": False, "pids": []})
        sd["anchors"]["P6"] = "P5"      # note_send anchors its points to the name it was sent on
        out = self.run_(recs, pts, sd)
        self.assertEqual(list(out["threads"]), ["P1"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2", "s:3", "s:4", "s:5"])
        self.assertEqual(self.channel(out, recs), ["s:0"])

    def test_a_card_closed_without_an_answer_notice_joins_the_cards_thread(self):
        recs, pts, sd = self.thread_case()
        closed = {"mid": "s:4", "sid": "s", "role": "user", "kind": "hub", "ts": T0 + 100,
                  "turn": {"role": "user", "text": '[asks] The CEO closed your question "Which one?" without an answer.'}}
        recs = recs + [closed, po("s:5", 101, "Understood, I will pick one.")]
        out = self.run_(recs, pts, sd, [self.card("c1", 6)])
        self.assertIn("s:4", out["threads"]["P1"]["members"])
        self.assertIn("s:4", out["work"])
        self.assertIn("s:5", out["threads"]["P1"]["members"])
        self.assertNotIn("s:4", self.channel(out, recs))
        self.assertNotIn("s:5", self.channel(out, recs))
        # The same notice about a main-chat card is Team activity.
        out = self.run_(recs, pts, sd, [self.card("c1", 60)])
        self.assertIn("s:4", out["threads"]["team:activity"]["members"])

    def test_room_cards_reads_the_points_and_skips_questions_to_the_po(self):
        room = {"messages": [
            {"id": "a1", "ts": T0 + 2, "text": "ctx", "asks": [{"question": "Which  one?"}], "points": ["P1"]},
            {"id": "a0", "ts": T0 + 1, "text": "", "asks": [{"question": "To the PO?"}], "askAudience": "po"},
            {"id": "a2", "ts": T0 + 3, "text": "plain"}]}
        self.assertEqual(threads.room_cards(room), [
            {"id": "a1", "ts": T0 + 2, "text": "ctx", "points": ["P1"], "questions": ["Which one?"],
             "tags": [threads.card_tag("a1", 0)]}])
        self.assertEqual(threads._card_question('[asks] The CEO reopened your question "Which  one?".'), "Which one?")
        self.assertEqual(threads._card_question('I closed your question "x" without an answer.'), "",
                         "only the hub's own line")


class PoRoomsTest(unittest.TestCase):
    def test_the_po_chats_are_read_once_per_ttl_and_again_when_a_po_changes(self):
        threads.forget()
        self.addCleanup(threads.forget)
        reads = []
        projects = [{"id": "p1", "poRoomId": "room-1"}]
        with mock.patch.object(dashboard, "load_projects", lambda: reads.append(1) or projects):
            self.assertTrue(threads.is_threaded("room-1"))
            self.assertFalse(threads.is_threaded("room-2"))
            self.assertEqual(len(reads), 1, "one read for many polls")
            projects.append({"id": "p2", "poRoomId": "room-2"})
            threads.po_rooms_changed()
            self.assertTrue(threads.is_threaded("room-2"))
            self.assertEqual(len(reads), 2)


class WordsTest(unittest.TestCase):
    def test_a_title_skips_the_comment_number_and_the_quote(self):
        self.assertEqual(threads._words("**1.** > New limits: up to 4 legs.\n\nwhy up to 4?"), "why up to 4?")
        self.assertEqual(threads._words("> only a quote"), "only a quote")
        self.assertTrue(threads._words(" ".join(["w"] * 30)).endswith("…"))


class RecTest(unittest.TestCase):
    def test_an_unclaimed_hub_line_is_not_the_persons(self):
        r = threads._rec("s", "s:0", {"role": "user", "kind": "human", "text": "[digest] usage: fine"})
        self.assertEqual(r["kind"], "digest")
        self.assertEqual(r["turn"]["kind"], "digest")
        r = threads._rec("s", "s:1", {"role": "user", "kind": "human", "text": "please look at #12"})
        self.assertEqual(r["kind"], "human")

    def test_a_plain_final_question_is_an_ask_as_the_page_draws_it(self):
        # The page draws a card for it (asks.safety), so the chat shows it (#234).
        ask = lambda text: threads._rec("s", "s:2", {"role": "assistant", "text": text})["ask"]
        self.assertTrue(ask("Ask: Should I deploy?"))
        self.assertTrue(ask("Done and merged.\n\nShall I deploy?"))
        self.assertTrue(ask("Nothing runs.\n\nDecision needed: which draft next\n- **#19:** a fix\n- **#23:** a button"))
        self.assertFalse(ask("Done and merged."))
        self.assertFalse(ask("You asked whether it is merged?"))


class SideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(threads, "_dir", lambda: Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        threads.forget()

    def test_seen_anchors_and_also_are_kept(self):
        rid = "room-abc123"
        past = 1_700_000_000.0          # seen times are never ahead of the hub's clock
        self.assertTrue(threads.mark_seen(rid, "P1", past))
        self.assertFalse(threads.mark_seen(rid, "P1", past - 1))
        # P7 is the point k1 made; P3 one it names (#237 review 1: not moved).
        led = {"points": [{"id": "P7", "key": "k1"}, {"id": "P3", "key": "k0"}]}
        with mock.patch.object(points, "load", lambda rid: led):
            threads.note_send(rid, "task:#12", "k1", ["P7"], True, "Re P3: ship it")
            threads.note_send(rid, "P3", "k2", ["P3"], False, "x")
            threads.note_send(rid, "P1", "k3", ["P3"], False, "Re P3: and this")
        threads.forget()
        sd = threads.load_side(rid)
        self.assertEqual(sd["seen"], {"P1": past})
        self.assertEqual(sd["anchors"], {"P7": "task:#12"})
        self.assertEqual([(a["key"], a["words"], a["also"], a["pids"]) for a in sd["sent"]],
                         [("k1", "ship it", True, ["P7"]), ("k2", "x", False, ["P3"]),
                          ("k3", "and this", False, ["P3"])])
        self.assertEqual(sd["also"], [], "#237: every reply is a sent row, none an also row")

    def test_a_refused_send_is_dropped_and_a_confirmed_one_keeps_its_balloon(self):
        rid = "room-abc125"
        threads.note_send(rid, "P1", "k1", [], False, "Re P1: one")
        threads.note_send(rid, "P1", "k2", [], False, "Re P1: two")
        threads.drop_send(rid, "k1")
        threads.confirmed(rid, {"k2": "s:9", "other": "s:1"})
        threads.forget()
        self.assertEqual([(a["key"], a.get("mid")) for a in threads.load_side(rid)["sent"]], [("k2", "s:9")])

    def test_a_new_thread_send_is_kept_and_a_main_box_one_is_not(self):
        rid = "room-abc128"
        with mock.patch.object(threads.points, "load", return_value={"points": [{"id": "P5", "key": "kn"}]}):
            threads.note_send(rid, "", "kn", ["P5"], False, "start this [point P5]", now=T0, new=True)
            threads.note_send(rid, "", "km", [], False, "just a message", now=T0 + 1)
        sd = threads.load_side(rid)
        self.assertEqual([(a["thread"], a["key"], a.get("new")) for a in sd["sent"]], [("", "kn", True)])
        self.assertEqual(sd["anchors"], {}, "a new thread has no thread to anchor in yet")

    def test_since_is_set_once(self):
        rid = "room-abc124"
        threads._ensure_since(rid, T0)
        threads._ensure_since(rid, T0 + 99)
        self.assertEqual(threads.load_side(rid)["since"], T0)


def http_get(path):
    h = dashboard.Handler.__new__(dashboard.Handler)
    h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
    h.requestline = f"GET {path} HTTP/1.1"
    h.headers = {"Host": "127.0.0.1"}
    h.rfile, h.wfile = io.BytesIO(), io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.log_message = lambda *a: None
    h.do_GET()
    head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")


class Endpoints(tp._World):
    """The hub's side, end to end on a PO chat's transcript."""

    def setUp(self):
        super().setUp()
        threads.forget()
        self.addCleanup(threads.forget)
        self.rid = self.solo_room()
        p = mock.patch.object(threads, "is_threaded", lambda rid, projects=None: rid == self.rid)
        p.start()
        self.addCleanup(p.stop)
        # Sent as a new thread (#367): the person's choice makes P1 a thread.
        out, ids = self.send(self.rid, "Make the chart wider", key="k0", at=self.t0)
        threads.note_send(self.rid, "", "k0", ids, False, out, now=self.t0, new=True)
        self.add("sid-1", tp.turn("user", out, self.t0 + 1), tp.turn("assistant", "Done, it is wider.", self.t0 + 2),
                 tp.turn("user", "[digest] all quiet", self.t0 + 10),
                 tp.turn("assistant", "Nothing to do.", self.t0 + 11))
        points.sync(self.rid, force=True)
        threads.build(self.rid)                 # the first read, done in the background by a poll

    def box_sent(self):
        """The thread box's send records but setUp's new-thread one."""
        return [a for a in threads.load_side(self.rid)["sent"] if a.get("key") != "k0"]

    def test_channel_shows_the_parent_and_hides_its_reply(self):
        status, ch = http_get(f"/api/room/channel?id={self.rid}")
        self.assertEqual(status, 200)
        # The answer is a reply; the digest and the PO's note on it stay in
        # the chat as Team activity (the page folds them).
        self.assertEqual([(i["mid"], i.get("thr")) for i in ch["items"]],
                         [("sid-1:0", ["P1"]), ("sid-1:2", None), ("sid-1:3", None)])
        self.assertEqual(ch["activity"]["id"], "team:activity")
        self.assertEqual(ch["threads"]["P1"]["replies"], 1)
        self.assertEqual(ch["threads"]["P1"]["unread"], 0, "what came before threading counts as read")
        same = http_get(f"/api/room/channel?id={self.rid}&sig={ch['sig']}")[1]
        self.assertEqual((same["sig"], same["same"], same.get("items")), (ch["sig"], True, None))
        status, th = http_get(f"/api/room/thread?id={self.rid}&t=P1")
        self.assertEqual([i["mid"] for i in th["items"]], ["sid-1:0", "sid-1:1"])
        self.assertEqual(http_get(f"/api/room/thread?id={self.rid}&mid=sid-1:3")[1]["thread"]["id"], "m:sid-1:3",
                         "Reply in thread on Team activity starts a thread of its own")
        self.assertEqual(http_get(f"/api/room/thread?id={self.rid}&t=P99")[0], 404)

    def test_an_old_topic_param_shows_the_whole_channel(self):
        # P345: Topics were removed; an old &topic= is ignored, the chat comes
        # in pages as without it.
        project = {"id": "p", "poRoomId": self.rid}
        with mock.patch.object(dashboard, "load_projects", return_value=[project]):
            plain = http_get(f"/api/room/channel?id={self.rid}&n=2")[1]
            status, first = http_get(f"/api/room/channel?id={self.rid}&n=2&topic=subject:P1")
            up = http_get(f"/api/room/channel?id={self.rid}&n=2&topic=subject:P1"
                          f"&before={first['items'][0]['mid']}")[1]
        self.assertEqual(status, 200)
        self.assertEqual([i["mid"] for i in first["items"]], [i["mid"] for i in plain["items"]])
        self.assertEqual((first["before"], first["after"]), (plain["before"], plain["after"]))
        self.assertTrue(up["items"], "a page above it")
        self.assertNotIn("topicSuggestion", first)

    def test_a_poll_that_holds_the_channel_gets_what_changed(self):
        # #247: a new balloon reaches a page that holds the channel as a
        # delta: the entries in order, only the new one whole.
        ch = http_get(f"/api/room/channel?id={self.rid}")[1]
        self.add("sid-1", tp.turn("assistant", "Something new.", time.time() + 2))
        self.poll()
        d = http_get(f"/api/room/channel?id={self.rid}&sig={ch['sig']}")[1]
        self.assertTrue(d.get("delta"), d)
        self.assertEqual(d["ids"][:3], [i["mid"] for i in ch["items"]])
        self.assertEqual(list(d["rows"]), [d["ids"][-1]], "only the new entry is sent whole")
        self.assertEqual(d["rows"][d["ids"][-1]]["text"], "Something new.")
        # A sig the hub no longer holds gets the view whole.
        whole = http_get(f"/api/room/channel?id={self.rid}&sig=gone:0")[1]
        self.assertFalse(whole.get("delta"))
        self.assertEqual([i["mid"] for i in whole["items"]], d["ids"])

    def test_the_channel_names_the_sends_its_threads_hold(self):
        # #259: a send confirmed before the threads hold its turn is not in a
        # thread: the page keeps drawing it until the next build brings it.
        dashboard.sends.accept(self.rid, "k-held", "Make the chart wider")
        dashboard.sends.mark(self.rid, ["k-held"], "confirmed", mid="sid-1:0")
        now = time.time() + 2
        dashboard.sends.accept(self.rid, "k-new", "and taller")
        dashboard.sends.mark(self.rid, ["k-new"], "confirmed", mid="sid-1:4")
        threads.build(self.rid, show_progress=False)
        ch = http_get(f"/api/room/channel?id={self.rid}")[1]
        self.assertEqual(ch["sendMids"], ["sid-1:0"], "a turn not read yet is not held")
        self.add("sid-1", tp.turn("user", "and taller", now))
        threads.build(self.rid, show_progress=False)
        d = http_get(f"/api/room/channel?id={self.rid}&sig={ch['sig']}")[1]
        self.assertEqual(d["set"]["sendMids"], ["sid-1:0", "sid-1:4"], "the poll's delta brings it")
        self.assertIn("sid-1:4", d["ids"])

    def test_team_activity_unread_points_to_latest_po_reply_not_hub_traffic(self):
        now = time.time() + 2
        self.add("sid-1", tp.turn("assistant", "An update for you.", now),
                 tp.turn("user", "[digest] more traffic", now + 1))
        self.poll()
        ch = http_get(f"/api/room/channel?id={self.rid}")[1]
        self.assertEqual(ch["activity"]["unread"], 1)
        self.assertEqual(ch["activity"]["lastMid"], "sid-1:4")
        # Team activity is in the channel itself (#234): never in the Threads list.
        self.assertNotIn("team:activity", [t["id"] for t in ch["unreadThreads"]])
        self.assertEqual(ch["activity"]["lastWho"], "po")

    def test_work_log_replies_do_not_raise_unread(self):
        now = time.time() + 2
        asked, ids = self.send(self.rid, "How is #12?", key="k2", at=now)
        threads.note_send(self.rid, "", "k2", ids, False, asked, now=now, new=True)
        report_turn = tp.turn("user", "[report] Review passed", now + 3)
        report_turn.update(kind="report", taskId="#12", taskTitle="Chart")
        self.add("sid-1", tp.turn("user", asked, now), tp.turn("assistant", "Re P2: The task is working.", now + 1),
                 report_turn, tp.turn("assistant", "Review passed; merging now.", now + 4))
        points.sync(self.rid, force=True)
        idx = threads.build(self.rid, show_progress=False)
        tid = next(t for t in idx["threads"] if t not in ("P1", "team:activity"))
        th = threads.thread_view(self.rid, chatroom.get_room(self.rid), tid)
        self.assertEqual(th["thread"]["replies"], 1)
        self.assertEqual(th["thread"]["unread"], 1, "only the explicit PO answer is new")
        self.assertEqual(th["thread"]["workCount"], 2)
        self.assertEqual([m.get("workLog", False) for m in th["items"]], [False, False, True, True])

    def test_a_reply_from_a_thread_names_its_point_and_new_replies_are_unread(self):
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            status, _ = tp.http("/api/room/resume", {"roomId": self.rid, "text": "a bit more", "key": "k1",
                                                     "thread": "P1", "also": True})
        self.assertEqual(status, 200)
        self.assertTrue(sent[0].startswith("Re P1: a bit more"), sent[0])
        row = self.box_sent()[0]
        self.assertEqual((row["thread"], row["key"], row["also"]), ("P1", "k1", True))
        now = time.time()
        self.add("sid-1", tp.turn("user", sent[0], now + 5), tp.turn("assistant", "Re P1: wider still.", now + 6))
        room = self.poll()
        self.assertEqual([(t["id"], t["unread"]) for t in room["points"]["threads"]], [("P1", 1)])
        status, ch = http_get(f"/api/room/channel?id={self.rid}")
        shown = {i["mid"]: i for i in ch["items"]}
        self.assertEqual(shown["sid-1:4"].get("inThr"), "P1", "sent to the chat too: there, marked")
        self.assertNotIn("sid-1:5", shown, "the PO's answer in the thread is a reply")
        self.assertEqual(tp.http("/api/room/thread/seen", {"roomId": self.rid, "thread": "P1", "ts": now + 6})[0], 200)
        self.assertEqual(self.poll()["points"]["threads"], [])
        for bad in ("nan", "inf", now + 10 * 86400):
            self.assertEqual(tp.http("/api/room/thread/seen", {"roomId": self.rid, "thread": "P1", "ts": bad})[0], 400, bad)

    def test_an_unticked_thread_reply_never_shows_in_the_chat_even_after_a_restart(self):
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            self.assertEqual(tp.http("/api/room/resume", {"roomId": self.rid, "text": "is this ready?", "key": "k9",
                                                          "thread": "P1"})[0], 200)
        self.assertEqual([(s["key"], s.get("thread"), s.get("also")) for s in dashboard.sends.view(self.rid)],
                         [("k9", "P1", None)], "the page knows it is the thread's")
        # Read late by a busy PO, after a hub restart.
        threads.forget()
        now = time.time()
        self.add("sid-1", tp.turn("user", sent[0], now + 900), tp.turn("assistant", "Re P1: yes.", now + 901))
        self.poll()
        status, ch = http_get(f"/api/room/channel?id={self.rid}")
        self.assertNotIn("sid-1:4", {i["mid"] for i in ch["items"]})
        status, th = http_get(f"/api/room/thread?id={self.rid}&t=P1")
        self.assertIn("sid-1:4", [i["mid"] for i in th["items"]])

    def test_a_refused_thread_reply_leaves_no_record(self):
        def refuse(h, room, text="", to="", key="", quiet=False):
            raise dashboard.StartRoomError("no")
        with mock.patch.object(dashboard.Handler, "_resume_room", refuse), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            status, body = tp.http("/api/room/resume", {"roomId": self.rid, "text": "x", "key": "k8", "thread": "P1"})
        self.assertEqual(status, 400)
        if not body.get("kept"):
            self.assertEqual(self.box_sent(), [])

    def test_a_send_confirmed_by_its_posts_id_keeps_its_balloon(self):
        # Review 2: a team chat's post confirms its send by id (sends.mark).
        dashboard.sends.accept(self.rid, "kp", "Re P1: posted", thread="P1")
        threads.note_send(self.rid, "P1", "kp", [], False, "Re P1: posted")
        dashboard.sends.mark(self.rid, ["kp"], "confirmed", mid="m-123")
        self.assertEqual([(a["key"], a.get("mid")) for a in self.box_sent()], [("kp", "m-123")])

    def test_only_a_turn_typed_after_a_thread_reply_confirms_it(self):
        # Review 4: the same words typed in the terminal 10 s before the
        # thread reply are never its receipt; the turn after it is, once.
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            self.assertEqual(tp.http("/api/room/resume", {"roomId": self.rid, "text": "ship it", "key": "k7",
                                                          "thread": "P1"})[0], 200)
        sends = dashboard.sends
        sends.mark(self.rid, ["k7"], "delivered")
        at = sends.get(self.rid, "k7")["at"]
        self.add("sid-1", tp.turn("user", sent[0], at - 10), tp.turn("assistant", "Shipping.", at - 9))
        sends.sync(self.rid, force=True, now=at + 5)
        self.assertEqual(sends.get(self.rid, "k7")["state"], "delivered", "a turn from before it is not it")
        self.add("sid-1", tp.turn("user", sent[0], at + 20), tp.turn("assistant", "Re P1: shipped.", at + 21))
        sends._SCANNED.clear()          # this world's transcripts keep one stat
        sends.sync(self.rid, force=True, now=at + 25)
        s = sends.get(self.rid, "k7")
        self.assertEqual((s["state"], s["mid"]), ("confirmed", "sid-1:6"))
        self.assertEqual([a.get("mid") for a in self.box_sent()], ["sid-1:6"])
        self.poll()
        shown = {i["mid"] for i in http_get(f"/api/room/channel?id={self.rid}")[1]["items"]}
        self.assertIn("sid-1:4", shown, "typed in the chat: the chat's")
        self.assertNotIn("sid-1:6", shown, "the thread's reply: in its thread alone")
        sends.sync(self.rid, force=True, now=at + 60)
        self.assertEqual(sends.get(self.rid, "k7")["mid"], "sid-1:6", "once")

    def test_discard_and_withdraw_take_the_thread_record_back(self):
        # Review 1: a reply taken back is no thread's; the same words typed
        # in the chat later are the chat's.
        sends = dashboard.sends
        for key, take_back in (("kd", "discard"), ("kw", "withdraw")):
            text = "Re P1: ship it"
            sends.accept(self.rid, key, text, thread="P1")
            threads.note_send(self.rid, "P1", key, [], False, text)
            if take_back == "discard":
                sends.mark(self.rid, [key], "failed", "no")
                self.assertTrue(dashboard.discard_pending(self.rid, key))
            else:
                sends.mark(self.rid, [key], "confirmed", mid="sid-1:9")
                with mock.patch.object(dashboard.chatroom, "withdraw_message", return_value=True):
                    self.assertTrue(dashboard.withdraw_send(self.rid, key))
            self.assertEqual([a["key"] for a in self.box_sent()], [], take_back)

    def settle(self):
        """The background build a poll started, done."""
        for _ in range(500):
            with threads._LOCK:
                if self.rid not in threads._BUILDING:
                    return
            time.sleep(0.01)
        self.fail("the build did not end")

    def poll(self):
        """A poll once the chat's threads are current (the first serves the last ones)."""
        dashboard._annotate_room_liveness(chatroom.get_room(self.rid), with_points=True)
        self.settle()
        return dashboard._annotate_room_liveness(chatroom.get_room(self.rid), with_points=True)

    def test_while_the_po_writes_the_last_threads_are_served(self):
        room = chatroom.get_room(self.rid)
        first = threads.ready(self.rid, room)
        self.add("sid-1", tp.turn("assistant", "One more thing.", time.time()))
        stale = threads.ready(self.rid, room)
        self.assertIs(stale, first, "a poll never waits on a build, nor gets nothing")
        self.settle()
        fresh = threads.ready(self.rid, room)
        self.assertIsNot(fresh, first)
        self.assertIn("sid-1:4", fresh["by_mid"])

    def test_warm_rebuild_reply_has_no_progress(self):
        self.add("sid-1", tp.turn("assistant", "One more thing.", time.time()))
        entered, release = threading.Event(), threading.Event()
        original = threads.balloons

        def slow_balloons(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)

        try:
            with mock.patch.object(threads, "balloons", side_effect=slow_balloons):
                status, reply = http_get(f"/api/room/channel?id={self.rid}")
                self.assertEqual(status, 200)
                self.assertTrue(entered.wait(2))
                self.assertIsNone(reply.get("progress"), reply)
                status, same = http_get(f"/api/room/channel?id={self.rid}&sig={reply['sig']}")
                self.assertEqual(status, 200)
                self.assertIsNone(same.get("progress"), same)
        finally:
            release.set()
            self.settle()

    def test_a_reply_from_a_thread_keeps_what_it_means(self):
        for text in ("ok", "thanks!", "/compact", "[digest] all quiet"):
            self.assertEqual(threads.reply_text(self.rid, chatroom.get_room(self.rid), "P1", text), text)
        self.assertEqual(threads.reply_text(self.rid, chatroom.get_room(self.rid), "P1", "and the axis?"), "Re P1: and the axis?")

    def test_a_bad_seen_time_is_skipped(self):
        sd = threads.load_side(self.rid)
        sd["seen"] = {"P1": "soon", "P2": None}
        threads._save_side(self.rid, sd)
        threads.forget(self.rid)
        self.assertIsNotNone(threads.build(self.rid))


    def test_a_message_sent_as_a_new_thread_starts_one_and_the_main_box_does_not(self):
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            self.assertEqual(tp.http("/api/room/resume", {"roomId": self.rid, "text": "make it blue", "key": "kb",
                                                          "newThread": True})[0], 200)
            self.assertEqual(tp.http("/api/room/resume", {"roomId": self.rid, "text": "and the title bold",
                                                          "key": "kc"})[0], 200)
        self.assertEqual([(a["key"], a["thread"], a.get("new")) for a in self.box_sent()], [("kb", "", True)])
        now = time.time()
        self.add("sid-1", tp.turn("user", sent[0], now + 5), tp.turn("assistant", "Re P2: blue now.", now + 6),
                 tp.turn("user", sent[1], now + 7), tp.turn("assistant", "Re P3: bold now.", now + 8))
        points.sync(self.rid, force=True)
        idx = threads.build(self.rid, show_progress=False)
        self.assertEqual(idx["threads"]["P2"]["members"], ["sid-1:4", "sid-1:5"])
        self.assertEqual(idx["threads"]["P3"]["members"], ["sid-1:6"])
        self.assertEqual(idx["of"]["sid-1:7"], ["m:sid-1:7"])
        ch = http_get(f"/api/room/channel?id={self.rid}")[1]
        self.assertEqual([i["mid"] for i in ch["items"]][-3:], ["sid-1:4", "sid-1:6", "sid-1:7"])
        self.assertEqual(ch["threads"]["P2"]["replies"], 1)


class InboxLook(tp._World):
    """#302 review 2: the inbox reads a PO chat's threads not opened since the
    hub started as a look only: the chat's record stays as it was."""

    def setUp(self):
        super().setUp()
        threads.forget()
        self.addCleanup(threads.forget)
        self.rid = self.solo_room()
        p = mock.patch.object(threads, "is_threaded", lambda rid, projects=None: rid == self.rid)
        p.start()
        self.addCleanup(p.stop)
        out, _ = self.send(self.rid, "Make the chart wider", at=self.t0)
        self.add("sid-1", tp.turn("user", out, self.t0 + 1), tp.turn("assistant", "Done, it is wider.", self.t0 + 2))
        points.sync(self.rid, force=True)

    def test_a_cold_inbox_read_writes_nothing(self):
        import inbox
        inbox.bind(dashboard)
        inbox._COLD_THREADS.clear()
        self.addCleanup(inbox._COLD_THREADS.clear)
        side = threads._path(self.rid)
        before = side.read_bytes() if side.exists() else None
        saves = threads.SAVES.get(self.rid, 0)
        with mock.patch.object(threads, "confirmed", side_effect=AssertionError("pairs kept")) as kept:
            self.assertIsNone(inbox._thread_index(threads, self.rid), "the first look is pending")
            for t in threading.enumerate():
                if t.name.startswith("inbox-threads-"):
                    t.join(30)
            idx = inbox._thread_index(threads, self.rid)
        self.assertIn("P1", idx["threads"])
        self.assertFalse(kept.called)
        self.assertEqual(side.read_bytes() if side.exists() else None, before, "the record is unchanged")
        self.assertEqual(threads.SAVES.get(self.rid, 0), saves)
        self.assertNotIn(self.rid, threads._INDEX, "the chat's own view still makes its first build")
        # The chat's own first build still sets its record.
        threads.build(self.rid, show_progress=False)
        self.assertTrue(threads.load_side(self.rid).get("since"))


class ReplyRoleLabels(unittest.TestCase):
    def test_report_and_po_replies_keep_their_sender_roles(self):
        def reply(kind, **turn):
            return {"role": "user", "kind": kind, "turn": turn}

        self.assertEqual(threads._reply_label({"role": "agent", "turn": {}}),
                         {"cls": "po", "text": "PO"})
        self.assertEqual(threads._reply_label(reply("report", reportKind="completed")),
                         {"cls": "engineer", "text": "engineer"})
        self.assertEqual(threads._reply_label(reply("report", reportKind="review 2 (approved)")),
                         {"cls": "reviewer", "text": "reviewer"})
        self.assertEqual(threads._reply_label(reply("human")),
                         {"cls": "person", "text": "you"})


if __name__ == "__main__":
    unittest.main()
