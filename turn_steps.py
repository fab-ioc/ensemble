"""A PO's mid-turn lines folded into the reply that ends the turn (#402, issue 61).

What the PO writes between two tool calls ("Merging #395 now.", "Checking the
hub log") is in its transcript as a turn of its own, marked ``interim`` by the
parser (dashboard._claude_text_turns: the text's stop reason is ``tool_use``).
In the PO chat each was a message of its own. Here they become the *steps* of
the message that ends the turn: ``turn.steps = [{mid, text, timestamp}]``,
shown behind "N steps" on the page. The newest steps of a turn still running
(the *open tail*) are what the page shows as the chat's live status line.

Pure: the balloons (threads.balloons recs) go in, new lists come out; a rec
and its turn are copied before they change (threads._BAL caches them).

A step stays a message of its own (*kept*) when it is something the person
must be able to find and answer in the chat: a ``Re Pn`` answer, a question
(Ask: or a card's), a link, a URL, an image or file line, a file path or
name (absolute, relative or bare: README.md), or a balloon the person
started a thread on. A task number (#395) does not keep it: "Merging #395"
is a step.

Nothing is ever lost: a run of steps nothing ended (a crash, a restart, a hub
line typed into the turn) becomes a message itself, its last step the text,
the earlier ones its steps.
"""
from __future__ import annotations

import re

try:
    from message_refs import PREFIXES as _REF_PREFIXES
except Exception:       # noqa: BLE001 — a unit test without the module
    _REF_PREFIXES = ("[image] ", "[file] ")

_LINK = re.compile(r"\]\(|https?://", re.I)
# An absolute path ending in a file name with an extension: /a/b.png,
# ~/x/y.md, /home/fab/Ensemble Projects/Docs/#402 plan.html (spaces inside).
_PATH = re.compile(r"(?:^|[\s(\[`'\"])(?:~|/)[^\n`'\"()<>]*?/[^\n/`'\"()<>]+?\.[A-Za-z0-9]{1,8}\b")
# A relative file name as the page links it (session.html LINK_RE): a path
# with a folder (src/report.md, ./a/b.py), or a bare name with an extension
# agents write about (README.md, shot.png). An extension holds a letter, so
# "0.45/0.53", "3/4.5x" or "v1.2" is no file; nor are "e.g." and "#402".
_BARE_EXT = ("md|txt|py|js|ts|tsx|jsx|html|css|json|yaml|yml|toml|csv|pdf|sh|ps1|sql|xml|log|ini|cfg"
             "|png|jpg|jpeg|gif|webp|svg|bmp")
_REL = re.compile(r"(?:^|[\s(*_`'\"])(?:(?:\.\.?/)?(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]\w{0,7}"
                  r"|\w[\w-]*\.(?:" + _BARE_EXT + r"))(?::\d+)?(?=[\s.,;:)\]`'\"]|$)", re.I)


def kept(rec: dict, chosen: set[str] | frozenset = frozenset()) -> bool:
    """Whether a mid-turn line stays a message of its own."""
    if rec.get("re") or rec.get("ask") or rec.get("mid") in chosen:
        return True
    text = (rec.get("turn") or {}).get("text") or ""
    if _LINK.search(text) or _PATH.search(text) or _REL.search(text):
        return True
    return any(line.lstrip().startswith(_REF_PREFIXES) for line in text.splitlines())


def is_step(rec: dict, chosen: set[str] | frozenset = frozenset()) -> bool:
    """An agent's mid-turn line that folds into the reply ending its turn."""
    return rec.get("role") == "agent" and bool((rec.get("turn") or {}).get("interim")) \
        and not kept(rec, chosen)


def _step(rec: dict) -> dict:
    t = rec.get("turn") or {}
    return {"mid": rec["mid"], "text": t.get("text") or "", "timestamp": t.get("timestamp") or ""}


def _owner(rec: dict, run: list[dict]) -> dict:
    """``rec`` with ``run`` as its steps (both copied)."""
    turn = {**(rec.get("turn") or {}), "steps": [_step(r) for r in run]}
    return {**rec, "turn": turn}


def fold(recs: list[dict], current_sid: str = "", chosen=frozenset()) -> tuple[list[dict], dict, str]:
    """The balloons with every agent's mid-turn lines folded in.

    Returns ``(recs, step_of, open_mid)``:

    - ``recs``: the balloons in order; an owner is a copy whose ``turn`` has
      ``steps``; a folded line is not in it;
    - ``step_of``: ``{step mid: its owner's mid}``, for a link or a search
      that lands on a step;
    - ``open_mid``: the owner of a run nothing ended yet at the very end of
      the session ``current_sid`` (the PO's turn still running, or one that
      stopped without its last word); "" when there is none. The page shows
      it as the live line only while the PO is working.

    A session change and every person's or hub's line end a run.
    """
    chosen = frozenset(chosen or ())
    out: list[dict] = []
    step_of: dict[str, str] = {}
    unended: set[str] = set()     # owners of a run nothing ended
    run: list[dict] = []

    def flush() -> None:
        nonlocal run
        if not run:
            return
        owner = _owner(run[-1], run[:-1]) if len(run) > 1 else run[-1]
        for r in run[:-1]:
            step_of[r["mid"]] = owner["mid"]
        out.append(owner)
        unended.add(owner["mid"])
        run = []

    sid = None
    for rec in recs:
        if sid is not None and rec.get("sid") != sid:
            flush()
        sid = rec.get("sid")
        if rec.get("role") != "agent":
            flush()
            out.append(rec)
            continue
        if is_step(rec, chosen):
            run.append(rec)
            continue
        if run:
            for r in run:
                step_of[r["mid"]] = rec["mid"]
            out.append(_owner(rec, run))
            run = []
        else:
            out.append(rec)
    flush()
    # The open tail is the last thing the current session says, and a run
    # nothing ended: a session read after it (a handover) does not end it.
    last_own = next((r for r in reversed(out) if r.get("sid") == current_sid), None) \
        if current_sid else None
    open_mid = last_own["mid"] if last_own is not None and last_own["mid"] in unended else ""
    return out, step_of, open_mid


def chosen_mids(side: dict) -> set[str]:
    """The balloons the person started a thread on (``m:<mid>`` threads in
    the chat's record): such a line stays a message, its thread's parent."""
    tids: set[str] = set()
    for k in ("names", "archived", "deleted", "seen"):
        tids |= {t for t in (side.get(k) or {}) if isinstance(t, str)}
    tids |= {str(v or "") for v in (side.get("anchors") or {}).values()}
    tids |= {str(a.get("thread") or "") for a in side.get("sent") or [] if isinstance(a, dict)}
    return {t[2:] for t in tids if t.startswith("m:")}
