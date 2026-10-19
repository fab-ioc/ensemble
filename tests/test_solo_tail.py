"""#247: a one-agent chat is sent the last balloons of its transcript
(&tail=), numbered as the whole would be, so links and points still find
their balloons.

    PYTHONPATH="<repo>;<repo>/tests" py -m unittest test_solo_tail
"""
from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import dashboard

SRC = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", SRC, re.M)
    assert m, f"{name} not found in session.html"
    return SRC[m.start():SRC.index("\n}\n", m.start()) + 3]


def transcript(seed: int, n: int = 400) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        kind = rnd.random()
        if kind < 0.05:
            out.append({"role": "user", "text": f"queued {i}", "queued": True})
        elif kind < 0.08:
            out.append({"role": "user", "text": f"image {i}", "queued": "blocks"})
        elif kind < 0.15 and out:
            out.append(dict(out[-1]))           # a repeat: no balloon of its own
        else:
            out.append({"role": rnd.choice(["user", "assistant"]), "text": f"turn {i}",
                        "timestamp": f"2026-10-19T10:{i // 60 % 60:02d}:{i % 60:02d}Z"})
    return out


class TurnsTail(unittest.TestCase):
    def test_the_tail_has_the_ids_of_the_whole(self):
        for seed in range(20):
            turns = transcript(seed)
            whole = dashboard.page_turn_ids("s", turns)
            for keep in (1, 7, 120, 500):
                tail, base = dashboard.turns_tail(turns, keep)
                if base is None:
                    self.assertEqual(tail, turns)
                    continue
                self.assertEqual(len(tail), len(turns) - base["cut"])
                # Numbered from the counters at the cut, the tail's ids are the whole's.
                n, q, qb, prev, ids = base["n"], base["q"], base["qb"], None, []
                for t in tail:
                    if t.get("queued") == "blocks":
                        ids.append(f"s:qb{qb}"); qb += 1
                    elif t.get("queued"):
                        ids.append(f"s:q{q}"); q += 1
                    else:
                        same = prev is not None and (t["role"], t["text"]) == (prev["role"], prev["text"])
                        prev = t
                        if not same:
                            ids.append(f"s:{n}"); n += 1
                self.assertEqual(ids, [i for i, _ in whole][-len(ids):], (seed, keep))
                self.assertEqual(sum(1 for i in ids if re.fullmatch(r"s:\d+", i)), keep)

    def test_all_sent_when_there_are_fewer(self):
        turns = transcript(1, 10)
        self.assertEqual(dashboard.turns_tail(turns, 120), (turns, None))
        self.assertEqual(dashboard.turns_tail(turns, 0), (turns, None))

    @unittest.skipUnless(NODE, "node not installed")
    def test_the_page_numbers_the_tail_as_the_whole(self):
        turns = transcript(3)
        tail, base = dashboard.turns_tail(turns, 120)
        script = js_function("soloItems") + f"""
const turns = {json.dumps(turns)}, tail = {json.dumps(tail)}, base = {json.dumps(base)};
const ids = l => l.map(m => m.id);
console.log(JSON.stringify([ids(soloItems(turns, 's', 'claude', 120)), ids(soloItems(tail, 's', 'claude', 120, base)),
  ids(soloItems(tail, 's', 'claude', 50, base)), ids(soloItems(turns, 's', 'claude', 50))]));
"""
        out = subprocess.run([NODE, "-"], input=script, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        whole120, tail120, tail50, whole50 = json.loads(out.stdout)
        self.assertEqual(tail120, whole120)
        self.assertEqual(tail50, whole50)


if __name__ == "__main__":
    unittest.main()
