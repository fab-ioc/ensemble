"""Codex CLI agent adapter.

Codex stores each session as a JSONL "rollout" file:

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl

Line 1 is a `session_meta` record carrying the session id, cwd, and start
timestamp. Subsequent lines are events. Unlike Claude, Codex writes **no**
live/pid registry, so discovery here yields history only (`is_live=False`);
liveness for dashboard-launched Codex sessions is tracked separately by the
dashboard's own launch registry.

CODEX_HOME overrides the default `~/.codex` location, matching the CLI.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .base import AgentSession, AgentType


def _codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else (Path.home() / ".codex")


def _iso_to_epoch(ts: str) -> float:
    """Parse Codex's ISO-8601 timestamps (e.g. '2026-08-06T09:09:06.301Z')."""
    if not ts:
        return 0.0
    try:
        # Python 3.11+ fromisoformat accepts the trailing 'Z'.
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, OSError):
        return 0.0


# Prefixes that mark a Codex-injected pseudo-'user' message rather than a real
# human prompt: XML-ish context blocks, and the project AGENTS.md preamble
# (Codex's equivalent of CLAUDE.md, fed in as a leading user turn).
_SYNTHETIC_PREFIXES = (
    "<",                       # <environment_context>, <permissions ...>, <user_instructions ...>
    "# AGENTS.md",
    "# instructions",
)


def _is_synthetic(text: str) -> bool:
    """True if this 'user' message is an injected context/instructions block,
    not something the human actually typed."""
    t = text.lstrip()
    low = t.lower()
    return t.startswith(_SYNTHETIC_PREFIXES) or low.startswith("# agents.md")


def _text_of(content) -> str:
    """Flatten a Codex message `content` (str or list of typed parts)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") in ("text", "input_text", "output_text"):
                parts.append(c.get("text", ""))
        return " ".join(parts).strip()
    return ""


def _parse_rollout(path: Path) -> AgentSession | None:
    """Read one rollout file into a normalized AgentSession."""
    session_id = ""
    cwd = ""
    started = 0.0
    first = last = ""
    turns = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                typ = d.get("type")
                payload = d.get("payload") or {}
                if typ == "session_meta":
                    session_id = payload.get("session_id") or payload.get("id") or ""
                    cwd = payload.get("cwd", "") or ""
                    started = _iso_to_epoch(payload.get("timestamp", ""))
                    continue
                # Real user input shows up either as an event_msg/user_message
                # or a response_item message with role == "user".
                is_user_msg = (
                    (typ == "event_msg" and payload.get("type") == "user_message")
                    or (isinstance(payload, dict)
                        and payload.get("type") == "message"
                        and payload.get("role") == "user")
                )
                if not is_user_msg:
                    continue
                text = _text_of(payload.get("content") or payload.get("message") or "")
                if not text or _is_synthetic(text):
                    continue
                turns += 1
                if not first:
                    first = text[:200]
                last = text[:200]
    except OSError:
        return None

    if not session_id:
        # Fall back to the uuid embedded in the filename.
        stem = path.stem  # rollout-<ts>-<uuid>
        session_id = stem.split("-", 2)[-1] if "-" in stem else stem

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = started

    return AgentSession(
        agent="codex",
        session_id=session_id,
        cwd=cwd,
        started_at=started,
        updated_at=mtime,
        first=first,
        last=last,
        turns=turns,
        transcript_path=str(path),
        is_live=False,
    )


class CodexAgent(AgentType):
    key = "codex"
    display_name = "Codex"

    def installed(self) -> bool:
        return shutil.which("codex") is not None

    def sessions_dir(self) -> Path:
        return _codex_home() / "sessions"

    def list_sessions(self, limit: int = 200) -> list[AgentSession]:
        root = self.sessions_dir()
        if not root.exists():
            return []
        files = list(root.glob("*/*/*/rollout-*.jsonl"))
        # Newest first by mtime; only parse up to `limit` to bound cost.
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            files.sort(key=lambda p: p.name, reverse=True)
        out: list[AgentSession] = []
        seen: set[str] = set()
        for f in files[:limit]:
            s = _parse_rollout(f)
            if s is None:
                continue
            # A resumed session writes a fresh rollout file under the same id.
            # Files are mtime-desc, so the first one we see is the most recent.
            if s.session_id in seen:
                continue
            seen.add(s.session_id)
            out.append(s)
        return out

    def launch_argv(self, cwd: str, prompt: str = "",
                    extra: list[str] | None = None) -> list[str]:
        argv = ["codex"]
        if extra:
            argv += list(extra)
        if prompt:
            argv.append(prompt)
        return argv

    def resume_argv(self, session_id: str,
                    extra: list[str] | None = None) -> list[str]:
        argv = ["codex", "resume", session_id]
        if extra:
            argv += list(extra)
        return argv

    def ensure_trusted(self, cwd: str) -> bool:
        """Pre-authorize `cwd` in ~/.codex/config.toml so an unattended launch
        skips Codex's interactive "Do you trust this directory?" gate (which
        otherwise blocks the session before it ever starts). Codex persists
        trust as `[projects.'<path>']` with `trust_level = "trusted"`, keying on
        the lowercased path — we match that. Idempotent; returns True if the
        entry is present (already there or freshly written)."""
        if not cwd:
            return False
        key = os.path.normpath(cwd).lower()
        cfg = _codex_home() / "config.toml"
        try:
            existing = ""
            if cfg.exists():
                existing = cfg.read_text(encoding="utf-8")
                # Cheap idempotency check: the exact table header already there.
                for variant in (key, cwd, os.path.normpath(cwd)):
                    if f"[projects.'{variant}']" in existing:
                        return True
            cfg.parent.mkdir(parents=True, exist_ok=True)
            block = f"\n[projects.'{key}']\ntrust_level = \"trusted\"\n"
            with cfg.open("a", encoding="utf-8") as fh:
                fh.write(block)
            return True
        except OSError:
            return False
