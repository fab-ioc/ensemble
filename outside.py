"""The frame for outside message sources (design #1, step 1).

The hub watches outside message services (Slack first; Discord and Jira
later) on a schedule and hands each new message on. Each service is a module
under ``sources/`` that only reads (``sources/base.py`` has its shape). This
module does everything that is the same for every service:

* **Which sources there are.** ``DASHBOARD_DIR/outside/sources.json`` lists
  the configured sources by id, each with its type, its mode and its
  settings. No source is there until someone adds one (the Sources section
  of the setup file, #15), so a hub checks nothing by default. A mode is ``off``,
  ``on`` or ``decide`` (decide only: read and route, deliver nothing, raise
  no Inbox rows; for the side-by-side run beside Slack Man). ``paused`` is
  the setup file's "pause".
* **When to check.** Every ``fastEvery`` minutes in the fast hours
  (Mon–Fri 07:00–20:00 by default, in the hub's time zone), every
  ``slowEvery`` minutes otherwise. A check whose page was full is followed
  by another on the next tick, until it has caught up.
* **Read positions.** Where each read got to, kept per source. They move
  only after a good check. The next read starts 5 minutes before a position
  (the overlap, for messages a service lists late), or exactly at it after a
  full page. A read never starts more than ``maxGapDays`` (3) back: after a
  longer gap the health line says what was not read. A read the frame has
  not seen starts from now, so nothing older than a source's first check is
  ever handed on.
* **Never twice.** A message's key goes into "seen" and into the unrouted
  list in the same save. Seen keys are kept down to the *horizon*: the
  earliest time a read can start, less an hour. A message older than the
  horizon is never handed on, so a key forgotten can never come back.
* **Handing on.** For now every new message waits in the source's unrouted
  list (``pending``). Routing (steps 4 and 5) sets ``ROUTER``; each tick then
  hands it the unrouted messages and drops those it took. The routed ones go
  to the queue (``queue_put``), which delivery (step 3) reads with
  ``queue_due``. A message read in decide-only mode carries ``decideOnly``
  all the way and is never due for delivery.
* **Wake and start-up grace.** A tick that comes more than three ticks after
  the last one means the Mac slept. For 3 minutes after a wake or a hub
  start, a failed check is tried again each minute and not counted (the
  Slack connection is often not ready then).
* **Health.** Per source: Off, Paused, Not set up, Starting, Working, or Not
  working since a time with the reason. After ``downAfterMin`` (10) minutes
  not working, ``down_rows`` lists the source for the Inbox (step 6), except
  in decide-only mode and during grace.

State per source in ``DASHBOARD_DIR/outside/source-<id>.json``, the queue in
``outside/queue.json``; every file is written to a temporary file and renamed
into place. Nothing here holds a user's ids: they live only in those files.

Bound to the dashboard module like ``issues``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path

import durable
import sources
from backends import base as _base
from sources.base import (FAILED, NOT_SET_UP, OK, Check, Field, Message, Watch)

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


TICK_S = 60                     # the frame looks once a minute
WAKE_GAP_S = 3 * TICK_S         # a gap longer than three ticks: the Mac slept
GRACE_S = 3 * 60                # after a wake or a start, failures are not counted
OVERLAP_S = 5 * 60              # a read starts this far before its position
KEEP_EXTRA_S = 3600             # seen keys are kept this far below the earliest read
FORGET_READ_S = 14 * 86400      # a read not made for this long is forgotten
GAP_NOTE_S = 86400              # how long the health line says what was not read
UNROUTED_MAX = 2000             # the unrouted list keeps at most this many
KEEP_DONE_S = 4 * 86400         # the queue remembers delivered keys this long
SLACK_S = 5                     # a check this close to due is due (the loop ticks every 15 s)
NOTES_MAX = 10                  # the health line keeps at most this many of a check's notes
RECOVERED_S = 86400             # a "works again" row stays this long unless Done is pressed

MODES = ("off", "on", "decide")
RUNNING = ("on", "decide")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")

DEFAULTS = {
    "mode": "off",
    "paused": False,
    "fastEvery": 1,             # minutes between checks in the fast hours
    "slowEvery": 15,            # minutes between checks otherwise
    "fastDays": [0, 1, 2, 3, 4],    # Monday is 0
    "fastFrom": "07:00",
    "fastTo": "20:00",
    "maxGapDays": 3,            # a read never starts further back than this
    "downAfterMin": 10,         # not working this long: a row in the Inbox
}

# The settings every source has, whatever its type, beside the source's own
# ``fields()``.
FRAME_FIELDS = [
    Field("mode", "Reading", "choice", "off",
          choices=(("off", "Off"), ("on", "On"), ("decide", "Decide only (no delivery)")),
          help="Decide only reads and routes but delivers nothing and raises no Inbox rows."),
    Field("fastEvery", "Every (minutes) in working hours", "number", 1),
    Field("slowEvery", "Every (minutes) otherwise", "number", 15),
    Field("fastFrom", "Working hours from", "text", "07:00"),
    Field("fastTo", "Working hours to", "text", "20:00"),
    Field("fastDays", "Working days", "list", [0, 1, 2, 3, 4], help="Monday is 0."),
    Field("maxGapDays", "Read back at most (days)", "number", 3,
          help="After a longer gap, older messages are not read."),
    Field("downAfterMin", "Tell me after (minutes) not working", "number", 10),
]

# Entry points for the later steps. Each is a callable or None.
# PLACES(source_id) -> [{"channel": …, "thread": …}]: what the routing tables
# add to what is read (step 4).
PLACES = None
# ROUTER(source_id, entries) -> keys taken: routing (steps 4 and 5).
ROUTER = None

_LOCK = threading.RLock()           # one change of the files at a time
_CHECKING: dict[str, threading.Lock] = {}
_INSTANCES: dict[tuple, object] = {}
_STARTED = 0.0                      # the first tick of this hub
_WOKE = 0.0                         # the last wake seen
_LAST_TICK = 0.0                    # the last tick


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] outside: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def reset() -> None:
    """Forget what this process holds (as a hub restart does). Tests use it."""
    global _STARTED, _WOKE, _LAST_TICK
    with _LOCK:
        _CHECKING.clear()
        _INSTANCES.clear()
        _STARTED = _WOKE = _LAST_TICK = 0.0


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def root() -> Path:
    state = _d.DASHBOARD_DIR if _d is not None else _base.DASHBOARD_DIR
    return Path(state) / "outside"


def _read(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(path: Path, data: dict) -> None:
    try:
        durable.write_text(path, json.dumps(data, indent=1, ensure_ascii=False))
    except OSError as e:
        _log(f"cannot save {path.name}: {e}")
        raise


def _num(v, default: float = 0.0) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


# ---------------------------------------------------------------------------
# Configured sources
# ---------------------------------------------------------------------------

def _config_file() -> Path:
    return root() / "sources.json"


# A preview of the setup file (outside_setup.apply_text with check_only)
# sees its own copy of the sources' config, on its own thread only; the file
# and every other thread are left alone.
_PREVIEW = threading.local()


def preview_config(cfg: dict | None) -> None:
    """Start (a copy of the config) or end (None) this thread's preview."""
    _PREVIEW.cfg = json.loads(json.dumps(cfg)) if cfg is not None else None


