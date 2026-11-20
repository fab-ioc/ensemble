"""Replies in threads the user is in: finding them, the first message of each, why they
were handed on, and where the thread searches start.

Ported from Slack Man's tests/test_threads.py. The "in thread" line and the
summary text are gone (why_seen and the record's thread take their place), and so
is the seen list (the frame does "never twice").

Every id here is made up (the repo is public).
"""
import unittest

from sources import slack
from sources import slack_threads as threadlist
from sources.base import WHY_CHANNEL, WHY_DM, WHY_MENTION, WHY_THREAD, Watch
from sources.slack_connector import ReadResult
from sources.slack_parse import SLACKBOT, SlackMessage
from tests import slack_fixtures as fx

T0 = 1794300000.0  # 2026-11-10 10:00 CET
DAY = 86400
ELSEWHERE = fx.CHANNEL3  # a channel that is not watched
TOP_CHANNEL = fx.CHANNEL2
BOT = "B000TEST01"
BOT2 = "B000TEST02"
MENTION = f"mention:{fx.ME}"
PARENT_TS = T0 - 3 * DAY  # a thread started three days ago


def msg(ts, channel=ELSEWHERE, sender=fx.OTHER, text="hello", thread=None, bot=False, name="Ann Example"):
    return SlackMessage(channel_id=channel, channel_name="#made-up", sender_id=sender, sender_name=name,
                        is_bot=bot, ts=f"{ts:.6f}", time_text="", thread_ts=f"{thread:.6f}" if thread else "",
                        permalink="", text=text)


def result(by_label=None, full=()):
    return ReadResult(messages=dict(by_label or {}), full_pages=set(full))


def identity():
    return {"userId": fx.ME, "workspace": fx.WORKSPACE, "slackbotDm": fx.SLACKBOT_DM, "name": "Me Example"}


def settings(follow=True, **extra):
    s = {"groups": [], "channels": [fx.CHANNEL], "threads": follow, "threadDays": 14}
    s.update(extra)
    return s


def mention_thread_memory(channel=ELSEWHERE, last=T0 - DAY):
    return {"identity": identity(), "threads_seeded": True, "threads": {
        f"{channel}:{PARENT_TS:.6f}": {"channel": channel, "ts": f"{PARENT_TS:.6f}", "mentioned": True,
                                         "written": False, "last": last, "first": f"<@{fx.ME}|Me>, any ideas?",
                                         "first_by": "Ann Example"}}}


def seeded():
    return {"identity": identity(), "threads_seeded": True}


def not_seeded():
    return {"identity": identity()}


