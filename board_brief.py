"""A bounded, read-only board snapshot for a PO's first hub prompt.

No scheduler is run here: the handover is context, never the board's ledger.
"""
from __future__ import annotations

import re
import time
from datetime import datetime


def _short(value, limit=220):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def usage_head(d) -> str:
    """Cached account windows; stale percentages are floors, never live values."""
    rows = []
    for source in d.usage.snapshot().get("sources", []):
        windows = []
        for w in source.get("windows", []):
            if w.get("inUse") is False:
                continue
            pct = w.get("percent")
            value = "unknown" if pct is None else (
                ("" if w.get("trusted") else "at least ") + f"{pct:g}%")
            reset = w.get("resetsAt")
            if isinstance(reset, str):
                reset = datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp()
            windows.append(f"{w.get('pool') or ''} {w.get('label', w.get('kind', 'window'))} "
                           f"{value}, resets {time.strftime('%m-%d %H:%M', time.localtime(reset)) if reset else 'unknown'}".strip())
        rows.append(f"{source.get('source', '?')}: {', '.join(windows) or 'unknown'}")
    return "; ".join(rows) or "unknown (no cached reading yet)"


def _due(d, project, now):
    hp = d.rotation.handover_path(project)
    try:
        anchor = min(hp.stat().st_mtime, now)
        items = d.due.parse(hp.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    seen = d.due._load().get("seen", {}).get(str(hp), {})
    out = []
    for item in items:
        when = (seen.get(item["line"]) or {}).get("due") or d.due.resolve(item, anchor)
        if when and when > now:
            out.append((when, f"{time.strftime('%m-%d %H:%M', time.localtime(when))} — {_short(item['what'])}"))
    return [text for _, text in sorted(out)]


def _state(d, room, attention):
    ask = d.attention.open_ask(room) or {}
    if ask:
        return "waiting on the PO" if ask.get("to") == "po" else "waiting on the CEO"
    if attention.get("state") in ("stalled", "agent_gone", "blocked"):
        return "stalled"
    for part in room.get("participants", []):
        review = part.get("review") or {}
        if review.get("startedAt") and not review.get("endedAt"):
            return "waiting on reviewer"
    if room.get("status") == "waiting_human":
        return "waiting on the CEO"
    if d.workflow_of(room) == "inreview":
        return "waiting on the PO"
    if d.ensemble_tools._status(room) in ("stopped", "paused"):
        return "stalled"
    return "working"


def _tasks(d, project):
    links, labels = d.load_session_projects(), d.load_labels()
    attn = d.attention.by_room()
    rooms = [r for r in d.chatroom.list_rooms()
             if r['id'] != project.get('poRoomId')
             and d.ensemble_tools._project_of_room(r, links) == project['id']]
    rooms.sort(key=lambda r: (d.priority_of(r), -float(r.get('updatedAt') or 0), r.get('no') or 0))
    # One git walk for a large board. Older Git versions fall back to one
    # bounded rev-list per branch; no commands change branches or files.
    counts = {}
    if project.get('isGit') and project.get('path'):
        refs = d._git_out(project['path'], 'for-each-ref',
                          '--format=%(refname:short)%09%(ahead-behind:main)', 'refs/heads', timeout=3)
        for line in refs.splitlines():
            ref, sep, value = line.partition('\t')
            ahead = value.split(' ')[0]
            if sep and ahead.isdigit():
                counts[ref] = ahead
    running, done, drafts = [], [], []
    for room in rooms:
        name = f"{d.task_label(room) or room['id']} {_short(d.ensemble_tools._title(room, labels), 100)}"
        if not room.get('launched', True):
            gate = room.get('after')
            if isinstance(gate, list):
                gate = ', '.join(f"{g.get('task', '?')} ({g.get('when', 'merged')})"
                                 if isinstance(g, dict) else str(g) for g in gate)
            drafts.append(f"{d.PRIORITY_NAMES[d.priority_of(room)]}: {name}"
                          + (f"; after {_short(gate)}" if gate else ""))
            continue
        rep = d.chatroom.last_real_report(room)
        workflow = d.workflow_of(room)
        # A completion survives later update reports. A subsequent move back
        # into progress explicitly reopens it and must win over that history.
        reopened = (workflow == 'inprogress'
                    and float(room.get('workflowAt') or 0) > float(rep.get('ts') or 0))
        completed = workflow == 'done' or (rep.get('kind') == 'completed' and not reopened)
        if (not completed and d.ensemble_tools._status(room) == 'stopped'
                and room['id'] not in attn and not d.attention.open_ask(room)):
            continue
        branch = (room.get('workspace') or {}).get('branch') or ''
        cwd = project.get('path') or room.get('cwd') or ''
        ahead = counts.get(branch)
        if ahead is None:
            ahead = d._git_out(cwd, 'rev-list', '--count', f'main..{branch}', timeout=2) if branch else ''
        git = f"branch `{branch or 'none'}`; {ahead if ahead.isdigit() else 'unknown'} ahead of main"
        if completed:
            if ahead.isdigit() and int(ahead) > 0:
                done.append(f"{name}; {git}")
        else:
            running.append(f"{name}; {_state(d, room, attn.get(room['id'], {}))}; {git}")
    return running, done, drafts, rooms


def _merges(d, project, rooms):
    if project.get('kind') == 'documents' or not project.get('isGit'):
        return []
    log = d._git_out(project.get('path', ''), 'log', 'main', '--first-parent',
                     '--merges', '-5', '--format=%h %s', timeout=2)
    rows = []
    for line in log.splitlines():
        refs = re.findall(r'#\d+|\b[A-Z]+-\d+\b', line)
        if not refs:
            refs = [d.task_label(r) for r in rooms
                    if (r.get('workspace') or {}).get('branch')
                    and r['workspace']['branch'] in line]
            if refs:
                line += '; ' + ', '.join(refs)
        rows.append(_short(line) + ("; task number unknown" if not refs else ""))
    return rows


def build(d, project, room=None, now=None) -> str:
    now = time.time() if now is None else now
    rows = [f"## Board now (from the hub, {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(now))})",
            "This board is the current truth; the handover is context. Omitted rows are counted; use ensemble_list_tasks/ensemble_points for the rest."]
    # Isolate sources: a missing git ref or ledger must not prevent a PO starting.
    try:
        running, done, drafts, rooms = _tasks(d, project)
    except Exception:
        running, done, drafts, rooms = ['unavailable'], ['unavailable'], ['unavailable'], []
    rid = (room or {}).get('id') or project.get('poRoomId', '')
    try:
        live = [p for p in d.points.load(rid).get('points', []) if p.get('state') in d.points.LIVE] if rid else []
        live.sort(key=lambda p: (d.points.LIVE.index(p['state']), p.get('createdAt', 0)))
        pts = [f"{p['id']} ({p['state']}): {_short(p.get('text'))}" for p in live]
    except Exception:
        pts = ['unavailable']
    try:
        due = _due(d, project, now)
    except Exception:
        due = ['unavailable']
    try:
        merges = _merges(d, project, rooms)
    except Exception:
        merges = ['unavailable']
    try:
        usage = usage_head(d)
    except Exception:
        usage = 'unavailable'
    # 2 intro + 7 headings + at most 29 data rows = 38 lines. A full bucket
    # reserves its final row for an explicit overflow count.
    for title, items, cap in [('Running', running, 8), ('Done, not merged', done, 4),
                              ('Drafts by priority', drafts, 5), ('CEO points', pts, 4),
                              ('Due', due, 2), ('Last 5 merges on main', merges, 5),
                              ('Usage (account-wide, cached)', [usage], 1)]:
        rows.append(f"### {title}")
        if len(items) > cap:
            rows.extend('- ' + item for item in items[:cap - 1])
            rows.append(f"- … {len(items) - cap + 1} more")
        else:
            rows.extend('- ' + item for item in (items or ['none']))
    return '\n'.join(rows)


def prepend(d, project, prompt, room=None):
    """Keep the transport's leading hub tag for attribution and input routing."""
    prefix, sep, body = prompt.partition('] ')
    if sep and prefix.startswith('['):
        return prefix + '] ' + build(d, project, room) + '\n\n' + body
    return build(d, project, room) + '\n\n' + prompt
