#!/usr/bin/env python3
"""Where the tool-result bytes go: per tool, per size band, with re-read cost.

Same scope and window as ``measure_task_tool_results.py`` (task rooms only,
PO rooms and adopted sessions excluded; that module supplies the room scope
and the session identity rules).  For every tool result this script records
the tool name, the payload size, the room it belongs to, a short preview of
the input, whether the command went through rtk, and how many model calls of
the same conversation came after it ("remaining turns").

Re-read bytes = payload bytes x remaining turns.  Every later model call of
the same conversation re-sends the whole history, so a result costs roughly
its size once per later call.  The count stops at a compaction (Claude's
compact summary, Codex's ``compacted`` record) because the history before it
is dropped.  It ignores prompt caching (cached input is cheaper but still
sent) and any tool-result clearing the CLIs do on their own, so it is an
upper bound on the input the result generated, not a bill.

Read-only: transcripts are opened for reading only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_task_tool_results as base  # noqa: E402

KB = 1024
BANDS: tuple[tuple[str, int], ...] = (
    ("<=2 KB", 2 * KB), ("2-8 KB", 8 * KB), ("8-32 KB", 32 * KB),
    ("32-128 KB", 128 * KB), (">128 KB", 1 << 62),
)
BAND_NAMES = tuple(name for name, _ in BANDS)
SHELL_TOOLS = {"bash", "powershell"}
CODEX_SHELL = {"exec_command", "shell", "exec", "container.exec", "write_stdin"}
IMAGE_SUFFIX = " [image/pdf]"
RTK_BRIEF_MARK = ("RTK is enabled for this task", "RTK is available for this task")
RTK_RESULT_MARK = "[rtk]"
RTK_RECALL_MARK = "rtk recall"
RTK_WARNING = "[rtk] /!\\ No hook installed"
# The warning line as it lands in a result: the marker, the advice and a newline.
RTK_WARNING_BYTES = len(RTK_WARNING) + 54
RTK_KINDS = ("rtk", "no-rtk", "pre-rtk", "unwired")
# The note a cap would leave in place of the cut bytes, in the what-if table.
CAP_NOTE_BYTES = 120
DEFAULT_CAPS: tuple[tuple[str, str, int], ...] = (
    ("claude", "Read", 8), ("claude", "Read", 16), ("claude", "Read", 32), ("claude", "Read", 64),
    ("claude", "Bash", 8), ("claude", "Bash", 16),
    ("claude", "Grep", 8), ("claude", "Grep", 16),
    ("claude", "Agent", 8),
    ("claude", "mcp__claude-in-chrome__browser_batch", 16),
    ("claude", "mcp__claude-in-chrome__browser_batch", 32),
    ("claude", "mcp__ensemble__chat_read", 8),
    ("claude", "mcp__ensemble__ensemble_get_task", 8),
    ("codex", "exec_command", 8), ("codex", "exec_command", 16), ("codex", "exec_command", 32),
    ("codex", "mcp__ensemble__ensemble_whoami", 8),
    ("codex", "mcp__ensemble__ensemble_get_task", 8),
)
# Codex's ``exec`` tool takes JavaScript: ``tools.exec_command({cmd:"..."})``.
# Keys may be bare or quoted; strings may use ", ' or `.
_JS_STR = r'(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`((?:[^`\\]|\\.)*)`)'
_CMD_RE = re.compile(r'["\']?(?:cmd|command)["\']?\s*:\s*' + _JS_STR)
_PATH_RE = re.compile(r'["\']?(?:path|file_path|url|pattern|query)["\']?\s*:\s*' + _JS_STR)
_TOOLS_RE = re.compile(r"\btools\.([A-Za-z0-9_]+)\s*\(")
_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)\s*(?:&&|;)\s*"      # cd x && / cd x;
    r"|\$?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S*)\s*[;&]*\s*"  # VAR=x  $v = x;
    r"|\(\s*"                                              # ( subshell
    r")+")


def band(size: int) -> str:
    for name, limit in BANDS:
        if size <= limit:
            return name
    return BAND_NAMES[-1]


def _one_line(text: Any, limit: int = 100) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _js_string(match: re.Match | None) -> str:
    if not match:
        return ""
    double, single, backtick = match.groups()
    if double is not None:
        raw = double
    else:
        raw = (single if single is not None else backtick)
        raw = raw.replace("\\'", "'").replace("\\`", "`").replace('"', '\\"')
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


def _preview(tool: str, inp: Any) -> str:
    """The input field a reader recognises: a path, a command, a pattern."""
    if isinstance(inp, str):
        return _one_line(inp)
    if not isinstance(inp, dict):
        return _one_line(inp)
    for key in ("command", "file_path", "path", "notebook_path", "url", "pattern",
                "description", "prompt", "query", "skill", "message"):
        if inp.get(key):
            extra = ""
            if key == "pattern" and inp.get("path"):
                extra = f" in {inp['path']}"
            return _one_line(f"{inp[key]}{extra}")
    return _one_line(json.dumps(inp, ensure_ascii=False, separators=(",", ":")))


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(b.get("text") or "") if isinstance(b, dict) else str(b)
                         for b in value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _has_image(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(b, dict) and b.get("type") in {"image", "document"} for b in value)


def first_word(command: str) -> str:
    """The program a shell command runs, past ``cd x &&`` and ``VAR=x`` prefixes."""
    text = _PREFIX_RE.sub("", command or "", count=1)
    word = text.split(None, 1)[0] if text.split() else ""
    word = word.strip("\"'()")
    return word.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "(empty)"


def _rtk_prefixed(command: str) -> bool:
    return first_word(command) == "rtk" or bool(
        re.search(r"(?:&&|\|\||[;|\n])\s*rtk\s", command or ""))


# --- rooms -----------------------------------------------------------------

def _rooms(home: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Room descriptors keyed by session id and by task cwd (task rooms only)."""
    po_rooms = base._project_po_rooms(home)
    by_session: dict[str, dict] = {}
    by_cwd: dict[str, dict] = {}
    for path in base._glob(home / ".ensemble" / "rooms", "room-*.json"):
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(room, dict) or room.get("adopted") or room.get("id") in po_rooms:
            continue
        if "launched" not in room and not room.get("taskDir"):
            continue
        info = {"room": str(room.get("id") or ""), "task": room.get("no"),
                "title": _one_line(room.get("title"), 60)}
        for part in room.get("participants", []):
            if isinstance(part, dict) and part.get("kind") == "agent":
                for sid in base._session_ids(part):
                    by_session.setdefault(sid, info)
                cwd = base._normal_path(part.get("cwd"))
                if cwd:
                    by_cwd.setdefault(cwd, info)
        for key in ("cwd", "taskDir"):
            cwd = base._normal_path(room.get(key))
            if cwd:
                by_cwd.setdefault(cwd, info)
    return by_session, by_cwd


