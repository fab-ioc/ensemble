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

import copy
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


def _read_meta(path: Path) -> dict:
    """The `session_meta` payload (line 1) only — id, cwd, start timestamp.

    Reading one line is orders of magnitude cheaper than `_parse_rollout`, which
    walks the whole file; use this whenever only the session's identity or cwd
    is needed (id lookup, cwd lookup, liveness backfill).
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            d = json.loads(fh.readline() or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    if d.get("type") != "session_meta":
        return {}
    return d.get("payload") or {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_ROLLOUT_CACHE: dict[str, tuple] = {}


def _parse_rollout(path: Path) -> AgentSession | None:
    """One rollout file as an AgentSession, remembered per file on its size and
    modification time: the task list asks for every rollout on every refresh,
    and an unchanged file always parses the same. A copy is returned, so a
    caller marking a session live can never change the remembered one."""
    try:
        stt = path.stat()
        sig = (stt.st_mtime_ns, stt.st_size)
    except OSError:
        sig = None
    if sig is not None:
        hit = _ROLLOUT_CACHE.get(str(path))
        if hit and hit[0] == sig:
            return copy.copy(hit[1]) if hit[1] is not None else None
    res = _parse_rollout_uncached(path)
    if sig is not None:
        _ROLLOUT_CACHE[str(path)] = (sig, res)
    return copy.copy(res) if res is not None else None


def _parse_rollout_uncached(path: Path) -> AgentSession | None:
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

    def rollout_files(self) -> list[Path]:
        """Every rollout file, newest first (by mtime)."""
        root = self.sessions_dir()
        if not root.exists():
            return []
        files = list(root.glob("*/*/*/rollout-*.jsonl"))
        files.sort(key=_mtime, reverse=True)
        return files

    def rollouts_for_session(self, session_id: str) -> list[Path]:
        """Every rollout belonging to ``session_id``, oldest first.

        The id is the uuid in the filename, so a targeted glob finds it without
        touching the rest of the history — this runs on the dashboard's poll
        loop, so it must not cost a full scan. Only when that misses (older
        layouts, or a resume that forked a file under a different name) do we
        fall back to reading each rollout's `session_meta` line."""
        root = self.sessions_dir()
        if not session_id or not root.exists():
            return []
        hits = list(root.glob(f"*/*/*/rollout-*-{session_id}.jsonl"))
        if not hits:
            hits = [f for f in self.rollout_files()
                    if (_read_meta(f).get("session_id")
                        or _read_meta(f).get("id")) == session_id]
        hits.sort(key=_mtime)
        return hits

    def session_stat(self, session_id: str) -> dict | None:
        """Aggregate size + newest mtime across the session's rollouts, or None
        when it has none yet. Backs the dashboard's cheap "did the transcript
        grow?" poll, the same signal a Claude session's .jsonl provides."""
        files = self.rollouts_for_session(session_id)
        if not files:
            return None
        size, mtime = 0, 0.0
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
        return {"size": size, "mtime": mtime}

    def read_turns(self, session_id: str) -> list[dict]:
        """User + assistant text turns, chronological — the shape the chat view
        renders as bubbles: ``{timestamp, role, text}``.

        Only `response_item` messages are read: codex records each one a second
        time as an `event_msg`/`item_completed`, so taking both would double
        every bubble. `developer` messages (skills, harness preamble) and
        injected `<environment_context>`-style user blocks are dropped — they're
        plumbing, not conversation."""
        turns: list[dict] = []
        seen: set[str] = set()
        for path in self.rollouts_for_session(session_id):
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
                        if d.get("type") != "response_item":
                            continue
                        payload = d.get("payload") or {}
                        if payload.get("type") != "message":
                            continue
                        role = payload.get("role")
                        if role not in ("user", "assistant"):
                            continue
                        text = _text_of(payload.get("content"))
                        if not text or (role == "user" and _is_synthetic(text)):
                            continue
                        key = payload.get("id") or f"{role}:{text}"
                        if key in seen:
                            continue
                        seen.add(key)
                        turns.append({"timestamp": d.get("timestamp", ""),
                                      "role": role, "text": text})
            except OSError:
                continue
        return turns

    def latest_session_id_for_cwd(self, cwd: str, since: float = 0.0) -> str:
        """The most recent codex session id whose rollout ran in `cwd` — used to
        `codex resume <id>` a headless session after a dashboard restart, and to
        learn the id of a session the dashboard just launched (codex, unlike
        claude, won't take a caller-supplied one). `since` skips rollouts older
        than that epoch, which keeps a "which session did I just start?" lookup
        to the handful of files written after the launch."""
        if not cwd:
            return ""
        target = os.path.normcase(os.path.normpath(cwd))
        try:
            for f in self.rollout_files():        # newest first
                if since and _mtime(f) < since:
                    break
                meta = _read_meta(f)
                if not meta:
                    continue
                if os.path.normcase(os.path.normpath(meta.get("cwd") or "")) == target:
                    return meta.get("session_id") or meta.get("id") or ""
        except OSError:
            pass
        return ""

    def delete_session(self, session_id: str) -> list[str]:
        """Remove every rollout file belonging to ``session_id`` (a resumed
        session writes several under the same id). Returns the deleted paths;
        empty when nothing matched. Used by the dashboard's unified Delete so a
        removed collaboration doesn't resurface as a Codex history row."""
        removed: list[str] = []
        for f in self.rollouts_for_session(session_id):
            try:
                f.unlink()
                removed.append(str(f))
            except OSError:
                pass
        return removed

    def cwd_for_session(self, session_id: str) -> str:
        """The cwd a rollout ran in, for the given session id (best-effort)."""
        for f in self.rollouts_for_session(session_id):
            cwd = (_read_meta(f).get("cwd") or "").strip()
            if cwd:
                return cwd
        return ""

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
