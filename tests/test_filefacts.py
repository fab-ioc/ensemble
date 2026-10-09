"""What the task list read from each transcript is kept across hub starts
(filefacts, ED-200): a start reads again only the files that changed since,
and answers exactly what reading them gave. Off unless a file is named, so a
test or tool importing the dashboard never touches the hub's own."""
from __future__ import annotations

import builtins
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import filefacts  # noqa: E402
from agents import codex as codex_mod  # noqa: E402

NOW = time.time()


def forget_memory() -> None:
    """What a fresh hub process has in memory: nothing."""
    for d in (dashboard._COST_CACHE, dashboard._SAID_AT, dashboard._CODEX_COST_CACHE,
              dashboard._CODEX_SAID_AT, dashboard._CODEX_ROLLOUT_PATHS,
              dashboard._CODEX_ROLLOUT_INDEX, dashboard._TRANSCRIPT_PATHS,
              dashboard._FLU_CACHE, dashboard._CWD_CACHE, dashboard._BRANCH_CHANGES,
              codex_mod._ROLLOUT_CACHE):
        d.clear()
    dashboard._FLU_KEPT.clear()
    dashboard._TRANSCRIPT_INDEX = ("", 0.0, {})
    dashboard._SESS_CACHE.clear()


class Reads:
    """Every transcript or rollout opened, by path."""

    def __init__(self):
        self.paths: list[str] = []

    def patches(self):
        real_open, real_io_open = builtins.open, io.open

        def counting(real):
            def opener(file, *a, **k):
                name = os.fspath(file) if isinstance(file, (str, os.PathLike)) else ""
                if isinstance(name, str) and name.endswith(".jsonl"):
                    self.paths.append(os.path.normcase(name))
                return real(file, *a, **k)
            return opener
        return [mock.patch.object(builtins, "open", counting(real_open)),
                mock.patch.object(io, "open", counting(real_io_open))]


def claude_transcript(path: Path, cwd: str, turns: int, model: str = "claude-opus-4-1") -> None:
    lines = []
    for i in range(turns):
        lines.append(json.dumps({"type": "user", "cwd": cwd, "timestamp": f"2026-10-01T00:{i % 60:02d}:00Z",
                                 "message": {"role": "user", "content": f"question {i} " + "x" * 200}}))
        lines.append(json.dumps({"type": "assistant", "requestId": f"r{i}", "timestamp": f"2026-10-01T00:{i % 60:02d}:30Z",
                                 "message": {"model": model, "content": [{"type": "text", "text": f"answer {i}"}],
                                             "usage": {"input_tokens": 10, "output_tokens": 20,
                                                       "cache_creation_input_tokens": 30,
                                                       "cache_read_input_tokens": 40}}}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def codex_rollout(path: Path, sid: str, cwd: str, turns: int) -> None:
    lines = [json.dumps({"type": "session_meta",
                         "payload": {"id": sid, "cwd": cwd, "timestamp": "2026-10-01T00:00:00Z"}}),
             json.dumps({"type": "turn_context", "payload": {"model": "gpt-5-codex"}})]
    for i in range(turns):
        lines.append(json.dumps({"type": "event_msg",
                                 "payload": {"type": "user_message", "message": f"hello {i}"}}))
        lines.append(json.dumps({"type": "response_item", "timestamp": f"2026-10-01T00:{i % 60:02d}:10Z",
                                 "payload": {"type": "message", "role": "assistant",
                                             "content": [{"type": "output_text", "text": f"done {i}"}]}}))
        lines.append(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 100 * (i + 1)},
                "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 60,
                                     "output_tokens": 7}}}}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sid_of(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.base = Path(td.name)
        self.proj = self.base / "projects"
        self.codex_home = self.base / "codex"
        self.facts = self.base / "facts.json"
        filefacts.use_file(self.facts)
        self.addCleanup(filefacts.use_file, None)
        forget_memory()
        self.addCleanup(forget_memory)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(dashboard, "PROJ_DIR", self.proj))
        stack.enter_context(mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}))

    def restart(self) -> None:
        """Write what this 'hub' kept and start a fresh one on the same file."""
        filefacts.flush()
        filefacts.use_file(self.facts)
        forget_memory()


