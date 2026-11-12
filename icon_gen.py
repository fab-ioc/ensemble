"""An initiative's icon drawn by an AI model (#335): Generate on its settings.

The hub asks an agent CLI it already runs for four small SVG icons from the
project's name (and its roadmap's title, when it has one): ``claude -p`` on
haiku with no tools, else ``codex exec`` in a read-only sandbox in an empty
folder. No new key, no new service; neither present, the page says so.

The answer is untrusted. Each SVG is parsed (no DTD, no entities), kept only
when every element and attribute is on the allow-list below, and written out
again from the parsed tree, so nothing the model typed reaches the page as
text. The picked one is then saved through #329's own path
(project_icon.accept), which checks it once more."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

TIMEOUT_S = 60
COUNT = 4
MAX_SVG_BYTES = 8 * 1024
MAX_ELEMENTS = 200
MAX_DEPTH = 8
MAX_OUTPUT = 256 * 1024
CLAUDE_MODEL = "haiku"
SVG_NS = "http://www.w3.org/2000/svg"

ELEMENTS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
GEOMETRY = {"x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2",
            "d", "points", "viewbox"}
PAINT = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
         "stroke-dasharray", "stroke-dashoffset", "fill-rule", "clip-rule", "opacity", "fill-opacity",
         "stroke-opacity", "transform"}
ATTRS = GEOMETRY | PAINT
# A value may hold numbers, words, colours (#hex, rgb()), path letters and the
# transform functions; never a quote, an angle bracket, a backslash, a colon
# (javascript:, data:) or a semicolon.
_VALUE_RE = re.compile(r"^[A-Za-z0-9#.,%()+\-\s]*$")
_BAD_VALUE_RE = re.compile(r"url\s*\(|expression\s*\(|var\s*\(|attr\s*\(", re.I)
_FUNC_RE = re.compile(r"([A-Za-z-]+)\s*\(")
_FUNCS = {"rgb", "rgba", "hsl", "hsla", "matrix", "translate", "scale", "rotate", "skewx", "skewy"}

REFUSALS = {
    "no_cli": "Generating needs claude or codex on the hub, and neither is installed there.",
    "timeout": "did not answer within 60 s.",
    "failed": "stopped with an error.",
    "no_icons": "answered with no icon that could be used.",
    "busy": "Icons for this project are already being drawn.",
    "no_such_project": "No such project.",
}

_BUSY: set[str] = set()
_BUSY_LOCK = threading.Lock()


class Unsafe(Exception):
    pass


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _ok_value(v: str) -> bool:
    if len(v) > 4000 or not _VALUE_RE.match(v) or _BAD_VALUE_RE.search(v):
        return False
    return all(f.lower() in _FUNCS for f in _FUNC_RE.findall(v))


def sanitize(text: str) -> str:
    """One SVG written out again from its parsed tree, every element and
    attribute on the allow-list; Unsafe when anything is not."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise Unsafe("size")
    low = text.lower()
    if "<!doctype" in low or "<!entity" in low or "<?" in low or "<![cdata[" in low:
        raise Unsafe("dtd")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise Unsafe("parse")
    if _local(root.tag) != "svg" or (root.tag.startswith("{") and not root.tag.startswith("{" + SVG_NS + "}")):
        raise Unsafe("root")
    count = [0]

    def walk(el, depth: int) -> str:
        count[0] += 1
        if count[0] > MAX_ELEMENTS or depth > MAX_DEPTH:
            raise Unsafe("too_many")
        if not isinstance(el.tag, str):
            raise Unsafe("node")
        if el.tag.startswith("{") and not el.tag.startswith("{" + SVG_NS + "}"):
            raise Unsafe("namespace")
        tag = _local(el.tag)
        if tag not in ELEMENTS or (tag == "svg" and depth):
            raise Unsafe("element " + tag)
        if (el.text or "").strip() or (depth and (el.tail or "").strip()):
            raise Unsafe("text")
        attrs = []
        for k, v in el.attrib.items():
            if k.startswith("{"):
                raise Unsafe("namespaced attribute")
            name = k.lower()
            if name in ("xmlns", "version"):
                continue
            if name not in ATTRS or not _ok_value(v):
                raise Unsafe("attribute " + name)
            if tag == "svg" and name in ("width", "height", "x", "y"):
                continue
            attrs.append((k if name != "viewbox" else "viewBox", " ".join(v.split())))
        if tag == "svg":
            if not any(k == "viewBox" for k, _ in attrs):
                raise Unsafe("viewBox")
            attrs = [("xmlns", SVG_NS), ("width", "48"), ("height", "48")] + attrs
        inner = "".join(walk(c, depth + 1) for c in el)
        a = "".join(f" {k}={quoteattr(v)}" for k, v in attrs)
        return f"<{tag}{a}>{inner}</{tag}>" if inner else f"<{tag}{a}/>"

    return walk(root, 0)


