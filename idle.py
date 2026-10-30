"""A running task that does nothing: the PO hears of it, the board says so.

Measured over a week (#292, ``tools/measure_idle.py``): 87 tasks in projects
with a PO, 508 idle stretches. The stalled nudge (stall.py) does not look at a
task that reported, or sits in In review or Done, so the CEO found those.

**Idle** is an agent that runs and has not worked for ``idleAlertMin`` minutes
(Settings; 20, 0 turns it off). What decides is whether an agent runs, not the
column: a stopped task never alerts, a live agent in Done does.

Not idle while the owner works, hands over or retries a provider's refusal, a
reviewer runs, or its own command runs (up to ``attention.OWN_WORK_CAP_S``).
Held, no column move and no alert: paused, held by the pacing brake, at a model
limit. Quiet, no alert: a marked wait, an open ask the PO has read, an open
card, a finished report younger than ``MERGE_AFTER_S``.

**The line.** All tasks of a project due at once share one line typed into the
idle PO, ``[idle] #412 idle since 09:42 ...``, at most one per ``FLOOR_S``.
Repeats back off: ``BACKOFF_MIN``, then every 6 h. A merged task whose agent is
merely left running gets one alert and no repeat. A failed read means no alert.

**A marked wait** (``waitMark`` on the room, ``ensemble_mark_wait``) survives a
restart and a rotation. It ends when the card is answered, the time comes, the
other task is done, or the owner works again; the PO is woken once. A background
wait with no wake time ends after ``BACKGROUND_CAP_S`` so it never goes silent.

**The column** (P246). In progress holds only moving work: a card not moving
goes to Waiting, and comes back when the work resumes. Only from In progress,
never over a move made in that stretch, and it leaves ``workflowAt`` alone (as
``dashboard.settle_finished``), so a merge still takes the card to Done. The
tick covers every project, so no pass at hub start is needed.

State: ``DASHBOARD_DIR/idle.json``, so a hub restart repeats nothing.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
import durable

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


TICK_S = 60                     # how often the tasks are looked at
DEFAULT_MIN = 20                # idle this long before the first alert
BACKOFF_MIN = (30, 120, 360)    # after the 1st, 2nd and every later alert
FLOOR_S = 3600                  # one line per project in this time
MERGE_AFTER_S = 4 * 3600        # a finished report nobody acted on alerts after this
BACKGROUND_CAP_S = 6 * 3600     # a background wait with no wake time ends after this
STOP_LOOK_S = 120               # not live on two looks this far apart is stopped
_EPISODE_S = 60                 # the idle clock moving later by this is a new stretch
_LINE_MAX = 900
PREFIX = "[idle] "              # how the typed line starts (HUB_INPUT_KINDS)
WAIT_KINDS = ("card", "time", "task", "background")
_REPORT_WORDS = 80

_LOCK = threading.Lock()
_LAST = 0.0


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] idle: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "idle.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    return {k: d[k] if isinstance(d.get(k), dict) else {}
            for k in ("tasks", "projects", "resolved", "stopped")}


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        durable.write_tmp(tmp, json.dumps(state, indent=2, ensure_ascii=False))
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save: {e}")


def threshold_s() -> int:
    """The first alert's wait in seconds; 0 is off."""
    try:
        return max(0, int(_d.load_settings().get("idleAlertMin", DEFAULT_MIN))) * 60
    except (TypeError, ValueError, OSError):
        return DEFAULT_MIN * 60


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(float(ts or 0)))


# ---------------------------------------------------------------------------
# What the task is doing
# ---------------------------------------------------------------------------

def _po_read_since(room: dict, ts: float) -> bool:
    """Whether the task's PO took a turn after ``ts``: it has seen the ask."""
    rot, cr = _d.rotation, _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False, copy_room=False, messages=False) if po else None
    part = cr.participant(po_room, cr.po_identity(po_room)) if po_room else None
    return bool(part) and _d.stall._wrote_since(rot._transcript_of(part)[0], ts)


def _finished_report(room: dict) -> dict:
    """The task's last real report when it is a completed one, else {}."""
    rep = _d.chatroom.last_real_report(room)
    return rep if rep.get("kind") == "completed" else {}


