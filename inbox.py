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
age-out, task done or stopped). One thing is the inbox's own (#352): a
``waiting`` row (or an answer row from a Needs you item) that Done with this
does not clear is set aside by Done in the Inbox, the room's ``dealtAt``
(POST /api/attention/done), until its state is new (``since`` after it).
Every row says where it goes (``open``) and how it closes (``dismiss``).

**Older questions** (#310). A PO chat's open Ask cards its Decisions no
longer count (the person wrote past them, they were asked again later, or
came before Clear all) are not rows: they come in ``older``, newest first,
each with ``why``. Every ask row, in ``rows`` or ``older``, carries its ask
ids (``roomId``, ``mid``, ``n``), its ``age`` in seconds, and the task it
names (``taskRef``) with that task's state (``taskState``: running, stopped,
done, or gone when it is no longer on the board), so the page can close it
and say what it is about.

``debug``: each row lists its ``sources``; ``dropped`` lists the candidates
left out and why (a duplicate of a row, answered, read, set aside, an older
question, another project).
"""
from __future__ import annotations

import threading
import time

from asks import title as _ask_title    # pure: an ask's words, never a bare link (P321)

_d = None   # the dashboard module (bind)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


CACHE_S = 2.0                   # the page polls every ten seconds; many tabs share one result
KIND_ORDER = {"answer": 0, "check": 1, "waiting": 2}
DROPPED_MAX = 400               # debug: at most this many dropped candidates listed
DEBUG_DAYS = 14                 # debug: asks left out are listed back to this many days

_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()  # one build at a time: concurrent misses wait for it (PO fix 3)
_CACHE: dict[bool, tuple[float, dict]] = {}     # debug → (when, the unfiltered build)
_GEN = [0]                      # bumped by forget(): a build begun before it is not kept
COLD_THREADS_S = 30.0           # a PO chat not opened since start: its signature checked again after this
_COLD_THREADS: dict[str, tuple[float, dict]] = {}   # room id → (when checked, index) of that look
_COLD_BUILDING: set[str] = set()


def forget() -> None:
    """A question was closed or reopened: the next request builds again."""
    with _LOCK:
        _CACHE.clear()
        _GEN[0] += 1


def _task_states():
    """A function from (task reference, project id) — or a task room's id —
    to that task's ``(ref, state)``: running, stopped, done, or gone (no
    longer on the board). The hub's task index is read once per build."""
    try:
        index = _d._task_index()
        projects = _d.load_projects()
        keys = _d.project_keys(projects)
        find = _d.task_numbers.find_task
        live, norm = _d._room_is_live, _d.normalize_workflow
    except Exception:       # noqa: BLE001 — the rows show without their task's state
        return lambda ref, pid="", rid="": ("", "")
    by_id = {e.get("id"): e for e in index}
    memo: dict = {}

    def state(ref: str, pid: str = "", rid: str = "") -> tuple[str, str]:
        k = (ref, pid, rid)
        if k in memo:
            return memo[k]
        try:
            if not rid and ref:
                rid = find(index, ref, pid, keys, any_project=not pid)[0]
            e = by_id.get(rid) if rid else None
            if e is None:
                out = (ref, "gone" if ref else "")
            else:
                ref = ref or _d.task_numbers.label(e.get("no"), keys.get(e.get("noProjectId") or "", ""))
                out = (ref, "done" if norm(e.get("workflow")) == "done" else "running" if live(e) else "stopped")
        except Exception:   # noqa: BLE001
            out = (ref, "")
        memo[k] = out
        return out
    return state


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
        self.errors: list[dict] = []
        self.ids: set[str] = set()

    def add(self, row: dict) -> dict:
        row.setdefault("points", [])
        # Unique ids (PO fix 4): two rows on one message (split points, a
        # report and its card) are told apart.
        base, n = row["id"], 2
        while row["id"] in self.ids:
            row["id"], n = f"{base}#{n}", n + 1
        self.ids.add(row["id"])
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


def _same_sig(th_mod, rid: str, idx: dict | None) -> bool:
    """Whether the chat's threads are made of what the last look read: then
    the look stands and nothing is built again (PO fix 1)."""
    if not idx or not idx.get("sig"):
        return False
    try:
        room = _d.chatroom.get_room(rid)
        if room is None:
            return False
        return th_mod._signature(rid, room, th_mod._led_sig(rid)) == idx["sig"]
    except Exception:       # noqa: BLE001 — built again
        return False


