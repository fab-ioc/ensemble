"""Links to a chat balloon, pasted into another chat.

Every balloon offers a link, ``<origin>/session?room=<roomId>&msg=<messageId>``.
A message holding such links reaches the agent with each referenced message
written out under it, so the agent reads what was pointed at without a tool
call. The stored chat message keeps the sender's words; only what is typed into
the agent's terminal (or read through chat_read) carries the expansion.

An image pasted into the chat box travels as one line per image at the very
end of the message, ``[image] <absolute path on the hub>`` (:func:`image_line`),
so Claude (Read) or Codex (view_image) can open it; the hub adds the lines, the
room and the transcript keep them, and the balloon shows them as thumbnails.
A message written as numbered points (``## Points (N)``, one ``**N.**`` item
each, as the chat's editor writes it; ``## Review comments (N)`` is the same
shape) keeps each image with its point: the page writes ``[image] <name>``
inside the item, and :func:`with_images` puts the stored path there. The
``[ref …]`` blocks of such a message go inside the item whose link they
expand, above that item's images and its ``[point Pn]`` line.

A task named by its number (``#18``, ``@codex@18``, ``#ED-18``) gets one line
under the message instead: its title, state, agents, branch and last report.

Pure functions: the hub passes ``lookup(room_id, msg_id)``, which returns
``{who, taskTitle, taskNo, ts, text, id, where}`` for a message it knows, else
None, and ``task_lookup(key, no)``, which returns the task a number names (see
:func:`task_line`), else None.
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlsplit

import task_numbers

QUOTE_MAX = 4000
REPORT_LINE_MAX = 200

# Any host: a link copied on another computer names the hub by the address it
# was read at. It stops at whatever Markdown or a sentence puts around it.
_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`*|\\]+", re.I)
_TAIL = re.compile(r"[.,;:!?_]+$")
# The last block expand_message_refs appended to a text, in exactly the shape
# it writes (what a transcript records of a message sent with links). A block
# counts only when its link is also in the words before it, so a person's own
# "[ref …]" lines stay.
_BLOCK = re.compile(
    r"\n\n\[ref (https?://[^\s\]]+)\] "
    r"(?:not found: no message \S+ in \S+ on this hub"
    r"|from [^\n]* in \"[^\n]*\" at (?:\d{4}-\d\d-\d\d \d\d:\d\d|an unknown time):(?:\n>(?: [^\n]*)?)+)"
    r"\s*$")
# The one line written under a task named by its number, in exactly its shape.
_TASK_BLOCK = re.compile(r"\n\n\[ref ((?:@[A-Za-z][\w-]*@|#)(?:[A-Za-z][A-Za-z0-9]*-)?\d{1,6})\] task [^\n]*\s*$")


IMAGE_PREFIX = "[image] "
# A message of numbered items (points.py reads the same head): its head line,
# and where each item starts, outside code fences.
ITEMS_HEAD = re.compile(r"^## (?:Review comments|Points) \(\d+\)")
_ITEM_START = re.compile(r"^\*\*\d+\.\*\*")
_FENCE = re.compile(r"^\s*(```|~~~)")
# What ends an item and stays at its end: its images and its point line.
_TAIL_LINE = re.compile(r"^(?:\[image\] \S.*|\[point P\d{1,5}[a-z]?\][ \t]*)$")


def fenced_lines(lines: list[str]) -> set[int]:
    """The indexes of ``lines`` inside a code fence, its fence lines included."""
    out, fence = set(), None
    for i, ln in enumerate(lines):
        f = _FENCE.match(ln)
        if f:
            out.add(i)
            fence = None if fence == f.group(1) else (fence or f.group(1))
        elif fence is not None:
            out.add(i)
    return out


def split_items(text: str) -> tuple[str, list[str]] | None:
    """A numbered-items message as ``(its head, each item)``, or None when it
    is not one. Items start at ``**N.**`` lines outside code fences; the head
    is everything above the first."""
    if not ITEMS_HEAD.match(text or ""):
        return None
    lines = text.split("\n")
    starts, fence = [], None
    for i, ln in enumerate(lines):
        f = _FENCE.match(ln)
        if f:
            fence = None if fence == f.group(1) else (fence or f.group(1))
            continue
        if fence is None and _ITEM_START.match(ln):
            starts.append(i)
    if not starts:
        return None
    head = "\n".join(lines[:starts[0]]).rstrip()
    items = ["\n".join(lines[a:b]).strip() for a, b in zip(starts, starts[1:] + [len(lines)])]
    return head, items


def _join_items(head: str, items) -> str:
    return "\n\n".join(x for x in [head, *items] if x)


def _split_tail(item: str) -> tuple[str, str]:
    """An item as ``(its body, its tail)``: the tail is the run of ``[image]``
    and ``[point Pn]`` lines (and blank lines) it ends with."""
    lines = item.rstrip().split("\n")
    n = len(lines)
    while n and (not lines[n - 1].strip() or _TAIL_LINE.match(lines[n - 1])):
        n -= 1
    if n == len(lines):
        return item.rstrip(), ""
    return "\n".join(lines[:n]).rstrip(), "\n".join(lines[n:]).strip()


def _with_tail(body: str, tail: str) -> str:
    return body.rstrip() + ("\n\n" + tail if tail else "")


def _per_item(text: str, fn) -> str:
    """``fn`` applied to the head and to each item's body of a numbered-items
    message, its tails kept where they are; the text itself when it is not
    one or nothing changed."""
    split = split_items(text)
    if not split:
        return text
    head, items = split
    changed = False
    out = []
    for it in items:
        body, tail = _split_tail(it)
        done = fn(body)
        changed = changed or done != body
        out.append(_with_tail(done, tail))
    # The head has a tail too when its words are a point of their own.
    hb, ht = _split_tail(head) if head else ("", "")
    new_head = _with_tail(fn(hb), ht) if hb else head
    changed = changed or new_head != head
    return _join_items(new_head, out) if changed else text


def image_line(path: str) -> str:
    """The line an attached image adds under a message: ``[image] <path>``."""
    return IMAGE_PREFIX + str(path)


def with_images(text: str, paths, names=None) -> str:
    """``text`` with one ``[image]`` line per path (none: unchanged). A path
    whose image the text already names on a ``[image] <name>`` line of its
    own (``names[i]``, else the file's name: how a point's images are written
    inside it) takes that line's place; the rest end the text."""
    names = list(names or [])
    lines = (text or "").replace("\r\n", "\n").split("\n")
    rest, placed = [], False
    for i, p in enumerate(paths or []):
        p = str(p or "").strip()
        if not p:
            continue
        want = {IMAGE_PREFIX + n for n in (names[i] if i < len(names) else "", _basename(p)) if n}
        at = next((k for k, ln in enumerate(lines) if ln.rstrip() in want), None)
        if at is None:
            rest.append(image_line(p))
        else:
            lines[at] = image_line(p)
            placed = True
    body = ("\n".join(lines) if placed else (text or "")).rstrip()
    if not rest:
        return body if placed else (text or "")
    return (body + "\n\n" if body else "") + "\n".join(rest)


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def all_images(text: str) -> list[str]:
    """Every ``[image] <path>`` line's path, wherever it is in the text (a
    point's images sit inside their point), in order."""
    return [ln[len(IMAGE_PREFIX):].strip() for ln in (text or "").replace("\r\n", "\n").split("\n")
            if ln.startswith(IMAGE_PREFIX) and ln[len(IMAGE_PREFIX):].strip()]


