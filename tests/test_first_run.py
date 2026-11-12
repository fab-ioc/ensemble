"""ED-340: a seat stopped on its CLI's first-run trust screen.

The hub pre-trusts the folders it launches agents in (Codex: exact-path
``[projects."<path>"]`` in ``$CODEX_HOME/config.toml``; Claude:
``projects[<path>].hasTrustDialogAccepted`` in ``.claude.json``), answers a
trust screen that shows anyway in a hub folder, and reports any first-run
screen it leaves up as a stalled seat. Every config here lives in a temporary
folder; the real home is never read or written.
"""

import itertools
import json
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

import dashboard  # noqa: F401  (binds first_run and attention)
import attention
import first_run
import agents.codex
from agents.claude import ClaudeAgent
from agents.codex import CodexAgent

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / f"first_run_{name}.txt").read_text(encoding="utf-8")


class CodexTrustTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "codex-home"
        self.cfg = self.home / "config.toml"
        env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.folder = Path(self.tmp.name) / "Projects" / "My Task" / "repo"
        self.folder.mkdir(parents=True)
        self.agent = CodexAgent()

    def load(self) -> dict:
        self.assertIsNotNone(tomllib, "tomllib needed to read the config back")
        return tomllib.loads(self.cfg.read_text(encoding="utf-8"))

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_new_config_trusts_the_exact_path(self):
        self.assertTrue(self.agent.ensure_trusted(str(self.folder)))
        projects = self.load()["projects"]
        key = os.path.realpath(self.folder)
        self.assertEqual(projects[key]["trust_level"], "trusted")
        if os.name != "nt":
            # Codex on Linux looks the folder up by its exact path: the
            # lowercased key the old hub wrote never matched.
            self.assertNotIn(key.lower(), projects)
            self.assertIn("/Projects/My Task/repo", key)

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_idempotent_and_keeps_other_entries(self):
        self.home.mkdir()
        before = ('model = "gpt-5"\n\n[projects."/somewhere/else"]\ntrust_level = "trusted"\n'
                  '\n[tui]\nnotifications = true\n')
        self.cfg.write_text(before, encoding="utf-8")
        self.assertTrue(self.agent.ensure_trusted(str(self.folder)))
        once = self.cfg.read_text(encoding="utf-8")
        self.assertTrue(once.startswith(before), "appended, not rewritten")
        self.assertTrue(self.agent.ensure_trusted(str(self.folder)))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), once, "a second call writes nothing")
        data = self.load()
        self.assertEqual(data["model"], "gpt-5")
        self.assertTrue(data["tui"]["notifications"])
        self.assertEqual(data["projects"]["/somewhere/else"]["trust_level"], "trusted")
        self.assertEqual(data["projects"][os.path.realpath(self.folder)]["trust_level"], "trusted")

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_quote_and_backslash_in_path_stay_valid_toml(self):
        odd = Path(self.tmp.name) / 'say "hi" \\ there'
        odd.mkdir()
        self.assertTrue(self.agent.ensure_trusted(str(odd)))
        self.assertEqual(self.load()["projects"][os.path.realpath(odd)]["trust_level"], "trusted")

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_a_folder_marked_untrusted_is_left_alone(self):
        self.home.mkdir()
        key = os.path.realpath(self.folder)
        text = f'[projects."{key}"]\ntrust_level = "untrusted"\n'
        self.cfg.write_text(text, encoding="utf-8")
        self.assertFalse(self.agent.ensure_trusted(str(self.folder)))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    def test_a_config_that_does_not_parse_is_left_alone(self):
        if tomllib is None:
            self.skipTest("Python 3.11+")
        self.home.mkdir()
        text = "this is [not toml\n"
        self.cfg.write_text(text, encoding="utf-8")
        self.assertFalse(self.agent.ensure_trusted(str(self.folder)))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_a_link_trusts_the_real_path_and_the_given_one(self):
        if os.name == "nt":
            self.skipTest("symlinks")
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.folder)
        self.assertTrue(self.agent.ensure_trusted(str(link)))
        projects = self.load()["projects"]
        self.assertEqual(projects[os.path.realpath(self.folder)]["trust_level"], "trusted")
        self.assertEqual(projects[str(link)]["trust_level"], "trusted")

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_an_alias_marked_untrusted_means_no_write_at_all(self):
        if os.name == "nt":
            self.skipTest("symlinks")
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.folder)
        self.home.mkdir()
        text = f'[projects."{link}"]\ntrust_level = "untrusted"\n'
        self.cfg.write_text(text, encoding="utf-8")
        self.assertFalse(self.agent.ensure_trusted(str(link)))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text,
                         "the real path is not trusted behind the person's back")
        self.assertTrue(agents.codex.explicitly_untrusted(str(link)))
        self.assertFalse(agents.codex.explicitly_untrusted(str(Path(self.tmp.name))))

    @unittest.skipIf(tomllib is None, "Python 3.11+")
    def test_a_table_without_a_trust_level_is_left_alone(self):
        self.home.mkdir()
        text = f'[projects."{os.path.realpath(self.folder)}"]\nnote = "mine"\n'
        self.cfg.write_text(text, encoding="utf-8")
        self.assertFalse(self.agent.ensure_trusted(str(self.folder)))
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    def test_without_tomllib_only_a_trusted_entry_counts(self):
        self.home.mkdir()
        key = os.path.realpath(self.folder)
        head = f'[projects."{key}"]\n'
        with mock.patch.object(agents.codex, "tomllib", None):
            for body in ('trust_level = "untrusted"\n', 'note = "mine"\n'):
                text = head + body + '\n[tui]\ntrust_level = "trusted"\n'
                self.cfg.write_text(text, encoding="utf-8")
                self.assertFalse(self.agent.ensure_trusted(str(self.folder)), body)
                self.assertEqual(self.cfg.read_text(encoding="utf-8"), text, body)
            self.assertTrue(agents.codex.explicitly_untrusted(str(self.folder)) is False)
            text = head + 'trust_level = "trusted"\n'
            self.cfg.write_text(text, encoding="utf-8")
            self.assertTrue(self.agent.ensure_trusted(str(self.folder)))
            self.assertEqual(self.cfg.read_text(encoding="utf-8"), text, "already there")
            self.cfg.write_text('model = "x"\n', encoding="utf-8")
            self.assertTrue(self.agent.ensure_trusted(str(self.folder)))
            self.assertIn(head + 'trust_level = "trusted"\n', self.cfg.read_text(encoding="utf-8"))
            self.cfg.write_text(head + 'trust_level = "untrusted"\n', encoding="utf-8")
            self.assertTrue(agents.codex.explicitly_untrusted(str(self.folder)))

    def test_answer_reads_untrusted_from_the_codex_config(self):
        self.home.mkdir()
        self.cfg.write_text(f'[projects."{os.path.realpath(self.folder)}"]\n'
                            'trust_level = "untrusted"\n', encoding="utf-8")
        sess = FakeSess("codex_trust", {})
        with mock.patch.object(first_run, "hub_folder", return_value=True), \
             mock.patch.object(first_run.time, "sleep", return_value=None):
            self.assertFalse(first_run.answer(sess, str(self.folder), log=lambda m: None))
        self.assertEqual(sess.writes, [])

    def test_without_tomllib_other_forms_of_the_key_are_respected(self):
        # Review 2: dotted keys and inline tables are valid TOML for the same
        # entry; a header of ours after them would be a duplicate table.
        self.home.mkdir()
        k = agents.codex._toml_key(os.path.realpath(self.folder))
        cases = {
            f'projects.{k}.trust_level = "untrusted"\n': "untrusted",
            f'[projects]\n{k} = {{ trust_level = "untrusted" }}\n': "untrusted",
            f'[projects]\n{k}.note = "mine"\n': "notrust",
            f'projects.{k}.trust_level = "trusted"\n': "trusted",
        }
        with mock.patch.object(agents.codex, "tomllib", None):
            for text, state in cases.items():
                self.cfg.write_text(text, encoding="utf-8")
                self.assertEqual(self.agent.ensure_trusted(str(self.folder)), state == "trusted", text)
                self.assertEqual(self.cfg.read_text(encoding="utf-8"), text, text)
                self.assertEqual(agents.codex.explicitly_untrusted(str(self.folder)),
                                 state == "untrusted", text)
        if tomllib is not None:
            for text in cases:
                tomllib.loads(text)             # the cases are valid TOML, left valid

    def test_the_repository_root_is_not_trusted(self):
        if tomllib is None:
            self.skipTest("Python 3.11+")
        (self.folder.parent / ".git").mkdir()
        sub = self.folder / "pkg"
        sub.mkdir()
        self.assertTrue(self.agent.ensure_trusted(str(sub)))
        projects = self.load()["projects"]
        self.assertEqual(list(projects), [os.path.realpath(sub)])


class ClaudeTrustTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = Path(self.tmp.name) / "claude-conf"
        self.conf.mkdir()
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.conf)})
        env.start()
        self.addCleanup(env.stop)
        self.folder = Path(self.tmp.name) / "Task" / "repo"
        self.folder.mkdir(parents=True)

    def test_trust_goes_to_the_config_dir_and_keeps_the_rest(self):
        cfg = self.conf / ".claude.json"
        cfg.write_text(json.dumps({"theme": "dark", "projects": {"/other": {"x": 1}}}),
                       encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=Path(self.tmp.name) / "nohome"):
            self.assertTrue(ClaudeAgent().ensure_trusted(str(self.folder)))
            self.assertTrue(ClaudeAgent().ensure_trusted(str(self.folder)))
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["projects"]["/other"], {"x": 1})
        key = str(self.folder).replace("\\", "/")
        self.assertIs(data["projects"][key]["hasTrustDialogAccepted"], True)
        self.assertFalse((Path(self.tmp.name) / "nohome").exists())

    def test_a_config_that_does_not_parse_is_left_alone(self):
        cfg = self.conf / ".claude.json"
        cfg.write_text("{nope", encoding="utf-8")
        self.assertFalse(ClaudeAgent().ensure_trusted(str(self.folder)))
        self.assertEqual(cfg.read_text(encoding="utf-8"), "{nope")


