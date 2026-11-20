"""The outside sources setup file (outside_setup.py, #15): written the first
time from the table, read in code when plain and by a (fake) Haiku when
loose, every value Haiku returns checked, each section applied on its own,
problems written at the top without counting as a save, a Haiku failure
changes nothing, clashes and moves, an initiative with no section cleared,
check only changes nothing, an agent changes only its own section, and
routing-now.md. Names, ids and channels are made up (the repo is public)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import ensemble_tools
import outside
import outside_route as route
import outside_setup as setup

WED = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
ALPHA, BETA, GAMMA = "p-alpha", "p-beta", "p-gamma"
IDS = {"made-up-one": "C000TEST01", "made-up-two": "C000TEST02", "made-up-three": "C000TEST03"}


def agent(project: str) -> dict:
    return {"kind": "agent", "identity": "claude", "projectId": project, "agent": "claude",
            "role": "engineer", "roomId": "room-made-up"}


class SetupFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-setup-")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        self.projects = [{"id": ALPHA, "name": "Alpha"}, {"id": BETA, "name": "Beta Two"},
                         {"id": GAMMA, "name": "Gamma"}]
        hub = types.SimpleNamespace(DASHBOARD_DIR=self.state, load_projects=lambda: list(self.projects))
        self.lookups: list[list[str]] = []
        self.haiku: list[str] = []
        self.answer = None              # what the fake Haiku answers (a dict, text, or an exception)

        def lookup(names):
            self.lookups.append(list(names))
            return {n: IDS.get(n, "") for n in names}

        def run(prompt, timeout):
            self.haiku.append(prompt)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer if isinstance(self.answer, str) else json.dumps(self.answer)

        for p in (mock.patch.object(outside, "_d", None),
                  mock.patch.object(route, "_d", None),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None),
                  mock.patch.object(outside, "_log"),
                  mock.patch.object(route, "_log"),
                  mock.patch.object(setup, "_log"),
                  mock.patch.object(setup, "LOOKUP", lookup),
                  mock.patch.object(setup, "RUN", run)):
            p.start()
            self.addCleanup(p.stop)
        outside.bind(hub)
        route.bind(hub)
        outside.reset()
        setup.reset()
        self.addCleanup(outside.reset)
        self.addCleanup(setup.reset)
        outside.set_source("slack", type="slack", mode="on", settings={"reader": "connector"})
        outside.set_source("jira", type="jira", mode="decide", settings={})

    # -- helpers -----------------------------------------------------------
    def row(self, pid):
        return route._load_one()["rows"].get(pid) or route._empty_row()

    def write(self, body: str) -> str:
        text = "# Outside sources setup\n\n" + body
        setup.setup_path().parent.mkdir(parents=True, exist_ok=True)
        setup.setup_path().write_text(text, encoding="utf-8")
        return text

    def apply(self, body: str) -> dict:
        self.write(body)
        return setup.apply_file(now=WED)

    def problems(self) -> list[str]:
        return [x["text"] for x in setup.load_state()["problems"]]

    # -- the first file ----------------------------------------------------
    def test_the_first_file_is_written_from_the_table_with_channel_names(self):
        route.set_initiative(ALPHA, {"topic": "made-up pricing", "places": {
            "slack": {"channels": ["C000TEST01"]}, "jira": {"epics": ["ABC-1"]}},
            "addItems": ["ABC-12", "repo#4"]}, {"kind": "ceo"}, now=WED)
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        setup._remember_ids({"made-up-one": "C000TEST01"})
        setup.tick(now=WED, background=False)
        text = setup.setup_path().read_text()
        self.assertIn("- Slack: on\n- Jira: decide only", text)
        self.assertIn("## Alpha\nTopic: made-up pricing\nSlack channels: #made-up-one\n"
                      "Jira: epic ABC-1\nWork items: ABC-12, repo#4", text)
        self.assertNotIn("ABC-99", text)               # filled by the hub: not listed
        self.assertNotIn("## Beta Two", text)          # nothing set: only named in a comment
        self.assertIn("Beta Two, Gamma", text)
        self.assertEqual(self.haiku, [])               # the first file counts as applied
        self.assertTrue(setup.now_path().exists())

    # -- plain sections, read in code --------------------------------------
    def test_a_plain_file_applies_without_haiku_and_keeps_the_hubs_items(self):
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        res = self.apply("## Sources\n- Slack: on\n- Jira: pause\n\n"
                         "## Alpha\nTopic: made-up pricing\nSlack channels: #made-up-one, #Made-Up-Two\n"
                         "Jira: epic abc-1, component Made Up\nWork items: ABC-12, made-up-thing 14\n")
        self.assertEqual(self.haiku, [])
        self.assertEqual(res["problems"], [])
        r = self.row(ALPHA)
        self.assertEqual(r["topic"], "made-up pricing")
        self.assertEqual(r["places"]["slack"]["channels"], ["C000TEST01", "C000TEST02"])
        self.assertEqual(r["places"]["jira"]["epics"], ["ABC-1"])
        self.assertEqual(r["places"]["jira"]["components"], ["Made Up"])
        self.assertEqual({(i["key"], i["from"]) for i in r["items"]},
                         {("ABC-12", "setup file"), ("made-up-thing#14", "setup file"), ("ABC-99", "task #7")})
        self.assertTrue(outside.source_config("jira")["paused"])
        h = route.history(None, ALPHA)[0]
        self.assertEqual(h["by"], {"kind": "setup"})
        self.assertEqual(self.lookups, [["made-up-one", "made-up-two"]])
        # Known names are not looked up again.
        self.apply("## Alpha\nTopic: other words\nSlack channels: #made-up-one\n")
        self.assertEqual(len(self.lookups), 1)
        self.assertIn("No problems on the last save.", setup.setup_path().read_text())

    # -- the Not line (#16) -------------------------------------------------
    def test_a_not_line_takes_hub_items_out_and_the_hub_does_not_add_them_back(self):
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7"), ("pr", "made-up-repo#52", "routed"),
                                      ("ticket", "ABC-98", "task #7")]}, "made up", now=WED)
        res = self.apply("## Alpha\nTopic: made-up pricing\nWork items: ABC-12\nNot: ABC-99, made-up-repo 52\n")
        self.assertEqual(self.haiku, [])
        self.assertEqual(res["problems"], [])
        r = self.row(ALPHA)
        self.assertEqual({i["key"] for i in r["items"]}, {"ABC-12", "ABC-98"})
        self.assertEqual(sorted(route.not_keys(r)), ["ABC-99", "made-up-repo#52"])
        self.assertIn("Alpha: Not: ABC-99, made-up-repo#52 added (the hub does not add them back)", res["changes"])
        self.assertIn("Alpha: work items ABC-99, made-up-repo#52 taken out", res["changes"])
        # The hub finds them again (a task, a routed message): it does not add them back to Alpha.
        added = route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7"), ("pr", "made-up-repo#52", "routed")]},
                                     "made up", now=WED + 60)
        self.assertEqual(added, {})
        self.assertNotIn("ABC-99", {i["key"] for i in self.row(ALPHA)["items"]})
        # Another row may still get it.
        self.assertEqual(route.add_items_auto({BETA: [("ticket", "ABC-99", "task #8")]}, "made up", now=WED + 60),
                         {BETA: ["ABC-99"]})
        # Applying the same file again changes nothing; routing-now shows the Not list.
        res = setup.apply_file(now=WED + 120)
        self.assertEqual(res["changes"], [])
        self.assertIn("- Not (taken out; the hub does not add them back): ABC-99, made-up-repo#52",
                      setup.now_path().read_text())

    def test_taking_an_item_off_the_not_line_lets_the_hub_add_it_again(self):
        self.apply("## Alpha\nTopic: made-up pricing\nNot: ABC-99\n")
        self.assertEqual(route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED), {})
        res = self.apply("## Alpha\nTopic: made-up pricing\n")
        self.assertIn("Alpha: Not: ABC-99 taken off", res["changes"])
        self.assertEqual(route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED),
                         {ALPHA: ["ABC-99"]})

    def test_an_item_on_both_lines_is_a_problem_and_the_section_keeps_its_row(self):
        self.apply("## Alpha\nTopic: made-up pricing\nWork items: ABC-12\n")
        res = self.apply("## Alpha\nTopic: made-up pricing\nWork items: ABC-12\nNot: abc-12\n")
        self.assertEqual([x["text"] for x in res["problems"]],
                         ["Alpha: ABC-12 is on both the Work items line and the Not line; take it off one of them"])
        self.assertEqual({i["key"] for i in self.row(ALPHA)["items"]}, {"ABC-12"})
        self.assertNotIn("not", self.row(ALPHA))

    def test_the_not_line_is_written_back_and_a_person_adding_the_item_takes_it_off(self):
        self.apply("## Alpha\nTopic: made-up pricing\nWork items: ABC-12\nNot: ABC-99, ABC-98\n")
        # An agent (the PO) adds ABC-99 by hand: it comes off the Not line, the other stays.
        setup.set_own(ALPHA, {"addItems": ["ABC-99"]}, agent(ALPHA), now=WED + 60)
        text = setup.setup_path().read_text()
        self.assertIn("## Alpha\nTopic: made-up pricing\nWork items: ABC-12, ABC-99\nNot: ABC-98\n", text)
        self.assertEqual(route.not_keys(self.row(ALPHA)), {"ABC-98"})
        # The rewrite counts as applied: the next tick calls nothing.
        setup.tick(now=WED + 120, background=False)
        self.assertEqual(self.haiku, [])
        # A file written from the table keeps the Not line.
        self.assertIn("Not: ABC-98", setup.first_text())

    def test_a_loose_section_may_say_what_to_leave_out(self):
        self.answer = {"sources": [], "sections": [
            {"heading": "Alpha", "topic": "", "slackChannels": [], "jiraEpics": [], "jiraComponents": [],
             "jiraProjects": [], "workItems": [], "notItems": ["ABC-99"]}]}
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        res = self.apply("## Alpha\nPlease leave ABC-99 out of this one, it is not ours.\n")
        self.assertEqual(res["problems"], [])
        self.assertEqual(route.not_keys(self.row(ALPHA)), {"ABC-99"})
        self.assertEqual(self.row(ALPHA)["items"], [])
        self.answer["sections"][0]["notItems"] = ["ABC-77"]
        res = self.apply("## Alpha\nPlease leave ABC-99 out of this one, it really is not ours.\n")
        self.assertIn("ABC-77", res["problems"][0]["text"])

    # -- loose sections, read by Haiku -------------------------------------
    def test_a_loose_section_is_read_by_haiku_and_checked(self):
        self.answer = {"sources": [], "sections": [
            {"heading": "Alpha", "topic": "made-up pricing", "slackChannels": ["made-up-one"],
             "jiraEpics": [], "jiraComponents": [], "jiraProjects": [], "workItems": ["ABC-12"]}]}
        res = self.apply("## Alpha\nThis is about made-up pricing. Read #made-up-one and\n"
                         "follow ticket ABC-12 too.\n\n## Beta Two\nTopic: plain\n")
        self.assertEqual(len(self.haiku), 1)
        self.assertIn("follow ticket ABC-12", self.haiku[0])
        self.assertNotIn("Topic: plain", self.haiku[0])   # only the loose sections go to Haiku
        self.assertEqual(res["problems"], [])
        self.assertEqual(self.row(ALPHA)["places"]["slack"]["channels"], ["C000TEST01"])
        self.assertEqual(self.row(BETA)["topic"], "plain")

    def test_a_value_haiku_made_up_is_a_problem_and_the_section_keeps_its_row(self):
        route.set_initiative(ALPHA, {"topic": "before"}, {"kind": "ceo"}, now=WED)
        self.answer = {"sections": [{"heading": "Alpha", "topic": "pricing", "slackChannels": ["made-up-three"],
                                     "workItems": []}]}
        self.apply("## Alpha\nAbout pricing, read made-up-one\n\n## Beta Two\nTopic: plain\n")
        self.assertEqual(self.row(ALPHA)["topic"], "before")
        self.assertEqual(self.row(BETA)["topic"], "plain")
        self.assertTrue(any(p.startswith("Alpha: Haiku read made-up-three") for p in self.problems()))

    def test_a_value_haiku_read_must_be_a_whole_name_in_the_section(self):
        # "kite" is only a piece of "#kite-quotes"; "ABC-1" only a piece of "ABC-12".
        for got in ({"slackChannels": ["kite"]}, {"workItems": ["ABC-1"]}):
            self.answer = {"sections": [{"heading": "Alpha", "topic": "", **got}]}
            self.apply("## Alpha\nRead #kite-quotes and follow ABC-12.\n")
            self.assertEqual(len(self.problems()), 1, got)
            self.assertIn("which the section does not say", self.problems()[0])
            self.assertEqual(self.row(ALPHA)["places"], {})
        self.assertTrue(setup._in_text("ABC-12", "follow ABC-12."))
        self.assertTrue(setup._in_text("kite-quotes", "Read #kite-quotes and"))

    def test_a_topic_haiku_made_up_is_a_problem(self):
        route.set_initiative(ALPHA, {"topic": "before"}, {"kind": "ceo"}, now=WED)
        self.answer = {"sections": [{"heading": "Alpha", "topic": "INVENTED TOPIC"}]}
        self.apply("## Alpha\nThis one is about pricing and quotes.\n")
        self.assertEqual(self.row(ALPHA)["topic"], "before")
        self.assertIn('Alpha: Haiku read the topic "INVENTED TOPIC"', self.problems()[0])

    def test_a_source_setting_haiku_read_must_be_on_the_sources_line(self):
        self.answer = {"sources": [{"name": "Slack", "setting": "off"}], "sections": []}
        res = self.apply("## Sources\nSlack stays as it is for now please\n")
        self.assertEqual(outside.source_config("slack")["mode"], "on")
        self.assertTrue(any(p["section"] == "Sources" and "Slack" in p["text"] for p in res["problems"]),
                        res["problems"])
        # Written on its line, it is applied.
        self.answer = {"sources": [{"name": "Slack", "setting": "off"}], "sections": []}
        self.apply("## Sources\nPlease turn Slack off for now\n")
        self.assertEqual(outside.source_config("slack")["mode"], "off")

    def test_a_section_haiku_left_out_is_a_problem(self):
        self.answer = {"sections": []}
        self.apply("## Alpha\nsomething loose here\n")
        self.assertEqual(self.problems(), ["Alpha: Haiku could not read this section; nothing in it changed. "
                                           "Write it more plainly."])

    def test_when_haiku_fails_nothing_changes_and_it_is_tried_again_in_10_minutes(self):
        route.set_initiative(ALPHA, {"topic": "before"}, {"kind": "ceo"}, now=WED)
        self.answer = TimeoutError("slow")
        self.apply("## Alpha\nTopic: plain change\n\n## Beta Two\nloose words\n")
        self.assertEqual(self.row(ALPHA)["topic"], "before")     # even the plain section
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("could not read the file", self.problems()[0])
        st = setup.load_state()
        self.assertTrue(st["failed"])
        setup._STARTED["checked"] = True
        setup.tick(now=WED + 60, background=False)
        self.assertEqual(len(self.haiku), 1)                    # not yet
        self.answer = {"sections": [{"heading": "Beta Two", "topic": "loose words"}]}
        setup.tick(now=WED + setup.RETRY_S + 1, background=False)
        self.assertEqual(len(self.haiku), 2)
        self.assertEqual(self.row(ALPHA)["topic"], "plain change")
        self.assertEqual(self.problems(), [])

    # -- problems ------------------------------------------------------------
    def test_problems_are_written_at_the_top_and_are_not_a_new_save(self):
        self.apply("## Alpha\nTopic: one\nSlack channels: #not-a-real-one\n\n## Nobody\nTopic: x\n\n"
                   "## Beta Two\nTopic: two\nWork items: ABC 12\n\n## Gamma\nTopic: three\n")
        text = setup.setup_path().read_text()
        self.assertTrue(text.startswith("# Outside sources setup\n\n" + setup.PROBLEMS_START))
        self.assertIn("- Alpha: #not-a-real-one is not a channel the Slack connection can see", text)
        self.assertIn("- Nobody: there is no initiative of that name on this hub", text)
        self.assertIn("- Beta Two: 'ABC 12' is not a ticket key", text)
        self.assertEqual(self.row(ALPHA)["topic"], "")          # its section had a problem
        self.assertEqual(self.row(GAMMA)["topic"], "three")     # the rest applied
        n = len(self.lookups)
        setup._STARTED["checked"] = True
        setup.tick(now=WED + 5, background=False)               # the hub's rewrite: no apply
        self.assertEqual(len(self.lookups), n)
        # Fixed: the problems are cleared.
        body = text.split(setup.PROBLEMS_END + "\n", 1)[1]
        body = body.replace("#not-a-real-one", "#made-up-one").replace("ABC 12", "ABC-12") \
            .replace("## Nobody\nTopic: x\n\n", "")
        setup.setup_path().write_text(text.split(setup.PROBLEMS_START)[0] + setup.PROBLEMS_START
                                      + "\nold\n" + setup.PROBLEMS_END + "\n" + body)
        os.utime(setup.setup_path(), (WED + 10, WED + 10))
        setup.tick(now=WED + 20, background=False)
        self.assertEqual(self.problems(), [])
        self.assertIn(setup.NO_PROBLEMS, setup.setup_path().read_text())
        self.assertEqual(self.row(ALPHA)["places"]["slack"]["channels"], ["C000TEST01"])

    def test_a_failed_slack_lookup_keeps_that_section_and_is_tried_again(self):
        def broken(names):
            raise setup.LookupFailed("the Slack connection did not answer")
        with mock.patch.object(setup, "LOOKUP", broken):
            self.apply("## Alpha\nTopic: one\nSlack channels: #made-up-one\n\n## Gamma\nTopic: three\n")
        self.assertEqual(self.row(ALPHA)["topic"], "")
        self.assertEqual(self.row(GAMMA)["topic"], "three")
        self.assertIn("could not be looked up", self.problems()[0])
        self.assertTrue(setup.load_state()["failed"])
        setup._STARTED["checked"] = True
        setup.tick(now=WED + setup.RETRY_S + 1, background=False)
        self.assertEqual(self.row(ALPHA)["places"]["slack"]["channels"], ["C000TEST01"])

    # -- clashes, moves, clearing --------------------------------------------
    def test_a_channel_moved_between_sections_in_one_save_works(self):
        self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one\n\n## Beta Two\nTopic: b\n")
        res = self.apply("## Beta Two\nTopic: b\nSlack channels: #made-up-one\n\n## Alpha\nTopic: a\n")
        self.assertEqual(res["problems"], [])
        self.assertEqual(self.row(BETA)["places"]["slack"]["channels"], ["C000TEST01"])
        self.assertNotIn("slack", self.row(ALPHA)["places"])

    def test_a_channel_in_two_sections_is_refused_for_one_of_them(self):
        self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one\n\n"
                   "## Beta Two\nTopic: b\nSlack channels: #made-up-one\n")
        self.assertEqual(self.row(ALPHA)["places"]["slack"]["channels"], ["C000TEST01"])
        self.assertEqual(self.row(BETA)["topic"], "")
        self.assertEqual(len(self.problems()), 1)
        self.assertIn("Beta Two: #C000TEST01 is already in Alpha's row", self.problems()[0])

    def test_an_initiative_with_no_section_is_cleared_unless_a_name_is_unknown(self):
        route.add_items_auto({BETA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        self.apply("## Alpha\nTopic: a\n\n## Beta Two\nTopic: b\nWork items: ABC-5\n")
        self.apply("## Alpha\nTopic: a\n\n## Beta Renamed\nTopic: b\n")
        self.assertEqual(self.row(BETA)["topic"], "b")          # likely a rename: nothing cleared
        self.apply("## Alpha\nTopic: a\n")
        self.assertEqual(self.row(BETA)["topic"], "")
        self.assertEqual([i["key"] for i in self.row(BETA)["items"]], ["ABC-99"])   # the hub's stays
        self.assertIn("Beta Two: no section in the file, so what was set is cleared",
                      setup.load_state()["changes"])

    # -- check only ------------------------------------------------------------
    def test_check_only_says_what_would_change_and_changes_nothing(self):
        before = route._one_file().read_bytes() if route._one_file().exists() else None
        res = setup.apply_text("## Sources\n- Slack: off\n\n## Alpha\nTopic: a\nSlack channels: #made-up-one\n"
                               "\n## Beta Two\nTopic: b\nSlack channels: #nope-channel\n", check_only=True, now=WED)
        self.assertIn("Alpha: topic changed", res["changes"])
        self.assertIn("Sources: Slack on → off", res["changes"])
        self.assertEqual([p["text"] for p in res["problems"]],
                         ["Beta Two: #nope-channel is not a channel the Slack connection can see"])
        self.assertEqual(route._one_file().read_bytes() if route._one_file().exists() else None, before)
        self.assertEqual(outside.source_config("slack")["mode"], "on")
        self.assertFalse(setup.setup_path().exists())

    def test_check_only_is_seen_by_no_other_thread_and_writes_no_file(self):
        self.apply("## Alpha\nTopic: a\n")
        table = route._one_file().read_bytes()
        ids_before = setup._ids_path().exists()
        seen = {}
        real = route.set_initiative

        def look_meanwhile(*a, **kw):
            # While the preview runs, another thread reads the files as they are.
            # (The files themselves: the table's lock is held by the apply.)
            t = threading.Thread(target=lambda: seen.update(
                mode=json.loads(outside._config_file().read_text())["sources"]["slack"]["mode"],
                table=route._one_file().read_bytes()))
            t.start(); t.join(5)
            return real(*a, **kw)
        with mock.patch.object(route, "set_initiative", look_meanwhile):
            res = setup.apply_text("## Sources\n- Slack: off\n\n## Alpha\nTopic: b\nSlack channels: #made-up-one\n",
                                   check_only=True, now=WED)
        self.assertIn("Alpha: topic changed", res["changes"])
        self.assertEqual(seen, {"mode": "on", "table": table})
        self.assertEqual(route._one_file().read_bytes(), table)
        self.assertEqual(setup._ids_path().exists(), ids_before)    # the looked-up id is not kept
        # The preview is over: this thread reads the files again.
        self.assertEqual(outside.source_config("slack")["mode"], "on")
        self.assertEqual(self.row(ALPHA)["topic"], "a")

    def test_a_source_line_adds_a_source_the_hub_can_read(self):
        outside.remove_source("jira")
        res = self.apply("## Sources\n- Jira: on\n- Mastodon: on\n")
        self.assertEqual(outside.source_config("jira")["mode"], "on")
        self.assertTrue(any("Mastodon" in p["text"] for p in res["problems"]))

    # -- #409: the read interval and starting a stopped PO -------------------
    def test_the_first_file_has_the_two_lines_with_todays_values(self):
        setup.tick(now=WED, background=False)
        text = setup.setup_path().read_text()
        self.assertIn("- Jira: decide only\n- Read every: 1 minute in working hours, 15 minutes otherwise\n"
                      "- Start a stopped PO for an outside message: yes\n", text)
        self.assertIn("- Read every: 1 minute in working hours", setup.now_text(WED))
        # Applied as written, it changes nothing.
        res = setup.apply_file(now=WED)
        self.assertEqual((res or {}).get("changes", []), [])

    def test_the_interval_is_set_on_every_source_and_a_new_one(self):
        outside.remove_source("jira")
        res = self.apply("## Sources\n- Slack: on\n- Jira: on\n"
                         "- Read every: 2 minutes in working hours, 30 minutes otherwise\n")
        self.assertEqual(res["problems"], [])
        for sid in ("slack", "jira"):
            c = outside.source_config(sid)
            self.assertEqual((c["fastEvery"], c["slowEvery"]), (2, 30), sid)
        self.assertIn("Sources: read every 1 minute in working hours, 15 minutes otherwise → "
                      "2 minutes in working hours, 30 minutes otherwise", res["changes"])
        # One number is both; a line left out is today's value again.
        self.apply("## Sources\n- Slack: on\n- Jira: on\n- Read every: 5 min\n")
        self.assertEqual(outside.source_config("slack")["slowEvery"], 5)
        self.apply("## Sources\n- Slack: on\n- Jira: on\n")
        c = outside.source_config("slack")
        self.assertEqual((c["fastEvery"], c["slowEvery"]), (1, 15))
        self.assertEqual(self.haiku, [])

    def test_a_bad_interval_is_a_problem_and_changes_nothing(self):
        self.apply("## Sources\n- Slack: on\n- Read every: 3 minutes in working hours, 20 minutes otherwise\n")
        res = self.apply("## Sources\n- Slack: on\n- Read every: now and then\n")
        self.assertTrue(any("Read every" in p["text"] for p in res["problems"]), res["problems"])
        res = self.apply("## Sources\n- Slack: on\n- Read every: 0 minutes\n")
        self.assertTrue(any("at least 1" in p["text"] for p in res["problems"]), res["problems"])
        self.assertEqual(outside.source_config("slack")["fastEvery"], 3)
        self.assertEqual(self.haiku, [])

    def test_start_a_stopped_po_no_and_back(self):
        self.assertTrue(outside.load_options()["startStoppedPo"])
        res = self.apply("## Sources\n- Slack: on\n- Start a stopped PO for an outside message: no\n")
        self.assertEqual(res["problems"], [])
        self.assertFalse(outside.load_options()["startStoppedPo"])
        self.assertIn("Sources: start a stopped po for an outside message: yes → no", res["changes"])
        self.assertIn("- Start a stopped PO for an outside message: no", setup.first_text())
        res = self.apply("## Sources\n- Slack: on\n- Start a stopped PO for an outside message: maybe\n")
        self.assertTrue(any("yes or no" in p["text"] for p in res["problems"]))
        self.assertFalse(outside.load_options()["startStoppedPo"])
        self.apply("## Sources\n- Slack: on\n")            # left out: today's yes
        self.assertTrue(outside.load_options()["startStoppedPo"])

    def test_check_only_changes_neither(self):
        res = setup.apply_text("## Sources\n- Slack: on\n- Jira: decide only\n- Read every: 4 minutes\n"
                               "- Start a stopped PO for an outside message: no\n", check_only=True, now=WED)
        self.assertEqual(len(res["changes"]), 2, res["changes"])
        self.assertEqual(outside.source_config("slack")["fastEvery"], 1)
        self.assertTrue(outside.load_options()["startStoppedPo"])

    def test_haiku_reads_them_in_a_loose_sources_section_checked_against_its_words(self):
        self.answer = {"sources": [{"name": "Slack", "setting": "on"}, {"name": "Read every", "setting": "on"}],
                       "readEvery": [2, 20], "startStoppedPo": "no", "sections": []}
        res = self.apply("## Sources\nSlack on please. Read it every 2 minutes at work and 20 otherwise;\n"
                         "do not wake a stopped PO for these, no.\n")
        self.assertEqual(res["problems"], [])
        self.assertEqual(outside.source_config("slack")["slowEvery"], 20)
        self.assertFalse(outside.load_options()["startStoppedPo"])
        # Numbers or a yes the words do not hold are problems; nothing changes.
        self.answer = {"sources": [{"name": "Slack", "setting": "on"}], "readEvery": [9, 99],
                       "startStoppedPo": "yes", "sections": []}
        res = self.apply("## Sources\nSlack on, read it often\n")
        self.assertEqual(len([p for p in res["problems"] if p["section"] == "Sources"]), 2, res["problems"])
        self.assertEqual(outside.source_config("slack")["slowEvery"], 20)
        self.assertFalse(outside.load_options()["startStoppedPo"])

    def test_jira_places_without_a_jira_source_are_a_problem(self):
        outside.remove_source("jira")
        self.apply("## Alpha\nTopic: a\nJira: epic ABC-1\n")
        self.assertIn("Alpha: it lists Jira places, but the hub has no Jira source", self.problems()[0])

    # -- agents ------------------------------------------------------------------
    def test_an_agent_changes_its_own_section_and_no_haiku_call_follows(self):
        self.answer = {"sections": [{"heading": "Gamma", "topic": "loose words"}]}
        self.apply("## Alpha\nTopic: a\n\n## Gamma\nloose words\n")
        self.assertEqual(len(self.haiku), 1)
        setup.set_own(ALPHA, {"addItems": ["ABC-7"], "places": {"slack": {"channels": ["#made-up-two"]}}},
                      agent(ALPHA), now=WED + 5)
        text = setup.setup_path().read_text()
        self.assertIn("## Alpha\nTopic: a\nSlack channels: #made-up-two\nWork items: ABC-7\n", text)
        self.assertIn("## Gamma\nloose words", text)             # the rest left as written
        self.assertEqual(self.row(ALPHA)["places"]["slack"]["channels"], ["C000TEST02"])
        self.assertEqual(route.history(None, ALPHA)[0]["by"]["identity"], "claude")
        setup._STARTED["checked"] = True
        setup.tick(now=WED + 10, background=False)
        self.assertEqual(len(self.haiku), 1)
        with self.assertRaises(route.Refused):
            setup.set_own(BETA, {"topic": "not mine"}, agent(ALPHA))
        with self.assertRaises(route.Refused):
            setup.set_own(ALPHA, {"places": {"slack": {"channels": ["#nope-channel"]}}}, agent(ALPHA))

    def test_a_loose_section_with_a_problem_or_an_unapplied_save_is_not_rewritten(self):
        self.answer = {"sections": []}                     # Haiku leaves it out: a problem
        self.apply("## Alpha\nsomething loose about pricing\n")
        before = setup.setup_path().read_text()
        with self.assertRaisesRegex(route.Refused, "written loosely and has a problem"):
            setup.set_own(ALPHA, {"topic": "new"}, agent(ALPHA))
        self.assertEqual(setup.setup_path().read_text(), before)
        self.assertEqual(self.row(ALPHA)["topic"], "")
        # Applied with no problem, then saved again and not applied yet: refused too.
        self.answer = {"sections": [{"heading": "Alpha", "topic": "pricing"}]}
        self.apply("## Alpha\nsomething loose about pricing\n")
        self.write("## Alpha\nsomething loose about pricing and quotes\n")
        with self.assertRaisesRegex(route.Refused, "words the hub has not applied yet"):
            setup.set_own(ALPHA, {"topic": "new"}, agent(ALPHA))
        # Applied: the loose section is rewritten whole, in the plain form.
        setup.apply_file(now=WED)
        setup.set_own(ALPHA, {"topic": "new"}, agent(ALPHA))
        self.assertIn("## Alpha\nTopic: new\n", setup.setup_path().read_text())
        self.assertNotIn("loose", setup.setup_path().read_text())

    def test_a_plain_section_with_a_problem_keeps_its_other_lines(self):
        self.apply("## Alpha\nTopic: a\nWork items: ABC-5\n")
        self.apply("## Alpha\nTopic: a\nSlack channels: #nope-channel\nWork items: ABC-5\n")
        setup.set_own(ALPHA, {"topic": "b"}, agent(ALPHA))
        text = setup.setup_path().read_text()
        self.assertIn("## Alpha\nTopic: b\nSlack channels: #nope-channel\nWork items: ABC-5\n", text)
        self.assertNotEqual(setup.text_hash(text), setup.load_state()["appliedHash"])  # applied again next tick

    def test_a_saved_change_to_the_same_line_not_applied_yet_is_not_lost(self):
        self.apply("## Alpha\nTopic: a\nWork items: ABC-1\n")
        self.write("## Alpha\nTopic: a\nWork items: ABC-1, ABC-2\n")      # saved, the hub not ticked yet
        with self.assertRaisesRegex(route.Refused, "has not applied it yet"):
            setup.set_own(ALPHA, {"addItems": ["ABC-3"]}, agent(ALPHA))
        self.assertIn("Work items: ABC-1, ABC-2\n", setup.setup_path().read_text())
        # Another line may change: the saved one stays as written.
        setup.set_own(ALPHA, {"topic": "b"}, agent(ALPHA))
        self.assertIn("## Alpha\nTopic: b\nWork items: ABC-1, ABC-2\n", setup.setup_path().read_text())

    def test_a_line_in_another_form_applied_already_is_not_taken_for_a_new_save(self):
        self.apply("## Alpha\nTopic: a\nSlack channels: made-up-one\n\n## Gamma\nTopic: c\n")
        self.write("## Alpha\nTopic: a\nSlack channels: made-up-one\n\n## Gamma\nTopic: c2\n")  # another section saved
        setup.set_own(ALPHA, {"places": {"slack": {"channels": ["#made-up-two"]}}}, agent(ALPHA))
        text = setup.setup_path().read_text()
        self.assertIn("## Alpha\nTopic: a\nSlack channels: #made-up-two\n", text)
        self.assertIn("## Gamma\nTopic: c2\n", text)            # the other save is kept
        # The line the hub just wrote counts as applied: a second change goes through too.
        setup.set_own(ALPHA, {"places": {"slack": {"channels": ["#made-up-one"]}}}, agent(ALPHA))

    def test_while_haiku_fails_the_refusal_says_the_file_cannot_be_applied_now(self):
        self.apply("## Alpha\nTopic: a\nWork items: ABC-1\n")
        self.answer = TimeoutError("slow")
        self.apply("## Alpha\nTopic: a\nWork items: ABC-1, ABC-2\n\n## Gamma\nloose words\n")
        with self.assertRaisesRegex(route.Refused, "cannot apply the file right now"):
            setup.set_own(ALPHA, {"addItems": ["ABC-3"]}, agent(ALPHA))

    def test_words_saved_with_a_problem_in_their_section_are_not_lost(self):
        self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one\nWork items: ABC-1\n")
        self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one, #nope-channel\nWork items: ABC-1, ABC-5\n")
        self.assertEqual([i["key"] for i in self.row(ALPHA)["items"]], ["ABC-1"])   # the row as it was
        with self.assertRaisesRegex(route.Refused, "has a problem the hub could not apply"):
            setup.set_own(ALPHA, {"addItems": ["ABC-7"]}, agent(ALPHA))
        self.assertIn("Work items: ABC-1, ABC-5\n", setup.setup_path().read_text())
        # The topic line is as last applied: it may change.
        setup.set_own(ALPHA, {"topic": "b"}, agent(ALPHA))
        self.assertIn("## Alpha\nTopic: b\nSlack channels: #made-up-one, #nope-channel\nWork items: ABC-1, ABC-5\n",
                      setup.setup_path().read_text())
        # A failed Slack lookup is a problem in the section too.
        def broken(names):
            raise setup.LookupFailed("the Slack connection did not answer")
        self.apply("## Beta Two\nTopic: b\nWork items: ABC-21\n")
        with mock.patch.object(setup, "LOOKUP", broken):
            self.apply("## Beta Two\nTopic: b\nSlack channels: #made-up-three\nWork items: ABC-21, ABC-22\n")
        with self.assertRaisesRegex(route.Refused, "could not be looked up"):
            setup.set_own(BETA, {"addItems": ["ABC-23"]}, agent(BETA))

    def test_a_change_that_changes_nothing_leaves_the_file_alone(self):
        self.answer = {"sections": [{"heading": "Alpha", "topic": "pricing"}]}
        self.apply("## Alpha\nsomething loose about pricing\n")
        before = setup.setup_path().read_text()
        setup.set_own(ALPHA, {"topic": "pricing"}, agent(ALPHA))
        self.assertEqual(setup.setup_path().read_text(), before)

    def test_a_preview_that_fails_to_start_does_not_stay_on_the_thread(self):
        with mock.patch.object(route, "preview_table", side_effect=[OSError("made-up"), None]), \
                self.assertRaises(OSError):
            setup.apply_text("## Alpha\nTopic: a\n", check_only=True, now=WED)
        self.assertIsNone(getattr(outside._PREVIEW, "cfg", None))

    def test_a_change_waits_for_an_apply_that_is_running(self):
        self.apply("## Alpha\nTopic: a\n")
        setup._APPLYING.acquire()
        try:
            with mock.patch.object(setup, "APPLY_WAIT_S", 0.05), \
                    self.assertRaisesRegex(route.Refused, "being applied right now"):
                setup.set_own(ALPHA, {"topic": "b"}, agent(ALPHA))
            self.assertEqual(self.row(ALPHA)["topic"], "a")
            # Released while it waits: the change goes through.
            threading.Timer(0.05, setup._APPLYING.release).start()
            setup.set_own(ALPHA, {"topic": "b"}, agent(ALPHA))
        finally:
            if setup._APPLYING.locked():
                setup._APPLYING.release()
        self.assertEqual(self.row(ALPHA)["topic"], "b")

    def test_the_routing_tool_writes_the_section_and_shows_its_problems(self):
        self.apply("## Alpha\nTopic: a\n")
        self.apply("## Alpha\nTopic: a\nSlack channels: #nope-channel\n")
        hub = types.SimpleNamespace(outside=outside)
        ctx = {"projectId": ALPHA, "identity": "claude", "part": {"agent": "claude", "role": "po"},
               "room": {"id": "room-made-up"}}
        with mock.patch.object(ensemble_tools, "_projects", lambda: {ALPHA: {"name": "Alpha"}}), \
                mock.patch.object(ensemble_tools, "_ref", lambda room, projects: "#1"), \
                mock.patch.object(ensemble_tools, "_d", hub):
            out = ensemble_tools._routing(ctx, {"action": "read"}, None)
            self.assertEqual(out["setupFile"], str(setup.setup_path()))
            self.assertEqual(out["setupProblems"],
                             ["Alpha: #nope-channel is not a channel the Slack connection can see"])
            ensemble_tools._routing(ctx, {"action": "set", "topic": "new words"}, None)
            out = ensemble_tools._routing(ctx, {"action": "read"}, None)
        # A plain section: only the changed line is rewritten, so the bad channel
        # line and its problem stay until someone fixes the line.
        self.assertEqual(out["setupProblems"],
                         ["Alpha: #nope-channel is not a channel the Slack connection can see"])
        text = setup.setup_path().read_text()
        self.assertIn("## Alpha\nTopic: new words\nSlack channels: #nope-channel\n", text)
        self.assertEqual(self.row(ALPHA)["topic"], "new words")

    # -- the panel ---------------------------------------------------------------
    def test_the_panel_tells_a_failed_read_from_a_failed_lookup(self):
        self.answer = TimeoutError("slow")
        self.apply("## Alpha\nloose words\n")
        d = setup.panel()
        self.assertTrue(d["failed"] and d["failedAll"])

        def broken(names):
            raise setup.LookupFailed("the Slack connection did not answer")
        with mock.patch.object(setup, "LOOKUP", broken):
            self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one\n")
        d = setup.panel()
        self.assertTrue(d["failed"])
        self.assertFalse(d["failedAll"])
        self.assertNotIn(setup.PROBLEMS_START, d["body"])
        self.assertIn("## Alpha", d["body"])

    def test_a_save_of_the_text_applied_already_applies_nothing(self):
        self.apply("## Alpha\nTopic: a\n")
        text = setup.setup_path().read_text()
        with mock.patch.object(setup, "start_apply") as start:
            setup.save_text(text.replace(setup.NO_PROBLEMS, "typed over the problems part"))
            start.assert_not_called()
            setup.save_text(text.replace("Topic: a", "Topic: b"))
            start.assert_called_once()

    def test_each_apply_that_changed_something_is_kept_with_what_it_changed(self):
        self.apply("## Sources\n- Slack: off\n\n## Alpha\nTopic: a\n")
        self.apply("## Sources\n- Slack: off\n\n## Alpha\nTopic: a\n")       # nothing new: not kept
        applies = setup.load_state()["applies"]
        self.assertEqual(len(applies), 1)
        self.assertIn("Sources: Slack on → off", applies[0]["changes"])
        self.assertEqual((applies[0]["at"], applies[0]["by"]), (WED, "the setup file"))

    # -- routing-now.md ------------------------------------------------------------
    def test_routing_now_shows_ids_names_items_and_the_last_change(self):
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        self.apply("## Alpha\nTopic: a\nSlack channels: #made-up-one\nWork items: ABC-5\n")
        text = setup.now_path().read_text()
        self.assertIn("**Do not edit this file**", text)
        self.assertIn("Slack channels: #made-up-one (C000TEST01)", text)
        self.assertIn("- ABC-5: from the setup file", text)
        self.assertIn("- ABC-99: from task #7", text)
        self.assertIn("by the setup file", text)
        self.assertIn("## Initiatives with nothing set\nBeta Two, Gamma", text)


class ReadingTest(unittest.TestCase):
    def test_plain_sections(self):
        self.assertEqual(setup.read_plain_section(["Topic: x y", "Slack channels: #a, b-c", "Jira: epic AB-1, project AB",
                                                   "Work items: AB-2", "Not: AB-3, repo 4"]),
                         {"topic": "x y", "channels": ["#a", "b-c"],
                          "jira": {"epics": ["AB-1"], "components": [], "projects": ["AB"]}, "items": ["AB-2"],
                          "not": ["AB-3", "repo 4"]})
        self.assertIsNone(setup.read_plain_section(["Topic: x", "and more words"]))
        self.assertIsNone(setup.read_plain_section(["Slack channels: #a and #b too!"]))
        self.assertEqual(setup.read_plain_sources(["- Slack: Decide only", "- Jira: paused"]),
                         [{"name": "Slack", "word": "decide only"}, {"name": "Jira", "word": "paused"}])
        self.assertIsNone(setup.read_plain_sources(["- Slack: on please"]))

    def test_the_problems_part_is_left_out_of_the_hash(self):
        a = "# T\n\n## Alpha\nTopic: x\n"
        b = setup.with_problems(a, ["Alpha: something"])
        self.assertIn(setup.PROBLEMS_START, b)
        self.assertEqual(setup.text_hash(a), setup.text_hash(b))
        self.assertEqual(setup.text_hash(setup.with_problems(b, [])), setup.text_hash(a))
        self.assertNotEqual(setup.text_hash(a), setup.text_hash(a.replace("x", "y")))


if __name__ == "__main__":
    unittest.main()
