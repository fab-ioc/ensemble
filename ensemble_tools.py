"""Ensemble task-management MCP tools — how an agent running under Ensemble
reads and shapes the board it lives on: list projects and tasks, read a task's
spec and chat, create/amend/start/stop/move/delete tasks.

These tools are served from the same ``/mcp`` endpoint as the chat tools, so
every headless agent (solo or collaboration) gets them automatically; the
bearer token minted at room creation identifies the caller and its room, and
the room's project scopes what the caller may change.

Scope rules (deliberately conservative — a planner for project A must not be
able to reshape project B):

* **Read anywhere.** Listing and reading tasks in any project is allowed.
* **Write inside your own project.** Create/update/start/stop/delete/move are
  allowed only on tasks of the caller's project. A caller whose task has no
  project (Unassigned) may write anywhere — that's the "general planner" case.
* **Never touch yourself.** A task may not stop, delete or move itself.
* **Stop before delete.** A running task is never deleted underneath its agents.

This module holds the tool schemas and the dispatch; the storage/launch
primitives live in ``dashboard.py``, which registers itself via :func:`bind`
(the dashboard runs as ``__main__``, so a plain import would load a second copy).
"""
from __future__ import annotations

import json
import time

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_AGENT_SPEC = {
    "type": "object",
    "properties": {
        "agent": {"type": "string",
                  "description": "Agent kind: \"claude\" or \"codex\"."},
        "identity": {"type": "string",
                     "description": "Only when reassigning an existing task: the identity of an "
                                    "agent already on it (e.g. \"claude-2\") that is staying, so it "
                                    "keeps its transcript. Omit for a new agent."},
        "model": {"type": "string",
                  "description": "Optional model id for that agent (omit for the default)."},
        "role": {"type": "string",
                 "description": "Optional role: \"engineer\", \"reviewer\", \"planner\", "
                                "\"pair\", or free text used verbatim as the role's charter."},
    },
    "required": ["agent"],
}

