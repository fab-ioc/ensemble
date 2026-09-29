"""The word for a board is one setting per hub (projectNoun, #130).

* the hub keeps {"one", "many"} in settings.json: Project / Projects until
  chosen, a choice comes back from GET /api/settings as saved, and a value
  that is not two short words is refused and leaves the last choice;
* ``dashboard.py --set-project-noun ONE MANY`` (the install scripts' way in)
  saves it and says so, and refuses what the setting refuses;
* project_noun() and static/noun.js (run here in Node) word it alike: lower
  or title case, a word in capitals kept, "a"/"an" in front of it;
* the hub's words for a conversation that cannot become a PO use the word,
  as the page's do (tests/test_project_setup.py keeps the two the same).
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402

NODE = shutil.which("node")


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [mock.patch.object(dashboard, "DASHBOARD_DIR", d),
                        mock.patch.object(dashboard, "SETTINGS_FILE", d / "settings.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def get_settings(self) -> dict:
        h = dashboard.Handler.__new__(dashboard.Handler)
        h.path, h.command, h.request_version = "/api/settings", "GET", "HTTP/1.1"
        sent = {}
        h._send_json = lambda code, body: sent.update(code=code, body=body)
        h.headers = {}
        with mock.patch.object(dashboard, "operator_name", lambda: "sam"):
            h.do_GET()
        self.assertEqual(sent.get("code"), 200)
        return sent["body"]


class TheSetting(Isolated):
    def test_project_until_chosen(self):
        self.assertEqual(dashboard.load_settings()["projectNoun"], {"one": "Project", "many": "Projects"})
        self.assertEqual(self.get_settings()["projectNoun"], {"one": "Project", "many": "Projects"})
        self.assertEqual(dashboard.project_noun(), "project")
        self.assertEqual(dashboard.project_noun("many", "title"), "Projects")

    def test_a_choice_comes_back_as_saved(self):
        out = dashboard.save_settings({"projectNoun": {"one": "Initiative", "many": "Initiatives"}})
        self.assertEqual(out["projectNoun"], {"one": "Initiative", "many": "Initiatives"})
        self.assertEqual(self.get_settings()["projectNoun"], {"one": "Initiative", "many": "Initiatives"})
        saved = json.loads(Path(dashboard.SETTINGS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["projectNoun"], {"one": "Initiative", "many": "Initiatives"})
        # Another setting saved later keeps it.
        dashboard.save_settings({"operatorNickname": "fab"})
        self.assertEqual(dashboard.load_settings()["projectNoun"]["one"], "Initiative")

    def test_spaces_are_tidied(self):
        out = dashboard.save_settings({"projectNoun": {"one": "  Work   stream ", "many": "Work streams"}})
        self.assertEqual(out["projectNoun"], {"one": "Work stream", "many": "Work streams"})

    def test_what_is_not_two_short_words_is_refused(self):
        dashboard.save_settings({"projectNoun": {"one": "Epic", "many": "Epics"}})
        for bad in ("Epic", None, [], {"one": "Epic"}, {"one": "", "many": "Epics"},
                    {"one": "Epic", "many": " "}, {"one": "x" * 41, "many": "xs"},
                    {"one": "<b>", "many": "bs"}, {"one": "{project}", "many": "ps"},
                    {"one": 3, "many": "threes"}):
            with self.subTest(bad=bad):
                out = dashboard.save_settings({"projectNoun": bad})
                self.assertEqual(out["projectNoun"], {"one": "Epic", "many": "Epics"})

    def test_a_broken_file_value_reads_as_the_default_word(self):
        Path(dashboard.SETTINGS_FILE).write_text(json.dumps({"projectNoun": "Epic"}), encoding="utf-8")
        self.assertEqual(dashboard.project_noun("one", "title"), "Project")


class TheInstallScriptsWayIn(Isolated):
    def test_it_saves_the_word(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(dashboard.set_project_noun_cli("Initiative", "Initiatives"), 0)
        self.assertIn("Initiative / Initiatives", out.getvalue())
        self.assertEqual(dashboard.load_settings()["projectNoun"], {"one": "Initiative", "many": "Initiatives"})

    def test_it_refuses_what_the_setting_refuses(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(dashboard.set_project_noun_cli("<x>", "xs"), 1)
        self.assertEqual(dashboard.load_settings()["projectNoun"]["one"], "Project")

    def test_the_scripts_ask_and_call_it(self):
        ps = (ROOT / "install-task.ps1").read_text(encoding="utf-8")
        sh = (ROOT / "install-launchd.sh").read_text(encoding="utf-8")
        for src in (ps, sh):
            self.assertIn("--set-project-noun", src)
            self.assertIn("Word [Enter keeps the current one: Project on a new install]", src)
        # Nobody at the console: no question, the hub keeps its word.
        self.assertIn("[Console]::IsInputRedirected", ps)
        self.assertIn("-t 0", sh)

    def test_the_scripts_parse(self):
        # A broken line stops every action of an installer before it runs.
        if sys.platform == "win32":
            ps = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                  f"'{ROOT / 'install-task.ps1'}', [ref]$null, [ref]$e); $e | ForEach-Object {{ $_.Message }}")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, encoding="utf-8", timeout=60)
            self.assertEqual((out.returncode, out.stdout.strip()), (0, ""), out.stderr)
        bash = shutil.which("bash")
        if bash:
            out = subprocess.run([bash, "-n", "install-launchd.sh"], cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)


class TheWords(Isolated):
    CASES = [({"one": "Project", "many": "Projects"}, ["project", "Project", "projects", "Projects", "a project", "A project"]),
             ({"one": "Initiative", "many": "Initiatives"}, ["initiative", "Initiative", "initiatives", "Initiatives", "an initiative", "An initiative"]),
             ({"one": "OKR", "many": "OKRs"}, ["OKR", "OKR", "OKRs", "OKRs", "an OKR", "An OKR"]),
             ({"one": "work stream", "many": "work streams"}, ["work stream", "Work stream", "work streams", "Work streams", "a work stream", "A work stream"])]

    def test_the_hub_words_it(self):
        for w, want in self.CASES:
            dashboard.save_settings({"projectNoun": w})
            with self.subTest(w=w):
                self.assertEqual([dashboard.project_noun("one"), dashboard.project_noun("one", "title"),
                                  dashboard.project_noun("many"), dashboard.project_noun("many", "title")], want[:4])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_pages_word_it_the_same(self):
        js = f"""
