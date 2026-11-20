"""A whole Slack check (sources/slack.py SlackSource): who the user is found on the
first check, the reads, the records handed to the frame, read positions, the
first message of a thread, failures, a whole thread, and a run of a stand-in
`claude` program that may only read. Made-up ids only: the repo is public."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from sources import TYPES, catalog
from sources import slack as slack_source
from sources import slack_threads as threadlist
from sources.base import (FAILED, NOT_SET_UP, OK, WHY_CHANNEL, WHY_DM, WHY_MENTION, WHY_THREAD, Message,
                          NotSupported, Watch)
from sources.slack import SlackSource
from tests import slack_fixtures as fx
from tests.slack_fixtures import CHANNEL, CHANNEL2, DM, ME, OTHER, SLACKBOT_DM, WORKSPACE

NOW = 1794400000.0
IDENTITY = {"userId": ME, "name": "Me Example", "workspace": WORKSPACE, "slackbotDm": SLACKBOT_DM}


def answers(by_label=None, default_search=None):
    """An answer function for FakeReader: by query label, else empty."""
    by_label = by_label or {}

    def answer(q):
        if q.label in by_label:
            a = by_label[q.label]
            return a(q) if callable(a) else a
        if q.kind == "profile":
            return fx.profile_answer()
        if q.label == "slackbot-dm":
            return fx.channel_answer([fx.added("1794300000.000100")])
        if q.label == "workspace":
            return fx.search_answer([fx.msg(CHANNEL2, "1794390000.000100", thread_ts="1794380000.000100")])
        if q.kind == "channel":
            return fx.channel_answer([])
        if q.kind == "thread":
            return fx.thread_answer((OTHER, "Ann Example", q.args["message_ts"], "first"), [])
        return default_search or fx.search_answer([])
    return answer


def source(answer):
    reader = fx.FakeReader(answer)
    return SlackSource(reader_factory=lambda settings, memory: reader), reader


class FirstCheckTest(unittest.TestCase):
    def test_finds_who_the_user_is_then_reads(self):
        src, reader = source(answers({"dms": fx.search_answer([fx.msg(DM, "1794399990.000100", "hi there",
                                                                       channel_name="DM")])}))
        check = src.check(Watch(settings={"groups": [fx.GROUP]}), {}, NOW)
        self.assertEqual(check.outcome, OK, check.reason)
        self.assertEqual([q.label for q in reader.batches[0]], ["profile", "slackbot-dm", "workspace"])
        self.assertEqual(check.memory["identity"], {"userId": ME, "name": "Me Example", "workspace": WORKSPACE,
                                                    "slackbotDm": SLACKBOT_DM})
        main = {q.label: q for q in reader.batches[1]}
        self.assertIn("dms", main)
        self.assertIn(f"mention:{ME}", main)
        self.assertIn(f"group:{fx.GROUP}", main)
        self.assertIn("slackbot", main)
        # Nothing older than the first check: every search starts now ...
        self.assertEqual(main["dms"].args["after"], str(int(NOW)))
        # ... except the one-time look back over threads, and the group membership.
        self.assertEqual(main[threadlist.SEED].args["after"], str(int(NOW - 14 * 86400)))
        self.assertEqual(main["slackbot"].args["oldest"], str(int(NOW - slack_source.MEMBERSHIP_LOOKBACK_SECONDS)))
        self.assertEqual(main["slackbot"].args["channel_id"], SLACKBOT_DM)
        [m] = check.messages
        self.assertIsInstance(m, Message)
        self.assertEqual(m.key, f"slack:{WORKSPACE}:{DM}:1794399990.000100")
        self.assertEqual(m.why_seen, WHY_DM)
        self.assertTrue(m.addressed)
        self.assertEqual(m.place.kind, "dm")
        self.assertEqual(m.author.name, "Ann Example")
        self.assertEqual(m.reply_to, {"workspace": WORKSPACE, "channel": DM, "thread_ts": "1794399990.000100"})
        self.assertTrue(check.memory["threads_seeded"])
        self.assertEqual(check.memory["toolPrefix"], fx.PREFIX)
        self.assertEqual(check.runs, 2)

    def test_no_slackbot_read_without_member_only_groups(self):
        src, reader = source(answers())
        check = src.check(Watch(settings={}), {}, NOW)
        self.assertEqual(check.outcome, OK, check.reason)
        self.assertEqual([q.label for q in reader.batches[0]], ["profile", "workspace"])
        self.assertNotIn("slackbot", [q.label for q in reader.batches[1]])

    def test_workspace_not_found_fails_and_keeps_nothing(self):
        src, _ = source(answers({"workspace": fx.search_answer([])}))
        check = src.check(Watch(settings={}), {}, NOW)
        self.assertEqual(check.outcome, FAILED)
        self.assertIn("workspace", check.reason)
        self.assertIsNone(check.memory)


class LaterCheckTest(unittest.TestCase):
    def memory(self, **extra):
        return dict({"identity": dict(IDENTITY), "threads_seeded": True}, **extra)

    def test_reads_from_the_positions_and_reports_new_ones(self):
        src, reader = source(answers({
            f"mention:{ME}": fx.search_answer([fx.msg(CHANNEL, "1794399000.000100", f"<@{ME}> look")]),
            f"channel:{CHANNEL2}": fx.search_answer([fx.msg(CHANNEL2, "1794399100.000100", "news")]),
        }))
        positions = {"dms": NOW - 600, f"mention:{ME}": NOW - 700}
        check = src.check(Watch(settings={"channels": [CHANNEL2]}, memory=self.memory()), positions, NOW)
        self.assertEqual(check.outcome, OK, check.reason)
        self.assertEqual(len(reader.batches), 1)  # who the user is is known: one run
        main = {q.label: q for q in reader.batches[0]}
        self.assertEqual(main["dms"].args["after"], str(int(NOW - 600)))
        self.assertEqual(main[f"mention:{ME}"].args["after"], str(int(NOW - 700)))
        self.assertEqual(main[f"channel:{CHANNEL2}"].args["after"], str(int(NOW)))  # new read: starts now
        self.assertNotIn(threadlist.SEED, main)
        self.assertEqual([m.why_seen for m in check.messages], [WHY_MENTION, WHY_CHANNEL])
        self.assertEqual([m.addressed for m in check.messages], [True, False])
        self.assertEqual(check.full, set())
        self.assertEqual(check.positions["dms"], NOW)
        # The thread of the mention is followed from now on, and its channel searched from the mention.
        self.assertTrue(check.memory["threads"][f"{CHANNEL}:1794399000.000100"]["mentioned"])
        self.assertEqual(check.positions[threadlist.MENTIONED_THREADS], 1794399000 - 1)

    def test_full_page_reports_its_newest_message(self):
        page = [fx.msg(CHANNEL, f"17943990{i:02d}.000100", f"m{i}") for i in range(20)]
        src, _ = source(answers({f"channel:{CHANNEL}": fx.search_answer(page)}))
        check = src.check(Watch(settings={"channels": [CHANNEL]}, memory=self.memory()), {}, NOW)
        self.assertEqual(check.full, {f"channel:{CHANNEL}"})
        self.assertTrue(check.more)
        self.assertEqual(check.positions[f"channel:{CHANNEL}"], 1794399019.0)

    def test_a_message_found_twice_is_handed_on_once(self):
        both = fx.search_answer([fx.msg(DM, "1794399000.000100", f"<@{ME}> hi")])
        src, _ = source(answers({"dms": both, f"mention:{ME}": both}))
        check = src.check(Watch(settings={}, memory=self.memory()), {}, NOW)
        self.assertEqual(len(check.messages), 1)
        self.assertEqual(check.messages[0].why_seen, WHY_DM)

    def test_own_messages_and_bot_dms_are_left_out(self):
        src, _ = source(answers({"dms": fx.search_answer([
            fx.msg(DM, "1794399000.000100", "mine", sender=ME),
            fx.msg(DM, "1794399001.000100", "bot", sender="B000TEST01", bot=True)])}))
        check = src.check(Watch(settings={}, memory=self.memory()), {}, NOW)
        self.assertEqual(check.messages, [])

    def test_reply_gets_its_threads_first_message(self):
        reply = fx.msg(CHANNEL, "1794399500.000100", "looks good", thread_ts="1794390000.000100")
        first = ("U000TEST03", "Bob Example <bob@example.com>", "1794390000.000100",
                 f"PR for <@{ME}|Me>: made-up-repo 1\nReactions: tada (1)")
        src, reader = source(answers({
            threadlist.MY_THREADS: fx.search_answer([reply]),
            f"{threadlist.FIRST_PREFIX}{CHANNEL}:1794390000.000100": fx.channel_answer([first], dm_id=CHANNEL,
                                                                                       header="#made-up"),
        }))
        check = src.check(Watch(settings={}, memory=self.memory()), {}, NOW)
        self.assertEqual(check.outcome, OK, check.reason)
        self.assertEqual(len(reader.batches), 2)
        [q] = reader.batches[1]
        self.assertEqual(q.args["oldest"], "1794390000.000099")
        self.assertEqual(q.args["latest"], "1794390000.000101")
        [m] = check.messages
        self.assertEqual(m.why_seen, WHY_THREAD)
        self.assertEqual(m.thread.id, f"{CHANNEL}:1794390000.000100")
        self.assertFalse(m.thread.first)
        self.assertEqual(m.thread.head, f"PR for <@{ME}|Me>: made-up-repo 1")
        entry = check.memory["threads"][f"{CHANNEL}:1794390000.000100"]
        self.assertEqual(entry["first_by"], "Bob Example")
        self.assertTrue(entry["written"] and entry["mentioned"])

    def test_first_message_read_failing_still_hands_on_the_reply(self):
        reply = fx.msg(CHANNEL, "1794399500.000100", "looks good", thread_ts="1794390000.000100")
        label = f"{threadlist.FIRST_PREFIX}{CHANNEL}:1794390000.000100"
        src, _ = source(answers({threadlist.MY_THREADS: fx.search_answer([reply]),
                                 label: json.dumps({"messages": "=== Message in a new layout"})}))
        check = src.check(Watch(settings={}, memory=self.memory()), {}, NOW)
        self.assertEqual(check.outcome, OK, check.reason)
        self.assertEqual(len(check.messages), 1)
        self.assertEqual(check.messages[0].thread.head, "")
        self.assertTrue(any("first message" in n for n in check.notes))

    def test_channels_over_the_limit_are_named_in_the_notes(self):
        threads = {f"C000TEST{i:02d}:1794390000.000100": {"channel": f"C000TEST{i:02d}", "ts": "1794390000.000100",
                                                          "mentioned": True, "written": False,
                                                          "last": NOW - 60 - i}
                   for i in range(10, 13)}
        src, reader = source(answers())
        check = src.check(Watch(settings={"maxThreadChannels": 2}, memory=self.memory(threads=threads)), {}, NOW)
        self.assertEqual(check.memory["channelsLeftOut"], 1)
        self.assertTrue(any("1 channels" in n for n in check.notes))
        q = next(q for q in reader.batches[0] if q.label == threadlist.MENTIONED_THREADS)
        self.assertEqual(q.args["filters"], f"is:thread in:<#C000TEST10> in:<#C000TEST11> -from:<@{ME}>")

    def test_a_failed_read_fails_the_check_and_keeps_nothing(self):
        def broken(q):
            return json.dumps({"results": "## Messages (1 result)\n### Result 1 of 1\nsomething new"})
        src, _ = source(answers({"dms": broken}))
        check = src.check(Watch(settings={}, memory=self.memory()), {"dms": NOW - 60}, NOW)
        self.assertEqual(check.outcome, FAILED)
        self.assertEqual(check.code, "unreadable")
        self.assertIn("Slack could not be read", check.reason)
        self.assertIsNone(check.memory)
        self.assertEqual(check.positions, {})
        self.assertEqual(check.messages, [])


class SetUpTest(unittest.TestCase):
    def test_problems(self):
        src = SlackSource(reader_factory=lambda s, m: None)
        self.assertEqual(src.problem({}), "")
        self.assertIn("token", src.problem({"reader": "token"}))
        self.assertIn("group ids", src.problem({"groups": ["not-a-group"]}))
        self.assertIn("channel ids", src.problem({"channels": ["#general"]}))
        self.assertIn("Nothing is chosen", src.problem({"dms": False, "mentions": False, "threads": False}))
        check = src.check(Watch(settings={"channels": ["in:<#C1> OR x"]}), {}, NOW)
        self.assertEqual(check.outcome, NOT_SET_UP)

    def test_listed_with_its_fields(self):
        self.assertIs(TYPES["slack"], SlackSource)
        slack = next(c for c in catalog() if c["kind"] == "slack")
        keys = [f["key"] for f in slack["fields"]]
        for k in ("dms", "mentions", "groups", "groupsAlways", "threads", "threadDays", "maxThreadChannels",
                  "channels", "userId", "workspace", "slackbotDm"):
            self.assertIn(k, keys)
        # The topic is every row's, whatever the source (round 2 step 1): Slack adds only its places.
        self.assertEqual([f["key"] for f in slack["routeFields"]], ["channels"])
        self.assertFalse(slack["canSend"])

    def test_plan_names_the_reads(self):
        names = SlackSource().plan(Watch(settings={"groups": [fx.GROUP], "channels": [CHANNEL]}), {})
        self.assertEqual(names[:3], ["profile", "slackbot-dm", "workspace"])
        self.assertIn("mention:me", names)
        self.assertIn(f"channel:{CHANNEL}", names)

    def test_never_sends(self):
        with self.assertRaises(NotSupported):
            SlackSource().send({"channel": CHANNEL}, "hi")


class WholeThreadTest(unittest.TestCase):
    def test_reads_every_page_once(self):
        ref = f"{CHANNEL}:1794390000.000100"
        first = (OTHER, "Ann Example", "1794390000.000100", "start")

        def answer(q):
            if q.args.get("cursor") == "page2":
                return fx.thread_answer(first, [(ME, "Me Example", "1794390300.000100", "three")])
            return fx.thread_answer(first, [(OTHER, "Ann Example", "1794390100.000100", "one"),
                                            ("U000TEST03", "Bob Example", "1794390200.000100", "two")],
                                    cursor="page2")
        reader = fx.FakeReader(answer)
        src = SlackSource(reader_factory=lambda s, m: reader, memory={"identity": dict(IDENTITY)})
        msgs = src.thread(ref)
        self.assertEqual([m.text for m in msgs], ["start", "one", "two", "three"])
        self.assertTrue(all(m.thread.id == ref for m in msgs))
        self.assertTrue(msgs[0].thread.first)
        self.assertEqual(msgs[1].thread.head, "start")
        self.assertTrue(msgs[3].author.me)
        self.assertEqual(msgs[1].key, f"slack:{WORKSPACE}:{CHANNEL}:1794390100.000100")
        self.assertEqual(len(reader.batches), 2)
        self.assertEqual(reader.batches[0][0].kind, "thread")

    def test_after_a_restart_the_kept_memory_is_used(self):
        """A new object (a hub restart) is handed what the last good check
        kept before any check: the keys carry the workspace, the user is
        known, and the reader is made with the kept memory and settings."""
        ref = f"{CHANNEL}:1794390000.000100"
        first = (OTHER, "Ann Example", "1794390000.000100", "start")
        made = []

        def factory(settings, memory):
            made.append((settings, memory))
            return fx.FakeReader(lambda q: fx.thread_answer(first, [(ME, "Me Example", "1794390100.000100", "mine")]))
        src = SlackSource(reader_factory=factory)
        src.remember({"channels": [CHANNEL]}, {"identity": dict(IDENTITY), "toolPrefix": "mcp__x__"})
        msgs = src.thread(ref)
        self.assertEqual(msgs[1].key, f"slack:{WORKSPACE}:{CHANNEL}:1794390100.000100")
        self.assertTrue(msgs[1].author.me)
        self.assertEqual(made[0][1]["toolPrefix"], "mcp__x__")
        self.assertEqual(made[0][0]["channels"], [CHANNEL])
        self.assertEqual(src.found(), IDENTITY)

    def test_refuses_what_is_not_a_thread(self):
        with self.assertRaises(ValueError):
            SlackSource(reader_factory=lambda s, m: None).thread("in:<#C1> x")


class StandInClaudeTest(unittest.TestCase):
    """A check that runs a stand-in `claude` program: every run only reads."""

    def test_check_runs_only_read_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fx.write_fake_claude(tmp, {
                "by_match": [
                    ['"channel_id":"USLACK"'.replace(":", ": "), fx.channel_answer([fx.added("1794300000.000100")])],
                    ['"filters": "is:thread"', fx.search_answer([fx.msg(CHANNEL2, "1794390000.000100",
                                                                         thread_ts="1794380000.000100")])],
                    ['"filters": "-from:<@' + ME + '>", "channel_types"',
                     fx.search_answer([fx.msg(DM, "1794399990.000100", "hi")])],
                ],
                "by_tool": {"slack_read_user_profile": fx.profile_answer(),
                            "slack_search_public_and_private": fx.search_answer([]),
                            "slack_read_channel": fx.channel_answer([])}})
            src = SlackSource(claude_path=path, cwd=os.path.join(tmp, "run"))
            check = src.check(Watch(settings={"groups": [fx.GROUP]}), {}, NOW)
            self.assertEqual(check.outcome, OK, check.reason)
            self.assertEqual([m.text for m in check.messages], ["hi"])
            runs = fx.runs(tmp)
            self.assertEqual(len(runs), 3)  # the probe, who the user is, the reads
            self.assertNotIn("--allowedTools", runs[0])
            from sources.slack_connector import WRITE_TOOLS
            for argv in runs[1:]:
                allowed = argv[argv.index("--allowedTools") + 1].split(",")
                denied = argv[argv.index("--disallowedTools") + 1].split(",")
                for tool in allowed:
                    self.assertNotIn(tool.rsplit("__", 1)[-1], WRITE_TOOLS)
                for tool in WRITE_TOOLS:
                    self.assertIn(fx.PREFIX + tool, denied)
                self.assertEqual(argv[argv.index("--max-turns") + 1], "1")


if __name__ == "__main__":
    unittest.main()