def assess(room: dict, now: float, limit: int) -> dict:
    """What a live task is doing: ``{"state": moving | hold | quiet | idle |
    unknown, "why", "since", "tpath"}``. ``room`` carries its messages. A read
    that fails raises, and the caller says nothing."""
    rot, cr, st, att = _d.rotation, _d.chatroom, _d.stall, _d.attention
    rid = room["id"]
    owners = cr.owners(room)
    owner = owners[0] if owners else ""
    part = cr.participant(room, owner) if owner else None
    if not part:
        return {"state": "unknown", "why": "no owner"}
    if room.get("status") == "paused" or room.get("paceHold") or st.limit_hit(part):
        return {"state": "hold", "why": "held"}
    if rot.is_rotating(rid, owner) or rot.awaiting_handover(rid, owner):
        return {"state": "moving", "why": "handover"}
    if _d.provider_retry.status(part, rid, now):
        return {"state": "moving", "why": "provider"}
    if any(st._busy(p) for p in cr.agent_participants(room) if p.get("identity") != owner):
        return {"state": "moving", "why": "teammate"}
    sess = rot._pty(part)
    if sess is None:
        return {"state": "unknown", "why": "owner not running"}
    tpath, reader = rot._transcript_of(part)
    if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
        return {"state": "moving", "why": "working"}
    idle = float(sess.info().get("idleSeconds") or 0)
    if st.own_work(sess) and idle < att.OWN_WORK_CAP_S:
        return {"state": "moving", "why": "command"}
    # The stalled nudge's own echo resets the screen's clock: its record keeps
    # when the owner really stopped.
    since = now - idle
    stalled = st.record(rid)
    if stalled and float(stalled.get("idleSince") or now) < since:
        since = float(stalled["idleSince"])
    out = {"since": since, "tpath": tpath}
    if room.get("waitMark"):
        return {**out, "state": "quiet", "why": "marked"}
    ask = att.open_ask(room)
    if ask and ask.get("kind") != "completed":
        if not _d.room_po_id(room) or _po_read_since(room, float(ask.get("ts") or 0)):
            return {**out, "state": "quiet", "why": "ask"}
        out["ask"] = ask.get("kind")
    elif _d.asks.open_cards(room):
        return {**out, "state": "quiet", "why": "card"}
    else:
        rep = _finished_report(room)
        if rep:
            if now - float(rep.get("ts") or 0) < MERGE_AFTER_S:
                return {**out, "state": "quiet", "why": "finished"}
            out["finished"] = True
    if not limit or now - since < limit:
        return {**out, "state": "moving", "why": "short"}
    return {**out, "state": "idle", "why": "idle"}


# ---------------------------------------------------------------------------
# Marked waits
# ---------------------------------------------------------------------------

def wait_words(mark: dict) -> str:
    """A wait as the card and the PO read it, after "Waiting ": "on CEO card"."""
    kind = mark.get("kind")
    if kind == "card":
        return "on CEO card"
    if kind == "time":
        return f"until {_clock(mark.get('until'))}"
    if kind == "task":
        return f"on {mark.get('what') or 'another task'}"
    return "on background work"


def card_ids(room: dict) -> list[str]:
    """The cards a wait on a card of ``room`` waits for: the open ones of its
    own chat and the PO's that name it, else every open one of its PO's."""
    cr = _d.chatroom
    own = [c["mid"] for c in _d.asks.open_cards(room)]
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    theirs = _d.asks.open_cards(po_room) if po_room else []
    names = {room.get("id"), _d.task_label(room), str(room.get("no") or "")} - {"", None}
    named = [c["mid"] for c in theirs if str(c.get("task") or "") in names]
    return list(dict.fromkeys(own + (named or ([] if own else [c["mid"] for c in theirs]))))


def _still_open(room: dict, mark: dict) -> bool:
    cr = _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    full = cr.get_room(room["id"], public=False) or room
    mids = {c["mid"] for r in (full, po_room) if r for c in _d.asks.open_cards(r)}
    return bool(mids & set(mark.get("mids") or []))


