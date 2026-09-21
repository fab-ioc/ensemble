"""Ensemble task-management MCP tools — how an agent running under Ensemble
reads and shapes the board it lives on: list projects and tasks, read a task's
spec and chat, create/amend/start/stop/move/delete tasks, read and update a
project's roadmap.

These tools are served from the same ``/mcp`` endpoint as the chat tools, so
every headless agent (solo or collaboration) gets them automatically; the
bearer token minted at room creation identifies the caller and its room, and
the room's project scopes what the caller may change.

Scope rules (deliberately conservative — a planner for project A must not be
able to reshape project B):

* **Read anywhere.** Listing and reading tasks in any project is allowed.
* **Write inside your own project.** For callers with administration tools,
  create/update/start/stop/delete/move are allowed only on tasks of the caller's
  project. An administrator whose task has no project may write anywhere.
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

_TASK_ID_DOC = ("The task: its number (#18 or 18 in your project, ED-18 in any) "
                "or its id (room-1a2b3c4d).")
_TASK_ID_SPEC = {"type": "string", "description": _TASK_ID_DOC}

_PRIORITY_DOC = ("Priority: \"highest\", \"high\", \"medium\", \"low\" or \"lowest\" "
                 "(a number 1-5 also works, 1 = highest).")


def _priority_spec(tail: str) -> dict:
    # A plain string type — every other schema here is scalar, and the server
    # takes "2" and 2 alike, so nothing is lost by not declaring a union.
    return {"type": "string", "description": f"{_PRIORITY_DOC} {tail}"}

_ALT_AGENT_SPEC = {
    "type": "object",
    "properties": {
        "agent": {"type": "string",
                  "description": "Alternative agent kind for this seat: \"claude\" or \"codex\"."},
        "model": {"type": "string",
                  "description": "Optional model to use if this alternative kind is chosen."},
    },
    "required": ["agent"],
}

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
        "alt": _ALT_AGENT_SPEC,
    },
    "required": ["agent"],
}

_ALL_TOOLS = [
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
            "repeat it to be sure it arrived. A blocked or question report stays "
            "on the user's Needs you list until the user answers in your chat or "
            "you report completed; an update about something else leaves it "
            "there. When what you asked for was resolved without an answer in "
            "chat, say so: an update with clears: true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": list(REPORT_KINDS),
                         "description": "completed | blocked | question | update."},
                "text": {"type": "string", "description": "The report, Markdown."},
                "clears": {"type": "boolean",
                           "description": "With kind update: your open blocked / question "
                                          "is over (resolved without an answer in chat). "
                                          "Default false: the ask stays open."},
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
            "is slim by default: id, title, priority, status (draft | running | "
            "waiting_user | paused | stopped), workflow, attention, agents and "
            "updatedAt; project is added only for a multi-project result. Set "
            "detail=true for the previous full rows, including spec previews, "
            "report previews and message counts. Rows come back "
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
                "detail": {"type": "boolean",
                           "description": "Include full row metadata, spec/report previews and "
                                          "message counts (default false)."},
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
            "before starting many tasks at once, to see whether there is room. "
            "A draft's first launch also uses this cached snapshot to make its "
            "final owner/reviewer allocation; reading it makes no network call. The numbers "
            "are **account-wide**: every task on this machine shares them, so they "
            "say nothing about what one task cost (that is the per-task cost "
            "chip). Claude and Codex have separate allowances — read them "
            "separately and never add them up. Codex itself has several pools "
            "(main, reserve, a model's own), each window naming its `pool`: judge "
            "Codex by the pool its agents run on (`sources[].poolInUse`, "
            "`windows[].inUse`; `kinds.codex` is that pool's figure) — a spent "
            "pool that is not in use is listed under `notices`, not `alerts`. "
            "**Judge each window on its own "
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
            "unknown and never fall back to `stalePercent`."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ensemble_points",
        "description": (
            "The product owner's points in your room: every message they send "
            "you is a point (P12) the hub keeps until they acknowledge its "
            "answer, and it reaches you with a `[point P12]` line under it. "
            "Answer a point by starting a paragraph of your reply with "
            "`Re P12:` (one reply may answer several); the first reply to a "
            "message holding a single point answers it by itself. action "
            "\"list\" (default): the open points and the answered ones not yet "
            "acknowledged. action \"answer\": a point answered by doing rather "
            "than by saying (\"started as #75\"), with a one-line summary. "
            "action \"split\": a message that holds several points becomes "
            "P12a, P12b … (parts: one text each)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "answer", "split"],
                           "description": "list | answer | split (default list)."},
                "point": {"type": "string", "description": "The point, e.g. P12 (answer, split)."},
                "summary": {"type": "string",
                            "description": "With answer: one line, what was done (\"started as #75\")."},
                "parts": {"type": "array", "items": {"type": "string"},
                          "description": "With split: the points the message holds, one text each."},
            },
        },
    },
    {
        "name": "ensemble_get_task",
        "description": (
            "Read one task in full: title, complete spec, priority, project, "
            "status, agents and roles, workspace mode, working directory, task "
            "folder, and its latest report in full. Chat messages are opt-in."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": _TASK_ID_DOC},
                "projectId": {"type": "string",
                              "description": "The project a bare number (#18) is read in (default: yours)."},
                "messages": {"type": "integer",
                             "description": "How many recent chat messages to include (default 0, max 200)."},
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
            "preferred agent line-up. By default the task is created as a DRAFT "
            "(not launched) so the product owner can review it in the dashboard; "
            "set start=true to launch it immediately. On that first launch the hub "
            "checks the latest cached plan allowance and may swap owner/reviewer "
            "kinds; a seat can name `alt: {agent, model}` when its alternative kind "
            "needs a particular model. One agent = a solo task the "
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
                           "description": "Preferred agents for the task (default: one agent of your "
                                          "own kind); the hub makes the final choice at first launch."},
                "workspace": {"type": "string",
                              "enum": ["empty", "inplace", "copy", "worktree"],
                              "description": "Workspace mode: empty (own task folder, no code — the default "
                                             "in a code project), inplace (work in the project's folder — the "
                                             "default in a documents project), copy (a copy of the code folder), "
                                             "worktree (a git worktree on its own branch)."},
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
            "agents. Task owners and reviewers may only move their OWN task to "
            "\"inreview\"; project POs and planners can administer tasks. Move "
            "your OWN task to \"inreview\" when you hand the work "
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
                "taskId": _TASK_ID_SPEC,
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
            "resuming their previous conversation. A draft's first launch checks "
            "the latest cached plan allowance, records the preferred and chosen "
            "line-ups, and returns the reason; a relaunch keeps the kinds that own "
            "the existing conversations. A resumed owner is then told to re-read "
            "its spec and carry on (the spec itself is not sent again). Fails if the "
            "task is already running."
        ),
        "inputSchema": {"type": "object", "properties": {"taskId": _TASK_ID_SPEC},
                        "required": ["taskId"]},
    },
    {
        "name": "ensemble_stop_task",
        "description": (
            "Stop a running task: ends its agents' processes but keeps the task, "
            "its spec and chat, so it can be started again later. You cannot stop "
            "your own task."
        ),
        "inputSchema": {"type": "object", "properties": {"taskId": _TASK_ID_SPEC},
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
        "inputSchema": {"type": "object", "properties": {"taskId": _TASK_ID_SPEC},
                        "required": ["taskId"]},
    },
    {
        "name": "ensemble_get_roadmap",
        "description": (
            "Read a project's roadmap — ROADMAP.md in the project's home folder, "
            "the same text the product owner sees and edits on the project's "
            "Roadmap tab. Defaults to your own project. Returns the Markdown "
            "text, the file path, whether it exists yet, and its `version`: pass "
            "that version to ensemble_update_roadmap so a newer edit is never "
            "overwritten."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectId": {"type": "string",
                              "description": "Project id (omit for your own project)."},
            },
        },
    },
    {
        "name": "ensemble_update_roadmap",
        "description": (
            "Replace a project's roadmap (ROADMAP.md in the project's home) with "
            "new Markdown. Only your own project's roadmap. Read it first with "
            "ensemble_get_roadmap and pass its `version` as baseVersion (\"\" "
            "when it does not exist yet): if the product owner saved since you "
            "read it, the write is refused and their newer text is returned — "
            "merge your change into it and try again with the new version. "
            "Send the whole document, not just the change."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The full new roadmap, Markdown."},
                "baseVersion": {"type": "string",
                                "description": "The `version` from ensemble_get_roadmap that "
                                               "your text is based on (\"\" for a new roadmap)."},
                "projectId": {"type": "string",
                              "description": "Project id (omit for your own project)."},
            },
            "required": ["text", "baseVersion"],
        },
    },
    {
        "name": "ensemble_move_task",
        "description": (
            "Move a task to another project. The task's folder stays where it was "
            "created; only the project link changes. It takes the next number "
            "there; its old number still finds it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"taskId": _TASK_ID_SPEC,
                           "projectId": {"type": "string", "description": "Destination project id."}},
            "required": ["taskId", "projectId"],
        },
    },
]

# Most agents only need to read the board, report, and hand their own work back.
# Project POs and explicitly delegated planners additionally administer it.
# Keep the combined alias for code that needs to inspect every schema.
COMMON_TOOL_NAMES = frozenset({
    "ensemble_whoami", "ensemble_report", "ensemble_list_tasks",
    "ensemble_list_attention", "ensemble_plan_usage", "ensemble_get_task",
    "ensemble_update_task", "ensemble_get_roadmap", "ensemble_points",
})
ADMIN_TOOL_NAMES = frozenset(t["name"] for t in _ALL_TOOLS) - COMMON_TOOL_NAMES
COMMON_TOOLS = [t for t in _ALL_TOOLS if t["name"] in COMMON_TOOL_NAMES]
ADMIN_TOOLS = [t for t in _ALL_TOOLS if t["name"] in ADMIN_TOOL_NAMES]
TOOLS = COMMON_TOOLS + ADMIN_TOOLS

# Offered only to a reviewer on mention (see chatroom.is_on_mention): the one
# way its review ends. Kept literal — no `_d.` at import time.
REVIEW_VERDICT_NAMES = ("approve", "changes_requested", "comment")
REVIEW_TOOLS = [
    {
        "name": "review_done",
        "description": (
            "Finish your review: give your verdict and findings. The hub appends "
            "them to the task's REVIEW-LOG.md (which the next reviewer reads), "
            "sends them to whoever asked for the review and to the project's PO, "
            "and then ends your session. Call it exactly once, when the review is "
            "complete; do not also send the verdict with chat_send."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": list(REVIEW_VERDICT_NAMES),
                            "description": "approve | changes_requested | comment."},
                "summary": {"type": "string",
                            "description": "One line: the verdict in a sentence."},
                "findings": {"type": "string",
                             "description": "The review, Markdown: what is right, what is "
                                            "wrong, what is missing, what to change (file:line "
                                            "where you can), and the status of each earlier "
                                            "finding from the review log."},
            },
            "required": ["verdict", "findings"],
        },
    },
]
REVIEW_TOOL_NAMES = frozenset(t["name"] for t in REVIEW_TOOLS)

# Offered only to the agent that may restart the hub (dashboard.may_restart_hub:
# the PO of a room in HUB_RESTART_ROOMS, the Ensemble Dashboard PO by default).
RESTART_TOOLS = [
    {
        "name": "ensemble_restart_hub",
        "description": (
            "Restart the Ensemble hub on the code already on disk: nothing is "
            "fetched, reset or pulled, so a merge not yet pushed survives. The hub "
            "first starts that code on a spare port and gives up, leaving itself "
            "untouched, if it does not serve. Then, after about 45 seconds (time "
            "for your reply to reach the user), it stops and starts again: every "
            "agent on this machine stops with it. You are resumed and told when "
            "it is back; resume the other rooms yourself. Progress is logged to "
            "~/.ensemble/logs/restart.log. Call it once."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]
RESTART_TOOL_NAMES = frozenset(t["name"] for t in RESTART_TOOLS)


def _role_head(part: dict) -> str:
    return ((part or {}).get("role") or "").split(":", 1)[0].strip().lower()


def is_admin_caller(room: dict, identity: str) -> bool:
    """Whether the caller administers the board: its project's PO or a planner."""
    if not (room or {}).get("id"):
        return False
    part = _d.chatroom.participant(room or {}, identity) or {}
    if _role_head(part) == "planner" or _d.is_product_owner(room, identity):
        return True
    pid = _project_of_room(room or {})
    project = _projects().get(pid)
    return bool(project and (project.get("poRoomId") or "").strip() == room.get("id"))


