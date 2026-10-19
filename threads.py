"""Threads in a PO chat (#210, the CEO's P180: "they should work like threads
in Slack").

A PO chat is a channel: each topic shows once, as its first message (the
parent) with a line under it (how many replies, who wrote, when the last one
came, where it stands), and opens in a panel of its own with all its messages
and its own reply box. Nobody has to post in the right thread: the hub places
every balloon from facts it already has, and agents write as they always did
(``Re Pn``, ``#N``). Nothing is migrated: the threads are derived from the
transcripts and the points ledger on every change, kept in memory per room.

**The rules** (one pass over the chat's balloons, oldest first):

* A person's message starts a thread for each point it makes (a review
  message's comments are a thread each, one balloon shows them all). The
  thread of a point is its own id (``P180``); a point that follows another up
  (``replyTo``: a comment on a passage of its answer, a quote, a link) is in
  the thread of the point it follows, and so is a ``Re P180:`` message (the
  ledger's ``followUps``). An answer on a question card is in the card's
  thread. A message sent from a thread's reply box is in that thread
  (``anchors``, kept by :func:`note_send`).
* A PO message that says ``Re Pn`` is in Pn's thread; one answering several
  points is in each of those threads (one copy, an "also in" mark). Any other
  PO message is in the thread of what it answers: the input before it.
* A task's report (a verdict, a progress line, a completion) is in the
  thread of the latest point of the person's about that task (planned on it,
  or naming it alone), else in a thread of the task's own, ``#N title``.
* The hub's notes (digests, restarts, due lines, …) and what the PO answers
  to them belong to no thread: the chat folds them as before (Team activity).

A PO message that puts a question to the person (an ``Ask:`` card) or says
something is live is also shown in the channel, at its time: never hidden in
a thread. The person's own reply can ask for the same ("Also send to chat").

**Unread.** A thread whose replies came after the person last opened it (or
after their own last message in it) is unread. What came before the room was
first threaded counts as read (``since``).

**Storage.** ``threads/<room>.json`` beside the rooms folder: when the room
was first threaded, when each thread was last opened, the anchors of replies
sent from a thread, and every reply sent from a thread's box (``sent``: its
key, its thread, whether it goes to the chat too, what it holds, and its
balloon once found; #237). The threads themselves are never stored.

Bound to the dashboard module like ``points``: nothing reads ``_d`` at import.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import progress
import time
from pathlib import Path

import points
import durable

_d = None   # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


CHANNEL_MAX = 150               # channel entries a page is sent at first
CHANNEL_LIMIT = 5000            # … and at most, however far it scrolls back
TITLE_WORDS = 14                # a thread's title: its first words
PREVIEW_MAX = 240               # what a reply line quotes of the last reply
ALSO_SLACK_S = 600              # a reply sent to the chat too: its balloon within this
SIDE_KEEP = 400                 # thread box sends kept whole (what they hold, to find their balloon)

# A PO message that says something is live: "Live: main 1a2b3c", "#240 is live".
_LIVE = re.compile(r"(?im)^[ \t>*_-]*live\b[ \t*_]*[:—–-]|\b(?:is|are|now|went|goes|be|been)[ \t]+live\b")
# An ask line (asks._ASK says it in full; this is the cheap test for "may hold one").
_ASK_HINT = re.compile(r"(?im)^[^\n]{0,12}\bask\b")

_LOCK = threading.RLock()
_BAL: dict[str, tuple] = {}         # session id -> (stat, its balloons)
_BAL_MAX = 1500                 # sessions; a long-lived PO chat has a few hundred
_INDEX: dict[str, tuple] = {}       # room id -> (signature, index)
_BUILDING: set = set()


SEEN_AHEAD_S = 600.0     # a page's clock ahead of the hub's, at most


def _num(v) -> float:
    """A time kept in the record, 0 for one that is not (a hand edit)."""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] threads: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Which chats are threaded
# ---------------------------------------------------------------------------

PO_ROOMS_TTL_S = 10.0
_PO_ROOMS: list = [None, 0.0, frozenset()]     # [where projects are read, when, PO room ids]


def _po_rooms() -> frozenset:
    """The projects' PO chats, read again at most every PO_ROOMS_TTL_S (every
    /api/room poll asks; load_projects reads every project.json)."""
    where = (str(_d.PROJECTS_FILE), str(_d.PROJECTS_ROOT))
    now = time.monotonic()
    with _LOCK:
        if _PO_ROOMS[0] == where and now - _PO_ROOMS[1] < PO_ROOMS_TTL_S:
            return _PO_ROOMS[2]
    rooms = frozenset((p.get("poRoomId") or "") for p in _d.load_projects() or []) - {""}
    with _LOCK:
        _PO_ROOMS[:] = [where, now, rooms]
    return rooms


def po_rooms_changed() -> None:
    """A project's PO was set or cleared: read the PO chats again."""
    with _LOCK:
        _PO_ROOMS[:] = [None, 0.0, frozenset()]


def is_threaded(room_id: str, projects: list | None = None) -> bool:
    """A project's PO chat is threaded; a task's chat is not."""
    if not room_id:
        return False
    try:
        if projects is None:
            return room_id in _po_rooms()
    except Exception:       # noqa: BLE001 — not known: not threaded
        return False
    return any((p.get("poRoomId") or "") == room_id for p in projects or [])


# ---------------------------------------------------------------------------
# What the hub keeps per room
# ---------------------------------------------------------------------------

def _dir() -> Path:
    return Path(_d.chatroom.ROOMS_DIR).parent / "threads"


_ROOM_ID = re.compile(r"room-[0-9a-f]+")
_SIDE: dict[str, tuple] = {}        # room id -> (mtime_ns, side)
SAVES: dict[str, int] = {}


def _path(room_id: str) -> Path:
    if not _ROOM_ID.fullmatch(room_id or ""):
        raise ValueError(f"not a room id: {room_id!r}")
    return _dir() / f"{room_id}.json"


def _empty_side() -> dict:
    return {"version": 1, "since": 0.0, "seen": {}, "anchors": {}, "also": [], "sent": []}


