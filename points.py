"""The person's points: every point they raise in a chat is kept by the hub
from the moment they send it until they have acknowledged its answer.

The CEO asked questions whose answers he could not find again: folded, lost
in a flood of task reports, or forgotten by a PO that was rotated (2026-09-21).
So the hub, not the conversation, holds the list. It is the mirror image of an
ask from a task to the person (``attention._open_to_human``, #74).

**A point** is one message the person sends to an agent through a chat (the
page's box, or one comment of a "## Review comments (N)" message: each comment
is its own point). It gets an id numbered per room, ``P12``, and a state:

* ``open``: no answer yet;
* ``answered``: an answer is linked, the person has not acknowledged it;
* ``acked``: they acknowledged it (thumbs up, Ack, or a bare "thanks");
* ``dropped``: they dismissed it themselves;
* ``split``: a PO split it into sub-points (``P12a``, ``P12b``), which carry it.

**Not points** (the rule is written down so it stays simple): a line starting
with ``/`` (an agent command), the hub's own lines and the PO's, a bare
acknowledgement ("ok", "thanks", "got it" … see :func:`is_bare_ack`), which
acknowledges what was answered since the person last spoke, the approval a
thumbs up on a decision types, and anything typed into a terminal. A message
that starts ``Re P12:`` is a follow-up on P12: it reopens P12 (same id) rather
than making a new point. Anything else is a point: when in doubt it is one, and
the person drops it with one click.

**Delivery.** The hub types each point to its agent with one line under it,
``[point P12]`` (under each comment for several). **Answers** are read from
what the agent wrote, for Claude and Codex alike (the transcript of a
one-agent chat, the room's messages of a team):

* the first reply after a message holding a single point, with no hub input
  in between, answers it (the run of replies up to the next input is one
  answer, linked to its last balloon) — not after a message the agent read
  while busy, which is followed by more of the work it was doing;
* a paragraph starting ``Re P12:`` answers P12 (several per reply);
* ``ensemble_points`` answers one by doing ("P12: started as #75").

A reply to a ``[report]``, ``[digest]``, ``[due]``, ``[handover]`` or restart
note answers nothing unless it says ``Re Pn:``.

**Storage.** ``points/<room>.json`` beside the rooms folder, written
atomically, with the version before kept as ``.prev``; a damaged file falls
back to it. Survives a hub restart, a rotation and a resume, which only change
the agent's session: the list of sessions read is the room's.

Bound to the dashboard module like ``due``: nothing reads ``_d`` at import time.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


PREFIX = "[points] "            # how the hub's lines about points start (HUB_INPUT_KINDS)
TICK_S = 60                     # how often open points are looked at for a reminder
DEFAULT_REMIND_MIN = 20         # settings.pointsRemindMin: open this long, the agent idle → one line
SYNC_EVERY_S = 3.0              # a room's answers are looked for at most this often
SLACK_S = 120                   # a transcript's clock and the hub's may differ this much
VIEW_MAX = 200                  # points a page is sent, newest first
TEXT_MAX = 2000                 # a point's words kept
_WAKE_MAX = 900                 # a typed line: a TUI takes one line
_PROMPT_MAX = 2500              # the open points in a first prompt (a command line)
_WORDS = 60                     # a point's first words in a line

_LOCK = threading.RLock()
_CACHE: dict[str, tuple] = {}   # room id -> (mtime, ledger): what is on disk
_SYNCED: dict[str, float] = {}  # room id -> when its answers were last looked for
_ADOPT_SEEN: set[str] = set()   # rooms whose last unanswered message was looked at
_SCANNED: dict[tuple, object] = {}  # (room, session or "~room") -> what it was when read
_LAST_TICK = 0.0

POINT_ID = r"P\d{1,5}[a-z]?"
_POINT_LINE = re.compile(rf"^\[point ({POINT_ID})\][ \t]*$", re.M)
_POINT_LINE_STRIP = re.compile(rf"\n*^\[point {POINT_ID}\][ \t]*$", re.M)
# "Re P12:", "**Re P12, P14:**", "- Re P3 and P4:" at the start of a line.
_RE_HEAD = re.compile(
    rf"^[ \t]*(?:[-*+>][ \t]+)*(?:\*\*|__)?[ \t]*re[ \t]+"
    rf"({POINT_ID}(?:[ \t]*(?:,|&|/|and)[ \t]*{POINT_ID})*)[ \t]*(?:\*\*|__)?[ \t]*:",
    re.M | re.I)
_ID_IN = re.compile(POINT_ID, re.I)
_COMMENTS_HEAD = re.compile(r"^## Review comments \(\d+\)")
_COMMENT_ITEM = re.compile(r"^\*\*\d+\.\*\*")
_FENCE = re.compile(r"^\s*(```|~~~)")
# A bare acknowledgement: every word is one of these, and there are few.
_ACK_WORDS = frozenset("""
ok okay k kk thanks thank you thx ty cheers great perfect got it noted nice cool
good fine understood ack acknowledged clear all right alright super excellent
very much many lovely brilliant makes sense sounds wonderful 👍 🙏 👌 ✅
""".split())
_WORD = re.compile(r"[\w']+|[^\w\s]", re.U)


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] points: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Reading text
# ---------------------------------------------------------------------------

def _norm_id(i: str) -> str:
    i = i.strip()
    return "P" + i[1:].lower() if i[:1] in "pP" else i


def point_ids(text: str) -> list[str]:
    """The ``[point Pn]`` lines of a delivered message, in order."""
    out = []
    for m in _POINT_LINE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def strip_point_lines(text: str) -> str:
    return _POINT_LINE_STRIP.sub("", text or "").strip()


def re_ids(text: str) -> list[str]:
    """The points a reply answers by ``Re Pn:`` at the start of a paragraph."""
    out = []
    for m in _RE_HEAD.finditer(text or ""):
        for i in _ID_IN.findall(m.group(1)):
            i = _norm_id(i)
            if i not in out:
                out.append(i)
    return out


def is_bare_ack(text: str) -> bool:
    """"ok", "thanks!", "got it, thanks 👍": an acknowledgement and nothing else."""
    t = (text or "").strip().lower()
    if not t or len(t) > 60 or "\n" in t:
        return False
    words = [w for w in _WORD.findall(t) if w not in ".,!?-–—:;)(" and w.strip()]
    return 0 < len(words) <= 6 and all(w in _ACK_WORDS for w in words)


def _comment_items(text: str) -> tuple[str, list[str]] | None:
    """A "## Review comments (N)" message as (its head, each comment), or None
    when it is not one. Split at ``**N.**`` lines outside code fences."""
    if not _COMMENTS_HEAD.match(text or ""):
        return None
    lines = text.split("\n")
    starts, fence = [], None
    for i, ln in enumerate(lines):
        f = _FENCE.match(ln)
        if f:
            fence = None if fence == f.group(1) else (fence or f.group(1))
            continue
        if fence is None and _COMMENT_ITEM.match(ln):
            starts.append(i)
    if not starts:
        return None
    head = "\n".join(lines[:starts[0]]).rstrip()
    items = ["\n".join(lines[a:b]).strip() for a, b in zip(starts, starts[1:] + [len(lines)])]
    return head, items


def _first_words(text: str, n: int = _WORDS) -> str:
    t = " ".join(strip_point_lines(text).split())
    t = re.sub(r"^\*\*\d+\.\*\*\s*", "", t)
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def _deliverable(text: str, ids: list[str]) -> str:
    """The message as typed to the agent: a ``[point Pn]`` line under it, or
    under each comment of a review-comments message."""
    if not ids:
        return text
    split = _comment_items(text)
    if split and len(split[1]) == len(ids):
        head, items = split
        return head + "\n\n" + "\n\n".join(f"{it}\n\n[point {i}]" for it, i in zip(items, ids))
    return text.rstrip() + "\n\n" + "\n".join(f"[point {i}]" for i in ids)


# ---------------------------------------------------------------------------
# The ledger on disk
# ---------------------------------------------------------------------------

def _dir() -> Path:
    # Beside the rooms, wherever they are kept.
    return Path(_d.chatroom.ROOMS_DIR).parent / "points"


def _path(room_id: str) -> Path:
    return _dir() / f"{room_id}.json"


def _empty(room_id: str) -> dict:
    return {"version": 1, "roomId": room_id, "next": 1, "points": [],
            "approvals": {}, "lastPersonAt": 0.0}


def _valid(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("points"), list)


def _clean(led: dict, room_id: str) -> dict:
    """Whatever is not a point is dropped, and the counter never goes back
    below a number in use."""
    out = _empty(room_id)
    if isinstance(led.get("approvals"), dict):
        out["approvals"] = led["approvals"]
    try:
        out["lastPersonAt"] = float(led.get("lastPersonAt") or 0)
    except (TypeError, ValueError):
        pass
    pts = [p for p in led["points"] if isinstance(p, dict) and isinstance(p.get("id"), str)
           and isinstance(p.get("n"), int)]
    for p in pts:
        p.setdefault("answers", [])
        p.setdefault("followUps", [])
        p.setdefault("state", "open")
        p.setdefault("createdAt", 0.0)
    out["points"] = pts
    try:
        nxt = int(led.get("next") or 1)
    except (TypeError, ValueError):
        nxt = 1
    out["next"] = max([nxt] + [p["n"] + 1 for p in pts])
    if led.get("damaged"):
        out["damaged"] = led["damaged"]
    return out


def _read_file(p: Path):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if _valid(d) else None


def load(room_id: str) -> dict:
    """A room's ledger (a fresh one when it has none). A damaged file falls
    back to the version before it, kept as ``.prev``; if both are gone the
    ledger starts again, numbering after the last it can still see, and
    ``damaged`` says when."""
    with _LOCK:
        p = _path(room_id)
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            mt = None
        hit = _CACHE.get(room_id)
        if hit and hit[0] == mt and mt is not None:
            return json.loads(json.dumps(hit[1]))
        if mt is None:
            prev = _read_file(p.with_suffix(".json.prev"))
            return _clean(prev, room_id) if prev else _empty(room_id)
        d = _read_file(p)
        if d is None:
            prev = _read_file(p.with_suffix(".json.prev"))
            _log(f"{room_id}: the ledger could not be read"
                 + ("; the version before it is used" if prev else "; it starts again"))
            try:
                p.replace(p.with_name(f"{p.name}.damaged-{int(time.time())}"))
            except OSError:
                pass
            d = prev or {**_empty(room_id), "damaged": time.time()}
            led = _clean(d, room_id)
            _save(room_id, led)
            return json.loads(json.dumps(led))
        led = _clean(d, room_id)
        _CACHE[room_id] = (mt, led)
        return json.loads(json.dumps(led))


def _save(room_id: str, led: dict) -> None:
    p = _path(room_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(led, indent=1, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            if p.exists():
                try:
                    os.replace(p, p.with_suffix(".json.prev"))
                except OSError:
                    pass
            os.replace(tmp, p)
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    try:
        _CACHE[room_id] = (p.stat().st_mtime_ns, json.loads(json.dumps(led)))
    except OSError:
        _CACHE.pop(room_id, None)
    try:
        _d.invalidate_session_listing()
    except Exception:
        pass


def exists(room_id: str) -> bool:
    return _path(room_id).exists() or _path(room_id).with_suffix(".json.prev").exists()


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _agents(room: dict) -> list[dict]:
    return [p for p in room.get("participants", []) if p.get("kind") == "agent"]


def _solo(room: dict) -> bool:
    return room.get("mode") == "solo" or len(_agents(room)) == 1


def _owner_for(room: dict, to: str) -> str:
    """Whose point it is: the one agent addressed, else the room's owner."""
    idents = [p.get("identity", "") for p in _agents(room)]
    if to and to in idents:
        return to
    try:
        own = _d.chatroom.owners(room)
    except Exception:
        own = []
    return (own[0] if own else (idents[0] if idents else ""))