def _mark_over(room: dict, mark: dict, now: float) -> str:
    """What ended a wait, in the PO's words; "" while it holds."""
    kind = mark.get("kind")
    until = float(mark.get("until") or 0)
    if kind == "time":
        return f"the time came ({_clock(until)})" if until <= now else ""
    if kind == "task":
        other = _d.chatroom.get_room(mark.get("taskId", ""), public=False,
                                     copy_room=False, messages=False)
        if other is None or _d.workflow_of(other) == "done":
            return f"{mark.get('what') or 'the other task'} is done"
        return ""
    if kind == "card":
        return "" if _still_open(room, mark) else "the card was answered"
    if kind == "background":
        if until:
            return f"the time came ({_clock(until)})" if until <= now else ""
        if now - float(mark.get("at") or 0) >= BACKGROUND_CAP_S:
            return f"nothing since {_clock(mark.get('at'))}"
    return ""


def _clear_mark(rid: str, mark: dict) -> bool:
    return _d.chatroom.patch_room_if(rid, lambda r: r.get("waitMark") == mark,
                                     waitMark=None) is not None


def _settle_mark(room: dict, seen: dict, state: dict, now: float, po: str) -> None:
    """A marked wait, on each look at a live task. The owner going back to work
    ends a background wait with no line: the one turn that marked it ran on past
    the mark, so that counts only once the owner has been seen idle."""
    rid, mark = room["id"], room.get("waitMark")
    if not mark:
        return
    if mark.get("kind") == "background":
        if seen.get("why") == "working":
            if mark.get("idleSeen"):
                _clear_mark(rid, mark)
                room["waitMark"] = None
                return
        elif seen.get("state") != "unknown" and not mark.get("idleSeen"):
            kept = {**mark, "idleSeen": True}
            if _d.chatroom.patch_room_if(rid, lambda r: r.get("waitMark") == mark,
                                         waitMark=kept) is not None:
                room["waitMark"] = mark = kept
    over = _mark_over(room, mark, now)
    if not over or not _clear_mark(rid, mark):
        return
    room["waitMark"] = None
    rec = state["tasks"].setdefault(rid, {"since": float(mark.get("at") or now)})
    # The wake below is this stretch's first alert.
    rec.update(alerts=1, lastAt=now, nextAt=now + BACKOFF_MIN[0] * 60)
    if po:
        state["resolved"][rid] = {"po": po, "at": now,
                                  "word": f"wait over, {wait_words(mark)}: {over}"}


# ---------------------------------------------------------------------------
# The column
# ---------------------------------------------------------------------------

def _move(room: dict, test, to: str, why: str, now: float) -> bool:
    cur = _d.chatroom.patch_room_if(room["id"], test, workflow=to, workflowAuto={
        "at": now, "from": _d.workflow_of(room), "why": why})
    if cur is None:
        return False
    _d._patch_task_json(cur.get("taskDir", ""), workflow=to)
    return True


def _to_waiting(room: dict, start: float, why: str, now: float) -> bool:
    """In progress to Waiting, unless someone moved the card after ``start``."""
    def test(r):
        return (_d.workflow_of(r) == "inprogress" and not _d._finished_unmoved(r)
                and float(r.get("workflowAt") or 0) <= start)
    return test(room) and _move(room, test, "waiting", why, now)


def _to_progress(room: dict, now: float) -> bool:
    """Back from Waiting, if the hub put it there and nobody moved it since."""
    def test(r):
        auto = r.get("workflowAuto") or {}
        return (_d.workflow_of(r) == "waiting" and bool(auto)
                and float(auto.get("at") or 0) >= float(r.get("workflowAt") or 0))
    return test(room) and _move(room, test, "inprogress", "resumed", now)


def _stopped_at(room: dict) -> float:
    ts = [float(room.get("stoppedAt") or 0)]
    for p in room.get("participants") or []:
        ex = p.get("lastExit") or {}
        ts.append(float(ex.get("endedAt") or ex.get("at") or 0))
    return max(ts) or float(room.get("launchedAt") or room.get("createdAt") or 0)


def _look_stopped(room: dict, state: dict, now: float, live_ptys: set) -> str:
    rid = room["id"]
    relevant = (_d.workflow_of(room) == "inprogress" or room.get("waitMark")
                or rid in state["tasks"] or rid in state["resolved"])
    if (not relevant or room.get("paceHold") or rid in live_ptys
            or _d._room_has_linked_agent(room)):
        state["stopped"].pop(rid, None)
        return ""
    seen = state["stopped"].setdefault(rid, now)
    if now - float(seen) < STOP_LOOK_S:
        return ""
    state["tasks"].pop(rid, None)
    state["resolved"].pop(rid, None)
    mark = room.get("waitMark")
    if mark:
        _clear_mark(rid, mark)
    return "to Waiting (stopped)" if _to_waiting(room, _stopped_at(room), "stopped", now) else ""


