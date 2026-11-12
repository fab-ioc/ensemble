"""The sessions list's loose rows are only the conversations a person started
(#350, P318). The hub's (a room's current or past conversation, a deleted
room's), a one-shot run's (``claude -p``, the Agent SDK, ``codex exec``), one
begun by hub text, one in a temporary folder outside every project and one in
a task's folder (here, or as written on the machine the state was moved from)
make no row. A person's own conversation keeps its row. Deep search leaves the
same ones out of Past sessions."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import loose_sessions as ls  # noqa: E402
from test_session_window import DAY, NOW, Bench  # noqa: E402


def plain(rows: list[dict]) -> list[str]:
    return sorted(r["sessionId"] for r in rows if not r.get("headless"))


def hub(s: str):
    return dashboard._hub_words_kind(s)


class Words(unittest.TestCase):
    def test_the_board_brief_is_a_po_brief(self):
        for s in ("## Board now (from the hub, 12:00)\n...",
                  "[rotation] ## Board now (from the hub, 12:00)\n...",
                  "[product owner] ## Board now (from the hub, x)"):
            self.assertTrue(dashboard.is_po_brief(s), s)
            self.assertEqual(hub(s), "Past PO conversation")
        self.assertFalse(dashboard.is_po_brief("Board now please"))
        self.assertFalse(dashboard.is_po_brief("[note] here is the board"))

    def test_the_kinds_of_hub_words(self):
        for s in ("[relay] codex said: done", "You are 'claude', the engineer on a small team",
                  "[rotation] You are the product owner (PO) of the project 'X'."):
            self.assertEqual(ls.reason(s, "/home/u/x", hub_words=hub), "hub words", s)
        self.assertEqual(ls.reason("Fix the header please", "/home/u/x", hub_words=hub), "")


class Folders(unittest.TestCase):
    def test_temporary_folders(self):
        for c in (r"C:\Users\u\AppData\Local\Temp\probe-1", "/tmp/x", "/var/tmp/y",
                  "/private/var/folders/ab/T/z", "/var/folders/ab/T/z"):
            self.assertTrue(ls.in_temp_folder(c, tmp="/nowhere"), c)
        for c in ("/home/u/tmp", r"C:\Users\u\code", "/home/u/dev/ensemble", ""):
            self.assertFalse(ls.in_temp_folder(c, tmp="/nowhere"), c)
        self.assertTrue(ls.in_temp_folder("/scratch/t/a", tmp="/scratch/t"))

    def test_a_temporary_folder_in_a_project_is_a_persons(self):
        r = ls.reason("hi", "/tmp/p/sub", hub_words=hub, project_dirs=["/tmp/p"], tmp="/nowhere")
        self.assertEqual(r, "")
        r = ls.reason("hi", "/tmp/q/sub", hub_words=hub, project_dirs=["/tmp/p"], tmp="/nowhere")
        self.assertEqual(r, "temporary folder")

    def test_task_folders_never_a_projects_own(self):
        rooms = [{"taskDir": "/p/A/task1"}, {"taskDir": "/p/A"}, {"taskDir": "/p"}, {"taskDir": ""}, {}]
        got = ls.task_folders(rooms, ["/p/A"])
        self.assertEqual(got, frozenset({ls._key("/p/A/task1")}))

    def test_a_task_folder_here_or_moved_from_another_machine(self):
        root = Path("/home/u/EnsembleProjects")
        tdirs = ls.task_folders([{"taskDir": str(root / "Dash" / "send_now")}], [])
        kw = dict(hub_words=hub, task_dirs=tdirs, home=Path("/home/u"), root=root)
        self.assertEqual(ls.reason("Run the shell command", str(root / "Dash" / "send_now" / "m-claude"), **kw),
                         "task folder")
        old = r"C:\Users\fabio\EnsembleProjects\Dash\send_now\m-claude-sendnow2"
        self.assertEqual(ls.reason("Without using any tools", old, **kw), "task folder")
        self.assertEqual(ls.reason("hello", r"C:\Users\fabio\IdeaProjects\claude-dashboard-main", **kw), "")
        self.assertEqual(ls.reason("hello", str(root / "Dash"), **kw), "")

    def test_a_task_folder_recorded_elsewhere_maps_as_the_conversations_does(self):
        # The state copied to another home: both sides map to the same place.
        home, root = Path("/home/v"), Path("/home/v/EnsembleProjects")
        tdirs = ls.task_folders([{"taskDir": "/home/u/EnsembleProjects/Dash/send_now"}], [], home, root)
        old = r"C:\Users\fabio\EnsembleProjects\Dash\send_now\m-claude-long"
        self.assertTrue(ls.in_task_folder(old, tdirs, home, root))
        # A project's own folder never becomes a task folder by mapping either.
        tdirs = ls.task_folders([{"taskDir": "/home/u/EnsembleProjects/Dash"}], ["/home/v/EnsembleProjects/Dash"],
                                home, root)
        self.assertFalse(ls.in_task_folder("/home/v/EnsembleProjects/Dash/x", tdirs, home, root))


class OneShot(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = Path(td.name)
        ls._ONE_SHOT.clear()

    def write(self, name: str, *lines: dict) -> Path:
        p = self.d / name
        p.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        return p

    def test_claude(self):
        p = self.write("a.jsonl", {"type": "summary"}, {"type": "user", "entrypoint": "sdk-cli"})
        self.assertTrue(ls.claude_one_shot(p))
        self.assertTrue(ls.claude_one_shot(self.write("b.jsonl", {"type": "user", "entrypoint": "sdk-py"})))
        self.assertFalse(ls.claude_one_shot(self.write("c.jsonl", {"type": "user", "entrypoint": "cli"})))
        self.assertFalse(ls.claude_one_shot(self.write("d.jsonl", {"type": "summary"})))
        self.assertFalse(ls.claude_one_shot(self.d / "missing.jsonl"))

    def test_a_file_that_says_nothing_yet_is_asked_again(self):
        p = self.write("e.jsonl", {"type": "summary"})
        self.assertFalse(ls.claude_one_shot(p))
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "entrypoint": "sdk-cli"}) + "\n")
        self.assertTrue(ls.claude_one_shot(p))

    def test_codex(self):
        meta = lambda **p: {"type": "session_meta", "payload": {"id": "x", **p}}    # noqa: E731
        self.assertTrue(ls.codex_one_shot(self.write("r1.jsonl", meta(source="exec"))))
        self.assertTrue(ls.codex_one_shot(self.write("r2.jsonl", meta(originator="codex_exec"))))
        self.assertFalse(ls.codex_one_shot(self.write("r3.jsonl", meta(source="cli", originator="codex_cli_rs"))))
        self.assertFalse(ls.codex_one_shot(""))


class GoneSessions(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        old = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = Path(td.name) / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old)

    def test_a_deleted_rooms_conversations_are_noted(self):
        room = chatroom.create_room("t", [{"identity": "claude", "agent": "claude", "sessionId": "c-now", "cwd": "x"}])
        stored = chatroom.get_room(room["id"], public=False)
        stored["participants"][0]["rotations"] = [{"fromSessionId": "c-old", "at": 1}]
        stored["retiredSessions"] = [{"sessionId": "r-1"}]
        chatroom._write(stored)
        self.assertEqual(chatroom.gone_sessions(), frozenset())
        self.assertTrue(chatroom.delete_room(room["id"]))
        self.assertEqual(chatroom.gone_sessions(), frozenset({"c-now", "c-old", "r-1"}))
        self.assertFalse(chatroom.delete_room(room["id"]))

    def test_a_preflight_notes_nothing(self):
        room = chatroom.create_room("t", [{"identity": "claude", "agent": "claude", "sessionId": "c-1", "cwd": "x"}])
        with mock.patch.dict(os.environ, {"ENSEMBLE_PTY_PREFLIGHT": "1"}):
            chatroom.delete_room(room["id"])
        self.assertEqual(chatroom.gone_sessions(), frozenset())


class TheList(unittest.TestCase):
    """_load_sessions_uncached over a bench of conversations."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.mine = "/home/u/code/shop"         # a person's folder, outside the temporary one
        self.gone = mock.patch.object(dashboard.chatroom, "gone_sessions", return_value=frozenset({"deleted"}))
        ls._ONE_SHOT.clear()

    def claude(self, sid: str, cwd: str, text: str = "hello", entrypoint: str = "cli") -> Path:
        p = self.b.claude(sid, cwd, age_days=1, text=text)
        lines = p.read_text(encoding="utf-8").splitlines()
        out = [json.dumps({**json.loads(ln), "entrypoint": entrypoint}) for ln in lines]
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.utime(p, (NOW - DAY,) * 2)
        return p

    def codex(self, sid: str, cwd: str, source: str = "cli") -> Path:
        p = self.b.codex(sid, cwd, age_days=1)
        lines = p.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        meta["payload"]["source"] = source
        p.write_text("\n".join([json.dumps(meta)] + lines[1:]) + "\n", encoding="utf-8")
        os.utime(p, (NOW - DAY,) * 2)
        return p

    def load(self, temp_rule: bool = False):
        return self.b.load(50, extra=[self.gone, mock.patch.object(ls, "TEMP_RULE", temp_rule)])

    def test_only_a_persons_own_conversations_make_rows(self):
        task_dir = str(self.b.base / "Dash" / "t1")
        self.b.room("room-t1", "claude", str(self.b.base / "elsewhere"), session_id="now", taskDir=task_dir)
        self.b.rooms[0]["participants"][0]["rotations"] = [{"fromSessionId": "past", "at": 1}]
        for sid in ("now", "past", "deleted"):
            self.claude(sid, self.mine)
        self.claude("print-run", self.mine, entrypoint="sdk-cli")
        self.claude("relay", self.mine, text="[relay] codex said")
        self.claude("standing-brief", self.mine, text="You are 'claude', the engineer")
        self.claude("board-brief", self.mine, text="[rotation] ## Board now (from the hub, 9:00)")
        self.claude("probe", str(Path(task_dir) / "m-claude-probe"), text="Run the shell command ls")
        self.codex("00000001-exec", self.mine, source="exec")
        self.claude("mine", self.mine, text="can you tell me whats going on")
        self.codex("00000002-mine", self.mine)
        rows = self.load()
        self.assertEqual(plain(rows), ["00000002-mine", "mine"])

    def test_a_temporary_folder_outside_every_project(self):
        tmp = tempfile.gettempdir()
        self.claude("in-temp", os.path.join(tmp, "ens-phone-1", "x"))
        proj = os.path.join(tmp, "ens-proj")
        self.b.projects = [{"id": "p", "name": "P", "path": proj}]
        self.claude("in-project", os.path.join(proj, "src"))
        self.assertEqual(plain(self.load(temp_rule=True)), ["in-project"])
        self.assertEqual(plain(self.load(temp_rule=False)), ["in-project", "in-temp"])

    def test_hidden_rows_do_not_use_up_the_window(self):
        for i in range(6):
            self.claude(f"run-{i}", self.mine, entrypoint="sdk-cli")
        p = self.b.claude("older", self.mine, age_days=3)
        self.assertTrue(p.exists())
        rows = self.b.load(3, extra=[self.gone, mock.patch.object(ls, "TEMP_RULE", False)])
        self.assertEqual(plain(rows), ["older"])