# --- one conversation ------------------------------------------------------

class _Conversation:
    """Events of one conversation in file order, resolved to remaining turns."""

    def __init__(self) -> None:
        self.events: list[tuple] = []  # ("turn", id) | ("compact",) | ("result", rec)

    def resolve(self) -> None:
        remaining = 0
        seen: set[str] = set()
        for event in reversed(self.events):
            kind = event[0]
            if kind == "turn":
                if event[1] not in seen:
                    seen.add(event[1])
                    remaining += 1
            elif kind == "compact":
                remaining = 0
                seen = set()
            else:
                rec = event[1]
                rec["remainingTurns"] = remaining
                rec["rereadBytes"] = rec["bytes"] * remaining


def _in_window(when: datetime | None, start: datetime, end: datetime) -> bool:
    if when is None:
        return False
    local = when.astimezone(end.tzinfo)
    return start <= local < end


def _record(agent: str, tool: str, size: int, when: datetime, end: datetime,
            session: str, room: dict | None, preview: str, rtk: str, recall: bool,
            warnings: int, first: str) -> dict:
    return {
        "agent": agent, "tool": tool, "bytes": size, "band": band(size),
        "when": when.astimezone(timezone.utc).isoformat(),
        "day": when.astimezone(end.tzinfo).date().isoformat(),
        "session": session,
        "room": (room or {}).get("room", ""), "task": (room or {}).get("task"),
        "title": (room or {}).get("title", ""), "preview": preview,
        "rtk": rtk, "recall": recall, "rtkWarnings": warnings, "firstWord": first,
        "remainingTurns": 0, "rereadBytes": 0,
    }


