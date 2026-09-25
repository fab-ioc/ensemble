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

    [from the opten PO] bug: The splitter jumps on drop — read it with ensemble_read_message id=pm-1a2b3c4d (the id is for the tools only; in text call it "opten PO's bug of 09-25 18:57")

and the PO reads the whole text with ``ensemble_read_message``. Nothing long
is ever typed, so nothing is cut.

**Its name.** The id is a handle for the tools; people read a message by its
name, who wrote it, what kind and when: "opten PO's bug of 09-25 18:57"
(:func:`name_of`). It is fixed when the message is sent and kept in both
copies (``name``), so the line, the tools and the page all say the same.

**When the line is typed.** As a task's report is (``_ring_report``), at once,
when the message is one that wakes and the target PO is running (or stopped:
see below). Otherwise it waits in :data:`pending` (``DASHBOARD_DIR/po_messages.json``) and the hub's
once-a-minute look (riding the progress check's loop, like ``due.py``) types
it when the PO is running and idle:

* **A PO that is stopped** (no live terminal; not being rotated, asked for
  its handover or replaced) is resumed for a message that wakes, the way a
  send to a stopped chat is (``Handler._resume_room``): the line is its first
  input (in a room with more agents, where that would post it as the CEO's
  words, it is typed at the first look that finds the PO settled), and the
  room's launch guard never starts a second terminal for the seat. A PO with
  no recorded conversation is never started blank. The resume counts as the line for the hourly limit. The messages stay
  queued, marked ``resuming``, until the line is in; a resume that fails
  (nothing to resume, a refused or failed start, the line not typed) leaves
  them queued, logged once, and the PO on the CEO's bell as not receiving
  them, and the next look tries again. A PO running but busy waits for idle.
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
    # Marked before its line was typed and never cleared: typed (or the hub
    # died typing it). At most once — the whole text is in the chat anyway.
    typing = [p["id"] for p in pending if p.get("typing")]
    if typing:
        _log(f"{', '.join(typing)}: marked as being typed — taken as told, not typed again")
        pending = [p for p in pending if not p.get("typing")]
    wakes = {k: [float(t) for t in v if isinstance(t, (int, float))]
             for k, v in (d.get("wakes") or {}).items() if isinstance(v, list)}
    return {"pending": pending, "wakes": wakes}


def _save(state: dict) -> bool:
    """Write the queue. False when it could not be written: the file is the
    queue, so callers must not act as if the change were kept."""
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        return True
    except OSError as e:
        _log(f"cannot save the queue: {e}")
        return False


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


def name_of(m: dict) -> str:
    """What people call a PO message: "opten PO's question of 09-25 18:57".
    The name given when it was sent, else made from what it keeps."""
    if m.get("name"):
        return m["name"]
    ts = m.get("ts") or m.get("at") or 0
    who = m.get("fromProjectName") or m.get("fromName") or "?"
    kind = m.get("poKind") or m.get("kind") or "message"
    return f"{who} PO's {kind} of {time.strftime('%m-%d %H:%M', time.localtime(ts))}"


def view(m: dict, full: bool = True, room: dict | None = None) -> dict:
    out = {"id": m.get("id"), "name": name_of(m), "direction": m.get("direction"), "kind": m.get("poKind"),
           "from": f"{m.get('fromProjectName')} PO", "to": f"{m.get('toProjectName')} PO",
           "fromProjectId": m.get("fromProjectId"), "toProjectId": m.get("toProjectId"),
           "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("ts") or 0))}
    if m.get("replyTo"):
        out["replyTo"] = m["replyTo"]
        replied = find(room, m["replyTo"]) if room else None
        if m.get("replyName") or replied:
            out["replyToName"] = m.get("replyName") or name_of(replied)
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
    name = name_of({"fromProjectName": _name(me), "poKind": kind, "ts": now})
    reply = {"replyTo": replied["id"], "replyName": name_of(replied)} if replied else {}
    meta = {"id": mid, "name": name, "poKind": kind, "fromProjectId": me["id"],
            "fromProjectName": _name(me), "toProjectId": target["id"], "toProjectName": _name(target),
            "fromRoomId": room["id"], "toRoomId": to_room["id"], **reply}
    got = _d.chatroom.post_po_message(to_room["id"], f"{identity}@{room['id']}", to_ident, text,
                                      {**meta, "direction": "received"})
    if got is None:
        raise Refused(f"the PO chat of '{_name(target)}' could not be written")
    _d.chatroom.post_po_message(room["id"], identity, f"{to_ident}@{to_room['id']}", text,
                                {**meta, "direction": "sent"})
    wakes = wakes_by_itself(kind, (replied or {}).get("poKind", ""))
    item = {"id": mid, "toRoomId": to_room["id"], "fromProjectId": me["id"],
            "toProjectId": target["id"], "fromName": _name(me), "kind": kind,
            "firstLine": first_line(text), "wake": wakes, "at": now, "name": name, **reply}
    with _LOCK:
        state = _load()
        state["pending"].append(item)
        queued = _save(state)
        if wakes and queued:
            _deliver(state, to_room["id"], now, require_idle=False)
    held = starting = False
    who = f"the {_name(target)} PO"
    if not queued:
        _log(f"{mid} {kind} {_name(me)} → {_name(target)}: in both chats, not queued")
        return {"ok": True, "id": mid, "name": name, **_answers(replied), "kind": kind,
                "to": f"{_name(target)} PO", "toProjectId": target["id"], "delivered": False, "held": False, "queued": False,
                "note": (f"in {who}'s chat, but the hub could not save its list of messages to "
                         f"tell, so {who} is NOT told of it: send it again later, or tell "
                         f"{_d.operator_name()}")}
    # What became of this message, not of the look: a look may have told
    # only what waited before it.
    with _LOCK:
        mine = next((p for p in _load()["pending"] if p["id"] == mid), None)
    told = mine is None
    if mine is not None:
        held = bool(mine.get("heldSince"))
        starting = bool(mine.get("resuming"))
    if told:
        note = f"delivered: {who} was typed a line and reads the whole text with ensemble_read_message"
    elif starting:
        note = (f"{who} was not running, so the hub is starting it; the line is typed as its "
                f"first input once it is up, and it reads the whole text with "
                f"ensemble_read_message")
    elif held:
        note = (f"held: {WAKES_PER_HOUR} lines between your projects in the last hour already. "
                f"It is in {who}'s chat, and {who} is told when the hour allows; "
                f"{_d.operator_name()} sees it is held")
    elif not wakes:
        note = (f"in {who}'s chat; a{'n' if kind[0] in 'aeiou' else ''} {kind} does not wake it: it is "
                f"told with its next PO message, or when it is idle within "
                f"{QUIET_WAIT_S // 60} minutes")
    else:
        note = (f"in {who}'s chat; {who} is busy, being replaced, or could not be started, so "
                f"it is told when it is idle (a stopped PO is started for it, within a minute)")
    _log(f"{mid} {kind} {_name(me)} → {_name(target)}: "
         f"{'typed' if told else 'starting the PO' if starting else 'held' if held else 'waits'}")
    return {"ok": True, "id": mid, "name": name, **_answers(replied), "kind": kind,
            "to": f"{_name(target)} PO", "toProjectId": target["id"], "delivered": told, "held": held,
            **({"starting": True} if starting else {}), "note": note}


def _answers(replied: dict | None) -> dict:
    return {"answers": name_of(replied), "answersId": replied["id"]} if replied else {}


# ---------------------------------------------------------------------------
# Delivering
# ---------------------------------------------------------------------------

def _line_for(p: dict) -> str:
    reply = p.get("replyName") or p.get("replyTo")
    return (f"[from the {p['fromName']} PO] {p['kind']}: {p['firstLine'] or '(no text)'}"
            f"{' (a reply to ' + reply + ')' if reply else ''}")


def _called(p: dict) -> str:
    return f'in text call it "{name_of(p)}"'



def wake_line(items: list[dict]) -> tuple[str, list[dict]]:
    """(the typed line, the messages it tells). The first always; the others
    while the line stays under _WAKE_MAX."""
    def line(told):
        first = told[0]
        head = (f"{_line_for(first)} — read it with {TOOL_HINT}{first['id']} "
                f"(the id is for the tools only; {_called(first)})")
        if len(told) == 1:
            return head
        rest = "; ".join(f"{_line_for(p)} — id={p['id']} ({_called(p)})" for p in told[1:])
        return f"{head}. Also waiting for you: {rest}. Read each the same way."

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
    before_how = ""
    if any(p.get("resuming") for p in mine):
        # A resume started for them is under way: nothing else until it is
        # done (a second one would start the PO twice, and a line typed into
        # a PO still coming up can be lost). Once done, what came meanwhile.
        got = _resume_outcome(state, room_id, now)
        if got != "done":
            return "typed" if got == "typed" else ""
        before_how = "typed"
        mine = [p for p in state["pending"] if p.get("toRoomId") == room_id]
        if not mine:
            return before_how
        free = [p for p in mine if pair_key(p["fromProjectId"], p["toProjectId"]) not in over]
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
    stopped = None
    if due:
        with _d.rotation.GATE:
            sess = _target(room_id, require_idle)
            if sess is None:
                stopped = _stopped(room_id)
            else:
                # What wakes first, then what waited longest.
                order = sorted(free, key=lambda p: (p not in due, p["at"]))
                wake, told = wake_line(order)
                typed = _type_line(state, room_id, sess, wake, told, now, count=True)
                if typed is None:
                    return before_how
                changed = True
                how = typed
    if stopped is not None and any(p.get("wake") for p in due):
        # Only a message that wakes starts a PO; a quiet one goes along.
        how = _resume(state, stopped, free, due, now)
    if changed:
        _save(state)
    return how or before_how


def _type_line(state: dict, room_id: str, sess, wake: str, told: list[dict], now: float,
               count: bool) -> str | None:
    """Type ``wake`` (telling ``told``) into the PO's terminal. Returns
    ``typed``, "" when the terminal did not take it, None when the mark
    could not be saved (nothing typed). ``count``: the line counts for the
    hourly limit (not when the resume that brought the PO up was counted)."""
    ids = {p["id"] for p in told}
    # Marked on disk before typing, so a failed save afterwards can never
    # type the same line twice (_load drops the mark). The wake is counted
    # in the same write, so the hourly limit holds even when the save after
    # typing fails.
    keys = {pair_key(p["fromProjectId"], p["toProjectId"]) for p in told} if count else set()
    before = {k: list(state["wakes"].get(k, [])) for k in keys}
    for p in told:
        p["typing"] = now
    for k in keys:
        state["wakes"][k] = _recent(state, k, now) + [now]
    if not _save(state):
        for p in told:
            p.pop("typing", None)
        state["wakes"].update(before)
        _log(f"not typed to the PO of {room_id}: the queue could not be saved")
        return None
    if _d._type_input(sess, wake):
        state["pending"] = [p for p in state["pending"] if p["id"] not in ids]
        _log(f"typed to the PO of {room_id}: {', '.join(sorted(ids))}")
        return "typed"
    for p in told:
        p.pop("typing", None)
    state["wakes"].update(before)
    return ""


def _stopped(room_id: str) -> dict | None:
    """The target PO's room when its PO is stopped and may be resumed for a
    message: no live terminal, and not being rotated, asked for its
    handover or replaced by a PO switch. None otherwise."""
    rot, cr = _d.rotation, _d.chatroom
    room = cr.get_room(room_id, public=False)
    if room is None:
        return None
    ident = cr.po_identity(room)
    part = cr.participant(room, ident) if ident else None
    if not part or part.get("kind") != "agent" or rot._pty(part) is not None:
        return None
    if (rot.is_rotating(room_id, ident) or rot.awaiting_handover(room_id, ident)
            or rot.room_rotating(room_id) or rot.switching(room_id)):
        return None
    return room


def _cannot_resume(room: dict) -> str:
    """Why the PO of this stopped room cannot be resumed, or ""."""
    part = _d.chatroom.participant(room, _d.chatroom.po_identity(room)) or {}
    if not room.get("launched", True):
        return ""                           # a first launch: its spec comes first
    if not part.get("sessionId"):
        # Never a blank PO, nor a guess at its conversation (a codex seat
        # would otherwise take the latest rollout in its folder, if any).
        return "it has no recorded conversation to resume"
    return ""


def _flag(items: list[dict], room_id: str, why: str, now: float) -> None:
    """The PO could not be started for these: they stay queued, the CEO's
    bell shows it, and the log says so once per reason."""
    if any((p.get("undelivered") or {}).get("why") != why for p in items):
        _log(f"not delivered to the stopped PO of {room_id} "
             f"({', '.join(p['id'] for p in items)}): {why}")
    for p in items:
        was = p.get("undelivered") or {}
        p["undelivered"] = {"since": was.get("since") or now, "why": why}


def _resume(state: dict, room: dict, free: list[dict], due: list[dict], now: float) -> str:
    """Resume the stopped PO of ``room`` with the line as its first input
    (``Handler._resume_room``: the path of a send to a stopped chat, one
    resume per room, never a second terminal for a seat). The messages
    stay queued, marked ``resuming``, until that resume has typed the line
    (``_resume_outcome``). Returns ``resuming``, ``typed`` or ""; saves."""
    room_id = room["id"]
    order = sorted(free, key=lambda p: (p not in due, p["at"]))
    wake, told = wake_line(order)
    why = _cannot_resume(room)
    if why:
        _flag(told, room_id, why, now)
        _save(state)
        return ""
    agents = _d.chatroom.agent_participants(room)
    team = room.get("mode") != "solo" and len(agents) > 1
    # A team's resume would post the line in its chat as the CEO's words:
    # there the room is brought back without it, and the line is typed at the
    # first look that finds the PO settled (``_resume_outcome``); nothing
    # else is typed to it before, and the line is not counted again.
    key = "" if team else f"pomsg:{told[0]['id']}"
    keys = {pair_key(p["fromProjectId"], p["toProjectId"]) for p in told}
    before = {k: list(state["wakes"].get(k, [])) for k in keys}
    # As a typed line: marked and counted in one write before the resume,
    # so it is never started twice for the same line.
    for p in told:
        p["resuming"] = {"at": now, "key": key}      # ``at``: the wake counted for it
    for k in keys:
        state["wakes"][k] = _recent(state, k, now) + [now]

    def undo():
        for p in told:
            p.pop("resuming", None)
        state["wakes"].update(before)

    if not _save(state):
        undo()
        _log(f"the PO of {room_id} not started: the queue could not be saved")
        return ""
    try:
        if team:
            if not _d.hub_launcher()._start_or_resume_room(room):
                raise RuntimeError("no agent of this task could be started")
            _log(f"the stopped PO of {room_id} was resumed for "
                 f"{', '.join(p['id'] for p in told)}: typed once it is settled")
            return "resuming"
        result = _d.hub_launcher()._resume_room(room, text=wake, key=key)
    except Exception as e:      # noqa: BLE001 — a refusal or a failed spawn alike
        # The failed resume would keep the line for the room's Retry; this
        # queue keeps it instead, so it is never typed twice.
        if key:
            _d.discard_pending(room_id, key)
        undo()
        _flag(told, room_id, f"the PO could not be started: {str(e)[:200] or e.__class__.__name__}", now)
        _save(state)
        return ""
    if result.get("delivered"):
        # It came up between the look and the resume: typed straight in.
        ids = {p["id"] for p in told}
        state["pending"] = [p for p in state["pending"] if p["id"] not in ids]
        _save(state)
        _log(f"typed to the PO of {room_id}: {', '.join(sorted(ids))}")
        return "typed"
    _log(f"the stopped PO of {room_id} is being resumed for {', '.join(p['id'] for p in told)}")
    return "resuming"


def _resume_outcome(state: dict, room_id: str, now: float) -> str:
    """How the resume started for the messages marked ``resuming`` went:
    ``done`` (the line is in: they left the queue), ``typed`` (typed now, to
    a team's PO), ``failed`` (queued again and flagged), "" (still under
    way: wait).

    A line given to the resume (``key``): still held by it, wait; held as
    failed (the PO stopped, or sat on a prompt, before it was typed), taken
    back from the room so it is never typed twice. Not held: typed — or the
    hub restarted since, and as with a line being typed it counts as told.
    A team's (no key): typed once the PO is settled, not counted again (the
    resume was); the PO stopped again meanwhile, failed."""
    marked = [p for p in state["pending"] if p.get("toRoomId") == room_id and p.get("resuming")]
    key = marked[0]["resuming"].get("key", "")
    ids = {p["id"] for p in marked}
    if not key:
        with _d.rotation.GATE:
            sess = _target(room_id, require_idle=True)
            if sess is not None and _unsettled(sess):
                # A fresh terminal reads as idle before it has drawn its
                # screen, and a prompt can sit quiet: the resume's own test.
                if _unsettled(sess) == "prompt" and                         now - marked[0]["resuming"]["at"] >= _d.RESUME_NOTE_WAIT_S:
                    _flag(marked, room_id, "a prompt is on the PO's screen: answer it in its "
                                           "terminal, and the line is typed", now)
                    _save(state)
                return ""
            if sess is not None:
                wake, told = wake_line(sorted(marked, key=lambda p: (not p.get("wake"), p["at"])))
                for p in told:
                    p.pop("resuming", None)
                typed = _type_line(state, room_id, sess, wake, told, now, count=False)
                if typed:
                    _save(state)
                    return "typed"
                for p in told:
                    p["resuming"] = {"at": now, "key": ""}
                _save(state)
                return ""
            if _stopped(room_id) is None:
                return ""       # coming up, busy, or being rotated
        why = "the PO stopped before the line could be typed"
    else:
        held = _d.pending_input(room_id) or {}
        has = any(it.get("key") == key for it in held.get("items") or [])
        if has and held.get("state") != "failed":
            return ""
        if not has:
            state["pending"] = [p for p in state["pending"] if p["id"] not in ids]
            _save(state)
            _log(f"typed to the PO of {room_id} once it was resumed: {', '.join(sorted(ids))}")
            return "done"
        _d.discard_pending(room_id, key)
        why = f"the PO was started but the line was not typed: {held.get('error') or 'it stopped'}"
    # Nothing was delivered: the wake the resume counted is taken back.
    for at in {p["resuming"].get("at") for p in marked}:
        for k in {pair_key(p["fromProjectId"], p["toProjectId"]) for p in marked
                  if p["resuming"].get("at") == at}:
            ts = state["wakes"].get(k, [])
            if at in ts:
                ts.remove(at)
    for p in marked:
        p.pop("resuming", None)
    _flag(marked, room_id, why, now)
    _save(state)
    return "failed"


def _unsettled(sess) -> str:
    """Why a PO's terminal just brought up cannot be typed into yet, as the
    resume's delivery tests it (``Handler._deliver_after_resume``): ``starting``
    (no screen yet, or output within ``rotation.IDLE_S``), ``prompt``, or ""."""
    tail = sess.tail()
    if not tail or time.time() - sess.last_output < _d.rotation.IDLE_S:
        return "starting"
    return "prompt" if _d.attention.looks_like_prompt(tail) else ""


def tick(now: float | None = None) -> list[str]:
    """Look at every PO with messages waiting once. Returns the rooms typed to,
    or whose stopped PO was started for them."""
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
    """PO rooms with messages held by the hourly limit, or that their
    stopped PO could not be started for: ``{roomId: {count, since,
    reason}}``. Read from the queue file only when it changed."""
    global _HELD_CACHE
    try:
        mtime = _state_file().stat().st_mtime
    except OSError:
        return {}
    if _HELD_CACHE[0] == mtime:
        return _HELD_CACHE[2]
    out: dict[str, dict] = {}
    for p in _load()["pending"]:
        since = p.get("heldSince") or (p.get("undelivered") or {}).get("since")
        if not since:
            continue
        o = out.setdefault(p["toRoomId"], {"count": 0, "since": since, "from": set(),
                                           "held": 0, "why": ""})
        o["count"] += 1
        o["since"] = min(o["since"], since)
        o["from"].add(p.get("fromName", "?"))
        if p.get("heldSince"):
            o["held"] += 1
        else:
            o["why"] = o["why"] or p["undelivered"].get("why", "")
    for o in out.values():
        names = " and ".join(sorted(o.pop("from")))
        held, why = o.pop("held"), o.pop("why")
        n = o["count"]
        if held == n:
            o["reason"] = (f"{n} message{'s' if n > 1 else ''} from the {names} PO held: "
                           f"more than {WAKES_PER_HOUR} PO-to-PO wakes between the two "
                           f"projects in an hour")
        else:
            o["reason"] = (f"{n} message{'s' if n > 1 else ''} from the {names} PO not "
                           f"received: this PO is stopped and could not be started for "
                           f"{'them' if n > 1 else 'it'} ({why})")
    _HELD_CACHE = (mtime, time.time(), out)
    return out