class RoundTrip(Base):
    def test_unchanged_transcript_is_not_read_again_a_changed_one_is(self):
        a = self.proj / "p" / "aaaa.jsonl"
        b = self.proj / "p" / "bbbb.jsonl"
        claude_transcript(a, "C:/w/a", 3)
        claude_transcript(b, "C:/w/b", 3)
        first_a, first_b = dashboard.compute_session_cost(a), dashboard.compute_session_cost(b)
        flu_a, flu_b = dashboard.first_last_user(a), dashboard.first_last_user(b)
        self.assertEqual(dashboard.cwd_of(a), "C:/w/a")
        said_a = dashboard._SAID_AT[a]
        self.assertGreater(said_a, 0)
        self.restart()

        claude_transcript(b, "C:/w/b", 5)          # grew: 2 more turns
        reads = Reads()
        with ExitStack() as st:
            for p in reads.patches():
                st.enter_context(p)
            again_a = dashboard.compute_session_cost(a)
            again_b = dashboard.compute_session_cost(b)
            self.assertEqual(dashboard.first_last_user(a), flu_a)
            self.assertEqual(dashboard.cwd_of(a), "C:/w/a")
        self.assertEqual(again_a, first_a)
        self.assertEqual(dashboard._SAID_AT[a], said_a, "the news time comes back with the cost")
        self.assertNotIn(os.path.normcase(str(a)), reads.paths, "an unchanged transcript is not read")
        self.assertIn(os.path.normcase(str(b)), reads.paths, "a changed one is")
        self.assertEqual(again_b["tokens"]["output"], 5 * 20)
        self.assertNotEqual(again_b, first_b)
        self.assertEqual(dashboard.first_last_user(b)[2], 5)
        self.assertEqual(flu_b[2], 3)

    def test_codex_rollouts_round_trip(self):
        sid = sid_of(7)
        f1 = self.codex_home / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T00-00-00-{sid}.jsonl"
        f2 = self.codex_home / "sessions" / "2026" / "10" / "02" / f"rollout-2026-10-02T00-00-00-{sid}.jsonl"
        codex_rollout(f1, sid, "C:/w/c", 2)
        codex_rollout(f2, sid, "C:/w/c", 1)
        cost = dashboard.compute_codex_session_cost(sid)
        said = dashboard._CODEX_SAID_AT[sid]
        parsed = codex_mod._parse_rollout(f1)
        self.assertEqual(cost["tokens"], {"input": 120, "output": 21, "cacheWrite": 0, "cacheRead": 180})
        self.restart()

        codex_rollout(f2, sid, "C:/w/c", 2)        # the resumed file grew
        reads = Reads()
        with ExitStack() as st:
            for p in reads.patches():
                st.enter_context(p)
            again = dashboard.compute_codex_session_cost(sid)
            self.assertEqual(codex_mod._parse_rollout(f1), parsed)
        self.assertNotIn(os.path.normcase(str(f1)), reads.paths)
        self.assertIn(os.path.normcase(str(f2)), reads.paths)
        self.assertEqual(again["tokens"], {"input": 160, "output": 28, "cacheWrite": 0, "cacheRead": 240})
        self.assertGreaterEqual(dashboard._CODEX_SAID_AT[sid], said)

    def test_another_version_is_read_again(self):
        a = self.proj / "p" / "aaaa.jsonl"
        claude_transcript(a, "C:/w/a", 2)
        dashboard.compute_session_cost(a)
        filefacts.flush()
        d = json.loads(self.facts.read_text(encoding="utf-8"))
        d["v"] = filefacts.VERSION + 1
        self.facts.write_text(json.dumps(d), encoding="utf-8")
        filefacts.use_file(self.facts)
        forget_memory()
        reads = Reads()
        with ExitStack() as st:
            for p in reads.patches():
                st.enter_context(p)
            dashboard.compute_session_cost(a)
        self.assertIn(os.path.normcase(str(a)), reads.paths)

    def test_off_without_a_file(self):
        filefacts.use_file(None)
        a = self.proj / "p" / "aaaa.jsonl"
        claude_transcript(a, "C:/w/a", 1)
        dashboard.compute_session_cost(a)
        self.assertIsNone(filefacts.get("cost", a, filefacts.sig_of(a.stat())))
        self.assertIsNone(filefacts._FACTS, "nothing loaded or kept")
        filefacts.flush()
        self.assertFalse(self.facts.exists())

    def test_gone_files_are_dropped_on_the_first_write(self):
        a = self.proj / "p" / "aaaa.jsonl"
        b = self.proj / "p" / "bbbb.jsonl"
        claude_transcript(a, "C:/w/a", 1)
        claude_transcript(b, "C:/w/b", 1)
        dashboard.compute_session_cost(a)
        dashboard.compute_session_cost(b)
        b.unlink()
        filefacts.flush()
        kept = json.loads(self.facts.read_text(encoding="utf-8"))["facts"]
        self.assertTrue(any(str(a) in k for k in kept))
        self.assertFalse(any(str(b) in k for k in kept))


