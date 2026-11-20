"""The frame for outside sources (outside.py), checked with the made-up source
in sources/fake.py: a new source starts from now, nothing is handed on twice
across a restart, a 2-hour gap is caught up page by page, a full page goes on
at the next tick, a gap past 3 days is cut and said so, failures right after
a wake or a start are not counted, failures are counted and the health
states and down rows follow them, decide-only messages are never due for
delivery, the queue ignores a message it already has, and a hub with no
source configured writes nothing."""
from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import outside
import sources
from sources.base import FAILED, NOT_SET_UP, OK, Check, Message, Source, Thread
from sources.fake import FakeSource, message

# A Wednesday at 10:00 in the hub's time zone (fast hours); a Saturday too.
WED = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
SAT = time.mktime((2026, 11, 21, 10, 0, 0, 0, 0, -1))
SID = "made-up"


class FrameTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-outside-")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        old = outside._d
        outside.bind(types.SimpleNamespace(DASHBOARD_DIR=self.state))
        self.addCleanup(outside.bind, old)
        for p in (mock.patch.object(outside, "_log"),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None)):
            p.start()
            self.addCleanup(p.stop)
        outside.reset()
        self.addCleanup(outside.reset)
        self.fake = FakeSource()
        self.use(self.fake)

    def use(self, fake: FakeSource) -> None:
        """Register the made-up type so the frame makes ``fake`` for it."""

        class Shared(FakeSource):
            def __new__(cls):
                return fake

        p = mock.patch.dict(sources.TYPES, {"fake": Shared})
        p.start()
        self.addCleanup(p.stop)

    def restart(self, now: float) -> FakeSource:
        """What a hub restart leaves: the files, and a new source object
        reading the same service."""
        outside.reset()
        fresh = FakeSource()
        fresh.messages = list(self.fake.messages)
        fresh.page = self.fake.page
        self.fake = fresh
        self.use(fresh)
        outside.note_tick(now)
        return fresh

    def add(self, mode="on", **extra) -> None:
        outside.set_source(SID, type="fake", mode=mode, settings={"account": "made-up"}, **extra)

    def write(self, n: int, at: float, **kw) -> Message:
        m = message(n, at, **kw)
        self.fake.messages.append(m)
        return m

    def keys(self) -> list[str]:
        return [e["key"] for e in outside.pending(SID)]

    def key(self, n: int) -> str:
        return message(n, 0).key


class NeverTwice(FrameTest):
    def test_a_new_source_starts_from_now(self):
        self.add()
        self.write(1, WED - 600)
        self.write(2, WED - 10)
        r = outside.run_check(SID, WED)
        self.assertEqual((r["outcome"], r["added"]), (OK, 0))
        self.assertEqual(self.fake.calls[0]["positions"], {}, "a new read starts from now")
        # The next read starts 5 minutes back (the overlap) and finds both,
        # but they were written before the source's first check.
        self.write(3, WED + 30)
        r = outside.run_check(SID, WED + 60)
        self.assertEqual(self.fake.calls[1]["positions"], {"inbox": WED - outside.OVERLAP_S})
        self.assertEqual(self.keys(), [self.key(3)])

    def test_never_twice_across_a_restart(self):
        self.add()
        outside.run_check(SID, WED)
        self.write(1, WED + 20)
        self.write(2, WED + 40)
        outside.run_check(SID, WED + 60)
        self.assertEqual(self.keys(), [self.key(1), self.key(2)])
        # Routing takes the first; the second still waits.
        outside.hand_on(lambda sid, entries: [entries[0]["key"]], [SID])
        self.assertEqual(self.keys(), [self.key(2)])
        self.restart(WED + 120)
        self.write(3, WED + 100)
        r = outside.run_check(SID, WED + 120)
        # The overlap reads 1 and 2 again: neither comes back.
        self.assertIn(self.key(1), {m.key for m in self.fake_messages_read()})
        self.assertEqual(r["added"], 1)
        self.assertEqual(self.keys(), [self.key(2), self.key(3)])
        # And again after another restart, with nothing new.
        self.restart(WED + 180)
        self.assertEqual(outside.run_check(SID, WED + 180)["added"], 0)
        self.assertEqual(self.keys(), [self.key(2), self.key(3)])

    def fake_messages_read(self):
        since = self.fake.calls[-1]["positions"]["inbox"]
        return [m for m in self.fake.messages if m.at >= since]

    def test_a_message_older_than_the_horizon_is_never_handed_on(self):
        self.add()
        outside.run_check(SID, WED)
        # A source that returns an old message (a thread read, say): the
        # frame drops it, since its key may have been forgotten.
        old = message(9, WED - 3600)
        self.fake.messages.append(old)

        real_check = self.fake.check

        def with_old(watch, positions, now):
            res = real_check(watch, positions, now)
            res.messages.append(old)
            return res

        self.fake.check = with_old
        outside.run_check(SID, WED + 60)
        self.assertEqual(self.keys(), [])

    def test_seen_keys_are_kept_down_to_the_horizon_only(self):
        self.add()
        outside.run_check(SID, WED)
        for i in range(5):
            self.write(i, WED + 10 + i)
        outside.run_check(SID, WED + 60)
        later = WED + 6 * 3600
        for t in range(int(WED + 120), int(later), 900):
            outside.run_check(SID, t)
        st = outside._load_state(SID)
        self.assertEqual(st["seen"], {}, "keys far below every read are dropped")
        self.assertGreater(st["horizon"], WED + 3600)


