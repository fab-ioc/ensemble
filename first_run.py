"""A seat stopped on its CLI's own first-run screen (ED-340).

Codex and Claude Code each open a new folder with a screen that waits for a
key: Codex's "Trust this folder?" and Claude's "Is this a project you created
or one you trust?". The hub pre-trusts the folders it launches in
(``agents/*.ensure_trusted``), so these should not appear; when one does
anyway (a Codex that keys trust differently, a config the hub could not
write), nobody is at the terminal and the seat waits for ever while the board
says it is working. On 2026-11-12 ten reviews waited eleven hours like that.

This module reads such a screen off a terminal's tail and, for a folder the
hub runs agents in, answers it the way a person would. Anything it does not
answer (another first-run screen, a folder that is not the hub's) is left on
screen and ``attention`` reports the seat ``stalled`` with the screen's name.

The tail is ``PtySession.tail()``: escape sequences are stripped, so cursor
moves vanish and the words of a screen run together ("Trustthisfolder?").
Matching therefore drops every space and ignores case.
"""

from __future__ import annotations

import os
import re
import threading
import time

_d = None


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


DOWN = "\x1b[B"
ENTER = "\r"
_CURSOR = "›❯>"

# Each screen: the CLI, a short name, the words that identify it (squashed:
# no spaces, lower case, all must be present), its options in screen order,
# the option the hub picks (None: the hub never answers it), the words a
# CLI prints once it is past the screen, so an answered screen that is still
# in the tail does not count, and the screen's own closing words ("end").
# The screen counts only while it is the last thing drawn (``_is_latest``):
# its options, words and "bridge" words chain to the end of the tail, closed
# by its "end" words or a common footer. "gap" widens the text allowed
# before an option for a screen with prose among them, "tailgap" the text
# allowed before its footer.
SCREENS = (
    {"cli": "codex", "kind": "trust", "name": "Codex's \"Trust this folder?\" screen",
     "words": ("trustthisfolder?", "trustandcontinue"),
     "options": ("1.trustandcontinue", "2.back"), "pick": "1.trustandcontinue",
     "bridge": ("2.backtoagentcommandcenter",),
     "past": ("askcodextodoanything", "forshortcuts"), "end": ("entercontinue·escback",)},
    # Codex before 0.160 asked it in other words.
    {"cli": "codex", "kind": "trust", "name": "Codex's \"Do you trust the contents of this directory?\" screen",
     "words": ("doyoutrustthecontentsofthisdirectory",),
     "options": ("1.yes,", "2.no,"), "pick": "1.yes,",
     "past": ("askcodextodoanything", "forshortcuts")},
    {"cli": "claude", "kind": "trust",
     "name": "Claude Code's \"Is this a project you created or one you trust?\" screen",
     "words": ("isthisaprojectyoucreatedoroneyoutrust?", "yes,itrustthisfolder"),
     "options": ("no,exit", "yes,itrustthisfolder"), "pick": "yes,itrustthisfolder",
     "past": ("bypasspermissionson", "forshortcuts", "shift+tabtocycle")},
    # Older Claude Code: "Do you trust the files in this folder?" 1. Yes, proceed / 2. No, exit.
    {"cli": "claude", "kind": "trust", "name": "Claude Code's \"Do you trust the files in this folder?\" screen",
     "words": ("doyoutrustthefilesinthisfolder?",),
     "options": ("1.yes,proceed", "2.no,exit"), "pick": "1.yes,proceed",
     "past": ("bypasspermissionson", "forshortcuts", "shift+tabtocycle")},
    # First-run screens the hub must not answer for the person.
    {"cli": "claude", "kind": "bypass", "name": "Claude Code's Bypass Permissions warning",
     "words": ("bypasspermissionsmode", "yes,iaccept"), "options": ("1.no,exit", "2.yes,iaccept"),
     "pick": None,
     "past": ("bypasspermissionson", "forshortcuts")},
    {"cli": "claude", "kind": "theme", "name": "Claude Code's first-run setup (text style)",
     "words": ("choosethetextstyle",), "options": ("darkmode", "lightmode"), "pick": None,
     "bridge": ("(colorblind-friendly)", "(ansicolorsonly)", "syntaxtheme:"),
     "end": ("(ctrl+ttodisable)",),
     # A code preview follows its options, then the syntax theme and its name.
     "gap": 600, "tailgap": 60,
     "past": ("bypasspermissionson", "forshortcuts")},
    {"cli": "codex", "kind": "signin", "name": "Codex's sign-in screen",
     "words": ("signinwithchatgpt",),
     "options": ("2.signinwithdevicecode", "3.signinanotherway", "provideyourownapikey"),
     "pick": None,
     "gap": 120, "tailgap": 60,          # each option has a line of description
     "past": ("askcodextodoanything",)},
    {"cli": "codex", "kind": "update", "name": "Codex's \"Update available\" screen",
     "words": ("updateavailable", "skipuntilnextversion"), "options": (), "pick": None,
     "past": ("askcodextodoanything",)},
)

