"""The hub's own Settings in Ask the hub (#410, P439): which settings the
chat may change and how each one reads, the proposal's check (the Settings
save's own validation, run without writing), Apply, and the history of
applied changes.

**The settings** are named by a path into ``settings.json``
(``reviewRoundsPerTask``, ``agentModels.claude.model``,
``poFallbackModels.claude``). ``TABLE`` holds the ones the chat may change,
each with its name and place as Settings shows them; ``REFUSED`` the ones it
may not, each with the one line it says when asked. Any other key is refused
too.

**The save** is the dashboard's own (``PUT /api/settings``): the dashboard
binds ``HUB`` at import (it imports the hub chat, so the hub chat cannot
import it): ``load`` (load_settings), ``merge`` (the save's merge step, no
write), ``save`` (save_settings), ``models_error`` (agent_models_error),
``lock`` (the settings lock, reentrant) and ``editors`` (the editor ids and
names).

**Stale**: a proposal keeps each setting's value as it was (``before``,
raw). It is stale once one of THOSE settings has changed; a change to any
other setting does not matter.

**The history**: ``<data folder>/settings-history.json`` (``{"changes":
[...]}``, the last ``HISTORY_KEPT``), one entry per applied change: ``{at,
ask, text, changes: [{key, name, before, after}]}`` with the values as
shown. Written only by an Apply: a hub that never applied one has no file,
and nothing of it is under ``outside/``.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import durable

HISTORY_NAME = "settings-history.json"
HISTORY_KEPT = 200

HUB: dict = {}          # bound by dashboard.py; tests bind their own

_HISTORY_LOCK = threading.Lock()


class Changed(Exception):
    """A setting the proposal changes was changed since it was made."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


# ---------------------------------------------------------------------------
# How a value reads
# ---------------------------------------------------------------------------

def _int(v):
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(f) if f == f and f not in (float("inf"), float("-inf")) else None


def _num(v):
    i = _int(v)
    if i is None:
        return None
    f = float(v)
    return i if f == i else round(f, 1)


def _thousands(n) -> str:
    return f"{n:,}"


def _off_or(fmt, off="off"):
    def show(v):
        n = _num(v)
        if n is None:
            return str(v)
        return off if n == 0 else fmt(n)
    return show


def _unit(word: str, one: str | None = None):
    def show(v):
        n = _num(v)
        if n is None:
            return str(v)
        return f"{_thousands(n) if isinstance(n, int) else n} {one if n == 1 and one else word}"
    return show


def _choice(labels: dict):
    return lambda v: labels.get(v, str(v))


def _onoff(v) -> str:
    return "on" if v else "off"


def _text(empty: str):
    return lambda v: f"“{v}”" if isinstance(v, str) and v.strip() else empty


def _noun(v) -> str:
    if isinstance(v, dict) and v.get("one") and v.get("many"):
        return f"{v['one']} / {v['many']}"
    return str(v)


def _editor(v) -> str:
    if v == "":
        return "Automatic"
    return dict(HUB.get("editors") or ()).get(v, str(v))


THEMES = {"light": "Light", "dark": "Dark", "dim": "Dim", "paper": "Paper", "contrast": "Contrast",
          "fjord": "Fjord", "intellij-dark": "IntelliJ Dark", "system": "System"}

AM = "Settings › Agent models"
NO_ROW = "No row in Settings yet"

# key -> name, where, show(value), allowed (in words, for the model)
TABLE: dict[str, dict] = {}


def _row(key, name, where, show, allowed):
    TABLE[key] = {"name": name, "where": where, "show": show, "allowed": allowed}


_row("preferAgent", "Prefer", f"{AM} › Which agent",
     _choice({"claude": "Claude", "codex": "Codex", "balanced": "Balanced"}),
     '"claude", "codex" or "balanced": the kind every seat (new tasks, reviews, handovers, PO switches) '
     'goes to while it is usable; balanced moves work to the kind with more room. No setting picks '
     'the reviewers alone')
_row("agentModels.claude.model", "Claude model", f"{AM} › Models", _text("Claude's own default"),
     '"" (Claude\'s own default), "fable", "opus", "sonnet", "haiku", or a full model id "claude-..."')
