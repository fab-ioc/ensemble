"""Versioned, detached PTY owner. The hub is only an RPC client.

This module intentionally depends only on ptyrun and the standard library.
It listens on loopback with a per-state secret. All output is spooled by the
owner, so a hub outage or a slow browser cannot discard stream bytes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backends import ptyrun

PROTOCOL = 1
DESCRIPTOR = "pty-host.json"
RETIRED_SPOOL_S = 60  # allow a brief hub reconnect to drain a just-ended PTY
_client = None


class HostVersionMismatch(RuntimeError):
    """A live owner speaks a different wire or code version."""


def version() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(ptyrun.__file__)):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def _call(port: int, token: str, op: str, *, timeout: float = 20, **args):
    with socket.create_connection(("127.0.0.1", port), timeout=min(5, timeout)) as sock:
        sock.settimeout(timeout)
        request = json.dumps({"token": token, "op": op, "args": args}).encode("utf-8") + b"\n"
        sock.sendall(request)
        with sock.makefile("r", encoding="utf-8") as src:
            line = src.readline(4 * 1024 * 1024)
    if not line:
        raise ConnectionError("PTY host closed the connection")
    result = json.loads(line)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "PTY host request failed")
    return result.get("value")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(4 * 1024 * 1024)
            req = json.loads(line)
            if not hmac.compare_digest(str(req.get("token") or ""), self.server.token):
                raise PermissionError("PTY host token required")
            op, args = req.get("op"), req.get("args") or {}
            value = self.server.dispatch(op, args)
            out = {"ok": True, "value": value}
        except Exception as exc:
            out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.wfile.write(json.dumps(out).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, token: str):
        self.token = token
        self.retired = {}
        self.retired_lock = threading.Lock()
        self.death_acks = set()
        self.next_reap = time.monotonic() + 5
        super().__init__(("127.0.0.1", 0), _Handler)

    def service_actions(self):
        super().service_actions()
        if time.monotonic() >= self.next_reap:
            self.next_reap = time.monotonic() + 5
            try:
                self.dispatch("reap", {})
            except Exception:
                pass  # cleanup must never stop the owner or its live PTYs

    def dispatch(self, op, a):
        if op == "ping":
            return {"protocol": PROTOCOL, "version": version(), "pid": os.getpid()}
        if op == "shutdown":
            for item in ptyrun.list_sessions():
                ptyrun.kill(item["id"])
            with self.retired_lock:
                retired = list(self.retired.values())
                self.retired.clear()
            for old, _ in retired:
                old.discard_spool()
            threading.Thread(target=self.shutdown, daemon=True).start()
            return True
        if op == "list":
            return ptyrun.list_sessions()
        if op == "create":
            s = ptyrun.create(a["argv"], cwd=a.get("cwd"), env=a.get("env"),
                              rows=a.get("rows", 40), cols=a.get("cols", 120),
                              label=a.get("label", ""), meta=a.get("meta"))
            return s.info()
        if op == "deaths":
            with ptyrun._DEATH_LOCK:
                return [rec for key, rec in ptyrun._DEATHS.items()
                        if key not in self.death_acks]
        if op == "ack_death":
            self.death_acks.add(a["id"])
            return True
        if op == "death":
            return ptyrun.death_for(a["id"])
        if op == "forget_death":
            ptyrun.forget_death(a["id"])
            self.death_acks.add(a["id"])
            return True
        if op == "reap":
            with ptyrun._REG_LOCK:
                # The child can exit before the reader has drained its final
                # PTY bytes. Keep it until that reader records the full tail.
                ended = [(key, s) for key, s in ptyrun._REGISTRY.items()
                         if not s.alive() and not s._reader.is_alive()]
                for key, _ in ended:
                    ptyrun._REGISTRY.pop(key, None)
            with self.retired_lock:
                for key, s in ended:
                    ptyrun._record_death(s)
                    self.retired[key] = (s, time.time())
                for key, (_, at) in list(self.retired.items()):
                    if time.time() - at > RETIRED_SPOOL_S:
                        old, _ = self.retired.pop(key)
                        old.discard_spool()
            return True
        if op == "kill":
            with self.retired_lock:
                old = self.retired.pop(a["id"], None)
                if old:
                    old[0].discard_spool()
            return ptyrun.kill(a["id"])
        s = ptyrun.get(a.get("id", ""))
        if s is None:
            with self.retired_lock:
                s = (self.retired.get(a.get("id", "")) or (None,))[0]
        if s is None:
            return None
        if op == "get":
            return {**s.info(), "lastOutput": s.last_output,
                    "lastSubmit": s.last_submit(), "lastInput": getattr(s, "last_input", 0),
                    "exitCode": s.exit_code(), "death": s.death()}
        if op == "write":
            return s.write(a["data"])
        if op == "send_line":
            return s.send_line(a["text"])
        if op == "resize":
            s.resize(a["rows"], a["cols"])
            return True
        if op == "last_input":
            s.last_input = a["value"]
            return True
        if op == "tail":
            return s.tail(a.get("lines", 40), a.get("max_bytes", 16384))
        if op == "snapshot":
            data, offset = s.snapshot()
            return {"data": base64.b64encode(data).decode("ascii"), "offset": offset}
        if op == "read":
            data, offset, alive = s.read_from(a["offset"])
            return {"data": base64.b64encode(data).decode("ascii"),
                    "offset": offset, "alive": alive}
        raise ValueError(f"unknown PTY host operation: {op}")


def serve(config_path: Path):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    for key, value in cfg["env"].items():
        os.environ[key] = value
    spool_dir = config_path.parent / "pty-spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    # Reclaim a dead predecessor's files. A missing/malformed descriptor
    # cannot prove that the old owner is gone, so preserve its spool.
    try:
        previous = descriptor(config_path.parent)
        predecessor_dead = previous is not None and not _pid_alive(int(previous["pid"]))
    except (OSError, ValueError, KeyError, TypeError):
        predecessor_dead = False
    if predecessor_dead:
        for stale in spool_dir.glob("*.bin"):
            try:
                stale.unlink()
            except OSError:
                pass
    os.environ["ENSEMBLE_PTY_SPOOL_DIR"] = str(spool_dir)
    log = (config_path.parent / "logs" / "pty-host.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = log.open("a", encoding="utf-8")
    sys.stderr = sys.stdout
    ptyrun.ensure_windows_console()
    with _Server(cfg["token"]) as server:
        host_descriptor = {"port": server.server_address[1], "token": cfg["token"],
                           "protocol": PROTOCOL, "version": version(), "pid": os.getpid()}
        path = config_path.parent / DESCRIPTOR
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(host_descriptor), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        server.serve_forever(poll_interval=0.2)


class Proxy:
    def __init__(self, client, info):
        self._client = client
        self.id = info["id"]
        self.meta = info.get("meta") or {}
        self.label = info.get("label", "")
        self.cwd = info.get("cwd", "")
        self.pid = info.get("pid")
        self.rows = info.get("rows", 40)
        self.cols = info.get("cols", 120)
        self.created = info.get("created", 0)
        self._last_input = info.get("lastInput", 0)
        self._subs = {}
        self._info = info
        self._info_at = time.monotonic()

    def _rpc(self, op, **args):
        return self._client.call(op, id=self.id, **args)

    @property
    def last_input(self):
        return self.info().get("lastInput", 0)

    @last_input.setter
    def last_input(self, value):
        self._rpc("last_input", value=value)
        self._info_at = 0

    @property
    def last_output(self):
        return self.info().get("lastOutput", 0)

    def last_submit(self):
        return self.info().get("lastSubmit", 0)

    def info(self):
        if time.monotonic() - self._info_at < 0.25:
            return self._info
        self._info = self._rpc("get") or {}
        self._info_at = time.monotonic()
        return self._info

    def alive(self):
        info = self.info()
        return bool(info and info["alive"])

    def exit_code(self):
        return self.info().get("exitCode")

    def death(self):
        return self.info().get("death")

    def write(self, data):
        result = self._rpc("write", data=data.decode("utf-8", "replace") if isinstance(data, bytes) else data)
        self._info_at = 0
        return result

    def send_line(self, text):
        result = self._rpc("send_line", text=text)
        self._info_at = 0
        return result

    def resize(self, rows, cols):
        self._rpc("resize", rows=rows, cols=cols)

    def tail(self, lines=40, max_bytes=16384):
        return self._rpc("tail", lines=lines, max_bytes=max_bytes) or ""

    def subscribe(self, from_offset=None):
        snap = self._rpc("snapshot")
        if snap is None:
            return b"", queue.Queue()
        out = queue.Queue()
        start = (from_offset if isinstance(from_offset, int) and
                 0 <= from_offset <= snap["offset"] else snap["offset"])
        out._start_offset = start
        stop = threading.Event()
        self._subs[out] = stop

        def pump():
            offset = start
            while not stop.is_set():
                try:
                    event = self._rpc("read", offset=offset)
                    if event is None:
                        out.put(None)
                        return
                    offset = event["offset"]
                    data = base64.b64decode(event["data"])
                    if data:
                        out.put(data)
                    elif not event["alive"]:
                        out.put(None)
                        return
                    else:
                        stop.wait(0.15)
                except (ConnectionError, OSError, RuntimeError):
                    stop.wait(0.5)
        threading.Thread(target=pump, daemon=True).start()
        return (b"" if from_offset is not None else base64.b64decode(snap["data"])), out

    def unsubscribe(self, q):
        stop = self._subs.pop(q, None)
        if stop:
            stop.set()


class Client:
    def __init__(self, info):
        self.port, self.token = info["port"], info["token"]
        self.version = info["version"]
        self.pid = info["pid"]
        self.death_hook = None
        self.seen_deaths = set()

    def call(self, op, **args):
        return _call(self.port, self.token, op, **args)

    def create(self, argv, cwd=None, env=None, rows=40, cols=120, label="", meta=None):
        return Proxy(self, self.call("create", argv=argv, cwd=cwd, env=env,
                                     rows=rows, cols=cols, label=label, meta=meta))

    def get(self, pty_id):
        info = self.call("get", id=pty_id)
        return Proxy(self, info) if info else None

    def list_sessions(self):
        return self.call("list")

    def kill(self, pty_id):
        return self.call("kill", id=pty_id)

    def reap(self):
        self.call("reap")
        if self.death_hook:
            for rec in self.call("deaths"):
                if rec["ptyId"] not in self.seen_deaths:
                    self.death_hook(rec)
                    self.seen_deaths.add(rec["ptyId"])
                    self.call("ack_death", id=rec["ptyId"])

    def death_for(self, pty_id):
        return self.call("death", id=pty_id)

    def forget_death(self, pty_id):
        self.seen_deaths.add(pty_id)
        return self.call("forget_death", id=pty_id)

    def set_death_hook(self, fn):
        self.death_hook = fn


def connect(state_dir: Path, backend=None, *, start=True) -> Client | None:
    """Reuse the state dir's host, or launch an isolated one for this hub."""
    path = Path(state_dir) / DESCRIPTOR
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            client = Client(info)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if start:
                raise RuntimeError("existing PTY host identity is unreadable") from exc
            return None
        for attempt in range(5):
            try:
                reply = _call(client.port, client.token, "ping", timeout=2)
                if (reply.get("pid") != client.pid or
                    reply.get("protocol") != PROTOCOL or
                    info.get("protocol") != PROTOCOL or
                    reply.get("version") != version() or
                    info.get("version") != reply.get("version")):
                    if start:
                        raise HostVersionMismatch("existing PTY host version differs")
                    return None
                return client
            except HostVersionMismatch:
                raise
            except (OSError, ValueError, ConnectionError, TimeoutError, RuntimeError):
                if not _pid_alive(client.pid):
                    break
                if attempt < 4:
                    time.sleep(0.25)
        if _pid_alive(client.pid):
            if start:
                raise RuntimeError("existing PTY host is alive but not answering")
            return None
    if not start:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    import secrets
    cfg = path.parent / "pty-host-start.json"
    token = secrets.token_urlsafe(32)
    cfg.write_text(json.dumps({"token": token, "env": dict(os.environ)}), encoding="utf-8")
    os.chmod(cfg, 0o600)
    import app_version
    if app_version.packaged():
        import app_update
        executable = str(app_update.copy_helper(app_version.app_root(),
                         path.parent / "pty-host-runtime"))
        argv = [executable, "--run", "pty_host", str(cfg)]
    else:
        executable = sys.executable
        if os.name == "nt" and executable.lower().endswith("python.exe"):
            maybe = executable[:-10] + "pythonw.exe"
            if Path(maybe).exists():
                executable = maybe
        argv = [executable, str(Path(__file__).resolve()), "--serve", str(cfg)]
    if backend is not None:
        result = backend.start_detached(argv, path.parent / "logs" / "pty-host.log")
        if not result.get("started"):
            raise RuntimeError(result.get("error") or "PTY host could not start")
    else:
        with (path.parent / "logs" / "pty-host-launch.log").open("a", encoding="utf-8") as log:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=os.name != "nt",
                             creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0))
    for _ in range(100):
        time.sleep(0.1)
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            client = Client(info)
            reply = _call(client.port, client.token, "ping", timeout=2)
            if (reply.get("pid") == client.pid and reply.get("protocol") == PROTOCOL
                    and info.get("protocol") == PROTOCOL
                    and reply.get("version") == version()):
                return client
        except (OSError, ValueError, ConnectionError, TimeoutError):
            pass
    raise RuntimeError("PTY host did not become ready")


