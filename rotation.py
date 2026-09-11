"""Rotating the PO: a fresh session from its written handover.

The PO has the longest conversation on a project, and every wake — a task's
report, a progress digest, the CEO's message — re-sends all of it. Measured on
2026-09-10 the Ensemble Dashboard PO was sending ~900k tokens per call. So
when the conversation gets long the hub starts a **fresh PO session** that
picks up from ``PO-HANDOVER.md`` (the PO's living handover in the project
home) and ``ROADMAP.md`` instead of the whole history.

How big a conversation is: the context of its latest model call, read from the
tail of its transcript (input + cache read + cache write of the last main-chain
assistant turn). Past ``poRotateTokens`` (a hub setting; 0 turns it off):

1. **Ask.** Once the PO is idle, the hub types one line into it: bring
   ``PO-HANDOVER.md`` up to date, because a fresh session starts from it.
2. **Wait** for the PO to finish that turn (its transcript ends on an
   ``end_turn`` after the ask, and its terminal has gone quiet). If it never
   answers — the line did not submit, say — it is rotated anyway once idle
   past ``ASK_TIMEOUT_S``; if it stays busy past ``GIVE_UP_S`` the attempt is
   dropped and made again later.
3. **Rotate.** The old terminal is ended, and a new session starts in the same
   room, same agent, model and working dir, whose first prompt is to read the
   handover and the roadmap. The participant keeps its identity and token, so
   reports and digests reach the new session unchanged; the old session id is
   kept in ``participant.rotations`` (its transcript stays on disk, and still
   counts in the task's cost).
4. **Say so.** A notice lands in the PO's room, and the PO's chat shows a line
   between the old conversation and the new one.

Only Claude POs are rotated for now: a Codex session mints its own id and keeps
a different transcript format.

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
SENDER = "ensemble"         # who the notice is from in the PO's room
_KILL_WAIT_S = 8.0
_TAIL_BYTES = (1 << 20, 8 << 20)
_SPEC_MAX = 6000            # the first prompt goes on a command line

_LOCK = threading.Lock()    # one check at a time: scheduler vs "check now"
_STATE: dict[str, dict] = {}  # projectId -> {phase, askedAt, ..., lastResult}


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


def threshold() -> int:
    v = clamp_tokens(_d.load_settings().get("poRotateTokens", DEFAULT_TOKENS))
    return DEFAULT_TOKENS if v is None else v


def handover_path(project: dict) -> Path:
    return Path(_d.project_home(project, create=False)) / HANDOVER_NAME


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
        start = max(0, size - want)
        try:
            with path.open("rb") as f:
                f.seek(start)
                raw = f.read(size - start)
        except OSError:
            return out
        # Walk the lines last to first, knowing where each one starts.
        lines, pos = [], start
        for ln in raw.split(b"\n"):
            lines.append((pos, ln))
            pos += len(ln) + 1
        if start > 0:
            lines = lines[1:]           # a partial first line
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
        if start == 0 or out["tokens"] is not None:
            return out
    return out


# ---------------------------------------------------------------------------
# The PO
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
    """When the PO's current session began, if a rotation started it."""
    rots = part.get("rotations") or []
    last = rots[-1] if rots and isinstance(rots[-1], dict) else {}
    if last.get("toSessionId") == part.get("sessionId"):
        return float(last.get("at") or 0)
    return 0.0


def _k(n) -> str:
    return f"{round((n or 0) / 1000)}k"


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def check(project: dict, force: bool = False, immediate: bool = False) -> dict:
    """Check one project's PO now. ``force`` ignores the threshold and the
    cool-down (it still asks for the handover first); ``immediate`` also skips
    the ask and rotates at once. Returns {result, ...} and logs it."""
    with _LOCK:
        return _check(project, force or immediate, immediate)


def _check(project: dict, force: bool, immediate: bool) -> dict:
    pid, name = project["id"], project.get("name", project["id"])
    now = time.time()
    st = _STATE.setdefault(pid, {"phase": "watching"})
    st["lastCheck"] = now
    limit = threshold()

    def done(result: str, **extra) -> dict:
        st["lastResult"] = result
        st.update(extra)
        _log(f"{name}: {result}")
        return {"projectId": pid, "result": result, "phase": st["phase"],
                "threshold": limit, **extra}

    room, part, why = _po(project)
    if room is None:
        st["phase"] = "watching"
        return done(f"nothing to watch — {why}")
    sid = part["sessionId"]
    if st.get("sessionId") != sid:
        # A new session (a rotation, a reassignment): any pending ask was for
        # the old one.
        st.update(phase="watching", sessionId=sid)
    live = _pty(part) is not None
    if not live:
        st["phase"] = "watching"
        return done("the PO is not running — nothing to do")
    tpath = _d.find_transcript(sid)
    tr = read_transcript(tpath, st.get("askSize", -1) if st["phase"] == "asked" else -1)
    st["tokens"] = tr["tokens"]

    if st["phase"] == "asked":
        waited = now - float(st.get("askedAt") or now)
        idle = _idle(part, tr)
        if idle and tr["promptSince"]:
            return _rotate(project, room, part, tr, st, done, answered=True)
        if idle and waited > ASK_TIMEOUT_S:
            return _rotate(project, room, part, tr, st, done, answered=False)
        if waited > GIVE_UP_S:
            st.update(phase="watching", lastAttempt=now)
            return done(f"gave up waiting for the handover after {int(waited // 60)} min "
                        f"(the PO stayed busy) — will ask again later")
        return done(f"waiting for the PO to update its handover "
                    f"({int(waited // 60)} min so far)")

    if limit <= 0 and not force:
        return done("rotation is off (poRotateTokens = 0)")
    if tr["tokens"] is None:
        return done("no model call in the transcript yet")
    if tr["tokens"] < limit and not force:
        return done(f"{_k(tr['tokens'])} tokens, under the {_k(limit)} limit")
    young = now - max(_session_started(part), float(st.get("lastAttempt") or 0))
    if young < COOLDOWN_S and not force:
        return done(f"{_k(tr['tokens'])} tokens, over the limit, but the last rotation "
                    f"or attempt was {int(young // 60)} min ago")
    if not _idle(part, tr):
        return done(f"{_k(tr['tokens'])} tokens, over the limit — waiting for the PO to be idle")
    if immediate:
        return _rotate(project, room, part, tr, st, done, answered=False, asked=False)
    hp = handover_path(project)
    ask = (f"[handover] Your conversation has reached {_k(tr['tokens'])} tokens "
           f"(the limit is {_k(limit)}), so the hub will start a fresh PO session that "
           f"picks up from your written handover instead of this history. Bring {hp} "
           f"up to date now: priorities, decisions and why, what is in flight, what you "
           f"have promised {_d.operator_name()}. Anything that is not in that file or in "
           f"ROADMAP.md will be forgotten. When it is current, end your turn; the hub "
           f"rotates you as soon as you are idle.")
    _pty(part).send_line(ask)
    st.update(phase="asked", askedAt=now, askSize=tr["size"], handoverAtAsk=_mtime(hp),
              tokensAtAsk=tr["tokens"])
    return done(f"{_k(tr['tokens'])} tokens, over the {_k(limit)} limit — "
                f"asked the PO to update its handover")


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


