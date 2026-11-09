"""The 2026-11-09 retransmissions from a resumed, October-dated session.

The point texts and timestamps below preserve the observed user turns. Agent
answers are shortened to avoid carrying unrelated conversation into a fixture.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import points
import sends
from tests.test_never_dropped import Harness


REAL_READ_SESSION_TURNS = dashboard.read_session_turns
REAL_SESSION_STAT = points._session_stat
SID = "01a18c09-cc9b-7df3-ab34-a79442c0dbd7"
MESSAGES = [
    ("P11", "2026-11-09T15:44:51.141Z", "2026-11-09T15:44:52.813Z",
     "2026-11-09T15:48:06.256Z", "can you check why epyc-32 is so high in CPU ?\n\n[point P11]"),
    ("P12", "2026-11-09T15:48:55.032Z", "2026-11-09T15:48:56.111Z",
     "2026-11-09T15:49:44.606Z", "## Review comments (1)\n\n**1.** > Windows Defender\n\n"
     "can thie be disabled when running tests ? and on anything related to opten and ensemble platforms ?"
     "\n\n[point P12]"),
    ("P13", "2026-11-09T17:12:28.766Z", "2026-11-09T17:12:30.045Z",
     "2026-11-09T17:12:33.359Z", "again ?\n\n[point P13]"),
]


def epoch(ts: str) -> float:
    return dashboard._turn_epoch(ts)


class OldTranscriptMtime(Harness):
    def _replay(self, agent: str):
        rid = self.room(agents=(agent,))
        room = chatroom.get_room(rid, public=False)
        part = next(p for p in room["participants"] if p.get("kind") == "agent")
        part["sessionId"] = SID
        part["rotations"] = [{"fromSessionId": "01a1059e-5bf5-7642-a5ad-dad989437ed4",
                              "toSessionId": SID, "at": epoch("2026-10-30T09:00:10.312Z")}]
        chatroom.update_room(room)
        root = Path(self.temp.name)
        if agent == "codex":
            path = root / "sessions/2026/10/30" / f"rollout-2026-10-30T10-00-14-{SID}.jsonl"
        else:
            path = root / "projects/old" / f"{SID}.jsonl"
        path.parent.mkdir(parents=True)

        lines = ([{"timestamp": "2026-10-30T09:00:15.349Z", "type": "session_meta",
                   "payload": {"id": SID}}] if agent == "codex" else [
                       {"timestamp": "2026-10-30T09:00:15.349Z", "type": "user",
                        "message": {"role": "user", "content": "earlier conversation"}}])
        for point, sent, received, answered, text in MESSAGES:
            sends.accept(rid, f"send:{point}", text, now=epoch(sent))
            sends.mark(rid, [f"send:{point}"], "delivered", now=epoch(sent))
            reply = ("Re P13: Yes—the hub retransmitted the previous message. "
                     "No new check or Defender change was performed." if point == "P13"
                     else f"Re {point}: answered.")
            if agent == "codex":
                for ts, role, body in ((received, "user", text), (answered, "assistant", reply)):
                    lines.append({"timestamp": ts, "type": "response_item", "payload": {
                        "type": "message", "role": role, "content": [{"type": "input_text" if role == "user"
                                                               else "output_text", "text": body}]}})
            else:
                for ts, role, body in ((received, "user", text), (answered, "assistant", reply)):
                    lines.append({"timestamp": ts, "type": role, "message": {
                        "role": role, "content": [{"type": "text", "text": body}]}})
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        old = epoch("2026-10-30T09:00:15.349Z")
        os.utime(path, (old, old))
        self.assertLess(path.stat().st_mtime, epoch(MESSAGES[0][1]))
        sends._SCANNED.clear()

        with mock.patch.object(dashboard, "read_session_turns", REAL_READ_SESSION_TURNS), \
                mock.patch.object(points, "_session_stat", REAL_SESSION_STAT), \
                mock.patch.object(dashboard, "kept_session", lambda sid: None), \
                mock.patch.object(dashboard, "find_transcript",
                                  lambda sid, **kw: path if agent == "claude" and sid == SID else None):
            if agent == "codex":
                codex = dashboard.agents.get_agent("codex")
                home = root / "sessions"
                with mock.patch.object(codex, "sessions_dir", lambda: home):
                    self._assert_no_retry(rid, path)
            else:
                self._assert_no_retry(rid, path)

    def _assert_no_retry(self, rid: str, path: Path):
        now = epoch("2026-11-09T17:14:49.508Z")
        self.assertEqual(len([t for t in dashboard.read_session_turns(SID) or []
                              if t.get("role") == "user" and "[point P13]" in t.get("text", "")]), 1)
        self.assertLess(path.stat().st_mtime, epoch(MESSAGES[0][1]))
        sends._redeliver_room(rid, now)
        for point, *_ in MESSAGES:
            self.assertEqual(sends.get(rid, f"send:{point}")["state"], "confirmed", point)
        self.assertEqual(self.ptys["pty-live-0"].typed, [])

        # An absent message still takes the original retry path, exactly once.
        missing_at = epoch("2026-11-09T17:12:35.000Z")
        sends.accept(rid, "send:missing", "a message that never arrived\n\n[point P99]", now=missing_at)
        sends.mark(rid, ["send:missing"], "delivered", now=missing_at)
        sends._redeliver_room(rid, now)
        self.assertEqual(len(self.ptys["pty-live-0"].typed), 1)
        self.assertIn("a message that never arrived", self.ptys["pty-live-0"].typed[0])
        sends._redeliver_room(rid, now + sends.REDELIVER_AFTER_S + 1)
        self.assertEqual(len(self.ptys["pty-live-0"].typed), 1)
        self.assertEqual(sends.get(rid, "send:missing")["state"], "failed")

    def test_codex_rollout_in_old_date_folder_was_already_answered(self):
        self._replay("codex")

    def test_claude_transcript_with_old_file_time_was_already_answered(self):
        self._replay("claude")