def _find(led: dict, pid: str) -> dict | None:
    return next((p for p in led["points"] if p["id"] == pid), None)


def _new_point(led: dict, text: str, owner: str, now: float, key: str, sid: str,
               n: int | None = None, sub: str = "", parent: str = "", mid: str = "") -> dict:
    if n is None:
        n = led["next"]
        led["next"] = n + 1
    p = {"id": f"P{n}{sub}", "n": n, "text": text[:TEXT_MAX], "owner": owner,
         "state": "open", "createdAt": now, "stateAt": now, "openedAt": now,
         "key": key, "mid": mid, "sessionId": sid, "answers": [], "followUps": [],
         "reminded": 0.0}
    if parent:
        p["parent"] = parent
    led["points"].append(p)
    return p


def _set_state(p: dict, state: str, now: float) -> bool:
    if p.get("state") == state:
        return False
    p["state"] = state
    p["stateAt"] = now
    if state == "open":
        p["openedAt"] = now
        p["reminded"] = 0.0
    return True


def _answer(p: dict, key: str, mid: str, at: float, how: str, summary: str = "") -> bool:
    """Link an answer. A new one moves an open point to answered; an answer
    already linked only follows its run to its latest balloon."""
    for a in p["answers"]:
        if a.get("key") == key:
            if a.get("mid") != mid and mid:
                a["mid"], a["at"] = mid, at
                return True
            return False
    p["answers"].append({"key": key, "mid": mid, "at": at, "how": how,
                         **({"summary": summary[:300]} if summary else {})})
    if p.get("state") == "open":
        _set_state(p, "answered", at or time.time())
    return True


