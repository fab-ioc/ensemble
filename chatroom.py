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
* **Who gets woken.** Every ring re-sends an agent's whole conversation, so a
  message wakes as few agents as it can: a direct message wakes its addressee;
  a message to everyone wakes only the task's *owner* (see :func:`owners`), and
  a specialist — a reviewer, a designer — only when the user, an owner or the
  PO @mentions it. :func:`wake_targets` is the one rule; the attention detector
  uses it too, so "was it asked?" means "was it woken?".
* **Persistence.** Each room is a JSON file under ``DASHBOARD_DIR/rooms`` so
  rooms (and their transcripts) survive a server restart. All mutation goes
  through a single process-wide lock — duo-scale volume, so read-modify-write
  per op is fine.

This module is pure state + policy; the HTTP endpoints, the MCP transport, and
the doorbell live in ``dashboard.py`` (which injects a ``ring`` callback).
"""
from __future__ import annotations

import json
import re
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
                "role": (m.get("role") or "").strip(),   # engineer | reviewer | pair | custom text
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


def _unique_identity(base: str, used: set[str]) -> str:
    """First free identity for ``base``: ``claude``, then ``claude-2``, …"""
    ident = base
    n = 2
    while ident in used:
        ident = f"{base}-{n}"
        n += 1
    return ident


def set_agents(room_id: str, members: list[dict],
               mode: str = "") -> dict | None:
    """Replace a room's agent line-up, keeping the agents that are staying.

    ``members`` are the same ``{identity?, agent, model, role}`` dicts
    :func:`create_room` takes. An entry is matched to an existing participant —
    which keeps that agent's identity, bearer token, session id and working dir,
    so its transcript and any live MCP client survive — by its ``identity``,
    naming an existing agent **of the same kind**.

    A pin that doesn't resolve does NOT fall back to anything: it means the
    agent it named is gone, or has changed kind, and the entry is a new agent.
    Falling back would let one row walk off with a *different* row's identity,
    token and transcript — a change to one agent silently rewriting another.
    For the same reason, once ANY entry carries an identity, an entry without
    one is taken at its word: a new agent.

    Positional matching — first unclaimed participant of the same kind — is the
    fallback only for a wholly identity-free list, which is what a plain
    ``["claude", "codex"]`` or an older client sends.

    An identity whose ``agent`` kind changed is deliberately NOT reused: claude
    and codex are different agents, and handing one the other's transcript would
    be wrong. Anything unmatched is a new agent — it gets a fresh identity, a
    fresh token, and a ``fresh`` flag so the launcher starts it from the task
    briefing instead of trying to resume a conversation it never had. Agents
    that dropped out lose their participant record and their token.

    ``mode`` ("solo" / "collab"), when given, is written in the same breath, so
    the file is never briefly on disk with a new line-up and the old mode.

    Returns the updated full room (tokens included), or None if there is no
    such room. Messages are untouched: a removed agent's turns stay in the log.
    """
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        existing = [p for p in room.get("participants", []) if p.get("kind") == "agent"]
        by_identity = {p.get("identity", ""): i for i, p in enumerate(existing)}
        claimed: set[int] = set()
        matched: list[dict | None] = [None] * len(members)

        pins = [(m.get("identity") or "").strip() for m in members]
        if any(pins):
            for i, m in enumerate(members):
                j = by_identity.get(pins[i], -1) if pins[i] else -1
                if j >= 0 and j not in claimed and \
                        existing[j].get("agent", "") == (m.get("agent") or "").strip():
                    claimed.add(j)
                    matched[i] = existing[j]
        else:
            for i, m in enumerate(members):
                kind = (m.get("agent") or "").strip()
                for j, p in enumerate(existing):
                    if j not in claimed and p.get("agent", "") == kind:
                        claimed.add(j)
                        matched[i] = p
                        break

        # An identity that has already spoken, or already read the room, is
        # spent: recycling it would relabel someone else's messages and hand
        # the newcomer the departed agent's read cursor, so its first
        # chat_read would skip the whole conversation.
        used = ({HUMAN_IDENTITY}
                | {p.get("identity", "") for p in matched if p}
                | {m.get("from", "") for m in room.get("messages", [])}
                | set(room.get("reads", {})))
        tokens_by_identity = {ident: tok for tok, ident in room.get("tokens", {}).items()}
        participants: list[dict] = []
        tokens: dict[str, str] = {}
        for m, part in zip(members, matched):
            if part is not None:
                part = dict(part)
                part["model"] = m.get("model", "")
                part["role"] = (m.get("role") or "").strip()
                # These two name a process that is already gone — a stopped room
                # keeps them, which is exactly what makes it read as live. Drop
                # them here so the record stops lying. ``sessionId`` and ``cwd``
                # deliberately survive: they are not liveness, they are how the
                # agent finds its own transcript again when the task restarts.
                part["pid"] = None
                part.pop("ptyId", None)
                tok = tokens_by_identity.get(part["identity"])
                if not tok:                     # shouldn't happen; don't strand it
                    tok = secrets.token_urlsafe(18)
                tokens[tok] = part["identity"]
            else:
                # A new agent is named after its kind, never after the
                # identity hint: that hint means "reuse this one", and it only
                # got here because nothing matched it.
                base = (m.get("agent") or "").strip() or "agent"
                ident = _unique_identity(base, used)
                used.add(ident)
                part = {
                    "identity": ident,
                    "kind": "agent",
                    "agent": m.get("agent", ""),
                    "model": m.get("model", ""),
                    "role": (m.get("role") or "").strip(),
                    "pid": None,
                    "sessionId": "",
                    "cwd": "",
                    "label": "",
                    # No conversation to resume — start it from the briefing.
                    "fresh": True,
                }
                tokens[secrets.token_urlsafe(18)] = ident
            participants.append(part)
        participants.append({"identity": HUMAN_IDENTITY, "kind": "human"})
        room["participants"] = participants
        room["tokens"] = tokens
        if mode:
            room["mode"] = mode
        room["updatedAt"] = _now()
        _write(room)
        return room


NUMBER_FIELDS = ("no", "noProjectId", "previousNos")


def update_room(room: dict) -> None:
    """Persist a full (non-public) room dict — used by the launcher to record
    the working dir and each participant's session id/pid after spawning.

    A task's number is written only by :func:`set_task_number`, so a copy
    read before it was given, or before a move renumbered it, never undoes it:
    once the file has a number, :data:`NUMBER_FIELDS` are the file's."""
    with _LOCK:
        disk = _read(room.get("id", "")) or {}
        if disk.get("no"):
            for k in NUMBER_FIELDS:
                if k in disk:
                    room[k] = disk[k]
                else:
                    room.pop(k, None)
        room["updatedAt"] = _now()
        _write(room)


