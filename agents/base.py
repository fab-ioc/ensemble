"""Agent-type abstraction for ensemble.

This is a *second* axis, orthogonal to the OS `backends/` package:

    backends/   → WHERE a session runs   (macOS iTerm / Windows Terminal / Linux)
    agents/     → WHAT is running in it   (claude / codex)

`backends` knows how to open/focus/close/theme a terminal window; it does not
care which CLI lives inside. `agents` knows how to *discover* a given CLI's
sessions and how to *launch/resume* it; it does not care which OS it is on.
The dashboard composes the two.

Each concrete agent (see `claude.py`, `codex.py`) subclasses `AgentType`.
Discovery returns a list of `AgentSession` records normalized to the same
shape the web UI already consumes, plus an `agent` tag so the UI can badge
each row.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentSession:
    """One discovered session, normalized across agent types.

    Field names mirror the keys `dashboard.load_sessions` already emits so a
    row can be merged into the existing unified list with minimal glue.
    """
    agent: str                    # "claude" | "codex"
    session_id: str
    cwd: str = ""
    started_at: float = 0.0       # epoch seconds (session birth)
    updated_at: float = 0.0       # epoch seconds (last activity / file mtime)
    first: str = ""               # first real user prompt (synthetic ctx filtered)
    last: str = ""                # last real user prompt
    turns: int = 0                # count of real user turns
    transcript_path: str = ""     # absolute path to the transcript/rollout file
    is_live: bool = False         # discovery can't always know; default False

    def to_row(self) -> dict:
        """Shape used by the HTTP layer / web UI."""
        return {
            "agent": self.agent,
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "first": self.first,
            "last": self.last,
            "turns": self.turns,
            "transcriptPath": self.transcript_path,
            "isLive": self.is_live,
        }


class AgentType:
    """Base agent. Concrete agents override discovery + launch."""

    key = "base"
    display_name = "Agent"
    # A short tag for the UI badge; defaults to the key.

    # ---- availability ----
    def installed(self) -> bool:
        """True if this agent's CLI is on PATH / usable on this machine."""
        return False

    # ---- discovery ----
    def list_sessions(self, limit: int = 200) -> list[AgentSession]:
        """Return recent sessions for this agent, newest first."""
        return []

    # ---- launch ----
    def launch_argv(self, cwd: str, prompt: str = "",
                    extra: list[str] | None = None) -> list[str]:
        """argv to start a NEW session (run with cwd=cwd by the caller)."""
        raise NotImplementedError

    def resume_argv(self, session_id: str,
                    extra: list[str] | None = None) -> list[str]:
        """argv to resume an existing session by id."""
        raise NotImplementedError

    # ---- metadata ----
    def info(self) -> dict:
        return {
            "key": self.key,
            "displayName": self.display_name,
            "installed": self.installed(),
        }