def _rtk_kind(hit: bool, wired: bool) -> str:
    return "rtk" if hit else ("no-rtk" if wired else "unwired")


def _mark_pre_rtk(records: list[dict]) -> None:
    """Shell results before a session's first rtk result were made before the
    hook reached it (a session launched before the pilot, resumed after)."""
    first = min((r["when"] for r in records if r["rtk"] == "rtk"), default=None)
    if first is None:
        return
    for rec in records:
        if rec["rtk"] == "no-rtk" and rec["when"] < first:
            rec["rtk"] = "pre-rtk"


def scan_claude(path: Path, start: datetime, end: datetime,
                room: dict | None) -> list[dict]:
    """Tool results of one Claude transcript (main line and in-file sidechains)."""
    calls: dict[str, dict] = {}
    conversations: dict[str, _Conversation] = defaultdict(_Conversation)
    records: list[dict] = []
    wired = False
    for row in base._json_lines(path):
        kind = row.get("type")
        if kind not in {"assistant", "user"}:
            continue
        key = str(row.get("agentId") or "")
        conv = conversations[key]
        message = row.get("message") or {}
        if row.get("isCompactSummary"):
            conv.events.append(("compact",))
            continue
        if kind == "assistant":
            conv.events.append(("turn", str(row.get("requestId") or row.get("uuid") or id(row))))
        content = message.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and kind == "user" and not wired:
                text = str(block.get("text") or "")
                wired = any(mark in text for mark in RTK_BRIEF_MARK)
            elif btype == "tool_use":
                calls[str(block.get("id") or "")] = block
            elif btype == "tool_result":
                when = base._timestamp(row.get("timestamp"))
                if not _in_window(when, start, end):
                    continue
                call = calls.get(str(block.get("tool_use_id") or ""), {})
                tool = str(call.get("name") or "(unknown)")
                inp = call.get("input") if isinstance(call.get("input"), dict) else {}
                payload = block.get("content")
                size = base._payload_bytes(payload)
                if _has_image(payload):
                    tool += IMAGE_SUFFIX
                rtk, recall, warnings, first = "n/a", False, 0, ""
                if tool.lower() in SHELL_TOOLS:
                    text = _text(payload)
                    command = str(inp.get("command") or "")
                    warnings = text.count(RTK_WARNING)
                    recall = RTK_RECALL_MARK in text
                    hit = RTK_RESULT_MARK in text or recall or _rtk_prefixed(command)
                    rtk = _rtk_kind(hit, wired)
                    first = first_word(command)
                rec = _record("claude", tool, size, when, end, path.stem, room,
                              _preview(tool, inp), rtk, recall, warnings, first)
                conv.events.append(("result", rec))
                records.append(rec)
    for conv in conversations.values():
        conv.resolve()
    _mark_pre_rtk(records)
    return records


def codex_tool(call: dict) -> tuple[str, str]:
    """(tool name, command-or-input preview) of a Codex call.

    Codex's ``exec`` custom tool runs JavaScript that calls ``tools.X(...)``;
    the first such call names the real tool (``exec_command``,
    ``apply_patch``, an MCP tool).  Plain scripts count as ``exec (js)``.
    """
    name = str(call.get("name") or "(unknown)")
    args = call.get("arguments")
    inp = call.get("input")
    if name == "exec" and isinstance(inp, str):
        match = _TOOLS_RE.search(inp)
        if match:
            name = match.group(1)
            rest = inp[match.end():]
            if name in CODEX_SHELL:
                return name, _js_string(_CMD_RE.search(rest)) or _one_line(rest)
            return name, _js_string(_PATH_RE.search(rest)) or _one_line(rest)
        return "exec (js)", _one_line(inp)
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            parsed = args
        if isinstance(parsed, dict):
            cmd = parsed.get("cmd") or parsed.get("command")
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            if cmd:
                return name, str(cmd)
        return name, _preview(name, parsed)
    return name, _preview(name, inp if inp is not None else args)