def _is_project_po(room: dict, identity: str) -> bool:
    """Whether the caller is its project's PO: the agent that reports are
    addressed to in the room the project names as ``poRoomId``. A project's PO
    is chosen by the person on the page, whatever role its seat carries (a
    documents project's PO, a past session made PO), and it accepts the work
    of its project's tasks: nothing merges there to move a card by itself."""
    if not (room or {}).get("id"):
        return False
    project = _projects().get(_project_of_room(room)) or {}
    return ((project.get("poRoomId") or "").strip() == room["id"]
            and _d.chatroom.po_identity(room) == identity)


def tool_schemas(room: dict, identity: str) -> list[dict]:
    """Task schemas offered to this authenticated participant."""
    tools = list(COMMON_TOOLS)
    if is_admin_caller(room, identity):
        tools += list(ADMIN_TOOLS)
    part = _d.chatroom.participant(room or {}, identity) or {}
    if _d.chatroom.is_on_mention(room or {}, part):
        tools += list(REVIEW_TOOLS)
    if _d.may_restart_hub((room or {}).get("id", ""), identity):
        tools += list(RESTART_TOOLS)
    return tools


def _allowed_names(ctx: dict) -> frozenset[str]:
    return frozenset(t["name"] for t in tool_schemas(ctx["room"], ctx["identity"]))


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
            "poRoomId": p.get("poRoomId", ""), "kind": p.get("kind") or "code"}


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


