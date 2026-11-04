"""The inbox (#302, Layout A step 1): one row per thing that waits for the
person, whatever list shows it today.

The same waiting thing shows up several times on the pages: an Ask card, its
point's stage in the PO chat (an answer not seen yet, or one that asks them to
try something), the thread holding it, the task's Needs you row, "Ready for
your check", "Waiting for you since". This module reads those existing
sources and folds them into rows, each with a ``kind``:

``answer``
    a question waiting on the person: an Ask card still open
    (``asks.open_by_room`` — the task rows' Needs you — and, in a PO chat, the
    Decisions waiting, ``asks.decisions``), or a task's question / blocked
    report put to them.
``check``
    something done, waiting for their look: a finished report not read
    (``attention`` ``reported``), or a PO chat point whose answer is new or
    asks them to try something (``points.needs_you``).
``waiting``
    any other Needs you item (``attention.snapshot`` items): a task or PO
    waiting on them, blocked, stalled, gone.

**One row per underlying question.** An Ask card is keyed by its room,
message and ask number; Needs you's row for the same card, the point the card
belongs to (the card's ``points``, or a point whose answer is the card's
message) and the thread holding either are added to that row as sources,
never rows of their own. A thread mark never makes a row: threads.py marks a
thread "needs" only for an open card or a point to try, both rows already.

**Leaving.** Nothing here decides what is open: each source keeps its own
rules (answered, read where reading clears it, Done with this, older than the
age-out, task done or stopped). Nothing is stored and nothing is changed: the
inbox is a view.

``debug``: each row lists its ``sources``; ``dropped`` lists the candidates
left out and why (a duplicate of a row, answered, read, set aside, an older
question, another project).
"""
from __future__ import annotations

import threading
import time

_d = None   # the dashboard module (bind)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


CACHE_S = 2.0                   # the page polls every ten seconds; many tabs share one result
KIND_ORDER = {"answer": 0, "check": 1, "waiting": 2}
DROPPED_MAX = 400               # debug: at most this many dropped candidates listed
DEBUG_DAYS = 14                 # debug: asks left out are listed back to this many days

_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
COLD_THREADS_S = 30.0           # a PO chat not opened since start: its threads looked at again after this
_COLD_THREADS: dict[str, tuple[float, dict]] = {}   # room id → (when, index) of that look
_COLD_BUILDING: set[str] = set()


def _line(text: str, n: int = 160) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


class _Rows:
    """The rows being built, with the keys that fold a source into one."""

    def __init__(self):
        self.rows: list[dict] = []
        self.by_key: dict[tuple, dict] = {}       # (room, mid, n) → ask row
        self.by_mid: dict[tuple, dict] = {}       # (room, mid) → first row on that message
        self.by_point: dict[tuple, dict] = {}     # (room, Pn) → row holding that point
        self.dropped: list[dict] = []

    def add(self, row: dict) -> dict:
        row.setdefault("points", [])
        self.rows.append(row)
        rid, mid = row["roomId"], row.get("mid") or ""
        if mid:
            self.by_mid.setdefault((rid, mid), row)
            if row.get("part", "").startswith("ask:"):
                self.by_key[(rid, mid, row["part"])] = row
        for pid in row["points"]:
            self.by_point.setdefault((rid, pid), row)
        return row

    def fold(self, row: dict, source: str, points=()) -> None:
        if source not in row["sources"]:
            row["sources"].append(source)
        for pid in points:
            if pid not in row["points"]:
                row["points"].append(pid)
            self.by_point.setdefault((row["roomId"], pid), row)

    def drop(self, **kw) -> None:
        self.dropped.append(kw)


