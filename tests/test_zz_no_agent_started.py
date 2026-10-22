"""Last in a `discover` run (modules run in name order): the run started no
agent (#257).

* No Claude Code, Codex or agent launch shell runs under this process.
* No test reached a real agent start: each one the guard refused
  (agent_launch.REFUSED) is a test whose hub tried to start an agent, so
  what it checks is not what a hub does. The agents' own status questions
  (`claude auth status`, `codex login status`) are told apart: the first-run
  check asks them, and a refused one just reads as "could not say".
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_launch  # noqa: E402
import agent_processes  # noqa: E402

PROBES = (("auth", "status"), ("login", "status"), ("--version",))


def _probe(argv: list[str]) -> bool:
    rest = tuple(argv[1:])
    return any(rest[:len(p)] == p for p in PROBES)


class TheRunStartedNoAgent(unittest.TestCase):
    def test_no_agent_process_under_the_run(self):
        found = agent_processes.agent_descendants()
        self.assertEqual(found, [], "agents started by this test run:\n" + "\n".join(
            f"  pid {r['pid']}: {r['cmd'][:200]}" for r in found))

    def test_no_test_reached_an_agent_start(self):
        self.assertFalse(agent_launch.allowed(), "a test left agent launches allowed")
        starts = [r for r in agent_launch.REFUSED if not _probe(r["argv"])]
        self.assertEqual(starts, [], "tests whose hub tried to start an agent (refused):\n"
                         + "\n".join(f"  {r['test'] or '(a hub thread)'}: {' '.join(r['argv'])[:160]}"
                                     f"\n{r['stack'][-1200:]}" for r in starts))


if __name__ == "__main__":
    unittest.main()
