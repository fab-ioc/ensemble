"""Every chat balloon says when it was written: "09/17 14:05" after the names.

The page's own code, run in Node: the label's format, nothing for a message
without a time, and the label in both a full balloon's and a row's header.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")

JS = r"""
%s
const at = (mo, d, h, mi) => new Date(2026, mo - 1, d, h, mi).getTime() / 1000;
console.log(JSON.stringify({
  padded: chatStamp(at(9, 7, 9, 5)),
  late: chatStamp(at(12, 31, 23, 59)),
  html: stampHtml(at(9, 17, 14, 5)),
  none: [stampHtml(0), stampHtml(undefined), stampHtml(null), chatStamp(NaN), chatStamp('x')],
}));
"""


def stamp_block(src: str) -> str:
    return src[src.index("function chatStamp("):src.index("// One balloon drawn as a row")]


@unittest.skipUnless(NODE, "node is not installed")
class BalloonStamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, "-e", JS % stamp_block(SRC)], capture_output=True,
                             encoding="utf-8", timeout=30)
        assert out.returncode == 0, out.stderr
        cls.got = json.loads(out.stdout)

    def test_month_day_hour_minute_zero_padded(self):
        self.assertEqual(self.got["padded"], "09/07 09:05")
        self.assertEqual(self.got["late"], "12/31 23:59")

    def test_label_markup(self):
        self.assertEqual(self.got["html"], '<span class="at">09/17 14:05</span>')

    def test_no_time_no_label(self):
        self.assertEqual(self.got["none"], ["", "", "", "", ""])

    def test_row_and_balloon_headers_carry_it(self):
        fn = SRC[SRC.index("function foldBalloonHtml("):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("whoHtml(m) + stampHtml(m.ts)", fn)
        self.assertEqual(fn.count("${who}"), 2)


if __name__ == "__main__":
    unittest.main()
