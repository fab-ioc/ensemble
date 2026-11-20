"""The hub chat (hub_chat.py, #16) with a fake Haiku: a proposed change shows
its changed lines and the check, changes nothing until Apply, and Apply
writes and applies it as "the hub chat"; a plain reply; an answer in no
form; a file changed since the answer (Apply refused, stale, Ask again);
Discard; Clear; the last 50 turns kept; a question left by a restart. Names,
ids and channels are made up (the repo is public)."""
from __future__ import annotations

import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agent_models
import dashboard  # noqa: F401  (imported first: its import binds outside to the real data folder, setUp then binds a temporary one)
import hub_chat
import hub_chat_settings as hs
import outside
import outside_route as route
import outside_setup as setup

CODEX_MODELS = [{"id": "gpt-6-sol", "name": "gpt-6-sol", "description": "", "efforts": ["low", "medium", "high"],
                 "defaultEffort": "medium", "pool": ""}]
WED = time.mktime((2026, 11, 18, 10, 0, 0, 0, 0, -1))
ALPHA, BETA = "p-alpha", "p-beta"
IDS = {"made-up-one": "C000TEST01", "made-up-two": "C000TEST02"}

FILE = """# Outside sources setup

## Sources
- Slack: on

## Alpha
Topic: made-up pricing
Slack channels: #made-up-one
Work items: ABC-12
"""


class HubChatTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-hubchat-")
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        projects = [{"id": ALPHA, "name": "Alpha"}, {"id": BETA, "name": "Beta"}]
        hub = types.SimpleNamespace(DASHBOARD_DIR=self.state, load_projects=lambda: list(projects))
        self.prompts: list[str] = []
        self.answer = "REPLY: hello"

        def run(prompt, timeout):
            self.prompts.append(prompt)
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

        for p in (mock.patch.object(outside, "_d", None),
                  mock.patch.object(route, "_d", None),
                  mock.patch.object(outside, "ROUTER", None),
                  mock.patch.object(outside, "PLACES", None),
                  mock.patch.object(outside, "_log"),
                  mock.patch.object(route, "_log"),
                  mock.patch.object(setup, "_log"),
                  mock.patch.object(hub_chat, "_log"),
                  mock.patch.object(setup, "LOOKUP", lambda names: {n: IDS.get(n, "") for n in names}),
                  mock.patch.object(setup, "RUN", lambda p, t: (_ for _ in ()).throw(AssertionError("no loose sections"))),
                  mock.patch.object(hub_chat, "RUN", run),
                  mock.patch.object(hub_chat, "BACKGROUND", False),
                  # The hub's Settings (#410): a settings file of the test's own.
                  mock.patch.object(dashboard, "DASHBOARD_DIR", self.state),
                  mock.patch.object(dashboard, "SETTINGS_FILE", self.state / "settings.json"),
                  mock.patch.object(dashboard.usage, "remark_codex", lambda: None),
                  mock.patch.object(agent_models, "codex_models", lambda path=None: list(CODEX_MODELS)),
                  mock.patch.object(agent_models, "codex_own", lambda: {"model": "gpt-6-sol", "effort": ""})):
            p.start()
            self.addCleanup(p.stop)
        outside.bind(hub)
        route.bind(hub)
        for m in (outside, setup, hub_chat):
            m.reset()
            self.addCleanup(m.reset)
        outside.set_source("slack", type="slack", mode="on", settings={"reader": "connector"})
        for f in (setup.setup_path(), hub_chat.path(), dashboard.SETTINGS_FILE, hs.history_path()):
            assert f.is_relative_to(self.state), f"{f} is not the test's own folder"
        setup.setup_path().parent.mkdir(parents=True, exist_ok=True)
        setup.setup_path().write_text(FILE, encoding="utf-8")
        setup.apply_file(now=WED)

    def row(self, pid):
        return route._load_one()["rows"].get(pid) or route._empty_row()

    def change(self, new_file: str, words="Moved the channel.") -> str:
        return f"CHANGE: {words}\n<<<SETUP outside\n{new_file}SETUP>>>"

    def last(self) -> dict:
        return hub_chat.view()["turns"][-1]

    # -- a change ------------------------------------------------------------
    def test_a_change_is_shown_with_its_lines_and_check_and_applied_only_on_apply(self):
        new = FILE.replace("Work items: ABC-12\n", "Work items: ABC-12\n\n## Beta\nSlack channels: #made-up-two\n")
        self.answer = self.change(new, "Beta now gets #made-up-two.")
        v = hub_chat.ask("Send made-up-two to Beta")
        user, hub = v["turns"]
        self.assertEqual((user["role"], user["text"]), ("user", "Send made-up-two to Beta"))
        self.assertNotIn("pending", user)
        self.assertEqual((hub["kind"], hub["state"], hub["text"]), ("change", "proposed", "Beta now gets #made-up-two."))
        self.assertEqual(hub["diff"], [{"before": [], "after": ["", "## Beta", "Slack channels: #made-up-two"]}])
        self.assertEqual(hub["check"]["problems"], [])
        self.assertTrue(any("Beta" in c for c in hub["check"]["changes"]), hub["check"])
        self.assertNotIn("proposed", hub)                       # the page gets the lines, not the file
        # The prompt carries the request, the file and the routing now.
        p = self.prompts[0]
        self.assertIn("Send made-up-two to Beta", p)
        self.assertIn("Slack channels: #made-up-one", p)
        self.assertIn("# Routing now", p)
        # Nothing changed yet.
        self.assertEqual(self.row(BETA)["places"], {})
        self.assertNotIn("## Beta", setup.setup_path().read_text())
        v = hub_chat.apply(hub["id"])
        hub = v["turns"][-1]
        self.assertEqual(hub["state"], "applied")
        self.assertEqual(hub["result"]["problems"], [])
        self.assertEqual(self.row(BETA)["places"]["slack"]["channels"], ["C000TEST02"])
        text = setup.setup_path().read_text()
        self.assertIn("## Beta\nSlack channels: #made-up-two", text)
        self.assertIn("No problems on the last save.", text)
        self.assertEqual(route.history(None, BETA)[0]["by"], {"kind": "hubchat"})
        self.assertEqual(setup.load_state()["applies"][-1]["by"], "the hub chat")
        with self.assertRaises(hub_chat.Refused):
            hub_chat.apply(hub["id"])                           # once only

    def test_a_not_line_change_takes_an_item_out(self):
        route.add_items_auto({ALPHA: [("ticket", "ABC-99", "task #7")]}, "made up", now=WED)
        self.answer = self.change(FILE + "Not: ABC-99\n", "ABC-99 is taken out of Alpha.")
        hub = hub_chat.ask("take ABC-99 out of Alpha")["turns"][-1]
        self.assertEqual(hub["diff"], [{"before": [], "after": ["Not: ABC-99"], "section": "## Alpha"}])
        hub_chat.apply(hub["id"])
        self.assertNotIn("ABC-99", [i["key"] for i in self.row(ALPHA)["items"]])
        self.assertEqual(route.not_keys(self.row(ALPHA)), {"ABC-99"})

    def test_a_change_with_a_problem_shows_the_problem(self):
        self.answer = self.change(FILE + "\n## Nowhere\nSlack channels: #made-up-two\n")
        hub = hub_chat.ask("route made-up-two to Nowhere")["turns"][-1]
        self.assertEqual(hub["kind"], "change")
        self.assertTrue(any("no initiative of that name" in p for p in hub["check"]["problems"]), hub["check"])

    # -- replies and failures --------------------------------------------------
    def test_a_reply_in_words_and_a_cannot_yet_reply_pass_through(self):
        self.answer = "REPLY: I cannot change the agents' models yet. I can change the outside sources setup."
        hub = hub_chat.ask("use Opus for every agent")["turns"][-1]
        self.assertEqual((hub["kind"], hub["text"]),
                         ("reply", "I cannot change the agents' models yet. I can change the outside sources setup."))
        self.assertIn("you cannot change", self.prompts[0])

    def test_an_answer_in_no_form_and_a_failed_call_change_nothing(self):
        before = setup.setup_path().read_text()
        self.answer = "Sure! Here is your new file: ..."
        hub = hub_chat.ask("do something")["turns"][-1]
        self.assertEqual(hub["kind"], "error")
        self.assertIn("not in the form asked for", hub["text"])
        self.answer = RuntimeError("made-up failure")
        hub = hub_chat.ask("do something")["turns"][-1]
        self.assertEqual(hub["kind"], "error")
        self.assertIn("did not answer", hub["text"])
        self.assertEqual(setup.setup_path().read_text(), before)
        self.assertFalse(hub_chat.view()["busy"])

    def test_the_same_file_is_nothing_to_change(self):
        self.answer = self.change(FILE, "Done.")
        hub = hub_chat.ask("keep it as it is")["turns"][-1]
        self.assertEqual(hub["kind"], "reply")
        self.assertIn("nothing to change", hub["text"])

    def test_parse(self):
        self.assertEqual(hub_chat.parse("```\nREPLY: hi\n```"), {"kind": "reply", "text": "hi"})
        self.assertIsNone(hub_chat.parse("REPLY:"))
        self.assertIsNone(hub_chat.parse("<<<SETUP outside\n# x\nSETUP>>>"))      # no CHANGE line
        self.assertEqual(hub_chat.parse("CHANGE: x\n<<<SETUP outside\n# y\nSETUP>>>")["setup"], "# y\n")

    # -- the file changed meanwhile --------------------------------------------
    def test_a_change_made_from_an_older_file_is_stale_and_not_written(self):
        self.answer = self.change(FILE.replace("made-up pricing", "made-up quotes"))
        hub = hub_chat.ask("change Alpha's topic")["turns"][-1]
        # Someone saves the file meanwhile.
        saved = FILE.replace("Work items: ABC-12", "Work items: ABC-13")
        setup.setup_path().write_text(saved, encoding="utf-8")
        self.assertEqual(self.last()["state"], "stale")        # shown so before Apply
        hub = hub_chat.apply(hub["id"])["turns"][-1]
        self.assertEqual(hub["state"], "stale")
        self.assertIn("changed since", hub["result"]["error"])
        self.assertEqual(setup.setup_path().read_text(), saved)
        self.assertEqual(self.row(ALPHA)["topic"], "made-up pricing")
        # Ask again: the same question, made from the file as it is now.
        self.answer = "REPLY: done thinking"
        v = hub_chat.again(hub["id"])
        self.assertEqual(v["turns"][-2]["text"], "change Alpha's topic")
        self.assertIn("Work items: ABC-13", self.prompts[-1])

    def test_applying_one_change_makes_the_other_proposals_stale(self):
        self.answer = self.change(FILE.replace("made-up pricing", "one"))
        a = hub_chat.ask("topic one")["turns"][-1]
        self.answer = self.change(FILE.replace("made-up pricing", "two"))
        b = hub_chat.ask("topic two")["turns"][-1]
        hub_chat.apply(b["id"])
        turns = {t["id"]: t for t in hub_chat.view()["turns"]}
        self.assertEqual((turns[a["id"]]["state"], turns[b["id"]]["state"]), ("stale", "applied"))
        self.assertEqual(self.row(ALPHA)["topic"], "two")

    def test_a_change_made_while_another_applies_stays_applicable(self):
        # The proposal made from the file the first Apply wrote is not stale.
        self.answer = self.change(FILE.replace("made-up pricing", "one"))
        a = hub_chat.ask("topic one")["turns"][-1]
        hub_chat.apply(a["id"])
        self.answer = self.change(setup.setup_path().read_text().replace("Topic: one", "Topic: two"))
        b = hub_chat.ask("topic two")["turns"][-1]
        self.assertEqual(b["state"], "proposed")
        self.assertEqual(hub_chat.apply(b["id"])["turns"][-1]["state"], "applied")
        self.assertEqual(self.row(ALPHA)["topic"], "two")

    def test_the_diff_names_the_section_of_each_hunk(self):
        two = FILE + "\n## Beta\nTopic: made-up ferries\nWork items: ABC-20\n"
        d = hub_chat.diff(two, two.replace("ABC-20", "ABC-21").replace("ABC-12", "ABC-12, ABC-13"))
        self.assertEqual([h.get("section") for h in d], ["## Alpha", "## Beta"])
        d = hub_chat.diff(two, two.replace("## Beta", "## Gamma"))
        self.assertNotIn("section", d[0], "a hunk that starts with a heading names itself")

    # -- discard, clear, what is kept ------------------------------------------
    def test_discard_and_clear(self):
        self.answer = self.change(FILE.replace("made-up pricing", "one"))
        hub = hub_chat.ask("topic one")["turns"][-1]
        self.assertEqual(hub_chat.discard(hub["id"])["turns"][-1]["state"], "discarded")
        with self.assertRaises(hub_chat.Refused):
            hub_chat.apply(hub["id"])
        self.assertEqual(self.row(ALPHA)["topic"], "made-up pricing")
        self.assertEqual(hub_chat.clear()["turns"], [])
        self.assertEqual(json.loads(hub_chat.path().read_text()), {"turns": []})

    def test_the_last_50_turns_are_kept_and_one_question_at_a_time(self):
        for i in range(30):
            hub_chat.ask(f"question {i}")
        turns = hub_chat.view()["turns"]
        self.assertEqual(len(turns), 50)
        self.assertEqual(turns[-2]["text"], "question 29")
        self.assertEqual(len(json.loads(hub_chat.path().read_text())["turns"]), 50)
        # The model sees only the last few earlier turns.
        self.assertIn("question 28", self.prompts[-1])
        self.assertNotIn("question 20", self.prompts[-1])
        hub_chat._BUSY["ask"] = True
        with self.assertRaises(hub_chat.Refused):
            hub_chat.ask("another")
        with self.assertRaises(hub_chat.Refused):
            hub_chat.clear()
        with self.assertRaises(hub_chat.Refused):
            hub_chat.ask("   ")

    def test_a_question_left_by_a_restart_becomes_an_error_turn(self):
        h = setup.text_hash(setup.setup_path().read_text())
        hub_chat.path().write_text(json.dumps({"turns": [
            {"id": "t-1", "at": WED, "role": "user", "text": "made-up", "pending": True},
            {"id": "t-0", "at": WED, "role": "hub", "kind": "change", "state": "applying", "ask": "t-x",
             "setup": "outside", "proposed": FILE, "baseHash": h, "diff": [], "check": {}}]}))
        hub_chat.reset()
        turns = hub_chat.view()["turns"]
        self.assertNotIn("pending", turns[0])
        self.assertEqual(turns[1]["state"], "proposed")
        self.assertIn("restarted while", turns[1]["result"]["error"])
        self.assertEqual((turns[2]["kind"], turns[2]["ask"]), ("error", "t-1"))
        self.assertIn("restarted before the answer", turns[2]["text"])




