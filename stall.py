"""A task that stopped without saying so: its owner is idle, nothing is open.

Task → PO → CEO (ED-138). A task in a project with a PO reports to the PO; an
ask of its is the PO's to answer, and never the CEO's (attention.py). What is
left is the task that simply stopped: its owner idle, no ask open (to the PO
or anyone), no review running, nothing handed to a teammate — OP-140
(2026-09-30) said "starting phase (b)" and then sat idle for hours.

Such a task is **stalled**. The hub

1. types its owner one line, ``[stalled] Carry on …``, once the owner has been
   idle ``GRACE_S``;
2. if the owner is still idle ``GRACE_S`` after that with nothing open, tells
   the PO once: a line in the PO's room, and one line typed into the PO —
   at once if it is idle, else on a later look once it is (``wokeAt``), for
   as long as the owner stays stalled.

Once per **episode**: an episode is what happened since the last thing that
could have set the owner going — a teammate's or a person's message, a report
of its own, a spec amendment, an answer typed into it, a rotation. Anything
new starts a new episode; nothing new, nothing is said again. The records are
kept in ``DASHBOARD_DIR/stalls.json``, so a hub restart does not repeat them.

Tasks with no PO are the attention detector's (the CEO's bell), as before.

Two more ways a task stops without saying so (ED-159):

* **A model at its limit.** An agent's turn ended on the CLI's own line
  "You've reached your Fable limit" (``model_limit``). The limit is
  remembered so no seat is given that model, a reviewer that hit it in the
  middle of a review has its review ended as failed, and the PO is told once
  per limit (a line in its room, and typed when it is idle). Attention shows
  the task blocked.
* **A silent owner.** A running task whose owner has produced nothing for
  ``SILENT_S``: no transcript growth, no hook event, no screen change, no
  commit (:func:`last_sign`), and nothing excuses it (:func:`silence_excuse`).
  Attention shows it stalled; the PO is told once per silent episode, unless
  the idle nudge above already told it.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


TICK_S = 60                 # how often the tasks are looked at
GRACE_S = 600               # idle this long before a nudge, and again before the PO hears
SENDER = "ensemble"         # who the line in the PO's room is from
PREFIX = "[stalled] "       # how the typed line starts (HUB_INPUT_KINDS)
NUDGE = (PREFIX + "You are idle and nothing of yours is open. Carry on from "
         "TASK-HANDOVER.md; if you are blocked or need a decision, report it with "
         "ensemble_report; if you are finished, report completed.")
_OWN_SUBMIT_S = 60          # a submit this close after the nudge is the nudge itself
_QUIET = ("inreview", "done")

_LOCK = threading.Lock()
_LAST = 0.0

# An owner that has produced nothing for this long is silent (ED-159 point 4).
SILENT_S = 30 * 60


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] stall: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _state_file() -> Path:
    return _d.DASHBOARD_DIR / "stalls.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save: {e}")


# ---------------------------------------------------------------------------
# Is it stalled
# ---------------------------------------------------------------------------

def episode(room: dict, owner: str, last_submit: float, nudged_at: float) -> float:
    """When the owner was last given something to go on: the newest message in
    the room not its own, its newest report, a spec amendment, an answer
    typed into it, its rotation, a line submitted to its terminal (not the
    nudge)."""
    ts = [0.0]
    for m in room.get("messages") or []:
        if m.get("from") != owner:
            ts.append(float(m.get("ts") or 0))
    ts.append(float((room.get("lastReport") or {}).get("ts") or 0))
    ts.append(float(room.get("specAt") or 0))
    part = _d.chatroom.participant(room, owner) or {}
    for k in ("answeredAt", "rotatedAt", "resumedAt"):
        try:
            ts.append(float(part.get(k) or 0))
        except (TypeError, ValueError):
            pass
    if last_submit and not (nudged_at and 0 <= last_submit - nudged_at < _OWN_SUBMIT_S):
        ts.append(float(last_submit))
    return max(ts)


def why_not(room: dict, owner: str) -> str:
    """"" when the room, by what it holds, could be stalled; else why not.
    The terminals are looked at by the caller."""
    if not room.get("launched", True):
        return "a draft"
    if room.get("status") == "paused":
        return "paused"
    if _d.workflow_of(room) in _QUIET:
        return "in review or done"
    if not _d.room_po_id(room):
        return "no PO"                  # the CEO's bell (attention.py) has it
    if _d.attention.open_ask(room) is not None:
        return "something of it is open"
    msgs = room.get("messages") or []
    last = msgs[-1] if msgs else {}
    if last.get("from") == owner and last.get("rang"):
        return "handed to a teammate"
    return ""


def _busy(part: dict) -> bool:
    rot = _d.rotation
    if rot._pty(part) is None:
        return False
    tpath, reader = rot._transcript_of(part)
    return not rot._idle(part, reader(tpath))


def _look(rid: str, state: dict, now: float) -> str:
    """One room: "" when nothing was done, else what was."""
    rot, cr = _d.rotation, _d.chatroom
    with rot.GATE:
        room = cr.get_room(rid, public=False)
        if not room or not _d._room_is_live(room):
            return ""
        owners = cr.owners(room)
        owner = owners[0] if owners else ""
        part = cr.participant(room, owner) if owner else None
        if not part or why_not(room, owner):
            return ""
        if rot.is_rotating(rid, owner) or rot.awaiting_handover(rid, owner):
            return ""
        if limit_hit(part):
            return ""                   # at a model limit: _look_limit tells the PO
        sess = rot._pty(part)
        if sess is None:
            return ""
        # A teammate still at work (a review running) is not a stall.
        if any(_busy(p) for p in cr.agent_participants(room) if p.get("identity") != owner):
            return ""
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return ""
        try:
            idle = float(sess.info().get("idleSeconds") or 0)
            last_submit = float(sess.last_submit() or 0)
        except Exception:
            return ""
        if idle < GRACE_S:
            return ""
        rec = state.get(rid) or {}
        ep = episode(room, owner, last_submit, float(rec.get("nudgedAt") or 0))
        if rec.get("episode") != ep:
            # The silent look's record, and when the PO last heard of it,
            # outlive an episode: they are about a different clock.
            rec = {"episode": ep, **{k: rec[k] for k in ("silent", "lastToldAt") if rec.get(k)}}
        rec["current"] = True           # stopped right now (record())
        state[rid] = rec
        if not rec.get("nudgedAt"):
            if not _d._type_input(sess, NUDGE):
                return ""
            rec.update(nudgedAt=now, owner=owner, idleSince=now - idle)
            state[rid] = rec
            return "nudged"
        if now - float(rec["nudgedAt"]) < GRACE_S or rec.get("wokeAt"):
            return ""
        if not rec.get("toldAt"):
            # The line in the PO's room, once; the wake below until it lands.
            if not _post_po(room, rec, now):
                return ""
            rec["toldAt"] = rec["lastToldAt"] = now
            state[rid] = rec
            did = "PO told"
        else:
            did = ""
        # A busy, rotating or stopped PO is typed the line when it is next
        # idle (review 2): the record keeps it pending, across a restart too.
        if _wake_po(room, rec, now):
            rec["wokeAt"] = now
            state[rid] = rec
            return (did + ", woken") if did else "PO woken"
        return (did + " (in its chat; typed when it is idle)") if did else ""


def _words(room: dict, rec: dict, now: float) -> tuple[str, str, str]:
    """(label, idle since, nudged at) as the PO is told them."""
    since = time.strftime("%H:%M", time.localtime(float(rec.get("idleSince") or now)))
    nudged = time.strftime("%H:%M", time.localtime(float(rec.get("nudgedAt") or now)))
    return _d.task_label(room) or room.get("id", ""), since, nudged


def _post_po(room: dict, rec: dict, now: float) -> bool:
    """The line in the PO's room. False when there is no PO room."""
    cr = _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    if not po_room:
        return False
    ident = cr.po_identity(po_room)
    label, since, nudged = _words(room, rec, now)
    title = room.get("title", "")
    body = (f"**{label} stalled since {since}** — {title}\n\n"
            f"Its owner is idle with nothing open: no question, block or report "
            f"waiting, no review running. The hub nudged it at {nudged} and it did "
            f"not carry on. Look at it (`ensemble_get_task {label}`) and steer it, "
            f"or stop it.")
    return bool(cr.post_report(po, SENDER, ident, body,
                               {"reportKind": "digest", "stalledTask": room.get("id", "")}, wake=False))