# Footers the CLIs draw under a screen; allowed after its last anchor.
FOOTERS = ("entertoconfirm·esctocancel", "entercontinue·escback", "entertoconfirm·esctoexit",
           "presstocontinue", "pressentertocontinue")
# Text allowed before an option (by default), before a footer (by default),
# and after the last anchor. Codex's older screen has "continue" between its
# options; the others draw them back to back.
_GAP = 10
_TAILGAP = 0
_TRAIL = 8
# In a gap, nothing that starts another question or option list: a question
# mark, a selection cursor, a numbered item. Only the lead of the next
# option ("❯ 1.", "✔") may end it.
_LEAD = re.compile(r"[›❯>]?✔?(?:\d+\.)?$")
_FOREIGN = re.compile(r"[?›❯>]|\d\.")

_SPACE = re.compile(r"\s+")


def squash(text: str) -> str:
    return _SPACE.sub("", text or "").lower()


def _find_in(flat: str) -> dict | None:
    best = None
    for scr in SCREENS:
        # The screen starts at its last-drawn question (words[0]); the rest of
        # its words must be drawn after it, and nothing the CLI prints once
        # past the screen may be.
        start = flat.rfind(scr["words"][0])
        if start < 0:
            continue
        if any(flat.find(w, start) < 0 for w in scr["words"][1:]):
            continue
        if any(flat.rfind(p) > start for p in scr["past"]):
            continue                            # it moved on since
        if not _is_latest(flat, scr, start):
            continue                            # something else was drawn after it
        if best is None or start > best[0]:
            best = (start, scr)
    if best is None:
        return None
    start, scr = best
    return {"cli": scr["cli"], "kind": scr["kind"], "name": scr["name"],
            "answerable": scr["pick"] is not None, "at": start, "screen": scr}


def _is_latest(flat: str, scr: dict, start: int, strict: bool = False) -> bool:
    """The screen is the last thing drawn. The tail is a history: a trust
    screen followed by another modal ("Grant access to this tool?", or a
    short "Allow this action" over the same footer) is still in it, and
    must be neither answered nor reported.

    From the screen's first anchor after its question, its anchors must
    follow one another to the end of the tail: an option (or word, or
    bridge word) after at most ``gap`` chars, a closing anchor (its end
    words, a common footer) after at most ``tailgap`` (none by default), a
    gap never holding a question mark, a cursor or a numbered item, and at
    most ``_TRAIL`` chars and no question mark after the last anchor. A
    later modal's own text breaks the chain before its footer.

    ``strict`` is the rule for sending a key: from its first option on, the
    screen's own options and footer (and the cursor and numbers leading an
    option) must be all that is drawn, to the very end of the tail.
    Anything after them, however short, leaves the screen to a person: it
    is reported, not answered."""
    body = tuple(scr["options"]) + tuple(scr["words"][1:]) + tuple(scr.get("bridge", ()))
    closing = tuple(scr.get("end", ())) + FOOTERS
    limit = {a: 0 if strict else scr.get("gap", _GAP) for a in body}
    limit.update({a: 0 if strict else scr.get("tailgap", _TAILGAP)
                  for a in closing if a not in limit})
    q = start + len(scr["words"][0])

    def next_anchor(p):
        nxt = None
        for a in limit:
            i = flat.find(a, p)
            if i >= 0 and (nxt is None or i < nxt[0] or (i == nxt[0] and len(a) > len(nxt[1]))):
                nxt = (i, a)
        return nxt

    first = next_anchor(q)
    p = q if first is None else first[0] + len(first[1])
    while p < len(flat):
        nxt = next_anchor(p)
        if nxt is None:
            break
        gap = flat[p:nxt[0]]
        bare = _LEAD.sub("", gap, count=1)
        if len(bare if strict else gap) > limit[nxt[1]]:
            break
        if _FOREIGN.search(bare):
            break
        p = nxt[0] + len(nxt[1])
    rest = flat[p:]
    if strict:
        return not rest
    return len(rest) <= _TRAIL and "?" not in rest


