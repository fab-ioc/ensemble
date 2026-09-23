#!/usr/bin/env python3
"""What fills the agents' conversations: internal communication against the rest.

Every record of a conversation (a Claude transcript under ``~/.claude/projects``,
a Codex rollout under ``~/.codex/sessions``) is put in one category:

* ``first``        the conversation's first prompt: a task's spec, a reviewer's
                   brief, a fresh PO's ``[rotation]`` prompt, a made PO's
                   ``[product owner]`` input;
* ``hub``          a line the hub typed, by kind (``dashboard.HUB_INPUT_KINDS``
                   plus the PO's ``[from the PO]`` line);
* ``ceo``          a human user turn: the CEO's own messages, review comments;
* ``ensemble``     the result of an Ensemble MCP tool (``ensemble_get_task``,
                   ``chat_read``, ...), by tool;
* ``docread``      a Read / ``cat`` / ``sed -n`` / ``Get-Content`` result whose
                   input names an internal document (handovers, review log,
                   reports, roadmap, skills, a room's JSON, a task folder file
                   outside ``repo/``);
* ``ownreply``     the agent's own text that ends a turn (what it says to the
                   CEO, the PO or the reviewer);
* ``ownnarr``      the agent's own text between two tool calls (narration);
* ``owninternal``  what the agent writes for others through a tool: the inputs
                   of the Ensemble tools (``chat_send``, ``ensemble_report``,
                   ``review_done``, ...) and writes of internal documents;
* ``owninput``     every other tool input: commands, edits, searches;
* ``otherresult``  every other tool result (code, shell, files, images): the
                   territory of ``measure_tool_results_by_tool.py`` (task #78);
* ``harness``      notes the CLI itself puts in user turns (system reminders,
                   task notifications, Codex's environment context, compaction
                   summaries);
* ``fixed``        the part of the fixed prompt that Codex records in its
                   rollout (base instructions, developer messages).  Claude's
                   is not in its transcript: it is estimated from the first
                   call's token usage and from the files on disk.

The agent's thinking (Claude's ``thinking`` blocks, Codex's encrypted
reasoning) is measured but kept out of the totals: Claude's API drops earlier
turns' thinking, Codex re-sends its encrypted reasoning, and neither is
communication.

Scope: the task rooms of ``measure_task_tool_results.py`` **plus the project
PO rooms**, Claude and Codex apart, per role (po, owner, reviewer).  The
re-read arithmetic is ``measure_tool_results_by_tool.py``'s: re-read bytes =
bytes x model calls that followed in the same conversation, stopping at a
compaction; a ceiling, not a bill.  Approx tokens = ceil(bytes / 4).

Read-only: transcripts, rooms and project files are opened for reading only.
``--dump DIR`` writes the largest internal texts to DIR (outside the hub's
data) so they can be rewritten by hand; ``--per-kind N`` adds the N largest
texts of every kind (a report line, a digest, a spec, a review verdict ...),
which the overall top list, made of handovers and task listings, leaves out.
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import measure_task_tool_results as base  # noqa: E402
import measure_tool_results_by_tool as bytool  # noqa: E402
import dashboard  # noqa: E402  (HUB_INPUT_KINDS, PO_MESSAGE_PREFIX, hub_input_kind)

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("first", "First prompt"),
    ("hub", "Hub-typed lines"),
    ("ceo", "CEO's own messages"),
    ("ensemble", "Ensemble tool results"),
    ("docread", "Internal document reads"),
    ("ownreply", "Own text: replies"),
    ("ownnarr", "Own text: narration between tool calls"),
    ("owninternal", "Own writes for others (Ensemble tool inputs, internal document writes)"),
    ("owninput", "Own tool inputs: commands, edits, searches"),
    ("otherresult", "Other tool results (code, shell, files, images)"),
    ("harness", "Harness notes in user turns"),
    ("fixed", "Fixed prompt recorded in the transcript (Codex)"),
)
CATEGORY_NAMES = tuple(name for name, _ in CATEGORIES)
INTERNAL = frozenset({"first", "hub", "ceo", "ensemble", "docread",
                      "ownreply", "ownnarr", "owninternal"})
# Categories whose table lists a row per kind.
DETAILED = frozenset({"first", "hub", "ensemble", "docread", "owninternal", "harness", "fixed"})
THINKING = "thinking"
ROLES = ("po", "owner", "reviewer")
ENSEMBLE_PREFIX = "mcp__ensemble__"
# The Ensemble tools whose input is text written for someone else (a message,
# a report, a verdict, a spec).  The others' inputs are a task id at most.
WRITING_TOOLS = frozenset({"chat_send", "ensemble_report", "review_done", "ensemble_create_task",
                           "ensemble_update_task", "ensemble_update_roadmap", "ensemble_points"})
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}
REVIEW_BRIEF_MARK = "fresh session started for this ONE review"
# What the hub's chat page treats as a person's line: the same rule as
# dashboard.typed_by_person, applied to a text turn.
FROM_PO_KIND = "from the PO"

_DOC_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    ("PO-HANDOVER", re.compile(r"PO-HANDOVER[\w.-]*\.md")),
    ("TASK-HANDOVER", re.compile(r"TASK-HANDOVER\.md")),
    ("REVIEW-LOG", re.compile(r"REVIEW-LOG\.md")),
    ("PO-CHECK", re.compile(r"PO-CHECK\.md")),
    ("ROADMAP", re.compile(r"ROADMAP\.md")),
    ("SKILL", re.compile(r"SKILL\.md")),
    ("REPORT", re.compile(r"[\w-]*REPORT[\w-]*\.md")),
    ("room json", re.compile(r"room-[0-9a-f]{6,}\.json")),
)
_READ_VERB = re.compile(r"(?<![\w-])(?:cat|sed|head|tail|less|more|type|gc|Get-Content)(?![\w-])")
_WRITE_VERB = re.compile(r"(?<![\w-])(?:Set-Content|Out-File|Add-Content|tee)(?![\w-])|>>?\s*\S")
_RE_POINT = re.compile(r"\bRe P\d+[a-z]?\s*:")
_REVIEW_KIND = re.compile(r"^review \d+\s*(\(.*\))?$")
_PASTED = re.compile(r'<pasted_content id="([^"]*)">\n?(.*?)\n?</pasted_content id="\1">', re.S)


def _unwrap_pasted(text: str) -> str:
    """The hub types a multi-line message as a bracketed paste; Claude Code
    logs it wrapped (dashboard._unwrap_pasted has the same rule)."""
    return _PASTED.sub(lambda m: m.group(2), text) if "<pasted_content" in text else text


def _norm_dir(value: Any) -> str:
    """A task folder as it is looked for inside a command: forward slashes,
    lower case, no trailing slash."""
    return base._normal_path(value).replace("\\", "/").rstrip("/") if value else ""


def doc_kind(text: str, task_dirs: Iterable[str] = ()) -> str:
    """Which internal document ``text`` (a path or a command) names, or ''."""
    if not text:
        return ""
    for name, rx in _DOC_KINDS:
        if rx.search(text):
            return name
    # A command inside Codex's JavaScript carries doubled backslashes.
    low = re.sub(r"/+", "/", text.replace("\\", "/")).lower()
    for folder in task_dirs:
        i = low.find(folder + "/")
        while i >= 0:
            rest = low[i + len(folder) + 1:]
            if rest and not rest.startswith("repo/") and not rest.startswith("repo\""):
                return "task folder"
            i = low.find(folder + "/", i + 1)
    return ""


def short_tool(name: str) -> str:
    return name[len(ENSEMBLE_PREFIX):] if name.startswith(ENSEMBLE_PREFIX) else name


def is_ensemble(name: str) -> bool:
    return name.startswith(ENSEMBLE_PREFIX)


def classify_user_text(text: str, first_done: bool, meta: bool = False) -> tuple[str, str, dict]:
    """(category, kind, extra) of a user turn's text."""
    s = _unwrap_pasted(text).lstrip()
    if meta:
        return "harness", "meta", {}
    if s.startswith("<") or s.startswith("Caveat:"):
        tag = re.match(r"<([\w-]+)", s)
        return "harness", tag.group(1) if tag else "tagged", {}
    info = dashboard.hub_input_kind(s)
    kind = info.get("kind", "human")
    if kind != "human":
        if kind in {"rotation", "madepo"} and not first_done:
            return "first", kind, {}
        extra = {}
        if kind == "report":
            rk = str(info.get("reportKind") or "")
            extra = {"reportKind": _REVIEW_KIND.sub(lambda m: "review " + (m.group(1) or ""), rk).strip(),
                     "taskId": info.get("taskId") or "", "reporter": info.get("reporter") or ""}
        return "hub", kind, extra
    if s.startswith(dashboard.PO_MESSAGE_PREFIX):
        return "hub", FROM_PO_KIND, {}
    if not first_done:
        return "first", "review brief" if REVIEW_BRIEF_MARK in s else "spec", {}
    return "ceo", "message", {}


