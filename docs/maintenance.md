# Restart, update and backup

## What it is

The hub can restart its own process safely, update an installed build and back up project folders. A restart temporarily pauses sessions and brings them back; a code update changes the version running on the hub computer.

![Backup settings for the fictional project hub](images/settings-backup.png)

## How to maintain the hub

1. When an update is available, open the avatar menu and choose **What's new · Update available**. Choose **Install now** in the dialog. It explains whether working agents will be waited for before the hub updates and restarts.
2. For a plain restart of a Linux systemd installation, run `systemctl --user restart ensemble`. On macOS use `./ensemble restart`; on Windows use `.\ensemble.ps1 restart`.
3. Open **Settings** → **Backup of your projects** to set up a backup:
   - **Git remote (optional)**: an empty repository you own, such as a private GitHub repository (`git@github.com:you/ensemble-projects.git`). The hub signs in with this machine's SSH key or Git credential manager; it stores only the URL. Without a remote the hub still commits each backup to the projects folder's own Git history, but nothing leaves the machine.
   - **Every N minutes**: how often it runs (60 by default, at least 5).
   - Tick **enabled**: backup is off until you do. Use **Back up now** for an immediate run and read its status line.

   The backup covers the projects folder (each project's `Documents/`, roadmap, handovers and review logs, see [where the agents' work is kept](files.md#where-the-agents-work-is-kept)) and exports each task's chat. It leaves out code checkouts (`repo/` folders and any other folder that is its own Git repository, with or without a remote: back those up separately, for example by pushing them), symbolic links and junctions, files over 20 MB, and tool folders such as `node_modules/`, `.venv/` and agents' `.claude/` / `.codex/` state. Keep anything you need from those somewhere else.

Keep the project repository's own Git remote and your backup destination under your control. A backup is a separate copy of project files, not a substitute for reviewing agent changes.
