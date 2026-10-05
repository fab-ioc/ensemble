"""Persistent attribution for text supplied to an agent by the hub/tools."""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
import input_provenance
import points


REPORT = ("[report] completed from task 'Seven' (#7, codex): done — "
          "read it with ensemble_get_task taskId=#7 messages=0.")


def stamp(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.records = Path(self.tmp.name) / "input-provenance"
        self.patch = mock.patch.object(input_provenance, "RECORDS_DIR", self.records)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        input_provenance._CACHE.clear()

    def test_persists_hash_only_and_matches_the_right_seat_and_time(self):
        row = input_provenance.record(
            "room-one", "codex", "  hub words\r\n", {"kind": "hub", "senderLabel": "Hub"},
            session_id="sid-1", at=1000)
        self.assertIsNotNone(row)
        saved = json.loads(input_provenance.path_for("room-one").read_text(encoding="utf-8"))
        self.assertNotIn("text", saved)
        self.assertEqual(saved["hash"], input_provenance.text_hash("hub words\n"))
        indexed = input_provenance.index("room-one")
        self.assertEqual(input_provenance.match(indexed, "hub words", at=1001,
                                                identity="codex", session_id="sid-1")["kind"], "hub")
        self.assertIsNone(input_provenance.match(indexed, "hub words", at=1001, identity="claude"))
        self.assertIsNone(input_provenance.match(
            indexed, "hub words", at=1000 + input_provenance.MATCH_SLACK_S + 1,
            identity="codex", session_id="sid-1"))

    def test_bracketed_paste_and_plain_text_have_one_hash(self):
        pasted = "\x1b[200~first\nsecond\x1b[201~"
        self.assertEqual(input_provenance.normalize(pasted), "first\nsecond")
        self.assertEqual(input_provenance.text_hash(pasted),
                         input_provenance.text_hash("first\nsecond"))

    def test_one_record_never_claims_one_of_two_identical_turns(self):
        input_provenance.record(
            "room-one", "codex", "same words", {"kind": "hub"},
            session_id="sid-1", at=100)
        got = input_provenance.assign(
            input_provenance.index("room-one"),
            [(0, "same words", 90), (1, "same words", 101)],
            identity="codex", session_id="sid-1")
        self.assertEqual(got, {})

    def test_repeated_records_follow_submission_order_not_racing_timestamps(self):
        input_provenance.record(
            "room-one", "codex", "same words", {"kind": "hub"},
            session_id="sid-1", at=100)
        input_provenance.record(
            "room-one", "codex", "same words", {"kind": "human"},
            session_id="sid-1", at=101)
        got = input_provenance.assign(
            input_provenance.index("room-one"),
            # The human timestamp is closer to the first (Hub) record: nearest
            # timestamp matching would invert the authors.
            [(0, "same words", 99), (1, "same words", 100)],
            identity="codex", session_id="sid-1")
        self.assertEqual([got[i]["kind"] for i in (0, 1)], ["hub", "human"])

    def test_ordered_human_record_protects_edited_text_when_the_hash_differs(self):
        typed_keys = "[digest] genuie\x1b[Dn"
        final_text = "[digest] genuine"
        input_provenance.record(
            "room-one", "codex", typed_keys, {"kind": "human"},
            session_id="sid-1", at=100)
        got = input_provenance.assign(
            input_provenance.index("room-one"), [(0, final_text, 101)],
            identity="codex", session_id="sid-1")
        self.assertEqual(got[0]["kind"], "human")
        self.assertNotEqual(got[0]["hash"], input_provenance.text_hash(final_text))

    def test_an_edited_human_record_does_not_guess_among_two_turns(self):
        input_provenance.record(
            "room-one", "codex", "keys with cursor input", {"kind": "human"},
            session_id="sid-1", at=100)
        got = input_provenance.assign(
            input_provenance.index("room-one"),
            [(0, "[digest] first", 100), (1, "[digest] second", 101)],
            identity="codex", session_id="sid-1")
        self.assertEqual(got, {})

    def test_edited_human_and_identical_hub_turn_follow_physical_order(self):
        final_text = "[digest] genuine"
        input_provenance.record(
            "room-one", "codex", "[digest] genuie\x1b[Dn", {"kind": "human"},
            session_id="sid-1", at=100)
        input_provenance.record(
            "room-one", "codex", final_text, {"kind": "hub"},
            session_id="sid-1", at=101)
        got = input_provenance.assign(
            input_provenance.index("room-one"),
            [(0, final_text, 100), (1, final_text, 101)],
            identity="codex", session_id="sid-1")
        self.assertEqual([got[i]["kind"] for i in (0, 1)], ["human", "hub"])


class InventoryFixtures(unittest.TestCase):
    """Every inventory kind is a represented sender, never the CEO."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        patches = [
            mock.patch.object(input_provenance, "RECORDS_DIR", base / "input-provenance"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", base),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(points, "_log"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        input_provenance._CACHE.clear()
        for cache in (points._CACHE, points._SYNCED, points._SCANNED, points._TEXTS):
            cache.clear()
        for seen in (points._ADOPT_SEEN, points._TOLD, points._GONE):
            seen.clear()
        self.base = time.time() - 120

    def fixtures(self):
        rows = [
            ("brief", "A task brief without a prefix", {"kind": "brief"}, "Hub"),
            ("reviewbrief", "Review commit abc123", {"kind": "reviewbrief"}, "Hub"),
            ("hub", "An unprefixed helper wake", {"kind": "hub"}, "Hub"),
            ("report", REPORT, None, "#7 codex"),
            ("pomsg", "[from the Dock PO] bug: splitter jumps", None, "Dock PO"),
        ]
        seen = {kind for kind, *_ in rows}
        for prefix, kind in dashboard.HUB_INPUT_KINDS:
            if kind in seen:
                continue
            tail = "sam] helper" if kind == "helper" else "fixture"
            text = prefix + tail
            label = "PO" if kind == "po" else "Hub"
            rows.append((kind, text, None, label))
        return rows

    def classified(self, room_id: str, sid: str, identity: str, include_human: bool):
        raw = []
        for i, (kind, text, forced, _label) in enumerate(self.fixtures()):
            at = self.base + i
            info = dashboard._input_sender_info(text, forced)
            input_provenance.record(room_id, identity, text, info, session_id=sid, at=at)
            raw.append({"role": "user", "text": text, "timestamp": stamp(at)})
        if include_human:
            at = self.base + 100
            text = "Please change the real setting."
            input_provenance.record(
                room_id, identity, text,
                dashboard._input_sender_info(text, {"kind": "human"}),
                session_id=sid, at=at)
            raw.append({"role": "user", "text": text, "timestamp": stamp(at)})
        return raw, dashboard.classify_turns(raw, room_id=room_id, identity=identity,
                                              session_id=sid)

    def test_every_kind_uses_its_recorded_sender_and_a_real_ceo_turn_stays_human(self):
        raw, turns = self.classified("room-fixture", "sid-fixture", "claude", True)
        expected = self.fixtures()
        self.assertEqual(len(turns), len(expected) + 1)
        for turn, (kind, _text, _forced, label) in zip(turns, expected):
            with self.subTest(kind=kind):
                self.assertEqual(turn["kind"], kind)
                self.assertEqual(turn["senderLabel"], label)
                self.assertEqual(turn["provenance"], "record")
                self.assertNotEqual(turn["kind"], "human")
        self.assertEqual(turns[-1]["kind"], "human")
        self.assertNotIn("senderLabel", turns[-1])

    def test_identical_ceo_text_is_not_borrowed_from_the_hub_turn(self):
        at = self.base
        text = "The exact same sentence"
        input_provenance.record(
            "room-copy", "claude", text,
            dashboard._input_sender_info(text, {"kind": "hub"}),
            session_id="sid-copy", at=at)
        input_provenance.record(
            "room-copy", "claude", text,
            dashboard._input_sender_info(text, {"kind": "human"}),
            session_id="sid-copy", at=at + 1)
        turns = dashboard.classify_turns([
            {"role": "user", "text": text, "timestamp": stamp(at)},
            {"role": "assistant", "text": "ok", "timestamp": stamp(at + 1)},
            {"role": "user", "text": text, "timestamp": stamp(at + 2)},
        ], room_id="room-copy", identity="claude", session_id="sid-copy")
        self.assertEqual(turns[0]["kind"], "hub")
        self.assertEqual(turns[2]["kind"], "human")

    def test_composed_resume_keeps_po_and_human_fragments_distinct(self):
        rid, sid, identity = "room-compose", "sid-compose", "claude"
        at = self.base
        note = dashboard.RESUME_NOTE
        po = "[from the PO] Use the safe migration."
        human = "I also want the title shortened."
        parts = [
            (note, dashboard._input_sender_info(note)),
            (po, dashboard._input_sender_info(po, {"kind": "po"})),
            (human, dashboard._input_sender_info(human, {"kind": "human"})),
        ]
        whole = "\n\n".join(text for text, _info in parts)
        input_provenance.record(
            rid, identity, whole, parts[0][1], session_id=sid, at=at, parts=parts)
        turns = dashboard.classify_turns(
            [{"role": "user", "text": whole, "timestamp": stamp(at)}],
            room_id=rid, identity=identity, session_id=sid)
        self.assertEqual([t["kind"] for t in turns], ["resumed", "po", "human"])
        self.assertEqual([t.get("senderLabel") for t in turns], ["Hub", "PO", None])
        self.assertTrue(all(t.get("provenance") == "record" for t in turns))

    def test_composed_resume_keeps_cross_project_po_sender(self):
        rid, sid, identity = "room-pomsg", "sid-pomsg", "claude"
        at = self.base
        note = dashboard.RESUME_NOTE
        pomsg = "[from the Dock PO] question: Which branch?"
        parts = [(note, dashboard._input_sender_info(note)),
                 (pomsg, dashboard._input_sender_info(pomsg, {"kind": "pomsg"}))]
        whole = "\n\n".join(text for text, _info in parts)
        input_provenance.record(
            rid, identity, whole, parts[0][1], session_id=sid, at=at, parts=parts)
        turns = dashboard.classify_turns(
            [{"role": "user", "text": whole, "timestamp": stamp(at)}],
            room_id=rid, identity=identity, session_id=sid)
        self.assertEqual([(t["kind"], t.get("senderLabel")) for t in turns],
                         [("resumed", "Hub"), ("pomsg", "Dock PO")])

    def test_legacy_po_fallback_has_represented_sender(self):
        turns = dashboard.classify_turns([
            {"role": "user", "text": "[from the PO] carry on"},
            {"role": "user", "text": "[from the Dock PO] info: shipped"},
        ])
        self.assertEqual([(t["kind"], t.get("senderLabel")) for t in turns],
                         [("po", "PO"), ("pomsg", "Dock PO")])
        self.assertEqual(turns[1]["fromProjectName"], "Dock")

    def test_recorded_and_legacy_po_replies_retain_answer_metadata(self):
        rid, sid, identity = "room-po-answer", "sid-po-answer", "claude"
        text = "[from the PO] carry on"
        input_provenance.record(
            rid, identity, text, dashboard._input_sender_info(text, {"kind": "po"}),
            session_id=sid, at=self.base)
        recorded = dashboard.classify_turns([
            {"role": "user", "text": text, "timestamp": stamp(self.base)},
            {"role": "assistant", "text": "Continuing."},
        ], room_id=rid, identity=identity, session_id=sid)
        legacy = dashboard.classify_turns([
            {"role": "user", "text": text},
            {"role": "assistant", "text": "Continuing."},
        ])
        for turns in (recorded, legacy):
            self.assertEqual((turns[1]["answers"]["kind"],
                              turns[1]["answers"]["senderLabel"]), ("po", "PO"))

    def test_only_the_genuine_ceo_turn_becomes_a_point(self):
        rid = chatroom.create_room(
            "Solo", [{"identity": "claude", "agent": "claude", "role": "Product owner"}])["id"]
        room = chatroom.get_room(rid, public=False)
        room["mode"] = "solo"
        room["participants"][0].update(sessionId="sid-points", ptyId="pty-1")
        chatroom.update_room(room)
        raw, turns = self.classified(rid, "sid-points", "claude", True)
        with mock.patch.object(dashboard, "read_session_turns", return_value=turns), \
                mock.patch.object(points, "_session_stat", return_value=[len(raw), self.base + 500]):
            led = points.sync(rid, force=True)
        self.assertEqual([(p["text"], p["state"]) for p in led["points"]],
                         [("Please change the real setting.", "open")])

        rid2 = chatroom.create_room(
            "Only hub", [{"identity": "claude", "agent": "claude", "role": "Product owner"}])["id"]
        room2 = chatroom.get_room(rid2, public=False)
        room2["mode"] = "solo"
        room2["participants"][0].update(sessionId="sid-hub-only", ptyId="pty-2")
        chatroom.update_room(room2)
        _raw2, turns2 = self.classified(rid2, "sid-hub-only", "claude", False)
        with mock.patch.object(dashboard, "read_session_turns", return_value=turns2), \
                mock.patch.object(points, "_session_stat", return_value=[len(turns2), self.base + 500]):
            self.assertEqual(points.sync(rid2, force=True)["points"], [])

    def test_failed_legacy_wake_does_not_leave_a_phantom_record(self):
        rid = chatroom.create_room(
            "Visible", [{"identity": "claude", "agent": "claude", "role": "engineer"}])["id"]
        chatroom.patch_participant(rid, "claude", {"pid": 42, "sessionId": "sid-visible"})
        handler = dashboard.Handler.__new__(dashboard.Handler)
        with mock.patch.object(handler, "_resolve_live_pid", return_value=42), \
                mock.patch.object(dashboard.BACKEND, "send_text", return_value="attach_failed:5"):
            self.assertEqual(handler._ring(rid, ["claude"], "[digest] check"), [])
        self.assertEqual(input_provenance.records(rid), [])

    def test_failed_legacy_brief_does_not_leave_a_phantom_record(self):
        rid = chatroom.create_room("Visible team", [
            {"identity": "claude", "agent": "claude", "role": "engineer", "pid": 42},
            {"identity": "codex", "agent": "codex", "role": "reviewer", "pid": 43},
        ])["id"]
        with mock.patch.object(dashboard.BACKEND, "send_text", return_value="not_alive") as sent:
            dashboard.Handler.__new__(dashboard.Handler)._brief_agents(rid)
        self.assertEqual(sent.call_count, 2)
        self.assertEqual(input_provenance.records(rid), [])


class LaunchKinds(unittest.TestCase):
    def test_retired_session_resolves_to_its_room_and_identity(self):
        room = {"id": "room-old", "participants": [], "retiredSessions": [
            {"sessionId": "sid-old", "identity": "codex", "agent": "codex"}]}
        with mock.patch.object(chatroom, "list_rooms", return_value=[room]):
            self.assertEqual(dashboard._session_input_context("sid-old"),
                             ("room-old", "codex"))

    def test_type_input_records_only_after_a_successful_submit(self):
        class Session:
            meta = {"room": "room-one", "identity": "claude", "sessionId": "sid-one"}

            def __init__(self, alive=True):
                self._alive = alive
                self.sent = []

            def send_line(self, text):
                self.sent.append(text)
                return True

            def alive(self):
                return self._alive

        sess = Session()
        with mock.patch.object(dashboard, "_record_typed_input") as record:
            self.assertTrue(dashboard._type_input(sess, "first\nsecond", {"kind": "digest"}))
        record.assert_called_once_with(sess, "first\nsecond", {"kind": "digest"})
        self.assertEqual(sess.sent, ["\x1b[200~first\nsecond\x1b[201~"])

        dead = Session(alive=False)
        with mock.patch.object(dashboard, "_record_typed_input") as record:
            self.assertFalse(dashboard._type_input(dead, "not delivered"))
        record.assert_not_called()

        human = Session()
        with mock.patch.object(dashboard, "_record_typed_input") as record:
            self.assertTrue(dashboard._type_input(human, "A genuine CEO message", record=False))
        record.assert_not_called()

    def test_rotation_reviewer_and_ordinary_briefs_are_distinct(self):
        reviewer = {"identity": "codex-2", "agent": "codex", "kind": "agent", "role": "reviewer",
                    "runs": "on mention"}
        owner = {"identity": "codex", "agent": "codex", "kind": "agent", "role": "engineer"}
        room = {"participants": [owner, reviewer]}
        self.assertEqual(dashboard._launch_input_kind(room, reviewer, "[rotation] quoted in spec", "review"),
                         "reviewbrief")
        self.assertEqual(dashboard._launch_input_kind(room, owner, "[board] now\n\n[rotation] take over", "fresh"),
                         "rotation")
        self.assertEqual(dashboard._launch_input_kind(room, owner, "ordinary first prompt", None), "brief")


if __name__ == "__main__":
    unittest.main()
