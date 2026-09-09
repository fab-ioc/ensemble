"""Plan-allowance readings for the agent kinds that run on this machine.

Answers one question the per-session cost chip cannot: **how much of the
subscription's allowance is gone, and when does the window reset.** That number
is *account-wide* — every task on this machine burns the same windows — so it
belongs in the board header, never on a task card, where a percentage would read
as caused by that task.

Two sources, deliberately not symmetrical:

``read_claude()``
    ``GET https://api.anthropic.com/api/oauth/usage`` with the OAuth access
    token Claude Code already keeps in ``~/.claude/.credentials.json``. Live
    server-side truth. **Undocumented** — an internal endpoint between Claude
    Code and Anthropic that can change or vanish without notice, so every
    failure degrades to "unavailable" and nothing else in the dashboard is
    affected. The stored token expires in ~3 hours and Claude Code refreshes it
    in place, so we re-read the file on every poll and report "unavailable"
    (with the reason) when it has gone stale rather than implementing an OAuth
    refresh we would then have to maintain. It also **rate-limits** — observed
    live, an HTTP 429 after a burst of calls — so a 429 stops the poller calling
    for a while rather than being retried on the next tick.

``read_codex()``
    No network, no credentials: the newest ``token_count`` event in the Codex
    rollout JSONLs. **A last-write snapshot, not a feed** — the rollout is
    appended only when that agent takes a turn, so a reading's age is unbounded
    and it freezes exactly when an agent stops, which is the condition we most
    want to notice. Two guards, both mandatory, both below: carry the age and
    stop trusting a reading once it has gone old *relative to that window's own
    length*, and treat a window whose ``resets_at`` has passed as rolled over
    rather than reporting its dead value.

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
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

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

# A parsed reset that lands this far from now is not a reset time — most likely
# the field changed meaning (a duration rather than an epoch, say). Reporting
# "rolled over" off a misparse would blank the chip confidently and for ever, so
# an implausible value fails the whole source loudly instead.
RESET_SANITY_S = 60 * 86400

WARN_PERCENT = 80          # banner
ALARM_PERCENT = 95         # banner, louder

# The usage endpoint rate-limits — observed live, an HTTP 429 after a burst of
# calls. It is an undocumented internal endpoint and we are a guest on it, so a
# 429 stops us calling rather than being absorbed and retried on the next tick.
# Honour `Retry-After` when the response carries one; otherwise wait this long.
DEFAULT_BACKOFF_S = 600
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

# Claude's `limits[]` kinds -> our shared vocabulary. Anything else is ignored,
# which is what keeps an endpoint change from breaking the surface.
_CLAUDE_KINDS = {
    "session": ("five_hour", "5h"),
    "weekly_all": ("seven_day", "7d"),
    "weekly_scoped": ("seven_day_model", "7d"),
}

# Codex names its windows by position; `window_minutes` says which is which.
_CODEX_WINDOWS = {
    "primary": ("five_hour", "5h"),
    "secondary": ("seven_day", "7d"),
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
            model: str | None = None, age_seconds=None) -> dict:
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

def _retry_after_seconds(exc) -> float:
    """The `Retry-After` header as seconds, clamped. Falls back to the default.

    Only the delta-seconds form is handled; an HTTP-date would fall back, which
    is the safe direction (we wait, we do not hammer).
    """
    try:
        raw = (exc.headers or {}).get("Retry-After")
        wait = float(str(raw).strip())
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_BACKOFF_S
    return min(MAX_BACKOFF_S, max(60.0, wait))


def read_claude(now: float | None = None) -> dict:
    """One HTTPS GET, mapped into the shared shape. Never raises."""
    global _claude_backoff_until, _claude_consecutive_429
    now = time.time() if now is None else now
    if now < _claude_backoff_until:
        # Rate-limited recently. Say so plainly and make no call at all — the
        # point of a backoff is not to send the request.
        wait = int(_claude_backoff_until - now)
        return _unavailable(
            "claude", f"the usage endpoint asked us to slow down — not retrying "
                      f"for another {max(1, wait // 60)} min")
    token = ""
    try:
        token = _access_token()
        req = urllib.request.Request(
            USAGE_URL, headers=dict(_HEADERS, Authorization="Bearer " + token))
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except LookupError as e:
        return _unavailable("claude", _scrub(e, token))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # The expected shape of a token that went stale between the
            # expiresAt check and the call. Name it in the user's terms.
            why = ("Claude Code's login token was rejected — it refreshes "
                   "itself when a Claude session runs")
        elif e.code == 429:
            _claude_consecutive_429 += 1
            # Double per consecutive refusal, from whatever the server asked
            # for, capped. The first 429 behaves exactly as `Retry-After` says.
            wait = min(MAX_BACKOFF_S,
                       _retry_after_seconds(e) * 2 ** (_claude_consecutive_429 - 1))
            _claude_backoff_until = now + wait
            again = " again" if _claude_consecutive_429 > 1 else ""
            why = (f"the usage endpoint is rate-limiting us{again} — pausing for "
                   f"{max(1, int(wait // 60))} min")
        else:
            why = f"the usage endpoint returned HTTP {e.code}"
        return _unavailable("claude", _scrub(why, token))
    except urllib.error.URLError as e:
        return _unavailable("claude", _scrub(f"cannot reach the usage endpoint ({e.reason})", token))
    except Exception as e:                                   # noqa: BLE001
        # The endpoint is undocumented: a shape change must degrade to
        # "unavailable", never throw into a request path.
        return _unavailable("claude", _scrub(f"usage reading failed ({type(e).__name__})", token))
    finally:
        token = ""

    # A successful call clears any standing backoff and resets the ladder.
    _claude_backoff_until = 0.0
    _claude_consecutive_429 = 0
    windows = []
    for limit in (payload.get("limits") or []):
        mapped = _CLAUDE_KINDS.get(limit.get("kind"))
        if not mapped:
            continue                                   # unknown bucket: ignore
        kind, label = mapped
        model = ((limit.get("scope") or {}).get("model") or {}).get("display_name")
        windows.append(_window(
            kind, label,
            percent=_as_percent(limit.get("percent")),
            window_minutes=300 if kind == "five_hour" else 10080,
            resets_at=limit.get("resets_at"),
            model=model,
            # A live HTTPS read: every window is as of right now.
            age_seconds=0,
        ))
    if not windows:
        return _unavailable("claude", "the usage endpoint returned no recognisable windows")
    return {
        "source": "claude",
        "state": "ok",
        "error": None,
        "planType": None,
        "asOf": time.time(),
        "ageSeconds": 0,
        "trusted": True,
        "windows": windows,
    }


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


def _newest_rate_limits(files) -> tuple[dict | None, float | None]:
    """The newest populated ``rate_limits`` payload, and when it was written.

    About one ``token_count`` record in a hundred has null windows — turns that
    never reached the API — so a record only counts when it actually carries
    numbers. Those nulls cluster per agent, which is why the caller passes a
    generous file budget rather than a handful.

    Records are ordered by *parsed* timestamp, not by string: Codex writes
    fractional seconds today, but ``...:40Z`` sorts above ``...:40.401Z``
    lexicographically, so a format change would silently pick the older record.
    """
    best: dict | None = None
    best_at: float | None = None
    extra = 0
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
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
                    at = _iso_to_epoch(rec.get("timestamp") or "")
                    if at is None:
                        continue
                    if best_at is None or at > best_at:
                        best, best_at = limits, at
        except OSError:
            continue
        if best is not None:
            # Files are ordered by mtime, but the record we want is ordered by
            # its own timestamp, and the two can disagree: a rollout touched a
            # minute ago may hold nothing newer than an hour-old reading, while
            # the file behind it was appended to more recently than that. So
            # don't stop at the first hit — look at a couple more files and keep
            # the newest record across all of them. Bounded, so this stays a
            # handful of reads and not a walk through the history.
            extra += 1
            if extra >= 3:
                break
    return best, best_at


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


def read_codex(now: float | None = None, files=None) -> dict:
    """Newest populated Codex rate-limit reading, with both staleness guards.

    ``now`` and ``files`` are injectable so the guards can be tested against a
    fixed clock and a synthetic record. Never raises.
    """
    now = time.time() if now is None else now
    try:
        limits, as_of = _newest_rate_limits(
            _rollout_files(CODEX_MAX_FILES) if files is None else files)
    except Exception as e:                                   # noqa: BLE001
        return _unavailable("codex", _scrub(f"cannot read Codex rollouts ({type(e).__name__})"))
    if limits is None:
        return _unavailable(
            "codex", "no Codex session has reported its limits recently")

    age = int(now - as_of) if as_of is not None else None

    windows = []
    for key, (kind, label) in _CODEX_WINDOWS.items():
        win = limits.get(key)
        if not win:
            continue
        # Guard 2 rests entirely on the reset time, so the shape of that field
        # decides everything. The invariant: **a window whose reset time we
        # cannot verify is never reported as current.** Without that, an
        # unparseable reset silently switches the guard off and a dead agent's
        # 100% is served as live — the precise bug this reader exists to avoid.
        resets = win.get("resets_at")
        resets_dt = None
        reset_unknown = False
        if resets is None:
            # Nothing to check against. The value may be current or may be a
            # dead number from a window that reset an hour ago; we cannot tell,
            # so we do not claim.
            reset_unknown = True
        elif isinstance(resets, (int, float)) and not isinstance(resets, bool):
            # Sanity-check before believing it. If this field ever changes
            # meaning — a duration instead of an epoch, say — the parse yields
            # 1970, every window reads "rolled over", and the chip goes
            # confidently and permanently blank. Fail the source instead.
            if abs(float(resets) - now) > RESET_SANITY_S:
                return _unavailable(
                    "codex", "Codex reported a reset time that is not a plausible "
                             "date — the rollout format has probably changed")
            try:
                resets_dt = datetime.fromtimestamp(resets, timezone.utc)
            except (OverflowError, OSError, ValueError):
                return _unavailable(
                    "codex", "Codex reported a reset time that could not be read "
                             "as a date — the rollout format has probably changed")
        else:
            # Present but not a number — an ISO string is the likeliest way this
            # field ever mutates. Fail the source loudly rather than let the
            # value fall through the guard unchecked.
            return _unavailable(
                "codex", "Codex reported a reset time in an unfamiliar format — "
                         "the rollout format has probably changed")
        # A window whose reset has passed has already rolled to 0. Its last value
        # is dead — preserve it separately, never report it as now.
        rolled = bool(resets_dt and resets_dt.timestamp() < now)
        value = _as_percent(win.get("used_percent"))
        # Withheld in both unverifiable cases: rolled over, or no reset to check.
        withheld = rolled or reset_unknown
        windows.append(_window(
            kind, label,
            percent=None if withheld else value,
            stale_percent=value if withheld else None,
            rolled_over=rolled,
            reset_unknown=reset_unknown,
            window_minutes=win.get("window_minutes"),
            resets_at=resets_dt.isoformat() if resets_dt else None,
            # Guard 1: each window carries the reading's age and decides for
            # itself whether that is still current — 30 minutes matters to the
            # five-hour window and is nothing to the weekly one.
            age_seconds=age,
        ))
    if not windows:
        return _unavailable("codex", "the newest Codex reading carries no windows")

    return {
        "source": "codex",
        "state": "ok",
        "error": None,
        "planType": limits.get("plan_type"),
        "asOf": as_of,
        "ageSeconds": age,
        # Source-level summary only: true when *every* window is still current.
        # The render path uses each window's own `trusted`.
        "trusted": all(w["trusted"] for w in windows),
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
        # Deep-copied: callers (the endpoint, the MCP tool) must not be able to
        # mutate the cache the poller owns.
        "sources": [copy.deepcopy(sources[k]) for k in ("claude", "codex")
                    if k in sources],
    }
    snap["alerts"] = alerts(snap["sources"])
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

    Thresholds come off ``percent`` only, never the payload's ``severity``: the
    investigation never observed a non-normal value, so branching on the
    escalated strings would be guessing.
    """
    out = []
    for src in sources:
        if src.get("state") != "ok":
            continue
        for win in src.get("windows") or []:
            if win.get("rolledOver"):
                continue
            pct = win.get("percent")
            if pct is None or pct < WARN_PERCENT:
                continue
            out.append({
                "source": src["source"],
                "kind": win["kind"],
                "label": win["label"],
                "model": win.get("model"),
                "percent": pct,
                "resetsAt": win.get("resetsAt"),
                "level": "alarm" if pct >= ALARM_PERCENT else "warn",
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
