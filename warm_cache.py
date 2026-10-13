"""Restart snapshots for the expensive first session and PO thread reads.

The snapshot is only accepted when every source file and parent directory has
the same size and nanosecond mtime. Parent directories catch new or removed
files; individual files catch appended transcripts and edited room records.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FORMAT = 2


def _codex_home(home: Path) -> Path:
    return Path(os.environ.get("CODEX_HOME") or home / ".codex").resolve()


def _sources(home: Path, state: Path):
    codex_home = _codex_home(home)
    roots = [home / ".claude" / "projects", home / ".claude" / "sessions",
             home / ".codex" / "sessions", codex_home / "sessions",
             state / "rooms", state / "agents",
             state / "points", state / "threads"]
    files = [state / name for name in ("projects.json", "labels.json",
                                       "session_projects.json", "settings.json")]
    return roots, files


def _fingerprint(home: Path, state: Path) -> dict[str, list[int] | None]:
    roots, extras = _sources(home, state)
    found = {}
    for root in roots:
        if not root.exists():
            found[str(root)] = None
            continue
        for current, dirs, files in os.walk(root):
            base = Path(current)
            try:
                st = base.stat()
                found[str(base)] = [st.st_size, st.st_mtime_ns]
            except OSError:
                found[str(base)] = None
            for name in files:
                path = base / name
                try:
                    st = path.stat()
                    found[str(path)] = [st.st_size, st.st_mtime_ns]
                except OSError:
                    found[str(path)] = None
    for path in extras:
        try:
            st = path.stat()
            found[str(path)] = [st.st_size, st.st_mtime_ns]
        except OSError:
            found[str(path)] = None
    return found


def _encode(value):
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(x) for x in value]}
    if isinstance(value, set):
        return {"__set__": [_encode(x) for x in value]}
    if isinstance(value, Path):
        return {"__path__": str(value)}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(x) for x in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        if "__tuple__" in value:
            return tuple(_decode(x) for x in value["__tuple__"])
        if "__set__" in value:
            return set(_decode(x) for x in value["__set__"])
        if "__path__" in value:
            return Path(value["__path__"])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(x) for x in value]
    return value


def path(state: Path) -> Path:
    return state / "cache" / "restart-warm.json"


def save(home: Path, state: Path, sessions: dict, transcript_index: dict,
         thread_indexes: dict) -> None:
    target = path(state)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {"format": FORMAT, "codexHome": str(_codex_home(home)),
            "sources": _fingerprint(home, state),
            "sessions": _encode(sessions),
            "transcripts": _encode(transcript_index),
            "threads": _encode(thread_indexes)}
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)


def load(home: Path, state: Path) -> dict | None:
    try:
        data = json.loads(path(state).read_text(encoding="utf-8"))
        if data.get("format") != FORMAT or data.get("codexHome") != str(_codex_home(home)):
            return None
        for name, expected in data["sources"].items():
            try:
                st = Path(name).stat()
                actual = [st.st_size, st.st_mtime_ns]
            except OSError:
                actual = None
            if actual != expected:
                return None
        return {k: _decode(data[k]) for k in ("sessions", "transcripts", "threads")}
    except (OSError, ValueError, KeyError, TypeError):
        return None
