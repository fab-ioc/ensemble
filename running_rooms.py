"""Which rooms' agents were running, kept on disk for a hub that starts cold.

A planned restart writes down who was running just before the stop
(``restart-snapshot.json``); a machine that boots, or a PTY host that died,
leaves no such note, and the hub that starts finds an empty host: before this,
nothing came back until a message launched each room (P190, 10-14: all nine
POs absent after a reboot). This file is that note, kept all the time:

* a room goes in when its agents start (``note_started``) and leaves when it is
  stopped on purpose (``note_stopped``: Stop, a task moved off the board, the
  last terminal of a room ended from the page);
* a heartbeat (``refresh``) adds any room that runs without having passed
  through a start, and keeps what each agent is doing (idle, mid-turn) so a
  cold start types the one restart line only to an agent that was mid-turn.
  It never takes a room out because its terminals vanished all at once: that
  is the machine going down or the host dying, which is exactly what has to be
  brought back. A room whose terminals ended on their own while other rooms
  kept running, seen so on two heartbeats in a row, leaves (as a hub restart
  would not bring it back either); an agent of a team likewise turns stopped.
  A room whose agent ended on its own while no other room ran (``/exit`` in
  the only running task) cannot be told from a crash, so it stays and comes
  back at the next cold start: bringing back one room too many is cheap,
  losing one is what P190 is about. It is not kept for ever: a room not seen
  running for ``MAX_AGE_S`` (a day) leaves, and is not brought back;
* ``boot_entries`` is what a hub that starts with none of them running brings
  back, in the snapshot's shape (``dashboard._bring_back_rooms``).

Written whole to a temp file and renamed, so a crash leaves the old or the new
record, never half of one. Only a serving hub keeps it (``activate``, from
main): a module imported by a test does not. A preflight hub
(ENSEMBLE_PTY_PREFLIGHT) runs on the same state with its own, empty PTY
host: it neither reads nor writes it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()
NAME = "running-rooms.json"
REFRESH_S = 30.0
MAX_AGE_S = 24 * 3600.0     # not seen running for longer: left stopped
SEEN_EVERY_S = 600.0        # how often a running room's "seen" is written down
_LOCK = threading.RLock()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


_ACTIVE = False      # only a serving hub keeps it (main), never an import


def activate() -> None:
    global _ACTIVE
    _ACTIVE = True


def enabled() -> bool:
    return _ACTIVE and not os.environ.get("ENSEMBLE_PTY_PREFLIGHT")


def path() -> Path:
    return Path(_d.DASHBOARD_DIR) / NAME


def read() -> dict:
    try:
        rec = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rooms": {}}
    if not isinstance(rec, dict) or not isinstance(rec.get("rooms"), dict):
        return {"rooms": {}}
    return rec


def exists() -> bool:
    return path().is_file()


def _write(rec: dict) -> None:
    rec["at"] = time.time()
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _live_rooms() -> set[str]:
    return {(i.get("meta") or {}).get("room") for i in _d.ptyrun.list_sessions()
            if i.get("alive")} - {None, ""}


def _merge(old: dict | None, new: dict) -> dict:
    """``new`` (what runs now) over ``old``: an agent whose terminal is gone
    keeps what it was last seen doing, unless the heartbeat saw it stopped
    twice while its room ran (``stoppedSeen``)."""
    before = {a.get("identity"): a for a in (old or {}).get("agents") or []}
    agents = []
    for a in new.get("agents") or []:
        prev = before.get(a.get("identity"))
        if a.get("state") == "stopped" and prev and prev.get("state") != "stopped":
            seen = int(prev.get("stoppedSeen") or 0) + 1
            if seen < 2:
                a = dict(prev, stoppedSeen=seen)
        agents.append(a)
    return {"roomId": new["roomId"], "title": new.get("title", ""),
            "since": (old or {}).get("since") or time.time(), "seen": time.time(),
            "agents": agents}


def _seen(entry: dict) -> float:
    """When the room was last seen running."""
    try:
        return float(entry.get("seen") or entry.get("since") or 0)
    except (TypeError, ValueError):
        return 0.0


def stale(entry: dict, now: float | None = None) -> bool:
    return (time.time() if now is None else now) - _seen(entry) > MAX_AGE_S


def note_started(rid: str) -> None:
    """A room's agents were started (a launch, a resume, a restore)."""
    if not (enabled() and rid):
        return
    try:
        with _LOCK:
            rec = read()
            entry = _d.room_run_entry(rid, statuses={})
            if entry is None:
                return
            # Just started: nobody is mid-turn yet as far as a cold start goes.
            for a in entry["agents"]:
                if a.get("state") != "stopped":
                    a["state"], a["source"] = "idle", "start"
            rec["rooms"][rid] = _merge(rec["rooms"].get(rid), entry)
            _write(rec)
    except Exception as exc:    # noqa: BLE001 — the record never stops a start
        print(f"[running-rooms] {rid}: not recorded as running: {exc!r}", flush=True)


