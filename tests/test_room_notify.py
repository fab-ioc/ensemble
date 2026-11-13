"""A line from an outside sender (#357): POST /api/room/notify.

A program on the hub's machine (a bot relaying an outside tester's answers)
posts {roomId, sender, text, key?}. Checked:

* the line is stored as a hub notice (noticeKind "external", from the hub,
  the label in senderLabel), never as the person's, and makes no point;
* it settles no open ask (the same ask a /api/room/say does settle);
* a request that is not plainly local is refused, with the access token too,
  and so is one from another site;
* the same key is posted once; the hourly limit per room and sender holds,
  and a refused line is not posted;
* the room's agent is typed "[<sender>] <text>", recorded as the outside
  sender's input (provenance kind "external", its label), and the chat reads
  that turn as such; a stopped agent is not started, and the line is then
  shown among a one-agent chat's turns;
* a bad sender label (empty, too long, control characters, brackets, one the
  hub or the person goes by) is refused, and the text is one line without
  control characters.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import input_provenance  # noqa: E402
import points  # noqa: E402

PORT = 8799
SENDER = "Tester (Discord)"


class FakeSess:
    def __init__(self, rid, ident="claude"):
        self.meta = {"agent": "claude", "room": rid, "identity": ident}
        self.id = "pty-po"

    def alive(self):
        return True


class Notify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "state").mkdir()
        self.typed: list[tuple] = []
        self.running = True
        self.real_hold_wake = dashboard.rotation.hold_wake
        patches = [
            mock.patch.object(dashboard, "DASHBOARD_DIR", base / "state"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard, "_type_input", self.fake_type),
            mock.patch.object(dashboard.ptyrun, "get",
                              lambda pid: FakeSess(self.rid) if self.running and pid == "pty-po" else None),
            mock.patch.object(dashboard.rotation, "hold_wake", lambda *a: False),
            mock.patch.object(dashboard.restart_drain, "holding", lambda: False),
            mock.patch.object(dashboard, "operator_name", lambda: "Pat"),
            mock.patch.dict(dashboard._NOTIFY_KEYS, clear=True),
            mock.patch.dict(dashboard._NOTIFY_SENT, clear=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.rid = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "model": "",
                                                 "role": "ProductOwner"}])["id"]
        full = chatroom.get_room(self.rid, public=False)
        chatroom.participant(full, "claude")["ptyId"] = "pty-po"
        chatroom.update_room(full)

    def fake_type(self, sess, text, provenance=None, **k):
        self.typed.append((sess.meta["identity"], text, provenance))
        return True

    # ---- requests ----

    def post(self, body, headers=None, path="/api/room/notify", client="127.0.0.1"):
        raw = json.dumps(body).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
        h.requestline = f"POST {path} HTTP/1.1"
        h.headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json",
                     "Content-Length": str(len(raw)), **(headers or {})}
        h.rfile = io.BytesIO(raw)
        h.wfile = io.BytesIO()
        h.client_address = (client, 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        try:
            return status, json.loads(payload or b"{}")
        except ValueError:  # the gate's plain-text 401
            return status, {"text": payload.decode(errors="replace")}

    def notify(self, text="answered \"Ship it?\": Yes", sender=SENDER, **kw):
        return self.post({"roomId": self.rid, "sender": sender, "text": text, **kw})

    def messages(self):
        return chatroom.get_room(self.rid, public=False)["messages"]

    def externals(self):
        return [m for m in self.messages() if m.get("noticeKind") == "external"]

    # ---- the stored line ----

    def test_a_line_is_a_hub_notice_with_its_sender_and_makes_no_point(self):
        status, r = self.notify()
        self.assertEqual(status, 200, r)
        (m,) = self.externals()
        self.assertEqual((m["kind"], m["from"], m["senderLabel"]), ("notice", "ensemble", SENDER))
        self.assertNotEqual(m["from"], chatroom.HUMAN_IDENTITY)
        self.assertEqual(m["text"], 'answered "Ship it?": Yes', "no point line, no prefix")
        self.assertTrue(m["typed"])
        self.assertFalse(any(x["from"] == chatroom.HUMAN_IDENTITY for x in self.messages()))
        led = points.load(self.rid) if points.exists(self.rid) else {"points": []}
        self.assertEqual(led["points"], [], "an outside line is no point of the person's")

    def test_it_settles_no_open_ask(self):
        chatroom.post_message(self.rid, "claude", "Ask: Ship it?\n- Yes\n- No", to="user")
        room = chatroom.get_room(self.rid, public=False)
        before = asks.open_in(room)
        self.assertEqual(len(before), 1, before)
        with mock.patch.object(dashboard, "_settle_asks") as settle, \
                mock.patch.object(points, "take") as take:
            self.assertEqual(self.notify("Yes")[0], 200)
        settle.assert_not_called()
        take.assert_not_called()
        led = points.load(self.rid) if points.exists(self.rid) else {}
        self.assertEqual(float(led.get("asksSettledAt") or 0), 0.0)
        self.assertEqual(asks.open_in(chatroom.get_room(self.rid, public=False)), before)
        # What the person's own words do, so the check above means something.
        self.assertEqual(self.post({"roomId": self.rid, "text": "Yes"}, path="/api/room/say")[0], 200)
        self.assertEqual(asks.open_in(chatroom.get_room(self.rid, public=False)), [])

    # ---- who may post ----

    def test_a_request_that_is_not_local_is_refused_even_with_the_token(self):
        with mock.patch.object(dashboard, "ACCESS_TOKEN", "tok-123"):
            for headers, client in (
                    ({"X-Ensemble-Token": "tok-123"}, "100.64.0.7"),
                    ({"X-Forwarded-For": "100.64.0.7", "X-Ensemble-Token": "tok-123"}, "127.0.0.1"),
                    ({"Host": "box.tail.ts.net", "X-Ensemble-Token": "tok-123"}, "127.0.0.1"),
                    ({}, "100.64.0.7")):
                status, r = self.post({"roomId": self.rid, "sender": SENDER, "text": "hi"},
                                      headers=headers, client=client)
                self.assertIn(status, (401, 403), (headers, client, r))
                if status == 403:
                    self.assertEqual(r["error"], "local_only")
        status, r = self.post({"roomId": self.rid, "sender": SENDER, "text": "hi"},
                              headers={"Origin": "https://evil.example"})
        self.assertEqual((status, r["error"]), (403, "cross_origin"))
        with mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: "pid 42 is an agent"):
            self.assertEqual(self.notify()[1]["error"], "agent")
        self.assertEqual(self.externals(), [])
        self.assertEqual(self.typed, [])

    # ---- floods ----

    def test_a_key_is_posted_once(self):
        self.assertEqual(self.notify(key="q-1")[0], 200)
        status, r = self.notify(key="q-1")
        self.assertEqual((status, r.get("duplicate")), (200, True))
        self.assertEqual(len(self.externals()), 1)
        self.assertEqual(len(self.typed), 1)
        self.assertEqual(self.notify(key="q-2")[0], 200, "another key is another line")
        self.assertEqual(len(self.externals()), 2)

    def test_the_hourly_limit_holds_per_room_and_sender(self):
        for i in range(dashboard.NOTIFY_PER_HOUR):
            self.assertEqual(self.notify(f"line {i}")[0], 200)
        status, r = self.notify("one too many")
        self.assertEqual((status, r["error"]), (429, "rate_limited"))
        self.assertIn(str(dashboard.NOTIFY_PER_HOUR), r["message"])
        self.assertEqual(len(self.externals()), dashboard.NOTIFY_PER_HOUR, "the refused one is not posted")
        self.assertEqual(self.notify("from another", sender="Other bot")[0], 200, "per sender")
        later = time.time() + dashboard.NOTIFY_WINDOW_S + 1
        with mock.patch.object(dashboard.time, "time", lambda: later):
            self.assertEqual(self.notify("an hour on")[0], 200)

    # ---- the agent ----

    def test_the_agent_is_typed_the_sender_and_the_text(self):
        status, r = self.notify('answered "Ship it?": Yes; file Documents/inbox/q-1.answer.json')
        self.assertEqual((status, r["typed"], r["agent"]), (200, True, "claude"))
        self.assertEqual(self.typed, [("claude", f'[{SENDER}] answered "Ship it?": Yes; '
                                                 f'file Documents/inbox/q-1.answer.json',
                                       {"kind": "external", "senderLabel": SENDER})])

    def test_the_typed_turn_reads_as_the_outside_senders(self):
        line = f"[{SENDER}] hello"
        info = dashboard._input_sender_info(line, {"kind": "external", "senderLabel": SENDER})
        self.assertEqual((info["kind"], info["senderType"], info["senderLabel"]),
                         ("external", "external", SENDER))
        self.assertEqual(dashboard.hub_input_kind(line)["kind"], "human",
                         "the prefix alone would read as the person's: the record decides")
        input_provenance.record(self.rid, "claude", line, info, session_id="sid-1")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        (turn,) = dashboard.classify_turns([{"role": "user", "text": line, "timestamp": stamp}],
                                           room_id=self.rid, identity="claude", session_id="sid-1")
        self.assertEqual((turn["kind"], turn["senderLabel"]), ("external", SENDER))

    def test_a_stopped_agent_is_not_started_and_the_line_is_shown(self):
        self.running = False
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room") as start, \
                mock.patch.object(dashboard.Handler, "_resume_room") as resume:
            status, r = self.notify()
        start.assert_not_called()
        resume.assert_not_called()
        self.assertEqual((status, r["typed"]), (200, False))
        self.assertIn("not running", r["note"])
        (m,) = self.externals()
        self.assertFalse(m["typed"])
        self.assertTrue(dashboard._hub_row(m), "a one-agent chat shows it among the turns")
        self.running = True
        self.notify("typed one")
        self.assertFalse(dashboard._hub_row(self.externals()[-1]), "typed: the turn shows it, not the row")

    # ---- held, replayed, or not journalled: still the outside sender's ----

    def test_a_wake_held_for_a_restart_keeps_its_sender(self):
        held = []
        with mock.patch.object(dashboard.restart_drain, "holding", lambda: True), \
                mock.patch.object(dashboard.restart_drain, "try_hold",
                                  lambda item, ready_only=None: held.append(item) or True):
            status, r = self.notify("hi")
        self.assertEqual((status, r["typed"]), (200, True))
        (item,) = held
        self.assertEqual(item["provenance"], {"kind": "external", "senderLabel": SENDER})
        rang = []
        h = SimpleNamespace(_ring=lambda rid, idents, text, provenance=None: rang.append(
            (idents, text, provenance)) or idents)
        self.assertEqual(dashboard.restart_drain._deliver(h, item), "")
        self.assertEqual(rang, [(["claude"], f"[{SENDER}] hi",
                                 {"kind": "external", "senderLabel": SENDER})])

    def test_a_wake_held_for_a_rotation_keeps_its_sender(self):
        key = (self.rid, "claude")
        rot = dashboard.rotation
        with mock.patch.dict(rot._ROTATING, {key: {}}), mock.patch.dict(rot._HELD, clear=True), \
                mock.patch.object(rot, "hold_wake", self.real_hold_wake):
            status, r = self.notify("hi")
            self.assertEqual((status, r["typed"]), (200, True))
            (w,) = rot._HELD[key]
        self.assertEqual(w, {"wake": f"[{SENDER}] hi",
                             "provenance": {"kind": "external", "senderLabel": SENDER}})
        sess = SimpleNamespace(tail=lambda: "> ", last_output=0.0, meta={"identity": "claude"})
        with mock.patch.object(rot, "_pty", lambda part: sess), \
                mock.patch.object(rot, "IDLE_S", 0.0), \
                mock.patch.object(rot._d.attention, "looks_like_prompt", lambda tail: False), \
                mock.patch.dict(rot._REPLAYING, {key: [w]}):
            rot._replay(self.rid, "claude", [w])
            deadline = time.time() + 10
            while not self.typed and time.time() < deadline:
                time.sleep(0.1)
        self.assertEqual(self.typed[-1][1:], (f"[{SENDER}] hi",
                                              {"kind": "external", "senderLabel": SENDER}))

    def test_a_turn_the_journal_missed_still_reads_as_the_outside_senders(self):
        self.assertEqual(self.notify("hi")[0], 200)
        # Another input of this session is journalled, so an unmatched turn
        # would be the person's.
        input_provenance.record(self.rid, "claude", "other", dashboard._input_sender_info("other"),
                                session_id="sid-1")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        (turn,) = dashboard.classify_turns([{"role": "user", "text": f"[{SENDER}] hi", "timestamp": stamp}],
                                           room_id=self.rid, identity="claude", session_id="sid-1")
        self.assertEqual((turn["kind"], turn["senderLabel"]), ("external", SENDER))
        (mine,) = dashboard.classify_turns([{"role": "user", "text": "hi", "timestamp": stamp}],
                                           room_id=self.rid, identity="claude", session_id="sid-1")
        self.assertEqual(mine["kind"], "human")

    def test_a_line_that_cannot_be_stored_is_answered_and_its_key_is_kept_once_typed(self):
        with mock.patch.object(chatroom, "post_notice", side_effect=OSError("disk full")):
            status, r = self.notify(key="q-1")
        self.assertEqual((status, r["error"], r["typed"]), (500, "not_stored", True))
        self.assertEqual(len(self.typed), 1)
        self.assertEqual(self.notify(key="q-1")[1].get("duplicate"), True, "a retry types it no second time")
        self.assertEqual(len(self.typed), 1)
        self.running = False
        with mock.patch.object(chatroom, "post_notice", side_effect=OSError("disk full")):
            status, r = self.notify(key="q-2")
        self.assertEqual((status, r["error"], r["typed"]), (500, "not_stored", False))
        self.assertEqual(dashboard._NOTIFY_SENT[(self.rid, SENDER.lower())].__len__(), 1,
                         "the one that went nowhere gave its slot back")
        self.assertEqual(self.notify(key="q-2")[0], 200, "and its key")

    # ---- what is refused ----

    def test_a_bad_sender_label_is_refused(self):
        for bad in ("", "   ", "x" * 41, "Tester\x1b[2J", "Tester\nNext", "Tab\there", " pad",
                    "[report]", "a]b", "user", "Hub", "ensemble", "CEO", "pat", "from the opten PO",
                    "report", None, 7):
            status, r = self.notify(sender=bad)
            self.assertEqual((status, r.get("error")), (400, "bad_sender"), repr(bad))
        self.assertEqual(self.externals(), [])
        self.assertEqual(self.notify(sender="x" * 40)[0], 200, "40 is allowed")
        self.assertEqual(self.notify(sender="Testeur·é (Discord)")[0], 200, "non-ASCII is printable")

    def test_the_text_is_one_line_without_control_characters(self):
        self.notify("first line\n\nsecond\x1b[201~\r\nthird\x07")
        self.assertEqual(self.externals()[-1]["text"], "first line second[201~ third")
        self.assertEqual(self.typed[-1][1], f"[{SENDER}] first line second[201~ third")
        for text in ("", "  \n ", "\x1b"):
            self.assertEqual(self.notify(text)[1]["error"], "missing_fields", repr(text))
        status, r = self.notify("x" * (dashboard.NOTIFY_TEXT_MAX + 1))
        self.assertEqual((status, r["error"]), (400, "text_too_long"))
        status, r = self.post({"roomId": "room-nothere0", "sender": SENDER, "text": "hi"})
        self.assertEqual((status, r["error"]), (404, "no_such_room"))


class ThePage(unittest.TestCase):
    def test_the_chat_labels_an_outside_line_and_shows_an_untyped_one(self):
        page = (ROOT / "session.html").read_text(encoding="utf-8")
        self.assertIn("external: 'From outside' };", page)
        self.assertIn("external: ['on an outside line',", page)
        self.assertIn("(m.noticeKind === 'external' && !m.typed)", page)
        # The sender's label is what the row names (senderName reads it first).
        self.assertIn("if (m && m.senderLabel) return String(m.senderLabel);", page)

    @unittest.skipUnless(shutil.which("node"), "node not found")
    def test_a_one_agent_chat_shows_an_untyped_line_among_its_turns(self):
        src = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")

        def line(start):
            at = src.index(start)
            return src[at:src.index("\n", at) + 1]
        at = src.index("function withHubRows(")
        code = "".join([line("const isHubRow ="), line("const isPoMsg ="),
                        "const ROOM_OBJ = null; const roomHasOlder = () => false;\n",
                        src[at:src.index("\n}\n", at) + 3]])
        js = code + """
