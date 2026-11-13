"""Notifications on the person's devices (#359): a new Inbox card for them, or
a PO's reply to them, pops up on every device that turned "Notifications on
this device" on, even when no Ensemble page is open. Standard Web Push
(:mod:`webpush`): the hub sends to the browser vendor's push service, which
wakes the device's service worker (``static/sw.js``).

**What notifies** (nothing else does: no digest, no task report to a PO, no
hub notice, no agent-to-agent traffic):

* a new row of the Inbox (:mod:`inbox`), of any kind: a question (``answer``),
  answers to check or a finished report (``check``), a task waiting on the
  person or one that could not start (``waiting``). Title "<Project> · <kind>",
  body the row's line; a tap opens the row's own link, as the Inbox does. The
  grouped "answers to check" row notifies once per answer in it;
* a PO's reply to the person: an assistant turn of the PO's conversation that
  answers a line the person typed (``answers.kind == "human"``, as
  ``classify_turns`` reads it), not one that answers a report or a hub line.
  Title "<Project> PO", body its first line; a tap opens the PO chat at it.
  A reply that is also an Inbox card (an answer to a point) notifies once, as
  the PO's message.

**Once.** An Inbox row is keyed by its id and its ``since`` (a state that is
new again is a new key); keys are kept on disk while listed and
:data:`KEEP_S` after, so a restart re-sends nothing. The very first run only
takes note of what is listed. A row first seen long after it began (a chat's
cards read late after a start) is not news: :func:`_fresh`.

**Bursts.** The first item of a project shows at once. More from the same
project within :data:`BURST_S` of that first one replace it ("3 new in
Motors", the same ``tag``); a tap opens the dashboard with the Inbox filtered
to that project (``/?inbox=<project id>``). After the window the next item
starts a new notification.

**Not where the person is looking.** A page that is visible and focused tells
the hub every :data:`FRONT_EVERY_S` (``POST /api/notify/seen``); that device
is skipped until :data:`FRONT_S` after its last word. Known limit: a device
locked within that time of being in front may miss that one notification.

**Off the request threads.** The watcher thread finds what to send; a small
pool sends it, :data:`SEND_TIMEOUT_S` per push and no retry. A push service
answering 404 or 410 removes that device.

**Storage** under ``~/.ensemble/notify/``: ``vapid.json`` (the hub's signing
key, mode 600, never in a page or a log), ``devices.json`` (each subscribed
browser's push address and keys, mode 600), ``state.json`` (the keys above).
A payload carries a title, a short line and a path on this hub: never a file
path or a token.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

try:
    import webpush
    _IMPORT_ERROR = ""
except Exception as e:          # noqa: BLE001 — the hub starts without notifications
    webpush = None
    _IMPORT_ERROR = f"the Web Push module did not load ({e!r}); is the cryptography package installed?"

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


TICK_S = 10.0                   # how often the Inbox and the PO chats are read
BURST_S = 30.0                  # more from one project within this: one notification
FRONT_S = 45.0                  # a device that said "in front" is skipped this long
FRONT_EVERY_S = 15.0            # how often a page in front says so (the page's own timer)
FRESH_S = 600.0                 # a row first seen this long after it began is not news
MAX_AGE_S = 6 * 3600.0          # nothing older than this notifies
KEEP_S = 4 * 86400.0            # keys remembered this long after they were last listed
PO_REREAD_S = 30.0              # a PO transcript still being written: read at most this often
SEND_TIMEOUT_S = 8.0
TEST_WAIT_S = 12.0
TITLE_MAX, BODY_MAX = 60, 120
SUBJECT = os.environ.get("ENSEMBLE_NOTIFY_CONTACT", "https://github.com/fab-ioc/ensemble")
# The push services of Chrome and Edge on every OS, Safari (macOS and iOS)
# and Firefox. The hub posts to whatever address a device gives it, so only
# these are taken: a subscription is never a way to make the hub call
# something on the tailnet or this machine.
PUSH_HOSTS = ("fcm.googleapis.com", ".googleapis.com", ".push.apple.com",
              ".push.services.mozilla.com", ".notify.windows.com")
ENDPOINT_MAX = 1024
DEVICES_MAX = 20

KIND_WORDS = {"question": "Question", "points": "Answers to check", "reported": "Ready for your check",
              "could_not_start": "Could not start", "waiting": "Waiting on you"}

_LOCK = threading.RLock()
_VAPID = None
_UNAVAILABLE = ""
_STARTED = 0.0
_FRONT: dict[str, float] = {}   # device id → in front until
_BURSTS: dict[str, dict] = {}   # project id → {start, tag, count, name}
_PO_SEEN: dict[str, dict] = {}  # PO room → {sig, changedAt, readAt}: when to read its transcript
_POOL = None
_RESEED = False                 # no device at the last look: the next one only takes note
_sender = None                  # webpush.send; tests put a fake here


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] notify: {msg}", flush=True)
    except (OSError, ValueError):
        pass


def _dir() -> Path:
    return Path(_d.DASHBOARD_DIR) / "notify"


def _read(name: str, default):
    try:
        v = json.loads((_dir() / name).read_text(encoding="utf-8"))
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(name: str, data, private: bool = False) -> None:
    p = _dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    if private and os.name != "nt":
        # Made private before a byte is written: the key is never readable.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=1))
    else:
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, p)
            return
        except OSError:
            if attempt == 4:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05 * (attempt + 1))


# ---------------------------------------------------------------------------
# The hub's key
# ---------------------------------------------------------------------------

def _vapid():
    """The signing key, made on first use and kept in vapid.json. None when
    notifications cannot run; :func:`status` then says why."""
    global _VAPID, _UNAVAILABLE
    with _LOCK:
        if _VAPID is not None:
            return _VAPID
        if webpush is None:
            _UNAVAILABLE = _IMPORT_ERROR
            return None
        try:
            saved = _read("vapid.json", {})
            key = None
            if saved.get("privateKey"):
                try:
                    key = webpush.private_from_bytes(webpush.b64u_dec(saved["privateKey"]))
                except (ValueError, TypeError):
                    key = None
            if key is None:
                key = webpush.new_private_key()
                _write("vapid.json", {"privateKey": webpush.b64u(webpush.private_bytes(key)),
                                      "publicKey": webpush.b64u(webpush.public_bytes(key)),
                                      "createdAt": time.time()}, private=True)
            _VAPID = webpush.Vapid(key, SUBJECT)
            _UNAVAILABLE = ""
        except Exception as e:      # noqa: BLE001
            _UNAVAILABLE = f"the hub could not make or read its notification key: {e.__class__.__name__}"
            return None
        return _VAPID


def status(endpoint=None) -> dict:
    """What the Settings tab shows: whether this hub can notify, the key a
    browser subscribes with, the devices (never their push addresses), and
    which one is this browser (``here``)."""
    v = _vapid()
    with _LOCK:
        devs = [_public(x) for x in _devices()]
    here = device_id(endpoint) if isinstance(endpoint, str) and endpoint else ""
    return {"available": v is not None, "reason": "" if v else _UNAVAILABLE,
            "publicKey": v.public_b64 if v else "", "devices": devs,
            "here": here if any(x["id"] == here for x in devs) else "",
            "frontEvery": FRONT_EVERY_S}


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

def _devices() -> list[dict]:
    return [x for x in _read("devices.json", {}).get("devices", []) if isinstance(x, dict) and x.get("id")]


def _save_devices(devs: list[dict]) -> None:
    _write("devices.json", {"devices": devs}, private=True)


def device_id(endpoint: str) -> str:
    return hashlib.sha256((endpoint or "").encode("utf-8")).hexdigest()[:16]


def _public(dev: dict) -> dict:
    return {k: dev.get(k) for k in ("id", "label", "createdAt", "lastOkAt", "lastError")}


def endpoint_problem(endpoint) -> str:
    """Why this push address is not taken, or ""."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > ENDPOINT_MAX:
        return "no push address"
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.password or u.port not in (None, 443):
        return "the push address is not a plain https address"
    if not any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS):
        return f"{host} is not a browser push service"
    return ""


