"""SCRATCH probe — read plan-limit usage from outside a running agent.

Throwaway research artifact for the "expose agent model usage" investigation.
NOT wired into the dashboard, NOT imported by anything. Delete it or fold the
two reader functions into the hub once a direction is chosen.

Two independent sources, both readable with no TTY and without touching a
running agent:

  claude_plan_usage()  GET https://api.anthropic.com/api/oauth/usage
                       with the OAuth access token Claude Code already stores
                       in ~/.claude/.credentials.json. This is the endpoint
                       behind the /usage screen: five-hour and seven-day
                       window utilisation, reset times, per-model weekly
                       scopes. Undocumented/internal — see findings.md.

  codex_plan_usage()   Newest `token_count` event in the Codex rollout JSONL
                       under ~/.codex/sessions/. Carries cumulative token
                       usage, the model context window, and (when the upstream
                       API returns them) primary/secondary rate-limit windows
                       with used_percent and resets_at. No credentials needed.

Prints a redacted summary. Never prints, logs or returns a token.

Usage (`py`, not `python` — on this machine `python.exe` is the WindowsApps
stub and fails with "The system cannot find the path specified"):

    py scratch_usage_probe.py            # both sources
    py scratch_usage_probe.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CLAUDE_HOME = os.path.join(os.path.expanduser("~"), ".claude")
CREDENTIALS = os.path.join(CLAUDE_HOME, ".credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# The bearer token is the only thing this endpoint requires. Measured: with the
# beta header + a spoofed claude-cli User-Agent, with the beta header alone, and
# with neither (plain Python-urllib) all returned HTTP 200 and identical bodies.
# So no User-Agent spoof — it bought nothing, hardcoded a version string that
# rots, and there is no reason to impersonate the CLI on an endpoint we
# authenticate to legitimately. The beta header is kept only because it is what
# Claude Code itself sends; it is not required.
_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "Accept": "application/json",
}


# --------------------------------------------------------------------------
# Claude: account-level plan windows
# --------------------------------------------------------------------------

def _access_token() -> str:
    """The OAuth access token Claude Code stores on disk.

    Kept in a local only; never returned to callers, never printed. Claude Code
    refreshes it in place, so a poller just re-reads the file each time.
    """
    with open(CREDENTIALS, encoding="utf-8") as fh:
        creds = json.load(fh)["claudeAiOauth"]
    expires_at = creds.get("expiresAt")
    if expires_at and expires_at / 1000 < time.time():
        raise RuntimeError(
            "stored access token is past expiresAt; a Claude Code session has "
            "to run (or the refresh flow has to be implemented) to renew it"
        )
    return creds["accessToken"]


def claude_plan_usage() -> dict:
    """Live plan-allowance state for the whole Claude account.

    Account-wide, not per-agent: every Ensemble task on this machine burns the
    same windows, so this answers 'how close are we to a limit', not 'which
    agent spent it'.
    """
    req = urllib.request.Request(
        USAGE_URL, headers=dict(_HEADERS, Authorization="Bearer " + _access_token())
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarise_claude(usage: dict) -> dict:
    """The three numbers a dashboard chip would actually show."""
    out = {"windows": [], "spend_percent": (usage.get("spend") or {}).get("percent")}
    for limit in usage.get("limits") or []:
        scope = limit.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")) or None
        out["windows"].append({
            "kind": limit.get("kind"),
            "percent": limit.get("percent"),
            "severity": limit.get("severity"),
            "resets_at": limit.get("resets_at"),
            "model": model,
            "is_active": limit.get("is_active"),
        })
    return out


# --------------------------------------------------------------------------
# Codex: per-session token counts + rate-limit windows
# --------------------------------------------------------------------------

def _codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def codex_plan_usage(max_files: int = 5) -> dict | None:
    """Newest *populated* `token_count` event across recent Codex rollouts.

    Scans newest-first and stops at the first file that has one. Codex also
    emits `token_count` records whose `info` and rate-limit windows are all
    null (seen on turns that never reached the API), so a record only counts
    when it actually carries numbers.
    """
    pattern = os.path.join(_codex_home(), "sessions", "*", "*", "*", "*.jsonl")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)[:max_files]

    # Token totals and rate-limit windows go stale independently — the newest
    # record can carry `info` with null windows — so track the newest of each.
    newest_info = None
    newest_limits = None
    for path in files:
        with open(path, encoding="utf-8") as fh:
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
                rec["_rollout"] = os.path.basename(path)
                limits = payload.get("rate_limits") or {}
                if payload.get("info"):
                    newest_info = _later(newest_info, rec)
                if limits.get("primary") or limits.get("secondary"):
                    newest_limits = _later(newest_limits, rec)
        if newest_info and newest_limits:
            break

    if not (newest_info or newest_limits):
        return None
    anchor = newest_info or newest_limits
    return {
        "rollout": anchor["_rollout"],
        "timestamp": anchor.get("timestamp"),
        "info": (newest_info or {}).get("payload", {}).get("info"),
        "rate_limits": (newest_limits or {}).get("payload", {}).get("rate_limits"),
        "rate_limits_as_of": (newest_limits or {}).get("timestamp"),
    }


def _later(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    return candidate if candidate.get("timestamp", "") > current.get("timestamp", "") else current


def _age_seconds(timestamp: str | None, now: datetime) -> int | None:
    """How old a Codex reading is. Its timestamps are ISO-8601 with a 'Z'."""
    if not timestamp:
        return None
    try:
        return int((now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return None


def summarise_codex(probe: dict) -> dict:
    """used_percent / window / reset per rate-limit window, plus context fill.

    Codex numbers are a *last-write snapshot*, not a live feed: the rollout is
    only appended when that agent takes a turn, so a reading's age is unbounded
    and it goes stale exactly when an agent has stopped — which is the case we
    care about. Two guards, and any caller must honour both:

      * every reading carries `age_seconds`, so a render path can qualify it
        ("as of 68 min ago") or suppress it past a threshold;
      * a window whose `resets_at` is in the past has already rolled over, so
        its `used_percent` is reported as None rather than as the last value
        seen. Without this a dead agent's 100% is shown forever, and looks
        identical to a live one.
    """
    limits = probe.get("rate_limits") or {}
    now = datetime.now(timezone.utc)
    windows = []
    for key in ("primary", "secondary"):
        win = limits.get(key)
        if not win:
            continue
        resets = win.get("resets_at")
        resets_dt = datetime.fromtimestamp(resets, timezone.utc) if resets else None
        rolled = bool(resets_dt and resets_dt < now)
        windows.append({
            "which": key,
            "used_percent": None if rolled else win.get("used_percent"),
            "stale_used_percent": win.get("used_percent") if rolled else None,
            "window_rolled_over": rolled,
            "window_minutes": win.get("window_minutes"),
            "resets_at": resets_dt.isoformat() if resets_dt else None,
        })
    info = probe.get("info") or {}
    total = info.get("total_token_usage") or {}
    ctx = info.get("model_context_window")
    last = (info.get("last_token_usage") or {}).get("total_tokens")
    return {
        "as_of": probe.get("timestamp"),
        "rate_limits_as_of": probe.get("rate_limits_as_of"),
        "age_seconds": _age_seconds(probe.get("rate_limits_as_of"), now),
        "plan_type": limits.get("plan_type"),
        "windows": windows,
        "total_tokens": total.get("total_tokens"),
        "context_window": ctx,
        "last_turn_tokens": last,
        "context_fill_percent": round(100 * last / ctx, 1) if ctx and last else None,
    }


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="dump raw payloads")
    args = ap.parse_args()

    result = {}

    try:
        raw = claude_plan_usage()
        result["claude"] = raw if args.json else summarise_claude(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        result["claude"] = {"error": f"HTTP {exc.code}", "body": body}
    except Exception as exc:                                  # noqa: BLE001
        result["claude"] = {"error": f"{type(exc).__name__}: {exc}"}

    # Guarded the same way as the Claude branch: an unreadable or malformed
    # rollout must not take down the half of the probe that was working.
    try:
        probe = codex_plan_usage()
        if probe is None:
            result["codex"] = {"error": "no populated token_count event in the recent rollouts"}
        else:
            result["codex"] = probe if args.json else summarise_codex(probe)
    except Exception as exc:                                  # noqa: BLE001
        result["codex"] = {"error": f"{type(exc).__name__}: {exc}"}

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
