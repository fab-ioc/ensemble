"""The Slack reader that goes through Claude's Slack connection (design #1, section 2).

This is the only file that talks to Slack. `sources/slack.py` hands it a list of
planned reads (`Query`) and gets back what they found (`ReadResult`). A later
reader that uses a Slack token would be another class with the same `read()`
and the same result; `make_reader()` at the bottom is the one place that picks
the reader.

How a read works (ported from Slack Man's watcher, `slackwatch/fetch.py`):

* One short `claude -p` run on Haiku, with only Claude's Slack connection
  loaded (`allowedMcpServers`), no built-in tools (`--tools ""`), and only the
  Slack read tools this run needs allowed. Every write tool is refused by name.
* The prompt lists every call with its exact arguments. Haiku makes them all in
  its first reply, and the run stops there (`--max-turns 1`): the tools run, but
  their answers never go back to a model. The answers are read here, straight
  from the run's output. No Slack message text ever reaches a model.
* Any call that was not planned, or planned but changed, fails the read. So does
  a write tool that is present in the run at all.

The tool names are not written into the code. Claude names a connection's tools
`mcp__<connection name>__<tool>`, and the connection's name may differ from one
user to the next. The first read starts a short run with no tool allowed, and
takes the name from the list of tools in its first line (`find_prefix`). A later
run whose tools carry another name corrects it the same way.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from . import slack_parse

# The tools by the part of the name after the connection's name.
SEARCH = "slack_search_public_and_private"
READ_CHANNEL = "slack_read_channel"
READ_THREAD = "slack_read_thread"
READ_PROFILE = "slack_read_user_profile"
SEARCH_CHANNELS = "slack_search_channels"  # a channel's id by its name

PAGE_SIZE = 20  # the most results the search tool returns at once

# Slack tools that change something in Slack. Never allowed, always refused by name.
WRITE_TOOLS = [
    "slack_send_message",
    "slack_send_message_draft",
    "slack_schedule_message",
    "slack_add_reaction",
    "slack_create_canvas",
    "slack_update_canvas",
]

# The tools known to only read. A run may allow nothing outside this list.
KNOWN_READ_ONLY = {SEARCH, READ_CHANNEL, READ_THREAD, READ_PROFILE, SEARCH_CHANNELS, "slack_search_public"}

# Other Slack tools that only read but are not used. Refused too, so the run
# stays small: each tool's description costs input on every run.
UNUSED_READ_TOOLS = [
    "slack_get_reactions",
    "slack_list_channel_members",
    "slack_list_user_channels",
    "slack_read_canvas",
    "slack_read_file",
    "slack_search_emojis",
    "slack_search_public",
    "slack_search_users",
]

# Words that mark a tool as one that changes something. Checked on every tool the
# run is allowed, and on every Slack tool the run reports it has.
WRITE_WORDS = re.compile(
    r"send|draft|schedule|post|add|create|update|edit|delete|remove|upload|"
    r"invite|join|leave|kick|archive|rename|set|pin|react|reaction|canvas|write|mark",
    re.I,
)

SYSTEM_PROMPT = (
    "You make Slack read calls for a script. In your first and only reply, make every tool call "
    "listed in the user message, all at once, with exactly the arguments given. "
    "Do not add, change or skip a call. Do not write any text."
)
PROBE_SYSTEM_PROMPT = "Reply with the single word ok. Do not call any tool."

# Only Claude's Slack connection is loaded; every other connection and MCP server stays off.
SETTINGS = {"allowedMcpServers": [{"serverUrl": "https://mcp.slack.com/*"}]}

# Environment passed to the run. Everything else of the caller is dropped, so a run
# started by the hub (or under an agent) cannot reach the hub or another agent.
# CLAUDE_CONFIG_DIR is kept: a Claude login kept in another folder is found there.
PASS_ENV = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "CLAUDE_CONFIG_DIR")
RUN_ENV = {
    # Wait for the claude.ai connections before the first model call. Without this
    # `claude -p` may start before Slack is connected, and has no Slack tools.
    "MCP_CONNECTION_NONBLOCKING": "false",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "DISABLE_AUTOUPDATER": "1",
}


class ReadError(Exception):
    """A read failed. `code` is a fixed word for the frame (see sources/base.Check);
    the text says why in plain words. `usage` is the plan use of the failed run."""

    def __init__(self, reason: str, code: str = "error", usage: dict | None = None):
        super().__init__(reason)
        self.code = code
        self.usage = usage or {}


@dataclass
class Query:
    """One planned read. kind: "search", "channel", "thread", "profile" or
    "channels" (channels found by name)."""
    label: str
    kind: str
    args: dict


TOOL_FOR = {"search": SEARCH, "channel": READ_CHANNEL, "thread": READ_THREAD, "profile": READ_PROFILE,
            "channels": SEARCH_CHANNELS}


@dataclass
class ReadResult:
    messages: dict = field(default_factory=dict)   # label -> [slack_parse.SlackMessage]
    full_pages: set = field(default_factory=set)   # labels whose answer was a full page
    facts: dict = field(default_factory=dict)      # label -> dict (profile; channel header)
    usage: dict = field(default_factory=dict)


# ---------- read-only rules ----------

def looks_like_write_tool(short: str) -> bool:
    return bool(WRITE_WORDS.search(short)) or short in WRITE_TOOLS


def check_allowed(shorts) -> None:
    """Raise ValueError if the list holds anything but known read-only tools."""
    for short in shorts:
        if looks_like_write_tool(short):
            raise ValueError(f"write tool in the allowed list: {short}")
        if short not in KNOWN_READ_ONLY:
            raise ValueError(f"tool not known to be read-only: {short}")


def short_name(name: str) -> str:
    """The tool's name after the connection's name: mcp__x__slack_read_channel -> slack_read_channel."""
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def _is_slack_tool(name: str) -> bool:
    return name.startswith("mcp__") and short_name(name).startswith("slack_")


# ---------- the command line ----------

def find_claude(given: str = "") -> str:
    for candidate in (given, shutil.which("claude"), os.path.expanduser("~/.local/bin/claude")):
        if candidate and os.path.exists(candidate):
            return candidate
    return "claude"


def run_env() -> dict:
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env.update(RUN_ENV)
    return env


def _base_command(claude_path: str) -> list:
    return [
        claude_path,
        "-p",
        "--model", "haiku",
        "--tools", "",
        "--setting-sources", "",
        "--settings", json.dumps(SETTINGS),
        "--permission-mode", "dontAsk",
        "--max-turns", "1",
        "--no-session-persistence",
        "--output-format", "stream-json",
        "--verbose",
    ]


def allowed_shorts(queries) -> list:
    """The read tools these queries use, and nothing more."""
    out = []
    for q in queries:
        short = TOOL_FOR[q.kind]
        if short not in out:
            out.append(short)
    return out


def build_command(claude_path: str, prefix: str, queries) -> list:
    allowed = allowed_shorts(queries)
    check_allowed(allowed)
    denied = [s for s in WRITE_TOOLS + UNUSED_READ_TOOLS + sorted(KNOWN_READ_ONLY) if s not in allowed]
    denied = list(dict.fromkeys(denied))
    lines = [f"Make these {len(queries)} tool calls:"]
    for i, q in enumerate(queries, 1):
        lines.append(f"{i}. {TOOL_FOR[q.kind]} {json.dumps(q.args, separators=(',', ':'))}")
    return _base_command(claude_path) + [
        "--allowedTools", ",".join(prefix + s for s in allowed),
        "--disallowedTools", ",".join(prefix + s for s in denied),
        "--system-prompt", SYSTEM_PROMPT,
        "\n".join(lines),
    ]


def build_probe_command(claude_path: str) -> list:
    """A run with no tool allowed at all, only to read the tools' names from its first line."""
    return _base_command(claude_path) + ["--system-prompt", PROBE_SYSTEM_PROMPT, "ok"]


