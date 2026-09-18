"""A past session no task holds stays in the list, however many transcripts the
hub's own tasks wrote after it: the window counts the rows kept, not the files
looked at. And however many tasks there are: the sessions outside every task
have a window of their own (SESSION_LIST_OWN_CAP of each kind, a year back),
the caller's `n` bounds the finished tasks alone, and a task someone needs
(running, asking, a PO, in any column before Done) is never cut. The list
carries a spec's revision, not the spec: the Spec pane asks for the text."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from agents.codex import CodexAgent  # noqa: E402

NOW = time.time()
DAY = 86400


class Bench:
    """A throwaway transcripts folder, Codex home and set of rooms."""

    def __init__(self, base: Path):
        self.base = base
        self.transcripts = base / "transcripts"
        self.codex_home = base / "codex"
        self.scratch = base / "cs"
        for d in (self.transcripts, self.codex_home, self.scratch):
            d.mkdir()
        self.rooms: list[dict] = []
        self.labels: dict[str, str] = {}
        self.projects: list[dict] = []
        self.attention: dict[str, dict] = {}

    def claude(self, sid: str, cwd: str, age_days: float, turns: int = 1,
               folder: str = "proj", text: str = "hello") -> Path:
        d = self.transcripts / folder
        d.mkdir(exist_ok=True)
        p = d / f"{sid}.jsonl"
        lines = [json.dumps({"type": "summary", "cwd": cwd})]
        lines += [json.dumps({"type": "user", "cwd": cwd,
                              "message": {"role": "user", "content": f"{text} {i}"}})
                  for i in range(turns)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(p, (NOW - age_days * DAY,) * 2)
        return p

    def codex(self, sid: str, cwd: str, age_days: float, turns: int = 1) -> Path:
        d = self.codex_home / "sessions" / "2026" / "09" / "01"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"rollout-2026-09-01T00-00-00-{sid}.jsonl"
        lines = [json.dumps({"type": "session_meta",
                             "payload": {"id": sid, "cwd": cwd, "timestamp": "2026-09-01T00:00:00Z"}})]
        lines += [json.dumps({"type": "event_msg",
                              "payload": {"type": "user_message", "message": f"hello {i}"}})
                  for i in range(turns)]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(p, (NOW - age_days * DAY,) * 2)
        return p

    def room(self, rid: str, agent: str, cwd: str, session_id: str = "", **fields) -> None:
        self.rooms.append({
            "id": rid, "title": rid, "createdAt": 1, "updatedAt": 2, "messages": [],
            "participants": [{"kind": "agent", "agent": agent, "identity": agent,
                              "cwd": cwd, "sessionId": session_id}],
            **fields,
        })

    def load(self, n: int, extra=(), own_cap: int | None = None) -> list[dict]:
        """`own_cap`: the window of the sessions no task holds; `n` when not
        given, as the two were one number before."""
        patches = [
            mock.patch.object(dashboard, "SESSION_LIST_OWN_CAP", n if own_cap is None else own_cap),
            mock.patch.object(dashboard, "load_projects", return_value=list(self.projects)),
            mock.patch.object(dashboard, "_room_is_live", side_effect=lambda rm: bool(rm.get("running"))),
            mock.patch.object(dashboard.attention, "by_room", return_value=dict(self.attention)),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}),
            mock.patch.object(dashboard, "PROJ_DIR", self.transcripts),
            mock.patch.object(dashboard, "CS_ROOT", self.scratch),
            mock.patch.object(dashboard, "load_live", return_value=[]),
            mock.patch.object(dashboard, "load_labels", return_value=dict(self.labels)),
            mock.patch.object(dashboard, "load_parents", return_value={}),
            mock.patch.object(dashboard, "load_archived", return_value=set()),
            mock.patch.object(dashboard, "load_jira_links", return_value={}),
            mock.patch.object(dashboard, "load_jira_unlinks", return_value={}),
            mock.patch.object(dashboard, "_read_agent_session_files", return_value=[]),
            mock.patch.object(dashboard.chatroom, "list_rooms", return_value=self.rooms),
            mock.patch.object(dashboard, "compute_room_cost", return_value={"dollars": 0}),
            *extra,
        ]
        with ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return dashboard._load_sessions_uncached(n)


def plain(rows: list[dict]) -> list[str]:
    return [r["sessionId"] for r in rows if not r.get("headless")]


class ClaudeWindow(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.outside = str(self.b.base / "SigmaTrader")
        self.task_cwd = str(self.b.base / "tasks" / "t1" / "repo")

    def many_task_transcripts(self, count: int = 12) -> None:
        """More than n transcripts newer than anything else: reviewers and
        rotations in the task's folder, and one owner known by its session id
        that runs elsewhere."""
        self.b.room("room-t1", "claude", self.task_cwd)
        self.b.room("room-t2", "claude", str(self.b.base / "tasks" / "t2"), session_id="owner-by-id")
        for i in range(count):
            self.b.claude(f"held-{i}", self.task_cwd, age_days=0.001 * (i + 1), folder="task")
        self.b.claude("owner-by-id", str(self.b.base / "moved"), age_days=0.0005, folder="task")

    def test_an_older_session_outside_any_task_is_returned(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        rows = self.b.load(5)
        self.assertEqual(plain(rows), ["mine"])
        self.assertEqual(sorted(r["sessionId"] for r in rows if r.get("headless")),
                         ["room-t1", "room-t2"], "the tasks are the same rows as ever")

    def test_the_other_rules_still_hold(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        self.b.claude("shell", self.outside, age_days=21, turns=0)
        self.b.claude("named-shell", self.outside, age_days=22, turns=0)
        self.b.labels["named-shell"] = "kept by its name"
        # The same conversation copied into another folder: the newest copy only.
        self.b.claude("mine", str(self.b.base / "copy"), age_days=30, folder="copy", text="older copy")
        self.b.claude("rename-1", str(dashboard.RENAME_WORKSPACE), age_days=23,
                      folder=dashboard._RENAME_PROJ_SLUG)
        self.b.claude("rename-2", self.outside, age_days=24,
                      text=dashboard.RENAME_PROMPT_PREFIX)
        self.b.claude("rename-3", self.outside, age_days=25, folder="x-rename-workspace-x")
        rows = self.b.load(5)
        self.assertEqual(plain(rows), ["mine", "named-shell"])
        mine = next(r for r in rows if r["sessionId"] == "mine")
        self.assertEqual(mine["cwd"], self.outside)
        self.assertTrue(mine["first"].startswith("hello"))

    def test_n_counts_the_rows_kept_newest_first(self):
        self.many_task_transcripts()
        for i in range(4):
            self.b.claude(f"mine-{i}", self.outside, age_days=10 + i)
        self.assertEqual(plain(self.b.load(2)), ["mine-0", "mine-1"])

    def test_past_the_window_nothing_older_than_a_year(self):
        self.many_task_transcripts()
        self.b.claude("recent", self.outside, age_days=300)
        self.b.claude("ancient", self.outside, age_days=400)
        self.assertEqual(plain(self.b.load(5)), ["recent"])
        # Inside the newest n files its age never mattered, and still does not.
        self.assertEqual(plain(self.b.load(50)), ["recent", "ancient"])

    def test_a_tasks_transcripts_are_not_read(self):
        self.many_task_transcripts()
        self.b.claude("mine", self.outside, age_days=20)
        self.b.load(5)      # every file's folder is now remembered
        real_open = Path.open
        opened: list[str] = []

        def spy(path, *a, **kw):
            opened.append(path.name)
            return real_open(path, *a, **kw)
        flu = mock.patch.object(dashboard, "first_last_user", wraps=dashboard.first_last_user)
        with flu as flu_spy:
            rows = self.b.load(5, extra=[mock.patch.object(Path, "open", spy)])
        self.assertEqual(plain(rows), ["mine"])
        self.assertEqual([c.args[0].stem for c in flu_spy.call_args_list], ["mine"])
        self.assertFalse([n for n in opened if n.startswith(("held-", "owner-"))],
                         "a task's transcript is opened on no later load")


class CodexWindow(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.outside = str(self.b.base / "elsewhere")
        self.task_cwd = str(self.b.base / "tasks" / "t1" / "codex")
        self.b.room("room-t1", "codex", self.task_cwd)
        for i in range(12):
            self.b.codex(f"0000000{i:02d}-held", self.task_cwd, age_days=0.001 * (i + 1))
        self.b.codex("shell", self.outside, age_days=5, turns=0)
        self.b.codex("mine", self.outside, age_days=20)
        self.b.codex("ancient", self.outside, age_days=400)

    def test_an_older_codex_session_outside_any_task_is_returned(self):
        self.assertEqual(plain(self.b.load(5)), ["mine"])

    def test_the_reader_alone_keeps_its_window_in_files(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.b.codex_home)}):
            got = CodexAgent().list_sessions(limit=5)
            self.assertEqual(len(got), 5)
            self.assertTrue(all(s.cwd == self.task_cwd for s in got))
            kept = CodexAgent().list_sessions(
                limit=1, dropped=lambda s: s.cwd == self.task_cwd or s.turns == 0,
                max_age=365 * DAY)
            self.assertEqual(kept[-1].session_id, "mine")
            far = CodexAgent().list_sessions(
                limit=5, dropped=lambda s: s.cwd == self.task_cwd, max_age=365 * DAY)
            self.assertNotIn("ancient", [s.session_id for s in far])


def tasks(rows: list[dict]) -> list[str]:
    return [r["sessionId"] for r in rows if r.get("headless")]


class TaskCut(unittest.TestCase):
    """`n` bounds the finished tasks alone. No number of tasks pushes out a
    session outside every task, and a task someone still needs is never cut."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.b = Bench(Path(td.name))
        self.outside = str(self.b.base / "SigmaTrader")

    def done_tasks(self, count: int, **fields) -> None:
        """Finished tasks, done-0 the most recently active, all of them newer
        and of a higher priority than anything outside a task."""
        for i in range(count):
            self.b.room(f"done-{i}", "claude", str(self.b.base / "tasks" / f"d{i}"),
                        workflow="done", priority=1, updatedAt=NOW - i, **fields)

    def test_no_number_of_tasks_pushes_out_a_session_outside_them(self):
        self.done_tasks(12)
        for i in range(6):
            self.b.claude(f"mine-{i}", self.outside, age_days=30 + i)
            self.b.codex(f"0000000{i}-codex-mine", self.outside, age_days=40 + i)
        rows = self.b.load(4, own_cap=10)
        self.assertEqual(sorted(plain(rows)),
                         sorted([f"mine-{i}" for i in range(6)]
                                + [f"0000000{i}-codex-mine" for i in range(6)]))
        self.assertEqual(sorted(tasks(rows)), ["done-0", "done-1", "done-2", "done-3"],
                         "the most recently active finished tasks")

    def test_the_age_bound_holds_past_the_window(self):
        self.done_tasks(3)
        held = str(self.b.base / "tasks" / "d0")
        for i in range(5):      # the newest files are a task's: the window is walked past
            self.b.claude(f"held-{i}", held, age_days=0.001 * (i + 1), folder="task")
            self.b.codex(f"0000001{i}-held", held, age_days=0.001 * (i + 1))
        for i in range(3):
            self.b.claude(f"mine-{i}", self.outside, age_days=300 + i)
            self.b.codex(f"0000000{i}-codex-mine", self.outside, age_days=300 + i)
        self.b.claude("ancient", self.outside, age_days=400)
        self.b.codex("00000009-ancient", self.outside, age_days=400)
        got = plain(self.b.load(300, own_cap=5))
        self.assertEqual(sorted(got), sorted([f"mine-{i}" for i in range(3)]
                                             + [f"0000000{i}-codex-mine" for i in range(3)]))

    def test_the_own_window_counts_each_kind(self):
        for i in range(5):
            self.b.claude(f"mine-{i}", self.outside, age_days=10 + i)
            self.b.codex(f"0000000{i}-codex-mine", self.outside, age_days=10 + i)
        got = plain(self.b.load(300, own_cap=2))
        self.assertEqual(sorted(got), ["00000000-codex-mine", "00000001-codex-mine", "mine-0", "mine-1"])

    def test_a_task_someone_needs_is_never_cut(self):
        self.done_tasks(8)
        old = NOW - 90 * DAY
        where = str(self.b.base / "tasks")
        self.b.room("running", "claude", where + "/r", workflow="done", running=True, updatedAt=old)
        self.b.room("running-codex", "codex", where + "/rc", workflow="done", running=True, updatedAt=old)
        self.b.room("asks", "claude", where + "/a", workflow="done", updatedAt=old)
        self.b.attention["asks"] = {"state": "waiting_for_you", "reason": "a question"}
        self.b.room("draft", "claude", where + "/dr", launched=False, updatedAt=old)
        self.b.room("todo", "claude", where + "/t", workflow="todo", updatedAt=old)
        self.b.room("in-review", "claude", where + "/ir", workflow="inreview", updatedAt=old)
        self.b.room("the-po", "claude", where + "/po", workflow="done", updatedAt=old)
        self.b.projects.append({"id": "p1", "name": "P", "path": where, "poRoomId": "the-po"})
        self.b.room("old-done", "claude", where + "/od", workflow="done", updatedAt=old)
        needed = ["asks", "draft", "in-review", "running", "running-codex", "the-po", "todo"]
        for n in (2, 7, 9):
            got = tasks(self.b.load(n))
            self.assertEqual(sorted(set(got) & set(needed)), needed, f"n={n}")
            self.assertNotIn("old-done", got)
            # What is left of n goes to the finished ones, newest first.
            self.assertEqual(sorted(set(got) - set(needed)),
                             [f"done-{i}" for i in range(max(0, n - len(needed)))], f"n={n}")

    def test_a_running_tasks_own_transcripts_stay_its_own(self):
        """Past the cut a task's sessions do not come back as rows of their own."""
        self.done_tasks(6)
        cwd = str(self.b.base / "tasks" / "d5")     # done-5 is cut at n=2
        self.b.claude("held", cwd, age_days=1, folder="task")
        self.b.codex("00000001-held", cwd, age_days=1)
        rows = self.b.load(2)
        self.assertEqual(tasks(rows), ["done-0", "done-1"])
        self.assertEqual(plain(rows), [])

    def test_the_list_carries_the_specs_revision_not_the_spec(self):
        self.b.room("with-spec", "claude", str(self.b.base / "t1"), spec="# Do it\n" + "x" * 5000)
        self.b.room("no-spec", "claude", str(self.b.base / "t2"), spec="  ")
        by = {r["sessionId"]: r for r in self.b.load(5)}
        self.assertNotIn("spec", by["with-spec"])
        self.assertEqual(by["with-spec"]["specRev"], dashboard._spec_rev("# Do it\n" + "x" * 5000))
        self.assertEqual(len(by["with-spec"]["specRev"]), 10)
        self.assertEqual(by["no-spec"]["specRev"], "")
        self.assertNotEqual(dashboard._spec_rev("a"), dashboard._spec_rev("b"))
        self.assertLess(len(json.dumps(by["with-spec"])), 1500)


