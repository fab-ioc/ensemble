# Getting started

## What it is

The hub is one program on your computer. It keeps its settings in `~/.ensemble`, creates projects under `~/EnsembleProjects`, and serves the dashboard at `http://127.0.0.1:8765`. The agents are the Claude Code and Codex programs already installed and signed in on that computer.

![A fictional project selected in the Paper-theme dashboard](images/overview.png)

## How to install and open it

1. Install Python, Git, and at least one agent CLI (`claude` or `codex`); sign in with that CLI.
2. Clone this repository. On Linux run `./install-systemd.sh`; on macOS run `./ensemble start`; in Windows PowerShell run `.\ensemble.ps1 start`.
3. Open `http://127.0.0.1:8765` on the hub computer. On Linux, `./install-systemd.sh status` checks the service; macOS uses `./ensemble status`; Windows uses `.\ensemble.ps1 status`.

## First run and your first project

1. On a new install with no projects, the [setup wizard](setup-wizard.md) opens when you load the dashboard. Check the agents, choose your preferences, and optionally name your first project. Existing installs do not open it automatically; use **Settings → Run setup again** whenever you want to revisit it.
2. On the final screen, choose **Create project** if you entered a name, or **Open board** to create one later. You can also use **Create** in the top bar to make a code or documents project.
3. Open the project and its PO conversation. Tell the PO what you want; it can create draft tasks with a spec, owner and reviewer.
4. Open **Board** to inspect the drafts, then start a task when its scope is ready. [The task life](task-life.md) explains the rest.
