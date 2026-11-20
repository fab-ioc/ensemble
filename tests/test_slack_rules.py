"""The Slack source's plan, group membership, choice of new messages and message records.

Ported from Slack Man's tests/test_rules.py. The overlap and "never twice" are the
frame's job now (outside.py), so the tests of the seen list and of the start time
after the last good check are gone; read positions take their place.

Every id here is made up (the repo is public).
"""
import re
import unittest

from sources import slack
from sources import slack_threads as threadlist
from sources.base import WHY_CHANNEL, WHY_DM, WHY_GROUP, WHY_INITIATIVE, WHY_MENTION, WHY_THREAD
from sources.slack_connector import ReadResult
from sources.slack_parse import SLACKBOT, SlackMessage, membership_events, parse_channel
from tests import slack_fixtures as fx

T0 = 1794300000.0  # 2026-11-10 10:00 CET
DAY = 86400
BOT = "B000TEST01"
CHANNEL4 = "C000TEST04"  # a channel only a routing table lists


def memory(**extra):
    m = {"identity": {"userId": fx.ME, "workspace": fx.WORKSPACE, "slackbotDm": fx.SLACKBOT_DM,
                      "name": "Me Example"},
         "threads_seeded": True}
    m.update(extra)
    return m


def settings(**extra):
    s = {"groups": [fx.GROUP], "channels": [fx.CHANNEL], "threads": False}
    s.update(extra)
    return s


def msg(ts, channel=fx.DM, sender=fx.OTHER, bot=False, text="hello", thread=None, name="Ann Example"):
    return SlackMessage(channel_id=channel, channel_name="#made-up", sender_id=sender, sender_name=name,
                        is_bot=bot, ts=f"{ts:.6f}", time_text="", thread_ts=f"{thread:.6f}" if thread else "",
                        permalink="", text=text)


def result(by_label=None, full=()):
    return ReadResult(messages=dict(by_label or {}), full_pages=set(full))


def plan(s=None, mem=None, positions=None, now=T0, places=()):
    return {q.label: q for q in slack.plan_queries(settings() if s is None else s,
                                                   memory() if mem is None else mem,
                                                   positions or {}, now, places)}


def select(fetched, s=None, mem=None, events=(), threads=None, places=()):
    s = settings() if s is None else s
    mem = memory() if mem is None else mem
    hits = slack.group_mentions(fetched, s, mem, list(events))
    return slack.select_new(fetched, s, mem, fx.ME, hits, threads or {}, places)


MENTION = f"mention:{fx.ME}"
GROUP_LABEL = f"group:{fx.GROUP}"


class PlanTest(unittest.TestCase):
    def test_first_check_reads_nothing_from_before_it(self):
        qs = plan()
        searches = {label: q for label, q in qs.items() if q.kind == "search"}
        self.assertEqual(set(searches), {"dms", MENTION, GROUP_LABEL, f"channel:{fx.CHANNEL}"})
        for q in searches.values():
            self.assertEqual(q.args["after"], str(int(T0)))
            self.assertIn(f"-from:<@{fx.ME}>", q.args["filters"])

    def test_each_read_starts_where_the_frame_says(self):
        qs = plan(positions={"dms": T0 - 360, f"channel:{fx.CHANNEL}": T0 - 360})
        self.assertEqual(qs["dms"].args["after"], str(int(T0 - 360)))
        self.assertEqual(qs[f"channel:{fx.CHANNEL}"].args["after"], str(int(T0 - 360)))
        # A read the frame does not know yet starts now.
        self.assertEqual(qs[MENTION].args["after"], str(int(T0)))

    def test_dms_leave_out_bots_and_mentions_keep_them(self):
        qs = plan()
        self.assertEqual(qs["dms"].args["channel_types"], "im,mpim")
        self.assertFalse(qs["dms"].args["include_bots"])
        self.assertEqual(qs[MENTION].args["keywords"], [f"<@{fx.ME}>"])
        self.assertTrue(qs[MENTION].args["include_bots"])

    def test_switched_off_reads_are_not_planned(self):
        qs = plan(s=settings(dms=False, mentions=False, groups=[], channels=[]))
        self.assertEqual(qs, {})

    def test_slackbot_is_read_14_days_back_while_membership_is_unknown(self):
        sb = plan()["slackbot"]
        self.assertEqual(sb.kind, "channel")
        self.assertEqual(sb.args["channel_id"], fx.SLACKBOT_DM)
        self.assertEqual(sb.args["oldest"], str(int(T0 - 14 * DAY)))
        known = memory(membership={fx.GROUP: {"member": False, "ts": T0 - 3600}})
        self.assertEqual(plan(mem=known, positions={"slackbot": T0 - 360})["slackbot"].args["oldest"],
                         str(int(T0 - 360)))
        self.assertEqual(plan(mem=known)["slackbot"].args["oldest"], str(int(T0)))

    def test_no_slackbot_read_without_a_group_that_needs_membership(self):
        self.assertNotIn("slackbot", plan(s=settings(groups=[], groupsAlways=[fx.GROUP])))
        no_dm = memory()
        del no_dm["identity"]["slackbotDm"]
        self.assertNotIn("slackbot", plan(mem=no_dm))


