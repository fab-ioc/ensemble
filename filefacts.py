"""What the task list read from each transcript, kept across hub starts.

A fresh hub used to read every held transcript and rollout end to end before
its first task list (90 s and growing, ED-200), because the per-file caches
(costs, a rollout's turns, a transcript's first and last words) lived only in
memory. They are kept here too, on disk, each under its file's path with the
size and modification time it was read at: a start re-reads only the files
that changed since. An answer from here is exactly what reading the file gave.

Only a cache miss in memory comes here (a parse is about to happen anyway), so
a warm poll never touches it. A new fact is written by one background thread
about half a minute later, never on the caller's path; what was not written yet is
read again on the next start.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# Raised whenever what a parser keeps changes shape or meaning: every fact
# kept by an older hub is then read again, once.
VERSION = 1
SAVE_DELAY_S = 30.0

_LOCK = threading.Lock()
_FILE: Path | None = None
_FACTS: dict[str, list] | None = None   # "kind|path" -> [size, mtime_ns, value]
_DIRTY = False
_PRUNED = False
_SAVER: threading.Thread | None = None
_SOON = threading.Event()


def use_file(path: Path | None) -> None:
    """Keep the facts in ``path``; None keeps none (the default: only the hub,
    in its main, names a file, so a test or tool importing the dashboard never
    reads or writes one). Forgets what was loaded."""
    global _FILE, _FACTS, _DIRTY, _PRUNED
    with _LOCK:
        _FILE, _FACTS, _DIRTY, _PRUNED = path, None, False, False


def _loaded() -> dict[str, list]:
    """The facts, read from disk on first use. Caller holds _LOCK."""
    global _FACTS
    if _FACTS is None:
        facts: dict[str, list] = {}
        try:
            with open(_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("v") == VERSION and isinstance(d.get("facts"), dict):
                facts = {k: v for k, v in d["facts"].items()
                         if isinstance(v, list) and len(v) == 3}
        except (OSError, ValueError):
            pass
        _FACTS = facts
    return _FACTS


def sig_of(st: os.stat_result) -> tuple[int, int]:
    return st.st_size, st.st_mtime_ns


def get(kind: str, path, sig: tuple[int, int] | None):
    """What was kept for ``path`` when it had ``sig`` (size, mtime_ns), else
    None. ``sig`` None takes the fact whatever the file is now (for facts a
    file's later growth cannot change)."""
    if _FILE is None:
        return None
    with _LOCK:
        hit = _loaded().get(f"{kind}|{path}")
    if hit is None or (sig is not None and (hit[0], hit[1]) != tuple(sig)):
        return None
    return hit[2]


def put(kind: str, path, sig: tuple[int, int] | None, value) -> None:
    """Keep ``value`` for ``path`` at ``sig``; written to disk shortly."""
    global _DIRTY, _SAVER
    if _FILE is None:
        return
    size, mtime = sig if sig is not None else (-1, -1)
    with _LOCK:
        facts = _loaded()
        key = f"{kind}|{path}"
        old = facts.get(key)
        if old is not None and old[0] == size and old[1] == mtime and old[2] == value:
            return
        facts[key] = [size, mtime, value]
        _DIRTY = True
        # One saver at a time: a put while one waits rides on its write.
        if _SAVER is None:
            _SAVER = threading.Thread(target=_save_later, name="filefacts-save", daemon=True)
            _SAVER.start()


def save_soon() -> None:
    """Have the waiting saver write now rather than at the end of its wait."""
    _SOON.set()


def _save_later() -> None:
    global _SAVER
    while True:
        _SOON.wait(SAVE_DELAY_S)
        _SOON.clear()
        try:
            flush()
        except Exception as e:      # noqa: BLE001 — a cache that is not written is read again next start
            print(f"[filefacts] not saved: {e!r}", flush=True)
        with _LOCK:
            if not _DIRTY:
                _SAVER = None
                return


def flush() -> None:
    """Write the facts now, when anything changed. Once a process, before its
    first write, the facts of files that no longer exist are dropped."""
    global _DIRTY, _PRUNED
    with _LOCK:
        if not _DIRTY or _FACTS is None or _FILE is None:
            return
        path = _FILE
        snap = dict(_FACTS)
        _DIRTY = False
        prune = not _PRUNED
        _PRUNED = True
    if prune:
        gone = [k for k in snap if not os.path.exists(k.split("|", 1)[1])]
        if gone:
            with _LOCK:
                for k in gone:
                    if _FACTS is not None:
                        _FACTS.pop(k, None)
                    snap.pop(k, None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": VERSION, "facts": snap}, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        with _LOCK:
            if _FILE == path:
                _DIRTY = True       # the saver tries again
        raise
