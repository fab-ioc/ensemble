"""#335 Generate: an initiative's icon drawn by the hub's CLI.

* sanitize(): anything that could run, load or hide (script, event
  attributes, links, foreignObject, use, image, style, url(), javascript:,
  a DTD or entity, CDATA, a processing instruction, a namespaced attribute,
  text, too big, too many elements, too deep, a foreign root) is refused; a
  plain icon is written out again from its tree, 48x48 with its viewBox, and
  #329's own check (project_icon.accept) takes it;
* parse(): the usable ones from a chatty answer, no two alike, at most four;
  linear on many openers with no closer, at most 32 tried, `</svg >` closes;
* _run(): the prompt goes in on stdin, never in argv (a Windows .cmd shim
  would shell-parse it); a runaway answer is read only up to MAX_OUTPUT;
* about_text(): the roadmap's first line of prose, its first sentence;
* generate() with the CLI stubbed: ok, no CLI, a timeout, an error, no
  usable icon, already busy; the prompt carries the name and what it is about;
* the endpoints: GET says which CLI draws and from what; POST answers the
  icons or the reason with its status; only from the page; the typed name
  is used when given.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
import icon_gen  # noqa: E402
import project_icon  # noqa: E402
from tests.test_project_settings import Hub  # noqa: E402

NS = 'xmlns="http://www.w3.org/2000/svg"'
GOOD = (f'<svg {NS} viewBox="0 0 48 48"><rect x="0" y="0" width="48" height="48" rx="10" fill="#0F766E"/>'
        '<path d="M26 8 14 27h9l-2 13 13-20h-9z" fill="#FFFFFF" stroke="none"/></svg>')
GOOD2 = (f'<svg {NS} viewBox="0 0 48 48" width="200" height="200"><rect width="48" height="48" rx="10" fill="rgb(234, 88, 12)"/>'
         '<g transform="rotate(45 24 24)" opacity="0.9"><circle cx="24" cy="24" r="9" fill="none" stroke="#fff" stroke-width="4"/>'
         '<line x1="24" y1="8" x2="24" y2="40" stroke="#fff" stroke-linecap="round"/></g>'
         '<polygon points="10,10 14,10 12,14" fill="#fde047"/><ellipse cx="38" cy="38" rx="3" ry="2" fill="#fde047"/>'
         '<polyline points="4 44 8 40 12 44" fill="none" stroke="#fde047"/></svg>')


def svg(inner: str, root_attrs: str = 'viewBox="0 0 48 48"') -> str:
    return f'<svg {NS} {root_attrs}>{inner}</svg>'


class Sanitize(unittest.TestCase):
    def refused(self, text: str):
        with self.assertRaises(icon_gen.Unsafe, msg=str(text)[:120]):
            icon_gen.sanitize(text)

    def test_a_plain_icon_comes_out_clean_and_329_takes_it(self):
        for s in (GOOD, GOOD2):
            out = icon_gen.sanitize(s)
            self.assertTrue(out.startswith(f'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">'), out)
            self.assertNotIn('width="200"', out)
            self.assertEqual(icon_gen.sanitize(out), out, "stable when read again")
            raw, ext = project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(out.encode()).decode())
            self.assertEqual(ext, "svg")
            self.assertEqual(raw.decode(), out)

    def test_viewbox_twice_in_two_cases_is_kept_once(self):
        out = icon_gen.sanitize(svg('<rect width="4" height="4"/>', 'viewBox="0 0 48 48" VIEWBOX="0 0 9 9"'))
        self.assertEqual(out.count("viewBox="), 1)
        self.assertIn('viewBox="0 0 48 48"', out)
        ET.fromstring(out)   # well-formed
        self.assertEqual(project_icon.accept("data:image/svg+xml;base64," + base64.b64encode(out.encode()).decode())[1], "svg")

    def test_whatever_runs_or_links_is_refused(self):
        for bad in (
            svg('<script>alert(1)</script>'),
            svg('<rect width="4" height="4" onload="alert(1)"/>'),
            svg('<rect width="4" height="4"/>', 'viewBox="0 0 48 48" onclick="x()"'),
            svg('<a href="https://x.example"><rect width="4" height="4"/></a>'),
            svg('<rect width="4" height="4" href="#x"/>'),
            f'<svg {NS} xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 48 48"><rect xlink:href="https://x.example" width="4" height="4"/></svg>',
            svg('<foreignObject width="4" height="4"><div xmlns="http://www.w3.org/1999/xhtml">hi</div></foreignObject>'),
            svg('<use href="#a"/>'),
            svg('<image href="https://x.example/a.png" width="4" height="4"/>'),
            svg('<rect width="4" height="4" style="fill:red"/>'),
            svg('<style>rect{fill:url(https://x.example)}</style>'),
            svg('<rect width="4" height="4" fill="url(#g)"/>'),
            svg('<rect width="4" height="4" fill="url(https://x.example/a)"/>'),
            svg('<rect width="4" height="4" fill="javascript:alert(1)"/>'),
            svg('<rect width="4" height="4" fill="&#106;avascript:alert(1)"/>'),
            svg('<rect width="4" height="4" fill="expression(alert(1))"/>'),
            svg('<rect width="4" height="4" fill="var(--x)"/>'),
            svg('<rect width="4" height="4" transform="evil(1)"/>'),
            svg('<rect width="4" height="4" fill="a;b"/>'),
            svg('<rect width="4" height="4" fill="&quot;&gt;&lt;script&gt;"/>'),
            svg('<rect width="4" height="4" class="x"/>'),
            svg('<rect width="4" height="4" id="x"/>'),
            svg('<animate attributeName="href" to="javascript:alert(1)"/>'),
            svg('<set attributeName="onload" to="alert(1)"/>'),
            svg('<linearGradient id="g"/>'),
            svg('<text x="1" y="9">Motors</text>'),
            svg('<rect width="4" height="4"/>hidden words'),
            svg('<svg viewBox="0 0 4 4"><rect width="4" height="4"/></svg>'),
            svg('<rect width="4" height="4" xml:space="preserve"/>'),
            svg('<x:rect xmlns:x="urn:evil" width="4" height="4"/>'),
            svg('<rect width="4" height="4"/>', 'width="48"'),        # no viewBox
        ):
            self.refused(bad)

    def test_a_dtd_an_entity_cdata_or_a_pi_is_refused(self):
        laughs = ('<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
                  '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>' + svg('<rect width="4" height="4" fill="&c;"/>'))
        self.refused(laughs)
        self.refused('<!DOCTYPE svg SYSTEM "file:///etc/passwd">' + svg('<rect width="4" height="4"/>'))
        self.refused(svg('<![CDATA[<script>alert(1)</script>]]><rect width="4" height="4"/>'))
        self.refused(svg('<?xml-stylesheet href="https://x.example/a.css"?><rect width="4" height="4"/>'))
        self.refused('<?xml version="1.0"?>' + GOOD)

    def test_size_count_depth_and_root_are_capped(self):
        self.refused(svg('<path d="' + "M1 1L2 2" * 1200 + '"/>'))            # over 8 KB
        self.refused(svg('<rect width="1" height="1"/>' * 210))
        nested = '<g>' * 9 + '<rect width="1" height="1"/>' + '</g>' * 9
        self.refused(svg(nested))
        icon_gen.sanitize(svg('<g>' * 7 + '<rect width="1" height="1"/>' + '</g>' * 7))
        self.refused('<html><svg viewBox="0 0 4 4"/></html>')
        self.refused('<svg xmlns="urn:not-svg" viewBox="0 0 4 4"><rect width="4" height="4"/></svg>')
        self.refused('<svg viewBox="0 0 4 4"><rect width="4" height="4"')     # cut short
        self.refused(None)


class Parse(unittest.TestCase):
    def test_the_usable_ones_from_a_chatty_answer(self):
        answer = ("Here are your icons:\n```svg\n" + GOOD + "\n" + GOOD + "\n"
                  + svg('<script>x</script>') + "\n" + GOOD2 + "\n```\nEnjoy!")
        got = icon_gen.parse(answer)
        self.assertEqual(got, [icon_gen.sanitize(GOOD), icon_gen.sanitize(GOOD2)])

    def test_text_that_lowercases_longer_does_not_shift_the_cut(self):
        # "İ".lower() is two characters; the offsets must stay the answer's own.
        self.assertEqual(icon_gen.parse("İşte: " + GOOD + "\nİİİ ẞ " + GOOD2),
                         [icon_gen.sanitize(GOOD), icon_gen.sanitize(GOOD2)])

    def test_at_most_four(self):
        many = "\n".join(svg(f'<rect width="{i + 1}" height="4" fill="#123456"/>') for i in range(7))
        got = icon_gen.parse(many)
        self.assertEqual(len(got), 4)
        self.assertIn('width="1"', got[0].split("<rect", 1)[1])

    def test_malformed_answers_parse_in_linear_time(self):
        t = time.monotonic()
        self.assertEqual(icon_gen.parse("<svg " * 60000), [])
        self.assertEqual(icon_gen.parse("<svg>x</svgx " * 20000), [])
        self.assertEqual(icon_gen.parse("<svgx " * 40000 + GOOD), [icon_gen.sanitize(GOOD)])
        self.assertLess(time.monotonic() - t, 1.0)

    def test_at_most_32_are_tried_and_a_spaced_closer_counts(self):
        bad = svg('<script>x</script>')
        self.assertEqual(icon_gen.parse(bad * 32 + GOOD), [])
        self.assertEqual(icon_gen.parse(bad * 31 + GOOD), [icon_gen.sanitize(GOOD)])
        self.assertEqual(icon_gen.parse(GOOD[:-6] + "</svg \n>"), [icon_gen.sanitize(GOOD)])

    def test_nothing_usable(self):
        self.assertEqual(icon_gen.parse(""), [])
        self.assertEqual(icon_gen.parse("I cannot draw."), [])
        self.assertEqual(icon_gen.parse(None), [])


class About(unittest.TestCase):
    def test_the_first_prose_line_cut_at_its_sentence(self):
        text = ("# Motors — roadmap\n\nOwned by the PO.\n\n"
                "Electric drivetrain for the cargo bike, from the motor to the battery. Then the rest.\n- a list item\n")
        self.assertEqual(icon_gen.about_text(text), "Electric drivetrain for the cargo bike, from the motor to the battery.")

    def test_markup_is_dropped_and_long_lines_are_cut(self):
        self.assertEqual(icon_gen.about_text("| a | b |\n> **Bold** _claim_ `here`"), "Bold claim here")
        long = icon_gen.about_text("x" * 500)
        self.assertEqual(len(long), 200)
        self.assertTrue(long.endswith("…"))

    def test_nothing(self):
        self.assertEqual(icon_gen.about_text(""), "")
        self.assertEqual(icon_gen.about_text("# Title only\n\n## And a heading\n"), "")


CLAUDE = {"agent": "claude", "model": "haiku", "bin": "/usr/bin/claude"}


class Generate(unittest.TestCase):
    def run_with(self, answer=None, error=None, cli=CLAUDE):
        seen = {}

        def fake_run(c, text, timeout):
            seen.update(cli=c, text=text, timeout=timeout)
            if error:
                raise error
            return answer
        with mock.patch.object(icon_gen, "which_cli", lambda: cli), mock.patch.object(icon_gen, "_run", fake_run):
            return icon_gen.generate("proj-a", "Motors", "Electric drivetrain."), seen

    def test_ok(self):
        (ok, code, res), seen = self.run_with(GOOD + "\n" + GOOD2)
        self.assertTrue(ok)
        self.assertEqual(code, "ok")
        self.assertEqual((res["agent"], res["model"]), ("claude", "haiku"))
        self.assertEqual(len(res["svgs"]), 2)
        self.assertIsInstance(res["ms"], int)
        self.assertIn('"Motors"', seen["text"])
        self.assertIn('"Electric drivetrain."', seen["text"])
        self.assertEqual(seen["timeout"], 60)

    def test_no_cli(self):
        (ok, code, res), _ = self.run_with(cli=None)
        self.assertEqual((ok, code, res), (False, "no_cli", {}))

    def test_timeout_error_and_nothing_usable(self):
        self.assertEqual(self.run_with(error=subprocess.TimeoutExpired("claude", 60))[0][:2], (False, "timeout"))
        self.assertEqual(self.run_with(error=RuntimeError("signed out"))[0][:2], (False, "failed"))
        self.assertEqual(self.run_with(error=OSError("gone"))[0][:2], (False, "failed"))
        (ok, code, res), _ = self.run_with("Sorry, no.")
        self.assertEqual((ok, code, res["agent"]), (False, "no_icons", "claude"))

    def test_at_most_two_runs_on_the_hub(self):
        go, release = threading.Semaphore(0), threading.Event()

        def slow(c, text, timeout):
            go.release()
            release.wait(5)
            return GOOD
        got = {}
        with mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE), mock.patch.object(icon_gen, "_run", slow):
            ts = [threading.Thread(target=lambda k=k: got.update({k: icon_gen.generate(k, "B", "")})) for k in ("p1", "p2")]
            for t in ts:
                t.start()
            self.assertTrue(go.acquire(timeout=5) and go.acquire(timeout=5))
            self.assertEqual(icon_gen.generate("p3", "C", "")[:2], (False, "busy"), "a third project waits its turn")
            release.set()
            for t in ts:
                t.join(5)
            self.assertTrue(got["p1"][0] and got["p2"][0])
            self.assertTrue(icon_gen.generate("p3", "C", "")[0], "free again afterwards")

    def test_one_at_a_time_per_project(self):
        go, release = threading.Event(), threading.Event()

        def slow(c, text, timeout):
            go.set()
            release.wait(5)
            return GOOD
        got = {}
        with mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE), mock.patch.object(icon_gen, "_run", slow):
            t = threading.Thread(target=lambda: got.update(first=icon_gen.generate("proj-b", "B", "")))
            t.start()
            self.assertTrue(go.wait(5))
            self.assertEqual(icon_gen.generate("proj-b", "B", "")[:2], (False, "busy"))
            release.set()
            t.join(5)
        self.assertTrue(got["first"][0])
        self.assertNotIn("proj-b", icon_gen._BUSY, "free again afterwards")

    def test_the_prompt_is_bounded(self):
        p = icon_gen.prompt("N" * 500, "A" * 500)
        self.assertIn("N" * 120 + '"', p)
        self.assertNotIn("N" * 121, p)
        self.assertNotIn("A" * 201, p)
        self.assertNotIn("about:", icon_gen.prompt("Motors", ""))


class Run(unittest.TestCase):
    """_run() with subprocess.run caught or a stand-in CLI script."""
    NASTY = 'Harbour" & del /q * | calc ^ %PATH% $(id) `id`'

    def caught(self, cli):
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd
            seen["stdin"] = kw["stdin"].read().decode("utf-8")
            self.assertNotIn("input", kw)
            seen["flags"] = kw.get("creationflags")
            seen["cwd"] = kw.get("cwd")
            kw["stdout"].write(GOOD.encode())
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(icon_gen.subprocess, "run", fake), mock.patch.object(icon_gen, "_NO_WINDOW", 0x08000000):
            out = icon_gen._run(cli, icon_gen.prompt(self.NASTY, "about & | ^"), 5)
        return seen, out

    def test_the_prompt_goes_in_on_stdin_never_in_argv(self):
        for cli in (CLAUDE, {"agent": "codex", "model": "", "bin": "C:/npm/codex.cmd"}):
            seen, out = self.caught(cli)
            self.assertIn(self.NASTY, seen["stdin"], cli["agent"])
            self.assertFalse(any("Harbour" in a or "about" in a for a in seen["cmd"]), seen["cmd"])
            self.assertEqual(out, GOOD)
        c = self.caught(CLAUDE)[0]
        # No MCP servers, no session folder left behind, no tools; no console window on Windows.
        self.assertEqual(c["cmd"][1:], ["-p", "--model", "haiku", "--no-session-persistence",
                                        "--tools", "", "--strict-mcp-config"])
        self.assertEqual(c["flags"], 0x08000000)
        # The hub's one workspace, so no ~/.claude/projects/-tmp-ens-icon-* per run.
        self.assertEqual(Path(c["cwd"]), dashboard.DASHBOARD_DIR / "_rename_workspace")
        self.assertEqual(seen["cmd"][-1], "-", "codex exec reads its prompt from stdin")

    def test_a_lone_surrogate_does_not_break_the_run(self):
        seen = {}

        def fake(cmd, **kw):
            seen["stdin"] = kw["stdin"].read().decode("utf-8")
            kw["stdout"].write(GOOD.encode())
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(icon_gen.subprocess, "run", fake):
            self.assertEqual(icon_gen._run(CLAUDE, icon_gen.prompt("A\ud800B", ""), 5), GOOD)
        self.assertIn('"A?B"', seen["stdin"])

    def test_claude_is_found_the_hub_s_way(self):
        fake = mock.Mock(_claude_bin=lambda: "/opt/x/claude")
        with mock.patch.object(icon_gen, "_d", fake):
            self.assertEqual(icon_gen.which_cli()["bin"], "/opt/x/claude")
        self.assertIs(icon_gen._d, dashboard, "the hub binds itself")

    @unittest.skipIf(os.name == "nt", "a POSIX stand-in script")
    def test_a_runaway_answer_is_read_only_up_to_the_cap(self):
        with tempfile.TemporaryDirectory() as d:
            cli = Path(d) / "stand-in"   # a "claude" would be refused by agent_launch in a test
            cli.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n"
                           "sys.stdout.write('x' * (3 * 1024 * 1024))\n")
            cli.chmod(0o755)
            out = icon_gen._run({"agent": "claude", "model": "haiku", "bin": str(cli)}, "p", 20)
            self.assertEqual(len(out), icon_gen.MAX_OUTPUT)
            cli.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('e' * 100000 + 'signed out')\nsys.exit(1)\n")
            with self.assertRaises(RuntimeError) as e:
                icon_gen._run({"agent": "claude", "model": "haiku", "bin": str(cli)}, "p", 20)
            self.assertLessEqual(len(str(e.exception)), 300)


class Endpoints(Hub):
    def test_get_says_which_cli_and_from_what(self):
        home = self.home(self.ed)
        home.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(dashboard, "read_roadmap", lambda proj: {"text": "# ED\n\nA dashboard for agent teams. More."}), \
                mock.patch.object(icon_gen, "which_cli", lambda: CLAUDE):
            st, _, body = self.call("GET", f"/api/projects/icon-gen?id={self.ed}")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body), {"agent": "claude", "model": "haiku", "name": "Ensemble Dashboard",
                                            "about": "A dashboard for agent teams."})
        with mock.patch.object(icon_gen, "which_cli", lambda: None):
            st, _, body = self.call("GET", f"/api/projects/icon-gen?id={self.ed}")
        self.assertEqual((st, json.loads(body)["agent"]), (200, ""))
        st, _, _ = self.call("GET", "/api/projects/icon-gen?id=proj-nope")
        self.assertEqual(st, 404)

    def post(self, body):
        st, _, raw = self.call("POST", "/api/projects/icon-gen", body)
        return st, json.loads(raw)

    def test_post_answers_the_icons_or_why_not(self):
        seen = {}

        def gen(key, name, about, timeout=60):
            seen.update(key=key, name=name)
            return seen.get("answer")
        with mock.patch.object(icon_gen, "generate", gen):
            seen["answer"] = (True, "ok", {"agent": "claude", "model": "haiku", "svgs": [icon_gen.sanitize(GOOD)], "ms": 5})
            st, body = self.post({"projectId": self.ed})
            self.assertEqual((st, body["ok"], len(body["svgs"]), body["agent"]), (200, True, 1, "claude"))
            self.assertEqual((seen["key"], seen["name"]), (self.ed, "Ensemble Dashboard"))
            st, body = self.post({"projectId": self.ed, "name": "  Harbour   Lights "})
            self.assertEqual(seen["name"], "Harbour Lights", "the name typed in the settings, not saved yet")
            st, body = self.post({"projectId": self.ed, "name": "Harbour \ud800 Lights"})
            self.assertEqual((st, seen["name"]), (200, "Harbour ? Lights"), "a lone surrogate is replaced, not a dropped connection")
            self.post({"projectId": self.ed, "name": 7})
            self.assertEqual(seen["name"], "Ensemble Dashboard")
            # Never 502-504: hub-resilience.js reads those as the hub being down.
            for code, status in (("no_cli", 200), ("timeout", 200), ("busy", 409), ("failed", 200), ("no_icons", 200)):
                seen["answer"] = (False, code, {"agent": "claude", "model": "haiku"} if code != "no_cli" else {})
                st, body = self.post({"projectId": self.ed})
                self.assertEqual((st, body["error"], body["ok"]), (status, code, False))
                self.assertEqual(body["message"], icon_gen.REFUSALS[code])
        st, body = self.post({"projectId": "proj-nope"})
        self.assertEqual((st, body["error"]), (404, "no_such_project"))

    def test_only_from_the_page(self):
        self.page = False
        with mock.patch.object(icon_gen, "generate", side_effect=AssertionError("must not run")):
            st, body = self.post({"projectId": self.ed})
            self.assertEqual((st, body["error"]), (403, "page_only"))
        # The GET has no Origin to go by: the page key and Sec-Fetch-Site gate it.
        for kw in ({"cookie": False}, {"site": "cross-site"}, {"site": "same-site"}):
            st, _, _ = self.call("GET", f"/api/projects/icon-gen?id={self.ed}", **kw)
            self.assertEqual(st, 403, kw)


if __name__ == "__main__":
    unittest.main()
