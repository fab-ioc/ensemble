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
| See slim task rows for your project (or another, or `"*"`) | `ensemble_list_tasks` |
| See which tasks need a human, and why | `ensemble_list_attention` |
| Read one task in full (spec, agents, status, latest report) | `ensemble_get_task` |
| Create a task | `ensemble_create_task` |
| Change a task's title, spec, priority, or assigned agents | `ensemble_update_task` |
| Launch a draft, or relaunch a stopped task (its owner is told to carry on) | `ensemble_start_task` |
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

Task reads are purpose-specific. `ensemble_list_tasks` returns only the fields
needed to scan the board; pass `detail: true` for spec/report previews, message
counts and the other full row metadata. `ensemble_get_task` includes no chat by
default; pass `messages` from 1 to 200 only when recent chat is needed. Its
`lastReport` is included in full without that opt-in.

The tool list is also role-specific. Owners and reviewers get the common read,
report and roadmap tools, plus `ensemble_update_task` only to move their own
task to `inreview`. A project's PO room and agents whose role is `planner` also
get project/task administration tools. A cached client calling an unavailable
tool is refused; it does not bypass the role check.

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
- **Be terse by default.** No progress narration; tool calls need no preamble.
  Reports: outcome, evidence and tests, files or commit, blocker or next
  decision. Do not repeat the spec. Concise English is the working language.
- **In a team, the owner reports.** That is the engineer, or the only agent.
  A reviewer tells the engineer, not the PO.
- Use `ensemble_update_task` to move your own task to `inreview` as well when
  you hand the work back.

**If you are the PO**, the hub also checks your project's tasks on a timer
(every 5 minutes by default) and wakes you with a `[digest]` only when
something changed: a task's status, board column or attention state, a new
report, new commits on its branch, or its work landing on `main`. No digest
means nothing changed, so you don't need to poll. The digest lists the tasks by
id; read one in full with `ensemble_get_task`. For each branch it says one of
three things: the work is merged into `main`, it has commits not yet on
`main`, or it has no commits yet.

**Merged work moves to Done by itself.** When you merge a task's branch into
`main`, the same check moves its card to Done, so you don't have to move it by
hand. It moves a card once per merge, and never one that someone moved after
the merge: if the owner drags a merged card out of Done, it stays where they
put it.

## Starting a task again

`ensemble_start_task` (or Start on the dashboard) on a task that has run before
resumes each agent's conversation where it stopped. A resumed session comes
back at an empty prompt, so once its terminal has settled the hub types one
line into the task's owner:

> [resumed] Your task was started again. Your spec may have changed while you
> were stopped: read it again with ensemble_get_task, then carry on from where
> you were; do not start over. Report with ensemble_report when you finish or
> are blocked.

The spec itself is never sent again: a session told its spec again redoes the
work. So when you change a stopped task's spec, the change reaches its owner
through that line. A reviewer on mention is not resumed, an agent added since
the last run starts fresh with its brief, and a project's PO does not get the
line (the rotation and the restart helper brief it).

Every agent the hub starts for a task runs without approval prompts, one-agent
tasks included: nobody watches a task's terminal. Only a past session someone
opens from the history to drive by hand keeps Codex's prompts.

Task sessions may also have RTK enabled by the hub. In a shell that is not transparently hooked, prefix noisy git, test, search, listing and log commands with `rtk` (for example `rtk git status`, `rtk pytest`, `rtk grep` or `rtk ls`); if a recovery hint names hidden output you need, run `rtk recall <hash> --full`. Do not install it globally or edit global agent configuration: the hub's `rtkForTasks` setting controls future task launches.

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
- **Wake it only for review work.** Mention a reviewer only for a commit to
  review or a specific question, never for a plan, acknowledgement, thanks or
  verdict restatement. Do not address or mention a sleeping reviewer to
  acknowledge, thank, or restate its verdict. Mention it again only with a new
  commit or evidence, an unresolved finding, or a specific new review question.
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
| `waiting_for_you` | it reported `completed` or asked a question, sent something to the user nobody answered, hit a permission, tool-approval or folder-trust prompt (Claude's or Codex's), or the collaboration paused at its hop limit. The report or message is in `quote` | a human answer |
| `stalled` | it was woken to do something, or started again and told to carry on, is not working, and never answered anyone. A one-agent task idle at its prompt has only finished its turn and is not stalled, unless it was started again and has done nothing since | a look, then a nudge or a restart |

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