def _context():
    """Rooms, projects and how to name a room's project."""
    a = _d.attention
    rooms = a._room_summaries()
    projects = _d.load_projects()
    by_id = {p["id"]: p for p in projects}
    po_of = {p.get("poRoomId"): p["id"] for p in projects if p.get("poRoomId")}
    links = _d.load_session_projects()
    try:
        keys = _d.project_keys(projects)
    except Exception:
        keys = {}
    labels = _d.load_labels()

    def project_of(s: dict) -> str:
        rid = s.get("id", "")
        if rid in po_of:
            return po_of[rid]
        # attention's rule for a task (attention._items)
        return links.get(rid) or s.get("projectId") or _d._project_for_cwd(s.get("cwd", ""), projects) or ""

    def where(s: dict) -> dict:
        rid = s.get("id", "")
        pid = project_of(s)
        no = s.get("no")
        is_po = rid in po_of
        return {"roomId": rid, "projectId": pid, "project": (by_id.get(pid) or {}).get("name", ""),
                "isPo": is_po, "no": no if not is_po else None,
                "ref": _d.task_numbers.label(no, keys.get(s.get("noProjectId"), "")) if no and not is_po else "",
                "task": "PO" if is_po else (labels.get(rid) or s.get("title") or "")}
    return rooms, po_of, where


def _decisions(s: dict) -> tuple[list, list, bool]:
    """A PO chat's Decisions as its Needs you shows them: ``(waiting, older,
    pending)``. The first read of a chat's cards can take seconds: read in the
    background and use the last list worked out (as points._needs_view)."""
    asks, points = _d.asks, _d.points
    rid = s["id"]
    if not asks.cards_ready(s):
        asks.warm_cards(s)
        waiting, older = points._LAST_DECISIONS.get(rid) or ([], [])
        return waiting, older, True
    led = points.load(rid, shared=True) if points.exists(rid) else {}
    waiting, older = asks.decisions(s, led)
    return waiting, older, False


def _why_not_open(s: dict, led: dict, now: float) -> list[dict]:
    """Debug: the asks of a room that ``asks.open_in`` leaves out, and why —
    the same tests in the same order."""
    asks, a = _d.asks, _d.attention
    rid = s.get("id", "")
    try:
        marked = asks._room_asks(s)
    except Exception:
        return []
    done, approved = led.get("asks") or {}, led.get("approvals") or {}
    settled = float(led.get("asksSettledAt") or 0)
    past = asks.moved_past(s, led)
    out = []
    for mid, ts, qs, who in marked:
        if ts and now - ts > DEBUG_DAYS * 86400:
            continue
        got = done.get(mid) or {}
        for n, q in enumerate(qs):
            if ts and ts < max(settled, past):
                why = "answered later (the person wrote past it)" if ts < past else "settled by a later message"
            elif a.read_and_old(s, ts, now):
                why = "read and older than the age-out"
            elif a.set_aside(s, ts):
                why = "set aside (Done with this)"
            elif str(n) in got:
                why = "answered"
            elif mid in approved:
                why = "approved"
            else:
                continue
            out.append({"roomId": rid, "mid": mid, "part": f"ask:{n}", "since": ts,
                        "line": _line(q), "source": "ask", "why": why})
    return out


def _thread_index(th_mod, rid: str, _s=None) -> dict | None:
    """A PO chat's thread index: the last one built (as the thread view uses
    between its polls), ``{}`` for a chat that is not threaded, None while a
    first build runs. A cold chat (no index since the hub started) is built in
    the background as a look only (``threads.build(keep=False)``: nothing of
    the chat's record is written, review 2), kept here for COLD_THREADS_S, so
    a fresh request never waits minutes and never misses its threads for long."""
    hit = th_mod._INDEX.get(rid)
    if hit:
        return hit[1] or {}
    try:
        if not th_mod.is_threaded(rid):
            return {}
    except Exception:       # noqa: BLE001
        return {}
    with _LOCK:
        cold = _COLD_THREADS.get(rid)
        start = rid not in _COLD_BUILDING and (cold is None or time.time() - cold[0] > COLD_THREADS_S)
        if start:
            _COLD_BUILDING.add(rid)
    if start:
        threading.Thread(target=_read_threads, args=(th_mod, rid), daemon=True,
                         name=f"inbox-threads-{rid}").start()
    return cold[1] if cold else None


