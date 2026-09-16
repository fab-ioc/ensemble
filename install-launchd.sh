#!/usr/bin/env bash
# Install the ensemble LaunchAgent so the server starts at login and
# restarts itself if it crashes.
#
# Usage:
#   ./install-launchd.sh           # install + load
#   ./install-launchd.sh uninstall # unload + remove
#   ./install-launchd.sh status    # show status
#
# Logs: ~/Library/Logs/ensemble.log
# Plist: ~/Library/LaunchAgents/com.ensemble.dashboard.plist
set -euo pipefail

LABEL="com.ensemble.dashboard"
DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$DIR/dashboard.py"
TEMPLATE="$DIR/com.ensemble.dashboard.plist.template"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/ensemble.log"
PORT="${ENSEMBLE_PORT:-8765}"

action="${1:-install}"

case "$action" in
  install)
    PYTHON="$(command -v python3)"
    if [[ -z "$PYTHON" ]]; then
      echo "error: python3 not found in PATH" >&2
      exit 1
    fi
    if [[ ! -f "$SCRIPT" ]]; then
      echo "error: $SCRIPT not found" >&2
      exit 1
    fi

    # Install the headless-PTY dependency (ptyprocess) for this python3.
    REQ="$DIR/requirements.txt"
    if [[ -f "$REQ" ]]; then
      echo "Installing Python dependencies…"
      "$PYTHON" -m pip install --user -r "$REQ" 2>/dev/null \
        || "$PYTHON" -m pip install --user --break-system-packages -r "$REQ" \
        || echo "warning: pip install failed — run '$PYTHON -m pip install -r $REQ' manually" >&2
    fi

    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

    # launchd starts the hub with a bare PATH, and the hub must find `claude`,
    # `codex`, `git` and `node` wherever this Mac has them (npm global, nvm,
    # Homebrew...): keep the PATH of the shell running the install.
    AGENT_PATH="$HOME/.local/bin:$PATH:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

    sed \
      -e "s|__PYTHON__|$PYTHON|g" \
      -e "s|__SCRIPT__|$SCRIPT|g" \
      -e "s|__DIR__|$DIR|g" \
      -e "s|__PORT__|$PORT|g" \
      -e "s|__LOG__|$LOG|g" \
      -e "s|__PATH__|$AGENT_PATH|g" \
      "$TEMPLATE" > "$PLIST"

    # Unload first if already loaded (safe to ignore failures)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true

    launchctl bootstrap "gui/$UID" "$PLIST"
    launchctl enable "gui/$UID/$LABEL"
    launchctl kickstart -k "gui/$UID/$LABEL"

    echo "Installed: $PLIST"
    echo "Logs:      $LOG"
    echo "URL:       http://127.0.0.1:$PORT"
    echo
    echo "Status:"
    launchctl print "gui/$UID/$LABEL" | grep -E '^\s*(state|pid|last exit code)' || true
    ;;

  uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled. Plist removed: $PLIST"
    ;;

  status)
    if [[ ! -f "$PLIST" ]]; then
      echo "Not installed (no $PLIST)"
      exit 0
    fi
    launchctl print "gui/$UID/$LABEL" | grep -E '^\s*(state|pid|last exit code|program|arguments)' || true
    echo
    echo "Recent log lines:"
    tail -n 10 "$LOG" 2>/dev/null || echo "  (no log yet)"
    ;;

  *)
    echo "Usage: $0 [install|uninstall|status]" >&2
    exit 2
    ;;
esac
