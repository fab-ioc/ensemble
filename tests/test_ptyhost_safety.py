"""Recovery rules for the detached PTY owner."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import dashboard
from backends import ptyhost, ptyrun
import warm_cache


class HostSafetyTest(unittest.TestCase):
    def test_force_stop_targets_only_the_pinned_pid_tree(self):
        with mock.patch.object(ptyhost, "_pid_alive", side_effect=[True, False]):
            if os.name == "nt":
                with mock.patch.object(ptyhost.subprocess, "run") as run:
                    self.assertTrue(ptyhost.force_stop({"pid": 5678}))
                self.assertEqual(run.call_args.args[0], ["taskkill", "/F", "/T", "/PID", "5678"])
            else:
                with mock.patch.object(ptyhost.os, "getpgid", return_value=5678), \
                     mock.patch.object(ptyhost.os, "killpg") as killpg:
                    self.assertTrue(ptyhost.force_stop({"pid": 5678}))
                killpg.assert_called_once_with(5678, ptyhost.signal.SIGKILL)
        self.assertFalse(ptyhost.force_stop({"port": 1234}))

    def _descriptor(self, root, **changes):
        info = {"port": 1234, "token": "secret", "pid": 5678,
                "protocol": ptyhost.PROTOCOL, "version": ptyhost.version()}
        info.update(changes)
        (root / ptyhost.DESCRIPTOR).write_text(json.dumps(info), encoding="utf-8")
        return info

    def test_code_or_protocol_mismatch_never_attaches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for mismatch in ({"version": "old"}, {"protocol": -1}):
                info = self._descriptor(root, **mismatch)
                with mock.patch.object(ptyhost, "_call", return_value={
                        "pid": info["pid"], "protocol": ptyhost.PROTOCOL,
                        "version": ptyhost.version()}), \
                     mock.patch.object(ptyhost, "_pid_alive", return_value=True), \
                     mock.patch.object(ptyhost.time, "sleep") as sleep:
                    self.assertIsNone(ptyhost.connect(root, start=False))
                    with self.assertRaises(ptyhost.HostVersionMismatch):
                        ptyhost.connect(root)
                    sleep.assert_not_called()

    def test_transient_ping_retries_without_launching_second_host(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            info = self._descriptor(root)
            pong = {"pid": info["pid"], "protocol": ptyhost.PROTOCOL,
                    "version": info["version"]}
            with mock.patch.object(ptyhost, "_call", side_effect=[OSError("miss"), pong]) as ping, \
                 mock.patch.object(ptyhost, "_pid_alive", return_value=True), \
                 mock.patch.object(ptyhost.time, "sleep") as sleep:
                self.assertEqual(ptyhost.connect(root).pid, info["pid"])
            self.assertEqual(ping.call_count, 2)
            sleep.assert_called_once()
            with mock.patch.object(ptyhost, "_call", side_effect=OSError("miss")), \
                 mock.patch.object(ptyhost, "_pid_alive", return_value=True), \
                 mock.patch.object(ptyhost.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "alive but not answering"):
                    ptyhost.connect(root)
            self.assertEqual(json.loads((root / ptyhost.DESCRIPTOR).read_text(encoding="utf-8")), info)

    def test_failed_host_start_uses_local_ptys_and_disables_hot_mode(self):
        with mock.patch.object(dashboard.ptyhost, "connect", side_effect=RuntimeError("start failed")), \
             mock.patch.object(dashboard.ptyrun, "use_local") as local, \
             mock.patch.object(dashboard, "_restart_log") as log, \
             mock.patch.object(dashboard, "_PTY_HOST_MODE", "unknown"):
            dashboard.attach_pty_host()
            local.assert_called_once()
            self.assertEqual(dashboard._PTY_HOST_MODE, "local")
            self.assertFalse(dashboard.pty_host_compatible())
            log.assert_called_once()

    def test_local_fallback_restores_registry_functions_after_attached_client(self):
        local_create = ptyrun._LOCAL_API["create"]
        try:
            ptyrun.use_host(mock.Mock())
            self.assertIsNot(ptyrun.create, local_create)
            ptyrun.use_local()
            self.assertIs(ptyrun.create, local_create)
        finally:
            ptyrun.use_local()

    def test_acknowledged_death_is_not_replayed_to_new_client(self):
        server = ptyhost._Server("secret")
        try:
            rec = {"ptyId": "dead", "exitCode": 1}
            with mock.patch.dict(ptyrun._DEATHS, {"dead": rec}, clear=True):
                first = ptyhost.Client({"port": 0, "token": "secret", "pid": 1, "version": "v"})
                second = ptyhost.Client({"port": 0, "token": "secret", "pid": 1, "version": "v"})
                first.call = lambda op, **args: server.dispatch(op, args)
                second.call = lambda op, **args: server.dispatch(op, args)
                got = []
                first.set_death_hook(got.append)
                second.set_death_hook(got.append)
                first.reap()
                second.reap()
                self.assertEqual(got, [rec])
        finally:
            server.server_close()

    def test_killed_and_expired_sessions_remove_spools(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "spool.bin"
            path.write_bytes(b"output")
            sess = object.__new__(ptyrun.PtySession)
            sess._spool = path
            sess._lock = __import__("threading").Lock()
            sess._proc = mock.Mock()
            sess._proc.pid = 0
            sess.kill()
            self.assertFalse(path.exists())
            path.write_bytes(b"output")
            sess._spool = path
            server = ptyhost._Server("secret")
            try:
                server.retired["ended"] = (sess, time.time() - ptyhost.RETIRED_SPOOL_S - 1)
                server.dispatch("reap", {})
                self.assertFalse(path.exists())
                path.write_bytes(b"output")
                sess._spool = path
                server.retired["ended"] = (sess, time.time())
                shutdown_called = __import__("threading").Event()
                with mock.patch.object(ptyrun, "list_sessions", return_value=[]), \
                     mock.patch.object(server, "shutdown", side_effect=shutdown_called.set):
                    server.dispatch("shutdown", {})
                    self.assertTrue(shutdown_called.wait(1))
                self.assertFalse(path.exists())
            finally:
                server.server_close()

    def test_proxy_info_reads_are_cached_briefly(self):
        info = {"id": "one", "alive": True, "lastOutput": 42, "lastInput": 1}
        client = mock.Mock()
        proxy = ptyhost.Proxy(client, info)
        for _ in range(20):
            self.assertTrue(proxy.alive())
            self.assertEqual(proxy.last_output, 42)
            self.assertEqual(proxy.last_input, 1)
        client.call.assert_not_called()
        proxy._info_at -= 1
        client.call.return_value = dict(info, lastOutput=43)
        self.assertEqual(proxy.last_output, 43)
        client.call.assert_called_once_with("get", id="one")

    def test_warm_cache_rejects_different_python_code_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.object(warm_cache, "_code_version", return_value="before"):
                warm_cache.save(root, root / ".ensemble", {}, {}, {})
                self.assertIsNotNone(warm_cache.load(root, root / ".ensemble"))
            with mock.patch.object(warm_cache, "_code_version", return_value="after"):
                self.assertIsNone(warm_cache.load(root, root / ".ensemble"))