class PlanTest(unittest.TestCase):
    def labels(self, mem, follow=True, positions=None, now=T0, **extra):
        return {q.label: q for q in slack.plan_queries(settings(follow, **extra), mem, positions or {}, now)}

    def test_switched_off_adds_no_search(self):
        qs = self.labels(not_seeded(), follow=False)
        self.assertFalse({threadlist.MY_THREADS, threadlist.MENTIONED_THREADS, threadlist.SEED} & set(qs))

    def test_threads_the_user_has_written_in_use_the_with_filter(self):
        q = self.labels(seeded())[threadlist.MY_THREADS]
        self.assertEqual(q.label, "my-threads")
        self.assertEqual(q.kind, "search")
        self.assertEqual(q.args["filters"], f"with:<@{fx.ME}> is:thread -from:<@{fx.ME}>")
        self.assertTrue(q.args["include_bots"])

    def test_threads_where_the_user_is_mentioned_search_their_channels(self):
        mem = mention_thread_memory()
        mem["threads"][f"{fx.DM}:1.000000"] = {"channel": fx.DM, "ts": "1.000000", "mentioned": True, "last": T0}
        mem["threads"][f"{fx.CHANNEL2}:2.000000"] = {"channel": fx.CHANNEL2, "ts": "2.000000",
                                                     "mentioned": False, "written": True, "last": T0}
        q = self.labels(mem, positions={threadlist.MENTIONED_THREADS: T0 - 360})[threadlist.MENTIONED_THREADS]
        # DMs are left out (the DM search has them) and so are threads the user only
        # wrote in (my-threads has them).
        self.assertEqual(q.args["filters"], f"is:thread in:<#{ELSEWHERE}> -from:<@{fx.ME}>")
        self.assertEqual(q.args["after"], str(int(T0 - 360)))

    def test_no_thread_list_no_mentioned_threads_search(self):
        self.assertNotIn(threadlist.MENTIONED_THREADS, self.labels(seeded()))

    def test_no_channel_allowed_no_mentioned_threads_search(self):
        self.assertNotIn(threadlist.MENTIONED_THREADS, self.labels(mention_thread_memory(), maxThreadChannels=0))

    def test_look_back_runs_once_over_thread_days(self):
        q = self.labels(not_seeded())[threadlist.SEED]
        self.assertEqual(q.args["after"], str(int(T0 - 14 * DAY)))
        self.assertEqual(q.args["keywords"], [f"<@{fx.ME}>"])
        self.assertEqual(self.labels(not_seeded(), threadDays=3)[threadlist.SEED].args["after"],
                         str(int(T0 - 3 * DAY)))
        self.assertNotIn(threadlist.SEED, self.labels(seeded()))

    def test_look_back_carries_on_after_a_full_page_then_stops(self):
        qs = slack.plan_queries(settings(), not_seeded(), {}, T0)
        page = [msg(T0 - 10 * DAY + i, text=f"<@{fx.ME}>") for i in range(20)]
        positions, full, done = slack.new_positions(qs, result({threadlist.SEED: page}, full={threadlist.SEED}), T0)
        self.assertFalse(done)
        self.assertIn(threadlist.SEED, full)
        self.assertEqual(positions[threadlist.SEED], float(int(float(page[-1].ts))))
        going_on = dict(not_seeded(), seedFrom=positions[threadlist.SEED])
        q = {q.label: q for q in slack.plan_queries(settings(), going_on, {}, T0 + 60)}[threadlist.SEED]
        self.assertEqual(q.args["after"], str(int(float(page[-1].ts))))
        qs = slack.plan_queries(settings(), going_on, {}, T0 + 60)
        _, full, done = slack.new_positions(qs, result(), T0 + 60)
        self.assertTrue(done)
        self.assertEqual(full, set())