class HubChatSettings(unittest.TestCase):
    """#410: Ask the hub changes the hub's own Settings: a proposal shown as
    old -> new per setting, saved only on Apply through the Settings save,
    stale only when one of ITS settings changed, refused settings in one
    line, values the save would drop or clamp, the history (written only on
    Apply, never under outside/)."""
    setUp = HubChatTest.setUp
    last = HubChatTest.last
    change = HubChatTest.change

    def settings(self, changes: dict, words="Changed it.") -> str:
        return f"SETTINGS: {words}\n<<<SETTINGS\n{json.dumps(changes)}\nSETTINGS>>>"

    def saved(self) -> dict:
        return json.loads(dashboard.SETTINGS_FILE.read_text()) if dashboard.SETTINGS_FILE.exists() else {}

    def test_a_proposal_shows_each_setting_and_changes_nothing_until_apply(self):
        self.answer = self.settings({"digestIntervalMin": 15, "reviewRoundsPerTask": 3},
                                    "The PO checks every 15 minutes, and a task gets at most 3 review rounds.")
        hub = hub_chat.ask("Check every 15 minutes and stop reviews after 3 rounds")["turns"][-1]
        self.assertEqual((hub["kind"], hub["setup"], hub["state"]), ("change", "settings", "proposed"))
        self.assertEqual(hub["diff"], [
            {"key": "digestIntervalMin", "name": "How often the PO checks on progress",
             "where": "No row in Settings yet · a project's own value still wins",
             "before": "every 5 minutes", "after": "every 15 minutes"},
            {"key": "reviewRoundsPerTask", "name": "Review rounds per task",
             "where": "Settings › Agent models › Reviews", "before": "5 rounds", "after": "3 rounds"}])
        self.assertEqual(hub["check"]["problems"], [])
        self.assertNotIn("proposed", hub)
        self.assertNotIn("before", hub)
        # The prompt lists the settings with their values now, and the ones it may not change.
        p = self.prompts[0]
        self.assertIn("- reviewRoundsPerTask: Review rounds per task (Settings › Agent models › Reviews). Now: 5", p)
        self.assertIn("agentModels.codex.model: The Codex model stays", p)
        self.assertIn('"low", "medium", "high"', p)                # the efforts the Codex model takes
        self.assertEqual(self.saved(), {}, "nothing saved before Apply")
        self.assertFalse(hs.history_path().exists())
        hub = hub_chat.apply(hub["id"])["turns"][-1]
        self.assertEqual(hub["state"], "applied")
        self.assertEqual(hub["result"]["changes"], ["How often the PO checks on progress: every 5 minutes → every 15 minutes",
                                                    "Review rounds per task: 5 rounds → 3 rounds"])
        s = dashboard.load_settings()
        self.assertEqual((s["digestIntervalMin"], s["reviewRoundsPerTask"]), (15, 3))
        self.assertEqual(self.saved()["theme"], "", "saved whole by the Settings save, defaults and all")
        hist = json.loads(hs.history_path().read_text())["changes"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["ask"], "Check every 15 minutes and stop reviews after 3 rounds")
        self.assertEqual(hist[0]["changes"][1], {"key": "reviewRoundsPerTask", "name": "Review rounds per task",
                                                 "before": "5 rounds", "after": "3 rounds"})
        self.assertEqual(hs.history_path(), self.state / "settings-history.json")
        self.assertFalse((self.state / "outside" / "settings-history.json").exists())
        with self.assertRaises(hub_chat.Refused):
            hub_chat.apply(hub["id"])

    def test_the_model_settings_go_through_the_models_check(self):
        self.answer = self.settings({"agentModels.claude.model": "sonnet", "agentModels.codex.effort": "high"})
        hub = hub_chat.ask("Claude on Sonnet, Codex effort high")["turns"][-1]
        self.assertEqual([d["after"] for d in hub["diff"]], ["“sonnet”", "“high”"])
        hub_chat.apply(hub["id"])
        m = dashboard.load_settings()["agentModels"]
        self.assertEqual((m["claude"]["model"], m["codex"]["effort"], m["codex"]["model"]), ("sonnet", "high", ""))
        self.assertEqual(dashboard.load_settings()["defaultModel"], "sonnet")
        # An effort the model does not take is a problem, and nothing is proposed.
        self.answer = self.settings({"agentModels.codex.effort": "extreme"})
        hub = hub_chat.ask("Codex effort extreme")["turns"][-1]
        self.assertEqual(hub["kind"], "reply")
        self.assertIn("does not take the reasoning effort", hub["text"])

    def test_refused_settings_are_one_line_replies(self):
        dashboard.save_settings({"agentModels": {"codex": {"model": "gpt-6-sol"}}})
        for asked, line in (({"agentModels.codex.model": "gpt-5"}, "The Codex model stays gpt-6-sol: Ask the hub cannot change it."),
                            ({"poFallbackModels.codex": "gpt-5"}, "The Codex model stays gpt-6-sol: Ask the hub cannot change it."),
                            ({"backupRemote": "https://user:pw@example.invalid/x.git"}, "Ask the hub cannot change the backup remote"),
                            ({"feedbackRelayUrl": "https://example.invalid"}, "Ask the hub cannot change the feedback relay"),
                            ({"taskAgentTools": "own"}, "Ask the hub cannot change task agents' tools"),
                            ({"editor": "custom"}, "Ask the hub cannot choose a custom editor"),
                            ({"slackToken": "xoxb-made-up"}, "Ask the hub cannot change “slackToken”: secrets and tokens")):
            with self.subTest(asked=asked):
                self.answer = self.settings(asked)
                hub = hub_chat.ask("change it")["turns"][-1]
                self.assertEqual(hub["kind"], "reply")
                self.assertEqual(len(hub["text"].split("\n")), 1, hub["text"])
                self.assertTrue(hub["text"].startswith(line), hub["text"])
        s = dashboard.load_settings()
        self.assertEqual((s["agentModels"]["codex"]["model"], s["backupRemote"], s["taskAgentTools"]),
                         ("gpt-6-sol", "", "ensemble"))
        self.assertNotIn("slackToken", self.saved())
        self.assertFalse(hs.history_path().exists())

    def test_a_refused_key_next_to_a_good_one_is_named_and_the_good_one_proposed(self):
        self.answer = self.settings({"theme": "dark", "backupRemote": "x"})
        hub = hub_chat.ask("dark theme and back up to x")["turns"][-1]
        self.assertEqual(hub["kind"], "change")
        self.assertEqual([d["key"] for d in hub["diff"]], ["theme"])
        self.assertTrue(any("backup remote" in p for p in hub["check"]["problems"]), hub["check"])

    def test_a_value_the_save_drops_or_clamps(self):
        self.answer = self.settings({"reviewRoundsPerTask": 5000, "digestModel": "", "theme": "neon"})
        hub = hub_chat.ask("many rounds")["turns"][-1]
        self.assertEqual(hub["kind"], "change")
        self.assertEqual([(d["key"], d["after"]) for d in hub["diff"]],
                         [("reviewRoundsPerTask", f"{dashboard.REVIEW_ROUNDS_MAX} rounds")])
        probs = hub["check"]["problems"]
        self.assertTrue(any("5000 is saved as" in p for p in probs), probs)
        self.assertTrue(any(p.startswith("The model the PO's progress check runs on: Settings does not take") for p in probs), probs)
        self.assertTrue(any(p.startswith("Theme: Settings does not take") for p in probs), probs)
        # The same value written another way is nothing to change, and no problem.
        self.answer = self.settings({"reviewRoundsPerTask": "5"})
        hub = hub_chat.ask("five rounds")["turns"][-1]
        self.assertEqual((hub["kind"], hub["text"]), ("reply", "Settings already say this; nothing to change."))

    def test_stale_only_when_one_of_its_settings_changed(self):
        self.answer = self.settings({"preferAgent": "codex"}, "Prefer: Codex.")
        a = hub_chat.ask("Make reviews Codex")["turns"][-1]
        dashboard.save_settings({"theme": "dark"})                   # some other setting
        self.assertEqual(self.last()["state"], "proposed")
        dashboard.save_settings({"preferAgent": "claude"})           # its own setting
        last = self.last()
        self.assertEqual(last["state"], "stale")
        self.assertEqual(last["staleWhy"], "Prefer was changed to Claude since this was made, so it can no "
                                           "longer be applied as it is.")
        # Apply writes nothing, and says which setting changed.
        t = hub_chat.apply(a["id"])["turns"][-1]
        self.assertEqual(t["state"], "stale")
        self.assertIn("Prefer was changed to Claude", t["result"]["error"])
        self.assertEqual(dashboard.load_settings()["preferAgent"], "claude")
        self.assertFalse(hs.history_path().exists())

    def test_a_change_in_between_apply_and_its_thread_is_stale_and_saves_nothing(self):
        self.answer = self.settings({"preferAgent": "codex"})
        a = hub_chat.ask("prefer codex")["turns"][-1]
        with mock.patch.object(hub_chat, "BACKGROUND", True), mock.patch.object(hub_chat, "_run", lambda fn, *x: None):
            hub_chat.apply(a["id"])                                  # the thread has not run yet
        dashboard.save_settings({"preferAgent": "claude"})
        hub_chat._apply(a["id"])
        t = self.last()
        self.assertEqual(t["state"], "stale")
        self.assertIn("Prefer was changed to Claude", t["result"]["error"])
        self.assertEqual(dashboard.load_settings()["preferAgent"], "claude")
        self.assertFalse(hs.history_path().exists())

    def test_a_history_that_cannot_be_written_still_says_applied(self):
        """Review 1: the save happened, so the turn is Applied, with a line
        saying History missed it; never "did not go through"."""
        self.answer = self.settings({"theme": "dark"}, "Dark theme.")
        a = hub_chat.ask("Dark theme")["turns"][-1]
        with mock.patch.object(hs, "_add_history", side_effect=OSError("disk full")):
            t = hub_chat.apply(a["id"])["turns"][-1]
        self.assertEqual(t["state"], "applied")
        self.assertEqual(t["result"]["problems"], ["Saved, but it could not be added to History (OSError)."])
        self.assertEqual(dashboard.load_settings()["theme"], "dark")
        self.assertFalse(hs.history_path().exists())

    def _no_outside(self):
        import shutil
        shutil.rmtree(outside.root())
        for m in (outside, setup, hub_chat):
            m.reset()
        self.assertFalse(outside.root().exists())

    def test_a_settings_question_writes_nothing_under_outside(self):
        """Review 1: on a hub with no outside source set up, asking about or
        changing Settings leaves no outside/ folder."""
        self._no_outside()
        self.answer = "REPLY: The theme follows the system."
        hub_chat.ask("What is the theme?")
        self.answer = self.settings({"theme": "dark"}, "Dark theme.")
        a = hub_chat.ask("Dark theme")["turns"][-1]
        self.assertEqual(self.last()["state"], "proposed")
        hub_chat.apply(a["id"])
        self.assertEqual(self.last()["state"], "applied")
        self.assertEqual(dashboard.load_settings()["theme"], "dark")
        self.assertFalse(outside.root().exists(), sorted(p.name for p in outside.root().iterdir()) if outside.root().exists() else "")

    def test_a_routing_change_with_no_setup_file_yet_writes_it_on_apply(self):
        self._no_outside()
        first = setup.first_text()
        self.answer = self.change(first.replace("## Sources\n", "## Sources\n- Slack: on\n", 1)
                                  if "## Sources\n<!--" not in first else
                                  first.split("## Sources\n")[0] + "## Sources\n- Slack: on\n", "Slack is on.")
        a = hub_chat.ask("Turn Slack on")["turns"][-1]
        self.assertEqual(a["state"], "proposed", a)
        self.assertFalse(setup.setup_path().exists(), "asking writes nothing")
        hub_chat.apply(a["id"])
        self.assertEqual(self.last()["state"], "applied", self.last())
        self.assertIn("- Slack: on", setup.setup_path().read_text(encoding="utf-8"))

    def test_parse_settings(self):
        self.assertEqual(hub_chat.parse('SETTINGS: x\n<<<SETTINGS\n{"theme": "dark"}\nSETTINGS>>>'),
                         {"kind": "settings", "text": "x", "settings": {"theme": "dark"}})
        self.assertIsNone(hub_chat.parse('<<<SETTINGS\n{"theme": "dark"}\nSETTINGS>>>'))   # no SETTINGS line
        self.assertIsNone(hub_chat.parse("SETTINGS: x\n<<<SETTINGS\nnot json\nSETTINGS>>>"))
        self.assertIsNone(hub_chat.parse("SETTINGS: x\n<<<SETTINGS\n{}\nSETTINGS>>>"))
        self.assertIsNone(hub_chat.parse('SETTINGS: x\n<<<SETTINGS\n{"a": 1}\nSETTINGS>>>\nCHANGE: y\n<<<SETUP outside\n# z\nSETUP>>>'))

    def test_history_lists_settings_and_routing_newest_first_and_clear_keeps_it(self):
        self.answer = self.settings({"idleAlertMin": 10})
        hub_chat.apply(hub_chat.ask("Tell the PO sooner when a task sits idle")["turns"][-1]["id"])
        self.answer = f"CHANGE: Topic.\n<<<SETUP outside\n{FILE.replace('made-up pricing', 'made-up quotes')}SETUP>>>"
        hub_chat.apply(hub_chat.ask("Alpha is about quotes")["turns"][-1]["id"])
        items = hub_chat.history()["items"]
        self.assertEqual([i["kind"] for i in items], ["routing", "settings"])
        self.assertEqual(items[0]["ask"], "Alpha is about quotes")
        self.assertEqual(items[0]["lines"], ["Alpha: topic changed"])
        self.assertEqual(items[1]["changes"][0]["after"], "10 minutes")
        hub_chat.clear()
        items = hub_chat.history()["items"]
        self.assertEqual([i["kind"] for i in items], ["routing", "settings"])
        self.assertEqual(items[0]["ask"], "", "the question went with Clear; the change stays")

    def test_the_history_keeps_the_last_200(self):
        for i in range(205):
            hs._add_history({"at": i, "ask": f"q{i}", "text": "", "changes": []})
        rows = hs.history()
        self.assertEqual((len(rows), rows[0]["ask"], rows[-1]["ask"]), (200, "q5", "q204"))


