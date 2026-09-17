"""Plan-allowance readings for the agent kinds that run on this machine.

Answers one question the per-session cost chip cannot: **how much of the
subscription's allowance is gone, and when does the window reset.** That number
is *account-wide* — every task on this machine burns the same windows — so it
belongs in the board header, never on a task card, where a percentage would read
as caused by that task.

Two sources, deliberately not symmetrical:

``read_claude()``
    Two readings, the newer wins:

    * **Local, preferred** — :func:`read_claude_statusline`. Claude Code hands
      its status-line command ``rate_limits`` (5-hour and 7-day
      ``used_percentage`` / ``resets_at``) after each turn; hub-launched agents
      run ``usage_statusline.py`` as that command, which keeps the newest in a
      file. No network, no credentials. A last-write snapshot like Codex's, so
      the same age and rollover guards apply. While it is current the endpoint
      is not called at all.
    * **Fallback** — :func:`read_claude_endpoint`,
      ``GET https://api.anthropic.com/api/oauth/usage`` with the OAuth access
      token Claude Code keeps in ``~/.claude/.credentials.json``.
      **Undocumented** — an internal endpoint that can change or vanish without
      notice. The stored token expires in ~3 hours and Claude Code refreshes it
      in place, so we re-read the file on every call rather than implementing
      an OAuth refresh. It **rate-limits** — on 2026-09-11 it refused every call
      for four hours — so it is asked at most every
      :data:`ENDPOINT_MIN_INTERVAL_S`, a 429 stops the calls for as long as
      ``Retry-After`` says (doubling on repeats), and a refusal serves its last
      good reading with that reading's age instead of going "unavailable".

``read_codex()``
    A Codex plan has several **pools**, each an allowance of its own: the main
    one (``limit_id`` "codex"), a reserve that only the model ``gpt-reserve``
    draws on, and a model's own (Spark's). They are read and shown apart, and
    the pool Codex agents run on (:func:`codex_pool_for_model`) is the one
    that alarms and the one allocation judges. Two readings, the newer wins
    per pool:

    * **Preferred** — :func:`read_codex_app_server`: ``codex app-server``
      answers ``account/rateLimits/read`` with every pool, used or not. One
      short-lived child process per reading, killed after a hard timeout.
    * **Fallback** — the newest ``token_count`` event of each pool in the
      Codex rollout JSONLs, when ``codex`` is missing, not logged in, too old
      to know the method, or slow. **A last-write snapshot, not a feed** — the
      rollout is appended only when that agent takes a turn, so a reading's
      age is unbounded and it freezes exactly when an agent stops, which is
      the condition we most want to notice. Two guards, both mandatory, both
      below: carry the age and stop trusting a reading once it has gone old
      *relative to that window's own length*, and treat a window whose
      ``resets_at`` has passed as rolled over rather than reporting its dead
      value.

Both normalise to one window vocabulary (``five_hour`` / ``seven_day`` /
``seven_day_model``) so the chip, the endpoint and the MCP tool share one render
path and one reader.

Thresholds are driven off ``percent`` only, never off the payload's ``severity``
string: the investigation only ever observed ``"normal"``, so branching on the
escalated values would be guessing.

**Credential hygiene.** The access token is read into a local, placed in one
request header, and never returned, cached, logged or interpolated into an error
message. :func:`_scrub` is a second line of defence over every error string that
reaches the cache.

Decoupled from ``dashboard.py`` the same way ``backup.py`` is: this module holds
the readers, the cache and the scheduler; the server just serves
:func:`snapshot`.
"""
from __future__ import annotations

import copy
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# Written by usage_statusline.py, the status-line command of hub-launched
# Claude agents; read by read_claude_statusline.
CLAUDE_STATUSLINE_FILE = Path.home() / ".ensemble" / "usage" / "claude-rate-limits.json"

# The bearer token is the only thing the endpoint requires; the investigation
# measured that the beta header and a spoofed User-Agent change nothing. The
# beta header is kept only because it is what Claude Code itself sends. No
# User-Agent spoof: it bought nothing and hardcoded a version string that rots.
_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "Accept": "application/json",
}

HTTP_TIMEOUT = 10          # well inside the poll interval: a hung request must
                           # not eat a whole cycle
REFRESH_INTERVAL_S = 90    # windows move in whole percents; faster buys nothing

# How old a reading may be before that window stops counting as current.
# Scaled to the window's own length rather than fixed: 30 minutes against the
# 300-minute window is a tenth of it and worth flagging, but the same 30 minutes
# against the 10080-minute weekly window is 0.3% out of date and essentially
# exact. A single flat threshold would grey out the *most* reliable number on
# the chip, permanently, because Codex only writes when an agent takes a turn.
STALE_FRACTION = 0.1
STALE_FLOOR_S = 300        # never call a reading stale in under 5 minutes
STALE_FALLBACK_S = 1800    # when the window's length is unknown

# Below this we don't bother qualifying the reading with its age.
AGE_QUALIFY_S = 60

# Rollouts to scan for a populated record. Newest-first, stopping at the first
# file that has one — so this is a bound on the pathological case, not a cost.
# It must be generous: ~1 record in 100 has null windows and they cluster per
# agent, so a handful of files can plausibly all be empty while a good record
# sits just past them.
CODEX_MAX_FILES = 60

# A reset time that could not belong to its record is not a reset time — most
# likely the field changed meaning (a duration rather than an epoch, say).
# Reporting "rolled over" off a misparse would blank the chip confidently and
# for ever, so an implausible value fails the whole source loudly instead. See
# _reset_plausible: judged against the record's own time and the window's own
# length. RESET_SANITY_S is the reach allowed when the length is unknown.
RESET_SANITY_S = 60 * 86400
RESET_SLACK_S = 86400

# Limit buckets reported together are written about a millisecond apart. One
# whose newest record trails the newest overall by more than this has stopped
# being reported — a model no longer in use, a plan that has changed — and its
# windows are no longer a statement about now.
BUCKET_TOGETHER_S = 300

# Codex's pools. The main one is the account's weekly (and, on some plans,
# five-hour) allowance; every model draws on it except the ones below.
CODEX_MAIN_POOL = "codex"
# The reserve: a weekly allowance of its own that only the model slug
# `gpt-reserve` draws on. The app server names it (`limitName` "gpt-reserve");
# a session record does not — a turn on `gpt-reserve` writes `limit_id`
# "codex" like any other, with the reserve's numbers — so the session reader
# tells the two apart by the session's model.
CODEX_RESERVE_POOL = "base_model_inference"
CODEX_RESERVE_MODEL = "gpt-reserve"
# Spark: a model with a five-hour and a weekly allowance of its own.
CODEX_SPARK_POOL = "codex_bengalfox"
CODEX_SPARK_MODEL = "gpt-5.3-codex-spark"
# The only models known to draw on a pool of their own. A pool Codex adds
# later is read and shown under its name, but no model is *judged* by it until
# it is listed here: a slug that merely looks like a pool's name stays on the
# main pool.
_CODEX_MODEL_POOLS = {CODEX_RESERVE_MODEL: CODEX_RESERVE_POOL,
                      CODEX_SPARK_MODEL: CODEX_SPARK_POOL}

# `codex app-server` answered in about a second when measured (2026-09-17).
# The whole reading, its kill included, stays under 15 s: this long to answer,
# then CODEX_APP_KILL_S, counted from the same start, to be gone.
CODEX_APP_TIMEOUT_S = 12
CODEX_APP_KILL_S = 3
# After a reading that failed — no `codex`, not logged in, an older Codex
# without the method, a timeout — the session records serve, and the app
# server is not started again for this long.
CODEX_APP_RETRY_S = 600
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_codex_app_retry_at = 0.0
_codex_app_last_good: dict | None = None   # {at, pools} of its last good answer
_codex_app_why = ""                        # why it last failed, in the user's terms