class CatchUp(FrameTest):
    def test_catch_up_after_a_two_hour_gap(self):
        self.add()
        outside.note_tick(WED)
        outside.run_check(SID, WED)
        outside.run_check(SID, WED + 60)
        # The hub is off for two hours; 50 messages are written meanwhile.
        for i in range(50):
            self.write(i, WED + 90 + i * 140)
        back = WED + 60 + 2 * 3600
        self.restart(back)
        self.assertTrue(outside.is_due(SID, back))
        got, now, checks = [], back, 0
        while True:
            r = outside.run_check(SID, now)
            checks += 1
            got += [e["key"] for e in outside.pending(SID)]
            outside.hand_on(lambda sid, entries: [e["key"] for e in entries], [SID])
            if not outside._load_state(SID)["more"]:
                break
            # More is waiting: due at the next tick, not in a minute's time by chance.
            now += outside.TICK_S
            self.assertTrue(outside.is_due(SID, now))
        self.assertEqual(checks, 3, "20, 20, then the last 10")
        self.assertEqual(got, [self.key(i) for i in range(50)], "each once, in order")
        # The first read after the gap starts 5 minutes before the last good one.
        self.assertEqual(self.fake.calls[0]["positions"]["inbox"], WED + 60 - outside.OVERLAP_S)
        self.assertEqual(outside.health(SID, now)["gap"], "", "2 hours is under the limit")

    def test_a_full_page_continues_on_the_next_check(self):
        self.add(slowEvery=15)
        outside.run_check(SID, SAT)
        self.fake.page = 2
        for i in range(5):
            self.write(i, SAT + 10 + i)
        outside.run_check(SID, SAT + 60)
        self.assertEqual(self.keys(), [self.key(0), self.key(1)])
        st = outside._load_state(SID)
        self.assertTrue(st["more"])
        # Saturday is slow hours (every 15 minutes), but a full page goes on
        # at the next tick, from exactly where it stopped (no overlap).
        self.assertTrue(outside.is_due(SID, SAT + 120))
        outside.run_check(SID, SAT + 120)
        self.assertEqual(self.fake.calls[-1]["positions"]["inbox"], SAT + 11)
        now = SAT + 120
        while outside._load_state(SID)["more"]:
            now += outside.TICK_S
            self.assertTrue(outside.is_due(SID, now))
            outside.run_check(SID, now)
        self.assertEqual(self.keys(), [self.key(i) for i in range(5)], "each once, in order")
        self.assertFalse(outside.is_due(SID, now + 60), "caught up: back to every 15 minutes")

    def test_a_gap_longer_than_three_days_is_read_from_three_days_back(self):
        self.add()
        outside.run_check(SID, WED)
        outside.run_check(SID, WED + 60)
        self.fake.page = 1000
        back = WED + 60 + 5 * 86400
        for i in range(10):              # one every half day
            self.write(i, WED + 120 + i * 43200)
        self.restart(back)
        outside.run_check(SID, back)
        limit = back - 3 * 86400
        self.assertEqual(self.fake.calls[0]["positions"]["inbox"], limit)
        expected = [self.key(i) for i in range(10) if WED + 120 + i * 43200 >= limit]
        self.assertEqual(self.keys(), expected)
        self.assertTrue(expected and len(expected) < 10)
        gap = outside.health(SID, back)["gap"]
        self.assertIn("nothing was read from", gap)
        self.assertIn("were not read", gap)
        self.assertIn(outside._when(limit, back), gap)

    def test_a_read_no_longer_made_does_not_say_the_hub_was_off(self):
        self.fake.reads = ("inbox", "extra")
        self.add()
        outside.run_check(SID, WED)
        outside.run_check(SID, WED + 60)
        # "extra" stops being made (a channel no longer watched); the source
        # is read every 6 hours for 5 days.
        self.fake.reads = ("inbox",)
        t = WED + 60
        for _ in range(20):
            t += 6 * 3600
            self.assertEqual(outside.run_check(SID, t)["outcome"], OK)
            self.assertEqual(outside.health(SID, t)["gap"], "", "the source was read all along")
        self.assertNotIn("extra", outside._load_state(SID)["positions"], "forgotten after 3 days")
        # It comes back: it starts from now, like a new read.
        self.fake.reads = ("inbox", "extra")
        outside.run_check(SID, t + 60)
        self.assertNotIn("extra", self.fake.calls[-1]["positions"])
        self.assertEqual(outside.health(SID, t + 60)["gap"], "")

    def test_a_read_not_made_while_the_source_was_read_starts_from_now(self):
        # A read last made just under 3 days ago, the source read an hour
        # ago: at the next check the read is past the limit but still saved.
        self.fake.reads = ("inbox", "extra")
        self.add()
        outside.run_check(SID, WED)
        self.fake.reads = ("inbox",)
        outside.run_check(SID, WED + 3 * 86400 - 3600)
        self.assertIn("extra", outside._load_state(SID)["positions"])
        self.fake.reads = ("inbox", "extra")
        now = WED + 3 * 86400 + 60
        outside.run_check(SID, now)
        self.assertNotIn("extra", self.fake.calls[-1]["positions"])
        self.assertEqual(outside.health(SID, now)["gap"], "")

    def test_a_read_missed_once_before_a_stop_is_cut_to_the_limit(self):
        self.fake.reads = ("inbox", "extra")
        self.add()
        outside.run_check(SID, WED)
        self.fake.reads = ("inbox",)             # one check without "extra"
        outside.run_check(SID, WED + 900)
        # The hub is off for 3 days less 10 minutes.
        back = WED + 900 + 3 * 86400 - 600
        self.restart(back).reads = ("inbox", "extra")
        outside.run_check(SID, back)
        limit = back - 3 * 86400
        self.assertEqual(self.fake.calls[-1]["positions"]["extra"], limit,
                         "read from the limit, not from now")
        self.assertIn("nothing was read from", outside.health(SID, back)["gap"])

    def test_a_position_that_is_not_a_time_before_now_is_not_kept(self):
        self.add()
        outside.run_check(SID, WED)
        # Milliseconds instead of seconds: taken as now.
        self.fake.positions_out = {"inbox": WED * 1000}
        outside.run_check(SID, WED + 60)
        self.assertEqual(outside._load_state(SID)["positions"]["inbox"]["at"], WED + 60)
        # Not a number at all: the position stays as it was.
        self.fake.positions_out = {"inbox": float("nan")}
        outside.run_check(SID, WED + 120)
        self.assertEqual(outside._load_state(SID)["positions"]["inbox"]["at"], WED + 60)
        # The source goes on reading new messages.
        self.fake.positions_out = {}
        self.write(1, WED + 150)
        outside.run_check(SID, WED + 180)
        self.assertEqual(self.keys(), [self.key(1)])
        self.assertLessEqual(outside._load_state(SID)["horizon"], WED + 150)


