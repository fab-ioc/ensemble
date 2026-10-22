"""Who may start a real agent (Claude Code, Codex) from this process.

A test that builds an in-process hub (``dashboard.Handler`` on a test port)
reaches the same launch paths as the real hub: a PO wake, a resume, a board
start, a due line, a gate. On 2026-10-22 a suite run started real Codex
agents with an empty CODEX_HOME, each of which opened a ChatGPT sign-in page
in the browser, and the run hung on them for 45 min (#257).

So starting an agent is refused unless this process is a hub: ``allow()``
is called by ``dashboard.main()`` alone, which no test runs with a real
listener. A test that wants real agents on purpose runs a hub through
``main()`` (tests/slash_commands_e2e.py) or wraps the launch in
``opted_in()``.

Two layers:

* ``check(argv, env)`` before a launch: refused under a test, and, in a hub,
  refused with "not signed in" when the agent has no login to use, instead
  of an agent that opens a sign-in page nobody asked for.
* ``install_spawn_guard()`` under every process start (subprocess.Popen and
  the PTY spawn): a command that would run an agent is refused while launches
  are not allowed, whatever code path reached it. Each one refused is kept in
  ``REFUSED`` (with the test that asked) for tests/test_no_agent_launch.py.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

AGENTS = ("claude", "codex")
# What starts a process that may in turn start an agent: a shell running a
# launch script, Windows Terminal, a Unix terminal opener.
SHELLS = {"powershell", "pwsh", "cmd", "wt", "bash", "sh", "zsh", "osascript", "open"}
SCRIPT_EXT = (".ps1", ".sh", ".command", ".cmd", ".bat")

_lock = threading.Lock()
_allowed = False                    # set by the hub's main()
_opt_in = threading.local()         # a test's opted_in() on this thread
REFUSED: list[dict] = []            # every refused launch: {argv, test, stack}


class LaunchRefused(PermissionError):
    """An agent start this process may not make. An OSError, as a spawn that
    fails is: callers that handle a missing program handle this too."""


class NotSignedIn(LaunchRefused):
    """The agent has no login to use: it would open a sign-in page instead."""


def allow() -> None:
    """This process is a hub: its launches start real agents."""
    global _allowed
    _allowed = True


def allowed() -> bool:
    return _allowed or bool(getattr(_opt_in, "on", 0))


@contextlib.contextmanager
def opted_in():
    """A test that starts real agents on purpose, on this thread only."""
    _opt_in.on = getattr(_opt_in, "on", 0) + 1
    try:
        yield
    finally:
        _opt_in.on -= 1


def _name(token: str) -> str:
    base = re.split(r"[\\/]", str(token).strip().strip("'\""))[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".js"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _script_runs_agent(path: str) -> bool:
    """A launch script whose command line runs an agent (`& 'codex' ...` in a
    .ps1; `codex ...` or `exec claude ...` in a shell script)."""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read(256 * 1024)
    except OSError:
        return False
    for line in text.splitlines():
        words = line.strip().split()
        while words and words[0] in ("&", "exec", "command"):
            words = words[1:]
        if words and _name(words[0]) in AGENTS:
            return True
    return False


def agent_in(argv) -> str:
    """The agent a command would start ("claude", "codex"), else ""."""
    if isinstance(argv, (str, bytes)):
        argv = (argv.decode("utf-8", "replace") if isinstance(argv, bytes) else argv).split()
    try:
        tokens = [str(a) for a in argv]
    except TypeError:
        return ""
    if not tokens:
        return ""
    first = _name(tokens[0])
    if first in AGENTS:
        return first
    if first not in SHELLS and first not in ("python", "py", "pythonw", "node"):
        return ""
    rest = tokens[1:]
    if first in SHELLS:
        # A command handed over as one string: `bash -lc "exec codex ..."`,
        # osascript's "'/usr/local/bin/claude' --resume x" for iTerm.
        rest = [w for t in rest for w in (_words(t) if any(c.isspace() for c in t) else [t])]
    for t in rest:
        n = _name(t)
        if first in SHELLS and n in AGENTS:
            return n
        if t.lower().strip("'\"").endswith(SCRIPT_EXT) and _script_runs_agent(t.strip("'\"")):
            return "agent"
    return ""


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _current_test() -> str:
    """The test asking: on this thread's stack, else on the main thread's (a
    test's hub answers its requests on threads of its own)."""
    import unittest
    main = sys._current_frames().get(threading.main_thread().ident)
    for f in (sys._getframe(1), main):
        while f is not None:
            me = f.f_locals.get("self")
            if isinstance(me, unittest.TestCase):
                return me.id()
            f = f.f_back
    return ""


def _refuse(argv, agent: str, where: str) -> None:
    entry = {"argv": [str(a) for a in argv] if not isinstance(argv, str) else [argv],
             "agent": agent, "where": where, "test": _current_test(),
             "stack": "".join(traceback.format_stack(limit=25)[:-2])}
    with _lock:
        REFUSED.append(entry)
    log = os.environ.get("ENSEMBLE_LAUNCH_REFUSED_LOG")
    if log:
        with contextlib.suppress(OSError), open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    raise LaunchRefused(f"{agent or 'an agent'} was not started: this process is not a hub "
                        f"(a test; see agent_launch.py)")


def _has_env(env: dict | None, *names: str) -> bool:
    def on(v):
        return bool(v) and str(v).strip().lower() not in ("0", "false", "no", "off")
    return any(on((env or {}).get(n) or os.environ.get(n)) for n in names)


# A yes from the agent's CLI, kept a while: on a Mac the Claude login is in the
# keychain (no file), and a boot that restores many rooms asks once, not per room.
SIGNED_IN_KEEP_S = 600
_cli_yes: dict[tuple, float] = {}


def signed_in(agent: str, env: dict | None = None) -> bool:
    """Has this agent a login it can use without asking anyone? A Codex
    with no auth.json in its CODEX_HOME starts a ChatGPT sign-in in the
    browser; a Claude with no credentials asks for a login. Its login file
    says yes at once; without one, the agent's own CLI is asked (as the
    first-run check does), and only its "no" is a no."""
    if _login_file(agent, env or {}):
        return True
    import app_setup
    exe = shutil.which(agent)
    if not exe:
        return True             # not installed: the launch fails on its own
    full = {**os.environ, **(env or {})}
    key = (agent, full.get("CODEX_HOME", ""), full.get("CLAUDE_CONFIG_DIR", ""))
    if time.monotonic() - _cli_yes.get(key, -SIGNED_IN_KEEP_S) < SIGNED_IN_KEEP_S:
        return True
    said = []

    def run(*a, **kw):
        r = subprocess.run(*a, env=full, **kw)
        said.append(r)
        return r
    answer = app_setup._signed_in(agent, exe, run)
    if answer is False and agent == "claude" and said:
        # Only a JSON "loggedIn": false is a no: a CLI too old for
        # `auth status` exits 1 too.
        try:
            json.loads(said[-1].stdout or "")
        except ValueError:
            answer = None
    if answer:
        _cli_yes[key] = time.monotonic()
    return answer is not False


def _login_file(agent: str, env: dict) -> bool:
    """A login the agent finds without asking: its login file or a key."""
    if agent == "codex":
        if _has_env(env, "OPENAI_API_KEY", "CODEX_API_KEY"):
            return True
        home = env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        return (Path(home) / "auth.json").is_file()
    if agent.startswith("claude"):
        if _has_env(env, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"):
            return True
        conf = Path(env.get("CLAUDE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
                    or str(Path.home() / ".claude"))
        if (conf / ".credentials.json").is_file():
            return True
        with contextlib.suppress(OSError, ValueError, AttributeError):
            settings = json.loads((conf / "settings.json").read_text(encoding="utf-8"))
            if settings.get("apiKeyHelper"):
                return True
        return False
    return True


AGENT_NAMES = {"codex": "Codex", "claude": "Claude Code"}
LOGIN_HINT = {"codex": "run `codex login` in a terminal", "claude": "run `claude` in a terminal and log in"}


def check(agent: str, env: dict | None = None) -> None:
    """Before a hub starts ``agent`` (claude, claude-N, codex) with ``env``:
    refused as not signed in when it has no login. Outside a hub this says
    nothing: the spawn guard refuses the process itself, so a test that
    replaces the spawn with a fake still runs the launch code."""
    kind = "codex" if agent == "codex" else "claude" if agent.startswith("claude") else ""
    if not kind or not allowed():
        return
    if not signed_in(kind, env):
        raise NotSignedIn(f"{AGENT_NAMES[kind]} is not signed in on this machine, so it was "
                          f"not started (it would only open a sign-in page): "
                          f"{LOGIN_HINT[kind]}, then start the task again.")


def guard_spawn(argv, where: str) -> None:
    """Under every process start: refuse one that runs an agent outside a hub."""
    if allowed():
        return
    agent = agent_in(argv)
    if agent:
        _refuse(argv, agent, where)


_installed = False


def install_spawn_guard() -> None:
    """Put ``guard_spawn`` under subprocess.Popen and the PTY spawn (pywinpty,
    ptyprocess), once per process. A hub that ``allow()``s passes through."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True
    popen_init = subprocess.Popen.__init__

    def guarded_init(self, args, *a, **kw):
        guard_spawn(args, "subprocess")
        popen_init(self, args, *a, **kw)
    subprocess.Popen.__init__ = guarded_init
    for mod in ("winpty", "ptyprocess"):
        try:
            pty = __import__(mod).PtyProcess
        except Exception:       # noqa: BLE001 — not on this platform
            continue
        spawn = pty.__dict__.get("spawn")
        fn = getattr(spawn, "__func__", None)
        if fn is None:
            continue

        def guarded_spawn(cls, argv, *a, _fn=fn, **kw):
            guard_spawn(argv, "pty")
            return _fn(cls, argv, *a, **kw)
        pty.spawn = classmethod(guarded_spawn)
