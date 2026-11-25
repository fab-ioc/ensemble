"""A PO's mid-turn lines fold into the reply that ends the turn (#402, issue 61)."""
from __future__ import annotations

import copy
import unittest

import turn_steps


def agent(mid, text, interim=False, sid="s1", **kw):
    t = {"role": "assistant", "text": text, "timestamp": f"2026-11-19T14:{mid[-2:]}:00Z"}
    if interim:
        t["interim"] = True
    return {"mid": mid, "sid": sid, "role": "agent", "ts": 0.0, "turn": t,
            "re": kw.get("re", []), "ask": kw.get("ask", False), "live": False}


def user(mid, text, sid="s1", kind="human"):
    return {"mid": mid, "sid": sid, "role": "user", "ts": 0.0,
            "turn": {"role": "user", "text": text, "kind": kind}, "kind": kind}


def texts(recs):
    return [r["turn"]["text"] for r in recs]


class FoldTest(unittest.TestCase):
    def test_four_steps_then_the_answer(self):
        recs = [user("m00", "[handover] carry on", kind="hub"),
                agent("m01", "Carrying on with: merging #395 Part 1", True),
                agent("m02", "Merging #395 Part 1 now.", True),
                agent("m03", "Checking every page and the hub log.", True),
                agent("m04", "Now update the handover.", True),
                agent("m05", "I merged #395 Part 1.")]
        before = copy.deepcopy(recs)
        out, step_of, open_mid = turn_steps.fold(recs, "s1")
        self.assertEqual(texts(out), ["[handover] carry on", "I merged #395 Part 1."])
        steps = out[1]["turn"]["steps"]
        self.assertEqual([s["mid"] for s in steps], ["m01", "m02", "m03", "m04"])
        self.assertEqual(steps[1]["text"], "Merging #395 Part 1 now.")
        self.assertTrue(steps[0]["timestamp"])
        self.assertEqual(step_of, {"m01": "m05", "m02": "m05", "m03": "m05", "m04": "m05"})
        self.assertEqual(open_mid, "")
        self.assertEqual(recs, before, "the cached balloons are never changed")

    def test_a_single_message_is_as_it_was(self):
        recs = [user("m00", "hi"), agent("m01", "Hello.")]
        out, step_of, open_mid = turn_steps.fold(recs, "s1")
        self.assertIs(out[1], recs[1])
        self.assertNotIn("steps", out[1]["turn"])
        self.assertEqual((step_of, open_mid), ({}, ""))

    def test_kept_lines_stay_messages(self):
        cases = [
            agent("m01", "Re P5: done, live now.", True, re=["P5"]),
            agent("m02", "Ask: which one?", True, ask=True),
            agent("m03", "See [the plan](/home/alex/Docs/plan.html).", True),
            agent("m04", "Built at https://example.org/x", True),
            agent("m05", "[image] /home/alex/shot.png", True),
            agent("m06", "The drawing is /home/alex/Ensemble Projects/Docs/#402 plan.png now", True),
            # Review 1: a relative or bare file name is a file too.
            agent("m09", "See src/report.md", True),
            agent("m10", "README.md is updated.", True),
            agent("m11", "Reading threads.py and session.html", True),
            agent("m12", "Fixed at ./a/b.py:12.", True),
        ]
        for rec in cases:
            with self.subTest(text=rec["turn"]["text"]):
                self.assertTrue(turn_steps.kept(rec))
        self.assertFalse(turn_steps.kept(agent("m07", "Merging #395 now.", True)))
        for text in ("Checking every page.", "v1.2 is out, 0.45/0.53 of it", "e.g. P413/P422", "a 3/4.5x ratio"):
            with self.subTest(text=text):
                self.assertFalse(turn_steps.kept(agent("m08", text, True)), "no dotted word is a file")

    def test_steps_before_a_kept_line_are_its_own(self):
        recs = [user("m00", "go"),
                agent("m01", "Reading the log.", True),
                agent("m02", "Re P7: fixed.", True, re=["P7"]),
                agent("m03", "Merging now.", True),
                agent("m04", "Merged.")]
        out, step_of, open_mid = turn_steps.fold(recs, "s1")
        self.assertEqual(texts(out), ["go", "Re P7: fixed.", "Merged."])
        self.assertEqual([s["mid"] for s in out[1]["turn"]["steps"]], ["m01"])
        self.assertEqual([s["mid"] for s in out[2]["turn"]["steps"]], ["m03"])
        self.assertEqual(open_mid, "")

    def test_a_thread_on_a_line_keeps_it(self):
        recs = [user("m00", "go"), agent("m01", "Reading.", True), agent("m02", "Done.")]
        out, step_of, _ = turn_steps.fold(recs, "s1", chosen={"m01"})
        self.assertEqual(texts(out), ["go", "Reading.", "Done."])
        self.assertEqual(step_of, {})
        side = {"names": {"m:m01": "x"}, "anchors": {"P3": "m:m09"}, "sent": [{"thread": "m:m08"}],
                "seen": {"t:P1": 1}}
        self.assertEqual(turn_steps.chosen_mids(side), {"m01", "m09", "m08"})

    def test_a_run_cut_by_a_line_is_a_message(self):
        recs = [user("m00", "go"),
                agent("m01", "Reading.", True),
                agent("m02", "Merging.", True),
                user("m03", "[restart] the hub restarted", kind="hub"),
                agent("m04", "Back.")]
        out, step_of, open_mid = turn_steps.fold(recs, "s1")
        self.assertEqual(texts(out), ["go", "Merging.", "[restart] the hub restarted", "Back."])
        self.assertEqual([s["mid"] for s in out[1]["turn"]["steps"]], ["m01"])
        self.assertEqual(step_of, {"m01": "m02"})
        self.assertEqual(open_mid, "")

    def test_the_open_tail_of_the_current_session(self):
        recs = [user("m00", "go"),
                agent("m01", "Reading.", True),
                agent("m02", "Merging #397 now.", True)]
        out, step_of, open_mid = turn_steps.fold(recs, "s1")
        self.assertEqual(texts(out), ["go", "Merging #397 now."])
        self.assertEqual(open_mid, "m02")
        self.assertEqual(step_of, {"m01": "m02"})
        # A single line is open too.
        out, _s, open_mid = turn_steps.fold(recs[:2], "s1")
        self.assertEqual(open_mid, "m01")
        # Not in an older session: that turn stopped.
        _o, _s, open_mid = turn_steps.fold(recs, "s2")
        self.assertEqual(open_mid, "")
        # An ended turn is not open.
        _o, _s, open_mid = turn_steps.fold(recs + [agent("m03", "Done.")], "s1")
        self.assertEqual(open_mid, "")

    def test_two_sessions(self):
        recs = [user("a00", "go", sid="s1"),
                agent("a01", "Reading.", True, sid="s1"),
                agent("a02", "Merging.", True, sid="s1"),
                user("b00", "[rotation] carry on", sid="s2", kind="hub"),
                agent("b01", "Reading the handover.", True, sid="s2"),
                agent("b02", "Carried on.", sid="s2"),
                agent("b03", "Now testing.", True, sid="s2")]
        out, step_of, open_mid = turn_steps.fold(recs, "s2")
        self.assertEqual(texts(out), ["go", "Merging.", "[rotation] carry on", "Carried on.", "Now testing."])
        self.assertEqual(step_of, {"a01": "a02", "b01": "b02"})
        self.assertEqual(open_mid, "b03")
        # A session change ends a run even with no line between.
        recs2 = [agent("a01", "Reading.", True, sid="s1"), agent("b01", "Done.", sid="s2")]
        out, step_of, open_mid = turn_steps.fold(recs2, "s1")
        self.assertEqual(texts(out), ["Reading.", "Done."])
        self.assertEqual((step_of, open_mid), ({}, "a01"))


if __name__ == "__main__":
    unittest.main()
