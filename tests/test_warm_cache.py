import tempfile
from pathlib import Path
import unittest
from unittest import mock

import warm_cache
import dashboard


class WarmCacheTest(unittest.TestCase):
    def test_save_refuses_sources_changed_during_rebuild(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-race-") as td:
            home = Path(td)
            state = home / ".ensemble"
            before = warm_cache._fingerprint(home, state)
            (state / "rooms").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "changed while rebuilding"):
                warm_cache.save(home, state, {}, {}, {}, expected_sources=before)
            self.assertFalse(warm_cache.path(state).exists())

    def test_restart_save_rebuilds_stale_session_and_thread_rows(self):
        old = {300: (0, 1, [{"id": "stale"}])}
        thread = {"room-one": ((1,), {"items": ["stale"]})}
        def rebuild(room_id, **kwargs):
            thread[room_id] = ((2,), {"items": ["fresh"]})
        with mock.patch.object(dashboard, "_SESS_CACHE", old), \
             mock.patch.object(dashboard, "_SESS_GEN", 1), \
             mock.patch.object(dashboard, "_load_sessions_uncached", return_value=[{"id": "fresh"}]) as sessions, \
             mock.patch.object(dashboard.warm_cache, "_fingerprint", return_value={"source": [1, 2]}), \
             mock.patch.object(dashboard, "_TRANSCRIPT_INDEX", ("", 0.0, {})), \
             mock.patch.object(dashboard, "_transcript_index", return_value={"fresh": Path("fresh.jsonl")}), \
             mock.patch.object(dashboard.threads, "_INDEX", thread), \
             mock.patch.object(dashboard.threads, "build", side_effect=rebuild), \
             mock.patch.object(dashboard.warm_cache, "save") as save:
            dashboard.save_restart_caches()
        sessions.assert_called_once_with(300)
        self.assertEqual(save.call_args.args[2], {"300": [{"id": "fresh"}]})
        self.assertEqual(save.call_args.args[4]["index"]["room-one"][1]["items"], ["fresh"])
        self.assertEqual(save.call_args.kwargs["expected_sources"], {"source": [1, 2]})

    def test_codex_home_override_is_part_of_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            sessions = home / "codex-a" / "sessions"
            sessions.mkdir(parents=True)
            rollout = sessions / "rollout.jsonl"
            rollout.write_text("first\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(sessions.parent)}):
                warm_cache.save(home, state, {}, {}, {})
                self.assertIsNotNone(warm_cache.load(home, state))
                with rollout.open("a", encoding="utf-8") as out:
                    out.write("second\n")
                self.assertIsNone(warm_cache.load(home, state))
                warm_cache.save(home, state, {}, {}, {})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex-b")}):
                self.assertIsNone(warm_cache.load(home, state))

    def test_accepts_unchanged_files_and_rejects_append_and_new_file(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            folder = home / ".claude" / "projects" / "one"
            folder.mkdir(parents=True)
            transcript = folder / "session.jsonl"
            transcript.write_text("first\n", encoding="utf-8")
            warm_cache.save(home, state, {"300": [{"id": "one"}]},
                            {"session": transcript}, {"room-one": ((1, 2), {"items": []})})
            loaded = warm_cache.load(home, state)
            self.assertEqual(loaded["sessions"]["300"], [{"id": "one"}])
            self.assertEqual(loaded["transcripts"]["session"], transcript)
            self.assertEqual(loaded["threads"]["room-one"][0], (1, 2))
            with transcript.open("a", encoding="utf-8") as out:
                out.write("second\n")
            self.assertIsNone(warm_cache.load(home, state))
            warm_cache.save(home, state, {}, {}, {})
            (folder / "new.jsonl").write_text("new\n", encoding="utf-8")
            self.assertIsNone(warm_cache.load(home, state))