def subscribe(sub, label: str = "") -> tuple[dict | None, str]:
    """Add or update this browser's subscription. (record, "") or (None, why)."""
    global _RESEED
    if _vapid() is None:
        return None, f"Notifications are off on this hub: {_UNAVAILABLE}"
    if not isinstance(sub, dict):
        return None, "no subscription"
    endpoint = sub.get("endpoint")
    why = endpoint_problem(endpoint)
    if why:
        return None, why
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
    try:
        webpush.public_from_bytes(webpush.b64u_dec(str(keys.get("p256dh") or "")))
        if len(webpush.b64u_dec(str(keys.get("auth") or ""))) != 16:
            raise ValueError("auth")
    except Exception:           # noqa: BLE001
        return None, "the subscription's keys are not valid"
    label = " ".join(str(label or "").split())[:60] or "A device"
    with _LOCK:
        devs = _devices()
        did = device_id(endpoint)
        old = next((x for x in devs if x.get("id") == did), None)
        if old is None and len(devs) >= DEVICES_MAX:
            return None, f"This hub already notifies {DEVICES_MAX} devices: remove one first."
        if not devs:
            # The first device of a hub that had none (however quickly after
            # the last one went): its first look only takes note, no backlog.
            _RESEED = True
        rec = {"id": did, "endpoint": endpoint,
               "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
               "label": label, "createdAt": (old or {}).get("createdAt") or time.time()}
        if old and old.get("lastOkAt"):
            rec["lastOkAt"] = old["lastOkAt"]
        _save_devices([x for x in devs if x.get("id") != did] + [rec])
    return _public(rec), ""


