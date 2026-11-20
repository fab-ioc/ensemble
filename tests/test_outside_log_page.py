"""#8 (outside sources step 7) in a real page: the message log.

In headless Chrome (tests/cdp_ws.py: no Node needed), against a hub in a
thread with two initiatives, Motors and Boats, and three made-up messages
(one routed to Motors in #made-up-one, one "no match", one routed to Boats in
#boats-ch, a channel in Boats's row):

* /?outside-log=all opens the Input sources panel on Message log: one row per
  message, newest first, where it went and why; the rule note instead of
  Make a rule where the channel is in a row already;
* the filters reload the log with their query;
* Send elsewhere: the chooser leaves out where it is now, Escape closes the
  chooser only and gives the focus back; Send pressed twice posts once; the
  row then says Boats;
* Send anyway on the "no match" row;
* Make a rule refused by the routing table: the reason under the chooser,
  the button back;
* a reload keeps the same row elements;
* an initiative's settings list its latest messages and open the log on it;
* on a phone nothing is wider than the screen and every control is 44px.

Skipped without Chrome. With ENSEMBLE_SHOTS set to a folder, pictures are left
there.
"""
from __future__ import annotations

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
import outside_route as route  # noqa: E402
import sources  # noqa: E402
from sources.base import Author, Message, Place  # noqa: E402
from sources.fake import FakeSource  # noqa: E402
from tests import cdp_ws  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

SID = "made-up"
# Every fetch the page makes, kept for the checks.
SPY = """(() => { if (window.fetchSeen) return; window.fetchSeen = [];
  const f = window.fetch; window.fetch = (u, o) => { window.fetchSeen.push([String(u), (o && o.method) || 'GET', (o && o.body) || '']); return f(u, o); }; })(); 0"""
ROW = "document.querySelector('#is-lg > li[data-k=\"' + CSS.escape('{sid}\\n{key}') + '\"]')"


def _msg(n: int, at: float, channel: str, name: str, kind: str = "channel") -> Message:
    return Message(key=f"fake:example:{channel}:{n}", source="slack",
                   place=Place(id=channel, name=name, kind=kind),
                   author=Author(id="U000TEST02", name="Made-up Person"), at=float(at),
                   text=f"message {n} about made-up things",
                   link=f"https://example.slack.com/archives/{channel}/p{n}")


