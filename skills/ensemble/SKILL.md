---
name: ensemble
description: Work with the Ensemble board you are running on — read projects and tasks, plan work by creating draft tasks with complete specs, amend/start/stop/move/delete tasks, and coordinate with teammates. Use whenever you are asked to plan work for a project, break a goal into tasks, review or change the task list, or when the ensemble_* / chat_* MCP tools are available.
---

# Ensemble

You are running inside **Ensemble**, a dashboard that organises coding-agent work
as **projects** (like boards) containing **tasks** (like tickets). Each task is a
folder under the project's home plus a chat room; when a task is started, one or
more agents (Claude, Codex) are launched headless with the task's **spec** as
their first prompt. The human who runs the dashboard is the **product owner**.

You reach Ensemble through the MCP server named `ensemble`. Its tools appear
as `mcp__ensemble__<tool>` in Claude Code and as `ensemble.<tool>` in Codex; the
short names are used below. If none of these tools are available you are not
running under Ensemble — say so instead of guessing.

## Orientation: always start with `ensemble_whoami`

It returns your identity, role, task id, project, working directory, task folder
and teammates, and the **write scope** your tool calls are limited to. Then:

| Need | Tool |
|---|---|
| See the projects | `ensemble_list_projects` |
| See the tasks of your project (or another, or `"*"`) | `ensemble_list_tasks` |
| Read one task in full (spec, agents, status, recent chat) | `ensemble_get_task` |
| Create a task | `ensemble_create_task` |
| Change a task's title, spec, priority, or assigned agents | `ensemble_update_task` |
| Launch a draft, or relaunch a stopped task | `ensemble_start_task` |
| Stop a running task (keeps everything) | `ensemble_stop_task` |
| Move a task to another project | `ensemble_move_task` |
| Delete a task permanently | `ensemble_delete_task` |

Task statuses: `draft` (created, never launched), `running`, `waiting_user`
(its agents asked the product owner and are paused), `paused` (turn limit
reached), `stopped`.

Task priorities: `highest`, `high`, `medium` (the default), `low`, `lowest` —
the product owner's ordering. `ensemble_create_task` and `ensemble_update_task`
take either the name or the number (1 = highest to 5 = lowest); anything else
is rejected. `ensemble_list_tasks` returns rows highest-priority first, and
most-recently-updated first inside one priority — so the top of the list is the
work that matters most. Priority is the owner's call: set one when they asked
for it, and otherwise leave the default alone.

## Scope and safety rules (enforced by the server, respect them anyway)

- **Read anywhere, write in your own project.** If your task belongs to a
  project, you can only create/amend/start/stop/move/delete tasks in that
  project. If your task has no project, you can write anywhere.
- **Never act on yourself.** You cannot stop, start, move or delete your own
  task. Finish by reporting to the product owner instead.
- **Stop before delete.** A running task cannot be deleted.
- **Stop before reassigning agents.** Who works a task can only be changed
  while it is not running — pass `agents` to `ensemble_update_task` on a draft
  or a stopped task. Give the *complete* new line-up, not just the change:
  anyone you leave out is removed. An agent that stays keeps its conversation
  and its access to the task; pass its `identity` (from `ensemble_get_task`) to
  be sure which one you mean, especially when two agents share a kind
  (`claude`, `claude-2`). The `model` is part of the assignment: changing only
  the model of an agent that is staying is a valid edit.
- **Prefer drafts.** `ensemble_create_task` creates a draft by default. Start
  tasks only when the product owner asked you to (or the brief clearly says so).
- **Prefer stop over delete.** Delete only drafts you created and no longer
  need, or when the product owner explicitly asks. Deleting removes the task
  record, chat and agent transcripts; the task folder and code are kept.

## Planning work for a project

When asked to plan (or when your role is `planner`):

1. `ensemble_whoami`, then `ensemble_list_tasks` — know what already exists so
   you do not duplicate or contradict live work. Read relevant tasks with
   `ensemble_get_task`.
2. Study the project itself: the code folder (`project.path`), the project home
   (`project.home`, notes and task folders), your own task folder.
3. Break the goal into tasks that are **independently workable**, small enough
   for one agent session, and ordered by dependency. Name dependencies in the
   spec ("depends on: <task title/id>") — Ensemble does not enforce them.
4. Create each with `ensemble_create_task` as a **draft**. Choose agents:
   - one agent (`[{"agent": "claude"}]`) for a task the product owner will
     drive interactively;
   - two agents with roles for autonomous work, typically
     `[{"agent":"claude","role":"engineer"},{"agent":"codex","role":"reviewer"}]`;
   - `workspace`: `empty` for research/writing tasks, `worktree` for code
     changes in a git project (own branch), `inplace` only when the product
     owner wants edits directly in the project folder.
5. Present the plan (titles, one-line purpose, order, suggested agents) to the
   product owner and wait for feedback. Amend with `ensemble_update_task`,
   remove drafts with `ensemble_delete_task`, start with `ensemble_start_task`
   when told to.

## Writing a good spec

The spec is the **entire briefing** the task's agent starts from — it will not
see your conversation. Write it in Markdown with:

- **Goal** — one paragraph, what done looks like.
- **Context** — where the code/notes live (paths), relevant decisions, links to
  related tasks by title/id.
- **Scope** — what is in and explicitly out.
- **Acceptance criteria** — checkable bullets (tests pass, file exists, endpoint
  returns X).
- **Constraints** — conventions, files not to touch, how to report back
  (for a collaboration: "send the product owner a summary with `chat_send
  to=\"user\"` when done").

Titles: short, imperative, unique within the project (they become folder names).

## Collaboration tools (only in multi-agent tasks)

`chat_send` hands off your turn (to a teammate, or `to="user"` to pause and ask
the product owner), `chat_read` reads new messages, `chat_whoami` shows the room
status. End every turn in a collaboration with a `chat_send`. These tools are
absent in a solo task: report to the human in your normal reply instead.

## Examples

Create a draft for an autonomous pair in your own project:

```json
{
  "title": "Add rate limiting to the public API",
  "spec": "# Goal\n…\n# Context\n…\n# Acceptance criteria\n- …",
  "agents": [{"agent": "claude", "role": "engineer"},
             {"agent": "codex", "role": "reviewer"}],
  "workspace": "worktree"
}
```

Amend a draft's spec after review:

```json
{"taskId": "room-1a2b3c4d", "spec": "# Goal\n(revised)…"}
```

Raise a task's priority (the name or the number — `2` means the same thing):

```json
{"taskId": "room-1a2b3c4d", "priority": "high"}
```

List everything running anywhere:

```json
{"projectId": "*", "includeStopped": false}
```
