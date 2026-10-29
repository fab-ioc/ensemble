"""Reports that do not wake their PO (#288).

A task's ``update`` report and an ``approved`` review verdict ask nothing of
the PO: #287 measured 15-29 Oct that the wakes they caused cost about 0.6B
tokens (3% of everything), each one a whole PO turn spent reading a line it
could act on later. They are posted in the PO's chat at their time as before
(``chatroom.post_report(wake=False)``: shown, ringing nobody, so the
attention detector waits for no answer) and their one-line gist waits here,
in ``DASHBOARD_DIR/quiet_reports.json``, by PO room:

* **With the PO's next hub line** — a completed report, a digest, a
  verdict that rings: ``_type_input_now`` takes them from the queue and adds
  them to that line (:func:`rider`), and puts them back when the line is not
  taken (:func:`put_back`). The person's own words to the PO, and a wake of
  a PO running in a visible terminal (``BACKEND.send_text``), do not carry
  them: they wait for the next hub line or the batch.
* **Or after** :data:`IDLE_S` **(30 minutes)** with no wake, when the PO is
  running and idle: one batched ``[quiet reports]`` line (:func:`tick`,
  riding the progress check's once-a-minute loop like ``po_messages``). A
  stopped PO is not started for them; they ride its next wake.

``completed``, ``question``, ``blocked``, any other verdict and the review
limit still wake at once (ensemble_tools).

Bound to the dashboard module like ``po_messages``: nothing here reads
``_d`` at import time.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import durable

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


PREFIX = "[quiet reports] "        # the batched line (dashboard.HUB_INPUT_KINDS)
IDLE_S = 30 * 60                   # a quiet report waits this long for a wake
TICK_S = 60
_ITEM_MAX = 160                    # one report's gist
_LINE_MAX = 900                    # the whole addition: a TUI takes one line
_LOCK = threading.Lock()
_STATE: dict | None = None         # {poRoomId: [{id, at, line, taskId}]}
_LAST = 0.0


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] quiet-reports: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "quiet_reports.json"


def _load() -> dict:
    global _STATE
    if _STATE is None:
        try:
            d = json.loads(_state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        _STATE = {k: [i for i in v if isinstance(i, dict) and i.get("id")]
                  for k, v in (d if isinstance(d, dict) else {}).items() if isinstance(v, list)}
    return _STATE


def _save() -> None:
    try:
        durable.write_text(_state_file(), json.dumps(_STATE or {}, indent=1, ensure_ascii=False))
    except OSError as e:
        _log(f"cannot save the queue: {e}")


def reset() -> None:
    """Forget the in-memory copy (tests, or a state file replaced by hand)."""
    global _STATE, _LAST
    with _LOCK:
        _STATE, _LAST = None, 0.0


def gist(what: str, text: str) -> str:
    """One report as a queued item: ``update from #12 (claude): first words…``."""
    flat = " ".join((text or "").split())
    if len(flat) > _ITEM_MAX:
        flat = flat[:_ITEM_MAX] + "…"
    return f"{what}: {flat}" if flat else what


def hold(po_room_id: str, msg_id: str, line: str, task_id: str = "",
         now: float | None = None) -> None:
    """Queue one quiet report for the PO of ``po_room_id``."""
    if not po_room_id or not line:
        return
    with _LOCK:
        st = _load()
        st.setdefault(po_room_id, []).append(
            {"id": msg_id or f"q{int(time.time() * 1000)}", "at": now or time.time(),
             "line": line, "taskId": task_id})
        _save()


def pending(po_room_id: str) -> list[dict]:
    with _LOCK:
        return list(_load().get(po_room_id) or [])


def _text(items: list[dict]) -> tuple[str, list[str]]:
    """``(the items as one clause, the ids it names)``, cut to _LINE_MAX."""
    told, parts = [], []
    for it in sorted(items, key=lambda i: i.get("at") or 0):
        if len("; ".join(parts + [it["line"]])) > _LINE_MAX - 60:
            break
        parts.append(it["line"])
        told.append(it["id"])
    rest = len(items) - len(told)
    body = "; ".join(parts) + (f"; and {rest} more" if rest else "")
    return body, [it["id"] for it in items] if rest else told


