#!/usr/bin/env python3
"""What a cheap-model reader would save: exploration reads of the task agents.

Scope: the task agents the hub launched (owners and reviewers, Claude and
Codex), the same rooms ``measure_internal_communication.conversation_scope``
gives ``measure_wakes.py``; PO rooms and adopted sessions are left out.

Every tool result is classed by what it does:

* ``read whole``: Claude ``Read`` without ``offset``/``limit``;
* ``read part``: Claude ``Read`` with them;
* ``dump``: a shell command that prints a file (cat, head, tail, sed -n,
  Get-Content, type, awk, ``git show <rev>:<path>``);
* ``search``: Grep, Glob, and shell searches/listings (grep, rg, find, ls,
  Select-String, Get-ChildItem, git grep, git ls-files, wc);
* ``git``: shell history and diffs (git diff/log/show/blame);
* ``subagent``: the result of an Agent call (a sweep already delegated);
* everything else is ``other``.

Re-read bytes = bytes x model calls that followed in the same conversation,
up to a compaction (the history is re-sent on each call).

**Sweeps.** A sweep is a run of consecutive model calls of the main
conversation whose tool calls are all exploration (``read whole``, ``read
part``, ``dump``, ``search``; ``ToolSearch``/``TodoWrite`` calls in between
are neutral).  Anything else ends it: an edit, a non-reading command, a chat
message, a call with no tool, a new user/hub line.  A sweep a reader could
take is one by an **owner** with at least ``--min-calls`` exploration calls
over at least two targets.  Reads of a file the session edits are kept in
the parent (the reader never reads the lines you will edit), and so is a
call made only of such reads.

With a reader, the sweep's first call becomes the reader call and the
sweep's other calls disappear.  Per sweep the script estimates:

* parent tokens saved = the context of each call that disappears (it is not
  made) + its output, + (the replaced result tokens - the reader's answer)
  x the calls left in the conversation (each re-sends the history);
* reader tokens = a fresh Haiku conversation making the same reads: per call
  its base context plus the results so far, + the answer it writes.

Tokens of a result are ceil(bytes / 4).  The base context of a reader and
the size of its answer are measured from the Explore/general-purpose
subagents in the window when there are any (``--reader-base``,
``--answer`` override them).

Read-only: transcripts and rooms are opened for reading only.
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
import measure_task_tool_results as base  # noqa: E402
import measure_tool_results_by_tool as bytool  # noqa: E402
import measure_internal_communication as mic  # noqa: E402

EXPLORE = ("read whole", "read part", "dump", "search")
CLASSES = EXPLORE + ("git", "subagent", "other")
NEUTRAL_TOOLS = {"ToolSearch", "TodoWrite"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DUMP_WORDS = {"cat", "head", "tail", "sed", "awk", "get-content", "gc", "type", "more",
              "nl", "bat", "less", "od", "xxd"}
SEARCH_WORDS = {"grep", "egrep", "fgrep", "rg", "find", "fd", "ls", "dir", "tree", "wc",
                "select-string", "sls", "get-childitem", "gci", "test-path", "findstr",
                "where", "which", "resolve-path", "get-item", "measure-object", "sort",
                "uniq", "cut", "echo"}
GIT_READ = {"diff", "log", "show", "blame", "shortlog"}
GIT_SEARCH = {"grep", "ls-files", "ls-tree"}
WRAPPERS = {"bash", "sh", "zsh", "pwsh", "powershell", "powershell.exe", "cmd", "cmd.exe"}
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
_PATH_TOKEN_RE = re.compile(r"[\w.\-/\\:~]+\.[A-Za-z0-9]{1,6}\b")
REVIEW_MARKS = ("fresh session started for this ONE review", "You are 'codex', the reviewer",
                "the reviewer on a small software team")


def _segments(command: str) -> list[list[str]]:
    try:
        return mic._segments(command)
    except Exception:  # noqa: BLE001 - a malformed command is "other"
        return []


def shell_class(command: str) -> str:
    """The class of one shell command: dump, search, git or other.

    A pipeline or chain is classed by its strongest segment: any segment that
    is not a read makes it ``other``; else ``dump`` beats ``git`` beats
    ``search``.
    """
    found: set[str] = set()
    for seg in _segments(command):
        words = [mic._unquote(w) for w in seg]
        while words and (words[0].lower() in {"rtk", "sudo", "time"} or "=" in words[0]):
            words = words[1:]
        if not words:
            continue
        head = words[0].lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if head in WRAPPERS and len(words) > 2 and words[1].startswith(("-", "/")):
            found.add(shell_class(" ".join(words[2:])))
            continue
        if head in {"git", "git.exe"}:
            args = [w for w in words[1:] if not w.startswith("-")]
            sub = args[0].lower() if args else ""
            if sub == "show" and any(":" in a and not a.startswith("http") for a in args[1:]):
                found.add("dump")
            elif sub in GIT_READ:
                found.add("git")
            elif sub in GIT_SEARCH:
                found.add("search")
            else:
                found.add("other")
            continue
        if head == "cd" or head == "set-location":
            continue
        if head in DUMP_WORDS:
            found.add("dump")
        elif head in SEARCH_WORDS:
            found.add("search")
        else:
            found.add("other")
    if not found or "other" in found:
        return "other"
    for kind in ("dump", "git", "search"):
        if kind in found:
            return kind
    return "other"


def path_tokens(text: str) -> set[str]:
    """Lower-case basenames of the file-looking words of a command or pattern."""
    names = set()
    for token in _PATH_TOKEN_RE.findall(text or ""):
        name = re.split(r"[/\\]", token)[-1].lower()
        if name and not name.startswith("."):
            names.add(name)
    return names


def _basename(path: str) -> str:
    return re.split(r"[/\\]", str(path or ""))[-1].lower()


def claude_class(tool: str, inp: dict, payload: Any) -> tuple[str, set[str]]:
    """(class, target basenames) of one Claude tool call."""
    if bytool._has_image(payload):
        return "other", set()
    if tool == "Read":
        kind = "read part" if (inp.get("offset") or inp.get("limit")) else "read whole"
        return kind, {_basename(inp.get("file_path"))}
    if tool in {"Grep", "Glob"}:
        target = f"{inp.get('pattern', '')}@{inp.get('path', '')}@{inp.get('glob', '')}"
        return "search", {target.lower()}
    if tool in SHELL_TOOLS:
        command = str(inp.get("command") or "")
        kind = shell_class(command)
        return kind, (path_tokens(command) or {command.strip().lower()[:80]})
    if tool in {"Agent", "Task"}:
        return "subagent", set()
    return "other", set()


def codex_class(call: dict) -> tuple[str, set[str], set[str]]:
    """(class, target basenames, edited basenames) of one Codex call."""
    tool, preview, commands = bytool.codex_tool(call)
    edited: set[str] = set()
    raw = call.get("input") if isinstance(call.get("input"), str) else str(call.get("arguments") or "")
    for match in _PATCH_FILE_RE.finditer(raw.replace("\\n", "\n")):
        edited.add(_basename(match.group(1).strip()))
    if tool in bytool.CODEX_SHELL and commands:
        kinds = {shell_class(c) for c in commands}
        if "other" in kinds:
            return "other", set(), edited
        kind = next(k for k in ("dump", "git", "search") if k in kinds)
        targets: set[str] = set()
        for c in commands:
            targets |= path_tokens(c) or {c.strip().lower()[:80]}
        return kind, targets, edited
    return "other", set(), edited


# --- one conversation ------------------------------------------------------

class Turn:
    __slots__ = ("key", "when", "context", "output", "calls", "after")

    def __init__(self, key: str, when: datetime | None) -> None:
        self.key = key
        self.when = when
        self.context = 0
        self.output = 0
        self.calls: list[dict] = []   # {"cls", "targets", "bytes", "tool"}
        self.after = 0                # model calls after this one, up to a compaction


class Conversation:
    def __init__(self, agent: str, role: str, room: dict, session: str) -> None:
        self.agent, self.role, self.room, self.session = agent, role, room, session
        self.events: list[Any] = []   # Turn | "user" | "compact"
        self.edited: set[str] = set()

    def turns(self) -> list[Turn]:
        return [e for e in self.events if isinstance(e, Turn)]

    def resolve(self) -> None:
        remaining = 0
        for event in reversed(self.events):
            if event == "compact":
                remaining = 0
            elif isinstance(event, Turn):
                event.after = remaining
                remaining += 1


def _usage_context(usage: dict) -> tuple[int, int]:
    ctx = sum(int(usage.get(k) or 0) for k in
              ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return ctx, int(usage.get("output_tokens") or 0)


def scan_claude(path: Path, agent_role: str, room: dict) -> Conversation:
    """The main line of one Claude transcript as turns (sidechains skipped)."""
    conv = Conversation("claude", agent_role, room, path.stem)
    by_request: dict[str, Turn] = {}
    by_call: dict[str, tuple[Turn, dict]] = {}
    for row in base._json_lines(path):
        kind = row.get("type")
        if kind not in {"assistant", "user"} or row.get("isSidechain"):
            continue
        message = row.get("message") or {}
        if row.get("isCompactSummary"):
            conv.events.append("compact")
            continue
        content = message.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if kind == "assistant":
            key = str(row.get("requestId") or row.get("uuid") or id(row))
            turn = by_request.get(key)
            if turn is None:
                turn = Turn(key, base._timestamp(row.get("timestamp")))
                by_request[key] = turn
                conv.events.append(turn)
            ctx, out = _usage_context(message.get("usage") or {})
            turn.context = max(turn.context, ctx)
            turn.output = max(turn.output, out)
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    call = {"tool": name, "input": inp, "cls": "other", "targets": set(), "bytes": 0}
                    turn.calls.append(call)
                    by_call[str(block.get("id") or "")] = (turn, call)
                    if name in EDIT_TOOLS:
                        conv.edited.add(_basename(inp.get("file_path") or inp.get("notebook_path")))
            continue
        has_result = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                has_result = True
                found = by_call.get(str(block.get("tool_use_id") or ""))
                if not found:
                    continue
                _, call = found
                payload = block.get("content")
                call["bytes"] = base._payload_bytes(payload)
                call["cls"], call["targets"] = claude_class(call["tool"], call["input"], payload)
        if not has_result and any(isinstance(b, dict) and b.get("type") == "text" for b in content):
            conv.events.append("user")
    for turn in by_request.values():
        for call in turn.calls:
            call.pop("input", None)
    conv.resolve()
    return conv


def scan_codex(path: Path, agent_role: str, room: dict) -> Conversation:
    conv = Conversation("codex", agent_role, room, path.stem)
    by_call: dict[str, dict] = {}
    turn: Turn | None = None
    in_run = False
    count = 0
    for row in base._json_lines(path):
        kind = row.get("type")
        payload = row.get("payload") or {}
        if kind == "compacted":
            conv.events.append("compact")
            in_run = False
            continue
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            last = info.get("last_token_usage") or {}
            if turn is not None and last:
                turn.context = int(last.get("input_tokens") or 0)
                turn.output = int(last.get("output_tokens") or 0)
            continue
        if kind != "response_item":
            continue
        item = payload.get("type")
        model_item = item in bytool.CODEX_MODEL_ITEMS or (
            item == "message" and payload.get("role") == "assistant")
        if model_item:
            if not in_run:
                count += 1
                turn = Turn(str(count), base._timestamp(row.get("timestamp")))
                conv.events.append(turn)
                in_run = True
            if item in {"function_call", "custom_tool_call"} and turn is not None:
                cls, targets, edited = codex_class(payload)
                conv.edited |= edited
                call = {"tool": bytool.codex_tool(payload)[0], "cls": cls, "targets": targets, "bytes": 0}
                turn.calls.append(call)
                by_call[str(payload.get("call_id") or "")] = call
        elif item in bytool.CODEX_INPUT_ITEMS:
            in_run = False
            call = by_call.get(str(payload.get("call_id") or ""))
            if call is not None:
                call["bytes"] = base._payload_bytes(payload.get("output"))
        elif item == "message":
            in_run = False
            if payload.get("role") == "user":
                conv.events.append("user")
    conv.resolve()
    return conv


# --- sweeps ----------------------------------------------------------------

def _turn_kind(turn: Turn) -> str:
    calls = [c for c in turn.calls if c["tool"] not in NEUTRAL_TOOLS]
    if not turn.calls:
        return "text"
    if not calls:
        return "neutral"
    return "explore" if all(c["cls"] in EXPLORE for c in calls) else "act"


def sweeps(conv: Conversation) -> list[list[Turn]]:
    out: list[list[Turn]] = []
    run: list[Turn] = []
    for event in conv.events + ["end"]:
        kind = _turn_kind(event) if isinstance(event, Turn) else "break"
        if kind == "explore" or (kind == "neutral" and run):
            run.append(event)
            continue
        if run:
            out.append(run)
        run = []
    return out


def _tok(nbytes: int) -> int:
    return math.ceil(nbytes / 4)


def price_sweep(run: list[Turn], edited: set[str], reader_base: int, answer: int) -> dict:
    calls = [c for t in run for c in t.calls if c["cls"] in EXPLORE]
    kept = [c for c in calls if c["cls"] in {"read whole", "read part", "dump"}
            and c["targets"] and c["targets"] <= edited]
    moved = [c for c in calls if c not in kept]
    kept_turns = sum(1 for t in run if any(c in kept for c in t.calls)
                     and all(c in kept or c["cls"] not in EXPLORE for c in t.calls))
    dropped = run[1:]
    # Calls that only read files the session edits still happen in the parent.
    dropped_turns = max(0, len(dropped) - kept_turns)
    by_cost = sorted(dropped, key=lambda t: t.context)[:dropped_turns]
    moved_tok = sum(_tok(c["bytes"]) for c in moved)
    after = run[-1].after
    parent_saved = (sum(t.context + t.output for t in by_cost)
                    + max(0, moved_tok - answer) * after)
    # The reader: the same reads in a fresh conversation, one call per parent call.
    reader, so_far = 0, 0
    for turn in run:
        reader += reader_base + so_far
        so_far += sum(_tok(c["bytes"]) for c in turn.calls if c in moved)
    reader += reader_base + so_far + answer
    targets = set()
    for c in moved:
        targets |= c["targets"]
    return {"calls": len(calls), "movedCalls": len(moved), "keptCalls": len(kept),
            "turns": len(run), "droppedTurns": dropped_turns, "targets": len(targets),
            "movedBytes": sum(c["bytes"] for c in moved), "after": after,
            "parentSaved": parent_saved, "readerTokens": reader,
            "classes": sorted({c["cls"] for c in moved})}


# --- scope and the report --------------------------------------------------

def _role_of(info: dict | None, first_text: str) -> str:
    if info and info.get("role") in {"owner", "reviewer"}:
        return info["role"]
    return "reviewer" if any(m in first_text for m in REVIEW_MARKS) else "owner"


def _first_user_text_claude(path: Path) -> str:
    for row in base._json_lines(path):
        if row.get("type") == "user":
            content = (row.get("message") or {}).get("content")
            return content if isinstance(content, str) else bytool._text(content)
    return ""


def _first_user_text_codex(path: Path) -> str:
    for row in base._json_lines(path):
        if row.get("type") == "response_item":
            text = base._codex_user_text(row.get("payload") or {})
            if text and "<environment_context>" not in text and "AGENTS.md" not in text[:200]:
                return text
    return ""


def _codex_cwd(path: Path) -> str:
    for row in base._json_lines(path):
        if row.get("type") == "session_meta":
            return base._normal_path((row.get("payload") or {}).get("cwd"))
    return ""


def _in(when: datetime | None, start: datetime, end: datetime) -> bool:
    return when is not None and start <= when.astimezone(end.tzinfo) < end


def subagent_stats(home: Path, sessions: Iterable[Path], start: datetime, end: datetime) -> dict:
    """Base context and answer size of the Agent subagents the task agents ran."""
    firsts, answers, models, kinds = [], [], defaultdict(int), defaultdict(int)
    tokens = 0
    for parent in sessions:
        for sub in base._glob(parent.parent / parent.stem / "subagents", "*.jsonl"):
            meta = sub.with_suffix(".meta.json")
            try:
                kind = json.loads(meta.read_text(encoding="utf-8")).get("agentType", "?")
            except (OSError, ValueError):
                kind = "?"
            first = None
            last_text = ""
            inside = False
            for row in base._json_lines(sub):
                if row.get("type") != "assistant":
                    continue
                if not _in(base._timestamp(row.get("timestamp")), start, end):
                    continue
                inside = True
                message = row.get("message") or {}
                ctx, out = _usage_context(message.get("usage") or {})
                tokens += ctx + out
                models[str(message.get("model") or "?")] += 1
                if first is None and ctx:
                    first = ctx
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        last_text = str(block.get("text") or "")
            if inside:
                kinds[kind] += 1
                if first:
                    firsts.append(first)
                if last_text:
                    answers.append(_tok(len(last_text.encode("utf-8"))))
    return {"subagents": sum(kinds.values()), "types": dict(kinds), "models": dict(models),
            "tokens": tokens,
            "medianBase": int(statistics.median(firsts)) if firsts else None,
            "medianAnswer": int(statistics.median(answers)) if answers else None}


def measure(home: Path, start: datetime, end: datetime, min_calls: int = 3,
            reader_base: int | None = None, answer: int | None = None,
            progress=None) -> dict:
    began = time.monotonic()
    scope = mic.conversation_scope(home)
    sessions = scope["sessions"]
    convs: list[Conversation] = []
    claude_paths: list[Path] = []
    for path in base._glob(home / ".claude" / "projects", "**/*.jsonl"):
        if "subagents" in path.parts:
            continue
        info = sessions.get(path.stem)
        if not info or info.get("role") == "po" or scope["kinds"].get(path.stem) != "claude":
            continue
        claude_paths.append(path)
        role = _role_of(info, _first_user_text_claude(path))
        convs.append(scan_claude(path, role, info.get("room") or {}))
        if progress and len(convs) % 100 == 0:
            progress(f"{len(convs)} conversations, {time.monotonic() - began:.0f}s")
    by_cwd = defaultdict(set)
    for cwd in scope["cwds"]:
        by_cwd["codex"].add(cwd)
    for path in base._glob(home / ".codex" / "sessions", "**/rollout-*.jsonl"):
        sid = path.stem[-36:]
        info = sessions.get(sid)
        if info and info.get("role") == "po":
            continue
        if not info and not base._codex_identity(path, scope["kinds"], by_cwd, scope["excluded"]):
            continue
        if not info:
            # A cwd match knows the room, not the role: the first prompt says it.
            info = {"room": (scope["cwds"].get(_codex_cwd(path)) or {}).get("room") or {}}
        role = _role_of(info, _first_user_text_codex(path))
        convs.append(scan_codex(path, role, info.get("room") or {}))
    sub = subagent_stats(home, claude_paths, start, end)
    base_tok = reader_base or sub["medianBase"] or 15000
    answer_tok = answer or sub["medianAnswer"] or 600

    days = (end - start).total_seconds() / 86400
    classes: dict[str, dict] = defaultdict(lambda: {c: {"results": 0, "bytes": 0, "reread": 0} for c in CLASSES})
    totals: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tokens": 0, "conversations": 0})
    sweep_rows: dict[str, dict] = defaultdict(lambda: {"sweeps": 0, "qualifying": 0, "calls": 0,
                                                        "droppedTurns": 0, "movedBytes": 0,
                                                        "parentSaved": 0, "readerTokens": 0})
    examples: list[dict] = []
    for conv in convs:
        who = f"{conv.agent}/{conv.role}"
        seen = False
        for turn in conv.turns():
            if not _in(turn.when, start, end):
                continue
            seen = True
            totals[who]["calls"] += 1
            totals[who]["tokens"] += turn.context + turn.output
            for call in turn.calls:
                row = classes[who][call["cls"] if call["cls"] in CLASSES else "other"]
                row["results"] += 1
                row["bytes"] += call["bytes"]
                row["reread"] += call["bytes"] * turn.after
        totals[who]["conversations"] += int(seen)
        for run in sweeps(conv):
            if not _in(run[0].when, start, end):
                continue
            priced = price_sweep(run, conv.edited, base_tok, answer_tok)
            row = sweep_rows[who]
            row["sweeps"] += 1
            if conv.role != "owner" or priced["movedCalls"] < min_calls or priced["targets"] < 2:
                continue
            row["qualifying"] += 1
            for key in ("calls", "droppedTurns", "movedBytes", "parentSaved", "readerTokens"):
                row[key] += priced[key]
            examples.append({**priced, "who": who, "task": conv.room.get("task"),
                             "title": conv.room.get("title", ""), "session": conv.session,
                             "when": run[0].when.isoformat() if run[0].when else ""})
    examples.sort(key=lambda e: -e["parentSaved"])
    all_tokens = sum(t["tokens"] for t in totals.values())
    saved = sum(r["parentSaved"] for r in sweep_rows.values())
    reader = sum(r["readerTokens"] for r in sweep_rows.values())
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "elapsedSeconds": round(time.monotonic() - began, 1),
        "assumptions": {"readerBaseTokens": base_tok, "answerTokens": answer_tok,
                        "minCalls": min_calls},
        "totals": dict(totals), "taskAgentTokens": all_tokens,
        "classes": {k: dict(v) for k, v in classes.items()},
        "sweeps": dict(sweep_rows),
        "parentSaved": saved, "readerTokens": reader,
        "savedShare": saved / all_tokens if all_tokens else 0,
        "subagents": sub,
        "examples": examples[:25],
    }


def _m(value: float) -> str:
    return f"{value / 1e6:,.1f}M"


def _markdown(report: dict) -> str:
    days = report["window"]["days"]
    a = report["assumptions"]
    out = [f"Window `{report['window']['start']}` to `{report['window']['end']}`; scan "
           f"{report['elapsedSeconds']}s. Reader base context {a['readerBaseTokens']:,} tokens, "
           f"answer {a['answerTokens']:,} tokens, a sweep needs >= {a['minCalls']} calls.", "",
           "## Task-agent tokens", "", "| who | conversations | model calls | tokens | per day |",
           "|---|---:|---:|---:|---:|"]
    for who, t in sorted(report["totals"].items()):
        out.append(f"| {who} | {t['conversations']} | {t['calls']:,} | {_m(t['tokens'])} | {_m(t['tokens'] / days)} |")
    out.append(f"| **all** | | | **{_m(report['taskAgentTokens'])}** | **{_m(report['taskAgentTokens'] / days)}** |")
    out += ["", "## Tool results by class (bytes, re-read bytes)", "",
            "| who | " + " | ".join(CLASSES) + " |", "|---|" + "---:|" * len(CLASSES)]
    for who, row in sorted(report["classes"].items()):
        cells = " | ".join(f"{row[c]['results']:,} / {row[c]['bytes'] / 1e6:,.1f} MB / {row[c]['reread'] / 1e6:,.0f} MB"
                           for c in CLASSES)
        out.append(f"| {who} | {cells} |")
    out += ["", "## Sweeps", "",
            "| who | sweeps | a reader could take | their calls | calls saved | bytes moved | parent tokens saved | per day | reader tokens |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for who, r in sorted(report["sweeps"].items()):
        out.append(f"| {who} | {r['sweeps']:,} | {r['qualifying']:,} | {r['calls']:,} | {r['droppedTurns']:,} | "
                   f"{r['movedBytes'] / 1e6:,.1f} MB | {_m(r['parentSaved'])} | {_m(r['parentSaved'] / days)} | "
                   f"{_m(r['readerTokens'])} |")
    out += ["", f"Parent tokens saved: **{_m(report['parentSaved'])}** = "
                f"**{100 * report['savedShare']:.1f}%** of task-agent tokens "
                f"({_m(report['parentSaved'] / days)} a day); the reader would spend "
                f"{_m(report['readerTokens'])} ({_m(report['readerTokens'] / days)} a day).", ""]
    sub = report["subagents"]
    out += [f"Subagents the task agents already ran: {sub['subagents']} ({sub['types']}), models "
            f"{sub['models']}, {_m(sub['tokens'])} tokens.", "",
            "## Largest sweeps", "", "| task | who | calls | calls saved | targets | KB moved | calls after | parent saved | reader |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for e in report["examples"][:15]:
        task = f"#{e['task']} {e['title'][:40]}" if e.get("task") is not None else e["session"][:8]
        out.append(f"| {task} | {e['who']} | {e['calls']} | {e['droppedTurns']} | {e['targets']} | "
                   f"{e['movedBytes'] // 1024:,} | {e['after']} | {_m(e['parentSaved'])} | {_m(e['readerTokens'])} |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--end", help="ISO-8601 window end (default: now)")
    parser.add_argument("--min-calls", type=int, default=3)
    parser.add_argument("--reader-base", type=int, help="reader base context tokens")
    parser.add_argument("--answer", type=int, help="reader answer tokens")
    parser.add_argument("--json", help="also write the report as JSON here")
    args = parser.parse_args()
    end = base._timestamp(args.end) if args.end else datetime.now().astimezone()
    if end is None or args.days <= 0:
        parser.error("bad --end or --days")
    end = end.astimezone()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    report = measure(args.home.expanduser(), end - timedelta(days=args.days), end,
                     args.min_calls, args.reader_base, args.answer,
                     progress=lambda m: print(m, file=sys.stderr, flush=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
