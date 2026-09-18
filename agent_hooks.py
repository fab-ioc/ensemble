"""What each hub-launched Claude agent last said about itself, through its hooks.

Claude Code runs ``agent_hook.py`` when the agent takes a prompt, stops to ask,
finishes its turn or ends (the hub registers the hooks in the agent's
``--settings`` file, see ``dashboard._agent_hooks``), and that script posts the
event to ``POST /api/agent/hook``. This module turns the events into one of four
states per terminal:

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

A wait is not just the last event. Several things run in one session — the
agent, its subagents, tool calls made side by side — and one of them finishing
says nothing about a question another one asked. So each ask is kept until
*its own* answer arrives: the same tool call coming back, or the asker's turn
ending. While any ask is open the agent is ``waiting``; otherwise it is what
the agent itself (not a subagent) last said.

``attention.py`` reads this in preference to the agent's screen, and tells this
module when the screen or Claude's status file has since shown otherwise
(``invalidate``): what was contradicted stays dropped until a new hook says
something. Kept in memory only, per terminal: after a hub restart there is
nothing here and the screen rules until the next hook; a relaunched agent has a
new terminal, so nothing an older run said can stick to it. Codex has no hooks
and never appears here.

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
# ptyId -> {room, identity, sessionId, base, waits}. ``base`` is what the agent
# itself last said, {state, detail, event, at, stale}; ``waits`` the open asks,
# [{agent, toolUseId, tool, detail, event, at}].
_STATES: dict[str, dict] = {}
_RECENT: collections.deque = collections.deque(maxlen=200)   # newest events, for /api/agent/hooks


def state_of(event: dict) -> tuple[str, str]:
    """``(state, detail)`` an event speaks of; ``("", "")`` when it says nothing
    about what the agent is doing (an event this module does not know, a notice
    that is only news). Whose state, and what it does to an open ask, is
    ``record``'s business."""
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
            return "idle", kind or "notification"
        return "", ""
    if name == "Stop":
        return "idle", ""
    if name == "StopFailure":
        return "idle", (event.get("error_type") or event.get("error") or "api_error")[:80]
    if name == "SessionStart":
        # A compaction starts a "session" in the middle of a turn.
        return ("", "") if event.get("source") == "compact" else ("idle", event.get("source") or "")
    if name == "SessionEnd":
        return "ended", event.get("reason") or ""
    return "", ""


def _answers(wait: dict, agent: str, tool_use_id: str, tool: str) -> bool:
    """Whether a tool call coming back is the one ``wait`` was asked for: the
    same asker, and the same call — by its id when both sides have one, else by
    the tool's name. A notification's ask names no tool; any call of its asker
    coming back ends it."""
    if wait["agent"] != agent:
        return False
    if wait["toolUseId"] and tool_use_id:
        return wait["toolUseId"] == tool_use_id
    return not wait["tool"] or wait["tool"] == tool


def _apply(held: dict, event: dict, state: str, detail: str, at: float) -> None:
    name = event["hook_event_name"]
    agent = event.get("agent_id") or ""          # "" is the agent itself, else a subagent
    tool_use_id, tool = event.get("tool_use_id") or "", event.get("tool_name") or ""
    waits, base = held["waits"], held["base"]
    own_word = not agent and at >= (base or {}).get("at", 0)
    if state == "waiting":
        if name == "Notification":
            # The late echo of an ask already open (measured: about 6 s after
            # its PermissionRequest) adds nothing; alone, it is the ask.
            if waits:
                return
            tool_use_id = tool = ""
        waits[:] = [w for w in waits if not (w["agent"] == agent and w["toolUseId"] == tool_use_id
                                             and w["tool"] == tool)]
        waits.append({"agent": agent, "toolUseId": tool_use_id, "tool": tool, "detail": detail,
                      "event": name, "at": at})
        return
    if state == "working":
        if name == "UserPromptSubmit":
            # A prompt went in, so no dialog of the agent's own was in the way.
            waits[:] = [w for w in waits if w["agent"] or w["at"] > at]
        else:
            waits[:] = [w for w in waits
                        if w["at"] > at or not _answers(w, agent, tool_use_id, tool)]
        # A subagent may run on after the agent's own turn ended, and nothing
        # says when it is done: its tool calls are not the agent's word.
        if own_word:
            held["base"] = {"state": state, "detail": detail, "event": name, "at": at, "stale": False}
        return
    if state == "idle":
        if name == "Notification":
            # "Still at the prompt" — also sent while a question sits unanswered.
            if waits or not own_word:
                return
        else:
            waits[:] = [w for w in waits if w["agent"] or w["at"] > at]     # its turn is over
        if own_word:
            held["base"] = {"state": state, "detail": detail, "event": name, "at": at, "stale": False}
        return
    if state == "ended":
        waits.clear()
        held["base"] = {"state": state, "detail": detail, "event": name, "at": at, "stale": False}