def _report_view(room: dict, full: bool = False) -> dict | None:
    rep = room.get("lastReport")
    if not isinstance(rep, dict):
        return None
    text = rep.get("text", "") or ""
    return {"kind": rep.get("kind", ""), "identity": rep.get("identity", ""),
            "ts": rep.get("ts"), "to": rep.get("to"),
            "text": text if full else ((text[:300] + "…") if len(text) > 300 else text)}


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
    out = []
    for p in room.get("participants", []):
        if p.get("kind") != "agent":
            continue
        a = {"identity": p.get("identity", ""), "agent": p.get("agent", ""),
             "model": p.get("model", ""), "role": p.get("role", "")}
        if _d.chatroom.is_on_mention(room, p):
            # Not a running agent: started fresh for each review request.
            a["runs"] = "reviewing now" if _d._pty_alive(p.get("ptyId")) else "on mention"
        out.append(a)
    return out


def _agents_summary(room: dict) -> list[dict]:
    """Only the role information useful while scanning task rows."""
    out = []
    for p in room.get("participants", []):
        if p.get("kind") != "agent":
            continue
        a = {"kind": p.get("agent", ""), "model": p.get("model", ""),
             "role": p.get("role", "")}
        if _d.chatroom.is_on_mention(room, p):
            a["runs"] = "reviewing now" if _d._pty_alive(p.get("ptyId")) else "on mention"
        out.append(a)
    return out


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


