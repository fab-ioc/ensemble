"""The accent colour follows the person, not the browser.

* the hub keeps it in settings.accent: a CSS colour or "default", never back
  to "" (which means never chosen here);
* index.html, session.html and fileview.html carry the same head script, run
  here in Node (skipped without Node): the hub's value wins over the browser's
  cache, a browser's colour is handed up once when the hub has none, "default"
  clears the browser's, and a hub from before the accent was synced changes
  nothing;
* index.html records a choice through that script and saves it on the hub.
"""
from __future__ import annotations

import json
import re
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

PAGES = {n: (ROOT / n).read_text(encoding="utf-8").replace("\r\n", "\n")
         for n in ("index.html", "session.html", "fileview.html")}
NODE = shutil.which("node")


def head_script(src: str) -> str:
    i = src.index("<script>\n// Appearance, resolved before first paint")
    j = src.index("</script>", i)
    return src[i + len("<script>\n"):j]


class AccentSetting(unittest.TestCase):
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

    def test_never_chosen_is_empty(self):
        self.assertEqual(dashboard.load_settings()["accent"], "")

    def test_colours_and_default_are_kept(self):
        for v in ("#C25D3C", "#abc", "default", "rebeccapurple", "rgb(12, 34, 56)",
                  "hsl(210 50% 40%)", "  #1F845A "):
            self.assertEqual(dashboard.save_settings({"accent": v})["accent"], v.strip(), v)
        self.assertEqual(json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))["accent"],
                         "#1F845A")

    def test_anything_else_leaves_the_choice(self):
        dashboard.save_settings({"accent": "#6E5DC6"})
        for v in ("", "   ", "red; background: url(x)", "url(x)", "#12", "x" * 40, 5, None,
                  "expression(alert(1))", "rgb(1,2,3);"):
            self.assertEqual(dashboard.save_settings({"accent": v})["accent"], "#6E5DC6", repr(v))


HARNESS = r"""
const mem = new Map(Object.entries(__STORAGE__));
const calls = [];
const style = new Map();
const events = [];
globalThis.localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null),
                            setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
// A 2D context that takes #rrggbb and a few names, the way a browser
// normalises fillStyle, and ignores anything else.
const NAMES = { rebeccapurple: '#663399', white: '#ffffff' };
globalThis.document = {
  documentElement: { dataset: {}, style: { setProperty: (k, v) => style.set(k, v), removeProperty: k => style.delete(k) } },
  hidden: false, addEventListener() {},
  createElement: () => ({ getContext: () => { let f = '#000000'; return {
    get fillStyle() { return f; },
    set fillStyle(v) { v = String(v).trim().toLowerCase(); if (/^#[0-9a-f]{6}$/.test(v)) f = v; else if (NAMES[v]) f = NAMES[v]; } }; } }),
};
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o.detail; } };
globalThis.window = globalThis;
window.matchMedia = () => ({ matches: false, addEventListener() {} });
window.addEventListener = () => {};
window.dispatchEvent = ev => events.push([ev.type, ev.detail]);
const HUB = __HUB__;
globalThis.fetch = (url, opt) => {
  calls.push([url, opt && opt.method || 'GET', opt && opt.body || '']);
  return Promise.resolve({ ok: true, json: () => Promise.resolve(HUB) });
};
__HEAD__
setTimeout(() => console.log(JSON.stringify({
  storage: Object.fromEntries(mem), style: Object.fromEntries(style), calls,
  accentEvents: events.filter(e => e[0] === 'cd-accent').map(e => e[1].accent),
})), 20);
"""


@unittest.skipUnless(NODE, "node is not installed")
class HeadScript(unittest.TestCase):
    def run_head(self, storage: dict, hub: dict) -> dict:
        js = (HARNESS.replace("__STORAGE__", json.dumps(storage))
                     .replace("__HUB__", json.dumps(hub))
                     .replace("__HEAD__", head_script(PAGES["index.html"])))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "head.js"
            p.write_text(js, encoding="utf-8")
            out = subprocess.run([NODE, str(p)], capture_output=True, text=True,
                                 encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def puts(self, r):
        return [json.loads(body) for url, method, body in r["calls"] if method == "PUT"]

    def test_the_three_pages_carry_the_same_script(self):
        idx = head_script(PAGES["index.html"])
        self.assertIn("cd-accent", idx)
        for name in ("session.html", "fileview.html"):
            self.assertEqual(head_script(PAGES[name]), idx, name)

    def test_first_paint_uses_the_cache(self):
        r = self.run_head({"cd-accent": "#1F845A", "cd-theme": "dark"}, {})
        self.assertEqual(r["style"], {"--accent": "#1F845A", "--accent-fg": "#FFFFFF"})
        self.assertEqual(self.puts(r), [])

    def test_empty_browser_takes_the_hubs_colour(self):
        r = self.run_head({}, {"theme": "light", "accent": "#C25D3C"})
        self.assertEqual(r["storage"].get("cd-accent"), "#C25D3C")
        self.assertEqual(r["style"]["--accent"], "#C25D3C")
        self.assertEqual(r["accentEvents"], ["", "#C25D3C"])
        self.assertEqual(self.puts(r), [])

    def test_the_hub_wins_over_another_browser_colour(self):
        r = self.run_head({"cd-accent": "#1F845A"}, {"theme": "light", "accent": "rebeccapurple"})
        self.assertEqual(r["storage"]["cd-accent"], "rebeccapurple")
        self.assertEqual(r["style"]["--accent"], "rebeccapurple")
        self.assertEqual(self.puts(r), [])

    def test_pale_accent_gets_dark_text(self):
        r = self.run_head({}, {"theme": "light", "accent": "white"})
        self.assertEqual(r["style"]["--accent-fg"], "#101214")

    def test_default_clears_the_browser_colour(self):
        r = self.run_head({"cd-accent": "#1F845A"}, {"theme": "light", "accent": "default"})
        self.assertNotIn("cd-accent", r["storage"])
        self.assertEqual(r["style"], {})
        self.assertEqual(self.puts(r), [])

    def test_migration_hands_the_browser_colour_up(self):
        r = self.run_head({"cd-accent": "#6E5DC6"}, {"theme": "light", "accent": ""})
        self.assertEqual(self.puts(r), [{"accent": "#6E5DC6"}])
        self.assertEqual(r["style"]["--accent"], "#6E5DC6")

    def test_nothing_to_migrate(self):
        r = self.run_head({}, {"theme": "light", "accent": ""})
        self.assertEqual(self.puts(r), [])

    def test_an_older_hub_changes_nothing(self):
        r = self.run_head({"cd-accent": "#6E5DC6"}, {"theme": "light"})
        self.assertEqual(self.puts(r), [])
        self.assertEqual(r["storage"]["cd-accent"], "#6E5DC6")

    def test_a_colour_the_browser_rejects_is_not_applied(self):
        r = self.run_head({"cd-accent": "notacolour"}, {})
        self.assertEqual(r["style"], {})


class IndexWiring(unittest.TestCase):
    def test_choice_is_saved_on_the_hub(self):
        src = PAGES["index.html"]
        i = src.index("function setAccent(")
        block = src[i:src.index("\n}\n", i)]
        self.assertIn("localStorage.setItem('cd-accent'", block)
        self.assertIn("cdTheme.applyAccent()", block)
        self.assertIn("JSON.stringify({accent: want})", block)
        self.assertIn("color || 'default'", block)
        self.assertNotIn("function accentFgFor(", src)   # one copy, in the head


if __name__ == "__main__":
    unittest.main()
