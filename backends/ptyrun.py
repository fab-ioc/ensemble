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
import re
import subprocess
import threading
import time
import uuid

IS_WINDOWS = os.name == "nt"

# Keep the last ~512 KB of output so a fresh viewer can repaint the screen.
_BUFFER_MAX = 512 * 1024

# How many death records to remember. A death record is the *evidence* of why a
# terminal ended (exit status + the last screen); it must outlive `reap()`,
# which drops the session itself.
_DEATHS_MAX = 200

# Terminal control sequences, stripped before anyone reads the screen as text:
# CSI (with intermediate bytes — codex's cursor-shape "ESC [ 0 SP q" needs the
# " -/" class), OSC ... BEL/ST (window titles), charset selects, and the lone
# two-byte escapes. Without this a TUI's tail is unmatchable noise.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"          # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL | ST
    r"|\x1b[()][0-9A-Za-z]"             # charset designators
    r"|\x1b[@-Z\\-_]"                   # other two-byte escapes
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(raw: str) -> str:
    """A PTY screen as readable text: escape sequences and control bytes gone."""
    return _CTRL_RE.sub("", _ANSI_RE.sub("", raw))


_console_ready = False
_console_lock = threading.Lock()


def _ensure_windows_console() -> None:
    """pywinpty's ConPTY (CreatePseudoConsole) needs the host process to have a
    console. A windowless host — pythonw.exe, or a service/scheduled-task launch
    without an attached console — has none, and the spawn then panics with
    HRESULT 0x800700BB ("The specified system semaphore name was not found").
    Allocate a hidden console once so every headless spawn succeeds regardless of
    how the server was started. No-op if a console is already attached."""
    global _console_ready
    if _console_ready or not IS_WINDOWS:
        return
    with _console_lock:
        if _console_ready:
            return
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            if not k32.GetConsoleWindow():
                if k32.AllocConsole():
                    hwnd = k32.GetConsoleWindow()
                    if hwnd:
                        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass
        _console_ready = True

_REGISTRY: dict[str, "PtySession"] = {}
_REG_LOCK = threading.Lock()

# Death records, keyed by pty id, oldest first. Kept OUT of the registry on
# purpose: `reap()` drops the session, this survives it.
_DEATHS: dict[str, dict] = {}
_DEATH_LOCK = threading.Lock()
_death_hook = None      # optional callback(record) — the dashboard persists it


def set_death_hook(fn) -> None:
    """Register a callback run once per terminal death, with its record. Runs on
    the dying session's reader thread, so it must be quick and must not raise."""
    global _death_hook
    _death_hook = fn


def _record_death(sess: "PtySession") -> dict:
    """Build (once) the evidence of why a terminal ended and remember it.

    The reader thread and `reap()` can both arrive here for the same session,
    so the "once" is enforced under the session's own lock — otherwise the hook
    fires twice and the task record is written twice."""
    with sess._death_lock:
        if sess._death is not None:
            return sess._death
        # NB: never infer *why* it died from the exit code — a deliberate stop
        # goes through `taskkill /F /T`, so the code is arbitrary. The tail is
        # the evidence; the code is only ever reported alongside it.
        code = sess.exit_code()
        rec = _build_death(sess, code)
        sess._death = rec
    with _DEATH_LOCK:
        _DEATHS[sess.id] = rec
        while len(_DEATHS) > _DEATHS_MAX:
            _DEATHS.pop(next(iter(_DEATHS)))
    hook = _death_hook
    if hook is not None:
        try:
            hook(rec)
        except Exception:
            pass        # a bookkeeping hook must never break teardown
    return rec


def _build_death(sess: "PtySession", code) -> dict:
    return {
        "ptyId": sess.id,
        "label": sess.label,
        "meta": dict(sess.meta),
        "cwd": sess.cwd or "",
        "pid": sess.pid,
        "exitCode": code,
        "killed": bool(sess._killed),
        "endedAt": time.time(),
        "startedAt": sess.created,
        "tail": sess.tail(),
    }


def death_for(pty_id: str) -> dict | None:
    """The death record of a terminal that is no longer in the registry."""
    with _DEATH_LOCK:
        return _DEATHS.get(pty_id)


