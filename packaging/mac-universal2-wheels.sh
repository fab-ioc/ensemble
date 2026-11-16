#!/bin/sh
# Installs cryptography and cffi as universal2 into the Mac build's Python, so
# Ensemble.app can notify on Apple silicon and Intel Macs alike.
#
#   packaging/mac-universal2-wheels.sh venv/bin/python
#
# pip on an Apple silicon runner takes the arm64-only wheels, and the build's
# check that every Mach-O in the bundle is universal2 then fails (or, without
# that check, the app could not notify on an Intel Mac). cryptography
# publishes a universal2 wheel; cffi (its _cffi_backend) does not, so its
# arm64 and x86_64 wheels of one version are merged into one (delocate).
# The versions are pinned: both cffi wheels must be of the same release.
set -eu

PY="${1:?the Python to install into}"
CRYPTOGRAPHY="48.0.1"
CFFI="2.0.0"
DELOCATE="0.13.0"

W=$(mktemp -d "${TMPDIR:-/tmp}/ensemble-wheels.XXXXXX")
trap 'rm -rf "$W"' EXIT

pyver=$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
dl() { # dl <platform> <requirement> <dir>
  "$PY" -m pip download --disable-pip-version-check --quiet --only-binary=:all: --no-deps \
    --implementation cp --python-version "$pyver" --platform "$1" -d "$3" "$2"
}

"$PY" -m pip install --disable-pip-version-check --quiet "delocate==$DELOCATE"
dl macosx_10_9_universal2 "cryptography==$CRYPTOGRAPHY" "$W/u2"
dl macosx_11_0_arm64 "cffi==$CFFI" "$W/arm64"
dl macosx_10_13_x86_64 "cffi==$CFFI" "$W/x86_64"
"$(dirname "$PY")/delocate-merge" "$W"/arm64/cffi-*.whl "$W"/x86_64/cffi-*.whl -w "$W/u2"

"$PY" -m pip install --disable-pip-version-check --force-reinstall --no-deps "$W"/u2/*.whl
for so in $("$PY" -c 'import _cffi_backend, cryptography.hazmat.bindings._rust as r; print(_cffi_backend.__file__, r.__file__)'); do
  archs=$(lipo -archs "$so")
  echo "$so: $archs"
  case "$archs" in *x86_64*arm64*|*arm64*x86_64*) ;; *) echo "not universal2: $so" >&2; exit 1 ;; esac
done
