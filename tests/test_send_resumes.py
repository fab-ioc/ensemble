"""Sending to a stopped session brings it back and delivers the message.

The hub side (``/api/room/resume`` with a text, ``Handler._resume_room`` and
the deliver-after-resume thread):

* a message to a stopped room starts ONE resume and is held by the hub; what
  is sent while the agents come up joins the queue, in order, and a second
  request (another browser, a plain Resume) starts nothing;
* once the agent's screen has settled it gets ONE input: the resume note (an
  owner) or nothing (a PO) first, then every held message in order — never a
  second wake; a room's messages are posted first and its owner rung once;
* a resume the hub refuses, or an agent that dies or sits on a prompt before
  the message could be typed, keeps the message with the reason; Retry sends
  it again (starting a resume again), Discard drops it;
* the room payload carries what is held (``pending``), so any copy of the
  chat shows it;
* a running room gets the text straight away, and is not launched twice.

And the page (session.html): the box and Send stay usable in a stopped
session, the hint says sending resumes it, and Send goes the resume way.
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


class FakePty:
    """A terminal that has drawn its screen and is quiet, unless told otherwise."""

    def __init__(self, pty_id, alive=True, tail="> ", prompt=False):
        self.id = pty_id
        self._alive = alive
        self._tail = tail
        self.prompt = prompt
        self.last_output = 0.0
        self.last_input = 0.0
        self.typed = []

    def alive(self):
        return self._alive

    def tail(self, *a, **k):
        return self._tail

    def send_line(self, text):
        self.typed.append(text)


class Resumes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        dashboard._RESUMES.clear()
        self.ptys: dict[str, FakePty] = {}
        self.patches = [
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.ptys.get(pid)),
            mock.patch.object(dashboard.rotation, "IDLE_S", 0),
            mock.patch.object(dashboard.attention, "looks_like_prompt",
                              lambda tail: tail == "PROMPT"),
            mock.patch.object(dashboard, "load_projects", lambda: self.projects),
            mock.patch.object(dashboard, "RESUME_NOTE_WAIT_S", 2),
        ]
        for p in self.patches:
            p.start()
        self.projects = []
        self.starts = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.join()
        dashboard._RESUMES.clear()
        chatroom.ROOMS_DIR = self.old_dir
        self.temp.cleanup()

    # ---- helpers ----
    def room(self, agents=("claude",), mode="solo", launched=True):
        # The second agent is a resumed partner, not a reviewer started per request.
        parts = [{"identity": a, "agent": a, "model": "", "role": "engineer" if i == 0 else "designer"}
                 for i, a in enumerate(agents)]
        r = chatroom.create_room("t", parts)
        full = chatroom.get_room(r["id"], public=False)
        full["mode"] = mode
        full["launched"] = launched
        full["messages"] = [{"id": "m0", "from": "user", "text": "spec", "to": "", "ts": time.time()}]
        chatroom.update_room(full)
        return chatroom.get_room(r["id"], public=False)

    def handler(self, tail="> ", alive=True):
        """A handler whose resume spawns fake terminals: each resumed agent
        gets a FakePty (settled, unless ``tail`` says a prompt is up)."""
        test = self

        class H(dashboard.Handler):
            def __init__(self):
                pass

            def _resume_room_agent_pty(self, room_full, part, collab=True, seed="", human=False):
                test.starts += 1
                pid = f"pty-{part['identity']}-{test.starts}"
                test.ptys[pid] = FakePty(pid, alive=alive, tail=tail)
                return {"ptyId": pid, "cwd": "", "sessionId": part.get("sessionId", ""),
                        "prompted": False}

            def _launch_room_agent_pty(self, *a, **k):
                raise AssertionError("a resumed room is not launched fresh")

        return H()

    def join(self, timeout=8):
        for t in threading.enumerate():
            if t.name.startswith("resume-deliver-"):
                t.join(timeout)

    def typed(self):
        return {pid: p.typed for pid, p in self.ptys.items() if p.typed}

    # ---- one resume, one input ----
    def test_a_message_to_a_stopped_owner_is_its_first_input_with_the_note(self):
        room = self.room()
        h = self.handler()
        out = h._resume_room(room, text="please continue with step 2")
        self.assertEqual(out["queued"], 1)
        self.assertEqual(len(out["resumed"]), 1)
        self.assertEqual(dashboard.pending_input(room["id"])["state"], "resuming")
        self.join()
        self.assertEqual(self.starts, 1)
        [(pid, lines)] = self.typed().items()
        self.assertEqual(len(lines), 1, "the agent was woken more than once")
        self.assertTrue(lines[0].startswith("\x1b[200~"), "a multi-line input goes as a bracketed paste")
        self.assertEqual(lines[0], "\x1b[200~" + dashboard.RESUME_NOTE + "\n\nplease continue with step 2\x1b[201~")
        self.assertIsNone(dashboard.pending_input(room["id"]), "held after delivery")
        part = chatroom.get_room(room["id"], public=False)["participants"][0]
        self.assertTrue(part.get("resumedAt"), "attention is not told the owner was asked to carry on")

    def test_a_po_gets_only_the_message(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()
        h._resume_room(room, text="what is the status?")
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["what is the status?"])

    def test_two_quick_sends_start_one_resume_and_arrive_in_order(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()
        first = h._resume_room(room, text="first")
        second = h._resume_room(room, text="second")
        plain = h._resume_room(room)
        self.assertEqual((first["queued"], second["queued"], plain["queued"]), (1, 1, 0))
        self.assertTrue(second.get("inFlight") and plain.get("inFlight"))
        self.assertEqual(second["resumed"], first["resumed"], "a second browser was not told the same agents")
        held = dashboard.pending_input(room["id"])
        self.assertEqual([i["text"] for i in held["items"]], ["first", "second"])
        self.join()
        self.assertEqual(self.starts, 1, "two sends started two agents")
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["\x1b[200~first\n\nsecond\x1b[201~"])

    def test_two_browsers_at_once_start_one_agent(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()
        outs = []
        ts = [threading.Thread(target=lambda i=i: outs.append(h._resume_room(room, text=f"m{i}")))
              for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.join()
        self.assertEqual(self.starts, 1)
        [(pid, lines)] = self.typed().items()
        self.assertEqual(len(lines), 1)
        for i in range(4):
            self.assertIn(f"m{i}", lines[0])

    def test_a_plain_resume_types_the_note_and_holds_nothing(self):
        room = self.room()
        h = self.handler()
        out = h._resume_room(room)
        self.assertEqual(out["queued"], 0)
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, [dashboard.RESUME_NOTE])
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_running_room_is_not_launched_again(self):
        room = self.room()
        h = self.handler()
        h._resume_room(room)
        self.join()
        room = chatroom.get_room(room["id"], public=False)
        out = h._resume_room(room, text="more")
        self.assertEqual((out["delivered"], out["queued"], out["resumed"]), (1, 0, []))
        self.assertEqual(self.starts, 1)
        self.assertEqual(h._resume_room(room), {"resumed": [], "queued": 0, "delivered": 0})
        pid = room["participants"][0]["ptyId"]
        self.assertEqual(self.ptys[pid].typed, [dashboard.RESUME_NOTE, "more"])

    # ---- a team ----
    def test_a_rooms_message_is_posted_and_its_owner_rung_once(self):
        room = self.room(agents=("claude", "codex"), mode="pair")
        h = self.handler()
        with mock.patch.object(dashboard.Handler, "_start_review", lambda *a, **k: None):
            h._resume_room(room, text="team: carry on", to="")
            self.join()
        self.assertEqual(self.starts, 2)
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "team: carry on"])
        owner = next(p for pid, p in self.ptys.items() if "claude" in pid)
        self.assertEqual(len(owner.typed), 1, "the owner was woken more than once")
        self.assertTrue(owner.typed[0].startswith("\x1b[200~" + dashboard.RESUME_NOTE + "\n\n"))
        self.assertIn("[relay] New message from 'user'", owner.typed[0])
        self.assertNotIn("team: carry on", owner.typed[0], "a room's text is read with chat_read, not typed")
        partner = next(p for pid, p in self.ptys.items() if "codex" in pid)
        self.assertEqual(partner.typed, [], "a message to everyone wakes only the owner")

    # ---- failure keeps the message ----
    def test_a_refused_resume_keeps_the_message_for_a_retry(self):
        room = self.room()
        h = self.handler()
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("codex would not start")):
            with self.assertRaises(dashboard.StartRoomError):
                h._resume_room(room, text="kept?")
        held = dashboard.pending_input(room["id"])
        self.assertEqual(held["state"], "failed")
        self.assertEqual(held["error"], "codex would not start")
        self.assertEqual([i["text"] for i in held["items"]], ["kept?"])
        # Text sent afresh does not take the failed one along; Retry does.
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("still not")):
            with self.assertRaises(dashboard.StartRoomError):
                h._resume_room(room, text="another")
        self.assertEqual([i["text"] for i in dashboard.pending_input(room["id"])["items"]], ["another"])
        self.projects = [{"poRoomId": room["id"]}]
        out = h._resume_room(room, retry=True)
        self.assertEqual(out["queued"], 1)
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["another"])

    def test_a_refused_plain_resume_holds_nothing(self):
        room = self.room()
        h = self.handler()
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("no")):
            with self.assertRaises(dashboard.StartRoomError):
                h._resume_room(room)
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_an_agent_that_dies_before_it_settles_fails_the_message(self):
        room = self.room()
        h = self.handler(alive=False)
        h._resume_room(room, text="lost?")
        self.join()
        held = dashboard.pending_input(room["id"])
        self.assertEqual(held["state"], "failed")
        self.assertIn("stopped", held["error"])
        self.assertEqual([i["text"] for i in held["items"]], ["lost?"])
        self.assertEqual(self.typed(), {})
        self.assertTrue(dashboard.discard_pending(room["id"]))
        self.assertIsNone(dashboard.pending_input(room["id"]))
        self.assertFalse(dashboard.discard_pending(room["id"]))

    def test_a_prompt_on_screen_fails_the_message_with_the_reason(self):
        room = self.room()
        h = self.handler(tail="PROMPT")
        h._resume_room(room, text="answer me")
        self.join(timeout=15)
        held = dashboard.pending_input(room["id"])
        self.assertEqual(held["state"], "failed")
        self.assertIn("prompt", held["error"])
        self.assertEqual(self.typed(), {})

    # ---- the payload and the endpoint ----
    def test_the_room_payload_carries_what_is_held(self):
        room = self.room()
        h = self.handler()
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("no")):
            with self.assertRaises(dashboard.StartRoomError):
                h._resume_room(room, text="shown?")
        pub = dashboard._annotate_room_liveness(chatroom.get_room(room["id"]))
        self.assertEqual(pub["pending"]["state"], "failed")
        self.assertEqual(pub["pending"]["items"][0]["text"], "shown?")
        dashboard.discard_pending(room["id"])
        pub = dashboard._annotate_room_liveness(chatroom.get_room(room["id"]))
        self.assertNotIn("pending", pub)

    def post(self, path, body):
        raw = json.dumps(body).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
        h.requestline = f"POST {path} HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json", "Host": "127.0.0.1"}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    def test_the_endpoint_resumes_with_the_text_and_refuses_in_words(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        spawned = self.handler()
        with mock.patch.object(dashboard.Handler, "_resume_room_agent_pty", spawned._resume_room_agent_pty):
            status, out = self.post("/api/room/resume", {"roomId": room["id"], "text": "hi there"})
        self.assertEqual(status, 200)
        self.assertEqual(out["queued"], 1)
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["hi there"])
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("codex would not start")):
            self.ptys.clear()
            status, out = self.post("/api/room/resume", {"roomId": room["id"], "text": "again"})
        self.assertEqual((status, out["error"]), (400, "codex would not start"))
        self.assertEqual(dashboard.pending_input(room["id"])["state"], "failed")
        status, out = self.post("/api/room/resume", {"roomId": room["id"], "discard": True})
        self.assertEqual((status, out["discarded"]), (200, True))
        status, out = self.post("/api/room/resume", {"roomId": "room-nope", "text": "x"})
        self.assertEqual(status, 404)

    def test_the_same_key_is_taken_once(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        spawned = self.handler()
        body = {"roomId": room["id"], "text": "## Review comments (1)", "key": "review:k:1"}
        with mock.patch.object(dashboard.Handler, "_resume_room_agent_pty", spawned._resume_room_agent_pty):
            self.assertEqual(self.post("/api/room/resume", body)[1]["queued"], 1)
            self.assertTrue(self.post("/api/room/resume", body)[1].get("duplicate"))
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["## Review comments (1)"])


class ThePage(unittest.TestCase):
    def refresh(self):
        m = re.search(r"^async function refresh\(\) \{.*?^\}", SESSION, re.S | re.M)
        return m.group(0)

    def test_the_box_stays_usable_when_not_running(self):
        body = self.refresh()
        self.assertIn("$('#send').disabled = false;", body)
        self.assertIn("$('#input').disabled = false;", body)
        self.assertNotIn("$('#input').disabled = notRunning", body)
        self.assertIn("'Not running: sending will resume it.'", body)
        self.assertIn("'Resuming: your message goes in once it is up.'", body)
        self.assertIn("$('#resume').hidden = !notRunning || resuming;", body)

    def test_send_goes_the_resume_way_when_stopped(self):
        self.assertIn("async function sendResuming(text, to)", SESSION)
        self.assertIn("postOk('/api/room/resume', { roomId: ROOM, text, to: to || '' })", SESSION)
        send = SESSION[SESSION.index("$('#send').onclick = async () => {"):]
        send = send[:send.index("\n};\n")]
        self.assertIn("if (needsResume()) await sendResuming(t, '')", send)
        self.assertIn("if (needsResume()) await sendResuming(t, to)", send)
        self.assertIn("orResume(e, t, '')", send)

    def test_held_messages_show_as_pending_with_retry_on_failure(self):
        self.assertIn("ROOM_PENDING.state === 'failed'", SESSION)
        self.assertIn('class="pend-retry"', SESSION)
        self.assertIn('class="pend-discard"', SESSION)
        self.assertIn("{ roomId: ROOM, retry: true }", SESSION)
        self.assertIn("{ roomId: ROOM, discard: true }", SESSION)
        self.assertIn(".msg.pending.failed { opacity:1; border-left-color:var(--c-danger-bold); }", SESSION)


if __name__ == "__main__":
    unittest.main()
