"""The model hub-launched agents run on, chosen in Settings (#145).

* what can be chosen: Codex's own model list (the ones its picker lists, and a
  model with a pool of its own), Claude's aliases, and each agent's own default
  as it is now;
* the setting: saved per agent kind, an unknown model or an effort the model
  does not take refused (the hub answers 400 and keeps what it had), the older
  ``defaultModel`` one and the same as Claude's model;
* the launch arguments of every seat kind the hub starts (a task's owner, its
  reviewer, a PO, a resume, an owner handover, a PO switch): the seat's own
  model wins, else the one chosen, else no flag at all; a conversation brought
  in from the history is resumed as it was, and every session the hub starts
  in its room afterwards takes the setting like any other;
* the pool Codex is shown and judged by follows the model chosen.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_models  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import rotation  # noqa: E402
import usage  # noqa: E402

LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")


def _model(slug, name, priority, levels=LEVELS, visibility="list", default="medium"):
    return {"slug": slug, "display_name": name, "description": f"{name} in a line.",
            "default_reasoning_level": default, "visibility": visibility, "priority": priority,
            "supported_reasoning_levels": [{"effort": e, "description": e} for e in levels]}


CACHE = {"fetched_at": "2026-09-30T12:39:02Z", "models": [
    _model("gpt-5.5", "GPT-5.5", 13, LEVELS[:4]),
    _model("gpt-6-astra", "GPT-6-Astra", 2),
    _model("gpt-6-sol", "GPT-6-Sol", 3),
    _model("gpt-6-luna", "GPT-6-Luna", 4, LEVELS[:5]),
    _model("gpt-reserve", "GPT-Reserve", 4, LEVELS[:5], visibility="hide"),
    _model("codex-auto-review", "Codex Auto Review", 43, LEVELS[:5], visibility="hide"),
]}
CONFIG = 'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n\n[projects.x]\ntrust_level = "trusted"\n'


class _Home(unittest.TestCase):
    """A Codex home with a model list and a config, and a hub state dir of its
    own: nothing of this machine's is read or written."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ens-models-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.codex = self.tmp / "codex"
        self.codex.mkdir()
        (self.codex / "models_cache.json").write_text(json.dumps(CACHE), encoding="utf-8")
        (self.codex / "config.toml").write_text(CONFIG, encoding="utf-8")
        (self.tmp / "claude.json").write_text('{"model": "claude-fable-5-1[1m]"}', encoding="utf-8")
        own = agent_models.claude_own
        for p in (mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex)}),
                  mock.patch.object(dashboard, "DASHBOARD_DIR", self.tmp),
                  mock.patch.object(dashboard, "SETTINGS_FILE", self.tmp / "settings.json"),
                  mock.patch.object(agent_models, "claude_own",
                                    side_effect=lambda path=None: own(path or self.tmp / "claude.json"))):
            p.start()
            self.addCleanup(p.stop)

    def choose(self, **kinds) -> dict:
        return dashboard.save_settings({"agentModels": kinds})["agentModels"]


