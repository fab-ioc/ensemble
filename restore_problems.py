"""The rooms a restart or an update could not bring back, and why.

A room that was running before a restart (or an Update now) is brought back by
the hub that comes back (``dashboard._bring_back_rooms``). When that fails for
one, the person must not have to find it in restart.log: the room gets a Needs
you item saying why (``by_room``, read by attention) and its project's PO chat
one report (``dashboard._tell_pos_not_back``, once per room and restart). The item clears as soon as
the room runs again (``clear``, from attention), or when the room is gone.

Kept in ``restore-problems.json`` in the state dir, so a hub that restarts again
before anyone looked still shows it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()

_LOCK = threading.Lock()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


def _path() -> Path:
    return _d.DASHBOARD_DIR / "restore-problems.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def po_room(rid: str) -> str:
    """The PO chat of the room's project ("" when it has none, or it is one)."""
    projects = {p["id"]: p for p in _d.load_projects()}
    if rid in {p.get("poRoomId") for p in projects.values()}:
        return ""
    room = _d.chatroom.get_room(rid, public=False)
    pid = _d.ensemble_tools._project_of_room(room) if room else ""
    return (projects.get(pid) or {}).get("poRoomId") or ""


def record(rid: str, title: str, why: str, what: str = "restart", lease: str = "") -> bool:
    """Note that ``what`` (``restart`` | ``update``) did not bring room ``rid``
    back, because ``why``. True the first time for this room and lease: its
    PO is to be told (``dashboard._tell_pos_not_back``), once."""
    if not rid:
        return False
    with _LOCK:
        data = _read()
        prior = data.get(rid) or {}
        same = prior.get("lease") == lease
        data[rid] = {"title": title, "why": why, "what": what, "lease": lease,
                     "since": prior.get("since") if same and prior.get("since") else time.time()}
        _write(data)
    return not (same and prior)


def clear(rid: str) -> bool:
    """The room runs again (or is gone): its item goes. True when there was one."""
    with _LOCK:
        data = _read()
        if rid not in data:
            return False
        data.pop(rid)
        _write(data)
    return True


def by_room() -> dict[str, dict]:
    """{room: {reason, since, why, what}}: each room's Needs you item."""
    with _LOCK:
        data = _read()
    return {rid: {"reason": f"The {e.get('what') or 'restart'} could not bring this task back: "
                            f"{e.get('why') or 'unknown'}. Resume it when you want it running.",
                  "since": float(e.get("since") or 0), "why": e.get("why") or "",
                  "what": e.get("what") or "restart"}
            for rid, e in data.items() if isinstance(e, dict)}
