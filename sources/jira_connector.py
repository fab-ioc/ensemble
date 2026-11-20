"""The Jira reader that goes through Claude's Atlassian connection (#14).

This is the only file that talks to Jira. `sources/jira.py` hands it a list of
planned reads (`Query`) and gets back what each one answered (`ReadResult`),
as the service's own JSON. It works the way the Slack reader does
(`slack_connector.py`), and shares its run handling:

* One short `claude -p` run on Haiku, with only Claude's Atlassian connection
  loaded (`allowedMcpServers`), no built-in tools (`--tools ""`), and only the
  Jira read tools this run needs allowed. Every write tool is refused by name.
* The prompt lists every call with its exact arguments. Haiku makes them all in
  its first reply and the run stops there (`--max-turns 1`): the tools run, but
  their answers never go back to a model. They are read here, from the run's
  output. No ticket text ever reaches a model.
* Any call that was not planned, or planned but changed, fails the read. So
  does a write tool that is present in the run at all.

Claude passes on a tool's answer only up to about 50 KB; a larger one is
saved to a file that is gone when the run ends. Such a read comes back in
`ReadResult.too_large` instead of `answers`, and `sources/jira.py` asks again
for less (a smaller page, a ticket without its history, fewer comments).

The connection's tool names start with a name that may differ from one user to
the next (`mcp__claude_ai_Atlassian_MCP__` on the first one built against). It
is found the way Slack's is: a run with no tool allowed, and the name of the
tool that ends in `__listJiraIssueComments` in its first line.

Only the current Atlassian connection (`https://mcp.atlassian.com/v2/...`) is
loaded. The older one (`/v1/...`, "Atlassian Rovo") answered every Jira read
with "403" when this was built, and is due to be switched off by Atlassian.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field

from .slack_connector import (PASS_ENV, ReadError, _events, _final_error, _no_start_error, _same_args,
                              _usage, _with_stderr, add_usage, find_claude)

# The tools by the part of the name after the connection's name.
ME = "atlassianUserInfo"
SITES = "getAccessibleAtlassianResources"
SEARCH = "searchJiraIssuesUsingJql"
ISSUE = "getJiraIssue"
COMMENTS = "listJiraIssueComments"

PAGE_SIZE = 20          # tickets asked for in one search (about 35 KB in the full view; never seen by a model)
SMALL_PAGE = 8          # asked for again when a page of PAGE_SIZE was too large to pass on
TICKETS_PER_RUN = 8     # tickets whose details one run reads (two calls each)

TOOL_FOR = {"me": ME, "sites": SITES, "search": SEARCH, "issue": ISSUE, "comments": COMMENTS}

# Atlassian tools that change something. Never allowed, always refused by name.
WRITE_TOOLS = [
    "addOrEditJiraIssueComment",
    "createJiraIssue",
    "editJiraIssue",
    "transitionJiraIssue",
    "executeWrite",
    "executeDestructive",
    "createConfluenceContent",
    "updateConfluenceContent",
    "addGraphContext",
    "showConfluenceEditor",
]

# The tools known to only read. A run may allow nothing outside this list.
KNOWN_READ_ONLY = {ME, SITES, SEARCH, ISSUE, COMMENTS}

# Other Atlassian tools that are not used. Refused too, so the run stays small
# and can do nothing else: `executeRead` and `discover` reach any operation.
UNUSED_TOOLS = [
    "atlassianHome",
    "discover",
    "executeRead",
    "getCollaborators",
    "getConfluenceContent",
    "getGraphContext",
    "getGraphObject",
    "getLoomObjectsContent",
    "getPeopleInfo",
    "getWorkActivity",
    "getWorkPortfolio",
    "search",
    "searchConfluence",
]

# Words that mark a tool as one that changes something. Checked on every tool the
# run is allowed, and on every tool of the connection the run reports it has.
WRITE_WORDS = re.compile(
    r"add|create|update|edit|transition|write|destructive|delete|remove|post|send|upload|"
    r"assign|move|archive|publish|log ?work|worklog",
    re.I,
)

SYSTEM_PROMPT = (
    "You make Jira read calls for a script. In your first and only reply, make every tool call "
    "listed in the user message, all at once, with exactly the arguments given. "
    "Do not add, change or skip a call. Do not write any text."
)
PROBE_SYSTEM_PROMPT = "Reply with the single word ok. Do not call any tool."

# Only Claude's Atlassian connection is loaded; every other connection stays off.
SETTINGS = {"allowedMcpServers": [{"serverUrl": "https://mcp.atlassian.com/v2/*"}]}

RUN_ENV = {
    "MCP_CONNECTION_NONBLOCKING": "false",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "DISABLE_AUTOUPDATER": "1",
    # A ticket with its history is 10 to 45 thousand characters: without this
    # Claude cuts a tool's answer short, and the cut answer is not JSON.
    "MAX_MCP_OUTPUT_TOKENS": "200000",
}


@dataclass
class Query:
    """One planned read. kind: "me", "sites", "search", "issue" or "comments"."""
    label: str
    kind: str
    args: dict


@dataclass
class ReadResult:
    answers: dict = field(default_factory=dict)    # label -> the answer's JSON ("data" taken off)
    usage: dict = field(default_factory=dict)
    too_large: set = field(default_factory=set)    # labels whose answer was too large to pass on


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
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


# ---------- the command line ----------

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
    out = []
    for q in queries:
        short = TOOL_FOR[q.kind]
        if short not in out:
            out.append(short)
    return out


def build_command(claude_path: str, prefix: str, queries) -> list:
    allowed = allowed_shorts(queries)
    check_allowed(allowed)
    denied = [s for s in WRITE_TOOLS + UNUSED_TOOLS + sorted(KNOWN_READ_ONLY) if s not in allowed]
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
    return _base_command(claude_path) + ["--system-prompt", PROBE_SYSTEM_PROMPT, "ok"]


# ---------- reading the run's output ----------

def _first(events, kind, subtype=None):
    return next((e for e in events if e.get("type") == kind
                 and (subtype is None or e.get("subtype") == subtype)), None)


def _last_result(events):
    return next((e for e in reversed(events) if e.get("type") == "result"), None)


def find_prefix(lines) -> str:
    events = _events(lines)
    init = _first(events, "system", "init")
    if init is None:
        raise _no_start_error(_last_result(events))
    return _prefix_from(init, _last_result(events))


def _prefix_from(init, final) -> str:
    hits = sorted({n[: -len(COMMENTS)] for n in init.get("tools") or []
                   if n.startswith("mcp__") and n.endswith("__" + COMMENTS)})
    if not hits:
        raise ReadError(_not_connected(init), "not_connected", _usage(final))
    if len(hits) > 1:
        raise ReadError("more than one Atlassian connection in Claude: " + ", ".join(hits), "error",
                        _usage(final))
    return hits[0]


def _not_connected(init) -> str:
    servers = ", ".join(f"{s.get('name')}: {s.get('status')}" for s in init.get("mcp_servers") or [])
    return ("Claude's Atlassian connection is not available (connections: "
            f"{servers or 'none'}). If this lasts, add the Atlassian connector again in claude.ai, "
            "Settings, Connectors.")


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


# An answer larger than Claude passes on (about 50 KB) is saved by Claude to a
# file, and the run carries only this pointer. The file is deleted when the
# run ends (no session is kept), so it cannot be read back: the read is marked
# too large and the caller asks again for less.
_TOO_LARGE = re.compile(r"\A\s*<persisted-output>")


def too_large(raw: str) -> bool:
    return bool(_TOO_LARGE.match(raw or ""))


def parse_answer(raw: str):
    """The answer's JSON, with the "data" wrapper some tools add taken off."""
    try:
        got = json.loads(raw)
    except ValueError:
        raise ReadError(f"Jira answered in a form the hub cannot read: {raw[:200]!r}", "unreadable")
    if isinstance(got, dict) and set(got) == {"data"} and isinstance(got["data"], dict):
        got = got["data"]
    if not isinstance(got, dict):
        raise ReadError("Jira answered with something that is not an object", "unreadable")
    useful = any(k in got for k in ("issues", "key", "comments", "accountId", "resources"))
    if got.get("error") or (got.get("errors") and not useful):
        raise ReadError(f"Jira answered with an error: {str(got.get('error') or got.get('errors'))[:300]}",
                        "jira_error")
    return got