class LearnTest(unittest.TestCase):
    def test_a_mention_puts_its_thread_on_the_list(self):
        reply = msg(T0 - 30, text=f"<@{fx.ME}|Me> can you look?", thread=PARENT_TS)
        top = msg(T0 - 20, channel=TOP_CHANNEL, text=f"hi <@{fx.ME}>")
        t = threadlist.learn({}, result({MENTION: [reply, top]}), fx.ME, T0, 14)
        self.assertTrue(t[f"{ELSEWHERE}:{PARENT_TS:.6f}"]["mentioned"])
        self.assertNotIn("first", t[f"{ELSEWHERE}:{PARENT_TS:.6f}"])  # a reply is not the first message
        top_entry = t[f"{TOP_CHANNEL}:{T0 - 20:.6f}"]
        self.assertTrue(top_entry["mentioned"])
        self.assertEqual(top_entry["first"], f"hi <@{fx.ME}>")
        self.assertEqual(threadlist.follow_channels(t), [TOP_CHANNEL, ELSEWHERE])

    def test_the_look_back_puts_threads_on_the_list_too(self):
        old = msg(T0 - 5 * DAY, text=f"<@{fx.ME}>", thread=PARENT_TS)
        t = threadlist.learn({}, result({threadlist.SEED: [old]}), fx.ME, T0, 14)
        self.assertTrue(t[f"{ELSEWHERE}:{PARENT_TS:.6f}"]["mentioned"])

    def test_my_threads_mark_threads_the_user_has_written_in(self):
        reply = msg(T0 - 30, thread=PARENT_TS)
        t = threadlist.learn({}, result({threadlist.MY_THREADS: [reply]}), fx.ME, T0, 14)
        entry = t[f"{ELSEWHERE}:{PARENT_TS:.6f}"]
        self.assertTrue(entry["written"])
        self.assertFalse(entry["mentioned"])
        self.assertEqual(threadlist.follow_channels(t), [])

    def test_a_quiet_thread_is_dropped_after_thread_days(self):
        threads = mention_thread_memory(last=T0 - DAY)["threads"]
        t = threadlist.learn(threads, result(), fx.ME, T0, 14)
        self.assertEqual(len(t), 1)
        t = threadlist.learn(t, result(), fx.ME, T0 + 13 * DAY - 1, 14)
        self.assertEqual(len(t), 1)
        t = threadlist.learn(t, result(), fx.ME, T0 + 13 * DAY + 1, 14)
        self.assertEqual(t, {})

    def test_a_new_reply_keeps_the_thread_followed(self):
        threads = mention_thread_memory(last=T0 - DAY)["threads"]
        reply = msg(T0 + 10 * DAY, thread=PARENT_TS)
        t = threadlist.learn(threads, result({threadlist.MENTIONED_THREADS: [reply]}), fx.ME, T0 + 10 * DAY, 14)
        t = threadlist.learn(t, result(), fx.ME, T0 + 20 * DAY, 14)
        self.assertEqual(len(t), 1)

    def test_a_reply_in_an_unknown_thread_adds_nothing(self):
        fetched = result({threadlist.MENTIONED_THREADS: [msg(T0, thread=PARENT_TS)],
                          f"channel:{fx.CHANNEL}": [msg(T0, channel=fx.CHANNEL)]})
        self.assertEqual(threadlist.learn({}, fetched, fx.ME, T0, 14), {})

    def test_the_list_keeps_the_most_recently_active_threads(self):
        threads = {f"{ELSEWHERE}:{i}.000000": {"channel": ELSEWHERE, "ts": f"{i}.000000", "mentioned": True,
                                                "written": False, "last": T0 - i}
                   for i in range(threadlist.MAX_THREADS + 5)}
        kept = threadlist.expire(threads, T0, 14)
        self.assertEqual(len(kept), threadlist.MAX_THREADS)
        self.assertNotIn(f"{ELSEWHERE}:{threadlist.MAX_THREADS}.000000", kept)
        self.assertIn(f"{ELSEWHERE}:0.000000", kept)


class SelectTest(unittest.TestCase):
    def check(self, mem, fetched, now=T0):
        t = threadlist.learn(mem.get("threads", {}), fetched, fx.ME, now, 14)
        return slack.select_new(fetched, settings(), mem, fx.ME, {}, t), t

    def test_reply_in_a_thread_where_the_user_is_mentioned_is_handed_on(self):
        # The user is mentioned in the first message; a later reply does not mention them.
        reply = msg(T0 - 30, text=f"<@{fx.THIRD}>, have you had time to check the spreadsheet?", thread=PARENT_TS)
        other = msg(T0 - 20, thread=T0 - DAY)  # another thread in the same channel
        new, _ = self.check(mention_thread_memory(), result({threadlist.MENTIONED_THREADS: [reply, other]}))
        self.assertEqual([m.key for m in new], [reply.key])

    def test_mention_and_reply_in_the_same_check(self):
        mention = msg(T0 - 40, text=f"<@{fx.ME}> thoughts?", thread=PARENT_TS)
        reply = msg(T0 - 30, thread=PARENT_TS, sender=fx.THIRD)
        fetched = result({MENTION: [mention], threadlist.MENTIONED_THREADS: [mention, reply]})
        new, _ = self.check(seeded(), fetched)
        self.assertEqual([m.key for m in new], [mention.key, reply.key])

    def test_reply_in_a_thread_the_user_has_written_in_is_handed_on(self):
        reply = msg(T0 - 30, thread=PARENT_TS)
        new, _ = self.check(seeded(), result({threadlist.MY_THREADS: [reply]}))
        self.assertEqual([m.key for m in new], [reply.key])

    def test_look_back_results_are_never_handed_on(self):
        old = msg(T0 - 5 * DAY, text=f"<@{fx.ME}>")
        new, t = self.check(not_seeded(), result({threadlist.SEED: [old]}))
        self.assertEqual(new, [])
        self.assertEqual(len(t), 1)

    def test_own_replies_slackbot_and_bot_dms_are_left_out(self):
        own = msg(T0 - 50, sender=fx.ME, thread=PARENT_TS)
        slackbot = msg(T0 - 40, sender=SLACKBOT, thread=PARENT_TS)
        bot_dm = msg(T0 - 30, channel=fx.DM, sender=BOT, bot=True, thread=T0 - 100)
        bot_in_thread = msg(T0 - 20, sender=BOT2, bot=True, thread=PARENT_TS)
        new, _ = self.check(mention_thread_memory(),
                            result({threadlist.MY_THREADS: [own, slackbot, bot_dm],
                                    threadlist.MENTIONED_THREADS: [bot_in_thread]}))
        self.assertEqual([m.key for m in new], [bot_in_thread.key])

    def test_message_found_by_several_searches_is_handed_on_once(self):
        # A reply in a watched channel, in a thread where the user is mentioned and has written.
        reply = msg(T0 - 30, channel=fx.CHANNEL, thread=PARENT_TS, text=f"cc <@{fx.ME}>")
        fetched = result({f"channel:{fx.CHANNEL}": [reply], MENTION: [reply],
                          threadlist.MY_THREADS: [reply], threadlist.MENTIONED_THREADS: [reply]})
        new, _ = self.check(mention_thread_memory(channel=fx.CHANNEL), fetched)
        self.assertEqual(len(new), 1)


