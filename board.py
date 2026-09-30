"""Declarative task gates and bounded idle-board alerts, on progress ticks.

Gate attempts are persisted before launching: a failed launch needs a human,
never a retry loop. Notices can retry delivery without retrying the launch.
Idle episodes survive hub restarts; starting any task clears the episode.
"""
import json
import threading
import time

import po_usage

_d = None
_LOCK = threading.RLock()


def bind(dashboard_module):
    global _d
    _d = dashboard_module


def validate(after, on_ready, project_id, room_id=""):
    """Resolve once to stable room ids; never follow a reused/moved number."""
    if not isinstance(after, list):
        raise ValueError("after must be a list of task references")
    if on_ready not in ("start", "tell"):
        raise ValueError("onReady must be start or tell")
    out = []
    for gate in after:
        if not isinstance(gate, dict) or not isinstance(gate.get("task"), str):
            raise ValueError("each after gate needs a task reference")
        when = gate.get("when", "merged")
        if when not in ("merged", "approved"):
            raise ValueError("a gate's when must be merged or approved")
        try:
            target = _d.ensemble_tools._load_target(gate["task"], {"projectId": project_id})
        except _d.ensemble_tools.ToolError as exc:
            raise ValueError(str(exc)) from exc
        if not project_id or _d.ensemble_tools._project_of_room(target) != project_id:
            raise ValueError("after must refer to an existing task in the same project")
        if target["id"] == room_id or target["id"] == next(
                (p.get("poRoomId") for p in _d.load_projects() if p["id"] == project_id), None):
            raise ValueError("after must refer to another task, not itself or the PO")
        out.append({"task": target["id"], "when": when})
    return out


def view(room):
    return {"after": [{"task": _d.task_label(_d.chatroom.get_room(g["task"]) or {})
                               or g["task"], "when": g.get("when", "merged")}
                      for g in room.get("after", [])],
            "onReady": room.get("onReady", "start")}


def reasons(room, project_id, git):
    gates = room.get("after") or []
    if not gates:
        return []
    out = []
    for gate in gates:
        target = _d.chatroom.get_room(gate["task"], public=False)
        if not target or _d.ensemble_tools._project_of_room(target) != project_id:
            return []
        when = gate.get("when", "merged")
        if when == "merged":
            if target["id"] not in git:
                git[target["id"]] = _d.digest._git_facts(target)
            satisfied = git[target["id"]].get("merged", False)
        else:
            reviews = [r for p in target.get("participants", [])
                       for r in [*(p.get("reviews") or []), p.get("review") or {}]
                       if r.get("endedAt") and r.get("verdict")]
            last = max(reviews, key=lambda r: r["endedAt"], default={})
            satisfied = last.get("verdict") == "approve"
        if not satisfied:
            return []
        out.append(f"{_d.task_label(target)} {when}")
    return out


def deliver(project, text):
    """A busy PO queues typed input. A stopped PO gets chat, never a resume."""
    rid = project.get("poRoomId")
    room = _d.chatroom.get_room(rid, public=False) if rid else None
    if not room:
        return False
    ident = _d.chatroom.po_identity(room)
    line = f"[board] {po_usage.head()} | {' '.join(text.split())}"
    with _d.rotation.GATE:
        part = _d.chatroom.participant(room, ident) if ident else None
        sess = _d.rotation._pty(part) if part else None
        if sess:
            if _d.rotation.is_rotating(rid, ident) or _d.rotation.awaiting_handover(rid, ident):
                return False
            return _d._type_input(sess, line)
        return bool(_d.chatroom.post_report(rid, "ensemble", ident, line,
                                           {"reportKind": "board"}, wake=False))


