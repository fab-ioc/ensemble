"""The Jira reader (sources/jira.py JiraSource, sources/jira_connector.py): who
the user is found on the first check, the events of a ticket, the user's own
changes, the moves left out by rule, linked pull requests, read positions, a
whole ticket, the frame never handing on an event twice, the read-only rules,
routing by epic, component and project, and how a Jira message is shown.
Made-up ids only: the repo is public."""
from __future__ import annotations

import json
import re
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import outside
import outside_deliver
import outside_model
import outside_items
import outside_route
import sources
from sources import jira as jira_source
from sources import jira_connector as jc
from sources.base import FAILED, NOT_SET_UP, OK, WHY_MENTION, WHY_TICKET, Message, NotSupported, Watch
from sources.jira import TICKETS, JiraSource
from sources.jira_connector import Query, ReadError
from tests import jira_fixtures as fx
from tests import test_outside_items
from tests.jira_fixtures import BOT, BOT_NAME, CLOUD, ME, MY_NAME, OTHER, OTHER_NAME, PREFIX, SITE

NOW = 1794400000.0
SINCE = NOW - 600
IDENTITY = {"accountId": ME, "cloudId": CLOUD, "site": SITE, "name": MY_NAME}


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def answers(tickets=(), details=None, comments=None, name_issue=None):
    """An answer function for FakeReader. tickets: the search's issues;
    details/comments: {KEY: answer text}."""
    details = details or {}
    comments = comments or {}

    def answer(q):
        if q.kind == "me":
            return fx.ME_ANSWER
        if q.kind == "sites":
            return fx.SITES_ANSWER
        if q.label in (TICKETS, "ticket"):
            return fx.search_answer(*tickets)
        if q.label == jira_source.NAME_READ:
            return fx.search_answer(*([name_issue] if name_issue else []))
        if q.kind == "issue":
            key = q.args["issueIdOrKey"]
            return details.get(key) or fx.detail_answer(key)
        if q.kind == "comments":
            return comments.get(q.args["issueIdOrKey"]) or fx.comments_answer()
        return None
    return answer


def source(answer, memory=None):
    reader = fx.FakeReader(answer)
    return JiraSource(reader_factory=lambda settings, memory: reader, memory=memory), reader


def known(**tickets):
    return {"identity": dict(IDENTITY), "tickets": tickets}


def check(answer, memory=None, settings=None, positions=None, now=NOW):
    src, reader = source(answer)
    memory = known() if memory is None else memory
    got = src.check(Watch(settings=settings or {}, memory=memory),
                    {TICKETS: SINCE} if positions is None else positions, now)
    return got, reader, src


def assert_next_page(test, got, at):
    """A full check: the position stays at the start of the catch-up, and the
    next search starts at ``at`` (kept in the source's memory)."""
    test.assertEqual(got.positions, {TICKETS: SINCE})
    test.assertEqual(got.full, {TICKETS})
    test.assertEqual(got.memory["page"], {"window": SINCE, "from": float(int(at))})


def by_event(messages):
    out = {}
    for m in messages:
        out.setdefault(m.details["event"], []).append(m)
    return out


class FirstCheckTest(unittest.TestCase):
    def test_finds_the_account_and_the_jira_site_and_keeps_no_email(self):
        t = fx.issue("ABC-12", "Fix the price feed", SINCE + 60, SINCE - 86400)
        got, reader, _ = check(answers([t], name_issue=t), memory={})
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual([q.label for q in reader.batches[0]], ["me", "sites"])
        self.assertEqual([q.label for q in reader.batches[1]], [TICKETS, jira_source.NAME_READ])
        # The first site that has Jira, not the wiki-only one listed before it.
        self.assertEqual(got.memory["identity"], {"accountId": ME, "cloudId": CLOUD, "site": SITE,
                                                  "name": MY_NAME})
        self.assertEqual(got.memory["toolPrefix"], PREFIX)
        kept = json.dumps(got.memory)
        self.assertNotIn("emailAddress", kept)
        self.assertNotIn("@example.com", kept)

    def test_a_known_identity_is_not_read_again(self):
        got, reader, _ = check(answers())
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual([[q.label for q in b] for b in reader.batches], [[TICKETS]])

    def test_the_name_is_found_in_a_ticket_history_too(self):
        t = fx.issue("ABC-12", "Fix the price feed", SINCE + 60, SINCE - 86400, assignee=None)
        d = fx.detail_answer("ABC-12", [fx.history(5, SINCE + 60, (ME, MY_NAME), fx.status_item("To Do", "Done"))])
        memory = {"identity": {"accountId": ME, "cloudId": CLOUD, "site": SITE}}
        got, _, _ = check(answers([t], {"ABC-12": d}), memory=memory)
        self.assertEqual(got.memory["identity"]["name"], MY_NAME)

    def test_a_connection_without_jira_is_not_set_up(self):
        def answer(q):
            if q.kind == "sites":
                return json.dumps({"data": {"resources": [{"cloudId": "x", "url": "https://wiki-only.example.com",
                                                           "products": [{"id": "confluence"}]}]}})
            return answers()(q)
        got, _, _ = check(answer, memory={})
        self.assertEqual(got.outcome, NOT_SET_UP)
        self.assertIn("no Jira site", got.reason)

    def test_a_failed_read_moves_nothing(self):
        def answer(q):
            raise ReadError("the Jira read tickets failed: Error: 404", "jira_error")
        got, _, _ = check(answer)
        self.assertEqual(got.outcome, FAILED)
        self.assertIn("Jira could not be read", got.reason)
        self.assertEqual((got.messages, got.positions, got.memory), ([], {}, None))


class SearchTest(unittest.TestCase):
    def test_the_search_asks_for_the_tickets_the_user_is_on_since_the_last_check(self):
        _, reader, _ = check(answers())
        q = reader.batches[0][0]
        self.assertEqual(q.kind, "search")
        self.assertEqual(q.args["cloudId"], CLOUD)
        jql = q.args["jql"]
        for part in ("assignee = currentUser()", "reporter = currentUser()", "watcher = currentUser()",
                     f'comment ~ "{ME}"', f'description ~ "{ME}"'):
            self.assertIn(part, jql)
        # 10 minutes since the last check, one more for the clock.
        self.assertTrue(jql.endswith('updated >= "-11m" ORDER BY updated ASC'), jql)

    def test_switches_off_take_their_part_out(self):
        _, reader, _ = check(answers(), settings={"watched": False, "mentioned": False})
        jql = reader.batches[0][0].args["jql"]
        self.assertNotIn("watcher", jql)
        self.assertNotIn("comment ~", jql)
        self.assertIn("assignee = currentUser()", jql)

    def test_nothing_chosen_is_not_set_up(self):
        got, reader, _ = check(answers(), settings={"assigned": False, "reported": False, "watched": False,
                                                    "mentioned": False})
        self.assertEqual(got.outcome, NOT_SET_UP)
        self.assertEqual(reader.batches, [])