def unsubscribe(endpoint) -> bool:
    if not isinstance(endpoint, str):
        return False
    return remove(device_id(endpoint))


def remove(did) -> bool:
    if not isinstance(did, str) or not did:
        return False
    with _LOCK:
        devs = _devices()
        keep = [x for x in devs if x.get("id") != did]
        if len(keep) == len(devs):
            return False
        _save_devices(keep)
        _FRONT.pop(did, None)
    return True


def seen(endpoint, front: bool, now: float | None = None) -> bool:
    """A page says it is (or is no longer) in front on this browser."""
    if not isinstance(endpoint, str) or not endpoint:
        return False
    now = time.time() if now is None else now
    did = device_id(endpoint)
    with _LOCK:
        if front:
            _FRONT[did] = now + FRONT_S
        else:
            _FRONT.pop(did, None)
    return True


def in_front(did: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _LOCK:
        until = _FRONT.get(did, 0.0)
        if until and until <= now:
            _FRONT.pop(did, None)
        return until > now


def _record_result(did: str, code: int, now: float) -> None:
    """After a send: a gone subscription (404, 410) is removed; otherwise the
    outcome is kept for the Settings tab."""
    with _LOCK:
        devs = _devices()
        dev = next((x for x in devs if x.get("id") == did), None)
        if dev is None:
            return
        if code in (404, 410):
            _save_devices([x for x in devs if x.get("id") != did])
            _FRONT.pop(did, None)
            _log(f"device {did} removed: its push service says the subscription is gone ({code})")
            return
        if 200 <= code < 300:
            dev["lastOkAt"] = now
            dev.pop("lastError", None)
        else:
            dev["lastError"] = f"push service answered {code}" if code else "push service not reachable"
        _save_devices(devs)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _payload(ev: dict) -> bytes:
    return json.dumps({k: ev.get(k, "") for k in ("title", "body", "path", "tag")},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _send_one(dev: dict, ev: dict) -> int:
    v = _vapid()
    if v is None:
        return 0
    send = _sender or webpush.send
    try:
        code = send(v, dev, _payload(ev), topic=_topic(ev.get("tag", "")), timeout=SEND_TIMEOUT_S)
    except Exception as e:      # noqa: BLE001 — one device must not stop the others
        _log(f"device {dev.get('id')}: not sent: {e.__class__.__name__}")
        code = 0
    _record_result(dev.get("id", ""), code, time.time())
    return code


def _topic(tag: str) -> str:
    # RFC 8030: at most 32 URL-safe characters.
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()[:32] if tag else ""


def _pool():
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify-send")
        return _POOL


def deliver(ev: dict, now: float | None = None, wait: bool = False) -> list[str]:
    """Send one notification to every device not in front. Returns the device
    ids it went to (sent off this thread; ``wait`` for tests)."""
    now = time.time() if now is None else now
    with _LOCK:
        devs = [x for x in _devices() if not in_front(x["id"], now)]
    futs = [_pool().submit(_send_one, dev, ev) for dev in devs]
    if wait:
        concurrent.futures.wait(futs, timeout=TEST_WAIT_S)
    if devs:
        _log(f"to {len(devs)} device(s): {ev.get('title', '')}")
    return [d["id"] for d in devs]


def send_test(endpoint) -> dict:
    """A test notification to this browser only, in front or not; waits for
    the push service's answer (at most :data:`TEST_WAIT_S`)."""
    if not isinstance(endpoint, str):
        return {"ok": False, "message": "This browser is not subscribed."}
    did = device_id(endpoint)
    with _LOCK:
        dev = next((x for x in _devices() if x.get("id") == did), None)
    if dev is None:
        return {"ok": False, "message": "This browser is not subscribed. Turn notifications on first."}
    ev = {"title": "Ensemble", "body": "Notifications reach this device.", "path": "/",
          "tag": "ensemble-test"}
    try:
        code = _pool().submit(_send_one, dev, ev).result(timeout=TEST_WAIT_S)
    except concurrent.futures.TimeoutError:
        return {"ok": False, "status": 0, "message": "The push service did not answer in time."}
    if 200 <= code < 300:
        return {"ok": True, "status": code, "message": "Sent. It shows here within a few seconds."}
    if code in (404, 410):
        return {"ok": False, "status": code,
                "message": "The push service says this subscription is gone. Turn notifications off and on again."}
    return {"ok": False, "status": code,
            "message": f"The push service answered {code}." if code else "The hub could not reach the push service."}


# ---------------------------------------------------------------------------
# What is new
# ---------------------------------------------------------------------------

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
# An address (with a scheme, or www.): a link's destination, a token in a query.
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
# A path, absolute (/x/y, ~/x, C:\x) or relative with two separators or a
# file name after one (notes/private/file.txt, src/app.py); "and/or" stays.
_PATHISH = re.compile(r"(?:(?<=[\s(\"'])|^)(?:~|[A-Za-z]:)?[/\\][^\s]*[/\\][^\s]*"
                      r"|(?:(?<=[\s(\"'])|^)[\w.~-]+(?:[/\\][\w.~-]*){2,}"
                      r"|(?:(?<=[\s(\"'])|^)[\w.~-]+[/\\][\w~-]*\.[A-Za-z0-9]{1,8}\b")
# A secret named as one (token=…, password: …, Bearer …) keeps its name only.
_SECRET = re.compile(r"(?i)\b(?:(token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|auth)"
                     r"(\s*[=:]\s*)|(bearer)(\s+))(?=\S*[0-9A-Za-z])\S+")
# An opaque string long enough to be a key or token: letters and digits mixed.
_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9_\-+/=]*[0-9])(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}")
_MARKS = re.compile(r"\*\*|__|`")


