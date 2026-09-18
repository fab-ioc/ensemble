"""Promises with a time: the ``## Due`` section of a handover, and its wake.

A PO that promised the CEO something "at 15:40" and was then rotated, or simply
went idle, had nothing to wake it: the progress check (``digest.py``) rings it
only when a task changed (seen 2026-09-18: a test promised for the afternoon
was never run, the fresh PO waited four hours). So a handover names what is due
at a time, one line per item::

    ## Due
    - 15:40 — the paper-1 hand test
    - 09-19 08:30 — read the overnight report

and about once a minute the hub reads that section of every project's
``PO-HANDOVER.md`` and of every running task owner's ``TASK-HANDOVER.md``:

* **An item whose time has come, its agent running and idle** (its turn over,
  its terminal quiet, nothing just submitted, no rotation under way) is typed
  into it as one ``[due] …`` line, a hub input like a digest. Items of one
  handover that are due together go in one line.
* **Busy** → nothing is typed; it is tried again each minute until it is idle.
  The look rides the progress check's loop, after its tick: while a digest is
  being written up (up to 90 s a project) it is that much later. Best effort.
* **More due at once than one line holds** (``_WAKE_MAX``): the line names as
  many as fit, in order; the others stay due and go in the next line.
* **A PO that is not running** is not resumed (resumes are the CEO's or the
  PO's): the item is written to its room's chat as a line from the hub, once.
  If the PO runs again while the item is less than ``MAX_AGE_S`` old and still
  in the handover, it is typed the item then, once.
* **Once per item.** What was delivered is kept in ``DASHBOARD_DIR/due.json``
  by handover path, line text and due time, so a hub restart re-sends nothing.
* **Never an old one.** An item more than ``MAX_AGE_S`` (24 h) past its time
  when the hub could first deliver it is dropped without a wake: a handover
  first read long after it was written (a hub that was down, a section older
  than this reader) wakes nobody about yesterday.

**Times** are the hub's local time, 24 h. ``HH:MM`` alone is the first such
time at or after the line was written — taken as the handover's modification
time when the hub first sees the line, and kept, so later edits of the file do
not move it. ``MM-DD HH:MM`` takes the year nearest to then; ``YYYY-MM-DD
HH:MM`` is exact. A time marked ``host`` or ``local`` is local; one marked with
any other zone (``NY``, ``ET``, ``UTC`` …) cannot be converted without a time
zone database, which Windows' Python lacks, and is ignored — as is every line
that does not read as ``- time — what``, a ticked ``[x]`` item, and anything in
a code fence. Ignored means no wake and no error.

Bound to the dashboard module like ``digest``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


TICK_S = 60                 # how often the handovers are looked at
MAX_AGE_S = 24 * 3600       # an item this far past its time is never delivered
SETTLE_S = 5.0              # a handover written this lately may be half-written
SENDER = "ensemble"         # who the chat line is from in the PO's room
PREFIX = "[due] "           # how the typed line starts (HUB_INPUT_KINDS)
_WHAT_MAX = 240             # one item's words in the typed line
_WAKE_MAX = 900             # the whole line: a TUI takes one line, not a paste

_LOCK = threading.Lock()    # one look at a time
_PARSED: dict[str, tuple] = {}     # path -> ((mtime, size), items)
_OWNER_PATHS: dict[tuple, Path] = {}   # (roomId, identity) -> its handover
_LAST = 0.0                 # when tick last ran (maybe_tick)


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] due: {msg}", flush=True)
    except (OSError, ValueError):       # a console that cannot show the line
        pass                            # must not cost a delivery its record


# ---------------------------------------------------------------------------
# Reading the section
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ITEM = re.compile(
    r"^\s*[-*+]\s+(?:\[(?P<box>[ xX])\]\s+)?"
    r"(?:(?:(?P<y>\d{4})-)?(?P<mo>\d{1,2})-(?P<d>\d{1,2})[ T]+)?"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?!\d|:\d)(?P<rest>.*)$")
_LOCAL = {"host", "local", "hub", "here"}
# Zones a PO may write. Any other ALL-CAPS word right before the dash counts
# as one too ("15:40 SGT — …"); a word of the item's own text does not.
_ZONES = {"ny", "nyc", "et", "est", "edt", "ct", "cst", "cdt", "mt", "mst", "mdt", "pt", "pst",
          "pdt", "utc", "gmt", "z", "cet", "cest", "bst", "jst", "hkt", "sgt", "ist", "aest",
          "london", "tokyo", "chicago", "eastern", "pacific", "zulu"}
_SEP = r"(?:—|–|-{1,2}|:)"
_LEAD = re.compile(rf"^\(?(?P<w>[A-Za-z][A-Za-z+\-0-9/]*)(?P<time>\s+time)?\)?(?P<after>\s*{_SEP}\s|\s*$|\s)")


def _what(rest: str) -> str | None:
    """The item's words from what follows its time, or None when the time is
    marked with a zone that is not the hub's."""
    rest = rest.strip()
    m = _LEAD.match(rest)
    if m:
        w = m["w"].lower()
        dash_next = bool(m["after"].strip()) or not m["after"]
        zone_like = w in _ZONES or re.match(r"^(utc|gmt)[+-]\d", w) or (
            dash_next and m["w"].isupper() and 2 <= len(m["w"]) <= 5)
        if w in _LOCAL:
            rest = rest[m.end("time") if m["time"] else m.end("w"):]
        elif zone_like:
            return None
    return re.sub(r"^[\s—–\-:)]+", "", rest).strip()