class ColdTaskList(Base):
    """A synthetic history: a few hundred transcripts held by tasks. A second
    start on the same files parses none of them."""

    ROOMS = 60
    CLAUDE_PER_ROOM = 4     # the owner's current conversation and 3 rotations
    CODEX_PER_ROOM = 2      # the reviewer's current one and an earlier review

    def setUp(self):
        super().setUp()
        self.rooms = []
        n = 0
        for r in range(self.ROOMS):
            cl = []
            for _ in range(self.CLAUDE_PER_ROOM):
                n += 1
                cl.append(sid_of(n))
                claude_transcript(self.proj / f"p{r % 7}" / f"{cl[-1]}.jsonl", f"C:/w/t{r}", 40)
            cx = []
            for _ in range(self.CODEX_PER_ROOM):
                n += 1
                cx.append(sid_of(n))
                codex_rollout(self.codex_home / "sessions" / "2026" / "10" / f"{1 + r % 9:02d}"
                              / f"rollout-2026-10-01T00-00-00-{cx[-1]}.jsonl", cx[-1], f"C:/w/t{r}/cx", 40)
            self.rooms.append({
                "id": f"room{r}", "title": f"t{r}", "createdAt": 1, "updatedAt": 2, "messages": [],
                "participants": [
                    {"kind": "agent", "agent": "claude", "identity": "claude", "cwd": f"C:/w/t{r}",
                     "sessionId": cl[0],
                     "rotations": [{"fromSessionId": s, "at": 1} for s in cl[1:]]},
                    {"kind": "agent", "agent": "codex", "identity": "codex", "cwd": f"C:/w/t{r}/cx",
                     "sessionId": cx[0], "reviews": [{"sessionId": s} for s in cx[1:]]},
                ]})
        # A few sessions no task holds, as rows of their own.
        for i in range(20):
            n += 1
            claude_transcript(self.proj / "solo" / f"{sid_of(n)}.jsonl", f"C:/solo/{i}", 5)
        self.files = len(list(self.proj.glob("*/*.jsonl"))) + len(list(self.codex_home.glob("sessions/*/*/*/*.jsonl")))

    def load(self) -> list[dict]:
        with ExitStack() as st:
            for p in (
                mock.patch.object(dashboard.chatroom, "list_rooms", return_value=self.rooms),
                mock.patch.object(dashboard, "load_projects", return_value=[]),
                mock.patch.object(dashboard, "load_live", return_value=[]),
                mock.patch.object(dashboard, "_room_is_live", return_value=False),
                mock.patch.object(dashboard, "_read_agent_session_files", return_value=[]),
                mock.patch.object(dashboard, "load_labels", return_value={}),
                mock.patch.object(dashboard, "load_parents", return_value={}),
                mock.patch.object(dashboard, "load_archived", return_value=set()),
                mock.patch.object(dashboard, "load_jira_links", return_value={}),
                mock.patch.object(dashboard, "load_jira_unlinks", return_value={}),
                mock.patch.object(dashboard.attention, "by_room", return_value={}),
                mock.patch.object(dashboard.attention, "waiting_on_po", return_value={}),
                mock.patch.object(dashboard.attention, "reported_by_room", return_value={}),
                mock.patch.object(dashboard.attention, "run_by_room", return_value={}),
            ):
                st.enter_context(p)
            return dashboard._load_sessions_uncached(self.ROOMS)

    def test_second_start_parses_no_unchanged_file(self):
        self.assertGreaterEqual(self.files, 300)
        t0 = time.perf_counter()
        first = self.load()
        cold = time.perf_counter() - t0
        rooms_first = {r["roomId"]: (r.get("cost"), r.get("costTokens"), r.get("newsAt"))
                       for r in first if r.get("roomId")}
        self.assertEqual(len(rooms_first), self.ROOMS)
        self.assertTrue(all(v[1] and v[1]["output"] for v in rooms_first.values()))
        self.restart()

        reads = Reads()
        with ExitStack() as st:
            for p in reads.patches():
                st.enter_context(p)
            t0 = time.perf_counter()
            second = self.load()
            again = time.perf_counter() - t0
        rooms_second = {r["roomId"]: (r.get("cost"), r.get("costTokens"), r.get("newsAt"))
                        for r in second if r.get("roomId")}
        self.assertEqual(rooms_second, rooms_first, "the same list, from what was kept")
        held = {os.path.normcase(str(p)) for p in self.proj.glob("p*/*.jsonl")}
        held |= {os.path.normcase(str(p)) for p in self.codex_home.glob("sessions/*/*/*/*.jsonl")}
        reparsed = sorted(set(reads.paths) & held)
        self.assertEqual(reparsed, [], f"{len(reparsed)} unchanged files were read again")
        self.assertLess(again, cold, f"a start with every fact kept ({again:.2f}s) "
                                     f"is quicker than one with none ({cold:.2f}s)")