class DetectorTest(unittest.TestCase):
    def test_stuck_screens_are_found(self):
        codex = first_run.find(fixture("codex_trust"))
        self.assertEqual((codex["cli"], codex["kind"], codex["answerable"]), ("codex", "trust", True))
        claude = first_run.find(fixture("claude_trust"))
        self.assertEqual((claude["cli"], claude["kind"], claude["answerable"]), ("claude", "trust", True))

    def test_answered_screens_are_not(self):
        self.assertIsNone(first_run.find(fixture("codex_answered")))
        self.assertIsNone(first_run.find(fixture("claude_answered")))
        self.assertIsNone(first_run.find(""))
        self.assertIsNone(first_run.find("› Ask Codex to do anything\n? for shortcuts"))

    def test_cursor_position(self):
        for name, want in (("codex_trust", "1.trustandcontinue"), ("claude_trust", "no,exit"),
                           ("claude_down", "yes,itrustthisfolder")):
            flat = first_run.squash(fixture(name))
            hit = first_run._find_in(flat)
            self.assertEqual(first_run._cursor_at(flat, hit["screen"], hit["at"]), want, name)

    def test_screens_the_hub_never_answers(self):
        hit = first_run.find("Update available! 0.168 -> 0.169\n› 1. Update now\n"
                             "2. Skip\n3. Skip until next version")
        self.assertEqual(hit["kind"], "update")
        self.assertFalse(hit["answerable"])
        hit = first_run.find("WARNING: Claude Code running in Bypass Permissions mode\n"
                             "❯ 1. No, exit\n2. Yes, I accept")
        self.assertEqual(hit["kind"], "bypass")
        self.assertFalse(hit["answerable"])

    def test_a_screen_followed_by_another_modal_is_not_the_screen(self):
        # The tail is a history: a trust screen still in it, with another
        # modal drawn after, is not what the terminal waits on.
        stale = fixture("codex_trust") + "\nGrant access to this tool? 1. Yes 2. No\n"
        self.assertIsNone(first_run.find(stale))
        stale = fixture("claude_trust") + ("\nDo you want to proceed?\n❯ 1. Yes\n2. No\n"
                                           "Enter to confirm · Esc to cancel\n")
        self.assertIsNone(first_run.find(stale))
        # A redraw of the screen's own options after a key still counts.
        self.assertIsNotNone(first_run.find(fixture("claude_down")))

    def test_a_later_modal_over_the_same_footer_is_not_the_screen(self):
        # Review 2: no question mark, the footer a trust screen also uses.
        for name, later in (
                ("claude_down", "\nAllow this action\nEnter to confirm · Esc to cancel\n"),
                ("claude_trust", "\nAllow this action  Yes  No, exit\nEnter to confirm · Esc to cancel\n"),
                ("claude_trust", "\nAllow this action\n❯ 1. Yes\n2. No, exit\n"),
                ("codex_trust", "\nAllow this action\nEnter continue · Esc back\n"),
                ("codex_trust", "\nRun this command\n› 1. Yes\n2. No\n")):
            self.assertIsNone(first_run.find(fixture(name) + later), (name, later))

    def test_first_run_screens_captured_from_the_clis(self):
        # Codex 0.167.1 and Claude Code 2.1.322 with an empty home.
        hit = first_run.find(fixture("codex_signin"))
        self.assertEqual((hit["cli"], hit["kind"], hit["answerable"]), ("codex", "signin", False))
        hit = first_run.find(fixture("claude_theme"))
        self.assertEqual((hit["cli"], hit["kind"], hit["answerable"]), ("claude", "theme", False))

    def test_old_codex_wording(self):
        hit = first_run.find("> You are in /x\nDo you trust the contents of this directory?\n"
                             "› 1. Yes, continue\n2. No, quit")
        self.assertEqual((hit["cli"], hit["answerable"]), ("codex", True))


