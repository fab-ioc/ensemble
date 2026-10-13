import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.codex import CodexAgent, _FALLBACK_INDEX
import agents.codex as codex


class CodexRolloutMissTests(unittest.TestCase):
    def test_known_missing_id_skips_full_history_but_sees_new_targeted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = CodexAgent()
            sid = "12345678-1234-1234-1234-123456789abc"
            _FALLBACK_INDEX.clear()
            folder = root / "2026" / "10" / "13"
            folder.mkdir(parents=True)
            legacy = folder / "rollout-legacy.jsonl"
            legacy.write_text('{"type":"session_meta","payload":{"id":"another"}}\n',
                              encoding="utf-8")
            with patch.object(agent, "sessions_dir", return_value=root), \
                    patch.object(codex, "_read_meta", wraps=codex._read_meta) as read:
                self.assertEqual(agent.rollouts_for_session(sid), [])
                self.assertEqual(agent.rollouts_for_session(sid), [])
                self.assertEqual(read.call_count, 1)
                path = folder / f"rollout-2026-10-13T00-00-00-{sid}.jsonl"
                path.write_text("", encoding="utf-8")
                self.assertEqual(agent.rollouts_for_session(sid), [path])
            _FALLBACK_INDEX.clear()


if __name__ == "__main__":
    unittest.main()
