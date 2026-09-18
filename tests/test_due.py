"""What a handover says is due at a time (due.py): the section is read, an item
whose time has come is typed once into its idle agent, a busy one is left, a PO
that is not running gets a chat line instead, and nothing old is delivered."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import dashboard
import due
import rotation


def at(hour, minute=0, day=18, month=9, year=2026) -> float:
    return datetime(year, month, day, hour, minute).timestamp()


class ParseTests(unittest.TestCase):
    def lines(self, text):
        return [(i["hour"], i["minute"], i["what"]) for i in due.parse(text)]

    def test_the_section_and_its_lines(self):
        text = ("# Handover\n\n## In flight\n- 12:00 — not under Due\n\n"
                "## Due\n- 15:40 — the paper-1 hand test\n* 9:05 - chase the broker\n"
                "- **20:30** — read the evening report\n\n### Later\n- 23:59 – still Due\n"
                "## Decisions\n- 10:00 — not under Due either\n")
        self.assertEqual(self.lines(text), [(15, 40, "the paper-1 hand test"), (9, 5, "chase the broker"),
                                            (20, 30, "read the evening report"), (23, 59, "still Due")])

    def test_a_heading_with_more_words_and_any_case(self):
        self.assertEqual(self.lines("## due (local time)\n- 08:00 — x\n"), [(8, 0, "x")])
        self.assertEqual(self.lines("## Due dates\n- 08:00 — x\n"), [(8, 0, "x")])
        self.assertEqual(self.lines("## Overdue\n- 08:00 — x\n"), [])
        self.assertEqual(self.lines("no heading\n- 08:00 — x\n"), [])

    def test_a_date_with_the_time(self):
        items = due.parse("## Due\n- 09-19 08:30 — overnight report\n- 2027-01-02 07:00 — new year\n")
        self.assertEqual([(i["year"], i["month"], i["day"], i["hour"], i["minute"]) for i in items],
                         [(0, 9, 19, 8, 30), (2027, 1, 2, 7, 0)])
        self.assertEqual(items[0]["line"], "- 09-19 08:30 — overnight report")

    def test_lines_that_do_not_parse_are_skipped(self):
        text = ("## Due\n"
                "- tomorrow morning — the test\n"       # no time
                "- 25:00 — no such hour\n"
                "- 12:75 — no such minute\n"
                "- 15:40\n"                              # no what
                "- 15:40:30 — seconds\n"
                "15:40 — not a list line\n"
                "- [x] 15:40 — done already\n"
                "- ~~15:40 — struck out~~\n"
                "```\n- 15:40 — in a code fence\n```\n"
                "- [ ] 16:00 — still open\n")
        self.assertEqual(self.lines(text), [(16, 0, "still open")])

    def test_a_zone_that_is_not_the_hubs_is_ignored(self):
        text = ("## Due\n- 15:40 NY — a\n- 15:40 ET b\n- 15:40 (UTC) — c\n- 15:40 SGT — d\n"
                "- 15:40 host — e\n- 15:40 (local time) — f\n- 15:40 TWS restart check\n"
                "- 15:40: the colon form\n")
        self.assertEqual([w for _, _, w in self.lines(text)],
                         ["e", "f", "TWS restart check", "the colon form"])

    def test_nothing_crashes_on_odd_input(self):
        for text in ("", None, "## Due", "## Due\n-\n- :\n- 99-99 10:00 — x\n", "\x00\n## Due\n- 1:1 — x"):
            due.parse(text)
        bad = due.parse("## Due\n- 02-30 10:00 — no such day\n")
        self.assertEqual(len(bad), 1)
        self.assertIsNone(due.resolve(bad[0], at(11, 20)))


class ResolveTests(unittest.TestCase):
    def item(self, line):
        return due.parse("## Due\n" + line)[0]

    def test_a_bare_time_is_the_first_one_after_it_was_written(self):
        self.assertEqual(due.resolve(self.item("- 15:40 — x"), at(11, 20)), at(15, 40))
        self.assertEqual(due.resolve(self.item("- 11:20 — x"), at(11, 20) + 40), at(11, 20))
        # Written after that time of day: tomorrow's.
        self.assertEqual(due.resolve(self.item("- 00:30 — x"), at(23, 50)), at(0, 30, day=19))
        self.assertEqual(due.resolve(self.item("- 15:40 — x"), at(15, 41)), at(15, 40, day=19))

    def test_a_date_takes_the_nearest_year(self):
        self.assertEqual(due.resolve(self.item("- 09-19 08:30 — x"), at(11, 20)), at(8, 30, day=19))
        self.assertEqual(due.resolve(self.item("- 09-17 08:30 — x"), at(11, 20)), at(8, 30, day=17))
        self.assertEqual(due.resolve(self.item("- 01-02 07:00 — x"), at(9, 0, day=30, month=12)),
                         at(7, 0, day=2, month=1, year=2027))
        self.assertEqual(due.resolve(self.item("- 2026-01-02 07:00 — x"), at(9, 0, day=30, month=12)),
                         at(7, 0, day=2, month=1, year=2026))


class _FakePty:
    def __init__(self):
        self.typed = []
        self._last_submit = 0.0
        self.refuse = False

    def alive(self) -> bool:
        return True

    def send_line(self, text):
        if self.refuse:
            raise OSError("gone")
        self.typed.append(text)
        return 0

    def last_submit(self) -> float:
        return self._last_submit


class _World(unittest.TestCase):
    """One project with a PO, its handover in a temp dir, a throwaway due.json."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.hp = self.dir / "home" / "PO-HANDOVER.md"
        self.hp.parent.mkdir()
        self.sess, self.idle, self.rotating, self.asked = _FakePty(), True, False, False
        self.owners, self.notices = [], []
        self.room = {"id": "room-po", "participants": [
            {"identity": "claude", "kind": "agent", "agent": "claude", "role": "Product owner",
             "ptyId": "pty-1", "sessionId": "sid-1", "cwd": str(self.dir / "task")}]}
        due._PARSED.clear()
        due._OWNER_PATHS.clear()
        patches = [
            mock.patch.object(dashboard, "DASHBOARD_DIR", self.dir / "state"),
            mock.patch.object(dashboard, "load_projects",
                              side_effect=lambda: [{"id": "p1", "name": "Trading", "poRoomId": "room-po"}]),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.chatroom, "get_room", side_effect=lambda rid, public=True: (
                self.room if rid in ("room-po", "room-t") else None)),
            mock.patch.object(dashboard.chatroom, "po_identity", return_value="claude"),
            mock.patch.object(dashboard.chatroom, "post_notice", side_effect=self.post_notice),
            mock.patch.object(rotation, "handover_path", return_value=self.hp),
            mock.patch.object(rotation, "running_owners", side_effect=lambda: list(self.owners)),
            mock.patch.object(rotation, "_pty", side_effect=lambda part: self.sess),
            mock.patch.object(rotation, "_idle", side_effect=lambda part, tr: self.idle),
            mock.patch.object(rotation, "_transcript_of", side_effect=lambda part: (None, lambda p: {})),
            mock.patch.object(rotation, "is_rotating", side_effect=lambda rid, ident: self.rotating),
            mock.patch.object(rotation, "awaiting_handover", side_effect=lambda rid, ident: self.asked),
            mock.patch.object(due, "_log"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post_notice(self, rid, sender, text, meta):
        self.notices.append((rid, sender, text, meta))
        return {"id": "m1"}

    def write(self, body, written=None, path=None):
        path = path or self.hp
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Handover\n\n## Due\n" + body + "\n## Decisions\n- none\n", encoding="utf-8")
        t = at(11, 20) if written is None else written
        os.utime(path, (t, t))

    def saved(self):
        return json.loads((self.dir / "state" / "due.json").read_text(encoding="utf-8"))


class WakeTests(_World):
    def test_a_passed_item_is_typed_once(self):
        self.write("- 15:40 — the paper-1 hand test\n- 20:30 — read the evening report\n")
        self.assertEqual(due.tick(at(11, 25)), [])
        self.assertEqual(due.tick(at(15, 39)), [])
        self.assertEqual(self.sess.typed, [])
        out = due.tick(at(15, 40) + 20)
        self.assertEqual([(o["line"], o["how"]) for o in out], [("- 15:40 — the paper-1 hand test", "typed")])
        self.assertEqual(self.sess.typed, [
            "[due] 15:40 — the paper-1 hand test (from PO-HANDOVER.md). This is due and you are "
            "idle: do it now, or tell sam why not."])
        self.assertEqual(dashboard.hub_input_kind(self.sess.typed[0]), {"kind": "due"})
        # The next looks, and the other item at its own time.
        self.assertEqual(due.tick(at(15, 41) + 20), [])
        self.assertEqual(due.tick(at(16, 0)), [])
        self.assertEqual(len(self.sess.typed), 1)
        due.tick(at(20, 31))
        self.assertEqual(len(self.sess.typed), 2)
        self.assertTrue(self.sess.typed[1].startswith("[due] 20:30 — read the evening report (from"))
        self.assertEqual(self.notices, [])

    def test_a_busy_po_is_not_woken_until_it_is_idle(self):
        self.write("- 15:40 — the test\n")
        self.idle = False
        self.assertEqual(due.tick(at(15, 41)), [])
        self.assertEqual(due.tick(at(15, 46)), [])
        self.assertEqual(self.sess.typed, [])
        self.idle = True
        self.sess._last_submit = time.time()        # something was just typed into it
        self.assertEqual(due.tick(at(15, 47)), [])
        self.sess._last_submit = 0.0
        self.assertEqual(len(due.tick(at(16, 30))), 1)
        self.assertEqual(len(self.sess.typed), 1)
        self.assertEqual(due.tick(at(16, 31)), [])

    def test_a_po_being_rotated_or_asked_for_its_handover_is_left_to_its_fresh_session(self):
        self.write("- 15:40 — the test\n")
        self.rotating = True
        self.assertEqual(due.tick(at(15, 41)), [])
        self.rotating, self.asked = False, True
        self.assertEqual(due.tick(at(15, 42)), [])
        self.assertEqual((self.sess.typed, self.notices), ([], []))
        self.asked = False
        self.assertEqual(len(due.tick(at(15, 43))), 1)

    def test_a_terminal_that_did_not_take_it_is_tried_again(self):
        self.write("- 15:40 — the test\n")
        self.sess.refuse = True
        self.assertEqual(due.tick(at(15, 41)), [])
        self.sess.refuse = False
        self.assertEqual(len(due.tick(at(15, 42))), 1)

    def test_items_due_together_go_in_one_line(self):
        self.write("- 15:40 — the test\n- 15:45 — chase the broker\n- 09-19 08:30 — tomorrow's\n")
        self.idle = False
        due.tick(at(15, 41))
        self.idle = True
        out = due.tick(at(15, 50))
        self.assertEqual([o["how"] for o in out], ["typed", "typed"])
        self.assertEqual(self.sess.typed, [
            "[due] 15:40 — the test; 15:45 — chase the broker (from PO-HANDOVER.md). These are due "
            "and you are idle: do them now, or tell sam why not."])
        due.tick(at(8, 31, day=19))
        self.assertTrue(self.sess.typed[1].startswith("[due] 08:30 — tomorrow's (from"))

    def test_more_than_one_line_holds_the_rest_stay_due(self):
        # Review 1: six long items came to 901 characters with the last two cut
        # off, and all six were marked delivered.
        self.write("".join(f"- 15:4{i} — item {i} " + "x" * 225 + "\n" for i in range(6)))
        out = due.tick(at(15, 50))
        first = self.sess.typed[0]
        self.assertLessEqual(len(first), due._WAKE_MAX)
        self.assertTrue(0 < len(out) < 6)
        self.assertEqual([i for i in range(6) if f"item {i} " in first], list(range(len(out))))
        self.assertTrue(first.endswith("do them now, or tell sam why not."))
        self.assertNotIn("…", first)                        # no item cut short
        # The others are still due: the next idle looks deliver them, each once.
        rest = due.tick(at(15, 51)) + due.tick(at(15, 52))
        self.assertEqual(len(out) + len(rest), 6)
        self.assertEqual(sorted(i for t in self.sess.typed for i in range(6) if f"item {i} " in t),
                         list(range(6)))
        self.assertEqual(due.tick(at(15, 53)), [])

    def test_a_long_item_stays_one_short_line(self):
        self.write("- 15:40 — " + "word " * 400 + "\n")
        due.tick(at(15, 41))
        self.assertLessEqual(len(self.sess.typed[0]), due._WAKE_MAX)
        self.assertNotIn("\n", self.sess.typed[0])
        self.assertTrue(self.sess.typed[0].endswith("or tell sam why not."))

    def test_a_stopped_po_gets_a_chat_line_and_is_not_resumed(self):
        self.write("- 15:40 — the paper-1 hand test\n")
        live, self.sess = self.sess, None
        out = due.tick(at(15, 41))
        self.assertEqual([o["how"] for o in out], ["chat"])
        self.assertEqual(len(self.notices), 1)
        rid, sender, text, meta = self.notices[0]
        self.assertEqual((rid, sender, meta), ("room-po", "ensemble", {"noticeKind": "due"}))
        self.assertIn("**Due: 15:40 — the paper-1 hand test** (from `PO-HANDOVER.md`)", text)
        self.assertIn("The PO is not running, so the hub did not wake it", text)
        # Not again while it stays stopped.
        self.assertEqual(due.tick(at(15, 46)), [])
        self.assertEqual(len(self.notices), 1)
        # Started again by the person within the day: told once, then never again.
        self.sess = live
        self.assertEqual([o["how"] for o in due.tick(at(16, 10))], ["typed"])
        self.assertEqual(due.tick(at(16, 11)), [])
        self.assertEqual((len(live.typed), len(self.notices)), (1, 1))

    def test_a_stopped_po_started_a_day_later_is_not_told(self):
        self.write("- 15:40 — the test\n")
        live, self.sess = self.sess, None
        due.tick(at(15, 41))
        self.sess = live
        self.assertEqual(due.tick(at(15, 45, day=19)), [])
        self.assertEqual(live.typed, [])

    def test_a_stopped_po_is_told_in_chat_only_of_what_is_new(self):
        self.write("- 15:40 — the test\n- 15:50 — chase\n")
        self.sess = None
        due.tick(at(15, 41))
        out = due.tick(at(15, 51))
        self.assertEqual([o["line"] for o in out], ["- 15:50 — chase"])
        self.assertEqual(len(self.notices), 2)
        self.assertNotIn("the test", self.notices[1][2])

    def test_a_project_without_a_po_room_or_a_handover_is_skipped(self):
        self.assertEqual(due.tick(at(15, 41)), [])          # no handover file
        self.room = None
        self.write("- 15:40 — the test\n")
        self.assertEqual(due.tick(at(15, 41)), [])          # the PO task is gone
        self.assertEqual(self.notices, [])


class PersistenceTests(_World):
    def test_what_was_delivered_survives_a_hub_restart(self):
        self.write("- 15:40 — the test\n")
        due.tick(at(15, 41))
        saved = self.saved()
        self.assertEqual([(r["line"], r["how"], r["due"]) for r in saved["fired"].values()],
                         [("- 15:40 — the test", "typed", at(15, 40))])
        self.assertEqual(saved["seen"][str(self.hp)]["- 15:40 — the test"]["due"], at(15, 40))
        due._PARSED.clear()                     # a new process
        self.assertEqual(due.tick(at(15, 46)), [])
        self.assertEqual(len(self.sess.typed), 1)

    def test_editing_the_rest_of_the_handover_does_not_move_a_time(self):
        self.write("- 15:40 — the test\n")
        due.tick(at(11, 25))
        # Rewritten after the time of day has passed, the PO busy all along.
        self.idle = False
        due.tick(at(15, 41))
        self.write("- 15:40 — the test\n- 17:00 — chase\n", written=at(15, 50))
        self.idle = True
        out = due.tick(at(15, 51))
        self.assertEqual([o["line"] for o in out], ["- 15:40 — the test"])
        # And once delivered, the same line rewritten again is not a new item.
        self.write("- 15:40 — the test\n", written=at(16, 0))
        self.assertEqual(due.tick(at(16, 1)), [])
        self.assertEqual(due.tick(at(15, 41, day=19)), [])
        self.assertEqual(len(self.sess.typed), 1)

    def test_a_handover_written_a_moment_ago_is_read_at_the_next_look(self):
        self.write("- 15:40 — the test\n")
        due.tick(at(11, 25))
        self.hp.write_text("", encoding="utf-8")            # caught half-written
        os.utime(self.hp, (at(15, 41) - 1, at(15, 41) - 1))
        self.assertEqual(due.tick(at(15, 41)), [])
        self.assertIn("- 15:40 — the test", self.saved()["seen"][str(self.hp)])
        self.write("- 15:40 — the test\n", written=at(15, 41))
        self.assertEqual(len(due.tick(at(15, 42))), 1)

    def test_a_line_taken_out_and_written_again_with_its_date_is_not_delivered_twice(self):
        self.write("- 09-18 15:40 — the test\n")
        due.tick(at(15, 41))
        self.write("- 17:00 — other\n", written=at(15, 50))
        due.tick(at(15, 51))
        self.write("- 09-18 15:40 — the test\n", written=at(16, 0))
        self.assertEqual(due.tick(at(16, 1)), [])
        self.assertEqual(len(self.sess.typed), 1)

    def test_records_of_lines_long_gone_are_dropped(self):
        self.write("- 15:40 — the test\n")
        due.tick(at(15, 41))
        self.write("", written=at(16, 0))
        due.tick(at(16, 1))
        self.assertEqual(len(self.saved()["fired"]), 1)
        due.tick(at(16, 1, day=19))
        self.assertEqual(self.saved(), {"seen": {}, "fired": {}})

    def test_a_broken_state_file_is_started_over(self):
        (self.dir / "state").mkdir()
        (self.dir / "state" / "due.json").write_text("{not json", encoding="utf-8")
        self.write("- 15:40 — the test\n")
        self.assertEqual(len(due.tick(at(15, 41))), 1)
        # A lost record is all the hub knew: the item is delivered once more,
        # and the look does not fail.
        (self.dir / "state" / "due.json").write_text('["a list"]', encoding="utf-8")
        self.assertEqual(len(due.tick(at(15, 42))), 1)
        self.assertEqual(due.tick(at(15, 43)), [])

    def test_a_damaged_state_file_does_not_stop_the_look(self):
        self.write("- 15:40 — the test\n")
        f = self.dir / "state" / "due.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'{"seen": "\xff\xfe"}')                  # not UTF-8
        self.assertEqual(due.tick(at(15, 39)), [])
        f.write_text(json.dumps({"seen": {str(self.hp): "text", "x": 3}, "fired": {"k": None}}),
                     encoding="utf-8")
        self.assertEqual(len(due.tick(at(15, 41))), 1)
        self.assertEqual(due.tick(at(15, 42)), [])
        self.assertEqual(len(self.sess.typed), 1)

    def test_a_typed_item_is_recorded_before_anything_later_can_fail(self):
        # Two handovers due in one look; the second one's delivery blows up in a
        # place nothing catches. The first was typed: it is on disk already.
        self.write("- 15:40 — the test\n")
        other = self.dir / "task" / "TASK-HANDOVER.md"
        self.write("- 15:40 — the owner's item\n", path=other)
        self.owners = [("room-t", "claude")]
        p = mock.patch.object(rotation, "task_handover_path", return_value=other)
        p.start()
        self.addCleanup(p.stop)
        real = due._deliver
        calls = []

        def deliver(subj, ready, now):
            calls.append(subj)
            if len(calls) > 1:
                raise KeyboardInterrupt()
            return real(subj, ready, now)

        with mock.patch.object(due, "_deliver", side_effect=deliver):
            with self.assertRaises(KeyboardInterrupt):
                due.tick(at(15, 41))
        self.assertEqual(len(self.sess.typed), 1)
        self.assertEqual([r["how"] for r in self.saved()["fired"].values()], ["typed"])


class AgeTests(_World):
    def test_an_item_first_seen_more_than_a_day_late_is_never_delivered(self):
        # Written on the 16th for 15:40; the hub first reads it on the 18th.
        self.write("- 15:40 — the old test\n- 09-10 08:00 — older still\n", written=at(11, 20, day=16))
        out = due.tick(at(9, 0))
        self.assertEqual(sorted(o["how"] for o in out), ["expired", "expired"])
        self.assertEqual((self.sess.typed, self.notices), ([], []))
        self.assertEqual(due.tick(at(9, 1)), [])            # said once, in the log only

    def test_an_item_less_than_a_day_late_is_delivered(self):
        # The hub was down over the time: it comes back 3 h later.
        self.write("- 15:40 — the test\n")
        out = due.tick(at(18, 40))
        self.assertEqual([o["how"] for o in out], ["typed"])

    def test_a_po_busy_for_more_than_a_day_is_not_told_of_yesterday(self):
        self.write("- 15:40 — the test\n")
        self.idle = False
        due.tick(at(15, 41))
        self.idle = True
        self.assertEqual([o["how"] for o in due.tick(at(15, 45, day=19))], ["expired"])
        self.assertEqual(self.sess.typed, [])


class OwnerTests(_World):
    LINE = "- 15:40 — rerun the soak test"

    def owner_task(self, written=None) -> Path:
        task = self.dir / "task"
        self.room = {"id": "room-t", "taskDir": str(task), "participants": [
            {"identity": "claude", "kind": "agent", "agent": "claude", "role": "engineer",
             "ptyId": "pty-2", "sessionId": "sid-2"}]}
        self.owners = [("room-t", "claude")]
        self.write(self.LINE + "\n", written=written, path=task / "TASK-HANDOVER.md")
        return task / "TASK-HANDOVER.md"

    def test_a_pending_time_survives_a_rewrite_and_a_gap_without_the_owner(self):
        # Review 1: written at 11:20 for 15:40, the owner busy; asked for its
        # handover it rewrites the file at 15:41, and between its two sessions
        # a look finds no live owner. The line is still today's 15:40.
        hp = self.owner_task()
        due.tick(at(11, 25))
        self.idle = False
        due.tick(at(15, 40) + 30)
        self.write(self.LINE + "\n", written=at(15, 41), path=hp)
        self.owners = []
        self.assertEqual(due.tick(at(15, 42)), [])
        self.assertEqual(self.saved()["seen"][str(hp)][self.LINE]["due"], at(15, 40))
        self.owners, self.idle = [("room-t", "claude")], True
        self.assertEqual([o["how"] for o in due.tick(at(15, 44))], ["typed"])
        self.assertEqual(len(self.sess.typed), 1)

    def test_a_delivered_line_is_not_delivered_again_after_a_rewrite_and_a_gap(self):
        hp = self.owner_task()
        self.assertEqual(len(due.tick(at(15, 41))), 1)
        self.write(self.LINE + "\n", written=at(16, 0), path=hp)
        self.owners = []                                    # stopped, or between two sessions
        due.tick(at(16, 1))
        self.owners = [("room-t", "claude")]
        for when in (at(16, 5), at(15, 41, day=19), at(15, 41, day=20)):
            self.assertEqual(due.tick(when), [])
        self.assertEqual(len(self.sess.typed), 1)

    def test_a_handover_deleted_for_a_moment_keeps_its_times(self):
        hp = self.owner_task()
        due.tick(at(11, 25))
        hp.unlink()                                         # "replace it if it exists"
        self.assertEqual(due.tick(at(15, 41)), [])
        self.write(self.LINE + "\n", written=at(15, 41), path=hp)
        self.assertEqual(len(due.tick(at(15, 42))), 1)

    def test_a_deleted_handovers_lines_are_forgotten_a_day_after_their_time(self):
        hp = self.owner_task()
        due.tick(at(11, 25))
        hp.unlink()
        due.tick(at(15, 0, day=19))
        self.assertIn(str(hp), self.saved()["seen"])
        due.tick(at(15, 41, day=19))
        self.assertEqual(self.saved()["seen"], {})

    def test_a_running_owners_task_handover_is_read_too(self):
        self.owner_task()
        out = due.tick(at(15, 41))
        self.assertEqual([o["how"] for o in out], ["typed"])
        self.assertEqual(self.sess.typed, [
            "[due] 15:40 — rerun the soak test (from TASK-HANDOVER.md). This is due and you are "
            "idle: do it now, or report why not."])

    def test_a_stopped_task_gets_nothing_and_keeps_its_times(self):
        hp = self.owner_task()
        due.tick(at(11, 25))
        self.owners = []
        self.assertEqual(due.tick(at(15, 41)), [])
        self.assertEqual((self.sess.typed, self.notices), ([], []))
        self.assertIn(str(hp), self.saved()["seen"])
        # Started again: the line is still the same 15:40, and is due.
        self.owners = [("room-t", "claude")]
        self.assertEqual(len(due.tick(at(15, 45))), 1)


class SchedulerTests(unittest.TestCase):
    def test_it_looks_once_a_minute_and_not_at_once(self):
        with mock.patch.object(due, "tick") as tick, mock.patch.object(due, "_LAST", 0.0), \
                mock.patch.object(due.time, "time") as clock:
            clock.return_value = 1000.0
            due.maybe_tick()
            clock.return_value = 1030.0
            due.maybe_tick()
            self.assertEqual(tick.call_count, 0)
            clock.return_value = 1061.0
            due.maybe_tick()
            clock.return_value = 1075.0
            due.maybe_tick()
            self.assertEqual(tick.call_count, 1)

    def test_the_progress_check_loop_runs_it(self):
        import digest
        src = Path(digest.__file__).read_text(encoding="utf-8")
        self.assertIn("due.maybe_tick()", src[src.index("def start_scheduler"):])

    def test_no_subprocess_and_every_read_is_utf8(self):
        src = Path(due.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", src)
        self.assertEqual(src.count("read_text("), src.count('read_text(encoding="utf-8"'))


if __name__ == "__main__":
    unittest.main()
