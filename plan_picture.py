"""The plan picture (#426, P469, P470): the hub draws a project's plan as one
static page, ``PO Plan.html`` in its Documents folder, from the board alone.
No model is called: the page is computed from the tasks, their gates, their
state, the points they carry and the days their specs give.

When it is drawn (Settings › Preferences › Plan picture, on by default):

* a PO (or planner) creates its second task within ``BURST_S``: one task
  alone is not a plan, two in a planning burst are;
* a PO sets or clears a gate (``after``) on a task, at its creation too: the
  gates are what make an order;
* once a project has the page, any change of state of a task on it (the
  scheduler compares a signature of the page's facts and redraws when it
  moves), so the page stays true.

What it holds: the tasks of those bursts and gates, grouped (a task and the
tasks its gates name are one group). A group whose tasks are all Done leaves
the page; the last one that left is kept at its foot under "Earlier". One
file per project, rewritten in place: the board keeps the older history.

The PO hears of a redraw once, as one line in the result of the tool call
that caused it (``line``), never as a chat message.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import durable

FILE_NAME = "PO Plan.html"
BURST_S = 600          # a PO's second task within 10 minutes is a plan
TICK_S = 30            # how often the scheduler looks for state changes
_d = None
_LOCK = threading.RLock()
_LAST_TICK = 0.0


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


def enabled() -> bool:
    try:
        return _d.load_settings().get("planPicture", True) is not False
    except Exception:
        return True


# ---------------------------------------------------------------------------
# State: plan_pictures.json beside the rooms' hub files
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    return Path(_d.DASHBOARD_DIR) / "plan_pictures.json"


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    durable.write_tmp(tmp, json.dumps(state))
    tmp.replace(path)


def _add_group(rec: dict, ids: list[str]) -> None:
    """Join ``ids`` to the plan: every group sharing a task with them merges."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return
    merged, keep = set(ids), []
    for g in rec.get("groups") or []:
        if merged & set(g):
            merged |= set(g)
        else:
            keep.append(g)
    order = [i for g in rec.get("groups") or [] for i in g] + ids
    keep.append([i for i in dict.fromkeys(order) if i in merged])
    rec["groups"] = keep


# ---------------------------------------------------------------------------
# Triggers (from the tools) and the scheduler's tick
# ---------------------------------------------------------------------------

def on_create(project_id: str, room: dict, by_po: bool, now: float | None = None) -> str:
    """A task was created. Its gates, or a PO's second task within
    ``BURST_S``, put it on the plan and redraw the page; the line for the
    tool's result, or "" when nothing was drawn."""
    if not project_id or not room or not room.get("id") or not by_po or not enabled():
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        rec = state.setdefault(project_id, {})
        recent = [[t, rid] for t, rid in rec.get("recent") or [] if now - float(t) < BURST_S]
        recent.append([now, room["id"]])
        rec["recent"] = recent
        changed = False
        if len(recent) >= 2:
            _add_group(rec, [rid for _, rid in recent])
            changed = True
        gates = [g.get("task") for g in room.get("after") or []]
        if gates:
            _add_group(rec, [room["id"], *gates])
            changed = True
        _save(state)
        return _redraw(project_id, state, now) if changed else ""


def on_gates(project_id: str, room: dict, old: list, new: list, by_po: bool,
             now: float | None = None) -> str:
    """A task's gates were set or cleared: the task and every task its gates
    name (before and after) are on the plan; the page is redrawn."""
    if not project_id or not room or not room.get("id") or not by_po or not enabled():
        return ""
    if [(g.get("task"), g.get("when")) for g in old or []] == \
            [(g.get("task"), g.get("when")) for g in new or []]:
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        rec = state.setdefault(project_id, {})
        _add_group(rec, [room["id"], *[g.get("task") for g in new or []]])
        # A gate cleared leaves its old target on the page only when it is
        # there already (it was part of the plan).
        _save(state)
        return _redraw(project_id, state, now)