class EventsTest(unittest.TestCase):
    def setUp(self):
        self.t = fx.issue("ABC-12", "Fix the price feed", SINCE + 300, SINCE + 10, assignee=(ME, MY_NAME),
                          components=("Prices",))
        self.d = fx.detail_answer("ABC-12", [
            fx.history(101, SINCE + 100, (OTHER, OTHER_NAME), fx.status_item("To Do", "In Progress")),
            fx.history(102, SINCE + 110, (OTHER, OTHER_NAME), fx.assignee_item(OTHER_NAME, MY_NAME)),
            fx.history(103, SINCE + 120, (OTHER, OTHER_NAME), {"field": "labels", "fromString": "",
                                                               "toString": "x"}),
            fx.history(50, SINCE - 100, (OTHER, OTHER_NAME), fx.status_item("Open", "To Do")),
        ], description=f"<p>Please look, {fx.mention(ME, MY_NAME)}</p>", prs=1, pr_at=SINCE + 200)
        self.c = fx.comments_answer(
            fx.comment(9002, SINCE + 250, OTHER_NAME, f"<p>{fx.mention(ME, MY_NAME)} can you check?</p>"),
            fx.comment(9001, SINCE + 200, OTHER_NAME, "<p>Seen on <b>staging</b> too.</p>"),
            fx.comment(9000, SINCE - 300, OTHER_NAME, "<p>Old, before the last check.</p>"))

    def test_each_kind_of_event_is_one_message(self):
        got, reader, _ = check(answers([self.t], {"ABC-12": self.d}, {"ABC-12": self.c}))
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual([q.label for q in reader.batches[1]], ["issue:ABC-12", "comments:ABC-12"])
        ev = by_event(got.messages)
        self.assertEqual(sorted(ev), ["assigned", "comment", "created", "pr", "status"])
        self.assertEqual([m.at for m in got.messages], sorted(m.at for m in got.messages))

        [new] = ev["created"]
        self.assertEqual(new.key, f"jira:{SITE}:ABC-12:created:0")
        self.assertEqual(new.text, f"New ticket: Fix the price feed\nPlease look, @{MY_NAME}")
        self.assertEqual(new.why_seen, WHY_MENTION)    # assigned to the user
        self.assertTrue(new.addressed)
        self.assertTrue(new.thread.first)

        [status] = ev["status"]
        self.assertEqual(status.text, "Status: To Do → In Progress")
        self.assertEqual(status.key, f"jira:{SITE}:ABC-12:status:101")
        self.assertEqual(status.author.name, OTHER_NAME)
        self.assertFalse(status.author.me)
        self.assertEqual(status.why_seen, WHY_TICKET)
        self.assertFalse(status.addressed)

        [assigned] = ev["assigned"]
        self.assertEqual(assigned.text, f"Assigned to you (was {OTHER_NAME})")
        self.assertEqual(assigned.why_seen, WHY_MENTION)
        self.assertTrue(assigned.addressed)

        plain, named = ev["comment"]
        self.assertEqual(plain.text, "Seen on staging too.")
        self.assertEqual(plain.why_seen, WHY_TICKET)
        self.assertEqual(plain.link, f"https://{SITE}/browse/ABC-12?focusedCommentId=9001")
        self.assertEqual(named.text, f"@{MY_NAME} can you check?")
        self.assertEqual(named.why_seen, WHY_MENTION)
        self.assertTrue(named.addressed)

        [pr] = ev["pr"]
        self.assertEqual(pr.text, "Pull request linked (1 in all, open)")
        self.assertEqual(pr.at, SINCE + 200)

        for m in got.messages:
            self.assertEqual(m.source, "jira")
            self.assertEqual((m.place.id, m.place.name, m.place.kind), ("ABC-12", "ABC-12 Fix the price feed",
                                                                        "ticket"))
            self.assertEqual((m.thread.id, m.thread.head), ("ABC-12", "Fix the price feed"))
            self.assertEqual(m.reply_to, {"site": SITE, "ticket": "ABC-12"})
            self.assertEqual(m.details["epic"], "ABC-10")
            self.assertEqual(m.details["components"], ["Prices"])
            self.assertEqual(m.details["project"], "ABC")
            self.assertTrue(m.link.startswith(f"https://{SITE}/browse/ABC-12"))
            self.assertNotIn("emailAddress", json.dumps(m.to_dict()))
        self.assertEqual(got.memory["tickets"]["ABC-12"]["updated"], fx.jtime(SINCE + 300))
        self.assertEqual(got.memory["tickets"]["ABC-12"]["prCount"], 1)

    def test_assigned_away_from_the_user(self):
        t = fx.issue("ABC-13", "Other work", SINCE + 60, SINCE - 86400, assignee=(OTHER, OTHER_NAME))
        d = fx.detail_answer("ABC-13", [fx.history(7, SINCE + 60, (OTHER, OTHER_NAME),
                                                   fx.assignee_item(MY_NAME, OTHER_NAME))])
        got, _, _ = check(answers([t], {"ABC-13": d}))
        [m] = got.messages
        self.assertEqual(m.text, f"Assigned to {OTHER_NAME} (was you)")
        self.assertFalse(m.addressed)

    def test_the_users_own_changes_are_marked_or_left_out(self):
        t = fx.issue("ABC-14", "Mine", SINCE + 60, SINCE - 86400)
        d = fx.detail_answer("ABC-14", [fx.history(8, SINCE + 50, (ME, MY_NAME), fx.status_item("To Do", "Done"))])
        c = fx.comments_answer(fx.comment(9100, SINCE + 60, MY_NAME, "<p>Done, see the pull request.</p>"))
        got, _, _ = check(answers([t], {"ABC-14": d}, {"ABC-14": c}))
        self.assertEqual(len(got.messages), 2)
        self.assertTrue(all(m.author.me for m in got.messages))
        got, _, _ = check(answers([t], {"ABC-14": d}, {"ABC-14": c}), settings={"mine": False})
        self.assertEqual(got.messages, [])


class DropRuleTest(unittest.TestCase):
    def setUp(self):
        self.t = fx.issue("ABC-20", "Release it", SINCE + 60, SINCE - 86400)
        self.d = fx.detail_answer("ABC-20", [fx.history(9, SINCE + 60, (BOT, BOT_NAME),
                                                        fx.status_item("In Review", "Released"))])

    def test_a_move_to_a_dropped_status_by_someone_else_is_counted_not_handed_on(self):
        got, _, _ = check(answers([self.t], {"ABC-20": self.d}))
        self.assertEqual(got.messages, [])
        self.assertEqual(got.notes, ["1 status moves to Ready for Release, Released left out by rule today"])
        self.assertEqual(got.memory["dropped"]["keys"], ["ABC-20:9"])

        # The ticket changes again and the same move is read again: still counted once.
        t2 = fx.issue("ABC-20", "Release it", SINCE + 120, SINCE - 86400)
        again, _, _ = check(answers([t2], {"ABC-20": self.d}), memory=got.memory)
        self.assertEqual(again.notes, ["1 status moves to Ready for Release, Released left out by rule today"])

    def test_the_count_starts_again_the_next_day(self):
        got, _, _ = check(answers([self.t], {"ABC-20": self.d}))
        memory = dict(got.memory, tickets={})
        later, _, _ = check(answers(), memory=memory, now=NOW + 86400, positions={TICKETS: NOW + 86400 - 600})
        self.assertEqual(later.notes, [])
        self.assertEqual(later.memory["dropped"]["keys"], [])

    def test_the_users_own_move_is_handed_on(self):
        d = fx.detail_answer("ABC-20", [fx.history(9, SINCE + 60, (ME, MY_NAME),
                                                   fx.status_item("In Review", "Released"))])
        got, _, _ = check(answers([self.t], {"ABC-20": d}))
        [m] = got.messages
        self.assertEqual(m.text, "Status: In Review → Released")
        self.assertEqual(got.notes, [])

    def test_the_list_is_a_setting(self):
        got, _, _ = check(answers([self.t], {"ABC-20": self.d}), settings={"dropStatuses": ["Done"]})
        self.assertEqual([m.text for m in got.messages], ["Status: In Review → Released"])