class DeepSearch(unittest.TestCase):
    def test_hub_own_hits_are_left_out(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        ls._ONE_SHOT.clear()

        def tr(name, text, ep="cli"):
            p = d / f"{name}.jsonl"
            p.write_text(json.dumps({"type": "user", "cwd": "/home/u/x", "entrypoint": ep,
                                     "message": {"role": "user", "content": text}}) + "\n", encoding="utf-8")
            return str(p)
        with mock.patch.object(dashboard.chatroom, "gone_sessions", return_value=frozenset({"gone"})), \
                mock.patch.object(dashboard.chatroom, "list_rooms", return_value=[]), \
                mock.patch.object(dashboard, "load_projects", return_value=[]), \
                mock.patch.object(ls, "TEMP_RULE", False):
            own = dashboard._find_hub_own()
            f = lambda sid, path: {"sessionId": sid, "agent": "claude", "path": path, "cwd": "/home/u/x"}  # noqa: E731
            self.assertTrue(own(f("gone", tr("g", "anything"))))
            self.assertTrue(own(f("relay", tr("r", "[relay] hi"))))
            self.assertTrue(own(f("run", tr("p", "hi", ep="sdk-cli"))))
            self.assertFalse(own(f("mine", tr("m", "Fix the header please"))))


if __name__ == "__main__":
    unittest.main()
