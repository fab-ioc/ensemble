"""The detached owner retains a real PTY and its output across client loss."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from backends import get_backend, ptyhost


class HostTest(unittest.TestCase):
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
