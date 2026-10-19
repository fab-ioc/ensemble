"""The text turns of each agent transcript, kept on disk across restarts (#247).

A PO's chat spans hundreds of transcripts (374 MB for one, measured
2026-10-19), and a hub that has just started read them all again before it
could show the chat: 2-4 s of parsing per open kind.  This keeps what
``dashboard._claude_text_turns`` made of each file, keyed by its path, size
and modification time, and by the parser's own source so a change to the
parser reads every file afresh.  Derived data: the database may be deleted at
any time, and every failure reads the transcript instead.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path

_LOCK = threading.Lock()
_CONN: dict[str, sqlite3.Connection] = {}
_LAST_PUT: dict[str, float] = {}
# A live transcript grows every few seconds: store it at most this often (a
# cold read then parses that one file again, not all of them).
PUT_EVERY = 30.0


def version_of(*sources: str) -> str:
    return hashlib.sha1("\0".join(sources).encode("utf-8")).hexdigest()[:16]


def _conn(db: Path) -> sqlite3.Connection:
    key = str(db)
    conn = _CONN.get(key)
    if conn is None:
        db.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = _open(db)
        except sqlite3.DatabaseError:
            # Not a database (a torn copy, another program's file): it holds
            # nothing that cannot be read again from the transcripts.
            for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
                p.unlink(missing_ok=True)
            conn = _open(db)
        _CONN[key] = conn
    return conn


def _open(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=2, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("CREATE TABLE IF NOT EXISTS turns (path TEXT PRIMARY KEY, size INTEGER, "
                     "mtime INTEGER, ver TEXT, data BLOB)")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def get(db: Path, path: str, sig: tuple[int, int], ver: str) -> list[dict] | None:
    """The turns stored for ``path`` at ``sig`` by parser ``ver``, or None."""
    try:
        with _LOCK:
            row = _conn(db).execute("SELECT size, mtime, ver, data FROM turns WHERE path=?",
                                    (path,)).fetchone()
        if not row or (row[0], row[1]) != tuple(sig) or row[2] != ver:
            return None
        out = json.loads(zlib.decompress(row[3]))
        return out if isinstance(out, list) else None
    except (OSError, sqlite3.Error, zlib.error, ValueError):
        return None


def put(db: Path, path: str, sig: tuple[int, int], ver: str, turns: list[dict],
        *, force: bool = False) -> bool:
    """Store ``turns``; skipped (False) for a file stored under PUT_EVERY ago."""
    now = time.monotonic()
    if not force and now - _LAST_PUT.get(path, -PUT_EVERY) < PUT_EVERY:
        return False
    try:
        blob = zlib.compress(json.dumps(turns, ensure_ascii=False, separators=(",", ":"))
                             .encode("utf-8"), 3)
        with _LOCK:
            conn = _conn(db)
            with conn:
                conn.execute("INSERT OR REPLACE INTO turns(path, size, mtime, ver, data) "
                             "VALUES (?, ?, ?, ?, ?)", (path, sig[0], sig[1], ver, blob))
            _LAST_PUT[path] = now
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


def close() -> None:
    with _LOCK:
        for conn in _CONN.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _CONN.clear()
        _LAST_PUT.clear()