def _see_balloon(p: dict, mid: str, sid: str = "") -> bool:
    if not mid:
        return False
    if not p.get("mid"):
        p["mid"] = mid
        if sid:
            p["sessionId"] = sid
        return True
    if p["mid"] != mid and mid not in p["followUps"]:
        p["followUps"].append(mid)
        return True
    return False


def _live(led: dict) -> dict:
    return {p["id"]: p for p in led["points"]}


# ---------------------------------------------------------------------------
# A message from the person
# ---------------------------------------------------------------------------

def take(room: dict, text: str, to: str = "", key: str = "", now: float | None = None,
         sid: str = "") -> tuple[str, list[str]]:
    """The person sends ``text`` to ``to`` (an agent, or everyone). Returns
    (the text to deliver, the points it carries). A new message becomes one
    point, a review-comments message one per comment; ``Re Pn:`` reopens Pn;
    a bare acknowledgement acknowledges what was answered since they last
    spoke and carries nothing. The same ``key`` again (a retried send) is the
    same points, never new ones."""
    now = time.time() if now is None else now
    rid = room.get("id", "")
    body = (text or "").strip()
    if not rid or not body or body.startswith("/") or _d.hub_input_kind(body)["kind"] != "human" \
            or body.startswith(_d.PO_MESSAGE_PREFIX):
        return text, []
    try:
        sync(rid, force=True)
    except Exception as e:      # answers are looked for again at the next poll
        _log(f"{rid}: answers not read before a new message: {e!r}")
    with _LOCK:
        led = load(rid)
        if key:
            again = sorted((p for p in led["points"] if p.get("key") == key), key=lambda p: p["createdAt"])
            if again:
                return _deliverable(text, [p["id"] for p in again]), [p["id"] for p in again]
        pts = _live(led)
        owner = _owner_for(room, to)
        if not sid and _solo(room):
            part = next(iter(_agents(room)), {})
            sid = part.get("sessionId", "") or ""
        follow = [i for i in re_ids(body) if i in pts]
        if follow:
            for i in follow:
                p = pts[i]
                if p["state"] in ("answered", "acked", "dropped"):
                    _set_state(p, "open", now)
                    p["reopenedAt"] = now
                p["followKey"] = key or p.get("followKey", "")
            led["lastPersonAt"] = now
            _save(rid, led)
            return _deliverable(text, follow), follow
        if is_bare_ack(body):
            since = float(led.get("lastPersonAt") or 0)
            for p in led["points"]:
                if p["state"] == "answered" and any(a.get("at", 0) > since for a in p["answers"]):
                    _set_state(p, "acked", now)
                    p["ackedBy"] = "reply"
            led["lastPersonAt"] = now
            _save(rid, led)
            return text, []
        split = _comment_items(body)
        texts = split[1] if split else [body]
        made = [_new_point(led, t, owner, now, key, sid) for t in texts]
        if split:
            for p in made:
                p["comment"] = True
        led["lastPersonAt"] = now
        _save(rid, led)
        ids = [p["id"] for p in made]
        return _deliverable(text, ids), ids