def maybe_tick(now: float | None = None) -> None:
    """Redraw the page of every project that has one when what it shows has
    changed (a task started, finished, asked, went to review)."""
    global _LAST_TICK
    now = time.time() if now is None else now
    if now - _LAST_TICK < TICK_S or not enabled():
        return
    _LAST_TICK = now
    with _LOCK:
        state = _load()
        for pid, rec in list(state.items()):
            if not rec.get("drawnAt"):
                continue
            try:
                project = _project(pid)
                if not project:
                    continue
                before = json.dumps(rec.get("groups") or [])
                _, sig, _ = _collect(project, rec, now)
                if sig != rec.get("sig"):
                    _redraw(pid, state, now)
                elif json.dumps(rec.get("groups") or []) != before:
                    _save(state)   # a task gone for good, the page unchanged
            except Exception as exc:   # noqa: BLE001 — one project never stops the rest
                _d.digest._log(f"plan picture {pid}: {str(exc)[:200]}")


def line(path: str) -> str:
    """The one line a tool's result carries after a redraw."""
    return (f"Plan picture redrawn by the hub: {path}. Link it by name, "
            f"[{FILE_NAME}]({path}), in your reply to the CEO.")


def _project(pid: str) -> dict | None:
    return next((p for p in _d.load_projects() if p.get("id") == pid), None)


def page_path(project: dict) -> str:
    # persist: the folder's name is chosen once and kept in project.json, so
    # the page never moves to another folder on its next redraw.
    return os.path.join(_d.project_documents_dir(project, persist=True), FILE_NAME)


def _redraw(pid: str, state: dict, now: float) -> str:
    """Write the page from the board; its path, or "" when it could not."""
    project = _project(pid)
    if not project:
        return ""
    rec = state.setdefault(pid, {})
    data, sig, retire = _collect(project, rec, now)
    if retire:
        # Groups whose tasks are all Done leave; the last one is "Earlier".
        rec["groups"] = [g for g in rec.get("groups") or [] if g not in retire]
        rec["earlier"] = {"ids": retire[-1], "at": now}
        data, sig, _ = _collect(project, rec, now)
    path = page_path(project)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(render(data))
        os.replace(tmp, path)
    except OSError:
        return ""
    rec.update(sig=sig, drawnAt=now)
    _save(state)
    return path


# ---------------------------------------------------------------------------
# From the board to rows
# ---------------------------------------------------------------------------

_PRIO = {1: ("▲", "Highest"), 2: ("▲", "High"), 3: ("=", "Medium"), 4: ("▼", "Low"), 5: ("▼", "Lowest")}
_POINT = re.compile(r"(?<![\w-])P\d{1,6}[a-z]?(?![\w-])")
# A title's trailing "(P12, P12b)": the points show as chips, so the title drops it.
_POINT_TAIL = re.compile(r"\s*\(\s*P\d{1,6}[a-z]?(?:\s*[,;]\s*P\d{1,6}[a-z]?)*\s*\)\s*$")
_NUM = r"(\d+(?:[.,]\d+)?|½)"
_DAYS = re.compile(r"(?i)(?:\bestimat\w*\s*(?:(?:effort|work|time)\s*)?(?:[:=—–-]\s*)?(?:(?:is|of|about|around|roughly)\s+)?"
                   r"(?:~\s*)?|~\s*)" + _NUM
                   + r"(?:\s*[-–]\s*" + _NUM + r")?\s*(?:days?|d)\b")


def spec_days(spec: str) -> float | None:
    """The days a spec gives its work ("Estimate: 2 days", "~3 d"; a range
    counts its upper end), else None."""
    m = _DAYS.search(spec or "")
    if not m:
        return None
    raw = (m.group(2) or m.group(1)).replace(",", ".")
    return 0.5 if raw == "½" else float(raw)


