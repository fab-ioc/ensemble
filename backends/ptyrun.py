"""Cross-platform headless PTY runtime.

The dashboard owns each agent process through a pseudo-terminal — ConPTY on
Windows (via pywinpty), a Unix pty on macOS/Linux (via ptyprocess) — and runs
it with **no visible window**. Output is streamed to the browser (an xterm.js
terminal, over SSE); keystrokes are written back. This replaces the visible
Windows-Terminal / iTerm execution model for headless sessions.

A `PtySession` keeps a bounded rolling output buffer (so a newly-connected
viewer sees the current screen) and fans new output out to any number of live
subscribers. All processes are owned by the dashboard, so liveness is just "is
the child alive" and teardown kills the whole process tree.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import uuid

IS_WINDOWS = os.name == "nt"

# Keep the last ~512 KB of output so a fresh viewer can repaint the screen.
_BUFFER_MAX = 512 * 1024

_REGISTRY: dict[str, "PtySession"] = {}
_REG_LOCK = threading.Lock()


class PtySession:
    """One headless PTY-hosted process the dashboard owns."""

    def __init__(self, argv, cwd: str | None = None, env: dict | None = None,
                 rows: int = 40, cols: int = 120, label: str = "",
                 meta: dict | None = None):
        self.id = "pty-" + uuid.uuid4().hex[:8]
        self.argv = argv
        self.cwd = cwd
        self.rows = rows
        self.cols = cols
        self.label = label
        self.meta = dict(meta or {})
        self.created = time.time()
        self._buf = bytearray()
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._alive = True
        self._exit_code = None
        self._last_output = time.time()
        self._spawn(env)
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    # ---- backend-specific spawn / io ----

    def _spawn(self, env):
        full_env = {**os.environ, **(env or {})}
        if IS_WINDOWS:
            from winpty import PtyProcess  # lazy: Windows-only dependency
            # Pass argv through as-is: pywinpty accepts a str (it shlex-splits,
            # posix=False) or a list (argv[0] resolved via PATH, the rest quoted
            # with list2cmdline). Pre-joining a list to a string would double-
            # quote argv[0] and break executable resolution — so keep the list.
            self._proc = PtyProcess.spawn(
                self.argv, cwd=self.cwd, env=full_env,
                dimensions=(self.rows, self.cols))
        else:
            from ptyprocess import PtyProcess  # lazy: Unix dependency (macOS)
            argv = self.argv if isinstance(self.argv, list) else [self.argv]
            self._proc = PtyProcess.spawn(
                argv, cwd=self.cwd, env=full_env,
                dimensions=(self.rows, self.cols))

    def _read_chunk(self) -> bytes:
        data = self._proc.read(4096)          # blocks until data / EOF
        if data is None:
            return b""
        if isinstance(data, str):             # pywinpty returns str
            return data.encode("utf-8", "replace")
        return data                           # ptyprocess returns bytes

    def write(self, data) -> None:
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        payload = data if IS_WINDOWS else data.encode("utf-8", "replace")
        try:
            self._proc.write(payload)
        except (OSError, EOFError):
            pass

    def send_line(self, text: str) -> None:
        """Type `text` then submit with Enter. The Enter is a SEPARATE write
        after a short delay — a TUI treats a trailing newline in the same write
        as pasted content (it lands in the input box unsubmitted), but a
        discrete Enter keystroke submits. This is the chat doorbell."""
        self.write(text)
        time.sleep(0.25)
        self.write("\r")

    def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            pass

    @property
    def pid(self):
        return getattr(self._proc, "pid", None)

    def alive(self) -> bool:
        try:
            return bool(self._alive and self._proc.isalive())
        except Exception:
            return False

    # ---- output fan-out ----

    def _pump(self):
        """Reader thread: PTY output → rolling buffer + every subscriber."""
        while True:
            try:
                chunk = self._read_chunk()
            except EOFError:
                break
            except Exception:
                break
            if not chunk:
                if not self.alive():
                    break
                continue
            self._last_output = time.time()
            with self._lock:
                self._buf.extend(chunk)
                if len(self._buf) > _BUFFER_MAX:
                    del self._buf[:len(self._buf) - _BUFFER_MAX]
                subs = list(self._subs)
            for q in subs:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    pass
        self._alive = False
        with self._lock:
            subs = list(self._subs)
        for q in subs:                        # unblock any waiting streamers
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    def subscribe(self) -> tuple[bytes, queue.Queue]:
        """Register a live subscriber. Returns (snapshot, queue): the current
        buffer to repaint the screen, then the queue delivers new chunks (None
        = the process ended)."""
        q: queue.Queue = queue.Queue(maxsize=1024)
        with self._lock:
            snapshot = bytes(self._buf)
            self._subs.append(q)
        return snapshot, q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    # ---- teardown ----

    def kill(self) -> None:
        """Kill the whole process tree (owning the PTY, terminating the top
        process alone would orphan node/codex children)."""
        self._alive = False
        pid = self.pid
        if pid and IS_WINDOWS:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass

    def info(self) -> dict:
        return {
            "id": self.id, "label": self.label, "cwd": self.cwd or "",
            "pid": self.pid, "alive": self.alive(),
            "rows": self.rows, "cols": self.cols,
            "created": self.created, "meta": self.meta,
            # Seconds since the process last produced output — a cheap
            # "is it working" signal for the UI (agentchattr-style activity).
            "idleSeconds": round(time.time() - self._last_output, 1),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def create(argv, cwd=None, env=None, rows=40, cols=120, label="",
           meta=None) -> PtySession:
    sess = PtySession(argv, cwd=cwd, env=env, rows=rows, cols=cols,
                      label=label, meta=meta)
    with _REG_LOCK:
        _REGISTRY[sess.id] = sess
    return sess


def get(pty_id: str) -> PtySession | None:
    with _REG_LOCK:
        return _REGISTRY.get(pty_id)


def list_sessions() -> list[dict]:
    with _REG_LOCK:
        sessions = list(_REGISTRY.values())
    return [s.info() for s in sessions]


def kill(pty_id: str) -> bool:
    with _REG_LOCK:
        sess = _REGISTRY.pop(pty_id, None)
    if sess is None:
        return False
    sess.kill()
    return True


def reap() -> None:
    """Drop registry entries whose process has exited."""
    with _REG_LOCK:
        dead = [k for k, s in _REGISTRY.items() if not s.alive()]
        for k in dead:
            _REGISTRY.pop(k, None)