def note_stopped(rid: str) -> None:
    """A room was stopped on purpose: a cold start leaves it stopped."""
    if not (enabled() and rid):
        return
    try:
        with _LOCK:
            rec = read()
            if rec["rooms"].pop(rid, None) is not None:
                _write(rec)
    except Exception as exc:    # noqa: BLE001
        print(f"[running-rooms] {rid}: not recorded as stopped: {exc!r}", flush=True)


def refresh() -> bool:
    """The heartbeat: True when the record changed (and was written)."""
    if not enabled():
        return False
    with _LOCK:
        rec = read()
        rooms = rec["rooms"]
        live = _live_rooms()
        statuses = _d.attention._claude_status_by_session()
        changed = not exists()
        for rid in sorted(live | set(rooms)):
            old = rooms.get(rid)
            if rid not in live:
                # Gone while other rooms ran (twice): ended on its own. Gone
                # with everything else: the host or the machine; kept.
                gone = int((old or {}).get("goneSeen") or 0) + 1 if live else 0
                if gone >= 2 or stale(old or {}):
                    rooms.pop(rid, None)
                    changed = True
                elif gone != int((old or {}).get("goneSeen") or 0):
                    old["goneSeen"] = gone
                    changed = True
                continue
            entry = _d.room_run_entry(rid, statuses)
            if entry is None:
                if old is not None:
                    rooms.pop(rid, None)       # no such room any more
                    changed = True
                continue
            merged = _merge(old, entry)
            if old is None or _comparable(merged) != _comparable(old) \
                    or merged["seen"] - _seen(old) >= SEEN_EVERY_S:
                rooms[rid] = merged
                changed = True
        if changed:
            _write(rec)
        return changed


def _comparable(entry: dict) -> list:
    return [(a.get("identity"), a.get("state"), a.get("ptyId"), a.get("stoppedSeen"),
             a.get("reviewMessageId")) for a in entry.get("agents") or []] + \
        [entry.get("title"), entry.get("goneSeen")]


def boot_entries() -> list[dict]:
    """The recorded rooms seen running within MAX_AGE_S, in the restart
    snapshot's shape."""
    return [dict(e, roomId=rid) for rid, e in sorted(read()["rooms"].items())
            if isinstance(e, dict) and not stale(e)]


def stale_rooms() -> list[str]:
    """The recorded rooms not seen running for over MAX_AGE_S."""
    return [rid for rid, e in sorted(read()["rooms"].items())
            if isinstance(e, dict) and stale(e)]


def start_heartbeat() -> None:
    if not enabled():
        return

    def loop():
        while True:
            time.sleep(REFRESH_S)
            try:
                refresh()
            except Exception as exc:    # noqa: BLE001 — the next beat tries again
                print(f"[running-rooms] heartbeat failed: {exc!r}", flush=True)
    threading.Thread(target=loop, daemon=True, name="running-rooms").start()
