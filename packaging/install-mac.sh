#!/bin/sh
# Installs (or updates) Ensemble.app on a Mac from a GitHub release:
#
#   curl -fsSL https://github.com/fab-ioc/ensemble/releases/latest/download/install-mac.sh | sh
#
# The app has no Apple developer signature, so a copy downloaded with a
# browser is blocked by Gatekeeper. This script downloads the release zip over
# https, checks it against the release's SHA256SUMS.txt, checks the app's own
# (ad-hoc) signature, puts Ensemble.app in /Applications (or ~/Applications
# when /Applications is not writable without admin rights), makes sure that
# copy carries no quarantine attribute, opens it and waits until its hub
# answers (or says why it does not). Run again, it replaces
# the installed app with this release, stopping a running Ensemble first and
# starting it again after. Nothing else on the machine is changed.
#
# The release workflow writes its version in place of @VERSION@.
#   ENSEMBLE_RELEASE_URL  where the release files are (default: the GitHub
#                         release of that version; the workflow's own test
#                         points it at a local folder)
#   ENSEMBLE_INSTALL_DIR  the folder to install into (default: see above)
#   ENSEMBLE_NO_OPEN=1    install only, do not start the app
#   ENSEMBLE_REPLACE_OLD_HUB=1  an older Ensemble hub on port 8765 (one run
#                         from a source checkout, say) is stopped without
#                         asking, with the LaunchAgent that starts it
#   ENSEMBLE_WAIT_SECONDS how long to wait for the app to answer (default 30)
set -eu

VERSION="@VERSION@"
REPO="fab-ioc/ensemble"
APP="Ensemble.app"
LABEL="com.ensemble.dashboard"
ZIP="Ensemble-$VERSION-macos-universal.zip"
SUMS="SHA256SUMS.txt"

say() { printf '%s\n' "$*"; }
die() { printf 'Ensemble install: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this installs the Mac app; on Windows use the setup .exe from the release page"
case "$VERSION" in @*) die "this copy of the script has no version; use the one attached to a release" ;; esac

if [ -n "${ENSEMBLE_RELEASE_URL:-}" ]; then
  BASE="${ENSEMBLE_RELEASE_URL%/}"
  PROTO="=https,http,file"
else
  BASE="https://github.com/$REPO/releases/download/v$VERSION"
  PROTO="=https"
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/ensemble-install.XXXXXX")
TARGET= NEW= OLD= plist= domain=
via_launchd= was_running= placed= finished=

# Whether a whole bundle carries no quarantine attribute. An xattr that fails
# proves nothing, so it counts as quarantined.
unquarantined() {
  attrs=$(xattr -r "$1" 2>/dev/null) || return 1
  case "$attrs" in *com.apple.quarantine*) return 1 ;; esac
}

# Starts the app at TARGET the way it ran: through its LaunchAgent (and only
# that, so it keeps starting at sign-in), or opened.
start_app() {
  if [ -n "$via_launchd" ]; then
    launchctl bootstrap "$domain" "$plist" >/dev/null 2>&1 &&
      launchctl print "$domain/$LABEL" >/dev/null 2>&1
  else
    open "$TARGET"
  fi
}

# On any failure before the install is finished, whatever it had changed is put
# back: the new app leaves TARGET, the previous one (if any) returns there and,
# if it was running, is started again the way it ran (or at least opened).
on_exit() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ -z "$finished" ]; then
    if [ -n "$placed" ] && [ -e "$TARGET" ]; then
      mv "$TARGET" "$NEW" 2>/dev/null || rm -rf "$TARGET"
    fi
    if [ -n "$OLD" ] && [ -e "$OLD" ]; then
      mv "$OLD" "$TARGET" || echo "Ensemble install: the previous app is in $OLD" >&2
    fi
    if [ -n "$was_running" ] && [ -e "$TARGET" ]; then
      if start_app || open "$TARGET"; then
        echo "Ensemble install: the previous Ensemble was started again" >&2
      else
        echo "Ensemble install: could not start the previous Ensemble; open it from $TARGET" >&2
      fi
    fi
  fi
  if [ -n "$NEW" ]; then rm -rf "$NEW"; fi
  rm -rf "$TMP"
}
trap on_exit EXIT
trap 'exit 1' HUP INT TERM

fetch() { curl -fsSL --proto "$PROTO" --retry 3 -o "$2" "$BASE/$1" || die "could not download $BASE/$1"; }

say "Downloading Ensemble $VERSION ..."
fetch "$SUMS" "$TMP/$SUMS"
fetch "$ZIP" "$TMP/$ZIP"