def _owner_kind(room: dict) -> str:
    agents = [p for p in room.get("participants") or [] if p.get("kind", "agent") == "agent"]
    eng = next((p for p in agents if (p.get("role") or "").split()[:1] == ["engineer"]), None)
    pick = eng or (agents[0] if agents else {})
    return (pick.get("agent") or "").strip()


def _gate_met(target: dict, when: str) -> bool:
    if when == "approved":
        reviews = [r for p in target.get("participants", [])
                   for r in [*(p.get("reviews") or []), p.get("review") or {}]
                   if r.get("endedAt") and r.get("verdict")]
        last = max(reviews, key=lambda r: r["endedAt"], default={})
        return last.get("verdict") == "approve"
    # Merged: the digest records the merge and settles the card in Done; no
    # git call here, so a redraw costs nothing.
    return bool(target.get("mergedAt")) or _d.workflow_of(target) == "done"


def _points(room: dict, po_led: dict | None, key: str = "") -> list[str]:
    out = _POINT.findall(room.get("title") or "")
    label = _d.task_label(room)
    # The ledger names a task "#104" or, with its project's key, "ED-104".
    names = {label, f"{key.upper()}-{label[1:]}" if key and label else label}
    for p in (po_led or {}).get("points") or []:
        if label and _d.points.task_ref(p.get("task")) in names and p.get("id"):
            out.append(p["id"])
    return list(dict.fromkeys(out))


def _state(room: dict, attn: dict, unmet: list[str]) -> tuple[str, str]:
    """(key, word) of a task on the page."""
    wf = _d.workflow_of(room)
    if wf == "done":
        return "done", "Done"
    item = attn.get(room["id"]) or {}
    if item.get("state") in ("waiting_for_you", "blocked"):
        return "needs", "Needs you"
    if wf == "inreview":
        return "review", "In review"
    if not room.get("launched", True):
        if unmet:
            return "gate", "Waits for " + ", ".join(unmet)
        return "draft", "Not started"
    if room.get("waitingOnPo"):
        return "po", "Waits on the PO"
    if _d._room_is_live(room):
        return "running", "Running"
    return "stopped", "Stopped"


def _room_gone(rid: str) -> bool:
    """True when a room's file is gone (the task was deleted); False when it
    is there or cannot be checked (a read that failed just now)."""
    try:
        os.stat(_d.chatroom._room_path(rid))
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return False


def _row(room: dict, rooms: dict, attn: dict, po_led: dict | None, pkey: str = "") -> dict:
    gates, unmet = [], []
    for g in room.get("after") or []:
        tid = g.get("task") or ""
        target = rooms.get(tid) or _d.chatroom.get_room(tid, public=False, messages=False)
        when = g.get("when", "merged")
        if not target:
            if _room_gone(tid):
                continue   # a gate on a deleted task holds nothing
            # Cannot be read just now: the gate stands, unconfirmed.
            gates.append({"id": tid, "label": "a task", "when": when, "met": False, "unknown": True})
            unmet.append("a task")
            continue
        met = _gate_met(target, when)
        label = _d.task_label(target) or target.get("title", "")
        gates.append({"id": target["id"], "label": label, "when": when, "met": met})
        if not met:
            unmet.append(label)
    key, word = _state(room, attn, unmet)
    item = attn.get(room["id"]) or {}
    done_at = float(room.get("mergedAt") or 0) if key == "done" else 0.0
    title = room.get("title") or ""
    return {"id": room["id"], "no": _d.task_label(room), "title": _POINT_TAIL.sub("", title) or title,
            "priority": _d.priority_of(room), "owner": _owner_kind(room),
            "state": key, "word": word, "gates": gates, "points": _points(room, po_led, pkey),
            "days": spec_days(room.get("spec") or ""),
            "ask": (item.get("reason") or "") if key == "needs" else "",
            "merged": key == "done" and bool(room.get("mergedAt")), "doneAt": done_at}