WARN_PERCENT = 80          # banner
ALARM_PERCENT = 95         # banner, louder

# The usage endpoint rate-limits — observed live, an HTTP 429 after a burst of
# calls. It is an undocumented internal endpoint and we are a guest on it, so a
# 429 stops us calling rather than being absorbed and retried on the next tick.
# Honour `Retry-After` when the response carries one; otherwise wait this long.
DEFAULT_BACKOFF_S = 600
# Cap on our own doubling, never on a longer wait the server asked for.
MAX_BACKOFF_S = 3600
# Consecutive refusals double the wait. One 429 answered with `Retry-After` is
# the server saying what it wants, and inventing a ladder on top of that would
# be second-guessing it — but a *second* refusal after we honoured the first
# says the hint was not enough, which is new information. The asymmetry settles
# it: waiting too long costs a chip for an hour, waiting too little risks an
# undocumented endpoint being closed to this machine with no warning and no
# appeal. Any success resets the ladder.
_claude_backoff_until = 0.0
_claude_consecutive_429 = 0
# The endpoint is only the fallback now (see read_claude), and even as that
# it is asked at most this often: a plan window moves slowly, a remembered
# reading carries its age, and on 2026-09-11 it refused every call for four
# hours at the old 90-second pace.
ENDPOINT_MIN_INTERVAL_S = 600
# A remembered endpoint reading is served, aged, while the endpoint refuses —
# for at most a week, the longest window it describes.
CACHED_MAX_AGE_S = 7 * 86400
_claude_last_call = float("-inf")  # when the endpoint was last asked
_claude_last_good: dict | None = None  # {at, entries} of its last good answer
_claude_last_why = ""                # its last refusal, in the user's terms
_claude_endpoint_calls = 0           # requests sent since the hub started

# Claude's `limits[]` kinds -> our shared vocabulary. Anything else is ignored,
# which is what keeps an endpoint change from breaking the surface.
_CLAUDE_KINDS = {
    "session": ("five_hour", "5h"),
    "weekly_all": ("seven_day", "7d"),
    "weekly_scoped": ("seven_day_model", "7d"),
}

# The statusline's `rate_limits` keys -> (kind, label, window minutes).
_STATUSLINE_KINDS = {
    "five_hour": ("five_hour", "5h", 300),
    "seven_day": ("seven_day", "7d", 10080),
}

# Codex windows by length in minutes. Never by slot: which window sits in
# `primary` depends on the plan (see read_codex).
_CODEX_KINDS = {
    300: ("five_hour", "5h"),
    10080: ("seven_day", "7d"),
}

_LOCK = threading.Lock()
_STATE: dict = {
    "state": "loading",     # loading | ready
    "checkedAt": 0.0,
    "sources": {},          # {"claude": {...}, "codex": {...}}
}


# ---------------------------------------------------------------------------
# Credential hygiene
# ---------------------------------------------------------------------------

def _scrub(text: str, *secrets: str) -> str:
    """Remove any secret that somehow reached an error string.

    Nothing in this module puts a token into a message, so this should never
    fire — it exists so that a change upstream (a library that echoes a request
    header, say) cannot leak one into the cache, the endpoint or a log.
    """
    out = str(text)
    for s in secrets:
        if s and len(s) >= 8:
            out = out.replace(s, "<redacted>")
    return out[:300]


def _access_token() -> str:
    """The OAuth access token Claude Code stores on disk.

    Claude Code refreshes it in place, so re-reading the file each poll keeps it
    fresh for as long as some session runs. Raises with a human-readable reason
    (never containing the token) when it cannot be used.
    """
    try:
        with open(CREDENTIALS, encoding="utf-8") as fh:
            creds = (json.load(fh) or {}).get("claudeAiOauth") or {}
    except FileNotFoundError:
        raise LookupError("Claude Code is not logged in on this machine")
    except (OSError, ValueError) as e:
        raise LookupError(f"cannot read Claude Code's stored login ({type(e).__name__})")
    token = creds.get("accessToken")
    if not token:
        raise LookupError("Claude Code's stored login has no access token")
    expires_at = creds.get("expiresAt")
    if expires_at and expires_at / 1000 < time.time():
        raise LookupError(
            "Claude Code's login token has expired — it refreshes itself when a "
            "Claude session runs")
    return token


# ---------------------------------------------------------------------------
# Shared shape
# ---------------------------------------------------------------------------

def _stale_after(window_minutes) -> float:
    """How old a reading of *this* window may be before it stops being current.

    A tenth of the window, floored at 5 minutes. See ``STALE_FRACTION``.
    """
    try:
        minutes = float(window_minutes)
    except (TypeError, ValueError):
        return STALE_FALLBACK_S
    # A window has to be a plausible length. Anything outside a minute-to-a-
    # month gets the fixed fallback rather than a threshold derived from it —
    # otherwise a garbage value could make *every* reading count as current.
    if not (1 <= minutes <= 31 * 24 * 60):
        return STALE_FALLBACK_S
    return max(STALE_FLOOR_S, STALE_FRACTION * minutes * 60)


def _window(kind: str, label: str, *, percent=None, stale_percent=None,
            rolled_over: bool = False, reset_unknown: bool = False,
            window_minutes=None, resets_at=None,
            model: str | None = None, age_seconds=None,
            pool: str | None = None) -> dict:
    # Trust is per window, not per source: the same reading can be current for
    # the weekly window and out of date for the five-hour one.
    stale_after = _stale_after(window_minutes)
    # An unknown age is not a fresh one: a reading we cannot date is not current.
    # Neither is one whose reset time we could not verify — the rollover guard
    # could not run on it, so it must never be presented as a live number.
    trusted = (age_seconds is not None and age_seconds < stale_after
               and not reset_unknown)
    return {
        "kind": kind,
        "label": label,
        "model": model,
        # Codex only: the pool this window belongs to (its limit id), and
        # whether Codex agents currently run on that pool (read_codex sets it).
        # `model` stays None for the main pool, so a page that reads the
        # `model: null` windows as the account's keeps working.
        "pool": pool,
        "inUse": None,
        "windowMinutes": window_minutes,
        # `percent` is the number that may be shown as current. When a window
        # has rolled over it is None and the dead value is preserved separately,
        # so a caller cannot render a stale reading by accident.
        "percent": percent,
        "stalePercent": stale_percent,
        "rolledOver": rolled_over,
        # True when the reading carried no reset time, so we cannot tell whether
        # this window has already rolled. `percent` is withheld either way.
        "resetUnknown": reset_unknown,
        "resetsAt": resets_at,
        "ageSeconds": age_seconds,
        "trusted": bool(trusted),
        "staleAfterS": int(stale_after),
    }


def _unavailable(source: str, reason: str) -> dict:
    return {
        "source": source,
        "state": "unavailable",
        "error": reason,
        "planType": None,
        "asOf": None,
        "ageSeconds": None,
        "trusted": False,
        "windows": [],
    }


# ---------------------------------------------------------------------------
# Claude — live account-wide plan windows
# ---------------------------------------------------------------------------

