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

import contextlib
import copy
import getpass
import hashlib
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
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urlparse

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
# Which tasks need a human, and why (the /api/attention join).
import attention
# Multi-agent chat rooms (pairing live sessions into a collaborating "duo").
import backup
import chatroom
# The PO's timed progress digest (only when something changed).
import digest
# Task-management MCP tools (ensemble_*) served next to the chat tools.
import ensemble_tools
# A documents project's automatic file history (a private git dir per project).
import history as file_history
# A link to a chat balloon, written out for the agent it is sent to.
import message_refs
import peer_process
# Task numbers (#18, ED-18) and project keys.
import task_numbers
# A fresh PO session from its written handover when its conversation gets long.
import rotation
# Plan-allowance readings (account-wide, per agent kind), refreshed in the
# background so no request path ever waits on the network.
import usage
# Go to file and search in files for a Workspace; a search runs as a child.
import workspace_search
# Headless PTY runtime — dashboard-owned agent processes streamed to the browser.
from backends import ptyrun

# Windows: a console program started by a host that has no console of its own
# (pythonw.exe under the scheduled task) gets a brand-new console window, which
# flashes up and takes the keyboard focus for the life of the call -- a `git
# status` per projects poll made the whole desktop flicker. CREATE_NO_WINDOW
# gives the child a console with no window instead; 0 (no effect) elsewhere.
# The hub also allocates a hidden console of its own at startup (main), so
# even a spawn that bypasses _run shares that one. Both are needed: the hidden
# console covers third-party spawns, the flag covers a hub started without it.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(argv, **kw):
    """subprocess.run for a console program the hub only reads: never a window."""
    kw.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(argv, **kw)

BACKEND = get_backend()
# The tools module calls back into this module's task/launch primitives; the
# dashboard runs as __main__, so hand it the live module object.
ensemble_tools.bind(sys.modules[__name__])
attention.bind(sys.modules[__name__])
digest.bind(sys.modules[__name__])
rotation.bind(sys.modules[__name__])
# Capture each agent terminal's dying screen onto its task, before the reaper
# drops the buffer — that evidence is why a death is visible at all.
attention.install()

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


# --- Who may restart the hub -------------------------------------------------
# POST /api/update pulls the code and restarts the hub, ending every agent on
# the machine. The loopback exemption above would let any agent do that with
# one curl, so the route asks for its own proof, from this machine too:
#   * the bearer token of the PO agent in a room listed here (by default the
#     Ensemble Dashboard PO, the project where the hub itself is built), or
#   * the dashboard page: a key minted per hub process, kept in memory only and
#     handed to a browser as an HttpOnly cookie when it navigates to the page.
# Every agent runs as the same OS user as the hub, so an agent set on it can
# get either one (room tokens sit on disk under the state dir; the cookie goes
# to anything that mimics a browser opening the page). This stops accidental
# and casual restarts, not a determined agent. The env var exists so a test hub
# can name its own stand-in PO room; agents cannot change a running hub's env.
HUB_RESTART_ROOMS = frozenset(
    r.strip() for r in os.environ.get("ENSEMBLE_RESTART_ROOMS", "room-8d56cd21").split(",")
    if r.strip())
RESTART_REFUSED = (
    f"Only the Ensemble Dashboard PO ({', '.join(sorted(HUB_RESTART_ROOMS))}) may "
    "restart the hub, or the CEO with the Update now button in the dashboard. "
    "If you think the hub needs a restart, tell your PO why.")
_UI_KEY = secrets.token_urlsafe(32)