class PullRequestTest(unittest.TestCase):
    def test_a_ticket_seen_for_the_first_time_sets_the_count_without_a_message(self):
        t = fx.issue("ABC-30", "Has pull requests", SINCE + 60, SINCE - 86400)
        got, _, _ = check(answers([t], {"ABC-30": fx.detail_answer("ABC-30", prs=2)}))
        self.assertEqual(got.messages, [])
        self.assertEqual(got.memory["tickets"]["ABC-30"]["prCount"], 2)

        t2 = fx.issue("ABC-30", "Has pull requests", NOW + 60, SINCE - 86400)
        later, _, _ = check(answers([t2], {"ABC-30": fx.detail_answer("ABC-30", prs=3, pr_at=NOW + 50)}),
                            memory=got.memory, positions={TICKETS: NOW}, now=NOW + 120)
        [m] = later.messages
        self.assertEqual(m.text, "Pull request linked (3 in all, open)")
        self.assertEqual(m.key, f"jira:{SITE}:ABC-30:pr:3")
        self.assertEqual(m.author.name, "Jira")

    def test_a_new_ticket_counts_from_zero(self):
        t = fx.issue("ABC-31", "New with a pull request", SINCE + 60, SINCE + 30)
        got, _, _ = check(answers([t], {"ABC-31": fx.detail_answer("ABC-31", prs=1)}))
        self.assertEqual(sorted(by_event(got.messages)), ["created", "pr"])

    def test_the_count_read_both_ways(self):
        self.assertEqual(jira_source.pull_requests({"customFields": {"Development": fx.development(4)}})["count"], 4)
        plain = {"customFields": {"Development": "{pullrequest={dataType=pullrequest, state=MERGED, stateCount=2}}"}}
        self.assertEqual(jira_source.pull_requests(plain), {"count": 2, "state": "MERGED", "at": None})
        self.assertEqual(jira_source.pull_requests({})["count"], 0)


class PositionTest(unittest.TestCase):
    def tickets(self, n, start=SINCE + 10, step=5):
        return [fx.issue(f"ABC-{100 + i}", f"Ticket {i}", start + i * step, SINCE - 86400) for i in range(n)]

    def test_a_check_that_read_everything_moves_to_now(self):
        got, _, _ = check(answers(self.tickets(3)))
        self.assertEqual(got.positions, {TICKETS: NOW})
        self.assertEqual(got.full, set())

    def test_tickets_read_at_the_same_change_are_not_read_again(self):
        ts = self.tickets(2)
        first, _, _ = check(answers(ts))
        _, reader, _ = check(answers(ts + [fx.issue("ABC-200", "New change", SINCE + 400, SINCE - 86400)]),
                             memory=first.memory)
        self.assertEqual([q.label for q in reader.batches[1]], ["issue:ABC-200", "comments:ABC-200"])

    def test_more_changed_tickets_than_one_check_reads_are_cut(self):
        ts = self.tickets(30)
        got, reader, _ = check(answers(ts))
        self.assertEqual(len(reader.batches), 1 + 3)        # the search, then 3 runs of 8 tickets
        self.assertEqual([len(b) for b in reader.batches[1:]], [16, 16, 16])
        self.assertEqual(got.full, {TICKETS})
        assert_next_page(self, got, SINCE + 10 + 23 * 5)
        self.assertEqual(len(got.memory["tickets"]), 24)

    def test_a_full_page_goes_on_from_its_last_ticket(self):
        ts = self.tickets(jc.PAGE_SIZE, step=3)
        memory = known(**{t["key"]: {"updated": t["fields"]["updated"], "prCount": 0, "at": SINCE}
                          for t in ts})
        got, reader, _ = check(answers(ts), memory=memory)
        self.assertEqual(len(reader.batches), 1)            # all read before at this change
        self.assertEqual(got.full, {TICKETS})
        assert_next_page(self, got, SINCE + 10 + (jc.PAGE_SIZE - 1) * 3)
        self.assertEqual(got.notes, [])

    def test_a_full_page_within_one_minute_skips_the_rest_of_that_minute(self):
        ts = self.tickets(jc.PAGE_SIZE, step=0)
        memory = known(**{t["key"]: {"updated": t["fields"]["updated"], "prCount": 0, "at": SINCE}
                          for t in ts})
        got, _, _ = check(answers(ts), memory=memory)
        assert_next_page(self, got, SINCE + 60)
        self.assertEqual(len(got.notes), 1)
        self.assertIn("within one minute", got.notes[0])


