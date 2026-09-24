"""Messages between the POs of two projects (``ensemble_message_po``).

A project that depends on another one (the opTen PO on the Dock library) has
bugs to report and questions to ask there. Before this, the only way across
was typing into the other PO's terminal (``po-tools/tell.py``), which on
2026-09-24 arrived cut to its last sentence and read like the CEO's words.

**A message** is stored whole in both PO rooms' chats, as a ``pomsg`` balloon
with the same id: the sender's room keeps it as sent, the target's as
received. Each carries who writes to whom (``fromProjectName`` →
``toProjectName``: the page draws "opten PO → Dock PO"), the ``poKind`` (bug,
question, answer, info) and, for a reply, ``replyTo``. It is not the CEO's
message, so it makes no points for him; it is no hub traffic either, so the
page never folds it away as such. Neither copy rings anyone by itself
(``rang`` is empty): the hub types the target PO one short line instead::

    [from the opten PO] bug: The splitter jumps on drop — read it in full with ensemble_read_message id=pm-1a2b3c4d.

and the PO reads the whole text with ``ensemble_read_message``. Nothing long
is ever typed, so nothing is cut.

**When the line is typed.** As a task's report is (``_ring_report``), at once,
when the message is one that wakes and the target PO is running. Otherwise it
waits in :data:`pending` (``DASHBOARD_DIR/po_messages.json``) and the hub's
once-a-minute look (riding the progress check's loop, like ``due.py``) types
it when the PO is running and idle:

* **A PO that is not running** is not resumed for it (reports never resume a
  PO either): the message waits in its chat and it is typed the line when it
  runs again and is idle, as a ``[due]`` item is.
* **Quiet kinds** — ``info``, and an ``answer`` to anything but a ``bug`` or a
  ``question`` — never wake the target by themselves: an answer to a
  question is what its asker waits for, but a thank-you for an answer is
  not, and that is how two POs would otherwise ping-pong. A quiet message is
  told with the next line typed to that PO (the other PO's next bug or
  question), or when the PO is idle and the message has waited
  :data:`QUIET_WAIT_S`; it is in the PO's chat all along.
* **At most** :data:`WAKES_PER_HOUR` lines per pair of projects in any hour,
  both directions together. Past that, messages of that pair are *held*:
  they wait until the hour allows, and the target PO shows on the CEO's bell
  (``attention``: ``held_for_room``) with how many are held and why.

A line names every message waiting for that PO that it may tell (as many as
fit in :data:`_WAKE_MAX`); each told message leaves the queue.

Bound to the dashboard module like ``due``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


KINDS = ("bug", "question", "answer", "info")
WAKING = ("bug", "question")        # an answer to one of these wakes too
WAKES_PER_HOUR = 6                  # typed lines per pair of projects, per hour
WINDOW_S = 3600
QUIET_WAIT_S = 30 * 60              # a quiet message waits this long for company
TICK_S = 60
_FIRST_MAX = 200                    # a message's first line in the typed line
_WAKE_MAX = 900                     # the whole line: a TUI takes one line
KIND = "pomsg"                      # the chat message's kind
TOOL_HINT = "ensemble_read_message id="

_LOCK = threading.RLock()
_LAST = 0.0


class Refused(Exception):
    """A message that cannot be sent: the words say why."""


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] po-messages: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# State: what waits, and the lines typed per pair
# ---------------------------------------------------------------------------

def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "po_messages.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    pending = [p for p in d.get("pending") or [] if isinstance(p, dict) and p.get("id")]
    wakes = {k: [float(t) for t in v if isinstance(t, (int, float))]
             for k, v in (d.get("wakes") or {}).items() if isinstance(v, list)}
    return {"pending": pending, "wakes": wakes}


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save the queue: {e}")


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a or "", b or "")))


def _recent(state: dict, key: str, now: float) -> list[float]:
    return [t for t in state["wakes"].get(key, []) if now - t < WINDOW_S]


def wakes_by_itself(kind: str, replied_kind: str = "") -> bool:
    """Whether a message of this kind wakes its target on its own."""
    return kind in WAKING or (kind == "answer" and replied_kind in WAKING)


# ---------------------------------------------------------------------------
# Who: projects and their POs
# ---------------------------------------------------------------------------

def find_project(ref: str) -> dict:
    """A project by its id, else by its name (any case)."""
    ref = str(ref or "").strip()
    if not ref:
        raise Refused("projectId is required: the project whose PO you write to (its id or name)")
    projects = _d.load_projects()
    hit = next((p for p in projects if p.get("id") == ref), None)
    if hit:
        return hit
    named = [p for p in projects if (p.get("name") or "").strip().lower() == ref.lower()]
    if len(named) == 1:
        return named[0]
    if named:
        raise Refused(f"more than one project is called '{ref}': "
                      + ", ".join(p["id"] for p in named) + " — give its id")
    raise Refused(f"no project '{ref}' (ensemble_list_tasks / the board name them)")


def _po_of(project: dict) -> tuple[dict, str]:
    """(the PO's room, its identity) of a project, or Refused."""
    rid = (project.get("poRoomId") or "").strip()
    room = _d.chatroom.get_room(rid, public=False) if rid else None
    ident = _d.chatroom.po_identity(room) if room else ""
    if not room or not ident:
        raise Refused(f"project '{project.get('name') or project['id']}' has no PO to write to")
    return room, ident


def _name(project: dict) -> str:
    return (project.get("name") or project.get("id") or "?").strip()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def messages_in(room: dict) -> list[dict]:
    return [m for m in (room or {}).get("messages") or [] if m.get("kind") == KIND]


def find(room: dict, msg_id: str) -> dict | None:
    msg_id = str(msg_id or "").strip()
    return next((m for m in messages_in(room) if m.get("id") == msg_id), None)


def first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        ln = " ".join(re.sub(r"^[#>\-*+\s]+|[*`_]{1,3}", "", ln).split())
        if ln and not re.fullmatch(r"[-=|:\s]+", ln):
            return ln if len(ln) <= _FIRST_MAX else ln[:_FIRST_MAX - 1] + "…"
    return ""


def view(m: dict, full: bool = True) -> dict:
    out = {"id": m.get("id"), "direction": m.get("direction"), "kind": m.get("poKind"),
           "from": f"{m.get('fromProjectName')} PO", "to": f"{m.get('toProjectName')} PO",
           "fromProjectId": m.get("fromProjectId"), "toProjectId": m.get("toProjectId"),
           "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("ts") or 0))}
    if m.get("replyTo"):
        out["replyTo"] = m["replyTo"]
    if full:
        out["text"] = m.get("text", "")
    else:
        out["firstLine"] = first_line(m.get("text", ""))
    return out


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send(room: dict, identity: str, project_id: str, target_ref: str, text: str,
         kind: str = "", reply_to: str = "", now: float | None = None) -> dict:
    """Store a message from the PO of ``project_id`` (in ``room``) in both PO
    rooms and deliver its line to the target PO by the rules above. The
    caller has checked it is a PO. Returns what happened, for the tool."""
    now = time.time() if now is None else now
    text = (text or "").strip()
    if not text:
        raise Refused("text is required — write the whole message in it")
    projects = {p["id"]: p for p in _d.load_projects()}
    me = projects.get(project_id)
    if not me:
        raise Refused("your task has no project, so there is no PO to write as")
    replied = None
    if str(reply_to or "").strip():
        replied = find(room, reply_to)
        if replied is None:
            raise Refused(f"no PO message {reply_to} in your chat (ensemble_read_message lists them)")
        other = (replied.get("fromProjectId") if replied.get("direction") == "received"
                 else replied.get("toProjectId"))
        target = projects.get(other or "")
        if not target:
            raise Refused("the project that message came from is gone")
        if str(target_ref or "").strip() and find_project(target_ref)["id"] != target["id"]:
            raise Refused(f"message {reply_to} is between you and '{_name(target)}': "
                          "a reply goes there (leave projectId out)")
    else:
        target = find_project(target_ref)
    if target["id"] == me["id"]:
        raise Refused("that is your own project: a PO does not message itself")
    kind = (kind or "").strip().lower() or ("answer" if replied else "question")
    if kind not in KINDS:
        raise Refused(f"kind must be one of: {', '.join(KINDS)}")
    to_room, to_ident = _po_of(target)
    if to_room["id"] == room["id"]:
        raise Refused(f"'{_name(target)}' is led from this same chat: there is nobody else to tell")
    mid = "pm-" + uuid.uuid4().hex[:8]
    meta = {"id": mid, "poKind": kind, "fromProjectId": me["id"], "fromProjectName": _name(me),
            "toProjectId": target["id"], "toProjectName": _name(target),
            "fromRoomId": room["id"], "toRoomId": to_room["id"],
            **({"replyTo": replied["id"]} if replied else {})}
    got = _d.chatroom.post_po_message(to_room["id"], f"{identity}@{room['id']}", to_ident, text,
                                      {**meta, "direction": "received"})
    if got is None:
        raise Refused(f"the PO chat of '{_name(target)}' could not be written")
    _d.chatroom.post_po_message(room["id"], identity, f"{to_ident}@{to_room['id']}", text,
                                {**meta, "direction": "sent"})
    wakes = wakes_by_itself(kind, (replied or {}).get("poKind", ""))
    item = {"id": mid, "toRoomId": to_room["id"], "fromProjectId": me["id"],
            "toProjectId": target["id"], "fromName": _name(me), "kind": kind,
            "firstLine": first_line(text), "wake": wakes, "at": now,
            **({"replyTo": replied["id"]} if replied else {})}
    with _LOCK:
        state = _load()
        state["pending"].append(item)
        _save(state)
        how = _deliver(state, to_room["id"], now, require_idle=False) if wakes else ""
    told = how == "typed"
    held = False
    if not told:
        with _LOCK:
            st = _load()
            held = any(p["id"] == mid and p.get("heldSince") for p in st["pending"])
    who = f"the {_name(target)} PO"
    if told:
        note = f"delivered: {who} was typed a line and reads the whole text with ensemble_read_message"
    elif held:
        note = (f"held: {WAKES_PER_HOUR} lines between your projects in the last hour already. "
                f"It is in {who}'s chat, and {who} is told when the hour allows; "
                f"{_d.operator_name()} sees it is held")
    elif not wakes:
        note = (f"in {who}'s chat; a{'n' if kind[0] in 'aeiou' else ''} {kind} does not wake it: it is "
                f"told with its next PO message, or when it is idle within "
                f"{QUIET_WAIT_S // 60} minutes")
    else:
        note = (f"in {who}'s chat; {who} is not running or is busy being replaced, so it is "
                f"told when it runs and is idle (it is not started for this)")
    _log(f"{mid} {kind} {_name(me)} → {_name(target)}: {how or ('held' if held else 'waits')}")
    return {"ok": True, "id": mid, "kind": kind, "to": f"{_name(target)} PO",
            "toProjectId": target["id"], "delivered": told, "held": held, "note": note}


# ---------------------------------------------------------------------------
# Delivering
# ---------------------------------------------------------------------------

def _line_for(p: dict) -> str:
    return (f"[from the {p['fromName']} PO] {p['kind']}: {p['firstLine'] or '(no text)'}"
            f"{' (a reply to ' + p['replyTo'] + ')' if p.get('replyTo') else ''}")


def wake_line(items: list[dict]) -> tuple[str, list[dict]]:
    """(the typed line, the messages it tells). The first always; the others
    while the line stays under _WAKE_MAX."""
    def line(told):
        head = f"{_line_for(told[0])} — read it in full with {TOOL_HINT}{told[0]['id']}."
        if len(told) == 1:
            return head
        rest = "; ".join(f"{_line_for(p)} — id={p['id']}" for p in told[1:])
        return f"{head} Also waiting for you: {rest}. Read each the same way."

    told = items[:1]
    for p in items[1:]:
        if len(line(told + [p])) > _WAKE_MAX:
            break
        told.append(p)
    return line(told)[:_WAKE_MAX], told


def _target(room_id: str, require_idle: bool):
    """The target PO's live terminal, or None when a line cannot be typed now:
    not running, being rotated, about to be replaced, or (require_idle) busy."""
    rot, cr = _d.rotation, _d.chatroom
    room = cr.get_room(room_id)
    if room is None:
        return None
    ident = cr.po_identity(room)
    part = cr.participant(room, ident) if ident else None
    if not part or rot.is_rotating(room_id, ident) or rot.awaiting_handover(room_id, ident):
        return None
    sess = rot._pty(part)
    if sess is None:
        return None
    if require_idle:
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return None
    return sess


def _deliver(state: dict, room_id: str, now: float, require_idle: bool) -> str:
    """Type one line to the PO of ``room_id`` if anything waiting for it may
    be told now. Returns ``typed`` or "". Saves the state it changes."""
    mine = [p for p in state["pending"] if p.get("toRoomId") == room_id]
    if not mine:
        return ""
    over = {k for k in {pair_key(p["fromProjectId"], p["toProjectId"]) for p in mine}
            if len(_recent(state, k, now)) >= WAKES_PER_HOUR}
    free = [p for p in mine if pair_key(p["fromProjectId"], p["toProjectId"]) not in over]
    changed = False
    for p in mine:
        is_over = p not in free and (p.get("wake") or now - p["at"] >= QUIET_WAIT_S)
        if is_over and not p.get("heldSince"):
            p["heldSince"] = now
            changed = True
        elif not is_over and p.get("heldSince"):
            p.pop("heldSince", None)
            changed = True
    due = [p for p in free if p.get("wake") or now - p["at"] >= QUIET_WAIT_S]
    how = ""
    if due:
        with _d.rotation.GATE:
            sess = _target(room_id, require_idle)
            if sess is not None:
                # What wakes first, then what waited longest.
                order = sorted(free, key=lambda p: (p not in due, p["at"]))
                wake, told = wake_line(order)
                if _d._type_input(sess, wake):
                    how = "typed"
                    ids = {p["id"] for p in told}
                    state["pending"] = [p for p in state["pending"] if p["id"] not in ids]
                    for k in {pair_key(p["fromProjectId"], p["toProjectId"]) for p in told}:
                        state["wakes"][k] = _recent(state, k, now) + [now]
                    changed = True
                    _log(f"typed to the PO of {room_id}: {', '.join(sorted(ids))}")
    if changed:
        _save(state)
    return how


def tick(now: float | None = None) -> list[str]:
    """Look at every PO with messages waiting once. Returns the rooms typed to."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        # A PO room that is gone has nobody to tell: its messages stay in the
        # sender's chat, and leave the queue.
        alive = []
        for p in state["pending"]:
            if _d.chatroom.get_room(p.get("toRoomId", "")) is not None:
                alive.append(p)
            else:
                _log(f"{p['id']}: the PO room {p.get('toRoomId')} is gone — dropped from the queue")
        if len(alive) != len(state["pending"]):
            state["pending"] = alive
            _save(state)
        for k in list(state["wakes"]):
            if not _recent(state, k, now):
                state["wakes"].pop(k)
        out = []
        for rid in sorted({p["toRoomId"] for p in state["pending"]}):
            try:
                if _deliver(state, rid, now, require_idle=True):
                    out.append(rid)
            except Exception as e:          # one PO must not stop the others
                _log(f"{rid}: not delivered: {str(e)[:200]}")
        return out


def maybe_tick() -> None:
    """From the progress check's scheduler loop: once per TICK_S."""
    global _LAST
    now = time.time()
    if not _LAST:
        _LAST = now
        return
    if now - _LAST >= TICK_S:
        _LAST = now
        tick(now)


# ---------------------------------------------------------------------------
# The CEO's bell
# ---------------------------------------------------------------------------

_HELD_CACHE: tuple = (0.0, 0.0, {})     # (file mtime, read at, result)


def held_by_room() -> dict[str, dict]:
    """PO rooms with messages held by the hourly limit: ``{roomId: {count,
    since, reason}}``. Read from the queue file only when it changed."""
    global _HELD_CACHE
    try:
        mtime = _state_file().stat().st_mtime
    except OSError:
        return {}
    if _HELD_CACHE[0] == mtime:
        return _HELD_CACHE[2]
    out: dict[str, dict] = {}
    for p in _load()["pending"]:
        if not p.get("heldSince"):
            continue
        o = out.setdefault(p["toRoomId"], {"count": 0, "since": p["heldSince"], "from": set()})
        o["count"] += 1
        o["since"] = min(o["since"], p["heldSince"])
        o["from"].add(p.get("fromName", "?"))
    for o in out.values():
        names = sorted(o.pop("from"))
        o["reason"] = (f"{o['count']} message{'s' if o['count'] > 1 else ''} from the "
                       f"{' and '.join(names)} PO held: more than {WAKES_PER_HOUR} "
                       f"PO-to-PO wakes between the two projects in an hour")
    _HELD_CACHE = (mtime, time.time(), out)
    return out