def may_restart_hub(room_id: str, identity: str) -> bool:
    """Whether an agent, as resolved from its bearer token, may restart the hub:
    only the PO agent of a room in HUB_RESTART_ROOMS (its ProductOwner, or its
    owner when it has none — the agent that reports into that room reach)."""
    if room_id not in HUB_RESTART_ROOMS or not identity:
        return False
    room = chatroom.get_room(room_id, public=False)
    return bool(room) and chatroom.po_identity(room) == identity


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
            out = _run([exe, "ip", "-4"], capture_output=True,
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
    # Nor an agent's own earlier conversations: a rotated agent's old rollout
    # is still the newest in its cwd until the fresh session writes one.
    taken = set()
    for pp in room.get("participants", []):
        taken.update(participant_session_ids(pp))
    found = {}
    for pp in pending:
        cwd = pp.get("cwd") or room.get("cwd") or ""
        rots = [r for r in pp.get("rotations") or [] if isinstance(r, dict)]
        p_since = max(since, float(rots[-1].get("startedAt") or 0)) if rots else since
        try:
            sid = cx.latest_session_id_for_cwd(cwd, since=p_since)
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


# The port this hub serves on, set by main(). A background job that launches an
# agent (the PO's rotation) wires it to this hub's MCP endpoint.
HUB_PORT = 0


def hub_launcher() -> "Handler":
    """A Handler with no request behind it, for background jobs that need the
    launch primitives (``_launch_room_agent_pty`` and friends). Those only ask
    the handler for the hub's own MCP address, so a stand-in server with the
    serving port is all it needs."""
    import types
    h = object.__new__(Handler)
    h.server = types.SimpleNamespace(server_address=("127.0.0.1", HUB_PORT))
    return h


def _pty_alive(pty_id) -> bool:
    sess = ptyrun.get(pty_id) if pty_id else None
    return bool(sess and sess.alive())


def _annotate_room_liveness(room: dict) -> dict:
    """Add a `live` flag: True if any agent PTY is running. Not live simply means
    the session isn't running — there's no separate 'ended' state. A reviewer
    that is started per request is flagged `onMention`."""
    room["live"] = _room_is_live(room)
    # What was sent while it was stopped and is still on its way in (a resume
    # in flight), or could not be delivered: the page shows those as pending.
    pending = pending_input(room.get("id", ""))
    if pending:
        room["pending"] = pending
    room["participants"] = [
        {**p, "onMention": True} if chatroom.is_on_mention(room, p) else p
        for p in room.get("participants", [])]
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
# Task folders whose chat uses the folder's own colour scheme instead of the
# dashboard theme. Off unless listed: the chat follows the global theme.
CHAT_SCHEME_FILE = DASHBOARD_DIR / "chat_scheme_override.json"
JIRA_LINKS_FILE = DASHBOARD_DIR / "jira_links.json"  # {sid: [tickets]} user-added links (override scan)
JIRA_UNLINKS_FILE = DASHBOARD_DIR / "jira_unlinks.json"  # {sid: [tickets]} user-removed from auto-scan
PARENTS_FILE = DASHBOARD_DIR / "parents.json"  # child_sid -> parent_sid
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


_TRANSCRIPT_PATHS: dict[str, Path] = {}


def find_transcript(session_id: str) -> Path | None:
    """A session's transcript. Remembered once found, so the task list does not
    glob every project folder for every live session on every refresh; checked
    for existence on each use, so a deleted transcript is looked up afresh."""
    hit = _TRANSCRIPT_PATHS.get(session_id)
    if hit is not None and hit.exists():
        return hit
    hits = list(PROJ_DIR.glob(f"*/{session_id}.jsonl"))
    if hits:
        _TRANSCRIPT_PATHS[session_id] = hits[0]
        return hits[0]
    _TRANSCRIPT_PATHS.pop(session_id, None)
    return None


_CODEX_ROLLOUT_PATHS: dict[str, tuple[float, list[Path]]] = {}
_CODEX_ROLLOUT_TTL_S = 30
_CODEX_COST_CACHE: dict[str, tuple[tuple, dict]] = {}


def _codex_rollouts(session_id: str) -> list[Path]:
    """A Codex session's rollout files, by the targeted filename glob only.

    This runs for every Codex conversation on every board poll, so it never
    falls back to reading each rollout's first line (the adapter's slow path),
    and a lookup is remembered for a few seconds."""
    now = time.time()
    hit = _CODEX_ROLLOUT_PATHS.get(session_id)
    if hit and now - hit[0] < _CODEX_ROLLOUT_TTL_S:
        return hit[1]
    ag = agents.get_agent("codex")
    root = ag.sessions_dir() if ag is not None and hasattr(ag, "sessions_dir") else None
    paths = sorted(root.glob(f"*/*/*/rollout-*-{session_id}.jsonl")) if root and root.exists() else []
    _CODEX_ROLLOUT_PATHS[session_id] = (now, paths)
    return paths


def compute_codex_session_cost(session_id: str) -> dict:
    """Token counts of one Codex conversation, from its rollouts' ``token_count``
    events, in compute_session_cost's shape. No prices: every model is
    ``unknownPricing`` and adds 0 dollars.

    Each event carries the request's own usage (``last_token_usage``) and the
    running total; an event whose total has not moved repeats the one before
    (a limits-only update) and is skipped. OpenAI counts cached input inside
    ``input_tokens``, so it is split out as cacheRead here. Reasoning tokens are
    already inside ``output_tokens``."""
    empty = {"dollars": 0.0, "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0},
             "byModel": {}}
    if not session_id:
        return empty
    files = _codex_rollouts(session_id)
    sig = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        sig.append((str(f), st.st_mtime_ns, st.st_size))
    sig = tuple(sig)
    if not sig:
        return empty
    cached = _CODEX_COST_CACHE.get(session_id)
    if cached and cached[0] == sig:
        return cached[1]
    by_model: dict[str, dict[str, int]] = {}
    for name, _, _ in sig:
        model = "codex"
        prev_total = None
        try:
            with open(name, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"turn_context"' not in line and '"token_count"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = rec.get("payload") if isinstance(rec, dict) else None
                    if not isinstance(payload, dict):
                        continue
                    if rec.get("type") == "turn_context":
                        model = payload.get("model") or model
                        continue
                    if payload.get("type") != "token_count":
                        continue
                    info = payload.get("info") or {}
                    total, last = info.get("total_token_usage"), info.get("last_token_usage")
                    if not isinstance(total, dict) or not isinstance(last, dict):
                        continue
                    if total == prev_total:
                        continue
                    prev_total = total
                    cached_in = int(last.get("cached_input_tokens") or 0)
                    bucket = by_model.setdefault(model, {
                        "input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0})
                    bucket["input"] += max(0, int(last.get("input_tokens") or 0) - cached_in)
                    bucket["output"] += int(last.get("output_tokens") or 0)
                    bucket["cacheWrite"] += int(last.get("cache_write_input_tokens") or 0)
                    bucket["cacheRead"] += cached_in
        except (OSError, ValueError, TypeError):
            continue
    tokens = {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0}
    for t in by_model.values():
        for k, v in t.items():
            tokens[k] += v
    result = {"dollars": 0.0, "tokens": tokens,
              "byModel": {m: {"dollars": 0.0, "tokens": t, "unknownPricing": True}
                          for m, t in by_model.items()}}
    _CODEX_COST_CACHE[session_id] = (sig, result)
    return result


def compute_room_cost(room: dict) -> dict:
    """Aggregate cost across a collaboration's agent members — sum each member's
    conversations and merge the per-model breakdowns — so a room shows the same
    Cost section a single-agent session does. Every conversation counts: each
    review, and each session a rotated owner or PO was handed out of. Claude
    conversations carry dollars and tokens from their transcripts; Codex ones
    carry tokens from their rollouts and no price. Same shape as
    compute_session_cost."""
    total = {
        "dollars": 0.0,
        "tokens": {"input": 0, "output": 0, "cacheWrite": 0, "cacheRead": 0},
        "byModel": {},
    }
    costs = []
    for pp in (room.get("participants") or []):
        if pp.get("kind") != "agent":
            continue
        # A reviewer on mention has one conversation per review.
        for sid in participant_session_ids(pp):
            if session_agent(pp, sid) == "codex":
                costs.append(compute_codex_session_cost(sid))
            else:
                costs.append(compute_session_cost(find_transcript(sid)))
    for c in costs:
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


def _user_turn(line: str):
    """(timestamp, normalised text) when a transcript line is a real user
    message, else None. The one definition of "a user turn", shared by the full
    reader and the incremental one so they can never disagree."""
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or d.get("type") != "user" or d.get("isMeta"):
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    text = _extract_text(msg.get("content"))
    if not text:
        return None
    text = text.strip()
    if not text or text.startswith("<") or text.startswith("Caveat:"):
        return None
    return d.get("timestamp", ""), " ".join(text.split())


def iter_user_turns(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                t = _user_turn(line)
                if t:
                    yield t
    except FileNotFoundError:
        return


# str(path) -> [bytes consumed, first, last, count, max_len, file identity]
_FLU_CACHE: dict[str, list] = {}


def _flu_take(st: list, raw: bytes, max_len: int) -> None:
    if not raw.strip():
        return
    t = _user_turn(raw.decode("utf-8", errors="replace"))
    if not t:
        return
    text = t[1]
    snippet = text[:max_len] + ("…" if len(text) > max_len else "")
    st[3] += 1
    if not st[1]:
        st[1] = snippet
    st[2] = snippet


def first_last_user(path: Path, max_len: int = 200):
    """First and last user message of a transcript, and how many there are.

    The task list asks this of every transcript on every refresh, and re-parsing
    each file from the start was most of its cost — worst exactly when agents
    are busy, because their transcripts are the big, growing ones. Transcripts
    only ever grow, so the lines already seen are remembered and only the bytes
    appended since the last call are parsed. A file that shrank or was replaced
    starts over; an unterminated last line is counted but not remembered, so it
    is read again once it is complete."""
    key = str(path)
    try:
        stt = path.stat()
    except OSError:
        return "", "", 0
    size, ident = stt.st_size, (stt.st_ino, stt.st_dev)
    st = _FLU_CACHE.get(key)
    if st is None or st[4] != max_len or st[5] != ident or size < st[0]:
        st = [0, "", "", 0, max_len, ident]
    tail = b""
    if size > st[0]:
        try:
            with path.open("rb") as fb:
                fb.seek(st[0])
                data = fb.read(size - st[0])
        except OSError:
            data = b""
        cut = data.rfind(b"\n")
        done, tail = (data[:cut + 1], data[cut + 1:]) if cut >= 0 else (b"", data)
        st = list(st)
        for raw in done.split(b"\n"):
            _flu_take(st, raw, max_len)
        st[0] += len(done)
    _FLU_CACHE[key] = st
    res = list(st)
    if tail.strip():
        _flu_take(res, tail, max_len)
    first, last, count = res[1], res[2], res[3]
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
    # How long an agent that owes a turn may print nothing before the dashboard
    # calls it stalled. Both CLIs repaint a working indicator every second while
    # they think, so real silence is the signal — but how much of it counts as
    # trouble is a judgement call, hence a setting.
    "attentionStallSeconds": attention.STALL_SECONDS_DEFAULT,
    # The PO's progress digest (digest.py): how often each project is checked,
    # in minutes (0 = off; a project's own project.json can override it), and
    # the cheap model that writes it up.
    "digestIntervalMin": digest.DEFAULT_INTERVAL_MIN,
    "digestModel": digest.DEFAULT_MODEL,
    # The PO's rotation (rotation.py): past this many tokens of context per
    # model call, the PO writes its handover and a fresh session takes over
    # from it. 0 = off.
    "poRotateTokens": rotation.DEFAULT_TOKENS,
    # The same for a running task's owner (its engineer, or its only agent),
    # from TASK-HANDOVER.md in the task folder. 0 = off.
    "taskRotateTokens": rotation.DEFAULT_TOKENS,
    # Compress noisy command output for hub-launched task owners/reviewers.
    # PO rooms, adopted sessions and ordinary user terminals are never wired.
    "rtkForTasks": True,
    # The dashboard theme, shared by every device on this hub. Empty = never
    # chosen here; the pages then hand up whatever their browser had.
    "theme": "",
    # The accent colour, shared the same way: a CSS colour, or "default" for
    # the theme's own accent. Empty = never chosen here (a page then hands up
    # its browser's), which is why the default is a word and not "".
    "accent": "",
}
# An accent is an opaque colour a primary button can be painted with: #rgb or
# #rrggbb, a CSS colour name, rgb(r g b) or hsl(h s% l%) (commas or spaces).
# No alpha: a see-through accent has no text colour that is sure to read on it.
_CSS_COLOUR_NAMES = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite
gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen
magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen
mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream
mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid
palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown
seagreen seashell sienna silver skyblue slateblue slategray slategrey snow springgreen
steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow yellowgreen
""".split())
_HEX_ACCENT_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")
_NUM = r"(\d{1,3}(?:\.\d+)?)"
_COLOUR_FN_RE = {
    sep: re.compile(rf"(rgb|hsl)\(\s*{_NUM}(%?)(deg)?{sep}{_NUM}(%?){sep}{_NUM}(%?)\s*\)", re.I)
    for sep in (r"\s*,\s*", r"\s+")
}


def valid_accent(v: str) -> bool:
    """True for "default" or an opaque colour the pages can paint (see above)."""
    if v == "default" or _HEX_ACCENT_RE.fullmatch(v) or v.lower() in _CSS_COLOUR_NAMES:
        return True
    for rx in _COLOUR_FN_RE.values():
        m = rx.fullmatch(v)
        if not m:
            continue
        fn, a, pa, deg, b, pb, c, pc = m.groups()
        a, b, c = float(a), float(b), float(c)
        if fn.lower() == "rgb":
            if deg or len({pa, pb, pc}) != 1:          # all percentages or none
                return False
            top = 100 if pa else 255
            return all(0 <= x <= top for x in (a, b, c))
        return (not pa and pb == pc == "%" and 0 <= a <= 360
                and 0 <= b <= 100 and 0 <= c <= 100)
    return False


_SETTINGS_ALLOWED_VALUES = {
    "openMode": {"window", "tab"},
    "theme": {"", "light", "dark", "dim", "paper", "contrast", "fjord", "system"},
    # defaultModel is free-form — anything claude --model accepts.
}


# One read-merge-write at a time: two partial PUTs at once (a page hands up its
# theme and its accent together) must both land. Reads take it too, so this
# process never holds the file open while a save replaces it.
_SETTINGS_LOCK = threading.RLock()


def load_settings() -> dict:
    """Merge saved settings over the defaults so a missing key doesn't crash
    the caller after we add new preferences later."""
    out = dict(_SETTINGS_DEFAULTS)
    try:
        with _SETTINGS_LOCK:
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
    with _SETTINGS_LOCK:
        return _save_settings_locked(settings)


def _save_settings_locked(settings: dict) -> dict:
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
        if k == "attentionStallSeconds":
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            v = max(60, min(24 * 3600, v))
        if k == "digestIntervalMin":
            v = digest.clamp_interval(v)
            if v is None:
                continue
        if k == "digestModel":
            if not isinstance(v, str) or not v.strip() or len(v) > 80:
                continue
            v = v.strip()
        if k in ("poRotateTokens", "taskRotateTokens"):
            v = rotation.clamp_tokens(v)
            if v is None:
                continue
        if k in ("backupEnabled", "rtkForTasks"):
            v = bool(v)
        if k == "accent":
            # Never back to "": that reads as never chosen, and the next page
            # would hand up its own browser's colour over the choice.
            if not isinstance(v, str) or not valid_accent(v.strip()):
                continue
            v = v.strip()
        current[k] = v
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    # A temp file of its own, and a few tries at the swap: on Windows another
    # process reading settings.json (a preflight hub) makes a replace fail.
    tmp = SETTINGS_FILE.with_name(f"{SETTINGS_FILE.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    try:
        for attempt in range(10):
            try:
                os.replace(tmp, SETTINGS_FILE)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
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
            out = _run(["git", "config", "user.name"],
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
            f"When a piece is committed, send the commit to {rev} for review before you consider it "
            f"done, and fold in the findings you get back. Go back to the product owner "
            f"only for requirements decisions or final sign-off."
        )
    if role == "reviewer":
        return (
            f"You are the reviewer. Do NOT design, plan, or implement — that is {eng}'s "
            f"job. Wait until {eng} sends a commit to review or a specific review "
            f"question; then review it: check it against the product owner's requirements, "
            f"hunt for bugs, edge cases, risks, and gaps, verify claims by reading the "
            f"actual code or running tests, and reply with a concise, structured critique "
            f"— what is correct, what is wrong, what is missing, and what to change. If "
            f"anyone asks you to build something, decline and redirect: {eng} builds, you "
            f"review. You are woken only when someone addresses you or @mentions you — "
            f"a message to the whole team goes to {eng}, who produces; weigh in once "
            f"there is a deliverable to review. Never start work on the task ahead of "
            f"{eng}."
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


OWNER_OUTPUT_NOTE = (
    "No progress narration; tool calls need no preamble. Reports: outcome, evidence "
    "and tests, files or commit, blocker or next decision. Do not repeat the spec.")

SOLO_REPORT_NOTE = (
    f"\n\n---\n{OWNER_OUTPUT_NOTE} When you finish this task, or get blocked and need help, report it "
    "with the ensemble_report tool (kind completed | blocked | question) — it "
    "reaches the project's PO, who otherwise cannot see your reply.")

# RTK is deliberately launch-scoped.  Never run ``rtk init -g`` here: that
# edits user-level Claude/Codex files and would also affect PO rooms and the
# operator's own terminals.  Claude's --settings file is an *additional*
# settings source, so the user's settings remain loaded alongside this hook.
RTK_BIN = DASHBOARD_DIR / "bin" / ("rtk.exe" if os.name == "nt" else "rtk")
RTK_DIR = DASHBOARD_DIR / "rtk"
RTK_CLAUDE_SETTINGS = RTK_DIR / "claude-task-settings.json"
USAGE_CLAUDE_SETTINGS = DASHBOARD_DIR / "usage" / "claude-agent-settings.json"
USAGE_STATUSLINE_SCRIPT = Path(__file__).resolve().parent / "usage_statusline.py"
RTK_TELEMETRY_ENV = "RTK_TELEMETRY_DISABLED"
RTK_RECALL_DB = RTK_DIR / "recall.db"
_RTK_SETTINGS_LOCK = threading.Lock()
RTK_BRIEF = {
    "claude": (
        "RTK is enabled for this task. Bash git/test/search commands are rewritten "
        "automatically; in PowerShell, prefix noisy git, test, search, listing, and "
        "log commands with `rtk`. If a recovery hint names hidden output you need, "
        "run `rtk recall <hash> --full`."
    ),
    "codex": (
        "RTK is available for this task's command output. Prefix noisy git, test, "
        "search, listing, and log commands with `rtk` (for example `rtk git status`, "
        "`rtk pytest`, `rtk grep`, or `rtk ls`). If a recovery hint names hidden "
        "output you need, run `rtk recall <hash> --full`."
    ),
}


def _rtk_task_room(room: dict) -> bool:
    """Whether ``room`` is a hub-created task covered by the RTK pilot."""
    if not load_settings().get("rtkForTasks", True) or room.get("adopted"):
        return False
    # create_task writes ``launched`` before the first launch.  Older tasks have
    # a task folder; ad-hoc/PO rooms and sessions adopted from history do not.
    if "launched" not in room and not room.get("taskDir"):
        return False
    rid = room.get("id", "")
    if any((p.get("poRoomId") or "") == rid for p in load_projects()):
        return False
    return RTK_BIN.is_file()


def _claude_status_line() -> dict:
    """The status-line command every hub-launched Claude agent runs.

    Claude Code hands it the plan windows (``rate_limits``) after each turn, and
    it keeps the newest in the file ``usage.read_claude_statusline`` reads — so
    Claude's allowance comes from Claude Code itself rather than from polling
    the rate-limited usage endpoint. It prints nothing.
    """
    command = (f'"{Path(sys.executable).as_posix()}" "{USAGE_STATUSLINE_SCRIPT.as_posix()}" '
               f'"{usage.CLAUDE_STATUSLINE_FILE.as_posix()}"')
    return {"type": "command", "command": command, "padding": 0}


def _write_settings_file(path: Path, settings: dict) -> Path:
    text = json.dumps(settings, indent=2) + "\n"
    with _RTK_SETTINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current != text:
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
    return path


def _rtk_claude_settings() -> Path:
    """Write the hub-owned, launch-only Claude settings file for RTK tasks: the
    RTK hook, plus the usage status line every hub-launched Claude gets.
    Claude takes one ``--settings`` source, so both live in this one file."""
    command = f'"{RTK_BIN.as_posix()}" hook claude'
    return _write_settings_file(RTK_CLAUDE_SETTINGS, {
        "hooks": {"PreToolUse": [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": command}],
        }]},
        "statusLine": _claude_status_line(),
    })


def _usage_claude_settings() -> Path:
    """The launch-only Claude settings file for agents outside the RTK pilot
    (POs, adopted sessions, tasks with RTK off): the usage status line only."""
    return _write_settings_file(USAGE_CLAUDE_SETTINGS, {"statusLine": _claude_status_line()})


def _rtk_task_wiring(room: dict, agent_key: str) -> tuple[list[str], dict, str]:
    """Return (agent argv, environment, brief) for a task launch.

    The environment and brief are RTK's, for covered tasks only. The argv is
    Claude's one ``--settings`` file, which every hub-launched Claude gets: it
    carries the usage status line, and the RTK hook when the task is covered.
    """
    if not _rtk_task_room(room):
        return (["--settings", str(_usage_claude_settings())]
                if agent_key == "claude" else []), {}, ""
    env = {
        RTK_TELEMETRY_ENV: "1",
        "RTK_RECALL_DB": str(RTK_RECALL_DB),
        "PATH": str(RTK_BIN.parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    args = ["--settings", str(_rtk_claude_settings())] if agent_key == "claude" else []
    return args, env, RTK_BRIEF.get(agent_key, RTK_BRIEF["codex"])

# The line typed into a task's owner when the task is started again. A resumed
# conversation comes back at an empty prompt and nothing else would wake it.
# It never repeats the spec: a session told its spec again redoes the work.
RESUME_NOTE = (
    "[resumed] Your task was started again. Your spec may have changed while you "
    "were stopped: read it again with ensemble_get_task, then carry on from where "
    f"you were; do not start over. {OWNER_OUTPUT_NOTE} Report with ensemble_report when you finish or "
    "are blocked.")
RESUME_NOTE_WAIT_S = 180    # a terminal that never settles gets the line anyway


class _Resume:
    """One resume of a room, in flight from the request that started it until
    its agents have had their first input. Holds what was sent to the room
    meanwhile (``queue``: [{text, to, at}]), delivered in order with the
    resume note as one input. A failed resume keeps its queue, with the
    reason, until it is retried or discarded."""

    def __init__(self):
        self.started = time.time()
        self.state = "resuming"        # resuming | failed
        self.error = ""
        self.queue: list[dict] = []
        self.resumed: list[dict] = []  # [{identity, ptyId}] once launched
        self.targets: list[tuple] = []

    def fail(self, why: str) -> None:
        self.state = "failed"
        self.error = why

    def view(self) -> dict:
        return {"state": self.state, "error": self.error, "since": self.started,
                "items": [{"text": it["text"], "to": it.get("to", ""), "at": it["at"]}
                          for it in self.queue]}


_RESUMES: dict[str, _Resume] = {}      # room id -> the resume in flight (or failed)
_RESUMES_LOCK = threading.Lock()


def pending_input(room_id: str) -> dict | None:
    """What a room's page shows as sent but not yet in: the messages a resume
    is holding (or could not deliver), or None."""
    with _RESUMES_LOCK:
        res = _RESUMES.get(room_id)
        return res.view() if res else None


def key_held(room_id: str, key: str) -> bool:
    """Whether a send with this key is held by a resume that FAILED after it
    was accepted. A key seen before is then not a duplicate — the same send
    again is that message's retry, and it stays one message. (Held by a
    resume still under way, it is a duplicate: already on its way in.)"""
    with _RESUMES_LOCK:
        res = _RESUMES.get(room_id)
        return (res is not None and res.state == "failed"
                and any(it.get("key") == key for it in res.queue))


def discard_pending(room_id: str) -> bool:
    """Drop what a failed resume was holding for a room."""
    with _RESUMES_LOCK:
        res = _RESUMES.get(room_id)
        if res is None or res.state != "failed":
            return False
        _RESUMES.pop(room_id, None)
        return True


def _type_input(sess, text: str) -> bool:
    """Type ``text`` into an agent's terminal and submit it as one input. A
    multi-line text goes as a bracketed paste (what the page does), so a TUI
    does not submit it line by line; Enter is a separate write (send_line).
    False when the terminal did not take it: a write was refused, or the
    process was gone right after (on Windows a write to an ended process
    is the only thing pywinpty reports; it returns 0 for what arrived)."""
    body = "\x1b[200~" + text + "\x1b[201~" if "\n" in text else text
    try:
        took = sess.send_line(body)
    except (OSError, EOFError):
        return False
    return took is not False and sess.alive()


_REF_ROOM_ID = re.compile(r"^room-[\w-]{1,64}$")


def _turn_epoch(stamp) -> float:
    """A transcript turn's ISO timestamp as epoch seconds, 0 when unreadable."""
    from datetime import datetime
    if isinstance(stamp, (int, float)):
        return float(stamp)
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def resolve_message_ref(room_id: str, msg_id: str) -> dict | None:
    """The message a balloon link points at: ``{roomId, id, from, who,
    taskTitle, ts, text, where, isPo}``, or None. A room's message is found by
    its id; a solo chat's balloon is a transcript turn, ``<sessionId>:<n>``,
    counted as the chat page counts it (repeats of the turn before dropped)."""
    room_id, msg_id = (room_id or "").strip(), (msg_id or "").strip()
    if not _REF_ROOM_ID.match(room_id) or not msg_id or msg_id == "task":
        return None
    room = chatroom.get_room(room_id)
    if room is None:
        return None
    is_po = any((p.get("poRoomId") or "") == room_id for p in load_projects())
    title = "PO" if is_po else (room.get("title") or room_id)
    out = {"roomId": room_id, "id": msg_id, "taskTitle": title, "isPo": is_po,
           **({"taskNo": room["no"]} if room.get("no") and not is_po else {})}
    who = lambda ident: operator_name() if ident == chatroom.HUMAN_IDENTITY else ident
    for m in room.get("messages") or []:
        if m.get("id") == msg_id:
            return {**out, "from": m.get("from", ""), "who": who(m.get("from", "")),
                    "ts": float(m.get("ts") or 0), "text": m.get("text") or "",
                    "where": f"~/.ensemble/rooms/{room_id}.json"}
    sid, sep, n = msg_id.rpartition(":")
    if not sep or not sid or not n.isdigit() or "/" in sid or "\\" in sid:
        return None
    # Only a solo chat shows transcript turns, and only its own agent's sessions
    # (the current one or one it was rotated from) are this room's: a link
    # naming another session gets nothing, not that session's text.
    agents_in = chatroom.agent_participants(room)
    if room.get("mode") != "solo" and len(agents_in) != 1:
        return None
    owner = next((p for p in agents_in if p.get("sessionId") == sid
                  or any(sid in (r.get("fromSessionId"), r.get("toSessionId")) for r in p.get("rotations") or [])),
                 None)
    if owner is None:
        return None
    raw = read_session_turns(sid)
    if not raw:
        return None
    turns = [t for i, t in enumerate(raw)
             if i == 0 or t.get("role") != raw[i - 1].get("role") or t.get("text") != raw[i - 1].get("text")]
    if int(n) >= len(turns):
        return None
    t = turns[int(n)]
    frm = chatroom.HUMAN_IDENTITY if t.get("role") == "user" else (owner.get("identity") or "agent")
    text = t.get("text") or ""
    if frm == chatroom.HUMAN_IDENTITY:
        text = message_refs.strip_message_refs(text)
    return {**out, "from": frm, "who": who(frm), "ts": _turn_epoch(t.get("timestamp")),
            "text": text, "where": f"the transcript of session {sid}"}


def _claude_text_turns(tpath: Path) -> list[dict]:
    """A Claude transcript's user and assistant text turns, unclassified."""
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
    return turns


def read_session_turns(sid: str) -> list[dict] | None:
    """A session's chat turns as /api/session/<sid>?full=1 serves them (Claude
    or Codex), or None when there is no such session."""
    tpath = find_transcript(sid)
    if tpath:
        return classify_turns(_claude_text_turns(tpath))
    cx = agents.get_agent("codex")
    if cx is None or cx.session_stat(sid) is None:
        return None
    return classify_turns(cx.read_turns(sid))


def with_message_refs(text: str, room_id: str = "") -> str:
    """A chat message as the agent receives it: its balloon links written out,
    and a line for each task it names by number (#18 in the project of the
    room it was sent in, ED-18 in any)."""
    return message_refs.expand_message_refs(text, resolve_message_ref, task_lookup_for(room_id))


def refs_expanded_for(room: dict, sender: str) -> bool:
    """Whose messages reach an agent with their balloon links written out: the
    person's and the room's ProductOwner's."""
    return sender == chatroom.HUMAN_IDENTITY or any(
        chatroom.is_product_owner_part(p) and p.get("identity") == sender
        for p in chatroom.agent_participants(room or {}))


class _NotTyped(Exception):
    """A message could not be typed into an agent that had looked ready."""


def _relay_wake(sender: str) -> str:
    """The doorbell line: what wakes an agent to read a new room message."""
    return (f"[relay] New message from '{sender}' in your shared room. "
            f"Use the chat_read tool to read it, then reply with chat_send "
            f"— to your partner, or to \"user\" if you need {operator_name()}'s "
            f"input.")


# What the hub types into an agent's terminal, told by how it starts. The
# transcript records it as the user's turn exactly like a line a person typed,
# so this prefix is the only thing that tells them apart. The chat page reads
# the kind to fold hub traffic and to say what each reply answers; it keeps no
# list of its own. A [word] not listed here is a person's (they type brackets).
HUB_INPUT_KINDS = (
    ("[digest] ", "digest"),            # digest.py: the PO's progress check
    ("[report] ", "report"),            # _ring_report: a task's ensemble_report
    ("[relay] ", "relay"),              # _relay_wake: a team room's doorbell
    ("[resumed] ", "resumed"),          # RESUME_NOTE
    ("[handover] ", "handover"),        # rotation.py: write your handover now
    ("[rotation] ", "rotation"),        # rotation.py: a fresh session's first prompt
    ("[from the restart helper, not ", "helper"),   # the note after a hub restart
)
# The kind is "completed", or a verdict such as "review 1 (changes requested)".
_REPORT_HEAD = re.compile(r"\[report\] (?P<reportKind>.+?) from task '(?P<taskTitle>.*?)' "
                          r"\((?P<taskId>[^\s,()]+), (?P<reporter>[^()]*?)\): ")


def hub_input_kind(text: str) -> dict:
    """``{"kind": ...}`` for a user turn: ``human`` or a HUB_INPUT_KINDS kind.
    A report also carries its ``reportKind``, ``taskTitle``, ``taskId`` and
    ``reporter`` when its header reads as _ring_report writes it."""
    s = (text or "").lstrip()
    for prefix, kind in HUB_INPUT_KINDS:
        if s.startswith(prefix):
            info = {"kind": kind}
            m = _REPORT_HEAD.match(s) if kind == "report" else None
            if m:
                info.update(m.groupdict())
            return info
    return {"kind": "human"}


def classify_turns(turns: list[dict]) -> list[dict]:
    """The chat's turns with ``kind`` on every user turn and ``answers`` (the
    kind of the user turn before it, with a report's details) on every
    assistant turn that follows one. A resume note typed together with the
    messages held for the resume (one input) is two turns: the note, then
    what the person sent."""
    out: list[dict] = []
    for t in turns:
        text = t.get("text") or ""
        if t.get("role") == "user" and text.startswith(RESUME_NOTE) and text[len(RESUME_NOTE):].strip():
            out.append({**t, "text": RESUME_NOTE})
            out.append({**t, "text": text[len(RESUME_NOTE):].strip()})
        else:
            out.append(dict(t))
    last = None
    for t in out:
        if t.get("role") == "user":
            t.update(hub_input_kind(t.get("text") or ""))
            last = {k: v for k, v in t.items() if k not in ("timestamp", "role", "text")}
        elif last is not None:
            t["answers"] = dict(last)
    return out


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
        ("The same MCP server offers the ensemble_* task tools appropriate to "
         "your role. Owners and reviewers get read/report tools and may move their "
         "own task to In review; project POs and planners also get board "
         "administration. The 'ensemble' skill explains the opt-ins and limits."),
        ("A message to everyone wakes only the task's owner (the engineer); to "
         "wake another specialist, address it or @mention it. Mention a reviewer "
         "only for a commit to review or a specific question, never for a plan, an "
         "acknowledgement, thanks, or a restatement of its verdict. When "
         "the task is finished, or the team is blocked and needs help, the owner "
         "reports it with ensemble_report (kind completed | blocked | question) — "
         "that reaches the project's PO."),
    ]
    if any(chatroom._role_head(t.get("role", "")) == chatroom.REVIEWER_ROLE
           for t in teammates):
        parts.append(
            "The reviewer is not kept running. Each time you address it or @mention "
            "it, the hub starts a fresh reviewer that reads the spec, your branch's "
            "diff, your message and the task's REVIEW-LOG.md — nothing else of this "
            "conversation. So make every review request self-contained: what to "
            "review, what changed since the last review, what you want checked. Its "
            "verdict comes back to you and to the PO, and is added to REVIEW-LOG.md. "
            "Do not address or mention a sleeping reviewer for a plan, acknowledgement, "
            "thanks, or to restate its verdict. Mention it again only with a new commit "
            "or evidence, an unresolved finding, or a specific new review question.")
    if role == "reviewer":
        eng = next((t["identity"] for t in teammates if t.get("role") == "engineer"), "the engineer")
        parts.append(f"Start by acknowledging your role in one line, then wait for "
                     f"{eng}'s first deliverable — do not begin working the task yourself.")
    else:
        parts.append(OWNER_OUTPUT_NOTE)
        non_reviewers = [t for t in teammates
                         if chatroom._role_head(t.get("role", "")) != chatroom.REVIEWER_ROLE]
        if non_reviewers:
            parts.append("Begin now by sending a non-reviewer teammate your initial plan or approach.")
        else:
            parts.append("Begin work now. Do not wake the reviewer until you have a commit "
                         "to review or a specific review question.")
    parts.append(f"TASK:\n{task}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Reviews on mention. A reviewer is not kept running in its room: every wake of
# a long-lived session re-sends its whole conversation (measured 2026-09-10: a
# message-triggered reviewer call averaged ~90k tokens early in a task and
# ~220k late), and a resume reloads the same history. So each request starts a
# fresh reviewer with a short brief, and the task's REVIEW-LOG.md carries what
# earlier reviews found. The reviewer ends its session with review_done.
# ---------------------------------------------------------------------------
REVIEW_LOG_NAME = "REVIEW-LOG.md"
REVIEW_VERDICTS = {"approve": "approved", "changes_requested": "changes requested",
                   "comment": "comments"}
_REVIEW_LAUNCH_LOCK = threading.Lock()
_REVIEW_LOG_LOCK = threading.Lock()
# A message said with a key (review comments carry one made from the comments
# they send) is posted once: the same key again, from another tab or a retry
# whose first answer was lost, is answered ok and posts nothing. Kept a day.
_SAY_KEYS: dict = {}
_SAY_KEYS_LOCK = threading.Lock()
_SAY_KEY_TTL = 24 * 3600


def _say_key_seen(rid: str, key: str) -> bool:
    now = time.time()
    for k in [k for k, at in _SAY_KEYS.items() if now - at > _SAY_KEY_TTL]:
        del _SAY_KEYS[k]
    return (rid, key) in _SAY_KEYS
# The brief is the agent's first prompt, passed on its command line, so it is
# kept well under Windows' 32k limit: a diff is inlined only when small, the
# log and the spec are cut to their newest / first part (the files hold all).
_BRIEF_DIFF_MAX = 6000
_BRIEF_LOG_MAX = 8000
_BRIEF_SPEC_MAX = 6000
_REVIEW_ALLOCATION_LIMIT = 20
# How long after review_done its session is ended — long enough for the tool's
# answer to reach the agent.
_REVIEW_END_DELAY = 5.0


def review_log_path(room: dict) -> Path:
    """The task's review log: REVIEW-LOG.md in its task folder (its working
    dir for a task without one)."""
    base = room.get("taskDir") or room.get("cwd") or ""
    if base:
        return Path(base) / REVIEW_LOG_NAME
    return DASHBOARD_DIR / "reviews" / f"{room.get('id', 'room')}-{REVIEW_LOG_NAME}"


def read_review_log(room: dict) -> str:
    try:
        return review_log_path(room).read_text(encoding="utf-8")
    except OSError:
        return ""


def review_count(log_text: str) -> int:
    return len(re.findall(r"^## Review \d+", log_text or "", re.M))


def append_review_log(room: dict, entry: str) -> Path:
    p = review_log_path(room)
    with _REVIEW_LOG_LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        head = "" if p.exists() else (
            f"# Review log — {room.get('title', '')}\n\n"
            "Every review of this task, oldest first. Each reviewer is a fresh "
            "session that reads this log before it starts.\n")
        with p.open("a", encoding="utf-8", newline="\n") as f:
            f.write(head + "\n" + entry.rstrip() + "\n")
    return p


def review_repo(room: dict) -> str:
    """The git checkout a review looks at: the owner's or the task's working dir
    when it is one, else the first checkout found inside them."""
    own = set(chatroom.owners(room))
    cands = [p.get("cwd", "") for p in chatroom.agent_participants(room)
             if p["identity"] in own]
    cands += [room.get("cwd", ""), room.get("taskDir", "")]
    for c in cands:
        if not c or not os.path.isdir(c):
            continue
        root = git_root(c)
        if root and path_is_git(root) and not _within(str(PROJECTS_ROOT), root):
            return root
        code, res = git_roots(c, 3)
        if code == 200 and res.get("roots"):
            return res["roots"][0]["path"]
    return ""


def review_git_context(root: str) -> dict:
    """Branch, base, commits and diff of the work under review."""
    if not root:
        return {}
    info = git_branch_base(root)
    mb = info.get("mergeBase") or ""
    against = mb or "HEAD"
    return {
        "root": root, **info, "against": against,
        "head": _git_out(root, "rev-parse", "--short", "HEAD"),
        "status": _git_out(root, "status", "--short")[:2000],
        "commits": _git_out(root, "log", "--oneline", f"{mb}..HEAD")[:2000] if mb else "",
        "stat": _git_out(root, "diff", "--stat", against)[:3000],
        "diff": _git_out(root, "diff", against, timeout=20),
    }


def _quote_block(text: str) -> str:
    return "\n".join("> " + ln for ln in (text or "").strip().splitlines()) or "> (empty)"


def review_brief(room: dict, part: dict, msg: dict, n: int, git: dict,
                 log_text: str) -> str:
    """The first prompt of a fresh reviewer: who asked what, the work to
    review, the task's spec and every earlier review, and how to finish."""
    ident = part["identity"]
    sender = msg.get("from", "") or "someone"
    if sender == chatroom.HUMAN_IDENTITY:
        who = f"{operator_name()}, the product owner (chat identity \"user\")"
    else:
        sp = chatroom.participant(room, sender) or {}
        role = chatroom._role_head(sp.get("role", "")) or "teammate"
        who = f"{sender}, the {role}"
    msgs = room.get("messages") or []
    idx = next((i for i, m in enumerate(msgs) if m.get("id") == msg.get("id")), len(msgs))
    context = "\n".join(
        f"- **{m.get('from', '')}** → {m.get('to') or 'everyone'}: "
        + " ".join((m.get("text") or "").split())[:500]
        for m in msgs[max(0, idx - 6):idx]) or "(none)"
    spec = (room.get("spec") or "").strip() or "(no written spec)"
    if len(spec) > _BRIEF_SPEC_MAX:
        spec = spec[:_BRIEF_SPEC_MAX] + "\n\n… (cut — read the full spec with ensemble_get_task)"
    log_path = review_log_path(room)
    if not log_text.strip():
        log = "(empty — this is the first review of this task)"
    elif len(log_text) > _BRIEF_LOG_MAX:
        log = (f"… (older entries cut — the whole log is {log_path})\n"
               + log_text[-_BRIEF_LOG_MAX:])
    else:
        log = log_text.strip()
    if git.get("root"):
        diff = git.get("diff", "")
        lines = [f"Checkout: `{git['root']}`",
                 f"Branch `{git.get('branch') or '?'}` at `{git.get('head') or '?'}`"
                 + (f", compared with `{git['base']}` from merge-base `{git['mergeBase'][:10]}` "
                    f"({git.get('ahead', 0)} commits ahead)" if git.get("base") else
                    " (on the main line — the change is what is uncommitted)"),
                 f"Diff command: `git -C \"{git['root']}\" diff {git['against']}` "
                 "(committed and uncommitted changes together)."]
        if git.get("commits"):
            lines.append("Commits:\n```\n" + git["commits"] + "\n```")
        if git.get("status"):
            lines.append("Uncommitted:\n```\n" + git["status"] + "\n```")
        lines.append("Diff stat:\n```\n" + (git.get("stat") or "(no changes)") + "\n```")
        if diff and len(diff) <= _BRIEF_DIFF_MAX:
            lines.append("Diff:\n````diff\n" + diff + "\n````")
        elif diff:
            lines.append(f"The diff is {len(diff):,} characters — read it with the "
                         "command above, file by file.")
        work = "\n\n".join(lines)
    else:
        work = ("No git checkout was found for this task. Review what the request "
                f"points at; the task's folder is `{room.get('taskDir') or room.get('cwd', '')}`.")
    verdicts = " | ".join(REVIEW_VERDICTS)
    no = task_label(room)
    brief = f"""You are '{ident}', the reviewer on the task {no + ' ' if no else ''}"{room.get('title', '')}" ({room.get('id', '')}). This is review {n} of this task.

You are a fresh session started for this ONE review. You remember nothing of earlier reviews: the review log below is what they found. When you have given your verdict with review_done, this session ends.

Do NOT design or implement — the engineer builds, you review. Check the work against the spec and the question, hunt for bugs, edge cases, risks and gaps, and verify claims by reading the actual code or running tests.

## What you were asked
From {who}:

{_quote_block(with_message_refs(msg.get('text', ''), room.get('id', '')) if refs_expanded_for(room, sender) else msg.get('text', ''))}

Recent conversation before it:
{context}

## The work to review
{work}

## Task specification
{spec}

## Review log ({log_path})
{log}

## How to finish
1. Where an earlier review raised findings, say for each whether it is now fixed, still open, or no longer relevant, naming the review it came from.
2. Call the review_done tool exactly once: verdict ({verdicts}), a one-line summary, and your findings as Markdown (what is right, what is wrong, what is missing, what to change — with file:line where you can). The hub appends it to the review log, sends it to {sender} and to the project's PO, and ends this session.
3. Do not also send the verdict with chat_send, and do not call ensemble_report — review_done does both jobs. If you cannot review (nothing to look at, the question is unclear), call review_done with verdict "comment" saying what you need.
"""
    # The brief travels in a PowerShell here-string, which a line starting
    # with '@ would end early.
    return re.sub(r"(?m)^'@", " '@", brief)


def finish_review(room_id: str, identity: str, verdict: str) -> dict | None:
    """Close the reviewer's current review on its record and end its session a
    moment later (after the tool's answer has reached it). Returns the review."""
    room = chatroom.get_room(room_id, public=False)
    part = chatroom.participant(room or {}, identity)
    if part is None:
        return None
    review = dict(part.get("review") or {})
    review.update(endedAt=time.time(), verdict=verdict,
                  sessionId=part.get("sessionId") or review.get("sessionId", ""))
    hist = {k: review.get(k) for k in ("n", "askedBy", "startedAt", "endedAt", "verdict",
                                         "sessionId", "branch", "head")}
    chatroom.patch_participant(room_id, identity, {"review": review},
                               append={"reviews": hist})
    pty_id = part.get("ptyId") or ""
    if pty_id:
        t = threading.Timer(_REVIEW_END_DELAY, ptyrun.kill, args=(pty_id,))
        t.daemon = True
        t.start()
    return review


def participant_session_ids(part: dict) -> list[str]:
    """Every conversation an agent has had on its task: its current one and,
    for a reviewer on mention, one per earlier review; for a rotated PO or task
    owner, each session it was rotated out of."""
    sids = [part.get("sessionId") or ""]
    sids += [r.get("sessionId") or "" for r in part.get("reviews") or []
             if isinstance(r, dict)]
    sids += [r.get("fromSessionId") or "" for r in part.get("rotations") or []
             if isinstance(r, dict)]
    out: list[str] = []
    for s in sids:
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


def session_agent(part: dict, sid: str) -> str:
    """The kind one of an agent's conversations was had as: an owner handed to
    the other kind at a rotation (or its reviewer, moved with it) keeps its
    earlier sessions' kinds in ``sessionKinds``."""
    return (part.get("sessionKinds") or {}).get(sid) or part.get("agent", "")


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
        out = _run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
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
        out = _run(["git", "-C", root, "status", "--porcelain"],
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
                      "createdAt": meta.get("createdAt", 0),
                      # The task that is this project's product owner: every
                      # other task reports into it (ensemble_report).
                      "poRoomId": (meta.get("poRoomId") or "").strip(),
                      # A documents project leads with its files and keeps
                      # their history; anything else is a code project.
                      "kind": "documents" if meta.get("kind") == "documents" else "code",
                      # Its short key (ED) and the number its next task gets.
                      "key": str(meta.get("key") or "").strip(),
                      "nextTaskNo": meta.get("nextTaskNo") if isinstance(meta.get("nextTaskNo"), int) else 0,
                      # Its own progress-digest interval, when it set one.
                      **({"digestIntervalMin": meta["digestIntervalMin"]}
                         if "digestIntervalMin" in meta else {})})
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
    # Its key now, so a project added later never changes this one's.
    proj["key"] = task_numbers.project_keys(projects)[proj["id"]]
    save_projects(projects)
    if _in_projects_root(norm):
        # Layout v2: the project's identity lives WITH its data, so a clone of
        # the projects root on a new machine is self-describing.
        try:
            (Path(norm) / "project.json").write_text(
                json.dumps({k: proj[k] for k in ("id", "name", "createdAt", "key")}, indent=2),
                encoding="utf-8")
        except OSError:
            pass
    return True, proj, "ok"


def set_project_po(project_id: str, room_id: str) -> tuple[bool, str]:
    """Name the task (room) that is a project's PO, or clear it with "".

    Stored as ``poRoomId`` in the project's own ``project.json`` in its home —
    the PO belongs to the project's data, so it travels with a backup or a
    clone of the projects root. Only that one key is touched: a documents
    project with a PO stays a documents project."""
    rid = (room_id or "").strip()
    if rid and chatroom.get_room(rid) is None:
        return False, "no_such_room"
    return _set_project_meta(project_id, "poRoomId", rid or None)


def set_project_digest_interval(project_id: str, minutes) -> tuple[bool, str]:
    """A project's own progress-digest interval in minutes (0 = off), or None
    to fall back to the hub default. Kept in its ``project.json`` next to the
    PO it concerns; the scheduler re-reads it every tick, so no restart."""
    if minutes is not None:
        minutes = digest.clamp_interval(minutes)
        if minutes is None:
            return False, "interval_must_be_minutes"
    return _set_project_meta(project_id, "digestIntervalMin", minutes)


PROJECT_KINDS = ("code", "documents")
# What the page says when a kind cannot be chosen, in plain words.
KIND_REFUSALS = {
    "files_outside_projects_root": "Only a project whose folder is in the projects folder can be a "
                                   "documents project; this one's files live elsewhere.",
    "project_is_a_git_repo": "This project's folder is a git repository, so it stays a code project.",
    "kind_must_be_code_or_documents": "A project is either a code project or a documents project.",
}


def set_project_kind(project_id: str, kind: str) -> tuple[bool, str]:
    """Make a project a documents project (its Overview leads with its files,
    its tasks work in its folder, the hub keeps every version of its files) or
    a code project again. Stored as ``kind`` in its project.json; a code
    project has no key. Either kind may have a PO, and switching keeps it."""
    k = (kind or "").strip().lower()
    if k not in PROJECT_KINDS:
        return False, "kind_must_be_code_or_documents"
    proj = find_project(project_id)
    if proj is None:
        return False, "no_such_project"
    if k == "code":
        return _set_project_meta(project_id, "kind", None)
    if not _in_projects_root(proj.get("path", "")):
        return False, "files_outside_projects_root"
    if proj.get("isGit"):
        return False, "project_is_a_git_repo"
    rid = (proj.get("poRoomId") or "").strip()
    if rid and chatroom.get_room(rid) is None:  # names a PO task that no longer exists
        _set_project_meta(project_id, "poRoomId", None)
    ok, msg = _set_project_meta(project_id, "kind", "documents")
    if ok:
        file_history.request(project_home(proj, create=False), None, "history started")
    return ok, msg


# ---- A documents project's file history (history.py) ----------------------

def _history_target(project_id: str) -> tuple[dict | None, str, tuple | None]:
    """(project, its folder, None), or (None, "", (status, error)) when the
    project has no file history to read: missing, not a documents project, or
    a folder the file APIs may not read."""
    proj = find_project(project_id)
    if proj is None:
        return None, "", (404, {"error": "no_such_project"})
    if proj.get("kind") != "documents":
        return None, "", (400, {"error": "not_a_documents_project"})
    home = project_home(proj, create=False)
    if not os.path.isdir(home) or not workspace_access_ok(home):
        return None, "", (403, {"error": "path_not_allowed"})
    return proj, home, None


def _room_project_id(room_id: str, room: dict | None, links: dict | None = None) -> str:
    links = load_session_projects() if links is None else links
    return links.get(room_id) or (room or {}).get("projectId") or ""


def _agent_pids() -> set[int]:
    """The processes of the agents the hub runs (their PTYs): a request whose
    sender descends from one is an agent's, whatever headers it sends."""
    return {i["pid"] for i in ptyrun.list_sessions() if i.get("alive") and isinstance(i.get("pid"), int)}


def _history_running(project_id: str, active_within: float | None = None) -> list[dict]:
    """The tasks of a project with an agent running now, which a snapshot
    of changes nobody announced is credited to. With ``active_within``, only
    those whose agents printed something within that many seconds: an agent
    sitting idle since the last snapshot did not make its changes."""
    idle: dict[str, float] = {}
    for i in ptyrun.list_sessions():
        rid = (i.get("meta") or {}).get("room")
        if rid and i.get("alive"):
            s = i.get("idleSeconds")
            idle[rid] = min(idle.get(rid, 1e9), 1e9 if s is None else s)
    out, links = [], load_session_projects()
    for rid in sorted(idle):
        if active_within is not None and idle[rid] > active_within:
            continue
        room = chatroom.get_room(rid)
        if room and _room_project_id(rid, room, links) == project_id:
            out.append({"id": rid, "title": room.get("title", "")})
    return out


_HISTORY_BUSY: dict[str, bool] = {}     # room id -> seen producing output since its last turn end


def _history_turn_ends() -> list[tuple[str, list]]:
    """[(folder, [task])] for tasks of documents projects whose agents just
    went quiet after working: the end of a turn. Sampled on the history's
    tick from the PTYs' idle time; a turn shorter than a tick may be missed,
    and the scan picks its changes up."""
    idle: dict[str, float] = {}
    for info in ptyrun.list_sessions():
        rid = (info.get("meta") or {}).get("room")
        if rid and info.get("alive"):
            s = info.get("idleSeconds")
            idle[rid] = min(idle.get(rid, 1e9), 1e9 if s is None else s)
    ended = []
    for rid, s in idle.items():
        if s < 3:
            _HISTORY_BUSY[rid] = True
        elif s >= 8 and _HISTORY_BUSY.pop(rid, False):
            ended.append(rid)
    for rid in [r for r in _HISTORY_BUSY if r not in idle]:
        if _HISTORY_BUSY.pop(rid):
            ended.append(rid)
    if not ended:
        return []
    docs = {p["id"]: p for p in load_projects() if p.get("kind") == "documents"}
    links, out = load_session_projects(), []
    for rid in ended:
        room = chatroom.get_room(rid)
        pid = _room_project_id(rid, room, links)
        if room and pid in docs:
            out.append((project_home(docs[pid], create=False), [{"id": rid, "title": room.get("title", "")}]))
    return out


def _history_nudge(room_id: str, reason: str) -> None:
    """A task said something (a chat message ends its turn, or a report):
    if it works in a documents project, ask for a snapshot credited to it.
    Only queues it; the history's thread takes the snapshot."""
    try:
        room = chatroom.get_room(room_id)
        proj = find_project(_room_project_id(room_id, room)) if room else None
        if proj and proj.get("kind") == "documents":
            file_history.request(project_home(proj, create=False),
                                 [{"id": room_id, "title": room.get("title", "")}], reason)
    except Exception:
        pass


def _history_homes() -> dict:
    return {p["id"]: project_home(p, create=False) for p in load_projects() if p.get("kind") == "documents"}


# ---- A documents project's files from the page: upload, folders, move, delete ----
#
# Every path is relative to the project folder and never reaches the hub's own
# records there (.history, _linked, project.json, a task's folder). Each change
# is made under the history's lock between two snapshots: the first keeps what
# the folder held (so a deleted or replaced file can be put back even if no
# scan saw it), the second records the change under the person's name.

class FileOpRefused(Exception):
    """A file operation that did not happen: its status, error code and a
    plain sentence for the person. ``extra`` joins the reply (a snapshot)."""

    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}

    def payload(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


_FILE_NAME_BAD = re.compile(r'[<>:"|?*\\\x00-\x1f]')
_FILE_NAME_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                       *(f"lpt{i}" for i in range(1, 10))}
_FILES_PATH_MAX = 1024
_UPLOAD_CHUNK = 1024 * 1024
_UPLOAD_DRAIN_MAX = 4 * file_history.MAX_FILE_BYTES   # a refused body past this is not read at all


def _files_user() -> dict:
    """Who a change made from the page is credited to: the person signed in
    to this computer."""
    try:
        name = getpass.getuser()
    except Exception:
        name = ""
    return {"kind": "user", "name": name or "you"}


def files_target(project_id) -> tuple[dict, str]:
    """(project, its folder) for a file operation, else FileOpRefused."""
    proj = find_project(project_id.strip()) if isinstance(project_id, str) and project_id.strip() else None
    if proj is None:
        raise FileOpRefused(404, "no_such_project", "That project is not on the board.")
    if proj.get("kind") != "documents":
        raise FileOpRefused(400, "documents_only",
                            "Files can be added, moved and deleted here only in a documents project.")
    home = project_home(proj, create=False)
    if not os.path.isdir(home) or not workspace_access_ok(home):
        raise FileOpRefused(403, "path_not_allowed", "The project's folder cannot be reached.")
    return proj, home


def _files_off_limits(home: str, parts: list[str], shown: str) -> None:
    low = [p.lower() for p in parts]
    ours = "That is where Ensemble keeps its own records, so files cannot be put there, moved or deleted."
    # .history anywhere: the tree hides any folder of that name holding a HEAD.
    if low[0] == "_linked" or file_history.DIR_NAME in low or (len(low) == 1 and low[0] in ("project.json", "project.json.tmp")):
        raise FileOpRefused(400, "path_not_allowed", f"“{shown}”: {ours}")
    if low[-1] == "task.json":
        raise FileOpRefused(400, "path_not_allowed", f"“{shown}”: a file named task.json would make its folder a task's folder.")
    for part in parts:
        # Compared exactly as the tree compares it: "Node_Modules" is listed there, so it stays usable.
        if part in workspace_search.SKIP_DIRS:
            raise FileOpRefused(400, "path_not_allowed",
                                f"“{shown}”: the Files panel never shows a folder named “{part}”, so a file "
                                "there could not be seen. Rename it and try again.")
        if file_history.not_kept(part):
            raise FileOpRefused(400, "path_not_allowed",
                                f"“{shown}”: the file history does not keep “{part}” (temporary and system "
                                "files), so it could not be put back. Rename it and try again.")
    cur = home
    isjunction = getattr(os.path, "isjunction", lambda p: False)
    for part in parts:
        cur = os.path.join(cur, part)
        # The tree never enters a linked folder: a file reached through one could not be seen there.
        if (os.path.islink(cur) or isjunction(cur)) and os.path.isdir(cur):
            raise FileOpRefused(400, "path_not_allowed",
                                f"“{shown}”: “{part}” is a link to another folder, which the Files panel "
                                "does not show. Use the folder it points to.")
        if os.path.isfile(os.path.join(cur, "task.json")):
            raise FileOpRefused(400, "path_not_allowed",
                                f"“{shown}” is in a task's folder, which its task looks after. {ours}")


def files_path(home: str, path) -> tuple[str, str]:
    """(clean relative path, absolute path) for a path the page sent: relative
    to ``home``, ``/``-separated, a name Windows and macOS both accept, and
    outside what the hub keeps there. Checked as written and again as it
    resolves on disk (a short 8.3 name, a link). Else FileOpRefused."""
    def refuse(msg: str):
        raise FileOpRefused(400, "path_not_allowed", msg)
    if not isinstance(path, str) or not path.strip():
        refuse("No file or folder name was given.")
    if len(path) > _FILES_PATH_MAX or path.startswith("/") or "\\" in path:
        refuse(f"“{path[:200]}” is not a path inside the project folder.")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            refuse(f"“{path}” is not a path inside the project folder.")
        if _FILE_NAME_BAD.search(part) or part[-1] in ". " or part.split(".")[0].rstrip().lower() in _FILE_NAME_RESERVED:
            refuse(f"“{part}” cannot be used as a file or folder name.")
    _files_off_limits(home, parts, path)
    full = os.path.join(home, *parts)
    try:
        real_home = os.path.realpath(home)
        rel_real = os.path.relpath(os.path.realpath(full), real_home).replace("\\", "/")
    except (OSError, ValueError):
        rel_real = ".."
    rparts = rel_real.split("/")
    if rel_real == "." or rparts[0] == "..":
        refuse(f"“{path}” leads outside the project folder.")
    _files_off_limits(real_home, rparts, path)
    return "/".join(parts), full


def _files_os_error(e: OSError, rel: str, extra: dict | None = None) -> FileOpRefused:
    if isinstance(e, PermissionError):
        return FileOpRefused(409, "in_use", f"“{rel}” is open in another program or cannot be changed. "
                                            "Close it and try again.", extra)
    return FileOpRefused(500, "failed", f"“{rel}” could not be changed: {e.strerror or e}.", extra)


def _files_parent_dirs(home: str, rel: str) -> None:
    """Every folder above ``rel`` that is there must be a folder."""
    cur = home
    for part in rel.split("/")[:-1]:
        cur = os.path.join(cur, part)
        if os.path.lexists(cur) and not os.path.isdir(cur):
            raise FileOpRefused(409, "not_a_folder", f"“{rel}”: “{part}” is a file, not a folder.")


def _files_snapshot(proj: dict, home: str, reason: str, before: bool = False) -> dict:
    """A snapshot around a change from the page. ``before`` keeps what the
    folder held, credited like the scan; after, the change is the person's.
    Never raises: a failure is returned and logged."""
    try:
        if before:
            res = file_history.snapshot(home, file_history.credit(_history_running, proj["id"], home),
                                        reason=f"before {reason}")
        else:
            res = file_history.snapshot(home, _files_user(), reason=reason)
    except Exception as e:
        res = {"ok": False, "committed": False, "rev": "", "files": 0, "skipped": [], "msg": f"error: {str(e)[:200]}"}
    if not res.get("ok"):
        print(f"[files] {'before ' if before else ''}{reason} in {home}: the history could not record it: "
              f"{res.get('msg')}", flush=True)
    return res


def _files_remove(full: str) -> None:
    """Delete a file or a whole folder, read-only files too."""
    def writable(func, p, _exc):
        os.chmod(p, os.stat(p).st_mode | 0o200)
        func(p)
    if os.path.isdir(full) and not os.path.islink(full):
        if sys.version_info >= (3, 12):
            shutil.rmtree(full, onexc=writable)
        else:
            shutil.rmtree(full, onerror=writable)
    else:
        try:
            os.remove(full)
        except PermissionError:
            os.chmod(full, os.stat(full).st_mode | 0o200)
            os.remove(full)


def _files_exists_refusal(rel: str, full: str) -> FileOpRefused:
    what = "A folder" if os.path.isdir(full) else "A file"
    return FileOpRefused(409, "exists", f"{what} named “{rel.rsplit('/', 1)[-1]}” is already there.")


def files_upload_check(home: str, rel: str, full: str, overwrite: bool) -> None:
    """Before the body is read: may a file land at ``rel``?"""
    _files_parent_dirs(home, rel)
    if os.path.lexists(full) and (not overwrite or os.path.isdir(full)):
        raise _files_exists_refusal(rel, full)


def files_upload_temp(home: str, rel: str, full: str) -> str:
    """The temp file an upload is written to: in the file's own folder (made
    now), hidden, and ending .tmp so neither the tree nor the history takes it."""
    parent = os.path.dirname(full)
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(full)[:80]}.", suffix=".upload.tmp", dir=parent)
        os.close(fd)
        if os.name != "nt":
            os.chmod(tmp, 0o644)
    except OSError as e:
        raise _files_os_error(e, rel)
    return tmp