def _read_threads(th_mod, rid: str) -> None:
    try:
        idx = th_mod.build(rid, show_progress=False, keep=False)
        with _LOCK:
            _COLD_THREADS[rid] = (time.time(), idx or {})
    except Exception as e:      # noqa: BLE001 — the next poll tries again
        print(f"[inbox] {rid}: threads not read: {e!r}", flush=True)
    finally:
        with _LOCK:
            _COLD_BUILDING.discard(rid)


def build(project_id: str = "", debug: bool = False, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rooms, po_of, where = _context()
    by_room = {s["id"]: s for s in rooms}
    R = _Rows()

    def row(s, kind, source, mid="", part="", line="", since=0.0, points=(), reason=""):
        return R.add({"id": f"{kind}:{s['id']}:{mid or source}:{part}", "kind": kind, **where(s),
                      "mid": mid, "part": part, "line": _line(line), "reason": _line(reason, 240),
                      "since": float(since or 0), "sources": [source], "points": list(points)})

    # 1. Ask cards: the task rows' Needs you (open_in), and a PO chat's Decisions.
    try:
        open_asks = _d.asks.open_by_room(rooms, now)
    except Exception as e:      # noqa: BLE001
        print(f"[inbox] open asks not read: {e!r}", flush=True)
        open_asks = {}
    for rid, got in open_asks.items():
        s = by_room.get(rid)
        if not s:
            continue
        for q in got:
            part = f"ask:{q['n']}"
            hit = R.by_key.get((rid, q["mid"], part))
            if hit:
                R.fold(hit, "ask:open")
                continue
            row(s, "answer", "ask:open", q["mid"], part, q["question"], q["ts"],
                reason=f"{q.get('who') or 'An agent'} asks")
    pending = []
    for rid in po_of:
        s = by_room.get(rid)
        if not s:
            continue
        try:
            waiting, older, pend = _decisions(s)
        except Exception as e:      # noqa: BLE001
            print(f"[inbox] {rid}: decisions not read: {e!r}", flush=True)
            continue
        if pend:
            pending.append(rid)
        for c in waiting:
            part = f"ask:{c['n']}"
            pts = list(c.get("points") or [])
            hit = R.by_key.get((rid, c["mid"], part))
            if hit:
                R.fold(hit, "ask:decision", pts)
                continue
            row(s, "answer", "ask:decision", c["mid"], part, c["question"], c["ts"], pts,
                reason=f"{c.get('who') or 'The PO'} asks")
        if debug:
            for c in older:
                if (rid, c["mid"], f"ask:{c['n']}") in R.by_key:
                    continue
                R.drop(roomId=rid, mid=c["mid"], part=f"ask:{c['n']}", since=c["ts"], line=_line(c["question"]),
                       source="ask:decision",
                       why={"repeat": "asked again later (only the newest card counts)",
                            "past": "older question: the person wrote past it",
                            "cleared": "older question: before Clear all"}.get(c.get("why"), c.get("why") or "older"))

    # 2. A PO chat's points whose answer is new or asks them to try something.
    pts_mod = _d.points
    for rid in po_of:
        s = by_room.get(rid)
        if not s or not pts_mod.exists(rid):
            continue
        try:
            led = pts_mod.load(rid, shared=True)
        except Exception:
            continue
        for p in led.get("points") or []:
            k = pts_mod.needs_you(p)
            if not k:
                continue
            a = pts_mod.last_answer(p) or {}
            bal = pts_mod.answer_balloon(p) or {}
            amid = a.get("mid") or bal.get("mid") or ""
            src = f"point:{k}"
            hit = R.by_point.get((rid, p["id"])) or (R.by_mid.get((rid, amid)) if amid else None)
            if hit:
                R.fold(hit, src, [p["id"]])
                if debug:
                    R.drop(roomId=rid, mid=amid, point=p["id"], source=src, why=f"duplicate of row {hit['id']}")
                continue
            said = a.get("said") or a.get("summary") or ""
            asked = pts_mod._first_words(p.get("text") or "", 120)
            line = f"{p['id']}: {asked}" if asked else p["id"]
            row(s, "check", src, amid or p.get("mid") or "", "", line,
                a.get("at") or p.get("stateAt") or p.get("createdAt"), [p["id"]],
                reason=("The answer asks you to try something" if k == "try" else "A new answer to read")
                + (f": {said}" if said else ""))
        if debug:
            try:
                R.dropped.extend(_why_not_open(s, led, now))
            except Exception:
                pass

    # 3. Finished reports nobody has looked at (Ready for your check).
    snap = _d.attention.snapshot()
    for rid, rep in (snap.get("reported") or {}).items():
        s = by_room.get(rid)
        if not s:
            continue
        row(s, "check", "reported", rep.get("mid") or "", "", rep.get("line") or "Finished",
            rep.get("since"), reason=f"{rep.get('agent') or 'The task'} reports it finished")

    # 4. Needs you items: folded into the row of the card or message they
    #    point at, else a row of their own.
    for it in snap.get("items") or []:
        rid = it.get("roomId", "")
        s = by_room.get(rid)
        if not s:
            continue
        src = f"attention:{it.get('state', '')}"
        mid, part = it.get("askId") or "", it.get("askPart") or ""
        hit = (R.by_key.get((rid, mid, part)) if part else None) or (R.by_mid.get((rid, mid)) if mid else None)
        if hit:
            R.fold(hit, src)
            if debug:
                R.drop(roomId=rid, mid=mid, part=part, source=src, why=f"duplicate of row {hit['id']}")
            continue
        kind = "answer" if it.get("askKind") in ("asks", "question") else "waiting"
        line = it.get("quote") or it.get("reason") or ""
        row(s, kind, src, mid, part, line, it.get("askedAt") or it.get("since"),
            reason=it.get("reason") or "")

    # 5. Thread marks: the thread holding a row's message or point is that row.
    th_mod = getattr(_d, "threads", None)
    if th_mod is not None:
        for rid in po_of:
            idx = _thread_index(th_mod, rid, by_room.get(rid))
            if idx is None:
                pending.append(rid)     # being built: the next poll folds its threads
                continue
            if not idx:
                continue
            for tid, th in (idx.get("threads") or {}).items():
                members = set(th.get("members") or [])
                tp = th.get("points") or []
                into = next((r for r in R.rows if r["roomId"] == rid and r.get("mid") in members), None) \
                    or next((R.by_point[(rid, p)] for p in tp if (rid, p) in R.by_point), None)
                if into is not None:
                    R.fold(into, f"thread:{tid}")
                    into.setdefault("threads", []).append(tid)

    # A filter by project never shows another project's task.
    rows = R.rows
    if project_id:
        if debug:
            for r in rows:
                if r["projectId"] != project_id:
                    R.drop(roomId=r["roomId"], mid=r.get("mid", ""), source=",".join(r["sources"]),
                           why=f"another project ({r['project'] or r['projectId'] or 'none'})")
        rows = [r for r in rows if r["projectId"] == project_id]
    rows.sort(key=lambda r: (KIND_ORDER.get(r["kind"], 9), -r["since"]))
    counts = {k: 0 for k in KIND_ORDER}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    out_rows = []
    for r in rows:
        o = {k: v for k, v in r.items() if k != "sources" or debug}
        if not o.get("points"):
            o.pop("points", None)
        out_rows.append(o)
    out = {"rows": out_rows, "count": len(out_rows), "counts": counts, "generatedAt": now}
    if pending:
        out["pending"] = True       # a chat's cards are still being read: the list may be short
    if debug:
        dropped = sorted(R.dropped, key=lambda d: -float(d.get("since") or 0))
        out["dropped"] = dropped[:DROPPED_MAX]
        out["droppedCount"] = len(dropped)
        out["folded"] = sum(len(r["sources"]) - 1 for r in rows)
    return out


def snapshot(project_id: str = "", debug: bool = False) -> dict:
    """:func:`build`, worked out at most every :data:`CACHE_S` per filter."""
    key = (project_id or "", bool(debug))
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    out = build(project_id, debug, now)
    with _LOCK:
        _CACHE[key] = (now, out)
    return out
