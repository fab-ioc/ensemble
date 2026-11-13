"""ED-355: one session id in two project folders resolves to the live copy.

A Tasse Ticino PO's conversation was held both in its Linux project folder
(written now) and in a Windows-era folder (stale since 11-11). The hub kept the
stale one, so the PO's chat showed none of its turns and every message was
typed again."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import lost_sessions
import points
import rotation
import sends
from tests.test_never_dropped import Harness

REAL_FIND = dashboard.find_transcript
REAL_READ_SESSION_TURNS = dashboard.read_session_turns
REAL_SESSION_STAT = points._session_stat
SID = "003d5fe9-deb6-4716-baf6-45e94631dc57"
STALE_DIR = "C--Users-fabio-EnsembleProjects-Tasse-Ticino-code"
LIVE_DIR = "-home-fab-EnsembleProjects-Tasse-Ticino-code"
LIVE_CWD = "/home/fab/EnsembleProjects/Tasse-Ticino/code"


def forget() -> None:
    for d in (dashboard._TRANSCRIPT_PATHS, dashboard._TRANSCRIPT_MISSES, dashboard._TRANSCRIPT_COPIES,
              dashboard._TRANSCRIPT_GROWTH):
        d.clear()
    dashboard._TRANSCRIPT_INDEX = ("", 0.0, {})
    dashboard.CLAUDE_FILES.forget()


def write(path: Path, rows: list[dict], at: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    os.utime(path, (at, at))
    return path


def turn(role: str, text: str, ts: str) -> dict:
    return {"type": role, "timestamp": ts, "message": {"role": role, "content": [{"type": "text", "text": text}]}}


class Copies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.proj = Path(self.temp.name) / "projects"
        self.proj.mkdir()
        self.patches = [mock.patch.object(dashboard, "PROJ_DIR", self.proj)]
        for p in self.patches:
            p.start()
        forget()
        now = time.time()
        self.stale = write(self.proj / STALE_DIR / f"{SID}.jsonl",
                           [turn("user", "old", "2026-11-11T07:36:00Z")], now - 2 * 86400)
        self.live = write(self.proj / LIVE_DIR / f"{SID}.jsonl",
                          [turn("user", "old", "2026-11-11T07:36:00Z"),
                           turn("user", "new", "2026-11-13T08:00:00Z")], now - 60)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        forget()
        self.temp.cleanup()

    def test_the_newer_of_two_copies_is_chosen(self):
        with mock.patch("builtins.print"):
            self.assertEqual(dashboard.find_transcript(SID), self.live)
        self.assertEqual(dashboard._transcript_index()[SID], self.live)
        self.assertEqual(sorted(dashboard._TRANSCRIPT_COPIES[SID]), sorted([self.stale, self.live]))
        # Its size and time are the live copy's too (points' sync).
        self.assertEqual(dashboard.transcript_stats()[SID][0], self.live.stat().st_size)
        # The glob a lookup falls back on picks the newest as well.
        forget()
        with mock.patch.object(dashboard, "_transcript_index", lambda *a, **k: {}):
            self.assertEqual(dashboard.find_transcript(SID), self.live)

    def test_the_seat_folder_copy_wins_a_tie(self):
        at = self.live.stat().st_mtime
        os.utime(self.stale, (at, at))
        with mock.patch("builtins.print"):
            dashboard.find_transcript(SID)
        self.assertEqual(dashboard.find_transcript(SID, cwd=LIVE_CWD), self.live)
        self.assertEqual(dashboard.find_transcript(SID, cwd="C:\\Users\\fabio\\EnsembleProjects\\Tasse-Ticino\\code"),
                         self.stale)
        # Not when the seat's folder holds the older copy.
        os.utime(self.stale, (at - 100, at - 100))
        self.assertEqual(dashboard.find_transcript(SID, cwd="C:\\Users\\fabio\\EnsembleProjects\\Tasse-Ticino\\code"),
                         self.live)
        # rotation asks with the seat's folder.
        with mock.patch.object(dashboard, "find_transcript", return_value=self.live) as ft:
            rotation._transcript_of({"agent": "claude", "sessionId": SID, "cwd": LIVE_CWD})
        ft.assert_called_once_with(SID, cwd=LIVE_CWD)

    def test_a_remembered_stale_copy_is_replaced_once_the_other_grows(self):
        # The hub remembered the stale copy when it was the newer one.
        now = time.time()
        os.utime(self.stale, (now - 30, now - 30))
        os.utime(self.live, (now - 3600, now - 3600))
        with mock.patch("builtins.print"):
            self.assertEqual(dashboard.find_transcript(SID), self.stale)
        # Claude now writes the other copy.
        with self.live.open("a", encoding="utf-8") as f:
            f.write(json.dumps(turn("assistant", "answer", "2026-11-13T08:01:00Z")) + "\n")
        # While the remembered file was quiet for less than the wait: kept, one stat.
        self.assertEqual(dashboard.find_transcript(SID), self.stale)
        with mock.patch.object(dashboard, "_transcript_index",
                               side_effect=AssertionError("listed while the wait runs")):
            self.assertEqual(dashboard.find_transcript(SID), self.stale)
        # Quiet past the wait: the listing is asked and the newer copy taken.
        size, at = dashboard._TRANSCRIPT_GROWTH[SID]
        dashboard._TRANSCRIPT_GROWTH[SID] = (size, at - dashboard._TRANSCRIPT_RECHECK_S - 1)
        with mock.patch.object(dashboard, "_TRANSCRIPT_INDEX_TTL_S", 0.0), mock.patch("builtins.print"):
            self.assertEqual(dashboard.find_transcript(SID), self.live)
        self.assertEqual(dashboard._TRANSCRIPT_PATHS[SID], self.live)

    def test_a_listing_drops_a_remembered_copy_another_outdates(self):
        dashboard._TRANSCRIPT_PATHS[SID] = self.stale
        with mock.patch("builtins.print") as said:
            dashboard._transcript_index(fresh=True)
            dashboard._TRANSCRIPT_INDEX = ("", 0.0, {})
            dashboard._transcript_index(fresh=True)
        self.assertNotIn(SID, dashboard._TRANSCRIPT_PATHS)
        self.assertEqual(dashboard.find_transcript(SID), self.live)
        # Logged once for the id, nothing moved.
        self.assertEqual(sum(SID in str(c) for c in said.call_args_list), 1)
        self.assertTrue(self.stale.exists() and self.live.exists())

    def test_lost_sessions_other_folder_takes_the_newest(self):
        other = Path(self.temp.name) / "claude-config"
        old = write(other / "projects" / "a" / f"{SID}.jsonl", [], time.time() - 500)
        new = write(other / "projects" / "b" / f"{SID}.jsonl", [], time.time() - 5)
        os.utime(old, (time.time() - 500,) * 2)
        with mock.patch.object(dashboard, "find_transcript", return_value=None), \
                mock.patch.object(lost_sessions, "claude_config_dir", return_value=other):
            self.assertEqual(lost_sessions._claude_transcript(SID), new)


MESSAGES = [
    ("P1", "2026-11-13T08:10:00.000Z", "2026-11-13T08:10:01.000Z", "is the filing ready?\n\n[point P1]"),
    ("P2", "2026-11-13T08:12:00.000Z", "2026-11-13T08:12:01.000Z", "and the receipts?\n\n[point P2]"),
]


class TypedAgain(Harness):
    """The message-delivered check reads the live copy: nothing typed again."""

    def _setup(self, remembered_stale: bool):
        rid = self.room(agents=("claude",))
        room = chatroom.get_room(rid, public=False)
        part = next(p for p in room["participants"] if p.get("kind") == "agent")
        part["sessionId"] = SID
        chatroom.update_room(room)
        proj = Path(self.temp.name) / "projects"
        epoch = dashboard._turn_epoch
        stale = write(proj / STALE_DIR / f"{SID}.jsonl",
                      [turn("user", "earlier conversation", "2026-11-11T07:36:00.000Z")],
                      epoch("2026-11-11T07:36:00.000Z"))
        rows = [turn("user", "earlier conversation", "2026-11-11T07:36:00.000Z")]
        for point, sent, received, text in MESSAGES:
            sends.accept(rid, f"send:{point}", text, now=epoch(sent))
            sends.mark(rid, [f"send:{point}"], "delivered", now=epoch(sent))
            rows += [turn("user", text, received), turn("assistant", f"Re {point}: yes.", received)]
        live = write(proj / LIVE_DIR / f"{SID}.jsonl", rows, epoch(MESSAGES[-1][2]))
        p = mock.patch.object(dashboard, "PROJ_DIR", proj)
        p.start()
        self.extra.append(p)
        forget()
        if remembered_stale:
            dashboard._TRANSCRIPT_PATHS[SID] = stale
            dashboard._TRANSCRIPT_GROWTH[SID] = (stale.stat().st_size,
                                                 time.monotonic() - dashboard._TRANSCRIPT_RECHECK_S - 1)
        sends._SCANNED.clear()
        return rid, live

    def _check(self, remembered_stale: bool):
        rid, live = self._setup(remembered_stale)
        with mock.patch.object(points, "_session_stat", REAL_SESSION_STAT), \
                mock.patch.object(dashboard, "read_session_turns", REAL_READ_SESSION_TURNS), \
                mock.patch.object(dashboard, "find_transcript", REAL_FIND), \
                mock.patch.object(dashboard, "kept_session", lambda sid: None), \
                mock.patch("builtins.print"):
            self.assertEqual(dashboard.find_transcript(SID), live)
            texts = [t.get("text", "") for t in dashboard.read_session_turns(SID) or []]
            self.assertTrue(any("[point P2]" in t for t in texts), texts)
            sends._redeliver_room(rid, dashboard._turn_epoch("2026-11-13T08:20:00.000Z"))
        for point, *_ in MESSAGES:
            self.assertEqual(sends.get(rid, f"send:{point}")["state"], "confirmed", point)
        self.assertEqual(self.ptys["pty-live-0"].typed, [])
        forget()

    def test_a_fresh_hub_reads_the_live_copy(self):
        self._check(remembered_stale=False)

    def test_a_hub_that_remembered_the_stale_copy_moves_to_the_live_one(self):
        self._check(remembered_stale=True)


if __name__ == "__main__":
    unittest.main()