def files_upload_commit(proj: dict, home: str, rel: str, full: str, tmp: str, overwrite: bool) -> dict:
    """Put a fully written temp file in place and record it. Everything is
    checked again under the lock: the folder may have changed while the file
    arrived (a task folder made, a link put in its way)."""
    with file_history.lock(home):
        if files_path(home, rel) != (rel, full):
            raise FileOpRefused(400, "path_not_allowed", f"“{rel}” is not a path inside the project folder.")
        files_upload_check(home, rel, full, overwrite)
        _files_snapshot(proj, home, "upload", before=True)
        try:
            size = os.path.getsize(tmp)
            os.replace(tmp, full)
        except OSError as e:
            raise _files_os_error(e, rel)
        return {"path": rel, "size": size, "snapshot": _files_snapshot(proj, home, "upload")}


# mkdir, move and delete check their paths under the lock, right before the change.

def files_mkdir(proj: dict, home: str, path) -> dict:
    with file_history.lock(home):
        rel, full = files_path(home, path)
        _files_parent_dirs(home, rel)
        _files_snapshot(proj, home, "mkdir", before=True)
        if os.path.isdir(full):
            created = False
        elif os.path.lexists(full):
            raise _files_exists_refusal(rel, full)
        else:
            try:
                os.makedirs(full)
            except OSError as e:
                raise _files_os_error(e, rel)
            created = True
        return {"path": rel, "created": created, "snapshot": _files_snapshot(proj, home, "mkdir")}


def files_move(proj: dict, home: str, src, dst, overwrite: bool = False) -> dict:
    with file_history.lock(home):
        frel, ffull = files_path(home, src)
        trel, tfull = files_path(home, dst)
        if not os.path.lexists(ffull):
            raise FileOpRefused(404, "not_found", f"“{frel}” is not there any more.")
        is_dir = os.path.isdir(ffull) and not os.path.islink(ffull)
        fl, tl = frel.lower().split("/"), trel.lower().split("/")
        try:
            inside = os.path.normcase(os.path.realpath(tfull)).startswith(os.path.normcase(os.path.realpath(ffull)) + os.sep)
        except OSError:
            inside = False
        if is_dir and (inside or (len(tl) > len(fl) and tl[:len(fl)] == fl)):
            raise FileOpRefused(400, "into_itself", f"The folder “{frel}” cannot be moved into itself.")
        _files_parent_dirs(home, trel)
        try:
            same = os.path.lexists(tfull) and os.path.samefile(ffull, tfull)    # the same name in another case
        except OSError:
            same = False
        if frel == trel:
            _files_snapshot(proj, home, "move", before=True)     # nothing moves: pending work stays its own
            return {"from": frel, "to": trel, "snapshot": _files_snapshot(proj, home, "move")}
        replace = os.path.lexists(tfull) and not same
        if replace:
            if not overwrite:
                raise _files_exists_refusal(trel, tfull)
            if fl[:len(tl)] == tl:
                raise FileOpRefused(409, "exists", f"“{frel}” cannot replace the folder “{trel}” it is in.")
        _files_snapshot(proj, home, "move", before=True)
        try:
            os.makedirs(os.path.dirname(tfull), exist_ok=True)
            if replace and (is_dir or os.path.isdir(tfull)):
                _files_remove(tfull)
            os.replace(ffull, tfull)
        except OSError as e:
            raise _files_os_error(e, frel, {"snapshot": _files_snapshot(proj, home, "move")})
        return {"from": frel, "to": trel, "snapshot": _files_snapshot(proj, home, "move")}


def files_delete(proj: dict, home: str, path) -> dict:
    with file_history.lock(home):
        rel, full = files_path(home, path)
        if not os.path.lexists(full):
            raise FileOpRefused(404, "not_found", f"“{rel}” is not there any more.")
        _files_snapshot(proj, home, "delete", before=True)
        try:
            _files_remove(full)
        except OSError as e:
            # Part of a folder may be gone: what is gone is recorded all the same.
            raise _files_os_error(e, rel, {"snapshot": _files_snapshot(proj, home, "delete")})
        return {"path": rel, "snapshot": _files_snapshot(proj, home, "delete")}


_PROJECT_META_LOCK = threading.RLock()


def _set_project_meta(project_id: str, key: str, value) -> tuple[bool, str]:
    """Set (or, with None, remove) one key of a project's own ``project.json``
    in its home. Only that one key is touched."""
    proj = find_project(project_id)
    if proj is None:
        return False, "no_such_project"
    home = project_home(proj, create=True)
    pj = Path(home) / "project.json"
    with _PROJECT_META_LOCK:
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if not isinstance(meta, dict):
            meta = {k: proj.get(k) for k in ("id", "name", "createdAt")}
        if meta.get("id") != proj["id"]:
            return False, "project_json_belongs_to_another_project"
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
        try:
            tmp = pj.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            tmp.replace(pj)
        except OSError as e:
            return False, f"cannot write {pj}: {e}"
    return True, "ok"


# ---- Task numbers (#18) and project keys (ED) --------------------------------
# A task gets the next number of its project when it is created or moved in
# (assign_task_number); tasks from before numbers existed get theirs when the
# hub starts (backfill_task_numbers). The counter is ``nextTaskNo`` in the
# project's project.json; the task's number is ``no`` in its room (and its
# task.json), with ``noProjectId`` saying which project it counts in.

_NUMBERS_LOCK = threading.RLock()
KEY_REFUSALS = {
    "bad_key": "A key is a letter followed by up to five letters or digits, such as ED or OT2.",
    "key_taken": "Another project already has that key.",
}


def project_keys(projects: list[dict] | None = None) -> dict[str, str]:
    """{project id: key}: the stored key, else one derived from the name."""
    return task_numbers.project_keys(load_projects() if projects is None else projects)


def set_project_key(project_id: str, key: str) -> tuple[bool, str]:
    """A project's key, as the person typed it on the project's settings."""
    k = task_numbers.normalize_key(key)
    if not k:
        return False, "bad_key"
    projects = load_projects()
    if not any(p["id"] == project_id for p in projects):
        return False, "no_such_project"
    if any(pid != project_id and v == k for pid, v in project_keys(projects).items()):
        return False, "key_taken"
    return _set_project_meta(project_id, "key", k)


def _task_project(room: dict, links: dict, projects: list[dict]) -> str:
    """The project a task belongs to: its recorded link, else its own record,
    else the project whose folder holds it."""
    return (links.get(room.get("id", "")) or room.get("projectId")
            or _project_for_cwd(room.get("cwd", ""), projects) or "")


def _po_room_ids(projects: list[dict]) -> set[str]:
    return {(p.get("poRoomId") or "").strip() for p in projects if (p.get("poRoomId") or "").strip()}


def assign_task_number(rid: str, project_id: str, room_full: dict | None = None) -> int | None:
    """Give a task the next number of ``project_id`` (a new task, or one moved
    in), unless it has one there already or is a project's PO. ``room_full``,
    a copy of the room the caller will write back, gets the number too.
    Returns the number, or None."""
    pid = (project_id or "").strip()
    if not rid or not pid:
        return None
    with _NUMBERS_LOCK:
        projects = load_projects()
        proj = next((p for p in projects if p["id"] == pid), None)
        room = chatroom.get_room(rid)
        if proj is None or room is None or rid in _po_room_ids(projects):
            return None
        if room.get("no") and room.get("noProjectId") == pid:
            n = room["no"]
        else:
            # The counter, and never a number a task of the project has.
            n = max(proj.get("nextTaskNo") or 0,
                    1 + max((r.get("no") or 0 for r in _task_index() if r.get("noProjectId") == pid), default=0))
            ok, _msg = _set_project_meta(pid, "nextTaskNo", n + 1)
            if not ok:
                return None
            room = chatroom.set_task_number(rid, pid, n) or room
            _patch_task_json(room.get("taskDir", ""), no=n, previousNos=room.get("previousNos") or [])
        if room_full is not None:
            for k in chatroom.NUMBER_FIELDS:
                if k in room:
                    room_full[k] = room[k]
        return n


def number_adopted_room(room_full: dict) -> int | None:
    """A past session brought in as a task (/api/session/adopt) gets the next
    number of the project its folder is in, like any new task."""
    projects = load_projects()
    return assign_task_number(room_full.get("id", ""),
                              _task_project(room_full, load_session_projects(), projects), room_full)


def backfill_task_numbers() -> dict:
    """Numbers for the tasks that have none, and a key for every project —
    run when the hub starts. Per project, oldest task first; a title a person
    numbered by hand keeps its number when no other task has it (see
    task_numbers.plan_numbers). A second run changes nothing. Returns
    ``{numbered, keys}``."""
    numbered, keyed = 0, 0
    with _NUMBERS_LOCK:
        projects = load_projects()
        keys = project_keys(projects)
        for p in projects:
            if (p.get("key") or "") != keys[p["id"]] and _set_project_meta(p["id"], "key", keys[p["id"]])[0]:
                keyed += 1
        links, labels = load_session_projects(), load_labels()
        po_rooms = _po_room_ids(projects)
        ids = {p["id"] for p in projects}
        by_project: dict[str, list[dict]] = {}
        for room in chatroom.list_rooms():
            pid = _task_project(room, links, projects)
            if pid in ids and room["id"] not in po_rooms:
                by_project.setdefault(pid, []).append(room)
        for p in projects:
            rooms = by_project.get(p["id"], [])
            tasks = [{"id": r["id"], "title": labels.get(r["id"]) or r.get("title", ""),
                      "createdAt": r.get("createdAt") or 0,
                      "no": r.get("no") if r.get("no") and r.get("noProjectId", p["id"]) == p["id"] else None}
                     for r in rooms]
            plan, nxt = task_numbers.plan_numbers(tasks, p.get("nextTaskNo") or 0)
            for r in rooms:
                n = plan.get(r["id"])
                if n is None and r.get("noProjectId"):
                    continue
                if n is None:                    # numbered here, but never said where
                    n = r["no"]
                done = chatroom.set_task_number(r["id"], p["id"], n)
                if done and r["id"] in plan:
                    numbered += 1
                    _patch_task_json(done.get("taskDir", ""), no=n, previousNos=done.get("previousNos") or [])
            if nxt != (p.get("nextTaskNo") or 0):
                _set_project_meta(p["id"], "nextTaskNo", nxt)
    return {"numbered": numbered, "keys": keyed}


_TASK_INDEX: dict[str, tuple[tuple, dict]] = {}
_TASK_INDEX_LOCK = threading.Lock()


def _task_index() -> list[dict]:
    """Every room's numbering and the little a reference shows of it, reading
    again only the room files that changed since the last call."""
    out: list[dict] = []
    try:
        paths = list(chatroom.ROOMS_DIR.glob("room-*.json"))
    except OSError:
        return out
    seen = set()
    for p in paths:
        rid = p.stem
        seen.add(rid)
        try:
            st = p.stat()
            mtime = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
        with _TASK_INDEX_LOCK:
            hit = _TASK_INDEX.get(rid)
        if hit and hit[0] == mtime:
            out.append(hit[1])
            continue
        room = chatroom.get_room(rid)
        if room is None:
            continue
        rep = room.get("lastReport") if isinstance(room.get("lastReport"), dict) else {}
        entry = {"id": rid, "no": room.get("no"), "noProjectId": room.get("noProjectId") or "",
                 "previousNos": room.get("previousNos") or [], "title": room.get("title", ""),
                 "projectId": room.get("projectId") or "", "cwd": room.get("cwd") or "",
                 "launched": room.get("launched", True), "status": room.get("status", ""),
                 "workflow": room.get("workflow"), "createdAt": room.get("createdAt"),
                 "branch": ((room.get("workspace") or {}).get("branch") or ""),
                 "participants": [{k: pp.get(k) for k in ("identity", "kind", "agent", "role", "ptyId", "pid")}
                                  for pp in room.get("participants") or []],
                 "report": {"kind": rep.get("kind", ""), "text": (rep.get("text") or "")[:1000]} if rep else None}
        with _TASK_INDEX_LOCK:
            _TASK_INDEX[rid] = (mtime, entry)
        out.append(entry)
    with _TASK_INDEX_LOCK:
        for rid in [r for r in _TASK_INDEX if r not in seen]:
            _TASK_INDEX.pop(rid, None)
    return out


def task_label(room: dict | None) -> str:
    """#18 for a numbered task, else ""."""
    return task_numbers.label((room or {}).get("no"))


def resolve_task_ref(ref, project_id: str = "", any_project: bool = False) -> tuple[str, str]:
    """(room id, "") for a task address — room-…, #18 or 18 in ``project_id``,
    ED-18 anywhere — else ("", a sentence saying why)."""
    projects = load_projects()
    return task_numbers.find_task(_task_index(), ref, (project_id or "").strip(), project_keys(projects),
                                  {p["id"]: p.get("name", "") for p in projects}, any_project=any_project)


def task_ref_info(rid: str, projects: list[dict] | None = None) -> dict | None:
    """What a reference to a task shows: ``{roomId, no, label, ref, key,
    projectId, project, title, status, workflow, workflowName, live, agents,
    branch, report}`` (``ref`` is the full form, ED-18)."""
    entry = next((e for e in _task_index() if e["id"] == rid), None)
    if entry is None:
        return None
    projects = load_projects() if projects is None else projects
    keys = project_keys(projects)
    pid = entry.get("noProjectId") or ""
    live = _room_is_live(entry)
    if not entry.get("launched", True):
        status = "draft"
    elif live:
        status = {"waiting_human": "waiting for you", "paused": "paused"}.get(entry.get("status"), "running")
    else:
        status = "not running"
    wf = workflow_of(entry)
    labels = load_labels()
    return {"roomId": rid, "no": entry.get("no"), "label": task_label(entry),
            "ref": task_numbers.label(entry.get("no"), keys.get(pid, "")) if pid else task_label(entry),
            "key": keys.get(pid, ""), "projectId": pid,
            "project": next((p.get("name", "") for p in projects if p["id"] == pid), ""),
            "title": labels.get(rid) or entry.get("title", ""), "status": status, "live": live,
            "workflow": wf, "workflowName": WORKFLOW_LABELS.get(wf, wf),
            "agents": [{"identity": pp.get("identity", ""), "agent": pp.get("agent", ""), "role": pp.get("role", "")}
                       for pp in entry["participants"] if pp.get("kind") == "agent"],
            "branch": entry.get("branch", ""), "report": entry.get("report")}


def http_task_id(value, project_id: str = "") -> tuple[str, dict | None]:
    """A task named in a request: a room id as it is, or a number (#18 or 18
    in ``project_id``, ED-18 anywhere, or a number only one project has)
    resolved to its room id. Returns (id, None), or ("", the 404 body).
    Anything that is not a number is returned as it is, as before numbers."""
    v = str(value if value is not None else "").strip()
    if "no" not in (task_numbers.parse_ref(v) or {}):
        return v, None
    rid, why = resolve_task_ref(v, project_id, any_project=not (project_id or "").strip())
    return (rid, None) if rid else ("", {"error": "no_such_task", "message": why})


