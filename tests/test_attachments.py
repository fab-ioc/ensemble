"""Images pasted or dropped into a chat, kept on the hub for the agent.

* an upload is stored in the room's task folder under attachments/, a pasted
  one named "<date time> screenshot.png", a dropped one by its own name, and a
  second of the same name is "-2", never an overwrite;
* refused before anything is stored: more than 20 MB (before the body is
  read), not a PNG/JPEG/GIF/WebP by its first bytes (whatever the header
  says), no length, an empty body, a body that ends early, a page of another
  site, a chat that is not there;
* served back only by a plain name inside that room's folder: "..", slashes,
  another room's file, a non-image name are refused;
* /api/room/say and /api/room/resume end the message with one
  "[image] <absolute path>" line per image; an image of another chat is copied
  into this one's folder once, however many times the send is retried;
* message_refs keeps those lines last, as they were, when it writes out links.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import attachments  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import message_refs  # noqa: E402

PORT = 8798
MB = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class Body(io.RawIOBase):
    """A large body served lazily; counts what was read of it."""

    def __init__(self, size: int, head: bytes = PNG):
        self.left, self.head, self.read_bytes = size, head, 0

    def readable(self):
        return True

    on_read = None

    def read(self, n=-1):
        if self.on_read:
            self.on_read()
            self.on_read = None
        n = self.left if n is None or n < 0 else min(n, self.left)
        start = self.read_bytes
        self.left -= n
        self.read_bytes += n
        out = self.head[start:start + n]
        return out + b"\x00" * (n - len(out))


class Attachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "state"
        self.state.mkdir()
        patches = [
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.state),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.task_dir = base / "Motors" / "fix_it"
        self.task_dir.mkdir(parents=True)
        self.rid = self.room("Fix it", self.task_dir)

    def room(self, title, task_dir=None):
        rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer"}])["id"]
        if task_dir:
            full = chatroom.get_room(rid, public=False)
            full["taskDir"] = str(task_dir)
            chatroom.update_room(full)
        return rid

    # ---- requests ----

    def call(self, method, path, body=b"", headers=None, page=True, length=True):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, method, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        size = body.left if isinstance(body, Body) else len(body)
        h.headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/octet-stream"}
        if length and method == "POST":
            h.headers["Content-Length"] = str(size)
        if page:
            h.headers.update({"Cookie": f"ensemble_ui_{PORT}={dashboard._UI_KEY}", "Origin": f"http://127.0.0.1:{PORT}"})
        h.headers.update(headers or {})
        h.rfile = body if isinstance(body, Body) else io.BytesIO(body)
        h.wfile = io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", PORT))
        h.log_message = lambda *a: None
        self.handler = h
        (h.do_POST if method == "POST" else h.do_GET)()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        heads = dict(l.split(": ", 1) for l in head.decode("latin-1").split("\r\n")[1:] if ": " in l)
        return status, heads, payload

    def upload(self, data, room=None, name=None, **kw):
        q = {"room": self.rid if room is None else room}
        if name is not None:
            q["name"] = name
        status, _h, payload = self.call("POST", f"/api/room/attachment?{urlencode(q)}", data, **kw)
        return status, json.loads(payload)

    def get(self, name, room=None):
        return self.call("GET", f"/api/room/attachment?{urlencode({'room': self.rid if room is None else room, 'name': name})}")

    def json_post(self, path, body):
        status, _h, payload = self.call("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        return status, json.loads(payload)

    def folder(self):
        return self.task_dir / "attachments"

    # ---- storing ----

    def test_a_pasted_image_is_stored_in_the_task_folder_named_for_the_moment(self):
        status, r = self.upload(PNG)
        self.assertEqual(status, 200, r)
        self.assertRegex(r["name"], r"^\d{4}-\d\d-\d\d \d\d\.\d\d\.\d\d screenshot\.png$")
        self.assertEqual(Path(r["path"]).parent, self.folder())
        self.assertEqual(Path(r["path"]).read_bytes(), PNG)
        self.assertEqual((r["type"], r["size"], r["room"]), ("image/png", len(PNG), self.rid))
        self.assertEqual(r["url"], dashboard.attachment_url(self.rid, r["name"]))

    def test_a_dropped_image_keeps_its_name_and_a_second_is_numbered(self):
        _s, a = self.upload(JPG, name="diagram.jpeg")
        _s, b = self.upload(JPG, name="diagram.jpeg")
        self.assertEqual((a["name"], b["name"]), ("diagram.jpg", "diagram-2.jpg"))
        self.assertEqual(sorted(os.listdir(self.folder())), ["diagram-2.jpg", "diagram.jpg"])

    def test_a_name_cannot_leave_the_folder(self):
        _s, r = self.upload(PNG, name="..\\..\\evil.png")
        self.assertEqual(Path(r["path"]).parent, self.folder())
        _s, r = self.upload(PNG, name="con.png")
        self.assertEqual(r["name"], "_con.png")

    def test_a_room_without_a_task_folder_keeps_them_in_the_state_dir(self):
        rid = self.room("PO")
        _s, r = self.upload(PNG, room=rid)
        self.assertEqual(Path(r["path"]).parent, self.state / "attachments" / rid)

    def test_over_20_mb_is_refused_before_the_body_is_read(self):
        body = Body(21 * MB)
        replied = []
        body.on_read = lambda: replied.append(b"413" in self.handler.wfile.getvalue()[:20])
        status, r = self.upload(body)
        self.assertEqual((status, r["error"]), (413, "too_large"))
        self.assertIn(replied, ([], [True]), "the body was read before the refusal")
        self.assertFalse(self.folder().exists())

    def test_twenty_mb_exactly_is_stored(self):
        status, r = self.upload(Body(20 * MB))
        self.assertEqual(status, 200, r)
        self.assertEqual(os.path.getsize(r["path"]), 20 * MB)

    def test_what_is_not_an_image_is_refused_whatever_the_header_says(self):
        status, r = self.upload(b"<html><script>alert(1)</script></html>", headers={"Content-Type": "image/png"})
        self.assertEqual((status, r["error"]), (415, "not_an_image"))
        self.assertFalse(self.folder().exists() and os.listdir(self.folder()))

    def test_no_length_an_empty_body_a_short_body_are_refused(self):
        status, r = self.upload(PNG, length=False)
        self.assertEqual((status, r["error"]), (411, "length_required"))
        status, r = self.upload(b"")
        self.assertEqual((status, r["error"]), (400, "empty"))
        stored = attachments.folder(chatroom.get_room(self.rid, public=False), self.state)
        with self.assertRaises(attachments.Refused) as e:
            attachments.store(chatroom.get_room(self.rid, public=False), self.state, io.BytesIO(PNG).read, len(PNG) + 100)
        self.assertEqual(e.exception.code, "incomplete")
        self.assertEqual(os.listdir(stored), [], "a part of an image was left behind")

    def test_another_site_and_an_unknown_chat_are_refused(self):
        status, r = self.upload(PNG, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403, r)
        status, r = self.upload(PNG, room="room-nothere")
        self.assertEqual((status, r["error"]), (404, "no_such_room"))

    # ---- serving ----

    def test_a_stored_image_is_served_as_an_image(self):
        _s, r = self.upload(PNG)
        status, heads, data = self.get(r["name"])
        self.assertEqual((status, data), (200, PNG))
        self.assertEqual(heads["Content-Type"], "image/png")
        self.assertEqual(heads["X-Content-Type-Options"], "nosniff")

    def test_only_a_plain_image_name_inside_the_rooms_folder_is_served(self):
        _s, r = self.upload(PNG)
        other = self.room("Other", self.task_dir.parent / "other")
        (self.task_dir.parent / "other").mkdir()
        _s, o = self.upload(PNG, room=other, name="theirs.png")
        (self.folder() / "notes.txt").write_text("secret", encoding="utf-8")
        (self.task_dir / "outside.png").write_bytes(PNG)
        for name in ["../outside.png", "..\\outside.png", "sub/x.png", "notes.txt", "..", "", " x.png",
                     "../../other/attachments/theirs.png"]:
            status, _h, payload = self.get(name)
            self.assertIn(status, (400, 404), name)
            self.assertNotEqual(payload, PNG, name)
        status, _h, _p = self.get("theirs.png")
        self.assertEqual(status, 404, "another room's image is served through this room")
        status, _h, data = self.get("theirs.png", room=other)
        self.assertEqual((status, data), (200, PNG))

    # ---- sending ----

    def test_say_ends_the_message_with_a_line_per_image(self):
        _s, a = self.upload(PNG)
        _s, b = self.upload(JPG, name="b.jpg")
        status, r = self.json_post("/api/room/say", {"roomId": self.rid, "text": "look at these", "attachments": [a["name"], {"room": self.rid, "name": b["name"]}]})
        self.assertEqual(status, 200, r)
        text = chatroom.get_room(self.rid, public=False)["messages"][-1]["text"]
        # The person's words are a point (points.py): its line comes before the images.
        self.assertEqual(text, f"look at these\n\n[point P1]\n\n[image] {a['path']}\n[image] {b['path']}")

    def test_say_takes_images_without_words_and_refuses_one_not_there(self):
        _s, a = self.upload(PNG)
        status, r = self.json_post("/api/room/say", {"roomId": self.rid, "text": "", "attachments": [a["name"]]})
        self.assertEqual(status, 200, r)
        self.assertEqual(chatroom.get_room(self.rid, public=False)["messages"][-1]["text"], f"[image] {a['path']}")
        n = len(chatroom.get_room(self.rid, public=False)["messages"])
        status, r = self.json_post("/api/room/say", {"roomId": self.rid, "text": "x", "attachments": ["gone.png"]})
        self.assertEqual((status, r["error"]), (404, "no_such_attachment"))
        status, r = self.json_post("/api/room/say", {"roomId": self.rid, "text": "x", "attachments": "a.png"})
        self.assertEqual((status, r["error"]), (400, "bad_attachments"))
        self.assertEqual(len(chatroom.get_room(self.rid, public=False)["messages"]), n, "a refused message was posted")

    def test_resume_carries_the_lines_and_brings_another_chats_image_once(self):
        other = self.room("PO")
        _s, a = self.upload(PNG, room=other)
        got = []
        with mock.patch.object(dashboard.Handler, "_resume_room",
                               # Delivered, as the real one would: an ok that did nothing is refused (sends.py).
                               lambda h, room, text="", to="", key="": got.append(text)
                               or dashboard.sends.mark(room["id"], [key], "delivered") or {"ok": True, "queued": 1}):
            for _ in range(2):          # a lost reply, sent again
                status, r = self.json_post("/api/room/resume", {"roomId": self.rid, "text": "see", "attachments": [{"room": other, "name": a["name"]}]})
                self.assertEqual(status, 200, r)
        copy = self.folder() / a["name"]
        self.assertEqual(got[0], f"see\n\n[point P1]\n\n[image] {copy}")
        self.assertEqual(copy.read_bytes(), PNG)
        self.assertEqual(os.listdir(self.folder()), [a["name"]], "a retried send copied the image again")

    def test_a_claude_transcript_gets_its_image_lines_back(self):
        # What Claude Code writes when "[image] <path>" is typed into it (seen live).
        att = self.folder()
        lines = [
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": "[Image #1][Image #2]What do these show?\n[image]\n[image]"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": f"[Image: source: {att / 'a.png'}]"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": f"[Image: source: {att / 'b c.png'}]"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Two screenshots."}]}},
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Image #3] a picture of my own"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": "[Image: source: C:\\Pictures\\cat.png]"}]}},
        ]
        t = Path(self.tmp.name) / "t.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        turns = dashboard._claude_text_turns(t)
        self.assertEqual([x["text"] for x in turns], [
            f"What do these show?\n\n[image] {att / 'a.png'}\n[image] {att / 'b c.png'}",
            "Two screenshots.",
            "[Image #3] a picture of my own",
        ])

    def test_a_po_chat_without_a_task_folder_gets_its_image_lines_back(self):
        # A PO room has no task folder: its images live in <state>/attachments/<room id>/.
        po = self.room("PO")
        _s, a = self.upload(PNG, room=po)
        lines = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Image #1]look\n[image]"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": f"[Image: source: {a['path']}]"}]}},
        ]
        t = Path(self.tmp.name) / "po.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        self.assertEqual([x["text"] for x in dashboard._claude_text_turns(t)], [f"look\n\n[image] {a['path']}"])
        self.assertFalse(attachments.is_attachment_path(self.state / "other" / "room-1" / "x.png", self.state))

    def test_a_retry_while_the_key_is_held_reuses_a_numbered_copy(self):
        other = self.room("PO")
        _s, a = self.upload(PNG, room=other, name="shot.png")
        self.folder().mkdir(parents=True, exist_ok=True)
        (self.folder() / "shot.png").write_bytes(JPG)        # a different image of that name is already here
        room = chatroom.get_room(self.rid, public=False)
        first = dashboard.attachment_paths(room, [{"room": other, "name": a["name"]}])
        again = dashboard.attachment_paths(room, [{"room": other, "name": a["name"]}])
        self.assertEqual(first, again)
        self.assertEqual(Path(first[0]).name, "shot-2.png")
        self.assertEqual(sorted(os.listdir(self.folder())), ["shot-2.png", "shot.png"])

    def test_a_duplicate_send_is_answered_before_its_images_are_looked_for(self):
        other = self.room("PO")
        _s, a = self.upload(PNG, room=other)
        body = {"roomId": self.rid, "text": "see", "key": "k1", "attachments": [{"room": other, "name": a["name"]}]}
        status, r = self.json_post("/api/room/say", body)
        self.assertEqual(status, 200, r)
        os.remove(a["path"])                                   # gone before the lost reply is retried
        status, r = self.json_post("/api/room/say", body)
        self.assertEqual((status, r.get("duplicate")), (200, True), r)

    # ---- the text ----

    def test_an_image_named_inside_a_point_takes_its_place(self):
        # The chat editor writes "[image] <name>" inside the point an image
        # was pasted into; the hub puts the stored path there. Images the
        # text does not name end it, as ever; split_images sees only those.
        text = "## Points (2)\n\n**1.** a\n[image] a.png\n\n[point P1]\n\n**2.** b\n\n[point P2]"
        out = message_refs.with_images(text, ["C:\\t\\attachments\\a.png", "/t/attachments/b.png"], ["a.png", "b.png"])
        self.assertEqual(out, "## Points (2)\n\n**1.** a\n[image] C:\\t\\attachments\\a.png\n\n[point P1]\n\n**2.** b\n\n[point P2]\n\n[image] /t/attachments/b.png")
        self.assertEqual(message_refs.split_images(out)[1], ["/t/attachments/b.png"])
        self.assertEqual(message_refs.all_images(out), ["C:\\t\\attachments\\a.png", "/t/attachments/b.png"])
        # Without the given names the file's own name places it; a name used
        # twice takes the first line; an unrelated text is unchanged.
        self.assertEqual(message_refs.with_images("x\n[image] a.png\n[image] a.png", ["/t/a.png"]), "x\n[image] /t/a.png\n[image] a.png")
        self.assertEqual(message_refs.with_images("x\n[image] other.png", ["/t/a.png"]), "x\n[image] other.png\n\n[image] /t/a.png")
        self.assertEqual(message_refs.with_images("plain", ["/t/a.png"], ["a.png"]), "plain\n\n[image] /t/a.png")

    def test_a_head_image_stays_above_the_first_point(self):
        # The editor names the head's images under the head: the hub's path
        # goes there, not to the end of the message (which is the last point).
        import points
        text = "## Points (2)\n\nIntro\n[image] h.png\n\n**1.** a\n[image] a.png\n\n**2.** b"
        typed = points._deliverable(text, ["P1", "P2"])
        out = message_refs.with_images(typed, ["/t/attachments/h.png", "/t/attachments/a.png"], ["h.png", "a.png"])
        self.assertEqual(out, ("## Points (2)\n\nIntro\n[image] /t/attachments/h.png\n\n"
                               "**1.** a\n[image] /t/attachments/a.png\n\n[point P1]\n\n**2.** b\n\n[point P2]"))
        head, items = message_refs.split_items(out)
        self.assertEqual(head, "## Points (2)\n\nIntro\n[image] /t/attachments/h.png")
        self.assertNotIn("[image]", items[1], "the last point has no image of its own")
        self.assertEqual(message_refs.split_images(out)[1], [], "none ends the message")
        self.assertEqual(message_refs.all_images(out), ["/t/attachments/h.png", "/t/attachments/a.png"])
        # Only the head has one: the same.
        alone = message_refs.with_images("## Points (1)\n\n[image] h.png\n\n**1.** a\n\n[point P1]", ["/t/attachments/h.png"], ["h.png"])
        self.assertEqual(alone, "## Points (1)\n\n[image] /t/attachments/h.png\n\n**1.** a\n\n[point P1]")

    def test_say_places_an_image_inside_its_point(self):
        _s, a = self.upload(PNG)
        _s, b = self.upload(JPG, name="b.jpg")
        body = f"## Points (2)\n\n**1.** first\n[image] {a['name']}\n\n**2.** second\n[image] {b['name']}"
        status, r = self.json_post("/api/room/say", {"roomId": self.rid, "text": body,
                                                     "attachments": [{"room": self.rid, "name": a["name"]}, b["name"]]})
        self.assertEqual(status, 200, r)
        text = chatroom.get_room(self.rid, public=False)["messages"][-1]["text"]
        self.assertEqual(text, f"## Points (2)\n\n**1.** first\n[image] {a['path']}\n\n[point P1]\n\n"
                               f"**2.** second\n[image] {b['path']}\n\n[point P2]")

    def test_a_claude_transcript_gets_a_points_image_back_in_its_point(self):
        att = self.folder()
        typed = f"## Points (2)\n\n**1.** first\n[image]\n\n[point P1]\n\n**2.** second\n\n[point P2]\n\n[image]"
        lines = [
            {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Image #1][Image #2]" + typed}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": f"[Image: source: {att / 'a.png'}]"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": [{"type": "text", "text": f"[Image: source: {att / 'b.png'}]"}]}},
        ]
        t = Path(self.tmp.name) / "p.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        self.assertEqual([x["text"] for x in dashboard._claude_text_turns(t)], [
            f"## Points (2)\n\n**1.** first\n[image] {att / 'a.png'}\n\n[point P1]\n\n**2.** second\n\n[point P2]\n\n[image] {att / 'b.png'}"])

    def transcript_turns(self, text, sources, one_entry=True):
        """The turns read from a transcript where ``text`` was typed and Claude
        Code logged its images' ``sources``: in one meta entry, a text block
        each (2.1.x, seen 2026-09-26), or an entry each (older)."""
        user = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]
                + [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBOR"}}] * len(sources)}}
        block = lambda p: {"type": "text", "text": f"[Image: source: {p}]"}
        meta = lambda ps: {"type": "user", "isMeta": True, "message": {"role": "user", "content": [block(p) for p in ps]}}
        lines = [user] + ([meta(sources)] if one_entry else [meta([p]) for p in sources])
        t = Path(self.tmp.name) / "m.jsonl"
        t.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        return [x["text"] for x in dashboard._claude_text_turns(t)]

    def test_every_points_image_comes_back_in_its_point(self):
        # P47: five points, 3 to 5 with a screenshot each. Claude Code began the
        # turn with a line of placeholders and logged the three sources in one
        # entry: only the first came back, and the line stayed.
        att = self.folder()
        a, b, c = (att / f"2026-09-26 06.4{i}.00 screenshot.png" for i in (3, 4, 7))
        typed = ("[Image #2] [Image #3] [Image #4]\n\n<pasted_content id=\"a6b4\">\n## Points (5)\n**1.** one\n[point P42]\n"
                 "**2.** two\n[point P43]\n**3.** three\n[image]\n[point P44]\n**4.** four\n[image]\n[point P45]\n"
                 "**5.** five\n[image]\n[point P46]\n</pasted_content id=\"a6b4\">\n")
        want = (f"## Points (5)\n**1.** one\n[point P42]\n**2.** two\n[point P43]\n**3.** three\n[image] {a}\n[point P44]\n"
                f"**4.** four\n[image] {b}\n[point P45]\n**5.** five\n[image] {c}\n[point P46]")
        self.assertEqual(self.transcript_turns(typed, [a, b, c]), [want])
        self.assertEqual(self.transcript_turns(typed, [a, b, c], one_entry=False), [want], "an entry each, as before")
        # Two of three points with one; the last point's image is its last line.
        two = "[Image #1][Image #2]## Points (3)\n\n**1.** one\n[image]\n\n[point P1]\n\n**2.** two\n\n[point P2]\n\n**3.** three\n[image]\n\n[point P3]"
        self.assertEqual(self.transcript_turns(two, [a, b]), [
            f"## Points (3)\n\n**1.** one\n[image] {a}\n\n[point P1]\n\n**2.** two\n\n[point P2]\n\n**3.** three\n[image] {b}\n\n[point P3]"])

    def test_one_two_and_three_images_without_points(self):
        att = self.folder()
        ps = [att / "a.png", att / "b.png", att / "c.png"]
        for n in (1, 2, 3):
            typed = "".join(f"[Image #{i + 1}] " for i in range(n)) + "what do these show?\n" + "\n".join(["[image]"] * n)
            lines = "\n".join(f"[image] {p}" for p in ps[:n])
            self.assertEqual(self.transcript_turns(typed, ps[:n]), [f"what do these show?\n\n{lines}"], n)
            self.assertEqual(self.transcript_turns(typed, ps[:n], one_entry=False), [f"what do these show?\n\n{lines}"], n)
        # Images only, no words.
        self.assertEqual(self.transcript_turns("[Image #1][Image #2]\n[image]\n[image]", ps[:2]),
                         [f"[image] {ps[0]}\n[image] {ps[1]}"])

    def test_an_image_from_elsewhere_keeps_its_placeholder_for_the_page(self):
        # Not this chat's: the hub cannot show it; the page drops the placeholder.
        att = self.folder()
        self.assertEqual(self.transcript_turns("[Image #1] [Image #2] mine\n[image]", ["C:\\Pictures\\cat.png", att / "a.png"]),
                         [f"[Image #1] mine\n\n[image] {att / 'a.png'}"])

    def test_a_codex_turn_with_an_image_keeps_its_words(self):
        # What codex logs for an image pasted into it (seen live): the turn began
        # with "<image name=…>" and was dropped as injected context.
        from agents import codex
        content = [{"type": "input_text", "text": "<image name=[Image #1]>"}, {"type": "input_image", "image_url": "data:x"},
                   {"type": "input_text", "text": "</image>"}, {"type": "input_text", "text": "[Image #1] can you see this"}]
        text = codex._text_of(content)
        self.assertEqual(text, "[Image #1] can you see this")
        self.assertFalse(codex._is_synthetic(text))
        self.assertEqual(codex._text_of([{"type": "input_text", "text": "<environment_context>x</environment_context>"}]),
                         "<environment_context>x</environment_context>")

    def test_with_images_and_split_images(self):
        paths = [r"C:\t\attachments\a.png", "/t/attachments/b c.png"]
        text = message_refs.with_images("hello", paths)
        self.assertEqual(text, "hello\n\n[image] C:\\t\\attachments\\a.png\n[image] /t/attachments/b c.png")
        self.assertEqual(message_refs.split_images(text), ("hello", paths))
        self.assertEqual(message_refs.with_images("", paths[:1]), "[image] C:\\t\\attachments\\a.png")
        self.assertEqual(message_refs.with_images("hello", []), "hello")
        self.assertEqual(message_refs.split_images("[image] x.png\nthen words"), ("[image] x.png\nthen words", []))

    def test_links_are_written_out_and_the_images_stay_last(self):
        text = message_refs.with_images("see [#m1](msg:m1)", ["/t/a.png"])
        lookup = lambda mid: {"id": mid, "from": "codex", "text": "the earlier point", "ts": time.time()}
        out = message_refs.expand_message_refs(text, lookup)
        self.assertTrue(out.endswith("\n[image] /t/a.png"), out)
        self.assertEqual(message_refs.split_images(out)[1], ["/t/a.png"])
        self.assertEqual(message_refs.split_images(message_refs.strip_message_refs(text))[1], ["/t/a.png"])


if __name__ == "__main__":
    unittest.main()
