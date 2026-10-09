"""The link audit of #199 as a test: every file link the pages would draw for
the agents' messages in ~/.ensemble/rooms, and none of them is a thing no
file is named (a ratio "0.45/0.53", a "#L887" left on the name, a glob, two
names in one link, a "--flag.txt"). Skipped where there are no rooms or no
node. The resolver's side of the audit (how many links find their file) is
in the task's report: it needs the real folders, so it is no test."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOMS = Path(os.path.expanduser("~/.ensemble/rooms"))
NODE = shutil.which("node")
LIMIT = 4000          # the newest messages: the parser, not the archive, is under test

JS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8').replace(/\r\n/g, '\n');
const B = '// ---- Links in rendered text: begin shared block', E = '// ---- Links in rendered text: end shared block';
const i = src.indexOf(B), j = src.indexOf('\n', src.indexOf(E, i)) + 1;
globalThis.location = new URL('http://127.0.0.1:8765/session');
globalThis.esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
globalThis.fileHref = (p, line) => '/fileview?path=' + encodeURIComponent((p || '').trim()) + (line ? '&line=' + line : '');
(0, eval)(src.slice(i, j).replace(/^(?:const|let) /gm, 'var '));
const out = [];
for (const text of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  const html = withPathAbbrevs(text, () => {
    let s = text.replace(/\r\n/g, '\n').replace(/```[\s\S]*?```/g, ' ');
    const parts = [];
    s = s.replace(/`([^`\n]+)`/g, (_, b) => { parts.push(codeSpanHtml(b)); return ' '; });
    for (const line of s.split('\n')) { const keep = []; parts.push(unlinkify(linkify(esc(line), keep), keep)); }
    return parts.join('\n');
  });
  for (const m of html.matchAll(/<a href="\/fileview\?path=([^"&]*)/g)) out.push(decodeURIComponent(m[1]));
}
process.stdout.write(JSON.stringify(out));
"""

NOT_A_NAME = {
    "a ratio": re.compile(r"^[\d.]+[\\/][\d.]+$"),
    "a line anchor left on": re.compile(r"#L\d+"),
    "two names": re.compile(r"\.[A-Za-z]\w{0,7} \S[^\\/]*$"),
    "a leading dash": re.compile(r"^-"),
    "a glob or placeholder": re.compile(r"[*?<>|\"]"),
}


def agent_messages() -> list[str]:
    msgs = []
    for f in ROOMS.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for m in d.get("messages") or []:
            if m.get("from") not in ("user", "ensemble") and isinstance(m.get("text"), str):
                msgs.append((m.get("ts") or 0, m["text"]))
    msgs.sort(key=lambda x: x[0] if isinstance(x[0], (int, float)) else 0)
    return [t for _, t in msgs[-LIMIT:]]


@unittest.skipUnless(NODE and ROOMS.is_dir(), "needs node and ~/.ensemble/rooms")
class LinkAudit(unittest.TestCase):
    def test_no_link_is_a_thing_no_file_is_named(self):
        msgs = agent_messages()
        if not msgs:
            self.skipTest("no agent messages")
        for page in ("session.html", "index.html", "fileview.html"):
            r = subprocess.run([NODE, "-e", JS, str(ROOT / page)], input=json.dumps(msgs), capture_output=True,
                               text=True, encoding="utf-8", timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            paths = json.loads(r.stdout)
            self.assertTrue(paths, f"{page}: no file links in {len(msgs)} messages")
            bad = {k: sorted({p for p in paths if rx.search(p)})[:5] for k, rx in NOT_A_NAME.items()}
            self.assertEqual({k: v for k, v in bad.items() if v}, {}, f"{page}: {len(paths)} links")


if __name__ == "__main__":
    unittest.main()
