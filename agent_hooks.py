"""What each hub-launched Claude agent last said about itself, through its hooks.

Claude Code runs ``agent_hook.py`` when the agent takes a prompt, stops to ask,
finishes its turn or ends (the hub registers the hooks in the agent's
``--settings`` file, see ``dashboard._agent_hook_settings``), and that script
posts the event to ``POST /api/agent/hook``. This module turns the event into
one of four states and keeps the last one per terminal:

``working``
    It took a prompt, or a tool call just came back.
``waiting``
    It stopped to ask a person: a permission prompt, a question
    (``AskUserQuestion``), a plan to approve, an MCP server's dialog.
``idle``
    It finished its turn (or the turn ended on an API error) and sits at its
    prompt.
``ended``
    The session is over.

``attention.py`` reads this in preference to the agent's screen, under the
staleness rules written there. Kept in memory only, per terminal: after a hub
restart there is nothing here and the screen rules until the next hook; a
relaunched agent has a new terminal, so nothing an older run said can stick to
it. Codex has no hooks and never appears here.

An event is accepted only from a terminal the hub owns whose room and identity
match what the hook claims, so a stray or forged post can at most misreport the
agent that sent it.
"""
from __future__ import annotations

import collections
import re
import threading
import time

STATES = ("working", "waiting", "idle", "ended")

# Tools that put a question to a person and wait: their PreToolUse is the ask.
ASKING_TOOLS = ("AskUserQuestion", "ExitPlanMode")

# Notification types that mean "a person has to answer", and the one that only
# says the agent has been sitting at its prompt.
_NOTIFY_WAITING = ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog",
                   "agent_needs_input")
_NOTIFY_IDLE = ("idle_prompt",)
# Older Claude Code sends the sentence without a type.
_MSG_WAITING = re.compile(r"needs your (?:permission|approval|attention)|permission to use", re.I)
_MSG_IDLE = re.compile(r"waiting for your input", re.I)

_MAX_AGENTS = 500
_LOCK = threading.Lock()
_STATES: dict[str, dict] = {}                      # ptyId -> the agent's last state
_RECENT: collections.deque = collections.deque(maxlen=200)   # newest events, for /api/agent/hooks


def state_of(event: dict, previous: str = "") -> tuple[str, str]:
    """``(state, detail)`` an event puts the agent in; ``("", "")`` when it
    changes nothing (an event this module does not know, a notice that is only
    news)."""
    state, detail = _state_of(event, previous)
    if state == "working" and event.get("agent_id") and previous != "waiting":
        # A subagent's tool call. It may run in the background after the agent
        # itself finished its turn, and nothing says when it is done — so it
        # ends a wait (its prompt was answered) and changes nothing else.
        return "", ""
    return state, detail


def _state_of(event: dict, previous: str) -> tuple[str, str]:
    name = event.get("hook_event_name") or ""
    if name == "UserPromptSubmit":
        return "working", ""
    if name == "PreToolUse":
        tool = event.get("tool_name") or ""
        return ("waiting", tool) if tool in ASKING_TOOLS else ("working", tool)
    if name in ("PostToolUse", "PostToolUseFailure"):
        return "working", event.get("tool_name") or ""
    if name == "PermissionRequest":
        return "waiting", event.get("tool_name") or "permission_prompt"
    if name == "Notification":
        kind = event.get("notification_type") or ""
        msg = event.get("message") or ""
        if kind in _NOTIFY_WAITING or (not kind and _MSG_WAITING.search(msg)):
            return "waiting", kind or "notification"
        if kind in _NOTIFY_IDLE or (not kind and _MSG_IDLE.search(msg)):
            # "Still at the prompt" — also sent while a question sits
            # unanswered, which stays a question.
            return ("", "") if previous == "waiting" else ("idle", kind or "notification")
        return "", ""
    if name == "Stop":
        return "idle", ""
    if name == "StopFailure":
        return "idle", str(event.get("error_type") or event.get("error") or "api_error")[:80]
    if name == "SessionStart":
        # A compaction starts a "session" in the middle of a turn.
        return ("", "") if event.get("source") == "compact" else ("idle", event.get("source") or "")
    if name == "SessionEnd":
        return "ended", event.get("reason") or ""
    return "", ""


def record(payload, owner, now: float | None = None) -> dict:
    """Take one post of ``agent_hook.py``. ``owner(pty_id)`` returns the
    ``(room, identity)`` of a terminal the hub owns, or None. Returns
    ``{"ok": bool, ...}``; never raises on a malformed payload."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), dict):
        return {"ok": False, "error": "bad_payload"}
    event = payload["event"]
    room, identity, pty_id = (str(payload.get(k) or "").strip()
                              for k in ("room", "identity", "ptyId"))
    name = event.get("hook_event_name")
    if not (room and identity and pty_id and isinstance(name, str) and name):
        return {"ok": False, "error": "bad_payload"}
    try:
        owned = owner(pty_id)
    except Exception:
        owned = None
    if not owned or tuple(owned) != (room, identity):
        return {"ok": False, "error": "unknown_agent"}
    try:
        at = float(payload.get("at"))
    except (TypeError, ValueError):
        at = now
    if not now - 60 <= at <= now:
        at = now                # a clock that cannot be the hook's own
    with _LOCK:
        held = _STATES.get(pty_id)
        state, detail = state_of(event, (held or {}).get("state", ""))
        late = bool(held and at < held["at"])
        _RECENT.append({"ptyId": pty_id, "room": room, "identity": identity, "event": name,
                        "state": state, "detail": detail, "at": at, "receivedAt": now,
                        "late": late})
        if not state or late:
            # Nothing to change, or an event from before the one already held
            # (background hooks can arrive out of order).
            return {"ok": True, "state": (held or {}).get("state", ""), "changed": False}
        _STATES.pop(pty_id, None)
        _STATES[pty_id] = {"state": state, "detail": detail, "event": name, "at": at,
                           "receivedAt": now, "room": room, "identity": identity,
                           "sessionId": str(event.get("session_id") or "")}
        while len(_STATES) > _MAX_AGENTS:
            _STATES.pop(next(iter(_STATES)))
    return {"ok": True, "state": state, "changed": True}


def state_for(pty_id: str) -> dict | None:
    """The last state the agent in this terminal reported, or None."""
    if not pty_id:
        return None
    with _LOCK:
        held = _STATES.get(pty_id)
        return dict(held) if held else None


def forget(pty_id: str) -> None:
    with _LOCK:
        _STATES.pop(pty_id, None)


def snapshot() -> dict:
    """Every held state and the newest events, for ``GET /api/agent/hooks``."""
    with _LOCK:
        return {"states": {k: dict(v) for k, v in _STATES.items()},
                "recent": [dict(e) for e in _RECENT]}


def reset() -> None:
    with _LOCK:
        _STATES.clear()
        _RECENT.clear()
