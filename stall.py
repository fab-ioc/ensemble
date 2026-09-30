"""A task that stopped without saying so: its owner is idle, nothing is open.

Task → PO → CEO (ED-138). A task in a project with a PO reports to the PO; an
ask of its is the PO's to answer, and never the CEO's (attention.py). What is
left is the task that simply stopped: its owner idle, no ask open (to the PO
or anyone), no review running, nothing handed to a teammate — OP-140
(2026-09-30) said "starting phase (b)" and then sat idle for hours.

Such a task is **stalled**. The hub

1. types its owner one line, ``[stalled] Carry on …``, once the owner has been
   idle ``GRACE_S``;
2. if the owner is still idle ``GRACE_S`` after that with nothing open, tells
   the PO once: a line in the PO's room, typed into the PO when it is idle.

Once per **episode**: an episode is what happened since the last thing that
could have set the owner going — a teammate's or a person's message, a report
of its own, a spec amendment, an answer typed into it, a rotation. Anything
new starts a new episode; nothing new, nothing is said again. The records are
kept in ``DASHBOARD_DIR/stalls.json``, so a hub restart does not repeat them.

Tasks with no PO are the attention detector's (the CEO's bell), as before.
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


TICK_S = 60                 # how often the tasks are looked at
GRACE_S = 600               # idle this long before a nudge, and again before the PO hears
SENDER = "ensemble"         # who the line in the PO's room is from
PREFIX = "[stalled] "       # how the typed line starts (HUB_INPUT_KINDS)
NUDGE = (PREFIX + "You are idle and nothing of yours is open. Carry on from "
         "TASK-HANDOVER.md; if you are blocked or need a decision, report it with "
         "ensemble_report; if you are finished, report completed.")
_OWN_SUBMIT_S = 60          # a submit this close after the nudge is the nudge itself
_QUIET = ("inreview", "done")

_LOCK = threading.Lock()
_LAST = 0.0


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] stall: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "stalls.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save: {e}")


# ---------------------------------------------------------------------------
# Is it stalled
# ---------------------------------------------------------------------------

def episode(room: dict, owner: str, last_submit: float, nudged_at: float) -> float:
    """When the owner was last given something to go on: the newest message in
    the room not its own, its newest report, a spec amendment, an answer
    typed into it, its rotation, a line submitted to its terminal (not the
    nudge)."""
    ts = [0.0]
    for m in room.get("messages") or []:
        if m.get("from") != owner:
            ts.append(float(m.get("ts") or 0))
    ts.append(float((room.get("lastReport") or {}).get("ts") or 0))
    ts.append(float(room.get("specAt") or 0))
    part = _d.chatroom.participant(room, owner) or {}
    for k in ("answeredAt", "rotatedAt", "resumedAt"):
        try:
            ts.append(float(part.get(k) or 0))
        except (TypeError, ValueError):
            pass
    if last_submit and not (nudged_at and 0 <= last_submit - nudged_at < _OWN_SUBMIT_S):
        ts.append(float(last_submit))
    return max(ts)


def why_not(room: dict, owner: str) -> str:
    """"" when the room, by what it holds, could be stalled; else why not.
    The terminals are looked at by the caller."""
    if not room.get("launched", True):
        return "a draft"
    if room.get("status") == "paused":
        return "paused"
    if _d.workflow_of(room) in _QUIET:
        return "in review or done"
    if not _d.room_po_id(room):
        return "no PO"                  # the CEO's bell (attention.py) has it
    if _d.attention.open_ask(room) is not None:
        return "something of it is open"
    msgs = room.get("messages") or []
    last = msgs[-1] if msgs else {}
    if last.get("from") == owner and last.get("rang"):
        return "handed to a teammate"
    return ""


def _busy(part: dict) -> bool:
    rot = _d.rotation
    if rot._pty(part) is None:
        return False
    tpath, reader = rot._transcript_of(part)
    return not rot._idle(part, reader(tpath))


def _look(rid: str, state: dict, now: float) -> str:
    """One room: "" when nothing was done, else what was."""
    rot, cr = _d.rotation, _d.chatroom
    with rot.GATE:
        room = cr.get_room(rid, public=False)
        if not room or not _d._room_is_live(room):
            return ""
        owners = cr.owners(room)
        owner = owners[0] if owners else ""
        part = cr.participant(room, owner) if owner else None
        if not part or why_not(room, owner):
            return ""
        if rot.is_rotating(rid, owner) or rot.awaiting_handover(rid, owner):
            return ""
        sess = rot._pty(part)
        if sess is None:
            return ""
        # A teammate still at work (a review running) is not a stall.
        if any(_busy(p) for p in cr.agent_participants(room) if p.get("identity") != owner):
            return ""
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return ""
        try:
            idle = float(sess.info().get("idleSeconds") or 0)
            last_submit = float(sess.last_submit() or 0)
        except Exception:
            return ""
        if idle < GRACE_S:
            return ""
        rec = state.get(rid) or {}
        ep = episode(room, owner, last_submit, float(rec.get("nudgedAt") or 0))
        if rec.get("episode") != ep:
            rec = {"episode": ep}
        rec["current"] = True           # stopped right now (record())
        state[rid] = rec
        if not rec.get("nudgedAt"):
            if not _d._type_input(sess, NUDGE):
                return ""
            rec.update(nudgedAt=now, owner=owner, idleSince=now - idle)
            state[rid] = rec
            return "nudged"
        if rec.get("toldAt") or now - float(rec["nudgedAt"]) < GRACE_S:
            return ""
        rec["toldAt"] = now
        state[rid] = rec
    told = _tell_po(room, rec, now)
    return "PO told" + ("" if told else " (in its chat; not typed)")


def _tell_po(room: dict, rec: dict, now: float) -> bool:
    """A line in the PO's room, typed into the PO too when it is idle."""
    rot, cr = _d.rotation, _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    if not po_room:
        return False
    ident = cr.po_identity(po_room)
    since = time.strftime("%H:%M", time.localtime(float(rec.get("idleSince") or now)))
    nudged = time.strftime("%H:%M", time.localtime(float(rec.get("nudgedAt") or now)))
    label = _d.task_label(room) or room.get("id", "")
    title = room.get("title", "")
    body = (f"**{label} stalled since {since}** — {title}\n\n"
            f"Its owner is idle with nothing open: no question, block or report "
            f"waiting, no review running. The hub nudged it at {nudged} and it did "
            f"not carry on. Look at it (`ensemble_get_task {label}`) and steer it, "
            f"or stop it.")
    cr.post_report(po, SENDER, ident, body,
                   {"reportKind": "digest", "stalledTask": room.get("id", "")}, wake=False)
    with rot.GATE:
        part = cr.participant(po_room, ident) if ident else None
        sess = rot._pty(part) if part else None
        if sess is None or rot.is_rotating(po, ident) or rot.awaiting_handover(po, ident):
            return False
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return False
        proj = next((p.get("name", "") for p in _d.load_projects()
                     if (p.get("poRoomId") or "") == po), "")
        wake = (f"[digest] {proj or 'Your project'}: {label} stalled since {since} — its "
                f"owner is idle with nothing open and did not carry on after the hub's "
                f"nudge at {nudged}. Details with ensemble_get_task {label}.")
        return _d._type_input(sess, wake)


