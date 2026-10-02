"""GitHub issues of the feedback repo reach the project's PO (ED-162).

The CEO files bugs and ideas as issues of the ``feedbackRepo`` setting
(``fab-ioc/ensemble`` by default: Send feedback, or straight on GitHub).
Nothing told the PO about them: issues 5 and 6 sat unseen for a day. So:

* **Polling.** Every ``POLL_S`` (15 min; the first a minute after the hub
  started, or 15 min after the last poll before a restart) the hub lists the
  repo's open issues and the comments since the last poll with ``gh api``, on
  the machine's own ``gh`` login. With no working login nothing is polled:
  one log line (again only once a login has worked in between), nothing shown
  as broken. Pull requests are left out.
* **What is new.** An open issue whose number was never seen, one that was
  closed at the last poll and is open again (reopened), and a comment on an
  open issue written after the last poll, except the PO's own: those carry
  ``PO_MARK`` (the hub's ``gh`` login is the CEO's own account, so the author
  cannot tell them apart). The comments are asked for from the last poll that
  read GitHub, so an outage loses none; an edit of an older comment is not new.
* **Who wrote it.** The repo is public. An issue or comment by someone with no
  role in it (``author_association`` not OWNER, MEMBER or COLLABORATOR) says
  so in its line: "by <login>, outside the team". Control characters are
  dropped from everything that reaches the terminal.
* **Delivery.** Each is queued once and delivered to the PO of the project
  whose code repo's ``origin`` is the feedback repo, else of the project named
  ``Ensemble Dashboard``: typed into an idle PO as one line, ``[issue] #N
  title (labels): first ~300 chars — url`` or ``[issue comment] #N title:
  login: … — url``. Everything queued goes in ONE line (``[issue] N new on
  GitHub: … | …``, each with its link, excerpts shortened to fit), so a burst
  of comments is one wake, not one a minute. A PO that is busy is tried again
  each minute. A PO that is not running gets
  the item in its room's chat as a line from the hub, once (like ``[due]``),
  and is typed it when it runs again within a day.
* **State.** ``DASHBOARD_DIR/issues.json``: the repo, the issue numbers seen
  and which were open, the comment ids seen, the queue, and the last poll — so
  a restart never delivers anything twice. The first poll of a repo (no state
  for it) records every open issue as seen and delivers nothing; comments
  older than that first poll are never delivered.

Bound to the dashboard module like ``due``: nothing here reads ``_d`` at
import time.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import unicodedata
from datetime import datetime, timezone, timedelta

import feedback

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


POLL_S = 15 * 60            # how often GitHub is asked
TICK_S = 60                 # how often the queue is looked at
MAX_AGE_S = 24 * 3600       # a PO told in chat is typed the item only this long after
EXCERPT = 300               # the issue's or comment's words in the line
OVERLAP_S = 3600            # comments are asked for this far before the last poll
KEEP_COMMENTS_S = 14 * 86400
FALLBACK_PROJECT = "Ensemble Dashboard"
SENDER = "ensemble"
PREFIX = "[issue] "
COMMENT_PREFIX = "[issue comment] "
# The PO's own comments carry this (the skill says so): the hub's gh login is
# the CEO's own account, so the author cannot tell the PO's answer from the
# CEO's comment.
PO_MARK = "<!-- ensemble-po -->"
_WAKE_MAX = 4000            # one line names everything new: a burst is one wake

_LOCK = threading.Lock()        # one change of the state at a time
_POLLING = threading.Lock()     # one poll at a time
_LAST = 0.0                     # when maybe_tick last looked
_STARTED = 0.0
_LAST_POLL = 0.0                # when poll last ran in this hub (a failed one is not saved)
_NO_LOGIN_LOGGED = False
_REPO_OF_PATH: dict[str, str] = {}


def _log(msg: str) -> None:
    try:
        print(f"[{time.strftime('%H:%M:%S')}] issues: {msg}", flush=True)
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _state_file():
    return _d.DASHBOARD_DIR / "issues.json"


def _load() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d = d if isinstance(d, dict) else {}
    out = {"repo": d.get("repo") if isinstance(d.get("repo"), str) else "",
           "lastPoll": float(d.get("lastPoll") or 0) if isinstance(d.get("lastPoll"), (int, float)) else 0.0,
           "lastGood": float(d.get("lastGood") or 0) if isinstance(d.get("lastGood"), (int, float)) else 0.0,
           "commentsAfter": d.get("commentsAfter") if isinstance(d.get("commentsAfter"), str) else ""}
    for k in ("issues", "comments"):
        out[k] = d.get(k) if isinstance(d.get(k), dict) else {}
    out["queue"] = [q for q in d.get("queue") or [] if isinstance(q, dict) and q.get("key")]
    return out


def _save(state: dict) -> None:
    f = _state_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError as e:
        _log(f"cannot save what was seen: {e}")


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def _gh(*args) -> str | None:
    """stdout of ``gh api …``, or None when it failed (no gh, no login, offline)."""
    try:
        r = feedback.run("gh", "api", "--hostname", "github.com", *args)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout is not None else None


def _lines(out: str) -> list[dict]:
    items = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            v = json.loads(ln)
        except ValueError:
            continue
        if isinstance(v, dict):
            items.append(v)
    return items


def login() -> str:
    out = _gh("user", "--jq", ".login")
    return (out or "").strip()


def open_issues(repo: str) -> list[dict] | None:
    out = _gh(f"repos/{repo}/issues?state=open&per_page=100", "--paginate", "--jq", ".[]")
    if out is None:
        return None
    return [i for i in _lines(out) if isinstance(i.get("number"), int) and "pull_request" not in i]


def comments_since(repo: str, since_iso: str) -> list[dict] | None:
    out = _gh(f"repos/{repo}/issues/comments?since={since_iso}&per_page=100",
              "--paginate", "--jq", ".[]")
    return None if out is None else _lines(out)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(iso: str) -> float:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# The lines
# ---------------------------------------------------------------------------

def _clean(text) -> str:
    """One line of plain text: whitespace collapsed, control and other
    non-printing characters (an ESC in an issue body) dropped before the
    terminal sees them."""
    s = "".join(ch if unicodedata.category(ch)[0] != "C" else " " for ch in str(text or ""))
    return " ".join(s.split())


def _excerpt(text) -> str:
    s = _clean(text)
    return s if len(s) <= EXCERPT else s[:EXCERPT].rstrip() + "…"


def _title(issue: dict) -> str:
    return _clean(issue.get("title"))[:200]


TEAM = ("OWNER", "MEMBER", "COLLABORATOR")


def _who(item: dict) -> str:
    return _clean((item.get("user") or {}).get("login"))[:60] or "someone"


def _outsider(item: dict) -> bool:
    """Written by someone with no role in the repo: the repo is public, so its
    text is a report to weigh, never the CEO's word."""
    return str(item.get("author_association") or "").upper() not in TEAM


