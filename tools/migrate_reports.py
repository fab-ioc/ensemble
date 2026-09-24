"""Copy the reports a project's task folders hold into its Documents folder.

Once per project, when it moves to the Documents folder: each numbered task
folder's top-level Markdown (TASK-HANDOVER.md, REVIEW-LOG.md and PO-*.md stay:
they are working notes) and its report subfolders (screenshots, evidence;
never a checkout, an agent's clone, a git repository or a browser profile) are
COPIED to ``<Documents>/#<no> <title>/``, with their times. Nothing is moved,
removed or overwritten, so it can be run again: a file already there is left
as it is.

    py tools/migrate_reports.py "Ensemble Dashboard"            # what it would copy
    py tools/migrate_reports.py "Ensemble Dashboard" --apply    # copy

The project is named by its folder under the projects root, its name or its
id. The Documents folder is the one the hub gives the project (``documentsDir``
in its project.json); a project that has none yet gets it chosen the same way.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import project_docs  # noqa: E402


def default_root() -> Path:
    try:
        from backends.base import PROJECTS_ROOT      # the hub's own answer
        return Path(PROJECTS_ROOT)
    except Exception:
        return Path.home() / "EnsembleProjects"


def find_home(root: Path, name: str) -> tuple[Path, dict] | None:
    """The project home under root named by folder, name or id."""
    want = name.strip().lower()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        pj = d / "project.json"
        if not pj.is_file():
            continue
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        if want in (d.name.lower(), str(meta.get("name", "")).lower(), str(meta.get("id", "")).lower()):
            return d, meta
    return None


def documents_dir(home: Path, meta: dict, write: bool) -> Path:
    """The project's Documents folder; its name is recorded in project.json
    (only with ``write``) when it had none, as the hub would."""
    name = meta.get("documentsDir") or ""
    if project_docs.valid_name(name):
        return home / name
    name = project_docs.choose_name(str(home), meta.get("kind") == "documents")
    if write:
        pj = home / "project.json"
        meta = json.loads(pj.read_text(encoding="utf-8"))
        meta["documentsDir"] = name
        tmp = pj.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        tmp.replace(pj)
    return home / name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", help="the project's folder, name or id")
    ap.add_argument("--apply", action="store_true", help="copy (without it, only say what would be copied)")
    ap.add_argument("--root", default="", help="the projects root (default: the hub's)")
    a = ap.parse_args(argv)
    root = Path(a.root) if a.root else default_root()
    found = find_home(root, a.project)
    if not found:
        print(f"No project {a.project!r} under {root}", file=sys.stderr)
        return 2
    home, meta = found
    docs = documents_dir(home, meta, a.apply)
    plan = project_docs.plan_migration(str(home), str(docs))
    rel = lambda p: os.path.relpath(p, home)                       # noqa: E731
    print(f"Project: {meta.get('name') or home.name}  ({home})")
    print(f"Documents folder: {docs}")
    copied = project_docs.apply_migration(plan) if a.apply else plan["copy"]
    verb = "Copied" if a.apply else "Would copy"
    by_task = collections.defaultdict(list)
    for it in copied:
        by_task[it["task"]].append(it)
    for no in sorted(by_task):
        items = by_task[no]
        into = Path(os.path.relpath(items[0]["dst"], docs)).parts[0]
        print(f"\n#{no}: {len(items)} file{'s' if len(items) != 1 else ''} -> {docs.name}/{into}/")
        for it in items:
            print(f"  {rel(it['src'])}")
    print(f"\n{verb}: {len(copied)} file{'s' if len(copied) != 1 else ''} from {len(by_task)} task{'s' if len(by_task) != 1 else ''}.")
    if plan["same"]:
        print(f"Already there: {len(plan['same'])} (left as they are).")
    for it in plan["differs"]:
        print(f"Left alone, a different file of that name is there: {rel(it['dst'])}")
    why = collections.Counter(s["why"] for s in plan["skipped"])
    if why:
        print("Not copied: " + "; ".join(f"{n} {w}" for w, n in why.most_common()))
    for s in plan["skipped"]:
        if s["why"] in ("a browser profile", "over 50 MB", "a git repository"):
            print(f"  {rel(s['path'])}: {s['why']}")
    if not a.apply:
        print("\nDry run: nothing was written. Add --apply to copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
