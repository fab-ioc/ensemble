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

    def test_ordinary_room_message_is_not_a_latest_visible_chat_item(self):
        out = {"latestAnyMid": "answer:1", "latestAnyAt": 100,
               "latestAnyThread": "P1", "latestAnyRole": "assistant",
               "items": [{"timestamp": 80}]}
        room = {"messages": [{"id": "restart:1", "ts": 90, "kind": "notice", "noticeKind": "restart"},
                             {"id": "ordinary:1", "ts": 120, "kind": "message"}]}
        dashboard.channel_hub_rows(out, room)
        self.assertEqual([m["id"] for m in out["hubRows"]], ["restart:1"])
        dashboard.channel_latest_visible(out)
        self.assertEqual(out["latestAnyMid"], "answer:1")
        self.assertNotIn("latestAnyAt", out)
        out["hubRows"].append({"id": "restart:2", "ts": 130, "kind": "notice"})
        dashboard.channel_latest_visible(out)
        self.assertEqual(out["latestAnyMid"], "restart:2")


if __name__ == "__main__":
    unittest.main()