class SelectTest(unittest.TestCase):
    def test_message_found_by_two_searches_is_handed_on_once(self):
        a = msg(T0 - 30, text=f"<@{fx.ME}> look")  # a DM that also mentions the user
        new = select(result({"dms": [a], MENTION: [a]}))
        self.assertEqual([m.key for m in new], [a.key])

    def test_own_bot_and_slackbot_messages_are_dropped(self):
        own = msg(T0 - 50, sender=fx.ME)
        bot_dm = msg(T0 - 40, sender=BOT, bot=True)
        slackbot = msg(T0 - 30, sender=SLACKBOT)
        bot_in_channel = msg(T0 - 20, channel=fx.CHANNEL, sender=BOT, bot=True)
        fetched = result({"dms": [own, bot_dm, slackbot], f"channel:{fx.CHANNEL}": [bot_in_channel]})
        self.assertEqual([m.key for m in select(fetched)], [bot_in_channel.key])

    def test_messages_are_sorted_by_time(self):
        a, b = msg(T0 - 10, channel=fx.CHANNEL), msg(T0 - 50)
        new = select(result({f"channel:{fx.CHANNEL}": [a], "dms": [b]}))
        self.assertEqual([m.key for m in new], [b.key, a.key])

    def test_slackbot_read_and_first_message_reads_are_never_handed_on(self):
        sb = msg(T0 - 30, channel=fx.SLACKBOT_DM, sender=fx.OTHER)
        first = msg(T0 - 20, channel=fx.CHANNEL)
        fetched = result({"slackbot": [sb], f"{threadlist.FIRST_PREFIX}{fx.CHANNEL}:{first.ts}": [first]})
        self.assertEqual(select(fetched), [])


class PositionsTest(unittest.TestCase):
    def test_full_page_continues_from_its_last_message(self):
        queries = slack.plan_queries(settings(), memory(), {}, T0)
        page = [msg(T0 - 600 + i) for i in range(20)]
        positions, full, seeded = slack.new_positions(queries, result({"dms": page}, full={"dms"}), T0)
        self.assertEqual(positions["dms"], float(int(float(page[-1].ts))))
        self.assertEqual(full, {"dms"})
        self.assertEqual(positions[f"channel:{fx.CHANNEL}"], T0)
        self.assertFalse(seeded)
        # The next check asks from there (the frame applies its overlap first).
        qs = plan(positions=positions, now=T0 + 60)
        self.assertEqual(qs["dms"].args["after"], str(int(float(page[-1].ts))))

    def test_every_planned_read_gets_a_position(self):
        queries = slack.plan_queries(settings(), memory(), {}, T0)
        positions, full, _ = slack.new_positions(queries, result({"dms": [msg(T0 - 5)]}), T0)
        self.assertEqual(set(positions), {q.label for q in queries})
        self.assertEqual(set(positions.values()), {T0})  # not full: everything up to now
        self.assertEqual(full, set())


