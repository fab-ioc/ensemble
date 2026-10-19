"""Project topics derived from PO threads, with durable manual corrections.

The last 30 days are grouped on first read. New threads are added as their
thread index changes; a person's corrections live beside the room state,
keyed by project, so a restart cannot undo them.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

import durable
import points
import threads

_LOCK = threading.RLock()
_PROJECT = re.compile(r"proj-[0-9a-f]+$")
_DAYS = 30 * 86400
_STOP = frozenset("a an and are at be been can did do does for from has have how i in is it its latest me my of on or our please the this to was we were what when where why with would you your after before about all any more new now one own same some still than then there these those them they their via will yes done ready update task thread point report review status issue work".split())
_CACHE: dict[str, tuple[tuple, dict]] = {}
_CACHE_LOCK = threading.Lock()
_BUILDING: set[str] = set()


def _path(project_id: str, rooms_dir: Path) -> Path:
    if not _PROJECT.fullmatch(project_id or ""):
        raise ValueError("invalid project")
    return Path(rooms_dir).parent / "topics" / f"{project_id}.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    archive = data.get("archive") or {}
    if isinstance(archive, list):
        archive = {key: 0 for key in archive}
    return {"version": 2, "auto": dict(data.get("auto") or {}) if data.get("version") == 2 else {},
            "assign": dict(data.get("assign") or {}), "names": dict(data.get("names") or {}),
            "merged": dict(data.get("merged") or {}), "archive": dict(archive),
            "declined": dict(data.get("declined") or {})}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        durable.write_tmp(tmp, json.dumps(data, ensure_ascii=True, indent=1))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _terms(text: str) -> set[str]:
    def stem(word: str) -> str:
        if len(word) > 7 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) > 6 and word.endswith("ed"):
            word = word[:-2]
        elif len(word) > 5 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        return word
    return {stem(w) for w in re.findall(r"[a-z0-9]{3,}|[a-z]+[0-9]+", text.lower())
            if w not in _STOP and not w.isdigit()}


def _features(th: dict, idx: dict) -> dict:
    root = idx["by_mid"].get(th["root"]) or {}
    title = th.get("title") or "Topic"
    root_text = (root.get("turn") or {}).get("text") or ""
    heading = _terms(title)
    own = heading | _terms(root_text[:1000])
    refs = Counter(points.task_refs(root_text))
    replies = []
    for mid in th["members"]:
        r = idx["by_mid"].get(mid) or {}
        if mid == th["root"] or mid in idx["work"]:
            continue
        body = (r.get("turn") or {}).get("text") or ""
        if r.get("role") == "agent":
            replies.append(body)
        # A PO reply may mention another task while answering this thread.
        # Only the CEO's own messages can establish its task identity.
        if r.get("role") == "user" and r.get("kind") == "human":
            refs.update(points.task_refs(body))
    reply_terms = _terms("\n".join(body[:450] for body in replies[-2:]))
    whole = own | reply_terms
    task = th.get("task") or ""
    if not task and refs:
        ranked = refs.most_common()
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            task = ranked[0][0]
    return {"task": task, "heading": heading, "root": own,
            "all": whole, "title": title,
            "ts": (root.get("ts") or 0)}


def _similarity(a: dict, b: dict, rarity: dict[str, float], rare_cutoff: float) -> float:
    common = a["root"] & b["root"]
    cross = (a["root"] & b["all"]) | (b["root"] & a["all"])
    if not cross:
        return 0.0
    core = sum(rarity.get(t, 0) for t in common)
    shared = core + .35 * sum(rarity.get(t, 0) for t in cross - common)
    base = sum(rarity.get(t, 0) for t in a["root"] | b["root"]) or 1
    score = shared / base
    # A distinctive name occurring in both roots is a useful subject even
    # when each question has quite different surrounding words.
    if any(len(t) >= 7 and rarity.get(t, 0) >= rare_cutoff
           and t in a["heading"] and t in b["heading"] for t in common):
        score = max(score, .72)
    return score


def _subject_name(members: list[str], features: dict, rarity: dict) -> str:
    if len(members) == 1:
        return features[members[0]]["title"][:80]
    counts = Counter(t for tid in members for t in features[tid]["root"])
    common = {t for t, n in counts.items() if n >= max(2, math.ceil(len(members) * .45))
              and rarity.get(t, 0) >= .7}
    if common:
        chosen = sorted(common, key=lambda t: (-counts[t], -rarity.get(t, 0), t))[:3]
        return " ".join(w.upper() if any(c.isdigit() for c in w) else w.capitalize() for w in chosen)
    return features[members[0]]["title"][:80]


def _auto_groups(candidates: dict[str, dict]) -> tuple[dict[str, str], dict[str, str], dict[tuple[str, str], float]]:
    ids = sorted(candidates, key=lambda tid: (candidates[tid]["ts"], tid))
    count = Counter(t for row in candidates.values() for t in row["root"])
    # Repeated hub briefs and handover instructions are poor subject signals.
    rare_cutoff = 1 + math.log((len(ids) + 1) / (max(2, len(ids) * .1) + 1))
    rarity = {t: 1 + math.log((len(ids) + 1) / (n + 1)) if n <= max(5, len(ids) * .12) else 0
              for t, n in count.items()}
    clusters: dict[str, list[str]] = {}
    scores = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            aa, bb = candidates[a], candidates[b]
            score = _similarity(aa, bb, rarity, rare_cutoff)
            if score >= .35:
                scores[a, b] = score
    for tid in ids:
        row = candidates[tid]
        same_task = "task:" + row["task"] if row["task"] else ""
        best = max(((scores.get((rep, tid), 0), group) for group, members in clusters.items()
                    for rep in members[:1]), default=(0, ""))
        if same_task and same_task in clusters:
            group = same_task
        elif best[0] >= (.78 if row["task"] or best[1].startswith("task:") else .68):
            group = best[1]
        else:
            group = same_task or "subject:" + tid.replace(":", "-")
        clusters.setdefault(group, []).append(tid)
    auto, labels = {}, {}
    for key, members in clusters.items():
        first = min(members, key=lambda tid: (candidates[tid]["ts"], tid))
        task = candidates[first]["task"]
        labels[key] = task or _subject_name(members, candidates, rarity)
        for tid in members:
            auto[tid] = key
    return auto, labels, scores


def _target(topic: str, merged: dict) -> str:
    seen = set()
    while topic in merged and topic not in seen:
        seen.add(topic)
        topic = merged[topic]
    return topic


def _build_view(project: dict, idx: dict, rooms_dir: Path, *, now: float | None = None) -> dict:
    """Build from one thread snapshot. Store edits are serialized, but readers
    never take this lock and can keep using the last complete view."""
    path = _path(project["id"], rooms_dir)
    cacheable = now is None
    now = time.time() if cacheable else now
    with _LOCK:
        stamp = path.stat().st_mtime_ns if path.exists() else 0
        cache_key = (idx["sig"], stamp)
        hit = _CACHE.get(project["id"])
        if cacheable and hit and hit[0] == cache_key:
            return hit[1]
        data = _load(path)
        changed = False
        topics: dict[str, dict] = {}
        suggestions: dict[str, dict] = {}
        eligible = []
        for tid in idx["order"]:
            th = idx["threads"][tid]
            if tid == "team:activity" or (th.get("rootWho") != "you" and not th.get("openQuestions")) \
                    or max((idx["by_mid"].get(mid) or {}).get("ts", 0)
                           for mid in th["members"]) < now - _DAYS:
                continue
            eligible.append(tid)
        features = {tid: _features(idx["threads"][tid], idx) for tid in eligible}
        heading_freq = Counter(t for feat in features.values() for t in feat["heading"])
        auto_keys, labels, scores = _auto_groups(features)
        for tid in eligible:
            th = idx["threads"][tid]
            auto = auto_keys[tid]
            if tid not in data["auto"]:
                data["auto"][tid] = auto
                changed = True
            raw = data["assign"].get(tid) or data["auto"][tid]
            topic_id = _target(raw, data["merged"])
            if topic_id not in topics:
                topics[topic_id] = {"id": topic_id, "name": data["names"].get(topic_id) or labels.get(topic_id) or features[tid]["title"],
                                    "threads": [], "latest": None, "unread": 0,
                                    "decisions": 0, "task": "", "state": ""}
            row = topics[topic_id]
            row["threads"].append(tid)
            row["unread"] += int(th.get("unread") or 0)
            row["decisions"] += int(th.get("openQuestions") or 0)
            if features[tid]["task"] and not row["task"]:
                row["task"] = features[tid]["task"]
            for mid in th["members"]:
                r = idx["by_mid"].get(mid)
                if not r or r["role"] != "agent" or mid in idx["work"]:
                    continue
                if row["latest"] is None or r["ts"] > row["latest"]["ts"]:
                    row["latest"] = {"mid": mid, "thread": tid, "ts": r["ts"],
                                     "text": " ".join((r["turn"].get("text") or "").split())[:240]}
                    row["state"] = th.get("state") or ""
        # Score against all topics only after all groups exist: a chip must
        # not depend on which thread happened to be visited first.
        for tid in eligible:
            own = _target(data["assign"].get(tid) or data["auto"][tid], data["merged"])
            if len(topics[own]["threads"]) != 1:
                continue
            best = None
            for other in eligible:
                target = _target(data["assign"].get(other) or data["auto"][other], data["merged"])
                if target == own or target in data["archive"] or target in data["declined"].get(tid, []):
                    continue
                if len(topics[target]["threads"]) < 2:
                    continue
                shared_headings = [term for term in features[tid]["heading"] & features[other]["heading"]
                                   if len(term) >= 4 and heading_freq[term] <= max(5, len(eligible) * .12)]
                if len(shared_headings) < 2:
                    continue
                score = scores.get((tid, other)) or scores.get((other, tid)) or 0
                if score >= .55 and (best is None or score > best[0]):
                    best = (score, target)
            if best:
                suggestions[tid] = {"topic": best[1], "name": topics[best[1]]["name"]}
        for topic_id, row in topics.items():
            archived_at = data["archive"].get(topic_id)
            if archived_at is not None and row["unread"] and (row["latest"] or {}).get("ts", 0) > archived_at:
                data["archive"].pop(topic_id)
                changed = True
        if changed:
            _save(path, data)
        rows = []
        for row in topics.values():
            if row["id"] in data["archive"]:
                continue
            row["threadCount"] = len(row["threads"])
            row["threadOptions"] = [{"id": tid, "title": (idx["threads"][tid].get("title") or tid)[:100]}
                                    for tid in row["threads"]]
            rows.append(row)
        rows.sort(key=lambda row: ((row["latest"] or {}).get("ts") or 0, row["name"]), reverse=True)
        result = {"pending": False, "topics": rows, "suggestions": suggestions,
                  "archived": [{"id": key, "name": data["names"].get(key) or topics.get(key, {}).get("name") or key}
                               for key in data["archive"]], "sig": idx["sig"]}
        if cacheable:
            final_stamp = path.stat().st_mtime_ns if path.exists() else 0
            result["stamp"] = final_stamp
            with _CACHE_LOCK:
                _CACHE[project["id"]] = ((idx["sig"], final_stamp), result)
        return result


def view(project: dict, room: dict, rooms_dir: Path, *, now: float | None = None) -> dict:
    """Return the last complete view, starting one background rebuild if stale.

    The explicit `now` path is for deterministic audits and tests only.
    """
    pid = project["id"]
    idx = threads.ready(project.get("poRoomId") or "", room)
    if now is not None:
        return _build_view(project, idx, rooms_dir, now=now) if idx else {"pending": True, "topics": [], "suggestions": {}}
    with _CACHE_LOCK:
        hit = _CACHE.get(pid)
    if idx is None:
        return {**hit[1], "building": True} if hit else {"pending": True, "topics": [], "suggestions": {}}
    path = _path(pid, rooms_dir)
    stamp = path.stat().st_mtime_ns if path.exists() else 0
    key = (idx["sig"], stamp)
    with _CACHE_LOCK:
        hit = _CACHE.get(pid)
        if hit and hit[0] == key:
            return hit[1]
        if pid in _BUILDING:
            return {**hit[1], "building": True} if hit else {"pending": True, "topics": [], "suggestions": {}}
        _BUILDING.add(pid)

    def run():
        try:
            _build_view(project, idx, rooms_dir)
        except Exception as e:  # noqa: BLE001 — retry on the next poll
            print(f"[topics] {pid}: not built: {e!r}", flush=True)
        finally:
            with _CACHE_LOCK:
                _BUILDING.discard(pid)

    threading.Thread(target=run, name=f"topics-{pid}", daemon=True).start()
    return {**hit[1], "building": True} if hit else {"pending": True, "topics": [], "suggestions": {}}


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
                data["archive"][topic] = time.time()
        elif action == "restore" and topic in data["archive"]:
            data["archive"].pop(topic)
        elif action == "reject" and thread in data["auto"] and topic in known:
            data["declined"].setdefault(thread, [])
            if topic not in data["declined"][thread]:
                data["declined"][thread].append(topic)
        else:
            raise ValueError("invalid topic change")
        _save(path, data)
