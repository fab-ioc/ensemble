"""Where a starting hub is, for GET /api/health and the start-up page (ED-227).

The hub listens from its first second and works through its start in this
order:

* ``starting``: the steps that must finish before anything else is answered
  (migrations, the PTY host, the saved start-up state). Only the start-up
  page, its files and /api/health (also /api/version and /api/platform) are
  answered; a page asks get the start-up page, a page's GET /api/... 503,
  and every other request waits for the end of this phase.
* ``warming``: the first task list is being read. Everything is answered;
  the board's page is the start-up page until the list is there.
* ``ready``.

A process that never calls ``begin`` (tests, tools importing the dashboard)
is ready from the start.
"""
from __future__ import annotations

import threading
import time

import progress

_LOCK = threading.Lock()
_BOOTED = threading.Event()
_BOOTED.set()
_state = {"phase": "ready", "step": "", "stepNo": 0, "steps": 0,
          "startedAt": time.time(), "phaseAt": time.time(), "readyAt": 0.0,
          "progressKey": ""}
# How long the steps before the first list took at the last start, for an
# estimate before any count is known.
_TYPICAL_S = {"starting": 10.0, "warming": 30.0}


def begin(steps: int) -> None:
    """The hub's main starts: nothing but the start-up page until booted()."""
    _BOOTED.clear()
    now = time.time()
    with _LOCK:
        _state.update(phase="starting", step="Starting", stepNo=0, steps=steps,
                      startedAt=now, phaseAt=now, readyAt=0.0, progressKey="")


def step(name: str) -> None:
    with _LOCK:
        _state["step"] = name
        _state["stepNo"] += 1
    print(f"[startup] {name}", flush=True)


def booted() -> None:
    """Every request may be answered now; the first task list is next."""
    with _LOCK:
        if _state["phase"] == "starting":
            _state.update(phase="warming", step="Reading the task list", phaseAt=time.time())
    _BOOTED.set()


def warming(step_name: str, progress_key: str = "") -> None:
    with _LOCK:
        _state["step"] = step_name
        _state["progressKey"] = progress_key


def ready() -> None:
    now = time.time()
    with _LOCK:
        if _state["phase"] != "ready":
            _state.update(phase="ready", step="", readyAt=now, progressKey="")
            print(f"[startup] ready after {now - _state['startedAt']:.1f}s", flush=True)
    _BOOTED.set()


def is_booted() -> bool:
    return _BOOTED.is_set()


def is_ready() -> bool:
    return _state["phase"] == "ready"


def wait_booted(timeout: float) -> bool:
    return _BOOTED.wait(timeout)


def health() -> dict:
    """What GET /api/health says: the phase, the step, counts where the
    step has them, an estimate of the time left, and how long it has run."""
    now = time.time()
    with _LOCK:
        s = dict(_state)
    out = {"phase": s["phase"], "step": s["step"], "uptime": round(now - s["startedAt"], 1)}
    if s["phase"] == "ready":
        out["readyAfter"] = round(s["readyAt"] - s["startedAt"], 1) if s["readyAt"] else 0
        return out
    if s["phase"] == "starting":
        out.update(done=max(0, s["stepNo"] - 1), total=s["steps"], unit="steps")   # steps finished
    work = progress.snapshot().get(s["progressKey"]) if s["progressKey"] else None
    if work and work.get("total"):
        out.update(done=work["done"], total=work["total"], unit="files",
                   step=work.get("phase") or s["step"])
    out["eta"] = _eta(s, out, now)
    return out


def _eta(s: dict, out: dict, now: float) -> int | None:
    """Seconds left in this phase, from the rate so far; a rough guess
    before there is a count. None when there is no fair guess."""
    spent = now - s["phaseAt"]
    done, total = out.get("done") or 0, out.get("total") or 0
    if total and done >= max(3, total * 0.02) and spent > 1:
        return max(1, round((total - done) * spent / done))
    typical = _TYPICAL_S.get(s["phase"], 0)
    return max(1, round(typical - spent)) if spent < typical else None
