"""The Inbox's Close this and earlier (#310): ``/api/room/ask/close-many``
closes many open questions in one call and tells nobody; ``reopen-many`` is
its Undo and puts the ledger back exactly as it was.

* only the dashboard page may call either (a bearer or no page cookie: 403);
* ``quiet: true`` is required, at most CLOSE_MANY_MAX questions a call;
* a quiet close wakes nobody (no resume, no ring) and writes the ledger entry
  of a single close for each, marked quiet with its batch;
* a question no longer open is skipped, not an error;
* Undo within the window restores the ledger exactly; after it, 409;
* the Inbox's ask rows carry what the page needs (n, age, task state).
"""
from __future__ import annotations

import copy
import time
import unittest
from unittest import mock

import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests.test_asks import THREE  # noqa: E402
from tests.test_points import _World, http, turn  # noqa: E402


def _no(*_a, **_kw):
    raise AssertionError("a quiet close must wake nobody")


class CloseMany(_World):
    def setUp(self):
        super().setUp()
        for name in ("_resume_room", "_room_resume", "_ring"):
            p = mock.patch.object(dashboard.Handler, name, side_effect=_no, create=True)
            p.start()
            self.addCleanup(p.stop)

    def _room(self):
        rid = self.solo_room()
        self.add("sid-1", turn("user", "[rotation] You are the PO.", self.t0), turn("assistant", THREE, self.t0 + 2))
        return rid

    def _open(self, rid):
        return [c["n"] for c in asks.open_cards(chatroom.get_room(rid))]

    def _page(self):
        return mock.patch.object(dashboard.Handler, "_page_refusal", return_value="")

    def test_only_the_page(self):
        rid = self._room()
        body = {"items": [{"roomId": rid, "mid": "sid-1:1", "n": 0}], "quiet": True}
        p = mock.patch.object(dashboard.Handler, "_ui_cookie", return_value="ensemble_ui_1")
        p.start()
        self.addCleanup(p.stop)
        for path in ("/api/room/ask/close-many", "/api/room/ask/reopen-many"):
            status, r = http(path, body)
            self.assertEqual((status, r["error"]), (403, "page_only"), path)
        with mock.patch.object(dashboard.Handler, "_bearer_token", return_value="tok"):
            self.assertEqual(http("/api/room/ask/close-many", body)[0], 403)
        self.assertEqual(self._open(rid), [0, 1, 2])

    def test_quiet_required_and_capped(self):
        rid = self._room()
        one = [{"roomId": rid, "mid": "sid-1:1", "n": 0}]
        with self._page():
            for quiet in (None, False, "true", 1):
                status, r = http("/api/room/ask/close-many", {"items": one, "quiet": quiet})
                self.assertEqual((status, r["error"]), (400, "quiet_only"), quiet)
            many = [{"roomId": rid, "mid": "sid-1:1", "n": i} for i in range(dashboard.CLOSE_MANY_MAX + 1)]
            status, r = http("/api/room/ask/close-many", {"items": many, "quiet": True})
            self.assertEqual((status, r["error"]), (400, "too_many"))
            for bad in ([], "x", [{"roomId": "nope", "mid": "m", "n": 0}], [{"roomId": rid, "mid": "m", "n": True}],
                        [{"roomId": rid, "mid": "", "n": 0}], [{"roomId": rid, "mid": "m", "n": -1}]):
                self.assertEqual(http("/api/room/ask/close-many", {"items": bad, "quiet": True})[0], 400, bad)
        self.assertEqual(self._open(rid), [0, 1, 2])

    def test_quiet_close_then_undo_restores_exactly(self):
        rid = self._room()
        before = copy.deepcopy(points.load(rid)["asks"])
        items = [{"roomId": rid, "mid": "sid-1:1", "n": 0}, {"roomId": rid, "mid": "sid-1:1", "n": 2},
                 {"roomId": rid, "mid": "sid-1:1", "n": 0}, {"roomId": rid, "mid": "sid-1:9", "n": 0}]
        with self._page():
            status, r = http("/api/room/ask/close-many", {"items": items, "quiet": True})
            self.assertEqual(status, 200, r)
            self.assertTrue(r["batch"].startswith("close-many:"))
            self.assertEqual([(c["mid"], c["n"]) for c in r["closed"]], [("sid-1:1", 0), ("sid-1:1", 2)])
            self.assertEqual([(s["mid"], s["why"]) for s in r["skipped"]], [("sid-1:9", "not_open")])
            self.assertEqual(self._open(rid), [1])
            led = points.load(rid)["asks"]["sid-1:1"]
            for n in ("0", "2"):
                self.assertEqual({k: led[n][k] for k in ("closed", "quiet", "batch")},
                                 {"closed": True, "quiet": True, "batch": r["batch"]})
                self.assertEqual(led[n]["question"].split()[0], "Which" if n == "0" else "What")
            # Closed again: nothing open to close, so skipped.
            again = http("/api/room/ask/close-many", {"items": items[:1], "quiet": True})[1]
            self.assertEqual((again["closed"], again["skipped"][0]["why"]), ([], "not_open"))
            # Another batch cannot undo it.
            status, u = http("/api/room/ask/reopen-many", {"batch": again["batch"], "items": r["closed"]})
            self.assertEqual((status, u["error"]), (409, "undo_expired"))
            status, u = http("/api/room/ask/reopen-many", {"batch": r["batch"], "items": r["closed"]})
            self.assertEqual(status, 200, u)
            self.assertEqual(len(u["reopened"]), 2)
        self.assertEqual(points.load(rid)["asks"], before)
        self.assertEqual(self._open(rid), [0, 1, 2])

    def test_undo_after_the_window_is_refused(self):
        rid = self._room()
        with self._page():
            r = http("/api/room/ask/close-many",
                     {"items": [{"roomId": rid, "mid": "sid-1:1", "n": 1}], "quiet": True})[1]
            late = time.time() + dashboard.CLOSE_MANY_UNDO_S + 5
            with mock.patch.object(dashboard.time, "time", return_value=late):
                status, u = http("/api/room/ask/reopen-many", {"batch": r["batch"], "items": r["closed"]})
            self.assertEqual((status, u["error"]), (409, "undo_expired"))
            self.assertEqual(http("/api/room/ask/reopen-many", {"batch": "x", "items": r["closed"]})[0], 400)
        self.assertEqual(self._open(rid), [0, 2])

    def test_a_single_reopen_after_a_quiet_close_reopens(self):
        rid = self._room()
        with self._page():
            http("/api/room/ask/close-many", {"items": [{"roomId": rid, "mid": "sid-1:1", "n": 1}], "quiet": True})
        with mock.patch.object(dashboard, "_pty_alive", return_value=False), \
                mock.patch.object(dashboard.Handler, "_room_resume", create=True), \
                mock.patch.object(dashboard.Handler, "_resume_room"):
            status, r = http("/api/room/ask/reopen", {"roomId": rid, "mid": "sid-1:1", "n": 1})
        self.assertEqual(status, 200, r)
        self.assertEqual(self._open(rid), [0, 1, 2])

    def test_points_ack_many(self):
        rid = self.solo_room()
        for q in ("Q one?", "Q two?"):
            out, _ = self.send(rid, q, at=self.t0)
            self.add("sid-1", turn("user", out, self.t0 + 1), turn("assistant", "A.", self.t0 + 2))
        status, r = http("/api/room/points", {"roomId": rid, "ids": ["P1", "P2", "P9"], "action": "ack"})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["acked"], ["P1", "P2"])
        self.assertEqual((r["points"]["open"], r["points"]["delivered"]), (0, 0))
        for bad in ({"ids": [], "action": "ack"}, {"ids": ["P1"], "action": "seen"}, {"ids": [1], "action": "ack"}):
            self.assertEqual(http("/api/room/points", {"roomId": rid, **bad})[0], 400, bad)


if __name__ == "__main__":
    unittest.main()