def classify_result(tool: str, inp: Any, payload: Any, task_dirs: Iterable[str],
                    commands: Iterable[str] = ()) -> tuple[str, str]:
    """(category, kind) of a tool result."""
    if is_ensemble(tool):
        return "ensemble", short_tool(tool)
    if bytool._has_image(payload):
        return "otherresult", "image"
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Read":
        doc = doc_kind(str(inp.get("file_path") or ""), task_dirs)
        if doc:
            return "docread", doc
    elif tool.lower() in bytool.SHELL_TOOLS or tool in bytool.CODEX_SHELL:
        command = str(inp.get("command") or "") if inp else " ".join(commands)
        if _READ_VERB.search(command) and not _WRITE_VERB.search(command):
            doc = doc_kind(command, task_dirs)
            if doc:
                return "docread", doc
    return "otherresult", "text"


def classify_input(tool: str, inp: Any, task_dirs: Iterable[str],
                   commands: Iterable[str] = ()) -> tuple[str, str]:
    """(category, kind) of a tool call's input."""
    if is_ensemble(tool):
        name = short_tool(tool)
        return ("owninternal", name) if name in WRITING_TOOLS else ("owninput", "other")
    inp = inp if isinstance(inp, dict) else {}
    if tool in EDIT_TOOLS:
        doc = doc_kind(str(inp.get("file_path") or inp.get("notebook_path") or ""), task_dirs)
        return ("owninternal", "write " + doc) if doc else ("owninput", "edit")
    if tool.lower() in bytool.SHELL_TOOLS or tool in bytool.CODEX_SHELL:
        command = str(inp.get("command") or "") if inp else " ".join(commands)
        if _WRITE_VERB.search(command):
            doc = doc_kind(command, task_dirs)
            if doc:
                return "owninternal", "write " + doc
        return "owninput", "shell"
    if tool in READ_TOOLS:
        return "owninput", "read/search"
    if tool == "Agent":
        return "owninput", "agent prompt"
    return "owninput", "other"


# --- rooms -----------------------------------------------------------------

