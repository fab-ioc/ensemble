"""The top bar's search, wherever you are (#161, GitHub issue 6).

global_search.py reads the query as the old dashboard did, finds tasks by
title, number and spec, chat messages on their own, and every Claude and Codex
transcript (in worker processes, matched on bytes first). dashboard.global_find
puts a transcript under the task that held it, by any of its conversations
(a rotated one too) or by its agents' folder, and the rest under Past sessions.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard
import global_search as gs

ROOT = Path(__file__).resolve().parent.parent


def claude_line(kind: str, text: str, cwd: str = "C:\\work\\x", sid: str = "s") -> str:
    return json.dumps({"type": kind, "cwd": cwd, "sessionId": sid, "uuid": "dyna-uuid",
                       "message": {"role": kind, "content": [{"type": "text", "text": text}]}})


def codex_lines(cwd: str, texts: list[str]) -> list[str]:
    out = [json.dumps({"type": "session_meta", "payload": {"id": "x", "cwd": cwd,
                                                           "base_instructions": "dyna-zero everywhere"}}),
           json.dumps({"type": "turn_context", "payload": {"cwd": cwd, "text": "tranche watchdog"}})]
    for t in texts:
        out.append(json.dumps({"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": t}]}}))
    return out


class Query(unittest.TestCase):
    def test_words_or_and_phrases(self):
        self.assertEqual(gs.parse_query('Margin use OR "History Phase 2"'),
                         [["margin", "use"], ["history phase 2"]])
        self.assertEqual(gs.parse_query("  "), [])
        self.assertEqual(gs.parse_query("OR a OR"), [["a"]])
        self.assertEqual(gs.parse_query('"open'), [["open"]])

    def test_matching_and_snippet(self):
        g = gs.parse_query("dyna zero")
        self.assertTrue(gs.text_matches("Zero days on Dyna", g))
        self.assertFalse(gs.text_matches("Dyna only", g))
        s = gs.snippet("x" * 200 + " the tranche rule " + "y" * 200, ["tranche"])
        self.assertIn("tranche", s)
        self.assertTrue(s.startswith("…") and s.endswith("…"))
        self.assertLess(len(s), 200)


class Transcripts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name: str, lines: list[str]) -> str:
        p = self.tmp / name
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(p)

    def test_a_claude_transcript_by_what_was_said(self):
        f = self.write("aaaa-1.jsonl", [claude_line("user", "Look at the dyna-zero watchdog", "C:\\w\\dz"),
                                        claude_line("assistant", "The watchdog restarts it"),
                                        json.dumps({"type": "summary", "summary": "dyna-zero"})])
        r = gs.scan_file(f, "claude", gs.parse_query("watchdog"))
        self.assertEqual((r["sessionId"], r["hits"], r["cwd"]), ("aaaa-1", 2, "C:\\w\\dz"))
        self.assertIn("watchdog", r["snippet"])
        # Both words, on different lines, satisfy an AND.
        self.assertIsNotNone(gs.scan_file(f, "claude", gs.parse_query("dyna-zero restarts")))
        # An id field is not text; bookkeeping lines are not either.
        self.assertIsNone(gs.scan_file(f, "claude", gs.parse_query("dyna-uuid")))
        self.assertIsNone(gs.scan_file(f, "claude", gs.parse_query("restarts nowhere")))

    def test_text_with_quotes_and_accents(self):
        f = self.write("b.jsonl", [claude_line("user", 'He said "go" — café')])
        self.assertIsNotNone(gs.scan_file(f, "claude", gs.parse_query('"said "')))
        self.assertIsNotNone(gs.scan_file(f, "claude", gs.parse_query("CAFÉ")))
        self.assertIsNotNone(gs.scan_file(f, "claude", gs.parse_query('"go"')))

    def test_a_codex_rollout_by_its_messages_not_its_base_prompt(self):
        sid = "019a0000-1111-2222-3333-444455556666"
        f = self.write(f"rollout-2026-10-02T10-00-00-{sid}.jsonl", codex_lines("C:\\w\\cx", ["tranche sizes"]))
        r = gs.scan_file(f, "codex", gs.parse_query("tranche"))
        self.assertEqual((r["sessionId"], r["cwd"], r["hits"]), (sid, "C:\\w\\cx", 1))
        self.assertIsNone(gs.scan_file(f, "codex", gs.parse_query("dyna-zero")))
        self.assertIsNone(gs.scan_file(f, "codex", gs.parse_query("watchdog")))

    def test_every_file_in_workers_most_hits_first(self):
        files = [[self.write(f"s{i}.jsonl", [claude_line("user", "backup")] * i + [claude_line("user", "other")]),
                  "claude"] for i in range(1, 6)]
        files.append([str(self.tmp / "gone.jsonl"), "claude"])
        for workers in (1, 2):
            res = gs.scan_files(files, gs.parse_query("backup"), workers=workers)
            self.assertEqual([r["sessionId"] for r in res["found"]], ["s5", "s4", "s3", "s2", "s1"])
            self.assertFalse(res["timedOut"])

    def test_the_child_process_reads_stdin(self):
        f = self.write("c.jsonl", [claude_line("user", "margin use")])
        req = json.dumps({"q": "margin", "files": [[f, "claude"]], "workers": 1})
        out = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "global_search.py")], input=req.encode(),
                             capture_output=True, timeout=60, check=True).stdout
        self.assertEqual([r["sessionId"] for r in json.loads(out)["found"]], ["c"])

    def test_a_past_deadline_stops_early(self):
        f = self.write("d.jsonl", [claude_line("user", "x")])
        res = gs.scan_files([[f, "claude"]], gs.parse_query("x"), workers=1, deadline=-1)
        self.assertEqual(res["found"], [])
        self.assertTrue(res["timedOut"])


def room(rid, no, title, spec="", msgs=(), parts=(), pid="p1", **kw):
    return {"id": rid, "no": no, "projectId": pid, "title": title, "spec": spec, "updatedAt": no,
            "messages": [{"id": f"m{i}", "ts": i, "from": "fab", "to": "claude", "text": t}
                         for i, t in enumerate(msgs)],
            "participants": list(parts), **kw}


class Rooms(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def put(self, d):
        (self.dir / f"{d['id']}.json").write_text(json.dumps(d), encoding="utf-8")

    def test_title_number_spec_and_messages(self):
        self.put(room("room-a", 18, "Backup service", msgs=["nothing here", "the backup ran"]))
        self.put(room("room-b", 19, "Board", spec="Make the backup faster"))
        self.put(room("room-c", 20, "Other"))
        es = gs.room_entries(self.dir)
        tasks, msgs = gs.search_rooms(es, gs.parse_query("backup"))
        self.assertEqual({k: v["where"] for k, v in tasks.items()}, {"room-a": "title", "room-b": "spec"})
        self.assertEqual([(m["roomId"], m["msgId"]) for m in msgs], [("room-a", "m1")])
        tasks, _ = gs.search_rooms(es, gs.parse_query("ed-20"), {"room-c": ["#20", "ED-20"]})
        self.assertEqual(list(tasks), ["room-c"])

    def test_read_again_only_when_changed(self):
        self.put(room("room-a", 1, "One"))
        calls = []
        first = gs.room_entries(self.dir, lambda d: calls.append(d["id"]) or {"n": 1})
        again = gs.room_entries(self.dir, lambda d: calls.append(d["id"]) or {"n": 2})
        self.assertIs(first[0], again[0])
        self.assertEqual((calls, again[0]["extra"]), (["room-a"], {"n": 1}))
        self.put(room("room-a", 1, "One renamed and longer"))
        self.assertEqual(gs.room_entries(self.dir)[0]["title"], "One renamed and longer")
        (self.dir / "room-a.json").unlink()
        self.assertEqual(gs.room_entries(self.dir), [])


class GlobalFind(unittest.TestCase):
    """Hits land on the task that held the conversation, wherever it was."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        rotated = {"kind": "agent", "identity": "claude", "agent": "claude", "sessionId": "now-1",
                   "cwd": "C:\\tasks\\a\\repo", "rotations": [{"fromSessionId": "old-1", "at": 1}]}
        rooms = [room("room-a", 5, "Rotated task", parts=[rotated]),
                 room("room-b", 6, "Cwd task", parts=[{"kind": "agent", "identity": "codex", "agent": "codex",
                                                     "sessionId": "", "cwd": "C:\\tasks\\b"}]),
                 room("room-po", 0, "", msgs=["tranche sizes for the PO"])]
        for d in rooms:
            (self.dir / f"{d['id']}.json").write_text(json.dumps(d), encoding="utf-8")
        found = [{"sessionId": "old-1", "agent": "claude", "cwd": "C:\\elsewhere", "hits": 3, "snippet": "a"},
                 {"sessionId": "cx-1", "agent": "codex", "cwd": "c:\\TASKS\\b\\", "hits": 2, "snippet": "b"},
                 {"sessionId": "mine-1", "agent": "claude", "cwd": "C:\\work", "hits": 4, "snippet": "c"},
                 {"sessionId": "listed-1", "agent": "claude", "cwd": "C:\\work", "hits": 1, "snippet": "d"}]
        rows = [{"sessionId": "listed-1", "label": "My tranche notes", "cwd": "C:\\work", "agent": "claude"},
                {"sessionId": "held", "roomId": "room-a", "label": "tranche"}]
        projects = [{"id": "p1", "name": "Proj", "key": "ED", "poRoomId": "room-po"}]
        gs._ROOM_CACHE.clear()
        for target, kw in [(dashboard.chatroom, {"ROOMS_DIR": self.dir}),
                           (dashboard, {"load_projects": mock.Mock(return_value=projects),
                                        "_find_deep": mock.Mock(return_value=(200, {"found": found,
                                                                                     "timedOut": False})),
                                        "_find_session_rows": mock.Mock(return_value=rows),
                                        "load_labels": mock.Mock(return_value={"mine-1": "Named"}),
                                        "_past_row": mock.Mock(side_effect=lambda sid, a, f: {
                                            "sessionId": sid, "agent": a, "label": "", "firstWords": "hi",
                                            "cwd": "C:\\work"})})]:
            p = mock.patch.multiple(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(gs._ROOM_CACHE.clear)

    def test_quick_half_alone(self):
        st, r = dashboard.global_find("tranche")
        self.assertEqual(st, 200)
        self.assertEqual(r["conversations"], "not searched")
        dashboard._find_deep.assert_not_called()
        self.assertEqual([s["sessionId"] for s in r["sessions"]["items"]], ["listed-1"])
        self.assertEqual(r["messages"]["items"][0]["title"], "Proj · PO")
        self.assertTrue(r["messages"]["items"][0]["po"])

    def test_deep_hits_land_on_their_tasks(self):
        st, r = dashboard.global_find("tranche", deep=True, tag="t", seq=1)
        self.assertEqual((st, r["conversations"]), (200, "done"))
        by = {t["roomId"]: t for t in r["tasks"]["items"]}
        self.assertEqual(set(by), {"room-a", "room-b"})
        self.assertEqual((by["room-a"]["where"], by["room-a"]["hits"]), ("conversation", 3))
        sess = {s["sessionId"]: s for s in r["sessions"]["items"]}
        self.assertEqual(set(sess), {"mine-1", "listed-1"})
        self.assertEqual([s["sessionId"] for s in r["sessions"]["items"]], ["mine-1", "listed-1"])
        self.assertEqual((sess["mine-1"]["title"], sess["mine-1"]["row"]["label"]), ("Named", "Named"))
        self.assertNotIn("path", sess["mine-1"])
        self.assertEqual((sess["listed-1"]["title"], sess["listed-1"]["hits"]), ("My tranche notes", 1))
        self.assertNotIn("row", sess["listed-1"])

    def test_a_number_finds_its_task(self):
        _, r = dashboard.global_find("ED-6")
        self.assertEqual([t["roomId"] for t in r["tasks"]["items"]], ["room-b"])

    def test_short_or_empty(self):
        for q in ("", "a", '""'):
            _, r = dashboard.global_find(q, deep=True)
            self.assertEqual(r["tasks"]["count"] + r["sessions"]["count"] + r["messages"]["count"], 0)
        dashboard._find_deep.assert_not_called()

    def test_a_failed_deep_search_says_so(self):
        dashboard._find_deep.return_value = (409, {"error": "cancelled"})
        self.assertEqual(dashboard.global_find("tranche", deep=True)[0], 409)


class Route(unittest.TestCase):
    def test_the_hub_answers_api_find(self):
        src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
        self.assertIn('if p == "/api/find":', src)
        self.assertIn('ws_search_cancel("find:" + tag', src)


if __name__ == "__main__":
    unittest.main()
