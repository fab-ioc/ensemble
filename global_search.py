"""Search from the top bar, wherever you are (#161, GitHub issue 6).

One query finds three kinds of thing:

* **tasks**: a task's title, number (``#18``, ``ED-18``) and spec, and the
  conversations its agents had on it (every seat, every rotation, every
  review);
* **past sessions**: conversations no task holds (your own terminal sessions,
  older agents' ones), by their name, first and last prompt, folder, and text;
* **messages**: a task's or a PO's chat messages, each its own result, so the
  page can open the chat at that message.

The query reads as the old dashboard's did: words are ANDed, an uppercase
``OR`` separates alternatives, ``"a phrase"`` keeps its spaces, and case is
ignored.

Two halves, so typing never waits on gigabytes:

* the **quick** half reads what is small and kept in memory here: the rooms'
  titles, specs and chats (re-read only when a room's file changes) and the
  session rows the task list already built;
* the **deep** half reads every Claude and Codex transcript (gigabytes on a
  busy hub). It runs as a child process (``python global_search.py`` reading
  a JSON request on stdin), split over worker processes, so the hub stays
  free and a newer query can kill it. Each file is matched on its raw bytes
  first; only the lines holding a term are parsed and checked as text.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

QUERY_MAX = 300
MIN_QUERY = 2
SNIPPET_PAD = 60
ITEMS_MAX = 50           # results sent per group; the count says how many there are
LINES_PER_FILE = 4000    # lines parsed per transcript and term, at most
DEADLINE_S = 40.0
# Fields that hold ids, hashes, signatures and bookkeeping (the model, a
# message's role, a part's type) rather than words.
SKIP_FIELDS = {"id", "uuid", "parentUuid", "tool_use_id", "requestId", "signature",
               "sessionId", "session_id", "call_id", "encrypted_content", "timestamp",
               "model", "role", "type", "stop_reason"}


# ---- The query ----------------------------------------------------------------

def parse_query(q: str) -> list[list[str]]:
    """OR-groups of AND-terms, lower-cased: ``a b`` both, ``a OR b`` either,
    ``"a b"`` a phrase. [] when nothing is left to look for."""
    q = (q or "").strip()[:QUERY_MAX]
    tokens: list[str] = []
    i, n = 0, len(q)
    while i < n:
        ch = q[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = q.find('"', i + 1)
            if end == -1:
                tokens.append(q[i + 1:])
                break
            tokens.append(q[i + 1:end])
            i = end + 1
        else:
            end = i
            while end < n and not q[end].isspace():
                end += 1
            tokens.append(q[i:end])
            i = end
    groups: list[list[str]] = [[]]
    for t in tokens:
        if t == "OR":
            if groups[-1]:
                groups.append([])
        elif t.strip():
            groups[-1].append(t.lower())
    return [g for g in groups if g]


def terms_of(groups: list[list[str]]) -> list[str]:
    return sorted({t for g in groups for t in g})


def satisfied(seen: set[str], groups: list[list[str]]) -> bool:
    return any(all(t in seen for t in g) for g in groups)


def text_matches(text: str, groups: list[list[str]]) -> bool:
    low = (text or "").lower()
    return any(all(t in low for t in g) for g in groups)


def snippet(text: str, terms: list[str], pad: int = SNIPPET_PAD) -> str:
    """A line of ``text`` around its first match, on one line."""
    text = text or ""
    low = text.lower()
    best = -1
    blen = 0
    for t in terms:
        k = low.find(t)
        if k != -1 and (best == -1 or k < best):
            best, blen = k, len(t)
    if best == -1:
        best, blen = 0, 0
    s = max(0, best - pad)
    e = min(len(text), best + blen + pad)
    out = ("…" if s > 0 else "") + text[s:e] + ("…" if e < len(text) else "")
    return " ".join(out.split())


# ---- Rooms: tasks, POs and their chats (the quick half) ------------------------

_ROOM_CACHE: dict[str, tuple[tuple[int, int], dict | None]] = {}
_ROOM_LOCK = threading.Lock()      # the hub's request threads share the cache


def _room_entry(d: dict) -> dict:
    msgs = []
    for m in d.get("messages") or []:
        if not isinstance(m, dict):
            continue
        text = m.get("text") if isinstance(m.get("text"), str) else ""
        if not text:
            continue
        msgs.append({"id": str(m.get("id") or ""), "ts": float(m.get("ts") or 0), "from": str(m.get("from") or ""),
                     "to": str(m.get("to") or ""), "text": text, "low": text.lower()})
    title = str(d.get("title") or "")
    spec = d.get("spec") if isinstance(d.get("spec"), str) else ""
    return {"id": str(d.get("id") or ""), "no": d.get("no"), "projectId": str(d.get("projectId") or ""),
            "title": title, "titleLow": title.lower(), "spec": spec, "specLow": spec.lower(),
            "updatedAt": float(d.get("updatedAt") or 0), "workflow": str(d.get("workflow") or ""),
            "archived": bool(d.get("archived")), "draft": bool(d.get("draft")), "msgs": msgs}


def room_entries(rooms_dir: Path, extra=None) -> list[dict]:
    """Every room, read again only when its file changed (size or time).
    ``extra(room)``: more the caller keeps about a room, under ``"extra"``."""
    with _ROOM_LOCK:
        return _room_entries(rooms_dir, extra)


def _room_entries(rooms_dir: Path, extra) -> list[dict]:
    out = []
    live: set[str] = set()
    try:
        paths = list(Path(rooms_dir).glob("room-*.json"))
    except OSError:
        paths = []
    for p in paths:
        key = str(p)
        live.add(key)
        try:
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            continue
        hit = _ROOM_CACHE.get(key)
        if hit is None or hit[0] != sig:
            try:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
                entry = _room_entry(d) if isinstance(d, dict) else None
                if entry is not None:
                    entry["extra"] = extra(d) if extra else {}
            except (OSError, ValueError):
                entry = None
            hit = (sig, entry)
            _ROOM_CACHE[key] = hit
        if hit[1] is not None:
            out.append(hit[1])
    for k in [k for k in _ROOM_CACHE if k not in live]:
        del _ROOM_CACHE[k]
    return out


def search_rooms(entries: list[dict], groups: list[list[str]], refs: dict[str, list[str]] | None = None
                 ) -> tuple[dict[str, dict], list[dict]]:
    """Tasks by title, number, spec or chat ({roomId: hit}) and chat messages that
    match on their own (newest first). ``refs``: a room's other names, as
    ``#18`` and ``ED-18``."""
    terms = terms_of(groups)
    tasks: dict[str, dict] = {}
    messages: list[dict] = []
    for e in entries:
        names = " ".join([e["titleLow"]] + [r.lower() for r in (refs or {}).get(e["id"], [])])
        if any(all(t in names for t in g) for g in groups):
            tasks[e["id"]] = {"roomId": e["id"], "where": "title", "snippet": "", "hits": 0}
        elif e["specLow"] and any(all(t in e["specLow"] or t in names for t in g) for g in groups):
            tasks[e["id"]] = {"roomId": e["id"], "where": "spec", "snippet": snippet(e["spec"], terms), "hits": 0}
        own = []
        for m in e["msgs"]:
            if any(all(t in m["low"] for t in g) for g in groups):
                own.append(m)
                messages.append({"roomId": e["id"], "msgId": m["id"], "ts": m["ts"], "from": m["from"],
                                 "to": m["to"], "snippet": snippet(m["text"], terms)})
        # A task is also found by its chat, the words spread over its title,
        # spec and messages as the old dashboard's rows allowed.
        if e["id"] not in tasks and e["msgs"] and any(
                all(t in names or t in e["specLow"] or any(t in m["low"] for m in e["msgs"]) for t in g)
                for g in groups):
            first = own[-1] if own else next((m for m in reversed(e["msgs"]) if any(t in m["low"] for t in terms)), None)
            tasks[e["id"]] = {"roomId": e["id"], "where": "chat", "hits": len(own),
                              "snippet": snippet(first["text"], terms) if first else ""}
    messages.sort(key=lambda m: -m["ts"])
    return tasks, messages


