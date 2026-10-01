#!/usr/bin/env python3
"""Where the tokens go, by wake: who is woken, by what, and what the wake cost.

Every model call an Ensemble agent makes is recorded with its token usage in
the agent's own transcript (Claude: ``~/.claude/projects/**/*.jsonl``, the
``usage`` of each assistant row; Codex: ``~/.codex/sessions/**/*.jsonl``, the
``token_count`` events).  This tool reads those for a window of days and
groups the calls by the **turn** that caused them: the user-side line the
agent answered.  For a PO that line is almost always typed by the hub
(``[digest]``, ``[report]``, ``[due]``, ``[points]`` ... — ``dashboard.
HUB_INPUT_KINDS``) and each such wake re-sends the whole conversation, so
wakes are where a PO's tokens go.

Per turn it records the tokens by kind (fresh input, cache writes, cache
reads, output), the model calls, which tools ran, whether anything was
written for someone (a message, a report, a task change, an edit, a shell
command that is not a read) and whether the CEO was told anything (a
``chat_send`` to the user; in a one-agent PO room, the reply itself, which
the chat page shows).  A wake that wrote nothing and told the CEO nothing is
a **no-op wake**: its whole cost was re-reading the history to decide that
nothing was needed.

Scope and roles come from ``measure_internal_communication.conversation_scope``:
project PO rooms (role ``po``), task rooms (``owner``, ``reviewer``).  The
hub's own helper calls (``digest.write_up``, ``claude -p --model haiku``) keep
no transcript (``--no-session-persistence``); they are estimated from the
digests delivered to the PO rooms, at the size of the facts they were given.

Reviewer rounds: each review session of a task room is a round (the order of
its ``reviews`` records); a round whose shell commands diff the branch
against its base (``git diff main...``, ``git log -p main..``) re-read the
whole diff rather than the commits since the previous round.

Read-only: transcripts and rooms are opened for reading only.  ``--json``
writes the full report; ``--turns FILE`` dumps every turn as JSON lines for
further analysis.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import measure_task_tool_results as base  # noqa: E402
import measure_tool_results_by_tool as bytool  # noqa: E402
import measure_internal_communication as mic  # noqa: E402
import dashboard  # noqa: E402

ROLES = ("po", "owner", "reviewer")
AGENTS = ("claude", "codex")
TOKEN_KINDS = ("input", "cacheWrite", "cacheRead", "output")
ENSEMBLE_PREFIX = "mcp__ensemble__"
# Ensemble tools whose call changes nothing: reading them is not an action.
READ_ONLY_ENSEMBLE = frozenset({
    "chat_read", "chat_whoami", "ensemble_whoami", "ensemble_get_task", "ensemble_list_tasks",
    "ensemble_list_attention", "ensemble_plan_usage", "ensemble_get_roadmap", "ensemble_list_projects",
    "ensemble_get_project", "ensemble_list_sends", "review_log",
})
READ_ONLY_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "TodoRead", "WebFetch", "WebSearch",
                             "ToolSearch", "Monitor", "ListAgents", "read_file", "list_dir",
                             "grep_files", "view_image"})
# Shell words that only look: a command made of these alone is a read.
_READ_WORDS = frozenset({
    "cat", "sed", "head", "tail", "less", "more", "type", "gc", "get-content", "ls", "dir",
    "get-childitem", "wc", "grep", "rg", "find", "findstr", "select-string", "echo", "pwd", "which",
    "where", "test", "stat", "file", "tree", "date", "sort", "uniq", "cut", "tr", "awk", "diff",
    "jq", "rtk", "true", "cd", "set-location", "measure-object", "select-object", "sleep",
})
_GIT_READ = frozenset({"log", "status", "diff", "show", "branch", "rev-parse", "ls-files", "blame",
                       "rev-list", "describe", "cat-file", "name-rev", "merge-base", "config",
                       "remote", "shortlog", "stash", "worktree", "fetch", "tag", "for-each-ref",
                       "check-ignore", "ls-tree", "symbolic-ref"})
_WHOLE_DIFF = re.compile(r"\bgit\s+(?:--no-pager\s+)?(?:diff|log\b[^|;&]*?-p|show)\b[^|;&\n]*?"
                         r"(?:\bmain\b|\bmaster\b|\borigin/\w+|merge-base|--stat\s*$)")
_RANGE_DIFF = re.compile(r"\bgit\s+(?:--no-pager\s+)?(?:diff|log|show|range-diff)\b[^|;&\n]*?\b[0-9a-f]{7,40}\b")
_REVIEW_VERDICT = re.compile(r"^review \d+ \((.*?)\)$")
# Per digest, what the hub gives the helper model (digest._PROMPT) plus the
# facts, and what it writes; the facts are not stored, so they are taken as
# the delivered text times this factor (facts list every open task, the
# digest at most 8 lines of them).
HELPER_PROMPT_TOKENS = 220
HELPER_FACTS_FACTOR = 3.0


def _tokens(usage: dict) -> dict:
    return {"input": int(usage.get("input_tokens") or 0),
            "cacheWrite": int(usage.get("cache_creation_input_tokens") or 0),
            "cacheRead": int(usage.get("cache_read_input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0)}


def _codex_tokens(usage: dict) -> dict:
    total_in = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    return {"input": max(0, total_in - cached), "cacheRead": cached,
            "cacheWrite": int(usage.get("cache_write_input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0)}


def _zero() -> dict:
    return {k: 0 for k in TOKEN_KINDS}


def _add_tokens(into: dict, more: dict) -> None:
    for k in TOKEN_KINDS:
        into[k] += more.get(k, 0)


def _total(t: dict) -> int:
    return sum(t.get(k, 0) for k in TOKEN_KINDS)


def _unwrap(text: str) -> str:
    return mic._unwrap_pasted(text or "").lstrip()


def wake_kind(text: str, role: str, first_done: bool) -> tuple[str, dict]:
    """(cause, extra) of a user-side turn.  Causes are the hub kinds of
    ``dashboard.HUB_INPUT_KINDS`` (``digest``, ``report``, ``due`` ...),
    ``pomsg`` (another project's PO), ``frompo`` (the PO's line to a task),
    ``first`` (the conversation's first prompt), ``ceo`` (a person's own
    words), ``harness`` (a CLI note alone), ``compact`` (a compaction)."""
    s = _unwrap(text)
    if s.startswith("<") or s.startswith("Caveat:"):
        return "harness", {}
    info = dashboard.hub_input_kind(s)
    kind = info.get("kind", "human")
    if kind == "report":
        rk = str(info.get("reportKind") or "")
        m = _REVIEW_VERDICT.match(rk)
        return "report", {"reportKind": "review (" + m.group(1) + ")" if m else rk,
                          "taskId": info.get("taskId") or ""}
    if kind != "human":
        if kind in {"rotation", "madepo"} and not first_done:
            return "first", {"kind": kind}
        return kind, {}
    if s.startswith(dashboard.PO_MESSAGE_PREFIX):
        return "frompo", {}
    if not first_done:
        return "first", {"kind": "review brief" if mic.REVIEW_BRIEF_MARK in s else "spec"}
    return "ceo", {}


def shell_is_read(command: str) -> bool:
    """Whether a shell command only looks (every segment starts with a
    reading word, or a reading git subcommand)."""
    segments = mic._segments(command)
    if not segments:
        return True
    for seg in segments:
        words = [mic._unquote(w) for w in seg]
        while words and (words[0].lower() in {"rtk", "sudo", "time"} or "=" in words[0]):
            words = words[1:]
        if not words:
            continue
        head = words[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if head in {"bash", "sh", "zsh", "pwsh", "powershell"} and len(words) > 2                 and words[1].startswith("-"):
            # Codex runs every command as ``bash -lc <script>``: judge the script.
            if not shell_is_read(" ".join(words[2:])):
                return False
            continue
        if head in {"git", "git.exe"}:
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub.lower() not in _GIT_READ or (sub.lower() == "stash" and len(words) > 2
                                                 and words[2] not in ("list", "show")):
                return False
            continue
        if head not in _READ_WORDS:
            return False
        if head == "rtk" and len(words) > 1:
            return shell_is_read(" ".join(words[1:]))
    return True


def _tool_call_kind(tool: str, inp: Any) -> tuple[str, bool, bool]:
    """(tool short name, is_action, tells_user) of one tool call."""
    name = tool[len(ENSEMBLE_PREFIX):] if tool.startswith(ENSEMBLE_PREFIX) else tool
    inp = inp if isinstance(inp, dict) else {}
    if name == "chat_send":
        return name, True, str(inp.get("to") or "").strip().lower() == "user"
    if name in READ_ONLY_ENSEMBLE:
        return name, False, False
    if name == "ensemble_points":
        return name, str(inp.get("action") or "list") != "list", False
    if name in READ_ONLY_TOOLS:
        return name, False, False
    if name.lower() in bytool.SHELL_TOOLS or name in bytool.CODEX_SHELL:
        cmd = inp.get("command") or inp.get("cmd") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        return name, not shell_is_read(str(cmd)), False
    return name, True, False


class Turn:
    __slots__ = ("agent", "role", "room", "task", "title", "project", "session", "when", "day",
                 "cause", "extra", "tokens", "calls", "tools", "actions", "toldUser", "replyChars",
                 "preview", "commands", "subagent")

    def __init__(self, agent: str, role: str, room: dict, session: str, when: datetime,
                 tz, cause: str, extra: dict, preview: str, subagent: bool = False) -> None:
        self.agent, self.role, self.session = agent, role, session
        self.room, self.task = room.get("room", ""), room.get("task")
        self.title, self.project = room.get("title", ""), room.get("project", "")
        self.when = when
        self.day = when.astimezone(tz).date().isoformat()
        self.cause, self.extra, self.preview = cause, extra, preview
        self.tokens = _zero()
        self.calls = 0
        self.tools: dict[str, int] = defaultdict(int)
        self.actions = 0
        self.toldUser = False
        self.replyChars = 0
        self.commands: list[str] = []
        self.subagent = subagent

    def tool(self, name: str, inp: Any) -> None:
        short, action, told = _tool_call_kind(name, inp)
        self.tools[short] += 1
        self.actions += int(action)
        self.toldUser |= told
        inp = inp if isinstance(inp, dict) else {}
        if short.lower() in bytool.SHELL_TOOLS or short in bytool.CODEX_SHELL:
            cmd = inp.get("command") or inp.get("cmd") or ""
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            if cmd:
                self.commands.append(str(cmd))

    def noop(self) -> bool:
        """Nothing written for anyone, the CEO not told (a one-agent PO's
        reply is what the chat shows: a short one is an acknowledgement)."""
        return self.actions == 0 and not self.toldUser and self.replyChars < 160

    def as_dict(self) -> dict:
        return {"agent": self.agent, "role": self.role, "room": self.room, "task": self.task,
                "title": self.title, "project": self.project, "session": self.session,
                "when": self.when.isoformat(), "day": self.day, "cause": self.cause, **self.extra,
                "tokens": dict(self.tokens), "total": _total(self.tokens), "calls": self.calls,
                "tools": dict(self.tools), "actions": self.actions, "toldUser": self.toldUser,
                "replyChars": self.replyChars, "noop": self.noop(), "preview": self.preview,
                "subagent": self.subagent, "wholeDiff": self.whole_diff()}

    def whole_diff(self) -> bool:
        return any(_WHOLE_DIFF.search(c) for c in self.commands)


def _in_window(when: datetime | None, start: datetime, end: datetime) -> bool:
    return when is not None and start <= when < end


# --- Claude ------------------------------------------------------------------

def scan_claude(path: Path, start: datetime, end: datetime, role: str, room: dict,
                agent: str = "claude") -> list[Turn]:
    turns: list[Turn] = []
    current: dict[str, Turn | None] = defaultdict(lambda: None)
    seen_requests: set[str] = set()
    first_done = False
    sid = path.stem
    tz = end.tzinfo
    for row in base._json_lines(path):
        rtype = row.get("type")
        if rtype not in {"assistant", "user"}:
            continue
        when = base._timestamp(row.get("timestamp"))
        key = str(row.get("agentId") or "")
        message = row.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        blocks = [b for b in content if isinstance(b, dict)]
        if rtype == "user":
            if row.get("isCompactSummary"):
                if _in_window(when, start, end):
                    current[key] = Turn(agent, role, room, sid, when, tz, "compact", {}, "", bool(key))
                    turns.append(current[key])
                continue
            if any(b.get("type") == "tool_result" for b in blocks):
                continue
            text = "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
            if not text.strip():
                continue
            cause, extra = wake_kind(text, role, first_done)
            if cause == "first":
                first_done = True
            if _in_window(when, start, end):
                current[key] = Turn(agent, role, room, sid, when, tz, cause, extra,
                                    bytool._one_line(_unwrap(text), 160), bool(key))
                turns.append(current[key])
            else:
                current[key] = None
            continue
        turn = current[key]
        if turn is None:
            continue
        rid = str(row.get("requestId") or row.get("uuid") or id(row))
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
        if usage and rid not in seen_requests:
            seen_requests.add(rid)
            turn.calls += 1
            _add_tokens(turn.tokens, _tokens(usage))
        for b in blocks:
            if b.get("type") == "tool_use":
                turn.tool(str(b.get("name") or ""), b.get("input"))
            elif b.get("type") == "text" and message.get("stop_reason") != "tool_use":
                turn.replyChars = len(str(b.get("text") or ""))
    return turns


# --- Codex -------------------------------------------------------------------

def scan_codex(path: Path, start: datetime, end: datetime, role: str, room: dict) -> list[Turn]:
    turns: list[Turn] = []
    turn: Turn | None = None
    sid = ""
    first_done = False
    tz = end.tzinfo
    last_user_at: datetime | None = None
    for row in base._json_lines(path):
        rtype = row.get("type")
        payload = row.get("payload") or {}
        when = base._timestamp(row.get("timestamp"))
        if rtype == "session_meta":
            sid = str(payload.get("id") or "") or path.stem
            continue
        if rtype == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
            if turn is not None and isinstance(usage, dict):
                turn.calls += 1
                _add_tokens(turn.tokens, _codex_tokens(usage))
            continue
        if rtype != "response_item":
            continue
        ptype = payload.get("type")
        if ptype == "message" and payload.get("role") == "user":
            text = base._codex_user_text(payload)
            cause, extra = wake_kind(text, role, first_done)
            # Codex sends its environment notes as user messages beside the
            # prompt: a harness note right after another user message joins
            # that turn; a prompt right after a harness note replaces it.
            adjacent = last_user_at is not None and when is not None \
                and (when - last_user_at) < timedelta(seconds=5)
            last_user_at = when
            if cause == "harness" and adjacent and turn is not None:
                continue
            if cause == "first":
                first_done = True
            if _in_window(when, start, end):
                new = Turn("codex", role, room, sid, when, tz, cause, extra,
                           bytool._one_line(_unwrap(text), 160))
                if adjacent and turn is not None and turn.cause == "harness" and turn.calls == 0:
                    turns[-1] = new
                else:
                    turns.append(new)
                turn = new
            else:
                turn = None
            continue
        if turn is None:
            continue
        if ptype in {"function_call", "custom_tool_call"}:
            name, _preview, commands = bytool.codex_tool(payload)
            # Turn.tool keeps the commands (one shell call, joined).
            turn.tool(name, {"command": " && ".join(commands)} if commands else {})
        elif ptype == "message" and payload.get("role") == "assistant":
            text = "\n".join(str(b.get("text") or "") for b in payload.get("content") or []
                             if isinstance(b, dict))
            turn.replyChars = len(text)
    return turns


# --- scope -----------------------------------------------------------------

def _claude_session_of(path: Path) -> str:
    """The session id a transcript belongs to: its own stem, or for a
    subagent file under ``<session>/subagents/``, that directory's name."""
    stem = path.stem
    if not stem.startswith("agent-"):
        return stem
    for part in reversed(path.parts[:-1]):
        if re.fullmatch(r"[0-9a-f-]{36}", part):
            return part
    return stem