class FakeAnswers:
    """Answers for fx.FakeReader: the given search results by label, an empty answer
    for every other read."""

    def __init__(self, by_label):
        self.by_label = by_label

    def __call__(self, q):
        if q.kind == "search":
            return fx.search_answer(self.by_label.get(q.label, []))
        return fx.channel_answer([], dm_id=q.args["channel_id"], header="#made-up")


def run_check(mem, by_label, now, positions=None):
    reader = fx.FakeReader(FakeAnswers(by_label))
    source = slack.SlackSource(reader_factory=lambda s, m: reader)
    out = source.check(Watch(settings=settings(), memory=mem), positions or {}, now)
    return out, reader


class NewChannelTest(unittest.TestCase):
    """A mention in a channel not searched yet: the next "mentioned-threads" search
    must start at the mention, or a reply written between the mention and this check
    is missed (the channel was not searched this time)."""

    def test_reply_between_the_mention_and_the_check_is_found_next_time(self):
        mention = msg(T0 - 14 * 60, channel=fx.CHANNEL2, text=f"<@{fx.ME}> thoughts?")
        reply = msg(T0 - 10 * 60, channel=fx.CHANNEL2, thread=T0 - 14 * 60, sender=fx.THIRD)
        fetched = result({MENTION: [mention]})
        start = slack.first_new_mention(seeded(), fetched, fx.ME, {}, threadlist.MAX_CHANNELS)
        self.assertEqual(start, int(T0 - 14 * 60) - 1)
        t = threadlist.learn({}, fetched, fx.ME, T0, 14)
        later = T0 + 15 * 60
        mem = dict(seeded(), threads=t)
        q = {x.label: x for x in slack.plan_queries(settings(), mem, {threadlist.MENTIONED_THREADS: start},
                                                    later)}[threadlist.MENTIONED_THREADS]
        self.assertLessEqual(float(q.args["after"]), float(reply.ts))
        self.assertIn(f"in:<#{fx.CHANNEL2}>", q.args["filters"])
        fetched = result({threadlist.MENTIONED_THREADS: [reply]})
        t = threadlist.learn(t, fetched, fx.ME, later, 14)
        self.assertEqual([m.key for m in slack.select_new(fetched, settings(), mem, fx.ME, {}, t)], [reply.key])
        # Back to normal: the channel is searched now, so nothing lowers the start.
        self.assertIsNone(slack.first_new_mention(mem, fetched, fx.ME, {}, threadlist.MAX_CHANNELS))
        positions, _, _ = slack.new_positions([q], fetched, later)
        self.assertEqual(positions[threadlist.MENTIONED_THREADS], later)

    def test_mention_in_a_channel_already_searched_keeps_the_normal_start(self):
        mention = msg(T0 - 14 * 60, text=f"<@{fx.ME}> thoughts?")
        self.assertIsNone(slack.first_new_mention(mention_thread_memory(), result({MENTION: [mention]}), fx.ME,
                                                  {}, threadlist.MAX_CHANNELS))

    def test_mention_in_a_dm_keeps_the_normal_start(self):
        mention = msg(T0 - 14 * 60, channel=fx.DM, text=f"<@{fx.ME}> thoughts?")
        self.assertIsNone(slack.first_new_mention(seeded(), result({MENTION: [mention]}), fx.ME, {},
                                                  threadlist.MAX_CHANNELS))

    def test_a_check_lowers_the_mentioned_threads_start(self):
        # Through the whole check: the position handed back is the mention's time, so
        # the frame starts the next search there.
        mention = fx.msg(fx.CHANNEL2, f"{T0 - 14 * 60:.6f}", text=f"<@{fx.ME}> thoughts?")
        out, _ = run_check(seeded(), {MENTION: [mention]}, T0)
        self.assertEqual(out.outcome, "ok", out.reason)
        self.assertEqual(out.positions[threadlist.MENTIONED_THREADS], float(int(T0 - 14 * 60) - 1))
        # The same when the search ran this time over another channel.
        out, _ = run_check(mention_thread_memory(), {MENTION: [mention]}, T0)
        self.assertEqual(out.positions[threadlist.MENTIONED_THREADS], float(int(T0 - 14 * 60) - 1))
        # And not when the mention's channel was searched already.
        mention = fx.msg(ELSEWHERE, f"{T0 - 14 * 60:.6f}", text=f"<@{fx.ME}> thoughts?")
        out, _ = run_check(mention_thread_memory(), {MENTION: [mention]}, T0)
        self.assertEqual(out.positions[threadlist.MENTIONED_THREADS], T0)

    # Suspected bug in sources/slack.py: threads found by the one-time look back are
    # searched only from the check after next. The look back puts them on the list,
    # but "mentioned-threads" is not planned in that check, so it gets no position;
    # the next check then starts it at its own `now` (a read the frame does not know
    # starts now), and replies written between the two checks are never read. Slack
    # Man started such a search at the last good check. first_new_mention covers the
    # same gap for mentions found by the mention search, but not for the look back.
    def test_threads_found_by_the_look_back_are_searched_from_that_check_on(self):
        old = fx.msg(ELSEWHERE, f"{T0 - 5 * DAY:.6f}", text=f"<@{fx.ME}> thoughts?")
        out, _ = run_check(not_seeded(), {threadlist.SEED: [old]}, T0)
        self.assertEqual(out.outcome, "ok", out.reason)
        self.assertTrue(out.memory["threads_seeded"])
        self.assertEqual(threadlist.follow_channels(out.memory["threads"]), [ELSEWHERE])
        self.assertLessEqual(out.positions.get(threadlist.MENTIONED_THREADS, float("inf")), T0)


