"""Burn rate, projected end of week and the brake on new work (#291).

The hub keeps a short history of each kind's 7-day reading (it survives a
restart) and works out points per hour and the percent projected at the
kind's reset. Seat choice reads the projection; the brake holds new work of
medium or lower priority while every usable kind is projected past its week.

Unknown is never a guess and never a brake: any failure here leaves a start
free. The figures below come from replaying 14 days of stored readings; the
document `#291 Pacing that paces.md` says why each is what it is.
"""
import json
import threading
import time

import durable

_d = None
_LOCK = threading.RLock()
_CACHE = {"path": None, "data": None}

WINDOW_H = 24               # a whole day and night: a quiet night cannot swing it
RECENT_SHARE = 0.75         # the rest is the week's average so far
KEEP_H = 36
MIN_SAMPLES = 3
MIN_SPAN_H = 3
SAMPLE_EVERY_S = 900
FALL_POINTS = 1             # a reading rounded one point lower is not a fall
RESET_MOVE_S = 2 * 3600     # a reset at 0% drifts by minutes; more is a new week
WEEK_H = 7 * 24
PROJECTED_LIMIT = 110       # 100 plus what a projection is off by a day ahead
RELEASE_POINTS = 5          # the brake lets go this far under the limit
DWELL_S = 6 * 3600          # and not sooner than this after it engaged
UNKNOWN_RELEASE_S = 1800    # held work waits out a short gap in the readings
TICK_EVERY_S = 60
USE_EVERY_S = 3600
HELD_FROM_PRIORITY = 3      # medium; high and highest always start
MODES = ("off", "tell", "hold")
MODE_DEFAULT = "hold"
_TICK = {"at": 0.0}


def bind(dashboard_module):
    global _d
    _d = dashboard_module


def _path():
    return _d.DASHBOARD_DIR / "pace-history.json"


def _blank():
    return {"series": {}, "brake": {"on": False, "episode": 0, "since": 0, "unknownSince": 0,
                                    "muted": 0, "why": "", "until": ""},
            "told": {}, "use": []}