def collect(home: Path, start: datetime, end: datetime, progress=None) -> tuple[list[Turn], dict]:
    scope = mic.conversation_scope(home)
    turns: list[Turn] = []
    files = {"claude": 0, "codex": 0}
    stale_cutoff = (start - timedelta(days=1)).timestamp()
    for path in base._glob(home / ".claude" / "projects", "**/*.jsonl"):
        try:
            if path.stat().st_mtime < stale_cutoff:
                continue
        except OSError:
            continue
        sid = _claude_session_of(path)
        known = scope["sessions"].get(sid)
        if not known:
            continue
        kind = scope["kinds"].get(sid, "claude")
        if kind != "claude":
            continue
        files["claude"] += 1
        found = scan_claude(path, start, end, known["role"], known["room"])
        if path.stem.startswith("agent-"):
            for t in found:
                t.subagent = True
        turns.extend(found)
        if progress:
            progress(files)
    for path in base._glob(home / ".codex" / "sessions", "**/*.jsonl"):
        try:
            if path.stat().st_mtime < stale_cutoff:
                continue
        except OSError:
            continue
        found_role = mic._codex_role(path, scope)
        if not found_role:
            continue
        files["codex"] += 1
        turns.extend(scan_codex(path, start, end, found_role[0], found_role[1]))
        if progress:
            progress(files)
    return turns, {"files": files, "rooms": scope["rooms"], "sessions": scope["sessions"]}