class OnCallTest(unittest.TestCase):
    """The user is added to the group at 07:00 and removed at 13:30."""

    ADDED = 1794290402.0    # 2026-11-10 07:00:02 CET
    REMOVED = 1794313803.0  # 2026-11-10 13:30:03 CET

    def events(self):
        return [(self.ADDED, fx.GROUP, True), (self.REMOVED, fx.GROUP, False)]

    def test_member_between_added_and_removed(self):
        ev = self.events()
        self.assertFalse(slack.member_at(fx.GROUP, self.ADDED - 60, {}, ev))
        self.assertTrue(slack.member_at(fx.GROUP, self.ADDED + 60, {}, ev))
        self.assertTrue(slack.member_at(fx.GROUP, self.REMOVED - 1, {}, ev))
        self.assertFalse(slack.member_at(fx.GROUP, self.REMOVED + 1, {}, ev))

    def test_no_message_at_all_means_not_a_member(self):
        self.assertFalse(slack.member_at(fx.GROUP, self.ADDED, {}, []))

    def test_known_membership_is_used_when_no_new_message(self):
        prior = {fx.GROUP: {"member": True, "ts": self.ADDED}}
        self.assertTrue(slack.member_at(fx.GROUP, self.ADDED + 3600, prior, []))
        prior = {fx.GROUP: {"member": False, "ts": self.REMOVED}}
        self.assertFalse(slack.member_at(fx.GROUP, self.REMOVED + 3600, prior, []))

    def test_group_mentions_only_while_a_member(self):
        before = msg(self.ADDED - 600, channel=fx.CHANNEL3)
        during = msg(self.ADDED + 600, channel=fx.CHANNEL3)
        after = msg(self.REMOVED + 600, channel=fx.CHANNEL3)
        new = select(result({GROUP_LABEL: [before, during, after]}), events=self.events())
        self.assertEqual([m.key for m in new], [during.key])

    def test_membership_is_read_from_slackbot_messages(self):
        answer = fx.channel_answer([
            fx.removed(f"{self.REMOVED:.6f}"),
            fx.added(f"{self.ADDED:.6f}"),
            (SLACKBOT, "Slack", f"{self.ADDED + 5:.6f}", "Some other Slackbot notice"),
        ])
        messages, header = parse_channel(answer, fx.SLACKBOT_DM, "slackbot")
        events = membership_events(messages)
        self.assertEqual(events, self.events())
        st = slack.update_membership({}, events)
        self.assertEqual((st[fx.GROUP]["member"], st[fx.GROUP]["ts"]), (False, self.REMOVED))
        # An older message never overrides a newer one.
        st = slack.update_membership(st, [(self.ADDED, fx.GROUP, True)])
        self.assertFalse(st[fx.GROUP]["member"])

    def test_mention_found_after_the_removal_was_saved_still_counts(self):
        # Both Slackbot messages were read and kept in earlier checks; a group search
        # still working through a backlog then finds a mention from 10:00.
        saved = slack.update_membership({}, self.events())
        during = msg(self.ADDED + 3 * 3600, channel=fx.CHANNEL3)
        new = select(result({GROUP_LABEL: [during]}), mem=memory(membership=saved))
        self.assertEqual(len(new), 1)

    def test_old_saved_membership_without_history_still_works(self):
        prior = {fx.GROUP: {"member": True, "ts": self.ADDED}}
        st = slack.update_membership(prior, [(self.REMOVED, fx.GROUP, False)])
        self.assertTrue(slack.member_at(fx.GROUP, self.ADDED + 60, st, []))
        self.assertFalse(slack.member_at(fx.GROUP, self.REMOVED + 60, st, []))

    def test_history_keeps_only_the_latest_changes(self):
        events = [(self.ADDED + i, fx.GROUP, i % 2 == 0) for i in range(slack.HISTORY_KEEP + 5)]
        st = slack.update_membership({}, events)
        self.assertEqual(len(st[fx.GROUP]["history"]), slack.HISTORY_KEEP)
        self.assertEqual(st[fx.GROUP]["ts"], self.ADDED + slack.HISTORY_KEEP + 4)


