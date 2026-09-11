#!/usr/bin/env python3
"""Measure tool-result volume for Ensemble task agents from local transcripts.

Rooms are the source of scope: project PO rooms and adopted sessions are
excluded, while current, rotated, and review session ids are attributed to the
agent kind recorded by the room.  A cwd match covers older Codex reviewer
records whose CLI did not expose a session id to the hub.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


def _glob(root: Path, pattern: str) -> Iterable[Path]:
    """Glob beneath ``root``, including transcript paths over MAX_PATH."""
    if os.name == "nt":
        value = str(root.resolve())
        if not value.startswith("\\\\?\\"):
            root = Path("\\\\?\\" + value)
    return root.glob(pattern)


def _json_lines(path: Path) -> Iterable[dict]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _normal_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return os.path.normcase(os.path.abspath(os.path.expanduser(value.strip())))


def _payload_bytes(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    return len(text.encode("utf-8", errors="replace"))


def _session_ids(value: Any) -> Iterable[str]:
    """Yield session ids from participant/review/rotation records."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().endswith("sessionid") and isinstance(item, str) and item:
                yield item
            elif isinstance(item, (dict, list)):
                yield from _session_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _session_ids(item)


def _project_po_rooms(home: Path) -> set[str]:
    result: set[str] = set()
    project_files = list(_glob(home / "EnsembleProjects", "*/project.json"))
    project_files.append(home / ".ensemble" / "projects.json")
    for path in project_files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        projects = value if isinstance(value, list) else [value]
        for project in projects:
            if isinstance(project, dict) and project.get("poRoomId"):
                result.add(str(project["poRoomId"]))
    return result


def _task_scope(home: Path) -> tuple[dict[str, str], dict[str, set[str]], set[str], int]:
    """Return explicit session ids, task cwd sets, and included room count."""
    by_session: dict[str, str] = {}
    by_cwd: dict[str, set[str]] = defaultdict(set)
    po_rooms = _project_po_rooms(home)
    count = 0
    rooms = []
    for path in _glob(home / ".ensemble" / "rooms", "room-*.json"):
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(room, dict):
            rooms.append(room)
    excluded_sessions = {
        sid for room in rooms
        if room.get("adopted") or room.get("id") in po_rooms
        for sid in _session_ids(room)
    }
    for room in rooms:
        if room.get("adopted") or room.get("id") in po_rooms:
            continue
        # create_task writes ``launched``; taskDir admits tasks created before
        # that marker existed.  Ordinary/ad-hoc rooms have neither.
        if "launched" not in room and not room.get("taskDir"):
            continue
        agents = [p for p in room.get("participants", [])
                  if isinstance(p, dict) and p.get("kind") == "agent"]
        if not agents:
            continue
        count += 1
        room_cwds = {p for p in (_normal_path(room.get("cwd")),
                                 _normal_path(room.get("taskDir"))) if p}
        for part in agents:
            kind = str(part.get("agent") or "").split("-", 1)[0].lower()
            if kind not in {"claude", "codex"}:
                continue
            for session_id in _session_ids(part):
                if session_id not in excluded_sessions:
                    by_session[session_id] = kind
            paths = set(room_cwds)
            paths.add(_normal_path(part.get("cwd")))
            for record in list(part.get("reviews") or []) + [part.get("review") or {}]:
                if isinstance(record, dict):
                    paths.add(_normal_path(record.get("repo")))
            by_cwd[kind].update(p for p in paths if p)
    return by_session, by_cwd, excluded_sessions, count


def _claude_identity(path: Path, by_session: dict[str, str],
                     by_cwd: dict[str, set[str]]) -> str:
    # Claude session ids are supplied by the hub and retained in room review /
    # rotation history.  Do not fall back to cwd: Claude subagents share their
    # parent's cwd but are not themselves launched by the hub.
    return "claude" if by_session.get(path.stem) == "claude" else ""


def _codex_user_text(payload: dict) -> str:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return ""
    return "\n".join(str(block.get("text") or "")
                     for block in payload.get("content") or []
                     if isinstance(block, dict))


def _codex_identity(path: Path, by_session: dict[str, str],
                    by_cwd: dict[str, set[str]], excluded_sessions: set[str]) -> str:
    sid = cwd = ""
    hub_prompt = False
    for row in _json_lines(path):
        payload = row.get("payload") or {}
        if row.get("type") == "session_meta":
            sid = str(payload.get("id") or "")
            cwd = _normal_path(payload.get("cwd"))
        elif row.get("type") == "response_item":
            text = _codex_user_text(payload)
            if ("Coordinate ONLY through the 'ensemble' MCP chat tools" in text
                    or "fresh session started for this ONE review" in text
                    or ("When you finish this task" in text and "ensemble_report" in text)):
                hub_prompt = True
        if sid and by_session.get(sid) == "codex":
            return "codex"
    if sid in excluded_sessions:
        return ""
    # Codex mints its own id, and ended reviewers were historically not always
    # backfilled into their room.  The task cwd plus the hub's distinctive first
    # prompt attributes those sessions without admitting same-cwd subagents.
    return "codex" if hub_prompt and cwd in by_cwd["codex"] else ""


