"""Terminal query responses must never be treated as typed prompt input."""
import io
import json
import shutil
import subprocess
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

    def test_other_xterm_query_replies_are_removed(self):
        replies = (
            "\x1b[>0;276;0c"                 # secondary device attributes
            "\x1b]4;2;rgb:1111/2222/3333\x1b\\"
            "\x1b]12;rgb:aaaa/bbbb/cccc\x07"   # palette and cursor color
            "\x1b[0n\x1b[24;80R\x1b[?24;80R" # status / cursor reports
            "\x1b[?1;1$y\x1b[4;2$y"          # private and ANSI mode reports
            "\x1b[8;24;80t"                  # character dimensions
            "\x1bP1$r0m\x1b\\"               # status-string report
        )
        self.assertEqual(dashboard._strip_pty_terminal_replies(replies), "")

    def test_xterm_530_title_query_reply_shapes_are_not_emitted_by_page(self):
        # xterm 5.3.0's windowOptions handler has no CSI 20/21 response cases.
        title_reports = "\x1b]Licon\x1b\\\x1b]lwindow\x1b\\"
        self.assertEqual(dashboard._strip_pty_terminal_replies(title_reports), title_reports)

    def test_non_response_input_and_incomplete_escape_are_immediate(self):
        paste = "\x1b[200~literal " + DA + FG + " text\x1b[201~"
        keys = "\x1b[A\x1bOP\x1b[I\x1b[O" + paste + "\x1b\r"
        self.assertEqual(dashboard._strip_pty_terminal_replies(keys), keys)
        partial = "typed\x1b]10;rgb:ffff/"
        self.assertEqual(dashboard._strip_pty_terminal_replies(partial), partial)
        modified_f3 = "\x1b[1;5R"
        self.assertEqual(dashboard._strip_pty_terminal_replies(modified_f3), "")
        self.assertEqual(dashboard._strip_pty_terminal_replies(modified_f3, user_key=True), modified_f3)
        self.assertEqual(dashboard._strip_pty_terminal_replies(FG, user_paste=True), FG)

    def test_page_filters_ondata_before_posting_and_keeps_display_cleanup_separate(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parents[1] / "session.html").read_text(encoding="utf-8")
        self.assertIn("const input = stripTerminalReplies(d, terminalKey, terminalPaste);", page)
        self.assertIn("if (input) jpost('/api/pty/input'", page)
        self.assertIn("function cleanSeq(s)", page)

    def test_page_filter_behavior_matches_server_for_replies_and_paste(self):
        from pathlib import Path

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        page = (Path(__file__).resolve().parents[1] / "session.html").read_text(encoding="utf-8")
        start = page.index("function stripTerminalReplies(s, userKey = false, userPaste = false)")
        end = page.index("\n}", start) + 2
        function = page[start:end]
        cases = [
            [DA + FG + BG + "question", "question"],
            ["\x1b[>0;276;0c\x1b]12;rgb:aaaa/bbbb/cccc\x07after", "after"],
            ["\x1b[0n\x1b[24;80R\x1b[?24;80R\x1b[?1;1$y\x1b[4;2$y\x1b[8;24;80t", ""],
            ["\x1b]Licon\x1b\\\x1b]lwindow\x1b\\", "\x1b]Licon\x1b\\\x1b]lwindow\x1b\\"],
            ["\x1bP1$r0m\x1b\\", ""],
            ["\x1b[A\x1b[200~" + DA + FG + "\x1b[201~", "\x1b[A\x1b[200~" + DA + FG + "\x1b[201~"],
            ["\x1b[I\x1b[O\x1bOP", "\x1b[I\x1b[O\x1bOP"],
            ["\x1b[1;5R", ""],
            ["\x1b[1;5R", "\x1b[1;5R", False, True],
            ["\x1b]10;rgb:ffff/", "\x1b]10;rgb:ffff/"],
        ]
        script = function + "\nconst cases = " + json.dumps(cases) + ";\n"
        script += "for (const [input, expected, key, paste] of cases) { if (stripTerminalReplies(input, key, paste) !== expected) { console.error({input, expected, key, paste, actual: stripTerminalReplies(input, key, paste)}); process.exit(1); } }\n"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


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
        self.post(sess, "\x1b[>0;276;0c\x1b]4;0;rgb:0000/1111/2222\x1b\\after")
        self.assertEqual(sess.writes, [
            "Question?", "\x1b[A" + paste, "after",
        ])

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

    def test_provenance_preserves_modified_f3_and_plain_user_paste(self):
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
        self.post(sess, "\x1b[1;5R", terminalKey=True)
        self.post(sess, FG + "copied text", terminalPaste=True)
        self.post(sess, "\x1b[1;5R")
        self.assertEqual(sess.writes, ["\x1b[1;5R", FG + "copied text"])


if __name__ == "__main__":
    unittest.main()