def load_config() -> dict:
    """{id: config} of the configured sources, each with the defaults filled
    in. Empty when none was ever added. During this thread's preview, the
    preview's copy."""
    pv = getattr(_PREVIEW, "cfg", None)
    if pv is not None:
        return json.loads(json.dumps(pv))
    raw = _read(_config_file()).get("sources")
    out = {}
    for sid, c in (raw or {}).items():
        if not (isinstance(c, dict) and isinstance(sid, str) and ID_RE.match(sid)):
            continue
        cfg = {**DEFAULTS, **c}
        cfg["type"] = str(c.get("type") or "")
        cfg["settings"] = c.get("settings") if isinstance(c.get("settings"), dict) else {}
        if cfg["mode"] not in MODES:
            cfg["mode"] = "off"
        out[sid] = cfg
    return out


def source_config(source_id: str) -> dict | None:
    return load_config().get(source_id)


def set_source(source_id: str, **changes) -> dict:
    """Add a source or change its config (the setup file's Sources section,
    #15; tests). ``settings`` replaces the source's own settings. Returns the
    config as saved."""
    if not ID_RE.match(source_id or ""):
        raise ValueError(f"not a source id: {source_id!r}")
    if "mode" in changes and changes["mode"] not in MODES:
        raise ValueError(f"not a mode: {changes['mode']!r}")
    with _LOCK:
        data = _read(_config_file())
        all_ = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        cur = dict(all_.get(source_id) or {})
        cur.update(changes)
        if not cur.get("type"):
            raise ValueError("a source needs a type")
        all_[source_id] = cur
        data["sources"] = all_
        _write(_config_file(), data)
    return load_config()[source_id]


def remove_source(source_id: str) -> None:
    """Drop a source from the list. Its state file stays, so adding it back
    goes on from where it was and hands on nothing twice."""
    with _LOCK:
        data = _read(_config_file())
        all_ = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        if all_.pop(source_id, None) is not None:
            data["sources"] = all_
            _write(_config_file(), data)


def instance(source_id: str, cfg: dict):
    """The source object for a configured source, or None for a type the hub
    does not have."""
    kind = cfg.get("type") or ""
    make = sources.TYPES.get(kind)
    if make is None:
        return None
    with _LOCK:
        key = (source_id, kind, id(make))
        if key not in _INSTANCES:
            src = make()
            # A new object (a hub start, a restart) knows nothing of what the
            # last good check kept: hand it the settings and the kept memory
            # now, so a thread read before the next check has them.
            _remember(src, cfg, _load_state(source_id)["memory"])
            _INSTANCES[key] = src
        return _INSTANCES[key]


def _remember(src, cfg: dict, memory: dict) -> None:
    try:
        src.remember(dict(cfg.get("settings") or {}), dict(memory or {}))
    except Exception as e:
        _log(f"the source could not take its kept memory: {_plain(e)[:200]}")


def thread(source_id: str, ref: str):
    """The whole thread ``ref`` of a configured source (the PO's read tool,
    step 3). The source is handed its current settings and the memory its
    last good check kept first, so it reads the thread the same way after a
    hub restart as before it."""
    cfg = source_config(source_id)
    if cfg is None:
        raise ValueError(f"there is no input source called {source_id!r}")
    src = instance(source_id, cfg)
    if src is None:
        raise ValueError(f"this hub has no reader for the type {cfg['type'] or '(none)'}")
    with _LOCK:
        memory = _load_state(source_id)["memory"]
    _remember(src, cfg, memory)
    return src.thread(ref)


def describe_types() -> list[dict]:
    """Every type the hub knows, each with the frame's fields first (tests)."""
    frame = [f.to_dict() for f in FRAME_FIELDS]
    return [{**t, "frameFields": frame} for t in sources.catalog()]


# ---------------------------------------------------------------------------
# A source's state
# ---------------------------------------------------------------------------

def _state_file(source_id: str) -> Path:
    return root() / f"source-{source_id}.json"