def search_rows(rows: list[dict], groups: list[list[str]]) -> dict[str, dict]:
    """The sessions no task holds, by what the old dashboard's rows matched on:
    name, first and last prompt, folder and id."""
    terms = terms_of(groups)
    out = {}
    for r in rows:
        sid = r.get("sessionId") or ""
        if not sid or r.get("roomId"):
            continue
        fields = [str(r.get(k) or "") for k in ("label", "first", "last", "cwd", "sessionId")]
        if text_matches(" ".join(fields), groups):
            shown = next((f for f in fields if text_matches(f, groups)), fields[0])
            out[sid] = {"sessionId": sid, "snippet": snippet(shown, terms), "hits": 0}
    return out


# ---- Transcripts (the deep half, in a child process) ---------------------------

def _needles(term: str) -> tuple[bytes, ...]:
    """What a file holding ``term`` must hold, one of, to look for in its
    lower-cased bytes: the term as it appears inside a JSON string. Bytes
    lower-case only ASCII, so a term with other letters (``café``) is looked
    for by its longest ASCII run (``caf``); one with none (``привет``) by its
    lower, capitalised and upper case forms, raw or ``\\u`` escaped. The
    lines found are then checked as text, where case is folded properly."""
    esc = json.dumps(term, ensure_ascii=False)[1:-1].lower()
    if esc.isascii():
        return (esc.encode("ascii"),)
    run = max(re.findall(r"[\x00-\x7f]+", esc), key=len, default="")
    if re.search(r"[a-z0-9]", run):     # a run of punctuation (``при-вет``) is on too many lines
        return (run.encode("ascii"),)
    forms = (term.lower(), term.capitalize(), term.upper())
    return tuple(dict.fromkeys(json.dumps(f, ensure_ascii=ascii_)[1:-1].encode("utf-8").lower()
                               for f in forms for ascii_ in (False, True)))


