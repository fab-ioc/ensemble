"""What the task list read from each transcript, kept across hub starts.

A fresh hub used to read every held transcript and rollout end to end before
its first task list (90 s and growing, ED-200), because the per-file caches
(costs, a rollout's turns, a transcript's first and last words) lived only in
memory. They are kept here too, on disk, each under its file's path with the
size and modification time it was read at: a start re-reads only the files
that changed since. An answer from here is exactly what reading the file gave.

Only a cache miss in memory comes here (a parse is about to happen anyway), so
a warm poll never touches it. A new fact reaches the disk a few seconds later,
from one background thread, never on the caller's path.

On disk it is a base file and a journal (ED-227). A new fact is a line
appended to the journal; the base is rewritten (compacted) only once a process
and when the journal grows large, on the disk before it is renamed into place,
with the copy it replaces kept as ``.prev``. A power cut used to be able to
leave the one file empty, and the next start then read every transcript again
(8 GB on the hub machine, six minutes on 10-14); now it can at worst lose the
journal's last line.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import durable

# Raised whenever what a parser keeps changes shape or meaning: every fact
# kept by an older hub is then read again, once.
VERSION = 1
SAVE_DELAY_S = 5.0
COMPACT_BYTES = 4 * 1024 * 1024

_LOCK = threading.Lock()
_FILE: Path | None = None
_FACTS: dict[str, list] | None = None   # "kind|path" -> [size, mtime_ns, value]
_PENDING: list[str] = []                # journal lines not written yet
_DIRTY = False                          # anything new since the base was written
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
        _PENDING.clear()


def _journal(path: Path) -> Path:
    return path.with_name(path.name + ".journal")


def _journal_old(path: Path) -> Path:
    return path.with_name(path.name + ".journal.old")


def _replay(path: Path, facts: dict[str, list]) -> None:
    """Apply a journal's lines in order; a line cut off by a power cut, or
    kept by another version, is skipped."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    v, key, size, mtime, value = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if v == VERSION and isinstance(key, str):
                    facts[key] = [size, mtime, value]
    except OSError:
        pass


def _loaded() -> dict[str, list]:
    """The facts, read from disk on first use. Caller holds _LOCK."""
    global _FACTS
    if _FACTS is None:
        facts: dict[str, list] = {}
        d = durable.read_json(_FILE)
        if isinstance(d, dict) and d.get("v") == VERSION and isinstance(d.get("facts"), dict):
            facts = {k: v for k, v in d["facts"].items()
                     if isinstance(v, list) and len(v) == 3}
        # A compaction cut short leaves the journal it set aside: its lines
        # are older than the current journal's, and replayed first.
        _replay(_journal_old(_FILE), facts)
        _replay(_journal(_FILE), facts)
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
        _PENDING.append(json.dumps([VERSION, key, size, mtime, value], separators=(",", ":")))
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
            with _LOCK:
                big = _FILE is not None and _journal_size(_FILE) > COMPACT_BYTES
                first = not _PRUNED
            if big or first:
                flush()
            else:
                _append()
        except Exception as e:      # noqa: BLE001 — a cache that is not written is read again next start
            print(f"[filefacts] not saved: {e!r}", flush=True)
        with _LOCK:
            if not _PENDING:
                _SAVER = None
                return


def _journal_size(path: Path) -> int:
    try:
        return _journal(path).stat().st_size
    except OSError:
        return 0


def _append_locked() -> None:
    """Write the pending lines to the journal, on the disk. Caller holds _LOCK."""
    if not _PENDING or _FILE is None:
        return
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_journal(_FILE), "a", encoding="utf-8") as f:
        f.write("\n".join(_PENDING) + "\n")
        f.flush()
        os.fsync(f.fileno())
    _PENDING.clear()


def _append() -> None:
    with _LOCK:
        _append_locked()


def flush() -> None:
    """Write every fact into the base file now (a compaction), when anything
    changed. Once a process, before its first compaction, the facts of files
    that no longer exist are dropped."""
    global _DIRTY, _PRUNED
    with _LOCK:
        if not _DIRTY or _FACTS is None or _FILE is None:
            return
        path = _FILE
        # The journal is complete, then set aside: the base written below
        # holds all of it, and new facts go to a fresh journal meanwhile.
        _append_locked()
        old, journal = _journal_old(path), _journal(path)
        try:
            if old.exists():
                # One set aside by a compaction that failed: it keeps its
                # lines, and this journal's go after them.
                with open(journal, encoding="utf-8") as src, open(old, "a", encoding="utf-8") as dst:
                    dst.write(src.read())
                    dst.flush()
                    os.fsync(dst.fileno())
                journal.unlink()
            else:
                os.replace(journal, old)
        except FileNotFoundError:
            pass
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
        durable.write_text(path, json.dumps({"v": VERSION, "facts": snap}, separators=(",", ":")),
                           keep_prev=True)
    except Exception:
        with _LOCK:
            if _FILE == path:
                _DIRTY = True       # the saver tries again; the set-aside journal is replayed
        raise
    try:
        old.unlink()
    except OSError:
        pass
