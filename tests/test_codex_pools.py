"""Codex's pools — main, reserve, a model's own — read and judged apart.

The app server's answer first, the session records when it cannot be had; the
reserve never under the main pool's label; and Codex judged, for allocation
and for alarms, by the pool its agents actually run on."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import dashboard
import ensemble_tools
import rotation
import usage

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANSWER = json.loads((FIXTURES / "codex_app_server_rate_limits.json").read_text(encoding="utf-8"))
FAKE_SERVER = FIXTURES / "fake_codex_app_server.py"

NOW = 1_789_620_900.0          # 2026-09-17, when the fixture was taken
MAIN_RESET = 1789881391
RESERVE_RESET = 1790225470


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _turn(model: str) -> dict:
    return {"type": "turn_context", "payload": {"model": model, "cwd": "."}}


def _count(at: float, percent, resets_at, *, limit_id="codex", limit_name=None,
           minutes=10080, plan="prolite", secondary=None) -> dict:
    return {"timestamp": _iso(at), "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {
            "limit_id": limit_id, "limit_name": limit_name,
            "primary": {"used_percent": percent, "window_minutes": minutes,
                        "resets_at": resets_at},
            "secondary": secondary, "plan_type": plan}}}


class _Case(unittest.TestCase):
    config_model = "gpt-reserve"

    def setUp(self):
        usage._reset_codex_state()
        self.addCleanup(usage._reset_codex_state)
        self.tmp = Path(tempfile.mkdtemp())
        self._n = 0
        config = mock.patch.object(usage, "codex_config_model",
                                   side_effect=lambda *a: self.config_model)
        config.start()
        self.addCleanup(config.stop)
        # Never this machine's own session records: a test that gives no files
        # reads none.
        rollouts = mock.patch.object(usage, "_rollout_files", return_value=[])
        rollouts.start()
        self.addCleanup(rollouts.stop)

    def rollout(self, *records) -> Path:
        self._n += 1
        path = self.tmp / f"rollout-{self._n}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path

    def answered(self, answer=None):
        return lambda: (copy.deepcopy(ANSWER if answer is None else answer), "")

    def by_pool(self, src) -> dict:
        return {(w["pool"], w["kind"]): w for w in src["windows"]}


class AppServerAnswer(_Case):
    def test_each_pool_is_read_apart_and_named_in_plain_words(self):
        src = usage.read_codex(NOW, app_server=self.answered())
        self.assertEqual((src["state"], src["via"], src["planType"]), ("ok", "app-server", "prolite"))
        self.assertEqual([(w["pool"], w["model"], w["kind"], w["percent"]) for w in src["windows"]], [
            ("codex", None, "seven_day", 97.0),                  # the main pool leads, unlabelled
            ("base_model_inference", "reserve", "seven_day", 0.0),
            ("codex_bengalfox", "GPT-5.3-Codex-Spark", "five_hour", 0.0),
            ("codex_bengalfox", "GPT-5.3-Codex-Spark", "seven_day", 0.0),
        ])
        self.assertTrue(all(w["trusted"] and w["ageSeconds"] == 0 for w in src["windows"]))
        self.assertEqual(src["windows"][0]["resetsAt"], "2026-09-20T05:16:31+00:00")
        self.assertTrue(src["trusted"])

    def test_the_reset_credits_and_the_account_id_go_nowhere(self):
        src = usage.read_codex(NOW, app_server=self.answered())
        text = json.dumps(src)
        for absent in ("Credit", "credit", "account-id", "Full reset"):
            self.assertNotIn(absent, text)

    def test_an_unknown_pool_is_kept_and_named_by_codex_or_else_by_its_id(self):
        answer = copy.deepcopy(ANSWER)
        answer["rateLimitsByLimitId"]["codex_newthing"] = {
            "limitId": "codex_newthing", "limitName": "GPT-7-Nova", "planType": "prolite",
            "primary": {"usedPercent": 12, "windowDurationMins": 1440, "resetsAt": NOW + 3600}}
        answer["rateLimitsByLimitId"]["codex_unnamed"] = {
            "limitId": "codex_unnamed", "limitName": None,
            "primary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": NOW + 3600}}
        src = usage.read_codex(NOW, app_server=self.answered(answer))
        wins = self.by_pool(src)
        nova = wins[("codex_newthing", "window_1440")]
        self.assertEqual((nova["model"], nova["label"], nova["percent"]), ("GPT-7-Nova", "1d", 12.0))
        self.assertEqual(wins[("codex_unnamed", "seven_day")]["model"], "codex_unnamed")
        self.assertEqual(wins[("codex", "seven_day")]["percent"], 97.0)

    def test_an_answer_with_only_the_account_pool_still_reads(self):
        src = usage.read_codex(NOW, app_server=self.answered({"rateLimits": ANSWER["rateLimits"]}))
        self.assertEqual([(w["pool"], w["percent"]) for w in src["windows"]], [("codex", 97.0)])

    def test_a_pool_the_answer_leaves_out_keeps_its_session_record(self):
        spark = {"used_percent": 4, "window_minutes": 10080, "resets_at": RESERVE_RESET}
        files = [
            self.rollout(_turn("gpt-reserve"), _count(NOW - 120, 20, RESERVE_RESET)),
            self.rollout(_turn("gpt-5.3-codex-spark"), _count(
                NOW - 60, 31, NOW + 9000, limit_id="codex_bengalfox",
                limit_name="GPT-5.3-Codex-Spark", minutes=300, plan=None, secondary=spark)),
            # ... and a record of the main pool whose clock runs ahead of ours:
            # this poll's answer stands all the same.
            self.rollout(_turn("gpt-5.6-sol"), _count(NOW + 5, 90, MAIN_RESET)),
        ]
        src = usage.read_codex(NOW, files=files,
                               app_server=self.answered({"rateLimits": ANSWER["rateLimits"]}))
        self.assertEqual((src["via"], src["note"], src["planType"]), ("app-server", None, "prolite"))
        self.assertEqual(
            [(w["pool"], w["model"], w["kind"], w["percent"], w["ageSeconds"]) for w in src["windows"]], [
                ("codex", None, "seven_day", 97.0, 0),
                ("base_model_inference", "reserve", "seven_day", 20.0, 120),
                ("codex_bengalfox", "GPT-5.3-Codex-Spark", "five_hour", 31.0, 60),
                ("codex_bengalfox", "GPT-5.3-Codex-Spark", "seven_day", 4.0, 60),
            ])
        self.assertEqual([(p["id"], p["ageSeconds"]) for p in src["pools"]],
                         [("codex", 0), ("base_model_inference", 120), ("codex_bengalfox", 60)])
        # The records keep their guards beside the answer: past its reset the
        # reserve's record is withheld, the answer's main pool is not.
        src = usage.read_codex(RESERVE_RESET + 10, files=files, app_server=self.answered(
            {"rateLimits": dict(ANSWER["rateLimits"], primary=dict(
                ANSWER["rateLimits"]["primary"], resetsAt=RESERVE_RESET + 86400))}))
        wins = self.by_pool(src)
        self.assertEqual(wins[("codex", "seven_day")]["percent"], 97.0)
        self.assertTrue(wins[("base_model_inference", "seven_day")]["rolledOver"])
        self.assertIsNone(wins[("base_model_inference", "seven_day")]["percent"])

    def test_a_record_that_cannot_be_read_costs_its_pool_not_the_answer(self):
        files = [self.rollout(_turn("gpt-reserve"), _count(NOW - 120, 20, "2026-09-24"))]
        src = usage.read_codex(NOW, files=files,
                               app_server=self.answered({"rateLimits": ANSWER["rateLimits"]}))
        self.assertEqual((src["state"], src["via"]), ("ok", "app-server"))
        self.assertEqual([w["pool"] for w in src["windows"]], ["codex"])
        self.assertEqual([p["id"] for p in src["pools"]], ["codex"])
        # Without an answer the same record fails the source, as before.
        src = usage.read_codex(NOW, files=files)
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("unfamiliar format", src["error"])

    def test_a_reset_time_that_is_not_a_date_falls_back_to_the_session_records(self):
        for bad in (18000, "2026-09-20T05:16:31Z", True):
            with self.subTest(bad=bad):
                usage._reset_codex_state()
                answer = copy.deepcopy(ANSWER)
                answer["rateLimitsByLimitId"]["codex"]["primary"]["resetsAt"] = bad
                files = [self.rollout(_turn("gpt-5.6-sol"), _count(NOW - 60, 96, MAIN_RESET))]
                src = usage.read_codex(NOW, files=files, app_server=self.answered(answer))
                self.assertEqual((src["state"], src["via"]), ("ok", "sessions"))
                self.assertIn("unfamiliar format", src["note"])
                self.assertEqual(self.by_pool(src)[("codex", "seven_day")]["percent"], 96.0)


class FallBack(_Case):
    def files(self):
        return [self.rollout(_turn("gpt-5.6-luna"), _count(NOW - 600, 97, MAIN_RESET))]

    def test_a_refusal_serves_the_session_records_and_says_why(self):
        src = usage.read_codex(NOW, files=self.files(),
                               app_server=lambda: (None, "Codex is not installed on this machine"))
        self.assertEqual((src["state"], src["via"]), ("ok", "sessions"))
        self.assertEqual(src["note"], "Codex is not installed on this machine")
        self.assertEqual(self.by_pool(src)[("codex", "seven_day")]["percent"], 97.0)

    def test_a_failed_app_server_is_not_started_again_for_a_while(self):
        calls = []

        def refuse():
            calls.append(1)
            return None, "Codex's app server did not answer within 12 s"
        files = self.files()
        usage.read_codex(NOW, files=files, app_server=refuse)
        usage.read_codex(NOW + 90, files=files, app_server=refuse)
        self.assertEqual(len(calls), 1)
        src = usage.read_codex(NOW + usage.CODEX_APP_RETRY_S, files=files, app_server=refuse)
        self.assertEqual(len(calls), 2)
        self.assertIn("did not answer", src["note"])

    def test_while_it_fails_a_pool_keeps_the_newer_of_its_last_answer_and_the_records(self):
        usage.read_codex(NOW, app_server=self.answered())
        later = NOW + 3600
        files = [self.rollout(_turn("gpt-5.6-luna"), _count(later - 30, 98, MAIN_RESET))]
        src = usage.read_codex(later, files=files, app_server=lambda: (None, "it timed out"))
        wins = self.by_pool(src)
        self.assertEqual(src["via"], "sessions")
        self.assertEqual((wins[("codex", "seven_day")]["percent"],
                          wins[("codex", "seven_day")]["ageSeconds"]), (98.0, 30))
        reserve = wins[("base_model_inference", "seven_day")]
        self.assertEqual((reserve["percent"], reserve["ageSeconds"], reserve["trusted"]),
                         (0.0, 3600, True))
        spark5 = wins[("codex_bengalfox", "five_hour")]
        self.assertFalse(spark5["trusted"])                  # an hour against five
        # ... and the aged answer goes through the rollover guard like a record.
        src = usage.read_codex(MAIN_RESET + 10, files=files,
                               app_server=lambda: (None, "it timed out"))
        spark5 = self.by_pool(src)[("codex_bengalfox", "five_hour")]
        self.assertTrue(spark5["rolledOver"])
        self.assertIsNone(spark5["percent"])

    def test_nothing_at_all_is_unavailable_with_both_reasons(self):
        src = usage.read_codex(NOW, files=[], app_server=lambda: (None, "Codex is not installed"))
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("not installed", src["error"])
        self.assertIn("no Codex session", src["error"])
        self.assertEqual(src["poolInUse"]["id"], "base_model_inference")

    def test_injected_files_alone_never_start_codex(self):
        with mock.patch.object(usage, "read_codex_app_server") as real:
            usage.read_codex(NOW, files=self.files())
        real.assert_not_called()


class AppServerProcess(_Case):
    """The child process itself, against a stand-in server."""

    def serve(self, mode: str, *more):
        self.pid_file = self.tmp / "pid"
        argv = [sys.executable, str(FAKE_SERVER), mode, str(self.pid_file), *more]
        patch = mock.patch.object(usage, "_codex_app_argv", return_value=argv)
        patch.start()
        self.addCleanup(patch.stop)

    def server_alive(self) -> bool:
        pid = int(self.pid_file.read_text(encoding="utf-8"))
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return code.value == 259
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def gone(self) -> bool:
        deadline = time.time() + 5
        while time.time() < deadline and self.server_alive():
            time.sleep(0.1)
        return not self.server_alive()

    def test_it_asks_reads_and_kills_the_whole_tree(self):
        self.serve("ok", "--shim")
        result, why = usage.read_codex_app_server(timeout=20)
        self.assertEqual(why, "")
        self.assertEqual(result["rateLimitsByLimitId"]["base_model_inference"]["limitName"],
                         "gpt-reserve")
        self.assertTrue(self.gone())

    def test_a_server_that_never_answers_is_killed_at_the_timeout(self):
        self.serve("hang", "--shim")
        started = time.time()
        result, why = usage.read_codex_app_server(timeout=3)
        self.assertIsNone(result)
        self.assertIn("did not answer within 3 s", why)
        self.assertLess(time.time() - started, 15)
        self.assertTrue(self.gone())
        src = usage.read_codex(NOW, files=[self.rollout(
            _turn("gpt-5.6-sol"), _count(NOW - 60, 97, MAIN_RESET))],
            app_server=lambda: (result, why))
        self.assertEqual((src["state"], src["via"]), ("ok", "sessions"))

    @unittest.skipUnless(os.name == "nt", "the job and taskkill are Windows'")
    def test_the_tree_ends_with_its_job_and_no_taskkill_is_started(self):
        self.serve("hang", "--shim")
        with mock.patch.object(usage.subprocess, "run") as run:
            result, _ = usage.read_codex_app_server(timeout=2)
        self.assertIsNone(result)
        run.assert_not_called()
        self.assertTrue(self.gone())

    @unittest.skipUnless(os.name == "nt", "the job and taskkill are Windows'")
    def test_without_a_job_taskkill_ends_the_tree(self):
        self.serve("hang", "--shim")
        with mock.patch.object(usage, "_job_open", return_value=None):
            result, _ = usage.read_codex_app_server(timeout=2)
        self.assertIsNone(result)
        self.assertTrue(self.gone())

    @unittest.skipUnless(os.name == "nt", "the job and taskkill are Windows'")
    def test_the_kill_gets_only_the_time_that_is_left(self):
        self.serve("hang")
        seen = {}

        def slow_taskkill(argv, **kw):
            seen.update(kw, argv=argv)
            time.sleep(kw["timeout"])                        # a taskkill that hangs
            raise usage.subprocess.TimeoutExpired(argv, kw["timeout"])
        started = time.monotonic()
        with mock.patch.object(usage, "_job_open", return_value=None),                 mock.patch.object(usage.subprocess, "run", side_effect=slow_taskkill):
            result, _ = usage.read_codex_app_server(timeout=2)
        took = time.monotonic() - started
        self.assertIsNone(result)
        self.assertEqual(seen["argv"][:3], ["taskkill", "/F", "/T"])
        self.assertLessEqual(seen["timeout"], usage.CODEX_APP_KILL_S)
        self.assertEqual(seen["creationflags"], usage._NO_WINDOW)
        self.assertLess(took, 2 + usage.CODEX_APP_KILL_S + 0.5)
        self.assertTrue(self.gone())                         # proc.kill() still had its turn

    def test_the_production_budget_is_fifteen_seconds(self):
        self.assertLessEqual(usage.CODEX_APP_TIMEOUT_S + usage.CODEX_APP_KILL_S, 15)

    def test_an_older_codex_without_the_method_is_a_refusal(self):
        self.serve("old")
        result, why = usage.read_codex_app_server(timeout=20)
        self.assertIsNone(result)
        self.assertIn("refused the reading", why)
        self.assertTrue(self.gone())

    def test_a_missing_binary_is_a_reason_not_an_error(self):
        with mock.patch.object(usage, "_codex_app_argv", return_value=None):
            self.assertEqual(usage.read_codex_app_server(),
                             (None, "Codex is not installed on this machine"))
        with mock.patch.object(usage, "_codex_app_argv",
                               return_value=[str(self.tmp / "no-such-codex"), "app-server"]):
            result, why = usage.read_codex_app_server()
        self.assertIsNone(result)
        self.assertIn("could not ask", why)


class SessionRecords(_Case):
    def test_a_turn_on_the_reserve_never_lands_under_the_main_label(self):
        main = self.rollout(_turn("gpt-5.6-luna"), _count(NOW - 100, 97, MAIN_RESET))
        reserve = self.rollout(_turn("gpt-reserve"), _count(NOW - 50, 0, RESERVE_RESET))
        for files in ([reserve, main], [main, reserve]):
            src = usage.read_codex(NOW, files=files)
            wins = self.by_pool(src)
            self.assertEqual(len(src["windows"]), 2)
            self.assertEqual((wins[("codex", "seven_day")]["model"],
                              wins[("codex", "seven_day")]["percent"]), (None, 97.0))
            self.assertEqual((wins[("base_model_inference", "seven_day")]["model"],
                              wins[("base_model_inference", "seven_day")]["percent"]),
                             ("reserve", 0.0))
        # Whichever wrote last, the figures stay where they are.
        main_later = self.rollout(_turn("gpt-5.6-luna"), _count(NOW - 10, 98, MAIN_RESET))
        wins = self.by_pool(usage.read_codex(NOW, files=[main_later, reserve, main]))
        self.assertEqual(wins[("codex", "seven_day")]["percent"], 98.0)
        self.assertEqual(wins[("base_model_inference", "seven_day")]["percent"], 0.0)

    def test_a_session_that_changes_model_is_read_turn_by_turn(self):
        one = self.rollout(_turn("gpt-reserve"), _count(NOW - 300, 3, RESERVE_RESET),
                           _turn("gpt-6-astra"), _count(NOW - 200, 40, MAIN_RESET),
                           _turn("GPT-Reserve"), _count(NOW - 100, 4, RESERVE_RESET))
        wins = self.by_pool(usage.read_codex(NOW, files=[one]))
        self.assertEqual(wins[("codex", "seven_day")]["percent"], 40.0)
        self.assertEqual(wins[("base_model_inference", "seven_day")]["percent"], 4.0)

    def test_the_reserve_is_not_dropped_for_trailing_the_main_pool_but_a_model_is(self):
        files = [
            self.rollout(_turn("gpt-reserve"), _count(NOW - 7200, 1, RESERVE_RESET)),
            self.rollout(_turn("gpt-5.6-sol"),
                         _count(NOW - 7000, 50, MAIN_RESET),
                         _count(NOW - 7000, 9, NOW + 3600, limit_id="codex_bengalfox",
                                limit_name="GPT-5.3-Codex-Spark", minutes=300, plan=None),
                         _count(NOW - 60, 55, MAIN_RESET)),
        ]
        src = usage.read_codex(NOW, files=files)
        self.assertEqual(sorted({w["pool"] for w in src["windows"]}),
                         ["base_model_inference", "codex"])
        self.assertEqual(src["planType"], "prolite")

    def test_each_pool_keeps_its_own_guards(self):
        files = [
            self.rollout(_turn("gpt-5.6-sol"), _count(NOW - 60, 97, MAIN_RESET)),
            self.rollout(_turn("gpt-reserve"), _count(NOW - 9 * 86400, 80, NOW - 2 * 86400)),
        ]
        wins = self.by_pool(usage.read_codex(NOW, files=files))
        reserve = wins[("base_model_inference", "seven_day")]
        self.assertTrue(reserve["rolledOver"])
        self.assertEqual((reserve["percent"], reserve["stalePercent"]), (None, 80.0))
        self.assertEqual(wins[("codex", "seven_day")]["percent"], 97.0)

        stale = [self.rollout(_turn("gpt-reserve"), _count(NOW - 2 * 86400, 30, NOW + 4 * 86400))]
        reserve = self.by_pool(usage.read_codex(NOW, files=stale))[("base_model_inference", "seven_day")]
        self.assertEqual((reserve["percent"], reserve["trusted"]), (30.0, False))

        unknown = [self.rollout(_turn("gpt-reserve"), _count(NOW - 60, 30, None))]
        reserve = self.by_pool(usage.read_codex(NOW, files=unknown))[("base_model_inference", "seven_day")]
        self.assertTrue(reserve["resetUnknown"])
        self.assertIsNone(reserve["percent"])

        duration = [self.rollout(_turn("gpt-reserve"), _count(NOW - 60, 30, 604800))]
        src = usage.read_codex(NOW, files=duration)
        self.assertEqual(src["state"], "unavailable")
        self.assertIn("plausible", src["error"])


class PoolInUse(_Case):
    def test_the_config_model_picks_the_pool_in_use(self):
        for model, pool, label in (("gpt-reserve", "base_model_inference", "reserve"),
                                   ("GPT-Reserve", "base_model_inference", "reserve"),
                                   ("gpt-5.3-codex-spark", "codex_bengalfox", "GPT-5.3-Codex-Spark"),
                                   ("gpt-6-astra", "codex", None),
                                   ("gpt-5.6-luna", "codex", None),   # the reserve's "normal" model
                                   ("", "codex", None)):
            with self.subTest(model=model):
                self.config_model = model
                src = usage.read_codex(NOW, app_server=self.answered())
                self.assertEqual((src["poolInUse"]["id"], src["poolInUse"]["label"]), (pool, label))
                self.assertEqual({w["pool"] for w in src["windows"] if w["inUse"]}, {pool})
                self.assertEqual([p["id"] for p in src["pools"] if p["inUse"]], [pool])

    def test_a_spent_pool_that_is_not_in_use_is_a_notice_not_an_alert(self):
        src = usage.read_codex(NOW, app_server=self.answered())
        self.assertEqual(usage.alerts([src]), [])
        notice, = usage.notices([src])
        self.assertEqual((notice["pool"], notice["model"], notice["percent"], notice["level"]),
                         ("codex", None, 97.0, "info"))

    def test_the_pool_in_use_alarms(self):
        self.config_model = "gpt-6-astra"
        src = usage.read_codex(NOW, app_server=self.answered())
        alert, = usage.alerts([src])
        self.assertEqual((alert["pool"], alert["model"], alert["level"]), ("codex", None, "alarm"))
        self.assertEqual(usage.notices([src]), [])

    def test_windows_that_name_no_pool_alert_as_before(self):
        old = {"source": "claude", "state": "ok", "windows": [
            {"kind": "five_hour", "label": "5h", "percent": 85.0, "trusted": True}]}
        self.assertEqual([a["level"] for a in usage.alerts([old])], ["warn"])
        self.assertEqual(usage.notices([old]), [])

    def test_the_snapshot_carries_the_notices(self):
        src = usage.read_codex(NOW, app_server=self.answered())
        with mock.patch.dict(usage._STATE, {"state": "ready", "sources": {"codex": src}}):
            snap = usage.snapshot()
        self.assertEqual(snap["alerts"], [])
        self.assertEqual([n["percent"] for n in snap["notices"]], [97.0])
        # A page that reads `model: null` windows as the main pool still can.
        main = [w for w in snap["sources"][0]["windows"] if w["model"] is None]
        self.assertEqual([w["percent"] for w in main], [97.0])


TRAY_JS = r"""
const esc = (s) => String(s);
const fmtAgo = (s) => s + 's';
const usageWindowRow = (w) => `<row ${w.pool} ${w.kind}>`;
const usageCodexPoolName = (p) => p.label ? p.label + ' pool' : 'main pool';
const USAGE_SRC_NAME = { codex: 'Codex', claude: 'Claude' };
const USAGE = { notices: [] };
%s
const win = (pool, kind, age, trusted) => ({ pool, kind, ageSeconds: age, trusted, rolledOver: false });
const pools = { source: 'codex', state: 'ok', ageSeconds: 30, trusted: false, via: 'app-server',
  poolInUse: { id: 'codex', label: null, model: '' },
  pools: [{ id: 'codex', label: null, ageSeconds: 30, inUse: true },
          { id: 'codex_bengalfox', label: 'Spark', ageSeconds: 3600, inUse: false }],
  windows: [win('codex', 'seven_day', 30, true), win('codex_bengalfox', 'five_hour', 3600, false),
            win('codex_bengalfox', 'seven_day', 3600, true)] };
