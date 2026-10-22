"""#256 step A: a room's messages live in an append-only log beside its file.

A post appends one line; a message taken back writes the log again; a room
file from before keeps its messages until its first write, which keeps a copy
of the original. A damaged or missing log never stops the room from reading."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chatroom


class _Rooms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        p = mock.patch.object(chatroom, "ROOMS_DIR", self.path)
        p.start()
        self.addCleanup(p.stop)
        for cache in (chatroom._INDEX, chatroom._CHANGES, chatroom._TOKEN_ROOMS, chatroom._LOGGED):
            cache.clear()
            self.addCleanup(cache.clear)

    def room_file(self, rid):
        return json.loads((self.path / f"{rid}.json").read_text(encoding="utf-8"))

    def log_lines(self, rid):
        return (self.path / "messages" / f"{rid}.jsonl").read_bytes().splitlines()

    def old_format(self, texts):
        """A room file as hubs before step A wrote it: the messages inside."""
        room = chatroom.create_room("t", [{"agent": "claude"}])
        rid = room["id"]
        disk = self.room_file(rid)
        disk.pop("messageLog", None)
        disk["messages"] = [{"id": f"m{i}", "from": "user", "to": "", "text": t, "ts": float(i + 1)}
                            for i, t in enumerate(texts)]
        (self.path / f"{rid}.json").write_text(json.dumps(disk), encoding="utf-8")
        (self.path / "messages" / f"{rid}.jsonl").unlink()
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        return rid


class MessageLog(_Rooms):
    def test_a_post_appends_one_line_and_the_room_file_has_no_messages(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        before = self.log_lines(rid)
        chatroom.post_message(rid, "claude", "two")
        after = self.log_lines(rid)
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(json.loads(after[-1])["text"], "two")
        disk = self.room_file(rid)
        self.assertNotIn("messages", disk)
        self.assertEqual(disk["messageLog"], {"count": len(after)})
        chatroom._INDEX.clear()
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]][-2:], ["one", "two"])

    def test_a_post_does_not_rewrite_the_log(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        with mock.patch.object(chatroom.durable, "write_tmp", wraps=chatroom.durable.write_tmp) as w:
            chatroom.post_message(rid, "claude", "two")
        self.assertFalse(any(str(c.args[0]).endswith(".jsonl.tmp") for c in w.call_args_list))

    def test_a_withdrawn_message_leaves_the_log(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        keep = chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, "keep")["message"]
        gone = chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, "oops")["message"]
        self.assertTrue(chatroom.withdraw_message(rid, gone["id"]))
        ids = [json.loads(x)["id"] for x in self.log_lines(rid)]
        self.assertIn(keep["id"], ids)
        self.assertNotIn(gone["id"], ids)
        chatroom._INDEX.clear()
        self.assertNotIn(gone["id"], [m["id"] for m in chatroom._read(rid)["messages"]])

    def test_a_damaged_line_is_skipped(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        with open(self.path / "messages" / f"{rid}.jsonl", "ab") as f:
            f.write(b'{"id": "cut", "te')       # a stop mid-append
        chatroom._INDEX.clear()
        msgs = chatroom._read(rid)["messages"]
        self.assertEqual(msgs[-1]["text"], "one")
        self.assertNotIn("cut", [m.get("id") for m in msgs])

    def test_a_missing_log_reads_as_no_messages(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        (self.path / "messages" / f"{rid}.jsonl").unlink()
        chatroom._INDEX.clear()
        room = chatroom._read(rid)
        self.assertEqual(room["messages"], [])
        self.assertNotIn("messageLog", room)
        self.assertIsNotNone(chatroom.post_message(rid, "claude", "two"))
        self.assertEqual([json.loads(x)["text"] for x in self.log_lines(rid)], ["two"])


class OldFormat(_Rooms):
    def test_an_old_room_reads_and_its_first_write_converts_it(self):
        rid = self.old_format(["a", "b"])
        original = (self.path / f"{rid}.json").read_bytes()
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]], ["a", "b"])
        chatroom.post_message(rid, "claude", "c")
        self.assertNotIn("messages", self.room_file(rid))
        self.assertEqual([json.loads(x)["text"] for x in self.log_lines(rid)], ["a", "b", "c"])
        kept = self.path / "before-message-log" / f"{rid}.json"
        self.assertEqual(kept.read_bytes(), original)
        # A later write leaves the kept original alone.
        chatroom.post_message(rid, "claude", "d")
        self.assertEqual(kept.read_bytes(), original)

    def test_half_migrated_uses_the_room_files_messages(self):
        # A log is there, but the room file still holds its messages (a stop
        # after the log was written, before the room file was replaced).
        rid = self.old_format(["a", "b"])
        (self.path / "messages" / f"{rid}.jsonl").write_text(
            json.dumps({"id": "stale", "text": "stale"}) + "\n", encoding="utf-8")
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]], ["a", "b"])
        chatroom.post_message(rid, "claude", "c")
        self.assertEqual([json.loads(x)["text"] for x in self.log_lines(rid)], ["a", "b", "c"])

    def test_a_deleted_room_takes_its_log(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        self.assertTrue(chatroom.delete_room(rid))
        self.assertFalse((self.path / "messages" / f"{rid}.jsonl").exists())


class Search(_Rooms):
    def test_search_finds_messages_in_the_log(self):
        import global_search
        global_search._ROOM_CACHE.clear()
        self.addCleanup(global_search._ROOM_CACHE.clear)
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "a zebra crossing")
        entries = global_search.room_entries(self.path, None, chatroom._read)
        self.assertEqual([m["text"] for m in entries[0]["msgs"]], ["a zebra crossing"])
        chatroom.post_message(rid, "claude", "another")
        entries = global_search.room_entries(self.path, None, chatroom._read)
        self.assertEqual(len(entries[0]["msgs"]), 2)


class LoadCopy(_Rooms):
    def test_load_is_independent_of_the_index_but_shares_messages(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        chatroom.get_room(rid)
        with mock.patch.object(chatroom, "_read", wraps=chatroom._read) as read:
            room = chatroom._load(rid)
            self.assertEqual(read.call_count, 0)
        indexed = chatroom._INDEX[rid]["room"]
        room["title"] = "changed"
        room.setdefault("reads", {})["claude"] = 99.0
        room["messages"].append({"id": "x"})
        self.assertEqual(indexed["title"], "t")
        self.assertNotEqual((indexed.get("reads") or {}).get("claude"), 99.0)
        self.assertNotIn("x", [m.get("id") for m in indexed["messages"]])
        self.assertIs(room["messages"][0], indexed["messages"][0])

    def test_load_reads_the_file_after_an_outside_change(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.get_room(rid)
        disk = self.room_file(rid)
        disk["title"] = "edited"
        (self.path / f"{rid}.json").write_text(json.dumps(disk) + " ", encoding="utf-8")
        self.assertEqual(chatroom._load(rid)["title"], "edited")


if __name__ == "__main__":
    unittest.main()