def _load_state(source_id: str) -> dict:
    d = _read(_state_file(source_id))
    st = {
        "created": _num(d.get("created")),
        "horizon": _num(d.get("horizon")),
        "lastCheck": _num(d.get("lastCheck")),
        "lastGood": _num(d.get("lastGood")),
        "lastOutcome": d.get("lastOutcome") if isinstance(d.get("lastOutcome"), str) else "",
        "counted": bool(d.get("counted", True)),
        "more": bool(d.get("more")),
        "failures": int(_num(d.get("failures"))),
        "downSince": _num(d.get("downSince")),
        "reason": d.get("reason") if isinstance(d.get("reason"), str) else "",
        "notSetUp": d.get("notSetUp") if isinstance(d.get("notSetUp"), str) else "",
        "gap": d.get("gap") if isinstance(d.get("gap"), dict) else None,
        "memory": d.get("memory") if isinstance(d.get("memory"), dict) else {},
        "today": d.get("today") if isinstance(d.get("today"), dict) else {},
        "notes": [n for n in d.get("notes") or [] if isinstance(n, str)][:NOTES_MAX],
        "recovered": d.get("recovered") if isinstance(d.get("recovered"), dict) else None,
        "rowShown": bool(d.get("rowShown")),
    }
    # The last counted failure; a file from before it was kept: the last
    # check, when that was a counted failure.
    lc = d.get("lastCounted")
    if isinstance(lc, (int, float)) and not isinstance(lc, bool):
        st["lastCounted"] = float(lc)
    else:
        st["lastCounted"] = (st["lastCheck"] if st["downSince"] and st["lastOutcome"] == FAILED
                             and st["counted"] else 0.0)
    st["positions"] = {k: v for k, v in (d.get("positions") or {}).items()
                       if isinstance(k, str) and isinstance(v, dict)
                       and isinstance(v.get("at"), (int, float))}
    st["seen"] = {k: float(v) for k, v in (d.get("seen") or {}).items()
                  if isinstance(k, str) and isinstance(v, (int, float))}
    st["unrouted"] = [e for e in d.get("unrouted") or []
                      if isinstance(e, dict) and isinstance(e.get("key"), str)
                      and isinstance(e.get("message"), dict)]
    return st


def _save_state(source_id: str, st: dict) -> None:
    _write(_state_file(source_id), st)


def pending(source_id: str) -> list[dict]:
    """The source's unrouted list: ``{key, message, readAt, decideOnly}``,
    oldest read first. ``Message.from_dict(e["message"])`` gives the record."""
    with _LOCK:
        return list(_load_state(source_id)["unrouted"])


# ---------------------------------------------------------------------------
# The schedule, wake and grace
# ---------------------------------------------------------------------------

def _minutes(hhmm, default: int) -> int:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(hhmm or "").strip())
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59:
        return default
    return int(m.group(1)) * 60 + int(m.group(2))


def fast_hours(cfg: dict, now: float) -> bool:
    """Is ``now`` in the source's fast hours (in the hub's time zone)?"""
    t = time.localtime(now)
    days = cfg.get("fastDays")
    days = days if isinstance(days, list) else DEFAULTS["fastDays"]
    start = _minutes(cfg.get("fastFrom"), 7 * 60)
    end = _minutes(cfg.get("fastTo"), 20 * 60)
    return t.tm_wday in days and start <= t.tm_hour * 60 + t.tm_min < end


def interval(cfg: dict, now: float) -> float:
    """Seconds between checks at ``now``."""
    key = "fastEvery" if fast_hours(cfg, now) else "slowEvery"
    minutes = _num(cfg.get(key), DEFAULTS[key])
    return max(1.0, minutes) * 60


def note_tick(now: float) -> None:
    """Called on each tick: the first is the hub's start; a tick more than
    three ticks after the last one is a wake."""
    global _STARTED, _WOKE, _LAST_TICK
    if not _STARTED:
        _STARTED = now
    elif _LAST_TICK and now - _LAST_TICK > WAKE_GAP_S:
        _WOKE = now
        _log(f"the clock jumped {int(now - _LAST_TICK)} s: the machine slept; "
             f"failures are not counted for {GRACE_S // 60} min")
    _LAST_TICK = now


def in_grace(now: float) -> bool:
    since = max(_STARTED, _WOKE)
    return bool(since) and now - since < GRACE_S


def _every(cfg: dict, st: dict, now: float) -> float:
    if st["more"] or (st["lastOutcome"] == FAILED and not st["counted"]):
        return TICK_S
    return interval(cfg, now)


