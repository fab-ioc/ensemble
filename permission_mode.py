"""Whether a hub-launched Claude agent runs in the permission mode the hub
launched it with, and whether Claude's own safety check stopped it (ED-216,
GitHub issue 17).

Every launch passes ``--permission-mode`` (``backends.shared.claude_cmd_args``,
``bypassPermissions`` unless ``ENSEMBLE_PERMISSION_MODE`` says otherwise). Two
things can still stop an agent, and launch flags change neither:

* **Claude's safety check** (the "auto mode classifier"). Claude Code routes
  browser tools (Claude in Chrome, its own browser and preview) to it even in
  bypass mode while its *Chrome classifier floor* is on; Anthropic turns the
  floor on remotely. A denial comes back as the tool's result: "Permission for
  this action was denied by the Claude Code auto mode classifier. Reason: …".
  The words made an agent conclude the hub had started it in auto mode (issue
  17); it had not. The floor stays on (PO ruling 2026-10-12): a person does
  that step.
* **A mode other than the launch's.** Claude settings (the user's or a managed
  policy) with ``permissions.disableBypassPermissionsMode: "disable"`` start
  it in the settings' ``defaultMode`` instead; without consent to bypass mode
  it starts in Manual mode, or an interactive one shows the bypass warning and
  waits for "Yes, I accept". The hub never writes those settings.

This module reads both from the agent's transcript (the ``permission-mode``
records and each user entry's ``permissionMode``, and the denial results), its
hooks (``permission_mode`` on every event, ``PermissionDenied``) and its
screen (the footer's "… on" line, the bypass warning), and turns them into one
:func:`problem` that ``attention`` shows and ``stall`` tells the PO once.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from backends.shared import PERMISSION_MODE

_d = None  # the dashboard module, set by bind()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


# The mode the hub launches every Claude agent in ("" = no flag passed).
LAUNCH_MODE = PERMISSION_MODE

# How a mode is named to a person.
MODE_NAMES = {"bypassPermissions": "bypass", "auto": "auto", "default": "Manual",
              "acceptEdits": "accept-edits", "plan": "plan", "dontAsk": "don't-ask"}
# Modes an agent enters and leaves by itself (plan mode, through its own
# tools): never a mismatch.
_OWN_MODES = ("plan",)

# What Claude Code puts in the tool result of a denied call.
DENIAL_PREFIXES = ("Permission for this action was denied by the Claude Code auto mode classifier.",
                   "Permission for this action has been denied.")
_REASON = re.compile(r"Reason:\s*\[?([^\].\n]{1,80})")

# The prompts that settle a denial: a person, or a PO, typed to the agent.
_SETTLING_KINDS = ("human", "po", "pomsg")
# A denial older than this is history, not news.
DENIAL_MAX_AGE = 24 * 3600

_TAIL_BYTES = 256 * 1024
_HEAD_BYTES = 64 * 1024

# The footer's mode line, and the warning that waits for consent to bypass mode.
_FOOTER = (("bypass permissions on", "bypassPermissions"), ("auto mode on", "auto"),
           ("accept edits on", "acceptEdits"), ("plan mode on", "plan"),
           ("don't ask on", "dontAsk"), ("don’t ask on", "dontAsk"))
_CONSENT = re.compile(r"running\s*in\s*bypass\s*permissions\s*mode", re.I)
_CONSENT_CHOICE = re.compile(r"yes,?\s*i\s*accept", re.I)


def mode_name(mode: str) -> str:
    return MODE_NAMES.get(mode or "", mode or "")


# ---------------------------------------------------------------------------
# The transcript
# ---------------------------------------------------------------------------

def _read(path: Path, start: int, size: int) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(start)
            raw = f.read(size)
    except OSError:
        return []
    lines = raw.decode("utf-8", "replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]               # the first line is cut
    return lines


def _content(d: dict) -> list:
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    c = msg.get("content")
    return c if isinstance(c, list) else ([{"type": "text", "text": c}] if isinstance(c, str) else [])


def _result_text(block: dict) -> str:
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(str(b.get("text") or "") for b in c if isinstance(b, dict))
    return ""


def denial_of(text: str) -> str | None:
    """The reason a denied tool call's result gives, "" when it gives none,
    None when the text is not a denial."""
    text = (text or "").lstrip()
    if not text.startswith(DENIAL_PREFIXES):
        return None
    m = _REASON.search(text[:400])
    return " ".join(m.group(1).split()) if m else ""


def _ts(s) -> float:
    try:
        import datetime as _dt
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _prompt_kind(text: str) -> str:
    if _d is not None and hasattr(_d, "hub_input_kind"):
        try:
            return _d.hub_input_kind(text).get("kind") or "human"
        except Exception:
            return "human"
    return "human"


def parse(lines: list[str]) -> dict:
    """``{mode, denial}`` from transcript lines, oldest first. ``mode`` is the
    newest mode recorded; ``denial`` the newest denied tool call that no later
    prompt of a person (or a PO) has settled — ``{tool, reason, at,
    toolUseId}`` — or None."""
    mode, denial = "", None
    tools: dict[str, str] = {}
    for ln in lines:
        if "permission" not in ln.lower() and '"tool_use"' not in ln and '"user"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("isSidechain"):
            continue
        kind = d.get("type")
        if kind == "permission-mode":
            mode = d.get("permissionMode") or mode
            continue
        if kind == "assistant":
            for b in _content(d):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    tools[b["id"]] = b.get("name") or ""
            continue
        if kind != "user":
            continue
        if isinstance(d.get("permissionMode"), str) and d["permissionMode"]:
            mode = d["permissionMode"]
        at = _ts(d.get("timestamp"))
        for b in _content(d):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                reason = denial_of(_result_text(b))
                if reason is not None:
                    tid = b.get("tool_use_id") or ""
                    denial = {"tool": tools.get(tid, ""), "reason": reason, "at": at,
                              "toolUseId": tid}
            elif b.get("type") == "text" and denial and not d.get("isMeta") \
                    and _prompt_kind(str(b.get("text") or "")) in _SETTLING_KINDS:
                denial = None           # a person (or the PO) took it from there
    return {"mode": mode, "denial": denial}


_SEEN: dict[str, tuple[tuple, dict]] = {}    # path -> ((mtime, size), parsed)
_HEAD: dict[str, str] = {}                   # path -> the mode its start recorded


def from_transcript(path) -> dict:
    """:func:`parse` of a conversation's newest part (its start too, when the
    newest part records no mode). Read again only when the file changed."""
    if not path:
        return {"mode": "", "denial": None}
    p = Path(path)
    try:
        st = p.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return {"mode": "", "denial": None}
    seen = _SEEN.get(str(p))
    if seen and seen[0] == key:
        return dict(seen[1])
    out = parse(_read(p, max(0, st.st_size - _TAIL_BYTES), _TAIL_BYTES))
    if not out["mode"] and st.st_size > _TAIL_BYTES:
        if str(p) not in _HEAD:
            _HEAD[str(p)] = parse(_read(p, 0, _HEAD_BYTES))["mode"]
        out["mode"] = _HEAD[str(p)]
    if len(_SEEN) > 500:
        _SEEN.clear()
        _HEAD.clear()
    _SEEN[str(p)] = (key, out)
    return dict(out)


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------

def from_screen(tail: str) -> dict:
    """``{mode, consent}`` from the end of an agent's screen: the mode its
    footer names ("" when none: Manual mode shows no "… on" line, and so does
    a screen mid-redraw) and whether the bypass warning is up."""
    lines = [ln for ln in (tail or "").splitlines() if ln.strip()]
    foot = " ".join(lines[-4:]).lower()
    mode = next((m for words, m in _FOOTER if words in foot), "")
    last = "\n".join(lines[-30:])
    consent = bool(_CONSENT.search(last) and _CONSENT_CHOICE.search(last))
    return {"mode": mode, "consent": consent}


# ---------------------------------------------------------------------------
# What it amounts to
# ---------------------------------------------------------------------------

_BROWSER = re.compile(r"chrome|browser|preview", re.I)
_ACTIONS = (("navigate", "opening a page"), ("javascript", "running a script in a page"),
            ("file_upload", "a file upload"), ("upload", "an upload"),
            ("form_input", "filling in a form"), ("tabs_create", "opening a tab"),
            ("tabs_context", "reading its tabs"), ("select_browser", "choosing a browser"),
            ("computer", "a click or key press"), ("browser_batch", "clicks and key presses"),
            ("find", "a search in a page"), ("shortcut", "a shortcut"))


def action_words(tool: str) -> str:
    """What a denied tool call did, in plain words."""
    tool = tool or ""
    short = tool.rsplit("__", 1)[-1]
    if _BROWSER.search(tool):
        what = next((w for k, w in _ACTIONS if k in short.lower()), short or "an action")
        return f"a browser action ({what})"
    return f"an action ({short})" if short else "an action"


def problem(part: dict, hook: dict | None = None, tail: str = "",
            transcript: dict | None = None) -> dict | None:
    """What stops a Claude agent from running as the hub launched it, or None:
    ``{cause, why, key, ...}``. ``cause`` is ``consent`` (the bypass warning
    waits on its screen), ``mode`` (it runs in another mode) or ``denied``
    (Claude's safety check refused a tool call that nobody has taken up since).
    ``why`` completes "<agent> …"; ``key`` names this one problem, so it is
    told once. ``hook`` is ``agent_hooks.state_for``, ``tail`` its screen."""
    if (part.get("agent") or "claude") != "claude":
        return None
    screen = from_screen(tail) if tail else {"mode": "", "consent": False}
    if screen["consent"]:
        return {"cause": "consent", "key": "consent",
                "why": ("is held at Claude's bypass mode warning on its screen: Claude on this "
                        "machine has not been told bypass mode is accepted. A person has to "
                        "answer it in the agent's terminal.")}
    if transcript is None:
        try:
            transcript = from_transcript(_d.rotation._transcript_of(part)[0]) if _d is not None \
                else {"mode": "", "denial": None}
        except Exception:
            transcript = {"mode": "", "denial": None}
    mode = (hook or {}).get("mode") or transcript.get("mode") or screen["mode"]
    if LAUNCH_MODE and mode and mode != LAUNCH_MODE and mode not in _OWN_MODES:
        launched = mode_name(LAUNCH_MODE)
        if mode == "default":
            why = (f"runs in Manual mode, not in {launched} mode as the hub launched it: Claude "
                   f"on this machine has bypass mode turned off or not accepted, so it stops "
                   f"for approval.")
        else:
            why = (f"runs in {mode_name(mode)} mode, not in {launched} mode as the hub launched "
                   f"it: Claude's settings on this machine may turn {launched} mode off, or "
                   f"someone switched it. Claude's safety check may stop it.")
        return {"cause": "mode", "key": f"mode:{mode}", "mode": mode, "why": why}
    den = transcript.get("denial")
    if den and den.get("at") and time.time() - float(den["at"]) > DENIAL_MAX_AGE:
        den = None
    if den:
        reason = f" (Claude's reason: {den['reason']})" if den.get("reason") else ""
        bypass = (f"; the agent runs in {mode_name(LAUNCH_MODE)} mode"
                  if LAUNCH_MODE == "bypassPermissions" else "")
        return {"cause": "denied", "key": f"denied:{den.get('toolUseId') or den.get('at')}",
                "at": float(den.get("at") or 0), "tool": den.get("tool") or "",
                "why": (f"was stopped by Claude's own safety check: it blocked "
                        f"{action_words(den.get('tool') or '')}{reason}{bypass}. "
                        f"A person has to do this step.")}
    return None