def scan_codex(path: Path, start: datetime, end: datetime,
               room: dict | None) -> list[dict]:
    calls: dict[str, dict] = {}
    conv = _Conversation()
    records: list[dict] = []
    wired = False
    for row in base._json_lines(path):
        kind = row.get("type")
        payload = row.get("payload") or {}
        if kind == "compacted":
            conv.events.append(("compact",))
        elif kind == "event_msg" and payload.get("type") == "token_count":
            conv.events.append(("turn", str(row.get("timestamp") or len(conv.events))))
        if kind != "response_item":
            continue
        item = payload.get("type")
        if item == "message" and not wired:
            wired = any(mark in base._codex_user_text(payload) for mark in RTK_BRIEF_MARK)
        elif item in {"function_call", "custom_tool_call"}:
            calls[str(payload.get("call_id") or "")] = payload
        elif item in {"function_call_output", "custom_tool_call_output"}:
            when = base._timestamp(row.get("timestamp"))
            if not _in_window(when, start, end):
                continue
            call = calls.get(str(payload.get("call_id") or ""), {})
            tool, command = codex_tool(call)
            size = base._payload_bytes(payload.get("output"))
            rtk, recall, warnings, first = "n/a", False, 0, ""
            if tool in CODEX_SHELL:
                text = _text(payload.get("output"))
                warnings = text.count(RTK_WARNING)
                recall = RTK_RECALL_MARK in text
                hit = _rtk_prefixed(command) or RTK_RESULT_MARK in text or recall
                rtk = _rtk_kind(hit, wired)
                first = first_word(command)
            rec = _record("codex", tool, size, when, end, path.stem, room,
                          _one_line(command), rtk, recall, warnings, first)
            conv.events.append(("result", rec))
            records.append(rec)
    conv.resolve()
    _mark_pre_rtk(records)
    return records


# --- aggregation -----------------------------------------------------------

def _empty_tool_row() -> dict:
    return {"results": 0, "bytes": 0, "rereadBytes": 0,
            "bands": {name: 0 for name in BAND_NAMES},
            "bandBytes": {name: 0 for name in BAND_NAMES}}


def _tool_table(rows: list[dict]) -> list[dict]:
    tools: dict[str, dict] = defaultdict(_empty_tool_row)
    for rec in rows:
        row = tools[rec["tool"]]
        row["results"] += 1
        row["bytes"] += rec["bytes"]
        row["rereadBytes"] += rec["rereadBytes"]
        row["bands"][rec["band"]] += 1
        row["bandBytes"][rec["band"]] += rec["bytes"]
    return [{"tool": tool, **row,
             "approxTokens": math.ceil(row["bytes"] / 4),
             "rereadApproxTokens": math.ceil(row["rereadBytes"] / 4)}
            for tool, row in sorted(tools.items(), key=lambda kv: -kv[1]["bytes"])]


def _totals(rows: list[dict]) -> dict:
    total_bytes = sum(r["bytes"] for r in rows)
    total_reread = sum(r["rereadBytes"] for r in rows)
    return {"results": len(rows), "bytes": total_bytes,
            "approxTokens": math.ceil(total_bytes / 4),
            "rereadBytes": total_reread,
            "rereadApproxTokens": math.ceil(total_reread / 4),
            "conversations": len({r["session"] for r in rows})}


def aggregate(records: list[dict], top: int = 20) -> dict:
    per_agent: dict[str, dict] = {}
    for agent in ("claude", "codex"):
        rows = [r for r in records if r["agent"] == agent]
        largest = sorted(rows, key=lambda r: -r["bytes"])[:top]
        per_agent[agent] = {
            **_totals(rows),
            "tools": _tool_table(rows),
            "top": [{k: r[k] for k in ("tool", "bytes", "band", "task", "room",
                                       "title", "preview", "remainingTurns",
                                       "rereadBytes", "when")} for r in largest],
            "rtk": _rtk_summary(rows),
        }
    return per_agent