def descriptor(state_dir: Path) -> dict | None:
    path = Path(state_dir) / DESCRIPTOR
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop(state_dir: Path, timeout: float = 15, expected: dict | None = None) -> bool:
    """Stop the pinned old host after its hub exits; wait for socket and PID."""
    path = Path(state_dir) / DESCRIPTOR
    if not path.exists():
        return expected is None or expected == {"absent": True}
    try:
        info = descriptor(state_dir)
        if expected is not None and info != expected:
            return False
        client = Client(info)
        try:
            _call(client.port, client.token, "shutdown", timeout=1)
        except (OSError, ConnectionError, RuntimeError, TimeoutError):
            pass  # the reply may be lost after shutdown took effect
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            listening = False
            try:
                with socket.create_connection(("127.0.0.1", client.port), timeout=0.5):
                    listening = True
            except OSError:
                pass
            if not listening and not _pid_alive(client.pid):
                return True
            time.sleep(0.1)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return False


def force_stop(expected: dict | None, timeout: float = 10) -> bool:
    """Last resort for a pinned detached host; never target another identity."""
    if not expected or expected.get("absent"):
        return True
    try:
        pid = int(expected["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not _pid_alive(pid):
        return True
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


if __name__ == "__main__" and len(sys.argv) >= 3:
    if sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    elif sys.argv[1] == "--stop":
        pin = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")) if len(sys.argv) > 3 else None
        sys.exit(0 if stop(Path(sys.argv[2]), expected=pin) else 1)
    elif sys.argv[1] == "--force-stop":
        pin = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        sys.exit(0 if force_stop(pin) else 1)
