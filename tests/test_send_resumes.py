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
import shutil
import subprocess
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
INDEX = (ROOT / "index.html").read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
FILEVIEW = (ROOT / "fileview.html").read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
NODE = shutil.which("node")


class FakePty:
    """A terminal that has drawn its screen and is quiet, unless told otherwise.
    ``dies_on_write``: its process ends the moment something is typed into
    it (it looked ready, but nothing arrived)."""

    def __init__(self, pty_id, alive=True, tail="> ", prompt=False):
        self.id = pty_id
        self._alive = alive
        self._tail = tail
        self.prompt = prompt
        self.dies_on_write = False
        self.last_output = 0.0
        self.last_input = 0.0
        self.typed = []

    def alive(self):
        return self._alive

    def tail(self, *a, **k):
        return self._tail

    def send_line(self, text):
        if self.dies_on_write:
            self._alive = False
            return False
        self.typed.append(text)
        return True


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
        self.alive: dict[str, bool] = {}

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
        gets a FakePty (settled, unless ``tail`` says a prompt is up; dead
        from the start when ``alive`` is False). ``self.alive[identity]``
        overrides ``alive`` for one agent, and can change between resumes."""
        test = self

        class H(dashboard.Handler):
            def __init__(self):
                pass

            def _resume_room_agent_pty(self, room_full, part, collab=True, seed="", human=False):
                test.starts += 1
                pid = f"pty-{part['identity']}-{test.starts}"
                up = test.alive.get(part["identity"], alive)
                test.ptys[pid] = FakePty(pid, alive=up, tail=tail)
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
        # Text sent afresh takes the failed one along, in order: only Discard
        # drops what a failed resume holds.
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("still not")):
            with self.assertRaises(dashboard.StartRoomError):
                h._resume_room(room, text="another")
        held = dashboard.pending_input(room["id"])
        self.assertEqual(([i["text"] for i in held["items"]], held["error"]),
                         (["kept?", "another"], "still not"))
        self.projects = [{"poRoomId": room["id"]}]
        out = h._resume_room(room)      # a plain Resume (the button, or Retry) sends them
        self.assertEqual(out["queued"], 2)
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["\x1b[200~kept?\n\nanother\x1b[201~"])
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_message_sent_as_the_resume_finishes_is_not_lost(self):
        """The deliver thread lets go of the lock the moment it has found
        nothing more to type: a message arriving right then must find no
        resume to queue onto (it goes straight into the running room), not a
        queue nobody drains any more."""
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()
        real = dashboard._RESUMES_LOCK
        test = self
        state = {"sent": False}

        class Lock:
            def __enter__(self):
                return real.__enter__()

            def __exit__(self, *a):
                out = real.__exit__(*a)
                if (threading.current_thread().name.startswith("resume-deliver-")
                        and not state["sent"] and test.typed()):
                    state["sent"] = True        # once: the first release after typing
                    h._resume_room(chatroom.get_room(room["id"], public=False), text="late")
                return out

        with mock.patch.object(dashboard, "_RESUMES_LOCK", Lock()):
            h._resume_room(room, text="first")
            self.join()
        self.assertTrue(state["sent"])
        self.assertEqual(self.starts, 1)
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["first", "late"], "the late message was lost or typed twice")
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_retry_on_a_prompt_waits_for_the_screen_to_clear(self):
        """A message that failed on a prompt is never typed straight into the
        running agent on Retry: it goes the settled way, and fails again while
        the prompt is still there."""
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler(tail="PROMPT")
        h._resume_room(room, text="answer me")
        self.join(timeout=15)
        self.assertIn("prompt", dashboard.pending_input(room["id"])["error"])
        room = chatroom.get_room(room["id"], public=False)
        out = h._resume_room(room)              # Retry, the prompt still up
        self.assertEqual((out["queued"], out["delivered"]), (1, 0))
        self.join(timeout=15)
        self.assertEqual(self.typed(), {}, "typed on top of the prompt")
        self.assertIn("prompt", dashboard.pending_input(room["id"])["error"])
        self.assertEqual(self.starts, 1, "a running agent was launched again")
        pty = self.ptys[room["participants"][0]["ptyId"]]
        pty._tail = "> "                        # the prompt is answered
        h._resume_room(room)
        self.join(timeout=15)
        self.assertEqual(pty.typed, ["answer me"])
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_named_partner_that_dies_keeps_its_message_and_comes_back_on_retry(self):
        room = self.room(agents=("claude", "codex"), mode="pair")
        self.alive["codex"] = False
        h = self.handler()
        h._resume_room(room, text="codex: look at this", to="codex")
        self.join()
        held = dashboard.pending_input(room["id"])
        self.assertEqual((held["state"], held["error"]),
                         ("failed", "codex stopped before the message could be typed"))
        self.assertEqual([i["text"] for i in held["items"]], ["codex: look at this"])
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec"], "posted to a room its recipient could not read")
        owner = next(p for pid, p in self.ptys.items() if "claude" in pid)
        self.assertEqual(owner.typed, [dashboard.RESUME_NOTE], "the owner was told of a message not for it")
        # Retry: the partner is brought back — alone, the owner is running —
        # and the message posted and relayed to it, once.
        self.alive["codex"] = True
        room = chatroom.get_room(room["id"], public=False)
        out = h._resume_room(room)
        self.assertEqual(sorted(r["identity"] for r in out["resumed"]), ["claude", "codex"])
        self.join()
        self.assertEqual(self.starts, 3, "the running owner was launched again")
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "codex: look at this"])
        partner = self.ptys[f"pty-codex-{self.starts}"]
        self.assertEqual(len(partner.typed), 1)
        self.assertIn("[relay] New message from 'user'", partner.typed[0])
        self.assertNotIn(dashboard.RESUME_NOTE, partner.typed[0], "a partner is not an owner")
        self.assertEqual(owner.typed, [dashboard.RESUME_NOTE], "the owner was woken for a message to codex")
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_team_that_stopped_since_the_check_is_resumed_and_the_message_posted_once(self):
        """The room read as running (a partner still is), but its owner had
        gone by the time the message reached it: the message is in the room
        once, the owner is brought back and rung for it."""
        room = self.room(agents=("claude", "codex"), mode="pair")
        h = self.handler()
        h._resume_room(room)
        self.join()
        room = chatroom.get_room(room["id"], public=False)
        self.ptys[room["participants"][0]["ptyId"]]._alive = False     # claude died
        out = h._resume_room(room, text="hey team")
        self.assertEqual((out["queued"], out["delivered"]), (1, 0))
        self.join()
        self.assertEqual(self.starts, 3, "the whole team was launched again")
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "hey team"], "posted twice, or not at all")
        owner = self.ptys[f"pty-claude-{self.starts}"]
        self.assertEqual(len(owner.typed), 1, "the owner was woken more than once")
        self.assertTrue(owner.typed[0].startswith("\x1b[200~" + dashboard.RESUME_NOTE + "\n\n"))
        self.assertIn("[relay] New message from 'user'", owner.typed[0])
        partner = self.ptys[room["participants"][1]["ptyId"]]
        self.assertEqual(partner.typed, [], "a message to everyone wakes only the owner")
        self.assertIsNone(dashboard.pending_input(room["id"]))

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

    # ---- gone between looking ready and the write ----
    def test_a_solo_agent_that_dies_as_the_input_is_typed_keeps_the_message(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()

        orig = dashboard.Handler._type_after_resume

        def dying(self_, rid, ready, notes, items, solo):
            for s in ready.values():
                s.dies_on_write = True      # ready a moment ago, gone at the write
            return orig(self_, rid, ready, notes, items, solo)

        with mock.patch.object(dashboard.Handler, "_type_after_resume", autospec=True, side_effect=dying):
            h._resume_room(room, text="did this arrive?")
            self.join()
        held = dashboard.pending_input(room["id"])
        self.assertEqual((held["state"], held["error"]),
                         ("failed", "the agent stopped before the message could be typed"))
        self.assertEqual([i["text"] for i in held["items"]], ["did this arrive?"])
        self.assertEqual(self.typed(), {})
        # Retry brings it back and types the message, once.
        h._resume_room(chatroom.get_room(room["id"], public=False))
        self.join()
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.ptys["pty-claude-2"].typed, ["did this arrive?"])
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_team_recipient_that_dies_as_it_is_woken_keeps_its_message_and_is_posted_once(self):
        room = self.room(agents=("claude", "codex"), mode="pair")
        h = self.handler()

        orig = dashboard.Handler._type_after_resume

        def dying(self_, rid, ready, notes, items, solo):
            if "codex" in ready:
                ready["codex"].dies_on_write = True
            return orig(self_, rid, ready, notes, items, solo)

        with mock.patch.object(dashboard.Handler, "_type_after_resume", autospec=True, side_effect=dying):
            h._resume_room(room, text="codex: a question", to="codex")
            self.join()
        held = dashboard.pending_input(room["id"])
        self.assertEqual((held["state"], held["error"]),
                         ("failed", "codex stopped before the message could be typed"))
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "codex: a question"], "posted once before the wake failed")
        owner = self.ptys["pty-claude-1"]
        self.assertEqual(owner.typed, [dashboard.RESUME_NOTE])
        # Retry: codex comes back alone, is rung once, and the message is not posted again.
        h._resume_room(chatroom.get_room(room["id"], public=False))
        self.join()
        self.assertEqual(self.starts, 3)
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "codex: a question"], "posted a second time on retry")
        partner = self.ptys["pty-codex-3"]
        self.assertEqual(len(partner.typed), 1)
        self.assertIn("[relay] New message from 'user'", partner.typed[0])
        self.assertEqual(owner.typed, [dashboard.RESUME_NOTE], "the owner was woken for codex's message")
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_running_owner_that_dies_as_it_is_rung_is_resumed_for_the_message(self):
        """The live path: the ring's write is what finds the owner gone."""
        room = self.room(agents=("claude", "codex"), mode="pair")
        h = self.handler()
        h._resume_room(room)
        self.join()
        room = chatroom.get_room(room["id"], public=False)
        self.ptys["pty-claude-1"].dies_on_write = True
        out = h._resume_room(room, text="hey team")
        self.assertEqual((out["queued"], out["delivered"]), (1, 0))
        self.join()
        self.assertEqual(self.starts, 3)
        texts = [m["text"] for m in chatroom.read_messages(room["id"]) if m.get("from") == chatroom.HUMAN_IDENTITY]
        self.assertEqual(texts, ["spec", "hey team"])
        owner = self.ptys["pty-claude-3"]
        self.assertEqual(len(owner.typed), 1)
        self.assertIn("[relay] New message from 'user'", owner.typed[0])
        self.assertEqual(self.ptys["pty-codex-2"].typed, [])
        self.assertIsNone(dashboard.pending_input(room["id"]))

    def test_a_running_solo_agent_that_dies_at_the_write_is_resumed_for_the_message(self):
        """The live solo path: the direct write is what finds the agent gone."""
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        h = self.handler()
        h._resume_room(room)
        self.join()
        room = chatroom.get_room(room["id"], public=False)
        self.ptys["pty-claude-1"].dies_on_write = True
        out = h._resume_room(room, text="is it there?")
        self.assertEqual((out["queued"], out["delivered"]), (1, 0))
        self.join()
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.typed(), {"pty-claude-2": ["is it there?"]})
        self.assertIsNone(dashboard.pending_input(room["id"]))

    # ---- a link to a balloon ----
    def test_a_balloon_link_is_written_out_in_the_terminal_not_in_the_room(self):
        other = self.room()
        url = f"http://hub-host:8765/session?room={other['id']}&msg=m0"
        block = f"[ref {url}] from sam in \"t\" at "
        room = self.room()
        h = self.handler()
        with mock.patch.object(dashboard, "operator_name", lambda: "sam"):
            # Stopped: typed in after the resume.
            h._resume_room(room, text=f"read **{url}**")
            self.join()
            [first] = self.typed()["pty-claude-1"]
            self.assertIn(f"read **{url}**\n\n{block}", first)
            self.assertTrue(first.rstrip("\x1b[201~").endswith("\n> spec"), first)
            # Running: typed straight in.
            room = chatroom.get_room(room["id"], public=False)
            h._resume_room(room, text=f"and {url}")
            self.assertIn(f"and {url}\n\n{block}", self.ptys["pty-claude-1"].typed[-1])
            # A team: the room keeps the words, chat_read writes the link out.
            team = self.room(agents=("claude", "codex"), mode="collab")
            h._resume_room(team, text=f"- see {url}")
            self.join()
            said = [m["text"] for m in chatroom.read_messages(team["id"]) if m.get("from") == "user"]
            self.assertEqual(said[-1], f"- see {url}")
            got = h._mcp_tool_call("chat_read", {}, team["id"], "claude",
                                   lambda r: r, lambda code, msg: {"error": msg})
            text = got["content"][0]["text"]
            self.assertIn(f"[from user] - see {url}\n\n{block}", text)
            self.assertIn("\n> spec", text)

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
        self.assertEqual(lines, ["\x1b[200~hi there\n\n[point P1]\x1b[201~"])
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
        self.assertEqual(lines, ["\x1b[200~## Review comments (1)\n\n[point P1]\x1b[201~"])

    def test_the_same_key_after_a_refusal_is_a_retry_not_a_second_message(self):
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        body = {"roomId": room["id"], "text": "once please", "key": "send:k:1"}
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("refused")):
            status, out = self.post("/api/room/resume", body)
            self.assertEqual((status, out["error"], out["kept"]), (400, "refused", True))
            status, out = self.post("/api/room/resume", body)        # the page sends it again
            self.assertEqual((status, out["kept"]), (400, True))
        self.assertEqual([i["text"] for i in dashboard.pending_input(room["id"])["items"]],
                         ["once please\n\n[point P1]"], "the same send was queued twice")
        spawned = self.handler()
        with mock.patch.object(dashboard.Handler, "_resume_room_agent_pty", spawned._resume_room_agent_pty):
            status, out = self.post("/api/room/resume", body)        # and again: it starts now
        self.assertEqual((status, out["queued"]), (200, 1))
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(lines, ["\x1b[200~once please\n\n[point P1]\x1b[201~"])
        # A plain refusal with nothing held says so: the page keeps the text.
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                               side_effect=dashboard.StartRoomError("refused")):
            self.ptys.clear()
            status, out = self.post("/api/room/resume", {"roomId": room["id"]})
        self.assertEqual((status, out["kept"]), (400, False))

    def test_the_same_key_after_a_resume_that_failed_later_is_a_retry_not_a_duplicate(self):
        """The request was accepted (its reply lost on the way back) and the
        resumed agent then died before the message went in: the page's same
        POST starts it again, once, instead of being waved off as a duplicate."""
        room = self.room()
        self.projects = [{"poRoomId": room["id"]}]
        body = {"roomId": room["id"], "text": "still coming?", "key": "send:k:2"}
        self.alive["claude"] = False
        spawned = self.handler()
        with mock.patch.object(dashboard.Handler, "_resume_room_agent_pty", spawned._resume_room_agent_pty):
            status, out = self.post("/api/room/resume", body)
            self.assertEqual((status, out["queued"]), (200, 1))
            self.join()
            self.assertEqual(dashboard.pending_input(room["id"])["state"], "failed")
            self.alive["claude"] = True
            status, out = self.post("/api/room/resume", body)        # the page sends it again
            self.assertEqual(status, 200)
            self.assertFalse(out.get("duplicate"), "the retry was waved off as a duplicate")
            self.join()
            self.assertEqual(self.starts, 2)
            self.assertEqual(self.typed(), {"pty-claude-2": ["\x1b[200~still coming?\n\n[point P1]\x1b[201~"]})
            self.assertIsNone(dashboard.pending_input(room["id"]))
            # Delivered, the same key is a duplicate again: nothing goes in twice.
            self.assertTrue(self.post("/api/room/resume", body)[1].get("duplicate"))
        self.assertEqual(self.typed(), {"pty-claude-2": ["\x1b[200~still coming?\n\n[point P1]\x1b[201~"]})