def _read_threads(th_mod, rid: str) -> None:
    try:
        with _LOCK:
            cold = _COLD_THREADS.get(rid)
        if cold and _same_sig(th_mod, rid, cold[1]):
            with _LOCK:
                _COLD_THREADS[rid] = (time.time(), cold[1])
            return
        idx = th_mod.build(rid, show_progress=False, keep=False)
        with _LOCK:
            _COLD_THREADS[rid] = (time.time(), idx or {})
    except Exception as e:      # noqa: BLE001 — the next poll tries again
        print(f"[inbox] {rid}: threads not read: {e!r}", flush=True)
    finally:
        with _LOCK:
            _COLD_BUILDING.discard(rid)


def _empty(now: float, error: str = "") -> dict:
    out = {"rows": [], "count": 0, "counts": {k: 0 for k in KIND_ORDER}, "older": [], "olderCount": 0,
           "generatedAt": now}
    if error:
        out["error"] = error
    return out


def _build_all(debug: bool = False, now: float | None = None) -> dict:
    """Every row, of every project (filtered by :func:`_filter`). A source that
    fails is left out and, in debug, named in ``errors``: never a 500."""
    now = time.time() if now is None else now
    try:
        rooms, po_of, where = _context()
    except Exception as e:      # noqa: BLE001
        print(f"[inbox] rooms not read: {e!r}", flush=True)
        out = _empty(now, "rooms not read")
        if debug:
            out["errors"] = [{"source": "context", "error": repr(e)}]
        return out
    by_room = {s["id"]: s for s in rooms}
    R = _Rows()

    def failed(source: str, e: Exception, rid: str = "") -> None:
        print(f"[inbox] {rid + ': ' if rid else ''}{source} not read: {e!r}", flush=True)
        R.errors.append({"source": source, "roomId": rid, "error": repr(e)})

    def row(s, kind, source, mid="", part="", line="", since=0.0, points=(), reason="", id_="", **extra):
        return R.add({"id": id_ or f"{kind}:{s['id']}:{mid or source}:{part}", "kind": kind, **where(s),
                      "mid": mid, "part": part, "line": _line(line), "reason": _line(reason, 240),
                      "since": float(since or 0), "sources": [source], "points": list(points), **extra})

    # 1. A PO chat's Decisions waiting: its Needs you's rule for which card
    #    counts (only the newest of a question asked again; none from before
    #    Clear all, or that the person wrote past).
    pending = []
    older_of: dict[tuple, str] = {}     # (room, mid, part) → why a PO card no longer counts
    older_rows: list[dict] = []         # those cards, as rows of the Older questions (#310)
    task_state = _task_states()
    for rid in po_of:
        s = by_room.get(rid)
        if not s:
            continue
        try:
            waiting, older, pend = _decisions(s)
            if pend:
                pending.append(rid)
            for c in waiting:
                part = f"ask:{c['n']}"
                pts = list(c.get("points") or [])
                hit = R.by_key.get((rid, c["mid"], part))
                if hit:
                    R.fold(hit, "ask:decision", pts)
                    continue
                row(s, "answer", "ask:decision", c["mid"], part, _ask_title(c["question"], c.get("who") or "the PO"),
                    c["ts"], pts, reason=f"{c.get('who') or 'The PO'} asks")
            for c in older:
                key = (rid, c["mid"], f"ask:{c['n']}")
                if key in R.by_key:
                    continue
                why = {"repeat": "asked again later (only the newest card counts)",
                       "past": "older question: the person wrote past it",
                       "cleared": "older question: before Clear all"}.get(c.get("why"), c.get("why") or "older")
                older_of[key] = why
                older_rows.append({"id": f"older:{rid}:{c['mid']}:{key[2]}", "kind": "answer", "older": True,
                                   **where(s), "mid": c["mid"], "part": key[2], "n": c["n"],
                                   "line": _line(_ask_title(c["question"], c.get("who") or "the PO")),
                                   "since": float(c.get("ts") or 0),
                                   "reason": _line(f"{c.get('who') or 'The PO'} asks", 240),
                                   "why": c.get("why") or "older", "taskRef": c.get("task") or "",
                                   "points": list(c.get("points") or [])})
                if debug:
                    R.drop(roomId=rid, mid=c["mid"], part=key[2], since=c["ts"], line=_line(c["question"]),
                           source="ask:decision", why=why)
        except Exception as e:      # noqa: BLE001
            failed("decisions", e, rid)

    # 2. Ask cards still open (the task rows' Needs you). In a PO chat a card
    #    its Decisions no longer count stays out (PO fix 2).
    try:
        open_asks = _d.asks.open_by_room(rooms, now)
    except Exception as e:      # noqa: BLE001
        failed("open asks", e)
        open_asks = {}
    for rid, got in open_asks.items():
        s = by_room.get(rid)
        if not s:
            continue
        try:
            for q in got:
                part = f"ask:{q['n']}"
                key = (rid, q["mid"], part)
                hit = R.by_key.get(key)
                if hit:
                    R.fold(hit, "ask:open")
                    continue
                if key in older_of:
                    if debug:
                        R.drop(roomId=rid, mid=q["mid"], part=part, since=q.get("ts"), line=_line(q.get("question")),
                               source="ask:open", why=older_of[key])
                    continue
                row(s, "answer", "ask:open", q["mid"], part, _ask_title(q["question"], q.get("who") or "an agent"),
                    q["ts"], reason=f"{q.get('who') or 'An agent'} asks")
        except Exception as e:      # noqa: BLE001
            failed("open asks", e, rid)

    # 3. A PO chat's answers to look at (a point whose answer is new, or asks
    #    them to try something): one row per PO chat, its answers listed under
    #    it (PO fix 6); a point whose answer is a card's message is that card.
    pts_mod = _d.points
    for rid in po_of:
        s = by_room.get(rid)
        if not s:
            continue
        try:
            if not pts_mod.exists(rid):
                continue
            led = pts_mod.load(rid, shared=True)
            items: list[dict] = []
            by_amid: dict[str, dict] = {}
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
                same = by_amid.get(amid) if amid else None
                if same is not None:        # one answer to several points: one answer to check
                    same["points"].append(p["id"])
                    same["try"] = same["try"] or k == "try"
                    if debug:
                        R.drop(roomId=rid, mid=amid, point=p["id"], source=src,
                               why=f"the same answer as {same['points'][0]}")
                    continue
                said = a.get("said") or a.get("summary") or ""
                asked = pts_mod._first_words(p.get("text") or "", 120)
                # `answer`: the answer itself (a tool's has no message of its
                # own), what an ack of this one opened item names (#325):
                # points.current_answer_key's rule.
                it = {"mid": amid or p.get("mid") or "", "answer": str(a.get("mid") or a.get("key") or ""),
                      "points": [p["id"]], "try": k == "try",
                      "line": _line(f"{p['id']}: {asked}" if asked else p["id"]),
                      "reason": _line(("The answer asks you to try something" if k == "try" else "A new answer to read")
                                      + (f": {said}" if said else ""), 240),
                      "since": float(a.get("at") or p.get("stateAt") or p.get("createdAt") or 0),
                      "source": src}
                items.append(it)
                if amid:
                    by_amid[amid] = it
            if items:
                items.sort(key=lambda i: i["since"])
                n = len(items)
                tries = sum(1 for i in items if i["try"])
                name = where(s)["project"] or "This project"
                pids = [pid for i in items for pid in i["points"]]
                first = next((i for i in items if i["mid"]), items[0])
                g = row(s, "check", "points", first["mid"], "", f"{name} PO: {n} answer{'s' if n != 1 else ''} to check",
                        items[0]["since"], pids,
                        reason=(f"{tries} ask{'s' if tries == 1 else ''} you to try something" if tries else ""),
                        id_=f"check:{rid}:points", items=[{k2: v for k2, v in i.items() if k2 != "source" or debug} for i in items])
                for i in items:
                    if i["mid"]:
                        R.by_mid.setdefault((rid, i["mid"]), g)
                    if debug and i["source"] not in g["sources"]:
                        g["sources"].append(i["source"])
            if debug:
                R.dropped.extend(_why_not_open(s, led, now))
        except Exception as e:      # noqa: BLE001
            failed("points", e, rid)

    try:
        snap = _d.attention.snapshot() or {}
    except Exception as e:      # noqa: BLE001
        failed("attention", e)
        snap = {}

    # 4. Finished reports nobody has looked at (Ready for your check).
    for rid, rep in (snap.get("reported") or {}).items():
        s = by_room.get(rid)
        if not s:
            continue
        try:
            row(s, "check", "reported", rep.get("mid") or "", "", rep.get("line") or "Finished",
                rep.get("since"), reason=f"{rep.get('agent') or 'The task'} reports it finished")
        except Exception as e:      # noqa: BLE001
            failed("reported", e, rid)

    # 5. Needs you items: folded into the row of the card or message they
    #    point at, else a row of their own.
    for it in snap.get("items") or []:
        try:
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
            since = it.get("askedAt") or it.get("since")
            if not it.get("canDismiss") and float(it.get("since") or 0) <= float(s.get("dealtAt") or 0):
                # Done in the Inbox (#352): the room's dealtAt, until the
                # state is new again (a new refusal, a new block).
                if debug:
                    R.drop(roomId=rid, mid=mid, part=part, since=since, line=_line(it.get("reason")),
                           source=src, why="set aside: Done in the Inbox")
                continue
            line = it.get("quote") or it.get("reason") or ""
            reason = it.get("reason") or ""
            extra = {}
            if it.get("cause") == "folder_missing":
                line, reason = _folder_words(s, it)
                extra["cause"] = "folder_missing"   # a could-not-start card (notify.py names it so)
            row(s, kind, src, mid, part, line, since, reason=reason, **extra)
        except Exception as e:      # noqa: BLE001
            failed("attention item", e, it.get("roomId", "") if isinstance(it, dict) else "")

    # 6. Thread marks: the thread holding a row's message or point is that row.
    th_mod = getattr(_d, "threads", None)
    if th_mod is not None:
        for rid in po_of:
            try:
                idx = _thread_index(th_mod, rid, by_room.get(rid))
                if idx is None:
                    pending.append(rid)     # being read: the next poll folds its threads
                    continue
                for tid, th in ((idx or {}).get("threads") or {}).items():
                    into = next((R.by_mid[(rid, m)] for m in th.get("members") or [] if (rid, m) in R.by_mid), None) \
                        or next((R.by_point[(rid, p)] for p in th.get("points") or [] if (rid, p) in R.by_point), None)
                    if into is not None:
                        R.fold(into, f"thread:{tid}")
                        into.setdefault("threads", []).append(tid)
            except Exception as e:      # noqa: BLE001
                failed("threads", e, rid)

    rows = sorted(R.rows, key=lambda r: (KIND_ORDER.get(r["kind"], 9), -r["since"]))
    out_rows = []
    for r in rows:
        o = {k: v for k, v in r.items() if k != "sources" or debug}
        if not o.get("points"):
            o.pop("points", None)
        _ask_ids(o, now, task_state)
        _acts(o)
        out_rows.append(o)
    # An older card already a row (a task's own Needs you holds it) is that row.
    shown = {(r["roomId"], r.get("mid"), r.get("part")) for r in out_rows}
    older = []
    for o in sorted(older_rows, key=lambda r: -r["since"]):
        if (o["roomId"], o["mid"], o["part"]) in shown:
            continue
        if not o.get("points"):
            o.pop("points", None)
        _ask_ids(o, now, task_state)
        older.append(o)
    out = {"rows": out_rows, "count": len(out_rows), "older": older, "generatedAt": now}
    if pending:
        out["pending"] = True       # a chat's cards or threads are still being read: the list may be short
    if debug:
        out["dropped"] = sorted(R.dropped, key=lambda d: -float(d.get("since") or 0))
        out["errors"] = R.errors
    return out


