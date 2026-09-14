"""Links to a chat balloon, pasted into another chat.

Every balloon offers a link, ``<origin>/session?room=<roomId>&msg=<messageId>``.
A message holding such links reaches the agent with each referenced message
written out under it, so the agent reads what was pointed at without a tool
call. The stored chat message keeps the sender's words; only what is typed into
the agent's terminal (or read through chat_read) carries the expansion.

Pure functions: the hub passes ``lookup(room_id, msg_id)``, which returns
``{who, taskTitle, ts, text, id, where}`` for a message it knows, else None.
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

QUOTE_MAX = 4000

# Any host: a link copied on another computer names the hub by the address it
# was read at. It stops at whatever Markdown or a sentence puts around it.
_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`*|\\]+", re.I)
_TAIL = re.compile(r"[.,;:!?_]+$")
# The blocks expand_message_refs appends, at the end of a text: what a
# transcript records of a message sent with links.
_BLOCKS = re.compile(r"(?:\n\n\[ref https?://\S+\] [^\n]*(?:\n>[^\n]*)*)+\s*$", re.I)


def find_message_refs(text: str) -> list[tuple[str, str, str]]:
    """The balloon links in ``text``, in order and each once:
    ``[(url, room_id, msg_id)]``."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _URL.finditer(text or ""):
        url = _TAIL.sub("", m.group(0))
        try:
            parts = urlsplit(url)
            q = parse_qs(parts.query)
        except ValueError:
            continue
        if parts.path != "/session":
            continue
        room = (q.get("room") or [""])[0].strip()
        msg = (q.get("msg") or [""])[0].strip()
        if not room or not msg or url in seen:
            continue
        seen.add(url)
        out.append((url, room, msg))
    return out


def strip_message_refs(text: str) -> str:
    """``text`` without the reference blocks the hub appended to it."""
    return _BLOCKS.sub("", text or "")


def _when(ts) -> str:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0.0
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts > 0 else "an unknown time"


def expand_message_refs(text: str, lookup) -> str:
    """``text`` followed by one block per balloon link in it:

        [ref <url>] from <who> in "<task title or PO>" at <YYYY-MM-DD HH:MM>:
        > <referenced text, every line quoted>

    A quoted text is cut at QUOTE_MAX characters, with a line saying how much
    more there is and where. A link the hub cannot resolve gets a one-line
    block saying so. A text with no links comes back unchanged."""
    refs = find_message_refs(text)
    if not refs:
        return text
    blocks = []
    for url, room, msg in refs:
        try:
            ref = lookup(room, msg)
        except Exception:       # noqa: BLE001 — a bad link never stops a message
            ref = None
        if not ref:
            blocks.append(f"[ref {url}] not found: no message {msg} in {room} on this hub")
            continue
        body = strip_message_refs(str(ref.get("text") or "")).replace("\r\n", "\n")
        lines = ["> " + line for line in body[:QUOTE_MAX].split("\n")]
        if len(body) > QUOTE_MAX:
            where = ref.get("where") or f"~/.ensemble/rooms/{room}.json"
            lines.append(f"> … ({len(body) - QUOTE_MAX} more characters; the full message "
                         f"is in {where}, id {ref.get('id') or msg})")
        head = (f"[ref {url}] from {ref.get('who') or '?'} in "
                f"\"{ref.get('taskTitle') or room}\" at {_when(ref.get('ts'))}:")
        blocks.append(head + "\n" + "\n".join(lines))
    return text.rstrip() + "\n\n" + "\n\n".join(blocks)