def forget_death(pty_id: str) -> None:
    """Drop a death record — the death has been dealt with.

    Marking the session killed after the fact would not do it: the record is
    built once, at death, and never rewritten. So stopping a task that had
    already died has to remove the evidence rather than relabel it, or the
    dashboard would keep reporting a death nobody can dismiss."""
    with _DEATH_LOCK:
        _DEATHS.pop(pty_id, None)


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
        self._last_submit = 0.0     # when something was last submitted (see write)
        self._killed = False       # True once someone deliberately killed it
        self._death = None          # the death record, built once at exit
        self._death_lock = threading.Lock()
        self._spawn(env)
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    # ---- backend-specific spawn / io ----

    def _spawn(self, env):
        full_env = {**os.environ, **(env or {})}
        # Never propagate our own Claude Code child-session markers to a spawned
        # agent. Inheriting CLAUDE_CODE_CHILD_SESSION makes the agent think it's a
        # nested child session and turns its transcript saving OFF — so the
        # dashboard can't see the agent's history/cost or resume it. A
        # dashboard-launched agent must be its own top-level session. (Harmless
        # for non-Claude agents, which ignore these vars.)
        for k in [k for k in full_env if k.startswith("CLAUDE_CODE_CHILD")]:
            full_env.pop(k, None)
        if IS_WINDOWS:
            _ensure_windows_console()        # ConPTY needs a console (pythonw has none)
            try:
                from winpty import PtyProcess  # lazy: Windows-only dependency
            except ImportError as e:
                raise RuntimeError(
                    "pywinpty is not installed — headless agents need it. Run: "
                    "py -m pip install -r requirements.txt") from e
            # Pass argv through as-is: pywinpty accepts a str (it shlex-splits,
            # posix=False) or a list (argv[0] resolved via PATH, the rest quoted
            # with list2cmdline). Pre-joining a list to a string would double-
            # quote argv[0] and break executable resolution — so keep the list.
            self._proc = PtyProcess.spawn(
                self.argv, cwd=self.cwd, env=full_env,
                dimensions=(self.rows, self.cols))
        else:
            try:
                from ptyprocess import PtyProcess  # lazy: Unix dependency (macOS)
            except ImportError as e:
                raise RuntimeError(
                    "ptyprocess is not installed — headless agents need it. Run: "
                    "python3 -m pip install -r requirements.txt") from e
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
        if "\r" in data or "\n" in data:
            # Something was submitted to the agent — a human answer typed in
            # the terminal, or the chat doorbell. Keystrokes without an Enter
            # (focus events, a half-typed line) are not an answer.
            self._last_submit = time.time()
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

    @property
    def last_output(self) -> float:
        """When this terminal last printed anything. Readers cache their
        analysis of the screen against it — an idle PTY is scanned once, not on
        every poll."""
        return self._last_output

    def last_submit(self) -> float:
        """When something was last submitted to it (0 if never) — how the
        attention detector knows a report has been answered in the terminal."""
        return getattr(self, "_last_submit", 0.0)

    def alive(self) -> bool:
        try:
            return bool(self._alive and self._proc.isalive())
        except Exception:
            return False

    def exit_code(self):
        """The child's exit status, or None if it isn't known.

        Both backends only publish `exitstatus` once the child has been reaped,
        and `isalive()` is what reaps it — so a death recorded the instant the
        pipe closed would otherwise persist None every time. Give it a few
        moments to settle rather than guessing."""
        for _ in range(5):
            try:
                if self._proc.isalive():
                    return None
                code = self._proc.exitstatus
            except Exception:
                return self._exit_code
            if code is not None:
                self._exit_code = code
                return code
            time.sleep(0.05)
        return self._exit_code

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
        try:
            # The process has just ended and nothing has reaped us yet: this is
            # the only moment the last screen still exists. Record it before the
            # buffer can be dropped — that evidence is the whole point.
            _record_death(self)
        except Exception:
            pass
        finally:
            # Whatever happened above, every open terminal must be released:
            # this None is what ends an SSE stream. Bookkeeping never gets to
            # hang a viewer's terminal.
            with self._lock:
                subs = list(self._subs)
            for q in subs:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass

    def death(self) -> dict | None:
        """This terminal's death record, or None while it is still running."""
        return self._death

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

    def tail(self, lines: int = 40, max_bytes: int = 16384) -> str:
        """The last `lines` non-empty lines of the screen, as readable text.

        The public way to ask "what was it saying?" — nothing outside this class
        touches the buffer. TUIs redraw with bare CRs, so those split lines too;
        escape sequences are stripped, so a message like a usage-limit warning
        comes back as the one line a human would read.
        """
        with self._lock:
            raw = bytes(self._buf[-max_bytes:])
        text = clean_text(raw.decode("utf-8", "replace"))
        out = [ln.strip() for ln in re.split(r"\r\n|\n|\r", text)]
        return "\n".join([ln for ln in out if ln][-lines:])

    # ---- teardown ----

    def kill(self) -> None:
        """Kill the whole process tree (owning the PTY, terminating the top
        process alone would orphan node/codex children)."""
        self._killed = True
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
    """Drop registry entries whose process has exited — capturing the evidence
    first. The reader thread normally records the death the instant the process
    ends; this is the belt-and-braces path for a session that went away without
    the pump noticing (it would otherwise take its last screen with it)."""
    with _REG_LOCK:
        dead = [(k, s) for k, s in _REGISTRY.items() if not s.alive()]
        for k, _ in dead:
            _REGISTRY.pop(k, None)
    # Capture outside the registry lock: it decodes a buffer and may write to
    # disk, and every /api/pty/stream and room-liveness check goes through
    # get(), which that lock would block.
    for _, s in dead:
        try:
            _record_death(s)
        except Exception:
            pass