def _rtk_summary(rows: list[dict]) -> dict:
    shell = [r for r in rows if r["rtk"] != "n/a"]
    summary: dict[str, Any] = {"shellResults": len(shell),
                               "shellBytes": sum(r["bytes"] for r in shell)}

    def counter() -> dict:
        row = {kind: 0 for kind in RTK_KINDS}
        row.update({kind + "Bytes": 0 for kind in RTK_KINDS})
        row["recall"] = 0
        return row

    by_tool: dict[str, dict] = defaultdict(counter)
    by_day: dict[str, dict] = defaultdict(counter)
    for r in shell:
        for row in (by_tool[r["tool"]], by_day[r["day"]]):
            row[r["rtk"]] += 1
            row[r["rtk"] + "Bytes"] += r["bytes"]
            row["recall"] += int(r["recall"])
    summary["byTool"] = dict(by_tool)
    summary["byDay"] = dict(sorted(by_day.items()))
    warnings = sum(r["rtkWarnings"] for r in shell)
    summary["warnings"] = warnings
    summary["warningBytes"] = warnings * RTK_WARNING_BYTES
    summary["warningRereadBytes"] = sum(r["rtkWarnings"] * r["remainingTurns"]
                                        for r in shell) * RTK_WARNING_BYTES
    words: dict[str, dict] = defaultdict(lambda: {"results": 0, "bytes": 0})
    for r in shell:
        if r["rtk"] == "no-rtk":
            words[r["firstWord"]]["results"] += 1
            words[r["firstWord"]]["bytes"] += r["bytes"]
    summary["noRtkFirstWords"] = [{"word": w, **v} for w, v in
                                  sorted(words.items(), key=lambda kv: -kv[1]["bytes"])[:15]]
    sessions: dict[str, dict] = defaultdict(lambda: {kind: 0 for kind in RTK_KINDS})
    for r in shell:
        sessions[r["session"]][r["rtk"]] += 1
    summary["sessions"] = {
        "withRtk": sum(1 for s in sessions.values() if s["rtk"]),
        "wiredWithoutRtk": sum(1 for s in sessions.values() if not s["rtk"] and s["no-rtk"]),
        "unwired": sum(1 for s in sessions.values()
                       if not s["rtk"] and not s["no-rtk"] and s["unwired"]),
    }
    return summary


def simulate_caps(records: list[dict], caps: Iterable[tuple[str, str, int]]) -> list[dict]:
    """What each cap would have cut over the window, per (agent, tool, KB)."""
    rows = []
    for agent, tool, kb in caps:
        limit = kb * KB
        total = [r for r in records if r["agent"] == agent and r["tool"] == tool]
        hit = [r for r in total if r["bytes"] > limit + CAP_NOTE_BYTES]
        saved = sum(r["bytes"] - limit - CAP_NOTE_BYTES for r in hit)
        reread = sum((r["bytes"] - limit - CAP_NOTE_BYTES) * r["remainingTurns"] for r in hit)
        rows.append({"agent": agent, "tool": tool, "capKB": kb,
                     "results": len(total), "affected": len(hit),
                     "bytesSaved": saved, "rereadBytesSaved": reread,
                     "toolBytes": sum(r["bytes"] for r in total),
                     "toolRereadBytes": sum(r["rereadBytes"] for r in total)})
    return rows


# --- driver ----------------------------------------------------------------

def _codex_room(path: Path, rooms_by_session: dict, rooms_by_cwd: dict) -> dict | None:
    for row in base._json_lines(path):
        if row.get("type") == "session_meta":
            payload = row.get("payload") or {}
            return (rooms_by_session.get(str(payload.get("id") or ""))
                    or rooms_by_cwd.get(base._normal_path(payload.get("cwd"))))
    return None


def measure(home: Path, start: datetime, end: datetime, top: int = 20,
            caps: Iterable[tuple[str, str, int]] = DEFAULT_CAPS,
            progress=None) -> dict:
    began = time.monotonic()
    by_session, by_cwd, excluded, room_count = base._task_scope(home)
    rooms_by_session, rooms_by_cwd = _rooms(home)
    records: list[dict] = []
    subagent_records: list[dict] = []
    sessions = {"claude": 0, "codex": 0, "claudeSubagents": 0}
    scanned = 0

    def tick() -> None:
        nonlocal scanned
        scanned += 1
        if progress and scanned % 50 == 0:
            progress(f"scanned {scanned} sessions, {len(records):,} results so far, "
                     f"{time.monotonic() - began:.0f}s")

    for path in base._glob(home / ".claude" / "projects", "**/*.jsonl"):
        if not base._claude_identity(path, by_session, by_cwd):
            continue
        room = rooms_by_session.get(path.stem)
        found = scan_claude(path, start, end, room)
        tick()
        if found:
            sessions["claude"] += 1
            records.extend(found)
        # A subagent's own tool results live in its own transcript and are
        # re-read only by that subagent; the parent sees the Agent result.
        for sub in base._glob(path.parent / path.stem / "subagents", "*.jsonl"):
            found = scan_claude(sub, start, end, room)
            if found:
                sessions["claudeSubagents"] += 1
                subagent_records.extend(found)
    for path in base._glob(home / ".codex" / "sessions", "**/rollout-*.jsonl"):
        if not base._codex_identity(path, by_session, by_cwd, excluded):
            continue
        found = scan_codex(path, start, end, _codex_room(path, rooms_by_session, rooms_by_cwd))
        tick()
        if found:
            sessions["codex"] += 1
            records.extend(found)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "taskRooms": room_count,
        "matchedSessions": sessions,
        "elapsedSeconds": round(time.monotonic() - began, 1),
        "agents": aggregate(records, top),
        "claudeSubagents": {**_totals(subagent_records),
                            "tools": _tool_table(subagent_records)[:8]},
        "caps": simulate_caps(records, caps),
    }


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):,.1f}"


