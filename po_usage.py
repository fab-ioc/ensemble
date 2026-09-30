"""A short, cache-only usage head for the PO's live context."""
import time

import usage


def head(snapshot=None, now=None):
    now = time.time() if now is None else now
    try:
        snap = usage.snapshot() if snapshot is None else snapshot
        checked = float(snap.get("checkedAt") or 0)
        if not checked or now - checked > 900:
            return "usage unknown"
        parts = []
        for kind, name in (("claude", "Claude"), ("codex", "Codex")):
            source = next((s for s in snap.get("sources", []) if s.get("source") == kind), {})
            windows = []
            for w in source.get("windows", []):
                if kind == "claude" and w.get("model"):
                    continue
                age = w.get("ageSeconds", source.get("ageSeconds"))
                fresh = (source.get("state") == "ok" and age is not None
                         and float(age) + max(0, now - checked) <= 900
                         and w.get("trusted") and w.get("percent") is not None
                         and not w.get("rolledOver") and not w.get("resetUnknown"))
                value = f"{w['percent']:g}%" if fresh else "unknown"
                minutes = w.get("windowMinutes")
                period = "5h" if minutes == 300 else "7d" if minutes == 10080 else w.get("label", "window")
                pool = w.get("pool") or "main"
                if pool == usage.CODEX_MAIN_POOL:
                    pool = "main"
                elif pool == usage.CODEX_RESERVE_POOL:
                    pool = "reserve"
                windows.append(f"{pool + ' ' if kind == 'codex' else ''}{period} {value}")
            parts.append(f"{name} " + (" · ".join(windows) if windows else "unknown"))
        return "usage: " + " | ".join(parts)
    except (TypeError, ValueError, KeyError, AttributeError):
        return "usage unknown"