_SVG_RE = re.compile(r"<svg\b.*?</svg\s*>", re.S | re.I)


def parse(output: str) -> list[str]:
    """The usable icons in a model's answer, sanitised, at most COUNT, no two alike."""
    out: list[str] = []
    for m in _SVG_RE.finditer((output or "")[:MAX_OUTPUT]):
        try:
            s = sanitize(m.group(0))
        except Unsafe:
            continue
        if s not in out:
            out.append(s)
        if len(out) >= COUNT:
            break
    return out


def about_text(roadmap_text: str) -> str:
    """What the project is about, from its roadmap: its first line of prose
    (its title is mostly "<name> — roadmap", and its first note often who
    owns it), cut at its first sentence, at most 200 characters."""
    for line in (roadmap_text or "").splitlines()[:80]:
        t = line.strip()
        if not t or t.startswith(("#", "|", "<", "```", "---")) or t.lower().startswith("owned by"):
            continue
        t = re.sub(r"[*_`]+", "", t.lstrip("-* >").strip())
        t = " ".join(t.split())
        m = re.match(r"(.{20,}?[.!?])(\s|$)", t)
        if m:
            t = m.group(1)
        return t if len(t) <= 200 else t[:199].rstrip() + "…"
    return ""


def prompt(name: str, about: str) -> str:
    name = " ".join((name or "").split())[:120]
    about = " ".join((about or "").split())[:200]
    what = f'a project called "{name}"' + (f', about: "{about}"' if about else "")
    return (f"Draw {COUNT} different icons for {what}. Each is one SVG: "
            'viewBox="0 0 48 48"; its first element a rect x="0" y="0" width="48" height="48" rx="10" as the ground; '
            "one bold symbol readable at 16 px; at most 3 colours as hex; only the elements "
            "svg, g, path, rect, circle, ellipse, line, polyline, polygon; "
            "no text, no gradients, no style, no scripts, no links, no external references. "
            f"Output only the {COUNT} SVGs, one per line, nothing else.")


def which_cli() -> dict | None:
    """The CLI Generate runs: {agent, model, bin}, or None."""
    claude = shutil.which("claude")
    if not claude:
        for c in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"),
                  Path("/usr/local/bin/claude"), Path("/usr/bin/claude")):
            if c.exists():
                claude = str(c)
                break
    if claude:
        return {"agent": "claude", "model": CLAUDE_MODEL, "bin": claude}
    codex = shutil.which("codex")
    if codex:
        return {"agent": "codex", "model": "", "bin": codex}
    return None


def _run(cli: dict, text: str, timeout: float) -> str:
    """The CLI's answer (raises subprocess.TimeoutExpired, RuntimeError)."""
    with tempfile.TemporaryDirectory(prefix="ens-icon-") as tmp:
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE",)}
        if cli["agent"] == "claude":
            # The prompt first: `--tools ""` before it would take it as a tool list.
            cmd = [cli["bin"], "-p", text, "--model", cli["model"], "--tools", ""]
            out_file = None
        else:
            out_file = Path(tmp) / "answer.txt"
            cmd = [cli["bin"], "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                   "-C", tmp, "-o", str(out_file), text]
        p = subprocess.run(cmd, cwd=tmp, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout or "")[-300:])
        if out_file is not None:
            try:
                return out_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return p.stdout or ""
        return p.stdout or ""


def generate(key: str, name: str, about: str, timeout: float = TIMEOUT_S) -> tuple[bool, str, dict]:
    """(ok, code, result): result {agent, model, svgs, ms} on success; code one of REFUSALS."""
    cli = which_cli()
    if cli is None:
        return False, "no_cli", {}
    meta = {"agent": cli["agent"], "model": cli["model"]}
    with _BUSY_LOCK:
        if key in _BUSY:
            return False, "busy", meta
        _BUSY.add(key)
    t0 = time.monotonic()
    try:
        try:
            answer = _run(cli, prompt(name, about), timeout)
        except subprocess.TimeoutExpired:
            return False, "timeout", meta
        except (RuntimeError, OSError) as e:
            print(f"[icon-gen] {cli['agent']} failed: {e!r}"[:400], flush=True)
            return False, "failed", meta
        svgs = parse(answer)
        if not svgs:
            return False, "no_icons", meta
        return True, "ok", {**meta, "svgs": svgs, "ms": int((time.monotonic() - t0) * 1000)}
    finally:
        with _BUSY_LOCK:
            _BUSY.discard(key)