TOOLS = [
    {
        "name": "ensemble_whoami",
        "description": (
            "Describe your own situation in Ensemble: your identity, agent kind and "
            "role, the task (room) you are running in, its project, working "
            "directory and task folder, and your teammates. Call this first when "
            "you need to plan or manage work — it tells you which project your "
            "writes are scoped to."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ensemble_list_projects",
        "description": (
            "List the projects registered in Ensemble (id, name, code path, home "
            "folder, task count). Use the id when creating or moving tasks."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ensemble_list_tasks",
        "description": (
            "List the tasks of a project — by default your own project. Each row "
            "carries id, title, status (draft | running | waiting_user | paused | "
            "stopped), agents, and a spec preview. Pass projectId to look at "
            "another project, or \"*\" for every task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "string",
                              "description": "Project id, \"*\" for all, omit for your own project."},
                "includeStopped": {"type": "boolean",
                                   "description": "Include stopped tasks (default true)."},
            },
        },
    },
    {
        "name": "ensemble_get_task",
        "description": (
            "Read one task in full: title, complete spec, project, status, agents "
            "and roles, workspace mode, working directory, task folder, and the "
            "most recent chat messages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": "The task (room) id, e.g. room-1a2b3c4d."},
                "messages": {"type": "integer",
                             "description": "How many recent chat messages to include (default 20, max 200)."},
            },
            "required": ["taskId"],
        },
    },
    {
        "name": "ensemble_create_task",
        "description": (
            "Create a new task in a project: a title, a full specification (this "
            "becomes the agent's first prompt, so write it as a complete brief in "
            "Markdown — goal, context, acceptance criteria, constraints), and the "
            "agents that will work it. By default the task is created as a DRAFT "
            "(not launched) so the product owner can review it in the dashboard; "
            "set start=true to launch it immediately. One agent = a solo task the "
            "human drives; two or more = an autonomous collaboration (give them "
            "roles, e.g. engineer + reviewer). Returns the new task id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title (≤120 chars)."},
                "spec": {"type": "string", "description": "The full task specification, Markdown."},
                "projectId": {"type": "string",
                              "description": "Project to create the task in (default: your own project)."},
                "agents": {"type": "array", "items": _AGENT_SPEC,
                           "description": "Agents for the task (default: one agent of your own kind)."},
                "workspace": {"type": "string",
                              "enum": ["empty", "inplace", "copy", "worktree"],
                              "description": "Workspace mode: empty (own task folder, no code — the default), "
                                             "inplace (work in the project's code folder), copy (a copy of "
                                             "the code folder), worktree (a git worktree on its own branch)."},
                "start": {"type": "boolean",
                          "description": "Launch the agents now (default false = leave as a draft)."},
            },
            "required": ["title", "spec"],
        },
    },
    {
        "name": "ensemble_update_task",
        "description": (
            "Amend a task's title, spec and/or assigned agents. Title and spec "
            "work on drafts and on stopped or running tasks (a running agent does "
            "not re-read the spec; tell it in chat if it must know). Reassigning "
            "agents — adding one, dropping one, changing a model or a role — is "
            "allowed only while the task is NOT running: stop it first, reassign, "
            "start it again. Agents that stay keep their identity and their "
            "conversation; pass their \"identity\" to be sure which is which. One "
            "agent left makes the task solo, two or more a collaboration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "taskId": {"type": "string"},
                "title": {"type": "string", "description": "New title (omit to keep)."},
                "spec": {"type": "string", "description": "New full spec (omit to keep). Replaces the old one."},
                "agents": {"type": "array", "items": _AGENT_SPEC,
                           "description": "The task's complete new agent line-up, replacing the old "
                                          "one (omit to keep it). List every agent that should be on "
                                          "the task, not just the change; anyone left out is removed."},
            },
            "required": ["taskId"],
        },
    },
    {
        "name": "ensemble_start_task",
        "description": (
            "Launch a draft task's agents, or relaunch a stopped task's agents "
            "resuming their previous conversation. Fails if the task is already running."
        ),
        "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}},
                        "required": ["taskId"]},
    },
    {
        "name": "ensemble_stop_task",
        "description": (
            "Stop a running task: ends its agents' processes but keeps the task, "
            "its spec and chat, so it can be started again later. You cannot stop "
            "your own task."
        ),
        "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}},
                        "required": ["taskId"]},
    },
    {
        "name": "ensemble_delete_task",
        "description": (
            "Delete a task permanently: removes the task record, its chat and its "
            "agents' transcripts. The task folder under the project and any real "
            "code folder are NOT deleted. Refused while the task is running (stop "
            "it first) and for your own task. Prefer stopping over deleting unless "
            "the product owner asked for deletion."
        ),
        "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}},
                        "required": ["taskId"]},
    },
    {
        "name": "ensemble_move_task",
        "description": (
            "Move a task to another project. The task's folder stays where it was "
            "created; only the project link changes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"taskId": {"type": "string"},
                           "projectId": {"type": "string", "description": "Destination project id."}},
            "required": ["taskId", "projectId"],
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ToolError(Exception):
    """A user-facing tool failure (bad args, out of scope, not found)."""


def _projects() -> dict:
    return {p["id"]: p for p in _d.load_projects()}


def _project_of_room(room: dict, links: dict | None = None) -> str:
    if links is None:
        links = _d.load_session_projects()
    pid = links.get(room["id"]) or room.get("projectId") or ""
    if not pid:
        pid = _d._project_for_cwd(room.get("cwd", ""), _d.load_projects())
    return pid or ""


def _project_view(p: dict | None) -> dict | None:
    if not p:
        return None
    return {"id": p["id"], "name": p.get("name", ""), "path": p.get("path", ""),
            "home": _d.project_home(p, create=False), "isGit": bool(p.get("isGit"))}


def _status(room: dict) -> str:
    if not room.get("launched", True):
        return "draft"
    if _d._room_is_live(room):
        st = room.get("status", "active")
        if st == "waiting_human":
            return "waiting_user"
        if st == "paused":
            return "paused"
        return "running"
    return "stopped"


def _agents_view(room: dict) -> list[dict]:
    return [{"identity": p.get("identity", ""), "agent": p.get("agent", ""),
             "model": p.get("model", ""), "role": p.get("role", "")}
            for p in room.get("participants", []) if p.get("kind") == "agent"]


def _title(room: dict, labels: dict | None = None) -> str:
    if labels is None:
        labels = _d.load_labels()
    return labels.get(room["id"]) or room.get("title", "")


def _row(room: dict, projects: dict, links: dict, labels: dict) -> dict:
    pid = _project_of_room(room, links)
    spec = room.get("spec", "") or ""
    return {
        "id": room["id"],
        "title": _title(room, labels),
        "status": _status(room),
        "mode": room.get("mode", ""),
        "projectId": pid,
        "project": (projects.get(pid) or {}).get("name", "") if pid else "",
        "agents": _agents_view(room),
        "specPreview": (spec[:160] + "…") if len(spec) > 160 else spec,
        "messages": len(room.get("messages", []) or []),
        "createdAt": room.get("createdAt"),
        "updatedAt": room.get("updatedAt"),
    }


def _caller(room_id: str, identity: str) -> dict:
    room = _d.chatroom.get_room(room_id, public=False)
    if room is None:
        raise ToolError("your room no longer exists")
    pid = _project_of_room(room)
    part = _d.chatroom.participant(room, identity) or {}
    return {"room": room, "identity": identity, "part": part, "projectId": pid}


def _load_target(task_id: str) -> dict:
    tid = (task_id or "").strip()
    if not tid:
        raise ToolError("taskId is required")
    room = _d.chatroom.get_room(tid, public=False)
    if room is None:
        raise ToolError(f"no such task: {tid}")
    return room


def _check_write_scope(ctx: dict, target_pid: str, what: str) -> None:
    """A caller with a project may only write inside it; a project-less caller
    may write anywhere."""
    own = ctx["projectId"]
    if own and target_pid != own:
        raise ToolError(f"{what} is out of scope: that task belongs to project "
                        f"'{target_pid or 'Unassigned'}', you are scoped to '{own}'")


def _not_self(ctx: dict, room: dict, what: str) -> None:
    if room["id"] == ctx["room"]["id"]:
        raise ToolError(f"you cannot {what} your own task")


def _text(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _whoami(ctx, args, handler):
    room = ctx["room"]
    projects = _projects()
    part = ctx["part"]
    mates = [a for a in _agents_view(room) if a["identity"] != ctx["identity"]]
    return {
        "identity": ctx["identity"],
        "agent": part.get("agent", ""),
        "model": part.get("model", ""),
        "role": part.get("role", ""),
        "taskId": room["id"],
        "title": _title(room),
        "mode": room.get("mode", ""),
        "status": _status(room),
        "cwd": part.get("cwd") or room.get("cwd", ""),
        "taskDir": room.get("taskDir", ""),
        "project": _project_view(projects.get(ctx["projectId"])),
        "writeScope": (ctx["projectId"] or "any project (your task has no project)"),
        "teammates": mates,
        "productOwner": _d.operator_name(),
    }


def _list_projects(ctx, args, handler):
    rooms = _d.chatroom.list_rooms()
    links = _d.load_session_projects()
    counts: dict[str, int] = {}
    for r in rooms:
        pid = _project_of_room(r, links)
        counts[pid] = counts.get(pid, 0) + 1
    out = []
    for p in _d.load_projects():
        v = _project_view(p)
        v["tasks"] = counts.get(p["id"], 0)
        v["isYours"] = p["id"] == ctx["projectId"]
        out.append(v)
    return {"projects": out, "unassignedTasks": counts.get("", 0)}


def _list_tasks(ctx, args, handler):
    pid = args.get("projectId")
    pid = ctx["projectId"] if pid is None or pid == "" else str(pid).strip()
    include_stopped = args.get("includeStopped", True) is not False
    projects = _projects()
    if pid not in ("*",) and pid and pid not in projects:
        raise ToolError(f"no such project: {pid}")
    links = _d.load_session_projects()
    labels = _d.load_labels()
    rows = []
    for r in _d.chatroom.list_rooms():
        rpid = _project_of_room(r, links)
        if pid != "*" and rpid != pid:
            continue
        row = _row(r, projects, links, labels)
        if not include_stopped and row["status"] == "stopped":
            continue
        rows.append(row)
    rows.sort(key=lambda x: x.get("updatedAt") or 0, reverse=True)
    scope = "all projects" if pid == "*" else (projects.get(pid, {}).get("name") or "Unassigned")
    return {"scope": scope, "projectId": ("" if pid == "*" else pid), "tasks": rows}


def _get_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    projects = _projects()
    links = _d.load_session_projects()
    labels = _d.load_labels()
    n = args.get("messages", 20)
    try:
        n = max(0, min(200, int(n)))
    except (TypeError, ValueError):
        n = 20
    msgs = room.get("messages", []) or []
    tail = [{"from": m.get("from"), "to": m.get("to", ""), "ts": m.get("ts"),
             "text": (m.get("text") or "")[:2000]} for m in msgs[-n:]] if n else []
    row = _row(room, projects, links, labels)
    row.pop("specPreview", None)
    row.update({
        "spec": room.get("spec", "") or "",
        "workspace": room.get("workspace", {}),
        "cwd": room.get("cwd", ""),
        "taskDir": room.get("taskDir", ""),
        "project": _project_view(projects.get(row["projectId"])),
        "recentMessages": tail,
        "isYou": room["id"] == ctx["room"]["id"],
    })
    return row


def _create_task(ctx, args, handler):
    title = (args.get("title") or "").strip()
    spec = (args.get("spec") or "").strip()
    if not title:
        raise ToolError("title is required")
    if not spec:
        raise ToolError("spec is required — write the full brief the agent will start from")
    pid = args.get("projectId")
    pid = ctx["projectId"] if pid is None or pid == "" else str(pid).strip()
    if pid and pid not in _projects():
        raise ToolError(f"no such project: {pid}")
    _check_write_scope(ctx, pid, "create_task")
    agent_list = args.get("agents") or []
    if not isinstance(agent_list, list):
        raise ToolError("agents must be a list")
    if not agent_list:
        agent_list = [{"agent": ctx["part"].get("agent") or "claude"}]
    workspace = (args.get("workspace") or "empty").strip()
    ok, room_full, err = _d.create_task(title, spec, pid, agent_list, workspace)
    if not ok:
        raise ToolError(err)
    started = False
    if args.get("start") is True:
        handler._start_room(room_full)
        started = True
    return {"ok": True, "taskId": room_full["id"], "title": room_full["title"],
            "status": "running" if started else "draft",
            "projectId": pid, "taskDir": room_full.get("taskDir", ""),
            "cwd": room_full.get("cwd", ""),
            "note": ("launched" if started else
                     "created as a draft — the product owner can start it from the "
                     "dashboard, or call ensemble_start_task")}


def _update_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    _check_write_scope(ctx, _project_of_room(room), "update_task")
    title = args.get("title")
    spec = args.get("spec")
    agent_list = args.get("agents")
    if title is None and spec is None and agent_list is None:
        raise ToolError("give a new title, spec and/or agents")
    notes = []
    room2 = room
    if title is not None or spec is not None:
        ok, room2, err = _d.update_task(room["id"], title=title, spec=spec)
        if not ok:
            raise ToolError(err)
        if _status(room2) in ("running", "waiting_user", "paused") and spec is not None:
            notes.append("the task is running — its agents will not re-read the spec")
    if agent_list is not None:
        # "That task is running" would be true of your own task too, but it is
        # not the useful answer.
        _not_self(ctx, room, "reassign the agents on")
        if not isinstance(agent_list, list) or not agent_list:
            raise ToolError("agents must be a non-empty list — a task needs at least one agent")
        ok, room3, err = _d.reassign_task(room["id"], agent_list)
        if not ok:
            raise ToolError(_reassign_error(err))
        room2 = room3
        notes.append("agents reassigned: " +
                     ", ".join(f"{a['identity']} ({a['agent']}"
                               + (f", {a['role']}" if a["role"] else "") + ")"
                               for a in _agents_view(room2))
                     + f" — the task is now {room2.get('mode', '')}")
    return {"ok": True, "taskId": room2["id"], "title": _title(room2),
            "status": _status(room2), "agents": _agents_view(room2),
            "mode": room2.get("mode", ""), "note": "; ".join(notes)}


def _reassign_error(err: str) -> str:
    """Turn reassign_task's error code into something an agent can act on."""
    if err == "task_is_running":
        return ("that task is running — agents cannot be reassigned under a live "
                "task; stop it (ensemble_stop_task), reassign, then start it again")
    if err == "need_an_agent":
        return "a task needs at least one agent"
    if err.startswith("agent_unavailable:"):
        key = err.split(":", 1)[1]
        return (f"no such agent, or it is not installed on this machine: "
                f"'{key}' — use one of the agent kinds already on the board "
                f"(e.g. \"claude\", \"codex\")")
    return err


def _start_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    _check_write_scope(ctx, _project_of_room(room), "start_task")
    _not_self(ctx, room, "start")
    if _d._room_is_live(room):
        raise ToolError("that task is already running")
    launched = handler._start_or_resume_room(room)
    return {"ok": True, "taskId": room["id"], "status": "running",
            "agents": [x["identity"] for x in launched]}


def _stop_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    _check_write_scope(ctx, _project_of_room(room), "stop_task")
    _not_self(ctx, room, "stop")
    if not _d._room_is_live(room):
        return {"ok": True, "taskId": room["id"], "status": _status(room),
                "note": "it was not running"}
    _d.stop_task(room["id"])
    return {"ok": True, "taskId": room["id"], "status": "stopped"}


def _delete_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    _check_write_scope(ctx, _project_of_room(room), "delete_task")
    _not_self(ctx, room, "delete")
    if _d._room_is_live(room):
        raise ToolError("that task is running — stop it first (ensemble_stop_task)")
    res = _d.delete_task(room["id"])
    return {"ok": True, "taskId": room["id"], "deleted": True,
            "transcriptsRemoved": len(res.get("transcripts", [])),
            "taskDirKept": room.get("taskDir", "")}


def _move_task(ctx, args, handler):
    room = _load_target(args.get("taskId"))
    src = _project_of_room(room)
    dest = (args.get("projectId") or "").strip()
    if not dest:
        raise ToolError("projectId is required")
    projects = _projects()
    if dest not in projects:
        raise ToolError(f"no such project: {dest}")
    _check_write_scope(ctx, src, "move_task")
    _not_self(ctx, room, "move")
    if src == dest:
        return {"ok": True, "taskId": room["id"], "projectId": dest, "note": "already there"}
    _d.move_task(room["id"], dest)
    return {"ok": True, "taskId": room["id"], "projectId": dest,
            "project": projects[dest].get("name", "")}


_IMPL = {
    "ensemble_whoami": _whoami,
    "ensemble_list_projects": _list_projects,
    "ensemble_list_tasks": _list_tasks,
    "ensemble_get_task": _get_task,
    "ensemble_create_task": _create_task,
    "ensemble_update_task": _update_task,
    "ensemble_start_task": _start_task,
    "ensemble_stop_task": _stop_task,
    "ensemble_delete_task": _delete_task,
    "ensemble_move_task": _move_task,
}

NAMES = frozenset(_IMPL)


def call(name: str, args: dict, room_id: str, identity: str, handler) -> tuple[str, bool]:
    """Run one ensemble_* tool. Returns ``(text, is_error)`` ready to wrap in an
    MCP tool result. ``handler`` is the serving HTTP handler (it owns the launch
    helpers, which need the server's port for the MCP URL)."""
    fn = _IMPL.get(name)
    if fn is None:
        return f"unknown tool: {name}", True
    try:
        ctx = _caller(room_id, identity)
        result = fn(ctx, args or {}, handler)
        return _text(result), False
    except ToolError as e:
        return f"error: {e}", True
    except Exception as e:  # never let a tool bug kill the agent's turn
        return f"error: {type(e).__name__}: {e}", True
