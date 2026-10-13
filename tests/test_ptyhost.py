"""The detached owner retains a real PTY and its output across client loss."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from backends import get_backend, ptyhost


class PoolTest(unittest.TestCase):
    """10-13: a TCP connection per call left ~15k sockets in TIME_WAIT and
    exhausted the machine's ports (WinError 10048)."""

    def test_many_calls_reuse_one_connection(self):
        import threading
        server = ptyhost._Server("tok")
        accepted = []
        orig = server.process_request

        def counting(request, client_address):
            accepted.append(client_address)
            return orig(request, client_address)
        server.process_request = counting
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        try:
            pool = ptyhost._Pool(server.server_address[1], "tok")
            for _ in range(200):
                self.assertIsInstance(pool.call("list"), list)
            self.assertEqual(len(accepted), 1)
            with self.assertRaises(RuntimeError):
                ptyhost._Pool(server.server_address[1], "wrong").call("list")
        finally:
            server.shutdown()
            server.server_close()


class HostTest(unittest.TestCase):
    def test_dead_predecessor_spool_is_removed_on_new_host_start(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-pty-stale-") as td:
            state = Path(td)
            spool = state / "pty-spool" / "old.bin"
            spool.parent.mkdir()
            spool.write_bytes(b"unreachable")
            (state / ptyhost.DESCRIPTOR).write_text(json.dumps({
                "port": 1, "token": "old", "pid": 99_999_999,
                "protocol": ptyhost.PROTOCOL, "version": ptyhost.version()}), encoding="utf-8")
            host = ptyhost.connect(state, get_backend())
            try:
                self.assertFalse(spool.exists())
            finally:
                self.assertTrue(ptyhost.stop(state, expected=ptyhost.descriptor(state)))

    def test_classic_stop_requires_pinned_identity_and_dead_host_process(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-pty-stop-") as td:
            state = Path(td)
            host = ptyhost.connect(state, get_backend())
            pin = ptyhost.descriptor(state)
            try:
                self.assertEqual(host.call("ping"), {"pid": pin["pid"],
                    "protocol": ptyhost.PROTOCOL, "version": ptyhost.version()})
                wrong = dict(pin, token="wrong")
                pin_file = state / "pinned.json"
                pin_file.write_text(json.dumps(wrong), encoding="utf-8")
                command = [sys.executable, str(Path(ptyhost.__file__)),
                           "--stop", str(state), str(pin_file)]
                self.assertEqual(subprocess.run(command, timeout=25).returncode, 1)
                self.assertTrue(host.call("ping"))
                pin_file.write_text(json.dumps(pin), encoding="utf-8")
                self.assertEqual(subprocess.run(command, timeout=25).returncode, 0)
                self.assertFalse(ptyhost._pid_alive(pin["pid"]))
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", pin["port"]), timeout=0.5)
            finally:
                if ptyhost._pid_alive(pin["pid"]):
                    host.call("shutdown")

    def test_spool_replays_output_larger_than_screen_tail(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-pty-replay-") as td:
            state = Path(td)
            host = ptyhost.connect(state, get_backend())
            try:
                size = 700_000
                code = f"import sys; sys.stdout.write('A' * {size}); sys.stdout.flush()"
                sid = host.create([sys.executable, "-c", code], cwd=td).id
                offset = 0
                received = bytearray()
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    event = host.call("read", id=sid, offset=offset)
                    offset = event["offset"]
                    received.extend(base64.b64decode(event["data"]))
                    if not event["alive"] and not event["data"]:
                        break
                    time.sleep(0.05)
                self.assertEqual(received.count(b"A"), size)
                self.assertGreater(offset, 512 * 1024)
            finally:
                host.call("shutdown")
                for _ in range(50):
                    try:
                        host.call("ping")
                    except (OSError, ConnectionError):
                        break
                    time.sleep(0.1)
                time.sleep(0.2)

    def test_reattach_to_running_command(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-pty-test-") as td:
            state = Path(td)
            host = ptyhost.connect(state, get_backend())
            try:
                code = ("import time; print('begin', flush=True); "
                        "time.sleep(6); print('end', flush=True)")
                sess = host.create([sys.executable, "-c", code], cwd=td)
                sid = sess.id
                first = sess._rpc("snapshot")
                # A fresh Client has no registry or subscription from the old
                # hub; the child and its ConPTY belong to the detached host.
                del sess, host
                host = ptyhost.connect(state, start=False)
                self.assertIsNotNone(host)
                self.assertTrue(host.get(sid).alive())
                offset = 0
                data = b""
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    event = host.call("read", id=sid, offset=offset)
                    offset = event["offset"]
                    data += base64.b64decode(event["data"])
                    if not event["alive"] and not event["data"]:
                        break
                    time.sleep(0.1)
                self.assertIn(b"begin", data)
                self.assertIn(b"end", data)
                self.assertEqual(data.count(b"begin"), 1)
            finally:
                if host:
                    host.call("shutdown")
                    for _ in range(50):
                        try:
                            host.call("ping")
                        except (OSError, ConnectionError):
                            break
                        time.sleep(0.1)
                    time.sleep(0.2)
