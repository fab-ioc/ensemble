"""Persistent provenance for text submitted to dashboard-owned agent terminals.

Agent transcripts have only ``user`` and ``assistant`` roles, so a terminal
write made by the hub is otherwise indistinguishable from text the operator
typed. Keep a compact, append-only record of both origins per room and match transcript user
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


_PUBLIC_FIELDS = ("kind", "senderType", "senderId", "senderLabel", "reportKind",
                  "taskTitle", "taskId", "reporter", "fromProject", "fromProjectName",
                  "poKind")


def _metadata(info: dict) -> dict:
    return {key: info[key] for key in _PUBLIC_FIELDS if info.get(key) not in (None, "")}


def record(room_id: str, identity: str, text: str, info: dict,
           *, session_id: str = "", at: float | None = None,
           parts: list[tuple[str, dict]] | None = None) -> dict | None:
    """Append one successful terminal submission and return its record.

    ``parts`` describes logical inputs that the hub deliberately submitted as
    one PTY turn (for example a resume note plus queued messages). Only each
    normalized part's hash and character count are stored, never its text.
    """
    body = normalize(text)
    kind = str((info or {}).get("kind") or "")
    if not room_id or not body or not kind:
        return None
    row = {
        "id": "input-" + uuid.uuid4().hex[:12],
        "at": float(time.time() if at is None else at),
        "room": room_id,
        "identity": identity or "",
        "sessionId": session_id or "",
        "hash": text_hash(body),
        "kind": kind,
        "senderType": str(info.get("senderType") or ("person" if kind == "human" else "hub")),
        "senderId": str(info.get("senderId") or ("user" if kind == "human" else "ensemble")),
        "senderLabel": str(info.get("senderLabel") or ("you" if kind == "human" else "Hub")),
    }
    row.update(_metadata(info))
    if parts:
        encoded = []
        for part_text, part_info in parts:
            part_body = normalize(part_text)
            if not part_body or not part_info.get("kind"):
                continue
            encoded.append({"hash": text_hash(part_body), "chars": len(part_body),
                            **_metadata(part_info)})
        if encoded:
            row["parts"] = encoded
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
    """Match records to transcript turns one-to-one in submission order.

    New browser/person submissions are journaled too. Thus equal text has one
    row per physical submission, and journal order is the causally reliable
    signal even when the transcript timestamp races the post-write journal.
    If eligible row/turn counts differ, the hash is ambiguous and none is
    attributed: a lone Hub row can never be borrowed by a later identical
    unrecorded person turn.
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
        eligible_turns = [
            (turn_no, at) for turn_no, at in candidates
            if not at or any(abs(float(row.get("at") or 0) - at) <= MATCH_SLACK_S for row in rows)
        ]
        eligible_rows = [
            row for row in rows
            if not candidates or any(not at or abs(float(row.get("at") or 0) - at) <= MATCH_SLACK_S
                                     for _turn_no, at in candidates)
        ]
        if len(eligible_turns) != len(eligible_rows):
            continue
        for (turn_no, at), row in zip(eligible_turns, eligible_rows):
            if at and abs(float(row.get("at") or 0) - at) > MATCH_SLACK_S:
                break
        else:
            assigned.update({turn_no: dict(row)
                             for (turn_no, _at), row in zip(eligible_turns, eligible_rows)})
    return assigned


def split_parts(record_: dict, text: str) -> list[tuple[str, dict]] | None:
    """Recover and verify a composed record's logical parts from transcript text."""
    parts = record_.get("parts")
    if not isinstance(parts, list) or not parts:
        return None
    body = normalize(text)
    at = 0
    out: list[tuple[str, dict]] = []
    for i, part in enumerate(parts):
        try:
            chars = int(part.get("chars") or 0)
        except (TypeError, ValueError):
            return None
        piece = body[at:at + chars]
        if chars <= 0 or len(piece) != chars or text_hash(piece) != part.get("hash"):
            return None
        out.append((piece, dict(part)))
        at += chars
        if i + 1 < len(parts):
            if body[at:at + 2] != "\n\n":
                return None
            at += 2
    return out if at == len(body) else None


def public(record_: dict) -> dict:
    """Fields safe and useful on a transcript turn returned to the page."""
    meta = _metadata(record_)
    if meta.get("kind") == "human":
        for key in ("senderType", "senderId", "senderLabel"):
            meta.pop(key, None)
    return meta | {
        "inputId": record_.get("id", ""), "provenance": "record"
    }
