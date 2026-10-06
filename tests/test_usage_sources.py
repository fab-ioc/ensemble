"""Claude's plan-window reading: the local statusline file only. Ensemble never
reads Claude Code's sign-in token (ED-179), so with no current file the reading
is unknown — never 0%, never "free" to allocation."""
from __future__ import annotations

import builtins
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
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


def _snapshot(claude: dict, codex: dict | None = None) -> dict:
    return {"state": "ready", "warnPercent": 80, "alarmPercent": 95,
            "sources": [claude, codex or usage._unavailable("codex", "none")]}


def _codex_at(percent: float) -> dict:
    """A current Codex reading of the main pool at ``percent``."""
    return {"source": "codex", "state": "ok", "error": None, "via": "app-server",
            "ageSeconds": 0, "trusted": True, "pools": [], "windows": [
                usage._window("seven_day", "7d", percent=percent, window_minutes=10080,
                              resets_at=usage._minute_iso(NOW + 3 * 86400),
                              age_seconds=0, pool=usage.CODEX_MAIN_POOL)]}


class ClaudeReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.missing = self.tmp / "absent.json"
        # No Claude reading may touch the network.
        net = mock.patch.object(socket, "create_connection",
                                side_effect=AssertionError("network used"))
        net.start()
        self.addCleanup(net.stop)

    def test_fresh_local_reading_is_used(self):
        path = _statusline_file(self.tmp, age=120)
        src = usage.read_claude(NOW, path)
        self.assertEqual(src["state"], "ok")
        self.assertEqual(src["via"], "statusline")
        self.assertTrue(src["trusted"])
        self.assertEqual(src["ageSeconds"], 120)
        self.assertEqual({w["kind"]: w["percent"] for w in src["windows"]},
                         {"five_hour": 40.0, "seven_day": 35.0})
        self.assertEqual(dashboard._kind_usage(_snapshot(src), "claude"),
                         {"state": "known", "percent": 40.0, "window": "five_hour",
                          "label": "5-hour", "trusted": True, "atLeast": False})

    def test_stale_local_reading_stays_known_as_a_floor(self):
        path = _statusline_file(self.tmp, age=2 * 3600, five=85)
        src = usage.read_claude(NOW, path)
        self.assertEqual(src["via"], "statusline")
        self.assertEqual(src["ageSeconds"], 2 * 3600)
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        seven = next(w for w in src["windows"] if w["kind"] == "seven_day")
        self.assertFalse(five["trusted"])          # 2 h against a 5 h window
        self.assertEqual(five["percent"], 85.0)    # kept, as a floor
        self.assertTrue(seven["trusted"])          # 2 h against a week
        figure = dashboard._kind_usage(_snapshot(src), "claude")
        self.assertEqual((figure["state"], figure["percent"], figure["atLeast"]),
                         ("known", 85.0, True))

    def test_local_window_past_its_reset_has_no_current_percent(self):
        path = _statusline_file(self.tmp, age=60, five_reset=NOW - 10)
        src = usage.read_claude(NOW, path)
        five = next(w for w in src["windows"] if w["kind"] == "five_hour")
        self.assertTrue(five["rolledOver"])
        self.assertIsNone(five["percent"])
        self.assertEqual(five["stalePercent"], 40.0)

    def test_a_reading_whose_windows_have_all_reset_is_unknown_not_zero(self):
        # Too old: both windows have rolled over since the last agent turn.
        path = _statusline_file(self.tmp, age=8 * 86400, five_reset=NOW - 7 * 86400,
                                seven_reset=NOW - 86400)
        src = usage.read_claude(NOW, path)
        self.assertTrue(all(w["percent"] is None for w in src["windows"]))
        snap = _snapshot(src, _codex_at(10))
        self.assertEqual(dashboard._kind_usage(snap, "claude")["state"], "unknown")
        self.assertEqual(dashboard._kind_pace(snap, "claude", now=NOW)["state"], "unknown")
        self.assertEqual(usage.alerts(snap["sources"]), [])

    def test_local_reading_with_an_implausible_reset_fails_loudly(self):
        path = _statusline_file(self.tmp, age=60, five_reset=18000)   # a duration
        src = usage.read_claude_statusline(NOW, path)
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("format", src["error"])

    def test_no_local_reading_is_unknown_to_the_chip_pacing_and_allocation(self):
        src = usage.read_claude(NOW, self.missing)
        self.assertEqual(src["state"], "unavailable")
        self.assertEqual(src["windows"], [])
        self.assertIn("no hub-launched Claude agent", src["error"])
        snap = _snapshot(src, _codex_at(90))       # Codex past the warning
        self.assertEqual(dashboard._kind_usage(snap, "claude")["state"], "unknown")
        self.assertEqual(dashboard._kind_pace(snap, "claude", now=NOW)["state"], "unknown")
        # Unknown is not "free": a seat on Codex at 90% is not moved to Claude.
        decision = dashboard.choose_agent_kind_for_seat(
            "codex", snap, installed=lambda kind: True)
        self.assertEqual((decision["decision"], decision["chosenKind"]), ("unknown", "codex"))
        decision = dashboard.choose_agent_kind_for_seat(
            "claude", snap, installed=lambda kind: True)
        self.assertEqual((decision["decision"], decision["chosenKind"]), ("unknown", "claude"))

    def test_no_code_path_opens_claude_codes_sign_in_file(self):
        home = self.tmp / "home"
        creds = home / ".claude" / ".credentials.json"
        creds.parent.mkdir(parents=True)
        creds.write_text('{"claudeAiOauth": {"accessToken": "sk-ant-oat-test"}}',
                         encoding="utf-8")
        opened = []
        real_open, real_io_open, real_read = builtins.open, io.open, Path.read_text

        def note(path):
            opened.append(os.path.basename(os.fspath(path)) if isinstance(
                path, (str, bytes, os.PathLike)) else "")

        def spy_open(file, *a, **k):
            note(file)
            return real_open(file, *a, **k)

        def spy_io_open(file, *a, **k):
            note(file)
            return real_io_open(file, *a, **k)

        def spy_read(self_, *a, **k):
            note(self_)
            return real_read(self_, *a, **k)

        with mock.patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}), \
                mock.patch.object(builtins, "open", spy_open), \
                mock.patch.object(io, "open", spy_io_open), \
                mock.patch.object(Path, "read_text", spy_read), \
                mock.patch.object(usage, "CLAUDE_STATUSLINE_FILE",
                                  self.tmp / "claude-rate-limits.json"), \
                mock.patch.dict(usage._STATE, {}), \
                mock.patch.object(usage, "read_codex",
                                  return_value=usage._unavailable("codex", "test")):
            # Codex's reader is stubbed (it starts `codex`); that it never opens
            # auth.json rests on the source check in the next test.
            for path in (self.missing, _statusline_file(self.tmp, age=60),
                         _statusline_file(self.tmp, age=9 * 86400)):
                usage.read_claude(NOW, path)
                usage.read_claude(None, path)
            usage.refresh()
            usage.snapshot()
        self.assertIn("claude-rate-limits.json", opened)   # the spy saw the reads
        self.assertNotIn(".credentials.json", opened)
        self.assertNotIn("auth.json", opened)

    def test_the_sign_in_file_and_the_endpoint_are_gone_from_the_module(self):
        for name in ("CREDENTIALS", "USAGE_URL", "read_claude_endpoint", "_access_token",
                     "_fetch_endpoint", "ENDPOINT_MIN_INTERVAL_S"):
            self.assertFalse(hasattr(usage, name), name)
        source = (REPO / "usage.py").read_text(encoding="utf-8")
        self.assertNotIn("api/oauth", source)
        self.assertNotIn("accessToken", source)
        for name in ("usage.py", "agent_models.py", "agents/codex.py", "agents/claude.py"):
            text = (REPO / name).read_text(encoding="utf-8")
            # As a path in code (a quoted name), not as words in a docstring.
            for file in (".credentials.json", "auth.json"):
                self.assertNotRegex(text, rf"[\"']{re.escape(file)}[\"']", name)
        self.assertNotIn("claudeEndpoint", usage.snapshot())


class StatuslineWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.transcript = self.tmp / "session.jsonl"
        self.transcript.write_text("{}\n", encoding="utf-8")
        self.target = self.tmp / "usage" / "claude-rate-limits.json"

    def payload(self, five=7):
        return {"session_id": "s1", "transcript_path": str(self.transcript),
                "model": {"id": "claude-opus-5"}, "version": "2.1.270",
                # Reset times ahead of the clock: a window whose reset has passed
                # reads as rolled over, and the test would fail with the calendar.
                "rate_limits": {"five_hour": {"used_percentage": five,
                                              "resets_at": int(time.time()) + 3600},
                                "seven_day": {"used_percentage": 35,
                                              "resets_at": int(time.time()) + 3 * 86400}}}

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

    def test_other_claude_launches_get_the_status_line_and_no_rtk(self):
        with mock.patch.object(dashboard, "_rtk_task_room", return_value=False):
            args, env, brief = dashboard._rtk_task_wiring({"id": "room-x"}, "claude")
            codex_args, _, _ = dashboard._rtk_task_wiring({"id": "room-x"}, "codex")
        settings = self.settings_of(args)
        # The attention hooks (tests/test_agent_hooks.py) and the status line.
        self.assertEqual(set(settings), {"hooks", "statusLine"})
        self.assertNotIn("hook claude", json.dumps(settings["hooks"]))
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
