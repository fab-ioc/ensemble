"""Structured questions, routing, fallback cards, and click acknowledgement."""
from __future__ import annotations

import types
import io
import json
import re
import unittest
from unittest import mock
from pathlib import Path

import asks
import chatroom
import dashboard
import ensemble_tools
import points
from test_points import _World, http


class Validation(unittest.TestCase):
    def test_shipped_ask_instructions_do_not_name_the_operator(self):
        root = Path(__file__).resolve().parent.parent
        operator = Path.home().name
        if operator.lower() in {"root", "runner", "user"}:
            self.skipTest("generic machine account is not a person's display name")
        paths = ("asks.py", "chatroom.py", "dashboard.py", "ensemble_tools.py", "points.py",
                 "rotation.py", "session.html", "skills/ensemble/SKILL.md")
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(re.search(rf"\b{re.escape(operator)}\b", (root / path).read_text(encoding="utf8"), re.I))

    def test_normalization_and_rejection(self):
        got = asks.validated([{"question": "Deploy tonight?", "yesno": True},
                              {"question": "Which?", "options": [
                                  {"label": "Now", "recommended": True}, {"label": "Later"}]}])
        self.assertEqual([a["kind"] for a in got], ["yesno", "decision"])
        self.assertEqual(got[1]["recommended"], 0)
        bad = [[], [{"question": " "}], [{"question": "Q", "options": []}, {"question": "Q2", "options": [{}]}],
               [{"question": "Q", "options": [{"label": "x"}] * 7}],
               [{"question": "Q", "options": [{"label": "x"}, {"label": "X"}]}],
               [{"question": "Q", "options": [{"label": "x" * 81}]}],
               [{"question": "Q", "options": [{"label": "x", "recommended": True},
                                                {"label": "y", "recommended": True}]}],
               [{"question": "Q", "yesno": True, "options": [{"label": "Maybe"}]}]]
        for case in bad:
            with self.subTest(case=case), self.assertRaises(ValueError):
                asks.validated(case)

    def test_lists_are_role_scoped_for_both_agent_kinds(self):
        for kind in ("claude", "codex"):
            for role in ("engineer", "ProductOwner"):
                room = {"id": "room-x", "participants": [{"identity": kind, "kind": "agent", "agent": kind, "role": role}]}
                with mock.patch.object(ensemble_tools, "is_admin_caller", return_value=False), \
                     mock.patch.object(dashboard.chatroom, "is_on_mention", return_value=False), \
                     mock.patch.object(dashboard, "may_restart_hub", return_value=False), \
                     mock.patch.object(ensemble_tools, "_is_project_po", return_value=False):
                    with mock.patch.object(chatroom, "get_room", return_value=room):
                        listed = dashboard.Handler.__new__(dashboard.Handler)._mcp_method(
                            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "room-x", kind)
                    self.assertIn("ensemble_ask", {t["name"] for t in listed["result"]["tools"]})
            reviewer = {"id": "room-x", "participants": [{"identity": kind, "kind": "agent", "agent": kind, "role": "reviewer"}]}
            with mock.patch.object(ensemble_tools, "is_admin_caller", return_value=False), \
                 mock.patch.object(dashboard.chatroom, "is_on_mention", return_value=True), \
                 mock.patch.object(dashboard, "may_restart_hub", return_value=False), \
                 mock.patch.object(ensemble_tools, "_is_project_po", return_value=False):
                self.assertNotIn("ensemble_ask", {t["name"] for t in ensemble_tools.tool_schemas(reviewer, kind)})