class FakeSess:
    """A terminal that replays captured screens: each key moves it to the next
    screen named in ``moves`` for (screen, key)."""

    _ids = itertools.count()

    def __init__(self, screen: str, moves: dict, extra: str = ""):
        self.screen, self.moves, self.writes, self.extra = screen, moves, [], extra
        self.id = f"pty-test-{next(self._ids)}"

    def tail(self, lines=40, max_bytes=16384):
        return fixture(self.screen) + self.extra

    def write(self, data):
        self.writes.append(data)
        self.screen = self.moves.get((self.screen, data), self.screen)
        return True

    def alive(self):
        return True


class AnswerTest(unittest.TestCase):
    def setUp(self):
        sleep = mock.patch.object(first_run.time, "sleep", return_value=None)
        sleep.start()
        self.addCleanup(sleep.stop)
        untrusted = mock.patch.object(first_run, "codex_untrusted", return_value=False)
        untrusted.start()
        self.addCleanup(untrusted.stop)

    def hub(self, yes=True):
        return mock.patch.object(first_run, "hub_folder", return_value=yes)

    def test_codex_gets_enter(self):
        sess = FakeSess("codex_trust", {("codex_trust", first_run.ENTER): "codex_answered"})
        with self.hub():
            self.assertTrue(first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None))
        self.assertEqual(sess.writes, [first_run.ENTER])
        self.assertIsNone(first_run.find(sess.tail()))

    def test_claude_gets_down_then_enter_never_enter_on_no_exit(self):
        sess = FakeSess("claude_trust", {("claude_trust", first_run.DOWN): "claude_down",
                                         ("claude_down", first_run.ENTER): "claude_answered",
                                         ("claude_trust", first_run.ENTER): "EXITED"})
        with self.hub():
            self.assertTrue(first_run.answer(sess, "/srv/hub/work/WTD/repo", log=lambda m: None))
        self.assertEqual(sess.writes, [first_run.DOWN, first_run.ENTER])
        self.assertEqual(sess.screen, "claude_answered")

    def test_a_cursor_that_does_not_move_is_never_entered(self):
        sess = FakeSess("claude_trust", {})     # Down does nothing
        with self.hub():
            self.assertFalse(first_run.answer(sess, "/srv/hub/work/WTD/repo", log=lambda m: None))
        self.assertNotIn(first_run.ENTER, sess.writes)
        self.assertLessEqual(len(sess.writes), 4)

    def test_a_folder_that_is_not_the_hubs_is_not_answered(self):
        sess = FakeSess("codex_trust", {})
        with self.hub(False):
            self.assertFalse(first_run.answer(sess, "/home/someone/code", log=lambda m: None))
        self.assertEqual(sess.writes, [])

    def test_a_stale_screen_under_another_modal_gets_no_key(self):
        sess = FakeSess("codex_trust", {}, extra="\nGrant access to this tool? 1. Yes 2. No\n")
        with self.hub():
            self.assertFalse(first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None))
        self.assertEqual(sess.writes, [])

    def test_a_later_modal_over_the_same_footer_gets_no_key(self):
        sess = FakeSess("claude_down", {},
                        extra="\nAllow this action\nEnter to confirm · Esc to cancel\n")
        with self.hub():
            self.assertFalse(first_run.answer(sess, "/srv/hub/work/WTD/repo", log=lambda m: None))
        self.assertEqual(sess.writes, [])

    def test_a_folder_marked_untrusted_in_codex_gets_no_key(self):
        sess = FakeSess("codex_trust", {})
        with self.hub(), mock.patch.object(first_run, "codex_untrusted", return_value=True):
            self.assertFalse(first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None))
        self.assertEqual(sess.writes, [])

    def test_two_callers_at_once_press_enter_once(self):
        gate, inside = threading.Event(), threading.Event()

        class Slow(FakeSess):
            def tail(self, lines=40, max_bytes=16384):
                inside.set()
                gate.wait(5)
                return super().tail(lines, max_bytes)

        sess = Slow("codex_trust", {})          # the screen stays up after Enter
        got = []
        with self.hub():
            a = threading.Thread(target=lambda: got.append(
                first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None)))
            a.start()
            inside.wait(5)
            b = threading.Thread(target=lambda: got.append(
                first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None)))
            b.start()
            b.join(5)
            gate.set()
            a.join(5)
            # Later callers (the stall tick) never answer it again either.
            self.assertFalse(first_run.answer(sess, "/srv/hub/work/WT", log=lambda m: None))
        self.assertEqual(sorted(got), [False, True])
        self.assertEqual(sess.writes, [first_run.ENTER])
        self.assertTrue(first_run.answered(sess))

    def test_watch_answers_a_fresh_seat(self):
        sess = FakeSess("codex_trust", {("codex_trust", first_run.ENTER): "codex_answered"})
        with self.hub():
            t = first_run.watch(sess, "/srv/hub/work/WT", log=lambda m: None)
            t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(sess.writes, [first_run.ENTER])