def _effective(held: dict | None) -> dict | None:
    """The one state a terminal's entry amounts to, or None."""
    if not held:
        return None
    who = {"room": held["room"], "identity": held["identity"], "sessionId": held["sessionId"]}
    if held["waits"]:
        w = max(held["waits"], key=lambda w: w["at"])
        return {"state": "waiting", "detail": w["detail"], "event": w["event"], "at": w["at"],
                "waits": len(held["waits"]), **who}
    base = held["base"]
    if not base or base["stale"]:
        return None
    return {"state": base["state"], "detail": base["detail"], "event": base["event"],
            "at": base["at"], "waits": 0, **who}


def record(payload, owner, now: float | None = None) -> dict:
    """Take one post of ``agent_hook.py``. ``owner(pty_id)`` returns the
    ``(room, identity)`` of a terminal the hub owns, or None. Returns
    ``{"ok": bool, ...}``; never raises on a malformed payload."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), dict):
        return {"ok": False, "error": "bad_payload"}
    # Only text is read from an event; a field of any other type is not there.
    event = {k: v for k, v in payload["event"].items() if isinstance(k, str) and isinstance(v, str)}
    room, identity, pty_id = (payload.get(k).strip() if isinstance(payload.get(k), str) else ""
                              for k in ("room", "identity", "ptyId"))
    name = event.get("hook_event_name")
    if not (room and identity and pty_id and name):
        return {"ok": False, "error": "bad_payload"}
    try:
        owned = owner(pty_id)
    except Exception:
        owned = None
    if not owned or tuple(owned) != (room, identity):
        return {"ok": False, "error": "unknown_agent"}
    at = payload.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not now - 60 <= at <= now:
        at = now                # a clock that cannot be the hook's own
    at = float(at)
    state, detail = state_of(event)
    with _LOCK:
        held = _STATES.pop(pty_id, None) or {"room": room, "identity": identity, "sessionId": "",
                                             "base": None, "waits": []}
        _STATES[pty_id] = held                      # newest last
        before = _effective(held)
        if state:
            _apply(held, event, state, detail, at)
            if not event.get("agent_id") and event.get("session_id"):
                held["sessionId"] = event["session_id"]
        after = _effective(held)
        _RECENT.append({"ptyId": pty_id, "room": room, "identity": identity, "event": name,
                        "says": state, "detail": detail, "agentId": event.get("agent_id") or "",
                        "toolUseId": event.get("tool_use_id") or "",
                        "state": (after or {}).get("state", ""), "at": at, "receivedAt": now})
        while len(_STATES) > _MAX_AGENTS:
            _STATES.pop(next(iter(_STATES)))
    changed = (before or {}).get("state") != (after or {}).get("state") or \
        (before or {}).get("at") != (after or {}).get("at")
    return {"ok": True, "state": (after or {}).get("state", ""), "changed": changed}


def state_for(pty_id: str) -> dict | None:
    """What the agent in this terminal is doing by its own hooks —
    ``{state, detail, event, at, waits, room, identity, sessionId}`` — or None
    when it has said nothing, or nothing that still stands."""
    if not pty_id:
        return None
    with _LOCK:
        return _effective(_STATES.get(pty_id))


def invalidate(pty_id: str, at: float) -> None:
    """The state of time ``at`` was seen to be out of date (``attention`` says
    how): it, and every ask open since before it, stays dropped until a new
    hook says something. Without this a contradicted state would be believed
    again as soon as the evidence against it stopped being visible."""
    with _LOCK:
        held = _STATES.get(pty_id)
        if not held:
            return
        held["waits"][:] = [w for w in held["waits"] if w["at"] > at]
        if held["base"] and held["base"]["at"] <= at:
            held["base"]["stale"] = True


def forget(pty_id: str) -> None:
    with _LOCK:
        _STATES.pop(pty_id, None)


def snapshot() -> dict:
    """Every terminal's state and the newest events, for ``GET /api/agent/hooks``."""
    with _LOCK:
        return {"states": {k: s for k, v in _STATES.items() if (s := _effective(v))},
                "recent": [dict(e) for e in _RECENT]}


def reset() -> None:
    with _LOCK:
        _STATES.clear()
        _RECENT.clear()
