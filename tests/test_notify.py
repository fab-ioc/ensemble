"""notify.py (#359): what notifies, once, on which devices.

* a new Inbox card notifies; nothing is read or sent with no device;
* the first look (and the first after a device is added to a hub that had
  none) only takes note; a restart re-sends nothing;
* a PO's reply to the person notifies once, and its point card is then not a
  second notification (and the other way round); a reply to a hub line or a
  report does not notify;
* bursts: more from one project within BURST_S replace the first under its
  tag ("3 new in Motors", a tap opens /?inbox=<project>); later, a new tag;
* a device in front is skipped, the others still get it; the test push goes
  to this browser whatever;
* a 404/410 from the push service removes the device;
* the store: push hosts allowed, keys checked, key and device files mode 600;
* texts: no file path, a link becomes its words;
* routes: only the dashboard page (403 page_only otherwise).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import dashboard  # noqa: E402
import notify  # noqa: E402
import webpush  # noqa: E402
from tests.test_points import _World, http, turn  # noqa: E402

EP1 = "https://fcm.googleapis.com/fcm/send/device-one"
EP2 = "https://web.push.apple.com/device-two"


def sub(endpoint):
    key = webpush.new_private_key()
    return {"endpoint": endpoint, "keys": {"p256dh": webpush.b64u(webpush.public_bytes(key)),
                                           "auth": webpush.b64u(os.urandom(16))}}


class _Notify(_World):
    def setUp(self):
        super().setUp()
        notify.bind(dashboard)
        notify._VAPID = None
        notify._UNAVAILABLE = ""
        notify._FRONT.clear()
        notify._BURSTS.clear()
        notify._PO_SEEN.clear()
        notify._RESEED = False
        notify._STARTED = time.time() - 3600
        self.sent: list[tuple[str, dict, str]] = []
        self.code = 201

        def fake_send(vapid, dev, payload, topic="", timeout=0, **_kw):
            import json
            self.sent.append((dev["endpoint"], json.loads(payload), topic))
            return self.code

        notify._sender = fake_send
        self.addCleanup(setattr, notify, "_sender", None)
        self.projects: list[dict] = []
        p = mock.patch.object(dashboard, "load_projects", side_effect=lambda: list(self.projects))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(notify, "_log")
        p.start()
        self.addCleanup(p.stop)
        self.now = time.time()

    def device(self, endpoint=EP1, label="Mac · Safari app"):
        rec, why = notify.subscribe(sub(endpoint), label)
        self.assertEqual(why, "")
        return rec

    def card(self, rid="room-aaaa0001", since=None, kind="answer", pid="proj-m", **kw):
        since = self.now - 5 if since is None else since
        row = {"id": f"{kind}:{rid}:{kw.pop('n', 1)}", "kind": kind, "roomId": rid, "projectId": pid,
               "project": "Motors", "since": since, "line": "Which colour?", "mid": kw.pop("mid", ""), **kw}
        return row

    def look(self, rows, at=None, pending=False, reseed=False):
        snap = {"rows": rows, "pending": pending}
        return notify.events(at or self.now, snap=snap, reseed=reseed)


class Store(_Notify):
    def test_push_hosts_and_keys(self):
        self.assertEqual(notify.endpoint_problem(EP1), "")
        self.assertEqual(notify.endpoint_problem(EP2), "")
        for bad in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://evil.example/x",
                    "https://fcm.googleapis.com:8443/x", "https://u:p@fcm.googleapis.com/x",
                    "https://googleapis.com.evil.example/x", "", None, "https://fcm.googleapis.com/" + "x" * 2000):
            self.assertNotEqual(notify.endpoint_problem(bad), "", bad)
        s = sub(EP1)
        s["keys"]["auth"] = webpush.b64u(b"short")
        self.assertIsNone(notify.subscribe(s, "x")[0])
        s = sub(EP1)
        s["keys"]["p256dh"] = webpush.b64u(b"\x04" + b"\x01" * 64)
        self.assertIsNone(notify.subscribe(s, "x")[0])
        self.assertIsNone(notify.subscribe(sub("https://intranet.local/push"), "x")[0])

    def test_files_are_private_and_status_hides_addresses(self):
        rec = self.device()
        if os.name != "nt":
            for name in ("vapid.json", "devices.json"):
                mode = stat.S_IMODE((notify._dir() / name).stat().st_mode)
                self.assertEqual(mode, 0o600, name)
        st = notify.status(EP1)
        self.assertTrue(st["available"])
        self.assertEqual(st["here"], rec["id"])
        self.assertNotIn("endpoint", st["devices"][0])
        self.assertNotIn(EP1, repr(st))
        self.assertEqual(notify.status(EP2)["here"], "")
        # The key is kept: the same public key after a restart.
        key = st["publicKey"]
        notify._VAPID = None
        self.assertEqual(notify.status()["publicKey"], key)

    def test_remove_and_unsubscribe(self):
        a, b = self.device(EP1), self.device(EP2, "iPhone")
        self.assertTrue(notify.remove(b["id"]))
        self.assertFalse(notify.remove(b["id"]))
        self.assertTrue(notify.unsubscribe(EP1))
        self.assertEqual(notify.status()["devices"], [])
        self.assertTrue(a)

    def test_clip(self):
        self.assertEqual(notify.clip("See [the plan](https://x/y) in /home/fab/p/a.md now", 80),
                         "See the plan in … now")
        self.assertEqual(notify.clip("**Done**\nsecond line", 80), "Done")
        self.assertEqual(notify.clip("a" * 50, 10), "a" * 9 + "…")

    def test_clip_keeps_addresses_markup_paths_and_tokens_off_a_lock_screen(self):
        cases = {
            "open https://example.org/private?token=secret now": "open … now",
            "see www.example.org/x first": "see … first",
            "<b>secret</b> here": "secret here",
            "read notes/private/file.txt please": "read … please",
            "edit src/app.py and/or main": "edit … and/or main",
            "token=abc123 and password: hunter2x": "token=… and password=…",
            "send Bearer eyJhbGciOiJFUzI1NiJ9abc": "send Bearer …",
            "key ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4 used": "key … used",
            "See ~/secret now": "See … now",
            "Use C:\\secret now": "Use … now",
            "API key: hunter2x": "API key=…",
            'token: "abc 123" ok': "token=… ok",
            "private key = abc": "private key=…",
            "Bearer in mind": "Bearer in mind",
            "2026NovemberReleaseSchedule ok": "2026NovemberReleaseSchedule ok",
            # Ordinary words stay.
            "commit 3273e44 merged; 1/2 done": "commit 3273e44 merged; 1/2 done",
            "the auth flow and the token are ready": "the auth flow and the token are ready",
        }
        for text, want in cases.items():
            self.assertEqual(notify.clip(text, 200), want, text)

    def test_clip_hides_a_credential_in_json(self):
        self.assertEqual(notify.clip('{"token": "abc 123"}', 200), '{"token=…}')
        self.assertEqual(notify.clip("{'api_key': 'k9x'} set", 200), "{'api_key=…} set")
        self.assertEqual(notify.clip('got {"password":"hunter2"} back', 200), 'got {"password=…} back')

    def test_clip_hides_an_explicit_relative_path(self):
        self.assertEqual(notify.clip("open ./secret now", 200), "open … now")
        self.assertEqual(notify.clip("open ../secret now", 200), "open … now")
        self.assertEqual(notify.clip("run .\\build.ps1 then", 200), "run … then")
        # Dots that are not a path stay.
        self.assertEqual(notify.clip("well... v2.0 done. See . and ..", 200), "well... v2.0 done. See . and ..")

    def test_a_first_device_right_after_the_last_went_sends_no_backlog(self):
        # Removed and re-added within one watcher interval: tick() never saw
        # the empty list, so subscribe() itself marks the next look a seed.
        self.look([])
        a = self.device()
        notify._RESEED = False
        self.assertTrue(notify.remove(a["id"]))
        self.device(EP2)
        self.assertTrue(notify._RESEED)
        with mock.patch.object(dashboard.inbox, "snapshot") as snap, \
                mock.patch.object(notify, "deliver") as deliver:
            snap.return_value = {"rows": [self.card(n=1)]}         # made while off
            self.assertEqual(notify.tick(self.now), [])
        deliver.assert_not_called()
        self.device(EP1)                                          # a second device: no reseed
        self.assertFalse(notify._RESEED)

    def test_a_device_added_during_a_look_gets_none_of_its_finds(self):
        # tick() has read the device list (A) and cleared the reseed; A goes
        # and B comes while it reads the Inbox: B is not sent that backlog.
        self.look([])
        a = self.device(EP1)
        notify._RESEED = False
        row = self.card(n=1)

        def mid_look(now, reseed=False, **_kw):
            notify.remove(a["id"])
            self.device(EP2)
            return [e for e in notify.row_events([row])]

        with mock.patch.object(notify, "events", side_effect=mid_look):
            shown = notify.tick(self.now)
        self.assertEqual(len(shown), 1)
        notify._pool().submit(lambda: None).result(timeout=5)
        self.assertEqual(self.sent, [])
        self.assertTrue(notify._RESEED)                            # B's first look takes note
        # A device there from the start is sent what the look found.
        notify._RESEED = False
        notify._BURSTS.clear()

        def found(now, reseed=False, **_kw):
            return notify.row_events([self.card(rid="room-aaaa0002", n=2)])

        with mock.patch.object(notify, "events", side_effect=found):
            notify.tick(self.now + 60)
        t = time.time()
        while not self.sent and time.time() - t < 5:
            time.sleep(0.02)
        self.assertEqual([e for e, _p, _t in self.sent], [EP2])


class Cards(_Notify):
    def test_no_device_reads_and_sends_nothing(self):
        notify._VAPID = None
        with mock.patch.object(notify, "events") as ev:
            self.assertEqual(notify.tick(self.now), [])
        ev.assert_not_called()
        self.assertTrue(notify._RESEED)

    def test_seed_then_a_new_card(self):
        old = self.card(n=1)
        self.assertEqual(self.look([old]), [])                 # first run: takes note
        new = self.card(n=2)
        evs = self.look([old, new])
        self.assertEqual([e["key"] for e in evs], [f"answer:room-aaaa0001:2|{int(new['since'])}"])
        self.assertEqual(evs[0]["title"], "Motors · Question")
        self.assertEqual(evs[0]["body"], "Which colour?")
        self.assertTrue(evs[0]["path"].startswith("/session?room=room-aaaa0001"))
        self.assertEqual(self.look([old, new]), [])              # once

    def test_restart_resends_nothing(self):
        self.look([])
        self.look([self.card(n=1)])
        notify._BURSTS.clear()
        notify._PO_SEEN.clear()
        self.assertEqual(self.look([self.card(n=1)]), [])

    def test_first_device_on_a_hub_that_had_none_takes_note(self):
        self.look([])
        notify.tick(self.now)                                    # no device yet
        self.assertTrue(notify._RESEED)
        self.device()
        with mock.patch.object(dashboard.inbox, "snapshot") as snap, \
                mock.patch.object(notify, "deliver") as deliver:
            snap.return_value = {"rows": [self.card(n=1)]}
            self.assertEqual(notify.tick(self.now), [])          # the backlog is not news
            snap.return_value = {"rows": [self.card(n=1), self.card(n=2)]}
            shown = notify.tick(self.now + 11)
        deliver.assert_called_once()
        self.assertEqual(len(shown), 1)

    def test_every_kind_and_a_new_state_is_a_new_key(self):
        self.look([])
        rows = [self.card(n=1, kind="answer"),
                self.card(rid="room-aaaa0002", n=2, kind="check", line="Ready"),
                self.card(rid="room-aaaa0003", n=3, kind="waiting", line="Blocked on a decision"),
                self.card(rid="room-aaaa0004", n=4, kind="waiting", cause="folder_missing", line="Folder gone")]
        titles = [e["title"] for e in self.look(rows)]
        self.assertEqual(titles, ["Motors · Question", "Motors · Ready for your check",
                                  "Motors · Waiting on you", "Motors · Could not start"])
        again = dict(rows[2], since=self.now + 30)
        self.assertEqual(len(self.look([again], at=self.now + 40)), 1)

    def test_an_old_card_first_seen_late_is_not_news(self):
        self.look([])
        notify._STARTED = self.now
        late = self.card(n=1, since=self.now - 3 * 3600)
        self.assertEqual(self.look([late]), [])
        stalled = self.card(n=2, kind="waiting", since=self.now - 3 * 3600)
        self.assertEqual(len(self.look([stalled])), 1)          # a stall is noticed late

    def test_pending_waiting_row_with_a_mid_waits(self):
        self.look([])
        row = self.card(n=1, kind="waiting", mid="sid-9:4")
        self.assertEqual(self.look([row], pending=True), [])
        self.assertEqual(len(self.look([row])), 1)

    def test_a_failed_build_is_not_a_seed(self):
        notify.events(self.now, snap={"rows": [], "error": "not built"})
        self.assertEqual(self.look([self.card(n=1)]), [])        # still the first real look


class PoReplies(_Notify):
    def setUp(self):
        super().setUp()
        self.rid = self.solo_room("sid-1")
        self.projects = [{"id": "proj-m", "name": "Motors", "poRoomId": self.rid}]
        self.add("sid-1", turn("user", "[rotation] You are the PO.", self.t0),
                 turn("assistant", "Ready.", self.t0 + 2))
        self.look([])                                            # seed: watermark at now

    def reply(self, text, after_user="Is the release on track?", dt=10):
        self.now += dt
        self.add("sid-1", turn("user", after_user, self.now), turn("assistant", text, self.now + 1))
        self.now += 2

    def settle(self, rows=()):
        """The transcript changed: one look notes it, the next (still) reads it."""
        first = self.look(list(rows))
        self.now += notify.TICK_S
        return first + self.look(list(rows))

    def test_a_reply_to_the_person_notifies_once(self):
        self.reply("Yes: **on track** for Friday, see /home/fab/x/plan.md")
        evs = self.settle()
        self.assertEqual(len(evs), 1)
        ev = evs[0]
        self.assertEqual(ev["title"], "Motors PO")
        self.assertEqual(ev["body"], "Yes: on track for Friday, see …")
        self.assertEqual(ev["path"], f"/session?room={self.rid}&msg=sid-1:3")
        self.now += notify.TICK_S
        self.assertEqual(self.look([]), [])
        notify._PO_SEEN.clear()                                  # a restart
        self.now += notify.TICK_S
        self.assertEqual(self.settle(), [])

    def test_a_reply_to_a_hub_line_or_a_report_does_not(self):
        self.reply("Noted.", after_user="[report] #12 is done")
        self.reply("Restarted.", after_user="[rotation] You are taking over.")
        self.assertEqual(self.settle(), [])

    def test_its_point_card_is_not_a_second_notification(self):
        self.reply("Re P3: yes")
        mid = "sid-1:3"
        evs = self.settle()
        self.assertEqual([e["kind"] for e in evs], ["po"])
        pts = {"id": f"check:{self.rid}:points", "kind": "check", "roomId": self.rid, "projectId": "proj-m",
               "project": "Motors", "since": self.now,
               "items": [{"points": ["P3"], "mid": mid, "since": self.now, "line": "Re P3: yes"}]}
        self.assertEqual(self.look([pts]), [])

    def test_a_point_card_first_then_the_reply_is_skipped(self):
        mid = "sid-1:3"
        pts = {"id": f"check:{self.rid}:points", "kind": "check", "roomId": self.rid, "projectId": "proj-m",
               "project": "Motors", "since": self.now + 12,
               "items": [{"points": ["P3"], "mid": mid, "since": self.now + 12, "line": "Re P3: yes"}]}
        self.reply("Re P3: yes")
        first = self.look([pts])
        self.assertEqual([e["kind"] for e in first], ["points"])
        self.now += notify.TICK_S
        self.assertEqual(self.look([pts]), [])

    def test_a_transcript_still_being_written_is_read_when_it_holds_still(self):
        self.reply("Part one.")
        self.assertEqual(self.look([]), [])                      # just changed
        self.reply("Part two.", dt=1)
        self.assertEqual(self.look([]), [])                      # changed again
        self.now += notify.TICK_S
        self.assertEqual([e["body"] for e in self.look([])], ["Part one.", "Part two."])

    def test_a_transcript_that_never_holds_still_is_read_every_while(self):
        got = []
        for _ in range(5):
            self.reply("More.", dt=notify.TICK_S - 3)
            got += self.look([])
        self.assertTrue(got)


class Bursts(_Notify):
    def test_one_then_three_new_then_a_new_tag(self):
        ev = lambda n: {"projectId": "proj-m", "project": "Motors", "title": "Motors · Question",
                        "body": f"line {n}", "path": f"/session?room=r&msg={n}"}
        a = notify.bursts([ev(1)], self.now)
        self.assertEqual(a[0]["title"], "Motors · Question")
        self.assertEqual(a[0]["path"], "/session?room=r&msg=1")
        b = notify.bursts([ev(2), ev(3)], self.now + 10)
        self.assertEqual(b[0]["tag"], a[0]["tag"])
        self.assertEqual(b[0]["title"], "3 new in Motors")
        self.assertEqual(b[0]["body"], "Newest: line 3 · and 2 more")
        self.assertEqual(b[0]["path"], "/?inbox=proj-m")
        c = notify.bursts([ev(4)], self.now + notify.BURST_S + 1)
        self.assertNotEqual(c[0]["tag"], a[0]["tag"])
        self.assertEqual(c[0]["title"], "Motors · Question")

    def test_projects_burst_apart(self):
        x = {"projectId": "proj-m", "project": "Motors", "title": "t", "body": "b", "path": "/"}
        y = dict(x, projectId="proj-w", project="Wheels")
        out = notify.bursts([x, y], self.now)
        self.assertEqual(len({o["tag"] for o in out}), 2)


class Sending(_Notify):
    def test_a_device_in_front_is_skipped(self):
        a, b = self.device(EP1), self.device(EP2, "iPhone")
        notify.seen(EP1, True, now=self.now)
        to = notify.deliver({"title": "t", "body": "b", "path": "/", "tag": "x"}, self.now, wait=True)
        self.assertEqual(to, [b["id"]])
        self.assertEqual([s[0] for s in self.sent], [EP2])
        self.assertEqual(self.sent[0][1], {"title": "t", "body": "b", "path": "/", "tag": "x"})
        self.assertEqual(len(self.sent[0][2]), 32)               # Topic: the tag's hash
        # Out of front after FRONT_S, or at once when the page says so.
        self.assertFalse(notify.in_front(a["id"], self.now + notify.FRONT_S + 1))
        notify.seen(EP1, True, now=self.now)
        notify.seen(EP1, False, now=self.now)
        self.assertFalse(notify.in_front(a["id"], self.now))

    def test_the_test_push_ignores_front(self):
        self.device(EP1)
        notify.seen(EP1, True)
        r = notify.send_test(EP1)
        self.assertTrue(r["ok"], r)
        self.assertEqual([s[0] for s in self.sent], [EP1])
        self.assertFalse(notify.send_test(EP2)["ok"])

    def test_gone_removes_the_device_and_errors_are_kept(self):
        a = self.device(EP1)
        self.code = 500
        notify.deliver({"title": "t"}, self.now, wait=True)
        self.assertEqual(notify.status()["devices"][0]["lastError"], "push service answered 500")
        self.code = 201
        notify.deliver({"title": "t"}, self.now, wait=True)
        dev = notify.status()["devices"][0]
        self.assertNotIn("lastError", {k for k, v in dev.items() if v})
        self.assertTrue(dev["lastOkAt"])
        self.code = 410
        notify.deliver({"title": "t"}, self.now, wait=True)
        self.assertEqual(notify.status()["devices"], [])
        self.assertTrue(a)


class Routes(_Notify):
    def test_only_the_page(self):
        p = mock.patch.object(dashboard.Handler, "_ui_cookie", return_value="ensemble_ui_1")
        p.start()
        self.addCleanup(p.stop)
        for path in ("/api/notify/status", "/api/notify/subscribe", "/api/notify/unsubscribe",
                     "/api/notify/remove", "/api/notify/test", "/api/notify/seen"):
            status, r = http(path, {"endpoint": EP1})
            self.assertEqual((status, r.get("error")), (403, "page_only"), path)

    def test_the_page_subscribes_and_reads_its_status(self):
        with mock.patch.object(dashboard.Handler, "_page_refusal", return_value=""):
            status, r = http("/api/notify/subscribe", {"subscription": sub(EP1), "label": "Mac · Chrome",
                                                       "endpoint": EP1})
            self.assertEqual(status, 200, r)
            self.assertTrue(r["device"]["label"].startswith("Mac · Chrome"))
            self.assertEqual(r["here"], r["device"]["id"])
            status, r = http("/api/notify/seen", {"endpoint": EP1, "front": True})
            self.assertEqual((status, r["ok"]), (200, True))
            status, r = http("/api/notify/subscribe", {"subscription": sub("https://evil.example/x")})
            self.assertEqual(status, 400)
            self.assertEqual(http("/api/notify/nope", {})[0], 404)
            status, r = http("/api/notify/unsubscribe", {"endpoint": EP1})
            self.assertEqual((status, r["removed"], r["devices"]), (200, True, []))


@unittest.skipUnless(shutil.which("node"), "needs Node")
class WorkerLinks(unittest.TestCase):
    """sw.js hubUrl: a tap opens only a page of this hub."""

    def test_only_this_origin(self):
        src = (Path(dashboard.__file__).parent / "static" / "sw.js").read_text(encoding="utf-8")
        fn = src[src.index("function hubUrl("):]
        fn = fn[:fn.index("\n}\n") + 2]
        js = ("const self = { location: { origin: 'https://hub.example' } };\n" + fn
              + "\nconsole.log(JSON.stringify(JSON.parse(process.argv[1]).map(hubUrl)));")
        paths = ["/session?room=r&msg=m", "/?inbox=p", "/\\evil.example/x", "//evil.example/x",
                 "https://evil.example/", "/\\\\evil.example", "javascript:alert(1)", None, ""]
        out = subprocess.run(["node", "-e", js, json.dumps(paths)], capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        home = "https://hub.example/"
        self.assertEqual(json.loads(out.stdout), ["https://hub.example/session?room=r&msg=m",
                                                  "https://hub.example/?inbox=p",
                                                  home, home, home, home, home, home, home])


if __name__ == "__main__":
    unittest.main()
