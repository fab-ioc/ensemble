"""Mentions of a user group: typed by a person (`<@S...>`) or posted by a bot
(`<!subteam^S...>`), counted only while the user is in the group (or always, for a
group in groupsAlways), handed on once, and their thread followed.

Ported from Slack Man's tests/test_group_mentions.py. The "in thread" line is
gone (why_seen takes its place), and so is the seen list (the frame does "never
twice").

Every id here is made up (the repo is public).
"""
import unittest

from sources import slack
from sources import slack_threads as threadlist
from sources.base import WHY_GROUP, WHY_MENTION, WHY_THREAD
from sources.slack_connector import ReadResult
from sources.slack_parse import SLACKBOT, SlackMessage
from tests import slack_fixtures as fx

ADDED = 1794376803.0    # 2026-11-11 07:00:03 CET
REMOVED = 1794400203.0  # 2026-11-11 13:30:03 CET
ASK_TS = 1794381314.000100    # 08:15:14 CET, a group mention the user must not miss
THREAD_TS = 1794381089.000200  # the alert it answers
ALERTS = fx.CHANNEL3
OPS = fx.CHANNEL2
BOT = "B000TEST01"
EVENTS = [(ADDED, fx.GROUP, True), (REMOVED, fx.GROUP, False)]
GROUP_LABEL = f"group:{fx.GROUP}"
MENTION = f"mention:{fx.ME}"


def settings(**extra):
    s = {"groups": [fx.GROUP], "channels": [fx.CHANNEL], "threads": True, "threadDays": 14}
    s.update(extra)
    return s


def memory(**extra):
    m = {"identity": {"userId": fx.ME, "workspace": fx.WORKSPACE, "slackbotDm": fx.SLACKBOT_DM,
                      "name": "Me Example"},
         "threads_seeded": True}
    m.update(extra)
    return m


def msg(ts, channel=ALERTS, sender=fx.OTHER, text="hello", thread=None, bot=False, name="Ann Example"):
    return SlackMessage(channel_id=channel, channel_name="#made-up-alerts", sender_id=sender, sender_name=name,
                        is_bot=bot, ts=f"{ts:.6f}", time_text="", thread_ts=f"{thread:.6f}" if thread else "",
                        permalink="", text=text)


def result(by_label):
    return ReadResult(messages=dict(by_label))


def by_person(ts=ASK_TS, **kw):
    return msg(ts, thread=THREAD_TS, text=f"<@{fx.GROUP}> could you please check if anything is serious", **kw)


def by_bot(ts):
    return msg(ts, channel=OPS, sender=BOT, bot=True, name="Made-up Automation",
               text=f"*Made-up notice!*\nAttention: <!subteam^{fx.GROUP}>")


class SearchTest(unittest.TestCase):
    def test_one_search_per_group_with_the_bare_id(self):
        qs = [q for q in slack.plan_queries(settings(), memory(), {}, ADDED + 3600)
              if q.label.startswith("group:")]
        self.assertEqual(len(qs), 1)
        q = qs[0]
        self.assertEqual(q.kind, "search")
        # The bare id matches both `<@S...>` (typed by a person) and `<!subteam^S...>` (a bot).
        self.assertEqual(q.args["keywords"], [fx.GROUP])
        self.assertEqual(q.args["filters"], f"-from:<@{fx.ME}> -from:<@{SLACKBOT}>")
        self.assertTrue(q.args["include_bots"])

    def test_a_group_listed_twice_is_searched_once(self):
        s = settings(groups=[fx.GROUP, fx.GROUP], groupsAlways=[fx.GROUP, fx.GROUP2])
        labels = [q.label for q in slack.plan_queries(s, memory(), {}, ADDED)]
        self.assertEqual([lb for lb in labels if lb.startswith("group:")], [GROUP_LABEL, f"group:{fx.GROUP2}"])