class Grace(FrameTest):
    def test_failures_after_a_start_or_a_wake_are_not_counted(self):
        self.add()
        outside.note_tick(WED)                      # the hub starts
        self.fake.fail = "Claude's Slack connection is not available."
        r = outside.run_check(SID, WED + 30)
        self.assertEqual(r["outcome"], FAILED)
        st = outside._load_state(SID)
        self.assertEqual((st["failures"], st["downSince"], st["counted"]), (0, 0.0, False))
        self.assertEqual(outside.health(SID, WED + 30)["state"], "starting")
        # Tried again each minute while in grace.
        self.assertTrue(outside.is_due(SID, WED + 90))
        # The connection comes up: working, nothing was counted.
        self.fake.fail = ""
        outside.run_check(SID, WED + 90)
        self.assertEqual(outside.health(SID, WED + 90)["state"], "working")
        # Ticks each minute; then the Mac sleeps for an hour.
        for t in range(int(WED + 60), int(WED + 600), 60):
            outside.note_tick(t)
        self.assertFalse(outside.in_grace(WED + 600))
        woke = WED + 600 + 3600
        outside.note_tick(woke)
        self.assertTrue(outside.in_grace(woke))
        self.fake.fail = "Claude's Slack connection is not available."
        outside.run_check(SID, woke + 10)
        st = outside._load_state(SID)
        self.assertEqual((st["failures"], st["downSince"]), (0, 0.0))
        self.assertEqual(outside.health(SID, woke + 10)["state"], "working")
        self.assertEqual(outside.down_rows(woke + 10), [])
        # After 3 minutes the same failure counts.
        later = woke + outside.GRACE_S + 5
        self.assertFalse(outside.in_grace(later))
        outside.run_check(SID, later)
        st = outside._load_state(SID)
        self.assertEqual((st["failures"], st["downSince"]), (1, later))

    def test_a_row_already_shown_stays_through_a_wake(self):
        self.add()
        for t in range(int(WED), int(WED + 600), 60):
            outside.note_tick(t)
        outside.run_check(SID, WED + 600)
        self.fake.fail = "Claude's Slack connection is not available."
        down = WED + 660
        for t in range(int(down), int(down + 19 * 60), 60):
            outside.note_tick(t)
            outside.run_check(SID, t)
        self.assertEqual(len(outside.down_rows(down + 19 * 60)), 1)
        # The Mac sleeps for an hour: right after the wake the row stays.
        woke = down + 19 * 60 + 3600
        outside.note_tick(woke)
        self.assertTrue(outside.in_grace(woke))
        self.assertEqual(len(outside.down_rows(woke)), 1)
        self.assertEqual(len(outside.down_rows(woke + 60)), 1)
        # A source that went down just before the wake shows no row in grace.
        outside.set_source("other", type="fake", mode="on", settings={"account": "x"})
        st = outside._load_state("other")
        st.update(downSince=woke - 5 * 60, lastOutcome=FAILED, counted=True, failures=1,
                  reason="x")
        outside._save_state("other", st)
        st["downSince"] = woke - 9 * 60       # due at woke + 60 s, inside grace
        outside._save_state("other", st)
        self.assertTrue(outside.in_grace(woke + 120))
        rows = outside.down_rows(woke + 120)
        self.assertEqual([r["source"] for r in rows], [SID], "a new row waits for grace to end")
        self.assertFalse(outside.in_grace(woke + outside.GRACE_S))
        rows = outside.down_rows(woke + outside.GRACE_S)
        self.assertEqual([r["source"] for r in rows], [SID],
                         "it fell due while the grace lasted: it waits for a counted failure")
        # A failure counted after the grace: still down, the row appears.
        st = outside._load_state("other")
        outside._record_failure(st, "x", woke + outside.GRACE_S + 5)
        outside._save_state("other", st)
        rows = outside.down_rows(woke + outside.GRACE_S + 5)
        self.assertEqual(sorted(r["source"] for r in rows), sorted([SID, "other"]))

    def test_a_short_pause_between_ticks_is_not_a_wake(self):
        # The loop runs late (a slow check elsewhere): 2 minutes between ticks.
        for t in range(int(WED), int(WED + 600), 120):
            outside.note_tick(t)
        self.assertFalse(outside.in_grace(WED + 600))