def discard(room_id: str, ids: list[str]) -> None:
    """Take back the points of a send the hub refused and does not hold: the
    text is still in the person's box, and its next attempt makes them again.
    Numbers are not reused."""
    if not ids:
        return
    with _LOCK:
        led = load(room_id)
        before = len(led["points"])
        led["points"] = [p for p in led["points"]
                         if not (p["id"] in ids and p["state"] == "open" and not p["answers"])]
        if len(led["points"]) != before:
            _save(room_id, led)


# ---------------------------------------------------------------------------
# Finding answers
# ---------------------------------------------------------------------------

def _session_ids(room: dict, led: dict) -> list[str]:
    sids: list[str] = []

    def add(s):
        s = (s or "").strip()
        if s and s not in sids:
            sids.append(s)
    for part in _agents(room):
        for r in part.get("rotations") or []:
            if isinstance(r, dict):
                add(r.get("fromSessionId"))
                add(r.get("toSessionId"))
        add(part.get("sessionId"))
    for p in led["points"]:
        add(p.get("sessionId"))
    return sids


def _session_stat(sid: str) -> list | None:
    try:
        tp = _d.find_transcript(sid)
        if tp:
            st = Path(tp).stat()
            return [st.st_size, round(st.st_mtime, 3)]
        cx = _d.agents.get_agent("codex")
        st = cx.session_stat(sid) if cx is not None else None
        return [st["size"], round(st["mtime"], 3)] if st else None
    except (OSError, TypeError, KeyError, AttributeError):
        return None