# --- the hub's helper calls --------------------------------------------------

def helper_estimate(home: Path, start: datetime, end: datetime) -> dict:
    """The digests delivered in the window, from the PO rooms' messages, and
    the tokens their write-up is estimated to have cost (Haiku, no transcript)."""
    po_rooms = mic._po_room_projects(home)
    per_day: dict[str, dict] = defaultdict(lambda: {"digests": 0, "tokens": 0})
    count = 0
    for rid in po_rooms:
        path = home / ".ensemble" / "rooms" / f"{rid}.json"
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for m in room.get("messages") or []:
            if not isinstance(m, dict) or m.get("from") != "ensemble":
                continue
            text = str(m.get("text") or "")
            if not text.startswith("**Progress digest**"):
                continue
            ts = float(m.get("ts") or 0)
            when = datetime.fromtimestamp(ts, tz=end.tzinfo) if ts else None
            if not _in_window(when, start, end):
                continue
            body = text.split("\n", 1)[1] if "\n" in text else ""
            out_tokens = math.ceil(len(body) / 4)
            tokens = HELPER_PROMPT_TOKENS + math.ceil(out_tokens * HELPER_FACTS_FACTOR) + out_tokens
            day = when.date().isoformat()
            per_day[day]["digests"] += 1
            per_day[day]["tokens"] += tokens
            count += 1
    return {"digests": count, "tokens": sum(d["tokens"] for d in per_day.values()),
            "perDay": dict(sorted(per_day.items()))}


