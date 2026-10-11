"""Read plan tiers locally and expose only labels and relative weights.

Credential contents and JWTs are never returned, logged, or persisted. The
Codex JWT payload is decoded only to identify the local subscription tier; no
signature check or authentication decision is made here.
"""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _claude(path: Path) -> dict:
    oauth = _json_file(path).get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return {"weight": None, "label": "Claude plan unknown"}
    subscription = str(oauth.get("subscriptionType") or "").lower()
    tier = str(oauth.get("rateLimitTier") or "").lower()
    if "max_20x" in tier:
        return {"weight": 20, "label": "Claude Max 20x"}
    if "max_5x" in tier:
        return {"weight": 5, "label": "Claude Max 5x"}
    if subscription == "pro":
        return {"weight": 1, "label": "Claude Pro"}
    return {"weight": None, "label": "Claude plan unknown"}


def _codex(path: Path) -> dict:
    tokens = _json_file(path).get("tokens")
    token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str):
        return {"weight": None, "label": "Codex plan unknown"}
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
        plan = claims["https://api.openai.com/auth"]["chatgpt_plan_type"]
    except (IndexError, KeyError, TypeError, UnicodeError, ValueError, binascii.Error):
        return {"weight": None, "label": "Codex plan unknown"}
    return {
        "plus": {"weight": 1, "label": "Codex Plus"},
        "prolite": {"weight": 5, "label": "Codex Pro 5x"},
        "pro": {"weight": 20, "label": "Codex Pro 20x"},
    }.get(str(plan).lower(), {"weight": None, "label": "Codex plan unknown"})


def detect(claude_path: Path | None = None, codex_path: Path | None = None) -> dict:
    """Return safe plan metadata; a missing or malformed tier has no weight."""
    home = Path.home()
    return {
        "claude": _claude(claude_path or home / ".claude" / ".credentials.json"),
        "codex": _codex(codex_path or home / ".codex" / "auth.json"),
    }
