#!/usr/bin/env bash
# Install the ensemble systemd user unit (Linux) so the hub starts at login and
# restarts itself if it crashes: the counterpart of install-launchd.sh (macOS)
# and install-task.ps1 (Windows). The unit runs the hub from THIS clone, so
# the dashboard's Update now (git fetch + reset to the upstream) and a merge
# pushed to the upstream reach the hub; its state stays in ~/.ensemble.
#
# Usage:
#   ./install-systemd.sh           # install + (re)start (asks what to call a board)
#   ENSEMBLE_NOUN=Initiative ./install-systemd.sh   # the same, without asking
#                                  # (ENSEMBLE_NOUN_PLURAL for a word of your own)
#   ENSEMBLE_PORT=8766 ENSEMBLE_BIND=tailscale ./install-systemd.sh
#                                  # another port; listen on the tailnet too
#   ./install-systemd.sh uninstall # stop + disable + remove
#   ./install-systemd.sh status    # show status
#   ./install-systemd.sh --force   # install or uninstall even if a project's
#                                  # long-running app would stop (see below)
#
# Logs: ~/.ensemble/logs/ensemble.log (and `journalctl --user -u ensemble`)
# Unit: ~/.config/systemd/user/ensemble.service
#
# Keep the unit name: the restart helper stops and starts the hub through the
# unit that runs it. Only the unit file is written (and removed on uninstall):
# drop-ins in ensemble.service.d/ are kept, and a drop-in's Environment= wins
# over the unit's.
#
# Restarting or stopping the unit ends every process in its cgroup, so a
# project's long-running app an agent started from its terminal (setsid,
# nohup) is first moved into a systemd scope of its own (restart_guard.py)
# and runs on. One that cannot be moved refuses the restart (exit 3), naming
# it, unless --force is given.
set -euo pipefail

UNIT="ensemble.service"
ME="${USER:-$(id -un)}"
DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$DIR/dashboard.py"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT"
LOG="$HOME/.ensemble/logs/ensemble.log"
PORT="${ENSEMBLE_PORT:-8765}"
BIND="${ENSEMBLE_BIND:-}"

FORCE=""
args=()
for a in "$@"; do
  if [[ "$a" == "--force" ]]; then FORCE=1; else args+=("$a"); fi
done
action="${args[0]:-install}"

# Before the running unit stops: move a project's long-running processes out.
guard() {
  local py
  py="$(command -v python3 || true)"
  if [[ -z "$py" || ! -f "$DIR/restart_guard.py" ]] \
      || ! systemctl --user is-active --quiet "$UNIT" 2>/dev/null; then
    return 0
  fi
  "$py" "$DIR/restart_guard.py" --unit "$UNIT" --what "$1" ${FORCE:+--force} && return 0
  echo "refused: $1 would stop the processes above; stop them, or run again with --force" >&2
  exit 3
}