class WhatCanBeChosen(_Home):
    def test_codex_offers_what_its_picker_lists_and_the_model_with_a_pool_of_its_own(self):
        models = agent_models.codex_models()
        self.assertEqual([m["id"] for m in models],
                         ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-reserve", "gpt-5.5"])
        by = {m["id"]: m for m in models}
        self.assertEqual((by["gpt-reserve"]["pool"], by["gpt-6-sol"]["pool"]), ("reserve", None))
        self.assertEqual(by["gpt-6-luna"]["efforts"], list(LEVELS[:5]))
        self.assertEqual(by["gpt-6-sol"]["description"], "GPT-6-Sol in a line.")

    def test_no_model_list_is_said_not_guessed(self):
        (self.codex / "models_cache.json").unlink()
        self.assertIsNone(agent_models.codex_models())
        (self.codex / "models_cache.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(agent_models.codex_models())
        info = agent_models.describe(None)["codex"]
        self.assertEqual((info["readable"], info["models"]), (False, []))

    def test_the_list_is_read_again_only_when_the_file_changes(self):
        agent_models.codex_models()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("read again")):
            self.assertEqual(len(agent_models.codex_models()), 5)
        less = {"models": CACHE["models"][:2]}
        (self.codex / "models_cache.json").write_text(json.dumps(less) + " ", encoding="utf-8")
        self.assertEqual([m["id"] for m in agent_models.codex_models()], ["gpt-6-astra", "gpt-5.5"])

    def test_each_agent_s_own_default_is_told_as_it_is_now(self):
        info = agent_models.describe(None)
        self.assertEqual(info["codex"]["own"], {"model": "gpt-6-astra", "effort": "high"})
        self.assertEqual(info["claude"]["own"], {"model": "claude-fable-5-1[1m]"})
        self.assertEqual([m["id"] for m in info["claude"]["models"]],
                         ["fable", "opus", "sonnet", "haiku"])
        self.assertEqual(info["codex"]["effective"],
                         {"model": "gpt-6-astra", "effort": "high", "efforts": list(LEVELS), "pool": None})
        (self.tmp / "claude.json").write_text("{}", encoding="utf-8")
        (self.codex / "config.toml").unlink()
        info = agent_models.describe(None)
        self.assertEqual((info["claude"]["own"], info["codex"]["own"]),
                         ({"model": ""}, {"model": "", "effort": ""}))

    def test_what_a_chosen_model_runs_on_and_draws_on(self):
        self.choose(codex={"model": "gpt-reserve", "effort": "max"}, claude={"model": "opus"})
        info = dashboard.agent_models_info()
        self.assertEqual(info["codex"]["effective"],
                         {"model": "gpt-reserve", "effort": "max", "efforts": list(LEVELS[:5]),
                          "pool": "reserve"})
        self.assertEqual(info["codex"]["chosen"], {"model": "gpt-reserve", "effort": "max"})
        self.assertEqual(info["claude"]["effective"], {"model": "opus"})
        self.assertEqual(info["codex"]["poSwitchModel"], "")

    def test_a_po_switched_to_codex_keeps_the_older_fallback_until_a_model_is_chosen(self):
        self.assertEqual(dashboard.agent_models_info()["codex"]["poSwitchModel"],
                         rotation.DEFAULT_PO_FALLBACK_MODELS["codex"])
        self.assertEqual(dashboard.agent_models_info()["claude"]["poSwitchModel"], "")

    def test_the_agents_own_files_are_never_written(self):
        before = {p: p.read_bytes() for p in self.codex.iterdir()}
        before[self.tmp / "claude.json"] = (self.tmp / "claude.json").read_bytes()
        self.choose(codex={"model": "gpt-6-sol", "effort": "xhigh"}, claude={"model": "sonnet"})
        dashboard.agent_models_info()
        dashboard.hub_launch_model("codex")
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(sorted(p.name for p in self.codex.iterdir()),
                         ["config.toml", "models_cache.json"])


class TheSetting(_Home):
    def saved(self) -> dict:
        return json.loads((self.tmp / "settings.json").read_text(encoding="utf-8"))

    def test_nothing_is_chosen_until_someone_does(self):
        s = dashboard.load_settings()
        self.assertEqual(s["agentModels"], {"claude": {"model": ""}, "codex": {"model": "", "effort": ""}})
        self.assertEqual(s["defaultModel"], "")
        self.assertEqual(dashboard.hub_launch_model("codex"), ("", ""))
        self.assertEqual(dashboard.hub_launch_model("claude"), ("", ""))

    def test_a_choice_is_kept_one_field_at_a_time(self):
        self.choose(codex={"model": "gpt-6-sol"})
        self.choose(codex={"effort": "ultra"})
        got = self.choose(claude={"model": "Opus"})
        self.assertEqual(got, {"claude": {"model": "opus"}, "codex": {"model": "gpt-6-sol", "effort": "ultra"}})
        self.assertEqual(self.saved()["agentModels"], got)
        self.assertEqual(dashboard.load_settings()["agentModels"], got)
        self.assertEqual(self.choose(codex={"model": ""})["codex"], {"model": "", "effort": "ultra"})

    def test_an_unknown_model_is_refused_on_save(self):
        self.choose(codex={"model": "gpt-6-sol"}, claude={"model": "opus"})
        for change, word in (({"codex": {"model": "gpt-7-nova"}}, "Codex offers no model"),
                             ({"codex": {"model": "codex-auto-review"}}, "Codex offers no model"),
                             ({"claude": {"model": "gpt-6-sol"}}, "Claude has no model"),
                             ({"claude": {"model": "opus; rm -rf"}}, "Claude has no model"),
                             ({"codex": {"model": 7}}, "Expected"),
                             ({"gemini": {"model": "pro"}}, "Expected"),
                             ("gpt-6-sol", "Expected")):
            with self.subTest(change=change):
                self.assertIn(word, dashboard.agent_models_error({"agentModels": change}))
                got = dashboard.save_settings({"agentModels": change})["agentModels"]
                self.assertEqual(got, {"claude": {"model": "opus"},
                                       "codex": {"model": "gpt-6-sol", "effort": ""}})
        self.assertEqual(dashboard.agent_models_error({"agentModels": {"codex": {"model": "gpt-6-luna"}}}), "")
        self.assertEqual(dashboard.agent_models_error({"theme": "dark"}), "")

    def test_the_hub_answers_400_and_says_why(self):
        def put(body):
            raw = json.dumps(body).encode()
            h = dashboard.Handler.__new__(dashboard.Handler)
            h.path, h.command, h.request_version = "/api/settings", "PUT", "HTTP/1.1"
            h.requestline = "PUT /api/settings HTTP/1.1"
            h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                         "Host": "127.0.0.1:8791"}
            h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
            h.client_address = ("127.0.0.1", 50000)
            h.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
            h.log_message = lambda *a: None
            h.do_PUT()
            head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
            return int(head.split(b" ", 2)[1]), json.loads(payload)

        with mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""):
            status, got = put({"agentModels": {"codex": {"model": "gpt-6-sol", "effort": "high"}}})
            self.assertEqual((status, got["agentModels"]["codex"]), (200, {"model": "gpt-6-sol", "effort": "high"}))
            # Nothing of a refused change is saved, not even its good half.
            status, got = put({"theme": "dark", "agentModels": {"codex": {"model": "gpt-7-nova"}}})
            self.assertEqual((status, got["error"]), (400, "unknown_model"))
            self.assertIn("gpt-7-nova", got["message"])
            s = dashboard.load_settings()
            self.assertEqual((s["theme"], s["agentModels"]["codex"]["model"]), ("", "gpt-6-sol"))

    def test_an_effort_is_one_the_model_takes(self):
        self.choose(codex={"model": "gpt-6-luna"})
        self.assertIn("does not take the reasoning effort",
                      dashboard.agent_models_error({"agentModels": {"codex": {"effort": "ultra"}}}))
        self.assertEqual(self.choose(codex={"effort": "ultra"})["codex"]["effort"], "")
        self.assertEqual(self.choose(codex={"effort": "max"})["codex"]["effort"], "max")
        # With Codex's own default model, the efforts are that model's.
        self.assertEqual(self.choose(codex={"model": "", "effort": "ultra"})["codex"],
                         {"model": "", "effort": "ultra"})
        self.assertEqual(self.choose(codex={"model": "gpt-5.5"})["codex"]["model"], "gpt-5.5")
        self.assertEqual(self.choose(codex={"effort": "xhigh"})["codex"]["effort"], "xhigh")
        self.assertEqual(self.choose(codex={"model": "gpt-6-sol"})["codex"], {"model": "gpt-6-sol", "effort": "xhigh"})
        # Both named together, and they do not go together: refused whole.
        self.assertEqual(dashboard.agent_models_error(
            {"agentModels": {"codex": {"model": "gpt-5.5", "effort": "max"}}}),
            "gpt-5.5 does not take the reasoning effort “max”: it takes low, medium, high, xhigh.")

    def test_going_through_the_model_list_never_loses_the_effort(self):
        """Arrow keys on the model list save one model a step. The effort
        chosen before is kept past a model that does not take it: left out
        for that one, passed again to one that does."""
        self.choose(codex={"model": "gpt-6-sol", "effort": "ultra"})
        for model, passed, in_effect in (("gpt-6-luna", "", "high"), ("gpt-reserve", "", "high"),
                                         ("gpt-6-luna", "", "high"), ("gpt-6-sol", "ultra", "ultra")):
            with self.subTest(model=model):
                self.assertEqual(self.choose(codex={"model": model})["codex"],
                                 {"model": model, "effort": "ultra"})
                self.assertEqual(dashboard.hub_launch_model("codex"), (model, passed))
                info = dashboard.agent_models_info()["codex"]
                self.assertEqual((info["chosen"]["effort"], info["effective"]["effort"]),
                                 ("ultra", in_effect))
        # Naming it for a model that does not take it is still refused.
        self.choose(codex={"model": "gpt-6-luna"})
        self.assertIn("gpt-5.5 does not take the reasoning effort “ultra”", dashboard.agent_models_error(
            {"agentModels": {"codex": {"model": "gpt-5.5", "effort": "ultra"}}}))
        self.assertEqual(self.choose(codex={"effort": "max"})["codex"]["effort"], "max")
        self.assertIn("gpt-6-luna does not take the reasoning effort “ultra”",
                      dashboard.agent_models_error({"agentModels": {"codex": {"effort": "ultra"}}}))
        self.choose(codex={"model": "gpt-6-sol", "effort": "ultra"})
        self.choose(codex={"model": "gpt-6-luna"})
        # Left as it is while something else changes: never refused.
        self.assertEqual(self.choose(claude={"model": "opus"})["codex"],
                         {"model": "gpt-6-luna", "effort": "ultra"})

    def test_an_effort_needs_a_model_the_list_tells_about(self):
        """A stock Codex names no model in its config (or names one its picker
        hides): the hub cannot tell what that model takes, and says that, not
        that the model does not take it."""
        for config in ("", 'model = "codex-auto-review"\n'):
            with self.subTest(config=config):
                (self.codex / "config.toml").write_text(config, encoding="utf-8")
                why = dashboard.agent_models_error({"agentModels": {"codex": {"effort": "high"}}})
                self.assertIn("cannot tell which reasoning efforts Codex’s own default model takes", why)
                self.assertIn("choose a Codex model from the list first", why)
                self.assertEqual(dashboard.agent_models_info()["codex"]["effective"]["efforts"], [])
                # With a model from the list, its efforts can be chosen.
                self.assertEqual(self.choose(codex={"model": "gpt-6-sol", "effort": "high"})["codex"],
                                 {"model": "gpt-6-sol", "effort": "high"})
                self.choose(codex={"model": "", "effort": ""})

    def test_no_model_list_no_codex_choice(self):
        (self.codex / "models_cache.json").unlink()
        self.assertIn("cannot be read",
                      dashboard.agent_models_error({"agentModels": {"codex": {"model": "gpt-6-sol"}}}))
        self.assertEqual(self.choose(codex={"model": "gpt-6-sol"})["codex"]["model"], "")
        self.assertEqual(dashboard.agent_models_error({"agentModels": {"codex": {"effort": "high"}}}),
                         "Codex's model list cannot be read, so a reasoning effort cannot be chosen.")
        self.assertEqual(self.choose(claude={"model": "haiku"})["claude"]["model"], "haiku")

    def test_claude_takes_an_alias_or_a_model_id(self):
        for model, kept in (("fable", "fable"), ("opus[1m]", "opus[1m]"), (" Sonnet ", "sonnet"),
                            ("claude-opus-5-5", "claude-opus-5-5"),
                            ("claude-fable-5-1[1m]", "claude-fable-5-1[1m]"), ("", "")):
            with self.subTest(model=model):
                self.assertEqual(self.choose(claude={"model": model})["claude"]["model"], kept)

    # --- the older defaultModel: one Claude setting, not two ------------------

    def test_a_default_model_saved_before_is_claude_s_model(self):
        (self.tmp / "settings.json").write_text('{"openMode": "tab", "defaultModel": "opus"}', encoding="utf-8")
        s = dashboard.load_settings()
        self.assertEqual((s["agentModels"]["claude"]["model"], s["defaultModel"]), ("opus", "opus"))
        self.assertEqual(dashboard.hub_launch_model("claude"), ("opus", ""))
        # Any later save writes it under both names, in step.
        dashboard.save_settings({"openMode": "window"})
        self.assertEqual((self.saved()["agentModels"]["claude"]["model"], self.saved()["defaultModel"]),
                         ("opus", "opus"))

    def test_a_value_the_old_free_text_field_saved_keeps_working(self):
        (self.tmp / "settings.json").write_text('{"defaultModel": "opusplan"}', encoding="utf-8")
        self.assertEqual(dashboard.hub_launch_model("claude"), ("opusplan", ""))
        # Changing something else does not refuse what was there.
        got = self.choose(codex={"model": "gpt-6-sol"})
        self.assertEqual(got["claude"]["model"], "opusplan")
        self.assertEqual(dashboard.agent_models_error({"agentModels": {"claude": {"model": "opusplan"}}}), "")

    def test_the_two_names_never_disagree(self):
        self.assertEqual(dashboard.save_settings({"defaultModel": "sonnet"})["agentModels"]["claude"]["model"],
                         "sonnet")
        s = dashboard.save_settings({"agentModels": {"claude": {"model": "haiku"}}})
        self.assertEqual((s["defaultModel"], s["agentModels"]["claude"]["model"]), ("haiku", "haiku"))
        s = dashboard.save_settings({"defaultModel": ""})
        self.assertEqual((s["defaultModel"], s["agentModels"]["claude"]["model"]), ("", ""))
        self.assertIn("Claude has no model", dashboard.agent_models_error({"defaultModel": "gpt-6"}))
        self.assertEqual(dashboard.save_settings({"defaultModel": "gpt-6"})["defaultModel"], "")
        # A file an older hub wrote after this one: agentModels says Claude's model.
        (self.tmp / "settings.json").write_text(json.dumps(
            {"defaultModel": "opus", "agentModels": {"claude": {"model": "fable"}}}), encoding="utf-8")
        s = dashboard.load_settings()
        self.assertEqual((s["defaultModel"], s["agentModels"]["claude"]["model"]), ("fable", "fable"))