# --- aggregation -------------------------------------------------------------

def _agg(turns: Iterable[Turn]) -> dict:
    out = {"turns": 0, "calls": 0, "tokens": _zero(), "noops": 0, "noopTokens": 0, "toldUser": 0}
    totals: list[int] = []
    for t in turns:
        out["turns"] += 1
        out["calls"] += t.calls
        _add_tokens(out["tokens"], t.tokens)
        total = _total(t.tokens)
        totals.append(total)
        if t.noop():
            out["noops"] += 1
            out["noopTokens"] += total
        out["toldUser"] += int(t.toldUser)
    out["total"] = _total(out["tokens"])
    out["perTurn"] = round(out["total"] / out["turns"]) if out["turns"] else 0
    out["medianTurn"] = round(statistics.median(totals)) if totals else 0
    return out


def _cause_label(t: Turn) -> str:
    if t.cause == "report":
        return f"report: {t.extra.get('reportKind') or '?'}"
    if t.cause == "first":
        return f"first: {t.extra.get('kind') or '?'}"
    if t.cause in {"resumed", "restart", "helper"}:
        return "restart note: " + t.cause
    return t.cause


def review_rounds(turns: list[Turn], sessions: dict) -> dict:
    """Per task: review rounds (reviewer sessions in time order), tokens per
    round, which re-read the whole diff."""
    by_session: dict[str, list[Turn]] = defaultdict(list)
    for t in turns:
        if t.role == "reviewer":
            by_session[t.session].append(t)
    per_task: dict[str, list[dict]] = defaultdict(list)
    for sid, ts in by_session.items():
        tokens = _zero()
        for t in ts:
            _add_tokens(tokens, t.tokens)
        first = min(ts, key=lambda t: t.when)
        per_task[str(first.task or first.room)].append({
            "session": sid, "agent": first.agent, "when": first.when.isoformat(),
            "title": first.title, "tokens": tokens, "total": _total(tokens),
            "calls": sum(t.calls for t in ts),
            "wholeDiff": any(t.whole_diff() for t in ts),
            "rangeDiff": any(_RANGE_DIFF.search(c) for t in ts for c in t.commands),
        })
    rows = []
    for task, rounds in per_task.items():
        rounds.sort(key=lambda r: r["when"])
        for i, r in enumerate(rounds, 1):
            r["round"] = i
        rows.append({"task": task, "title": rounds[0]["title"], "rounds": len(rounds),
                     "tokens": sum(r["total"] for r in rounds),
                     "wholeDiffRounds": sum(1 for r in rounds if r["wholeDiff"]),
                     "wholeDiffLater": sum(1 for r in rounds if r["wholeDiff"] and r["round"] > 1),
                     "details": rounds})
    rows.sort(key=lambda r: -r["tokens"])
    all_rounds = [r for row in rows for r in row["details"]]
    later = [r for r in all_rounds if r["round"] > 1]
    return {
        "tasks": len(rows),
        "rounds": len(all_rounds),
        "roundsPerTask": round(len(all_rounds) / len(rows), 2) if rows else 0,
        "tokensPerRound": round(sum(r["total"] for r in all_rounds) / len(all_rounds)) if all_rounds else 0,
        "tokensPerRoundByNo": {
            str(n): {"rounds": len(g), "perRound": round(sum(r["total"] for r in g) / len(g))}
            for n, g in sorted(_group_by(all_rounds, lambda r: min(r["round"], 4)).items())},
        "laterRounds": len(later),
        "laterWholeDiff": sum(1 for r in later if r["wholeDiff"]),
        "laterWholeDiffTokens": sum(r["total"] for r in later if r["wholeDiff"]),
        "byTask": rows,
    }


