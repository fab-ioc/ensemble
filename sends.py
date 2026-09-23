"""The person's sends: every message the hub takes from a chat's box is kept
here, by its key, until the agent's conversation shows it.

The CEO's long message was accepted, its balloon went, and it came back
minutes later when Codex finally read it (2026-09-23): the page alone had
kept it, and dropped it after two minutes or at the first new row. A send to a
stopped task once answered ok and did nothing at all. So the hub, not the
page, holds what was sent, and every copy of the chat (and a reload) shows it
until it is really in.

**A send** has the key the page gave it (``send:…``; one the hub mints when
none was given), the text as the hub delivers it (its ``[point Pn]`` and
``[image]`` lines included), and a state:

* ``queued``: taken, held by the hub until the agent is up (a resume);
* ``delivered``: typed into the agent's terminal; its conversation does not
  show it yet (a busy agent reads it when its turn ends);
* ``failed``: not delivered, with the reason; Retry sends it again, Discard
  drops it;
* ``confirmed``: the conversation shows it (``mid`` is the balloon's id): a
  one-agent chat's transcript has the turn, a team's room has the message.

Nothing but those events moves a send: no clock, no count of rows. A send
typed into a terminal whose agent then stopped without reading it becomes
``failed`` ("the session stopped before it read this"), never gone.

A ``/command`` is typed but never shows as a turn: it is confirmed once typed.

**Storage.** ``sends/<room>.json`` beside the rooms folder, written
atomically, like the points. A send queued by a hub that is no longer running
(the queue lived in that hub's memory) is ``failed`` with Retry. Confirmed
sends are kept a day, so a key sent again after a lost reply is still known
and taken once.

Bound to the dashboard module like ``points``: nothing reads ``_d`` at import
time.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


STATES = ("queued", "delivered", "failed", "confirmed")
BOOT = uuid.uuid4().hex[:12]    # this hub process: a queue lives in its memory
KEEP_S = 24 * 3600              # a confirmed send is remembered this long (its key)
SHOW_CONFIRMED_S = 120          # and shown this long, until the page's own copy has it
STOPPED_AFTER_S = 30            # delivered, the agent gone this long after: not read
SLACK_S = 120                   # a transcript's clock and the hub's may differ this much
SYNC_EVERY_S = 2.0              # a room's conversation is looked at at most this often
_NEEDLE = 400                   # the first words of a send that must be in its turn

_LOCK = threading.RLock()
_SYNCED: dict[str, float] = {}
_SCANNED: dict[tuple, tuple] = {}   # (room, session) -> (its stat, since, its user turns, when read)


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] sends: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_ROOM_ID = re.compile(r"room-[0-9a-f]+")


def _dir() -> Path:
    return Path(_d.chatroom.ROOMS_DIR).parent / "sends"


def _path(room_id: str) -> Path:
    if not _ROOM_ID.fullmatch(room_id or ""):
        raise ValueError(f"not a room id: {room_id!r}")
    return _dir() / f"{room_id}.json"


def _load(room_id: str) -> list[dict]:
    try:
        d = json.loads(_path(room_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("sends") if isinstance(d, dict) else None
    return [s for s in items or [] if isinstance(s, dict) and s.get("key") and s.get("state") in STATES]


def _save(room_id: str, items: list[dict]) -> None:
    p = _path(room_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    body = json.dumps({"version": 1, "roomId": room_id, "sends": items}, indent=1, ensure_ascii=True)
    try:
        tmp.write_text(body, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, p)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _prune(items: list[dict], now: float) -> list[dict]:
    return [s for s in items if not (s["state"] == "confirmed"
                                     and now - float(s.get("stateAt") or 0) > KEEP_S)]


def _find(items: list[dict], key: str) -> dict | None:
    return next((s for s in items if s.get("key") == key), None)


def _set(s: dict, state: str, now: float, error: str = "") -> None:
    s["state"] = state
    s["stateAt"] = now
    s["error"] = error if state == "failed" else ""
    s["boot"] = BOOT


# ---------------------------------------------------------------------------
# What the hub does with a send
# ---------------------------------------------------------------------------

def new_key() -> str:
    return "hub:" + uuid.uuid4().hex[:16]


def get(room_id: str, key: str) -> dict | None:
    if not key or not _ROOM_ID.fullmatch(room_id or ""):
        return None
    with _LOCK:
        s = _find(_load(room_id), key)
        return dict(s) if s else None


def accept(room_id: str, key: str, text: str, to: str = "", now: float | None = None) -> dict:
    """The hub has taken ``text`` (as it will be delivered) under ``key``:
    queued. The same key again is the same send: kept as it is, except that
    one that failed is queued again (its retry)."""
    now = time.time() if now is None else now
    with _LOCK:
        items = _prune(_load(room_id), now)
        s = _find(items, key)
        if s is None:
            # The whole text: Retry sends it again as it is.
            s = {"key": key, "text": text or "", "to": to or "", "at": now,
                 "state": "queued", "stateAt": now, "error": "", "boot": BOOT}
            items.append(s)
        elif s["state"] == "failed":
            _set(s, "queued", now)
        else:
            return dict(s)
        try:
            _save(room_id, items)
        except OSError as e:    # the send itself goes ahead; only its receipt is missing
            _log(f"{room_id}: send {key} not recorded: {e!r}")
        return dict(s)


def mark(room_id: str, keys, state: str, error: str = "", mid: str = "",
         now: float | None = None) -> None:
    """Move the sends with these keys (unknown keys are skipped: a hub line,
    the PO's message). A confirmed send stays confirmed."""
    keys = [k for k in (keys or []) if k]
    if not keys or state not in STATES or not _ROOM_ID.fullmatch(room_id or ""):
        return
    now = time.time() if now is None else now
    try:
        with _LOCK:
            items = _load(room_id)
            changed = False
            for s in items:
                if s["key"] not in keys or s["state"] == "confirmed":
                    continue
                if state == "delivered" and s["state"] == "delivered":
                    continue
                _set(s, state, now, error)
                if state == "delivered":
                    s["deliveredAt"] = now
                if mid:
                    s["mid"] = mid
                if state == "confirmed" and not s.get("confirmedAt"):
                    s["confirmedAt"] = now
                changed = True
            if changed:
                _save(room_id, items)
    except (OSError, ValueError) as e:     # the delivery itself stands
        _log(f"{room_id}: sends not marked {state}: {e!r}")


def drop(room_id: str, keys) -> list[dict]:
    """Take these sends away (Discard). Returns what was dropped."""
    keys = set(k for k in (keys or []) if k)
    if not keys or not _ROOM_ID.fullmatch(room_id or ""):
        return []
    with _LOCK:
        items = _load(room_id)
        gone = [s for s in items if s["key"] in keys]
        if gone:
            _save(room_id, [s for s in items if s["key"] not in keys])
        return gone


def orphans(room_id: str, after_s: float, now: float | None = None) -> list[str]:
    """The keys of sends queued by this hub more than ``after_s`` ago: the
    caller knows whether anything is still on its way to deliver them."""
    now = time.time() if now is None else now
    if not _ROOM_ID.fullmatch(room_id or ""):
        return []
    with _LOCK:
        return [s["key"] for s in _load(room_id) if s["state"] == "queued" and s.get("boot") == BOOT
                and now - float(s.get("stateAt") or s["at"]) > after_s]


def typed_confirms(text: str) -> bool:
    """A send that never shows as a turn of its own once typed: an agent's
    ``/command``."""
    return (text or "").lstrip().startswith("/")


# ---------------------------------------------------------------------------
# Finding a send in the conversation
# ---------------------------------------------------------------------------

_IMAGE_LINE = re.compile(r"^\s*\[image\][^\n]*$", re.M | re.I)
_IMAGE_TOKEN = re.compile(r"\[Image(?: #\d+|: source: [^\]]*)\]")
_POINT_LINE = re.compile(r"\[point (P\d{1,5}[a-z]?)\]")


def _norm(text: str) -> str:
    try:
        text = _d.message_refs.strip_message_refs(text or "")
    except Exception:       # noqa: BLE001 — compared as it is
        text = text or ""
    text = _IMAGE_TOKEN.sub(" ", _IMAGE_LINE.sub(" ", text))
    return " ".join(text.split())


_IMAGE_PATH = re.compile(r"^\s*\[image\][ \t]+(\S[^\n]*?)\s*$", re.M | re.I)


def _image_paths(text: str) -> list[str]:
    return [p.replace("\\", "/").lower() for p in _IMAGE_PATH.findall(text or "")]


class Evidence:
    """What of a send its turn holds: its point ids, its first words (``cut``:
    the send has more) and its images' paths. A turn must have the first of
    them it has at all (points: unique to a send, else words, else images);
    the rest are claimed with it where the turn has them, so no other send
    confirms itself by this one's image or words."""

    def __init__(self, send_text: str):
        self.points = _POINT_LINE.findall(send_text or "")
        words = _norm(send_text)
        self.words, self.cut = words[:_NEEDLE], len(words) > _NEEDLE
        self.images = _image_paths(send_text)
        self.kind = "points" if self.points else "words" if self.words else "images" if self.images else ""

    def order(self) -> tuple[int, int]:
        """Points first (they can only be this send's), then the fullest."""
        rank = ("points", "words", "images", "").index(self.kind)
        size = {"points": len(self.points), "words": len(self.words), "images": len(self.images)}.get(self.kind, 0)
        return rank, -size


def evidence(send_text: str) -> Evidence:
    return Evidence(send_text)


class _Turn:
    """What of one user turn is still unclaimed. One turn may confirm several
    sends (a resume types them in as one input), each by a part of it no
    other send has: words at their own place, as whole words, each image
    path and point occurrence once."""

    def __init__(self, text: str):
        self.words = _norm(text)
        self.spans: list[tuple[int, int]] = []
        self.images = _image_paths(text)
        self.points = _POINT_LINE.findall(text or "")

    @staticmethod
    def _less(pool: list, want: list, partial: bool = False) -> list | None:
        """``pool`` less ``want`` (each occurrence once); None when it lacks
        one, unless ``partial``: then less those it has."""
        left = list(pool)
        for x in want:
            if x in left:
                left.remove(x)
            elif not partial:
                return None
        return left

    def _span(self, ev: Evidence) -> tuple[int, int] | None:
        w, val = self.words, ev.words
        i = w.find(val) if val else -1
        while i >= 0:
            j = i + len(val)
            if ((i == 0 or w[i - 1] == " ") and (j == len(w) or w[j] == " " or ev.cut)
                    and not any(i < b and a < j for a, b in self.spans)):
                return i, j
            i = w.find(val, i + 1)
        return None

    def take(self, ev: Evidence) -> bool:
        points = self._less(self.points, ev.points)
        span = self._span(ev)
        images = self._less(self.images, ev.images, partial=ev.kind != "images")
        if {"points": points, "words": span, "images": images}.get(ev.kind) is None:
            return False
        self.points = points
        if span is not None:
            self.spans.append(span)
        self.images = images
        return True


def by_size(sends_: list[dict]) -> list[dict]:
    """The order sends claim turns in: point sends first, then the fullest
    evidence (so "go" cannot take the words of "go now" from it), then the
    oldest."""
    return sorted(sends_, key=lambda s: (*evidence(s["text"]).order(), s["at"]))


def matches(send_text: str, turn_text: str) -> bool:
    """Whether a user turn holds this send: its point lines, when it has any
    (unique in a room), else its first words, as the agent was given them,
    else (images alone) the images' paths."""
    return _Turn(turn_text).take(evidence(send_text))


def _session_ids(room: dict) -> list[str]:
    out: list[str] = []
    for part in room.get("participants", []):
        if part.get("kind") != "agent":
            continue
        for s in [part.get("sessionId")] + [x for r in part.get("rotations") or [] if isinstance(r, dict)
                                            for x in (r.get("toSessionId"), r.get("fromSessionId"))]:
            s = (s or "").strip()
            if s and s not in out:
                out.append(s)
    return out


def _solo(room: dict) -> bool:
    agents = [p for p in room.get("participants", []) if p.get("kind") == "agent"]
    return room.get("mode") == "solo" or len(agents) < 2


def unseen(text: str) -> bool:
    """A send its transcript never shows as a turn of its own: the reader
    skips a user turn that starts with "<" or "Caveat:" (Claude Code's own
    lines). It stays delivered (Dismiss hides it), never failed: Retry would
    type in again what the agent read."""
    t = (text or "").lstrip()
    return t.startswith("<") or t.startswith("Caveat:")


RESCAN_UNKNOWN_S = 30           # a transcript whose size and time cannot be read: read again this often


def _user_turns(room_id: str, sid: str, since: float, now: float) -> list[tuple[str, dict]]:
    """A session's user turns from ``since`` on, read again only when its
    transcript changed (a PO's is megabytes, and the page polls every two
    seconds); one whose stat cannot be had, every RESCAN_UNKNOWN_S. A session
    not written since ``since`` holds none."""
    st = _d.points._session_stat(sid)
    if st is not None and float(st[1]) < since:
        return []
    seen = _SCANNED.get((room_id, sid))
    if seen is not None and seen[1] <= since and (
            seen[0] == st if st is not None else seen[0] is None and now - seen[3] < RESCAN_UNKNOWN_S):
        return seen[2]
    try:
        raw = _d.read_session_turns(sid) or []
    except Exception as e:     # noqa: BLE001 — looked at again next time
        _log(f"{room_id}: transcript {sid} not read: {e!r}")
        return []
    turns = [(mid, t) for mid, t in _d.page_turn_ids(sid, raw)
             if t.get("role") == "user" and (_d._turn_epoch(t.get("timestamp")) or now) >= since]
    _SCANNED[(room_id, sid)] = (st, since, turns, now)
    return turns


def sync(room_id: str, room: dict | None = None, force: bool = False,
         now: float | None = None) -> None:
    """Look for the delivered sends of a one-agent chat in its transcript:
    each is confirmed by the first user turn after it was taken that holds
    it, by a part of it no other send has. One still not there once its
    agent has stopped for a while failed: it was never read. Matched on a
    copy, outside the lock; what it finds is applied under it to the sends
    still delivered."""
    now = time.time() if now is None else now
    if not force and now - _SYNCED.get(room_id, 0) < SYNC_EVERY_S:
        return
    _SYNCED[room_id] = now
    with _LOCK:
        items = [dict(s) for s in _load(room_id)]
    waiting = [s for s in items if s["state"] == "delivered"]
    if not waiting:
        return
    if room is None:
        room = _d.chatroom.get_room(room_id)
    if room is None or not _solo(room):
        return
    since = min(float(s["at"]) for s in waiting) - SLACK_S
    turns: list[tuple[str, dict]] = []
    for sid in _session_ids(room):
        turns += _user_turns(room_id, sid, since, now)
    live = _d._room_is_live(room)
    # What of each turn the sends it confirmed already have not claimed:
    # one turn may confirm several sends (a resume types them in as one
    # input), each by its own part of it.
    left = {mid: _Turn(t.get("text") or "") for mid, t in turns}
    for s in by_size([s for s in items if s.get("mid") in left]):
        left[s["mid"]].take(evidence(s["text"]))
    hits: dict[str, str] = {}
    gone: set[str] = set()
    for s in by_size(waiting):
        floor = float(s["at"]) - SLACK_S
        ev = evidence(s["text"])
        hit = next((mid for mid, t in turns
                    if (_d._turn_epoch(t.get("timestamp")) or now) >= floor
                    and left[mid].take(ev)), None)
        if hit:
            hits[s["key"]] = hit
        elif (not live and not unseen(s["text"])
              and now - float(s.get("deliveredAt") or s["stateAt"]) > STOPPED_AFTER_S):
            gone.add(s["key"])
    if not hits and not gone:
        return
    with _LOCK:
        items = _load(room_id)
        changed = False
        for s in items:
            if s["state"] != "delivered":
                continue
            if s["key"] in hits:
                _set(s, "confirmed", now)
                s["mid"], s["confirmedAt"] = hits[s["key"]], now
                changed = True
            elif s["key"] in gone:
                _set(s, "failed", now, "the session stopped before it read this")
                changed = True
        if changed:
            _save(room_id, items)


def reconcile(room_id: str, now: float | None = None) -> list[dict]:
    """A room's sends, with those queued by a hub that has since stopped
    failed: their queue went with that hub. Before anything reads a send's
    state to act on it (the page's poll, a send of the same key again)."""
    now = time.time() if now is None else now
    if not _ROOM_ID.fullmatch(room_id or ""):
        return []
    with _LOCK:
        items = _load(room_id)
        changed = False
        for s in items:
            if s["state"] == "queued" and s.get("boot") != BOOT:
                _set(s, "failed", now, "the hub restarted before it was delivered")
                changed = True
        if changed:
            try:
                _save(room_id, items)
            except OSError as e:
                _log(f"{room_id}: {e!r}")
    return items


def view(room_id: str, now: float | None = None) -> list[dict]:
    """What a room's page shows: every send not yet confirmed, oldest first,
    and one confirmed a moment ago (until the page's own copy of the
    conversation has it). A send queued by a hub that has since stopped is
    failed here: its queue went with that hub."""
    now = time.time() if now is None else now
    if not _ROOM_ID.fullmatch(room_id or ""):
        return []
    items = reconcile(room_id, now)
    out = []
    for s in sorted(items, key=lambda s: s["at"]):
        if s["state"] == "confirmed" and (not s.get("mid") or now - float(
                s.get("confirmedAt") or s["stateAt"]) > SHOW_CONFIRMED_S):
            continue        # in the conversation (or never to be: a /command)
        out.append({k: s.get(k) for k in ("key", "text", "to", "at", "state", "stateAt", "error", "mid")
                    if s.get(k) not in (None, "")})
    return out
