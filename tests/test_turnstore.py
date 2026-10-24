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

    def test_a_deleted_transcript_is_read_from_the_store(self):
        """#265: Free disk space deleted the PO chats' past conversations;
        the hub keeps their text first (keep_conversation_text) and reads it
        from the store: the chat, its links and threads have it as before."""
        sid = "0199a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5b"
        t = Path(self.tmp.name) / "proj" / f"{sid}.jsonl"
        t.parent.mkdir()
        t.write_text(_line("user", "the old ask", "2026-10-12T10:00:00Z")
                     + _line("assistant", "the old answer", "2026-10-12T10:00:01Z"), encoding="utf-8")
        st = t.stat()
        self.assertTrue(dashboard.keep_conversation_text("claude", sid, "\\\\?\\" + str(t) if os.name == "nt" else str(t)))
        self.assertFalse(dashboard.keep_conversation_text("codex", sid, str(t)), "a Codex text has no store")
        t.unlink()
        turnstore.close()
        dashboard._KEPT[0] = 0.0
        self.addCleanup(dashboard._KEPT.__setitem__, 0, 0.0)
        with mock.patch.object(dashboard, "find_transcript", return_value=None), \
                mock.patch.object(dashboard.agents, "get_agent", return_value=None):
            turns = dashboard.read_session_turns(sid)
            self.assertEqual([x["text"] for x in turns], ["the old ask", "the old answer"])
            self.assertEqual(dashboard.points._session_stat(sid), [st.st_size, round(st.st_mtime_ns / 1e9, 3)])
            self.assertIsNone(dashboard.read_session_turns("0199a1b2-c3d4-4e5f-8a9b-000000000000"))
            # A row that no longer reads (damaged): no kept session either.
            with turnstore._LOCK:
                conn = turnstore._conn(dashboard._turnstore_db())
                with conn:
                    good = conn.execute("SELECT data FROM turns WHERE path=?", (str(t),)).fetchone()[0]
                    conn.execute("UPDATE turns SET data=? WHERE path=?", (b"garbage", str(t)))
            dashboard._KEPT[0] = 0.0
            self.assertIsNone(dashboard.kept_session(sid))
            self.assertIsNone(dashboard.read_session_turns(sid))
            with turnstore._LOCK:
                conn = turnstore._conn(dashboard._turnstore_db())
                with conn:
                    conn.execute("UPDATE turns SET data=? WHERE path=?", (good, str(t)))
            dashboard._KEPT[0] = 0.0
            self.assertIsNotNone(dashboard.kept_session(sid))
            # Damaged after a read found it whole (review 3): no session,
            # not an empty one, though the listing still names it.
            with turnstore._LOCK:
                conn = turnstore._conn(dashboard._turnstore_db())
                with conn:
                    conn.execute("UPDATE turns SET data=? WHERE path=?", (b"garbage", str(t)))
            self.assertIsNone(dashboard.read_session_turns(sid))
            self.assertIsNone(dashboard.kept_session(sid))
            # The store loses the row after it was listed: no kept session
            # (a 404), not an empty one.
            with turnstore._LOCK:
                conn = turnstore._conn(dashboard._turnstore_db())
                with conn:
                    conn.execute("DELETE FROM turns WHERE path=?", (str(t),))
            self.assertIsNone(dashboard.kept_session(sid))
            self.assertIsNone(dashboard.read_session_turns(sid))

    def test_a_transcript_is_deleted_only_once_this_reading_is_stored(self):
        """#265 review: an older row of the file, a store that does not
        write, or a file that cannot be read keeps the file."""
        sid = "0199a1b2-c3d4-4e5f-8a9b-0c1d2e3f4a5c"
        t = Path(self.tmp.name) / "proj2" / f"{sid}.jsonl"
        t.parent.mkdir()
        t.write_text(_line("user", "ask", "2026-10-12T10:00:00Z"), encoding="utf-8")
        st = t.stat()
        db = dashboard._turnstore_db()
        # A row from a later reading (another hub, a newer mtime) stays and
        # this reading is not stored: the file is not given up.
        turnstore.put(db, str(t), (st.st_size + 5, st.st_mtime_ns + 10**9), dashboard._turnstore_version(),
                      [{"role": "user", "text": "other"}], force=True)
        self.assertFalse(dashboard.keep_conversation_text("claude", sid, str(t)))
        with mock.patch.object(dashboard.turnstore, "put", return_value=False):
            self.assertFalse(dashboard.keep_conversation_text("claude", sid, str(t)))
        with mock.patch.object(Path, "open", side_effect=PermissionError("locked")):
            self.assertFalse(dashboard.keep_conversation_text("claude", sid, str(t)))
        # Written to while it was read (review 2): kept on disk.
        real = dashboard._claude_text_turns

        def read_then_append(tpath, **kw):
            out = real(tpath, **kw)
            with open(tpath, "a", encoding="utf-8") as f:
                f.write(_line("assistant", "late", "2026-10-12T10:00:05Z"))
            return out
        with mock.patch.object(dashboard, "_claude_text_turns", read_then_append):
            self.assertFalse(dashboard.keep_conversation_text("claude", sid, str(t)))
        os.utime(t, ns=(st.st_atime_ns, st.st_mtime_ns + 2 * 10**9))     # newer than the other hub's row
        self.assertTrue(dashboard.keep_conversation_text("claude", sid, str(t)))

    def test_the_turns_kept_in_memory_stay_well_under_150_mb(self):
        # The PO's read: 96 M chars was ~480 MB of the hub at ~5 MB per
        # million measured on the real state. Filled past its cap, the cache
        # holds at most the cap, and that is well under 150 MB.
        import gc
        import tracemalloc
        self.addCleanup(dashboard._TURNS_CACHE.clear)
        self.addCleanup(dashboard._TURNS_CACHE_SIZE.__setitem__, 0, 0)
        dashboard._TURNS_CACHE.clear()
        dashboard._TURNS_CACHE_SIZE[0] = 0
        per_file = 1_000_000
        files = []
        for i in range(dashboard._TURNS_CACHE_CHARS // per_file + 5):
            f = Path(self.tmp.name) / f"s{i}.jsonl"
            f.write_text("x", encoding="utf-8")
            files.append(f)
        made = lambda tpath, sig: [{"role": "assistant", "text": f"{tpath.stem} {n} " + "w" * 9_000,  # noqa: E731
                                    "timestamp": "2026-10-19T10:00:00Z"} for n in range(per_file // 9_200)]
        gc.collect()
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        base = tracemalloc.get_traced_memory()[0]
        with mock.patch.object(dashboard, "_stored_text_turns", made):
            for f in files:
                dashboard.claude_turns_classified(f)
        gc.collect()
        used = tracemalloc.get_traced_memory()[0] - base
        self.assertLessEqual(dashboard._TURNS_CACHE_SIZE[0], dashboard._TURNS_CACHE_CHARS)
        self.assertLess(len(dashboard._TURNS_CACHE), len(files), "the oldest went")
        self.assertLessEqual(dashboard._TURNS_CACHE_CHARS * 5 / 1e6, 120, "~5 MB a million chars, measured")
        self.assertLess(used / 2**20, 100, f"{used / 2**20:.0f} MB")


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