class ChannelLimitTest(unittest.TestCase):
    def threads(self, n):
        t = {f"C000TEST{i:02d}:1.000000": {"channel": f"C000TEST{i:02d}", "ts": "1.000000", "mentioned": True,
                                            "written": False, "last": T0 - i} for i in range(n)}
        t[f"{fx.DM}:1.000000"] = {"channel": fx.DM, "ts": "1.000000", "mentioned": True, "written": False,
                                  "last": T0}
        return t

    def test_channels_over_the_limit_are_counted(self):
        t = self.threads(threadlist.MAX_CHANNELS + 3)
        self.assertEqual(len(threadlist.follow_channels(t)), threadlist.MAX_CHANNELS)
        self.assertEqual(threadlist.channels_left_out(t), 3)
        self.assertEqual(threadlist.follow_channels(t)[0], "C000TEST00")  # most recently active first

    def test_the_limit_is_a_setting(self):
        t = self.threads(8)
        self.assertEqual(threadlist.follow_channels(t, 5), [f"C000TEST{i:02d}" for i in range(5)])
        self.assertEqual(threadlist.channels_left_out(t, 5), 3)
        self.assertEqual(threadlist.follow_channels(t, 0), [])
        self.assertEqual(threadlist.channels_left_out(t, 0), 8)
        mem = dict(seeded(), threads=t)
        q = {x.label: x for x in slack.plan_queries(settings(maxThreadChannels=2), mem, {}, T0)}
        self.assertEqual(q[threadlist.MENTIONED_THREADS].args["filters"],
                         f"is:thread in:<#C000TEST00> in:<#C000TEST01> -from:<@{fx.ME}>")


