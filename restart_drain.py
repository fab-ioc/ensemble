"""A hub restart at a safe point (#211): the hub drains before it restarts.

Until now a restart (the PO's ``ensemble_restart_hub``, the page's Update now)
stopped every agent wherever it was: a test suite running in a background
shell was lost, an agent in the middle of a turn got one line and redid its
work (0.8 to 3.4M tokens each), and the PO polled ``/api/ptys`` for an hour
looking for a quiet moment that never came.

A ``safe`` request drains first:

* **Nothing new starts.** While a drain is on (``holding()``), the hub holds
  what it would start or type on its own or for a person: task launches,
  review starts, sends of the CEO's or a PO's, the scheduled wakes ([digest],
  [board], [due], …), rotations, and a wake for an agent that is already at
  its safe point. What is held (``hold``) is kept in the drain's file and let
  go after the restart, after each agent's resume line.
* **Every working agent is told once** (``DRAIN_LINE``): reach a safe point,
  then call ``ensemble_ready_for_restart`` with what it will do next. An idle
  agent counts as ready at once and is typed nothing. One idle at its prompt
  with a command still running in the background counts as working: it is
  told, and says it will rerun the command.
* **The restart goes ahead** when every agent is ready, at the deadline, or
  when the CEO or the PO that asked forces it; either can cancel it. A failed
  preflight cancels it.
* **Each agent resumes where it said** (``RESUME_LINE``); one forced before it
  was ready gets the interrupted line and what its screen last showed.

This module is the state machine and its file; the dashboard drives it (the
monitor's tick, the agents it observes, what it types). The functions that
change a state take the state and ``now`` and touch nothing else, so the
tests run them without a hub. The file is ``restart-drain.json`` in the state
dir: the hub that comes back reads it to type each agent its line.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


FILE_NAME = "restart-drain.json"
DEFAULT_DEADLINE_MIN = 20
MIN_DEADLINE_MIN, MAX_DEADLINE_MIN = 1, 240
# Told, then idle this long without calling the tool: it stopped at a safe
# point all the same (an agent whose turn ended on the line it was typed).
SETTLED_S = 30
TICK_S = 2.0

# draining: telling agents and waiting for them; restarting: the restart was
# started (its helper preflights, waits, stops the hub). Both hold.
HOLDING = ("draining", "restarting")
# ready: it called the tool; idle: idle when looked at, typed nothing;
# stopped: its terminal ended; settled: told, then idle without calling.
READY_STATES = ("ready", "idle", "stopped", "settled")

DRAIN_PREFIX = "[hub restart] "
DRAIN_LINE = (
    DRAIN_PREFIX + "The hub restarts for an update. Reach a safe point: finish or stop your "
    "current step, commit work in progress, do not start long commands; then call "
    "`ensemble_ready_for_restart` with one line saying exactly what you will do next (e.g. "
    "'rerun the full suite, then ask for review 1'). Do not wait for background commands "
    "you can rerun.")
RESUME_LINE = ("[hub restarted] The hub restarted for {reason}. You said you would next: "
               "{next}. Carry on from there.")
CANCEL_READY_LINE = (DRAIN_PREFIX + "The restart was called off ({why}): nothing was "
                     "restarted. Carry on with what you said you would do next: {next}.")
CANCEL_TOLD_LINE = (DRAIN_PREFIX + "The restart was called off ({why}): nothing was "
                    "restarted. Carry on as you were; ensemble_ready_for_restart is not needed.")


def key(room_id: str, identity: str) -> str:
    return f"{room_id}/{identity}"


# ---------------------------------------------------------------------------
# The state machine: pure functions of (state, …, now)
# ---------------------------------------------------------------------------

def new_state(reason: str, by: dict, action: str, now: float, deadline_s: float) -> dict:
    return {"id": uuid.uuid4().hex[:12], "state": "draining",
            "action": "update" if action == "update" else "restart",
            "reasons": [reason] if reason else [],
            "requestedBy": [dict(by)], "requestedAt": now,
            "deadline": now + max(0.0, float(deadline_s)),
            "agents": {}, "held": [], "observedAt": 0.0,
            "lease": "", "restartAt": 0.0, "forcedBy": "", "outcome": "", "error": "",
            "endedAt": 0.0}


def request(st: dict | None, reason: str, by: dict, action: str, now: float,
            deadline_s: float) -> tuple[dict, bool]:
    """A ``safe`` request: a new drain, or merged into the one under way
    (its reason added, an update wins over a plain restart, the requester
    added). Returns (state, merged). A restart already started takes no more
    requests: the caller says so."""
    reason = " ".join((reason or "").split())[:300]
    if st and st.get("state") == "draining":
        if reason and reason not in st["reasons"]:
            st["reasons"].append(reason)
        if action == "update":
            st["action"] = "update"
        if not any(_same_requester(r, by) for r in st["requestedBy"]):
            st["requestedBy"].append(dict(by))
        return st, True
    return new_state(reason, by, action, now, deadline_s), False


def _same_requester(a: dict, b: dict) -> bool:
    return (a.get("roomId") or "", a.get("identity") or "") == (b.get("roomId") or "", b.get("identity") or "")


def reason_text(st: dict) -> str:
    return "; ".join(st.get("reasons") or []) or (
        "an update" if st.get("action") == "update" else "a restart")


def _agent(st: dict, room_id: str, identity: str, label: str = "", kind: str = "") -> dict:
    a = st["agents"].get(key(room_id, identity))
    if a is None:
        a = st["agents"][key(room_id, identity)] = {
            "roomId": room_id, "identity": identity, "label": label, "kind": kind,
            "state": "", "toldAt": 0.0, "readyAt": 0.0, "next": "", "idleSince": 0.0,
            "since": 0.0}
    if label:
        a["label"] = label
    if kind:
        a["kind"] = kind
    return a


def _set(a: dict, state: str, now: float) -> None:
    if a["state"] != state:
        a["state"] = state
        a["since"] = now


def observe(st: dict, seen: dict, now: float) -> list[str]:
    """Bring each agent's state up to date from what the hub sees now:
    ``seen`` is {key: {roomId, identity, label, kind, state, background}}
    with state working | waiting | idle | stopped (attention.turn_state)
    and background True when a command of its runs while it is idle.
    Returns the keys to type the drain line to: working, not told yet."""
    tell = []
    for k, ob in seen.items():
        a = _agent(st, ob.get("roomId", ""), ob.get("identity", ""), ob.get("label", ""),
                   ob.get("kind", ""))
        if a["state"] == "ready":
            continue                    # it said where it stops: that holds
        state = ob.get("state") or "idle"
        if state == "idle" and ob.get("background"):
            state = "working"
        if state == "stopped":
            _set(a, "stopped", now)
        elif state == "idle":
            if a["toldAt"]:
                a["idleSince"] = a["idleSince"] or now
                _set(a, "settled" if now - a["idleSince"] >= SETTLED_S else "working", now)
            elif a["state"] in ("", "idle", "stopped", "waiting", "working"):
                _set(a, "idle", now)
        elif state == "waiting":
            a["idleSince"] = 0.0
            _set(a, "waiting", now)
        else:
            a["idleSince"] = 0.0
            _set(a, "working", now)
            if not a["toldAt"]:
                tell.append(k)
    for k, a in st["agents"].items():
        if k not in seen and a["state"] != "ready":
            _set(a, "stopped", now)
    st["observedAt"] = now
    return tell


def mark_told(st: dict, k: str, now: float) -> None:
    a = st["agents"].get(k)
    if a is not None:
        a["toldAt"] = now


def ready(st: dict | None, room_id: str, identity: str, next_step: str, now: float,
          label: str = "", kind: str = "") -> dict | None:
    """An agent at its safe point, with what it will do next. None when no
    drain is under way."""
    if not st or st.get("state") not in HOLDING:
        return None
    a = _agent(st, room_id, identity, label, kind)
    a["next"] = " ".join((next_step or "").split())[:500]
    a["readyAt"] = now
    _set(a, "ready", now)
    return a


def waiting_for(st: dict) -> list[dict]:
    """The agents the restart still waits for, oldest first."""
    return sorted((a for a in (st or {}).get("agents", {}).values()
                   if a["state"] not in READY_STATES), key=lambda a: (a.get("since") or 0, a["label"]))


def outcome(st: dict | None, now: float) -> str:
    """Whether the restart goes ahead now, and why: forced | ready |
    deadline, or "" to keep waiting."""
    if not st or st.get("state") != "draining":
        return ""
    if st.get("forcedBy"):
        return "forced"
    if st.get("observedAt") and not waiting_for(st):
        return "ready"
    if now >= float(st.get("deadline") or 0):
        return "deadline"
    return ""


def force(st: dict | None, by: str) -> bool:
    if not st or st.get("state") != "draining":
        return False
    st["forcedBy"] = by or "someone"
    return True


def end(st: dict, state: str, why: str, now: float) -> list[dict]:
    """End a drain (cancelled | failed | done). Returns what it held, for the
    caller to let go of, and empties it."""
    st["state"] = state
    st["outcome"] = why
    st["endedAt"] = now
    held, st["held"] = list(st.get("held") or []), []
    return held


def hold(st: dict, item: dict, now: float) -> None:
    st.setdefault("held", []).append({**item, "heldAt": now})


def holds_send(st: dict | None, room_id: str, send_key: str) -> bool:
    return bool(st and send_key and any(
        it.get("kind") == "send" and it.get("roomId") == room_id
        and (it.get("item") or {}).get("key") == send_key for it in st.get("held") or []))


def is_ready(st: dict | None, room_id: str, identity: str) -> bool:
    a = ((st or {}).get("agents") or {}).get(key(room_id, identity))
    return bool(a and a["state"] in READY_STATES)


def resume_line(st: dict, a: dict) -> str:
    """What an agent that called the tool is typed after the restart."""
    nxt = (a.get("next") or "").strip().rstrip(".") or "the step you were on"
    return RESUME_LINE.format(reason=reason_text(st), next=nxt)


def view(st: dict | None, now: float) -> dict:
    """What the dashboard's banner shows."""
    if not st:
        return {"state": "none"}
    agents = sorted(st.get("agents", {}).values(), key=lambda a: a["label"])
    return {"id": st.get("id"), "state": st.get("state"), "action": st.get("action"),
            "reason": reason_text(st), "reasons": list(st.get("reasons") or []),
            "requestedBy": [r.get("label", "") for r in st.get("requestedBy") or []],
            "requestedAt": st.get("requestedAt"), "deadline": st.get("deadline"),
            "secondsLeft": max(0, int(float(st.get("deadline") or 0) - now)),
            "waitingFor": [{"label": a["label"], "state": a["state"]} for a in waiting_for(st)],
            "agents": [{"label": a["label"], "state": a["state"], "next": a.get("next", ""),
                        "roomId": a["roomId"], "identity": a["identity"]} for a in agents],
            "held": len(st.get("held") or []), "forcedBy": st.get("forcedBy", ""),
            "outcome": st.get("outcome", ""), "error": st.get("error", ""),
            "endedAt": st.get("endedAt", 0)}