def _wake_po(room: dict, rec: dict, now: float) -> bool:
    """Type the PO one line about it, if the PO is running and idle."""
    rot, cr = _d.rotation, _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    if not po_room:
        return False
    ident = cr.po_identity(po_room)
    label, since, nudged = _words(room, rec, now)
    with rot.GATE:
        part = cr.participant(po_room, ident) if ident else None
        sess = rot._pty(part) if part else None
        if sess is None or rot.is_rotating(po, ident) or rot.awaiting_handover(po, ident):
            return False
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return False
        proj = next((p.get("name", "") for p in _d.load_projects()
                     if (p.get("poRoomId") or "") == po), "")
        wake = (f"[digest] {proj or 'Your project'}: {label} stalled since {since} — its "
                f"owner is idle with nothing open and did not carry on after the hub's "
                f"nudge at {nudged}. Details with ensemble_get_task {label}.")
        return _d._type_input(sess, wake)


# ---------------------------------------------------------------------------
# A silent owner (ED-159)
# ---------------------------------------------------------------------------

_COMMIT_CACHE: dict[str, tuple[float, float]] = {}   # cwd -> (read at, commit time)


def commit_at(cwd: str, now: float | None = None) -> float:
    """When the checkout at ``cwd`` last moved its HEAD (a commit, a merge, a
    checkout): the mtime of its ``logs/HEAD``, read through a worktree's
    ``.git`` file. 0 when there is none. Cached for a minute."""
    if not cwd:
        return 0.0
    now = time.time() if now is None else now
    hit = _COMMIT_CACHE.get(cwd)
    if hit and now - hit[0] < 60:
        return hit[1]
    at = 0.0
    try:
        git = Path(cwd) / ".git"
        if git.is_file():
            line = git.read_text(encoding="utf-8").strip()
            if line.startswith("gitdir:"):
                git = Path(line[7:].strip())
                if not git.is_absolute():
                    git = Path(cwd) / git
        at = (git / "logs" / "HEAD").stat().st_mtime
    except (OSError, ValueError):
        at = 0.0
    _COMMIT_CACHE[cwd] = (now, at)
    return at


