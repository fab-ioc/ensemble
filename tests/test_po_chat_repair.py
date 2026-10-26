"""The latest channel mid survives an unchanged (including cached 304) poll."""
import unittest

import dashboard


class ChannelLatestMid(unittest.TestCase):
    def test_unchanged_poll_still_names_the_latest_mid(self):
        view = {"sig": "index", "items": [{"mid": "older:1"}, {"mid": "newer:2"}],
                "latestMid": "newer:2", "latestAnyMid": "thread:3",
                "latestAnyThread": "P1", "latestAnyRole": "agent", "before": 0, "after": 0}
        first = dashboard.thread_view_answer("room-267test", dict(view), "")
        same = dashboard.thread_view_answer("room-267test", dict(view), first["sig"])
        self.assertTrue(same["same"])
        self.assertEqual(same["latestMid"], "newer:2")
        self.assertEqual((same["latestAnyMid"], same["latestAnyThread"], same["latestAnyRole"]),
                         ("thread:3", "P1", "agent"))


if __name__ == "__main__":
    unittest.main()
