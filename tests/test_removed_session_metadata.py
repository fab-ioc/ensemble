"""The retired session grouping and quick-access metadata stay retired."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402

NODE = shutil.which("node")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")


class RemovedSurface(unittest.TestCase):
    def test_retired_names_are_absent_from_served_code(self):
        paths = [ROOT / name for name in ("index.html", "session.html", "fileview.html", "dashboard.py")]
        paths.extend((ROOT / "static").glob("*.js"))
        needles = ("categor", "pinned", "pin-btn", "selected_cat", "/api/categories", "/api/pin")
        for path in paths:
            text = path.read_text(encoding="utf-8").lower()
            for needle in needles:
                self.assertNotIn(needle, text, f"{path.name}: {needle}")

    def _call(self, method: str, path: str, body=None) -> int:
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.headers = {
            "Content-Length": str(len(raw)),
            "Content-Type": "application/json",
            "Host": "127.0.0.1:8798",
            "Origin": "http://127.0.0.1:8798",
            "Cookie": f"ensemble_ui_8798={dashboard._UI_KEY}",
        }
        h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", 8798))
        h.log_message = lambda *args: None
        getattr(h, f"do_{method}")()
        head = h.wfile.getvalue().partition(b"\r\n\r\n")[0]
        return int(head.split(b" ", 2)[1])

    def test_retired_endpoints_are_not_found(self):
        calls = [
            ("GET", "/api/pinned", None),
            ("GET", "/api/categories", None),
            ("GET", "/api/category-list", None),
            ("PUT", "/api/categories/s1", {"category": "old"}),
            ("POST", "/api/pinned", {"sessionId": "s1", "pinned": True}),
            ("POST", "/api/category-list", {"name": "old"}),
            ("POST", "/api/categories/rename", {"from": "old", "to": "new"}),
            ("POST", "/api/categories/delete", {"category": "old"}),
        ]
        for method, path, body in calls:
            with self.subTest(method=method, path=path):
                self.assertEqual(self._call(method, path, body), 404)


class SessionRows(unittest.TestCase):
    def test_old_sidecars_are_ignored_and_rows_omit_their_fields(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state = base / "state"
            transcripts = base / "transcripts"
            scratch = base / "scratch"
            state.mkdir()
            transcripts.mkdir()
            scratch.mkdir()
            # Deliberately invalid: reading either retired file would fail.
            (state / "pinned.json").write_text("{", encoding="utf-8")
            (state / "categories.json").write_text("{", encoding="utf-8")
            (state / "known_categories.json").write_text("{", encoding="utf-8")
            room = {
                "id": "room-12345678", "title": "Old metadata", "participants": [],
                "messages": [], "createdAt": 1, "updatedAt": 2, "launched": False,
            }
            empty_agent = SimpleNamespace(list_sessions=lambda limit: [])
            patches = [
                mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                mock.patch.object(dashboard, "PROJ_DIR", transcripts),
                mock.patch.object(dashboard, "CS_ROOT", scratch),
                mock.patch.object(dashboard, "load_live", return_value=[]),
                mock.patch.object(dashboard, "load_labels", return_value={}),
                mock.patch.object(dashboard, "load_parents", return_value={}),
                mock.patch.object(dashboard, "load_archived", return_value=set()),
                mock.patch.object(dashboard, "load_jira_links", return_value={}),
                mock.patch.object(dashboard, "load_jira_unlinks", return_value={}),
                mock.patch.object(dashboard.agents, "get_agent", return_value=empty_agent),
                mock.patch.object(dashboard, "_read_agent_session_files", return_value=[]),
                mock.patch.object(dashboard.chatroom, "list_rooms", return_value=[room]),
                mock.patch.object(dashboard, "_room_is_live", return_value=False),
                mock.patch.object(dashboard, "compute_room_cost", return_value={"dollars": 0}),
                mock.patch.object(dashboard.attention, "by_room", return_value={}),
            ]
            with mock.patch("builtins.print") as printed:
                with ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    rows = dashboard._load_sessions_uncached()
            self.assertEqual(len(rows), 1)
            self.assertNotIn("pinned", rows[0])
            self.assertNotIn("category", rows[0])
            printed.assert_not_called()


@unittest.skipUnless(NODE, "node is not installed")
class StoredFilters(unittest.TestCase):
    def test_a_retired_only_stored_filter_falls_back_to_the_current_defaults(self):
        start = INDEX.index("const FILTER_KEYS = ")
        end = INDEX.index("\nlet FILTERS = loadFilters();", start) + len("\nlet FILTERS = loadFilters();")
        source = INDEX[start:end]
        script = f"""
const localStorage = {{
  getItem: () => JSON.stringify(['pinned']),
  setItem: () => {{}}
}};
{source}
console.log(JSON.stringify([...FILTERS]));
"""
        proc = subprocess.run(
            [NODE, "-e", script], capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), ["live", "history"])


if __name__ == "__main__":
    unittest.main()
