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


def numbered(part: list[dict], base: dict) -> list[str]:
    """The ids of ``part``, counted on from ``base`` as page_turn_ids counts."""
    n, q, qb, prev, ids = base["n"], base["q"], base["qb"], None, []
    for t in part:
        if t.get("queued") == "blocks":
            ids.append(f"s:qb{qb}"); qb += 1
        elif t.get("queued"):
            ids.append(f"s:q{q}"); q += 1
        else:
            same = prev is not None and (t["role"], t["text"]) == (prev["role"], prev["text"])
            prev = t
            if not same:
                ids.append(f"s:{n}"); n += 1
    return ids


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

    def test_big_balloons_are_cut_to_the_bytes_asked(self):
        turns = transcript(5)
        for t in turns:
            t["text"] += " " + "w" * 3000
        tail, base = dashboard.turns_tail(turns, 120, 96_000)
        self.assertLessEqual(sum(len(json.dumps(t)) + 1 for t in tail), 96_000)
        # As many as fit: one balloon more would not.
        kept = sum(1 for i, _ in dashboard.page_turn_ids("s", tail) if re.fullmatch(r"s:\d+", i))
        more, _ = dashboard.turns_tail(turns, kept + 1)
        self.assertGreater(sum(len(json.dumps(t)) + 1 for t in more), 96_000)
        # Never fewer than TAIL_MIN balloons.
        tail, base = dashboard.turns_tail(turns, 120, 10)
        self.assertEqual(len([i for i, _ in dashboard.page_turn_ids("s", tail) if re.fullmatch(r"s:\d+", i)]),
                         dashboard.TAIL_MIN)

    def test_all_sent_when_there_are_fewer(self):
        turns = transcript(1, 10)
        self.assertEqual(dashboard.turns_tail(turns, 120), (turns, None))
        self.assertEqual(dashboard.turns_tail(turns, 0), (turns, None))

    def test_a_link_gets_a_window_around_its_balloon(self):
        # #247 review 4: a link far back sends its balloon with 20 before and
        # 40 after, numbered as the whole; pages below it run to the end, or
        # to the turns the page holds (&to=), never past them.
        for seed in range(10):
            turns = transcript(seed, 2000)
            whole = [i for i, _ in dashboard.page_turn_ids("s", turns)]
            for target in ("s:30", "s:500", "s:0", next(i for i in whole[400:] if ":q" in i)):
                w = dashboard.turns_window(turns, around=target.split(":")[1])
                ids = numbered(w["turns"], w["base"])
                self.assertIn(target, ids)
                at = ids.index(target)
                self.assertEqual(ids, whole[whole.index(ids[0]):whole.index(ids[0]) + len(ids)], (seed, target))
                balloons = lambda l: sum(1 for i in l if re.fullmatch(r"s:\d+", i))
                self.assertLessEqual(balloons(ids[:at]), dashboard.SOLO_LINK_ABOVE + 1, "a queued line: from the balloon before it")
                self.assertLessEqual(balloons(ids[at + 1:]), dashboard.SOLO_LINK_BELOW + 1)
                self.assertEqual(w["base"]["cut"] + len(w["turns"]), w["end"])
                # Down to the end, a page at a time, with nothing missed or twice.
                got, end = list(w["turns"]), w["end"]
                for _ in range(100):
                    if end >= len(turns):
                        break
                    p = dashboard.turns_window(turns, frm=end, n=100)
                    self.assertEqual(p["from"], end)
                    got += p["turns"]
                    end = p["end"]
                self.assertEqual(got, turns[w["base"]["cut"]:])
            # Up to the turns held: the window and its pages stop there.
            held = len(turns) - 300
            w = dashboard.turns_window(turns, around="1000", to=held)
            self.assertLessEqual(w["end"], held)
            p = dashboard.turns_window(turns, frm=w["end"], n=10_000, to=held)
            self.assertEqual(p["end"], held)
            self.assertEqual(dashboard.turns_window(turns, around="99999"), {"turns": [], "total": 2000, "missing": True})

    def test_a_window_and_its_pages_fit_the_bytes_asked(self):
        turns = transcript(7, 2000)
        for t in turns:
            t["text"] += " " + "w" * 3000
        whole = [i for i, _ in dashboard.page_turn_ids("s", turns)]
        bytes_ = lambda l: sum(len(json.dumps(t)) + 1 for t in l)
        w = dashboard.turns_window(turns, around="500", max_bytes=96_000)
        self.assertLessEqual(bytes_(w["turns"]), 96_000)
        ids = numbered(w["turns"], w["base"])
        self.assertIn("s:500", ids)
        self.assertEqual(ids, whole[whole.index(ids[0]):whole.index(ids[0]) + len(ids)])
        above, below = ids.index("s:500"), len(ids) - ids.index("s:500") - 1
        self.assertLessEqual(abs(above - below), 6, "cut on both sides alike")
        got, end = list(w["turns"]), w["end"]
        while end < len(turns):
            p = dashboard.turns_window(turns, frm=end, n=100, max_bytes=96_000)
            self.assertLessEqual(bytes_(p["turns"]), 96_000)
            self.assertGreater(p["end"], end)
            got += p["turns"]
            end = p["end"]
        self.assertEqual(got, turns[w["base"]["cut"]:])
        # One balloon bigger than the bytes is still sent.
        self.assertTrue(dashboard.turns_window(turns, around="500", max_bytes=10)["turns"])
        self.assertTrue(dashboard.turns_window(turns, frm=end - 5, max_bytes=10)["turns"])

    def test_a_po_message_link_finds_its_turns_by_time(self):
        turns = transcript(2, 3000)
        stamps = [dashboard._turn_epoch(t["timestamp"]) for t in turns if t.get("timestamp")]
        w = dashboard.turns_window(turns, at=stamps[900] + 0.5)
        got = [dashboard._turn_epoch(t["timestamp"]) for t in w["turns"] if t.get("timestamp")]
        self.assertTrue(got[0] <= stamps[900] < got[-1])
        self.assertLess(len(w["turns"]), 200)
        self.assertTrue(dashboard.turns_window(turns, at=stamps[0] - 60)["early"], "the session before has it")

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
