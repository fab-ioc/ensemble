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

from chatroom import REPORT_KINDS   # a plain module, safe to import here

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

# The board's columns. Defined here, not read from the dashboard: this module
# is imported before the dashboard binds itself, so any `_d.` lookup at import
# time crashes the hub on start. The dashboard takes its copy from here.
WORKFLOW_NAMES = ("backlog", "todo", "inprogress", "inreview", "done")

_PRIORITY_DOC = ("Priority: \"highest\", \"high\", \"medium\", \"low\" or \"lowest\" "
                 "(a number 1-5 also works, 1 = highest).")


def _priority_spec(tail: str) -> dict:
    # A plain string type — every other schema here is scalar, and the server
    # takes "2" and 2 alike, so nothing is lost by not declaring a union.
    return {"type": "string", "description": f"{_PRIORITY_DOC} {tail}"}

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
            "directory and task folder, your teammates, and your project's PO "
            "(`projectPO` — the task your reports go to). Call this first when "
            "you need to plan or manage work — it tells you which project your "
            "writes are scoped to."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ensemble_report",
        "description": (
            "Report on your task to the people responsible for it. Call it when "
            "you have FINISHED (kind \"completed\"), when you are BLOCKED and need "
            "help (\"blocked\"), when you need a decision (\"question\"), or for a "
            "milestone worth knowing (\"update\"). The report goes to your "
            "project's PO — it is posted in the PO's room and wakes the PO — or, "
            "if the project has no PO, to the user. It is also recorded on your "
            "task, so the board shows it: after completed / question / blocked "
            "your task reads as waiting on a human, not as stalled. Write the "
            "text as a self-contained Markdown summary — what was done or what is "
            "needed, where (branch, commit, paths), how you verified it — because "
            "the PO does not see your conversation. One report per event; do not "
            "repeat it to be sure it arrived."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(REPORT_KINDS),
                         "description": "completed | blocked | question | update."},
                "text": {"type": "string", "description": "The report, Markdown."},
            },
            "required": ["kind", "text"],
        },
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
            "carries id, title, priority, status (draft | running | waiting_user | "
            "paused | stopped), agents, and a spec preview. Rows come back "
            "highest-priority first, most-recently-updated first within a "
            "priority. A row whose task needs a human also carries `attention` — "
            "{state, reason, agent} — with state one of agent_gone | blocked | "
            "waiting_for_you | stalled. Pass projectId to look at another project, "
            "or \"*\" for every task."
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
        "name": "ensemble_list_attention",
        "description": (
            "List the tasks that need a human right now, and why — the same "
            "answer the dashboard's notifications show. Use it to find out what "
            "happened to work you started: an agent that died (agent_gone, with "
            "its exit status and the last lines it printed), one that cannot "
            "continue (blocked — a usage or credit limit, an expired login; the "
            "offending line is quoted), one waiting on a human answer "
            "(waiting_for_you), or one that was asked to do something, isn't "
            "working, and never reported back (stalled). Defaults to your own "
            "project; pass projectId for another, or \"*\" for every project. "
            "Nothing here is acted on automatically — deciding what to do is "
            "yours or the product owner's."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "string",
                              "description": "Project id, \"*\" for all, omit for your own project."},
                "state": {"type": "string",
                          "description": "Only this state: agent_gone | blocked | "
                                         "waiting_for_you | stalled."},
            },
        },
    },
    {
        "name": "ensemble_plan_usage",
        "description": (
            "How much of each agent kind's plan allowance is spent, and when the "
            "windows reset — the same reading the board header shows. Use it "
            "before starting more work, to see whether there is room. The numbers "
            "are **account-wide**: every task on this machine shares them, so they "
            "say nothing about what one task cost (that is the per-task cost "
            "chip). Claude and Codex have separate allowances — read them "
            "separately and never add them up. **Judge each window on its own "
            "`windows[].trusted`**, not on the source-level flag, which is only "
            "the conjunction: Codex writes its usage to a file only when one of "
            "its agents takes a turn, so the same reading can be out of date for "
            "the five-hour window and still exact for the weekly one. A window "
            "with `trusted: false` is frozen — `ageSeconds` says how long ago it "
            "was written, and because a window only fills up until it resets, its "
            "`percent` is a floor (\"at least this much\"), not a measurement. A "
            "window with `percent: null` has no current "
            "value at all: either `rolledOver` (it has already reset) or "
            "`resetUnknown` (no reset time to check against) — treat both as "
            "unknown and never fall back to `stalePercent`. Nothing here is "
            "enforced — deciding what to do is yours or the product owner's."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ensemble_get_task",
        "description": (
            "Read one task in full: title, complete spec, priority, project, "
            "status, agents and roles, workspace mode, working directory, task "
            "folder, and the most recent chat messages."
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
                "priority": _priority_spec("Defaults to medium."),
                "start": {"type": "boolean",
                          "description": "Launch the agents now (default false = leave as a draft)."},
            },
            "required": ["title", "spec"],
        },
    },
    {
        "name": "ensemble_update_task",
        "description": (
            "Amend a task's title, spec, priority, board column and/or assigned "
            "agents. Move your OWN task to \"inreview\" when you hand the work "
            "back — that is a thing you know and the board cannot infer, because "
            "an agent that has finished and one that is stuck both just go quiet. "
            "Only a ProductOwner moves a task to \"done\"; it means accepted after "
            "testing, not handed over. "
            "Title, spec and priority work on drafts and on stopped or running "
            "tasks (a running agent does not re-read the spec; tell it in chat "
            "if it must know). Reassigning agents — adding one, dropping one, "
            "changing a model or a role — is allowed only while the task is NOT "
            "running: stop it first, reassign, start it again. Agents that stay "
            "keep their identity and their conversation; pass their \"identity\" "
            "to be sure which is which. One agent left makes the task solo, two "
            "or more a collaboration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "taskId": {"type": "string"},
                "title": {"type": "string", "description": "New title (omit to keep)."},
                "spec": {"type": "string", "description": "New full spec (omit to keep). Replaces the old one."},
                "priority": _priority_spec("Omit to keep the current one."),
                "workflow": {"type": "string",
                             "enum": list(WORKFLOW_NAMES),
                             "description": "Board column (omit to keep). backlog | todo | "
                                            "inprogress | inreview | done. Move your own task to "
                                            "\"inreview\" when you hand work back. \"done\" is "
                                            "the ProductOwner's alone."},
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
            "home": _d.project_home(p, create=False), "isGit": bool(p.get("isGit")),
            "poRoomId": p.get("poRoomId", "")}


def _project_po(project: dict | None) -> dict | None:
    """The project's PO as a caller sees it, or None when it has none.

    ``identity`` is who in that room a report is addressed to; ``live`` says
    whether anyone is there to be woken right now."""
    rid = ((project or {}).get("poRoomId") or "").strip()
    if not rid:
        return None
    room = _d.chatroom.get_room(rid)
    if room is None:
        return {"roomId": rid, "missing": True,
                "note": "the project names a PO task that no longer exists"}
    return {"roomId": rid, "title": _title(room),
            "identity": _d.chatroom.po_identity(room),
            "live": _d._room_is_live(room), "projectId": project["id"]}


def _report_view(room: dict) -> dict | None:
    rep = room.get("lastReport")
    if not isinstance(rep, dict):
        return None
    text = rep.get("text", "") or ""
    return {"kind": rep.get("kind", ""), "identity": rep.get("identity", ""),
            "ts": rep.get("ts"), "to": rep.get("to"),
            "text": (text[:300] + "…") if len(text) > 300 else text}


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


def _attention_by_room() -> dict:
    """Attention items keyed by task id. Cached in the attention module, so
    asking for it per listing costs nothing."""
    try:
        return _d.attention.by_room()
    except Exception:
        return {}


def _attention_view(item: dict | None) -> dict | None:
    """The compact form carried on a task row: what is wrong and why."""
    if not item:
        return None
    out = {"state": item["state"], "reason": item["reason"],
           "agent": item.get("agentIdentity", ""), "since": item.get("since", 0)}
    for k in ("quote", "cause", "exitCode"):
        if k in item:
            out[k] = item[k]
    return out


def _row(room: dict, projects: dict, links: dict, labels: dict,
         attn: dict | None = None) -> dict:
    pid = _project_of_room(room, links)
    spec = room.get("spec", "") or ""
    prio = _d.priority_of(room)
    return {
        "id": room["id"],
        "attention": _attention_view((attn or {}).get(room["id"])),
        "title": _title(room, labels),
        "priority": prio,
        "priorityName": _d.PRIORITY_NAMES[prio],
        "status": _status(room),
        "workflow": _d.workflow_of(room),
        "mode": room.get("mode", ""),
        "projectId": pid,
        "project": (projects.get(pid) or {}).get("name", "") if pid else "",
        "agents": _agents_view(room),
        "specPreview": (spec[:160] + "…") if len(spec) > 160 else spec,
        "messages": len(room.get("messages", []) or []),
        "lastReport": _report_view(room),
        "createdAt": room.get("createdAt"),
        "updatedAt": room.get("updatedAt"),
    }


def _workflow(value, ctx):
    """Validate a requested board column, and enforce who may set it.

    Accepting work is the ProductOwner's call, so "done" is refused to anyone
    else. This is a real check rather than an honour system, and it is worth
    saying why: ``ctx["identity"]`` comes from ``chatroom.resolve_token`` — the
    bearer token minted per participant at room creation — so an agent cannot
    answer "who am I" with someone else's name. The other half is that the
    role cannot be self-granted: ``normalize_agent_specs`` refuses
    ProductOwner from every agent-side caller, so it only ever arrives from
    the human's own UI.

    Everything short of "done" stays open to the working agent, which is the
    point — an agent that finishes moves its own task to "inreview" and the
    owner decides whether that is true."""
    if value is None:
        return None
    w = _d.normalize_workflow(value)
    if w is None:
        raise ToolError(f"workflow must be one of: {_d.WORKFLOW_CHOICES}")
    if w in _d.OWNER_ONLY_WORKFLOW and not _d.is_product_owner(ctx["room"], ctx["identity"]):
        raise ToolError(
            f"only a ProductOwner moves a task to {_d.WORKFLOW_LABELS[w]} — it is "
            "accepted after testing, not when the work is handed over. Move it to "
            "\"inreview\" and say so in chat.")
    return w


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


def _priority(value):
    """A caller's priority as the stored int, or None when they didn't give one.
    Raises so the agent gets a readable message instead of a silent default."""
    if value is None or value == "":
        return None
    n = _d.normalize_priority(value)
    if n is None:
        raise ToolError(f"priority must be one of {_d.PRIORITY_CHOICES} "
                        f"(or 1-{len(_d.PRIORITY_NAMES)}, 1 = highest); got {value!r}")
    return n


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
    proj = projects.get(ctx["projectId"])
    po = _project_po(proj)
    if po and po.get("roomId") == room["id"]:
        reports_to = "the user — you are this project's PO"
    elif po and not po.get("missing"):
        reports_to = f"the PO: '{po['identity']}' in task {po['roomId']} ({po['title']})"
    else:
        reports_to = "the user — this project has no PO"
    return {
        "identity": ctx["identity"],
        "agent": part.get("agent", ""),
        "model": part.get("model", ""),
        "role": part.get("role", ""),
        "taskId": room["id"],
        "title": _title(room),
        "mode": room.get("mode", ""),
        "status": _status(room),
        "workflow": _d.workflow_of(room),
        "cwd": part.get("cwd") or room.get("cwd", ""),
        "taskDir": room.get("taskDir", ""),
        "project": _project_view(proj),
        "projectPO": po,
        "reportsTo": reports_to,
        "isProjectPO": bool(po and po.get("roomId") == room["id"]),
        "writeScope": (ctx["projectId"] or "any project (your task has no project)"),
        "teammates": mates,
        "productOwner": _d.operator_name(),
    }


def _report(ctx, args, handler):
    """Record a report on the caller's task and deliver it to the project's PO.

    The task record comes first: even if the PO room is gone or asleep, the
    board shows the task as waiting on a human with the report quoted."""
    kind = (args.get("kind") or "").strip().lower()
    if kind not in REPORT_KINDS:
        raise ToolError(f"kind must be one of: {', '.join(REPORT_KINDS)}")
    text = (args.get("text") or "").strip()
    if not text:
        raise ToolError("text is required — write the report itself")
    room, me = ctx["room"], ctx["identity"]
    title = _title(room)
    po = _project_po(_projects().get(ctx["projectId"]))
    if po and (po.get("missing") or po["roomId"] == room["id"] or not po.get("identity")):
        po = None                   # no PO to route to: the report goes to the user
    routed = ({"roomId": po["roomId"], "identity": po["identity"], "title": po["title"]}
              if po else None)
    heading = (f"**Report — {kind}**, sent to the PO (*{po['title']}*)" if po
               else f"**Report — {kind}**")
    _d.chatroom.record_report(room["id"], me, kind, text, routed, heading=heading)
    if not po:
        return {"ok": True, "kind": kind, "deliveredTo": "user",
                "note": "recorded on your task; the board shows it to the user"}
    body = f"**{kind}** — report from task *{title}* (`{room['id']}`, {me}):\n\n{text}"
    res = _d.chatroom.post_report(po["roomId"], f"{me}@{room['id']}", po["identity"], body,
                                  {"reportKind": kind, "taskId": room["id"],
                                   "taskTitle": title, "reporter": me})
    rung = handler._ring_report(po["roomId"], res, room["id"], title, me, kind, text) if res else []
    return {"ok": True, "kind": kind,
            "deliveredTo": {"roomId": po["roomId"], "identity": po["identity"],
                            "title": po["title"]},
            "poWoken": bool(rung),
            "note": ("delivered, and the PO was woken" if rung else
                     "delivered to the PO's room, but the PO is not running, so it "
                     "was not woken — it will see the report when it next reads")}


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
    attn = _attention_by_room()
    rows = []
    for r in _d.chatroom.list_rooms():
        rpid = _project_of_room(r, links)
        if pid != "*" and rpid != pid:
            continue
        row = _row(r, projects, links, labels, attn)
        if not include_stopped and row["status"] == "stopped":
            continue
        rows.append(row)
    # Priority first (1 = highest), then the previous recency order inside it.
    rows.sort(key=lambda x: (x["priority"], -(x.get("updatedAt") or 0)))
    scope = "all projects" if pid == "*" else (projects.get(pid, {}).get("name") or "Unassigned")
    return {"scope": scope, "projectId": ("" if pid == "*" else pid), "tasks": rows}


def _list_attention(ctx, args, handler):
    """The tasks that need a human, and why.

    Scoped exactly like :func:`_list_tasks` — your own project by default, any
    project by id, ``"*"`` for all — because it answers the same question about
    the same rows and a second, different rule would only surprise the caller.
    """
    pid = args.get("projectId")
    pid = ctx["projectId"] if pid is None or pid == "" else str(pid).strip()
    projects = _projects()
    if pid != "*" and pid and pid not in projects:
        raise ToolError(f"no such project: {pid}")
    want = (args.get("state") or "").strip()
    if want and want not in _d.attention.STATES:
        raise ToolError(f"no such state: {want} (one of {', '.join(_d.attention.STATES)})")
    snap = _d.attention.snapshot()
    items = [dict(it) for it in snap["items"]
             if (pid == "*" or it.get("projectId", "") == pid)
             and (not want or it["state"] == want)]
    for it in items:
        it.pop("ptyId", None)          # a browser handle, meaningless to an agent
        if not it.get("otherStates"):
            it.pop("otherStates", None)
    scope = "all projects" if pid == "*" else (projects.get(pid, {}).get("name") or "Unassigned")
    return {"scope": scope, "projectId": ("" if pid == "*" else pid),
            "count": len(items), "needAttention": items,
            "states": {"agent_gone": "its terminal died and nobody asked it to",
                       "blocked": "running, but it says it cannot continue",
                       "waiting_for_you": "it is waiting on a human answer — including "
                                          "a task that reported it finished or asked a question",
                       "stalled": "asked to do something, not working, never reported back"}}


def _plan_usage(ctx, args, handler):
    """The board header's own reading, unchanged.

    Deliberately the same `usage.snapshot()` the chip and `/api/usage` read —
    one reader, one cache, one set of staleness guards. Like the endpoint this
    is a cache read: it does no network call and cannot block the agent's turn.
    """
    snap = _d.usage.snapshot()
    return {
        **snap,
        "note": ("Account-wide, not per-task: every task on this machine shares "
                 "these windows. Claude and Codex allowances are separate — never "
                 "sum them. Judge freshness per window (windows[].trusted), not "
                 "per source: the source flag is only the conjunction, and one "
                 "Codex reading can be stale for the five-hour window while still "
                 "exact for the weekly one. trusted=false means the percent is a "
                 "floor, not a measurement. percent=null means there is no current "
                 "value — rolledOver (already reset) or resetUnknown (no reset "
                 "time to check) — and stalePercent is a dead number kept only "
                 "for context, never a fallback."),
    }


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
    priority = _priority(args.get("priority"))
    ok, room_full, err = _d.create_task(title, spec, pid, agent_list, workspace,
                                        priority)
    if not ok:
        raise ToolError(err)
    started = False
    if args.get("start") is True:
        handler._start_room(room_full)
        started = True
    return {"ok": True, "taskId": room_full["id"], "title": room_full["title"],
            "status": "running" if started else "draft",
            "priority": _d.PRIORITY_NAMES[_d.priority_of(room_full)],
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
    priority = _priority(args.get("priority"))
    workflow = _workflow(args.get("workflow"), ctx)
    agent_list = args.get("agents")
    if (title is None and spec is None and priority is None
            and workflow is None and agent_list is None):
        raise ToolError("give a new title, spec, priority, workflow and/or agents")
    notes = []
    room2 = room
    if title is not None or spec is not None or priority is not None or workflow is not None:
        ok, room2, err = _d.update_task(room["id"], title=title, spec=spec,
                                        priority=priority, workflow=workflow)
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
            "priority": _d.PRIORITY_NAMES[_d.priority_of(room2)],
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
    "ensemble_report": _report,
    "ensemble_list_projects": _list_projects,
    "ensemble_list_tasks": _list_tasks,
    "ensemble_list_attention": _list_attention,
    "ensemble_plan_usage": _plan_usage,
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