const N = require({json.dumps(str(ROOT / 'static' / 'noun.js'))});
const cases = {json.dumps([c[0] for c in self.CASES])};
const out = cases.map(w => {{ N.setNoun(w); return N.nounText('{{project}}|{{Project}}|{{projects}}|{{Projects}}|{{a project}}|{{A project}}').split('|'); }});
N.setNoun({{ one: '<b>', many: 'x' }});
out.push([N.noun('one', 'title')]);
console.log(JSON.stringify(out));
"""
        r = subprocess.run([NODE, "-"], input=js, capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        for (w, want), words in zip(self.CASES, got):
            with self.subTest(w=w):
                self.assertEqual(words, want)
        self.assertEqual(got[-1], ["Project"], "a word the setting refuses is the default")

    def test_make_po_words_use_it(self):
        dashboard.save_settings({"projectNoun": {"one": "Initiative", "many": "Initiatives"}})
        w = dashboard.make_po_words({"ok": False, "code": "other_project", "project": "Motors"})
        self.assertEqual(w["reason"], "It belongs to the initiative “Motors”.")
        self.assertIn("Move to initiative…", w["fix"])
        for code in ("is_po", "other_project", "has_po"):
            w = dashboard.make_po_words({"ok": False, "code": code, "project": "Motors", "po": "PO"})
            self.assertNotIn("project", (w["reason"] + w["fix"]).lower(), code)


if __name__ == "__main__":
    unittest.main()
