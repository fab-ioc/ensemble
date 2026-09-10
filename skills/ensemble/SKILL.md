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
and teammates, the **write scope** your tool calls are limited to, and your
project's PO (`projectPO`, with `reportsTo` saying in words where your reports
go). Then:

| Need | Tool |
|---|---|
| Report that you finished, are blocked, or need a decision | `ensemble_report` |
| See the projects | `ensemble_list_projects` |
| See the tasks of your project (or another, or `"*"`) | `ensemble_list_tasks` |
| See which tasks need a human, and why | `ensemble_list_attention` |
| Read one task in full (spec, agents, status, recent chat) | `ensemble_get_task` |
| Create a task | `ensemble_create_task` |
| Change a task's title, spec, priority, or assigned agents | `ensemble_update_task` |
| Launch a draft, or relaunch a stopped task | `ensemble_start_task` |
| Stop a running task (keeps everything) | `ensemble_stop_task` |
| Move a task to another project | `ensemble_move_task` |
| Delete a task permanently | `ensemble_delete_task` |
| Read a project's roadmap | `ensemble_get_roadmap` |
| Replace your project's roadmap | `ensemble_update_roadmap` |

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

## Reporting: `ensemble_report`

Each project can have a **PO**: one task whose agent runs the project for the
human. Every other task reports into it. Nobody watches your terminal, and your
final reply reaches only whoever happens to open it. Report through the tool:

| When | `kind` |
|---|---|
| The work is finished and handed back | `completed` |
| You cannot go on without help: a missing permission, a failing dependency, an unclear requirement | `blocked` |
| You need a decision before you continue | `question` |
| A milestone worth knowing, while you keep working | `update` |

The report is posted in the PO's room and **wakes the PO**. It is also recorded
on your task, so after `completed`, `question` or `blocked` the board shows your
task as waiting on a human, with your report quoted, not as stalled. If the
project has no PO, the report goes to the user instead.

- **Report once per event.** Every wake costs the PO its whole conversation
  again. Never send a second report just to be sure the first one arrived.
- **Make it self-contained.** The PO does not see your conversation. Say what
  was done or what is needed, where it is (branch, commit, paths), how you
  verified it, and what you left out.
- **In a team, the owner reports.** That is the engineer, or the only agent.
  A reviewer tells the engineer, not the PO.
- Use `ensemble_update_task` to move your own task to `inreview` as well when
  you hand the work back.

## Waking teammates (multi-agent tasks)

A chat message wakes as few agents as it can. A message to one participant
(`to`) wakes that participant. A message to everyone wakes only the task's
**owner**: the engineer, or a designer-and-engineer. A reviewer or other
specialist is woken only when you address it with `to`, or when the user, the
owner or the PO **@mentions** it by identity or role (`@codex`, `@reviewer`).
When you want a review, name the reviewer; otherwise it sleeps, and that is
the point.

### The reviewer runs on mention

A reviewer is not kept running. Every wake of a long-lived session re-sends
its whole conversation, and a resume reloads the same history, so each time a
reviewer is addressed or @mentioned the hub starts a **fresh reviewer session**
for that one review. Its first prompt is the task's spec, the branch and its
diff, the message that asked, and `REVIEW-LOG.md` from the task folder. The
board shows it as *on mention*, and as *reviewing now* while a review runs.

- **Asking for a review:** make the request self-contained. The reviewer sees
  the spec, the diff, your message and the review log, and nothing else of
  the chat. Say what to review, what changed since the last review and what
  you want checked.
- **As the reviewer:** read the log first and say, for each earlier finding,
  whether it is fixed, still open or no longer relevant. Then call
  `review_done` once with `verdict` (`approve`, `changes_requested` or
  `comment`), a one-line `summary` and your `findings`. The hub appends the
  review to `REVIEW-LOG.md`, sends it to whoever asked and to the project's
  PO, and ends your session. Don't send it with `chat_send` or
  `ensemble_report` as well.
- A message to the reviewer while a review is running reaches that same
  session. It is not a second review.

## Checking what needs a human

