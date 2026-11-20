"""Work items route messages (round 2 step 2): finding tickets and pull
requests in a text (work_items.py); a message naming an item one row lists
goes to that initiative before any other rule; items of two rows decide
nothing and say so; rows fill themselves from the hub's tasks and from
routed messages, never taking an item another row has; finished items leave
(outside_items.py). Made-up ids, names and links only (the repo is public)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import outside
import outside_items as oi
import outside_route as route
import sources
import work_items as wi
from sources.base import Author, Message, Place, Thread
from sources.fake import FakeSource

DAY = 86400
WED = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
SID = "made-up"
ALPHA, BETA, GAMMA = "p-alpha", "p-beta", "p-gamma"
CEO = {"kind": "ceo"}


def msg(n: int, text: str, at: float = WED, channel: str = "C000TEST01", thread: str = "",
        head: str = "") -> Message:
    th = None
    if thread:
        th = Thread(id=f"{channel}:{thread}", first=thread == str(n), head=head)
    return Message(key=f"fake:example:{channel}:{n}", source="fake",
                   place=Place(id=channel, name="made-up-one", kind="channel"),
                   author=Author(id="U000TEST02", name="Made-up Person"), at=float(at), text=text,
                   link=f"https://example.slack.com/archives/{channel}/p{n}", thread=th)


def entry(m: Message) -> dict:
    return {"key": m.key, "message": m.to_dict(), "readAt": WED, "decideOnly": False}


class Finding(unittest.TestCase):
    def test_tickets_pull_requests_and_links(self):
        text = ("see ABC-12 and https://github.com/made-up/Example-Repo/pull/34, also "
                "<https://made-up.example/browse/abc-7|this one> and other-repo#5; "
                "made-up/third#6 and https://made-up.atlassian.net/jira/software?selectedIssue=XYZ-9")
        # Links first (Slack's <link|words> before plain ones), then the rest in order.
        self.assertEqual(wi.find(text), [
            ("ticket", "ABC-7"), ("pr", "example-repo#34"), ("ticket", "XYZ-9"),
            ("ticket", "ABC-12"), ("pr", "other-repo#5"), ("pr", "third#6")])
        self.assertEqual(wi.pr_links(text), [("made-up", "example-repo", 34)])

    def test_words_that_look_like_tickets_are_not(self):
        self.assertEqual(wi.find("UTF-8, SHA-256, ISO-8601 and abc-12 in lower case"), [])
        self.assertEqual(wi.find("GPT-4 and ABC-1", prefixes={"ABC"}), [("ticket", "ABC-1")])

    def test_several_numbers_and_a_bare_repo_number_only_for_known_repos(self):
        self.assertEqual(wi.find("ABC-1/2 done"), [("ticket", "ABC-1"), ("ticket", "ABC-2")])
        self.assertEqual(wi.find("example-repo 14/15 merged"), [])
        self.assertEqual(wi.find("example-repo 14/15 merged", repos={"example-repo"}),
                         [("pr", "example-repo#14"), ("pr", "example-repo#15")])
        self.assertEqual(wi.find("example-repo PR #16", repos={"example-repo"}),
                         [("pr", "example-repo#16")])

    def test_names_of_branches_and_folders(self):
        self.assertEqual(wi.find_in_name("abc_123_fix_things", {"ABC"}), [("ticket", "ABC-123")])
        self.assertEqual(wi.find_in_name("abc_123_fix_things", set()), [])
        self.assertEqual(wi.label("pr", "example-repo#12"), "pull request example-repo 12")
        self.assertEqual(wi.label("ticket", "ABC-1"), "ticket ABC-1")


class _Hub(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-items-")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.projects = [{"id": ALPHA, "name": "Alpha", "key": "AL"},
                         {"id": BETA, "name": "Beta", "key": "BE", "poRoomId": "room-po-beta"}]
        self.rooms: list[dict] = []
        hub = types.SimpleNamespace(
            DASHBOARD_DIR=self.state, load_projects=lambda: list(self.projects),
            chatroom=types.SimpleNamespace(list_rooms=lambda copy_rooms=False: list(self.rooms)))
        self.gh_calls: list[list[str]] = []
        self.gh = {}
        for p in (mock.patch.object(outside, "_d", None),
                  mock.patch.object(route, "_d", None),
                  mock.patch.object(oi, "_d", None),
                  mock.patch.object(oi, "_ORGS", {}),
                  mock.patch.object(oi, "_ORGS_READ", [0.0]),
                  mock.patch.object(oi, "_LAST", 0.0),
                  mock.patch.object(oi, "GH", self.fake_gh),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None),
                  mock.patch.object(outside, "_log"),
                  mock.patch.object(route, "_log"),
                  mock.patch.object(oi, "_log"),
                  mock.patch.dict(sources.TYPES, {"fake": FakeSource})):
            p.start()
            self.addCleanup(p.stop)
        outside.bind(hub)
        route.bind(hub)
        oi.bind(hub)
        outside.reset()
        self.addCleanup(outside.reset)
        outside.set_source(SID, type="fake", mode="on", settings={"account": "made-up"})

    def fake_gh(self, args):
        self.gh_calls.append(args)
        n, repo = args[2], args[4]
        return json.dumps(self.gh[f"{repo}#{n}"])

    def items(self, pid: str) -> list[tuple[str, str]]:
        r = next(r for r in route.one_table()["rows"] if r["projectId"] == pid)
        return [(i["key"], i["from"]) for i in r["items"]]

    def add(self, pid: str, *keys: str, at: float = WED):
        route.set_initiative(pid, {"addItems": list(keys)}, CEO, now=at)


class Routing(_Hub):
    def test_an_item_one_row_lists_goes_there_before_the_channel(self):
        self.add(ALPHA, "ABC-1")
        route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)
        [(_, dec)] = route.decide(SID, [entry(msg(1, "about ABC-1, the made-up fix"))])
        self.assertEqual((dec["to"], dec["how"]), (ALPHA, route.BY_ITEM))
        self.assertIn("ticket ABC-1", dec["why"])
        [(_, dec)] = route.decide(SID, [entry(msg(2, "nothing named here"))])
        self.assertEqual((dec["to"], dec["how"]), (BETA, route.BY_CHANNEL))

    def test_a_reply_naming_nothing_follows_its_threads_first_message(self):
        self.add(ALPHA, "example-repo#5")
        m = msg(3, "looks good", thread="1", head="please look at example-repo#5")
        [(_, dec)] = route.decide(SID, [entry(m)])
        self.assertEqual((dec["to"], dec["how"]), (ALPHA, route.BY_ITEM))
        self.assertIn("thread's first message", dec["why"])
        # Its own words win over the thread's first message.
        self.add(BETA, "ABC-2")
        m = msg(4, "and ABC-2 too", thread="1", head="please look at example-repo#5")
        [(_, dec)] = route.decide(SID, [entry(m)])
        self.assertEqual(dec["to"], BETA)

    def test_items_of_two_rows_decide_nothing_and_the_log_says_why(self):
        self.add(ALPHA, "ABC-1")
        self.add(BETA, "ABC-2")
        route.set_row(SID, BETA, {"channels": ["C000TEST01"]}, CEO)
        m = msg(5, "ABC-1 and ABC-2 both")
        [(e, dec)] = route.decide(SID, [entry(m)])
        self.assertEqual((dec["to"], dec["how"]), (BETA, route.BY_CHANNEL))
        route.record(SID, [(e, dec)], now=WED)
        [line] = route.log_lines()
        self.assertIn("work items in two rows", line["why"])

    def test_an_item_two_rows_list_in_a_file_edited_by_hand_belongs_to_nobody(self):
        (self.state / "outside" / "routing.json").write_text(json.dumps({"version": 1, "rows": {
            ALPHA: {"items": [{"kind": "ticket", "key": "ABC-1"}]},
            BETA: {"items": [{"kind": "ticket", "key": "ABC-1"}]}}}))
        [(_, dec)] = route.decide(SID, [entry(msg(6, "ABC-1 again"))])
        self.assertEqual(dec["how"], route.NEEDS_MODEL)
        # The hub never adds it anywhere either.
        self.assertEqual(route.add_items_auto({GAMMA: [("ticket", "ABC-1", "routed")]}, "x", WED), {})


class Filling(_Hub):
    def test_a_routed_message_adds_its_unlisted_items_to_its_row(self):
        self.add(ALPHA, "ABC-1")
        self.add(BETA, "ABC-9")
        m = msg(7, "ABC-1 needs ABC-3 and https://github.com/made-up/example-repo/pull/8; "
                   "GPT-4 wrote it; ABC-9 is Beta's")
        [(e, dec)] = route.decide(SID, [entry(m)])
        self.assertEqual(dec["how"], route.NEEDS_MODEL, "items of two rows decide nothing")
        route.record(SID, [(e, {"to": ALPHA, "how": route.BY_MODEL, "why": "made-up fix"})],
                     now=WED + 60)
        self.assertEqual(self.items(ALPHA), [("ABC-1", "ceo"), ("example-repo#8", "routed"),
                                             ("ABC-3", "routed")])
        self.assertEqual(self.items(BETA), [("ABC-9", "ceo")], "never taken from another row")
        st = route.items_state()
        self.assertEqual(st["orgs"], {"example-repo": "made-up"})
        self.assertEqual(st["seen"]["ABC-3"], float(WED))
        [h] = [h for h in route.flat_history(ALPHA) if h["by"] == route.HUB]
        self.assertEqual(h["note"], "named in a message routed here")

    def test_the_hub_never_takes_an_item_another_row_has_and_notes_it(self):
        self.add(BETA, "ABC-1")
        got = route.add_items_auto({ALPHA: [("ticket", "ABC-1", "task #3"), ("ticket", "ABC-2", "task #3")]},
                                   "named by its tasks", WED)
        self.assertEqual(got, {ALPHA: ["ABC-2"]})
        [n] = route.one_table()["itemNotes"]
        self.assertEqual((n["key"], n["wantedName"], n["keptName"], n["from"]),
                         ("ABC-1", "Alpha", "Beta", "task #3"))
        # Noted once; gone once Beta's row no longer lists it.
        route.add_items_auto({ALPHA: [("ticket", "ABC-1", "task #3")]}, "named by its tasks", WED)
        self.assertEqual(len(route.one_table()["itemNotes"]), 1)
        route.set_initiative(BETA, {"removeItems": ["ABC-1"]}, CEO)
        self.assertEqual(route.one_table()["itemNotes"], [])

    def test_send_elsewhere_moves_the_messages_items_with_it(self):
        self.add(ALPHA, "ABC-1", "ABC-4")
        self.add(BETA, "ABC-8")
        m = msg(9, "ABC-1 is done, ABC-5 next; ABC-8 is not ours")
        e = entry(m)
        route.record(SID, [(e, {"to": ALPHA, "how": route.BY_CHANNEL, "why": "x"})], now=WED)
        self.assertIn(("ABC-5", "routed"), self.items(ALPHA))
        self.projects.append({"id": GAMMA, "name": "Gamma"})
        got = route.correct(SID, m.key, GAMMA, CEO, now=WED + 60, message=m.to_dict())
        self.assertEqual(sorted(got["items"]), ["ABC-1", "ABC-5"])
        self.assertEqual(self.items(ALPHA), [("ABC-4", "ceo")])
        self.assertEqual(self.items(GAMMA), [("ABC-1", "send elsewhere"), ("ABC-5", "send elsewhere")])
        self.assertEqual(self.items(BETA), [("ABC-8", "ceo")], "a third row keeps its own")
        notes = [h["note"] for h in route.flat_history() if h["at"] == WED + 60]
        self.assertEqual(sorted(notes), ["came with a message (Send elsewhere) from Alpha's row",
                                         "moved to Gamma's row with a message (Send elsewhere)"])

    def test_send_elsewhere_to_a_full_row_moves_only_what_fits(self):
        # Review 1, B: an item that does not fit stays in its row.
        self.add(ALPHA, "ABC-1")
        self.projects.append({"id": GAMMA, "name": "Gamma"})
        self.add(GAMMA, *[f"XYZ-{n}" for n in range(1, route.ITEMS_MAX + 1)])
        m = msg(9, "ABC-1 is done")
        route.record(SID, [(entry(m), {"to": ALPHA, "how": route.BY_CHANNEL, "why": "x"})], now=WED)
        got = route.correct(SID, m.key, GAMMA, CEO, now=WED + 60, message=m.to_dict())
        self.assertEqual(got["items"], [])
        self.assertEqual(self.items(ALPHA), [("ABC-1", "ceo")])

    def test_tasks_name_the_items_of_their_initiative(self):
        self.projects.append({"id": GAMMA, "name": "Gamma", "key": "GA"})
        self.rooms = [
            {"id": "room-a1", "projectId": ALPHA, "no": 3, "createdAt": WED - 9 * DAY,
             "title": "Fix ABC-12 rounding", "spec": "",
             "workspace": {"branch": "sess/AL-3-abc_14_more"},
             "lastReport": {"text": "opened https://github.com/made-up/example-repo/pull/21"}},
            {"id": "room-g1", "projectId": GAMMA, "no": 1, "createdAt": WED - 5 * DAY,
             "title": "Also ABC-12", "spec": "Follow-up of AL-3; see ABC-15", "workflow": "done"},
            {"id": "room-a2", "projectId": ALPHA, "no": 4, "createdAt": WED - 2 * DAY,
             "title": "Notes", "spec": "ABC-20 ABC-21 ABC-22 ABC-23 ABC-24 ABC-25"},
            {"id": "room-po-beta", "projectId": BETA, "no": 9, "title": "PO with ABC-30"},
            {"id": "room-x", "projectId": "gone", "no": 1, "title": "ABC-31"},
        ]
        oi.scan_tasks(WED)
        self.assertEqual(self.items(ALPHA), [("ABC-12", "task #3"), ("example-repo#21", "task #3"),
                                             ("ABC-14", "task #3")])
        self.assertEqual(self.items(GAMMA), [("ABC-15", "task #1")],
                         "the older task keeps ABC-12; AL-3 is a task, not a ticket")
        self.assertEqual(self.items(BETA), [], "a PO's room names nothing")
        st = route.items_state()
        self.assertEqual([(n["key"], n["wanted"], n["kept"]) for n in st["notes"]],
                         [("ABC-12", GAMMA, ALPHA)])
        self.assertEqual(st["orgs"], {"example-repo": "made-up"})
        self.assertIn("ABC-12", st["seen"])
        self.assertNotIn("ABC-15", st["seen"], "a done task does not keep its items alive")
        # A second run adds nothing and notes nothing again.
        self.assertEqual(oi.scan_tasks(WED + 600), {})
        self.assertEqual(len(route.items_state()["notes"]), 1)


class Dropping(_Hub):
    def test_a_merged_pull_request_leaves_14_days_after_its_merge(self):
        self.add(ALPHA, "https://github.com/made-up/example-repo/pull/3", "example-repo#4", "other#5",
                 at=WED - 30 * DAY)
        route.save_items_state({**route.items_state(), "orgs": {"example-repo": "made-up"}})
        merged = "2026-11-01T10:00:00Z"
        self.gh = {"made-up/example-repo#3": {"state": "MERGED", "mergedAt": merged},
                   "made-up/example-repo#4": {"state": "OPEN"}}
        now = oi._when(merged) + 15 * DAY
        self.assertEqual(oi.drop_finished(now), 1)
        self.assertEqual([k for k, _f in self.items(ALPHA)], ["example-repo#4", "other#5"])
        self.assertEqual(len(self.gh_calls), 2, "no organisation known for other: not read")
        self.assertEqual(self.gh_calls[0][:2], ["pr", "view"])
        [h] = [h for h in route.flat_history(ALPHA) if h["by"] == route.HUB]
        self.assertEqual(h["note"], "finished: pull request example-repo 3 merged 15 days ago")
        # An open one is read again only after 6 hours.
        oi.drop_finished(now + 3600)
        self.assertEqual(len(self.gh_calls), 2)
        oi.drop_finished(now + oi.PR_RECHECK_S)
        self.assertEqual(len(self.gh_calls), 3)

    def test_a_pull_request_merged_lately_stays(self):
        self.add(ALPHA, "https://github.com/made-up/example-repo/pull/3")
        self.gh = {"made-up/example-repo#3": {"state": "MERGED", "mergedAt": "2026-11-15T10:00:00Z"}}
        self.assertEqual(oi.drop_finished(WED), 0)
        self.assertEqual(len(self.items(ALPHA)), 1)

    def test_a_ticket_nobody_names_for_30_days_leaves(self):
        self.add(ALPHA, "ABC-1", "ABC-2", "epic ABC-3", at=WED - 40 * DAY)
        st = route.items_state()
        st["seen"]["ABC-2"] = WED - 3 * DAY
        route.save_items_state(st)
        self.assertEqual(oi.drop_finished(WED), 1)
        self.assertEqual([k for k, _f in self.items(ALPHA)], ["ABC-2", "ABC-3"],
                         "named lately, and an epic waits for its state from Jira")

    def test_a_ticket_with_a_state_from_jira(self):
        self.add(ALPHA, "ABC-1", "ABC-2", "epic ABC-3", at=WED - 40 * DAY)
        oi.note_ticket_state("abc-1", "Closed", WED - 15 * DAY)
        oi.note_ticket_state("ABC-2", "open", WED - 40 * DAY)
        oi.note_ticket_state("ABC-3", "released", WED - 2 * DAY)
        self.assertEqual(oi.drop_finished(WED), 1)
        self.assertEqual([k for k, _f in self.items(ALPHA)], ["ABC-2", "ABC-3"])

    def test_a_finished_item_of_a_done_task_is_not_added_back_on_every_run(self):
        # Review 1, A: a done task's report keeps linking a pull request merged
        # long ago, and its title names a ticket nobody talks about.
        self.rooms = [{"id": "room-a1", "projectId": ALPHA, "no": 3, "createdAt": WED - 60 * DAY,
                       "title": "Fix ABC-12", "workflow": "done",
                       "lastReport": {"text": "https://github.com/made-up/example-repo/pull/21"}}]
        self.gh = {"made-up/example-repo#21": {"state": "MERGED", "mergedAt": "2026-10-01T10:00:00Z"}}
        oi.run(WED - 40 * DAY)
        for n in range(6):
            oi.run(WED + n * oi.EVERY_S)
        self.assertEqual(self.items(ALPHA), [])
        notes = [h["note"] for h in route.flat_history(ALPHA)]
        self.assertEqual(len(notes), 2, notes)
        self.assertEqual(notes[1], "named by its tasks")
        self.assertEqual(notes[0], "finished: ticket ABC-12 not named for 40 days; "
                                   "pull request example-repo 21 merged 47 days ago")
        self.assertEqual(len(self.gh_calls), 1, "merged is final: never read again")
        # A message naming the ticket again brings it back; the pull request does not come back.
        self.add(BETA, "ABC-99")            # so ABC is a ticket project the rows know
        m = msg(1, "ABC-12 again, and example-repo#21", at=WED + DAY)
        route.record(SID, [(entry(m), {"to": ALPHA, "how": route.BY_CHANNEL, "why": "x"})], now=WED + DAY)
        self.assertEqual(self.items(ALPHA), [("ABC-12", "routed")])
        # The PO can still add the pull request by hand.
        route.set_initiative(ALPHA, {"addItems": ["example-repo#21"]}, CEO, now=WED + DAY)
        self.assertIn(("example-repo#21", "ceo"), self.items(ALPHA))
        # ... and it stays 14 days from that add, not taken out at the next run.
        oi.run(WED + DAY + oi.EVERY_S)
        self.assertIn(("example-repo#21", "ceo"), self.items(ALPHA))
        oi.run(WED + DAY + oi.DONE_KEEP_S)
        self.assertNotIn(("example-repo#21", "ceo"), self.items(ALPHA))

    def test_reads_of_pull_requests_with_no_known_organisation_cost_nothing(self):
        # Review 1, C: only real ``gh`` reads count, and a failed one waits like an open one.
        self.add(ALPHA, *[f"other#{n}" for n in range(1, oi.GH_MAX + 5)], "example-repo#7")
        route.save_items_state({**route.items_state(), "orgs": {"example-repo": "made-up"}})
        oi.drop_finished(WED)
        self.assertEqual(self.gh_calls, [["pr", "view", "7", "--repo", "made-up/example-repo",
                                          "--json", "state,mergedAt,closedAt"]])
        self.assertEqual(route.items_state()["prs"]["example-repo#7"], {"state": "unknown", "checked": WED})
        oi.drop_finished(WED + 3600)
        self.assertEqual(len(self.gh_calls), 1, "a failed read waits 6 hours")
        oi.drop_finished(WED + oi.PR_RECHECK_S)
        self.assertEqual(len(self.gh_calls), 2)

    def test_what_the_hub_knows_of_items_in_no_row_is_forgotten_after_60_days(self):
        # Review 1, D.
        self.add(ALPHA, "ABC-1")
        st = route.items_state()
        st["seen"].update({"ABC-1": WED - 90 * DAY, "XYZ-1": WED - 90 * DAY, "XYZ-2": WED - DAY})
        st["tickets"]["XYZ-3"] = {"state": "closed", "at": WED - 70 * DAY}
        st["dropped"]["XYZ-1"] = WED - 80 * DAY
        route.save_items_state(st)
        oi.drop_finished(WED)
        st = route.items_state()
        self.assertEqual(sorted(st["seen"]), ["ABC-1", "XYZ-2"], "ABC-1 is in a row; XYZ-1 is old")
        self.assertEqual(st["tickets"], {})
        self.assertIn("XYZ-1", st["dropped"], "a finished item stays out")

    def test_the_background_run_needs_an_input_source(self):
        outside.remove_source(SID)
        with mock.patch.object(oi.threading, "Thread") as th:
            oi.maybe_tick(WED)
            th.assert_not_called()
        outside.set_source(SID, type="fake", mode="on", settings={"account": "made-up"})
        with mock.patch.object(oi.threading, "Thread") as th:
            oi.maybe_tick(WED)
            oi.maybe_tick(WED + 60)
            self.assertEqual(th.call_count, 1, "every 10 minutes at most")
        oi._LOCK.release()


class OrgsAreReadAgain(_Hub):
    """P439: the repo-to-organisation map is read again every hour, not once."""

    def test_a_changed_remote_is_seen_after_an_hour(self):
        repo = self.state / "made-up-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                        "https://github.com/made-up-org/widgets.git"], check=True)
        self.projects.append({"id": "proj-w", "name": "Widgets", "path": str(repo), "isGit": True})
        now = [1_000_000.0]
        with mock.patch.object(oi.time, "time", side_effect=lambda: now[0]):
            self.assertEqual(oi._project_orgs().get("widgets"), "made-up-org")
            subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin",
                            "https://github.com/other-made-up-org/widgets.git"], check=True)
            now[0] += 60
            self.assertEqual(oi._project_orgs().get("widgets"), "made-up-org")
            now[0] += oi.ORGS_EVERY_S
            self.assertEqual(oi._project_orgs().get("widgets"), "other-made-up-org")
            self.projects.pop()
            now[0] += oi.ORGS_EVERY_S + 1
            self.assertNotIn("widgets", oi._project_orgs())


if __name__ == "__main__":
    unittest.main()
