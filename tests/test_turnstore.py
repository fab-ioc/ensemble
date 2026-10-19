"""#247: a transcript's text turns are kept on disk, so a restarted hub shows
a long chat without reading every transcript again."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import input_provenance
import turnstore


def _line(role, text, ts):
    return json.dumps({"type": role, "timestamp": ts,
                       "message": {"role": role, "content": text}}) + "\n"


class TurnStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "rooms").mkdir()
        self.db = base / "cache" / "turns.sqlite3"
        self.t = base / "abc.jsonl"
        self.t.write_text(_line("user", "hello", "2026-10-19T10:00:00Z")
                          + _line("assistant", "hi there", "2026-10-19T10:00:01Z"), encoding="utf-8")
        patches = [mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(turnstore.close)
        turnstore.close()

    def _sig(self):
        st = self.t.stat()
        return st.st_size, st.st_mtime_ns

    def test_a_second_read_comes_from_the_store(self):
        first = dashboard._stored_text_turns(self.t, self._sig())
        self.assertEqual([x["text"] for x in first], ["hello", "hi there"])
        self.assertTrue(self.db.is_file())
        turnstore.close()       # a new hub: nothing in memory
        with mock.patch.object(dashboard, "_claude_text_turns", side_effect=AssertionError("parsed")):
            again = dashboard._stored_text_turns(self.t, self._sig())
        self.assertEqual(again, first)

    def test_a_changed_file_is_read_again(self):
        dashboard._stored_text_turns(self.t, self._sig())
        with self.t.open("a", encoding="utf-8") as f:
            f.write(_line("user", "more", "2026-10-19T10:00:02Z"))
        turnstore.close()
        out = dashboard._stored_text_turns(self.t, self._sig())
        self.assertEqual([x["text"] for x in out], ["hello", "hi there", "more"])

    def test_a_new_parser_reads_every_file_afresh(self):
        sig = self._sig()
        turnstore.put(self.db, str(self.t), sig, "old-parser", [{"role": "user", "text": "stale"}])
        self.assertIsNone(turnstore.get(self.db, str(self.t), sig, "new-parser"))
        out = dashboard._stored_text_turns(self.t, sig)
        self.assertEqual(out[0]["text"], "hello")

    def test_a_change_to_anything_the_parser_uses_is_a_new_version(self):
        base = turnstore.version_of(*dashboard._parser_sources())
        def other_image_path(turn, path, k):
            return False
        def other_meta_texts(meta):
            return []
        for name, repl in (("_claude_image_path", other_image_path), ("_meta_texts", other_meta_texts),
                           ("_PASTED", __import__("re").compile("x"))):
            with mock.patch.object(dashboard, name, repl):
                self.assertNotEqual(turnstore.version_of(*dashboard._parser_sources()), base, name)
        with mock.patch.object(dashboard.attachments, "is_attachment_path", other_image_path):
            # a hub module is taken whole: its source, not its live names
            self.assertIn(__import__("inspect").getsource(dashboard.attachments),
                          dashboard._parser_sources())
        self.assertEqual(turnstore.version_of(*dashboard._parser_sources()), base)

    def test_a_locked_or_busy_store_is_kept(self):
        sig = self._sig()
        turnstore.put(self.db, "x", sig, "v", [{"text": "a"}], force=True)
        for err in (sqlite3.OperationalError("database is locked"),
                    sqlite3.DatabaseError("a healthy file, a passing error")):
            turnstore.close()
            with mock.patch.object(turnstore, "_open", side_effect=err):
                self.assertIsNone(turnstore.get(self.db, "x", sig, "v"))
            self.assertTrue(self.db.is_file())
        turnstore.close()
        self.assertEqual(turnstore.get(self.db, "x", sig, "v"), [{"text": "a"}])

    def test_an_older_reading_never_replaces_a_newer_one(self):
        turnstore.put(self.db, "x", (20, 2000), "v", [{"text": "new"}], force=True)
        turnstore.put(self.db, "x", (10, 1000), "v", [{"text": "old"}], force=True)
        self.assertEqual(turnstore.get(self.db, "x", (20, 2000), "v"), [{"text": "new"}])
        turnstore.put(self.db, "x", (10, 1000), "v2", [{"text": "new parser"}], force=True)
        self.assertEqual(turnstore.get(self.db, "x", (10, 1000), "v2"), [{"text": "new parser"}])

    def test_a_live_transcript_is_stored_at_most_every_put_every(self):
        sig = self._sig()
        self.assertTrue(turnstore.put(self.db, "x", sig, "v", [{"text": "a"}]))
        self.assertFalse(turnstore.put(self.db, "x", (sig[0] + 1, sig[1]), "v", [{"text": "b"}]))
        self.assertEqual(turnstore.get(self.db, "x", sig, "v"), [{"text": "a"}])

    def test_a_broken_store_reads_the_transcript(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.db.write_bytes(b"not a database at all" * 100)
        out = dashboard._stored_text_turns(self.t, self._sig())
        self.assertEqual([x["text"] for x in out], ["hello", "hi there"])
        # ... and starts the store again, so the next hub reads it from there
        turnstore.close()
        self.assertEqual(turnstore.get(self.db, str(self.t), self._sig(),
                                       dashboard._turnstore_version()), out)

    def test_a_damaged_row_reads_the_transcript(self):
        sig = self._sig()
        dashboard._stored_text_turns(self.t, sig)
        turnstore.close()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE turns SET data=?", (b"garbage",))
        out = dashboard._stored_text_turns(self.t, sig)
        self.assertEqual([x["text"] for x in out], ["hello", "hi there"])


class ProvenanceIndexTest(unittest.TestCase):
    """index() for one seat and session keeps what match() and assign()
    would keep anyway: the result is the same as filtering the whole index."""

    def test_filtered_index_matches_the_whole_one(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(chatroom, "ROOMS_DIR", Path(d) / "rooms"):
            (Path(d) / "rooms").mkdir()
            rid = "room-one"
            rows = [("claude", "s1", "a"), ("claude", "s2", "a"), ("codex", "s1", "b"),
                    ("", "", "c"), ("claude", "", "d")]
            for at, (ident, sid, text) in enumerate(rows, 1):
                input_provenance.record(rid, ident, text, {"kind": "hub"}, session_id=sid, at=100 + at)
            whole = input_provenance.index(rid)
            part = input_provenance.index(rid, identity="claude", session_id="s1")
            turns = [(0, "a", 101), (1, "c", 104), (2, "d", 105), (3, "b", 103)]
            self.assertEqual(input_provenance.assign(whole, turns, identity="claude", session_id="s1"),
                             input_provenance.assign(part, turns, identity="claude", session_id="s1"))
            self.assertEqual(sorted(r["_order"] for b in part.values() for r in b), [0, 3, 4])


if __name__ == "__main__":
    unittest.main()
