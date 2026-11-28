"""A project's Slack channel (#431, GitHub issue 75, steps 0 and 1).

**Step 0.** A project's settings record its Slack channel by hand
(``slackChannel`` in its ``project.json``: id, name, link, workspace, when it
was set). Nothing here creates a channel or invites anyone. A project with no
channel is untouched: no read, no state, no line.

**Step 1.** Every ``slackPollMin`` minutes (a hub setting, default 5), and
when the project's PO wakes (at most once a minute), the hub reads the
channel through the person's own Claude Slack connector (:mod:`slack_reader`:
one short Haiku run, Slack read tools only, nothing ever posted):

* the channel's new top-level messages (from the last one seen; the first read
  starts where the channel was set),
* the replies in the threads of its recent messages (``slack_read_thread``),
* the comments on the canvases its messages link to: ``slack_read_canvas``
  lists the comment threads, and each comment thread read with
  ``slack_read_thread`` gives the commented line as its first message.

**Once only.** ``DASHBOARD_DIR/slack/<project id>.json`` keeps the channel,
the last message time, the keys of every item delivered (``m:<ts>``,
``r:<parent ts>:<ts>``, ``c:<conversation>:<thread ts>:<ts>``, 30 days), the
threads and canvases watched, the connector's state and the wake queue. The
room's own Slack lines carry their key too, and a key already in the room is
never posted again, so even a crash between posting and saving repeats nothing.

**In the PO chat.** Each item is a notice in the PO's room (``noticeKind``
``slack``) with who wrote it, where (the channel, the thread it answers, the
canvas line it comments on) and its Slack link; ``outsideThread`` puts a thread
reply or a further comment in the PO thread of its parent. The PO is woken by
ONE ``[slack] N new in #channel: …`` line naming everything new, at most every
``WAKE_MIN_S`` (15 min), only when it is idle; a stopped PO is not started (it
is told when it runs again, within a day).

**Rule 5.** Nothing from Slack starts, stops or changes a task: this module
writes notices and types one line, nothing else. Slack text is flattened to
plain words, so a wake line can never be a slash command.

**Down.** No connector, not signed in, or a failed run: the channel's state
says so (the project's settings show it), the PO chat gets ONE line per
outage, and the hub tries again every ``RETRY_DOWN_S`` (30 min). When reads
work again, one line says so; nothing is missed (the next read starts at the
last message seen).

Seams for the later steps: ``add_canvas`` (step 3: a document the PO made a
canvas), ``note_own_post`` (step 2: the PO's own posts are not delivered back
to it), and ``slack_reader.run`` taking any planned calls (a write run, behind
a click, later).

Bound to the dashboard module like ``issues``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
import unicodedata

import durable
import slack_reader

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


POLL_CHOICES = (1, 2, 5, 10, 15, 30)       # minutes, hub setting slackPollMin
DEFAULT_POLL_MIN = 5
TICK_S = 60                 # how often maybe_tick looks
WAKE_READ_S = 60            # a PO that wakes has the channel read, at most this often
RETRY_DOWN_S = 30 * 60      # a channel that cannot be read is tried this often
WAKE_MIN_S = 15 * 60        # the PO is woken by Slack at most this often
MAX_AGE_S = 24 * 3600       # a queued item for a stopped PO is kept this long
KEEP_SEEN_S = 30 * 86400
WATCH_S = 14 * 86400        # a thread is read for replies this long after its last activity
MAX_THREADS = 10
MAX_COMMENT_THREADS = 20
MAX_CANVASES = 10
CHANNEL_LIMIT = 100
EXCERPT = 300
TEXT_MAX = 4000
_WAKE_MAX = 4000
SENDER = "ensemble"
PREFIX = "[slack] "
NOTICE = "slack"

_LOCK = threading.Lock()        # one change of a state file at a time
_READING = threading.Lock()     # one read run at a time
_LAST = 0.0
_PO_BUSY: dict[str, bool] = {}  # PO room -> busy at the last look (a wake is idle -> busy)
_WOKE: set[str] = set()         # project ids whose PO woke since their last read


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] slack: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Step 0: the channel
# ---------------------------------------------------------------------------

_ID = r"[CG][A-Z0-9]{8,}"
_LINK = re.compile(rf"^https?://(?P<ws>[a-z0-9-]+\.(?:enterprise\.)?slack\.com)/archives/(?P<id>{_ID})(?:[/?#].*)?$", re.I)
_APP_LINK = re.compile(rf"^https?://app\.slack\.com/client/T[A-Z0-9]+/(?P<id>{_ID})(?:[/?#].*)?$", re.I)
_NAMED = re.compile(rf"^#?(?P<name>[a-z0-9][a-z0-9._-]{{0,79}})\s*[\s(,:]\s*(?:ID:\s*)?(?P<id>{_ID})\)?$")
_BARE = re.compile(rf"^(?P<id>{_ID})$")


def parse_ref(text: str) -> dict | None:
    """``{id, name, url, workspace}`` from what the person pasted: the
    channel's link (``https://<ws>.slack.com/archives/C…``, or the app's
    ``app.slack.com/client/T…/C…``), its name and id (``#general C012…``), or
    its id alone. None when it is none of these."""
    s = " ".join(str(text or "").split())
    m = _LINK.match(s)
    if m:
        cid = m.group("id").upper()
        return {"id": cid, "name": "", "workspace": m.group("ws").lower(),
                "url": f"https://{m.group('ws').lower()}/archives/{cid}"}
    m = _APP_LINK.match(s)
    if m:
        return {"id": m.group("id").upper(), "name": "", "workspace": "", "url": s.split("#")[0]}
    m = _NAMED.match(s)
    if m:
        return {"id": m.group("id"), "name": m.group("name").lower(), "workspace": "", "url": ""}
    m = _BARE.match(s)
    if m:
        return {"id": m.group("id"), "name": "", "workspace": "", "url": ""}
    return None


def sanitize(v) -> dict | None:
    """A project's ``slackChannel`` as kept, or None for anything that is not one."""
    if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not re.fullmatch(_ID, v["id"]):
        return None
    out = {"id": v["id"]}
    for k, n in (("name", 80), ("url", 300), ("workspace", 120)):
        out[k] = v[k][:n] if isinstance(v.get(k), str) else ""
    out["setAt"] = float(v["setAt"]) if isinstance(v.get("setAt"), (int, float)) else 0.0
    return out


def set_channel(project_id: str, text, now: float | None = None) -> tuple[bool, str, dict | None]:
    """Record (or, with "" or None, remove) a project's channel. ``(ok, error, channel)``.
    A changed channel starts afresh: what arrived there before this moment is not read."""
    if text is None or not str(text).strip():
        ok, err = _d._set_project_metas(project_id, {"slackChannel": None})
        return ok, err, None
    ref = parse_ref(text)
    if ref is None:
        return False, "not_a_slack_channel", None
    proj = _d.find_project(project_id)
    old = sanitize((proj or {}).get("slackChannel"))
    if old and old["id"] == ref["id"]:
        # The same channel pasted again: keep when it was set (and what was read).
        ref = {**ref, "setAt": old["setAt"], "name": ref["name"] or old["name"],
               "workspace": ref["workspace"] or old["workspace"], "url": ref["url"] or old["url"]}
    else:
        ref["setAt"] = time.time() if now is None else now
    ok, err = _d._set_project_metas(project_id, {"slackChannel": ref})
    return ok, err, (ref if ok else None)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_file(project_id: str):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", project_id)
    return _d.DASHBOARD_DIR / "slack" / f"{safe}.json"


def _fresh(ch: dict) -> dict:
    return {"channel": ch["id"], "since": ch["setAt"], "name": ch.get("name") or "",
            "lastTs": f"{ch['setAt']:.6f}", "seen": {}, "threads": {}, "canvases": {},
            "comments": {}, "ownPosts": [], "prefix": "", "queue": [], "lastWake": 0.0,
            "lastTry": 0.0, "lastOk": 0.0,
            "status": {"state": "new", "since": 0.0, "detail": "", "downNoticed": False}}


def _load(project_id: str) -> dict:
    try:
        d = json.loads(_state_file(project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return d if isinstance(d, dict) and d.get("channel") else {}


def _state_for(project_id: str, ch: dict) -> dict:
    """The saved state of this channel, or a fresh one (a channel set again
    or changed starts afresh)."""
    st = _load(project_id)
    if st.get("channel") != ch["id"] or float(st.get("since") or 0) != ch["setAt"]:
        return _fresh(ch)
    base = _fresh(ch)
    for k, v in base.items():
        if not isinstance(st.get(k), type(v)) and not (isinstance(v, float) and isinstance(st.get(k), int)):
            st[k] = v
    return st


def _save(project_id: str, st: dict) -> None:
    f = _state_file(project_id)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        durable.write_tmp(tmp, json.dumps(st, indent=2, ensure_ascii=False))
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save what was read: {e}")


def status_of(project: dict) -> dict | None:
    """What the project's settings show of its channel: ``{state, detail,
    since, lastOk, lastTry}``; None without a channel. ``state``: new (not read
    yet), ok, not_connected, not_signed_in, failed."""
    ch = sanitize(project.get("slackChannel"))
    if not ch:
        return None
    st = _state_for(project["id"], ch)
    s = st["status"]
    return {"state": s.get("state") or "new", "detail": s.get("detail") or "",
            "since": s.get("since") or 0.0, "lastOk": st["lastOk"], "lastTry": st["lastTry"],
            "name": ch.get("name") or st.get("name") or ""}


def add_canvas(project_id: str, file_id: str, title: str = "") -> bool:
    """Step 3's seam: watch a canvas's comments (one the PO made from a document)."""
    proj = _d.find_project(project_id)
    ch = sanitize((proj or {}).get("slackChannel"))
    if not ch or not re.fullmatch(r"F[A-Z0-9]{8,}", file_id or ""):
        return False
    with _LOCK:
        st = _state_for(project_id, ch)
        st["canvases"].setdefault(file_id, {"title": _clean(title)[:200], "at": time.time()})
        _save(project_id, st)
    return True


def note_own_post(project_id: str, ts: str) -> None:
    """Step 2's seam: a message the PO posted (after its click) is not delivered back to it."""
    proj = _d.find_project(project_id)
    ch = sanitize((proj or {}).get("slackChannel"))
    if not ch:
        return
    with _LOCK:
        st = _state_for(project_id, ch)
        st["ownPosts"] = (st["ownPosts"] + [str(ts)])[-200:]
        _save(project_id, st)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _clean(text) -> str:
    """One line of plain words: control characters dropped, whitespace collapsed."""
    s = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in str(text or ""))
    return " ".join(s.split())


def flatten(text) -> str:
    """Slack's markup as plain words: ``<url|label>`` is "label (url)", a
    mention is "@name", a channel "#name"."""
    s = str(text or "")
    s = re.sub(r"<(https?://[^|>\s]+)\|([^>]+)>", r"\2 (\1)", s)
    s = re.sub(r"<(https?://[^>\s]+)>", r"\1", s)
    s = re.sub(r"<@([UW][A-Z0-9]+)\|([^>]+)>", r"@\2", s)
    s = re.sub(r"<@([UW][A-Z0-9]+)>", r"@\1", s)
    s = re.sub(r"<#([CG][A-Z0-9]+)\|([^>]*)>", lambda m: "#" + (m.group(2) or m.group(1)), s)
    s = re.sub(r"<!(here|channel|everyone)(?:\|[^>]*)?>", r"@\1", s)
    s = re.sub(r"<!subteam\^[A-Z0-9]+(?:\|([^>]*))?>", lambda m: m.group(1) or "@group", s)
    return s


def _text(text) -> str:
    """A Slack message's words as kept in the PO chat: plain, lines kept."""
    lines = [_clean(ln) for ln in flatten(text).splitlines()]
    s = "\n".join(lines).strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s if len(s) <= TEXT_MAX else s[:TEXT_MAX].rstrip() + "…"


def _excerpt(text, n: int = EXCERPT) -> str:
    s = _clean(flatten(text))
    return s if len(s) <= n else s[:n].rstrip() + "…"


_CANVAS_LINK = re.compile(r"https?://[a-z0-9.-]+\.slack\.com/docs/T[A-Z0-9]+/(F[A-Z0-9]{8,})", re.I)


def _digits(ts: str) -> str:
    return str(ts).replace(".", "")


def thread_key(conv: str, ts: str) -> str:
    return f"slack-{conv}-{_digits(ts)}"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _queries_a(ch: dict, st: dict) -> list:
    q = [slack_reader.Query("channel", "channel", {
        "channel_id": ch["id"], "oldest": st["lastTs"], "limit": CHANNEL_LIMIT,
        "response_format": "detailed"})]
    for ts in sorted(st["threads"], key=lambda t: -float(st["threads"][t]))[:MAX_THREADS]:
        q.append(slack_reader.Query(f"thread:{ts}", "thread", {
            "channel_id": ch["id"], "message_ts": ts, "limit": 100, "response_format": "detailed"}))
    for fid in list(st["canvases"])[:MAX_CANVASES]:
        q.append(slack_reader.Query(f"canvas:{fid}", "canvas", {"canvas_id": fid}))
    for key in sorted(st["comments"], key=lambda k: -float(st["comments"][k].get("at") or 0))[:MAX_COMMENT_THREADS]:
        q.append(_comment_query(key))
    return q


def _comment_query(key: str):
    conv, ts = key.split(":", 1)
    return slack_reader.Query(f"comments:{key}", "thread", {
        "channel_id": conv, "message_ts": ts, "limit": 100, "response_format": "detailed"})


def _po_room(project: dict) -> str:
    return (project.get("poRoomId") or "").strip()


def _room_keys(room_id: str) -> set:
    room = _d.chatroom.get_room(room_id)
    return {m.get("slackKey") for m in (room or {}).get("messages") or []
            if m.get("noticeKind") == NOTICE and m.get("slackKey")}


def _chan_label(ch: dict, st: dict) -> str:
    name = ch.get("name") or st.get("name") or ""
    return f"#{name}" if name else ch["id"]


def _down(project: dict, ch: dict, st: dict, err: slack_reader.ReadError, now: float) -> None:
    s = st["status"]
    code = err.code if err.code in ("not_connected", "not_signed_in") else "failed"
    if err.code == "not_connected" and err.raw:
        st["prefix"] = err.raw
    was_down = s.get("state") in ("not_connected", "not_signed_in", "failed")
    st["status"] = {"state": code, "since": s.get("since") if was_down else now,
                    "detail": _clean(str(err))[:500], "downNoticed": bool(was_down and s.get("downNoticed"))}
    _log(f"{_chan_label(ch, st)} cannot be read ({err.code}): {str(err)[:200]}")
    if st["status"]["downNoticed"]:
        return
    lozenge = {"not_connected": "Slack not connected", "not_signed_in": "Slack not signed in"}.get(
        code, "Slack cannot be read")
    text = (f"{_chan_label(ch, st)} cannot be read: {_clean(str(err))[:400]} "
            f"The hub tries again every {RETRY_DOWN_S // 60} minutes.")
    if _d.chatroom.post_notice(_po_room(project), SENDER, text, {
            "noticeKind": NOTICE, "slack": {"kind": "down", "state": code, "label": lozenge,
                                            "channel": ch["id"], "channelName": ch.get("name") or st.get("name") or ""}}):
        st["status"]["downNoticed"] = True


def _up(project: dict, ch: dict, st: dict, now: float) -> None:
    s = st["status"]
    was_down = s.get("state") in ("not_connected", "not_signed_in", "failed")
    if was_down and s.get("downNoticed"):
        _d.chatroom.post_notice(_po_room(project), SENDER,
                                f"{_chan_label(ch, st)} is read again. Nothing was missed: the hub "
                                f"read on from the last message it had seen.",
                                {"noticeKind": NOTICE, "slack": {"kind": "up", "channel": ch["id"],
                                                                 "channelName": ch.get("name") or st.get("name") or ""}})
    st["status"] = {"state": "ok", "since": now if s.get("state") != "ok" else s.get("since") or now,
                    "detail": "", "downNoticed": False}
    st["lastOk"] = now


def _item(kind: str, msg, ch: dict, st: dict, key: str, thread: str, **extra) -> dict:
    return {"key": key, "kind": kind, "author": _clean(msg.author)[:80] or "someone",
            "authorId": msg.author_id, "ts": msg.ts, "text": _text(msg.text), "thread": thread, **extra}


def _collect(ch: dict, st: dict, res, now: float) -> tuple[list[dict], list]:
    """(new items, oldest first; comment threads still to be read for the
    first time). Updates the watch lists in ``st``."""
    since = float(st["since"])
    own = set(st["ownPosts"])
    seen = st["seen"]
    items = []
    ans = res.answers.get("channel") or {}
    if ans.get("name") and not st.get("name"):
        st["name"] = _clean(ans["name"])[:80]
    for m in ans.get("messages") or []:
        if float(m.ts) <= since:
            continue
        if float(m.ts) > float(st["lastTs"]):
            st["lastTs"] = m.ts
        key = f"m:{m.ts}"
        for cm in _CANVAS_LINK.finditer(m.text or ""):
            fid = cm.group(1)
            if fid not in st["canvases"] and len(st["canvases"]) < MAX_CANVASES:
                st["canvases"][fid] = {"title": "", "at": now, "url": cm.group(0)}
        if key in seen or m.bot or m.ts in own:
            continue
        st["threads"].setdefault(m.ts, float(m.ts))
        items.append(_item("message", m, ch, st, key, thread_key(ch["id"], m.ts)))
    for label, ans in res.answers.items():
        if not label.startswith("thread:"):
            continue
        parent_ts = label.split(":", 1)[1]
        parent = ans.get("parent")
        for r in ans.get("replies") or []:
            key = f"r:{parent_ts}:{r.ts}"
            st["threads"][parent_ts] = max(float(st["threads"].get(parent_ts) or 0), float(r.ts))
            if (key in seen or f"m:{r.ts}" in seen or r.bot or r.ts in own or float(r.ts) <= since
                    or any(i["key"] == f"m:{r.ts}" for i in items)):
                continue
            items.append(_item("reply", r, ch, st, key, thread_key(ch["id"], parent_ts), parentTs=parent_ts,
                               parentAuthor=_clean(parent.author)[:80] if parent else "",
                               parentText=_excerpt(parent.text, 200) if parent else ""))
    for label in [l for l in res.errors if l.startswith("thread:")]:
        # A thread that cannot be read (its message deleted) is not read again.
        st["threads"].pop(label.split(":", 1)[1], None)
    new_threads = []
    for label, ans in res.answers.items():
        if not label.startswith("canvas:"):
            continue
        fid = label.split(":", 1)[1]
        cv = st["canvases"].setdefault(fid, {"title": "", "at": now})
        cv.pop("error", None)
        if ans.get("title"):
            cv["title"] = _clean(ans["title"])[:200]
        for conv, ts in ans.get("threads") or []:
            ck = f"{conv}:{ts}"
            if ck not in st["comments"]:
                # Watched from when it was first seen (an old comment thread is
                # read once, finds nothing new, and is dropped after WATCH_S).
                st["comments"][ck] = {"canvas": fid, "at": now}
                new_threads.append(ck)
    for label, err in res.errors.items():
        if label.startswith("canvas:"):
            fid = label.split(":", 1)[1]
            cv = st["canvases"].setdefault(fid, {"title": "", "at": now})
            cv["error"] = _clean(str(err))[:300]
    items += _comments(ch, st, res, now)
    st["threads"] = dict(sorted(((t, a) for t, a in st["threads"].items() if now - float(a) <= WATCH_S),
                                key=lambda x: -float(x[1]))[:MAX_THREADS])
    st["comments"] = {k: v for k, v in st["comments"].items() if now - float(v.get("at") or 0) <= WATCH_S}
    items.sort(key=lambda i: float(i["ts"]))
    return items, new_threads


def _comments(ch: dict, st: dict, res, now: float) -> list[dict]:
    """The new comments in the comment threads read (``comments:<conv>:<ts>``).
    The thread's first message is the commented line and the rest are the
    comments; a thread with nothing after its first message is one comment
    whose line is not known."""
    since, seen, own = float(st["since"]), st["seen"], set(st["ownPosts"])
    out = []
    for label, ans in res.answers.items():
        if not label.startswith("comments:"):
            continue
        ck = label.split(":", 1)[1]
        conv, tts = ck.split(":", 1)
        rec = st["comments"].setdefault(ck, {"canvas": "", "at": float(tts)})
        cv = st["canvases"].get(rec.get("canvas") or "") or {}
        parent, replies = ans.get("parent"), ans.get("replies") or []
        line = _excerpt(parent.text, 400) if parent and replies else ""
        for c in replies or ([parent] if parent else []):
            rec["at"] = max(float(rec.get("at") or 0), float(c.ts))
            key = f"c:{conv}:{tts}:{c.ts}"
            if key in seen or c.bot or c.ts in own or float(c.ts) <= since:
                continue
            out.append(_item("comment", c, ch, st, key, thread_key(conv, tts), canvasId=rec.get("canvas") or "",
                             canvasTitle=cv.get("title") or "", canvasUrl=cv.get("url") or "", line=line,
                             conv=conv, threadTs=tts))
    return out


def _permalink(it: dict, ch: dict) -> str:
    ws = ch.get("workspace") or ""
    if it["kind"] == "message":
        return slack_reader.link_for(ws, ch["id"], it["ts"])
    if it["kind"] == "reply":
        return slack_reader.link_for(ws, ch["id"], it["ts"], it["parentTs"])
    return it.get("canvasUrl") or slack_reader.link_for(ws, it["conv"], it["ts"], it["threadTs"])


def _notice_text(it: dict, chan: str) -> str:
    if it["kind"] == "reply":
        where = f"replied in #{chan.lstrip('#')}'s thread of {it.get('parentAuthor') or 'a message'}"
    elif it["kind"] == "comment":
        title = it.get("canvasTitle") or "a canvas"
        where = f"commented on the canvas \"{title}\"" + (f" (on the line: {it['line']})" if it.get("line") else "")
    else:
        where = f"in {chan}"
    return f"**{it['author']}** {where} (Slack): {it['text']}" + (f" — {it['link']}" if it.get("link") else "")


def _wake_part(it: dict) -> str:
    who = it["author"]
    if it["kind"] == "reply":
        who += f" (reply in the thread \"{_excerpt(it.get('parentText'), 60)}\")"
    elif it["kind"] == "comment":
        who += f" (comment on the canvas \"{it.get('canvasTitle') or 'canvas'}\"" + (
            f", on the line \"{_excerpt(it['line'], 60)}\")" if it.get("line") else ")")
    return who


def wake_line(items: list[dict], chan: str) -> tuple[str, list[dict]]:
    """(the one line typed into the PO, the items it names), like
    ``issues.wake_line``: excerpts cut to fit ``_WAKE_MAX``; items that still do
    not fit wait for the next wake."""
    def bit(it, n):
        text = _excerpt(it.get("text"), n) if n else ""
        head = _wake_part(it)
        tail = f" — {it['link']}" if it.get("link") else ""
        return f"{head}: {text}{tail}" if text else f"{head}{tail}"

    def line(told, n):
        return f"{PREFIX}{len(told)} new in {chan}: " + " | ".join(bit(it, n) for it in told)

    told = list(items)
    while told:
        for n in (EXCERPT, 200, 120, 80, 40, 0):
            if len(line(told, n)) <= _WAKE_MAX:
                return line(told, n), told
        if len(told) == 1:
            return line(told, 0)[:_WAKE_MAX], told
        told.pop()
    return "", []


def read_project(project: dict, now: float | None = None) -> list[dict]:
    """Read one project's channel once and post what is new. Returns the new items."""
    now = time.time() if now is None else now
    ch = sanitize(project.get("slackChannel"))
    room_id = _po_room(project)
    if not ch or not room_id:
        return []
    pid = project["id"]
    with _LOCK:
        st = _state_for(pid, ch)
        st["lastTry"] = now
        _save(pid, st)
    err, base = None, copy.deepcopy(st)
    try:
        res, prefix = slack_reader.run(_queries_a(ch, st), st["prefix"])
        st["prefix"] = prefix or st["prefix"]
        if "channel" in res.errors:
            raise res.errors["channel"]
        items, new_threads = _collect(ch, st, res, now)
        if new_threads:
            res_b, prefix = slack_reader.run([_comment_query(k) for k in new_threads], st["prefix"])
            st["prefix"] = prefix or st["prefix"]
            items += _comments(ch, st, res_b, now)
            items.sort(key=lambda i: float(i["ts"]))
    except slack_reader.ReadError as e:
        # Nothing of a failed read is kept but the tool prefix it saw: the
        # next read starts where this one did, so nothing is missed.
        err, items = e, []
        base["prefix"] = st["prefix"]
        st = base
    with _LOCK:
        cur = _state_for(pid, ch)
        if cur["since"] != st["since"]:
            return []      # the channel was changed meanwhile
        st["queue"], st["lastWake"] = cur["queue"], cur["lastWake"]     # deliver may have changed them
        if err is not None:
            _down(project, ch, st, err, now)
            _save(pid, st)
            return []
        _up(project, ch, st, now)
        posted = []
        in_room = _room_keys(room_id) if items else set()
        chan = _chan_label(ch, st)
        for it in items:
            if it["key"] in in_room:
                st["seen"][it["key"]] = now
                continue
            it["link"] = _permalink(it, ch)
            meta = {"noticeKind": NOTICE, "slackKey": it["key"], "outsideThread": it["thread"],
                    "slack": {"kind": it["kind"], "author": it["author"], "authorId": it["authorId"],
                              "channel": ch["id"], "channelName": ch.get("name") or st.get("name") or "",
                              "ts": it["ts"], "parentTs": it.get("parentTs") or it.get("threadTs") or "",
                              "canvasId": it.get("canvasId") or "", "canvasTitle": it.get("canvasTitle") or "",
                              "line": it.get("line") or "", "permalink": it["link"], "text": it["text"]}}
            if not _d.chatroom.post_notice(room_id, SENDER, _notice_text(it, chan), meta):
                break
            st["seen"][it["key"]] = now
            st["queue"].append({"key": it["key"], "at": now, "kind": it["kind"], "author": it["author"],
                                "text": it["text"], "link": it["link"], "line": it.get("line") or "",
                                "canvasTitle": it.get("canvasTitle") or "",
                                "parentText": it.get("parentText") or ""})
            posted.append(it)
        st["seen"] = {k: v for k, v in st["seen"].items() if now - float(v) <= KEEP_SEEN_S}
        _save(pid, st)
    if posted:
        _log(f"{len(posted)} new in {chan} posted to the PO chat")
    return posted


# ---------------------------------------------------------------------------
# Waking the PO
# ---------------------------------------------------------------------------

def _po_state(room_id: str) -> tuple[str, object]:
    """("stopped" | "busy" | "idle", its terminal), under the rotation gate."""
    rot, cr = _d.rotation, _d.chatroom
    room = cr.get_room(room_id)
    if room is None:
        return "stopped", None
    ident = cr.po_identity(room)
    part = cr.participant(room, ident) if ident else None
    if not part:
        return "stopped", None
    if rot.is_rotating(room_id, ident) or rot.awaiting_handover(room_id, ident):
        return "busy", None
    sess = rot._pty(part)
    if sess is None:
        return "stopped", None
    tpath, reader = rot._transcript_of(part)
    if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
        return "busy", sess
    return "idle", sess


def deliver(project: dict, now: float | None = None) -> list[dict]:
    """Wake the project's idle PO with what is queued, at most every ``WAKE_MIN_S``."""
    now = time.time() if now is None else now
    ch = sanitize(project.get("slackChannel"))
    room_id = _po_room(project)
    if not ch or not room_id:
        return []
    pid = project["id"]
    with _LOCK:
        st = _state_for(pid, ch)
        if not st["queue"] or not _state_file(pid).exists():
            return []
        st["queue"] = [q for q in st["queue"] if now - float(q.get("at") or 0) <= MAX_AGE_S]
        if not st["queue"] or now - float(st["lastWake"]) < WAKE_MIN_S:
            _save(pid, st)
            return []
        told, typed = [], False
        try:
            with _d.rotation.GATE:
                state, sess = _po_state(room_id)
                if state == "idle":
                    line, told = wake_line(st["queue"], _chan_label(ch, st))
                    typed = bool(line) and _d._type_input(sess, line)
        except Exception as e:
            _log(f"not delivered: {str(e)[:200]}")
        if typed:
            keys = {t["key"] for t in told}
            st["queue"] = [q for q in st["queue"] if q["key"] not in keys]
            st["lastWake"] = now
            _log(f"{len(told)} Slack item(s) typed into the idle PO")
        _save(pid, st)
        return told if typed else []


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------

def poll_min() -> int:
    v = (_d.load_settings() or {}).get("slackPollMin", DEFAULT_POLL_MIN)
    return clamp_poll(v) or DEFAULT_POLL_MIN


def clamp_poll(v) -> int | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if v in POLL_CHOICES else min(POLL_CHOICES, key=lambda c: abs(c - v))


def _projects() -> list[dict]:
    return [p for p in _d.load_projects() if sanitize(p.get("slackChannel")) and _po_room(p)]


def _due(project: dict, now: float) -> bool:
    ch = sanitize(project.get("slackChannel"))
    with _LOCK:
        st = _state_for(project["id"], ch)
    last = float(st["lastTry"])
    if st["status"].get("state") in ("not_connected", "not_signed_in", "failed"):
        return now - last >= RETRY_DOWN_S
    if project["id"] in _WOKE and now - last >= WAKE_READ_S:
        return True
    return now - last >= poll_min() * 60


def _note_wakes(projects: list[dict]) -> None:
    """A PO that went from idle (or stopped) to busy since the last look has woken."""
    for p in projects:
        rid = _po_room(p)
        try:
            with _d.rotation.GATE:
                busy = _po_state(rid)[0] == "busy"
        except Exception:
            continue
        if busy and not _PO_BUSY.get(rid, True):
            _WOKE.add(p["id"])
        _PO_BUSY[rid] = busy


def _read_due(projects: list[dict], now: float) -> None:
    try:
        for p in projects:
            if _due(p, now):
                _WOKE.discard(p["id"])
                try:
                    read_project(p, now)
                except Exception as e:
                    _log(f"read error: {str(e)[:200]}")
    finally:
        _READING.release()


def maybe_tick(background: bool = True) -> None:
    """Called from the progress check's loop: once a minute, reads the channels
    that are due (on a thread of their own: a run takes seconds) and wakes the
    POs that have something queued."""
    global _LAST
    now = time.time()
    if now - _LAST < TICK_S:
        return
    _LAST = now
    projects = _projects()
    if not projects:
        return
    _note_wakes(projects)
    if any(_due(p, now) for p in projects) and _READING.acquire(blocking=False):
        if background:
            threading.Thread(target=_read_due, args=(projects, now), daemon=True, name="slack-read").start()
        else:
            _read_due(projects, now)
    for p in projects:
        try:
            deliver(p, now)
        except Exception as e:
            _log(f"deliver error: {str(e)[:200]}")