_row("agentModels.codex.effort", "Codex reasoning effort", f"{AM} › Models", _text("Codex's own default"),
     '"" (Codex\'s own default) or one of the efforts the Codex model takes: {efforts}')
_row("reviewRoundsPerTask", "Review rounds per task", f"{AM} › Reviews",
     _off_or(_unit("rounds", "round"), "no limit"),
     "a whole number of rounds, 0 = no limit (a task's own limit set by its PO still wins)")
_row("taskRotateTokens", "Hand a task session over at", f"{AM} › Handover",
     _off_or(_unit("tokens")), "tokens of context per model call, 0 = off")
_row("poRotateTokens", "Hand the PO over at", f"{AM} › Handover",
     _off_or(_unit("tokens")), "tokens of context per model call, 0 = off")
_row("idleAlertMin", "Tell the PO a task is idle after", f"{AM} › Idle tasks",
     _off_or(_unit("minutes", "minute")), "minutes, at least 5, at most 1440; 0 = off")
_row("paceWeek", "Pace each agent's week", f"{AM} › Weekly pacing", _onoff, "true or false")
_row("paceMarginPoints", "Allowance above pace", f"{AM} › Weekly pacing",
     _unit("points", "point"), "percentage points, 0 to 40")
_row("paceBrake", "When both plans run too fast", f"{AM} › Weekly pacing",
     _choice({"off": "Off", "tell": "Tell only", "hold": "Hold new work"}), '"off", "tell" or "hold"')
_row("balanceHysteresisPercent", "Switch hysteresis", f"{AM} › Weekly pacing",
     lambda v: f"{_num(v)}%" if _num(v) is not None else str(v), "a percent, 0 to 100")
for _k, _n in (("claudePlanWeight", "Claude"), ("codexPlanWeight", "Codex")):
    _row(_k, f"{_n} weight override", f"{AM} › Weekly pacing",
         lambda v: "detected" if v in ("", None) else _unit("units", "unit")(v),
         '"" (use the detected plan) or units, 1 to 1000')
_row("poFallbackModels.claude", "The Claude model a PO switched to Claude starts on", NO_ROW,
     _text("the one chosen in Settings"), '"" or a Claude model ("opus", "sonnet", ...), used only '
     'while Settings chooses no Claude model')

_row("operatorNickname", "What the agents call you", "Settings › Preferences › You",
     _text("your git user name"), 'a short name, at most 60 characters; "" = your git user name')
_row("attentionStallSeconds", "Call an agent stalled after", "Settings › Preferences › Stalls and restarts",
     _unit("seconds", "second"), "seconds, 60 to 86400")
_row("restartDrainMinutes", "Restart anyway after", "Settings › Preferences › Stalls and restarts",
     _unit("minutes", "minute"), "minutes, 1 to 240")
_row("rtkForTasks", "RTK for task agents", "Settings › Preferences › Command output", _onoff, "true or false")
_row("readerForTasks", "Cheap reader for task owners", "Settings › Preferences › Command output",
     _onoff, "true or false")
_row("projectNoun", "Call a board", "Settings › Preferences › Words", _noun,
     '{"one": "Project", "many": "Projects"} (or Initiative, Epic, Workstream, or other plain words)')

_row("digestIntervalMin", "How often the PO checks on progress", f"{NO_ROW} · a project's own value still wins",
     _off_or(lambda n: f"every {n} minute" + ("" if n == 1 else "s")), "minutes, 0 = off")
_row("digestModel", "The model the PO's progress check runs on", NO_ROW, _text("haiku"),
     'a Claude model ("haiku", "sonnet", ...)')
_row("pointsRemindMin", "Remind an agent of your open point after", NO_ROW,
     _off_or(_unit("minutes", "minute")), "minutes, at least 5, at most 1440; 0 = off")
_row("pointsDeliverRemindMin", "Remind an agent of a point it has not said is live after", NO_ROW,
     _off_or(_unit("minutes", "minute")), "minutes after its task is Done, at least 5; 0 = off")