class _Agent:
    def __init__(self, kind):
        self.display_name = kind.title()

    def installed(self):
        return True

    def launch_argv(self, cwd, prompt="", extra=None):
        return [self.display_name.lower()]


class LaunchArguments(_Home):
    """What the hub hands ``codex`` and ``claude`` when it starts each seat."""

    def setUp(self):
        super().setUp()
        self.made: list[list[str]] = []

        def create(cmd, cwd=None, env=None, label="", meta=None, **kw):
            self.made.append(list(cmd))
            return types.SimpleNamespace(id="pty-new")
        for p in (mock.patch.object(dashboard, "load_projects",
                                    return_value=[{"id": "p1", "poRoomId": "room-po"}]),
                  mock.patch.object(dashboard, "_rtk_task_wiring", return_value=([], {}, "")),
                  mock.patch.object(dashboard.agents, "get_agent", side_effect=_Agent),
                  mock.patch.object(dashboard.BACKEND, "headless_launch",
                                    side_effect=lambda cwd, argv, prompt: argv),
                  mock.patch.object(dashboard.ptyrun, "create", side_effect=create)):
            p.start()
            self.addCleanup(p.stop)
        self.handler = dashboard.Handler.__new__(dashboard.Handler)
        self.handler.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))

    def room(self, kind, rid="room-t", role="engineer", model="", **room):
        part = {"identity": "a", "agent": kind, "kind": "agent", "sessionId": "", "model": model,
                "cwd": str(self.tmp), "role": role}
        return {"id": rid, "title": "t", "cwd": str(self.tmp), "sharedCwd": True, "launched": True,
                "tokens": {"tok": "a"}, "participants": [part], **room}, part

    def flags(self, argv) -> dict:
        """The model and effort an argv carries: {model, effort}, None for no flag."""
        out = {"model": None, "effort": None}
        for i, a in enumerate(argv):
            if a == "--model":
                out["model"] = argv[i + 1]
            for key, name in (("model=", "model"), ("model_reasoning_effort=", "effort")):
                if a.startswith(key) and argv[i - 1] == "-c":
                    out[name] = json.loads(a[len(key):])
        return out

    def seats(self, kind, model=""):
        """One launch per seat kind the hub starts; returns {seat: argv}."""
        self.made.clear()
        h = self.handler
        room, part = self.room(kind, model=model)
        h._launch_room_agent_pty(room, part, "do it", collab=True)
        room, part = self.room(kind, role="reviewer", model=model)
        h._launch_room_agent_pty(room, part, "", collab=True, prompt="review this")
        room, part = self.room(kind, "room-po", "ProductOwner", model=model)
        h._launch_room_agent_pty(room, part, "watch the project", collab=False)
        room, part = self.room(kind, model=model)
        part["sessionId"] = "0199-sid"
        h._resume_room_agent_pty(room, part, collab=True)
        # An owner handover and a PO switch start a fresh session of the seat
        # as rotation.py hands it over: the same seat, a first prompt, a cwd.
        room, part = self.room(kind, model=model)
        h._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=True,
                                 prompt="carry on from TASK-HANDOVER.md", cwd=str(self.tmp))
        room, part = self.room(kind, "room-po", "ProductOwner", model=model)
        h._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=False,
                                 prompt="carry on from PO-HANDOVER.md", cwd=str(self.tmp))
        names = ("owner", "reviewer", "po", "resume", "handover", "po switch")
        self.assertEqual(len(self.made), len(names))
        return dict(zip(names, self.made))

    def test_nothing_chosen_no_flag_and_no_file_read(self):
        with mock.patch.object(agent_models, "codex_models", side_effect=AssertionError("read")):
            for kind in ("codex", "claude"):
                for seat, argv in self.seats(kind).items():
                    with self.subTest(kind=kind, seat=seat):
                        self.assertEqual(self.flags(argv), {"model": None, "effort": None})

    def test_every_codex_seat_runs_on_the_model_and_effort_chosen(self):
        self.choose(codex={"model": "gpt-6-sol", "effort": "xhigh"})
        for seat, argv in self.seats("codex").items():
            with self.subTest(seat=seat):
                self.assertEqual(argv[0], "codex")
                self.assertEqual(self.flags(argv), {"model": "gpt-6-sol", "effort": "xhigh"})
                self.assertEqual(argv.count('model="gpt-6-sol"'), 1)
        resume = self.made[3]
        self.assertEqual(resume[-2:], ["resume", "0199-sid"])
        self.assertLess(resume.index('model_reasoning_effort="xhigh"'), resume.index("resume"))

    def test_every_claude_seat_runs_on_the_model_chosen(self):
        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "xhigh"})
        for seat, argv in self.seats("claude").items():
            with self.subTest(seat=seat):
                self.assertEqual(self.flags(argv), {"model": "opus", "effort": None})
                self.assertEqual(argv.count("--model"), 1)

    def test_a_seat_that_names_a_model_keeps_it(self):
        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "ultra"})
        for seat, argv in self.seats("codex", "gpt-6-astra").items():
            with self.subTest(seat=seat):
                self.assertEqual(self.flags(argv), {"model": "gpt-6-astra", "effort": "ultra"})
                self.assertNotIn('model="gpt-6-sol"', argv)
        for seat, argv in self.seats("claude", "sonnet").items():
            with self.subTest(seat=seat):
                self.assertEqual(self.flags(argv), {"model": "sonnet", "effort": None})
                self.assertNotIn("opus", argv)

    def test_the_effort_is_only_passed_to_a_model_known_to_take_it(self):
        self.choose(codex={"model": "gpt-6-sol", "effort": "ultra"})
        for model in ("gpt-6-luna", "gpt-5.3-codex-spark"):     # no ultra; not in the list
            with self.subTest(model=model):
                self.assertEqual(self.flags(self.seats("codex", model)["owner"]),
                                 {"model": model, "effort": None})
        # Only an effort chosen: Codex's own default model, which takes it.
        self.choose(codex={"model": "", "effort": "max"})
        self.assertEqual(self.flags(self.seats("codex")["reviewer"]), {"model": None, "effort": "max"})

    def test_a_chosen_model_codex_no_longer_lists_is_not_passed(self):
        self.choose(codex={"model": "gpt-6-luna", "effort": "max"})
        gone = {"models": [m for m in CACHE["models"] if m["slug"] != "gpt-6-luna"]}
        (self.codex / "models_cache.json").write_text(json.dumps(gone) + "\n", encoding="utf-8")
        self.assertEqual(self.flags(self.seats("codex")["owner"]), {"model": None, "effort": "max"})
        info = dashboard.agent_models_info()["codex"]
        self.assertEqual((info["gone"], info["effective"]["model"]), (True, "gpt-6-astra"))
        # No list at all: what was chosen is passed as it was saved.
        (self.codex / "models_cache.json").unlink()
        self.assertEqual(self.flags(self.seats("codex")["owner"]), {"model": "gpt-6-luna", "effort": None})

    # --- a room made from a past session (adopted) ----------------------------

    def test_a_conversation_brought_in_from_the_history_is_resumed_as_it_was(self):
        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "high"})
        for kind in ("codex", "claude"):
            with self.subTest(kind=kind):
                self.made.clear()
                room, part = self.room(kind, adopted=True)
                part["sessionId"] = "0199-sid"
                self.handler._resume_room_agent_pty(room, part, collab=False)
                room.pop("adopted")
                self.handler._resume_room_agent_pty(room, part, collab=False, human=True)
                for argv in self.made:
                    self.assertEqual(self.flags(argv), {"model": None, "effort": None})

    def test_every_session_the_hub_starts_in_such_a_room_takes_the_setting(self):
        """A PO made from a past conversation lives in an adopted room for
        good: its handovers, its switch to the other kind and a reviewer
        started in an adopted task are the hub's own sessions."""
        chosen = {"codex": {"model": "gpt-6-sol", "effort": "high"},
                  "claude": {"model": "opus", "effort": None}}
        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "high"})
        for kind in ("codex", "claude"):
            with self.subTest(kind=kind):
                self.made.clear()
                room, part = self.room(kind, "room-po", "ProductOwner", adopted=True)
                part["sessionId"] = "0199-adopted"
                # A PO handover: a fresh session of the same kind.
                self.handler._launch_room_agent_pty(room, {**part, "sessionId": ""}, "", collab=False,
                                                    prompt="carry on from PO-HANDOVER.md")
                # A PO switch or failover to this kind: the seat names none.
                model = rotation._po_fallback_model(room, kind)
                self.assertEqual(model, "")
                self.handler._launch_room_agent_pty(
                    room, {**part, "agent": kind, "model": model, "sessionId": ""}, "", collab=False,
                    prompt="carry on from PO-HANDOVER.md")
                # A reviewer on mention in an adopted task.
                task, reviewer = self.room(kind, role="reviewer", adopted=True)
                self.handler._launch_room_agent_pty(task, reviewer, "", collab=True, prompt="review this")
                # The fresh session, resumed later (a hub restart): it is the
                # hub's own, recorded as a rotation of the seat, so it follows
                # Settings; only the conversation as it was found does not.
                part.update(sessionId="0199-fresh", rotations=[
                    {"n": 1, "fromSessionId": "0199-adopted", "toSessionId": "0199-fresh"}])
                self.handler._resume_room_agent_pty(room, part, collab=False)
                self.assertEqual(len(self.made), 4)
                for argv in self.made:
                    self.assertEqual(self.flags(argv), chosen[kind], argv)
                self.assertEqual(rotation._model_name(kind, model), chosen[kind]["model"])

    def test_a_seat_added_to_an_adopted_task_is_the_hub_s_own_when_resumed_too(self):
        """The agents of a past session brought in as a task are edited: the
        added seat is started by the hub (no rotation says so), and resumed
        after a Stop or a hub restart it must not fall back to the agent's own
        default; the conversation that was found stays as it was."""
        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "high"})
        for p in (mock.patch.object(chatroom, "ROOMS_DIR", self.tmp / "rooms"),
                  mock.patch.object(dashboard, "_pty_alive", return_value=False),
                  mock.patch.object(dashboard.Handler, "_deliver_after_resume")):
            p.start()
            self.addCleanup(p.stop)
        rid = chatroom.create_room("a past session", [
            {"identity": "claude", "agent": "claude", "model": ""}])["id"]
        room = chatroom.get_room(rid, public=False)
        room.update(cwd=str(self.tmp), sharedCwd=True, mode="solo", adopted=True)
        found = chatroom.participant(room, "claude")
        found.update(sessionId="0199-found", cwd=str(self.tmp))
        chatroom.update_room(room)
        # As chatroom.set_agents adds one: no conversation yet, marked fresh.
        chatroom.set_agents(rid, [{"identity": "claude", "agent": "claude", "model": ""},
                                  {"agent": "codex", "model": "", "role": ""},
                                  {"agent": "claude", "model": "", "role": ""}])
        room = chatroom.get_room(rid, public=False)
        added = [p["identity"] for p in chatroom.agent_participants(room) if p.get("fresh")]
        self.assertEqual(len(added), 2)
        chosen = {"codex": {"model": "gpt-6-sol", "effort": "high"},
                  "claude": {"model": "opus", "effort": None}}
        for run in ("first start", "resumed"):
            self.made.clear()
            room = chatroom.get_room(rid, public=False)
            for part in chatroom.agent_participants(room):
                part.pop("ptyId", None)                 # the task was stopped
            started = self.handler._start_or_resume_room_now(room)
            self.assertEqual(len(started), 3, run)
            flags = {}
            for argv in self.made:
                kind = "codex" if argv[0] == "codex" else "claude"
                resumes = "resume" in argv or "--resume" in argv
                flags[(kind, "0199-found" in argv)] = (self.flags(argv), resumes)
            with self.subTest(run=run):
                # The conversation as it was found: resumed, nothing passed.
                self.assertEqual(flags[("claude", True)], ({"model": None, "effort": None}, True))
                # The added seats: the setting at their first start and at every resume.
                self.assertEqual(flags[("codex", False)][0], chosen["codex"])
                self.assertEqual(flags[("claude", False)][0], chosen["claude"])
                self.assertEqual(flags[("claude", False)][1], run == "resumed")
        saved = chatroom.get_room(rid, public=False)
        self.assertEqual({p["identity"]: bool(p.get("hubStarted")) for p in chatroom.agent_participants(saved)},
                         {"claude": False, **{ident: True for ident in added}})

    def test_a_failover_in_an_adopted_po_room_with_nothing_chosen_keeps_the_older_fallback(self):
        room, part = self.room("claude", "room-po", "ProductOwner", adopted=True)
        model = rotation._po_fallback_model(room, "codex")
        self.assertEqual(model, "gpt-5.6-sol")
        self.handler._launch_room_agent_pty(room, {**part, "agent": "codex", "model": model}, "",
                                            collab=False, prompt="carry on")
        self.assertEqual(self.flags(self.made[-1]), {"model": "gpt-5.6-sol", "effort": None})

    def test_a_chosen_model_codex_no_longer_lists_does_not_put_a_failover_on_codex_s_default(self):
        self.choose(codex={"model": "gpt-6-luna"})
        gone = {"models": [m for m in CACHE["models"] if m["slug"] != "gpt-6-luna"]}
        (self.codex / "models_cache.json").write_text(json.dumps(gone) + "\n", encoding="utf-8")
        room, part = self.room("claude", "room-po", "ProductOwner")
        self.assertEqual(dashboard.hub_launch_model("codex")[0], "")
        self.assertEqual(rotation._po_fallback_model(room, "codex"), "gpt-5.6-sol")
        self.assertEqual(rotation._model_name("codex", "gpt-5.6-sol"), "gpt-5.6-sol")

    # --- who decides the seat's model before the launch ----------------------

    def snap(self, claude, codex) -> dict:
        def win(p):
            return {"kind": "five_hour", "percent": p, "trusted": True, "rolledOver": False,
                    "resetUnknown": False}
        return {"warnPercent": 80, "alarmPercent": 95, "checkedAt": 1.0, "sources": [
            {"source": "claude", "state": "ok", "windows": [win(claude)]},
            {"source": "codex", "state": "ok", "windows": [win(codex)]}]}

    def test_an_owner_handed_to_the_other_kind_runs_on_that_kind_s_chosen_model(self):
        self.choose(codex={"model": "gpt-6-sol"}, claude={"model": "opus"})
        room, part = self.room("claude")
        choice = rotation.choose_owner_kind(room, part, self.snap(90, 10), installed=lambda k: True)
        self.assertEqual((choice["agent"], choice["model"], choice["changed"]), ("codex", "", True))
        self.handler._launch_room_agent_pty(
            room, {**part, "agent": choice["agent"], "model": choice["model"]}, "", prompt="carry on")
        self.assertEqual(self.flags(self.made[-1]), {"model": "gpt-6-sol", "effort": None})
        # A seat that names the other kind's model keeps it there too.
        room["agentPreference"] = [{"agent": "claude", "model": "", "role": "engineer",
                                    "alt": {"agent": "codex", "model": "gpt-6-luna"}}]
        choice = rotation.choose_owner_kind(room, part, self.snap(90, 10), installed=lambda k: True)
        self.assertEqual(choice["model"], "gpt-6-luna")

    def test_a_po_switch_runs_on_the_chosen_model_and_the_seat_follows_settings(self):
        room, part = self.room("claude", "room-po", "ProductOwner")
        # Nothing chosen: the older fallback, as before.
        self.assertEqual(rotation._po_fallback_model(room, "codex"), "gpt-5.6-sol")
        self.assertEqual(rotation._po_fallback_model(room, "claude"), "")
        self.choose(codex={"model": "gpt-6-sol", "effort": "high"}, claude={"model": "opus"})
        for kind, flags in (("codex", {"model": "gpt-6-sol", "effort": "high"}),
                            ("claude", {"model": "opus", "effort": None})):
            with self.subTest(kind=kind):
                model = rotation._po_fallback_model(room, kind)
                self.assertEqual(model, "", "the seat names none, so it follows Settings")
                self.handler._launch_room_agent_pty(room, {**part, "agent": kind, "model": model},
                                                    "", collab=False, prompt="carry on")
                self.assertEqual(self.flags(self.made[-1]), flags)
                self.assertEqual(rotation._model_name(kind, model), flags["model"])
        # The seat's own alternative model still wins.
        room["agentPreference"] = [{"agent": "claude", "model": "", "role": "ProductOwner",
                                    "alt": {"agent": "codex", "model": "gpt-6-luna"}}]
        self.assertEqual(rotation._po_fallback_model(room, "codex"), "gpt-6-luna")

    def test_a_new_session_in_a_terminal(self):
        """POST /api/new: no ``model`` is the one chosen in Settings, an empty
        one is no flag at all, a named one is kept; the same for both kinds."""
        opened: list[dict] = []

        def open_new(path, prompt, **kw):
            opened.append(kw)
            return {"ok": True}

        def new(body):
            raw = json.dumps({"description": "try it", **body}).encode()
            h = dashboard.Handler.__new__(dashboard.Handler)
            h.path, h.command, h.request_version = "/api/new", "POST", "HTTP/1.1"
            h.requestline = "POST /api/new HTTP/1.1"
            h.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                         "Host": "127.0.0.1:8791"}
            h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
            h.client_address = ("127.0.0.1", 50000)
            h.server = types.SimpleNamespace(server_address=("127.0.0.1", 8791))
            h.log_message = lambda *a: None
            h.do_POST()
            self.assertIn(b" 200 ", h.wfile.getvalue().split(b"\r\n", 1)[0])
            return opened[-1]

        self.choose(claude={"model": "opus"}, codex={"model": "gpt-6-sol", "effort": "high"})
        with mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""), \
                mock.patch.object(dashboard, "create_cs_session", return_value=(True, str(self.tmp), "")), \
                mock.patch.object(dashboard, "load_labels", return_value={}), \
                mock.patch.object(dashboard, "save_labels"), \
                mock.patch.object(dashboard, "_allocate_agent_identity", return_value="codex"), \
                mock.patch.object(dashboard.BACKEND, "open_new", side_effect=open_new):
            self.assertEqual(self.flags(new({"agent": "codex"})["command"]),
                             {"model": "gpt-6-sol", "effort": "high"})
            self.assertEqual(self.flags(new({"agent": "codex", "model": ""})["command"]),
                             {"model": None, "effort": None})
            self.assertEqual(self.flags(new({"agent": "codex", "model": "gpt-5.5"})["command"]),
                             {"model": "gpt-5.5", "effort": "high"})
            self.assertEqual(new({"agent": "claude"})["model"], "opus")
            self.assertIsNone(new({"agent": "claude", "model": ""})["model"])
            self.assertEqual(new({"agent": "claude", "model": "haiku"})["model"], "haiku")
        self.assertEqual(dashboard._codex_model_args("", ""), [])


