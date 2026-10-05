"""Measure Ensemble's IntelliJ Dark theme token contrast.

Run from the repository root:

    py tools/check_intellij_theme.py

The script reads the shipped CSS token blocks rather than repeating their
values. It exits non-zero if a text pair is below 4.5:1, a code token is below
6:1 on a normal dark ground, a state/tool graphic is below 3:1, or the added
and removed diff grounds are less than 25 RGB units apart.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = "intellij-dark"
DIFF_GROUND_MIN_RGB_DISTANCE = 25.0


def _block(text: str, selector: str) -> dict[str, str]:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    if not match:
        raise ValueError(f"missing CSS block: {selector}")
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", match.group(1))
    }


def tokens(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    base = _block(text, ":root")
    base.update(_block(text, f':root[data-theme="{THEME}"]'))
    return base


def theme_tokens(path: Path) -> dict[str, str]:
    return _block(path.read_text(encoding="utf-8"), f':root[data-theme="{THEME}"]')


def _painted_token(text: str, selector: str) -> str:
    """Return the custom property used by a selector's final background rule."""
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text)
    if not match:
        raise ValueError(f"missing painted-state selector: {selector}")
    backgrounds = re.findall(r"background\s*:\s*var\((--[a-z0-9-]+)\)\s*;", match.group(1))
    if not backgrounds:
        raise ValueError(f"missing background token on painted-state selector: {selector}")
    return backgrounds[-1]


def _rgb(value: str) -> tuple[float, float, float, float]:
    value = value.strip()
    if value.startswith("#"):
        raw = value[1:]
        if len(raw) in (3, 4):
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) not in (6, 8):
            raise ValueError(value)
        alpha = int(raw[6:8], 16) / 255 if len(raw) == 8 else 1.0
        return (*(int(raw[i : i + 2], 16) for i in (0, 2, 4)), alpha)
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value, re.I)
    if not match:
        raise ValueError(value)
    parts = [float(x.strip()) for x in match.group(1).split(",")]
    return (parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else 1.0)


