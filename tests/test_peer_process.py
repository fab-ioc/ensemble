"""Which program sent a request (peer_process.py).

* the walk up the process tree finds an agent: a PTY the hub runs, or a
  claude/codex executable started anywhere; it stops at the hub, and at a cycle;
* for a real local connection the owner is found and an agent's child is named
  as an agent's; a loopback connection nobody holds is refused.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import peer_process  # noqa: E402

PROCS = {
    100: (1, "explorer"), 200: (100, "chrome"),
    300: (1, "python"), 310: (300, "pwsh"), 320: (310, "curl"),
    400: (1, "claude"), 410: (400, "bash"), 420: (410, "python"),
    500: (1, "python"), 510: (500, "chrome"),
}


class Ancestry(unittest.TestCase):
    def test_the_walk_finds_agents_and_only_agents(self):
        self.assertIsNone(peer_process.agent_ancestor(200, PROCS, {310}), "a browser")
        self.assertEqual(peer_process.agent_ancestor(320, PROCS, {310}), 310, "a curl under an agent's PTY")
        self.assertEqual(peer_process.agent_ancestor(420, PROCS, set()), 400, "under a claude started anywhere")
        self.assertIsNone(peer_process.agent_ancestor(510, PROCS, set(), hub_pid=500), "the hub opened it")
        self.assertIsNone(peer_process.agent_ancestor(1, {1: (2, "x"), 2: (1, "y")}, set()), "a cycle ends")
        self.assertEqual(peer_process._exe_stem("C:\\Tools\\Codex.EXE"), "codex")
        self.assertEqual(peer_process._exe_stem("/usr/local/bin/claude"), "claude")


class LiveOwner(unittest.TestCase):
    def test_the_owner_of_a_local_connection_is_found(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        child = subprocess.Popen(
            [sys.executable, "-c", "import socket, sys, time; s = socket.create_connection(('127.0.0.1', int(sys.argv[1]))); "
                                   "print('up', flush=True); time.sleep(60)", str(srv.getsockname()[1])],
            stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        conn, addr = srv.accept()
        self.addCleanup(conn.close)
        child.stdout.readline()
        server = conn.getsockname()
        self.assertIsNotNone(peer_process.owner(addr, server))
        self.assertIn("agent's process", peer_process.from_agent(addr, server, {child.pid}))
        self.assertEqual(peer_process.from_agent(addr, server, set()), "", "a program that is no agent's")

    def test_a_loopback_connection_nobody_holds_is_refused(self):
        self.assertTrue(peer_process.from_agent(("127.0.0.1", 1), ("127.0.0.1", 2), set()).startswith("cannot tell"))


if __name__ == "__main__":
    unittest.main()