A task's `status` says what it is *meant* to be doing. It does not say whether
that is actually happening — an agent can die, run out of usage, or sit on a
permission prompt while its task still reads `running`. `ensemble_list_attention`
answers the other question, and every `ensemble_list_tasks` row carries the same
verdict in its `attention` field.

| State | What happened | What it usually needs |
|---|---|---|
| `agent_gone` | its terminal died and nobody asked it to — carries `exitCode` and the last lines it printed | relaunching, once you know why |
| `blocked` | still running, but it cannot continue: its own output shows a usage or credit limit or an expired login, or it reported `blocked` (`cause: "reported"`). The offending line or the report is in `quote` | waiting for a reset, the product owner logging in, or the help it asked for |
| `waiting_for_you` | it reported `completed` or asked a question, sent something to the user nobody answered, hit a permission prompt, or the collaboration paused at its hop limit. The report or message is in `quote` | a human answer |
| `stalled` | it was woken to do something, is not working, and never answered anyone. A one-agent task idle at its prompt has only finished its turn and is not stalled | a look, then a nudge or a restart |

Use it whenever you are asked what happened to work that was started, and
**before** planning follow-up work: a task that died at its usage limit is not a
task that needs re-specifying. Reporting what you found is the job — the tools
never restart or resume anything on their own, and neither should you without
being asked.

## Scope and safety rules (enforced by the server, respect them anyway)

- **Read anywhere, write in your own project.** If your task belongs to a
  project, you can only create/amend/start/stop/move/delete tasks in that
  project. If your task has no project, you can write anywhere.
- **Never act on yourself.** You cannot stop, start, move or delete your own
  task. Finish by reporting with `ensemble_report` instead.
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

## The project roadmap

Each project has one roadmap: `ROADMAP.md`, plain Markdown, in the project's
home folder (`project.home`). The product owner reads and edits it on the
project's **Roadmap** tab; the project's PO keeps it current through the tools.

- **If you are the project's PO, you own the roadmap**, alongside the
  project's documentation. When a task lands, a plan changes, or the product
  owner decides something about direction, update it. Keep it about what the
  project is for, what is next and why — not a copy of the task list.
- **Read before you write.** `ensemble_get_roadmap` returns the text and a
  `version`. Pass that version as `baseVersion` to `ensemble_update_roadmap`,
  with the **whole** new document. A roadmap that does not exist yet has
  version `""`; the first update creates the file.
- **A refused update is not an error to retry blindly.** If the product owner
  saved since you read it, the write is refused with `"error": "conflict"` and
  their current text. Merge your change into *their* text — their edit wins
  where you disagree — and try again with the new version.
- Only your own project's roadmap can be written; any project's can be read.
  Mention files by path (`docs/plan.md`) and they open in the file view.

## The hub is not yours to restart

The Ensemble hub (the `Ensemble` scheduled task, a `python`/`pythonw` process
serving port 8765) runs every agent on this machine, in every project.
Stopping it ends all of them mid-work. **Only the Ensemble Dashboard PO
(`room-8d56cd21`) may restart, stop or kill the hub** — that project is where
the hub itself is built. Everyone else, whatever their project or role:

- never stop, end or restart the `Ensemble` scheduled task;
- never kill `python`/`pythonw` processes you did not start yourself — kill
  your own by PID, never by name;
- never free port 8765, and never call the hub's `/api/update`;
- never do any of these indirectly (a script, another shell, an interpreter).

If you believe the hub needs a restart, stop and tell the product owner why.
No Ensemble tool offers hub control. This rule is not yet enforced by the
machine, so it rests on you keeping it.

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
  ("report with `ensemble_report` when you finish or are blocked"; for a
  team, say which agent owns the report).

Titles: short, imperative, unique within the project (they become folder names).

## Collaboration tools (only in multi-agent tasks)

`chat_send` hands off your turn (to a teammate, or `to="user"` to pause and ask
the product owner), `chat_read` reads new messages, `chat_whoami` shows the room
status. End every turn in a collaboration with a `chat_send`, and remember
that a message to everyone wakes only the owner (see *Waking teammates*).
These tools are absent in a solo task. There, `ensemble_report` is how
finished or blocked work reaches anyone.

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