def _collect(project: dict, rec: dict, now: float) -> tuple[dict, str, list]:
    """The page's data, its signature, and the groups now all Done."""
    rooms: dict = {}
    pid = project.get("id") or ""
    links = _d.load_session_projects()

    gone: set[str] = set()

    def mine(rid: str) -> dict | None:
        """The room, while it is still this project's. A task moved to another
        project, or whose file is gone, leaves the plan for good (``gone``);
        one that cannot be read just now is left out of this drawing only."""
        room = rooms.get(rid) or _d.chatroom.get_room(rid, public=False, messages=False)
        if room is None:
            if _room_gone(rid):
                gone.add(rid)
            return None
        if _d._room_project_id(rid, room, links) != pid:
            gone.add(rid)
            return None
        rooms[rid] = room
        return room

    saved = []   # the plan's groups, less what is gone for good
    for g in rec.get("groups") or []:
        for rid in g:
            mine(rid)
        keep = [rid for rid in g if rid not in gone]
        if keep:
            saved.append(keep)
    if saved != (rec.get("groups") or []):
        rec["groups"] = saved   # saved with the redraw
    try:
        key = _d.project_keys(_d.load_projects()).get(pid, "") or project.get("key", "")
    except Exception:
        key = ""
    try:
        attn = _d.attention.by_room(max_age=60)
    except Exception:
        attn = {}
    po_led = None
    try:
        po = project.get("poRoomId") or ""
        if po and _d.points.exists(po):
            po_led = _d.points.load(po, shared=True)
    except Exception:
        po_led = None
    # A group retires once every task in it reads as Done; one that cannot
    # be read now keeps it on the page.
    retire = [g for g in saved if all(r in rooms and _d.workflow_of(rooms[r]) == "done" for r in g)]
    current = [r for g in saved if g not in retire for r in g if r in rooms]
    current = list(dict.fromkeys(current))
    rows = [_row(rooms[r], rooms, attn, po_led, key) for r in current]
    earlier_ids = [r for r in ((rec.get("earlier") or {}).get("ids") or []) if r not in current]
    earlier = []
    for rid in earlier_ids:
        room = mine(rid)
        if room:
            earlier.append(_row(room, rooms, attn, po_led, key))
    data = {"project": project.get("name") or "Project", "rows": rows, "earlier": earlier,
            "earlierAt": float((rec.get("earlier") or {}).get("at") or 0), "drawnAt": now}
    sig_src = json.dumps([rows, earlier], sort_keys=True, default=str)
    return data, sig_src, retire


# ---------------------------------------------------------------------------
# Layout and the page
# ---------------------------------------------------------------------------

BOX_W, BOX_H, GAP, PITCH, TOP, PAD = 312, 112, 88, 136, 26, 4
_NUMKEY = re.compile(r"(\d+)")


def _numkey(row: dict) -> tuple:
    m = _NUMKEY.search(row.get("no") or "")
    return (int(m.group(1)) if m else 10 ** 9, row.get("title") or "")


def in_order(rows: list[dict]) -> list[dict]:
    """The list's order: by task number, but never a task before one of its
    gates on the page (a gate loop falls back to number order)."""
    left = {r["id"]: r for r in rows}
    out = []
    while left:
        ready = [r for r in left.values()
                 if not any(g["id"] in left and g["id"] != r["id"] for g in r["gates"])]
        r = min(ready or left.values(), key=_numkey)
        out.append(left.pop(r["id"]))
    return out


