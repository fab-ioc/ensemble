"""The person's points (points.py): every point they raise in a chat is kept by
the hub from the moment they send it until they acknowledge its answer.

* a message is a point, a review-comments message one per comment, each
  delivered with its ``[point Pn]`` line; the same send again is the same point;
* the first reply after a single point answers it; ``Re Pn:`` answers any,
  several in one reply, late, after hub traffic and after other points; a
  reply to hub input answers nothing without it;
* a follow-up ``Re Pn:`` from the person reopens Pn with the same id; ack,
  drop, reopen; a bare "thanks" acknowledges what was answered since;
* the ledger survives a reload of the module, a damaged file falls back to the
  version before it, and threads writing at once lose nothing;
* a team room: the owner's reply to the person answers, a message to the
  reviewer does not, a report saying ``Re Pn:`` does;
* the fresh session's first prompt and the note after a restart name the open
  points; the reminder is typed once per point, only into an idle live agent;
* the endpoints: an ack types nothing and wakes nobody, a foreign page is
  refused, an approval of a decision is typed exactly once.
"""
from __future__ import annotations

import importlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import points
import rotation


class _Pty:
    def __init__(self):
        self.typed = []
        self.id = "pty-1"

    def alive(self):
        return True

    def send_line(self, text):
        self.typed.append(text)
        return 0

    def last_submit(self):
        return 0.0