def _file_mtime(path) -> float:
    try:
        return Path(path).stat().st_mtime if path else 0.0
    except OSError:
        return 0.0


def last_sign(room: dict, part: dict, ev: dict) -> float:
    """The last time the owner did anything, or was given something to go on:
    its transcript growing, a hook event, its screen printing, a commit in its
    checkout, a resume, rotation, answer or spec amendment. 0 when nothing is
    known. A line the hub typed into it is not its own doing."""
    ts = [0.0]
    try:
        ts.append(_file_mtime(_d.rotation._transcript_of(part)[0]))
    except Exception:
        pass
    ts.append(float((ev.get("hook") or {}).get("lastEventAt") or 0))
    ts.append(float(ev.get("lastOutput") or 0))
    ts.append(commit_at(room.get("cwd", "")))
    for k in ("resumedAt", "rotatedAt", "answeredAt"):
        try:
            ts.append(float(part.get(k) or 0))
        except (TypeError, ValueError):
            pass
    last = _last_message(room)
    try:
        ts.append(float(room.get("specAt") or 0))
        ts.append(float(last.get("ts") or 0))     # its own, or one to it
    except (TypeError, ValueError):
        pass
    return max(ts)


def _last_message(room: dict) -> dict:
    if "lastMessage" in room:
        return room.get("lastMessage") or {}
    msgs = room.get("messages") or []
    return msgs[-1] if msgs else {}


def _open_ask(room: dict):
    if "openToHuman" in room:
        return room.get("openToHuman")
    return _d.attention.open_ask(room)