def parse(text: str) -> list[dict]:
    """The items of a handover's ``## Due`` section, in order: ``{line, year,
    month, day, hour, minute, what}`` (year/month/day 0 when not written).
    ``line`` is the line as written, stripped: what an item is known by."""
    out, level, fenced = [], 0, False
    for raw in (text or "").splitlines():
        if _FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        h = _HEADING.match(raw)
        if h:
            depth, title = len(h[1]), re.sub(r"[*_`]", "", h[2]).strip().lower()
            if level and depth <= level:
                level = 0
            if not level and re.match(r"due\b", title):
                level = depth
            continue
        if not level:
            continue
        m = _ITEM.match(re.sub(r"\*\*|__|`", "", raw))
        if not m or (m["box"] or " ") in "xX":
            continue
        hour, minute = int(m["h"]), int(m["mi"])
        if hour > 23 or minute > 59:
            continue
        what = _what(m["rest"])
        if not what:
            continue
        out.append({"line": raw.strip(), "year": int(m["y"] or 0), "month": int(m["mo"] or 0),
                    "day": int(m["d"] or 0), "hour": hour, "minute": minute, "what": what})
    return out


def resolve(item: dict, anchor: float) -> float | None:
    """When the item is due (epoch), given when its line was written. None for
    a date that does not exist."""
    try:
        a = datetime.fromtimestamp(anchor).replace(second=0, microsecond=0)
        hm = {"hour": item["hour"], "minute": item["minute"]}
        if not item.get("month"):
            t = a.replace(**hm)
            if t < a:
                t = (a + timedelta(days=1)).replace(**hm)
            return t.timestamp()
        years = [item["year"]] if item.get("year") else [a.year - 1, a.year, a.year + 1]
        found = []
        for y in years:
            try:
                found.append(datetime(y, item["month"], item["day"], **hm))
            except ValueError:
                pass                # 02-30; or 02-29 in that year
        if not found:
            return None
        return min(found, key=lambda t: abs((t - a).total_seconds())).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _read_items(path: Path, now: float) -> tuple[float, list[dict]] | None:
    """(mtime, items) of a handover; None when there is none (an owner that
    replaces its handover may delete it first), or it was written a moment ago
    and may be half-written: looked at next time."""
    try:
        st = path.stat()
    except OSError:
        _PARSED.pop(str(path), None)
        return None
    if 0 <= now - st.st_mtime < SETTLE_S:
        return None
    key = (st.st_mtime, st.st_size)
    had = _PARSED.get(str(path))
    if had and had[0] == key:
        return st.st_mtime, had[1]
    try:
        items = parse(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    _PARSED[str(path)] = (key, items)
    return st.st_mtime, items


# ---------------------------------------------------------------------------
# What was seen and what was delivered
# ---------------------------------------------------------------------------

def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "due.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):       # unreadable, not JSON, or not UTF-8
        d = {}
    d = d if isinstance(d, dict) else {}
    # seen: path -> line -> {anchor, due}; fired: key -> {path, line, due, at, how}
    # A damaged entry is dropped, not raised on at every look.
    return {k: {a: b for a, b in d[k].items() if isinstance(b, dict)}
            if isinstance(d.get(k), dict) else {} for k in ("seen", "fired")}


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save what was delivered: {e}")


def _key(path: str, line: str, due: float) -> str:
    return f"{path}|{int(due)}|{line}"


# ---------------------------------------------------------------------------
# Whose handovers
# ---------------------------------------------------------------------------

def _subjects() -> list[dict]:
    """Every project's PO handover, and every running task owner's. A room is
    not read here (a PO's is long): only when something of it is due."""
    rot = _d.rotation
    out = []
    for p in _d.load_projects():
        rid = (p.get("poRoomId") or "").strip()
        if rid:
            out.append({"kind": "po", "path": rot.handover_path(p), "roomId": rid,
                        "identity": "", "file": rot.HANDOVER_NAME})
    try:
        owners = rot.running_owners()
    except Exception as e:
        _log(f"the running tasks could not be listed: {str(e)[:200]}")
        owners = []
    for gone in [k for k in _OWNER_PATHS if k not in set(owners)]:
        _OWNER_PATHS.pop(gone, None)
    for rid, ident in owners:
        hp = _OWNER_PATHS.get((rid, ident))
        if hp is None:
            room = _d.chatroom.get_room(rid)
            part = _d.chatroom.participant(room, ident) if room else None
            if not part:
                continue
            hp = _OWNER_PATHS[(rid, ident)] = rot.task_handover_path(room, part)
        out.append({"kind": "owner", "path": hp, "roomId": rid, "identity": ident,
                    "file": rot.TASK_HANDOVER_NAME})
    return out


# ---------------------------------------------------------------------------
# Delivering
# ---------------------------------------------------------------------------

def _when(due: float, now: float) -> str:
    t = datetime.fromtimestamp(due)
    same_day = t.date() == datetime.fromtimestamp(now).date()
    return t.strftime("%H:%M" if same_day else "%m-%d %H:%M")


def _listed(items: list[dict], now: float) -> str:
    bits = []
    for it in items:
        what = " ".join(it["what"].split())
        if len(what) > _WHAT_MAX:
            what = what[:_WHAT_MAX] + "…"
        bits.append(f"{_when(it['due'], now)} — {what}")
    return "; ".join(bits)


def wake_line(subj: dict, items: list[dict], now: float) -> tuple[str, list[dict]]:
    """(the typed line, the items it names). As many items, in order, as fit in
    ``_WAKE_MAX``; the first always does (``_WHAT_MAX``). The rest are not
    delivered by this line: they stay due, and go in the next one."""
    tell = (f"tell {_d.operator_name()} why not" if subj["kind"] == "po"
            else "report why not")

    def line(told: list[dict]) -> str:
        one = len(told) == 1
        return (f"{PREFIX}{_listed(told, now)} (from {subj['file']}). "
                f"{'This is' if one else 'These are'} due and you are idle: do "
                f"{'it' if one else 'them'} now, or {tell}.")

    told = items[:1]
    for it in items[1:]:
        if len(line(told + [it])) > _WAKE_MAX:
            break
        told.append(it)
    return line(told)[:_WAKE_MAX], told


def _deliver(subj: dict, items: list[dict], now: float) -> tuple[str, list[dict]]:
    """(how, the items it delivered): ``typed`` (into the idle agent), ``chat``
    (a PO that is not running: a line in its room) or "" (busy, being rotated,
    or not reachable: later). One step under the rotation's gate, like a doorbell: the terminal is read
    afresh, and never one a rotation is replacing."""
    rot, cr = _d.rotation, _d.chatroom
    rid = subj["roomId"]
    with rot.GATE:
        room = cr.get_room(rid)
        if room is None:
            return "", []
        ident = subj["identity"] or cr.po_identity(room)
        part = cr.participant(room, ident) if ident else None
        if not part or rot.is_rotating(rid, ident):
            return "", []
        sess = rot._pty(part)
        if sess is None:
            new = [it for it in items if it.get("how") != "chat"]
            if subj["kind"] != "po" or not new:
                return "", []
            text = (f"**Due: {_listed(new, now)}** (from `{subj['file']}`). The PO is not "
                    f"running, so the hub did not wake it. It is told when it next runs, "
                    f"if that is within a day.")
            posted = cr.post_notice(rid, SENDER, text, {"noticeKind": "due"})
            return ("chat", new) if posted else ("", [])
        # About to be replaced by a fresh session: that one is told instead.
        if rot.awaiting_handover(rid, ident):
            return "", []
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return "", []
        wake, told = wake_line(subj, items, now)
        return ("typed", told) if _d._type_input(sess, wake) else ("", [])


# ---------------------------------------------------------------------------
# One look
# ---------------------------------------------------------------------------

def tick(now: float | None = None) -> list[dict]:
    """Look at every handover once. Returns what was delivered or dropped:
    ``[{path, line, due, how}]`` with how ``typed`` | ``chat`` | ``expired``."""
    with _LOCK:
        return _tick(time.time() if now is None else now)


def _tick(now: float) -> list[dict]:
    state = _load()
    before = json.dumps(state, sort_keys=True)
    seen_all, fired = state["seen"], state["fired"]
    read, out = set(), []
    for subj in _subjects():
        path = str(subj["path"])
        got = _read_items(subj["path"], now)
        if got is None:
            continue
        read.add(path)
        mtime, items = got
        old, seen = seen_all.get(path) or {}, {}
        ready = []
        for it in items:
            rec = old.get(it["line"])
            if not (isinstance(rec, dict) and rec.get("due")):
                anchor = min(mtime, now)
                rec = {"anchor": anchor, "due": resolve(it, anchor)}
            if rec["due"] is None:
                continue
            seen[it["line"]] = rec
            if rec["due"] > now:
                continue
            key = _key(path, it["line"], rec["due"])
            how = (fired.get(key) or {}).get("how", "")
            if how in ("typed", "expired"):
                continue
            if now - rec["due"] > MAX_AGE_S:
                if not how:
                    fired[key] = {"path": path, "line": it["line"], "due": rec["due"],
                                  "at": now, "how": "expired"}
                    out.append({**fired[key]})
                    _log(f"{path}: “{it['line']}” is more than a day past — not delivered")
                continue
            ready.append({**it, "due": rec["due"], "key": key, "how": how})
        if seen:
            seen_all[path] = seen
        else:
            seen_all.pop(path, None)
        if not ready:
            continue
        try:
            how, told = _deliver(subj, ready, now)
        except Exception as e:              # one handover must not stop the others
            _log(f"{path}: not delivered: {str(e)[:200]}")
            how, told = "", []
        for it in told:
            fired[it["key"]] = {"path": path, "line": it["line"], "due": it["due"],
                                "at": now, "how": how}
            out.append({**fired[it["key"]]})
        if told:
            # Written down at once: whatever fails later in this look, a line
            # that was typed is not typed again.
            _save(state)
            before = json.dumps(state, sort_keys=True)
        for it in told:
            _log(f"{path}: “{it['line']}” — "
                 + ("typed into the idle agent" if how == "typed" else "the PO is not running: "
                    "written to its chat"))
    # A handover not read this time — its owner's terminal is away (a rotation
    # between its two sessions, a stopped task), or the file is being written —
    # keeps what is known of its lines: forgotten, an unchanged line would be
    # resolved again from a newer write, a bare time move to tomorrow, and a
    # delivered one be delivered again. Once the file itself is gone, a line
    # is forgotten when it could no longer be delivered.
    for path in [p for p in seen_all if p not in read and not Path(p).exists()]:
        kept = {ln: r for ln, r in seen_all[path].items()
                if isinstance(r, dict) and now - float(r.get("due") or 0) <= MAX_AGE_S}
        if kept:
            seen_all[path] = kept
        else:
            seen_all.pop(path, None)
    # A record outlives its line only while the same line, written again, could
    # still be delivered: past MAX_AGE_S it would be dropped anyway.
    for key in [k for k, r in fired.items()
                if now - float(r.get("due") or 0) > MAX_AGE_S
                and r.get("line") not in (seen_all.get(r.get("path")) or {})]:
        fired.pop(key, None)
    if json.dumps(state, sort_keys=True) != before:
        _save(state)
    return out


def maybe_tick() -> None:
    """Called from the progress check's scheduler loop (every few seconds):
    looks once per ``TICK_S``, the first time a minute after the hub started."""
    global _LAST
    now = time.time()
    if not _LAST:
        _LAST = now
        return
    if now - _LAST >= TICK_S:
        _LAST = now
        tick(now)
