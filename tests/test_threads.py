"""Threads in a PO chat (threads.py, #210): the hub places every balloon of a
PO chat in a thread from facts it already has.

* a person's message starts a thread per point it makes; one with no point
  in the ledger a thread of its own; a follow-up (``replyTo``, ``Re Pn``, a
  card's answer, a reply sent from a thread) is in the thread it follows;
* a PO message is in the threads its ``Re Pn`` names (one copy, each), else
  in the thread of the input or PO balloon before it; after a hub note it
  joins Team activity;
* a task's report is in the thread of the latest point about the task, else
  in Team activity;
* an ``Ask:`` or "is live" PO message and a reply sent to the chat too remain
  in their threads; a thread's title is its first words, not a quote or number;
* the channel (#234) is the whole chat but a thread's replies: its first
  message, what the person typed in the chat's own box (a follow-up there is
  in the chat, marked with its thread), a reply sent to the chat too, an ask
  or "is live", and the hub's and tasks' traffic no thread claimed;
* an old hub line the provenance journal did not claim is a hub line, not the
  person's; the side record keeps what was seen, anchors and also-sends.
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


class AssignTest(unittest.TestCase):
    def run_(self, recs, pts, sd=None, title=None):
        return threads.assign(recs, {"points": pts}, sd or side(), title)

    def test_a_point_starts_a_thread_and_the_po_answer_joins_it(self):
        out = self.run_([human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done.")],
                        [point("P1", "s:0", "make the chart wider")])
        th = out["threads"]["P1"]
        self.assertEqual(th["members"], ["s:0", "s:1"])
        self.assertEqual(th["title"], "make the chart wider")
        self.assertEqual(out["parent"], {"s:0": ["P1"]})

    def test_a_hub_note_and_its_answer_live_in_team_activity(self):
        out = self.run_([human("s:0", 0, "a"), note("s:1", 5), po("s:2", 6, "Digest read.")],
                        [point("P1", "s:0", "a")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:1", "s:2"])

    def test_review_comments_are_a_thread_each_and_a_reply_to_both_is_in_both(self):
        recs = [human("s:0", 0, "## Review comments (2)\n**1.** one\n**2.** two"),
                po("s:1", 5, "Re P2: yes\n\nRe P3: no", re_=["P2", "P3"])]
        out = self.run_(recs, [point("P2", "s:0", "**1.** one"), point("P3", "s:0", "**2.** two")])
        self.assertEqual(out["parent"]["s:0"], ["P2", "P3"])
        self.assertEqual(out["of"]["s:1"], ["P2", "P3"])
        self.assertEqual(out["threads"]["P2"]["title"], "one")
        # A reply is present in its threads only, even when it answers several.
        self.assertEqual(out["also"], {})
        idx = {**out, "recs": recs, "by_mid": {r["mid"]: r for r in recs}}
        self.assertEqual(threads._channel(idx), ["s:0"])
        self.assertEqual(threads._turn(idx, "s:1", "P2")["alsoIn"], ["P3"])
        self.assertEqual(threads._turn(idx, "s:1", "P3")["alsoIn"], ["P2"])

    def test_a_follow_up_point_and_a_re_message_are_in_the_thread_they_follow(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "answer"),
                human("s:2", 10, "> answer\n\nwhy?"), po("s:3", 12, "because"),
                human("s:4", 20, "Re P1: still wrong", re_=["P1"])]
        pts = [point("P1", "s:0", "a", followUps=["s:4"]),
               point("P4", "s:2", "> answer\n\nwhy?", 10, replyTo="P1")]
        out = self.run_(recs, pts)
        self.assertEqual(list(out["threads"]), ["P1"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2", "s:3", "s:4"])
        self.assertEqual(out["threads"]["P1"]["points"], ["P1", "P4"])
        # A point of its own that opens with "Re P1" follows P1 up too.
        recs.append(human("s:5", 30, "Re P1: and this", re_=["P1"]))
        out = self.run_(recs, pts + [point("P5", "s:5", "Re P1: and this", 30)])
        self.assertEqual(list(out["threads"]), ["P1"])
        self.assertEqual(out["threads"]["P1"]["points"], ["P1", "P4", "P5"])

    def test_a_card_answer_is_in_the_cards_thread(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "Ask: which?\n- x\n- y", ask=True),
                human("s:2", 10, "x")]
        pts = [point("P1", "s:0", "a"), point("P2", "s:2", "x", 10, key="ask:s:1:0")]
        out = self.run_(recs, pts)
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        # The question is in the chat too; the answer clicked there is as well.
        self.assertEqual(out["also"], {"s:1": "P1", "s:2": "P1"})

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

    def test_a_report_goes_to_the_latest_point_about_its_task_else_team_activity(self):
        recs = [human("s:0", 0, "build it"), po("s:1", 5, "Re P1 (planned #12): started"),
                report("s:2", 50, "#12"), report("s:3", 60, "#13", "Other work"), po("s:4", 61, "ok")]
        out = self.run_(recs, [point("P1", "s:0", "build it", state="planned", task="#12")],
                        title=lambda ref: "never asked")
        self.assertIn("s:2", out["threads"]["P1"]["members"])
        th = out["threads"]["team:activity"]
        self.assertEqual((th["title"], th["members"]), ("Team activity", ["s:3", "s:4"]))

    def test_a_point_naming_one_task_claims_its_reports(self):
        recs = [human("s:0", 0, "how is #12 going?"), report("s:1", 50, "#12")]
        out = self.run_(recs, [point("P1", "s:0", "how is #12 going?")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1"])
        self.assertEqual(out["threads"]["P1"]["task"], "#12")

    def test_a_point_naming_two_tasks_claims_the_reports_of_both(self):
        recs = [human("s:0", 0, "Status of #12 and #13?"), report("s:1", 50, "#12"), report("s:2", 60, "#13")]
        out = self.run_(recs, [point("P1", "s:0", "Status of #12 and #13?")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2"])
        self.assertEqual(list(out["threads"]), ["P1"])

    def test_a_message_with_no_point_is_a_thread_of_its_own(self):
        out = self.run_([human("s:0", 0, "hello there"), po("s:1", 2, "hi")], [])
        self.assertEqual(out["threads"]["m:s:0"]["members"], ["s:0", "s:1"])

    def test_every_message_kind_has_one_home_and_the_channel_is_all_but_replies(self):
        recs = [human("s:0", 0, "first topic"), po("s:1", 1, "answer"),
                po("s:2", 2, "a new PO topic"), note("s:3", 3),
                po("s:4", 4, "digest acknowledged"), report("s:5", 5, "#13"),
                human("s:6", 6, "Re P1: another thought", re_=["P1"]),
                po("s:7", 7, "Ask: choose one\n- A\n- B", ask=True),
                po("s:8", 8, "#13 is live", live=True)]
        out = self.run_(recs, [point("P1", "s:0", "first topic", followUps=["s:6"])])
        idx = {**out, "recs": recs}
        # The answer and the PO's next words are replies; the hub note, its
        # answer and the report are Team activity (in the chat, folded by the
        # page); the person's follow-up typed in the chat, the ask and the
        # live news are in the chat too.
        self.assertEqual(threads._channel(idx), ["s:0", "s:3", "s:4", "s:5", "s:6", "s:7", "s:8"])
        self.assertEqual(out["also"], {"s:6": "P1", "s:7": "P1", "s:8": "P1"})
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:3", "s:4", "s:5"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2", "s:6", "s:7", "s:8"])
        self.assertEqual(set(out["of"]), {r["mid"] for r in recs})

    def test_po_turn_stays_together_after_a_human_or_digest(self):
        recs = [po("s:0", 0, "Unprompted topic"), po("s:1", 1, "more on topic"),
                human("s:2", 2, "please fix"), po("s:3", 3, "working"), po("s:4", 4, "done"),
                note("s:5", 5), po("s:6", 6, "reading digest"), po("s:7", 7, "digest details")]
        out = self.run_(recs, [point("P1", "s:2", "please fix", 2)])
        self.assertEqual(out["threads"]["m:s:0"]["members"], ["s:0", "s:1"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:2", "s:3", "s:4"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:5", "s:6", "s:7"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2", "s:5", "s:6", "s:7"])

    def test_po_point_mention_after_a_digest_claims_the_answer_turn(self):
        recs = [human("s:0", 0, "three problems"), note("s:1", 1),
                po("s:2", 2, "I've started three tasks for your P5."),
                po("s:3", 3, "The first task is working now.")]
        out = self.run_(recs, [point("P5", "s:0", "three problems")])
        self.assertEqual(out["threads"]["P5"]["members"], ["s:0", "s:2", "s:3"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:1"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])
        self.assertEqual(threads._point_mentions("Re P506–P509: done"),
                         ["P506", "P509", "P507", "P508"])

    def test_po_ask_or_live_after_team_activity_starts_a_visible_topic(self):
        recs = [note("s:0", 0), po("s:1", 1, "Ask: shall we deploy?\n- Yes\n- No", ask=True),
                po("s:2", 2, "The change is ready."), report("s:3", 3, "#12"),
                po("s:4", 4, "#12 is live", live=True)]
        out = self.run_(recs, [])
        self.assertEqual(out["threads"]["m:s:1"]["members"], ["s:1", "s:2"])
        self.assertEqual(out["threads"]["m:s:4"]["members"], ["s:4"])
        self.assertEqual(out["threads"]["team:activity"]["members"], ["s:0", "s:3"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1", "s:3", "s:4"])

    def test_live_news_shows_in_the_channel_and_an_old_re_is_ignored(self):
        recs = [human("s:0", 0, "a"), po("s:1", 1000, "Re P9: #12 is live", re_=["P9"], live=True)]
        out = self.run_(recs, [point("P1", "s:0", "a"), point("P9", "x:9", "later", 5000)])
        self.assertEqual(out["of"]["s:1"], ["P1"])             # P9 came later: the chain decides
        self.assertEqual(out["also"], {"s:1": "P1"})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:1"])

    def test_a_reply_sent_to_the_chat_too_is_marked(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "Re P1: also this", re_=["P1"])]
        sd = side(also=[{"thread": "P1", "key": "k", "at": T0 + 100, "words": "also this"}],
                  anchors={"P2": "P1"})
        out = self.run_(recs, [point("P1", "s:0", "a"), point("P2", "s:2", "Re P1: also this", 100)], sd)
        self.assertEqual(out["also"], {"s:2": "P1"})
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])

    def test_a_reply_sent_from_a_thread_box_stays_out_of_the_channel(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "Re P1: only here", re_=["P1"]),
                human("s:3", 200, "Re P1: typed in the chat", re_=["P1"])]
        pts = [point("P1", "s:0", "a"), point("P2", "s:2", "Re P1: only here", 100),
               point("P3", "s:3", "Re P1: typed in the chat", 200)]
        out = self.run_(recs, pts, side(anchors={"P2": "P1"}))
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:2", "s:3"])
        # The thread box's reply is in the thread alone; the chat's own is in both.
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:3"])
        self.assertEqual(out["also"], {"s:3": "P1"})

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
        # Review 1: "Re P2" typed in P1's box. P2 keeps its thread and its
        # first message; the reply is in P1 (where it was sent) and in P2.
        recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "Done."),
                human("s:2", 10, "and the legend"), po("s:3", 15, "Done too."),
                human("s:4", 100, "Re P2: smaller please", re_=["P2"])]
        pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the legend", 10, followUps=["s:4"])]
        out = self.run_(recs, pts, side(sent=[self.sent_row("Re P2: smaller please", 100)]))
        self.assertEqual(out["threads"]["P2"]["members"], ["s:2", "s:3", "s:4"])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1", "s:4"])
        self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0", "s:2"])
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
        img = "[image] C:/Users/x/shot.png"
        quote = "> Done." + chr(10) * 2 + "not quite: wider"
        for sent, turn in ((quote, quote),
                           ("see [the spec](https://example.com/x) again", "see [the spec](https://example.com/x) again"),
                           (img, img),                                                   # an image alone
                           ("look at this" + chr(10) + img, "look at this [Image #1]")):  # Codex names it
            recs = [human("s:0", 0, "a"), po("s:1", 5, "Done."), human("s:2", 100, turn)]
            out = self.run_(recs, [point("P1", "s:0", "a")], side(sent=[self.sent_row(sent, 100)]))
            self.assertEqual(threads._channel({**out, "recs": recs}), ["s:0"], sent)
            self.assertIn("s:2", out["threads"]["P1"]["members"], sent)

    def test_a_record_from_before_237_keeps_its_old_placement(self):
        recs, pts = self.leak_case("Re P1: ship it")
        out = self.run_(recs, pts, side(also=[{"thread": "P1", "key": "k", "at": T0 + 100, "words": "ship it"}]))
        self.assertEqual(out["also"], {"s:2": "P1"})

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
        with mock.patch.object(threads, "ready", return_value=idx):
            plain = threads.channel_view("r", {})
            linked = threads.channel_view("r", {}, upto="s:50")
            gone = threads.channel_view("r", {}, upto="s:nope")
        self.assertEqual((len(plain["items"]), plain["n"]), (threads.CHANNEL_MAX, threads.CHANNEL_MAX))
        self.assertIn("s:50", [t["mid"] for t in linked["items"]])
        self.assertEqual(linked["n"], 400 - 50 + 20)
        self.assertEqual(linked["before"], 30)
        self.assertEqual(gone["n"], threads.CHANNEL_MAX)


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
        out, _ = self.send(self.rid, "Make the chart wider", at=self.t0)
        self.add("sid-1", tp.turn("user", out, self.t0 + 1), tp.turn("assistant", "Done, it is wider.", self.t0 + 2),
                 tp.turn("user", "[digest] all quiet", self.t0 + 10),
                 tp.turn("assistant", "Nothing to do.", self.t0 + 11))
        points.sync(self.rid, force=True)
        threads.build(self.rid)                 # the first read, done in the background by a poll

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
        self.assertEqual(http_get(f"/api/room/channel?id={self.rid}&sig={ch['sig']}")[1], {"sig": ch["sig"], "same": True})
        status, th = http_get(f"/api/room/thread?id={self.rid}&t=P1")
        self.assertEqual([i["mid"] for i in th["items"]], ["sid-1:0", "sid-1:1"])
        self.assertEqual(http_get(f"/api/room/thread?id={self.rid}&mid=sid-1:3")[1]["thread"]["id"], "m:sid-1:3",
                         "Reply in thread on Team activity starts a thread of its own")
        self.assertEqual(http_get(f"/api/room/thread?id={self.rid}&t=P99")[0], 404)

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

    def test_a_reply_from_a_thread_names_its_point_and_new_replies_are_unread(self):
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="", quiet=False: sent.append(text) or {"delivered": 1}), \
                mock.patch.object(dashboard, "_send_left_behind", return_value=""):
            status, _ = tp.http("/api/room/resume", {"roomId": self.rid, "text": "a bit more", "key": "k1",
                                                     "thread": "P1", "also": True})
        self.assertEqual(status, 200)
        self.assertTrue(sent[0].startswith("Re P1: a bit more"), sent[0])
        row = threads.load_side(self.rid)["sent"][0]
        self.assertEqual((row["thread"], row["key"], row["also"]), ("P1", "k1", True))
        now = time.time()
        self.add("sid-1", tp.turn("user", sent[0], now + 5), tp.turn("assistant", "Re P1: wider still.", now + 6))
        room = self.poll()
        self.assertEqual([(t["id"], t["unread"]) for t in room["threadsUnread"]], [("P1", 1)])
        status, ch = http_get(f"/api/room/channel?id={self.rid}")
        shown = {i["mid"]: i for i in ch["items"]}
        self.assertEqual(shown["sid-1:4"].get("inThr"), "P1", "sent to the chat too: there, marked")
        self.assertNotIn("sid-1:5", shown, "the PO's answer in the thread is a reply")
        self.assertEqual(tp.http("/api/room/thread/seen", {"roomId": self.rid, "thread": "P1", "ts": now + 6})[0], 200)
        self.assertEqual(self.poll()["threadsUnread"], [])
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
            self.assertEqual(threads.load_side(self.rid)["sent"], [])

    def test_a_send_confirmed_by_its_posts_id_keeps_its_balloon(self):
        # Review 2: a team chat's post confirms its send by id (sends.mark).
        dashboard.sends.accept(self.rid, "kp", "Re P1: posted", thread="P1")
        threads.note_send(self.rid, "P1", "kp", [], False, "Re P1: posted")
        dashboard.sends.mark(self.rid, ["kp"], "confirmed", mid="m-123")
        self.assertEqual([(a["key"], a.get("mid")) for a in threads.load_side(self.rid)["sent"]], [("kp", "m-123")])

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
            self.assertEqual([a["key"] for a in threads.load_side(self.rid)["sent"]], [], take_back)

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


if __name__ == "__main__":
    unittest.main()