class Health(FrameTest):
    def test_failure_counting_and_health_states(self):
        h = lambda now: outside.health(SID, now)
        outside.note_tick(WED - 3600)               # the hub has been running an hour
        self.assertIsNone(h(WED), "not configured: nothing")
        self.add(mode="off")
        self.assertEqual(h(WED)["state"], "off")
        self.assertEqual(outside.run_check(SID, WED)["ran"], False)
        outside.set_source(SID, mode="on", paused=True)
        self.assertEqual(h(WED)["state"], "paused")
        self.assertFalse(outside.is_due(SID, WED))
        outside.set_source(SID, paused=False, settings={})
        self.assertEqual(outside.run_check(SID, WED)["outcome"], NOT_SET_UP)
        self.assertEqual(h(WED)["text"], "Not set up: No account is set.")
        self.assertEqual(outside.down_rows(WED + 3600), [], "not set up is not down")
        outside.set_source(SID, settings={"account": "made-up"})
        self.assertEqual(h(WED)["state"], "not set up", "until the next check")
        outside.run_check(SID, WED + 60)
        self.assertEqual(h(WED + 60)["state"], "working")
        # Failures, counted (no grace in this test).
        self.fake.fail = "Slack answered in a form the hub cannot read."
        down = WED + 120
        outside.run_check(SID, down)
        outside.run_check(SID, down + 60)
        self.fake.fail, self.fake.broken = "", "connection reset by peer"
        outside.run_check(SID, down + 120)
        x = h(down + 120)
        self.assertEqual((x["state"], x["failures"], x["since"]), ("not working", 3, down))
        self.assertEqual(x["reason"], "connection reset by peer", "an exception's words are the reason")
        self.assertTrue(x["text"].startswith(f"Not working since {outside._when(down, down)}: "))
        self.assertEqual(outside.down_rows(down + 9 * 60), [], "under 10 minutes")
        rows = outside.down_rows(down + 10 * 60)
        self.assertEqual([r["source"] for r in rows], [SID])
        self.assertIn("is not being read since", rows[0]["text"])
        self.assertEqual(outside._load_state(SID)["today"]["failed"], 3)
        # Recovered: working, the row goes, the count starts again.
        self.fake.broken = ""
        outside.run_check(SID, down + 11 * 60)
        x = h(down + 11 * 60)
        self.assertEqual((x["state"], x["failures"], x["since"]), ("working", 0, 0))
        self.assertEqual(outside.down_rows(down + 11 * 60), [])

    def test_a_type_the_hub_does_not_have_is_not_set_up(self):
        outside.set_source("other", type="nothing-here", mode="on")
        r = outside.run_check("other", WED)
        self.assertEqual(r["outcome"], NOT_SET_UP)
        self.assertIn("no reader for the type nothing-here", outside.health("other", WED)["text"])

    def test_a_check_that_answers_not_set_up(self):
        self.add()
        self.fake.not_set_up = "Claude is not signed in."
        outside.run_check(SID, WED)
        self.assertEqual(outside.health(SID, WED)["text"], "Not set up: Claude is not signed in.")

    def test_the_schedule(self):
        self.add()
        cfg = outside.source_config(SID)
        self.assertTrue(outside.fast_hours(cfg, WED))
        self.assertFalse(outside.fast_hours(cfg, SAT))
        self.assertFalse(outside.fast_hours(cfg, WED + 11 * 3600), "21:00")
        self.assertFalse(outside.fast_hours(cfg, WED - 3.5 * 3600), "06:30")
        self.assertEqual(outside.interval(cfg, WED), 60)
        self.assertEqual(outside.interval(cfg, SAT), 900)
        outside.run_check(SID, SAT)
        self.assertFalse(outside.is_due(SID, SAT + 600))
        self.assertTrue(outside.is_due(SID, SAT + 900))
        self.assertEqual(outside.health(SID, SAT)["nextCheck"], SAT + 900)


class DecideOnly(FrameTest):
    def test_decide_only_reads_but_nothing_is_due_for_delivery(self):
        self.add(mode="decide")
        outside.run_check(SID, WED)
        self.write(1, WED + 10)
        outside.run_check(SID, WED + 60)
        [entry] = outside.pending(SID)
        self.assertTrue(entry["decideOnly"])

        def route(sid, entries):
            outside.queue_put([{"key": e["key"], "to": "proj-made-up", "source": sid,
                                "decideOnly": e["decideOnly"], "message": e["message"]}
                               for e in entries])
            return [e["key"] for e in entries]

        outside.hand_on(route, [SID])
        self.assertEqual([i["key"] for i in outside.queue_items("proj-made-up")], [self.key(1)])
        self.assertEqual(outside.queue_due("proj-made-up"), [], "decide only: never delivered")
        # Switched on: new messages are due; the one decided earlier stays undelivered.
        outside.set_source(SID, mode="on")
        self.write(2, WED + 70)
        outside.run_check(SID, WED + 120)
        outside.hand_on(route, [SID])
        self.assertEqual([i["key"] for i in outside.queue_due("proj-made-up")], [self.key(2)])

    def test_decide_only_raises_no_inbox_rows(self):
        self.add(mode="decide")
        outside.run_check(SID, WED)
        self.fake.fail = "Claude's Slack connection is not available."
        outside.run_check(SID, WED + 60)
        self.assertEqual(outside.health(SID, WED + 60)["state"], "not working")
        self.assertEqual(outside.down_rows(WED + 3600), [])


class Queue(FrameTest):
    def test_the_queue_ignores_what_it_has_or_delivered(self):
        item = {"key": "fake:example:C000TEST01:1", "to": "proj-a", "message": {}}
        self.assertEqual(len(outside.queue_put([item, dict(item)], WED)), 1)
        self.assertEqual(len(outside.queue_put([item], WED)), 0)
        # The router running again on it after a stop names another
        # initiative: ignored, the first one keeps it.
        self.assertEqual(outside.queue_put([dict(item, to="proj-b")], WED), [])
        # A deliberate send to a second initiative is its own item.
        self.assertEqual(len(outside.queue_put([dict(item, to="proj-b")], WED, also=True)), 1)
        self.assertEqual(outside.queue_done([(item["key"], "proj-a")], WED + 1), 1)
        self.assertEqual([i["to"] for i in outside.queue_items()], ["proj-b"])
        self.assertEqual(outside.queue_put([item], WED + 2), [], "delivered lately: not again")
        self.assertEqual(outside.queue_put([dict(item, to="proj-c")], WED + 2), [],
                         "delivered lately, to another initiative: not again")
        # Forgotten after 4 days (the seen keys are gone long before).
        outside.queue_done([(item["key"], "proj-b")], WED + 3)
        outside.queue_done([], WED + outside.KEEP_DONE_S + 10)
        self.assertEqual(len(outside.queue_put([item], WED + outside.KEEP_DONE_S + 20)), 1)

    def test_a_crash_between_the_queue_and_the_unrouted_list(self):
        self.add()
        outside.run_check(SID, WED)
        self.write(1, WED + 10)
        outside.run_check(SID, WED + 60)

        def route(sid, entries):
            outside.queue_put([{"key": e["key"], "to": "proj-a"} for e in entries])
            raise RuntimeError("the hub stopped here")

        outside.hand_on(route, [SID])
        self.assertEqual(self.keys(), [self.key(1)], "still unrouted")
        outside.hand_on(lambda sid, entries: (outside.queue_put(
            [{"key": e["key"], "to": "proj-a"} for e in entries]), [e["key"] for e in entries])[1], [SID])
        self.assertEqual(self.keys(), [])
        self.assertEqual(len(outside.queue_items("proj-a")), 1, "queued once")