@unittest.skipUnless(CHROME, "needs Chrome")
class MessageLogPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-lg-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        for d in ("root", "transcripts", "cs"):
            (base / d).mkdir()
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
            mock.patch.object(route, "_log", lambda *a, **k: None),
            mock.patch.object(outside, "check_now", lambda sid: None),
            mock.patch.dict(sources.TYPES, {"fake": FakeSource}),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        outside.reset()
        cls.addClassCleanup(outside.reset)
        ids = []
        for name in ("Motors", "Boats"):
            ok, proj, why = dashboard.register_project(str(base / "root" / name), name)
            assert ok, why
            ids.append(proj["id"])
        cls.motors, cls.boats = ids
        outside.set_source(SID, type="fake", mode="on", settings={"account": "made-up"})
        route.set_row(SID, cls.boats, {route.CHANNELS: ["boats-ch"]}, {"kind": "ceo"})
        now = time.time()

        def put(n, to, channel, name, **d):
            m = _msg(n, now - 600 + n, channel, name)
            e = {"key": m.key, "message": m.to_dict(), "readAt": now, "decideOnly": False}
            route.record(SID, [(e, dict({"to": to, "how": route.BY_CHANNEL, "why": f"channel #{name}"}, **d))], now=now)
            return m.key
        cls.a = put(1, cls.motors, "C000TEST01", "made-up-one")
        cls.b = put(2, "", "C000TEST02", "made-up-two", dropped=route.NO_MATCH, how=route.BY_MODEL,
                    why="about nothing an initiative does")
        cls.c = put(3, cls.boats, "C000TEST03", "boats-ch")
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
        p.eval(SPY)

    @classmethod
    def row(cls, key):
        return ROW.replace("{sid}", SID).replace("{key}", key)

    @classmethod
    def _desktop(cls):
        g = cls.got
        p = cls.browser.page(1280, 860)
        cls._open(p, "/?outside-log=all")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-lg > li').length === 3")
        a, b, c = cls.row(cls.a), cls.row(cls.b), cls.row(cls.c)
        g["first"] = p.eval(f"""(() => {{ const t = e => e.innerText.replace(/\\s+/g, ' ').trim();
          return {{ cur: document.querySelector('#is-nav [aria-current="true"]').dataset.src, url: location.search,
            count: document.getElementById('is-lg-n').textContent,
            order: [...document.querySelectorAll('#is-lg > li')].map(li => li.dataset.k.split('\\n')[1]),
            a: t({a}), b: t({b}), c: t({c}),
            aActs: [...{a}.querySelectorAll('[data-lg-act]')].map(x => x.dataset.lgAct),
            bActs: [...{b}.querySelectorAll('[data-lg-act]')].map(x => x.dataset.lgAct),
            cActs: [...{c}.querySelectorAll('[data-lg-act]')].map(x => x.dataset.lgAct),
            link: {a}.querySelector('a.is-lg-open').getAttribute('href'),
            pjOpts: [...document.querySelectorAll('#is-lg-pj option')].map(o => o.textContent) }}; }})()""")
        cls._shot(p, "1 Message log")
        # A filter reloads with its query.
        p.eval(f"{{ const s = document.getElementById('is-lg-pj'); s.value = '{cls.motors}'; s.dispatchEvent(new Event('change', {{bubbles: true}})); }} 0")
        p.until("document.querySelectorAll('#is-lg > li').length === 1")
        g["filter"] = p.eval("({ url: fetchSeen.filter(f => f[0].startsWith('/api/outside/messages?')).pop()[0],"
                             " keys: [...document.querySelectorAll('#is-lg > li')].map(li => li.dataset.k.split('\\n')[1]) })")
        p.eval("{ const s = document.getElementById('is-lg-pj'); s.value = ''; s.dispatchEvent(new Event('change', {bubbles: true})); } 0")
        p.eval("{ const s = document.getElementById('is-lg-nm'); s.checked = true; s.dispatchEvent(new Event('change', {bubbles: true})); } 0")
        p.until("document.querySelectorAll('#is-lg > li').length === 1 && /nomatch=1/.test(fetchSeen[fetchSeen.length - 1][0])")
        g["nomatch"] = p.eval("[...document.querySelectorAll('#is-lg > li')].map(li => li.dataset.k.split('\\n')[1])")
        p.eval("{ const s = document.getElementById('is-lg-nm'); s.checked = false; s.dispatchEvent(new Event('change', {bubbles: true})); } 0")
        p.until("document.querySelectorAll('#is-lg > li').length === 3")
        # A reload keeps the same row elements.
        p.eval("window.keepRows = [...document.querySelectorAll('#is-lg > li')]; 0")
        p.eval("isLogLoad().then(() => { window.reloaded = true; }); 0")
        p.until("window.reloaded === true")
        g["kept"] = p.eval("[...document.querySelectorAll('#is-lg > li')].every((li, i) => li === keepRows[i])")
        # Send elsewhere: the chooser, Escape, then Send pressed twice.
        p.eval(f"{a}.querySelector('[data-lg-act=\"move\"]').click(); 0")
        p.until(f"{a}.querySelector('.is-lg-ch')")
        g["chooser"] = p.eval(f"""({{ opts: [...{a}.querySelectorAll('#is-lg-to option')].map(o => o.textContent),
          focus: document.activeElement.id, label: {a}.querySelector('.is-lg-ch label').textContent,
          hint: {a}.querySelector('.is-lg-ch .is-hint').textContent }})""")
        cls._shot(p, "2 Send elsewhere")
        p.eval("document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); 0")
        g["escape"] = p.eval(f"({{ chooser: !!{a}.querySelector('.is-lg-ch'), panel: !document.getElementById('sources-panel').hidden,"
                             " focus: document.activeElement.dataset.lgAct || '' })")
        p.eval(f"{a}.querySelector('[data-lg-act=\"move\"]').click(); 0")
        p.until(f"{a}.querySelector('.is-lg-ch')")
        g["sendTwice"] = p.eval(f"""(() => {{ const go = {a}.querySelector('[data-lg-go]'); go.click();
          const during = [go.disabled, go.textContent]; go.click(); return during; }})()""")
        p.until(f"!{a}.querySelector('.is-lg-ch') && /To Boats/.test({a}.innerText)")
        g["moved"] = p.eval(f"""({{ posts: fetchSeen.filter(f => f[1] === 'POST' && f[0] === '/api/outside/messages').map(f => JSON.parse(f[2])),
          text: {a}.innerText.replace(/\\s+/g, ' ').trim(), toast: (document.querySelector('#status .msg') || {{}}).textContent || '',
          focus: document.activeElement.dataset.lgAct || '' }})""")
        g["queue"] = sorted((i["key"], i["to"]) for i in outside.queue_items())
        cls._shot(p, "3 Sent elsewhere")
        # Send anyway on the no match row.
        p.eval(f"{b}.querySelector('[data-lg-act=\"send\"]').click(); 0")
        p.until(f"{b}.querySelector('.is-lg-ch')")
        p.eval(f"{{ {b}.querySelector('#is-lg-to').value = '{cls.motors}'; {b}.querySelector('[data-lg-go]').click(); }} 0")
        p.until(f"!{b}.querySelector('.is-lg-ch') && /To Motors/.test({b}.innerText)")
        g["anyway"] = p.eval(f"({{ acts: [...{b}.querySelectorAll('[data-lg-act]')].map(x => x.dataset.lgAct), text: {b}.innerText.replace(/\\s+/g, ' ').trim() }})")
        # Make a rule, refused: meanwhile #made-up-one went into Boats's row.
        p.eval(f"{a}.querySelector('[data-lg-act=\"rule\"]').click(); 0")
        p.until(f"{a}.querySelector('.is-lg-ch')")
        route.set_row(SID, cls.boats, {route.CHANNELS: ["boats-ch", "made-up-one"]}, {"kind": "ceo"})
        p.eval(f"{{ {a}.querySelector('#is-lg-to').value = '{cls.motors}'; {a}.querySelector('[data-lg-go]').click(); }} 0")
        p.until(f"{a}.querySelector('.is-lg-msg.err') && !{a}.querySelector('.is-lg-msg.err').hidden")
        g["refused"] = p.eval(f"""({{ msg: {a}.querySelector('.is-lg-msg.err').textContent, role: {a}.querySelector('.is-lg-msg.err').getAttribute('role'),
          go: [{a}.querySelector('[data-lg-go]').disabled, {a}.querySelector('[data-lg-go]').textContent] }})""")
        cls._shot(p, "4 A refused rule")
        # A reload while the chooser is open leaves it open.
        p.eval("window.reloaded = false; isLogLoad().then(() => { window.reloaded = true; }); 0")
        p.until("window.reloaded === true")
        g["busyKept"] = p.eval(f"!!{a}.querySelector('.is-lg-ch .is-lg-msg.err')")
        p.eval(f"{a}.querySelector('[data-lg-cancel]').click(); 0")
        g["afterCancel"] = p.eval(f"({{ ch: !!{a}.querySelector('.is-lg-ch'), acts: [...{a}.querySelectorAll('[data-lg-act]')].map(x => x.dataset.lgAct),"
                                  f" hint: ({a}.querySelector('.is-lg-a .is-hint') || {{}}).textContent || '' }})")
        # An initiative's settings: its latest messages, and the log on it.
        p.eval("closeSources(); 0")
        p.eval(f"projSettings('{cls.boats}'); 0")
        p.until("document.querySelector('.ps-out li')")
        g["settings"] = p.eval("""({ items: [...document.querySelectorAll('.ps-out li')].map(li => li.querySelector('.ps-ot').textContent),
          ell: getComputedStyle(document.querySelector('.ps-out .ps-ot')).textOverflow })""")
        p.eval("document.querySelector('.ps-out').scrollIntoView({block: 'center'}); 0")
        cls._shot(p, "5 Initiative settings")
        p.eval("document.querySelector('[data-ps-act=\"outside-log\"]').click(); 0")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-lg > li').length >= 1"
                " && document.getElementById('is-lg-pj').value !== ''")
        g["fromSettings"] = p.eval("({ pj: document.getElementById('is-lg-pj').value,"
                                   " keys: [...document.querySelectorAll('#is-lg > li')].map(li => li.dataset.k.split('\\n')[1]) })")
        p.close()

    @classmethod
    def _phone(cls):
        g = cls.got
        p = cls.browser.page(390, 844, mobile=True)
        cls._open(p, "/?outside-log=all")
        p.eval("{ const w = document.getElementById('wide-banner'); if (w) w.hidden = true; } 0")
        p.until("!document.getElementById('sources-panel').hidden && document.querySelectorAll('#is-lg > li').length === 3")
        p.eval(f"{cls.row(cls.c)}.querySelector('[data-lg-act]').click(); 0")
        p.until(f"{cls.row(cls.c)}.querySelector('.is-lg-ch')")
        g["phone"] = p.eval("""(() => { const pg = document.getElementById('is-page');
          const els = [...pg.querySelectorAll('button, select, input, a')].filter(e => e.getBoundingClientRect().width);
          return { title: document.getElementById('is-title').textContent, vw: innerWidth,
            wide: Math.max(...[...pg.querySelectorAll('*')].map(e => e.getBoundingClientRect().right)), sw: pg.scrollWidth, cw: pg.clientWidth,
            small: els.filter(e => e.type !== 'checkbox' && e.getBoundingClientRect().height < 43.5).map(e => e.outerHTML.slice(0, 90)) }; })()""")
        cls._shot(p, "6 Phone message log")
        p.close()

    # ---- the tests

    def test_the_log_lists_each_message(self):
        f = self.got["first"]
        self.assertEqual((f["cur"], f["url"]), (":log", ""))
        self.assertEqual(f["count"], "3 messages.")
        self.assertEqual(f["order"], [self.c, self.b, self.a], "newest first")
        self.assertIn("Made-up Person · #made-up-one", f["a"])
        self.assertIn("message 1 about made-up things", f["a"])
        self.assertIn("To Motors Channel #made-up-one", f["a"])
        self.assertIn("No match Model: about nothing an initiative does", f["b"].replace("NO MATCH", "No match"))
        self.assertEqual(f["aActs"], ["move", "rule"])
        self.assertEqual(f["bActs"], ["send", "rule"])
        self.assertEqual(f["cActs"], ["move"])
        self.assertIn("#boats-ch is in Boats's row.", f["c"])
        self.assertEqual(f["link"], "https://example.slack.com/archives/C000TEST01/p1")
        self.assertEqual(f["pjOpts"][0], "All initiatives")
        self.assertEqual(sorted(f["pjOpts"][1:]), ["Boats", "Motors"])

    def test_the_filters_reload_with_their_query(self):
        self.assertIn(f"project={self.motors}", self.got["filter"]["url"])
        self.assertEqual(self.got["filter"]["keys"], [self.a])
        self.assertEqual(self.got["nomatch"], [self.b])

    def test_a_reload_keeps_the_rows(self):
        self.assertTrue(self.got["kept"])
        self.assertTrue(self.got["busyKept"], "an open chooser is left alone")

    def test_send_elsewhere_once(self):
        g = self.got
        self.assertEqual(g["chooser"]["opts"], ["Boats"], "not where it is now")
        self.assertEqual(g["chooser"]["focus"], "is-lg-to")
        self.assertEqual(g["chooser"]["label"], "Send it instead to")
        self.assertEqual(g["escape"], {"chooser": False, "panel": True, "focus": "move"})
        self.assertEqual(g["sendTwice"], [True, "Sending…"])
        moves = [x for x in g["moved"]["posts"] if x["action"] == "move"]
        self.assertEqual(moves, [{"action": "move", "source": SID, "key": self.a, "to": self.boats}])
        self.assertIn("To Boats", g["moved"]["text"])
        self.assertIn("To Boats Sent here by you", g["moved"]["text"], "why it is there now")
        self.assertIn("Moved from Motors to Boats by you (Motors's PO had not been given it)", g["moved"]["text"])
        self.assertTrue(g["moved"]["toast"].startswith("Sent to Boats."), g["moved"]["toast"])
        self.assertEqual(g["moved"]["focus"], "move")
        self.assertIn((self.a, self.boats), g["queue"])
        self.assertNotIn((self.a, self.motors), g["queue"], "taken back before Motors's PO was given it")

    def test_send_anyway(self):
        self.assertEqual(self.got["anyway"]["acts"], ["move", "rule"])
        self.assertIn("To Motors", self.got["anyway"]["text"])

    def test_a_refused_rule_says_why(self):
        r = self.got["refused"]
        self.assertTrue(r["msg"].startswith("Not added: "), r)
        self.assertIn("Boats", r["msg"])
        self.assertEqual(r["role"], "alert")
        self.assertEqual(r["go"], [False, "Add"])
        c = self.got["afterCancel"]
        self.assertFalse(c["ch"])
        self.assertEqual(c["acts"], ["move"], "the channel is in Boats's row now")

    def test_initiative_settings(self):
        s = self.got["settings"]
        self.assertEqual(sorted(s["items"]), ["message 1 about made-up things", "message 3 about made-up things"],
                         "what it has now, the moved one too")
        self.assertEqual(s["ell"], "ellipsis")
        self.assertEqual(self.got["fromSettings"]["pj"], self.boats)

    def test_phone(self):
        ph = self.got["phone"]
        self.assertEqual(ph["title"], "Message log")
        self.assertLessEqual(ph["wide"], ph["vw"])
        self.assertLessEqual(ph["sw"], ph["cw"], "nothing scrolls sideways")
        self.assertEqual(ph["small"], [], "every control is finger-sized")


if __name__ == "__main__":
    unittest.main()
