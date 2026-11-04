"""#305 (P189): /api/files/check, /api/point/refs and /api/projects/documents
stay fast on a big board, answer as before, and build each cache once."""
import json
import os
import threading
import time
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import asks
import chatroom
import dashboard
import file_refs
import points
import project_docs
from tests.test_task_numbers import Hub


class DocumentsCache(Hub):
    def setUp(self):
        super().setUp()
        project_docs._LIST_CACHE.clear()
        self.addCleanup(project_docs._LIST_CACHE.clear)
        self.docs = Path(self.tmp.name) / "Docs"
        (self.docs / "#7 Plan").mkdir(parents=True)
        (self.docs / "#3 Report.md").write_text("a", encoding="utf-8")
        (self.docs / "#7 Plan" / "notes.md").write_text("b", encoding="utf-8")
        (self.docs / "shot.png").write_bytes(b"x")

    def test_same_answer_as_the_walk_and_a_copy(self):
        got = project_docs.list_documents_cached(str(self.docs))
        self.assertEqual(got, project_docs.list_documents(str(self.docs)))
        self.assertEqual(sorted(i["rel"] for i in got["items"]), ["#3 Report.md", "#7 Plan/notes.md"])
        got["items"].clear()
        self.assertEqual(len(project_docs.list_documents_cached(str(self.docs))["items"]), 2,
                         "a caller's change never reaches the cache")

    def test_a_new_report_shows_at_once_a_deep_edit_after_the_ttl(self):
        project_docs.list_documents_cached(str(self.docs))
        (self.docs / "#9 New.md").write_text("c", encoding="utf-8")
        os.utime(self.docs, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))   # coarse clocks
        self.assertIn("#9 New.md", [i["rel"] for i in project_docs.list_documents_cached(str(self.docs))["items"]])
        deep = self.docs / "#7 Plan" / "notes.md"
        deep.write_text("longer text", encoding="utf-8")
        with mock.patch.object(project_docs, "LIST_TTL_S", 0.0):
            got = project_docs.list_documents_cached(str(self.docs))
        self.assertEqual(next(i for i in got["items"] if i["rel"] == "#7 Plan/notes.md")["size"], 11)

    def test_one_walk_for_requests_at_once(self):
        walks = []
        real = project_docs.list_documents

        def slow(*a, **k):
            walks.append(1)
            time.sleep(0.2)
            return real(*a, **k)
        with mock.patch.object(project_docs, "list_documents", slow):
            ts = [threading.Thread(target=project_docs.list_documents_cached, args=(str(self.docs),))
                  for _ in range(5)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(walks), 1)


class ReadableCheck(Hub):
    def test_same_answers_as_within(self):
        home = dashboard.find_project(self.ed)["path"]
        paths = [home, os.path.join(home, "a", "b.md"), str(Path(home).parent / "elsewhere"),
                 str(Path(self.tmp.name) / "outside"), "", os.path.join(home, "..", "x")]
        ok = dashboard._readable_check()
        roots = [p["path"] for p in dashboard.load_projects()] + [str(dashboard.CS_ROOT), str(dashboard.PROJECTS_ROOT)]
        for p in paths:
            want = bool(p) and (any(dashboard._within(p, r) for r in roots) or dashboard.session_folder_ok(p))
            self.assertEqual(ok(p), want, p)


class GoneSessions(Hub):
    def test_a_rotated_out_session_without_transcript_is_not_read(self):
        room = {"id": "room-x", "messages": []}
        with mock.patch.object(dashboard, "room_po_id", return_value=""), \
                mock.patch.object(points, "_session_stat", return_value=None) as stat, \
                mock.patch.object(dashboard, "solo_turns", return_value={}) as turns:
            asks._SID_CARDS.pop("gone-sid", None)
            self.assertEqual(asks._session_cards(room, "gone-sid", "claude", current=False), [])
            self.assertEqual(asks._session_cards(room, "gone-sid", "claude", current=False), [])
        turns.assert_not_called()
        self.assertEqual(stat.call_args.kwargs.get("ended"), True)
        asks._SID_CARDS.pop("gone-sid", None)