class Ticks(FrameTest):
    def test_no_source_configured_writes_nothing(self):
        outside.maybe_tick(background=False, now=WED)
        outside.maybe_tick(background=False, now=WED + 60)
        self.assertFalse((self.state / "outside").exists())

    def test_the_tick_checks_what_is_due_and_hands_on(self):
        self.add()
        taken = []
        with mock.patch.object(outside, "ROUTER",
                               lambda sid, entries: taken.extend(e["key"] for e in entries)
                               or [e["key"] for e in entries]):
            outside.maybe_tick(background=False, now=WED)
            self.write(1, WED + 10)
            outside.maybe_tick(background=False, now=WED + 30)      # not due yet
            self.assertEqual(taken, [])
            outside.maybe_tick(background=False, now=WED + 60)
        self.assertEqual(taken, [self.key(1)])
        self.assertEqual(len(self.fake.calls), 2)

    def test_memory_and_places_reach_the_source(self):
        self.add()
        self.fake.memory_out = {"me": "U000TEST01"}
        with mock.patch.object(outside, "PLACES", lambda sid: [{"channel": "C000TEST02"}]):
            outside.run_check(SID, WED)
            self.fake.memory_out = None              # keep what was kept
            outside.run_check(SID, WED + 60)
        self.assertEqual(self.fake.calls[1]["memory"], {"me": "U000TEST01"})
        self.assertEqual(self.fake.calls[1]["places"], [{"channel": "C000TEST02"}])
        # A failed check keeps the memory as it was.
        self.fake.memory_out, self.fake.fail = {"me": "changed"}, "down"
        outside.run_check(SID, WED + 120)
        self.assertEqual(outside._load_state(SID)["memory"], {"me": "U000TEST01"})

    def test_records_without_a_proper_key_are_left_out(self):
        self.add()
        outside.run_check(SID, WED)
        good = self.write(1, WED + 10)
        bad = message(2, WED + 20)
        bad.key = "other:example:2"                 # not this source's kind

        real = self.fake.check
        self.fake.check = lambda w, p, n: (lambda r: (r.messages.append(bad), r)[1])(real(w, p, n))
        outside.run_check(SID, WED + 60)
        self.assertEqual(self.keys(), [good.key])


