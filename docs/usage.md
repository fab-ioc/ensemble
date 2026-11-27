# Usage and pacing

## What it is

The top bar's plan chip shows the hub's latest Claude and Codex allowance readings. The hub uses them when selecting a task's owner and reviewer from the preferred line-up, while preserving a human choice to keep particular agents.

![Open Plan allowance tray with the demo usage windows](images/usage-plan.png)

## How to read it

1. Find **Plan** in the top bar. Open it for the available windows and their reset times. A missing reading is shown as unavailable, not as zero usage.
2. Before starting a task, read its preferred line-up and the allocation reason on the task. The hub may swap Claude and Codex if one is close to its pace limit.
3. To keep a particular task on the agents you chose, use **Keep these agents** in that task's menu. It holds while those agents are installed and below their usage alarm; if the chosen one reaches the alarm and the other is below it, the hub still switches.

## How Ensemble saves tokens

Every model call re-sends the whole conversation, so a long session costs more with each turn. The hub keeps sessions short and wakes agents only when there is work:

- **Handovers.** When a task owner's conversation reaches 200,000 tokens, the hub asks the owner to write `TASK-HANDOVER.md` and a fresh session carries on from it, on the same task and branch. The PO does the same at 150,000 tokens with `PO-HANDOVER.md` and `ROADMAP.md`. Change the limits in **Settings** → **Agent models** → **Handover** (**Hand a task session over at**, **Hand the PO over at**; at least 100,000, 0 turns it off). An owner that keeps working about 30,000 tokens past the ask is normally handed over by the hub; one that has just written its handover gets another 30,000 to finish, and one waiting at a prompt for you is left alone. A PO is never cut off mid-turn, and it is not replaced until its handover file exists; the hub keeps asking.
- **Reviewers on demand.** A reviewer starts only when the owner sends it a commit to review, as a fresh session that reads the spec, the diff and the review log, then stops after its verdict.
- **Progress checks without a model call.** Every few minutes the hub compares each task's facts (status, column, commits, blockers) with what the PO was last told. If nothing changed, nobody is woken and no model runs. If something changed, a cheap model (Haiku) writes the PO a short digest.
- **A cheap reader for searches.** With **Settings** → **Preferences** → **Command output** → **Cheap reader for task owners** on, a task owner can hand "where is X, how does Y work" sweeps to a Haiku helper that can only read and search files, and gets back short file-and-line answers. It is off by default, is offered to task owners only, and applies from each session's next launch. A Codex owner gets it too when Claude Code is installed.
- **Plans draw themselves.** With **Settings** → **Preferences** → **Plans** → **Plan picture** on (the default), the hub keeps one page per project, `PO Plan.html` in its Documents folder, drawn from the board: the plan's tasks as boxes in order, gates as arrows (met or not yet), and each task's state, owner, priority, points and days. It is drawn when a PO or planner creates a second task within ten minutes or sets or clears a gate, and redrawn when a task on it changes state. No model is called. The PO's tool result names the page; nothing is posted in chat. A plan leaves the page once all its tasks are done; the last finished plan stays under "Earlier". Off: nothing is drawn.
- **Weekly pacing.** With **Settings** → **Agent models** → **Weekly pacing** (**Pace each agent's week**, on by default), the hub reads each plan's projected week and can give a new task's ownership to the other agent when the preferred one has clearly less allowance to spare. A small difference keeps the preferred line-up, and so does **Keep these agents** on a task, unless the kept agent has reached its usage alarm and the other has not. When both agents are forecast to run past their week, new work of medium priority or lower waits until there is room.