def read_stream(lines, queries, prefix: str) -> ReadResult:
    """What the planned calls answered. Raises ReadError (with the run's plan
    use in `usage`, so failed runs are counted too)."""
    events = _events(lines)
    init = _first(events, "system", "init")
    final = _last_result(events)
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
    write_present = sorted(t for t in available if t.startswith(prefix) and looks_like_write_tool(short_name(t)))
    if write_present:
        raise ReadError("the Claude run had Atlassian write tools, which should have been refused: "
                        + ", ".join(write_present), "write_tool")
    missing = [s for s in allowed_shorts(queries) if prefix + s not in available]
    if missing:
        raise ReadError(_not_connected(init) + f" Missing: {', '.join(missing)}.", "not_connected")

    calls, unplanned, remaining = {}, [], list(queries)
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
            raw = result_text(c.get("content"))
            if c.get("is_error"):
                raise ReadError(f"the Jira read {q.label} failed: {raw[:300]}", "jira_error")
            if too_large(raw):
                result.too_large.add(q.label)
                continue
            result.answers[q.label] = parse_answer(raw)

    not_answered = [q.label for q in queries if q.label not in result.answers and q.label not in result.too_large]
    if not_answered:
        reason = _final_error(final)
        raise ReadError("these Jira reads were not made or got no answer: " + ", ".join(not_answered)
                        + (f" ({reason})" if reason else ""), "error")
    return result


# ---------- the reader ----------

class ConnectorReader:
    """Reads Jira through Claude's Atlassian connection, one `claude -p` run per batch."""

    name = "connector"

    def __init__(self, claude_path: str = "", cwd: str = "", prefix: str = "", timeout: int = 180):
        self.claude_path = find_claude(claude_path)
        self.cwd = cwd or os.path.join(tempfile.gettempdir(), "ensemble-jira-reader")
        self.prefix = prefix
        self.timeout = timeout
        self.runs = 0
        self.usage: dict = {}

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

    def find_prefix(self) -> str:
        lines, stderr = self._run(build_probe_command(self.claude_path))
        self.usage = add_usage(self.usage, _usage(_last_result(_events(lines))))
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
            self.usage = add_usage(self.usage, e.usage)
            if e.code == "not_connected":
                init = _first(_events(lines), "system", "init")
                if init is not None:
                    try:
                        self.prefix = _prefix_from(init, None)
                    except ReadError:
                        pass
            raise _with_stderr(e, stderr)
        self.usage = add_usage(self.usage, result.usage)
        return result


def make_reader(settings: dict, memory: dict, claude_path: str = "", cwd: str = ""):
    """The one place that picks how Jira is read: Claude's Atlassian connection,
    the only one built. A later reader (a Jira API token) takes the same `Query`
    list and returns the same `ReadResult`."""
    kind = (settings or {}).get("reader") or "connector"
    if kind != "connector":
        raise ReadError(f"the Jira reader {kind!r} is not built yet; use Claude's Atlassian connection",
                        "not_set_up")
    return ConnectorReader(claude_path=claude_path, cwd=cwd, prefix=(memory or {}).get("toolPrefix", ""))