# ---------- reading the run's output ----------

def _events(lines) -> list:
    events = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _usage(final) -> dict:
    if not final:
        return {}
    u = final.get("usage") or {}
    return {
        "input": u.get("input_tokens", 0),
        "cache_write": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "output": u.get("output_tokens", 0),
        "cost_usd": final.get("total_cost_usd", 0.0),
        "duration_ms": final.get("duration_ms", 0),
    }


def _final_error(final) -> str:
    if not final:
        return ""
    if final.get("subtype") == "error_max_turns":
        return ""  # expected: the run stops after Haiku's tool calls
    if final.get("is_error") or final.get("subtype") not in (None, "success"):
        text = final.get("result") or final.get("subtype") or "unknown error"
        errors = final.get("errors")
        if errors:
            text = f"{text} {errors}"
        return str(text)[:400]
    return ""


def _no_start_error(final) -> ReadError:
    reason = _final_error(final)
    if re.search(r"log ?in|logged in|auth", reason, re.I):
        return ReadError(f"Claude is not signed in ({reason})", "not_signed_in", _usage(final))
    return ReadError(reason or "Claude did not start (no first line in its output)", "error", _usage(final))


def find_prefix(lines) -> str:
    """The connection's tool name prefix, from a run's first line. Raises ReadError."""
    events = _events(lines)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    final = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if init is None:
        raise _no_start_error(final)
    return _prefix_from(init, final)


