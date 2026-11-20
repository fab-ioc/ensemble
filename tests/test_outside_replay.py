"""The replay tool (outside_replay.py): reading a watcher's notices file and
a dispatch log, matching each message with its log line, routing the
notices through the channel table, thread memory and saved model answers
(no real Claude starts), comparing the two and writing the report. The
notices, the log and every id are made up (the repo is public)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import outside
import outside_model as model
import outside_replay as rp
import outside_route as route
import sources
from sources.fake import FakeSource

INBOX = """# Made-up notices

## 2026-11-18 09:00:00 (reported as task #1, PO woken)

[slack] 1 new Slack message (08:55-09:00)

1. **#made-up-pricing** (`C000TEST01`) · Pat Example (`U000TEST02`) · 2026-11-18 08:58:10 CET
   starts a thread with 1 replies, message `1795000000.000100`
   https://example.slack.com/archives/C000TEST01/p1795000000000100
   > the made-up spread looks wide

## 2026-11-18 09:10:00 (reported as task #1, PO woken)

[slack] 3 new Slack messages (09:00-09:10)

1. **#made-up-pricing** (`C000TEST01`) · Sam Sample (`U000TEST03`) · 2026-11-18 09:05:40 CET
   in a thread (thread `1795000000.000100`), message `1795000400.000200`
   first message of the thread (Pat): the made-up spread looks wide
   https://example.slack.com/archives/C000TEST01/p1795000400000200?thread_ts=1795000000.000100
   > agreed, looking at it

2. **DM** (`D000TEST01`) · Pat Example (`U000TEST02`) · 2026-11-18 09:06:00 CET
   message `1795000460.000300`
   Robin in thread: **yes** (a DM or group DM with Robin)
   https://example.slack.com/archives/D000TEST01/p1795000460000300
   > thanks!

3. **#made-up-stress** (`C000TEST04`) · Kim Test (`U000TEST04`) · 2026-11-18 09:07:30 CET
   message `1795000550.000400`
   https://example.slack.com/archives/C000TEST04/p1795000550000400
   > the made-up run is tonight

## 2026-11-18 09:30:00 (reported as task #1, PO woken)

[slack] 3 new Slack messages (09:10-09:30)

1. **DM** (`D000TEST02`) · Lee Nobody (`U000TEST05`) · 2026-11-18 09:20:00 CET
   message `1795001200.000500`
   https://example.slack.com/archives/D000TEST02/p1795001200000500
   > what time is the made-up lunch

2. **Group DM** (`C000TEST05`) · Jo Unknown (`U000TEST06`) · 2026-11-18 09:25:00 CET
   message `1795001500.000600`
   https://example.slack.com/archives/C000TEST05/p1795001500000600
   > a made-up question about pricing

3. **DM** (`D000TEST03`) · Max Missing (`U000TEST07`) · 2026-11-18 09:26:00 CET
   message `1795001560.000700`
   https://example.slack.com/archives/D000TEST03/p1795001560000700
   > hi

## 2026-11-18 09:40:00 (reported as task #1, PO woken)

[slack] a notice with no message in the expected form
"""

LOG = """# Made-up dispatch log

## 2026-11-18