_row("codexToolOutputTokens", "What Codex task agents keep of one tool output", NO_ROW,
     _off_or(_unit("tokens"), "Codex's own (about 10,000 tokens)"), "tokens, 0 = Codex's own")
_row("readCapBytes", "Cut a file a Claude task agent reads whole when over", NO_ROW,
     _off_or(_unit("bytes")), "bytes, 0 = off")
_row("readCapLines", "Lines kept of a file cut short", NO_ROW, _unit("lines", "line"), "lines, at least 1")

AP = "Settings › Appearance"
_row("theme", "Theme", f"{AP} › Colours", lambda v: THEMES.get(v, "not chosen" if v == "" else str(v)),
     ", ".join(f'"{k}"' for k in THEMES))
_row("accent", "Accent", f"{AP} › Colours",
     lambda v: "the theme's own" if v == "default" else ("not chosen" if v == "" else str(v)),
     '"default" (the theme\'s own) or an opaque CSS colour: #rgb, #rrggbb, a colour name, rgb(...) or hsl(...)')
_row("chatLog", "Chat as a log", f"{AP} › Chat", _choice({"off": "Off", "on": "On"}), '"off" or "on"')
_row("poTeamActivity", "Team activity in the PO chat", f"{AP} › Chat",
     _choice({"shown": "Shown", "hidden": "Hidden"}), '"shown" or "hidden"')
_row("sendBox", "Send box", f"{AP} › Chat",
     _choice({"workspace": "One for the workspace", "pane": "One under each chat"}), '"workspace" or "pane"')
_row("projectIcons", "Project icons", f"{AP} › Lists", _choice({"flat": "Flat", "glossy": "Glossy"}),
     '"flat" or "glossy"')
_row("inboxHighlightSec", "Inbox highlight", f"{AP} › Inbox", _unit("seconds", "second"), "seconds, 1 to 30")

_row("pruneDoneAfterDays", "Remove Done tasks by themselves", "Settings › Disk space",
     _off_or(lambda n: f"after {n} day" + ("" if n == 1 else "s")), "days, 0 = off")
_row("backupEnabled", "Backup of your projects", "Settings › Backup", _onoff, "true or false")
_row("backupIntervalMin", "Back up every", "Settings › Backup", _unit("minutes", "minute"), "minutes, 5 to 1440")
_row("editor", "Open in editor opens", "Settings › Editor", _editor,
     '"" (Automatic) or one of: {editors}')


def _codex_model_line(_key: str, settings: dict) -> str:
    m = (((settings.get("agentModels") or {}).get("codex") or {}).get("model") or "")
    return (f"The Codex model stays {m}: Ask the hub cannot change it." if m else
            "The Codex model stays as Settings has it: Ask the hub cannot change it.")


# key (or prefix) -> the one line said when asked
REFUSED: dict = {
    "agentModels.codex.model": _codex_model_line,
    "poFallbackModels.codex": _codex_model_line,
    "defaultModel": "That is the Claude model's older name: ask for the Claude model instead.",
    "feedbackRepo": "Ask the hub cannot change where feedback goes; change it in Settings › Feedback.",
    "feedbackRelayUrl": "Ask the hub cannot change the feedback relay; change it in Settings › Feedback.",
    "backupRemote": "Ask the hub cannot change the backup remote (an address that can hold a password); "
                    "change it in Settings › Backup.",
    "updateSkipped": "Ask the hub cannot skip or unskip an update; use What's new.",
    "pinnedProjects": "Ask the hub cannot pin projects; pin them from a project list.",
    "taskAgentTools": "Ask the hub cannot change task agents' tools (an isolation boundary); change it in "
                      "Settings › Agent models.",
    "editorCommand": "Ask the hub cannot set a custom editor command (a command the hub runs); set it in "
                     "Settings › Editor.",
    "openMode": "Ask the hub cannot change how sessions open in iTerm; change it in Settings.",
}
EDITOR_CUSTOM = "custom"
_CUSTOM_EDITOR_LINE = ("Ask the hub cannot choose a custom editor (a command the hub runs); "
                       "choose it in Settings › Editor.")
NOT_SETTINGS_LINE = ("Ask the hub cannot change {what}: secrets and tokens, connector sign-in, the "
                     "tailnet serve setup, a project's own settings and anything that restarts the hub "
                     "stay where they are.")


