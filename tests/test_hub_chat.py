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

import dashboard  # noqa: F401  (imported first: its import binds outside to the real data folder, setUp then binds a temporary one)
import hub_chat
import outside
import outside_route as route
import outside_setup as setup

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
                  mock.patch.object(hub_chat, "BACKGROUND", False)):
            p.start()
            self.addCleanup(p.stop)
        outside.bind(hub)
        route.bind(hub)
        for m in (outside, setup, hub_chat):
            m.reset()
            self.addCleanup(m.reset)
        outside.set_source("slack", type="slack", mode="on", settings={"reader": "connector"})
        for f in (setup.setup_path(), hub_chat.path()):
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



class HubChatEndpoints(unittest.TestCase):
    """GET and POST /api/hub-chat answer the dashboard page only: never a
    bearer token, a request with no key cookie, or an agent's process."""
    setUp = HubChatTest.setUp

    def call(self, method, cookie=True, bearer=False, agent=False):
        import io
        self.assertTrue(hub_chat.path().is_relative_to(self.state), "never the real data folder")
        raw = json.dumps({"action": "ask", "text": "hello"}).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/hub-chat", method, "HTTP/1.1"
        h.requestline = f"{method} /api/hub-chat HTTP/1.1"
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


if __name__ == "__main__":
    unittest.main()
