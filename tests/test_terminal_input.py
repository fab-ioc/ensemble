"""Terminal query responses must never be treated as typed prompt input."""
import io
import json
import unittest
from unittest import mock

import dashboard
from backends import ptyrun
import rotation


DA = "\x1b[?1;2c"
FG = "\x1b]10;rgb:ffff/ffff/ffff\x1b\\"
BG = "\x1b]11;rgb:0b0b/0b0b/0b0b\x07"


class TerminalReplyFilter(unittest.TestCase):
    def test_rollout_replies_and_concatenated_human_text(self):
        polluted = DA + FG + BG + "What is the status?"
        self.assertEqual(dashboard._strip_pty_terminal_replies(polluted),
                         "What is the status?")

    def test_repeated_replies_leave_adjacent_text_exact(self):
        self.assertEqual(
            dashboard._strip_pty_terminal_replies("first" + DA + "second" + FG + FG + "!"),
            "firstsecond!",
        )

    def test_non_response_input_and_incomplete_escape_are_immediate(self):
        paste = "\x1b[200~literal " + DA + FG + " text\x1b[201~"
        keys = "\x1b[A\x1bOP" + paste + "\x1b\r"
        self.assertEqual(dashboard._strip_pty_terminal_replies(keys), keys)
        partial = "typed\x1b]10;rgb:ffff/"
        self.assertEqual(dashboard._strip_pty_terminal_replies(partial), partial)

    def test_page_filters_ondata_before_posting_and_keeps_display_cleanup_separate(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "session.html").read_text(encoding="utf-8")
        self.assertIn("const input = stripTerminalReplies(d);", page)
        self.assertIn("if (input) jpost('/api/pty/input'", page)
        self.assertIn("function cleanSeq(s)", page)


class PtyInputEndpointFilter(unittest.TestCase):
    def post(self, sess, data, **extra):
        body = json.dumps({"id": "pty-x", "data": data, **extra}).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/pty/input", "POST", "HTTP/1.1"
        h.requestline = "POST /api/pty/input HTTP/1.1"
        h.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json",
                     "Host": "127.0.0.1"}
        h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.log_message = lambda *a: None
        with (mock.patch.object(ptyrun, "get", lambda _id: sess),
              mock.patch.object(dashboard.Handler, "_pty_input_by_person", return_value=False),
              mock.patch.object(rotation, "room_rotating", return_value=False)):
            h.do_POST()
        return h.wfile.getvalue().split(b" ", 2)[1]

    def test_browser_input_filters_only_the_recognized_frames(self):
        class Session:
            meta = {}
            hub_line_typed = False

            def __init__(self):
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        sess = Session()
        self.post(sess, DA + "Question?" + FG + BG)
        paste = "\x1b[200~literal " + DA + FG + " text\x1b[201~"
        self.post(sess, "\x1b[A" + paste)
        self.assertEqual(sess.writes, ["Question?", "\x1b[A" + paste])

    def test_hub_message_is_byte_for_byte_unchanged(self):
        class Session:
            meta = {}
            hub_line_typed = False

            def __init__(self):
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        sess = Session()
        self.post(sess, DA + "hub-authored", hub=True)
        self.assertEqual(sess.writes, [DA + "hub-authored"])


if __name__ == "__main__":
    unittest.main()
