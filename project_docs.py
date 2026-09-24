"""A project's Documents folder: where its tasks' reports and design notes live.

Task folders hold working notes (TASK-HANDOVER.md, REVIEW-LOG.md) and
checkouts; the project's knowledge — a task's deliverable report, a design
decision — goes in one folder per project, in its home, named by the task:
``#<no> <short title>.md``, or a folder ``#<no> <title>/`` for several files.
The Workspace opens on it, newest first.

The folder's name is chosen once and kept in the project's project.json
(``documentsDir``): ``Documents`` unless the folder already holds something of
that name that is not ours (a documents project's own files), then the next
free candidate. This module is plain file work, so tools/migrate_reports.py
and the tests use it without the hub.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

DEFAULT_NAME = "Documents"
# For a documents project the folder is the CEO's own: a name already there is
# his, never taken over.
CANDIDATES = ("Documents", "Task documents", "Ensemble documents")
# What the list leaves out: pictures and media are part of a document, reached
# from it, not documents of their own.
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
             ".mp4", ".webm", ".mov", ".mp3", ".wav"}
LIST_MAX = 1000
_NO_RE = re.compile(r"^#(\d+)(?:\s+|$)")
_NAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def valid_name(name: str) -> bool:
    """A plain folder name: no separators, not . or .., not hidden."""
    n = (name or "").strip()
    return bool(n) and n not in (".", "..") and not n.startswith(".") and not _NAME_BAD.search(n)


def _is_task_folder(p: Path) -> bool:
    try:
        return (p / "task.json").is_file()
    except OSError:
        return False


def choose_name(home: str, documents_project: bool) -> str:
    """The name a project's Documents folder gets, the first time it is asked
    for. A code project's home holds only what the hub and its tasks write, so
    an existing ``Documents`` there is ours unless it is a task's folder. A
    documents project's home is the CEO's folder: only a name not there yet."""
    for i, cand in enumerate(CANDIDATES + tuple(f"Ensemble documents {n}" for n in range(2, 50))):
        p = Path(home) / cand
        try:
            exists = p.exists()
        except OSError:
            exists = True
        if not exists:
            return cand
        if not documents_project and i == 0 and p.is_dir() and not _is_task_folder(p):
            return cand
    return "Ensemble documents 50"


def doc_title(name: str) -> tuple[int | None, str]:
    """The task number and the title a Documents entry names:
    ``#12 Short title.md`` -> (12, "Short title")."""
    stem = name
    for ext in (".md", ".markdown"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    else:
        stem = os.path.splitext(stem)[0] if "." in stem[1:] else stem
    m = _NO_RE.match(stem)
    if not m:
        return None, stem.strip() or name
    rest = stem[m.end():].strip()
    return int(m.group(1)), rest or stem


def _walk(base: Path, max_depth: int):
    """Files under base, depth first, hidden ones and dirs left out."""
    stack = [(base, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            kids = list(os.scandir(d))
        except OSError:
            continue
        for e in kids:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < max_depth:
                        stack.append((Path(e.path), depth + 1))
                elif e.is_file(follow_symlinks=False):
                    yield e
            except OSError:
                continue


def list_documents(docs_dir: str, code_docs: str = "", limit: int = LIST_MAX) -> dict:
    """The project's documents, newest first: every file of the Documents
    folder (media left out: a picture belongs to the document that shows it),
    and the Markdown under the main checkout's docs/ folder, marked inCode.

    Each item: path (absolute), rel (under its folder), title, no (the task
    number its top-level entry names, or None), group (the title of the
    ``#<no> <title>/`` folder it is in, or ""), mtime, size, inCode."""
    items: list[dict] = []
    base = Path(docs_dir) if docs_dir else None
    if base and base.is_dir():
        for e in _walk(base, 6):
            if os.path.splitext(e.name)[1].lower() in MEDIA_EXT:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            rel = os.path.relpath(e.path, base).replace("\\", "/")
            top = rel.split("/", 1)[0]
            no, title = doc_title(e.name)
            group = ""
            if "/" in rel:
                no, group = doc_title(top)
            items.append({"path": e.path, "rel": rel, "title": title, "no": no, "group": group,
                          "mtime": int(st.st_mtime), "size": st.st_size, "inCode": False})
    cbase = Path(code_docs) if code_docs else None
    if cbase and cbase.is_dir():
        for e in _walk(cbase, 6):
            if os.path.splitext(e.name)[1].lower() not in (".md", ".markdown"):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            rel = os.path.relpath(e.path, cbase).replace("\\", "/")
            items.append({"path": e.path, "rel": "docs/" + rel, "title": doc_title(e.name)[1], "no": None,
                          "group": "", "mtime": int(st.st_mtime), "size": st.st_size, "inCode": True})
    items.sort(key=lambda x: (-x["mtime"], x["rel"].lower()))
    return {"dir": docs_dir, "exists": bool(base and base.is_dir()),
            "codeDocs": code_docs if cbase and cbase.is_dir() else "",
            "items": items[:limit], "truncated": len(items) > limit}


# ---- Migration: copy the reports task folders hold into Documents -------------

KEEP_IN_TASK = {"task-handover.md", "review-log.md"}
# Task-root folders that are never a report's: checkouts, agents' clones,
# tooling.
SKIP_DIRS = {"repo", "claude", "codex", "node_modules", "__pycache__", "venv", ".venv"}
# A browser profile a review left behind is not a report.
_PROFILE_MARKS = ("Local State", "Default", "Crashpad", "Preferences", "Cookies")
SUBFOLDER_MAX_BYTES = 50 * 1024 * 1024


def folder_title(no: int, title: str, max_len: int = 80) -> str:
    """``#<no> <title>`` made safe as a folder name."""
    t = _NAME_BAD.sub(" ", title or "").strip().rstrip(". ")
    t = re.sub(r"\s+", " ", t)
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0].rstrip(" .,;:-") or t[:max_len]
    return f"#{no} {t}".rstrip() if t else f"#{no}"


def _subfolder_skip(p: Path) -> str:
    """Why a task-root folder is not a report's, or ""."""
    name = p.name
    if name.startswith("."):
        return "hidden"
    if name.lower() in SKIP_DIRS:
        return "a checkout or an agent's folder"
    if (p / ".git").exists():
        return "a git repository"
    if _is_task_folder(p):
        return "a task's folder"
    total = 0
    for root, dirs, files in os.walk(p):
        if any(m in files or m in dirs for m in _PROFILE_MARKS):
            return "a browser profile"
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            if total > SUBFOLDER_MAX_BYTES:
                return "over 50 MB"
    if total == 0:
        return "empty"
    return ""


def task_folders(home: str) -> list[dict]:
    """The task folders of a project home: each folder holding a task.json
    with a number. [{dir, no, title}], by number."""
    import json
    out = []
    try:
        kids = sorted(Path(home).iterdir(), key=lambda c: c.name.lower())
    except OSError:
        return out
    for d in kids:
        tj = d / "task.json"
        try:
            if not d.is_dir() or not tj.is_file():
                continue
            meta = json.loads(tj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        no = meta.get("no") if isinstance(meta, dict) else None
        if not isinstance(no, int) or no <= 0:
            continue
        out.append({"dir": str(d), "no": no, "title": str(meta.get("title") or d.name)})
    out.sort(key=lambda t: t["no"])
    return out


def plan_migration(home: str, docs_dir: str) -> dict:
    """What a migration would copy: for each numbered task folder, its
    top-level Markdown (TASK-HANDOVER.md, REVIEW-LOG.md and PO-*.md stay: they
    are working notes), its report subfolders (those holding a document:
    their documents and pictures only) and the pictures a copied document
    links to, into ``<Documents>/#<no> <title>/``. Nothing is written.

    {copy: [{src, dst, task, size, mtime}], same: [...], differs: [...],
    skipped: [{path, why}]}: ``same`` is already there (the same bytes),
    ``differs`` is there with other content and is left alone."""
    plan = {"copy": [], "same": [], "differs": [], "skipped": []}
    docs = Path(docs_dir)
    for t in task_folders(home):
        src_dir = Path(t["dir"])
        if os.path.normcase(str(src_dir)) == os.path.normcase(str(docs)):
            continue
        dest_dir = docs / folder_title(t["no"], t["title"])
        files: list[tuple[Path, Path]] = []
        try:
            kids = sorted(src_dir.iterdir(), key=lambda c: c.name.lower())
        except OSError:
            continue
        for c in kids:
            try:
                is_file, is_dir = c.is_file(), c.is_dir()
            except OSError:
                continue
            if is_file:
                if c.suffix.lower() != ".md":
                    continue
                low = c.name.lower()
                if low in KEEP_IN_TASK or low.startswith("po-"):
                    plan["skipped"].append({"path": str(c), "why": "working notes, kept in the task folder"})
                    continue
                files.append((c, dest_dir / c.name))
            elif is_dir:
                why = _subfolder_skip(c)
                if why:
                    if why not in ("hidden", "empty"):
                        plan["skipped"].append({"path": str(c), "why": why})
                    continue
                # A report's folder holds a document; from it go its documents
                # and their pictures, not the samples, fixtures or data beside
                # them. A folder of screenshots alone is evidence, not a report.
                found = [p for p in _files_under(c) if p.suffix.lower() in DOC_EXT]
                if not found:
                    plan["skipped"].append({"path": str(c), "why": "no document in it (screenshots, samples, data)"})
                    continue
                for sp in _files_under(c):
                    if sp.suffix.lower() in DOC_EXT or sp.suffix.lower() in MEDIA_EXT:
                        files.append((sp, dest_dir / sp.relative_to(src_dir)))
        # A picture a copied document links to goes with it, wherever it is
        # in the task folder.
        have = {os.path.normcase(str(sp)) for sp, _ in files}
        for sp, _ in list(files):
            if sp.suffix.lower() not in (".md", ".markdown"):
                continue
            for pic in _linked_media(sp, src_dir):
                if os.path.normcase(str(pic)) not in have:
                    have.add(os.path.normcase(str(pic)))
                    files.append((pic, dest_dir / pic.relative_to(src_dir)))
        for sp, dp in files:
            try:
                st = sp.stat()
            except OSError:
                continue
            item = {"src": str(sp), "dst": str(dp), "task": t["no"], "size": st.st_size, "mtime": int(st.st_mtime)}
            try:
                dst = dp.stat()
            except OSError:
                dst = None
            if dst is None:
                plan["copy"].append(item)
            elif dst.st_size == st.st_size and _same_bytes(sp, dp):
                plan["same"].append(item)
            else:
                plan["differs"].append(item)
    return plan


# What makes a task subfolder a report's, and what is copied from it.
DOC_EXT = {".md", ".markdown", ".html", ".htm", ".pdf", ".docx"}
# The image destinations the Markdown viewer (fileview.html) renders: one
# level of parentheses inside the path, an optional "title" or 'title'.
_LINK_RE = re.compile(r"""\]\(\s*<?([^()\s<>]+(?:\([^()\s]*\)[^()\s<>]*)*)>?(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)|\bsrc\s*=\s*["']([^"']+)["']""", re.I)


def _files_under(d: Path) -> list[Path]:
    out = []
    for root, dirs, fs in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        out.extend(Path(root) / f for f in sorted(fs) if not f.startswith("."))
    return out


def _linked_media(md: Path, task_dir: Path) -> list[Path]:
    """The pictures a Markdown file links to by a relative path inside its
    task folder."""
    try:
        text = md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    base = os.path.normcase(os.path.realpath(task_dir))
    for m in _LINK_RE.finditer(text):
        ref = (m.group(1) or m.group(2) or "").split("#", 1)[0].split("?", 1)[0]
        if not ref or re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I) or ref.startswith(("/", "\\")):
            continue

        p = (md.parent / unquote(ref))
        if p.suffix.lower() not in MEDIA_EXT:
            continue
        real = os.path.normcase(os.path.realpath(p))
        if not real.startswith(base + os.sep):
            continue
        try:
            if p.is_file():
                out.append(Path(os.path.realpath(p)))
        except OSError:
            continue
    return out


def _same_bytes(a: Path, b: Path) -> bool:
    """Whether two files hold the same bytes: a copy made earlier, not
    someone's own file of that name and size."""
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(1 << 16), fb.read(1 << 16)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def apply_migration(plan: dict) -> list[dict]:
    """Copy what ``plan`` says to copy, keeping each file's times. Never
    overwrites and never removes: the destination is created exclusively, so
    a file that appeared there since the plan was made (even a moment before
    the copy) is left alone. Returns what was copied."""
    done = []
    for item in plan["copy"]:
        dp = Path(item["dst"])
        dp.parent.mkdir(parents=True, exist_ok=True)
        try:
            out = open(dp, "xb")
        except FileExistsError:
            continue
        try:
            with out, open(item["src"], "rb") as src:
                shutil.copyfileobj(src, out)
            # Inside the try: a copy left without its times would read as
            # "same" next time and never get them.
            shutil.copystat(item["src"], dp)
        except OSError:
            try:
                dp.unlink()      # our own half-written file, never someone else's
            except OSError:
                pass
            raise
        done.append(item)
    return done
