"""Persistent provenance for text the hub types into agent terminals.

Agent transcripts have only ``user`` and ``assistant`` roles, so a terminal
write made by the hub is otherwise indistinguishable from text the operator
typed.  Keep a compact, append-only record per room and match transcript user
turns by content hash and time.  Prefix recognition in :mod:`dashboard` stays
as the compatibility path for transcripts written before these records.

The record intentionally does not keep the input text: the transcript already
does.  It stores the fields needed to prove who supplied it and to render it.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

import chatroom

# ``None`` follows chatroom.ROOMS_DIR, which also keeps tests and alternate
# dashboard state roots isolated without another patch point. Tests that need
# to exercise the journal directly may replace this with an explicit path.
RECORDS_DIR: Path | None = None
MATCH_SLACK_S = 10 * 60

_LOCK = threading.RLock()
_CACHE: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_PASTE = re.compile(r"^\x1b\[200~(.*)\x1b\[201~$", re.S)


def normalize(text: str) -> str:
    """The stable form shared by a PTY write and its transcript turn."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    m = _PASTE.match(s)
    if m:
        s = m.group(1)
    return s.strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _safe_room(room_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", room_id or "")


def path_for(room_id: str) -> Path:
    root = RECORDS_DIR if RECORDS_DIR is not None else chatroom.ROOMS_DIR.parent / "input-provenance"
    return root / f"{_safe_room(room_id)}.jsonl"


def signature(room_id: str) -> tuple[int, int]:
    try:
        st = path_for(room_id).stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return 0, 0


def records(room_id: str) -> list[dict]:
    """All valid records for a room, cached while its journal is unchanged."""
    if not room_id:
        return []
    p = path_for(room_id)
    sig = signature(room_id)
    key = str(p)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return [dict(x) for x in hit[1]]
        out: list[dict] = []
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(row, dict) and row.get("hash") and row.get("kind"):
                        out.append(row)
        except OSError:
            pass
        _CACHE[key] = (sig, out)
        return [dict(x) for x in out]


def record(room_id: str, identity: str, text: str, info: dict,
           *, session_id: str = "", at: float | None = None) -> dict | None:
    """Append one successful hub/tool terminal input and return its record."""
    body = normalize(text)
    kind = str((info or {}).get("kind") or "")
    if not room_id or not body or not kind or kind == "human":
        return None
    row = {
        "id": "input-" + uuid.uuid4().hex[:12],
        "at": float(time.time() if at is None else at),
        "room": room_id,
        "identity": identity or "",
        "sessionId": session_id or "",
        "hash": text_hash(body),
        "kind": kind,
        "senderType": str(info.get("senderType") or "hub"),
        "senderId": str(info.get("senderId") or "ensemble"),
        "senderLabel": str(info.get("senderLabel") or "Hub"),
    }
    for key in ("reportKind", "taskTitle", "taskId", "reporter", "fromProject", "poKind"):
        if info.get(key) not in (None, ""):
            row[key] = info[key]
    p = path_for(room_id)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        _CACHE.pop(str(p), None)
    return dict(row)


def index(room_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row in records(room_id):
        out[str(row.get("hash") or "")].append(row)
    return dict(out)


def match(indexed: dict[str, list[dict]], text: str, *, at: float = 0,
          identity: str = "", session_id: str = "") -> dict | None:
    """Closest provenance record for a transcript user turn.

    Time prevents a later genuine operator message with identical words from
    borrowing an old hub record.  Missing transcript timestamps are accepted
    only when the hash identifies one record after identity/session filtering.
    """
    rows = list(indexed.get(text_hash(text), []))
    if identity:
        rows = [r for r in rows if not r.get("identity") or r.get("identity") == identity]
    if session_id:
        rows = [r for r in rows if not r.get("sessionId") or r.get("sessionId") == session_id]
    if not rows:
        return None
    if at:
        rows = [r for r in rows if abs(float(r.get("at") or 0) - at) <= MATCH_SLACK_S]
        if not rows:
            return None
        return dict(min(rows, key=lambda r: abs(float(r.get("at") or 0) - at)))
    return dict(rows[0]) if len(rows) == 1 else None


def assign(indexed: dict[str, list[dict]], turns: list[tuple[int, str, float]], *,
           identity: str = "", session_id: str = "") -> dict[int, dict]:
    """Match records to transcript turns one-to-one, choosing closest times.

    One journal row represents one terminal submission.  This matters when a
    person repeats a Hub input verbatim soon afterwards: the single row must
    attribute only the actual Hub turn, not every equal transcript turn.
    """
    by_hash: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for turn_no, text, at in turns:
        by_hash[text_hash(text)].append((turn_no, at))
    assigned: dict[int, dict] = {}
    for digest, candidates in by_hash.items():
        rows = list(indexed.get(digest, []))
        if identity:
            rows = [r for r in rows if not r.get("identity") or r.get("identity") == identity]
        if session_id:
            rows = [r for r in rows if not r.get("sessionId") or r.get("sessionId") == session_id]
        edges: list[tuple[float, int, int]] = []
        for turn_no, at in candidates:
            if not at:
                continue
            for row_no, row in enumerate(rows):
                distance = abs(float(row.get("at") or 0) - at)
                if distance <= MATCH_SLACK_S:
                    edges.append((distance, turn_no, row_no))
        used_turns: set[int] = set()
        used_rows: set[int] = set()
        for _distance, turn_no, row_no in sorted(edges):
            if turn_no in used_turns or row_no in used_rows:
                continue
            assigned[turn_no] = dict(rows[row_no])
            used_turns.add(turn_no)
            used_rows.add(row_no)
        # A timestamp-less transcript can be attributed only when the filtered
        # hash has exactly one unclaimed turn and one unclaimed journal row.
        undated = [turn_no for turn_no, at in candidates if not at and turn_no not in used_turns]
        spare = [row_no for row_no in range(len(rows)) if row_no not in used_rows]
        if len(undated) == len(spare) == 1:
            assigned[undated[0]] = dict(rows[spare[0]])
    return assigned


def public(record_: dict) -> dict:
    """Fields safe and useful on a transcript turn returned to the page."""
    keep = ("kind", "senderType", "senderId", "senderLabel", "reportKind", "taskTitle",
            "taskId", "reporter", "fromProject", "poKind")
    return {k: record_[k] for k in keep if record_.get(k) not in (None, "")} | {
        "inputId": record_.get("id", ""), "provenance": "record"
    }
