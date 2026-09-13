"""The accent colour follows the person, not the browser.

* the hub keeps it in settings.accent: an opaque CSS colour or "default", never
  back to "" (which means never chosen here); partial settings saves at once
  all land;
* index.html, session.html and fileview.html carry the same head script, run
  here in Node (skipped without Node): the hub's value wins over the browser's
  cache, a browser's colour is handed up once when the hub has none, "default"
  clears the browser's, a hub from before the accent was synced changes
  nothing, a reply already on its way does not undo a newer choice, saves go
  one at a time with the newest last, and the text on the accent clears 4.5:1;
* index.html records a choice through that script and saves it on the hub.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
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
        for v in ("#C25D3C", "#abc", "default", "rebeccapurple", "RebeccaPurple",
                  "rgb(12, 34, 56)", "rgb(12 34 56)", "rgb(10%, 20%, 30.5%)",
                  "hsl(210 50% 40%)", "hsl(210deg, 50%, 40%)", "  #1F845A "):
            self.assertEqual(dashboard.save_settings({"accent": v})["accent"], v.strip(), v)
        self.assertEqual(json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))["accent"],
                         "#1F845A")

    def test_anything_else_leaves_the_choice(self):
        dashboard.save_settings({"accent": "#6E5DC6"})
        for v in ("", "   ", "red; background: url(x)", "url(x)", "#12", "x" * 40, 5, None,
                  "expression(alert(1))", "rgb(1,2,3);",
                  # not colours
                  "#12345", "#1234567", "notacolour", "currentcolor", "inherit",
                  "rgb(300, 0, 0)", "rgb(10%, 20, 30)", "rgb(1, 2 3)", "hsl(10, 50, 40)",
                  "hsl(400, 50%, 40%)", "rgb(1deg, 2, 3)",
                  # see-through: no text colour is sure to read on it
                  "transparent", "#0008", "#00000000", "rgba(0,0,0,.5)", "rgba(0,0,0,1)",
                  "rgb(1 2 3 / .5)", "hsla(0, 0%, 0%, .5)", "rgb(1, 2, 3, .5)"):
            self.assertEqual(dashboard.save_settings({"accent": v})["accent"], "#6E5DC6", repr(v))

    def test_saves_at_once_all_land(self):
        # A page hands up its theme and its accent in two PUTs at the same time.
        for n in range(20):
            gate = threading.Barrier(2)
            errors = []

            def put(body):
                try:
                    gate.wait()
                    dashboard.save_settings(body)
                except Exception as e:          # noqa: BLE001 - reported below
                    errors.append(e)
            accent = f"#1234{n:02d}"
            ts = [threading.Thread(target=put, args=({"theme": "dark" if n % 2 else "dim"},)),
                  threading.Thread(target=put, args=({"accent": accent},))]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(errors, [])
            saved = json.loads(dashboard.SETTINGS_FILE.read_text(encoding="utf-8"))
            self.assertEqual((saved["theme"], saved["accent"]),
                             ("dark" if n % 2 else "dim", accent), n)
        self.assertEqual([p.name for p in Path(self.tmp.name).iterdir()], ["settings.json"])


HARNESS = r"""
const mem = new Map(Object.entries(__STORAGE__));
const calls = [];
const style = new Map();
const events = [];
globalThis.localStorage = { getItem: k => (mem.has(k) ? mem.get(k) : null),
                            setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
// A 2D context that normalises fillStyle the way a browser does for #rrggbb,
// a few names and rgba(), and keeps the previous colour for anything else.
const NAMES = { rebeccapurple: '#663399', white: '#ffffff', transparent: 'rgba(0, 0, 0, 0)' };
globalThis.document = {
  documentElement: { dataset: {}, style: { setProperty: (k, v) => style.set(k, v), removeProperty: k => style.delete(k) } },
  hidden: false, addEventListener() {},
  createElement: () => ({ getContext: () => { let f = '#000000'; return {
    get fillStyle() { return f; },
    set fillStyle(v) {
      v = String(v).trim().toLowerCase();
      if (/^#[0-9a-f]{6}$/.test(v)) f = v; else if (NAMES[v]) f = NAMES[v];
      else if (/^rgba\([\d.]+, [\d.]+, [\d.]+, [\d.]+\)$/.test(v)) f = v;
    } }; } }),
};
globalThis.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o.detail; } };
globalThis.window = globalThis;
window.matchMedia = () => ({ matches: false, addEventListener() {} });
window.addEventListener = () => {};
window.dispatchEvent = ev => events.push([ev.type, ev.detail]);
const HUB = __HUB__;
// MANUAL: every request waits until the scenario answers it (held[i].answer).
const MANUAL = __MANUAL__;
const held = [];
const reply = body => ({ ok: true, status: 200, json: () => Promise.resolve(body) });
globalThis.fetch = (url, opt) => {
  const call = [url, opt && opt.method || 'GET', opt && opt.body || ''];
  calls.push(call);
  if (!MANUAL) return Promise.resolve(reply(HUB));
  return new Promise(res => held.push({ call, answer: body => res(reply(body)) }));
};
const tick = () => new Promise(r => setTimeout(r, 5));
const out = {};
__HEAD__
(async () => { __SCENARIO__ })().then(() => setTimeout(() => console.log(JSON.stringify({
  storage: Object.fromEntries(mem), style: Object.fromEntries(style), calls, out,
  accentEvents: events.filter(e => e[0] === 'cd-accent').map(e => e[1].accent),
})), 20));
"""


@unittest.skipUnless(NODE, "node is not installed")
class HeadScript(unittest.TestCase):
    def run_head(self, storage: dict, hub: dict | None = None, scenario: str = "") -> dict:
        js = (HARNESS.replace("__STORAGE__", json.dumps(storage))
                     .replace("__HUB__", json.dumps(hub or {}))
                     .replace("__MANUAL__", "true" if hub is None else "false")
                     .replace("__SCENARIO__", scenario)
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

    def test_text_on_the_accent_clears_4_5(self):
        for accent, fg in (("white", "#101214"), ("#0c66e4", "#FFFFFF"),
                           # white reads 4.48:1 and #101214 4.19:1 here; black 4.69:1
                           ("#777777", "#000000"), ("#808080", "#101214")):
            r = self.run_head({}, {"theme": "light", "accent": accent})
            self.assertEqual(r["style"]["--accent-fg"], fg, accent)

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
        for bad in ("notacolour", "transparent", "rgba(1, 2, 3, 0.5)"):
            r = self.run_head({"cd-accent": bad}, {})
            self.assertEqual(r["style"], {}, bad)

    def test_the_first_probe_colour_is_a_colour(self):
        r = self.run_head({}, {}, "out.rgb = cdTheme.rgbOf('#010203'); out.opaque = cdTheme.rgbOf('rgba(1, 2, 3, 1)');")
        self.assertEqual(r["out"], {"rgb": [1, 2, 3], "opaque": [1, 2, 3]})

    def test_a_reply_on_its_way_does_not_undo_a_choice(self):
        r = self.run_head({"cd-accent": "#111111"}, scenario="""
            cdTheme.chooseAccent('#333333');
            held[0].answer({ theme: 'light', accent: '#222222' }); await tick();
        """)
        self.assertEqual(r["storage"]["cd-accent"], "#333333")
        self.assertEqual(r["style"]["--accent"], "#333333")

    def test_an_unsaved_choice_is_not_undone(self):
        # Typing waits before it saves; a sync in that gap keeps the typed colour.
        r = self.run_head({"cd-accent": "#111111"}, scenario="""
            held[0].answer({ theme: 'light', accent: '#111111' }); await tick();
            cdTheme.chooseAccent('#333333');
            cdTheme.sync(); await tick();
            held[1].answer({ theme: 'light', accent: '#111111' }); await tick();
        """)
        self.assertEqual(r["storage"]["cd-accent"], "#333333")

    def test_saves_go_one_at_a_time_newest_last(self):
        r = self.run_head({"cd-accent": "#111111"}, scenario="""
            held[0].answer({ theme: 'light', accent: '#111111' }); await tick();
            const results = [];
            ['#aaaaaa', '#bbbbbb', '#cccccc'].forEach((c, n) => {
              cdTheme.chooseAccent(c);
              cdTheme.saveAccent(c).then(s => { results[n] = s && s.accent; });
            });
            await tick();
            out.waiting = held.length;
            held[1].answer({ theme: 'light', accent: '#aaaaaa' }); await tick();
            held[2].answer({ theme: 'light', accent: '#cccccc' }); await tick();
            out.results = results;
            // Every choice saved: the next sync takes the hub's colour again.
            cdTheme.sync(); await tick();
            held[3].answer({ theme: 'light', accent: '#dddddd' }); await tick();
        """)
        self.assertEqual(r["out"]["waiting"], 2)            # the first GET and one PUT
        self.assertEqual(self.puts(r), [{"accent": "#aaaaaa"}, {"accent": "#cccccc"}])
        self.assertEqual(r["out"]["results"], ["#aaaaaa", None, "#cccccc"])
        self.assertEqual(r["storage"]["cd-accent"], "#dddddd")

    def test_a_failed_save_rejects_and_frees_the_queue(self):
        r = self.run_head({}, {"theme": "light", "accent": "default"}, """
            await tick();
            globalThis.fetch = () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ error: 'x' }) });
            await cdTheme.saveAccent('#aaaaaa').then(() => { out.failed = false; }, e => { out.failed = e.message; });
            globalThis.fetch = (url, opt) => { calls.push([url, opt && opt.method || 'GET', opt && opt.body || '']);
                                               return Promise.resolve(reply({ accent: '#bbbbbb' })); };
            out.next = (await cdTheme.saveAccent('#bbbbbb')).accent;
        """)
        self.assertIn("500", r["out"]["failed"])
        self.assertEqual(r["out"]["next"], "#bbbbbb")


class IndexWiring(unittest.TestCase):
    def test_choice_is_saved_on_the_hub(self):
        src = PAGES["index.html"]
        i = src.index("function setAccent(")
        block = src[i:src.index("\n}\n", i)]
        self.assertIn("cdTheme.chooseAccent(color)", block)
        self.assertIn("cdTheme.saveAccent(color)", block)
        self.assertIn("color || 'default'", block)
        self.assertNotIn("fetch(", block)               # saves go through the head's queue
        self.assertNotIn("function accentFgFor(", src)   # one copy, in the head


if __name__ == "__main__":
    unittest.main()