def _add(stats: dict, when: datetime | None, end: datetime, start: datetime,
         kind: str, size: int, shell: bool) -> bool:
    if when is None:
        return False
    local = when.astimezone(end.tzinfo)
    if local < start or local >= end:
        return False
    row = stats[(local.date().isoformat(), kind)]
    row["results"] += 1
    row["bytes"] += size
    if shell:
        row["shellResults"] += 1
        row["shellBytes"] += size
    return True


def _measure_claude(path: Path, stats: dict, start: datetime,
                    end: datetime) -> bool:
    calls: dict[str, str] = {}
    matched = False
    for row in _json_lines(path):
        message = row.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[str(block.get("id") or "")] = str(block.get("name") or "")
            elif block.get("type") == "tool_result":
                name = calls.get(str(block.get("tool_use_id") or ""), "")
                matched |= _add(
                    stats, _timestamp(row.get("timestamp")), end, start, "claude",
                    _payload_bytes(block.get("content")),
                    name.lower() in {"bash", "powershell"})
    return matched


def _measure_codex(path: Path, stats: dict, start: datetime,
                   end: datetime) -> bool:
    calls: dict[str, str] = {}
    matched = False
    for row in _json_lines(path):
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload") or {}
        item_type = payload.get("type")
        if item_type in {"function_call", "custom_tool_call"}:
            calls[str(payload.get("call_id") or "")] = str(payload.get("name") or "")
        elif item_type in {"function_call_output", "custom_tool_call_output"}:
            name = calls.get(str(payload.get("call_id") or ""), "")
            matched |= _add(
                stats, _timestamp(row.get("timestamp")), end, start, "codex",
                _payload_bytes(payload.get("output")),
                name.lower() in {"exec", "exec_command", "shell", "powershell"})
    return matched


def measure(home: Path, start: datetime, end: datetime) -> dict:
    by_session, by_cwd, excluded_sessions, room_count = _task_scope(home)
    stats: dict = defaultdict(lambda: {
        "results": 0, "bytes": 0, "shellResults": 0, "shellBytes": 0,
    })
    sessions = {"claude": 0, "codex": 0}
    for path in _glob(home / ".claude" / "projects", "**/*.jsonl"):
        if _claude_identity(path, by_session, by_cwd):
            sessions["claude"] += int(_measure_claude(path, stats, start, end))
    for path in _glob(home / ".codex" / "sessions", "**/rollout-*.jsonl"):
        if _codex_identity(path, by_session, by_cwd, excluded_sessions):
            sessions["codex"] += int(_measure_codex(path, stats, start, end))
    rows = []
    for (day, kind), values in sorted(stats.items()):
        rows.append({"day": day, "agent": kind, **values,
                     "approxTokens": math.ceil(values["bytes"] / 4),
                     "shellApproxTokens": math.ceil(values["shellBytes"] / 4)})
    totals = {}
    for kind in ("claude", "codex"):
        selected = [row for row in rows if row["agent"] == kind]
        size = sum(row["bytes"] for row in selected)
        shell_size = sum(row["shellBytes"] for row in selected)
        totals[kind] = {
            "results": sum(row["results"] for row in selected),
            "bytes": size,
            "approxTokens": math.ceil(size / 4),
            "shellResults": sum(row["shellResults"] for row in selected),
            "shellBytes": shell_size,
            "shellApproxTokens": math.ceil(shell_size / 4),
        }
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "taskRooms": room_count,
        "matchedSessions": sessions,
        "rows": rows,
        "totals": totals,
    }


def _markdown(report: dict) -> str:
    lines = [
        f"Window: `{report['window']['start']}` to `{report['window']['end']}`",
        f"Scope: {report['taskRooms']} task rooms; matched sessions: "
        f"Claude {report['matchedSessions']['claude']}, Codex {report['matchedSessions']['codex']}",
        "",
        "| Day | Agent | Results | Bytes | Approx tokens | Shell results | Shell bytes |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['day']} | {row['agent']} | {row['results']:,} | "
            f"{row['bytes']:,} | {row['approxTokens']:,} | "
            f"{row['shellResults']:,} | {row['shellBytes']:,} |")
    lines += ["", "| Total | Results | Bytes | Approx tokens | Shell results | Shell bytes |",
              "|---|---:|---:|---:|---:|---:|"]
    for kind, row in report["totals"].items():
        lines.append(
            f"| {kind} | {row['results']:,} | {row['bytes']:,} | "
            f"{row['approxTokens']:,} | {row['shellResults']:,} | "
            f"{row['shellBytes']:,} |")
    lines.append("\nApproximate tokens use RTK's `ceil(UTF-8 bytes / 4)` convention.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="Home containing .ensemble/.claude/.codex (default: current home)")
    parser.add_argument("--days", type=float, default=7,
                        help="Rolling window length ending at --end (default: 7)")
    parser.add_argument("--end", help="ISO-8601 window end (default: now, local time)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days must be greater than zero")
    end = _timestamp(args.end) if args.end else datetime.now().astimezone()
    if end is None:
        parser.error("--end must be an ISO-8601 timestamp")
    start = end - timedelta(days=args.days)
    report = measure(args.home.expanduser(), start, end)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
