"""ED-230: a hub never shares its port.

On Windows the stdlib's SO_REUSEADDR let a second hub bind the port a hub
already listened on, without an error: after a restart two hubs shared 8765.
The hub binds exclusively there, and a hub whose port is taken says so and
exits before it touches the PTY host or the state. A restart still binds the
port right after the old hub is gone, its closed connections in TIME_WAIT.

Every port here is a free one the OS hands out, never the live hub's.
"""
import errno
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402


class Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ExclusiveBind(unittest.TestCase):
    def serve(self, cls, port):
        srv = cls(("127.0.0.1", port), Ok)
        Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv

    def test_a_second_hub_cannot_bind_a_hubs_port(self):
        port = free_port()
        self.serve(dashboard.HubServer, port)
        with self.assertRaises(OSError):
            dashboard.HubServer(("127.0.0.1", port), Ok)

    def test_a_reusing_server_cannot_join_a_hubs_port(self):
        # What a test's plain HTTPServer (SO_REUSEADDR) did to the live hub.
        port = free_port()
        self.serve(dashboard.HubServer, port)
        with self.assertRaises(OSError):
            ThreadingHTTPServer(("127.0.0.1", port), Ok)

    def test_a_hub_cannot_join_a_reusing_servers_port(self):
        port = free_port()
        self.serve(ThreadingHTTPServer, port)
        with self.assertRaises(OSError):
            dashboard.HubServer(("127.0.0.1", port), Ok)

    @unittest.skipUnless(sys.platform == "win32", "the exclusive option is Windows'")
    def test_exclusive_on_windows(self):
        self.assertFalse(dashboard.HubServer.allow_reuse_address)
        srv = self.serve(dashboard.HubServer, 0)
        self.assertEqual(srv.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        self.assertEqual(srv.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)

    def test_the_port_binds_again_right_after_the_hub_closes(self):
        # A hub-only restart: the old hub served requests (each one leaves a
        # closed connection in TIME_WAIT on the port), then goes away.
        port = free_port()
        srv = dashboard.HubServer(("127.0.0.1", port), Ok)
        Thread(target=srv.serve_forever, daemon=True).start()
        for _ in range(30):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
        srv.shutdown()
        srv.server_close()
        self.serve(dashboard.HubServer, port)
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read(), b"ok")

    def test_a_taken_port_is_an_address_in_use_error(self):
        # The built app's launcher reports EADDRINUSE as "port taken".
        port = free_port()
        self.serve(dashboard.HubServer, port)
        with unittest.mock.patch("builtins.print"), self.assertRaises(dashboard.PortTaken) as cm:
            dashboard._listen_or_exit("127.0.0.1", port)
        self.assertIsInstance(cm.exception, OSError)
        self.assertEqual(cm.exception.errno, errno.EADDRINUSE)
        self.assertIn(f"port {port} on 127.0.0.1 is taken", str(cm.exception))


class SecondHubProcess(unittest.TestCase):
    def test_a_hub_on_a_busy_port_exits_with_a_clear_line(self):
        # The real dashboard.py, its home a scratch folder: with the port
        # held it says so and exits, and leaves no PTY host and no state.
        port = free_port()
        holder = ThreadingHTTPServer(("127.0.0.1", port), Ok)
        Thread(target=holder.serve_forever, daemon=True).start()
        self.addCleanup(holder.server_close)
        self.addCleanup(holder.shutdown)
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith("ENSEMBLE_")}
            env.update(USERPROFILE=home, HOME=home, CODEX_HOME=str(Path(home) / ".codex"),
                       PYTHONIOENCODING="utf-8")
            log = Path(home) / "hub.log"
            t0 = time.time()
            r = subprocess.run([sys.executable, str(ROOT / "dashboard.py"), "--port", str(port),
                                "--log", str(log)], cwd=str(ROOT), env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=120)
            took = time.time() - t0
            text = log.read_text(encoding="utf-8")
            self.assertEqual(r.returncode, 3, text)
            self.assertIn(f"port {port} on 127.0.0.1 is taken", text)
            self.assertIn("exits without touching the agents' terminals or the state", text)
            self.assertNotIn("Traceback", text)
            self.assertNotIn("Connecting to the agents' terminals", text)
            state = Path(home) / ".ensemble"
            self.assertFalse((state / "pty-host.json").exists())
            self.assertFalse((state / "cache" / "file-facts.json").exists())
            self.assertLess(took, 60)
        # The holder still answers alone.
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read(), b"ok")


if __name__ == "__main__":
    unittest.main()
