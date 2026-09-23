"""A person's send stays in the chat until the agent's conversation shows it.

The hub keeps every send by its key (sends.py) and the page draws what the
hub keeps (session.html, ``sendsToShow``). These check the contract:

* P11 (2026-09-23): a long message sent to a busy Codex chat vanished when the
  next row arrived, or after two minutes, and came back minutes later when
  Codex read it. Now it is kept, through other rows and any time, until the
  turn that holds it is in the transcript; then exactly one balloon, the
  transcript's, is drawn;
* every copy of the chat, and a reload, gets the same sends from the hub;
* the same key again (a lost reply, a Retry) delivers once;
* #82: a send to a stopped, Done task answered 200 and did nothing (no
  running agent, nothing held, nothing delivered). A 200 now always names a
  send that is delivered or held by a resume under way; anything else is a
  failure the chat keeps, with Retry and Discard, across a reload and a hub
  restart;
* a ``/command`` is done once typed; a team's send is confirmed once posted;
  a send the agent stopped without reading fails.

Skipped parts need Node.
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
import points
import sends

ROOT = Path(__file__).resolve().parent.parent
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")
SID = "019a0000-0000-7000-8000-000000000001"


class FakePty:
    def __init__(self, pty_id, alive=True):
        self.id = pty_id
        self._alive = alive
        self.last_output = 0.0
        self.last_input = 0.0
        self.typed: list[str] = []

    def alive(self):
        return self._alive

    def tail(self, *a, **k):
        return "> "

    def send_line(self, text):
        if not self._alive:
            return False
        self.typed.append(text)
        return True


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"


class Receipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(self.temp.name) / "rooms"
        dashboard._RESUMES.clear()
        dashboard._SAY_KEYS.clear()
        sends._SYNCED.clear()
        points._CACHE.clear()
        points._SYNCED.clear()
        points._SCANNED.clear()
        self.ptys: dict[str, FakePty] = {}
        self.turns: dict[str, list[dict]] = {}
        self.starts = 0
        self.come_up = True
        test = self
        self.patches = [
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: self.ptys.get(pid)),
            mock.patch.object(dashboard.ptyrun, "list_sessions", lambda: []),
            mock.patch.object(dashboard.rotation, "IDLE_S", 0),
            mock.patch.object(dashboard.attention, "looks_like_prompt", lambda tail: False),
            mock.patch.object(dashboard, "load_projects", lambda: []),
            mock.patch.object(dashboard, "RESUME_NOTE_WAIT_S", 2),
            mock.patch.object(dashboard, "read_session_turns", lambda sid: self.turns.get(sid)),
            mock.patch.object(dashboard, "find_transcript", lambda sid: None),
            mock.patch.object(dashboard.Handler, "_resume_room_agent_pty",
                              lambda h, room_full, part, **k: test.spawn(part)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.join()
        dashboard._RESUMES.clear()
        dashboard._SAY_KEYS.clear()
        chatroom.ROOMS_DIR = self.old_dir
        self.temp.cleanup()

    def spawn(self, part):
        self.starts += 1
        pid = f"pty-{part['identity']}-{self.starts}"
        self.ptys[pid] = FakePty(pid, alive=self.come_up)
        return {"ptyId": pid, "cwd": "", "sessionId": part.get("sessionId", ""), "prompted": False}

    def join(self, timeout=8):
        for t in threading.enumerate():
            if t.name.startswith("resume-deliver-"):
                t.join(timeout)

    # ---- helpers ----
    def room(self, running=True, agents=("codex",), mode="solo", workflow="inprogress"):
        parts = [{"identity": a, "agent": a, "model": "", "role": "engineer" if i == 0 else "reviewer"}
                 for i, a in enumerate(agents)]
        r = chatroom.create_room("t", parts)
        full = chatroom.get_room(r["id"], public=False)
        full["mode"] = mode
        full["launched"] = True
        full["workflow"] = workflow
        full["messages"] = [{"id": "m0", "from": "user", "text": "spec", "to": "", "ts": time.time() - 900}]
        for i, part in enumerate(p for p in full["participants"] if p.get("kind") == "agent"):
            part["sessionId"] = SID if i == 0 else f"{SID[:-1]}{i + 1}"
            part["ptyId"] = f"pty-live-{i}"
            self.ptys[part["ptyId"]] = FakePty(part["ptyId"], alive=running)
        chatroom.update_room(full)
        t0 = time.time() - 600
        self.turns[SID] = [
            {"role": "user", "text": "spec", "timestamp": iso(t0)},
            {"role": "assistant", "text": "Working on it.", "timestamp": iso(t0 + 5)},
        ]
        return full["id"]

    def post(self, body):
        raw = json.dumps(body).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/room/resume", "POST", "HTTP/1.1"
        h.requestline = "POST /api/room/resume HTTP/1.1"
        h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json", "Host": "127.0.0.1"}
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        h.server = mock.Mock(server_address=("127.0.0.1", 8765))
        h.do_POST()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload)

    def payload(self, rid, now=None):
        """The room as a page's poll gets it."""
        sends._SYNCED.clear()
        if now is None:
            return dashboard._annotate_room_liveness(chatroom.get_room(rid), with_points=True)
        with mock.patch.object(sends.time, "time", lambda: now):
            return dashboard._annotate_room_liveness(chatroom.get_room(rid), with_points=True)

    def typed(self):
        return {pid: p.typed for pid, p in self.ptys.items() if p.typed}

    def assert_not_silent(self, rid, status, out):
        """#82's rule: a 200 names a send delivered, or held by a resume
        under way; a failure is kept (``kept``) and shown."""
        pub = self.payload(rid)
        mine = [s for s in pub.get("sends", []) if s["key"] == out.get("send", {}).get("key")]
        if status == 200:
            self.assertTrue(out.get("send"), "a 200 without a receipt")
            self.assertIn(out["send"]["state"], ("queued", "delivered", "confirmed"))
            # Once the resume is done, it is delivered, or failed and shown.
            now = mine[0]["state"] if mine else "confirmed"
            self.assertTrue(now in ("delivered", "confirmed") or now == "failed" and mine[0].get("error"),
                            f"200, then {now}, live={pub['live']}, pending={pub.get('pending')}")
        else:
            self.assertTrue(out.get("kept"))
            self.assertEqual(mine[0]["state"], "failed")

    # ---- P11: a busy solo agent reads it minutes later ----
    def test_p11_a_send_stays_through_other_rows_and_time_until_its_turn(self):
        rid = self.room()
        status, out = self.post({"roomId": rid, "text": "A long message about P11", "key": "send:p11"})
        self.assertEqual(status, 200)
        self.assertEqual(out["send"]["state"], "delivered")
        self.assertEqual(out["send"]["key"], "send:p11")
        self.assertEqual(len(self.typed()["pty-live-0"]), 1)
        sent_at = out["send"]["at"]
        # The agent keeps working: more turns, a hub row, and three minutes.
        self.turns[SID] += [{"role": "assistant", "text": f"step {i}", "timestamp": iso(sent_at + 10 + i)}
                            for i in range(5)]
        chatroom.post_notice(rid, "hub", "The hub was restarted.", {})
        for later in (sent_at + 30, sent_at + 121, sent_at + 400):
            pub = self.payload(rid, now=later)
            [s] = pub["sends"]
            self.assertEqual((s["key"], s["state"]), ("send:p11", "delivered"), f"gone at +{later - sent_at:.0f}s")
        # Codex takes it: the turn (as it was typed, point line and all).
        typed = self.typed()["pty-live-0"][0]
        self.turns[SID].append({"role": "user", "text": typed, "timestamp": iso(sent_at + 420), "queued": True})
        pub = self.payload(rid, now=sent_at + 421)
        [s] = pub["sends"]
        self.assertEqual(s["state"], "confirmed")
        mid = dict((t["text"], m) for m, t in dashboard.page_turn_ids(SID, self.turns[SID]))[typed]
        self.assertEqual(s["mid"], mid)
        # Kept on the list a moment (until the page's copy has the turn), then not.
        self.assertEqual(self.payload(rid, now=sent_at + 421 + sends.SHOW_CONFIRMED_S + 5).get("sends"), [])
        # One point, never two.
        self.assertEqual([p["id"] for p in points.load(rid)["points"]], ["P1"])

    def test_every_copy_and_a_reload_get_the_same_sends(self):
        rid = self.room()
        self.post({"roomId": rid, "text": "visible everywhere", "key": "send:v"})
        a, b = self.payload(rid), self.payload(rid)
        self.assertEqual(a["sends"], b["sends"])
        self.assertEqual([s["key"] for s in a["sends"]], ["send:v"])
        # The hub's memory is not where it lives: a fresh read of the file.
        self.assertEqual([s["key"] for s in sends._load(rid)], ["send:v"])

    # ---- exactly once ----
    def test_a_lost_reply_sent_again_delivers_once(self):
        rid = self.room()
        body = {"roomId": rid, "text": "only once", "key": "send:once"}
        s1, o1 = self.post(body)
        s2, o2 = self.post(body)
        self.assertEqual((s1, s2), (200, 200))
        self.assertTrue(o2.get("duplicate"))
        self.assertEqual(o2["send"]["key"], "send:once")
        self.assertEqual(len(self.typed()["pty-live-0"]), 1)
        self.assertEqual(len(points.load(rid)["points"]), 1)
        self.assertEqual(len(self.payload(rid)["sends"]), 1)

    def test_a_send_without_a_key_gets_one(self):
        rid = self.room()
        status, out = self.post({"roomId": rid, "text": "no key given"})
        self.assertEqual(status, 200)
        self.assertTrue(out["send"]["key"].startswith("hub:"))

    # ---- #82: a stopped or Done task ----
    def test_82_a_send_to_a_done_task_that_starts_nothing_fails_visibly(self):
        rid = self.room(running=False, workflow="done")
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room", lambda h, room, **k: []):
            status, out = self.post({"roomId": rid, "text": "go ahead with the changes", "key": "send:82"})
            self.join()
        pub = self.payload(rid)
        self.assertFalse(pub["live"])
        [s] = pub["sends"]
        self.assertEqual((s["key"], s["state"]), ("send:82", "failed"))
        self.assertTrue(s["error"])
        if status == 200:       # it was queued on a resume, which then failed
            self.assertEqual(out["send"]["state"], "queued")
        else:
            self.assertEqual((status, out["kept"]), (400, True))
        # Retry, once it can start: delivered, once.
        status, out = self.post({"roomId": rid, "retry": True, "key": "send:82"})
        self.assertEqual(status, 200)
        self.join()
        self.assertEqual(self.payload(rid)["sends"][0]["state"], "delivered")
        [(pid, lines)] = self.typed().items()
        self.assertEqual(len(lines), 1)
        self.assertIn("go ahead with the changes", lines[0])

    def test_82_a_send_to_a_done_task_is_never_a_silent_ok(self):
        # Every way the start can go: it comes up, it dies at once, it is refused.
        for case in ("up", "dies", "refused"):
            with self.subTest(case=case):
                rid = self.room(running=False, workflow="done")
                self.come_up = case != "dies"
                ctx = (mock.patch.object(dashboard.Handler, "_start_or_resume_room",
                                         side_effect=dashboard.StartRoomError("codex would not start"))
                       if case == "refused" else mock.MagicMock())
                with ctx:
                    status, out = self.post({"roomId": rid, "text": f"go ahead ({case})", "key": f"send:{case}"})
                self.join()
                self.assert_not_silent(rid, status, out)
                [s] = self.payload(rid)["sends"]
                self.assertEqual(s["state"], "delivered" if case == "up" else "failed")
        self.come_up = True

    def test_82_the_same_key_the_hub_has_seen_is_never_a_silent_ok(self):
        # The duplicate answer carries the send and where it is.
        rid = self.room(running=False, workflow="done")
        body = {"roomId": rid, "text": "go ahead", "key": "send:seen"}
        self.assertEqual(self.post(body)[0], 200)
        self.join()
        status, out = self.post(body)
        self.assertEqual(status, 200)
        self.assertTrue(out["duplicate"])
        self.assertEqual(out["send"]["state"], "delivered")

    def test_a_send_queued_by_a_hub_that_stopped_fails_and_its_retry_delivers(self):
        rid = self.room(running=False)
        # Held by the resume of a hub that has since restarted: its queue is gone.
        with mock.patch.object(dashboard.Handler, "_start_or_resume_room", lambda h, room, **k: []), \
                mock.patch.object(dashboard.Handler, "_deliver_after_resume", lambda *a, **k: None):
            dashboard._RESUMES[rid] = dashboard._Resume(rid)
            sends.accept(rid, "send:boot", "before the restart\n\n[point P1]")
        dashboard._RESUMES.clear()
        with mock.patch.object(sends, "BOOT", "another-hub"):
            [s] = self.payload(rid)["sends"]
        self.assertEqual(s["state"], "failed")
        self.assertIn("restarted", s["error"])
        # And it stays failed across reloads, with its reason.
        [s] = self.payload(rid)["sends"]
        self.assertEqual(s["state"], "failed")
        status, out = self.post({"roomId": rid, "retry": True, "key": "send:boot"})
        self.assertEqual(status, 200)
        self.join()
        [(pid, lines)] = self.typed().items()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("before the restart\n\n[point P1]\x1b[201~"), lines)

    def test_discard_drops_one_failed_send_and_its_point(self):
        rid = self.room(running=False)
        self.come_up = False
        self.post({"roomId": rid, "text": "first", "key": "send:1"})
        self.join()
        self.post({"roomId": rid, "text": "second", "key": "send:2"})
        self.join()
        self.assertEqual([s["state"] for s in self.payload(rid)["sends"]], ["failed", "failed"])
        self.assertEqual([p["id"] for p in points.load(rid)["points"]], ["P1", "P2"])
        status, out = self.post({"roomId": rid, "discard": True, "key": "send:1"})
        self.assertEqual((status, out["discarded"]), (200, True))
        self.assertEqual([s["key"] for s in self.payload(rid)["sends"]], ["send:2"])
        self.assertEqual([p["id"] for p in points.load(rid)["points"]], ["P2"])
        self.come_up = True

    # ---- other ways a send ends ----
    def test_a_command_is_done_once_typed(self):
        rid = self.room()
        status, out = self.post({"roomId": rid, "text": "/compact", "key": "send:cmd"})
        self.assertEqual(out["send"]["state"], "confirmed")
        self.assertEqual(self.payload(rid)["sends"], [])

    def test_a_teams_send_is_confirmed_by_its_message(self):
        rid = self.room(agents=("claude", "codex"), mode="collab")
        with mock.patch.object(dashboard.Handler, "_ring_recipients", lambda h, rid, result: []):
            status, out = self.post({"roomId": rid, "text": "hey team", "key": "send:team"})
        self.assertEqual(out["send"]["state"], "confirmed")
        msg = chatroom.get_room(rid)["messages"][-1]
        self.assertEqual(out["send"]["mid"], msg["id"])

    def test_a_send_the_agent_stopped_without_reading_fails(self):
        rid = self.room()
        status, out = self.post({"roomId": rid, "text": "read me", "key": "send:gone"})
        self.assertEqual(out["send"]["state"], "delivered")
        self.ptys["pty-live-0"]._alive = False
        at = out["send"]["at"]
        self.assertEqual(self.payload(rid, now=at + 5)["sends"][0]["state"], "delivered")
        [s] = self.payload(rid, now=at + sends.STOPPED_AFTER_S + 5)["sends"]
        self.assertEqual(s["state"], "failed")
        self.assertIn("stopped before it read", s["error"])

    def test_the_same_words_again_need_their_own_turn(self):
        rid = self.room()
        now = time.time()
        for k in ("send:ok1", "send:ok2"):
            sends.accept(rid, k, "sounds good, carry on", now=now)
            sends.mark(rid, [k], "delivered", now=now)
        self.turns[SID].append({"role": "user", "text": "sounds good, carry on", "timestamp": iso(now + 1)})
        states = {s["key"]: s["state"] for s in self.payload(rid)["sends"]}
        self.assertEqual(sorted(states.values()), ["confirmed", "delivered"])

    def test_one_resume_input_confirms_every_send_it_carries(self):
        # A resume types its note and every send it held as one input: one
        # transcript turn then holds them all (review 1, finding 1).
        rid = self.room()
        now = time.time()
        held = {"send:p1": "first\n\n[point P1]", "send:p2": "second\n\n[point P2]",
                "send:w": "and a plain line"}
        for k, t in held.items():
            sends.accept(rid, k, t, now=now)
            sends.mark(rid, [k], "delivered", now=now)
        turn = "[resumed] carry on\n\n" + "\n\n".join(held.values())
        self.turns[SID].append({"role": "user", "text": turn, "timestamp": iso(now + 1)})
        pub = self.payload(rid)["sends"]
        self.assertEqual({s["key"]: s["state"] for s in pub}, dict.fromkeys(held, "confirmed"))
        self.assertEqual(len({s["mid"] for s in pub}), 1)

    def test_a_retry_straight_after_a_restart_delivers_it(self):
        # Queued by a hub that has since restarted, and sent again before any
        # poll of the room: a retry, not a duplicate that says "queued"
        # (review 1, finding 2).
        rid = self.room()
        with mock.patch.object(sends, "BOOT", "the-hub-before"):
            sends.accept(rid, "send:early", "sent before the restart")
        status, out = self.post({"roomId": rid, "text": "sent before the restart", "key": "send:early"})
        self.assertEqual(status, 200)
        self.assertFalse(out.get("duplicate"))
        self.join()
        self.assertEqual(out["send"]["state"], "delivered")
        [(pid, lines)] = self.typed().items()
        self.assertEqual(len(lines), 1)
        self.assertIn("sent before the restart", lines[0])

    def test_an_image_alone_is_confirmed_by_its_turn(self):
        # No words and no point: its image's path is what to look for, in
        # Claude's turn too (review 1, finding 3).
        self.assertTrue(sends.matches("[image] C:/a.png", "[Image #1]\n[image] C:/a.png"))
        self.assertTrue(sends.matches("[image] C:\\x\\A.png", "[image] c:/x/a.png"))
        self.assertFalse(sends.matches("[image] C:/a.png", "[Image #1]\n[image] C:/b.png"))
        self.assertFalse(sends.matches("[image] C:/a.png", "[Image #1]"))
        rid = self.room(agents=("claude",))
        now = time.time()
        sends.accept(rid, "send:img", "[image] C:\\att\\shot.png", now=now)
        sends.mark(rid, ["send:img"], "delivered", now=now)
        self.turns[SID].append({"role": "user", "text": "[Image #1]\n[image] C:\\att\\shot.png",
                                "timestamp": iso(now + 1)})
        [s] = self.payload(rid)["sends"]
        self.assertEqual(s["state"], "confirmed")

    def overlapping(self, texts, turn):
        """The states of delivered sends ``texts`` after one turn ``turn``."""
        rid = self.room()
        now = time.time()
        for i, t in enumerate(texts):
            sends.accept(rid, f"send:{i}", t, now=now + i * 0.01)
            sends.mark(rid, [f"send:{i}"], "delivered", now=now)
        self.turns[SID].append({"role": "user", "text": turn, "timestamp": iso(now + 1)})
        return [s["state"] for s in sorted(self.payload(rid)["sends"], key=lambda s: s["key"])]

    def test_one_part_of_a_turn_confirms_one_send(self):
        # Review 2: a shorter send, or a subset of images, cannot ride on the
        # part of the turn a longer one is confirmed by.
        self.assertEqual(self.overlapping(["go", "go now"], "go now"), ["delivered", "confirmed"])
        self.assertEqual(self.overlapping(["go now", "go"], "go now"), ["confirmed", "delivered"])
        self.assertEqual(self.overlapping(["go", "go now"], "go\n\ngo now"), ["confirmed", "confirmed"])
        self.assertEqual(self.overlapping(["[image] a.png", "[image] a.png\n[image] b.png"],
                                          "[image] a.png\n[image] b.png"), ["delivered", "confirmed"])
        self.assertEqual(self.overlapping(["[image] a.png\n[image] a.png"], "[image] a.png"), ["delivered"])
        self.assertEqual(self.overlapping(["[image] a.png\n[image] b.png"] * 2,
                                          "[image] a.png\n[image] b.png"), ["confirmed", "delivered"])
        # Words inside the resume note's prose, or inside other words, are not the send.
        note = dashboard.RESUME_NOTE + "\n\nin"
        self.assertEqual(self.overlapping(["in", "in", "in"], note), ["confirmed", "delivered", "delivered"])
        self.assertEqual(self.overlapping(["ok"], "book it"), ["delivered"])

    def test_one_send_claims_its_points_words_and_images_together(self):
        # Review 3: an image sent alone cannot ride on the image of a send
        # confirmed by its point.
        captioned = "caption\n\n[point P2]\n\n[image] C:/a.png"
        self.assertEqual(self.overlapping([captioned, "[image] C:/a.png"], captioned), ["confirmed", "delivered"])
        self.assertEqual(self.overlapping(["[image] C:/a.png", captioned], captioned), ["delivered", "confirmed"])
        self.assertEqual(self.overlapping(["[image] C:/a.png", captioned], captioned + "\n\n[image] C:/a.png"),
                         ["confirmed", "confirmed"])

    def test_two_follow_ups_to_one_point_in_one_input(self):
        # Review 3: follow-ups to P1 each carry [point P1]; a resume types
        # them in as one input.
        a, b = "more on it\n\n[point P1]", "and this\n\n[point P1]"
        self.assertEqual(self.overlapping([a, b], "[resumed] carry on\n\n" + a + "\n\n" + b), ["confirmed", "confirmed"])
        self.assertEqual(self.overlapping([a, b], a), ["confirmed", "delivered"])

    def test_the_first_400_characters_in_code_points(self):
        # Review 3: an astral character at the cut, and a send of exactly 400
        # characters, which is not cut short.
        self.assertFalse(sends.matches("a" * 399 + "\U0001F600", "a" * 399 + "\U0001F601"))
        self.assertFalse(sends.matches("a" * 400, "a" * 400 + "x"))
        self.assertTrue(sends.matches("a" * 400, "a" * 400))
        self.assertTrue(sends.matches("a" * 401, "a" * 400 + "a and more"))