class TranscriptIndex(Base):
    def test_one_listing_serves_every_lookup_and_a_new_file_is_found(self):
        a = self.proj / "p1" / "aaaa.jsonl"
        claude_transcript(a, "C:/w", 1)
        self.assertEqual(dashboard.find_transcript("aaaa"), a)
        self.assertIsNone(dashboard.find_transcript("nope"))
        b = self.proj / "p2" / "bbbb.jsonl"
        claude_transcript(b, "C:/w", 1)
        # Written after the listing, within its TTL: still found.
        self.assertEqual(dashboard.find_transcript("bbbb"), b)
        a.unlink()
        with mock.patch.object(dashboard, "_TRANSCRIPT_INDEX_TTL_S", 0.0):
            self.assertIsNone(dashboard.find_transcript("aaaa"))
        self.assertIsNone(dashboard.find_transcript("*"))

    def test_codex_index_matches_the_glob(self):
        sid = sid_of(3)
        f = self.codex_home / "sessions" / "2026" / "10" / "01" / f"rollout-2026-10-01T00-00-00-{sid}.jsonl"
        codex_rollout(f, sid, "C:/w", 1)
        self.assertEqual(dashboard._codex_rollouts(sid), [f])
        self.assertEqual(dashboard._codex_rollouts(sid_of(4)), [])
        dashboard._CODEX_ROLLOUT_PATHS.clear()
        g = f.with_name(f"rollout-2026-10-01T00-00-00-{sid_of(5)}.jsonl")
        codex_rollout(g, sid_of(5), "C:/w", 1)
        self.assertEqual(dashboard._codex_rollouts(sid_of(5)), [g], "written after the listing")


if __name__ == "__main__":
    unittest.main()
