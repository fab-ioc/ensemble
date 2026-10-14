"""#228: what a page switch costs the hub stays within budget and flat as the
history grows. tests/budget_hub.py serves a throwaway hub over a fixture of a
10 MB PO chat (~600 points) and 2000 transcripts, at 1x, 2x and 5x that
history, and reports the bytes and warm server time of each hot endpoint."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCALES = (1, 2, 5)

# Bytes a switch may move per answer (the spec's ~200 KB a page), and the
# warm server time of a poll.
BYTES = {"sessionsDelta": 64_000, "projectsDelta": 16_000, "roomDelta": 8_000, "roomOlder": 300_000}
ROOM_BUT_POINTS = 200_000       # a chat's first read: its 50 latest messages and status
POINTS = 160_000                # the points' view (capped closed ones; the live ones all)
POLL_MS = 150


def measure(scale: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="b228-") as home:
        env = dict(os.environ, USERPROFILE=home, HOME=home, PYTHONIOENCODING="utf-8",
                   PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
        env.pop("CODEX_HOME", None)
        r = subprocess.run([sys.executable, str(ROOT / "tests" / "budget_hub.py"), str(scale)],
                           cwd=str(ROOT), env=env, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=900)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])


class SwitchBudgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.at = {s: measure(s) for s in SCALES}
        print("\n#228 budgets:", json.dumps(cls.at, indent=1), file=sys.stderr)

    def test_each_answer_a_switch_moves_is_within_budget(self):
        for s, m in self.at.items():
            for name, cap in BYTES.items():
                with self.subTest(scale=s, endpoint=name):
                    self.assertLessEqual(m[name]["bytes"], cap)
            with self.subTest(scale=s, endpoint="room"):
                self.assertLessEqual(m["room"]["bytes"] - m["roomPointsBytes"], ROOM_BUT_POINTS)
                self.assertLessEqual(m["roomPointsBytes"], POINTS)

    def test_the_chat_poll_leaves_the_indexed_messages_as_they_are(self):
        for s, m in self.at.items():
            self.assertTrue(m["messagesUntouched"], s)

    def test_polls_answer_quickly(self):
        for s, m in self.at.items():
            for name in ("sessionsDelta", "projectsDelta", "roomDelta", "roomOlder", "room"):
                with self.subTest(scale=s, endpoint=name):
                    self.assertLessEqual(m[name]["ms"], POLL_MS)

    def test_five_times_the_history_costs_about_the_same(self):
        one, five = self.at[1], self.at[5]
        for name in ("sessions", "sessionsDelta", "projects", "projectsDelta", "room", "roomDelta", "roomOlder"):
            with self.subTest(endpoint=name):
                # Bytes: the same answer, give or take a few rows; the room's
                # first read only by the points that are still live.
                self.assertLessEqual(five[name]["bytes"],
                                     one[name]["bytes"] * 1.05 + 2_000 + (five["roomPointsBytes"] if name == "room" else 0))
                # Time: no read of the whole history (a measured 3x before #228).
                self.assertLessEqual(five[name]["ms"], one[name]["ms"] * 2.5 + 15)


if __name__ == "__main__":
    unittest.main()