class PlacesTest(unittest.TestCase):
    """Channels and threads the routing tables list (Watch.places)."""

    THREAD_TS = T0 - DAY

    def test_a_place_channel_gets_a_channel_search(self):
        qs = plan(places=[{"channel": CHANNEL4}])
        q = qs[f"channel:{CHANNEL4}"]
        self.assertEqual(q.kind, "search")
        self.assertEqual(q.args["filters"], f"in:<#{CHANNEL4}> -from:<@{fx.ME}>")
        self.assertTrue(q.args["include_bots"])

    def test_a_channel_in_the_settings_and_a_table_is_searched_once(self):
        qs = slack.plan_queries(settings(), memory(), {}, T0, [{"channel": fx.CHANNEL}])
        self.assertEqual([q.label for q in qs].count(f"channel:{fx.CHANNEL}"), 1)

    def test_a_place_thread_gets_a_thread_search_over_its_channel(self):
        places = [{"channel": fx.CHANNEL2, "thread": f"{self.THREAD_TS:.6f}"},
                  {"channel": fx.CHANNEL2, "thread": f"{self.THREAD_TS + 5:.6f}"},
                  {"channel": fx.DM, "thread": f"{self.THREAD_TS:.6f}"}]
        qs = plan(places=places)
        q = qs[slack.INITIATIVE_THREADS]
        # One in: per channel; a DM thread is left out (the DM search has it).
        self.assertEqual(q.args["filters"], f"is:thread in:<#{fx.CHANNEL2}> -from:<@{fx.ME}>")
        self.assertNotIn(f"channel:{fx.CHANNEL2}", qs)  # not every message of the channel

    def test_only_the_listed_thread_is_handed_on(self):
        places = [{"channel": fx.CHANNEL2, "thread": f"{self.THREAD_TS:.6f}"}]
        first = msg(self.THREAD_TS, channel=fx.CHANNEL2)
        reply = msg(T0 - 30, channel=fx.CHANNEL2, thread=self.THREAD_TS)
        other_thread = msg(T0 - 20, channel=fx.CHANNEL2, thread=self.THREAD_TS - 100)
        new = select(result({slack.INITIATIVE_THREADS: [first, reply, other_thread]}), places=places)
        self.assertEqual([m.key for m in new], [first.key, reply.key])

    def test_malformed_ids_never_reach_a_search(self):
        s = settings(groups=[fx.GROUP, "S000TEST02 OR x", "@made-up-team", "s000test03"],
                     groupsAlways=[f"<!subteam^{fx.GROUP2}>"],
                     channels=[fx.CHANNEL, f"{fx.CHANNEL2}> in:<#{fx.CHANNEL3}", "general", "c000test05"],
                     threads=True)
        places = [{"channel": f"{fx.CHANNEL3} -from:x"},
                  {"channel": CHANNEL4, "thread": "1794300000 OR 1"},
                  {"channel": "made-up", "thread": f"{T0:.6f}"},
                  {"channel": None},
                  {}]
        qs = slack.plan_queries(s, memory(), {}, T0, places)
        self.assertEqual(sorted(q.label for q in qs),
                         sorted(["dms", MENTION, GROUP_LABEL, f"channel:{fx.CHANNEL}",
                                 threadlist.MY_THREADS, "slackbot"]))
        well_formed = re.compile(r"^(?:-from:<@[A-Z0-9]+>|in:<#[A-Z0-9]+>|with:<@[A-Z0-9]+>|is:thread)$")
        for q in qs:
            for part in (q.args.get("filters") or "").split():
                self.assertRegex(part, well_formed, q.label)
            for word in q.args.get("keywords") or []:
                self.assertRegex(word, r"^(?:<@[A-Z0-9]+>|S[A-Z0-9]+)$", q.label)

    def test_settings_are_tidied(self):
        s = slack.settings_of({"groups": fx.GROUP, "channels": [f" {fx.CHANNEL} ", "", "  "],
                               "threadDays": None})
        self.assertEqual(s["groups"], [fx.GROUP])  # a single id becomes a list
        self.assertEqual(s["channels"], [fx.CHANNEL])
        self.assertEqual(s["threadDays"], slack.THREAD_DAYS)  # a missing value takes the default
        labels = {q.label for q in slack.plan_queries(s, memory(), {}, T0)}
        self.assertIn(GROUP_LABEL, labels)
        self.assertIn(f"channel:{fx.CHANNEL}", labels)


