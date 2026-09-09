#!/usr/bin/env python3
"""
Ensemble: a multi-agent collaboration & coordination dashboard for coding agents.

Drives and coordinates multiple coding agents (Claude Code, Codex, …) — running
them headless, letting them collaborate through a shared room, and managing every
session from one surface. Agent-agnostic: each agent is a pluggable adapter.

Run:
    python3 dashboard.py [--port 8765]

Then open http://127.0.0.1:8765 in Chrome.

Per-agent data is read from each agent's own store, e.g. for Claude:
  ~/.claude/sessions/<pid>.json               — one per running session (live state)
  ~/.claude/projects/<slug>/<session-id>.jsonl — full transcripts (history)
Ensemble's own state lives in ~/.ensemble.
"""
from __future__ import annotations

import hmac
import json
import os
import plistlib
import random
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# All OS-specific behavior (terminal control, process introspection, desktop
# integration) lives behind a platform backend, selected by sys.platform.
from backends import get_backend
from backends.base import (
    HOME, DASHBOARD_DIR, PRESETS_DIR, CS_ROOT, PROJECTS_ROOT, APP_NAME,
    NUMBERED_RE as _NUMBERED_RE,
)
from backends.shared import (
    SESS_DIR, AGENT_SESS_DIR, RENAME_WORKSPACE, GEOMETRIES_FILE, PERMISSION_MODE,
    claude_cmd, claude_cmd_args, load_geometries, save_geometries, save_geometry,
    is_workspace_cwd, read_session_files, read_agent_session_files,
)
# Agent-type abstraction (WHAT runs in a session), orthogonal to the OS backend
# (WHERE it runs). Codex discovery + the claude/codex registry live here.
import agents
# Multi-agent chat rooms (pairing live sessions into a collaborating "duo").
import backup
import chatroom
# Task-management MCP tools (ensemble_*) served next to the chat tools.
import ensemble_tools
# Headless PTY runtime — dashboard-owned agent processes streamed to the browser.
from backends import ptyrun

BACKEND = get_backend()
# The tools module calls back into this module's task/launch primitives; the
# dashboard runs as __main__, so hand it the live module object.
ensemble_tools.bind(sys.modules[__name__])

# --- Remote-access gate ------------------------------------------------------
# When the server is bound to anything other than loopback (e.g. exposed on a
# Tailscale interface so other machines can reach it), every non-loopback
# request must present this access token. Loopback requests (the local browser
# and the agents' own /mcp calls on 127.0.0.1) are always allowed, so nothing
# local changes. Empty token => open (the historical local-only behaviour).
ACCESS_TOKEN = ""
TOKEN_COOKIE = "ensemble_token"
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", ""}


def _addr_is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    if h.startswith("::ffff:"):
        h = h[7:]
    return h in _LOOPBACK


def _detect_tailscale_ip() -> str | None:
    """Best-effort lookup of this machine's Tailscale IPv4, for --bind tailscale."""
    candidates = [
        "tailscale",
        r"C:\Program Files\Tailscale\tailscale.exe",
        "/usr/bin/tailscale",
        "/usr/local/bin/tailscale",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    for exe in candidates:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True,
                                 text=True, timeout=5)
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
        ip = (out.stdout or "").strip().splitlines()
        if ip and ip[0].strip():
            return ip[0].strip()
    return None


# Live-session reading + workspace filtering live in backends.shared (so the
# backends can use them without importing dashboard.py). Aliased to the private
# names the rest of this module already uses.
_read_session_files = read_session_files
_read_agent_session_files = read_agent_session_files
_is_workspace_cwd = is_workspace_cwd


def _room_is_live(room: dict) -> bool:
    """True if any of a room's agents still has a live PTY. A dashboard restart
    orphans the PTYs (they're the server's children, registry is in-memory), so
    a pre-restart room reads as not-live — we mark those 'ended'."""
    for pp in room.get("participants", []):
        if pp.get("kind") != "agent":
            continue
        pid = pp.get("ptyId")
        if pid:
            sess = ptyrun.get(pid)
            if sess and sess.alive():
                return True
    return False


def _room_has_live_pty(rid: str) -> bool:
    """True if any live PTY says it belongs to this room.

    :func:`_room_is_live` asks the room record which PTYs its agents hold, and a
    room record can be wrong in both directions — it keeps a dead ``ptyId``
    after a stop, and it loses one if a participant is rewritten. The PTY list
    is the authority on what is actually running, so anything that must not
    touch a working agent asks here as well."""
    for info in ptyrun.list_sessions():
        if info.get("alive") and (info.get("meta") or {}).get("room") == rid:
            return True
    return False


def _room_has_linked_agent(room: dict) -> bool:
    """True if the room holds an agent in a visible terminal we don't own.

    ``/api/room/create`` adopts agents already running in their own terminals:
    those participants carry a real ``pid`` and no ``ptyId``, and no PTY of ours
    reports them — so neither PTY check sees them. They are still someone's
    working agent, and rewriting their token underneath them is exactly the
    thing we refuse to do."""
    for pp in room.get("participants", []):
        if pp.get("kind") == "agent" and pp.get("pid") and not pp.get("ptyId"):
            return True
    return False


# Rooms whose codex session id we've recently looked for and not found, so a
# poll loop doesn't rescan the rollout dir every second: {roomId: last try}.
_CODEX_SID_TRIED: dict[str, float] = {}
_CODEX_SID_RETRY = 5.0


def _backfill_codex_session_ids(room: dict) -> None:
    """Fill in a codex participant's missing ``sessionId``.

    Claude takes a caller-supplied ``--session-id``, so the dashboard knows a
    Claude agent's id the moment it launches. Codex mints its own and prints it
    nowhere, so a freshly spawned codex agent's ``sessionId`` starts empty —
    which left a solo codex session with no transcript to render (an empty
    balloon chat) and no conversation to resume. Codex does write a rollout file
    tagged with its cwd, so once it exists we can recover the id from there and
    persist it on the room.
    """
    pending = [pp for pp in room.get("participants", [])
               if pp.get("kind") == "agent" and pp.get("agent") == "codex"
               and not (pp.get("sessionId") or "").strip()]
    if not pending:
        return
    rid = room.get("id", "")
    now = time.time()
    if now - _CODEX_SID_TRIED.get(rid, 0.0) < _CODEX_SID_RETRY:
        return
    _CODEX_SID_TRIED[rid] = now
    cx = agents.get_agent("codex")
    if cx is None:
        return
    # Only rollouts written after the room was created can belong to it; that
    # bound keeps this to the few files codex has touched since the launch.
    since = float(room.get("createdAt") or 0.0)
    # Two codex agents can share one workspace, so never hand the same rollout
    # to both — a claimed id is off the table for the rest of the room.
    taken = {(pp.get("sessionId") or "").strip()
             for pp in room.get("participants", [])} - {""}
    found = {}
    for pp in pending:
        cwd = pp.get("cwd") or room.get("cwd") or ""
        try:
            sid = cx.latest_session_id_for_cwd(cwd, since=since)
        except Exception:
            sid = ""
        if sid and sid not in taken:
            taken.add(sid)
            found[pp["identity"]] = sid
            pp["sessionId"] = sid
    if not found:
        return
    # Persist on the full room (the caller holds the token-stripped view).
    full = chatroom.get_room(rid, public=False)
    if full is None:
        return
    for pp in full.get("participants", []):
        if pp.get("identity") in found:
            pp["sessionId"] = found[pp["identity"]]
    chatroom.update_room(full)


def _annotate_room_liveness(room: dict) -> dict:
    """Add a `live` flag: True if any agent PTY is running. Not live simply means
    the session isn't running — there's no separate 'ended' state."""
    room["live"] = _room_is_live(room)
    _backfill_codex_session_ids(room)
    return room


def _allocate_agent_identity(agent_key: str) -> str:
    """Pick a unique identity for a newly launched agent session, e.g.
    ``codex`` then ``codex-2`` if one is already live. Identities are the
    routing handles the chat/MCP layer will address."""
    existing = {r.get("identity", "") for r in _read_agent_session_files()}
    if agent_key not in existing:
        return agent_key
    i = 2
    while f"{agent_key}-{i}" in existing:
        i += 1
    return f"{agent_key}-{i}"

# Kept for /api/config display only; parallel-install (--instance) isolation is
# not wired up on the cross-platform build.
INSTANCE = ""

PROJ_DIR = HOME / ".claude" / "projects"           # shared (claude-owned)
LABELS_FILE = DASHBOARD_DIR / "labels.json"
FAVORITE_THEMES_FILE = DASHBOARD_DIR / "favorite_themes.json"
JIRA_LINKS_FILE = DASHBOARD_DIR / "jira_links.json"  # {sid: [tickets]} user-added links (override scan)
JIRA_UNLINKS_FILE = DASHBOARD_DIR / "jira_unlinks.json"  # {sid: [tickets]} user-removed from auto-scan
PARENTS_FILE = DASHBOARD_DIR / "parents.json"  # child_sid -> parent_sid
PINNED_FILE = DASHBOARD_DIR / "pinned.json"    # list of pinned session ids
CATEGORIES_FILE = DASHBOARD_DIR / "categories.json"  # {sid: "category name"}
KNOWN_CATEGORIES_FILE = DASHBOARD_DIR / "known_categories.json"  # explicit category list
ARCHIVED_FILE = DASHBOARD_DIR / "archived.json"      # list of archived session ids
SETTINGS_FILE = DASHBOARD_DIR / "settings.json"      # user preferences (openMode, …)
PROJECTS_FILE = DASHBOARD_DIR / "projects.json"      # registered projects: [{id,name,path,isGit,createdAt}]
SESSION_PROJECTS_FILE = DASHBOARD_DIR / "session_projects.json"  # {sessionId|roomId: projectId}
STATIC_DIR = Path(__file__).parent
# Default log location per platform (macOS keeps the historical ~/Library/Logs
# path; Windows/Linux log under the dashboard state dir, matching the CLIs).
if sys.platform == "darwin":
    DEFAULT_LOG_FILE = HOME / "Library" / "Logs" / "ensemble.log"
else:
    DEFAULT_LOG_FILE = DASHBOARD_DIR / "logs" / "ensemble.log"
_LOG_FILE = None  # set by main() when --log is passed
# HOME, DASHBOARD_DIR, PRESETS_DIR, CS_ROOT, _NUMBERED_RE come from backends.base;
# SESS_DIR, RENAME_WORKSPACE, GEOMETRIES_FILE from backends.shared.
_SLUG_NONALNUM = re.compile(r"[^a-z0-9]+")


# ---------- transcript parsers (mirrored from claude-sessions) ----------

def pid_alive(pid: int) -> bool:
    return BACKEND.process_alive(pid)


_CWD_CACHE: dict[str, str] = {}


def cwd_of(path: Path) -> str:
    key = str(path)
    if key in _CWD_CACHE:
        return _CWD_CACHE[key]
    cwd = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(d.get("cwd"), str):
                    cwd = d["cwd"]
                    break
    except FileNotFoundError:
        pass
    _CWD_CACHE[key] = cwd
    return cwd


# ---------- cost estimation ----------
#
# Anthropic doesn't publish a machine-readable pricing endpoint, so this table
# is a snapshot. If Anthropic changes their prices, edit the numbers below —
# the calculation is a straight (tokens / 1e6) * $-per-M multiply.
#
# Tuple is (input, output, cache_write, cache_read) in USD per million tokens.
# Match is by MODEL-ID PREFIX so version suffixes (…-20251001, …-4-7 vs -4-8)
# fall into the right bucket without an entry per revision.
#
# Ephemeral cache writes have a duration surcharge (5m vs 1h). We treat both
# as the 1h price since that's the conservative estimate; if a session hits
# only 5m cache the number will be slightly high.
_MODEL_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4":   (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-5": ( 3.0, 15.0,  3.75, 0.30),
    "claude-sonnet-4": ( 3.0, 15.0,  3.75, 0.30),
    "claude-haiku-4":  ( 1.0,  5.0,  1.25, 0.10),
    "claude-fable-5":  ( 3.0, 15.0,  3.75, 0.30),  # assumed sonnet-tier
}


def _pricing_for_model(model_id: str) -> tuple[float, float, float, float] | None:
    if not model_id:
        return None
    for prefix, prices in _MODEL_PRICING.items():
        if model_id.startswith(prefix):
            return prices
    return None


_COST_CACHE: dict[Path, tuple[float, dict]] = {}  # jsonl_path -> (mtime, result)


