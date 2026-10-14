"""The hub's expensive first reads, kept for the next hub (ED-227).

The task list rows, the transcript index and the PO thread builds are saved
from memory every few minutes while they change, and when a restart is asked
for. The next hub takes them as a first answer, however old: the rows are
shown at once while the hub reads the list again, a transcript found through
the index is checked to exist, and each thread build carries the signature
of what it was built from, which threads.build compares before using it. So
a hub that crashed, or a machine that lost power, starts from the last save
instead of from nothing. Rows made by different Python code are refused: they
may lack fields the new code expects.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import durable

FORMAT = 4
_CODE_VERSION = ""


def _code_version() -> str:
    """The Python code this process runs, read once: a merge into the
    checkout under a running hub does not change the code that made its rows."""
    global _CODE_VERSION
    if _CODE_VERSION:
        return _CODE_VERSION
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    paths = sorted(p for p in root.rglob("*.py")
                   if not any(part in {"tests", ".venv", "build", "dist", "__pycache__"}
                              for part in p.relative_to(root).parts))
    for file in paths:
        digest.update(str(file.relative_to(root)).encode("utf-8"))
        digest.update(file.read_bytes().replace(b"\r\n", b"\n"))
    _CODE_VERSION = digest.hexdigest()[:16]
    return _CODE_VERSION


def _codex_home(home: Path) -> Path:
    return Path(os.environ.get("CODEX_HOME") or home / ".codex").resolve()


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
    """On the disk before the rename; the copy it replaces is kept as
    ``.prev`` for a save that a power cut tore."""
    body = {"format": FORMAT, "codexHome": str(_codex_home(home)),
            "codeVersion": _code_version(),
            "sessions": _encode(sessions),
            "transcripts": _encode(transcript_index),
            "threads": _encode(thread_indexes)}
    durable.write_text(path(state), json.dumps(body, separators=(",", ":")), keep_prev=True)


def load(home: Path, state: Path) -> dict | None:
    data = durable.read_json(path(state))
    try:
        if (not isinstance(data, dict) or data.get("format") != FORMAT or
                data.get("codexHome") != str(_codex_home(home)) or
                data.get("codeVersion") != _code_version()):
            return None
        return {k: _decode(data[k]) for k in ("sessions", "transcripts", "threads")}
    except (KeyError, TypeError, ValueError):
        return None