def task_lookup_for(room_id: str = "", project_id: str = ""):
    """``lookup(key, no)`` for message_refs: the task a number in a message
    names, read in the project of the room it was sent in. Nothing is read
    until a message names a task."""
    project: list[str] = []

    def lookup(key: str, no: int) -> dict | None:
        if not project:
            pid = project_id
            if room_id and not pid:
                room = next((e for e in _task_index() if e["id"] == room_id), None)
                pid = _task_project(room, load_session_projects(), load_projects()) if room else ""
            project.append(pid)
        rid, _why = resolve_task_ref(f"{key}-{no}" if key else f"#{no}", project[0])
        return task_ref_info(rid) if rid else None
    return lookup


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
    # taskDir is the task's own folder; cwd is where its agents run, which for a
    # `copy`/`worktree` task is <taskDir>/repo and for `inplace` is the project
    # itself. The workspace tree needs the folder, not the cwd, so carry both.
    keep = ("sessionId", "roomId", "label", "status", "isLive", "idleSeconds",
            "updatedAt", "agents", "members", "mode", "headless", "cwd",
            "taskDir", "priority", "priorityName", "workflow", "workflowName",
            "lastAgent", "attention", "allocation", "reviewAllocations", "no")
    # One group per registered project, plus a synthetic unassigned bucket.
    groups: dict = {}
    keys = project_keys(projects_reg)
    for p in projects_reg:
        groups[p["id"]] = {"id": p["id"], "name": p["name"], "path": p["path"], "key": keys.get(p["id"], ""),
                           "home": project_home(p, create=False),
                           "poRoomId": p.get("poRoomId", ""), "kind": p.get("kind") or "code",
                           "isGit": p.get("isGit", False), "registered": True,
                           "sessions": [], "live": 0, "waiting": 0, "updatedAt": 0}
    UNASSIGNED = "__unassigned__"
    # A project's PO is not one of its tasks: it stays in `sessions` (the page
    # finds and chooses the PO there) but counts as no task live or waiting.
    po_rooms = {p.get("poRoomId") for p in projects_reg if p.get("poRoomId")}
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
        g["updatedAt"] = max(g["updatedAt"], s.get("updatedAt") or 0)
        if s.get("roomId") and s.get("roomId") in po_rooms:
            continue
        if s.get("isLive"):
            g["live"] += 1
            agents_live += len(s.get("agents") or [1])
        # "Needs you" now means what the notifications mean — a dead agent or
        # one at its usage limit counts, not just a task that says it's waiting.
        if s.get("attention") or s.get("status") in ("waiting", "waiting_human"):
            g["waiting"] += 1
            needs_you += 1
    projects = []
    total_changed = 0
    for gid, g in groups.items():
        g["changed"] = git_changed_count(g["path"]) if (g.get("isGit") and g.get("path")) else 0
        total_changed += g["changed"]
        g["count"] = len(g["sessions"])
        # Priority first, most-recently-active first inside a priority — the
        # same rule the flat task list uses.
        g["sessions"] = [{k: s.get(k) for k in keep}
                         for s in sorted(g["sessions"],
                                         key=lambda x: (x.get("priority") or DEFAULT_PRIORITY,
                                                        -(x.get("updatedAt") or 0)))]
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
_TEXT_AFTER_LINE = 64 * 1024   # opened at a line past the cap: this much of what follows it


def _text_cap(raw: bytes, line: int) -> int:
    """How much of a file the viewer gets: the first 512 KB or, when it opens
    at a line further in (a search result, a link to name.py:120), as far as
    that line and a little after it, within the largest file a search reads. So
    any line a search found can be shown and marked."""
    if line <= 0 or raw.count(b"\n", 0, _TEXT_MAX) >= line:
        return _TEXT_MAX
    # Line N ends at its Nth newline: the shortest start of the file holding N.
    lo, hi = _TEXT_MAX, len(raw)
    if raw.count(b"\n") >= line:
        while lo < hi:
            mid = (lo + hi) // 2
            if raw.count(b"\n", 0, mid) >= line:
                hi = mid
            else:
                lo = mid + 1
    return min(workspace_search.FILE_BYTES_MAX, max(_TEXT_MAX, hi + _TEXT_AFTER_LINE))


def list_dir(path: str) -> tuple[int, dict]:
    if path.startswith("~"):             # a home path from a chat link: ~/notes/
        path = os.path.expanduser(path)
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
                # A documents project's file history is a git db too.
                if is_dir and child.name == file_history.DIR_NAME and (child / "HEAD").is_file():
                    continue
                # A task's folder says so, so a documents project's tree can hide
                # its tasks and still show the project's own folders.
                task = is_dir and (child / "task.json").is_file()
            except OSError:
                continue
            entries.append({"name": child.name, "type": "dir" if is_dir else "file",
                            "size": 0 if is_dir else st.st_size,
                            "mtime": int(st.st_mtime), **({"task": True} if task else {})})
    except OSError as e:
        return 500, {"error": f"read_failed: {e}"}
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    parent = str(d.parent) if workspace_access_ok(str(d.parent)) else ""
    return 200, {"path": str(d), "parent": parent, "entries": entries[:2000]}


def read_workspace_file(path: str, line: int = 0) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    f = Path(path)
    if not f.exists() or not f.is_file():
        return 404, {"error": "not_found"}
    try:
        with f.open("rb") as fh:
            raw = fh.read((workspace_search.FILE_BYTES_MAX if line > 0 else _TEXT_MAX) + 1)
        size = f.stat().st_size
    except OSError as e:
        return 500, {"error": f"read_failed: {e}"}
    cap = _text_cap(raw, line)
    truncated = len(raw) > cap
    raw = raw[:cap]
    if b"\x00" in raw:
        return 200, {"path": str(f), "binary": True, "text": "",
                     "size": size, "truncated": truncated}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return 200, {"path": str(f), "binary": False, "text": text,
                 "size": size, "truncated": truncated}


def _git_out(root: str, *args: str, timeout: int = 8) -> str:
    """stdout of ``git -C root <args>``, stripped; "" when git fails."""
    try:
        out = _run(["git", "-C", root, *args], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or "").strip() if out.returncode == 0 else ""


