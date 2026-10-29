"""#289: every card on the board says what its task is really doing, in
headless Chrome over CDP against a throwaway hub in a thread (its own state in
a temp dir, fake terminals), at 1728x1117 and on a phone (390x844):

* one task in each state, all in In progress: Working (an agent in a turn),
  Idle (a terminal open, no turn), Waiting on you (a question, no PO),
  Waiting on the PO (a question its PO has not answered), Stopped (no agent);
* the word sits on the card, whole, with its dot, and reads at 4.5:1 or
  better on the card in every theme;
* the In progress header counts what is real: "5 · 1 working · 2 waiting";
* Running only leaves the Working and Idle tasks, and gives the rest back.
* A block shows on a card even while its agent works.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent_hooks  # noqa: E402
import asks  # noqa: E402
import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1728,1117', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const i = ++id; waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  try {
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const R = JSON.stringify(A.rooms), P = JSON.stringify(A.project);
    // The board of the project, every task in it drawn, the attention poll in.
    const show = `(() => { if (typeof renderRows !== 'function' || !PROJECTS || !ALL_ROWS || !ALL_ROWS.length) return false;
      SELECTED_PROJECT = ${P}; PROJECT_TAB = 'tasks'; if (VIEW_MODE !== 'board') setViewMode('board'); else { BOARD_SIG = ''; renderRows(); }
      return Object.values(${R}).every(rid => document.querySelector('.card[data-room="' + rid + '"] .tstate'))
        && !!document.querySelector('.card[data-room="' + ${R}.asks + '"] .tstate.ts-waiting'); })()`;
    // Each card's state chip: its word, its dot, whether it is whole on the card.
    const read = `(() => { const out = { cards: {} };
      for (const [k, rid] of Object.entries(${R})) {
        const card = document.querySelector('.card[data-room="' + rid + '"]');
        const chip = card && card.querySelector('.tstate');
        if (!chip) { out.cards[k] = null; continue; }
        const dot = chip.querySelector('.dot'), cr = card.getBoundingClientRect(), r = chip.getBoundingClientRect();
        out.cards[k] = { word: chip.textContent.trim(), dot: [...dot.classList].filter(c => c !== 'dot').join(' '),
          label: dot.getAttribute('aria-label'), whole: r.left >= cr.left - 0.5 && r.right <= cr.right + 0.5 && r.width > 0
            && chip.scrollWidth <= chip.clientWidth + 1, dotW: dot.getBoundingClientRect().width };
      }
      const head = document.querySelector('.col[data-col="inprogress"] .col-head');
      out.head = head ? [...head.children].map(e => e.textContent.trim()).filter(Boolean).join(' | ') : null;
      out.width = innerWidth; out.scroll = document.documentElement.scrollWidth;
      return out; })()`;
    const wait = async () => { const t = Date.now(); while (Date.now() - t < 30000) { try { if (await evalIn(show)) return true; } catch (e) {} await sleep(250); } return false; };
    await send('Emulation.setDeviceMetricsOverride', { width: 1728, height: 1117, deviceScaleFactor: 1, mobile: false }, sessionId);
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    const got = {};
    got.shown = await wait();
    got.desk = await evalIn(read);
    // Contrast of each word on its card, in every theme.
    got.contrast = await evalIn(`(async () => {
      const lum = c => { const m = c.match(/[\\d.]+/g).map(Number); const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2]); };
      const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
      const out = {};
      for (const t of Object.keys(cdTheme.themes || {}).filter(t => t !== 'system')) {
        document.documentElement.setAttribute('data-theme', t);
        await new Promise(r => setTimeout(r, 30));
        let worst = 99;
        for (const chip of document.querySelectorAll('.card .tstate')) {
          const bg = getComputedStyle(chip.closest('.card')).backgroundColor;
          worst = Math.min(worst, ratio(getComputedStyle(chip).color, bg));
        }
        out[t] = Math.round(worst * 100) / 100;
      }
      document.documentElement.removeAttribute('data-theme');
      return out; })()`);
    // Running only: the Working and Idle tasks stay.
    got.runOnly = await evalIn(`(async () => {
      const ids = () => Object.entries(${R}).filter(([k, rid]) => document.querySelector('.card[data-room="' + rid + '"]')).map(([k]) => k).sort();
      const chip = () => document.querySelector('.colchip[data-runonly]');
      const out = { chip: !!chip(), before: ids() };
      chip().click(); await new Promise(r => setTimeout(r, 300));
      out.on = ids(); out.pressed = chip().getAttribute('aria-pressed');
      // An archived task stays out while it is on, even with Archived chosen.
      const idle = ALL_ROWS.find(r => r.roomId === ${R}.idle), had = FILTERS.has('archived');
      FILTERS.add('archived'); out.archived = passesFilter(Object.assign({}, idle, { archived: true }));
      if (!had) FILTERS.delete('archived');
      const busy = ALL_ROWS.find(r => r.roomId === ${R}.busy);
      out.busyBlocked = taskState(Object.assign({}, busy, { roomId: null, attention: { state: 'blocked', reason: 'no key' } })).word;
      chip().click(); await new Promise(r => setTimeout(r, 300));
      out.off = ids(); out.after = chip().getAttribute('aria-pressed');
      return out; })()`);
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await sleep(400);
    got.phoneShown = await wait();
    got.phone = await evalIn(read);
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _live(rid, pty, screen="● ok\n❯ \n"):
    return types.SimpleNamespace(
        id=pty, alive=lambda: True, tail=lambda: screen, last_output=time.time(),
        info=lambda: {"idleSeconds": 30}, last_submit=lambda: 0.0, death=lambda: None, last_input=0.0,
        meta={"room": rid, "identity": "claude"})


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheBoardSaysWhatRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-st-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        live = {}
        po_for: dict[str, str] = {}
        for p in [
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
            mock.patch.object(dashboard, "_read_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.ptyrun, "get", lambda pid: live.get(pid)),
            mock.patch.object(dashboard.ptyrun, "death_for", lambda pid: None),
            mock.patch.object(dashboard.ptyrun, "list_sessions",
                              lambda: [{"id": k, "alive": True, "meta": s.meta} for k, s in live.items()]),
            # One task asks a PO; the project keeps no PO, so its board is the plain one.
            mock.patch.object(dashboard, "room_po_id",
                              lambda room, *a, **k: po_for.get((room or {}).get("id"), "")),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            p.start()
            cls.addClassCleanup(p.stop)
        for cache in (attention._SUMMARY_CACHE, attention._ANALYSIS, attention._FIRST_SEEN):
            cache.clear()
            cls.addClassCleanup(cache.clear)
        cls.addClassCleanup(setattr, attention, "_result", (0.0, None))
        attention._result = (0.0, None)
        asks.forget()
        cls.addClassCleanup(asks.forget)
        ok, proj, _ = dashboard.register_project("Garage")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))

        def task(title, pty=""):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": home}])["id"]
            chatroom.post_message(rid, "user", "Please look at it")
            full = chatroom.get_room(rid, public=False)
            part = chatroom.participant(full, "claude")
            part.update(sessionId="s-" + rid, ptyId=pty)
            full.update(mode="solo", launched=True, workflow="inprogress")
            chatroom.update_room(full)
            dashboard.assign_session_project(rid, proj["id"])
            return rid

        rooms = {
            "busy": task("Works now", "pty-busy"),
            "idle": task("Open, doing nothing", "pty-idle"),
            "asks": task("Asks you", "pty-asks"),
            "po": task("Asks its PO"),
            "off": task("Stopped hours ago"),
        }
        live["pty-busy"] = _live(rooms["busy"], "pty-busy", "● Checking\n\n✻ Working… (12s · esc to interrupt)\n")
        live["pty-idle"] = _live(rooms["idle"], "pty-idle")
        live["pty-asks"] = _live(rooms["asks"], "pty-asks")
        agent_hooks.reset()
        cls.addClassCleanup(agent_hooks.reset)
        agent_hooks.record({"room": rooms["busy"], "identity": "claude", "ptyId": "pty-busy",
                            "event": {"hook_event_name": "UserPromptSubmit", "session_id": "s-busy"}},
                           lambda pid: (rooms["busy"], "claude"))
        chatroom.record_report(rooms["asks"], "claude", "question", "Which price?")
        po_rid = chatroom.create_room("Planning", [{"identity": "po", "agent": "claude",
                                                    "role": "ProductOwner"}])["id"]
        po_for[rooms["po"]] = po_rid
        chatroom.record_report(rooms["po"], "claude", "question", "Which supplier?",
                               {"roomId": po_rid, "identity": "po", "project": "Garage"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "state_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "project": proj["id"],
                "base": f"http://127.0.0.1:{server.server_address[1]}", "rooms": rooms}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=200)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    WANT = {"busy": ("Working", "working"), "idle": ("Idle", "idle"), "asks": ("Waiting on you", "warning"),
            "po": ("Waiting on the PO", "neutral"), "off": ("Stopped", "off")}

    def check_cards(self, view):
        got = self.got[view]
        for k, (word, dot) in self.WANT.items():
            c = got["cards"][k]
            self.assertTrue(c, f"{view} {k}: a state chip ({got})")
            self.assertEqual((c["word"], c["dot"]), (word, dot), f"{view} {k}: {c}")
            self.assertTrue(c["whole"], f"{view} {k}: the word is whole on the card: {c}")
            self.assertGreaterEqual(c["dotW"], 8, c)
            self.assertTrue(c["label"], c)

    def test_each_card_says_its_state_on_a_computer(self):
        self.assertTrue(self.got["shown"], self.got)
        self.assertEqual(self.got["desk"]["width"], 1728)
        self.check_cards("desk")

    def test_each_card_says_its_state_on_a_phone(self):
        self.assertTrue(self.got["phoneShown"], self.got)
        self.assertEqual(self.got["phone"]["width"], 390)
        self.check_cards("phone")
        self.assertLessEqual(self.got["phone"]["scroll"], 391, "no page scroll sideways on a phone")

    def test_the_header_counts_what_is_real(self):
        self.assertEqual(self.got["desk"]["head"], "In progress | 5 | 1 working · 2 waiting", self.got["desk"])

    def test_the_words_read_in_every_theme(self):
        c = self.got["contrast"]
        self.assertGreaterEqual(len(c), 7, c)
        for theme, ratio in c.items():
            self.assertGreaterEqual(ratio, 4.5, f"{theme}: {c}")

    def test_running_only_keeps_the_working_and_idle_tasks(self):
        r = self.got["runOnly"]
        self.assertTrue(r["chip"], r)
        self.assertEqual(r["before"], sorted(self.WANT))
        # Working + Idle: the live task that asks you is not running.
        self.assertEqual((r["on"], r["pressed"]), (["busy", "idle"], "true"), r)
        self.assertFalse(r["archived"], r)
        self.assertEqual(r["busyBlocked"], "Waiting on you", r)
        self.assertEqual((r["off"], r["after"]), (sorted(self.WANT), "false"), r)


if __name__ == "__main__":
    unittest.main()