def _scan_turns(led: dict, sid: str, turns: list[dict]) -> bool:
    """One session's turns (a one-agent chat): link each point's balloon, and
    the answers to it."""
    pts = _live(led)
    if not pts:
        return False
    changed = False
    implicit, run = None, ""
    seen: set[tuple] = set()        # (point, answer key) found in this reading
    for mid, t in _d.page_turn_ids(sid, turns):
        ts = _d._turn_epoch(t.get("timestamp"))
        text = t.get("text") or ""
        if t.get("role") == "user":
            implicit = None
            if (t.get("kind") or "human") != "human":
                continue
            found = [i for i in point_ids(text)
                     if i in pts and (not ts or ts >= pts[i]["createdAt"] - SLACK_S)]
            for i in found:
                changed |= _see_balloon(pts[i], mid, sid)
            # A line read while the agent was busy is not followed by its
            # reply but by more of the work it was doing: only Re Pn: answers.
            if len(found) == 1 and not t.get("queued"):
                implicit, run = found[0], mid
            continue
        said = [i for i in re_ids(text) if i in pts and (not ts or ts >= pts[i]["createdAt"] - SLACK_S)]
        for i in said:
            changed |= _answer(pts[i], mid, mid, ts, "re")
            seen.add((i, mid))
        if implicit and implicit not in said:
            changed |= _answer(pts[implicit], "after:" + run, mid, ts, "implicit")
            seen.add((implicit, "after:" + run))
        elif implicit in said:
            implicit = None
    # The whole session was read: an answer linked in it before that it does
    # not hold now (its turns were counted otherwise then) goes. The state
    # stays what it became.
    head = sid + ":"
    for p in pts.values():
        keep = [a for a in p["answers"]
                if not (str(a.get("key", "")).startswith((head, "after:" + head)))
                or (p["id"], a.get("key")) in seen]
        if len(keep) != len(p["answers"]):
            p["answers"] = keep
            changed = True
    return changed


