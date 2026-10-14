import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import durable
import warm_cache
import dashboard


class WarmCacheTest(unittest.TestCase):
    def test_round_trip_keeps_paths_and_tuples(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            transcript = home / "session.jsonl"
            warm_cache.save(home, state, {"300": [{"id": "one"}]},
                            {"session": transcript}, {"room-one": ((1, 2), {"items": []})})
            loaded = warm_cache.load(home, state)
            self.assertEqual(loaded["sessions"]["300"], [{"id": "one"}])
            self.assertEqual(loaded["transcripts"]["session"], transcript)
            self.assertEqual(loaded["threads"]["room-one"][0], (1, 2))

    def test_changed_files_do_not_refuse_the_snapshot(self):
        """ED-227: rows are a first answer, read again at once; a transcript
        written since does not throw the whole snapshot away."""
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            folder = home / ".claude" / "projects" / "one"
            folder.mkdir(parents=True)
            transcript = folder / "session.jsonl"
            transcript.write_text("first\n", encoding="utf-8")
            warm_cache.save(home, state, {"300": [{"id": "one"}]}, {}, {})
            with transcript.open("a", encoding="utf-8") as out:
                out.write("second\n")
            (folder / "new.jsonl").write_text("new\n", encoding="utf-8")
            self.assertEqual(warm_cache.load(home, state)["sessions"]["300"], [{"id": "one"}])

    def test_torn_snapshot_falls_back_to_the_previous_save(self):
        """A power cut that leaves the newest save empty or cut off."""
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            warm_cache.save(home, state, {"300": [{"id": "old"}]}, {}, {})
            warm_cache.save(home, state, {"300": [{"id": "new"}]}, {}, {})
            target = warm_cache.path(state)
            self.assertTrue(durable.prev_of(target).exists())
            for torn in (b"", b"\0" * 4096, target.read_bytes()[:100]):
                target.write_bytes(torn)
                self.assertEqual(warm_cache.load(home, state)["sessions"]["300"], [{"id": "old"}])

    def test_codex_home_override_is_part_of_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex-a")}):
                warm_cache.save(home, state, {}, {}, {})
                self.assertIsNotNone(warm_cache.load(home, state))
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex-b")}):
                self.assertIsNone(warm_cache.load(home, state))

    def test_save_from_memory_reads_nothing_again(self):
        gen = dashboard._SESS_GEN
        old = {300: (0, gen, [{"id": "held"}]), 500: (0, gen, [{"id": "boot"}])}
        thread = {"room-one": ((1,), {"items": ["held"]})}
        with mock.patch.object(dashboard, "_SESS_CACHE", old), \
             mock.patch.object(dashboard, "_SESS_BOOT", {500}), \
             mock.patch.object(dashboard, "_WARM_SAVED_SIG", None), \
             mock.patch.object(dashboard, "_load_sessions_uncached") as sessions, \
             mock.patch.object(dashboard, "_TRANSCRIPT_INDEX", (str(dashboard.PROJ_DIR), 0.0, {"s": Path("s.jsonl")})), \
             mock.patch.object(dashboard.threads, "_INDEX", thread), \
             mock.patch.object(dashboard.threads, "build") as build, \
             mock.patch.object(dashboard, "_restart_log"), \
             mock.patch.object(dashboard.warm_cache, "save") as save:
            self.assertTrue(dashboard.save_restart_caches(why="periodic"))
            # Nothing new since: the periodic save writes nothing.
            self.assertFalse(dashboard.save_restart_caches(why="periodic"))
            # A restart saves anyway.
            self.assertTrue(dashboard.save_restart_caches())
        sessions.assert_not_called()
        build.assert_not_called()
        # The start-up snapshot's own rows (_SESS_BOOT) are not saved again.
        self.assertEqual(save.call_args.args[2], {"300": [{"id": "held"}]})
        self.assertEqual(save.call_args.args[3], {"s": Path("s.jsonl")})
        self.assertEqual(save.call_args.args[4]["index"]["room-one"][1]["items"], ["held"])

    def test_snapshot_written_through_durable(self):
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            with mock.patch.object(warm_cache.durable, "write_text", wraps=durable.write_text) as w:
                warm_cache.save(home, state, {}, {}, {})
            self.assertTrue(w.call_args.kwargs.get("keep_prev"))
            self.assertEqual(json.loads(warm_cache.path(state).read_text(encoding="utf-8"))["format"],
                             warm_cache.FORMAT)

    def parts_saved(self, cache, **extra):
        with mock.patch.object(dashboard, "_SESS_CACHE", cache),              mock.patch.object(dashboard, "_SESS_BOOT", set()),              mock.patch.object(dashboard, "_WARM_SAVED_SIG", None),              mock.patch.object(dashboard, "_TRANSCRIPT_INDEX", (str(dashboard.PROJ_DIR), 0.0, {})),              mock.patch.object(dashboard.threads, "_INDEX", {}),              mock.patch.object(dashboard, "_restart_log") as log,              mock.patch.object(dashboard.warm_cache, "save", **extra) as save:
            ok = dashboard.save_restart_caches()
        return ok, save, log

    def test_a_list_a_change_made_stale_is_not_saved(self):
        # PO check of cc820db: a stale list would come back as valid and the
        # board would open on it at once.
        gen = dashboard._SESS_GEN
        ok, save, _ = self.parts_saved({300: (0, gen, [{"id": "now"}]),
                                        500: (0, gen - 1, [{"id": "before an archive"}])})
        self.assertTrue(ok)
        self.assertEqual(save.call_args.args[2], {"300": [{"id": "now"}]})

    def test_any_error_in_a_save_is_logged_not_raised(self):
        ok, _, log = self.parts_saved({300: (0, dashboard._SESS_GEN, [{"id": "a"}])},
                                      side_effect=RuntimeError("dictionary changed size during iteration"))
        self.assertFalse(ok)
        self.assertIn("dictionary changed size", log.call_args.args[0])

    def test_the_saver_keeps_going_after_an_error(self):
        rounds = []

        def save(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                raise RuntimeError("boom")
            raise SystemExit      # end the loop for the test

        with mock.patch.object(dashboard, "WARM_SAVE_EVERY_S", 0),              mock.patch.object(dashboard.startup, "is_ready", return_value=True),              mock.patch.object(dashboard, "save_restart_caches", side_effect=save):
            with self.assertRaises(SystemExit):
                dashboard._warm_saver()
        self.assertEqual(len(rounds), 2)

    def test_saved_parts_are_copies(self):
        # The save walks them unlocked while the hub changes the originals.
        build = {"items": ["a"]}
        with mock.patch.object(dashboard.threads, "_INDEX", {"room-one": ((1,), build)}),              mock.patch.object(dashboard, "_SESS_CACHE", {}):
            _, _, cache = dashboard._warm_parts()
        build["items"].append("b")
        self.assertEqual(cache["index"]["room-one"][1]["items"], ["a"])

    def test_no_current_list_overwrites_the_saved_one(self):
        # PO item 2: older rows left on disk would load as current next start
        # (an archived task comes back), from the file or from its .prev.
        with tempfile.TemporaryDirectory(prefix="ensemble-warm-") as td:
            home = Path(td)
            state = home / ".ensemble"
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home / "codex")}):
                warm_cache.save(home, state, {"300": [{"id": "archived"}]}, {}, {})
                warm_cache.save(home, state, {"300": [{"id": "archived"}]}, {}, {})
                self.assertTrue(durable.prev_of(warm_cache.path(state)).exists())
                gen = dashboard._SESS_GEN
                with mock.patch.object(dashboard, "HOME", home), \
                     mock.patch.object(dashboard, "DASHBOARD_DIR", state):
                    ok, _, _ = self.parts_saved({300: (0, gen - 1, [{"id": "archived"}])},
                                                wraps=warm_cache.save)
                self.assertTrue(ok)
                self.assertEqual(warm_cache.load(home, state)["sessions"], {})
                self.assertFalse(durable.prev_of(warm_cache.path(state)).exists())

    def test_unchanged_periodic_save_copies_no_thread_builds(self):
        # PO item 4: the 20 MB deep copy only for a save that is written.
        gen = dashboard._SESS_GEN
        with mock.patch.object(dashboard, "_SESS_CACHE", {300: (0, gen, [{"id": "a"}])}), \
             mock.patch.object(dashboard, "_SESS_BOOT", set()), \
             mock.patch.object(dashboard, "_WARM_SAVED_SIG", None), \
             mock.patch.object(dashboard.threads, "_INDEX", {"room-one": ((1,), {"items": []})}), \
             mock.patch.object(dashboard, "_restart_log"), \
             mock.patch.object(dashboard.warm_cache, "save"), \
             mock.patch.object(dashboard, "_warm_threads", wraps=dashboard._warm_threads) as copies:
            self.assertTrue(dashboard.save_restart_caches(why="periodic"))
            self.assertFalse(dashboard.save_restart_caches(why="periodic"))
            self.assertEqual(copies.call_count, 1)
            dashboard.threads._INDEX["room-one"] = ((2,), {"items": ["new"]})
            self.assertTrue(dashboard.save_restart_caches(why="periodic"))
            self.assertEqual(copies.call_count, 2)

    def test_a_bad_cache_never_stops_the_start(self):
        # PO item 3: a .py locked while the code hash is read, or anything
        # else the load raises, is logged and the hub reads the files.
        with mock.patch.object(warm_cache, "_CODE_VERSION", ""), \
             mock.patch.object(Path, "read_bytes", side_effect=PermissionError("locked")), \
             mock.patch.object(warm_cache.durable, "read_json", return_value={"format": warm_cache.FORMAT,
                               "codexHome": str(warm_cache._codex_home(Path.home()))}):
            self.assertIsNone(warm_cache.load(Path.home(), Path("unused")))
        with mock.patch.object(dashboard.warm_cache, "load", side_effect=RuntimeError("bad cache")), \
             mock.patch.object(dashboard, "_restart_log") as log:
            dashboard.load_restart_caches()
        self.assertIn("bad cache", log.call_args.args[0])

    def test_signature_leaves_out_what_changes_by_itself(self):
        row = {"id": "a", "label": "x", "idleSeconds": 5, "isLive": True, "status": "busy", "pid": 1}
        sig = dashboard._warm_sig({"300": [row]}, {}, {"index": {}})
        ticked = {**row, "idleSeconds": 300, "isLive": False, "status": "idle", "pid": None}
        self.assertEqual(dashboard._warm_sig({"300": [ticked]}, {}, {"index": {}}), sig)
        self.assertNotEqual(dashboard._warm_sig({"300": [{**row, "label": "y"}]}, {}, {"index": {}}), sig)


if __name__ == "__main__":
    unittest.main()
