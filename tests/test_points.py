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


def turn(role, text, ts, kind=None, queued=False, interim=False):
    t = {"role": role, "text": text,
         "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"}
    if queued:
        t["queued"] = True
    if interim:
        t["interim"] = True
    return t


class _World(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.turns: dict[str, list] = {}
        self.gen = 0
        self.mtime = time.time() + 86400    # a transcript written after every point
        self.pty = _Pty()
        patches = [
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", base),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard, "read_session_turns",
                              side_effect=lambda sid: dashboard.classify_turns(self.turns.get(sid, []))),
            mock.patch.object(points, "_session_stat",
                              side_effect=lambda sid: [len(self.turns.get(sid, [])), self.mtime + self.gen]
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
        points._TOLD.clear()
        points._GONE.clear()
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

    def test_examples_in_code_and_quotes_answer_nothing(self):
        text = "Write it like this:\n\n```\nRe P1: your answer here\n```\n\n> Re P2: quoted\n\nRe P3: real"
        self.assertEqual(points.re_ids(text), ["P3"])
        self.assertEqual(points.re_ids("~~~md\nRe P1: x\n~~~\nRe P4: y"), ["P4"])
        nested = "````markdown\n```\nRe P1: example only\n```\n````\nRe P5: real"
        self.assertEqual(points.re_ids(nested), ["P5"])
        self.assertEqual(points.re_ids("```\nRe P1: x\n```js\nRe P2: still code\n```\nRe P6: out"), ["P6"])

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

    def test_each_item_of_a_points_message_is_its_own_point(self):
        # The chat editor's numbered points: "## Points (N)", one **N.** item
        # each, a point's images and links written inside it.
        rid = self.solo_room()
        body = ("## Points (3)\n\nAfter today's tests.\n\n"
                "**1.** The board is slow\n[image] 2026-09-22 10.00.00 screenshot.png\n\n"
                "**2.** See http://h/session?room=room-1a2b3c4d&msg=0123456789ab\n\n"
                "```\n**9.** not an item\n```\n\n"
                "**3.**\n\n- a list\n- inside the point")
        # The words above the list are a point too, the first (the person
        # typed the message and then added points under it: P6, 09-23).
        out, ids = self.send(rid, body, key="k-pts")
        self.assertEqual(ids, ["P1", "P2", "P3", "P4"])
        self.assertTrue(out.startswith("## Points (3)\n\nAfter today's tests.\n\n[point P1]\n\n**1.** The board is slow\n"
                                       "[image] 2026-09-22 10.00.00 screenshot.png\n\n[point P2]\n\n**2.** "), out)
        self.assertIn("**9.** not an item\n```\n\n[point P3]\n\n**3.**", out)
        self.assertTrue(out.endswith("- inside the point\n\n[point P4]"), out)
        self.assertEqual(points.point_ids(out), ["P1", "P2", "P3", "P4"])
        self.assertEqual(self.point(rid, "P1")["text"], "After today's tests.")
        self.assertEqual(self.point(rid, "P2")["text"], "**1.** The board is slow\n[image] 2026-09-22 10.00.00 screenshot.png")
        self.assertIn("a list", self.point(rid, "P4")["text"])
        self.assertNotIn("comment", self.point(rid, "P2"), "a point is not a review comment")
        self.assertEqual(self.send(rid, body, key="k-pts"), (out, ids), "a retried send is the same points")
        # The same head with no item is one point, as any message.
        _, ids = self.send(rid, "## Points (0)\n\njust words")
        self.assertEqual(ids, ["P5"])
        # A head of only the title and the head's images is no point: each
        # item is one, the images stay above the list.
        out, ids = self.send(rid, "## Points (2)\n\n[image] C:\\t\\attachments\\h.png\n\n**1.** a\n\n**2.** b")
        self.assertEqual(ids, ["P6", "P7"])
        self.assertTrue(out.startswith("## Points (2)\n\n[image] C:\\t\\attachments\\h.png\n\n**1.** a\n\n[point P6]"), out)
        # Head words with a head image: the point line under the image.
        out, ids = self.send(rid, "## Points (1)\n\nIntro\n[image] C:\\t\\attachments\\h.png\n\n**1.** a")
        self.assertEqual(ids, ["P8", "P9"])
        self.assertEqual(out, "## Points (1)\n\nIntro\n[image] C:\\t\\attachments\\h.png\n\n[point P8]\n\n**1.** a\n\n[point P9]")
        self.assertEqual(self.point(rid, "P8")["text"], "Intro")

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
        self.assertEqual(self.state(rid), {"P1": "delivered"})
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

    def test_a_line_written_between_tool_calls_is_not_an_answer(self):
        # 09-22: "Reproducing the balloon the CEO pointed at..." written while the
        # PO was still working marked P4 answered; the handover cut in before
        # its real reply, and only the fresh session's Re P4: answered it.
        rid = self.solo_room()
        a, _ = self.send(rid, "I still cannot open those images", at=self.t0)
        self.add("sid-1", turn("user", a, self.t0 + 1),
                 turn("assistant", "Reproducing the balloon through the page's own code.", self.t0 + 2, interim=True),
                 turn("assistant", "Rendering the live balloon in headless Chrome.", self.t0 + 3, interim=True))
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.add("sid-1", turn("user", "[handover] write it now", self.t0 + 4, queued=True),
                 turn("assistant", "The handover is current.", self.t0 + 5))
        self.assertEqual(self.state(rid), {"P1": "open"}, "the reply to the handover answers nothing")
        # An interim line that says Re Pn: is deliberate; the reply that ends
        # the turn answers by itself.
        b, _ = self.send(rid, "And the links?", at=self.t0 + 6)
        self.add("sid-1", turn("user", b, self.t0 + 7),
                 turn("assistant", "Checking the page.", self.t0 + 8, interim=True),
                 turn("assistant", "Re P1: your tab ran the old page; reload once.", self.t0 + 9, interim=True),
                 turn("assistant", "The links open on the live page.", self.t0 + 10))
        self.assertEqual(self.state(rid), {"P1": "delivered", "P2": "delivered"})
        self.assertEqual(self.point(rid, "P1")["answers"][0]["how"], "re")
        self.assertEqual(self.point(rid, "P2")["answers"][0]["mid"], "sid-1:7")

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
        self.assertEqual(self.state(rid), {"P1": "delivered", "P2": "delivered", "P3": "delivered"})
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
        self.assertEqual(self.state(rid)["P3"], "delivered")
        self.assertEqual(len(self.point(rid, "P3")["answers"]), 2)

    def test_a_session_read_again_keeps_only_the_answers_it_holds(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Re P1: flaky.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        # An answer linked under a turn count the session no longer has (a hub
        # that counted its turns otherwise) goes when the session is read again.
        led = points.load(rid)
        led["points"][0]["answers"].append({"key": "sid-1:7", "mid": "sid-1:7", "at": self.t0 + 3, "how": "re"})
        points._save(rid, led)
        points._SCANNED.clear()
        points.sync(rid, force=True)
        p = self.point(rid, "P1")
        self.assertEqual(([a["mid"] for a in p["answers"]], p["state"]), (["sid-1:1"], "delivered"))

    def test_a_follow_up_reopens_with_the_same_id_and_the_thread_keeps_both(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A flaky test.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        again, ids = self.send(rid, "Re P1: which one?", at=self.t0 + 3)
        self.assertEqual((ids, again), (["P1"], "Re P1: which one?\n\n[point P1]"))
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.add("sid-1", turn("user", again, self.t0 + 4), turn("assistant", "test_x.", self.t0 + 5))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        p = self.point(rid, "P1")
        self.assertEqual((len(p["answers"]), p["followUps"]), (2, ["sid-1:2"]))
        self.assertEqual(len(points.load(rid)["points"]), 1)

    def test_a_syntax_example_after_hub_input_answers_nothing(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Explain the points", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("user", "[digest] 2 tasks moved", self.t0 + 2),
                 turn("assistant", "Answer like this:\n\n```\nRe P1: your answer here\n```", self.t0 + 3))
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.add("sid-1", turn("assistant", "Re P1: every message is kept until you ack it.", self.t0 + 4))
        self.assertEqual(self.state(rid), {"P1": "delivered"})

    def test_old_unresolved_points_are_always_listed(self):
        rid = self.solo_room()
        self.send(rid, "Old open", at=self.t0)
        self.send(rid, "Old answered", at=self.t0 + 1)
        points.answer_by_tool(rid, "P2", "done", "claude")
        with mock.patch.object(points, "VIEW_MAX", 5):
            for k in range(8):
                self.send(rid, f"Newer {k}", at=self.t0 + 10 + k)
                points.act(rid, f"P{k + 3}", "ack")
            v = points.view(rid)
        ids = [i["id"] for i in v["items"]]
        self.assertEqual((v["open"], v["delivered"]), (1, 1))
        self.assertIn("P1", ids)
        self.assertIn("P2", ids)
        self.assertEqual(len(ids), 5)
        self.assertEqual(ids[:3], ["P10", "P9", "P8"])

    def test_ack_drop_reopen_and_a_bare_thanks(self):
        rid = self.solo_room()
        a, _ = self.send(rid, "One?", at=self.t0)
        b, _ = self.send(rid, "Two?", at=self.t0 + 1)
        self.add("sid-1", turn("user", a, self.t0 + 1), turn("assistant", "one", self.t0 + 2),
                 turn("user", b, self.t0 + 3), turn("assistant", "two", self.t0 + 4))
        self.assertEqual(self.state(rid), {"P1": "delivered", "P2": "delivered"})
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
        # Taken in, it is answered by the reply after its balloon.
        self.add("sid-1", turn("assistant", "Yes, deployed.", self.t0 + 3))
        self.assertEqual(self.state(rid), {"P1": "delivered"})

    def test_what_launched_a_room_is_not_taken_in(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "You are the PO of this project. Reply briefly.", self.t0))
        self.assertEqual(points.sync(rid, force=True)["points"], [])
        team = self.team_room()
        chatroom.post_message(team, chatroom.HUMAN_IDENTITY, "Build the thing (the task)")
        self.assertEqual(points.sync(team, force=True)["points"], [])


class POCheck(_World):
    """The PO's merge check: reading, links and writes that must hold up."""

    def test_a_file_that_cannot_be_read_now_is_not_damage(self):
        rid = self.solo_room()
        self.send(rid, "one")
        points._CACHE.clear()
        real = Path.read_text

        def read(path, *a, **k):
            if path.name.endswith(".json") and path.parent.name == "points":
                raise PermissionError("locked")
            return real(path, *a, **k)
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=read):
            with self.assertRaises(OSError):
                points.load(rid)
        self.assertEqual([f.name for f in points._dir().iterdir() if "damaged" in f.name], [])
        self.assertEqual(self.send(rid, "two")[1], ["P2"], "numbering goes on")

    def test_transcripts_are_read_without_the_lock_and_old_ones_not_at_all(self):
        rid = self.solo_room()
        held, read = [], []

        def turns(sid):
            held.append(points._LOCK._is_owned())
            read.append(sid)
            return dashboard.classify_turns(self.turns.get(sid, []))
        out, _ = self.send(rid, "Q?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A.", self.t0 + 2))
        with mock.patch.object(dashboard, "read_session_turns", side_effect=turns):
            self.assertEqual(self.state(rid), {"P1": "delivered"})
            self.assertEqual(held, [False])
            # A session last written before the oldest waiting point is not read.
            room = chatroom.get_room(rid, public=False)
            room["participants"][0]["rotations"] = [{"fromSessionId": "sid-old", "toSessionId": "sid-1"}]
            chatroom.update_room(room)
            with mock.patch.object(points, "_session_stat",
                                   side_effect=lambda sid: [1, self.t0 - 9000] if sid == "sid-old" else None):
                read.clear()
                points.sync(rid, force=True)
            self.assertEqual(read, [])

    def test_an_empty_read_keeps_the_links(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Q?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        self.gen += 1
        with mock.patch.object(dashboard, "read_session_turns", return_value=[]):
            points.sync(rid, force=True)
        self.assertEqual([a["mid"] for a in self.point(rid, "P1")["answers"]], ["sid-1:1"])

    def test_a_team_reply_after_hub_input_answers_only_with_re(self):
        rid = self.team_room()
        out, _ = self.send(rid, "Is it merged?", at=time.time() - 5)
        chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, out)
        time.sleep(0.01)
        chatroom.patch_participant(rid, "claude", {"resumedAt": time.time()})
        time.sleep(0.01)
        chatroom.post_message(rid, "claude", "Resumed, carrying on.", to="user")
        self.assertEqual(self.state(rid), {"P1": "open"})
        chatroom.post_message(rid, "claude", "Re P1: yes, merged.", to="user")
        self.assertEqual(self.state(rid), {"P1": "delivered"})

    def test_a_lone_surrogate_is_saved_and_no_temp_file_stays(self):
        rid = self.solo_room()
        self.assertEqual(self.send(rid, "odd \ud800 text")[1], ["P1"])
        points._CACHE.clear()
        self.assertIn("\ud800", points.load(rid)["points"][0]["text"])
        self.assertEqual([f.name for f in points._dir().iterdir() if f.name.endswith(".tmp")], [])

    def test_a_bad_room_id_names_no_file(self):
        with self.assertRaises(ValueError):
            points.load("../rooms/x")
        self.assertFalse(points.exists("..\\x"))

    def test_an_interruption_is_not_taken_in(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "launch", self.t0), turn("assistant", "ok", self.t0 + 1),
                 turn("user", "[Request interrupted by user]", self.t0 + 2))
        self.assertEqual(points.sync(rid, force=True)["points"], [])


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
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        # To the reviewer by name: its point.
        out, _ = self.send(rid, "@codex does it hold?", to="codex")
        self.assertEqual(self.point(rid, "P2")["owner"], "codex")
        self.post(rid, "user", out, to="codex")
        self.post(rid, "codex", "verdict", to="user", kind="report")
        self.assertEqual(self.state(rid)["P2"], "open", "a report is not an implicit answer")
        self.post(rid, "claude", "Re P2: it holds, see the log.", to="user", kind="report")
        self.assertEqual(self.state(rid)["P2"], "delivered")


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

    def test_a_follow_up_is_what_the_fresh_session_is_told_to_answer(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Flaky test.", self.t0 + 2))
        points.sync(rid, force=True)
        self.send(rid, "Re P1: Which exact test, and fix it please?", at=self.t0 + 3)
        block = points.prompt_block(rid)
        self.assertIn("Why red?", block)
        self.assertIn("follow-up: Re P1: Which exact test, and fix it please?", block)
        self.assertIn("Which exact test", points.note_line(rid))
        self.assertIn("Which exact test", points.reminder_line(points.open_points(rid), time.time())[0])
        item = points.view(rid)["items"][0]
        self.assertEqual([f["text"] for f in item["follows"]], ["Re P1: Which exact test, and fix it please?"])
        # Answered again: the follow-up is no longer what is open.
        self.add("sid-1", turn("user", "Re P1: Which exact test, and fix it please?\n\n[point P1]", self.t0 + 4),
                 turn("assistant", "test_x, fixed.", self.t0 + 5))
        points.sync(rid, force=True)
        self.assertEqual(points.follow_up(self.point(rid, "P1")), "")

    def test_every_unanswered_follow_up_reaches_the_fresh_session(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Flaky test.", self.t0 + 2))
        points.sync(rid, force=True)
        self.send(rid, "Re P1: Which exact test?", at=self.t0 + 3)
        self.send(rid, "Re P1: Also check Windows please.", at=self.t0 + 4)
        block = points.prompt_block(rid)
        self.assertIn("Which exact test?", block)
        self.assertIn("Also check Windows please.", block)
        self.assertEqual(points.follow_ups(self.point(rid, "P1")),
                         ["Re P1: Which exact test?", "Re P1: Also check Windows please."])

    def test_no_points_no_block(self):
        rid = self.solo_room()
        self.assertEqual(points.prompt_block(rid), "")
        self.assertEqual(points.note_line(rid), "")


class _Idle(_World):
    """An agent that is live and idle unless a test says otherwise."""

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


class Reminder(_Idle):
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

    def test_a_paused_room_is_not_typed_into(self):
        rid = self.solo_room()
        self.send(rid, "Is the backup running?", at=time.time() - 30 * 60)
        room = chatroom.get_room(rid, public=False)
        room["status"] = "paused"
        chatroom.update_room(room)
        self.assertEqual(points.tick(), [])
        self.assertEqual(self.pty.typed, [])

    def test_typed_once_even_when_it_cannot_be_recorded(self):
        rid = self.solo_room()
        self.send(rid, "Is the backup running?", at=time.time() - 30 * 60)
        with mock.patch.object(points, "_save", side_effect=OSError("locked")):
            self.assertEqual(points.tick(), ["P1"])
            self.assertEqual(points.tick(), [])
        self.assertEqual(len(self.pty.typed), 1)

    def test_one_rooms_failure_does_not_end_the_tick(self):
        bad = self.solo_room("sid-bad")
        good = self.solo_room()
        self.send(bad, "x", at=time.time() - 30 * 60)
        self.send(good, "y", at=time.time() - 30 * 60)
        real = points.load

        def load(r):
            if r == bad:
                raise OSError("no")
            return real(r)
        with mock.patch.object(points, "load", side_effect=load):
            self.assertEqual(points.tick(), ["P1"])

    def test_a_deleted_rooms_ledger_is_not_read_again(self):
        rid = self.solo_room()
        self.send(rid, "x", at=time.time() - 30 * 60)
        with mock.patch.object(chatroom, "get_room", return_value=None), \
                mock.patch.object(points, "load", wraps=points.load) as load:
            self.assertEqual(points.tick(), [])
            self.assertEqual(points.tick(), [])
        load.assert_not_called()
        self.assertIn(rid, points._GONE)

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


    def test_a_text_before_a_tool_call_is_interim_the_reply_that_ends_the_turn_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jsonl"
            lines = [
                {"type": "user", "message": {"role": "user", "content": "hello"}, "timestamp": "2026-09-21T10:00:00Z"},
                {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                                                  "content": [{"type": "text", "text": "Reading the log."}]},
                 "timestamp": "2026-09-21T10:00:01Z"},
                {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use",
                                                  "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]},
                 "timestamp": "2026-09-21T10:00:02Z"},
                {"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn",
                                                  "content": [{"type": "text", "text": "Nothing in it."}]},
                 "timestamp": "2026-09-21T10:00:03Z"},
                {"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": "no stop reason"}]},
                 "timestamp": "2026-09-21T10:00:04Z"},
            ]
            p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            turns = dashboard._claude_text_turns(p)
        self.assertEqual([(t["text"], t.get("interim")) for t in turns],
                         [("hello", None), ("Reading the log.", True), ("Nothing in it.", None), ("no stop reason", None)])

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
            self.assertEqual((r["points"]["open"], r["points"]["delivered"]), (0, 0))
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

    def test_a_refused_follow_up_leaves_the_point_as_it_was(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Flaky.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        before = self.point(rid, "P1")
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("handover")):
            status, r = http("/api/room/resume", {"roomId": rid, "text": "Re P1: which test?", "key": "k2"})
        self.assertEqual((status, r.get("kept")), (400, False))
        p = self.point(rid, "P1")
        self.assertEqual((p["state"], p["stateAt"], p.get("follows")), ("delivered", before["stateAt"], []))
        # A refused new point goes; the one before it stays.
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("no")):
            http("/api/room/resume", {"roomId": rid, "text": "Another thing", "key": "k3"})
        self.assertEqual(self.state(rid), {"P1": "delivered"})

    def test_a_discarded_held_send_takes_its_points_back(self):
        rid = self.solo_room()
        self.send(rid, "Old question", at=self.t0)
        out, ids = self.send(rid, "New question", key="k1", at=self.t0 + 5)
        fol, _ = self.send(rid, "Re P1: and also this", key="k2", at=self.t0 + 6)
        res = dashboard._Resume()
        res.queue = [{"text": out, "key": "k1", "at": 0}, {"text": fol, "key": "k2", "at": 0}]
        res.fail("no")
        self.addCleanup(dashboard._RESUMES.pop, rid, None)
        dashboard._RESUMES[rid] = res
        self.assertEqual(ids, ["P2"])
        self.assertTrue(dashboard.discard_pending(rid))
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.assertEqual(self.point(rid, "P1").get("follows"), [])
        self.assertEqual(points.counts(rid), {"open": 1, "planned": 0, "delivered": 0})

    def hold(self, rid, *items):
        res = dashboard._Resume()
        res.queue = [dict(it, at=0) for it in items]
        res.fail("no")
        self.addCleanup(dashboard._RESUMES.pop, rid, None)
        dashboard._RESUMES[rid] = res

    def test_discarding_several_held_follow_ups_undoes_each(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Why red?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Flaky.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        before = self.point(rid, "P1")["stateAt"]
        a, _ = self.send(rid, "Re P1: which one?", key="k1", at=self.t0 + 3)
        b, _ = self.send(rid, "Re P1: and on Windows?", key="k2", at=self.t0 + 4)
        self.hold(rid, {"text": a, "key": "k1"}, {"text": b, "key": "k2"})
        status, _ = http("/api/room/resume", {"roomId": rid, "discard": True})
        self.assertEqual(status, 200)
        p = self.point(rid, "P1")
        self.assertEqual((p["state"], p["stateAt"], p.get("follows"), p.get("undo")),
                         ("delivered", before, [], None))

    def test_discarding_a_held_point_and_its_held_follow_up_removes_both(self):
        rid = self.solo_room()
        a, _ = self.send(rid, "New thing", key="k1", at=self.t0)
        b, _ = self.send(rid, "Re P1: more on it", key="k2", at=self.t0 + 1)
        self.hold(rid, {"text": a, "key": "k1"}, {"text": b, "key": "k2"})
        self.assertTrue(dashboard.discard_pending(rid))
        self.assertEqual(points.load(rid)["points"], [])

    def test_discard_keeps_a_point_already_posted_in_a_team_chat(self):
        rid = self.team_room()
        out, _ = self.send(rid, "Is it merged?", key="k1", at=self.t0)
        chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, out)
        # Posted, only its wake failed: held with "posted".
        self.hold(rid, {"text": out, "key": "k1", "to": "", "posted": True, "wake": ["claude"]})
        status, _ = http("/api/room/resume", {"roomId": rid, "discard": True})
        self.assertEqual(status, 200)
        self.assertEqual(self.state(rid), {"P1": "open"})

    def test_a_held_approval_discarded_can_be_given_again(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Which way?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("assistant", "Decision needed: A or B?\n\nI recommend A.", self.t0 + 2))
        points.sync(rid, force=True)
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="": {"queued": 1, "delivered": 0}):
            self.assertEqual(http("/api/room/approve", {"roomId": rid, "mid": "sid-1:1"})[0], 200)
        self.assertEqual(self.state(rid), {"P1": "acked"})
        # Its delivery failed; the person discards what was held.
        self.hold(rid, {"text": "Approved: go with your recommendation.", "key": "approve:sid-1:1"})
        self.assertTrue(dashboard.discard_pending(rid))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        sent = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               lambda h, room, text="", to="", key="": sent.append(key) or {"delivered": 1}):
            status, r = http("/api/room/approve", {"roomId": rid, "mid": "sid-1:1"})
        self.assertEqual((status, r.get("duplicate"), sent), (200, None, ["approve:sid-1:1"]))

    def test_a_refused_approval_leaves_its_point_unacknowledged(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Which way?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("assistant", "Decision needed: A or B?\n\nI recommend A.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        with mock.patch.object(dashboard.Handler, "_resume_room", side_effect=dashboard.StartRoomError("no")):
            self.assertEqual(http("/api/room/approve", {"roomId": rid, "mid": "sid-1:1"})[0], 400)
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        self.assertNotIn("ackedBy", self.point(rid, "P1"))

    def test_a_ledger_that_cannot_be_written_is_answered_500(self):
        rid = self.solo_room()
        self.send(rid, "Q?")
        with mock.patch.object(points, "_save", side_effect=OSError("locked")):
            status, r = http("/api/room/points", {"roomId": rid, "id": "P1", "action": "drop"})
            self.assertEqual((status, r["error"]), (500, "not_saved"))
            status, r = http("/api/room/approve", {"roomId": rid, "mid": "sid-1:9"})
            self.assertEqual((status, r["error"]), (500, "not_saved"))

    def test_the_room_poll_carries_the_points(self):
        rid = self.solo_room()
        self.send(rid, "Q?")
        room = dashboard._annotate_room_liveness(chatroom.get_room(rid), with_points=True)
        self.assertEqual((room["points"]["open"], [i["id"] for i in room["points"]["items"]]), (1, ["P1"]))
        self.assertEqual(points.counts(rid), {"open": 1, "planned": 0, "delivered": 0})


class Stages(_World):
    """A point follows the work: open → planned (a plan, its task linked) →
    delivered (said live) → acked. A plan is never what the person acks."""

    def test_plan_marks(self):
        self.assertEqual(points.re_marks("Re P12 (planned #104): started"),
                         {"P12": {"plan": True, "task": "#104"}})
        self.assertEqual(points.re_marks("**Re P7** (plan ED-7): queued\n\nRe P3 (planned): later"),
                         {"P7": {"plan": True, "task": "ED-7"}, "P3": {"plan": True, "task": ""}})
        self.assertEqual(points.re_marks("Re P1 (planned #5): a\n\nRe P1: it is live"),
                         {"P1": {"plan": False, "task": ""}}, "a plain Re Pn: in the same reply wins")
        self.assertEqual(points.re_marks("Re P2, P4 (planned #9): both"),
                         {"P2": {"plan": True, "task": "#9"}, "P4": {"plan": True, "task": "#9"}})
        self.assertEqual((points.task_ref("ed-0104"), points.task_ref("104"), points.task_ref("x")),
                         ("ED-104", "#104", ""))

    def test_a_plan_is_in_progress_until_it_is_said_live(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Add a dark mode", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1),
                 turn("assistant", "Re P1 (planned #104): started as #104.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "planned"})
        p = self.point(rid, "P1")
        self.assertEqual((p["task"], [a.get("kind") for a in p["answers"]]), ("#104", ["plan"]))
        self.assertEqual(points.counts(rid), {"open": 0, "planned": 1, "delivered": 0})
        # Neither a click nor a thumbs up nor a "thanks" acks a plan.
        self.assertIsNone(points.act(rid, "P1", "ack"))
        self.assertTrue(points.approve(rid, "sid-1:1"))
        self.assertEqual(self.send(rid, "thanks", at=self.t0 + 3)[1], [])
        self.assertEqual(self.state(rid), {"P1": "planned"})
        # Later, in the same run, the work is live.
        self.add("sid-1", turn("assistant", "Re P1: dark mode is live after the restart.", self.t0 + 4))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        p = self.point(rid, "P1")
        self.assertEqual(([a.get("kind") for a in p["answers"]], p["task"]), (["plan", None], "#104"))
        item = points.view(rid)["items"][0]
        self.assertEqual((item["answers"][0]["kind"], item["task"]["ref"]), ("plan", "#104"))
        self.assertEqual(points.act(rid, "P1", "ack")["state"], "acked")

    def test_a_newer_plan_takes_a_delivered_point_back_and_a_follow_up_reopens_a_plan(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Fix the login", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Fixed.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        self.add("sid-1", turn("user", "[digest] 1 task moved", self.t0 + 3),
                 turn("assistant", "Re P1 (planned #7): the rest needs a task.", self.t0 + 4))
        self.assertEqual(self.state(rid), {"P1": "planned"})
        again, _ = self.send(rid, "Re P1: and the logout too?", at=self.t0 + 5)
        self.assertEqual(self.state(rid), {"P1": "open"})
        self.add("sid-1", turn("user", again, self.t0 + 6),
                 turn("assistant", "Re P1 (planned #7): folded into #7.", self.t0 + 7))
        self.assertEqual(self.state(rid), {"P1": "planned"})

    def test_approving_a_delivery_acks_it(self):
        rid = self.solo_room()
        out, _ = self.send(rid, "Use Postgres?", at=self.t0)
        self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "Yes: it is set up.", self.t0 + 2))
        self.assertEqual(self.state(rid), {"P1": "delivered"})
        self.assertTrue(points.approve(rid, "sid-1:1"))
        self.assertEqual(self.point(rid, "P1")["state"], "acked")

    def test_a_ledger_from_before_the_stages_loads(self):
        rid = self.solo_room()
        self.send(rid, "Old", at=self.t0)
        led = points.load(rid)
        led["points"][0]["state"] = "answered"
        points._path(rid).write_text(json.dumps(led), encoding="utf-8")
        points._CACHE.clear()
        self.assertEqual(points.load(rid)["points"][0]["state"], "delivered")

    def test_plan_by_tool(self):
        rid = self.solo_room()
        self.send(rid, "Add export", at=self.t0)
        ctx = {"room": chatroom.get_room(rid, public=False), "identity": "claude"}
        import ensemble_tools
        with mock.patch.object(ensemble_tools, "_d", dashboard):
            r = ensemble_tools._points(ctx, {"action": "plan", "point": "P1", "task": "ed-12"}, None)
            self.assertEqual(r, {"ok": True, "point": "P1", "state": "planned", "task": "ED-12"})
            self.assertEqual(self.point(rid, "P1")["answers"][0]["summary"], "claude: started as ED-12")
            with self.assertRaises(ensemble_tools.ToolError):
                ensemble_tools._points(ctx, {"action": "plan", "point": "P1", "task": "soon"}, None)
            r = ensemble_tools._points(ctx, {"action": "answer", "point": "P1", "summary": "live"}, None)
            self.assertEqual(r["state"], "delivered")

    def test_the_fresh_session_is_told_what_it_planned(self):
        rid = self.solo_room()
        self.send(rid, "Add export", at=self.t0)
        points.answer_by_tool(rid, "P1", "started", "claude", plan=True, task="#12")
        block = points.prompt_block(rid)
        self.assertTrue(block.startswith("In progress, planned and not yet said live: P1 (#12) \"Add export\""))
        self.assertIn("never ask sam to acknowledge a plan", block)
        self.send(rid, "And import", at=self.t0 + 1)
        block = points.prompt_block(rid)
        self.assertIn("- P2 (since", block)
        self.assertNotIn("- P1", block)
        self.assertTrue(block.endswith("never ask sam to acknowledge a plan."))


class DoneReminder(_Idle):
    def test_a_plan_whose_task_is_done_is_reminded_once(self):
        rid = self.solo_room()
        now = time.time()
        self.send(rid, "Add export", at=now - 5 * 3600)
        points.act(rid, "P1", "ack")        # an open one is reminded on its own clock
        points.act(rid, "P1", "reopen", now=now)
        points.answer_by_tool(rid, "P1", "started", "claude", plan=True, task="#12")
        task = {"ref": "#12", "done": False, "doneAt": None}
        with mock.patch.object(points, "task_lookup", return_value=lambda ref: dict(task) if ref == "#12" else None):
            self.assertEqual(points.tick(now), [], "not Done yet")
            task.update(done=True, doneAt=now - 60 * 60)
            self.assertEqual(points.tick(now), [], "Done for an hour")
            self.assertEqual(points.tick(now + 61 * 60), ["P1"])
            line = self.pty.typed[-1]
            self.assertTrue(line.startswith('[points] planned, its task Done, not yet said live: P1 "Add export" '
                                            '(#12 Done since '))
            self.assertEqual(dashboard.hub_input_kind(line)["kind"], "points")
            self.assertEqual(points.tick(now + 300 * 60), [])
            self.assertEqual(len(self.pty.typed), 1)

    def test_a_done_task_without_its_time_counts_from_when_it_was_seen(self):
        rid = self.solo_room()
        now = time.time()
        self.send(rid, "Add export", at=now)
        points.answer_by_tool(rid, "P1", "started", "claude", plan=True, task="#12")
        with mock.patch.object(points, "task_lookup",
                               return_value=lambda ref: {"ref": ref, "done": True, "doneAt": None}):
            self.assertEqual(points.tick(now), [])
            self.assertEqual(self.point(rid, "P1")["doneSeenAt"], now)
            self.assertEqual(points.tick(now + 119 * 60), [])
            self.assertEqual(points.tick(now + 121 * 60), ["P1"])


if __name__ == "__main__":
    unittest.main()