def set_task_number(room_id: str, project_id: str, no: int) -> dict | None:
    """Give a task its number in a project — read-modify-write under the room
    lock, no ``updatedAt`` bump (numbering is not activity). A number it had
    in another project is kept in ``previousNos`` so an old reference still
    finds it. Returns the public room, or None when it no longer exists."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        old, old_pid = room.get("no"), room.get("noProjectId")
        if old and old_pid and (old_pid, old) != (project_id, no):
            prev = [p for p in room.get("previousNos") or [] if isinstance(p, dict)]
            if not any(p.get("projectId") == old_pid and p.get("no") == old for p in prev):
                prev.append({"projectId": old_pid, "no": old})
            room["previousNos"] = prev
        room["no"] = int(no)
        room["noProjectId"] = project_id
        _write(room)
        return _public(room)


def record_exit(room_id: str, identity: str, exit_rec: dict) -> bool:
    """Stamp one agent's last exit onto the room — read-modify-write under the
    room lock.

    Deliberately narrow. The caller is a dying PTY's reader thread, and a
    ``get_room`` → mutate → :func:`update_room` from there would race
    :func:`post_message`: both rewrite the whole file, so a chat message posted
    in between would be silently dropped. Doing the read and the write inside
    the same lock makes that impossible.
    """
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return False
        hit = False
        for p in room.get("participants", []):
            if p.get("identity") == identity:
                p["lastExit"] = exit_rec
                hit = True
        if not hit:
            return False
        _write(room)                 # NB: not update_room — no updatedAt bump;
        return True                  # a death is not activity on the task.


def patch_room(room_id: str, **fields) -> dict | None:
    """Set a few fields on a room — read-modify-write under the room lock, for
    the same reason as :func:`record_exit`. No ``updatedAt`` bump: the hub
    noting something about a task is not activity on it. Returns the full
    room, or None when it no longer exists."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        room.update(fields)
        _write(room)
        return room