class SpecOnRequest(unittest.TestCase):
    """The text the list no longer carries: /api/room?id=…&part=spec."""

    def call(self, path: str):
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = path, "GET", "HTTP/1.1"
        h.requestline = f"GET {path} HTTP/1.1"
        h.headers = {"Content-Length": "0", "Host": "127.0.0.1:8798"}
        h.rfile, h.wfile = io.BytesIO(b""), io.BytesIO()
        h.client_address = ("127.0.0.1", 50000)
        h.server = SimpleNamespace(server_address=("127.0.0.1", 8798))
        h.log_message = lambda *a: None
        h.do_GET()
        head, _, payload = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(payload.decode("utf-8"))

    def test_the_spec_alone(self):
        room = {"id": "room-abc", "spec": "# Do it\nétape 1", "messages": [{"text": "big"}] * 50,
                "participants": []}
        with mock.patch.object(dashboard.chatroom, "get_room",
                               side_effect=lambda rid, **k: dict(room) if rid == "room-abc" else None):
            status, body = self.call("/api/room?id=room-abc&part=spec")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"id": "room-abc", "spec": room["spec"],
                                    "specRev": dashboard._spec_rev(room["spec"])})
            status, body = self.call("/api/room?id=room-nope&part=spec")
            self.assertEqual(status, 404)
            status, body = self.call("/api/room?id=room-abc")
            self.assertEqual(len(body["messages"]), 50, "without part= the room is whole, as ever")


