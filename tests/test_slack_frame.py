"""The Slack source run by the real frame (outside.run_check), with a stand-in
reader: the one-time look back over threads goes further back than the frame
lets a read start (maxGapDays), so after a full page it must carry on from
where it stopped, not from the frame's limit, and no gap is reported."""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import outside
import sources
from sources import slack_threads as threadlist
from sources.slack import SlackSource
from tests import slack_fixtures as fx

# A Wednesday at 10:00 in the hub's time zone (fast hours).
NOW = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
DAY = 86400
SID = "slack-made-up"


class LookBackThroughTheFrameTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-outside-slack-")
        self.addCleanup(tmp.cleanup)
        old = outside._d
        outside.bind(types.SimpleNamespace(DASHBOARD_DIR=Path(tmp.name)))
        self.addCleanup(outside.bind, old)
        for p in (mock.patch.object(outside, "_log"),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None)):
            p.start()
            self.addCleanup(p.stop)
        outside.reset()
        self.addCleanup(outside.reset)

        # 20 mentions (a full page) from 13 days ago, then nothing more.
        self.page = [fx.msg(fx.CHANNEL3, f"{NOW - 13 * DAY + i * 60:.6f}", text=f"<@{fx.ME}> thoughts?")
                     for i in range(20)]
        self.seed_calls = []

        def answer(q):
            if q.kind == "profile":
                return fx.profile_answer()
            if q.label == "workspace":
                return fx.search_answer([fx.msg(fx.CHANNEL2, "1794390000.000100", thread_ts="1794380000.000100")])
            if q.kind == "channel":
                return fx.channel_answer([])
            if q.label == threadlist.SEED:
                self.seed_calls.append(q.args["after"])
                return fx.search_answer(self.page if len(self.seed_calls) == 1 else [])
            return fx.search_answer([])

        reader = fx.FakeReader(answer)
        src = SlackSource(reader_factory=lambda settings, memory: reader)

        class Shared(SlackSource):
            def __new__(cls):
                return src

        p = mock.patch.dict(sources.TYPES, {"slack": Shared})
        p.start()
        self.addCleanup(p.stop)
        outside.set_source(SID, type="slack", mode="decide", settings={"channels": []})

    def test_a_full_look_back_page_carries_on_where_it_stopped(self):
        outside.note_tick(NOW)
        first = outside.run_check(SID, NOW)
        self.assertEqual(first["outcome"], "ok", first)
        self.assertEqual(self.seed_calls, [str(int(NOW - 14 * DAY))])

        later = NOW + 60
        outside.note_tick(later)
        self.assertTrue(outside.is_due(SID, later))  # a full page: read again soon
        second = outside.run_check(SID, later)
        self.assertEqual(second["outcome"], "ok", second)
        # Not cut to the frame's 3-day limit: on from the newest message of the full page.
        self.assertEqual(self.seed_calls[1], str(int(float(self.page[-1]["ts"]))))
        st = outside._load_state(SID)
        self.assertIsNone(st["gap"])
        self.assertNotIn(threadlist.SEED, st["positions"])
        self.assertTrue(st["memory"]["threads_seeded"])
        self.assertNotIn("seedFrom", st["memory"])
        self.assertEqual(threadlist.follow_channels(st["memory"]["threads"]), [fx.CHANNEL3])