def turn(role, text, ts, kind=None, queued=False):
    t = {"role": role, "text": text,
         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"}
    if queued:
        t["queued"] = True
    return t


class _World(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.turns: dict[str, list] = {}
        self.gen = 0
        self.pty = _Pty()
        patches = [
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", base),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard, "read_session_turns",
                              side_effect=lambda sid: dashboard.classify_turns(self.turns.get(sid, []))),
            mock.patch.object(points, "_session_stat",
                              side_effect=lambda sid: [len(self.turns.get(sid, [])), self.gen]
                              if sid in self.turns else None),
            mock.patch.object(points, "_log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        points._CACHE.clear()
        points._SYNCED.clear()
        points._SCANNED.clear()
        points._ADOPT_SEEN.clear()
        self.t0 = time.time() - 3600

    def solo_room(self, sid="sid-1"):
        rid = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner"}])["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        room["participants"][0].update(sessionId=sid, ptyId="pty-1")
        chatroom.update_room(room)
        self.turns[sid] = []
        return rid

    def team_room(self):
        rid = chatroom.create_room("Task", [
            {"identity": "claude", "agent": "claude", "role": "engineer"},
            {"identity": "codex", "agent": "codex", "role": "reviewer"}])["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "collab"
        chatroom.update_room(room)
        return rid

    def send(self, rid, text, to="", key="", at=None):
        room = chatroom.get_room(rid, public=False)
        out, ids = points.take(room, text, to, key, now=at or time.time())
        return out, ids

    def add(self, sid, *turns):
        self.turns[sid].extend(turns)
        self.gen += 1

    def state(self, rid):
        led = points.sync(rid, force=True)
        return {p["id"]: p["state"] for p in led["points"]}

    def point(self, rid, pid):
        return next(p for p in points.load(rid)["points"] if p["id"] == pid)


class Reading(unittest.TestCase):
    def test_re_heads(self):
        self.assertEqual(points.re_ids("Re P12: yes\n\nRe P3, P4: no"), ["P12", "P3", "P4"])
        self.assertEqual(points.re_ids("**Re P7:** done\n- re p8 and P9: later"), ["P7", "P8", "P9"])
        self.assertEqual(points.re_ids("I said Re P1: inline"), [])
        self.assertEqual(points.re_ids("Re P12a: split"), ["P12a"])

    def test_point_lines_and_bare_acks(self):
        self.assertEqual(points.point_ids("x\n\n[point P1]\n[point P2]"), ["P1", "P2"])
        self.assertEqual(points.strip_point_lines("x\n\n[point P1]\n[point P2]"), "x")
        for t in ("ok", "Thanks!", "got it, thanks 👍", "ok thank you", "perfect"):
            self.assertTrue(points.is_bare_ack(t), t)
        for t in ("ok but why", "yes", "thanks, now do X", "no", "", "ok\nthanks"):
            self.assertFalse(points.is_bare_ack(t), t)


class Ledger(_World):
    def test_a_message_is_a_point_and_the_same_key_is_the_same_point(self):
        rid = self.solo_room()
        out, ids = self.send(rid, "Why is the build red?", key="k1")
        self.assertEqual((ids, out), (["P1"], "Why is the build red?\n\n[point P1]"))
        self.assertEqual(self.send(rid, "Why is the build red?", key="k1"), (out, ["P1"]))
        _, ids = self.send(rid, "And the docs?")
        self.assertEqual(ids, ["P2"])
        self.assertEqual(self.state(rid), {"P1": "open", "P2": "open"})

    def test_each_comment_is_its_own_point(self):
        rid = self.solo_room()
        body = ("## Review comments (2)\n\n**1.** > the first passage\n\nWhy?\n\n"
                "**2.** `a.py` line 3\n\n```diff\n+**3.** not an item\n```\n\nRename it.")
        out, ids = self.send(rid, body)
        self.assertEqual(ids, ["P1", "P2"])
        self.assertIn("Why?\n\n[point P1]\n\n**2.**", out)
        self.assertTrue(out.endswith("Rename it.\n\n[point P2]"))
        self.assertIn("the first passage", self.point(rid, "P1")["text"])
        self.assertTrue(self.point(rid, "P2")["comment"])

    def test_not_points(self):
        rid = self.solo_room()
        for t in ("/compact", "[digest] x", "[from the PO] do it", "ok thanks"):
            self.assertEqual(self.send(rid, t)[1], [], t)
        self.assertEqual(points.load(rid)["points"], [])

    def test_the_first_reply_after_a_single_point_answers_it(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Is #26 merged?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Checking.", self.t0 + 5),
                 turn("assistant", "Yes, merged at 10:05.", self.t0 + 9))
        self.assertEqual(self.state(rid), {"P1": "answered"})
        p = self.point(rid, "P1")
        self.assertEqual(p["mid"], "sid-1:0")
        self.assertEqual([a["mid"] for a in p["answers"]], ["sid-1:2"], "the run's last balloon")

    def test_a_reply_to_hub_input_answers_nothing_without_re(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Is #26 merged?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("user", "[report] completed from task 'X' (#26, claude): done", self.t0 + 2),
                 turn("assistant", "Merged #26.", self.t0 + 5))
        self.assertEqual(self.state(rid), {"P1": "open"})
        for kind in ("[digest] a", "[due] 15:40 — b", "[handover] c", dashboard.RESTART_NOTE):
            self.add("sid-1", turn("user", kind, self.t0 + 6), turn("assistant", "noted", self.t0 + 7))
        self.assertEqual(self.state(rid), {"P1": "open"})

    def test_re_answers_several_late_after_hub_traffic_and_other_points(self):
        rid = self.solo_room()
        a, _ = self.send(rid, "First question", at=self.t0)
        b, _ = self.send(rid, "Second question", at=self.t0 + 1)
        c, _ = self.send(rid, "Third question", at=self.t0 + 2)
        # Sent while it was busy: read at its next pause, then a report came in.
        self.add("sid-1", turn("user", a, self.t0 + 1, queued=True), turn("user", b, self.t0 + 2, queued=True),
                 turn("assistant", "Working on the migration.", self.t0 + 3),
                 turn("user", "[report] update from task 'X' (#2, codex): half", self.t0 + 4),
                 turn("assistant", "Re P2: no.\n\n**Re P1:** yes, tomorrow.", self.t0 + 5),
                 turn("user", c, self.t0 + 6), turn("assistant", "Re P3: done", self.t0 + 7))
        self.assertEqual(self.state(rid), {"P1": "answered", "P2": "answered", "P3": "answered"})
        self.assertEqual(self.point(rid, "P1")["mid"], "sid-1:q0")
        self.assertEqual(self.point(rid, "P2")["answers"][0]["how"], "re")
        # An answer much later, in the next session, to a point reopened meanwhile.
        points.act(rid, "P3", "ack")
        points.act(rid, "P3", "reopen")
        self.turns["sid-2"] = [turn("assistant", "Re P3: redone differently", self.t0 + 60)]
        room = chatroom.get_room(rid, public=False)
        room["participants"][0]["rotations"] = [{"fromSessionId": "sid-1", "toSessionId": "sid-2"}]
        room["participants"][0]["sessionId"] = "sid-2"
        chatroom.update_room(room)
        self.assertEqual(self.state(rid)["P3"], "answered")
        self.assertEqual(len(self.point(rid, "P3")["answers"]), 2)

    def test_a_session_read_again_keeps_only_the_answers_it_holds(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Re P1: flaky.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "answered"})
        # An answer linked under a turn count the session no longer has (a hub
        # that counted its turns otherwise) goes when the session is read again.
        led = points.load(rid)
        led["points"][0]["answers"].append({"key": "sid-1:7", "mid": "sid-1:7", "at": self.t0 + 3, "how": "re"})
        points._save(rid, led)
        points._SCANNED.clear()
        points.sync(rid, force=True)
        p = self.point(rid, "P1")
        self.assertEqual(([a["mid"] for a in p["answers"]], p["state"]), (["sid-1:1"], "answered"))

    def test_a_follow_up_reopens_with_the_same_id_and_the_thread_keeps_both(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A flaky test.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "answered"})
        again, ids = self.send(rid, "Re P1: which one?", at=self.t0 + 3)
        self.assertEqual((ids, again), (["P1"], "Re P1: which one?\n\n[point P1]"))
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.add("sid-1", turn("user", again, self.t0 + 4), turn("assistant", "test_x.", self.t0 + 5))
        self.assertEqual(self.state(rid), {"P1": "answered"})
        p = self.point(rid, "P1")
        self.assertEqual((len(p["answers"]), p["followUps"]), (2, ["sid-1:2"]))
        self.assertEqual(len(points.load(rid)["points"]), 1)

    def test_ack_drop_reopen_and_a_bare_thanks(self):
        rid = self.solo_room()
        a, _ = self.send(rid, "One?", at=self.t0)
        b, _ = self.send(rid, "Two?", at=self.t0 + 1)
        self.add("sid-1", turn("user", a, self.t0 + 1), turn("assistant", "one", self.t0 + 2),
                 turn("user", b, self.t0 + 3), turn("assistant", "two", self.t0 + 4))
        self.assertEqual(self.state(rid), {"P1": "answered", "P2": "answered"})
        self.assertEqual(points.act(rid, "P1", "ack")["state"], "acked")
        self.assertIsNone(points.act(rid, "P1", "ack"), "already acknowledged")
        self.assertIsNone(points.act(rid, "P1", "drop"))
        self.assertEqual(points.act(rid, "p1", "reopen")["state"], "open")
        self.assertEqual(points.act(rid, "P1", "drop")["state"], "dropped")
        self.assertIsNone(points.act(rid, "P9", "ack"))
        # "thanks": what was answered since the person last spoke (P2's answer).
        self.assertEqual(self.send(rid, "Thanks!", at=self.t0 + 2.5)[1], [])
        self.assertEqual(self.state(rid), {"P1": "dropped", "P2": "acked"})

    def test_the_last_unanswered_message_is_taken_in_once(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "old question", self.t0), turn("assistant", "answer", self.t0 + 1),
                 turn("user", "is the deploy done?", self.t0 + 2))
        led = points.sync(rid, force=True)
        self.assertEqual([(p["id"], p["text"], p["mid"]) for p in led["points"]],
                         [("P1", "is the deploy done?", "sid-1:2")])
        other = self.solo_room("sid-9")
        self.add("sid-9", turn("user", "q", self.t0), turn("assistant", "a", self.t0 + 1))
        self.assertEqual(points.sync(other, force=True)["points"], [])
        self.assertFalse(points.exists(other), "nothing written for a room with nothing open")


class Storage(_World):
    def test_it_survives_a_reload_of_the_module(self):
        rid = self.solo_room()
        self.send(rid, "Keep me")
        points.act(rid, "P1", "drop")
        importlib.reload(points)
        points.bind(dashboard)
        self.addCleanup(lambda: (importlib.reload(points), points.bind(dashboard)))
        dashboard.points = points
        led = points.load(rid)
        self.assertEqual([(p["id"], p["state"]) for p in led["points"]], [("P1", "dropped")])
        self.assertEqual(led["next"], 2)

    def test_a_damaged_file_falls_back_to_the_version_before(self):
        rid = self.solo_room()
        self.send(rid, "one")
        self.send(rid, "two")
        path = points._path(rid)
        path.write_text("{not json", encoding="utf-8")
        points._CACHE.clear()
        led = points.load(rid)
        self.assertEqual([p["id"] for p in led["points"]], ["P1"], "the version before the last write")
        self.assertTrue(list(path.parent.glob(path.name + ".damaged-*")))
        # Both gone: it starts again, and says so.
        path.write_text("[]", encoding="utf-8")
        path.with_suffix(".json.prev").write_text("garbage", encoding="utf-8")
        points._CACHE.clear()
        led = points.load(rid)
        self.assertEqual((led["points"], bool(led.get("damaged"))), ([], True))
        self.assertEqual(self.send(rid, "three")[1], ["P1"])

    def test_writes_at_once_lose_nothing(self):
        rid = self.solo_room()
        room = chatroom.get_room(rid, public=False)
        got, errs = [], []

        def one(i):
            try:
                got.extend(points.take(room, f"question {i}", "", f"k{i}")[1])
            except Exception as e:      # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=one, args=(i,)) for i in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(sorted(got, key=lambda i: int(i[1:])), [f"P{i}" for i in range(1, 13)])
        points._CACHE.clear()
        self.assertEqual(len(points.load(rid)["points"]), 12)


class Team(_World):
    def post(self, rid, sender, text, to="", **meta):
        m = chatroom.post_message(rid, sender, text, to=to)["message"]
        if meta:
            room = chatroom.get_room(rid, public=False)
            room["messages"][-1].update(meta)
            chatroom.update_room(room)
        return m

    def test_the_owners_reply_answers_a_message_to_the_reviewer_does_not(self):
        rid = self.team_room()
        out, ids = self.send(rid, "Is the parser done?")
        self.assertEqual(self.point(rid, "P1")["owner"], "claude")
        self.post(rid, "user", out)
        self.post(rid, "claude", "Please review abc123", to="codex")
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.post(rid, "claude", "Yes, done in abc123.", to="user")
        self.assertEqual(self.state(rid), {"P1": "answered"})
        # To the reviewer by name: its point.
        out, _ = self.send(rid, "@codex does it hold?", to="codex")
        self.assertEqual(self.point(rid, "P2")["owner"], "codex")
        self.post(rid, "user", out, to="codex")
        self.post(rid, "codex", "verdict", to="user", kind="report")
        self.assertEqual(self.state(rid)["P2"], "open", "a report is not an implicit answer")
        self.post(rid, "claude", "Re P2: it holds, see the log.", to="user", kind="report")
        self.assertEqual(self.state(rid)["P2"], "answered")


class Prompts(_World):
    def test_the_fresh_sessions_first_prompt_and_the_restart_note_name_open_points(self):
        rid = self.solo_room()
        self.send(rid, "Ship the fix before Friday", at=self.t0)
        self.send(rid, "And the release notes", at=self.t0 + 60)
        points.act(rid, "P2", "drop")
        room = chatroom.get_room(rid, public=False)
        with mock.patch.object(dashboard, "roadmap_path", return_value=Path("ROADMAP.md")):
            text = rotation.first_prompt({"id": "p1", "name": "Trading"}, room, "sid-old", 250_000)
        self.assertIn("Open points from sam", text)
        self.assertIn("- P1 (since", text)
        self.assertIn("Ship the fix before Friday", text)
        self.assertNotIn("release notes", text)
        text = rotation.task_first_prompt(room, "sid-old", 250_000, Path("TASK-HANDOVER.md"), True)
        self.assertIn("Ship the fix before Friday", text)
        line = points.note_line(rid, "claude")
        self.assertTrue(line.startswith("[points] Open points from sam"))
        self.assertIn('P1 "Ship the fix before Friday"', line)
        self.assertEqual(dashboard.hub_input_kind(line)["kind"], "points")
        # Typed after the restart note and before what the person sent: three turns.
        turns = dashboard.classify_turns([{"role": "user", "text": dashboard.RESTART_NOTE + "\n\n" + line
                                           + "\n\nand go on"}])
        self.assertEqual([t["kind"] for t in turns], ["restart", "points", "human"])
        self.assertEqual(points.note_line(rid, "claude", skip={"P1"}), "")

    def test_no_points_no_block(self):
        rid = self.solo_room()
        self.assertEqual(points.prompt_block(rid), "")
        self.assertEqual(points.note_line(rid), "")


class Reminder(_World):
    def setUp(self):
        super().setUp()
        self.idle = True
        for p in [
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.pty if self.live else None),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
            mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (None, lambda p: {})),
            mock.patch.object(rotation, "_submitted_lately", return_value=False),
            mock.patch.object(rotation, "is_rotating", return_value=False),
            mock.patch.object(rotation, "awaiting_handover", return_value=False),
            mock.patch.object(dashboard, "load_settings", return_value={"pointsRemindMin": 20}),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.live = True

    def test_once_per_point_only_into_an_idle_live_agent(self):
        rid = self.solo_room()
        now = time.time()
        self.send(rid, "First, is the backup running?", at=now - 30 * 60)
        self.send(rid, "Second", at=now - 25 * 60)
        self.send(rid, "Too new", at=now - 5 * 60)
        self.idle = False
        self.assertEqual(points.tick(now), [])
        self.live, self.idle = False, True
        self.assertEqual(points.tick(now), [], "a stopped agent is never started for it")
        self.live = True
        self.assertEqual(points.tick(now), ["P1", "P2"])
        self.assertEqual(len(self.pty.typed), 1)
        line = self.pty.typed[0]
        self.assertTrue(line.startswith('[points] still open: P1 "First, is the backup running?" (since '))
        self.assertIn('; P2 "Second"', line)
        self.assertNotIn("Too new", line)
        self.assertEqual(dashboard.hub_input_kind(line)["kind"], "points")
        self.assertEqual(points.tick(now + 60), [])
        self.assertEqual(points.tick(now + 16 * 60), ["P3"])
        self.assertEqual(points.tick(now + 99 * 60), [])
        self.assertEqual(len(self.pty.typed), 2)
        # Reopened, it may be reminded once more.
        points.act(rid, "P1", "drop")
        points.act(rid, "P1", "reopen", now=now + 100 * 60)
        self.assertEqual(points.tick(now + 121 * 60), ["P1"])

    def test_off_when_the_setting_is_zero(self):
        rid = self.solo_room()
        self.send(rid, "x", at=time.time() - 3600)
        with mock.patch.object(dashboard, "load_settings", return_value={"pointsRemindMin": 0}):
            self.assertEqual(points.tick(), [])


