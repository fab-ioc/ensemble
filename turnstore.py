"""The text turns of each agent transcript, kept on disk across restarts (#247).

A PO's chat spans hundreds of transcripts (374 MB for one, measured
2026-10-19), and a hub that has just started read them all again before it
could show the chat: 2-4 s of parsing per open kind.  This keeps what
``dashboard._claude_text_turns`` made of each file, keyed by its path, size
and modification time, and by the parser's own source so a change to the
parser reads every file afresh.  Derived data while the transcript is there:
every failure reads the transcript instead.  Once a transcript is deleted
(Free disk space takes a PO's past conversations, #260), its row is the only
copy of that chat's text, and the hub reads it from here (#265, :func:`kept`):
a row is never deleted with its file.
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
        except sqlite3.OperationalError:
            raise           # busy or locked (another hub writing): parse this time
        except sqlite3.DatabaseError:
            # Not a database (a torn copy, another program's file): it holds
            # nothing that cannot be read again from the transcripts. A file
            # another process has open is left (Windows refuses the unlink,
            # OSError: parsed this time); its WAL goes only with it.
            if not _corrupt(db):
                raise
            # Kept aside, not deleted: it may hold the only text of a deleted transcript.
            db.replace(db.with_name(f"{db.name}.damaged-{int(time.time())}"))
            for p in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
                p.unlink(missing_ok=True)
            conn = _open(db)
        _CONN[key] = conn
    return conn


def _corrupt(db: Path) -> bool:
    """Whether ``db`` is surely no store: not an SQLite file, or one its own
    integrity check fails. A lock or busy error is no proof."""
    try:
        with db.open("rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return True
    except OSError:
        return False
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            return conn.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return False
    except sqlite3.DatabaseError:
        return True


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


def kept(db: Path) -> dict[str, tuple[str, int, int]]:
    """Every stored file by its name without ``.jsonl`` (a session id):
    ``{name: (path, size, mtime_ns)}``. A file deleted since (Free disk
    space, #260) keeps its row: its text is read from here (#265)."""
    out: dict[str, tuple[str, int, int]] = {}
    try:
        with _LOCK:
            rows = _conn(db).execute("SELECT path, size, mtime FROM turns").fetchall()
    except (OSError, sqlite3.Error):
        return out
    for path, size, mtime in rows:
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".jsonl"):
            out[name[:-6]] = (path, size, mtime)
    return out


def has(db: Path, path: str) -> bool:
    """Whether a row for ``path`` is stored."""
    try:
        with _LOCK:
            return _conn(db).execute("SELECT 1 FROM turns WHERE path=?", (path,)).fetchone() is not None
    except (OSError, sqlite3.Error):
        return False


def get_kept(db: Path, path: str) -> list[dict] | None:
    """The turns stored for ``path`` whatever the file and the parser are
    now: for a file that is gone, the best there is."""
    try:
        with _LOCK:
            row = _conn(db).execute("SELECT data FROM turns WHERE path=?", (path,)).fetchone()
        out = json.loads(zlib.decompress(row[0])) if row else None
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
                # Two hubs on one home may store one file: an older reading
                # never replaces a newer one of the same parser.
                conn.execute("INSERT INTO turns(path, size, mtime, ver, data) VALUES (?, ?, ?, ?, ?) "
                             "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                             "ver=excluded.ver, data=excluded.data WHERE turns.ver != excluded.ver "
                             "OR excluded.mtime > turns.mtime "
                             "OR (excluded.mtime = turns.mtime AND excluded.size >= turns.size)",
                             (path, sig[0], sig[1], ver, blob))
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
