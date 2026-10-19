"""#247: the hub reads the chats likeliest to be opened as it starts, PO
chats first, so the first open after a restart is a warm one.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_warm_rooms
"""
import threading
import time
import unittest
from unittest import mock

import chatroom
import dashboard
import threads


class WarmRooms(unittest.TestCase):
    def test_po_chats_first_then_the_latest_changed_and_a_failure_skipped(self):
        rows = [{"id": f"room-{i}", "updatedAt": i} for i in range(10)]
        seen = []

        def warm(rid):
            seen.append(rid)
            if rid == "room-8":
                raise OSError("locked")
        with mock.patch.object(chatroom, "room_index", return_value=rows), \
                mock.patch.object(threads, "_po_rooms", return_value=frozenset({"room-2"})), \
                mock.patch.object(dashboard, "warm_room", warm), \
                mock.patch.object(dashboard, "WARM_ROOM_PAUSE_S", 0):
            done = dashboard.warm_rooms(limit=4)
        self.assertEqual(seen, ["room-2", "room-9", "room-8", "room-7"])
        self.assertEqual(done, ["room-2", "room-9", "room-7"])

    def test_a_po_chat_is_built_once_while_a_poll_builds_it(self):
        room = {"id": "room-po", "messages": [], "participants": []}
        builds = []
        with mock.patch.object(chatroom, "get_room", return_value=room), \
                mock.patch.object(threads, "is_threaded", return_value=True), \
                mock.patch.object(threads, "build", lambda rid, r, **k: builds.append(rid)), \
                mock.patch.object(dashboard, "_annotate_room_liveness", return_value=room):
            dashboard.warm_room("room-po")
            with threads._LOCK:
                threads._BUILDING.add("room-po")
            try:
                dashboard.warm_room("room-po")
            finally:
                threads._BUILDING.discard("room-po")
        self.assertEqual(builds, ["room-po"])
        self.assertNotIn("room-po", threads._BUILDING)



class YieldToPages(unittest.TestCase):
    # Review 4, finding 1: what the hub reads behind the pages stands aside
    # while a page's request is answered (a one-agent chat opened as the hub
    # read its chats ahead took ~2.6 s for ~0.17 s of work).
    def setUp(self):
        self.addCleanup(dashboard._ANSWERING.clear)
        self.addCleanup(dashboard._ANSWERED.__setitem__, 0, dashboard._ANSWERED[0])

    def waited(self, **kw) -> float:
        out = []

        def run():
            t = time.monotonic()
            dashboard.yield_to_pages(**kw)
            out.append(time.monotonic() - t)
        th = threading.Thread(target=run)
        th.start()
        th.join()
        return out[0]

    def test_a_read_behind_waits_for_the_request_being_answered(self):
        dashboard._ANSWERING[-1] = time.monotonic()
        threading.Timer(0.2, dashboard._ANSWERING.pop, (-1,)).start()
        self.assertGreater(self.waited(most_s=2.0), 0.15)
        self.assertLess(self.waited(most_s=2.0), 0.05, "nothing answered: no wait")

    def test_it_waits_for_a_quiet_moment_and_never_long(self):
        dashboard._ANSWERED[0] = time.monotonic()
        self.assertGreater(self.waited(quiet_s=0.2), 0.15)
        dashboard._ANSWERING[-1] = time.monotonic()
        self.assertLess(self.waited(most_s=0.1), 0.5)
        dashboard._ANSWERING[-1] = time.monotonic() - dashboard.PAGE_ANSWER_S - 1
        self.assertLess(self.waited(most_s=2.0), 0.05, "a stream is not waited for")

    def test_a_request_never_waits_for_another(self):
        dashboard._ANSWERING[-1] = time.monotonic()
        dashboard._ANSWERING[threading.get_ident()] = time.monotonic()
        t = time.monotonic()
        dashboard.yield_to_pages(most_s=2.0)
        self.assertLess(time.monotonic() - t, 0.05)

    def test_nothing_yields_while_holding_a_lock_a_page_may_wait_on(self):
        # The PO's read: the open asks and Needs you are worked out under a
        # lock a page's request waits on; that request counts as answered, so
        # a yield under the lock waited up to 1 s per 50 messages for it.
        import asks
        import attention
        dashboard._ANSWERING[-1] = time.monotonic()
        lock = threading.Lock()
        t = time.monotonic()
        with dashboard.holding(lock):
            self.assertTrue(lock.locked())
            dashboard.yield_to_pages(most_s=2.0)
            with dashboard.holding(threading.Lock()):
                pass
            dashboard.yield_to_pages(most_s=2.0)
        self.assertFalse(lock.locked())
        self.assertLess(time.monotonic() - t, 0.05)
        self.assertGreater(self.waited(most_s=0.2), 0.15, "outside the lock it yields again")
        room = {"participants": [{"identity": "claude", "kind": "agent"}],
                "messages": [{"id": f"m{i}", "from": "claude", "text": "x"} for i in range(200)]}
        asks.forget()
        self.addCleanup(asks.forget)
        t = time.monotonic()
        with mock.patch.object(asks, "open_in", lambda s, now: asks.message_asks(room, False)):
            asks.open_by_room([{"id": "room-1"}])
        self.assertLess(time.monotonic() - t, 0.5, "the open asks read under their lock")
        t = time.monotonic()
        with mock.patch.object(attention, "_items", lambda: dashboard.yield_to_pages(most_s=2.0) or []), \
                mock.patch.object(attention, "_result", (0.0, {})):
            attention.snapshot()
        self.assertLess(time.monotonic() - t, 0.5, "Needs you worked out under its lock")


if __name__ == "__main__":
    unittest.main()
