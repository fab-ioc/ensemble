"""#409 4a: a hub with no outside source set up leaves no trace of it.

It writes no outside state (no ``outside/`` folder, no hub chat file) on its
loop, offers no agent the outside tools, and installs the skills without
their outside sources part. Opening the setup box writes the setup file.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chatroom              # noqa: E402
import dashboard             # noqa: E402
import ensemble_tools        # noqa: E402
import hub_chat              # noqa: E402
import outside               # noqa: E402
import outside_deliver       # noqa: E402
import outside_items         # noqa: E402
import outside_route         # noqa: E402
import outside_setup         # noqa: E402

T0 = 1_800_000_000.0


class _Hub(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ensemble-quiet-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.state = self.dir / "state"
        self.state.mkdir()
        old_rooms = chatroom.ROOMS_DIR
        chatroom.ROOMS_DIR = self.dir / "rooms"
        self.addCleanup(setattr, chatroom, "ROOMS_DIR", old_rooms)
        self.projects = [{"id": "proj-made-up", "name": "Made up", "poRoomId": ""}]
        for p in (mock.patch.object(dashboard, "DASHBOARD_DIR", self.state),
                  mock.patch.object(dashboard, "load_projects",
                                    side_effect=lambda: [dict(p) for p in self.projects]),
                  mock.patch.object(dashboard, "load_session_projects", return_value={}),
                  mock.patch.object(dashboard, "may_restart_hub", return_value=False),
                  mock.patch.object(outside, "_log"),
                  mock.patch.object(outside_setup, "_log"),
                  mock.patch.object(outside_deliver, "_log"),
                  mock.patch.object(outside_deliver, "_LAST", 0.0),
                  mock.patch.object(outside_items, "_LAST", 0.0)):
            p.start()
            self.addCleanup(p.stop)
        outside.reset()
        self.addCleanup(outside.reset)

    def add_source(self):
        outside._write(outside._config_file(), {"sources": {"slack": {"type": "slack"}}})


class NothingWritten(_Hub):
    def test_the_loop_writes_nothing_without_a_source(self):
        outside.maybe_tick(background=False, now=T0)
        outside_setup.tick(now=T0, background=False)
        outside_deliver.maybe_tick()
        outside_items.maybe_tick(now=T0)
        hub_chat.clear()
        self.assertFalse((self.state / "outside").exists(), list(self.state.rglob("*")))
        self.assertFalse(hub_chat.path().exists())
        self.assertEqual(list(self.state.iterdir()), [])

    def test_opening_the_setup_box_writes_the_file(self):
        out = outside_setup.panel()
        self.assertTrue(outside_setup.setup_path().exists())
        self.assertIn("## Sources", outside_setup.setup_path().read_text(encoding="utf-8"))
        self.assertTrue(out)

    def test_the_first_source_brings_the_file(self):
        self.add_source()
        outside_setup.tick(now=T0, background=False)
        self.assertTrue(outside_setup.setup_path().exists())


class NoToolsWithoutASource(_Hub):
    def setUp(self):
        super().setUp()

        def room(title, role):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude",
                                                "role": role}])["id"]
            chatroom.patch_room(rid, projectId="proj-made-up")
            return rid
        self.po = room("Made up PO", "Product owner")
        self.projects[0]["poRoomId"] = self.po
        self.task = room("A task", "engineer")

    def names(self, rid):
        room = chatroom.get_room(rid, public=False)
        return {t["name"] for t in ensemble_tools.tool_schemas(room, "claude")}

    def route(self):
        outside_route.set_initiative("proj-made-up", {"topic": "made-up pricing"},
                                     {"kind": "ceo"}, T0)

    def test_none_offered_and_each_refused(self):
        for rid in (self.po, self.task):
            names = self.names(rid)
            self.assertNotIn("ensemble_read_outside", names)
            self.assertNotIn("ensemble_routing", names)
        for name in ("ensemble_read_outside", "ensemble_routing"):
            text, err = ensemble_tools.call(name, {}, self.po, "claude", None)
            self.assertTrue(err)
            self.assertIn("reads no outside source", text)
        # The messages tools a PO has without outside sources stay.
        self.assertIn("ensemble_message_po", self.names(self.po))

    def test_with_a_source_the_po_reads_and_a_routed_project_routes(self):
        self.add_source()
        self.assertIn("ensemble_read_outside", self.names(self.po))
        self.assertNotIn("ensemble_read_outside", self.names(self.task))
        # No row yet: the CEO gives the project its first one in the setup file.
        self.assertNotIn("ensemble_routing", self.names(self.po))
        text, err = ensemble_tools.call("ensemble_routing", {}, self.task, "claude", None)
        self.assertTrue(err)
        self.assertIn("the CEO sets the routing in the setup file", text)
        self.route()
        self.assertIn("ensemble_routing", self.names(self.po))
        self.assertIn("ensemble_routing", self.names(self.task))


class SkillPart(_Hub):
    BODY = ("# Skill\n\nbefore\n\n"
            "<!-- outside-sources: left out until a source is set up -->\n"
            "**A `[slack]` line** is outside words.\n"
            "<!-- /outside-sources -->\n\nafter\n")

    def test_the_part_is_left_out_or_kept_without_its_marks(self):
        self.assertEqual(dashboard._skill_text(self.BODY, False), "# Skill\n\nbefore\n\n\nafter\n")
        self.assertEqual(dashboard._skill_text(self.BODY, True),
                         "# Skill\n\nbefore\n\n**A `[slack]` line** is outside words.\n\nafter\n")

    def test_the_real_skill_has_one_whole_part(self):
        body = (dashboard.SKILLS_SRC / "ensemble" / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(len(dashboard.SKILL_OUTSIDE_RE.findall(body)), 1)
        off = dashboard._skill_text(body, False)
        self.assertNotIn("[slack]", off)
        self.assertNotIn("outside-sources", off)
        self.assertNotIn("outside-sources", dashboard._skill_text(body, True))

    def test_installed_again_when_a_source_is_set_up(self):
        src = self.dir / "skills"
        (src / "made-up").mkdir(parents=True)
        (src / "made-up" / "SKILL.md").write_text(self.BODY, encoding="utf-8")
        home = self.dir / "home"
        with mock.patch.object(dashboard, "SKILLS_SRC", src), \
                mock.patch.object(dashboard, "HOME", home), \
                mock.patch.object(dashboard.shutil, "which", return_value=None), \
                mock.patch.object(dashboard, "_SKILLS_OUTSIDE", [None]):
            installed = home / ".claude" / "skills" / "made-up" / "SKILL.md"
            self.assertEqual(dashboard.refresh_agent_skills(), [])   # never installed yet
            dashboard.install_agent_skills()
            self.assertNotIn("[slack]", installed.read_text(encoding="utf-8"))
            self.assertEqual(dashboard.refresh_agent_skills(), [])   # nothing changed
            self.add_source()
            self.assertTrue(dashboard.refresh_agent_skills())
            self.assertIn("[slack]", installed.read_text(encoding="utf-8"))
            self.assertEqual(dashboard.refresh_agent_skills(), [])
            outside.remove_source("slack")
            self.assertTrue(dashboard.refresh_agent_skills())
            self.assertNotIn("[slack]", installed.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