@unittest.skipUnless(NODE, "node is not installed")
class ThePage(unittest.TestCase):
    """The page's side: sendsToShow, sendHtml and the poll/reply hand-over."""

    @classmethod
    def setUpClass(cls):
        block = SESSION[SESSION.index("// ---- What you sent, until the conversation shows it: begin"):
                        SESSION.index("// ---- What you sent, until the conversation shows it: end")]
        js = r"""
const vm = require('vm');
const code = require('fs').readFileSync(0, 'utf8');
let renders = 0;
const ctx = {
  esc: s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
  whoHtml: m => 'You', stripPointLines: t => String(t).replace(/\n*^\[point P\d+\]$/gm, ''),
  renderBubbles: () => { renders++; }, LAST_ITEMS: [], ROOM: 'room-1', pointItems: () => null,
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
const run = s => vm.runInContext(s, ctx);
const md = t => t;
const out = {};
const sent = { key: 'send:p11', text: 'A long message\n\n[point P11]', at: 1000, state: 'delivered' };
const busy = [{ id: 's:0', from: 'user', text: 'spec', ts: 10 }, { id: 's:1', from: 'codex', text: 'working', ts: 20 }];
// P11: other rows and any time leave it; its turn replaces it.
run('ROOM_SENDS = ' + JSON.stringify([sent]));
const more = busy.concat([{ id: 'h1', from: 'hub', text: 'restart', ts: 1100 }, { id: 's:2', from: 'codex', text: 'more', ts: 1300 }]);
out.through = ctx.sendsToShow(more).map(s => s.key);
const turn = { id: 's:3', from: 'user', text: 'A long message\n\n[point P11]', ts: 1400 };
out.replaced = ctx.sendsToShow(more.concat([turn])).map(s => s.key);
// Confirmed by the hub before this page has the turn: still shown; then its turn.
run('ROOM_SENDS = ' + JSON.stringify([Object.assign({}, sent, { state: 'confirmed', mid: 's:3' })]));
out.confirmedEarly = ctx.sendsToShow(more).map(s => s.key);
out.confirmedDrawn = ctx.sendsToShow(more.concat([turn])).map(s => s.key);
// A command confirmed with no turn: nothing to draw.
run('ROOM_SENDS = ' + JSON.stringify([{ key: 'k', text: '/compact', at: 1, state: 'confirmed' }]));
out.command = ctx.sendsToShow(busy).length;
// The same words twice: one turn answers one send.
run('ROOM_SENDS = ' + JSON.stringify([{ key: 'a', text: 'ok go', at: 1000, state: 'delivered' }, { key: 'b', text: 'ok go', at: 1001, state: 'delivered' }]));
out.twice = ctx.sendsToShow(busy.concat([{ id: 's:4', from: 'user', text: 'ok go', ts: 1002 }])).map(s => s.key);
// An older turn with the same words does not count.
out.older = ctx.sendsToShow([{ id: 's:0', from: 'user', text: 'ok go', ts: 10 }]).map(s => s.key);
// One resume input carries several sends: each is replaced by it.
run('ROOM_SENDS = ' + JSON.stringify([{ key: 'p1', text: 'one\n\n[point P1]', at: 1000, state: 'delivered' },
  { key: 'p2', text: 'two\n\n[point P2]', at: 1001, state: 'delivered' }, { key: 'w', text: 'plain words', at: 1002, state: 'delivered' }]));
out.coalesced = ctx.sendsToShow(busy.concat([{ id: 's:5', from: 'user', ts: 1003,
  text: '[resumed] carry on\n\none\n\n[point P1]\n\ntwo\n\n[point P2]\n\nplain words' }])).map(s => s.key);
// An image alone: its path, as Claude's turn gives it back.
run('ROOM_SENDS = ' + JSON.stringify([{ key: 'img', text: '[image] C:\\att\\shot.png', at: 1000, state: 'delivered' }]));
out.imageOther = ctx.sendsToShow(busy.concat([{ id: 's:6', from: 'user', text: '[Image #1]\n[image] C:/att/other.png', ts: 1001 }])).map(s => s.key);
out.imageTurn = ctx.sendsToShow(busy.concat([{ id: 's:6', from: 'user', text: '[Image #1]\n[image] C:/att/shot.png', ts: 1001 }])).map(s => s.key);
// Review 2: one part of a turn confirms one send, the fullest first.
const over = (texts, turn) => {
  run('ROOM_SENDS = ' + JSON.stringify(texts.map((text, i) => ({ key: 'o' + i, text, at: 1000 + i, state: 'delivered' }))));
  return ctx.sendsToShow([{ id: 's:9', from: 'user', text: turn, ts: 1010 }]).map(s => s.key);
};
out.overlap = [over(['go', 'go now'], 'go now'), over(['go', 'go now'], 'go\n\ngo now'),
  over(['[image] a.png', '[image] a.png\n[image] b.png'], '[image] a.png\n[image] b.png'),
  over(['[image] a.png\n[image] a.png'], '[image] a.png'), over(['ok'], 'book it')];
// Review 3: one send claims its point, words and image together; a point
// repeated by two follow-ups counts twice; 400 characters in code points.
const cap = 'caption\n\n[point P2]\n\n[image] C:/a.png', f1 = 'more on it\n\n[point P1]', f2 = 'and this\n\n[point P1]';
out.review3 = [over([cap, '[image] C:/a.png'], cap), over(['[image] C:/a.png', cap], cap),
  over([f1, f2], '[resumed] carry on\n\n' + f1 + '\n\n' + f2), over([f1, f2], f1)];
out.cut = [ctx.sendMatches('a'.repeat(399) + '\u{1F600}', 'a'.repeat(399) + '\u{1F601}'),
  ctx.sendMatches('a'.repeat(400), 'a'.repeat(400) + 'x'), ctx.sendMatches('a'.repeat(400), 'a'.repeat(400)),
  ctx.sendMatches('a'.repeat(401), 'a'.repeat(400) + 'a and more')];
// The reply shows it at once; a poll that asked before keeps it, one after decides.
run('ROOM_SENDS = []; SENDS_GEN = 5;');
ctx.noteSent(sent);
out.local = ctx.sendsToShow(busy).map(s => s.key);
out.oldPoll = [ctx.sendsFromPoll([], 5), ctx.sendsToShow(busy).map(s => s.key)];
out.newPoll = [ctx.sendsFromPoll([sent], 6), ctx.sendsToShow(busy).map(s => s.key)];
out.samePoll = ctx.sendsFromPoll([sent], 7);
out.renders = renders;
// Each state in words.
out.html = ['queued', 'delivered', 'failed'].map(state => ctx.sendHtml(Object.assign({}, sent, { state, error: 'the task did not start' }), md));
console.log(JSON.stringify(out));
"""
        r = subprocess.run([NODE, "-e", js], input=block, capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr)
        cls.r = json.loads(r.stdout)

    def test_p11_other_rows_and_time_leave_it(self):
        self.assertEqual(self.r["through"], ["send:p11"])

    def test_its_turn_replaces_it_once(self):
        self.assertEqual(self.r["replaced"], [])
        self.assertEqual(self.r["confirmedEarly"], ["send:p11"], "gone before the page has the turn: a flash")
        self.assertEqual(self.r["confirmedDrawn"], [])
        self.assertEqual(self.r["command"], 0)

    def test_one_turn_per_send(self):
        self.assertEqual(self.r["twice"], ["b"])
        self.assertEqual(self.r["older"], ["a", "b"])

    def test_one_turn_for_several_sends(self):
        self.assertEqual(self.r["coalesced"], [])

    def test_one_part_of_a_turn_replaces_one_send(self):
        self.assertEqual(self.r["overlap"], [["o0"], [], ["o0"], ["o0"], ["o0"]])

    def test_one_send_claims_its_points_words_and_images_together(self):
        self.assertEqual(self.r["review3"], [["o1"], ["o0"], [], ["o1"]])

    def test_the_first_400_characters_as_the_hub_counts_them(self):
        self.assertEqual(self.r["cut"], [False, False, True, True])

    def test_an_image_alone_is_replaced_by_its_turn(self):
        self.assertEqual(self.r["imageOther"], ["img"])
        self.assertEqual(self.r["imageTurn"], [])

    def test_the_reply_and_the_polls(self):
        self.assertEqual(self.r["local"], ["send:p11"])
        self.assertEqual(self.r["oldPoll"], [True, ["send:p11"]], "a poll that asked before the reply dropped it")
        self.assertEqual(self.r["newPoll"], [True, ["send:p11"]])
        self.assertFalse(self.r["samePoll"])
        self.assertEqual(self.r["renders"], 1)

    def test_each_state_in_words(self):
        queued, delivered, failed = self.r["html"]
        self.assertIn("Queued: it goes in once the session is up.", queued)
        self.assertIn('class="pend-state ok"', queued)
        self.assertIn("Delivered, not yet read by the agent.", delivered)
        self.assertIn('class="send-hide"', delivered)
        self.assertIn("Not delivered: the task did not start.", failed)
        self.assertIn('class="send-retry" data-send="send:p11"', failed)
        self.assertIn('class="send-discard" data-send="send:p11"', failed)
        self.assertIn("msg user pending failed", failed)
        self.assertNotIn("[point P11]", delivered)

    def test_the_old_local_echo_is_gone(self):
        self.assertNotIn("PENDING_USER", SESSION)
        self.assertNotIn("showPendingUser", SESSION)
        self.assertNotIn("(nowS - p.ts) < 120", SESSION)
        self.assertIn(".msg.pending { border-style:dashed; }", SESSION)
        self.assertNotIn(".msg.pending { opacity", SESSION)


if __name__ == "__main__":
    unittest.main()