def clear_exits(room_id: str) -> bool:
    """Forget every recorded exit on a room — the human has dealt with it.

    Same read-modify-write-under-the-lock as :func:`record_exit`, and for the
    same reason: the caller has been away killing processes (`taskkill /F /T`
    with a 5-second timeout apiece), and rewriting the whole room after that
    would silently drop any chat message posted in the meantime.
    """
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return False
        cleared = False
        for p in room.get("participants", []):
            if p.pop("lastExit", None) is not None:
                cleared = True
        if cleared:
            _write(room)
        return cleared


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
# Roles that decide who is woken
# ---------------------------------------------------------------------------

PRODUCT_OWNER_ROLE = "productowner"
BROADCAST = ("", "all", "everyone", "*")
REPORT_KINDS = ("completed", "blocked", "question", "update")

# The same shape session.html highlights: "@name" at the start, after a space
# or after "(".
_MENTION = re.compile(r"(?:^|[\s(])@([A-Za-z][\w-]*)")
_ENGINEER = re.compile(r"\bengineer\b")


def role_key(role: str) -> str:
    return (role or "").strip().lower().replace(" ", "")


def is_product_owner_part(part: dict) -> bool:
    return role_key((part or {}).get("role", "")) == PRODUCT_OWNER_ROLE


def _role_head(role: str) -> str:
    """A role's name without its charter: "designer and engineer: follows the
    skill…" → "designer and engineer"."""
    return (role or "").split(":", 1)[0].strip().lower()


def owners(room: dict) -> list[str]:
    """The agents a message to everyone wakes: the one agent of a solo task;
    otherwise every agent whose role is an engineer ("engineer", "designer and
    engineer"); failing that the ProductOwner; failing that every agent — a
    room of equal partners has no specialists to spare."""
    agents = agent_participants(room)
    if len(agents) <= 1:
        return [p["identity"] for p in agents]
    eng = [p["identity"] for p in agents if _ENGINEER.search(_role_head(p.get("role", "")))]
    if eng:
        return eng
    po = [p["identity"] for p in agents if is_product_owner_part(p)]
    return po or [p["identity"] for p in agents]


def po_identity(room: dict) -> str:
    """Who a report into this room is addressed to: its ProductOwner agent,
    else its owner. Empty for a room with no agents."""
    agents = agent_participants(room)
    po = next((p["identity"] for p in agents if is_product_owner_part(p)), "")
    if po:
        return po
    own = owners(room)
    return own[0] if own else ""


REVIEWER_ROLE = "reviewer"


def is_on_mention(room: dict, part: dict) -> bool:
    """Whether this agent is started fresh for each request instead of being
    kept running in the room: a reviewer on a team.

    Every wake of a long-lived session re-sends its whole conversation, and a
    resume reloads the same history, so a reviewer is launched only when a
    message wakes it (see :func:`wake_targets`), briefed from the task's spec,
    diff and review log, and ends once it has given its verdict. A task's only
    agent is its owner, never on mention, whatever its role says."""
    if (part or {}).get("kind") != "agent" or len(agent_participants(room)) < 2:
        return False
    return _role_head(part.get("role", "")) == REVIEWER_ROLE