def layout(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """Each task's (column, row): its column is how many gates deep it sits
    among the page's tasks, its row the first free one at or below the row of
    the task its gates name first."""
    ids = {r["id"] for r in rows}
    by_id = {r["id"]: r for r in rows}
    depth: dict[str, int] = {}

    def d_of(rid: str, seen: frozenset = frozenset()) -> int:
        if rid in depth:
            return depth[rid]
        if rid in seen:
            return 0           # a loop in the gates: drawn, never followed
        parents = [g["id"] for g in by_id[rid]["gates"] if g["id"] in ids]
        depth[rid] = 1 + max((d_of(p, seen | {rid}) for p in parents), default=-1)
        return depth[rid]

    for r in rows:
        d_of(r["id"])
    pos: dict[str, tuple[int, int]] = {}
    for col in range(max(depth.values(), default=-1) + 1):
        here = [r for r in rows if depth[r["id"]] == col]

        def pref(r):
            ps = [pos[g["id"]][1] for g in r["gates"] if g["id"] in pos]
            return min(ps) if ps else 0
        here.sort(key=lambda r: (pref(r), _numkey(r)))
        used: set[int] = set()
        for r in here:
            row = pref(r)
            while row in used:
                row += 1
            used.add(row)
            pos[r["id"]] = (col, row)
    return pos


_TONE = {"done": "s", "running": "p", "needs": "w", "review": "d", "gate": "n", "draft": "n",
         "po": "n", "stopped": "n"}
_EDGE = {"s": "#22A06B", "p": "#1D7AFC", "w": "#E2B203", "d": "#8270DB", "n": "#626F86"}
_LOZ = {"s": ("#DCFFF1", "#216E4E"), "p": ("#E9F2FF", "#0055CC"), "w": ("#FFF7D6", "#7F5F01"),
        "d": ("#F3F0FF", "#5E4DB2"), "n": ("#F1F2F4", "#44546F")}
_PRIO_C = {1: "#CD1317", 2: "#E9494A", 3: "#B8901A", 4: "#2898BD", 5: "#0B62D0"}
_AGENT_C = {"claude": "#A9502F", "codex": "#166A7C"}
_SUM = [("done", "done", "s"), ("running", "running", "p"), ("needs", "needs you", "w"),
        ("po", "waits on the PO", "n"), ("review", "in review", "d"),
        ("gate", "waits for a gate", "n"), ("draft", "not started", "n"), ("stopped", "stopped", "n")]

e = html.escape


def _days(v: float) -> str:
    n = f"{v:g}"
    return f"{n} day" if v == 1 else f"{n} days"


def _wrap(text: str, width: int, lines: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
        if len(out) == lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    rest = " ".join(words)
    if len(" ".join(out)) < len(rest) and out:
        last = out[-1]
        out[-1] = (last[: width - 1].rstrip() if len(last) >= width else last) + "…"
    return [s[:width] for s in out] or [""]


def _box(r: dict, x: int, y: int) -> str:
    tone = _TONE[r["state"]]
    bg, fg = _LOZ[tone]
    word = r["word"].upper()
    lw = int(len(word) * 7.4) + 18 + (14 if r["state"] == "running" else 0)
    arrow, pname = _PRIO.get(r["priority"], _PRIO[3])
    stroke = ('stroke="#E2B203" stroke-width="1.5"' if tone == "w" else
              'stroke="#DCDFE4" stroke-dasharray="5 4"' if r["state"] == "gate" else 'stroke="#DCDFE4"')
    out = [f'<g transform="translate({x},{y})">',
           f'<rect width="{BOX_W}" height="{BOX_H}" rx="8" fill="#fff" {stroke}/>'
           f'<rect width="5" height="{BOX_H}" rx="2" fill="{_EDGE[tone]}"/>',
           f'<text x="18" y="24" font-size="15" font-weight="700" fill="#172B4D">{e(r["no"])}</text>',
           f'<text x="{18 + 9 * len(r["no"]) + 8}" y="24" font-size="12" font-weight="600" '
           f'fill="{_PRIO_C.get(r["priority"], "#B8901A")}">{arrow} {pname}</text>',
           f'<rect x="{BOX_W - 12 - lw}" y="10" width="{lw}" height="19" rx="4" fill="{bg}"/>']
    if r["state"] == "running":
        out.append(f'<circle cx="{BOX_W - 12 - lw + 13}" cy="19.5" r="4" fill="#22A06B"/>')
        out.append(f'<text x="{BOX_W - 12 - lw / 2 + 7}" y="24" font-size="11" font-weight="700" '
                   f'fill="{fg}" text-anchor="middle">{e(word)}</text>')
    else:
        out.append(f'<text x="{BOX_W - 12 - lw / 2}" y="24" font-size="11" font-weight="700" '
                   f'fill="{fg}" text-anchor="middle">{e(word)}</text>')
    for i, t in enumerate(_wrap(r["title"], 34, 2)):
        out.append(f'<text x="18" y="{50 + 18 * i}" font-size="14" font-weight="600" '
                   f'fill="#172B4D">{e(t)}</text>')
    cx = 18
    if r["owner"]:
        w = int(len(r["owner"]) * 6.6) + 16
        out.append(f'<rect x="{cx}" y="82" width="{w}" height="18" rx="9" '
                   f'fill="{_AGENT_C.get(r["owner"], "#626F86")}"/>'
                   f'<text x="{cx + w / 2}" y="95" font-size="11" font-weight="700" fill="#fff" '
                   f'text-anchor="middle">{e(r["owner"])}</text>')
        cx += w + 8
    pts = r["points"][:4] + ([f'+{len(r["points"]) - 4}'] if len(r["points"]) > 4 else [])
    for p in pts:
        w = int(len(p) * 6.6) + 10
        out.append(f'<rect x="{cx}" y="82" width="{w}" height="18" rx="4" fill="#F1F2F4"/>'
                   f'<text x="{cx + w / 2}" y="95" font-size="11" font-weight="600" fill="#172B4D" '
                   f'text-anchor="middle">{e(p)}</text>')
        cx += w + 6
    days = _days(r["days"]) if r["days"] is not None else "no estimate"
    out.append(f'<text x="{BOX_W - 12}" y="95" font-size="12" fill="#626F86" '
               f'text-anchor="end">{days}</text></g>')
    return "".join(out)


def graph(rows: list[dict]) -> tuple[str, int]:
    """The SVG of the boxes and gates, and its width."""
    pos = layout(rows)
    cols = max((c for c, _ in pos.values()), default=0) + 1
    nrows = max((r for _, r in pos.values()), default=0) + 1
    width = PAD * 2 + cols * BOX_W + (cols - 1) * GAP
    height = TOP + nrows * PITCH - (PITCH - BOX_H) + 8
    xy = {rid: (PAD + c * (BOX_W + GAP), TOP + r * PITCH) for rid, (c, r) in pos.items()}
    parts = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
             f'aria-label="The plan\'s tasks and their gates" '
             f'font-family="system-ui,-apple-system,Segoe UI,sans-serif">',
             '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
             'markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="#626F86"/></marker>'
             '<marker id="ahs" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
             'orient="auto"><path d="M0,0L10,5L0,10z" fill="#22A06B"/></marker></defs>']
    for c in range(cols):
        head = "FIRST" if c == 0 else "THEN"
        parts.append(f'<text x="{PAD + c * (BOX_W + GAP)}" y="14" font-size="11" fill="#626F86" '
                     f'font-weight="600" letter-spacing=".05em">{head}</text>')
    labels = []
    for r in rows:
        for g in r["gates"]:
            if g["id"] not in xy:
                continue
            (x1, y1), (x2, y2) = xy[g["id"]], xy[r["id"]]
            x1, y1, y2 = x1 + BOX_W, y1 + BOX_H // 2, y2 + BOX_H // 2
            xa = x2 - GAP           # where the curve leaves the last gap
            xm = x2 - GAP // 2
            colour = "#22A06B" if g["met"] else "#626F86"
            dash = "" if g["met"] else ' stroke-dasharray="5 4"'
            path = (f"M{x1},{y1} H{xa} C{xm},{y1} {xm},{y2} {x2},{y2}" if xa > x1
                    else f"M{x1},{y1} C{xm},{y1} {xm},{y2} {x2},{y2}")
            parts.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2"{dash} '
                         f'marker-end="url(#{"ahs" if g["met"] else "ah"})"/>')
            text = g["when"] + (" ✓" if g["met"] else "")
            w = int(len(text) * 6.4) + 14
            bg, fg = (_LOZ["s"] if g["met"] else _LOZ["n"])
            ly = (y1 + y2) // 2
            labels.append(f'<rect x="{xm - w / 2}" y="{ly - 20}" width="{w}" height="18" rx="9" '
                          f'fill="{bg}"/><text x="{xm}" y="{ly - 7}" font-size="11" '
                          f'text-anchor="middle" fill="{fg}" font-weight="600">{e(text)}</text>')
    parts += labels
    for r in rows:
        parts.append(_box(r, *xy[r["id"]]))
    parts.append("</svg>")
    return "".join(parts), width


