# Ensemble

Ensemble is a local dashboard for projects worked on by coding agents. A project has a product owner (PO) chat and a board of tasks; Claude Code and Codex can own tasks, review changes, report back and carry work across long sessions. The hub runs on your machine and serves a browser page you can open from another device on your tailnet.

![Paper-theme view of a fictional Recipe Box project](docs/images/overview.png)

## Why Ensemble

- **You act as the CEO.** Each project has a PO agent that plans the work, writes task specs, starts tasks, reads the owners' reports and merges finished branches. You set direction in the [PO chat](docs/po-chat.md); decisions come to you as [Inbox cards](docs/inbox.md) with choices or Yes/No.
- **The work lands in files you keep.** Plans, reports and design notes are written to the project's `Documents/` folder, with handovers and review logs beside them. [Open them rendered and comment on a passage](docs/files.md), and [back the whole folder up](docs/maintenance.md) to a Git remote of your own.
- **Long sessions do not get expensive.** A session that grows long writes a handover and a fresh one continues from it; reviewers start only when there is a commit to check. [How Ensemble saves tokens](docs/usage.md#how-ensemble-saves-tokens).
- **Checks cost nothing when nothing changed.** Progress checks compare task facts without a model call and wake the PO only on news; a [cheap reader](docs/usage.md#how-ensemble-saves-tokens) can take file searches off a task owner.
- **Work is paced across your plans.** [Weekly pacing](docs/usage.md#how-ensemble-saves-tokens) gives new tasks to the agent with allowance to spare and holds lower-priority work when both are running out.
- **Review code without leaving the hub.** A task's [Changes panel](docs/sessions.md#review-a-tasks-code-in-the-hub) shows its diff; click a line to comment and send the notes to the owner, from a desktop or a phone.
- **Claude Code and Codex today.** Both sit behind one agent interface; [adding another agent](docs/developer.md#adding-an-agent) starts there, and the guide lists what else still names these two.

## What you can do

- [Plan and track tasks on the board](docs/board.md), with priorities, drafts and workflow columns.
- [Follow a task from draft through review](docs/task-life.md), with owner reports and handovers.
- [Talk to a project's PO](docs/po-chat.md) in threads and track your points.
- [Answer decisions in the Inbox](docs/inbox.md) with choice and Yes/No cards.
- [Read and comment on project files](docs/files.md) and [open live task sessions](docs/sessions.md).
- [Set up a new hub with the first-run wizard](docs/setup-wizard.md), and revisit its choices in Settings.
- [Use it remotely](docs/remote-security.md), [watch plan usage](docs/usage.md), and [choose a theme](docs/settings.md).

## Quick start

Get a build from [Releases](https://github.com/fab-ioc/ensemble/releases), or clone the repository and run the platform installer:

```sh
git clone https://github.com/fab-ioc/ensemble.git
cd ensemble
./install-systemd.sh              # Linux: starts a user service
# ./ensemble start                 # macOS: starts the hub in the background
# .\ensemble.ps1 start            # Windows PowerShell
```

Open `http://127.0.0.1:8765` on the hub machine. On a new hub with no projects, the setup wizard opens. Install and sign in to Claude Code or Codex before starting agent work. See [Getting started](docs/getting-started.md) for platform details and your first project.

[User guide](docs/README.md) · [Contributor guide](docs/developer.md) · [MIT license](LICENSE)
