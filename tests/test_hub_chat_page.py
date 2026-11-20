"""#16: Ask the hub, in a real page.

In headless Chrome (tests/cdp_ws.py), against a hub in a thread with two
initiatives, Harbour and Lighthouse, and a fake Haiku:

* the avatar menu's Ask the hub opens the dialog, the box has the focus, and
  it says what can be asked; Input sources' Ask the hub opens it too;
* a request answered with a change shows the changed lines (+ on the add
  ground), what the hub's check says, and Apply (the one primary) / Discard;
  nothing changes until Apply; Apply applies it as the hub chat;
* a request answered in words shows the words;
* a change made from a file that changed since shows as stale, with Ask again;
* Esc closes and gives the avatar the focus back;
* the words read in every theme;
* on a phone the dialog is the whole screen, every control is finger-sized,
  the box is 16px and nothing scrolls sideways;
* #21: an icon in its own colour is always in the bar and opens or closes the
  panel, docked at the right under the bar; the page narrows beside it (the
  board, a task panel), nothing else covers it, it stays open on another page,
  its width (splitter: keys and a drag) and open or closed come back after a
  reload; a dot on the icon says a change waits for Apply, also while closed;
  Esc on the icon closes it; on a phone the icon is 44px in row two, the bar
  fits at 360 and 390, the panel is a full-width sheet and is not reopened on
  a load.

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

import agent_models  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import hub_chat  # noqa: E402
import outside  # noqa: E402
import outside_route  # noqa: E402
import outside_setup  # noqa: E402
from tests import cdp_ws  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402
from tests.test_input_sources_page import CONTRAST_JS, THEMES  # noqa: E402

IDS = {"made-up-docks": "C000TEST01", "made-up-lamps": "C000TEST02"}


@unittest.skipUnless(CHROME, "needs Chrome")
class AskTheHubPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-hc-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("root", "transcripts", "cs"):
            (base / d).mkdir()
        cls.answers: list[str] = []
        cls.prompts: list[str] = []

        def run(prompt, timeout):
            cls.prompts.append(prompt)
            return cls.answers.pop(0) if cls.answers else "REPLY: (no answer set)"

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
            mock.patch.object(hub_chat, "_log", lambda *a, **k: None),
            mock.patch.object(outside_setup, "RUN", lambda p, t: (_ for _ in ()).throw(RuntimeError("not asked"))),
            mock.patch.object(outside_setup, "LOOKUP", lambda names: {n: IDS.get(n, "") for n in names}),
            mock.patch.object(hub_chat, "RUN", run),
            mock.patch.object(outside, "check_now", lambda sid: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
            # The hub's Settings (#410): saved in the test's own folder only.
            mock.patch.object(dashboard.usage, "remark_codex", lambda: None),
            mock.patch.object(agent_models, "codex_models", lambda path=None: [
                {"id": "gpt-6-sol", "name": "gpt-6-sol", "description": "", "efforts": ["low", "medium", "high"],
                 "defaultEffort": "medium", "pool": ""}]),
            mock.patch.object(agent_models, "codex_own", lambda: {"model": "gpt-6-sol", "effort": ""}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        for m in (outside, outside_setup, hub_chat):
            m.reset()
            cls.addClassCleanup(m.reset)
        ids = []
        for name in ("Harbour", "Lighthouse"):
            ok, proj, why = dashboard.register_project(str(base / "root" / name), name)
            assert ok, why
            ids.append(proj["id"])
        cls.harbour, cls.lighthouse = ids
        outside.set_source("slack", type="slack", mode="on", settings={"reader": "connector"})
        outside.set_source("jira", type="jira", mode="decide", settings={})
        outside_route.set_initiative(cls.harbour, {"topic": "made-up shipping", "places": {
            "slack": {"channels": ["C000TEST01"]}}, "addItems": ["ABC-12"]}, {"kind": "ceo"})
        outside_route.add_items_auto({cls.harbour: [("ticket", "ABC-99", "task #7")]}, "made up")
        outside_setup._remember_ids(IDS)
        outside_setup.tick(background=False)
        cls.first = outside_setup.setup_path().read_text(encoding="utf-8")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
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
            cls._settings()
            cls._docked()
            cls._docked_phone()
        finally:
            cls.browser.close()

    @classmethod
    def _shot(cls, p, name):
        if cls.shots:
            p.shot(Path(cls.shots) / f"{name}.png")

    @classmethod
    def _open(cls, p, path="/"):
        p.goto(cls.base + path)
        p.until("window.ensBooted === true", 40)

    @classmethod
    def _ask(cls, p, text, turns):
        p.eval(f"{{ const t = document.getElementById('hc-in'); t.value = {json.dumps(text)}; }} 0")
        p.eval("document.getElementById('hc-send').click(); 0")
        p.until(f"HC.data && !HC.data.busy && HC.data.turns.length === {turns}"
                " && !document.querySelector('#hc-turns .hc-wait')", 20)

    @classmethod
    def _desktop(cls):
        g = cls.got
        p = cls.browser.page(1280, 860)
        cls._open(p)
        p.eval("document.getElementById('me-btn').click(); 0")
        p.eval("document.querySelector('#me-menu [data-me=\"hubchat\"]').click(); 0")
        p.until("!document.getElementById('hub-chat').hidden && HC.data")
        g["open"] = p.eval("""({ focus: document.activeElement.id, intro: document.querySelector('#hc-turns .hc-intro').textContent,
          menu: [...document.querySelectorAll('#me-menu > .me-item')].map(b => b.dataset.me || '') })""")
        # A change: take ABC-99 out of Harbour.
        new = cls.first.replace("Work items: ABC-12\n", "Work items: ABC-12\nNot: ABC-99\n")
        assert new != cls.first, cls.first
        cls.answers.append(f"CHANGE: ABC-99 is taken out of Harbour; the hub will not add it back.\n<<<SETUP outside\n{new}SETUP>>>")
        cls._ask(p, "Take ABC-99 out of Harbour", 2)
        g["change"] = p.eval("""(() => { const h = [...document.querySelectorAll('#hc-turns .hc-h')].pop();
          return { said: h.querySelector('.hc-said').textContent,
            add: [...h.querySelectorAll('.hc-diff .add')].map(d => d.textContent),
            del: [...h.querySelectorAll('.hc-diff .del')].map(d => d.textContent),
            sec: [...h.querySelectorAll('.hc-diff .sec')].map(d => d.textContent),
            secs: [...h.querySelectorAll('.hc-sec b')].map(b => b.textContent),
            changes: [...h.querySelectorAll('.hc-sec li')].map(b => b.textContent),
            primary: [...document.querySelectorAll('#hub-chat .rm-btn.primary')].map(b => b.textContent),
            buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent),
            state: h.querySelector('.hc-state').textContent,
            sign: getComputedStyle(h.querySelector('.hc-diff .add'), '::before').content }; })()""")
        g["beforeApply"] = list(outside_route.not_keys(outside_route._load_one()["rows"][cls.harbour]))
        cls._shot(p, "1 A proposed change")
        # Every theme, while a change with its check shows.
        g["themes"] = {}
        for t in THEMES:
            p.eval(f"document.documentElement.dataset.theme = '{t}'; 0")
            g["themes"][t] = p.eval("(() => {" + CONTRAST_JS + """
              const pair = e => e ? ratio(getComputedStyle(e).color, bgOf(e)) : 0;
              const q = s => document.querySelector(s);
              return { said: pair(q('#hc-turns .hc-said')), from: pair(q('#hc-turns .hc-from')), user: pair(q('#hc-turns .hc-u')),
                       add: pair(q('#hc-turns .hc-diff .add')), section: pair(q('#hc-turns .hc-diff .sec')),
                       change: pair(q('#hc-turns .hc-sec li')),
                       state: pair(q('#hc-turns .hc-state')), intro: pair(q('#hc-turns .hc-intro li')) }; })()""")
        p.eval("document.documentElement.dataset.theme = 'light'; 0")
        p.eval("document.querySelector('#hc-turns [data-hc=\"apply\"]').click(); 0")
        p.until("HC.data && HC.data.turns[1].state === 'applied' && /Applied/.test(document.querySelector('#hc-turns .hc-state').textContent)", 20)
        g["applied"] = p.eval("""(() => { const h = [...document.querySelectorAll('#hc-turns .hc-h')].pop();
          return { state: h.querySelector('.hc-state').textContent, secs: [...h.querySelectorAll('.hc-sec b')].map(b => b.textContent),
            buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent) }; })()""")
        g["row"] = outside_route._load_one()["rows"][cls.harbour]
        g["file"] = outside_setup.setup_path().read_text(encoding="utf-8")
        cls._shot(p, "2 After Apply")
        # A reply in words.
        cls.answers.append("REPLY: Lighthouse gets nothing yet: it has no section in the setup file. "
                           "Say which Slack channels or Jira projects should go to it.")
        cls._ask(p, "What goes to Lighthouse now?", 4)
        g["reply"] = p.eval("[...document.querySelectorAll('#hc-turns .hc-h')].pop().querySelector('.hc-said').textContent")
        cls._shot(p, "3 A reply in words")
        # A change made from a file that changed since: stale, Ask again.
        cur = outside_setup.setup_path().read_text(encoding="utf-8")
        cls.answers.append("CHANGE: Harbour's topic is now made-up ferries.\n<<<SETUP outside\n"
                           + cur.replace("made-up shipping", "made-up ferries") + "SETUP>>>")
        cls._ask(p, "Harbour's topic is ferries", 6)
        outside_setup.setup_path().write_text(cur.replace("Work items: ABC-12", "Work items: ABC-13"), encoding="utf-8")
        p.eval("hcLoad(); 0")
        p.until("document.querySelector('#hc-turns [data-hc=\"again\"]')")
        g["stale"] = p.eval("""(() => { const h = [...document.querySelectorAll('#hc-turns .hc-h')].pop();
          return { state: h.querySelector('.hc-state').textContent, buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent) }; })()""")
        cls._shot(p, "4 Stale: the file changed meanwhile")
        # Clear asks first: one click only arms it, and closing disarms it.
        n = p.eval("HC.data.turns.length")
        p.eval("document.getElementById('hc-clear').click(); 0")
        g["clearArmed"] = p.eval("({ text: document.getElementById('hc-clear').textContent, turns: HC.data.turns.length })")
        g["clearArmed"]["before"] = n
        # Esc closes; the focus goes back to the avatar.
        p.eval("document.getElementById('hc-in').dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 0")
        g["closed"] = p.eval("({ hidden: document.getElementById('hub-chat').hidden, focus: document.activeElement && document.activeElement.id })")
        g["clearAfterClose"] = p.eval("document.getElementById('hc-clear').textContent")
        # Input sources' button opens it.
        p.eval("openSources('', document.getElementById('me-btn')); 0")
        p.until("document.querySelector('#is-setup [data-setup=\"ask\"]')")
        p.eval("document.querySelector('#is-setup [data-setup=\"ask\"]').click(); 0")
        g["fromSources"] = p.eval("({ chat: !document.getElementById('hub-chat').hidden, sources: !document.getElementById('sources-panel').hidden })")
        p.eval("closeHubChat(); 0")
        p.close()

    @classmethod
    def _phone(cls):
        g = cls.got
        p = cls.browser.page(390, 844, mobile=True)
        cls._open(p)
        p.eval("{ const w = document.getElementById('wide-banner'); if (w) w.hidden = true; } 0")
        p.eval("openHubChat(document.getElementById('me-btn')); 0")
        p.until("HC.data && document.querySelector('#hc-turns .hc-diff')")
        small = """[...document.querySelectorAll('#hub-chat button, #hub-chat textarea')]
          .filter(e => e.getBoundingClientRect().width && e.getBoundingClientRect().height < 43).map(e => e.outerHTML.slice(0, 80))"""
        g["phone"] = p.eval(f"""(() => {{ const d = document.querySelector('#hub-chat .hc-dlg').getBoundingClientRect();
          const b = document.getElementById('hc-turns');
          return {{ w: Math.round(d.width), vw: innerWidth, fs: getComputedStyle(document.getElementById('hc-in')).fontSize,
            sw: b.scrollWidth, cw: b.clientWidth, small: {small} }}; }})()""")
        cls._shot(p, "5 Phone")
        # #410: the History view's controls are finger-sized too.
        p.eval("document.getElementById('hc-hist').click(); 0")
        p.until("HC.hist && document.querySelector('#hc-hist-view [data-hcf]')", 20)
        g["phoneHist"] = p.eval(f"""(() => {{ const b = document.getElementById('hc-hist-view');
          return {{ sw: b.scrollWidth, cw: b.clientWidth, small: {small} }}; }})()""")
        cls._shot(p, "5b Phone History")
        p.eval("document.getElementById('hc-hist-back').click(); 0")
        p.close()

    LAST = "[...document.querySelectorAll('#hc-turns .hc-h')].pop()"

    @classmethod
    def _settings(cls):
        """#410: a Settings change (one row per setting, old and new), its
        Apply, a stale one, and the History view."""
        g = cls.got
        p = cls.browser.page(1280, 860)
        cls._open(p)
        p.eval("openHubChat(document.getElementById('me-btn')); 0")
        p.until("HC.data && !document.getElementById('hub-chat').hidden")
        n = p.eval("HC.data.turns.length")
        cls.answers.append('SETTINGS: The PO checks every 15 minutes, and a task gets at most 3 review rounds.\n'
                           '<<<SETTINGS\n{"digestIntervalMin": 15, "reviewRoundsPerTask": 3}\nSETTINGS>>>')
        cls._ask(p, "Check every 15 minutes and stop reviews after 3 rounds", n + 2)
        g["setCard"] = p.eval(f"""(() => {{ const h = {cls.LAST};
          return {{ said: h.querySelector('.hc-said').textContent, title: h.querySelector('.hc-ch-title').textContent,
            rows: [...h.querySelectorAll('.hc-set')].map(r => [r.querySelector('.nm').textContent, r.querySelector('.where').textContent,
              r.querySelector('.del').textContent, r.querySelector('.add').textContent]),
            lines: h.querySelectorAll('.hc-diff > .add, .hc-diff > .del').length,
            buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent),
            state: h.querySelector('.hc-state').textContent }}; }})()""")
        g["setBefore"] = dashboard.load_settings()["reviewRoundsPerTask"]
        cls._shot(p, "11 A Settings change")
        g["setThemes"] = {}
        for t in THEMES:
            p.eval(f"document.documentElement.dataset.theme = '{t}'; 0")
            g["setThemes"][t] = p.eval("(() => {" + CONTRAST_JS + """
              const pair = e => e ? ratio(getComputedStyle(e).color, bgOf(e)) : 0;
              const q = s => [...document.querySelectorAll(s)].pop();
              return { title: pair(q('#hc-turns .hc-ch-title')), name: pair(q('#hc-turns .hc-set .nm')),
                       where: pair(q('#hc-turns .hc-set .where')), old: pair(q('#hc-turns .hc-set .del')),
                       now: pair(q('#hc-turns .hc-set .add')) }; })()""")
        p.eval("document.documentElement.dataset.theme = 'light'; 0")
        p.eval(f"{cls.LAST}.querySelector('[data-hc=\"apply\"]').click(); 0")
        p.until(f"HC.data && HC.data.turns[{n + 1}].state === 'applied' && /Applied/.test({cls.LAST}.querySelector('.hc-state').textContent)", 20)
        g["setApplied"] = p.eval(f"""(() => {{ const h = {cls.LAST};
          return {{ state: h.querySelector('.hc-state').textContent, secs: [...h.querySelectorAll('.hc-sec b')].map(b => b.textContent),
            rows: h.querySelectorAll('.hc-set').length, buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent) }}; }})()""")
        s = dashboard.load_settings()
        g["setSaved"] = (s["digestIntervalMin"], s["reviewRoundsPerTask"])
        # Stale: its own setting changed in Settings meanwhile.
        cls.answers.append('SETTINGS: Prefer: Codex.\n<<<SETTINGS\n{"preferAgent": "codex"}\nSETTINGS>>>')
        cls._ask(p, "Make reviews Codex", n + 4)
        dashboard.save_settings({"preferAgent": "claude"})
        p.eval("hcLoad(); 0")
        p.until(f"{cls.LAST}.querySelector('[data-hc=\"again\"]')")
        g["setStale"] = p.eval(f"""(() => {{ const h = {cls.LAST};
          return {{ state: h.querySelector('.hc-state').textContent, buttons: [...h.querySelectorAll('.hc-acts .rm-btn')].map(b => b.textContent) }}; }})()""")
        cls._shot(p, "12 A stale Settings change")
        p.eval(f"{cls.LAST}.querySelector('[data-hc=\"discard\"]').click(); 0")
        p.until(f"HC.data && HC.data.turns[{n + 3}].state === 'discarded'", 20)
        # History: what was applied, newest first; Clear is hidden meanwhile.
        VIEW = """(() => { const shown = id => { const e = document.getElementById(id); return !!e && getComputedStyle(e).display !== 'none' && e.getClientRects().length > 0; };
          const v = document.getElementById('hc-hist-view');
          return { pressed: document.getElementById('hc-hist').getAttribute('aria-pressed'),
            turns: shown('hc-turns'), compose: shown('hc-chat-foot'), clear: shown('hc-clear'), hist: shown('hc-hist-view'), back: shown('hc-hist-foot'),
            h: Math.round(v.getBoundingClientRect().height), focus: document.activeElement && document.activeElement.id,
            items: [...v.querySelectorAll('.hc-ev')].map(e => [e.querySelector('.loz').textContent, (e.querySelector('.asked') || {}).textContent || '',
              [...e.querySelectorAll('tr, li')].map(r => r.textContent)]),
            filter: [...v.querySelectorAll('[data-hcf]')].map(b => b.textContent + (b.getAttribute('aria-pressed') === 'true' ? '*' : '')) }; })()"""
        g["chatView"] = p.eval(VIEW)
        p.eval("document.getElementById('hc-hist').click(); 0")
        p.until("HC.hist && document.querySelector('#hc-hist-view .hc-ev')", 20)
        g["hist"] = p.eval(VIEW)
        cls._shot(p, "13 History")
        g["histThemes"] = {}
        for t in THEMES:
            p.eval(f"document.documentElement.dataset.theme = '{t}'; 0")
            g["histThemes"][t] = p.eval("(() => {" + CONTRAST_JS + """
              const pair = e => e ? ratio(getComputedStyle(e).color, bgOf(e)) : 0;
              const q = s => document.querySelector(s);
              return { intro: pair(q('#hc-hist-view .hc-hist-intro')), top: pair(q('#hc-hist-view .hc-ev .top')),
                       asked: pair(q('#hc-hist-view .hc-ev .asked')), name: pair(q('#hc-hist-view .hc-ev td.k')),
                       old: pair(q('#hc-hist-view .hc-ev .old')), line: pair(q('#hc-hist-view .hc-ev li')) }; })()""")
        p.eval("document.documentElement.dataset.theme = 'light'; 0")
        p.eval("document.querySelector('#hc-hist-view [data-hcf=\"settings\"]').click(); 0")
        g["histSettings"] = p.eval(VIEW)
        p.eval("document.querySelector('#hc-hist-view [data-hcf=\"routing\"]').click(); 0")
        g["histRouting"] = p.eval(VIEW)
        p.eval("document.querySelector('#hc-hist-view [data-hcf=\"all\"]').click(); 0")
        p.eval("document.getElementById('hc-hist-back').click(); 0")
        g["histBack"] = p.eval(VIEW)
        p.eval("closeHubChat(); 0")
        p.close()

    PANEL = """(() => { const r = document.getElementById('hub-chat').getBoundingClientRect();
      const m = document.querySelector('main').getBoundingClientRect();
      const h = document.querySelector('header').getBoundingClientRect();
      const b = document.getElementById('hc-btn');
      return { hidden: document.getElementById('hub-chat').hidden, left: Math.round(r.left), right: Math.round(r.right),
        top: Math.round(r.top), w: Math.round(r.width), vw: innerWidth, barBottom: Math.round(h.bottom),
        mainRight: Math.round(m.right), pressed: b.getAttribute('aria-pressed'),
        dot: !b.querySelector('.hc-dot').hidden, label: b.getAttribute('aria-label') }; })()"""

    # Anything visible and fixed or absolute, outside the panel and the bar,
    # that covers part of the open panel.
    OVER = """(() => { const hc = document.getElementById('hub-chat'), p = hc.getBoundingClientRect();
      return [...document.querySelectorAll('body *')].filter(e => {
        if (hc.contains(e) || e.closest('header') || e.contains(hc)) return false;
        const cs = getComputedStyle(e);
        if (!['fixed', 'absolute', 'sticky'].includes(cs.position) || cs.visibility === 'hidden' || cs.display === 'none'
            || +cs.opacity === 0 || cs.pointerEvents === 'none') return false;
        const r = e.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) return false;
        return r.right > p.left + 1 && r.left < p.right - 1 && r.bottom > p.top + 1 && r.top < p.bottom - 1;
      }).map(e => e.tagName + '#' + e.id + '.' + e.className).slice(0, 8); })()"""

    @classmethod
    def _docked(cls):
        g = cls.got
        p = cls.browser.page(1280, 860)
        cls._open(p)
        p.eval("localStorage.removeItem('cd-hub-chat'); 0")
        p.eval("window.ensBooted = false; 0")
        p.goto(cls.base + "/")
        p.until("window.ensBooted === true", 40)
        b = """(() => { const b = document.getElementById('hc-btn'), r = b.getBoundingClientRect(), h = document.querySelector('header');
          return { w: Math.round(r.width), h: Math.round(r.height), inBar: h.contains(b), colour: getComputedStyle(b.querySelector('.hc-ico')).color,
            tool: getComputedStyle(document.documentElement).getPropertyValue('--tool-ask').trim(),
            barFits: h.scrollWidth <= h.clientWidth }; })()"""
        g["icon"] = p.eval(b)
        g["iconClosed"] = p.eval(cls.PANEL)
        # The icon opens it, docked; the page narrows.
        p.eval("document.getElementById('hc-btn').click(); 0")
        p.until("HC.data && !document.getElementById('hub-chat').hidden")
        g["iconOpen"] = p.eval(cls.PANEL)
        g["iconFocus"] = p.eval("document.activeElement.id")
        # A change waits: the dot, open and closed.
        cur = outside_setup.setup_path().read_text(encoding="utf-8")
        cls.answers.append("CHANGE: Harbour's topic is now made-up boats.\n<<<SETUP outside\n"
                           + cur.replace("made-up shipping", "made-up boats") + "SETUP>>>")
        n = p.eval("HC.data.turns.length")
        cls._ask(p, "Harbour's topic is boats", n + 2)
        g["dotOpen"] = p.eval(cls.PANEL)
        p.eval("document.getElementById('hc-btn').click(); 0")
        g["dotClosed"] = p.eval(cls.PANEL)
        g["closeFocus"] = p.eval("document.activeElement.id")
        # Esc on the icon closes it too.
        p.eval("document.getElementById('hc-btn').click(); 0")
        p.eval("document.getElementById('hc-btn').focus(); document.getElementById('hc-btn').dispatchEvent("
               "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 0")
        g["escIcon"] = p.eval("document.getElementById('hub-chat').hidden")
        # Open on Harbour's board: it stays open, the board narrows, a task
        # panel sits beside it, nothing covers it.
        p.eval("document.getElementById('hc-btn').click(); 0")
        p.eval(f"openProjectPage({json.dumps(cls.harbour)}); 0")
        p.until(f"SELECTED_PROJECT === {json.dumps(cls.harbour)}")
        p.eval("new Promise(r => setTimeout(r, 400))")
        g["board"] = p.eval(cls.PANEL)
        g["boardView"] = p.eval("Math.round(document.getElementById('view').getBoundingClientRect().right)")
        g["over"] = p.eval(cls.OVER)
        cls._shot(p, "6 Docked on a board, a change waits")
        p.eval("document.documentElement.dataset.theme = 'dark'; 0")
        cls._shot(p, "7 Docked on a board, dark")
        g["iconDark"] = p.eval("({ colour: getComputedStyle(document.querySelector('#hc-btn .hc-ico')).color,"
                               " tool: getComputedStyle(document.documentElement).getPropertyValue('--tool-ask').trim() })")
        p.eval("document.documentElement.dataset.theme = 'light'; 0")
        p.eval("document.body.classList.add('detail-open'); 0")
        p.eval("new Promise(r => setTimeout(r, 400))")
        g["task"] = p.eval("""(() => { const d = document.getElementById('detail-panel').getBoundingClientRect();
          return { right: Math.round(d.right), left: Math.round(d.left) }; })()""")
        p.eval("document.body.classList.remove('detail-open'); 0")
        # The width: three ← (16px each), then a drag 100px to the left.
        p.eval("document.getElementById('hc-split').focus(); 0")
        for _ in range(3):
            p.eval("document.getElementById('hc-split').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true})); 0")
        g["keys"] = p.eval(cls.PANEL)
        r = p.eval("(() => { const r = document.getElementById('hc-split').getBoundingClientRect(); return [r.left + r.width / 2, r.top + 200]; })()")
        x, y = r
        for t, xx in (("mousePressed", x), ("mouseMoved", x - 50), ("mouseMoved", x - 100), ("mouseReleased", x - 100)):
            p.send("Input.dispatchMouseEvent", {"type": t, "x": xx, "y": y, "button": "left",
                                                "buttons": 0 if t == "mouseReleased" else 1, "clickCount": 1})
        g["drag"] = p.eval(cls.PANEL)
        # A reload: open, at that width.
        p.eval("window.ensBooted = false; 0")
        p.goto(cls.base + "/")
        p.until("window.ensBooted === true", 40)
        p.until("HC.data")
        g["reload"] = p.eval(cls.PANEL)
        # Closed stays closed after a reload.
        p.eval("document.getElementById('hc-btn').click(); 0")
        p.eval("window.ensBooted = false; 0")
        p.goto(cls.base + "/")
        p.until("window.ensBooted === true", 40)
        time.sleep(1.5)
        g["reloadClosed"] = p.eval(cls.PANEL)
        # #409: closed, the page reads nothing for it, on load either.
        g["closedReads"] = p.eval("performance.getEntriesByType('resource')"
                                  ".filter(e => /\\/api\\/hub-chat/.test(e.name)).length + (HC.data ? 100 : 0)")
        # The avatar menu opens the same panel, docked.
        p.eval("document.getElementById('me-btn').click(); 0")
        p.eval("document.querySelector('#me-menu [data-me=\"hubchat\"]').click(); 0")
        g["fromMenu"] = p.eval(cls.PANEL)
        p.eval("closeHubChat(); localStorage.removeItem('cd-hub-chat'); 0")
        p.close()

    @classmethod
    def _docked_phone(cls):
        g = cls.got
        g["phoneBar"] = {}
        for w in (360, 390):
            p = cls.browser.page(w, 844, mobile=True)
            cls._open(p)
            # Left open on a computer: a phone does not reopen it on a load.
            p.eval("localStorage.setItem('cd-hub-chat', JSON.stringify({open: true, w: 500})); window.ensBooted = false; 0")
            p.goto(cls.base + "/")
            p.until("window.ensBooted === true", 40)
            p.eval("{ const w = document.getElementById('wide-banner'); if (w) w.hidden = true; } 0")
            g["phoneBar"][w] = p.eval("""(() => { const h = document.querySelector('header'), b = document.getElementById('hc-btn');
              const r = b.getBoundingClientRect(), c = document.querySelector('header [aria-controls="create-menu"]');
              return { closed: document.getElementById('hub-chat').hidden, w: Math.round(r.width), h: Math.round(r.height),
                visible: r.width > 0 && r.right <= innerWidth && r.left >= 0,
                rowTwo: c ? r.top >= c.getBoundingClientRect().bottom - 1 : null,
                fits: h.scrollWidth <= h.clientWidth && document.documentElement.scrollWidth <= innerWidth }; })()""")
            if w == 390:
                cls._shot(p, "8 Phone bar")
                p.eval("document.getElementById('hc-btn').click(); 0")
                p.until("HC.data && !document.getElementById('hub-chat').hidden")
                g["sheet"] = p.eval(cls.PANEL)
                cls._shot(p, "9 Phone sheet")
                p.eval("document.documentElement.dataset.theme = 'dark'; 0")
                cls._shot(p, "10 Phone sheet, dark")
                p.eval("closeHubChat(); 0")
            p.eval("localStorage.removeItem('cd-hub-chat'); 0")
            p.close()

    # ---- the tests

    def test_it_opens_from_the_avatar_menu_with_the_box_focused(self):
        o = self.got["open"]
        self.assertEqual(o["focus"], "hc-in")
        self.assertIn("nothing changes until you click Apply", o["intro"])
        i = o["menu"].index("hubchat")
        self.assertEqual(o["menu"][i:i + 3], ["hubchat", "sources", "settings"])
        self.assertEqual(self.got["fromSources"], {"chat": True, "sources": False})

    def test_a_change_shows_its_lines_and_check_and_waits_for_apply(self):
        c = self.got["change"]
        self.assertEqual(c["said"], "ABC-99 is taken out of Harbour; the hub will not add it back.")
        self.assertEqual(c["add"], ["added: Not: ABC-99"])
        self.assertEqual(c["del"], [])
        self.assertEqual(c["sec"], ["in section: ## Harbour"], "a Not: line says whose it is")
        self.assertIn("What it would change", c["secs"])
        self.assertTrue(any("Harbour" in x and "ABC-99" in x for x in c["changes"]), c["changes"])
        # Send is the box's own; Apply is the one primary among the turns.
        self.assertEqual(c["primary"], ["Apply", "Send"])
        self.assertEqual(c["buttons"], ["Apply", "Discard"])
        self.assertEqual(c["state"], "Nothing is changed until you apply it.")
        self.assertEqual(c["sign"], '"+"')
        self.assertEqual(self.got["beforeApply"], [], "nothing changed before Apply")
        self.assertIn("Take ABC-99 out of Harbour", self.prompts[0])

    def test_apply_applies_it_as_the_hub_chat(self):
        a = self.got["applied"]
        self.assertRegex(a["state"], r"^Applied \d\d:\d\d$")
        self.assertEqual(a["buttons"], [])
        self.assertIn("ABC-99", outside_route.not_keys(self.got["row"]))
        self.assertIn("Not: ABC-99", self.got["file"])
        self.assertEqual(outside_route.history(None, self.harbour)[0]["by"], {"kind": "hubchat"})

    def test_a_reply_and_a_stale_change(self):
        self.assertTrue(self.got["reply"].startswith("Lighthouse gets nothing yet"))
        s = self.got["stale"]
        self.assertIn("changed since", s["state"])
        self.assertEqual(s["buttons"], ["Ask again", "Discard"])
        self.assertEqual(self.got["closed"], {"hidden": True, "focus": "me-btn"})

    def test_clear_asks_before_it_throws_turns_away(self):
        a = self.got["clearArmed"]
        self.assertEqual(a["text"], "Clear all turns?")
        self.assertEqual(a["turns"], a["before"], "one click clears nothing")
        self.assertEqual(self.got["clearAfterClose"], "Clear")

    def test_colours_read_in_every_theme(self):
        for theme, m in self.got["themes"].items():
            for k, v in m.items():
                with self.subTest(theme=theme, what=k):
                    self.assertGreaterEqual(v, 4.5, m)

    def test_phone(self):
        ph = self.got["phone"]
        self.assertEqual(ph["w"], ph["vw"], "the whole screen")
        self.assertEqual(ph["fs"], "16px")
        self.assertLessEqual(ph["sw"], ph["cw"], "nothing scrolls sideways")
        self.assertEqual(ph["small"], [], "every control is finger-sized")
        h = self.got["phoneHist"]
        self.assertLessEqual(h["sw"], h["cw"], "History scrolls not sideways")
        self.assertEqual(h["small"], [], "every History control is finger-sized")

    # ---- #410: the hub's own Settings, and the History

    def test_a_settings_change_shows_each_setting_old_and_new(self):
        c = self.got["setCard"]
        self.assertEqual(c["said"], "The PO checks every 15 minutes, and a task gets at most 3 review rounds.")
        self.assertEqual(c["title"], "Settings · 2 changes")
        self.assertEqual(c["rows"], [
            ["How often the PO checks on progress", "No row in Settings yet · a project's own value still wins",
             "was: every 5 minutes", "becomes: every 15 minutes"],
            ["Review rounds per task", "Settings › Agent models › Reviews", "was: 5 rounds", "becomes: 3 rounds"]])
        self.assertEqual(c["lines"], 4, "no setup-file lines beside them")
        self.assertEqual(c["buttons"], ["Apply", "Discard"])
        self.assertEqual(c["state"], "Nothing is changed until you apply it.")
        self.assertEqual(self.got["setBefore"], 5, "nothing saved before Apply")
        for theme, m in self.got["setThemes"].items():
            for k, v in m.items():
                with self.subTest(theme=theme, what=k):
                    self.assertGreaterEqual(v, 4.5, m)

    def test_apply_saves_the_settings(self):
        a = self.got["setApplied"]
        self.assertRegex(a["state"], r"^Applied \d\d:\d\d$")
        self.assertEqual(a["buttons"], [])
        self.assertEqual(a["rows"], 2, "the rows stay, old and new")
        self.assertNotIn("What it changed", a["secs"], "the rows already say it")
        self.assertEqual(self.got["setSaved"], (15, 3))

    def test_a_stale_settings_change_names_the_setting(self):
        s = self.got["setStale"]
        self.assertEqual(s["state"], "Prefer was changed to Claude since this was made, so it can no longer be applied as it is.")
        self.assertEqual(s["buttons"], ["Ask again", "Discard"])
        self.assertEqual(dashboard.load_settings()["preferAgent"], "claude")

    def test_history_lists_what_was_applied_newest_first(self):
        c, h = self.got["chatView"], self.got["hist"]
        self.assertEqual((c["pressed"], c["turns"], c["compose"], c["clear"], c["hist"], c["back"]),
                         ("false", True, True, True, False, False))
        self.assertEqual((h["pressed"], h["turns"], h["compose"], h["clear"], h["hist"], h["back"]),
                         ("true", False, False, False, True, True), "Clear is hidden while History shows")
        self.assertGreater(h["h"], 300, "it fills the panel")
        self.assertEqual(h["filter"], ["All*", "Settings", "Routing"])
        self.assertEqual([i[0] for i in h["items"]], ["Settings", "Routing"], "newest first")
        self.assertEqual(h["items"][0][1], "Asked: Check every 15 minutes and stop reviews after 3 rounds")
        self.assertEqual(h["items"][0][2], ["How often the PO checks on progresswas every 5 minutes→, now every 15 minutes",
                                            "Review rounds per taskwas 5 rounds→, now 3 rounds"])
        self.assertEqual(h["items"][1][1], "Asked: Take ABC-99 out of Harbour")
        self.assertTrue(h["items"][1][2], "the routing lines")
        self.assertEqual(([i[0] for i in self.got["histSettings"]["items"]], self.got["histSettings"]["filter"]),
                         (["Settings"], ["All", "Settings*", "Routing"]))
        self.assertEqual([i[0] for i in self.got["histRouting"]["items"]], ["Routing"])
        b = self.got["histBack"]
        self.assertEqual((b["pressed"], b["turns"], b["compose"], b["clear"], b["hist"], b["back"], b["focus"]),
                         ("false", True, True, True, False, False, "hc-hist"))
        for theme, m in self.got["histThemes"].items():
            for k, v in m.items():
                with self.subTest(theme=theme, what=k):
                    self.assertGreaterEqual(v, 4.5, m)

    # ---- #21: docked, with its icon

    def test_it_wrote_only_under_its_own_folder(self):
        self.assertTrue(str(hub_chat.path()).startswith(self.tmp.name), hub_chat.path())

    def test_the_icon_is_in_the_bar_in_its_own_colour(self):
        i = self.got["icon"]
        self.assertTrue(i["inBar"])
        self.assertEqual((i["w"], i["h"]), (32, 32))
        self.assertTrue(i["tool"], "--tool-ask is set")
        self.assertTrue(i["barFits"])
        self.assertNotEqual(i["colour"], self.got["iconDark"]["colour"], "each theme has its own shade")
        c = self.got["iconClosed"]
        self.assertTrue(c["hidden"])
        self.assertEqual(c["pressed"], "false")
        self.assertFalse(c["dot"], "nothing waits yet")

    def test_the_icon_opens_it_docked_and_the_page_narrows(self):
        o = self.got["iconOpen"]
        self.assertFalse(o["hidden"])
        self.assertEqual(o["pressed"], "true")
        self.assertEqual(o["right"], o["vw"], "at the right edge")
        self.assertEqual(o["top"], o["barBottom"], "under the bar")
        self.assertEqual(o["w"], 420)
        self.assertLessEqual(o["mainRight"], o["left"], "the page narrows beside it")
        self.assertEqual(self.got["iconFocus"], "hc-in")

    def test_the_dot_says_a_change_waits_also_while_closed(self):
        d, c = self.got["dotOpen"], self.got["dotClosed"]
        self.assertTrue(d["dot"])
        self.assertTrue(c["hidden"])
        self.assertTrue(c["dot"], "the dot stays while closed")
        self.assertIn("waits for you to apply", c["label"])
        self.assertEqual(c["pressed"], "false")
        self.assertEqual(self.got["closeFocus"], "hc-btn")
        self.assertTrue(self.got["escIcon"], "Esc on the icon closes it")

    def test_it_stays_open_on_a_board_beside_the_page(self):
        b = self.got["board"]
        self.assertFalse(b["hidden"])
        self.assertLessEqual(b["mainRight"], b["left"])
        self.assertLessEqual(self.got["boardView"], b["left"])
        self.assertEqual(self.got["over"], [], "nothing covers the panel")
        self.assertEqual(self.got["task"]["right"], b["left"], "a task panel sits beside it")

    def test_its_width_and_open_come_back_after_a_reload(self):
        k, d, r = self.got["keys"], self.got["drag"], self.got["reload"]
        self.assertEqual(k["w"], 420 + 48)
        self.assertEqual(d["w"], 420 + 48 + 100)
        self.assertLessEqual(d["mainRight"], d["left"])
        self.assertFalse(r["hidden"])
        self.assertEqual(r["w"], d["w"])
        self.assertTrue(self.got["reloadClosed"]["hidden"])
        self.assertEqual(self.got["closedReads"], 0, "closed, nothing is read on load")
        m = self.got["fromMenu"]
        self.assertFalse(m["hidden"])
        self.assertEqual((m["pressed"], m["w"], m["right"]), ("true", d["w"], m["vw"]))

    def test_phone_icon_bar_and_sheet(self):
        for w, b in self.got["phoneBar"].items():
            with self.subTest(width=w):
                self.assertTrue(b["closed"], "not reopened on a phone's load")
                self.assertEqual((b["w"], b["h"]), (44, 44))
                self.assertTrue(b["visible"])
                self.assertTrue(b["rowTwo"], b)
                self.assertTrue(b["fits"], "nothing scrolls sideways")
        s = self.got["sheet"]
        self.assertEqual((s["left"], s["w"], s["top"]), (0, s["vw"], 0))


class ReadOnlyWhileOpen(unittest.TestCase):
    """#409: closed, the page reads nothing for Ask the hub (no timer); the
    dot comes from the read on load, on opening and after its actions. On a
    phone the PO drawer is the whole width over an open task too (P439)."""

    def setUp(self):
        self.page = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_no_timer_while_closed(self):
        body = self.page[self.page.index("function hcSchedule() {"):]
        body = body[:body.index("\n}")]
        closed = next(ln for ln in body.splitlines() if "$('#hub-chat').hidden" in ln)
        self.assertNotIn("setTimeout", closed)
        self.assertIn("return", closed)

    def test_the_phone_drawer_is_the_whole_width_over_a_task(self):
        self.assertIn("body.po-peek #po-panel, body.po-peek.detail-open #po-panel,\n"
                      "    body.mid.po-peek.detail-open #po-panel { left: 0; right: 0; width: auto; }",
                      self.page.replace("\r\n", "\n"))


if __name__ == "__main__":
    unittest.main()
