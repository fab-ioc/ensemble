"""Checkpoints in Chrome (ED-197), at 1728x1117 and 390x844: the marks show
in the task's chat, "Changes in this turn" opens that turn's diff in the
dashboard's Changes tool, "Go back to here" lists what will change and
restores it, and Undo puts the work back."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import checkpoints
import dashboard
import points
from test_page_update import CHROME
from test_po_chat_clean import NODE, _turn
from tests import chrome_profile


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8",
                          check=True).stdout.strip()


CDP_JS = chrome_profile.JS + r"""
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const udd = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + udd, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', '--window-size=1280,800', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ''; ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) res(m[1]); });
    ch.on('exit', c => rej(new Error('chrome exited ' + c))); setTimeout(() => rej(new Error('no devtools ' + buf)), 20000);
  });
  const ws = new WebSocket(wsUrl); let id = 0; const waits = new Map();
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const EXC = []; ws.onmessage = ev => { const m = JSON.parse(ev.data); if (m.method === 'Runtime.exceptionThrown') EXC.push(JSON.stringify(m.params.exceptionDetails).slice(0, 700)); if (m.id && waits.has(m.id)) { const w = waits.get(m.id); waits.delete(m.id); m.error ? w.rej(new Error(m.error.message)) : w.res(m.result); } };
  const send = (method, params = {}, sessionId) => { const i = ++id; return new Promise((res, rej) => { waits.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params, sessionId })); }); };
  const out = {};
  try {
    for (const [w, h, mob] of A.views) {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      await send('Page.enable', {}, sessionId); await send('Runtime.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } const msgs = await evalIn(`(document.querySelector('#msgs') || {}).textContent || ''`).catch(() => ''); throw new Error('timeout: ' + expr + ' :: ' + EXC.join(' | ') + ' :: ' + String(msgs).slice(0, 400)); };
      const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      const o = {};
      // The chat, in a window of its own.
      await send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
      await until('document.querySelectorAll("#msgs .cp-line").length >= 3');
      await sleep(300);
      o.marks = await evalIn(`(() => ({
        lines: [...document.querySelectorAll('#msgs .cp-line')].map(l => l.querySelector('.cp-what').textContent),
        buttons: [...document.querySelectorAll('#msgs .cp-line')].map(l => [...l.querySelectorAll('button')].map(b => b.textContent)),
        heights: [...document.querySelectorAll('#msgs .cp-btn')].map(b => Math.round(b.getBoundingClientRect().height)),
        inside: [...document.querySelectorAll('#msgs .cp-line, #msgs .cp-btn')].every(e => { const r = e.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1; }),
        order: [...document.querySelectorAll('#msgs > *')].map(e => e.classList.contains('cp-line') ? 'cp' : 'msg').join(' '),
        colour: getComputedStyle(document.querySelector('#msgs .cp-what')).color, muted: getComputedStyle(document.documentElement).getPropertyValue('--fg-muted').trim(),
        scrollX: document.documentElement.scrollWidth - innerWidth }))()`);
      await evalIn(`document.querySelector('#msgs .cp-line').scrollIntoView({ block: 'center' }); 0`);
      await shot(`checkpoints-${w}-marks`);
      // "Changes in this turn" without a dashboard around the chat: the dashboard in a tab, on that turn.
      o.opened = await evalIn(`(() => { let url = ''; const keep = window.open; window.open = u => { url = u; return null; };
        document.querySelector('#msgs [data-cp-diff="2"]').click(); window.open = keep; return url; })()`);
      // Go back to checkpoint 1: what will change, then the restore.
      await evalIn(`document.querySelector('#msgs [data-cp-back="1"]').click(); 0`);
      await until(`document.querySelector('#cp-dlg[open] .cp-files') && !document.querySelector('#cp-dlg .cp-ok').disabled`);
      o.dialog = await evalIn(`(() => { const d = document.querySelector('#cp-dlg'), r = d.getBoundingClientRect(); return {
        title: d.querySelector('h3').textContent, files: [...d.querySelectorAll('.cp-files .cp-path')].map(e => e.textContent),
        sums: [...d.querySelectorAll('.cp-sum')].map(e => e.textContent), commits: [...d.querySelectorAll('.cp-commits li')].map(e => e.textContent),
        left: Math.round(r.left), right: Math.round(innerWidth - r.right), buttons: [...d.querySelectorAll('.cp-acts button')].map(b => Math.round(b.getBoundingClientRect().height)) }; })()`);
      await shot(`checkpoints-${w}-confirm`);
      await evalIn(`document.querySelector('#cp-dlg .cp-ok').click(); 0`);
      await until(`!document.querySelector('#cp-dlg').open && document.querySelector('#msgs [data-cp-undo]')`);
      o.restored = await evalIn(`[...document.querySelectorAll('#msgs .cp-restore .cp-what')].map(e => e.textContent)`);
      await shot(`checkpoints-${w}-restored`);
      o.afterRestore = fs.readFileSync(path.join(A.repo, 'a.txt'), 'utf8');
      o.laterGone = !fs.existsSync(path.join(A.repo, 'later.txt'));
      // Undo: the work is back.
      await evalIn(`document.querySelector('#msgs [data-cp-undo]').click(); 0`);
      await until(`document.querySelector('#cp-dlg[open]') && !document.querySelector('#cp-dlg .cp-ok').disabled`);
      o.undoTitle = await evalIn(`document.querySelector('#cp-dlg h3').textContent`);
      await evalIn(`document.querySelector('#cp-dlg .cp-ok').click(); 0`);
      await until(`!document.querySelector('#cp-dlg').open && !document.querySelector('#msgs [data-cp-undo]')`);
      o.afterUndo = fs.readFileSync(path.join(A.repo, 'a.txt'), 'utf8');
      o.laterBack = fs.existsSync(path.join(A.repo, 'later.txt'));
      o.restoredAfterUndo = await evalIn(`[...document.querySelectorAll('#msgs .cp-restore .cp-what')].map(e => e.textContent)`);
      // The dashboard on that turn: its Changes tool lists the turn's files and opens a diff.
      await send('Page.navigate', { url: A.base + o.opened }, sessionId);
      await until(`[...document.querySelectorAll('.tch-files .chf[data-file]')].length >= 2`, 40000);
      o.turnHead = await evalIn(`document.querySelector('.tch-head').textContent`);
      o.turnFiles = await evalIn(`[...document.querySelectorAll('.tch-files .chf[data-file]')].map(e => e.dataset.file).sort()`);
      await evalIn(`document.querySelector('.tch-files .chf[data-file="a.txt"]').click(); 0`);
      await until(`document.querySelector('.tch-diff') && /turn 2/.test(document.querySelector('.tch-diff').textContent)`);
      o.diff = await evalIn(`/turn 1/.test(document.querySelector('.tch-diff').textContent) && /turn 2/.test(document.querySelector('.tch-diff').textContent)`);
      await shot(`checkpoints-${w}-turn-diff`);
      await evalIn(`document.querySelector('.tch-head .tch-back').click(); 0`);
      await until(`!document.querySelector('.tch-head .tch-back')`);
      o.backHead = await evalIn(`document.querySelector('.tch-head').textContent`);
      // A one-agent task: its chat is the agent's transcript, the marks among its turns.
      await send('Page.navigate', { url: A.base + '/session?room=' + A.solo }, sessionId);
      await until('document.querySelectorAll("#msgs .cp-line").length >= 2');
      o.solo = await evalIn(`[...document.querySelectorAll('#msgs > *')].map(e => e.classList.contains('cp-line') ? e.querySelector('.cp-what').textContent : 'msg')`);
      out[w] = o;
      await send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix="ens-cp-", ignore_cleanup_errors=True)
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        state = base / "state"
        state.mkdir()
        (base / "EnsembleProjects").mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        cls.told = []
        for patch in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
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
            mock.patch.object(dashboard, "workspace_access_ok", lambda path: True),
            mock.patch.object(dashboard, "_checkpoint_tell", lambda rid, line, *a: cls.told.append(line)),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        points._CACHE.clear(); points._SYNCED.clear(); points._SCANNED.clear(); points._ADOPT_SEEN.clear()
        for d in (dashboard._CP_DIR, dashboard._CP_LIST, dashboard._CP_CODEX, dashboard._CP_BASED):
            d.clear()
        repo = cls.repo = base / "task-repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("start\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "first")
        ok, project, _ = dashboard.register_project("Undo")
        assert ok, project
        rid = cls.rid = chatroom.create_room("Fix the parser", [
            {"identity": "claude", "agent": "claude", "model": "", "role": "engineer"},
            {"identity": "codex", "agent": "codex", "model": "", "role": "reviewer"}])["id"]
        room = chatroom.get_room(rid, public=False)
        room["cwd"] = str(repo)
        chatroom.update_room(room)
        dashboard.assign_session_project(rid, project["id"])
        # A one-agent task: its chat is the transcript of the agent's session.
        solo = cls.solo = chatroom.create_room("Tidy the docs", [
            {"identity": "claude", "agent": "claude", "model": "", "role": "engineer", "sessionId": "eng-sid"}])["id"]
        room = chatroom.get_room(solo, public=False)
        room["cwd"] = str(repo)
        chatroom.update_room(room)
        dashboard.assign_session_project(solo, project["id"])
        t0 = time.time() - 600
        (base / "transcripts" / "C--eng").mkdir()
        (base / "transcripts" / "C--eng" / "eng-sid.jsonl").write_text("".join(json.dumps(t) + "\n" for t in [
            _turn("user", "Tidy the docs.", t0), _turn("assistant", "Tidied.", t0 + 60),
            _turn("user", "And the index?", t0 + 120), _turn("assistant", "Done too.", t0 + 180)]), encoding="utf-8")
        with mock.patch.object(checkpoints.time, "time", return_value=t0 + 1):
            dashboard.checkpoint_turn(solo, kind="base")
        (repo / "a.txt").write_text("docs\n", encoding="utf-8")
        with mock.patch.object(checkpoints.time, "time", return_value=t0 + 61):
            dashboard.checkpoint_turn(solo, "claude")
        (repo / "a.txt").write_text("start\n", encoding="utf-8")
        # The task's turns: a message from each side, each turn's end a checkpoint.
        chatroom.post_message(rid, "user", "Fix the parser, please.", to="claude")
        time.sleep(0.05)
        dashboard.checkpoint_turn(rid, kind="base")
        (repo / "a.txt").write_text("turn 1\n", encoding="utf-8")
        time.sleep(0.05)
        chatroom.post_message(rid, "claude", "First pass done.", to="user")
        dashboard.checkpoint_turn(rid, "claude")
        (repo / "a.txt").write_text("turn 2\n", encoding="utf-8")
        (repo / "later.txt").write_text("later\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "second pass")
        time.sleep(0.05)
        chatroom.post_message(rid, "claude", "Second pass committed.", to="user")
        dashboard.checkpoint_turn(rid, "claude")
        assert [t["n"] for t in checkpoints.list_checkpoints(str(repo), rid)["turns"]] == [0, 1, 2]
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "repo": str(repo),
                "base": f"http://127.0.0.1:{server.server_address[1]}", "room": rid, "solo": solo, "shots": shots,
                "views": [[1728, 1117, False], [390, 844, True]]}
        path = base / "checkpoints_cdp.js"
        path.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def test_the_marks_show_in_the_chat(self):
        for w, result in self.got.items():
            m = result["marks"]
            self.assertEqual(m["lines"][:3], ["Checkpoint 0 · the code when the task started",
                                          "Checkpoint 1 · 1 file +1 −1", "Checkpoint 2 · 2 files +2 −1"], w)
            self.assertEqual(m["buttons"][:3], [["Go back to here"], ["Changes in this turn", "Go back to here"],
                                            ["Changes in this turn", "Go back to here"]], w)
            self.assertTrue(m["order"].startswith("msg cp msg cp msg cp"), (w, m["order"]))   # task, ask, base, turn 1, turn 2
            self.assertTrue(m["inside"], w)
            self.assertLessEqual(m["scrollX"], 1, w)
            self.assertEqual(result["solo"], ["msg", "Checkpoint 0 · the code when the task started", "msg",
                                              "Checkpoint 1 · 1 file +1 −1", "msg", "msg"], w)
        self.assertTrue(all(h >= 44 for h in self.got["390"]["marks"]["heights"]), self.got["390"]["marks"]["heights"])
        self.assertTrue(all(h == 24 for h in self.got["1728"]["marks"]["heights"]))

    def test_changes_in_this_turn_opens_the_diff_of_that_turn(self):
        for w, result in self.got.items():
            self.assertEqual(result["opened"], f"/?task={self.rid}&turn=2", w)
            self.assertIn("Checkpoint 2", result["turnHead"], w)
            self.assertEqual(result["turnFiles"], ["a.txt", "later.txt"], w)
            self.assertTrue(result["diff"], w)
            self.assertNotIn("Checkpoint", result["backHead"], w)

    def test_go_back_lists_the_changes_restores_and_undo_puts_it_back(self):
        for w, result in self.got.items():
            d = result["dialog"]
            self.assertEqual(d["title"], "Go back to checkpoint 1?", w)
            self.assertEqual(sorted(d["files"]), ["a.txt", "later.txt"], w)
            self.assertEqual(len(d["commits"]), 1, w)
            self.assertIn("second pass", d["commits"][0], w)
            self.assertGreaterEqual(min(d["left"], d["right"]), 15, w)
            self.assertEqual(result["afterRestore"].replace("\r\n", "\n"), "turn 1\n", w)
            self.assertTrue(result["laterGone"], w)
            self.assertIn("Restored to checkpoint 1 (by sam)", result["restored"], w)
            self.assertEqual(result["undoTitle"], "Undo the restore?", w)
            self.assertEqual(result["afterUndo"].replace("\r\n", "\n"), "turn 2\n", w)
            self.assertTrue(result["laterBack"], w)
            self.assertIn("Undid the restore (by sam)", result["restoredAfterUndo"], w)
        self.assertTrue(all(h >= 44 for h in self.got["390"]["dialog"]["buttons"]))
        self.assertEqual(git(self.repo, "log", "-1", "--format=%s"), "second pass")
        self.assertTrue(any("restored the code to checkpoint 1" in t for t in self.told))


if __name__ == "__main__":
    unittest.main()