# --- the pool Codex is shown and judged by ------------------------------------

def _pool_source() -> dict:
    """A Codex reading with a spent main pool and an untouched reserve."""
    def win(pool, percent):
        return {"kind": "seven_day", "label": "7d", "pool": pool, "percent": percent,
                "model": None if pool == usage.CODEX_MAIN_POOL else "reserve",
                "trusted": True, "rolledOver": False, "resetUnknown": False, "ageSeconds": 0}
    return {"source": "codex", "state": "ok", "error": None,
            "pools": [{"id": usage.CODEX_MAIN_POOL, "label": None, "inUse": False},
                      {"id": usage.CODEX_RESERVE_POOL, "label": "reserve", "inUse": False}],
            "windows": [win(usage.CODEX_MAIN_POOL, 97.0), win(usage.CODEX_RESERVE_POOL, 3.0)]}


class ThePoolShown(_Home):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(usage._STATE, {"state": "ready", "checkedAt": 1.0, "sources": {
            "codex": usage._mark_pool_in_use(_pool_source(), usage.codex_config_model(),
                                             usage._hub_model())}})
        p.start()
        self.addCleanup(p.stop)

    def source(self) -> dict:
        return next(s for s in usage.snapshot()["sources"] if s["source"] == "codex")

    def test_codex_s_own_default_until_a_model_is_chosen(self):
        src = self.source()
        self.assertEqual(src["poolInUse"],
                         {"id": "codex", "label": None, "model": "gpt-6-astra", "from": "config"})
        self.assertEqual([a["level"] for a in usage.snapshot()["alerts"]], ["alarm"])

    def test_the_pool_follows_the_model_chosen_at_once(self):
        self.choose(codex={"model": "gpt-reserve"})          # no poll in between
        src = self.source()
        self.assertEqual(src["poolInUse"], {"id": usage.CODEX_RESERVE_POOL, "label": "reserve",
                                            "model": "gpt-reserve", "from": "settings"})
        self.assertEqual((src["configModel"], src["ownModel"]), ("gpt-reserve", "gpt-6-astra"))
        self.assertEqual([w["inUse"] for w in src["windows"]], [False, True])
        self.assertEqual([p["inUse"] for p in src["pools"]], [False, True])
        snap = usage.snapshot()
        # The spent main pool stops nothing now: a notice, not an alarm.
        self.assertEqual((snap["alerts"], [n["percent"] for n in snap["notices"]]), ([], [97.0]))
        self.choose(codex={"model": "gpt-6-sol"})
        src = self.source()
        self.assertEqual((src["poolInUse"]["id"], src["poolInUse"]["model"], src["poolInUse"]["from"]),
                         ("codex", "gpt-6-sol", "settings"))
        self.choose(codex={"model": ""})
        self.assertEqual(self.source()["poolInUse"]["from"], "config")

    def test_the_next_poll_reads_it_the_same(self):
        self.choose(codex={"model": "gpt-reserve"})
        with mock.patch.object(usage, "_read_codex_pools", return_value=_pool_source()):
            src = usage.read_codex(1.0, app_server=False)
        self.assertEqual((src["poolInUse"]["label"], src["poolInUse"]["from"]), ("reserve", "settings"))

    def test_the_allocation_judges_codex_by_the_pool_of_the_model_chosen(self):
        figure = dashboard._kind_usage(usage.snapshot(), "codex")
        self.assertEqual((figure["percent"], figure["pool"]), (97.0, "codex"))
        self.choose(codex={"model": "gpt-reserve"})
        snap = usage.snapshot()
        figure = dashboard._kind_usage(snap, "codex")
        self.assertEqual((figure["percent"], figure["poolLabel"], figure["model"]),
                         (3.0, "reserve", "gpt-reserve"))
        # A seat's own model is still judged by its own pool.
        self.assertEqual(dashboard._kind_usage(snap, "codex", "gpt-6-sol")["percent"], 97.0)
        claude = {"source": "claude", "state": "ok", "windows": [
            {"kind": "five_hour", "percent": 20.0, "trusted": True, "rolledOver": False,
             "resetUnknown": False}]}
        snap = {**snap, "sources": [claude, *snap["sources"]], "warnPercent": 80, "alarmPercent": 95}
        with mock.patch.object(dashboard.agents, "get_agent", return_value=None):
            kept = dashboard.choose_agent_kind_for_seat("codex", snap, installed=lambda k: True)
        self.assertEqual(kept["chosenKind"], "codex")

    def test_a_chosen_model_codex_no_longer_lists_is_not_the_pool_in_use(self):
        self.choose(codex={"model": "gpt-reserve"})
        gone = {"models": [m for m in CACHE["models"] if m["slug"] != "gpt-reserve"]}
        (self.codex / "models_cache.json").write_text(json.dumps(gone) + "\n", encoding="utf-8")
        usage.remark_codex()
        self.assertEqual(self.source()["poolInUse"],
                         {"id": "codex", "label": None, "model": "gpt-6-astra", "from": "config"})


