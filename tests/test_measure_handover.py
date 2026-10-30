"""tools/measure_handover.py: a model call counted once, how far sessions run
past the limit, and the replay at a lower one (#290)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import measure_handover as mh  # noqa: E402


def _call(mid, ctx, minute=0):
    return json.dumps({"type": "assistant", "timestamp": f"2026-10-30T08:{minute:02d}:00Z",
                       "message": {"id": mid, "usage": {
                           "input_tokens": 1, "cache_read_input_tokens": ctx - 1}}})


def _user(text, minute=0):
    return json.dumps({"type": "user", "timestamp": f"2026-10-30T08:{minute:02d}:00Z",
                       "message": {"role": "user", "content": text}})


class Reading(unittest.TestCase):
    def test_a_call_with_two_blocks_counts_once(self):
        s = mh.read_claude([_call("m1", 100), _call("m1", 100), _call("m2", 200)])
        self.assertEqual([c for _, c in s["steps"]], [100, 200])
        self.assertEqual(s["lineTokens"], 400)

    def test_a_sub_agent_is_not_the_session(self):
        side = json.loads(_call("m2", 900))
        side["isSidechain"] = True
        s = mh.read_claude([_call("m1", 100), json.dumps(side)])
        self.assertEqual([c for _, c in s["steps"]], [100])

    def test_an_ask_inside_a_paste_is_an_ask(self):
        ask = '\n<pasted_content id="a">\n[handover] Your conversation has reached 401k\n'
        s = mh.read_claude([_call("m1", 100), _user(ask)])
        self.assertEqual(s["asks"], [1])

    def test_codex_counts_each_call(self):
        ev = lambda ctx, total: json.dumps({"payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": ctx},
            "total_token_usage": {"total_tokens": total}}}})
        self.assertEqual(mh.read_codex([ev(10, 1), ev(10, 1), ev(20, 2)]), [10, 20])


class Summary(unittest.TestCase):
    def test_past_the_limit(self):
        s = mh.read_claude([_call("a", 150), _call("b", 250, 5), _user(
            "[handover] Your conversation", 6), _call("c", 300, 7)])
        got = mh.summary([s], 200)
        self.assertEqual((got["past"], got["above"], got["excess"]), (1, 550, 150))
        self.assertEqual(got["askAt"][0], 300)
        self.assertEqual(got["crossedInCooldown"], 1)


class Replay(unittest.TestCase):
    def test_a_lower_limit_means_more_fresh_starts_and_fewer_tokens(self):
        work = [2_000] * 400
        high, low = mh.replay(work, 200_000), mh.replay(work, 120_000)
        self.assertGreater(low["fresh"], high["fresh"])
        self.assertLess(low["tokens"], high["tokens"])

    def test_never_past_the_limit_costs_nothing_extra(self):
        got = mh.replay([1_000] * 10, 200_000)
        self.assertEqual((got["fresh"], got["above"]), (0, 0))

    def test_work_skips_what_the_replay_charges_itself(self):
        one = {"steps": [(0, c) for c in (50, 60, 70)], "asks": [2]}
        two = {"steps": [(0, c) for c in range(50, 120, 10)], "asks": []}
        self.assertEqual(mh.work_of([one, two]), [10] + [10, 10])

    def test_weighted_cost(self):
        self.assertAlmostEqual(mh.weighted_cost([100, 150]), 125 + 10 + 62.5)


if __name__ == "__main__":
    unittest.main()
