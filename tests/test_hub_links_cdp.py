"""CDP coverage for hub addresses in a chat's balloons (P358).

An agent's message holds the hub's addresses as it knows them: a card's link,
a file view, another task's chat. The chat draws each as the thing's name, a
link: the card as its question, the picture as its file name, the task as its
number. Another website's address stays as written, and no balloon shows an
Ensemble address. A click on the card's chip lands on the card. Skipped
without Node and Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.chrome_profile import CHROME  # noqa: E402

NODE = shutil.which("node")
SIZES = [(1280, 800, False), (390, 844, True)]
QUESTION = "Project icons: A or B?"
GITHUB = "https://github.com/acme/x/issues/1"
# A 1x1 PNG, so the picture's thumbnail has something to show.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002"
                    "000100e221bc330000000049454e44ae426082")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function launch() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const ws = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  return { ch, ws };
}
class Cdp {
  constructor(url) { this.url = url; this.id = 0; this.waits = new Map(); }
  open() { return new Promise((res, rej) => { this.ws = new WebSocket(this.url); this.ws.onopen = () => res(); this.ws.onerror = e => rej(e);
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && this.waits.has(m.id)) { const w = this.waits.get(m.id); this.waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } }; }); }
  send(method, params = {}, sessionId) { const id = ++this.id; return new Promise((res, rej) => { this.waits.set(id, { res, rej }); this.ws.send(JSON.stringify({ id, method, params, sessionId })); }); }
}
// Each link balloon as the person sees it: its words and its anchors.
const VIEW = `(() => {
  const out = {};
  for (const [key, mid] of Object.entries(${JSON.stringify(A.mids)})) {
    const el = [...document.querySelectorAll('#msgs .msg[data-mid]')].find(e => e.dataset.mid === mid);
    const body = el && (el.querySelector('.text') || el);
    out[key] = !el ? null : { text: body.innerText, whole: el.innerText,
      links: [...body.querySelectorAll('a')].map(a => ({ cls: a.className, text: a.innerText.trim(), href: a.getAttribute('href') || '',
        task: a.dataset.task || '', room: a.dataset.refRoom || '', msg: a.dataset.refMsg || '', part: a.dataset.refPart || '' })) };
  }
  out.balloons = [...document.querySelectorAll('#msgs .msg')].map(e => e.innerText);
  return out;
})()`;
// Where the click on the card's chip went: the card (marked), a note, a second window.
const SEEN = `(() => {
  const box = document.getElementById('msgs'), list = box.getBoundingClientRect(), mid = ${JSON.stringify(A.cardMid)};
  const el = [...box.querySelectorAll('.msg[data-mid]')].find(e => e.dataset.mid === mid);
  const card = el ? [...el.querySelectorAll('.qa[data-ask]')].find(e => e.dataset.ask === mid + ':0') : null;
  const cr = card && card.getBoundingClientRect(), note = document.getElementById('goto-note');
  return { card: !!card, cardLanded: !!card && card.classList.contains('landed'), cardVisible: !!cr && cr.top < list.bottom && cr.bottom > list.top,
    note: note && !note.hidden ? note.textContent.trim() : '', opened: sessionStorage.getItem('__secondWindow') || '',
    href: location.pathname + location.search };
})()`;
const READY = `(() => typeof CHAT_DRAWN !== 'undefined' && CHAT_DRAWN
  && [...document.querySelectorAll('#msgs a.ref-chip.card-jump')].some(a => a.innerText.includes(${JSON.stringify(A.question)}))
  && !!document.querySelector('#msgs a.task-chip[data-task="${A.other}"]'))()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [w, h, mob] of A.sizes) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      await c.send('Page.addScriptToEvaluateOnNewDocument', {
        source: 'window.open = url => { sessionStorage.setItem("__secondWindow", String(url)); return null; };' }, sessionId);
      const evalIn = async expr => { const r = await c.send('Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
        if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
      let ready = false;
      const t = Date.now();
      while (Date.now() - t < 30000) {
        try { ready = await evalIn(READY); } catch (e) { ready = false; }
        if (ready) break;
        await sleep(150);
      }
      await sleep(600);    // the redraw after the hub named everything
      const o = { ready, view: await evalIn(VIEW) };
      // A link to the card, clicked (tapped on the phone): the page lands on the card.
      const clickTo = async sel => {
        const at = await evalIn(`(() => { const a = document.querySelector('${sel}'); if (!a) return null; a.scrollIntoView({ block: 'center' });
          document.querySelectorAll('.landed').forEach(e => e.classList.remove('landed')); return true; })()`);
        if (!at) return { hit: false, click: null };
        await sleep(200);
        // The link at the foot of the list, the card's own balloon above it scrolled away if it can be.
        const p = await evalIn(`(() => { const a = document.querySelector('${sel}'); a.scrollIntoView({ block: 'end' });
          const b = a.getBoundingClientRect(), x = Math.round(b.left + Math.min(b.width / 2, 40)), y = Math.round(b.top + b.height / 2);
          const hit = document.elementFromPoint(x, y); return { x, y, hit: !!hit && !!hit.closest('${sel}') }; })()`);
        await sleep(200);
        if (mob) {
          await c.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: p.x, y: p.y }] }, sessionId);
          await c.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }, sessionId);
        } else {
          await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p.x, y: p.y }, sessionId);
          for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: 'left', clickCount: 1 }, sessionId);
        }
        let v = null;
        const t2 = Date.now();
        while (Date.now() - t2 < 10000) {
          try { v = await evalIn(SEEN); } catch (e) { v = null; }
          if (v && (v.cardLanded || v.note || v.opened)) break;
          await sleep(100);
        }
        return { hit: p.hit, click: v };
      };
      const chip = await clickTo('#msgs .msg[data-mid="' + A.mids.card + '"] a.ref-chip.card-jump');
      o.hit = chip.hit; o.click = chip.click;
      // The card's name in a fenced code block (review 2) is a link to it too.
      o.fenceClick = await clickTo('#msgs .msg[data-mid="' + A.mids.fence + '"] pre a');
      out[w] = o;
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class HubLinksInBalloons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-names-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)   # undone even when setUpClass fails
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        team = [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer", "cwd": str(home)},
                {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer", "cwd": str(home)}]
        cls.room = cls.task("Project icons", proj["id"], team, 5)
        cls.other = cls.task("Billing export", proj["id"], team, 7)
        cls.picture = home / "Documents" / "links.png"
        cls.picture.parent.mkdir(parents=True, exist_ok=True)
        cls.picture.write_bytes(PNG)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        asks_ = [{"n": 0, "question": QUESTION, "kind": "decision",
                  "options": [{"label": "A", "detail": "", "recommended": True}, {"label": "B", "detail": "", "recommended": False}]}]

        def post(text, ask=None):
            return chatroom.post_message(cls.room, "claude", text, to="user", structured_asks=ask,
                                         ask_audience="user" if ask else "")["message"]["id"]
        cls.card_mid = post("Two icons to choose from.", asks_)
        # The links as an agent writes them: bare, with the hub's loopback addresses.
        cls.card_url = f"http://127.0.0.1:{port}/session?room={cls.room}&msg={cls.card_mid}&part=ask:0"
        cls.file_url = f"http://127.0.0.1:{port}/fileview?path={quote(str(cls.picture), safe='')}"
        cls.task_url = f"http://localhost:{port}/session?room={cls.other}"
        cls.away_card = f"https://example.com/session?room={cls.room}&msg={cls.card_mid}&part=ask:0"
        cls.away_file = f"http://localhost:{port + 1 if port < 65535 else port - 1}/fileview?path={quote(str(cls.picture), safe='')}"
        cls.mids = {
            "card": post(f"The card is still open: {cls.card_url}"),
            "file": post(f"The two icons side by side: {cls.file_url}"),
            "task": post(f"The billing side is in {cls.task_url}."),
            "web": post(f"Upstream bug: {GITHUB}"),
            # In a fenced code block too (review 1): the code's text, its hub addresses as names.
            "fence": post(f"The links:\n\n```\ncard {cls.card_url}\nfile {cls.file_url}\nweb {GITHUB}\n```"),
            # The hub's paths on another website or a dev server's port are that site's (review 1).
            "away": post(f"Elsewhere: {cls.away_card} and {cls.away_file}"),
        }
        for _ in range(3):      # below the links, so a landing has to scroll back up
            post("A later note. " + "words " * 80)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{port}", "room": cls.room,
                "other": cls.other, "mids": cls.mids, "cardMid": cls.card_mid, "question": QUESTION, "sizes": SIZES}
        script = base / "hub_links_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def task(cls, title: str, pid: str, team: list[dict], no: int) -> str:
        rid = chatroom.create_room(title, [dict(m) for m in team])["id"]
        full = chatroom.get_room(rid, public=False)
        full["projectId"] = pid
        chatroom.update_room(full)
        dashboard.assign_session_project(rid, pid)
        chatroom.set_task_number(rid, pid, no)
        return rid

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _each(self):
        for w, _h, _m in SIZES:
            o = self.got[str(w)]
            with self.subTest(width=w):
                self.assertTrue(o["ready"], o["view"])
            yield str(w), o

    def test_a_card_link_is_the_cards_chip_named_by_its_question(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o["view"]["card"]
                chips = [a for a in v["links"] if "ref-chip" in a["cls"].split() and "card-jump" in a["cls"].split()]
                self.assertEqual(len(chips), 1, v)
                a = chips[0]
                self.assertIn(QUESTION, a["text"])
                self.assertEqual((a["room"], a["msg"], a["part"]), (self.room, self.card_mid, "ask:0"))
                q = parse_qs(urlsplit(a["href"]).query)
                self.assertEqual(urlsplit(a["href"]).path, "/session")
                self.assertEqual((q["room"], q["msg"], q["part"]), ([self.room], [self.card_mid], ["ask:0"]))

    def test_a_file_view_link_is_the_files_name(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o["view"]["file"]
                named = [a for a in v["links"] if a["text"] == "links.png"]
                self.assertEqual(len(named), 1, v)
                href = urlsplit(named[0]["href"])
                self.assertEqual(href.path, "/fileview")
                self.assertEqual(parse_qs(href.query)["path"], [str(self.picture)])

    def test_a_task_link_is_the_tasks_chip(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o["view"]["task"]
                chips = [a for a in v["links"] if "task-chip" in a["cls"].split()]
                self.assertEqual(len(chips), 1, v)
                self.assertIn("#7", chips[0]["text"])
                self.assertEqual(chips[0]["task"], self.other)
                self.assertIn(self.other, chips[0]["href"])

    def test_another_websites_address_stays_as_written(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o["view"]["web"]
                self.assertIn(GITHUB, [a["text"] for a in v["links"]], v)
                self.assertEqual(next(a for a in v["links"] if a["text"] == GITHUB)["href"], GITHUB)

    def test_no_balloon_shows_an_ensemble_address(self):
        for w, o in self._each():
            with self.subTest(width=w):
                self.assertTrue(o["view"]["balloons"])
                for text in o["view"]["balloons"]:
                    if text.find("Elsewhere:") >= 0:
                        continue    # other sites' addresses, as written
                    for bare in ("127.0.0.1", "localhost:", "session?room=", "fileview?"):
                        self.assertNotIn(bare, text)

    def test_a_fenced_code_block_shows_names_not_addresses(self):
        for w, o in self._each():
            with self.subTest(width=w):
                text = o["view"]["fence"]["text"]
                self.assertIn(f"card {QUESTION}", text)
                self.assertIn("file links.png", text)
                self.assertIn(f"web {GITHUB}", text)
                for bare in ("127.0.0.1", "localhost:", "session?room=", "fileview?"):
                    self.assertNotIn(bare, text)
                # Each name is a link to what it names (review 2); the other site's address is text.
                links = o["view"]["fence"]["links"]
                card = [a for a in links if QUESTION in a["text"]]
                self.assertEqual(len(card), 1, links)
                q = parse_qs(urlsplit(card[0]["href"]).query)
                self.assertEqual((urlsplit(card[0]["href"]).path, q["room"], q["msg"], q["part"]),
                                 ("/session", [self.room], [self.card_mid], ["ask:0"]))
                f = [a for a in links if a["text"] == "links.png"]
                self.assertEqual(len(f), 1, links)
                self.assertEqual(parse_qs(urlsplit(f[0]["href"]).query)["path"], [str(self.picture)])
                self.assertNotIn(GITHUB, [a["text"] for a in links])

    def test_a_click_on_the_cards_name_in_code_lands_on_the_card(self):
        for w, o in self._each():
            with self.subTest(width=w):
                r = o["fenceClick"]
                self.assertTrue(r["hit"], "the click falls on the link")
                v = r["click"]
                self.assertIsNotNone(v)
                self.assertEqual(v["opened"], "", "no window of its own")
                self.assertTrue(v["card"] and v["cardLanded"] and v["cardVisible"], v)

    def test_the_hubs_paths_on_another_site_stay_that_sites_links(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o["view"]["away"]
                self.assertFalse([a for a in v["links"] if "ref-chip" in a["cls"].split() or a["room"]], v)
                self.assertIn(self.away_card, v["text"])
                self.assertIn(self.away_file, v["text"])
                self.assertNotIn("links.png", [a["text"] for a in v["links"]], "not named as the hub's file")

    def test_a_click_on_the_card_chip_lands_on_the_card(self):
        for w, o in self._each():
            with self.subTest(width=w):
                v = o.get("click")
                self.assertIsNotNone(v, "the chip was drawn")
                self.assertTrue(o["hit"], "the click falls on the chip")
                self.assertEqual(v["opened"], "", "no window of its own")
                self.assertEqual(v["note"], "")
                self.assertTrue(v["card"] and v["cardLanded"], v)
                self.assertTrue(v["cardVisible"], v)


if __name__ == "__main__":
    unittest.main()