def _markdown(report: dict) -> str:
    sessions = report["matchedSessions"]
    out = [
        f"Window: `{report['window']['start']}` to `{report['window']['end']}`  ",
        f"Scope: {report['taskRooms']} task rooms; sessions with results: "
        f"Claude {sessions['claude']}, Codex {sessions['codex']}; scan {report['elapsedSeconds']}s",
        "",
        "Re-read bytes = bytes x model calls that followed in the same conversation "
        "(stops at a compaction; ignores prompt caching and the CLIs' own result "
        "clearing, so it is an upper bound). Approx tokens = ceil(bytes / 4).",
    ]
    for agent, data in report["agents"].items():
        out += ["", f"## {agent.capitalize()}: {data['results']:,} results, {_mb(data['bytes'])} MB "
                    f"(~{data['approxTokens']:,} tokens), re-read {_mb(data['rereadBytes'])} MB "
                    f"(~{data['rereadApproxTokens']:,} tokens)", "",
                "| Tool | Results | Bytes | % | Approx tokens | Re-read bytes | " + " | ".join(BAND_NAMES) + " |",
                "|---|---:|---:|---:|---:|---:|" + "---:|" * len(BAND_NAMES)]
        for row in data["tools"]:
            share = 100 * row["bytes"] / data["bytes"] if data["bytes"] else 0
            bands = " | ".join(f"{row['bands'][b]:,}" for b in BAND_NAMES)
            out.append(f"| {row['tool']} | {row['results']:,} | {row['bytes']:,} | {share:.1f} | "
                       f"{row['approxTokens']:,} | {row['rereadBytes']:,} | {bands} |")
        out += ["", f"Bytes by size band ({agent}):", "",
                "| Band | Results | Bytes | % |", "|---|---:|---:|---:|"]
        for name in BAND_NAMES:
            n = sum(r["bands"][name] for r in data["tools"])
            b = sum(r["bandBytes"][name] for r in data["tools"])
            share = 100 * b / data["bytes"] if data["bytes"] else 0
            out.append(f"| {name} | {n:,} | {b:,} | {share:.1f} |")
        out += ["", f"Largest single results ({agent}):", "",
                "| # | Tool | Bytes | Task | Turns after | Re-read bytes | Input |",
                "|---:|---|---:|---|---:|---:|---|"]
        for i, row in enumerate(data["top"], 1):
            task = f"#{row['task']} {row['title'][:40]}" if row["task"] is not None else (row["room"] or "?")
            preview = row["preview"].replace("|", "\\|").replace("`", "'")
            out.append(f"| {i} | {row['tool']} | {row['bytes']:,} | {task} | "
                       f"{row['remainingTurns']} | {row['rereadBytes']:,} | `{preview}` |")
        rtk = data["rtk"]
        out += ["", f"rtk ({agent}): {rtk['shellResults']:,} shell results, {rtk['shellBytes']:,} bytes; "
                    f"sessions with an rtk result {rtk['sessions']['withRtk']}, wired but never rtk "
                    f"{rtk['sessions']['wiredWithoutRtk']}, not wired {rtk['sessions']['unwired']}; "
                    f"rtk's 'No hook installed' line printed {rtk['warnings']:,} times "
                    f"(~{rtk['warningBytes']:,} bytes, re-read ~{rtk['warningRereadBytes']:,})", "",
                "| Shell tool | Through rtk | Bytes | Hook active, not rewritten | Bytes | "
                "Before the hook | Bytes | Session not wired | Bytes | Recall hints |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for tool, row in sorted(rtk["byTool"].items(),
                                key=lambda kv: -sum(kv[1][k] for k in RTK_KINDS)):
            cells = " | ".join(f"{row[k]:,} | {row[k + 'Bytes']:,}" for k in RTK_KINDS)
            out.append(f"| {tool} | {cells} | {row['recall']:,} |")
        out += ["", f"Shell results by day ({agent}): through rtk / hook active but not rewritten / "
                    "before the hook / not wired", "",
                "| Day | rtk | not rewritten | before hook | not wired |",
                "|---|---:|---:|---:|---:|"]
        for day, row in rtk["byDay"].items():
            out.append(f"| {day} | " + " | ".join(f"{row[k]:,}" for k in RTK_KINDS) + " |")
        if rtk["noRtkFirstWords"]:
            out += ["", f"Commands rtk left alone while the hook was active ({agent}), by program:", "",
                    "| Program | Results | Bytes |", "|---|---:|---:|"]
            for row in rtk["noRtkFirstWords"]:
                word = row["word"].replace("|", "\\|").replace("`", "'")
                out.append(f"| `{word}` | {row['results']:,} | {row['bytes']:,} |")
    sub = report["claudeSubagents"]
    out += ["", f"## Claude subagents (not in the tables above): {sessions['claudeSubagents']} transcripts, "
                f"{sub['results']:,} results, {_mb(sub['bytes'])} MB (~{sub['approxTokens']:,} tokens), "
                f"re-read {_mb(sub['rereadBytes'])} MB", ""]
    if sub["tools"]:
        out += ["| Tool | Results | Bytes | Re-read bytes |", "|---|---:|---:|---:|"]
        for row in sub["tools"]:
            out.append(f"| {row['tool']} | {row['results']:,} | {row['bytes']:,} | {row['rereadBytes']:,} |")
    out += ["", "## What a cap would have cut", "",
            f"A result over the cap keeps the first cap KB plus a {CAP_NOTE_BYTES}-byte note.", "",
            "| Agent | Tool | Cap KB | Results | Over cap | Bytes saved | % of tool | Re-read bytes saved | % of tool re-read |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["caps"]:
        share = 100 * row["bytesSaved"] / row["toolBytes"] if row["toolBytes"] else 0
        rshare = 100 * row["rereadBytesSaved"] / row["toolRereadBytes"] if row["toolRereadBytes"] else 0
        out.append(f"| {row['agent']} | {row['tool']} | {row['capKB']} | {row['results']:,} | "
                   f"{row['affected']:,} | {row['bytesSaved']:,} | {share:.0f} | "
                   f"{row['rereadBytesSaved']:,} | {rshare:.0f} |")
    return "\n".join(out)


def _parse_cap(text: str) -> tuple[str, str, int]:
    try:
        agent, tool, kb = text.split(":", 2)
        return agent.strip().lower(), tool.strip(), int(kb)
    except ValueError:
        raise argparse.ArgumentTypeError(f"cap must be agent:tool:KB, got {text!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="Home containing .ensemble/.claude/.codex (default: current home)")
    parser.add_argument("--days", type=float, default=7, help="Window length ending at --end (default: 7)")
    parser.add_argument("--end", help="ISO-8601 window end (default: now, local time)")
    parser.add_argument("--top", type=int, default=20, help="Largest results to list per agent")
    parser.add_argument("--cap", type=_parse_cap, action="append", metavar="AGENT:TOOL:KB",
                        help="What-if cap to simulate (repeatable; default: a built-in set)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be greater than zero")
    end = base._timestamp(args.end) if args.end else datetime.now().astimezone()
    if end is None:
        parser.error("--end must be an ISO-8601 timestamp")
    start = end - timedelta(days=args.days)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    report = measure(args.home.expanduser(), start, end, args.top,
                     args.cap or DEFAULT_CAPS,
                     progress=lambda msg: print(msg, file=sys.stderr, flush=True))
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
