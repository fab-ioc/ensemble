"""The repo is public: the Slack source, its tests, its fixtures and the test hub
script hold no real Slack id, workspace, mail address or home folder. Made-up
ids have the shape U000TEST01 (a letter, then 000, then TEST or PLAN); the only
workspace is example.slack.com and the only mail domain example.com.

Also reads the two saved real runs (tests/fixtures/slack/), whose answers were
replaced by made-up ones but whose order of events is as Claude wrote it."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from sources import slack_connector as sc
from sources.slack_connector import Query

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "slack"


def files() -> list[Path]:
    out = sorted((ROOT / "sources").glob("slack*.py"))
    out += sorted((ROOT / "tests").glob("test_slack_*.py"))
    out += [ROOT / "tests" / "slack_fixtures.py", ROOT / "tests" / "outside_test_hub.py",
            ROOT / "tests" / "test_outside_test_hub.py"]
    out += sorted(FIXTURES.glob("*"))
    return [p for p in out if p.is_file()]


# Any Slack-shaped id, old workspaces too (U2..., C1...): a capital U C D G S T B W
# or E, then 8 or more capitals and digits with at least one digit among them.
SLACK_ID = re.compile(r"\b[UCDGSTBWE](?=[A-Z0-9]*[0-9])[A-Z0-9]{8,}\b")
MADE_UP = re.compile(r"[A-Z]000TEST[0-9A-Z]*")
WORKSPACE = re.compile(r"\b([a-z0-9-]+)\.slack\.com\b")
MAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[a-z]{2,})\b")
HOME = re.compile(r"/Users/(?!<)[A-Za-z0-9._-]+")


class PublicRepoTest(unittest.TestCase):
    def test_files_are_found(self):
        names = {p.name for p in files()}
        for name in ("slack.py", "slack_connector.py", "slack_parse.py", "slack_threads.py", "slack_fixtures.py",
                     "run1.jsonl", "test_slack_public.py"):
            self.assertIn(name, names)

    def test_no_real_ids_workspaces_mail_or_home(self):
        found = []
        for path in files():
            text = path.read_text(encoding="utf-8")
            for m in SLACK_ID.finditer(text):
                if not MADE_UP.fullmatch(m.group(0)):
                    found.append(f"{path.name}: id {m.group(0)}")
            for m in WORKSPACE.finditer(text):
                if m.group(1) != "example" and m.group(0) != "mcp.slack.com":
                    found.append(f"{path.name}: workspace {m.group(0)}")
            for m in MAIL.finditer(text):
                if m.group(1) != "example.com":
                    found.append(f"{path.name}: mail address domain {m.group(1)}")
            for m in HOME.finditer(text):
                found.append(f"{path.name}: home folder {m.group(0)}")
        self.assertEqual(found, [])

    def test_the_scan_finds_what_it_is_for(self):
        # Put together from pieces, so this file does not hold what it looks for.
        sample = " ".join(["U05" + "AAAA1BCD", "in:<#C09" + "ZZZZ9XYZ>", "https://acme" + ".slack.com/x",
                           "a@" + "acme.io", "/Us" + "ers/someone", "U2" + "ABCDEFG", "C1" + "2345678AB",
                           "SLACKBOT_DM", "DEFAULTSX"])
        # Two new-style ids and two from old workspaces; names in capitals are not ids.
        self.assertEqual(len(SLACK_ID.findall(sample)), 4)
        self.assertEqual(WORKSPACE.findall(sample), ["acme"])
        self.assertEqual(MAIL.findall(sample), ["acme.io"])
        self.assertTrue(HOME.search(sample))
        self.assertTrue(MADE_UP.fullmatch("U000TEST01"))


class SavedRunsTest(unittest.TestCase):
    """Two real runs (2026-11-16), answers made up: the calls came in several
    replies, one with a rate-limit line between results, and one search failed."""

    def lines(self, name):
        return (FIXTURES / name).read_text(encoding="utf-8").splitlines()

    def planned(self, name):
        out = []
        for line in self.lines(name):
            e = json.loads(line)
            if e["type"] == "assistant":
                for c in e["message"]["content"]:
                    if c["type"] == "tool_use":
                        short = sc.short_name(c["name"])
                        kind = {v: k for k, v in sc.TOOL_FOR.items()}[short]
                        out.append(Query(f"q{len(out)}", kind, c["input"]))
        return out

    def test_run_with_a_thread_and_a_channel_read(self):
        qs = self.planned("run2.jsonl")
        r = sc.read_stream(self.lines("run2.jsonl"), qs, "mcp__claude_ai_Slack__")
        self.assertEqual(len(r.messages["q0"]), 4)
        self.assertEqual(r.facts["q0"]["cursor"], "bWFkZS11cA==")
        # oldest = latest = the message's ts: both ends left out, nothing comes back.
        self.assertEqual(r.messages["q1"], [])

    def test_run_where_a_search_failed(self):
        qs = self.planned("run1.jsonl")
        with self.assertRaises(sc.ReadError) as ctx:
            sc.read_stream(self.lines("run1.jsonl"), qs, "mcp__claude_ai_Slack__")
        self.assertEqual(ctx.exception.code, "slack_error")
        self.assertIn("parameter_validation_failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