def _over(fg: tuple[float, float, float, float], bg: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    alpha = fg[3] + bg[3] * (1 - fg[3])
    return tuple((fg[i] * fg[3] + bg[i] * bg[3] * (1 - fg[3])) / alpha for i in range(3)) + (alpha,)


def _mix(a: tuple[float, float, float, float], amount: float, b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(a[i] * amount + b[i] * (1 - amount) for i in range(4))


def colour(name: str, values: dict[str, str], over_name: str | None = None) -> tuple[float, float, float, float]:
    value = values[name] if name.startswith("--") else name
    seen: set[str] = set()
    while (match := re.fullmatch(r"var\((--[a-z0-9-]+)\)", value)):
        key = match.group(1)
        if key in seen:
            raise ValueError(f"cyclic token {key}")
        seen.add(key)
        value = values[key]
    mix = re.fullmatch(
        r"color-mix\(in srgb, var\((--[a-z0-9-]+)\)\s+([\d.]+%|var\(--[a-z0-9-]+\)),\s*var\((--[a-z0-9-]+)\)\)",
        value,
    )
    if mix:
        amount = mix.group(2)
        if amount.startswith("var("):
            amount = values[amount[4:-1]]
        result = _mix(colour(mix.group(1), values), float(amount.rstrip("%")) / 100, colour(mix.group(3), values))
    else:
        result = _rgb(value)
    if result[3] < 1:
        if not over_name:
            raise ValueError(f"{name} needs a ground")
        result = _over(result, colour(over_name, values))
    return result


def _luminance(value: tuple[float, float, float, float]) -> float:
    def channel(n: float) -> float:
        n /= 255
        return n / 12.92 if n <= 0.03928 else ((n + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value[i]) for i in range(3))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    lighter, darker = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _hex(value: tuple[float, float, float, float]) -> str:
    return "#" + "".join(f"{round(value[i]):02X}" for i in range(3))


def measure() -> dict[str, object]:
    page_path = ROOT / "index.html"
    page_text = page_path.read_text(encoding="utf-8")
    page = tokens(page_path)
    session = tokens(ROOT / "session.html")
    fileview = tokens(ROOT / "fileview.html")

    # The same theme token must paint the host page, conversation iframe and file view.
    page_theme = theme_tokens(ROOT / "index.html")
    session_theme = theme_tokens(ROOT / "session.html")
    file_theme = theme_tokens(ROOT / "fileview.html")
    shared = set(session_theme) & set(file_theme) & set(page_theme)
    drift = {
        key: {"index": page_theme[key], "session": session_theme[key], "fileview": file_theme[key]}
        for key in sorted(shared)
        if page_theme[key].replace(" ", "").lower() != session_theme[key].replace(" ", "").lower()
        or page_theme[key].replace(" ", "").lower() != file_theme[key].replace(" ", "").lower()
    }

    checks: list[dict[str, object]] = []

    def add(group: str, foreground: str, ground: str, minimum: float, values: dict[str, str] = page) -> None:
        fg = colour(foreground, values)
        bg = colour(ground, values)
        checks.append(
            {
                "group": group,
                "foreground": foreground,
                "ground": ground,
                "fg": _hex(fg),
                "bg": _hex(bg),
                "ratio": round(contrast(fg, bg), 2),
                "minimum": minimum,
            }
        )

    grounds = ("--bg", "--surface", "--surface-sunken", "--surface-overlay", "--hover")
    for foreground in ("--fg", "--fg-subtle", "--fg-muted"):
        for ground in grounds:
            add("text", foreground, ground, 4.5)
    for ground in (*grounds, "--selected-bg"):
        add("interaction", "--accent", ground, 4.5)
    add("interaction", "--accent-fg", "--accent", 4.5)

    for stem in ("neutral", "progress", "success", "warning", "danger", "discovery"):
        add("semantic", f"--c-{stem}-fg", f"--c-{stem}-bg", 4.5)
    add("identity", "--agent-claude-fg", "--agent-claude-bg", 4.5)
    add("identity", "--agent-codex-fg", "--agent-codex-bg", 4.5)
    add("match", "--match-fg", "--match-bg", 4.5)

    for foreground in (
        "--code-kw",
        "--code-str",
        "--code-num",
        "--code-fn",
        "--code-ty",
        "--code-attr",
        "--code-meta",
        "--code-tag",
        "--code-com",
    ):
        for ground in ("--bg", "--surface", "--surface-sunken"):
            add("code", foreground, ground, 4.5 if foreground == "--code-com" else 6.0)
        for ground in ("--diff-add-bg", "--diff-del-bg"):
            add("diff", foreground, ground, 4.5)

    # Read the task switcher's painted selectors, rather than assuming that a
    # semantic token with the right name is the one the interface uses.
    state_selectors = {
        "running": ".sw-st.working",
        "waiting": ".sw-st.warning",
        "blocked": ".sw-st.danger",
        "done": ".sw-st.success",
    }
    states = {label: _painted_token(page_text, selector) for label, selector in state_selectors.items()}
    state_rows = {}
    for label, token in states.items():
        value = colour(token, page)
        state_rows[label] = {
            "selector": state_selectors[label],
            "token": token,
            "colour": _hex(value),
            "on_surface": round(contrast(value, colour("--surface", page)), 2),
        }
    state_distance = min(
        math.dist(colour(a, page)[:3], colour(b, page)[:3])
        for i, a in enumerate(states.values())
        for b in list(states.values())[i + 1 :]
    )

    diff_add = colour("--diff-add-bg", page)
    diff_remove = colour("--diff-del-bg", page)
    diff_distance = math.dist(diff_add[:3], diff_remove[:3])
    diff_ground_separation = {
        "added": _hex(diff_add),
        "removed": _hex(diff_remove),
        "rgb_distance": round(diff_distance, 1),
        "minimum": DIFF_GROUND_MIN_RGB_DISTANCE,
        "passed": diff_distance >= DIFF_GROUND_MIN_RGB_DISTANCE,
    }

    for token in ("--tool-points", "--tool-changes", "--tool-workspace", "--tool-board", "--tool-spec", "--tool-list"):
        for ground in ("--surface", "--surface-sunken", "--hover"):
            add("tool", token, ground, 3.0)
        add("tool ink", "--tool-ink", token, 3.0)

    failed = [row for row in checks if row["ratio"] < row["minimum"]]
    return {
        "theme": THEME,
        "ground": _hex(colour("--bg", page)),
        "panels": _hex(colour("--surface", page)),
        "drift": drift,
        "checks": checks,
        "states": state_rows,
        "minimum_state_rgb_distance": round(state_distance, 1),
        "diff_ground_separation": diff_ground_separation,
        "minimum_ratio": min(row["ratio"] for row in checks),
        "failed": failed,
    }


def main() -> int:
    result = measure()
    print(json.dumps(result, indent=2))
    return 1 if result["drift"] or result["failed"] or not result["diff_ground_separation"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
