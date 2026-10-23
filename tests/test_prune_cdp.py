"""Free disk space in Chrome (#260), at 1728x1117 and 390x844, on a hub of
throwaway state: Settings' Disk space makes a preview, the dialog lists the
groups with their sizes and ticks (your own conversations unticked, an
unmerged branch named as kept), and Delete removes what was ticked: the Done
tasks' worktrees, folders and conversations go, the running task, Documents
and the unmerged branch stay, and a pruned task keeps its slim record."""
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
import dashboard
import prune
from test_page_update import CHROME
from test_po_chat_clean import NODE
from tests import chrome_profile

OLD = time.time() - 60 * 86400


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
    for (const [w, h, mob, del] of A.views) {
      const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      await send('Page.enable', {}, sessionId); await send('Runtime.enable', {}, sessionId);
      await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: mob }, sessionId);
      if (mob) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async expr => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 40000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(200); } const d = await evalIn(`(document.querySelector('#modal') || {}).textContent || ''`).catch(() => ''); throw new Error('timeout: ' + expr + ' :: ' + EXC.join(' | ') + ' :: ' + String(d).slice(0, 600)); };
      const shot = async name => { if (!A.shots) return; const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
      const o = {};
      await send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until(`typeof renderPrunePrefs === 'function' && document.querySelector('#pref-prune-auto option')`);
      await evalIn(`openSettings('prune'); 0`);
      await sleep(300);
      o.section = await evalIn(`(() => { const s = document.querySelector('#settings-prune'); const sel = s.querySelector('#pref-prune-auto');
        return { text: s.innerText, options: [...sel.options].map(x => x.textContent), value: sel.value,
                 hint: s.querySelector('#prune-auto-hint').textContent }; })()`);
      await shot(`prune-${w}-settings`);
      await evalIn(`document.querySelector('#prune-days').value = '30'; document.querySelector('#prune-preview-btn').click(); 0`);
      await until(`document.querySelector('#modal[open] .pr-group') && document.querySelector('#pr-go')`);
      await sleep(200);
      o.preview = await evalIn(`(() => { const d = document.querySelector('#modal'), r = d.getBoundingClientRect();
        const groups = [...d.querySelectorAll('.pr-group')].map(g => ({ id: g.dataset.g, head: g.querySelector('.pr-ghead').innerText,
          ticked: g.querySelector('.pr-ghead input').checked, items: [...g.querySelectorAll('input[data-id]')].map(i => ({ id: i.dataset.id, on: i.checked, text: i.closest('li').innerText })) }));
        return { groups, kept: (d.querySelector('.pr-kept') || {}).textContent || '', go: d.querySelector('#pr-go').textContent,
          ticked: d.querySelector('#pr-ticked').textContent, left: Math.round(r.left), right: Math.round(innerWidth - r.right),
          inside: [...d.querySelectorAll('*')].every(e => { const b = e.getBoundingClientRect(); return !b.width || (b.right <= r.right + 1 && b.left >= r.left - 1); }),
          scrollX: document.documentElement.scrollWidth - innerWidth, dlgScrollX: d.scrollWidth - d.clientWidth,
          buttons: [...d.querySelectorAll('.pr-acts button')].map(b => Math.round(b.getBoundingClientRect().height)),
          rows: [...d.querySelectorAll('.pr-ghead')].map(b => Math.round(b.getBoundingClientRect().height)),
          danger: getComputedStyle(d.querySelector('#pr-go')).color, dangerFg: getComputedStyle(document.documentElement).getPropertyValue('--c-danger-fg').trim(),
          dangerBg: getComputedStyle(d.querySelector('#pr-go')).backgroundColor }; })()`);
      await evalIn(`document.querySelectorAll('#modal .pr-group details').forEach(x => x.open = true); 0`);
      await sleep(150);
      await shot(`prune-${w}-preview`);
      // Untick a group, then tick it back: the button follows.
      o.untick = await evalIn(`(() => { const b = document.querySelector('#modal input[data-g="done"]'); b.click(); const t1 = document.querySelector('#pr-go').textContent;
        b.click(); return [t1, document.querySelector('#pr-go').textContent]; })()`);
      if (del) {
        await evalIn(`document.querySelector('#pr-go').click(); 0`);
        await until(`document.querySelector('#modal[open] #pr-again')`, 60000);
        o.result = await evalIn(`document.querySelector('#modal .pr-dlg').innerText`);
        await shot(`prune-${w}-result`);
      }
      await evalIn(`document.querySelector('#pr-close').click(); 0`);
      o.closed = await evalIn(`!document.querySelector('#modal').open`);
      o.errors = EXC.slice();
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
        # A short root: git and the long-path folder below need the room.
        base = cls.base = Path(tempfile.mkdtemp(prefix="pc-"))
        cls.addClassCleanup(lambda: prune.remove(base))
        state = base / "st"
        state.mkdir()
        root = base / "EP"
        root.mkdir()
        trans = cls.trans = base / "tr"
        trans.mkdir()
        (base / "cs").mkdir()
        for patch in [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
            mock.patch.object(dashboard, "PRUNE_AUTO_FILE", state / "prune-auto.json"),
            mock.patch.object(dashboard, "PROJ_DIR", trans),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(chatroom, "ROOMS_DIR", state / "rooms"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard.ptyrun, "list_sessions", lambda *a, **k: []),
            mock.patch.object(dashboard, "operator_name", return_value="sam"),
            mock.patch.object(dashboard, "workspace_access_ok", lambda path: True),
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]:
            patch.start()
            cls.addClassCleanup(patch.stop)
        code = cls.code = base / "code"
        code.mkdir()
        git(code, "init", "-q", "-b", "main")
        git(code, "config", "user.email", "t@t")
        git(code, "config", "user.name", "t")
        (code / "a.txt").write_text("start\n", encoding="utf-8")
        git(code, "add", "-A")
        git(code, "commit", "-q", "-m", "first")
        ok, project, why = dashboard.register_project(str(code), name="Shop")
        assert ok, why
        home = cls.home = Path(dashboard.project_home(project))
        docs = cls.docs = Path(dashboard.project_documents_dir(project))
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "#1 report.md").write_text("keep", encoding="utf-8")
        cls.tasks = {}
        for name, workflow, unmerged, deep in (("old-a", "done", False, True), ("old-b", "done", True, False),
                                               ("busy", "inprogress", False, False)):
            folder = home / name
            folder.mkdir()
            wt = folder / "repo"
            branch = f"sess/{name}"
            git(code, "worktree", "add", "-q", "-b", branch, str(wt))
            if unmerged:
                (wt / "b.txt").write_text("work\n", encoding="utf-8")
                git(wt, "add", "-A")
                git(wt, "commit", "-q", "-m", "work")
            if deep:
                p = folder / "build"
                while len(str(p)) < 300:
                    p = p / ("n" * 40)
                os.makedirs(prune._long(p))
                with open(prune._long(p / "f.bin"), "wb") as f:
                    f.write(b"x" * 200_000)
            sid = f"{abs(hash(name)) % 10**8:08d}-1111-2222-3333-444444444444"
            tdir = trans / "C--x"
            tdir.mkdir(exist_ok=True)
            tp = tdir / f"{sid}.jsonl"
            tp.write_text('{"type":"user"}\n' * 4000, encoding="utf-8")
            os.utime(tp, (OLD, OLD))
            rid = chatroom.create_room(name, [{"identity": "claude", "agent": "claude", "model": "", "role": "engineer",
                                               "sessionId": sid, "cwd": str(wt)}])["id"]
            room = chatroom.get_room(rid, public=False)
            room.update(cwd=str(wt), taskDir=str(folder), workflow=workflow, workflowAt=OLD, spec="Do it",
                        lastReport={"kind": "completed", "text": "Done."},
                        workspace={"mode": "worktree", "branch": branch, "taskDir": str(folder)})
            chatroom.update_room(room)
            dashboard.assign_session_project(rid, project["id"])
            chatroom.post_message(rid, "claude", "progress " * 300, to="user")
            cls.tasks[name] = {"rid": rid, "folder": folder, "wt": wt, "branch": branch, "conv": tp}
        own = trans / "C--Users-sam-elsewhere" / "99999999-1111-2222-3333-444444444444.jsonl"
        own.parent.mkdir()
        own.write_text('{"type":"user","cwd":"C:\\\\Users\\\\sam\\\\elsewhere"}\n', encoding="utf-8")
        os.utime(own, (OLD, OLD))
        cls.own = own
        logs = state / "logs"
        logs.mkdir()
        (logs / "ensemble.log.1").write_text("old " * 1000, encoding="utf-8")
        os.utime(logs / "ensemble.log.1", (OLD, OLD))   # one written today is never listed
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": str(base), "base": f"http://127.0.0.1:{server.server_address[1]}",
                "shots": shots, "views": [[1728, 1117, False, False], [390, 844, True, True]]}
        path = base / "prune_cdp.js"
        path.write_text(CDP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(path), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert run.returncode == 0, run.stderr[-4000:]
        cls.got = json.loads(run.stdout.strip().splitlines()[-1])

    def test_settings_section(self):
        for w, o in self.got.items():
            self.assertIn("disk space", o["section"]["text"].lower(), w)
            self.assertEqual(o["section"]["value"], "0", w)
            self.assertTrue(o["section"]["options"][0].startswith("Off"), w)
            self.assertIn("nothing is removed", o["section"]["hint"], w)
            self.assertEqual(o["errors"], [], w)

    def test_preview_lists_groups_with_ticks(self):
        a, b, busy = (self.tasks[k]["rid"] for k in ("old-a", "old-b", "busy"))
        for w, o in self.got.items():
            pv = o["preview"]
            groups = {g["id"]: g for g in pv["groups"]}
            done = {i["id"] for i in groups["done"]["items"]}
            self.assertEqual(done, {f"done:{a}", f"done:{b}"}, w)
            self.assertTrue(groups["done"]["ticked"], w)
            self.assertFalse(groups["own"]["ticked"], w)
            self.assertTrue(all(not i["on"] for i in groups["own"]["items"]), w)
            self.assertIn("housekeeping", groups, w)
            self.assertIn("sess/old-b", pv["kept"], w)
            self.assertIn("not merged", pv["kept"], w)
            self.assertRegex(pv["go"], r"^Delete \d+ items \(", w)
            self.assertNotEqual(o["untick"][0], o["untick"][1], w)
            self.assertEqual(o["untick"][1], pv["go"], w)
            self.assertTrue(pv["inside"], w)
            self.assertLessEqual(pv["scrollX"], 1, w)
            self.assertLessEqual(pv["dlgScrollX"], 1, w)
            self.assertEqual(pv["danger"].replace(" ", ""), self._rgb(pv["dangerFg"]), w)
            self.assertIn(pv["dangerBg"], ("rgba(0, 0, 0, 0)", "transparent"), w)
            self.assertTrue(o["closed"], w)
        self.assertTrue(all(x >= 44 for x in self.got["390"]["preview"]["buttons"]), self.got["390"]["preview"])
        self.assertTrue(all(x >= 44 for x in self.got["390"]["preview"]["rows"]), self.got["390"]["preview"])
        self.assertGreaterEqual(self.got["390"]["preview"]["left"], 8)

    @staticmethod
    def _rgb(hex_):
        h = hex_.lstrip("#")
        return f"rgb({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)})"

    def test_delete_removes_what_was_ticked(self):
        res = self.got["390"]["result"]
        self.assertIn("Freed", res)
        a, b, busy = self.tasks["old-a"], self.tasks["old-b"], self.tasks["busy"]
        for t in (a, b):
            self.assertFalse(os.path.lexists(prune._long(t["folder"])), t["folder"])
            self.assertFalse(t["conv"].exists())
            room = chatroom.get_room(t["rid"])
            self.assertIn("pruned", room)
            self.assertEqual(room["spec"], "Do it")
            self.assertEqual([m.get("noticeKind") for m in room["messages"]], ["pruned"])
        self.assertEqual(git(self.code, "branch", "--list", a["branch"]), "")
        self.assertIn(b["branch"], git(self.code, "branch", "--list", b["branch"]))
        self.assertTrue(busy["wt"].exists())
        self.assertTrue(busy["conv"].exists())
        self.assertTrue((self.docs / "#1 report.md").exists())
        self.assertTrue(self.own.exists())          # not ticked: stays
        self.assertTrue((self.code / "a.txt").exists())
        log = (dashboard.DASHBOARD_DIR / "logs" / "prune.log").read_text(encoding="utf-8")
        self.assertIn("old-a", log)


if __name__ == "__main__":
    unittest.main()