class Recovered(FrameTest):
    """The "works again" row: only after a down row was in the Inbox."""

    def go_down(self, start: float, minutes: int) -> float:
        """Ticks each minute from ``start`` with every check failing; returns
        the time of the last tick."""
        self.fake.fail = "Claude's Slack connection is not available."
        t = start
        for t in range(int(start), int(start + minutes * 60), 60):
            outside.note_tick(t)
            outside.run_check(SID, t)
            outside.down_rows(t)                    # the Inbox is read
        return float(t)

    def recover(self, at: float) -> None:
        self.fake.fail = ""
        outside.note_tick(at)
        outside.run_check(SID, at)

    def started(self) -> None:
        self.add()
        for t in range(int(WED), int(WED + 600), 60):
            outside.note_tick(t)
        outside.run_check(SID, WED + 540)

    def test_a_row_after_ten_minutes_down_and_done_clears_it(self):
        self.started()
        last = self.go_down(WED + 600, 12)
        self.assertEqual(len(outside.down_rows(last)), 1)
        self.recover(last + 60)
        self.assertEqual(outside.down_rows(last + 60), [])
        rows = outside.recovered_rows(last + 60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], SID)
        self.assertEqual(rows[0]["from"], WED + 600)
        self.assertIn("is read again (not read from 10:10 to 10:2", rows[0]["text"])
        self.assertTrue(outside.recovered_seen(SID))
        self.assertEqual(outside.recovered_rows(last + 120), [])
        self.assertFalse(outside.recovered_seen(SID))

    def test_the_row_goes_after_a_day(self):
        self.started()
        last = self.go_down(WED + 600, 12)
        self.recover(last + 60)
        self.assertEqual(len(outside.recovered_rows(last + 60 + outside.RECOVERED_S - 1)), 1)
        self.assertEqual(outside.recovered_rows(last + 60 + outside.RECOVERED_S + 1), [])

    def test_none_when_the_down_row_was_never_shown(self):
        self.started()
        last = self.go_down(WED + 600, 5)
        self.assertEqual(outside.down_rows(last), [])
        self.recover(last + 60)
        self.assertEqual(outside.recovered_rows(last + 60), [])

    def test_none_in_decide_only(self):
        self.add(mode="decide")
        for t in range(int(WED), int(WED + 600), 60):
            outside.note_tick(t)
        outside.run_check(SID, WED + 540)
        last = self.go_down(WED + 600, 15)
        self.recover(last + 60)
        self.assertEqual(outside.recovered_rows(last + 60), [])

    def test_pause_and_resume_does_not_count_the_pause_as_down(self):
        # Review 3: down 5 minutes, paused for an hour, resumed: no row
        # before any check, the health line does not say "Not working", the
        # source is checked at the next tick, and its good read gives no
        # "works again" row.
        self.started()
        last = self.go_down(WED + 600, 5)
        self.assertEqual(outside.down_rows(last), [])
        outside.update_source(SID, paused=True)
        later = last + 3600
        for t in range(int(last + 60), int(later) + 1, 60):
            outside.note_tick(t)                    # the hub runs, the source is paused
        outside.update_source(SID, paused=False)
        self.assertEqual(outside.down_rows(later), [])
        h = outside.health(SID, later)
        self.assertEqual(h["state"], "starting")
        self.assertTrue(outside.is_due(SID, later))
        self.recover(later + 30)
        self.assertEqual(outside.recovered_rows(later + 30), [])
        self.assertEqual(outside.health(SID, later + 30)["state"], "working")

    def test_off_and_on_does_not_bring_back_an_old_row(self):
        # Review 3: down 12 minutes (row shown), Off, On a day later: no row
        # at once, and no "works again" row after the first good read.
        self.started()
        last = self.go_down(WED + 600, 12)
        self.assertEqual(len(outside.down_rows(last)), 1)
        outside.update_source(SID, mode="off")
        self.assertEqual(outside.down_rows(last + 60), [])
        later = last + 86400
        for t in range(int(later - 600), int(later) + 1, 60):
            outside.note_tick(t)
        outside.update_source(SID, mode="on")
        self.assertEqual(outside.down_rows(later), [])
        self.recover(later + 30)
        self.assertEqual(outside.recovered_rows(later + 30), [])

    def test_still_down_after_resume_counts_from_the_first_failure_after_it(self):
        self.started()
        last = self.go_down(WED + 600, 5)
        outside.update_source(SID, paused=True)
        later = last + 3600
        for t in range(int(last + 60), int(later) + 1, 60):
            outside.note_tick(t)
        outside.update_source(SID, paused=False)
        end = self.go_down(later + 60, 9)
        self.assertEqual(outside.down_rows(end), [], "9 minutes since the resume")
        end = self.go_down(end + 60, 3)
        rows = outside.down_rows(end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["since"], later + 60)

    def test_decide_only_to_on_keeps_the_count(self):
        # The source was read all along in decide only: its failures count.
        self.add(mode="decide")
        for t in range(int(WED), int(WED + 600), 60):
            outside.note_tick(t)
        outside.run_check(SID, WED + 540)
        last = self.go_down(WED + 600, 12)
        outside.update_source(SID, mode="on")
        self.assertEqual(len(outside.down_rows(last)), 1)

    def test_none_during_grace(self):
        # Failures right after the start are not counted: nothing was shown.
        self.add()
        outside.note_tick(WED)
        self.fake.fail = "Claude's Slack connection is not available."
        outside.run_check(SID, WED + 30)
        self.recover(WED + 90)
        self.assertEqual(outside.recovered_rows(WED + 90), [])
        # Down 9 minutes before a wake: its row would first appear in grace
        # (at the wake plus a minute), so it never did; read again in grace,
        # no "works again" row.
        for t in range(int(WED + 60), int(WED + 900), 60):
            outside.note_tick(t)
        st = outside._load_state(SID)
        woke = WED + 900 + 3600
        st.update(downSince=woke - 9 * 60, lastOutcome=FAILED, counted=True, failures=3, reason="x")
        outside._save_state(SID, st)
        outside.note_tick(woke)
        self.assertEqual(outside.down_rows(woke + 120), [])
        self.recover(woke + 120)
        self.assertEqual(outside.recovered_rows(woke + 120), [])

    def test_read_again_in_grace_after_a_row_was_shown(self):
        self.started()
        last = self.go_down(WED + 600, 15)
        self.assertEqual(len(outside.down_rows(last)), 1)
        woke = last + 3600
        outside.note_tick(woke)
        self.assertTrue(outside.in_grace(woke))
        self.recover(woke + 30)
        self.assertEqual(len(outside.recovered_rows(woke + 30)), 1)

    def test_down_a_few_minutes_before_a_sleep_shows_nothing_on_waking(self):
        # Review 1: 5 minutes down, then an hour asleep. On waking, no row
        # (the hour asleep does not count), and the good read that follows
        # gives no "works again" row.
        self.started()
        last = self.go_down(WED + 600, 5)
        self.assertEqual(outside.down_rows(last), [])
        woke = last + 3600
        outside.note_tick(woke)
        self.assertTrue(outside.in_grace(woke))
        self.assertEqual(outside.down_rows(woke), [])
        self.assertEqual(outside.down_rows(woke + 60), [])
        self.recover(woke + 90)
        self.assertEqual(outside.recovered_rows(woke + 90), [])
        # Down again after that read: counted from its new start.
        last = self.go_down(woke + 120, 3)
        self.assertEqual(outside.down_rows(last), [], "down again 2 minutes")

    def test_still_down_after_a_sleep_gives_the_row_after_the_grace(self):
        self.started()
        last = self.go_down(WED + 600, 5)
        woke = last + 3600
        outside.note_tick(woke)
        self.assertEqual(outside.down_rows(woke + 60), [], "in grace")
        after = woke + outside.GRACE_S + 5
        for t in range(int(woke + 60), int(after) + 1, 60):
            outside.note_tick(t)                    # a tick each minute
        outside.run_check(SID, after)
        self.assertEqual(len(outside.down_rows(after)), 1, "down since before the sleep, and still")

    def test_down_a_few_minutes_before_a_restart_shows_nothing(self):
        self.started()
        last = self.go_down(WED + 600, 5)
        outside.reset()                             # the hub restarts
        later = last + 3600
        self.assertEqual(outside.down_rows(later), [], "before its first tick")
        outside.note_tick(later)
        self.assertEqual(outside.down_rows(later + 60), [], "first tick: in grace")
        self.recover(later + 90)
        self.assertEqual(outside.recovered_rows(later + 90), [])

    def test_a_shown_row_stays_through_a_restart(self):
        self.started()
        last = self.go_down(WED + 600, 11)
        self.assertEqual(len(outside.down_rows(last)), 1)
        outside.reset()
        self.assertEqual(len(outside.down_rows(last + 60)), 1, "before the first tick")
        outside.note_tick(last + 60)
        self.assertEqual(len(outside.down_rows(last + 120)), 1, "in grace")
        self.recover(last + 150)
        self.assertEqual(len(outside.recovered_rows(last + 150)), 1)

    def test_read_again_when_the_row_fell_due_but_was_not_shown(self):
        # Review 1, small 7: read again at the very second the row fell due,
        # before the Inbox was read: no row was shown, so no "works again".
        self.started()
        self.fake.fail = "Claude's Slack connection is not available."
        down = WED + 600
        outside.note_tick(down)
        outside.run_check(SID, down)
        self.recover(down + 600)
        self.assertEqual(outside.recovered_rows(down + 600), [])

    def test_a_new_down_row_hides_the_works_again_row(self):
        self.started()
        last = self.go_down(WED + 600, 12)
        self.recover(last + 60)
        self.assertEqual(len(outside.recovered_rows(last + 60)), 1)
        again = self.go_down(last + 120, 12)
        self.assertEqual(len(outside.down_rows(again)), 1)
        self.assertEqual(outside.recovered_rows(again), [])