def _look(light: dict, state: dict, now: float, limit: int, live_ptys: set) -> str:
    """One task: "" when nothing was done, else what was."""
    rot, cr = _d.rotation, _d.chatroom
    rid = light["id"]
    if not _d._room_is_live(light):
        return _look_stopped(light, state, now, live_ptys)
    state["stopped"].pop(rid, None)
    with rot.GATE:
        room = cr.get_room(rid, public=False)
        if not room or not _d._room_is_live(room):
            return ""
        seen = assess(room, now, limit)
    po = _d.room_po_id(room)
    _settle_mark(room, seen, state, now, po)
    tasks = state["tasks"]
    rec = tasks.get(rid)
    if rec:
        rec["current"] = False
    verdict, since = seen["state"], seen.get("since")
    if verdict == "unknown":
        return ""
    if verdict == "moving" and seen["why"] != "short":
        tasks.pop(rid, None)
        return "back to In progress" if _to_progress(room, now) else ""
    if verdict == "hold":
        return ""
    if rec and since is not None and since > float(rec["since"]) + _EPISODE_S:
        # A later clock with no turn taken is a line's echo or a restart.
        if _d.stall._wrote_since(seen.get("tpath"), float(rec["since"]) + _EPISODE_S):
            tasks.pop(rid, None)
            rec = None
        else:
            since = float(rec["since"])
    if verdict == "moving":             # idle less than the limit
        if rec and limit and now - float(rec["since"]) >= limit:
            verdict = "idle"
        else:
            return "back to In progress" if not rec and _to_progress(room, now) else ""
    if room.get("waitMark"):
        return ("to Waiting (wait marked)"
                if _to_waiting(room, float(room["waitMark"].get("at") or now), "wait", now) else "")
    if not limit or since is None or now - since < limit:
        return ""
    moved = "to Waiting (idle)" if _to_waiting(room, since, "idle", now) else ""
    if verdict != "idle":
        return moved
    if not rec:
        rec = tasks[rid] = {"since": since, "alerts": 0, "lastAt": 0, "nextAt": 0}
        told = float((_d.stall.record(rid) or {}).get("toldAt") or 0)
        if told:                        # the stalled line said it already
            rec.update(alerts=1, lastAt=told, nextAt=told + BACKOFF_MIN[0] * 60)
    rec["current"] = True
    rec["why"] = ("merged" if room.get("mergedAt") else "finished" if seen.get("finished")
                  else "ask" if seen.get("ask") else "done" if _d.workflow_of(room) == "done" else "")
    return moved


# ---------------------------------------------------------------------------
# The line
# ---------------------------------------------------------------------------

def _due(rec: dict, now: float) -> bool:
    if not rec.get("current") or now < float(rec.get("nextAt") or 0):
        return False
    return not (rec.get("why") == "merged" and rec.get("alerts"))


def _last_report_words(room: dict) -> str:
    rep = room.get("lastReport") or {}
    line = _d.attention._first_line(rep.get("text"))
    return line[:_REPORT_WORDS] + ("…" if len(line) > _REPORT_WORDS else "")


def _item(room: dict, rec: dict) -> str:
    """One task in the PO's line."""
    name = _d.task_label(room) or room.get("title") or "a task"
    why = rec.get("why")
    head = f"{name} idle since {_clock(rec.get('since'))}"
    if why == "merged":
        return f"{head}, merged, agent left running: stop it"
    said = _last_report_words(room)
    bits = [head, f'last report "{said}"' if said else "no report yet"]
    bits.append({"finished": "finished report not acted on",
                 "ask": "its question is not read yet",
                 "done": "in Done, agent still running"}.get(why, "waiting on nothing"))
    return ", ".join(bits)


def _line(items: list[str]) -> tuple[str, int]:
    """The line and how many items fit; those left wait for the next one."""
    tail = " Send work, stop the task, or mark the wait."
    text, n = PREFIX, 0
    for it in items:
        add = ("; " if n else "") + it
        if n and len(text) + len(add) + len(tail) + 1 > _LINE_MAX:
            break
        text, n = text + add, n + 1
    return f"{text}.{tail}", n


