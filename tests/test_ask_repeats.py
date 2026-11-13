"""A question already open is never posted again (P325, #354).

ensemble_ask posts no second card for a question a card of the room still
waiting asks (the reference of the open card comes back), a batch posts only
its new questions, and the same call within a minute returns the first
result. A plain ``Ask:`` line asking what an open card asks makes no card:
the hub names its lines (``askRepeats``) and the page draws them as that
card's link, i.e. its chip. A fresh session's brief lists its open cards.
"""
from __future__ import annotations

import re
import shutil
import types
import unittest
from pathlib import Path
from unittest import mock

import asks
import chatroom
import dashboard
import ensemble_tools
import points
import rotation
from test_points import _World, http

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


class _Repeats(_World):
    def setUp(self):
        super().setUp()
        # The rule holds for every line of these rooms (they are all newer).
        (Path(dashboard.DASHBOARD_DIR) / asks.REPEATS_SINCE_NAME).write_text("0\n")
        asks._SINCE.clear()
        ensemble_tools._ASK_RECENT.clear()
        asks.forget()

    def handler(self):
        return types.SimpleNamespace(_ring_recipients=lambda *a: [], _ring_report=lambda *a: [])

    def ctx(self, rid):
        room = chatroom.get_room(rid, public=False)
        return {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": ""}

    def ask(self, rid, questions, context=""):
        with mock.patch.object(ensemble_tools, "_project_po", return_value=None):
            return ensemble_tools._ask(self.ctx(rid), {"questions": questions, "context": context}, self.handler())

    def n_messages(self, rid):
        return len(chatroom.get_room(rid)["messages"])

    def close(self, rid, mid, n):
        with mock.patch.object(dashboard, "_pty_alive", return_value=True), \
                mock.patch.object(dashboard.Handler, "_resume_room", lambda *a, **k: None):
            status, r = http("/api/room/ask/close", {"roomId": rid, "mid": mid, "n": n})
        # A team room with no running seat keeps the notice for later (400, kept).
        self.assertTrue(status == 200 or r.get("kept"), r)
        self.assertTrue(points.load(rid)["asks"][mid][str(n)]["closed"])
        asks.forget()


class EnsembleAsk(_Repeats):
    Q = {"question": "Should the back-test value Run C with the 189 DTE chain?", "yesno": True}

    def test_an_open_question_gets_its_old_reference_and_no_message(self):
        rid = self.team_room()
        first = self.ask(rid, [self.Q], "First time.")
        before = self.n_messages(rid)
        again = self.ask(rid, [{**self.Q, "question": "Should the back-test value Run C with the 189 DTE chain now?"}],
                         "Asked again after a rotation.")
        self.assertFalse(again["posted"])
        self.assertNotIn("messageId", again)
        self.assertEqual(again["references"], first["references"])
        self.assertEqual(self.n_messages(rid), before, "nothing posted")
        self.assertEqual(again["alreadyOpen"][0]["reference"], first["references"][0])
        self.assertRegex(again["alreadyOpen"][0]["openSince"], r"^\d\d-\d\d \d\d:\d\d$")
        self.assertIn("already open since", again["note"])
        self.assertIn("paste its reference", again["note"])
        self.assertEqual(len(asks.open_cards(chatroom.get_room(rid))), 1)

    def test_a_mixed_batch_posts_only_the_new_question(self):
        rid = self.team_room()
        first = self.ask(rid, [self.Q])
        got = self.ask(rid, [{"question": "Which broker feed for the replay?",
                              "options": [{"label": "IBKR", "recommended": True}, {"label": "Polygon"}]}, self.Q])
        self.assertTrue(got["posted"])
        mid = got["messageId"]
        msg = next(m for m in chatroom.get_room(rid)["messages"] if m["id"] == mid)
        self.assertEqual([(a["n"], a["question"]) for a in msg["asks"]], [(0, "Which broker feed for the replay?")])
        self.assertEqual(got["references"], [f"http://127.0.0.1:8765/session?room={rid}&msg={mid}&part=ask:0",
                                             first["references"][0]])
        self.assertEqual(got["questions"], 1)
        self.assertEqual([a["question"] for a in got["alreadyOpen"]], [self.Q["question"]])
        self.assertEqual(len(asks.open_cards(chatroom.get_room(rid))), 2)

    def test_a_closed_question_can_be_asked_again(self):
        rid = self.team_room()
        first = self.ask(rid, [self.Q])
        self.close(rid, first["messageId"], 0)
        again = self.ask(rid, [self.Q], "Changed.")
        self.assertTrue(again["posted"])
        self.assertNotEqual(again["messageId"], first["messageId"])

    def test_the_same_call_within_a_minute_returns_the_first_result(self):
        rid = self.team_room()
        first = self.ask(rid, [self.Q], "Same.")
        before = self.n_messages(rid)
        retry = self.ask(rid, [self.Q], "Same.")
        self.assertEqual(self.n_messages(rid), before)
        self.assertTrue(retry.pop("repeated"))
        self.assertEqual(retry, first, "a transport retry gets what the first call returned")
        # Past the minute it is a new call: the open card still stops a second post.
        later = ensemble_tools.time.time() + ensemble_tools.ASK_RETRY_S + 1
        with mock.patch.object(ensemble_tools.time, "time", return_value=later):
            third = self.ask(rid, [self.Q], "Same.")
        self.assertNotIn("repeated", third)
        self.assertFalse(third["posted"])
        self.assertEqual(self.n_messages(rid), before)

    def test_an_error_is_not_kept_for_the_retry(self):
        rid = self.team_room()
        bad = [{"question": "Which?", "options": [{"label": "A", "recommended": True},
                                                  {"label": "B", "recommended": True}]}]
        for _ in range(2):
            with self.assertRaises(ensemble_tools.ToolError):
                self.ask(rid, bad)
        self.assertEqual(ensemble_tools._ASK_RECENT, {})


class PlainAskLine(_Repeats):
    ASK = "**Ask (yes/no):** Should the back-test value Run C with the 189 DTE chain?"

    def post(self, rid, text):
        mid = chatroom.post_message(rid, "claude", text, to="user")["message"]["id"]
        asks.forget()
        return mid

    def test_a_repeated_line_makes_no_card_and_is_the_cards_link(self):
        rid = self.team_room()
        first = self.post(rid, "Run C needs a valuation.\n\n" + self.ASK)
        again = self.post(rid, "Re P1213: still on it.\n\n" + self.ASK.replace("chain?", "chain, please?")
                          + "\n\n**Ask (yes/no):** Merge #353 first?")
        room = chatroom.get_room(rid)
        cards = asks.open_cards(room)
        self.assertEqual(sorted((c["mid"], c["question"]) for c in cards),
                         sorted([(first, "Should the back-test value Run C with the 189 DTE chain?"),
                                 (again, "Merge #353 first?")]))
        self.assertEqual([(a["mid"], a["n"]) for a in asks.open_in(room)], [(first, 0), (again, 0)])
        self.assertEqual(asks.repeats_of(room), {again: [[2, 3, [[first, 0]]]]})
        self.assertEqual([a["question"] for a in asks.balloon_asks(rid, again)], ["Merge #353 first?"])
        shown = asks.shown_text(room["messages"][-1]["text"], asks.repeats_of(room)[again], rid)
        self.assertIn(f"\n\nhttp://hub/session?room={rid}&msg={first}&part=ask:0\n\n", shown)
        self.assertTrue(asks.reminder(shown.split("\n\n")[1]))

    def test_the_room_poll_names_the_lines_for_the_page(self):
        rid = self.team_room()
        first = self.post(rid, self.ASK)
        again = self.post(rid, self.ASK)
        room = dashboard._annotate_room_liveness(chatroom.get_room(rid), with_points=True)
        self.assertEqual(room.get("askRepeats"), {again: [[0, 1, [[first, 0]]]]})

    def test_a_line_repeating_an_answered_card_is_a_new_card(self):
        rid = self.team_room()
        first = self.post(rid, self.ASK)
        self.close(rid, first, 0)
        again = self.post(rid, self.ASK)
        room = chatroom.get_room(rid)
        self.assertEqual(asks.repeats_of(room), {})
        self.assertEqual([c["mid"] for c in asks.open_cards(room)], [again])

    def approve(self, rid, mid, at):
        led = points.load(rid)
        led.setdefault("approvals", {})[mid] = at
        points._save(rid, led)
        asks._CARDS.clear()
        asks.forget()

    def test_an_approval_that_settles_nothing_leaves_the_card_waiting(self):
        rid = self.team_room()
        first = self.post(rid, self.ASK)        # yes/no, no recommendation: Go ahead answers nothing
        self.approve(rid, first, ensemble_tools.time.time())
        again = self.post(rid, self.ASK)
        room = chatroom.get_room(rid)
        self.assertEqual([c["mid"] for c in asks.open_cards(room)], [first])
        self.assertEqual(asks.repeats_of(room, fresh=True), {again: [[0, 1, [[first, 0]]]]})

    def test_an_approval_after_the_repeat_keeps_the_line_a_repeat(self):
        rid = self.team_room()
        ask = "Ask: Which chain should Run C use?\n- 189 DTE (recommended)\n- 30 DTE"
        first = self.post(rid, ask)
        again = self.post(rid, ask)
        self.assertEqual(list(asks.repeats_of(chatroom.get_room(rid), fresh=True)), [again])
        ensemble_tools.time.sleep(0.01)
        self.approve(rid, first, ensemble_tools.time.time())
        room = chatroom.get_room(rid)
        self.assertEqual(list(asks.repeats_of(room, fresh=True)), [again], "approved after the line")
        self.assertEqual(asks.open_cards(room), [])
        # Approved before a line: that line asks again, and is a card.
        ensemble_tools.time.sleep(0.01)
        third = self.post(rid, ask)
        room = chatroom.get_room(rid)
        self.assertEqual(list(asks.repeats_of(room, fresh=True)), [again])
        self.assertEqual([c["mid"] for c in asks.open_cards(room)], [third])

    def test_lines_from_before_the_rule_keep_their_cards(self):
        rid = self.team_room()
        first = self.post(rid, self.ASK)
        again = self.post(rid, self.ASK)
        asks._SINCE.clear()
        (Path(dashboard.DASHBOARD_DIR) / asks.REPEATS_SINCE_NAME).write_text(f"{ensemble_tools.time.time() + 5}\n")
        asks._CARDS.clear()
        asks.forget()
        room = chatroom.get_room(rid)
        self.assertEqual(asks.repeats_of(room), {})
        self.assertEqual(sorted(c["mid"] for c in asks.open_cards(room)), sorted([first, again]))

    def test_the_mark_is_written_once_at_hub_start_and_reads_never_write(self):
        p = Path(dashboard.DASHBOARD_DIR) / asks.REPEATS_SINCE_NAME
        p.unlink()
        asks._SINCE.clear()
        rid = self.team_room()
        self.post(rid, self.ASK)
        self.post(rid, self.ASK)
        room = chatroom.get_room(rid)
        asks.open_cards(room)
        asks.repeats_of(room, fresh=True)
        self.assertEqual(asks.repeats_since(), float("inf"))
        self.assertFalse(p.exists(), "a read writes no mark")
        at = asks.mark_repeats_since()
        self.assertEqual(float(p.read_text()), round(at, 3))
        asks._SINCE.clear()
        self.assertEqual(asks.mark_repeats_since(), round(at, 3), "kept, not written again")
        self.assertIn("asks.mark_repeats_since()", (ROOT / "dashboard.py").read_text(encoding="utf-8"))

    def test_chat_send_says_the_line_made_no_card(self):
        rid = self.team_room()
        self.post(rid, self.ASK)
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler._ring_recipients = lambda *a: []
        with mock.patch.object(dashboard, "_history_nudge"):
            out = handler._mcp_tool_call("chat_send", {"to": "user", "message": self.ASK}, rid, "claude",
                                         lambda r: r, lambda *a: a)
        self.assertIn("made no new card", out["content"][0]["text"])


class Brief(_Repeats):
    def test_the_fresh_sessions_brief_lists_its_open_cards(self):
        rid = self.team_room()
        refs = []
        topics = ["retention", "backups", "pricing", "logo", "release", "hiring", "budget", "vendor",
                  "roadmap", "launch", "naming", "support"]
        for i, t in enumerate(topics):
            refs.append(self.ask(rid, [{"question": f"Decide {t} now?", "yesno": True}], f"c{i}")["references"][0])
        room = chatroom.get_room(rid)
        block = asks.prompt_block(room)
        lines = block.split("\n")
        self.assertIn("do not ask them again", lines[0])
        self.assertIn("paste its link alone", lines[0])
        self.assertIn("closes it", lines[0])
        self.assertEqual(len(lines), 1 + 10 + 1)
        self.assertIn(refs[-1], lines[1], "newest first")
        self.assertRegex(lines[1], r"\(open \d+ min\)$")
        self.assertEqual(lines[-1], "- +2 more")
        with mock.patch.object(dashboard, "operator_name", return_value="sam"):
            text = rotation.task_first_prompt({**room, "title": "T"}, "sid-old", 210_000,
                                              Path("/t/TASK-HANDOVER.md"), False)
        self.assertIn(block, text)
        line = asks.note_line(room, "claude")
        self.assertTrue(line.startswith("[asks] "))
        self.assertIn(refs[-1], line)
        self.assertIn("more.", line)
        self.assertLessEqual(len(line), asks.NOTE_MAX)
        self.assertEqual(asks.prompt_block(chatroom.get_room(self.team_room())), "")

    def test_the_strict_rule_is_in_the_notes_and_the_skill(self):
        skill = (ROOT / "skills/ensemble/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Never ask an open question again", skill)
        self.assertIn("Your open cards stay open", skill)
        self.assertIn("never ask an open question again", dashboard.ASK_NOTE)
        self.assertIn("closed first", dashboard.ASK_NOTE)
        tool = next(t for t in ensemble_tools.TOOLS if t["name"] == "ensemble_ask") \
            if hasattr(ensemble_tools, "TOOLS") else None
        if tool:
            self.assertIn("not posted again", tool["description"])


QA_RE = SESSION[SESSION.index("const QA_RE = {"):SESSION.index("\n};\n", SESSION.index("const QA_RE = {")) + 4]


def _fn(name):
    m = re.search(rf"^function {name}\(", SESSION, re.M)
    return SESSION[m.start():SESSION.index("\n}\n", m.start()) + 3]


@unittest.skipUnless(NODE, "node is not installed")
class Page(unittest.TestCase):
    def test_the_page_shows_the_same_text_as_the_hub(self):
        from tests.test_task_refs import node
        text = "Re P1213: still on it.\r\n\r\n**Ask (yes/no):** Ship it?\n  - Yes\n  - No\n\nAsk: Merge first?"
        reps = [[2, 5, [["m1", 0], ["m2", 1]]]]
        js = "\n".join([
            "globalThis.location = new URL('http://127.0.0.1:8765/session?id=room-a');",
            "const ROOM = 'room-a';",
            "let ROOM_OBJ = {askRepeats: {mx: %s}};" % __import__("json").dumps(reps),
            QA_RE,
            re.search(r"^const askLines = .*$", SESSION, re.M).group(0),
            _fn("askRepeatText"), "const ASK_SHOWN = new Map();", _fn("askShown"),
            "const m = {id: 'mx', text: %s};" % __import__("json").dumps(text),
            "const s = askShown(m);",
            "console.log(JSON.stringify({text: s.text, shown: s.askShown, again: askShown(s) === s,"
            " other: askShown({id: 'my', text: 'x'}).text}));"])
        r = node(js)
        hub = asks.shown_text(text, reps, "room-a").replace("http://hub", "http://127.0.0.1:8765")
        self.assertEqual(r["text"], hub)
        self.assertTrue(r["shown"])
        self.assertTrue(r["again"], "never applied twice")
        self.assertEqual(r["other"], "x")
        self.assertEqual(asks.parse(hub)[0]["question"], "Merge first?")
        self.assertEqual(len(asks.parse(hub)), 1)

    def test_the_page_reads_the_shown_text_for_cards_and_balloons(self):
        self.assertIn("  m = askShown(m);\n", _fn("asksOf"))
        self.assertIn("if (!a.length && !m.askShown) a = safetyAsk(t);", _fn("asksOf"))
        self.assertIn("  const balloon = (m, i) => {\n    m = askShown(m);\n", SESSION)


if __name__ == "__main__":
    unittest.main()