def git_branch_base(root: str) -> dict:
    """What a task's branch is compared against: the branch it was cut from.

    A task works on its own branch and commits as it goes, so its changes are
    everything since it left the main line, committed or not — not just what is
    uncommitted right now. The base is the first of main, master or the
    remote's default branch that exists and is not the branch itself; the diff
    is taken from the merge-base, so work landed on main since does not show
    up as this task's. A repo sitting on its main branch has no base, and its
    changes are the uncommitted ones."""
    branch = _git_out(root, "rev-parse", "--abbrev-ref", "HEAD")
    remote_head = _git_out(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    for cand in ("main", "master", remote_head):
        if not cand or cand == branch:
            continue
        if not _git_out(root, "rev-parse", "--verify", "--quiet", cand + "^{commit}"):
            continue
        mb = _git_out(root, "merge-base", "HEAD", cand)
        if not mb:
            continue
        ahead = _git_out(root, "rev-list", "--count", mb + "..HEAD")
        return {"branch": branch, "base": cand, "mergeBase": mb,
                "ahead": int(ahead) if ahead.isdigit() else 0}
    return {"branch": branch, "base": "", "mergeBase": "", "ahead": 0}


def _git_branch_files(root: str, mb: str) -> list[dict] | None:
    """Files that differ between the merge-base and the working tree, plus
    untracked ones: a branch's whole change, committed and not."""
    try:
        out = _run(["git", "-C", root, "diff", "--name-status", "-z", mb],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        if out.returncode != 0:
            return None
        parts = (out.stdout or "").split("\x00")
        files, i = [], 0
        while i < len(parts):
            code = parts[i]
            if not code:
                i += 1
                continue
            if code[0] in ("R", "C") and i + 2 < len(parts):   # old path, then new path
                files.append({"path": parts[i + 2], "status": code[0]})
                i += 3
                continue
            if i + 1 < len(parts):
                files.append({"path": parts[i + 1], "status": code[0]})
            i += 2
        un = _run(["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z"],
                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        seen = {f["path"] for f in files}
        files += [{"path": p, "status": "?"} for p in (un.stdout or "").split("\x00") if p and p not in seen]
    except (OSError, subprocess.SubprocessError):
        return None
    return files


def git_status(path: str, branch: bool = False) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    root = git_root(path)
    if not root or not path_is_git(root):
        return 200, {"root": root, "isGit": False, "files": []}
    if branch:
        info = git_branch_base(root)
        if info["mergeBase"]:
            files = _git_branch_files(root, info["mergeBase"])
            if files is None:
                return 500, {"error": "git_diff_failed"}
            files.sort(key=lambda f: f["path"].lower())
            return 200, {"root": root, "isGit": True, "files": files, **info}
        # On the main line itself: the branch's change is what is uncommitted.
        code, payload = git_status(path)
        if code == 200:
            payload.update(info)
        return code, payload
    files = []
    try:
        out = _run(["git", "-C", root, "status", "--porcelain=v1", "-z"],
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


def git_diff(path: str, file: str, branch: bool = False) -> tuple[int, dict]:
    if not path or not workspace_access_ok(path):
        return 403, {"error": "path_not_allowed"}
    root = git_root(path)
    if not root or not path_is_git(root):
        return 200, {"root": root, "isGit": False, "diff": ""}
    # A branch diff runs from where the branch left the main line to the
    # working tree, so it carries the commits and the uncommitted edits alike.
    against = (git_branch_base(root)["mergeBase"] if branch else "") or "HEAD"
    argv = ["git", "-C", root, "diff", against, "--"]
    if file:
        argv.append(file)
    try:
        out = _run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
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


# ---- Workspace find: go to file, search in files (workspace_search.py) ------

def _ws_find_root(root: str) -> tuple[int, dict] | None:
    """Why a folder cannot be searched, or None when it can: it must be an
    absolute folder the file APIs may read."""
    if not root or not workspace_access_ok(root):
        return 403, {"error": "path_not_allowed"}
    if not os.path.isdir(root):
        return 404, {"error": "not_found"}
    return None


def _ws_enclosing_ignores(root: str) -> bool:
    """Whether ``root`` sits inside a code repo whose ignore rules apply to it.
    The projects root is the backup repo, not code: its rules would hide a
    task's checkout, so they never apply."""
    top = git_root(root)
    return bool(top and not _within(top, root) and path_is_git(top)
                and not _within(str(PROJECTS_ROOT), top))


def _ws_abs(root: str) -> str:
    """An absolute folder with any ``..`` folded away before it is checked; a
    relative one is refused, not resolved against the hub's own folder."""
    return os.path.abspath(root) if root and os.path.isabs(root) else ""


def ws_files(root: str) -> tuple[int, dict]:
    root = _ws_abs(root)
    bad = _ws_find_root(root)
    if bad:
        return bad
    t0 = time.monotonic()
    files, cut = workspace_search.list_files(root, _ws_enclosing_ignores(root))
    return 200, {"root": root, "files": files, "truncated": cut, "max": workspace_search.FILES_MAX,
                 "ms": round((time.monotonic() - t0) * 1000)}


# tag -> (the highest seq seen, its child while it runs)
_WS_SEARCHES: dict[str, tuple[int, subprocess.Popen | None]] = {}
_WS_SEARCHES_LOCK = threading.Lock()
_WS_SEARCHES_KEEP = 500    # finished tags remembered; past this the finished ones are forgotten


def _ws_search_start(tag: str, seq: int, argv: list[str]) -> subprocess.Popen | None:
    """Start a search child, or None when a newer one is already under way.

    A box searches one thing at a time: the search it asked for last runs,
    and anything it asked for before is stopped or never started. "Last" is
    the box's own count (``seq``), not the order requests reach the hub: two
    searches typed in quick succession can arrive, or get this far, the other
    way round."""
    def spawn() -> subprocess.Popen:
        # Its own process group off Windows, so ending it ends what it started
        # (on Windows the child puts itself in a job that does the same).
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             start_new_session=os.name != "nt")
        p.ws_cancelled = False
        return p
    if not tag:
        return spawn()
    with _WS_SEARCHES_LOCK:
        cur = _WS_SEARCHES.get(tag)
        if cur is not None and cur[0] > seq:
            return None
        proc = spawn()
        _ws_searches_put(tag, seq, proc)
    _ws_stop(cur[1] if cur else None)
    return proc


def _ws_searches_put(tag: str, seq: int, proc: subprocess.Popen | None) -> None:
    """Under the lock: record a box's latest count, as its most recent entry.
    Past the limit the boxes heard from longest ago, with nothing running,
    are forgotten; never the one just recorded."""
    _WS_SEARCHES.pop(tag, None)
    _WS_SEARCHES[tag] = (seq, proc)
    over = len(_WS_SEARCHES) - _WS_SEARCHES_KEEP
    if over > 0:
        for k in [k for k, (_, p) in _WS_SEARCHES.items() if p is None and k != tag][:over]:
            del _WS_SEARCHES[k]


def _ws_kill(proc: subprocess.Popen) -> None:
    """End a search child and whatever it started."""
    with contextlib.suppress(OSError):
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)


def _ws_stop(proc: subprocess.Popen | None) -> bool:
    """End a search that is no longer wanted; whether one was still running."""
    if proc is None or proc.poll() is not None:
        return False
    proc.ws_cancelled = True
    _ws_kill(proc)
    return True


def ws_search_cancel(tag: str, seq: int) -> tuple[int, dict]:
    """The box no longer wants a search (its query was cleared or cut short,
    or Files was chosen): the one running ends now rather than when it is done
    or timed out, and one it asked for before that reaches the hub late never
    starts."""
    tag = tag[:200]
    if not tag:
        return 400, {"error": "no_tag"}
    with _WS_SEARCHES_LOCK:
        cur = _WS_SEARCHES.get(tag)
        if cur is not None and cur[0] > seq:
            return 200, {"stopped": False}
        _ws_searches_put(tag, seq, None)
    return 200, {"stopped": _ws_stop(cur[1] if cur else None)}


def _ws_search_done(tag: str, proc: subprocess.Popen) -> None:
    if tag:
        with _WS_SEARCHES_LOCK:
            cur = _WS_SEARCHES.get(tag)
            if cur is not None and cur[1] is proc:
                _WS_SEARCHES[tag] = (cur[0], None)


def ws_search(root: str, q: str, case: bool = False, regex: bool = False, tag: str = "", seq: int = 0,
              limit: int = workspace_search.MATCH_MAX, deadline: float = workspace_search.DEADLINE_S) -> tuple[int, dict]:
    root = _ws_abs(root)
    bad = _ws_find_root(root)
    if bad:
        return bad
    if not q:
        return 400, {"error": "empty_query"}
    if len(q) > workspace_search.QUERY_MAX:
        return 400, {"error": "query_too_long", "max": workspace_search.QUERY_MAX}
    try:
        workspace_search.compile_query(q, case, regex)
    except re.error as e:
        return 400, {"error": "bad_regex", "detail": str(e)}
    req = json.dumps({"root": root, "q": q, "case": case, "regex": regex, "limit": limit, "deadline": deadline,
                      "enclosingIgnores": _ws_enclosing_ignores(root)}).encode("utf-8")
    argv = [sys.executable, "-X", "utf8", str(Path(workspace_search.__file__).resolve())]
    try:
        proc = _ws_search_start(tag[:200], seq, argv)
    except OSError as e:
        return 500, {"error": f"search_failed: {e}"}
    if proc is None:
        return 409, {"error": "cancelled"}
    try:
        out, err = proc.communicate(req, timeout=deadline + 4)
    except subprocess.TimeoutExpired:
        _ws_kill(proc)
        proc.communicate()
        return 504, {"error": "search_timed_out", "seconds": deadline + 4}
    finally:
        _ws_search_done(tag[:200], proc)
    if proc.ws_cancelled:
        return 409, {"error": "cancelled"}
    try:
        res = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 500, {"error": "search_failed", "detail": err.decode("utf-8", errors="replace")[-400:]}
    if res.get("error"):
        return (500 if res["error"] == "search_failed" else 400), res
    res["root"] = root
    return 200, res


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


def _cwd_key(cwd: str) -> str:
    return os.path.normcase(os.path.normpath(cwd)) if cwd else ""


def load_chat_scheme_cwds() -> set[str]:
    try:
        data = json.loads(CHAT_SCHEME_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {_cwd_key(x) for x in data if isinstance(x, str) and x}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return set()


def set_chat_scheme(cwd: str, on: bool) -> bool:
    cwds = load_chat_scheme_cwds()
    key = _cwd_key(cwd)
    if on:
        cwds.add(key)
    else:
        cwds.discard(key)
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHAT_SCHEME_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(cwds), indent=2), encoding="utf-8")
    tmp.replace(CHAT_SCHEME_FILE)
    return key in cwds


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


# ---- Roadmap: ROADMAP.md in the project's home ------------------------------
# Plain Markdown next to the project's tasks, so it travels with the project
# and the projects backup carries it. The owner edits it in the dashboard, the
# project's PO through the MCP tools; neither may silently overwrite the other.
# Every read hands out a version (a hash of the file's bytes) and every write
# must name the version it was based on. A mismatch is refused and the newer
# text is returned, so both edits survive. A hash, not the mtime: it cannot
# collide on a coarse clock, and a file rewritten with the same bytes is not
# a conflict because nothing would be lost.
ROADMAP_NAME = "ROADMAP.md"
ROADMAP_MAX_BYTES = 1_000_000
_ROADMAP_LOCK = threading.Lock()


def roadmap_path(project: dict, create: bool = False) -> Path:
    return Path(project_home(project, create=create)) / ROADMAP_NAME


def _roadmap_state(fp: Path) -> dict:
    try:
        raw = fp.read_bytes()
        mtime = fp.stat().st_mtime
    except FileNotFoundError:
        return {"exists": False, "text": "", "version": "", "mtime": None}
    return {"exists": True,
            "text": raw.decode("utf-8", errors="replace").replace("\r\n", "\n"),
            "version": hashlib.sha256(raw).hexdigest()[:16],
            "mtime": round(mtime, 3)}


def read_roadmap(project: dict) -> dict:
    """The project's roadmap: {path, exists, text, version, mtime}. A roadmap
    nobody has written yet is exists=False with version ""."""
    fp = roadmap_path(project)
    return {"projectId": project.get("id", ""), "path": str(fp), **_roadmap_state(fp)}


def write_roadmap(project: dict, text: str, base_version: str) -> tuple[bool, dict]:
    """Save the roadmap if it is still the version the writer read.

    ``base_version`` is the version the writer's text started from ("" for a
    roadmap that did not exist yet). Returns (True, state after the write) or
    (False, {"error": ..., "current": state now}) — on a conflict the caller
    gets the newer text back and nothing on disk is touched."""
    if not isinstance(text, str):
        return False, {"error": "text must be a string"}
    data = text.replace("\r\n", "\n").encode("utf-8")
    if len(data) > ROADMAP_MAX_BYTES:
        return False, {"error": f"roadmap is too large ({len(data)} bytes, "
                                f"the limit is {ROADMAP_MAX_BYTES})"}
    fp = roadmap_path(project, create=True)
    with _ROADMAP_LOCK:
        cur = _roadmap_state(fp)
        if (base_version or "") != cur["version"]:
            return False, {"error": "conflict", "current": {"path": str(fp), **cur}}
        tmp = fp.with_name(f".{ROADMAP_NAME}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, fp)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False, {"error": f"could not write {fp}: {e}"}
        return True, {"projectId": project.get("id", ""), "path": str(fp),
                      **_roadmap_state(fp)}


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
            out = _run(
                ["git", "-C", ppath, "worktree", "add", str(dest), "-b", branch],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if out.returncode != 0:
                # Branch may already exist → attach without -b.
                out2 = _run(
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


def attach_search_rooms(results: list[dict], rooms: list[dict] | None = None) -> list[dict]:
    """Name the task each deep-search hit belongs to. A task row is its room,
    while the transcripts found are its agents' conversations, so without the
    room id the page could not show a single task among the matches."""
    if rooms is None:
        try:
            rooms = chatroom.list_rooms()
        except Exception:
            rooms = []
    room_of: dict[str, str] = {}
    for rm in rooms:
        for pp in rm.get("participants") or []:
            if pp.get("kind") == "agent":
                for sid in participant_session_ids(pp):
                    room_of.setdefault(sid, rm.get("id") or "")
    for r in results:
        rid = room_of.get(r.get("sessionId") or "")
        if rid:
            r["roomId"] = rid
    return results


def delete_session(sid: str) -> dict:
    """Remove a session's JSONL transcript and all sidecar entries
    (labels, parents, geometries, archive). Does not touch the cwd."""
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
        r = _run(
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


# The task list is asked for by every open dashboard tab every 2.5 s and by the
# project grouping on top. Concurrent callers wait for the one computation in
# flight and share its answer instead of each redoing it; the answer is kept for
# a couple of seconds, and dropped as soon as any write request completes, so an
# action is never followed by a stale list.
_SESS_TTL = 2.0
_SESS_GEN = 0
_SESS_CACHE: dict[int, tuple[float, int, list]] = {}
_SESS_LOCKS: dict[int, threading.Lock] = {}


def invalidate_session_listing() -> None:
    global _SESS_GEN
    _SESS_GEN += 1


def load_sessions(n: int = 200) -> list[dict]:
    lock = _SESS_LOCKS.setdefault(n, threading.Lock())
    with lock:
        hit = _SESS_CACHE.get(n)
        if hit and hit[1] == _SESS_GEN and time.time() - hit[0] < _SESS_TTL:
            rows = hit[2]
        else:
            gen = _SESS_GEN
            rows = _load_sessions_uncached(n)
            _SESS_CACHE[n] = (time.time(), gen, rows)
    return [dict(r) for r in rows]      # callers may annotate rows; never the cached ones


def _load_sessions_uncached(n: int = 200) -> list[dict]:
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
        # user-applied label or archive state — unless it's live.
        if (not is_live and cs.turns == 0 and not labels.get(sid)
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
            "archived": sid in archived_set,
            "jira": (sorted(
                (set(extract_jira_tickets(row_label, row_cwd)) | set(jira_links.get(sid, [])))
                - set(jira_unlinks.get(sid, []))
            ) if JIRA_ENABLED else []),
            "cost": 0.0,
        })
        out.append(row)
    # Sort: the owner's priority first (1 = highest), then — as the tie-break
    # inside one priority — the activity tiering the list has always used:
    # Tier 0: live & busy   (orange blinker — claude is doing something)
    # Tier 1: live & idle
    # Tier 2: historical
    # and most-recently-updated first within each tier. Combined with the
    # hover-freeze on the client, this gives "important on top" without rows
    # shuffling out from under your mouse. Note the consequence: a low-priority
    # running task now sorts below a higher-priority idle one.
    def _key(r):
        prio = r.get("priority") or DEFAULT_PRIORITY
        if r["isLive"]:
            tier = 0 if r.get("status") == "busy" else 1
            return (prio, tier, -r["updatedAt"])
        return (prio, 2, -r["updatedAt"])
    # Each headless session (solo or collaboration) becomes ONE row that opens
    # its window; its per-agent sub-sessions are hidden (they'd otherwise scatter
    # as a live claude row + a codex history row). Matched by agent session id
    # and per-agent subfolder cwd.
    collab_sids: set[str] = set()
    collab_cwds: set[str] = set()
    room_rows: list[dict] = []
    # Attention states for the same rooms, so a row can be marked without a
    # second lookup. Cached and independent of everything above — this adds no
    # measurable work to an already-slow endpoint.
    try:
        att_by_room = attention.by_room()
    except Exception:
        att_by_room = {}
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
            msgs = rm.get("messages", []) or []
            _user_msgs = [m for m in msgs
                          if m.get("from") == "user" and (m.get("text") or "").strip()]
            first_txt = ((_user_msgs[0] if _user_msgs else (msgs[0] if msgs else {})).get("text") or "")[:200]
            last_txt = ((msgs[-1] if msgs else {}).get("text") or "")[:200]
            # The issue view's summary answers "what did this task do", so it
            # needs the last thing an AGENT said. `last` is the last message
            # from anyone, which would happily caption your own question as
            # "what happened".
            _agent_msgs = [m for m in msgs
                           if m.get("from") != "user" and (m.get("text") or "").strip()]
            last_agent_txt = ((_agent_msgs[-1] if _agent_msgs else {}).get("text") or "")[:400]
            room_cost = compute_room_cost(rm)
            room_rows.append({
                "sessionId": rid, "roomId": rid, "headless": True,
                # Its number in its project (#18), when it has one.
                "no": rm.get("no") or None,
                "mode": rm.get("mode", ""),
                "agent": (agents_in[0]["agent"] if len(agents_in) == 1 else "duo"),
                "agents": [p.get("identity", "") for p in agents_in],
                "members": [{"identity": p.get("identity", ""),
                             "agent": p.get("agent", ""),
                             "model": p.get("model", ""),
                             "role": p.get("role", ""),
                             **({"onMention": True,
                                 "reviewing": _pty_alive(p.get("ptyId"))}
                                if chatroom.is_on_mention(rm, p) else {})}
                            for p in agents_in],
                "label": labels.get(rid) or rm.get("title", ""), "cwd": rm.get("cwd", ""),
                "spec": rm.get("spec", ""), "taskDir": rm.get("taskDir", ""),
                "priority": priority_of(rm),
                "priorityName": PRIORITY_NAMES[priority_of(rm)],
                "workflow": workflow_of(rm),
                "workflowName": WORKFLOW_LABELS[workflow_of(rm)],
                # A draft is a task created (e.g. by a planning agent) but never
                # launched; Open/Start launches it fresh with its spec.
                "draft": not rm.get("launched", True),
                "isLive": live, "status": "busy" if (live and busy) else "idle",
                "updatedAt": rm.get("updatedAt", rm.get("createdAt", 0)),
                "startedAt": rm.get("createdAt", 0),
                "turns": len(rm.get("messages", [])),
                "idleSeconds": (idle if live else None),
                "pid": None, "archived": False,
                "parent": "", "jira": [], "cost": room_cost.get("dollars", 0.0),
                # Tokens across every conversation, Codex's included (which
                # have no price, so "cost" alone shows nothing for them).
                "costTokens": room_cost.get("tokens"),
                "currentTheme": "",
                "first": first_txt, "last": last_txt,
                "lastAgent": last_agent_txt, "transcriptPath": "",
                "allocation": rm.get("allocation"),
                # Only the latest swap: the panel shows no more, and every
                # board poll carries this row.
                "reviewAllocations": [a for a in (rm.get("reviewAllocations") or [])
                                      if isinstance(a, dict) and a.get("changed")][-1:],
                # {state, reason, agentIdentity} when this task needs a human.
                "attention": ({k: v for k, v in att_by_room[rid].items()
                               if k in ("state", "reason", "agentIdentity", "since")}
                              if rid in att_by_room else None),
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
            "idleSeconds": None, "pid": None, "archived": False,
            "parent": "", "jira": [],
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
        # Rows that aren't tasks (legacy transcripts, orphan groups) have no
        # room record to carry a priority — they read as medium.
        r.setdefault("priority", DEFAULT_PRIORITY)
        r.setdefault("priorityName", PRIORITY_NAMES[r["priority"]])
        r.setdefault("workflow", "backlog")
        r.setdefault("workflowName", WORKFLOW_LABELS[r["workflow"]])
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
                    return _run(
                        ["git", *args], cwd=str(install_dir),
                        capture_output=True, text=True, timeout=5,
                    ).stdout.strip()
                # Fetch the remote our branch actually tracks — not always
                # "origin". Otherwise we'd fetch origin but compare against
                # tr/main and get stale/inconsistent numbers.
                upstream_ref = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
                remote_name = upstream_ref.split("/", 1)[0] if "/" in upstream_ref else "origin"
                fetch = _run(
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
    Task Scheduler on Windows). Returns immediately.

    ENSEMBLE_UPDATE_DRY_RUN answers as if the update had started, without
    running it: on a test hub the real one would reset that hub's checkout and
    bounce the machine's Ensemble scheduled task — the REAL hub."""
    if os.environ.get("ENSEMBLE_UPDATE_DRY_RUN"):
        return {"started": True, "dryRun": True, "pid": os.getpid()}
    result = BACKEND.self_update(STATIC_DIR)
    if result.get("started"):
        global _UPDATE_CHECK_CACHE
        _UPDATE_CHECK_CACHE = None
    return result


# --- A plain restart ---------------------------------------------------------
# Stop and start the hub on the code already on disk: no fetch, reset or pull,
# so a merge not yet pushed survives (the Update path resets main to its
# upstream). A detached helper (restart-hub.ps1) first starts that code on a
# spare port and gives up, leaving the hub alone, if it does not serve; then
# waits RESTART_GRACE_S so the caller's reply reaches the user, restarts the
# hub, resumes the PO and types it a note. Same lock as /api/update.
RESTART_GRACE_S = 45
RESTART_BUSY_S = 180            # a second request this soon is refused
_RESTART_LOCK = threading.Lock()


# The restart lease: a file in the state dir, created exclusively, so two
# requests at once cannot both start a helper and the hub that comes back still
# refuses a second restart until it expires. The helper renews "at" at every
# wait, so it cannot expire mid-restart, and last when the hub is back; it drops
# it when its preflight fails (the hub was not touched, so trying again is
# fine); a helper that could not be started drops it here.
def _restart_lease_path() -> Path:
    return DASHBOARD_DIR / "restart.lease"


def _restart_lease_age(path: Path) -> float:
    try:
        return time.time() - float(json.loads(path.read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        try:
            return time.time() - path.stat().st_mtime    # being written right now
        except OSError:
            return RESTART_BUSY_S                       # gone meanwhile


def _take_restart_lease() -> tuple[str, float]:
    """(lease id, 0) when taken; ("", age in seconds) while one is held."""
    path = _restart_lease_path()
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    with _RESTART_LOCK:
        for _ in range(3):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                age = _restart_lease_age(path)
                if 0 <= age < RESTART_BUSY_S:
                    return "", age
                with contextlib.suppress(OSError):
                    path.unlink()                       # expired
                continue
            lease = uuid.uuid4().hex
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"id": lease, "at": time.time(), "pid": os.getpid()}, f)
            return lease, 0.0
    return "", 0.0


def _drop_restart_lease(lease: str) -> None:
    path = _restart_lease_path()
    with _RESTART_LOCK:
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("id") == lease:
                path.unlink()
        except (OSError, ValueError, AttributeError):
            pass


def _spare_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def restart_plan(room_id: str = "") -> dict:
    """What the helper needs: how this hub was started (to start it again the
    same way), where to preflight, whom to resume and what to tell them.
    ``room_id`` is the calling PO's room; without one (the dashboard page) the
    restart rooms that have a live terminal are resumed, and nobody is told."""
    if room_id:
        resume = [room_id]
    else:
        live = {(s.get("meta") or {}).get("room") for s in ptyrun.list_sessions()
                if s.get("alive")}
        resume = sorted(r for r in HUB_RESTART_ROOMS if r in live)
    wake = ""
    if room_id:
        room = chatroom.get_room(room_id, public=False) or {}
        project = {p["id"]: p for p in load_projects()}.get(
            ensemble_tools._project_of_room(room) if room else "")
        handover = str(rotation.handover_path(project)) if project else rotation.HANDOVER_NAME
        who = operator_name()
        wake = (f"[from the restart helper, not {who}] The hub restarted on the code "
                "already on disk (a plain restart: nothing was fetched, reset or pulled) "
                "after a passing preflight, and you, the PO, were resumed. First verify "
                "that /, /session, /fileview, /api/usage and /api/settings answer. Then "
                f"start what {handover} lists to start now, resume any other room that was "
                f"running, and tell {who} what is running. Do not restart the hub again.")
    try:
        grace = max(0, min(300, int(os.environ.get("ENSEMBLE_RESTART_GRACE", RESTART_GRACE_S))))
    except ValueError:
        grace = RESTART_GRACE_S
    logs = DASHBOARD_DIR / "logs"
    return {
        "repo": str(STATIC_DIR),
        "python": sys.executable,
        "script": str(Path(__file__).resolve()),
        "args": sys.argv[1:],
        "port": HUB_PORT,
        "hubPid": os.getpid(),
        "preflightPort": _spare_port(),
        "taskName": APP_NAME,
        # A test hub never touches the machine's scheduled task, whatever port.
        "noTask": bool(os.environ.get("ENSEMBLE_RESTART_NO_TASK")),
        "graceSeconds": grace,
        "resumeRooms": resume,
        "wakeRoom": room_id if wake else "",
        "wakeText": wake,
        "log": str(logs / "restart.log"),
        "preflightLog": str(logs / "preflight.log"),
        "env": dict(os.environ),
    }


def trigger_restart(room_id: str = "") -> dict:
    """Start a plain restart; returns at once. ``status`` is the HTTP code.

    ENSEMBLE_RESTART_DRY_RUN answers with the plan instead of running it."""
    lease, since = _take_restart_lease()
    if not lease:
        return {"started": False, "status": 409,
                "error": f"a restart began {int(since)}s ago and is still under way or has just "
                         f"finished — see {DASHBOARD_DIR / 'logs' / 'restart.log'}. "
                         f"Try again after {int(RESTART_BUSY_S - since) + 1}s."}
    try:
        plan = restart_plan(room_id)
        plan["leasePath"] = str(_restart_lease_path())
        plan["leaseId"] = lease
        if os.environ.get("ENSEMBLE_RESTART_DRY_RUN"):
            _drop_restart_lease(lease)
            view = {k: v for k, v in plan.items() if k != "env"}
            return {"started": True, "status": 202, "dryRun": True, "plan": view}
        result = BACKEND.self_restart(plan)
    except BaseException:
        _drop_restart_lease(lease)
        raise
    if not result.get("started"):
        _drop_restart_lease(lease)
        return {**result, "status": 500}
    return {**result, "status": 202, "graceSeconds": plan["graceSeconds"],
            "preflightPort": plan["preflightPort"], "resumeRooms": plan["resumeRooms"],
            "log": plan["log"],
            "message": (f"Restart started. In about {plan['graceSeconds']}s, once the code "
                        f"on disk has served on port {plan['preflightPort']}, the hub stops "
                        "and starts again; every agent on it stops with it. "
                        + ("You will be resumed and told when it is back. "
                           if plan["wakeRoom"] else "")
                        + f"Progress: {plan['log']}")}


# ---------- HTTP server ----------

# ---------------------------------------------------------------------------
# Task service — the ONE implementation behind both the REST endpoints the UI
# calls and the ensemble_* MCP tools agents call. A task is a room (plus its
# folder under the project home); "launching" it is a separate step owned by
# the Handler (it needs the server port for the MCP URL).
# ---------------------------------------------------------------------------

# Task priority — the five Jira levels, stored as a small int so it sorts
# naturally (1 = highest through 5 = lowest) with the name kept for display.
# A task without the field reads as medium, so tasks created before priorities
# existed need no migration: they are only rewritten when someone sets one.
PRIORITY_NAMES = {1: "highest", 2: "high", 3: "medium", 4: "low", 5: "lowest"}
PRIORITY_NUMBERS = {name: n for n, name in PRIORITY_NAMES.items()}
DEFAULT_PRIORITY = 3
PRIORITY_CHOICES = ", ".join(PRIORITY_NAMES[n] for n in sorted(PRIORITY_NAMES))


def normalize_priority(value) -> int | None:
    """A caller's priority — a name ("high") or a number (2, "2") — as the
    stored int. None when it isn't a valid priority, so callers can reject it."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() and int(value) in PRIORITY_NAMES else None
    s = str(value).strip().lower()
    if s.isdigit():
        return int(s) if int(s) in PRIORITY_NAMES else None
    return PRIORITY_NUMBERS.get(s)


def priority_of(record: dict) -> int:
    """The priority of a room/task record — medium for anything that predates
    the field or carries a value we can't read."""
    return normalize_priority((record or {}).get("priority")) or DEFAULT_PRIORITY


# Workflow — the board column a task sits in. Unlike run state (draft /
# running / paused / stopped) this is CHOSEN, stored, and only ever changed by
# someone deciding to change it.
#
# The distinction is load-bearing. Run state is computed from a live PTY in an
# in-memory registry, so it resets on every hub restart and flips as agents
# finish asynchronously. Columns built on it would empty into "Done" whenever
# the hub restarted, and would move a card out from under the mouse mid-click.
# The dot on the card still tells the truth about what is running; the column
# tells the truth about what was decided. They are allowed to disagree.
WORKFLOW_NAMES = ensemble_tools.WORKFLOW_NAMES   # one list of columns, owned by the tools module
WORKFLOW_LABELS = {"backlog": "Backlog", "todo": "To do",
                   "inprogress": "In progress", "inreview": "In review",
                   "done": "Done"}
WORKFLOW_CHOICES = ", ".join(WORKFLOW_NAMES)
# Only a ProductOwner may accept work. Assigned by the human alone — see
# normalize_agent_specs, which refuses the role from an agent-side caller.
PRODUCT_OWNER_ROLE = chatroom.PRODUCT_OWNER_ROLE


class StartRoomError(Exception):
    """A first launch that cannot be satisfied by an installed agent kind."""


OWNER_ONLY_WORKFLOW = ("done",)


def normalize_workflow(value) -> str | None:
    """A caller's column — "inprogress", "In progress", "in-progress" — as the
    stored key. None when it isn't one, so callers can reject it."""
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    return s if s in WORKFLOW_NAMES else None


def workflow_of(record: dict) -> str:
    """The column a task sits in. A task that predates the field — or one
    created before it was recorded — has no stored value, so we DERIVE one from
    its run state rather than migrating anything. Nothing is written until
    someone moves the card, exactly the way priority_of defaults to medium.

    Note the asymmetry that makes this safe: _start_room stores "inprogress" the
    moment a task launches, so anything that has ever run carries a real
    value. Only never-launched tasks fall through to the derived default, and
    `launched` is itself persisted — so their column is stable across a
    restart, which is the failure this whole design exists to avoid."""
    stored = normalize_workflow((record or {}).get("workflow"))
    if stored:
        return stored
    if not (record or {}).get("launched", True):
        return "backlog"
    return "done" if not _room_is_live(record or {}) else "inprogress"


def is_product_owner(room: dict, identity: str) -> bool:
    """Whether this caller holds the ProductOwner role in its own room.

    ``identity`` reaches us from ``chatroom.resolve_token`` — the bearer token
    minted per participant at room creation — so an agent cannot answer this
    question by claiming to be someone else. Combined with the role being
    unassignable from the agent side, that makes the check a real one rather
    than an honour system."""
    return chatroom.is_product_owner_part(chatroom.participant(room or {}, identity) or {})


def normalize_agent_specs(agent_list, human: bool = False) -> tuple[list[tuple[str, str, str]], str]:
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
        # The ProductOwner accepts work, so it is the one role an agent may not
        # hand out — including to itself. Only the human assigns it, from the
        # UI, where `human` is true.
        if not human and (role or "").strip().lower().replace(" ", "") == PRODUCT_OWNER_ROLE:
            return [], "role_reserved:productowner"
        specs.append((ak, mdl, role))
    return specs, ""


def normalize_agent_preferences(agent_list, human: bool = False) -> tuple[list[dict], str]:
    """The stored first-launch preference for each seat.

    The existing ``agent`` / ``model`` / ``role`` shape remains the preferred
    line-up. A seat may additionally name ``alt: {agent, model}``, so a model
    chosen specifically for the other kind survives an allowance-driven swap.
    An unavailable alternative is valid as a preference but can never be
    selected on this machine.
    """
    specs, err = normalize_agent_specs(agent_list, human=human)
    if err:
        return [], err
    out = []
    for item, (agent_key, model, role) in zip(agent_list, specs):
        pref = {"agent": agent_key, "model": model, "role": role}
        alt = item.get("alt") if isinstance(item, dict) else None
        if alt is not None:
            if not isinstance(alt, dict):
                return [], "alt_must_be_an_agent"
            alt_agent = (alt.get("agent") or "").strip()
            alt_model = (alt.get("model") or "").strip()
            if not alt_agent or agents.get_agent(alt_agent) is None:
                return [], f"agent_unavailable:{alt_agent}"
            pref["alt"] = {"agent": alt_agent, "model": alt_model}
        out.append(pref)
    return out, ""


def _allocation_owner_index(lineup: list[dict]) -> int:
    """The one seat whose allowance drives a first-launch decision."""
    if len(lineup) <= 1:
        return 0
    for i, seat in enumerate(lineup):
        role = (seat.get("role") or "").split(":", 1)[0].strip().lower()
        if re.search(r"\bengineer\b", role):
            return i
    for i, seat in enumerate(lineup):
        if chatroom.role_key(seat.get("role", "")) == PRODUCT_OWNER_ROLE:
            return i
    return 0


def _allocation_reviewer_index(lineup: list[dict], owner_index: int) -> int | None:
    for i, seat in enumerate(lineup):
        if i == owner_index:
            continue
        role = (seat.get("role") or "").split(":", 1)[0].strip().lower()
        if role == chatroom.REVIEWER_ROLE:
            return i
    return None


def _kind_usage(snapshot: dict, kind: str) -> dict:
    """Worst usable account window for ``kind`` from the cached snapshot.

    Unknown values are skipped, while an untrusted value remains usable as a
    floor. Model-specific limits are not an agent-kind allowance and therefore
    do not participate in this decision.
    """
    source = next((s for s in snapshot.get("sources", [])
                   if s.get("source") == kind), None)
    if not source or source.get("state") != "ok":
        return {"state": "unknown", "error": (source or {}).get("error", "source unavailable")}
    windows = []
    for window in source.get("windows") or []:
        if window.get("kind") not in ("five_hour", "seven_day"):
            continue
        if (window.get("percent") is None or window.get("rolledOver")
                or window.get("resetUnknown")):
            continue
        windows.append(window)
    if not windows:
        return {"state": "unknown", "error": "no current 5-hour or 7-day reading"}
    worst = max(windows, key=lambda w: float(w.get("percent") or 0))
    return {
        "state": "known",
        "percent": worst.get("percent"),
        "window": worst.get("kind"),
        "label": "5-hour" if worst.get("kind") == "five_hour" else "7-day",
        "trusted": bool(worst.get("trusted")),
        "atLeast": not bool(worst.get("trusted")),
    }


def _agent_kind_name(kind: str) -> str:
    agent = agents.get_agent(kind)
    return (agent.display_name if agent is not None else kind.title())


def _usage_reason_phrase(kind: str, reading: dict) -> str:
    percent = reading.get("percent")
    value = f"{float(percent):g}" if percent is not None else "?"
    floor = "at least " if reading.get("atLeast") else ""
    return (f"{_agent_kind_name(kind)} {reading.get('label', 'usage')} window "
            f"at {floor}{value}%")


def _seat_for_kind(preference: dict, kind: str) -> dict:
    """Copy a preferred seat onto ``kind`` without leaking another kind's model."""
    model = ""
    if preference.get("agent") == kind:
        model = preference.get("model", "")
    else:
        alt = preference.get("alt") or {}
        if alt.get("agent") == kind:
            model = alt.get("model", "")
    return {"agent": kind, "model": model, "role": preference.get("role", "")}


def choose_agent_kind_for_seat(preferred_kind: str, snapshot: dict,
                               installed=None, current_kind: str = "") -> dict:
    """Choose one seat's kind using the allowance rules shared by task launch
    and reviews.

    ``current_kind`` matters for a recurring seat: an unknown allowance reading
    keeps what is already assigned.  A first launch omits it, making the
    preferred kind the current kind too.  The returned decision is deliberately
    model-free; callers apply the seat preference with :func:`_seat_for_kind`.
    """
    other_kind = {"claude": "codex", "codex": "claude"}.get(preferred_kind, "")
    warn = snapshot.get("warnPercent", usage.WARN_PERCENT)
    alarm = snapshot.get("alarmPercent", usage.ALARM_PERCENT)
    figures = {kind: _kind_usage(snapshot, kind) for kind in ("claude", "codex")}
    preferred_usage = figures.get(preferred_kind, {"state": "unknown"})
    other_usage = figures.get(other_kind, {"state": "unknown"})
    installed = installed or (lambda kind: bool(
        agents.get_agent(kind) and agents.get_agent(kind).installed()))
    current_kind = current_kind or preferred_kind

    def result(kind: str, decision: str) -> dict:
        return {
            "preferredKind": preferred_kind,
            "currentKind": current_kind,
            "chosenKind": kind,
            "decision": decision,
            "changed": kind != current_kind,
            "warnPercent": warn,
            "alarmPercent": alarm,
            "figures": figures,
        }

    if not installed(preferred_kind):
        if other_kind and installed(other_kind):
            return result(other_kind, "preferred_uninstalled")
        raise StartRoomError(f"agent_unavailable:{preferred_kind}")

    both_alarm = all(figures[k].get("state") == "known"
                     and float(figures[k]["percent"]) >= alarm
                     for k in ("claude", "codex"))
    if both_alarm:
        return result(preferred_kind, "both_alarm")
    if (preferred_usage.get("state") != "known"
            or other_usage.get("state") != "known"):
        # A recurring seat keeps its current installed kind when the cache says
        # nothing trustworthy enough to make a change.
        if installed(current_kind):
            return result(current_kind, "unknown")
        return result(preferred_kind, "unknown")
    if not other_kind or not installed(other_kind):
        return result(preferred_kind, "other_uninstalled")
    if (float(preferred_usage["percent"]) >= warn
            and float(other_usage["percent"]) < warn):
        return result(other_kind, "switch_warning")
    if float(preferred_usage["percent"]) < warn:
        return result(preferred_kind, "preferred_below_warning")
    return result(preferred_kind, "both_warning")


def choose_first_launch_allocation(preferred: list[dict], snapshot: dict,
                                   installed=None) -> tuple[list[dict], dict]:
    """Choose the first-launch line-up and return it with its audit record."""
    preferred = copy.deepcopy(preferred)
    chosen = [{"agent": seat.get("agent", ""),
               "model": seat.get("model", ""),
               "role": seat.get("role", "")}
              for seat in preferred]
    owner_i = _allocation_owner_index(preferred)
    owner_kind = preferred[owner_i].get("agent", "")
    other_kind = {"claude": "codex", "codex": "claude"}.get(owner_kind, "")
    installed = installed or (lambda kind: bool(
        agents.get_agent(kind) and agents.get_agent(kind).installed()))
    warn = snapshot.get("warnPercent", usage.WARN_PERCENT)
    alarm = snapshot.get("alarmPercent", usage.ALARM_PERCENT)
    figures = {kind: _kind_usage(snapshot, kind) for kind in ("claude", "codex")}
    owner_usage = figures.get(owner_kind, {"state": "unknown"})

    def result(reason: str, changed: bool) -> tuple[list[dict], dict]:
        return chosen, {
            "preferred": preferred,
            "chosen": copy.deepcopy(chosen),
            "reason": reason,
            "changed": changed,
            "at": time.time(),
            "usage": {"snapshotState": snapshot.get("state"),
                      "checkedAt": snapshot.get("checkedAt"),
                      "warnPercent": warn, "alarmPercent": alarm, "kinds": figures},
        }

    unavailable_seats = [i for i, seat in enumerate(preferred)
                         if not installed(seat.get("agent", ""))]
    if unavailable_seats:
        switched = []
        for i in unavailable_seats:
            old_kind = preferred[i].get("agent", "")
            new_kind = {"claude": "codex", "codex": "claude"}.get(old_kind, "")
            if not new_kind or not installed(new_kind):
                raise StartRoomError(f"agent_unavailable:{old_kind}")
            chosen[i] = _seat_for_kind(preferred[i], new_kind)
            switched.append(f"{_agent_kind_name(old_kind)} to {_agent_kind_name(new_kind)}")
        reason = (f"Unavailable agent seat switched from {', '.join(switched)} because its "
                  f"preferred kind is not installed on this machine.")
        return result(reason, True)

    decision = choose_agent_kind_for_seat(owner_kind, snapshot, installed=installed)
    if decision["decision"] == "both_alarm":
        reason = (f"Preferred line-up kept although Claude and Codex are both at or above "
                  f"the {float(alarm):g}% alarm.")
    elif decision["decision"] == "unknown":
        reason = "Preferred line-up kept because a current allowance reading is unavailable."
    elif decision["decision"] == "other_uninstalled":
        name = _agent_kind_name(other_kind) if other_kind else "The other agent kind"
        reason = f"Preferred line-up kept because {name} is not installed on this machine."
    elif decision["decision"] == "switch_warning":
        old_owner_kind = owner_kind
        chosen[owner_i] = _seat_for_kind(preferred[owner_i], other_kind)
        reviewer_i = _allocation_reviewer_index(preferred, owner_i)
        if reviewer_i is not None:
            chosen[reviewer_i] = _seat_for_kind(preferred[reviewer_i], old_owner_kind)
        reason = (f"Owner switched to {_agent_kind_name(other_kind)}: "
                  f"{_usage_reason_phrase(old_owner_kind, owner_usage)}.")
        return result(reason, True)
    elif decision["decision"] == "preferred_below_warning":
        reason = (f"Preferred line-up kept because {_usage_reason_phrase(owner_kind, owner_usage)} "
                  f"is below the {float(warn):g}% warning.")
    else:
        reason = (f"Preferred line-up kept because both agent kinds are at or above "
                  f"the {float(warn):g}% warning.")
    return result(reason, False)


def apply_first_launch_allocation(room_full: dict) -> dict | None:
    """Choose and persist a new task's agents exactly once, before spawning."""
    preferred = room_full.get("agentPreference")
    if not isinstance(preferred, list) or not preferred:
        return None                         # pre-feature room: historical behavior
    if isinstance(room_full.get("allocation"), dict):
        return room_full["allocation"]     # a failed spawn does not re-decide
    if len(preferred) == 1 and room_full.get("lineupPickedByHuman") is True:
        seat = preferred[0]
        agent = agents.get_agent(seat.get("agent", ""))
        if not (agent and agent.installed()):
            raise StartRoomError(f"agent_unavailable:{seat.get('agent', '')}")
        allocation = {
            "preferred": copy.deepcopy(preferred),
            "chosen": [],
            "reason": "kept as picked: a one-agent task you created",
            "changed": False,
            "at": time.time(),
            "usage": {"skipped": "human-picked solo"},
        }
        allocation["chosen"] = [
            {"identity": p.get("identity", ""), "agent": p.get("agent", ""),
             "model": p.get("model", ""), "role": p.get("role", "")}
            for p in chatroom.agent_participants(room_full)
        ]
        room_full["allocation"] = allocation
        chatroom.update_room(room_full)
        _patch_task_json(room_full.get("taskDir", ""),
                         agents=allocation["chosen"], allocation=allocation)
        return allocation
    for seat in preferred:
        kind = seat.get("agent", "")
        alternative = {"claude": "codex", "codex": "claude"}.get(kind, "")
        agent = agents.get_agent(kind)
        alt_agent = agents.get_agent(alternative) if alternative else None
        if not (agent and agent.installed()) and not (alt_agent and alt_agent.installed()):
            raise StartRoomError(f"agent_unavailable:{kind}")
    snapshot = {}
    try:
        snapshot = usage.snapshot()
        chosen, allocation = choose_first_launch_allocation(preferred, snapshot)
    except StartRoomError:
        raise
    except Exception as exc:                              # noqa: BLE001
        chosen = [{"agent": seat.get("agent", ""),
                   "model": seat.get("model", ""),
                   "role": seat.get("role", "")}
                  for seat in preferred]
        allocation = {
            "preferred": copy.deepcopy(preferred),
            "chosen": copy.deepcopy(chosen),
            "reason": "Preferred line-up kept because the allowance check failed.",
            "changed": False,
            "at": time.time(),
            "usage": {"snapshotState": snapshot.get("state"),
                      "checkedAt": snapshot.get("checkedAt"),
                      "warnPercent": snapshot.get("warnPercent", usage.WARN_PERCENT),
                      "alarmPercent": snapshot.get("alarmPercent", usage.ALARM_PERCENT),
                      "kinds": {}, "error": type(exc).__name__},
        }
    if allocation["changed"]:
        updated = chatroom.set_agents(room_full["id"], chosen, mode=room_full.get("mode", ""))
        if updated is not None:
            room_full.clear()
            room_full.update(updated)
    allocation["chosen"] = [
        {"identity": p.get("identity", ""), "agent": p.get("agent", ""),
         "model": p.get("model", ""), "role": p.get("role", "")}
        for p in chatroom.agent_participants(room_full)
    ]
    room_full["allocation"] = allocation
    chatroom.update_room(room_full)
    _patch_task_json(room_full.get("taskDir", ""),
                     agents=allocation["chosen"], allocation=allocation)
    return allocation


def _participant_seat_preference(room_full: dict, identity: str, part: dict) -> dict:
    """The stored preference belonging to ``identity``, with legacy fallback."""
    preferred = room_full.get("agentPreference")
    if isinstance(preferred, list):
        participants = chatroom.agent_participants(room_full)
        i = next((n for n, seat in enumerate(participants)
                  if seat.get("identity") == identity), None)
        if i is not None and i < len(preferred):
            return copy.deepcopy(preferred[i])
        reviewer_i = next((n for n, seat in enumerate(preferred)
                           if (seat.get("role") or "").split(":", 1)[0].strip().lower()
                           == chatroom.REVIEWER_ROLE), None)
        if reviewer_i is not None:
            return copy.deepcopy(preferred[reviewer_i])
    return {"agent": part.get("agent", ""), "model": part.get("model", ""),
            "role": part.get("role", "")}


def _review_allocation_reason(decision: dict, owner: dict) -> str:
    chosen_kind = decision["chosenKind"]
    preferred_kind = decision["preferredKind"]
    owner_kind = owner.get("agent", "")
    changed = decision["changed"]
    action = "switched to" if changed else "kept on"
    chosen_name = _agent_kind_name(chosen_kind)
    owner_name = _agent_kind_name(owner_kind)
    preferred_name = _agent_kind_name(preferred_kind)
    warn = decision["warnPercent"]
    alarm = decision["alarmPercent"]
    figures = decision["figures"]
    code = decision["decision"]
    if code == "check_failed":
        return f"Reviewer {action} {chosen_name} because the allowance check failed."
    if code == "preferred_uninstalled":
        return (f"Reviewer {action} {chosen_name} because {preferred_name} is not installed "
                "on this machine.")
    if code == "other_uninstalled":
        return (f"Reviewer {action} {chosen_name}, different from owner {owner_name}, because "
                f"{owner_name} is not installed on this machine.")
    if code == "unknown":
        return (f"Reviewer {action} {chosen_name} because a current allowance reading is "
                "unavailable.")
    if code == "both_alarm":
        return (f"Reviewer {action} {chosen_name}, the preferred kind different from owner "
                f"{owner_name}, although Claude and Codex are both at or above the "
                f"{float(alarm):g}% alarm.")
    if code == "switch_warning":
        preferred_usage = figures.get(preferred_kind, {})
        owner_usage = figures.get(owner_kind, {})
        return (f"Reviewer {action} {chosen_name}, the owner's kind: "
                f"{_usage_reason_phrase(preferred_kind, preferred_usage)} while "
                f"{_usage_reason_phrase(owner_kind, owner_usage)} is below the "
                f"{float(warn):g}% warning.")
    relation = f"different from owner {owner_name}"
    if code == "preferred_below_warning":
        detail = (f"{_usage_reason_phrase(preferred_kind, figures.get(preferred_kind, {}))} "
                  f"is below the {float(warn):g}% warning")
    else:
        detail = f"both agent kinds are at or above the {float(warn):g}% warning"
    return f"Reviewer {action} {chosen_name}, {relation}, because {detail}."


def apply_review_allocation(room_full: dict, identity: str) -> tuple[dict, dict, dict]:
    """Choose, persist and return one fresh review's reviewer allocation.

    The participant identity and bearer token stay stable. Kind and model are
    written together with the capped audit list before the brief is built, so
    every downstream identity/model decision observes the same reviewer.
    """
    part = chatroom.participant(room_full, identity)
    if part is None:
        raise StartRoomError(f"agent_unavailable:{identity}")
    participants = chatroom.agent_participants(room_full)
    owner_identity = next((owner for owner in chatroom.owners(room_full)
                           if owner != identity), "")
    owner = (chatroom.participant(room_full, owner_identity)
             if owner_identity else None)
    if owner is None:
        owner = next((p for p in participants if p.get("identity") != identity), None)
    if owner is None:
        raise StartRoomError("review_owner_unavailable")
    owner = dict(owner)  # part may change below; the audit describes this owner.
    owner_kind = owner.get("agent", "")
    preferred_kind = {"claude": "codex", "codex": "claude"}.get(owner_kind, "")
    current_kind = part.get("agent", "")
    seat_preference = _participant_seat_preference(room_full, identity, part)
    snapshot = {}
    installed = lambda kind: bool(agents.get_agent(kind) and agents.get_agent(kind).installed())
    try:
        snapshot = usage.snapshot()
        decision = choose_agent_kind_for_seat(
            preferred_kind, snapshot, installed=installed, current_kind=current_kind)
    except StartRoomError:
        raise
    except Exception as exc:                              # noqa: BLE001
        # Cache failures are never allowed to cost the task its review.
        if installed(current_kind):
            chosen_kind = current_kind
        elif preferred_kind and installed(preferred_kind):
            chosen_kind = preferred_kind
        elif owner_kind and installed(owner_kind):
            chosen_kind = owner_kind
        else:
            raise StartRoomError(f"agent_unavailable:{current_kind}") from exc
        decision = {
            "preferredKind": preferred_kind, "currentKind": current_kind,
            "chosenKind": chosen_kind, "decision": "check_failed",
            "changed": chosen_kind != current_kind,
            "warnPercent": snapshot.get("warnPercent", usage.WARN_PERCENT),
            "alarmPercent": snapshot.get("alarmPercent", usage.ALARM_PERCENT),
            "figures": {}, "error": type(exc).__name__,
        }

    chosen_kind = decision["chosenKind"]
    if decision["changed"]:
        # Its earlier sessions keep their kind, so a Delete finds each one.
        chatroom.patch_participant(room_full["id"], identity,
                                   {"sessionKinds": rotation.session_kinds(part)})
        chosen_seat = _seat_for_kind(seat_preference, chosen_kind)
        part["agent"] = chosen_kind
        part["model"] = chosen_seat.get("model", "")
    chosen = {"identity": identity, "agent": part.get("agent", ""),
              "model": part.get("model", ""), "role": part.get("role", "")}
    allocation = {
        "reviewer": identity,
        "owner": {"identity": owner.get("identity", ""), "agent": owner_kind},
        "preferred": _seat_for_kind(seat_preference, preferred_kind),
        "chosen": chosen,
        "reason": _review_allocation_reason(decision, owner),
        "changed": decision["changed"],
        "at": time.time(),
        "usage": {
            "snapshotState": snapshot.get("state"),
            "checkedAt": snapshot.get("checkedAt"),
            "warnPercent": decision["warnPercent"],
            "alarmPercent": decision["alarmPercent"],
            "kinds": decision["figures"],
            **({"error": decision["error"]} if decision.get("error") else {}),
        },
    }
    updated = chatroom.apply_review_allocation(
        room_full["id"], identity, chosen["agent"], chosen["model"], allocation,
        limit=_REVIEW_ALLOCATION_LIMIT)
    if updated is None:
        raise StartRoomError(f"agent_unavailable:{identity}")
    room_full = updated
    part = chatroom.participant(room_full, identity)
    assigned = [
        {"identity": p.get("identity", ""), "agent": p.get("agent", ""),
         "model": p.get("model", ""), "role": p.get("role", "")}
        for p in chatroom.agent_participants(room_full)
    ]
    _patch_task_json(room_full.get("taskDir", ""), agents=assigned,
                     reviewAllocations=room_full["reviewAllocations"])
    return room_full, part, allocation


def find_project(project_id: str) -> dict | None:
    pid = (project_id or "").strip()
    if not pid:
        return None
    return next((pr for pr in load_projects() if pr["id"] == pid), None)


def create_task(title: str, spec: str, project_id: str, agent_list,
                workspace: str = "empty",
                priority=None, human: bool = False) -> tuple[bool, dict | None, str]:
    """Create a task: its room, workspace and task folder — WITHOUT launching
    the agents (``launched`` is False until the Handler starts it). Returns
    (ok, room_full, error)."""
    title = (title or "multiagent session").strip()[:120]
    spec = (spec or "").strip()
    # "" is a caller who didn't say, not a caller who said something wrong —
    # the same reading the MCP layer takes.
    prio = (DEFAULT_PRIORITY if priority is None or priority == ""
            else normalize_priority(priority))
    if prio is None:
        return False, None, "bad_priority"
    preferences, err = normalize_agent_preferences(agent_list, human=human)
    if err:
        return False, None, err
    project_id = (project_id or "").strip()
    project = None
    if project_id:
        project = find_project(project_id)
        if project is None:
            return False, None, "no_such_project"
    # A documents project's tasks work in its folder unless told otherwise.
    if not workspace:
        workspace = "inplace" if project and project.get("kind") == "documents" else "empty"
    ok, base, ws_meta, msg = setup_session_workspace(project, workspace, title)
    if not ok:
        return False, None, msg
    members = [{"identity": pref["agent"], "agent": pref["agent"],
                "model": pref.get("model", ""), "role": pref.get("role", "")}
               for pref in preferences]
    room = chatroom.create_room(title, members)
    room_full = chatroom.get_room(room["id"], public=False)
    room_full["cwd"] = base
    room_full["projectId"] = project_id
    room_full["workspace"] = ws_meta
    room_full["spec"] = spec            # the task's specification, shown in the UI
    room_full["priority"] = prio        # 1 = highest … 5 = lowest (medium by default)
    room_full["taskDir"] = ws_meta.get("taskDir", "")
    # Project-backed workspaces (inplace/copy/worktree) are shared: agents
    # collaborate on the same files, not isolated per-identity subdirs.
    room_full["sharedCwd"] = ws_meta.get("mode", "empty") != "empty"
    # 1 agent → a solo session the human drives directly (no chat tools,
    # terminal-primary window). 2+ → an autonomous collaboration.
    room_full["mode"] = "solo" if len(preferences) < 2 else "collab"
    room_full["launched"] = False
    room_full["agentPreference"] = preferences
    # The solo exception is intentionally opt-in. Rooms created before this
    # field existed behave exactly as they did before (allowance-aware).
    room_full["lineupPickedByHuman"] = bool(human)
    if project_id:
        assign_session_project(room["id"], project_id)
    if ws_meta.get("taskDir"):
        _write_task_json(ws_meta["taskDir"], {
            "roomId": room["id"], "projectId": project_id, "title": title,
            "spec": spec, "priority": prio,
            # The room's participants, not the requested list: create_room
            # de-duplicates identities (claude, claude-2), and task.json is
            # meant to mirror the room record — which is what reassignment
            # keeps it in step with.
            "agents": [{"identity": p["identity"], "agent": p.get("agent", ""),
                        "model": p.get("model", ""), "role": p.get("role", "")}
                       for p in chatroom.agent_participants(room_full)],
            "agentPreference": preferences,
            "lineupPickedByHuman": bool(human),
            "mode": room_full["mode"], "workspace": ws_meta,
            "createdAt": int(time.time())})
    # Persist BEFORE any launch so an interrupted spawn leaves a resumable
    # draft, not a corrupt room with no cwd.
    chatroom.update_room(room_full)
    if project_id:
        assign_task_number(room["id"], project_id, room_full)
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


def update_task(rid: str, title=None, spec=None,
                priority=None, workflow=None) -> tuple[bool, dict | None, str]:
    """Amend a task's title, spec, priority and/or workflow column (room
    record, task.json and any label override the user set from the UI)."""
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
    if priority is not None:
        n = normalize_priority(priority)
        if n is None:
            return False, None, "bad_priority"
        room["priority"] = n
        patch["priority"] = n
    if workflow is not None:
        w = normalize_workflow(workflow)
        if w is None:
            return False, None, "bad_workflow"
        room["workflow"] = w
        # When the card was last moved, by anyone: a merge only moves a card
        # nobody has moved since (digest._settle_merges).
        room["workflowAt"] = time.time()
        patch["workflow"] = w
    if not patch:
        return False, None, "nothing_to_change"
    chatroom.update_room(room)
    _patch_task_json(room.get("taskDir", ""), **patch)
    return True, room, ""


def reassign_task(rid: str, agent_list, human: bool = False) -> tuple[bool, dict | None, str]:
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
    preferences, err = normalize_agent_preferences(agent_list, human=human)
    if err:
        return False, None, err
    # normalize_agent_specs validates and drops the identity; recover it from the
    # caller's own list (same order) so a retained agent can be matched by name.
    idents = [(a.get("identity") or "").strip() if isinstance(a, dict) else ""
              for a in agent_list]
    members = [{"identity": ident, "agent": pref["agent"],
                "model": pref.get("model", ""), "role": pref.get("role", "")}
               for ident, pref in zip(idents, preferences)]
    mode = "solo" if len(preferences) < 2 else "collab"
    room = chatroom.set_agents(rid, members, mode=mode)
    if room is None:
        return False, None, "no_such_room"
    assigned = [{"identity": pp["identity"], "agent": pp.get("agent", ""),
                 "model": pp.get("model", ""), "role": pp.get("role", "")}
                for pp in chatroom.agent_participants(room)]
    room["lineupPickedByHuman"] = bool(human)
    room["agentPreference"] = preferences
    if not room.get("launched", True):
        room.pop("allocation", None)
    chatroom.update_room(room)
    _patch_task_json(room.get("taskDir", ""), agents=assigned, mode=room["mode"],
                     lineupPickedByHuman=bool(human),
                     agentPreference=preferences,
                     **({"allocation": None} if not room.get("launched", True) else {}))
    return True, room, ""


def stop_task(rid: str) -> bool:
    """End a task's agents (kill their PTYs) but KEEP the room, so it stays one
    row with its spec and chat and can be resumed.

    Stopping is also how the product owner says "I've seen this and dealt with
    it", so it clears any recorded death: an agent that had already died on its
    own would otherwise keep its `agent_gone` notification forever — Stop can't
    relabel a record that was written once, at death, so it removes it.
    """
    # Under the rotation's gate, the room read inside it: a rotation under way
    # is told and ends its fresh terminal itself, and one that has already put
    # its fresh terminal on the room is seen here.
    with rotation.GATE:
        rotation.note_stopped(rid)
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
                ptyrun.forget_death(pid)
    # Cleared through the room lock, and only after the kills: writing the room
    # we read before a slow kill loop would drop a chat message posted during it.
    chatroom.clear_exits(rid)
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
        # Stop tells a rotation under way to end its fresh session; wait for
        # it, then read again, so the session it added is deleted too.
        if not rotation.await_rotation(rid):
            return {"ok": False, "error": "the task is being handed over to a fresh "
                                          "session — try again shortly",
                    "transcripts": [], "folders": []}
        room = chatroom.get_room(rid, public=False) or room
        cwds.append(room.get("cwd", "") or "")
        members = [{"agent": session_agent(pp, sid), "sessionId": sid,
                    "cwd": pp.get("cwd", "")}
                   for pp in room.get("participants", [])
                   if pp.get("kind") == "agent"
                   for sid in (participant_session_ids(pp) or [""])]
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
    # The next number there; the old one stays in previousNos.
    assign_task_number(rid, project_id)
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
        return self._cookie(TOKEN_COOKIE)

    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
        return ""

    # --- Who may restart the hub (see HUB_RESTART_ROOMS) ----------------------
    def _ui_cookie(self) -> str:
        # Named per port: cookies ignore the port, so two hubs on one machine
        # would otherwise overwrite each other's key in the same browser.
        return f"ensemble_ui_{self.server.server_address[1]}"

    def _is_page_navigation(self) -> bool:
        # Browsers send Sec-Fetch-* only to secure origins (https, or this
        # machine's loopback), so a page opened over the tailnet's plain http
        # has none; there a navigation still says Upgrade-Insecure-Requests and
        # asks for HTML. A bare curl sends neither.
        mode = self.headers.get("Sec-Fetch-Mode")
        if mode is not None:
            return mode == "navigate" and self.headers.get("Sec-Fetch-Dest") == "document"
        return (self.headers.get("Upgrade-Insecure-Requests") == "1"
                and "text/html" in (self.headers.get("Accept") or ""))

    def _ui_key_headers(self) -> list[tuple[str, str]]:
        """The cookie that lets this browser use the Update now button. Only
        for a page navigation, so a script that merely fetches the page does
        not pick the key up by accident."""
        if self._is_page_navigation():
            return [("Set-Cookie", f"{self._ui_cookie()}={_UI_KEY}; HttpOnly; "
                                   "SameSite=Strict; Path=/")]
        return []

    def _same_origin_request(self) -> bool:
        # A browser sends Origin on every POST, on http origins too. Matching
        # it to our own Host keeps out a page on another local port: that is
        # same-site, so SameSite alone would let its request carry the cookie.
        origin, host = self.headers.get("Origin") or "", self.headers.get("Host") or ""
        if not host or origin not in (f"http://{host}", f"https://{host}"):
            return False
        return self.headers.get("Sec-Fetch-Site") in (None, "same-origin")

    def _bearer_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth[:7].lower() == "bearer " else ""

    def _restart_refusal(self) -> str:
        """Empty when this request may restart the hub, else why it may not.
        Passes for the PO's room bearer token, or for the dashboard page: its
        key cookie on a request from the page's own origin."""
        token = self._bearer_token()
        if token:
            resolved = chatroom.resolve_token(token)
            return "" if resolved and may_restart_hub(*resolved) else RESTART_REFUSED
        key = self._cookie(self._ui_cookie())
        if not key:
            return RESTART_REFUSED
        if not hmac.compare_digest(key, _UI_KEY):
            # The page was opened before the hub last restarted.
            return RESTART_REFUSED + " Reload the dashboard page and try again."
        if not self._same_origin_request():
            return RESTART_REFUSED
        return ""

    def _page_refusal(self) -> str:
        """Empty when this request comes from the dashboard page (its key
        cookie, on a request from its own origin, sent by a program that is
        not an agent), else why not. For writes a task's agent must never make,
        such as restoring a file. The cookie and the headers alone prove
        nothing: an agent can fetch the page and replay them, so the process
        that sent the request is checked too (see peer_process)."""
        if self._bearer_token():
            return "Only the dashboard page can do this, not a task's agent."
        key = self._cookie(self._ui_cookie())
        if not key or not self._same_origin_request():
            return "Only the dashboard page can do this."
        if not hmac.compare_digest(key, _UI_KEY):
            return "Reload the dashboard page and try again."
        why = self._agent_peer()
        if why:
            return f"Only the dashboard page can do this, and this request did not come from it: {why}."
        return ""

    def _agent_peer(self) -> str:
        """Why the program that sent this request counts as an agent, or ""."""
        server = self.server.server_address[:2]
        conn = getattr(self, "connection", None)
        if conn is not None:
            try:
                server = conn.getsockname()[:2]
            except OSError:
                pass
        return peer_process.from_agent(self.client_address[:2], server, _agent_pids())

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
                # Re-encoded: a file link's path= holds \, spaces, & and #,
                # which a raw k=v join turned into a different URL.
                rest = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                                  if k != "token"])
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

    def _send_file(self, path: Path, content_type: str, extra_headers=()):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers:
            self.send_header(name, value)
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
            # /session?room=#18 (&project=…), ?room=ED-18 or ?task=18: the page
            # itself only knows room ids, so the number goes to the address.
            pairs = parse_qsl(u.query, keep_blank_values=True)
            q = dict(pairs)
            name = next((k for k in ("id", "room", "task")
                         if "no" in (task_numbers.parse_ref(q.get(k, "")) or {})), "")
            if name and not any(q.get(k, "").strip().startswith("room-") for k in ("id", "room")):
                rid, bad = http_task_id(q[name], q.get("project", ""))
                if bad:
                    body = f"{bad['message']}\n".encode("utf-8")
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                keep = [(k, v) for k, v in pairs if k not in ("id", "room", "task", "project")]
                where = "id" if name == "id" else "room"
                self.send_response(302)
                self.send_header("Location", "/session?" + urlencode([(where, rid)] + keep))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_file(STATIC_DIR / "session.html",
                            "text/html; charset=utf-8")
            return
        if p in ("/", "/index.html"):
            # The Update now button lives on this page; hand the browser the
            # key that /api/update asks for.
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8",
                            self._ui_key_headers())
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
                    ".jpeg": "image/jpeg", ".gif": "image/gif",
                    # The scripts the pages share (static/hl.js, comments.js).
                    ".js": "text/javascript; charset=utf-8"}.get(ext, "application/octet-stream")
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
                else:
                    turns = classify_turns(turns)
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
                turns = classify_turns(_claude_text_turns(tpath))
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
        if p == "/api/roadmap":
            pid = (parse_qs(u.query).get("project", [""])[0]).strip()
            proj = next((x for x in load_projects() if x["id"] == pid), None)
            if proj is None:
                self._send_json(404, {"error": "no_such_project"})
                return
            self._send_json(200, read_roadmap(proj))
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
            line = q.get("line", ["0"])[0]
            self._send_json(*read_workspace_file(path, int(line) if line.isdigit() else 0))
            return
        if p == "/api/ws/files":
            # Every file of a Workspace folder, for Go to file.
            q = parse_qs(u.query)
            self._send_json(*ws_files((q.get("root", [""])[0] or "").strip()))
            return
        if p == "/api/ws/search":
            # Search the text of a Workspace folder's files.
            q = parse_qs(u.query)
            n = q.get("n", ["0"])[0]
            if q.get("cancel", [""])[0] == "1":
                self._send_json(*ws_search_cancel((q.get("tag", [""])[0] or "").strip(), int(n) if n.isdigit() else 0))
                return
            self._send_json(*ws_search((q.get("root", [""])[0] or "").strip(), q.get("q", [""])[0] or "",
                                       q.get("case", [""])[0] == "1", q.get("regex", [""])[0] == "1",
                                       (q.get("tag", [""])[0] or "").strip(), int(n) if n.isdigit() else 0))
            return
        if p == "/api/git/status":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            self._send_json(*git_status(path, q.get("branch", [""])[0] == "1"))
            return
        if p == "/api/git/diff":
            q = parse_qs(u.query)
            path = (q.get("path", [""])[0] or "").strip()
            fpath = (q.get("file", [""])[0] or "").strip()
            self._send_json(*git_diff(path, fpath, q.get("branch", [""])[0] == "1"))
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
        if p.startswith("/api/history/"):
            self._history_get(p, parse_qs(u.query))
            return
        if p == "/api/rooms":
            rooms = [_annotate_room_liveness(r) for r in chatroom.list_rooms()]
            self._send_json(200, rooms)
            return
        if p == "/api/room":
            q = parse_qs(u.query)
            # ?id=room-… or a number: ?id=#18 / ?task=18 (&project=…), ?id=ED-18.
            rid, bad = http_task_id((q.get("id", [""])[0] or q.get("task", [""])[0]).strip(),
                                    q.get("project", [""])[0])
            if bad:
                self._send_json(404, bad)
                return
            room = chatroom.get_room(rid) if rid else None
            if room is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            self._send_json(200, _annotate_room_liveness(room))
            return
        if p == "/api/task/ref":
            # The task a number in a chat names, for the chip that shows it:
            # ?ref=#18 read in the project of ?room= (or ?project=), ?ref=ED-18
            # in any. 404 with a sentence when it names none.
            q = parse_qs(u.query)
            ctx_room = (q.get("room", [""])[0] or "").strip()
            pid = (q.get("project", [""])[0] or "").strip()
            if ctx_room and not pid:
                rm = chatroom.get_room(ctx_room)
                pid = _task_project(rm, load_session_projects(), load_projects()) if rm else ""
            rid, why = resolve_task_ref(q.get("ref", [""])[0], pid)
            info = task_ref_info(rid) if rid else None
            if info is None:
                self._send_json(404, {"error": "no_such_task", "message": why or "no such task"})
                return
            info.pop("report", None)
            self._send_json(200, info)
            return
        if p == "/api/room/msg":
            # The message a balloon link points at, for the chip that shows it.
            q = parse_qs(u.query)
            ref = resolve_message_ref(q.get("room", [""])[0], q.get("msg", [""])[0])
            if ref is None:
                self._send_json(404, {"error": "not_found"})
                return
            text = ref["text"]
            ref = {**ref, "text": text[:message_refs.QUOTE_MAX],
                   "more": max(0, len(text) - message_refs.QUOTE_MAX)}
            self._send_json(200, ref)
            return
        if p == "/api/attention":
            # "What needs me, and why" — one item per task. Deliberately cheap
            # (rooms + live-session files + the in-memory PTY registry, all
            # cached) so the page can poll it every few seconds; it shares
            # nothing with /api/sessions or /api/projects, which are slow.
            self._send_json(200, attention.snapshot())
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
            # The chat uses these colours only when the owner switched the
            # folder's scheme on for it; otherwise it follows the global theme.
            override = bool(cwd) and _cwd_key(cwd) in load_chat_scheme_cwds()
            self._send_json(200, {"theme": name, "colors": colors, "override": override})
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
        if p == "/api/rotation/status":
            self._send_json(200, rotation.status())
            return
        if p == "/api/digest/status":
            self._send_json(200, digest.status())
            return
        if p == "/api/usage":
            # Cached plan allowance, both agent kinds. A dict copy and nothing
            # else — the HTTPS call and the rollout scan happen on usage.py's
            # background thread, so this answers instantly and the page's poll
            # never waits on the network.
            self._send_json(200, usage.snapshot())
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
        if p == "/api/archived":
            self._send_json(200, sorted(load_archived()))
            return
        if p == "/api/search":
            q_params = parse_qs(u.query)
            query = (q_params.get("q", [""])[0] or "").strip()
            self._send_json(200, attach_search_rooms(search_transcripts(query)) if query else [])
            return
        if p.startswith("/api/repos/"):
            sid = p[len("/api/repos/"):]
            self._send_json(200, find_repos_for_session(sid))
            return
        self.send_error(404)

    # Any write can change what the task list shows — a rename, a priority,
    # a new task, a chat message — so the cached listing is dropped the
    # moment the write finishes.
    def do_PUT(self):
        try:
            self._do_PUT()
        finally:
            invalidate_session_listing()

    def _do_PUT(self):
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

    # Any write can change what the task list shows — a rename, a priority,
    # a new task, a chat message — so the cached listing is dropped the
    # moment the write finishes.
    def do_DELETE(self):
        try:
            self._do_DELETE()
        finally:
            invalidate_session_listing()

    def _do_DELETE(self):
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

    def _ring_recipients(self, room_id: str, result: dict) -> list[str]:
        """Ring each agent recipient's terminal (the keystroke doorbell) so it
        wakes to read the freshly posted message. Recipients are already
        loop-guard filtered by chatroom.post_message (empty when paused/waiting
        on the human). Returns the recipients that could NOT be woken: a
        stopped agent (the message is in the room, but nobody read it)."""
        recipients = (result or {}).get("recipients") or []
        if not recipients:
            return []
        msg = result.get("message") or {}
        room = chatroom.get_room(room_id) or {}
        # A reviewer on mention that isn't mid-review is not rung — it is not
        # running. The message starts a fresh reviewer briefed with it.
        rest = []
        for ident in recipients:
            part = chatroom.participant(room, ident) or {}
            if chatroom.is_on_mention(room, part) and not _pty_alive(part.get("ptyId")):
                try:
                    self._start_review(room_id, ident, msg)
                except Exception as e:      # a failed launch must not fail the send
                    print(f"[review] could not start {ident} in {room_id}: {e}", flush=True)
            else:
                rest.append(ident)
        if not rest:
            return []
        rung = self._ring(room_id, rest, _relay_wake(msg.get("from", "your partner")))
        return [ident for ident in rest if ident not in rung]

    def _start_review(self, room_id: str, ident: str, msg: dict) -> dict | None:
        """Start a fresh reviewer session for one request: its first prompt is
        the review brief (the question, the branch and its diff, the spec and
        REVIEW-LOG.md), it runs in the checkout under review, and it ends when
        it calls review_done. Returns the review record, or None when it was
        already running (the caller then rings it like anyone else)."""
        with _REVIEW_LAUNCH_LOCK:
            room_full = chatroom.get_room(room_id, public=False)
            part = chatroom.participant(room_full or {}, ident)
            if part is None or _pty_alive(part.get("ptyId")):
                return None
            room_full, part, review_allocation = apply_review_allocation(room_full, ident)
            log_text = read_review_log(room_full)
            n = review_count(log_text) + 1
            root = review_repo(room_full)
            git = review_git_context(root)
            brief = review_brief(room_full, part, msg, n, git, log_text)
            info = self._launch_room_agent_pty(room_full, part, "", collab=True,
                                               prompt=brief, cwd=root or None)
            review = {"n": n, "askedBy": msg.get("from", ""), "messageId": msg.get("id", ""),
                      "question": (msg.get("text") or "")[:1000], "startedAt": time.time(),
                      "ptyId": info["ptyId"], "sessionId": info["sessionId"],
                      "repo": root, "branch": git.get("branch", ""),
                      "head": git.get("head", ""), "base": git.get("base", ""),
                      "allocation": review_allocation}
            chatroom.patch_participant(
                room_id, ident,
                {"ptyId": info["ptyId"], "sessionId": info["sessionId"],
                 "cwd": info["cwd"], "pid": None, "review": review},
                drop=("lastExit", "fresh"))
            print(f"[review] started review {n} by {ident} in {room_id} "
                  f"(pty {info['ptyId']}, asked by {review['askedBy']})", flush=True)
            return review

    def _ring(self, room_id: str, idents: list, wake: str) -> list[str]:
        """Type ``wake`` into each named agent's terminal and submit it — the
        one thing that wakes an agent, and every wake re-sends its whole
        conversation, so callers ring as few as they can. Returns the
        identities actually rung (a stopped agent cannot be)."""
        room = chatroom.get_room(room_id)
        if not room:
            return []
        rung = []
        for ident in idents:
            # One step under the rotation's gate: a wake for an agent whose old
            # session is being ended is held for its fresh one (it would start
            # a turn that is then killed), and the terminal is read afresh, so
            # it is never one a rotation has just replaced.
            with rotation.GATE:
                if rotation.hold_wake(room_id, ident, wake):
                    rung.append(ident)
                    continue
                part = chatroom.participant(chatroom.get_room(room_id) or room, ident)
                if not part:
                    continue
                # Headless PTY session → the doorbell is a PTY write.
                pty_id = part.get("ptyId")
                if pty_id:
                    sess = ptyrun.get(pty_id)
                    if sess and sess.alive() and _type_input(sess, wake):
                        rung.append(ident)     # typed + discrete Enter, and it took
                    continue
            # Legacy visible-terminal session → keystroke injection.
            pid = self._resolve_live_pid(part)
            if pid:
                try:
                    BACKEND.send_text(int(pid), wake, submit=True)
                    rung.append(ident)
                except (OSError, ValueError):
                    pass
        return rung

    def _ring_report(self, po_room_id: str, result: dict, task_id: str,
                     task_title: str, reporter: str, kind: str, text: str) -> list[str]:
        """Wake a PO with a task's report. The doorbell carries the report
        itself, on one line — a PO that is a one-agent task has no chat_read
        to fetch it with, and a multi-line paste lands unsubmitted in a TUI."""
        recipients = (result or {}).get("recipients") or []
        if not recipients:
            return []
        flat = " ".join((text or "").split())
        if len(flat) > 700:
            flat = flat[:700] + "…"
        # The task by its number, which the PO resolves in its own project.
        name = task_label(chatroom.get_room(task_id)) or task_id
        wake = (f"[report] {kind} from task '{task_title}' ({name}, {reporter}): "
                f"{flat} — read it in full with ensemble_get_task taskId={name} messages=0.")
        return self._ring(po_room_id, recipients, wake)

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
        rtk_args, rtk_env, rtk_brief = _rtk_task_wiring(room_full, agent_key)
        briefing = collab_briefing(ident, part.get("role", ""), teammates, task)
        if rtk_brief:
            briefing += "\n\n" + rtk_brief
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
                                   env={"CHAT_TOKEN": token, **rtk_env})
            return {"sessionId": "", "cwd": cwd, "launch": res}
        # claude (and claude-N)
        cfg = {"mcpServers": {"ensemble": {"type": "http", "url": url,
                                       "headers": {"Authorization": f"Bearer {token}"}}}}
        mcp_dir = DASHBOARD_DIR / "_mcp"
        mcp_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = mcp_dir / f"{uuid.uuid4().hex}.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        new_sid = str(uuid.uuid4())
        claude_extra = ["--mcp-config", str(cfg_path), "--strict-mcp-config", *rtk_args]
        if model:
            claude_extra += ["--model", model]
        res = BACKEND.open_new(cwd, briefing, label=label, session_id=new_sid,
                               agent="claude", identity=ident,
                               extra_args=claude_extra, env=rtk_env)
        return {"sessionId": new_sid, "cwd": cwd, "launch": res}

    def _mcp_wiring(self, token: str, collab: bool,
                    human: bool = False) -> tuple[list[str], list[str], dict]:
        """The per-agent bits that connect it to the Ensemble MCP server
        (chat + ensemble_* task tools) with its own bearer token. EVERY headless
        agent gets the server — solo tasks included, so an agent can plan and
        manage tasks. Returns (codex_args, claude_args, env).

        Codex bypasses its approval prompts (incl. MCP tool approval) and
        sandbox, whatever the number of agents: a task runs headless and nobody
        watches its terminal, so a prompt there freezes it for good. (Claude
        gets the same from ``claude_cmd_args``' bypassPermissions mode.)
        ``human`` keeps Codex's prompts: only for a past session a person
        opened from the history to drive it themselves (/api/session/adopt).

        collab: an autonomous collaboration sees only our MCP server."""
        url = self._mcp_url()
        codex_args = ["-c", f'mcp_servers.ensemble.url="{url}"',
                      "-c", 'mcp_servers.ensemble.bearer_token_env_var="CHAT_TOKEN"']
        if not human:
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
                               collab: bool = True, prompt: str | None = None,
                               cwd: str | None = None) -> dict:
        """Headless variant: spawn the agent in a dashboard-owned PTY (no
        terminal window). Returns {ptyId, cwd, sessionId}. The PtySession owns
        liveness; the doorbell is a PTY write.

        collab=True: a collaboration briefing (roles, teammates, chat protocol)
        and, for codex, approval bypass for autonomy. collab=False (solo): a
        headless agent the human drives directly through the embedded
        terminal — the task is simply the first message. Both get the Ensemble
        MCP server (task tools always; chat tools only in a collaboration).

        ``prompt`` replaces the briefing outright and ``cwd`` the working dir —
        how a reviewer on mention is started with its review brief, in the
        checkout it reviews."""
        ident = part["identity"]
        agent_key = part["agent"]
        model = (part.get("model") or "").strip()
        token = next((t for t, i in room_full.get("tokens", {}).items()
                      if i == ident), "")
        base = room_full.get("cwd") or str(CS_ROOT)
        # Project-backed sessions share one workspace (agents work on the same
        # files); ad-hoc scratch collaborations keep per-identity subdirs.
        if not cwd:
            cwd = base if room_full.get("sharedCwd") else os.path.join(base, ident)
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass
        rtk_args, rtk_env, rtk_brief = _rtk_task_wiring(room_full, agent_key)
        if prompt is not None:
            briefing = prompt
        elif collab:
            teammates = [{"identity": p["identity"], "role": p.get("role", "")}
                         for p in room_full["participants"]
                         if p.get("kind") == "agent" and p["identity"] != ident]
            briefing = collab_briefing(ident, part.get("role", ""), teammates, task)
        else:
            # solo: the task is just the first prompt, plus how to report —
            # a one-agent task has no chat, so without the tool its finished
            # work is only visible to whoever is watching this terminal.
            briefing = task + SOLO_REPORT_NOTE
        if rtk_brief:
            briefing += "\n\n" + rtk_brief
        ag = agents.get_agent(agent_key)
        if hasattr(ag, "ensure_trusted"):
            ag.ensure_trusted(cwd)
        label = f"{room_full['title'][:40]} · {ident}"
        meta = {"room": room_full["id"], "identity": ident, "agent": agent_key}
        codex_mcp, claude_mcp, env = self._mcp_wiring(token, collab)
        env.update(rtk_env)
        if agent_key == "codex":
            argv = ["codex", "-c", "check_for_update_on_startup=false"] + codex_mcp
            if model:
                argv += ["-c", f'model="{model}"']
            cmd = BACKEND.headless_launch(cwd, argv, briefing)
            sess = ptyrun.create(cmd, cwd=cwd, env=env, label=label, meta=meta)
            return {"ptyId": sess.id, "cwd": cwd, "sessionId": ""}
        # claude (and claude-N)
        new_sid = str(uuid.uuid4())
        argv = claude_cmd_args("--session-id", new_sid, *claude_mcp, *rtk_args)
        if model:
            argv += ["--model", model]
        cmd = BACKEND.headless_launch(cwd, argv, briefing)
        sess = ptyrun.create(cmd, cwd=cwd, env=env, label=label, meta=meta)
        return {"ptyId": sess.id, "cwd": cwd, "sessionId": new_sid}

    def _resume_room_agent_pty(self, room_full: dict, part: dict,
                               collab: bool = True, seed: str = "",
                               human: bool = False) -> dict:
        """Relaunch an agent in a fresh PTY, RESUMING its prior conversation
        (claude --resume / codex resume). Used to recover a session after a
        dashboard restart killed its PTY. Returns {ptyId, cwd, sessionId,
        prompted} — ``prompted`` when there was nothing to resume and it was
        started with ``seed`` as its first prompt instead."""
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
        codex_mcp, claude_mcp, env = self._mcp_wiring(token, collab, human=human)
        rtk_args, rtk_env, _ = _rtk_task_wiring(room_full, agent_key)
        env.update(rtk_env)
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
            return {"ptyId": sess.id, "cwd": cwd, "sessionId": part.get("sessionId", ""),
                    "prompted": bool(seed and not codex_sid)}
        # claude
        sid = part.get("sessionId", "")
        resume = ["--resume", sid] if sid else []
        argv = claude_cmd_args(*resume, *claude_mcp, *rtk_args)
        if model:
            argv += ["--model", model]
        cmd = BACKEND.headless_launch(cwd, argv, "")
        sess = ptyrun.create(cmd, cwd=cwd, env=env, label=label, meta=meta)
        return {"ptyId": sess.id, "cwd": cwd, "sessionId": sid, "prompted": False}

    def _start_room(self, room_full: dict) -> list[dict]:
        """First launch of a task's agents (a fresh conversation seeded with the
        spec / collaboration briefing). Marks the room launched. Returns
        [{identity, ptyId}]."""
        apply_first_launch_allocation(room_full)
        task = room_full.get("spec", "") or ""
        collab = room_full.get("mode") != "solo"
        launched = []
        for part in [pp for pp in room_full["participants"] if pp.get("kind") == "agent"]:
            part.pop("fresh", None)     # launching IS its first conversation
            if chatroom.is_on_mention(room_full, part):
                continue                # started per request (_start_review)
            info = self._launch_room_agent_pty(room_full, part, task, collab=collab)
            part["sessionId"] = info["sessionId"]
            part["cwd"] = info["cwd"]
            part["ptyId"] = info["ptyId"]
            part.pop("lastExit", None)   # a fresh agent isn't the dead one
            launched.append({"identity": part["identity"], "agent": part.get("agent", ""),
                             "model": part.get("model", ""), "role": part.get("role", ""),
                             "ptyId": info["ptyId"]})
        room_full["launched"] = True
        room_full["status"] = "active"
        room_full["hopCount"] = 0
        room_full["waitingFor"] = ""
        # Pin the column on launch rather than on the owner's first drag:
        # correctness must not depend on a gesture nobody has a reason to make.
        room_full.setdefault("workflow", "inprogress")
        chatroom.update_room(room_full)
        return launched

    def _start_or_resume_room(self, room_full: dict) -> list[dict]:
        """Bring a not-running task up: a draft (never launched) starts fresh,
        anything else relaunches its agents resuming their prior conversations.
        Returns [{identity, ptyId}]."""
        agents_in = [pp for pp in room_full.get("participants", [])
                     if pp.get("kind") == "agent"]
        solo = room_full.get("mode") == "solo" or len(agents_in) < 2
        if not room_full.get("launched", True):
            launched = self._start_room(room_full)
            # A first launch gets its spec as its first prompt; anything said
            # to it meanwhile is typed once its screen has settled.
            self._deliver_after_resume(
                room_full["id"], [(x["identity"], x["ptyId"], False) for x in launched], solo)
            return launched
        # A solo task whose agent never actually got going (no messages yet)
        # is (re)started WITH its specification as the first prompt —
        # otherwise "Open" would bring up a blank agent that idles.
        seed = (room_full.get("spec") or "") if (solo and not room_full.get("messages")) else ""
        # Who is told to carry on: an owner whose conversation was resumed. Not
        # a project's PO (the rotation and the restart helper brief it), not a
        # reviewer, not an agent given a first prompt just now.
        is_po = any((p.get("poRoomId") or "") == room_full["id"] for p in load_projects())
        own = set(chatroom.owners(room_full))
        notify = []
        resumed = []
        # An agent still running (a partner died, a retry after a failed
        # delivery) is never launched a second time: it stays as it is, and
        # what is held for it is typed once its screen is settled.
        running = [p["identity"] for p in agents_in if _pty_alive(p.get("ptyId"))]
        for part in agents_in:
            if part["identity"] in running:
                resumed.append({"identity": part["identity"], "ptyId": part["ptyId"]})
                continue
            part.pop("resumedAt", None)     # set again once the note is typed
            if chatroom.is_on_mention(room_full, part):
                # Never resumed: a reviewer is started fresh for each request,
                # and resuming would reload the very history this avoids.
                part.pop("fresh", None)
                continue
            if part.pop("fresh", False):
                # Assigned to the task after it had already run: there is no
                # conversation to resume, so give it the same first prompt a
                # launch would have (the collaboration briefing, or the spec).
                info = self._launch_room_agent_pty(
                    room_full, part, room_full.get("spec", "") or "", collab=not solo)
                part["sessionId"] = info["sessionId"]
            else:
                info = self._resume_room_agent_pty(room_full, part, collab=not solo, seed=seed)
                if part["identity"] in own and not is_po and not info.get("prompted"):
                    notify.append((part["identity"], info["ptyId"]))
            part["ptyId"] = info["ptyId"]
            part["cwd"] = info["cwd"]
            # Drop any recorded death: this agent is running again, and an
            # alert about the previous run is one nobody could ever dismiss.
            part.pop("lastExit", None)
            resumed.append({"identity": part["identity"], "ptyId": info["ptyId"]})
        room_full["status"] = "active"
        room_full["hopCount"] = 0
        room_full["waitingFor"] = ""
        if running:
            # A running agent may post meanwhile: write only what changed,
            # under the room lock, so a message of its is never overwritten.
            rid = room_full["id"]
            for part in agents_in:
                if part["identity"] in running:
                    continue
                keep = {k: part[k] for k in ("ptyId", "cwd", "sessionId") if k in part}
                gone = tuple(k for k in ("lastExit", "fresh", "resumedAt") if k not in part)
                chatroom.patch_participant(rid, part["identity"], keep, drop=gone)
            chatroom.patch_room(rid, status="active", hopCount=0, waitingFor="")
        else:
            chatroom.update_room(room_full)
        self._deliver_after_resume(
            room_full["id"],
            [(r["identity"], r["ptyId"], (r["identity"], r["ptyId"]) in notify) for r in resumed],
            solo)
        return resumed

    # ---- Sending to a stopped session: it is resumed, then the message is
    # typed in as its first input. The hub holds the message (a closed tab or
    # a phone losing its connection mid-resume drops nothing), one resume per
    # room at a time (two browsers cannot start two agents), and everything
    # said while the agents come up is delivered in order with the resume note
    # as ONE input: an agent is woken once, never twice.

    def _resume_room(self, room_full: dict, text: str = "", to: str = "",
                     key: str = "") -> dict:
        """Resume a room, delivering ``text`` (if any) once it is up — or, when
        it is already running, deliver right away. What a failed resume still
        holds comes along with ANY new attempt (a fresh text, a plain Resume,
        Retry), in order; only Discard drops it. A text whose ``key`` is
        already held (the same send again, after a refusal or a lost reply)
        is not queued twice: the attempt is its retry. Returns {resumed,
        queued, delivered}. Raises StartRoomError when the hub refuses; the
        message is then kept for a retry."""
        rid = room_full["id"]
        now = time.time()
        items = [{"text": text, "to": to, "at": now, "key": key}] if text else []
        start = direct = carried = False
        with _RESUMES_LOCK:
            res = _RESUMES.get(rid)
            if res is not None and res.state == "failed":
                _RESUMES.pop(rid, None)
                held_keys = {it.get("key") for it in res.queue if it.get("key")}
                items = res.queue + [it for it in items if it["key"] not in held_keys]
                carried = bool(res.queue)
                res = None
            elif res is not None and key and any(it.get("key") == key for it in res.queue):
                items = []      # the same send again: already on its way in
            if res is None:
                # A held message is never typed straight in: it failed on a
                # prompt, or on an agent that stopped, so it goes the settled
                # way again (a stopped partner is brought back for it).
                if not carried and _room_is_live(room_full):
                    direct = True
                else:
                    res = _RESUMES[rid] = _Resume()
                    start = True
            if res is not None:
                res.queue.extend(items)
        if direct:
            left = self._deliver_now(room_full, items) if items else []
            if left:
                # It stopped between the check and the write: resume it after all.
                return self._resume_after_all(room_full, left)
            return {"resumed": [], "queued": 0, "delivered": len(items)}
        if start:
            try:
                room_full = chatroom.get_room(rid, public=False) or room_full
                resumed = self._start_or_resume_room(room_full)
            except Exception as exc:    # noqa: BLE001 — a refusal or a failed spawn alike
                with _RESUMES_LOCK:
                    if _RESUMES.get(rid) is res:
                        if res.queue:
                            res.fail(str(exc) or exc.__class__.__name__)  # kept for Retry
                        else:
                            _RESUMES.pop(rid, None)
                raise
            with _RESUMES_LOCK:
                res.resumed = resumed
            return {"resumed": resumed, "queued": len(items), "delivered": 0}
        return {"resumed": list(res.resumed), "queued": len(items), "delivered": 0,
                "inFlight": True}

    def _resume_after_all(self, room_full: dict, items: list[dict]) -> dict:
        """The room read as live but an agent was gone by the time the message
        reached it: queue what is left and resume the room for it."""
        rid = room_full["id"]
        room_full = chatroom.get_room(rid, public=False) or room_full
        with _RESUMES_LOCK:
            res = _RESUMES.get(rid)
            if res is not None and res.state != "failed":
                res.queue.extend(items)
                return {"resumed": list(res.resumed), "queued": len(items), "delivered": 0,
                        "inFlight": True}
            held = res.queue if res is not None else []
            _RESUMES.pop(rid, None)
            res = _RESUMES[rid] = _Resume()
            res.queue.extend(held + items)
        try:
            resumed = self._start_or_resume_room(room_full)
        except Exception as exc:    # noqa: BLE001
            with _RESUMES_LOCK:
                if _RESUMES.get(rid) is res:
                    res.fail(str(exc) or exc.__class__.__name__)
            raise
        with _RESUMES_LOCK:
            res.resumed = resumed
        return {"resumed": resumed, "queued": len(items), "delivered": 0}

    def _deliver_now(self, room_full: dict, items: list[dict]) -> list[dict]:
        """Deliver messages to a RUNNING room: a solo agent gets them typed
        into its terminal as one input, a team gets them posted and its
        recipients rung. Returns what could not be delivered — everything,
        when the solo terminal turned out to be gone; for a team, from the
        first message whose recipient could not be woken (that one is in the
        room already, marked ``posted``: only its wake is still owed)."""
        rid = room_full["id"]
        agents_in = [pp for pp in room_full.get("participants", []) if pp.get("kind") == "agent"]
        solo = room_full.get("mode") == "solo" or len(agents_in) < 2
        if not solo:
            for i, it in enumerate(items):
                result = chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, it["text"], to=it["to"])
                if result is None:
                    continue
                missed = self._ring_recipients(rid, result)
                if missed:
                    it["posted"] = True
                    it["wake"] = missed
                    return items[i:]
            return []
        sess = ptyrun.get((agents_in[0] if agents_in else {}).get("ptyId"))
        if sess is None or not sess.alive():
            return items
        with rotation.GATE:
            if rotation.room_rotating(rid):
                raise StartRoomError("handing over to a fresh session, try again shortly")
            sess.last_input = time.time()
            if not _type_input(sess, "\n\n".join(with_message_refs(it["text"], rid) for it in items)):
                return items     # it looked alive, but the write found it gone
        return []

    def _deliver_after_resume(self, room_id: str, targets: list[tuple], solo: bool) -> None:
        """Once the resumed agents' TUIs are up, type each one its first input:
        the resume note (for an owner told to carry on) and, in the same input,
        whatever was sent to the room while it was stopped or coming up —
        typed into a solo agent, posted and relayed to a team. An agent is
        settled when it has drawn its screen and then been quiet for
        rotation.IDLE_S, the quiet the PO rotation waits for (a line typed
        during start-up can be lost; a turn repaints its timer every second, so
        quiet also means not working). Not on top of a prompt. In the
        background, so the start returns at once; the time a note was typed
        goes on the participant, which is how attention knows the agent was
        asked to carry on. ``targets`` is [(identity, ptyId, note)]."""
        with _RESUMES_LOCK:
            res = _RESUMES.get(room_id)
            if res is None:
                res = _RESUMES[room_id] = _Resume()
            res.targets = list(targets)

        def blocker(room: dict, ready: dict, dead: set, prompted: set, it: dict) -> str:
            """Why a message cannot go in now: one of the agents it wakes
            stopped, sits on a prompt, or is not running. Empty when every
            one of them is settled, running, or started fresh for it."""
            wants = (it.get("wake") or []) if it.get("posted") else chatroom.wake_targets(
                room, chatroom.HUMAN_IDENTITY, it.get("to", ""), it["text"])
            for ident in wants:
                if ident in ready:
                    continue
                name = "the agent" if solo else ident
                if ident in dead:
                    return f"{name} stopped before the message could be typed"
                if ident in prompted:
                    return (f"a prompt is on {name}'s screen: answer it in its terminal, "
                            f"then send again")
                part = chatroom.participant(room, ident)
                if part is None or chatroom.is_on_mention(room, part) or _pty_alive(part.get("ptyId")):
                    continue
                return f"{name} is not running"
            return ""

        def run():
            end = time.time() + RESUME_NOTE_WAIT_S
            waiting = {ident: pty_id for ident, pty_id, _note in targets}
            ready: dict[str, object] = {}
            dead: set[str] = set()
            prompted: set[str] = set()
            while waiting:
                time.sleep(1)
                for ident, pty_id in list(waiting.items()):
                    sess = ptyrun.get(pty_id)
                    if sess is None or not sess.alive():
                        waiting.pop(ident)          # nothing to type into
                        dead.add(ident)
                        continue
                    tail = sess.tail()
                    settled = bool(tail) and time.time() - sess.last_output >= rotation.IDLE_S
                    late = time.time() > end
                    if attention.looks_like_prompt(tail):
                        if late:
                            print(f"[resume] {room_id}/{ident}: a prompt is on screen — "
                                  f"nothing was typed", flush=True)
                            waiting.pop(ident)
                            prompted.add(ident)
                        continue
                    if settled or late:
                        waiting.pop(ident)
                        ready[ident] = sess
            notes = {ident for ident, _pty, note in targets if note}
            first = True
            while True:
                with _RESUMES_LOCK:
                    items, res.queue = res.queue, []
                    if not items and not first:
                        # Done, in the same step as the empty check: a message
                        # sent from here on finds no resume and goes straight
                        # into the running room, never onto a queue nobody drains.
                        if _RESUMES.get(room_id) is res:
                            _RESUMES.pop(room_id, None)
                        break
                # In order, up to the first message somebody cannot take now.
                room = chatroom.get_room(room_id) or {}
                go: list[dict] = []
                why = ""
                for it in items:
                    why = blocker(room, ready, dead, prompted, it)
                    if why:
                        break
                    go.append(it)
                if go or (first and notes and ready):
                    try:
                        self._type_after_resume(room_id, ready, notes if first else set(),
                                                go, solo)
                    except Exception as e:      # noqa: BLE001 — never lose the queue silently
                        why = str(e) if isinstance(e, _NotTyped) else f"could not deliver: {e}"
                        with _RESUMES_LOCK:
                            res.queue = items + res.queue
                            res.fail(why)
                        print(f"[resume] {room_id}: {len(items)} message(s) not delivered — {why}",
                              flush=True)
                        return
                if why:
                    held = items[len(go):]
                    with _RESUMES_LOCK:
                        res.queue = held + res.queue
                        res.fail(why)
                    print(f"[resume] {room_id}: {len(held)} message(s) not delivered — {why}",
                          flush=True)
                    return
                first = False
        threading.Thread(target=run, daemon=True, name=f"resume-deliver-{room_id}").start()

    def _type_after_resume(self, room_id: str, ready: dict, notes: set,
                           items: list[dict], solo: bool) -> None:
        """One input per settled agent: its resume note (if it gets one) and
        the messages — the texts themselves for a solo agent, the relay for a
        team, whose messages are posted to the room first, in order."""
        wake_for: dict[str, list[str]] = {}
        if not solo and items:
            for it in items:
                if not it.get("posted"):
                    result = chatroom.post_message(room_id, chatroom.HUMAN_IDENTITY,
                                                   it["text"], to=it["to"])
                    if result is None:
                        continue
                    msg = result.get("message") or {}
                    room = chatroom.get_room(room_id) or {}
                    owed = []
                    for ident in result.get("recipients") or []:
                        part = chatroom.participant(room, ident) or {}
                        if chatroom.is_on_mention(room, part) and not _pty_alive(part.get("ptyId")):
                            try:        # a reviewer is started fresh with the message
                                self._start_review(room_id, ident, msg)
                            except Exception as e:      # noqa: BLE001
                                print(f"[review] could not start {ident} in {room_id}: {e}", flush=True)
                            continue
                        owed.append(ident)
                    # In the room now: from here on only its wakes are owed, so
                    # a retry after a failed wake never posts it a second time.
                    it["posted"] = True
                    it["wake"] = owed
                for ident in it.get("wake") or []:
                    wake_for.setdefault(ident, []).append(chatroom.HUMAN_IDENTITY)
        not_typed: list[str] = []
        for ident, sess in ready.items():
            parts = [RESUME_NOTE] if ident in notes else []
            if solo:
                parts += [with_message_refs(it["text"], room_id) for it in items]
            elif ident in wake_for:
                parts.append(_relay_wake(wake_for.pop(ident)[-1]))
            if not parts:
                continue
            sess.last_input = time.time()
            if not _type_input(sess, "\n\n".join(parts)):
                # Gone between looking ready and the write: the messages stay
                # owed to it (a partner that got its wake is not woken again).
                print(f"[resume] {room_id}/{ident}: stopped before the input was typed "
                      f"(pty {sess.id})", flush=True)
                not_typed.append(ident)
                continue
            for it in items:
                if it.get("posted"):
                    it["wake"] = [w for w in it["wake"] if w != ident]
            if ident in notes:
                chatroom.patch_participant(room_id, ident, {"resumedAt": time.time()})
            what = ["the resume note"] if ident in notes else []
            if len(parts) > len(what):
                what.append(f"{len(items)} message(s)" if solo
                            else f"the relay for {len(items)} message(s)")
            print(f"[resume] {room_id}/{ident}: typed {' and '.join(what)} as one input "
                  f"(pty {sess.id})", flush=True)
        # Recipients that were not among the resumed (a live agent of another
        # kind): rung the ordinary way, which skips one that is not running.
        for ident, senders in wake_for.items():
            if ident in self._ring(room_id, [ident], _relay_wake(senders[-1])):
                for it in items:
                    if it.get("posted"):
                        it["wake"] = [w for w in it["wake"] if w != ident]
            else:
                not_typed.append(ident)
        if not_typed:
            name = "the agent" if solo else not_typed[0]
            raise _NotTyped(f"{name} stopped before the message could be typed")

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
            reviewer = chatroom._role_head(role) == chatroom.REVIEWER_ROLE
            has_reviewer = any(chatroom._role_head(t.get("role", "")) ==
                               chatroom.REVIEWER_ROLE for t in teammates)
            if reviewer:
                closing = ("Acknowledge your role in one line and wait for the engineer's "
                           "next deliverable before doing any work yourself.")
            elif has_reviewer:
                closing = (
                    f"{OWNER_OUTPUT_NOTE} Mention the reviewer only for a commit to review "
                    "or a specific question, never for a plan, acknowledgement, thanks, or "
                    "verdict restatement. Begin work without waking it.")
            else:
                closing = f"{OWNER_OUTPUT_NOTE} Pick up the collaboration with chat_send."
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

    # ---------- A documents project's files: upload, mkdir, move, delete ----------
    # A plain API, gated like the rest of the hub (the access token off loopback).
    # A browser sends Origin on every POST; a page on another site must not
    # reach these through the person's own browser. A program (curl, an agent)
    # sends no Origin and passes.

    def _files_cross_site(self) -> bool:
        if self.headers.get("Origin") and not self._same_origin_request():
            self._send_json(403, {"error": "cross_origin",
                                  "message": "The request came from another site."})
            return True
        return False

    def _files_upload(self, u) -> None:
        """POST /api/files/upload?project=&path=[&overwrite=1], the raw file as
        the body. Everything that can refuse it is checked before a byte of
        the body is read; the body goes to a temp file renamed into place."""
        if self._files_cross_site():
            return
        q = parse_qs(u.query, keep_blank_values=True)

        def arg(k: str) -> str:
            return (q.get(k) or [""])[0]
        raw = self.headers.get("Content-Length")
        ln = int(raw) if raw is not None and str(raw).strip().isdigit() else -1
        unread, tmp = max(ln, 0), ""
        try:
            if ln < 0:
                raise FileOpRefused(411, "length_required", "The upload did not say how large the file is.")
            if ln > file_history.MAX_FILE_BYTES:
                raise FileOpRefused(413, "too_large",
                                    f"The file is {ln / 1048576:.1f} MB; a file can be at most "
                                    f"{file_history.MAX_FILE_BYTES // 1048576} MB.")
            proj, home = files_target(arg("project"))
            rel, full = files_path(home, arg("path"))
            overwrite = arg("overwrite") == "1"
            files_upload_check(home, rel, full, overwrite)
            tmp = files_upload_temp(home, rel, full)
            try:
                with open(tmp, "wb") as fh:
                    while unread:
                        chunk = self.rfile.read(min(unread, _UPLOAD_CHUNK))
                        if not chunk:
                            break
                        fh.write(chunk)
                        unread -= len(chunk)
            except OSError as e:
                if unread and not isinstance(e, (ConnectionError, TimeoutError)):
                    raise _files_os_error(e, rel)
            if unread:
                raise FileOpRefused(400, "incomplete", f"“{rel}” did not arrive in full, so it was not saved. "
                                                       "Try again.")
            res = files_upload_commit(proj, home, rel, full, tmp, overwrite)
            tmp = ""
            self._send_json(200, res)
        except FileOpRefused as e:
            self._send_json(e.status, e.payload())
            self._files_drain(unread)
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _files_drain(self, n: int) -> None:
        """Read and drop what is left of a refused upload after the reply is
        sent, so the browser gets the reply rather than a reset connection.
        Past a limit, or when it stalls, the connection is simply closed."""
        self.close_connection = True
        if n <= 0 or n > _UPLOAD_DRAIN_MAX:
            return
        conn = getattr(self, "connection", None)
        try:
            if conn is not None:
                conn.settimeout(15)
            while n > 0:
                chunk = self.rfile.read(min(n, _UPLOAD_CHUNK))
                if not chunk:
                    break
                n -= len(chunk)
        except OSError:
            pass

    def _files_post(self, p: str, data) -> None:
        """POST /api/files/mkdir {project, path}, /api/files/move {project,
        from, to, overwrite}, /api/files/delete {project, path}."""
        if self._files_cross_site():
            return
        try:
            if not isinstance(data, dict):
                raise FileOpRefused(400, "bad_json", "The request was not understood.")
            op = p[len("/api/files/"):]
            if op not in ("mkdir", "move", "delete"):
                raise FileOpRefused(404, "not_found", "There is no such file operation.")
            proj, home = files_target(data.get("project"))
            if op == "mkdir":
                res = files_mkdir(proj, home, data.get("path"))
            elif op == "move":
                res = files_move(proj, home, data.get("from"), data.get("to"),
                                 data.get("overwrite") in (True, 1, "1", "true"))
            else:
                res = files_delete(proj, home, data.get("path"))
            self._send_json(200, res)
        except FileOpRefused as e:
            self._send_json(e.status, e.payload())

    def _history_get(self, p: str, q: dict) -> None:
        """A documents project's file history: its snapshots (or its deleted
        files), a version's content, a diff, and what changed since the last
        snapshot. Read-only, gated like the workspace file APIs."""
        def arg(k: str) -> str:
            v = q.get(k, [""])[0] or ""
            # A path is taken as written: " notes.txt" is a real file name.
            return v if k in ("path", "file", "now", "after", "through") else v.strip()

        def place(k: str):
            rev, _, path = arg(k).partition(":")      # "<rev>:<path>", a place in the deleted list
            return (rev, path) if rev and path else None

        def num(k: str, d: int) -> int:
            return int(arg(k)) if arg(k).isdigit() else d
        proj, home, bad = _history_target(arg("project"))
        if bad:
            self._send_json(*bad)
            return
        if p == "/api/history/log":
            if arg("deleted") == "1":
                self._send_json(200, {"projectId": proj["id"], "root": home,
                                      **file_history.deleted(home, num("limit", file_history.LOG_MAX),
                                                             place("after"), place("through"))})
                return
            path = ""
            if arg("path"):
                path = file_history.rel_path(home, arg("path"))
                if not path:
                    self._send_json(400, {"error": "bad_path"})
                    return
            self._send_json(200, {"projectId": proj["id"], "root": home, "path": path,
                                  "maxFileBytes": file_history.MAX_FILE_BYTES,
                                  **file_history.log(home, path, num("limit", 50), num("skip", 0))})
            return
        if p == "/api/history/status":
            self._send_json(200, file_history.status(home))
            return
        if p == "/api/history/diff":
            self._send_json(*file_history.diff(home, arg("rev"), arg("path") or arg("file"),
                                               "parent" if arg("against") == "parent" else "current",
                                               arg("now")))
            return
        if p == "/api/history/file":
            code, data = file_history.file_at(home, arg("rev"), arg("path"))
            if code != 200:
                self._send_json(code, {"error": data})
                return
            if arg("raw") == "1":
                self._send_history_raw(arg("path"), data)
                return
            head = data[:_TEXT_MAX]
            binary = b"\x00" in head
            self._send_json(200, {"path": file_history.rel_path(home, arg("path")), "binary": binary,
                                  "text": "" if binary else head.decode("utf-8", errors="replace"),
                                  "size": len(data), "truncated": len(data) > _TEXT_MAX})
            return
        self._send_json(404, {"error": "not_found"})

    # An old version the browser may show itself; anything else downloads.
    # Never HTML or SVG: served from the hub, it would run as one of its pages.
    _HISTORY_INLINE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                       ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
                       ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
                       ".csv": "text/plain; charset=utf-8", ".json": "text/plain; charset=utf-8",
                       ".log": "text/plain; charset=utf-8"}

    def _send_history_raw(self, path: str, data: bytes) -> None:
        name = os.path.basename(path.replace("\\", "/")) or "file"
        ctype = self._HISTORY_INLINE.get(os.path.splitext(name)[1].lower())
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition",
                         f"{'inline' if ctype else 'attachment'}; filename*=UTF-8''{quote(name)}")
        self.end_headers()
        self.wfile.write(data)

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
            # Read/report tools are common; board administration belongs to a
            # project's PO and explicitly delegated planners. Chat tools only
            # make sense in a collaboration.
            room = chatroom.get_room(room_id)
            tools = ensemble_tools.tool_schemas(room or {}, identity)
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
            if name == "ensemble_report" and not is_err:
                _history_nudge(room_id, "report")
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
            _history_nudge(room_id, "turn")
            status = result["status"]
            note = "delivered"
            if status == "waiting_human":
                note = ("delivered to the human — the collaboration is paused "
                        "until they reply, so stop and wait.")
            elif status == "paused":
                note = ("delivered, but the room reached its turn limit and is "
                        "paused for human review — stop and wait.")
            if to.lower() in chatroom.BROADCAST and not result["recipients"]:
                note += (" No teammate was woken: a message to everyone wakes only "
                         "the task's owner. To wake a reviewer or another "
                         "specialist, address it with `to` or @mention it.")
            return ok({"content": [{"type": "text", "text": note}],
                       "isError": False})
        if name == "chat_read":
            msgs = chatroom.read_new_for(room_id, identity)
            if not msgs:
                body = "(no new messages)"
            else:
                # What the person or the PO sent comes with its balloon links
                # written out; the room keeps the words as they were sent.
                room = chatroom.get_room(room_id) or {}
                body = "\n".join(
                    f"[from {m['from']}] "
                    + (with_message_refs(m["text"], room_id) if refs_expanded_for(room, m["from"]) else m["text"])
                    for m in msgs)
            return ok({"content": [{"type": "text", "text": body}],
                       "isError": False})
        if name == "chat_whoami":
            info = chatroom.whoami(room_id, identity)
            return ok({"content": [{"type": "text", "text": json.dumps(info)}],
                       "isError": False})
        return err(-32602, f"unknown tool: {name}")


    # Any write can change what the task list shows — a rename, a priority,
    # a new task, a chat message — so the cached listing is dropped the
    # moment the write finishes.
    def do_POST(self):
        try:
            self._do_POST()
        finally:
            invalidate_session_listing()

    def _do_POST(self):
        if not self._gate():
            return
        u = urlparse(self.path)
        p = u.path
        if p == "/api/files/upload":
            # Its body is the file itself: read in pieces, never as JSON.
            self._files_upload(u)
            return
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
        if p.startswith("/api/files/"):
            self._files_post(p, data)
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
            if not sess.alive():
                # Still listed until it is reaped, but the write would vanish:
                # say so, or the page counts a message as sent that never was.
                self._send_json(410, {"error": "the session has stopped"})
                return
            # One step with a rotation's mark (rotation.GATE): input to a task
            # being handed over is refused rather than reach the session being
            # ended, and input before it is seen by the rotation's last check.
            with rotation.GATE:
                if rotation.room_rotating((sess.meta or {}).get("room", "")):
                    self._send_json(409, {"error": "handing over to a fresh session, "
                                                   "try again shortly"})
                    return
                sess.last_input = time.time()
                if not sess.write(data.get("data", "")):
                    # It ended after the check above: the input went nowhere.
                    self._send_json(410, {"error": "the session has stopped"})
                    return
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
            # Only the Ensemble Dashboard PO or the dashboard page — even from
            # this machine, where the gate lets everything through.
            refusal = self._restart_refusal()
            if refusal:
                self._send_json(403, {"error": "not_allowed", "message": refusal})
                return
            # Fire and forget — the spawned `ensemble update`
            # restarts the server via launchctl kickstart -k. Send the 202
            # before that SIGKILL arrives.
            result = trigger_update()
            self._send_json(202 if result.get("started") else 500, result)
            return
        if p == "/api/restart":
            # The same lock; stops and starts the hub on the code on disk.
            refusal = self._restart_refusal()
            if refusal:
                self._send_json(403, {"error": "not_allowed", "message": refusal})
                return
            resolved = chatroom.resolve_token(self._bearer_token()) if self._bearer_token() else None
            result = trigger_restart(resolved[0] if resolved else "")
            self._send_json(result.pop("status"), result)
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
            payload = {"ok": True, "project": proj}
            if (data.get("kind") or "").strip().lower() == "documents":
                kok, kmsg = set_project_kind(proj["id"], "documents")
                if kok:
                    proj["kind"] = "documents"
                else:
                    payload["kindRefused"] = KIND_REFUSALS.get(kmsg, kmsg)
            self._send_json(200, payload)
            return
        if p == "/api/projects/assign":
            sid = (data.get("sessionId") or data.get("roomId") or "").strip()
            pid = (data.get("projectId") or "").strip()
            if not sid:
                self._send_json(400, {"error": "missing_session"})
                return
            assign_session_project(sid, pid)
            if pid and sid.startswith("room-"):
                # A task moved in takes the project's next number.
                assign_task_number(sid, pid)
            self._send_json(200, {"ok": True})
            return
        if p == "/api/projects/po":
            # {projectId, roomId}: name the task that is the project's PO
            # (roomId "" clears it). Every other task reports into it.
            ok, msg = set_project_po(data.get("projectId", ""), data.get("roomId", ""))
            code = {"no_such_project": 404, "no_such_room": 404}.get(msg, 400)
            self._send_json(200 if ok else code, {"ok": ok} if ok else {"error": msg})
            return
        if p == "/api/projects/digest":
            # {projectId, intervalMin}: the project's own progress-digest
            # interval in minutes (0 = off, null = the hub default).
            ok, msg = set_project_digest_interval(data.get("projectId", ""),
                                                  data.get("intervalMin"))
            self._send_json(200 if ok else (404 if msg == "no_such_project" else 400),
                            {"ok": ok} if ok else {"error": msg})
            return
        if p == "/api/projects/key":
            # {projectId, key}: the project's short key (ED), for ED-18.
            ok, msg = set_project_key(data.get("projectId", ""), data.get("key", ""))
            if ok:
                self._send_json(200, {"ok": True, "key": task_numbers.normalize_key(data.get("key"))})
            else:
                self._send_json(404 if msg == "no_such_project" else 400,
                                {"error": msg, "message": KEY_REFUSALS.get(msg, msg)})
            return
        if p == "/api/projects/kind":
            # {projectId, kind: "code" | "documents"}.
            ok, msg = set_project_kind(data.get("projectId", ""), data.get("kind", ""))
            if ok:
                self._send_json(200, {"ok": True, "kind": (data.get("kind") or "").strip().lower()})
            else:
                self._send_json(404 if msg == "no_such_project" else 400,
                                {"error": msg, "message": KIND_REFUSALS.get(msg, msg)})
            return
        if p == "/api/history/snapshot":
            # {projectId}: Snapshot now. The history's own thread takes it
            # within a moment; the page reads the log again after.
            proj, home, bad = _history_target(data.get("projectId", ""))
            if bad:
                self._send_json(*bad)
                return
            file_history.request(home, None, "snapshot now")
            self._send_json(200, {"ok": True, "queued": True})
            return
        if p == "/api/history/restore":
            # {projectId, rev, path}: put an old version back. A write, so only
            # the dashboard page may, never a task's agent.
            why = self._page_refusal()
            if why:
                self._send_json(403, {"error": "page_only", "message": why})
                return
            proj, home, bad = _history_target(data.get("projectId", ""))
            if bad:
                self._send_json(*bad)
                return
            res = file_history.restore(home, str(data.get("rev") or ""), str(data.get("path") or ""),
                                       file_history.credit(_history_running, proj["id"], home))
            if not res.get("ok"):
                res["message"] = res.get("error", "")
            # 500 when the file was written but its snapshot failed: the page
            # must not say it went well.
            self._send_json(200 if res.get("ok") else (500 if res.get("written") else 400), res)
            return
        if p == "/api/digest/check":
            # {projectId, force?}: run the progress check now. Without force it
            # sends only when something changed, exactly like the timer.
            proj = find_project((data.get("projectId") or "").strip())
            if proj is None:
                self._send_json(404, {"error": "no_such_project"})
                return
            self._send_json(200, digest.check(proj, force=bool(data.get("force"))))
            return
        if p == "/api/rotation/check":
            # {projectId, force?, immediate?}: check the PO's size now. force
            # ignores the threshold (it still asks for the handover first);
            # immediate rotates at once, without asking. {roomId, identity?,
            # ...} checks that task's owner instead.
            rid = (data.get("roomId") or "").strip()
            if rid:
                if chatroom.get_room(rid) is None:
                    self._send_json(404, {"error": "no_such_room"})
                    return
                self._send_json(200, rotation.check_task(
                    rid, (data.get("identity") or "").strip(),
                    force=bool(data.get("force")), immediate=bool(data.get("immediate"))))
                return
            proj = find_project((data.get("projectId") or "").strip())
            if proj is None:
                self._send_json(404, {"error": "no_such_project"})
                return
            self._send_json(200, rotation.check(proj, force=bool(data.get("force")),
                                                immediate=bool(data.get("immediate"))))
            return
        if p == "/api/projects/delete":
            pid = (data.get("projectId") or "").strip()
            ok = unregister_project(pid)
            self._send_json(200 if ok else 404, {"ok": ok})
            return
        if p == "/api/roadmap":
            # Refused with 409 and the newer text when the file changed since
            # the editor opened it (see write_roadmap).
            pid = (data.get("projectId") or "").strip()
            proj = next((x for x in load_projects() if x["id"] == pid), None)
            if proj is None:
                self._send_json(404, {"error": "no_such_project"})
                return
            ok, res = write_roadmap(proj, data.get("text"), data.get("baseVersion") or "")
            if ok:
                self._send_json(200, {"ok": True, **res})
            else:
                self._send_json(409 if res.get("error") == "conflict" else 400, res)
            return
        if p == "/api/room/new":
            # Create a task (room + workspace + task folder) and — unless
            # start:false asks for a draft — launch its agents pre-wired to the
            # Ensemble MCP (each with its own identity token), seeded with the
            # spec. The workspace mode is the caller's choice: the framework
            # never forces a code checkout (a project or task may have no code).
            ok, room_full, err = create_task(
                data.get("title"), data.get("task"), data.get("projectId"),
                data.get("agents") or [], data.get("workspace") or "",
                data.get("priority"), human=True)
            if not ok:
                self._send_json(400, {"error": err})
                return
            try:
                launched = [] if data.get("start") is False else self._start_room(room_full)
            except StartRoomError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, {"ok": True,
                                  "room": chatroom.get_room(room_full["id"]),
                                  "launched": launched})
            return
        if p == "/api/room/say":
            rid = (data.get("roomId") or "").strip()
            text = (data.get("text") or "").strip()
            to = (data.get("to") or "").strip()
            key = str(data.get("key") or "").strip()[:200]
            if not rid or not text:
                self._send_json(400, {"error": "missing_fields"})
                return
            # Posts to the room as it is, running or not (a stopped room's
            # agents read it once resumed). To have a stopped room resumed
            # and woken for a message, say it through /api/room/resume.
            with _SAY_KEYS_LOCK if key else contextlib.nullcontext():
                if key and _say_key_seen(rid, key):
                    self._send_json(200, {"ok": True, "duplicate": True})
                    return
                result = chatroom.post_message(rid, chatroom.HUMAN_IDENTITY, text, to=to)
                if result is not None and key:
                    _SAY_KEYS[(rid, key)] = time.time()
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
            ok, room_full, err = reassign_task(rid, data.get("agents") or [], human=True)
            if not ok:
                code = {"no_such_room": 404, "task_is_running": 409}.get(err, 400)
                self._send_json(code, {"error": err})
                return
            self._send_json(200, {"ok": True, "room": chatroom.get_room(rid)})
            return
        if p == "/api/room/priority":
            # Set the product owner's priority on a task: 1-5 or a level name.
            rid = (data.get("roomId") or "").strip()
            n = normalize_priority(data.get("priority"))
            if n is None:
                self._send_json(400, {"error": "bad_priority",
                                      "expected": PRIORITY_CHOICES})
                return
            ok, room, err = update_task(rid, priority=n)
            if not ok:
                self._send_json(404 if err == "no_such_room" else 400, {"error": err})
                return
            self._send_json(200, {"ok": True, "priority": n,
                                  "priorityName": PRIORITY_NAMES[n]})
            return
        if p == "/api/room/workflow":
            # The owner moving a card. This endpoint is the UI's, and the UI is
            # the human — agents come in through /mcp, where the ProductOwner
            # check applies. So every column is available here, including Done.
            rid, bad = http_task_id(data.get("roomId") or data.get("task") or "", data.get("projectId") or "")
            if bad:
                self._send_json(404, bad)
                return
            w = normalize_workflow(data.get("workflow"))
            if w is None:
                self._send_json(400, {"error": "bad_workflow",
                                      "expected": WORKFLOW_CHOICES})
                return
            ok, room, err = update_task(rid, workflow=w)
            if not ok:
                self._send_json(404 if err == "no_such_room" else 400, {"error": err})
                return
            self._send_json(200, {"ok": True, "workflow": w,
                                  "workflowName": WORKFLOW_LABELS[w]})
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
                number_adopted_room(room_full)
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
            # A past session opened to drive by hand: its prompts stay.
            info = self._resume_room_agent_pty(room_full, part, collab=False, human=True)
            part["ptyId"] = info["ptyId"]
            chatroom.update_room(room_full)
            number_adopted_room(room_full)
            self._send_json(200, {"ok": True,
                                  "room": chatroom.get_room(room["id"])})
            return
        if p == "/api/room/resume":
            # Recover an ended session after a restart: relaunch each agent in a
            # fresh PTY, resuming its prior conversation.
            # With ``text`` (and ``to``): sending to a stopped session — it
            # is resumed and the text typed in as its first input, held here
            # meanwhile. What a failed resume kept goes along with any new
            # attempt (``retry`` is that, with nothing new); only ``discard``
            # drops it. A room already running is not launched again: text
            # goes straight in, a plain resume is a no-op. ``key`` makes a
            # request safe to send twice (a lost reply): the second is a
            # duplicate, nothing is queued again — unless the hub still holds
            # that key (the resume it started failed later): then the same
            # send is its retry. ``roomId`` may be a number (#18 with
            # ``projectId``, ED-18), or ``task`` may give it.
            rid, bad = http_task_id(data.get("roomId") or data.get("task") or "", data.get("projectId") or "")
            if bad:
                self._send_json(404, bad)
                return
            room_full = chatroom.get_room(rid, public=False)
            if room_full is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            if data.get("discard"):
                self._send_json(200, {"ok": True, "discarded": discard_pending(rid)})
                return
            text = (data.get("text") or "").strip()
            to = (data.get("to") or "").strip()
            key = str(data.get("key") or "").strip()[:200]
            # A draft (created but never launched, e.g. by a planning agent)
            # starts fresh; anything else resumes its agents' conversations.
            try:
                with _SAY_KEYS_LOCK if key else contextlib.nullcontext():
                    if key and _say_key_seen(rid, key) and not key_held(rid, key):
                        self._send_json(200, {"ok": True, "duplicate": True})
                        return
                    result = self._resume_room(room_full, text=text, to=to, key=key)
                    if key:
                        _SAY_KEYS[(rid, key)] = time.time()
            except Exception as exc:    # noqa: BLE001 — refused, in words; the text is kept
                print(f"[resume] {rid}: refused — {exc!r}", flush=True)
                # ``kept``: the text is held here, shown in the chat as not
                # delivered with Retry — the page need not keep it too.
                with _RESUMES_LOCK:
                    held = _RESUMES.get(rid)
                    kept = bool(text) and held is not None and any(
                        (key and it.get("key") == key) or it["text"] == text for it in held.queue)
                self._send_json(400, {"error": str(exc) or exc.__class__.__name__, "kept": kept})
                return
            self._send_json(200, {"ok": True, **result,
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
            res = delete_task(rid, data.get("members") or [])
            self._send_json(200 if res.get("ok", True) else 409, res)
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
            # so there is no sid-keyed label here — the session surfaces with
            # its own id shortly after launch.
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
            if pid in (os.getpid(), os.getppid()):
                # Closing kills the pid's whole tree (taskkill /T on Windows),
                # so the hub or its launcher here would stop the hub.
                self._send_json(403, {"error": "not_allowed",
                                      "message": "That is the hub itself. " + RESTART_REFUSED})
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
        if p == "/api/chat-scheme":
            cwd = (data.get("cwd") or "").strip()
            if not cwd:
                self._send_json(400, {"error": "missing_cwd"})
                return
            self._send_json(200, {"override": set_chat_scheme(cwd, bool(data.get("on")))})
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
    global _LOG_FILE, ACCESS_TOKEN, HUB_PORT
    args = sys.argv[1:]
    # Port: --port wins, else ENSEMBLE_PORT, else 8765.
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    else:
        port = int(os.environ.get("ENSEMBLE_PORT", "8765"))
    HUB_PORT = port
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
    # Windows, under pythonw.exe (the scheduled task): give the hub a hidden
    # console NOW, before its first git/tailscale call. Every console child then
    # shares it. Without one, each spawn opened its own console window and took
    # the keyboard focus for a few ms -- a desktop-wide flicker on every poll,
    # until the first agent start allocated the console as a side effect.
    ptyrun.ensure_windows_console()
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
    # Tasks from before numbers existed get theirs, and every project a key.
    try:
        done = backfill_task_numbers()
        if done["numbered"] or done["keys"]:
            print(f"task numbers: {done['numbered']} task(s) numbered, {done['keys']} project key(s) set", flush=True)
    except Exception as e:
        print(f"task numbers backfill skipped: {e}", flush=True)

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

    # Documents projects: every version of every file, snapshotted on its own
    # thread (a task's turn or report, a scan every few minutes, Snapshot now).
    file_history.start_scheduler(_history_homes, _history_running, _history_turn_ends)

    # Plan allowance: refreshed on its own thread so /api/usage is a cache read.
    usage.start_scheduler()

    # The PO's progress digest: checks each project with a PO on its interval,
    # wakes the PO only when something changed.
    digest.start_scheduler()

    # The PO's rotation: a fresh session from its handover when it gets long.
    rotation.start_scheduler()

    # Serve every listener; extra ones run in daemon threads, the last inline.
    for s in servers[:-1]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[-1].serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