def _acts(o: dict) -> None:
    """Where a row goes and how it is closed (#352), in place. ``open``:
    ``message`` (its message in its chat) or ``chat`` (the task's chat, at
    its latest). ``dismiss``: ``close`` (an Ask card closed by its number),
    ``done`` (a check: its points or report marked seen), else ``dealt``
    (Done: the room's ``dealtAt``, POST /api/attention/done, until it is
    new again)."""
    o["open"] = "message" if o.get("mid") else "chat"
    if o.get("kind") == "check":
        o["dismiss"] = "done"
    elif o.get("kind") == "answer" and o.get("mid") and isinstance(o.get("n"), int):
        o["dismiss"] = "close"
    else:
        o["dismiss"] = "dealt"


def _folder_words(s: dict, it: dict) -> tuple[str, str]:
    """A refused start in plain words (#352): what happened and what to do.
    The folder itself is in the task's chat note, not on the card."""
    who = it.get("ref") or "This task"
    if it.get("folderKind") == "elsewhere":
        return (f"{who} could not start: its conversation was recorded in another folder",
                "Open it to give the seat a fresh session or bring the conversation back, or Done to dismiss.")
    # P324: plain words: what happened, and what the person can do now.
    return (f"{who} could not start: its work folder is missing",
            "Its agent cannot start because its folder was removed. Press Done, or open the task and Stop it.")