def issue_part(issue: dict, reopened: bool = False) -> dict:
    """What a line naming several items says of this one: head, text, url."""
    labels = [_clean(l.get("name"))[:40] for l in issue.get("labels") or []
              if isinstance(l, dict) and l.get("name")]
    if reopened:
        labels.append("reopened")
    if _outsider(issue):
        labels.append(f"by {_who(issue)}, outside the team")
    tag = f" ({', '.join(labels)})" if labels else ""
    return {"head": f"issue #{issue['number']} {_title(issue)}{tag}",
            "text": _excerpt(issue.get("body")) or "(no description)",
            "url": _clean(issue.get("html_url"))}


def comment_part(comment: dict, number: int, title: str) -> dict:
    who = _who(comment) + (" (outside the team)" if _outsider(comment) else "")
    return {"head": f"comment on #{number} {title}: {who}", "text": _excerpt(comment.get("body")),
            "url": _clean(comment.get("html_url"))}


def wake_line(items: list[dict]) -> tuple[str, list[dict]]:
    """(the one line typed into the PO, the items it names). One item is its
    own line; several go in one ``[issue] N new on GitHub: …`` line, each with
    its link, their excerpts cut as short as it takes to fit ``_WAKE_MAX``
    (down to none). Items that still do not fit stay queued for the next look."""
    if len(items) == 1:
        return items[0]["line"], items[:1]

    def bit(it: dict, n: int) -> str:
        if "head" not in it:                 # queued before the parts were kept
            ln = it["line"]
            return ln[len(COMMENT_PREFIX):] if ln.startswith(COMMENT_PREFIX) else ln[len(PREFIX):]
        text = it.get("text") or ""
        text = text if len(text) <= n else text[:n].rstrip() + "…"
        return f"{it['head']}: {text} — {it['url']}" if text else f"{it['head']} — {it['url']}"

    def line(told: list[dict], n: int) -> str:
        head = f"{PREFIX}{len(told)} new on GitHub: " if len(told) > 1 else PREFIX
        return head + " | ".join(bit(it, n) for it in told)

    told = list(items)
    while len(told) > 1:
        for n in (EXCERPT, 200, 120, 80, 40, 0):
            if len(line(told, n)) <= _WAKE_MAX:
                return line(told, n), told
        told.pop()
    return told[0]["line"], told


