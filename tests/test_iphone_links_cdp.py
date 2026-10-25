"""CDP regression for PO chat navigation with real touch sequences (#268).

The phone cases suppress Chrome's compatibility click, the failure mode seen
on iOS Safari. The page must still activate a short touch. The same controls
are exercised with a mouse at desktop width. The fixture uses an isolated hub.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import points
import threads
from tests import chrome_profile
from tests.test_task_numbers import Hub
from tests.test_threads_page import _turn


ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
CHROME = chrome_profile.CHROME


@unittest.skipUnless(NODE and CHROME, "Node and Chrome are required")
class IPhoneLinks(Hub):
    def test_chat_controls_reach_visible_cards_tasks_and_threads(self):
        base = Path(self.tmp.name)
        self.tmp._ignore_cleanup_errors = True
        for attr, val in (("load_live", lambda: []), ("_read_agent_session_files", lambda *a, **k: [])):
            p = mock.patch.object(dashboard, attr, val)
            p.start(); self.addCleanup(p.stop)
        for attr, val in (("_agent_peer", lambda h: ""), ("log_message", lambda *a, **k: None)):
            p = mock.patch.object(dashboard.Handler, attr, val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, "SETTINGS_FILE", base / "settings.json")
        p.start(); self.addCleanup(p.stop)
        tdir = base / "transcripts" / "C--po"
        tdir.mkdir(parents=True)
        p = mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts")
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, "CS_ROOT", base / "cs")
        p.start(); self.addCleanup(p.stop)
        sid = "iphonepo"
        room = chatroom.create_room("PO", [{"identity": "claude", "agent": "claude", "role": "Product owner", "sessionId": sid}])["id"]
        full = chatroom.get_room(room, public=False)
        full["mode"] = "solo"
        chatroom.update_room(full)
        dashboard.assign_session_project(room, self.ot)
        dashboard.set_project_po(self.ot, room)
        task = self.room("Tap target task", self.ot, numbered=True)
        task_no = self.no(task)
        t0 = time.time() - 300
        question, ids = points.take(chatroom.get_room(room, public=False), "Please keep this in a thread.",
                                    key="iphone-thread", now=t0, sid=sid)
        self.assertEqual(ids, ["P1"])
        source_id = f"{sid}:1"
        card = chatroom.post_message(room, "claude", "A question card for the reference.", to="user")["message"]
        full = chatroom.get_room(room, public=False)
        full["messages"] = [{**m, "asks": [{"n": 0, "question": "Open the linked card?", "kind": "decision",
                                              "options": [{"label": "Open", "detail": "", "recommended": True},
                                                          {"label": "Later", "detail": "", "recommended": False}]}],
                             "askAudience": "user"} if m["id"] == card["id"] else m for m in full["messages"]]
        chatroom.update_room(full)
        # Keep the linked card outside the room's initial 50-message window.
        for n in range(55):
            chatroom.post_message(room, "claude", f"Later room message {n}.", to="user")
        threads._ensure_since(room, t0 - 60)
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        ref = f"{base_url}/session?room={room}&msg={card['id']}&part=ask:0"
        status_id = f"{sid}:4"
        point_status_id = f"{sid}:5"
        lines = [_turn(sid, "assistant", "A channel for the project.", t0 - 1),
                 _turn(sid, "user", question, t0 + 1),
                 _turn(sid, "assistant", "Re P1: the thread has an answer.", t0 + 2),
                 _turn(sid, "user", "A separate request about navigation.", t0 + 3),
                 _turn(sid, "user", f"Task #{task_no} and {ref}", t0 + 4),
                 _turn(sid, "user", "Point P1 is still useful.", t0 + 5)]
        (tdir / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        points.sync(room, force=True)
        idx = threads.build(room, show_progress=False)
        for mid in (status_id, point_status_id):
            self.assertIn(mid, idx["channel"], str({"mid": mid, "of": idx["of"].get(mid), "parent": idx["parent"].get(mid)}))
        p = mock.patch.object(points, "sync", side_effect=lambda rid, **kw: points.load(rid))
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(threads, "mark_seen", return_value=False)
        p.start(); self.addCleanup(p.stop)
        self.addCleanup(points._CACHE.clear)
        def add_during_press():
            for width in (440, 390, 1728):
                signal = base / f"poll-signal-{width}"
                deadline = time.monotonic() + 240
                while not signal.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                if not signal.exists():
                    return
                chatroom.post_message(room, "claude", f"Polling update during touch {width}.", to="user")
                (base / f"poll-done-{width}").write_text("ready", encoding="utf-8")
        threading.Thread(target=add_during_press, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": base_url, "room": room,
                "task": task, "source": source_id, "card": card["id"], "status": status_id,
                "pointStatus": point_status_id}
        script = ROOT / "tests" / "iphone_links_cdp.js"
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-5000:] + "\n" + run.stdout[-3000:])
        got = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual([row["size"] for row in got], [[440, 956], [390, 844], [1728, 1117]])
        for row in got:
            with self.subTest(size=row["size"]):
                self.assertTrue(row["coarse"] == (row["size"][0] < 700), row)
                for name in ("pointCard", "pointTarget", "referenceTarget", "thread", "threadList", "threadRow", "replyButton",
                             "taskCard", "redrawCard", "pollApplied", "taskOpen"):
                    self.assertTrue(row[name], f"{name}: {row}")
                self.assertTrue(row["referenceWasOlder"], row)
                if row["coarse"]:
                    for name in ("pointSize", "referenceSize", "replySize", "taskSize"):
                        self.assertGreaterEqual(min(row[name]), 44, f"{name}: {row}")
                    self.assertEqual(len(row["reactionTargets"]), 2, row)
                    for target in row["reactionTargets"]:
                        self.assertGreaterEqual(min(target["w"], target["h"]), 44, row)
                        self.assertTrue(target["hit"], row)
                        self.assertGreaterEqual(target["gap"], 0, row)
                        self.assertEqual(target["pseudo"], "none", row)
                    self.assertTrue(row["drawHeld"], row)


if __name__ == "__main__":
    unittest.main()
