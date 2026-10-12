"""Progress snapshots expose bounded counts and live thread build phases."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import progress
import threads


class ProgressFields(unittest.TestCase):
    def tearDown(self):
        for key in progress.snapshot():
            progress.clear(key)

    def test_snapshot_counts_are_bounded_and_private_file_path_is_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "count.json"
            path.write_text('{"done": 7}', encoding="utf-8")
            progress.set_progress("search:test", "Searching transcripts", 0, 10, str(path))
            self.assertEqual(progress.snapshot()["search:test"]["done"], 7)
            self.assertNotIn("progress_file", progress.snapshot()["search:test"])
            path.write_text('{"done": 50}', encoding="utf-8")
            self.assertEqual(progress.snapshot()["search:test"]["done"], 10)
            progress.set_progress("thread:test", "Sorting messages", 25, 10)
            self.assertEqual(progress.snapshot()["thread:test"]["done"], 10)

    def test_pending_thread_channel_carries_build_progress(self):
        key = "thread:sample"
        progress.set_progress(key, "Sorting messages into threads", 50, 100)
        with mock.patch.object(threads, "ready", return_value=None):
            result = threads.channel_view("sample", {})
        self.assertEqual(result["progress"]["phase"], "Sorting messages into threads")
        self.assertEqual((result["progress"]["done"], result["progress"]["total"]), (50, 100))
