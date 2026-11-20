"""Reading the Slack connection's text answers (sources/slack_parse.py), ported
from Slack Man's watcher. Made-up ids only: the repo is public."""
from __future__ import annotations

import json
import unittest

from sources import slack_parse
from tests import slack_fixtures as fx
from tests.slack_fixtures import CHANNEL, ME, OTHER, SLACKBOT_DM


class SearchParseTest(unittest.TestCase):
    def test_reads_every_field(self):
        raw = fx.search_answer([
            fx.search_result(CHANNEL, "#made-up-pricing", OTHER, "Ann Example",
                             "1794225424.518499", f"first line\n\nsecond line with <@{ME}|Me>",
                             thread_ts="1793717797.607069"),
            fx.search_result("D000TEST05", "DM", "B000TEST01", "Build Bot", "1794225500.000100", "build failed",
                             bot=True),
        ])
        msgs, count = slack_parse.parse_search(raw, "x")
        self.assertEqual(count, 2)
        a, b = msgs
        self.assertEqual(a.channel_id, CHANNEL)
        self.assertEqual(a.channel_name, "#made-up-pricing")
        self.assertEqual(a.sender_id, OTHER)
        self.assertEqual(a.sender_name, "Ann Example")
        self.assertFalse(a.is_bot)
        self.assertEqual(a.ts, "1794225424.518499")
        self.assertEqual(a.thread_ts, "1793717797.607069")
        self.assertTrue(a.permalink.startswith("https://example.slack.com/archives/"))
        self.assertEqual(a.text, f"first line\n\nsecond line with <@{ME}|Me>")
        self.assertEqual(a.key, f"{CHANNEL}:1794225424.518499")
        self.assertEqual(a.source, "x")
        self.assertTrue(b.is_bot)
        self.assertEqual(b.thread_ts, "")

    def test_link_with_escaped_slashes_as_in_the_raw_answer(self):
        # The tool's JSON escapes "/" as "\/"; json.loads undoes it.
        raw = fx.search_answer([fx.msg(CHANNEL, "1794225424.518499")]).replace("/", "\\/")
        msgs, _ = slack_parse.parse_search(raw)
        self.assertEqual(slack_parse.workspace_from_link(msgs[0].permalink), "example.slack.com")

    def test_no_results(self):
        self.assertEqual(slack_parse.parse_search(fx.search_answer([])), ([], 0))

    def test_count_mismatch_is_an_error_not_a_guess(self):
        raw = fx.search_answer([fx.msg("D000TEST05", "1.1")])
        text = json.loads(raw)["results"].replace("(1 result)", "(2 results)")
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_search(json.dumps({"results": text}))

    def test_changed_layout_is_an_error(self):
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_search(json.dumps({"results": "## Messages (1 result)\n### Result 1 of 1\nsomething new"}))
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_search("not json")
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_search(json.dumps({"other": "x"}))


class ChannelParseTest(unittest.TestCase):
    def test_reads_slackbot_dm(self):
        raw = fx.channel_answer([fx.removed("1794313803.043919"), fx.added("1794290402.856859")])
        msgs, header = slack_parse.parse_channel(raw, SLACKBOT_DM)
        self.assertEqual(header, {"name": "DM", "id": SLACKBOT_DM})
        self.assertEqual([m.ts for m in msgs], ["1794313803.043919", "1794290402.856859"])
        self.assertTrue(all(m.sender_id == "USLACK" and m.is_bot for m in msgs))
        self.assertIn("removed from", msgs[0].text)
        events = slack_parse.membership_events(msgs)
        self.assertEqual([(e[1], e[2]) for e in events], [(fx.GROUP, True), (fx.GROUP, False)])

    def test_reading_by_user_id_gives_the_dm_id(self):
        # Read with channel_id "USLACK", the answer's first line names the DM's own id.
        msgs, header = slack_parse.parse_channel(fx.channel_answer([fx.added("1794290402.856859")]), "USLACK")
        self.assertEqual(header["id"], SLACKBOT_DM)
        self.assertEqual(msgs[0].channel_id, SLACKBOT_DM)

    def test_empty_channel(self):
        msgs, header = slack_parse.parse_channel(json.dumps({"messages": "Channel: DM (D000TEST05)\n\n"}),
                                                 "D000TEST05")
        self.assertEqual(msgs, [])
        self.assertEqual(header["id"], "D000TEST05")

    def test_changed_message_layout_is_an_error(self):
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_channel(json.dumps({"messages": "Channel: DM (D000TEST05)\n\n=== Message by x ==="}),
                                      "D000TEST05")


class ThreadParseTest(unittest.TestCase):
    FIRST = (OTHER, "Ann Example", "1794391396.694169", f"Can you look? <@{ME}|Me>")
    REPLIES = [(fx.THIRD, "Bob Example", "1794571828.582569", "first reply\nsecond line"),
               (OTHER, "Ann Example", "1794832360.872539", "thanks")]

    def test_reads_first_message_and_replies(self):
        msgs, cursor = slack_parse.parse_thread(fx.thread_answer(self.FIRST, self.REPLIES), CHANNEL, "t")
        self.assertEqual(cursor, "")
        self.assertEqual([m.ts for m in msgs], ["1794391396.694169", "1794571828.582569", "1794832360.872539"])
        self.assertTrue(all(m.thread_ts == "1794391396.694169" and m.channel_id == CHANNEL for m in msgs))
        self.assertEqual(msgs[0].sender_name, "Ann Example")
        self.assertEqual(msgs[0].text, f"Can you look? <@{ME}|Me>")  # "Reactions:" line dropped
        self.assertEqual(msgs[1].text, "first reply\nsecond line")
        self.assertEqual(msgs[2].sender_id, OTHER)

    def test_next_page_cursor(self):
        _, cursor = slack_parse.parse_thread(fx.thread_answer(self.FIRST, self.REPLIES, cursor="bmV4dA=="),
                                             CHANNEL)
        self.assertEqual(cursor, "bmV4dA==")

    def test_changed_layout_is_an_error(self):
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_thread(json.dumps({"messages": "something new"}), CHANNEL)
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_thread(json.dumps({"messages": "=== THREAD PARENT MESSAGE ===\nno fields\n"}), CHANNEL)


class ProfileAndLinksTest(unittest.TestCase):
    def test_profile(self):
        self.assertEqual(slack_parse.parse_profile(fx.profile_answer()),
                         {"id": ME, "name": "Me Example", "team": ""})

    def test_profile_without_id_is_an_error(self):
        with self.assertRaises(slack_parse.ParseError):
            slack_parse.parse_profile(json.dumps({"result": "Display Name: x"}))

    def test_workspace_and_links(self):
        self.assertEqual(slack_parse.workspace_from_link("https://example.slack.com/archives/C000TEST01/p1"),
                         "example.slack.com")
        self.assertEqual(slack_parse.workspace_from_link("https://example.com/x"), "")
        self.assertEqual(slack_parse.link_for("example.slack.com", CHANNEL, "1794391396.694169"),
                         f"https://example.slack.com/archives/{CHANNEL}/p1794391396694169")
        self.assertEqual(slack_parse.link_for("example.slack.com", CHANNEL, "1.2", "1.1"),
                         f"https://example.slack.com/archives/{CHANNEL}/p12?thread_ts=1.1&cid={CHANNEL}")
        self.assertEqual(slack_parse.link_for("", CHANNEL, "1.2"), "")


if __name__ == "__main__":
    unittest.main()
