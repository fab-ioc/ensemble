"""Reading Slack through the person's own Claude Slack connector (#431, GitHub issue 75, step 1).

This is the only file that talks to Slack. :mod:`slack_channel` hands it a
list of planned reads (:class:`Query`) and gets back what each found. Ported
in a small form from #409 (reverted by #413), whose design held:

* One short ``claude -p`` run on Haiku, with only Claude's Slack connector
  loaded (``allowedMcpServers``), no built-in tools (``--tools ""``), and only
  the Slack *read* tools this run needs allowed. Every write tool is refused by
  name, and a run whose tool list still holds one fails.
* The prompt lists every call with its exact arguments. Haiku makes them all in
  its first reply and the run stops there (``--max-turns 1``): the tools run,
  but their answers never go back to a model. The hub reads them straight from
  the run's stream output, so no Slack text reaches a model (cheap, and a
  guest's words cannot steer anything).
* A call that was not planned, or planned and changed, fails the read.

The tool names carry the connector's name (``mcp__claude_ai_Slack__…``); the
prefix is taken from the run's first line, never written in the code.

A stand-in for ``claude`` (tests, or a machine without the connector) is named
by the environment variable ``ENSEMBLE_SLACK_READER``: it gets the same
arguments and answers in the same stream-json shape.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

READ_CHANNEL = "slack_read_channel"
READ_THREAD = "slack_read_thread"
READ_CANVAS = "slack_read_canvas"
TOOL_FOR = {"channel": READ_CHANNEL, "thread": READ_THREAD, "canvas": READ_CANVAS}
READ_TOOLS = set(TOOL_FOR.values())

# Slack tools that change something in Slack: never allowed, always refused by name.
WRITE_TOOLS = [
    "slack_send_message",
    "slack_send_message_draft",
    "slack_schedule_message",
    "slack_add_reaction",
    "slack_create_canvas",
    "slack_update_canvas",
]
# Other read tools this step does not use: refused too, so a run stays small.
UNUSED_TOOLS = [
    "slack_get_reactions", "slack_list_channel_members", "slack_list_user_channels",
    "slack_read_file", "slack_read_user_profile", "slack_search_channels", "slack_search_emojis",
    "slack_search_public", "slack_search_public_and_private", "slack_search_users",
]
# A Slack tool whose name says it changes something. Checked on every tool a
# run reports it has; the three read tools above are the only exceptions.
WRITE_WORDS = re.compile(
    r"send|draft|schedule|post|add|create|update|edit|delete|remove|upload|invite|join|leave|"
    r"kick|archive|rename|set|pin|react|write|mark", re.I)

SYSTEM_PROMPT = (
    "You make Slack read calls for a script. In your first and only reply, make every tool call "
    "listed in the user message, all at once, with exactly the arguments given. "
    "Do not add, change or skip a call. Do not write any text.")
SETTINGS = {"allowedMcpServers": [{"serverUrl": "https://mcp.slack.com/*"}]}
PASS_ENV = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "CLAUDE_CONFIG_DIR")
RUN_ENV = {
    # claude.ai connectors load asynchronously; without this the run may start
    # before Slack is connected and have no Slack tools.
    "MCP_CONNECTION_NONBLOCKING": "false",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "DISABLE_AUTOUPDATER": "1",
}
STAND_IN_ENV = "ENSEMBLE_SLACK_READER"
TIMEOUT_S = 150


class ReadError(Exception):
    """A read failed. ``code``: not_connected | not_signed_in | write_tool |
    unplanned_call | slack_error | unreadable | timeout | error."""

    def __init__(self, reason: str, code: str = "error", raw: str = ""):
        super().__init__(reason)
        self.code = code
        self.raw = raw


class ParseError(Exception):
    """An answer did not have the expected layout."""


@dataclass
class Query:
    label: str
    kind: str          # channel | thread | canvas
    args: dict


@dataclass
class Message:
    author_id: str
    author: str
    ts: str
    text: str
    bot: bool = False


@dataclass
class Result:
    answers: dict = field(default_factory=dict)    # label -> parsed answer
    raw: dict = field(default_factory=dict)        # label -> the tool's text
    errors: dict = field(default_factory=dict)     # label -> ReadError (slack_error | unreadable)
    cost: float = 0.0


# ---------- read-only rules ----------

def short_name(name: str) -> str:
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def is_write_tool(short: str) -> bool:
    return short in WRITE_TOOLS or (short not in READ_TOOLS and bool(WRITE_WORDS.search(short)))


def _is_slack_tool(name: str) -> bool:
    return name.startswith("mcp__") and short_name(name).startswith("slack_")


# ---------- the command line ----------

def find_claude() -> str:
    given = os.environ.get(STAND_IN_ENV, "").strip()
    if given:
        return given
    for c in (shutil.which("claude"), os.path.expanduser("~/.local/bin/claude")):
        if c and os.path.exists(c):
            return c
    return "claude"


def run_env() -> dict:
    env = {k: os.environ[k] for k in PASS_ENV if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env.update(RUN_ENV)
    return env


def build_command(claude_path: str, prefix: str, queries) -> list:
    """The ``claude -p`` command for ``queries``. Without a prefix yet, the
    tools are named by a wildcard-free guess, ``mcp__claude_ai_Slack__``: the
    run's first line says the real one, and a run that had it under another
    name fails as not connected and the next one uses it."""
    allowed = list(dict.fromkeys(TOOL_FOR[q.kind] for q in queries))
    if any(is_write_tool(s) for s in allowed):
        raise ValueError("a write tool in the allowed list")
    denied = [s for s in WRITE_TOOLS + UNUSED_TOOLS + sorted(READ_TOOLS) if s not in allowed]
    lines = [f"Make these {len(queries)} tool calls:"]
    for i, q in enumerate(queries, 1):
        lines.append(f"{i}. {TOOL_FOR[q.kind]} {json.dumps(q.args, separators=(',', ':'))}")
    return [claude_path, "-p", "--model", "haiku", "--tools", "", "--setting-sources", "",
            "--settings", json.dumps(SETTINGS), "--permission-mode", "dontAsk",
            "--max-turns", "1", "--no-session-persistence", "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", ",".join(prefix + s for s in allowed),
            "--disallowedTools", ",".join(prefix + s for s in dict.fromkeys(denied)),
            "--system-prompt", SYSTEM_PROMPT, "\n".join(lines)]


# ---------- reading the run's output ----------

def _events(lines) -> list:
    out = []
    for line in lines:
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _final_error(final) -> str:
    if not final or final.get("subtype") == "error_max_turns":
        return ""   # expected: the run stops after Haiku's calls
    if final.get("is_error") or final.get("subtype") not in (None, "success"):
        return str(final.get("result") or final.get("subtype") or "unknown error")[:400]
    return ""


def prefix_of(init: dict) -> str:
    hits = sorted({n[: -len(READ_CHANNEL)] for n in init.get("tools") or []
                   if n.startswith("mcp__") and n.endswith("__" + READ_CHANNEL)})
    return hits[0] if len(hits) == 1 else ""


def _not_connected(init: dict) -> ReadError:
    servers = init.get("mcp_servers") or []
    slack = [s for s in servers if "slack" in str(s.get("name") or "").lower()]
    if any(str(s.get("status")) == "needs-auth" for s in slack):
        return ReadError("Claude's Slack connector is not signed in. Sign in to Slack in claude.ai, "
                         "Settings, Connectors.", "not_signed_in")
    names = ", ".join(f"{s.get('name')}: {s.get('status')}" for s in servers) or "none"
    return ReadError("Claude has no Slack connector (connectors: " + names + "). Connect Slack in "
                     "claude.ai, Settings, Connectors, with the account Claude Code signs in with.",
                     "not_connected")


def _norm(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v, sort_keys=True)
    return str(v)


def _same_args(planned: dict, made: dict) -> bool:
    return set(planned) == set(made) and all(_norm(planned[k]) == _norm(made[k]) for k in planned)


def read_stream(lines, queries, prefix: str) -> tuple[Result, str]:
    """(what the planned calls found, the tool prefix the run had). Raises ReadError."""
    events = _events(lines)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    final = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if init is None:
        why = _final_error(final)
        if re.search(r"log ?in|logged in|auth", why, re.I):
            raise ReadError(f"Claude is not signed in ({why})", "not_signed_in")
        raise ReadError(why or "Claude did not start (no first line in its output)")
    tools = set(init.get("tools") or [])
    writes = sorted(t for t in tools if _is_slack_tool(t) and is_write_tool(short_name(t)))
    if writes:
        raise ReadError("the Claude run had Slack write tools, which must be refused: " + ", ".join(writes),
                        "write_tool")
    found = prefix_of(init) or prefix
    if any(found + TOOL_FOR[q.kind] not in tools for q in queries):
        e = _not_connected(init)
        e.raw = found      # the prefix seen, for the next run
        raise e
    res = Result(cost=float((final or {}).get("total_cost_usd") or 0))
    calls, unplanned, left = {}, [], list(queries)
    for e in events:
        if e.get("type") != "assistant":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            q = next((q for q in left if found + TOOL_FOR[q.kind] == c.get("name")
                      and _same_args(q.args, c.get("input") or {})), None)
            if q is None:
                unplanned.append(f"{c.get('name')} {json.dumps(c.get('input'))}")
            else:
                left.remove(q)
                calls[c.get("id")] = q
    if unplanned:
        raise ReadError("Haiku made calls it was not asked for: " + "; ".join(unplanned)[:400],
                        "unplanned_call")
    for e in events:
        content = (e.get("message") or {}).get("content") if e.get("type") == "user" else None
        for c in content if isinstance(content, list) else ():
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            q = calls.get(c.get("tool_use_id"))
            if q is None:
                continue
            raw = tool_result_text(c.get("content"))
            res.raw[q.label] = raw
            # One read that failed (a deleted thread, a canvas in an unknown
            # layout) is that read's error, not the run's: the caller decides.
            if c.get("is_error"):
                res.errors[q.label] = ReadError(f"the Slack read {q.label} failed: {raw[:300]}",
                                                "slack_error", raw)
                continue
            try:
                res.answers[q.label] = PARSE[q.kind](raw)
            except ParseError as err:
                res.errors[q.label] = ReadError(
                    f"Slack answered {q.label} in a form the hub cannot read: {err}", "unreadable", raw)
    missing = [q.label for q in queries if q.label not in res.answers and q.label not in res.errors]
    if missing:
        why = _final_error(final)
        raise ReadError("these Slack reads got no answer: " + ", ".join(missing) + (f" ({why})" if why else ""))
    return res, found


def run(queries, prefix: str) -> tuple[Result, str]:
    """Make ``queries`` in one run. Returns (result, tool prefix). Raises ReadError."""
    if not queries:
        return Result(), prefix
    cwd = os.path.join(tempfile.gettempdir(), "ensemble-slack-reader")
    os.makedirs(cwd, exist_ok=True)
    cmd = build_command(find_claude(), prefix or "mcp__claude_ai_Slack__", queries)
    try:
        p = subprocess.run(cmd, cwd=cwd, env=run_env(), capture_output=True, text=True,
                           timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ReadError(f"the Claude run took longer than {TIMEOUT_S} s and was stopped", "timeout")
    except OSError as e:
        raise ReadError(f"could not start Claude: {e}")
    try:
        return read_stream(p.stdout.splitlines(), queries, prefix)
    except ReadError as e:
        tail = " | ".join((p.stderr or "").strip().splitlines()[-2:])
        if tail and e.code == "error":
            e.args = (f"{e} (Claude said: {tail[:200]})",)
        raise


# ---------- the answers' layouts ----------

def _unwrap(raw: str, key: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        raise ParseError(f"answer is not JSON: {raw[:200]!r}")
    if not isinstance(data, dict) or not isinstance(data.get(key), str):
        raise ParseError(f"answer has no {key!r}: {raw[:200]!r}")
    return data[key]


_CHANNEL_MSG = re.compile(
    r"^=== Message from (?P<name>.*?) \((?P<uid>[A-Z0-9]+)\) at (?P<time>.*?) ===[ \t]*\n"
    r"Message TS: (?P<ts>\d+\.\d+)[ \t]*\n", re.M)
_CHANNEL_HEAD = re.compile(r"^Channel: (?P<name>.*?) \((?:ID: )?(?P<id>[A-Z0-9]+)\)[ \t]*$", re.M)
# Lines Slack adds after a message's text: reactions, files, a thread's summary.
_TRAILER = re.compile(r"(?:\n(?:Thread|Reactions|Files?|Replies|Reply count):[^\n]*)+\s*$")


def parse_channel(raw: str) -> dict:
    """slack_read_channel (detailed): {"name": channel name, "messages": [Message], oldest first}."""
    text = _unwrap(raw, "messages")
    head = _CHANNEL_HEAD.search(text)
    ms = list(_CHANNEL_MSG.finditer(text))
    if not ms and "=== Message" in text:
        raise ParseError(f"cannot read the messages in {text[:200]!r}")
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = _TRAILER.sub("", text[m.end():end].strip("\n")).strip("\n")
        uid = m.group("uid")
        out.append(Message(uid, m.group("name").strip(), m.group("ts"), body,
                           bot=uid == "USLACK" or uid.startswith("B")))
    out.sort(key=lambda x: float(x.ts))
    return {"name": head.group("name").strip().lstrip("#") if head else "", "messages": out}


_THREAD_SPLIT = re.compile(r"^(?:=== THREAD PARENT MESSAGE ===|--- Reply \d+ of \d+ ---)[ \t]*\n", re.M)
_THREAD_REPLIES = re.compile(r"\n*^=== THREAD REPLIES \(\d+ total\) ===[ \t]*$", re.M)
_THREAD_FROM = re.compile(
    r"^From: (?P<name>.*?)\s*(?:<[^>]*>)?\s*\((?:ID: )?(?P<uid>[A-Z0-9]+)\)\s*(?P<bot>\[BOT\])?[ \t]*$", re.M)
_THREAD_TS = re.compile(r"^Message TS: (?P<ts>\d+\.\d+)[ \t]*$", re.M)
_THREAD_TIME = re.compile(r"^Time: (?P<time>.*?)[ \t]*$", re.M)


def parse_thread(raw: str) -> dict:
    """slack_read_thread (detailed): {"parent": Message | None, "replies": [Message], oldest first}."""
    text = _unwrap(raw, "messages")
    blocks = _THREAD_SPLIT.split(text)
    if len(blocks) < 2:
        if text.strip():
            raise ParseError(f"cannot read the thread in {text[:200]!r}")
        return {"parent": None, "replies": []}
    msgs = []
    for block in blocks[1:]:
        block = _THREAD_REPLIES.split(block)[0]
        fm, tm = _THREAD_FROM.search(block), _THREAD_TS.search(block)
        if not fm or not tm:
            raise ParseError(f"thread message without sender or time: {block[:200]!r}")
        when = _THREAD_TIME.search(block)
        start = max(fm.end(), tm.end(), when.end() if when else 0)
        body = _TRAILER.sub("", block[start:].strip("\n")).strip("\n")
        uid = fm.group("uid")
        msgs.append(Message(uid, fm.group("name").strip(), tm.group("ts"), body,
                            bot=bool(fm.group("bot")) or uid == "USLACK" or uid.startswith("B")))
    msgs.sort(key=lambda x: float(x.ts))
    return {"parent": msgs[0], "replies": msgs[1:]}


_TS = re.compile(r"\b(\d{9,11}\.\d{6})\b")
_CONV = re.compile(r"\b([CGD][A-Z0-9]{8,})\b")
_TITLE = re.compile(r"^(?:#\s+|Title:\s*)(?P<t>\S.*?)\s*$", re.M)
_COMMENTS_AT = re.compile(r"^.*\bcomments?\b.*$", re.M | re.I)


def parse_canvas(raw: str) -> dict:
    """slack_read_canvas: {"title", "threads": [(conversation id, thread ts)]},
    the canvas's comment threads. **Its layout has not been seen yet**
    (#431: no Slack connector on the hub machine): this reads it tolerantly,
    a comment thread being any line, after the first that names comments, that
    holds a conversation id and a message time. An answer that names comments
    but holds no such pair is not guessed at: it fails (``ParseError``), so
    the channel's state says the canvas cannot be read and the hub keeps the
    raw answer to fix this by."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    if isinstance(data, dict):
        text = "\n".join(v if isinstance(v, str) else json.dumps(v) for v in data.values())
    else:
        text = data if isinstance(data, str) else json.dumps(data)
    tm = _TITLE.search(text)
    head = _COMMENTS_AT.search(text)
    threads = []
    if head:
        for line in text[head.start():].splitlines():
            ts, conv = _TS.search(line), _CONV.search(line)
            if ts and conv and (conv.group(1), ts.group(1)) not in threads:
                threads.append((conv.group(1), ts.group(1)))
        if not threads and not re.search(r"\b(?:no|0)\s+comments?\b", text, re.I):
            raise ParseError("the canvas names comments but no comment thread could be read from it")
    return {"title": tm.group("t") if tm else "", "threads": threads}


PARSE = {"channel": parse_channel, "thread": parse_thread, "canvas": parse_canvas}


def link_for(workspace: str, channel_id: str, ts: str, thread_ts: str = "") -> str:
    """A message's link built the way Slack builds them; "" without a workspace."""
    if not workspace or not ts:
        return ""
    url = f"https://{workspace}/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        url += f"?thread_ts={thread_ts}&cid={channel_id}"
    return url