def _group_by(items: Iterable, key) -> dict:
    out: dict = defaultdict(list)
    for it in items:
        out[key(it)].append(it)
    return out


def aggregate(turns: list[Turn], meta: dict, helper: dict, start: datetime, end: datetime) -> dict:
    days = sorted({t.day for t in turns} | set(helper["perDay"]))
    by_day_role: dict[str, dict] = {}
    for day in days:
        row: dict[str, Any] = {}
        for agent in AGENTS:
            for role in ROLES:
                g = _agg(t for t in turns if t.day == day and t.agent == agent and t.role == role)
                if g["turns"]:
                    row[f"{agent}/{role}"] = g
        h = helper["perDay"].get(day)
        if h:
            row["hub helper (est.)"] = {"turns": h["digests"], "total": h["tokens"],
                                       "tokens": {"input": h["tokens"], "cacheWrite": 0,
                                                  "cacheRead": 0, "output": 0}}
        by_day_role[day] = row
    by_role_agent = {f"{agent}/{role}": _agg(t for t in turns if t.agent == agent and t.role == role)
                     for agent in AGENTS for role in ROLES}
    by_role_agent = {k: v for k, v in by_role_agent.items() if v["turns"]}
    po_turns = [t for t in turns if t.role == "po" and not t.subagent]
    po_by_cause = {}
    for label, group in _group_by(po_turns, _cause_label).items():
        g = _agg(group)
        g["noopShare"] = round(g["noops"] / g["turns"], 3) if g["turns"] else 0
        g["examples"] = [{"when": t.when.isoformat(), "project": t.project, "total": _total(t.tokens),
                          "noop": t.noop(), "tools": dict(t.tools), "preview": t.preview}
                         for t in sorted(group, key=lambda t: -_total(t.tokens))[:3]]
        po_by_cause[label] = g
    po_by_cause = dict(sorted(po_by_cause.items(), key=lambda kv: -kv[1]["total"]))
    po_by_room = {}
    for room, group in _group_by(po_turns, lambda t: t.project or t.room).items():
        g = _agg(group)
        g["medianCacheRead"] = round(statistics.median([t.tokens["cacheRead"] for t in group])) if group else 0
        po_by_room[room] = g
    owner_turns = [t for t in turns if t.role == "owner"]
    owner_by_cause = {label: _agg(group) for label, group in _group_by(owner_turns, _cause_label).items()}
    owner_by_cause = dict(sorted(owner_by_cause.items(), key=lambda kv: -kv[1]["total"]))
    grand = _agg(turns)
    grand_total = grand["total"] + helper["tokens"]
    span_days = max(1, len(days))
    shares = []
    for label, g in by_role_agent.items():
        shares.append({"what": label, "total": g["total"], "share": g["total"] / grand_total if grand_total else 0})
    sub = _agg(t for t in turns if t.subagent)
    shares.append({"what": "subagents (within the above)", "total": sub["total"],
                   "share": sub["total"] / grand_total if grand_total else 0})
    shares.append({"what": "hub helper (digest write-ups, est.)", "total": helper["tokens"],
                   "share": helper["tokens"] / grand_total if grand_total else 0})
    for label, g in po_by_cause.items():
        shares.append({"what": f"PO wakes: {label}", "total": g["total"],
                       "share": g["total"] / grand_total if grand_total else 0})
        shares.append({"what": f"PO no-op wakes: {label}", "total": g["noopTokens"],
                       "share": g["noopTokens"] / grand_total if grand_total else 0})
    for label, g in owner_by_cause.items():
        shares.append({"what": f"owner turns: {label}", "total": g["total"],
                       "share": g["total"] / grand_total if grand_total else 0})
    cache = sum(t.tokens["cacheRead"] for t in turns)
    shares.append({"what": "cache reads (all roles): the re-sent history", "total": cache,
                   "share": cache / grand_total if grand_total else 0})
    big = sorted([s for s in shares if s["share"] >= 0.05], key=lambda s: -s["total"])
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "scope": {"files": meta["files"], "rooms": meta["rooms"]},
        "grand": grand, "grandTotalWithHelper": grand_total, "perDay": round(grand_total / span_days),
        "byDayRole": by_day_role, "byRoleAgent": by_role_agent,
        "po": {"turns": len(po_turns), "byCause": po_by_cause, "byRoom": po_by_room},
        "owner": {"turns": len(owner_turns), "byCause": owner_by_cause},
        "reviews": review_rounds(turns, meta["sessions"]),
        "helper": helper,
        "over5pct": big,
    }