## Running a project as its PO

The PO is one long-lived session per project (`poRoomId` in the project's
`project.json`). The product owner talks mainly to it, tasks report to it, and
it reports to the product owner. What keeps that cheap and reliable:

**Keep a living handover.** `PO-HANDOVER.md` in the project home holds what a
successor with no memory would need: how the product owner likes to work,
priorities and decisions with the why, what is running, what waits on whom,
and what you have promised. Update it whenever one of those changes, not only
when asked. Past the rotation limit (200k tokens by default) the hub asks you
to bring it up to date, then starts a fresh PO whose first prompt is to read it
and `ROADMAP.md`. Anything in neither file is lost. Never hand a task's spec to
a fresh session as an instruction: it redoes the work.

**The line-up of a task.** One owner does the work end to end, in its own
`worktree`. A reviewer is optional and runs only on mention (see *The reviewer
runs on mention*): add one where a second opinion is worth a review's cost
(money, safety, data, a shared contract), and have the owner ask for the review
before it reports. Every spec ends by telling the owner to report with
`ensemble_report` when finished or blocked — never `chat_send to="user"`, which
reaches nobody who is watching.

**Allocate with the allowance in view.** Claude and Codex have separate plan
allowances, and either can be the owner or the reviewer. Read
`ensemble_plan_usage` before you start work, and spread the load:

- Give the owner seat to the kind with more room left, and the reviewer, if
  any, to the other kind, so one task does not spend one allowance twice.
- When one kind passes the warning (80%), start new work on the other; past
  the alarm (95%), start nothing more on it.
- A Codex window with `trusted: false` is a floor, not a reading: Codex writes
  its usage only when one of its agents takes a turn. Codex doing work is what
  refreshes it.
- Choose the model for the job and name it in the line-up: Opus for ambiguous,
  design-heavy or risky work; Codex or a cheaper model for well-specified
  implementation; Fable only where it is known to do well.
- When you tell the product owner what you started, say who got each task and
  why whenever it is not the obvious choice.

**Closing a task.** When a task reports `completed`: read the whole report
(`ensemble_get_task`), review the diff, merge its branch into the project's
main branch with a merge commit, prove it the way the project proves changes
(its tests, a real start), push, then stop the task. The progress check sees
the merge and moves the card to Done. A Done card whose branch still has
commits not on main is not done: merge them, reopen the task with a reason, or
record in the handover why they are dropped.

**Wakes cost your whole conversation.** Reports and the `[digest]` wake you;
never poll. Keep turns short and the history small: hand large reading to a
subagent, and don't re-read what you already have.

### Bringing a project onto this model

For a project that ran the old way (standing reviewers, reports by chat, no
handover):

1. Write `PO-HANDOVER.md` and `ROADMAP.md` in the project home from the
   project's own plans and what you know. The roadmap is direction; the
   handover is state.
2. Every task not in Done: a line-up by the rules above with the allowance in
   view, `workspace: worktree` for code, and a spec that ends with
   `ensemble_report`. Amend drafts with `ensemble_update_task`.
3. Every Done task whose branch has commits not on main (the digest says so):
   merged, reopened with a reason, or recorded in the handover as dropped.
4. Report to the product owner with `ensemble_report`: what changed, what you
   decided for each task, and what needs them.

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
No Ensemble tool offers hub control. The hub refuses `/api/update` from
anyone but the Ensemble Dashboard PO and the dashboard's Update now button,
but the rest of this rule is not enforced by the machine, so it rests on you
keeping it.

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
   - for autonomous work, one owner (`"role": "engineer"`), plus a reviewer
     of the other kind when the work merits one — which kind takes which seat
     follows the allowance (see *Allocate with the allowance in view*);
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
