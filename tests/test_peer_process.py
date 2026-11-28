"""Which program sent a request (peer_process.py).

* the walk up the process tree finds an agent: a PTY the hub runs, or a
  claude/codex executable started anywhere; it stops at the hub, and at a cycle;
* for a real local connection the owner is found and an agent's child is named
  as an agent's; a loopback connection nobody holds is refused;
* the tailnet https proxy (tailscale serve, whose tailscaled runs as root on
  Linux, so lsof cannot see its sockets): a loopback client end held, by the
  kernel's socket table, by tailscaled's uid and not the hub's counts as a
  request from another machine; an unknown owner, a closed socket (TIME_WAIT
  reads as uid 0), the hub's own uid and an agent are still refused, whatever
  headers they send (#432).
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


def enc(ip: str, port: int) -> str:
    """An address as /proc/net/tcp(6) writes it."""
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    raw = socket.inet_pton(fam, ip)
    words = "".join("%08X" % struct.unpack("=I", raw[i:i + 4])[0] for i in range(0, len(raw), 4))
    return f"{words}:{port:04X}"


def table(*rows) -> str:
    """A /proc/net/tcp file: rows of (local, remote, state, uid, inode)."""
    out = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"]
    for i, (la, ra, st, uid, ino) in enumerate(rows):
        out.append(f"{i:4d}: {enc(*la)} {enc(*ra)} {st} 00000000:00000000 00:00000000 00000000 "
                   f"{uid:5d}        0 {ino} 1 0000000000000000 20 4 30 10 -1")
    return "\n".join(out) + "\n"


HUB = ("127.0.0.1", 8765)
ME = 1000           # the hub's uid in these tests
ROOT = 0            # tailscaled's


class SocketTable(unittest.TestCase):
    def test_addresses_and_owners_are_read_from_the_table(self):
        self.assertEqual(peer_process._proc_net_addr(enc("127.0.0.1", 8765)), ("127.0.0.1", 8765))
        self.assertEqual(peer_process._proc_net_addr(enc("::ffff:127.0.0.1", 40000)), ("127.0.0.1", 40000))
        self.assertEqual(peer_process._proc_net_addr(enc("::1", 5)), ("::1", 5))
        with tempfile.TemporaryDirectory() as tmp:
            v4, v6 = Path(tmp) / "tcp", Path(tmp) / "tcp6"
            v4.write_text(table((("127.0.0.1", 41000), HUB, "01", ROOT, 777),
                                (("127.0.0.1", 41001), HUB, "06", ROOT, 0),       # TIME_WAIT: nobody's
                                (("127.0.0.1", 41002), HUB, "01", ME, 778)), encoding="ascii")
            v6.write_text(table((("::ffff:127.0.0.1", 41003), ("::ffff:127.0.0.1", 8765), "01", ROOT, 779)),
                          encoding="ascii")
            rows = peer_process._linux_tcp_rows((str(v4), str(v6), str(Path(tmp) / "missing")))
        self.assertEqual(rows, [("127.0.0.1", 41000, "127.0.0.1", 8765, ROOT),
                                ("127.0.0.1", 41002, "127.0.0.1", 8765, ME),
                                ("127.0.0.1", 41003, "127.0.0.1", 8765, ROOT)])

    @unittest.skipUnless(sys.platform.startswith("linux"), "/proc")
    def test_the_live_table_lists_this_processs_own_socket(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        cli = socket.create_connection(srv.getsockname())
        self.addCleanup(cli.close)
        conn, addr = srv.accept()
        self.addCleanup(conn.close)
        self.assertEqual(peer_process.socket_uid(addr, conn.getsockname()), os.getuid())
        self.assertFalse(peer_process.via_proxy(addr, conn.getsockname()), "the hub's own user is never the proxy")


class TailnetProxy(unittest.TestCase):
    """from_agent with the socket table, the proxy's uid and the process list
    stubbed: Linux, the hub running as uid 1000, tailscaled as root."""

    def judge(self, client, rows, owners=(), procs=None, proxy_uids=frozenset({ROOT}), agents=frozenset()):
        with mock.patch.object(peer_process.sys, "platform", "linux"), \
             mock.patch.object(peer_process.os, "getuid", lambda: ME, create=True), \
             mock.patch.object(peer_process, "_linux_tcp_rows", lambda *a: list(rows)), \
             mock.patch.object(peer_process, "_linux_proxy_uids", lambda: set(proxy_uids)), \
             mock.patch.object(peer_process, "_posix_owners", lambda c: list(owners)), \
             mock.patch.object(peer_process, "_posix_processes", lambda: dict(procs or PROCS)):
            return peer_process.from_agent(client, HUB, set(agents))

    def test_a_loopback_client_held_by_tailscaled_passes(self):
        c = ("127.0.0.1", 41000)
        self.assertEqual(self.judge(c, [(*c, *HUB, ROOT)]), "")
        self.assertEqual(self.judge(("::ffff:127.0.0.1", 41000), [(*c, *HUB, ROOT)]), "", "a dual-stack socket")

    def test_an_unknown_loopback_owner_is_still_refused(self):
        c = ("127.0.0.1", 41000)
        cases = {
            "not in the table (closed: TIME_WAIT is left out)": [],
            "held by the hub's own user, yet lsof cannot see it": [(*c, *HUB, ME)],
            "another user, not tailscaled's": [(*c, *HUB, 1001)],
            "another port's socket": [("127.0.0.1", 41001, *HUB, ROOT)],
        }
        for why, rows in cases.items():
            with self.subTest(why):
                self.assertTrue(self.judge(c, rows).startswith("cannot tell"))
        with self.subTest("root's socket, but no tailscaled runs"):
            self.assertTrue(self.judge(c, [(*c, *HUB, ROOT)], proxy_uids=set()).startswith("cannot tell"))

    def test_an_agent_is_refused_whatever_the_table_says(self):
        c = ("127.0.0.1", 41000)
        self.assertIn("agent's process", self.judge(c, [(*c, *HUB, ME)], owners=[320], agents={310}))
        self.assertIn("agent's process", self.judge(c, [(*c, *HUB, ME)], owners=[420]))

    def test_the_tailnet_address_is_unchanged(self):
        c = ("100.101.102.103", 41000)
        self.assertEqual(self.judge(c, []), "", "another machine: no owner here")
        self.assertIn("agent's process", self.judge(c, [], owners=[420]), "an agent here on the tailnet address")

    def test_macos_and_windows_keep_asking_the_owner_only(self):
        for plat in ("darwin", "win32"):
            with self.subTest(plat), mock.patch.object(peer_process.sys, "platform", plat):
                self.assertIsNone(peer_process.socket_uid(("127.0.0.1", 1), HUB))
                self.assertFalse(peer_process.via_proxy(("127.0.0.1", 1), HUB))

    @unittest.skipUnless(sys.platform.startswith("linux"), "/proc")
    def test_the_proxy_uids_are_read_from_proc(self):
        with mock.patch.object(peer_process, "PROXY_EXES", {Path(f"/proc/{os.getpid()}/comm").read_text().strip()}):
            self.assertIn(os.getuid(), peer_process._linux_proxy_uids())
        with mock.patch.object(peer_process, "PROXY_EXES", {"no-such-program-here"}):
            self.assertEqual(peer_process._linux_proxy_uids(), set())


if __name__ == "__main__":
    unittest.main()
