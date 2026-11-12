"""Claude Code agent adapter.

Claude is the dashboard's original (and default) agent. Discovery of Claude
sessions is already implemented in `dashboard.load_sessions` against
`~/.claude/projects/<slug>/<id>.jsonl` transcripts plus the live pid-registry
at `~/.claude/sessions/<pid>.json`. To avoid duplicating that logic (and the
circular import it would create), `list_sessions` here is intentionally left
delegating to the existing loader when the adapter is wired into the HTTP
layer; this class currently owns availability + launch/resume only.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from backends.shared import claude_cmd_args

from .base import AgentSession, AgentType


class ClaudeAgent(AgentType):
    key = "claude"
    display_name = "Claude"

    def installed(self) -> bool:
        return shutil.which("claude") is not None

    def ensure_trusted(self, cwd: str) -> bool:
        """Pre-accept Claude's workspace-trust dialog for ``cwd`` so an
        unattended launch doesn't block on the "Is this a folder you trust?"
        prompt (which is separate from --permission-mode). Claude records trust
        as ``projects["<path>"].hasTrustDialogAccepted = true`` in
        ~/.claude.json (``$CLAUDE_CONFIG_DIR/.claude.json`` when that is set),
        keyed by the forward-slash path. Claude 2.1 on Linux shows the dialog
        in every new worktree ("Is this a project you created or one you
        trust?", default "No, exit") and skips it for this entry (ED-340). The
        real path is the one Claude sees as its working folder, so that is
        trusted too when a link leads there. Idempotent."""
        if not cwd:
            return False
        conf = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
        cfg = (Path(conf) if conf else Path.home()) / ".claude.json"
        keys = []
        for k in (str(Path(cwd)), os.path.realpath(cwd)):
            k = k.replace("\\", "/")
            if k not in keys:
                keys.append(k)
        try:
            data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
        except (json.JSONDecodeError, OSError):
            return False
        if not isinstance(data, dict):
            return False
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return False
        todo = [k for k in keys if not (isinstance(projects.get(k), dict)
                                        and projects[k].get("hasTrustDialogAccepted") is True)]
        if not todo:
            return True
        for key in todo:
            entry = projects.get(key)
            if not isinstance(entry, dict):
                entry = {}
            entry["hasTrustDialogAccepted"] = True
            # Claude expects a few companion flags on a project entry; set the
            # ones that gate first-run prompts so the session starts clean.
            entry.setdefault("hasCompletedProjectOnboarding", True)
            projects[key] = entry
        try:
            tmp = cfg.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(cfg)
            return True
        except OSError:
            return False

    def list_sessions(self, limit: int = 200) -> list[AgentSession]:
        # Claude discovery stays in dashboard.load_sessions for now (it carries
        # cost/Jira/label enrichment the UI depends on). This adapter method is
        # a placeholder so the registry has a uniform interface; the wiring
        # increment routes Claude rows through the existing loader.
        return []

    def launch_argv(self, cwd: str, prompt: str = "",
                    extra: list[str] | None = None) -> list[str]:
        parts = list(extra) if extra else []
        if prompt:
            # Claude takes an initial prompt positionally.
            parts.append(prompt)
        return claude_cmd_args(*parts)

    def resume_argv(self, session_id: str,
                    extra: list[str] | None = None) -> list[str]:
        parts = ["--resume", session_id]
        if extra:
            parts += list(extra)
        return claude_cmd_args(*parts)
