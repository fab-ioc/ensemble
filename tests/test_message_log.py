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
        for cache in (chatroom._INDEX, chatroom._CHANGES, chatroom._TOKEN_ROOMS, chatroom._LOGGED, chatroom._DAMAGED):
            cache.clear()
            self.addCleanup(cache.clear)

    def restart(self):
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        chatroom._DAMAGED.clear()

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

    def test_a_post_after_a_cut_line_survives_a_restart(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        with open(self.path / "messages" / f"{rid}.jsonl", "ab") as f:
            f.write(b'{"id": "cut", "te')       # a stop mid-append
        self.restart()
        chatroom.post_message(rid, "claude", "two")
        self.restart()
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]][-2:], ["one", "two"])
        self.assertTrue(self.log_lines(rid)[-1].endswith(b"}"))

    def test_a_replaced_message_is_kept_on_disk(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "original")
        full = chatroom.get_room(rid, public=False)
        full["messages"][-1] = {**full["messages"][-1], "text": "edited"}
        chatroom.update_room(full)
        self.restart()
        self.assertEqual(chatroom._read(rid)["messages"][-1]["text"], "edited")

    def test_where_an_agent_is_told_the_messages_are(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        self.assertEqual(chatroom.messages_file(rid), f"~/.ensemble/rooms/messages/{rid}.jsonl")
        old = self.old_format(["a"])
        chatroom._read(old)
        self.assertEqual(chatroom.messages_file(old), f"~/.ensemble/rooms/{old}.json")

    def test_a_missing_log_of_counted_messages_makes_the_room_unreadable(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        (self.path / "messages" / f"{rid}.jsonl").unlink()
        self.restart()
        self.assertIsNone(chatroom._read(rid))
        self.assertIsNone(chatroom.post_message(rid, "claude", "two"))
        self.assertFalse((self.path / "messages" / f"{rid}.jsonl").exists())

    def test_a_room_with_no_messages_and_no_log_reads(self):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        (self.path / "messages" / f"{rid}.jsonl").unlink()
        self.restart()
        self.assertEqual(chatroom._read(rid)["messages"], [])
        chatroom.post_message(rid, "claude", "one")
        self.assertEqual([json.loads(x)["text"] for x in self.log_lines(rid)], ["one"])


class FailedReadsAndWrites(_Rooms):
    """The PO's pre-merge read of step A: a read or write that fails never
    costs a message, nor posts one twice."""

    def setUp(self):
        super().setUp()
        self.rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        for t in ("1", "2", "3", "4", "5"):
            chatroom.post_message(self.rid, "claude", t)

    def texts(self):
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        return [m["text"] for m in chatroom._read(self.rid)["messages"]]

    def log_texts(self):
        return [json.loads(x)["text"] for x in self.log_lines(self.rid)]

    def test_a_log_that_cannot_be_read_does_not_read_as_empty(self):
        log = self.path / "messages" / f"{self.rid}.jsonl"
        real = Path.read_bytes

        def locked(p):
            if p == log:
                raise PermissionError(32, "The process cannot access the file")
            return real(p)
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        with mock.patch.object(Path, "read_bytes", locked):
            self.assertIsNone(chatroom._read(self.rid))
            self.assertIsNone(chatroom.get_room(self.rid))
            self.assertIsNone(chatroom.post_message(self.rid, "claude", "after"))
        self.assertEqual(self.log_texts(), ["1", "2", "3", "4", "5"])
        chatroom.post_message(self.rid, "claude", "after")
        self.assertEqual(self.texts(), ["1", "2", "3", "4", "5", "after"])

    def test_a_rewrite_with_fewer_messages_than_counted_is_refused(self):
        room = chatroom._read(self.rid)
        room["messages"] = room["messages"][:1]
        chatroom._LOGGED.discard(self.rid)
        with self.assertRaises(RuntimeError):
            chatroom._write(room)
        self.assertEqual(self.log_texts(), ["1", "2", "3", "4", "5"])

    def test_a_withdraw_may_rewrite_with_one_less(self):
        m = chatroom.post_message(self.rid, chatroom.HUMAN_IDENTITY, "oops")["message"]
        self.assertTrue(chatroom.withdraw_message(self.rid, m["id"]))
        self.assertEqual(self.texts(), ["1", "2", "3", "4", "5"])

    def test_a_room_file_not_written_takes_its_appended_line_back(self):
        real = chatroom._replace
        room_file = self.path / f"{self.rid}.json"

        def busy(tmp, path):
            if path == room_file:
                raise PermissionError(5, "Access is denied")
            return real(tmp, path)
        with mock.patch.object(chatroom, "_replace", busy):
            with self.assertRaises(PermissionError):
                chatroom.post_message(self.rid, "claude", "six")
        self.assertEqual(self.log_texts(), ["1", "2", "3", "4", "5"])
        chatroom.post_message(self.rid, "claude", "six")     # the retry
        self.assertEqual(self.texts(), ["1", "2", "3", "4", "5", "six"])

    def test_a_room_file_not_written_after_a_rewrite_puts_the_log_back(self):
        m = chatroom.post_message(self.rid, chatroom.HUMAN_IDENTITY, "oops")["message"]
        real = chatroom._replace
        room_file = self.path / f"{self.rid}.json"

        def busy(tmp, path):
            if path == room_file:
                raise PermissionError(5, "Access is denied")
            return real(tmp, path)
        with mock.patch.object(chatroom, "_replace", busy):
            with self.assertRaises(PermissionError):
                chatroom.withdraw_message(self.rid, m["id"])
        self.assertEqual(self.log_texts(), ["1", "2", "3", "4", "5", "oops"])
        self.assertEqual(self.texts(), ["1", "2", "3", "4", "5", "oops"])

    def test_an_append_that_fails_is_cut_back_and_the_next_write_rewrites(self):
        def half(path, msgs):
            with open(path, "ab") as f:
                f.write(b'{"id": "half", "te')
            raise OSError(28, "No space left on device")
        with mock.patch.object(chatroom, "_append_lines", half):
            with self.assertRaises(OSError):
                chatroom.post_message(self.rid, "claude", "six")
        self.assertNotIn(self.rid, chatroom._LOGGED)
        self.assertEqual(self.log_texts(), ["1", "2", "3", "4", "5"])
        chatroom.post_message(self.rid, "claude", "six")
        self.assertEqual(self.texts(), ["1", "2", "3", "4", "5", "six"])

    def test_a_damaged_middle_line_is_kept_aside_when_the_log_is_rewritten(self):
        log = self.path / "messages" / f"{self.rid}.jsonl"
        lines = log.read_bytes().splitlines(keepends=True)
        lines[2] = b'{"broken\n'
        log.write_bytes(b"".join(lines))
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        chatroom.post_message(self.rid, "claude", "six")
        self.assertEqual(len(list(log.parent.glob(f"{log.name}.damaged-*"))), 1)
        self.assertEqual(self.texts()[-1], "six")


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

    def test_a_downgraded_hubs_messages_join_the_log(self):
        # A hub from before the log keeps "messageLog" and writes the one
        # message it posted inline: the logged ones are not lost.
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "a")
        chatroom.post_message(rid, "claude", "b")
        disk = self.room_file(rid)
        disk["messages"] = [{"id": "old-hub", "from": "claude", "to": "", "text": "c", "ts": 9e9}]
        (self.path / f"{rid}.json").write_text(json.dumps(disk), encoding="utf-8")
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]][-3:], ["a", "b", "c"])
        chatroom.post_message(rid, "claude", "d")
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        self.assertEqual([m["text"] for m in chatroom._read(rid)["messages"]][-4:], ["a", "b", "c", "d"])
        self.assertNotIn("messages", self.room_file(rid))

    def test_an_original_cut_short_is_copied_again(self):
        rid = self.old_format(["a", "b"])
        original = (self.path / f"{rid}.json").read_bytes()
        keep = self.path / "before-message-log" / f"{rid}.json"
        keep.parent.mkdir()
        keep.write_bytes(original[:20])         # a stop mid-copy
        chatroom.post_message(rid, "claude", "c")
        self.assertEqual(keep.read_bytes(), original)

    def test_verified_originals_are_pruned_after_two_weeks(self):
        import os
        import time
        done = self.old_format(["a", "b"])
        chatroom.post_message(done, "claude", "c")
        short = self.old_format(["x", "y"])
        chatroom.post_message(short, "claude", "z")
        log = self.path / "messages" / f"{short}.jsonl"
        log.write_bytes(b"".join(log.read_bytes().splitlines(keepends=True)[1:]))   # "x" lost
        young = self.old_format(["p"])
        chatroom.post_message(young, "claude", "q")
        keep = self.path / "before-message-log"
        old = time.time() - chatroom.ORIGINALS_KEEP_S - 60
        for rid in (done, short):
            os.utime(keep / f"{rid}.json", (old, old))
        self.restart()
        self.assertEqual(chatroom.prune_originals(), 1)
        self.assertFalse((keep / f"{done}.json").exists())
        self.assertTrue((keep / f"{short}.json").exists())
        self.assertTrue((keep / f"{young}.json").exists())

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
