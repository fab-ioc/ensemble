"""Agent-type registry.

Orthogonal to the OS `backends/` package: `backends` is *where* a session runs,
`agents` is *what* runs in it. Use `get_agent(key)` for a specific agent and
`available_agents()` / `installed_agents()` to enumerate.
"""
from __future__ import annotations

from .base import AgentSession, AgentType
from .claude import ClaudeAgent
from .codex import CodexAgent

# Registration order = default UI order. Claude first (the dashboard's origin).
_REGISTRY: dict[str, AgentType] = {
    "claude": ClaudeAgent(),
    "codex": CodexAgent(),
}

DEFAULT_AGENT = "claude"


def get_agent(key: str) -> AgentType | None:
    return _REGISTRY.get(key)


def available_agents() -> list[AgentType]:
    """Every registered agent, whether or not its CLI is installed."""
    return list(_REGISTRY.values())


def installed_agents() -> list[AgentType]:
    """Only agents whose CLI is present on this machine."""
    return [a for a in _REGISTRY.values() if a.installed()]


def agents_info() -> list[dict]:
    """Metadata list for the /api/agents endpoint."""
    return [a.info() for a in _REGISTRY.values()]


__all__ = [
    "AgentSession",
    "AgentType",
    "get_agent",
    "available_agents",
    "installed_agents",
    "agents_info",
    "DEFAULT_AGENT",
]