def _retry_after_seconds(exc, now: float | None = None) -> float:
    """The `Retry-After` header as seconds, at least a minute. Falls back to the
    default.

    Both forms HTTP allows: delta-seconds and an HTTP-date. Anything unreadable
    falls back, which is the safe direction (we wait, we do not hammer). There
    is no upper cap: a server that asks for two hours gets two hours, and the
    statusline file covers the gap.
    """
    try:
        raw = str((exc.headers or {}).get("Retry-After")).strip()
    except (AttributeError, TypeError):
        return DEFAULT_BACKOFF_S
    try:
        wait = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            wait = when.timestamp() - (time.time() if now is None else now)
        except (TypeError, ValueError, IndexError):
            return DEFAULT_BACKOFF_S
    # `float` also reads "inf", "nan" and "1e309": an endless wait would stop
    # the endpoint for good and break the minutes arithmetic downstream.
    if not math.isfinite(wait):
        return DEFAULT_BACKOFF_S
    return max(60.0, wait)


def _reset_epoch(value):
    """A reset time as epoch seconds: ``(epoch, "ok")``, ``(None, "missing")`` or
    ``(None, "bad")``. The statusline gives an epoch, the endpoint an ISO string."""
    if value is None:
        return None, "missing"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), "ok"
    if isinstance(value, str):
        at = _iso_to_epoch(value)
        return (at, "ok") if at is not None else (None, "bad")
    return None, "bad"


def _minute_iso(epoch: float) -> str:
    """A reset time as ISO-8601, to the nearest minute.

    The two Claude sources state the same reset differently — the statusline
    ``1789293000``, the endpoint ``...T09:59:59.880051+00:00`` — and the board
    treats a changed ``resetsAt`` as a new window (it re-shows a dismissed
    banner). Rounding makes the same window read the same from either source.
    """
    return datetime.fromtimestamp(round(epoch / 60) * 60, timezone.utc).isoformat()


def _claude_source(entries, *, at: float, now: float, via: str,
                   live: bool) -> dict:
    """Claude windows in the shared shape, from ``(kind, label, minutes, percent,
    reset, model)`` entries read at ``at``.

    A ``live`` reading (the endpoint, this very poll) is taken as it comes. Any
    other — the statusline file, or an endpoint reading kept from an earlier
    poll — goes through the same two guards Codex readings do: its age decides
    ``trusted`` per window, and a window whose reset has passed, or whose reset
    time is missing, keeps no current ``percent``.
    """
    age = max(0, int(now - at))
    windows = []
    for kind, label, minutes, percent, reset, model in entries:
        epoch, how = _reset_epoch(reset)
        if how == "bad" or (epoch is not None and not live
                            and not _reset_plausible(epoch, at, minutes)):
            return _unavailable(
                "claude", f"Claude's {via} reading carries a reset time that is "
                          f"not a plausible date — its format has probably changed")
        rolled = bool(not live and epoch is not None and epoch < now)
        reset_unknown = not live and how == "missing"
        withheld = rolled or reset_unknown
        windows.append(_window(
            kind, label,
            percent=None if withheld else percent,
            stale_percent=percent if withheld else None,
            rolled_over=rolled,
            reset_unknown=reset_unknown,
            window_minutes=minutes,
            resets_at=_minute_iso(epoch) if epoch is not None else reset,
            model=model,
            age_seconds=age,
        ))
    if not windows:
        return _unavailable("claude", f"Claude's {via} reading carries no recognisable windows")
    return {
        "source": "claude",
        "state": "ok",
        "error": None,
        # Where the numbers came from: "statusline" (hub-launched Claude agents
        # write what Claude Code shows its status line), "endpoint" (fetched
        # this poll) or "endpoint-cached" (an earlier endpoint reading, kept
        # while the endpoint refuses).
        "via": via,
        "note": None,
        "planType": None,
        "asOf": at,
        "ageSeconds": age,
        "trusted": all(w["trusted"] for w in windows),
        "windows": windows,
    }


# --- Local: the statusline file -------------------------------------------

def read_claude_statusline(now: float | None = None, path=None) -> dict:
    """The newest plan windows a hub-launched Claude agent saw.

    Claude Code hands its status-line command a JSON that carries
    ``rate_limits.five_hour`` / ``seven_day`` (``used_percentage``,
    ``resets_at``) from its own latest API response. Hub-launched agents run
    ``usage_statusline.py`` as that command, which keeps the newest reading in
    :data:`CLAUDE_STATUSLINE_FILE`. No network, no credentials — but, like a
    Codex rollout, a last-write snapshot: it only moves while some hub agent
    takes turns, so it carries its age and goes untrusted per window.
    """
    now = time.time() if now is None else now
    path = CLAUDE_STATUSLINE_FILE if path is None else Path(path)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return _unavailable(
            "claude", "no hub-launched Claude agent has reported its limits yet")
    except (OSError, ValueError) as e:
        return _unavailable(
            "claude", _scrub(f"cannot read the Claude agents' limits file ({type(e).__name__})"))
    at = raw.get("asOf") if isinstance(raw, dict) else None
    limits = raw.get("rateLimits") if isinstance(raw, dict) else None
    if (not isinstance(at, (int, float)) or isinstance(at, bool)
            or not isinstance(limits, dict)):
        return _unavailable("claude", "the Claude agents' limits file is not in a known format")
    entries = []
    for key, (kind, label, minutes) in _STATUSLINE_KINDS.items():
        win = limits.get(key)
        if not isinstance(win, dict):
            continue
        entries.append((kind, label, minutes, _as_percent(win.get("used_percentage")),
                        win.get("resets_at"), None))
    return _claude_source(entries, at=float(at), now=now, via="statusline", live=False)


# --- Fallback: the usage endpoint ------------------------------------------

def _endpoint_entries(payload: dict) -> list[tuple]:
    entries = []
    for limit in (payload.get("limits") or []):
        mapped = _CLAUDE_KINDS.get(limit.get("kind"))
        if not mapped:
            continue                                   # unknown bucket: ignore
        kind, label = mapped
        model = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
        entries.append((kind, label, 300 if kind == "five_hour" else 10080,
                        _as_percent(limit.get("percent")), limit.get("resets_at"), model))
    return entries


def _fetch_endpoint(now: float) -> tuple[dict | None, str]:
    """One HTTPS GET: ``(payload, "")`` or ``(None, reason)``. Keeps the backoff
    ladder. Never raises."""
    global _claude_backoff_until, _claude_consecutive_429, _claude_endpoint_calls
    _claude_endpoint_calls += 1
    token = ""
    try:
        token = _access_token()
        req = urllib.request.Request(
            USAGE_URL, headers=dict(_HEADERS, Authorization="Bearer " + token))
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except LookupError as e:
        return None, _scrub(e, token)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # The expected shape of a token that went stale between the
            # expiresAt check and the call. Name it in the user's terms.
            why = ("Claude Code's login token was rejected — it refreshes "
                   "itself when a Claude session runs")
        elif e.code == 429:
            _claude_consecutive_429 += 1
            # Double per consecutive refusal, from whatever the server asked
            # for; the doubling is capped, the server's own ask never is. The
            # first 429 behaves exactly as `Retry-After` says.
            asked = _retry_after_seconds(e, now)
            wait = max(asked, min(MAX_BACKOFF_S,
                                  asked * 2 ** (_claude_consecutive_429 - 1)))
            _claude_backoff_until = now + wait
            again = " again" if _claude_consecutive_429 > 1 else ""
            why = (f"the usage endpoint is rate-limiting us{again} — pausing for "
                   f"{max(1, int(wait // 60))} min")
        else:
            why = f"the usage endpoint returned HTTP {e.code}"
        return None, _scrub(why, token)
    except urllib.error.URLError as e:
        return None, _scrub(f"cannot reach the usage endpoint ({e.reason})", token)
    except Exception as e:                                   # noqa: BLE001
        # The endpoint is undocumented: a shape change must degrade to
        # "unavailable", never throw into a request path.
        return None, _scrub(f"usage reading failed ({type(e).__name__})", token)
    finally:
        token = ""
    # A successful call clears any standing backoff and resets the ladder.
    _claude_backoff_until = 0.0
    _claude_consecutive_429 = 0
    if not isinstance(payload, dict):
        return None, "the usage endpoint returned an unfamiliar answer"
    return payload, ""


