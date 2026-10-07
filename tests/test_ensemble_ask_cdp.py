"""The structured and fallback cards at desktop and phone sizes."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import asks
import chatroom
import dashboard
import ensemble_tools
import points
import sends
from test_asks import CDP_JS
from test_page_update import CHROME
from test_po_chat_clean import NODE, _turn
from tests import chrome_profile


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-asktool-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        (base / "transcripts" / "C--po").mkdir(parents=True)
        (base / "cs").mkdir()
        cls.sent = []

        def resume(h, room, text="", to="", key="", quiet=False):
            cls.sent.append((text, key))
            sends.mark(room["id"], [key], "delivered")
            return {"delivered": 1}

        for patch in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "_resume_room", resume),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, project, _ = dashboard.register_project("Cards")
        assert ok, project
        po = chatroom.create_room("Cards PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": "po-sid"}])
        cls.rid = po["id"]
        room = chatroom.get_room(cls.rid, public=False)
        room["mode"] = "solo"
        chatroom.update_room(room)
        dashboard.assign_session_project(cls.rid, project["id"])
        ok, why = dashboard.set_project_po(project["id"], cls.rid)
        assert ok, why
        t0 = time.time() - 300
        (base / "transcripts" / "C--po" / "po-sid.jsonl").write_text(
            "".join(json.dumps(t) + "\n" for t in [
                _turn("user", "[rotation] PO brief.", t0),
                _turn("assistant", "Ready for decisions.", t0 + 1)]), encoding="utf-8")
        room = chatroom.get_room(cls.rid, public=False)
        context = {"room": room, "identity": "claude", "part": room["participants"][0], "projectId": project["id"]}
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler._ring_recipients = lambda *a: []
        cls.tool = ensemble_tools._ask(context, {"context": "Release timing.", "questions": [
            {"question": "When should this go live?", "options": [
                {"label": "Now", "recommended": True}, {"label": "Later"}]}]}, handler)
        fallback = asks.safety("Decision needed: Run the backup tonight?")
        cls.safety = chatroom.post_message(cls.rid, "claude", "Decision needed: Run the backup tonight?",
                                           to="user", structured_asks=fallback)["message"]["id"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base),
                "base": f"http://127.0.0.1:{server.server_address[1]}", "po": cls.rid, "shots": shots}
        script = CDP_JS.replace("[[1280, 800, false, 0, '30 days'], [390, 844, true, 1, 'Yes']]",
                                "[[1728, 1117, false, 0, 'Now'], [390, 844, true, 1, 'Yes']]")
        script = script.replace('querySelectorAll("#msgs .qa").length >= 3', 'querySelectorAll("#msgs .qa").length >= 2')
        script = script.replace('cards: document.querySelectorAll(\'#msgs .qa\').length,',
                                'approve: document.querySelectorAll(\'#msgs .pt-approve\').length, cards: document.querySelectorAll(\'#msgs .qa\').length,')
        path = base / "asktool_cdp.js"
        path.write_text(script, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])
        cls.ledger = points.load(cls.rid)

    def test_cards_fit_and_answer_in_one_click(self):
        desktop, mobile = self.got["1728"], self.got["390"]
        for result in (desktop, mobile):
            self.assertEqual(result["before"]["cards"], 2)
            self.assertLessEqual(result["before"]["scrollX"], 1)
            self.assertTrue(result["before"]["inside"])
            self.assertLessEqual(result["after"]["scrollX"], 1)
            self.assertTrue(result["card"]["disabled"])
        self.assertEqual(desktop["card"]["pressed"], ["Now"])
        self.assertGreaterEqual(desktop["before"]["approve"], 1)
        self.assertEqual(mobile["card"]["pressed"], ["Yes"])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({p["state"] for p in self.ledger["points"]}, {"acked"})
