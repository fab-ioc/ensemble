import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import attention
import provider_retry
import stall


FIXTURES = Path(__file__).parent / "fixtures"


class ProviderRetryTest(unittest.TestCase):
    def setUp(self):
        provider_retry._PATHS.clear()

    def test_codex_rollout_detects_refusal_and_clears_on_new_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            path.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            hit = provider_retry.detect(path, "codex")
            self.assertIn("server_overloaded", hit["error"])
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
            self.assertIsNone(provider_retry.detect(path, "codex"))
            self.assertFalse(provider_retry.resolved(path, "codex"))
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete",
                                                                    "error": None}}) + "\n")
            self.assertTrue(provider_retry.resolved(path, "codex"))

    def test_transient_codes_and_usage_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            for error in ({"message": "HTTP 503 Service Unavailable", "codex_error_info": "server_error"},
                          {"message": "Too many requests", "codex_error_info": "rate_limit"}):
                path.write_text(json.dumps({"type": "event_msg", "payload": {
                    "type": "task_complete", "error": error}}) + "\n", encoding="utf-8")
                self.assertIsNotNone(provider_retry.detect(path, "codex"))
            path.write_text(json.dumps({"type": "event_msg", "payload": {
                "type": "task_complete", "error": {"message": "Usage limit reached"}}}) + "\n", encoding="utf-8")
            self.assertIsNone(provider_retry.detect(path, "codex"))

    def test_claude_api_error_and_retry_schedule(self):
        path = FIXTURES / "provider_claude_overloaded.jsonl"
        self.assertIn("529", provider_retry.detect(path, "claude")["error"])
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "claude.jsonl"
            other.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True,
                                         "apiError": "server_error", "message": {"content": [
                                             {"type": "text", "text": "API Error: 503 Service Unavailable"}]}})
                             + "\n", encoding="utf-8")
            self.assertIsNotNone(provider_retry.detect(other, "claude"))
        rec = {"key": "error-1", "attempts": 0, "nextAt": 120}
        for count, next_at in ((1, 420), (2, 1020), (3, 0)):
            rec = provider_retry.schedule(rec, 120 if count == 1 else (420 if count == 2 else 1020))
            self.assertEqual((rec["attempts"], rec["nextAt"]), (count, next_at))
        self.assertEqual(provider_retry.schedule(rec, 1200)["attempts"], 3)

    def test_three_refusals_get_three_backed_off_retries(self):
        class Pty:
            def alive(self):
                return True
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            state = Path(tmp) / "retries.json"
            part = {"agent": "codex", "identity": "codex", "ptyId": "pty-test",
                    "sessionId": "session-test"}
            room = {"id": "room-test"}
            writes = []
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})), \
                 mock.patch.object(dashboard.rotation, "_idle", return_value=True), \
                 mock.patch.object(dashboard.rotation, "_submitted_lately", return_value=False), \
                 mock.patch.object(dashboard, "_type_input", side_effect=lambda s, line: writes.append(line) or True), \
                 mock.patch.object(dashboard, "room_po_id", return_value=None):
                at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
                now = at + 120
                for n, wait in enumerate((300, 600, 0, 0)):
                    with rollout.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"timestamp": f"2026-10-13T09:13:0{n}.000Z",
                                            "type": "event_msg", "payload": {"type": "task_complete",
                                            "error": {"codex_error_info": "server_overloaded"}}}) + "\n")
                    provider_retry.tick(room, part, Pty(), now)
                    if wait:
                        now += wait
                rec = json.loads(state.read_text(encoding="utf-8"))["room-test::codex"]
                rec["toldAt"], rec["wokeAt"] = now, now
                state.write_text(json.dumps({"room-test::codex": rec}), encoding="utf-8")
                with rollout.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"timestamp": "2026-10-13T09:13:04.000Z",
                                        "type": "event_msg", "payload": {"type": "task_complete",
                                        "error": {"codex_error_info": "server_overloaded"}}}) + "\n")
                with mock.patch.object(stall, "_type_po") as wake:
                    provider_retry.tick(room, {**part, "ptyId": "new-pty"}, Pty(), now + 1)
                self.assertFalse(wake.called, "the PO was woken again on the fifth refusal")
                continued = provider_retry.status({**part, "ptyId": "new-pty"}, room["id"], now + 1)
                self.assertEqual((continued["attempts"], continued["wokeAt"]), (3, now))
            self.assertEqual(writes, ["[provider retry] Carry on."] * 3)
            self.assertEqual(rec["attempts"], 3)
            self.assertEqual(rec["nextAt"], 0)

    def test_old_refusal_stays_blocked_without_retrying(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            state = Path(tmp) / "retries.json"
            part = {"agent": "codex", "identity": "codex", "ptyId": "pty-old",
                    "sessionId": "session-old"}
            at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
            now = at + provider_retry.MAX_AGE_S + 1
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})), \
                 mock.patch.object(dashboard.rotation, "_idle", return_value=True), \
                 mock.patch.object(dashboard, "_type_input") as typed:
                rec = provider_retry.status(part, "room-old", now)
                self.assertTrue(rec["stale"])
                self.assertIn("overloaded", provider_retry.reason(rec))
                provider_retry.tick({"id": "room-old"}, part, mock.Mock(alive=lambda: True), now)
                typed.assert_not_called()
            self.assertFalse(state.exists())

    def test_paused_and_done_tasks_are_not_retried(self):
        for status, workflow in (("paused", "inprogress"), ("waiting_human", "inprogress"),
                                 ("running", "done")):
            room = {"id": "room-quiet", "launched": True, "status": status}
            with self.subTest(status=status, workflow=workflow), \
                 mock.patch.object(dashboard.chatroom, "get_room", return_value=room), \
                 mock.patch.object(dashboard, "_room_is_live", return_value=True), \
                 mock.patch.object(dashboard, "workflow_of", return_value=workflow), \
                 mock.patch.object(provider_retry, "tick") as retried:
                stall._look_provider("room-quiet", 1000)
                retried.assert_not_called()

    def test_deleted_rooms_and_old_pty_keys_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "retries.json"
            state.write_text(json.dumps({"room-gone::codex": {"attempts": 1},
                                         "room-kept::codex": {"attempts": 2},
                                         "pty-old": {"attempts": 3}}), encoding="utf-8")
            with mock.patch.object(provider_retry, "_file", return_value=state):
                provider_retry.prune_rooms({"room-kept"})
            self.assertEqual(list(json.loads(state.read_text(encoding="utf-8"))),
                             ["room-kept::codex"])

    def test_submit_check_recovers_swallowed_enter(self):
        class Pty:
            meta = {"agent": "codex"}
            def __init__(self):
                self.writes = []
                self.submitted = False
            def alive(self):
                return True
            def write(self, data):
                self.writes.append(data)
                if data == "\r" and self.writes.count("\r") == 2:
                    self.submitted = True
                return True
            def tail(self):
                return "› carry on" if not self.submitted else "› "
        sess = Pty()
        with mock.patch.object(dashboard.time, "sleep", return_value=None), \
             mock.patch.object(dashboard, "_record_typed_input_safely"):
            self.assertTrue(dashboard._type_input(sess, "carry on"))
        self.assertTrue(sess.submitted)
        self.assertEqual(sess.writes, ["carry on", "\r", "\r"])

    def test_submit_check_trusts_new_rollout_turn_over_stale_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({"type": "event_msg", "payload": {
                "type": "task_started"}}) + "\n", encoding="utf-8")
            class Pty:
                def tail(self):
                    return "› [Pasted Content 400 chars]"
            writes = []
            with mock.patch.object(dashboard.time, "sleep", return_value=None):
                dashboard._verify_codex_submit(Pty(), writes.append, "carry on", rollout, 0)
            self.assertEqual(writes, [])

    def test_attention_is_blocked_with_retry_time(self):
        rec = {"key": "error", "attempts": 1, "nextAt": 1791879000,
               "error": "server_overloaded"}
        ev = {"alive": True, "scan": {"block": None}, "hook": None,
              "claudeStatus": "", "limit": None}
        with mock.patch.object(provider_retry, "status", return_value=rec), \
             mock.patch.object(dashboard, "workflow_of", return_value="inprogress"):
            state, reason, extra = attention._classify_agent(
                {"owners": ["codex"]}, {"identity": "codex", "agent": "codex"},
                ev, 600, 1791878500)
        self.assertEqual(state, "blocked")
        self.assertIn("the model provider was overloaded, retrying at", reason)
        self.assertEqual(extra["cause"], "provider_overload")

    def test_attention_does_not_promise_retry_for_paused_task(self):
        rec = {"key": "error", "attempts": 0, "nextAt": 1791879000,
               "error": "server_overloaded"}
        ev = {"alive": True, "scan": {"block": None}, "hook": None,
              "claudeStatus": "", "limit": None}
        with mock.patch.object(provider_retry, "status", return_value=rec), \
             mock.patch.object(dashboard, "workflow_of", return_value="inprogress"):
            state, reason, extra = attention._classify_agent(
                {"status": "paused"}, {"identity": "codex", "agent": "codex"},
                ev, 600, 1791878500)
        self.assertEqual(state, "blocked")
        self.assertIn("automatic retry is paused", reason)
        self.assertEqual(extra["retryAt"], 0)

    def test_rollout_path_and_miss_are_cached(self):
        at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            part = {"agent": "codex", "sessionId": "cached-sid"}
            with mock.patch.object(dashboard.rotation, "_transcript_of", side_effect=[
                    (None, dashboard.rotation.read_rollout),
                    (rollout, dashboard.rotation.read_rollout)]) as lookup:
                self.assertIsNone(provider_retry.status(part, "room-cache", at + 120))
                self.assertIsNone(provider_retry.status(part, "room-cache", at + 120))
                self.assertEqual(lookup.call_count, 1, "a miss must not glob every poll")
                provider_retry._PATHS["cached-sid"] = (None, 0)  # expired TTL
                self.assertIsNotNone(provider_retry.status(part, "room-cache", at + 120))
                self.assertIsNotNone(provider_retry.status(part, "room-cache", at + 120))
                self.assertEqual(lookup.call_count, 2, "the found path is reused")

    def test_recent_submission_or_unsent_editor_blocks_retry(self):
        at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
        class Pty:
            def __init__(self):
                self.editor = ""
            def alive(self):
                return True
            def tail(self):
                return self.editor
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            state = Path(tmp) / "retries.json"
            part = {"agent": "codex", "identity": "codex", "sessionId": "guard-sid"}
            room = {"id": "room-guard"}
            sess = Pty()
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})), \
                 mock.patch.object(dashboard.rotation, "_idle", return_value=True), \
                 mock.patch.object(dashboard.rotation, "_submitted_lately", side_effect=[True, False, False, False]), \
                 mock.patch.object(dashboard, "_type_input", return_value=True) as typed:
                provider_retry.tick(room, part, sess, at + 120)
                sess.editor = "› [Pasted Content 37 chars]"
                provider_retry.tick(room, part, sess, at + 120)
                sess.editor = ""
                sess.person_line_text = "draft still in the editor"
                provider_retry.tick(room, part, sess, at + 120)
                typed.assert_not_called()
                sess.person_line_text = ""
                sess.editor = "› "
                provider_retry.tick(room, part, sess, at + 120)
                typed.assert_called_once()
                self.assertEqual(provider_retry._load()["room-guard::codex"]["attempts"], 1)

    def test_retry_and_po_wake_type_without_global_locks(self):
        def locks_free():
            got = []
            def probe():
                for lock in (dashboard.rotation.GATE, provider_retry._LOCK):
                    acquired = lock.acquire(timeout=0.2)
                    got.append(acquired)
                    if acquired:
                        lock.release()
            worker = threading.Thread(target=probe)
            worker.start()
            worker.join(timeout=1)
            self.assertEqual(got, [True, True])
            return True
        at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            state = Path(tmp) / "retries.json"
            room = {"id": "room-lock", "status": "running", "launched": True}
            part = {"agent": "codex", "identity": "codex", "sessionId": "lock-sid"}
            sess = mock.Mock(alive=lambda: True, tail=lambda: "› ")
            sess.person_line_text = ""
            sess.hub_line_typed = False
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.chatroom, "get_room", return_value=room), \
                 mock.patch.object(dashboard.chatroom, "agent_participants", return_value=[part]), \
                 mock.patch.object(dashboard.chatroom, "po_identity", return_value="po"), \
                 mock.patch.object(dashboard.chatroom, "participant", return_value=part), \
                 mock.patch.object(dashboard, "room_po_id", return_value="po-room"), \
                 mock.patch.object(dashboard, "workflow_of", return_value="inprogress"), \
                 mock.patch.object(dashboard, "_room_is_live", return_value=True), \
                 mock.patch.object(dashboard.rotation, "is_rotating", return_value=False), \
                 mock.patch.object(dashboard.rotation, "awaiting_handover", return_value=False), \
                 mock.patch.object(dashboard.rotation, "_pty", return_value=sess), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})), \
                 mock.patch.object(dashboard.rotation, "_idle", return_value=True), \
                 mock.patch.object(dashboard.rotation, "_submitted_lately", return_value=False), \
                 mock.patch.object(stall, "limit_hit", return_value=None), \
                 mock.patch.object(dashboard, "_type_input", side_effect=lambda *_: locks_free()) as typed:
                stall._look_provider(room["id"], at + 120)
                typed.assert_called_once()
                rec = provider_retry._load()["room-lock::codex"]
                rec.update(attempts=3, nextAt=0, toldAt=at + 120, wokeAt=0)
                provider_retry._save({"room-lock::codex": rec})
                provider_retry.tick(room, part, sess, at + 121)
                self.assertEqual(typed.call_count, 2, "the PO wake must also type without either lock")

    def test_retry_record_is_not_rewritten_on_unchanged_tick(self):
        at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_bytes((FIXTURES / "provider_codex_overloaded.jsonl").read_bytes())
            state = Path(tmp) / "retries.json"
            part = {"agent": "codex", "identity": "codex", "sessionId": "quiet-sid"}
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})):
                provider_retry.tick({"id": "room-quiet"}, part, None, at + 1)
                with mock.patch.object(provider_retry, "_save") as save:
                    provider_retry.tick({"id": "room-quiet"}, part, None, at + 2)
                    save.assert_not_called()

    def test_stall_survives_prune_error_and_failed_room_glob(self):
        room_path = Path("room-kept.json")
        with mock.patch.object(stall, "_load", return_value={}), \
             mock.patch.object(dashboard.chatroom, "ROOMS_DIR") as rooms, \
             mock.patch.object(provider_retry, "prune_rooms", side_effect=PermissionError("locked")) as prune, \
             mock.patch.object(stall, "_look_provider", return_value="") as looked, \
             mock.patch.object(stall, "_look", return_value=""), \
             mock.patch.object(stall, "_look_limit", return_value=""), \
             mock.patch.object(stall, "_look_silent", return_value=""), \
             mock.patch.object(stall, "_look_permission", return_value=""):
            rooms.glob.return_value = [room_path]
            stall.tick(1000)
            looked.assert_called_once_with("room-kept", 1000)
            rooms.glob.side_effect = PermissionError("no rooms")
            looked.reset_mock()
            self.assertEqual(stall.tick(1001), {})
            looked.assert_not_called()
            self.assertEqual(prune.call_count, 1, "a failed glob must not prune with an empty set")

    def test_codex_submit_probe_survives_rollout_lookup_error(self):
        sess = mock.Mock(meta={"room": "room-test", "identity": "codex"})
        with mock.patch.object(dashboard.chatroom, "get_room", return_value={"id": "room-test"}), \
             mock.patch.object(dashboard.chatroom, "participant", return_value={"agent": "codex"}), \
             mock.patch.object(dashboard.rotation, "_transcript_of", side_effect=PermissionError("locked")):
            self.assertEqual(dashboard._codex_rollout_start(sess), (None, -1))