def rider(po_room_id: str, identity: str) -> tuple[str, list[dict]]:
    """What a line typed to the PO of ``po_room_id`` carries along: ``(" |
    Quiet reports since your last wake: ...", items)``, ("", []) when nothing
    waits or ``identity`` is not that room's PO. The items leave the queue
    here, under the lock, so two lines typed at once never both carry them;
    :func:`put_back` returns them when the line is not taken."""
    room = _d.chatroom.get_room(po_room_id) if po_room_id else None
    if room is None or _d.chatroom.po_identity(room) != identity:
        return "", []
    with _LOCK:
        st = _load()
        items = list(st.get(po_room_id) or [])
        if not items:
            return "", []
        body, ids = _text(items)
        taken = [i for i in items if i["id"] in set(ids)]
        left = [i for i in items if i["id"] not in set(ids)]
        if left:
            st[po_room_id] = left
        else:
            st.pop(po_room_id, None)
        _save()
    return (f" | Quiet reports since your last wake (in your chat; nothing to answer): "
            f"{body}."), taken


def put_back(po_room_id: str, items: list[dict]) -> None:
    """The line that carried ``items`` was not taken: they wait again."""
    if not items:
        return
    with _LOCK:
        st = _load()
        have = {i.get("id") for i in st.get(po_room_id) or []}
        st[po_room_id] = sorted([i for i in items if i.get("id") not in have] + st.get(po_room_id, []),
                                key=lambda i: i.get("at") or 0)
        _save()


def told(po_room_id: str, ids: list[str]) -> None:
    """The line carrying ``ids`` is in: they leave the queue."""
    if not ids:
        return
    gone = set(ids)
    with _LOCK:
        st = _load()
        left = [i for i in st.get(po_room_id) or [] if i.get("id") not in gone]
        if left:
            st[po_room_id] = left
        else:
            st.pop(po_room_id, None)
        _save()


def batch_line(items: list[dict]) -> tuple[str, list[str]]:
    body, ids = _text(items)
    return (f"{PREFIX}{len(items)} report{'s' if len(items) != 1 else ''} that did not wake "
            f"you, all in your chat, nothing to answer unless one needs you: {body}."), ids


def tick(now: float | None = None) -> list[str]:
    """Type the batched line to each PO whose oldest quiet report has waited
    IDLE_S, when it is running and idle. Returns the PO rooms typed to."""
    now = now or time.time()
    with _LOCK:
        due = {rid: list(items) for rid, items in _load().items()
               if items and now - min(i.get("at") or now for i in items) >= IDLE_S}
    out = []
    for rid, items in due.items():
        room = _d.chatroom.get_room(rid)
        if room is None:
            told(rid, [i["id"] for i in items])     # the PO room is gone
            continue
        with _d.rotation.GATE:
            sess = _d.po_messages._target(rid, require_idle=True)
        if sess is None:
            continue
        # Taken from the queue under the lock: a line typed meanwhile may
        # have carried some, and the rider must not add these to this line.
        with _LOCK:
            st = _load()
            items = list(st.get(rid) or [])
            line, ids = batch_line(items) if items else ("", [])
            st[rid] = [i for i in items if i["id"] not in set(ids)]
            if not st[rid]:
                st.pop(rid, None)
            _save()
        if not ids:
            continue
        if _d._type_input(sess, line):
            out.append(rid)
            _log(f"typed {len(ids)} to the PO of {rid}")
        else:
            put_back(rid, [i for i in items if i["id"] in set(ids)])
    return out


def maybe_tick() -> None:
    """From the progress check's scheduler loop: once per TICK_S."""
    global _LAST
    now = time.time()
    if now - _LAST >= TICK_S:
        _LAST = now
        tick(now)
