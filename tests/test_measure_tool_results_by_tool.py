"""tools/measure_tool_results_by_tool.py on a small fixture home.

The fixture is a task room with one Claude and one Codex session, each with
a handful of tool results in and out of the window, a compaction, rtk
evidence of several kinds, an image Read and a subagent transcript.  The
Codex rollout follows the real item order: call, usage record, completion
event, output, token count.
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
GUIDANCE = "If a recovery hint names hidden output you need, run `rtk recall <hash> --full`.\n"


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


def _item(kind: str, payload: dict, ts: str) -> dict:
    return {"type": kind, "timestamp": ts, "payload": payload}


def _call(cid: str, script: str, ts: str) -> list:
    """A Codex exec call as the rollout records it, up to its output."""
    return [
        _item("response_item", {"type": "custom_tool_call", "name": "exec",
                                "call_id": cid, "input": script}, ts),
        {"type": "token_usage_record", "timestamp": ts, "payload": {}},
        _item("event_msg", {"type": "item_completed"}, ts),
    ]


def _output(cid: str, text: str, ts: str) -> list:
    return [
        _item("response_item", {"type": "custom_tool_call_output", "call_id": cid,
                                "output": text}, ts),
        _item("event_msg", {"type": "token_count", "info": {}}, ts),
    ]


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
        # briefed session, first command is one the hook does not rewrite: no evidence
        _claude("assistant", [_use("t0", "Bash", {"command": "cat x.py"})], IN.format(1), requestId="r0"),
        _claude("user", [_result("t0", "print(1)\n")], IN.format(2)),
        # rtk ran: its warning line is in the result
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
        # quoted rtk in the command and rtk guidance in the output are not evidence
        _claude("assistant", [_use("t6", "Bash", {"command": 'echo "hello; rtk git status"'})], IN.format(14), requestId="r5"),
        _claude("user", [_result("t6", "hello; rtk git status\n")], IN.format(15)),
        _claude("assistant", [_use("t7", "Bash", {"command": "sed -n 1,3p SKILL.md"})], IN.format(16), requestId="r6"),
        _claude("user", [_result("t7", GUIDANCE)], IN.format(17)),
        # the real recovery hint is evidence
        _claude("assistant", [_use("t8", "PowerShell", {"command": "rtk pytest -q"})], IN.format(18), requestId="r7"),
        _claude("user", [_result("t8", "1 failed\n[full output: rtk recall e113d27fab59]\n")], IN.format(19)),
        _claude("assistant", [{"type": "text", "text": "done"}], IN.format(20), requestId="r8"),
    ]
    project = root / ".claude" / "projects" / "C--w"
    _lines(project / "claude-1.jsonl", claude_rows)
    _lines(project / "claude-1" / "subagents" / "agent-1.jsonl", [
        _claude("assistant", [_use("u1", "Read", {"file_path": "C:/w/c.py"})], IN.format(1), requestId="q1"),
        _claude("user", [_result("u1", "y" * 2048)], IN.format(2)),
        _claude("assistant", [{"type": "text", "text": "ok"}], IN.format(3), requestId="q2"),
    ])

    codex_rows = [
        {"type": "session_meta", "timestamp": IN.format(0),
         "payload": {"id": "codex-1", "cwd": str(task_dir / "repo")}},
        *_call("c9", 'tools.exec_command({cmd:"git log"})', OLD),
        *_output("c9", "old", OLD),
        _item("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Spec. RTK is available for this task's command output."}]}, IN.format(0)),
        # reasoning + call = one model call
        _item("response_item", {"type": "reasoning", "summary": []}, IN.format(1)),
        *_call("c1", 'const r = await tools.exec_command({cmd:"rtk git status", workdir:"C:/w"});', IN.format(1)),
        *_output("c1", "ok\n", IN.format(2)),
        # a duplicate cumulative-usage event is not a model call
        _item("event_msg", {"type": "token_count", "info": {}}, IN.format(2)),
        *_call("c2", 'const r = await tools.exec_command({"cmd":"Get-Content f.txt | Select-Object -First 5","yield_time_ms":1000});', IN.format(3)),
        *_output("c2", "z" * 5000, IN.format(4)),
        *_call("c3", 'const t = await tools.mcp__ensemble__ensemble_get_task({taskId:"room-aaaa", messages:20}); text(t);', IN.format(5)),
        *_output("c3", json.dumps({"task": "seven"}), IN.format(6)),
        {"type": "compacted", "timestamp": IN.format(7), "payload": {}},
        *_call("c4", "text(1 + 1);", IN.format(8)),
        *_output("c4", "2", IN.format(9)),
        # a batch of two shell commands, one prefixed
        *_call("c5", 'const a = await tools.exec_command({cmd:"rtk rg foo"}); const b = await tools.exec_command({cmd:"git diff"}); text(a + b);', IN.format(10)),
        *_output("c5", "m" * 100, IN.format(11)),
        # a batch mixing a shell command with MCP tools
        *_call("c6", 'const [s, w] = await Promise.all([tools.exec_command({cmd:"Get-Content x"}), tools.mcp__ensemble__ensemble_whoami({})]); text(s);', IN.format(12)),
        *_output("c6", "n" * 300, IN.format(13)),
        _item("response_item", {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "done"}]}, IN.format(14)),
        _item("event_msg", {"type": "token_count", "info": {}}, IN.format(14)),
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
        self.assertEqual(self.report["agents"]["claude"]["results"], 10)  # t9 is old
        self.assertEqual(self.report["agents"]["codex"]["results"], 6)   # c9 is old

    def test_claude_tools_bands_and_images(self) -> None:
        tools = self.tools("claude")
        self.assertEqual(tools["Bash"]["results"], 5)
        self.assertEqual(tools["Read"]["results"], 1)
        self.assertEqual(tools["Read"]["bytes"], 10_000)
        self.assertEqual(tools["Read"]["bands"]["8-32 KB"], 1)
        self.assertEqual(tools["Read" + m.IMAGE_SUFFIX]["results"], 1)
        self.assertEqual(tools["Grep"]["results"], 2)
        self.assertEqual(tools["Bash"]["bands"]["<=2 KB"], 5)
        self.assertEqual(tools["Read"]["approxTokens"], 2500)

    def test_remaining_turns_stop_at_compaction_and_sidechain(self) -> None:
        top = {r["preview"]: r for r in self.report["agents"]["claude"]["top"]}
        # the 10 KB Read: r3 follows, then the compaction
        self.assertEqual(top["C:/w/a.py"]["remainingTurns"], 1)
        self.assertEqual(top["C:/w/a.py"]["rereadBytes"], 10_000)
        # the PDF: nothing before the compaction
        self.assertEqual(top["C:/w/b.pdf"]["remainingTurns"], 0)
        tools = self.tools("claude")
        # cat: r1 r2 r3; git: r2 r3; sed: r3; echo (22 bytes): r6 r7 r8; sed guidance: r7 r8
        self.assertEqual(tools["Bash"]["rereadBytes"],
                         3 * 9 + 2 * (len(WARN.encode()) + 6) + 1 * 5
                         + 3 * 22 + 2 * len(GUIDANCE.encode()))
        # sidechain Grep has no later sidechain turn; main Grep has r5..r8
        self.assertEqual(tools["Grep"]["rereadBytes"], 0 * 11 + 4 * 11)

    def test_task_and_title_resolved(self) -> None:
        row = self.report["agents"]["claude"]["top"][0]
        self.assertEqual(row["task"], 7)
        self.assertEqual(row["title"], "Seventh task")
        self.assertEqual(self.report["agents"]["codex"]["top"][0]["task"], 7)

    def test_rtk_classification_claude(self) -> None:
        rtk = self.report["agents"]["claude"]["rtk"]
        bash = rtk["byTool"]["Bash"]
        # git ran rtk; cat (before any rtk result), sed, quoted echo, guidance sed did not
        self.assertEqual((bash["rtk"], bash["no-rtk"], bash["unwired"], bash["partial"]), (1, 4, 0, 0))
        ps = rtk["byTool"]["PowerShell"]
        self.assertEqual((ps["rtk"], ps["recall"]), (1, 1))
        self.assertEqual(bash["recall"], 0)
        self.assertEqual(rtk["warnings"], 1)
        words = {w["word"]: w for w in rtk["noRtkFirstWords"]}
        self.assertEqual(set(words), {"cat", "sed", "echo"})
        self.assertEqual(words["sed"]["results"], 2)
        self.assertEqual(rtk["sessions"], {"withRtk": 1, "briefedWithoutRtk": 0, "unwired": 0})
        self.assertEqual(rtk["byDay"]["2026-09-20"]["rtk"], 2)

    def test_codex_tools_and_turns(self) -> None:
        tools = self.tools("codex")
        self.assertEqual(tools["exec_command"]["results"], 3)
        self.assertEqual(tools["exec_command"]["bytes"], 3 + 5000 + 100)
        self.assertEqual(tools["mcp__ensemble__ensemble_get_task"]["results"], 1)
        self.assertEqual(tools[m.CODEX_JS]["results"], 1)
        self.assertEqual(tools[m.CODEX_MIXED]["results"], 1)
        self.assertEqual(tools[m.CODEX_MIXED]["bytes"], 300)
        # c1 output: the c2 call and the c3 call follow before the compaction (the
        # token_count after c1's own output is not a later model call); c2: c3; c3: 0
        # c4: c5, c6, final message; c5: c6, message; c6: message
        self.assertEqual(tools["exec_command"]["rereadBytes"], 2 * 3 + 1 * 5000 + 2 * 100)
        self.assertEqual(tools[m.CODEX_JS]["rereadBytes"], 3 * 1)
        self.assertEqual(tools[m.CODEX_MIXED]["rereadBytes"], 1 * 300)
        preview = self.report["agents"]["codex"]["top"][0]["preview"]
        self.assertEqual(preview, "Get-Content f.txt | Select-Object -First 5")

    def test_codex_rtk(self) -> None:
        rtk = self.report["agents"]["codex"]["rtk"]
        row = rtk["byTool"]["exec_command"]
        self.assertEqual((row["rtk"], row["no-rtk"], row["partial"]), (1, 1, 1))
        self.assertEqual(rtk["noRtkFirstWords"][0]["word"], "Get-Content")
        self.assertEqual((rtk["mixedResults"], rtk["mixedBytes"]), (1, 300))
        self.assertEqual(rtk["shellResults"], 3)

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
        self.assertEqual(codex["rereadBytesSaved"], codex["bytesSaved"] * 1)

    def test_markdown_renders(self) -> None:
        text = m._markdown(self.report)
        self.assertIn("| Read | 1 | 10,000 |", text)
        self.assertIn("#7 Seventh task", text)
        self.assertIn("## What a cap would have cut", text)
        self.assertIn("| `sed` | 2 |", text)
        self.assertIn("1 mixed batches (300 bytes)", text)
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
        self.assertTrue(m.rtk_prefixed("rtk git status"))
        self.assertTrue(m.rtk_prefixed("cd x && rtk grep foo"))
        self.assertTrue(m.rtk_prefixed("git add -A; rtk git status"))
        self.assertFalse(m.rtk_prefixed("git status"))
        self.assertFalse(m.rtk_prefixed("echo rtk"))
        self.assertFalse(m.rtk_prefixed('echo "hello; rtk git status"'))
        self.assertFalse(m.rtk_prefixed("git commit -m 'x; rtk test'"))

    def test_rtk_ran(self) -> None:
        self.assertTrue(m.rtk_ran(WARN + "clean"))
        self.assertTrue(m.rtk_ran("x\n[full output: rtk recall e113d27fab59]"))
        self.assertFalse(m.rtk_ran(GUIDANCE))
        self.assertFalse(m.rtk_ran("see [rtk] in the middle of a line"))
        self.assertFalse(m.rtk_ran("run rtk recall <hash> --full"))

    def test_codex_tool_shapes(self) -> None:
        self.assertEqual(m.codex_tool({"name": "exec", "input": 'tools.exec_command({cmd:"git log"})'}),
                         ("exec_command", "git log", ["git log"]))
        self.assertEqual(m.codex_tool({"name": "exec", "input": "tools.exec_command({'cmd':'rg \"x\" .'})"}),
                         ("exec_command", 'rg "x" .', ['rg "x" .']))
        self.assertEqual(m.codex_tool({"name": "exec", "input": 'const r = await tools.view_image({path:"C:\\\\a.png"});'}),
                         ("view_image", "C:\\a.png", []))
        self.assertEqual(m.codex_tool({"name": "shell", "arguments": '{"command": ["git", "status"]}'}),
                         ("shell", "git status", ["git status"]))
        self.assertEqual(m.codex_tool({"name": "exec_command", "arguments": '{"cmd": "ls"}'}),
                         ("exec_command", "ls", ["ls"]))
        self.assertEqual(m.codex_tool({"name": "exec", "input": "text(1)"})[0], m.CODEX_JS)
        two = m.codex_tool({"name": "exec", "input": 'tools.exec_command({cmd:"a"}); tools.exec_command({cmd:"b"});'})
        self.assertEqual(two, ("exec_command", "a", ["a", "b"]))
        mixed = m.codex_tool({"name": "exec", "input": 'tools.exec_command({cmd:"a"}); tools.mcp__x({});'})
        self.assertEqual(mixed[0], m.CODEX_MIXED)
        self.assertEqual(mixed[2], ["a"])
        self.assertIn("exec_command+mcp__x", mixed[1])


if __name__ == "__main__":
    unittest.main()
