"""Free disk space never hangs (#282): a link in a task folder (a Windows
junction, a symlink) is never followed. Its size counts as 0, a prune removes
the link and leaves what it points to, every other walk stops at it too. One
item too slow to measure is kept and the scan goes on; Stop ends a scan within
a second and a new preview never runs beside an old one."""
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backup  # noqa: E402
import fslinks  # noqa: E402
import prune  # noqa: E402
import test_prune  # noqa: E402
from test_prune import write  # noqa: E402

WIN = os.name == "nt"


def junction(link: Path, target: Path) -> None:
    """A Windows junction, as the test homes of #223/#225 had."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True)
        if r.returncode:
            raise unittest.SkipTest(f"no junction: {r.stdout} {r.stderr}")
    assert os.path.isdir(link)


@unittest.skipUnless(WIN, "junctions are Windows'")
class LinkTests(unittest.TestCase):
    setUp = test_prune.PruneTests.setUp
    hub = test_prune.PruneTests.hub
    task = test_prune.PruneTests.task
    ids = test_prune.PruneTests.ids

    def outside(self):
        """A folder beside the project home, with a marker and some bytes."""
        out = self.tmp / "real-home"
        write(out / "marker.txt", "must survive")
        write(out / "deep" / "big.bin", "x" * 100_000)
        return out

    def test_size_never_follows_a_junction_even_one_to_its_own_parent(self):
        rid, folder, *_ = self.task("a")
        out = self.outside()
        junction(folder / "shadow-home" / "EnsembleProjects", self.home)   # a loop through itself
        junction(folder / "shadow-home" / "cs", out)
        t0 = time.monotonic()
        own = prune.size_of(folder)
        self.assertLess(time.monotonic() - t0, 5)
        self.assertLess(own, 50_000, "only the task folder's own bytes")
        self.assertEqual(prune.size_of(folder / "shadow-home" / "cs"), 0, "a link itself is 0")
        preview = prune.scan(self.hub(), 30)
        item = next(it for g in preview["groups"] for it in g["items"] if it["id"] == f"done:{rid}")
        part = next(p for p in item["parts"] if p["kind"] == "folder")
        self.assertLess(part["bytes"], 50_000)

    def test_a_prune_removes_the_links_and_never_their_targets(self):
        rid, folder, *_ = self.task("a")
        out = self.outside()
        junction(folder / "shadow-home" / "cs", out)
        junction(folder / "shadow-home" / "EnsembleProjects", self.home)
        try:
            os.symlink(out / "marker.txt", folder / "shadow-home" / "marker-link.txt")
        except OSError:
            pass                                # no right to make symlinks: the junctions say enough
        hub = self.hub()
        preview = prune.scan(hub, 30)
        res = prune.run(hub, preview, [f"done:{rid}"])
        self.assertEqual(res["skipped"], [])
        self.assertEqual(res["done"][0]["errors"], [])
        self.assertFalse(os.path.lexists(prune._long(folder)))
        self.assertEqual((out / "marker.txt").read_text(encoding="utf-8"), "must survive")
        self.assertEqual((out / "deep" / "big.bin").stat().st_size, 100_000)
        self.assertTrue((self.docs / "#1 report.md").exists(), "the home it linked to is untouched")
        self.assertTrue((self.home / "ROADMAP.md").exists())

    def test_remove_tree_takes_a_link_as_a_link_and_read_only_files(self):
        out = self.outside()
        top = self.tmp / "gone"
        write(top / "a" / "b" / "ro.txt", "read only")
        os.chmod(top / "a" / "b" / "ro.txt", 0o444)
        junction(top / "a" / "b" / "j", out)
        fslinks.remove_tree(top)
        self.assertFalse(os.path.lexists(top))
        self.assertTrue((out / "marker.txt").is_file())
        # A link handed in by itself goes alone too.
        junction(self.tmp / "solo", out)
        fslinks.remove_tree(self.tmp / "solo")
        self.assertFalse(os.path.lexists(self.tmp / "solo"))
        self.assertTrue((out / "marker.txt").is_file())

    def test_every_walk_stops_at_a_link(self):
        out = self.outside()
        write(out / "nested" / ".git" / "HEAD", "ref: refs/heads/main")
        top = self.tmp / "walked"
        write(top / "own.txt")
        junction(top / "j", out)
        junction(top / "loop", top)
        seen = [os.path.relpath(os.path.join(d, f), top) for d, _dirs, fs in fslinks.walk(top) for f in fs]
        self.assertEqual(seen, ["own.txt"])
        with os.scandir(top) as it:
            self.assertFalse(fslinks.is_dir(next(e for e in it if e.name == "j")))
        self.assertTrue(fslinks.is_link(top / "j"))
        self.assertFalse(fslinks.is_link(top / "own.txt"))
        nested, _big = backup.scan(top)
        self.assertEqual(nested, [], "backup's nested-repo check does not go through the link")

    def test_orphan_folder_with_a_link_is_measured_without_it(self):
        out = self.outside()
        orphan = self.home / "left-behind"
        write(orphan / "task.json", "{}")
        junction(orphan / "cs", out)
        for p in (orphan / "task.json", orphan):
            test_prune.age(p)
        preview = prune.scan(self.hub(), 30, groups=("orphans",))
        item = next(it for g in preview["groups"] for it in g["items"] if it["label"] == "left-behind")
        self.assertLess(item["bytes"], 1000)

    def test_a_link_inside_the_worktree_is_gone_before_git_removes_it(self):
        # git worktree remove --force walks into a junction on Windows and
        # empties its target; the links go first.
        rid, folder, cwd, *_ = self.task("a")
        out = self.outside()
        junction(cwd / "shadow" / "cs", out)
        hub = self.hub()
        preview = prune.scan(hub, 30)
        res = prune.run(hub, preview, [f"done:{rid}"])
        self.assertEqual(res["skipped"], [])
        self.assertEqual(res["done"][0]["errors"], [])
        self.assertFalse(os.path.lexists(prune._long(cwd)))
        self.assertFalse(os.path.lexists(prune._long(folder)))
        self.assertEqual((out / "marker.txt").read_text(encoding="utf-8"), "must survive")
        self.assertEqual((out / "deep" / "big.bin").stat().st_size, 100_000)

    def test_unlink_links_takes_only_the_links(self):
        out = self.outside()
        top = self.tmp / "mixed"
        write(top / "own.txt")
        write(top / "a" / "kept.txt")
        junction(top / "a" / "j", out)
        junction(top / "loop", top)
        self.assertEqual(fslinks.unlink_links(top), 2)
        self.assertFalse(os.path.lexists(top / "a" / "j"))
        self.assertFalse(os.path.lexists(top / "loop"))
        self.assertTrue((top / "own.txt").is_file())
        self.assertTrue((top / "a" / "kept.txt").is_file())
        self.assertTrue((out / "marker.txt").is_file())
        self.assertTrue((out / "deep" / "big.bin").is_file())


class SlowScanTests(unittest.TestCase):
    setUp = test_prune.PruneTests.setUp
    hub = test_prune.PruneTests.hub
    task = test_prune.PruneTests.task

    def slow_scandir(self, marker: str, delay: float):
        real = os.scandir

        def scandir(p="."):
            if marker in str(p):
                time.sleep(delay)
            return real(p)
        return mock.patch.object(prune.os, "scandir", scandir)

    def deep(self, folder: Path, n: int = 40):
        """``n`` folders to walk, each one a slowed listing."""
        for i in range(n):
            write(folder / f"d{i}" / "f.txt")

    def test_an_item_over_its_budget_is_kept_and_the_rest_are_measured(self):
        slow_rid, slow, *_ = self.task("slowone")
        fast_rid, fast, *_ = self.task("fast")
        self.deep(slow)
        with self.slow_scandir("slowone", 0.05), mock.patch.object(prune, "ITEM_BUDGET_S", 0.3):
            t0 = time.monotonic()
            preview = prune.scan(self.hub(), 30, groups=("done",))
            took = time.monotonic() - t0
        self.assertLess(took, 1.5)
        ids = {it["id"] for g in preview["groups"] for it in g["items"]}
        self.assertIn(f"done:{fast_rid}", ids)
        self.assertNotIn(f"done:{slow_rid}", ids, "never offered: it was not measured")
        kept = [k for k in preview["kept"] if k.get("room") == slow_rid]
        self.assertEqual([k["why"] for k in kept], [prune.TOO_SLOW])
        self.assertEqual(kept[0]["path"], str(slow))

    def test_stop_ends_a_scan_within_a_second(self):
        self.task("slowone")
        self.deep(self.home / "slowone", 200)
        job = prune.Job()
        hub = self.hub()
        hub.progress = job.progress
        out = {}
        with self.slow_scandir("slowone", 0.05):
            self.assertTrue(job.start("scan", lambda stop: out.update(pv=prune.scan(hub, 30, stop=stop))))
            end = time.monotonic() + 10
            while job.snapshot()["phase"] != "Measuring Done tasks" and time.monotonic() < end:
                time.sleep(0.01)
            time.sleep(0.2)
            t0 = time.monotonic()
            job.cancel()
            job._thread.join(5)
            self.assertLess(time.monotonic() - t0, 1.0)
        s = job.snapshot()
        self.assertEqual((s["running"], s["stopped"]), ("", True))

    def test_a_new_preview_stops_the_old_one_and_never_runs_beside_it(self):
        job = prune.Job()
        running, most, stops = [0], [0], []
        lock = threading.Lock()

        def fn(stop):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            stops.append(stop)
            try:
                while not stop():
                    time.sleep(0.01)
            finally:
                with lock:
                    running[0] -= 1
        self.assertTrue(job.start("scan", fn))
        time.sleep(0.05)
        first = job._thread
        self.assertFalse(job.start("run", fn), "a deletion waits for the preview")
        self.assertTrue(job.start("scan", fn, replace=True))
        self.assertFalse(first.is_alive())
        self.assertTrue(stops[0]() and not stops[1](), "the old one stopped, the new one runs")
        self.assertEqual(job.snapshot()["running"], "scan")
        job.cancel()
        job._thread.join(2)
        self.assertEqual(most[0], 1)

    def test_an_old_scan_that_does_not_stop_in_time_refuses_the_new(self):
        job = prune.Job()
        job.REPLACE_WAIT_S = 0.1
        release = threading.Event()
        self.assertTrue(job.start("scan", lambda stop: release.wait(5)))
        self.assertFalse(job.start("scan", lambda stop: None, replace=True))
        release.set()
        job._thread.join(2)
        self.assertEqual(job.snapshot()["running"], "")


if __name__ == "__main__":
    unittest.main()