TRAY_JS = r"""
const esc = (s) => String(s);
const fmtAgo = (s) => s + 's';
const usageWindowRow = (w) => `<row ${w.pool} ${w.kind}>`;
const usageCodexPoolName = (p) => p.label ? p.label + ' pool' : 'main pool';
const USAGE_SRC_NAME = { codex: 'Codex', claude: 'Claude' };
const USAGE = { notices: [] };
%s
const src = (used, inUse) => ({ source: 'codex', state: 'ok', ageSeconds: 30, trusted: true, via: 'app-server',
  poolInUse: used,
  pools: [{ id: 'codex', label: null, ageSeconds: 30, inUse: inUse === 'codex' },
          { id: 'base_model_inference', label: 'reserve', ageSeconds: 30, inUse: inUse !== 'codex' }],
  windows: [] });
console.log(JSON.stringify({
  chosen: usageSourceHtml(src({ id: 'base_model_inference', label: 'reserve', model: 'gpt-reserve', from: 'settings' }, 'reserve')),
  own: usageSourceHtml(src({ id: 'codex', label: null, model: 'gpt-6-astra', from: 'config' }, 'codex')),
  older: usageSourceHtml(src({ id: 'codex', label: null, model: 'gpt-6-astra' }, 'codex')) }));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TheTraySaysWhichModel(unittest.TestCase):
    """index.html's usageSourceHtml, run in Node: the plan chip's Codex line."""

    @classmethod
    def setUpClass(cls):
        index = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        i = index.index("function usageSourceHtml(")
        script = Path(tempfile.mkdtemp()) / "tray.cjs"
        script.write_text(TRAY_JS % index[i:index.index("\n}\n", i) + 3], encoding="utf-8")
        proc = subprocess.run([shutil.which("node"), str(script)], capture_output=True,
                              text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.html = {k: " ".join(v.split()) for k, v in json.loads(proc.stdout).items()}

    def test_the_line_names_the_model_and_where_it_was_chosen(self):
        self.assertIn("Codex sessions currently use the reserve pool "
                      "(model gpt-reserve, chosen in Settings).", self.html["chosen"])
        self.assertIn("Codex sessions currently use the main pool "
                      "(model gpt-6-astra, Codex’s own default).", self.html["own"])
        # A reading from a hub before this setting says no more than it knows.
        self.assertIn("use the main pool (model gpt-6-astra).", self.html["older"])

    def test_the_pool_marked_in_use_is_the_chosen_model_s(self):
        self.assertRegex(self.html["chosen"], r"reserve pool</span>\s*<span class=\"usage-pool-use\">in use")
        self.assertNotRegex(self.html["chosen"], r"main pool</span>\s*<span class=\"usage-pool-use\">")


if __name__ == "__main__":
    unittest.main()