const turns = [{ id: 't1', ts: 100 }, { id: 't2', ts: 300 }];
const ext = (id, ts, typed) => ({ id, ts, from: 'ensemble', kind: 'notice', noticeKind: 'external', senderLabel: 'Tester', text: 'hi', typed });
console.log(JSON.stringify([withHubRows(turns, { messages: [ext('u', 200, false), ext('y', 250, true)] }).map(m => m.id),
  withHubRows(turns, { messages: [ext('e', 50, false), { id: 'd', from: 'ensemble', kind: 'notice', noticeKind: 'due', ts: 60 }] }).map(m => m.id)]));
"""
        r = subprocess.run([shutil.which("node"), "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        # The untyped line goes in by its time; a typed one is already a turn.
        # One older than the first turn (posted before the first session)
        # stays, above it.
        self.assertEqual(json.loads(r.stdout), [["t1", "u", "t2"], ["e", "t1", "t2"]])
        # With no session yet, the chat shows those lines, not nothing.
        self.assertIn("if (!SOLO_SID) { CHAT_DRAWN = true; renderBubbles(((ROOM_OBJ && ROOM_OBJ.messages) || [])"
                      ".filter(m => m.kind === 'notice' && m.noticeKind === 'external' && !m.typed)); return; }", src)

if __name__ == "__main__":
    unittest.main()
