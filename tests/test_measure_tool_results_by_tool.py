"""tools/measure_tool_results_by_tool.py on a small fixture home.

The fixture is a task room with one Claude and one Codex session, each with
a handful of tool results in and out of the window, a compaction, an rtk
marker, an image Read and a subagent transcript.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import measure_task_tool_results as base  # noqa: E402
import measure_tool_results_by_tool as m  # noqa: E402

END = base._timestamp("2026-09-21T00:00:00+00:00")
START = END - base.timedelta(days=7)
IN = "2026-09-20T10:00:{:02d}.000Z"
OLD = "2026-09-01T10:00:00.000Z"
WARN = "[rtk] /!\\ No hook installed \u2014 run `rtk init -g` for automatic token savings\n"


def _lines(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def _claude(kind: str, content, ts: str, **extra) -> dict:
    row = {"type": kind, "timestamp": ts, "message": {"role": kind, "content": content}}
    row.update(extra)
    return row


def _use(tid: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _result(tid: str, content) -> dict:
    return {"type": "tool_result", "tool_use_id": tid, "content": content}


def build_home(root: Path) -> None:
    task_dir = root / "EnsembleProjects" / "Proj" / "task_seven"
    (root / ".ensemble" / "rooms").mkdir(parents=True)
    (root / ".ensemble" / "projects.json").write_text("[]", encoding="utf-8")
    (root / ".ensemble" / "rooms" / "room-aaaa.json").write_text(json.dumps({
        "id": "room-aaaa", "launched": True, "no": 7, "title": "Seventh task",
        "taskDir": str(task_dir), "cwd": str(task_dir / "repo"),
        "participants": [
            {"kind": "agent", "agent": "claude", "sessionId": "claude-1", "cwd": str(task_dir / "repo")},
            {"kind": "agent", "agent": "codex", "sessionId": "codex-1", "cwd": str(task_dir / "repo")},
        ],
    }), encoding="utf-8")
    big = "x" * 10_000
    claude_rows = [
        # outside the window
        _claude("assistant", [_use("t9", "Read", {"file_path": "old.py"})], OLD, requestId="r9"),
        _claude("user", [_result("t9", big)], OLD),
        _claude("user", [{"type": "text", "text": "Spec.\n\nRTK is enabled for this task. Bash git..."}], IN.format(0)),
        # before the hook reached this session: no marker
        _claude("assistant", [_use("t0", "Bash", {"command": "cat x.py"})], IN.format(1), requestId="r0"),
        _claude("user", [_result("t0", "print(1)\n")], IN.format(2)),
        # through rtk
        _claude("assistant", [_use("t1", "Bash", {"command": "git status"})], IN.format(3), requestId="r1"),
        _claude("user", [_result("t1", WARN + "clean\n")], IN.format(4)),
        # two calls in one turn: a 10 KB Read and a sed the hook leaves alone
        _claude("assistant", [_use("t2", "Read", {"file_path": "C:/w/a.py"})], IN.format(5), requestId="r2"),
        _claude("assistant", [_use("t3", "Bash", {"command": 'cd "C:/w" && sed -n 1,5p a.py'})], IN.format(5), requestId="r2"),
        _claude("user", [_result("t2", big), _result("t3", "line\n")], IN.format(6)),
        # a PDF read: a document block
        _claude("assistant", [_use("t4", "Read", {"file_path": "C:/w/b.pdf"})], IN.format(7), requestId="r3"),
        _claude("user", [_result("t4", [{"type": "document", "source": {"data": "A" * 3000}}])], IN.format(8)),
        # a sidechain (in-file subagent) with its own single turn
        _claude("assistant", [_use("s1", "Grep", {"pattern": "foo", "path": "C:/w"})], IN.format(9),
                requestId="rs1", agentId="side", isSidechain=True),
        _claude("user", [_result("s1", "a.py:1:foo\n")], IN.format(10), agentId="side", isSidechain=True),
        # compaction: what came before is no longer re-sent
        _claude("user", [{"type": "text", "text": "summary"}], IN.format(11), isCompactSummary=True),
        _claude("assistant", [_use("t5", "Grep", {"pattern": "bar"})], IN.format(12), requestId="r4"),
        _claude("user", [_result("t5", "b.py:2:bar\n")], IN.format(13)),
        _claude("assistant", [{"type": "text", "text": "done"}], IN.format(14), requestId="r5"),
    ]
    project = root / ".claude" / "projects" / "C--w"
    _lines(project / "claude-1.jsonl", claude_rows)
    _lines(project / "claude-1" / "subagents" / "agent-1.jsonl", [
        _claude("assistant", [_use("u1", "Read", {"file_path": "C:/w/c.py"})], IN.format(1), requestId="q1"),
        _claude("user", [_result("u1", "y" * 2048)], IN.format(2)),
        _claude("assistant", [{"type": "text", "text": "ok"}], IN.format(3), requestId="q2"),
    ])

    def item(kind: str, payload: dict, ts: str) -> dict:
        return {"type": kind, "timestamp": ts, "payload": payload}

    def call(cid: str, script: str, ts: str) -> dict:
        return item("response_item", {"type": "custom_tool_call", "name": "exec",
                                      "call_id": cid, "input": script}, ts)

    def output(cid: str, text: str, ts: str) -> dict:
        return item("response_item", {"type": "custom_tool_call_output", "call_id": cid,
                                      "output": text}, ts)

    def tick(ts: str) -> dict:
        return item("event_msg", {"type": "token_count", "info": {}}, ts)

    codex_rows = [
        {"type": "session_meta", "timestamp": IN.format(0),
         "payload": {"id": "codex-1", "cwd": str(task_dir / "repo")}},
        call("c9", 'tools.exec_command({cmd:"git log"})', OLD),
        output("c9", "old", OLD),
        item("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Spec. RTK is available for this task's command output."}]}, IN.format(0)),
        call("c1", 'const r = await tools.exec_command({cmd:"rtk git status", workdir:"C:/w"});', IN.format(1)),
        output("c1", "ok\n", IN.format(2)),
        tick(IN.format(3)),
        call("c2", 'const r = await tools.exec_command({"cmd":"Get-Content f.txt | Select-Object -First 5","yield_time_ms":1000});', IN.format(4)),
        output("c2", "z" * 5000, IN.format(5)),
        tick(IN.format(6)),
        call("c3", 'const t = await tools.mcp__ensemble__ensemble_get_task({taskId:"room-aaaa", messages:20}); text(t);', IN.format(7)),
        output("c3", json.dumps({"task": "seven"}), IN.format(8)),
        tick(IN.format(9)),
        {"type": "compacted", "timestamp": IN.format(10), "payload": {}},
        call("c4", "text(1 + 1);", IN.format(11)),
        output("c4", "2", IN.format(12)),
        tick(IN.format(13)),
    ]
    _lines(root / ".codex" / "sessions" / "2026" / "09" / "20" / "rollout-2026-09-20T10-00-00-codex-1.jsonl",
           codex_rows)


class MeasureByToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls.tmp.name)
        build_home(cls.home)
        cls.report = m.measure(cls.home, START, END, top=3,
                               caps=[("claude", "Read", 4), ("codex", "exec_command", 2)])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def tools(self, agent: str) -> dict:
        return {row["tool"]: row for row in self.report["agents"][agent]["tools"]}

    def test_scope_and_counts(self) -> None:
        self.assertEqual(self.report["taskRooms"], 1)
        self.assertEqual(self.report["matchedSessions"],
                         {"claude": 1, "codex": 1, "claudeSubagents": 1})
        claude = self.report["agents"]["claude"]
        self.assertEqual(claude["results"], 7)  # t0 t1 t2 t3 t4 s1 t5; t9 is old
        self.assertEqual(self.report["agents"]["codex"]["results"], 4)

    def test_claude_tools_bands_and_images(self) -> None:
        tools = self.tools("claude")
        self.assertEqual(tools["Bash"]["results"], 3)
        self.assertEqual(tools["Read"]["results"], 1)
        self.assertEqual(tools["Read"]["bytes"], 10_000)
        self.assertEqual(tools["Read"]["bands"]["8-32 KB"], 1)
        self.assertEqual(tools["Read" + m.IMAGE_SUFFIX]["results"], 1)
        self.assertEqual(tools["Grep"]["results"], 2)
        self.assertEqual(tools["Bash"]["bands"]["<=2 KB"], 3)
        self.assertEqual(tools["Read"]["approxTokens"], 2500)

    def test_remaining_turns_stop_at_compaction_and_sidechain(self) -> None:
        top = {r["preview"]: r for r in self.report["agents"]["claude"]["top"]}
        # the 10 KB Read: r3 follows, then the compaction
        self.assertEqual(top["C:/w/a.py"]["remainingTurns"], 1)
        self.assertEqual(top["C:/w/a.py"]["rereadBytes"], 10_000)
        # the PDF: nothing before the compaction
        self.assertEqual(top["C:/w/b.pdf"]["remainingTurns"], 0)
        tools = self.tools("claude")
        # cat: r1 r2 r3 = 3 turns x 9 bytes; git: r2 r3 x len; sed: r3 x 5
        self.assertEqual(tools["Bash"]["rereadBytes"],
                         3 * 9 + 2 * (len(WARN.encode()) + 6) + 1 * 5)
        # sidechain Grep has no later sidechain turn; main Grep has r5
        self.assertEqual(tools["Grep"]["rereadBytes"], 0 * 11 + 1 * 11)

    def test_task_and_title_resolved(self) -> None:
        row = self.report["agents"]["claude"]["top"][0]
        self.assertEqual(row["task"], 7)
        self.assertEqual(row["title"], "Seventh task")
        self.assertEqual(self.report["agents"]["codex"]["top"][0]["task"], 7)

    def test_rtk_classification_claude(self) -> None:
        rtk = self.report["agents"]["claude"]["rtk"]
        bash = rtk["byTool"]["Bash"]
        self.assertEqual((bash["rtk"], bash["no-rtk"], bash["pre-rtk"], bash["unwired"]), (1, 1, 1, 0))
        self.assertEqual(rtk["warnings"], 1)
        self.assertEqual(rtk["noRtkFirstWords"], [{"word": "sed", "results": 1, "bytes": 5}])
        self.assertEqual(rtk["sessions"], {"withRtk": 1, "wiredWithoutRtk": 0, "unwired": 0})
        self.assertEqual(rtk["byDay"]["2026-09-20"]["rtk"], 1)

    def test_codex_tools_and_rtk(self) -> None:
        tools = self.tools("codex")
        self.assertEqual(tools["exec_command"]["results"], 2)
        self.assertEqual(tools["exec_command"]["bytes"], 5000 + 3)
        self.assertEqual(tools["mcp__ensemble__ensemble_get_task"]["results"], 1)
        self.assertEqual(tools["exec (js)"]["results"], 1)
        rtk = self.report["agents"]["codex"]["rtk"]["byTool"]["exec_command"]
        self.assertEqual((rtk["rtk"], rtk["no-rtk"]), (1, 1))
        words = self.report["agents"]["codex"]["rtk"]["noRtkFirstWords"]
        self.assertEqual(words[0]["word"], "Get-Content")
        # c1: three token counts before the compaction; c4: one after
        self.assertEqual(tools["exec_command"]["rereadBytes"], 3 * 3 + 2 * 5000)
        self.assertEqual(tools["exec (js)"]["rereadBytes"], 1)
        preview = self.report["agents"]["codex"]["top"][0]["preview"]
        self.assertEqual(preview, "Get-Content f.txt | Select-Object -First 5")

    def test_subagents_counted_apart(self) -> None:
        sub = self.report["claudeSubagents"]
        self.assertEqual(sub["results"], 1)
        self.assertEqual(sub["bytes"], 2048)
        self.assertEqual(sub["rereadBytes"], 2048)
        self.assertNotIn("C:/w/c.py", [r["preview"] for r in self.report["agents"]["claude"]["top"]])

    def test_cap_simulation(self) -> None:
        caps = {(c["agent"], c["tool"], c["capKB"]): c for c in self.report["caps"]}
        read = caps[("claude", "Read", 4)]
        self.assertEqual(read["affected"], 1)
        self.assertEqual(read["bytesSaved"], 10_000 - 4096 - m.CAP_NOTE_BYTES)
        self.assertEqual(read["rereadBytesSaved"], read["bytesSaved"] * 1)
        codex = caps[("codex", "exec_command", 2)]
        self.assertEqual(codex["affected"], 1)
        self.assertEqual(codex["bytesSaved"], 5000 - 2048 - m.CAP_NOTE_BYTES)
        self.assertEqual(codex["rereadBytesSaved"], codex["bytesSaved"] * 2)

    def test_markdown_renders(self) -> None:
        text = m._markdown(self.report)
        self.assertIn("| Read | 1 | 10,000 |", text)
        self.assertIn("#7 Seventh task", text)
        self.assertIn("## What a cap would have cut", text)
        self.assertIn("| `sed` | 1 | 5 |", text)
        json.dumps(self.report)


class HelpersTest(unittest.TestCase):
    def test_bands(self) -> None:
        self.assertEqual(m.band(2048), "<=2 KB")
        self.assertEqual(m.band(2049), "2-8 KB")
        self.assertEqual(m.band(32 * 1024), "8-32 KB")
        self.assertEqual(m.band(128 * 1024 + 1), ">128 KB")

    def test_first_word(self) -> None:
        self.assertEqual(m.first_word('cd "C:/a b" && git status'), "git")
        self.assertEqual(m.first_word("SP=/tmp/x node script.js"), "node")
        self.assertEqual(m.first_word("$p='a.py'; Get-Content $p"), "Get-Content")
        self.assertEqual(m.first_word("./gradlew.bat test"), "gradlew.bat")
        self.assertEqual(m.first_word("(cd x; ls)"), "ls")
        self.assertEqual(m.first_word(""), "(empty)")

    def test_rtk_prefixed(self) -> None:
        self.assertTrue(m._rtk_prefixed("rtk git status"))
        self.assertTrue(m._rtk_prefixed("cd x && rtk grep foo"))
        self.assertFalse(m._rtk_prefixed("git status"))
        self.assertFalse(m._rtk_prefixed("echo rtk"))

    def test_codex_tool_shapes(self) -> None:
        self.assertEqual(m.codex_tool({"name": "exec", "input": 'tools.exec_command({cmd:"git log"})'}),
                         ("exec_command", "git log"))
        self.assertEqual(m.codex_tool({"name": "exec", "input": "tools.exec_command({'cmd':'rg \"x\" .'})"}),
                         ("exec_command", 'rg "x" .'))
        self.assertEqual(m.codex_tool({"name": "exec", "input": 'const r = await tools.view_image({path:"C:\\\\a.png"});'}),
                         ("view_image", "C:\\a.png"))
        self.assertEqual(m.codex_tool({"name": "shell", "arguments": '{"command": ["git", "status"]}'}),
                         ("shell", "git status"))
        self.assertEqual(m.codex_tool({"name": "exec_command", "arguments": '{"cmd": "ls"}'}),
                         ("exec_command", "ls"))
        self.assertEqual(m.codex_tool({"name": "exec", "input": "text(1)"})[0], "exec (js)")


if __name__ == "__main__":
    unittest.main()