def issue_line(issue: dict, reopened: bool = False) -> str:
    labels = [_clean(l.get("name"))[:40] for l in issue.get("labels") or []
              if isinstance(l, dict) and l.get("name")]
    if reopened:
        labels.append("reopened")
    if _outsider(issue):
        labels.append(f"by {_who(issue)}, outside the team")
    tag = f" ({', '.join(labels)})" if labels else ""
    body = _excerpt(issue.get("body")) or "(no description)"
    line = f"{PREFIX}#{issue['number']} {_title(issue)}{tag}: {body} — {issue.get('html_url', '')}"
    return line[:_WAKE_MAX]


def comment_line(comment: dict, number: int, title: str) -> str:
    who = _who(comment) + (" (outside the team)" if _outsider(comment) else "")
    line = (f"{COMMENT_PREFIX}#{number} {title}: {who}: {_excerpt(comment.get('body'))} "
            f"— {comment.get('html_url', '')}")
    return line[:_WAKE_MAX]


def _number_of(comment: dict) -> int | None:
    m = re.search(r"/issues/(\d+)$", str(comment.get("issue_url") or ""))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# One poll
# ---------------------------------------------------------------------------

def poll(now: float | None = None) -> list[dict]:
    """Ask GitHub once and queue what is new. Returns the queued items. Nothing
    is delivered here: ``deliver`` does it (each minute)."""
    global _NO_LOGIN_LOGGED, _LAST_POLL
    now = time.time() if now is None else now
    _LAST_POLL = now
    repo = (_d.load_settings().get("feedbackRepo") or feedback.DEFAULT_REPO).strip()
    if not feedback.valid_repo(repo):
        return []
    me = login()
    if not me:
        if not _NO_LOGIN_LOGGED:
            _log(f"no working gh login: the issues of {repo} are not polled")
            _NO_LOGIN_LOGGED = True
        with _LOCK:
            st = _load()
            st["lastPoll"] = now
            if st["repo"] == repo:
                _save(st)
        return []
    _NO_LOGIN_LOGGED = False
    with _LOCK:
        st = _load()
    first = st["repo"] != repo
    # Comments are asked for from the last poll that read GitHub, not the last
    # attempt: an outage must not skip what was written during it.
    good = st["lastGood"] or st["lastPoll"]
    since = good - OVERLAP_S if not first and good else now - OVERLAP_S
    issues = open_issues(repo)
    comments = None if issues is None or first else comments_since(repo, _iso(since))
    if issues is None or (comments is None and not first):
        _log(f"the issues of {repo} could not be read; tried again in {POLL_S // 60} min")
        with _LOCK:
            st = _load()
            st["lastPoll"] = now
            if st["repo"] == repo:
                _save(st)
        return []
    with _LOCK:
        st = _load()
        if st["repo"] != repo:
            st = {"repo": repo, "lastPoll": now, "lastGood": now, "commentsAfter": _iso(now), "issues": {},
                  "comments": {}, "queue": []}
            for i in issues:
                st["issues"][str(i["number"])] = {"open": True, "seen": now}
            _save(st)
            _log(f"first look at {repo}: {len(issues)} open issue(s) recorded as seen, none delivered")
            return []
        queued, keys = [], {q["key"] for q in st["queue"]}

        def put(key: str, line: str, number: int, part: dict) -> None:
            if key in keys:
                return
            keys.add(key)
            item = {"key": key, "line": line, "number": number, "at": now, "how": "", **part}
            st["queue"].append(item)
            queued.append(item)

        open_now = {}
        for i in issues:
            n = str(i["number"])
            open_now[n] = i
            rec = st["issues"].get(n)
            if not isinstance(rec, dict):
                put(f"issue:{n}", issue_line(i), i["number"], issue_part(i))
                st["issues"][n] = {"open": True, "seen": now}
            elif not rec.get("open"):
                put(f"issue:{n}:reopened:{int(now)}", issue_line(i, reopened=True), i["number"],
                    issue_part(i, reopened=True))
                rec["open"] = True
        for n, rec in st["issues"].items():
            if n not in open_now and isinstance(rec, dict):
                rec["open"] = False
        after = _ts(st["commentsAfter"])
        for c in sorted(comments or [], key=lambda c: str(c.get("created_at") or "")):
            cid = str(c.get("id") or "")
            num = _number_of(c)
            if not cid or num is None or cid in st["comments"]:
                continue
            created = _ts(c.get("created_at"))
            # ``since`` is by update: an edit of an older comment is not a new one.
            if created <= after or created < since:
                continue
            st["comments"][cid] = created
            if str(c.get("body") or "").lstrip().startswith(PO_MARK) or str(num) not in open_now:
                continue
            title = _title(open_now[str(num)])
            put(f"comment:{cid}", comment_line(c, num, title), num, comment_part(c, num, title))
        st["comments"] = {k: v for k, v in st["comments"].items()
                          if isinstance(v, (int, float)) and now - v <= KEEP_COMMENTS_S}
        st["lastPoll"] = st["lastGood"] = now
        _save(st)
    for q in queued:
        _log(f"queued for the PO: {q['line'][:120]}")
    return queued