const one = { source: 'claude', state: 'ok', ageSeconds: 900, trusted: false,
  windows: [{ kind: 'five_hour', ageSeconds: 900, trusted: false, rolledOver: false }] };
console.log(JSON.stringify({ pools: usageSourceHtml(pools), one: usageSourceHtml(one) }));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Tray(unittest.TestCase):
    """index.html's usageSourceHtml, run in Node."""

    @classmethod
    def setUpClass(cls):
        index = (Path(usage.__file__).parent / "index.html").read_text(
            encoding="utf-8").replace("\r\n", "\n")
        i = index.index("function usageSourceHtml(")
        script = Path(tempfile.mkdtemp()) / "tray.cjs"
        script.write_text(TRAY_JS % index[i:index.index("\n}\n", i) + 3], encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), str(script)], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.html = {k: " ".join(v.split()) for k, v in json.loads(proc.stdout).items()}

    def test_a_frozen_pool_tells_its_own_age_not_the_newest_pool_s(self):
        html = self.html["pools"]
        self.assertIn("a pool’s usage when one of its agents takes a turn on it, "
                      "and none has for 3600s.", html)
        self.assertNotIn("none has for 30s", html)
        # The age sits on the pool it belongs to; the fresh pool and the source
        # head carry none.
        self.assertEqual(html.count("as of "), 1)
        self.assertRegex(html, r'Spark pool</span>\s*<span class="usage-src-age untrusted" '
                               r'>as of 3600s ago</span>')

    def test_a_source_without_pools_reads_as_before(self):
        html = self.html["one"]
        self.assertIn("only writes its usage when one of its agents takes a turn, "
                      "and none has for 900s.", html)
        self.assertEqual(html.count("as of 900s ago"), 1)
        self.assertNotIn("usage-pool", html)