case "$action" in
  install)
    PYTHON="$(command -v python3 || true)"
    if [[ -z "$PYTHON" ]]; then
      echo "error: python3 not found in PATH" >&2
      exit 1
    fi
    if [[ ! -f "$SCRIPT" ]]; then
      echo "error: $SCRIPT not found" >&2
      exit 1
    fi
    if [[ ! -e "$DIR/.git" ]]; then
      echo "warning: $DIR is not a git clone: Update now will not find anything to update" >&2
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
      echo "error: systemctl not found (this script needs systemd)" >&2
      exit 1
    fi
    if ! command -v systemd-run >/dev/null 2>&1; then
      echo "error: systemd-run not found: the hub's restart and Update now need it to outlive the unit" >&2
      exit 1
    fi

    # Install the headless-PTY dependency (ptyprocess) for this python3.
    REQ="$DIR/requirements.txt"
    if [[ -f "$REQ" ]] && ! "$PYTHON" -c "import ptyprocess" 2>/dev/null; then
      echo "Installing Python dependencies…"
      "$PYTHON" -m pip install --user -r "$REQ" 2>/dev/null \
        || "$PYTHON" -m pip install --user --break-system-packages -r "$REQ" \
        || echo "warning: pip install failed — run '$PYTHON -m pip install -r $REQ' manually" >&2
    fi

    # The word the pages use for a board (the projectNoun setting): asked once
    # when someone is at the terminal, Project on Enter (then nothing is
    # written and the hub keeps what it has). Saved by the hub's own code.
    one="${ENSEMBLE_NOUN:-}"
    many="${ENSEMBLE_NOUN_PLURAL:-}"
    if [[ -z "$one" && -t 0 ]]; then
      echo
      echo "What should Ensemble call a board of tasks with its PO?"
      echo "  1) Project  2) Initiative  3) Epic  4) Workstream  - or type your own word"
      read -r -p "Word [Enter keeps the current one: Project on a new install]: " one || one=""
      one="$(echo "$one" | xargs)"
      if [[ -n "$one" && -z "$many" ]]; then
        case "$one" in 1|2|3|4|Project|Initiative|Epic|Workstream) ;;
          *) read -r -p "Plural [${one}s]: " many || many="" ;;
        esac
      fi
    fi
    if [[ -n "$one" ]]; then
      case "$one" in
        1|Project) one="Project"; many="Projects" ;;
        2|Initiative) one="Initiative"; many="Initiatives" ;;
        3|Epic) one="Epic"; many="Epics" ;;
        4|Workstream) one="Workstream"; many="Workstreams" ;;
      esac
      many="$(echo "$many" | xargs)"
      [[ -n "$many" ]] || many="${one}s"
      "$PYTHON" "$SCRIPT" --set-project-noun "$one" "$many" \
        || echo "warning: the word was not saved; choose it later in Settings" >&2
    fi

    # Refused before anything is written: the restart below would stop them.
    guard "the install's restart"

    mkdir -p "$UNIT_DIR" "$(dirname "$LOG")"

    # The user manager starts the hub with a bare PATH, and the hub must find
    # `claude`, `codex`, `git` and `node` wherever this machine has them (npm
    # global, nvm...): keep the PATH of the shell running the install.
    AGENT_PATH="$HOME/.local/bin:$PATH:/usr/local/bin:/usr/bin:/bin"

    BIND_LINE=""
    if [[ -n "$BIND" ]]; then
      BIND_LINE="Environment=\"ENSEMBLE_BIND=$BIND\""
    fi

    cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Ensemble hub
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
Environment="PATH=$AGENT_PATH"
$BIND_LINE
ExecStart="$PYTHON" "$SCRIPT" --port $PORT --log "$LOG"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable "$UNIT"
    # A hub already running (on the old code or another folder) is replaced.
    systemctl --user restart "$UNIT"

    echo "Installed: $UNIT_FILE"
    echo "Code:      $DIR"
    echo "Logs:      $LOG"
    echo "URL:       http://127.0.0.1:$PORT"
    if [[ -n "$BIND" ]]; then
      echo "Bind:      $BIND (ENSEMBLE_BIND)"
    fi
    if compgen -G "$UNIT_DIR/$UNIT.d/*.conf" >/dev/null; then
      kept=""
      for f in "$UNIT_DIR/$UNIT.d"/*.conf; do kept="$kept $(basename "$f")"; done
      echo "Drop-ins kept:$kept"
    fi
    if [[ "$(loginctl show-user "$ME" --property=Linger --value 2>/dev/null || true)" != "yes" ]]; then
      echo "To keep the hub running while you are logged out: loginctl enable-linger $ME"
    fi
    echo
    systemctl --user --no-pager status "$UNIT" | head -n 5 || true
    ;;

  uninstall)
    guard "the uninstall"
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload
    echo "Uninstalled. Unit removed: $UNIT_FILE"
    ;;

  status)
    if [[ ! -f "$UNIT_FILE" ]]; then
      echo "Not installed (no $UNIT_FILE)"
      exit 0
    fi
    systemctl --user --no-pager status "$UNIT" | head -n 12 || true
    echo
    echo "Recent log lines:"
    tail -n 10 "$LOG" 2>/dev/null || echo "  (no log yet)"
    ;;

  *)
    echo "Usage: $0 [install|uninstall|status] [--force]" >&2
    exit 2
    ;;
esac
