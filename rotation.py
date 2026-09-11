"""Rotating long conversations: a fresh session from a written handover.

The PO has the longest conversation on a project, and every wake — a task's
report, a progress digest, the CEO's message — re-sends all of it. Measured on
2026-09-10 the Ensemble Dashboard PO was sending ~900k tokens per call. So
when the conversation gets long the hub starts a **fresh PO session** that
picks up from ``PO-HANDOVER.md`` (the PO's living handover in the project
home) and ``ROADMAP.md`` instead of the whole history.

A task's **owner** — its engineer, or its only agent — is rotated the same way
past ``taskRotateTokens``: it writes ``TASK-HANDOVER.md`` in the task folder
and a fresh session continues from it. Task owners were 53% of the tokens in
the week to 2026-09-11. A reviewer is not (it already runs fresh for each
review), nor a paused or stopped task, nor a PO room (it rotates as the PO).

How big a conversation is: the context of its latest model call, read from the
tail of its transcript (Claude: input + cache read + cache write of the last
main-chain assistant turn; Codex: the input of its last ``token_count``, which
includes the cached part). Past ``poRotateTokens`` / ``taskRotateTokens`` (hub
settings; 0 turns them off):

1. **Ask.** Once the agent is idle, the hub types one line into it: bring its
   handover up to date, because a fresh session starts from it.
2. **Wait** for it to finish that turn (its transcript ends its turn after the
   ask, and its terminal has gone quiet). If it never answers — the line did
   not submit, say — it is rotated anyway once idle past ``ASK_TIMEOUT_S``; if
   it stays busy past ``GIVE_UP_S`` the attempt is dropped and made again later.
3. **Rotate.** The old terminal is ended, and a new session starts in the same
   room, same agent, model and working dir, whose first prompt is to read the
   handover. The participant keeps its identity and token, so messages and
   reports reach the new session unchanged; the old session id is kept in
   ``participant.rotations`` (its transcript stays on disk, and still counts in
   the task's cost).
4. **Say so.** A notice lands in the room; a task owner's rotation is also
   reported to the project's PO in one line.

Only Claude POs are rotated: a PO is started from a prompt that holds its
spec, which a Codex command line has no room for. Owners of both kinds are.

Bound to the dashboard module like ``digest``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


HANDOVER_NAME = "PO-HANDOVER.md"
TASK_HANDOVER_NAME = "TASK-HANDOVER.md"
DEFAULT_TOKENS = 200_000
# A fresh PO starts at ~55k (measured 2026-09-10: Claude Code's own prompt, the
# MCP tools, the handover and the roadmap); a threshold near that would rotate
# it again after every cool-down.
MIN_TOKENS = 100_000
MAX_TOKENS = 5_000_000
TICK_S = 60
IDLE_S = 5.0                # terminal quiet this long after an end_turn = idle
ASK_TIMEOUT_S = 20 * 60     # no answer to the ask by then: rotate once idle
GIVE_UP_S = 60 * 60         # still busy by then: drop the attempt, try later
COOLDOWN_S = 30 * 60        # a session this young is never rotated, nor re-asked
SENDER = "ensemble"         # who the notice is from in the room
_KILL_WAIT_S = 8.0
_CODEX_SID_WAIT_S = 20.0    # Codex mints its own id: wait this long to learn it
_TAIL_BYTES = (1 << 20, 8 << 20)
_SPEC_MAX = 6000            # the first prompt goes on a command line

_LOCK = threading.Lock()    # one check at a time: scheduler vs "check now"
_STATE: dict[str, dict] = {}  # projectId -> {phase, askedAt, ..., lastResult}
_TASK_STATE: dict[str, dict] = {}  # "roomId/identity" -> the same, for owners
# The lifecycle gate: a rotation's last idle check, its mark and its hand-over
# to the fresh terminal, every doorbell's check-and-send, and every Stop's
# read-and-kill each run as one step under it.
GATE = threading.RLock()
_ROTATING: dict[tuple, dict] = {}  # (roomId, identity) -> {stopped}, mid-rotation
_HELD: dict[tuple, list] = {}      # (roomId, identity) -> wakes held meanwhile
REPLAY_WAIT_S = 15 * 60            # a fresh session that never settles gets them anyway


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def clamp_tokens(v) -> int | None:
    """A threshold in tokens: 0 (off) or MIN … MAX; None when not a number."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return 0
    return max(MIN_TOKENS, min(MAX_TOKENS, v))


def threshold(key: str = "poRotateTokens") -> int:
    v = clamp_tokens(_d.load_settings().get(key, DEFAULT_TOKENS))
    return DEFAULT_TOKENS if v is None else v


def task_threshold() -> int:
    return threshold("taskRotateTokens")


def handover_path(project: dict) -> Path:
    return Path(_d.project_home(project, create=False)) / HANDOVER_NAME


def task_handover_path(room: dict, part: dict) -> Path:
    base = room.get("taskDir") or part.get("cwd") or room.get("cwd") or ""
    return Path(base) / TASK_HANDOVER_NAME


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] rotation: {msg}", flush=True)


# ---------------------------------------------------------------------------
# Reading the transcript
# ---------------------------------------------------------------------------

