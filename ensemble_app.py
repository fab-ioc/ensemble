"""The entry point of the built app (Ensemble.exe, Ensemble.app).

A built app has no Python next to it, so the scripts the hub starts as
processes of their own (the agents' hooks and status line, the searches) run
through the app itself: ``Ensemble --run agent_hook ...`` is
``python agent_hook.py ...`` (see ``app_version.script_argv``). Anything else
starts the hub, with ``dashboard.py``'s own arguments.
"""
from __future__ import annotations

import multiprocessing
import os
import sys


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# Each script's ``if __name__ == "__main__"`` block, as a function.
def _agent_hook(argv: list[str]) -> None:
    import agent_hook
    # Straight out, without the interpreter's teardown: the agent is waiting.
    os._exit(agent_hook.main())


def _task_tool_hook(argv: list[str]) -> None:
    import task_tool_hook
    _utf8_stdio()
    sys.exit(task_tool_hook.main())


def _usage_statusline(argv: list[str]) -> None:
    import usage_statusline
    sys.exit(usage_statusline.main(argv))


def _workspace_search(argv: list[str]) -> None:
    import workspace_search
    sys.exit(workspace_search.main())


def _global_search(argv: list[str]) -> None:
    import global_search
    global_search.main()


def _app_update(argv: list[str]) -> None:
    import app_update
    sys.exit(app_update.main(argv))


SCRIPTS = {
    "app_update": _app_update,
    "agent_hook": _agent_hook,
    "task_tool_hook": _task_tool_hook,
    "usage_statusline": _usage_statusline,
    "workspace_search": _workspace_search,
    "global_search": _global_search,
}


def main() -> None:
    # The global search scans with worker processes, which a built app starts
    # as itself: this answers them before anything else runs.
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--run":
        run = SCRIPTS.get(args[1])
        if run is None:
            print(f"unknown script: {args[1]}", file=sys.stderr)
            sys.exit(2)
        sys.argv = [args[1] + ".py", *args[2:]]
        run(sys.argv)
        return
    import app_launch
    sys.exit(app_launch.main(args))


if __name__ == "__main__":
    main()