class ThePage(unittest.TestCase):
    def refresh(self):
        m = re.search(r"^async function refreshRoom\(\) \{.*?^\}", SESSION, re.S | re.M)
        return m.group(0)

    def test_the_box_stays_usable_when_not_running(self):
        body = self.refresh()
        self.assertIn("$('#send').disabled = false;", body)
        self.assertIn("$('#input').disabled = false;", body)
        self.assertNotIn("$('#input').disabled = notRunning", body)
        self.assertIn("'Not running: sending will resume it.'", body)
        self.assertIn("'Resuming: your message goes in once it is up.'", body)
        # Resume is the header's primary action (static/actions.js), off while resuming.
        self.assertIn("ROOM_RESUMING = resuming;", body)
        self.assertIn("renderActions();", body)
        self.assertIn("resuming: ROOM_RESUMING", SESSION)

    def test_send_goes_the_resume_way_when_stopped(self):
        self.assertIn("async function sendResuming(text, to, key, attachments)", SESSION)
        self.assertIn("const body = { roomId: ROOM, text, to: to || '', key: key || '' };", SESSION)
        self.assertIn("return postOk('/api/room/resume', body);", SESSION)
        send = SESSION[SESSION.index("$('#send').onclick = async () => {"):]
        send = send[:send.index("\n};\n")]
        # A one-agent chat's too: the hub keeps the person's points (points.py)
        # and types it in, or resumes the session for it.
        self.assertIn("try { d = await sendResuming(t, '', key, atts); }", send)
        self.assertNotIn("/api/pty/input", send)
        # A team's every send goes through the hub's resume-or-deliver: the
        # hub, not the last poll, knows whether the team is still running.
        self.assertIn("await sendResuming(t, to, msgKey(), atts)", send)
        self.assertNotIn("/api/room/say", send)
        submit = SESSION[SESSION.index("async function submitComments() {"):]
        submit = submit[:submit.index("\n}\n")]
        self.assertIn("await sendResuming(body, SOLO_MODE ? '' : to, key, images);", submit)
        self.assertNotIn("/api/room/say", submit)
        # Refused but held by the hub: the box is cleared (the message shows
        # in the chat as not delivered, with Retry); the tray lets the batch go.
        self.assertIn("kept: !!(d && d.kept)", SESSION)
        self.assertEqual(send.count("if (keptByHub(e)) return;"), 2)
        self.assertIn("if (e && e.kept) sent = true;", submit)

    def test_a_send_is_keyed_once_per_message(self):
        # The same key goes with every attempt to send the message in the box
        # (a lost reply, sent again, is taken once), a new one once it is
        # edited or sent.
        self.assertIn("function msgKey() {", SESSION)
        self.assertIn("if (!SEND_KEY) SEND_KEY = 'send:'", SESSION)
        self.assertIn("SEND_KEY = '';             // edited, so it is a new message", SESSION)
        self.assertEqual(SESSION.count("SEND_ERR = ''; SEND_KEY = '';"), 2, "a sent message keeps its key")
        self.assertIn("const key = `cmt:${batch.length}:${batch[0].cid}:${batch[batch.length - 1].cid}`;", SESSION)

    def test_no_function_is_declared_twice(self):
        # Declarations hoist: a second `function sendKey` silently replaced
        # the prompt card's keystroke sender, and no prompt could be answered.
        for i, script in enumerate(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", SESSION, re.S)):
            names = re.findall(r"^(?:async )?function (\w+)\(", script, re.M)
            dup = sorted({n for n in names if names.count(n) > 1})
            self.assertEqual(dup, [], f"declared twice in inline script {i}")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_prompt_card_still_sends_keystrokes(self):
        # Executable: with the message-key helper loaded alongside, the prompt
        # card's sendKey still posts the keystroke to the terminal.
        def one_liner(name):
            m = re.search(rf"^(?:const {name} = |function {name}\().*$", SESSION, re.M)
            self.assertTrue(m, name)
            return m.group(0)

        def block(name):
            m = re.search(rf"^(?:async )?function {name}\(", SESSION, re.M)
            self.assertTrue(m, name)
            return SESSION[m.start():SESSION.index("\n}\n", m.start()) + 3]

        code = "\n".join([one_liner("PC_KEYS"), one_liner("sendKey"), block("moveSelection"), block("msgKey")])
        js = r"""
const vm = require('vm');
const code = require('fs').readFileSync(0, 'utf8');
const posts = [];
const ctx = { jpost: (u, b) => { posts.push([u, b]); return Promise.resolve({}); }, SOLO_PTY: 'pty-9', PC_SEL: 0, SEND_KEY: '' };
vm.createContext(ctx);
vm.runInContext(code + "\nmoveSelection(2); sendKey(PC_KEYS.enter); globalThis.key1 = msgKey(); globalThis.key2 = msgKey();", ctx);
console.log(JSON.stringify({ posts, same: ctx.key1 === ctx.key2 && !!ctx.key1 }));
"""
        out = subprocess.run([NODE, "-e", js], input=code, capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r["posts"], [["/api/pty/input", {"id": "pty-9", "data": "\x1b[B\x1b[B"}],
                                      ["/api/pty/input", {"id": "pty-9", "data": "\r"}]])
        self.assertTrue(r["same"])

    def test_the_task_panel_and_the_comment_trays_reach_a_stopped_task(self):
        # The task panel embeds a room's chat whether or not it is running (the
        # chat's box brings it back); the Changes-tab and Workspace comment
        # trays send through the keyed resume-or-deliver, never /api/room/say.
        self.assertIn("const liveEmbed = isRoom && !isOrphan;", INDEX)
        dr = INDEX[INDEX.index("function drSubmitState(rv) {"):]
        dr = dr[:dr.index("\n}\n")]
        self.assertIn("is not running: submitting will resume it and deliver them.", dr)
        self.assertNotIn("can: false, label: 'Submit', note: `${t.name} is not running", dr)
        submit = INDEX[INDEX.index("async function drSubmit(rv) {"):]
        submit = submit[:submit.index("\n}\n")]
        self.assertIn("fetch('/api/room/resume'", submit)
        self.assertNotIn("fetch('/api/room/say'", submit)
        self.assertIn("key: drKey(batch)", submit)
        self.assertIn("if (!(j && j.kept)) throw", submit)
        fv = FILEVIEW[FILEVIEW.index("async function submitCmts() {"):]
        fv = fv[:fv.index("\n}\n")]
        self.assertIn("fetch('/api/room/resume'", fv)
        self.assertNotIn("fetch('/api/room/say'", fv)
        self.assertIn("const key = 'fv:' + FV_MARK + ':' + CMTS.map(c => c.cid).join(',');", fv)
        self.assertIn("showFallback('Couldn’t send to the chat. Copy your comments:', body)", fv)

    def test_retry_and_discard_are_a_fingers_size_on_a_phone(self):
        # The compact rule is more specific than the phone's `button` rule, so
        # the phone block names them itself (SKILL.md §8), on the 4px grid.
        self.assertIn(".msg .pend-state button { min-height:24px; padding:0 var(--s-200); font-size:var(--fs-200); }", SESSION)
        coarse = SESSION[SESSION.index("@media (pointer: coarse) {"):]
        coarse = coarse[:coarse.index("\n  }\n")]
        self.assertIn(".msg .pend-state button { min-height: var(--touch-min); }", coarse)
        self.assertIn(".msg .pend-state { margin-top:var(--s-100);", SESSION)
        self.assertIn("gap:var(--s-200); }", SESSION[SESSION.index(".msg .pend-state {"):][:300])

    def test_held_messages_show_as_pending_with_retry_on_failure(self):
        self.assertIn("ROOM_PENDING.state === 'failed'", SESSION)
        self.assertIn('class="pend-retry"', SESSION)
        self.assertIn('class="pend-discard"', SESSION)
        self.assertIn("{ roomId: ROOM, retry: true }", SESSION)
        self.assertIn("{ roomId: ROOM, discard: true }", SESSION)
        self.assertIn(".msg.pending.failed { opacity:1; border-left-color:var(--c-danger-bold); }", SESSION)


if __name__ == "__main__":
    unittest.main()
