# Contributor guide

## Architecture

The hub is a Python HTTP server in `dashboard.py`. It keeps project, task and settings state under `~/.ensemble` and provides the browser pages and the `ensemble` MCP endpoint. `chatroom.py` stores collaboration messages; `ensemble_tools.py` applies role checks to task and project tools. The front end is in `index.html`, `session.html` and `fileview.html`, with shared code in `static/`. Agent adapters and operating-system terminal backends are in `agents/` and `backends/`.

The repository keeps these root modules in place because tests, packaging and imports use them. See [session action notes](session-actions.md) for the actions shared by session and file views.

## Layout of the repository


| Path | What it is |
|---|---|
| `dashboard.py` | The hub: HTTP server, API, MCP endpoint, task launching, settings |
| `index.html` | The main page: projects, board, task panel, settings |
| `session.html` | A task's or PO's chat page |
| `fileview.html` | The file viewer that links open in |
| `static/` | Scripts the pages share (syntax highlighting, comments), the Dock library, the web manifest and app icons |
| `chatroom.py` | Task rooms: participants, messages, who a message wakes |
| `ensemble_tools.py` | The `ensemble_*` MCP tools and their role checks |
| `digest.py` | The PO's progress check |
| `rotation.py` | Handovers and fresh sessions past the token limit |
| `notify.py`, `webpush.py` | Notifications on your devices: what notifies, the devices, Web Push encryption and signing |
| `due.py` | What a handover's `## Due` section names for a time, typed into its idle agent then |
| `attention.py` | Needs you: blocked, stalled, dead or waiting tasks |
| `agent_hooks.py`, `agent_hook.py` | What each Claude agent says it is doing, through its Claude Code hooks; `attention.py` reads it before the agent's screen |
| `usage.py`, `usage_statusline.py` | Plan allowance readings for Claude and Codex |
| `backup.py` | Backup of `~/EnsembleProjects` to a git remote |
| `legacy_install.py` | Moving from claude-dashboard: copies its state into `~/.ensemble` at startup |
| `history.py` | A documents project's file history |
| `task_numbers.py`, `message_refs.py`, `workspace_search.py`, `peer_process.py` | Task numbers (`#18`, `ED-18`), links to messages, Workspace search, caller detection |
| `agents/` | Adapters for Claude Code and Codex |
| `backends/` | Per-OS code (Windows, macOS, Linux) and the headless terminal runner |
| `skills/` | The two agent skills |
| `tests/` | The test suite |
| `tools/` | Maintenance tools and the documentation screenshot builder |
| `docs/` | The user guide and reproducible Paper-theme screenshots |
| `ensemble.ps1`, `install-task.ps1`, `restart-hub.ps1` | Windows: start and stop, autostart task, restart helper |
| `ensemble`, `install-launchd.sh`, `com.ensemble.dashboard.plist.template` | macOS: start and stop, LaunchAgent |
| `install-systemd.sh` | Linux: the systemd user unit (`ensemble` starts and stops there too) |
| `restart_guard.py` | Linux: moves a project's long-running processes out of the hub's cgroups before its unit stops |
| `requirements.txt` | `pywinpty` / `ptyprocess` |
| `ensemble_app.py`, `app_launch.py`, `app_version.py`, `app_update.py`, `app_setup.py` | The built app: its entry, start, version, Update now from GitHub Releases, agent check and start at sign-in |
| `packaging/`, `.github/workflows/release.yml` | The PyInstaller build, the Windows installer (Inno Setup) and the release workflow |
| `LICENSE` | MIT |

## Adding an agent

Claude Code and Codex are two implementations of one agent interface, `AgentType` in `agents/base.py`. It is kept apart from the operating-system terminal layer in `backends/`: `backends/` decides *where* a session runs (a terminal window on Windows, macOS or Linux, or the headless runner), `agents/` decides *what* runs in it. A backend never needs to know which CLI it hosts.

An agent class says:

| Member | What it gives the hub |
|---|---|
| `key`, `display_name` | The kind's name in seats, line-ups and labels |
| `installed()` | Whether the CLI is on this machine |
| `list_sessions(limit)` | The CLI's own past sessions, as `AgentSession` rows, for session history and adopting a session |
| `launch_argv(cwd, prompt, extra)` | The command that starts a new session |
| `resume_argv(session_id, extra)` | The command that resumes a session by its id |
| `ensure_trusted(cwd)` (optional) | Clears the CLI's first-run trust prompt so an unattended launch does not wait on it |

Register the class in `_REGISTRY` in `agents/__init__.py`; `/api/agents` and `get_agent()` then know it.

That is the start, not the whole job. Today these parts of the hub name Claude and Codex directly, and a third kind needs code in each:

- **Launching a hub agent** (`dashboard.py`): the `ensemble` MCP server is wired per CLI (a `--mcp-config` file for Claude, `-c mcp_servers…` for Codex), and so are the model and effort flags and the sign-in check.
- **Models**: `agent_models.py` (`KINDS`) and the model pickers in Settings → Agent models.
- **Usage and pacing**: `usage.py` reads each CLI's plan allowance in its own way, and `plan_weights.py`, `pacing.py` and the owner choice in `rotation.py` (`_OTHER_KIND`) balance work between exactly these two kinds.
- **Idle and attention**: `attention.py` and `agent_hooks.py` recognise each CLI's prompt and status (Claude through its hooks, Codex through its terminal).
- **Pages**: the agent pickers and labels in `index.html` and `session.html`.

## Run the tests

From the repository root, run:

```sh
python3 -m unittest discover -s tests
```

On Windows, use `py -m unittest discover -s tests`. The suite uses the Python standard library. Page JavaScript tests need Node on `PATH`; some tests need Git. The test suite uses fake agents and must not launch real ones.

## Refresh the guide screenshots

Install Chrome or Chromium, Node 20+ and Pillow (`python3 -m pip install Pillow`). Then run from the repository root:

```sh
python3 tools/doc_shots/run.py
```

This creates a fresh fictional hub under `/tmp`, on port 8791, with stand-ins for `claude`, `codex` and `tailscale`. It never uses the live hub or a real agent account. Headless Chrome uses port 9341. Override occupied ports with `--port` and `--chrome-port`. The command rebuilds every `docs/images/*.png` shot and checks that each page has the Paper theme and contains none of the known personal-data markers. The images are palettized and optimized with Pillow. Review a contact sheet or the images themselves after UI changes, then run `python3 -m unittest tests.test_readme.Readme.test_relative_links_and_images_resolve_to_tracked_files` to check links.

## Releasing

Set `VERSION` in `app_version.py`, merge it into main, then push a matching `v<version>` tag. `.github/workflows/release.yml` builds the app artifacts and publishes a release with checksums. To build locally, install `pyinstaller` and `requirements.txt`, then run `pyinstaller --noconfirm packaging/ensemble.spec`. Consult the release workflow before changing an installer or a platform script.