def find(tail: str) -> dict | None:
    """The first-run screen a terminal is stopped on, or None: ``{cli, kind,
    name, answerable}`` (plus internals). Only a screen nothing has been
    printed past, and nothing else drawn after, counts."""
    hit = _find_in(squash(tail))
    if hit is None:
        return None
    return {k: hit[k] for k in ("cli", "kind", "name", "answerable")}


def _cursor_at(flat: str, scr: dict, start: int) -> str:
    """The option the selection cursor is on (the last one drawn with it)."""
    seg = flat[start:]
    best, where = "", -1
    for opt in scr["options"]:
        for g in _CURSOR:
            i = seg.rfind(g + opt)
            if i > where:
                best, where = opt, i
    return best


# ---------------------------------------------------------------------------
# The folders the hub runs agents in
# ---------------------------------------------------------------------------

def _within(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        p = os.path.normcase(os.path.realpath(path))
        r = os.path.normcase(os.path.realpath(root))
    except (OSError, ValueError):
        return False
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def hub_folder(cwd: str) -> bool:
    """``cwd`` is a folder the hub made or was given to run agents in: under
    the projects root (task folders, worktrees, a project's home), under the
    scratch-session root, or a registered project's code folder (where a
    code project's PO works)."""
    if not cwd or _d is None:
        return False
    roots = [str(getattr(_d, "PROJECTS_ROOT", "") or ""), str(getattr(_d, "CS_ROOT", "") or "")]
    try:
        for p in _d.load_projects():
            roots += [p.get("path") or "", p.get("home") or ""]
    except Exception:
        pass
    return any(_within(cwd, r) for r in roots if r)


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

def codex_blocker(cwd: str) -> str:
    """Why ``cwd``'s Codex trust screen is left to a person, or "" (see
    ``agents.codex.trust_blocker``)."""
    try:
        from agents import codex
        return codex.trust_blocker(cwd)
    except Exception:
        return "unreadable"


def _key(sess) -> str:
    return str(getattr(sess, "id", "") or f"obj-{id(sess)}")


# One answer per terminal, ever: the launch watch and the stall tick may look
# at the same seat at the same moment, and a second Enter would land on
# whatever the CLI draws next.
_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_ANSWERED: dict[str, float] = {}


def answered(sess) -> bool:
    """The hub already answered a trust screen in this terminal."""
    return _key(sess) in _ANSWERED


def answer(sess, cwd: str, log=print) -> bool:
    """If ``sess`` shows an answerable trust screen and ``cwd`` is a hub
    folder, pick the trust option: move the cursor onto it (checking the
    screen after each key, never more than the options) and press Enter only
    once the cursor is on it — an Enter on Claude's default "No, exit" would
    end the seat. A terminal is answered once at most, by one caller at a
    time (another caller at the same moment gets False at once). True when
    the Enter went in."""
    if not hub_folder(cwd):
        return False
    key = _key(sess)
    with _GUARD:
        if key in _ANSWERED:
            return False
        lock = _LOCKS.get(key)
        if lock is None:
            if len(_LOCKS) > 500:
                for k in [k for k, v in _LOCKS.items() if not v.locked()]:
                    del _LOCKS[k]
            lock = _LOCKS[key] = threading.Lock()
    if not lock.acquire(blocking=False):
        return False
    try:
        if key in _ANSWERED:
            return False
        return _answer(sess, key, cwd, log)
    finally:
        lock.release()


def _answer(sess, key: str, cwd: str, log) -> bool:
    for _ in range(4):
        try:
            flat = squash(sess.tail(lines=80, max_bytes=32768))
        except Exception:
            return False
        hit = _find_in(flat)
        if hit is None or not hit["answerable"]:
            return False
        scr = hit["screen"]
        if scr["cli"] == "codex" and codex_blocker(cwd):
            return False                # marked untrusted, or a config the hub cannot read
        if not _is_latest(flat, scr, hit["at"], strict=True):
            return False                # something follows its options and footer: flag it
        on = _cursor_at(flat, scr, hit["at"])
        if on == scr["pick"]:
            # Marked before the key: an Enter that went in is never repeated.
            with _GUARD:
                _ANSWERED[key] = time.time()
                if len(_ANSWERED) > 2000:
                    for k in list(_ANSWERED)[:1000]:
                        del _ANSWERED[k]
            if sess.write(ENTER) is False:
                return False
            log(f"[first-run] answered {scr['name']} in {cwd}")
            return True
        if not on:
            return False                # no cursor drawn: not a screen to type into
        if sess.write(DOWN) is False:
            return False
        time.sleep(0.5)
    return False


# How long a fresh seat is watched for a first-run screen, and how often.
WATCH_S = 90
_POLL_S = 1.0


def watch(sess, cwd: str, log=print) -> threading.Thread | None:
    """Watch a just-launched seat for its first minute and a half, answering
    a trust screen at once instead of at the next stall tick. Ends when the
    CLI is past its start (its ready words are on screen), it was answered, or
    the seat died."""
    if sess is None or not hub_folder(cwd):
        return None

    def run():
        end = time.time() + WATCH_S
        while time.time() < end:
            time.sleep(_POLL_S)
            try:
                if not sess.alive():
                    return
                flat = squash(sess.tail(lines=80, max_bytes=32768))
            except Exception:
                return
            hit = _find_in(flat)
            if hit is None:
                continue
            if not hit["answerable"]:
                return                  # attention reports it
            time.sleep(0.5)             # let the screen finish drawing
            if answer(sess, cwd, log):
                return

    t = threading.Thread(target=run, name="first-run-watch", daemon=True)
    t.start()
    return t


def look(rid: str, now: float) -> str:
    """The stall tick's backstop: every running seat of a task stopped on a
    trust screen in a hub folder gets it answered (a seat started by a path
    without ``watch``, or one the watch missed). Twice per seat at most."""
    cr = _d.chatroom
    # Read, never changed: the indexed room, without its messages (#228).
    room = cr.get_room(rid, public=False, copy_room=False, messages=False)
    if not room or not room.get("launched", True):
        return ""
    done = []
    for part in cr.agent_participants(room):
        sess = _d.rotation._pty(part)
        if sess is None:
            continue
        try:
            hit = find(sess.tail(lines=80, max_bytes=32768))
        except Exception:
            continue
        if not hit or not hit["answerable"] or answered(sess):
            continue
        key = (rid, part.get("ptyId") or "")
        if _TRIED.get(key, 0) >= 2:
            continue
        _TRIED[key] = _TRIED.get(key, 0) + 1
        cwd = part.get("cwd") or getattr(sess, "cwd", "") or (sess.info() or {}).get("cwd", "")
        if answer(sess, cwd, _log):
            done.append(f"{part.get('identity', '')}: answered {hit['name']}")
    if len(_TRIED) > 500:
        _TRIED.clear()
    return "; ".join(done)


_TRIED: dict[tuple[str, str], int] = {}


def _log(msg: str) -> None:
    print(msg, flush=True)