def compute_session_cost(jsonl_path: Path | None) -> dict:
    """Walk a transcript's assistant turns, aggregate usage per model, apply
    per-model prices. Returns {dollars, tokens, byModel}. Cached by mtime so
    replays over a static file are near-free."""
    empty = {"dollars": 0.0, "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0}, "byModel": {}}
    if not jsonl_path:
        return empty
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return empty
    cached = _COST_CACHE.get(jsonl_path)
    if cached and cached[0] == mtime:
        return cached[1]
    by_model: dict[str, dict[str, int]] = {}
    # A single API response can appear as several `type: "assistant"` lines
    # (one per content block: text, tool_use, follow-up text). Every one of
    # those lines carries the SAME cumulative usage for the underlying
    # response — summing them all triples the total. Dedupe by requestId.
    seen_requests: set[str] = set()
    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                rid = d.get("requestId") or ""
                if rid:
                    if rid in seen_requests:
                        continue
                    seen_requests.add(rid)
                model_id = msg.get("model", "") or "unknown"
                bucket = by_model.setdefault(model_id, {
                    "input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0,
                })
                bucket["input"] += int(usage.get("input_tokens") or 0)
                bucket["output"] += int(usage.get("output_tokens") or 0)
                bucket["cacheWrite"] += int(usage.get("cache_creation_input_tokens") or 0)
                bucket["cacheRead"] += int(usage.get("cache_read_input_tokens") or 0)
    except OSError:
        return empty
    total_dollars = 0.0
    total_tokens = {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0}
    breakdown = {}
    for model_id, tokens in by_model.items():
        for k, v in tokens.items():
            total_tokens[k] += v
        prices = _pricing_for_model(model_id)
        if prices:
            i, o, cw, cr = prices
            cost = (
                tokens["input"] * i
                + tokens["output"] * o
                + tokens["cacheWrite"] * cw
                + tokens["cacheRead"] * cr
            ) / 1_000_000.0
            total_dollars += cost
            breakdown[model_id] = {"dollars": round(cost, 4), "tokens": tokens}
        else:
            breakdown[model_id] = {"dollars": 0.0, "tokens": tokens, "unknownPricing": True}
    result = {
        "dollars": round(total_dollars, 4),
        "tokens": total_tokens,
        "byModel": breakdown,
    }
    _COST_CACHE[jsonl_path] = (mtime, result)
    return result


def find_transcript(session_id: str) -> Path | None:
    hits = list(PROJ_DIR.glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def compute_room_cost(room: dict) -> dict:
    """Aggregate cost across a collaboration's agent members — sum each member's
    transcript cost and merge the per-model breakdowns — so a room shows the same
    Cost section a single-agent session does. Codex members have no Claude-format
    transcript (and no pricing entry), so they contribute 0; Claude members carry
    the real numbers. Same shape as compute_session_cost."""
    total = {
        "dollars": 0.0,
        "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0},
        "byModel": {},
    }
    for pp in (room.get("participants") or []):
        if pp.get("kind") != "agent":
            continue
        sid = pp.get("sessionId")
        path = find_transcript(sid) if sid else None
        c = compute_session_cost(path)
        total["dollars"] += c.get("dollars", 0.0)
        for k, v in (c.get("tokens") or {}).items():
            total["tokens"][k] = total["tokens"].get(k, 0) + v
        for mid, mv in (c.get("byModel") or {}).items():
            b = total["byModel"].get(mid)
            if b is None:
                b = {"dollars": 0.0,
                     "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0}}
                total["byModel"][mid] = b
            b["dollars"] = round(b["dollars"] + mv.get("dollars", 0.0), 4)
            for k, v in (mv.get("tokens") or {}).items():
                b["tokens"][k] = b["tokens"].get(k, 0) + v
            if mv.get("unknownPricing"):
                b["unknownPricing"] = True
    total["dollars"] = round(total["dollars"], 4)
    return total


def _extract_text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        for x in c:
            if isinstance(x, dict) and x.get("type") == "text":
                return x.get("text", "")
    return None


def iter_user_turns(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user" or d.get("isMeta"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                text = _extract_text(msg.get("content"))
                if not text:
                    continue
                text = text.strip()
                if not text or text.startswith("<") or text.startswith("Caveat:"):
                    continue
                yield d.get("timestamp", ""), " ".join(text.split())
    except FileNotFoundError:
        return


def first_last_user(path: Path, max_len: int = 200):
    first = last = ""
    count = 0
    for _, text in iter_user_turns(path):
        count += 1
        snippet = text[:max_len] + ("…" if len(text) > max_len else "")
        if not first:
            first = snippet
        last = snippet
    if last == first:
        last = ""
    return first, last, count


# ---------- labels (rename) ----------

def load_labels() -> dict[str, str]:
    try:
        return json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_labels(labels: dict[str, str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LABELS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(labels, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(LABELS_FILE)


def load_known_categories() -> list[str]:
    try:
        d = json.loads(KNOWN_CATEGORIES_FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            return [x for x in d if isinstance(x, str) and x]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_known_categories(lst) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = KNOWN_CATEGORIES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(set(lst)), indent=2), encoding="utf-8")
    tmp.replace(KNOWN_CATEGORIES_FILE)


def all_known_categories() -> list[str]:
    """Union of explicit category names and any names assigned to sessions."""
    known = set(load_known_categories())
    for v in load_categories().values():
        if v:
            known.add(v)
    return sorted(known)


def load_archived() -> set[str]:
    try:
        d = json.loads(ARCHIVED_FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            return {x for x in d if isinstance(x, str)}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return set()


def save_archived(arch: set[str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(arch), indent=2), encoding="utf-8")
    tmp.replace(ARCHIVED_FILE)


# User-facing preferences. Persisted to settings.json; the whitelist of keys
# below is what the /api/settings PUT endpoint accepts.
_SETTINGS_DEFAULTS = {
    "openMode": "window",   # "window" (new iTerm window) | "tab" (new tab in front window)
    "defaultModel": "",     # e.g. "opus" | "sonnet" | "haiku" | "fable" | full ID; empty = claude default
    "operatorNickname": "", # what the agents call you; empty = fall back to git user.name
    # Projects backup (see backup.py). Only the remote URL is stored — never credentials.
    "backupRemote": "",      # git remote for the projects root; empty = commit locally only
    "backupIntervalMin": 60, # how often to commit (+push if a remote is set)
    "backupEnabled": False,  # scheduler on/off
}
_SETTINGS_ALLOWED_VALUES = {
    "openMode": {"window", "tab"},
    # defaultModel is free-form — anything claude --model accepts.
}


def load_settings() -> dict:
    """Merge saved settings over the defaults so a missing key doesn't crash
    the caller after we add new preferences later."""
    out = dict(_SETTINGS_DEFAULTS)
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k in _SETTINGS_DEFAULTS:
                    out[k] = v
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return out


def save_settings(settings: dict) -> dict:
    """Persist only known keys with validated values; ignore extras."""
    current = load_settings()
    for k, v in settings.items():
        if k not in _SETTINGS_DEFAULTS:
            continue
        allowed = _SETTINGS_ALLOWED_VALUES.get(k)
        if allowed and v not in allowed:
            continue
        # Free-form strings still get shape checks — the value is passed
        # verbatim as claude's --model argument.
        if k == "defaultModel":
            if not isinstance(v, str) or len(v) > 80:
                continue
            v = v.strip()
        if k == "operatorNickname":
            if not isinstance(v, str) or len(v) > 60:
                continue
            v = v.strip()
        if k == "backupRemote":
            if not isinstance(v, str) or len(v) > 300 or any(ch in v for ch in " \n\r\t"):
                continue
            v = v.strip()
        if k == "backupIntervalMin":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            v = max(5, min(1440, v))
        if k == "backupEnabled":
            v = bool(v)
        current[k] = v
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    return current


_GIT_NAME_CACHE: list = []
def operator_name() -> str:
    """What the collaborating agents should call the person running the dashboard:
    an explicit nickname from settings, else the git user.name, else a neutral
    fallback. The git lookup is cached for the process lifetime."""
    nick = load_settings().get("operatorNickname", "")
    if isinstance(nick, str) and nick.strip():
        return nick.strip()
    if not _GIT_NAME_CACHE:
        name = ""
        try:
            out = subprocess.run(["git", "config", "user.name"],
                                 capture_output=True, text=True, timeout=3)
            name = (out.stdout or "").strip()
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            name = ""
        _GIT_NAME_CACHE.append(name)
    return _GIT_NAME_CACHE[0] or "the user"


# ---------------------------------------------------------------------------
# Team roles — each agent in a collaboration plays a role with a behavior
# charter, so the team divides labor like a real software team instead of every
# agent racing to do everything. The product owner is always the human (`user`).
# ---------------------------------------------------------------------------
ROLE_TITLES = {
    "engineer": "engineer",
    "reviewer": "reviewer",
    "planner": "planner",
    "pair": "collaborator",
    "": "collaborator",
}


def role_title(role: str) -> str:
    return ROLE_TITLES.get(role, (role or "collaborator"))


def role_charter(role: str, teammates: list) -> str:
    """The behavior contract for a role. ``teammates`` is the OTHER agents as
    [{identity, role}] so the charter can name the engineer/reviewer directly."""
    eng = next((t["identity"] for t in teammates if t.get("role") == "engineer"), "the engineer")
    rev = next((t["identity"] for t in teammates if t.get("role") == "reviewer"), "the reviewer")
    if role == "engineer":
        return (
            f"You own design and implementation. Break the product owner's request "
            f"into steps and do the actual work — write the code, the plan, the diffs. "
            f"When a piece is ready, send it to {rev} for review before you consider it "
            f"done, and fold in the findings you get back. Go back to the product owner "
            f"only for requirements decisions or final sign-off."
        )
    if role == "reviewer":
        return (
            f"You are the reviewer. Do NOT design, plan, or implement — that is {eng}'s "
            f"job. Wait until {eng} sends a deliverable (a plan, a diff, code, or a "
            f"claim); then review it: check it against the product owner's requirements, "
            f"hunt for bugs, edge cases, risks, and gaps, verify claims by reading the "
            f"actual code or running tests, and reply with a concise, structured critique "
            f"— what is correct, what is wrong, what is missing, and what to change. If "
            f"anyone asks you to build something, decline and redirect: {eng} builds, you "
            f"review. When the product owner posts to the whole team, do NOT answer first "
            f"— let {eng} respond and produce; weigh in only once there is a deliverable "
            f"to review, or when the product owner addresses you directly. Never start "
            f"work on the task ahead of {eng}."
        )
    if role == "planner":
        return (
            "You are the planner. Do NOT implement — you shape the work. Study the "
            "project (its code, notes and existing tasks — ensemble_whoami, "
            "ensemble_list_tasks, ensemble_get_task), break the product owner's goal "
            "into well-scoped tasks, and create them with ensemble_create_task as "
            "DRAFTS with complete specs (goal, context, acceptance criteria, "
            "constraints, suggested agents/roles). Keep each task independently "
            "workable and small enough for one session. Present the plan to the "
            "product owner for review before starting anything; only start tasks "
            "when they ask you to. Amend or delete drafts as the plan evolves."
        )
    if role and role not in ROLE_TITLES:
        # Custom role: the value itself is the charter the user wrote.
        return role
    return (
        "Collaborate as an equal partner: share findings and critique, and converge "
        "on the best solution together."
    )


def collab_briefing(ident: str, role: str, teammates: list, task: str,
                    wire_mcp: bool = True) -> str:
    """Assemble a role-aware collaboration briefing. ``teammates`` is the other
    agents as [{identity, role}]."""
    op = operator_name()
    title = role_title(role)
    mates = ", ".join(f"{t['identity']} (the {role_title(t.get('role',''))})"
                      for t in teammates) or "your partner"
    parts = [
        f"You are '{ident}', the {title} on a small software team.",
        role_charter(role, teammates),
        (f"Team: {mates}. The product owner is {op} — they set requirements and "
         f"priorities and accept or reject the work; reach them with chat_send "
         f"to=\"user\"."),
        ("Coordinate ONLY through the 'ensemble' MCP chat tools: chat_send to hand "
         "off your turn (end each turn by sending a teammate a message), chat_read "
         "to read replies. Write every chat message as GitHub-flavored Markdown "
         "(headings, bullet and numbered lists, inline code for identifiers/paths/"
         "commands, fenced code blocks for code, and tables where useful)."),
        ("The same MCP server also offers the ensemble_* task tools (list/read/"
         "create/amend/start/stop/delete tasks in this project); the 'ensemble' "
         "skill explains how to use them."),
    ]
    if role == "reviewer":
        eng = next((t["identity"] for t in teammates if t.get("role") == "engineer"), "the engineer")
        parts.append(f"Start by acknowledging your role in one line, then wait for "
                     f"{eng}'s first deliverable — do not begin working the task yourself.")
    else:
        parts.append("Begin now by sending a teammate your initial plan or approach.")
    parts.append(f"TASK:\n{task}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Projects — group sessions/rooms by their git repo root (the "project").
# ---------------------------------------------------------------------------
_GIT_ROOT_CACHE: dict = {}
_GIT_STAT_CACHE: dict = {}   # root -> (ts, changed_count)


def git_root(cwd: str) -> str:
    """The git top-level for ``cwd`` (cached), or ``cwd`` itself if not a repo."""
    if not cwd:
        return ""
    if cwd in _GIT_ROOT_CACHE:
        return _GIT_ROOT_CACHE[cwd]
    root = cwd
    try:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=4)
        r = (out.stdout or "").strip()
        if r:
            root = os.path.normpath(r)
    except (OSError, subprocess.SubprocessError):
        pass
    _GIT_ROOT_CACHE[cwd] = root
    return root


def git_changed_count(root: str) -> int:
    """Number of changed (dirty) files in ``root``; cached ~12s (git status is
    slow on large repos, and dirtiness doesn't change second-to-second)."""
    now = time.time()
    hit = _GIT_STAT_CACHE.get(root)
    if hit and now - hit[0] < 12:
        return hit[1]
    n = 0
    try:
        out = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6)
        n = sum(1 for ln in (out.stdout or "").splitlines() if ln.strip())
    except (OSError, subprocess.SubprocessError):
        n = 0
    _GIT_STAT_CACHE[root] = (now, n)
    return n


def path_is_git(path: str) -> bool:
    """True if ``path`` is (inside) a git repo whose top-level is ``path`` itself,
    or simply contains a .git entry. Cheap: prefer the .git check, fall back to git."""
    if not path:
        return False
    # A repo root has a .git entry (a directory, or a file for a worktree).
    # Do NOT fall back to comparing git_root(): for a folder with no repo
    # anywhere above it, git_root() returns the folder itself, which would
    # misreport a plain folder as a repo (seen with a project inside
    # ~/EnsembleProjects before that root was initialised).
    try:
        return (Path(path) / ".git").exists()
    except OSError:
        return False


def load_projects() -> list[dict]:
    """The registered projects — explicit, never guessed. Two sources, merged
    (deduped by normalized path):
      1. layout v2: every <PROJECTS_ROOT>/<dir>/project.json — the project's
         identity lives WITH its data, so a cloned projects root is
         self-describing on a new machine (no sidecar to restore);
      2. projects.json — external projects (absolute paths outside the root,
         e.g. a code repo) and legacy entries.
    Each is {id, name, path, isGit, createdAt}."""
    out: list[dict] = []
    seen: set[str] = set()

    def _add(p: dict) -> None:
        key = os.path.normcase(os.path.normpath(p["path"]))
        if key not in seen and p["id"] not in seen:
            seen.add(key); seen.add(p["id"])
            out.append(p)

    try:
        if PROJECTS_ROOT.is_dir():
            for d in sorted(PROJECTS_ROOT.iterdir()):
                pj = d / "project.json"
                if not d.is_dir() or not pj.is_file():
                    continue
                try:
                    meta = json.loads(pj.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict) or not meta.get("id"):
                    continue
                home = os.path.normpath(str(d))
                # A project registered from an external code folder keeps its
                # tasks/notes/chats HERE (its home) while ``path`` stays the code.
                code = (meta.get("code") or "").strip()
                path = os.path.normpath(code) if code and os.path.isdir(code) else home
                _add({"id": meta["id"], "name": meta.get("name") or d.name,
                      "path": path, "home": home, "isGit": path_is_git(path),
                      "createdAt": meta.get("createdAt", 0)})
    except OSError:
        pass
    try:
        d = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            for p in d:
                if isinstance(p, dict) and p.get("id") and p.get("path"):
                    _add(p)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return out


def save_projects(projects: list[dict]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROJECTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    tmp.replace(PROJECTS_FILE)


def register_project(path: str, name: str = "") -> tuple[bool, dict, str]:
    """Register a folder as a project (idempotent by normalized path). The folder
    is created if missing. Returns (ok, project, message)."""
    raw = (path or "").strip()
    if not raw:
        return False, {}, "empty path"
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        # A bare name is a project under the projects root — NEVER relative to
        # the hub's cwd (that once created a project folder inside the app repo).
        if any(sep in raw for sep in ("/", "\\")) or raw in (".", "..") or ".." in raw.split("/"):
            return False, {}, "use a plain project name, or an absolute path"
        p = PROJECTS_ROOT / raw
    norm = os.path.normpath(str(p))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, {}, f"cannot create/access folder: {e}"
    projects = load_projects()
    for existing in projects:
        if os.path.normcase(os.path.normpath(existing["path"])) == os.path.normcase(norm):
            return True, existing, "already registered"
    proj = {
        "id": "proj-" + uuid.uuid4().hex[:8],
        "name": (name.strip() or os.path.basename(norm.rstrip("/\\")) or norm),
        "path": norm,
        "isGit": path_is_git(norm),
        "createdAt": int(time.time()),
    }
    projects.append(proj)
    save_projects(projects)
    if _in_projects_root(norm):
        # Layout v2: the project's identity lives WITH its data, so a clone of
        # the projects root on a new machine is self-describing.
        try:
            (Path(norm) / "project.json").write_text(
                json.dumps({k: proj[k] for k in ("id", "name", "createdAt")}, indent=2),
                encoding="utf-8")
        except OSError:
            pass
    return True, proj, "ok"


def unregister_project(project_id: str) -> bool:
    """Drop a project from the registry (does NOT touch its folder or sessions)."""
    projects = load_projects()
    kept = [p for p in projects if p.get("id") != project_id]
    if len(kept) == len(projects):
        return False
    save_projects(kept)
    return True


def _file_ref_bases(room_id: str = "", cwd: str = "") -> list[str]:
    """Folders a relative file mention ("docs/plan.md") may be relative to, in
    order: an explicit cwd, then the room's own folders (cwd, task dir, each
    agent's cwd), then its project's code folder and home."""
    bases: list[str] = []

    def add(x) -> None:
        x = (x or "").strip() if isinstance(x, str) else ""
        if x and x not in bases:
            bases.append(x)

    add(cwd)
    if room_id:
        try:
            rm = chatroom.get_room(room_id) or {}
        except Exception:
            rm = {}
        add(rm.get("cwd")); add(rm.get("taskDir")); add(rm.get("sharedCwd"))
        for part in rm.get("participants") or []:
            if isinstance(part, dict):
                add(part.get("cwd"))
        pid = rm.get("projectId") or load_session_projects().get(room_id)
        if pid:
            for pj in load_projects():
                if pj.get("id") == pid:
                    add(pj.get("path"))
                    add(pj.get("home") or project_home(pj, create=False))
                    break
    return bases


def resolve_file_ref(raw: str, room_id: str = "", cwd: str = "") -> Path | None:
    """The file an agent mentioned: absolute paths as-is; a relative one is
    tried against ``_file_ref_bases`` and the first existing file wins."""
    raw = (raw or "").strip().strip("\"'")
    if not raw:
        return None
    try:
        fp = Path(os.path.expanduser(raw))
    except (ValueError, OSError):
        return None
    if fp.is_absolute():
        return fp if fp.is_file() else None
    rel = raw.replace("/", os.sep)
    bases = _file_ref_bases(room_id, cwd)
    for b in bases:
        try:
            cand = Path(os.path.expanduser(b)) / rel
            if cand.is_file():
                return cand
        except (ValueError, OSError):
            continue
    # "navigation.html" mentioned after "docs/ui/sketches/console.html": the
    # agent dropped the folder. Look for the name (or trailing path) under the
    # same folders, bounded, and take the shallowest / newest match.
    return _find_file_by_tail(rel, bases)


_FILE_SEARCH_SKIP = {"node_modules", ".venv", "venv", "__pycache__", "target", "dist",
                     "build", ".idea", ".tox", "site-packages", ".git"}


def _find_file_by_tail(rel: str, bases: list[str], max_depth: int = 6,
                       max_entries: int = 40000) -> Path | None:
    """First file under any base whose path ends with ``rel`` (a bare name or a
    partial path like ``sketches/navigation.html``). Bounded walk: hidden and
    dependency folders are skipped, depth and total entries are capped."""
    parts = [x for x in rel.replace("/", os.sep).split(os.sep) if x and x != "."]
    if not parts or ".." in parts:
        return None
    name = parts[-1].lower()
    tail = os.sep.join(parts).lower()
    best: tuple[int, float, Path] | None = None
    seen_roots: set[str] = set()
    budget = max_entries
    for b in bases:
        try:
            root = os.path.expanduser(b)
            key = os.path.normcase(os.path.normpath(root))
        except (ValueError, OSError):
            continue
        if key in seen_roots or not os.path.isdir(root):
            continue
        seen_roots.add(key)
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d.lower() not in _FILE_SEARCH_SKIP] \
                if depth < max_depth else []
            budget -= len(filenames) + len(dirnames)
            for fn in filenames:
                if fn.lower() != name:
                    continue
                full = os.path.join(dirpath, fn)
                if not full.lower().endswith(tail):
                    continue
                try:
                    mtime = os.stat(full).st_mtime
                except OSError:
                    continue
                cand = (depth, -mtime, Path(full))
                if best is None or cand[:2] < best[:2]:
                    best = cand
            if budget <= 0:
                break
        if best is not None and best[0] == 0:
            break
    return best[2] if best else None


def load_session_projects() -> dict[str, str]:
    """Sidecar map {sessionId|roomId: projectId} — the recorded membership link.
    Explicit, so drill-in is never guessed (the Phase-A bug). Porting a legacy
    session = adding an entry here."""
    try:
        d = json.loads(SESSION_PROJECTS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str) and v}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_session_projects(m: dict[str, str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_PROJECTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(SESSION_PROJECTS_FILE)


def assign_session_project(sid: str, project_id: str) -> None:
    m = load_session_projects()
    if project_id:
        m[sid] = project_id
    else:
        m.pop(sid, None)
    save_session_projects(m)


def _project_for_cwd(cwd: str, projects: list[dict]) -> str:
    """Best-effort membership for a session with no explicit link: the registered
    project whose folder contains the session's cwd (longest match wins)."""
    if not cwd:
        return ""
    c = os.path.normcase(os.path.normpath(cwd))
    best_id, best_len = "", -1
    for p in projects:
        pp = os.path.normcase(os.path.normpath(p["path"]))
        if c == pp or c.startswith(pp + os.sep):
            if len(pp) > best_len:
                best_id, best_len = p["id"], len(pp)
    return best_id


def _write_task_json(folder: str, data: dict) -> None:
    """The task's portable record (spec, agents, ids) — lives in the task folder
    so a backup of the projects root carries it."""
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / "task.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    except OSError:
        pass


def _export_task_chats() -> int:
    """Backup hook: write every project-linked room's chat (PUBLIC view — tokens
    are never included) into its task folder as chat.json, so the backup carries
    the knowledge, not just the files. Rooms whose folder is outside the projects
    root (linked legacy sessions, inplace tasks) go under <project>/_linked/<room>/."""
    links = load_session_projects()
    projects = {p["id"]: p for p in load_projects()}
    n = 0
    for rid, pid in links.items():
        if not rid.startswith("room-"):
            continue
        pj = projects.get(pid)
        if not pj:
            continue
        try:
            room = chatroom.get_room(rid)            # public=True → no tokens
        except Exception:
            room = None
        if not room:
            continue
        home = project_home(pj)                      # created on demand
        task_dir = room.get("taskDir") or ""
        cwd = room.get("cwd") or ""
        own_folder = cwd and _within(cwd, home) and \
            os.path.normcase(os.path.normpath(cwd)) != os.path.normcase(os.path.normpath(home))
        folder = task_dir if (task_dir and _within(task_dir, home)) else \
            (cwd if own_folder else os.path.join(home, "_linked", rid))
        payload = {
            "roomId": rid, "title": room.get("title", ""), "mode": room.get("mode", ""),
            "createdAt": room.get("createdAt"), "updatedAt": room.get("updatedAt"),
            "participants": [{k: p.get(k) for k in ("identity", "agent", "model", "role")}
                             for p in room.get("participants", []) if p.get("kind") == "agent"],
            "messages": room.get("messages", []),
        }
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / "chat.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                                    encoding="utf-8")
            n += 1
        except OSError:
            continue
    return n


def build_projects() -> dict:
    """Registry-driven: group sessions under the projects the user has registered,
    linked by the explicit session→project sidecar (falling back to path
    containment for legacy sessions). Everything unmatched lands in an
    'Unassigned' bucket so nothing disappears (those get ported later)."""
    projects_reg = load_projects()
    links = load_session_projects()
    rows = load_sessions(500)
    keep = ("sessionId", "roomId", "label", "status", "isLive", "idleSeconds",
            "updatedAt", "agents", "members", "mode", "headless", "cwd")
    # One group per registered project, plus a synthetic unassigned bucket.
    groups: dict = {}
    for p in projects_reg:
        groups[p["id"]] = {"id": p["id"], "name": p["name"], "path": p["path"],
                           "home": project_home(p, create=False),
                           "isGit": p.get("isGit", False), "registered": True,
                           "sessions": [], "live": 0, "waiting": 0, "updatedAt": 0}
    UNASSIGNED = "__unassigned__"
    needs_you = 0
    agents_live = 0
    for s in rows:
        sid = s.get("roomId") or s.get("sessionId") or ""
        pid = links.get(sid) or _project_for_cwd(s.get("cwd", ""), projects_reg)
        if pid not in groups:
            pid = UNASSIGNED
            if UNASSIGNED not in groups:
                groups[UNASSIGNED] = {"id": UNASSIGNED, "name": "Unassigned",
                                      "path": "", "isGit": False, "registered": False,
                                      "sessions": [], "live": 0, "waiting": 0, "updatedAt": 0}
        g = groups[pid]
        g["sessions"].append(s)
        if s.get("isLive"):
            g["live"] += 1
            agents_live += len(s.get("agents") or [1])
        if s.get("status") in ("waiting", "waiting_human"):
            g["waiting"] += 1
            needs_you += 1
        g["updatedAt"] = max(g["updatedAt"], s.get("updatedAt") or 0)
    projects = []
    total_changed = 0
    for gid, g in groups.items():
        g["changed"] = git_changed_count(g["path"]) if (g.get("isGit") and g.get("path")) else 0
        total_changed += g["changed"]
        g["count"] = len(g["sessions"])
        g["sessions"] = [{k: s.get(k) for k in keep}
                         for s in sorted(g["sessions"],
                                         key=lambda x: x.get("updatedAt") or 0, reverse=True)]
        projects.append(g)
    # Registered projects first (even when empty, so the landing isn't blank),
    # most-recently-active first; Unassigned always last.
    projects.sort(key=lambda x: (x["id"] == UNASSIGNED, -(x["updatedAt"] or 0),
                                 x["name"].lower()))
    return {"projects": projects,
            "summary": {"needsYou": needs_you, "agentsLive": agents_live,
                        "changed": total_changed,
                        "projects": len([p for p in projects if p.get("registered")])}}


# ---- Workspace file browsing (read-only, scoped to projects + ~/cs) ----------

def _within(child: str, parent: str) -> bool:
    try:
        c = os.path.normcase(os.path.realpath(child))
        pr = os.path.normcase(os.path.realpath(parent))
    except OSError:
        return False
    return c == pr or c.startswith(pr + os.sep)


def workspace_access_ok(path: str) -> bool:
    """A path is browsable only if it lives inside a registered project or under
    the collaboration-session root (~/cs). Keeps the read-only file APIs from
    wandering the whole disk even though the hub is single-user + token-gated."""
    if not path:
        return False
    roots = [p["path"] for p in load_projects()]
    roots.append(str(CS_ROOT))
    roots.append(str(PROJECTS_ROOT))   # every project home and task folder
    return any(_within(path, r) for r in roots)


_TEXT_MAX = 512 * 1024   # 512 KB read cap for the file viewer


def list_dir(path: str) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    d = Path(path)
    if not d.exists():
        return 404, {"error": "not_found"}
    if not d.is_dir():
        return 400, {"error": "not_a_directory"}
    entries = []
    try:
        for child in d.iterdir():
            if child.name == ".git":         # never descend into the git db
                continue
            try:
                st = child.stat()
                is_dir = child.is_dir()
            except OSError:
                continue
            entries.append({"name": child.name, "type": "dir" if is_dir else "file",
                            "size": 0 if is_dir else st.st_size,
                            "mtime": int(st.st_mtime)})
    except OSError as e:
        return 500, {"error": f"read_failed: {e}"}
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    parent = str(d.parent) if workspace_access_ok(str(d.parent)) else ""
    return 200, {"path": str(d), "parent": parent, "entries": entries[:2000]}


def read_workspace_file(path: str) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    f = Path(path)
    if not f.exists() or not f.is_file():
        return 404, {"error": "not_found"}
    try:
        raw = f.read_bytes()
        size = f.stat().st_size
    except OSError as e:
        return 500, {"error": f"read_failed: {e}"}
    truncated = len(raw) > _TEXT_MAX
    raw = raw[:_TEXT_MAX]
    if b"\x00" in raw:
        return 200, {"path": str(f), "binary": True, "text": "",
                     "size": size, "truncated": truncated}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return 200, {"path": str(f), "binary": False, "text": text,
                 "size": size, "truncated": truncated}


def git_status(path: str) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    root = git_root(path)
    if not root or not path_is_git(root):
        return 200, {"root": root, "isGit": False, "files": []}
    files = []
    try:
        out = subprocess.run(["git", "-C", root, "status", "--porcelain=v1", "-z"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
        parts = (out.stdout or "").split("\x00")   # NUL-separated records
        i = 0
        while i < len(parts):
            rec = parts[i]
            if not rec:
                i += 1
                continue
            xy, name = rec[:2], rec[3:]
            if xy and xy[0] == "R":                # rename: new path is next part
                i += 1
                name = parts[i] if i < len(parts) else name
            files.append({"path": name, "status": xy.strip() or "?",
                          "staged": xy[0] not in (" ", "?")})
            i += 1
    except (OSError, subprocess.SubprocessError) as e:
        return 500, {"error": f"git_status_failed: {e}"}
    files.sort(key=lambda f: f["path"].lower())
    return 200, {"root": root, "isGit": True, "files": files}


_GIT_ROOTS_SKIP = {"node_modules", ".venv", "venv", "__pycache__", "target", "dist",
                   "build", ".idea", ".tox", "site-packages"}


def git_roots(path: str, depth: int = 3) -> tuple[int, dict]:
    """Git repositories in scope for a Workspace/Changes view: the enclosing
    repo if ``path`` sits inside one, else every repo found up to ``depth``
    levels below it. A task's workspace usually isn't a repo itself — agents
    clone into it (``<task>/repo/``, legacy ``<task>/<agent>/<name>/``)."""
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    base = Path(path)
    if not base.is_dir():
        return 404, {"error": "not_found"}
    enclosing = git_root(path)
    # The projects root is the backup repo, not code: skip it and look inside.
    if enclosing and path_is_git(enclosing) and not _within(str(PROJECTS_ROOT), enclosing):
        name = os.path.basename(enclosing.rstrip("\\/")) or enclosing
        return 200, {"roots": [{"path": enclosing, "name": name}]}
    found: list[dict] = []

    def walk(d: Path, lvl: int) -> None:
        if len(found) >= 50:
            return
        try:
            kids = sorted(d.iterdir(), key=lambda c: c.name.lower())
        except OSError:
            return
        for c in kids:
            try:
                if not c.is_dir():
                    continue
            except OSError:
                continue
            if c.name.startswith(".") or c.name.lower() in _GIT_ROOTS_SKIP:
                continue
            if (c / ".git").exists():        # dir, or a file for worktrees
                found.append({"path": str(c), "name": str(c.relative_to(base)).replace("\\", "/")})
                continue
            if lvl < depth:
                walk(c, lvl + 1)

    walk(base, 1)
    return 200, {"roots": found}


def git_diff(path: str, file: str) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    root = git_root(path)
    if not root or not path_is_git(root):
        return 200, {"root": root, "isGit": False, "diff": ""}
    argv = ["git", "-C", root, "diff", "HEAD", "--"]
    if file:
        argv.append(file)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        diff = out.stdout or ""
        # Untracked files don't show in `git diff HEAD`; surface their content as
        # an all-added diff so the reviewer still sees the new file.
        if file and not diff.strip():
            fpath = os.path.join(root, file)
            if os.path.isfile(fpath) and workspace_access_ok(fpath):
                st, payload = read_workspace_file(fpath)
                if st == 200 and not payload.get("binary"):
                    lines = payload["text"].split("\n")
                    body = "".join("+" + ln + "\n" for ln in lines)
                    diff = f"--- /dev/null\n+++ b/{file}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
    except (OSError, subprocess.SubprocessError) as e:
        return 500, {"error": f"git_diff_failed: {e}"}
    return 200, {"root": root, "isGit": True, "file": file, "diff": diff}


def load_categories() -> dict[str, str]:
    try:
        d = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str) and v}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_categories(c: dict[str, str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CATEGORIES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(c, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(CATEGORIES_FILE)


def load_pinned() -> set[str]:
    try:
        d = json.loads(PINNED_FILE.read_text(encoding="utf-8"))
        if isinstance(d, list):
            return {x for x in d if isinstance(x, str)}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return set()


def save_pinned(pins: set[str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PINNED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(pins), indent=2), encoding="utf-8")
    tmp.replace(PINNED_FILE)


def load_parents() -> dict[str, str]:
    try:
        d = json.loads(PARENTS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_parents(p: dict[str, str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PARENTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(p, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(PARENTS_FILE)


def load_jira_links() -> dict[str, list[str]]:
    try:
        d = json.loads(JIRA_LINKS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {
                k: [t for t in v if isinstance(t, str)]
                for k, v in d.items()
                if isinstance(k, str) and isinstance(v, list)
            }
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_jira_links(links: dict[str, list[str]]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JIRA_LINKS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(links, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(JIRA_LINKS_FILE)


def load_jira_unlinks() -> dict[str, list[str]]:
    try:
        d = json.loads(JIRA_UNLINKS_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return {
                k: [t for t in v if isinstance(t, str)]
                for k, v in d.items()
                if isinstance(k, str) and isinstance(v, list)
            }
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def save_jira_unlinks(unlinks: dict[str, list[str]]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JIRA_UNLINKS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(unlinks, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(JIRA_UNLINKS_FILE)


def load_favorite_themes() -> list[str]:
    try:
        data = json.loads(FAVORITE_THEMES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def save_favorite_themes(favs: list[str]) -> None:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FAVORITE_THEMES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(favs, indent=2), encoding="utf-8")
    tmp.replace(FAVORITE_THEMES_FILE)


_CLAUDE_THEMES = ["dark", "light", "dark-ansi", "light-ansi", "dark-daltonized", "light-daltonized"]


def next_cs_counter() -> int:
    if not CS_ROOT.exists():
        return 1
    nums = []
    for child in CS_ROOT.iterdir():
        if not child.is_dir():
            continue
        m = _NUMBERED_RE.match(child.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def claude_project_slug(path: str) -> str:
    """Mirror claude-code's path → project-slug rule (replace every
    non-alphanumeric character with '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def create_fork_workspace(orig_cwd: str, fork_label: str = "") -> tuple[bool, str, str]:
    """Create an isolated workspace at ~/cs/NN_<slug>/ for a forked session.
    Git repos under orig_cwd become worktrees; everything else is cp -R'd.
    Returns (ok, new_cwd_path, message)."""
    orig = Path(orig_cwd)
    if not orig.exists() or not orig.is_dir():
        return False, "", f"original cwd does not exist: {orig_cwd}"

    n = next_cs_counter()
    base = fork_label or f"fork of {orig.name}"
    slug = sanitize_slug(base)
    new_path = CS_ROOT / f"{n:02d}_{slug}"
    if new_path.exists():
        return False, "", f"target already exists: {new_path.name}"
    try:
        new_path.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        return False, "", f"mkdir failed: {e}"

    # Roll a terminal theme for the fork (matches `cs new` behaviour). The
    # backend seeds whatever its terminal understands (.iterm-preset on macOS,
    # .wt-scheme on Windows); a no-op where themes aren't supported.
    BACKEND.prepare_session_theme(new_path)

    notes: list[str] = []
    for child in orig.iterdir():
        # Skip hidden files at the top level — we've already written
        # .iterm-preset; .DS_Store and the like aren't worth copying.
        if child.name.startswith("."):
            continue
        target = new_path / child.name
        if child.is_dir() and (child / ".git").exists():
            # Git worktree — fast, shares object store, separate working tree.
            try:
                r = subprocess.run(
                    ["git", "-C", str(child), "worktree", "add", "--detach", str(target)],
                    capture_output=True, text=True, timeout=60,
                )
                if r.returncode == 0:
                    notes.append(f"worktree: {child.name}")
                    continue
            except subprocess.SubprocessError:
                pass
            # Fallthrough to copy if worktree failed.
        # Plain copy
        try:
            if child.is_dir():
                subprocess.run(["cp", "-R", str(child), str(target)],
                               capture_output=True, timeout=180)
                notes.append(f"copied dir: {child.name}")
            elif child.is_file():
                subprocess.run(["cp", str(child), str(target)],
                               capture_output=True, timeout=30)
                notes.append(f"copied file: {child.name}")
        except subprocess.SubprocessError:
            pass

    return True, str(new_path), "; ".join(notes) or "empty"


def create_cs_session(description: str) -> tuple[bool, str, str]:
    """Create ~/cs/NN_<slug>/ with random iTerm preset + Claude theme. Returns (ok, path, msg)."""
    slug = sanitize_slug(description)
    if not slug or slug == "untitled":
        return False, "", "empty description"
    n = next_cs_counter()
    target = CS_ROOT / f"{n:02d}_{slug}"
    if target.exists():
        return False, "", f"already exists: {target.name}"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        return False, "", f"mkdir failed: {e}"

    # Random terminal theme, seeded by the platform backend (iTerm preset on
    # macOS, WT color scheme on Windows; no-op elsewhere).
    BACKEND.prepare_session_theme(target)

    # Random Claude theme in .claude/settings.json
    settings_dir = target / ".claude"
    try:
        settings_dir.mkdir(exist_ok=True)
        settings_file = settings_dir / "settings.json"
        existing = {}
        if settings_file.exists():
            try:
                existing = json.loads(settings_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        existing["theme"] = random.choice(_CLAUDE_THEMES)
        settings_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass

    return True, str(target), "ok"


def _in_projects_root(path: str) -> bool:
    """True if ``path`` is inside PROJECTS_ROOT (a layout-v2 project or task)."""
    if not path:
        return False
    try:
        c = os.path.normcase(os.path.realpath(path))
        r = os.path.normcase(os.path.realpath(str(PROJECTS_ROOT)))
    except OSError:
        return False
    return c == r or c.startswith(r + os.sep)


_UNSAFE_DIR_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_dir_name(name: str) -> str:
    s = _UNSAFE_DIR_CHARS.sub("_", (name or "").strip()).strip(". ")
    return s[:80] or "project"


def project_home(project: dict, create: bool = True) -> str:
    """The project's folder under PROJECTS_ROOT: where its tasks, notes and
    chat exports live — what the backup carries. A project registered from an
    external code folder (``path`` outside the root) gets a home created on
    demand; its project.json records the code path so a restored root is still
    self-describing. In-root projects are their own home."""
    ppath = project.get("path", "")
    if _in_projects_root(ppath):
        return os.path.normpath(ppath)
    home = project.get("home") or ""
    if not home:
        base = PROJECTS_ROOT / _safe_dir_name(project.get("name") or os.path.basename(ppath))
        home = str(base)
        n = 2
        while True:                          # same name, different project → -2, -3…
            pj = Path(home) / "project.json"
            if not pj.exists():
                break
            try:
                if json.loads(pj.read_text(encoding="utf-8")).get("id") == project.get("id"):
                    break
            except (OSError, json.JSONDecodeError):
                pass
            home = f"{base}-{n}"
            n += 1
    if create:
        try:
            Path(home).mkdir(parents=True, exist_ok=True)
            pj = Path(home) / "project.json"
            if not pj.exists():
                pj.write_text(json.dumps({"id": project.get("id", ""), "name": project.get("name", ""),
                                          "code": os.path.normpath(ppath) if ppath else "",
                                          "createdAt": project.get("createdAt") or int(time.time())},
                                         indent=2), encoding="utf-8")
        except OSError:
            pass
    return os.path.normpath(home)


def _task_dir_for(project: dict, title: str) -> Path:
    """A new task's folder: <project home>/<slug>[-N]. Plain names, no NN_
    prefix — the project folder is the namespace."""
    slug = sanitize_slug(title) or "task"
    base = Path(project_home(project))
    dest = base / slug
    n = 2
    while dest.exists():
        dest = base / f"{slug}-{n}"
        n += 1
    return dest


def _init_task_folder(target: Path) -> tuple[bool, str]:
    """Create a task folder with the same per-session niceties create_cs_session
    gives ~/cs scratch dirs (terminal theme, Claude theme)."""
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return False, f"already exists: {target}"
    except OSError as e:
        return False, f"mkdir failed: {e}"
    BACKEND.prepare_session_theme(target)
    settings_dir = target / ".claude"
    try:
        settings_dir.mkdir(exist_ok=True)
        sf = settings_dir / "settings.json"
        existing = {}
        if sf.exists():
            try:
                existing = json.loads(sf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        existing["theme"] = random.choice(_CLAUDE_THEMES)
        sf.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return True, "ok"


def setup_session_workspace(project: dict, mode: str, title: str) -> tuple[bool, str, dict, str]:
    """Provision a session's own workspace for a project. The framework does NOT
    force a code checkout — the mode is the user's per-session choice, because a
    project (or a session) may have no code at all:

        empty     a fresh scratch dir under ~/cs (no code) — the default
        inplace   work directly in the project folder (shared with the project)
        copy      a full copy of the project folder into a session dir
        worktree  a git worktree on its own branch (git projects only)

    Returns (ok, base_path, meta, msg). ``meta`` may carry {branch, mode}.
    ``base_path`` becomes the room cwd; project-backed modes also imply a shared
    cwd (agents work on the same files, not per-identity subdirs)."""
    mode = (mode or "empty").strip()
    if not project:
        # No project: legacy scratch under ~/cs (Unassigned).
        ok, base, msg = create_cs_session(title)
        return ok, base, {"mode": "empty"}, msg
    ppath = project.get("path", "")
    if not ppath or not os.path.isdir(ppath):
        return False, "", {}, "project folder missing"
    in_root = _in_projects_root(ppath)
    # Every task gets its own folder under the project's home in the projects
    # root (task.json, notes, chat export) — so the backup carries every task.
    task_dir = _task_dir_for(project, title)
    ok, msg = _init_task_folder(task_dir)
    if not ok:
        return False, "", {}, msg
    tmeta = {"taskDir": str(task_dir)}
    if mode == "empty":
        return True, str(task_dir), {"mode": "empty", **tmeta}, "ok"
    if mode == "inplace":
        return True, os.path.normpath(ppath), {"mode": "inplace", **tmeta}, "ok"
    if in_root and mode in ("copy", "worktree"):
        # A project inside the projects root is a container of tasks, not a
        # code tree: copying/worktree-ing it makes no sense (and copytree into
        # its own subfolder would recurse). Code goes in <task>/repo/.
        return False, "", {}, (f"'{mode}' is for projects with an external code folder; "
                               f"create an empty task and clone into repo/")
    slug = sanitize_slug(title)
    # The checkout lives INSIDE the task folder; repo/ is ignored by the backup
    # (the code has its own remote), the task's spec/notes/chat are not.
    dest = task_dir / "repo"
    if mode == "copy":
        try:
            shutil.copytree(ppath, dest,
                            ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
        except OSError as e:
            return False, "", {}, f"copy failed: {e}"
        return True, str(dest), {"mode": "copy", **tmeta}, "ok"
    if mode == "worktree":
        if not path_is_git(ppath):
            return False, "", {}, "worktree needs a git project"
        branch = "sess/" + (slug or "session")
        try:
            out = subprocess.run(
                ["git", "-C", ppath, "worktree", "add", str(dest), "-b", branch],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if out.returncode != 0:
                # Branch may already exist → attach without -b.
                out2 = subprocess.run(
                    ["git", "-C", ppath, "worktree", "add", str(dest), branch],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                if out2.returncode != 0:
                    return False, "", {}, f"worktree failed: {(out.stderr or out2.stderr or '').strip()[:200]}"
        except (OSError, subprocess.SubprocessError) as e:
            return False, "", {}, f"worktree failed: {e}"
        return True, str(dest), {"mode": "worktree", "branch": branch, **tmeta}, "ok"
    return False, "", {}, f"unknown workspace mode: {mode}"


def sanitize_slug(s: str, max_len: int = 60) -> str:
    s = _SLUG_NONALNUM.sub("_", s.lower()).strip("_")
    return s[:max_len].rstrip("_") or "untitled"


def cwd_for_session(session_id: str) -> str:
    """Best-effort cwd lookup: live metadata first, then transcript."""
    # A collaboration room is addressed by its room id (not a transcript) — its
    # cwd lives in the room record. Lets room rows reuse the cwd-based tools
    # (Explorer, IDE) exactly as a single-agent session does.
    if session_id.startswith("room-"):
        try:
            rm = chatroom.get_room(session_id, public=False)
            if rm:
                return rm.get("cwd", "") or ""
        except Exception:
            pass
    for s in _read_session_files():
        if s.get("sessionId") == session_id:
            return s.get("cwd", "") or ""
    tpath = find_transcript(session_id)
    return cwd_of(tpath) if tpath else ""


def rename_session_folder(cwd: str, new_slug: str) -> tuple[bool, str, str]:
    """Returns (ok, new_path, message). Only renames ~/cs/NN_* dirs; preserves NN_."""
    if not cwd:
        return False, "", "no cwd"
    p = Path(cwd)
    if not p.exists():
        return False, "", f"cwd does not exist: {cwd}"
    if p.parent.resolve() != CS_ROOT.resolve():
        return False, "", f"only ~/cs/NN_* dirs can be renamed (got {p})"
    m = _NUMBERED_RE.match(p.name)
    if not m:
        return False, "", f"folder does not match NN_* pattern: {p.name}"
    prefix = m.group(1)
    slug = sanitize_slug(new_slug)
    new_name = f"{prefix}_{slug}"
    new_path = p.parent / new_name
    if new_path == p:
        return True, str(p), "no change"
    if new_path.exists():
        return False, "", f"target already exists: {new_name}"
    try:
        p.rename(new_path)
    except OSError as e:
        return False, "", f"rename failed: {e}"
    return True, str(new_path), "ok"


RENAME_PROMPT_PREFIX = "[ensemble-rename] "

_CLAUDE_BIN: str | None = None


def _claude_bin() -> str | None:
    """Resolve the absolute path to `claude`. Launchd's PATH is minimal and may
    miss ~/.local/bin or homebrew's user-prefix bin, so we look in common spots
    after consulting PATH."""
    global _CLAUDE_BIN
    if _CLAUDE_BIN:
        return _CLAUDE_BIN
    import shutil
    p = shutil.which("claude")
    if p:
        _CLAUDE_BIN = p
        return p
    for candidate in (
        HOME / ".local/bin/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        Path("/usr/bin/claude"),
    ):
        if candidate.exists():
            _CLAUDE_BIN = str(candidate)
            return _CLAUDE_BIN
    return None


_RENAME_PROJ_SLUG = claude_project_slug(str(RENAME_WORKSPACE))


def _is_rename_artifact(jsonl: Path) -> bool:
    """A jsonl created by our own `claude -p` rename call. Detected primarily
    by path: the call runs with cwd=RENAME_WORKSPACE, so its transcript lives
    under PROJ_DIR/<slug-of-rename-workspace>/. Falls back to legacy sentinel
    detection for in-flight artifacts written by older versions of the prompt."""
    try:
        if jsonl.parent.name == _RENAME_PROJ_SLUG:
            return True
    except (OSError, AttributeError):
        pass
    try:
        with jsonl.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user" or d.get("isMeta"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                text = _extract_text(msg.get("content"))
                if text is None:
                    continue
                stripped = text.strip()
                if not stripped:
                    continue
                if stripped.startswith(RENAME_PROMPT_PREFIX):
                    return True
                if stripped.startswith("Below is a transcript of user turns from a coding session"):
                    return True
                return False
    except OSError:
        return False
    return False


# Noisy string fields we don't want the transcript search to match against
# (they're opaque identifiers, not content).
_SEARCH_SKIP_FIELDS = frozenset({
    "uuid", "parentUuid", "id", "requestId", "sessionId",
    "signature",           # base64 blob on thinking blocks
    "cache_control", "service_tier", "stop_reason", "stop_sequence",
    "type", "role", "model", "version",
})


def _walk_message_strings(obj):
    """Yield every leaf string in a JSONL message that's meaningful for
    substring search — walks tool_use inputs, tool_result outputs, all
    content blocks. Skips opaque identifier fields (see _SEARCH_SKIP_FIELDS).
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SEARCH_SKIP_FIELDS:
                continue
            yield from _walk_message_strings(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk_message_strings(x)
    elif isinstance(obj, str) and obj:
        yield obj


def _parse_search_query(q: str) -> list[list[str]]:
    """Parse a query string into OR-groups of AND-terms.

    Rules:
      * space-separated tokens are AND within a group
      * uppercase OR separates groups
      * "double-quoted" runs treat internal spaces as literal

    Returns [] if the parse produces no non-empty groups. Malformed quoting
    is tolerated (unterminated quote reads to end-of-string)."""
    q = (q or "").strip()
    if not q:
        return []
    tokens: list[str] = []
    i, n = 0, len(q)
    while i < n:
        ch = q[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = q.find('"', i + 1)
            if end == -1:
                tokens.append(q[i + 1:])
                break
            tokens.append(q[i + 1:end])
            i = end + 1
        else:
            end = i
            while end < n and not q[end].isspace():
                end += 1
            tokens.append(q[i:end])
            i = end
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    groups: list[list[str]] = [[]]
    for t in tokens:
        if t == "OR":
            if groups[-1]:
                groups.append([])
        else:
            groups[-1].append(t)
    return [g for g in groups if g]


def search_transcripts(query: str, max_results: int = 100, snippet_pad: int = 60) -> list[dict]:
    """Grep across ALL text-shaped content in each transcript — assistant
    replies, user prompts, tool_use inputs, tool_result outputs.

    Query syntax:
      * `foo bar`  ─ AND (both must appear anywhere in the session)
      * `foo OR bar` ─ OR (either alone is enough)
      * `"foo bar"` ─ literal phrase; spaces inside quotes stay literal
      * combines: `"prop-risk" replay OR quoting review`

    Returns most-hit sessions first with a short snippet around the first
    match. Case-insensitive throughout."""
    groups = _parse_search_query(query)
    if not groups:
        return []
    # Union of every distinct term across all groups — one pass finds all.
    all_terms = sorted({t.lower() for g in groups for t in g if t})
    if not all_terms:
        return []
    # Precompile snippet regexes for each distinct term.
    term_pats = {t: re.compile(re.escape(t), re.IGNORECASE) for t in all_terms}
    results: list[dict] = []
    for jsonl in PROJ_DIR.glob("*/*.jsonl"):
        if "rename-workspace" in jsonl.parent.name:
            continue
        if _is_rename_artifact(jsonl):
            continue
        seen_terms: set[str] = set()
        hits = 0
        snippet = ""
        try:
            with jsonl.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    lower = line.lower()
                    # Cheap pre-check: which terms could possibly match this
                    # line? If none, skip the JSON parse entirely.
                    line_terms = [t for t in all_terms if t in lower]
                    if not line_terms:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") not in ("user", "assistant"):
                        continue
                    msg = d.get("message")
                    if not isinstance(msg, dict):
                        continue
                    for text in _walk_message_strings(msg):
                        matched_here = False
                        for t in line_terms:
                            m = term_pats[t].search(text)
                            if not m:
                                continue
                            matched_here = True
                            seen_terms.add(t)
                            if not snippet:
                                s = max(0, m.start() - snippet_pad)
                                e = min(len(text), m.end() + snippet_pad)
                                snippet = ("…" if s > 0 else "") + text[s:e] + ("…" if e < len(text) else "")
                                snippet = " ".join(snippet.split())
                        if matched_here:
                            hits += 1
                    # No early break: we need to scan the whole file to know
                    # whether AND is satisfied (a term might be first mentioned
                    # 10 000 lines in). Full scans are still cheap.
        except OSError:
            continue
        # A session matches iff at least one OR-group has all its terms seen.
        satisfied = any(all(t.lower() in seen_terms for t in g) for g in groups)
        if satisfied and hits:
            results.append({
                "sessionId": jsonl.stem,
                "hits": hits,
                "snippet": snippet,
            })
    results.sort(key=lambda r: -r["hits"])
    return results[:max_results]


def delete_session(sid: str) -> dict:
    """Remove a session's JSONL transcript and all sidecar entries
    (labels, parents, geometries, pinned). Does not touch the cwd."""
    deleted: dict = {"sessionId": sid, "files": []}
    for jsonl in PROJ_DIR.glob(f"*/{sid}.jsonl"):
        try:
            jsonl.unlink()
            deleted["files"].append(str(jsonl))
        except OSError as e:
            deleted.setdefault("errors", []).append(str(e))
    labels = load_labels()
    if labels.pop(sid, None) is not None:
        save_labels(labels)
        deleted["label"] = True
    parents = load_parents()
    if parents.pop(sid, None) is not None:
        save_parents(parents)
        deleted["parent_ref"] = True
    g = load_geometries()
    if g.pop(sid, None) is not None:
        save_geometries(g)
        deleted["geometry"] = True
    pins = load_pinned()
    if sid in pins:
        pins.discard(sid)
        save_pinned(pins)
        deleted["pinned"] = True
    cats = load_categories()
    if cats.pop(sid, None) is not None:
        save_categories(cats)
        deleted["category"] = True
    arch = load_archived()
    if sid in arch:
        arch.discard(sid)
        save_archived(arch)
        deleted["archived"] = True
    return deleted


def _scratch_root_for(cwd: str) -> Path | None:
    """The ~/cs/<NN_slug> scratch folder that owns `cwd`, or None when `cwd`
    isn't a dashboard-created scratch dir — so a real project folder is NEVER a
    deletion target. Handles both the collab root and a per-agent subfolder."""
    if not cwd:
        return None
    try:
        root = CS_ROOT.resolve()
        cur = Path(cwd).resolve()
    except OSError:
        return None
    for _ in range(4):
        try:
            if cur.parent == root and _NUMBERED_RE.match(cur.name):
                return cur
        except OSError:
            break
        if cur == cur.parent:
            break
        cur = cur.parent
    return None


def _delete_scratch_root(cwd: str) -> str | None:
    """Recursively remove the ~/cs/<NN_slug> scratch folder owning `cwd`.
    Returns the deleted path, or None if `cwd` isn't a scratch dir / removal
    failed. Never touches a real project folder."""
    root = _scratch_root_for(cwd)
    if not root:
        return None
    try:
        shutil.rmtree(root)
        return str(root)
    except OSError:
        return None


def cleanup_rename_artifacts() -> int:
    n = 0
    for jsonl in PROJ_DIR.glob("*/*.jsonl"):
        if "rename-workspace" in jsonl.parent.name or _is_rename_artifact(jsonl):
            try:
                jsonl.unlink()
                n += 1
            except OSError:
                pass
    return n


def _iter_text_for_rename(path: Path):
    """Yield (role, text) for user/assistant turns. Skips tool results and meta."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                if t not in ("user", "assistant") or d.get("isMeta"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                text = _extract_text(msg.get("content"))
                if not text:
                    continue
                stripped = text.strip()
                if not stripped:
                    continue
                if t == "user" and (stripped.startswith("<") or stripped.startswith("Caveat:")):
                    continue
                yield t, stripped
    except FileNotFoundError:
        return


def claude_rename(session_id: str, max_turns: int = 12, timeout: int = 60) -> str | None:
    """Ask `claude -p` for a brief content summary. Returns the suggested label,
    or None on failure. Feeds the FIRST turns of the transcript so the title
    reflects the original goal rather than a late tangent (e.g. a rename call
    from this very dashboard)."""
    tpath = find_transcript(session_id)
    if not tpath:
        return None
    turns: list[str] = []
    total = 0
    for role, text in _iter_text_for_rename(tpath):
        snippet = text[:800]
        turns.append(f"[{role}] {snippet}")
        total += len(snippet)
        if len(turns) >= max_turns or total > 12000:
            break
    if not turns:
        return None
    excerpt = "\n---\n".join(turns)
    # No sentinel in the prompt body — the model used to echo "claude
    # dashboard rename" into the title. Artifact detection is now path-based
    # (see _is_rename_artifact).
    prompt = (
        "You will be shown an excerpt from a coding-assistant session "
        "between a user and the assistant. Write a SHORT TITLE that "
        "SUMMARISES WHAT THE SESSION IS ABOUT — the work the user is doing "
        "IN the session, not the meta-task of titling it. Anchor on the "
        "FIRST user message: it usually states the original goal; later "
        "turns refine but should not redirect the title.\n"
        "\n"
        "Format: 3-8 lowercase words, separated by single spaces, no "
        "punctuation, no quotes, no leading verbs like add/build/fix/review/"
        "implement. Include concrete identifiers when present: ticket IDs "
        "(e.g. PTECH-12345), PR titles (not bare numbers), repo names, "
        "service names, the specific bug/feature.\n"
        "\n"
        "Good examples:\n"
        "  ptech-44560 batch listener missing events\n"
        "  lsx spread conformity follow-up clamps\n"
        "  quoting-algo replay prop-risk locally\n"
        "  myrepo pr 42 multi-level cache support\n"
        "\n"
        "Reply with the title ONLY on one line.\n\n"
        f"Transcript:\n{excerpt}"
    )

    # `claude -p` creates a session JSONL of its own. Snapshot the project dir so
    # we can delete the artifact after the call.
    workspace = DASHBOARD_DIR / "_rename_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    before = set(PROJ_DIR.glob("*/*.jsonl"))

    bin_path = _claude_bin()
    if not bin_path:
        _cleanup_new_jsonls(before)
        return None
    try:
        r = subprocess.run(
            [bin_path, "-p", prompt],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        _cleanup_new_jsonls(before)
        return None
    _cleanup_new_jsonls(before)

    if r.returncode != 0:
        return None
    title = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    title = title.strip(" \"'`.")
    return title[:80] or None


def _cleanup_new_jsonls(before: set[Path]) -> None:
    """Delete any artifact JSONLs created since the snapshot."""
    after = set(PROJ_DIR.glob("*/*.jsonl"))
    for f in after - before:
        if _is_rename_artifact(f):
            try:
                f.unlink()
            except OSError:
                pass


# ---------- terminal control (delegated to the platform backend) ----------

def live_cwd_of_pid(pid: int) -> str | None:
    return BACKEND.live_cwd_of_pid(pid)


# iTerm's "Merge All Windows" menu action, invoked via System Events. Requires
# macOS Accessibility permission for /usr/bin/osascript.


# Move each of the given TTYs to its own window. Uses iTerm's "Move Session
# to New Window" menu action. Requires macOS Accessibility permission.


# ---------- repo discovery + Finder/IJ opening ----------

_PATH_RE = re.compile(r"/Users/[A-Za-z0-9_./-]+")
IJ_APP = os.environ.get("ENSEMBLE_IJ_APP", "IntelliJ IDEA")  # legacy fallback

# Jira auto-detection is OPT-IN and configured at install time (or via env).
# Precedence: settings.json (jiraEnabled / jiraBase / jiraPrefixes) then the
# ENSEMBLE_JIRA_* env vars. When no base is configured, Jira is off and
# the whole feature is hidden in the UI (via /api/jira-config → enabled:false).
def _load_jira_config() -> tuple[bool, str, set[str]]:
    s = load_settings()
    base = (s.get("jiraBase") or os.environ.get("ENSEMBLE_JIRA_BASE", "") or "").strip()
    raw_prefixes = s.get("jiraPrefixes")
    if raw_prefixes is None:
        raw_prefixes = os.environ.get("ENSEMBLE_JIRA_PREFIXES", "")
    if isinstance(raw_prefixes, list):
        prefixes = {str(p).strip().upper() for p in raw_prefixes if str(p).strip()}
    else:
        prefixes = {p.strip().upper() for p in str(raw_prefixes).split(",") if p.strip()}
    enabled = s.get("jiraEnabled")
    if enabled is None:
        # Default: on only when a base URL has actually been configured.
        enabled = bool(base)
    return bool(enabled), base, prefixes


JIRA_ENABLED, JIRA_BASE, JIRA_PREFIXES = _load_jira_config()
_JIRA_TICKET_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9]{1,9})[-_](\d{2,7})")


def extract_jira_tickets(*texts: str) -> list[str]:
    """Find Jira-style ticket IDs in any of the given strings. Case-insensitive,
    accepts both `PTECH-12345` and `ptech_12345` shapes; filters to the
    configured prefix list."""
    found = set()
    for t in texts:
        if not t:
            continue
        for m in _JIRA_TICKET_RE.finditer(t):
            prefix = m.group(1).upper()
            if prefix in JIRA_PREFIXES:
                found.add(f"{prefix}-{m.group(2)}")
    return sorted(found)


_JIRA_CREATED_CACHE: dict = {}  # path -> (mtime, [tickets])
_JIRA_USER_CACHE: dict = {}     # path -> (mtime, [tickets])


_JIRA_USER_TURN_MAX_CHARS = 600
_JIRA_USER_TURN_MAX_TICKETS = 3


def scan_jira_urls_in_user_turns(jsonl_path) -> list[str]:
    """Find Jira URLs that the USER pasted in their turns. URL paste is an
    intentional act (you don't type 'https://...atlassian.net/browse/PTECH-X'
    by mistake), so this is deterministic rather than heuristic. Plain
    'PTECH-XXX' mentions in text are NOT counted. Tool results excluded."""
    if not jsonl_path:
        return []
    base = (JIRA_BASE or "").rstrip("/")
    if not base:
        return []
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return []
    cached = _JIRA_USER_CACHE.get(jsonl_path)
    if cached and cached[0] == mtime:
        return cached[1]
    url_re = re.compile(re.escape(base) + r"/([A-Za-z][A-Za-z0-9]+-\d+)")
    found = set()
    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user" or d.get("isMeta"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                text = None
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            text = c.get("text", "")
                            break
                if not text:
                    continue
                for m in url_re.finditer(text):
                    tk = m.group(1).upper()
                    if tk.split("-")[0] in JIRA_PREFIXES:
                        found.add(tk)
    except OSError:
        pass
    result = sorted(found)
    _JIRA_USER_CACHE[jsonl_path] = (mtime, result)
    return result


def _collect_text_blocks(content) -> list[str]:
    """Walk a JSONL message's content (string or content-block array) and
    return all plain-text strings, including those inside tool_result blocks."""
    out = []
    if isinstance(content, str):
        out.append(content)
        return out
    if not isinstance(content, list):
        return out
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "text":
            out.append(c.get("text", "") or "")
        elif c.get("type") == "tool_result":
            tc = c.get("content", "")
            if isinstance(tc, str):
                out.append(tc)
            elif isinstance(tc, list):
                for tcb in tc:
                    if isinstance(tcb, dict) and tcb.get("type") == "text":
                        out.append(tcb.get("text", "") or "")
    return out


def scan_jira_created_in_transcript(jsonl_path) -> list[str]:
    """Catch tickets the session itself CREATED. Looks for explicit
    `createJiraIssue` (or similar) tool_use calls in the transcript and
    extracts the resulting ticket key from the paired tool_result. Much
    more precise than any text/URL match — lookups don't trigger this.
    Cached by mtime."""
    if not jsonl_path:
        return []
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        return []
    cached = _JIRA_CREATED_CACHE.get(jsonl_path)
    if cached and cached[0] == mtime:
        return cached[1]
    base = (JIRA_BASE or "").rstrip("/")
    url_re = re.compile(re.escape(base) + r"/([A-Za-z][A-Za-z0-9]+-\d+)") if base else None
    key_re = re.compile(r'"key"\s*:\s*"([A-Z][A-Z0-9]+-\d+)"')
    create_ids: set = set()       # tool_use ids of create-issue calls
    found: set = set()
    try:
        with jsonl_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") not in ("user", "assistant") or d.get("isMeta"):
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        name = (c.get("name") or "").lower()
                        # Matches createJiraIssue, mcp__*__createJiraIssue, etc.
                        if "createjira" in name or "createissue" in name:
                            tid = c.get("id")
                            if tid:
                                create_ids.add(tid)
                    elif c.get("type") == "tool_result":
                        if c.get("tool_use_id") not in create_ids:
                            continue
                        tc = c.get("content", "")
                        text_blocks = []
                        if isinstance(tc, str):
                            text_blocks.append(tc)
                        elif isinstance(tc, list):
                            for tcb in tc:
                                if isinstance(tcb, dict) and tcb.get("type") == "text":
                                    text_blocks.append(tcb.get("text", "") or "")
                        for block in text_blocks:
                            if url_re:
                                for m in url_re.finditer(block):
                                    tk = m.group(1).upper()
                                    if tk.split("-")[0] in JIRA_PREFIXES:
                                        found.add(tk)
                            for m in key_re.finditer(block):
                                tk = m.group(1).upper()
                                if tk.split("-")[0] in JIRA_PREFIXES:
                                    found.add(tk)
    except OSError:
        pass
    result = sorted(found)
    _JIRA_CREATED_CACHE[jsonl_path] = (mtime, result)
    return result

EDITOR_MAP_FILE = DASHBOARD_DIR / "editors.json"
ICON_CACHE_DIR = DASHBOARD_DIR / "static" / "editors"

# Defaults — user can override per-language by writing {"java": "Cursor", ...}
# to ~/.ensemble/editors.json. Keys are language tokens emitted by
# detect_repo_language(); the "default" key handles any unknown language.
DEFAULT_EDITOR_MAP = {
    "java":       "IntelliJ IDEA",
    "kotlin":     "IntelliJ IDEA",
    "scala":      "IntelliJ IDEA",
    "python":     "PyCharm",
    "javascript": "WebStorm",
    "typescript": "WebStorm",
    "rust":       "RustRover",
    "go":         "GoLand",
    "ruby":       "RubyMine",
    "php":        "PhpStorm",
    "swift":      "Xcode",
    "default":    "Visual Studio Code",
}


def resolve_editor_for_repo(repo_path: Path) -> tuple[str, str, str | None]:
    return BACKEND.resolve_editor_for_repo(repo_path)

# Permission mode for sessions the dashboard launches. "bypassPermissions"
# auto-approves everything (no "yes?" prompts). Override with the env var, e.g.
# ENSEMBLE_PERMISSION_MODE=acceptEdits  (or "" to disable the flag).
PERMISSION_MODE = os.environ.get("ENSEMBLE_PERMISSION_MODE", "bypassPermissions")


def _claude_cmd(*extra: str) -> str:
    return claude_cmd(*extra)


_PROJECT_MARKERS = (".git", ".idea")


def _is_project_dir(p: Path) -> bool:
    return any((p / m).exists() for m in _PROJECT_MARKERS)


def find_repos_for_session(session_id: str, max_repos: int = 10) -> list[dict]:
    """Find IDE-openable projects belonging to this session. Only looks at the cwd:
    (1) enclosing project (walk up for .git or .idea), and (2) direct children
    with .git or .idea. Transcript mentions are not used."""
    cwd = cwd_for_session(session_id)
    if cwd and not Path(cwd).exists():
        for s in _read_session_files():
            if s.get("sessionId") == session_id:
                live = live_cwd_of_pid(s.get("pid", 0) or 0)
                if live:
                    cwd = live
                break

    repos: dict[str, str] = {}
    if not cwd or not Path(cwd).exists():
        return []

    # (1) Enclosing project (walk up)
    path = Path(cwd)
    for _ in range(8):
        if _is_project_dir(path):
            repos[str(path)] = path.name
            break
        if path == path.parent:
            break
        path = path.parent

    # (2) Direct children that are projects
    try:
        for child in Path(cwd).iterdir():
            if not child.is_dir():
                continue
            if _is_project_dir(child):
                repos.setdefault(str(child), child.name)
            if len(repos) >= max_repos:
                break
    except OSError:
        pass

    enriched = []
    for path_str, name in sorted(repos.items()):
        lang, editor, icon_url = resolve_editor_for_repo(Path(path_str))
        enriched.append({
            "path": path_str,
            "name": name,
            "language": lang,
            "editor": editor,
            "iconUrl": icon_url,
        })
    return enriched


def open_path(path: str, app: str = "default") -> str:
    return BACKEND.open_path(path, app)


def maybe_resync_iterm_label(pid: int, label: str) -> None:
    BACKEND.maybe_resync_label(pid, label)


def push_label_to_iterm(session_id: str, label: str) -> None:
    BACKEND.push_label(session_id, label)


# ---------- themes (delegated to the platform backend) ----------


def list_presets() -> list[dict]:
    return BACKEND.list_themes()


def current_theme_for_cwd(cwd: str) -> str:
    return BACKEND.current_theme_for_cwd(cwd)


# ---------- data loaders ----------


def load_live() -> list[dict]:
    labels = load_labels()
    out = []
    for d in _read_session_files():
        sid = d.get("sessionId", "")
        # If the recorded cwd no longer exists (the folder was moved/renamed),
        # fall back to lsof to resolve the live cwd from the process itself.
        recorded_cwd = d.get("cwd", "") or ""
        if recorded_cwd and not Path(recorded_cwd).exists():
            live = live_cwd_of_pid(d.get("pid", 0))
            if live:
                d = {**d, "cwd": live}
        tpath = find_transcript(sid)
        first = last = ""
        turns = 0
        idle = None
        if tpath:
            first, last, turns = first_last_user(tpath)
            idle = int(time.time() - tpath.stat().st_mtime)
        cwd = d.get("cwd", "")
        label = labels.get(sid, "")
        # Re-push label to iTerm periodically so shell-prompt overrides
        # don't drift the tab name away from the dashboard's title.
        maybe_resync_iterm_label(d.get("pid", 0) or 0, label)
        out.append({
            "pid": d.get("pid"),
            "sessionId": sid,
            "cwd": cwd,
            "startedAt": d.get("startedAt", 0),
            "status": d.get("status", ""),
            "updatedAt": d.get("updatedAt", 0),
            "first": first,
            "last": last,
            "turns": turns,
            "idleSeconds": idle,
            "label": label,
            "currentTheme": current_theme_for_cwd(cwd),
        })
    out.sort(key=lambda r: r.get("startedAt", 0))
    return out


def load_recent(n: int = 100) -> list[dict]:
    rows = []
    for jsonl in PROJ_DIR.glob("*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        rows.append((mtime, jsonl))
    rows.sort(reverse=True)
    live_ids = {d.get("sessionId", "") for d in _read_session_files()}
    labels = load_labels()
    out = []
    for mtime, jsonl in rows[:n]:
        sid = jsonl.stem
        first, last, turns = first_last_user(jsonl)
        out.append({
            "sessionId": sid,
            "cwd": cwd_of(jsonl),
            "updatedAt": mtime,
            "first": first,
            "last": last,
            "turns": turns,
            "isLive": sid in live_ids,
            "label": labels.get(sid, ""),
        })
    return out


def load_sessions(n: int = 200) -> list[dict]:
    """Unified view: recent transcripts with live-state overlaid where applicable.
    Sorted by updatedAt desc, so active live sessions naturally float to the top."""
    live_by_sid = {s["sessionId"]: s for s in load_live()}
    rows = []
    for jsonl in PROJ_DIR.glob("*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        rows.append((mtime, jsonl))
    rows.sort(reverse=True)
    labels = load_labels()
    parents_map = load_parents()
    pinned_set = load_pinned()
    categories_map = load_categories()
    archived_set = load_archived()
    jira_links = load_jira_links()
    jira_unlinks = load_jira_unlinks()
    out: list[dict] = []
    seen: set[str] = set()
    for mtime, jsonl in rows[:n]:
        # Parent-dir name will contain "rename-workspace" once claude-code
        # slugifies the workspace path — catches the case where the JSONL
        # exists but doesn't have a `cwd` field yet.
        if "rename-workspace" in jsonl.parent.name:
            continue
        if _is_rename_artifact(jsonl):
            continue
        if _is_workspace_cwd(cwd_of(jsonl)):
            continue
        sid = jsonl.stem
        # Dedupe by session id — the same transcript may appear in multiple
        # project dirs (e.g. when an isolated fork seeded its workspace with
        # a copy of the parent's transcript). `rows` is mtime-desc so the
        # first one we see is the most-recently-active copy.
        if sid in seen:
            continue
        first, last, turns = first_last_user(jsonl)
        live = live_by_sid.get(sid)
        # Skip historical artifacts: transcripts with zero user turns and no
        # user-set label. These are typically bg-spare daemons, immediately-
        # closed sessions, or other empty-shell files that pollute the list.
        if (live is None and turns == 0 and not labels.get(sid)
                and sid not in pinned_set and sid not in categories_map
                and sid not in archived_set):
            continue
        seen.add(sid)
        row_cwd = (live and live.get("cwd")) or cwd_of(jsonl)
        row_label = labels.get(sid, "")
        out.append({
            "sessionId": sid,
            "cwd": row_cwd,
            "updatedAt": mtime,
            "first": first,
            "last": last,
            "turns": turns,
            "isLive": live is not None,
            "label": row_label,
            "pid": (live or {}).get("pid"),
            "status": (live or {}).get("status", ""),
            "startedAt": (live or {}).get("startedAt", 0),
            "currentTheme": (live or {}).get("currentTheme", ""),
            "idleSeconds": (live or {}).get("idleSeconds"),
            "parent": parents_map.get(sid, ""),
            "pinned": sid in pinned_set,
            "category": categories_map.get(sid, ""),
            "archived": sid in archived_set,
            # Deterministic detection: label + cwd (explicit naming) +
            # Jira URLs pasted by the user (URL paste is intentional — bare
            # 'PTECH-XXX' mentions in text are NOT scanned) +
            # tickets the session created via createJiraIssue MCP +
            # user-curated manual links, minus user-removed false positives.
            "jira": (sorted(
                (
                    set(extract_jira_tickets(row_label, row_cwd))
                    | set(scan_jira_urls_in_user_turns(jsonl))
                    | set(scan_jira_created_in_transcript(jsonl))
                    | set(jira_links.get(sid, []))
                )
                - set(jira_unlinks.get(sid, []))
            ) if JIRA_ENABLED else []),
            "cost": compute_session_cost(jsonl).get("dollars", 0.0),
            "agent": "claude",
        })
    # Live sessions without a transcript yet (rare — only at session birth).
    for sid, live in live_by_sid.items():
        if sid in seen:
            continue
        row_cwd = live.get("cwd", "")
        row_label = labels.get(sid, "")
        out.append({
            "sessionId": sid,
            "cwd": row_cwd,
            "updatedAt": time.time(),
            "first": "",
            "last": "",
            "turns": 0,
            "isLive": True,
            "label": row_label,
            "pid": live.get("pid"),
            "status": live.get("status", ""),
            "startedAt": live.get("startedAt", 0),
            "currentTheme": live.get("currentTheme", ""),
            "idleSeconds": live.get("idleSeconds"),
            "parent": parents_map.get(sid, ""),
            "pinned": sid in pinned_set,
            "category": categories_map.get(sid, ""),
            "archived": sid in archived_set,
            "jira": (sorted(
                (
                    set(extract_jira_tickets(row_label, row_cwd))
                    | set(jira_links.get(sid, []))
                )
                - set(jira_unlinks.get(sid, []))
            ) if JIRA_ENABLED else []),
            "cost": 0.0,  # live-only entries with no transcript yet — cost is 0
            "agent": "claude",
        })
    # ---- Codex sessions. Discovered from ~/.codex rollout files; a session the
    # dashboard launched also has a live registry record (AGENT_SESS_DIR), which
    # supplies its pid/identity and marks it live. Codex has no pid file of its
    # own, so un-launched (externally started) codex sessions still surface as
    # history. Wrapped defensively: a Codex parse hiccup must never break the
    # (Claude-critical) sessions list. ----
    try:
        codex_sessions = agents.get_agent("codex").list_sessions(limit=n)
    except Exception:
        codex_sessions = []
    # Live launch records keyed by normalized cwd (newest wins). Each fresh codex
    # session gets its own ~/cs folder, so cwd is a unique key back to its pid.
    def _norm_cwd(p: str) -> str:
        return os.path.normcase(os.path.normpath(p)) if p else ""
    codex_live: dict[str, dict] = {}
    try:
        for _r in _read_agent_session_files():
            if _r.get("agent") != "codex":
                continue
            _k = _norm_cwd(_r.get("cwd", ""))
            if not _k:
                continue
            _prev = codex_live.get(_k)
            if _prev is None or _r.get("startedAt", 0) >= _prev.get("startedAt", 0):
                codex_live[_k] = _r
    except Exception:
        codex_live = {}
    _claimed_cwds: set[str] = set()
    for cs in codex_sessions:
        sid = cs.session_id
        if sid in seen:
            continue
        # A live launch record for this cwd (not yet claimed by a newer session
        # in the newest-first list) makes this the live session for that folder.
        _ck = _norm_cwd(cs.cwd)
        _rec = codex_live.get(_ck) if _ck not in _claimed_cwds else None
        is_live = _rec is not None
        # Same empty-shell filter Claude uses: drop zero-turn history with no
        # user-applied label/pin/category/archive — unless it's live.
        if (not is_live and cs.turns == 0 and not labels.get(sid)
                and sid not in pinned_set and sid not in categories_map
                and sid not in archived_set):
            continue
        seen.add(sid)
        if is_live:
            _claimed_cwds.add(_ck)
        row = cs.to_row()
        row["isLive"] = is_live
        row_cwd = row.get("cwd", "")
        row_label = labels.get(sid, "")
        row.update({
            "label": row_label,
            "pid": (int(_rec["pid"]) if is_live else None),
            "identity": (_rec.get("identity", "") if is_live else ""),
            "status": "",
            "currentTheme": "",
            "idleSeconds": None,
            "parent": parents_map.get(sid, ""),
            "pinned": sid in pinned_set,
            "category": categories_map.get(sid, ""),
            "archived": sid in archived_set,
            "jira": (sorted(
                (set(extract_jira_tickets(row_label, row_cwd)) | set(jira_links.get(sid, [])))
                - set(jira_unlinks.get(sid, []))
            ) if JIRA_ENABLED else []),
            "cost": 0.0,
        })
        out.append(row)
    # Sort: tiered by activity, then by updatedAt desc within each tier.
    # Tier 0: live & busy   (orange blinker — claude is doing something)
    # Tier 1: live & idle
    # Tier 2: historical
    # Within each tier, most-recently-updated first. Combined with the
    # hover-freeze on the client, this gives "busy on top" without rows
    # shuffling out from under your mouse.
    def _key(r):
        if r["isLive"]:
            tier = 0 if r.get("status") == "busy" else 1
            return (tier, -r["updatedAt"])
        return (2, -r["updatedAt"])
    # Each headless session (solo or collaboration) becomes ONE row that opens
    # its window; its per-agent sub-sessions are hidden (they'd otherwise scatter
    # as a live claude row + a codex history row). Matched by agent session id
    # and per-agent subfolder cwd.
    collab_sids: set[str] = set()
    collab_cwds: set[str] = set()
    room_rows: list[dict] = []
    try:
        for rm in chatroom.list_rooms():
            agents_in = [p for p in rm.get("participants", [])
                         if p.get("kind") == "agent"]
            for pp in agents_in:
                if pp.get("sessionId"):
                    collab_sids.add(pp["sessionId"])
                if pp.get("cwd"):
                    collab_cwds.add(os.path.normcase(os.path.normpath(pp["cwd"])))
            live = _room_is_live(rm)
            idle = None
            busy = False
            for pp in agents_in:
                pid = pp.get("ptyId")
                sess = ptyrun.get(pid) if pid else None
                if sess:
                    isec = sess.info().get("idleSeconds")
                    if isec is not None:
                        idle = isec if idle is None else min(idle, isec)
                        if isec < 2.5:
                            busy = True
            rid = rm["id"]
            # Rename and pin apply to a room via the same label/pin sidecars a
            # single-agent session uses (keyed by the room id), so the shared
            # rename/pin buttons "just work" — read them back here.
            msgs = rm.get("messages", []) or []
            _user_msgs = [m for m in msgs
                          if m.get("from") == "user" and (m.get("text") or "").strip()]
            first_txt = ((_user_msgs[0] if _user_msgs else (msgs[0] if msgs else {})).get("text") or "")[:200]
            last_txt = ((msgs[-1] if msgs else {}).get("text") or "")[:200]
            room_rows.append({
                "sessionId": rid, "roomId": rid, "headless": True,
                "mode": rm.get("mode", ""),
                "agent": (agents_in[0]["agent"] if len(agents_in) == 1 else "duo"),
                "agents": [p.get("identity", "") for p in agents_in],
                "members": [{"identity": p.get("identity", ""),
                             "agent": p.get("agent", ""),
                             "model": p.get("model", ""),
                             "role": p.get("role", "")} for p in agents_in],
                "label": labels.get(rid) or rm.get("title", ""), "cwd": rm.get("cwd", ""),
                "spec": rm.get("spec", ""), "taskDir": rm.get("taskDir", ""),
                # A draft is a task created (e.g. by a planning agent) but never
                # launched; Open/Start launches it fresh with its spec.
                "draft": not rm.get("launched", True),
                "isLive": live, "status": "busy" if (live and busy) else "idle",
                "updatedAt": rm.get("updatedAt", rm.get("createdAt", 0)),
                "startedAt": rm.get("createdAt", 0),
                "turns": len(rm.get("messages", [])),
                "idleSeconds": (idle if live else None),
                "pid": None, "pinned": rid in pinned_set, "category": "", "archived": False,
                "parent": "", "jira": [], "cost": compute_room_cost(rm).get("dollars", 0.0),
                "currentTheme": "",
                "first": first_txt, "last": last_txt, "transcriptPath": "",
            })
    except Exception:
        pass
    if collab_sids or collab_cwds:
        out = [r for r in out
               if r.get("sessionId") not in collab_sids
               and os.path.normcase(os.path.normpath(r.get("cwd", "") or "."))
                   not in collab_cwds]
    # Orphaned headless sub-sessions (a finished collaboration whose room record
    # is gone) live in CS_ROOT/<slug>/<identity>. Group siblings by their parent
    # folder into ONE row tagged with the agents, so a duo doesn't split back
    # into separate claude + codex rows.
    cs_root_n = os.path.normcase(os.path.normpath(str(CS_ROOT)))
    orphan_groups: dict[str, dict] = {}
    kept: list[dict] = []
    for r in out:
        cwd = r.get("cwd", "") or ""
        parent = os.path.dirname(cwd)
        is_subfolder = (
            os.path.normcase(os.path.normpath(os.path.dirname(parent))) == cs_root_n
            and bool(_NUMBERED_RE.match(os.path.basename(parent))))
        if is_subfolder and not r.get("isLive"):
            g = orphan_groups.setdefault(
                os.path.normcase(os.path.normpath(parent)),
                {"parent": parent, "rows": []})
            g["rows"].append(r)
        else:
            kept.append(r)
    out = kept
    for key, g in orphan_groups.items():
        rs = g["rows"]
        if len(rs) < 2:
            out.extend(rs)          # a lone sub-session isn't a collaboration
            continue
        members = [{"identity": os.path.basename(r.get("cwd", "")) or r.get("agent", ""),
                    "agent": r.get("agent", ""), "model": "",
                    "sessionId": r.get("sessionId", ""),
                    "cwd": r.get("cwd", "")} for r in rs]
        out.append({
            "sessionId": "grp:" + key, "roomId": "", "headless": True,
            "orphan": True, "mode": "collab", "agent": "duo",
            "agents": [m["identity"] for m in members], "members": members,
            "label": os.path.basename(g["parent"]), "cwd": g["parent"],
            "isLive": False, "status": "idle",
            "updatedAt": max((r.get("updatedAt", 0) for r in rs), default=0),
            "startedAt": 0, "turns": sum(r.get("turns", 0) for r in rs),
            "idleSeconds": None, "pid": None, "pinned": False, "category": "",
            "archived": False, "parent": "", "jira": [],
            "cost": sum(r.get("cost", 0) or 0 for r in rs),
            "currentTheme": "", "first": "", "last": "", "transcriptPath": "",
        })
    out.extend(room_rows)
    # Flag sessions run OUTSIDE the dashboard (cwd not under ~/cs) so the UI can
    # optionally hide them. Headless rooms/orphans are always dashboard-managed.
    cs_root_n2 = os.path.normcase(os.path.normpath(str(CS_ROOT)))
    for r in out:
        cwd_n = os.path.normcase(os.path.normpath(r.get("cwd", "") or "."))
        under_cs = cwd_n == cs_root_n2 or cwd_n.startswith(cs_root_n2 + os.sep)
        r["external"] = (not r.get("headless")) and not under_cs
    out.sort(key=_key)
    return out[:n]


# ---------- self-update ----------

_UPDATE_CHECK_CACHE: tuple[float, dict] | None = None
_UPDATE_CHECK_TTL = 1800  # 30 minutes — keep the GitHub fetch infrequent.
_UPDATE_CHECK_LOCK = False  # crude reentrancy guard for concurrent /update-check calls


def check_for_update(force: bool = False) -> dict:
    """Does the install dir have unseen upstream commits? Returns a small
    dict the frontend can decide whether to surface. Cached so we don't hit
    the remote on every page load. Returns available=False (with a reason)
    on any failure so the banner stays hidden."""
    global _UPDATE_CHECK_CACHE, _UPDATE_CHECK_LOCK
    now = time.time()
    if not force and _UPDATE_CHECK_CACHE and (now - _UPDATE_CHECK_CACHE[0]) < _UPDATE_CHECK_TTL:
        return _UPDATE_CHECK_CACHE[1]
    if _UPDATE_CHECK_LOCK:
        if _UPDATE_CHECK_CACHE:
            return _UPDATE_CHECK_CACHE[1]
        return {"available": False, "reason": "checking"}
    _UPDATE_CHECK_LOCK = True
    try:
        install_dir = STATIC_DIR
        if not (install_dir / ".git").exists():
            result = {"available": False, "reason": "not_a_git_checkout"}
        else:
            try:
                def git(*args) -> str:
                    return subprocess.run(
                        ["git", *args], cwd=str(install_dir),
                        capture_output=True, text=True, timeout=5,
                    ).stdout.strip()
                # Fetch the remote our branch actually tracks — not always
                # "origin". Otherwise we'd fetch origin but compare against
                # tr/main and get stale/inconsistent numbers.
                upstream_ref = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
                remote_name = upstream_ref.split("/", 1)[0] if "/" in upstream_ref else "origin"
                fetch = subprocess.run(
                    ["git", "fetch", "--quiet", remote_name],
                    cwd=str(install_dir),
                    capture_output=True, text=True, timeout=15,
                )
                if fetch.returncode != 0:
                    result = {"available": False, "reason": "fetch_failed"}
                else:
                    current = git("rev-parse", "HEAD")
                    upstream = git("rev-parse", "@{u}")
                    if not current or not upstream:
                        result = {"available": False, "reason": "no_upstream"}
                    else:
                        # Only "behind upstream" counts as an update. Being
                        # ahead of upstream (unpushed local work) isn't a
                        # user-facing update.
                        behind_str = git("rev-list", "--count", f"{current}..{upstream}")
                        behind = int(behind_str) if behind_str.isdigit() else 0
                        if behind == 0:
                            result = {"available": False, "currentSha": current[:7]}
                        else:
                            latest_msg = git("log", "-1", "--format=%s", upstream)
                            result = {
                                "available": True,
                                "currentSha": current[:7],
                                "latestSha": upstream[:7],
                                "behind": behind,
                                "latestMessage": latest_msg[:120],
                            }
            except (subprocess.SubprocessError, OSError) as e:
                result = {"available": False, "reason": f"error: {e.__class__.__name__}"}
        _UPDATE_CHECK_CACHE = (now, result)
        return result
    finally:
        _UPDATE_CHECK_LOCK = False


def trigger_update() -> dict:
    """Pull latest + restart via the platform backend (launchd on macOS,
    Task Scheduler on Windows). Returns immediately."""
    result = BACKEND.self_update(STATIC_DIR)
    if result.get("started"):
        global _UPDATE_CHECK_CACHE
        _UPDATE_CHECK_CACHE = None
    return result


# ---------- HTTP server ----------

# ---------------------------------------------------------------------------
# Task service — the ONE implementation behind both the REST endpoints the UI
# calls and the ensemble_* MCP tools agents call. A task is a room (plus its
# folder under the project home); "launching" it is a separate step owned by
# the Handler (it needs the server port for the MCP URL).
# ---------------------------------------------------------------------------

def normalize_agent_specs(agent_list) -> tuple[list[tuple[str, str, str]], str]:
    """Turn the caller's agent list — plain keys ("claude") or objects
    {agent, model, role} — into (agent_key, model, role) tuples, checking each
    agent is installed. Returns (specs, error)."""
    specs = []
    if not isinstance(agent_list, list) or len(agent_list) < 1:
        return [], "need_an_agent"
    for item in agent_list:
        if isinstance(item, dict):
            ak = (item.get("agent") or "").strip()
            mdl = (item.get("model") or "").strip()
            role = (item.get("role") or "").strip()
        else:
            ak, mdl, role = str(item).strip(), "", ""
        ag = agents.get_agent(ak)
        if ag is None or not ag.installed():
            return [], f"agent_unavailable:{ak}"
        specs.append((ak, mdl, role))
    return specs, ""


def find_project(project_id: str) -> dict | None:
    pid = (project_id or "").strip()
    if not pid:
        return None
    return next((pr for pr in load_projects() if pr["id"] == pid), None)


def create_task(title: str, spec: str, project_id: str, agent_list,
                workspace: str = "empty") -> tuple[bool, dict | None, str]:
    """Create a task: its room, workspace and task folder — WITHOUT launching
    the agents (``launched`` is False until the Handler starts it). Returns
    (ok, room_full, error)."""
    title = (title or "multiagent session").strip()[:120]
    spec = (spec or "").strip()
    specs, err = normalize_agent_specs(agent_list)
    if err:
        return False, None, err
    project_id = (project_id or "").strip()
    project = None
    if project_id:
        project = find_project(project_id)
        if project is None:
            return False, None, "no_such_project"
    ok, base, ws_meta, msg = setup_session_workspace(project, workspace or "empty", title)
    if not ok:
        return False, None, msg
    members = [{"identity": ak, "agent": ak, "model": mdl, "role": role}
               for ak, mdl, role in specs]
    room = chatroom.create_room(title, members)
    room_full = chatroom.get_room(room["id"], public=False)
    room_full["cwd"] = base
    room_full["projectId"] = project_id
    room_full["workspace"] = ws_meta
    room_full["spec"] = spec            # the task's specification, shown in the UI
    room_full["taskDir"] = ws_meta.get("taskDir", "")
    # Project-backed workspaces (inplace/copy/worktree) are shared: agents
    # collaborate on the same files, not isolated per-identity subdirs.
    room_full["sharedCwd"] = ws_meta.get("mode", "empty") != "empty"
    # 1 agent → a solo session the human drives directly (no chat tools,
    # terminal-primary window). 2+ → an autonomous collaboration.
    room_full["mode"] = "solo" if len(specs) < 2 else "collab"
    room_full["launched"] = False
    if project_id:
        assign_session_project(room["id"], project_id)
    if ws_meta.get("taskDir"):
        _write_task_json(ws_meta["taskDir"], {
            "roomId": room["id"], "projectId": project_id, "title": title,
            "spec": spec,
            # The room's participants, not the requested list: create_room
            # de-duplicates identities (claude, claude-2), and task.json is
            # meant to mirror the room record — which is what reassignment
            # keeps it in step with.
            "agents": [{"identity": p["identity"], "agent": p.get("agent", ""),
                        "model": p.get("model", ""), "role": p.get("role", "")}
                       for p in chatroom.agent_participants(room_full)],
            "mode": room_full["mode"], "workspace": ws_meta,
            "createdAt": int(time.time())})
    # Persist BEFORE any launch so an interrupted spawn leaves a resumable
    # draft, not a corrupt room with no cwd.
    chatroom.update_room(room_full)
    return True, room_full, ""


def _patch_task_json(folder: str, **fields) -> None:
    if not folder:
        return
    p = Path(folder) / "task.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data.update(fields)
    _write_task_json(folder, data)


def update_task(rid: str, title=None, spec=None) -> tuple[bool, dict | None, str]:
    """Amend a task's title and/or spec (room record, task.json and any label
    override the user set from the UI)."""
    room = chatroom.get_room(rid, public=False)
    if room is None:
        return False, None, "no_such_room"
    patch = {}
    if title is not None:
        t = str(title).strip()[:120]
        if not t:
            return False, None, "empty_title"
        room["title"] = t
        patch["title"] = t
        labels = load_labels()
        if rid in labels:              # a UI rename would otherwise mask the change
            labels[rid] = t
            save_labels(labels)
    if spec is not None:
        s = str(spec).strip()
        if not s:
            return False, None, "empty_spec"
        room["spec"] = s
        patch["spec"] = s
    if not patch:
        return False, None, "nothing_to_change"
    chatroom.update_room(room)
    _patch_task_json(room.get("taskDir", ""), **patch)
    return True, room, ""


def reassign_task(rid: str, agent_list) -> tuple[bool, dict | None, str]:
    """Change WHO works a task — add an agent, drop one, or change an agent's
    model or role — on a task that is not running.

    Refused outright while the task is live: hot-swapping an agent under a
    running collaboration is a product decision we've taken the other way. Stop
    the task first, reassign, start it again.

    Agents that stay keep their identity, bearer token, session id and working
    dir (see :func:`chatroom.set_agents`), so a retained agent resumes its own
    transcript and an unrelated change never invalidates its MCP client. The
    room's mode follows the head-count (one agent = solo, two or more = collab)
    and ``task.json`` is patched to match. Returns (ok, room_full, error)."""
    room = chatroom.get_room(rid, public=False)
    if room is None:
        return False, None, "no_such_room"
    if _room_is_live(room) or _room_has_live_pty(rid) or _room_has_linked_agent(room):
        return False, None, "task_is_running"
    specs, err = normalize_agent_specs(agent_list)
    if err:
        return False, None, err
    # normalize_agent_specs validates and drops the identity; recover it from the
    # caller's own list (same order) so a retained agent can be pinned by name.
    idents = [(a.get("identity") or "").strip() if isinstance(a, dict) else ""
              for a in agent_list]
    members = [{"identity": ident, "agent": ak, "model": mdl, "role": role}
               for ident, (ak, mdl, role) in zip(idents, specs)]
    mode = "solo" if len(specs) < 2 else "collab"
    room = chatroom.set_agents(rid, members, mode=mode)
    if room is None:
        return False, None, "no_such_room"
    assigned = [{"identity": pp["identity"], "agent": pp.get("agent", ""),
                 "model": pp.get("model", ""), "role": pp.get("role", "")}
                for pp in chatroom.agent_participants(room)]
    _patch_task_json(room.get("taskDir", ""), agents=assigned, mode=room["mode"])
    return True, room, ""


def stop_task(rid: str) -> bool:
    """End a task's agents (kill their PTYs) but KEEP the room, so it stays one
    row with its spec and chat and can be resumed."""
    room = chatroom.get_room(rid, public=False)
    if not room:
        return False
    for part in room.get("participants", []):
        pid = part.get("ptyId")
        if pid:
            try:
                ptyrun.kill(pid)
            except Exception:
                pass
    return True


def delete_task(rid: str, members=None) -> dict:
    """Unified delete for a task/collaboration (or a grouped orphan): stop the
    agents, remove the room record AND every member's transcript (so it can't
    resurface as an orphan/history row), and — when the working dir is a
    dashboard ~/cs/<NN_slug> scratch folder — delete that folder too. A real
    project folder (and a task folder under the projects root) is NEVER removed."""
    members = list(members or [])
    room = chatroom.get_room(rid, public=False) if rid else None
    result = {"transcripts": [], "folders": [], "ok": True}
    cwds: list[str] = []
    if room:
        stop_task(rid)
        cwds.append(room.get("cwd", "") or "")
        members = [{"agent": pp.get("agent", ""),
                    "sessionId": pp.get("sessionId", ""),
                    "cwd": pp.get("cwd", "")}
                   for pp in room.get("participants", [])
                   if pp.get("kind") == "agent"]
    for m in members:
        sid = (m.get("sessionId") or "").strip()
        agent = (m.get("agent") or "claude").strip() or "claude"
        cwds.append(m.get("cwd", "") or "")
        if not sid:
            continue
        if agent == "claude":
            result["transcripts"] += delete_session(sid).get("files", [])
        else:
            ag = agents.get_agent(agent)
            if ag is not None and hasattr(ag, "delete_session"):
                try:
                    result["transcripts"] += ag.delete_session(sid)
                except Exception:
                    pass
    if rid:
        chatroom.delete_room(rid)
        links = load_session_projects()
        if rid in links:
            links.pop(rid, None)
            save_session_projects(links)
    seen: set[str] = set()
    for c in cwds:
        folder = _delete_scratch_root(c)
        if folder and folder not in seen:
            seen.add(folder)
            result["folders"].append(folder)
    return result


def move_task(rid: str, project_id: str) -> bool:
    """Re-link a task to another project (the folder stays where it is)."""
    room = chatroom.get_room(rid, public=False)
    if room is None:
        return False
    assign_session_project(rid, project_id)
    room["projectId"] = project_id
    chatroom.update_room(room)
    _patch_task_json(room.get("taskDir", ""), projectId=project_id)
    return True


# ---------------------------------------------------------------------------
# Agent skills — the 'ensemble' skill teaches an agent how to use the task
# tools. Installed (refreshed) at startup into each installed agent's user-level
# skills folder, so it is available whatever the task's working directory is.
# ---------------------------------------------------------------------------
SKILLS_SRC = STATIC_DIR / "skills"


def install_agent_skills() -> list[str]:
    """Copy skills/<name>/SKILL.md into ~/.claude/skills/<name>/ and (when the
    codex CLI is present) ~/.codex/skills/<name>/. Idempotent: rewrites only
    when the content differs. Returns the paths written."""
    written = []
    if not SKILLS_SRC.is_dir():
        return written
    targets = [HOME / ".claude" / "skills"]
    if shutil.which("codex"):
        targets.append(HOME / ".codex" / "skills")
    for src_dir in sorted(SKILLS_SRC.iterdir()):
        src = src_dir / "SKILL.md"
        if not src_dir.is_dir() or not src.is_file():
            continue
        try:
            body = src.read_text(encoding="utf-8")
        except OSError:
            continue
        for root in targets:
            dest = root / src_dir.name / "SKILL.md"
            try:
                if dest.exists() and dest.read_text(encoding="utf-8") == body:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(body, encoding="utf-8")
                written.append(str(dest))
            except OSError:
                continue
    return written


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        # Disable Nagle's algorithm — tiny terminal-keystroke packets must go out
        # immediately, otherwise interactive typing lags ~200ms-1s on a LAN.
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    def log_message(self, fmt, *args):
        # Guard against a missing/closed stderr — under pythonw.exe (Windows,
        # windowless) there is no console stream, and writes would raise.
        if not sys.stderr:
            return
        try:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")
        except (OSError, ValueError):
            pass

    # --- Access gate ---------------------------------------------------------
    def _client_ip(self) -> str:
        ip = self.client_address[0] if self.client_address else ""
        return ip[7:] if ip.startswith("::ffff:") else ip

    def _presented_token(self, u) -> str:
        q = parse_qs(u.query)
        if q.get("token"):
            return q["token"][0]
        h = self.headers.get("X-Ensemble-Token")
        if h:
            return h.strip()
        auth = self.headers.get("Authorization", "")
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            part = part.strip()
            if part.startswith(TOKEN_COOKIE + "="):
                return part[len(TOKEN_COOKIE) + 1:]
        return ""

    def _gate(self) -> bool:
        """Return True if the request may proceed. When an ACCESS_TOKEN is set,
        non-loopback requests must present it; a matching ?token= on a GET is
        swapped for an HttpOnly cookie via redirect so the URL stays clean."""
        if not ACCESS_TOKEN or _addr_is_loopback(self._client_ip()):
            return True
        u = urlparse(self.path)
        tok = self._presented_token(u)
        if tok and hmac.compare_digest(tok, ACCESS_TOKEN):
            q = parse_qs(u.query)
            if q.get("token") and self.command == "GET":
                rest = "&".join(f"{k}={v[0]}" for k, v in q.items() if k != "token")
                dest = u.path + (("?" + rest) if rest else "")
                self.send_response(303)
                self.send_header("Location", dest or "/")
                self.send_header(
                    "Set-Cookie",
                    f"{TOKEN_COOKIE}={ACCESS_TOKEN}; HttpOnly; SameSite=Lax; "
                    f"Path=/; Max-Age=31536000")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        body = b"401 Unauthorized: append ?token=<your token> to the URL.\n"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass
        return False

    def _send_json(self, code: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._gate():
            return
        u = urlparse(self.path)
        p = u.path
        if p == "/mcp":
            # We don't offer a server-initiated SSE stream (the "doorbell" is a
            # terminal keystroke instead); tell clients the GET stream is absent.
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p == "/pty-test":
            self._send_file(STATIC_DIR / "pty-test.html",
                            "text/html; charset=utf-8")
            return
        if p == "/session":
            self._send_file(STATIC_DIR / "session.html",
                            "text/html; charset=utf-8")
            return
        if p in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if p.startswith("/static/"):
            sub = p[len("/static/"):]
            # Reject any path that tries to escape the static dir.
            if ".." in sub or sub.startswith("/"):
                self.send_error(400)
                return
            f = STATIC_DIR / "static" / sub
            ext = f.suffix.lower()
            mime = {".png": "image/png", ".ico": "image/x-icon",
                    ".svg": "image/svg+xml", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(ext, "application/octet-stream")
            self._send_file(f, mime)
            return
        if p == "/api/live":
            self._send_json(200, load_live())
            return
        if p == "/api/recent":
            q = parse_qs(u.query)
            n = int(q.get("n", ["100"])[0])
            self._send_json(200, load_recent(n))
            return
        if p == "/api/sessions":
            q = parse_qs(u.query)
            n = int(q.get("n", ["200"])[0])
            self._send_json(200, load_sessions(n))
            return
        if p.startswith("/api/session/"):
            sid = p[len("/api/session/"):]
            qs = parse_qs(u.query)
            full = qs.get("full", ["0"])[0] in ("1", "true", "yes")
            tpath = find_transcript(sid)
            want_stat = qs.get("stat", ["0"])[0] in ("1", "true", "yes")
            if not tpath:
                # No Claude transcript — this may be a Codex session, which keeps
                # its own rollout files. Serve those in the same shape so the
                # solo chat view renders a codex session's turns as bubbles too.
                cx = agents.get_agent("codex")
                st = cx.session_stat(sid) if cx is not None else None
                if st is None:
                    self._send_json(404, {"error": "not_found"})
                    return
                if want_stat:
                    self._send_json(200, {"sessionId": sid, "size": st["size"],
                                          "mtime": round(st["mtime"], 3)})
                    return
                turns = cx.read_turns(sid)
                if not full:
                    turns = [{"timestamp": t["timestamp"], "text": t["text"]}
                             for t in turns if t["role"] == "user"]
                self._send_json(200, {"sessionId": sid,
                                      "cwd": cx.cwd_for_session(sid),
                                      "turns": turns,
                                      "label": load_labels().get(sid, "")})
                return
            # Cheap change-signal: the transcript's size+mtime. Clients poll this
            # to know when new turns exist without re-fetching the whole transcript.
            if want_stat:
                try:
                    st = tpath.stat()
                    self._send_json(200, {"sessionId": sid, "size": st.st_size,
                                          "mtime": round(st.st_mtime, 3)})
                except OSError:
                    self._send_json(200, {"sessionId": sid, "size": 0, "mtime": 0})
                return
            if full:
                # User + assistant text turns, with timestamps and roles.
                turns = []
                try:
                    with tpath.open(encoding="utf-8", errors="replace") as f:
                        for line in f:
                            try:
                                d = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            t = d.get("type")
                            if t not in ("user", "assistant") or d.get("isMeta"):
                                continue
                            msg = d.get("message")
                            if not isinstance(msg, dict):
                                continue
                            text = _extract_text(msg.get("content"))
                            if not text:
                                continue
                            stripped = text.strip()
                            if not stripped:
                                continue
                            if t == "user" and (stripped.startswith("<") or stripped.startswith("Caveat:")):
                                continue
                            turns.append({
                                "timestamp": d.get("timestamp", ""),
                                "role": t,
                                "text": stripped,
                            })
                except OSError:
                    pass
            else:
                turns = [{"timestamp": ts, "text": t} for ts, t in iter_user_turns(tpath)]
            labels = load_labels()
            self._send_json(200, {
                "sessionId": sid,
                "cwd": cwd_of(tpath),
                "turns": turns,
                "label": labels.get(sid, ""),
            })
            return
        if p == "/fileview":
            self._send_file(STATIC_DIR / "fileview.html",
                            "text/html; charset=utf-8")
            return
        if p == "/api/file":
            # Stream a local file the agents referenced, so it can be viewed in
            # the browser (remote-friendly). Token-gated like everything else; a
            # token holder already has full access to this machine.
            q = parse_qs(u.query)
            raw = (q.get("path", [""])[0]).strip()
            if not raw:
                self._send_json(400, {"error": "missing_path"})
                return
            # Relative mentions ("docs/plan.md") resolve against the room's
            # folders and its project, so agents needn't spell out full paths.
            fp = resolve_file_ref(raw, room_id=(q.get("room", [""])[0]).strip(),
                                  cwd=(q.get("cwd", [""])[0]).strip())
            if fp is None:
                self._send_json(404, {"error": "not_found"})
                return
            ext = fp.suffix.lower()
            img = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".gif": "image/gif",
                   ".webp": "image/webp", ".svg": "image/svg+xml",
                   ".ico": "image/x-icon", ".bmp": "image/bmp"}
            if ext in img:
                self._send_file(fp, img[ext])
            elif ext == ".pdf":
                self._send_file(fp, "application/pdf")
            else:
                # Everything else (md, code, text, unknown) as inline UTF-8 text.
                self._send_file(fp, "text/plain; charset=utf-8")
            return
        if p == "/api/platform":
            self._send_json(200, BACKEND.info())
            return
        if p == "/api/agents":
            self._send_json(200, agents.agents_info())
            return
        if p == "/api/projects":
            self._send_json(200, build_projects())
            return
        if p == "/api/dir":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            self._send_json(*list_dir(path))
            return
        if p == "/api/ws/file":
            # JSON text read with size cap + binary detection (workspace tools).
            # NOTE: distinct from the older raw-stream /api/file that fileview
            # uses — routing is top-down, so sharing the path would shadow it.
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            self._send_json(*read_workspace_file(path))
            return
        if p == "/api/git/status":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            self._send_json(*git_status(path))
            return
        if p == "/api/git/diff":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            fpath = (q.get("file", [""])[0] or "").strip()
            self._send_json(*git_diff(path, fpath))
            return
        if p == "/api/git/roots":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            try:
                depth = max(1, min(5, int(q.get("depth", ["3"])[0])))
            except ValueError:
                depth = 3
            self._send_json(*git_roots(path, depth))
            return
        if p == "/api/rooms":
            rooms = [_annotate_room_liveness(r) for r in chatroom.list_rooms()]
            self._send_json(200, rooms)
            return
        if p == "/api/room":
            rid = (parse_qs(u.query).get("id", [""])[0]).strip()
            room = chatroom.get_room(rid) if rid else None
            if room is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            self._send_json(200, _annotate_room_liveness(room))
            return
        if p == "/api/ptys":
            ptyrun.reap()
            self._send_json(200, ptyrun.list_sessions())
            return
        if p == "/api/pty/stream":
            pty_id = (parse_qs(u.query).get("id", [""])[0]).strip()
            self._handle_pty_stream(pty_id)
            return
        if p == "/api/themes":
            self._send_json(200, list_presets())
            return
        if p == "/api/theme-colors":
            # Colors for the chat window of a headless session: resolve the
            # session's chosen scheme (by cwd) to concrete colors so the chat
            # view can recolor itself — there's no OS terminal tab to theme.
            q = parse_qs(u.query)
            cwd = (q.get("cwd", [""])[0] or "").strip()
            name = current_theme_for_cwd(cwd) if cwd else ""
            colors = {}
            try:
                colors = BACKEND.theme_colors(name) or {}
            except Exception:
                colors = {}
            self._send_json(200, {"theme": name, "colors": colors})
            return
        if p == "/api/jira-config":
            self._send_json(200, {"enabled": JIRA_ENABLED, "base": JIRA_BASE,
                                  "prefixes": sorted(JIRA_PREFIXES)})
            return
        if p == "/api/update-check":
            self._send_json(200, check_for_update())
            return
        if p == "/api/backup/status":
            bs = load_settings()
            self._send_json(200, {**backup.status(), "root": str(PROJECTS_ROOT),
                                  "remote": bs.get("backupRemote", ""),
                                  "intervalMin": bs.get("backupIntervalMin", 60),
                                  "enabled": bool(bs.get("backupEnabled"))})
            return
        if p == "/api/settings":
            # Include the *resolved* operator display name so the UI can show the
            # human by name (nickname, else git user.name) instead of "user".
            self._send_json(200, {**load_settings(), "operatorName": operator_name()})
            return
        if p.startswith("/api/cost/"):
            sid = p[len("/api/cost/"):]
            if sid.startswith("room-"):
                rm = chatroom.get_room(sid, public=False)
                self._send_json(200, compute_room_cost(rm) if rm
                                else compute_session_cost(None))
                return
            self._send_json(200, compute_session_cost(find_transcript(sid)))
            return
        if p == "/api/config":
            # Bound port comes from the actual listener — what was passed on
            # the CLI may differ from what we end up on after fallback.
            actual_port = self.server.server_address[1] if hasattr(self, "server") else None
            self._send_json(200, {
                "instance": INSTANCE,
                "port": actual_port,
                "os": BACKEND.os_name,
                "stateDir": str(DASHBOARD_DIR),
                "sessionsDir": str(SESS_DIR),
                "projectsDir": str(PROJ_DIR),
                "csRoot": str(CS_ROOT),
                "presetsDir": str(PRESETS_DIR),
                "logFile": str(_LOG_FILE if _LOG_FILE else DEFAULT_LOG_FILE),
                "pid": os.getpid(),
                "python": sys.executable,
            })
            return
        if p.startswith("/api/jira-links/"):
            sid = p[len("/api/jira-links/"):]
            self._send_json(200, load_jira_links().get(sid, []))
            return
        if p == "/api/jira-links":
            self._send_json(200, load_jira_links())
            return
        if p == "/api/favorite-themes":
            self._send_json(200, load_favorite_themes())
            return
        if p == "/api/pinned":
            self._send_json(200, sorted(load_pinned()))
            return
        if p == "/api/categories":
            self._send_json(200, load_categories())
            return
        if p == "/api/category-list":
            self._send_json(200, all_known_categories())
            return
        if p == "/api/archived":
            self._send_json(200, sorted(load_archived()))
            return
        if p == "/api/search":
            q_params = parse_qs(u.query)
            query = (q_params.get("q", [""])[0] or "").strip()
            self._send_json(200, search_transcripts(query) if query else [])
            return
        if p.startswith("/api/repos/"):
            sid = p[len("/api/repos/"):]
            self._send_json(200, find_repos_for_session(sid))
            return
        self.send_error(404)

    def do_PUT(self):
        u = urlparse(self.path)
        p = u.path
        ln = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(ln) if ln else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "bad_json"})
            return
        if p == "/api/backup/now":
            bs = load_settings()
            r = backup.run_backup(PROJECTS_ROOT, bs.get("backupRemote", ""), _export_task_chats)
            self._send_json(200 if r.get("ok") else 500, r)
            return
        if p == "/api/settings":
            if not isinstance(data, dict):
                self._send_json(400, {"error": "expected_object"})
                return
            self._send_json(200, save_settings(data))
            return
        if p.startswith("/api/jira-links/"):
            sid = p[len("/api/jira-links/"):]
            add = (data.get("add") or "").strip().upper().replace("_", "-")
            remove = (data.get("remove") or "").strip().upper().replace("_", "-")
            if not (add or remove):
                self._send_json(400, {"error": "need_add_or_remove"})
                return
            links = load_jira_links()
            unlinks = load_jira_unlinks()
            link_set = set(links.get(sid, []))
            unlink_set = set(unlinks.get(sid, []))
            if add and _JIRA_TICKET_RE.fullmatch(add):
                link_set.add(add)
                unlink_set.discard(add)  # re-adding clears any prior denial
            if remove:
                # Both forget any manual link AND record a denial so the
                # auto-scan can't bring it back on the next refresh.
                link_set.discard(remove)
                unlink_set.add(remove)
            if link_set:
                links[sid] = sorted(link_set)
            else:
                links.pop(sid, None)
            if unlink_set:
                unlinks[sid] = sorted(unlink_set)
            else:
                unlinks.pop(sid, None)
            save_jira_links(links)
            save_jira_unlinks(unlinks)
            self._send_json(200, {"sessionId": sid, "linked": sorted(link_set), "unlinked": sorted(unlink_set)})
            return
        if p.startswith("/api/categories/"):
            sid = p[len("/api/categories/"):]
            cat = (data.get("category") or "").strip()[:80]
            cats = load_categories()
            if cat:
                cats[sid] = cat
            else:
                cats.pop(sid, None)
            save_categories(cats)
            self._send_json(200, {"sessionId": sid, "category": cat})
            return
        if p.startswith("/api/label/"):
            sid = p[len("/api/label/"):]
            label = (data.get("label") or "").strip()[:120]
            labels = load_labels()
            if label:
                labels[sid] = label
            else:
                labels.pop(sid, None)
            save_labels(labels)
            push_label_to_iterm(sid, label)
            self._send_json(200, {"sessionId": sid, "label": label})
            return
        self.send_error(404)

    def do_DELETE(self):
        if not self._gate():
            return
        u = urlparse(self.path)
        p = u.path
        if p == "/mcp":
            # MCP clients DELETE /mcp to end a session; nothing to tear down.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.startswith("/api/label/"):
            sid = p[len("/api/label/"):]
            labels = load_labels()
            labels.pop(sid, None)
            save_labels(labels)
            self._send_json(200, {"sessionId": sid, "label": ""})
            return
        if p.startswith("/api/session/"):
            sid = p[len("/api/session/"):]
            # Refuse to delete a session that is currently live — close it first.
            for s in _read_session_files():
                if s.get("sessionId") == sid:
                    self._send_json(409, {"error": "session_is_live"})
                    return
            cwd = cwd_for_session(sid)
            cx = agents.get_agent("codex")
            # Resolve a Codex session's cwd BEFORE deleting its rollout (after,
            # there's nothing left to resolve it from).
            if not cwd and cx is not None and hasattr(cx, "cwd_for_session"):
                try:
                    cwd = cx.cwd_for_session(sid)
                except Exception:
                    pass
            result = delete_session(sid)         # Claude transcript + sidecars
            # A Codex session has no Claude transcript, so delete_session finds
            # nothing and its row never clears — also remove its rollout files.
            try:
                if cx is not None and hasattr(cx, "delete_session"):
                    removed = cx.delete_session(sid)
                    if removed:
                        result.setdefault("files", []).extend(removed)
            except Exception:
                pass
            folder = _delete_scratch_root(cwd)   # only ~/cs scratch dirs; never real code
            if folder:
                result.setdefault("folders", []).append(folder)
            self._send_json(200, result)
            return
        self.send_error(404)

    def _resolve_live_pid(self, part: dict):
        """Resolve a participant's current terminal pid. Claude sessions come
        from Claude's own registry (by session id); other agents from our launch
        registry (by cwd). Resolved live because a Claude pid isn't known until
        after launch, and pids change across resume."""
        agent_key = part.get("agent", "")
        if agent_key == "claude":
            sid = part.get("sessionId", "")
            for s in _read_session_files():
                if s.get("sessionId") == sid:
                    return s.get("pid")
            return None
        cwd = part.get("cwd", "")
        nk = os.path.normcase(os.path.normpath(cwd)) if cwd else ""
        for r in _read_agent_session_files():
            if (r.get("agent") == agent_key and nk and
                    os.path.normcase(os.path.normpath(r.get("cwd", ""))) == nk):
                return r.get("pid")
        return None

    def _ring_recipients(self, room_id: str, result: dict) -> None:
        """Ring each agent recipient's terminal (the keystroke doorbell) so it
        wakes to read the freshly posted message. Recipients are already
        loop-guard filtered by chatroom.post_message (empty when paused/waiting
        on the human)."""
        recipients = (result or {}).get("recipients") or []
        if not recipients:
            return
        room = chatroom.get_room(room_id)
        if not room:
            return
        sender = (result.get("message") or {}).get("from", "your partner")
        wake = (f"[relay] New message from '{sender}' in your shared room. "
                f"Use the chat_read tool to read it, then reply with chat_send "
                f"— to your partner, or to \"user\" if you need {operator_name()}'s "
                f"input.")
        for ident in recipients:
            part = next((x for x in room["participants"]
                         if x.get("identity") == ident), None)
            if not part:
                continue
            # Headless PTY session → the doorbell is a PTY write.
            pty_id = part.get("ptyId")
            if pty_id:
                sess = ptyrun.get(pty_id)
                if sess and sess.alive():
                    sess.send_line(wake)   # type + discrete Enter to submit
                continue
            # Legacy visible-terminal session → keystroke injection.
            pid = self._resolve_live_pid(part)
            if pid:
                try:
                    BACKEND.send_text(int(pid), wake, submit=True)
                except (OSError, ValueError):
                    pass

    def _mcp_url(self) -> str:
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}/mcp"

    def _launch_room_agent(self, room_full: dict, part: dict, task: str) -> dict:
        """Spawn one agent for a room, pre-wired to the chat MCP with its own
        bearer token, in its own working dir, seeded with the collaboration
        briefing + task. Returns {sessionId, cwd, launch}."""
        ident = part["identity"]
        agent_key = part["agent"]
        token = next((t for t, i in room_full.get("tokens", {}).items()
                      if i == ident), "")
        url = self._mcp_url()
        base = room_full.get("cwd") or str(CS_ROOT)
        # Project-backed sessions share one workspace (agents work on the same
        # files); ad-hoc scratch collaborations keep per-identity subdirs.
        cwd = base if room_full.get("sharedCwd") else os.path.join(base, ident)
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass
        teammates = [{"identity": p["identity"], "role": p.get("role", "")}
                     for p in room_full["participants"]
                     if p.get("kind") == "agent" and p["identity"] != ident]
        briefing = collab_briefing(ident, part.get("role", ""), teammates, task)
        ag = agents.get_agent(agent_key)
        label = room_full["title"][:60]
        model = (part.get("model") or "").strip()
        # Pre-clear each agent's first-run trust gate so the unattended launch
        # starts talking instead of blocking on a prompt no one can answer.
        if hasattr(ag, "ensure_trusted"):
            ag.ensure_trusted(cwd)
        if agent_key == "codex":
            command = ["codex",
                       # never prompt for tool approval — the whole point is
                       # autonomous collaboration; the workspace is a scratch dir.
                       "-c", 'approval_policy="never"',
                       "-c", f'mcp_servers.ensemble.url="{url}"',
                       "-c", 'mcp_servers.ensemble.bearer_token_env_var="CHAT_TOKEN"']
            if model:
                command += ["-c", f'model="{model}"']
            res = BACKEND.open_new(cwd, briefing, label=label, command=command,
                                   agent="codex", identity=ident,
                                   env={"CHAT_TOKEN": token})
            return {"sessionId": "", "cwd": cwd, "launch": res}
        # claude (and claude-N)
        cfg = {"mcpServers": {"ensemble": {"type": "http", "url": url,
                                       "headers": {"Authorization": f"Bearer {token}"}}}}
        mcp_dir = DASHBOARD_DIR / "_mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = mcp_dir / f"{uuid.uuid4().hex}.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        new_sid = str(uuid.uuid4())
        claude_extra = ["--mcp-config", str(cfg_path), "--strict-mcp-config"]
        if model:
            claude_extra += ["--model", model]
        res = BACKEND.open_new(cwd, briefing, label=label, session_id=new_sid,
                               agent="claude", identity=ident,
                               extra_args=claude_extra)
        return {"sessionId": new_sid, "cwd": cwd, "launch": res}

    def _mcp_wiring(self, token: str, collab: bool) -> tuple[list[str], list[str], dict]:
        """The per-agent bits that connect it to the Ensemble MCP server
        (chat + ensemble_* task tools) with its own bearer token. EVERY headless
        agent gets the server — solo tasks included, so an agent can plan and
        manage tasks. Returns (codex_args, claude_args, env).

        collab: an autonomous collaboration additionally bypasses Codex's
        approval prompts (incl. MCP tool approval) and sandbox — nobody is there
        to answer them. A solo agent is human-driven, so its prompts stay."""
        url = self._mcp_url()
        codex_args = ["-c", f'mcp_servers.ensemble.url="{url}"',
                      "-c", 'mcp_servers.ensemble.bearer_token_env_var="CHAT_TOKEN"']
        if collab:
            # (approval_policy="never" would *block* MCP tools.)
            codex_args = ["--dangerously-bypass-approvals-and-sandbox"] + codex_args
        cfg = {"mcpServers": {"ensemble": {"type": "http", "url": url,
                                           "headers": {"Authorization": f"Bearer {token}"}}}}
        mcp_dir = DASHBOARD_DIR / "_mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = mcp_dir / f"{uuid.uuid4().hex}.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        claude_args = ["--mcp-config", str(cfg_path)]
        if collab:
            # Autonomous agents see ONLY our server; a human-driven solo agent
            # keeps the user's own configured MCP servers (Jira etc.) as well.
            claude_args.append("--strict-mcp-config")
        return codex_args, claude_args, {"CHAT_TOKEN": token}

    def _launch_room_agent_pty(self, room_full: dict, part: dict, task: str,
                               collab: bool = True) -> dict:
        """Headless variant: spawn the agent in a dashboard-owned PTY (no
        terminal window). Returns {ptyId, cwd, sessionId}. The PtySession owns
        liveness; the doorbell is a PTY write.

        collab=True: a collaboration briefing (roles, teammates, chat protocol)
        and, for codex, approval bypass for autonomy. collab=False (solo): a
        headless agent the human drives directly through the embedded
        terminal — the task is simply the first message. Both get the Ensemble
        MCP server (task tools always; chat tools only in a collaboration)."""
        ident = part["identity"]
        agent_key = part["agent"]
        model = (part.get("model") or "").strip()
        token = next((t for t, i in room_full.get("tokens", {}).items()
                      if i == ident), "")
        base = room_full.get("cwd") or str(CS_ROOT)
        # Project-backed sessions share one workspace (agents work on the same
        # files); ad-hoc scratch collaborations keep per-identity subdirs.
        cwd = base if room_full.get("sharedCwd") else os.path.join(base, ident)
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass
        if collab:
            teammates = [{"identity": p["identity"], "role": p.get("role", "")}
                         for p in room_full["participants"]
                         if p.get("kind") == "agent" and p["identity"] != ident]
            briefing = collab_briefing(ident, part.get("role", ""), teammates, task)
        else:
            briefing = task  # solo: the task is just the first prompt
        ag = agents.get_agent(agent_key)
        if hasattr(ag, "ensure_trusted"):
            ag.ensure_trusted(cwd)
        label = f"{room_full['title'][:40]} · {ident}"
        meta = {"room": room_full["id"], "identity": ident, "agent": agent_key}
        codex_mcp, claude_mcp, env = self._mcp_wiring(token, collab)
        if agent_key == "codex":
            argv = ["codex", "-c", "check_for_update_on_startup=false"] + codex_mcp
            if model:
                argv += ["-c", f'model="{model}"']
            cmd = BACKEND.headless_launch(cwd, argv, briefing)
            sess = ptyrun.create(cmd, cwd=cwd, env=env, label=label, meta=meta)
            return {"ptyId": sess.id, "cwd": cwd, "sessionId": ""}
        # claude (and claude-N)
        new_sid = str(uuid.uuid4())
        argv = claude_cmd_args("--session-id", new_sid, *claude_mcp)
        if model:
            argv += ["--model", model]
        cmd = BACKEND.headless_launch(cwd, argv, briefing)
        sess = ptyrun.create(cmd, cwd=cwd, label=label, meta=meta)
        return {"ptyId": sess.id, "cwd": cwd, "sessionId": new_sid}

    def _resume_room_agent_pty(self, room_full: dict, part: dict,
                               collab: bool = True, seed: str = "") -> dict:
        """Relaunch an agent in a fresh PTY, RESUMING its prior conversation
        (claude --resume / codex resume). Used to recover a session after a
        dashboard restart killed its PTY. Returns {ptyId, cwd, sessionId}."""
        ident = part["identity"]
        agent_key = part["agent"]
        model = (part.get("model") or "").strip()
        token = next((t for t, i in room_full.get("tokens", {}).items()
                      if i == ident), "")
        base = room_full.get("cwd") or str(CS_ROOT)
        cwd = part.get("cwd") or os.path.join(base, ident)
        ag = agents.get_agent(agent_key)
        if hasattr(ag, "ensure_trusted"):
            ag.ensure_trusted(cwd)
        label = f"{room_full['title'][:40]} · {ident}"
        meta = {"room": room_full["id"], "identity": ident, "agent": agent_key}
        codex_mcp, claude_mcp, env = self._mcp_wiring(token, collab)
        if agent_key == "codex":
            argv = ["codex", "-c", "check_for_update_on_startup=false"] + codex_mcp
            if model:
                argv += ["-c", f'model="{model}"']
            codex_sid = part.get("sessionId") or (
                ag.latest_session_id_for_cwd(cwd)
                if hasattr(ag, "latest_session_id_for_cwd") else "")
            if codex_sid:
                argv += ["resume", codex_sid]   # subcommand goes last
            cmd = BACKEND.headless_launch(cwd, argv, "" if codex_sid else seed)
            sess = ptyrun.create(cmd, cwd=cwd, env=env, label=label, meta=meta)
            return {"ptyId": sess.id, "cwd": cwd, "sessionId": part.get("sessionId", "")}
        # claude
        sid = part.get("sessionId", "")
        resume = ["--resume", sid] if sid else []
        argv = claude_cmd_args(*resume, *claude_mcp)
        if model:
            argv += ["--model", model]
        cmd = BACKEND.headless_launch(cwd, argv, "")
        sess = ptyrun.create(cmd, cwd=cwd, label=label, meta=meta)
        return {"ptyId": sess.id, "cwd": cwd, "sessionId": sid}

    def _start_room(self, room_full: dict) -> list[dict]:
        """First launch of a task's agents (a fresh conversation seeded with the
        spec / collaboration briefing). Marks the room launched. Returns
        [{identity, ptyId}]."""
        task = room_full.get("spec", "") or ""
        collab = room_full.get("mode") != "solo"
        launched = []
        for part in [pp for pp in room_full["participants"] if pp.get("kind") == "agent"]:
            part.pop("fresh", None)     # launching IS its first conversation
            info = self._launch_room_agent_pty(room_full, part, task, collab=collab)
            part["sessionId"] = info["sessionId"]
            part["cwd"] = info["cwd"]
            part["ptyId"] = info["ptyId"]
            launched.append({"identity": part["identity"], "ptyId": info["ptyId"]})
        room_full["launched"] = True
        room_full["status"] = "active"
        room_full["hopCount"] = 0
        room_full["waitingFor"] = ""
        chatroom.update_room(room_full)
        return launched

    def _start_or_resume_room(self, room_full: dict) -> list[dict]:
        """Bring a not-running task up: a draft (never launched) starts fresh,
        anything else relaunches its agents resuming their prior conversations.
        Returns [{identity, ptyId}]."""
        if not room_full.get("launched", True):
            return self._start_room(room_full)
        agents_in = [pp for pp in room_full.get("participants", [])
                     if pp.get("kind") == "agent"]
        solo = room_full.get("mode") == "solo" or len(agents_in) < 2
        # A solo task whose agent never actually got going (no messages yet)
        # is (re)started WITH its specification as the first prompt —
        # otherwise "Open" would bring up a blank agent that idles.
        seed = (room_full.get("spec") or "") if (solo and not room_full.get("messages")) else ""
        resumed = []
        for part in agents_in:
            if part.pop("fresh", False):
                # Assigned to the task after it had already run: there is no
                # conversation to resume, so give it the same first prompt a
                # launch would have (the collaboration briefing, or the spec).
                info = self._launch_room_agent_pty(
                    room_full, part, room_full.get("spec", "") or "", collab=not solo)
                part["sessionId"] = info["sessionId"]
            else:
                info = self._resume_room_agent_pty(room_full, part, collab=not solo, seed=seed)
            part["ptyId"] = info["ptyId"]
            part["cwd"] = info["cwd"]
            resumed.append({"identity": part["identity"], "ptyId": info["ptyId"]})
        room_full["status"] = "active"
        room_full["hopCount"] = 0
        room_full["waitingFor"] = ""
        chatroom.update_room(room_full)
        return resumed

    def _brief_agents(self, room_id: str) -> None:
        """Introduce the room to each agent: its identity, partner(s), and the
        collaboration protocol. Delivered via the doorbell. (The chat tools that
        this references are provided by the MCP server wired at launch.)"""
        room = chatroom.get_room(room_id)
        if not room:
            return
        agents_in = [x for x in room["participants"] if x.get("kind") == "agent"]
        for part in agents_in:
            pid = part.get("pid")
            if not pid:
                continue
            teammates = [{"identity": a["identity"], "role": a.get("role", "")}
                         for a in agents_in if a["identity"] != part["identity"]]
            role = part.get("role", "")
            mates = ", ".join(f"{t['identity']} (the {role_title(t['role'])})"
                              for t in teammates) or "your partner"
            closing = ("Acknowledge your role in one line and wait for the engineer's "
                       "next deliverable before doing any work yourself."
                       if role == "reviewer"
                       else "Pick up the collaboration with chat_send.")
            brief = (
                f"[room '{room['title']}'] You are '{part['identity']}', the "
                f"{role_title(role)} on this team. {role_charter(role, teammates)} "
                f"Team: {mates}. The product owner is {operator_name()} — reach them "
                f"with chat_send to=\"user\". Coordinate via chat_read / chat_send and "
                f"end each turn by messaging a teammate. Write every chat message as "
                f"GitHub-flavored Markdown (headings, lists, inline code, fenced code "
                f"blocks, tables). {closing}"
            )
            try:
                BACKEND.send_text(int(pid), brief, submit=True)
            except (OSError, ValueError):
                pass

    # ---------- headless PTY streaming (xterm.js drill-down) ----------

    def _handle_pty_stream(self, pty_id: str) -> None:
        """SSE: stream a PTY's output to an xterm.js terminal. Sends the current
        screen snapshot first, then live chunks (base64, since terminal bytes
        aren't valid UTF-8 at chunk boundaries)."""
        import base64
        import queue as _queue
        sess = ptyrun.get(pty_id)
        if sess is None:
            self._send_json(404, {"error": "no_such_pty"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        snapshot, q = sess.subscribe()

        def emit(chunk: bytes):
            b64 = base64.b64encode(chunk).decode("ascii")
            self.wfile.write(f"data: {b64}\n\n".encode("ascii"))
            self.wfile.flush()

        try:
            if snapshot:
                emit(snapshot)
            while True:
                try:
                    chunk = q.get(timeout=15)
                except _queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if chunk is None:
                    self.wfile.write(b"event: end\ndata: end\n\n")
                    self.wfile.flush()
                    break
                emit(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sess.unsubscribe(q)

    # ---------- MCP (streamable-HTTP) endpoint for the chat rooms ----------

    def _send_mcp(self, payload, session_id: str = "") -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def _handle_mcp(self, data) -> None:
        """Serve one MCP request (single or JSON-RPC batch) over streamable
        HTTP. Identity comes from the bearer token minted at room creation, so
        every tool call is attributed to the right agent."""
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        resolved = chatroom.resolve_token(token)
        if not resolved:
            self._send_json(401, {"error": "unauthorized"})
            return
        room_id, identity = resolved
        session_id = self.headers.get("Mcp-Session-Id") or room_id
        reqs = data if isinstance(data, list) else [data]
        responses = []
        for req in reqs:
            if not isinstance(req, dict):
                continue
            resp = self._mcp_method(req, room_id, identity)
            if resp is not None:
                responses.append(resp)
        if not responses:
            # Everything was a notification → 202 Accepted, empty body.
            self.send_response(202)
            self.send_header("Content-Length", "0")
            if session_id:
                self.send_header("Mcp-Session-Id", session_id)
            self.end_headers()
            return
        payload = responses if isinstance(data, list) else responses[0]
        self._send_mcp(payload, session_id)

    def _mcp_method(self, req: dict, room_id: str, identity: str):
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code, msg):
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": msg}}

        if method == "initialize":
            return ok({
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ensemble", "version": "2.0"},
            })
        if method is not None and method.startswith("notifications/"):
            return None  # notifications get no JSON-RPC response
        if method == "ping":
            return ok({})
        if method == "tools/list":
            # Task tools for everyone; chat tools only make sense in a
            # collaboration (a solo agent has no teammate to hand off to).
            room = chatroom.get_room(room_id)
            tools = list(ensemble_tools.TOOLS)
            if room and room.get("mode") != "solo":
                tools = list(chatroom.MCP_TOOLS) + tools
            return ok({"tools": tools})
        if method == "tools/call":
            return self._mcp_tool_call(params.get("name"),
                                       params.get("arguments") or {},
                                       room_id, identity, ok, err)
        return err(-32601, f"method not found: {method}")

    def _mcp_tool_call(self, name, args, room_id, identity, ok, err):
        if name in ensemble_tools.NAMES:
            text, is_err = ensemble_tools.call(name, args, room_id, identity, self)
            return ok({"content": [{"type": "text", "text": text}],
                       "isError": is_err})
        if name == "chat_send":
            text = (args.get("message") or "").strip()
            to = (args.get("to") or "").strip()
            if not text:
                return err(-32602, "message is required")
            result = chatroom.post_message(room_id, identity, text, to=to)
            if result is None:
                return err(-32000, "room no longer exists")
            self._ring_recipients(room_id, result)
            status = result["status"]
            note = "delivered"
            if status == "waiting_human":
                note = ("delivered to the human — the collaboration is paused "
                        "until they reply, so stop and wait.")
            elif status == "paused":
                note = ("delivered, but the room reached its turn limit and is "
                        "paused for human review — stop and wait.")
            return ok({"content": [{"type": "text", "text": note}],
                       "isError": False})
        if name == "chat_read":
            msgs = chatroom.read_new_for(room_id, identity)
            if not msgs:
                body = "(no new messages)"
            else:
                body = "\n".join(f"[from {m['from']}] {m['text']}" for m in msgs)
            return ok({"content": [{"type": "text", "text": body}],
                       "isError": False})
        if name == "chat_whoami":
            info = chatroom.whoami(room_id, identity)
            return ok({"content": [{"type": "text", "text": json.dumps(info)}],
                       "isError": False})
        return err(-32602, f"unknown tool: {name}")


    def do_POST(self):
        if not self._gate():
            return
        u = urlparse(self.path)
        p = u.path
        ln = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(ln) if ln else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "bad_json"})
            return
        if p == "/mcp":
            self._handle_mcp(data)
            return
        if p == "/api/pty/create":
            # Dev/testing entry point for a headless PTY process. (Real agent
            # sessions get created server-side by the room launcher.)
            cmd = data.get("cmd")
            if not cmd:
                self._send_json(400, {"error": "missing_cmd"})
                return
            cwd = (data.get("cwd") or "").strip() or None
            rows = int(data.get("rows") or 40)
            cols = int(data.get("cols") or 120)
            sess = ptyrun.create(cmd, cwd=cwd, rows=rows, cols=cols,
                                 label=(data.get("label") or "").strip())
            self._send_json(200, {"id": sess.id, "info": sess.info()})
            return
        if p == "/api/pty/input":
            sess = ptyrun.get((data.get("id") or "").strip())
            if sess is None:
                self._send_json(404, {"error": "no_such_pty"})
                return
            sess.write(data.get("data", ""))
            self._send_json(200, {"ok": True})
            return
        if p == "/api/pty/resize":
            sess = ptyrun.get((data.get("id") or "").strip())
            if sess is None:
                self._send_json(404, {"error": "no_such_pty"})
                return
            sess.resize(int(data.get("rows") or 40), int(data.get("cols") or 120))
            self._send_json(200, {"ok": True})
            return
        if p == "/api/pty/kill":
            self._send_json(200, {"ok": ptyrun.kill((data.get("id") or "").strip())})
            return
        if p == "/api/update":
            # Fire and forget — the spawned `ensemble update`
            # restarts the server via launchctl kickstart -k. Send the 202
            # before that SIGKILL arrives.
            result = trigger_update()
            self._send_json(202 if result.get("started") else 500, result)
            return
        if p == "/api/iterm/consolidate":
            ok, msg = BACKEND.consolidate_windows()
            self._send_json(200 if ok else 500, {"ok": ok, "message": msg})
            return
        if p == "/api/iterm/split-live":
            # The backend resolves each live session's window/tty internally.
            sessions = _read_session_files()
            ok, msg = BACKEND.split_live(sessions)
            self._send_json(200 if ok else 500,
                            {"ok": ok, "message": msg, "count": len(sessions)})
            return
        if p == "/api/focus":
            pid = data.get("pid")
            if not isinstance(pid, int):
                self._send_json(400, {"error": "missing_pid"})
                return
            # Resolve the session's label so backends that focus by window title
            # (Windows Terminal) can match the right window.
            sid = ""
            for s in _read_session_files():
                if s.get("pid") == pid:
                    sid = s.get("sessionId", "")
                    break
            label = load_labels().get(sid, "") if sid else ""
            res = BACKEND.focus({"pid": pid, "sessionId": sid, "label": label})
            self._send_json(200, {"result": res})
            return
        if p == "/api/open":
            sid = data.get("sessionId")
            cwd = data.get("cwd")
            if not sid or not cwd:
                self._send_json(400, {"error": "missing_fields"})
                return
            label = load_labels().get(sid, "")
            agent_key = (data.get("agent") or "claude").strip() or "claude"
            if agent_key != "claude":
                # Non-Claude agent: resume via that agent's own CLI (e.g.
                # `codex resume <id>`), launched verbatim by the OS backend.
                ag = agents.get_agent(agent_key)
                if ag is None:
                    self._send_json(400, {"error": "unknown_agent"})
                    return
                res = BACKEND.open_resume(cwd, sid, label=label,
                                          command=ag.resume_argv(sid))
            else:
                res = BACKEND.open_resume(cwd, sid, label=label)
            self._send_json(200, {"result": res})
            return
        if p == "/api/send":
            # Inject text into a live session's terminal (the chat "doorbell").
            # Empty text with submit=True is a bare Enter — used to confirm
            # prompts (e.g. codex's directory-trust gate) and as a lightweight
            # doorbell ring; only reject when there's nothing to do at all.
            pid = data.get("pid")
            text = data.get("text", "")
            submit = data.get("submit", True)
            if not pid or (not text and not submit):
                self._send_json(400, {"error": "missing_fields"})
                return
            res = BACKEND.send_text(int(pid), text, submit=bool(submit))
            self._send_json(200, {"result": res})
            return
        if p == "/api/room/create":
            title = (data.get("title") or "multiagent session").strip()[:120]
            members = data.get("members") or []
            if not isinstance(members, list) or len(members) < 2:
                self._send_json(400, {"error": "need_two_members"})
                return
            room = chatroom.create_room(title, members)
            # Announce the room to each agent so they know their identity, their
            # partner(s), and the collaboration protocol (chat_send/chat_read).
            self._brief_agents(room["id"])
            self._send_json(200, {"ok": True, "room": chatroom.get_room(room["id"])})
            return
        if p == "/api/projects/new":
            ok, proj, msg = register_project(data.get("path", ""), data.get("name", ""))
            if not ok:
                self._send_json(400, {"error": msg})
                return
            self._send_json(200, {"ok": True, "project": proj})
            return
        if p == "/api/projects/assign":
            sid = (data.get("sessionId") or data.get("roomId") or "").strip()
            pid = (data.get("projectId") or "").strip()
            if not sid:
                self._send_json(400, {"error": "missing_session"})
                return
            assign_session_project(sid, pid)
            self._send_json(200, {"ok": True})
            return
        if p == "/api/projects/delete":
            pid = (data.get("projectId") or "").strip()
            ok = unregister_project(pid)
            self._send_json(200 if ok else 404, {"ok": ok})
            return
        if p == "/api/room/new":
            # Create a task (room + workspace + task folder) and — unless
            # start:false asks for a draft — launch its agents pre-wired to the
            # Ensemble MCP (each with its own identity token), seeded with the
            # spec. The workspace mode is the caller's choice: the framework
            # never forces a code checkout (a project or task may have no code).
            ok, room_full, err = create_task(
                data.get("title"), data.get("task"), data.get("projectId"),
                data.get("agents") or [], data.get("workspace") or "empty")
            if not ok:
                self._send_json(400, {"error": err})
                return
            launched = [] if data.get("start") is False else self._start_room(room_full)
            self._send_json(200, {"ok": True,
                                  "room": chatroom.get_room(room_full["id"]),
                                  "launched": launched})
            return
        if p == "/api/room/say":
            rid = (data.get("roomId") or "").strip()
            text = (data.get("text") or "").strip()
            to = (data.get("to") or "").strip()
            if not rid or not text:
                self._send_json(400, {"error": "missing_fields"})
                return
            result = chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, text, to=to)
            if result is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            self._ring_recipients(rid, result)
            self._send_json(200, {"ok": True, "result": result})
            return
        if p == "/api/room/roles":
            # Assign/clear team roles on an existing room's agents:
            # {roomId, roles: {identity: "engineer"|"reviewer"|"pair"|"<custom>"}}.
            rid = (data.get("roomId") or "").strip()
            roles = data.get("roles") or {}
            if not rid or not isinstance(roles, dict):
                self._send_json(400, {"error": "missing_fields"})
                return
            room_full = chatroom.get_room(rid, public=False)
            if room_full is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            for part in room_full.get("participants", []):
                if part.get("kind") == "agent" and part["identity"] in roles:
                    part["role"] = (str(roles[part["identity"]]) or "").strip()[:400]
            chatroom.update_room(room_full)
            self._send_json(200, {"ok": True, "room": chatroom.get_room(rid)})
            return
        if p == "/api/room/agents":
            # Reassign a task's agents: {roomId, agents:[{identity?, agent,
            # model, role}]}. Agents that stay keep their identity and token;
            # refused while the task is running.
            rid = (data.get("roomId") or "").strip()
            ok, room_full, err = reassign_task(rid, data.get("agents") or [])
            if not ok:
                code = {"no_such_room": 404, "task_is_running": 409}.get(err, 400)
                self._send_json(code, {"error": err})
                return
            self._send_json(200, {"ok": True, "room": chatroom.get_room(rid)})
            return
        if p == "/api/room/status":
            rid = (data.get("roomId") or "").strip()
            status = (data.get("status") or "").strip()
            if status not in ("active", "paused"):
                self._send_json(400, {"error": "bad_status"})
                return
            room = chatroom.set_status(rid, status)
            if room is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            self._send_json(200, {"ok": True, "room": room})
            return
        if p == "/api/session/adopt":
            # Bring existing (legacy / orphaned) session(s) into the headless
            # model: resume them in PTYs as a room, opened in the window.
            members_in = data.get("members")
            if isinstance(members_in, list) and members_in:
                # Multi-agent adopt (e.g. re-open an orphaned collaboration).
                title = (data.get("label") or "session")[:120]
                solo = len(members_in) < 2
                room = chatroom.create_room(
                    title, [{"identity": m.get("agent", ""),
                             "agent": m.get("agent", ""),
                             "model": m.get("model", "")} for m in members_in])
                room_full = chatroom.get_room(room["id"], public=False)
                first_cwd = members_in[0].get("cwd", "")
                room_full["cwd"] = os.path.dirname(first_cwd) or first_cwd
                room_full["mode"] = "solo" if solo else "collab"
                room_full["adopted"] = True
                parts = [pp for pp in room_full["participants"]
                         if pp.get("kind") == "agent"]
                for part, m in zip(parts, members_in):
                    part["sessionId"] = (m.get("sessionId") or "").strip()
                    part["cwd"] = (m.get("cwd") or "").strip()
                    info = self._resume_room_agent_pty(room_full, part,
                                                       collab=not solo)
                    part["ptyId"] = info["ptyId"]
                chatroom.update_room(room_full)
                self._send_json(200, {"ok": True,
                                      "room": chatroom.get_room(room["id"])})
                return
            agent_key = (data.get("agent") or "claude").strip().lower()
            sid = (data.get("sessionId") or "").strip()
            cwd = (data.get("cwd") or "").strip()
            label = (data.get("label") or "").strip()
            if not sid or not cwd:
                self._send_json(400, {"error": "missing_fields"})
                return
            ag = agents.get_agent(agent_key)
            if ag is None or not ag.installed():
                self._send_json(400, {"error": f"agent_unavailable:{agent_key}"})
                return
            title = (label or f"{agent_key} {sid[:8]}")[:120]
            room = chatroom.create_room(title, [{"identity": agent_key,
                                                 "agent": agent_key}])
            room_full = chatroom.get_room(room["id"], public=False)
            room_full["cwd"] = cwd
            room_full["mode"] = "solo"
            room_full["adopted"] = True
            part = next(p for p in room_full["participants"]
                        if p.get("kind") == "agent")
            part["sessionId"] = sid
            part["cwd"] = cwd          # resume in place (no per-agent subfolder)
            info = self._resume_room_agent_pty(room_full, part, collab=False)
            part["ptyId"] = info["ptyId"]
            chatroom.update_room(room_full)
            self._send_json(200, {"ok": True,
                                  "room": chatroom.get_room(room["id"])})
            return
        if p == "/api/room/resume":
            # Recover an ended session after a restart: relaunch each agent in a
            # fresh PTY, resuming its prior conversation.
            rid = (data.get("roomId") or "").strip()
            room_full = chatroom.get_room(rid, public=False)
            if room_full is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            # A draft (created but never launched, e.g. by a planning agent)
            # starts fresh; anything else resumes its agents' conversations.
            resumed = self._start_or_resume_room(room_full)
            self._send_json(200, {"ok": True, "resumed": resumed,
                                  "room": chatroom.get_room(rid)})
            return
        if p == "/api/room/close":
            # End a session: stop every agent's PTY but KEEP the room (marked
            # ended) so a collaboration stays as ONE row tagged with its agents,
            # instead of its per-agent sub-sessions reappearing separately. Use
            # /api/room/dismiss to actually remove it.
            rid = (data.get("roomId") or "").strip()
            # Killing the PTYs makes the room not-live; no explicit 'ended' state.
            self._send_json(200, {"ok": stop_task(rid)})
            return
        if p == "/api/room/delete" or p == "/api/room/dismiss":
            # Unified Delete for a task/collaboration (or a grouped orphan) —
            # see delete_task for exactly what is (and is never) removed.
            rid = (data.get("roomId") or "").strip()
            self._send_json(200, delete_task(rid, data.get("members") or []))
            return
        if p == "/api/fork":
            sid = data.get("sessionId")
            cwd = data.get("cwd")
            label = (data.get("label") or "").strip()[:120]
            user_prompt = (data.get("initialPrompt") or "").strip()
            if not sid or not cwd:
                self._send_json(400, {"error": "missing_fields"})
                return
            # Resolve the actual live cwd in case the recorded one is stale.
            if not Path(cwd).exists():
                for s in _read_session_files():
                    if s.get("sessionId") == sid:
                        live = live_cwd_of_pid(s.get("pid", 0) or 0)
                        if live:
                            cwd = live
                        break
            # Materialise an isolated workspace — worktrees for git repos,
            # cp for everything else — so the fork doesn't fight the original
            # over file state.
            ok, new_cwd, ws_msg = create_fork_workspace(cwd, label)
            if not ok:
                self._send_json(400, {"error": ws_msg})
                return
            # `claude --resume <sid>` only searches the current cwd's project
            # dir. The fork runs in a brand-new dir, so we must seed that
            # project dir with a copy of the original transcript before launch.
            orig_jsonl = find_transcript(sid)
            if orig_jsonl:
                new_proj = PROJ_DIR / claude_project_slug(new_cwd)
                try:
                    new_proj.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(orig_jsonl, new_proj / orig_jsonl.name)
                except OSError:
                    pass
            new_sid = str(uuid.uuid4())
            # Tell claude where the workspace moved, then the user's prompt.
            preamble = (
                f"[forked into isolated workspace]\n"
                f"original cwd: {cwd}\n"
                f"new cwd:      {new_cwd}\n"
                f"(git repos are worktrees; loose files were copied; "
                f"changes here won't affect the original)\n"
            )
            combined_user = "\n\n".join(p for p in (label, user_prompt) if p)
            combined = preamble + ("\n" + combined_user if combined_user else "")
            res = BACKEND.open_resume(new_cwd, sid, fork=True,
                                      new_session_id=new_sid,
                                      initial_prompt=combined,
                                      label=label)
            if res == "ok":
                if label:
                    labels = load_labels()
                    labels[new_sid] = label
                    save_labels(labels)
                parents = load_parents()
                parents[new_sid] = sid
                save_parents(parents)
                # Inherit the parent's category so the fork lands in the same
                # group on the dashboard.
                cats = load_categories()
                parent_cat = cats.get(sid, "")
                if parent_cat:
                    cats[new_sid] = parent_cat
                    save_categories(cats)
            self._send_json(200, {
                "result": res,
                "sessionId": new_sid,
                "newCwd": new_cwd,
                "workspace": ws_msg,
            })
            return
        if p == "/api/new":
            desc = (data.get("description") or "").strip()
            if not desc:
                self._send_json(400, {"error": "missing_description"})
                return
            user_prompt = (data.get("initialPrompt") or "").strip()
            agent_key = (data.get("agent") or "claude").strip().lower()
            ok, path, msg = create_cs_session(desc)
            if not ok:
                self._send_json(400, {"error": msg})
                return
            settings = load_settings()
            open_mode = settings.get("openMode", "window")
            # --- Codex (or any non-claude agent): launch a fresh CLI in the new
            # folder. We can't pre-allocate its session id (codex mints its own
            # and we discover it from ~/.codex rollout files on the next refresh),
            # so there's no sid-keyed label/category here — the session surfaces
            # with its own id shortly after launch.
            if agent_key != "claude":
                ag = agents.get_agent(agent_key)
                if ag is None:
                    self._send_json(400, {"error": f"unknown_agent:{agent_key}"})
                    return
                if not ag.installed():
                    self._send_json(400, {"error": f"{agent_key}_not_installed"})
                    return
                combined = "\n\n".join(p for p in (desc, user_prompt) if p)
                # Pre-authorize the folder so codex doesn't block at its
                # interactive directory-trust gate (no one's there to confirm).
                if hasattr(ag, "ensure_trusted"):
                    ag.ensure_trusted(path)
                identity = _allocate_agent_identity(agent_key)
                # launch_argv("") keeps the prompt out of argv; _launch_wt appends
                # it as a here-string so the CLI receives it as its first message.
                # agent/identity make the launch script self-register the session
                # (liveness + injection target) in AGENT_SESS_DIR.
                argv = ag.launch_argv(path, "")
                # Apply a per-agent model where the CLI supports it (codex reads
                # it as a `-c` config override).
                raw_model = data.get("model")
                sess_model = raw_model.strip()[:80] if isinstance(raw_model, str) else ""
                if sess_model and agent_key == "codex":
                    argv += ["-c", f'model="{sess_model}"']
                res = BACKEND.open_new(path, combined, label=desc,
                                       command=argv, open_mode=open_mode,
                                       agent=agent_key, identity=identity)
                self._send_json(200, {"ok": True, "path": path, "result": res,
                                      "sessionId": "", "agent": agent_key,
                                      "identity": identity})
                return
            # Pre-allocate the session UUID so we can label it before claude
            # has written its first transcript line. Otherwise the dashboard
            # would show "(no turns)" for the brief window between launch and
            # first user message.
            new_sid = str(uuid.uuid4())
            labels = load_labels()
            labels[new_sid] = desc
            save_labels(labels)
            category = (data.get("category") or "").strip()[:80]
            if category:
                cats = load_categories()
                cats[new_sid] = category
                save_categories(cats)
            combined = "\n\n".join(p for p in (desc, user_prompt) if p)
            # `model` from the client picker overrides the persisted default;
            # missing → the persisted default → no --model flag. open_mode
            # (window/tab, read above) is honored where the terminal supports it.
            raw_model = data.get("model")
            model_override = raw_model.strip()[:80] if isinstance(raw_model, str) else None
            chosen_model = (model_override if model_override is not None
                            else settings.get("defaultModel", "")).strip() or None
            res = BACKEND.open_new(path, combined, label=desc, session_id=new_sid,
                                   model=chosen_model, open_mode=open_mode)
            self._send_json(200, {"ok": True, "path": path, "result": res,
                                  "sessionId": new_sid, "agent": "claude"})
            return
        if p == "/api/close":
            pid = data.get("pid")
            if not isinstance(pid, int):
                self._send_json(400, {"error": "missing_pid"})
                return
            sid = ""
            for s in _read_session_files():
                if s.get("pid") == pid:
                    sid = s.get("sessionId", "")
                    break
            res = BACKEND.close({"pid": pid, "sessionId": sid})
            self._send_json(200, {"result": res, "sessionId": sid})
            return
        if p == "/api/open-path":
            target = (data.get("path") or "").strip()
            app = (data.get("app") or "default").strip()
            if not target:
                self._send_json(400, {"error": "missing_path"})
                return
            res = open_path(target, app)
            self._send_json(200, {"result": res})
            return
        if p == "/api/favorite-themes":
            theme = (data.get("theme") or "").strip()
            favorite = bool(data.get("favorite"))
            if not theme:
                self._send_json(400, {"error": "missing_theme"})
                return
            favs = load_favorite_themes()
            if favorite and theme not in favs:
                favs.append(theme)
            elif not favorite and theme in favs:
                favs.remove(theme)
            save_favorite_themes(favs)
            self._send_json(200, {"favorites": favs})
            return
        if p == "/api/categories/delete":
            cat = (data.get("category") or "").strip()
            if not cat:
                self._send_json(400, {"error": "missing_category"})
                return
            cats = load_categories()
            removed = 0
            for sid in list(cats.keys()):
                if cats[sid] == cat:
                    del cats[sid]
                    removed += 1
            save_categories(cats)
            # Also drop from explicit known list.
            known = [x for x in load_known_categories() if x != cat]
            save_known_categories(known)
            self._send_json(200, {"category": cat, "removed": removed})
            return
        if p == "/api/categories/rename":
            src = (data.get("from") or "").strip()
            dst = (data.get("to") or "").strip()[:80]
            if not src or not dst:
                self._send_json(400, {"error": "from_and_to_required"})
                return
            cats = load_categories()
            moved = 0
            for sid, c in list(cats.items()):
                if c == src:
                    cats[sid] = dst
                    moved += 1
            save_categories(cats)
            # Rename in known list too.
            known = [dst if x == src else x for x in load_known_categories()]
            save_known_categories(known)
            self._send_json(200, {"from": src, "to": dst, "moved": moved})
            return
        if p == "/api/category-list":
            name = (data.get("name") or "").strip()[:80]
            if not name:
                self._send_json(400, {"error": "missing_name"})
                return
            known = load_known_categories()
            if name not in known:
                known.append(name)
                save_known_categories(known)
            self._send_json(200, {"categories": all_known_categories()})
            return
        if p == "/api/archived":
            sid = (data.get("sessionId") or "").strip()
            archived = bool(data.get("archived"))
            if not sid:
                self._send_json(400, {"error": "missing_session_id"})
                return
            arch = load_archived()
            if archived:
                arch.add(sid)
            else:
                arch.discard(sid)
            save_archived(arch)
            self._send_json(200, {"archived": sorted(arch)})
            return
        if p == "/api/pinned":
            sid = (data.get("sessionId") or "").strip()
            pinned = bool(data.get("pinned"))
            if not sid:
                self._send_json(400, {"error": "missing_session_id"})
                return
            pins = load_pinned()
            if pinned:
                pins.add(sid)
            else:
                pins.discard(sid)
            save_pinned(pins)
            self._send_json(200, {"pinned": sorted(pins)})
            return
        if p == "/api/theme":
            pid = data.get("pid")
            theme = data.get("theme")
            if not theme:
                self._send_json(400, {"error": "missing_theme"})
                return
            if isinstance(pid, int):
                # Live session: resolve the cwd (falling back to the live cwd if
                # the recorded one is stale) and apply against the running tab.
                cwd = ""
                for s in _read_session_files():
                    if s.get("pid") == pid:
                        cwd = s.get("cwd", "") or ""
                        break
                if cwd and not Path(cwd).exists():
                    live = live_cwd_of_pid(pid)
                    if live:
                        cwd = live
                res = BACKEND.apply_theme({"pid": pid}, theme, cwd)
                self._send_json(200, {"result": res, "cwd": cwd})
                return
            # Historical session: set the theme by cwd for the next Open.
            cwd = (data.get("cwd") or "").strip()
            if not cwd:
                self._send_json(400, {"error": "missing_pid_or_cwd"})
                return
            res = BACKEND.set_theme_for_cwd(cwd, theme)
            self._send_json(200, {"result": res, "cwd": cwd})
            return
        if p.startswith("/api/label/") and p.endswith("/auto"):
            sid = p[len("/api/label/"):-len("/auto")]
            # A room has no transcript of its own — summarise a member's instead
            # (prefer a Claude member, whose transcript claude_rename can read),
            # but save the suggested label under the room id.
            rename_sid = sid
            if sid.startswith("room-"):
                rm = chatroom.get_room(sid, public=False)
                members = chatroom.agent_participants(rm) if rm else []
                chosen = (next((m for m in members
                                if m.get("agent") == "claude" and m.get("sessionId")), None)
                          or next((m for m in members if m.get("sessionId")), None))
                if chosen:
                    rename_sid = chosen["sessionId"]
            label = claude_rename(rename_sid)
            if not label:
                self._send_json(500, {"error": "rename_failed"})
                return
            labels = load_labels()
            labels[sid] = label
            save_labels(labels)
            push_label_to_iterm(sid, label)
            self._send_json(200, {"sessionId": sid, "label": label})
            return
        self.send_error(404)


def main():
    global _LOG_FILE, ACCESS_TOKEN
    args = sys.argv[1:]
    # Port: --port wins, else ENSEMBLE_PORT, else 8765.
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    else:
        port = int(os.environ.get("ENSEMBLE_PORT", "8765"))
    # Bind: --bind wins, else ENSEMBLE_BIND, else 127.0.0.1 (local only).
    # The literal "tailscale"/"ts" means "expose on this machine's tailnet IP".
    # We DON'T resolve it eagerly: at logon Tailscale may still be connecting, so
    # resolving is deferred to a background thread (below) that attaches the
    # tailnet listener once the IP appears. Loopback always comes up immediately.
    if "--bind" in args:
        host = args[args.index("--bind") + 1]
    else:
        host = os.environ.get("ENSEMBLE_BIND", "127.0.0.1")
    tailnet = host.lower() in ("tailscale", "ts")
    # --log <path>: redirect stdout/stderr to a file. Required under pythonw.exe
    # (Windows autostart), which has no console to write to.
    if "--log" in args:
        log_path = Path(args[args.index("--log") + 1])
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            f = open(log_path, "a", buffering=1, encoding="utf-8")
            sys.stdout = f
            sys.stderr = f
            _LOG_FILE = log_path
        except OSError:
            pass
    # Remote bind, if any. Loopback is always served (agents reach /mcp on
    # 127.0.0.1, and so does the local browser) — a non-loopback bind adds a
    # SECOND, token-gated listener on that interface only (e.g. the tailnet IP),
    # so the LAN is never exposed. "0.0.0.0"/"::" is the exception: it already
    # covers loopback, so it replaces the loopback listener rather than doubling.
    # `tailnet` means the remote address is resolved lazily (see below).
    remote_host = None
    if tailnet:
        remote_host = _detect_tailscale_ip()  # may be None now; retried in a thread
    elif not _addr_is_loopback(host):
        remote_host = host
    want_remote = tailnet or remote_host is not None
    wildcard = remote_host in ("0.0.0.0", "::")

    # Access token: required for every non-loopback request. Explicit
    # ENSEMBLE_TOKEN wins; otherwise, when the server will expose a remote
    # listener (now or once Tailscale is up), load-or-mint a stable token
    # persisted under the state dir so URLs/cookies survive a restart.
    ACCESS_TOKEN = os.environ.get("ENSEMBLE_TOKEN", "").strip()
    if want_remote:
        tok_file = DASHBOARD_DIR / "access-token.txt"
        if not ACCESS_TOKEN and tok_file.exists():
            ACCESS_TOKEN = tok_file.read_text(encoding="utf-8").strip()
        if not ACCESS_TOKEN:
            ACCESS_TOKEN = secrets.token_urlsafe(24)
            try:
                tok_file.write_text(ACCESS_TOKEN, encoding="utf-8")
            except OSError:
                pass

    cleaned = cleanup_rename_artifacts()
    if cleaned:
        print(f"cleaned up {cleaned} rename artifact session(s)", flush=True)
    # Refresh the agent skills (the 'ensemble' skill teaches agents the task
    # tools) into each installed agent's user-level skills folder.
    try:
        for pth in install_agent_skills():
            print(f"installed agent skill: {pth}", flush=True)
    except Exception as e:
        print(f"agent skill install skipped: {e}", flush=True)

    def _announce_remote(ip: str) -> None:
        print(f"ensemble [{BACKEND.os_name}]: http://{ip}:{port} (remote)", flush=True)
        if ACCESS_TOKEN:
            print(f"remote access token: {ACCESS_TOKEN}", flush=True)
            print(f"open from another device: http://{ip}:{port}/"
                  f"?token={ACCESS_TOKEN}", flush=True)

    servers = []
    remote_bound = False
    if wildcard:
        servers.append(ThreadingHTTPServer((remote_host, port), Handler))
        _announce_remote(remote_host)
        remote_bound = True
    else:
        servers.append(ThreadingHTTPServer(("127.0.0.1", port), Handler))
        print(f"ensemble [{BACKEND.os_name}]: http://127.0.0.1:{port}", flush=True)
        if remote_host:
            # The remote (tailnet) bind must NEVER kill the hub. Right after a
            # reboot Tailscale's IP is often detectable but not yet assigned to an
            # interface, so bind() raises WinError 10049 — serve loopback now and
            # attach the remote listener in the background once it's bindable.
            try:
                servers.append(ThreadingHTTPServer((remote_host, port), Handler))
                _announce_remote(remote_host)
                remote_bound = True
            except OSError as e:
                print(f"remote bind {remote_host}:{port} failed ({e}); "
                      f"serving loopback, retrying in background", flush=True)

    # Tailnet requested but its listener isn't up yet (never resolved, or bind
    # failed above): keep retrying in the background, without ever blocking startup.
    if tailnet and not remote_bound and not wildcard:
        def _await_tailnet():
            for _ in range(300):  # ~10 min of 2s polls
                time.sleep(2)
                ip = _detect_tailscale_ip() or remote_host
                if not ip:
                    continue
                try:
                    s = ThreadingHTTPServer((ip, port), Handler)
                except OSError:
                    continue   # IP not bindable yet — keep waiting, don't give up
                _announce_remote(ip)
                s.serve_forever()
                return
            print("gave up waiting for Tailscale; serving loopback only", flush=True)
        threading.Thread(target=_await_tailnet, daemon=True).start()

    # Projects backup: the app owns the projects-root repo. Init it now
    # (idempotent; remote only if configured) and start the scheduler, which
    # re-reads settings every tick so changes apply without a restart.
    try:
        _bs = load_settings()
        _r = backup.ensure_repo(PROJECTS_ROOT, _bs.get("backupRemote", ""))
        if _r.get("notes"):
            print("projects backup: " + "; ".join(_r["notes"]), flush=True)
    except Exception as e:
        print(f"projects backup init skipped: {e}", flush=True)

    def _backup_config():
        s = load_settings()
        return (PROJECTS_ROOT, s.get("backupRemote", ""), s.get("backupIntervalMin", 60),
                bool(s.get("backupEnabled")))
    backup.start_scheduler(_backup_config, _export_task_chats)

    # Serve every listener; extra ones run in daemon threads, the last inline.
    for s in servers[:-1]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[-1].serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