class BigBoard(Hub):
    """300 rooms, 2,000 points, 500 documents, 200 file links."""
    ROOMS, POINTS, DOCS, PATHS = 300, 2000, 500, 200

    def setUp(self):
        super().setUp()
        for c in (points._CACHE, dashboard._POINT_INDEX, dashboard._POINT_ANSWERS, project_docs._LIST_CACHE):
            c.clear()
            self.addCleanup(c.clear)
        p = mock.patch.object(points, "sync", side_effect=lambda rid, **k: points.load(rid, **k))
        p.start(); self.addCleanup(p.stop)
        self.rooms = [self.room(f"Task {i}", self.ed) for i in range(self.ROOMS)]
        per = self.POINTS // self.ROOMS + 1
        n = 0
        for rid in self.rooms:
            led = points.load(rid)
            for _ in range(per):
                if n >= self.POINTS:
                    break
                n += 1
                led["points"].append({"id": f"P{n}", "text": f"ask {n}", "state": "acked", "mid": f"m{n}",
                                      "createdAt": 1, "answers": []})
            points._save(rid, led)
        proj = dashboard.find_project(self.ed)
        docs = Path(dashboard.project_documents_dir(proj))
        for i in range(self.DOCS):
            d = docs / f"#{i} Folder" if i % 5 == 0 else docs
            d.mkdir(parents=True, exist_ok=True)
            (d / f"#{i} Report {i}.md").write_text("x", encoding="utf-8")
        self.docs = docs

    def timed(self, path):
        t = time.perf_counter()
        status, _, body = self.call("GET", path)
        self.assertEqual(status, 200, body[:200])
        return time.perf_counter() - t, json.loads(body)

    def test_under_a_second_warm_two_cold(self):
        ids = ",".join(f"P{i}" for i in range(1, 201))
        refs = f"/api/point/refs?project={self.ed}&ids={ids}"
        names = [f"#{i} Report {i}.md" for i in range(0, self.PATHS)]
        q = {"ctx": [[self.rooms[0], ""]], "items": [[n, 0] for n in names]}
        check = "/api/files/check?q=" + quote(json.dumps(q))
        docs = f"/api/projects/documents?project={self.ed}"
        for label, path in (("point/refs", refs), ("files/check", check), ("documents", docs)):
            cold, got = self.timed(path)
            warm, again = self.timed(path)
            self.assertEqual(got, again, label)
            self.assertLess(cold, 2.0, f"{label} cold {cold:.2f} s")
            self.assertLess(warm, 1.0, f"{label} warm {warm:.2f} s")
            if label == "point/refs":
                self.assertEqual(sum(1 for v in got["points"].values() if v), 200)
            elif label == "files/check":
                self.assertEqual(got["there"], [True] * self.PATHS)
            else:
                self.assertEqual(len(got["items"]), self.DOCS)

    def test_one_index_build_for_requests_at_once(self):
        builds = []
        real = dashboard._point_index_build

        def slow(*a, **k):
            builds.append(1)
            time.sleep(0.2)
            return real(*a, **k)
        with mock.patch.object(dashboard, "_point_index_build", slow):
            ts = [threading.Thread(target=dashboard.point_ref, args=("P5", self.ed)) for _ in range(5)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(builds), 1)


class BigTreeBareNames(Hub):
    """Round 2: a chat's bare names ("sigma.yml") searched through a code
    folder past the walk cap. A miss must not re-walk it inline each minute."""
    FILES = 6000

    def setUp(self):
        super().setUp()
        file_refs.forget_indexes()
        self.addCleanup(file_refs.forget_indexes)
        for p in (mock.patch.object(file_refs, "MAX_ENTRIES", 4000),):
            p.start(); self.addCleanup(p.stop)
        self.code = Path(dashboard.find_project(self.ed)["path"]) / "src"
        for i in range(self.FILES):
            d = self.code / f"pkg{i // 100}" / "sub"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"Mod{i}.java").write_text("x", encoding="utf-8")
        (self.code / "pkg0" / "sigma-real.yml").write_text("x", encoding="utf-8")
        self.rid = self.room("Trading PO", self.ed)
        # one bare name per request: a folder at the cap spends the request's
        # whole Budget, so the bare names after it go unsearched (as before)
        names = [str(self.code / "pkg0" / "sub" / "Mod1.java"), "pkg1/sub/Mod101.java", "sigma.yml"]
        self.q = {"ctx": [[self.rid, str(self.code)]], "items": [[n, 0] for n in names]}

    def check(self, q=None):
        t = time.perf_counter()
        out = dashboard.files_check(q or self.q)["there"]
        return time.perf_counter() - t, out

    def test_answers_and_an_expired_index_answers_without_walking(self):
        cold, got = self.check()
        self.assertEqual(got, [True, True, False])
        self.assertEqual(self.check({"ctx": [[self.rid, str(self.code)]], "items": [["Mod5.java", 0]]})[1], [True])
        self.assertEqual(self.check({"ctx": [[self.rid, str(self.code)]], "items": [["sigma-real.yml", 0]]})[1], [True])
        self.assertLess(cold, 2.0)
        warm, again = self.check()
        self.assertEqual(again, got)
        self.assertLess(warm, 1.0)
        gate = threading.Event()
        real = file_refs._walk

        def held(*a, **k):
            gate.wait(10)
            return real(*a, **k)
        (self.code / "pkg1" / "sigma.yml").write_text("x", encoding="utf-8")
        with mock.patch.object(file_refs, "INDEX_TTL", 0.0), mock.patch.object(file_refs, "_walk", held):
            w = file_refs.WALKS
            t, stale = self.check()       # the walk is held: the answer cannot have waited for it
            self.assertLess(t, 1.0)
            self.assertEqual(stale, got, "the old index answered")
            t, stale = self.check()       # a second request does not start a second walk
            self.assertEqual(len([th for th in threading.enumerate() if th.name == "file-refs-index"]),
                             len(file_refs._BUILDING))
            gate.set()
            for ev in list(file_refs._BUILDING.values()):
                ev.wait(10)
        _, fresh = self.check()
        self.assertEqual(fresh, [True, True, True], "the renewed index has the new file")

    def test_past_the_stale_bound_the_walk_is_inline(self):
        self.check()
        (self.code / "pkg2" / "sigma.yml").write_text("x", encoding="utf-8")
        with mock.patch.object(file_refs, "INDEX_TTL", 0.0), mock.patch.object(file_refs, "INDEX_STALE", 0.0):
            self.assertEqual(self.check()[1], [True, True, True])
