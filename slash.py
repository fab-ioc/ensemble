"""Slash commands from the chat (#253).

What the message box suggests after a leading ``/``: an agent kind's built-in
commands, and the skills and custom commands its CLI itself would list (Claude
Code: ``~/.claude/skills``, ``~/.claude/commands`` and the same under the
working directory; Codex: ``$CODEX_HOME/prompts``). A command goes into the
agent's terminal raw (dashboard ``_send_command``): never wrapped, never a
point, never queued, since both CLIs take a command while a turn runs
(measured on Claude Code 2.1.308 and Codex 0.163.0).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# The built-ins, with one line each. Claude Code 2.1 and Codex 0.16x, as
# their own ``/`` lists show them; the ones that end the agent are left out
# of the suggestions (typed, they still go).
CLAUDE_BUILTINS: tuple[tuple[str, str], ...] = (
    ("add-dir", "Add a working directory"),
    ("agents", "Manage agent configurations"),
    ("clear", "Clear the conversation and start a new one"),
    ("compact", "Summarise the conversation to free context"),
    ("config", "Open the settings panel"),
    ("context", "Show what fills the context window"),
    ("cost", "Show this session's cost and duration"),
    ("doctor", "Check the installation"),
    ("effort", "Set the model's effort level"),
    ("export", "Export the conversation"),
    ("help", "Show help and the available commands"),
    ("hooks", "Manage hook configurations"),
    ("init", "Write a CLAUDE.md for this codebase"),
    ("mcp", "Manage MCP servers"),
    ("memory", "Edit Claude memory files"),
    ("model", "Choose the model"),
    ("output-style", "Choose the output style"),
    ("permissions", "Manage allow and deny rules"),
    ("plugin", "Manage plugins"),
    ("release-notes", "Show the release notes"),
    ("resume", "Resume an earlier conversation"),
    ("review", "Review a pull request"),
    ("rewind", "Go back to an earlier point in the conversation"),
    ("security-review", "Security review of the pending changes"),
    ("status", "Show version, model, account and connectivity"),
    ("todos", "List the current todo items"),
    ("usage", "Show plan usage limits"),
)
CODEX_BUILTINS: tuple[tuple[str, str], ...] = (
    ("approvals", "Choose what Codex may do without asking"),
    ("compact", "Summarise the conversation to free context"),
    ("diff", "Show the git diff, untracked files included"),
    ("init", "Write an AGENTS.md for this codebase"),
    ("mcp", "List the configured MCP tools"),
    ("mention", "Mention a file"),
    ("model", "Choose the model and reasoning effort"),
    ("new", "Start a new conversation"),
    ("resume", "Resume an earlier conversation"),
    ("review", "Review the current changes"),
    ("skills", "List and use skills"),
    ("status", "Show the session's configuration and token usage"),
    ("undo", "Undo the last turn's changes"),
)
# Commands after which the agent is in a new conversation: the hub follows it.
NEW_CONVERSATION = {"claude": ("clear", "reset", "new"), "codex": ("new", "clear")}

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_NAME = re.compile(r"^[A-Za-z0-9][\w.:-]{0,63}$")
_MAX = 400          # entries per source, so a huge folder cannot swamp the list


def _front(path: Path) -> dict:
    """A Markdown file's frontmatter, ``key: value`` lines only."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return {}
    m = _FRONT.match(head)
    out: dict = {}
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep and k.strip() and not k.startswith((" ", "\t")):
            out[k.strip().lower()] = v.strip().strip("\"'")
    if "description" not in out:
        # The first line of text stands in, as Claude's own list does.
        body = head[m.end():] if m else head
        first = next((ln.strip().lstrip("#").strip() for ln in body.splitlines() if ln.strip()), "")
        out["description"] = first
    return out


def _line(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


def _skills(root: Path, source: str) -> list[dict]:
    out = []
    try:
        dirs = sorted(d for d in root.iterdir() if d.is_dir())
    except OSError:
        return out
    for d in dirs[:_MAX]:
        f = d / "SKILL.md"
        if not f.is_file():
            continue
        fm = _front(f)
        name = fm.get("name") or d.name
        if _NAME.match(name):
            out.append({"name": name, "description": _line(fm.get("description")), "source": source})
    return out


def _commands(root: Path, source: str, prefix: str = "") -> list[dict]:
    """``*.md`` under ``root``; a subfolder's are ``/dir:name``."""
    out = []
    try:
        files = sorted(root.rglob("*.md"))
    except OSError:
        return out
    for f in files[:_MAX]:
        try:
            rel = f.relative_to(root).with_suffix("")
        except ValueError:
            continue
        name = prefix + ":".join(rel.parts)
        if _NAME.match(name):
            out.append({"name": name, "description": _line(_front(f).get("description")), "source": source})
    return out


def catalogue(agent: str, cwd: str = "", home: Path | None = None) -> list[dict]:
    """The commands to suggest for an agent of kind ``agent`` working in
    ``cwd``: ``[{name, description, source}]``, name without the slash,
    ``source`` one of builtin, skill, command, prompt. The first of a name wins
    (the project's over the user's, as the CLI does)."""
    home = home or Path.home()
    found: list[dict] = []
    if agent == "codex":
        found += [{"name": n, "description": d, "source": "builtin"} for n, d in CODEX_BUILTINS]
        codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex")
        found += _commands(codex_home / "prompts", "prompt", "prompts:")
    elif agent == "claude":
        found += [{"name": n, "description": d, "source": "builtin"} for n, d in CLAUDE_BUILTINS]
        roots = [Path(cwd) / ".claude"] if cwd else []
        roots.append(home / ".claude")
        for r in roots:
            found += _commands(r / "commands", "command")
            found += _skills(r / "skills", "skill")
    seen: set[str] = set()
    out = []
    for c in found:
        if c["name"] not in seen:
            seen.add(c["name"])
            out.append(c)
    return out


def command_name(text: str) -> str:
    """``/model opus`` -> ``model``; "" when ``text`` is not a command."""
    m = re.match(r"^/([A-Za-z0-9][\w.:-]*)(?:\s|$)", (text or "").strip())
    return m.group(1).lower() if m else ""


def is_command(text: str) -> bool:
    """A single-line text that starts with ``/name`` (a path such as
    ``/usr/bin`` is not one: a command name has no further slash)."""
    t = (text or "").strip()
    return bool(command_name(t)) and "\n" not in t


def starts_new_conversation(agent: str, text: str) -> bool:
    return command_name(text) in NEW_CONVERSATION.get(agent, ())