def _endpoint_due(now: float) -> bool:
    return now >= _claude_backoff_until and now - _claude_last_call >= ENDPOINT_MIN_INTERVAL_S


def _cached_endpoint(now: float, why: str) -> dict:
    """The last good endpoint reading, aged; or "unavailable" with ``why``."""
    good = _claude_last_good
    if good and now - good["at"] <= CACHED_MAX_AGE_S:
        src = _claude_source(good["entries"], at=good["at"], now=now,
                             via="endpoint-cached", live=False)
        if src["state"] == "ok":
            src["note"] = why or None
            return src
    return _unavailable("claude", why or "the usage endpoint has not been asked yet")


def read_claude_endpoint(now: float | None = None) -> dict:
    """The endpoint, called at most once per :data:`ENDPOINT_MIN_INTERVAL_S` and
    never during a backoff. A refusal serves the last good reading with its age
    instead of going "unavailable". Never raises."""
    global _claude_last_call, _claude_last_good, _claude_last_why
    now = time.time() if now is None else now
    if now < _claude_backoff_until:
        # Rate-limited recently. Make no call at all — the point of a backoff
        # is not to send the request.
        wait = int(_claude_backoff_until - now)
        return _cached_endpoint(
            now, f"the usage endpoint asked us to slow down — not retrying "
                 f"for another {max(1, wait // 60)} min")
    if now - _claude_last_call < ENDPOINT_MIN_INTERVAL_S:
        return _cached_endpoint(now, _claude_last_why)
    _claude_last_call = now
    payload, why = _fetch_endpoint(now)
    _claude_last_why = why
    if payload is None:
        return _cached_endpoint(now, why)
    entries = _endpoint_entries(payload)
    src = _claude_source(entries, at=now, now=now, via="endpoint", live=True)
    if src["state"] == "ok":
        _claude_last_good = {"at": now, "entries": entries}
        return src
    return _cached_endpoint(now, src["error"])


def read_claude(now: float | None = None, statusline_path=None) -> dict:
    """Claude's plan windows: the local statusline file first, the endpoint
    only when that is missing or no longer current. Never raises.

    While hub agents are working the file stays current and the endpoint is
    never called. When they idle — or when Claude is being used only outside
    the hub, which the file cannot see — the endpoint is asked at most every
    :data:`ENDPOINT_MIN_INTERVAL_S`, and whichever reading is newer wins.
    """
    now = time.time() if now is None else now
    local = read_claude_statusline(now, statusline_path)
    # Current means every window is recent AND still has a number: a window
    # that has rolled over withholds its percent, and only the endpoint can say
    # what the new window holds until an agent takes another turn.
    if local["state"] == "ok" and all(
            w["trusted"] and w["percent"] is not None for w in local["windows"]):
        return local
    remote = read_claude_endpoint(now)
    usable = [s for s in (local, remote) if s["state"] == "ok"]
    if usable:
        best = min(usable, key=lambda s: s["ageSeconds"])
        if best is local and remote["state"] != "ok":
            best["note"] = remote["error"]
        return best
    return _unavailable("claude", f"{local['error']}; {remote['error']}")


def _reset_claude_state() -> None:
    """Forget the endpoint's backoff, schedule and last good reading (tests)."""
    global _claude_backoff_until, _claude_consecutive_429, _claude_last_call, _claude_last_good
    global _claude_last_why
    _claude_backoff_until = 0.0
    _claude_consecutive_429 = 0
    _claude_last_call = float("-inf")
    _claude_last_good = None
    _claude_last_why = ""


