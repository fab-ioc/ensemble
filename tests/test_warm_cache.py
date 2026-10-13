import tempfile
from pathlib import Path
import unittest
from unittest import mock

import warm_cache


class WarmCacheTest(unittest.TestCase):
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