def _role_head(part: dict) -> str:
    return ((part or {}).get("role") or "").split(":", 1)[0].strip().lower()


def conversation_scope(home: Path) -> dict:
    """Which conversations count, and as what.

    ``sessions``: session id -> {"role", "room"} for every session a task
    room or a project PO room records (current, rotated, review sessions).
    ``kinds``: session id -> agent kind, in the shape base._codex_identity
    takes.  ``cwds``: the task folders and checkouts of those rooms, for
    Codex sessions whose id no room kept.  ``task_dirs``: the task folders,
    normalised for doc_kind.  ``excluded``: sessions of rooms out of scope
    (adopted, ad-hoc), so a cwd match never admits them.
    """
    po_rooms = base._project_po_rooms(home)
    sessions: dict[str, dict] = {}
    kinds: dict[str, str] = {}
    cwds: dict[str, dict] = {}
    task_dirs: set[str] = set()
    excluded: set[str] = set()
    counts = {"po": 0, "task": 0}
    for path in base._glob(home / ".ensemble" / "rooms", "room-*.json"):
        try:
            room = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(room, dict):
            continue
        is_po = room.get("id") in po_rooms
        if not is_po and (room.get("adopted") or ("launched" not in room and not room.get("taskDir"))):
            excluded.update(base._session_ids(room))
            continue
        agents = [p for p in room.get("participants", [])
                  if isinstance(p, dict) and p.get("kind") == "agent"]
        if not agents:
            continue
        counts["po" if is_po else "task"] += 1
        info = {"room": str(room.get("id") or ""), "task": room.get("no"),
                "title": bytool._one_line(room.get("title"), 60),
                "project": str(room.get("projectId") or ""), "po": is_po}
        if room.get("taskDir"):
            task_dirs.add(_norm_dir(room.get("taskDir")))
        room_cwds = {base._normal_path(room.get(k)) for k in ("cwd", "taskDir")}
        for part in agents:
            kind = str(part.get("agent") or "").split("-", 1)[0].lower()
            role = "reviewer" if _role_head(part) == "reviewer" else ("po" if is_po else "owner")
            review_sids = set()
            for rec in list(part.get("reviews") or []) + [part.get("review") or {}]:
                if isinstance(rec, dict):
                    review_sids.update(base._session_ids(rec))
                    room_cwds.add(base._normal_path(rec.get("repo")))
            session_kinds = part.get("sessionKinds") if isinstance(part.get("sessionKinds"), dict) else {}
            for sid in set(base._session_ids(part)) | set(session_kinds):
                sessions.setdefault(sid, {"role": "reviewer" if sid in review_sids else role, "room": info})
                kinds.setdefault(sid, str(session_kinds.get(sid) or kind))
            room_cwds.add(base._normal_path(part.get("cwd")))
        for cwd in room_cwds:
            if cwd:
                cwds.setdefault(cwd, {"room": info, "role": "po" if is_po else "owner"})
    return {"sessions": sessions, "kinds": kinds, "cwds": cwds, "excluded": excluded,
            "task_dirs": sorted(task_dirs), "rooms": counts}


# --- records ---------------------------------------------------------------

def _record(agent: str, role: str, room: dict, cat: str, kind: str, size: int,
            when: datetime, end: datetime, session: str, preview: str,
            text: str | None = None, **extra) -> dict:
    rec = {
        "agent": agent, "role": role, "room": room.get("room", ""),
        "task": room.get("task"), "title": room.get("title", ""),
        "cat": cat, "kind": kind, "bytes": size,
        "when": when.astimezone(timezone.utc).isoformat(),
        "day": when.astimezone(end.tzinfo).date().isoformat(),
        "session": session, "preview": preview,
        "remainingTurns": 0, "rereadBytes": 0,
    }
    rec.update(extra)
    if text is not None and cat in INTERNAL:
        rec["text"] = text
    return rec


def _usage_total(usage: dict) -> int:
    return int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0) \
        + int(usage.get("cache_read_input_tokens") or 0)


