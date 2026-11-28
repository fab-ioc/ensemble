"""A project's Slack channel (#431, slack_channel.py + slack_reader.py),
checked against a stand-in for the Claude run: each item kind is posted once
(and not again after a restart), a project with no channel is never read, the
connector being down shows once and is retried slowly, the PO is woken at most
every 15 minutes and only when idle, and nothing from Slack touches a task.

Every id, name and text here is made up: the repo is public."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import rotation
import slack_channel
import slack_reader

CHAN = "C000TEST01"
CANVAS = "F000TEST01"
WS = "example.slack.com"
ANN, BOB = ("U000TEST02", "Ann Example"), ("U000TEST03", "Bob Example")
PREFIX = "mcp__claude_ai_Slack__"
SET_AT = 1_795_000_000.0


def ts(n: int) -> str:
    return f"{SET_AT + n:.6f}"


NO_MORE = "There are no more messages available.\n"


def more_at(offset: int) -> str:
    return f"More messages are available. Use cursor: Y{offset:05d}pg to read on.\n"


def channel_answer(msgs, name="made-up", info=NO_MORE):
    """msgs: [(who, ts, text)], any order (Slack gives newest first)."""
    parts = [f"Channel: #{name} ({CHAN})\n\n"]
    for (uid, nm), t, text in sorted(msgs, key=lambda m: -float(m[1])):
        parts.append(f"=== Message from {nm} ({uid}) at 2026-11-10 07:00:02 CET === \nMessage TS: {t}\n{text}\n\n")
    return json.dumps({"messages": "".join(parts), "pagination_info": info})


def thread_page(msgs, info):
    """A later page of a thread: no parent block, just replies."""
    parts = []
    for i, ((uid, nm), t, text) in enumerate(msgs, 1):
        parts.append(f"--- Reply {i} of {len(msgs)} ---\nFrom: {nm} ({uid})\nTime: 2026-11-11 11:03:16 CET\n"
                     f"Message TS: {t}\n{text}\n\n")
    return json.dumps({"messages": "".join(parts), "pagination_info": info})


def thread_answer(first, replies, info="There are no more messages in this thread.\n"):
    def block(m):
        (uid, nm), t, text = m
        return (f"From: {nm} <{nm.lower().replace(' ', '.')}@example.com> ({uid})\n"
                f"Time: 2026-11-11 11:03:16 CET\nMessage TS: {t}\n{text}\n")
    parts = ["=== THREAD PARENT MESSAGE ===\n", block(first), "Reactions: tada (2)\n\n",
             f"=== THREAD REPLIES ({len(replies)} total) ===\n\n"]
    for i, r in enumerate(replies, 1):
        parts.append(f"--- Reply {i} of {len(replies)} ---\n" + block(r) + "\n")
    return json.dumps({"messages": "".join(parts), "pagination_info": info})


def canvas_answer(title, threads):
    lines = [f"# {title}", "", "Goals for the quarter.", "", "## Comments"]
    lines += [f"- Comment thread {conv} {t}" for conv, t in threads]
    return json.dumps({"canvas": "\n".join(lines)})


class FakeSlack:
    """What the channel holds; answers each planned read through the real
    read_stream, as a run of `claude -p` would."""

    def __init__(self):
        self.messages = []            # (who, ts, text)
        self.threads = {}             # (conv, parent ts) -> (parent, [replies])
        self.canvases = {}            # F… -> (title, [(conv, ts)])
        self.init = None              # an init event to use instead (down cases)
        self.extra_calls = []
        self.runs = []
        self.fail_labels = set()
        self.fail_text = "channel_not_found"
        self.page = None              # page size, like Slack's limit (newest first for a channel)
        self.raw_canvas = {}          # F… -> the answer as given

    @staticmethod
    def _offset(q):
        c = q.args.get("cursor") or ""
        return int(c[1:6]) if c else 0

    def answer(self, q):
        if q.kind == "channel":
            oldest = float(q.args.get("oldest") or 0)
            msgs = sorted((m for m in self.messages if float(m[1]) >= oldest), key=lambda m: -float(m[1]))
            if not self.page:
                return channel_answer(msgs)
            off = self._offset(q)
            nxt = off + self.page
            return channel_answer(msgs[off:nxt], info=more_at(nxt) if nxt < len(msgs) else NO_MORE)
        if q.kind == "thread":
            key = (q.args["channel_id"], q.args["message_ts"])
            parent, replies = self.threads.get(key, (None, []))
            if parent is None:
                parent = next((m for m in self.messages if m[1] == q.args["message_ts"]), None)
            if parent is None:
                return json.dumps({"messages": ""})
            if not self.page:
                return thread_answer(parent, replies)
            allm = [parent] + list(replies)
            off = self._offset(q)
            nxt = off + self.page
            info = more_at(nxt) if nxt < len(allm) else NO_MORE
            if off == 0:
                return thread_answer(parent, allm[1:nxt], info)
            return thread_page(allm[off:nxt], info)
        if q.args["canvas_id"] in self.raw_canvas:
            return self.raw_canvas[q.args["canvas_id"]]
        title, threads = self.canvases[q.args["canvas_id"]]
        return canvas_answer(title, threads)

    def stream(self, queries):
        tools = sorted({PREFIX + slack_reader.TOOL_FOR[q.kind] for q in queries})
        init = self.init or {"type": "system", "subtype": "init", "tools": tools,
                             "mcp_servers": [{"name": "claude.ai Slack", "status": "connected"}]}
        calls = [{"type": "tool_use", "id": f"t{i}", "name": PREFIX + slack_reader.TOOL_FOR[q.kind],
                  "input": dict(q.args)} for i, q in enumerate(queries)]
        calls += [{"type": "tool_use", "id": f"x{i}", "name": n, "input": a}
                  for i, (n, a) in enumerate(self.extra_calls)]
        results = []
        for i, q in enumerate(queries):
            if q.label in self.fail_labels:
                results.append({"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True,
                                "content": [{"type": "text", "text": self.fail_text}]})
            else:
                results.append({"type": "tool_result", "tool_use_id": f"t{i}",
                                "content": [{"type": "text", "text": self.answer(q)}]})
        events = [init, {"type": "assistant", "message": {"content": calls}},
                  {"type": "user", "message": {"content": results}},
                  {"type": "result", "subtype": "error_max_turns", "is_error": True, "total_cost_usd": 0.001}]
        return [json.dumps(e) for e in events]

    def run(self, queries, prefix):
        self.runs.append(list(queries))
        return slack_reader.read_stream(self.stream(queries), queries, prefix)


class _FakePty:
    def __init__(self):
        self.typed = []


class _World(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.slack = FakeSlack()
        self.sess, self.running, self.idle = _FakePty(), True, True
        self.room = {"id": "room-po", "messages": [], "participants": [
            {"identity": "claude", "kind": "agent", "agent": "claude", "role": "Product owner",
             "ptyId": "pty-1", "sessionId": "sid-1"}]}
        self.project = {"id": "p1", "name": "Made-up", "poRoomId": "room-po",
                        "slackChannel": {"id": CHAN, "name": "made-up", "workspace": WS,
                                         "url": f"https://{WS}/archives/{CHAN}", "setAt": SET_AT}}
        self.projects = [self.project]
        self.settings = {}
        self.restart()
        patches = [
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir / "state"),
            mock.patch.object(dashboard, "load_settings", side_effect=lambda: dict(self.settings)),
            mock.patch.object(dashboard, "load_projects", side_effect=lambda: list(self.projects)),
            mock.patch.object(dashboard, "find_project", side_effect=lambda pid: next(
                (p for p in self.projects if p["id"] == pid), None)),
            mock.patch.object(dashboard.chatroom, "get_room", side_effect=lambda rid, public=True: (
                self.room if rid == "room-po" else None)),
            mock.patch.object(dashboard.chatroom, "po_identity", return_value="claude"),
            mock.patch.object(dashboard.chatroom, "post_notice", side_effect=self.post_notice),
            mock.patch.object(dashboard, "_type_input", side_effect=self.type_input),
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.sess if self.running else None),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
            mock.patch.object(rotation, "_submitted_lately", return_value=False),
            mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (None, lambda p: {})),
            mock.patch.object(rotation, "is_rotating", return_value=False),
            mock.patch.object(rotation, "awaiting_handover", return_value=False),
            mock.patch.object(slack_reader, "run", side_effect=self.slack.run),
            mock.patch.object(slack_channel, "_log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def restart(self):
        slack_channel._LAST = 0.0
        slack_channel._PO_BUSY.clear()
        slack_channel._WOKE.clear()

    def post_notice(self, rid, sender, text, meta):
        msg = {"id": f"m{len(self.room['messages'])}", "from": sender, "text": text, "kind": "notice", **meta}
        self.room["messages"].append(msg)
        return msg

    def type_input(self, sess, line):
        sess.typed.append(line)
        return True

    def slack_rows(self, kind=None):
        return [m for m in self.room["messages"] if m.get("noticeKind") == "slack"
                and (kind is None or m["slack"]["kind"] == kind)]

    def state(self):
        return json.loads((self.dir / "state" / "slack" / "p1.json").read_text(encoding="utf-8"))


class ItemTests(_World):
    def test_a_message_is_posted_once_and_not_again_after_a_restart(self):
        self.slack.messages.append((ANN, ts(-60), "from before the channel was set"))
        self.slack.messages.append((ANN, ts(100), "Can we ship <https://example.com|the beta> by Friday?"))
        out = slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual([i["kind"] for i in out], ["message"])
        [row] = self.slack_rows()
        self.assertEqual(row["slack"]["author"], "Ann Example")
        self.assertEqual(row["slack"]["text"], "Can we ship the beta (https://example.com) by Friday?")
        self.assertEqual(row["slack"]["permalink"], f"https://{WS}/archives/{CHAN}/p{ts(100).replace('.', '')}")
        self.assertEqual(row["outsideThread"], f"slack-{CHAN}-{ts(100).replace('.', '')}")
        self.assertEqual(row["slackKey"], f"m:{ts(100)}")
        self.assertTrue(dashboard._hub_row(row))
        # The first read starts where the channel was set.
        self.assertEqual(self.slack.runs[0][0].args["oldest"], f"{SET_AT:.6f}")
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 600), [])
        self.restart()
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 900), [])
        self.assertEqual(len(self.slack_rows()), 1)
        self.assertEqual(self.slack.runs[-1][0].args["oldest"], ts(100))

    def test_a_key_already_in_the_room_is_not_posted_again(self):
        # A crash after posting and before saving: the room has the line.
        self.slack.messages.append((ANN, ts(100), "hello"))
        slack_channel.read_project(self.project, SET_AT + 300)
        (self.dir / "state" / "slack" / "p1.json").unlink()
        self.restart()
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 600), [])
        self.assertEqual(len(self.slack_rows()), 1)

    def test_a_thread_reply_joins_its_parents_thread(self):
        parent = (ANN, ts(100), "Who reviews the plan?")
        self.slack.messages.append(parent)
        slack_channel.read_project(self.project, SET_AT + 300)
        self.slack.threads[(CHAN, ts(100))] = (parent, [(BOB, ts(400), "I will, tomorrow.")])
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["kind"] for i in out], ["reply"])
        reply = self.slack_rows("reply")[0]
        self.assertEqual(reply["outsideThread"], self.slack_rows("message")[0]["outsideThread"])
        self.assertEqual(reply["slack"]["parentTs"], ts(100))
        self.assertIn(f"?thread_ts={ts(100)}&cid={CHAN}", reply["slack"]["permalink"])
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 900), [])

    def test_a_canvas_comment_comes_with_its_line(self):
        link = f"https://{WS}/docs/T000TEST01/{CANVAS}"
        self.slack.messages.append((ANN, ts(100), f"The plan is in the canvas: {link}"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.assertIn(CANVAS, self.state()["canvases"])
        self.slack.canvases[CANVAS] = ("Plan", [(CHAN, ts(500))])
        self.slack.threads[(CHAN, ts(500))] = ((ANN, ts(500), "Goals for the quarter."),
                                               [(BOB, ts(510), "Too many goals?")])
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["kind"] for i in out], ["comment"])
        row = self.slack_rows("comment")[0]
        self.assertEqual(row["slack"]["line"], "Goals for the quarter.")
        self.assertEqual(row["slack"]["canvasTitle"], "Plan")
        self.assertEqual(row["slack"]["permalink"], link)
        # Two runs: the canvas lists the thread, then the thread is read.
        self.assertEqual([q.kind for q in self.slack.runs[-1]], ["thread"])
        # A later comment in the same thread comes too, once.
        self.slack.threads[(CHAN, ts(500))][1].append((ANN, ts(700), "No, it is fine."))
        out = slack_channel.read_project(self.project, SET_AT + 900)
        self.assertEqual([i["text"] for i in out], ["No, it is fine."])
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 1200), [])

    def test_bots_and_own_posts_are_not_delivered(self):
        self.slack.messages.append((("B000TEST01", "Some Bot"), ts(100), "build passed"))
        self.slack.messages.append((ANN, ts(200), "posted by the PO after a click"))
        slack_channel.note_own_post("p1", ts(200))
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 300), [])

    def test_a_deleted_thread_is_dropped_not_fatal(self):
        self.slack.messages.append((ANN, ts(100), "hello"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.slack.fail_labels = {f"thread:{ts(100)}"}
        self.slack.messages.append((BOB, ts(400), "second"))
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["text"] for i in out], ["second"])
        self.assertNotIn(ts(100), self.state()["threads"])
        self.assertEqual(self.state()["status"]["state"], "ok")


class ReviewOneTests(_World):
    """Review 1 of #431: nothing read is lost or hidden."""

    def test_a_message_not_posted_is_read_again(self):
        self.slack.messages += [(ANN, ts(100), "first"), (BOB, ts(200), "second")]
        ok = [False]

        def post(rid, sender, text, meta):
            return self.post_notice(rid, sender, text, meta) if ok[0] else None
        with mock.patch.object(dashboard.chatroom, "post_notice", side_effect=post):
            self.assertEqual(slack_channel.read_project(self.project, SET_AT + 300), [])
        self.assertEqual(self.state()["lastTs"], f"{SET_AT:.6f}")
        ok[0] = True
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["text"] for i in out], ["first", "second"])
        self.assertEqual(self.state()["lastTs"], ts(200))

    def test_a_failure_after_the_first_post_keeps_the_cursor_and_posts_the_rest_once(self):
        self.slack.messages += [(ANN, ts(100), "first"), (BOB, ts(200), "second")]
        n = [0]

        def post(rid, sender, text, meta):
            n[0] += 1
            return self.post_notice(rid, sender, text, meta) if n[0] == 1 else None
        with mock.patch.object(dashboard.chatroom, "post_notice", side_effect=post):
            self.assertEqual([i["text"] for i in slack_channel.read_project(self.project, SET_AT + 300)], ["first"])
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["text"] for i in out], ["second"])
        self.assertEqual([r["slack"]["text"] for r in self.slack_rows("message")], ["first", "second"])

    def test_more_new_threads_than_one_read_takes_are_all_read_in_turn(self):
        n = slack_channel.HOT_READS + slack_channel.TURN_READS + 5
        parents = [(ANN, ts(10 + i), f"q{i}") for i in range(n)]
        self.slack.messages += parents
        slack_channel.read_project(self.project, SET_AT + 300)
        for p in parents:
            self.slack.threads[(CHAN, p[1])] = (p, [(BOB, f"{float(p[1]) + 400:.6f}", "re " + p[2])])
        got = []
        for k in range(4):
            got += [i["text"] for i in slack_channel.read_project(self.project, SET_AT + 600 + 300 * k)]
        self.assertEqual(sorted(got), sorted("re " + p[2] for p in parents))
        self.assertTrue(all(len([q for q in r if q.kind == "thread"])
                            <= slack_channel.HOT_READS + slack_channel.TURN_READS for r in self.slack.runs))

    def test_a_backlog_longer_than_a_page_is_read_page_by_page(self):
        self.slack.page = 3
        self.slack.messages += [(ANN, ts(100 + i), f"m{i}") for i in range(8)]
        out = slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual([i["text"] for i in out], [f"m{i}" for i in range(8)])
        self.assertEqual(self.state()["lastTs"], ts(107))
        self.assertEqual(self.state()["page"], {})
        self.assertEqual(slack_channel.status_of(self.project)["state"], "ok")

    def test_a_backlog_longer_than_one_read_goes_on_at_the_next_read(self):
        self.slack.page = 2
        k = 2 * slack_channel.MAX_PAGES + 3
        self.slack.messages += [(ANN, ts(100 + i), f"m{i}") for i in range(k)]
        first = slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual(len(first), 2 * slack_channel.MAX_PAGES)
        self.assertEqual(self.state()["lastTs"], f"{SET_AT:.6f}")
        self.assertTrue(self.state()["page"]["cursor"])
        second = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual(sorted(i["text"] for i in first + second), sorted(f"m{i}" for i in range(k)))
        self.assertEqual(self.state()["page"], {})
        self.assertEqual(self.state()["lastTs"], ts(100 + k - 1))
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 900), [])

    def test_a_long_thread_is_read_page_by_page(self):
        self.slack.page = 2
        parent = (ANN, ts(100), "parent")
        self.slack.messages.append(parent)
        slack_channel.read_project(self.project, SET_AT + 300)
        self.slack.threads[(CHAN, ts(100))] = (parent, [(BOB, ts(200 + i), f"r{i}") for i in range(5)])
        out = slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual([i["text"] for i in out], [f"r{i}" for i in range(5)])
        self.assertTrue(all(r["slack"]["parentTs"] == ts(100) for r in self.slack_rows("reply")))

    def test_a_full_answer_with_no_cursor_does_not_move_on_and_says_so(self):
        msgs = [(ANN, ts(100 + i), f"m{i}") for i in range(slack_channel.CHANNEL_LIMIT)]
        self.slack.messages += msgs
        with mock.patch.object(FakeSlack, "answer", lambda me, q: json.dumps(
                {"messages": json.loads(channel_answer(msgs))["messages"]})):
            slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual(self.state()["lastTs"], f"{SET_AT:.6f}")
        st = slack_channel.status_of(self.project)
        self.assertEqual(st["state"], "partial")
        self.assertIn("more new messages", st["detail"])
        self.assertEqual(len(self.slack_rows("down")), 1)

    def test_a_thread_that_fails_for_another_reason_is_kept_and_said(self):
        self.slack.messages.append((ANN, ts(100), "hello"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.slack.fail_labels, self.slack.fail_text = {f"thread:{ts(100)}"}, "ratelimited"
        slack_channel.read_project(self.project, SET_AT + 600)
        self.assertIn(ts(100), self.state()["threads"])
        st = slack_channel.status_of(self.project)
        self.assertEqual(st["state"], "partial")
        self.assertIn("cannot be read", st["detail"])
        [row] = self.slack_rows("down")
        self.assertEqual(row["slack"]["state"], "partial")
        slack_channel.read_project(self.project, SET_AT + 900)
        self.assertEqual(len(self.slack_rows("down")), 1)          # said once
        self.slack.fail_labels = set()
        slack_channel.read_project(self.project, SET_AT + 1200)
        self.assertEqual(slack_channel.status_of(self.project)["state"], "ok")
        [up] = self.slack_rows("up")
        self.assertIn("read in full again", up["text"])

    def test_a_canvas_in_an_unknown_layout_says_so(self):
        link = f"https://{WS}/docs/T000TEST01/{CANVAS}"
        self.slack.messages.append((ANN, ts(100), f"canvas: {link}"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.slack.raw_canvas[CANVAS] = json.dumps({"canvas": "# Plan\n\nGoals.\n\n[thread C000TEST01 "
                                                              + ts(500) + "]"})
        slack_channel.read_project(self.project, SET_AT + 600)
        st = slack_channel.status_of(self.project)
        self.assertEqual(st["state"], "partial")
        self.assertIn("canvas", st["detail"])
        with self.assertRaises(slack_reader.ParseError):
            slack_reader.parse_canvas(json.dumps({"canvas": "# Plan\n\nGoals."}))
        self.assertEqual(slack_reader.parse_canvas(json.dumps({"canvas": "# Plan\n\nNo comments."}))["threads"], [])

    def test_every_item_has_a_link_whatever_the_channel_was_set_from(self):
        self.project["slackChannel"] = {"id": CHAN, "name": "", "workspace": "", "team": "T000TEST01",
                                        "url": f"https://app.slack.com/client/T000TEST01/{CHAN}", "setAt": SET_AT}
        self.slack.messages.append((ANN, ts(100), "hello"))
        slack_channel.read_project(self.project, SET_AT + 300)
        d = ts(100)
        self.assertEqual(self.slack_rows("message")[0]["slack"]["permalink"],
                         f"https://app.slack.com/client/T000TEST01/{CHAN}/thread/{CHAN}-{d}")
        self.assertEqual(slack_channel._permalink({"kind": "message", "ts": d}, {"id": CHAN}),
                         f"https://slack.com/app_redirect?channel={CHAN}")
        self.assertEqual(slack_channel.parse_ref(f"https://app.slack.com/client/T000TEST01/{CHAN}")["team"],
                         "T000TEST01")


class NoChannelTests(_World):
    def test_a_project_without_a_channel_is_never_read(self):
        del self.project["slackChannel"]
        self.assertEqual(slack_channel.read_project(self.project, SET_AT), [])
        slack_channel.maybe_tick(background=False)
        self.assertEqual(self.slack.runs, [])
        self.assertEqual(self.room["messages"], [])
        self.assertFalse((self.dir / "state" / "slack").exists())

    def test_a_project_without_a_po_is_not_read(self):
        self.project["poRoomId"] = ""
        slack_channel.maybe_tick(background=False)
        self.assertEqual(self.slack.runs, [])


class DownTests(_World):
    def test_not_connected_shows_once_retries_slowly_and_says_when_back(self):
        self.slack.init = {"type": "system", "subtype": "init", "tools": [],
                           "mcp_servers": [{"name": "claude.ai Gmail", "status": "connected"}]}
        self.slack.messages.append((ANN, ts(100), "while down"))
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 300), [])
        [down] = self.slack_rows("down")
        self.assertEqual(down["slack"]["state"], "not_connected")
        self.assertIn("#made-up cannot be read", down["text"])
        self.assertIn("every 30 minutes", down["text"])
        self.assertEqual(slack_channel.status_of(self.project)["state"], "not_connected")
        # Not due again for 30 minutes, then a second failure says nothing new.
        self.assertFalse(slack_channel._due(self.project, SET_AT + 300 + 29 * 60))
        self.assertTrue(slack_channel._due(self.project, SET_AT + 300 + 30 * 60))
        slack_channel.read_project(self.project, SET_AT + 2100)
        self.assertEqual(len(self.slack_rows("down")), 1)
        # Back: one line, and what arrived meanwhile is read.
        self.slack.init = None
        out = slack_channel.read_project(self.project, SET_AT + 3900)
        self.assertEqual([i["text"] for i in out], ["while down"])
        self.assertEqual(len(self.slack_rows("up")), 1)
        self.assertEqual(slack_channel.status_of(self.project)["state"], "ok")

    def test_not_signed_in(self):
        self.slack.init = {"type": "system", "subtype": "init", "tools": [],
                           "mcp_servers": [{"name": "claude.ai Slack", "status": "needs-auth"}]}
        slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual(self.slack_rows("down")[0]["slack"]["state"], "not_signed_in")
        self.assertIn("not signed in", self.slack_rows("down")[0]["text"])

    def test_a_write_tool_or_an_unplanned_call_fails_the_read(self):
        self.slack.messages.append((ANN, ts(100), "hello"))
        self.slack.extra_calls = [(PREFIX + "slack_send_message", {"channel_id": CHAN, "message": "hi"})]
        self.assertEqual(slack_channel.read_project(self.project, SET_AT + 300), [])
        self.assertIn("not asked for", self.state()["status"]["detail"])
        self.assertEqual(self.slack_rows("message"), [])
        self.slack.extra_calls = []
        self.slack.init = {"type": "system", "subtype": "init",
                           "tools": [PREFIX + "slack_read_channel", PREFIX + "slack_send_message"],
                           "mcp_servers": [{"name": "claude.ai Slack", "status": "connected"}]}
        with self.assertRaises(slack_reader.ReadError) as e:
            slack_reader.read_stream(self.slack.stream([slack_reader.Query("channel", "channel", {})]),
                                     [slack_reader.Query("channel", "channel", {})], PREFIX)
        self.assertEqual(e.exception.code, "write_tool")

    def test_the_command_allows_only_the_read_tools(self):
        cmd = slack_reader.build_command("claude", PREFIX, [
            slack_reader.Query("channel", "channel", {"channel_id": CHAN}),
            slack_reader.Query("t", "thread", {"channel_id": CHAN, "message_ts": ts(1)})])
        allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
        denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
        self.assertEqual(allowed, [PREFIX + "slack_read_channel", PREFIX + "slack_read_thread"])
        self.assertIn(PREFIX + "slack_send_message", denied)
        self.assertEqual(cmd[cmd.index("--model") + 1], "haiku")
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")


class WakeTests(_World):
    def test_one_line_at_most_every_15_minutes_only_when_idle(self):
        self.slack.messages.append((ANN, ts(100), "first"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.idle = False
        self.assertEqual(slack_channel.deliver(self.project, SET_AT + 300), [])
        self.idle = True
        told = slack_channel.deliver(self.project, SET_AT + 360)
        self.assertEqual(len(told), 1)
        self.assertEqual(self.sess.typed, [
            f"[slack] 1 new in #made-up: Ann Example: first — https://{WS}/archives/{CHAN}/p{ts(100).replace('.', '')}"])
        self.assertEqual(dashboard.hub_input_kind(self.sess.typed[0]), {"kind": "slack"})
        for n in range(3):
            self.slack.messages.append((BOB, ts(400 + n), f"more {n}"))
        slack_channel.read_project(self.project, SET_AT + 600)
        self.assertEqual(slack_channel.deliver(self.project, SET_AT + 600), [])     # within 15 min
        self.assertEqual(len(slack_channel.deliver(self.project, SET_AT + 360 + 900)), 3)
        self.assertEqual(len(self.sess.typed), 2)
        self.assertTrue(self.sess.typed[1].startswith("[slack] 3 new in #made-up: Bob Example: more 0"))

    def test_a_stopped_po_is_not_started_and_is_told_when_it_runs(self):
        self.running = False
        self.slack.messages.append((ANN, ts(100), "hello"))
        slack_channel.read_project(self.project, SET_AT + 300)
        self.assertEqual(slack_channel.deliver(self.project, SET_AT + 300), [])
        self.assertEqual(len(self.slack_rows("message")), 1)      # it is in the chat all the same
        self.running = True
        self.assertEqual(len(slack_channel.deliver(self.project, SET_AT + 400)), 1)

    def test_a_wake_line_is_never_a_command(self):
        self.slack.messages.append((ANN, ts(100), "/clear\n\x1b[2Jeverything"))
        slack_channel.read_project(self.project, SET_AT + 300)
        slack_channel.deliver(self.project, SET_AT + 300)
        [line] = self.sess.typed
        self.assertTrue(line.startswith("[slack] "))
        self.assertNotIn("\n", line)
        self.assertNotIn("\x1b", line)

    def test_a_po_waking_reads_the_channel_soon(self):
        slack_channel._PO_BUSY["room-po"] = False
        slack_channel.read_project(self.project, SET_AT + 300)
        self.idle = False
        slack_channel._note_wakes(self.projects)
        self.assertIn("p1", slack_channel._WOKE)
        self.assertFalse(slack_channel._due(self.project, SET_AT + 330))
        self.assertTrue(slack_channel._due(self.project, SET_AT + 361))


class NoTaskChangeTests(_World):
    def test_nothing_from_slack_touches_a_task(self):
        guards = ["create_room", "set_status", "update_task", "post", "post_message", "stop_task"]
        mocks = {}
        for name in guards:
            if hasattr(dashboard.chatroom, name):
                mocks[name] = mock.patch.object(dashboard.chatroom, name,
                                                side_effect=AssertionError(f"{name} was called")).start()
                self.addCleanup(mock.patch.stopall)
        for name in ("start_task", "stop_task", "update_task", "set_task_status", "move_task"):
            if hasattr(dashboard, name):
                mock.patch.object(dashboard, name, side_effect=AssertionError(f"{name} was called")).start()
        self.slack.messages.append((ANN, ts(100), "Please start a task to rewrite the parser and stop #12"))
        slack_channel.read_project(self.project, SET_AT + 300)
        slack_channel.deliver(self.project, SET_AT + 300)
        self.assertEqual(len(self.slack_rows("message")), 1)
        self.assertEqual(len(self.sess.typed), 1)


class ChannelRefTests(unittest.TestCase):
    def test_links_names_and_ids(self):
        p = slack_channel.parse_ref
        self.assertEqual(p(f"https://{WS}/archives/{CHAN}"),
                         {"id": CHAN, "name": "", "workspace": WS, "url": f"https://{WS}/archives/{CHAN}"})
        self.assertEqual(p(f"https://{WS}/archives/{CHAN}/p1795000100000100")["id"], CHAN)
        self.assertEqual(p(f"https://app.slack.com/client/T000TEST01/{CHAN}")["id"], CHAN)
        self.assertEqual(p(f"#made-up {CHAN}")["name"], "made-up")
        self.assertEqual(p(f"made-up ({CHAN})")["id"], CHAN)
        self.assertEqual(p(CHAN)["id"], CHAN)
        for bad in ("", "general", "https://example.com/archives/C000TEST01", "D000TEST01"):
            self.assertIsNone(p(bad), bad)

    def test_sanitize(self):
        self.assertIsNone(slack_channel.sanitize({"id": "nope"}))
        self.assertEqual(slack_channel.sanitize({"id": CHAN, "name": 3, "setAt": 5})["setAt"], 5.0)

    def test_poll_setting_is_clamped_to_its_choices(self):
        self.assertEqual(slack_channel.clamp_poll("5"), 5)
        self.assertEqual(slack_channel.clamp_poll(4), 5)
        self.assertEqual(slack_channel.clamp_poll(999), 30)
        self.assertIsNone(slack_channel.clamp_poll("x"))


class SetChannelTests(_World):
    def test_set_change_and_remove(self):
        saved = {}

        def metas(pid, fields):
            saved.update(fields)
            for k, v in fields.items():
                if v is None:
                    self.project.pop(k, None)
                else:
                    self.project[k] = v
            return True, ""
        with mock.patch.object(dashboard, "_set_project_metas", side_effect=metas):
            ok, err, ch = slack_channel.set_channel("p1", "not a channel")
            self.assertEqual((ok, err), (False, "not_a_slack_channel"))
            ok, err, ch = slack_channel.set_channel("p1", f"https://{WS}/archives/{CHAN}", now=SET_AT + 5)
            self.assertTrue(ok)
            self.assertEqual(ch["setAt"], SET_AT)       # the same channel keeps when it was set
            ok, err, ch = slack_channel.set_channel("p1", "other C000TEST09", now=SET_AT + 50)
            self.assertEqual((ch["id"], ch["setAt"]), ("C000TEST09", SET_AT + 50))
            ok, err, ch = slack_channel.set_channel("p1", "")
            self.assertTrue(ok)
            self.assertNotIn("slackChannel", self.project)


STAND_IN = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stands in for `claude -p`: makes the calls listed in the prompt and
    answers each from answers.json by its tool's short name."""
    import json, os, re, sys
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    answers = json.load(open(os.path.join(here, "answers.json")))
    prefix = "mcp__claude_ai_Slack__"
    allowed = sys.argv[sys.argv.index("--allowedTools") + 1].split(",")
    calls = []
    for line in sys.argv[-1].splitlines():
        m = re.match(r"\\d+\\. (\\S+) (\\{.*\\})$", line)
        if m:
            calls.append((m.group(1), json.loads(m.group(2))))
    out = [{"type": "system", "subtype": "init", "tools": allowed,
            "mcp_servers": [{"name": "claude.ai Slack", "status": "connected"}]},
           {"type": "assistant", "message": {"content": [
               {"type": "tool_use", "id": f"t{i}", "name": prefix + n, "input": a}
               for i, (n, a) in enumerate(calls)]}},
           {"type": "user", "message": {"content": [
               {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": answers[n]}]}
               for i, (n, a) in enumerate(calls)]}},
           {"type": "result", "subtype": "error_max_turns", "is_error": True, "total_cost_usd": 0.0001}]
    for e in out:
        print(json.dumps(e))
    sys.exit(1)
''')


class StandInTests(unittest.TestCase):
    def test_a_real_run_of_the_stand_in(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fake-claude")
            Path(path).write_text(STAND_IN)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
            Path(d, "answers.json").write_text(json.dumps(
                {"slack_read_channel": channel_answer([(ANN, ts(100), "hello")])}))
            with mock.patch.dict(os.environ, {"ENSEMBLE_SLACK_READER": path}):
                res, prefix = slack_reader.run([slack_reader.Query(
                    "channel", "channel", {"channel_id": CHAN, "oldest": ts(0)})], "")
        self.assertEqual(prefix, PREFIX)
        self.assertEqual([m.text for m in res.answers["channel"]["messages"]], ["hello"])
        self.assertEqual(res.answers["channel"]["name"], "made-up")


WRITE_STAND_IN = textwrap.dedent('''\
    #!/usr/bin/env python3
    """A run whose tool list holds a write tool: says so first, then would
    go on to the model (marked by a file) if it were not stopped."""
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    print(json.dumps({"type": "system", "subtype": "init", "tools": [
        "mcp__claude_ai_Slack__slack_read_channel", "mcp__claude_ai_Slack__slack_send_message"],
        "mcp_servers": [{"name": "claude.ai Slack", "status": "connected"}]}), flush=True)
    time.sleep(5)
    open(os.path.join(here, "went-on"), "w").write("yes")
''')


class StopAtWriteToolTests(unittest.TestCase):
    def test_a_run_with_a_write_tool_is_stopped_at_its_first_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fake-claude")
            Path(path).write_text(WRITE_STAND_IN)
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"ENSEMBLE_SLACK_READER": path}):
                with self.assertRaises(slack_reader.ReadError) as e:
                    slack_reader.run([slack_reader.Query("channel", "channel", {"channel_id": CHAN})], "")
            self.assertEqual(e.exception.code, "write_tool")
            time.sleep(0.3)
            self.assertFalse(os.path.exists(os.path.join(d, "went-on")))


if __name__ == "__main__":
    unittest.main()