def _when(ts: float) -> str:
    t = datetime.fromtimestamp(ts)
    return f"{t.day} {t.strftime('%b')}"


def _li(r: dict) -> str:
    tone = _TONE[r["state"]]
    arrow, pname = _PRIO.get(r["priority"], _PRIO[3])
    meta = [f'<span class="loz l-{tone}">{e(r["word"])}</span>',
            f'<span class="pr" style="color:{_PRIO_C.get(r["priority"])}">{arrow} {pname}</span>']
    if r["owner"]:
        meta.append(f'<span class="ag" style="background:{_AGENT_C.get(r["owner"], "#626F86")}">'
                    f'{e(r["owner"])}</span>')
    meta += [f'<span class="pt">{e(p)}</span>' for p in r["points"]]
    meta.append(f'<span>{_days(r["days"]) if r["days"] is not None else "no estimate"}</span>')
    if r["state"] == "done":
        meta.append(f'<span>merged {_when(r["doneAt"])}</span>' if r["merged"] and r["doneAt"]
                    else "<span>done</span>")
    for g in r["gates"]:
        meta.append(f'<span>after {e(g["label"])} {e(g["when"])}{" ✓" if g["met"] else ""}'
                    f'{" (cannot be read just now)" if g.get("unknown") else ""}</span>')
    out = (f'<li><span class="no">{e(r["no"])}</span><span class="t">{e(r["title"])}</span>'
           f'<span class="meta">{"".join(meta)}</span>')
    if r["ask"]:
        out += f'<span class="ask">Asks you: “{e(" ".join(r["ask"].split())[:200])}”</span>'
    return out + "</li>"