def _wake(state: dict, now: float, rooms: dict) -> dict:
    """One line per project due one. Marks a task told only once it is typed."""
    tasks, resolved = state["tasks"], state["resolved"]
    groups: dict[str, list] = {}
    for rid, it in resolved.items():
        room = rooms.get(rid)
        if room:
            groups.setdefault(it.get("po") or "", []).append(("resolved", rid, room))
    for rid, rec in tasks.items():
        room = rooms.get(rid)
        if room and _due(rec, now):
            po = _d.room_po_id(room)
            if po:
                groups.setdefault(po, []).append(("task", rid, room))
    out = {}
    for po, entries in groups.items():
        if not po or now - float((state["projects"].get(po) or {}).get("lineAt") or 0) < FLOOR_S:
            continue
        entries.sort(key=lambda e: (e[0] != "resolved", float(tasks.get(e[1], {}).get("since") or 0)))
        words = [(_d.task_label(e[2]) or e[2].get("title") or "a task") + " " + resolved[e[1]]["word"]
                 if e[0] == "resolved" else _item(e[2], tasks[e[1]]) for e in entries]
        line, n = _line(words)
        try:
            typed = _d.stall._type_po(entries[0][2], line)
        except Exception as e:          # stays pending: tried again next tick
            _log(f"{po}: {str(e)[:200]}")
            typed = False
        if not typed:
            continue
        state["projects"][po] = {"lineAt": now}
        for kind, rid, _room in entries[:n]:
            if kind == "resolved":
                resolved.pop(rid, None)
                continue
            rec = tasks[rid]
            rec["alerts"] = int(rec.get("alerts") or 0) + 1
            rec["lastAt"] = now
            rec["nextAt"] = now + BACKOFF_MIN[min(rec["alerts"], len(BACKOFF_MIN)) - 1] * 60
            out[rid] = f"PO told ({rec['alerts']})"
    return out


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------

def tick(now: float | None = None) -> dict[str, str]:
    """Look at every task once. Returns {roomId: what was done}."""
    now = time.time() if now is None else now
    if not _LOCK.acquire(blocking=False):
        return {}                       # the last one is still at it
    try:
        state = _load()
        before = json.dumps(state, sort_keys=True)
        try:
            ids = [p.stem for p in _d.chatroom.ROOMS_DIR.glob("room-*.json")]
        except OSError as e:
            _log(f"rooms list failed: {str(e)[:200]}")
            return {}
        limit = threshold_s()
        try:
            projects = _d.load_projects()
            archived = _d.load_archived()
            live_ptys = {(x.get("meta") or {}).get("room") for x in _d.ptyrun.list_sessions()
                         if x.get("alive")}
        except Exception as e:
            _log(f"cannot read the board: {str(e)[:200]}")
            return {}
        po_ids = {p.get("poRoomId") for p in projects if p.get("poRoomId")}
        out, rooms = {}, {}
        for rid in ids:
            try:
                room = _d.chatroom.get_room(rid, public=False, copy_room=False, messages=False)
                if (not room or not room.get("launched", True) or room.get("pruned")
                        or room.get("archived") or rid in archived or rid in po_ids):
                    continue
                rooms[rid] = room
                did = _look(room, state, now, limit, live_ptys)
            except Exception as e:      # one task must not stop the others
                _log(f"{rid}: {str(e)[:200]}")
                continue
            if did:
                out[rid] = did
        for key in ("tasks", "resolved", "stopped"):
            for rid in [r for r in state[key] if r not in ids]:
                state[key].pop(rid, None)
        if limit:
            for rid, did in _wake(state, now, rooms).items():
                out[rid] = f"{out[rid]}; {did}" if rid in out else did
        for rid, did in out.items():
            _log(f"{rid}: {did}")
        if json.dumps(state, sort_keys=True) != before:
            _save(state)
        return out
    finally:
        _LOCK.release()


def records() -> dict[str, dict]:
    """The tasks idle right now, ``{since, alerts, ...}``. One read of the file
    for a whole list."""
    return {rid: rec for rid, rec in _load()["tasks"].items() if rec.get("current")}


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