PAGE_JS = r"""
let SELECTED_SID = 'room-a', renders = 0, now = 1000, fail = false;
const asked = [], waiting = [];
Date.now = () => now;
const issueEmpty = m => 'EMPTY:' + m, mdIn = (r, t) => 'MD:' + t;
function renderDetail() { renders++; }
function api(url) {
  asked.push(url);
  const n = asked.length;         // an answer without specRev: a hub that does not send one
  return new Promise((ok, no) => waiting.push(reply => fail ? no(new Error('down')) : ok(reply || { spec: 'text of ' + n })));
}
const answer = async reply => { waiting.shift()(reply); await new Promise(r => setTimeout(r, 0)); };
const many = (row, times) => { let last; for (let i = 0; i < times; i++) last = specPaneHtml(row); return last; };
%s
(async () => {
  const out = {};
  const row = { roomId: 'room-a', specRev: 'r1' };
  out.first = specPaneHtml(row);
  specPaneHtml(row); specPaneHtml(row);                 // polls while the answer is awaited
  out.askedOnce = asked.slice();
  await answer();
  out.rendersAfter = renders;
  out.shown = specPaneHtml(row);
  specPaneHtml(row);
  out.askedStill = asked.length;
  row.specRev = 'r2';                                   // the spec was amended
  out.staysUp = specPaneHtml(row);
  await answer();
  out.newer = specPaneHtml(row);
  out.askedTwice = asked.length;
  // A failed request is made again, not on every poll.
  fail = true;
  const other = { roomId: 'room-b', specRev: 'x' };
  specPaneHtml(other); await answer();
  specPaneHtml(other); out.noRetryYet = asked.length;
  now += 6000; fail = false;
  specPaneHtml(other); await answer();
  out.afterRetry = specPaneHtml(other);
  out.rendersForOther = renders;                        // room-b is not the open task
  // Rows that ask for nothing.
  const before = asked.length;
  out.noSpec = specPaneHtml({ roomId: 'room-c', specRev: '' });
  out.plain = specPaneHtml({ sessionId: 'sid' });
  out.carried = specPaneHtml({ roomId: 'room-d', spec: 'as sent' });      // a hub not restarted yet
  out.askedForThose = asked.length - before;
  // The spec is amended while its text is on its way: the answer is kept under
  // the revision it names, whichever that is.
  let at = asked.length;
  const e = { roomId: 'room-e', specRev: 'A' };
  specPaneHtml(e); e.specRev = 'B'; many(e, 3);
  out.pendingAsks = asked.length - at;
  await answer({ spec: 'B text', specRev: 'B' });
  out.pendingNewer = many(e, 5); out.pendingNewerAsks = asked.length - at;
  at = asked.length;
  const g = { roomId: 'room-g', specRev: 'A' };
  specPaneHtml(g); g.specRev = 'B';
  await answer({ spec: 'A text', specRev: 'A' });        // read before the amendment
  specPaneHtml(g); out.pendingOlderAsks = asked.length - at;
  await answer({ spec: 'B text', specRev: 'B' });
  out.pendingOlder = many(g, 5); out.pendingOlderAsksAfter = asked.length - at;
  // A -> B -> A: the list saw A, the text read was B's, and the spec is A again
  // before the list is read again.
  at = asked.length;
  const f = { roomId: 'room-f', specRev: 'A' };
  specPaneHtml(f);
  await answer({ spec: 'B text', specRev: 'B' });
  out.abaMeanwhile = many(f, 10); out.abaNoLoop = asked.length - at;
  now += 6000;
  specPaneHtml(f); out.abaAskedAgain = asked.length - at;
  await answer({ spec: 'A text', specRev: 'A' });
  out.abaSettled = many(f, 10); out.abaAsksInAll = asked.length - at;
  console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SpecPane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        page = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        i = page.index("// ---- A task's spec, on request: begin")
        block = page[i:page.index("// ---- A task's spec, on request: end", i)]
        r = subprocess.run([shutil.which("node"), "-"], input=PAGE_JS % block, capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_asked_once_per_task_and_again_when_it_changes(self):
        o = self.out
        self.assertEqual(o["first"], "EMPTY:Loading…")
        self.assertEqual(o["askedOnce"], ["/api/room?id=room-a&part=spec"])
        self.assertEqual(o["rendersAfter"], 1, "the open task's panel is drawn again when its spec arrives")
        self.assertEqual(o["shown"], '<div class="dp-spec">MD:text of 1</div>')
        self.assertEqual(o["askedStill"], 1)
        self.assertEqual(o["staysUp"], o["shown"], "the spec shown stays up while the amended one loads")
        self.assertEqual(o["newer"], '<div class="dp-spec">MD:text of 2</div>')
        self.assertEqual(o["askedTwice"], 2)

    def test_a_failed_request_is_made_again_later(self):
        o = self.out
        self.assertEqual(o["noRetryYet"], 3)
        self.assertEqual(o["afterRetry"], '<div class="dp-spec">MD:text of 4</div>')
        self.assertEqual(o["rendersForOther"], 2, "only room-a's two answers drew the panel")

    def test_the_spec_amended_while_its_text_is_on_its_way(self):
        o = self.out
        self.assertEqual(o["pendingAsks"], 1, "one request at a time for a task")
        self.assertEqual(o["pendingNewer"], '<div class="dp-spec">MD:B text</div>')
        self.assertEqual(o["pendingNewerAsks"], 1, "the answer was already the amended spec")
        self.assertEqual(o["pendingOlderAsks"], 2, "an answer older than the list is followed by a request at once")
        self.assertEqual(o["pendingOlder"], '<div class="dp-spec">MD:B text</div>')
        self.assertEqual(o["pendingOlderAsksAfter"], 2)

    def test_amended_and_put_back_never_leaves_the_wrong_text(self):
        o = self.out
        self.assertEqual(o["abaMeanwhile"], '<div class="dp-spec">MD:B text</div>')
        self.assertEqual(o["abaNoLoop"], 1, "an answer the list does not agree with is not asked for again at once")
        self.assertEqual(o["abaAskedAgain"], 2)
        self.assertEqual(o["abaSettled"], '<div class="dp-spec">MD:A text</div>')
        self.assertEqual(o["abaAsksInAll"], 2)

    def test_rows_that_need_no_request(self):
        o = self.out
        self.assertTrue(o["noSpec"].startswith("EMPTY:No spec"))
        self.assertTrue(o["plain"].startswith("EMPTY:No spec"))
        self.assertEqual(o["carried"], '<div class="dp-spec">MD:as sent</div>')
        self.assertEqual(o["askedForThose"], 0)


if __name__ == "__main__":
    unittest.main()
