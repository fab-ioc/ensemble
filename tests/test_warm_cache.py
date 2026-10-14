import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import durable
import warm_cache
import dashboard


class WarmCacheTest(unittest.TestCase):
    def test_round_trip_keeps_paths_and_tuples(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            transcript = home / "session.jsonl"
            warm_cache.save(home, state, {"300": [{"id": "one"}]},
                            {"session": transcript}, {"room-one": ((1, 2), {"items": []})})
            loaded = warm_cache.load(home, state)
            self.assertEqual(loaded["sessions"]["300"], [{"id": "one"}])
            self.assertEqual(loaded["transcripts"]["session"], transcript)
            self.assertEqual(loaded["threads"]["room-one"][0], (1, 2))

    def test_changed_files_do_not_refuse_the_snapshot(self):
        """ED-227: rows are a first answer, read again at once; a transcript
        written since does not throw the whole snapshot away."""
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            folder = home / ".claude" / "projects" / "one"
            folder.mkdir(parents=True)
            transcript = folder / "session.jsonl"
            transcript.write_text("first\n", encoding="utf-8")
            warm_cache.save(home, state, {"300": [{"id": "one"}]}, {}, {})
            with transcript.open("a", encoding="utf-8") as out:
                out.write("second\n")
            (folder / "new.jsonl").write_text("new\n", encoding="utf-8")
            self.assertEqual(warm_cache.load(home, state)["sessions"]["300"], [{"id": "one"}])

    def test_torn_snapshot_falls_back_to_the_previous_save(self):
        """A power cut that leaves the newest save empty or cut off."""
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            warm_cache.save(home, state, {"300": [{"id": "old"}]}, {}, {})
            warm_cache.save(home, state, {"300": [{"id": "new"}]}, {}, {})
            target = warm_cache.path(state)
            self.assertTrue(durable.prev_of(target).exists())
            for torn in (b"", b"\0" * 4096, target.read_bytes()[:100]):
                target.write_bytes(torn)
                self.assertEqual(warm_cache.load(home, state)["sessions"]["300"], [{"id": "old"}])

    def test_codex_home_override_is_part_of_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex-a")}):
                warm_cache.save(home, state, {}, {}, {})
                self.assertIsNotNone(warm_cache.load(home, state))
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex-b")}):
                self.assertIsNone(warm_cache.load(home, state))

    def test_save_from_memory_reads_nothing_again(self):
        old = {300: (0, 1, [{"id": "held"}]), 500: (0, 1, [{"id": "boot"}])}
        thread = {"room-one": ((1,), {"items": ["held"]})}
        with mock.patch.object(dashboard, "_SESS_CACHE", old), \
             mock.patch.object(dashboard, "_SESS_BOOT", {500}), \
             mock.patch.object(dashboard, "_WARM_SAVED_SIG", None), \
             mock.patch.object(dashboard, "_load_sessions_uncached") as sessions, \
             mock.patch.object(dashboard, "_TRANSCRIPT_INDEX", (str(dashboard.PROJ_DIR), 0.0, {"s": Path("s.jsonl")})), \
             mock.patch.object(dashboard.threads, "_INDEX", thread), \
             mock.patch.object(dashboard.threads, "build") as build, \
             mock.patch.object(dashboard, "_restart_log"), \
             mock.patch.object(dashboard.warm_cache, "save") as save:
            self.assertTrue(dashboard.save_restart_caches(why="periodic"))
            # Nothing new since: the periodic save writes nothing.
            self.assertFalse(dashboard.save_restart_caches(why="periodic"))
            # A restart saves anyway.
            self.assertTrue(dashboard.save_restart_caches())
        sessions.assert_not_called()
        build.assert_not_called()
        # The start-up snapshot's own rows (_SESS_BOOT) are not saved again.
        self.assertEqual(save.call_args.args[2], {"300": [{"id": "held"}]})
        self.assertEqual(save.call_args.args[3], {"s": Path("s.jsonl")})
        self.assertEqual(save.call_args.args[4]["index"]["room-one"][1]["items"], ["held"])

    def test_snapshot_written_through_durable(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            with mock.patch.object(warm_cache.durable, "write_text", wraps=durable.write_text) as w:
                warm_cache.save(home, state, {}, {}, {})
            self.assertTrue(w.call_args.kwargs.get("keep_prev"))
            self.assertEqual(json.loads(warm_cache.path(state).read_text(encoding="utf-8"))["format"],
                             warm_cache.FORMAT)


if __name__ == "__main__":
    unittest.main()