# ---------------------------------------------------------------------------
# Delivering
# ---------------------------------------------------------------------------

def _repo_of(path: str) -> str:
    if path in _REPO_OF_PATH:
        return _REPO_OF_PATH[path]
    url = ""
    try:
        r = _d._run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True,
                    text=True, encoding="utf-8", timeout=10)
        url = (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        pass
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$", url)
    if not m:       # not kept: a git that failed once (a lock, a timeout) is asked again
        return ""
    _REPO_OF_PATH[path] = m.group(1).casefold()
    return _REPO_OF_PATH[path]


def target_project(repo: str) -> dict | None:
    """The project whose code repo is ``repo``, else Ensemble Dashboard; one with a PO."""
    projects = [p for p in _d.load_projects() if (p.get("poRoomId") or "").strip()]
    for p in projects:
        if p.get("isGit") and p.get("path") and _repo_of(p["path"]) == repo.casefold():
            return p
    return next((p for p in projects if p.get("name") == FALLBACK_PROJECT), None)


def _chat_text(items: list[dict]) -> str:
    def what(line: str) -> str:
        if line.startswith(COMMENT_PREFIX):
            return "Issue comment: " + line[len(COMMENT_PREFIX):]
        return "Issue: " + line[len(PREFIX):]
    listed = "\n".join(f"- **{what(it['line'])}**" for it in items)
    return (f"{listed}\n\nThe PO is not running, so the hub did not wake it. "
            f"It is told when it next runs, if that is within a day.")


def _deliver_one(room_id: str, items: list[dict], now: float) -> list[tuple[dict, str]]:
    """[(item, how)]: ``typed`` for the items one line names (wake_line: all
    of them, unless too many to fit) into an idle PO; ``chat`` for every item
    not yet posted, in one notice, when the PO is not running; [] when busy."""
    rot, cr = _d.rotation, _d.chatroom
    with rot.GATE:
        room = cr.get_room(room_id)
        if room is None:
            return []
        ident = cr.po_identity(room)
        part = cr.participant(room, ident) if ident else None
        if not part or rot.is_rotating(room_id, ident):
            return []
        sess = rot._pty(part)
        if sess is None:
            new = [it for it in items if it.get("how") != "chat"]
            if not new or not cr.post_notice(room_id, SENDER, _chat_text(new), {"noticeKind": "issue"}):
                return []
            return [(it, "chat") for it in new]
        if rot.awaiting_handover(room_id, ident):
            return []
        tpath, reader = rot._transcript_of(part)
        if not rot._idle(part, reader(tpath)) or rot._submitted_lately(sess):
            return []
        wake, told = wake_line(items)
        return [(it, "typed") for it in told] if _d._type_input(sess, wake) else []


def deliver(now: float | None = None) -> list[dict]:
    """Deliver the queue: returns ``[{key, line, how}]``."""
    now = time.time() if now is None else now
    with _LOCK:
        st = _load()
        if not st["queue"]:
            return []
        # Told in chat and not typed within a day: the chat line is the delivery.
        st["queue"] = [q for q in st["queue"]
                       if not (q.get("how") == "chat" and now - float(q.get("chatAt") or 0) > MAX_AGE_S)]
        proj = target_project(st["repo"])
        if not proj:
            _save(st)
            return []
        try:
            done = _deliver_one(proj["poRoomId"].strip(), st["queue"], now)
        except Exception as e:
            _log(f"not delivered: {str(e)[:200]}")
            done = []
        out = []
        for it, how in done:
            if how == "typed":
                st["queue"] = [q for q in st["queue"] if q["key"] != it["key"]]
            else:
                it["how"], it["chatAt"] = "chat", now
            out.append({"key": it["key"], "line": it["line"], "how": how})
            _log(f"{it['line'][:80]} — " + ("typed into the idle PO" if how == "typed"
                                           else "the PO is not running: written to its chat"))
        _save(st)
        return out


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------

def _poll_due(now: float) -> bool:
    with _LOCK:
        last = max(_load()["lastPoll"], _LAST_POLL)
    return now - max(last, _STARTED + 60 - POLL_S) >= POLL_S


def _poll_safely() -> None:
    try:
        poll()
    except Exception as e:
        _log(f"poll error: {str(e)[:200]}")
    finally:
        _POLLING.release()


def maybe_tick(background: bool = True) -> None:
    """Called from the progress check's loop (every few seconds): delivers the
    queue once a minute and polls GitHub once per ``POLL_S`` (on a thread of
    its own, so a slow GitHub does not hold up the other checks)."""
    global _LAST, _STARTED
    now = time.time()
    if not _STARTED:
        _STARTED = now
    if now - _LAST < TICK_S:
        return
    _LAST = now
    if _poll_due(now) and _POLLING.acquire(blocking=False):
        if background:
            threading.Thread(target=_poll_safely, daemon=True, name="issues-poll").start()
        else:
            _poll_safely()
    deliver(now)
