"""Tool hook for hub-launched Claude task agents: big text files are read in
parts, and rtk's "no hook installed" line is kept out of shell results.

Every byte of a tool result is read again on every later model call of the
same conversation (task #78 measured where those bytes go). Two of them are
avoidable and this script, registered by the hub in a task agent's
``--settings`` file (``dashboard._rtk_claude_settings``), removes them:

``PreToolUse`` for ``Read``
    A text file read whole (no ``offset``, no ``limit``) that is over
    ``--bytes`` comes back as its first ``--lines`` lines: the call's input is
    replaced with ``hookSpecificOutput.updatedInput``, the same input plus
    ``limit``. A read with an ``offset`` or ``limit`` of the agent's own, an
    image, a PDF or a notebook (by extension), a missing file: untouched.

``PostToolUse`` for ``Read``
    When such a cut read comes back, the agent is told so in one line of
    ``hookSpecificOutput.additionalContext``: which lines it got, how many the
    file has, and how to read on (``offset``). Claude Code itself adds no such
    note to a limited read, and its ``systemMessage`` is shown to the person,
    not to the model (checked on 2.1.280: the additional context arrives as a
    system reminder right after the result; ``updatedToolOutput`` would have
    to rebuild the Read result's own shape and would number the note as a
    line of the file).

``PostToolUse`` for ``Bash``
    The line ``[rtk] /!\\ No hook installed — run `rtk init -g` ...`` (rtk
    0.49.0 prints it on every command whose user-level hook it cannot see,
    and the hub keeps rtk launch-scoped on purpose) is dropped from the
    result's ``stdout``/``stderr`` with ``hookSpecificOutput.updatedToolOutput``,
    only when it is there.

Usage (set by the hub)::

    python task_tool_hook.py [--bytes 16384] [--lines 400]

It must never stand between the agent and its tool: any error, any input it
does not understand, exits 0 with nothing printed, which Claude Code reads as
"carry on as you were". Standard library only; every file is opened as
UTF-8.
"""
from __future__ import annotations

import json
import os
import re
import sys

CAP_BYTES_DEFAULT = 16 * 1024
CAP_LINES_DEFAULT = 400

# Read handles these by content, not as text: the cap is for text files only
# (cap 3 of the report, images and PDFs, is a different change).
SKIP_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico",
    ".heic", ".heif", ".avif", ".pdf", ".ipynb",
})

# rtk's once-a-day warning, printed on every run here (its marker never
# refreshes on Windows); either wording, with or without the em dash intact.
NAG_RE = re.compile(r"^\[rtk\] /!\\ (?:No hook installed|Hook outdated)\b.*$", re.M)


def _given(tool_input: dict, key: str) -> bool:
    """Whether the agent set ``key`` itself (a null counts as not set)."""
    return tool_input.get(key) is not None


def _text_file_over(path: str, cwd: str, cap_bytes: int) -> int:
    """The size of ``path`` when it is a text file (by extension) over the
    cap; 0 otherwise (a missing path included)."""
    if not isinstance(path, str) or not path.strip() or cap_bytes <= 0:
        return 0
    if os.path.splitext(path)[1].lower() in SKIP_SUFFIXES:
        return 0
    full = path if os.path.isabs(path) else os.path.join(cwd or "", path)
    try:
        if not os.path.isfile(full):
            return 0
        size = os.path.getsize(full)
    except OSError:
        return 0
    return size if size > cap_bytes else 0


def read_cap(tool_input: dict, cwd: str, cap_bytes: int, cap_lines: int) -> dict | None:
    """The Read input with ``limit`` added, or None when the read stays as it
    is: the agent chose a window, the file is not a big text file, or the cap
    is off."""
    if not isinstance(tool_input, dict) or cap_lines <= 0:
        return None
    if _given(tool_input, "limit") or _given(tool_input, "offset"):
        return None
    if not _text_file_over(tool_input.get("file_path"), cwd, cap_bytes):
        return None
    return {**tool_input, "limit": cap_lines}


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB" if n >= 10 * 1024 else f"{n / 1024:.1f} KB"


def read_note(tool_input: dict, tool_response, cwd: str, cap_bytes: int,
              cap_lines: int) -> str | None:
    """One line for the agent after a read this hook cut: what it got and how
    to read on. None for a read that was not cut, or that got the whole file
    anyway."""
    if not isinstance(tool_input, dict) or not isinstance(tool_response, dict):
        return None
    if tool_input.get("limit") != cap_lines or _given(tool_input, "offset"):
        return None
    info = tool_response.get("file")
    if not isinstance(info, dict):
        return None
    try:
        total = int(info.get("totalLines"))
        got = int(info.get("numLines"))
        start = int(info.get("startLine") or 1)
    except (TypeError, ValueError):
        return None
    last = start + got - 1
    if got <= 0 or last >= total:
        return None
    size = _text_file_over(tool_input.get("file_path"), cwd, cap_bytes)
    if not size:
        return None
    name = os.path.basename(str(tool_input.get("file_path") or ""))
    return (f"[hub] {name}: lines {start}-{last} of {total} shown; the file is "
            f"{_kb(size)}, over the {_kb(cap_bytes)} a task agent reads in one go. "
            f"For the rest, Read it again with offset={last + 1} (and limit), "
            f"or Grep for what you need.")


def strip_nag(tool_response) -> dict | None:
    """The Bash result without rtk's warning line, or None when it has none
    (or is not a Bash result)."""
    if not isinstance(tool_response, dict):
        return None
    out = None
    for key in ("stdout", "stderr"):
        text = tool_response.get(key)
        if not isinstance(text, str) or not NAG_RE.search(text):
            continue
        cleaned = "\n".join(line for line in text.split("\n") if not NAG_RE.match(line))
        if out is None:
            out = dict(tool_response)
        out[key] = cleaned
    return out


def handle(data, cap_bytes: int = CAP_BYTES_DEFAULT,
           cap_lines: int = CAP_LINES_DEFAULT) -> dict | None:
    """The hook's answer to one event, or None for nothing to say."""
    if not isinstance(data, dict):
        return None
    event = data.get("hook_event_name")
    tool = data.get("tool_name")
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
    if event == "PreToolUse" and tool == "Read":
        updated = read_cap(data.get("tool_input"), cwd, cap_bytes, cap_lines)
        if updated is None:
            return None
        # The shape rtk's own rewrite uses on this CLI: no permission decision,
        # so the read goes through the permission flow it would have anyway.
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecisionReason": f"big file: first {cap_lines} lines (hub read cap)",
            "updatedInput": updated}}
    if event == "PostToolUse" and tool == "Read":
        note = read_note(data.get("tool_input"), data.get("tool_response"), cwd,
                         cap_bytes, cap_lines)
        if note is None:
            return None
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "additionalContext": note}}
    if event == "PostToolUse" and tool == "Bash":
        cleaned = strip_nag(data.get("tool_response"))
        if cleaned is None:
            return None
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                       "updatedToolOutput": cleaned}}
    return None


def _int_arg(argv: list[str], flag: str, default: int) -> int:
    try:
        i = argv.index(flag)
        return int(argv[i + 1])
    except (ValueError, IndexError):
        return default


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        cap_bytes = _int_arg(argv, "--bytes", CAP_BYTES_DEFAULT)
        cap_lines = _int_arg(argv, "--lines", CAP_LINES_DEFAULT)
        raw = sys.stdin.buffer.read()
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        out = handle(data, cap_bytes, cap_lines)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            sys.stdout.flush()
    except BaseException:                                    # noqa: BLE001 — never in the tool's way
        pass
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