class StructuredRoundTrip(_World):
    def _handler(self):
        return types.SimpleNamespace(_ring_recipients=lambda *a: [], _ring_report=lambda *a: [])

    def test_tool_click_and_comment_close_without_ack(self):
        rid = self.solo_room()
        room = chatroom.get_room(rid, public=False)
        ctx = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": ""}
        with mock.patch.object(ensemble_tools, "_project_po", return_value=None):
            result = ensemble_tools._ask(ctx, {"context": "A short reason.", "questions": [
                {"question": "Deploy tonight?", "yesno": True},
                {"question": "Any notes?"}]}, self._handler())
        mid = result["messageId"]
        msg = next(m for m in chatroom.get_room(rid)["messages"] if m["id"] == mid)
        self.assertEqual(len(msg["asks"]), 2)
        self.assertIn("A short reason.", msg["text"])
        self.assertEqual([a["question"] for a in asks.open_in(chatroom.get_room(rid))],
                         ["Deploy tonight?", "Any notes?"])
        def delivered(_handler, data):
            points.take(chatroom.get_room(rid, public=False), data["text"], key=data["key"])
            return 200, {"ok": True}
        with mock.patch.object(dashboard.Handler, "_room_resume", delivered):
            status, answer = http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 0, "option": "Yes"})
            self.assertEqual(status, 200, answer)
            status, answer = http("/api/room/ask", {"roomId": rid, "mid": mid, "n": 1, "comment": "After 22:00"})
            self.assertEqual(status, 200, answer)
        self.assertEqual(asks.open_in(chatroom.get_room(rid)), [])
        ledger = points.load(rid)
        self.assertEqual(len(ledger["points"]), 2)
        self.assertEqual({p["state"] for p in ledger["points"]}, {"acked"})
        self.assertEqual({p["ackedBy"] for p in ledger["points"]}, {"ask"})

    def test_task_question_routes_to_po_and_does_not_open_ceo_ask(self):
        rid = self.team_room()
        room = chatroom.get_room(rid, public=False)
        po_room = chatroom.create_room("PO", [{"identity": "po", "agent": "claude", "role": "ProductOwner"}])["id"]
        ctx = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": "project-test"}
        po = {"roomId": po_room, "identity": "po", "title": "PO"}
        with mock.patch.object(ensemble_tools, "_projects", return_value={"project-test": {"id": "project-test"}}), \
             mock.patch.object(ensemble_tools, "_project_po", return_value=po), \
             mock.patch.object(dashboard, "room_po_id", side_effect=lambda r, *a, **k: po_room if r and r.get("id") == rid else ""):
            result = ensemble_tools._ask(ctx, {"questions": [{"question": "Proceed?", "yesno": True}]}, self._handler())
            self.assertEqual(result["deliveredTo"]["roomId"], po_room)
            self.assertEqual(asks.open_in(chatroom.get_room(rid)), [])
            ceo = ensemble_tools._ask(ctx, {"questions": [{"question": "Release publicly?", "yesno": True}],
                                            "forCeo": True}, self._handler())
            self.assertEqual(ceo["deliveredTo"], "user")
            self.assertEqual([a["question"] for a in asks.open_in(chatroom.get_room(rid))],
                             ["Release publicly?"])
            # A reply to the task's PO may update answeredAt after this direct
            # user question; only the user's answer should settle the card.
            summary = chatroom.get_room(rid)
            summary["participants"][0]["answeredAt"] = summary["messages"][-1]["ts"] + 1
            self.assertEqual([a["question"] for a in asks.open_in(summary)],
                             ["Release publicly?"])
            def delivered(_handler, data):
                points.take(chatroom.get_room(rid, public=False), data["text"], key=data["key"])
                return 200, {"ok": True}
            with mock.patch.object(dashboard.Handler, "_room_resume", delivered):
                status, answer = http("/api/room/ask", {"roomId": rid, "mid": ceo["messageId"],
                                                          "n": 0, "option": "No"})
            self.assertEqual(status, 200, answer)
            self.assertEqual(asks.open_in(chatroom.get_room(rid)), [])
        own = chatroom.get_room(rid)["messages"][-1]
        self.assertEqual(own["askAudience"], "user")
        self.assertEqual(own["asks"][0]["question"], "Release publicly?")

    def test_invalid_tool_input_posts_nothing(self):
        rid = self.team_room()
        room = chatroom.get_room(rid, public=False)
        ctx = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": ""}
        before = len(room["messages"])
        with self.assertRaisesRegex(ensemble_tools.ToolError, "at most one recommended"):
            ensemble_tools._ask(ctx, {"questions": [{"question": "Which?", "options": [
                {"label": "A", "recommended": True}, {"label": "B", "recommended": True}]}]}, self._handler())
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), before)

    def test_chat_safety_net_only_for_an_agent_addressing_the_user(self):
        rid = self.team_room()
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler._ring_recipients = lambda *a: []
        ok = lambda result: result
        err = lambda *a: a
        with mock.patch.object(dashboard, "_history_nudge"):
            handler._mcp_tool_call("chat_send", {"to": "codex", "message": "Should I merge this?"},
                                   rid, "claude", ok, err)
            handler._mcp_tool_call("chat_send", {"to": "user", "message": "Should I merge this?"},
                                   rid, "claude", ok, err)
        messages = chatroom.get_room(rid)["messages"]
        self.assertNotIn("asks", messages[-2])
        self.assertEqual(messages[-1]["asks"][0]["question"], "Should I merge this?")
        chatroom.post_message(rid, "user", "Should I merge this?", to="claude")
        self.assertNotIn("asks", chatroom.get_room(rid)["messages"][-1])

    def test_reviewer_question_has_no_answer_card(self):
        rid = self.team_room()
        posted = chatroom.post_message(rid, "codex", "Should I merge this?", to="user")
        mid = posted["message"]["id"]
        self.assertEqual(asks.message_asks(chatroom.get_room(rid)), [])
        self.assertEqual(asks.balloon_asks(rid, mid), [])

    def test_room_response_resolves_linked_task_po(self):
        rid = self.team_room()
        po_room = chatroom.create_room("PO", [{"identity": "po", "agent": "claude", "role": "ProductOwner"}])["id"]
        self.assertFalse(chatroom.get_room(rid).get("projectId"))
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.path, handler.command, handler.request_version = f"/api/room?id={rid}", "GET", "HTTP/1.1"
        handler.requestline = f"GET {handler.path} HTTP/1.1"
        handler.headers = {"Host": "127.0.0.1"}
        handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
        handler.client_address = ("127.0.0.1", 50000)
        handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8765))
        handler.log_message = lambda *a: None
        with mock.patch.object(dashboard, "load_projects", return_value=[{"id": "project-test", "poRoomId": po_room}]), \
             mock.patch.object(dashboard, "load_session_projects", return_value={rid: "project-test"}):
            handler.do_GET()
        head, _, payload = handler.wfile.getvalue().partition(b"\r\n\r\n")
        self.assertEqual(int(head.split(b" ", 2)[1]), 200)
        self.assertEqual(json.loads(payload)["reportsToRoom"], po_room)


class SafetySample(unittest.TestCase):
    def test_questions_and_false_positives(self):
        positives = [
            "Decision needed: Start task #29 now? I recommend yes.",
            "A note.\n\nDo you want the wider panel?",
            "Your choice.\n\nDecision needed: Pick one\nA. Keep it\nB. Drop it",
        ]
        negatives = [
            "The CEO asked: 'What about backup?' I answered it in the report.",
            "> Do you want the wider panel?\n\nThat is the question the user asked us.",
            "```\nDecision needed: Deploy?\n```\n\nNo action is needed.",
            "Question for task #23: should I merge? I will ask its PO.",
            "The user asked us this: Should we deploy?",
        ]
        self.assertTrue(all(asks.safety(s) for s in positives))
        self.assertEqual(sum(bool(asks.safety(s)) for s in negatives), 0)
        self.assertEqual([o["label"] for o in asks.safety(positives[2])[0]["options"]], ["Keep it", "Drop it"])
