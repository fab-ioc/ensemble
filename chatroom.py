"""Multi-agent chat rooms — pairing live agent sessions into a collaborative
"duo" (plus the human) that coordinate through an MCP-backed message log.

A *room* links two or more live agent sessions and the human participant
(`user`). Agents exchange messages by calling the `chat_send` / `chat_read` MCP
tools; each agent's identity is resolved from a per-session bearer token, so the
server always knows who is speaking. When a message is posted, the dashboard
rings the recipient's terminal (the keystroke "doorbell") so the agent wakes to
read it. An agent can address `user` to pull the human in — that pauses the
autonomous relay until the human replies from the browser panel.

Design notes
------------
* **Hand-off = a `chat_send` call.** We don't scrape terminals to guess when an
  agent finished; the agent signals turn-end by sending. This is reliable and
  keeps the loop explicit.
* **Loop guard.** `hop_count` tracks consecutive agent→agent hands-off with no
  human turn; once it reaches `max_hops` the room pauses and waits for the
  human, so a two-agent loop can't run away.
* **Who gets woken.** Every ring re-sends an agent's whole conversation, so a
  message wakes as few agents as it can: a direct message wakes its addressee;
  a message to everyone wakes only the task's *owner* (see :func:`owners`), and
  a specialist — a reviewer, a designer — only when the user, an owner or the
  PO @mentions it. :func:`wake_targets` is the one rule; the attention detector
  uses it too, so "was it asked?" means "was it woken?".
* **Persistence.** Each room is a JSON file under ``DASHBOARD_DIR/rooms`` so
  rooms (and their transcripts) survive a server restart. All mutation goes
  through a single process-wide lock — duo-scale volume, so read-modify-write
  per op is fine.

This module is pure state + policy; the HTTP endpoints, the MCP transport, and
the doorbell live in ``dashboard.py`` (which injects a ``ring`` callback).
"""
from __future__ import annotations

import json
import copy
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path

from backends.base import DASHBOARD_DIR
import durable
import filetable

ROOMS_DIR = DASHBOARD_DIR / "rooms"
HUMAN_IDENTITY = "user"
DEFAULT_MAX_HOPS = 24

_LOCK = threading.RLock()
# A stat is cheap; parsing a 10 MB room on each browser poll is not. Entries
# are replaced as a unit, including their small metadata index.
_INDEX: dict[str, dict] = {}
_CHANGES: dict[str, list[tuple[str, str, set[str]]]] = {}
_INDEX_LOCK = threading.RLock()
_TOKEN_ROOMS: dict[str, str] = {}       # bearer token -> the room it was last found in


def _signature(st) -> str:
    return f"{st.st_size}:{st.st_mtime_ns}"


def _entry(room: dict, st) -> dict:
    signature = f'{room.get("_revision", "")}:{_signature(st)}' if room.get("_revision") else _signature(st)
    return {"id": room["id"], "project": room.get("projectId") or room.get("noProjectId") or "",
            "workflow": room.get("workflow") or "", "updatedAt": room.get("updatedAt", 0),
            "stoppedAt": room.get("stoppedAt", 0), "size": st.st_size,
            "mtime": st.st_mtime_ns, "signature": signature, "room": room}


# The room files with their time and size (#228): the hub writes a room by
# replacing it, which moves the folder, so a refresh re-lists the folder only
# then and re-stats only rooms changed lately, not all ~600.
ROOM_FILES = filetable.FileTable(lambda: ROOMS_DIR, 0,
                                 lambda n: n.startswith("room-") and n.endswith(".json"))


def room_index() -> list[dict]:
    """Incremental room index; external edits and deletions are noticed by
    stat (an in-place edit of a room quiet for hours: within a minute)."""
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()
    with _INDEX_LOCK:
        _cards_here()
        # Listed under the lock, as a write replaces a room under it: a room
        # written meanwhile is never taken for one deleted.
        table = ROOM_FILES.files()
        for name, sig in table.items():
            path = filetable.as_path(name)
            rid = path.stem
            seen.add(rid)
            try:
                hit = _INDEX.get(rid)
                if hit and hit["size"] == sig[1] and hit["mtime"] == sig[2]:
                    continue
                st = path.stat()
                hit = _INDEX.get(rid)
                if hit and hit["size"] == st.st_size and hit["mtime"] == st.st_mtime_ns:
                    continue
                # A kept card is taken while its room file is unchanged. A
                # veto that changed without a write (a room made a PO's, a
                # seat come alive) and a crash between the log append and the
                # room file both leave it a card until the room is written:
                # the chat itself is always read whole from file and log.
                kept = _CARDS.get(rid)
                if kept and kept["sig"] == _signature(st):
                    _INDEX[rid] = _card_entry(kept["card"], st)
                    _CHANGES.pop(rid, None)
                    continue
                room = _read(rid)
                if room is not None:
                    _INDEX[rid] = _index_entry(rid, room, st)
                    # An external write has no recorded message diff.
                    _CHANGES.pop(rid, None)
            except OSError:
                continue
        for rid in list(_INDEX):
            if rid not in seen:
                _INDEX.pop(rid, None)
                _CHANGES.pop(rid, None)
        for rid in [r for r in _CARDS if r not in seen]:
            _CARDS.pop(rid, None)
            _FULL.pop(rid, None)
            _CARDS_STATE["dirty"] = True
        if time.monotonic() - _CARDS_STATE["swept"] >= CARDS_SWEEP_S:
            _CARDS_STATE["swept"] = time.monotonic()
            sweep_cards()
        out = list(_INDEX.values())
    save_cards()
    return out


def room_is_cold(room: dict, is_live=None, now: float | None = None) -> bool:
    """A finished room leaves hot scans one day after its last change."""
    now = _now() if now is None else now
    done = (room.get("workflow") or "").lower() == "done"
    stopped_at = float(room.get("stoppedAt") or 0)
    # Stop records a timestamp permanently. A later launch/resume makes that
    # marker historical, even if the terminal has since gone idle or died.
    resumed_at = max([float(room.get("launchedAt") or 0)] +
                     [float(p.get("resumedAt") or 0) for p in room.get("participants", [])])
    stopped = stopped_at > 0 and stopped_at >= resumed_at
    if not (done or stopped):
        return False
    changed = max(float(room.get("updatedAt") or 0), stopped_at)
    return changed < now - 86400 and not (is_live and is_live(room))


# Finished tasks as cards (#256): a task Done for a day is held in the index
# as its record without its messages (a card), with a few facts its messages
# gave (CARD_FACTS), and the cards are kept in CARDS_FILE_NAME, so a start
# parses neither the ~700 finished rooms nor their chats. The room's file and
# log stay as they are: they are its archive, read whole only when the task
# is opened or changed (get_room, _load), the last few kept (FULL_KEEP).
ARCHIVE_AFTER_S = 86400
CARDS_FILE_NAME = "cards.json"
CARDS_VERSION = 1
CARDS_SAVE_S = 60.0
# The cards file is written by a hub (dashboard.main says so) or into a rooms
# folder other than the one this process started with (a test's own): a test
# that happens to read the live rooms never writes into the live state.
_HOME_ROOMS = ROOMS_DIR
CARDS_WRITER = False
CARDS_SWEEP_S = 300.0
FULL_KEEP = 16
# Each: room -> True to keep the room whole (a live seat, an open ask, a
# PO's room). One that raises keeps it whole too.
ARCHIVE_VETO: list = []
# Each: room -> a dict of facts its messages give, kept on its card under
# CARD_KEY ("turns" is always there).
CARD_FACTS: list = []
CARD_KEY = "_card"
_CARDS: dict[str, dict] = {}            # room id -> {"sig": size:mtime of its file, "card": {...}}
_CARDS_STATE = {"dir": "", "dirty": False, "saved": 0.0, "swept": 0.0}
_FULL: dict[str, tuple[int, int, dict]] = {}    # room id -> (size, mtime, whole room), newest last


def archivable(room: dict, now: float | None = None) -> bool:
    """Whether a room is held as a card: Done, unchanged for ARCHIVE_AFTER_S,
    not waiting on the person, and no ARCHIVE_VETO keeps it whole."""
    if (room.get("workflow") or "").lower() != "done" or room.get("status") == "waiting_human":
        return False
    now = _now() if now is None else now
    try:
        changed = max(float(room.get("updatedAt") or 0), float(room.get("stoppedAt") or 0),
                      float(room.get("workflowAt") or 0))
    except (TypeError, ValueError):
        return False
    if changed >= now - ARCHIVE_AFTER_S:
        return False
    for veto in ARCHIVE_VETO:
        try:
            if veto(room):
                return False
        except Exception:       # noqa: BLE001 — not sure: the room stays whole
            return False
    return True


