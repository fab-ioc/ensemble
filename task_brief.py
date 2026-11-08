"""What an open board card says about a task (#316): its goal, where it
stands, and facts the hub has without a model call.

- **Goal** (``room["goal"]``, ``goalSource``, ``goalAt``): what the task is
  for, at most two short lines. The PO writes it with ``ensemble_create_task``
  / ``ensemble_update_task`` (``goalSource: "po"``). A task without one gets
  one written once, in the background after the hub starts, from its spec by
  the digest's cheap model (``goalSource: "auto"``); a goal already there is
  never overwritten by it. Until then, or when the model call fails, the card
  shows the spec's first sentence, marked as from the spec.
- **Where we are** (``room["stateNote"]``: ``{text, by, at}``): progress
  towards the goal, at most two short lines, from the owner's
  ``ensemble_report`` (its optional ``state``). A report without one keeps
  the last.
- **The row's brief** (``row["brief"]``, :func:`row_brief`): the two above,
  the latest report (to link to it) and the reviewer's round.
"""
import hashlib
import json
import re
import subprocess
import threading
import time

_d = None                       # the dashboard module, set by bind()

TEXT_MAX = 250                  # a goal's or a state's characters
LINES_MAX = 2
SPEC_SENTENCE_MAX = 200
BACKFILL_DELAY_S = 90           # after the hub starts: boot restore goes first
BACKFILL_BATCH = 6              # tasks per model call
BACKFILL_SPEC_CHARS = 2000      # of each spec the model reads
BACKFILL_GAP_S = 2.0
BACKFILL_MAX_FAILS = 3          # in a row: the model is not there; stop this pass
BACKFILL_MAX_TASKS = 60         # per hub start; the rest wait for the next
DONE_RECENT_S = 14 * 86400      # a Done task older than this is not backfilled

_STARTED = False
_LOCK = threading.Lock()


def bind(dashboard_module) -> None:
    global _d
    _d = dashboard_module


def clean(text, cap: int = TEXT_MAX) -> tuple[str, bool]:
    """(text, was_cut): at most LINES_MAX non-empty lines, spaces collapsed,
    at most ``cap`` characters (cut at a word, with an ellipsis). Raises
    ValueError when ``text`` is not a string."""
    if not isinstance(text, str):
        raise ValueError("must be text")
    lines = [" ".join(ln.split()) for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    cut = len(lines) > LINES_MAX
    out = "\n".join(lines[:LINES_MAX])
    if len(out) > cap:
        out = out[:cap - 1]
        sp = out.rfind(" ")
        if sp > cap * 0.6:
            out = out[:sp]
        out = out.rstrip(" ,;:.-") + "…"
        cut = True
    return out, cut


def set_goal(room: dict, goal: str, source: str = "po") -> dict | None:
    """Store a goal on the task (room and task.json). An empty goal clears it.
    Returns the room, or None when it is gone."""
    fields = {"goal": goal, "goalSource": source if goal else "", "goalAt": time.time() if goal else 0}
    out = _d.chatroom.patch_room(room["id"], **fields)
    if out is not None:
        _d._patch_task_json(room.get("taskDir", ""), **fields)
    return out


def set_auto_goal(room: dict, goal: str) -> bool:
    """The backfill's goal: written only while the task still has none, tested
    under the room lock, so a PO's goal written meanwhile is never replaced."""
    goal, _ = clean(goal)
    if not goal:
        return False
    at = time.time()
    out = _d.chatroom.patch_room_if(room["id"], lambda r: not (r.get("goal") or "").strip(),
                                    goal=goal, goalSource="auto", goalAt=at)
    if out is None:
        return False
    _d._patch_task_json(room.get("taskDir", ""), goal=goal, goalSource="auto", goalAt=at)
    return True


def spec_key(spec: str) -> str:
    """A short hash of the spec, to tell whether it changed since the backfill
    last asked about it."""
    return hashlib.sha1((spec or "").strip().encode("utf-8")).hexdigest()[:16]


def mark_tried(room: dict) -> None:
    """The model answered the batch but gave this task no goal: remember the
    spec it saw, so later hub starts skip it until the spec changes."""
    _d.chatroom.patch_room_if(room["id"], lambda r: not (r.get("goal") or "").strip(),
                              goalTried={"spec": spec_key(room.get("spec") or ""), "at": time.time()})


def set_state(room_id: str, text: str, by: str, at: float | None = None) -> dict | None:
    """Store where the task stands, by whom and when."""
    return _d.chatroom.patch_room(room_id, stateNote={"text": text, "by": by,
                                                     "at": time.time() if at is None else at})


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARK = re.compile(r"[*_`>#]+")
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)")


