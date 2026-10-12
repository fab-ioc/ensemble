"""Threads in a PO chat (threads.py, #210): the hub places every balloon of a
PO chat in a thread from facts it already has.

* a person's message starts a thread per point it makes; one with no point
  in the ledger a thread of its own; a follow-up (``replyTo``, ``Re Pn``, a
  card's answer, a reply sent from a thread) is in the thread it follows;
* a PO message is in the threads its ``Re Pn`` names (one copy, each), else
  in the thread of the input before it; after a hub note it is in none;
* a task's report is in the thread of the latest point about the task, else
  in a thread of the task's own;
* an ``Ask:`` or "is live" PO message and a reply sent to the chat too show in
  the channel; a thread's title is its first words, not a quote or a number;
* an old hub line the provenance journal did not claim is a hub line, not the
  person's; the side record keeps what was seen, anchors and also-sends.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import points
import threads

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

    def test_a_hub_note_breaks_the_chain_and_what_answers_it_is_in_no_thread(self):
        out = self.run_([human("s:0", 0, "a"), note("s:1", 5), po("s:2", 6, "Digest read.")],
                        [point("P1", "s:0", "a")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0"])
        self.assertNotIn("s:1", out["of"])
        self.assertNotIn("s:2", out["of"])

    def test_review_comments_are_a_thread_each_and_a_reply_to_both_is_in_both(self):
        recs = [human("s:0", 0, "## Review comments (2)\n**1.** one\n**2.** two"),
                po("s:1", 5, "Re P2: yes\n\nRe P3: no", re_=["P2", "P3"])]
        out = self.run_(recs, [point("P2", "s:0", "**1.** one"), point("P3", "s:0", "**2.** two")])
        self.assertEqual(out["parent"]["s:0"], ["P2", "P3"])
        self.assertEqual(out["of"]["s:1"], ["P2", "P3"])
        self.assertEqual(out["threads"]["P2"]["title"], "one")

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
        self.assertEqual(out["also"], {"s:1": "P1"})          # the question shows in the channel

    def test_a_reply_sent_from_a_thread_is_in_it(self):
        recs = [report("s:0", 0, "#12", "Wider chart"), human("s:1", 5, "looks good")]
        out = self.run_(recs, [point("P7", "s:1", "looks good", 5)], side(anchors={"P7": "task:#12"}))
        self.assertEqual(out["threads"]["task:#12"]["members"], ["s:0", "s:1"])
        self.assertNotIn("P7", out["threads"])

    def test_reply_in_thread_on_a_loose_balloon_makes_it_the_parent(self):
        recs = [note("s:0", 0), po("s:1", 1, "Digest: all quiet"), human("s:2", 5, "and #9?")]
        out = self.run_(recs, [point("P8", "s:2", "and #9?", 5)], side(anchors={"P8": "m:s:1"}))
        self.assertEqual(out["threads"]["m:s:1"]["members"], ["s:1", "s:2"])
        self.assertEqual(out["parent"]["s:1"], ["m:s:1"])

    def test_a_report_goes_to_the_latest_point_about_its_task_else_its_own_thread(self):
        recs = [human("s:0", 0, "build it"), po("s:1", 5, "Re P1 (planned #12): started"),
                report("s:2", 50, "#12"), report("s:3", 60, "#13", "Other work"), po("s:4", 61, "ok")]
        out = self.run_(recs, [point("P1", "s:0", "build it", state="planned", task="#12")],
                        title=lambda ref: "never asked")
        self.assertIn("s:2", out["threads"]["P1"]["members"])
        th = out["threads"]["task:#13"]
        self.assertEqual((th["title"], th["members"]), ("#13 Other work", ["s:3", "s:4"]))

    def test_a_point_naming_one_task_claims_its_reports(self):
        recs = [human("s:0", 0, "how is #12 going?"), report("s:1", 50, "#12")]
        out = self.run_(recs, [point("P1", "s:0", "how is #12 going?")])
        self.assertEqual(out["threads"]["P1"]["members"], ["s:0", "s:1"])
        self.assertEqual(out["threads"]["P1"]["task"], "#12")

    def test_a_message_with_no_point_is_a_thread_of_its_own(self):
        out = self.run_([human("s:0", 0, "hello there"), po("s:1", 2, "hi")], [])
        self.assertEqual(out["threads"]["m:s:0"]["members"], ["s:0", "s:1"])

    def test_live_news_shows_in_the_channel_and_an_old_re_is_ignored(self):
        recs = [human("s:0", 0, "a"), po("s:1", 1000, "Re P9: #12 is live", re_=["P9"], live=True)]
        out = self.run_(recs, [point("P1", "s:0", "a"), point("P9", "x:9", "later", 5000)])
        self.assertEqual(out["of"]["s:1"], ["P1"])             # P9 came later: the chain decides
        self.assertEqual(out["also"], {"s:1": "P1"})

    def test_a_reply_sent_to_the_chat_too_is_marked(self):
        recs = [human("s:0", 0, "a"), po("s:1", 5, "b"), human("s:2", 100, "Re P1: also this", re_=["P1"])]
        sd = side(also=[{"thread": "P1", "key": "k", "at": T0 + 100, "words": "also this"}])
        out = self.run_(recs, [point("P1", "s:0", "a")], sd)
        self.assertEqual(out["also"], {"s:2": "P1"})


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
        self.assertTrue(threads.mark_seen(rid, "P1", T0))
        self.assertFalse(threads.mark_seen(rid, "P1", T0 - 1))
        threads.note_send(rid, "task:#12", "k1", ["P7"], True, "Re P3: ship it")
        threads.note_send(rid, "P3", "k2", ["P3"], False, "x")
        threads.forget()
        sd = threads.load_side(rid)
        self.assertEqual(sd["seen"], {"P1": T0})
        self.assertEqual(sd["anchors"], {"P7": "task:#12"})
        self.assertEqual([a["words"] for a in sd["also"]], ["ship it"])

    def test_since_is_set_once(self):
        rid = "room-abc124"
        threads._ensure_since(rid, T0)
        threads._ensure_since(rid, T0 + 99)
        self.assertEqual(threads.load_side(rid)["since"], T0)


if __name__ == "__main__":
    unittest.main()
