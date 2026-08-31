#!/usr/bin/env python3
"""
claude-dashboard: web dashboard for Claude Code sessions.

Run:
    python3 dashboard.py [--port 8765]

Then open http://127.0.0.1:8765 in Chrome.

Data sources mirror ~/.claude/bin/claude-sessions:
  ~/.claude/sessions/<pid>.json               — one per running session (live state)
  ~/.claude/projects/<slug>/<session-id>.jsonl — full transcripts (history)
"""
from __future__ import annotations

import json
import os
import plistlib
import random
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# All OS-specific behavior (terminal control, process introspection, desktop
# integration) lives behind a platform backend, selected by sys.platform.
from backends import get_backend
from backends.base import (
    HOME, DASHBOARD_DIR, PRESETS_DIR, CS_ROOT, NUMBERED_RE as _NUMBERED_RE,
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
import chatroom
# Headless PTY runtime — dashboard-owned agent processes streamed to the browser.
from backends import ptyrun

BACKEND = get_backend()

# Live-session reading + workspace filtering live in backends.shared (so the
# backends can use them without importing dashboard.py). Aliased to the private
# names the rest of this module already uses.
_read_session_files = read_session_files
_read_agent_session_files = read_agent_session_files
_is_workspace_cwd = is_workspace_cwd


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
STATIC_DIR = Path(__file__).parent
# Default log location per platform (macOS keeps the historical ~/Library/Logs
# path; Windows/Linux log under the dashboard state dir, matching the CLIs).
if sys.platform == "darwin":
    DEFAULT_LOG_FILE = HOME / "Library" / "Logs" / "claude-dashboard.log"
else:
    DEFAULT_LOG_FILE = DASHBOARD_DIR / "logs" / "claude-dashboard.log"
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
        current[k] = v
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    return current


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


def sanitize_slug(s: str, max_len: int = 60) -> str:
    s = _SLUG_NONALNUM.sub("_", s.lower()).strip("_")
    return s[:max_len].rstrip("_") or "untitled"


def cwd_for_session(session_id: str) -> str:
    """Best-effort cwd lookup: live metadata first, then transcript."""
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


RENAME_PROMPT_PREFIX = "[claude-dashboard-rename] "

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
IJ_APP = os.environ.get("CLAUDE_DASHBOARD_IJ_APP", "IntelliJ IDEA")  # legacy fallback

# Jira auto-detection is OPT-IN and configured at install time (or via env).
# Precedence: settings.json (jiraEnabled / jiraBase / jiraPrefixes) then the
# CLAUDE_DASHBOARD_JIRA_* env vars. When no base is configured, Jira is off and
# the whole feature is hidden in the UI (via /api/jira-config → enabled:false).
def _load_jira_config() -> tuple[bool, str, set[str]]:
    s = load_settings()
    base = (s.get("jiraBase") or os.environ.get("CLAUDE_DASHBOARD_JIRA_BASE", "") or "").strip()
    raw_prefixes = s.get("jiraPrefixes")
    if raw_prefixes is None:
        raw_prefixes = os.environ.get("CLAUDE_DASHBOARD_JIRA_PREFIXES", "")
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
# to ~/.claude/dashboard/editors.json. Keys are language tokens emitted by
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
# CLAUDE_DASHBOARD_PERMISSION_MODE=acceptEdits  (or "" to disable the flag).
PERMISSION_MODE = os.environ.get("CLAUDE_DASHBOARD_PERMISSION_MODE", "bypassPermissions")


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

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Guard against a missing/closed stderr — under pythonw.exe (Windows,
        # windowless) there is no console stream, and writes would raise.
        if not sys.stderr:
            return
        try:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")
        except (OSError, ValueError):
            pass

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
            if not tpath:
                self._send_json(404, {"error": "not_found"})
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
        if p == "/api/platform":
            self._send_json(200, BACKEND.info())
            return
        if p == "/api/agents":
            self._send_json(200, agents.agents_info())
            return
        if p == "/api/rooms":
            self._send_json(200, chatroom.list_rooms())
            return
        if p == "/api/room":
            rid = (parse_qs(u.query).get("id", [""])[0]).strip()
            room = chatroom.get_room(rid) if rid else None
            if room is None:
                self._send_json(404, {"error": "no_such_room"})
                return
            self._send_json(200, room)
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
        if p == "/api/jira-config":
            self._send_json(200, {"enabled": JIRA_ENABLED, "base": JIRA_BASE,
                                  "prefixes": sorted(JIRA_PREFIXES)})
            return
        if p == "/api/update-check":
            self._send_json(200, check_for_update())
            return
        if p == "/api/settings":
            self._send_json(200, load_settings())
            return
        if p.startswith("/api/cost/"):
            sid = p[len("/api/cost/"):]
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
        u = urlparse(self.path)
        p = u.path
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
            self._send_json(200, delete_session(sid))
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
        for ident in recipients:
            part = next((x for x in room["participants"]
                         if x.get("identity") == ident), None)
            if not part:
                continue
            pid = self._resolve_live_pid(part)
            if not pid:
                continue
            wake = (f"[relay] New message from '{sender}' in your shared room. "
                    f"Use the chat_read tool to read it, then reply with "
                    f"chat_send — to your partner, or to \"user\" if you need "
                    f"the human's input.")
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
        cwd = os.path.join(base, ident)
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            pass
        partners = ", ".join(p["identity"] for p in room_full["participants"]
                             if p.get("kind") == "agent" and p["identity"] != ident)
        briefing = (
            f"You are '{ident}', collaborating with {partners or 'your partner'} "
            f"in a shared workspace to find the best possible solution to the "
            f"task below. Coordinate through the 'chat' MCP tools: call chat_send "
            f"to message your partner (end each of your turns by sending them "
            f"your findings, critique, or proposal), chat_read to read their "
            f"replies, and chat_send with to=\"user\" whenever you need the "
            f"human's decision, input, or clarification. Do not stop until you "
            f"have converged on a solution together or tagged the human. Begin "
            f"now by sending your partner your initial approach.\n\n"
            f"TASK:\n{task}"
        )
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
                       "-c", f'mcp_servers.chat.url="{url}"',
                       "-c", 'mcp_servers.chat.bearer_token_env_var="CHAT_TOKEN"']
            if model:
                command += ["-c", f'model="{model}"']
            res = BACKEND.open_new(cwd, briefing, label=label, command=command,
                                   agent="codex", identity=ident,
                                   env={"CHAT_TOKEN": token})
            return {"sessionId": "", "cwd": cwd, "launch": res}
        # claude (and claude-N)
        cfg = {"mcpServers": {"chat": {"type": "http", "url": url,
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
            partners = [a["identity"] for a in agents_in
                        if a["identity"] != part["identity"]]
            brief = (
                f"[room '{room['title']}'] You are '{part['identity']}', "
                f"collaborating with {', '.join(partners) or 'your partner'} to "
                f"find the best possible solution. Coordinate via the chat tools: "
                f"call chat_read to see new messages and chat_send to reply. End "
                f"each turn by sending your partner a message. When you need input "
                f"from the human, chat_send to \"user\". Start by introducing your "
                f"approach with chat_send."
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
                "serverInfo": {"name": "claude-dashboard-chat", "version": "1.0"},
            })
        if method is not None and method.startswith("notifications/"):
            return None  # notifications get no JSON-RPC response
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": chatroom.MCP_TOOLS})
        if method == "tools/call":
            return self._mcp_tool_call(params.get("name"),
                                       params.get("arguments") or {},
                                       room_id, identity, ok, err)
        return err(-32601, f"method not found: {method}")

    def _mcp_tool_call(self, name, args, room_id, identity, ok, err):
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

    def do_DELETE(self):
        # MCP clients DELETE /mcp to end a session; nothing to tear down.
        if urlparse(self.path).path == "/mcp":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):
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
            # Fire and forget — the spawned `claude-dashboard update`
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
        if p == "/api/room/new":
            # Create a multiagent room AND launch its agents pre-wired to the
            # chat MCP (each with its own identity token), seeded with the task.
            title = (data.get("title") or "multiagent session").strip()[:120]
            task = (data.get("task") or "").strip()
            agent_list = data.get("agents") or []
            if not isinstance(agent_list, list) or len(agent_list) < 2:
                self._send_json(400, {"error": "need_two_agents"})
                return
            # Each item is either a plain agent key ("claude") or an object
            # {agent, model}. Normalize to (agent_key, model) pairs.
            specs = []
            for item in agent_list:
                if isinstance(item, dict):
                    ak = (item.get("agent") or "").strip()
                    mdl = (item.get("model") or "").strip()
                else:
                    ak, mdl = str(item).strip(), ""
                ag = agents.get_agent(ak)
                if ag is None or not ag.installed():
                    self._send_json(400, {"error": f"agent_unavailable:{ak}"})
                    return
                specs.append((ak, mdl))
            ok, base, msg = create_cs_session(title)
            if not ok:
                self._send_json(400, {"error": msg})
                return
            members = [{"identity": ak, "agent": ak, "model": mdl}
                       for ak, mdl in specs]
            room = chatroom.create_room(title, members)
            room_full = chatroom.get_room(room["id"], public=False)
            room_full["cwd"] = base
            launched = []
            for part in [pp for pp in room_full["participants"]
                         if pp.get("kind") == "agent"]:
                info = self._launch_room_agent(room_full, part, task)
                part["sessionId"] = info["sessionId"]
                part["cwd"] = info["cwd"]
                launched.append({"identity": part["identity"],
                                 "result": info["launch"]})
            chatroom.update_room(room_full)
            self._send_json(200, {"ok": True,
                                  "room": chatroom.get_room(room["id"]),
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
        if p == "/api/room/delete":
            rid = (data.get("roomId") or "").strip()
            self._send_json(200, {"ok": chatroom.delete_room(rid)})
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
            label = claude_rename(sid)
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
    global _LOG_FILE
    args = sys.argv[1:]
    # Port: --port wins, else CLAUDE_DASHBOARD_PORT, else 8765.
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    else:
        port = int(os.environ.get("CLAUDE_DASHBOARD_PORT", "8765"))
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
    addr = ("127.0.0.1", port)
    cleaned = cleanup_rename_artifacts()
    if cleaned:
        print(f"cleaned up {cleaned} rename artifact session(s)", flush=True)
    srv = ThreadingHTTPServer(addr, Handler)
    print(f"claude-dashboard [{BACKEND.os_name}]: http://{addr[0]}:{addr[1]}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