class HubChatEndpoints(unittest.TestCase):
    """GET and POST /api/hub-chat answer the dashboard page only: never a
    bearer token, a request with no key cookie, or an agent's process."""
    setUp = HubChatTest.setUp

    def call(self, method, cookie=True, bearer=False, agent=False, path="/api/hub-chat", body=None):
        import io
        self.assertTrue(hub_chat.path().is_relative_to(self.state), "never the real data folder")
        raw = json.dumps(body or {"action": "ask", "text": "hello"}).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                     "Host": "127.0.0.1", "Origin": "http://127.0.0.1"}
        if cookie:
            h.headers["Cookie"] = f"ensemble_ui_t={dashboard._UI_KEY}"
        if bearer:
            h.headers["Authorization"] = "Bearer tok"
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        with mock.patch.object(dashboard.Handler, "_ui_cookie", return_value="ensemble_ui_t"), \
                mock.patch.object(dashboard.Handler, "_agent_peer", return_value="an agent's process" if agent else ""):
            (h.do_GET if method == "GET" else h.do_POST)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")

    def test_only_the_page(self):
        for method in ("GET", "POST"):
            for why in ({"cookie": False}, {"bearer": True}, {"agent": True}):
                with self.subTest(method=method, **why):
                    status, r = self.call(method, **why)
                    self.assertEqual((status, r.get("error")), (403, "page_only"), r)
        self.assertEqual(hub_chat.view()["turns"], [], "nothing was asked")
        status, r = self.call("POST")
        self.assertEqual(status, 200, r)
        self.assertEqual(self.call("GET")[1]["turns"][0]["text"], "hello")

    def test_apply_and_the_history_are_the_page_only_too(self):
        """#410: a Settings proposal is applied only from the page, and its
        history is shown only to the page."""
        self.answer = 'SETTINGS: Reviews stop after 3 rounds.\n<<<SETTINGS\n{"reviewRoundsPerTask": 3}\nSETTINGS>>>'
        hub = hub_chat.ask("Stop reviews after 3 rounds")["turns"][-1]
        self.assertEqual((hub["setup"], hub["state"]), ("settings", "proposed"))
        apply = {"action": "apply", "id": hub["id"]}
        for why in ({"cookie": False}, {"bearer": True}, {"agent": True}):
            with self.subTest(apply=True, **why):
                status, r = self.call("POST", body=apply, **why)
                self.assertEqual((status, r.get("error")), (403, "page_only"), r)
            with self.subTest(history=True, **why):
                status, r = self.call("GET", path="/api/hub-chat/history", **why)
                self.assertEqual((status, r.get("error")), (403, "page_only"), r)
        self.assertNotEqual(dashboard.load_settings()["reviewRoundsPerTask"], 3, "nothing saved")
        self.assertFalse(hs.history_path().exists())
        status, r = self.call("POST", body=apply)
        self.assertEqual((status, r["turns"][-1]["state"]), (200, "applied"), r)
        self.assertEqual(dashboard.load_settings()["reviewRoundsPerTask"], 3)
        status, r = self.call("GET", path="/api/hub-chat/history")
        self.assertEqual(status, 200, r)
        self.assertEqual([(i["kind"], i["ask"]) for i in r["items"]], [("settings", "Stop reviews after 3 rounds")], r)


if __name__ == "__main__":
    unittest.main()
