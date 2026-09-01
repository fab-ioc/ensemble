# Ensemble

A local, multi-agent collaboration & coordination dashboard for coding agents — [Claude Code](https://claude.com/claude-code) and Codex today, any agent tomorrow (each is a pluggable adapter). One browser tab that surfaces every live and historical session across agents, runs them headless, lets multiple agents collaborate in a shared room, and gives one-click controls to open, resume, fork, theme, label, categorise, pin, archive, search, track cost, and (optionally) link Jira tickets — backed by the data each agent already writes to disk. The server binds to `127.0.0.1` only; no network exposure.

Cross-platform: **macOS** (iTerm2) and **Windows** (Windows Terminal) are supported today; Linux terminal control is stubbed and coming. Everything except the terminal-driving bits (session list, history, transcripts, labels, categories, pins, archive, search, cost, Jira, repo/editor opening, folder opening) works everywhere. All OS-specific behavior lives behind a platform backend in `backends/`, selected by `sys.platform`.

![dashboard screenshot](docs/screenshot.png)

## What it does

**Browsing**

- **Live + history** in one searchable table. Live sessions stay pinned at the top; history sorts by recency. Busy sessions get an orange blinker.
- **Search** — instant filter as you type; press Enter to grep across all transcripts.
- **Filter chips** — Live · 📌 Pinned · History · 🗄 Archived (multi-select).
- **Sidebar** — Views (All / Uncategorized) and your own **Categories** (drag-drop sessions in).
- **Detail panel** — click a row for cwd, first/last turn, full-conversation toggle, cost, and action buttons.

**Session actions**

- **Click a live row** → focuses the matching terminal tab (macOS/iTerm only; elsewhere opens the transcript).
- **▶ Open** a historical session → new terminal window with `claude --resume <sid>`, restoring the saved theme (and, on macOS, the window position).
- **⛔ Close** a live session → macOS saves window geometry then kills claude; Windows kills the process.
- **+ New** → creates `~/cs/NN_<slug>/`, opens a new terminal window, runs `claude "<your prompt>"` (optional `--model` picker).
- **🍴 Fork** → copies the transcript into a sibling workspace (git worktrees where possible) and resumes there; original untouched.
- **✎ Rename / ✨ Auto** → label manually or have Claude propose a name from the conversation (also becomes the terminal tab title on macOS).
- **🎨 Theme** → macOS: any `~/.claude/iterm-presets/*.itermcolors`, applied live. Windows: any Windows Terminal color scheme, applied at the next Open.
- **📂 Finder / Explorer** → opens the session's actual cwd (resolved via `lsof` on macOS if the recorded path is stale).
- **🧠 IDE** → opens the session's git repo (or `.idea` project) in its preferred editor, auto-detected by language.
- **📌 Pin / 🗄 Archive** → keep important sessions on top, hide finished ones.
- **💲 Cost** → per-session token cost, estimated from a built-in Anthropic pricing table.

**Jira (opt-in)** — auto-detects tickets from labels, cwd names, pasted `…atlassian.net/browse/PTECH-X` URLs, and tickets created via the Atlassian MCP tool; manual link/unlink in the detail panel. Off unless configured (see Configuration).

**Self-update** — when the install dir is a git checkout, a banner offers *Update now* (git fetch/reset + restart). Hidden for zip installs.

## Requirements

Common:
- Python 3.10+
- [Claude Code](https://docs.claude.com/en/docs/claude-code) — `claude` must be on PATH

macOS:
- [iTerm2](https://iterm2.com/) (terminal control is AppleScript-driven)

Windows:
- [Windows Terminal](https://aka.ms/terminal) (`wt.exe` on PATH)
- The `py` launcher (ships with python.org installers) — the Microsoft Store
  Python alias is detected and skipped

Optional everywhere: an IDE for the 🧠 IDE button (auto-detected per language).

### Platform support

| Feature | macOS | Windows |
|---|---|---|
| Session list / history / transcripts / labels | ✅ | ✅ |
| Search, categories, pin, archive, cost, detail panel | ✅ | ✅ |
| Jira integration (opt-in) | ✅ | ✅ |
| Self-update banner (git checkouts only) | ✅ launchd | ✅ Task Scheduler |
| Open / Fork (new window, `claude --resume`) | ✅ | ✅ |
| Close (kill session) | ✅ saves window position | ✅ kills process |
| New session (`~/cs/NN_<slug>`) | ✅ | ✅ |
| Open folder | ✅ Finder | ✅ Explorer |
| Open in editor | ✅ | ✅ |
| Focus the live terminal tab | ✅ | ❌ (no tty→tab mapping) |
| Per-session terminal theme | ✅ live | ⏳ applied at next Open (`--colorScheme`) |
| Consolidate / split terminal windows | ✅ iTerm | ❌ (no WT equivalent) |

Windows themes come from your Windows Terminal color schemes (read from its
`settings.json`); Windows Terminal can't re-theme a running tab, so a chosen
scheme is saved and applied the next time you Open the session.

## Install (macOS)

Four steps. The whole thing takes about 30 seconds.

### 1. Clone the repo into `~/.ensemble`

```sh
git clone https://github.com/fab-ioc/agent-ensemble.git ~/.ensemble
```

### 2. Put the CLI on your PATH

```sh
mkdir -p ~/.local/bin
ln -s ~/.ensemble/ensemble ~/.local/bin/ensemble
```

Make sure `~/.local/bin` is on your `PATH` (most shells already have it; otherwise add `export PATH="$HOME/.local/bin:$PATH"` to your `~/.zshrc` or `~/.bashrc`). Verify:

```sh
which ensemble       # should print the symlink path
```

### 3. Start the server

Manual (foreground or one-shot):

```sh
ensemble start       # starts detached on port 8765
ensemble status      # verify it's running
ensemble open        # opens http://127.0.0.1:8765 in your default browser
```

### 4. (Optional) Autostart at login

Recommended — survives reboots, restarts on crash, no need to ever `start` it again:

```sh
~/.ensemble/install-launchd.sh
```

That installs a LaunchAgent at `~/Library/LaunchAgents/com.ensemble.dashboard.plist` and starts it immediately. To check / uninstall later:

```sh
~/.ensemble/install-launchd.sh status
~/.ensemble/install-launchd.sh uninstall
```

### First-time iTerm permission prompt

The first time the dashboard tries to control iTerm (focus a tab, apply a theme, open a session), macOS will prompt:

> "Python wants access to control 'iTerm'."

Click **Allow**. You can review/change this later in **System Settings → Privacy & Security → Automation**. Without this permission, the AppleScript-based features (focus, themes, open/close) won't work — but the dashboard view itself will still display all sessions correctly.

### Updating

```sh
cd ~/.ensemble && git pull
~/.ensemble/install-launchd.sh   # re-runs to pick up plist changes; safe to repeat
```

## Install (Windows)

### 1. Clone the repo

```powershell
git clone https://github.com/fab-ioc/agent-ensemble.git "$env:USERPROFILE\.ensemble"
cd "$env:USERPROFILE\.ensemble"
```

### 2. Start the server

```powershell
.\ensemble.ps1 start     # starts detached on port 8765
.\ensemble.ps1 status    # verify it's running
.\ensemble.ps1 open      # opens http://127.0.0.1:8765 in your browser
```

If PowerShell blocks the script, allow local scripts for your user once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The server runs windowless via `pythonw.exe` (no console pops up), logging to
`%USERPROFILE%\.ensemble\logs\ensemble.log`.

### 3. (Optional) Autostart at logon

```powershell
.\install-task.ps1               # registers a Scheduled Task and starts it
.\install-task.ps1 status        # show task state + recent log
.\install-task.ps1 uninstall     # remove it

# Optionally enable Jira integration at install time:
.\install-task.ps1 -JiraBase "https://your-org.atlassian.net/browse/" -JiraPrefixes "PTECH,PLAT"
```

The task runs in your interactive session (so it can open Windows Terminal) and
restarts on crash. State and logs live under `%USERPROFILE%\.ensemble\`.

### Health check

```powershell
.\ensemble.ps1 doctor    # green/red check of python, claude, wt.exe, server, task
```

## CLI

macOS / Linux:

```
ensemble {start|stop|restart|status|logs|open|doctor}
```

Windows (PowerShell):

```
.\ensemble.ps1 {start|stop|restart|status|logs|open|doctor} [-Port N]
```

State lives in `~/.ensemble/`:
- `labels.json` · `categories.json` · `pinned.json` · `archived.json` · `parents.json` — your session metadata
- `settings.json` — preferences (open mode, default model, Jira config)
- `geometries.json` — saved iTerm window bounds per session (macOS)
- `favorite_themes.json` — starred themes
- `jira_links.json` / `jira_unlinks.json` — manual Jira link overrides
- `server.pid` — when running manually (not under launchd/Task Scheduler)

Logs: `~/Library/Logs/ensemble.log` (macOS) · `~/.ensemble/logs/ensemble.log` (Windows).

## Configuration

- `ENSEMBLE_PORT` — port to bind (default `8765`)
- `ENSEMBLE_PERMISSION_MODE` — permission mode for launched sessions (default `bypassPermissions`; set to `acceptEdits`, or empty to omit the flag)
- `ENSEMBLE_IJ_APP` — macOS only; application name for IntelliJ (default `IntelliJ IDEA`)

**Jira integration is opt-in** and off unless configured. Precedence: `~/.ensemble/settings.json` then environment. In `settings.json`:

```json
{ "jiraEnabled": true, "jiraBase": "https://your-org.atlassian.net/browse/", "jiraPrefixes": ["PTECH", "PLAT"] }
```

or via env: `ENSEMBLE_JIRA_BASE` and `ENSEMBLE_JIRA_PREFIXES` (comma-separated). On Windows, `install-task.ps1 -JiraBase … -JiraPrefixes …` writes these for you. When no base is set, the whole Jira UI is hidden.

Editor choice per language is configurable for any platform via `~/.ensemble/editors.json` (e.g. `{"python": "code", "rust": "code"}`). On macOS the values are app names; on Windows/Linux they are launcher commands resolved on PATH.

## How it works

- Reads live session state from `~/.claude/sessions/<pid>.json` (Claude Code's own per-process metadata).
- Reads transcripts from `~/.claude/projects/<slug>/<session-id>.jsonl`.
- All OS-specific behavior lives behind a platform backend in `backends/` (selected by `sys.platform`):
  - **macOS** (`backends/macos.py`) — controls iTerm via `osascript` (AppleScript): finds sessions by tty, sets colors, focuses windows, opens/closes windows, reads/writes bounds.
  - **Windows** (`backends/windows.py`) — opens Windows Terminal (`wt.exe`) running `claude` via a one-shot PowerShell launcher; process checks/termination use the Win32 API; folders open in Explorer.
  - **Linux** (`backends/linux.py`) — process + desktop (`xdg-open`) work; terminal control is not wired up yet.
- Stores its own state (labels, geometries, favorites) in `~/.ensemble/`.

The server binds to `127.0.0.1` only — no network exposure. On macOS, the OS will prompt for Automation permission the first time the python process tries to send Apple Events to iTerm; approve it.

## Acknowledgements

Inspired by the `claude-sessions` (`cs`) CLI script that ships with my personal Claude Code setup — same data sources, web frontend.

## License

MIT — see [LICENSE](LICENSE).