# ---------------------------------------------------------------------------
# The file, and the one copy in memory everything reads
# ---------------------------------------------------------------------------

LOCK = threading.RLock()
_CACHE: tuple[str, dict | None] | None = None
WAKE = threading.Event()        # a request or a ready: tick now, not in 2 s


def _path() -> Path:
    return Path(_d.DASHBOARD_DIR) / FILE_NAME


def current() -> dict | None:
    """The drain on file (live, or the last one ended), read once per state dir."""
    global _CACHE
    with LOCK:
        p = str(_path())
        if _CACHE is None or _CACHE[0] != p:
            try:
                st = json.loads(Path(p).read_text(encoding="utf-8"))
                st = st if isinstance(st, dict) and st.get("state") else None
            except (OSError, ValueError):
                st = None
            _CACHE = (p, st)
        return _CACHE[1]


def save(st: dict | None) -> None:
    global _CACHE
    with LOCK:
        path = _path()
        if st is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        _CACHE = (str(path), st)


def reset() -> None:
    """Forget the copy in memory (tests: a new state dir)."""
    global _CACHE
    with LOCK:
        _CACHE = None


def holding() -> bool:
    """Whether the hub holds what it would start: a drain or its restart is
    under way. Cheap: the copy in memory."""
    st = current()
    return bool(st and st.get("state") in HOLDING)


def deadline_s(settings: dict | None) -> float:
    try:
        mins = int((settings or {}).get("restartDrainMinutes", DEFAULT_DEADLINE_MIN))
    except (TypeError, ValueError):
        mins = DEFAULT_DEADLINE_MIN
    return 60.0 * max(MIN_DEADLINE_MIN, min(MAX_DEADLINE_MIN, mins))


def start_monitor(tick) -> None:
    """The drain's own thread: ``tick()`` every TICK_S, at once on WAKE."""
    def loop():
        while True:
            WAKE.wait(TICK_S)
            WAKE.clear()
            try:
                tick()
            except Exception as e:      # noqa: BLE001 — keep the loop alive
                print(f"[drain] tick failed: {e!r}", flush=True)
    threading.Thread(target=loop, daemon=True, name="restart-drain").start()
