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


# The addresses the hub is known to answer at, as (host, port): one a reader
# opened a page at (dashboard.py's _note_origin: the Host of a direct request
# when it matches the socket it came in on, or the X-Forwarded-Host of a proxy
# on this machine when it names this machine), the address it listens on
# (_announce_remote), and what tailscale itself says serves the hub
# (learn_tailscale). Never a host a request merely claims.
_SEEN: set[tuple[str, int]] = set()
# This machine's tailnet names (MagicDNS), from `tailscale status`.
_TS_NAMES: set[str] = set()


def _norm(host: str) -> str:
    return (host or "").strip().strip("[]").rstrip(".").lower()


def _port_of(scheme: str, port) -> int:
    return int(port) if port else (443 if (scheme or "").lower() == "https" else 80)


def hub_port() -> int:
    return int(getattr(_d, "HUB_PORT", 0) or 8765)


def _loopback(h: str) -> bool:
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _names() -> set[str]:
    return set(_own_names()) | _TS_NAMES


def own_host(host: str) -> bool:
    """A name or address of this machine: loopback, its own names, the tailnet
    name tailscale gave it (exactly: the same short name in another tailnet is
    another machine). Not a LAN or tailnet address by itself."""
    h = _norm(host)
    return bool(h) and (_loopback(h) or h in _names())


def _split(host: str, scheme: str) -> tuple[str, int] | None:
    raw = (host or "").strip()
    if not raw:
        return None
    try:
        u = urlsplit(f"{scheme}://{raw}")
        h, port = _norm(u.hostname or ""), u.port
    except ValueError:
        return None
    return (h, _port_of(scheme, port)) if h else None


def _add(h: str, p: int) -> None:
    if h not in ("0.0.0.0", "::") and len(_SEEN) < 64:
        _SEEN.add((h, p))


def seen(host: str, scheme: str = "http", local: tuple[str, int] | None = None) -> None:
    """Note an address a page of the hub was opened at (a Host header,
    ``name[:port]``). local: the (address, port) the request's socket came in
    on, for a direct request: the Host counts when its port is that port and
    it names this machine or is that very address (a LAN or tailnet bind).
    Without it (a proxy on this machine) the Host must name this machine."""
    hp = _split(host, scheme)
    if not hp:
        return
    h, p = hp
    if local is not None:
        ip, port = _norm(str(local[0])), int(local[1])
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        if p == port and (own_host(h) or h == ip):
            _add(h, p)
    elif own_host(h):
        _add(h, p)


def listens(host: str, scheme: str = "http") -> None:
    """An address the hub itself listens on or is served at (its bind,
    tailscale serve): known, not claimed."""
    hp = _split(host, scheme)
    if hp:
        _add(*hp)


def learn_tailscale(status: dict | None, serve: dict | None) -> None:
    """What `tailscale status --json` and `tailscale serve status --json` say:
    this machine's tailnet name, and each https address tailscale serve proxies
    to the hub's port on this machine."""
    me = (status or {}).get("Self") or {}
    name = _norm(me.get("DNSName") or "")
    if name:
        _TS_NAMES.add(name)
    for where, cfg in ((serve or {}).get("Web") or {}).items():
        for h in ((cfg or {}).get("Handlers") or {}).values():
            proxy = str((h or {}).get("Proxy") or "")
            if not proxy:
                continue
            try:
                u = urlsplit(proxy if "://" in proxy else "http://" + proxy)
                to = (u.port, _loopback(_norm(u.hostname or "")))
            except ValueError:
                continue
            if to == (hub_port(), True):
                listens(where, "https")
                break


def hub_origin(host: str, port: int | None, scheme: str = "http") -> bool:
    """Whether scheme://host:port is this hub: an address it is known to answer
    at (seen, listens, learn_tailscale), or the hub's own port on this
    machine's loopback, names or tailnet name. A dev server on localhost:5173,
    another service on this machine's tailnet name, another machine: another
    website."""
    h = _norm(host)
    if not h:
        return False
    p = _port_of(scheme, port)
    if (h, p) in _SEEN:
        return True
    return p == hub_port() and own_host(h)


def origins() -> dict:
    """What the chat page needs to read the hub's addresses as hub_origin
    does (session.html's hubOrigin): the addresses it was reached at, as
    ``host:port``, and this machine's own names."""
    return {"seen": sorted(f"[{h}]:{p}" if ":" in h else f"{h}:{p}" for h, p in _SEEN),
            "names": sorted(_names())}


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