def load_side(room_id: str) -> dict:
    """The room's record (a fresh one when it has none)."""
    with _LOCK:
        p = _path(room_id)
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return _empty_side()
        hit = _SIDE.get(room_id)
        if hit and hit[0] == mt:
            return json.loads(json.dumps(hit[1]))
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
        side = _empty_side()
        if isinstance(d, dict):
            try:
                side["since"] = float(d.get("since") or 0)
            except (TypeError, ValueError):
                pass
            for k in ("seen", "anchors"):
                if isinstance(d.get(k), dict):
                    side[k] = d[k]
            for k in ("also", "sent"):
                if isinstance(d.get(k), list):
                    side[k] = [x for x in d[k] if isinstance(x, dict)]
        _SIDE[room_id] = (mt, side)
        return json.loads(json.dumps(side))


def _save_side(room_id: str, side: dict) -> None:
    p = _path(room_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    side["also"] = side.get("also", [])[-SIDE_KEEP:]
    sent = side.get("sent", [])
    if len(sent) > SIDE_KEEP:
        # A reply's place lasts as long as its balloon (#237 reviews 1, 2):
        # an older send keeps only that, once its balloon is known.
        old = [{k: a[k] for k in ("mid", "thread", "also") if k in a}
               for a in sent[:-SIDE_KEEP] if a.get("mid")]
        sent = old + sent[-SIDE_KEEP:]
    side["sent"] = sent
    try:
        durable.write_tmp(tmp, json.dumps(side, indent=1, ensure_ascii=True))
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    SAVES[room_id] = SAVES.get(room_id, 0) + 1
    try:
        _SIDE[room_id] = (p.stat().st_mtime_ns, json.loads(json.dumps(side)))
    except OSError:
        _SIDE.pop(room_id, None)


def _side_sig(room_id: str) -> tuple:
    try:
        return (_path(room_id).stat().st_mtime_ns, SAVES.get(room_id, 0))
    except OSError:
        return (None, SAVES.get(room_id, 0))


def _ensure_since(room_id: str, now: float | None = None) -> None:
    """The first time a room is threaded: what came before counts as read."""
    with _LOCK:
        side = load_side(room_id)
        if side.get("since"):
            return
        side["since"] = time.time() if now is None else now
        _save_side(room_id, side)


# ---------------------------------------------------------------------------
# The balloons of a chat
# ---------------------------------------------------------------------------

def _rec(sid: str, mid: str, t: dict) -> dict:
    """What threading needs of one balloon, and the turn itself (shown as is)."""
    role = "user" if t.get("role") == "user" else "agent"
    text = t.get("text") or ""
    r = {"mid": mid, "sid": sid, "role": role, "ts": _d._turn_epoch(t.get("timestamp")), "turn": t}
    if role == "agent":
        r["re"] = points.re_ids(text)
        # An "Ask:" line, or the plain final question the page draws a card
        # for too (asks.safety): either is in the chat, not only in a thread.
        r["ask"] = bool(_ASK_HINT.search(text)) and bool(_d.asks.parse(text)) \
            or ("?" in text or "decision needed:" in text.lower()) and bool(_d.asks.safety(text))
        r["live"] = bool(_LIVE.search(points._prose(text)))
        return r
    kind = t.get("kind") or "human"
    if kind == "human":
        # A hub line the provenance journal did not claim (older sessions):
        # its prefix still tells it ([digest], [report] …).
        legacy = _d.hub_input_kind(text)
        if legacy.get("kind") not in (None, "human"):
            t = {**t, **_d._input_sender_info(text, legacy)}
            kind = t.get("kind") or "hub"
            r["turn"] = t
    r["kind"] = kind
    if kind == "human":
        r["pts"] = points.point_ids(text)
        r["re"] = points.re_ids(text)
        r["words"] = points.norm_text(points.strip_point_lines(_d.message_refs.strip_message_refs(text)))[:400]
    elif kind == "report":
        r["task"] = points.task_ref(t.get("taskId") or "")
        r["taskTitle"] = t.get("taskTitle") or ""
    return r


def _session_balloons(sid: str, on_turn=None) -> list[dict]:
    """One session's balloons, read again only when its transcript changed."""
    st = points._session_stat(sid)
    with _LOCK:
        hit = _BAL.get(sid)
        if hit is not None and st is not None and hit[0] == st:
            return hit[1]
    turns = _d.read_session_turns(sid) or []
    entries = _d.page_turn_ids(sid, turns)
    recs = []
    for number, (mid, turn) in enumerate(entries, 1):
        recs.append(_rec(sid, mid, turn))
        if on_turn and (number == 1 or number % 50 == 0 or number == len(entries)):
            on_turn(number, len(entries))
    if st is not None:
        with _LOCK:
            _BAL.pop(sid, None)
            _BAL[sid] = (st, recs)
            while len(_BAL) > _BAL_MAX:
                _BAL.pop(next(iter(_BAL)))
    return recs


def _sessions(room: dict, led: dict) -> list[str]:
    """The chat's sessions (points._session_ids: its agent's, those it was
    rotated from, those its points were made in)."""
    return [s for s in points._session_ids(room, led) if _d._solo_owner(room, s) is not None]


def balloons(room: dict, led: dict, on_session=None, on_turn=None) -> list[dict]:
    """Every balloon of the chat, oldest first: each session's in order, the
    sessions by their first balloon's time."""
    per = []
    sids = _sessions(room, led)
    for number, sid in enumerate(sids, 1):
        recs = _session_balloons(sid, (lambda done, total: on_turn(number, len(sids), done, total))
                                if on_turn else None)
        if recs:
            first = next((r["ts"] for r in recs if r["ts"]), 0.0)
            per.append((first, sid, recs))
        if on_session:
            on_session(number, len(sids))
    per.sort(key=lambda x: (x[0], x[1]))
    return [r for _f, _s, recs in per for r in recs]


# ---------------------------------------------------------------------------
# Threading
# ---------------------------------------------------------------------------

def _words(text: str, n: int = TITLE_WORDS) -> str:
    t = points.strip_point_lines(text or "")
    t = re.sub(r"^## (?:Review comments|Points) \(\d+\)\s*", "", t)
    t = re.sub(r"^\s*\*\*\d+\.\*\*[ \t]*", "", t)      # a review comment's number
    said = re.sub(r"(?m)^[ \t]*>[^\n]*(?:\n|$)", "", t)  # its comment, not the passage it quotes
    t = said if said.strip() else t.replace(">", " ")
    words = " ".join(t.split()).split(" ")
    out = " ".join(words[:n])
    return out + ("…" if len(words) > n else "")


_POINT_MENTION = re.compile(r"(?<![\w])P(\d{1,5})([a-z]?)(?![\w])", re.I)
_POINT_RANGE = re.compile(r"(?<![\w])P(\d{1,5})\s*[-–—]\s*P(\d{1,5})(?![\w])", re.I)


def _point_mentions(text: str) -> list[str]:
    """Point names in the PO's own prose, including short `P5–P8` ranges."""
    prose = points._prose(points.strip_point_lines(text or ""))
    found = ["P" + n + suffix.lower() for n, suffix in _POINT_MENTION.findall(prose)]
    for start, end in _POINT_RANGE.findall(prose):
        lo, hi = int(start), int(end)
        if 0 < hi - lo <= 20:
            found.extend(f"P{n}" for n in range(lo + 1, hi))
    return list(dict.fromkeys(found))


def assign(recs: list[dict], led: dict, side: dict, task_title=None, on_message=None,
           claimed: dict | None = None) -> dict:
    """The threads of a chat, from its balloons (oldest first), its points
    ledger and its record (:func:`load_side`). Pure: the same input gives the
    same threads. Returns ``{threads: {tid: thread}, of: {mid: [tid, …]},
    parent: {mid: [tid, …]}, also: {mid: tid}, order: [tid, …]}``; a thread is
    ``{id, root, title, points, task, members}`` (``members``: its balloons,
    the parent first). ``task_title(ref)``: a task's title, for a task's own
    thread whose report gave none."""
    pts = {p["id"]: p for p in led.get("points") or []}
    anchors = side.get("anchors") or {}
    by_mid: dict[str, list[str]] = {}       # a balloon → the points it made
    follows: dict[str, list[str]] = {}      # a balloon → the points it follows up (Re Pn, said again)
    answers: dict[str, list[str]] = {}      # a balloon → the points it answers (the ledger's links)
    for p in pts.values():
        if p.get("mid"):
            by_mid.setdefault(p["mid"], []).append(p["id"])
        for m in p.get("followUps") or []:
            follows.setdefault(m, []).append(p["id"])
        for a in p.get("answers") or []:
            if a.get("mid"):
                answers.setdefault(a["mid"], []).append(p["id"])
    threads: dict[str, dict] = {}
    of: dict[str, list[str]] = {}
    parent: dict[str, list[str]] = {}
    also: dict[str, str] = {}
    work: set[str] = set()             # task traffic inside a person's thread
    order: list[str] = []
    done: dict[str, dict] = {}              # the balloons read so far
    task_thread: dict[str, str] = {}
    memo: dict[str, str | None] = {}

    def new(tid: str, root: dict, title: str, task: str = "", pids=()) -> dict:
        th = {"id": tid, "root": root["mid"], "title": title, "points": list(pids), "task": task,
              "members": [root["mid"]], "rootTs": root["ts"]}
        threads[tid] = th
        order.append(tid)
        of.setdefault(root["mid"], []).append(tid)
        parent.setdefault(root["mid"], []).append(tid)
        return th

    def join(tid: str, r: dict) -> None:
        th = threads.get(tid)
        if th is None or r["mid"] in th["members"]:
            return
        th["members"].append(r["mid"])
        of.setdefault(r["mid"], []).append(tid)

    def point_thread(pid: str) -> str | None:
        """The thread a point is in: its anchor, else that of the point it
        follows, else the card it answers, else its own (once its message
        was read)."""
        if pid in memo:
            return memo[pid]
        memo[pid] = None            # a loop in replyTo ends here
        p = pts.get(pid)
        out = None
        if p is not None:
            anc = anchors.get(pid) or ""
            if anc.startswith("task:") and anc not in threads:
                anc = task_thread.get(anc[5:], anc)
            if anc.startswith("m:") and anc not in threads:
                # Started from a balloon's "Reply in thread": that balloon is
                # its parent, or the thread it is in by now.
                root = done.get(anc[2:])
                if root is not None and any(t != activity for t in of.get(root["mid"]) or []):
                    anc = [t for t in of[root["mid"]] if t != activity][-1]
                elif root is not None:
                    new(anc, root, _words(root["turn"].get("text") or "") or "Thread")
            # Team activity is a shared inbox for hub and task traffic. A
            # human point raised from it starts a topic of its own.
            if anc == "team:activity":
                anc = ""
            if anc in threads:
                out = anc
            else:
                # The point it follows up: its replyTo, else a "Re Pn" it opens with.
                up = p.get("replyTo") or next(iter(points.re_ids(p.get("text") or "")), "")
                if up and up != pid and up in pts:
                    out = point_thread(up)
            if out is None and str(p.get("key") or "").startswith("ask:"):
                card = str(p["key"])[4:].rpartition(":")[0]
                tids = of.get(card) or []
                out = tids[-1] if tids else None
            if out is None and pid in threads:
                out = pid
        memo[pid] = out
        return out

    def claim_tasks(pid: str, tid: str) -> None:
        p = pts.get(pid) or {}
        refs = points.task_refs(points.strip_point_lines(p.get("text") or ""))
        for ref in ([p["task"]] if p.get("task") else []) + list(refs):
            task_thread[ref] = tid
            if tid != "team:activity" and threads[tid].get("task") in ("", None):
                threads[tid]["task"] = ref

    last: str | None = None         # the thread of the last input: what a PO message answers
    last_input_work = False
    activity = "team:activity"
    sent_of = _pair_sent(recs, side, {**(claimed or {}),
                                      **{p["key"]: p["mid"] for p in pts.values() if p.get("key") and p.get("mid")}})

    def team(r: dict) -> str:
        # Hub and unclaimed task traffic has one home outside the channel.
        if activity not in threads:
            new(activity, r, "Team activity")
        else:
            join(activity, r)
        return activity
    for number, r in enumerate(recs, 1):
        if on_message and (number == 1 or number % 50 == 0 or number == len(recs)):
            on_message(number, len(recs))
        mid = r["mid"]
        if mid in done:
            continue
        done[mid] = r
        if r["role"] == "user":
            kind = r.get("kind")
            if kind == "human":
                last_input_work = False
                made = [i for i in by_mid.get(mid, []) if i in pts]
                made += [i for i in r.get("pts") or [] if i in pts and i not in made
                         and pts[i].get("mid") in (None, "", mid)]
                tids: list[str] = []
                starts: list[str] = []
                for i in made:
                    memo.pop(i, None)
                    t = point_thread(i)
                    if t is None:
                        p = pts[i]
                        th = threads.get(i) or new(i, r, _words(p.get("text") or "") or i, pids=[i])
                        if i not in th["points"]:
                            th["points"].append(i)
                        memo[i] = t = i
                        starts.append(i)
                    elif i not in threads[t]["points"]:
                        threads[t]["points"].append(i)
                    tids.append(t)
                    claim_tasks(i, t)
                for i in follows.get(mid, []) + [x for x in r.get("re") or [] if x in pts]:
                    t = point_thread(i)
                    if t:
                        tids.append(t)
                tids = list(dict.fromkeys(tids))
                sent = sent_of.get(mid)
                if sent and sent.get("thread"):
                    # Sent from a thread's box: in that thread (and in those
                    # the points it names are in), whatever it holds.
                    t = sent["thread"]
                    if t.startswith("task:") and task_thread.get(t[5:]) not in (None, activity):
                        t = task_thread[t[5:]]
                    if t.startswith("m:") and t not in threads and t[2:] in done:
                        # Its balloon's thread, or one of its own: Team
                        # activity is not a thread (as a point's anchor above).
                        root = done[t[2:]]
                        homes = [x for x in of.get(root["mid"]) or [] if x != activity]
                        t = homes[-1] if homes else \
                            new(t, root, _words(root["turn"].get("text") or "") or "Thread")["id"]
                    if t in threads:
                        tids = [x for x in tids if x != t] + [t]
                if not tids:
                    # A message the ledger has no point of (sent before the
                    # ledger, a point taken back, or an image): a thread of its own.
                    tid = "m:" + mid
                    if tid not in threads:
                        new(tid, r, _words(r["turn"].get("text") or "") or "Thread")
                    tids, starts = [tid], [tid]
                for t in tids:
                    join(t, r)
                last = tids[-1] if tids else None
                if tids and not starts:
                    # Slack (#234): what is typed in the channel is in the
                    # channel. Only a reply sent from a thread's own box stays
                    # in the thread alone, unless it was sent to the chat too.
                    if sent and sent.get("also"):
                        also[mid] = sent["thread"] if sent.get("thread") in tids else tids[-1]
                    elif not sent:
                        _also_mark(r, tids, side, also)     # a record from before #237
                    boxed = bool(sent) or (bool(made) and all(anchors.get(i) for i in made))
                    if not boxed and mid not in also:
                        also[mid] = tids[-1]
            elif kind == "report":
                last_input_work = True
                ref = r.get("task") or ""
                tid = task_thread.get(ref) if ref else None
                if tid is None:
                    tid = team(r)
                    if ref:
                        task_thread[ref] = tid
                else:
                    join(tid, r)
                    if tid != activity:
                        work.add(mid)
                last = tid
            else:
                last_input_work = True
                last = team(r)
            continue
        # The PO's balloon.
        tids = []
        for i in list(dict.fromkeys((r.get("re") or []) + answers.get(mid, [])
                                    + _point_mentions(r["turn"].get("text") or ""))):
            p = pts.get(i)
            if p is None or (r["ts"] and r["ts"] < float(p.get("createdAt") or 0) - points.SLACK_S):
                continue
            t = point_thread(i)
            if t:
                tids.append(t)
        tids = list(dict.fromkeys(tids))
        explicit = bool(r.get("re") or _point_mentions(r["turn"].get("text") or "")
                        or re.search(r"(?im)^\s*(?:>\s*)?for you\s*:", r["turn"].get("text") or ""))
        if not tids and last == activity and (r.get("ask") or r.get("live")):
            # A PO question or live announcement woken by a digest/report is
            # addressed to the person, not just more task traffic.
            tid = "m:" + mid
            new(tid, r, _words(r["turn"].get("text") or "") or "Thread")
            tids = [tid]
        if not tids and last:
            tids = [last]
        if not tids:
            tid = "m:" + mid
            new(tid, r, _words(r["turn"].get("text") or "") or "Thread")
            tids = [tid]
        for t in tids:
            join(t, r)
        if last_input_work and not explicit and not r.get("ask") and not r.get("live"):
            if any(t != activity for t in tids):
                work.add(mid)
        if tids and (r.get("ask") or r.get("live")) and activity not in tids:
            # Never hidden in a thread (an ask, "is live"): in the chat too,
            # marked with the newest of its threads. An answer to several
            # points is a reply in each of them (#234), as any answer is.
            also[mid] = max(tids, key=lambda t: (threads[t]["rootTs"], order.index(t)))
        # Several PO balloons may be one answer with no new input between
        # them. Keep the same home until the next human or hub input.
        last = tids[-1]
    # Sends paired by what they hold: their balloon, kept (build), so their
    # place outlives their record (#237 review 1).
    paired = {a["key"]: (m, _num(a.get("at"))) for m, a in sent_of.items() if a.get("key") and a.get("mid") != m}
    return {"threads": threads, "of": of, "parent": parent, "also": also,
            "work": work, "order": order, "paired": paired}


PAIR_SLACK_S = 120              # a transcript's clock behind the hub's, at most (sends.SLACK_S)
PAIR_EARLY_S = 5                # a balloon this much before its send was kept: its clock, not another message
PAIR_LATE_S = 6 * 3600          # a send read this long after it was sent, at most (a busy agent)


def _pair_sent(recs: list[dict], side: dict, keyed: dict | None = None) -> dict[str, dict]:
    """The thread box send (:func:`note_send`) each person's balloon is:
    ``{mid: send}``. A send is one balloon and a balloon one send. By its
    key first: the balloon the hub confirmed it in (:func:`confirmed`), or
    the one a point it made is in, or the hub found it in (``keyed``:
    {key: mid}, the ledger's and sends'). Else by what it holds
    (sends.Evidence: its point lines, its first words as delivered, its
    images; a busy agent reads it late) in a balloon no other send is,
    else (a record from before #237) by its words; one from after it was
    sent before one from before (a transcript's clock may be behind), the
    nearest in time first, so the same words said in the channel ("ok") are
    not taken for a reply in a thread."""
    rows = side.get("sent") or []
    if not rows:
        return {}
    keyed = keyed or {}
    people = [r for r in recs if r.get("role") == "user" and r.get("kind") == "human"]
    by_mid = {r["mid"]: r for r in people}
    out: dict[str, dict] = {}
    taken: set[int] = set()
    others = set(keyed.values())        # balloons the hub knows a send of: no guess takes them
    for n, a in enumerate(rows):
        mid = a.get("mid") or keyed.get(a.get("key") or "")
        if mid in by_mid and mid not in out:
            taken.add(n)
            out[mid] = a
    pairs = []
    turns: dict[str, object] = {}
    for n, a in enumerate(rows):
        if n in taken:
            continue
        at = _num(a.get("at"))
        ev = _d.sends.Evidence.kept(a["ev"]) if _d and isinstance(a.get("ev"), dict) else None
        words = a.get("words") or ""
        for r in people:
            if r["mid"] in out or r["mid"] in others:
                continue
            ts = r["ts"]
            if ev is not None and ev.kind:
                if ts and not (at - PAIR_SLACK_S <= ts <= at + PAIR_LATE_S):
                    continue
                if r["mid"] not in turns:
                    turns[r["mid"]] = _d.sends._Turn(r["turn"].get("text") or "")
                if not _holds(turns[r["mid"]], ev):
                    continue
            else:
                if not words or _send_words(r.get("words") or "") != words:
                    continue
                if ts and not (at - ALSO_SLACK_S <= ts <= at + ALSO_SLACK_S):
                    continue
            pairs.append(((ts or at) < at - PAIR_EARLY_S, abs((ts or at) - at), n, r["mid"]))
    for *_, n, mid in sorted(pairs):
        if n not in taken and mid not in out:
            taken.add(n)
            out[mid] = rows[n]
    return out


def _holds(turn, ev) -> bool:
    """Whether a balloon holds all a send's evidence says (sends._Turn, but
    every part: its point lines, its words, its images), without claiming
    it: two follow-ups of one point carry the same point line. Images count
    only for a send of images alone, as sends does (a transcript may name
    them otherwise)."""
    if turn._less(turn.points, ev.points) is None:
        return False
    if ev.words and turn._span(ev) is None:
        return False
    return ev.kind != "images" or turn._less(turn.images, ev.images) is not None


def _send_words(norm: str) -> str:
    """A send's words as both sides keep them: no ``Re Pn:``, at most 80."""
    return re.sub(r"^re p\d{1,5}[a-z]?\s*:?\s*", "", norm or "")[:80]


def _also_mark(r: dict, tids: list[str], side: dict, also: dict) -> None:
    """A person's reply sent to the chat too (:func:`note_send` with ``also``)."""
    words = r.get("words") or ""
    for a in side.get("also") or []:
        tid = a.get("thread")
        if tid not in tids or not words:
            continue
        at = float(a.get("at") or 0)
        if r["ts"] and not (at - ALSO_SLACK_S <= r["ts"] <= at + ALSO_SLACK_S):
            continue
        w = a.get("words") or ""
        if w and w in words:
            also[r["mid"]] = tid
            return


# ---------------------------------------------------------------------------
# A chat's index, kept while nothing it is made of changed
# ---------------------------------------------------------------------------

OLD_STAT_S = 30                 # a session the chat's agent left: its transcript looked at this often
_SIDS: dict[str, tuple] = {}        # room id -> (what it depends on, the chat's sessions)
_OLD: dict[str, tuple] = {}         # room id -> (when, the sessions its agent left, their stats)


def _room_sessions(room_id: str, room: dict, led_sig: tuple) -> list[str]:
    """The chat's sessions, worked out again only when its ledger or its
    agents (a rotation) changed: a poll does not read the ledger for them."""
    parts = repr([(p.get("identity"), p.get("sessionId"),
                   [(r.get("fromSessionId"), r.get("toSessionId")) for r in p.get("rotations") or []])
                  for p in room.get("participants") or []])
    key = (led_sig, parts)
    with _LOCK:
        hit = _SIDS.get(room_id)
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        led = points.load(room_id)
    except Exception:       # noqa: BLE001
        led = {"points": []}
    sids = _sessions(room, led)
    with _LOCK:
        _SIDS[room_id] = (key, sids)
    return sids


def _signature(room_id: str, room: dict, led_sig: tuple) -> tuple:
    """What the chat's threads are made of: its transcripts (the running
    session's on every call, those its agent left every OLD_STAT_S), its
    ledger, its record and its room's own rows."""
    sids = _room_sessions(room_id, room, led_sig)
    live = {p.get("sessionId") for p in room.get("participants") or [] if p.get("sessionId")}
    stat = lambda s: (s, tuple(points._session_stat(s) or ()))
    old = [s for s in sids if s not in live]
    now = time.time()
    with _LOCK:
        hit = _OLD.get(room_id)
    if hit is not None and hit[1] == old and now - hit[0] < OLD_STAT_S:
        old_stats = hit[2]
    else:
        old_stats = tuple(stat(s) for s in old)
        with _LOCK:
            _OLD[room_id] = (now, old, old_stats)
    cur = tuple(stat(s) for s in sids if s in live)
    msgs = room.get("messages") or []
    try:
        sends_sig = _d.sends._path(room_id).stat().st_mtime_ns
    except (OSError, AttributeError, ValueError):
        sends_sig = None
    return (old_stats, cur, led_sig, _side_sig(room_id), len(msgs), sends_sig)


def _led_sig(room_id: str) -> tuple:
    try:
        return (points._path(room_id).stat().st_mtime_ns, points.SAVES.get(room_id, 0))
    except (OSError, ValueError):
        return (None, points.SAVES.get(room_id, 0))


def _state_of(th: dict, pts: dict, open_mids: set, task_of) -> str:
    """Where a thread stands: needs (a question of it waits on the person, or
    an answer asks them to try something), po (a point waits on the PO), task
    (a task works on it), answered (an answer not acknowledged), done."""
    ps = [pts[i] for i in th["points"] if i in pts]
    if any(m in open_mids for m in th["members"]) or any(points.needs_you(p) == "try" for p in ps):
        return "needs"
    if any(p.get("state") == "open" for p in ps):
        return "po"
    task = th.get("task") or ""
    info = task_of(task) if task and task_of else None
    if any(p.get("state") == "planned" for p in ps):
        return "done" if info and info.get("done") else "task"
    if any(p.get("state") == "delivered" for p in ps):
        return "answered"
    if not ps and info is not None:
        return "done" if info.get("done") else "task"
    return "done"


def _who(r: dict) -> str:
    if r["role"] == "agent":
        return "po"
    if r.get("kind") == "human":
        return "you"
    if r.get("kind") == "report":
        return "task"
    return "hub"


def build(room_id: str, room: dict | None = None, *, show_progress: bool = True) -> dict | None:
    """The chat's threads, worked out again only when one of its transcripts,
    its ledger or its record changed. None for a chat that is not threaded."""
    if room is None:
        room = _d.chatroom.get_room(room_id)
    if room is None:
        return None
    # The first build saves the record (``since``) first: the signature then
    # holds it, and the next poll does not build it again.
    _ensure_since(room_id)
    lsig = _led_sig(room_id)
    sig = _signature(room_id, room, lsig)
    with _LOCK:
        hit = _INDEX.get(room_id)
        if hit is not None and hit[0] == sig:
            return hit[1]
    t0 = time.time()
    led = points.load(room_id)
    side = load_side(room_id)
    key = "thread:" + room_id
    def announce(phase, done=0, total=0):
        if show_progress:
            progress.set_progress(key, phase, done, total)

    recs = balloons(room, led, lambda done, total:
                    announce("Reading conversations", done, total),
                    lambda session, sessions, done, total:
                    announce(f"Reading conversation {session} of {sessions}", done, total))
    task_of = points.task_lookup(room_id, room)
    announce("Sorting messages into threads", 0, len(recs))
    try:
        claimed = _d.sends.confirmed_mids(room_id)
    except Exception:       # noqa: BLE001 — paired by what they hold alone
        claimed = {}
    out = assign(recs, led, side, lambda ref: (task_of(ref) or {}).get("title") or "",
                 lambda done, total: announce("Sorting messages into threads", done, total), claimed)
    announce("Finishing threads")
    # Kept only once no later balloon can be the send (review 2): a guess
    # while it may still come is made again on the next build.
    settled = {k: m for k, (m, at) in (out.get("paired") or {}).items()
               if time.time() - at > PAIR_LATE_S + PAIR_SLACK_S}
    if settled:
        try:
            confirmed(room_id, settled)
        except Exception as e:      # noqa: BLE001 — paired again by what they hold next time
            print(f"[threads] {room_id}: replies not kept with their balloons: {e!r}", flush=True)
    by_mid = {r["mid"]: r for r in recs}
    try:
        waiting, _older = points._LAST_DECISIONS.get(room_id) or ([], [])
    except Exception:       # noqa: BLE001
        waiting = []
    open_mids = {c.get("mid") for c in waiting}
    pts = {p["id"]: p for p in led.get("points") or []}
    since = float(side.get("since") or 0)
    seen = side.get("seen") or {}
    for tid, th in out["threads"].items():
        rs = [by_mid[m] for m in th["members"] if m in by_mid]
        work = out["work"]
        replies = [r for r in rs[1:] if r["mid"] not in work]
        work_replies = [r for r in rs[1:] if r["mid"] in work]
        mine = max([r["ts"] for r in rs if _who(r) == "you"] + [0.0])
        mark = max(since, _num(seen.get(tid)), mine)
        news = [r for r in replies if _who(r) != "you" and r["ts"] > mark]
        po_replies = [r for r in replies if _who(r) == "po"] if tid == "team:activity" else []
        po_news = [r for r in po_replies if r["ts"] > mark]
        last_po = max(po_replies, key=lambda r: r["ts"]) if po_replies else None
        lastr = replies[-1] if replies else None
        open_questions = sum(1 for m in th["members"] if m in open_mids)
        th.update({
            "replies": len(replies),
            "workCount": len(work_replies),
            "workLastMid": work_replies[-1]["mid"] if work_replies else "",
            "rootWho": _who(rs[0]) if rs else "",
            "who": list(dict.fromkeys(_who(r) for r in replies)),
            "lastAt": (lastr or rs[0])["ts"] if rs else th["rootTs"],
            "lastMid": (lastr or rs[0])["mid"] if rs else th["root"],
            "lastWho": _who(lastr) if lastr else "",
            "unread": len(news),
            "poUnread": len(po_news),
            "lastPoAt": last_po["ts"] if last_po else 0,
            "lastPoMid": last_po["mid"] if last_po else "",
            "openQuestions": open_questions,
            "state": _state_of(th, pts, open_mids, task_of),
        })
    idx = {**out, "sig": sig, "builtAt": time.time(), "took": round(time.time() - t0, 3),
           "by_mid": by_mid, "recs": recs}
    idx["channel"] = _channel(idx)
    with _LOCK:
        _INDEX[room_id] = (sig, idx)
    progress.clear(key)
    return idx


def _channel(idx: dict) -> list[str]:
    """The whole conversation, as Slack's channel (#234): every message in
    time order except a thread's replies. A thread's first message stays
    where it was posted (the page draws its thread line under it), a reply
    sent to the chat too is there at its own time, and the hub's and the
    tasks' traffic nobody's thread claimed (Team activity) is there as in
    the whole chat, for the page to fold."""
    parent, of, also = idx["parent"], idx["of"], idx["also"]
    out = []
    for r in idx["recs"]:
        mid = r["mid"]
        if mid in parent and any(t != "team:activity" for t in parent[mid]) or mid in also:
            out.append(mid)
        elif all(t == "team:activity" for t in of.get(mid) or []):
            out.append(mid)
    return out


def ready(room_id: str, room: dict) -> dict | None:
    """The chat's index, with a build started in the background when it is
    not current: until it is done the last one is served (a poll never waits
    on a build, and what the page shows does not blink while the PO writes).
    None only for a chat never read yet (its first read takes seconds)."""
    lsig = _led_sig(room_id)
    try:
        sig = _signature(room_id, room, lsig)
    except Exception as e:      # noqa: BLE001
        _log(f"{room_id}: not read: {e!r}")
        return None
    with _LOCK:
        hit = _INDEX.get(room_id)
        if hit is not None and hit[0] == sig:
            return hit[1]
        if room_id in _BUILDING:
            return hit[1] if hit is not None else None
        _BUILDING.add(room_id)
        if hit is None:
            progress.set_progress("thread:" + room_id, "Reading conversations")

    def run():
        try:
            build(room_id, room, show_progress=hit is None)
        except Exception as e:      # noqa: BLE001 — the next poll tries again
            _log(f"{room_id}: not threaded: {e!r}")
        finally:
            progress.clear("thread:" + room_id)
            with _LOCK:
                _BUILDING.discard(room_id)
    threading.Thread(target=run, name=f"threads-{room_id}", daemon=True).start()
    return hit[1] if hit is not None else None


# ---------------------------------------------------------------------------
# What the pages are sent
# ---------------------------------------------------------------------------

def _meta(th: dict) -> dict:
    out = {k: th.get(k) for k in ("id", "root", "title", "points", "task", "replies", "workCount", "workLastMid", "rootWho", "who",
                                   "lastAt", "lastMid", "lastWho", "unread", "state", "openQuestions")}
    if th["id"] == "team:activity":
        # Hub notes and reports do not ring the unread bell. A PO reply in
        # this shared thread still has a visible, message-specific signal.
        out.update(unread=th.get("poUnread", 0), lastAt=th.get("lastPoAt") or out["lastAt"],
                   lastMid=th.get("lastPoMid") or out["lastMid"], lastWho="po" if th.get("lastPoAt") else out["lastWho"])
    return out


def _turn(idx: dict, mid: str, tid: str = "") -> dict:
    r = idx["by_mid"][mid]
    out = {**r["turn"], "mid": mid}
    if tid and mid in idx.get("work", ()):
        out["workLog"] = True
    thr = [t for t in idx["parent"].get(mid) or [] if t != "team:activity"]
    if thr:
        out["thr"] = thr
    if mid in idx["also"]:
        out["inThr"] = idx["also"][mid]
    tids = idx["of"].get(mid) or []
    here = tid or idx["also"].get(mid) or ""
    if here and len(tids) > 1:
        out["alsoIn"] = [t for t in tids if t != here]
    return out


def channel_view(room_id: str, room: dict, n: int = CHANNEL_MAX, upto: str = "") -> dict:
    """The channel: its last ``n`` entries (turns as /api/session gives
    them, each with its balloon id, ``thr`` on a parent, ``inThr`` on a reply
    also sent to the chat), the threads they show, and how many came before.
    ``upto``: a balloon a link lands on; when the channel has it, as many
    more as reach it (``n`` says how many were sent). ``{pending: true}``
    while the chat is first read."""
    idx = ready(room_id, room)
    if idx is None:
        return {"pending": True, "progress": progress.snapshot().get("thread:" + room_id)}
    n = int(n or CHANNEL_MAX)
    ch = idx["channel"]
    if upto and upto in idx["by_mid"]:
        try:
            n = max(n, len(ch) - ch.index(upto) + 20)
        except ValueError:
            pass
    n = max(1, min(n, CHANNEL_LIMIT))
    shown = ch[-n:]
    tids = {t for m in shown for t in (idx["parent"].get(m) or [])}
    # A reply also in the chat names its thread ("replied to a thread: …").
    tids |= {t for m in shown if m in idx["also"] for t in idx["of"].get(m) or []}
    return {"sig": _sig_text(idx), "progress": progress.snapshot().get("thread:" + room_id),
            "items": [_turn(idx, m) for m in shown],
            "threads": {t: _meta(idx["threads"][t]) for t in tids if t in idx["threads"]},
            "activity": _meta(idx["threads"]["team:activity"]) if "team:activity" in idx["threads"] else None,
            "unreadThreads": sorted((_meta(t) for t in idx["threads"].values()
                                    if t["id"] != "team:activity" and t.get("unread")),
                                    key=lambda t: t.get("lastAt") or 0, reverse=True),
            "before": max(0, len(ch) - n), "n": n, "balloons": len(idx["recs"]), "channel": len(ch)}


def thread_view(room_id: str, room: dict, tid: str = "", mid: str = "") -> dict:
    """One thread: its meta and its balloons in order (``alsoIn`` on one that
    is in other threads too). ``mid`` instead of ``tid``: the thread that
    balloon is in (its newest), or a new one it would start (``m:<mid>``)
    when it is in none. ``{pending: true}`` while the chat is first read;
    ``{error}`` for a thread it does not have."""
    idx = ready(room_id, room)
    if idx is None:
        return {"pending": True, "progress": progress.snapshot().get("thread:" + room_id)}
    if not tid and mid:
        # Team activity is the chat's own traffic, not a thread (#234): a
        # balloon of it starts a thread of its own.
        tids = [t for t in idx["of"].get(mid) or [] if t != "team:activity"]
        tid = tids[-1] if tids else ("m:" + mid if mid in idx["by_mid"] else "")
    th = idx["threads"].get(tid)
    if th is None and tid.startswith("m:") and tid[2:] in idx["by_mid"]:
        # A thread started on a balloon from its menu: its parent alone so far.
        r = idx["by_mid"][tid[2:]]
        meta = {"id": tid, "root": r["mid"], "title": _words(r["turn"].get("text") or "") or "Thread",
                "points": [], "task": "", "replies": 0, "who": [], "lastAt": r["ts"], "lastWho": "",
                "unread": 0, "state": "", "new": True}
        return {"sig": _sig_text(idx), "progress": progress.snapshot().get("thread:" + room_id),
                "thread": meta, "items": [_turn(idx, r["mid"], tid)]}
    if th is None:
        return {"error": "no_such_thread"}
    return {"sig": _sig_text(idx), "progress": progress.snapshot().get("thread:" + room_id),
            "thread": _meta(th),
            "items": [_turn(idx, m, tid) for m in th["members"] if m in idx["by_mid"]]}


def unread_threads(room_id: str, room: dict) -> list[dict] | None:
    """Needs you's Threads: the threads with replies not seen, newest first.
    None while the chat is first read."""
    idx = ready(room_id, room)
    if idx is None:
        return None
    out = [_meta(th) for th in idx["threads"].values()
           if th["id"] != "team:activity" and th.get("unread")]
    out.sort(key=lambda t: (t["lastAt"] or 0, t["id"]), reverse=True)
    return out


def _sig_text(idx: dict) -> str:
    return str(abs(hash(repr(idx["sig"]))))


# ---------------------------------------------------------------------------
# What the person does
# ---------------------------------------------------------------------------

def mark_seen(room_id: str, tid: str, ts: float | None = None) -> bool:
    """The person opened a thread: its replies up to ``ts`` (now) are read."""
    if not tid or len(tid) > 300:
        return False
    now = time.time()
    ts = now if ts is None else float(ts)
    if not math.isfinite(ts) or ts > now + SEEN_AHEAD_S:
        raise ValueError(f"not a time it could have been seen: {ts!r}")
    with _LOCK:
        side = load_side(room_id)
        if _num(side["seen"].get(tid)) >= ts:
            return False
        side["seen"][tid] = ts
        _save_side(room_id, side)
    return True


def thread_points(room_id: str, room: dict, tid: str) -> list[str]:
    """The points of a thread, oldest first ([] for a thread with none, or
    one not known). Never waits for a chat's first read: a send does not."""
    idx = ready(room_id, room)
    th = idx["threads"].get(tid) if idx else None
    if th:
        return list(th["points"])
    return [tid] if re.fullmatch(points.POINT_ID, tid or "") else []


def reply_text(room_id: str, room: dict, tid: str, text: str) -> str:
    """A reply typed in a thread's box as the PO receives it: ``Re Pn:``
    before it, Pn the thread's latest point, so the PO knows the thread and
    the hub links the answer to it. A thread without a point (a task's, or one
    started on a balloon) is sent as it is: the page writes the parent's link
    above it. A text that already says ``Re`` is left as it is, and so is one
    a prefix would change the meaning of: a bare acknowledgement ("ok",
    "thanks" acknowledge the answer; with ``Re Pn`` they would reopen the
    point), a slash command, a hub-prefixed line."""
    body = (text or "").strip()
    if not body or points.re_ids(body) or points.is_bare_ack(body) or body.startswith("/"):
        return text
    if _d.hub_input_kind(body).get("kind") not in (None, "human"):
        return text
    pids = thread_points(room_id, room, tid)
    return f"Re {pids[-1]}: {body}" if pids else text


def note_send(room_id: str, tid: str, key: str, pids: list[str], also: bool, text: str,
              now: float | None = None) -> None:
    """A reply sent from a thread's box, before it is delivered: the points
    it made are in that thread (a thread without a point gets one now), and
    the send itself is kept (#237), so its balloon is that thread's reply
    whatever it says (a ``Re Pn`` that makes no point, images, a quote):
    in the chat too only with ``also``. Its balloon is found by its key
    (:func:`confirmed`, the points it made), else by what it holds."""
    if not tid:
        return
    # Only the points it made are anchored there: a "Re P2" typed in P1's
    # box names a point with a thread of its own (#237 review 1).
    made = set(pids or [])
    if key:
        try:
            made &= {p["id"] for p in points.load(room_id).get("points") or [] if p.get("key") == key}
        except Exception:       # noqa: BLE001 — anchored as it names them
            pass
    with _LOCK:
        side = load_side(room_id)
        for pid in pids or []:
            if pid in made and (not re.fullmatch(points.POINT_ID, tid) or pid != tid):
                side["anchors"][pid] = tid
        mine = _send_words(points.norm_text(points.strip_point_lines(
            _d.message_refs.strip_message_refs(text or "")))) if _d else \
            _send_words(points.norm_text(points.strip_point_lines(text or "")))
        side["sent"] = [a for a in side["sent"] if not (key and a.get("key") == key)]
        side["sent"].append({"thread": tid, "key": key, "at": time.time() if now is None else now,
                             "words": mine, "also": bool(also), "pids": list(pids or []),
                             "ev": _d.sends.evidence(text or "").keep() if _d else {}})
        _save_side(room_id, side)


def drop_send(room_id: str, key: str) -> None:
    """A send refused and not kept: its record goes (its next attempt makes it again)."""
    if not key:
        return
    with _LOCK:
        side = load_side(room_id)
        left = [a for a in side["sent"] if a.get("key") != key]
        if len(left) != len(side["sent"]):
            side["sent"] = left
            _save_side(room_id, side)


def confirmed(room_id: str, mids: dict) -> None:
    """The sends ``{key: balloon id}`` were found in the conversation
    (sends.sync): a thread's reply is paired with its balloon for good."""
    if not _ROOM_ID.fullmatch(room_id or "") or not mids:
        return
    with _LOCK:
        side = load_side(room_id)
        changed = False
        for a in side["sent"]:
            mid = mids.get(a.get("key") or "")
            if mid and a.get("mid") != mid:
                a["mid"] = mid
                changed = True
        if changed:
            _save_side(room_id, side)


def forget(room_id: str | None = None) -> None:
    """Drop what is kept in memory (tests)."""
    with _LOCK:
        _PO_ROOMS[:] = [None, 0.0, frozenset()]
        if room_id is None:
            _BAL.clear()
            _INDEX.clear()
            _SIDE.clear()
            _SIDS.clear()
            _OLD.clear()
        else:
            for d in (_INDEX, _SIDE, _SIDS, _OLD):
                d.pop(room_id, None)
