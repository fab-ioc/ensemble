"""#247: any chat, thread, task or topic shows its latest messages at once,
on a hub just started too. tests/open_budget_hub.py fills a throwaway hub
with a PO chat of a 10 MB transcript (300 points in threads) and 10 MB of
messages, and a task chat of 10 MB with a 10 MB transcript; then starts it
again with its restart caches, and again without them (cold), and measures
each open's first answer, as the page asks for it, and its polls.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_open_budgets
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = ("fill", "restart", "cold")
OPENS = ("poRoom", "poChannel", "poThread", "poTopic", "taskRoom", "soloTail")

FIRST_BYTES = 100_000       # the spec: a first answer of at most 100 KB
FIRST_MS = 300              # the first answer after a start (the spec's warm open, all of it)
WARM_MS = 150               # the same open again
POLL_BYTES = 8_000          # a poll with nothing new


def measure() -> dict:
    with tempfile.TemporaryDirectory(prefix="o247-", ignore_cleanup_errors=True) as home:
        env = dict(os.environ, USERPROFILE=home, HOME=home, PYTHONIOENCODING="utf-8",
                   PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]))
        env.pop("CODEX_HOME", None)
        out = {}
        for mode in MODES:
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "open_budget_hub.py"), mode],
                               cwd=str(ROOT), env=env, capture_output=True, encoding="utf-8",
                               errors="replace", timeout=900)
            if r.returncode != 0:
                raise AssertionError(f"{mode}: {r.stderr[-3000:]}")
            out[mode] = json.loads(r.stdout.strip().splitlines()[-1])
        return out


class OpenBudgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.at = measure()
        print("\n#247 open budgets:", json.dumps(cls.at, indent=1), file=sys.stderr)

    def test_every_first_answer_fits_100_kb(self):
        for mode, m in self.at.items():
            for name in OPENS:
                with self.subTest(mode=mode, open=name):
                    self.assertLessEqual(m[name]["bytes"], FIRST_BYTES)

    def test_a_hub_just_started_answers_each_open_within_budget(self):
        # The chats were read ahead as the hub started: none is still being
        # read, and the first answer of each is as quick as a warm one.
        for mode in ("restart", "cold"):
            m = self.at[mode]
            self.assertFalse(m["channelPending"], mode)
            for name in OPENS:
                with self.subTest(mode=mode, open=name):
                    self.assertLessEqual(m[name]["firstMs"], FIRST_MS)

    def test_an_open_again_is_quicker_still(self):
        for mode, m in self.at.items():
            for name in OPENS:
                with self.subTest(mode=mode, open=name):
                    self.assertLessEqual(m[name]["warmMs"], WARM_MS)

    def test_polls_send_only_what_changed(self):
        for mode, m in self.at.items():
            with self.subTest(mode=mode, poll="channel"):
                self.assertLessEqual(m["poChannelPoll"]["bytes"], POLL_BYTES)
                self.assertLessEqual(m["poChannelPoll"]["warmMs"], WARM_MS)
            with self.subTest(mode=mode, poll="transcript"):
                # Fetched once its size changed: the turns after those held,
                # and the last 4 again (~3.4 KB each here), not the tail.
                self.assertLessEqual(m["soloPoll"]["bytes"], 5 * 3_500)
                self.assertLessEqual(m["soloPoll"]["warmMs"], WARM_MS)

    def test_the_opens_show_what_they_should(self):
        m = self.at["restart"]
        self.assertTrue(m["poThread"]["thread"].startswith("P"))
        self.assertTrue(m["poTopic"]["topic"])
        self.assertGreater(m["poTopic"]["items"], 0)


if __name__ == "__main__":
    unittest.main()