def patch_participant(room_id: str, identity: str, fields: dict,
                      append: dict | None = None, drop: tuple = ()) -> dict | None:
    """Update one participant's record — read-modify-write under the room lock,
    so a chat message posted meanwhile is never lost (see :func:`record_exit`).
    ``append`` adds items to list fields; ``drop`` removes keys. Returns the
    updated participant, or None when the room or the participant is gone."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        part = participant(room, identity)
        if part is None:
            return None
        part.update(fields)
        for k, v in (append or {}).items():
            part.setdefault(k, []).append(v)
        for k in drop:
            part.pop(k, None)
        room["updatedAt"] = _now()
        _write(room)
        return dict(part)


def apply_review_allocation(room_id: str, identity: str, agent: str, model: str,
                            allocation: dict, limit: int = 20) -> dict | None:
    """Atomically update a reviewer's kind/model and append its room audit.

    Review allocation happens on a live room, so rewriting a previously read
    room can discard chat posted during the allowance check.  Re-read, mutate
    and write under the room lock, and return the complete updated room the
    caller must use to build the review brief.
    """
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        part = participant(room, identity)
        if part is None:
            return None
        part.update(agent=agent, model=model)
        history = list(room.get("reviewAllocations") or [])
        history.append(allocation)
        room["reviewAllocations"] = history[-max(1, int(limit)):]
        room["updatedAt"] = _now()
        _write(room)
        return room


def mentions(room: dict, text: str) -> set[str]:
    """Agents @mentioned in ``text``, by identity ("@codex") or by role name
    ("@reviewer")."""
    names = {m.group(1).lower() for m in _MENTION.finditer(text or "")}
    if not names:
        return set()
    return {p["identity"] for p in agent_participants(room)
            if p["identity"].lower() in names or _role_head(p.get("role", "")) in names}


def wake_targets(room: dict, sender: str, to: str, text: str) -> list[str]:
    """The agents a message wakes, before any pause is taken into account.

    * Addressed to one participant: that participant, if it is an agent.
    * Addressed to everyone: the owners (:func:`owners`), plus any specialist
      the user, an owner or the PO @mentions. A specialist mentioned by
      another specialist stays asleep — that is how two reviewers talking
      about each other would otherwise wake each other forever.
    """
    others = [p["identity"] for p in agent_participants(room) if p["identity"] != sender]
    t = (to or "").strip()
    if t.lower() not in BROADCAST:
        return [t] if t in others else []
    own = set(owners(room))
    authority = sender == HUMAN_IDENTITY or sender in own or sender == po_identity(room)
    named = mentions(room, text) if authority else set()
    return [i for i in others if i in own or i in named]


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

        # Who it is addressed to, and — separately — which agents it wakes. A
        # message to everyone reaches every reader, but wakes only the owner
        # and whoever was @mentioned (see wake_targets). An agent that is
        # addressed but not woken is not waiting on anything, so a broadcast
        # that wakes nobody is, in effect, a message to the human.
        idents = [p["identity"] for p in room["participants"]
                  if p["identity"] != sender]
        if to_norm and to_norm.lower() not in BROADCAST:
            recipients = [i for i in idents if i == to_norm]
        else:
            recipients = idents

        human_addressed = HUMAN_IDENTITY in recipients
        agent_recipients = wake_targets(room, sender, to_norm, text)
        msg["rang"] = agent_recipients

        if sender == HUMAN_IDENTITY:
            # Human spoke → reset the loop guard and resume autonomous relay.
            room["hopCount"] = 0
            room["status"] = "active"
            room["waitingFor"] = ""
        elif human_addressed and not agent_recipients:
            # An agent tagged ONLY the human → pause and wait for input.
            room["status"] = "waiting_human"
            room["waitingFor"] = HUMAN_IDENTITY
        else:
            # Agent → agent hand-off (a direct message, or a broadcast that also
            # cc's the human). Ring the addressed teammate and keep the relay
            # alive even if we were previously waiting on the human — the team is
            # actively collaborating (e.g. the engineer asks the PO for sign-off
            # AND hands the reviewer a deliverable in parallel). The hop guard
            # still caps runaway loops.
            room["hopCount"] = room.get("hopCount", 0) + 1
            if room["hopCount"] >= room.get("maxHops", DEFAULT_MAX_HOPS):
                room["status"] = "paused"
                room["waitingFor"] = HUMAN_IDENTITY
            else:
                room["status"] = "active"
                room["waitingFor"] = ""

        # Only ring agents when the room isn't paused / waiting on the human.
        ring = agent_recipients if room["status"] == "active" else []
        _write(room)
        return {
            "message": msg,
            "recipients": ring,
            "status": room["status"],
            "hopCount": room["hopCount"],
        }


def record_report(room_id: str, identity: str, kind: str, text: str,
                  routed_to: dict | None = None, heading: str = "",
                  clears: bool = False) -> dict | None:
    """Put a task agent's report on its own task: a chat message to the human
    (so the task's chat shows it) and ``lastReport`` (so the attention detector
    and the task tools can read it without walking the log).

    Deliberately leaves status and the hop count alone: a report is not a
    hand-off inside the team, and whether it leaves the task waiting on a human
    is the attention detector's call, which it makes from ``lastReport``.
    ``routed_to`` is ``{roomId, identity, project}`` of the PO it went to, or
    None when it went to the user. ``clears``: this report says the task's open
    ask to the person (a blocked, a question) is over, although nobody answered
    it in chat; without it an ``update`` leaves the ask open (attention.py).
    It is the one report that touches the status: a room that was waiting on
    the person's reply to a message stops waiting."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        now = _now()
        msg = {"id": uuid.uuid4().hex[:12], "from": identity, "to": HUMAN_IDENTITY,
               "text": f"{heading}\n\n{text}" if heading else text, "ts": now,
               "kind": "report", "reportKind": kind, "rang": []}
        if routed_to:
            msg["reportTo"] = routed_to
        if clears:
            msg["clears"] = True
            _wait_for_human_over(room)
        room.setdefault("messages", []).append(msg)
        room["lastReport"] = {"kind": kind, "text": text[:4000], "ts": now,
                              "identity": identity, "messageId": msg["id"],
                              "to": routed_to or {"identity": HUMAN_IDENTITY}}
        # An update (a rotation, a handover, "working again") says nothing the
        # PO must act on: the last completed / question / blocked survives it.
        if kind != "update":
            room["lastRealReport"] = room["lastReport"]
        room["updatedAt"] = now
        _write(room)
        return msg


