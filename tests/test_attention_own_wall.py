"""Only the agent's own wall blocks it; the same words quoted do not.

On 2026-09-14 two healthy rooms read "needs you to log in again" for an hour:
a review finding quoted "OAuth token has expired · run /login to renew", the
owner read it through chat_read, and the hub typed a digest quoting the same
finding into the PO. The screens below are shaped like the live ones —
``ptyrun.tail()`` strips every line, so glyphs start lines and wrapped
continuations start bare, and spinner frames sit between blocks.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import attention  # noqa: E402

FINDING = ("4. **Medium — the login-notice exemption hides real authentication failures.** "
           "Thus each of “OAuth token has expired · run /login to renew”, “Not logged in · "
           "run /login to renew” now returns no block.")

# The owner reads the review through chat_read, then carries on.
CHAT_READ = "\n".join([
    "● Waiting for codex's verdict.",
    "✻Cooked for 12s · done 1:58 PM",
    "> [relay] codex sent you a message — read it with chat_read.",
    "● ensemble - chat_read (MCP)",
    '⎿  {"messages": [{"from": "codex", "text": "Review 1: changes requested.',
    FINDING,
    '"}]}',
    "✢Mulling…",
    "● Codex is right about the notice exemption; fixing it now.",
    "✻Cogitated for 40s · done 2:01 PM",
    ">",
])

# Same, but the turn ends on the tool result with nothing after it.
CHAT_READ_LAST = "\n".join(CHAT_READ.split("\n")[:7] + [">"])

# The hub types a progress digest into the PO, quoting the finding.
DIGEST = "\n".join([
    "● Noted, merging when the review is clean.",
    "✻Cooked for 5s · done 1:59 PM",
    "> [digest] Ensemble Dashboard: Docs (v2) — review 1 changes requested: "
    "“OAuth token has expired · run /login to renew” now returns no block — "
    "details with ensemble_list_tasks / ensemble_get_task.",
    "✶Mulling…",
    "● The docs task is fixing a review finding; nothing for you yet.",
    ">",
])
DIGEST_BARE = "[digest] Ensemble: OAuth token has expired · run /login to renew — details with ensemble_get_task."

# The agent's own last balloon discusses the phrase, and it is idle.
OWN_BALLOON = "\n".join([
    "> [report] blocked from task 'Docs' (room-cc9bd746, claude): says it needs a login",
    "● The docs task reported “OAuth token has expired · run /login to renew”, but its",
    "credentials are valid until tomorrow, so that report came from a quoted finding.",
    "✻Cooked for 9s · done 2:10 PM",
    ">",
])

# A task spec echoed as the first prompt (the screen this very task started on).
SPEC_ECHO = "\n".join([
    "● Skill(ensemble)",
    "⎿  Successfully loaded skill",
    "# Acceptance criteria",
    '-With a screen where "OAuth token has expired · run /login to renew" appears only inside a',
    "tool result and Claude's status is `idle`, the board shows nothing for that room.",
    "✢Schlepping…",
])

# Claude's own wall: the hub rang, the API call failed, the turn ended.
CLAUDE_WALL = "\n".join([
    "● Sent review 2 to codex.",
    "✻Cooked for 30s · done 1:40 PM",
    "> [relay] codex sent you a message — read it with chat_read.",
    "⎿  API Error: 401 · OAuth token has expired · run /login to renew",
    ">",
])

# The token expires mid-turn: the error follows a tool's output.
CLAUDE_WALL_MID_TURN = "\n".join([
    "● Bash(py -m unittest discover -s tests)",
    "⎿  Ran 384 tests in 41.2s",
    "OK (skipped=1)",
    "⎿  Credit balance is too low",
    ">",
])

CODEX_WALL = "\n".join([
    "• Ran py -m unittest tests.test_attention_login_notice",
    "└ OK",
    "■ You've hit your usage limit. Upgrade to Pro (https://openai.com/chatgpt/pricing) "
    "or try again at 3:32 PM.",
    "› Improve documentation in @filename",
])
CODEX_QUOTING = "\n".join([
    "• The spec says a real wall reads “You've hit your usage limit” and must still block.",
    "└ attention.py:125",
    "› Improve documentation in @filename",
])


def _ev(tail: str, status: str) -> dict:
    return {"ptyId": "p1", "alive": True, "tail": tail, "idleSeconds": 2,
            "lastSubmit": 0.0, "death": None, "scan": attention.analyse(tail),
            "claudeStatus": status}


def _classify(tail: str, status: str):
    part = {"identity": "claude", "agent": "claude"}
    room = {"status": "active", "mode": "", "participants": [part]}
    return attention._classify_agent(room, part, _ev(tail, status), 900, 0.0)


class QuotedWallsDoNotBlock(unittest.TestCase):
    def test_a_finding_read_through_chat_read(self):
        self.assertIsNone(attention.find_block(CHAT_READ))
        self.assertIsNone(_classify(CHAT_READ, "idle"))

    def test_a_tool_result_that_ends_the_turn(self):
        self.assertIsNone(attention.find_block(CHAT_READ_LAST))
        self.assertIsNone(_classify(CHAT_READ_LAST, "idle"))

    def test_a_digest_the_hub_typed(self):
        self.assertIsNone(attention.find_block(DIGEST))
        self.assertIsNone(_classify(DIGEST, "idle"))
        self.assertIsNone(attention.find_block(DIGEST_BARE))

    def test_the_agents_own_balloon_discussing_it(self):
        self.assertIsNone(attention.find_block(OWN_BALLOON))

    def test_a_quoted_spec(self):
        self.assertIsNone(attention.find_block(SPEC_ECHO))

    def test_codex_quoting_it(self):
        self.assertIsNone(attention.find_block(CODEX_QUOTING))

    def test_a_file_or_test_output_that_starts_with_the_phrase(self):
        # The tool's output is the phrase itself, and the turn ended on it.
        for tail in ("● Bash(type fixture.txt)\n⎿  OAuth token has expired · run /login to renew\n>",
                     "● Bash(py -m unittest)\n⎿  output: You've hit your usage limit\n>",
                     "●Bash(type fixture.txt)\n⎿  Running…\n⎿  Credit balance is too low\n>",
                     "● ensemble - chat_read (MCP)\n⎿  Invalid API key · Please run /login\n>",
                     "● Read(notes.md)\n✢Mulling…\n⎿  Not logged in · Please run /login\n>",
                     # Claude's live shell header, spaced and space-stripped (review 3).
                     "● Running 1 shell command…\n⎿  $ type fixture.txt\n"
                     "OAuth token has expired · run /login to renew\n>",
                     "●Running1shellcommand…\n⎿  $ type fixture.txt\n"
                     "OAuth token has expired · run /login to renew\n>",
                     "●RunningNshellcommand · 3s…\n⎿  Credit balance is too low\n>".replace("N", "1"),
                     "● Calling ensemble…\n⎿  Invalid API key · Please run /login\n>",
                     "●Searching for 2 patterns, reading 1 file\n⎿  Not logged in · run /login\n>",
                     # A plain-sentence label over tool work, as live screens show.
                     "● Checking where the new test runs execute\n⎿  $ type fixture.txt\n"
                     "You've hit your usage limit\n>",
                     "● Reading the review\n⎿  [from codex] OAuth token has expired · run /login to renew\n>"):
            self.assertIsNone(attention.find_block(tail), tail)
            self.assertIsNone(_classify(tail, "idle"), tail)

    def test_a_busy_claude_is_never_blocked_by_its_screen(self):
        # Even a screen that would block when idle.
        self.assertIsNotNone(attention.find_block(CLAUDE_WALL))
        self.assertIsNone(_classify(CLAUDE_WALL, "busy"))


class OwnWallsStillBlock(unittest.TestCase):
    def test_claude_prints_it_in_its_own_error_line(self):
        hit = attention.find_block(CLAUDE_WALL)
        self.assertEqual(hit, ("needs you to log in again", "auth",
                               "⎿ API Error: 401 · OAuth token has expired · run /login to renew"))
        state, reason, extra = _classify(CLAUDE_WALL, "idle")
        self.assertEqual(state, "blocked")
        self.assertEqual(extra["cause"], "auth")
        self.assertEqual(extra["quote"], hit[2])

    def test_the_same_quote_as_before_this_rule(self):
        # The bare line, which the old whole-screen scan also saw.
        line = CLAUDE_WALL.split("\n")[3]
        self.assertEqual(attention.find_block(CLAUDE_WALL), attention.find_block(line))

    def test_mid_turn_after_a_tool_result(self):
        hit = attention.find_block(CLAUDE_WALL_MID_TURN)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "credit")

    def test_codex_prints_it(self):
        hit = attention.find_block(CODEX_WALL)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[:2], ("hit its usage limit", "usage_limit"))
        self.assertTrue(hit[2].startswith("You've hit your usage limit."), hit[2])
        # Codex publishes no status.
        self.assertEqual(_classify(CODEX_WALL, "")[0], "blocked")

    def test_a_wall_drawn_while_the_status_is_a_second_behind(self):
        ev = _ev(CLAUDE_WALL, "busy")
        part = {"identity": "claude", "agent": "claude"}
        room = {"status": "active", "mode": "", "participants": [part]}
        self.assertIsNone(attention._classify_agent(room, part, ev, 900, 0.0))
        ev["claudeStatus"] = "idle"          # the next poll, same cached scan
        self.assertEqual(attention._classify_agent(room, part, ev, 900, 0.0)[0], "blocked")

    def test_after_claudes_own_words_mid_turn(self):
        for tail in ("● Checking the review now.\n⎿  API Error: 401 · OAuth token has expired · run /login\n>",
                     # A sentence shaped like a tool call is still a sentence (review 3).
                     "● Note(this is Claude prose)\n⎿ API Error: 401 · OAuth token has expired · run /login\n>",
                     "●Note(thisisClaudeprose)\n⎿ API Error: 401 · OAuth token has expired · run /login\n>"):
            hit = attention.find_block(tail)
            self.assertIsNotNone(hit, tail)
            self.assertEqual(hit[1], "auth", tail)
            self.assertEqual(_classify(tail, "idle")[0], "blocked", tail)

    def test_a_background_notice_after_the_wall_is_not_recovery(self):
        for notice in ("● Background task completed",
                       '●Backgroundcommand"Run the test suites"completed(exitcode0)'):
            tail = CLAUDE_WALL.rstrip(">").rstrip("\n") + "\n" + notice + "\n>"
            hit, alone = attention.find_block(tail), attention.find_block(CLAUDE_WALL)
            self.assertIsNotNone(hit, notice)
            # The quote itself is out of scope here: `_quote_at` reads on over
            # a `●` the way it always has.
            self.assertEqual(hit[:2], alone[:2], notice)
            self.assertTrue(hit[2].startswith(alone[2]), hit[2])
            self.assertEqual(_classify(tail, "idle")[0], "blocked")

    def test_a_wall_the_agent_got_past_is_history(self):
        tail = CLAUDE_WALL + "\n> /login\n● Logged in again; carrying on with review 2."
        self.assertIsNone(attention.find_block(tail))


class LoginNoticeStillExempt(unittest.TestCase):
    def test_the_notice_in_claudes_status_area(self):
        tail = "● Done.\n✻Cooked for 3s\n⚠ Your login expires in 3 days · run /login to renew\n>"
        self.assertIsNone(attention.find_block(tail))
        self.assertIsNone(attention.find_block("⎿  Your login expires in 3 days · run /login to renew"))


if __name__ == "__main__":
    unittest.main()