def clip(text: str, n: int) -> str:
    """One short line for a lock screen: the first line, links as their words,
    no markup, no address, no file path, no token."""
    text = str(text or "").strip().split("\n", 1)[0]
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub("", text)
    text = _URL.sub("…", text)
    text = _PATHISH.sub("…", text)
    text = _SECRET.sub(lambda m: (m.group(1) + "=" if m.group(1) else m.group(3) + " ") + "…", text)
    text = _OPAQUE.sub("…", text)
    text = " ".join(_MARKS.sub("", text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _q(v: str) -> str:
    return quote(str(v), safe="").replace("%3A", ":")


def _msg_path(rid: str, mid: str = "", part: str = "") -> str:
    path = f"/session?room={_q(rid)}"
    if mid:
        path += f"&msg={_q(mid)}"
        p = "inbox" + ("," + part if part else "")
        path += "&part=" + quote(p, safe="").replace("%3A", ":").replace("%2C", ",")
    return path


def _row_kind(row: dict) -> str:
    if row.get("kind") == "answer":
        return "question"
    if row.get("kind") == "check":
        return "points" if str(row.get("id", "")).endswith(":points") else "reported"
    if row.get("cause") == "folder_missing":
        return "could_not_start"
    return "waiting"


def row_events(rows: list[dict]) -> list[dict]:
    """The candidates of one Inbox build: one per row, one per answer of a
    grouped "answers to check" row. Each with its ``key``."""
    out = []
    for r in rows or []:
        rid, kind = r.get("roomId", ""), _row_kind(r)
        name = r.get("project") or "Ensemble"
        base = {"projectId": r.get("projectId") or "", "project": r.get("project") or "",
                "roomId": rid, "title": clip(f"{name} · {KIND_WORDS[kind]}", TITLE_MAX)}
        if kind == "points":
            for it in r.get("items") or []:
                pts = ",".join(it.get("points") or [])
                out.append({**base, "key": f"{rid}|pt|{pts}|{it.get('answer') or it.get('mid') or ''}",
                            "mid": it.get("mid") or "", "since": float(it.get("since") or 0),
                            "kind": kind, "body": clip(it.get("line") or it.get("reason") or "", BODY_MAX),
                            "path": _msg_path(rid, it.get("mid") or "")})
            continue
        out.append({**base, "key": f"{r.get('id', '')}|{int(float(r.get('since') or 0))}",
                    "mid": r.get("mid") or "", "since": float(r.get("since") or 0), "kind": kind,
                    "body": clip(r.get("line") or r.get("reason") or "", BODY_MAX),
                    "path": _msg_path(rid, r.get("mid") or "", r.get("part") or "")})
    return out


def _fresh(ev: dict, now: float) -> bool:
    """Whether a row seen for the first time is news: it began recently, or
    after the watcher started. A ``waiting`` row's state can be found well
    after it began (a stall is noticed late), so it counts while young."""
    since = float(ev.get("since") or 0)
    if since and now - since > MAX_AGE_S:
        return False
    if ev.get("kind") == "waiting":
        return True
    return not since or now - since <= FRESH_S or since >= _STARTED - 60


def _po_rooms() -> list[tuple[str, dict]]:
    try:
        return [(p["poRoomId"], p) for p in _d.load_projects() if p.get("poRoomId")]
    except Exception:           # noqa: BLE001
        return []


def _po_session(rid: str) -> str:
    cr = _d.chatroom
    room = cr.get_room(rid, public=False, copy_room=False, messages=False)
    if not room:
        return ""
    part = cr.participant(room, cr.po_identity(room))
    return ((part or {}).get("sessionId") or "").strip()


def po_replies(sid: str, turns: list[dict], after: float) -> list[tuple[str, dict, float]]:
    """A PO session's replies to the person after ``after``: (mid, turn, ts)."""
    out = []
    for mid, t in _d.page_turn_ids(sid, turns or []):
        if t.get("role") != "assistant" or t.get("interim") or t.get("hidden"):
            continue
        if (t.get("answers") or {}).get("kind") != "human":
            continue
        ts = _d._turn_epoch(t.get("timestamp"))
        if ts and ts > after:
            out.append((mid, t, ts))
    return out


def _po_events(st: dict, mids: dict, now: float, seed: bool) -> list[dict]:
    """New PO replies to the person, one event each, with the PO watermarks
    in ``st`` brought up to date. A transcript is read only once it changed
    and then held still a tick (its reply finished), or every
    :data:`PO_REREAD_S` while it keeps changing."""
    out = []
    marks = st.setdefault("po", {})
    for rid, proj in _po_rooms():
        try:
            sid = _po_session(rid)
            if not sid:
                continue
            sig = _d.points._session_stat(sid)
            mark = marks.get(rid)
            if mark is None or seed:
                marks[rid] = {"after": now}
                _PO_SEEN[rid] = {"sig": sig, "changedAt": now, "readAt": now, "dirtySince": 0.0}
                continue
            look = _PO_SEEN.setdefault(rid, {"sig": None, "changedAt": 0.0, "readAt": 0.0, "dirtySince": 0.0})
            if sig != look["sig"]:
                look["sig"], look["changedAt"] = sig, now
                look["dirtySince"] = look.get("dirtySince") or now
                if now - look["dirtySince"] < PO_REREAD_S:
                    continue    # still being written: read when it holds still
            elif look["readAt"] >= look["changedAt"]:
                continue        # nothing written since it was read
            look["readAt"], look["dirtySince"] = now, 0.0
            turns = _d.read_session_turns(sid) or []
            after = max(float(mark.get("after") or 0), now - MAX_AGE_S)
            name = proj.get("name") or "Project"
            for mid, t, ts in po_replies(sid, turns, after):
                mark["after"] = max(float(mark.get("after") or 0), ts)
                if f"{rid}|{mid}" in mids:
                    continue    # notified already, as its Inbox card
                out.append({"key": f"{rid}|po|{mid}", "kind": "po", "projectId": proj.get("id") or "",
                            "project": proj.get("name") or "", "roomId": rid, "mid": mid, "since": ts,
                            "title": clip(f"{name} PO", TITLE_MAX), "body": clip(t.get("text") or "", BODY_MAX),
                            "path": f"/session?room={_q(rid)}&msg={_q(mid)}"})
        except Exception as e:      # noqa: BLE001 — one PO chat must not stop the rest
            _log(f"{rid}: PO chat not read: {e.__class__.__name__}: {str(e)[:120]}")
    return out


def events(now: float | None = None, snap: dict | None = None, reseed: bool = False) -> list[dict]:
    """What is new now (PO replies first, then Inbox rows), with the keys
    brought up to date on disk. ``reseed``: take note of everything listed
    and send nothing (the first run, and the first after a device is added
    to a hub that had none). ``snap`` is for tests."""
    now = time.time() if now is None else now
    if snap is None:
        snap = _d.inbox.snapshot()
    with _LOCK:
        st = _read("state.json", {})
        seed = reseed or not st.get("seeded")
        mids = st.setdefault("mids", {})       # "room|mid" already notified
        out = _po_events(st, mids, now, seed)
        for ev in out:
            mids[f"{ev['roomId']}|{ev['mid']}"] = now
        keys = st.setdefault("rows", {})
        failed = bool(snap.get("error"))
        for ev in ([] if failed else row_events(snap.get("rows") or [])):
            k = ev["key"]
            if k in keys:
                keys[k] = now
                continue
            if snap.get("pending") and ev["kind"] == "waiting" and ev["mid"] and not seed:
                # While a chat's cards are read, a Needs you item they hold
                # shows as a row of its own for a moment: not news.
                continue
            keys[k] = now
            if seed:
                continue
            mk = f"{ev['roomId']}|{ev['mid']}"
            if ev["mid"] and mk in mids:
                continue        # the PO's message, already notified as such
            if not _fresh(ev, now):
                continue
            if ev["mid"]:
                mids[mk] = now
            out.append(ev)
        for store in (keys, mids):
            for k in [k for k, t in store.items() if now - float(t or 0) > KEEP_S]:
                store.pop(k, None)
        # Seeded once a whole list was read (not a failed or partial one).
        st["seeded"] = (st.get("seeded") and not reseed) or (not failed and not snap.get("pending"))
        _write("state.json", st)
    return out


def bursts(evs: list[dict], now: float) -> list[dict]:
    """Group new events into what is shown: per project, the first at once
    under a new tag; more within :data:`BURST_S` of that first replace it
    ("3 new in Motors")."""
    by_proj: dict[str, list[dict]] = {}
    for ev in evs:
        by_proj.setdefault(ev.get("projectId") or "", []).append(ev)
    shown = []
    with _LOCK:
        for pid, group in by_proj.items():
            b = _BURSTS.get(pid)
            if b and now - b["start"] < BURST_S:
                b["count"] += len(group)
            else:
                b = _BURSTS[pid] = {"start": now, "count": len(group),
                                    "tag": f"p-{pid or 'none'}-{int(now)}"}
            newest = group[-1]
            if b["count"] == 1:
                shown.append({**newest, "tag": b["tag"]})
                continue
            name = newest.get("project") or ""
            more = b["count"] - 1
            shown.append({**newest, "tag": b["tag"],
                          "title": clip(f"{b['count']} new in {name}" if name else f"{b['count']} new", TITLE_MAX),
                          "body": clip(f"Newest: {newest.get('body') or newest.get('title')}", BODY_MAX - 16)
                          + f" · and {more} more",
                          "path": f"/?inbox={_q(pid)}" if pid else "/"})
        for pid in [p for p, b in _BURSTS.items() if now - b["start"] >= BURST_S * 4]:
            _BURSTS.pop(pid, None)
    return shown


def tick(now: float | None = None) -> list[dict]:
    """One look. With no device subscribed nothing is read (no cost on a hub
    that does not use notifications); the first look after one is added
    only takes note of what is there, so turning on sends no backlog."""
    global _RESEED
    now = time.time() if now is None else now
    if _vapid() is None:
        return []
    with _LOCK:
        if not _devices():
            _RESEED = True
            return []
        reseed, _RESEED = _RESEED, False
    shown = bursts(events(now, reseed=reseed), now)
    for ev in shown:
        deliver(ev, now)
    return shown


def start() -> None:
    """The watcher: every TICK_S, the Inbox and the PO chats → notifications."""
    global _STARTED
    _STARTED = time.time()
    if _vapid() is None:
        _log(f"off: {_UNAVAILABLE}")

    def loop():
        while True:
            try:
                tick()
            except Exception as e:      # noqa: BLE001 — keep the loop alive
                _log(f"check failed: {e.__class__.__name__}: {str(e)[:200]}")
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True, name="notify").start()