class TooLargeTest(unittest.TestCase):
    """Claude passes on about 50 KB of one answer; a larger one is read again
    asking for less (a smaller page, a ticket without its history, fewer
    comments)."""

    def search_too_large_at_full_page(self, ts):
        base = answers(ts)

        def answer(q):
            if q.label == TICKETS and q.args["maxResults"] > jc.SMALL_PAGE:
                return fx.TOO_LARGE
            return base(q)
        return answer

    def test_a_search_too_large_is_read_again_a_small_page_at_a_time(self):
        ts = [fx.issue(f"ABC-{100 + i}", f"Ticket {i}", SINCE + 10 + i * 10, SINCE - 86400)
              for i in range(jc.SMALL_PAGE)]
        memory = known(**{t["key"]: {"updated": t["fields"]["updated"], "prCount": 0, "at": SINCE} for t in ts})
        got, reader, _ = check(self.search_too_large_at_full_page(ts), memory=memory)
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual([b[0].args["maxResults"] for b in reader.batches], [jc.PAGE_SIZE, jc.SMALL_PAGE])
        # A full small page: the next check goes on from its last ticket.
        self.assertEqual(got.full, {TICKETS})
        assert_next_page(self, got, SINCE + 10 + (jc.SMALL_PAGE - 1) * 10)

    def test_a_search_too_large_even_small_fails_the_check_and_moves_nothing(self):
        got, _, _ = check(lambda q: fx.TOO_LARGE if q.kind == "search" else answers()(q))
        self.assertEqual(got.outcome, FAILED)
        self.assertEqual(got.code, "too_large")
        self.assertEqual((got.messages, got.positions), ([], {}))

    def without_history(self, big=(), huge=()):
        """issue reads of `big` are too large with their history, of `huge` too large always."""
        def answer(q, base):
            key = q.args.get("issueIdOrKey")
            if q.kind == "issue" and (key in huge or (key in big and "expand" in q.args)):
                return fx.TOO_LARGE
            return base(q)
        return answer

    def test_a_ticket_too_large_with_its_history_is_read_without_it(self):
        t = fx.issue("ABC-40", "Long history", SINCE + 60, SINCE - 86400, status="In Progress",
                     assignee=(ME, MY_NAME))
        memory = known(**{"ABC-40": {"updated": fx.jtime(SINCE - 500), "prCount": 0, "status": "To Do",
                                     "assignee": OTHER, "at": SINCE - 500}})
        base = answers([t])
        got, reader, _ = check(lambda q: self.without_history(big={"ABC-40"})(q, base), memory=memory)
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual([q.label for q in reader.batches[1]], ["issue:ABC-40", "comments:ABC-40"])
        self.assertIn("expand", reader.batches[1][0].args)
        [again] = reader.batches[2]
        self.assertEqual((again.label, "expand" in again.args), ("issue:ABC-40", False))
        ev = by_event(got.messages)
        self.assertEqual(sorted(ev), ["assigned", "status"])
        [status], [assigned] = ev["status"], ev["assigned"]
        self.assertEqual(status.text, "Status: To Do → In Progress")
        self.assertEqual(status.key, f"jira:{SITE}:ABC-40:status:k{int(SINCE + 60)}")
        self.assertEqual((status.author.name, status.author.me), ("", False))   # who did it is not known
        self.assertEqual(assigned.text, "Assigned to you")
        self.assertTrue(assigned.addressed)
        self.assertEqual(got.notes, ["history too large to read for ABC-40: status and assignee changes there "
                                     "were taken from the ticket, without who made them"])
        kept = got.memory["tickets"]["ABC-40"]
        self.assertEqual((kept["status"], kept["assignee"]), ("In Progress", ME))

    def test_a_move_to_a_dropped_status_found_that_way_is_left_out_by_rule(self):
        t = fx.issue("ABC-41", "Release it", SINCE + 60, SINCE - 86400, status="Released")
        memory = known(**{"ABC-41": {"updated": fx.jtime(SINCE - 500), "prCount": 0, "status": "In Review",
                                     "assignee": ME, "at": SINCE - 500}})
        base = answers([t])
        got, _, _ = check(lambda q: self.without_history(big={"ABC-41"})(q, base), memory=memory)
        self.assertEqual(got.messages, [])
        self.assertEqual(got.memory["dropped"]["keys"], [f"ABC-41:k{int(SINCE + 60)}"])
        self.assertIn("1 status moves to Ready for Release, Released left out by rule today", got.notes)

    def test_a_first_sighting_without_history_only_sets_what_is_kept(self):
        t = fx.issue("ABC-42", "Seen first", SINCE + 60, SINCE - 86400, status="In Progress")
        base = answers([t])
        got, _, _ = check(lambda q: self.without_history(big={"ABC-42"})(q, base))
        self.assertEqual(got.messages, [])
        self.assertEqual(got.memory["tickets"]["ABC-42"]["status"], "In Progress")

    def test_a_ticket_too_large_even_without_history_is_not_kept(self):
        ts = [fx.issue("ABC-43", "Huge", SINCE + 60, SINCE - 86400),
              fx.issue("ABC-44", "Small", SINCE + 70, SINCE - 86400)]
        c = fx.comments_answer(fx.comment(9300, SINCE + 65, OTHER_NAME, "<p>On the huge one.</p>"))
        base = answers(ts, comments={"ABC-43": c})
        got, _, _ = check(lambda q: self.without_history(huge={"ABC-43"})(q, base))
        self.assertEqual(got.outcome, OK, got.reason)
        self.assertEqual(got.messages, [])          # its comment waits for its next change
        self.assertNotIn("ABC-43", got.memory["tickets"])
        self.assertIn("ABC-44", got.memory["tickets"])
        self.assertEqual(got.notes, ["the Jira read issue:ABC-43 was too large to read even when asking for less"])

    def test_comments_too_large_are_read_again_fewer(self):
        t = fx.issue("ABC-45", "Talkative", SINCE + 60, SINCE - 86400)
        c = fx.comments_answer(fx.comment(9400, SINCE + 50, OTHER_NAME, "<p>Latest.</p>"))

        def answer(q):
            if q.kind == "comments" and q.args["maxResults"] > jira_source.FEW_COMMENTS:
                return fx.TOO_LARGE
            return answers([t], comments={"ABC-45": c})(q)
        got, reader, _ = check(answer)
        [again] = reader.batches[2]
        self.assertEqual((again.label, again.args["maxResults"]), ("comments:ABC-45", jira_source.FEW_COMMENTS))
        self.assertEqual([m.text for m in got.messages], ["Latest."])
        self.assertEqual(got.notes, [])

    def test_a_ticket_whose_comments_read_are_all_new_gets_a_note(self):
        # Review 1: only the newest comments are read; when even the oldest
        # of them is new and the ticket has more, new ones may be missing.
        t = fx.issue("ABC-47", "Busy", SINCE + 600, SINCE - 86400)
        u = fx.issue("ABC-48", "Quiet", SINCE + 600, SINCE - 86400)
        many = json.loads(fx.comments_answer(*[fx.comment(500 + i, SINCE + 10 + i, OTHER_NAME, f"<p>{i}</p>")
                                               for i in range(jira_source.COMMENTS_READ)]))
        many.update(total=31, isLast=False)
        few = fx.comments_answer(fx.comment(600, SINCE + 20, OTHER_NAME, "<p>New.</p>"),
                                 fx.comment(601, SINCE - 500, OTHER_NAME, "<p>Old.</p>"))
        got, _, _ = check(answers([t, u], comments={"ABC-47": json.dumps(many), "ABC-48": few}))
        self.assertEqual(len(got.messages), jira_source.COMMENTS_READ + 1)
        self.assertEqual(got.notes, ["only the newest comments are read, and all of them were new on ABC-47: "
                                     "older new comments there may be missing"])
        # Not when the read reaches back past the last check, nor when there are no more.
        self.assertFalse(jira_source.comments_may_be_missing(json.loads(few), SINCE))
        self.assertFalse(jira_source.comments_may_be_missing(json.loads(fx.comments_answer(
            fx.comment(1, SINCE + 5, OTHER_NAME, "x"))), SINCE))

    def test_the_stream_puts_a_too_large_answer_aside_without_failing(self):
        qs = [Query(TICKETS, "search", {"cloudId": CLOUD, "jql": "x", "maxResults": 20}),
              Query("issue:ABC-1", "issue", {"cloudId": CLOUD, "issueIdOrKey": "ABC-1"})]
        got = jc.read_stream(fx.stream(qs, {TICKETS: fx.TOO_LARGE, "issue:ABC-1": fx.detail_answer("ABC-1")}),
                             qs, PREFIX)
        self.assertEqual(got.too_large, {TICKETS})
        self.assertEqual(list(got.answers), ["issue:ABC-1"])
        self.assertTrue(jc.too_large(fx.TOO_LARGE))
        self.assertFalse(jc.too_large(fx.search_answer()))

    def test_a_whole_ticket_with_too_many_comments_reads_fewer(self):
        t = fx.issue("ABC-46", "Talkative", NOW - 100, NOW - 86400)
        c = fx.comments_answer(fx.comment(1, NOW - 300, OTHER_NAME, "<p>Only.</p>"))

        def answer(q):
            if q.kind == "comments" and q.args["maxResults"] > jira_source.FEW_COMMENTS:
                return fx.TOO_LARGE
            return answers([t], comments={"ABC-46": c})(q)
        src, reader = source(answer, memory=known())
        got = src.thread("ABC-46")
        self.assertEqual([m.text for m in got][1:], ["Only."])
        self.assertEqual(reader.batches[1][0].args["maxResults"], jira_source.FEW_COMMENTS)

    def test_the_name_read_asks_for_the_full_view(self):
        _, reader, _ = check(answers(), memory={"identity": {"accountId": ME, "cloudId": CLOUD, "site": SITE}})
        [name] = [q for q in reader.batches[0] if q.label == jira_source.NAME_READ]
        self.assertEqual(name.args["view"], "full")


