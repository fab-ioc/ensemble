"""The count of what waits on you beside a project in the Projects panel is amber
(#388, GitHub issue 54), in headless Chrome over CDP against a hub in a thread.

Two projects: Waits, whose PO asked two questions, and Quiet, whose PO asked
nothing. Checked at 1440x900, in Light and in Dark:

* All and Waits show 2 in the "Waiting for you" pair: the pill's background is
  --c-warning-bg, its text --c-warning-fg at 600, both as the theme resolves
  them (Dark's are its own shades), at 4.5:1 or better;
* the selected row (Waits) keeps the same amber pill;
* Quiet, with nothing waiting, shows no count at all;
* the project switcher's count of the same project is the same colours, so the
  number reads alike wherever it shows;
* the page threw nothing.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
import inbox  # noqa: E402
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_left_strip_cdp import CDP_JS as _BASE, NODE  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

_HEAD = _BASE.split("async function main()")[0]
_FLAG = "'--disable-gpu', "
assert _FLAG in _HEAD
_HEAD = _HEAD.replace(_FLAG, _FLAG + "'--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', ")

CDP_JS = _HEAD + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId); await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const click = async (expr) => {
      const [x, y] = await evalIn(`(() => { const e = (${expr}); if (!e) throw new Error('nothing to click'); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1435, y: 895 }, sessionId);
    };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const s = { 'cd-list-dock': null, 'cd-list-dock-a7': null, 'cd-ld-sel': null, 'cd-tool-strip': null, 'cd-tool-open': null, 'cd-inbox-scope': 'all', 'cd-theme': 'light' };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); const s = ' + JSON.stringify(s) + '; for (const k in s) s[k] === null ? localStorage.removeItem(k) : localStorage.setItem(k, s[k]); } } catch (e) {}' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length >= 2', 30000);
    await until('window.ensBooted === true && !!IBX.data && !IBX.data.pending', 30000);
    await until(`!!document.querySelector('#ld-projs [data-ldp-proj="${A.ids.Waits}"] .ldp-n')`, 20000);
    // Every reading: the pills of All, Waits and Quiet, the switcher's count of Waits, and
    // the two tokens as this theme resolves them.
    const read = () => evalIn(`(() => {
      const css = e => { const s = getComputedStyle(e); return { n: e.textContent.trim(), bg: s.backgroundColor, fg: s.color, w: s.fontWeight, display: s.display }; };
      const rgb = v => { const e = document.createElement('i'); e.style.color = v; document.body.appendChild(e); const c = getComputedStyle(e).color; e.remove(); return c; };
      const row = sel => { const b = document.querySelector('#ld-projs ' + sel); if (!b) return null; const n = b.querySelector('.ldp-n');
        return { cur: b.getAttribute('aria-current') === 'true', pill: n ? css(n) : null }; };
      const host = document.createElement('div'); host.className = 'pm-probe'; host.style.cssText = 'position:fixed;left:-9999px;top:0;width:300px';
      host.innerHTML = projMenuHtml(); document.body.appendChild(host);
      const it = host.querySelector('.pm-item[data-proj=${JSON.stringify(A.ids.Waits)}] .pm-needs');
      const sw = it ? css(it) : null; host.remove();
      return { theme: document.documentElement.getAttribute('data-theme') || '',
        all: row('[data-ldp="all"]'), waits: row('[data-ldp-proj=${JSON.stringify(A.ids.Waits)}]'), quiet: row('[data-ldp-proj=${JSON.stringify(A.ids.Quiet)}]'),
        switcher: sw, tok: { bg: rgb('var(--c-warning-bg)'), fg: rgb('var(--c-warning-fg)'), nbg: rgb('var(--c-neutral-bg)') } };
    })()`);
    await click(`document.querySelector('#ld-projs [data-ldp-proj="${A.ids.Waits}"]')`); await sleep(500);
    await until(`document.querySelector('#ld-projs [data-ldp-proj="${A.ids.Waits}"]').getAttribute('aria-current') === 'true'`, 20000);
    await sleep(300);
    out.light = await read(); await shot('answer-count-light');
    await evalIn(`(() => { document.documentElement.setAttribute('data-theme', 'dark'); return 0; })()`); await sleep(300);
    out.dark = await read(); await shot('answer-count-dark');
    await c.send('Target.closeTarget', { targetId });
  } finally { try { ch.kill(); } catch (e) {} }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _lum(rgb: str) -> float:
    parts = [float(x) for x in rgb[rgb.index("(") + 1:rgb.index(")")].split(",")[:3]]
    c = [v / 255 for v in parts]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


@unittest.skipUnless(NODE and CHROME, "needs node and Chrome")
class AnswerCountColourInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-acc-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()
        patches = [
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(points, "mark_seen", lambda *a, **k: []),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        inbox.bind(dashboard)
        cls.ids = {}

        def project(name, asks):
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)},
                                                     {"identity": "codex", "agent": "codex", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            if asks:
                chatroom.post_message(po["id"], "claude", "Two things.\n\n" + "".join(
                    f"{i + 1}. **Ask:** {a}\n   - Yes\n   - No\n" for i, a in enumerate(asks)), to="user")
            else:
                chatroom.post_message(po["id"], "claude", "All quiet here.", to="user")
            cls.ids[name] = proj["id"]

        project("Waits", ["Ship the new tyre photos?", "Rename the board?"])
        project("Quiet", [])
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        server.handle_error = lambda *a: None
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        script = base / "answer_count_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{server.server_address[1]}",
                "ids": cls.ids, "shots": shots}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assert_amber(self, pill, tok, what):
        self.assertIsNotNone(pill, what)
        self.assertEqual(pill["bg"], tok["bg"], what)
        self.assertEqual(pill["fg"], tok["fg"], what)
        self.assertNotEqual(pill["bg"], tok["nbg"], f"{what}: no longer the neutral grey")
        self.assertEqual(pill["w"], "600", what)
        self.assertGreaterEqual(_contrast(pill["fg"], pill["bg"]), 4.5, what)

    def test_the_count_is_the_waiting_for_you_pair_in_light_and_dark(self):
        for theme in ("light", "dark"):
            g = self.got[theme]
            for key in ("all", "waits"):
                self.assertEqual(g[key]["pill"]["n"], "2", (theme, key))
                self.assert_amber(g[key]["pill"], g["tok"], (theme, key))
        self.assertEqual(self.got["dark"]["theme"], "dark")
        self.assertNotEqual(self.got["light"]["tok"]["bg"], self.got["dark"]["tok"]["bg"], "Dark has its own shade")

    def test_the_selected_row_keeps_the_amber_pill(self):
        for theme in ("light", "dark"):
            w = self.got[theme]["waits"]
            self.assertTrue(w["cur"], theme)
            self.assert_amber(w["pill"], self.got[theme]["tok"], (theme, "selected"))

    def test_zero_shows_no_count(self):
        for theme in ("light", "dark"):
            q = self.got[theme]["quiet"]
            self.assertIsNotNone(q, "Quiet has its row")
            self.assertIsNone(q["pill"], (theme, "nothing waits: no count"))

    def test_the_switcher_shows_the_same_number_in_the_same_colours(self):
        for theme in ("light", "dark"):
            g = self.got[theme]
            sw = g["switcher"]
            self.assertIsNotNone(sw, theme)
            self.assertEqual(sw["n"], g["waits"]["pill"]["n"], theme)
            self.assertEqual((sw["bg"], sw["fg"], sw["w"]), (g["waits"]["pill"]["bg"], g["waits"]["pill"]["fg"], g["waits"]["pill"]["w"]), theme)

    def test_no_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