def silence_excuse(room: dict, part: dict, ev: dict) -> str:
    """"" when a quiet owner may be silent; else what excuses the quiet: a
    background run of its own, a reviewer at work, an ask waiting on the PO or
    the CEO (a completed report too), a hand-off to a teammate, a task in
    review, done, paused or stopped. ``room`` is a room record or attention's
    summary of one."""
    if not room.get("launched", True):
        return "a draft"
    if room.get("status") in ("paused", "waiting_human"):
        return "paused" if room.get("status") == "paused" else "waiting on the CEO"
    if (room.get("workflow") or "") in _QUIET:
        return "in review or done"
    ident = part.get("identity", "")
    if ident not in (room.get("owners") or []):
        return "not the owner"
    if not ev.get("alive"):
        return "not running"
    if ev.get("claudeStatus") == "shell":
        return "a background run"
    if _open_ask(room):
        return "an ask is open"
    last = _last_message(room)
    if last.get("from") == ident and last.get("rang"):
        return "handed to a teammate"
    for p in room.get("participants") or []:
        if p.get("identity") == ident or p.get("kind") != "agent":
            continue
        rev = p.get("review") or {}
        if rev.get("startedAt") and not rev.get("endedAt"):
            return "a review is running"
    return ""


def silent_for(room: dict, part: dict, ev: dict, now: float) -> float:
    """How long the owner has been silent; 0 when it is not (excused, or
    nothing is known)."""
    if silence_excuse(room, part, ev):
        return 0.0
    sign = last_sign(room, part, ev)
    return max(0.0, now - sign) if sign else 0.0


def _look_silent(rid: str, state: dict, now: float, statuses: dict) -> str:
    """Tell the PO once per silent episode (the attention item shows it).
    Under the gate, as ``_look``: a seat being handed over is left alone."""
    with _d.rotation.GATE:
        return _look_silent_gated(rid, state, now, statuses)


def silent_owner_owes(room: dict, identity: str) -> bool:
    """Whether a silent owner counts at all: it owes something
    (``attention._owed_since``). A solo task nobody has written to, or an
    owner that spoke last, does not. The one rule for the attention item and
    the PO's line. ``room`` is a room record or attention's summary of one."""
    view = room if "lastMessage" in room else {**room, "lastMessage": _last_message(room)}
    return bool(_d.attention._owed_since(view, identity)[0])


def _look_silent_gated(rid: str, state: dict, now: float, statuses: dict) -> str:
    cr, rot = _d.chatroom, _d.rotation
    room = cr.get_room(rid, public=False)
    if not room or not _d._room_is_live(room) or not _d.room_po_id(room):
        return ""
    owners = cr.owners(room)
    part = cr.participant(room, owners[0]) if owners else None
    if not part:
        return ""
    if rot.is_rotating(rid, owners[0]) or rot.awaiting_handover(rid, owners[0]):
        return ""
    if limit_hit(part):
        return ""                       # at a model limit: _look_limit tells the PO
    view = {**room, "owners": owners, "workflow": _d.workflow_of(room)}
    quiet = silent_for(view, part, _d.attention._evidence(part, statuses), now) \
        if silent_owner_owes(view, owners[0]) else 0.0
    rec = state.get(rid)
    if quiet < SILENT_S:
        if rec and (rec.get("silent") or {}).get("current"):
            rec["silent"]["current"] = False
        return ""
    rec = rec or {}
    since = round(now - quiet, 3)
    srec = rec.get("silent") or {}
    if srec.get("since") != since:
        srec = {"since": since}
    srec["current"] = True
    rec["silent"] = srec
    state[rid] = rec
    if srec.get("wokeAt"):
        return ""
    if max(float(rec.get("toldAt") or 0), float(rec.get("lastToldAt") or 0)) >= since:
        return ""                       # the idle nudge already told the PO
    label = _d.task_label(room) or rid
    at = time.strftime("%H:%M", time.localtime(since))
    did = ""
    if not srec.get("toldAt"):
        po = _d.room_po_id(room)
        po_room = cr.get_room(po, public=False) if po else None
        if not po_room:
            return ""
        body = (f"**{label} silent since {at}** — {room.get('title', '')}\n\n"
                f"Its owner has produced nothing for {int(quiet // 60)} min: no transcript "
                f"growth, no hook event, no screen change, no commit, and nothing excuses "
                f"it (no background run, review or open ask). Look at it "
                f"(`ensemble_get_task {label}`) and steer it, or stop it.")
        if not cr.post_report(po, SENDER, cr.po_identity(po_room), body,
                              {"reportKind": "digest", "silentTask": rid}, wake=False):
            return ""
        srec["toldAt"] = now
        did = "PO told (silent)"
    wake = (f"[digest] {label} silent since {at}: its owner has produced nothing for "
            f"{int(quiet // 60)} min with nothing open. Details with ensemble_get_task {label}.")
    if _type_po(room, wake):
        srec["wokeAt"] = now
        return (did + ", woken") if did else "PO woken (silent)"
    return did