class ThreadTest(unittest.TestCase):
    def test_a_whole_ticket_is_its_start_and_its_comments(self):
        t = fx.issue("ABC-12", "Fix the price feed", NOW - 100, NOW - 86400)
        t["fields"]["description"] = "<p>What to do.</p>"
        c = fx.comments_answer(fx.comment(2, NOW - 200, MY_NAME, "<p>Second</p>"),
                               fx.comment(1, NOW - 300, OTHER_NAME, "<p>First</p>"))
        src, reader = source(answers([t], comments={"ABC-12": c}), memory=known())
        got = src.thread("ABC-12")
        self.assertEqual([m.text for m in got], ["New ticket: Fix the price feed\nWhat to do.", "First", "Second"])
        self.assertEqual([q.label for q in reader.batches[0]], ["ticket", "comments:ABC-12"])
        self.assertEqual(reader.batches[0][0].args["jql"], "key = ABC-12")
        self.assertTrue(got[2].author.me)    # own comments are part of the ticket

    def test_not_a_ticket_and_not_read_yet(self):
        src, _ = source(answers(), memory=known())
        with self.assertRaises(ValueError):
            src.thread("abc 12")
        src, _ = source(answers(), memory={})
        with self.assertRaises(ValueError):
            src.thread("ABC-12")

    def test_nothing_is_written_to_jira(self):
        src, _ = source(answers(), memory=known())
        self.assertFalse(src.can_send)
        with self.assertRaises(NotSupported):
            src.send({"site": SITE, "ticket": "ABC-12"}, "hello")


class OnlyReadsTest(unittest.TestCase):
    """The reader must never write to Jira."""

    def queries(self):
        return [Query(TICKETS, "search", {"cloudId": CLOUD, "jql": "assignee = currentUser()", "maxResults": 50}),
                Query("issue:ABC-12", "issue", {"cloudId": CLOUD, "issueIdOrKey": "ABC-12"})]

    def test_every_tool_of_the_connection_is_read_only_or_refused(self):
        for name in jc.KNOWN_READ_ONLY:
            self.assertFalse(jc.looks_like_write_tool(name), name)
        for name in jc.WRITE_TOOLS:
            self.assertTrue(jc.looks_like_write_tool(name), name)
        for name in fx.ALL_TOOLS:
            self.assertTrue(name in jc.KNOWN_READ_ONLY or name in jc.WRITE_TOOLS or name in jc.UNUSED_TOOLS, name)
        for name in jc.WRITE_TOOLS + ["executeRead", "deleteJiraIssue"]:
            with self.assertRaises(ValueError):
                jc.check_allowed([jc.SEARCH, name])

    def test_the_command_allows_only_the_planned_tools(self):
        cmd = jc.build_command("fake-claude", PREFIX, self.queries())
        allowed = flag(cmd, "--allowedTools").split(",")
        denied = flag(cmd, "--disallowedTools").split(",")
        self.assertEqual(sorted(allowed), [PREFIX + jc.ISSUE, PREFIX + jc.SEARCH])
        for name in jc.WRITE_TOOLS + ["executeRead", "discover", jc.COMMENTS]:
            self.assertIn(PREFIX + name, denied)
        self.assertEqual(set(allowed) & set(denied), set())
        self.assertEqual(flag(cmd, "--tools"), "")
        self.assertEqual(flag(cmd, "--max-turns"), "1")
        self.assertEqual(flag(cmd, "--permission-mode"), "dontAsk")
        self.assertEqual(json.loads(flag(cmd, "--settings")),
                         {"allowedMcpServers": [{"serverUrl": "https://mcp.atlassian.com/v2/*"}]})
        self.assertTrue(cmd[-1].startswith("Make these 2 tool calls:"))

    def test_a_write_tool_present_in_the_run_fails_it(self):
        qs = self.queries()
        lines = fx.stream(qs, {q.label: fx.search_answer() for q in qs},
                          tools_available=[PREFIX + jc.SEARCH, PREFIX + jc.ISSUE, PREFIX + "editJiraIssue"])
        with self.assertRaises(ReadError) as ctx:
            jc.read_stream(lines, qs, PREFIX)
        self.assertEqual(ctx.exception.code, "write_tool")

    def test_a_call_that_was_not_planned_fails_the_read(self):
        qs = self.queries()
        lines = fx.stream(qs, {q.label: fx.search_answer() for q in qs},
                          extra_calls=[(PREFIX + jc.SEARCH, {"cloudId": CLOUD, "jql": "project = ABC"})])
        with self.assertRaises(ReadError) as ctx:
            jc.read_stream(lines, qs, PREFIX)
        self.assertEqual(ctx.exception.code, "unplanned_call")

    def test_answers_lose_their_data_wrapper_and_errors_fail(self):
        qs = self.queries()[:1]
        got = jc.read_stream(fx.stream(qs, {TICKETS: fx.search_answer()}), qs, PREFIX)
        self.assertEqual(got.answers[TICKETS]["issues"], [])
        with self.assertRaises(ReadError) as ctx:
            jc.read_stream(fx.stream(qs, {}, errors=[TICKETS]), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "jira_error")
        with self.assertRaises(ReadError) as ctx:
            jc.read_stream(fx.stream(qs, {TICKETS: "not json"}), qs, PREFIX)
        self.assertEqual(ctx.exception.code, "unreadable")

    def test_the_old_connection_is_not_taken_for_the_new_one(self):
        old = "mcp__claude_ai_Atlassian_Rovo__"
        init = {"type": "system", "subtype": "init", "tools": [old + "getJiraIssue", PREFIX + jc.COMMENTS]}
        self.assertEqual(jc.find_prefix([json.dumps(init)]), PREFIX)

    def test_another_reader_is_not_built_yet(self):
        with self.assertRaises(ReadError) as ctx:
            jc.make_reader({"reader": "token"}, {})
        self.assertEqual(ctx.exception.code, "not_set_up")