def refusal(key: str, settings: dict | None = None) -> str:
    why = REFUSED.get(key)
    if why is None:
        return NOT_SETTINGS_LINE.format(what=f"“{key}”")
    return why(key, settings or {}) if callable(why) else why


# ---------------------------------------------------------------------------
# Values by path
# ---------------------------------------------------------------------------

def get(settings: dict, key: str):
    v = settings
    for part in key.split("."):
        if not isinstance(v, dict) or part not in v:
            return None
        v = v[part]
    return copy.deepcopy(v)


def payload(changes: dict) -> dict:
    """``{path: value}`` as the save takes it (nested for a dotted path)."""
    out: dict = {}
    for key, v in changes.items():
        parts = key.split(".")
        d = out
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = v
    return out


def show(key: str, v) -> str:
    try:
        return TABLE[key]["show"](v)
    except Exception:
        return str(v)


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# What the model reads
# ---------------------------------------------------------------------------

def prompt_text() -> tuple[str, str]:
    """(the settings the model may change, each with its value now; the
    ones it may not, each with its line)."""
    s = HUB["load"]()
    try:
        import agent_models
        codex = (s.get("agentModels") or {}).get("codex") or {}
        runs_on = codex.get("model") or agent_models.codex_own()["model"]
        efforts = ", ".join(f'"{e}"' for e in agent_models._efforts_of(agent_models.codex_models(), runs_on))
    except Exception:
        efforts = ""
    editors = ", ".join(f'"{i}" ({n})' for i, n in HUB.get("editors") or ())
    lines = []
    for key, row in TABLE.items():
        allowed = row["allowed"].replace("{efforts}", efforts or "(the list cannot be read now)")
        allowed = allowed.replace("{editors}", editors or "(none known)")
        lines.append(f"- {key}: {row['name']} ({row['where']}). Now: {json.dumps(get(s, key), ensure_ascii=False)} "
                     f"({show(key, get(s, key))}). Values: {allowed}.")
    refused = []
    for key in REFUSED:
        refused.append(f"- {key}: {refusal(key, s)}")
    refused.append(f'- editor "{EDITOR_CUSTOM}": {_CUSTOM_EDITOR_LINE}')
    refused.append("- anything else (secrets, tokens, connector sign-in, the tailnet serve setup, a "
                   "project's own settings, anything that restarts the hub): "
                   + NOT_SETTINGS_LINE.format(what="that"))
    return "\n".join(lines), "\n".join(refused)


# ---------------------------------------------------------------------------
# A proposal
# ---------------------------------------------------------------------------

def propose(asked: dict) -> dict:
    """The model's ``{path: value}`` checked as the save would take it:
    ``{changes: {path: value as saved}, before: {path: raw now}, diff:
    [{key, name, where, before, after}], problems: [lines], refused:
    [lines]}``. A key it may not change is refused with its line; a value
    the save would drop is a problem and left out; a value it would clamp
    is proposed as it would be saved, with a problem saying so."""
    with HUB["lock"]:
        now = HUB["load"]()
        refused, problems, keep = [], [], {}
        for key, v in asked.items():
            if key == "editor" and v == EDITOR_CUSTOM:
                refused.append(_CUSTOM_EDITOR_LINE)
            elif key not in TABLE:
                line = refusal(key, now)
                if line not in refused:
                    refused.append(line)
            else:
                keep[key] = v
        changes, before = {}, {}
        for key, v in keep.items():
            name = TABLE[key]["name"]
            if _not_claude(key, v):
                problems.append(f"{name}: {json.dumps(v, ensure_ascii=False)} is not a Claude model; "
                                f"it stays {show(key, get(now, key))}.")
                continue
            one = payload({key: v})
            why = HUB["models_error"](one)
            if why:
                problems.append(f"{name}: {why}")
                continue
            merged = HUB["merge"](one)
            saved, old = get(merged, key), get(now, key)
            if _same(saved, old):
                if not (_same(v, old) or _loosely_same(old, v)):
                    problems.append(f"{name}: Settings does not take {json.dumps(v, ensure_ascii=False)}; "
                                    f"it stays {show(key, old)}.")
                continue
            if not _same(saved, v) and not _loosely_same(saved, v):
                problems.append(f"{name}: {json.dumps(v, ensure_ascii=False)} is saved as {show(key, saved)}, "
                                f"what Settings allows.")
            changes[key], before[key] = saved, old
        diff = [{"key": k, "name": TABLE[k]["name"], "where": TABLE[k]["where"],
                 "before": show(k, before[k]), "after": show(k, changes[k])} for k in changes]
    return {"changes": changes, "before": before, "diff": diff, "problems": problems, "refused": refused}