class RecordTest(unittest.TestCase):
    """why_seen and to_record: why a message was handed on, and its record."""

    def record(self, m, found_by, group=None, threads=None, own=(fx.CHANNEL,), wanted=()):
        return slack.to_record(m, fx.WORKSPACE, set(found_by), fx.ME, group, threads or {}, result(),
                               set(own), set(wanted))

    def test_a_dm(self):
        r = self.record(msg(T0), {"dms"})
        self.assertEqual((r.why_seen, r.addressed, r.place.kind), (WHY_DM, True, "dm"))

    def test_a_mention(self):
        r = self.record(msg(T0, channel=fx.CHANNEL3, text=f"<@{fx.ME}|me> look"), {MENTION})
        self.assertEqual((r.why_seen, r.addressed, r.place.kind), (WHY_MENTION, True, "channel"))

    def test_a_counted_group_mention(self):
        r = self.record(msg(T0, channel=fx.CHANNEL3, text=f"<@{fx.GROUP}> look"), {GROUP_LABEL}, group=fx.GROUP)
        self.assertEqual((r.why_seen, r.addressed), (WHY_GROUP, True))

    def test_a_thread_the_user_is_in(self):
        parent = T0 - DAY
        reply = msg(T0, channel=fx.CHANNEL3, thread=parent)
        r = self.record(reply, {threadlist.MY_THREADS})
        self.assertEqual((r.why_seen, r.addressed), (WHY_THREAD, True))
        threads = {threadlist.key(reply): {"channel": fx.CHANNEL3, "ts": f"{parent:.6f}", "mentioned": True,
                                           "written": False, "last": T0}}
        r = self.record(reply, {f"channel:{fx.CHANNEL3}"}, threads=threads)
        self.assertEqual((r.why_seen, r.addressed), (WHY_THREAD, True))

    def test_the_users_own_channel(self):
        r = self.record(msg(T0, channel=fx.CHANNEL), {f"channel:{fx.CHANNEL}"})
        self.assertEqual((r.why_seen, r.addressed), (WHY_CHANNEL, False))

    def test_a_place_channel_and_a_place_thread(self):
        r = self.record(msg(T0, channel=CHANNEL4), {f"channel:{CHANNEL4}"})
        self.assertEqual((r.why_seen, r.addressed), (WHY_INITIATIVE, False))
        reply = msg(T0, channel=fx.CHANNEL2, thread=T0 - DAY)
        r = self.record(reply, {slack.INITIATIVE_THREADS}, wanted={threadlist.key(reply)})
        self.assertEqual((r.why_seen, r.addressed), (WHY_INITIATIVE, False))

    def test_the_record_fields(self):
        m = msg(T0, channel=fx.CHANNEL3, name="Ann Example <ann.example@example.com>")
        r = self.record(m, {f"channel:{fx.CHANNEL3}"})
        self.assertEqual(r.key, f"slack:example.slack.com:{fx.CHANNEL3}:{T0:.6f}")
        self.assertEqual(r.source, "slack")
        self.assertEqual(r.at, T0)
        self.assertEqual((r.author.id, r.author.name, r.author.me), (fx.OTHER, "Ann Example", False))
        self.assertEqual(r.link, f"https://{fx.WORKSPACE}/archives/{fx.CHANNEL3}/p{m.ts.replace('.', '')}")
        self.assertEqual(r.reply_to, {"workspace": fx.WORKSPACE, "channel": fx.CHANNEL3, "thread_ts": m.ts})
        self.assertIsNone(r.thread)

    def test_a_reply_names_its_thread(self):
        parent = T0 - DAY
        reply = msg(T0, channel=fx.CHANNEL3, thread=parent)
        r = self.record(reply, {threadlist.MY_THREADS})
        self.assertEqual(r.thread.id, f"{fx.CHANNEL3}:{parent:.6f}")
        self.assertFalse(r.thread.first)
        self.assertEqual(r.reply_to["thread_ts"], f"{parent:.6f}")


if __name__ == "__main__":
    unittest.main()