# ---------------------------------------------------------------------------
# A model at its limit (ED-159)
# ---------------------------------------------------------------------------

def _type_po(room: dict, line: str) -> bool:
    """Type the task's PO one line, if the PO is running and idle."""
    rot, cr = _d.rotation, _d.chatroom
    po = _d.room_po_id(room)
    po_room = cr.get_room(po, public=False) if po else None
    if not po_room:
        return False
    ident = cr.po_identity(po_room)
    with rot.GATE:
        part = cr.participant(po_room, ident) if ident else None
        sess = rot._pty(part) if part else None
        if sess is None or rot.is_rotating(po, ident) or rot.awaiting_handover(po, ident):
            return False
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return False
        return _d._type_input(sess, line)


def limit_hit(part: dict) -> dict | None:
    """The model limit an agent's conversation ended on, or None. Claude only."""
    if (part.get("agent") or "claude") != "claude":
        return None
    try:
        return _d.model_limit.from_transcript(_d.rotation._transcript_of(part)[0])
    except Exception:
        return None


def _seat_model(part: dict) -> str:
    """The model an agent ran on, for a line that names none."""
    try:
        return _d.model_limit.last_model(_d.rotation._transcript_of(part)[0]) or \
            (part.get("model") or "")
    except Exception:
        return part.get("model") or ""


def _look_limit(rid: str, now: float) -> str:
    """Each agent of a task whose turn ended on a model limit: the limit is
    remembered, a review it was doing ends as failed, and the PO hears of it
    once per limit (with no PO, the attention item is the CEO's bell)."""
    cr, ml = _d.chatroom, _d.model_limit
    room = cr.get_room(rid, public=False)
    if not room or not room.get("launched", True) or not _d._room_is_live(room):
        return ""
    done = []
    for part in cr.agent_participants(room):
        ident = part.get("identity", "")
        hit = limit_hit(part)
        if not hit:
            continue
        key = f"{rid}/{ident}"
        rec = ml.note(hit, room=rid, identity=ident, fallback_model=_seat_model(part)) or {}
        try:
            _d.learn_cli_default(part, _d.rotation._transcript_of(part)[0], hit)
        except Exception:
            pass
        old = ml.reported(key)
        if old.get("at") == hit["at"] and old.get("wokeAt"):
            continue
        if old.get("at") != hit["at"]:
            old = {"at": hit["at"]}
        model = rec.get("model") or hit.get("model") or ""
        words = _d.attention.model_limit_words(model, float(rec.get("until") or 0))
        rev = part.get("review") or {}
        failed = ""
        if rev.get("startedAt") and not rev.get("endedAt") and not old.get("reviewFailed"):
            try:
                _d.finish_review(rid, ident, "failed")
                old["reviewFailed"] = now
                failed = f" Its review {rev.get('n') or ''} ended as failed.".replace("  ", " ")
            except Exception as e:          # noqa: BLE001
                _log(f"{rid}: cannot end the review of {ident}: {str(e)[:120]}")
        label = _d.task_label(room) or rid
        po = _d.room_po_id(room)
        until = float(rec.get("until") or hit.get("resetAt") or 0) or \
            float(hit["at"]) + ml.CLEAR_AFTER_S
        if _d.rotation._pty(part) is None or until <= now:
            # A stale hit: the agent is gone or the limit has cleared. Only a
            # review it ended is news; nobody is woken for it.
            if failed and po and not old.get("toldAt"):
                po_room = cr.get_room(po, public=False)
                at = time.strftime("%H:%M", time.localtime(float(hit["at"])))
                body = (f"**{label} {ident} was stopped by a model limit "
                        f"({_d.model_limit.title(model) or 'model'}) at {at}** — "
                        f"{room.get('title', '')}\n\n{failed.strip()}")
                if po_room and cr.post_report(po, SENDER, cr.po_identity(po_room), body,
                                              {"reportKind": "digest", "limitTask": rid}, wake=False):
                    old["toldAt"] = now
            ml.set_reported(key, {**old, "wokeAt": old.get("wokeAt") or now, "stale": True})
            if failed:
                done.append(f"{ident} was stopped by a model limit "
                            f"({_d.model_limit.title(model) or 'model'}).{failed}")
            continue
        if not po:
            ml.set_reported(key, {**old, "wokeAt": now})    # the CEO's bell shows it
            done.append(f"{ident} {words}{failed}")
            continue
        if not old.get("toldAt"):
            po_room = cr.get_room(po, public=False)
            if not po_room:
                continue
            at = time.strftime("%H:%M", time.localtime(float(hit["at"])))
            body = (f"**{label} {ident} is {words}** — {room.get('title', '')}\n\n"
                    f"Its turn ended at {at} on the CLI's own line: “{hit.get('line', '')}”."
                    f"{failed} The hub seats no Claude agent on this model until the limit "
                    f"clears; a seat that names it keeps it, with a warning. Switch the seat's "
                    f"model and resume it, or wait for the reset.")
            if not cr.post_report(po, SENDER, cr.po_identity(po_room), body,
                                  {"reportKind": "digest", "limitTask": rid}, wake=False):
                continue
            old["toldAt"] = now
            news = True
        else:
            news = bool(failed)
        wake = (f"[digest] {label} {ident} is {words}: its turn ended on "
                f"“{hit.get('line', '')[:120]}”. Details with ensemble_get_task {label}.")
        if _type_po(room, wake):
            old["wokeAt"] = now
            news = True
        ml.set_reported(key, old)
        if news:
            done.append(f"{ident} {words}{failed}"
                        + (", PO woken" if old.get("wokeAt") else " (PO told; typed when idle)"))
    return "; ".join(done)


