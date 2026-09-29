"""Where every Chrome that the tests and tools launch gets its profile.

Why: on Windows, Chrome with a new --user-data-dir tests whether the account's
password is blank by signing in with an empty one (``LogonUser(user, ".", "",
LOGON32_LOGON_INTERACTIVE)``, chrome/browser/password_manager/
password_manager_util_win.cc, ``CheckBlankPasswordWithPrefs``). For an account
with a password that is one failed sign-in (event 4625, chrome.exe) per launch;
a full suite run made enough of them to lock the account (10 in 10 minutes).

Chrome skips the probe when the profile's ``Local State`` holds
``password_manager.os_password_last_changed`` > 0 and not older than the
password's last change (NetUserGetInfo level 1: now - usri1_password_age), and
then uses ``password_manager.os_password_blank`` as the answer. After a real
probe Chrome itself writes "last change + 1 s" there.

So every launch still gets its own new, empty profile directory (nothing leaks
between tests: storage, service workers and caches start empty as before), but
the directory is seeded with a ``Local State`` holding exactly those two
values, computed here the way Chrome computes them:

* os_password_last_changed = the password's last change + 2 s (Chrome's own
  skew is 1 s; usri1_password_age is whole seconds);
* os_password_blank = false. The probe only fails when the password is not
  blank, so the only account it could lock is one this answer is right for.

Initialisation therefore never launches Chrome and costs no failed sign-in at
all, not even once. A password change moves the last change forward, and the
next seed follows it, so a change does not bring the probe back either. A
Chrome that stored these prefs under another name would probe again: the
check below cannot see that (it reads our file, not Chrome's behaviour). The
account's failed sign-in counter can, without elevation:
``([ADSI]"WinNT://./$env:USERNAME,user").BadPasswordAttempts.Value`` in
PowerShell goes up by one per unseeded launch (measured 2026-09-29) and stays
put across seeded ones; it resets once the lockout window (10 min) has passed,
so compare readings taken inside one window.

Use:

* Python launchers: ``new_profile(parent)`` returns a seeded directory for
  ``--user-data-dir``;
* node launchers: put ``JS`` in front of the script, pass ``**node_args()`` in
  its arguments, and call ``chromeProfile(A)`` for each launch.

Both check the seeded file before returning and raise if it lacks the value,
so no launch goes out with an unseeded profile. ``ENSEMBLE_NO_CHROME=1`` makes
``CHROME`` empty: the browser tests skip.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

CHROME = next((p for p in (
    os.environ.get("ENSEMBLE_CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "",
) if p and Path(p).exists()), "")
if os.environ.get("ENSEMBLE_NO_CHROME"):
    CHROME = ""

LOCAL_STATE = "Local State"
_EPOCH_1601 = 11644473600  # seconds from 1601-01-01 (Chrome's time base) to 1970-01-01


def password_age() -> int | None:
    """Seconds since this account's password was last set (NetUserGetInfo
    level 1, as Chrome reads it), or None where there is none to read."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes as w

    class UserInfo1(ctypes.Structure):
        _fields_ = [("name", w.LPWSTR), ("password", w.LPWSTR), ("password_age", w.DWORD), ("priv", w.DWORD),
                    ("home_dir", w.LPWSTR), ("comment", w.LPWSTR), ("flags", w.DWORD), ("script_path", w.LPWSTR)]

    size = w.DWORD(257)
    name = ctypes.create_unicode_buffer(size.value)
    if not ctypes.windll.advapi32.GetUserNameW(name, ctypes.byref(size)):
        return None
    info = ctypes.POINTER(UserInfo1)()
    user = name.value.rsplit("\\", 1)[-1]
    if ctypes.windll.netapi32.NetUserGetInfo(None, user, 1, ctypes.byref(info)) != 0:
        return None  # Chrome then assumes "not blank" and does not probe
    try:
        return int(info.contents.password_age)
    finally:
        ctypes.windll.netapi32.NetApiBufferFree(info)


def local_state() -> str:
    """The seed ``Local State`` text (see the module docstring)."""
    age = password_age()
    changed = time.time() - (age or 0) + 2
    return json.dumps({"password_manager": {
        "os_password_blank": False,
        # An int64 pref: Chrome stores it as a decimal string.
        "os_password_last_changed": str(int((changed + _EPOCH_1601) * 1_000_000)),
    }})


def check(profile: str | os.PathLike) -> None:
    """Raise unless ``profile`` holds a Local State that stops the probe."""
    try:
        pm = json.loads((Path(profile) / LOCAL_STATE).read_text(encoding="utf-8"))["password_manager"]
        ok = int(pm["os_password_last_changed"]) > 0 and pm["os_password_blank"] is False
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise RuntimeError(f"chrome_profile: {profile} has no password_manager.os_password_last_changed; "
                           "refusing to launch Chrome with it (Windows would log a failed sign-in)")


def new_profile(parent: str | os.PathLike | None = None) -> str:
    """A new, empty, seeded directory under ``parent`` for ``--user-data-dir``."""
    d = tempfile.mkdtemp(prefix="chrome-", dir=parent)
    (Path(d) / LOCAL_STATE).write_text(local_state(), encoding="utf-8")
    check(d)
    return d


def node_args() -> dict:
    """The arguments a node launcher needs: Chrome's path and the seed."""
    return {"chrome": CHROME, "chromeLocalState": local_state()}


# chromeProfile(A): a new seeded profile directory under A.tmp, checked; throws
# rather than hand back one without the value. A needs node_args() and tmp.
JS = r"""
function chromeProfile(A) {
  const fs = require('fs'), path = require('path');
  const dir = fs.mkdtempSync(path.join(A.tmp, 'chrome-'));
  const file = path.join(dir, 'Local State');
  fs.writeFileSync(file, A.chromeLocalState || '');
  let pm = null; try { pm = JSON.parse(fs.readFileSync(file, 'utf8')).password_manager; } catch (e) {}
  if (!pm || !(Number(pm.os_password_last_changed) > 0) || pm.os_password_blank !== false)
    throw new Error('chrome_profile: ' + dir + ' has no password_manager.os_password_last_changed; refusing to launch Chrome with it (Windows would log a failed sign-in)');
  return dir;
}
"""