_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:22px 24px 28px;background:#F7F8F9;color:#172B4D;font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
h1{font-size:19px;margin:0 0 2px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:#626F86;margin:0 0 10px;font-weight:600}
.sub{color:#626F86;margin:0 0 14px;font-size:13px}
.sum{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:13px;font-weight:600;background:#F1F2F4;color:#44546F}
.chip i{width:8px;height:8px;border-radius:50%;display:inline-block;background:#626F86}
.c-s{background:#DCFFF1;color:#216E4E}.c-s i{background:#22A06B}
.c-p{background:#E9F2FF;color:#0055CC}.c-p i{background:#1D7AFC}
.c-w{background:#FFF7D6;color:#7F5F01}.c-w i{background:#E2B203}
.c-d{background:#F3F0FF;color:#5E4DB2}.c-d i{background:#8270DB}
.c-e{background:none;color:#626F86;font-weight:500;padding-left:2px}
.wrap{display:grid;grid-template-columns:minmax(0,760px) minmax(0,1fr);gap:22px;align-items:start}
.wrap.wide{grid-template-columns:1fr}
@media (max-width:1100px){.wrap{grid-template-columns:1fr}}
.panel{background:#fff;border:1px solid #DCDFE4;border-radius:10px;padding:14px 16px}
.graph{overflow-x:auto}
.graph svg{display:block;max-width:none;height:auto}
.list{list-style:none;margin:0;padding:0}
.list li{border-top:1px solid #DCDFE4;padding:10px 0;display:grid;grid-template-columns:44px 1fr;gap:2px 8px}
.list li:first-child{border-top:0;padding-top:2px}
.no{font-weight:700;font-size:15px}
.t{font-weight:600}
.meta{grid-column:2;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px;color:#626F86}
.loz{font-size:11.5px;font-weight:700;padding:1px 7px;border-radius:4px;text-transform:uppercase;letter-spacing:.03em}
.l-s{background:#DCFFF1;color:#216E4E}.l-p{background:#E9F2FF;color:#0055CC}.l-w{background:#FFF7D6;color:#7F5F01}.l-d{background:#F3F0FF;color:#5E4DB2}.l-n{background:#F1F2F4;color:#44546F}
.ag{color:#fff;font-weight:700;font-size:11px;padding:1px 7px;border-radius:999px}
.pt{font-weight:600;color:#172B4D;background:#F1F2F4;border-radius:4px;padding:0 5px}
.ask{grid-column:2;margin-top:4px;font-size:13px;background:#FFF7D6;color:#7F5F01;border-radius:6px;padding:4px 8px}
.pr{font-weight:600}
.leg{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:#626F86;margin-top:12px}
.none{color:#626F86;margin:0}
.earlier{margin-top:22px}
.foot{margin-top:18px;font-size:12px;color:#626F86}
"""


def render(data: dict) -> str:
    """The page: one static HTML file, inline CSS and SVG, no script, no
    external load, light theme, readable at phone width."""
    rows, earlier = data["rows"], data["earlier"]
    t = datetime.fromtimestamp(data["drawnAt"])
    when = f"{t.day} {t.strftime('%b %Y, %H:%M')}"
    name = e(data["project"])
    body = [f"<h1>{name}: the plan</h1>",
            f'<p class="sub">Drawn by the hub from the board at {when}. No model wrote it; it '
            "redraws itself when the PO plans tasks or changes a gate, and when a task on it "
            "changes state.</p>"]
    if rows:
        chips = [f'<span class="chip">{len(rows)} task{"s" if len(rows) != 1 else ""}</span>']
        for key, words, tone in _SUM:
            n = sum(1 for r in rows if r["state"] == key)
            if n:
                chips.append(f'<span class="chip c-{tone}"><i></i>{n} {words}</span>')
        given = [r for r in rows if r["days"] is not None]
        if given:
            left = sum(r["days"] for r in given if r["state"] != "done")
            chips.append(f'<span class="chip c-e">about {_days(left)} left ({len(given)} of '
                         f'{len(rows)} tasks give days)</span>')
        body.append(f'<div class="sum">{"".join(chips)}</div>')
        svg, width = graph(rows)
        ordered = in_order(rows)
        body.append(f'<div class="wrap{" wide" if width > 730 else ""}">'
                    f'<div class="panel graph"><h2>Order and gates</h2>{svg}'
                    '<div class="leg"><span><b style="color:#22A06B">──▶</b> gate met</span>'
                    '<span><b style="color:#626F86">- - ▶</b> gate not met yet</span>'
                    "<span><b>merged</b> starts when the task before is merged · "
                    "<b>approved</b> when it is accepted</span></div></div>"
                    f'<div class="panel"><h2>Tasks in order</h2><ul class="list">'
                    f'{"".join(_li(r) for r in ordered)}</ul></div></div>')
    else:
        body.append('<div class="panel"><p class="none">No plan under way: every planned task '
                    "is done.</p></div>")
    if earlier:
        at = f" (finished {_when(data['earlierAt'])})" if data.get("earlierAt") else ""
        body.append(f'<div class="panel earlier"><h2>Earlier{e(at)}</h2><ul class="list">'
                    f'{"".join(_li(r) for r in sorted(earlier, key=_numkey))}</ul></div>')
    body.append(f'<p class="foot">{FILE_NAME} · one page per project, rewritten in place. A plan '
                "leaves it once all its tasks are done; the last finished plan stays under "
                "“Earlier”.</p>")
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{name}: the plan</title><style>{_CSS}</style></head><body>"
            + "\n".join(body) + "</body></html>\n")