class ThroughTheFrameTest(unittest.TestCase):
    """The Jira source run by the real frame (outside.run_check): an event
    read again in a later check is never handed on twice."""
    SID = "jira-made-up"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-outside-jira-")
        self.addCleanup(tmp.cleanup)
        old = outside._d
        outside.bind(types.SimpleNamespace(DASHBOARD_DIR=Path(tmp.name)))
        self.addCleanup(outside.bind, old)
        for p in (mock.patch.object(outside, "_log"),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None)):
            p.start()
            self.addCleanup(p.stop)
        outside.reset()
        self.addCleanup(outside.reset)
        self.tickets, self.details, self.comments = [], {}, {}
        reader = self.reader = fx.FakeReader(lambda q: answers(self.tickets, self.details, self.comments,
                                                 name_issue=fx.issue("ABC-1", "x", NOW - 9e5, NOW - 9e5))(q))
        src = JiraSource(reader_factory=lambda settings, memory: reader)

        class Shared(JiraSource):
            def __new__(cls):
                return src

        p = mock.patch.dict(sources.TYPES, {"jira": Shared})
        p.start()
        self.addCleanup(p.stop)
        outside.set_source(self.SID, type="jira", mode="decide", settings={})

    def run_at(self, now):
        outside.note_tick(now)
        got = outside.run_check(self.SID, now)
        self.assertEqual(got["outcome"], "ok", got)
        return got["added"]

    def test_each_event_once(self):
        self.assertEqual(self.run_at(NOW), 0)
        self.tickets = [fx.issue("ABC-12", "Fix the price feed", NOW + 100, NOW - 86400)]
        self.comments = {"ABC-12": fx.comments_answer(fx.comment(1, NOW + 100, OTHER_NAME, "<p>First</p>"))}
        self.assertEqual(self.run_at(NOW + 300), 1)
        # Read again within the one-minute overlap, at the same change: nothing new.
        self.assertEqual(self.run_at(NOW + 360), 0)
        # The ticket changes again: only the new event.
        self.tickets = [fx.issue("ABC-12", "Fix the price feed", NOW + 400, NOW - 86400)]
        self.details = {"ABC-12": fx.detail_answer("ABC-12", [
            fx.history(3, NOW + 400, (OTHER, OTHER_NAME), fx.status_item("To Do", "In Progress"))])}
        self.comments = {"ABC-12": fx.comments_answer(
            fx.comment(1, NOW + 100, OTHER_NAME, "<p>First</p>"))}
        self.assertEqual(self.run_at(NOW + 420), 1)
        # A restart, and the tickets forgotten: everything is read again, nothing is handed on twice.
        outside.reset()
        st = outside._load_state(self.SID)
        st["memory"]["tickets"] = {}
        outside._save_state(self.SID, st)
        self.assertEqual(self.run_at(NOW + 480), 0)
        keys = [e["key"] for e in outside._load_state(self.SID)["unrouted"]]
        self.assertEqual(sorted(keys), [f"jira:{SITE}:ABC-12:comment:1", f"jira:{SITE}:ABC-12:status:3"])

    def test_a_ticket_left_for_the_next_page_keeps_its_older_events(self):
        # Review 1: a full page used to move the position to its last ticket,
        # and a ticket left for the next page lost events older than that.
        self.assertEqual(self.run_at(NOW), 0)
        self.tickets = [fx.issue(f"ABC-{100 + i}", f"Ticket {i}", NOW + 10 + i * 20, NOW - 86400)
                        for i in range(jc.PAGE_SIZE)]
        self.run_at(NOW + 600)
        st = outside._load_state(self.SID)
        self.assertTrue(st["positions"][TICKETS]["full"])
        self.assertEqual(st["memory"]["page"]["from"], float(int(NOW + 10 + (jc.PAGE_SIZE - 1) * 20)))
        # The next page: a ticket last changed after the page, with a comment from before its end.
        self.tickets = [fx.issue("ABC-200", "Left for the next page", NOW + 550, NOW - 86400)]
        self.comments = {"ABC-200": fx.comments_answer(fx.comment(7, NOW + 20, OTHER_NAME, "<p>Early</p>"))}
        self.assertEqual(self.run_at(NOW + 660), 1)
        jql = [b[0].args["jql"] for b in self.reader.batches if b and b[0].label == TICKETS][-1]
        minutes = int(re.search(r'updated >= "-(\d+)m"', jql).group(1))
        self.assertLessEqual(minutes, 6)         # from the page's end, not from the catch-up's start
        st = outside._load_state(self.SID)
        self.assertEqual((st["positions"][TICKETS]["at"], st["positions"][TICKETS]["full"]), (NOW + 660, False))
        self.assertNotIn("page", st["memory"])

    def test_a_full_page_within_the_overlap_still_hands_on_the_next_pages_events(self):
        # Review 2 (a): the page lies within the frame's 5-minute overlap, so
        # the frame hands back its own position (past the page's end) next
        # time; the page must still count. (Two checks first: a new source
        # reads nothing from before its first check.)
        self.assertEqual(self.run_at(NOW - 600), 0)
        self.assertEqual(self.run_at(NOW), 0)

        def listed_late(keys_times):
            self.tickets = [fx.issue(k, k, at, NOW - 86400) for k, at in keys_times]
            self.comments = {k: fx.comments_answer(fx.comment(int(at), at, OTHER_NAME, f"<p>{k}</p>"))
                             for k, at in keys_times}
        first = [(f"ABC-{300 + i}", NOW - 250 + i * 10) for i in range(jc.PAGE_SIZE)]
        rest = [(f"ABC-{400 + i}", NOW - 50 + i * 10) for i in range(5)]
        listed_late(first)
        self.assertEqual(self.run_at(NOW + 60), jc.PAGE_SIZE)
        listed_late(rest)
        self.assertEqual(self.run_at(NOW + 120), 5)          # was 0: their comments are before NOW
        jql = [b[0].args["jql"] for b in self.reader.batches if b and b[0].label == TICKETS][-1]
        minutes = int(re.search(r'updated >= "-(\d+)m"', jql).group(1))
        self.assertEqual(minutes, 4)                          # from the page's end, NOW - 60
        self.assertNotIn("page", outside._load_state(self.SID)["memory"])

    def test_a_catch_up_after_a_gap_past_the_limit_keeps_its_health_note(self):
        # Review 2 (b): each catch-up check starts below the 3-day limit again
        # (the position stays back); the gap note must stay the first one.
        self.assertEqual(self.run_at(NOW), 0)
        back = NOW + 4 * 86400
        self.tickets = [fx.issue(f"ABC-{500 + i}", "x", back - 3600 + i * 30, NOW - 86400)
                        for i in range(jc.PAGE_SIZE)]
        self.run_at(back)
        gap = outside._load_state(self.SID)["gap"]
        self.assertEqual((gap["from"], gap["to"]), (NOW, back))
        self.tickets = [fx.issue("ABC-600", "y", back - 60, NOW - 86400)]
        self.run_at(back + 60)
        st = outside._load_state(self.SID)
        self.assertEqual(st["gap"], gap)
        self.assertFalse(st["positions"][TICKETS]["full"])

    def test_a_sleep_during_a_catch_up_ends_its_page_and_says_so(self):
        # Review 4: a catch-up after a gap past the 3-day limit, then the
        # machine sleeps 2 days: the frame cuts the start to the new limit.
        # The old page no longer counts (nothing older than the limit is
        # handed on) and the health line names the new stop.
        self.assertEqual(self.run_at(NOW), 0)
        back = NOW + 4 * 86400
        first_limit = back - 3 * 86400
        self.tickets = [fx.issue(f"ABC-{700 + i}", "x", first_limit + 600 + i * 30, NOW - 86400)
                        for i in range(jc.PAGE_SIZE)]
        self.run_at(back)
        self.assertIn("page", outside._load_state(self.SID)["memory"])
        later = back + 2 * 86400
        at = first_limit + 600 + jc.PAGE_SIZE * 30
        self.tickets = [fx.issue(f"ABC-{800 + i}", "y", at + i, NOW - 86400) for i in range(5)]
        self.comments = {f"ABC-{800 + i}": fx.comments_answer(fx.comment(900 + i, at + i, OTHER_NAME, "<p>Old</p>"))
                         for i in range(5)}
        self.assertEqual(self.run_at(later), 0)
        st = outside._load_state(self.SID)
        self.assertEqual((st["gap"]["from"], st["gap"]["to"], st["gap"]["before"]),
                         (back, later, later - 3 * 86400))
        self.assertNotIn("page", st["memory"])
        self.assertEqual(jira_source.PAGE_OVERLAP_S, outside.OVERLAP_S)

    def test_catch_up_after_the_machine_slept(self):
        self.assertEqual(self.run_at(NOW), 0)
        self.tickets = [fx.issue("ABC-12", "Fix the price feed", NOW + 3 * 3600, NOW - 86400)]
        self.comments = {"ABC-12": fx.comments_answer(
            fx.comment(1, NOW + 3 * 3600, OTHER_NAME, "<p>While you slept</p>"))}
        self.assertEqual(self.run_at(NOW + 5 * 3600), 1)
        jqls = [b[0].args["jql"] for b in self.reader.batches if b and b[0].label == TICKETS]
        # Back to the last check at least (the frame adds its own overlap).
        minutes = int(re.search(r'updated >= "-(\d+)m"', jqls[-1]).group(1))
        self.assertGreaterEqual(minutes, 301)
        self.assertLess(minutes, 320)


