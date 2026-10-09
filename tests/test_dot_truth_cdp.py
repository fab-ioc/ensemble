"""#190 (GitHub issue 13): the dots in headless Chrome over CDP, against a
throwaway hub in a thread (its own state in a temp dir, fake terminals):

* a task whose agent is working is green (#193: only then), at 1728 px and
  on a phone (390 px);
* a task whose terminal runs and does nothing is an idle ring, not green;
* a Done task that does not run is not green (it was, before);
* a task whose agent died and was resumed since is not red;
* the same death nobody dealt with is red, so the check above is not empty;
* Done with this, in a waiting row's Details, takes the row out and the hub
  keeps it (``dealtAt``), through the page's own key;
* a question two days old on a task and on a PO leaves Waiting for you once
  its chat is opened and read, even after the hub drew a new page key while
  the page stayed open (the Music PO, point P157);
* a phone's project cards: green only for a project with an agent working.

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
import attention  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_po_chat_clean import _turn  # noqa: E402

NODE = shutil.which("node")

CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--window-size=1728,1000', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
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
    await send('Page.navigate', { url: A.base + '/' }, sessionId);
    const rooms = JSON.stringify(A.rooms);
    // The grouped view holds a project's tasks; Done today unfolded.
    const read = `(() => { try { localStorage.setItem('cd-switcher-view', 'status'); } catch (e) {}
      if (typeof renderRows !== 'function' || !ALL_ROWS || !ALL_ROWS.length) return null;
      SW_DONE_OPEN = true; renderRows();
      const out = {};
      for (const [k, rid] of Object.entries(${rooms})) {
        const dots = [...document.querySelectorAll('.sw-row[data-room="' + rid + '"] .sw-st')];
        out[k] = dots.map(d => [...d.classList].filter(c => c !== 'sw-st').join(' ') + '|' + d.getAttribute('aria-label'));
      }
      return out; })()`;
    let got = null; const t = Date.now();
    while (Date.now() - t < 30000) {
      try { got = await evalIn(read); } catch (e) { got = null; }
      if (got && got.gone.length && got.gone.every(s => s.startsWith('danger'))
          && got.busy.length && got.busy.every(s => s.startsWith('working'))
          && got.asks.length && got.asks.every(s => s.startsWith('warning'))   // the attention answer is in
          && await evalIn(`['oldtask', 'oldpo'].every(k => document.querySelector('#sw-list .sw-row.needs[data-room="' + ${rooms}[k] + '"]'))`)) break;   // and the PO's row, placed once the projects came
      await sleep(250);
    }
    // How the dots look: green fill for working, a ring and no fill for idle.
    const paint = `(() => { const o = {};
      for (const k of ['busy', 'live']) { const d = document.querySelector('.sw-row[data-room="' + ${rooms}[k] + '"] .sw-st');
        const cs = d && getComputedStyle(d); o[k] = cs ? [cs.backgroundColor, cs.boxShadow] : null; }
      return o; })()`;
    got.paint = await evalIn(paint);
    // A hub restart draws a new page key while this page and its chats stay
    // open (the Python side swaps it when asked). The chats, opened before in
    // hidden frames of the page (nothing in view, so nothing read), are then
    // shown and read the way a person does: the first read is refused with the
    // old key, the page fetches the new one and sends it again, and both rows
    // leave Waiting for you.
    got.oldOpen = await evalIn(`(async () => { const R = ${rooms}, out = {};
      const row = rid => !!document.querySelector('#sw-list .sw-row.needs[data-room="' + rid + '"]');
      window.__frames = {};
      for (const k of ['oldtask', 'oldpo']) {
        out[k] = { before: row(R[k]) };
        const f = document.createElement('iframe');
        f.style.cssText = 'position:fixed;left:0;top:0;width:800px;height:600px;z-index:99999';
        f.hidden = true;
        f.src = '/session?room=' + R[k];
        document.body.appendChild(f);
        for (let i = 0; i < 150 && !(f.contentWindow && f.contentDocument.readyState === 'complete'
          && f.contentWindow.eval('typeof CHAT_DRAWN !== "undefined" && CHAT_DRAWN')); i++) await new Promise(r => setTimeout(r, 100));
        out[k].drawn = !!f.contentWindow.eval('CHAT_DRAWN && LAST_ITEMS && LAST_ITEMS.length');
        const log = [], fetch0 = f.contentWindow.fetch;
        f.contentWindow.fetch = (u, o) => fetch0(u, o).then(r => { if (String(u).includes('/api/attention/seen')) log.push(r.status); return r; });
        window.__frames[k] = { f, log };
      }
      return out; })()`);
    const fs = require('fs');
    fs.writeFileSync(A.tmp + '/rotate', '1');
    for (let i = 0; i < 100 && !fs.existsSync(A.tmp + '/rotated'); i++) await sleep(100);
    got.oldRead = await evalIn(`(async () => { const R = ${rooms}, out = {};
      const row = rid => !!document.querySelector('#sw-list .sw-row.needs[data-room="' + rid + '"]');
      for (const k of ['oldtask', 'oldpo']) {
        const { f, log } = window.__frames[k];
        f.hidden = false;
        await new Promise(r => setTimeout(r, 500));
        const b = f.contentDocument.querySelector('#msgs .cu-read');
        out[k] = { markRead: !!b };
        if (b) b.click();
        for (let i = 0; i < 150 && row(R[k]); i++) await new Promise(r => setTimeout(r, 100));
        out[k].after = row(R[k]);
        out[k].seen = log.slice();
        f.remove();
      }
      return out; })()`);
    // The same on a phone (390 px): the list's rows are drawn the same way.
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await sleep(300);
    got.phone = await evalIn(`(() => { const r = ${read}; return r && { busy: r.busy, live: r.live }; })()`);
    got.phonePaint = await evalIn(paint);
    // A phone's home is the project cards: their dots follow the same rule.
    got.cards = await evalIn(`(async () => { SELECTED_PROJECT = null; renderRows();
      for (let i = 0; i < 40 && !document.querySelector('.proj[data-proj] .srow .sdot'); i++) await new Promise(r => setTimeout(r, 100));
      const R = ${rooms}, out = {};
      out.rows = {};
      for (const [k, rid] of Object.entries(R)) { const d = document.querySelector('.proj .srow[data-sid="' + rid + '"] .sdot');
        if (d) out.rows[k] = [...d.classList].join(' ') + '|' + d.title; }
      const card = document.querySelector('.proj[data-proj]:not(.unassigned) .row1 .live');
      out.card = card ? card.textContent.trim() + '|' + !!card.querySelector('.live-dot.idle') : null;
      return out; })()`);
    await send('Emulation.clearDeviceMetricsOverride', {}, sessionId);
    await sleep(300);
    // Done with this: open the waiting row's Details, press it.
    got.doneWithThis = await evalIn(`(async () => { const rid = ${rooms}.asks;
      const row = () => document.querySelector('#sw-list .sw-row.needs[data-room="' + rid + '"]');
      const before = !!row();
      const det = document.querySelector('#sw-list .sw-diag-btn[data-room="' + rid + '"]');
      if (!det) return { before, err: 'no details' };
      det.click(); await new Promise(r => setTimeout(r, 100));
      const b = document.querySelector('#sw-list .sw-done[data-room="' + rid + '"]');
      if (!b) return { before, err: 'no done button' };
      b.click();
      for (let i = 0; i < 40 && row(); i++) await new Promise(r => setTimeout(r, 100));
      return { before, after: !!row() }; })()`);
    console.log(JSON.stringify(got));
  } finally { ch.kill(); }
}
main().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def _live(rid, pty="pty-live", screen="● ok\n❯ \n"):
    return types.SimpleNamespace(
        id=pty, alive=lambda: True, tail=lambda: screen, last_output=time.time(),
        info=lambda: {"idleSeconds": 30}, last_submit=lambda: 0.0, death=lambda: None, last_input=0.0,
        meta={"room": rid, "identity": "claude"})


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheDotsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-dot-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        live = {}
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
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = str(Path(proj.get("home") or proj["path"]))
        died = time.time() - 600

        def task(title, **fields):
            rid = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": home}])["id"]
            chatroom.post_message(rid, "user", "Please look at it")
            full = chatroom.get_room(rid, public=False)
            for m in full.get("messages") or []:
                m["ts"] = died - 3600      # written before the death: not a sign of use since
            part = chatroom.participant(full, "claude")
            part["sessionId"] = "s-" + rid
            for k, v in fields.pop("part", {}).items():
                part[k] = v
            full.update(mode="solo", launched=True, **fields)
            chatroom.update_room(full)
            dashboard.assign_session_project(rid, proj["id"])
            return rid

        def gone(pty):
            return {"ptyId": pty, "lastExit": {"ptyId": pty, "exitCode": 1, "killed": False,
                                               "endedAt": died, "tail": "● ok\n", "lastInput": 0.0}}

        rooms = {
            "live": task("Runs now", workflow="inprogress", part={"ptyId": "pty-live"}),
            "busy": task("Works now", workflow="inprogress", part={"ptyId": "pty-busy"}),
            "asks": task("Asks you", workflow="inprogress", part={"ptyId": "pty-asks"}),
            "oldtask": task("Asked two days ago", workflow="inprogress", part={"ptyId": "pty-ot"}),
            "oldpo": task("Motors PO", part={"ptyId": "pty-op"}),
            "done": task("Finished this morning", workflow="done", part={"ptyId": "pty-old"}),
            "resumed": task("Died then resumed", workflow="inprogress",
                            part={**gone("pty-r"), "resumedAt": died + 60}),
            "gone": task("Died, nobody looked", workflow="inprogress", part=gone("pty-g")),
        }
        live["pty-live"] = _live(rooms["live"])
        live["pty-busy"] = _live(rooms["busy"], "pty-busy",
                                 "● Checking the listings\n\n✻ Working… (12s · esc to interrupt)\n")
        agent_hooks.reset()
        cls.addClassCleanup(agent_hooks.reset)
        agent_hooks.record({"room": rooms["busy"], "identity": "claude", "ptyId": "pty-busy",
                            "event": {"hook_event_name": "UserPromptSubmit", "session_id": "s-busy"}},
                           lambda pid: (rooms["busy"], "claude"))
        live["pty-asks"] = _live(rooms["asks"], "pty-asks")
        chatroom.record_report(rooms["asks"], "claude", "question", "Which price?")
        live["pty-ot"] = _live(rooms["oldtask"], "pty-ot")
        live["pty-op"] = _live(rooms["oldpo"], "pty-op")
        ok, why = dashboard.set_project_po(proj["id"], rooms["oldpo"])
        assert ok, why
        chatroom.record_report(rooms["oldtask"], "claude", "question", "Which colour?")
        chatroom.post_message(rooms["oldpo"], "claude", "Ask: Start task #1 now?", to="user")
        ago = time.time() - 2 * 86400
        (base / "transcripts" / "C--x").mkdir()
        for rid, ask in ((rooms["oldtask"], "Which colour?"), (rooms["oldpo"], "Ask: Start task #1 now?")):
            # Asked two days ago, never read: the chat draws the agent's transcript.
            turns = [_turn("user", "Please look at it", ago - 60), _turn("assistant", ask, ago)]
            (base / "transcripts" / "C--x" / f"s-{rid}.jsonl").write_text(
                "".join(json.dumps(t) + "\n" for t in turns), encoding="utf-8")
            full = chatroom.get_room(rid, public=False)
            for m in full["messages"][-1:]:
                m["ts"] = float(m["ts"]) - 2 * 86400
            for k in ("lastReport", "lastRealReport"):
                if isinstance(full.get(k), dict) and full[k].get("ts"):
                    full[k]["ts"] = float(full[k]["ts"]) - 2 * 86400
            chatroom.update_room(full)

        def swap_key():
            flag = base / "rotate"
            for _ in range(2000):
                if flag.exists():
                    dashboard._UI_KEY = "rotated-" + dashboard.secrets.token_urlsafe(16)
                    (base / "rotated").write_text("1", encoding="utf-8")
                    return
                time.sleep(0.1)
        cls.addClassCleanup(setattr, dashboard, "_UI_KEY", dashboard._UI_KEY)
        # What the hub answered each read point: the page's chats (its own
        # embedded chat too) send them, so which frame met the old key varies.
        cls.seen_log = []
        refusal = dashboard.Handler._page_refusal

        def logged(h):
            why = refusal(h)
            if h.path.startswith("/api/attention/seen"):
                cls.seen_log.append("refused" if why else "ok")
            return why
        p = mock.patch.object(dashboard.Handler, "_page_refusal", logged)
        p.start()
        cls.addClassCleanup(p.stop)
        threading.Thread(target=swap_key, daemon=True).start()
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        script = base / "dot_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{server.server_address[1]}", "rooms": rooms}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=200)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        cls.dealt = chatroom.get_room(rooms["asks"], public=False).get("dealtAt")
        cls.seen = {k: chatroom.get_room(rooms[k], public=False).get("seenAt") for k in ("oldtask", "oldpo")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def dots(self, key):
        got = self.got[key]
        self.assertTrue(got, f"{key}: its row is in the list ({self.got})")
        return got

    def test_a_working_agent_is_green(self):
        self.assertTrue(all(d == "working|working" for d in self.dots("busy")), self.got)
        bg, _ = self.got["paint"]["busy"]
        self.assertNotIn(bg, ("", "rgba(0, 0, 0, 0)", "transparent"), self.got["paint"])

    def test_a_running_terminal_doing_nothing_is_an_idle_ring(self):
        self.assertTrue(all(d == "idle|idle: running, not working" for d in self.dots("live")), self.got)
        bg, ring = self.got["paint"]["live"]
        self.assertIn(bg, ("rgba(0, 0, 0, 0)", "transparent"), self.got["paint"])
        self.assertIn("inset", ring)
        self.assertNotEqual(bg, self.got["paint"]["busy"][0])

    def test_a_phone_draws_the_same_dots(self):
        self.assertEqual(self.got["phone"], {"busy": self.got["busy"], "live": self.got["live"]}, self.got)
        self.assertEqual(self.got["phonePaint"], self.got["paint"])

    def test_an_old_question_read_after_a_new_key_leaves(self):
        self.assertEqual(self.got["oldOpen"], {k: {"before": True, "drawn": True} for k in ("oldtask", "oldpo")},
                         "both wait for you, and their chats are drawn, unread")
        for k, r in self.got["oldRead"].items():
            self.assertFalse(r["after"], f"{k} left Waiting for you: {r}")
        # The read of the PO's chat can come from the page's own PO panel (an
        # embedded chat of the same room), so which frame sent it varies; the
        # hub's log below holds them all.
        log = self.seen_log
        self.assertIn("refused", log, "a read met the old key")
        self.assertIn("ok", log[log.index("refused"):], f"and was sent again with the new one: {log}")
        self.assertTrue(all(self.seen.values()), f"the hub kept the read point: {self.seen}")

    def test_a_phones_project_cards_are_green_only_for_work(self):
        c = self.got["cards"]
        self.assertTrue(c["rows"], c)
        for k, dot in c["rows"].items():
            self.assertEqual(dot.startswith("sdot work"), k == "busy", c)
        # What needs you shows on the card too: an ask is yellow, a death red.
        self.assertTrue(c["rows"].get("asks", "").startswith("sdot wait"), c)
        self.assertTrue(c["rows"].get("gone", "").startswith("sdot danger"), c)
        self.assertEqual(c["card"], "1 working|false", c)

    def test_done_with_this_takes_the_row_out(self):
        self.assertEqual(self.got["doneWithThis"], {"before": True, "after": False}, self.got["doneWithThis"])
        self.assertTrue(self.dealt, "the hub keeps it")

    def test_a_done_task_that_does_not_run_is_not_green(self):
        self.assertEqual(set(self.dots("done")), {"off|done, stopped"}, self.got)

    def test_a_resumed_once_gone_task_is_not_red(self):
        self.assertFalse(any(d.startswith("danger") for d in self.dots("resumed")), self.got)

    def test_a_death_nobody_dealt_with_is_red(self):
        self.assertTrue(all(d.startswith("danger") for d in self.dots("gone")), self.got)


if __name__ == "__main__":
    unittest.main()
