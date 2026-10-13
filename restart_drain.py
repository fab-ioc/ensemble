"""Classic restart drain for a PTY host change or a requested safe stop.

Ordinary hub-only restarts keep the PTY host and agents running. A classic
restart drains first:

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
import sys
import threading
import time
import uuid
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


FILE_NAME = "restart-drain.json"
DEFAULT_DEADLINE_MIN = 20
MIN_DEADLINE_MIN, MAX_DEADLINE_MIN = 1, 240
# Told, then idle this long without calling the tool: told once more (the
# line may have gone in as its turn ended); then waited for until the deadline.
RETELL_S = 30
TICK_S = 2.0

# draining: telling agents and waiting for them; restarting: the restart was
# started (its helper preflights, waits, stops the hub). Both hold.
HOLDING = ("draining", "restarting")
# ready: it called the tool; idle: idle when looked at, never told, typed
# nothing; stopped: its terminal ended; waiting: a question or prompt of its
# is up (it asked the CEO or its PO): a clean step, and its answer is held.
READY_STATES = ("ready", "idle", "stopped", "waiting")

DRAIN_PREFIX = "[hub restart] "
DRAIN_LINE = (
    DRAIN_PREFIX + "The hub restarts for an update. Reach a safe point: finish or stop your "
    "current step, commit work in progress, do not start long commands; then call "
    "`ensemble_ready_for_restart` with one line saying exactly what you will do next (e.g. "
    "'rerun the full suite, then ask for review 1'). Do not wait for background commands "
    "you can rerun.")
RESUME_LINE = ("[hub restarted] The hub restarted for {reason}. You said you would next: "
               "{next}. Carry on from there.")
WAITING_LINE = ("[hub restarted] The hub restarted for {reason} while you were waiting for an "
                "answer, and the question or prompt on your screen is gone. An answer that came "
                "meanwhile follows this line; if none does, ask again.")
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
    new = new_state(reason, by, action, now, deadline_s)
    if st and st.get("pending"):
        new["pending"] = list(st["pending"])     # the last drain's, still being let go
    return new, False


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
        if state == "idle" and ob.get("background") and not a["toldAt"]:
            state = "working"           # told once; then idle is idle (it can rerun it)
        if state == "stopped":
            _set(a, "stopped", now)
        elif state == "idle":
            if a["toldAt"]:
                # Told, and idle without calling the tool: where it stands is
                # not known (a command of its may still run). Told once more,
                # then waited for: the deadline or a force cuts it off.
                a["idleSince"] = a["idleSince"] or now
                _set(a, "working", now)
                if now - a["idleSince"] >= RETELL_S and not a.get("retold"):
                    a["retold"] = True
                    tell.append(k)
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
    """End a drain (cancelled | failed | done). Returns everything not let go
    yet, for the caller to let go of: what this drain held and what an earlier
    one still had pending (carried over by ``request``). Each item stays on
    file under ``pending`` until it is let go (``settle``), so a hub that dies
    in between replays it."""
    st["state"] = state
    st["outcome"] = why
    st["endedAt"] = now
    held, st["held"] = list(st.get("held") or []), []
    st.setdefault("pending", []).extend(held)
    return list(st["pending"])


def settle(st: dict, item: dict) -> None:
    """One pending item let go: off the file."""
    hid = item.get("hid")
    st["pending"] = [it for it in st.get("pending") or []
                     if not (it.get("hid") == hid if hid else it == item)]


def hold(st: dict, item: dict, now: float) -> None:
    st.setdefault("held", []).append({**item, "heldAt": now, "hid": uuid.uuid4().hex[:12]})


def holds_send(st: dict | None, room_id: str, send_key: str) -> bool:
    return bool(st and send_key and any(
        it.get("kind") == "send" and it.get("roomId") == room_id and it.get("key") == send_key
        for it in st.get("held") or []))


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
            "external": bool(st.get("external")),
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
# hids of the held items a thread of this process is letting go (or waits
# to try again): a later drain that carried them leaves them to it.
_INFLIGHT: set[str] = set()
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
        _INFLIGHT.clear()


def holding() -> bool:
    """Whether the hub holds what it would start: a drain or its restart is
    under way. Cheap: the copy in memory."""
    st = current()
    return bool(st and st.get("state") in HOLDING)


def draining() -> bool:
    """A drain is waiting for agents: Restart now forces it."""
    st = current()
    return bool(st and st.get("state") == "draining")


def restart_now(room_id: str = "") -> dict:
    """``when: "now"`` after the drain went to restart (its helper hung, or
    there is none to watch): a plain restart. When it starts, the drain
    follows its lease, so the hub that comes back closes it and types each
    agent its line."""
    res = _d.trigger_restart(room_id, drain=True)
    if res.get("started") and res.get("leaseId"):
        with LOCK:
            st = current()
            if st and st.get("state") == "restarting":
                now = time.time()
                st.update(lease=res["leaseId"], startedAt=now, restartAt=now, retryAt=0.0,
                          error="", hubPid=os.getpid(), hubPort=_d.HUB_PORT)
                save(st)
        _log("a restart now took over the one under way")
    return res


def try_hold(item: dict, ready_only: tuple[str, str] | None = None) -> bool:
    """Hold ``item`` for after the restart when a drain is under way (under
    the lock, so a drain that just ended takes nothing more). ``ready_only``
    (room, identity): only when that agent is at its safe point (a wake for
    a working agent still goes in)."""
    with LOCK:
        st = current()
        if not st or st.get("state") not in HOLDING:
            return False
        if ready_only and not is_ready(st, *ready_only):
            return False
        hold(st, item, time.time())
        save(st)
    return True


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


# ---------------------------------------------------------------------------
# Driving a drain: what the hub sees, types, starts and lets go
# ---------------------------------------------------------------------------

_SHELLS = ("bash", "sh", "zsh", "fish", "powershell", "pwsh", "cmd")
RETRY_S = 15                    # a restart refused for the lease is tried again this often
LEASE_GONE_S = 5                # the lease gone this long after the start: the helper gave up
RESTORE_WAIT_S = 600            # a hub back on the port waits this long for the helper's restore
CANCEL_RELEASE_S = 60           # a hub that died mid-drain lets go this long after it is back
MAX_TRIES = 3                   # a held item not handed over: tried this often, then failed
HANDOVER_RETRY_S = 30           # between those tries
UPDATE_CAP_S = 1800             # the app updater (it downloads first) gets this long to stop the hub
_OBSERVE_ERROR = ""              # the last error looking at the agents (logged once)


def _log(msg: str) -> None:
    _d._restart_log(f"drain: {msg}")


def _children(procs: dict) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _stem) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    return kids


def background(pid, procs: dict, kids: dict | None = None) -> bool:
    """Whether an agent's process has a shell under it: a command it started
    in the background is still running (an idle Claude or Codex keeps no
    shell of its own). A cmd with node under it is an MCP server's launcher,
    not a command."""
    if not pid or not procs:
        return False
    kids = _children(procs) if kids is None else kids
    stack, seen = list(kids.get(int(pid), [])), set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stem = procs.get(p, (0, ""))[1].lower()
        if stem in _SHELLS and not (stem == "cmd" and _has_node(p, procs, kids)):
            return True
        stack.extend(kids.get(p, []))
    return False


def _has_node(pid: int, procs: dict, kids: dict) -> bool:
    stack, seen = list(kids.get(pid, [])), set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        if procs.get(p, (0, ""))[1].lower() == "node":
            return True
        stack.extend(kids.get(p, []))
    return False


def _names() -> tuple[dict, dict]:
    projects = _d.load_projects()
    po_rooms = {(p.get("poRoomId") or "").strip(): p for p in projects
                if (p.get("poRoomId") or "").strip()}
    return _d.project_keys(projects), po_rooms


def label_of(room: dict, part: dict, keys: dict, po_rooms: dict,
             live_agents: int = 1) -> tuple[str, str]:
    """How the banner and the log name an agent, and its kind: ED-209,
    OP-253 review, "Trading PO"; with its identity when two agents of one
    task are running."""
    rid = room.get("id", "")
    if rid in po_rooms:
        return f"{po_rooms[rid].get('name') or 'a project'} PO", "po"
    no = room.get("no")
    lab = (_d.task_numbers.label(no, keys.get(room.get("noProjectId") or "", "")) if no
           else (room.get("title") or rid)[:40])
    if _d.chatroom.is_on_mention(room, part):
        return f"{lab} review", "review"
    if live_agents > 1:
        lab += f" ({part.get('identity', '')})"
    return lab, "task"


def observe_hub() -> dict:
    """Every agent running in the terminal its seat names, as ``observe``
    takes it."""
    keys, po_rooms = _names()
    statuses = _d.attention._claude_status_by_session()
    try:
        procs = _d.peer_process.processes()
    except Exception:       # noqa: BLE001 — no background command seen, that is all
        procs = {}
    kids = _children(procs)
    rooms: dict[str, list] = {}
    for info in _d.ptyrun.list_sessions():
        meta = info.get("meta") or {}
        if info.get("alive") and meta.get("room") and meta.get("identity"):
            rooms.setdefault(meta["room"], []).append(info)
    seen = {}
    for rid, infos in rooms.items():
        room = _d.chatroom.get_room(rid, public=False)
        if room is None:
            continue
        mine = []
        for info in infos:
            part = _d.chatroom.participant(room, info["meta"]["identity"])
            if part is not None and part.get("ptyId") == info.get("id"):
                mine.append((part, info))
        workers = sum(1 for part, _ in mine if not _d.chatroom.is_on_mention(room, part))
        for part, info in mine:
            try:
                state, _src = _d.attention.turn_state(part, statuses)
            except Exception:       # noqa: BLE001 — unknown: it is asked, not assumed done
                state = "working"
            lab, kind = label_of(room, part, keys, po_rooms, workers)
            seen[key(rid, part["identity"])] = {
                "roomId": rid, "identity": part["identity"], "label": lab, "kind": kind,
                "state": state, "ptyId": info.get("id"),
                "background": state == "idle" and background(info.get("pid"), procs, kids)}
    return seen


def _sess_of(room_id: str, identity: str):
    room = _d.chatroom.get_room(room_id, public=False) or {}
    part = _d.chatroom.participant(room, identity) or {}
    sess = _d.ptyrun.get(part.get("ptyId")) if part.get("ptyId") else None
    return sess if sess is not None and sess.alive() else None


def _type(room_id: str, identity: str, line: str) -> bool:
    """Type a line into an agent; not on top of a prompt (it is told at a
    later tick, once the prompt is answered)."""
    sess = _sess_of(room_id, identity)
    if sess is None or _d.attention.looks_like_prompt(sess.tail() or ""):
        return False
    return bool(_d._type_input(sess, line))


_SCREEN_MARKS = ("●", "⏺", "•")


def screen_line(tail: str) -> str:
    """The last thing a screen shows its agent doing: its last ● / ⏺ / • line,
    with the ⎿ line under it (the command a tool runs) when there is one."""
    lines = [ln.strip() for ln in (tail or "").splitlines()]
    for i in range(len(lines) - 1, -1, -1):
        t = lines[i]
        if t[:1] in _SCREEN_MARKS and len(t) > 2:
            out = " ".join(t[1:].split())
            under = lines[i + 1] if i + 1 < len(lines) else ""
            if under[:1] == "⎿" and len(under) > 2:
                out += " — " + " ".join(under[1:].split())
            return out[:200]
    return ""


def _whole_screen(sess) -> str:
    """All a terminal still holds, as text: a minute of spinner redraws fills
    the default tail, so the line saying what it was doing is further up."""
    return sess.tail(lines=100000, max_bytes=1 << 20)


def request_restart(reason: str, by: dict, action: str = "restart",
                    external: bool = False) -> dict:
    """A ``safe`` request (the PO's tool, the page): a drain starts, or its
    reason joins the one under way. ``by`` is {roomId, identity, label} for a
    PO, {label} for the page. Returns {status, ...} for the caller."""
    # A matching detached PTY owner survives the web process. There is no
    # safe point to wait for and no agent needs a resume line.
    if action == "restart" and not holding() and _d.pty_host_compatible():
        _log(f"hub-only mode by {by.get('label') or 'someone'}: {reason or '(no reason)'}")
        return _d.trigger_restart(by.get("roomId", ""))
    if action == "update" and not holding() and not external:
        if _d.app_version.packaged():
            _log("packaged update staging; helper will select hub-only or classic mode")
            res = _d.start_app_update()
            return {"status": 202 if res.get("started") else 500, **res}
        if _d.source_update_keeps_host():
            err = _d.pull_checkout()
            if err:
                return {"status": 500, "started": False, "error": err}
            _log("source update: PTY host unchanged; hub-only mode")
            res = _d.trigger_restart(by.get("roomId", ""))
            return {"status": 202 if res.get("started") else 500, **res}
    now = time.time()
    with LOCK:
        st = current()
        if st and st.get("state") == "restarting":
            return {"status": 409, "started": False, "drain": view(st, now),
                    "error": "the restart is already under way: it takes no more requests"}
        st, merged = request(st, reason, by, action, now, deadline_s(_d.load_settings()))
        if external:
            st["external"] = True
        if by.get("roomId"):
            # The PO that asks is at its safe point: it asked and moves on.
            room = _d.chatroom.get_room(by["roomId"], public=False) or {"id": by["roomId"]}
            keys, po_rooms = _names()
            lab, kind = label_of(room, {"identity": by.get("identity", "")}, keys, po_rooms)
            a = ready(st, by["roomId"], by.get("identity", ""), "", now, lab, kind)
            a["requester"] = True
        save(st)
    _log(("joined the drain under way" if merged else "started")
         + f" by {by.get('label') or 'someone'}: {reason or '(no reason)'} ({st['action']})")
    WAKE.set()
    mins = max(1, int(round((float(st["deadline"]) - now) / 60)))
    return {"status": 202, "started": True, "draining": True, "merged": merged,
            "drain": view(st, now),
            "message": ("Restart requested" + (" (joined the one under way)" if merged else "")
                        + f" for: {reason_text(st)}. Every working agent is told to reach a safe "
                        "point; the hub restarts once all are there, in about "
                        f"{mins} min at the latest. Do not poll /api/ptys or resume rooms: every "
                        "agent is brought back by the restart and told where it stopped. Carry "
                        "on; you are told when the hub is back.")}


def agent_ready(room_id: str, identity: str, next_step: str) -> dict | None:
    """``ensemble_ready_for_restart``. None when no drain is under way."""
    now = time.time()
    with LOCK:
        st = current()
        if not st or st.get("state") not in HOLDING:
            return None
        room = _d.chatroom.get_room(room_id, public=False) or {"id": room_id}
        keys, po_rooms = _names()
        part = _d.chatroom.participant(room, identity) or {"identity": identity}
        lab, kind = label_of(room, part, keys, po_rooms)
        prior = (st.get("agents") or {}).get(key(room_id, identity)) or {}
        a = ready(st, room_id, identity, next_step, now, prior.get("label") or lab,
                  prior.get("kind") or kind)
        save(st)
    _log(f"{a['label']} ready; next: {a['next'] or '(nothing said)'}")
    WAKE.set()
    return a


def force_drain(by: str) -> dict:
    with LOCK:
        st = current()
        ok = force(st, by)
        if ok:
            save(st)
    if ok:
        _log(f"forced by {by}")
        WAKE.set()
    return {"ok": ok, "drain": view(current(), time.time()),
            **({} if ok else {"error": "no drain is waiting"})}


def cancel_drain(by: str) -> dict:
    st = current()
    if not st or st.get("state") != "draining":
        return {"ok": False, "drain": view(st, time.time()),
                "error": ("the restart has already started" if st and st.get("state") == "restarting"
                          else "no drain to cancel")}
    end_drain("cancelled", f"cancelled by {by}")
    return {"ok": True, "drain": view(current(), time.time())}


def drain_tick() -> None:
    st = current()
    if not st:
        return
    if st.get("state") == "draining":
        _tick_draining()
    elif st.get("state") == "restarting":
        _tick_restarting()


def _tick_draining() -> None:
    global _OBSERVE_ERROR
    try:
        seen = observe_hub()
    except Exception as e:      # noqa: BLE001 — the deadline and a force still count
        seen = None
        if repr(e) != _OBSERVE_ERROR:
            _log(f"could not look at the agents: {e!r}; the deadline and Restart now still hold")
        _OBSERVE_ERROR = repr(e)
    else:
        _OBSERVE_ERROR = ""
    tell = []
    with LOCK:
        st = current()
        if not st or st.get("state") != "draining":
            return
        if seen is not None:
            tell = observe(st, seen, time.time())
            save(st)
    for k in tell:
        ob = seen[k]
        if _type(ob["roomId"], ob["identity"], DRAIN_LINE):
            with LOCK:
                st = current()
                if st and st.get("state") == "draining":
                    mark_told(st, k, time.time())
                    save(st)
            _log(f"told {ob['label']} ({ob['state']}"
                 + (", a command running in the background" if ob.get("background") else "") + ")")
    with LOCK:
        why = outcome(current(), time.time())
    if why:
        go(why)


def go(why: str) -> None:
    """Every agent is at a safe point, the deadline came, or someone forced
    it: note who is cut off and what its screen shows, then restart."""
    now = time.time()
    with LOCK:
        st = current()
        if not st or st.get("state") != "draining":
            return
        cut = []
        for a in st["agents"].values():
            if a["state"] not in READY_STATES:
                a["interrupted"] = True
                sess = _sess_of(a["roomId"], a["identity"])
                a["screen"] = screen_line(_whole_screen(sess) if sess is not None else "")
                cut.append(a["label"])
        st["state"] = "restarting"
        st["outcome"] = {"ready": "every agent reached a safe point",
                         "deadline": "the deadline came",
                         "forced": f"forced by {st.get('forcedBy')}"}.get(why, why)
        st["restartAt"] = now
        st["hubPid"] = os.getpid()
        st["hubPort"] = _d.HUB_PORT
        save(st)
    _log(f"restarting: {st['outcome']}" + (f"; cut off: {', '.join(cut)}" if cut else ""))
    _launch()


def _requesters(st: dict) -> list[dict]:
    return [r for r in st.get("requestedBy") or [] if r.get("roomId")]


def _launch() -> None:
    """Start the restart (or the update) the drain was for."""
    st = current()
    if not st or st.get("state") != "restarting" or os.getpid() != st.get("hubPid"):
        return
    if st.get("external"):
        _log("external update helper continues after the drain")
        return
    update = st.get("action") == "update"
    if os.environ.get("ENSEMBLE_UPDATE_DRY_RUN" if update else "ENSEMBLE_RESTART_DRY_RUN"):
        _log("dry run: nothing restarted")
        end_drain("done", "dry run: nothing was restarted", tell=False)
        return
    wake = next((r["roomId"] for r in _requesters(st)), "")
    try:
        if update and _d.app_version.packaged():
            res = _d.start_app_update()
            res["status"] = 202 if res.get("started") else (409 if "under way" in res.get("error", "") else 500)
        else:
            if update:
                err = _d.pull_checkout()
                if err:
                    end_drain("failed", f"the update could not be fetched: {err}")
                    return
            res = _d.trigger_restart(wake, drain=True)
    except Exception as e:      # noqa: BLE001 — said, and everything let go
        end_drain("failed", f"{e.__class__.__name__}: {e}")
        return
    with LOCK:
        st = current()
        if st is None or st.get("state") != "restarting":
            return
        if res.get("status") == 409:
            st["retryAt"] = time.time() + RETRY_S
            st["error"] = res.get("error", "")
            save(st)
            _log(f"another restart holds the lease; trying again in {RETRY_S}s")
            return
        if res.get("started"):
            st["lease"] = res.get("leaseId") or _d._current_restart_lease()
            st["startedAt"] = time.time()
            st["retryAt"] = 0.0
            st["error"] = ""
            save(st)
            _log("the restart helper started")
            return
    end_drain("failed", res.get("error") or "the restart did not start")


def restart_cap_s(st: dict) -> float:
    """How long the hub that drained waits, after the drain went to restart,
    for something to stop it; then nothing will (no lease to watch: a macOS
    checkout update; a helper hung or gone with its lease) and the drain is
    called off rather than holding everything for good."""
    return (UPDATE_CAP_S if st.get("action") == "update" and _d.app_version.packaged()
            else _d.RESTART_BUSY_S + 120)


def _tick_restarting() -> None:
    st = current()
    if not st or os.getpid() != st.get("hubPid"):
        return
    now = time.time()
    cap = restart_cap_s(st)
    if now - (st.get("restartAt") or now) > cap:
        end_drain("failed", f"the hub was still running {int(cap)} s after the restart started "
                            f"(see restart.log)")
        return
    if not st.get("startedAt"):
        if st.get("retryAt") and now >= st["retryAt"]:
            _launch()
        return
    # The helper drops the lease when its preflight fails: the hub was not
    # touched, the restart is off.
    if st.get("lease") and now - st["startedAt"] > LEASE_GONE_S \
            and _d._current_restart_lease() != st["lease"]:
        end_drain("failed", "the preflight failed: " + _preflight_why())


def _preflight_why() -> str:
    try:
        lines = (Path(_d.DASHBOARD_DIR) / "logs" / "restart.log").read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for ln in reversed(lines[-200:]):
        if "preflight failed" in ln.lower():
            return ln.split("] ", 1)[-1][:300]
    return "the code on disk did not serve on the spare port (see restart.log)"


def end_drain(state: str, why: str, tell: bool = True, after_s: float = 0.0) -> None:
    """Call a drain off (cancelled | failed) or close it (done) when nothing
    restarted: let go of what it held, tell the agents it told, tell the PO
    that asked. The drain ends at once; ``after_s``: the lines and what was
    held wait that long (a hub just back, its rooms still coming up)."""
    with LOCK:
        st = current()
        if not st or st.get("state") not in HOLDING:
            return
        held = end(st, state, why, time.time())
        save(st)
    _log(f"{state}: {why}; {len(held)} held item(s) let go")
    if after_s:
        def later():
            time.sleep(after_s)
            _after_end(st, held, why, tell)
        threading.Thread(target=later, daemon=True, name="restart-drain-end").start()
    else:
        _after_end(st, held, why, tell)


def _after_end(st: dict, held: list, why: str, tell: bool) -> None:
    if tell:
        for a in st["agents"].values():
            if a.get("requester"):
                continue
            if a["state"] == "ready":
                _type(a["roomId"], a["identity"], CANCEL_READY_LINE.format(
                    why=why, next=(a.get("next") or "the step you were on").rstrip(".")))
            elif a.get("toldAt") and a["state"] != "stopped":
                _type(a["roomId"], a["identity"], CANCEL_TOLD_LINE.format(why=why))
        for r in _requesters(st):
            held.append({"kind": "wake", "roomId": r["roomId"], "identity": r.get("identity", ""),
                         "text": f"{DRAIN_PREFIX}The restart for {reason_text(st)} did not "
                                 f"happen: {why}. Nothing was restarted."})
    release(held)


def release(held: list, after_s: float = 0.0, wait_rooms: bool = False,
            late_rooms: tuple = ()) -> threading.Thread | None:
    """Let go of what a drain held, in order, on a thread: sends go to their
    room the usual way (behind a resume under way: after its line), then
    wakes, reviews and task starts. ``wait_rooms``: those wait until their
    room's resume is through; ``late_rooms`` (the PO the restart helper types
    to) wait ``after_s`` more. An item a thread of this process is already
    letting go (an earlier drain's, carried into this one) is left to it."""
    with LOCK:
        held = [it for it in held if not it.get("hid") or it["hid"] not in _INFLIGHT]
        _INFLIGHT.update(it["hid"] for it in held if it.get("hid"))
    if not held:
        return None

    def run():
        h = _d.hub_launcher()
        rest = []
        for it in held:
            if it.get("kind") == "send" and it.get("roomId") not in late_rooms:
                _release_one(h, it)
            else:
                rest.append(it)
        if wait_rooms:
            end = time.time() + _d.RESUME_NOTE_WAIT_S + 30
            rooms = {it.get("roomId") for it in rest}
            while time.time() < end and any(_resuming(r) for r in rooms):
                time.sleep(1)
        if any(it.get("roomId") in late_rooms for it in rest):
            time.sleep(after_s)
        for it in rest:
            _release_one(h, it)
    t = threading.Thread(target=run, daemon=True, name="restart-drain-release")
    t.start()
    return t


def _resuming(room_id: str) -> bool:
    with _d._RESUMES_LOCK:
        res = _d._RESUMES.get(room_id)
        return res is not None and res.state == "resuming"


def _release_one(h, it: dict) -> None:
    """Let go of one item. It leaves the file only once it was handed over;
    a failed try is retried ``HANDOVER_RETRY_S`` later, ``MAX_TRIES`` times in all
    (the count is on file, so a hub that stops in between goes on counting);
    then it is moved to ``failed``, logged, and the PO told once."""
    hid = it.get("hid")
    try:
        why = _deliver(h, it)
    except BaseException as e:      # noqa: BLE001 — never leave it claimed
        why = repr(e)
    if not hid:                     # a line made when the drain ended: once
        return
    gave_up = False
    with LOCK:
        st = current()
        mine = next((p for p in (st or {}).get("pending") or [] if p.get("hid") == hid), None)
        if not why:
            if mine is not None:
                settle(st, it)
                save(st)
            _INFLIGHT.discard(hid)
            return
        tries = (mine or it).get("tries", 0) + 1
        it.update(tries=tries, lastError=why)
        if mine is not None:
            mine.update(tries=tries, lastError=why)
        if tries >= MAX_TRIES:
            gave_up = True
            if mine is not None:
                settle(st, it)
            if st is not None:
                st.setdefault("failed", []).append({**it, "failedAt": time.time()})
            _INFLIGHT.discard(hid)
        if st is not None:
            save(st)
    if gave_up:
        _give_up(h, st or {}, it, why)
        return
    _log(f"could not let go of a {it.get('kind')} for {it.get('roomId')} "
         f"(try {tries} of {MAX_TRIES}): {why}; again in {HANDOVER_RETRY_S} s")

    def again():
        time.sleep(HANDOVER_RETRY_S)
        _release_one(h, it)
    threading.Thread(target=again, daemon=True, name="restart-drain-retry").start()


def _give_up(h, st: dict, it: dict, why: str) -> None:
    rid, kind = it.get("roomId") or "", it.get("kind")
    _log(f"FAILED: a {kind} for {rid} held for the restart was not let go after "
         f"{MAX_TRIES} tries: {why}")
    if kind == "send" and it.get("key"):
        try:
            _d.sends.mark(rid, [it["key"]], "failed", f"held for the restart, then: {why}")
        except Exception as e:      # noqa: BLE001
            _log(f"send {it['key']} not marked failed: {e!r}")
    room = None
    try:
        room = _d.chatroom.get_room(rid, public=False)
    except Exception:               # noqa: BLE001
        pass
    what = (room or {}).get("title") or rid
    text = (f"{DRAIN_PREFIX}A {kind} held for the restart ({what}) could not be let go after "
            f"{MAX_TRIES} tries: {why}. It is not tried again.")
    told = False
    for r in _requesters(st):
        try:
            told = bool(h._ring(r["roomId"], [r.get("identity", "")], text)) or told
        except Exception as e:      # noqa: BLE001
            _log(f"{r['roomId']} not told of the failed {kind}: {e!r}")
    if not told:
        # Nobody asked (the dashboard) or the PO that asked is stopped: a
        # notice in the PO chat of the task's project.
        try:
            pid = _d.room_project(rid)
        except Exception:           # noqa: BLE001
            pid = ""
        _keys, po_rooms = _names()
        for po_rid, p in po_rooms.items():
            if p.get("id") == pid:
                try:
                    _d.chatroom.post_notice(po_rid, _d.digest.SENDER, text,
                                            {"noticeKind": _d.RESTART_NOTICE_KIND})
                except Exception as e:      # noqa: BLE001
                    _log(f"no notice in {po_rid}: {e!r}")


def _deliver(h, it: dict) -> str:
    """Hand one item over. Returns "" when it was, else why not."""
    rid = it.get("roomId") or ""
    kind = it.get("kind")
    try:
        room = _d.chatroom.get_room(rid, public=False)
        if room is None:
            return "the task is gone"
        if kind == "send":
            if it.get("key"):
                _d.sends.release_after_restart(rid, [it["key"]])
            h._resume_room(room, text=it.get("text", ""), to=it.get("to", ""),
                           key=it.get("key", ""), quiet=bool(it.get("quiet")),
                           project=it.get("project", ""), origin=it.get("origin") or "human")
        elif kind == "start":
            if not _d._room_is_live(room):
                if it.get("fresh"):
                    h._start_room(room)
                else:
                    h._resume_room(room, quiet=bool(it.get("quiet")))
        elif kind == "review":
            msg = next((m for m in room.get("messages") or []
                        if m.get("id") == it.get("messageId")), None)
            if msg is None:
                return "its review request is gone"
            h._start_review(rid, it.get("identity", ""), msg)
        elif kind == "wake":
            # An agent that is not running is not woken: as without a drain.
            h._ring(rid, [it.get("identity", "")], it.get("text", ""))
        _log(f"let go: {kind} for {rid}" + (f"/{it['identity']}" if it.get("identity") else ""))
        return ""
    except Exception as e:      # noqa: BLE001 — one item never stops the rest
        return repr(e)


def not_restarted(lease: str) -> None:
    """The helper is through but this hub process is still the one that
    drained (it could not be stopped): nothing restarted, so call the drain
    off and let go of what it held instead of holding it for good."""
    st = current()
    if st and st.get("state") == "restarting" and lease and st.get("lease") == lease \
            and st.get("hubPid") == os.getpid():
        end_drain("failed", "the hub could not be stopped, so it did not restart")


def restore_notes(lease: str) -> tuple[dict | None, dict]:
    """For the hub that came back: the drain this restart closes, and
    {roomId: {identity: its line}} — where it said it would carry on, or
    what its screen showed when it was cut off. (None, {}) for a restart that
    did not drain."""
    st = current()
    if not st or st.get("state") != "restarting" or not lease or st.get("lease") != lease:
        return None, {}
    notes: dict[str, dict] = {}
    for a in st["agents"].values():
        if a.get("requester"):
            continue
        if a["state"] == "ready":
            notes.setdefault(a["roomId"], {})[a["identity"]] = resume_line(st, a)
        elif a["state"] == "waiting":
            notes.setdefault(a["roomId"], {})[a["identity"]] = WAITING_LINE.format(
                reason=reason_text(st))
        elif a.get("interrupted"):
            line = _d.RESTART_NOTE
            if a.get("screen"):
                line += f" The last thing your screen showed you doing: {a['screen']}"
            notes.setdefault(a["roomId"], {})[a["identity"]] = line
    return st, notes


def po_summary(st: dict) -> str:
    paused = sorted({a["label"] for a in st["agents"].values()
                     if a["state"] == "ready" and not a.get("requester")})
    cut = sorted({a["label"] for a in st["agents"].values() if a.get("interrupted")})
    out = []
    if paused:
        out.append("paused at a safe point and resumed: " + ", ".join(paused))
    if cut:
        out.append("cut off and resumed: " + ", ".join(cut))
    return "; ".join(out) or "no agent was working"


def close_restore(st: dict) -> list | None:
    """The hub that came back closes the drain before it brings the rooms
    back (so their starts and reviews are not held again). Returns what was
    held, for ``finish_restore``; None when it was closed already."""
    with LOCK:
        cur = current()
        if not cur or cur.get("id") != st.get("id") or cur.get("state") != "restarting":
            return None
        held = end(cur, "done", f"restarted; {po_summary(cur)}", time.time())
        save(cur)
    _log(f"done: {po_summary(st)}; {len(held)} held item(s) to let go")
    return held


def finish_restore(st: dict, held: list, wake_room: str) -> None:
    """After the hub that came back restored the rooms: tell every other PO
    once (a notice in its chat, not a typed wake: a wake costs a PO its whole
    history) and let go of what was held, after each room's resume line."""
    summary = po_summary(st)
    _keys, po_rooms = _names()
    text = (f"The hub restarted at {time.strftime('%H:%M')} for {reason_text(st)}. "
            f"{summary[:1].upper() + summary[1:]}.")
    for rid in po_rooms:
        if rid != wake_room:
            try:
                _d.chatroom.post_notice(rid, _d.digest.SENDER, text,
                                        {"noticeKind": _d.RESTART_NOTICE_KIND})
            except Exception as e:      # noqa: BLE001
                _log(f"no notice in {rid}: {e!r}")
    release(held, after_s=25, wait_rooms=True, late_rooms=(wake_room,) if wake_room else ())


def on_startup() -> None:
    """A hub starting finds a drain on file. One whose hub stopped mid-drain
    is called off; one restarting (this is the hub that came back) waits for
    the helper's restore and closes itself if that never comes. A preflight
    hub (another port) leaves it alone."""
    st = current()
    if not st or (st.get("hubPort") and st.get("hubPort") != _d.HUB_PORT):
        return
    if st.get("state") not in HOLDING:
        # A hub that stopped while it let go of what a drain held: the rest
        # goes in once the rooms are back.
        if st.get("pending"):
            _log(f"{len(st['pending'])} held item(s) not let go before the hub stopped: "
                 f"let go in {CANCEL_RELEASE_S} s")

            def later():
                time.sleep(CANCEL_RELEASE_S)
                release(list((current() or {}).get("pending") or []))
            threading.Thread(target=later, daemon=True, name="restart-drain-end").start()
        return
    if st.get("state") == "draining":
        # Ended now, before this hub's monitor ticks: it sees none of the old
        # terminals and would read every agent as ready and restart again.
        end_drain("cancelled", "the hub stopped during the drain", after_s=CANCEL_RELEASE_S)
        return
    if st.get("hubPid") == os.getpid():
        return

    def wait():
        # No helper of this restart restores the rooms (a checkout's updater
        # on macOS): nothing to wait for.
        end_at = time.time() + (RESTORE_WAIT_S if st.get("lease") else 0)
        while time.time() < end_at:
            cur = current()
            if not cur or cur.get("id") != st.get("id") or cur.get("state") != "restarting":
                return
            time.sleep(5)
        cur = current() or st
        held = close_restore(cur)
        if held is not None:
            finish_restore(cur, held, "")
    threading.Thread(target=wait, daemon=True).start()