def _ask_ids(o: dict, now: float, task_state) -> None:
    """An ask row's ids, age and task (#310), in place: ``n`` from its part,
    ``age`` in seconds, ``taskRef`` and ``taskState``. A PO chat's card names
    its task in its words; a task's own card is about that task."""
    part = o.get("part") or ""
    if part.startswith("ask:") and part[4:].isdigit():
        o["n"] = int(part[4:])
    if o.get("since"):
        o["age"] = max(0, int(now - o["since"]))
    if o.get("isPo"):
        ref, st = task_state(o.get("taskRef") or "", o.get("projectId") or "") if o.get("taskRef") else ("", "")
    elif o.get("kind") == "answer":
        ref, st = task_state(o.get("ref") or "", "", o.get("roomId") or "")
    else:
        return
    if ref:
        o["taskRef"] = ref
    if st:
        o["taskState"] = st


def _filter(full: dict, project_id: str = "", debug: bool = False) -> dict:
    """``full`` for one project (or all), with its counts; ``full`` is shared
    and not changed."""
    rows = full.get("rows") or []
    dropped = list(full.get("dropped") or []) if debug else []
    if project_id:
        if debug:
            for r in rows:
                if r["projectId"] != project_id:
                    dropped.append({"roomId": r["roomId"], "mid": r.get("mid", ""), "since": r.get("since"),
                                    "source": ",".join(r.get("sources") or []),
                                    "why": f"another project ({r['project'] or r['projectId'] or 'none'})"})
        rows = [r for r in rows if r["projectId"] == project_id]
    older = full.get("older") or []
    if project_id:
        older = [r for r in older if r["projectId"] == project_id]
    counts = {k: 0 for k in KIND_ORDER}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    out = {**{k: v for k, v in full.items() if k not in ("dropped",)},
           "rows": rows, "count": len(rows), "counts": counts, "older": older, "olderCount": len(older)}
    if debug:
        dropped.sort(key=lambda d: -float(d.get("since") or 0))
        out["dropped"] = dropped[:DROPPED_MAX]
        out["droppedCount"] = len(dropped)
        out["folded"] = sum(len(r.get("sources") or []) - 1 for r in rows)
    return out


def build(project_id: str = "", debug: bool = False, now: float | None = None) -> dict:
    """The inbox for one project (or all), worked out now."""
    return _filter(_build_all(debug, now), project_id, debug)


def snapshot(project_id: str = "", debug: bool = False) -> dict:
    """One unfiltered build kept :data:`CACHE_S` (per debug), filtered per
    request; concurrent misses wait for the one build (PO fix 3). Never
    raises: a failure is an empty inbox with ``error``."""
    debug = bool(debug)
    try:
        now = time.time()
        with _LOCK:
            hit = _CACHE.get(debug)
        if not (hit and now - hit[0] < CACHE_S):
            with _BUILD_LOCK:
                with _LOCK:
                    hit = _CACHE.get(debug)
                now = time.time()
                if not (hit and now - hit[0] < CACHE_S):
                    with _LOCK:
                        gen = _GEN[0]
                    hit = (now, _build_all(debug, now))
                    with _LOCK:
                        if _GEN[0] == gen:  # a forget() during the build: it may be stale
                            _CACHE[debug] = hit
        return _filter(hit[1], project_id or "", debug)
    except Exception as e:      # noqa: BLE001
        print(f"[inbox] not built: {e!r}", flush=True)
        return _empty(time.time(), "not built")