class _Session:
    """What one conversation file yields: records, model calls and usage."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.calls = 0                  # model calls in the window
        self.input_tokens = 0           # their measured input tokens (cached included)
        self.first_call_input: int | None = None
        self.first_prompt_bytes = 0
        self.first_prompt_in_window = False
        # What the transcript shows the first call held (the first prompt, the
        # harness notes beside it, Codex's recorded instructions): the rest of
        # that call's input tokens is the fixed prompt the transcript omits.
        self.visible_first_call = 0
        self.first_call_seen = False

    def add(self, conv: bytool._Conversation, rec: dict | None) -> None:
        if rec is None:
            return
        conv.events.append(("result", rec))
        self.records.append(rec)
        if not self.first_call_seen and rec["cat"] != THINKING:
            self.visible_first_call += rec["bytes"]


def scan_claude(path: Path, start: datetime, end: datetime, role: str, room: dict,
                task_dirs: list[str]) -> _Session:
    out = _Session()
    calls: dict[str, dict] = {}
    conversations: dict[str, bytool._Conversation] = defaultdict(bytool._Conversation)
    seen_requests: set[str] = set()
    turned: set[str] = set()
    first_done = False
    sid = path.stem
    add = out.add

    for row in base._json_lines(path):
        rtype = row.get("type")
        when = base._timestamp(row.get("timestamp"))
        if rtype == "attachment" and not row.get("isSidechain"):
            att = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
            prompt = att.get("prompt")
            if (att.get("type") == "queued_command" and isinstance(prompt, str) and prompt.strip()
                    and att.get("commandMode") in (None, "prompt")):
                when = base._timestamp(att.get("timestamp")) or when
                conv = conversations[""]
                cat, kind, extra = classify_user_text(prompt, first_done)
                if cat == "first":
                    first_done = True
                if bytool._in_window(when, start, end):
                    add(conv, _record("claude", role, room, cat, kind, base._payload_bytes(prompt),
                                      when, end, sid, bytool._one_line(prompt), prompt, **extra))
            continue
        if rtype not in {"assistant", "user"}:
            continue
        key = str(row.get("agentId") or "")
        conv = conversations[key]
        message = row.get("message") or {}
        in_window = bytool._in_window(when, start, end)
        content = message.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if row.get("isCompactSummary"):
            conv.events.append(("compact",))
            text = "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
            if in_window and text:
                add(conv, _record("claude", role, room, "harness", "compact summary",
                                  base._payload_bytes(text), when, end, sid, bytool._one_line(text)))
            continue
        if rtype == "assistant":
            # One API call streams several assistant rows (thinking, text,
            # tool_use) under one request id: one turn event, before the
            # first of them, so nothing the call produced counts it as a
            # later call.
            rid = str(row.get("requestId") or row.get("uuid") or id(row))
            if rid not in turned:
                turned.add(rid)
                conv.events.append(("turn", rid))
            out.first_call_seen = True
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
            if usage and rid not in seen_requests:
                seen_requests.add(rid)
                total = _usage_total(usage)
                if out.first_call_input is None:
                    out.first_call_input = total
                if in_window:
                    out.calls += 1
                    out.input_tokens += total
            narration = message.get("stop_reason") == "tool_use"
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = str(block.get("text") or "")
                    if not text.strip():
                        continue
                    cat = "ownnarr" if narration else "ownreply"
                    if in_window:
                        add(conv, _record("claude", role, room, cat, cat, base._payload_bytes(text),
                                          when, end, sid, bytool._one_line(text), text))
                elif btype == "thinking":
                    text = str(block.get("thinking") or "")
                    if in_window and text:
                        add(conv, _record("claude", role, room, THINKING, THINKING,
                                          base._payload_bytes(text), when, end, sid, ""))
                elif btype == "tool_use":
                    calls[str(block.get("id") or "")] = block
                    name = str(block.get("name") or "(unknown)")
                    inp = block.get("input")
                    cat, kind = classify_input(name, inp, task_dirs)
                    if in_window:
                        text = None
                        if cat == "owninternal" and isinstance(inp, dict):
                            text = str(inp.get("message") or inp.get("text") or inp.get("findings")
                                       or inp.get("content") or inp.get("spec") or "")
                            if inp.get("summary") and inp.get("findings"):
                                text = f"{inp['summary']}\n\n{text}"
                        add(conv, _record("claude", role, room, cat, kind, base._payload_bytes(inp),
                                          when, end, sid, bytool._preview(name, inp), text, tool=name))
            continue
        meta = bool(row.get("isMeta"))
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                call = calls.get(str(block.get("tool_use_id") or ""), {})
                name = str(call.get("name") or "(unknown)")
                inp = call.get("input")
                payload = block.get("content")
                cat, kind = classify_result(name, inp, payload, task_dirs)
                if in_window:
                    text = bytool._text(payload) if cat in INTERNAL else None
                    add(conv, _record("claude", role, room, cat, kind, base._payload_bytes(payload),
                                      when, end, sid, bytool._preview(name, inp), text, tool=name))
            elif btype == "text":
                text = str(block.get("text") or "")
                if not text.strip():
                    continue
                cat, kind, extra = classify_user_text(text, first_done, meta)
                if cat == "first":
                    first_done = True
                    out.first_prompt_bytes = base._payload_bytes(text)
                    out.first_prompt_in_window = in_window
                if in_window:
                    add(conv, _record("claude", role, room, cat, kind, base._payload_bytes(text),
                                      when, end, sid, bytool._one_line(_unwrap_pasted(text)),
                                      _unwrap_pasted(text), **extra))
    for conv in conversations.values():
        conv.resolve()
    return out


def _codex_names(call: dict) -> list[str]:
    inp = call.get("input")
    return list(dict.fromkeys(bytool._TOOLS_RE.findall(inp))) if isinstance(inp, str) else []


def scan_codex(path: Path, start: datetime, end: datetime, role: str, room: dict,
               task_dirs: list[str]) -> _Session:
    """One Codex rollout.  Model calls are counted as in
    measure_tool_results_by_tool.scan_codex (a run of model-produced items)."""
    out = _Session()
    calls: dict[str, dict] = {}
    conv = bytool._Conversation()
    first_done = False
    in_run = False
    turns = 0
    sid = path.stem
    first_usage_seen = False

    def add(rec: dict) -> None:
        out.add(conv, rec)

    for row in base._json_lines(path):
        rtype = row.get("type")
        payload = row.get("payload") or {}
        when = base._timestamp(row.get("timestamp"))
        in_window = bytool._in_window(when, start, end)
        if rtype == "session_meta":
            sid = str(payload.get("id") or sid)
            text = str((payload.get("base_instructions") or {}).get("text") or "")
            if in_window and text:
                add(_record("codex", role, room, "fixed", "base instructions",
                            base._payload_bytes(text), when, end, sid, bytool._one_line(text)))
            continue
        if rtype == "compacted":
            conv.events.append(("compact",))
            in_run = False
            continue
        if rtype == "token_usage_record":
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            tokens = int(usage.get("input_tokens") or 0)
            if not first_usage_seen:
                first_usage_seen = True
                out.first_call_input = tokens
            if in_window:
                out.calls += 1
                out.input_tokens += tokens
            continue
        if rtype != "response_item":
            continue
        item = payload.get("type")
        model_item = item in bytool.CODEX_MODEL_ITEMS or (
            item == "message" and payload.get("role") == "assistant")
        if model_item:
            out.first_call_seen = True
            if not in_run:
                turns += 1
                conv.events.append(("turn", str(turns)))
                in_run = True
        elif item in bytool.CODEX_INPUT_ITEMS or item == "message":
            in_run = False
        if item == "message":
            text = "\n".join(str(b.get("text") or "") for b in payload.get("content") or []
                             if isinstance(b, dict))
            if not text.strip():
                continue
            role_of = payload.get("role")
            if role_of == "assistant":
                cat = "ownnarr" if payload.get("phase") == "commentary" else "ownreply"
                kind, extra = cat, {}
            elif role_of == "developer":
                cat, kind, extra = "fixed", "developer", {}
            else:
                cat, kind, extra = classify_user_text(text, first_done)
                if cat == "first":
                    first_done = True
                    out.first_prompt_bytes = base._payload_bytes(text)
                    out.first_prompt_in_window = in_window
            if in_window:
                add(_record("codex", role, room, cat, kind, base._payload_bytes(text),
                            when, end, sid, bytool._one_line(text), text, **extra))
        elif item == "reasoning":
            size = base._payload_bytes(payload.get("encrypted_content")) + base._payload_bytes(payload.get("summary"))
            if in_window and size:
                add(_record("codex", role, room, THINKING, THINKING, size, when, end, sid, ""))
        elif item in {"function_call", "custom_tool_call"}:
            calls[str(payload.get("call_id") or "")] = payload
            tool, preview, commands = bytool.codex_tool(payload)
            names = _codex_names(payload) if tool == bytool.CODEX_MIXED else []
            raw = payload.get("input") if payload.get("input") is not None else payload.get("arguments")
            if names and all(is_ensemble(n) for n in names):
                writing = [short_tool(n) for n in names if short_tool(n) in WRITING_TOOLS]
                cat, kind = ("owninternal", "batch: " + "+".join(writing)) if writing else ("owninput", "other")
            else:
                cat, kind = classify_input(tool, None, task_dirs, commands)
            if in_window:
                add(_record("codex", role, room, cat, kind, base._payload_bytes(raw), when, end, sid,
                            preview, str(raw) if cat == "owninternal" else None, tool=tool))
        elif item in bytool.CODEX_INPUT_ITEMS:
            call = calls.get(str(payload.get("call_id") or ""), {})
            tool, preview, commands = bytool.codex_tool(call)
            names = _codex_names(call) if tool == bytool.CODEX_MIXED else []
            output = payload.get("output")
            if names and all(is_ensemble(n) for n in names):
                cat, kind = "ensemble", "batch: " + "+".join(short_tool(n) for n in names)
            else:
                cat, kind = classify_result(tool, None, output, task_dirs, commands)
                if cat == "otherresult" and tool == bytool.CODEX_MIXED:
                    kind = "mixed batch"
            if in_window:
                text = bytool._text(output) if cat in INTERNAL else None
                add(_record("codex", role, room, cat, kind, base._payload_bytes(output), when, end, sid,
                            preview, text, tool=tool))
    conv.resolve()
    return out


def _codex_role(path: Path, scope: dict) -> tuple[str, dict] | None:
    """(role, room) of a Codex rollout, or None when it is out of scope."""
    if not base._codex_identity(path, scope["kinds"], {"codex": set(scope["cwds"])}, scope["excluded"]):
        return None
    sid = cwd = ""
    for row in base._json_lines(path):
        if row.get("type") == "session_meta":
            payload = row.get("payload") or {}
            sid = str(payload.get("id") or "")
            cwd = base._normal_path(payload.get("cwd"))
            break
    known = scope["sessions"].get(sid)
    if known:
        return known["role"], known["room"]
    by_cwd = scope["cwds"].get(cwd)
    if not by_cwd:
        return None
    role = by_cwd["role"]
    if role != "po":
        role = "owner"
        for row in base._json_lines(path):
            if row.get("type") == "response_item":
                text = base._codex_user_text(row.get("payload") or {})
                if text and not text.lstrip().startswith("<"):
                    role = "reviewer" if REVIEW_BRIEF_MARK in text else "owner"
                    break
    return role, by_cwd["room"]


# --- aggregation -----------------------------------------------------------

def _empty() -> dict:
    return {"count": 0, "bytes": 0, "rereadBytes": 0}


def _fill(row: dict, rec: dict) -> None:
    row["count"] += 1
    row["bytes"] += rec["bytes"]
    row["rereadBytes"] += rec["rereadBytes"]


def _with_tokens(row: dict) -> dict:
    return {**row, "approxTokens": math.ceil(row["bytes"] / 4),
            "rereadApproxTokens": math.ceil(row["rereadBytes"] / 4)}


def _group(records: list[dict]) -> dict:
    """Category and kind totals of one (agent, role) slice."""
    cats: dict[str, dict] = {name: _empty() for name in CATEGORY_NAMES}
    kinds: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(_empty))
    thinking = _empty()
    total = _empty()
    for rec in records:
        if rec["cat"] == THINKING:
            _fill(thinking, rec)
            continue
        _fill(cats[rec["cat"]], rec)
        _fill(kinds[rec["cat"]][rec["kind"]], rec)
        _fill(total, rec)
    internal = _empty()
    for name in INTERNAL:
        for key in internal:
            internal[key] += cats[name][key]
    return {
        "total": _with_tokens(total),
        "internal": _with_tokens(internal),
        "internalSharePayload": round(100 * internal["bytes"] / total["bytes"], 1) if total["bytes"] else 0.0,
        "internalShareReread": round(100 * internal["rereadBytes"] / total["rereadBytes"], 1) if total["rereadBytes"] else 0.0,
        "categories": {name: _with_tokens(cats[name]) for name in CATEGORY_NAMES},
        "kinds": {name: {k: _with_tokens(v) for k, v in
                         sorted(kinds[name].items(), key=lambda kv: -kv[1]["bytes"])}
                  for name in CATEGORY_NAMES if name in DETAILED},
        "thinking": _with_tokens(thinking),
        "conversations": len({r["session"] for r in records}),
    }


def _signals(records: list[dict]) -> dict:
    """What the data says about misreads: reports by kind, tasks that reported
    completed more than once, the PO's follow-up lines to tasks, answers to
    points.  It counts events; whether a follow-up was a misread or a
    legitimate next step is not in the transcript."""
    reports: dict[str, int] = defaultdict(int)
    completed_by_task: dict[str, int] = defaultdict(int)
    for rec in records:
        if rec["cat"] == "hub" and rec["kind"] == "report":
            rk = rec.get("reportKind") or "(unparsed)"
            reports[rk] += 1
            if rk == "completed" and rec.get("taskId"):
                completed_by_task[rec["taskId"]] += 1
    from_po = [r for r in records if r["cat"] == "hub" and r["kind"] == FROM_PO_KIND]
    re_points = sum(1 for r in records if r["cat"] in {"ownreply", "owninternal"}
                    and _RE_POINT.search(r.get("text") or ""))
    return {
        "reportsByKind": dict(sorted(reports.items(), key=lambda kv: -kv[1])),
        "tasksReportingCompleted": len(completed_by_task),
        "tasksReportingCompletedMoreThanOnce": sum(1 for n in completed_by_task.values() if n > 1),
        "extraCompletedReports": sum(n - 1 for n in completed_by_task.values() if n > 1),
        "fromPoLines": len(from_po),
        "fromPoLinesRooms": len({r["room"] for r in from_po}),
        "rePointAnswers": re_points,
    }


def _fixed_estimate(sessions: list[_Session], agent: str) -> dict:
    """The fixed prompt per call, from the first model call of the sessions
    that started in the window: measured input tokens of that call minus the
    first prompt's approx tokens (Claude), or minus everything the rollout
    shows the call held (Codex: base instructions, developer messages,
    environment context, first prompt)."""
    values = []
    for s in sessions:
        if s.first_call_input is None or not s.first_prompt_in_window:
            continue
        values.append(max(0, s.first_call_input - math.ceil(s.visible_first_call / 4)))
    values.sort()
    if not values:
        return {"sessions": 0}
    return {"sessions": len(values), "medianTokens": values[len(values) // 2],
            "minTokens": values[0], "maxTokens": values[-1]}


def _files_on_disk(home: Path, repo: Path) -> list[dict]:
    """Sizes of what is sent on every call but never appears in a Claude
    transcript: an estimate per call from the files on disk."""
    rows = []

    def add(label: str, size: int, note: str = "") -> None:
        rows.append({"item": label, "bytes": size, "approxTokens": math.ceil(size / 4), "note": note})

    for label, path in (("CLAUDE.md (repo)", repo / "CLAUDE.md"), ("~/.claude/CLAUDE.md", home / ".claude" / "CLAUDE.md"),
                        ("AGENTS.md (repo)", repo / "AGENTS.md"), ("~/.codex/AGENTS.md", home / ".codex" / "AGENTS.md")):
        try:
            add(label, path.stat().st_size)
        except OSError:
            add(label, 0, "not present")
    for root, label in ((home / ".claude" / "skills", "Claude skills: front matter (name + description)"),
                        (home / ".codex" / "skills", "Codex skills: front matter (name + description)")):
        size = n = 0
        for skill in base._glob(root, "**/SKILL.md"):
            try:
                head = skill.read_text(encoding="utf-8", errors="replace").split("---", 2)
            except OSError:
                continue
            if len(head) >= 3:
                size += len(head[1].encode("utf-8"))
                n += 1
        add(label, size, f"{n} skills")
    try:
        import ensemble_tools  # noqa: WPS433
        tools = ensemble_tools.TOOLS
        common = ensemble_tools.COMMON_TOOLS
        add("Ensemble MCP tool definitions: PO / planner (all task tools)",
            len(json.dumps(tools, ensure_ascii=False).encode("utf-8")), f"{len(tools)} tools, chat tools not included")
        add("Ensemble MCP tool definitions: owner (common tools)",
            len(json.dumps(common, ensure_ascii=False).encode("utf-8")), f"{len(common)} tools, chat tools not included")
        add("Ensemble MCP tool definitions: reviewer (common + review_done)",
            len(json.dumps(common + ensemble_tools.REVIEW_TOOLS, ensure_ascii=False).encode("utf-8")),
            f"{len(common) + len(ensemble_tools.REVIEW_TOOLS)} tools, chat tools not included")
    except Exception as e:  # noqa: BLE001
        add("Ensemble MCP tool definitions", 0, f"not sized: {e}")
    memory = home / ".claude" / "projects"
    sizes = [p.stat().st_size for p in base._glob(memory, "*/memory/MEMORY.md")]
    add("Claude memory index (MEMORY.md, largest project)", max(sizes, default=0), f"{len(sizes)} projects")
    return rows


def top_internal(records: list[dict], n: int) -> list[dict]:
    """The n largest internal texts by re-read bytes, narration excluded."""
    pool = [r for r in records if r["cat"] in INTERNAL and r["cat"] != "ownnarr"]
    return sorted(pool, key=lambda r: (-r["rereadBytes"], -r["bytes"]))[:n]


def top_per_kind(records: list[dict], n: int) -> list[dict]:
    """The n largest internal texts of every category/kind, by re-read bytes."""
    by_kind: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in top_internal(records, len(records)):
        by_kind[(r["cat"], r["kind"])].append(r)
    return [r for key in sorted(by_kind) for r in by_kind[key][:n]]


def _top_row(rec: dict) -> dict:
    return {k: rec.get(k) for k in ("agent", "role", "cat", "kind", "tool", "bytes", "remainingTurns",
                                    "rereadBytes", "task", "room", "title", "preview", "when", "reportKind")}


def dump_texts(rows: list[dict], folder: Path, prefix: str = "") -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for i, rec in enumerate(rows, 1):
        task = f"t{rec['task']}" if rec.get("task") is not None else (rec.get("room") or "room")
        kind = re.sub(r"[^\w.-]+", "_", f"{rec['cat']}-{rec['kind']}")[:40]
        name = f"{prefix}{i:02d}-{rec['agent']}-{rec['role']}-{kind}-{task}.md"
        head = (f"<!-- {rec['cat']}/{rec['kind']} {rec['agent']} {rec['role']} task {rec.get('task')} "
                f"room {rec.get('room')} bytes {rec['bytes']} turns-after {rec['remainingTurns']} "
                f"re-read {rec['rereadBytes']} at {rec['when']} -->\n")
        (folder / name).write_text(head + (rec.get("text") or ""), encoding="utf-8")
        names.append(name)
    return names


def measure(home: Path, start: datetime, end: datetime, top: int = 30,
            progress=None, repo: Path | None = None, per_kind: int = 0) -> dict:
    began = time.monotonic()
    scope = conversation_scope(home)
    records: list[dict] = []
    sessions: dict[str, list[_Session]] = {"claude": [], "codex": []}
    matched = {("claude", r): 0 for r in ROLES}
    matched.update({("codex", r): 0 for r in ROLES})
    scanned = 0

    def tick() -> None:
        nonlocal scanned
        scanned += 1
        if progress and scanned % 50 == 0:
            progress(f"scanned {scanned} sessions, {len(records):,} records so far, "
                     f"{time.monotonic() - began:.0f}s")

    for path in base._glob(home / ".claude" / "projects", "**/*.jsonl"):
        if not base._claude_identity(path, scope["kinds"], {}):
            continue
        known = scope["sessions"].get(path.stem)
        if not known:
            continue
        found = scan_claude(path, start, end, known["role"], known["room"], scope["task_dirs"])
        tick()
        if found.records:
            matched[("claude", known["role"])] += 1
            sessions["claude"].append(found)
            records.extend(found.records)
    for path in base._glob(home / ".codex" / "sessions", "**/rollout-*.jsonl"):
        who = _codex_role(path, scope)
        if not who:
            continue
        role, room = who
        found = scan_codex(path, start, end, role, room, scope["task_dirs"])
        tick()
        if found.records:
            matched[("codex", role)] += 1
            sessions["codex"].append(found)
            records.extend(found.records)
    agents: dict[str, dict] = {}
    for agent in ("claude", "codex"):
        rows = [r for r in records if r["agent"] == agent]
        by_role = {role: _group([r for r in rows if r["role"] == role]) for role in ROLES}
        agents[agent] = {
            "all": _group(rows),
            "roles": by_role,
            "sessions": {role: matched[(agent, role)] for role in ROLES},
            "usage": {
                "calls": sum(s.calls for s in sessions[agent]),
                "inputTokens": sum(s.input_tokens for s in sessions[agent]),
            },
            "fixedPromptFromUsage": _fixed_estimate(sessions[agent], agent),
            "signals": _signals(rows),
        }
    largest = top_internal(records, top)
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "rooms": scope["rooms"],
        "elapsedSeconds": round(time.monotonic() - began, 1),
        "agents": agents,
        "top": [_top_row(r) for r in largest],
        "topRecords": largest,
        "perKindRecords": top_per_kind(records, per_kind) if per_kind else [],
        "filesOnDisk": _files_on_disk(home, repo or HERE.parent),
    }


# --- output ----------------------------------------------------------------

def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}" if whole else "0.0"


def _table(group: dict) -> list[str]:
    total = group["total"]
    out = ["| Category | Kind | Count | Bytes | Approx tokens | Re-read bytes | % payload | % re-read |",
           "|---|---|---:|---:|---:|---:|---:|---:|"]
    for name, label in CATEGORIES:
        row = group["categories"][name]
        if not row["count"]:
            continue
        out.append(f"| **{label}** | | {row['count']:,} | {row['bytes']:,} | {row['approxTokens']:,} | "
                   f"{row['rereadBytes']:,} | {_pct(row['bytes'], total['bytes'])} | "
                   f"{_pct(row['rereadBytes'], total['rereadBytes'])} |")
        for kind, krow in group["kinds"].get(name, {}).items():
            out.append(f"| | {kind} | {krow['count']:,} | {krow['bytes']:,} | {krow['approxTokens']:,} | "
                       f"{krow['rereadBytes']:,} | {_pct(krow['bytes'], total['bytes'])} | "
                       f"{_pct(krow['rereadBytes'], total['rereadBytes'])} |")
    internal = group["internal"]
    out.append(f"| **Internal communication (first + hub + CEO + Ensemble results + document reads + own text + own writes for others)** | | "
               f"{internal['count']:,} | {internal['bytes']:,} | {internal['approxTokens']:,} | {internal['rereadBytes']:,} | "
               f"**{group['internalSharePayload']}** | **{group['internalShareReread']}** |")
    out.append(f"| **Everything counted** | | {total['count']:,} | {total['bytes']:,} | {total['approxTokens']:,} | "
               f"{total['rereadBytes']:,} | 100 | 100 |")
    th = group["thinking"]
    if th["count"]:
        out.append(f"\nNot counted above: the agent's own thinking, {th['count']:,} blocks, {th['bytes']:,} bytes "
                   f"(re-read {th['rereadBytes']:,} if it were re-sent).")
    return out


def _markdown(report: dict) -> str:
    out = [
        f"Window: `{report['window']['start']}` to `{report['window']['end']}`  ",
        f"Scope: {report['rooms']['task']} task rooms + {report['rooms']['po']} project PO rooms; scan {report['elapsedSeconds']}s",
        "",
        "Re-read bytes = bytes x model calls that followed in the same conversation (stops at a compaction; "
        "ignores prompt caching, so it is a ceiling). Approx tokens = ceil(bytes / 4).",
    ]
    for agent, data in report["agents"].items():
        sessions = ", ".join(f"{role} {n}" for role, n in data["sessions"].items())
        g = data["all"]
        out += ["", f"## {agent.capitalize()}: sessions with records {sessions}; internal communication "
                    f"{g['internalSharePayload']}% of payload, {g['internalShareReread']}% of re-read bytes", ""]
        out += _table(g)
        for role in ROLES:
            rg = data["roles"][role]
            if not rg["total"]["count"]:
                continue
            out += ["", f"### {agent.capitalize()} {role}: {rg['conversations']} conversations, "
                        f"internal {rg['internalSharePayload']}% of payload, {rg['internalShareReread']}% of re-read", ""]
            out += _table(rg)
        usage = data["usage"]
        fixed = data["fixedPromptFromUsage"]
        out += ["", f"Measured input tokens of {agent}'s model calls in the window (from the transcripts' usage records, "
                    f"cached input included): {usage['inputTokens']:,} over {usage['calls']:,} calls."]
        if fixed.get("sessions"):
            out.append(f"Fixed prompt per call, estimated from the first call of {fixed['sessions']} sessions started in the window "
                       f"(first call's input tokens minus what the transcript shows it held): median {fixed['medianTokens']:,} tokens "
                       f"(min {fixed['minTokens']:,}, max {fixed['maxTokens']:,}). An estimate per call, not measured content.")
        sig = data["signals"]
        out += ["", f"Signals ({agent}): reports by kind {json.dumps(sig['reportsByKind'])}; tasks that reported completed "
                    f"{sig['tasksReportingCompleted']}, of which more than once {sig['tasksReportingCompletedMoreThanOnce']} "
                    f"({sig['extraCompletedReports']} extra completed reports); `[from the PO]` lines {sig['fromPoLines']} in "
                    f"{sig['fromPoLinesRooms']} rooms; replies answering a point (`Re Pn:`) {sig['rePointAnswers']}."]
    out += ["", "## Fixed prompt: files on disk (an estimate per call, not measured)", "",
            "| Item | Bytes | Approx tokens | Note |", "|---|---:|---:|---|"]
    for row in report["filesOnDisk"]:
        out.append(f"| {row['item']} | {row['bytes']:,} | {row['approxTokens']:,} | {row['note']} |")
    out += ["", f"## The {len(report['top'])} largest internal texts by re-read bytes", "",
            "| # | Agent | Role | Kind | Task | Bytes | Turns after | Re-read bytes | Preview |",
            "|---:|---|---|---|---|---:|---:|---:|---|"]
    for i, row in enumerate(report["top"], 1):
        task = f"#{row['task']}" if row.get("task") is not None else (row.get("room") or "?")
        kind = f"{row['cat']}/{row['kind']}" + (f" ({row['reportKind']})" if row.get("reportKind") else "")
        preview = (row.get("preview") or "").replace("|", "\\|").replace("`", "'")[:80]
        out.append(f"| {i} | {row['agent']} | {row['role']} | {kind} | {task} | {row['bytes']:,} | "
                   f"{row['remainingTurns']} | {row['rereadBytes']:,} | `{preview}` |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--home", type=Path, default=Path.home(),
                        help="Home containing .ensemble/.claude/.codex (default: current home)")
    parser.add_argument("--days", type=float, default=7, help="Window length ending at --end (default: 7)")
    parser.add_argument("--end", help="ISO-8601 window end (default: now, local time)")
    parser.add_argument("--top", type=int, default=30, help="Largest internal texts to list")
    parser.add_argument("--dump", type=Path, help="Write the largest internal texts to this folder")
    parser.add_argument("--per-kind", type=int, default=0,
                        help="With --dump: also write the N largest texts of every kind (files kind-NN-...)")
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
    report = measure(args.home.expanduser(), start, end, args.top, per_kind=args.per_kind,
                     progress=lambda msg: print(msg, file=sys.stderr, flush=True))
    largest = report.pop("topRecords")
    per_kind = report.pop("perKindRecords")
    if args.dump:
        names = dump_texts(largest, args.dump) + dump_texts(per_kind, args.dump, prefix="kind-")
        print(f"wrote {len(names)} texts to {args.dump}", file=sys.stderr, flush=True)
    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
