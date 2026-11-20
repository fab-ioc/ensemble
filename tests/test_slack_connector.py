"""The Slack reader that goes through Claude's Slack connection
(sources/slack_connector.py): it only ever reads, and a run that is not exactly
what was planned fails. Ported from Slack Man's watcher (test_fetch, test_tools).
Made-up ids only: the repo is public."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from sources import slack_connector as sc
from sources.slack_connector import Query, ReadError
from tests import slack_fixtures as fx
from tests.slack_fixtures import CHANNEL, ME, PREFIX, SLACKBOT_DM

T0 = 1794300000.0


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def queries():
    return [
        Query("dms", "search", {"after": str(int(T0)), "filters": f"-from:<@{ME}>", "channel_types": "im,mpim",
                                "limit": 20, "response_format": "detailed"}),
        Query(f"channel:{CHANNEL}", "search", {"after": str(int(T0)), "filters": f"in:<#{CHANNEL}> -from:<@{ME}>",
                                                "limit": 20, "response_format": "detailed"}),
        Query("slackbot", "channel", {"channel_id": SLACKBOT_DM, "oldest": str(int(T0)), "limit": 20,
                                      "response_format": "detailed"}),
    ]


def empty_answers(qs):
    return {q.label: fx.channel_answer([]) if q.kind == "channel" else fx.search_answer([]) for q in qs}


class ReadStreamTest(unittest.TestCase):
    def test_good_run(self):
        qs = queries()
        answers = empty_answers(qs)
        answers["dms"] = fx.search_answer([fx.msg("D000TEST05", "1794299990.000100", "hi")])
        answers["slackbot"] = fx.channel_answer([fx.added("1794290402.856859")])
        r = sc.read_stream(fx.stream(qs, answers), qs, PREFIX)
        self.assertEqual([m.text for m in r.messages["dms"]], ["hi"])
        self.assertEqual(len(r.messages["slackbot"]), 1)
        self.assertEqual(r.facts["slackbot"]["id"], SLACKBOT_DM)
        self.assertEqual(r.full_pages, set())
        self.assertEqual(r.usage["cache_read"], 3720)
        self.assertEqual(r.usage["output"], 300)

    def test_calls_spread_over_several_replies_are_paired(self):
        # Haiku made the calls of one real run in several assistant events (2026-11-16).
        qs = queries()
        lines = fx.stream(qs, empty_answers(qs))
        first = json.loads(lines[1])
        content = first["message"]["content"]
        split = [json.dumps({"type": "assistant", "message": {"content": [c]}}) for c in content]
        r = sc.read_stream([lines[0]] + split + lines[2:], qs, PREFIX)
        self.assertEqual(set(r.messages), {q.label for q in qs})

    def test_full_page_is_marked(self):
        qs = queries()
        answers = empty_answers(qs)
        answers["dms"] = fx.search_answer([fx.msg("D000TEST05", f"17942999{i:02d}.000100") for i in range(20)])
        r = sc.read_stream(fx.stream(qs, answers), qs, PREFIX)
        self.assertEqual(r.full_pages, {"dms"})

    def test_missing_slack_tools_is_a_failure(self):
        qs = queries()
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(fx.stream(qs, empty_answers(qs), tools_available=[]), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "not_connected")
        self.assertIn("not available", str(ctx.exception))

    def test_write_tool_present_in_the_run_is_a_failure(self):
        qs = queries()
        present = [PREFIX + "slack_search_public_and_private", PREFIX + "slack_read_channel",
                   PREFIX + "slack_send_message"]
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(fx.stream(qs, empty_answers(qs), tools_available=present), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "write_tool")

    def test_call_with_changed_arguments_is_refused(self):
        qs = queries()
        lines = fx.stream(qs, empty_answers(qs))
        lines[1] = lines[1].replace('"in:<#', '"in:<#C000TEST09> in:<#', 1)
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(lines, qs, PREFIX)
        self.assertEqual(ctx.exception.code, "unplanned_call")

    def test_a_write_call_in_the_run_is_refused(self):
        qs = queries()
        lines = fx.stream(qs, empty_answers(qs),
                          extra_calls=[(PREFIX + "slack_send_message", {"channel_id": CHANNEL, "message": "hi"})])
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(lines, qs, PREFIX)
        self.assertIn("not asked for", str(ctx.exception))

    def test_call_without_answer_is_a_failure_and_keeps_the_usage(self):
        qs = queries()
        answers = empty_answers(qs)
        del answers["dms"]
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(fx.stream(qs, answers), qs, PREFIX)
        self.assertIn("dms", str(ctx.exception))
        self.assertEqual(ctx.exception.usage["output"], 300)

    def test_slack_error_is_a_failure(self):
        qs = queries()
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(fx.stream(qs, empty_answers(qs), errors={"dms"}), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "slack_error")

    def test_unreadable_answer_is_a_failure(self):
        qs = queries()
        answers = empty_answers(qs)
        answers["dms"] = json.dumps({"results": "## Messages (1 result)\n### Result 1 of 1\nsomething new"})
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(fx.stream(qs, answers), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "unreadable")

    def test_login_error_is_reported(self):
        lines = ['{"type": "result", "subtype": "success", "is_error": true, '
                 '"result": "Not logged in. Please run /login"}']
        with self.assertRaises(ReadError) as ctx:
            sc.read_stream(lines, queries(), PREFIX)
        self.assertEqual(ctx.exception.code, "not_signed_in")
        self.assertIn("Not logged in", str(ctx.exception))

    def test_prefix_is_taken_from_the_first_line(self):
        init = {"type": "system", "subtype": "init", "mcp_servers": [],
                "tools": ["mcp__my_slack__" + t for t in fx.ALL_TOOLS]}
        self.assertEqual(sc.find_prefix([json.dumps(init)]), "mcp__my_slack__")
        with self.assertRaises(ReadError) as ctx:
            sc.find_prefix([json.dumps(dict(init, tools=[]))])
        self.assertEqual(ctx.exception.code, "not_connected")
        two = dict(init, tools=["mcp__a__slack_search_public_and_private", "mcp__b__slack_search_public_and_private"])
        with self.assertRaises(ReadError):
            sc.find_prefix([json.dumps(two)])

    def test_run_environment_is_clean(self):
        env = sc.run_env()
        self.assertEqual(env["MCP_CONNECTION_NONBLOCKING"], "false")
        for name in ("CLAUDECODE", "ANTHROPIC_API_KEY", "ENSEMBLE_HOME", "CLAUDE_CODE_ENTRYPOINT"):
            self.assertNotIn(name, env)

    def test_a_login_in_another_config_folder_is_kept(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/tmp/made-up-claude-config"}):
            self.assertEqual(sc.run_env()["CLAUDE_CONFIG_DIR"], "/tmp/made-up-claude-config")


class OnlyReadsTest(unittest.TestCase):
    """The reader must never write to Slack."""

    def test_known_read_tools_are_not_write_tools(self):
        for name in sc.KNOWN_READ_ONLY:
            self.assertFalse(sc.looks_like_write_tool(name), name)
            self.assertNotIn(name, sc.WRITE_TOOLS)
        sc.check_allowed(sorted(sc.KNOWN_READ_ONLY))  # does not raise

    def test_check_refuses_write_and_unknown_tools(self):
        for name in sc.WRITE_TOOLS + ["slack_delete_message", "slack_new_thing"]:
            with self.assertRaises(ValueError):
                sc.check_allowed([sc.SEARCH, name])

    def test_every_write_tool_is_known_as_write(self):
        for name in sc.WRITE_TOOLS:
            self.assertTrue(sc.looks_like_write_tool(name), name)
        # Every Slack tool the connection has today is either read-only or refused by name.
        for name in fx.ALL_TOOLS:
            self.assertTrue(name in sc.KNOWN_READ_ONLY or name in sc.WRITE_TOOLS or name in sc.UNUSED_READ_TOOLS,
                            name)

    def test_command_allows_only_the_tools_the_reads_use(self):
        cmd = sc.build_command("fake-claude", PREFIX, queries())
        allowed = flag(cmd, "--allowedTools").split(",")
        denied = flag(cmd, "--disallowedTools").split(",")
        self.assertEqual(sorted(allowed), [PREFIX + "slack_read_channel", PREFIX + "slack_search_public_and_private"])
        for name in sc.WRITE_TOOLS:
            self.assertIn(PREFIX + name, denied)
        self.assertIn(PREFIX + "slack_read_thread", denied)  # read-only but not used by these reads
        self.assertEqual(set(allowed) & set(denied), set())

    def test_command_has_no_builtin_tools_and_only_the_slack_connection(self):
        cmd = sc.build_command("fake-claude", PREFIX, queries())
        self.assertEqual(flag(cmd, "--tools"), "")
        self.assertEqual(flag(cmd, "--permission-mode"), "dontAsk")
        self.assertEqual(flag(cmd, "--max-turns"), "1")
        self.assertEqual(flag(cmd, "--setting-sources"), "")
        self.assertEqual(flag(cmd, "--model"), "haiku")
        self.assertEqual(json.loads(flag(cmd, "--settings")),
                         {"allowedMcpServers": [{"serverUrl": "https://mcp.slack.com/*"}]})
        self.assertIn("--no-session-persistence", cmd)
        for bad in ("--dangerously-skip-permissions", "bypassPermissions", "--mcp-config"):
            self.assertNotIn(bad, cmd)
        prompt = cmd[-1]
        self.assertTrue(prompt.startswith("Make these 3 tool calls:"))
        self.assertIn(f'"filters":"in:<#{CHANNEL}> -from:<@{ME}>"', prompt)

    def test_probe_allows_no_tool(self):
        cmd = sc.build_probe_command("fake-claude")
        self.assertNotIn("--allowedTools", cmd)
        self.assertEqual(flag(cmd, "--tools"), "")
        self.assertEqual(flag(cmd, "--max-turns"), "1")

    def test_a_token_reader_is_not_built_yet(self):
        with self.assertRaises(ReadError) as ctx:
            sc.make_reader({"reader": "token"}, {})
        self.assertEqual(ctx.exception.code, "not_set_up")


class ConnectorReaderTest(unittest.TestCase):
    """The reader running a stand-in `claude` as a real program."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_then_read(self):
        path = fx.write_fake_claude(self.dir, {"by_tool": {
            "slack_search_public_and_private": fx.search_answer([fx.msg(CHANNEL, "1794299990.000100", "hello")]),
            "slack_read_channel": fx.channel_answer([])}})
        reader = sc.ConnectorReader(claude_path=path, cwd=os.path.join(self.dir, "run"))
        r = reader.read(queries())
        self.assertEqual(reader.prefix, PREFIX)
        self.assertEqual([m.text for m in r.messages[f"channel:{CHANNEL}"]], ["hello"])
        runs = fx.runs(self.dir)
        self.assertEqual(len(runs), 2)
        self.assertNotIn("--allowedTools", runs[0])  # the probe
        self.assertIn("--allowedTools", runs[1])
        self.assertEqual(reader.runs, 2)
        self.assertGreater(reader.usage["output"], 0)

    def test_known_prefix_skips_the_probe(self):
        path = fx.write_fake_claude(self.dir, {"by_tool": {
            "slack_search_public_and_private": fx.search_answer([]), "slack_read_channel": fx.channel_answer([])}})
        reader = sc.ConnectorReader(claude_path=path, cwd=os.path.join(self.dir, "run"), prefix=PREFIX)
        reader.read(queries())
        self.assertEqual(len(fx.runs(self.dir)), 1)

    def test_a_renamed_connection_is_learned(self):
        path = fx.write_fake_claude(self.dir, {"prefix": "mcp__renamed__", "by_tool": {
            "slack_search_public_and_private": fx.search_answer([]), "slack_read_channel": fx.channel_answer([])}})
        reader = sc.ConnectorReader(claude_path=path, cwd=os.path.join(self.dir, "run"), prefix=PREFIX)
        with self.assertRaises(ReadError) as ctx:
            reader.read(queries())
        self.assertEqual(ctx.exception.code, "not_connected")
        self.assertEqual(reader.prefix, "mcp__renamed__")
        reader.read(queries())  # the next read uses the new name

    def test_a_write_tool_offered_to_the_run_fails_it(self):
        path = fx.write_fake_claude(self.dir, {"extra_tools": ["slack_send_message"], "by_tool": {
            "slack_search_public_and_private": fx.search_answer([]), "slack_read_channel": fx.channel_answer([])}})
        reader = sc.ConnectorReader(claude_path=path, cwd=os.path.join(self.dir, "run"), prefix=PREFIX)
        with self.assertRaises(ReadError) as ctx:
            reader.read(queries())
        self.assertEqual(ctx.exception.code, "write_tool")


if __name__ == "__main__":
    unittest.main()