- 08:58 · Pat Example, #made-up-pricing: the spread looks wide → **Old Alpha** (info).
- 09:05 · Sam Sample, #made-up-pricing (thread): looking at it → **Alpha** (`question`) and **Beta** (`info`).
- 09:06 · Pat Example, DM: thanks → not sent (small talk).
- 09:07 · Kim Test, #made-up-stress: the run is tonight → **Gamma** (`question`) and **Beta** (`info`).
- 09:20 · Lee Nobody, DM: lunch → no initiative: **card to Robin** (12:00).
- 09:25 · Jo Unknown, group DM: a question → left for later.
- 09:30 · (re 09:06) Pat Example, DM: on a second look it is about Beta → **Beta**.
- 09:31 · Correction: the 09:05 line was about Alpha only → **Alpha**.
- 11:00 · Nobody Here, DM: a message the watcher missed → **Alpha**.
- 11:05 · Ray Late, #made-up-alerts, on-call group: an alert → **missed by the watcher**; ask card to Robin 11:20.
"""

TABLE = {"initiatives": [
    {"name": "Alpha", "topic": "made-up pricing and spreads", "channels": []},
    {"name": "Beta", "topic": "made-up stress runs", "channels": ["made-up-stress"]},
    {"name": "Gamma", "topic": "made-up releases", "channels": []}],
    "aliases": {"Old Alpha": "Alpha"}}

# Saved answers, one per model call, in the order the replay asks.
FIRST = json.dumps([{"id": "m1", "to": "Alpha", "why": "about made-up spreads"}])
SECOND = json.dumps([{"id": "m1", "to": "small talk", "why": "a thank you"}])
THIRD = json.dumps([{"id": "m1", "to": "no match", "why": "about lunch"},
                    {"id": "m2", "to": "Alpha", "why": "a pricing question"},
                    {"id": "m3", "to": "small talk", "why": "a greeting"}])


class Saved:
    """Plays back saved answers in order and keeps every prompt."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, timeout: float) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError("the model was asked more often than the test expected")
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


def _isolated(test: unittest.TestCase) -> None:
    """The replay binds the routing modules to its own scratch hub: put them
    back afterwards."""
    for p in (mock.patch.object(outside, "_d", None),
              mock.patch.object(route, "_d", None),
              mock.patch.object(model, "_d", None),
              mock.patch.object(model, "RUN", model.RUN),
              mock.patch.object(outside, "ROUTER", None),
              mock.patch.object(outside, "PLACES", None),
              mock.patch.object(outside, "_log"),
              mock.patch.object(route, "_log"),
              mock.patch.object(model, "_log"),
              mock.patch.dict(sources.TYPES, {"fake": FakeSource})):
        p.start()
        test.addCleanup(p.stop)
    outside.reset()
    model.reset()
    test.addCleanup(outside.reset)
    test.addCleanup(model.reset)


class Reading(unittest.TestCase):

    def test_notices_become_batches_of_messages(self):
        batches = rp.parse_inbox(INBOX)
        self.assertEqual([len(b) for b in batches], [1, 3, 3])    # the empty notice is left out
        first, reply = batches[0][0], batches[1][0]
        self.assertEqual(first.message.key, "slack:example:C000TEST01:1795000000.000100")
        self.assertEqual((first.day, first.hhmm, first.minute), ("2026-11-18", "08:58", 8 * 60 + 58))
        self.assertEqual(first.message.text, "the made-up spread looks wide")
        self.assertTrue(first.message.thread.first)
        self.assertEqual(reply.message.thread.id, first.message.thread.id)
        self.assertFalse(reply.message.thread.first)
        self.assertEqual(reply.message.thread.head, "the made-up spread looks wide")
        self.assertEqual(reply.message.place.name, "made-up-pricing")
        self.assertEqual(batches[1][1].message.place.kind, "dm")
        self.assertEqual(batches[2][1].message.place.kind, "group dm")
        self.assertEqual(batches[1][1].message.author.name, "Pat Example")

    def test_log_lines(self):
        lines = rp.parse_log(LOG, TABLE["aliases"])
        by = {(lg.start, lg.authors[0]): lg for lg in lines if not lg.later}
        self.assertEqual(len(lines), 9)                            # the correction is left out
        self.assertEqual(by[(8 * 60 + 58, "pat")].to, ["Alpha"])   # through the alias
        self.assertEqual(by[(8 * 60 + 58, "pat")].channels, ["made-up-pricing"])
        self.assertEqual(by[(9 * 60 + 5, "sam")].to, ["Alpha", "Beta"])
        self.assertEqual(by[(9 * 60 + 6, "pat")].outcome, rp.NOT_SENT)
        self.assertEqual(by[(9 * 60 + 20, "lee")].outcome, rp.CARD)
        self.assertEqual(by[(9 * 60 + 25, "jo")].outcome, rp.UNCLEAR)
        self.assertEqual(by[(9 * 60 + 25, "jo")].kind, "group dm")
        self.assertEqual(by[(11 * 60 + 5, "ray")].outcome, rp.CARD)     # missed, then a card
        later = [lg for lg in lines if lg.later]
        self.assertEqual(len(later), 1)
        self.assertEqual((later[0].start, later[0].to), (9 * 60 + 6, ["Beta"]))

    def test_matching_by_day_author_place_and_minute(self):
        seen = [s for b in rp.parse_inbox(INBOX) for s in b]
        lines = rp.parse_log(LOG, TABLE["aliases"])
        got = rp.match(seen, lines)
        dm = "slack:example:D000TEST01:1795000460.000300"
        self.assertTrue(got[dm].later)                              # the later decision wins
        self.assertEqual(got["slack:example:C000TEST04:1795000550.000400"].to, ["Gamma", "Beta"])
        self.assertNotIn("slack:example:D000TEST03:1795001560.000700", got)   # no line for Max
        # A line in another place or too many minutes away does not fit.
        moved = rp.parse_log(LOG.replace("Kim Test, #made-up-stress", "Kim Test, #other"))
        self.assertNotIn("slack:example:C000TEST04:1795000550.000400", rp.match(seen, moved))
        late = rp.parse_log(LOG.replace("- 09:07 · Kim", "- 09:12 · Kim"))
        self.assertNotIn("slack:example:C000TEST04:1795000550.000400", rp.match(seen, late))
        near = rp.parse_log(LOG.replace("- 09:07 · Kim", "- 09:09 · Kim"))
        self.assertIn("slack:example:C000TEST04:1795000550.000400", rp.match(seen, near))


