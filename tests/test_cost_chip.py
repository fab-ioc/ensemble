"""A task's cost chip shows tokens when there is no price.

Codex conversations carry tokens and no dollars (dashboard.py's task rows send
them as costTokens), so a Codex task's chip used to show nothing. index.html's
"Cost chip" block runs here in Node; skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")

JS = r"""
%s
const log = {};
log.fmt = [0, 7, 999, 1000, 1049, 12345, 99949, 450000, 999499, 999500, 4500000, 12345678].map(fmtTokens);
const T = (o) => ({ input: 0, output: 0, cacheWrite: 0, cacheRead: 0, ...o });
log.chips = [
  { cost: 0, costTokens: T({ input: 120000, output: 30000, cacheRead: 1200000 }) },   // Codex
  { cost: 1.234, costTokens: T({ input: 5 }) },                                        // priced
  { cost: 0, costTokens: T({}) },                                                      // nothing yet
  { cost: 0, costTokens: null },                                                       // an older hub
  { cost: 0.004, costTokens: T({ output: 950 }) },                                     // under a cent
  { cost: 0, costTokens: { input: -5, output: 'x', cacheRead: Infinity, cacheWrite: 2000 } },
  { cost: null },
].map(costChipHtml);
console.log(JSON.stringify(log));
"""


@unittest.skipUnless(NODE, "node is not installed")
class CostChip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        i = INDEX.index("// ---- Cost chip: begin")
        src = "\n".join([re.search(r"^const esc = .*$", INDEX, re.M).group(0),
                         INDEX[INDEX.index("const fmtCost = "):INDEX.index("const fmtInt = ")],
                         INDEX[i:INDEX.index("// ---- Cost chip: end", i)]])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "chip.cjs"
            script.write_text(JS % src, encoding="utf-8")
            proc = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        cls.r = json.loads(proc.stdout)

    def test_tokens_read_as_a_person_reads_them(self):
        self.assertEqual(self.r["fmt"], ["0", "7", "999", "1k", "1k", "12k", "100k", "450k", "999k", "1M", "4.5M", "12M"])

    def test_a_codex_task_shows_its_tokens(self):
        codex, priced, empty, old, cent, junk, none = self.r["chips"]
        self.assertIn(">1.4M tokens</span>", codex)
        self.assertIn("in 120,000 · out 30,000 · cache w 0 · cache r 1,200,000", codex)
        self.assertIn("No price is known", codex)
        self.assertIn(">$1.23</span>", priced, "dollars where they are known")
        self.assertIn("Tokens: in 5", priced)
        self.assertEqual([empty, old, none], ["", "", ""], "nothing used, nothing shown")
        self.assertIn(">950 tokens</span>", cent)
        self.assertIn(">2k tokens</span>", junk, "only real counts are added up")

    def test_the_row_uses_it(self):
        self.assertIn("const costChip = costChipHtml(r);", INDEX)


if __name__ == "__main__":
    unittest.main()
