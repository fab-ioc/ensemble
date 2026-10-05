"""Terminal query responses must never be treated as typed prompt input."""
import io
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import input_provenance
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
    def request(self, path, payload, sess=None):
        body = json.dumps(payload).encode()
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "POST", "HTTP/1.1"
        h.requestline = f"POST {path} HTTP/1.1"
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

    def post(self, sess, data, **extra):
        return self.request("/api/pty/input", {"id": "pty-x", "data": data, **extra}, sess)

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

    def test_multiline_hub_paste_is_recorded_once_when_enter_submits_it(self):
        class Session:
            meta = {"room": "room-one", "identity": "claude"}
            hub_line_typed = False
            hub_line_text = ""

            def __init__(self):
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        sess = Session()
        paste = "\x1b[200~first\nsecond\x1b[201~"
        with mock.patch.object(dashboard, "_record_typed_input") as record, \
                mock.patch.object(dashboard, "note_answer") as answered:
            self.post(sess, paste, hub=True)
            self.post(sess, "\r")
        self.assertEqual(sess.writes, [paste, "\r"])
        record.assert_called_once_with(sess, "first\nsecond", {
            "kind": "hub", "senderType": "hub", "senderId": "ensemble", "senderLabel": "Hub"})
        answered.assert_not_called()

    def test_explicit_source_beats_prefix_and_bracketed_po_keeps_its_sender(self):
        class Session:
            meta = {"room": "room-one", "identity": "claude"}
            hub_line_typed = False
            hub_line_text = ""
            person_line_text = ""

            def __init__(self):
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        sess = Session()
        with mock.patch.object(dashboard, "_record_typed_input") as record, \
                mock.patch.object(dashboard, "note_answer") as answered:
            self.post(sess, "[digest] these are the operator's words", origin="po")
            self.post(sess, "\r")
        info = record.call_args.args[2]
        self.assertEqual((record.call_args.args[1], info["kind"], info["senderType"]),
                         ("[digest] these are the operator's words", "human", "person"))
        answered.assert_called_once_with("room-one", "claude")

        sess = Session()
        po = "\x1b[200~[from the PO] use branch B\x1b[201~"
        with mock.patch.object(dashboard, "_record_typed_input") as record, \
                mock.patch.object(dashboard, "note_answer") as answered:
            self.post(sess, po, hub=True, origin="po")
            self.post(sess, "\r", hub=True, origin="po")
        info = record.call_args.args[2]
        self.assertEqual((record.call_args.args[1], info["kind"], info["senderLabel"]),
                         ("[from the PO] use branch B", "po", "PO"))
        answered.assert_called_once_with("room-one", "claude")

    def test_cursor_edited_browser_input_keeps_its_explicit_human_origin(self):
        class Session:
            hub_line_typed = False
            hub_line_text = ""
            person_line_text = ""

            def __init__(self, rid):
                self.meta = {"room": rid, "identity": "claude", "sessionId": "sid-one"}
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(chatroom, "ROOMS_DIR", Path(tmp) / "rooms"):
            input_provenance._CACHE.clear()
            rid = chatroom.create_room(
                "Edited", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
            sess = Session(rid)
            with mock.patch.object(dashboard, "note_answer"):
                self.post(sess, "[digest] genuie")
                self.post(sess, "\x1b[D", terminalKey=True)
                self.post(sess, "n")
                self.post(sess, "\r")
            [row] = input_provenance.records(rid)
            final_text = "[digest] genuine"
            self.assertNotEqual(row["hash"], input_provenance.text_hash(final_text))
            [turn] = dashboard.classify_turns(
                [{"role": "user", "text": final_text}], room_id=rid,
                identity="claude", session_id="sid-one")
            self.assertEqual(turn["kind"], "human")
            self.assertEqual(turn["provenance"], "record")

    def test_physical_write_and_journal_append_share_one_submission_order(self):
        class Session:
            hub_line_typed = False
            hub_line_text = ""
            person_line_text = ""

            def __init__(self, rid):
                self.meta = {"room": rid, "identity": "claude", "sessionId": "sid-one"}
                self.physical = []
                self.hub_written = threading.Event()

            def alive(self):
                return True

            def write(self, _data):
                if _data == "same\r":
                    self.physical.append("human")
                elif _data == "same":
                    self.physical.append("hub")
                    self.hub_written.set()
                else:
                    self.physical.append("hub-enter")
                return True

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(chatroom, "ROOMS_DIR", Path(tmp) / "rooms"):
            input_provenance._CACHE.clear()
            rid = chatroom.create_room(
                "Ordered", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
            sess = Session(rid)
            recording_human = threading.Event()
            release_human = threading.Event()
            real_record = dashboard._record_typed_input

            def delayed_record(*args, **kwargs):
                info = args[2] if len(args) > 2 else {}
                if info.get("kind") == "human":
                    recording_human.set()
                    self.assertTrue(release_human.wait(2))
                return real_record(*args, **kwargs)

            with mock.patch.object(dashboard, "_record_typed_input", side_effect=delayed_record), \
                    mock.patch.object(dashboard, "note_answer"):
                human = threading.Thread(target=lambda: self.post(sess, "same\r"))
                human.start()
                self.assertTrue(recording_human.wait(2))
                hub = threading.Thread(
                    target=lambda: dashboard._type_input(sess, "same", {"kind": "hub"}))
                hub.start()
                hub_was_blocked = not sess.hub_written.wait(0.1)
                release_human.set()
                human.join(2)
                hub.join(2)
                self.assertTrue(hub_was_blocked,
                                "the Hub write passed the unjournaled human submission")
            self.assertFalse(human.is_alive())
            self.assertFalse(hub.is_alive())
            self.assertEqual(sess.physical, ["human", "hub", "hub-enter"])
            self.assertEqual([row["kind"] for row in input_provenance.records(rid)],
                             ["human", "hub"])
            assigned = input_provenance.assign(
                input_provenance.index(rid), [(0, "same", 0), (1, "same", 0)],
                identity="claude", session_id="sid-one")
            self.assertEqual([assigned[i]["kind"] for i in (0, 1)], ["human", "hub"])

    def test_page_keystroke_does_not_wait_for_another_terminal_enter_pause(self):
        class Session:
            hub_line_typed = False
            hub_line_text = ""
            person_line_text = ""

            def __init__(self, name):
                self.name = name
                self.meta = {}
                self.writes = []

            def alive(self):
                return True

            def write(self, data):
                self.writes.append(data)
                return True

        brief = Session("brief")
        terminal = Session("terminal")
        pausing = threading.Event()
        release = threading.Event()

        def pause(_seconds):
            pausing.set()
            self.assertTrue(release.wait(2))

        with mock.patch.object(dashboard.time, "sleep", side_effect=pause), \
                mock.patch.object(dashboard, "_record_typed_input"):
            worker = threading.Thread(
                target=lambda: dashboard._type_input(brief, "x" * 400, {"kind": "brief"}))
            worker.start()
            self.assertTrue(pausing.wait(1))
            key_done = threading.Event()
            key = threading.Thread(
                target=lambda: (self.post(terminal, "k", terminalKey=True), key_done.set()))
            key.start()
            key_was_immediate = key_done.wait(0.2)
            release.set()
            worker.join(3)
            key.join(3)
        self.assertTrue(key_was_immediate, "the keystroke waited for the brief's Enter pause")
        self.assertEqual(terminal.writes, ["k"])
        self.assertEqual(brief.writes, ["x" * 400, "\r"])
        self.assertFalse(worker.is_alive())
        self.assertFalse(key.is_alive())

    def test_visible_terminal_send_journals_human_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(chatroom, "ROOMS_DIR", Path(tmp) / "rooms"):
            input_provenance._CACHE.clear()
            rid = chatroom.create_room(
                "Visible", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
            chatroom.patch_participant(
                rid, "claude", {"pid": 42, "sessionId": "sid-visible"})
            text = "[digest] these are the operator's words"
            with mock.patch.object(dashboard.BACKEND, "send_text", return_value="ok"):
                self.request("/api/send", {"pid": 42, "text": text})
            [row] = input_provenance.records(rid)
            self.assertEqual((row["kind"], row["identity"], row["sessionId"]),
                             ("human", "claude", "sid-visible"))
            [turn] = dashboard.classify_turns(
                [{"role": "user", "text": text}], room_id=rid,
                identity="claude", session_id="sid-visible")
            self.assertEqual(turn["kind"], "human")

            with mock.patch.object(dashboard.BACKEND, "send_text", return_value="not_alive"):
                self.request("/api/send", {"pid": 42, "text": "another line"})
            self.assertEqual(len(input_provenance.records(rid)), 1)


if __name__ == "__main__":
    unittest.main()