def _as_percent(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Codex — last-write snapshot from the rollout files
# ---------------------------------------------------------------------------

def _rollout_files(limit: int) -> list[Path]:
    """Newest rollouts first, from the agent adapter that already owns the path.

    Falls back to its own glob only if the adapter is unavailable, so there is
    one definition of where Codex keeps its sessions.
    """
    try:
        import agents
        codex = agents.get_agent("codex")
        if codex is not None:
            return list(codex.rollout_files())[:limit]
    except Exception:                                        # noqa: BLE001
        pass
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    root = home / "sessions"
    if not root.exists():
        return []
    files = list(root.glob("*/*/*/rollout-*.jsonl"))
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files.sort(key=lambda p: p.name, reverse=True)
    return files[:limit]


def _newest_rate_limits(files) -> dict[str, tuple[dict, float]]:
    """The newest whole ``rate_limits`` record of each limit bucket.

    Returns ``{bucket: (rate_limits, epoch)}``. The bucket is the record's
    ``limit_id``; absent or ``"codex"`` is the account-wide one.

    Whole records within a bucket, deliberately — not the newest reading of
    each window stitched together from different records. A record is Codex's
    statement of which limits apply right now, and that set changes: when this
    account moved to a plan with only a weekly limit, its records stopped
    carrying a five-hour window at all, and stitching across records would
    have brought the previous plan's five-hour window back.

    But one record states one bucket, and Codex writes more than one: a model
    with a limit of its own gets its own bucket (``codex_bengalfox``, named
    "GPT-5.3-Codex-Spark", on this machine), written alongside the account's
    within the same millisecond, turn after turn. Taking only the newest
    record overall showed whichever bucket happened to write last, so the
    account's weekly figure and the model's swapped back and forth under the
    same label. Hence per bucket; read_codex decides which are still current.

    About one ``token_count`` record in a hundred has null windows — turns that
    never reached the API — so a record only counts when it actually carries
    numbers. Those nulls cluster per agent, which is why the caller passes a
    generous file budget rather than a handful.

    Records are ordered by *parsed* timestamp, not by string: Codex writes
    fractional seconds today, but ``...:40Z`` sorts above ``...:40.401Z``
    lexicographically, so a format change would silently pick the older record.

    One bucket is not named by its record. A turn on the model ``gpt-reserve``
    draws on the reserve pool, yet writes ``limit_id`` "codex" like any other,
    with the reserve's numbers (0% and its own reset time on 2026-09-17, beside
    the main pool's 97%). Read as written, the main figure flipped between the
    two with whichever session wrote last. So an account record belongs to the
    reserve when the turn it follows ran on that model — the ``turn_context``
    record before it names the model — and is returned under
    :data:`CODEX_RESERVE_POOL`, named as the app server names it. Not told
    apart by reset time: the main pool's own wobbles by a second from record
    to record.
    """
    best: dict[str, tuple[dict, float]] = {}
    extra = 0
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                model = ""
                for line in fh:
                    if '"turn_context"' in line:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if rec.get("type") == "turn_context":
                            named = (rec.get("payload") or {}).get("model")
                            model = named.strip().lower() if isinstance(named, str) else ""
                        continue
                    if '"token_count"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    payload = rec.get("payload") or {}
                    if payload.get("type") != "token_count":
                        continue
                    limits = payload.get("rate_limits") or {}
                    if not (limits.get("primary") or limits.get("secondary")):
                        continue
                    at = _iso_to_epoch(rec.get("timestamp"))
                    if at is None:
                        continue
                    bucket = limits.get("limit_id") or CODEX_MAIN_POOL
                    if bucket == CODEX_MAIN_POOL and model == CODEX_RESERVE_MODEL:
                        bucket = CODEX_RESERVE_POOL
                        limits = dict(limits, limit_id=CODEX_RESERVE_POOL,
                                      limit_name=CODEX_RESERVE_MODEL)
                    if bucket not in best or at > best[bucket][1]:
                        best[bucket] = (limits, at)
        except OSError:
            continue
        if best:
            # Files are ordered by mtime, but the records we want are ordered
            # by their own timestamps, and the two can disagree: a rollout
            # touched a minute ago may hold nothing newer than an hour-old
            # reading, while the file behind it was appended to more recently.
            # So don't stop at the first hit — look at a couple more files and
            # keep the newest record of each bucket across all of them.
            # Bounded, so this stays a handful of reads and not a walk through
            # the history.
            extra += 1
            if extra >= 3:
                break
    return best


def _iso_to_epoch(ts) -> float | None:
    """Codex's ISO-8601 timestamps ('2026-09-09T11:50:28.610Z') to epoch.

    Returns None for anything it cannot read — including a non-string, should
    the field ever become numeric. A record we cannot date is skipped rather
    than dated wrongly, which keeps the age (and so guard 1) honest.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError, OverflowError):
        return None


def _codex_window_kind(minutes) -> tuple[str, str]:
    """A Codex window's kind and short label, from its length.

    The two lengths seen so far map onto the shared vocabulary. Anything else
    is still a real limit, so it is kept and labelled by its length rather than
    dropped or forced into a window it is not.
    """
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "window", "?"
    if m in _CODEX_KINDS:
        return _CODEX_KINDS[m]
    if m > 0 and m % 1440 == 0:
        label = f"{m // 1440}d"
    elif m > 0 and m % 60 == 0:
        label = f"{m // 60}h"
    else:
        label = f"{m}m"
    return f"window_{m}", label


def _listed_windows(limits: dict) -> list[tuple[dict, str, str]]:
    """The windows one record lists, each identified by its length.

    Never by its slot: Codex lists whichever limits apply in `primary`, then
    `secondary` — usually the five-hour window first and the weekly one second,
    but a plan with only a weekly limit reports it as `primary` with nothing
    after it. Seen on this machine: every record since the account moved to
    "prolite", and a stretch of "plus" records on 2026-08-06. Reading the slot
    as the window labelled a weekly number "5-hour" and judged its staleness
    against five hours instead of a week. Codex's own RateLimitWindow is
    exactly {used_percent, window_minutes, resets_at}: the length is the
    identity.

    The same length listed twice has never been seen (0 of 4,164 records). If
    it ever is, the higher reading is kept: on a surface whose job is to warn,
    the safe mistake is to over-warn.
    """
    def pct(win):
        v = _as_percent(win.get("used_percent"))
        return -1.0 if v is None else v

    chosen: dict[str, tuple[dict, str, str]] = {}
    for slot in ("primary", "secondary"):
        win = limits.get(slot)
        if not win:
            continue
        kind, label = _codex_window_kind(win.get("window_minutes"))
        if kind not in chosen or pct(win) > pct(chosen[kind][0]):
            chosen[kind] = (win, kind, label)
    return list(chosen.values())


def _reset_plausible(resets: float, written_at: float, window_minutes) -> bool:
    """Whether a reset time can belong to a record written at ``written_at``.

    Judged against the record's own time, not against now, and against the
    window's own length, not a fixed bound. A reset is set when a window opens,
    so when the record is written it lies ahead by at most the window's length.
    Checked that way it still catches what the check exists for — a duration
    read as an epoch lands in 1970 — without rejecting a window longer than
    some fixed bound, or a reading that is simply old: a Codex left idle for
    two months writes nothing, and its last, long-past reset is a rolled-over
    window, not a format change.
    """
    try:
        minutes = float(window_minutes)
    except (TypeError, ValueError):
        minutes = 0.0
    reach = minutes * 60 if 1 <= minutes <= 366 * 24 * 60 else RESET_SANITY_S
    return written_at - RESET_SLACK_S <= resets <= written_at + reach + RESET_SLACK_S


def _session_pools(files) -> list[dict]:
    """The pools the session records still report, as pool records
    ``{id, limitName, limits, at, plan, live}`` (``limits`` in the records'
    own snake_case shape)."""
    buckets = _newest_rate_limits(files)
    if not buckets:
        return []
    newest_at = max(at for _, at in buckets.values())
    # Only buckets still being reported: co-reported ones are a millisecond
    # apart, and one that trails by more than BUCKET_TOGETHER_S has stopped.
    # Never the account's two pools, though. A model's bucket can write on its
    # own — on this machine Spark ran up to 43 s ahead of the account's — and a
    # longer run must not drop the one number the chip exists for; and the main
    # pool and the reserve are never written together at all, since a turn
    # draws on one or the other. Nothing is lost by exempting them: across a
    # plan change the bucket stays "codex", so the change is handled by taking
    # its whole record, and each pool's own staleness and rollover guards
    # still apply.
    return [{"id": bucket, "limitName": limits.get("limit_name"), "limits": limits,
             "at": at, "plan": limits.get("plan_type"), "live": False}
            for bucket, (limits, at) in buckets.items()
            if bucket in (CODEX_MAIN_POOL, CODEX_RESERVE_POOL)
            or newest_at - at <= BUCKET_TOGETHER_S]


# --- Preferred: Codex's app server ------------------------------------------

def _codex_app_argv() -> list[str] | None:
    """How to start Codex's app server, or None when ``codex`` is not installed."""
    exe = shutil.which("codex")
    return [exe, "app-server"] if exe else None


_SUSPENDED = 0x4 if os.name == "nt" else 0                   # CREATE_SUSPENDED


def _win_job_api():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            wintypes.LPVOID, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def _job_open():
    """Windows: a job that ends everything in it when it is ended — or when
    its one handle, held here, closes, so also when the hub dies mid-reading.
    None where there is no such thing or it cannot be had; the kill then goes
    by ``taskkill``."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        k32 = _win_job_api()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, of which only LimitFlags (a
        # DWORD after two 64-bit time limits) is set: KILL_ON_JOB_CLOSE.
        info = (ctypes.c_byte * (144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112))()
        ctypes.c_uint32.from_buffer(info, 16).value = 0x2000
        kept = False
        try:
            kept = bool(k32.SetInformationJobObject(job, 9, ctypes.byref(info),
                                                    ctypes.sizeof(info)))
        finally:
            if not kept:
                k32.CloseHandle(job)
        return job if kept else None
    except Exception:                                        # noqa: BLE001
        return None


def _job_start(job, proc) -> bool:
    """Put the child, started suspended, in the job and let it run: it has
    started nothing yet, so nothing of its tree is ever outside the job. False
    when the job did not take it (it still runs; ``taskkill`` ends it then).
    Raises when it could not be let run at all."""
    import ctypes
    from ctypes import wintypes
    handle = int(proc._handle)                               # noqa: SLF001
    held = bool(_win_job_api().AssignProcessToJobObject(job, handle))
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    if ntdll.NtResumeProcess(handle) != 0:
        raise OSError("the child could not be resumed")
    return held


def _job_end(job) -> bool:
    """End everything in the job and let the job go. True when it was ended."""
    ended = False
    try:
        k32 = _win_job_api()
        try:
            ended = bool(k32.TerminateJobObject(job, 1))
        finally:
            k32.CloseHandle(job)                             # kills too, by its limit
    except Exception:                                        # noqa: BLE001
        pass
    return ended


def _kill_tree(proc, job=None, deadline: float | None = None) -> None:
    """End the child and everything it started, by ``deadline`` (monotonic).
    ``codex`` on Windows is an npm shim — cmd.exe, then node, then the real
    program — and killing only the first leaves the other two running and
    holding the pipes open. On Windows the tree is in ``job`` and ends with
    it, at once and without another process; ``taskkill`` only when there is
    no job, and only for the time that is left."""
    if deadline is None:
        deadline = time.monotonic() + CODEX_APP_KILL_S
    ended = _job_end(job) if job is not None else False
    if not ended:
        try:
            if os.name == "nt":
                # No pipes: a taskkill killed at its timeout is not waited on
                # for output.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                               timeout=max(1.0, deadline - time.monotonic() - 0.2))
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:                                    # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        proc.wait(timeout=max(0.2, deadline - time.monotonic()))
    except Exception:                                        # noqa: BLE001
        pass
    # stdout is left to the reader thread, which closes it at end of file:
    # closing it here could wait on a read that a surviving grandchild holds.
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:                                        # noqa: BLE001
        pass


def read_codex_app_server(timeout: float | None = None) -> tuple[dict | None, str]:
    """One ``account/rateLimits/read`` from ``codex app-server``: ``(result,
    "")`` or ``(None, reason)``. Never raises.

    JSON-RPC over stdio, one object per line: ``initialize``, ``initialized``,
    then the one read. A child process per reading, never kept: it is started
    without a console window (a console-less hub's spawns took the desktop's
    focus before), given ``timeout`` seconds to answer, and killed with its
    whole tree whether it answered or not — within ``CODEX_APP_KILL_S`` more,
    so the call never outlasts the two together. Only ever that one read-only method — the
    same server also *consumes* rate-limit reset credits.
    """
    timeout = CODEX_APP_TIMEOUT_S if timeout is None else timeout
    argv = _codex_app_argv()
    if not argv:
        return None, "Codex is not installed on this machine"
    deadline = time.monotonic() + timeout
    proc = None
    pumping = False
    job = _job_open()
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW | (_SUSPENDED if job is not None else 0),
            # Its own process group where there is one, so the kill takes
            # everything it started with it.
            start_new_session=os.name != "nt")
        if job is not None and not _job_start(job, proc):
            _job_end(job)
            job = None
        lines: queue.Queue = queue.Queue()

        def pump(stream=proc.stdout):
            try:
                with stream:
                    for line in stream:
                        lines.put(line)
            except Exception:                                # noqa: BLE001
                pass
            lines.put(None)

        threading.Thread(target=pump, daemon=True, name="codex-app-server").start()
        pumping = True

        def send(message: dict) -> None:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()

        send({"id": 1, "method": "initialize", "params": {"clientInfo": {
            "name": "ensemble", "title": "ensemble", "version": "0"}}})
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise queue.Empty
            line = lines.get(timeout=left)
            if line is None:
                return None, "Codex's app server ended without answering"
            try:
                message = json.loads(line)
            except ValueError:
                continue
            # Notifications (no id) and anything else it volunteers are skipped.
            if not isinstance(message, dict) or message.get("id") not in (1, 2):
                continue
            if "result" not in message:
                # Not logged in, or an older Codex that does not know the method.
                error = message.get("error")
                detail = error.get("message") if isinstance(error, dict) else ""
                return None, _scrub(f"Codex's app server refused the reading"
                                    f"{': ' + str(detail)[:120] if detail else ''}")
            if message["id"] == 1:
                send({"method": "initialized"})
                send({"id": 2, "method": "account/rateLimits/read"})
                continue
            result = message["result"]
            if not isinstance(result, dict):
                return None, "Codex's app server gave an unfamiliar answer"
            return result, ""
    except queue.Empty:
        return None, f"Codex's app server did not answer within {int(timeout)} s"
    except Exception as e:                                   # noqa: BLE001
        return None, _scrub(f"could not ask Codex's app server ({type(e).__name__})")
    finally:
        if proc is not None:
            _kill_tree(proc, job, deadline + CODEX_APP_KILL_S)
            if not pumping and proc.stdout is not None:
                # It never ran, so nothing holds the pipe and nobody reads it.
                try:
                    proc.stdout.close()
                except Exception:                            # noqa: BLE001
                    pass
        elif job is not None:
            _job_end(job)


def _app_server_pools(result: dict, now: float) -> list[dict]:
    """The app server's answer as pool records. Raises ValueError for an answer
    whose shape cannot be trusted — the caller then falls back to the session
    records rather than show it.

    ``rateLimitsByLimitId`` holds every pool, ``rateLimits`` the account's own
    (kept for an answer that has only that). The windows are camelCase here
    and snake_case in the session records; they leave in the records' shape so
    one path turns either into windows. Everything else in the answer — the
    reset credits, the account id, the upsell — is ignored.
    """
    by_id = result.get("rateLimitsByLimitId")
    entries = dict(by_id) if isinstance(by_id, dict) else {}
    account = result.get("rateLimits")
    if isinstance(account, dict):
        entries.setdefault(account.get("limitId") or CODEX_MAIN_POOL, account)
    pools = []
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        limits = {}
        for slot in ("primary", "secondary"):
            win = entry.get(slot)
            if not isinstance(win, dict):
                continue
            resets = win.get("resetsAt")
            if resets is not None and (
                    isinstance(resets, bool) or not isinstance(resets, (int, float))
                    or not _reset_plausible(float(resets), now, win.get("windowDurationMins"))):
                raise ValueError("reset time")
            limits[slot] = {"used_percent": win.get("usedPercent"),
                            "window_minutes": win.get("windowDurationMins"),
                            "resets_at": resets}
        if not limits:
            continue
        name = entry.get("limitName")
        pools.append({"id": str(entry.get("limitId") or key),
                      "limitName": name if isinstance(name, str) and name else None,
                      "limits": limits, "at": now, "plan": entry.get("planType"),
                      "live": True})
    if not pools:
        raise ValueError("no windows")
    return pools


def _codex_app_pools(now: float, fetch=None) -> tuple[list[dict], str]:
    """Pools from the app server: this poll's, or the last good answer's (aged)
    while it fails. ``(pools, why it is not this poll's or "")``."""
    global _codex_app_retry_at, _codex_app_last_good, _codex_app_why
    if now >= _codex_app_retry_at:
        result, why = (fetch or read_codex_app_server)()
        pools = None
        if result is not None:
            try:
                pools = _app_server_pools(result, now)
            except Exception:                                # noqa: BLE001
                why = ("Codex's app server gave an answer in an unfamiliar "
                       "format — it has probably changed")
        if pools is not None:
            _codex_app_last_good = {"at": now, "pools": pools}
            _codex_app_why = ""
            return copy.deepcopy(pools), ""
        _codex_app_why = why
        _codex_app_retry_at = now + CODEX_APP_RETRY_S
    good = _codex_app_last_good
    if good and now - good["at"] <= CACHED_MAX_AGE_S:
        return [dict(p, live=False) for p in copy.deepcopy(good["pools"])], _codex_app_why
    return [], _codex_app_why


def _reset_codex_state() -> None:
    """Forget the app server's retry time and last good answer (tests)."""
    global _codex_app_retry_at, _codex_app_last_good, _codex_app_why
    _codex_app_retry_at = 0.0
    _codex_app_last_good = None
    _codex_app_why = ""


# --- Pools -------------------------------------------------------------------

def _pool_label(pool_id: str, limit_name) -> str | None:
    """A pool's name on the board: None for the main pool (unlabelled, as it
    always was), "reserve" for ``gpt-reserve``, and otherwise the name Codex
    gives it ("GPT-5.3-Codex-Spark") — or its id, for a pool with no name. An
    unknown pool is kept and named, never dropped."""
    if pool_id == CODEX_MAIN_POOL:
        return None
    name = (limit_name or pool_id or "").strip()
    plain = re.fullmatch(r"gpt-([a-z][a-z -]*)", name, re.IGNORECASE)
    return plain.group(1).lower() if plain else name


def codex_config_model(path=None) -> str:
    """The model Codex runs on when a launch names none: ``model`` in
    ``config.toml`` (the active ``profile``'s, when one is set). "" when
    there is no such file or line."""
    if path is None:
        home = os.environ.get("CODEX_HOME")
        path = (Path(home) if home else Path.home() / ".codex") / "config.toml"
    try:
        # utf-8-sig: an editor's byte-order mark would hide the first line.
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    try:
        import tomllib
        config = tomllib.loads(text)
        profile = (config.get("profiles") or {}).get(config.get("profile")) or {}
        model = profile.get("model") or config.get("model")
        return model.strip() if isinstance(model, str) else ""
    except Exception:                                        # noqa: BLE001
        pass
    # No tomllib (Python < 3.11), or a file it will not take: the top-level
    # `model = "..."` line, which sits above the first table.
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            break
        found = re.match(r"""\s*model\s*=\s*(["'])(.*?)\1""", line)
        if found:
            return found.group(2).strip()
    return ""


def codex_pool_for_model(source: dict | None, model: str = "") -> dict:
    """The pool a Codex agent runs on: ``{id, label, model}``.

    ``model`` is the seat's named model; without one it is the config's
    (``source["configModel"]``). ``gpt-reserve`` draws on the reserve, Spark's
    slug on Spark's pool, anything else — no model at all, and a slug that
    matches some pool not known here, included — on the main pool.
    """
    source = source or {}
    slug = (model or source.get("configModel") or "").strip()
    low = slug.lower()
    main = {"id": CODEX_MAIN_POOL, "label": None, "model": slug}
    pool_id = _CODEX_MODEL_POOLS.get(low)
    if pool_id is None:
        return main
    for pool in source.get("pools") or []:
        if pool.get("id") == pool_id:
            return {"id": pool_id, "label": pool.get("label"), "model": slug}
    # The pool has not been read (session records only, and no turn on it
    # yet). Still that pool: unknown, never the main pool's number.
    return {"id": pool_id, "label": _pool_label(pool_id, slug), "model": slug}


def _mark_pool_in_use(source: dict, config_model: str) -> dict:
    """Say on the source, its pools and its windows which pool Codex sessions
    currently run on — the config's model, since the hub names a model only
    when a task's line-up does."""
    source["configModel"] = config_model
    source.setdefault("pools", [])
    used = codex_pool_for_model(source, "")
    source["poolInUse"] = used
    for pool in source["pools"]:
        pool["inUse"] = pool["id"] == used["id"]
    for win in source.get("windows") or []:
        win["inUse"] = win.get("pool") == used["id"]
    return source


def read_codex(now: float | None = None, files=None, app_server=None) -> dict:
    """Codex's pools, each with both staleness guards.

    The app server's reading first, the session records for whatever it did
    not give: per pool the newer record wins, so this poll's app-server answer
    always does, and while the app server fails a pool keeps the newer of its
    last good answer and the session records.

    ``now``, ``files`` and ``app_server`` are injectable so the guards can be
    tested against a fixed clock and synthetic readings. ``app_server`` is a
    callable returning ``(result, why)`` like :func:`read_codex_app_server`,
    or False for the session records alone — which is also what injected
    ``files`` without it mean, so a test never starts a real Codex. Never
    raises.
    """
    now = time.time() if now is None else now
    try:
        config_model = codex_config_model()
    except Exception:                                        # noqa: BLE001
        config_model = ""
    if app_server is None and files is not None:
        app_server = False
    try:
        source = _read_codex_pools(now, files, app_server)
    except Exception as e:                                   # noqa: BLE001
        source = _unavailable("codex", _scrub(f"cannot read Codex's limits ({type(e).__name__})"))
    return _mark_pool_in_use(source, config_model)


def _pool_windows(pool: dict, now: float) -> list[dict] | str:
    """One pool's windows, each with both staleness guards — or, as a string,
    why the pool's reset times cannot be believed."""
    windows = []
    at, limits = pool["at"], pool["limits"]
    for win, kind, label in _listed_windows(limits):
        age = max(0, int(now - at))
        # Guard 2 rests entirely on the reset time, so the shape of that field
        # decides everything. The invariant: **a window whose reset time we
        # cannot verify is never reported as current.** Without that, an
        # unparseable reset silently switches the guard off and a dead agent's
        # 100% is served as live — the precise bug this reader exists to avoid.
        resets = win.get("resets_at")
        resets_dt = None
        reset_unknown = False
        if resets is None:
            # Nothing to check against. From a record, the value may be
            # current or may be a dead number from a window that reset an
            # hour ago; we cannot tell, so we do not claim. This poll's
            # app-server answer is Codex's statement about now, and stands.
            reset_unknown = not pool["live"]
        elif isinstance(resets, (int, float)) and not isinstance(resets, bool):
            # Sanity-check before believing it. If this field ever changes
            # meaning — a duration instead of an epoch, say — the parse yields
            # 1970, every window reads "rolled over", and the chip goes
            # confidently and permanently blank. Fail the source instead.
            if not _reset_plausible(float(resets), at, win.get("window_minutes")):
                return ("Codex reported a reset time that is not a plausible "
                        "date — the rollout format has probably changed")
            try:
                resets_dt = datetime.fromtimestamp(resets, timezone.utc)
            except (OverflowError, OSError, ValueError):
                return ("Codex reported a reset time that could not be read "
                        "as a date — the rollout format has probably changed")
        else:
            # Present but not a number — an ISO string is the likeliest way this
            # field ever mutates. Fail the source loudly rather than let the
            # value fall through the guard unchecked.
            return ("Codex reported a reset time in an unfamiliar format — "
                    "the rollout format has probably changed")
        # A window whose reset has passed has already rolled to 0. Its last value
        # is dead — preserve it separately, never report it as now.
        rolled = bool(not pool["live"] and resets_dt and resets_dt.timestamp() < now)
        value = _as_percent(win.get("used_percent"))
        # Withheld in both unverifiable cases: rolled over, or no reset to check.
        withheld = rolled or reset_unknown
        windows.append(_window(
            kind, label,
            percent=None if withheld else value,
            stale_percent=value if withheld else None,
            rolled_over=rolled,
            reset_unknown=reset_unknown,
            model=pool["label"],
            pool=pool["id"],
            window_minutes=win.get("window_minutes"),
            resets_at=resets_dt.isoformat() if resets_dt else None,
            # Guard 1: each window carries the reading's age and decides for
            # itself whether that is still current — 30 minutes matters to the
            # five-hour window and is nothing to the weekly one.
            age_seconds=age,
        ))
    return windows


def _read_codex_pools(now: float, files, app_server) -> dict:
    app_pools, app_why = ([], "") if app_server is False else _codex_app_pools(
        now, None if app_server in (None, True) else app_server)
    via = "app-server" if any(p["live"] for p in app_pools) else "sessions"
    # The session records are read beside even a good answer: one that leaves
    # a pool out (an answer with the account's pool alone is a known shape)
    # must not take the reserve's or Spark's still-valid record off the board.
    # A pool the answer does give is this poll's, so it wins below.
    session_pools, session_why = [], ""
    try:
        session_pools = _session_pools(
            _rollout_files(CODEX_MAX_FILES) if files is None else files)
    except Exception as e:                                   # noqa: BLE001
        session_why = _scrub(f"cannot read Codex rollouts ({type(e).__name__})")
    by_id: dict[str, dict] = {}
    for pool in [*app_pools, *session_pools]:
        held = by_id.get(pool["id"])
        # This poll's answer stands whatever a record's clock says; otherwise
        # the newer of the two.
        if held is None or (not held["live"] and pool["at"] > held["at"]):
            by_id[pool["id"]] = pool
    if not by_id:
        reason = session_why or "no Codex session has reported its limits recently"
        return _unavailable("codex", f"{app_why}; {reason}" if app_why else reason)

    # The main pool first, so its windows lead.
    pools = sorted(by_id.values(), key=lambda p: (p["id"] != CODEX_MAIN_POOL, p["id"]))
    newest_at = max(p["at"] for p in pools)
    # The plan is the account's. Model buckets in the session records carry a
    # null plan_type, so taking it from whichever wrote last would make the
    # label flicker.
    plan = (by_id.get(CODEX_MAIN_POOL) or max(pools, key=lambda p: p["at"]))["plan"]

    windows = []
    for pool in list(pools):
        # A pool's own limit is labelled with its name — the same path Claude's
        # per-model weekly cap takes through the tray and banner.
        pool["label"] = _pool_label(pool["id"], pool["limitName"])
        listed = _pool_windows(pool, now)
        if isinstance(listed, str):
            if via == "app-server" and not pool["live"]:
                # A record that cannot be read beside a good answer costs its
                # own pool, not the answer.
                pools.remove(pool)
                continue
            return _unavailable("codex", listed)
        windows.extend(listed)
    if not windows:
        return _unavailable("codex", "the newest Codex reading carries no windows")

    return {
        "source": "codex",
        "state": "ok",
        "error": None,
        # Where the numbers came from: "app-server" (asked this poll) or
        # "sessions" (the session records, with whatever the app server last
        # said about a pool they do not cover). `note` says why not the former.
        "via": via,
        "note": (app_why or None) if via != "app-server" else None,
        "planType": plan,
        "asOf": newest_at,
        "ageSeconds": max(0, int(now - newest_at)),
        # Source-level summary only: true when *every* window is still current.
        # The render path uses each window's own `trusted`.
        "trusted": all(w["trusted"] for w in windows),
        "pools": [{"id": p["id"], "label": p["label"], "limitName": p["limitName"],
                   "ageSeconds": max(0, int(now - p["at"])), "inUse": False}
                  for p in pools],
        "windows": windows,
    }


# ---------------------------------------------------------------------------
# Cache + scheduler
# ---------------------------------------------------------------------------

def refresh() -> dict:
    """Read both sources and replace the cache. Called only by the scheduler
    (and by tests) — never from a request path."""
    sources = {"claude": read_claude(), "codex": read_codex()}
    with _LOCK:
        _STATE["sources"] = sources
        _STATE["checkedAt"] = time.time()
        _STATE["state"] = "ready"
        return _snapshot_locked()


def snapshot() -> dict:
    """The cached readings. Pure dict copy — no network, no file I/O, no
    blocking — so the dashboard's poll can never wait on the remote call."""
    with _LOCK:
        return _snapshot_locked()


def _snapshot_locked() -> dict:
    sources = _STATE["sources"]
    snap = {
        "state": _STATE["state"],
        "checkedAt": _STATE["checkedAt"],
        "refreshIntervalS": REFRESH_INTERVAL_S,
        "staleFraction": STALE_FRACTION,
        "warnPercent": WARN_PERCENT,
        "alarmPercent": ALARM_PERCENT,
        # How hard the fallback endpoint is being used: it rate-limits, and
        # this is the number to look at when it starts refusing again.
        "claudeEndpoint": {
            "calls": _claude_endpoint_calls,
            "lastCalledAt": _claude_last_call if _claude_last_call > 0 else None,
            "backoffUntil": _claude_backoff_until or None,
            "minIntervalS": ENDPOINT_MIN_INTERVAL_S,
        },
        # Deep-copied: callers (the endpoint, the MCP tool) must not be able to
        # mutate the cache the poller owns.
        "sources": [copy.deepcopy(sources[k]) for k in ("claude", "codex")
                    if k in sources],
    }
    snap["alerts"] = alerts(snap["sources"])
    snap["notices"] = notices(snap["sources"])
    return snap


def alerts(sources) -> list[dict]:
    """Windows a human should be told about, loudest first.

    What does and does not alert, and why:

    * **Rolled over, or no verifiable reset time → never.** Either the window
      has already reset and its last value is dead, or we could not check
      whether it had. Both withhold ``percent`` upstream, so both land here as
      "no current value" — which is the guard that stops a dead agent's 100%
      shouting for ever.
    * **Stale but not rolled over → yes, marked ``atLeast``.** Codex windows are
      anchored with a fixed reset, not sliding, so within one window
      ``used_percent`` only ever goes up. A reading of 95% from three hours ago
      whose window has *not* rolled therefore means "at least 95% right now" —
      which is exactly the warning this feature exists to give. Suppressing it
      would be the one failure mode we cannot afford. It is qualified with its
      age, never presented as a fresh measurement.
    * **Fresh and high → yes, plainly.**
    * **A Codex pool that Codex agents are not running on → never.** A spent
      main pool while Codex runs on the reserve stops nothing, so it is
      information (:func:`notices`), not an alarm that Codex is unusable.

    Thresholds come off ``percent`` only, never the payload's ``severity``: the
    investigation never observed a non-normal value, so branching on the
    escalated strings would be guessing.
    """
    return _hot_windows(sources, in_use=True)


def notices(sources) -> list[dict]:
    """High windows of a Codex pool that is not the one in use: the same
    entries as :func:`alerts` with ``level`` "info". Kept out of ``alerts`` so
    that neither the banner nor a page written before pools can take one for
    an alarm."""
    return _hot_windows(sources, in_use=False)


def _hot_windows(sources, *, in_use: bool) -> list[dict]:
    out = []
    for src in sources:
        if src.get("state") != "ok":
            continue
        for win in src.get("windows") or []:
            if win.get("rolledOver"):
                continue
            # Only a Codex window says False; every other window counts as in use.
            if (win.get("inUse") is not False) != in_use:
                continue
            pct = win.get("percent")
            if pct is None or pct < WARN_PERCENT:
                continue
            out.append({
                "source": src["source"],
                "kind": win["kind"],
                "label": win["label"],
                "model": win.get("model"),
                "pool": win.get("pool"),
                "percent": pct,
                "resetsAt": win.get("resetsAt"),
                "level": ("info" if not in_use
                          else "alarm" if pct >= ALARM_PERCENT else "warn"),
                # True when the reading is old: the value is a floor, not a
                # measurement, and the surface must say so.
                "atLeast": not win.get("trusted"),
                "ageSeconds": win.get("ageSeconds"),
            })
    out.sort(key=lambda a: a["percent"], reverse=True)
    return out


def start_scheduler(interval_s: int = REFRESH_INTERVAL_S) -> None:
    """Background refresh. All the I/O this feature does happens here."""
    def loop():
        while True:
            try:
                refresh()
            except Exception as e:                           # keep the loop alive
                with _LOCK:
                    _STATE["state"] = "ready"
                    _STATE["sources"] = {
                        "claude": _unavailable("claude", _scrub(f"poller error ({type(e).__name__})")),
                        "codex": _unavailable("codex", _scrub(f"poller error ({type(e).__name__})")),
                    }
            time.sleep(max(30, int(interval_s)))

    threading.Thread(target=loop, daemon=True, name="plan-usage").start()
