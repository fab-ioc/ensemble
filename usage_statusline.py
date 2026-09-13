"""Status-line command for hub-launched Claude agents: keeps the newest plan
windows Claude Code has seen in one file the hub reads (``usage.py``).

Claude Code runs its status-line command after each turn and hands it a JSON on
stdin that carries ``rate_limits.five_hour`` / ``seven_day`` —
``used_percentage`` and ``resets_at`` — from its own latest API response. That
is the same account-wide figure ``/usage`` shows, with no network call and no
credentials, so the hub asks the rate-limited usage endpoint only when this
file has gone quiet.

Usage (set by the hub in the agent's per-launch ``--settings``)::

    python usage_statusline.py <target.json>

Prints nothing: the agent runs headless and the hub reads its screen, so a
status line would only add a line the hub's screen reading has to ignore.
Standard library only, never raises, always exits 0 — a status-line command
that fails must not disturb the agent.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path


def _as_of(data: dict) -> float | None:
    """When these numbers were true.

    ``rate_limits`` come from the session's latest API response, but the
    status line is also redrawn without one (start-up, resume, a mode change),
    so the time of *this* run would make an old reading look new. The session's
    transcript is appended with every response, so its modification time is
    the reading's time. No transcript yet means no response yet: nothing to say.
    """
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        return min(time.time(), os.stat(path).st_mtime)
    except OSError:
        return None


def record(data: dict, target: Path) -> bool:
    """Write ``data``'s rate limits to ``target`` when they are newer than what
    it holds. Returns whether it wrote."""
    limits = data.get("rate_limits")
    if not isinstance(limits, dict) or not any(
            isinstance(w, dict) and w.get("used_percentage") is not None
            for w in limits.values()):
        return False
    as_of = _as_of(data)
    if as_of is None:
        return False
    try:
        with open(target, encoding="utf-8") as fh:
            held = float((json.load(fh) or {}).get("asOf") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        held = 0.0
    # Every hub agent writes here. Keep the newest reading, not the last writer:
    # an agent resumed after a long pause redraws with its old numbers.
    if held >= as_of:
        return False
    model = data.get("model")
    text = json.dumps({
        "asOf": as_of,
        "writtenAt": time.time(),
        "sessionId": data.get("session_id"),
        "model": model.get("id") if isinstance(model, dict) else None,
        "version": data.get("version"),
        "rateLimits": limits,
    })
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        # Windows refuses the replace while the hub has the file open for its
        # read; that read is over in a moment.
        for attempt in range(5):
            try:
                os.replace(tmp, target)
                return True
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    try:
        raw = sys.stdin.buffer.read()
        if len(argv) < 2:
            return 0
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        if isinstance(data, dict):
            record(data, Path(argv[1]))
    except Exception:                                        # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