class RememberingFake(FakeSource):
    def __init__(self):
        super().__init__()
        self.remembered = []

    def remember(self, settings, memory):
        self.remembered.append((dict(settings), dict(memory)))
        self.memory = dict(memory)

    def thread(self, ref):
        self.calls.append({"thread": ref, "memory": dict(getattr(self, "memory", {}))})
        return super().thread(ref)


class MemoryAfterRestart(FrameTest):
    def test_a_thread_read_after_a_restart_has_the_kept_memory(self):
        self.add()
        self.fake.memory_out = {"identity": {"workspace": "example"}}
        outside.run_check(SID, WED)
        # The hub restarts: a new object, no check yet.
        outside.reset()
        fresh = RememberingFake()
        fresh.messages = [message(1, WED + 10, thread="t1")]
        self.fake = fresh
        self.use(fresh)
        got = outside.thread(SID, "C000TEST01:t1")
        self.assertEqual(len(got), 1)
        self.assertEqual(fresh.calls[-1], {"thread": "C000TEST01:t1",
                                           "memory": {"identity": {"workspace": "example"}}})
        self.assertEqual(fresh.remembered[0][0], {"account": "made-up"})

    def test_a_thread_of_a_source_not_configured_is_refused(self):
        with self.assertRaises(ValueError):
            outside.thread("nothing", "C000TEST01:t1")


class Panel(FrameTest):
    def test_add_input_source(self):
        cfg = outside.add_source("fake")
        self.assertEqual((cfg["type"], cfg["mode"]), ("fake", "off"))
        self.assertEqual(cfg["settings"], {"account": "", "dms": True})
        self.assertIn("fake", outside.load_config())
        outside.add_source("fake")
        self.assertEqual(sorted(outside.load_config()), ["fake", "fake-2"])
        self.assertEqual(outside.add_source("slack")["type"], "slack")
        self.assertIn("slack", outside.load_config())
        with self.assertRaisesRegex(outside.Refused, "Discord comes later"):
            outside.add_source("discord")
        with self.assertRaisesRegex(outside.Refused, "no input source type"):
            outside.add_source("nothing")

    def test_changes_are_checked_by_the_kind_of_field(self):
        self.add(mode="off")
        bad = [({"settings": {"dms": "yes"}}, "on or off"),
               ({"settings": {"me": "U1"}}, "found by the checks"),
               ({"settings": {"nope": 1}}, "no setting"),
               ({"frame": {"fastEvery": 0}}, "at least 1"),
               ({"frame": {"fastEvery": "x"}}, "must be a number"),
               ({"frame": {"fastFrom": "7"}}, "a time like"),
               ({"frame": {"fastTo": "24:30"}}, "a time like"),
               ({"frame": {"fastDays": [7]}}, "0 \\(Monday\\)"),
               ({"frame": {"mode": "on"}}, "on its own"),
               ({"mode": "loud"}, "not a reading mode"),
               ({"paused": "yes"}, "yes or no"),
               ({"settings": {"account": "ok", "dms": 3}}, "on or off")]
        for change, words in bad:
            with self.subTest(change=change):
                with self.assertRaisesRegex(outside.Refused, words):
                    outside.update_source(SID, **change)
        self.assertEqual(outside.source_config(SID)["settings"], {"account": "made-up"},
                         "nothing is saved when one value is refused")
        cfg = outside.update_source(SID, mode="decide", settings={"dms": False},
                                    frame={"fastEvery": "2", "fastDays": "0, 2,4", "fastFrom": "08:30"})
        self.assertEqual(cfg["mode"], "decide")
        self.assertEqual(cfg["settings"], {"account": "made-up", "dms": False})
        self.assertEqual((cfg["fastEvery"], cfg["fastDays"], cfg["fastFrom"]), (2, [0, 2, 4], "08:30"))
        self.assertTrue(outside.update_source(SID, paused=True)["paused"])
        with self.assertRaises(outside.Refused):
            outside.update_source("nothing", mode="on")

    def test_check_now(self):
        self.add(mode="off")
        with self.assertRaisesRegex(outside.Refused, "is off"):
            outside.check_now(SID)
        outside.update_source(SID, mode="on", paused=True)
        with self.assertRaisesRegex(outside.Refused, "paused"):
            outside.check_now(SID)
        outside.update_source(SID, paused=False)
        lock = outside._checking(SID)
        lock.acquire()
        with self.assertRaisesRegex(outside.Refused, "being checked now"):
            outside.check_now(SID)
        lock.release()
        outside.check_now(SID)
        for _ in range(100):
            if outside._load_state(SID)["lastCheck"] and not lock.locked():
                break
            time.sleep(0.02)
        self.assertEqual(outside._load_state(SID)["lastOutcome"], OK)
        self.assertFalse(lock.locked())

    def test_what_the_panel_gets(self):
        self.add(mode="on")
        orig = self.fake.check

        def with_notes(watch, positions, now):
            res = orig(watch, positions, now)
            res.notes = ["3 channels left out"]
            res.memory = {"me": "U000TEST01"}
            return res
        self.fake.check = with_notes
        self.fake.found = lambda memory: {"me": memory.get("me", "")}
        outside.run_check(SID, WED)
        p = outside.panel(WED + 10)
        self.assertEqual(len(p["sources"]), 1)
        s = p["sources"][0]
        self.assertEqual((s["id"], s["label"], s["state"], s["mode"]), (SID, "Made-up source", "working", "on"))
        self.assertEqual([f["key"] for f in s["fields"]], ["account", "dms", "me"])
        self.assertEqual(s["values"], {"account": "made-up", "dms": True})
        self.assertEqual(s["found"], {"me": "U000TEST01"})
        self.assertEqual(s["notes"], ["3 channels left out"])
        self.assertEqual((s["problem"], s["checking"], s["routed"], s["down"]), ("", False, True, False))
        self.assertEqual(s["frame"]["fastEvery"], 1)
        self.assertEqual(set(p), {"sources"})          # no types: the panel adds no source (#15)
        outside.update_source(SID, settings={"account": ""})
        self.assertEqual(outside.panel(WED + 20)["sources"][0]["problem"], "No account is set.")