class Replaying(unittest.TestCase):

    def setUp(self):
        _isolated(self)
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-replay-")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name) / "work"
        self.batches = rp.parse_inbox(INBOX)
        self.seen = [s for b in self.batches for s in b]
        self.lines = rp.parse_log(LOG, TABLE["aliases"])

    def run_replay(self, run):
        return rp.replay(self.batches, TABLE, self.work, run=run, say=lambda _m: None)

    def test_routing_uses_the_table_thread_memory_and_the_model(self):
        run = Saved(FIRST, SECOND, THIRD)
        hub = self.run_replay(run)
        self.assertEqual(len(run.prompts), 3)            # the thread reply and the channel needed no call
        k = {s.message.author.name + " " + s.hhmm: hub[s.message.key] for s in self.seen}
        self.assertEqual((k["Pat Example 08:58"]["to"], k["Pat Example 08:58"]["how"]),
                         ("Alpha", route.BY_MODEL))
        self.assertEqual((k["Sam Sample 09:05"]["to"], k["Sam Sample 09:05"]["how"]),
                         ("Alpha", route.BY_THREAD))
        self.assertEqual((k["Kim Test 09:07"]["to"], k["Kim Test 09:07"]["how"]),
                         ("Beta", route.BY_CHANNEL))
        self.assertEqual(k["Pat Example 09:06"]["dropped"], route.SMALL_TALK)
        self.assertEqual(k["Lee Nobody 09:20"]["dropped"], route.NO_MATCH)
        self.assertEqual(k["Jo Unknown 09:25"]["to"], "Alpha")
        self.assertIn("made-up stress runs", run.prompts[0])      # the table's topics are asked about
        self.assertTrue((self.work / "hub").is_dir())

    def test_a_second_run_asks_nothing(self):
        first = self.run_replay(Saved(FIRST, SECOND, THIRD))
        again = self.run_replay(Saved())                 # raises if asked
        self.assertEqual(first, again)

    def test_a_model_that_never_answers(self):
        run = Saved(*["not a list"] * 20)
        hub = self.run_replay(run)
        pat = hub["slack:example:C000TEST01:1795000000.000100"]
        self.assertEqual(pat["dropped"], rp.FAILED)
        self.assertEqual(len(run.prompts), 3 * rp.TRIES * model.TRIES)
        self.assertFalse(json.loads((self.work / "answers.json").read_text()) if
                         (self.work / "answers.json").exists() else {})   # nothing broken kept

    def test_compare_and_report(self):
        hub = self.run_replay(Saved(FIRST, SECOND, THIRD))
        rows = rp.compare(self.seen, self.lines, hub)
        verdict = {r["seen"].message.author.name + " " + r["seen"].hhmm: r["verdict"] for r in rows}
        self.assertEqual(verdict, {
            "Pat Example 08:58": "same",        # Old Alpha is Alpha
            "Sam Sample 09:05": "same",         # the log's first initiative
            "Pat Example 09:06": "differ",      # the later line says Beta; the hub dropped it
            "Kim Test 09:07": "partly",         # the log's second initiative
            "Lee Nobody 09:20": "same",         # a card and no match: neither delivers it
            "Jo Unknown 09:25": "unclear",
            "Max Missing 09:26": "same",        # no line and small talk
        })
        text = rp.report(rows, self.lines, TABLE, "Made-up report", ["_a note_"])
        self.assertTrue(text.startswith("# Made-up report\n\n_a note_"))
        self.assertIn("| **Routed the same** | **4** | **57%** |", text)
        self.assertIn("| All messages replayed | 7 | |", text)
        self.assertIn("## Every difference (1)", text)
        self.assertIn("## Partly the same (1)", text)
        self.assertIn("## Log lines that could not be read (1)", text)
        self.assertIn("## Log lines with no message found (2)", text)   # Nobody Here, Ray Late
        self.assertIn("| Beta | small talk | 1 |", text)
        self.assertIn("| Haiku: a thank you |", text)                   # the reason said once
        self.assertIn("Names in the log read as: Old Alpha → Alpha.", text)

    def test_the_command_line(self):
        d = self.work.parent
        (d / "inbox.md").write_text(INBOX, encoding="utf-8")
        (d / "log.md").write_text(LOG, encoding="utf-8")
        (d / "table.json").write_text(json.dumps(TABLE), encoding="utf-8")
        out = d / "report.md"
        # Without the 08:58 message the thread reply has no thread memory: it is asked too.
        second = json.dumps([{"id": "m1", "to": "Alpha", "why": "spreads"},
                             {"id": "m2", "to": "small talk", "why": "a thank you"}])
        with mock.patch.object(model, "run_claude", Saved(second, THIRD)), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = rp.main(["--inbox", str(d / "inbox.md"), "--log", str(d / "log.md"),
                          "--table", str(d / "table.json"), "--work", str(self.work),
                          "--out", str(out), "--from", "2026-11-18 09:00", "--title", "Made-up"])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("| All messages replayed | 6 | |", text)      # 08:58 is before --from
        self.assertIn("2 notices, 6 messages", text)