def split_images(text: str) -> tuple[str, list[str]]:
    """``(words, paths)``: a text without the ``[image]`` lines it ends with,
    and their paths in order."""
    lines = (text or "").replace("\r\n", "\n").rstrip().split("\n")
    n = len(lines)
    while n and lines[n - 1].startswith(IMAGE_PREFIX) and lines[n - 1][len(IMAGE_PREFIX):].strip():
        n -= 1
    if n == len(lines):
        return text or "", []
    return "\n".join(lines[:n]).rstrip(), [ln[len(IMAGE_PREFIX):].strip() for ln in lines[n:]]


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
    """``text`` without the reference blocks the hub appended to it (its
    ``[image]`` lines kept; a numbered-items message loses each item's)."""
    if split_items(text or ""):
        return _per_item(text, strip_message_refs)
    words, images = split_images(text)
    if images:
        out = strip_message_refs(words)
        return text if out == words else with_images(out, images)
    text = text or ""
    while True:
        m = _TASK_BLOCK.search(text) or _BLOCK.search(text)
        if not m or m.group(1) not in text[:m.start()]:
            return text
        text = text[:m.start()]


def _when(ts) -> str:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        ts = 0.0
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts > 0 else "an unknown time"


def _flat(s) -> str:
    return " ".join(str(s or "").split())


