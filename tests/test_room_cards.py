"""#256 step C: a task Done for a day is held as a card, its record without
its messages; the room's file and log stay as they are and are read whole
only when the task is opened or changed. The cards are kept in a file, used
only while the room file is the one they were made from."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom

DAY = 86400


class Cards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self._patch("ROOMS_DIR", self.path)
        self.veto = []
        self._patch("ARCHIVE_VETO", self.veto)
        self._patch("CARD_FACTS", [lambda room: {"last": (room.get("messages") or [{}])[-1].get("text", "")}])
        for cache in (chatroom._INDEX, chatroom._CHANGES, chatroom._TOKEN_ROOMS, chatroom._LOGGED,
                      chatroom._DAMAGED, chatroom._CARDS, chatroom._FULL):
            cache.clear()
            self.addCleanup(cache.clear)
        chatroom._CARDS_STATE.update(dir="", dirty=False, saved=0.0, swept=0.0)
        self.addCleanup(chatroom._CARDS_STATE.update, dir="", dirty=False, saved=0.0, swept=0.0)
        self.now = time.time()

    def _patch(self, name, value):
        p = mock.patch.object(chatroom, name, value)
        p.start()
        self.addCleanup(p.stop)

    def later(self, days=2):
        """The clock this many days on."""
        self._patch("_now", lambda: self.now + days * DAY)

    def restart(self):
        chatroom._INDEX.clear()
        chatroom._LOGGED.clear()
        chatroom._FULL.clear()
        chatroom._CARDS_STATE["dir"] = ""

    def done_room(self, texts=("one", "two")):
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        for t in texts:
            chatroom.post_message(rid, "claude", t)
        chatroom.patch_room(rid, workflow="done", workflowAt=self.now, updatedAt=self.now, status="active")
        return rid

    def entry(self, rid):
        chatroom.room_index()
        return chatroom._INDEX[rid]

    def test_a_task_done_for_a_day_is_a_card_and_opens_whole(self):
        rid = self.done_room()
        self.assertFalse(self.entry(rid).get("cold"))
        self.later()
        self.restart()
        e = self.entry(rid)
        self.assertTrue(e.get("cold"))
        listed = next(r for r in chatroom.list_rooms(copy_rooms=False) if r["id"] == rid)
        self.assertNotIn("messages", listed)
        self.assertEqual(listed[chatroom.CARD_KEY]["turns"], 2)
        self.assertEqual(listed[chatroom.CARD_KEY]["last"], "two")
        self.assertEqual(chatroom.message_count(listed), 2)
        self.assertEqual([m["text"] for m in chatroom.get_room(rid)["messages"]], ["one", "two"])
        self.assertNotIn("messages", chatroom.get_room(rid, messages=False))
        self.assertEqual(chatroom.read_messages(rid)[-1]["text"], "two")

    def test_a_task_not_done_or_done_lately_stays_whole(self):
        busy = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.patch_room(busy, updatedAt=self.now)
        lately = self.done_room()
        self.later(0.5)
        self.restart()
        self.assertFalse(self.entry(busy).get("cold"))
        self.assertFalse(self.entry(lately).get("cold"))

    def test_a_task_waiting_on_the_person_stays_whole(self):
        rid = self.done_room()
        chatroom.patch_room(rid, status="waiting_human")
        self.later()
        self.restart()
        self.assertFalse(self.entry(rid).get("cold"))

    def test_a_veto_keeps_the_room_whole(self):
        rid = self.done_room()
        self.veto.append(lambda room: room["id"] == rid)
        self.later()
        self.restart()
        self.assertFalse(self.entry(rid).get("cold"))
        self.veto[:] = [lambda room: 1 / 0]     # a veto that fails keeps it whole too
        self.restart()
        self.assertFalse(self.entry(rid).get("cold"))

    def test_a_start_takes_the_cards_file_without_reading_the_rooms(self):
        rid = self.done_room()
        self.later()
        self.restart()
        self.entry(rid)
        self.assertTrue(chatroom.save_cards(force=True) or (self.path / chatroom.CARDS_FILE_NAME).exists())
        self.restart()
        with mock.patch.object(chatroom, "_read", side_effect=AssertionError("read")):
            self.assertTrue(self.entry(rid).get("cold"))
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), 2)

    def test_a_card_of_a_room_changed_since_is_not_used(self):
        rid = self.done_room()
        self.later()
        self.restart()
        self.entry(rid)
        chatroom.save_cards(force=True)
        # The room changed behind the cards file (another hub, a hand edit).
        disk = json.loads((self.path / f"{rid}.json").read_text(encoding="utf-8"))
        disk["title"] = "changed"
        (self.path / f"{rid}.json").write_text(json.dumps(disk), encoding="utf-8")
        self.restart()
        self.assertEqual(self.entry(rid)["room"]["title"], "changed")

    def test_a_damaged_cards_file_reads_the_rooms(self):
        rid = self.done_room()
        self.later()
        (self.path / chatroom.CARDS_FILE_NAME).write_text('{"version": 1, "cards": {', encoding="utf-8")
        self.restart()
        self.assertTrue(self.entry(rid).get("cold"))
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), 2)
        (self.path / chatroom.CARDS_FILE_NAME).write_text(json.dumps(
            {"version": 1, "cards": {rid: {"sig": "x", "card": {"id": "room-other"}}}}), encoding="utf-8")
        self.restart()
        self.assertEqual(self.entry(rid)["room"]["title"], "t")

    def test_a_change_to_a_card_keeps_every_message(self):
        rid = self.done_room(["one", "two", "three"])
        self.later()
        self.restart()
        self.assertTrue(self.entry(rid).get("cold"))
        chatroom.patch_room(rid, title="renamed")
        room = chatroom.get_room(rid)
        self.assertEqual(room["title"], "renamed")
        self.assertEqual([m["text"] for m in room["messages"]], ["one", "two", "three"])
        self.restart()
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), 3)

    def test_a_post_to_a_finished_task_makes_it_whole_again(self):
        rid = self.done_room()
        self.later()
        self.restart()
        self.assertTrue(self.entry(rid).get("cold"))
        chatroom.patch_room(rid, workflow="review")
        self.assertFalse(self.entry(rid).get("cold"))
        chatroom.post_message(rid, "claude", "three")
        self.assertEqual([m["text"] for m in chatroom.get_room(rid)["messages"]], ["one", "two", "three"])
        self.assertNotIn(rid, chatroom._CARDS)

    def test_a_room_turns_into_a_card_on_the_sweep(self):
        rid = self.done_room()
        self.assertFalse(self.entry(rid).get("cold"))
        self.assertEqual(chatroom.sweep_cards(now=self.now + 2 * DAY), 1)
        self.assertTrue(chatroom._INDEX[rid].get("cold"))
        self.assertEqual(len(chatroom.get_room(rid)["messages"]), 2)

    def test_a_deleted_room_takes_its_card(self):
        rid = self.done_room()
        self.later()
        self.restart()
        self.entry(rid)
        self.assertTrue(chatroom.delete_room(rid))
        self.assertNotIn(rid, chatroom._CARDS)
        self.assertIsNone(chatroom.get_room(rid))

    def test_an_old_format_room_is_a_card_and_stays_as_it_was(self):
        rid = self.done_room()
        disk = json.loads((self.path / f"{rid}.json").read_text(encoding="utf-8"))
        disk.pop("messageLog", None)
        disk["messages"] = [{"id": "m1", "from": "user", "to": "", "text": "inline", "ts": 1.0}]
        raw = json.dumps(disk)
        (self.path / f"{rid}.json").write_text(raw, encoding="utf-8")
        (self.path / "messages" / f"{rid}.jsonl").unlink()
        self.later()
        self.restart()
        self.assertTrue(self.entry(rid).get("cold"))
        self.assertEqual([m["text"] for m in chatroom.get_room(rid)["messages"]], ["inline"])
        chatroom.save_cards(force=True)
        self.assertEqual((self.path / f"{rid}.json").read_text(encoding="utf-8"), raw)

    def _reads_free(self):
        """_read, wrapped to record whether another thread could take the
        index lock while a room was read."""
        import threading
        free, real = [], chatroom._read

        def take():
            ok = chatroom._INDEX_LOCK.acquire(timeout=2)
            if ok:
                chatroom._INDEX_LOCK.release()
            free.append(ok)

        def read(rid):
            t = threading.Thread(target=take)
            t.start()
            t.join()
            return real(rid)
        return free, mock.patch.object(chatroom, "_read", read)

    def test_a_finished_task_is_read_whole_outside_the_index_lock(self):
        # PO pre-merge: a 17 MB chat read under the lock held every list.
        rid = self.done_room(["one", "two", "three"])
        self.later()
        self.restart()
        self.assertTrue(self.entry(rid).get("cold"))
        free, patch = self._reads_free()
        with patch:
            self.assertEqual(len(chatroom.get_room(rid)["messages"]), 3)
            chatroom._FULL.clear()
            chatroom.patch_room(rid, title="renamed")
        self.assertTrue(free and all(free), free)
        self.assertEqual([m["text"] for m in chatroom.get_room(rid)["messages"]], ["one", "two", "three"])

    def test_a_card_that_cannot_be_saved_leaves_the_list_whole(self):
        rid = self.done_room()
        self.later()
        self.restart()
        self.entry(rid)
        chatroom.save_cards(force=True)
        before = (self.path / chatroom.CARDS_FILE_NAME).read_bytes()
        chatroom._CARDS[rid]["card"]["bad"] = object()
        chatroom._CARDS_STATE["dirty"] = True
        with mock.patch("builtins.print"):
            self.assertFalse(chatroom.save_cards(force=True))
            self.assertIn(rid, [r["id"] for r in chatroom.list_rooms()])
        self.assertTrue(chatroom._CARDS_STATE["dirty"])
        self.assertEqual((self.path / chatroom.CARDS_FILE_NAME).read_bytes(), before)

    def test_only_a_hub_writes_the_cards_file_into_its_own_home(self):
        # A test that read the live rooms wrote the live cards file (#256).
        rid = self.done_room()
        self.later()
        self._patch("_HOME_ROOMS", self.path)
        self.entry(rid)
        self.assertFalse(chatroom.save_cards(force=True))
        self.assertFalse((self.path / chatroom.CARDS_FILE_NAME).exists())
        self._patch("CARDS_WRITER", True)
        self.assertTrue(chatroom.save_cards(force=True))
        self.assertTrue((self.path / chatroom.CARDS_FILE_NAME).exists())

    def test_a_post_during_a_list_is_in_the_next_list(self):
        # Review 8: the task list caches a room's facts by its revision; that
        # revision is the listed room's, never that of a write made since.
        import dashboard
        dashboard._FACTS.clear()
        self.addCleanup(dashboard._FACTS.clear)
        rid = chatroom.create_room("t", [{"agent": "claude"}])["id"]
        chatroom.post_message(rid, "claude", "one")
        revs = {}
        rm = [r for r in chatroom.list_rooms(copy_rooms=False, revisions=revs) if r["id"] == rid][0]
        chatroom.post_message(rid, "claude", "two")
        self.assertNotEqual(revs[rid], chatroom.room_revision(rid))
        self.assertEqual(dashboard._room_facts(rm, revs[rid])["turns"], 1)
        for _ in range(2):
            revs = {}
            rm = [r for r in chatroom.list_rooms(copy_rooms=False, revisions=revs) if r["id"] == rid][0]
            facts = dashboard._room_facts(rm, revs[rid])
            self.assertEqual((facts["turns"], facts["last"]), (2, "two"))


if __name__ == "__main__":
    unittest.main()