class SourcesApi(FrameTest):
    """GET and POST /api/outside/sources: the page only; a refused change
    says why; the answer is the panel as it is now."""

    def setUp(self):
        # Imported before the frame is pointed at the test folder: importing
        # dashboard binds the frame to the hub's own data folder.
        import dashboard
        super().setUp()
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.sent = []
        self.handler._send_json = lambda code, payload: self.sent.append((code, payload))
        self.handler._page_refusal = lambda: ""
        self.handler._page_read_ok = lambda: True
        self.handler._agent_peer = lambda: ""

    def post(self, **data):
        self.handler._outside_post("/api/outside/sources", data)
        return self.sent[-1]

    def test_seen_only(self):
        outside.set_source("fake", type="fake", mode="on")
        with mock.patch.object(outside, "recovered_seen") as seen:
            self.assertEqual(self.post(action="seen", source="fake")[0], 200)
        seen.assert_called_once_with("fake")
        # Sources are set in the setup file now (#15): the page's add, set and check are gone.
        for data in ({"action": "add", "type": "fake"}, {"action": "set", "source": "fake", "mode": "off"},
                     {"action": "check", "source": "fake"}, {"action": "nothing"}):
            with self.subTest(data=data):
                self.assertEqual(self.post(**data)[0], 404)
        self.assertEqual(outside.source_config("fake")["mode"], "on")

    def test_only_the_page(self):
        self.handler._page_refusal = lambda: "Only the dashboard page can do this."
        self.assertEqual(self.post(action="add", type="fake")[0], 403)
        self.assertEqual(outside.load_config(), {})
        self.handler._agent_peer = lambda: "it was started by an agent"
        self.handler._outside_get("/api/outside/sources", types.SimpleNamespace(query=""))
        self.assertEqual(self.sent[-1][0], 403)
        self.handler._agent_peer = lambda: ""
        self.handler._outside_get("/api/outside/sources", types.SimpleNamespace(query=""))
        self.assertEqual(self.sent[-1][0], 200)
        self.assertIn("sources", self.sent[-1][1])


class Shapes(unittest.TestCase):
    def test_a_message_keeps_every_field_through_the_files(self):
        m = message(1, WED, thread="1794520222.979069")
        m.thread = Thread(id="C000TEST01:1794520222.979069", first=False, head="the first message")
        m.addressed = True
        back = Message.from_dict(m.to_dict())
        self.assertEqual(back, m)
        self.assertEqual(back.reply_to, {"channel": "C000TEST01", "thread": "1794520222.979069"})

    def test_what_a_type_tells_the_panel(self):
        d = FakeSource().describe()
        self.assertEqual([f["key"] for f in d["fields"]], ["account", "dms", "me"])
        # The topic is every row's, whatever the source (round 2 step 1).
        self.assertEqual([f["key"] for f in d["routeFields"]], ["channels"])
        self.assertFalse(d["canSend"])
        with mock.patch.dict(sources.TYPES, {"fake": FakeSource}):
            cat = {t["kind"]: t for t in outside.describe_types()}
        self.assertTrue(cat["fake"]["ready"])
        self.assertEqual((cat["discord"]["ready"], cat["jira"]["ready"]), (False, False))
        self.assertIn("mode", [f["key"] for f in cat["fake"]["frameFields"]])

    def test_no_source_sends_in_this_round(self):
        from sources.base import NotSupported
        with self.assertRaises(NotSupported):
            FakeSource().send({"channel": "C000TEST01"}, "hello")
        self.assertFalse(any(getattr(t, "can_send", False) for t in sources.TYPES.values()))

    def test_the_base_check_must_be_written_by_each_source(self):
        from sources.base import Watch
        with self.assertRaises(NotImplementedError):
            Source().check(Watch(), {}, WED)


if __name__ == "__main__":
    unittest.main()