def _load():
    try:
        return json.loads((_d.DASHBOARD_DIR / "boards.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state):
    path = _d.DASHBOARD_DIR / "boards.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def check(project, now=None):
    now = time.time() if now is None else now
    with _LOCK:
        state = _load()
        rec = state.setdefault(project["id"], {})
        rooms = [r for r in _d.chatroom.list_rooms()
                 if r["id"] != project.get("poRoomId")
                 and _d.ensemble_tools._project_of_room(r) == project["id"]]
        git, ready = {}, []
        for room in rooms:
            action = room.get("gateAction") or {}
            if action.get("notice") and not action.get("told") and deliver(project, action["notice"]):
                action["told"] = now
                _d.chatroom.patch_room(room["id"], gateAction=action)
            if room.get("launched", True) or _d.workflow_of(room) == "done":
                continue
            why = reasons(room, project["id"], git)
            if why and not room.get("gateAction"):
                # Claim before launching, including failures and process interruption.
                action = {"at": now, "result": "attempted"}
                room["gateAction"] = action
                if _d.chatroom.patch_room(room["id"], gateAction=action) is None:
                    continue
                label = _d.task_label(room)
                if room.get("onReady", "start") == "tell":
                    text = f"{label} is unblocked: {', '.join(why)}."
                    action["result"] = "tell"
                else:
                    try:
                        _d.hub_launcher()._resume_room(room)
                        room["launched"] = True
                        text = f"{label} started: {', '.join(why)}."
                        action["result"] = "started"
                    except Exception as exc:
                        text = f"{label} could not start: {exc}. Start it manually after resolving this."
                        action["result"] = "failed"
                action["notice"] = text
                room["gateAction"] = action
                _d.chatroom.patch_room(room["id"], gateAction=action)
            action = room.get("gateAction") or {}
            if action.get("notice") and not action.get("told") and deliver(project, action["notice"]):
                action["told"] = now
                _d.chatroom.patch_room(room["id"], gateAction=action)
            if not room.get("launched", True) and (why or (
                    not room.get("after") and _d.priority_of(room) <= 2)):
                ready.append(room)
        # Reload after launches: normal allocation/start changes the live seats.
        rooms = [_d.chatroom.get_room(r["id"]) or r for r in rooms]
        active = any(r.get("launched", True) and _d.workflow_of(r) != "done"
                     and _d._room_is_live(r) for r in rooms)
        ep = rec.get("episode")
        if ep and any(r.get("launchedAt", 0) > ep["since"] for r in rooms):
            rec.pop("episode", None)
        if active or not ready or not project.get("poRoomId") or project.get("idleBoardAlert") is False:
            rec.pop("episode", None)
        else:
            ep = rec.get("episode")
            if ep is None:
                # Use the recorded board transition, not the time we noticed it.
                ended = [float((r.get("mergedAt") or r.get("workflowAt"))
                               if _d.workflow_of(r) == "done" and (r.get("mergedAt") or r.get("workflowAt"))
                               else r.get("updatedAt") or now)
                         for r in rooms if r.get("launched", True)]
                available = min(float((r.get("gateAction") or {}).get("at") or
                                      r.get("updatedAt") or r.get("createdAt") or now) for r in ready)
                since = min(now, max([available, *ended]))
                ep = rec["episode"] = {"since": since, "count": 0, "last": 0}
            elapsed = now - ep["since"]
            if (elapsed > 600 and ep["count"] < 3
                    and (not ep["count"] or now - ep["last"] >= 1800)):
                ready.sort(key=lambda r: (_d.priority_of(r), r.get("no", 0)))
                names = ", ".join(f"{_d.task_label(r)} ({_d.PRIORITY_NAMES[_d.priority_of(r)]}) "
                                  f"{r.get('title', '')}" for r in ready)
                line = (f"Board idle {int(elapsed // 60)} min: nothing in progress; ready: {names}. "
                        "Start them or say why not; work that needs code reading or edits belongs in a task.")
                if deliver(project, line):
                    ep.update(count=ep["count"] + 1, last=now)
        rec["lastCheck"] = now
        _save(state)


def maybe_tick():
    now = time.time()
    state = _load()
    for project in _d.load_projects():
        minutes = _d.digest.interval_min(project) or _d.digest.DEFAULT_INTERVAL_MIN
        if now - state.get(project["id"], {}).get("lastCheck", 0) >= minutes * 60:
            check(project, now)