def _wait_for_human_over(room: dict) -> None:
    """The room was waiting on the person's reply to an agent's message
    (``waiting_human``, see :func:`post_message`) and that ask has closed
    without them speaking in the chat: the room is not waiting any more, so
    the bell and the lists agree with the ask's own line. A pause at the hop
    limit is another matter and stays."""
    if room.get("status") == "waiting_human":
        room["status"] = "active"
        room["waitingFor"] = ""


def record_answer(room_id: str, identity: str, at: float) -> dict | None:
    """A person answered a one-agent task's open ask in its terminal, at
    ``at``: kept on the participant (``answeredAt``, read by
    attention._open_to_human), and the room stops waiting on them. Returns the
    participant, or None when the room or the participant is gone."""
    with _LOCK:
        room = _read(room_id)
        part = participant(room, identity) if room is not None else None
        if part is None:
            return None
        part["answeredAt"] = at
        _wait_for_human_over(room)
        room["updatedAt"] = _now()
        _write(room)
        return dict(part)


def last_real_report(room: dict) -> dict:
    """The task's last report that is not an ``update``, or {}. A task that
    reported before ``lastRealReport`` was kept falls back to ``lastReport``
    when that is not an update."""
    rep = room.get("lastRealReport")
    if isinstance(rep, dict):
        return rep
    rep = room.get("lastReport")
    return rep if isinstance(rep, dict) and rep.get("kind") != "update" else {}


def post_report(room_id: str, sender: str, to: str, text: str, meta: dict,
                wake: bool = True) -> dict | None:
    """Deliver a report from another task into this (the PO's) room, addressed
    to ``to``. Returns the same shape as :func:`post_message`.

    Unlike a chat hand-off it wakes its addressee — being woken by the tasks it
    runs is the PO's whole job — and it leaves the PO room's status and hop
    count alone: the report came from outside the room, so it is neither a turn
    inside it nor a loop the guard could stop. ``wake=False`` posts a note that
    asks nothing of the PO (a task owner's rotation): it is shown in the room
    but rings nobody, so the attention detector never waits for an answer."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": to, "text": text,
               "ts": _now(), "kind": "report", **meta}
        is_agent = any(p.get("identity") == to for p in agent_participants(room))
        msg["rang"] = [to] if is_agent and wake else []
        room.setdefault("messages", []).append(msg)
        room["updatedAt"] = _now()
        _write(room)
        return {"message": msg, "recipients": msg["rang"],
                "status": room.get("status", "active"), "hopCount": room.get("hopCount", 0)}


def post_notice(room_id: str, sender: str, text: str, meta: dict) -> dict | None:
    """A line from the hub itself into a room, for the human — e.g. that the
    room's PO was rotated to a fresh session. Wakes nobody and leaves status
    and the hop count alone. Returns the message, or None if there is no room."""
    with _LOCK:
        room = _read(room_id)
        if room is None:
            return None
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": HUMAN_IDENTITY,
               "text": text, "ts": _now(), "kind": "notice", "rang": [], **meta}
        room.setdefault("messages", []).append(msg)
        room["updatedAt"] = _now()
        _write(room)
        return msg


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
            "Send a message to your collaboration partner(s) or the user. This "
            "is how you hand off your turn: after you finish a step of thinking "
            "or work, send your partner your findings/critique/proposal. To pull "
            "the user in for a decision, question, or clarification, set "
            "to=\"user\" — that pauses the collaboration until they reply. A "
            "message to everyone wakes only the task's owner (the engineer); "
            "to wake a reviewer or another specialist, address it with `to` or "
            "@mention it (\"@reviewer\", \"@codex\"). Mention a reviewer only "
            "for a commit to review or a specific question — never for a plan, "
            "acknowledgement, thanks, or verdict restatement."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "The message text to send."},
                "to": {"type": "string",
                       "description": "Recipient identity: your partner's name, "
                                      "\"user\" for the user, or omit / \"all\" "
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
            "current status (active / waiting on the user / paused)."
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
