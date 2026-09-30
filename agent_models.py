"""The model a hub-launched agent runs on when its seat names none.

Settings hold one choice per agent kind (``agentModels``):

    {"claude": {"model": ""}, "codex": {"model": "", "effort": ""}}

"" is the agent's own default: no flag is passed and the agent decides (for
Codex, ``model`` and ``model_reasoning_effort`` in its ``config.toml``; for
Claude, ``model`` in its ``settings.json``). Anything else is passed as a flag
at launch. Neither agent's own files are ever written.

What can be chosen is what the agent offers: for Codex the models in its
``models_cache.json`` (the ones its own picker lists, and a model with a pool
of its own such as ``gpt-reserve``), each with the reasoning efforts it takes;
for Claude the aliases the hub knows. A Claude model id (``claude-...``) is
taken too, so a value saved by the older free-text setting stays valid.

No import of dashboard.py: the settings value is handed in.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path

import usage

KINDS = ("claude", "codex")
DEFAULT = {"claude": {"model": ""}, "codex": {"model": "", "effort": ""}}

# The aliases ``claude --model`` takes, newest family first.
CLAUDE_MODELS = (
    {"id": "fable", "name": "Fable"},
    {"id": "opus", "name": "Opus"},
    {"id": "sonnet", "name": "Sonnet"},
    {"id": "haiku", "name": "Haiku"},
)
_CLAUDE_ALIASES = {m["id"] for m in CLAUDE_MODELS}
_CONTEXT_SUFFIX = "[1m]"
_CLAUDE_ID_RE = re.compile(r"claude-[a-z0-9][a-z0-9.\-]{1,60}")

_CACHE_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "models": None}


def _codex_home() -> Path:
    home = os.environ.get("CODEX_HOME")
    return Path(home) if home else Path.home() / ".codex"


def codex_models(path=None) -> list[dict] | None:
    """The Codex models that can be chosen, in Codex's own order: ``{id, name,
    description, efforts, defaultEffort, pool}``. None when the model list
    cannot be read (no Codex, or it has not fetched one yet). Read again only
    when the file changes."""
    path = Path(path) if path is not None else _codex_home() / "models_cache.json"
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        if _CACHE["key"] == key:
            return copy.deepcopy(_CACHE["models"])
    try:
        listed = json.loads(path.read_text(encoding="utf-8")).get("models")
    except (OSError, ValueError, AttributeError):
        listed = None
    models = None
    if isinstance(listed, list):
        models = []
        for m in listed:
            slug = m.get("slug") if isinstance(m, dict) else None
            if not isinstance(slug, str) or not slug.strip():
                continue
            slug = slug.strip()
            pool = usage.codex_pool_for_model(None, slug)
            # What Codex's picker lists, and a model with an allowance of its
            # own: choosing it is how that allowance is spent.
            if m.get("visibility") != "list" and pool["id"] == usage.CODEX_MAIN_POOL:
                continue
            efforts = [e.get("effort") for e in m.get("supported_reasoning_levels") or []
                       if isinstance(e, dict) and isinstance(e.get("effort"), str)]
            models.append({
                "id": slug,
                "name": m.get("display_name") if isinstance(m.get("display_name"), str) else slug,
                "description": (m.get("description") or "").strip()
                               if isinstance(m.get("description"), str) else "",
                "efforts": efforts,
                "defaultEffort": m.get("default_reasoning_level")
                                 if isinstance(m.get("default_reasoning_level"), str) else "",
                "pool": pool["label"],
                "_priority": m.get("priority") if isinstance(m.get("priority"), (int, float)) else 1e9,
            })
        models.sort(key=lambda x: x.pop("_priority"))
    with _CACHE_LOCK:
        _CACHE.update(key=key, models=models)
    return copy.deepcopy(models)


def codex_own() -> dict:
    """What Codex runs on when a launch names nothing: ``{model, effort}`` from
    its ``config.toml`` ("" for what the file does not say)."""
    try:
        return {"model": usage.codex_config_model(),
                "effort": usage.codex_config_value("model_reasoning_effort")}
    except Exception:                                        # noqa: BLE001
        return {"model": "", "effort": ""}


def claude_own(path=None) -> dict:
    """What Claude runs on when a launch names nothing: ``{model}`` from its
    ``settings.json`` ("" when it names none: the plan's default)."""
    path = Path(path) if path is not None else Path.home() / ".claude" / "settings.json"
    try:
        model = json.loads(path.read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        model = None
    return {"model": model.strip() if isinstance(model, str) else ""}


def normalise(value, legacy_claude=None) -> dict:
    """A saved ``agentModels`` as the full shape, whatever was in the file.
    ``legacy_claude`` is the older ``defaultModel``: taken as Claude's model
    when the saved value does not say one."""
    out = copy.deepcopy(DEFAULT)
    value = value if isinstance(value, dict) else {}
    for kind, fields in out.items():
        saved = value.get(kind) if isinstance(value.get(kind), dict) else {}
        for name in fields:
            if isinstance(saved.get(name), str):
                fields[name] = saved[name].strip()[:80]
    said = isinstance(value.get("claude"), dict) and isinstance(value["claude"].get("model"), str)
    if not said and isinstance(legacy_claude, str):
        out["claude"]["model"] = legacy_claude.strip()[:80]
    return out


def _claude_model(text: str) -> str | None:
    """``text`` as a model ``claude --model`` takes, or None."""
    low = text.strip().lower()
    base = low[:-len(_CONTEXT_SUFFIX)] if low.endswith(_CONTEXT_SUFFIX) else low
    if base in _CLAUDE_ALIASES or _CLAUDE_ID_RE.fullmatch(base):
        return low
    return None


def _efforts_of(models: list[dict] | None, model: str) -> list[str]:
    """The reasoning efforts ``model`` takes; none for a model not listed."""
    found = next((m for m in models or [] if m["id"].lower() == model.lower()), None)
    return list(found["efforts"]) if found else []


def check(change, current, models="read") -> tuple[dict | None, str]:
    """``change`` (any part of the shape) laid over ``current``: the new value,
    or (None, why it is refused). A field left as it was saved is never
    refused, so an older value keeps working while another field is changed."""
    current = normalise(current)
    if not isinstance(change, dict) or any(
            kind not in DEFAULT or not isinstance(fields, dict)
            or any(name not in DEFAULT[kind] or not isinstance(v, str) or len(v) > 80
                   for name, v in fields.items())
            for kind, fields in change.items()):
        return None, "Expected {claude: {model}, codex: {model, effort}}, each a short text."
    new = copy.deepcopy(current)
    for kind, fields in change.items():
        for name, v in fields.items():
            new[kind][name] = v.strip()

    model = new["claude"]["model"]
    if model and model != current["claude"]["model"]:
        known = _claude_model(model)
        if known is None:
            return None, (f"Claude has no model “{model}”: choose "
                          f"{', '.join(m['id'] for m in CLAUDE_MODELS)} or a full model id "
                          f"(claude-…).")
        new["claude"]["model"] = known

    if models == "read":
        models = codex_models()
    model = new["codex"]["model"]
    if model and model != current["codex"]["model"]:
        if models is None:
            return None, "Codex's model list cannot be read, so a Codex model cannot be chosen."
        found = next((m for m in models if m["id"].lower() == model.lower()), None)
        if found is None:
            return None, f"Codex offers no model “{model}”."
        new["codex"]["model"] = model = found["id"]
    effort = new["codex"]["effort"]
    if effort:
        runs_on = model or codex_own()["model"]
        takes = _efforts_of(models, runs_on)
        named = "effort" in (change.get("codex") or {})
        model_changed = model != current["codex"]["model"]
        if effort not in takes and named and (model_changed or effort != current["codex"]["effort"]):
            return None, (f"{runs_on or 'Codex’s own default model'} does not take the reasoning "
                          f"effort “{effort}”" + (f": it takes {', '.join(takes)}." if takes else "."))
        if effort not in takes and model_changed:
            # Only the model was changed and the effort does not carry over to
            # it: back to Codex's own.
            new["codex"]["effort"] = ""
    return new, ""


def launch_choice(kind: str, seat_model: str, value, models="read") -> tuple[str, str]:
    """(model, reasoning effort) a hub launch of ``kind`` passes, "" for none.
    The seat's own model wins; else the one chosen in Settings. A chosen Codex
    model that Codex no longer lists is not passed (Codex would refuse to
    start): its own default runs instead. The effort is Codex's only, and only
    for a model known to take it."""
    chosen = normalise(value).get(kind) or {}
    seat_model = (seat_model or "").strip()
    if kind != "codex":
        return seat_model or chosen.get("model", ""), ""
    if not chosen.get("model") and not chosen.get("effort"):
        return seat_model, ""
    if models == "read":
        models = codex_models()
    model = seat_model
    if not model and chosen.get("model"):
        listed = models is None or any(m["id"] == chosen["model"] for m in models)
        model = chosen["model"] if listed else ""
    effort = chosen.get("effort", "")
    if effort and effort not in _efforts_of(models, model or codex_own()["model"]):
        effort = ""
    return model, effort


def describe(value) -> dict:
    """What Settings shows: per kind what is chosen, what can be, the agent's
    own default, and what a launch that names no model runs on."""
    chosen = normalise(value)
    models = codex_models()
    own = codex_own()
    model, effort = launch_choice("codex", "", chosen, models)
    runs_on = model or own["model"]
    known = next((m for m in models or [] if m["id"] == runs_on), None)
    pool = usage.codex_pool_for_model(None, runs_on)
    return {
        "claude": {
            "chosen": chosen["claude"],
            "own": claude_own(),
            "models": [dict(m) for m in CLAUDE_MODELS],
            "effective": {"model": chosen["claude"]["model"] or claude_own()["model"]},
        },
        "codex": {
            "chosen": chosen["codex"],
            "own": own,
            "models": models or [],
            # False: Codex's model list could not be read, so nothing can be chosen.
            "readable": models is not None,
            # The chosen model is no longer offered: Codex's own default runs.
            "gone": bool(chosen["codex"]["model"]) and not model,
            "effective": {
                "model": runs_on,
                "effort": effort or own["effort"] or (known or {}).get("defaultEffort", ""),
                "efforts": list((known or {}).get("efforts", [])),
                "pool": pool["label"],
            },
        },
    }