def _state():
    """The file's contents, kept in memory: :func:`burn` runs on every pace
    view and seat choice. Tests move DASHBOARD_DIR, so it is kept by path."""
    path = str(_path())
    if _CACHE["path"] != path:
        data = _blank()
        try:
            saved = json.loads(_path().read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({k: saved[k] for k in data if isinstance(saved.get(k), type(data[k]))})
                data["brake"] = {**_blank()["brake"], **data["brake"]}
        except (OSError, ValueError):
            pass
        _CACHE.update(path=path, data=data)
    return _CACHE["data"]


def _save():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    durable.write_tmp(tmp, json.dumps(_state()))
    tmp.replace(path)


def series_key(kind, pool=""):
    return f"codex:{pool or _d.usage.CODEX_MAIN_POOL}" if kind == "codex" else kind


def note(key, at, percent, end):
    """One reading into ``key``'s history. True when the history changed. A
    fall, or a reset that moved, is a new week: the history restarts there."""
    with _LOCK:
        series = _state()["series"]
        rec = series.get(key)
        if rec and rec.get("samples"):
            last_at, last_pct = rec["samples"][-1]
            if at <= last_at:
                return False
            if percent < last_pct - FALL_POINTS or abs(end - rec["reset"]) > RESET_MOVE_S:
                rec = None
        else:
            rec = None
        if rec is None:
            series[key] = {"reset": end, "samples": [[at, percent]]}
            return True
        rec["reset"] = end
        if percent == last_pct and at - last_at < SAMPLE_EVERY_S:
            return False
        samples = rec["samples"]
        samples.append([at, percent])
        cut = at - KEEP_H * 3600
        while len(samples) > 1 and samples[1][0] <= cut:
            samples.pop(0)      # one older than the window stays: it is the "then"
        return True


def burn(key, percent, end, now, at_least=False):
    """Points per hour and the percent projected at ``end``, or state
    "unknown" with why. The reading is a step: its last value runs flat to
    now, so idle time counts as no use."""
    def unknown(why):
        return {"state": "unknown", "why": why}
    try:
        if at_least:
            return unknown("the reading is only a floor")
        with _LOCK:
            samples = list((_state()["series"].get(key) or {}).get("samples") or [])
        if len(samples) < MIN_SAMPLES:
            return unknown("too few samples")
        span = (now - samples[0][0]) / 3600
        if span < MIN_SPAN_H:
            return unknown("too little history")
        then = samples[0][1]
        for at, pct in samples:
            if at > now - WINDOW_H * 3600:
                break
            then = pct
        recent = max(0.0, percent - then) / min(WINDOW_H, span)
        hours_left = max(0.0, (end - now) / 3600)
        average = percent / max(1.0, WEEK_H - hours_left)
        rate = RECENT_SHARE * recent + (1 - RECENT_SHARE) * average
        return {"state": "known", "ratePerHour": round(rate, 2),
                "projected": round(percent + rate * hours_left, 1), "samples": len(samples)}
    except (TypeError, ValueError, KeyError, IndexError, OSError):
        return unknown("the history could not be read")


def past_limit(reading):
    """``reading`` (:func:`_kind_pace`) has a projection past its week."""
    seen = (reading or {}).get("burn") or {}
    return seen.get("state") == "known" and seen["projected"] >= PROJECTED_LIMIT


def known(reading):
    return ((reading or {}).get("burn") or {}).get("state") == "known"


def sample(snapshot):
    """Note every reading the snapshot trusts, Codex by pool: a seat naming a
    model is judged by its own pool."""
    checked = float(snapshot.get("checkedAt") or 0)
    changed = False
    for source in snapshot.get("sources") or []:
        kind = source.get("source")
        if kind not in ("claude", "codex") or source.get("state") != "ok":
            continue
        seen = set()
        for w in source.get("windows") or []:
            pool = (w.get("pool") or "") if kind == "codex" else ""
            if (w.get("kind") != "seven_day" or pool in seen or w.get("percent") is None
                    or w.get("rolledOver") or w.get("resetUnknown") or not w.get("trusted")
                    or (kind == "claude" and w.get("model"))):
                continue
            end, how = _d.usage._reset_epoch(w.get("resetsAt"))
            if how != "ok" or end is None:
                continue
            seen.add(pool)
            at = checked - float(w.get("ageSeconds") or 0)
            changed = note(series_key(kind, pool), at, float(w["percent"]), end) or changed
    return changed


def mode():
    settings = _d.load_settings()
    if settings.get("paceWeek", True) is False:
        return "off"
    value = settings.get("paceBrake", MODE_DEFAULT)
    return value if value in MODES else MODE_DEFAULT


def _judge(snapshot, now):
    """Every usable kind's reading. ``lowest``: the smallest projection among
    them, None when any is unknown or none is usable. A week about to reset is
    never out: what is left of it is lost at the reset."""
    cfg = _d.pace_settings(snapshot)
    def installed(kind):
        agent = _d.agents.get_agent(kind)
        return bool(agent and agent.installed())
    blocked = _d._blocked_kinds()
    kinds = {}
    for kind in ("claude", "codex"):
        figures = _d._kind_usage(snapshot, kind, "")
        if not _d._spent_why(kind, figures, installed, blocked,
                             _d._reading_at(snapshot, kind, figures)):
            kinds[kind] = _d._kind_pace(snapshot, kind, "", cfg["margin"], now)
    if not kinds or not all(known(r) for r in kinds.values()):
        return {"kinds": kinds, "lowest": None}
    lowest = min(0.0 if r.get("nearReset") else r["burn"]["projected"] for r in kinds.values())
    return {"kinds": kinds, "lowest": lowest}


def _why(kinds, now):
    parts = []
    for kind, r in kinds.items():
        parts.append(f"{_d._agent_kind_name(kind)} at {float(r['percent']):g}%, "
                     f"{r['burn']['ratePerHour']:g} points an hour, heading for "
                     f"{r['burn']['projected']:g}% by {_d._reset_clock(r.get('resetsAt'), now)}")
    return "; ".join(parts)


def _until(kinds, now):
    soonest = min(kinds.values(), key=lambda r: r.get("hoursToReset") or 0)
    return (f"the projection falls under {PROJECTED_LIMIT - RELEASE_POINTS}%, "
            f"or {_d._reset_clock(soonest.get('resetsAt'), now)} at the latest")


def _step(how, judged, now):
    """Move the brake one tick. Unknown keeps what is held for a short while,
    so one failed read does not start everything at once."""
    brake = _state()["brake"]
    before = dict(brake)
    lowest = judged["lowest"]
    if how == "off":
        brake.update(on=False, unknownSince=0, muted=brake["episode"])
    elif lowest is None:
        if brake["on"]:
            brake["unknownSince"] = brake["unknownSince"] or now
            if now - brake["unknownSince"] >= UNKNOWN_RELEASE_S:
                brake.update(on=False, unknownSince=0)
    else:
        brake["unknownSince"] = 0
        if lowest >= PROJECTED_LIMIT and not brake["on"]:
            brake.update(on=True, since=now, episode=brake["episode"] + 1)
        elif (brake["on"] and lowest < PROJECTED_LIMIT - RELEASE_POINTS
                and now - brake["since"] >= DWELL_S):
            brake["on"] = False
        if brake["on"]:
            brake.update(why=_why(judged["kinds"], now), until=_until(judged["kinds"], now))
    return brake != before


def hold(room, by, now=None):
    """Whether a start of ``room`` waits: the hold mark, written on the room,
    or None. A start waits only while the brake is on and today's readings
    still say so; a read that fails starts it."""
    now = time.time() if now is None else now
    try:
        if (mode() != "hold" or _d.priority_of(room) < HELD_FROM_PRIORITY
                or any(p.get("poRoomId") == room["id"] for p in _d.load_projects())):
            return None
        with _LOCK:
            brake = dict(_state()["brake"])
        if not brake["on"]:
            return None
        judged = _judge(_d.usage.snapshot(), now)
        if judged["lowest"] is None or judged["lowest"] < PROJECTED_LIMIT - RELEASE_POINTS:
            return None
        mark = {"at": (room.get("paceHold") or {}).get("at", now), "by": by,
                "why": brake["why"], "until": brake["until"]}
        if _d.chatroom.patch_room(room["id"], paceHold=mark) is None:
            return None
        room["paceHold"] = mark
        return mark
    except Exception as e:                                  # noqa: BLE001
        print(f"[pacing] hold check failed: {str(e)[:200]}", flush=True)
        return None


def note_text(mark):
    return (f"Held by the pacing brake: {mark['why']}. It starts by itself when "
            f"{mark['until']}; a person's Start from the page starts it now.")


def clear_hold(room):
    """A held task that started, however it started."""
    if room.get("paceHold"):
        room["paceHold"] = None
        _d.chatroom.patch_room(room["id"], paceHold=None)


def _project_of(room):
    pid = _d.ensemble_tools._project_of_room(room)
    return next((p for p in _d.load_projects() if p["id"] == pid), None)


def _release(how):
    """Start the waiting task that comes first, one a tick, so the brake is
    judged again before each. Raised to high or highest, a task needs no
    recovery. A start that fails goes to its PO and is not retried."""
    holding = how == "hold" and _state()["brake"]["on"]
    waiting = []
    for listed in _d.chatroom.list_rooms(copy_rooms=False):
        if not listed.get("paceHold"):
            continue
        if _d._room_is_live(listed) or _d.workflow_of(listed) == "done":
            clear_hold(listed)
        elif not holding or _d.priority_of(listed) < HELD_FROM_PRIORITY:
            waiting.append(listed)
    if not waiting:
        return
    first = min(waiting, key=lambda r: (_d.priority_of(r), r.get("no") or 0))
    room = _d.chatroom.get_room(first["id"], public=False) or first
    try:
        _d.hub_launcher()._resume_room(room)
        clear_hold(room)
    except Exception as exc:                                # noqa: BLE001
        clear_hold(room)
        project = _project_of(room)
        if project:
            _d.board.deliver(project, f"{_d.task_label(room)} could not start after the pacing "
                                      f"brake: {exc}. Start it manually after resolving this.")


def _tell(how):
    """Each project's PO hears once that the brake engaged and once that it
    released; a delivery that fails is tried again on the next tick."""
    data = _state()
    brake = data["brake"]
    episode = brake["episode"]
    if not episode or episode <= brake["muted"] or how == "off":
        return False
    changed = False
    for project in _d.load_projects():
        if not project.get("poRoomId"):
            continue
        told = data["told"].setdefault(project["id"], {"engage": 0, "release": 0})
        if brake["on"] and told["engage"] < episode:
            what = ("New work of medium or lower priority waits; high and highest still start."
                    if how == "hold" else "Nothing is held: the setting is Tell only.")
            if _d.board.deliver(project, f"Pacing brake on: {brake['why']}. {what} "
                                         f"Until {brake['until']}."):
                told["engage"] = episode
                changed = True
        elif not brake["on"] and told["engage"] == episode and told["release"] < episode:
            if _d.board.deliver(project, "Pacing brake off: the projection recovered. "
                                         "Waiting tasks start by themselves, highest priority first."):
                told["release"] = episode
                changed = True
    return changed


def _note_use(now):
    """Each recently active task's token total, hourly, for the top project
    by share of recent use. The totals are cached by transcript time."""
    use = _state()["use"]
    if use and now - use[-1][0] < USE_EVERY_S:
        return False
    totals = {}
    for room in _d.chatroom.list_rooms(copy_rooms=False):
        if now - float(room.get("updatedAt") or 0) > (WINDOW_H + 2) * 3600:
            continue
        tokens = _d.compute_room_cost(room).get("tokens") or {}
        totals[room["id"]] = sum(tokens.values())
    use.append([now, totals])
    del use[:-(WINDOW_H + 1)]
    return True


def top_project():
    """(name, percent) of the project with the largest share of the tokens
    between the oldest and newest totals, or None."""
    use = _state()["use"]
    if len(use) < 2:
        return None
    links = _d.load_session_projects()
    old, new = use[0][1], use[-1][1]
    by_project, whole = {}, 0
    for rid, total in new.items():
        spent = max(0, total - old.get(rid, 0))
        room = _d.chatroom.get_room(rid, copy_room=False, messages=False)
        pid = _d.ensemble_tools._project_of_room(room, links) if room else ""
        by_project[pid] = by_project.get(pid, 0) + spent
        whole += spent
    named = {p["id"]: p.get("name", "") for p in _d.load_projects()}
    best = max((p for p in by_project if p in named), key=by_project.get, default=None)
    if best is None or not whole or not by_project[best]:
        return None
    return named[best], round(100 * by_project[best] / whole)


def line(snapshot, now):
    """Rate and projection per kind plus the top project, for a usage line to
    a PO; "" when nothing is known. Never raises."""
    try:
        parts = []
        cfg = _d.pace_settings(snapshot)
        for kind in ("claude", "codex"):
            seen = _d._kind_pace(snapshot, kind, "", cfg["margin"], now).get("burn") or {}
            if seen.get("state") == "known":
                parts.append(f"{_d._agent_kind_name(kind)} {seen['ratePerHour']:g}/h "
                             f"to {seen['projected']:g}%")
        top = top_project()
        if top:
            parts.append(f"top {top[0]} {top[1]}%")
        return " · ".join(parts)
    except Exception:                                       # noqa: BLE001
        return ""


def view():
    """The brake for /api/usage: the plan chip's tray."""
    with _LOCK:
        brake = _state()["brake"]
        return {"mode": mode(), "on": bool(brake["on"]), "why": brake["why"],
                "until": brake["until"], "limit": PROJECTED_LIMIT}


def tick(now=None):
    now = time.time() if now is None else now
    snapshot = _d.usage.snapshot()
    how = mode()
    with _LOCK:
        changed = sample(snapshot)
        try:
            judged = _judge(snapshot, now)
        except Exception as e:                              # noqa: BLE001
            print(f"[pacing] reading failed: {str(e)[:200]}", flush=True)
            judged = {"kinds": {}, "lowest": None}
        changed = _step(how, judged, now) or changed
        changed = _tell(how) or changed
        changed = _note_use(now) or changed
        if changed:
            _save()
    _release(how)


def maybe_tick():
    now = time.time()
    if now - _TICK["at"] >= TICK_EVERY_S:
        _TICK["at"] = now
        tick(now)
