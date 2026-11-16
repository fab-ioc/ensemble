"""The hub's own addresses in text, as names (P358).

The person never sees an Ensemble address: where a list or a notification
shows a message's words as plain text (the Inbox, a lock screen), a link to a
card, a message, a task or a file on this hub reads as that thing's name: a
card as its question, clipped; a file as its file name; a task's chat as its
number and title. Another website's address stays as written. The stored text
is never changed.

The chat pages do the same in the browser (session.html's hubUrlOf,
hubUrlName and refChipHtml), where the name is also a link.
"""
from __future__ import annotations

import functools
import ipaddress
import re
import socket
from urllib.parse import parse_qs, unquote, urlsplit

_d = None       # the dashboard module, for the room and card lookups

QUESTION_MAX = 60
TITLE_MAX = 48

_ROOM_ID = re.compile(r"^room-[0-9a-f]{8}$")
# A bare http(s) address, as the page's linkify reads one (no spaces, quotes,
# brackets or backticks), and the punctuation that ends a sentence after it.
_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`*|\\]+", re.I)
_TAIL = re.compile(r"[.,;:!?_]+$")
# [words](address) and <address>; `address` alone in a code span.
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\(\s*<?(https?://[^\s<>()]+)>?\s*\)", re.I)
_ANGLE = re.compile(r"<(https?://[^\s<>]+)>", re.I)
_CODE = re.compile(r"`\s*(https?://[^\s`]+)\s*`", re.I)


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


@functools.lru_cache(maxsize=1)
def _own_names() -> frozenset[str]:
    try:
        names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    except OSError:
        names = set()
    return frozenset(n for n in names if n)


def hub_host(host: str) -> bool:
    """An address the hub answers on: this machine's loopback, its own
    names, a tailnet name (*.ts.net) or address (100.64.0.0/10)."""
    h = (host or "").strip("[]").rstrip(".").lower()
    if not h:
        return False
    if h == "localhost" or h.endswith(".ts.net") or h in _own_names():
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address((host or "").strip("[]")).is_loopback
    except ValueError:
        return False


def target(url: str) -> dict | None:
    """What a hub address opens: {"card": (room, msg, n)}, {"msg": (room,
    msg)}, {"task": room}, {"thread": room}, {"file": path} or {"page": path};
    None for another website's address."""
    try:
        u = urlsplit(url)
        host = u.hostname or ""
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not hub_host(host):
        return None
    q = {k: v[0].strip() for k, v in parse_qs(u.query).items() if v}
    room = q.get("room") or q.get("id") or ""
    if u.path == "/session" and _ROOM_ID.match(room):
        if "thread" in q:
            return {"thread": room}
        msg = q.get("msg", "")
        if msg:
            m = re.fullmatch(r"ask:(\d{1,4})", q.get("part", ""))
            return {"card": (room, msg, int(m.group(1)))} if m else {"msg": (room, msg)}
        return {"task": room}
    if u.path == "/" and _ROOM_ID.match(q.get("task", "")):
        return {"task": q["task"]}
    if u.path == "/fileview" and q.get("path"):
        return {"file": q["path"]}
    # Any other page is the hub's only on its own port (as the chat page's
    # hubUrlOf reads it): a dev server on localhost:5173 is another website.
    try:
        port = u.port
    except ValueError:
        return None
    hub_port = getattr(_d, "HUB_PORT", 0) or 8765
    loop = host.strip("[]").lower() == "localhost" or _loopback(host)
    if port != hub_port and (loop or port is not None):
        return None
    return {"page": u.path}


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _question(room: str, msg: str, n: int) -> str:
    try:
        ref = _d.resolve_message_ref(room, msg) if _d else None
    except Exception:       # noqa: BLE001 — the generic words then
        ref = None
    qs = (ref or {}).get("questions") or []
    return _clip(qs[n], QUESTION_MAX) if 0 <= n < len(qs) and qs[n] else ""


def _task(room: str) -> str:
    try:
        r = _d.chatroom.get_room(room, copy_room=False) if _d else None
    except Exception:       # noqa: BLE001
        r = None
    if not r:
        return ""
    title = _clip(r.get("title") or "", TITLE_MAX)
    if r.get("no"):
        return f"#{r['no']} {title}".strip()
    return title


def name(url: str) -> str | None:
    """The words a hub address shows; None for another website's."""
    t = target(url)
    if t is None:
        return None
    if "card" in t:
        return _question(*t["card"]) or "the question card"
    if "msg" in t:
        return "the message"
    if "task" in t:
        return _task(t["task"]) or "the task"
    if "thread" in t:
        return "the thread"
    if "file" in t:
        p = unquote(t["file"]).rstrip("\\/")
        return re.split(r"[\\/]", p)[-1] or p
    return "Ensemble"


def names(text: str) -> str:
    """``text`` with every hub address as its name: a [words](address) link
    as its words, an address in <…> or a code span or bare as :func:`name`.
    Any other address stays as written."""
    s = str(text or "")
    if "://" not in s:
        return s

    def md(m):
        return m.group(1) if target(m.group(2)) is not None and m.group(1).strip() else m.group(0)

    def whole(m):
        nm = name(m.group(1))
        return nm if nm is not None else m.group(0)

    def bare(m):
        url = m.group(0)
        tail = _TAIL.search(url)
        cut = url[:tail.start()] if tail else url
        nm = name(cut)
        return nm + url[len(cut):] if nm is not None else url

    s = _MD_LINK.sub(md, s)
    s = _ANGLE.sub(whole, s)
    s = _CODE.sub(whole, s)
    return _URL.sub(bare, s)
