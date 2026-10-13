import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard


class TranscriptMissTests(unittest.TestCase):
    def test_repeated_miss_does_not_glob_until_expiry(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dashboard, "PROJ_DIR", Path(d)):
            dashboard._TRANSCRIPT_PATHS.clear()
            dashboard._TRANSCRIPT_MISSES.clear()
            self.assertIsNone(dashboard.find_transcript("absent", immediate=False))
            with mock.patch.object(Path, "glob", side_effect=AssertionError("repeated glob")):
                self.assertIsNone(dashboard.find_transcript("absent", immediate=False))
            p = Path(d) / "project" / "absent.jsonl"
            p.parent.mkdir()
            p.write_text("{}\n", encoding="utf-8")
            dashboard._TRANSCRIPT_MISSES.clear()  # expiry
            with mock.patch.object(dashboard, "_TRANSCRIPT_INDEX_TTL_S", 0.0):
                self.assertEqual(dashboard.find_transcript("absent", immediate=False), p)


class RotatingLogTests(unittest.TestCase):
    def test_caps_existing_and_new_log_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ensemble.log"
            p.write_bytes(b"old" * 100)
            log = dashboard.RotatingLog(p, max_bytes=64, backups=2)
            try:
                log.write("new\n" * 50)
                log.flush()
            finally:
                log._stream.close()
            files = [p, p.with_name("ensemble.log.1"), p.with_name("ensemble.log.2")]
            self.assertTrue(all(f.exists() for f in files))
            self.assertTrue(all(f.stat().st_size <= 64 for f in files))
            self.assertEqual(p.read_bytes(), b"new\n" * 2)


class ConditionalRoomResponseTests(unittest.TestCase):
    def test_same_payload_returns_empty_304(self):
        def send(headers):
            h = dashboard.Handler.__new__(dashboard.Handler)
            h.headers, h.wfile = headers, io.BytesIO()
            result = {"status": None, "headers": {}}
            h.send_response = lambda code: result.update(status=code)
            h.send_header = lambda key, value: result["headers"].update({key: value})
            h.end_headers = lambda: None
            h._send_json(200, {"messages": ["same"]}, conditional=True)
            return result, h.wfile.getvalue()

        first, body = send({})
        self.assertEqual(first["status"], 200)
        self.assertTrue(body)
        second, body = send({"If-None-Match": first["headers"]["ETag"]})
        self.assertEqual(second["status"], 304)
        self.assertEqual(body, b"")
        self.assertEqual(second["headers"]["Content-Length"], "0")


if __name__ == "__main__":
    unittest.main()