def task_line(token: str, task: dict) -> str:
    """The line written under a message for a task it names:

        [ref #18] task "<title>" — <status>, <column>; <agent (role)>, …;
        branch <branch>; last report (<kind>): <its first line>

    ``task`` is ``{label, title, status, workflowName, agents: [{identity,
    role}], branch, report: {kind, text}}``; what it lacks is left out."""
    title = _flat(task.get("title")).replace('"', "'")
    head = f"[ref {token}] task " + (f"{task['label']} " if task.get("label") and task["label"] != token else "") + f"\"{title}\""
    state = ", ".join(x for x in (_flat(task.get("status")), _flat(task.get("workflowName"))) if x)
    bits = [state] if state else []
    agents = ", ".join(_flat(a.get("identity")) + (f" ({_flat(a['role']).split(':')[0].strip()})" if a.get("role") else "")
                       for a in task.get("agents") or [] if a.get("identity"))
    if agents:
        bits.append(agents)
    if task.get("branch"):
        bits.append(f"branch {_flat(task['branch'])}")
    rep = task.get("report") or {}
    first = next((ln.strip() for ln in str(rep.get("text") or "").splitlines() if ln.strip()), "")
    if first:
        first = _flat(first)
        cut = first[:REPORT_LINE_MAX] + ("…" if len(first) > REPORT_LINE_MAX else "")
        bits.append(f"last report ({_flat(rep.get('kind')) or 'report'}): {cut}")
    return head + (" — " + "; ".join(bits) if bits else "")


def expand_message_refs(text: str, lookup, task_lookup=None) -> str:
    """``text`` followed by one block per balloon link in it:

        [ref <url>] from <who> in "<#18 task title, or PO>" at <YYYY-MM-DD HH:MM>:
        > <referenced text, every line quoted>

    then one line per task it names by number (see :func:`task_line`), when
    ``task_lookup`` is given. A quoted text is cut at QUOTE_MAX characters,
    with a line saying how much more there is and where. A link the hub cannot
    resolve gets a one-line block saying so; a number that names no task gets
    nothing. A text with no references comes back unchanged. ``[image]``
    lines stay last, under the blocks. In a numbered-items message each
    item's links are written out inside that item, above its images and its
    ``[point Pn]`` line."""
    if split_items(text or ""):
        return _per_item(text, lambda t: expand_message_refs(t, lookup, task_lookup))
    words, images = split_images(text)
    if images:
        out = expand_message_refs(words, lookup, task_lookup)
        return text if out == words else with_images(out, images)
    refs = find_message_refs(text)
    trefs = task_numbers.find_text_refs(text) if task_lookup else []
    blocks = []
    for ref in trefs:
        try:
            task = task_lookup(ref["key"], ref["no"])
        except Exception:       # noqa: BLE001 — a bad reference never stops a message
            task = None
        if task:
            blocks.append(task_line(ref["token"], task))
    if not refs and not blocks:
        return text
    tasks, blocks = blocks, []
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
        where = ref.get("taskTitle") or room
        if ref.get("taskNo") and not ref.get("isPo"):
            where = f"#{ref['taskNo']} {where}"
        head = (f"[ref {url}] from {ref.get('who') or '?'} in "
                f"\"{where}\" at {_when(ref.get('ts'))}:")
        blocks.append(head + "\n" + "\n".join(lines))
    return text.rstrip() + "\n\n" + "\n\n".join(blocks + tasks)