def is_due(source_id: str, now: float, cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else source_config(source_id)
    if not cfg or cfg["mode"] not in RUNNING or cfg.get("paused"):
        return False
    with _LOCK:
        st = _load_state(source_id)
    if not st["lastCheck"]:
        return True
    return now - st["lastCheck"] >= _every(cfg, st, now) - SLACK_S


# ---------------------------------------------------------------------------
# One check
# ---------------------------------------------------------------------------

def _plain(e: BaseException) -> str:
    text = " ".join(str(e).split())
    return (text or type(e).__name__)[:300]


def _starts(st: dict, cfg: dict, now: float) -> tuple[dict, float]:
    """({read: the time it starts from}, the limit when a read was cut to it
    else 0)."""
    floor = now - _num(cfg.get("maxGapDays"), DEFAULTS["maxGapDays"]) * 86400
    out, cut = {}, 0.0
    for name, p in st["positions"].items():
        start = p["at"] if p.get("full") else p["at"] - OVERLAP_S
        if start < floor:
            last = st["lastGood"]
            if last and last - _num(p.get("readAt")) > now - last:
                # The read was missing from good checks for longer than the
                # source has gone unread since: it is no longer made, not cut
                # off by a stop. It starts again from now, like a new one.
                # (Missing only briefly before a stop: cut to the limit.)
                continue
            start, cut = floor, floor
        out[name] = start
    return out, cut


def _count(st: dict, now: float, what: str, n: int = 1) -> None:
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if st["today"].get("day") != day:
        st["today"] = {"day": day, "checks": 0, "failed": 0, "messages": 0, "dropped": 0}
    st["today"][what] = st["today"].get(what, 0) + n


def _when(ts: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    t = time.localtime(ts)
    same = time.strftime("%Y-%m-%d", t) == time.strftime("%Y-%m-%d", time.localtime(now))
    return time.strftime("%H:%M" if same else "%d %b %H:%M", t)


def _record_failure(st: dict, reason: str, now: float) -> None:
    st["lastOutcome"] = FAILED
    st["reason"] = reason or "the check failed"
    if in_grace(now):
        st["counted"] = False       # tried again next minute, not counted
        return
    st["counted"] = True
    st["failures"] += 1
    st["lastCounted"] = now
    if not st["downSince"]:
        st["downSince"] = now


def _valid(m, kind: str) -> bool:
    return (isinstance(m, Message) and isinstance(m.key, str) and m.key.startswith(f"{kind}:")
            and len(m.key) > len(kind) + 1 and isinstance(m.at, (int, float))
            and not isinstance(m.at, bool))


def _take(st: dict, res: Check, now: float, kind: str, decide: bool) -> list[dict]:
    """Put the check's new messages into seen and the unrouted list. Returns
    the entries added."""
    queued = {e["key"] for e in st["unrouted"]}
    added, dropped = [], 0
    for m in sorted((m for m in res.messages if _valid(m, kind)), key=lambda m: m.at):
        if m.key in st["seen"] or m.key in queued:
            continue
        if m.at < st["horizon"]:
            dropped += 1            # older than any read can start: may have been handed on
            continue
        st["seen"][m.key] = float(m.at)
        queued.add(m.key)
        entry = {"key": m.key, "message": m.to_dict(), "readAt": now, "decideOnly": decide}
        st["unrouted"].append(entry)
        added.append(entry)
    bad = sum(1 for m in res.messages if not _valid(m, kind))
    if bad:
        _log(f"{bad} record(s) without a proper key or time were left out")
    if dropped:
        _log(f"{dropped} message(s) older than what was already read were left out")
    if len(st["unrouted"]) > UNROUTED_MAX:
        over = len(st["unrouted"]) - UNROUTED_MAX
        st["unrouted"] = st["unrouted"][over:]
        _count(st, now, "dropped", over)    # health()'s today.dropped shows it
        _log(f"the unrouted list is full: the {over} oldest were dropped")
    return added


def _move_positions(st: dict, res: Check, cfg: dict, now: float) -> None:
    floor = now - _num(cfg.get("maxGapDays"), DEFAULTS["maxGapDays"]) * 86400
    for name, t in res.positions.items():
        if (not isinstance(name, str) or not isinstance(t, (int, float)) or isinstance(t, bool)
                or not math.isfinite(t)):
            _log(f"the position {t!r} of the read {name!r} is not a time: left as it was")
            continue
        if t > now:
            # Positions only move forward and the horizon follows them, so a
            # time after now (milliseconds, a clock fault) would stop the read
            # for good: it is taken as now.
            _log(f"the position of the read {name!r} is after now ({t!r}): taken as now")
            t = now
        old = st["positions"].get(name, {}).get("at", 0.0)
        st["positions"][name] = {"at": max(float(t), old), "full": name in res.full,
                                 "readAt": now}
    # A read not made for longer than the gap limit (or two weeks) is
    # forgotten: if it comes back, it starts from now.
    forget = max(now - FORGET_READ_S, floor)
    st["positions"] = {k: p for k, p in st["positions"].items()
                       if _num(p.get("readAt"), now) >= forget}
    # The horizon: no read can start before the earliest start, nor before
    # the gap limit. Seen keys below it (less an hour) can go.
    starts = [p["at"] if p.get("full") else p["at"] - OVERLAP_S for p in st["positions"].values()]
    earliest = max(min(starts) if starts else now, floor)
    st["horizon"] = max(st["horizon"], earliest - KEEP_EXTRA_S)
    st["seen"] = {k: at for k, at in st["seen"].items() if at >= st["horizon"]}


def run_check(source_id: str, now: float | None = None) -> dict:
    """Check one source once. Returns what happened: ``{"ran", "outcome",
    "added", "reason"}``."""
    now = time.time() if now is None else now
    cfg = source_config(source_id)
    if not cfg or cfg["mode"] not in RUNNING or cfg.get("paused"):
        return {"ran": False, "outcome": "", "added": 0, "reason": "not on"}
    decide = cfg["mode"] == "decide"
    src = instance(source_id, cfg)
    settings = dict(cfg["settings"])
    if src is None:
        problem = f"this hub has no reader for the type {cfg['type'] or '(none)'}"
    else:
        try:
            problem = src.problem(settings) or ""
        except Exception as e:
            problem = f"its settings could not be read: {_plain(e)}"
    with _LOCK:
        st = _load_state(source_id)
        if not st["created"]:
            # A new source: nothing written before now is ever handed on.
            st["created"] = st["horizon"] = now
            _save_state(source_id, st)
        if problem:
            st["lastCheck"], st["lastOutcome"], st["notSetUp"] = now, NOT_SET_UP, problem
            _save_state(source_id, st)
            return {"ran": True, "outcome": NOT_SET_UP, "added": 0, "reason": problem}
    starts, cut = _starts(st, cfg, now)
    places = []
    if PLACES is not None:
        try:
            places = list(PLACES(source_id) or [])
        except Exception as e:
            _log(f"{source_id}: the routing tables' places could not be read: {_plain(e)}")
    watch = Watch(settings=settings, places=places, memory=dict(st["memory"]),
                  source_id=source_id)
    try:
        res = src.check(watch, dict(starts), now)
        if not isinstance(res, Check) or res.outcome not in (OK, FAILED, NOT_SET_UP):
            res = Check(outcome=FAILED, reason="the source answered with something that is not a check")
    except Exception as e:
        res = Check(outcome=FAILED, reason=_plain(e))
    with _LOCK:
        st = _load_state(source_id)     # the unrouted list may have been handed on meanwhile
        st["lastCheck"] = now
        _count(st, now, "checks")
        if res.outcome == NOT_SET_UP:
            st["lastOutcome"], st["notSetUp"] = NOT_SET_UP, res.reason or "not set up"
            _save_state(source_id, st)
            return {"ran": True, "outcome": NOT_SET_UP, "added": 0, "reason": st["notSetUp"]}
        st["notSetUp"] = ""
        if res.outcome == FAILED:
            _record_failure(st, res.reason, now)
            _count(st, now, "failed")
            _save_state(source_id, st)
            _log(f"{source_id}: check failed{'' if st['counted'] else ' (not counted: grace)'}: "
                 f"{st['reason'][:200]}")
            return {"ran": True, "outcome": FAILED, "added": 0, "reason": st["reason"]}
        added = _take(st, res, now, src.kind, decide)
        _count(st, now, "messages", len(added))
        _move_positions(st, res, cfg, now)
        if res.memory is not None and isinstance(res.memory, dict):
            st["memory"] = res.memory
        if st["downSince"]:
            _log(f"{source_id}: read again (not working since {_when(st['downSince'], now)})")
            if st["rowShown"] and cfg["mode"] == "on":
                # The Inbox showed it not working: a row says it works again.
                st["recovered"] = {"from": st["downSince"], "to": now}
        notes = getattr(res, "notes", None)
        if isinstance(notes, list):
            st["notes"] = [str(n) for n in notes if str(n).strip()][:NOTES_MAX]
        if cut and st["gap"] and st["lastGood"] > cut and now - st["lastGood"] <= OVERLAP_S:
            # The last good check was moments ago: nothing went unread since;
            # a catch-up that keeps its position back (Jira's) is still
            # running from the cut that made this note. It stays. (After a
            # longer stop the cut is new, and so is the note.)
            pass
        elif cut:
            off = st["lastGood"] or min((p["at"] for p in st["positions"].values()), default=cut)
            st["gap"] = {"from": off, "to": now, "before": cut}
        elif st["gap"] and now - _num(st["gap"].get("to")) > GAP_NOTE_S:
            st["gap"] = None
        st.update(lastOutcome=OK, counted=True, failures=0, downSince=0.0, reason="",
                  lastGood=now, more=res.more, lastCounted=0.0, rowShown=False)
        _save_state(source_id, st)
    _note_item_states(source_id, getattr(res, "item_states", None))
    if added:
        _log(f"{source_id}: {len(added)} new message(s)"
             + (" (decide only)" if decide else "") + (", more waiting" if res.more else ""))
    return {"ran": True, "outcome": OK, "added": len(added), "reason": ""}


def _note_item_states(source_id: str, states) -> None:
    """Hand the work item states a good check learned (Jira: a ticket
    released, closed or done) to the routing table's items
    (``outside_items``), which take a finished ticket out of its row 14 days
    later. Imported here: ``outside_items`` imports this module."""
    if not states:
        return
    try:
        import outside_items
        for key, state, at in states:
            outside_items.note_ticket_state(key, state, at)
    except Exception as e:
        _log(f"{source_id}: ticket states could not be kept: {_plain(e)[:200]}")


def _checking(source_id: str) -> threading.Lock:
    with _LOCK:
        return _CHECKING.setdefault(source_id, threading.Lock())


def _check_then_release(source_id: str, lock: threading.Lock) -> None:
    try:
        run_check(source_id)
    except Exception as e:
        _log(f"{source_id}: check error: {_plain(e)[:200]}")
    finally:
        lock.release()


def maybe_tick(background: bool = True, now: float | None = None) -> None:
    """Called from the hub's background loop (every few seconds): checks each
    source that is due, each on a thread of its own so a slow service holds
    up nothing else, then hands the unrouted messages to routing when it is
    built. With no source configured it reads one small file and does
    nothing else."""
    now = time.time() if now is None else now
    note_tick(now)
    cfg = load_config()
    for sid, c in cfg.items():
        if not is_due(sid, now, c):
            continue
        lock = _checking(sid)
        if not lock.acquire(blocking=False):
            continue                # its last check is still running
        if background:
            threading.Thread(target=_check_then_release, args=(sid, lock), daemon=True,
                             name=f"outside-{sid}").start()
        else:
            try:
                run_check(sid, now)
            finally:
                lock.release()
    if ROUTER is not None and cfg:
        hand_on(ROUTER, list(cfg))


# ---------------------------------------------------------------------------
# Handing on to routing, and the queue
# ---------------------------------------------------------------------------

def hand_on(take, source_ids: list[str] | None = None) -> int:
    """Give each source's unrouted list to ``take(source_id, entries)``,
    which returns the keys it took (put in the queue, or decided to drop).
    Those leave the unrouted list. A crash between the two leaves a message
    in both: ``queue_put`` ignores a key it already has, for whatever
    initiative, so it is neither delivered twice nor sent to a second PO.
    Returns how many were taken."""
    n = 0
    for sid in source_ids if source_ids is not None else list(load_config()):
        entries = pending(sid)
        if not entries:
            continue
        try:
            taken = set(take(sid, entries) or ())
        except Exception as e:
            _log(f"{sid}: routing error: {_plain(e)[:200]}")
            continue
        n += drop_unrouted(sid, taken)
    return n


def drop_unrouted(source_id: str, keys) -> int:
    """Take these keys out of the source's unrouted list: they were routed
    or dropped. Routing step 3 (``outside_model``) calls this from its own
    thread when the model has answered. Returns how many left the list."""
    keys = set(keys or ())
    if not keys:
        return 0
    with _LOCK:
        st = _load_state(source_id)
        before = len(st["unrouted"])
        st["unrouted"] = [e for e in st["unrouted"] if e["key"] not in keys]
        if len(st["unrouted"]) == before:
            return 0
        _save_state(source_id, st)
        return before - len(st["unrouted"])


def last_check(source_id: str) -> float:
    """When the source was last checked (good or not), 0 if never."""
    with _LOCK:
        return _load_state(source_id)["lastCheck"]


def _queue_file() -> Path:
    return root() / "queue.json"


def _load_queue() -> dict:
    d = _read(_queue_file())
    items = [q for q in d.get("items") or [] if isinstance(q, dict)
             and isinstance(q.get("key"), str) and isinstance(q.get("to"), str)]
    done = {k: float(v) for k, v in (d.get("done") or {}).items()
            if isinstance(k, str) and isinstance(v, (int, float))}
    return {"items": items, "done": done}


def _qkey(key: str, to: str) -> str:
    return f"{key}\n{to}"


def queue_put(items: list[dict], now: float | None = None, also: bool = False) -> list[dict]:
    """Add routed messages to the queue: each ``{key, to, message, ...}``,
    ``to`` the initiative's id. A message may go to two initiatives (one
    item each, in the same call). An item whose (key, to) is queued or was
    delivered lately is ignored.

    A message already queued or delivered lately *for any initiative* is
    ignored too: that is the router running again on it after the hub stopped
    between this call and the save of the unrouted list (``hand_on``), and it
    may pick another initiative the second time. The initiatives it was first
    queued for are kept, so it never reaches a second PO that way. ``also``
    is for a deliberate send to one more initiative (Send elsewhere, step 7).
    Returns the items added."""
    now = time.time() if now is None else now
    with _LOCK:
        q = _load_queue()
        have = {_qkey(i["key"], i["to"]) for i in q["items"]} | set(q["done"])
        known = {i["key"] for i in q["items"]} | {k.rsplit("\n", 1)[0] for k in q["done"]}
        added = []
        for it in items:
            if not (isinstance(it, dict) and isinstance(it.get("key"), str)
                    and isinstance(it.get("to"), str)):
                continue
            k = _qkey(it["key"], it["to"])
            if k in have or (it["key"] in known and not also):
                continue
            have.add(k)
            item = {**it, "queuedAt": now, "decideOnly": bool(it.get("decideOnly"))}
            q["items"].append(item)
            added.append(item)
        if added:
            _write(_queue_file(), q)
        return added


def queue_items(to: str | None = None) -> list[dict]:
    """Everything queued (for ``to`` when given), decide-only items included."""
    with _LOCK:
        items = _load_queue()["items"]
    return [i for i in items if to is None or i["to"] == to]


def queue_due(to: str | None = None) -> list[dict]:
    """What delivery may type into a PO: never an item read in decide-only mode."""
    return [i for i in queue_items(to) if not i.get("decideOnly")]


def queue_take_back(key: str, to: str) -> bool:
    """The log view moved a message elsewhere before it was typed to ``to``:
    it leaves the queue without being marked as delivered there. True when
    it was queued for ``to``. The caller holds delivery's lock, so delivery
    is not typing it at the same time."""
    with _LOCK:
        q = _load_queue()
        keep = [i for i in q["items"] if not (i["key"] == key and i["to"] == to)]
        if len(keep) == len(q["items"]):
            return False
        q["items"] = keep
        _write(_queue_file(), q)
        return True


DECIDE_KEEP_S = KEEP_DONE_S       # a decide-only item leaves the queue after this long


def queue_drop_decided(now: float | None = None) -> list[dict]:
    """Items queued in decide-only mode are never delivered: after
    DECIDE_KEEP_S they leave the queue (not marked delivered). Returns them;
    writes nothing when none is that old (P439)."""
    now = time.time() if now is None else now
    with _LOCK:
        q = _load_queue()
        old = [i for i in q["items"] if i.get("decideOnly")
               and now - float(i.get("queuedAt") or 0) > DECIDE_KEEP_S]
        if old:
            q["items"] = [i for i in q["items"] if i not in old]
            _write(_queue_file(), q)
        return old


def queue_delivered(key: str, to: str) -> bool:
    """Whether delivery has typed ``key`` to ``to`` lately (4 days)."""
    with _LOCK:
        return _qkey(key, to) in _load_queue()["done"]


def queue_done(pairs: list[tuple[str, str]], now: float | None = None) -> int:
    """Delivery has typed these (key, to): they leave the queue in the same
    save that records them as delivered. Returns how many left."""
    now = time.time() if now is None else now
    gone = {_qkey(k, t) for k, t in pairs}
    with _LOCK:
        q = _load_queue()
        keep = [i for i in q["items"] if _qkey(i["key"], i["to"]) not in gone]
        n = len(q["items"]) - len(keep)
        q["items"] = keep
        for k in gone:
            q["done"][k] = now
        q["done"] = {k: at for k, at in q["done"].items() if now - at <= KEEP_DONE_S}
        _write(_queue_file(), q)
        return n


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def label_of(cfg: dict) -> str:
    make = sources.TYPES.get(cfg.get("type") or "")
    return (getattr(make, "label", "") if make else "") or cfg.get("type") or "A source"


def health(source_id: str, now: float | None = None) -> dict | None:
    """What the Input sources panel shows for one source (step 6)."""
    now = time.time() if now is None else now
    cfg = source_config(source_id)
    if cfg is None:
        return None
    with _LOCK:
        st = _load_state(source_id)
    if cfg["mode"] == "off":
        state, text = "off", "Off"
    elif cfg.get("paused"):
        state, text = "paused", "Paused"
    elif st["notSetUp"]:
        state, text = "not set up", f"Not set up: {st['notSetUp']}"
    elif st["downSince"]:
        state = "not working"
        text = f"Not working since {_when(st['downSince'], now)}: {st['reason']}"
    elif st["lastGood"] and st["lastCheck"]:
        state, text = "working", "Working"
    else:
        state, text = "starting", "Starting" + (f" ({st['reason']})" if st["reason"] else "")
    gap = ""
    if st["gap"]:
        g = st["gap"]
        gap = (f"nothing was read from {_when(_num(g.get('from')), now)} to "
               f"{_when(_num(g.get('to')), now)}; messages before "
               f"{_when(_num(g.get('before')), now)} were not read")
    running = cfg["mode"] in RUNNING and not cfg.get("paused")
    nxt = (st["lastCheck"] + _every(cfg, st, now)) if running and st["lastCheck"] else (now if running else 0)
    return {"id": source_id, "type": cfg["type"], "label": label_of(cfg), "mode": cfg["mode"],
            "paused": bool(cfg.get("paused")), "state": state, "text": text,
            "since": st["downSince"] or 0, "reason": st["reason"], "failures": st["failures"],
            "lastCheck": st["lastCheck"], "lastGood": st["lastGood"], "nextCheck": nxt,
            "inGrace": in_grace(now), "gap": gap, "unrouted": len(st["unrouted"]),
            "today": dict(st["today"])}


def health_all(now: float | None = None) -> list[dict]:
    return [h for h in (health(sid, now) for sid in load_config()) if h]


def down_rows(now: float | None = None) -> list[dict]:
    """Sources not working for longer than their ``downAfterMin``: one row
    each for the Inbox (step 6), which makes the device notification when a
    row appears and when it goes. None in decide-only mode, none while
    paused. During grace no new row appears, but a row that was already due
    before the grace began stays, so a wake or a restart does not make the
    row go and come back."""
    now = time.time() if now is None else now
    rows = []
    for sid, cfg in load_config().items():
        with _LOCK:
            st = _load_state(sid)
            due = _down_due(cfg, st, now)
            if due and not st["rowShown"]:
                # Kept, so the row stays through a restart or a wake, and
                # the first good read after it gives a "works again" row.
                st["rowShown"] = True
                _save_state(sid, st)
        if due:
            label = label_of(cfg)
            rows.append({"source": sid, "label": label, "since": st["downSince"],
                         "reason": st["reason"],
                         "text": f"{label} is not being read since "
                                 f"{_when(st['downSince'], now)}: {st['reason']}"})
    return rows


def _down_due(cfg: dict, st: dict, now: float) -> bool:
    """Is the source's down row in the Inbox at ``now``? Mode on, not paused,
    not working, and one of:
    - the row was already shown (it stays through a restart or a wake);
    - a counted failure came ``downAfterMin`` or more after the first one;
    - the row fell due (``downSince`` + ``downAfterMin``) while this hub was
      running, awake and past its grace.
    So a source that failed a few minutes before a sleep or a restart gets
    no row on waking: the time it was not watched does not count, and its
    row waits for a failure counted after the grace."""
    if cfg["mode"] != "on" or cfg.get("paused") or not st["downSince"]:
        return False
    if st["rowShown"]:
        return True
    due_at = st["downSince"] + _num(cfg.get("downAfterMin"), DEFAULTS["downAfterMin"]) * 60
    if st["lastCounted"] >= due_at:
        return True
    since = max(_STARTED, _WOKE)
    return bool(since) and due_at >= since + GRACE_S and now >= due_at


def recovered_rows(now: float | None = None) -> list[dict]:
    """Sources whose down row was in the Inbox and that were read again: one
    "works again" row each, for a day or until Done is pressed
    (``recovered_seen``), and not while the source has a down row again."""
    now = time.time() if now is None else now
    rows = []
    for sid, cfg in load_config().items():
        with _LOCK:
            st = _load_state(sid)
        r = st["recovered"]
        if not r or now - _num(r.get("to")) > RECOVERED_S or _down_due(cfg, st, now):
            continue
        label = label_of(cfg)
        frm, to = _num(r.get("from")), _num(r.get("to"))
        rows.append({"source": sid, "label": label, "from": frm, "at": to,
                     "text": f"{label} is read again (not read from {_when(frm, now)} "
                             f"to {_when(to, now)})"})
    return rows


def recovered_seen(source_id: str) -> bool:
    """Done on a "works again" row: it goes. False when there was none."""
    with _LOCK:
        st = _load_state(source_id)
        if not st["recovered"]:
            return False
        st["recovered"] = None
        _save_state(source_id, st)
        return True


# ---------------------------------------------------------------------------
# Changes to a source (the setup file's Sources section, #15)
# ---------------------------------------------------------------------------

class Refused(ValueError):
    """A change that cannot be made; the words say why."""


_HHMM = re.compile(r"^(([01]?\d|2[0-3]):[0-5]\d|24:00)$")
_AT_LEAST_ONE = ("fastEvery", "slowEvery", "maxGapDays", "downAfterMin")


def add_source(kind: str, built: bool = False) -> dict:
    """A new source of type ``kind``, off, with its fields' defaults. Its id
    is the type ("slack"), else "slack-2", ... Returns its config with its
    ``id``. ``built``: any type the hub has a reader for, also one
    ``sources.LATER`` lists (the setup file, #15)."""
    kind = str(kind or "")
    if kind not in sources.TYPES or (kind in sources.LATER and not built):
        later = sources.LATER.get(kind)
        raise Refused(f"{later} comes later: it cannot be added yet" if later
                      else f"there is no input source type {kind!r}")
    src = sources.TYPES[kind]()
    settings = {f.key: f.default for f in src.fields() if f.kind != "found"}
    with _LOCK:
        have = load_config()
        sid, n = kind, 1
        while sid in have:
            n += 1
            sid = f"{kind}-{n}"
        if not ID_RE.match(sid):
            raise Refused(f"{kind!r} cannot name a source")
        return {"id": sid, **set_source(sid, type=kind, mode="off", paused=False, settings=settings)}


def _clean_value(f: Field, value):
    """A value from the page for the field ``f``, checked for its kind.
    Raises Refused with what is wrong."""
    what = f.label or f.key
    if f.kind == "found":
        raise Refused(f"{what} is found by the checks and cannot be set")
    if f.kind == "switch":
        if not isinstance(value, bool):
            raise Refused(f"{what} is on or off")
        return value
    if f.kind == "number":
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise Refused(f"{what} must be a number") from None
        if isinstance(value, bool) or not math.isfinite(v) or v < 0:
            raise Refused(f"{what} must be a number, 0 or more")
        return int(v) if v == int(v) else v
    if f.kind == "list":
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list):
            raise Refused(f"{what} is a list")
        return [str(x).strip() for x in items if str(x).strip()]
    if f.kind == "choice":
        allowed = [c[0] for c in f.choices]
        if value not in allowed:
            raise Refused(f"{what} must be one of: {', '.join(str(c[1]) for c in f.choices)}")
        return value
    if not isinstance(value, str) or len(value) > 500:
        raise Refused(f"{what} is a text of at most 500 characters")
    return value.strip()


def _frame_value(key: str, value):
    f = next(f for f in FRAME_FIELDS if f.key == key)
    v = _clean_value(f, value)
    if key in _AT_LEAST_ONE and v < 1:
        raise Refused(f"{f.label} must be at least 1")
    if key in ("fastFrom", "fastTo") and not _HHMM.match(v):
        raise Refused(f"{f.label} is a time like 07:00")
    if key == "fastDays":
        try:
            days = sorted({int(x) for x in v})
        except ValueError:
            raise Refused("Working days are numbers from 0 (Monday) to 6 (Sunday)") from None
        if any(d < 0 or d > 6 for d in days):
            raise Refused("Working days are numbers from 0 (Monday) to 6 (Sunday)")
        v = days
    return v


def _read_now(cfg: dict) -> bool:
    return cfg["mode"] in RUNNING and not cfg.get("paused")


def _start_watch_again(source_id: str) -> None:
    """Resume, or On / Decide only after Off: the time the source was not
    read is not time spent down. Its failure count starts again, a down row
    shown before is gone (no "works again" row for it), and it is checked
    at the next tick. ``recovered`` is kept."""
    with _LOCK:
        st = _load_state(source_id)
        st.update(downSince=0.0, failures=0, lastCounted=0.0, rowShown=False, reason="",
                  lastOutcome="", counted=True, lastCheck=0.0)
        _save_state(source_id, st)


def update_source(source_id: str, mode=None, paused=None, settings: dict | None = None,
                  frame: dict | None = None) -> dict:
    """A change made on a source's page. ``settings`` holds the changed
    values of the type's own fields (merged into what is kept), ``frame``
    those of the frame's fields (the schedule). Every value is checked for
    its field's kind; nothing is saved when one is refused. Returns the
    config as saved."""
    cfg = source_config(source_id)
    if cfg is None:
        raise Refused(f"there is no input source called {source_id!r}")
    changes = {}
    if mode is not None:
        if mode not in MODES:
            raise Refused(f"not a reading mode: {mode!r}")
        changes["mode"] = mode
    if paused is not None:
        if not isinstance(paused, bool):
            raise Refused("paused is yes or no")
        changes["paused"] = paused
    for key, value in (frame or {}).items():
        if key == "mode":
            raise Refused("the reading mode is set on its own")
        if key not in {f.key for f in FRAME_FIELDS}:
            raise Refused(f"there is no schedule setting {key!r}")
        changes[key] = _frame_value(key, value)
    if settings:
        make = sources.TYPES.get(cfg["type"])
        if make is None:
            raise Refused(f"this hub has no reader for the type {cfg['type'] or '(none)'}")
        fields = {f.key: f for f in (instance(source_id, cfg) or make()).fields()}
        merged = dict(cfg["settings"])
        for key, value in settings.items():
            if key not in fields:
                raise Refused(f"{label_of(cfg)} has no setting {key!r}")
            merged[key] = _clean_value(fields[key], value)
        changes["settings"] = merged
    if not changes:
        return cfg
    saved = set_source(source_id, **changes)
    if not _read_now(cfg) and _read_now(saved):
        _start_watch_again(source_id)
    src = instance(source_id, saved)
    if src is not None and "settings" in changes:
        with _LOCK:
            memory = _load_state(source_id)["memory"]
        _remember(src, saved, memory)
    return saved


def check_now(source_id: str) -> None:
    """Check now: one check at once, in the background. Refused when the
    source is off or paused, or a check of it is running."""
    cfg = source_config(source_id)
    if cfg is None:
        raise Refused(f"there is no input source called {source_id!r}")
    if cfg["mode"] not in RUNNING:
        raise Refused(f"{label_of(cfg)} is off: turn it on first")
    if cfg.get("paused"):
        raise Refused(f"{label_of(cfg)} is paused: resume it first")
    lock = _checking(source_id)
    if not lock.acquire(blocking=False):
        raise Refused(f"{label_of(cfg)} is being checked now")
    threading.Thread(target=_check_then_release, args=(source_id, lock), daemon=True,
                     name=f"outside-{source_id}-now").start()


def panel(now: float | None = None) -> dict:
    """Each source with its config, health, fields and values: the Input
    sources panel draws one health line per source from it."""
    now = time.time() if now is None else now
    out = []
    for sid, cfg in load_config().items():
        h = health(sid, now) or {}
        make = sources.TYPES.get(cfg["type"])
        src = instance(sid, cfg) if make else None
        with _LOCK:
            st = _load_state(sid)
        fields = [f.to_dict() for f in src.fields()] if src else []
        values = {f["key"]: cfg["settings"].get(f["key"], f["default"])
                  for f in fields if f["kind"] != "found"}
        found, problem = {}, ""
        if src is not None:
            try:
                found = dict(src.found(st["memory"]) or {})
            except Exception as e:
                _log(f"{sid}: its found values could not be read: {_plain(e)[:200]}")
            try:
                problem = src.problem(dict(cfg["settings"])) or ""
            except Exception as e:
                problem = f"its settings could not be read: {_plain(e)}"
        elif cfg["type"]:
            problem = f"this hub has no reader for the type {cfg['type']}"
        lock = _CHECKING.get(sid)
        out.append({**h, "fields": fields, "values": values, "found": found,
                    "problem": problem, "notes": list(st["notes"]),
                    "checking": bool(lock and lock.locked()),
                    "routed": bool(src and src.route_fields()),
                    "frame": {f.key: cfg.get(f.key, f.default) for f in FRAME_FIELDS},
                    "down": _down_due(cfg, st, now)})
    return {"sources": out}