def spec_sentence(spec: str) -> str:
    """The spec's first sentence, in plain words: headings, list marks and
    Markdown left out; at most SPEC_SENTENCE_MAX characters."""
    for ln in (spec or "").replace("\r", "").split("\n"):
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("```") or s.startswith("|") or set(s) <= set("-=*_ "):
            continue
        s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
        s = _MD_LINK.sub(r"\1", s)
        s = " ".join(_MD_MARK.sub("", s).split())
        if not s:
            continue
        m = _SENTENCE.match(s)
        return clean(m.group(1) if m else s, SPEC_SENTENCE_MAX)[0]
    return ""


def _review_view(room: dict, alive) -> dict | None:
    """The reviewer seat's latest round: ``{n, running, verdict}``, or None
    before its first."""
    best = None
    for p in room.get("participants", []):
        if p.get("kind") != "agent":
            continue
        cur = p.get("review") or {}
        if not isinstance(cur, dict) or not cur.get("startedAt"):
            continue
        if best is None or (cur.get("startedAt") or 0) > (best[0].get("startedAt") or 0):
            best = (cur, p)
    if best is None:
        return None
    cur, p = best
    try:
        n = int(cur.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    running = not cur.get("endedAt") and bool(alive(p.get("ptyId")))
    return {"n": n or None, "running": running,
            "verdict": "" if running else (cur.get("verdict") or "")}


def row_brief(room: dict, cold: bool = False) -> dict:
    """What a board card shows when opened, from the room alone (no git, no
    model): goal, its source, where it stands, the latest report and review."""
    goal = (room.get("goal") or "").strip()
    src = room.get("goalSource") or ("po" if goal else "")
    if not goal:
        goal, src = spec_sentence(room.get("spec") or ""), "spec"
        if not goal:
            src = ""
    st = room.get("stateNote")
    state = ({"text": st.get("text", ""), "by": st.get("by", ""), "at": st.get("at") or 0}
             if isinstance(st, dict) and st.get("text") else None)
    rep = room.get("lastReport")
    report = ({"kind": rep.get("kind", ""), "at": rep.get("ts") or 0, "by": rep.get("identity", ""),
               "msg": rep.get("messageId", "")}
              if isinstance(rep, dict) and rep.get("messageId") else None)
    alive = (lambda _pid: False) if cold else _d._pty_alive
    try:
        review = _review_view(room, alive)
    except Exception:
        review = None
    return {"goal": goal, "goalSource": src, "state": state, "report": report, "review": review}


# ---- The goal backfill ------------------------------------------------------

_PROMPT = (
    "Each task below is a brief for a software team. For each one write its "
    "goal: what the task is meant to achieve, for someone glancing at a task "
    "board. One or two short plain sentences, under 200 characters, no "
    "Markdown, no names of people. Use only what the brief says. Answer with a "
    "JSON object only, mapping each task's key (t1, t2, ...) to its goal.\n\n"
)


def _po_rooms() -> set:
    try:
        return {p.get("poRoomId") for p in _d.load_projects() if p.get("poRoomId")}
    except Exception:
        return set()


def needs_goal(room: dict, po_rooms: set, now: float) -> bool:
    """A task the backfill writes a goal for: a numbered task with a spec and
    no goal, not a PO's room, not Done for longer than DONE_RECENT_S, and not
    one the model already passed over with the same spec (mark_tried)."""
    if (room.get("goal") or "").strip() or not room.get("no") or room.get("id") in po_rooms:
        return False
    if not (room.get("spec") or "").strip():
        return False
    tried = room.get("goalTried")
    if isinstance(tried, dict) and tried.get("spec") == spec_key(room.get("spec") or ""):
        return False
    if (room.get("workflow") or "").lower() == "done" and (room.get("updatedAt") or 0) < now - DONE_RECENT_S:
        return False
    return True


def backfill_candidates() -> list[dict]:
    now = time.time()
    po = _po_rooms()
    rooms = [r for r in _d.chatroom.list_rooms(copy_rooms=False) if needs_goal(r, po, now)]
    # The ones on the board's working columns first, newest first.
    rooms.sort(key=lambda r: ((r.get("workflow") or "").lower() == "done", -(r.get("updatedAt") or 0)))
    return rooms[:BACKFILL_MAX_TASKS]


def ask_model(batch: list[dict]) -> dict[str, str] | None:
    """{room id: goal} from one model call over ``batch``, or None when the
    call failed or its answer was not the JSON asked for."""
    bin_path = _d._claude_bin()
    if not bin_path:
        return None
    keys = {f"t{i + 1}": r for i, r in enumerate(batch)}
    text = _PROMPT + "\n\n".join(
        f"{k}: {r.get('title', '')}\n{(r.get('spec') or '')[:BACKFILL_SPEC_CHARS]}" for k, r in keys.items())
    workspace = _d.DASHBOARD_DIR / "_rename_workspace"
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(
            [bin_path, "-p", "--model", _d.digest._model(), "--no-session-persistence",
             "--tools", "", "--strict-mcp-config"],
            input=text, cwd=str(workspace), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_d.digest.MODEL_TIMEOUT_S, creationflags=_d.digest._NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_answer(out.stdout if out.returncode == 0 else "", keys)


def parse_answer(answer: str, keys: dict) -> dict[str, str] | None:
    s = answer or ""
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        got = json.loads(s[a:b + 1])
    except ValueError:
        return None
    if not isinstance(got, dict):
        return None
    return {keys[k]["id"]: v for k, v in got.items() if k in keys and isinstance(v, str) and v.strip()}


def backfill(ask=None, sleep=time.sleep) -> dict:
    """One pass: a goal for every task that needs one (needs_goal), in batches,
    never over a goal written meanwhile. ``ask`` stands in for ask_model in
    tests. Returns {written, failed, stopped}."""
    ask = ask or ask_model
    todo = backfill_candidates()
    written = failed = fails_in_row = 0
    for i in range(0, len(todo), BACKFILL_BATCH):
        batch = todo[i:i + BACKFILL_BATCH]
        try:
            got = ask(batch)
        except Exception:
            got = None
        if not got:
            failed += len(batch)
            fails_in_row += 1
            if fails_in_row >= BACKFILL_MAX_FAILS:
                return {"written": written, "failed": failed, "stopped": True}
        else:
            fails_in_row = 0
            for r in batch:
                if r["id"] in got and set_auto_goal(r, got[r["id"]]):
                    written += 1
                elif r["id"] not in got or not clean(got[r["id"]])[0]:
                    mark_tried(r)
        if i + BACKFILL_BATCH < len(todo):
            sleep(BACKFILL_GAP_S)
    return {"written": written, "failed": failed, "stopped": False}


def start_backfill() -> None:
    """The once-per-start pass, on its own thread after BACKFILL_DELAY_S."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True

    def run():
        time.sleep(BACKFILL_DELAY_S)
        try:
            res = backfill()
            if res["written"] or res["failed"]:
                print(f"[goal] task goals written: {res['written']}, not written: {res['failed']}"
                      + (" (the model did not answer; stopped)" if res["stopped"] else ""), flush=True)
        except Exception as e:
            print(f"[goal] task goal backfill failed: {e!r}", flush=True)
    threading.Thread(target=run, name="goal-backfill", daemon=True).start()