def is_card(room: dict) -> bool:
    return CARD_KEY in room


def message_count(room: dict) -> int:
    """How many messages a room has, a card's included."""
    card = room.get(CARD_KEY)
    return int(card.get("turns") or 0) if isinstance(card, dict) else len(room.get("messages") or [])


def _make_card(room: dict) -> dict:
    msgs = room.get("messages") or []
    facts = {"turns": len(msgs)}
    for fn in CARD_FACTS:
        try:
            facts.update(fn(room) or {})
        except Exception as e:      # noqa: BLE001 — a fact missing shows as none
            print(f"[rooms] {room.get('id')}: card fact not read: {e!r}", flush=True)
    card = {k: v for k, v in room.items() if k != "messages"}
    card[CARD_KEY] = facts
    return card


def _cards_path() -> Path:
    return ROOMS_DIR / CARDS_FILE_NAME


def _cards_here() -> None:
    """The cards of this ROOMS_DIR, read once: a card is used only while its
    room's file has the size and time it was made from, so a stale or
    damaged cards file costs a re-read, never a wrong room."""
    here = str(ROOMS_DIR)
    if _CARDS_STATE["dir"] == here:
        return
    _CARDS.clear()
    _FULL.clear()
    _CARDS_STATE.update(dir=here, dirty=False, saved=0.0, swept=0.0)
    try:
        d = json.loads(_cards_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        print(f"[rooms] cards not read, the rooms are read instead: {e!r}", flush=True)
        return
    if not isinstance(d, dict) or d.get("version") != CARDS_VERSION or not isinstance(d.get("cards"), dict):
        return
    for rid, c in d["cards"].items():
        if (isinstance(c, dict) and isinstance(c.get("sig"), str) and isinstance(c.get("card"), dict)
                and c["card"].get("id") == rid and isinstance(c["card"].get(CARD_KEY), dict)):
            _CARDS[rid] = c


def _card_entry(card: dict, st) -> dict:
    e = _entry(card, st)
    e["cold"] = True
    return e


def _index_entry(room_id: str, room: dict, st, now: float | None = None) -> dict:
    """The index entry of a room just read or written: a card if it is
    archivable. Under _INDEX_LOCK."""
    _cards_here()
    if archivable(room, now):
        card = _make_card(room)
        _CARDS[room_id] = {"sig": _signature(st), "card": card}
        _CARDS_STATE["dirty"] = True
        _keep_full(room_id, st, room)
        return _card_entry(card, st)
    if _CARDS.pop(room_id, None) is not None:
        _CARDS_STATE["dirty"] = True
    _FULL.pop(room_id, None)
    return _entry(room, st)


def _keep_full(room_id: str, st, room: dict) -> None:
    _FULL.pop(room_id, None)
    _FULL[room_id] = (st.st_size, st.st_mtime_ns, room)
    while len(_FULL) > FULL_KEEP:
        _FULL.pop(next(iter(_FULL)))


def _full_room(room_id: str, st) -> dict | None:
    """A carded room whole, from its file and log. Called with _INDEX_LOCK
    released: reading a 17 MB chat under it held every list ~0.5 s. The room
    read is kept only while the index still has the file it was read from."""
    with _INDEX_LOCK:
        hit = _FULL.get(room_id)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            _keep_full(room_id, st, hit[2])
            return hit[2]
    room = _read(room_id)
    if room is not None:
        with _INDEX_LOCK:
            e = _INDEX.get(room_id)
            if e and e.get("cold") and e["size"] == st.st_size and e["mtime"] == st.st_mtime_ns:
                _keep_full(room_id, st, room)
    return room


def sweep_cards(now: float | None = None) -> int:
    """Hold the whole rooms that became archivable as cards; how many."""
    n = 0
    with _INDEX_LOCK:
        for rid, e in list(_INDEX.items()):
            if e.get("cold") or not archivable(e["room"], now):
                continue
            try:
                st = _room_path(rid).stat()
            except OSError:
                continue
            if e["size"] != st.st_size or e["mtime"] != st.st_mtime_ns:
                continue
            _INDEX[rid] = _index_entry(rid, e["room"], st, now)
            n += 1
    return n


def save_cards(force: bool = False) -> bool:
    """Write the cards file when cards changed, at most every CARDS_SAVE_S."""
    if not CARDS_WRITER and ROOMS_DIR == _HOME_ROOMS:
        return False
    with _INDEX_LOCK:
        if not _CARDS_STATE["dirty"] or not _CARDS_STATE["dir"]:
            return False
        if not force and time.monotonic() - _CARDS_STATE["saved"] < CARDS_SAVE_S:
            return False
        snap = dict(_CARDS)
        _CARDS_STATE.update(dirty=False, saved=time.monotonic())
        path = _cards_path()
    try:
        tmp = path.with_name(path.name + ".tmp")
        durable.write_tmp(tmp, json.dumps({"version": CARDS_VERSION, "cards": snap}))
        _replace(tmp, path)
        return True
    except Exception as e:
        # Any failure (a card that will not serialise too) leaves the cards
        # file as it was and the index untouched: the list never empties.
        print(f"[rooms] cards not saved: {e!r}", flush=True)
        with _INDEX_LOCK:
            _CARDS_STATE["dirty"] = True
        return False


def room_delta(room_id: str, cursor: str, target: str = "") -> tuple[str, set[str]] | None:
    """Return current revision and changed message IDs, or require a full load."""
    with _INDEX_LOCK:
        hit = _INDEX.get(room_id)
        if not hit:
            return None
        current = target or hit["signature"]
        if cursor == current:
            return current, set()
        changed: set[str] = set()
        for before, after, ids in _CHANGES.get(room_id, []):
            if before == cursor:
                changed.update(ids)
                cursor = after
                if cursor == current:
                    return current, changed
        return None


def room_revision(room_id: str) -> str:
    with _INDEX_LOCK:
        return (_INDEX.get(room_id) or {}).get("signature", "")


def get_room_snapshot(room_id: str) -> tuple[dict | None, str]:
    """Read the room and its cursor under the same index lock. The room is
    its own copy but for the message dicts, which are the indexed ones (read
    them, never change them): deep-copying a 10 MB chat on every poll cost
    ~40 ms, ~130 ms at five times the history (#228)."""
    with _INDEX_LOCK:
        room = get_room(room_id, copy_room=False)
        return (_listed_copy(room) if room is not None else None), room_revision(room_id)


def _now() -> float:
    return time.time()


def _room_path(room_id: str) -> Path:
    return ROOMS_DIR / f"{room_id}.json"


# A room's messages are a log, one JSON line each, beside its file (#256): a
# message is appended, and a change to the room rewrites only the room. The
# whole chat in the room file made each change of the 17 MB PO chat a 17 MB
# write. A room file from before keeps its messages until its first write,
# which copies it to ORIGINALS_DIR_NAME first. The log has every message: one
# appended just before a stop lacks only the room file's count.
LOG_DIR_NAME = "messages"
ORIGINALS_DIR_NAME = "before-message-log"
_LOGGED: set[str] = set()       # rooms whose file on disk keeps its messages in the log


def _log_path(room_id: str) -> Path:
    return ROOMS_DIR / LOG_DIR_NAME / f"{room_id}.jsonl"


class _Unreadable(Exception):
    """A room's log is there but cannot be read just now (another process has
    it open, no access), or is gone while the room file counts messages: the
    room is not read at all rather than read empty, which the next write
    would make true (#256, the PO's pre-merge read)."""


_DAMAGED: dict[str, int] = {}   # room id -> damaged lines its last read skipped


def _read_log(room_id: str, count: int) -> tuple[list[dict], bool]:
    """The logged messages, and whether the log is whole: a line cut short
    by a stop mid-append makes the next write rewrite it, so a message
    appended after it is not glued onto the damaged line. ``count``: the
    messages the room file says the log has."""
    try:
        raw = _log_path(room_id).read_bytes()
    except FileNotFoundError:
        if count > 0:
            print(f"[rooms] {room_id}: the log of {count} messages is missing; the room is not read", flush=True)
            raise _Unreadable(room_id)
        return [], False
    except OSError as e:
        print(f"[rooms] {room_id}: messages not read: {e!r}", flush=True)
        raise _Unreadable(room_id) from e
    out, bad = [], 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except ValueError:      # a line cut short by a stop mid-append
            bad += 1
            continue
        if isinstance(m, dict):
            out.append(m)
    if bad:
        print(f"[rooms] {room_id}: {bad} damaged message line(s) skipped", flush=True)
        _DAMAGED[room_id] = bad
    else:
        _DAMAGED.pop(room_id, None)
    return out, not bad and (not raw or raw.endswith(b"\n"))


def _logged_count(room: dict) -> int:
    try:
        return max(0, int((room.get("messageLog") or {}).get("count") or 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def _read(room_id: str) -> dict | None:
    p = _room_path(room_id)
    try:
        room = json.loads(p.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(room, dict):
        return room
    if isinstance(room.get("messageLog"), dict):
        try:
            logged, whole = _read_log(room_id, _logged_count(room))
        except _Unreadable:
            _LOGGED.discard(room_id)
            return None
        inline = room.get("messages")
        if isinstance(inline, list):
            # A hub from before the log wrote this file (a downgrade): its
            # messages are added to the log's, never put in their place.
            ids = {m.get("id") for m in logged}
            logged += [m for m in inline if isinstance(m, dict) and m.get("id") not in ids]
            whole = False
        room["messages"] = logged
        if whole:
            _LOGGED.add(room_id)
        else:
            _LOGGED.discard(room_id)
    else:
        _LOGGED.discard(room_id)
    room.pop("messageLog", None)
    return room


def _append_lines(path: Path, msgs: list[dict]) -> None:
    with open(path, "ab") as f:
        f.write(b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in msgs))
        f.flush()
        os.fsync(f.fileno())


def _replace(tmp: Path, path: Path) -> None:
    # Windows refuses the replace while another process (a virus scanner, a
    # backup, the indexer) has the file open for a moment: WinError 5 on
    # 2026-09-23 failed a PO resume after its terminal had started.
    for attempt in range(8):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def _log_text(msgs: list[dict]) -> str:
    return "".join(json.dumps(m) + "\n" for m in msgs)


def _on_disk(room_id: str) -> tuple[bytes, dict | None]:
    """The room file as it is now: its bytes and its JSON (None when it is
    not there). A file there that cannot be read raises."""
    try:
        raw = _room_path(room_id).read_bytes()
    except FileNotFoundError:
        return b"", None
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return raw, None
    return raw, d if isinstance(d, dict) else None


def _keep_original(room_id: str, raw: bytes) -> None:
    """The room file from before the log, kept once (whole: a copy cut short
    by a stop is made again)."""
    keep = ROOMS_DIR / ORIGINALS_DIR_NAME / f"{room_id}.json"
    try:
        if keep.stat().st_size == len(raw):
            return
    except OSError:
        pass
    keep.parent.mkdir(parents=True, exist_ok=True)
    tmp = keep.with_name(keep.name + ".tmp")
    durable.write_tmp(tmp, raw.decode("utf-8-sig"))
    _replace(tmp, keep)


def _write_messages(room_id: str, old: list[dict] | None, new: list[dict], removed: int = 0):
    """The log holds ``new``: the messages added after ``old`` (the logged
    ones) are appended; anything else (a first write, a message taken back,
    a damaged log) writes the log again. Messages are never changed once
    posted. Returns how to put the log back if the room file then cannot be
    written. A rewrite never holds fewer messages than the room file counts,
    but for the ``removed`` ones and the damaged lines skipped: a room read
    without its messages must not lose them (#256)."""
    path = _log_path(room_id)
    if room_id in _LOGGED and old is not None and len(new) >= len(old) and all(
            a is b or a == b for a, b in zip(old, new)):
        if len(new) == len(old):
            return lambda: None
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        try:
            _append_lines(path, new[len(old):])
        except BaseException:
            # A part of a line may be there: the next write rewrites the log.
            _LOGGED.discard(room_id)
            _truncate(path, size)
            raise

        def undo():
            if not _truncate(path, size):
                _LOGGED.discard(room_id)
        return undo
    raw, disk = _on_disk(room_id)
    if disk is not None:
        inline = disk.get("messages")
        had = len(inline) if isinstance(inline, list) and "messageLog" not in disk else _logged_count(disk)
        if isinstance(inline, list) and "messageLog" not in disk:
            _keep_original(room_id, raw)
        floor = had - removed - _DAMAGED.get(room_id, 0)
        if len(new) < floor:
            print(f"[rooms] {room_id}: refused to write {len(new)} messages over {had}", flush=True)
            raise RuntimeError(f"{room_id}: the log would lose messages ({len(new)} < {floor})")
    path.parent.mkdir(parents=True, exist_ok=True)
    if _DAMAGED.get(room_id) and path.exists():
        try:
            shutil.copy2(path, path.with_name(f"{path.name}.damaged-{int(time.time())}"))
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    restore = old is not None and disk is not None and "messageLog" in disk
    durable.write_tmp(tmp, _log_text(new))
    _replace(tmp, path)
    _DAMAGED.pop(room_id, None)

    def undo():
        # The room file still names the old messages: the log holds them
        # again, or (an old room file) is not read at all.
        _LOGGED.discard(room_id)
        if restore:
            try:
                durable.write_tmp(tmp, _log_text(old))
                _replace(tmp, path)
            except OSError as e:
                print(f"[rooms] {room_id}: the log was not put back: {e!r}", flush=True)
    return undo


ORIGINALS_KEEP_S = 14 * 86400   # a kept original goes this long after it was made, once verified


def prune_originals(now: float | None = None) -> int:
    """Remove the room files kept from before the log once their room is
    verified: its file reads, its log is whole, and it holds every message
    the original had (or the room is gone). Returns how many went."""
    now = time.time() if now is None else now
    d = ROOMS_DIR / ORIGINALS_DIR_NAME
    try:
        entries = [e for e in os.scandir(d) if e.name.endswith(".json") and e.is_file()]
    except OSError:
        return 0
    gone = 0
    for e in entries:
        rid = e.name[:-len(".json")]
        try:
            if e.stat().st_mtime > now - ORIGINALS_KEEP_S:
                continue
            if _room_path(rid).exists():
                with _LOCK:
                    room = _read(rid)
                if room is None or rid not in _LOGGED:
                    continue
                kept = json.loads(Path(e.path).read_text(encoding="utf-8-sig"))
                ids = {m.get("id") for m in room.get("messages") or []}
                if any(isinstance(m, dict) and m.get("id") not in ids
                       for m in (kept.get("messages") or [] if isinstance(kept, dict) else [])):
                    print(f"[rooms] {rid}: the log lacks messages its original has; the original is kept", flush=True)
                    continue
            os.unlink(e.path)
            gone += 1
        except (OSError, ValueError):
            continue
    return gone


def _truncate(path: Path, size: int | None) -> bool:
    """The log cut back to ``size`` bytes (what it was before an append)."""
    if size is None:
        return False
    try:
        with open(path, "r+b") as f:
            f.truncate(size)
        return True
    except OSError as e:
        print(f"[rooms] {path.stem}: the log was not cut back: {e!r}", flush=True)
        return False


def _load(room_id: str) -> dict | None:
    """The room to change, for a read-modify-write under ``_LOCK``: the
    indexed room when the file is the one it was read from, else the file.
    Its own copy but for the message dicts, which are never changed once
    posted: parsing and copying a 17 MB chat for each change cost ~0.5 s of
    the hub's lock per message there (#256)."""
    try:
        st = _room_path(room_id).stat()
    except OSError:
        return None
    with _INDEX_LOCK:
        hit = _INDEX.get(room_id)
        if hit and hit["size"] == st.st_size and hit["mtime"] == st.st_mtime_ns and not hit.get("cold"):
            return _listed_copy(hit["room"])
        cold = bool(hit and hit.get("cold") and hit["size"] == st.st_size
                    and hit["mtime"] == st.st_mtime_ns)
    if cold:
        room = _full_room(room_id, st)
        return _listed_copy(room) if room is not None else None
    return _read(room_id)


def messages_file(room_id: str) -> str:
    """Where a room's messages are on disk, as an agent is told it."""
    if room_id in _LOGGED:
        return f"~/.ensemble/rooms/{LOG_DIR_NAME}/{room_id}.jsonl"
    return f"~/.ensemble/rooms/{room_id}.json"


def _write(room: dict, *, removed: int = 0) -> None:
    """Write the room: its log first, then its file. ``removed``: messages
    taken out of the list on purpose (a withdraw), which a rewrite of the
    log may drop."""
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    p = _room_path(room["id"])
    tmp = p.with_suffix(".json.tmp")
    room["_revision"] = uuid.uuid4().hex
    # The room as it was; a whole file and log are read outside the index
    # lock (the writer holds _LOCK, so the room cannot change meanwhile).
    try:
        old_st = p.stat()
        with _INDEX_LOCK:
            prior = _INDEX.get(room["id"])
            same = prior and prior["size"] == old_st.st_size and prior["mtime"] == old_st.st_mtime_ns
            old = prior["room"] if same and not prior.get("cold") else None
        if old is None:
            old = _full_room(room["id"], old_st) if same else _read(room["id"])
        old_sig = _entry(old, old_st)["signature"] if old else ""
    except OSError:
        old_sig, old = "", None
    # The log first: a stop between the two leaves a room file that still
    # reads (its old messages, or the log with one message more).
    msgs = room.get("messages") or []
    undo = _write_messages(room["id"], (old or {}).get("messages") if old is not None else None, msgs, removed)
    # A room file that cannot be written takes the log back with it: the
    # caller's retry would post the message twice (#256).
    try:
        durable.write_tmp(tmp, json.dumps({**{k: v for k, v in room.items() if k != "messages"},
                                           "messageLog": {"count": len(msgs)}}, indent=2))
    except BaseException:
        undo()
        raise
    with _INDEX_LOCK:
        try:
            _replace(tmp, p)
        except BaseException:
            undo()
            raise
        _LOGGED.add(room["id"])
        st = p.stat()
        new_sig = _entry(room, st)["signature"]
        _INDEX[room["id"]] = _index_entry(room["id"], _listed_copy(room), st)
        old_msgs = (old or {}).get("messages") or []
        new_msgs = room.get("messages") or []
        # A delta can patch changed messages and appends. Deletion or reorder
        # invalidates the chain and tells the browser to reload in full.
        if old is not None and len(new_msgs) >= len(old_msgs) and all(
                a.get("id") == b.get("id") for a, b in zip(old_msgs, new_msgs)):
            changed = {b.get("id") for a, b in zip(old_msgs, new_msgs) if a is not b and a != b}
            changed.update(m.get("id") for m in new_msgs[len(old_msgs):])
            history = _CHANGES.setdefault(room["id"], [])
            history.append((old_sig, new_sig, changed))
            del history[:-64]
        else:
            _CHANGES.pop(room["id"], None)


# ---------------------------------------------------------------------------
# Room lifecycle
# ---------------------------------------------------------------------------

def create_room(title: str, members: list[dict], max_hops: int = DEFAULT_MAX_HOPS) -> dict:
    """Create a room from a list of agent members.

    Each ``member`` is a dict describing a live agent session:
        {identity, agent, pid, sessionId, cwd, label}
    The human participant (`user`) is always added implicitly. Every agent is
    issued a bearer token used to authenticate its MCP calls.
    """
    with _LOCK:
        room_id = "room-" + uuid.uuid4().hex[:8]
        participants = []
        tokens = {}
        used: set[str] = {HUMAN_IDENTITY}
        for m in members:
            base = (m.get("identity") or m.get("agent") or "agent").strip()
            ident = base
            n = 2
            while ident in used:            # two Claudes → claude, claude-2
                ident = f"{base}-{n}"
                n += 1
            used.add(ident)
            participants.append({
                "identity": ident,
                "kind": "agent",
                "agent": m.get("agent", ""),
                "model": m.get("model", ""),
                "role": (m.get("role") or "").strip(),   # engineer | reviewer | pair | custom text
                "pid": m.get("pid"),
                "sessionId": m.get("sessionId", ""),
                "cwd": m.get("cwd", ""),
                "label": m.get("label", ""),
            })
            tokens[secrets.token_urlsafe(18)] = ident
        participants.append({"identity": HUMAN_IDENTITY, "kind": "human"})
        room = {
            "id": room_id,
            "title": title or "multiagent session",
            "mode": "multiagent",
            "status": "active",          # active | paused | waiting_human
            "createdAt": _now(),
            "updatedAt": _now(),
            "participants": participants,
            "tokens": tokens,            # bearer token -> identity
            "messages": [],              # {id, from, to, text, ts, seenBy:[]}
            "hopCount": 0,
            "maxHops": int(max_hops),
            "waitingFor": "",            # identity the room is blocked on (human)
        }
        _write(room)
        return room


def _unique_identity(base: str, used: set[str]) -> str:
    """First free identity for ``base``: ``claude``, then ``claude-2``, …"""
    ident = base
    n = 2
    while ident in used:
        ident = f"{base}-{n}"
        n += 1
    return ident


def set_agents(room_id: str, members: list[dict],
               mode: str = "", keep_live: set | frozenset = frozenset()) -> dict | None:
    """Replace a room's agent line-up, keeping the agents that are staying.

    ``members`` are the same ``{identity?, agent, model, role}`` dicts
    :func:`create_room` takes. An entry is matched to an existing participant —
    which keeps that agent's identity, bearer token, session id and working dir,
    so its transcript and any live MCP client survive — by its ``identity``,
    naming an existing agent **of the same kind**.

    A pin that doesn't resolve does NOT fall back to anything: it means the
    agent it named is gone, or has changed kind, and the entry is a new agent.
    Falling back would let one row walk off with a *different* row's identity,
    token and transcript — a change to one agent silently rewriting another.
    For the same reason, once ANY entry carries an identity, an entry without
    one is taken at its word: a new agent.

    Positional matching — first unclaimed participant of the same kind — is the
    fallback only for a wholly identity-free list, which is what a plain
    ``["claude", "codex"]`` or an older client sends.

    An identity whose ``agent`` kind changed is deliberately NOT reused: claude
    and codex are different agents, and handing one the other's transcript would
    be wrong. Anything unmatched is a new agent — it gets a fresh identity, a
    fresh token, and a ``fresh`` flag so the launcher starts it from the task
    briefing instead of trying to resume a conversation it never had. Agents
    that dropped out lose their participant record and their token; their
    conversations are kept in the room's ``retiredSessions``.

    ``mode`` ("solo" / "collab"), when given, is written in the same breath, so
    the file is never briefly on disk with a new line-up and the old mode.

    ``keep_live`` names retained agents that are RUNNING while the line-up
    changes (dashboard.reassign_task lets a seat nobody sits in change under a
    live task): their ``ptyId`` and ``pid`` stay, so the record keeps saying
    what is true. Any other retained agent's are dropped, as before.

    Returns the updated full room (tokens included), or None if there is no
    such room. Messages are untouched: a removed agent's turns stay in the log.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        existing = [p for p in room.get("participants", []) if p.get("kind") == "agent"]
        by_identity = {p.get("identity", ""): i for i, p in enumerate(existing)}
        claimed: set[int] = set()
        matched: list[dict | None] = [None] * len(members)

        pins = [(m.get("identity") or "").strip() for m in members]
        if any(pins):
            for i, m in enumerate(members):
                j = by_identity.get(pins[i], -1) if pins[i] else -1
                if j >= 0 and j not in claimed and \
                        existing[j].get("agent", "") == (m.get("agent") or "").strip():
                    claimed.add(j)
                    matched[i] = existing[j]
        else:
            for i, m in enumerate(members):
                kind = (m.get("agent") or "").strip()
                for j, p in enumerate(existing):
                    if j not in claimed and p.get("agent", "") == kind:
                        claimed.add(j)
                        matched[i] = p
                        break

        # An identity that has already spoken, or already read the room, is
        # spent: recycling it would relabel someone else's messages and hand
        # the newcomer the departed agent's read cursor, so its first
        # chat_read would skip the whole conversation.
        used = ({HUMAN_IDENTITY}
                | {p.get("identity", "") for p in matched if p}
                | {m.get("from", "") for m in room.get("messages", [])}
                | set(room.get("reads", {})))
        tokens_by_identity = {ident: tok for tok, ident in room.get("tokens", {}).items()}
        participants: list[dict] = []
        tokens: dict[str, str] = {}
        for m, part in zip(members, matched):
            if part is not None:
                part = dict(part)
                part["model"] = m.get("model", "")
                part["role"] = (m.get("role") or "").strip()
                # These two name a process that is already gone — a stopped room
                # keeps them, which is exactly what makes it read as live. Drop
                # them here so the record stops lying. ``sessionId`` and ``cwd``
                # deliberately survive: they are not liveness, they are how the
                # agent finds its own transcript again when the task restarts.
                if part["identity"] not in keep_live:
                    part["pid"] = None
                    part.pop("ptyId", None)
                tok = tokens_by_identity.get(part["identity"])
                if not tok:                     # shouldn't happen; don't strand it
                    tok = secrets.token_urlsafe(18)
                tokens[tok] = part["identity"]
            else:
                # A new agent is named after its kind, never after the
                # identity hint: that hint means "reuse this one", and it only
                # got here because nothing matched it.
                base = (m.get("agent") or "").strip() or "agent"
                ident = _unique_identity(base, used)
                used.add(ident)
                part = {
                    "identity": ident,
                    "kind": "agent",
                    "agent": m.get("agent", ""),
                    "model": m.get("model", ""),
                    "role": (m.get("role") or "").strip(),
                    "pid": None,
                    "sessionId": "",
                    "cwd": "",
                    "label": "",
                    # No conversation to resume — start it from the briefing.
                    "fresh": True,
                }
                tokens[secrets.token_urlsafe(18)] = ident
            participants.append(part)
        participants.append({"identity": HUMAN_IDENTITY, "kind": "human"})
        # A dropped agent's conversations stay the room's: its transcripts are
        # the task's history, never sessions of no task (the board's cards).
        retired = [r for r in room.get("retiredSessions") or [] if isinstance(r, dict)]
        known = {r.get("sessionId") for r in retired}
        now = _now()
        for j, p in enumerate(existing):
            if j in claimed:
                continue
            for sid in agent_conversations(p):
                if sid not in known:
                    known.add(sid)
                    retired.append({"sessionId": sid, "identity": p.get("identity", ""),
                                    "agent": (p.get("sessionKinds") or {}).get(sid) or p.get("agent", ""),
                                    "at": now})
        if retired:
            room["retiredSessions"] = retired
        room["participants"] = participants
        room["tokens"] = tokens
        if mode:
            room["mode"] = mode
        room["updatedAt"] = _now()
        _write(room)
        return room


def agent_conversations(part: dict) -> list[str]:
    """An agent's conversations on its task: its current one, one per review,
    each one a rotation, switch or handover moved it out of, and each one the
    person's ``/clear`` left (#253)."""
    sids = [part.get("sessionId") or ""]
    sids += [r.get("sessionId") or "" for r in part.get("reviews") or [] if isinstance(r, dict)]
    sids += [r.get("fromSessionId") or "" for r in part.get("rotations") or [] if isinstance(r, dict)]
    sids += [r.get("fromSessionId") or "" for r in part.get("cleared") or [] if isinstance(r, dict)]
    out: list[str] = []
    for s in sids:
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


NUMBER_FIELDS = ("no", "noProjectId", "previousNos")


def update_room(room: dict) -> None:
    """Persist a full (non-public) room dict — used by the launcher to record
    the working dir and each participant's session id/pid after spawning.

    A task's number is written only by :func:`set_task_number`, so a copy
    read before it was given, or before a move renumbered it, never undoes it:
    once the file has a number, :data:`NUMBER_FIELDS` are the file's."""
    with _LOCK:
        disk = _load(room.get("id", "")) or {}
        if disk.get("no"):
            for k in NUMBER_FIELDS:
                if k in disk:
                    room[k] = disk[k]
                else:
                    room.pop(k, None)
        if (disk.get("interrupts") or []) and (
                not room.get("interrupts") or
                disk["interrupts"][-1]["ts"] > room["interrupts"][-1]["ts"]):
            room["interrupts"] = disk["interrupts"]
        room["updatedAt"] = _now()
        _write(room)


def set_task_number(room_id: str, project_id: str, no: int, forget_old: bool = False) -> dict | None:
    """Give a task its number in a project — read-modify-write under the room
    lock, no ``updatedAt`` bump (numbering is not activity). A number it had
    in another project is kept in ``previousNos`` so an old reference still
    finds it — unless ``forget_old``: a number it shared with another task
    (the hub's start repair) was never its own. Returns the public room, or
    None when it no longer exists."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        old, old_pid = room.get("no"), room.get("noProjectId")
        if old and old_pid and (old_pid, old) != (project_id, no) and not forget_old:
            prev = [p for p in room.get("previousNos") or [] if isinstance(p, dict)]
            if not any(p.get("projectId") == old_pid and p.get("no") == old for p in prev):
                prev.append({"projectId": old_pid, "no": old})
            room["previousNos"] = prev
        room["no"] = int(no)
        room["noProjectId"] = project_id
        _write(room)
        return _public(room)


def record_exit(room_id: str, identity: str, exit_rec: dict) -> bool:
    """Stamp one agent's last exit onto the room — read-modify-write under the
    room lock.

    Only the participant's current PTY may write its death. A replacement can
    run briefly under the same room and identity before it is committed; its
    death must not overwrite the retained session's exit metadata.

    Deliberately narrow. The caller is a dying PTY's reader thread, and a
    ``get_room`` → mutate → :func:`update_room` from there would race
    :func:`post_message`: both rewrite the whole file, so a chat message posted
    in between would be silently dropped. Doing the read and the write inside
    the same lock makes that impossible.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return False
        hit = False
        for p in room.get("participants", []):
            if (p.get("identity") == identity and exit_rec.get("ptyId")
                    and (not p.get("ptyId") or p.get("ptyId") == exit_rec["ptyId"])):
                p["lastExit"] = exit_rec
                hit = True
        if not hit:
            return False
        _write(room)                 # NB: not update_room — no updatedAt bump;
        return True                  # a death is not activity on the task.


def patch_room(room_id: str, **fields) -> dict | None:
    """Set a few fields on a room — read-modify-write under the room lock, for
    the same reason as :func:`record_exit`. No ``updatedAt`` bump: the hub
    noting something about a task is not activity on it. Returns the full
    room, or None when it no longer exists."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        room.update(fields)
        _write(room)
        return room


def patch_room_if(room_id: str, test, **fields) -> dict | None:
    """:func:`patch_room` when ``test(room)`` holds for the room as stored,
    tested under the same lock as the write. None when it does not (or the
    room is gone)."""
    with _LOCK:
        room = _load(room_id)
        if room is None or not test(room):
            return None
        room.update(fields)
        _write(room)
        return room


def record_interrupt(room_id: str, identity: str, pty_id: str = "") -> dict | None:
    """Record a person's terminal interrupt without sending the agent a message.

    It deliberately leaves the room status, reads, asks, and pending sends alone.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        event = {"from": HUMAN_IDENTITY, "identity": identity, "ptyId": pty_id, "ts": _now()}
        room["interrupts"] = (room.get("interrupts") or [])[-29:] + [event]
        room["updatedAt"] = event["ts"]
        _write(room)
        return event


def clear_exits(room_id: str) -> bool:
    """Forget every recorded exit on a room — the human has dealt with it.

    Same read-modify-write-under-the-lock as :func:`record_exit`, and for the
    same reason: the caller has been away killing processes (`taskkill /F /T`
    with a 5-second timeout apiece), and rewriting the whole room after that
    would silently drop any chat message posted in the meantime.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return False
        cleared = False
        for p in room.get("participants", []):
            if p.pop("lastExit", None) is not None:
                cleared = True
        if cleared:
            _write(room)
        return cleared


def _listed_copy(room: dict) -> dict:
    """A listed room: its own copy of everything but the messages, which come
    as a new list of the indexed message dicts (read them, never change them).
    Deep-copying every chat cost ~1.5 s a listing over ~580 rooms (#228)."""
    msgs = room.get("messages")
    out = copy.deepcopy({k: v for k, v in room.items() if k != "messages"})
    if msgs is not None:
        out["messages"] = list(msgs)
    return out


def list_rooms(*, copy_rooms: bool = True, revisions: dict | None = None) -> list[dict]:
    """List rooms; read-only hot paths may borrow nested indexed values.
    ``revisions`` gets each listed room's revision, taken with the room: a
    room_revision() read later may be that of a write made since (#256)."""
    entries = room_index()
    if revisions is not None:
        revisions.update((e["id"], e["signature"]) for e in entries)
    out = [_public(_listed_copy(e["room"]) if copy_rooms else e["room"])
           for e in entries]
    out.sort(key=lambda r: r.get("updatedAt", 0), reverse=True)
    return out


def get_room(room_id: str, public: bool = True, *, copy_room: bool = True,
             messages: bool = True) -> dict | None:
    # The GET path uses the index, while mutations keep using _read under
    # _LOCK. Copy so status decoration cannot change the indexed source.
    # The message dicts are shared even in a copy: read them, never change
    # one in place (replace it in the list), or the log keeps the old one.
    # A finished task's room is a card in the index: ``messages=False`` takes
    # the card (its record without messages), else the room is read whole.
    p = _room_path(room_id)
    try:
        st = p.stat()
    except OSError:
        return None
    with _INDEX_LOCK:
        hit = _INDEX.get(room_id)
        if not hit or hit["size"] != st.st_size or hit["mtime"] != st.st_mtime_ns:
            fresh = _read(room_id)
            if fresh is None:
                return None
            hit = _index_entry(room_id, fresh, st)
            _INDEX[room_id] = hit
            _CHANGES.pop(room_id, None)
        src = hit["room"]
        whole = hit.get("cold") and messages
        if not whole:
            r = _listed_copy(src) if copy_room else src
    if whole:
        # A carded room's file and log are read outside the index lock.
        src = _full_room(room_id, st)
        if src is None:
            return None
        r = _listed_copy(src) if copy_room else src
    if r is None:
        return None
    return _public(r) if public else r


def _public(room: dict) -> dict:
    """Room view without the secret tokens."""
    r = dict(room)
    r.pop("tokens", None)
    r.pop("_revision", None)
    return r


def delete_room(room_id: str) -> bool:
    with _LOCK:
        try:
            _room_path(room_id).unlink()
        except (FileNotFoundError, OSError):
            return False
        _LOGGED.discard(room_id)
        _DAMAGED.pop(room_id, None)
        with _INDEX_LOCK:
            _FULL.pop(room_id, None)
            if _CARDS.pop(room_id, None) is not None:
                _CARDS_STATE["dirty"] = True
        try:
            _log_path(room_id).unlink()
        except OSError:
            pass
        # Input provenance is room-owned auxiliary state. Keep its lifetime
        # aligned with the room without importing input_provenance here (that
        # module imports chatroom).
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", room_id or "")
        journal = ROOMS_DIR.parent / "input-provenance" / f"{safe}.jsonl"
        for path in (journal, journal.with_name(journal.name + ".tmp")):
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                pass
        return True


# ---------------------------------------------------------------------------
# Identity resolution (for the MCP transport)
# ---------------------------------------------------------------------------

# What a pruned task keeps (#260): enough for its card, its number, a link to
# it and what it delivered. Its chat, its seats' conversations and tokens go.
SLIM_KEEP = ("id", "title", "mode", "status", "createdAt", "updatedAt", "projectId", "noProjectId",
             "no", "previousNos", "workflow", "workflowAt", "mergedAt", "mergedHead", "priority",
             "spec", "lastReport", "lastRealReport", "taskDir", "cwd", "workspace", "stoppedAt",
             "kind", "labels", "after", "onReady")
SLIM_PART_KEEP = ("identity", "kind", "agent", "model", "role")


def slim_room(room_id: str, pruned: dict, notice: str) -> bool:
    """Make a Done task's room its slim record: the fields of SLIM_KEEP, its
    seats without their conversations, ``pruned`` (when, how much) and one hub
    line, ``notice``, in place of its chat. Its message log is rewritten to
    that line. False when there is no such room."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return False
        old = room.get("messages") or []
        slim = {k: room[k] for k in SLIM_KEEP if k in room}
        slim["participants"] = [{k: p[k] for k in SLIM_PART_KEEP if k in p}
                                for p in room.get("participants") or [] if isinstance(p, dict)]
        slim["tokens"] = {}
        slim["pruned"] = dict(pruned)
        slim["status"] = "stopped" if room.get("status") not in ("stopped", "closed") else room["status"]
        slim["messages"] = [{"id": uuid.uuid4().hex[:12], "from": "ensemble", "to": HUMAN_IDENTITY,
                             "text": notice, "ts": _now(), "kind": "notice", "rang": [],
                             "noticeKind": "pruned"}]
        _write(slim, removed=len(old))
        # The copy of the old chat a first log write keeps (#256): the chat is
        # dropped on purpose here, and the copy would never be let go (its log
        # lacks the messages it holds).
        try:
            (ROOMS_DIR / ORIGINALS_DIR_NAME / f"{room_id}.json").unlink()
        except FileNotFoundError:
            pass
        return True


def resolve_token(token: str) -> tuple[str, str] | None:
    """Map a bearer token to ``(room_id, identity)``, or None if unknown."""
    if not token:
        return None
    # The room index, not the files: parsing rooms until the token turned up
    # cost an agent's every tool call up to ~0.6 s under the store lock, by
    # where its room fell in the folder (#256). The room last found for a
    # token is looked at first; the index re-reads a room that changed.
    # The scan is outside the lock: room_index may save the cards file, which
    # it does with the lock released.
    rid = _TOKEN_ROOMS.get(token)
    if rid:
        room = get_room(rid, public=False, copy_room=False, messages=False)
        ident = (room or {}).get("tokens", {}).get(token)
        if ident:
            return rid, ident
    for e in room_index():
        ident = (e["room"].get("tokens") or {}).get(token)
        if ident:
            _TOKEN_ROOMS[token] = e["id"]
            return e["id"], ident
    _TOKEN_ROOMS.pop(token, None)
    return None


def participant(room: dict, identity: str) -> dict | None:
    for p in room.get("participants", []):
        if p.get("identity") == identity:
            return p
    return None


def agent_participants(room: dict) -> list[dict]:
    return [p for p in room.get("participants", []) if p.get("kind") == "agent"]


# ---------------------------------------------------------------------------
# Roles that decide who is woken
# ---------------------------------------------------------------------------

PRODUCT_OWNER_ROLE = "productowner"
BROADCAST = ("", "all", "everyone", "*")
REPORT_KINDS = ("completed", "blocked", "question", "update")

# The same shape session.html highlights: "@name" at the start, after a space
# or after "(".
_MENTION = re.compile(r"(?:^|[\s(])@([A-Za-z][\w-]*)")
_ENGINEER = re.compile(r"\bengineer\b")


def role_key(role: str) -> str:
    return (role or "").strip().lower().replace(" ", "")


def is_product_owner_part(part: dict) -> bool:
    return role_key((part or {}).get("role", "")) == PRODUCT_OWNER_ROLE


def _role_head(role: str) -> str:
    """A role's name without its charter: "designer and engineer: follows the
    skill…" → "designer and engineer"."""
    return (role or "").split(":", 1)[0].strip().lower()


def owners(room: dict) -> list[str]:
    """The agents a message to everyone wakes: the one agent of a solo task;
    otherwise every agent whose role is an engineer ("engineer", "designer and
    engineer"); failing that the ProductOwner; failing that every agent — a
    room of equal partners has no specialists to spare."""
    agents = agent_participants(room)
    if len(agents) <= 1:
        return [p["identity"] for p in agents]
    eng = [p["identity"] for p in agents if _ENGINEER.search(_role_head(p.get("role", "")))]
    if eng:
        return eng
    po = [p["identity"] for p in agents if is_product_owner_part(p)]
    return po or [p["identity"] for p in agents]


def po_identity(room: dict) -> str:
    """Who a report into this room is addressed to: its ProductOwner agent,
    else its owner. Empty for a room with no agents."""
    agents = agent_participants(room)
    po = next((p["identity"] for p in agents if is_product_owner_part(p)), "")
    if po:
        return po
    own = owners(room)
    return own[0] if own else ""


REVIEWER_ROLE = "reviewer"


def is_on_mention(room: dict, part: dict) -> bool:
    """Whether this agent is started fresh for each request instead of being
    kept running in the room: a reviewer on a team.

    Every wake of a long-lived session re-sends its whole conversation, and a
    resume reloads the same history, so a reviewer is launched only when a
    message wakes it (see :func:`wake_targets`), briefed from the task's spec,
    diff and review log, and ends once it has given its verdict. A task's only
    agent is its owner, never on mention, whatever its role says."""
    if (part or {}).get("kind") != "agent" or len(agent_participants(room)) < 2:
        return False
    return _role_head(part.get("role", "")) == REVIEWER_ROLE


def patch_participant(room_id: str, identity: str, fields: dict,
                      append: dict | None = None, drop: tuple = (),
                      room_fields: dict | None = None) -> dict | None:
    """Update one participant's record — read-modify-write under the room lock,
    so a chat message posted meanwhile is never lost (see :func:`record_exit`).
    ``append`` adds items to list fields; ``drop`` removes keys. Returns the
    updated participant, or None when the room or the participant is gone."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        part = participant(room, identity)
        if part is None:
            return None
        part.update(fields)
        for k, v in (append or {}).items():
            part.setdefault(k, []).append(v)
        for k in drop:
            part.pop(k, None)
        if room_fields:
            room.update(room_fields)
        room["updatedAt"] = _now()
        _write(room)
        return dict(part)


def apply_review_allocation(room_id: str, identity: str, agent: str, model: str,
                            allocation: dict, limit: int = 20) -> dict | None:
    """Atomically update a reviewer's kind/model and append its room audit.

    Review allocation happens on a live room, so rewriting a previously read
    room can discard chat posted during the allowance check.  Re-read, mutate
    and write under the room lock, and return the complete updated room the
    caller must use to build the review brief.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        part = participant(room, identity)
        if part is None:
            return None
        part.update(agent=agent, model=model)
        history = list(room.get("reviewAllocations") or [])
        history.append(allocation)
        room["reviewAllocations"] = history[-max(1, int(limit)):]
        room["updatedAt"] = _now()
        _write(room)
        return room


def mentions(room: dict, text: str) -> set[str]:
    """Agents @mentioned in ``text``, by identity ("@codex") or by role name
    ("@reviewer")."""
    names = {m.group(1).lower() for m in _MENTION.finditer(text or "")}
    if not names:
        return set()
    return {p["identity"] for p in agent_participants(room)
            if p["identity"].lower() in names or _role_head(p.get("role", "")) in names}


def wake_targets(room: dict, sender: str, to: str, text: str) -> list[str]:
    """The agents a message wakes, before any pause is taken into account.

    * Addressed to one participant: that participant, if it is an agent.
    * Addressed to everyone: the owners (:func:`owners`), plus any specialist
      the user, an owner or the PO @mentions. A specialist mentioned by
      another specialist stays asleep — that is how two reviewers talking
      about each other would otherwise wake each other forever.
    """
    others = [p["identity"] for p in agent_participants(room) if p["identity"] != sender]
    t = (to or "").strip()
    if t.lower() not in BROADCAST:
        return [t] if t in others else []
    own = set(owners(room))
    authority = sender == HUMAN_IDENTITY or sender in own or sender == po_identity(room)
    named = mentions(room, text) if authority else set()
    return [i for i in others if i in own or i in named]


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------

def post_message(room_id: str, sender: str, text: str, to: str = "",
                 wait_for_human: bool = True, structured_asks: list[dict] | None = None,
                 ask_audience: str = "") -> dict | None:
    """Append a message from ``sender`` to the room. ``to`` may be a specific
    participant identity or '' / 'all' to address every other participant.

    Returns a dict describing what the caller (dashboard) should do next:
        {message, recipients:[identity...], status, hopCount}
    ``recipients`` are the agent identities that should be rung; if the human is
    a recipient the room enters ``waiting_human`` and the relay pauses —
    unless ``wait_for_human`` is False: a task in a project with a PO asks
    nobody by a message to the person (attention._open_to_human), so the room
    stays active and nothing reads as waiting for them.
    """
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        to_norm = (to or "").strip()
        msg = {
            "id": uuid.uuid4().hex[:12],
            "from": sender,
            "to": to_norm,
            "text": text,
            "ts": _now(),
        }
        if structured_asks is not None:
            msg["asks"] = structured_asks
        if ask_audience:
            msg["askAudience"] = ask_audience
        room["messages"].append(msg)
        room["updatedAt"] = _now()

        # Who it is addressed to, and — separately — which agents it wakes. A
        # message to everyone reaches every reader, but wakes only the owner
        # and whoever was @mentioned (see wake_targets). An agent that is
        # addressed but not woken is not waiting on anything, so a broadcast
        # that wakes nobody is, in effect, a message to the human.
        idents = [p["identity"] for p in room["participants"]
                  if p["identity"] != sender]
        if to_norm and to_norm.lower() not in BROADCAST:
            recipients = [i for i in idents if i == to_norm]
        else:
            recipients = idents

        human_addressed = HUMAN_IDENTITY in recipients
        agent_recipients = wake_targets(room, sender, to_norm, text)
        msg["rang"] = agent_recipients

        if sender == HUMAN_IDENTITY:
            # Human spoke → reset the loop guard and resume autonomous relay.
            room["hopCount"] = 0
            room["status"] = "active"
            room["waitingFor"] = ""
        elif human_addressed and not agent_recipients and not wait_for_human:
            pass                # read in the chat; it asks nobody
        elif human_addressed and not agent_recipients:
            # An agent tagged ONLY the human → pause and wait for input.
            room["status"] = "waiting_human"
            room["waitingFor"] = HUMAN_IDENTITY
        else:
            # Agent → agent hand-off (a direct message, or a broadcast that also
            # cc's the human). Ring the addressed teammate and keep the relay
            # alive even if we were previously waiting on the human — the team is
            # actively collaborating (e.g. the engineer asks the PO for sign-off
            # AND hands the reviewer a deliverable in parallel). The hop guard
            # still caps runaway loops.
            room["hopCount"] = room.get("hopCount", 0) + 1
            if room["hopCount"] >= room.get("maxHops", DEFAULT_MAX_HOPS):
                room["status"] = "paused"
                room["waitingFor"] = HUMAN_IDENTITY
            else:
                room["status"] = "active"
                room["waitingFor"] = ""

        # Only ring agents when the room isn't paused / waiting on the human.
        ring = agent_recipients if room["status"] == "active" else []
        _write(room)
        return {
            "message": msg,
            "recipients": ring,
            "status": room["status"],
            "hopCount": room["hopCount"],
        }


def record_report(room_id: str, identity: str, kind: str, text: str,
                  routed_to: dict | None = None, heading: str = "",
                  clears: bool = False, structured_asks: list[dict] | None = None) -> dict | None:
    """Put a task agent's report on its own task: a chat message to the human
    (so the task's chat shows it) and ``lastReport`` (so the attention detector
    and the task tools can read it without walking the log).

    Deliberately leaves status and the hop count alone: a report is not a
    hand-off inside the team, and whether it leaves the task waiting on a human
    is the attention detector's call, which it makes from ``lastReport``.
    ``routed_to`` is ``{roomId, identity, project}`` of the PO it went to, or
    None when it went to the user. ``clears``: this report says the task's open
    ask to the person (a blocked, a question) is over, although nobody answered
    it in chat; without it an ``update`` leaves the ask open (attention.py).
    It and a ``completed`` are the reports that touch the status: either closes
    what the task had asked, so a room that was waiting on the person's reply
    to a message stops waiting (the completed report is then what the bell
    holds, by attention's own rule)."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        now = _now()
        msg = {"id": uuid.uuid4().hex[:12], "from": identity, "to": HUMAN_IDENTITY,
               "text": f"{heading}\n\n{text}" if heading else text, "ts": now,
               "kind": "report", "reportKind": kind, "rang": []}
        if routed_to:
            msg["reportTo"] = routed_to
        if structured_asks is not None:
            msg["asks"] = structured_asks
            msg["askAudience"] = "po" if routed_to else "user"
        if clears:
            msg["clears"] = True
        if clears or kind == "completed":
            _wait_for_human_over(room)      # either closes what it had asked
        room.setdefault("messages", []).append(msg)
        room["lastReport"] = {"kind": kind, "text": text[:4000], "ts": now,
                              "identity": identity, "messageId": msg["id"],
                              "to": routed_to or {"identity": HUMAN_IDENTITY}}
        # An update (a rotation, a handover, "working again") says nothing the
        # PO must act on: the last completed / question / blocked survives it.
        if kind != "update":
            room["lastRealReport"] = room["lastReport"]
        room["updatedAt"] = now
        _write(room)
        return msg


def _wait_for_human_over(room: dict) -> None:
    """The room was waiting on the person's reply to an agent's message
    (``waiting_human``, see :func:`post_message`) and that ask has closed
    without them speaking in the chat: the room is not waiting any more, so
    the bell and the lists agree with the ask's own line. A pause at the hop
    limit is another matter and stays."""
    if room.get("status") == "waiting_human":
        room["status"] = "active"
        room["waitingFor"] = ""


def record_answer(room_id: str, identity: str, at: float) -> dict | None:
    """A person answered a one-agent task's open ask in its terminal, at
    ``at``: kept on the participant (``answeredAt``, read by
    attention._open_to_human), and the room stops waiting on them. Returns the
    participant, or None when the room or the participant is gone."""
    with _LOCK:
        room = _load(room_id)
        part = participant(room, identity) if room is not None else None
        if part is None:
            return None
        part["answeredAt"] = at
        _wait_for_human_over(room)
        room["updatedAt"] = _now()
        _write(room)
        return dict(part)


def record_seen(room_id: str, ts: float) -> float | None:
    """The person read the room's chat up to the message of time ``ts`` (the
    page's read point, session.html ``markRead``): kept on the room as
    ``seenAt``, which only moves forward and never past now. What the person
    has read leaves Needs you on attention's rules (a finished report at
    once, an ask a day after it was put). ``updatedAt`` is left alone: reading
    is not news. Returns the room's ``seenAt``, or None when the room is gone."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        try:
            ts = min(float(ts), _now())
        except (TypeError, ValueError):
            return None
        seen = float(room.get("seenAt") or 0)
        if ts > seen:
            room["seenAt"] = seen = ts
            _write(room)
        return seen


def record_dealt(room_id: str) -> float | None:
    """The person is done with what the room has put to them so far (Done
    with this, on the list's Details): ``dealtAt`` is now, and the chat counts
    as read up to now as well. What it asked leaves Needs you at once on every
    device (attention.set_aside); a later ask is news again. Returns the
    room's ``dealtAt``, or None when the room is gone."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        now = _now()
        room["dealtAt"] = now
        room["seenAt"] = max(float(room.get("seenAt") or 0), now)
        _write(room)
        return now


def dealt_marks(room_id: str) -> dict | None:
    """The room's ``{dealtAt, seenAt}`` as they are, for Clear all's Undo
    (#279), or None when the room is gone."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        return {"dealtAt": float(room.get("dealtAt") or 0), "seenAt": float(room.get("seenAt") or 0)}


def restore_dealt(room_id: str, marks: dict) -> bool:
    """Undo of Clear all (#279): the room's ``dealtAt`` and ``seenAt`` go back
    to ``marks`` (what :func:`dealt_marks` gave before). Only back, never
    forward: a mark later than the room's own is ignored. False when the room
    is gone or nothing changed."""
    def num(v):
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return None
    dealt, seen = num((marks or {}).get("dealtAt")), num((marks or {}).get("seenAt"))
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return False
        changed = False
        for key, val in (("dealtAt", dealt), ("seenAt", seen)):
            if val is not None and val < float(room.get(key) or 0):
                if val:
                    room[key] = val
                else:
                    room.pop(key, None)
                changed = True
        if changed:
            _write(room)
        return changed


def last_real_report(room: dict) -> dict:
    """The task's last report that is not an ``update``, or {}. A task that
    reported before ``lastRealReport`` was kept falls back to ``lastReport``
    when that is not an update."""
    rep = room.get("lastRealReport")
    if isinstance(rep, dict):
        return rep
    rep = room.get("lastReport")
    return rep if isinstance(rep, dict) and rep.get("kind") != "update" else {}


def post_report(room_id: str, sender: str, to: str, text: str, meta: dict,
                wake: bool = True) -> dict | None:
    """Deliver a report from another task into this (the PO's) room, addressed
    to ``to``. Returns the same shape as :func:`post_message`.

    Unlike a chat hand-off it wakes its addressee — being woken by the tasks it
    runs is the PO's whole job — and it leaves the PO room's status and hop
    count alone: the report came from outside the room, so it is neither a turn
    inside it nor a loop the guard could stop. ``wake=False`` posts a note that
    asks nothing of the PO (a task owner's rotation): it is shown in the room
    but rings nobody, so the attention detector never waits for an answer."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": to, "text": text,
               "ts": _now(), "kind": "report", **meta}
        is_agent = any(p.get("identity") == to for p in agent_participants(room))
        msg["rang"] = [to] if is_agent and wake else []
        room.setdefault("messages", []).append(msg)
        room["updatedAt"] = _now()
        _write(room)
        return {"message": msg, "recipients": msg["rang"],
                "status": room.get("status", "active"), "hopCount": room.get("hopCount", 0)}


def post_po_message(room_id: str, sender: str, to: str, text: str, meta: dict) -> dict | None:
    """A message between two projects' POs (``po_messages``), as one of the
    two rooms keeps it: ``meta`` carries its id (the same in both rooms), its
    projects and its direction. Rings nobody — the hub types the target PO a
    line of its own — and leaves status and the hop count alone, like a
    report. Returns the message, or None if there is no room."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        msg = {"from": sender, "to": to, "text": text, "ts": _now(), "rang": [],
               **meta, "kind": "pomsg"}
        msg.setdefault("id", uuid.uuid4().hex[:12])
        room.setdefault("messages", []).append(msg)
        room["updatedAt"] = _now()
        _write(room)
        return msg


def post_notice(room_id: str, sender: str, text: str, meta: dict) -> dict | None:
    """A line from the hub itself into a room, for the human — e.g. that the
    room's PO was rotated to a fresh session. Wakes nobody and leaves status
    and the hop count alone. Returns the message, or None if there is no room."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": HUMAN_IDENTITY,
               "text": text, "ts": _now(), "kind": "notice", "rang": [], **meta}
        room.setdefault("messages", []).append(msg)
        room["updatedAt"] = _now()
        _write(room)
        return msg


def read_messages(room_id: str, since_ts: float = 0.0, for_identity: str = "") -> list[dict]:
    """Return messages after ``since_ts``. If ``for_identity`` is given, only
    messages that identity should see (addressed to it, to all, or its own)."""
    room = _load(room_id)
    if room is None:
        return []
    out = []
    for m in room.get("messages", []):
        if m["ts"] <= since_ts:
            continue
        if for_identity:
            to = (m.get("to") or "").lower()
            if (m["from"] != for_identity and to not in ("", "all", "everyone", "*")
                    and m.get("to") != for_identity):
                continue
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# MCP tool surface (exposed to agents via the dashboard's MCP endpoint)
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "chat_send",
        "description": (
            "Send a message to your collaboration partner(s) or the user. This "
            "is how you hand off your turn: after you finish a step of thinking "
            "or work, send your partner your findings/critique/proposal. To pull "
            "the user in for a decision, question, or clarification, set "
            "to=\"user\" — that pauses the collaboration until they reply. A "
            "message to everyone wakes only the task's owner (the engineer); "
            "to wake a reviewer or another specialist, address it with `to` or "
            "@mention it (\"@reviewer\", \"@codex\"). Mention a reviewer only "
            "for a commit to review or a specific question — never for a plan, "
            "acknowledgement, thanks, or verdict restatement."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string",
                            "description": "The message text to send."},
                "to": {"type": "string",
                       "description": "Recipient identity: your partner's name, "
                                      "\"user\" for the user, or omit / \"all\" "
                                      "to address everyone."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "chat_read",
        "description": (
            "Read new messages addressed to you in the shared room since you "
            "last read. Call this whenever you're notified a message arrived. "
            "Returns each message's sender and text."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "chat_whoami",
        "description": (
            "Return your identity, your collaboration partner(s), and the room's "
            "current status (active / waiting on the user / paused)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# A slash command the person ran from the chat and what it printed (#253):
# notices (``noticeKind``), the chat's record of what went into a terminal,
# never news for an agent; and the line saying the hub closed a menu left open.
COMMAND_KINDS = ("command", "commandOutput", "menuClosed")


def is_command_record(msg: dict) -> bool:
    return msg.get("kind") == "notice" and msg.get("noticeKind") in COMMAND_KINDS


def _visible_to(msg: dict, identity: str) -> bool:
    if is_command_record(msg):
        return False
    to = (msg.get("to") or "").lower()
    return (msg["from"] == identity or to in ("", "all", "everyone", "*")
            or msg.get("to") == identity)


def withdraw_message(room_id: str, msg_id: str, sender: str = HUMAN_IDENTITY) -> bool:
    """Take back a message of ``sender``'s that none of the agents it reaches
    has read yet (chat_read moves past it): the Undo of a quick answer sent by
    mistake (#198). False when it is not there, or one of them read it."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return False
        msgs = room.get("messages", [])
        m = next((x for x in msgs if x.get("id") == msg_id and x.get("from") == sender), None)
        if m is None:
            return False
        reads = room.get("reads") or {}
        for p in room.get("participants", []):
            if (p.get("kind") == "agent" and _visible_to(m, p["identity"])
                    and float(reads.get(p["identity"], 0.0)) >= m["ts"]):
                return False
        msgs.remove(m)
        room["updatedAt"] = _now()
        _write(room, removed=1)
        return True


def read_new_for(room_id: str, identity: str) -> list[dict]:
    """Return messages visible to ``identity`` that it hasn't read yet, and
    advance its read cursor. Backs the ``chat_read`` tool."""
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return []
        reads = room.setdefault("reads", {})
        cursor = reads.get(identity, 0.0)
        msgs = [m for m in room.get("messages", [])
                if m["ts"] > cursor and _visible_to(m, identity)]
        if room.get("messages"):
            reads[identity] = room["messages"][-1]["ts"]
            _write(room)
        return msgs


def whoami(room_id: str, identity: str) -> dict:
    room = _load(room_id)
    if room is None:
        return {}
    partners = [p["identity"] for p in room["participants"]
                if p["identity"] != identity and p.get("kind") == "agent"]
    return {
        "you": identity,
        "partners": partners,
        "human": HUMAN_IDENTITY,
        "status": room.get("status", ""),
        "title": room.get("title", ""),
    }


def set_status(room_id: str, status: str) -> dict | None:
    with _LOCK:
        room = _load(room_id)
        if room is None:
            return None
        room["status"] = status
        if status == "active":
            room["hopCount"] = 0
            room["waitingFor"] = ""
        room["updatedAt"] = _now()
        _write(room)
        return _public(room)