# ---------------------------------------------------------------------------
# Looking
# ---------------------------------------------------------------------------

def tick(now: float | None = None) -> dict[str, str]:
    """Look at every task once. Returns {roomId: what was done}."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        before = json.dumps(state, sort_keys=True)
        out = {}
        try:
            ids = [p.stem for p in _d.chatroom.ROOMS_DIR.glob("room-*.json")]
        except OSError:
            ids = []
        for rid in ids:
            if rid in state:
                state[rid]["current"] = False   # until this look finds it stopped again
            try:
                did = _look(rid, state, now)
            except Exception as e:          # one task must not stop the others
                _log(f"{rid}: {str(e)[:200]}")
                continue
            if did:
                out[rid] = did
                _log(f"{rid}: {did}")
        for rid in [r for r in state if r not in ids]:
            state.pop(rid, None)
        if json.dumps(state, sort_keys=True) != before:
            _save(state)
        return out


def records() -> dict[str, dict]:
    """Per task the hub has nudged and that is still stopped: ``{episode,
    nudgedAt, toldAt, idleSince}``. One read of the file for a whole list."""
    return {rid: rec for rid, rec in _load().items()
            if rec.get("current") and rec.get("nudgedAt")}


def record(room_id: str) -> dict | None:
    """``records()`` for one task, or None."""
    return records().get(room_id)


def maybe_tick() -> None:
    """Called from the progress check's scheduler loop: once per ``TICK_S``,
    the first time a minute after the hub started."""
    global _LAST
    now = time.time()
    if not _LAST:
        _LAST = now
        return
    if now - _LAST >= TICK_S:
        _LAST = now
        tick(now)