class HubFolderTest(unittest.TestCase):
    def test_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in ("projects/P/task/repo", "scratch/s1", "code/proj", "elsewhere"):
                (root / d).mkdir(parents=True)
            fake = types.SimpleNamespace(
                PROJECTS_ROOT=root / "projects", CS_ROOT=root / "scratch",
                load_projects=lambda: [{"path": str(root / "code" / "proj"), "home": ""}])
            with mock.patch.object(first_run, "_d", fake):
                self.assertTrue(first_run.hub_folder(str(root / "projects/P/task/repo")))
                self.assertTrue(first_run.hub_folder(str(root / "scratch/s1")))
                self.assertTrue(first_run.hub_folder(str(root / "code/proj")))
                self.assertFalse(first_run.hub_folder(str(root / "elsewhere")))
                self.assertFalse(first_run.hub_folder(str(root / "projects-other")))
                self.assertFalse(first_run.hub_folder(""))


class LookTest(unittest.TestCase):
    def test_stall_tick_answers_a_seat_once(self):
        first_run._TRIED.clear()
        self.addCleanup(first_run._TRIED.clear)
        sess = FakeSess("codex_trust", {})       # never moves on
        part = {"identity": "codex", "ptyId": "pty-1", "cwd": "/srv/hub/work/WT"}
        fake = types.SimpleNamespace(
            chatroom=types.SimpleNamespace(get_room=lambda rid, **kw: {"launched": True},
                                           agent_participants=lambda room: [part]),
            rotation=types.SimpleNamespace(_pty=lambda p: sess))
        with mock.patch.object(first_run, "_d", fake), \
             mock.patch.object(first_run, "hub_folder", return_value=True), \
             mock.patch.object(first_run, "codex_untrusted", return_value=False), \
             mock.patch.object(first_run, "_log"):
            for _ in range(4):
                first_run.look("room-x", 0)
        self.assertEqual(sess.writes, [first_run.ENTER])