class OnThePageTest(unittest.TestCase):
    """No page work in #14 (PO, P28): Jira is not offered under Add input
    source; a Jira source is set up in the sources file."""

    def test_jira_is_listed_as_later_and_cannot_be_added_from_the_page(self):
        cat = {t["kind"]: t for t in sources.catalog()}
        self.assertFalse(cat["jira"]["ready"])
        self.assertIn("jira", sources.TYPES)
        with self.assertRaises(outside.Refused) as ctx:
            outside.add_source("jira")
        self.assertEqual(str(ctx.exception), "Jira comes later: it cannot be added yet")


class RoutingTest(unittest.TestCase):
    """Step 1b: a ticket's epic, component or project listed by one row."""

    def message(self, epic="ABC-10", components=("Prices",), project="ABC"):
        t = fx.issue("ABC-12", "Fix the price feed", SINCE + 60, SINCE + 10, epic=(epic, "E") if epic else None,
                     components=components)
        got, _, src = check(answers([t]))
        m = by_event(got.messages)["created"][0]
        if project != "ABC":
            m.details["project"] = project
        return src, m

    def route(self, rows, **kw):
        src, m = self.message(**kw)
        cols = outside_route._place_columns("jira", src.route_fields())
        return outside_route._by_place(src, m, cols, outside_route._place_owners(rows, cols))

    def test_the_epic_decides_first(self):
        got = self.route({"p1": {"epics": ["ABC-10"]}, "p2": {"components": ["Prices"]}})
        self.assertEqual(got, {"to": "p1", "how": outside_route.BY_PLACE, "why": "Jira epic ABC-10"})

    def test_a_clash_falls_through_to_the_next_column(self):
        got = self.route({"p1": {"epics": ["abc-10"]}, "p2": {"epics": ["ABC-10"], "components": ["prices"]}})
        self.assertEqual(got["to"], "p2")
        self.assertEqual(got["why"], "Jira component Prices")

    def test_the_project_by_key_or_name(self):
        self.assertEqual(self.route({"p3": {"projects": ["ABC"]}}, epic=None, components=())["why"],
                         "Jira project ABC")
        self.assertEqual(self.route({"p3": {"projects": ["Made-up project"]}}, epic=None, components=())["to"],
                         "p3")

    def test_nothing_listed_decides_nothing(self):
        self.assertIsNone(self.route({"p1": {"epics": ["ABC-99"]}}))

    def test_the_place_comes_after_a_channel_and_before_thread_memory(self):
        _, m = self.message()
        mem = {"threads": {"ABC-12": {"to": "p9", "how": "model"}}}
        names = {"p1": "One", "p9": "Nine"}
        by_place = {"to": "p1", "how": outside_route.BY_PLACE, "why": "Jira epic ABC-10"}
        self.assertEqual(outside_route._decide(m, {}, {}, mem, names, None, by_place)["to"], "p1")
        self.assertEqual(outside_route._decide(m, {}, {}, mem, names, None, None)["to"], "p9")

    def test_the_work_items_of_a_message(self):
        src, m = self.message()
        self.assertEqual(src.work_items(m), ([("ticket", "ABC-12")], [("ticket", "ABC-10")]))
        m.text = "Needs https://github.com/made-up/example-repo/pull/8 first"
        self.assertEqual(outside_route.message_items(m, ()),
                         ([("ticket", "ABC-12"), ("pr", "example-repo#8")], [("ticket", "ABC-10")]))


