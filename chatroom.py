"""Multi-agent chat rooms — pairing live agent sessions into a collaborative
"duo" (plus the human) that coordinate through an MCP-backed message log.

A *room* links two or more live agent sessions and the human participant
(`user`). Agents exchange messages by calling the `chat_send` / `chat_read` MCP
tools; each agent's identity is resolved from a per-session bearer token, so the
server always knows who is speaking. When a message is posted, the dashboard
rings the recipient's terminal (the keystroke "doorbell") so the agent wakes to
read it. An agent can address `user` to pull the human in — that pauses the
autonomous relay until the human replies from the browser panel.

Design notes
------------
* **Hand-off = a `chat_send` call.** We don't scrape terminals to guess when an
  agent finished; the agent signals turn-end by sending. This is reliable and
  keeps the loop explicit.
* **Loop guard.** `hop_count` tracks consecutive agent→agent hands-off with no
  human turn; once it reaches `max_hops` the room pauses and waits for the
  human, so a two-agent loop can't run away.
* **Persistence.** Each room is a JSON file under ``DASHBOARD_DIR/rooms`` so
  rooms (and their transcripts) survive a server restart. All mutation goes
  through a single process-wide lock — duo-scale volume, so read-modify-write
  per op is fine.

This module is pure state + policy; the HTTP endpoints, the MCP transport, and
the doorbell live in ``dashboard.py`` (which injects a ``ring`` callback).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from pathlib import Path

from backends.base import DASHBOARD_DIR

ROOMS_DIR = DASHBOARD_DIR / "rooms"
HUMAN_IDENTITY = "user"
DEFAULT_MAX_HOPS = 24

_LOCK = threading.RLock()


def _now() -> float:
    return time.time()


def _room_path(room_id: str) -> Path:
    return ROOMS_DIR / f"{room_id}.json"


def _read(room_id: str) -> dict | None:
    p = _room_path(room_id)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write(room: dict) -> None:
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    p = _room_path(room["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(room, indent=2), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Room lifecycle
# ---------------------------------------------------------------------------

def create_room(title: str, members: list[dict], max_hops: int = DEFAULT_MAX_HOPS) -> dict:
    """Create a room from a list of agent members.

    Each ``member`` is a dict describing a live agent session:
        {identity, agent, pid, sessionId, cwd, label}
    The human participant (`user`) is always added implicitly. Every agent is
    issued a bearer token used to authenticate its MCP calls.
    """
    with _LOCK:
        room_id = "room-" + uuid.uuid4().hex[:8]
        participants = []
        tokens = {}
        used: set[str] = {HUMAN_IDENTITY}
        for m in members:
            base = (m.get("identity") or m.get("agent") or "agent").strip()
            ident = base
            n = 2
            while ident in used:            # two Claudes → claude, claude-2
                ident = f"{base}-{n}"
                n += 1
            used.add(ident)
            participants.append({
                "identity": ident,
                "kind": "agent",
                "agent": m.get("agent", ""),
                "model": m.get("model", ""),
                "pid": m.get("pid"),
                "sessionId": m.get("sessionId", ""),
                "cwd": m.get("cwd", ""),
                "label": m.get("label", ""),
            })
            tokens[secrets.token_urlsafe(18)] = ident
        participants.append({"identity": HUMAN_IDENTITY, "kind": "human"})
        room = {
            "id": room_id,
            "title": title or "multiagent session",
            "mode": "multiagent",
            "status": "active",          # active | paused | waiting_human
            "createdAt": _now(),
            "updatedAt": _now(),
            "participants": participants,
            "tokens": tokens,            # bearer token -> identity
            "messages": [],              # {id, from, to, text, ts, seenBy:[]}
            "hopCount": 0,
            "maxHops": int(max_hops),
            "waitingFor": "",            # identity the room is blocked on (human)
        }
        _write(room)
        return room


def update_room(room: dict) -> None:
    """Persist a full (non-public) room dict — used by the launcher to record
    the working dir and each participant's session id/pid after spawning."""
    with _LOCK:
        room["updatedAt"] = _now()
        _write(room)


def list_rooms() -> list[dict]:
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in ROOMS_DIR.glob("room-*.json"):
        r = _read(p.stem)
        if r:
            out.append(_public(r))
    out.sort(key=lambda r: r.get("updatedAt", 0), reverse=True)
    return out


def get_room(room_id: str, public: bool = True) -> dict | None:
    r = _read(room_id)
    if r is None:
        return None
    return _public(r) if public else r


def _public(room: dict) -> dict:
    """Room view without the secret tokens."""
    r = dict(room)
    r.pop("tokens", None)
    return r


def delete_room(room_id: str) -> bool:
    with _LOCK:
        try:
            _room_path(room_id).unlink()
            return True
        except (FileNotFoundError, OSError):
            return False


# ---------------------------------------------------------------------------
# Identity resolution (for the MCP transport)
# ---------------------------------------------------------------------------

def resolve_token(token: str) -> tuple[str, str] | None:
    """Map a bearer token to ``(room_id, identity)``, or None if unknown."""
    if not token:
        return None
    with _LOCK:
        for p in ROOMS_DIR.glob("room-*.json"):
            r = _read(p.stem)
            if r and token in r.get("tokens", {}):
                return r["id"], r["tokens"][token]
    return None