class Turns(unittest.TestCase):
    def test_a_pasted_message_is_the_persons_turn(self):
        # Claude Code 2.1.278 logs a bracketed paste (every multi-line message the
        # hub types) wrapped; it used to start with "<" and never showed.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            body = '\n\n<pasted_content id="40eb">\nPoint A: what is 2 + 2?\n\n[point P2]\n</pasted_content id="40eb">\n'
            p.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": body},
                                     "timestamp": "2026-09-21T10:00:00Z"}), encoding="utf-8")
            self.assertEqual([t["text"] for t in dashboard._claude_text_turns(p)],
                             ["Point A: what is 2 + 2?\n\n[point P2]"])


    def test_a_queued_command_is_a_turn_with_its_own_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            lines = [
                {"type": "user", "message": {"role": "user", "content": "hello"}, "timestamp": "2026-09-21T10:00:00Z"},
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
                 "timestamp": "2026-09-21T10:00:01Z"},
                {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "while busy\n\n[point P1]",
                                                       "commandMode": "prompt"}, "timestamp": "2026-09-21T10:00:02Z"},
                {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "<task-notification>x",
                                                       "commandMode": "task-notification"}},
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
                 "timestamp": "2026-09-21T10:00:03Z"},
                {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Re P1: yes"}]},
                 "timestamp": "2026-09-21T10:00:04Z"},
            ]
            p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            turns = dashboard.classify_turns(dashboard._claude_text_turns(p))
        self.assertEqual([(t["role"], t["text"]) for t in turns],
                         [("user", "hello"), ("assistant", "hi"), ("user", "while busy\n\n[point P1]"),
                          ("assistant", "hi"), ("assistant", "Re P1: yes")])
        # The ids are the page's: a repeated turn is dropped, a queued one counted apart,
        # so the others keep the ids they had before queued lines were shown.
        self.assertEqual([i for i, _ in dashboard.page_turn_ids("s", turns)], ["s:0", "s:1", "s:q0", "s:2"])


