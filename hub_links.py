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
# brackets or backticks, but a bracketed IPv6 host: http://[::1]:8765/...),
# and the punctuation that ends a sentence after it.
_URL_CH = r"[^\s<>()\[\]{}\"'`*|\\]"
_URL = re.compile(rf"https?://(?:\[[0-9a-f:.]+\]{_URL_CH}*|{_URL_CH}+)", re.I)
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
    return frozenset(n.rstrip(".") for n in names if n)


# The addresses the hub has been reached at, as (host, port): the Host a
# browser (or tailscale serve's X-Forwarded-Host) sent with a page the hub
# served, and the tailnet address it listens on (dashboard.py's seen_origin
# and _announce_remote).
_SEEN: set[tuple[str, int]] = set()


def _norm(host: str) -> str:
    return (host or "").strip().strip("[]").rstrip(".").lower()


def _port_of(scheme: str, port) -> int:
    return int(port) if port else (443 if (scheme or "").lower() == "https" else 80)


def hub_port() -> int:
    return int(getattr(_d, "HUB_PORT", 0) or 8765)


def hub_host(host: str) -> bool:
    """A host that can be this hub's: this machine's loopback, its own
    names, a tailnet name (*.ts.net) or address (100.64.0.0/10). Whether an
    address is the hub's also takes its port: :func:`hub_origin`."""
    h = _norm(host)
    if not h:
        return False
    if h == "localhost" or h.endswith(".ts.net") or h in _own_names():
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def _loopback(h: str) -> bool:
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def seen(host: str, scheme: str = "http") -> None:
    """Note an address the hub was reached at (a Host header, ``name[:port]``)."""
    raw = (host or "").strip()
    if not raw:
        return
    try:
        u = urlsplit(f"{scheme}://{raw}")
        h, port = _norm(u.hostname or ""), u.port
    except ValueError:
        return
    if h and hub_host(h) and len(_SEEN) < 64:
        _SEEN.add((h, _port_of(scheme, port)))


def hub_origin(host: str, port: int | None, scheme: str = "http") -> bool:
    """Whether scheme://host:port is this hub: its own port on this machine's
    loopback or names, an address the hub was reached at (:func:`seen`), or
    this machine's tailnet name (``<hostname>.<tailnet>.ts.net``) over https
    (tailscale serve) or on the hub's port; a tailnet address on the hub's
    port. A dev server on localhost:5173 or another machine's tailnet name
    is another website."""
    h = _norm(host)
    if not hub_host(h):
        return False
    p, hp = _port_of(scheme, port), hub_port()
    if (h, p) in _SEEN:
        return True
    if _loopback(h) or h in _own_names():
        return p == hp
    if h.endswith(".ts.net"):
        # tailscale serve may give the hub any https port (443, 9443, ...)
        short = {n.split(".")[0] for n in _own_names()}
        return h.split(".")[0] in short and (p == hp or scheme.lower() == "https")
    # a tailnet address: the hub listening there (--bind tailscale)
    return p == hp


def origins() -> dict:
    """What the chat page needs to read the hub's addresses as hub_origin
    does (session.html's hubOrigin): the addresses it was reached at, as
    ``host:port``, and this machine's own names."""
    return {"seen": sorted(f"[{h}]:{p}" if ":" in h else f"{h}:{p}" for h, p in _SEEN),
            "names": sorted(_own_names())}


def target(url: str) -> dict | None:
    """What a hub address opens: {"card": (room, msg, n)}, {"msg": (room,
    msg)}, {"task": room}, {"thread": room}, {"file": path} or {"page": path};
    None for another website's address."""
    try:
        u = urlsplit(url)
        host, port = u.hostname or "", u.port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not hub_origin(host, port, u.scheme):
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
