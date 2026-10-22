"""#256 step D2: a task Done for 14 days has its chat packed into one gzip of
its log; the room file says so, and search reads its last reports; the log or
room file it came from is kept with the originals until the pack is verified.
A packed chat opens whole, and the first write after it writes the log again."""
import gzip
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom

DAY = 86400


class Packs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self._patch("ROOMS_DIR", self.path)
        self.veto = []
        self._patch("ARCHIVE_VETO", self.veto)
        self._patch("CARD_FACTS", [])
        for cache in (chatroom._INDEX, chatroom._CHANGES, chatroom._TOKEN_ROOMS, chatroom._LOGGED,
                      chatroom._DAMAGED, chatroom._CARDS, chatroom._FULL, chatroom._PACKED):
            cache.clear()
            self.addCleanup(cache.clear)
        chatroom._CARDS_STATE.update(dir="", dirty=False, saved=0.0, swept=0.0)
        self.addCleanup(chatroom._CARDS_STATE.update, dir="", dirty=False, saved=0.0, swept=0.0)
        self.now = time.time()

    def _patch(self, name, value):
        p = mock.patch.object(chatroom, name, value)
        p.start()
        self.addCleanup(p.stop)

    def later(self, days):
        self._patch("_now", lambda: self.now + days * DAY)

    def restart(self):
        for cache in (chatroom._INDEX, chatroom._LOGGED, chatroom._FULL, chatroom._PACKED, chatroom._DAMAGED):
            cache.clear()
        chatroom._CARDS_STATE["dir"] = ""

    def done_room(self, texts=("one", "two")):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        for t in texts:
            chatroom.post_message(rid, "claude", t)
        chatroom.record_report(rid, "claude", "completed", "all done")
        chatroom.patch_room(rid, workflow="done", workflowAt=self.now, updatedAt=self.now, status="active")
        return rid

    def old_room(self, texts=("one", "two")):
        """A room file from before the message log: its messages inside it."""
        rid = "room-0ld0ld01"
        msgs = [{"id": f"m{k}", "from": "claude", "to": "", "text": t, "ts": self.now - 100 + k}
                for k, t in enumerate(texts)]
        room = {"id": rid, "title": "old", "participants": [], "messages": msgs, "workflow": "done",
                "workflowAt": self.now, "updatedAt": self.now, "status": "active"}
        (self.path / f"{rid}.json").write_text(json.dumps(room), encoding="utf-8")
        return rid

    def texts(self, rid):
        return [m["text"] for m in chatroom.get_room(rid)["messages"]]

    def pack(self, rid):
        return chatroom.pack_room(rid)

    def file(self, rid):
        return json.loads((self.path / f"{rid}.json").read_text(encoding="utf-8"))

    def test_a_chat_done_for_14_days_is_packed_and_opens_whole(self):
        rid = self.done_room()
        before = self.texts(rid)
        self.later(15)
        self.assertTrue(self.pack(rid))
        pack = self.path / "messages" / f"{rid}.jsonl.gz"
        self.assertTrue(pack.exists())
        self.assertFalse((self.path / "messages" / f"{rid}.jsonl").exists())
        self.assertTrue((self.path / "before-message-log" / f"{rid}.jsonl").exists())
        log = self.file(rid)["messageLog"]
        self.assertEqual((log["count"], log["packed"]), (3, True))
        self.assertNotIn("reports", log)
        self.assertEqual(self.texts(rid), before)
        self.restart()
        self.assertEqual(self.texts(rid), before)
        self.assertFalse(self.pack(rid), "packed once")

    def test_a_room_file_from_before_the_log_is_packed_and_kept(self):
        rid = self.old_room()
        raw = (self.path / f"{rid}.json").read_bytes()
        self.later(15)
        self.assertTrue(self.pack(rid))
        self.assertEqual((self.path / "before-message-log" / f"{rid}.json").read_bytes(), raw)
        self.assertNotIn("messages", self.file(rid))
        self.assertEqual(self.texts(rid), ["one", "two"])

    def test_not_packed_before_14_days_or_while_kept_whole(self):
        rid = self.done_room()
        self.later(13)
        self.assertFalse(self.pack(rid))
        self.later(15)
        self.veto.append(lambda room: True)
        self.assertFalse(self.pack(rid))
        self.veto.clear()
        chatroom.patch_room(rid, status="waiting_human", updatedAt=self.now)
        self.assertFalse(self.pack(rid))
        self.assertTrue((self.path / "messages" / f"{rid}.jsonl").exists())

    def test_a_write_after_the_pack_writes_the_log_again(self):
        rid = self.done_room()
        self.later(15)
        self.pack(rid)
        self.restart()
        chatroom.post_message(rid, "claude", "three")
        self.assertFalse((self.path / "messages" / f"{rid}.jsonl.gz").exists())
        self.assertNotIn("packed", self.file(rid)["messageLog"])
        self.assertEqual(self.texts(rid), ["one", "two", "all done", "three"])
        self.restart()
        self.assertEqual(self.texts(rid), ["one", "two", "all done", "three"])
        chatroom.post_message(rid, "claude", "four")      # appended to the log again
        self.assertIn(rid, chatroom._LOGGED)
        self.assertEqual(self.texts(rid)[-1], "four")

    def test_search_reads_the_reports_not_the_chat(self):
        rid = self.done_room()
        self.later(15)
        self.pack(rid)
        room = chatroom.read_unpacked(rid)
        self.assertEqual([m["text"] for m in room["messages"]], ["all done"])
        self.assertEqual(room["title"], "t")
        live = self.done_room()
        self.assertEqual(len(chatroom.read_unpacked(live)["messages"]), 3)

    def test_a_damaged_pack_reads_no_room_and_takes_no_write(self):
        rid = self.done_room()
        self.later(15)
        self.pack(rid)
        self.restart()
        (self.path / "messages" / f"{rid}.jsonl.gz").write_bytes(b"not a gzip")
        self.assertIsNone(chatroom.get_room(rid))
        self.assertIsNone(chatroom.post_message(rid, "claude", "lost?"))
        self.assertTrue(self.file(rid)["messageLog"]["packed"], "the room file is as it was")

    def test_a_pack_that_does_not_read_back_is_not_kept(self):
        rid = self.done_room()
        self.later(15)
        with mock.patch.object(chatroom.gzip, "decompress", return_value=b"other"):
            self.assertFalse(self.pack(rid))
        self.assertFalse((self.path / "messages" / f"{rid}.jsonl.gz").exists())
        self.assertTrue((self.path / "messages" / f"{rid}.jsonl").exists())
        self.assertNotIn("packed", self.file(rid)["messageLog"])

    def test_the_originals_go_once_the_pack_is_verified(self):
        rid = self.done_room()
        old = self.old_room()
        self.later(15)
        self.pack(rid)
        self.pack(old)
        kept = self.path / "before-message-log"
        self.assertEqual(chatroom.prune_originals(self.now + DAY), 0, "kept 14 days from the pack")
        self.restart()
        # A pack missing a message keeps its original.
        pack = self.path / "messages" / f"{rid}.jsonl.gz"
        lines = gzip.decompress(pack.read_bytes()).splitlines(keepends=True)
        pack.write_bytes(gzip.compress(b"".join(lines[1:])))
        self.assertEqual(chatroom.prune_originals(self.now + 30 * DAY), 1)
        self.assertTrue((kept / f"{rid}.jsonl").exists())
        self.assertFalse((kept / f"{old}.json").exists())

    def test_the_hub_packs_its_finished_chats(self):
        due = self.done_room()
        fresh = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        self.later(15)
        self.restart()
        chatroom.room_index()
        chatroom.patch_room(fresh, workflow="done", workflowAt=self.now + 14 * DAY, updatedAt=self.now + 14 * DAY)
        self.restart()
        self.later(16)
        chatroom.room_index()
        self.assertEqual(chatroom.pack_chats(), 1)
        self.assertTrue((self.path / "messages" / f"{due}.jsonl.gz").exists())
        self.assertTrue(chatroom._INDEX[due].get("cold"))
        self.assertEqual(chatroom.pack_chats(), 0)
        with mock.patch.object(chatroom, "_HOME_ROOMS", self.path):
            self.assertEqual(chatroom.pack_chats(), 0, "a test never packs the live rooms")


if __name__ == "__main__":
    unittest.main()