class PageIds(unittest.TestCase):
    """The chat page and the hub name a one-agent chat's balloons alike: a
    point finds its balloon by the id the page draws it with."""

    def test_soloitems_and_page_turn_ids_agree(self):
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("needs Node")
        src = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        m = re.search(r"^function soloItems\(", src, re.M)
        fn = src[m.start():src.index("\n}\n", m.start()) + 3]
        raw = [{"role": "user", "text": "a"}, {"role": "user", "text": "a"}, {"role": "assistant", "text": "b"},
               {"role": "user", "text": "q1", "queued": True}, {"role": "assistant", "text": "b"},
               {"role": "assistant", "text": "c"}, {"role": "user", "text": "q2", "queued": True},
               {"role": "user", "text": "d"}]
        js = fn + "\nconst raw = " + json.dumps(raw) + ";\nconsole.log(JSON.stringify([soloItems(raw, 's', 'claude', 99).map(m => m.id), soloItems(raw, 's', 'claude', 2).map(m => m.id)]));"
        out = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        full, last2 = json.loads(out.stdout)
        self.assertEqual(full, [i for i, _ in dashboard.page_turn_ids("s", raw)])
        self.assertEqual(full, ["s:0", "s:1", "s:q0", "s:2", "s:q1", "s:3"])
        self.assertEqual(last2, ["s:2", "s:q1", "s:3"])


