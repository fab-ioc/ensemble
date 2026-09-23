"""A link to a chat balloon, pasted into another chat, reaches the agent with the
referenced message written out under it (message_refs.expand_message_refs), and
the hub finds that message in a room's messages or in a solo chat's transcript
(dashboard.resolve_message_ref).
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import message_refs as mr

TS = 1788975798.0
WHEN = time.strftime("%Y-%m-%d %H:%M", time.localtime(TS))
URL = "http://hub-host:8765/session?room=room-1a2b3c4d&msg=0123456789ab"
URL2 = "http://127.0.0.1:8765/session?room=room-cafe0001&msg=abcdefabcdef"
MSGS = {
    ("room-1a2b3c4d", "0123456789ab"): {"who": "claude", "taskTitle": "Docs", "ts": TS,
                                        "text": "Merged.\nTests pass.", "id": "0123456789ab"},
    ("room-cafe0001", "abcdefabcdef"): {"who": "sam", "taskTitle": "PO", "ts": TS,
                                        "text": "Ship it", "id": "abcdefabcdef"},
}


def lookup(room, msg):
    return MSGS.get((room, msg))


class ExpandTest(unittest.TestCase):
    def test_no_link_is_unchanged(self):
        for t in ("plain words", "http://hub-host:8765/?task=room-1a2b3c4d",
                  "http://h/session?room=room-1a2b3c4d", "http://h/fileview?room=r&msg=m", ""):
            self.assertEqual(mr.expand_message_refs(t, lookup), t)

    def test_one_link(self):
        out = mr.expand_message_refs(f"Look at {URL} please", lookup)
        self.assertEqual(out, f"Look at {URL} please\n\n"
                              f"[ref {URL}] from claude in \"Docs\" at {WHEN}:\n"
                              "> Merged.\n> Tests pass.")

    def test_two_links_in_order_and_a_repeat_once(self):
        out = mr.expand_message_refs(f"{URL2} and {URL}, again {URL}.", lookup)
        blocks = out.split("\n\n")[1:]
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0].startswith(f"[ref {URL2}] from sam in \"PO\" at {WHEN}:\n> Ship it"))
        self.assertTrue(blocks[1].startswith(f"[ref {URL}] from claude in \"Docs\""))

    def test_unknown_room_and_unknown_id(self):
        bad_room = "http://hub-host:8765/session?room=room-deadbeef&msg=0123456789ab"
        bad_id = "http://hub-host:8765/session?room=room-1a2b3c4d&msg=ffffffffffff"
        out = mr.expand_message_refs(f"{bad_room}\n{bad_id}", lookup)
        self.assertIn(f"[ref {bad_room}] not found: no message 0123456789ab in room-deadbeef on this hub", out)
        self.assertIn(f"[ref {bad_id}] not found: no message ffffffffffff in room-1a2b3c4d on this hub", out)
        self.assertTrue(out.startswith(f"{bad_room}\n{bad_id}\n\n"))

    def test_a_points_message_keeps_each_items_blocks_inside_it(self):
        # The chat editor's numbered points: an item's link is written out in
        # that item, above its images and its [point] line; the head's under
        # the head. Review comments have the same shape.
        text = (f"## Points (2)\n\nIntro {URL2}\n\n**1.** first {URL}\n[image] C:\\t\\attachments\\a.png\n\n[point P1]\n\n"
                f"**2.** second, plain\n\n[point P2]")
        out = mr.expand_message_refs(text, lookup)
        self.assertEqual(out, (
            f"## Points (2)\n\nIntro {URL2}\n\n[ref {URL2}] from sam in \"PO\" at {WHEN}:\n> Ship it\n\n"
            f"**1.** first {URL}\n\n[ref {URL}] from claude in \"Docs\" at {WHEN}:\n> Merged.\n> Tests pass.\n\n"
            f"[image] C:\\t\\attachments\\a.png\n\n[point P1]\n\n"
            f"**2.** second, plain\n\n[point P2]"))
        # Stripped again: the same message, an item's tail set off by a blank line.
        self.assertEqual(mr.strip_message_refs(out), text.replace("9ab\n[image]", "9ab\n\n[image]"))
        self.assertEqual(mr.split_items(out)[1][1], "**2.** second, plain\n\n[point P2]")
        # The head's own image (named under it by the editor) stays under
        # the head's block, above the first item.
        with_img = f"## Points (1)\n\nIntro {URL2}\n[image] C:\\t\\attachments\\h.png\n\n**1.** first\n\n[point P1]"
        out = mr.expand_message_refs(with_img, lookup)
        self.assertEqual(out, (f"## Points (1)\n\nIntro {URL2}\n\n[ref {URL2}] from sam in \"PO\" at {WHEN}:\n> Ship it\n\n"
                               f"[image] C:\\t\\attachments\\h.png\n\n**1.** first\n\n[point P1]"))
        self.assertEqual(mr.strip_message_refs(out), with_img.replace("\n[image]", "\n\n[image]"))
        # Head words that are a point of their own: their [point] line stays
        # at the head's end, under the ref block and the head's image.
        head_pt = f"## Points (1)\n\nIntro {URL2}\n[image] C:\\t\\attachments\\h.png\n\n[point P1]\n\n**1.** first\n\n[point P2]"
        out = mr.expand_message_refs(head_pt, lookup)
        self.assertEqual(out, (f"## Points (1)\n\nIntro {URL2}\n\n[ref {URL2}] from sam in \"PO\" at {WHEN}:\n> Ship it\n\n"
                               f"[image] C:\\t\\attachments\\h.png\n\n[point P1]\n\n**1.** first\n\n[point P2]"))
        self.assertEqual(mr.strip_message_refs(out), head_pt.replace("\n[image]", "\n\n[image]"))
        self.assertEqual(mr.with_images(f"## Points (1)\n\nIntro\n[image] h.png\n\n[point P1]\n\n**1.** first\n\n[point P2]",
                                        ["C:\\t\\attachments\\h.png"], ["h.png"]),
                         "## Points (1)\n\nIntro\n[image] C:\\t\\attachments\\h.png\n\n[point P1]\n\n**1.** first\n\n[point P2]")
        review = f"## Review comments (1)\n\n**1.** > quoted\n\nsee {URL}"
        self.assertTrue(mr.expand_message_refs(review, lookup).endswith(f"see {URL}\n\n[ref {URL}] from claude in \"Docs\" at {WHEN}:\n> Merged.\n> Tests pass."))
        plain = "## Points (1)\n\n**1.** nothing to expand\n\n[point P1]"
        self.assertIs(mr.expand_message_refs(plain, lookup), plain)
        self.assertIsNone(mr.split_items("## Points (1)\n\nno item line"))
        self.assertIsNone(mr.split_items("**1.** no head"))

    def test_a_lookup_that_raises_is_not_found(self):
        def boom(room, msg):
            raise OSError("disk")
        self.assertIn("not found", mr.expand_message_refs(URL, boom))

    def test_cap_at_4000_with_the_tail(self):
        long = {("room-1a2b3c4d", "0123456789ab"): {"who": "codex", "taskTitle": "Docs", "ts": TS,
                                                   "text": "x" * 4250, "id": "0123456789ab",
                                                   "where": "~/.ensemble/rooms/room-1a2b3c4d.json"}}
        out = mr.expand_message_refs(URL, lambda r, m: long.get((r, m)))
        lines = out.split("\n")
        self.assertEqual(lines[-2], "> " + "x" * 4000)
        self.assertEqual(lines[-1], "> … (250 more characters; the full message is in "
                                    "~/.ensemble/rooms/room-1a2b3c4d.json, id 0123456789ab)")

    def test_a_link_inside_markdown(self):
        for t in (f"**{URL}**", f"- see {URL}", f"[the merge]({URL})", f"<{URL}>", f"({URL})", f"_{URL}_"):
            out = mr.expand_message_refs(t, lookup)
            self.assertIn(f"[ref {URL}] from claude", out, t)
            self.assertTrue(out.startswith(t + "\n\n"), t)
        # A link in bold followed by the sentence's full stop.
        self.assertEqual(mr.find_message_refs(f"see **{URL}**.")[0][0], URL)

    def test_a_different_host_and_an_encoded_id(self):
        url = "https://my-mac.tailnet.example/session?msg=sess-1%3A12&room=room-1a2b3c4d"
        seen = []
        mr.expand_message_refs(url, lambda r, m: seen.append((r, m)))
        self.assertEqual(seen, [("room-1a2b3c4d", "sess-1:12")])

    def test_stripping_the_blocks_gives_the_words_back(self):
        t = f"Look at {URL}\n- and {URL2}"
        self.assertEqual(mr.strip_message_refs(mr.expand_message_refs(t, lookup)), t)
        self.assertEqual(mr.strip_message_refs("no refs\n\n> quoted"), "no refs\n\n> quoted")
        gone = "http://h/session?room=room-deadbeef&msg=0123456789ab"
        self.assertEqual(mr.strip_message_refs(mr.expand_message_refs(f"see {gone}", lookup)), f"see {gone}")

    def test_a_persons_own_ref_lines_stay(self):
        for t in (f"My own note\n\n[ref {URL}] this is ordinary text\n> keep this",
                  # The shape of a block, but its link is not in the words before it.
                  f"My own note\n\n[ref {URL}] from claude in \"Docs\" at {WHEN}:\n> keep this",
                  f"see {URL}\n\n[ref {URL}] from claude in \"Docs\" at yesterday:\n> keep this"):
            self.assertEqual(mr.strip_message_refs(t), t)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(chatroom, "ROOMS_DIR", Path(self.tmp.name)),
                  mock.patch.object(dashboard, "operator_name", lambda: "sam"),
                  mock.patch.object(dashboard, "load_projects", lambda: [{"poRoomId": "room-00000002"}])):
            p.start()
            self.addCleanup(p.stop)
        self.write({"id": "room-00000001", "title": "Docs (v2)", "participants": [
            {"identity": "claude", "kind": "agent"}], "messages": [
            {"id": "aaaaaaaaaaaa", "from": "user", "to": "", "text": "hello", "ts": TS},
            {"id": "bbbbbbbbbbbb", "from": "claude", "to": "user", "text": "done", "ts": TS + 60}]})
        self.write({"id": "room-00000002", "title": "PO task", "mode": "solo", "participants": [
            {"identity": "codex", "kind": "agent", "sessionId": "s-new",
             "rotations": [{"fromSessionId": "s-old", "toSessionId": "s-new"}]}], "messages": []})

    def write(self, room):
        (Path(self.tmp.name) / f"{room['id']}.json").write_text(json.dumps(room), encoding="utf-8")

    def test_a_room_message(self):
        r = dashboard.resolve_message_ref("room-00000001", "aaaaaaaaaaaa")
        self.assertEqual((r["who"], r["from"], r["taskTitle"], r["ts"], r["text"], r["isPo"]),
                         ("sam", "user", "Docs (v2)", TS, "hello", False))
        self.assertEqual(r["where"], "~/.ensemble/rooms/room-00000001.json")
        self.assertEqual(dashboard.resolve_message_ref("room-00000001", "bbbbbbbbbbbb")["who"], "claude")

    def test_unknown_room_id_and_bad_names(self):
        self.assertIsNone(dashboard.resolve_message_ref("room-00000009", "aaaaaaaaaaaa"))
        self.assertIsNone(dashboard.resolve_message_ref("room-00000001", "cccccccccccc"))
        self.assertIsNone(dashboard.resolve_message_ref("room-00000001", "task"))
        self.assertIsNone(dashboard.resolve_message_ref("../room-00000001", "aaaaaaaaaaaa"))

    def test_a_transcript_turn_counted_as_the_page_counts(self):
        turns = [{"role": "user", "text": "go", "timestamp": "2026-09-14T10:00:00Z", "kind": "human"},
                 {"role": "user", "text": "go", "timestamp": "2026-09-14T10:00:01Z", "kind": "human"},
                 {"role": "assistant", "text": "on it", "timestamp": "2026-09-14T10:00:05Z"},
                 {"role": "user", "text": f"see {URL}\n\n[ref {URL}] from x in \"y\" at 2026-09-14 10:00:\n> q",
                  "timestamp": "2026-09-14T10:01:00Z", "kind": "human"}]
        with mock.patch.object(dashboard, "read_session_turns", lambda sid: None if sid == "s-gone" else turns):
            r = dashboard.resolve_message_ref("room-00000002", "s-old:1")
            self.assertEqual((r["who"], r["taskTitle"], r["text"], r["isPo"]), ("codex", "PO", "on it", True))
            self.assertAlmostEqual(r["ts"], datetime(2026, 9, 14, 10, 0, 5, tzinfo=timezone.utc).timestamp())
            self.assertEqual(dashboard.resolve_message_ref("room-00000002", "s-old:2")["text"], f"see {URL}")
            self.assertEqual(dashboard.resolve_message_ref("room-00000002", "s-old:0")["who"], "sam")
            self.assertIsNone(dashboard.resolve_message_ref("room-00000002", "s-old:3"))
            self.assertIsNone(dashboard.resolve_message_ref("room-00000002", "s-gone:0"))
            self.assertEqual(dashboard.resolve_message_ref("room-00000002", "s-new:1")["text"], "on it")

    def test_a_transcript_of_another_session_is_not_this_rooms(self):
        self.write({"id": "room-00000003", "title": "Team", "mode": "collab", "participants": [
            {"identity": "claude", "kind": "agent", "sessionId": "s-team"},
            {"identity": "codex", "kind": "agent", "sessionId": "s-rev"}], "messages": []})
        turns = [{"role": "assistant", "text": "secret from another room", "timestamp": ""}]
        with mock.patch.object(dashboard, "read_session_turns", lambda sid: turns):
            # The transcript exists, but the room's agent never had that session.
            self.assertIsNone(dashboard.resolve_message_ref("room-00000002", "unrelated:0"))
            self.assertIsNone(dashboard.resolve_message_ref("room-00000001", "unrelated:0"))
            # A team chat shows room messages, not its agents' transcripts.
            self.assertIsNone(dashboard.resolve_message_ref("room-00000003", "s-team:0"))

    def test_the_person_and_the_po_get_links_written_out(self):
        room = {"id": "room-00000004", "title": "T", "participants": [
            {"identity": "po", "kind": "agent", "role": chatroom.PRODUCT_OWNER_ROLE},
            {"identity": "claude", "kind": "agent", "role": "engineer"},
            {"identity": "codex", "kind": "agent", "role": "reviewer"}], "messages": []}
        self.assertTrue(dashboard.refs_expanded_for(room, "user"))
        self.assertTrue(dashboard.refs_expanded_for(room, "po"))
        self.assertFalse(dashboard.refs_expanded_for(room, "claude"))
        url = "http://127.0.0.1:8765/session?room=room-00000001&msg=bbbbbbbbbbbb"
        for sender, expanded in (("po", True), ("user", True), ("claude", False)):
            msg = {"id": "cccccccccccc", "from": sender, "to": "codex", "text": f"@codex check {url}"}
            with mock.patch.object(dashboard, "review_log_path", lambda r: "REVIEW-LOG.md"):
                brief = dashboard.review_brief(room, room["participants"][2], msg, 1, {}, "")
            self.assertEqual(f"[ref {url}] from claude" in brief, expanded, sender)

    def test_with_message_refs_uses_the_hub_lookup(self):
        url = "http://127.0.0.1:8765/session?room=room-00000001&msg=bbbbbbbbbbbb"
        out = dashboard.with_message_refs(f"check {url}")
        self.assertEqual(out, f"check {url}\n\n[ref {url}] from claude in \"Docs (v2)\" at "
                              + time.strftime("%Y-%m-%d %H:%M", time.localtime(TS + 60)) + ":\n> done")


if __name__ == "__main__":
    unittest.main()
