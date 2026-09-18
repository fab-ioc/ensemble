"""Hook command for hub-launched Claude agents: tells the hub the moment the
agent starts working, stops to ask, finishes its turn or ends.

Claude Code runs a hook command at exactly those moments and hands it a JSON on
stdin (``hook_event_name`` and the event's own fields). This script posts the
few fields the hub needs to its loopback endpoint, ``POST /api/agent/hook``,
with who is speaking: the hub puts ``ENSEMBLE_HOOK_URL``, ``ENSEMBLE_HOOK_ROOM``
and ``ENSEMBLE_HOOK_IDENTITY`` in the agent's environment at launch, and the
terminal adds its own ``ENSEMBLE_PTY_ID``. ``attention.py`` then prefers what
the agent said over what its screen looks like (see ``agent_hooks.py``).

Usage (set by the hub in the agent's per-launch ``--settings``)::

    python agent_hook.py

Fire and forget. One attempt, at most ``CONNECT_TIMEOUT`` to connect and
``TOTAL_TIMEOUT`` for everything, enforced by a watchdog that ends the process.
Prints nothing and always exits 0: Claude Code reads a hook's output as
feedback (for ``UserPromptSubmit`` and ``SessionStart`` it goes into the
conversation) and exit code 2 as "block this". A hub that is down, slow or
restarting must never hold the agent up, so a lost event is simply lost — the
hub's screen reading covers for it. Without the environment above (a Claude
started by hand with the same settings) it does nothing at all. Standard
library only.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

# A loopback connect takes well under a millisecond. Windows keeps retrying a
# closed port for about two seconds rather than fail at once, so with the hub
# stopped this is what each hook costs.
CONNECT_TIMEOUT = 0.5
# The hub answers in a few milliseconds. Once the request is sent the event is
# delivered whether or not the answer is waited for, so giving up early loses
# nothing.
TOTAL_TIMEOUT = 1.0

# What the hub reads. A hook's stdin can be megabytes (a Write's whole file, a
# tool's whole output); none of that is the hub's business.
_FIELDS = ("hook_event_name", "session_id", "notification_type", "tool_name",
           "tool_use_id", "source", "reason", "error", "error_type",
           "permission_mode", "agent_id", "agent_type")
_TEXT_LIMIT = 300


def trimmed(data: dict) -> dict:
    """The fields of a hook's input the hub uses, short strings only."""
    out = {}
    for k in _FIELDS:
        v = data.get(k)
        if isinstance(v, (str, int, float, bool)):
            out[k] = v[:_TEXT_LIMIT] if isinstance(v, str) else v
    msg = data.get("message")
    if isinstance(msg, str):
        out["message"] = msg[:_TEXT_LIMIT]
    return out


def post(url: str, body: bytes, deadline: float) -> bool:
    """One POST of ``body`` to a plain-http ``url``; whether the hub took it.
    Never raises. ``deadline`` is on ``time.monotonic()``."""
    sock = None
    try:
        u = urlsplit(url)
        if u.scheme != "http" or not u.hostname:
            return False
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        head = (f"POST {path} HTTP/1.1\r\nHost: {u.netloc}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
        sock = socket.create_connection(
            (u.hostname, u.port or 80),
            timeout=max(0.05, min(CONNECT_TIMEOUT, deadline - time.monotonic())))
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        sock.sendall(head.encode("ascii") + body)
        # The status line is read so the hub is not left writing to a closed
        # socket; what it says changes nothing here.
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        return sock.recv(64).split(b" ")[1:2] == [b"200"]
    except Exception:                                        # noqa: BLE001
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def main() -> int:
    started = time.time()
    # Whatever happens below — stdin that never closes, a hub that accepts and
    # then says nothing — the agent gets its hook back in time, as a success.
    watchdog = threading.Timer(TOTAL_TIMEOUT, os._exit, (0,))
    watchdog.daemon = True
    watchdog.start()
    deadline = time.monotonic() + TOTAL_TIMEOUT
    try:
        url = os.environ.get("ENSEMBLE_HOOK_URL", "")
        room = os.environ.get("ENSEMBLE_HOOK_ROOM", "")
        identity = os.environ.get("ENSEMBLE_HOOK_IDENTITY", "")
        if not (url and room and identity):
            return 0
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        if not isinstance(data, dict) or not isinstance(data.get("hook_event_name"), str):
            return 0
        body = json.dumps({
            "room": room, "identity": identity,
            "ptyId": os.environ.get("ENSEMBLE_PTY_ID", ""),
            # When the hook ran, by the agent's clock (the hub's too: loopback).
            # Background hooks can reach the hub out of order; this orders them.
            "at": started,
            "event": trimmed(data),
        }).encode("utf-8")
        post(url, body, deadline)
    except BaseException:                                    # noqa: BLE001 — an interrupt too: exit 0, silent
        pass
    return 0


if __name__ == "__main__":
    # Straight out, without the interpreter's teardown: the agent is waiting.
    os._exit(main())