class ConfigModel(unittest.TestCase):
    def read(self, text: str | None) -> str:
        folder = Path(tempfile.mkdtemp())
        if text is not None:
            (folder / "config.toml").write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(folder)}):
            return usage.codex_config_model()

    def test_the_top_level_model(self):
        self.assertEqual(self.read('model = "gpt-reserve"\n\n[projects.\'d:\\work\\x\']\n'
                                   'trust_level = "trusted"\n'), "gpt-reserve")

    def test_a_table_s_model_is_not_the_default(self):
        self.assertEqual(self.read('[profiles.fast]\nmodel = "gpt-5.6-luna"\n'), "")

    def test_the_active_profile_s_model_wins(self):
        self.assertEqual(self.read('model = "gpt-6-astra"\nprofile = "spare"\n'
                                   '[profiles.spare]\nmodel = "gpt-reserve"\n'), "gpt-reserve")

    def test_no_file_or_a_broken_one(self):
        self.assertEqual(self.read(None), "")
        self.assertEqual(self.read('model = "gpt-reserve"\n[broken\n'), "gpt-reserve")


class Allocation(_Case):
    def snapshot(self, claude_percent=20.0) -> dict:
        claude = {"source": "claude", "state": "ok", "windows": [
            {"kind": "five_hour", "percent": claude_percent, "trusted": True,
             "rolledOver": False, "resetUnknown": False}]}
        codex = usage.read_codex(NOW, app_server=self.answered())
        usage._reset_codex_state()
        return {"state": "ready", "warnPercent": 80, "alarmPercent": 95,
                "sources": [claude, codex]}

    def choose(self, snap, **kw) -> dict:
        return dashboard.choose_agent_kind_for_seat(
            "codex", snap, installed=lambda kind: True, **kw)

    def test_codex_on_the_reserve_counts_as_available(self):
        snap = self.snapshot()
        figure = dashboard._kind_usage(snap, "codex")
        self.assertEqual((figure["state"], figure["percent"], figure["pool"], figure["poolLabel"]),
                         ("known", 0.0, "base_model_inference", "reserve"))
        decision = self.choose(snap)
        self.assertEqual((decision["chosenKind"], decision["decision"]),
                         ("codex", "preferred_below_warning"))

    def test_codex_on_the_main_pool_does_not(self):
        self.config_model = "gpt-6-astra"
        snap = self.snapshot()
        figure = dashboard._kind_usage(snap, "codex")
        self.assertEqual((figure["percent"], figure["pool"], figure["poolLabel"]),
                         (97.0, "codex", ""))
        decision = self.choose(snap)
        self.assertEqual((decision["chosenKind"], decision["decision"]),
                         ("claude", "switch_warning"))

    def test_the_seat_s_named_model_beats_the_config(self):
        self.config_model = "gpt-6-astra"
        snap = self.snapshot()
        self.assertEqual(self.choose(snap, codex_model="gpt-reserve")["chosenKind"], "codex")
        self.config_model = "gpt-reserve"
        snap = self.snapshot()
        self.assertEqual(self.choose(snap, codex_model="gpt-5.6-sol")["chosenKind"], "claude")
        spark = dashboard._kind_usage(snap, "codex", "gpt-5.3-codex-spark")
        self.assertEqual((spark["pool"], spark["percent"]), ("codex_bengalfox", 0.0))

    def test_a_reserve_nobody_has_read_is_unknown_never_the_main_figure(self):
        files = [self.rollout(_turn("gpt-5.6-sol"), _count(NOW - 60, 97, MAIN_RESET))]
        snap = self.snapshot()
        snap["sources"][1] = usage.read_codex(NOW, files=files)
        figure = dashboard._kind_usage(snap, "codex")
        self.assertEqual((figure["state"], figure["pool"]), ("unknown", "base_model_inference"))
        self.assertEqual(self.choose(snap)["decision"], "unknown")

    def test_first_launch_judges_the_owner_s_codex_model(self):
        self.config_model = "gpt-6-astra"
        snap = self.snapshot()
        seats = [{"agent": "codex", "model": "gpt-reserve", "role": "engineer"},
                 {"agent": "claude", "model": "", "role": "reviewer"}]
        chosen, record = dashboard.choose_first_launch_allocation(
            seats, snap, installed=lambda kind: True)
        self.assertFalse(record["changed"])
        self.assertIn("Codex reserve pool 7-day window at 0%", record["reason"])
        seats[0]["model"] = ""
        chosen, record = dashboard.choose_first_launch_allocation(
            seats, snap, installed=lambda kind: True)
        self.assertEqual([s["agent"] for s in chosen], ["claude", "codex"])
        # A Claude owner whose seat names a Codex alternative is judged by that.
        hot = self.snapshot(claude_percent=90.0)
        seats = [{"agent": "claude", "model": "", "role": "engineer",
                  "alt": {"agent": "codex", "model": "gpt-reserve"}},
                 {"agent": "codex", "model": "", "role": "reviewer"}]
        chosen, record = dashboard.choose_first_launch_allocation(
            seats, hot, installed=lambda kind: True)
        self.assertEqual((chosen[0]["agent"], chosen[0]["model"]), ("codex", "gpt-reserve"))

    def test_an_owner_handover_judges_the_same_pool(self):
        snap = self.snapshot()
        with mock.patch.object(dashboard.agents, "get_agent", return_value=None):
            kept = rotation.choose_owner_kind({}, {"agent": "codex", "model": ""}, snap,
                                              installed=lambda kind: True)
            moved = rotation.choose_owner_kind({}, {"agent": "codex", "model": "gpt-5.6-sol"},
                                               snap, installed=lambda kind: True)
        self.assertEqual((kept["agent"], kept["changed"]), ("codex", False))
        self.assertEqual((moved["agent"], moved["changed"]), ("claude", True))
        self.assertIn("97%", moved["why"])

    def test_the_plan_usage_tool_gives_the_figure_the_hub_goes_by(self):
        snap = self.snapshot()
        snap["notices"] = usage.notices(snap["sources"])
        with mock.patch.object(usage, "snapshot", return_value=snap):
            out = ensemble_tools._plan_usage({}, {}, None)
        self.assertEqual((out["kinds"]["codex"]["percent"], out["kinds"]["codex"]["poolLabel"]),
                         (0.0, "reserve"))
        self.assertEqual(out["kinds"]["claude"]["percent"], 20.0)
        self.assertIn("poolInUse", out["note"])


if __name__ == "__main__":
    unittest.main()