class AttentionTest(unittest.TestCase):
    def ev(self, tail: str, idle):
        return {"alive": True, "scan": attention.analyse(tail), "hook": None,
                "claudeStatus": "", "limit": None, "idleSeconds": idle, "tail": tail,
                "cwd": "/somewhere/else", "death": None}

    def classify(self, ev, part=None):
        part = part or {"identity": "codex", "agent": "codex"}
        with mock.patch.object(attention._d.provider_retry, "status", return_value=None), \
             mock.patch.object(attention._d, "workflow_of", return_value="inprogress"):
            return attention._classify_agent({"owners": ["claude"]}, part, ev, 600, 1791878500)

    def test_a_trust_screen_is_a_stall_after_the_grace(self):
        with mock.patch.object(first_run, "hub_folder", return_value=False):
            state, reason, extra = self.classify(self.ev(fixture("codex_trust"), 120))
        self.assertEqual(state, "stalled")
        self.assertEqual(extra["cause"], "first_run")
        self.assertEqual((extra["cli"], extra["screen"]), ("codex", "trust"))
        self.assertIn("Trust this folder?", reason)
        self.assertIn("/somewhere/else", reason)

    def test_hub_folder_wording(self):
        with mock.patch.object(first_run, "hub_folder", return_value=True):
            _, reason, _ = self.classify(self.ev(fixture("claude_trust"),  120),
                                         {"identity": "claude", "agent": "claude"})
        self.assertIn("the hub could not answer it", reason)

    def test_not_within_the_grace(self):
        got = self.classify(self.ev(fixture("codex_trust"), 5))
        self.assertFalse(got and got[2].get("cause") == "first_run")

    def test_a_stale_screen_does_not_hide_the_modal_after_it(self):
        tail = fixture("codex_trust") + "\nGrant access to this tool? 1. Yes 2. No\n"
        with mock.patch.object(first_run, "hub_folder", return_value=True):
            got = self.classify(self.ev(tail, 120))
        self.assertFalse(got and got[2].get("cause") == "first_run")

    def test_untrusted_folder_wording(self):
        with mock.patch.object(first_run, "hub_folder", return_value=True), \
             mock.patch.object(first_run, "codex_untrusted", return_value=True):
            _, reason, extra = self.classify(self.ev(fixture("codex_trust"), 120))
        self.assertEqual(extra["cause"], "first_run")
        self.assertIn("marked untrusted in Codex's config", reason)

    def test_an_answered_screen_is_not_a_stall(self):
        got = self.classify(self.ev(fixture("codex_answered"), 120))
        self.assertFalse(got and got[2].get("cause") == "first_run")


if __name__ == "__main__":
    unittest.main()
