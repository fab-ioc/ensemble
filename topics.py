"""Project topics derived from PO threads, with durable manual corrections.

The last 30 days are grouped on first read. New threads are added as their
thread index changes; a person's corrections live beside the room state,
keyed by project, so a restart cannot undo them.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import durable
import threads

_LOCK = threading.RLock()
_PROJECT = re.compile(r"proj-[0-9a-f]+$")
_DAYS = 30 * 86400
_ALIASES = (
    ("backtesting", "Backtesting", re.compile(r"\bbacktest(?:ed|er|ing|s)?\b|\bdyna.zero\b.*\breplay\b", re.I)),
    ("skew-chart", "Skew chart", re.compile(r"\bskew\b", re.I)),
    ("single-app", "Single app", re.compile(r"\bsingle.app\b|\bsingle app\b", re.I)),
    ("massive-data", "Massive data", re.compile(r"\bmassive\b.{0,30}\b(?:data|history|prices|provider)\b", re.I)),
    ("x5-ad", "X5 ad", re.compile(r"\bX5\b.{0,100}\b(?:ad|advert|views?|statistics|listing|lease|portal|price|disabled|enquir\w*|tra(?:c|ck)tion|facebook|autoscout\w*)\b|"
                                     r"\b(?:ad|advert|views?|statistics|listing|lease|portal|price|disabled|enquir\w*|tra(?:c|ck)tion|facebook|autoscout\w*)\b.{0,100}\bX5\b|"
                                     r"\bAutoSco(?:ut|unt)(?:24)?\b", re.I)),
)
_STOP = frozenset("a an and are at be can do for from has how i in is it latest me my of on or our please the this to was we what where why with you your".split())


def _path(project_id: str, rooms_dir: Path) -> Path:
    if not _PROJECT.fullmatch(project_id or ""):
        raise ValueError("invalid project")
    return Path(rooms_dir).parent / "topics" / f"{project_id}.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"version": 1, "auto": dict(data.get("auto") or {}),
            "assign": dict(data.get("assign") or {}), "names": dict(data.get("names") or {}),
            "merged": dict(data.get("merged") or {}), "archive": list(data.get("archive") or [])}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        durable.write_tmp(tmp, json.dumps(data, ensure_ascii=True, indent=1))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in _STOP and not w.isdigit()}


def _key(th: dict, idx: dict) -> tuple[str, str, set[str]]:
    root = idx["by_mid"].get(th["root"]) or {}
    title = th.get("title") or "Topic"
    text = title + "\n" + ((root.get("turn") or {}).get("text") or "")
    for slug, name, pat in _ALIASES:
        if pat.search(text):
            return "subject:" + slug, name, _terms(name)
    if th.get("task"):
        return "task:" + th["task"], th["task"], _terms(title)
    terms = _terms(title)
    signature = "-".join(sorted(terms)[:3]) or th["id"].replace(":", "-")
    return "subject:" + signature, title, terms


def _target(topic: str, merged: dict) -> str:
    seen = set()
    while topic in merged and topic not in seen:
        seen.add(topic)
        topic = merged[topic]
    return topic


def view(project: dict, room: dict, rooms_dir: Path, *, now: float | None = None) -> dict:
    room_id = project.get("poRoomId") or ""
    idx = threads.ready(room_id, room)
    if idx is None:
        return {"pending": True, "topics": [], "suggestions": {}}
    path = _path(project["id"], rooms_dir)
    now = time.time() if now is None else now
    with _LOCK:
        data = _load(path)
        changed = False
        topics: dict[str, dict] = {}
        suggestions: dict[str, dict] = {}
        for tid in idx["order"]:
            th = idx["threads"][tid]
            if tid == "team:activity" or (th.get("rootWho") != "you" and not th.get("openQuestions")) \
                    or max((idx["by_mid"].get(mid) or {}).get("ts", 0)
                           for mid in th["members"]) < now - _DAYS:
                continue
            auto, label, terms = _key(th, idx)
            if tid not in data["auto"]:
                data["auto"][tid] = auto
                changed = True
            raw = data["assign"].get(tid) or data["auto"][tid]
            topic_id = _target(raw, data["merged"])
            if topic_id not in topics:
                topics[topic_id] = {"id": topic_id, "name": data["names"].get(topic_id) or label,
                                    "threads": [], "latest": None, "unread": 0,
                                    "decisions": 0, "task": "", "state": "", "terms": terms}
            row = topics[topic_id]
            row["threads"].append(tid)
            row["unread"] += int(th.get("unread") or 0)
            row["decisions"] += int(th.get("openQuestions") or 0)
            if th.get("task") and not row["task"]:
                row["task"] = th["task"]
            for mid in th["members"]:
                r = idx["by_mid"].get(mid)
                if not r or r["role"] != "agent" or mid in idx["work"]:
                    continue
                if row["latest"] is None or r["ts"] > row["latest"]["ts"]:
                    row["latest"] = {"mid": mid, "thread": tid, "ts": r["ts"],
                                     "text": " ".join((r["turn"].get("text") or "").split())[:240]}
                    row["state"] = th.get("state") or ""
            # A loose subject may resemble another topic, but isn't joined
            # until the person accepts the chip in its thread.
            if raw.startswith("subject:") and not raw.startswith("subject:" + next((a[0] for a in _ALIASES if auto == "subject:" + a[0]), "\0")):
                best = None
                for other_id, other in topics.items():
                    if other_id == topic_id or other_id in data["archive"] or not terms or not other["terms"]:
                        continue
                    score = len(terms & other["terms"]) / len(terms | other["terms"])
                    if score >= .45 and (best is None or score > best[0]):
                        best = (score, other_id, other["name"])
                if best:
                    suggestions[tid] = {"topic": best[1], "name": best[2]}
        if changed:
            _save(path, data)
        rows = []
        for row in topics.values():
            if row["id"] in data["archive"]:
                continue
            row.pop("terms", None)
            row["threadCount"] = len(row["threads"])
            row["threadOptions"] = [{"id": tid, "title": (idx["threads"][tid].get("title") or tid)[:100]}
                                    for tid in row["threads"]]
            rows.append(row)
        rows.sort(key=lambda row: ((row["latest"] or {}).get("ts") or 0, row["name"]), reverse=True)
        return {"pending": False, "topics": rows, "suggestions": suggestions,
                "sig": idx["sig"]}


def change(project: dict, rooms_dir: Path, action: str, *, topic: str = "", other: str = "",
           thread: str = "", name: str = "") -> None:
    """Apply one reversible correction; the endpoint validates room ownership."""
    path = _path(project["id"], rooms_dir)
    with _LOCK:
        data = _load(path)
        known = set(data["auto"].values()) | set(data["names"]) | set(data["merged"].values())
        if action == "rename" and topic in known and name.strip():
            data["names"][topic] = name.strip()[:80]
        elif action == "merge" and topic in known and other in known and topic != other:
            destination = _target(other, data["merged"])
            if destination == topic:
                raise ValueError("topic merge would make a cycle")
            data["merged"][topic] = destination
        elif action == "move" and thread in data["auto"] and topic in known:
            data["assign"][thread] = topic
        elif action == "out" and thread in data["auto"]:
            data["assign"][thread] = "thread:" + thread
            data["names"]["thread:" + thread] = name.strip()[:80] or "Separate topic"
        elif action == "archive" and topic in known:
            if topic not in data["archive"]:
                data["archive"].append(topic)
        elif action == "restore" and topic in data["archive"]:
            data["archive"].remove(topic)
        else:
            raise ValueError("invalid topic change")
        _save(path, data)
