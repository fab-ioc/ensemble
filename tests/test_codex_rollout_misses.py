import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.codex import CodexAgent, _ROLLOUT_MISSES


class CodexRolloutMissTests(unittest.TestCase):
    def test_known_missing_id_skips_full_history_but_sees_new_targeted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = CodexAgent()
            sid = "12345678-1234-1234-1234-123456789abc"
            _ROLLOUT_MISSES.clear()
            with patch.object(agent, "sessions_dir", return_value=root), \
                    patch.object(agent, "rollout_files", return_value=[]) as scan:
                self.assertEqual(agent.rollouts_for_session(sid), [])
                self.assertEqual(agent.rollouts_for_session(sid), [])
                self.assertEqual(scan.call_count, 1)
                folder = root / "2026" / "10" / "13"
                folder.mkdir(parents=True)
                path = folder / f"rollout-2026-10-13T00-00-00-{sid}.jsonl"
                path.write_text("", encoding="utf-8")
                self.assertEqual(agent.rollouts_for_session(sid), [path])
            _ROLLOUT_MISSES.clear()


if __name__ == "__main__":
    unittest.main()