def _walk(obj, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP_FIELDS:
                _walk(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk(x, out)
    elif isinstance(obj, str) and obj:
        out.append(obj)


def _texts_of_line(d: dict, agent: str) -> list[str]:
    """The words a person or an agent wrote, or a tool read or wrote, in one
    transcript line; nothing for bookkeeping lines (a Codex turn's context is
    its whole base prompt, which would match nearly anything)."""
    out: list[str] = []
    if agent == "codex":
        if d.get("type") != "response_item":
            return out
        _walk(d.get("payload") or {}, out)
    else:
        if d.get("type") not in ("user", "assistant"):
            return out
        msg = d.get("message")
        if isinstance(msg, dict):
            _walk(msg, out)
    return out


def _first_value(data: bytes, key: bytes) -> str:
    """The first ``"key":"…"`` string in a file, read from its line."""
    m = re.search(rb'"' + re.escape(key) + rb'"\s*:\s*"', data)
    if m is None:
        return ""
    k = m.start()
    s = data.rfind(b"\n", 0, k) + 1
    e = data.find(b"\n", k)
    try:
        d = json.loads(data[s:e if e != -1 else len(data)].decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(d, dict):
        v = d.get(key.decode())
        if not v and isinstance(d.get("payload"), dict):
            v = d["payload"].get(key.decode())
        return v if isinstance(v, str) else ""
    return ""


def session_id_of(path: str, agent: str) -> str:
    stem = Path(path).stem
    if agent == "codex":
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", stem)
        return m.group(1) if m else stem
    return stem


def scan_file(path: str, agent: str, groups: list[list[str]]) -> dict | None:
    """One transcript: None unless it satisfies the query; else its session,
    folder, number of matching messages and a snippet around the first."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    low = data.lower()
    terms = terms_of(groups)
    needles = {t: _needles(t) for t in terms}
    present = {t for t, nds in needles.items() if any(nd in low for nd in nds)}
    if not satisfied(present, groups):
        return None
    # The lines holding a term, in file order, parsed once each; each term
    # has its own cap, so a common word cannot crowd out a rare one's lines.
    starts: set[int] = set()
    for t in present:
        for nd in needles[t]:
            n = 0
            k = low.find(nd)
            while k != -1 and n < LINES_PER_FILE:
                s = low.rfind(b"\n", 0, k) + 1
                starts.add(s)
                n += 1
                e = low.find(b"\n", k)
                if e == -1:
                    break
                k = low.find(nd, e)
    seen: set[str] = set()
    hits = 0
    snip = ""
    for s in sorted(starts):
        e = data.find(b"\n", s)
        try:
            d = json.loads(data[s:e if e != -1 else len(data)].decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        matched = False
        for text in _texts_of_line(d, agent):
            tl = text.lower()
            here = [t for t in present if t in tl]
            if not here:
                continue
            matched = True
            seen.update(here)
            if not snip:
                snip = snippet(text, here)
        if matched:
            hits += 1
    if not hits or not satisfied(seen, groups):
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {"sessionId": session_id_of(path, agent), "agent": agent, "path": path,
            "cwd": _first_value(data, b"cwd"), "hits": hits, "snippet": snip, "updatedAt": mtime}


def _scan_chunk(args) -> list[dict]:
    files, groups, deadline = args
    out = []
    for path, agent in files:
        if time.time() > deadline:
            break
        r = scan_file(path, agent, groups)
        if r:
            out.append(r)
    return out


def _chunks(files: list[list[str]], n: int) -> list[list[list[str]]]:
    """``files`` in ``n`` piles of about the same size in bytes."""
    sized = []
    for f in files:
        try:
            sized.append((os.path.getsize(f[0]), f))
        except OSError:
            continue
    sized.sort(key=lambda x: -x[0])
    piles: list[tuple[int, list]] = [(0, []) for _ in range(max(1, n))]
    for size, f in sized:
        i = min(range(len(piles)), key=lambda j: piles[j][0])
        piles[i] = (piles[i][0] + size, piles[i][1] + [f])
    return [p for _, p in piles if p]


def scan_files(files: list[list[str]], groups: list[list[str]], workers: int = 0,
               deadline: float = DEADLINE_S) -> dict:
    """Every transcript in ``files`` ([path, agent]) that satisfies the query,
    most matching messages first. Workers are processes: matching bytes holds
    the interpreter, so threads would take turns."""
    t0 = time.time()
    until = t0 + deadline
    n = workers or min(8, os.cpu_count() or 1)
    piles = _chunks(files, n * 2)
    found: list[dict] = []
    if n <= 1 or len(piles) <= 1:
        for p in piles:
            found += _scan_chunk((p, groups, until))
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=n) as ex:
            for part in ex.map(_scan_chunk, [(p, groups, until) for p in piles]):
                found += part
    found.sort(key=lambda r: (-r["hits"], -r["updatedAt"]))
    return {"found": found, "files": len(files), "timedOut": time.time() > until,
            "seconds": round(time.time() - t0, 2)}


def main() -> None:
    """The child: a request on stdin, the answer on stdout. Its workers end
    with it, however it ends (the hub kills a search a newer query replaced);
    where that cannot be arranged it scans alone rather than leave them."""
    workers = 0
    try:
        import workspace_search
        workspace_search._children_die_with_me()
    except (OSError, ImportError):
        workers = 1
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        groups = parse_query(req.get("q") or "")
        res = scan_files(req.get("files") or [], groups, workers or int(req.get("workers") or 0),
                         float(req.get("deadline") or DEADLINE_S))
    except Exception as e:  # noqa: BLE001 — the hub reads the error
        res = {"error": "search_failed", "detail": repr(e)}
    sys.stdout.buffer.write(json.dumps(res).encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
