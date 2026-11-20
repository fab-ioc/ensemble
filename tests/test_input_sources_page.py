"""#7, cut down in #15: the Input sources panel in a real page.

In headless Chrome (tests/cdp_ws.py: no Node needed), against a hub in a
thread with two initiatives, Motors and Boats, and two sources, Slack (not
working) and Jira (working):

* the avatar menu's Input sources opens the panel on Sources; the list has two
  pages, Sources and the Message log, and nothing else (no source pages, no
  Add input source, no routing table);
* Sources is one health line per source: its name and state, why it is not
  read, when it was last read; colour only on the one not working;
* the setup file: its path, Open setup file and See routing now, which opens
  routing-now.md in the file view (the file view may read it);
* Open setup file shows the whole file in a text box; Save writes it and the
  hub applies it (a section in the plain form is read without Haiku); the
  problems it found show under the box and at the top of the file; a section
  with a problem keeps its last good row, the rest apply (Motors' new topic);
* Esc in the box with nothing changed closes the box, not the panel;
* the Message log is still there;
* /?source=<id> opens the panel on Sources, an unknown id too; an Inbox row
  about a source opens it, and its Done still posts `seen`;
* on a phone the list comes first, a page has Back, nothing scrolls sideways,
  every control is finger-sized, the text box is 16px;
* the page's colours read in every theme.

Skipped without Chrome. With ENSEMBLE_SHOTS set to a folder, pictures are left
there. Made-up names and ids only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import outside  # noqa: E402
import outside_route  # noqa: E402
import outside_setup  # noqa: E402
from tests import cdp_ws  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

THEMES = ["light", "dark", "dim", "paper", "contrast", "fjord", "intellij-dark"]
IDS = {"made-up-cars": "C000TEST01", "made-up-boats": "C000TEST02"}

# Contrast of two CSS colours (WCAG), for the theme checks.
CONTRAST_JS = r"""
// rgb(255 0 0), or color(srgb 1 0 0 / .5) as Chrome gives a color-mix(): channels 0..255.
const chans = c => { const v = (c.match(/[\d.]+/g) || []).map(Number); return /^color\(/.test(c) ? v.map((x, i) => i < 3 ? x * 255 : x) : v; };
const lum = c => { const v = chans(c).slice(0, 3).map(x => x / 255)
  .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4); return .2126 * v[0] + .7152 * v[1] + .0722 * v[2]; };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
// The colour behind an element: its backgrounds and its parents', see-through ones laid over what is under them.
const rgba = c => { const v = chans(c); return [v[0] || 0, v[1] || 0, v[2] || 0, v.length > 3 ? v[3] : 1]; };
const bgOf = e => { const layers = []; for (; e; e = e.parentElement) { const b = rgba(getComputedStyle(e).backgroundColor); if (b[3] > 0) layers.push(b); if (b[3] >= 1) break; }
  let out = [255, 255, 255]; for (const [r, g, b, a] of layers.reverse()) out = [r * a + out[0] * (1 - a), g * a + out[1] * (1 - a), b * a + out[2] * (1 - a)];
  return `rgb(${out.join(',')})`; };