class WorkItemsTest(test_outside_items._Hub):
    """#13's work items: a Jira event's ticket (and, after it, its epic)
    routes it; a routed event's ticket joins its row; ticket states reach
    the routing table's items through the frame."""
    ALPHA, BETA = test_outside_items.ALPHA, test_outside_items.BETA

    def jira_message(self, text=None):
        t = fx.issue("ABC-12", "Fix the price feed", SINCE + 60, SINCE - 86400)
        d = fx.detail_answer("ABC-12", [fx.history(3, SINCE + 60, (OTHER, OTHER_NAME),
                                                   fx.status_item("To Do", "In Progress"))])
        got, _, _ = check(answers([t], {"ABC-12": d}))
        [m] = got.messages
        if text is not None:
            m.text = text
        return m

    def decide(self, m):
        [(e, dec)] = outside_route.decide(test_outside_items.SID, [test_outside_items.entry(m)])
        return e, dec

    def test_the_ticket_routes_its_events(self):
        self.add(self.ALPHA, "ABC-12")
        _, dec = self.decide(self.jira_message())
        self.assertEqual((dec["to"], dec["how"], dec["why"]), (self.ALPHA, outside_route.BY_ITEM, "ticket ABC-12"))

    def test_the_epic_counts_only_when_no_row_lists_the_ticket(self):
        self.add(self.BETA, "ABC-10")
        _, dec = self.decide(self.jira_message())
        self.assertEqual((dec["to"], dec["how"]), (self.BETA, outside_route.BY_ITEM))
        self.add(self.ALPHA, "ABC-12")
        _, dec = self.decide(self.jira_message())
        self.assertEqual(dec["to"], self.ALPHA)

    def test_a_routed_event_adds_its_ticket_to_its_row(self):
        # No row knows the project ABC, but Jira says ABC-12 is a ticket; its epic is not added.
        e, dec = self.decide(self.jira_message())
        self.assertEqual(dec["how"], outside_route.NEEDS_MODEL)
        outside_route.record(test_outside_items.SID,
                             [(e, {"to": self.ALPHA, "how": outside_route.BY_MODEL, "why": "made-up"})], now=NOW)
        self.assertEqual(self.items(self.ALPHA), [("ABC-12", "routed")])
        # A ticket only named in an event's text still needs a project the rows know.
        e, _ = self.decide(self.jira_message("Blocked by XYZ-3"))
        outside_route.record(test_outside_items.SID,
                             [(e, {"to": self.ALPHA, "how": outside_route.BY_MODEL, "why": "made-up"})], now=NOW)
        self.assertNotIn(("XYZ-3", "routed"), self.items(self.ALPHA))


class TicketStateTest(unittest.TestCase):
    def test_states_from_the_status(self):
        def state(name, category=""):
            return jira_source.ticket_state(jira_source.Ticket(key="ABC-1", status=name, category=category))
        self.assertEqual(state("Released"), "released")
        self.assertEqual(state("Won't Do"), "closed")
        self.assertEqual(state("Done"), "done")
        self.assertEqual(state("Verified", "done"), "done")
        self.assertEqual(state("Ready for Release", "done"), "open")
        self.assertEqual(state("In Progress", "indeterminate"), "open")

    def test_a_check_reports_a_state_once_with_when_it_was_reached(self):
        t = fx.issue("ABC-50", "Shipped", SINCE + 60, SINCE - 86400, status="Released")
        d = fx.detail_answer("ABC-50", [fx.history(4, SINCE - 3000, (OTHER, OTHER_NAME),
                                                   fx.status_item("In Review", "Released"))])
        got, _, _ = check(answers([t], {"ABC-50": d}))
        self.assertEqual(got.item_states, [("ABC-50", "released", SINCE - 3000)])
        self.assertEqual(got.memory["tickets"]["ABC-50"]["state"], "released")
        # It changes again (a comment), same state: not reported again.
        t2 = fx.issue("ABC-50", "Shipped", SINCE + 120, SINCE - 86400, status="Released")
        again, _, _ = check(answers([t2], {"ABC-50": d}), memory=got.memory)
        self.assertEqual(again.item_states, [])

    def test_open_is_reported_only_for_a_reopened_ticket(self):
        # Review 1: a new or moving open ticket reports nothing (the work
        # items' 30-day quiet rule needs that); a finished one that opens again does.
        t = fx.issue("ABC-51", "New work", SINCE + 60, SINCE - 86400, status="In Progress")
        got, _, _ = check(answers([t]))
        self.assertEqual(got.item_states, [])
        self.assertEqual(got.memory["tickets"]["ABC-51"]["state"], "open")
        moved = fx.issue("ABC-51", "New work", SINCE + 120, SINCE - 86400, status="In Review")
        got, _, _ = check(answers([moved]), memory=got.memory)
        self.assertEqual(got.item_states, [])
        done = fx.issue("ABC-51", "New work", SINCE + 180, SINCE - 86400, status="Done")
        got, _, _ = check(answers([done]), memory=got.memory)
        self.assertEqual([s[:2] for s in got.item_states], [("ABC-51", "done")])
        reopened = fx.issue("ABC-51", "New work", SINCE + 240, SINCE - 86400, status="Reopened")
        got, _, _ = check(answers([reopened]), memory=got.memory)
        self.assertEqual([s[:2] for s in got.item_states], [("ABC-51", "open")])

    def test_the_frame_hands_the_states_to_the_routing_tables_items(self):
        frame = ThroughTheFrameTest("test_each_event_once")
        frame.setUp()
        self.addCleanup(frame.doCleanups)
        frame.tickets = [fx.issue("ABC-12", "Fix the price feed", NOW + 100, NOW - 86400, status="Closed")]
        with mock.patch.object(outside_items, "note_ticket_state") as noted:
            frame.run_at(NOW + 300)
        noted.assert_called_once_with("ABC-12", "closed", NOW + 100)


class ShownTest(unittest.TestCase):
    def setUp(self):
        t = fx.issue("ABC-12", "Fix the price feed", SINCE + 60, SINCE + 10, components=("Prices",))
        got, _, _ = check(answers([t]))
        self.m = by_event(got.messages)["created"][0]

    def test_the_inbox_line_says_on_which_ticket(self):
        p = outside_deliver.part({"key": self.m.key, "to": "p1", "why": "Jira epic ABC-10",
                                  "message": self.m.to_dict()})
        self.assertEqual(p["kind"], "jira")
        self.assertEqual(p["where"], "on ABC-12 Fix the price feed")
        self.assertEqual(outside_deliver.label_of("jira"), "Jira")

    def test_the_po_reads_the_ticket_details(self):
        v = outside_deliver._view(self.m.to_dict())
        self.assertEqual(v["placeKind"], "ticket")
        self.assertEqual(v["details"]["ticket"], "ABC-12")
        self.assertEqual(v["details"]["epic"], "ABC-10")
        self.assertEqual(v["details"]["components"], ["Prices"])

    def test_the_model_is_told_the_ticket_and_its_epic(self):
        self.assertEqual(outside_model._where(self.m),
                         "Jira ticket ABC-12 Fix the price feed (epic ABC-10; components Prices)")

    def test_a_message_keeps_its_details_through_a_dict(self):
        self.assertEqual(Message.from_dict(self.m.to_dict()).details, self.m.details)


class PlainTextTest(unittest.TestCase):
    def test_markup_goes_and_mentions_stay_as_names(self):
        self.assertEqual(jira_source.plain(f"<p>Hi {fx.mention(OTHER, OTHER_NAME)},</p><p>a &amp; b</p>"),
                         f"Hi @{OTHER_NAME},\na & b")
        self.assertTrue(jira_source.plain("x" * 5000).endswith(" …"))

    def test_jira_times(self):
        self.assertEqual(jira_source.ts_of("2026-11-16T11:50:48.744+0100"),
                         jira_source.ts_of("2026-11-16T10:50:48.744+0000"))
        self.assertIsNone(jira_source.ts_of("yesterday"))


if __name__ == "__main__":
    unittest.main()