# ---------------------------------------------------------------------------
# Looking
# ---------------------------------------------------------------------------

def tick(now: float | None = None) -> dict[str, str]:
    """Look at every task once. Returns {roomId: what was done}."""
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        before = json.dumps(state, sort_keys=True)
        out = {}
        try:
            ids = [p.stem for p in _d.chatroom.ROOMS_DIR.glob("room-*.json")]
        except OSError:
            ids = []
        try:
            statuses = _d.attention._claude_status_by_session()
        except Exception:
            statuses = {}
        for rid in ids:
            if rid in state:
                state[rid]["current"] = False   # until this look finds it stopped again
            dids = []
            for look in (lambda: _look(rid, state, now), lambda: _look_limit(rid, now),
                         lambda: _look_silent(rid, state, now, statuses)):
                try:
                    did = look()
                except Exception as e:      # one task must not stop the others
                    _log(f"{rid}: {str(e)[:200]}")
                    continue
                if did:
                    dids.append(did)
            if dids:
                out[rid] = "; ".join(dids)
                _log(f"{rid}: {out[rid]}")
        for rid in [r for r in state if r not in ids]:
            state.pop(rid, None)
        if json.dumps(state, sort_keys=True) != before:
            _save(state)
        return out


def records() -> dict[str, dict]:
    """Per task the hub has nudged and that is still stopped: ``{episode,
    nudgedAt, toldAt, idleSince}``. One read of the file for a whole list."""
    return {rid: rec for rid, rec in _load().items()
            if rec.get("current") and rec.get("nudgedAt")}


def record(room_id: str) -> dict | None:
    """``records()`` for one task, or None."""
    return records().get(room_id)


def maybe_tick() -> None:
    """Called from the progress check's scheduler loop: once per ``TICK_S``,
    the first time a minute after the hub started."""
    global _LAST
    now = time.time()
    if not _LAST:
        _LAST = now
        return
    if now - _LAST >= TICK_S:
        _LAST = now
        tick(now)