class SelectTest(unittest.TestCase):
    def select(self, by_label, mem=None, s=None, events=EVENTS, threads=None):
        fetched = result(by_label)
        s = s or settings()
        mem = mem or memory()
        hits = slack.group_mentions(fetched, s, mem, events)
        return slack.select_new(fetched, s, mem, fx.ME, hits, threads or {})

    def test_mention_typed_by_a_person_is_handed_on(self):
        m = by_person()
        self.assertEqual([x.key for x in self.select({GROUP_LABEL: [m]})], [m.key])

    def test_mention_posted_by_a_bot_is_still_handed_on(self):
        m = by_bot(ADDED + 600)
        self.assertEqual([x.key for x in self.select({GROUP_LABEL: [m]})], [m.key])

    def test_mention_while_the_user_is_out_of_the_group_is_not_handed_on(self):
        before = by_person(ts=ADDED - 600)
        after = by_bot(REMOVED + 600)
        self.assertEqual(self.select({GROUP_LABEL: [before, after]}), [])

    def test_the_same_message_from_several_searches_is_handed_on_once(self):
        # The message also mentions the user, and the thread is followed: three searches find it.
        m = by_person()
        m.text += f" cc <@{fx.ME}>"
        again = by_person()
        again.text = m.text
        threads = {threadlist.key(m): {"channel": ALERTS, "ts": f"{THREAD_TS:.6f}", "mentioned": True, "last": 0}}
        new = self.select({GROUP_LABEL: [m], MENTION: [again], threadlist.MENTIONED_THREADS: [by_person()]},
                          threads=threads)
        self.assertEqual([x.key for x in new], [m.key])

    def test_slackbot_notice_is_never_handed_on(self):
        notice = msg(ADDED, channel=fx.SLACKBOT_DM, sender=SLACKBOT, name="Slack",
                     text=f"You have been added to the *made-up* (<!subteam^{fx.GROUP}>) user group")
        self.assertEqual(self.select({GROUP_LABEL: [notice]}), [])
        self.assertEqual(slack.group_mentions(result({GROUP_LABEL: [notice]}), settings(), memory(), EVENTS), {})
        always = settings(groups=[], groupsAlways=[fx.GROUP])
        self.assertEqual(slack.group_mentions(result({GROUP_LABEL: [notice]}), always, memory(), []), {})


class AlwaysTest(unittest.TestCase):
    """A group in groupsAlways counts whatever the membership."""

    def test_counts_with_no_membership_known(self):
        s = settings(groups=[], groupsAlways=[fx.GROUP])
        m = by_person(ts=ADDED - 600)
        fetched = result({GROUP_LABEL: [m]})
        hits = slack.group_mentions(fetched, s, memory(), [])
        self.assertEqual(hits, {m.key: fx.GROUP})
        self.assertEqual([x.key for x in slack.select_new(fetched, s, memory(), fx.ME, hits, {})], [m.key])

    def test_counts_even_after_a_removal(self):
        s = settings(groups=[fx.GROUP], groupsAlways=[fx.GROUP])
        m = by_bot(REMOVED + 600)
        self.assertEqual(slack.group_mentions(result({GROUP_LABEL: [m]}), s, memory(), EVENTS), {m.key: fx.GROUP})

    def test_needs_no_slackbot_read(self):
        labels = {q.label for q in slack.plan_queries(settings(groups=[], groupsAlways=[fx.GROUP]), memory(),
                                                      {}, ADDED)}
        self.assertIn(GROUP_LABEL, labels)
        self.assertNotIn("slackbot", labels)

    def test_only_the_listed_group_counts_always(self):
        s = settings(groups=[fx.GROUP], groupsAlways=[fx.GROUP2])
        a, b = by_person(ts=ADDED - 600), by_person(ts=ADDED - 500)
        hits = slack.group_mentions(result({GROUP_LABEL: [a], f"group:{fx.GROUP2}": [b]}), s, memory(), [])
        self.assertEqual(hits, {b.key: fx.GROUP2})