def _prefix_from(init, final) -> str:
    hits = sorted({n[: -len(SEARCH)] for n in init.get("tools") or []
                   if n.startswith("mcp__") and n.endswith("__" + SEARCH)})
    if not hits:
        raise ReadError(_not_connected(init), "not_connected", _usage(final))
    if len(hits) > 1:
        raise ReadError("more than one Slack connection in Claude: " + ", ".join(hits), "error", _usage(final))
    return hits[0]


def _not_connected(init) -> str:
    servers = ", ".join(f"{s.get('name')}: {s.get('status')}" for s in init.get("mcp_servers") or [])
    return ("Claude's Slack connection is not available (connections: "
            f"{servers or 'none'}). If this lasts, reconnect Slack in claude.ai, Settings, Connectors.")


def _norm(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _same_args(planned: dict, made: dict) -> bool:
    if set(planned) != set(made):
        return False
    return all(_norm(planned[k]) == _norm(made[k]) for k in planned)


def read_stream(lines, queries, prefix: str) -> ReadResult:
    """What the planned calls found, from the run's stream-json output. Raises ReadError
    (with the run's plan use in `usage`, so failed runs are counted too)."""
    events = _events(lines)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    final = next((e for e in reversed(events) if e.get("type") == "result"), None)
    result = ReadResult(usage=_usage(final))
    try:
        return _read(events, init, final, queries, prefix, result)
    except ReadError as e:
        e.usage = e.usage or result.usage
        raise


def _read(events, init, final, queries, prefix, result) -> ReadResult:
    if init is None:
        raise _no_start_error(final)
    available = set(init.get("tools") or [])
    # Every write tool should have been refused. If one is in the run at all, the
    # refusal has stopped working: fail loudly rather than carry on.
    write_present = sorted(t for t in available if _is_slack_tool(t) and looks_like_write_tool(short_name(t)))
    if write_present:
        raise ReadError("the Claude run had Slack write tools, which should have been refused: "
                        + ", ".join(write_present), "write_tool")
    needed = [prefix + s for s in allowed_shorts(queries)]
    missing = [t for t in needed if t not in available]
    if missing:
        raise ReadError(_not_connected(init) + f" Missing: {', '.join(short_name(t) for t in missing)}.",
                        "not_connected")

    # Pair each call Haiku made with the call that was planned.
    calls = {}  # tool_use id -> Query
    unplanned = []
    remaining = list(queries)
    for e in events:
        if e.get("type") != "assistant":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if c.get("type") != "tool_use":
                continue
            match = next((q for q in remaining if prefix + TOOL_FOR[q.kind] == c.get("name")
                          and _same_args(q.args, c.get("input") or {})), None)
            if match is None:
                unplanned.append(f"{c.get('name')} {json.dumps(c.get('input'))}")
            else:
                remaining.remove(match)
                calls[c.get("id")] = match
    if unplanned:
        raise ReadError("Haiku made calls it was not asked for: " + "; ".join(unplanned)[:500], "unplanned_call")

    answered = set()
    for e in events:
        if e.get("type") != "user":
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            q = calls.get(c.get("tool_use_id"))
            if q is None:
                continue
            raw = slack_parse.tool_result_text(c.get("content"))
            if c.get("is_error"):
                raise ReadError(f"the Slack read {q.label} failed: {raw[:300]}", "slack_error")
            try:
                _take(result, q, raw)
            except slack_parse.ParseError as err:
                raise ReadError(f"Slack answered {q.label} in a form the hub cannot read: {err}", "unreadable")
            answered.add(q.label)

    not_answered = [q.label for q in queries if q.label not in answered]
    if not_answered:
        reason = _final_error(final)
        raise ReadError("these Slack reads were not made or got no answer: " + ", ".join(not_answered)
                        + (f" ({reason})" if reason else ""), "error")
    return result


def _take(result: ReadResult, q: Query, raw: str) -> None:
    if q.kind == "search":
        msgs, count = slack_parse.parse_search(raw, q.label)
        if count >= int(q.args.get("limit", PAGE_SIZE)):
            result.full_pages.add(q.label)
        result.messages[q.label] = msgs
    elif q.kind == "channel":
        msgs, header = slack_parse.parse_channel(raw, q.args["channel_id"], q.label)
        result.messages[q.label] = msgs
        result.facts[q.label] = header
    elif q.kind == "thread":
        msgs, cursor = slack_parse.parse_thread(raw, q.args["channel_id"], q.label)
        result.messages[q.label] = msgs
        result.facts[q.label] = {"cursor": cursor}
    elif q.kind == "profile":
        result.facts[q.label] = slack_parse.parse_profile(raw)
    elif q.kind == "channels":
        result.facts[q.label] = {"channels": slack_parse.parse_channels(raw)}


# ---------- the reader ----------

class ConnectorReader:
    """Reads Slack through Claude's Slack connection, one `claude -p` run per batch.

    prefix: the tool name prefix found earlier ("" when not known yet). After each
    read, `self.prefix` holds the one to keep.
    """

    name = "connector"

    def __init__(self, claude_path: str = "", cwd: str = "", prefix: str = "", timeout: int = 150):
        self.claude_path = find_claude(claude_path)
        self.cwd = cwd or os.path.join(tempfile.gettempdir(), "ensemble-slack-reader")
        self.prefix = prefix
        self.timeout = timeout
        self.runs = 0
        self.usage: dict = {}  # plan use of every run made by this reader

    def _run(self, cmd) -> tuple[list, str]:
        os.makedirs(self.cwd, exist_ok=True)
        self.runs += 1
        try:
            proc = subprocess.run(cmd, cwd=self.cwd, env=run_env(), capture_output=True, text=True,
                                  timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ReadError(f"the Claude run took longer than {self.timeout} seconds and was stopped", "timeout")
        except OSError as e:
            raise ReadError(f"could not start Claude ({self.claude_path}): {e}", "error")
        return proc.stdout.splitlines(), proc.stderr

    def _count(self, usage: dict) -> None:
        self.usage = add_usage(self.usage, usage)

    def find_prefix(self) -> str:
        """Start a run with no tool allowed and take the Slack tools' name prefix from it."""
        lines, stderr = self._run(build_probe_command(self.claude_path))
        events = _events(lines)
        final = next((e for e in reversed(events) if e.get("type") == "result"), None)
        self._count(_usage(final))
        try:
            self.prefix = find_prefix(lines)
        except ReadError as e:
            raise _with_stderr(e, stderr)
        return self.prefix

    def read(self, queries) -> ReadResult:
        """Make the planned reads in one run. Raises ReadError."""
        if not queries:
            return ReadResult()
        if not self.prefix:
            self.find_prefix()
        lines, stderr = self._run(build_command(self.claude_path, self.prefix, queries))
        try:
            result = read_stream(lines, queries, self.prefix)
        except ReadError as e:
            self._count(e.usage)
            if e.code == "not_connected":
                self._learn_new_prefix(lines)
            raise _with_stderr(e, stderr)
        self._count(result.usage)
        return result

    def _learn_new_prefix(self, lines) -> None:
        """The run had Slack tools under another name: keep that name for the next read."""
        events = _events(lines)
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
        if init is None:
            return
        try:
            self.prefix = _prefix_from(init, None)
        except ReadError:
            pass


def _with_stderr(e: ReadError, stderr: str) -> ReadError:
    lines = (stderr or "").strip().splitlines()
    tail = " | ".join(x for x in lines[-3:] if not x.startswith("Warning: claude.ai MCP servers blocked"))
    if tail and tail not in str(e):
        return ReadError(f"{e} (Claude said: {tail[:300]})", e.code, e.usage)
    return e


def add_usage(a: dict, b: dict) -> dict:
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = (out.get(k) or 0) + v
    return out


def make_reader(settings: dict, memory: dict, claude_path: str = "", cwd: str = ""):
    """The one place that picks how Slack is read.

    settings["reader"]: "connector" (Claude's Slack connection, the only one built)
    or, later, "token" (a Slack user token and the Slack Web API: `search.messages`,
    `conversations.history`, `conversations.replies`, `users.profile.get`). A token
    reader takes the same `Query` list and returns the same `ReadResult`; nothing
    above this function changes.
    """
    kind = (settings or {}).get("reader") or "connector"
    if kind != "connector":
        raise ReadError(f"the Slack reader {kind!r} is not built yet; use Claude's Slack connection",
                        "not_set_up")
    return ConnectorReader(claude_path=claude_path, cwd=cwd, prefix=(memory or {}).get("toolPrefix", ""))