def _await_death(pty_id: str) -> None:
    """Let the killed terminal's death be recorded before the participant is
    rewritten, so its lastExit lands on the old run and is then dropped."""
    end = time.time() + _KILL_WAIT_S
    while time.time() < end:
        if _d.ptyrun.death_for(pty_id) is not None:
            time.sleep(0.5)     # the record is kept a moment before the hook writes it
            return
        time.sleep(0.2)


def _rotate(project: dict, room: dict, part: dict, tr: dict, st: dict, done,
            answered: bool, asked: bool = True) -> dict:
    rid, ident = room["id"], part["identity"]
    old_sid, old_pty = part["sessionId"], part.get("ptyId") or ""
    hp = handover_path(project)
    tokens = st.get("tokensAtAsk") if asked else tr["tokens"]
    tokens = tokens or tr["tokens"] or 0
    updated = _mtime(hp) > float(st.get("handoverAtAsk") or 0) if asked else False

    if old_pty:
        _d.ptyrun.kill(old_pty)
        _await_death(old_pty)
    room_full = _d.chatroom.get_room(rid, public=False)
    fpart = _d.chatroom.participant(room_full or {}, ident)
    if fpart is None:
        st["phase"] = "watching"
        return done("the PO's task changed while rotating — nothing started")
    agents_in = _d.chatroom.agent_participants(room_full)
    solo = room_full.get("mode") == "solo" or len(agents_in) < 2
    prompt = first_prompt(project, room_full, old_sid, tokens)
    launcher = _d.hub_launcher()
    cwd = fpart.get("cwd") or None
    if solo:
        info = launcher._launch_room_agent_pty(room_full, fpart, "", collab=False,
                                               prompt=prompt, cwd=cwd)
    else:
        info = launcher._launch_room_agent_pty(room_full, fpart, prompt, collab=True, cwd=cwd)
    now = time.time()
    n = len(fpart.get("rotations") or []) + 1
    rec = {"n": n, "at": now, "fromSessionId": old_sid, "toSessionId": info["sessionId"],
           "tokens": tokens, "threshold": threshold(), "handover": str(hp),
           "handoverUpdated": updated, "asked": asked, "answered": answered}
    _d.chatroom.patch_participant(rid, ident,
                                  {"sessionId": info["sessionId"], "ptyId": info["ptyId"],
                                   "cwd": info["cwd"], "pid": None},
                                  append={"rotations": rec}, drop=("lastExit", "fresh"))
    if not asked:
        how = "without asking for a handover first"
    elif not answered:
        how = "after the PO did not answer the handover request"
    elif updated:
        how = f"after the PO brought {HANDOVER_NAME} up to date"
    else:
        how = f"after the PO confirmed {HANDOVER_NAME} was current"
    text = (f"**New PO session** — the conversation had reached {_k(tokens)} tokens "
            f"(limit {_k(threshold())}), so the hub started a fresh session {how}. It "
            f"picks up from `{HANDOVER_NAME}` and `ROADMAP.md`. The previous conversation "
            f"is kept (session `{old_sid}`).")
    _d.chatroom.post_notice(rid, SENDER, text, {"noticeKind": "rotation", "rotation": rec})
    st.update(phase="watching", sessionId=info["sessionId"], lastRotation=now)
    for k in ("askedAt", "askSize", "handoverAtAsk", "tokensAtAsk"):
        st.pop(k, None)
    return done(f"rotated at {_k(tokens)} tokens {how} — new session "
                f"{info['sessionId']} (pty {info['ptyId']})", rotation=rec)


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
    return {"threshold": threshold(), "projects": out}


def _tick() -> None:
    if threshold() <= 0:
        return
    for p in _d.load_projects():
        if (p.get("poRoomId") or "").strip():
            check(p)


def start_scheduler() -> None:
    """Background loop; the threshold is re-read every tick. The first check
    comes a tick after start, once the hub has settled."""
    def loop():
        while True:
            time.sleep(TICK_S)
            try:
                _tick()
            except Exception as e:          # keep the loop alive
                _log(f"scheduler error: {str(e)[:200]}")

    threading.Thread(target=loop, daemon=True, name="po-rotation").start()