class WhySeenTest(unittest.TestCase):
    """Why a message was handed on (Slack Man's "in thread" line)."""

    def why(self, m, threads=None, found_by=()):
        return slack.why_seen(m, set(found_by), fx.ME, None, threads or {}, {fx.CHANNEL}, set())

    def test_mentioned_in_this_message(self):
        self.assertEqual(self.why(msg(T0, text=f"hi <@{fx.ME}|Me>")), WHY_MENTION)

    def test_mentioned_in_the_thread(self):
        reply = msg(T0, thread=PARENT_TS)
        self.assertEqual(self.why(reply, mention_thread_memory()["threads"]), WHY_THREAD)

    def test_has_written(self):
        reply = msg(T0, thread=PARENT_TS)
        self.assertEqual(self.why(reply, found_by=[threadlist.MY_THREADS]), WHY_THREAD)
        t = mention_thread_memory()["threads"]
        next(iter(t.values())).update(mentioned=False, written=True)
        self.assertEqual(self.why(reply, t), WHY_THREAD)

    def test_dm(self):
        self.assertEqual(self.why(msg(T0, channel=fx.DM), found_by=["dms"]), WHY_DM)

    def test_not_in_the_thread(self):
        found = [f"channel:{fx.CHANNEL}"]
        self.assertEqual(self.why(msg(T0, channel=fx.CHANNEL, thread=PARENT_TS), found_by=found), WHY_CHANNEL)
        self.assertEqual(self.why(msg(T0, channel=fx.CHANNEL), found_by=found), WHY_CHANNEL)

    def test_mention_of_someone_else_with_a_similar_id_does_not_count(self):
        self.assertFalse(threadlist.mentions(f"<@{fx.ME}X>", fx.ME))
        self.assertTrue(threadlist.mentions(f"<@{fx.ME}|Me>", fx.ME))
        self.assertFalse(threadlist.mentions(f"<@{fx.ME}>", ""))
        self.assertNotEqual(self.why(msg(T0, channel=fx.CHANNEL, text=f"<@{fx.ME}X>"),
                                     found_by=[f"channel:{fx.CHANNEL}"]), WHY_MENTION)