expected=$(awk -v f="$ZIP" '{n=$2; sub(/^\*/, "", n)} n == f {print $1; exit}' "$TMP/$SUMS")
[ -n "$expected" ] || die "$ZIP is not listed in $SUMS; not installed"
actual=$(shasum -a 256 "$TMP/$ZIP" | awk '{print $1}')
[ "$actual" = "$expected" ] || die "the checksum of $ZIP does not match $SUMS; not installed"

mkdir "$TMP/unpacked"
ditto -x -k "$TMP/$ZIP" "$TMP/unpacked" || die "could not unpack $ZIP"
[ -x "$TMP/unpacked/$APP/Contents/MacOS/Ensemble" ] || die "no $APP in $ZIP"
codesign --verify --deep --strict "$TMP/unpacked/$APP" 2>/dev/null \
  || die "the app's signature does not verify; not installed"

# After the install: whether the app can have its port, and whether it answers.
PORT=8765
LOG="$HOME/Library/Logs/ensemble.log"
WAIT="${ENSEMBLE_WAIT_SECONDS:-30}"
not_started() {
  printf 'Ensemble install: Ensemble %s is installed in %s but was not started: %s
' "$VERSION" "$TARGET" "$*" >&2
  exit 1
}
wait_hub() {
  i=0
  while [ $i -lt "$WAIT" ]; do
    curl -fs --max-time 2 "http://127.0.0.1:$PORT/api/version" 2>/dev/null | grep -q "\"$VERSION\"" && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}
not_answering() {
  {
    echo "Ensemble install: Ensemble $VERSION was started but does not answer on http://127.0.0.1:$PORT/ after $WAIT s."
    st=$("$TARGET/Contents/MacOS/Ensemble" --port-status --port "$PORT" 2>/dev/null | sed -n 's/^message=//p') || st=
    case "$st" in "Port $PORT is free."|"") ;; *) echo "$st" ;; esac
    echo "Its log is $LOG; the last lines:"
    tail -n 20 "$LOG" 2>/dev/null | sed 's/^/  /' || echo "  (no log)"
  } >&2
  exit 1
}
# For a hub this script did not start with the app's own browser opening (one
# already serving, one started by its LaunchAgent with --background).
open_dashboard() {
  if open "http://127.0.0.1:$PORT/"; then
    say "Opened the dashboard in your browser: http://127.0.0.1:$PORT/"
  else
    printf 'Ensemble install: Ensemble is running, but the browser did not open: open http://127.0.0.1:%s/ in your browser.
' "$PORT" >&2
    exit 1
  fi
}
# Before opening the app: the port is free, or held by an Ensemble hub it can
# use, or by an older Ensemble hub that is stopped only when the person says so
# (asked at the terminal, or ENSEMBLE_REPLACE_OLD_HUB=1). Anything else on the
# port is never stopped: the person is told what it is.
check_port() {
  exe="$TARGET/Contents/MacOS/Ensemble"
  status=$("$exe" --port-status --port "$PORT" 2>/dev/null) || return 0
  state=$(printf '%s
' "$status" | sed -n 's/^state=//p')
  msg=$(printf '%s
' "$status" | sed -n 's/^message=//p')
  case "$state" in
    free|"") return 0 ;;
    other) not_started "$msg" ;;
    ensemble)
      v=$(printf '%s
' "$status" | sed -n 's/^version=//p')
      newest=$(printf '%s
%s
' "$v" "$VERSION" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
      if [ "$v" = "$VERSION" ] || [ "$newest" = "$v" ]; then
        say "Ensemble $v already serves port $PORT: opening it."
        finished=1
        open_dashboard
        exit 0
      fi ;;
  esac
  say "$msg"
  answer=
  if [ "${ENSEMBLE_REPLACE_OLD_HUB:-}" = 1 ]; then
    answer=y
  elif ( : </dev/tty ) 2>/dev/null; then
    printf 'Stop that hub (and the sign-in service that starts it) and start Ensemble %s? [y/N] ' "$VERSION" >/dev/tty
    read -r answer </dev/tty || answer=
  fi
  case "$answer" in
    y|Y|yes|YES|Yes)
      "$exe" --stop-old-hub --port "$PORT" | sed 's/^/  /' || true
      st=$("$exe" --port-status --port "$PORT" 2>/dev/null | sed -n 's/^state=//p') || st=
      [ "$st" = free ] || not_started "the older hub did not stop; see above"
      ;;
    *)
      not_started "stop that hub first, or run the install again and let it stop the hub:
  curl -fsSL https://github.com/$REPO/releases/latest/download/install-mac.sh | ENSEMBLE_REPLACE_OLD_HUB=1 sh" ;;
  esac
}

# Where: an existing install is updated where it is.
if [ -n "${ENSEMBLE_INSTALL_DIR:-}" ]; then
  DEST="$ENSEMBLE_INSTALL_DIR"