"""


@unittest.skipUnless(CHROME, "needs Chrome")
class InputSourcesPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-is-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "root").mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.state = state
        cls.haiku = []
        cls.lookups = []

        def run(prompt, timeout):
            cls.haiku.append(prompt)
            raise RuntimeError("Haiku is not asked in this test")

        def lookup(names):
            cls.lookups.append(list(names))
            return {n: IDS.get(n, "") for n in names}

        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "root"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "load_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(outside, "_log", lambda *a, **k: None),
            mock.patch.object(outside_route, "_log", lambda *a, **k: None),
            mock.patch.object(outside_setup, "_log", lambda *a, **k: None),
            mock.patch.object(outside_setup, "RUN", run),
            mock.patch.object(outside_setup, "LOOKUP", lookup),
            # No check runs here, and nothing ticks.
            mock.patch.object(outside, "check_now", lambda sid: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        outside.reset()
        outside_setup.reset()
        cls.addClassCleanup(outside.reset)
        cls.addClassCleanup(outside_setup.reset)
        ids = []
        for name in ("Motors", "Boats"):
            ok, proj, why = dashboard.register_project(str(base / "root" / name), name)
            assert ok, why
            ids.append(proj["id"])
        cls.motors, cls.boats = ids
        cls._sources()
        outside_route.set_initiative(cls.motors, {"topic": "made-up cars", "places": {
            "slack": {"channels": ["C000TEST01"]}}, "addItems": ["ABC-12"]}, {"kind": "ceo"})
        outside_setup._remember_ids({"made-up-cars": "C000TEST01"})
        outside_setup.tick(background=False)        # the first file, from the table
        cls.first = outside_setup.setup_path().read_text(encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        # Cleanups, not tearDownClass: they run when setUpClass fails too.
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if cls.shots:
            Path(cls.shots).mkdir(parents=True, exist_ok=True)
        cls.browser = cdp_ws.Browser(cls.tmp.name)
        cls.got = {}
        try:
            cls._desktop()
            cls._phone()
        finally:
            cls.browser.close()

    @classmethod
    def _sources(cls):
        """Slack on and not working for an hour; Jira decide only and working."""
        now = time.time()
        outside.set_source("slack", type="slack", mode="on", settings={"reader": "connector"})
        outside.set_source("jira", type="jira", mode="decide", settings={})
        st = outside._load_state("slack")
        st.update(downSince=now - 3600, reason="the Slack connection did not answer",
                  lastGood=now - 4000, lastCheck=now - 60, failures=3)
        outside._save_state("slack", st)
        st = outside._load_state("jira")
        st.update(lastGood=now - 120, lastCheck=now - 120)
        outside._save_state("jira", st)

    @classmethod
    def _shot(cls, p, name):
        if cls.shots:
            p.shot(Path(cls.shots) / f"{name}.png")

    @classmethod
    def _open(cls, p, path="/"):
        p.goto(cls.base + path)
        p.until("window.ensBooted === true", 40)

    @classmethod
    def _desktop(cls):
        g = cls.got
        p = cls.browser.page(1280, 860)
        cls._open(p)
        p.eval("document.getElementById('me-btn').click(); 0")
        p.eval("[...document.querySelectorAll('#me-menu .me-item')].find(b => b.dataset.me === 'sources').click(); 0")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-health .is-hl-r').length === 2"
                " && document.querySelector('#is-setup [data-setup=\"open\"]')")
        g["menuOrder"] = p.eval("[...document.querySelectorAll('#me-menu > .me-item')].map(b => b.dataset.me || '')")
        g["first"] = p.eval("""(() => { const q = s => document.querySelector(s);
          return {
            nav: [...document.querySelectorAll('#is-nav .is-item')].map(b => [b.dataset.src, b.querySelector('.is-nm').textContent,
              b.querySelector('.is-st').textContent, b.getAttribute('aria-current')]),
            navDot: q('#is-nav [data-src=":sources"] .is-dot').className,
            h: q('#is-page h4').textContent,
            health: [...document.querySelectorAll('#is-health .is-hl-r')].map(li => ({ id: li.dataset.src,
              text: li.querySelector('.is-hl-t').firstChild.textContent + li.querySelector('.is-hl-t').childNodes[1].textContent,
              why: li.querySelector('small').textContent, dot: li.querySelector('.is-dot').className })),
            path: q('#is-setup .is-path').textContent,
            primary: [...document.querySelectorAll('#is-page .rm-btn.primary')].map(b => b.textContent),
            now: q('#is-setup [data-setup="now"]').getAttribute('href'), nowTarget: q('#is-setup [data-setup="now"]').target,
            status: q('#is-setup-st').textContent,
            old: ['.is-types', '#is-rt', '[data-add]', '[data-mode]', '.is-grid', 'input', 'select'].filter(s => q('#is-page ' + s)),
            focus: document.activeElement && document.activeElement.dataset.src }; })()""")
        cls._shot(p, "1 Input sources, cut down")
        # See routing now: the file view may read routing-now.md.
        href = g["first"]["now"]
        g["nowFetch"] = p.eval(f"""fetch({json.dumps(href.replace('/fileview?', '/api/file?'))}).then(async r => ({{ ok: r.ok, status: r.status,
          text: (await r.text()).slice(0, 4000) }}))""")
        # Open setup file: the whole file in a text box, Save the one primary.
        p.eval("document.querySelector('#is-setup [data-setup=\"open\"]').click(); 0")
        p.until("document.getElementById('is-setup-text')")
        g["edit"] = p.eval("""(() => { const t = document.getElementById('is-setup-text');
          return { value: t.value, font: getComputedStyle(t).fontFamily, focus: document.activeElement === t,
            primary: [...document.querySelectorAll('#is-page .rm-btn.primary')].map(b => b.textContent),
            buttons: [...document.querySelectorAll('#is-setup .is-ctl .rm-btn')].map(b => b.textContent),
            moved: document.getElementById('is-setup-moved').hidden, h: t.getBoundingClientRect().height, vh: innerHeight }; })()""")
        cls._shot(p, "2 Setup file open")
        # Esc in the box with nothing typed: the box closes, the panel stays.
        p.eval("document.getElementById('is-setup-text').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 0")
        g["escBox"] = p.eval("""({ box: !!document.getElementById('is-setup-text'), panel: !document.getElementById('sources-panel').hidden,
          focus: document.activeElement && document.activeElement.dataset.setup })""")
        # Edit and save: Boats gets a channel and one Slack cannot see; a section for no initiative.
        p.eval("document.querySelector('#is-setup [data-setup=\"open\"]').click(); 0")
        p.until("document.getElementById('is-setup-text')")
        assert "Topic: made-up cars\n" in cls.first, cls.first
        new = (cls.first.replace("Topic: made-up cars\n", "Topic: made-up cars and vans\n").rstrip("\n") + "\n\n## Boats\nTopic: made-up sailing\nSlack channels: #made-up-boats, #made-up-gone\n"
               "\n## Planes\nTopic: made-up flying\n")
        cls.typed = new
        p.eval(f"{{ const t = document.getElementById('is-setup-text'); t.value = {json.dumps(new)}; t.dispatchEvent(new Event('input', {{bubbles: true}})); }} 0")
        p.eval("document.querySelector('#is-setup [data-setup=\"save\"]').click(); 0")
        p.until("!document.getElementById('is-setup-text') && IS.setup && !IS.setup.applying && IS.setup.lastTry > 0"
                " && document.querySelector('#is-setup .is-probs')", 30)
        g["saved"] = p.eval("""(() => { const q = s => document.querySelector(s);
          return { probs: [...document.querySelectorAll('#is-setup .is-probs li')].map(li => li.textContent),
            head: q('#is-setup .is-probs b').textContent, status: q('#is-setup-st').textContent,
            changes: [...document.querySelectorAll('#is-setup .is-changes li')].map(li => li.textContent),
            focus: document.activeElement && document.activeElement.id }; })()""")
        g["file"] = outside_setup.setup_path().read_text(encoding="utf-8")
        g["rows"] = outside_route.one_table()["rows"]
        g["haiku"] = list(cls.haiku)
        cls._shot(p, "3 After a save with problems")
        p.eval("document.querySelector('#is-setup [data-setup=\"open\"]').click(); 0")
        p.until("document.getElementById('is-setup-text')")
        g["reopened"] = p.eval("document.getElementById('is-setup-text').value")
        cls._shot(p, "4 Setup file with a problem at its top")
        # The hub rewrites only the problems part meanwhile: not a change anyone made, no note.
        outside_setup.setup_path().write_text(outside_setup.with_problems(g["file"], ["made-up other problem"]),
                                              encoding="utf-8")
        p.eval("isSetupLoad(); 0")
        p.until("IS.setup.text.includes('made-up other problem')")
        g["problemsOnly"] = p.eval("document.getElementById('is-setup-moved').hidden")
        # Typed but not saved, and the file changes meanwhile (an agent's section): said above Save.
        p.eval("{ const t = document.getElementById('is-setup-text'); t.value += '\\n'; } 0")
        outside_setup.setup_path().write_text(g["file"] + "\n", encoding="utf-8")
        p.eval("isSetupLoad(); 0")
        p.until("!document.getElementById('is-setup-moved').hidden")
        g["moved"] = p.eval("document.getElementById('is-setup-moved').textContent")
        p.eval("IS.edit.base = document.getElementById('is-setup-text').value; isSetupCancel(); 0")
        # Every theme: the words on the page read.
        g["themes"] = {}
        for t in THEMES:
            p.eval(f"document.documentElement.dataset.theme = '{t}'; 0")
            g["themes"][t] = p.eval("(() => {" + CONTRAST_JS + """
              const pair = e => e ? ratio(getComputedStyle(e).color, bgOf(e)) : 0;
              const q = s => document.querySelector(s);
              return { prob: pair(q('#is-setup .is-probs li')), why: pair(q('#is-health small')), name: pair(q('#is-health .is-hl-t')),
                       path: pair(q('#is-setup .is-path')), status: pair(q('#is-setup-st')), change: pair(q('#is-setup .is-changes li')),
                       navSt: pair(q('#is-nav .is-item[aria-current="true"] .is-st')), kind: pair(q('#is-page .is-kind')) }; })()""")
        p.eval("document.documentElement.dataset.theme = 'light'; 0")
        # The Message log is still a page of its own.
        p.eval("document.querySelector('#is-nav [data-src=\":log\"]').click(); 0")
        p.until("document.querySelector('#is-nav [data-src=\":log\"]').getAttribute('aria-current') === 'true' && document.querySelector('#is-page h4')")
        g["log"] = p.eval("({ h: document.querySelector('#is-page h4').textContent, filters: !!document.querySelector('#is-page [data-lg-f]') })")
        # Esc closes; the focus goes back to the avatar.
        p.eval("document.querySelector('#sources-panel .is-dlg').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 0")
        g["closed"] = p.eval("({ hidden: document.getElementById('sources-panel').hidden, focus: document.activeElement && document.activeElement.id })")
        # A link to a source, and to one that is not there: both open Sources.
        cls._open(p, "/?source=slack")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-health .is-hl-r').length === 2")
        g["deep"] = p.eval("({ url: location.search, cur: document.querySelector('#is-nav [aria-current=\"true\"]').dataset.src })")
        cls._open(p, "/?source=gone-one")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-health .is-hl-r').length === 2")
        g["gone"] = p.eval("document.querySelector('#is-nav [aria-current=\"true\"]').dataset.src")
        p.eval("closeSources(); 0")
        # An Inbox row about a source: where it says it is, its link, and a click opens the panel.
        g["inbox"] = p.eval(f"""(() => {{ const row = {{ id: 'outside:slack:down:1', kind: 'waiting', hub: true, roomId: '', projectId: '', project: '',
            task: 'Input sources', source: 'slack', open: 'source', line: 'Slack is not read: not working since 10:00', since: 1 }};
          const d = {{ rows: [row], older: [] }};
          const html = ibxHtml(d, 'project', '{cls.motors}', Date.now() / 1000, false);
          const box = document.getElementById('ph-inbox-body'); box.innerHTML = html;
          const a = box.querySelector('a.ibx-link');
          const out = {{ href: a.getAttribute('href'), where: box.querySelector('.ibx-where').textContent, shown: ibxRows(d, 'project', '{cls.motors}').length,
            done: !!box.querySelector('[data-ibx-done]'), req: ibxRowReq({{ ...row, kind: 'check' }}) }};
          a.click();
          out.opened = !document.getElementById('sources-panel').hidden;
          return out; }})()""")
        p.until("document.querySelectorAll('#is-health .is-hl-r').length === 2")
        p.close()
        # routing-now.md in the file view, for the picture.
        if cls.shots:
            f = cls.browser.page(1100, 860)
            f.goto(cls.base + href)
            f.until("/Routing now/.test(document.body.innerText) && /Motors/.test(document.body.innerText)", 30)
            cls._shot(f, "5 routing-now.md in the file view")
            f.close()

    @classmethod
    def _phone(cls):
        g = cls.got
        p = cls.browser.page(390, 844, mobile=True)
        cls._open(p)
        # The page's warning about how the test's screen is drawn is not this panel's.
        p.eval("{ const w = document.getElementById('wide-banner'); if (w) w.hidden = true; } 0")
        p.eval("openSources('', document.getElementById('me-btn')); 0")
        p.until("document.querySelector('#is-nav [data-src=\":sources\"]')")
        R = "(e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), r.width > 0]; })"
        g["phoneList"] = p.eval(f"""({{ nav: {R}(document.getElementById('is-nav')), page: {R}(document.getElementById('is-page')),
          back: {R}(document.getElementById('is-back')), title: document.getElementById('is-title').textContent }})""")
        cls._shot(p, "6 Phone list")
        p.eval("document.querySelector('#is-nav [data-src=\":sources\"]').click(); 0")
        p.until("document.querySelector('#sources-panel .is-dlg').classList.contains('is-on-page')"
                " && document.querySelector('#is-setup [data-setup=\"open\"]')")
        small = """[...document.getElementById('is-page').querySelectorAll('button, a, textarea')]
          .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height < 43).map(e => e.outerHTML.slice(0, 80))"""
        g["phonePage"] = p.eval(f"""(() => {{ const pg = document.getElementById('is-page');
          const els = [...pg.querySelectorAll('button, a, textarea')].filter(e => e.getBoundingClientRect().width);
          return {{ nav: {R}(document.getElementById('is-nav')), back: {R}(document.getElementById('is-back')),
            title: document.getElementById('is-title').textContent, focus: document.activeElement.id,
            wide: Math.max(...els.map(e => e.getBoundingClientRect().right)), sw: pg.scrollWidth, cw: pg.clientWidth, vw: innerWidth,
            small: {small} }}; }})()""")
        cls._shot(p, "7 Phone sources")
        p.eval("document.querySelector('#is-setup [data-setup=\"open\"]').click(); 0")
        p.until("document.getElementById('is-setup-text')")
        g["phoneEdit"] = p.eval(f"""(() => {{ const pg = document.getElementById('is-page'); const t = document.getElementById('is-setup-text');
          return {{ fs: getComputedStyle(t).fontSize, sw: pg.scrollWidth, cw: pg.clientWidth, small: {small} }}; }})()""")
        cls._shot(p, "8 Phone setup file")
        p.eval("isSetupCancel(); document.getElementById('is-back').click(); 0")
        g["phoneBack"] = p.eval(f"({{ nav: {R}(document.getElementById('is-nav')), focus: document.activeElement.dataset.src }})")
        p.close()

    # ---- the tests

    def test_the_list_is_sources_and_the_message_log_only(self):
        g = self.got
        self.assertEqual(g["menuOrder"][-2:], ["sources", "settings"], "Input sources sits just above Settings")
        f = g["first"]
        self.assertEqual([n[:2] for n in f["nav"]], [[":sources", "Sources"], [":log", "Message log"]])
        self.assertEqual(f["nav"][0][2], "1 not working")
        self.assertEqual([n[3] for n in f["nav"]], ["true", "false"])
        self.assertEqual(f["navDot"], "is-dot bad")
        self.assertEqual(f["h"], "Sources")
        self.assertEqual(f["old"], [], "no source pages, no Add, no routing table, no form fields")
        self.assertEqual(f["focus"], ":sources")

    def test_one_health_line_per_source(self):
        h = {x["id"]: x for x in self.got["first"]["health"]}
        self.assertEqual(h["slack"]["text"], "Slack · On · Not working")
        self.assertRegex(h["slack"]["why"], r"^Not working since \d\d:\d\d: the Slack connection did not answer · Last read \d\d:\d\d$")
        self.assertEqual(h["slack"]["dot"], "is-dot bad")
        self.assertEqual(h["jira"]["text"], "Jira · Decide only · Working")
        self.assertRegex(h["jira"]["why"], r"^Last read \d\d:\d\d$")
        self.assertEqual(h["jira"]["dot"], "is-dot", "a working source has no colour")

    def test_setup_file_and_routing_now(self):
        f = self.got["first"]
        self.assertEqual(f["path"], str(outside_setup.setup_path()))
        self.assertEqual(f["primary"], ["Open setup file"], "one primary button")
        self.assertTrue(f["now"].startswith("/fileview?"), f["now"])
        self.assertEqual(f["nowTarget"], "_blank")
        self.assertRegex(f["status"], r"^Applied \d\d:\d\d\.$")
        n = self.got["nowFetch"]
        self.assertTrue(n["ok"], n)
        self.assertIn("Routing now", n["text"])
        self.assertIn("Do not edit this file", n["text"])
        self.assertIn("C000TEST01", n["text"])

    def test_open_setup_file_shows_the_whole_file(self):
        e = self.got["edit"]
        self.assertEqual(e["value"], self.first)
        self.assertIn("mono", e["font"].lower())
        self.assertTrue(e["focus"])
        self.assertEqual(e["primary"], ["Save"], "Save is the one primary while editing")
        self.assertEqual(e["buttons"], ["Save", "Cancel"])
        self.assertTrue(e["moved"])
        self.assertGreaterEqual(e["h"], e["vh"] * 0.45)
        self.assertEqual(self.got["escBox"], {"box": False, "panel": True, "focus": "open"})

    def test_save_applies_and_shows_the_problems(self):
        g = self.got
        s = g["saved"]
        self.assertEqual(s["head"], "2 problems on the last save")
        self.assertEqual(len(s["probs"]), 2, s["probs"])
        self.assertTrue(any("Planes" in x for x in s["probs"]), s["probs"])
        self.assertTrue(any("#made-up-gone" in x and "Boats" in x for x in s["probs"]), s["probs"])
        self.assertRegex(s["status"], r"^Applied \d\d:\d\d\.$")
        self.assertEqual(s["focus"], "is-setup-st", "the focus goes to what happened")
        self.assertEqual(g["haiku"], [], "sections in the plain form are read without Haiku")
        # The problems are at the top of the file, and the rest is as typed.
        top = g["file"].split("## Sources")[0]
        for x in s["probs"]:
            self.assertIn(x, top)
        self.assertEqual(outside_setup.without_problems(g["file"]), outside_setup.without_problems(self.typed))
        self.assertEqual(g["reopened"], g["file"], "the box shows the file with its problems part")
        # Boats has a problem: its row keeps its last good form (nothing set); Motors' new topic applies.
        rows = {r["projectName"]: r for r in g["rows"]}
        self.assertEqual(rows["Motors"]["topic"], "made-up cars and vans")
        self.assertTrue(any("Motors" in c for c in s["changes"]), s["changes"])
        self.assertNotEqual(rows.get("Boats", {}).get("topic"), "made-up sailing")
        self.assertIn("changed since you opened it", g["moved"])
        self.assertTrue(g["problemsOnly"], "a rewrite of the problems part alone is not shown as a change")

    def test_message_log_is_unchanged(self):
        self.assertEqual(self.got["log"], {"h": "Message log", "filters": True})

    def test_colours_read_in_every_theme(self):
        for theme, m in self.got["themes"].items():
            for k, v in m.items():
                with self.subTest(theme=theme, what=k):
                    self.assertGreaterEqual(v, 4.5, self.got["themes"][theme])

    def test_esc_closes_and_links_open_it(self):
        g = self.got
        self.assertEqual(g["closed"], {"hidden": True, "focus": "me-btn"})
        self.assertEqual(g["deep"], {"url": "", "cur": ":sources"})
        self.assertEqual(g["gone"], ":sources")
        i = g["inbox"]
        self.assertEqual(i["href"], "/?source=slack")
        self.assertEqual(i["where"], "Input sources")
        self.assertEqual(i["shown"], 1, "a source's row is in every project's Inbox")
        self.assertFalse(i["done"], "a not working row has no Done")
        self.assertEqual(i["req"], ["/api/outside/sources", {"action": "seen", "source": "slack"}])
        self.assertTrue(i["opened"])

    def test_phone(self):
        g = self.got
        lst = g["phoneList"]
        self.assertTrue(lst["nav"][2])
        self.assertFalse(lst["page"][2], "the list first")
        self.assertFalse(lst["back"][2])
        self.assertEqual(lst["title"], "Input sources")
        pg = g["phonePage"]
        self.assertFalse(pg["nav"][2])
        self.assertTrue(pg["back"][2])
        self.assertEqual(pg["title"], "Sources")
        self.assertEqual(pg["focus"], "is-back")
        self.assertLessEqual(pg["wide"], pg["vw"])
        self.assertLessEqual(pg["sw"], pg["cw"], "nothing scrolls sideways")
        self.assertEqual(pg["small"], [], "every control is finger-sized")
        e = g["phoneEdit"]
        self.assertEqual(e["fs"], "16px", "a phone does not zoom into the box")
        self.assertLessEqual(e["sw"], e["cw"])
        self.assertEqual(e["small"], [])
        self.assertTrue(g["phoneBack"]["nav"][2])
        self.assertEqual(g["phoneBack"]["focus"], ":sources")


if __name__ == "__main__":
    unittest.main()