DM_INBOX = """# Made-up notices

## 2026-11-18 09:00:00 (reported as task #1, PO woken)

1. **DM** (`D000TEST01`) · Pat Example (`U000TEST02`) · 2026-11-18 08:58:10 CET
   message `1795000000.000100`
   > <https://github.com/example-org/example-repo/pull/1234> can you look at the made-up spread fix

## 2026-11-18 09:10:00 (reported as task #1, PO woken)

1. **DM** (`D000TEST01`) · Pat Example (`U000TEST02`) · 2026-11-18 09:05:40 CET
   message `1795000400.000200`
   > thanks

## 2026-11-18 15:10:00 (reported as task #1, PO woken)

1. **DM** (`D000TEST01`) · Pat Example (`U000TEST02`) · 2026-11-18 15:05:40 CET
   message `1795022000.000300`
   > <https://github.com/example-org/example-repo/pull/1240>
"""

DM_LOG = """# Made-up dispatch log

## 2026-11-18

- 08:58 · Pat Example, DM: example-repo 1234 → **Alpha** (`question`).
- 09:05 · Pat Example, DM: thanks → **Alpha** (`info`).
- 15:05 · Pat Example, DM: example-repo 1240 → **Alpha** (`question`).
"""


class FollowUps(unittest.TestCase):
    """#11: the conversation in the prompt, in the replay; the bare numbers
    in the report."""

    def setUp(self):
        _isolated(self)
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-replay-")
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name) / "work"
        self.batches = rp.parse_inbox(DM_INBOX)
        self.seen = [s for b in self.batches for s in b]

    def test_the_model_sees_each_dm_follow_up_with_the_conversation(self):
        run = Saved(FIRST, json.dumps([{"id": "m1", "to": "Alpha", "why": "follows the review"}]),
                    json.dumps([{"id": "m1", "to": "no match", "why": "a bare link"}]))
        hub = rp.replay(self.batches, TABLE, self.work, run=run, say=lambda _m: None)
        first, thanks, bare = (hub[s.message.key] for s in self.seen)
        self.assertEqual((first["to"], first["how"]), ("Alpha", route.BY_MODEL))
        self.assertEqual((thanks["to"], thanks["how"]), ("Alpha", route.BY_MODEL))
        # Each is asked about, with the earlier messages and where they went.
        self.assertEqual(len(run.prompts), 3)
        self.assertNotIn("<earlier>", run.prompts[0])
        earlier = json.loads(run.prompts[2].split("<earlier>")[1].split("</earlier>")[0])
        self.assertEqual([(x["text"][:16], x.get("went to")) for x in earlier["c1"]],
                         [("example-repo 123", "Alpha"), ("thanks", "Alpha")])
        self.assertEqual(bare["dropped"], route.NO_MATCH)
        rows = rp.compare(self.seen, rp.parse_log(DM_LOG), hub)
        self.assertEqual([r["verdict"] for r in rows], ["same", "same", "differ"])
        self.assertEqual([r["seen"].message.text[-4:] for r in rp.bare_rows(rows)], ["240>"])
        text = rp.report(rows, rp.parse_log(DM_LOG), TABLE)
        self.assertIn("## Bare numbers", text)
        self.assertIn("(at most 3 other words): 1. The log sent 1 of them to an initiative; of "
                      "those, the hub sends 0 to the same one, 0 to another one and **1 nowhere**",
                      text)
        self.assertIn("**How it decided:** 3 by Haiku.", text)

    def test_bare_numbers(self):
        for text in ("<https://github.com/example-org/example-repo/pull/1234>",
                     "example-repo 1234 pls", "ABC-123 :eyes:", "<@U000TEST02|Pat> ABC-123",
                     "https://example.com/some/page", "ABC-1 and ABC-2"):
            self.assertTrue(rp.bare_ref(text), text)
        for text in ("pls", "thanks", "last 600 days", "", "can you look at ABC-123 today please",
                     "the made-up spread looks wide"):
            self.assertFalse(rp.bare_ref(text), text)

    def test_the_command_line_cuts_both_ends_and_writes_the_rows(self):
        d = self.work.parent
        (d / "inbox.md").write_text(DM_INBOX, encoding="utf-8")
        (d / "log.md").write_text(DM_LOG, encoding="utf-8")
        (d / "table.json").write_text(json.dumps(TABLE), encoding="utf-8")
        out, rows = d / "report.md", d / "rows.json"
        saved = Saved(FIRST, SECOND)
        with mock.patch.object(model, "run_claude", saved), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = rp.main(["--inbox", str(d / "inbox.md"), "--log", str(d / "log.md"),
                          "--table", str(d / "table.json"), "--work", str(self.work),
                          "--out", str(out), "--until", "2026-11-18 15:00",
                          "--rows", str(rows)])
        self.assertEqual(rc, 0)
        self.assertIn("| All messages replayed | 2 | |", out.read_text(encoding="utf-8"))
        got = json.loads(rows.read_text(encoding="utf-8"))
        # The 15:05 message is left out; the thanks is asked about too.
        self.assertEqual(len(saved.prompts), 2)
        self.assertEqual([(r["hub"]["how"], r["bare"]) for r in got],
                         [(route.BY_MODEL, False), (route.BY_MODEL, False)])


if __name__ == "__main__":
    unittest.main()