class FollowTest(unittest.TestCase):
    def learn(self, by_label, prior=None, events=EVENTS, s=None):
        fetched = result(by_label)
        hits = slack.group_mentions(fetched, s or settings(), memory(), events)
        return threadlist.learn(prior or {}, fetched, fx.ME, ASK_TS + 30, 14, hits), hits, fetched

    def why(self, m, found_by, hits, threads):
        return slack.why_seen(m, set(found_by), fx.ME, hits.get(m.key), threads, {fx.CHANNEL}, set())

    def test_the_thread_is_followed_and_the_group_named(self):
        m = by_person()
        t, hits, _ = self.learn({GROUP_LABEL: [m]})
        entry = t[f"{ALERTS}:{THREAD_TS:.6f}"]
        self.assertTrue(entry["mentioned"])
        self.assertEqual(entry["group"], fx.GROUP)
        self.assertEqual(threadlist.follow_channels(t), [ALERTS])
        self.assertEqual(threadlist.through_group(t), 1)
        self.assertEqual(self.why(m, [GROUP_LABEL], hits, t), WHY_GROUP)
        record = slack.to_record(m, fx.WORKSPACE, {GROUP_LABEL}, fx.ME, hits.get(m.key), t, result({}),
                                 {fx.CHANNEL}, set())
        self.assertTrue(record.addressed)

    def test_a_group_in_groups_always_is_followed_too(self):
        m = by_person(ts=REMOVED + 600)
        t, hits, _ = self.learn({GROUP_LABEL: [m]}, events=[], s=settings(groups=[], groupsAlways=[fx.GROUP]))
        self.assertEqual(t[f"{ALERTS}:{THREAD_TS:.6f}"]["group"], fx.GROUP)

    def test_a_later_reply_in_that_thread_is_handed_on(self):
        t, _, _ = self.learn({GROUP_LABEL: [by_person()]})
        reply = msg(ASK_TS + 120, thread=THREAD_TS, sender=fx.THIRD, text="looking", name="Bo Example")
        fetched = result({threadlist.MENTIONED_THREADS: [reply]})
        new = slack.select_new(fetched, settings(), memory(), fx.ME, {}, t)
        self.assertEqual([x.key for x in new], [reply.key])
        self.assertEqual(self.why(reply, [threadlist.MENTIONED_THREADS], {}, t), WHY_THREAD)

    def test_mention_while_out_of_the_group_does_not_follow_the_thread(self):
        t, hits, _ = self.learn({GROUP_LABEL: [by_person(ts=REMOVED + 60)]})
        self.assertEqual(hits, {})
        self.assertEqual(t, {})

    def test_a_mention_of_the_user_takes_over_as_the_reason(self):
        t, _, _ = self.learn({GROUP_LABEL: [by_person()]})
        direct = msg(ASK_TS + 60, thread=THREAD_TS, text=f"<@{fx.ME}> can you look?")
        t, hits, _ = self.learn({MENTION: [direct]}, prior=t)
        entry = t[f"{ALERTS}:{THREAD_TS:.6f}"]
        self.assertTrue(entry["mentioned"])
        self.assertNotIn("group", entry)
        self.assertEqual(threadlist.through_group(t), 0)
        self.assertEqual(self.why(direct, [MENTION], hits, t), WHY_MENTION)
        # And a group mention after that does not change it back.
        t, _, _ = self.learn({GROUP_LABEL: [by_person(ts=ASK_TS + 90)]}, prior=t)
        self.assertNotIn("group", t[f"{ALERTS}:{THREAD_TS:.6f}"])

    def test_the_thread_search_starts_at_the_group_mention(self):
        # The channel is not searched yet: the next "mentioned-threads" search must start
        # at the mention, so a reply written between it and the next check is not missed.
        m = by_person()
        fetched = result({GROUP_LABEL: [m]})
        hits = slack.group_mentions(fetched, settings(), memory(), EVENTS)
        self.assertEqual(slack.first_new_mention(memory(), fetched, fx.ME, hits, threadlist.MAX_CHANNELS),
                         int(ASK_TS) - 1)
        # A group mention that does not count leaves the start alone.
        self.assertIsNone(slack.first_new_mention(memory(), fetched, fx.ME, {}, threadlist.MAX_CHANNELS))


if __name__ == "__main__":
    unittest.main()