elif [ -d "/Applications/$APP" ] && [ -w /Applications ]; then
  DEST=/Applications
elif [ -d "$HOME/Applications/$APP" ]; then
  DEST="$HOME/Applications"
elif [ -w /Applications ]; then
  DEST=/Applications
else
  DEST="$HOME/Applications"
fi
[ -d "/Applications/$APP" ] && [ "$DEST" != /Applications ] && [ -z "${ENSEMBLE_INSTALL_DIR:-}" ] \
  && die "/Applications/$APP is there but this account cannot replace it; run this from an administrator account"
mkdir -p "$DEST"
TARGET="$DEST/$APP"

# The copy goes next to the target first, so the swap is two renames, and it is
# checked there before anything running is stopped.
NEW="$DEST/.$APP.new.$$"
OLD="$DEST/.$APP.old.$$"
ditto "$TMP/unpacked/$APP" "$NEW" || die "could not copy the app into $DEST"
xattr -dr com.apple.quarantine "$NEW" 2>/dev/null || true
unquarantined "$NEW" || die "could not make sure the new app carries no quarantine attribute; not installed"
codesign --verify --deep --strict "$NEW" 2>/dev/null || die "the copied app's signature does not verify; not installed"

# A running Ensemble from this copy is stopped for the swap and started after.
hub_pids() {
  pgrep -f "$TARGET/Contents/MacOS/Ensemble" 2>/dev/null | while read -r pid; do
    case " $(ps -o args= -p "$pid" 2>/dev/null) " in *" --run "*) ;; *) echo "$pid" ;; esac
  done
}
plist="$HOME/Library/LaunchAgents/$LABEL.plist"
domain="gui/$(id -u)"
if [ -f "$plist" ] && grep -q "$TARGET/Contents/MacOS/Ensemble" "$plist" \
   && launchctl print "$domain/$LABEL" >/dev/null 2>&1; then
  say "Stopping the running Ensemble ..."
  launchctl bootout "$domain/$LABEL" >/dev/null 2>&1 || true
  i=0
  while launchctl print "$domain/$LABEL" >/dev/null 2>&1 && [ $i -lt 30 ]; do sleep 1; i=$((i + 1)); done
  launchctl print "$domain/$LABEL" >/dev/null 2>&1 \
    && die "could not stop Ensemble's sign-in service ($LABEL); nothing was changed"
  via_launchd=1 was_running=1
fi
pids=$(hub_pids)
if [ -n "$pids" ]; then
  [ -n "$was_running" ] || say "Stopping the running Ensemble ..."
  was_running=1
  kill $pids 2>/dev/null || true
  i=0
  while [ -n "$(hub_pids)" ] && [ $i -lt 30 ]; do sleep 1; i=$((i + 1)); done
  pids=$(hub_pids)
  if [ -n "$pids" ]; then
    kill -9 $pids 2>/dev/null || true
    sleep 2
  fi
  [ -z "$(hub_pids)" ] || die "the running Ensemble did not stop; nothing was changed"
fi

if [ -e "$TARGET" ]; then
  mv "$TARGET" "$OLD" || die "could not move the old $TARGET aside; nothing was changed"
fi
mv "$NEW" "$TARGET" || die "could not put the new app in $TARGET; nothing was changed"
placed=1
unquarantined "$TARGET" || die "could not make sure the installed app carries no quarantine attribute; nothing was changed"
codesign --verify --deep --strict "$TARGET" 2>/dev/null ||
  die "the installed app's signature does not verify; nothing was changed"

if [ -n "$was_running" ]; then
  # Until it runs again, a failure still puts the previous app back.
  start_app || die "could not start the new Ensemble the way it ran; nothing was changed"
  finished=1
  say "Installed Ensemble $VERSION in $TARGET and started it again."
  rm -rf "$OLD" 2>/dev/null || say "Note: could not remove the previous app at $OLD"
  OLD=
  wait_hub || not_answering
  say "Ensemble $VERSION is running: http://127.0.0.1:$PORT/"
  # Opened, the app opens the browser itself; its LaunchAgent starts it
  # without (--background).
  [ -z "$via_launchd" ] || [ "${ENSEMBLE_NO_OPEN:-}" = 1 ] || open_dashboard
else
  finished=1
  say "Installed Ensemble $VERSION in $TARGET"
  rm -rf "$OLD" 2>/dev/null || say "Note: could not remove the previous app at $OLD"
  OLD=
  if [ "${ENSEMBLE_NO_OPEN:-}" != 1 ]; then
    check_port
    open "$TARGET" || not_started "could not open it: open Ensemble from $TARGET"
    say "Starting Ensemble ..."
    wait_hub || not_answering
    say "Ensemble $VERSION is running: the dashboard opens in your browser (http://127.0.0.1:$PORT/)."
  fi
fi
