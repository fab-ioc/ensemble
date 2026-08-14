"""Platform backend selection for claude-dashboard."""
from __future__ import annotations

import sys

from .base import Backend

_BACKEND: Backend | None = None


def get_backend() -> Backend:
    """Return the singleton backend for the current platform."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    if sys.platform == "darwin":
        from .macos import MacBackend
        _BACKEND = MacBackend()
    elif sys.platform == "win32":
        from .windows import WindowsBackend
        _BACKEND = WindowsBackend()
    else:
        from .linux import LinuxBackend
        _BACKEND = LinuxBackend()
    return _BACKEND
