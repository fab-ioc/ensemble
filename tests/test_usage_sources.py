"""Claude's plan-window reading: the local statusline file first, the usage
endpoint as a paced fallback that remembers its last good answer."""
from __future__ import annotations

import email.message
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import dashboard
import usage
import usage_statusline

NOW = 1_789_280_000.0          # 2026-09-13T06:13:20Z
REPO = Path(__file__).resolve().parent.parent


def _statusline_file(folder: Path, *, age: float, five=40, seven=35,
                     five_reset=NOW + 3 * 3600, seven_reset=NOW + 3 * 86400) -> Path:
    path = folder / "claude-rate-limits.json"
    limits = {}
    if five is not None:
        limits["five_hour"] = {"used_percentage": five, "resets_at": five_reset}
    if seven is not None:
        limits["seven_day"] = {"used_percentage": seven, "resets_at": seven_reset}
    path.write_text(json.dumps({"asOf": NOW - age, "rateLimits": limits}), encoding="utf-8")
    return path


def _endpoint_payload(five=12.0, seven=50.0) -> bytes:
    return json.dumps({"limits": [
        {"kind": "session", "percent": five,
         "resets_at": "2026-09-13T09:59:59.880051+00:00"},
        {"kind": "weekly_all", "percent": seven,
         "resets_at": "2026-09-16T11:59:59.880080+00:00"},
    ]}).encode("utf-8")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _refusal(retry_after: str | None = None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(usage.USAGE_URL, 429, "Too Many Requests", headers, None)


def _snapshot(claude: dict) -> dict:
    return {"state": "ready", "warnPercent": 80, "alarmPercent": 95,
            "sources": [claude, usage._unavailable("codex", "none")]}


class ClaudeReadingTests(unittest.TestCase):
    def setUp(self):
        usage._reset_claude_state()
        self.addCleanup(usage._reset_claude_state)
        self.tmp = Path(tempfile.mkdtemp())
        self.missing = self.tmp / "absent.json"
        token = mock.patch.object(usage, "_access_token", return_value="token-for-tests")
        token.start()
        self.addCleanup(token.stop)
        self.calls = []
        self.answers = []

        def urlopen(req, timeout=None):
            self.calls.append(req.full_url)
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return _Response(answer)

        net = mock.patch.object(usage.urllib.request, "urlopen", side_effect=urlopen)
        net.start()
        self.addCleanup(net.stop)

    # --- local source -----------------------------------------------------

    def test_fresh_local_reading_is_used_and_the_endpoint_is_not_called(self):
        path = _statusline_file(self.tmp, age=120)
        src = usage.read_claude(NOW, path)
        self.assertEqual(src["state"], "ok")
        self.assertEqual(src["via"], "statusline")
        self.assertTrue(src["trusted"])
        self.assertEqual(src["ageSeconds"], 120)
        self.assertEqual({w["kind"]: w["percent"] for w in src["windows"]},
                         {"five_hour": 40.0, "seven_day": 35.0})
        self.assertEqual(self.calls, [])
        self.assertEqual(dashboard._kind_usage(_snapshot(src), "claude"),
                         {"state": "known", "percent": 40.0, "window": "five_hour",
                          "label": "5-hour", "trusted": True, "atLeast": False})

    def test_stale_local_reading_stays_known_as_a_floor_when_the_endpoint_refuses(self):
        path = _statusline_file(self.tmp, age=2 * 3600, five=85)
        self.answers = [_refusal("900")]
        src = usage.read_claude(NOW, path)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(src["via"], "statusline")
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        seven = next(w for w in src["windows"] if w["kind"] == "seven_day")
        self.assertFalse(five["trusted"])          # 2 h against a 5 h window
        self.assertEqual(five["percent"], 85.0)    # kept, as a floor
        self.assertTrue(seven["trusted"])          # 2 h against a week
        self.assertIn("rate-limiting", src["note"])
        figure = dashboard._kind_usage(_snapshot(src), "claude")
        self.assertEqual((figure["state"], figure["percent"], figure["atLeast"]),
                         ("known", 85.0, True))

    def test_local_window_past_its_reset_has_no_current_percent(self):
        path = _statusline_file(self.tmp, age=60, five_reset=NOW - 10)
        src = usage.read_claude_statusline(NOW, path)
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        self.assertTrue(five["rolledOver"])
        self.assertIsNone(five["percent"])
        self.assertEqual(five["stalePercent"], 40.0)

    def test_rolled_over_local_window_asks_the_endpoint_for_the_new_one(self):
        path = _statusline_file(self.tmp, age=60, five_reset=NOW - 10)
        self.answers = [_endpoint_payload(five=3.0)]
        src = usage.read_claude(NOW, path)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(src["via"], "endpoint")
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        self.assertEqual(five["percent"], 3.0)

    def test_local_reading_with_an_implausible_reset_fails_loudly(self):
        path = _statusline_file(self.tmp, age=60, five_reset=18000)   # a duration
        src = usage.read_claude_statusline(NOW, path)
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("format", src["error"])

    def test_missing_local_reading_falls_back_to_the_endpoint(self):
        self.answers = [_endpoint_payload()]
        src = usage.read_claude(NOW, self.missing)
        self.assertEqual(src["via"], "endpoint")
        self.assertTrue(src["trusted"])
        self.assertEqual(src["ageSeconds"], 0)
        self.assertEqual({w["kind"]: w["percent"] for w in src["windows"]},
                         {"five_hour": 12.0, "seven_day": 50.0})

    # --- endpoint fallback ------------------------------------------------

    def test_refused_endpoint_serves_its_recent_good_reading_flagged_by_age(self):
        self.answers = [_endpoint_payload(five=12.0), _refusal()]
        usage.read_claude(NOW, self.missing)
        later = NOW + 40 * 60
        src = usage.read_claude(later, self.missing)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(src["state"], "ok")
        self.assertEqual(src["via"], "endpoint-cached")
        self.assertEqual(src["ageSeconds"], 40 * 60)
        self.assertIn("rate-limiting", src["note"])
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        self.assertFalse(five["trusted"])          # 40 min > a tenth of 5 h
        self.assertEqual(five["percent"], 12.0)
        # Still known: the worst window is the weekly one, exact at 40 minutes.
        figure = dashboard._kind_usage(_snapshot(src), "claude")
        self.assertEqual((figure["state"], figure["percent"], figure["atLeast"]),
                         ("known", 50.0, False))

    def test_endpoint_is_asked_at_most_every_interval(self):
        self.answers = [_endpoint_payload(), _endpoint_payload(five=20.0)]
        usage.read_claude(NOW, self.missing)
        src = usage.read_claude(NOW + 90, self.missing)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(src["via"], "endpoint-cached")
        self.assertEqual(src["ageSeconds"], 90)
        src = usage.read_claude(NOW + usage.ENDPOINT_MIN_INTERVAL_S, self.missing)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(src["via"], "endpoint")

    def test_retry_after_is_respected_before_asking_again(self):
        self.answers = [_refusal("1800"), _endpoint_payload()]
        usage.read_claude(NOW, self.missing)
        usage.read_claude(NOW + 1799, self.missing)
        self.assertEqual(len(self.calls), 1)
        src = usage.read_claude(NOW + 1800, self.missing)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(src["via"], "endpoint")

    def test_retry_after_as_an_http_date(self):
        err = _refusal("Sun, 13 Sep 2026 07:13:20 GMT")          # NOW + 1 h
        self.assertEqual(usage._retry_after_seconds(err, NOW), 3600)

    def test_a_retry_after_longer_than_an_hour_is_not_shortened(self):
        err = _refusal("Sun, 13 Sep 2026 08:13:20 GMT")          # NOW + 2 h
        self.assertEqual(usage._retry_after_seconds(err, NOW), 7200)
        self.answers = [_refusal("7200"), _endpoint_payload()]
        usage.read_claude(NOW, self.missing)
        usage.read_claude(NOW + 3601, self.missing)
        usage.read_claude(NOW + 7199, self.missing)
        self.assertEqual(len(self.calls), 1)
        src = usage.read_claude(NOW + 7200, self.missing)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(src["via"], "endpoint")

    def test_a_non_finite_retry_after_falls_back_to_the_default_wait(self):
        for raw in ("inf", "1e309", "nan", "-inf"):
            with self.subTest(raw=raw):
                usage._reset_claude_state()
                self.calls.clear()
                self.answers = [_refusal(raw), _endpoint_payload()]
                first = usage.read_claude(NOW, self.missing)
                self.assertEqual(first["state"], "unavailable")
                self.assertIn("rate-limiting", first["error"])
                usage.read_claude(NOW + usage.DEFAULT_BACKOFF_S - 1, self.missing)
                self.assertEqual(len(self.calls), 1)
                src = usage.read_claude(NOW + usage.DEFAULT_BACKOFF_S, self.missing)
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(src["via"], "endpoint")

    def test_a_second_refusal_waits_at_least_what_the_server_asked(self):
        self.answers = [_refusal("5000"), _refusal("5000"), _endpoint_payload()]
        usage.read_claude(NOW, self.missing)
        usage.read_claude(NOW + 5000, self.missing)
        self.assertEqual(len(self.calls), 2)
        usage.read_claude(NOW + 5000 + 4999, self.missing)
        self.assertEqual(len(self.calls), 2)
        usage.read_claude(NOW + 10000, self.missing)
        self.assertEqual(len(self.calls), 3)

    def test_newer_endpoint_reading_beats_a_stale_local_one(self):
        path = _statusline_file(self.tmp, age=3 * 3600)
        self.answers = [_endpoint_payload(five=12.0)]
        src = usage.read_claude(NOW, path)
        self.assertEqual(src["via"], "endpoint")

    def test_both_sources_missing_is_unknown(self):
        self.answers = [_refusal()]
        src = usage.read_claude(NOW, self.missing)
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("no hub-launched Claude agent", src["error"])
        self.assertIn("rate-limiting", src["error"])
        self.assertEqual(dashboard._kind_usage(_snapshot(src), "claude")["state"], "unknown")
        decision = dashboard.choose_agent_kind_for_seat(
            "claude", _snapshot(src), installed=lambda kind: True)
        self.assertEqual(decision["decision"], "unknown")

    def test_both_sources_name_the_same_reset_minute(self):
        path = _statusline_file(self.tmp, age=60, five_reset=1789293600)  # 10:00Z
        local = usage.read_claude_statusline(NOW, path)
        self.answers = [_endpoint_payload()]
        remote = usage.read_claude_endpoint(NOW)
        self.assertEqual(local["windows"][0]["resetsAt"], remote["windows"][0]["resetsAt"])


class StatuslineWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.transcript = self.tmp / "session.jsonl"
        self.transcript.write_text("{}\n", encoding="utf-8")
        self.target = self.tmp / "usage" / "claude-rate-limits.json"

    def payload(self, five=7):
        return {"session_id": "s1", "transcript_path": str(self.transcript),
                "model": {"id": "claude-opus-5"}, "version": "2.1.270",
                "rate_limits": {"five_hour": {"used_percentage": five, "resets_at": 1789293000},
                                "seven_day": {"used_percentage": 35, "resets_at": 1789560000}}}

    def test_script_writes_the_limits_and_prints_nothing(self):
        out = subprocess.run(
            [sys.executable, str(REPO / "usage_statusline.py"), str(self.target)],
            input=json.dumps(self.payload()), capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        held = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(held["rateLimits"]["five_hour"]["used_percentage"], 7)
        self.assertAlmostEqual(held["asOf"], self.transcript.stat().st_mtime, places=3)
        src = usage.read_claude_statusline(held["asOf"] + 5, self.target)
        self.assertEqual((src["state"], src["via"]), ("ok", "statusline"))

    def test_an_older_reading_never_replaces_a_newer_one(self):
        self.assertTrue(usage_statusline.record(self.payload(five=7), self.target))
        old = time.time() - 3600
        os.utime(self.transcript, (old, old))
        self.assertFalse(usage_statusline.record(self.payload(five=99), self.target))
        held = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(held["rateLimits"]["five_hour"]["used_percentage"], 7)

    def test_concurrent_writers_cannot_land_the_older_reading_last(self):
        # The older writer passes its check first and stalls before publishing;
        # the newer one runs meanwhile. Unserialized, the older lands last.
        old_transcript = self.tmp / "old.jsonl"
        old_transcript.write_text("{}\n", encoding="utf-8")
        old = time.time() - 3600
        os.utime(old_transcript, (old, old))
        older = dict(self.payload(five=99), transcript_path=str(old_transcript))
        newer = self.payload(five=10)
        real_publish = usage_statusline._publish
        older_checked = threading.Event()

        def publish(target, text):
            if threading.current_thread().name == "older":
                older_checked.set()
                time.sleep(0.5)
            return real_publish(target, text)

        with mock.patch.object(usage_statusline, "_publish", side_effect=publish):
            first = threading.Thread(name="older",
                                     target=usage_statusline.record, args=(older, self.target))
            first.start()
            self.assertTrue(older_checked.wait(5))
            usage_statusline.record(newer, self.target)
            first.join(5)
        held = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(held["rateLimits"]["five_hour"]["used_percentage"], 10)

    def test_nothing_is_written_without_limits_or_a_transcript(self):
        data = self.payload()
        del data["rate_limits"]
        self.assertFalse(usage_statusline.record(data, self.target))
        data = self.payload()
        data["transcript_path"] = str(self.tmp / "not-yet.jsonl")
        self.assertFalse(usage_statusline.record(data, self.target))
        self.assertFalse(self.target.exists())


class LaunchSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        for name, value in (("RTK_DIR", tmp / "rtk"),
                            ("RTK_CLAUDE_SETTINGS", tmp / "rtk" / "claude-task-settings.json"),
                            ("USAGE_CLAUDE_SETTINGS", tmp / "usage" / "claude-agent-settings.json")):
            patch = mock.patch.object(dashboard, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def settings_of(self, args):
        self.assertEqual(args[0], "--settings")
        return json.loads(Path(args[1]).read_text(encoding="utf-8"))

    def test_rtk_task_keeps_its_hook_and_gains_the_status_line(self):
        with mock.patch.object(dashboard, "_rtk_task_room", return_value=True):
            args, env, brief = dashboard._rtk_task_wiring({"id": "room-x"}, "claude")
        settings = self.settings_of(args)
        hook = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Bash")
        self.assertIn("hook claude", hook["hooks"][0]["command"])
        self.assertIn("usage_statusline.py", settings["statusLine"]["command"])
        self.assertTrue(brief)

    def test_other_claude_launches_get_the_status_line_only(self):
        with mock.patch.object(dashboard, "_rtk_task_room", return_value=False):
            args, env, brief = dashboard._rtk_task_wiring({"id": "room-x"}, "claude")
            codex_args, _, _ = dashboard._rtk_task_wiring({"id": "room-x"}, "codex")
        settings = self.settings_of(args)
        self.assertEqual(set(settings), {"statusLine"})
        self.assertIn(usage.CLAUDE_STATUSLINE_FILE.as_posix(), settings["statusLine"]["command"])
        self.assertEqual((env, brief, codex_args), ({}, "", []))


class CodexTokenTests(unittest.TestCase):
    def test_codex_tokens_count_each_request_once(self):
        folder = Path(tempfile.mkdtemp()) / "2026" / "09" / "13"
        folder.mkdir(parents=True)
        sid = "0192aaaa-bbbb-cccc-dddd-eeeeffff0000"
        rollout = folder / f"rollout-2026-09-13T06-00-00-{sid}.jsonl"

        def count(total, last_in, cached, out):
            return {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"total_tokens": total},
                "last_token_usage": {"input_tokens": last_in, "cached_input_tokens": cached,
                                     "output_tokens": out}}}}
        records = [
            {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
            count(1100, 1000, 0, 100),
            count(1100, 1000, 0, 100),          # limits-only repeat: skipped
            count(2300, 1100, 900, 100),
        ]
        rollout.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        class Codex:
            def sessions_dir(self):
                return folder.parent.parent.parent

        room = {"participants": [
            {"kind": "agent", "agent": "codex", "sessionId": "", "identity": "codex",
             "reviews": [{"sessionId": sid}]},
        ]}
        with mock.patch.object(dashboard.agents, "get_agent", return_value=Codex()):
            dashboard._CODEX_ROLLOUT_PATHS.clear()
            cost = dashboard.compute_room_cost(room)
        self.assertEqual(cost["tokens"],
                         {"input": 1200, "output": 200, "cacheWrite": 0, "cacheRead": 900})
        self.assertEqual(cost["dollars"], 0.0)
        self.assertTrue(cost["byModel"]["gpt-5.6-sol"]["unknownPricing"])


if __name__ == "__main__":
    unittest.main()
