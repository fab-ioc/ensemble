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
# copy carries no quarantine attribute, and opens it. Run again, it replaces
# the installed app with this release, stopping a running Ensemble first and
# starting it again after. Nothing else on the machine is changed.
#
# The release workflow writes its version in place of @VERSION@.
#   ENSEMBLE_RELEASE_URL  where the release files are (default: the GitHub
#                         release of that version; the workflow's own test
#                         points it at a local folder)
#   ENSEMBLE_INSTALL_DIR  the folder to install into (default: see above)
#   ENSEMBLE_NO_OPEN=1    install only, do not start the app
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
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
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

# The copy goes next to the target first, so the swap is two renames.
NEW="$DEST/.$APP.new.$$"
OLD="$DEST/.$APP.old.$$"
rm -rf "$NEW"
ditto "$TMP/unpacked/$APP" "$NEW" || { rm -rf "$NEW"; die "could not copy the app into $DEST"; }
xattr -dr com.apple.quarantine "$NEW" 2>/dev/null || true

# A running Ensemble from this copy is stopped for the swap and started after.
hub_pids() {
  pgrep -f "$TARGET/Contents/MacOS/Ensemble" 2>/dev/null | while read -r pid; do
    case " $(ps -o args= -p "$pid" 2>/dev/null) " in *" --run "*) ;; *) echo "$pid" ;; esac
  done
}
plist="$HOME/Library/LaunchAgents/$LABEL.plist"
domain="gui/$(id -u)"
via_launchd=
if [ -f "$plist" ] && launchctl print "$domain/$LABEL" >/dev/null 2>&1 \
   && grep -q "$TARGET/Contents/MacOS/Ensemble" "$plist"; then
  launchctl bootout "$domain/$LABEL" >/dev/null 2>&1 || true
  via_launchd=1
fi
pids=$(hub_pids)
if [ -n "$pids" ] || [ -n "$via_launchd" ]; then
  say "Stopping the running Ensemble ..."
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
  i=0
  while [ -n "$(hub_pids)" ] && [ $i -lt 30 ]; do sleep 1; i=$((i + 1)); done
  pids=$(hub_pids)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
fi

if [ -e "$TARGET" ]; then
  mv "$TARGET" "$OLD" || { rm -rf "$NEW"; die "could not move the old $TARGET aside"; }
fi
if ! mv "$NEW" "$TARGET"; then
  [ -e "$OLD" ] && mv "$OLD" "$TARGET"
  rm -rf "$NEW"
  die "could not put the new app in $TARGET"
fi
rm -rf "$OLD"

if xattr -r "$TARGET" 2>/dev/null | grep -q com.apple.quarantine; then
  die "$TARGET still carries the quarantine attribute"
fi
say "Installed Ensemble $VERSION in $TARGET"

if [ -n "$via_launchd" ]; then
  launchctl bootstrap "$domain" "$plist" >/dev/null 2>&1 || open "$TARGET"
  say "Started again (it starts at sign-in)."
elif [ "${ENSEMBLE_NO_OPEN:-}" != 1 ]; then
  open "$TARGET"
  say "Opening Ensemble: the dashboard opens in your browser in a few seconds."
fi
