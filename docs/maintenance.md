# Restart, update and backup

## What it is

The hub can restart its own process safely, update an installed build and back up project folders. A restart temporarily pauses sessions and brings them back; a code update changes the version running on the hub computer.

![Backup settings for the fictional project hub](images/settings-backup.png)

## How to maintain the hub

1. Open the avatar menu for **Update now** when an update is offered. Read the confirmation before applying it; the hub restarts after updating.
2. For a plain restart, use the hub's restart control or the platform service command (`./ensemble restart` on Linux/macOS, `.\ensemble.ps1 restart` on Windows).
3. Open **Settings** → **Backup of your projects** to choose a backup destination and check its status. Use **Back up now** for an immediate run.

Keep the project repository's own Git remote and your backup destination under your control. A backup is a separate copy of project files, not a substitute for reviewing agent changes.