def _ref(room: dict, projects: dict) -> str:
    """ED-18: a task's number with its project's key; "" without a number."""
    if not room.get("no"):
        return ""
    keys = _d.project_keys(list(projects.values()))
    return _d.task_numbers.label(room["no"], keys.get(room.get("noProjectId") or "", ""))


def _row(room: dict, projects: dict, links: dict, labels: dict,
         attn: dict | None = None, detail: bool = False,
         include_project: bool = False) -> dict:
    pid = _project_of_room(room, links)
    spec = room.get("spec", "") or ""
    prio = _d.priority_of(room)
    no = room.get("no") or None
    # The number first: it is how a person and a PO name the task.
    ref = ({"no": no, **({"ref": _ref(room, projects)} if detail or include_project else {})}
           if no else {})
    compact = {
        **ref,
        "id": room["id"],
        "title": _title(room, labels),
        "priority": prio,
        "status": _status(room),
        "workflow": _d.workflow_of(room),
        "attention": _attention_view((attn or {}).get(room["id"])),
        "agents": _agents_summary(room),
        "updatedAt": room.get("updatedAt"),
    }
    if include_project:
        compact["project"] = (projects.get(pid) or {}).get("name", "") if pid else ""
    if not detail:
        return compact
    return {
        **ref,
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
        "allocation": room.get("allocation"),
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
    if (w in _d.OWNER_ONLY_WORKFLOW and not _d.is_product_owner(ctx["room"], ctx["identity"])
            and not _is_project_po(ctx["room"], ctx["identity"])):
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


def _load_target(task_id, ctx: dict | None = None, project_id=None) -> dict:
    """The task a caller names: its id, or its number (#18 or 18 in
    ``project_id``, else the caller's project; ED-18 in any)."""
    tid = str(task_id if task_id is not None and not isinstance(task_id, bool) else "").strip()
    if not tid:
        raise ToolError("taskId is required")
    if not tid.startswith("room-"):
        pid = str(project_id or "").strip() or (ctx or {}).get("projectId", "")
        rid, why = _d.resolve_task_ref(tid, pid)
        if not rid:
            raise ToolError(why)
        tid = rid
    room = _d.chatroom.get_room(tid, public=False)
    if room is None:
        raise ToolError(f"no such task: {tid}")
    return room


def _check_write_scope(ctx: dict, target_pid: str, what: str) -> None:
    """An administrator with a project may write only inside it; an allowed
    project-less administrator may write anywhere."""
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
        "taskNo": room.get("no") or None,
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
    # Only an update can say an earlier ask is over: a completed ends it by
    # itself, a new blocked or question takes its place.
    clears = kind == "update" and args.get("clears") is True
    _d.chatroom.record_report(room["id"], me, kind, text, routed, heading=heading, clears=clears)
    if not po:
        return {"ok": True, "kind": kind, "deliveredTo": "user",
                "note": "recorded on your task; the board shows it to the user"}
    no = _d.task_label(room)
    body = (f"**{kind}**{f' (its open ask to {_d.operator_name()} is over)' if clears else ''} — "
            f"report from task {no + ' ' if no else ''}*{title}* "
            f"(`{room['id']}`, {me}):\n\n{text}")
    res = _d.chatroom.post_report(po["roomId"], f"{me}@{room['id']}", po["identity"], body,
                                  {"reportKind": kind, "taskId": room["id"], "taskNo": room.get("no") or None,
                                   "taskTitle": title, "reporter": me})
    rung = handler._ring_report(po["roomId"], res, room["id"], title, me, kind, text) if res else []
    return {"ok": True, "kind": kind,
            "deliveredTo": {"roomId": po["roomId"], "identity": po["identity"],
                            "title": po["title"]},
            "poWoken": bool(rung),
            "note": ("delivered, and the PO was woken" if rung else
                     "delivered to the PO's room, but the PO is not running, so it "
                     "was not woken — it will see the report when it next reads")}


def _review_done(ctx, args, handler):
    """A reviewer on mention hands in its review: log it, send it to whoever
    asked and to the PO, and end the session."""
    room, me, part = ctx["room"], ctx["identity"], ctx["part"]
    if not _d.chatroom.is_on_mention(room, part):
        raise ToolError("review_done is for a reviewer started on mention — "
                        "reply with chat_send instead")
    verdict = (args.get("verdict") or "").strip().lower()
    if verdict not in REVIEW_VERDICT_NAMES:
        raise ToolError(f"verdict must be one of: {', '.join(REVIEW_VERDICT_NAMES)}")
    findings = (args.get("findings") or "").strip()
    if not findings:
        raise ToolError("findings are required — write the review itself")
    summary = " ".join((args.get("summary") or "").split())[:300]
    review = part.get("review") or {}
    if review.get("endedAt"):
        raise ToolError("this review is already recorded and your session is ending")
    log_text = _d.read_review_log(room)
    n = review.get("n") or (_d.review_count(log_text) + 1)
    label = _d.REVIEW_VERDICTS[verdict]
    asker = review.get("askedBy") or ""
    title = _title(room)
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    where = ""
    if review.get("branch") or review.get("head"):
        where = (f"- Reviewed: branch `{review.get('branch') or '?'}` at "
                 f"`{review.get('head') or '?'}`"
                 + (f" (against `{review['base']}`)" if review.get("base") else "") + "\n")
    question = " ".join((review.get("question") or "").split())[:400]
    entry = (f"## Review {n} — {label} — {when}\n\n"
             f"- Reviewer: {me} ({part.get('agent', '')}"
             + (f" {part.get('model')}" if part.get("model") else "") + ")\n"
             + (f"- Asked by: {asker} — “{question}”\n" if asker else "")
             + where
             + (f"- Summary: {summary}\n" if summary else "")
             + f"\n{findings}\n")
    path = _d.append_review_log(room, entry)

    # To whoever asked, in the task's own chat.
    po = _project_po(_projects().get(ctx["projectId"]))
    if po and (po.get("missing") or po["roomId"] == room["id"] or not po.get("identity")):
        po = None
    idents = {p.get("identity") for p in room.get("participants", [])}
    to = asker if asker in idents and asker != me else ""
    head = f"**Review {n}: {label}**" + (f" — {summary}" if summary else "")
    tail = f"\n\n_Added to `{_d.REVIEW_LOG_NAME}`" + ("; sent to the PO._" if po else "._")
    res = _d.chatroom.post_message(room["id"], me, f"{head}\n\n{findings}{tail}", to=to)
    if res:
        handler._ring_recipients(room["id"], res)

    # To the PO, as a report into its room (it wakes the PO).
    po_woken = False
    if po:
        no = _d.task_label(room)
        body = (f"**review {n} — {label}** of task {no + ' ' if no else ''}*{title}* (`{room['id']}`, by {me}"
                + (f", asked by {asker}" if asker else "") + f"):\n\n"
                + (f"{summary}\n\n" if summary else "") + findings)
        pres = _d.chatroom.post_report(po["roomId"], f"{me}@{room['id']}", po["identity"], body,
                                       {"reportKind": "review", "verdict": verdict,
                                        "taskId": room["id"], "taskNo": room.get("no") or None,
                                        "taskTitle": title, "reporter": me})
        po_woken = bool(handler._ring_report(po["roomId"], pres, room["id"], title, me,
                                             f"review {n} ({label})",
                                             summary or findings)) if pres else False

    _d.finish_review(room["id"], me, verdict)
    return {"ok": True, "review": n, "verdict": verdict, "log": str(path),
            "sentTo": to or "everyone",
            "po": ({"roomId": po["roomId"], "identity": po["identity"], "woken": po_woken}
                   if po else "none — this project has no PO"),
            "note": "recorded. Your session ends in a few seconds; there is nothing more to do."}


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
    detail = args.get("detail") is True
    selected = []
    for r in _d.chatroom.list_rooms():
        rpid = _project_of_room(r, links)
        if (pid == "*" or rpid == pid) and (include_stopped or _status(r) != "stopped"):
            selected.append((r, rpid))
    include_project = len({rpid for _, rpid in selected}) > 1
    rows = []
    for r, _ in selected:
        row = _row(r, projects, links, labels, attn, detail=detail,
                   include_project=include_project)
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
        # What the hub itself goes by when it picks a kind for a seat that
        # names no model: each kind's worst current window, Codex's taken from
        # the pool its agents run on.
        "kinds": {kind: _d._kind_usage(snap, kind) for kind in ("claude", "codex")},
        "note": ("Codex has several pools, each its own allowance: the main one "
                 "(windows with model=null), the reserve (only the model "
                 "gpt-reserve draws on it) and a model's own. Judge Codex by the "
                 "pool its agents run on — sources[].poolInUse, windows[].inUse, "
                 "and `kinds.codex` is that pool's figure. A spent pool that is "
                 "not in use stops nothing: it is listed under `notices`, never "
                 "`alerts`. "
                 "Account-wide, not per-task: every task on this machine shares "
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
    room = _load_target(args.get("taskId"), ctx, args.get("projectId"))
    projects = _projects()
    links = _d.load_session_projects()
    labels = _d.load_labels()
    n = args.get("messages", 0)
    try:
        n = max(0, min(200, int(n)))
    except (TypeError, ValueError):
        n = 0
    msgs = room.get("messages", []) or []
    tail = [{"from": m.get("from"), "to": m.get("to", ""), "ts": m.get("ts"),
             "text": (m.get("text") or "")[:2000]} for m in msgs[-n:]] if n else []
    row = _row(room, projects, links, labels, detail=True)
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
    if row["isYou"]:
        # This session has now read its spec as it stands: the next resume
        # note need not send it to read it again (dashboard.resume_note_for).
        try:
            seen = _d.spec_seen(ctx["part"], room.get("spec", "") or "")
            if ctx["part"].get("specSeen") != seen["specSeen"]:
                _d.chatroom.patch_participant(room["id"], ctx["identity"], seen)
        except Exception:
            pass
    row["lastReport"] = _report_view(room, full=True)
    real = _d.chatroom.last_real_report(room)
    if real and real.get("ts") != (row["lastReport"] or {}).get("ts"):
        # An update came after it: the question or completion still stands.
        row["lastRealReport"] = _report_view({"lastReport": real}, full=True)
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
    workspace = (args.get("workspace") or "").strip()
    priority = _priority(args.get("priority"))
    ok, room_full, err = _d.create_task(title, spec, pid, agent_list, workspace,
                                        priority)
    if not ok:
        raise ToolError(err)
    started = False
    if args.get("start") is True:
        try:
            handler._start_room(room_full)
        except _d.StartRoomError as exc:
            raise ToolError(str(exc)) from exc
        started = True
    allocation = room_full.get("allocation") if started else None
    return {"ok": True, "no": room_full.get("no") or None, "taskId": room_full["id"], "title": room_full["title"],
            "status": "running" if started else "draft",
            "priority": _d.PRIORITY_NAMES[_d.priority_of(room_full)],
            "projectId": pid, "taskDir": room_full.get("taskDir", ""),
            "cwd": room_full.get("cwd", ""),
            "agents": _agents_view(room_full),
            **({"allocation": allocation} if allocation else {}),
            "note": ((allocation or {}).get("reason", "launched") if started else
                     "created as a draft — the product owner can start it from the "
                     "dashboard, or call ensemble_start_task")}


def _update_task(ctx, args, handler):
    room = _load_target(args.get("taskId"), ctx)
    _check_write_scope(ctx, _project_of_room(room), "update_task")
    if not is_admin_caller(ctx["room"], ctx["identity"]):
        changes_other_than_workflow = any(args.get(k) is not None
                                          for k in ("title", "spec", "priority", "agents"))
        if (room["id"] != ctx["room"]["id"] or changes_other_than_workflow
                or _d.normalize_workflow(args.get("workflow")) != "inreview"):
            raise ToolError(
                "task owners and reviewers may use update_task only to move their "
                "own task to In review; project POs and planners administer tasks")
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
    room = _load_target(args.get("taskId"), ctx)
    _check_write_scope(ctx, _project_of_room(room), "start_task")
    _not_self(ctx, room, "start")
    if _d._room_is_live(room):
        raise ToolError("that task is already running")
    first_launch = not room.get("launched", True)
    try:
        # Through the hub's resume guard: one resume per task at a time, and
        # a message the board sent it meanwhile is delivered once it is up.
        handler._resume_room(room)
    except _d.StartRoomError as exc:
        raise ToolError(str(exc)) from exc
    active = _d.chatroom.get_room(room["id"], public=False) or room
    allocation = active.get("allocation") if first_launch else None
    chosen = _agents_view(active)
    return {"ok": True, "taskId": room["id"], "status": "running",
            "agents": [a["identity"] for a in chosen],
            "chosenAgents": chosen,
            **({"allocation": allocation, "note": allocation.get("reason", "")}
               if allocation else {"note": "resumed with the existing agent line-up"})}


def _stop_task(ctx, args, handler):
    room = _load_target(args.get("taskId"), ctx)
    _check_write_scope(ctx, _project_of_room(room), "stop_task")
    _not_self(ctx, room, "stop")
    if not _d._room_is_live(room):
        return {"ok": True, "taskId": room["id"], "status": _status(room),
                "note": "it was not running"}
    _d.stop_task(room["id"])
    return {"ok": True, "taskId": room["id"], "status": "stopped"}


def _delete_task(ctx, args, handler):
    room = _load_target(args.get("taskId"), ctx)
    _check_write_scope(ctx, _project_of_room(room), "delete_task")
    _not_self(ctx, room, "delete")
    if _d._room_is_live(room):
        raise ToolError("that task is running — stop it first (ensemble_stop_task)")
    res = _d.delete_task(room["id"])
    if not res.get("ok", True):
        raise ToolError(res.get("error") or "the task could not be deleted")
    return {"ok": True, "taskId": room["id"], "deleted": True,
            "transcriptsRemoved": len(res.get("transcripts", [])),
            "taskDirKept": room.get("taskDir", "")}


def _move_task(ctx, args, handler):
    # A bare number is read in the caller's project: projectId is where it goes.
    room = _load_target(args.get("taskId"), ctx)
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
    moved = _d.chatroom.get_room(room["id"]) or room
    return {"ok": True, "no": moved.get("no") or None, "ref": _ref(moved, projects), "taskId": room["id"],
            "projectId": dest, "project": projects[dest].get("name", "")}


def _roadmap_project(ctx, args) -> dict:
    pid = args.get("projectId")
    pid = ctx["projectId"] if pid is None or pid == "" else str(pid).strip()
    if not pid:
        raise ToolError("your task has no project — pass projectId")
    proj = _projects().get(pid)
    if proj is None:
        raise ToolError(f"no such project: {pid}")
    return proj


def _get_roadmap(ctx, args, handler):
    proj = _roadmap_project(ctx, args)
    out = _d.read_roadmap(proj)
    out["project"] = proj.get("name", "")
    if not out["exists"]:
        out["note"] = ("no roadmap yet — ensemble_update_roadmap with baseVersion \"\" "
                       "creates it")
    return out


def _update_roadmap(ctx, args, handler):
    proj = _roadmap_project(ctx, args)
    own = ctx["projectId"]
    if own and proj["id"] != own:
        raise ToolError(f"update_roadmap is out of scope: that roadmap belongs to project "
                        f"'{proj['id']}', you are scoped to '{own}'")
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ToolError("text is required — the whole roadmap, Markdown")
    base = args.get("baseVersion")
    if base is None:
        raise ToolError("baseVersion is required — read the roadmap with "
                        "ensemble_get_roadmap and pass its version (\"\" for a new one)")
    ok, res = _d.write_roadmap(proj, text, str(base).strip())
    if ok:
        return {"ok": True, "projectId": proj["id"], "path": res["path"],
                "version": res["version"], "mtime": res["mtime"]}
    if res.get("error") != "conflict":
        raise ToolError(res.get("error") or "could not save the roadmap")
    # Refused, so it is an error result — but one that carries the newer text,
    # because the agent's next move is to merge into it.
    cur = res["current"]
    raise ToolError(
        "conflict: the roadmap changed since you read it (the product owner may have "
        "edited it) — nothing was written. Merge your change into the current text "
        "below and call ensemble_update_roadmap again with its version as baseVersion.\n"
        + _text({"current": {"exists": cur["exists"], "version": cur["version"],
                             "mtime": cur["mtime"], "text": cur["text"]}}))


def _points(ctx, args, handler):
    pts = _d.points
    rid = ctx["room"]["id"]
    action = (args.get("action") or "list").strip().lower()
    pid = (args.get("point") or "").strip()
    if action == "answer":
        summary = " ".join(str(args.get("summary") or "").split())
        if not pid or not summary:
            raise ToolError("answer needs point (e.g. P12) and a one-line summary")
        p = pts.answer_by_tool(rid, pid, summary[:300], ctx["identity"])
        if p is None:
            raise ToolError(f"no point {pid} in your room")
        return {"ok": True, "point": p["id"], "state": p["state"]}
    if action == "split":
        kids = pts.split(rid, pid, args.get("parts") or [])
        if kids is None:
            raise ToolError("split needs an open point that was not split before (e.g. P12) "
                            "and two to 26 parts")
        return {"ok": True, "point": pid, "parts": [{"id": k["id"], "text": k["text"]} for k in kids]}
    if action != "list":
        raise ToolError("action is list, answer or split")
    led = pts.sync(rid, force=True)
    show = [p for p in led["points"] if p["state"] in ("open", "answered")]
    return {"note": ("open: no answer yet; answered: waiting for the product owner to "
                     "acknowledge. Answer an open one with 'Re Pn:' in your reply."),
            "points": [{"id": p["id"], "state": p["state"], "owner": p.get("owner", ""),
                        "since": time.strftime("%m-%d %H:%M", time.localtime(p.get("openedAt") or p["createdAt"])),
                        "text": pts.asked(p)[:1000]}
                       for p in sorted(show, key=lambda p: p["createdAt"])]}


def _restart_hub(ctx, args, handler):
    res = _d.trigger_restart(ctx["room"]["id"])
    res.pop("status", None)
    if not res.get("started"):
        raise ToolError(res.get("error") or "the restart did not start")
    return res


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
    "ensemble_get_roadmap": _get_roadmap,
    "ensemble_update_roadmap": _update_roadmap,
    "review_done": _review_done,
    "ensemble_restart_hub": _restart_hub,
    "ensemble_points": _points,
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
        if name not in _allowed_names(ctx):
            role = _role_head(ctx["part"]) or "task owner"
            if name in REVIEW_TOOL_NAMES:
                raise ToolError(
                    f"{name} is available only to a reviewer during an active review")
            if name in RESTART_TOOL_NAMES:
                raise ToolError(_d.RESTART_REFUSED)
            raise ToolError(
                f"{name} is not available to role '{role}': board administration "
                "tools are reserved for a project's PO room or an agent whose "
                "role is planner")
        result = fn(ctx, args or {}, handler)
        return _text(result), False
    except ToolError as e:
        return f"error: {e}", True
    except Exception as e:  # never let a tool bug kill the agent's turn
        return f"error: {type(e).__name__}: {e}", True