def http(path, body, origin=None):
    raw = json.dumps(body).encode()
    h = dashboard.Handler.__new__(dashboard.Handler)
    h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
    h.requestline = f"POST {path} HTTP/1.1"
    h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json", "Host": "127.0.0.1"}
    if origin:
        h.headers["Origin"] = origin
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h.client_address = ("127.0.0.1", 50000)
    h.log_message = lambda *a: None
    h.do_POST()
    head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(payload or b"{}")


class Endpoints(_World):
    def test_an_ack_types_nothing_and_a_foreign_page_is_refused(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Q?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A.", self.t0 + 2))
        with mock.patch.object(dashboard.Handler, "_resume_room") as resume, \
                mock.patch.object(dashboard.Handler, "_ring") as ring:
            status, _ = http("/api/room/points", {"roomId": rid, "id": "P1", "action": "ack"},
                             origin="http://elsewhere.example")
            self.assertEqual(status, 403)
            status, r = http("/api/room/points", {"roomId": rid, "id": "P1", "action": "ack"})
            self.assertEqual((status, r["point"]["state"]), (200, "acked"))
            self.assertEqual((r["points"]["open"], r["points"]["answered"]), (0, 0))
            self.assertEqual(http("/api/room/points", {"roomId": rid, "id": "P1", "action": "ack"})[0], 409)
            self.assertEqual(http("/api/room/points", {"roomId": rid, "id": "P1", "action": "zap"})[0], 400)
        resume.assert_not_called()
        ring.assert_not_called()

    def test_an_approval_is_typed_once(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Which way?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("assistant", "Decision needed: A or B?\n\nI recommend A.", self.t0 + 2))
        points.sync(rid, force=True)
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="": sent.append((text, key)) or {"delivered": 1}):
            body = {"roomId": rid, "mid": "sid-1:1", "question": "A or B?"}
            self.assertEqual(http("/api/room/approve", body)[0], 200)
            status, r = http("/api/room/approve", body)
        self.assertEqual((status, r.get("duplicate")), (200, True))
        self.assertEqual(sent, [("Approved: go with your recommendation on “A or B?”.", "approve:sid-1:1")])
        self.assertEqual(self.state(rid), {"P1": "acked"}, "the decision it answered is acknowledged")
        # Refused: it may be given again.
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("no")):
            self.assertEqual(http("/api/room/approve", {"roomId": rid, "mid": "sid-1:9"})[0], 400)
        self.assertNotIn("sid-1:9", points.load(rid)["approvals"])

    def test_the_room_poll_carries_the_points(self):
        rid = self.solo_room()
        self.send(rid, "Q?")
        room = dashboard._annotate_room_liveness(chatroom.get_room(rid), with_points=True)
        self.assertEqual((room["points"]["open"], [i["id"] for i in room["points"]["items"]]), (1, ["P1"]))
        self.assertEqual(points.counts(rid), {"open": 1, "answered": 0})


if __name__ == "__main__":
    unittest.main()
