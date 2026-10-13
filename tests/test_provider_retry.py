import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import attention
import provider_retry


FIXTURES = Path(__file__).parent / "fixtures"


class ProviderRetryTest(unittest.TestCase):
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
            part = {"agent": "codex", "ptyId": "pty-test"}
            writes = []
            with mock.patch.object(provider_retry, "_file", return_value=state), \
                 mock.patch.object(dashboard.rotation, "_transcript_of", return_value=(rollout, lambda _: {})), \
                 mock.patch.object(dashboard.rotation, "_idle", return_value=True), \
                 mock.patch.object(dashboard, "_type_input", side_effect=lambda s, line: writes.append(line) or True), \
                 mock.patch.object(dashboard, "room_po_id", return_value=None):
                at = provider_retry._d.model_limit._ts("2026-10-13T09:13:00.000Z")
                now = at + 120
                for n, wait in enumerate((300, 600, 0, 0)):
                    with rollout.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"timestamp": f"2026-10-13T09:13:0{n}.000Z",
                                            "type": "event_msg", "payload": {"type": "task_complete",
                                            "error": {"codex_error_info": "server_overloaded"}}}) + "\n")
                    provider_retry.tick({}, part, Pty(), now)
                    if wait:
                        now += wait
                rec = json.loads(state.read_text(encoding="utf-8"))["pty-test"]
            self.assertEqual(writes, ["[provider retry] Carry on."] * 3)
            self.assertEqual(rec["attempts"], 3)
            self.assertEqual(rec["nextAt"], 0)

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
        with mock.patch.object(provider_retry, "status", return_value=rec):
            state, reason, extra = attention._classify_agent(
                {"owners": ["codex"]}, {"identity": "codex", "agent": "codex"},
                ev, 600, 1791878500)
        self.assertEqual(state, "blocked")
        self.assertIn("the model provider was overloaded, retrying at", reason)
        self.assertEqual(extra["cause"], "provider_overload")
