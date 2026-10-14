"""Whether the lines the hub types into agents arrive, and an alarm when not.

Every message, digest, wake and point reminder the hub delivers to a running
agent is typed into its terminal (``dashboard._type_input``). From 10-13 18:11
to 10-14 07:31 every one of them was refused before it was typed (the seat
check of ``rotation.reserve_hub_line`` compared terminal objects that the PTY
host client handed out fresh per lookup), each logged as "the agent stopped
before the message could be typed" while the agents sat idle, and nobody
noticed for 13 hours (P190).

So each typed line's outcome is counted per agent (``record``), and lines to
an agent that is idle and alive that keep being refused raise an alarm, once
per cause: ``REPEATS`` refusals within ``WINDOW_S``, or the first refusal of a
kind that should never happen to an idle prompt (``NEVER``). An alarm is put
where the person looks — the affected tasks' Needs you item (``by_room``,
read by attention) and one hub row in each affected project's PO chat — and
in ``logs/hub-typing.log`` and the hub log. It clears when a line reaches each
agent it names again, and says so in the same places.

Refusals that are not faults are not counted: a terminal that is gone (the
agent stopped) or an agent being handed to a fresh session.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

_d = None  # the dashboard module, set by bind()

WINDOW_S = 600.0
REPEATS = 3
NOTICE_KIND = "hub-typing"

# Why a hub line was not typed, in the person's words.
CAUSES = {
    "own-seat": "the hub's check of which terminal belongs to the agent rejected the "
                "agent's own terminal",
    "write": "the terminal refused the typed text while the agent was running",
    "busy": "another hub line to the agent was still marked as being typed",
    "seat": "the task records another terminal for the agent than the one typed into",
    "refused": "the hub refused to type the line",
}
# An alarm at the first one. A write error ("write") is not: one can be a
# passing hiccup of a live terminal, so it needs REPEATS like the rest.
NEVER = {"own-seat"}
NOT_FAULTS = {"gone", "rotating"}       # not counted at all

_LOCK = threading.Lock()
_FAILS: dict[tuple, deque] = {}         # (room, identity) -> deque[(ts, cause)]
_COUNTS: dict[tuple, dict] = {}         # (room, identity) -> {ok, refused, last, lastCause}
_ALARMS: dict[str, dict] = {}           # cause -> {since, agents: {key: ts}, rooms: set}


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


def reset() -> None:
    with _LOCK:
        _FAILS.clear()
        _COUNTS.clear()
        _ALARMS.clear()


def _key(sess) -> tuple[str, str]:
    meta = getattr(sess, "meta", None) or {}
    rid, ident = str(meta.get("room") or ""), str(meta.get("identity") or "")
    return (rid, ident) if rid else ("", str(getattr(sess, "id", "") or ""))


def _idle(sess) -> bool:
    """Alive, and quiet as long as the PO rotation's idle (a turn repaints
    every second, so quiet means at its prompt)."""
    try:
        return bool(sess.alive()) and \
            time.time() - float(sess.last_output or 0) >= _d.rotation.IDLE_S
    except Exception:       # noqa: BLE001 — unknown is not idle
        return False


def log(msg: str) -> None:
    print(f"[hub-typing] {msg}", flush=True)
    try:
        path = Path(_d.DASHBOARD_DIR) / "logs" / "hub-typing.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def record(sess, ok: bool, cause: str = "") -> None:
    """The outcome of one hub-typed line into ``sess``. Never raises."""
    try:
        _record(sess, ok, cause)
    except Exception as exc:    # noqa: BLE001 — counting never stops a delivery
        print(f"[hub-typing] not counted: {exc!r}", flush=True)


def _record(sess, ok: bool, cause: str) -> None:
    key = _key(sess)
    now = time.time()
    raised, cleared = None, []
    idle = False if ok or cause in NOT_FAULTS else _idle(sess)
    with _LOCK:
        c = _COUNTS.setdefault(key, {"ok": 0, "refused": 0, "last": 0.0, "lastCause": ""})
        c["last"] = now
        if ok or cause == "gone":
            # A line that arrived, or an agent no longer there: either way
            # no alarm names it any more.
            _FAILS.pop(key, None)
            cleared = [(n, a, not ok) for n, a in _settle(lambda k: k == key)]
        if ok:
            c["ok"] += 1
        else:
            c["refused"] += 1
            c["lastCause"] = cause or "refused"
        if not ok and cause not in NOT_FAULTS and idle:
            cause = cause if cause in CAUSES else "refused"
            fails = _FAILS.setdefault(key, deque())
            fails.append((now, cause))
            while fails and now - fails[0][0] > WINDOW_S:
                fails.popleft()
            same = sum(1 for _, k in fails if k == cause)
            if cause in NEVER or same >= REPEATS:
                alarm = _ALARMS.get(cause)
                if alarm is None:
                    alarm = _ALARMS[cause] = {"since": now, "agents": {}, "rooms": set()}
                    raised = (cause, alarm, True)
                alarm["agents"].setdefault(key, now)
                new_rooms = {key[0]} - alarm["rooms"] if key[0] else set()
                alarm["rooms"] |= new_rooms
                if raised is None and new_rooms:
                    raised = (cause, alarm, False)  # the same cause, another task
    if raised:
        _raise(*raised, key, idle_fails=len(_FAILS.get(key) or ()))
    for name, alarm, ended in cleared:
        _clear(name, alarm, ended)


def _settle(gone) -> list:
    """Take the agents ``gone(key)`` names out of every alarm (under _LOCK);
    the alarms left with none, removed and returned for _clear."""
    cleared = []
    for name, alarm in list(_ALARMS.items()):
        for k in [k for k in alarm["agents"] if gone(k)]:
            alarm["agents"].pop(k)
            if not alarm["agents"]:
                cleared.append((name, _ALARMS.pop(name)))
    return cleared


def forget_room(rid: str, identity: str = "") -> None:
    """A task stopped or deleted (or, with ``identity``, one agent's terminal
    ended from the page): its agents leave every alarm (an alarm left naming
    nobody clears) and their refusals are dropped."""
    if not rid:
        return

    def gone(k):
        return k[0] == rid and (not identity or k[1] == identity)
    try:
        with _LOCK:
            for k in [k for k in _FAILS if gone(k)]:
                _FAILS.pop(k)
            cleared = _settle(gone)
        for name, alarm in cleared:
            _clear(name, alarm, ended=True)
    except Exception as exc:    # noqa: BLE001 — a Stop never fails on it
        print(f"[hub-typing] {rid}: not forgotten: {exc!r}", flush=True)


def _who(key: tuple) -> str:
    rid, ident = key
    room = _d.chatroom.get_room(rid, public=False) if rid else None
    if not room:
        return f"{ident or 'an agent'}"
    no = room.get("no")
    return f"{ident} in #{no}" if no else f"{ident} in '{(room.get('title') or rid)[:40]}'"


def _po_rooms(rooms: set) -> set[str]:
    """The PO chat of each task's project (a PO's own room is its own)."""
    out = set()
    projects = {p["id"]: p for p in _d.load_projects()}
    po_rooms = {p.get("poRoomId") for p in projects.values()} - {None, ""}
    for rid in rooms:
        if rid in po_rooms:
            out.add(rid)
            continue
        room = _d.chatroom.get_room(rid, public=False)
        pid = _d.ensemble_tools._project_of_room(room) if room else ""
        po = (projects.get(pid) or {}).get("poRoomId") or ""
        if po:
            out.add(po)
    return out


def _raise(cause: str, alarm: dict, first: bool, key: tuple, idle_fails: int = 0) -> None:
    why = CAUSES.get(cause, cause)
    who = _who(key)
    if first:
        log(f"ALARM ({cause}): hub lines to idle agents are refused: {why}; "
            f"first seen for {who} ({idle_fails} refused in {int(WINDOW_S // 60)} min)")
    text = (f"Hub lines are not reaching {who}: {why}. Messages, digests and wakes the "
            f"hub types into it are not arriving. Details in logs/hub-typing.log.")
    targets = _po_rooms({key[0]} if key[0] else set())
    with _LOCK:
        told = alarm.setdefault("told", set())
        new = targets - told
        told |= new
    for po in sorted(new):
        _d.chatroom.post_notice(po, _d.digest.SENDER, text, {"noticeKind": NOTICE_KIND})


def _clear(cause: str, alarm: dict, ended: bool = False) -> None:
    """``ended``: the last agent it named stopped (or its task went), so
    nothing showed the lines arriving again."""
    why = CAUSES.get(cause, cause)
    raised = time.strftime('%H:%M', time.localtime(alarm['since']))
    if ended:
        log(f"cleared ({cause}): the agents it named are no longer running (raised {raised})")
        text = f"The hub-typing alarm is over: the agent it named is no longer running (it was: {why})."
    else:
        log(f"cleared ({cause}): hub lines reach every agent it named again (raised {raised})")
        text = f"Hub lines reach the agents again (it was: {why})."
    for po in alarm.get("told") or ():
        _d.chatroom.post_notice(po, _d.digest.SENDER, text, {"noticeKind": NOTICE_KIND})


def by_room() -> dict[str, dict]:
    """{room: {reason, since, identity, cause}} for each task an alarm names:
    its Needs you item (attention)."""
    out: dict[str, dict] = {}
    with _LOCK:
        for cause, alarm in _ALARMS.items():
            for (rid, ident), since in alarm["agents"].items():
                if rid and rid not in out:
                    out[rid] = {"reason": f"The hub cannot type into {ident}: "
                                          f"{CAUSES.get(cause, cause)}. Lines to it are not "
                                          f"arriving.",
                                "since": since, "identity": ident, "cause": cause}
    return out


def view() -> dict:
    """What /api/hub-typing answers: outcomes per agent, and the alarms up."""
    with _LOCK:
        return {"agents": [{"roomId": k[0], "identity": k[1], **v} for k, v in _COUNTS.items()],
                "alarms": [{"cause": c, "since": a["since"], "why": CAUSES.get(c, c),
                            "agents": [{"roomId": k[0], "identity": k[1]} for k in a["agents"]]}
                           for c, a in _ALARMS.items()]}