def _scan_messages(led: dict, room: dict) -> bool:
    """The room's messages: a team's points and their answers, and in any
    room an answer an agent gave in a report or a message (``Re Pn:``)."""
    pts = _live(led)
    if not pts:
        return False
    floor = min(p["createdAt"] for p in pts.values()) - SLACK_S
    agents = {p.get("identity", "") for p in _agents(room)}
    team = not _solo(room)
    changed = False
    implicit, ukey = None, ""
    human = _d.chatroom.HUMAN_IDENTITY
    for m in room.get("messages") or []:
        try:
            ts = float(m.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts < floor:
            continue
        frm, mid, text = m.get("from", ""), m.get("id", ""), m.get("text") or ""
        if frm == human:
            implicit = None
            found = [i for i in point_ids(text) if i in pts and ts >= pts[i]["createdAt"] - SLACK_S]
            for i in found:
                changed |= _see_balloon(pts[i], mid)
            if team and len(found) == 1:
                implicit, ukey = found[0], mid
            continue
        if frm not in agents:
            implicit = None             # the hub, another task's report: not a reply
            continue
        said = [i for i in re_ids(text) if i in pts and ts >= pts[i]["createdAt"] - SLACK_S]
        for i in said:
            changed |= _answer(pts[i], mid, mid, ts, "re")
        if not implicit:
            continue
        if implicit in said:
            implicit = None
            continue
        if m.get("kind") in ("report", "notice"):
            implicit = None
            continue
        to = (m.get("to") or "").strip().lower()
        if frm == pts[implicit].get("owner") and to in ("user", "", "all", "everyone", "*"):
            changed |= _answer(pts[implicit], "after:" + ukey, mid, ts, "implicit")
            implicit = None
    return changed


def _adopt(led: dict, room: dict) -> bool:
    """A room seen for the first time: the person's last message, if nothing
    answered it, is taken in as an open point. Nothing older is."""
    human = _d.chatroom.HUMAN_IDENTITY
    agents = _agents(room)
    if not agents or not room.get("launched", True):
        return False
    owner = _owner_for(room, "")
    if _solo(room):
        sid = (agents[0].get("sessionId") or "").strip()
        turns = _d.read_session_turns(sid) if sid else None
        ids = _d.page_turn_ids(sid, turns or [])
        last = next(((mid, t) for mid, t in reversed(ids)
                     if not (t.get("role") == "user" and (t.get("kind") or "human") != "human")), None)
        if not last or last[1].get("role") != "user":
            return False
        mid, t = last
        text = strip_point_lines(_d.message_refs.strip_message_refs(t.get("text") or ""))
        if not text or text.startswith("/") or is_bare_ack(text) or text.startswith(_d.PO_MESSAGE_PREFIX):
            return False
        p = _new_point(led, text, owner, _d._turn_epoch(t.get("timestamp")) or time.time(), "", sid, mid=mid)
    else:
        msgs = [m for m in room.get("messages") or [] if m.get("kind") not in ("notice",)]
        if not msgs or msgs[-1].get("from") != human:
            return False
        m = msgs[-1]
        text = (m.get("text") or "").strip()
        if not text or is_bare_ack(text):
            return False
        p = _new_point(led, text, _owner_for(room, m.get("to", "")), float(m.get("ts") or time.time()),
                       "", "", mid=m.get("id", ""))
    p["adopted"] = True
    return True


def sync(room_id: str, force: bool = False, room: dict | None = None) -> dict:
    """Look for new answers in the room's conversations (only those that
    changed since last time), and return the ledger."""
    now = time.time()
    if not force and now - _SYNCED.get(room_id, 0) < SYNC_EVERY_S:
        return load(room_id)
    _SYNCED[room_id] = now
    if room is None:
        room = _d.chatroom.get_room(room_id)
    if room is None:
        return load(room_id)
    with _LOCK:
        led = load(room_id)
        changed = False
        if not led["points"] and not exists(room_id):
            if room_id in _ADOPT_SEEN:
                return led
            _ADOPT_SEEN.add(room_id)
            try:
                changed = _adopt(led, room)
            except Exception as e:
                _log(f"{room_id}: the last message was not read: {e!r}")
            if not changed:
                return led
        # What was read is remembered here, not on disk: after a restart
        # everything is read once more, and linking again changes nothing.
        if _solo(room):
            for sid in _session_ids(room, led):
                st = _session_stat(sid)
                if st is None or _SCANNED.get((room_id, sid)) == st:
                    continue
                turns = _d.read_session_turns(sid) or []
                changed |= _scan_turns(led, sid, turns)
                _SCANNED[(room_id, sid)] = st
        msgs = room.get("messages") or []
        mark = (len(msgs), msgs[-1].get("id", "") if msgs else "", len(led["points"]))
        if _SCANNED.get((room_id, "~room")) != mark:
            changed |= _scan_messages(led, room)
            _SCANNED[(room_id, "~room")] = mark
        if changed:
            _save(room_id, led)
        return led


# ---------------------------------------------------------------------------
# What the person does
# ---------------------------------------------------------------------------

ACTIONS = ("ack", "drop", "reopen")


def act(room_id: str, pid: str, action: str, now: float | None = None) -> dict | None:
    """Ack (answered or open → acked), Drop (open or answered → dropped), or
    Reopen (acked or dropped → open). Types nothing into anyone. Returns the
    point, or None when there is no such point or the move does not apply."""
    now = time.time() if now is None else now
    if action not in ACTIONS:
        return None
    with _LOCK:
        led = load(room_id)
        p = _find(led, _norm_id(pid or ""))
        if p is None:
            return None
        st = p["state"]
        if action == "ack" and st in ("open", "answered"):
            _set_state(p, "acked", now)
            p["ackedBy"] = "click"
        elif action == "drop" and st in ("open", "answered"):
            _set_state(p, "dropped", now)
        elif action == "reopen" and st in ("acked", "dropped"):
            _set_state(p, "open", now)
            p["reopenedAt"] = now
        else:
            return None
        _save(room_id, led)
        return dict(p)


def approve(room_id: str, mid: str, now: float | None = None) -> bool:
    """A thumbs up on a decision: recorded once per balloon (False when it
    already was), and every point that balloon answers is acknowledged."""
    now = time.time() if now is None else now
    mid = (mid or "").strip()
    if not mid:
        return False
    with _LOCK:
        led = load(room_id)
        if mid in led["approvals"]:
            return False
        led["approvals"][mid] = now
        for p in led["points"]:
            if p["state"] in ("open", "answered") and any(a.get("mid") == mid for a in p["answers"]):
                _set_state(p, "acked", now)
                p["ackedBy"] = "approval"
        _save(room_id, led)
        return True


def unapprove(room_id: str, mid: str) -> None:
    """The approval could not be delivered: it may be given again."""
    with _LOCK:
        led = load(room_id)
        if led["approvals"].pop(mid, None) is not None:
            _save(room_id, led)


# ---------------------------------------------------------------------------
# What an agent does (ensemble_points)
# ---------------------------------------------------------------------------

def answer_by_tool(room_id: str, pid: str, summary: str, identity: str) -> dict | None:
    """An answer given by doing: "P12: started as #75"."""
    now = time.time()
    with _LOCK:
        led = load(room_id)
        p = _find(led, _norm_id(pid or ""))
        if p is None or p["state"] == "split":
            return None
        _answer(p, f"tool:{int(now * 1000)}", "", now, "tool", summary=f"{identity}: {summary}".strip())
        _save(room_id, led)
        return dict(p)


def split(room_id: str, pid: str, parts: list[str]) -> list[dict] | None:
    """A message that holds several points becomes P12a, P12b …; P12 is then
    carried by them. Only an open point without sub-points is split."""
    parts = [" ".join(str(t).split())[:TEXT_MAX] for t in parts or [] if str(t).strip()]
    if len(parts) < 2 or len(parts) > 26:
        return None
    now = time.time()
    with _LOCK:
        led = load(room_id)
        p = _find(led, _norm_id(pid or ""))
        if p is None or p["state"] != "open" or p.get("parent") or not re.fullmatch(r"P\d+", p["id"]):
            return None
        kids = [_new_point(led, t, p["owner"], now, "", p.get("sessionId", ""), n=p["n"],
                           sub=chr(ord("a") + k), parent=p["id"], mid=p.get("mid", ""))
                for k, t in enumerate(parts)]
        _set_state(p, "split", now)
        _save(room_id, led)
        return [dict(k) for k in kids]


# ---------------------------------------------------------------------------
# What is shown
# ---------------------------------------------------------------------------

def _counts(led: dict) -> dict:
    return {"open": sum(1 for p in led["points"] if p["state"] == "open"),
            "answered": sum(1 for p in led["points"] if p["state"] == "answered")}


def counts(room_id: str) -> dict | None:
    """``{open, answered}`` for a task card or the PO pill, from the ledger as
    it stands (no looking for answers: the chat and the reminder do that).
    None when the room has no ledger."""
    if not exists(room_id):
        return None
    c = _counts(load(room_id))
    return c if c["open"] or c["answered"] else None


def _item(p: dict) -> dict:
    out = {k: p.get(k) for k in ("id", "state", "owner", "createdAt", "stateAt", "mid", "followUps",
                                 "parent", "comment") if p.get(k) not in (None, "", [])}
    out["text"] = strip_point_lines(p.get("text") or "")[:400]
    out["answers"] = [{k: a[k] for k in ("mid", "at", "how", "summary") if a.get(k)} for a in p["answers"]]
    return out


def view(room_id: str, room: dict | None = None) -> dict:
    """What the chat page shows: the points (newest first, capped), how many
    wait for an answer and for an acknowledgement, and the decisions approved."""
    try:
        led = sync(room_id, room=room)
    except Exception as e:
        _log(f"{room_id}: answers not read: {e!r}")
        led = load(room_id)
    pts = sorted(led["points"], key=lambda p: (p["createdAt"], p["id"]), reverse=True)[:VIEW_MAX]
    return {"items": [_item(p) for p in pts], **_counts(led),
            "approvals": sorted(led.get("approvals") or {})}


def open_points(room_id: str, identity: str = "", skip=()) -> list[dict]:
    """The open points, oldest first; ``identity`` keeps that agent's."""
    if not exists(room_id):
        return []
    led = load(room_id)
    return sorted((p for p in led["points"] if p["state"] == "open" and p["id"] not in skip
                   and (not identity or p.get("owner") in ("", identity))),
                  key=lambda p: p["createdAt"])


def _when(ts: float, now: float) -> str:
    t = datetime.fromtimestamp(ts)
    return t.strftime("%H:%M" if t.date() == datetime.fromtimestamp(now).date() else "%m-%d %H:%M")


def note_line(room_id: str, identity: str = "", now: float | None = None, skip=()) -> str:
    """One line naming the open points, for the note typed after a restart or
    a resume: ``[points] Open points from sam: P12 "…" (since 14:05); …``.
    Empty when there are none."""
    now = time.time() if now is None else now
    pts = open_points(room_id, identity, skip)
    if not pts:
        return ""
    head = (f"{PREFIX}Open points from {_d.operator_name()}, kept by the hub: answer each with "
            f"\"Re Pn:\" at the start of a paragraph, or say why not. ")
    bits, used = [], len(head)
    for i, p in enumerate(pts):
        b = f"{p['id']} \"{_first_words(p['text'])}\" (since {_when(p.get('openedAt') or p['createdAt'], now)})"
        if used + len(b) + 2 > _WAKE_MAX - 60 and bits:
            bits.append(f"and {len(pts) - i} more (ensemble_points lists them)")
            break
        bits.append(b)
        used += len(b) + 2
    return head + "; ".join(bits) + "."


def prompt_block(room_id: str, identity: str = "", now: float | None = None) -> str:
    """The open points verbatim, for a fresh session's first prompt. Empty
    when there are none."""
    now = time.time() if now is None else now
    pts = open_points(room_id, identity)
    if not pts:
        return ""
    op = _d.operator_name()
    lines = [f"Open points from {op} (the CEO). The hub keeps this list, not your handover: "
             f"answer each with \"Re Pn:\" at the start of a paragraph of your reply (one reply "
             f"may answer several), or say why not the same way; an answer given by doing "
             f"something gets ensemble_points action=answer. Never leave one open silently."]
    used = len(lines[0])
    for i, p in enumerate(pts):
        words = " ".join(strip_point_lines(p["text"]).split())
        if len(words) > 400:
            words = words[:399] + "…"
        ln = f"- {p['id']} (since {_when(p.get('openedAt') or p['createdAt'], now)}): {words}"
        if used + len(ln) > _PROMPT_MAX and i:
            lines.append(f"- … and {len(pts) - i} more: ensemble_points lists them all.")
            break
        lines.append(ln)
        used += len(ln)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The reminder: once per point
# ---------------------------------------------------------------------------

def remind_after_s() -> float:
    try:
        v = int(_d.load_settings().get("pointsRemindMin", DEFAULT_REMIND_MIN))
    except (TypeError, ValueError):
        v = DEFAULT_REMIND_MIN
    return max(0, v) * 60.0


def reminder_line(items: list[dict], now: float) -> tuple[str, list[dict]]:
    """(the typed line, the points it names): as many as fit, oldest first."""
    def line(told):
        bits = "; ".join(f"{p['id']} \"{_first_words(p['text'], 48)}\" "
                         f"(since {_when(p.get('openedAt') or p['createdAt'], now)})" for p in told)
        return (f"{PREFIX}still open: {bits}. Answer each with \"Re Pn:\" at the start of a "
                f"paragraph, or say why not.")
    told = items[:1]
    for p in items[1:]:
        if len(line(told + [p])) > _WAKE_MAX:
            break
        told.append(p)
    return line(told)[:_WAKE_MAX], told


def _deliver(room_id: str, owner: str, items: list[dict], now: float) -> list[dict]:
    """Type the reminder into an idle, live agent; never start one. Returns
    the points it named ([] when it was not typed)."""
    rot, cr = _d.rotation, _d.chatroom
    with rot.GATE:
        room = cr.get_room(room_id)
        part = cr.participant(room, owner) if room and owner else None
        if not part or rot.is_rotating(room_id, owner) or rot.awaiting_handover(room_id, owner):
            return []
        sess = rot._pty(part)
        if sess is None:
            return []
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return []
        wake, told = reminder_line(items, now)
        return told if _d._type_input(sess, wake) else []


def tick(now: float | None = None) -> list[str]:
    """Remind each idle agent, once, of its points open past the setting.
    Returns the ids reminded."""
    now = time.time() if now is None else now
    after = remind_after_s()
    if after <= 0:
        return []
    out = []
    try:
        files = sorted(_dir().glob("room-*.json"))
    except OSError:
        return []
    for f in files:
        rid = f.stem
        led = load(rid)
        if not any(p["state"] == "open" for p in led["points"]):
            continue
        try:
            led = sync(rid, force=True)
        except Exception as e:
            _log(f"{rid}: answers not read: {e!r}")
        owed: dict[str, list] = {}
        for p in sorted(led["points"], key=lambda p: p["createdAt"]):
            if p["state"] == "open" and not p.get("reminded") \
                    and now - float(p.get("openedAt") or p["createdAt"]) >= after:
                owed.setdefault(p.get("owner") or "", []).append(p)
        for owner, items in owed.items():
            if not owner:
                continue
            try:
                told = _deliver(rid, owner, items, now)
            except Exception as e:
                _log(f"{rid}/{owner}: reminder not typed: {e!r}")
                told = []
            if not told:
                continue
            with _LOCK:
                cur = load(rid)
                ids = {p["id"] for p in told}
                for p in cur["points"]:
                    if p["id"] in ids:
                        p["reminded"] = now
                _save(rid, cur)
            out += [p["id"] for p in told]
            _log(f"{rid}/{owner}: reminded of {', '.join(p['id'] for p in told)}")
    return out


def maybe_tick() -> None:
    """From the progress check's loop: once per TICK_S, the first a minute
    after the hub started."""
    global _LAST_TICK
    now = time.time()
    if not _LAST_TICK:
        _LAST_TICK = now
        return
    if now - _LAST_TICK >= TICK_S:
        _LAST_TICK = now
        tick(now)