_CLAUDE_ONLY = ("poFallbackModels.claude", "digestModel")


def _not_claude(key: str, v) -> bool:
    """A value for a setting that takes only a Claude model that is not one
    ("" is the default and allowed)."""
    if key not in _CLAUDE_ONLY or v == "":
        return False
    if not isinstance(v, str):
        return True
    import agent_models
    return agent_models._claude_model(v) is None


def _loosely_same(saved, v) -> bool:
    """The same value written another way: "5" for 5, 3.0 for 3, a name
    with spaces around it."""
    if isinstance(saved, bool) or isinstance(v, bool):
        return isinstance(saved, bool) and isinstance(v, bool) and saved == v
    if isinstance(saved, (int, float)) and not isinstance(v, (dict, list)):
        n = _num(v)
        return n is not None and float(n) == float(saved)
    if isinstance(saved, str) and isinstance(v, str):
        return saved.strip().lower() == v.strip().lower()
    return False


def stale_key(before: dict) -> str | None:
    """The first setting of a proposal that changed since it was made."""
    now = HUB["load"]()
    for key, old in (before or {}).items():
        if not _same(get(now, key), old):
            return key
    return None


def stale_line(key: str) -> str:
    name = TABLE.get(key, {}).get("name", key)
    now = HUB["load"]()
    return (f"{name} was changed to {show(key, get(now, key))} since this was made, so it can no "
            f"longer be applied as it is.")


def apply(changes: dict, before: dict, ask: str, text: str) -> dict:
    """Saved through the Settings save under its lock, after checking that
    none of the settings changed since (``Changed``) and that the models
    can be saved (``ValueError``). The history gets one entry. Returns
    ``{diff, at}``. Only settings the chat may change are saved."""
    changes = {k: v for k, v in (changes or {}).items() if k in TABLE}
    bad = next((k for k, v in changes.items() if _not_claude(k, v)), None)
    if bad:
        raise ValueError(f"{TABLE[bad]['name']}: {json.dumps(changes[bad], ensure_ascii=False)} "
                         f"is not a Claude model.")
    with HUB["lock"]:
        key = stale_key(before)
        if key:
            raise Changed(key)
        body = payload(changes)
        why = HUB["models_error"](body)
        if why:
            raise ValueError(why)
        saved = HUB["save"](body)
        at = time.time()
        rows = [{"key": k, "name": TABLE.get(k, {}).get("name", k), "before": show(k, before.get(k)),
                 "after": show(k, get(saved, k))} for k in changes]
        # Saved by now: a history that cannot be written does not undo it,
        # it is said beside the Applied (#410 review 1).
        try:
            _add_history({"at": at, "ask": ask, "text": text, "changes": rows})
            history_error = ""
        except Exception as e:
            history_error = f"Saved, but it could not be added to History ({type(e).__name__})."
    return {"diff": rows, "at": at, "historyError": history_error}


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------

def history_path() -> Path:
    return Path(HUB["dir"]()) / HISTORY_NAME


def history() -> list[dict]:
    try:
        d = json.loads(history_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = d.get("changes") if isinstance(d, dict) else None
    return [r for r in rows if isinstance(r, dict)][-HISTORY_KEPT:] if isinstance(rows, list) else []


def _add_history(entry: dict) -> None:
    with _HISTORY_LOCK:
        rows = (history() + [entry])[-HISTORY_KEPT:]
        durable.write_text(history_path(), json.dumps({"changes": rows}, ensure_ascii=False, indent=1))