# --- report ------------------------------------------------------------------

def _k(n: int) -> str:
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _tok_cells(t: dict) -> str:
    return f"{_k(t['input'])} | {_k(t['cacheWrite'])} | {_k(t['cacheRead'])} | {_k(t['output'])}"


def markdown(report: dict) -> str:
    w = report["window"]
    lines = [
        f"# Token use by wake, {w['start'][:10]} to {w['end'][:10]}",
        "",
        f"Scope: {report['scope']['rooms']['po']} PO rooms, {report['scope']['rooms']['task']} task rooms; "
        f"{report['scope']['files']['claude']} Claude transcripts, {report['scope']['files']['codex']} Codex rollouts.",
        f"Total: **{_k(report['grandTotalWithHelper'])}** tokens ({_k(report['perDay'])}/day), "
        f"of which {_k(report['grand']['tokens']['cacheRead'])} cache reads, "
        f"{_k(report['grand']['tokens']['cacheWrite'])} cache writes, "
        f"{_k(report['grand']['tokens']['input'])} fresh input, {_k(report['grand']['tokens']['output'])} output; "
        f"hub helper (estimated) {_k(report['helper']['tokens'])}.",
        "",
        "## Tokens per day by role and agent",
        "",
        "| day | who | turns | total | input | cache write | cache read | output |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for day, row in report["byDayRole"].items():
        for who, g in sorted(row.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(f"| {day} | {who} | {g['turns']} | {_k(g['total'])} | {_tok_cells(g['tokens'])} |")
    lines += ["", "### Whole window", "",
              "| who | turns | calls | total | input | cache write | cache read | output | per turn | no-op turns |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for who, g in sorted(report["byRoleAgent"].items(), key=lambda kv: -kv[1]["total"]):
        lines.append(f"| {who} | {g['turns']} | {g['calls']} | {_k(g['total'])} | {_tok_cells(g['tokens'])} | "
                     f"{_k(g['perTurn'])} | {g['noops']} ({_k(g['noopTokens'])}) |")
    po = report["po"]
    lines += ["", f"## PO wakes by cause ({po['turns']} wakes)", "",
              "A no-op wake wrote nothing for anyone (no message, report, task change, edit or "
              "changing command) and told the CEO nothing (no `chat_send` to the user; a reply "
              "under 160 characters).", "",
              "| cause | wakes | total | per wake | median | cache read/wake | no-op wakes | no-op share | no-op tokens |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, g in po["byCause"].items():
        cr = round(g["tokens"]["cacheRead"] / g["turns"]) if g["turns"] else 0
        lines.append(f"| {label} | {g['turns']} | {_k(g['total'])} | {_k(g['perTurn'])} | {_k(g['medianTurn'])} | "
                     f"{_k(cr)} | {g['noops']} | {_pct(g['noopShare'])} | {_k(g['noopTokens'])} |")
    lines += ["", "### By PO room", "", "| project | wakes | total | per wake | median cache read | no-op wakes |",
              "|---|---:|---:|---:|---:|---:|"]
    for room, g in sorted(po["byRoom"].items(), key=lambda kv: -kv[1]["total"]):
        lines.append(f"| {room} | {g['turns']} | {_k(g['total'])} | {_k(g['perTurn'])} | {_k(g['medianCacheRead'])} | {g['noops']} |")
    lines += ["", "### Costliest wakes per cause", ""]
    for label, g in po["byCause"].items():
        for ex in g["examples"][:2]:
            lines.append(f"- **{label}** {ex['when'][:16]} {ex['project']}: {_k(ex['total'])} tokens"
                         f"{' (no-op)' if ex['noop'] else ''}; tools {ex['tools']}; `{ex['preview'][:120]}`")
    ow = report["owner"]
    lines += ["", f"## Owner turns by cause ({ow['turns']} turns)", "",
              "| cause | turns | total | per turn | median |", "|---|---:|---:|---:|---:|"]
    for label, g in ow["byCause"].items():
        lines.append(f"| {label} | {g['turns']} | {_k(g['total'])} | {_k(g['perTurn'])} | {_k(g['medianTurn'])} |")
    rv = report["reviews"]
    lines += ["", "## Review rounds", "",
              f"{rv['tasks']} tasks reviewed, {rv['rounds']} rounds ({rv['roundsPerTask']} per task), "
              f"{_k(rv['tokensPerRound'])} tokens per round. Of the {rv['laterRounds']} rounds after the first, "
              f"{rv['laterWholeDiff']} diffed the whole branch against its base again "
              f"({_k(rv['laterWholeDiffTokens'])} tokens).", "",
              "| round | rounds | tokens per round |", "|---|---:|---:|"]
    for n, g in rv["tokensPerRoundByNo"].items():
        lines.append(f"| {n if n != '4' else '4+'} | {g['rounds']} | {_k(g['perRound'])} |")
    lines += ["", "| task | rounds | tokens | whole-diff rounds | rounds (agent, tokens, whole diff) |", "|---|---:|---:|---:|---|"]
    for row in rv["byTask"][:25]:
        detail = "; ".join(f"{r['round']}: {r['agent']} {_k(r['total'])}{' W' if r['wholeDiff'] else ''}"
                           for r in row["details"])
        lines.append(f"| #{row['task']} {row['title'][:40]} | {row['rounds']} | {_k(row['tokens'])} | "
                     f"{row['wholeDiffRounds']} | {detail} |")
    lines += ["", "## Hub helper (estimated)", "",
              f"{report['helper']['digests']} digests written up by the helper model; "
              f"≈{_k(report['helper']['tokens'])} tokens "
              f"(prompt {HELPER_PROMPT_TOKENS} + facts ≈ {HELPER_FACTS_FACTOR}× the digest + the digest).",
              "", "## Everything over 5% of the total", "", "| what | tokens | share |", "|---|---:|---:|"]
    for s in report["over5pct"]:
        lines.append(f"| {s['what']} | {_k(s['total'])} | {_pct(s['share'])} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--home", default=str(Path.home()))
    ap.add_argument("--json", help="write the full report as JSON here")
    ap.add_argument("--turns", help="write every turn as JSON lines here")
    ap.add_argument("--end", help="window end (ISO date/time), default now")
    args = ap.parse_args()
    home = Path(args.home)
    end = datetime.fromisoformat(args.end).astimezone() if args.end else datetime.now().astimezone()
    start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = time.time()
    last = [0.0]

    def progress(files: dict) -> None:
        if time.time() - last[0] > 5:
            last[0] = time.time()
            print(f"  ... {files['claude']} Claude, {files['codex']} Codex files", file=sys.stderr, flush=True)

    turns, meta = collect(home, start, end, progress)
    helper = helper_estimate(home, start, end)
    report = aggregate(turns, meta, helper, start, end)
    report["elapsedSeconds"] = round(time.time() - t0, 1)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    if args.turns:
        with Path(args.turns).open("w", encoding="utf-8") as h:
            for t in turns:
                h.write(json.dumps(t.as_dict(), default=str) + "\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