def participant(room: dict, identity: str) -> dict | None:
    for p in room.get("participants", []):
        if p.get("identity") == identity:
            return p
    return None


def agent_participants(room: dict) -> list[dict]:
    return [p for p in room.get("participants", []) if p.get("kind") == "agent"]


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

def post_message(room_id: str, sender: str, text: str, to: str = "") -> dict | None:
    """Append a message from ``sender`` to the room. ``to`` may be a specific
    participant identity or '' / 'all' to address every other participant.

    Returns a dict describing what the caller (dashboard) should do next:
        {message, recipients:[identity...], status, hopCount}
    ``recipients`` are the agent identities that should be rung; if the human is
    a recipient the room enters ``waiting_human`` and the relay pauses.
    """
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        to_norm = (to or "").strip()
        msg = {
            "id": uuid.uuid4().hex[:12],
            "from": sender,
            "to": to_norm,
            "text": text,
            "ts": _now(),
        }
        room["messages"].append(msg)
        room["updatedAt"] = _now()

        # Work out recipients.
        idents = [p["identity"] for p in room["participants"]
                  if p["identity"] != sender]
        if to_norm and to_norm.lower() not in ("all", "everyone", "*"):
            recipients = [i for i in idents if i == to_norm]
        else:
            recipients = idents

        human_addressed = HUMAN_IDENTITY in recipients
        agent_recipients = [i for i in recipients if i != HUMAN_IDENTITY]

        if sender == HUMAN_IDENTITY:
            # Human spoke → reset the loop guard and resume autonomous relay.
            room["hopCount"] = 0
            room["status"] = "active"
            room["waitingFor"] = ""
        elif human_addressed:
            # An agent tagged the human → pause and wait for input.
            room["status"] = "waiting_human"
            room["waitingFor"] = HUMAN_IDENTITY
        else:
            # Agent → agent hand-off. Advance the loop guard.
            room["hopCount"] = room.get("hopCount", 0) + 1
            if room["hopCount"] >= room.get("maxHops", DEFAULT_MAX_HOPS):
                room["status"] = "paused"
                room["waitingFor"] = HUMAN_IDENTITY

        # Only ring agents when the room isn't paused / waiting on the human.
        ring = agent_recipients if room["status"] == "active" else []
        _write(room)
        return {
            "message": msg,
            "recipients": ring,
            "status": room["status"],
            "hopCount": room["hopCount"],
        }


def read_messages(room_id: str, since_ts: float = 0.0, for_identity: str = "") -> list[dict]:
    """Return messages after ``since_ts``. If ``for_identity`` is given, only
    messages that identity should see (addressed to it, to all, or its own)."""
    room = _read(room_id)
    if room is None:
        return []
    out = []
    for m in room.get("messages", []):
        if m["ts"] <= since_ts:
            continue
        if for_identity:
            to = (m.get("to") or "").lower()
            if (m["from"] != for_identity and to not in ("", "all", "everyone", "*")
                    and m.get("to") != for_identity):
                continue
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# MCP tool surface (exposed to agents via the dashboard's MCP endpoint)
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "chat_send",
        "description": (
            "Send a message to your collaboration partner(s) or the human. This "
            "is how you hand off your turn: after you finish a step of thinking "
            "or work, send your partner your findings/critique/proposal. To pull "
            "the human in for a decision, question, or clarification, set "
            "to=\"user\" — that pauses the collaboration until they reply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "The message text to send."},
                "to": {"type": "string",
                       "description": "Recipient identity: your partner's name, "
                                      "\"user\" for the human, or omit / \"all\" "
                                      "to address everyone."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "chat_read",
        "description": (
            "Read new messages addressed to you in the shared room since you "
            "last read. Call this whenever you're notified a message arrived. "
            "Returns each message's sender and text."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "chat_whoami",
        "description": (
            "Return your identity, your collaboration partner(s), and the room's "
            "current status (active / waiting on the human / paused)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _visible_to(msg: dict, identity: str) -> bool:
    to = (msg.get("to") or "").lower()
    return (msg["from"] == identity or to in ("", "all", "everyone", "*")
            or msg.get("to") == identity)


def read_new_for(room_id: str, identity: str) -> list[dict]:
    """Return messages visible to ``identity`` that it hasn't read yet, and
    advance its read cursor. Backs the ``chat_read`` tool."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return []
        reads = room.setdefault("reads", {})
        cursor = reads.get(identity, 0.0)
        msgs = [m for m in room.get("messages", [])
                if m["ts"] > cursor and _visible_to(m, identity)]
        if room.get("messages"):
            reads[identity] = room["messages"][-1]["ts"]
            _write(room)
        return msgs


def whoami(room_id: str, identity: str) -> dict:
    room = _read(room_id)
    if room is None:
        return {}
    partners = [p["identity"] for p in room["participants"]
                if p["identity"] != identity and p.get("kind") == "agent"]
    return {
        "you": identity,
        "partners": partners,
        "human": HUMAN_IDENTITY,
        "status": room.get("status", ""),
        "title": room.get("title", ""),
    }


def set_status(room_id: str, status: str) -> dict | None:
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        room["status"] = status
        if status == "active":
            room["hopCount"] = 0
            room["waitingFor"] = ""
        room["updatedAt"] = _now()
        _write(room)
        return _public(room)
