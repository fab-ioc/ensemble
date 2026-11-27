"""#430 (GitHub issue 73): a question card and the reply its turn writes just
after it are one balloon on the page (session.html joinAskWords), so in a
threaded PO chat they are always in one place (threads.assign)."""
from __future__ import annotations

import unittest

import threads
from tests import test_threads as tt
from tests.test_threads import human, new_row, point, po, report, side

card = tt.ThreadStaysTest.card


def run_(recs, pts, sd=None, cards=None):
    return threads.assign(recs, {"points": pts, "asks": {}}, sd or side(), None, cards=cards or [])


def thread_and_main():
    """P1 sent as a new thread and answered there; then P2 in the main chat."""
    recs = [human("s:0", 0, "make the chart wider"), po("s:1", 5, "On it."),
            human("s:2", 50, "and the export?")]
    pts = [point("P1", "s:0", "make the chart wider"), point("P2", "s:2", "and the export?", 50)]
    return recs, pts, side(sent=[new_row("s:0")])


class CardWordsTogetherTest(unittest.TestCase):

    def test_a_card_its_points_placed_takes_the_reply_after_it_there(self):
        recs, pts, sd = thread_and_main()
        recs = recs + [po("s:3", 60, "The wide chart cuts the legend on a phone. I hold it until you pick.")]
        out = run_(recs, pts, sd, [card("c1", 56, pts=["P1"])])
        self.assertEqual(out["cards"], {"c1": "P1"})
        self.assertIn("s:3", out["threads"]["P1"]["members"])
        self.assertNotIn("s:3", tt.ThreadStaysTest.channel(out, recs))

    def test_a_card_goes_where_the_reply_after_it_is(self):
        recs, pts, sd = thread_and_main()
        # Asked after the main chat's input; the reply speaks of P1: its thread.
        recs = recs + [po("s:3", 60, "For P1 the wide chart cuts the legend on a phone.")]
        out = run_(recs, pts, sd, [card("c1", 56)])
        self.assertIn("s:3", out["threads"]["P1"]["members"])
        self.assertEqual(out["cards"], {"c1": "P1"})

    def test_a_reply_long_after_the_card_leaves_it_where_it_was(self):
        # The same case with the reply too late to be the card's words: as before #430.
        recs, pts, sd = thread_and_main()
        recs = recs + [po("s:3", 56 + threads.ASK_JOIN_S + 5, "For P1 the wide chart cuts the legend.")]
        out = run_(recs, pts, sd, [card("c1", 56)])
        self.assertIn("s:3", out["threads"]["P1"]["members"])
        self.assertEqual(out["cards"], {}, "the card stays in the main chat")

    def test_an_answer_to_a_point_after_a_card_is_not_its_words(self):
        recs, pts, sd = thread_and_main()
        recs = recs + [po("s:3", 60, "Re P2: the export ships Friday.", re_=["P2"])]
        out = run_(recs, pts, sd, [card("c1", 56, pts=["P1"])])
        self.assertEqual(out["cards"], {"c1": "P1"})
        self.assertNotIn("s:3", out["threads"]["P1"]["members"])

    def test_an_input_between_the_card_and_the_reply_keeps_them_apart(self):
        recs, pts, sd = thread_and_main()
        recs = recs + [human("s:3", 58, "also, the logo?"), po("s:4", 60, "Noted, the logo next.")]
        out = run_(recs, pts, sd, [card("c1", 56, pts=["P1"])])
        self.assertEqual(out["cards"], {"c1": "P1"})
        self.assertNotIn("s:4", out["threads"]["P1"]["members"])

    def test_a_reply_after_a_card_is_never_team_activity(self):
        recs = [human("s:0", 0, "hello"), po("s:1", 5, "Hi."),
                report("s:2", 50, "room-aaaa1111", "Some task"),
                po("s:3", 60, "The task needs your pick on the colour.")]
        out = run_(recs, [point("P1", "s:0", "hello")], side(), [card("c1", 56)])
        self.assertNotIn("s:3", (out["threads"].get("team:activity") or {}).get("members") or [])
        self.assertEqual(out["cards"], {})
        self.assertIn("s:3", tt.ThreadStaysTest.channel(out, recs))


if __name__ == "__main__":
    unittest.main()