class FirstMessageTest(unittest.TestCase):
    def test_exact_window_around_the_first_message(self):
        self.assertEqual(threadlist.around("1793717797.607069"), ("1793717797.607068", "1793717797.607070"))
        self.assertEqual(threadlist.around("1793717797.000000"), ("1793717796.999999", "1793717797.000001"))

    def test_read_only_when_not_known(self):
        threads = mention_thread_memory()["threads"]
        known = msg(T0, thread=PARENT_TS)
        unknown = msg(T0 + 1, channel=fx.CHANNEL, thread=T0 - 500)
        unknown_again = msg(T0 + 2, channel=fx.CHANNEL, thread=T0 - 500)
        parent_here = msg(T0 - 400, channel=TOP_CHANNEL)
        with_parent = msg(T0 + 3, channel=TOP_CHANNEL, thread=T0 - 400)
        top = msg(T0 + 4, channel=fx.CHANNEL)
        fetched = result({"dms": [parent_here]})
        qs = threadlist.first_queries([known, unknown, unknown_again, with_parent, top], threads, fetched)
        self.assertEqual(len(qs), 1)
        q = qs[0]
        self.assertEqual(q.kind, "channel")
        self.assertEqual(q.label, f"{threadlist.FIRST_PREFIX}{fx.CHANNEL}:{T0 - 500:.6f}")
        self.assertEqual(q.args["channel_id"], fx.CHANNEL)
        self.assertEqual(q.args["limit"], 1)
        self.assertEqual((q.args["oldest"], q.args["latest"]), threadlist.around(f"{T0 - 500:.6f}"))

    def test_at_most_a_few_reads_per_check(self):
        replies = [msg(T0 + i, thread=T0 - 1000 - i) for i in range(threadlist.MAX_FIRST_READS + 3)]
        self.assertEqual(len(threadlist.first_queries(replies, {}, result())), threadlist.MAX_FIRST_READS)

    def test_first_message_is_kept_and_can_put_the_thread_on_the_list(self):
        reply = msg(T0, channel=fx.CHANNEL, thread=PARENT_TS)
        q = threadlist.first_queries([reply], {}, result())[0]
        parent = msg(PARENT_TS, channel=fx.CHANNEL, name="Ann Example <ann.example@example.com>",
                     text=f"<@{fx.ME}|Me>, any ideas?\nThread: 41 replies (latest: 2026-11-10 18:41:23 CET)")
        t = threadlist.add_firsts({}, result({q.label: [parent]}), fx.ME, T0)
        entry = t[threadlist.key(reply)]
        self.assertEqual(entry["first"], f"<@{fx.ME}|Me>, any ideas?")
        self.assertEqual(entry["first_by"], "Ann Example")
        self.assertTrue(entry["mentioned"])
        self.assertEqual(slack.why_seen(reply, {f"channel:{fx.CHANNEL}"}, fx.ME, None, t, {fx.CHANNEL}, set()),
                         WHY_THREAD)
        self.assertEqual(threadlist.first_of(reply, t, result()), (f"<@{fx.ME}|Me>, any ideas?", "Ann Example"))

    def test_the_users_own_first_message_does_not_count_as_a_mention(self):
        reply = msg(T0, channel=fx.CHANNEL, thread=PARENT_TS)
        q = threadlist.first_queries([reply], {}, result())[0]
        parent = msg(PARENT_TS, channel=fx.CHANNEL, sender=fx.ME, text=f"note to self <@{fx.ME}>")
        t = threadlist.add_firsts({}, result({q.label: [parent]}), fx.ME, T0)
        self.assertFalse(t[threadlist.key(reply)]["mentioned"])


class RecordThreadTest(unittest.TestCase):
    """Replies carry the first message of their thread (Slack Man's summary line)."""

    def record(self, m, threads):
        return slack.to_record(m, fx.WORKSPACE, {threadlist.MENTIONED_THREADS}, fx.ME, None, threads, result(),
                               {fx.CHANNEL}, set())

    def test_replies_get_the_first_message_when_it_is_known(self):
        threads = mention_thread_memory()["threads"]
        reply = self.record(msg(T0, thread=PARENT_TS, text="have you checked?"), threads)
        self.assertEqual(reply.thread.head, f"<@{fx.ME}|Me>, any ideas?")
        self.assertEqual(reply.thread.id, f"{ELSEWHERE}:{PARENT_TS:.6f}")
        lost = self.record(msg(T0 + 2, channel=fx.CHANNEL, thread=T0 - 900), threads)
        self.assertEqual(lost.thread.head, "")  # could not be read this time
        top = self.record(msg(T0 + 1, channel=fx.CHANNEL, text="news"), threads)
        self.assertIsNone(top.thread)

    def test_the_first_message_of_a_followed_thread_starts_it(self):
        threads = mention_thread_memory()["threads"]
        first = self.record(msg(PARENT_TS, text="start"), threads)
        self.assertTrue(first.thread.first)
        self.assertEqual(first.thread.head, "")


if __name__ == "__main__":
    unittest.main()