def _context_of(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def _tail_lines(path: Path, size: int, want: int) -> tuple[int, list] | None:
    """The last ``want`` bytes of a JSONL file as [(offset, line)], dropping a
    partial first line. None when it cannot be read."""
    start = max(0, size - want)
    try:
        with path.open("rb") as f:
            f.seek(start)
            raw = f.read(size - start)
    except OSError:
        return None
    lines, pos = [], start
    for ln in raw.split(b"\n"):
        lines.append((pos, ln))
        pos += len(ln) + 1
    if start > 0:
        lines = lines[1:]           # a partial first line
    return start, lines


def read_transcript(path: Path | None, since: int = -1) -> dict:
    """What the tail of a Claude transcript says, without reading the whole of
    it (a long PO's transcript runs to tens of megabytes):

    * ``tokens`` — the context of the latest main-chain model call, or None;
    * ``turnOver`` — the conversation's last main-chain turn is the model
      ending its turn (not a prompt waiting, not a tool call in flight);
    * ``promptSince`` — a user turn was written at byte ``since`` or later
      (the ask has reached the conversation);
    * ``size`` — the file's size, the offset a later ``since`` compares to.
    """
    out = {"tokens": None, "turnOver": False, "promptSince": False, "size": 0}
    if not path:
        return out
    try:
        size = path.stat().st_size
    except OSError:
        return out
    out["size"] = size
    for want in _TAIL_BYTES:
        got = _tail_lines(path, size, want)
        if got is None:
            return out
        start, lines = got
        last_seen = False
        for off, ln in reversed(lines):
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            typ = d.get("type")
            if typ not in ("user", "assistant") or d.get("isSidechain"):
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            if not last_seen:
                last_seen = True
                out["turnOver"] = typ == "assistant" and msg.get("stop_reason") == "end_turn"
            if typ == "user" and since >= 0 and off >= since:
                out["promptSince"] = True
            if typ == "assistant" and out["tokens"] is None and isinstance(msg.get("usage"), dict):
                out["tokens"] = _context_of(msg["usage"])
            if out["tokens"] is not None and (since < 0 or off < since):
                return out
        # Only a scan that reached ``since`` can say no prompt came after it.
        if start == 0 or (out["tokens"] is not None and (since < 0 or start <= since)):
            return out
    return _grew_past(out, since, start)


def _grew_past(out: dict, since: int, start: int) -> dict:
    """The widest tail did not reach back to ``since``: the conversation grew
    by more than that since the ask, so a turn has followed it."""
    if since >= 0 and start > since:
        out["promptSince"] = True
    return out


# The rollout events that say where a Codex turn is: a turn opens with
# task_started (the prompt follows as user_message) and ends with task_complete,
# or turn_aborted when it is interrupted.
_CODEX_TURN = {"task_started": False, "user_message": False,
               "task_complete": True, "turn_aborted": True}


def read_rollout(path: Path | None, since: int = -1) -> dict:
    """The same as :func:`read_transcript`, for a Codex rollout file: the
    tokens are the input of the newest ``token_count`` (its ``input_tokens``
    already include the cached part), the turn is over when the newest turn
    event is a ``task_complete``, and a turn started at ``since`` or later
    means the ask arrived."""
    out = {"tokens": None, "turnOver": False, "promptSince": False, "size": 0}
    if not path:
        return out
    try:
        size = path.stat().st_size
    except OSError:
        return out
    out["size"] = size
    for want in _TAIL_BYTES:
        got = _tail_lines(path, size, want)
        if got is None:
            return out
        start, lines = got
        last_seen = False
        for off, ln in reversed(lines):
            if not ln.strip() or b'"event_msg"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            pl = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            typ = pl.get("type")
            if typ in _CODEX_TURN and not last_seen:
                last_seen = True
                out["turnOver"] = _CODEX_TURN[typ]
            # A line typed into its terminal opens a turn; it is not always
            # logged as a user_message (seen 2026-09-11), the task_started is.
            if typ in ("task_started", "user_message") and since >= 0 and off >= since:
                out["promptSince"] = True
            if typ == "token_count" and out["tokens"] is None:
                last = ((pl.get("info") or {}).get("last_token_usage") or {})
                if last:
                    out["tokens"] = int(last.get("input_tokens") or 0)
            if out["tokens"] is not None and last_seen and (since < 0 or off < since):
                return out
        if start == 0 or (out["tokens"] is not None and last_seen
                          and (since < 0 or start <= since)):
            return out
    return _grew_past(out, since, start)


def _transcript_of(part: dict):
    """(path, reader) of an agent's current conversation."""
    sid = (part.get("sessionId") or "").strip()
    if part.get("agent") == "codex":
        cx = _d.agents.get_agent("codex")
        files = cx.rollouts_for_session(sid) if (cx and sid) else []
        return (files[-1] if files else None), read_rollout
    return _d.find_transcript(sid), read_transcript


# ---------------------------------------------------------------------------
# Who is watched: the PO, and each running task's owner
# ---------------------------------------------------------------------------

def _po(project: dict) -> tuple[dict | None, dict | None, str]:
    """(room, participant, why-not). Both None when there is nothing to watch."""
    rid = (project.get("poRoomId") or "").strip()
    if not rid:
        return None, None, "the project has no PO"
    room = _d.chatroom.get_room(rid)
    if room is None:
        return None, None, f"the PO task {rid} no longer exists"
    ident = _d.chatroom.po_identity(room)
    part = _d.chatroom.participant(room, ident) if ident else None
    if not part:
        return None, None, "the PO task has no agent"
    if part.get("agent") != "claude":
        return None, None, f"only a Claude PO can be rotated (this one is {part.get('agent')})"
    if not (part.get("sessionId") or "").strip():
        return None, None, "the PO has no session yet"
    return room, part, ""


def _po_room_ids() -> set[str]:
    return {(p.get("poRoomId") or "").strip() for p in _d.load_projects()} - {""}


def task_owners(room: dict) -> list[dict]:
    """The agents of a task that are rotated: its only agent, else its
    engineers. Never a reviewer on mention, never a ProductOwner."""
    cr = _d.chatroom
    agents = cr.agent_participants(room)
    if len(agents) == 1:
        return agents
    return [p for p in agents
            if not cr.is_on_mention(room, p) and not cr.is_product_owner_part(p)
            and cr._ENGINEER.search(cr._role_head(p.get("role", "")))]


def _owner(room_id: str, identity: str = "") -> tuple[dict | None, dict | None, str]:
    """(room, participant, why-not) for a task's owner."""
    if room_id in _po_room_ids():
        return None, None, "this is a project's PO task — it rotates as the PO"
    room = _d.chatroom.get_room(room_id)
    if room is None:
        return None, None, f"the task {room_id} no longer exists"
    if not room.get("launched", True):
        return None, None, "the task is a draft"
    if room.get("status") == "paused":
        return None, None, "the task is paused"
    _d._backfill_codex_session_ids(room)
    owners = task_owners(room)
    if identity:
        owners = [p for p in owners if p.get("identity") == identity]
        if not owners:
            return None, None, (f"{identity} is not the task's owner — only the engineer "
                                f"or the only agent is rotated")
    if not owners:
        return None, None, "the task has no owner agent"
    if len(owners) > 1:
        return None, None, "the task has several owners — name one"
    part = owners[0]
    if part.get("agent") not in ("claude", "codex"):
        return None, None, f"a {part.get('agent')} agent cannot be rotated"
    if not (part.get("sessionId") or "").strip():
        return None, None, f"{part['identity']} has no session yet"
    return room, part, ""


def running_owners() -> list[tuple[str, str]]:
    """(roomId, identity) of every task owner with a live terminal, from the
    attention summaries (cached per room file, so no chat log is re-read)."""
    po_rooms = _po_room_ids()
    out = []
    for r in _d.attention._room_summaries():
        if (r["id"] in po_rooms or not r.get("launched", True)
                or r.get("status") == "paused"):
            continue
        for p in task_owners(r):
            if _pty(p) is not None:
                out.append((r["id"], p["identity"]))
    return out


def _pty(part: dict):
    sess = _d.ptyrun.get(part.get("ptyId") or "")
    return sess if sess and sess.alive() else None


def _idle(part: dict, tr: dict) -> bool:
    sess = _pty(part)
    if sess is None or not tr["turnOver"]:
        return False
    try:
        return float(sess.info().get("idleSeconds") or 0) >= IDLE_S
    except Exception:
        return False


def _session_started(part: dict) -> float:
    """When the agent's current session began, if a rotation started it."""
    rots = part.get("rotations") or []
    last = rots[-1] if rots and isinstance(rots[-1], dict) else {}
    if last.get("toSessionId") == part.get("sessionId"):
        return float(last.get("at") or 0)
    # A Codex session's id is learnt after its launch.
    return float(part.get("rotatedAt") or 0)


def _k(n) -> str:
    return f"{round((n or 0) / 1000)}k"


def _po_subject(project: dict) -> dict:
    room, part, why = _po(project)
    return {"kind": "po", "name": project.get("name", project["id"]),
            "project": project, "room": room, "part": part, "why": why,
            "state": _STATE.setdefault(project["id"], {"phase": "watching"}),
            "limit": threshold(), "setting": "poRotateTokens",
            "who": "the PO", "whose": "the PO's", "handoverName": HANDOVER_NAME,
            "handover": handover_path(project) if room else None,
            "ids": {"projectId": project["id"]}}


def _owner_subject(room_id: str, identity: str = "") -> dict:
    room, part, why = _owner(room_id, identity)
    ident = part["identity"] if part else identity
    title = (room or {}).get("title") or room_id
    key = f"{room_id}/{ident}"
    return {"kind": "owner", "name": f"{title} / {ident or '?'}",
            "project": None, "room": room, "part": part, "why": why,
            "state": _TASK_STATE.setdefault(key, {"phase": "watching"}),
            "limit": task_threshold(), "setting": "taskRotateTokens",
            "who": f"the owner ({ident})", "whose": f"{ident}'s",
            "handoverName": TASK_HANDOVER_NAME,
            "handover": task_handover_path(room, part) if room else None,
            "ids": {"roomId": room_id, "identity": ident}}


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def check(project: dict, force: bool = False, immediate: bool = False) -> dict:
    """Check one project's PO now. ``force`` ignores the threshold and the
    cool-down (it still asks for the handover first); ``immediate`` also skips
    the ask and rotates at once. Returns {result, ...} and logs it."""
    with _LOCK:
        return _check(_po_subject(project), force or immediate, immediate)


def check_task(room_id: str, identity: str = "", force: bool = False,
               immediate: bool = False) -> dict:
    """The same for a task's owner (``identity`` picks one when a task has
    several engineers)."""
    with _LOCK:
        return _check(_owner_subject(room_id, identity), force or immediate, immediate)


def _check(s: dict, force: bool, immediate: bool) -> dict:
    st, who, limit = s["state"], s["who"], s["limit"]
    now = time.time()
    st["lastCheck"] = now

    def done(result: str, quiet: bool = False, **extra) -> dict:
        st["lastResult"] = result
        st.update(extra)
        if not quiet:
            _log(f"{s['name']}: {result}")
        return {**s["ids"], "result": result, "phase": st["phase"],
                "threshold": limit, **extra}

    # The scheduler checks every owner every minute: only what happens is logged.
    routine = s["kind"] == "owner"
    room, part = s["room"], s["part"]
    if room is None:
        st["phase"] = "watching"
        return done(f"nothing to watch — {s['why']}", quiet=routine)
    sid = part["sessionId"]
    if st.get("sessionId") != sid:
        # A new session (a rotation, a reassignment): any pending ask was for
        # the old one.
        st.update(phase="watching", sessionId=sid)
    live = _pty(part) is not None
    if not live:
        st["phase"] = "watching"
        return done(f"{who} is not running — nothing to do", quiet=routine)
    tpath, reader = _transcript_of(part)
    since = -1
    if st["phase"] == "asked":
        # A Codex resume can move the conversation to a new rollout file: all
        # of a file that is not the one asked in came after the ask.
        since = st.get("askSize", -1) if str(tpath) == st.get("askPath") else 0
    tr = reader(tpath, since)
    st["tokens"] = tr["tokens"]

    if st["phase"] == "asked":
        waited = now - float(st.get("askedAt") or now)
        idle = _idle(part, tr)
        if idle and tr["promptSince"]:
            return _rotate(s, tr, done, answered=True)
        if idle and waited > ASK_TIMEOUT_S:
            return _rotate(s, tr, done, answered=False)
        if waited > GIVE_UP_S:
            st.update(phase="watching", lastAttempt=now)
            return done(f"gave up waiting for the handover after {int(waited // 60)} min "
                        f"({who} stayed busy) — will ask again later")
        return done(f"waiting for {who} to update its handover "
                    f"({int(waited // 60)} min so far)", quiet=routine)

    if limit <= 0 and not force:
        return done(f"rotation is off ({s['setting']} = 0)", quiet=routine)
    if tr["tokens"] is None:
        return done("no model call in the transcript yet", quiet=routine)
    if tr["tokens"] < limit and not force:
        return done(f"{_k(tr['tokens'])} tokens, under the {_k(limit)} limit", quiet=routine)
    young = now - max(_session_started(part), float(st.get("lastAttempt") or 0))
    if young < COOLDOWN_S and not force:
        return done(f"{_k(tr['tokens'])} tokens, over the limit, but the last rotation "
                    f"or attempt was {int(young // 60)} min ago", quiet=routine)
    if not _idle(part, tr):
        return done(f"{_k(tr['tokens'])} tokens, over the limit — waiting for {who} "
                    f"to be idle", quiet=routine)
    if immediate:
        return _rotate(s, tr, done, answered=False, asked=False)
    hp = s["handover"]
    if s["kind"] == "po":
        ask = (f"[handover] Your conversation has reached {_k(tr['tokens'])} tokens "
               f"(the limit is {_k(limit)}), so the hub will start a fresh PO session that "
               f"picks up from your written handover instead of this history. Bring {hp} "
               f"up to date now: priorities, decisions and why, what is in flight, what you "
               f"have promised {_d.operator_name()}. Anything that is not in that file or in "
               f"ROADMAP.md will be forgotten. When it is current, end your turn; the hub "
               f"rotates you as soon as you are idle.")
    else:
        ask = (f"[handover] Your conversation has reached {_k(tr['tokens'])} tokens "
               f"(the limit is {_k(limit)}), so the hub will start a fresh session for you "
               f"that continues this task from a written handover instead of this history. "
               f"Write {hp} now (replace it if it exists), holding: the current goal and "
               f"how far you got; decisions and why; branch and commits; changed files; "
               f"tests run and their results; open review findings; blockers; and the "
               f"exact next action. Anything that is not in that file, the repo or the "
               f"task's spec will be forgotten. When it is written, end your turn without "
               f"messaging anyone; the hub rotates you as soon as you are idle.")
    sess = _pty(part)
    sess.send_line(ask)
    # The ask's own submit: anything typed into the terminal after it means
    # someone is working with the agent, and the rotation waits.
    st.update(phase="asked", askedAt=now, askSize=tr["size"], askPath=str(tpath),
              handoverAtAsk=_mtime(hp), tokensAtAsk=tr["tokens"],
              askSubmit=float(sess.last_submit() or time.time()))
    return done(f"{_k(tr['tokens'])} tokens, over the {_k(limit)} limit — "
                f"asked {who} to update its handover")


# ---------------------------------------------------------------------------
# The rotation itself
# ---------------------------------------------------------------------------

def first_prompt(project: dict, room: dict, old_sid: str, tokens) -> str:
    name = project.get("name", project["id"])
    hp = handover_path(project)
    rp = _d.roadmap_path(project)
    parts = [
        f"[rotation] You are the product owner (PO) of the project '{name}', taking "
        f"over from the previous PO session of this task. Its conversation had grown "
        f"to {_k(tokens)} tokens, and every wake re-sends the whole conversation, so "
        f"the hub started you fresh. The old conversation is kept on disk (session "
        f"{old_sid}); do not load it."]
    # The spec was the old session's first prompt: one-shot steps it has already
    # carried out. Given as instructions it would be run again, so it is only
    # background here, and the handover — read last, and first — wins.
    spec = (room.get("spec") or "").strip()
    if spec:
        if len(spec) > _SPEC_MAX:
            spec = spec[:_SPEC_MAX] + "\n…(cut; the task's spec has the rest)"
        parts.append(
            "For background only, this task's original brief — the previous session "
            "already acted on it, so do not repeat any step it asks for; where it and "
            f"the handover disagree, the handover wins:\n\n<original-brief>\n{spec}\n"
            "</original-brief>")
    parts += [
        f"Keep {HANDOVER_NAME} current as you work — the next fresh session starts "
        f"from it too. Task reports and progress digests will wake you; the ensemble_* "
        f"tools show the tasks.",
        _d.OWNER_OUTPUT_NOTE,
        f"Now, before anything else, read {hp} (your handover) and {rp} (the roadmap). "
        f"They are everything you know about this project: priorities, decisions and "
        f"why, what is in flight, what has been promised to {_d.operator_name()}. "
        f"Then reply with a short summary of where things stand, and wait.",
    ]
    return "\n\n".join(parts)


def task_first_prompt(room: dict, old_sid: str, tokens, hp: Path, solo: bool) -> str:
    """A rotated owner's first prompt. It never carries the spec: a fresh
    session handed its spec as an instruction does the task again (seen
    2026-09-10). The handover is its state; the spec is only a reference."""
    rid = room["id"]
    title = room.get("title") or rid
    parts = [
        f"[rotation] You are taking over the task '{title}' ({rid}) from your previous "
        f"session on it. Its conversation had grown to {_k(tokens)} tokens, and every "
        f"model call re-sends the whole conversation, so the hub started you fresh. The "
        f"old conversation is kept on disk (session {old_sid}); do not load it.",
        f"Your state is {hp}: the handover your previous session wrote just before it "
        f"stopped: the goal and how far it got, decisions and why, branch and commits, "
        f"changed files, tests and results, open review findings, blockers, and the "
        f"exact next action. Read it first.",
        f"Do not start the task over and do not redo finished work: what the handover "
        f"says is done is done. The task's spec is on the task (ensemble_get_task "
        f"taskId={rid} messages=0) for reference only: it is not an instruction to "
        f"carry out again, and where it and the handover disagree the handover wins. "
        f"If the handover is missing or plainly stale, rebuild your state from the "
        f"branch (git log, git status, git diff) and REVIEW-LOG.md instead, still "
        f"without starting over.",
    ]
    if not solo:
        parts.append("Then read any messages that arrived meanwhile with chat_read.")
    parts.append(f"Then carry on with the next action. When this conversation grows "
                 f"long again the hub will ask you to rewrite {TASK_HANDOVER_NAME}.")
    return "\n\n".join(parts)


def _await_death(pty_id: str) -> None:
    """Let the killed terminal's death be recorded before the participant is
    rewritten, so its lastExit lands on the old run and is then dropped."""
    end = time.time() + _KILL_WAIT_S
    while time.time() < end:
        if _d.ptyrun.death_for(pty_id) is not None:
            time.sleep(0.5)     # the record is kept a moment before the hook writes it
            return
        time.sleep(0.2)


def _await_codex_session(cwd: str, since: float, taken: set) -> str:
    """The id of the Codex session just started in ``cwd`` (Codex takes no
    caller-supplied id), or "" if its rollout has not appeared in time — the
    dashboard's backfill then finds it later."""
    cx = _d.agents.get_agent("codex")
    end = time.time() + _CODEX_SID_WAIT_S
    while cx is not None and time.time() < end:
        try:
            sid = cx.latest_session_id_for_cwd(cwd, since=since)
        except Exception:
            sid = ""
        if sid and sid not in taken:
            return sid
        time.sleep(1.0)
    return ""


def _report_to_po(room: dict, ident: str, rec: dict, how: str) -> bool:
    """Tell the project's PO in one line, the way ensemble_report reaches it.
    True when the PO was woken."""
    proj = _d.find_project(room.get("projectId") or "")
    po_rid = ((proj or {}).get("poRoomId") or "").strip()
    if not po_rid or po_rid == room["id"]:
        return False
    po_room = _d.chatroom.get_room(po_rid)
    po_ident = _d.chatroom.po_identity(po_room) if po_room else ""
    if not po_ident:
        return False
    title = room.get("title") or room["id"]
    line = (f"{ident} was handed to a fresh session at {_k(rec['tokens'])} tokens "
            f"(limit {_k(rec['threshold'])}) {how}; it continues from "
            f"{TASK_HANDOVER_NAME}. The old conversation is kept (session "
            f"{rec['fromSessionId']}).")
    body = f"**update** — report from task *{title}* (`{room['id']}`, {ident}):\n\n{line}"
    res = _d.chatroom.post_report(po_rid, f"{ident}@{room['id']}", po_ident, body,
                                  {"reportKind": "update", "taskId": room["id"],
                                   "taskTitle": title, "reporter": ident,
                                   "noticeKind": "rotation"})
    if not res:
        return False
    return bool(_d.hub_launcher()._ring_report(po_rid, res, room["id"], title, ident,
                                               "update", line))


def _rotate(s: dict, tr: dict, done, answered: bool, asked: bool = True) -> dict:
    """End the old session and start the fresh one. From the last idle check
    until the fresh terminal is on the room, the agent is marked as rotating
    under GATE: a doorbell for it is held and typed into the fresh session
    once that has settled (see the dashboard's ``_ring``), and a Stop or
    Delete is noted and ends the fresh session."""
    key = (s["room"]["id"], s["part"]["identity"])
    flags = {"stopped": False}
    st, part = s["state"], s["part"]
    with GATE:
        # The last look, in the same step as the mark: its turn over, the
        # screen quiet, and nobody at its terminal since the handover ask.
        # Otherwise this attempt is dropped and made again after the cool-down.
        sess = _pty(part)
        tpath, reader = _transcript_of(part)
        since = float(st.get("askSubmit") or 0) if asked else time.time() - IDLE_S
        if sess is None or not _idle(part, reader(tpath)) or _typed_since(sess, since):
            st.update(phase="watching", lastAttempt=time.time())
            return done(f"{s['who']} was typed to or started working again — this "
                        f"attempt is dropped and made again later")
        _ROTATING[key] = flags
    try:
        return _rotate_marked(s, tr, done, answered, asked, key, flags)
    finally:
        _release(key)


def _release(key: tuple) -> None:
    """Clear the mark. The wakes held meanwhile go to whatever session the
    agent now has (none, if the task was stopped)."""
    with GATE:
        _ROTATING.pop(key, None)
        held = _HELD.pop(key, [])
    if held:
        _replay(key[0], key[1], held)


def _typed_since(sess, since: float) -> bool:
    """Someone typed into its terminal (``/api/pty/input``) after ``since``, or
    anything was submitted to it within IDLE_S — a doorbell rung just before
    the gate was taken may not have reached the transcript yet."""
    try:
        return (float(getattr(sess, "last_input", 0) or 0) > since
                or time.time() - float(sess.last_submit() or 0) < IDLE_S)
    except Exception:
        return True


def room_rotating(room_id: str) -> bool:
    """An agent of this task is being handed to a fresh session."""
    with GATE:
        return any(rid == room_id for rid, _ in _ROTATING)


def await_rotation(room_id: str, timeout: float = 60.0) -> bool:
    """Wait for a rotation of this task under way to finish (a Delete, after
    its Stop, so it sees the session the rotation added). False if it has not
    finished by ``timeout``."""
    end = time.time() + timeout
    while True:
        if not room_rotating(room_id):
            return True
        if time.time() >= end:
            return False
        time.sleep(0.2)


def is_rotating(room_id: str, identity: str) -> bool:
    with GATE:
        return (room_id, identity) in _ROTATING


def hold_wake(room_id: str, identity: str, wake: str) -> bool:
    """Hold a doorbell for an agent being rotated (True); it is typed into the
    fresh session. A caller that also sends holds GATE across both."""
    with GATE:
        if (room_id, identity) not in _ROTATING:
            return False
        _HELD.setdefault((room_id, identity), []).append(wake)
        return True


def note_stopped(room_id: str) -> None:
    """A Stop (or Delete) of this task: a rotation under way ends its fresh
    session instead of leaving the task running."""
    with GATE:
        for (rid, _), flags in _ROTATING.items():
            if rid == room_id:
                flags["stopped"] = True


def _replay(rid: str, ident: str, wakes: list) -> None:
    """Type held doorbells into the agent's current session once it has
    settled (drawn its screen, then quiet for IDLE_S, as for the resume note),
    one at a time, in the background."""
    def run():
        for wake in wakes:
            end = time.time() + REPLAY_WAIT_S
            while True:
                time.sleep(1)
                part = _d.chatroom.participant(_d.chatroom.get_room(rid) or {}, ident)
                sess = _pty(part or {})
                if sess is None:
                    _log(f"{rid}/{ident}: not running — {len(wakes)} held wake(s) dropped")
                    return
                tail = sess.tail()
                quiet = bool(tail) and time.time() - sess.last_output >= IDLE_S
                if (quiet and not _d.attention.looks_like_prompt(tail)) or time.time() > end:
                    break
            with GATE:
                sess.send_line(wake)
        _log(f"{rid}/{ident}: typed {len(wakes)} held wake(s) into the fresh session")
    threading.Thread(target=run, daemon=True, name=f"rotation-replay-{rid}-{ident}").start()


def _rotate_marked(s: dict, tr: dict, done, answered: bool, asked: bool,
                   key: tuple, flags: dict) -> dict:
    st, who, part = s["state"], s["who"], s["part"]
    rid, ident = key
    old_sid, old_pty = part["sessionId"], part.get("ptyId") or ""
    hp, hname, limit = s["handover"], s["handoverName"], s["limit"]
    tokens = st.get("tokensAtAsk") if asked else tr["tokens"]
    tokens = tokens or tr["tokens"] or 0
    updated = _mtime(hp) > float(st.get("handoverAtAsk") or 0) if asked else False
    owner = s["kind"] == "owner"

    if old_pty:
        _d.ptyrun.kill(old_pty)
        _await_death(old_pty)
    room_full = _d.chatroom.get_room(rid, public=False)
    fpart = _d.chatroom.participant(room_full or {}, ident)
    if fpart is None or flags["stopped"]:
        st["phase"] = "watching"
        return done(f"{s['whose']} task changed or was stopped while rotating — "
                    f"nothing started")
    agents_in = _d.chatroom.agent_participants(room_full)
    solo = room_full.get("mode") == "solo" or len(agents_in) < 2
    launcher = _d.hub_launcher()
    cwd = fpart.get("cwd") or None
    started = time.time()
    if not owner:
        prompt = first_prompt(s["project"], room_full, old_sid, tokens)
        if solo:
            info = launcher._launch_room_agent_pty(room_full, fpart, "", collab=False,
                                                   prompt=prompt, cwd=cwd)
        else:
            info = launcher._launch_room_agent_pty(room_full, fpart, prompt, collab=True,
                                                   cwd=cwd)
    else:
        # The rotation text goes where the spec would: a team owner gets its
        # collaboration briefing around it, a solo one how to report.
        text = task_first_prompt(room_full, old_sid, tokens, hp, solo)
        info = launcher._launch_room_agent_pty(room_full, fpart, text, collab=not solo,
                                               cwd=cwd)
    now = time.time()
    n = len(fpart.get("rotations") or []) + 1
    rec = {"n": n, "at": now, "fromSessionId": old_sid, "toSessionId": info["sessionId"],
           "tokens": tokens, "threshold": limit, "handover": str(hp),
           "handoverUpdated": updated, "asked": asked, "answered": answered}
    fields = {"sessionId": info["sessionId"], "ptyId": info["ptyId"],
              "cwd": info["cwd"], "pid": None}
    drop = ("lastExit", "fresh")
    if owner:
        rec.update(agent=fpart.get("agent", ""), startedAt=started)
        # The fresh session's ask dates from its launch: anything it says
        # after that is an answer to it.
        fields["rotatedAt"] = started
        drop += ("resumedAt",)
    # On the room at once, so a doorbell is held for the fresh terminal and a
    # Stop ends it. The mark is cleared only once the stopped or clean-up path
    # and a Codex session's id are settled: a Delete waiting on it then sees
    # the fresh session and deletes it too.
    with GATE:
        patched = _d.chatroom.patch_participant(rid, ident, fields,
                                                append={"rotations": rec}, drop=drop)
    if patched is None:
        _discard_fresh(info, fpart.get("agent", ""), started, agents_in)
        st["phase"] = "watching"
        return done(f"{s['whose']} task went away while rotating — the fresh session "
                    f"was ended")
    if owner and fpart.get("agent") == "codex" and not info["sessionId"]:
        sid = _await_codex_session(info["cwd"], started, _taken(agents_in))
        if sid:
            rec["toSessionId"] = info["sessionId"] = _learn_session(rid, ident,
                                                                    info["ptyId"], sid)
    with GATE:
        stopped = flags["stopped"]
        if not stopped:
            _release(key)
    if stopped:
        _d.stop_task(rid)
        st["phase"] = "watching"
        return done("the task was stopped while rotating — the fresh session was ended")
    if _d.chatroom.get_room(rid) is None:
        st["phase"] = "watching"
        return done(f"{s['whose']} task was deleted as it rotated — nothing posted")
    if not asked:
        how = "without asking for a handover first"
    elif not answered:
        how = f"after {who} did not answer the handover request"
    elif updated:
        how = f"after {who} brought {hname} up to date"
    else:
        how = f"after {who} confirmed {hname} was current"
    if not owner:
        text = (f"**New PO session** — the conversation had reached {_k(tokens)} tokens "
                f"(limit {_k(limit)}), so the hub started a fresh session {how}. It "
                f"picks up from `{HANDOVER_NAME}` and `ROADMAP.md`. The previous conversation "
                f"is kept (session `{old_sid}`).")
        _d.chatroom.post_notice(rid, SENDER, text, {"noticeKind": "rotation", "rotation": rec})
    else:
        text = (f"**New session for {ident}** — its conversation had reached {_k(tokens)} "
                f"tokens (limit {_k(limit)}), so the hub started a fresh session {how}. It "
                f"continues from `{TASK_HANDOVER_NAME}`. The previous conversation is kept "
                f"(session `{old_sid}`).")
        _d.chatroom.post_notice(rid, SENDER, text, {"noticeKind": "rotation", "rotation": rec})
        try:
            rec["poWoken"] = _report_to_po(room_full, ident, rec, how)
        except Exception as e:          # the rotation itself has happened
            _log(f"{s['name']}: could not report the rotation to the PO: {str(e)[:200]}")
    st.update(phase="watching", sessionId=info["sessionId"], lastRotation=now)
    for k in ("askedAt", "askSize", "askPath", "handoverAtAsk", "tokensAtAsk", "askSubmit"):
        st.pop(k, None)
    return done(f"rotated at {_k(tokens)} tokens {how} — new session "
                f"{info['sessionId'] or '(not known yet)'} (pty {info['ptyId']})",
                rotation=rec)


def _taken(agents_in: list) -> set:
    """Every session id the task's agents have had: never a fresh one."""
    taken = set()
    for p in agents_in:
        taken.update(_d.participant_session_ids(p))
    return taken


def _discard_fresh(info: dict, agent: str, started: float, agents_in: list) -> None:
    """The task went away while its fresh session started: end it and delete
    its conversation, so nothing of it outlives the task."""
    _d.ptyrun.kill(info["ptyId"])
    _await_death(info["ptyId"])
    sid = info["sessionId"]
    if agent == "codex":
        sid = sid or _await_codex_session(info["cwd"], started, _taken(agents_in))
        cx = _d.agents.get_agent("codex")
        if sid and cx is not None:
            cx.delete_session(sid)
    elif sid:
        _d.delete_session(sid)


def _learn_session(rid: str, ident: str, pty_id: str, sid: str) -> str:
    """Record a fresh Codex session's id on the participant and its rotation,
    unless the backfill got there first (its id wins) or the agent has moved
    on to another terminal since. Returns the id recorded."""
    room = _d.chatroom.get_room(rid, public=False)
    part = _d.chatroom.participant(room or {}, ident)
    if not part or part.get("ptyId") != pty_id:
        return sid
    sid = (part.get("sessionId") or "").strip() or sid
    rots = list(part.get("rotations") or [])
    if rots and isinstance(rots[-1], dict):
        rots[-1] = {**rots[-1], "toSessionId": sid}
    _d.chatroom.patch_participant(rid, ident, {"sessionId": sid, "rotations": rots})
    return sid


# ---------------------------------------------------------------------------
# Scheduler and status
# ---------------------------------------------------------------------------

def status() -> dict:
    out = []
    for p in _d.load_projects():
        if not (p.get("poRoomId") or "").strip():
            continue
        st = _STATE.get(p["id"], {})
        room, part, why = _po(p)
        rots = (part or {}).get("rotations") or []
        out.append({"projectId": p["id"], "name": p.get("name", ""),
                    "poRoomId": p.get("poRoomId", ""),
                    "sessionId": (part or {}).get("sessionId", ""),
                    "watchable": room is not None, "why": why,
                    "phase": st.get("phase", "watching"),
                    "tokens": st.get("tokens"),
                    "lastCheck": st.get("lastCheck", 0),
                    "lastResult": st.get("lastResult", ""),
                    "rotations": len(rots),
                    "lastRotation": rots[-1] if rots else None})
    tasks = []
    for key, st in list(_TASK_STATE.items()):
        rid, _, ident = key.partition("/")
        tasks.append({"roomId": rid, "identity": ident,
                      "sessionId": st.get("sessionId", ""),
                      "phase": st.get("phase", "watching"),
                      "tokens": st.get("tokens"),
                      "lastCheck": st.get("lastCheck", 0),
                      "lastResult": st.get("lastResult", ""),
                      "lastRotation": st.get("rotation")})
    return {"threshold": threshold(), "taskThreshold": task_threshold(),
            "projects": out, "tasks": tasks}


def _tick() -> None:
    if threshold() > 0:
        for p in _d.load_projects():
            if (p.get("poRoomId") or "").strip():
                check(p)
    if task_threshold() > 0:
        owners = running_owners()
        keys = {f"{rid}/{ident}" for rid, ident in owners}
        for gone in [k for k in _TASK_STATE if k not in keys]:
            _TASK_STATE.pop(gone, None)     # stopped, paused, or finished
        for rid, ident in owners:
            try:
                check_task(rid, ident)
            except Exception as e:          # one task must not stop the others
                _log(f"{rid}/{ident}: check failed: {str(e)[:200]}")


def start_scheduler() -> None:
    """Background loop; the thresholds are re-read every tick. The first check
    comes a tick after start, once the hub has settled."""
    def loop():
        while True:
            time.sleep(TICK_S)
            try:
                _tick()
            except Exception as e:          # keep the loop alive
                _log(f"scheduler error: {str(e)[:200]}")

    threading.Thread(target=loop, daemon=True, name="po-rotation").start()
