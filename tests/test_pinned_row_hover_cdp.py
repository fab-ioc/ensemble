"""#378 (P363): the pointer resting on a left-list project row shows a steady row.

Real Chrome (CDP), desktop 1440x900 with a mouse and hover, docked list: a
pinned project with a short name and one with a cut (long) name, and the same
two unpinned. For each, the pointer rests ~2 s on the row's name, on its pin
button and on its ⋯; every animation frame records what the eye sees (the pin
button's and ⋯'s opacity, the pin mark's visibility, the grip or swatch, the
raised copy of a cut name, which element is under the pointer, and the row's
node identity). Nothing of it may change while the pointer rests.
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
import points  # noqa: E402
from tests import chrome_profile  # noqa: E402
from tests.test_left_strip_cdp import CDP_JS as STRIP_JS, NODE  # noqa: E402
from tests.test_page_update import CHROME  # noqa: E402

NAMES = ["Atlas", "Harbor", "Orchard", "Lumen", "Quarry", "Juniper", "Beacon", "Cobalt"]
LONG = "A pinned project with a name far too long for the left list"
LONG2 = "An unpinned project with a name far too long for the left list"
REST_MS = 2000
# Where the pointer rests: the name, the pin button and ⋯, each also 2 px inside the row's bottom edge
# (for the buttons, which are 22 px in a 28 px row, that is 1 px under them: the row's flicker band).
WHERE = ("row", "rowTop", "rowBottom", "pin", "pinBottom", "dots", "dotsBottom")

MAIN = r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const ID = A.ids;
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s); await c.send('Runtime.enable', {}, s);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(120); } return null; };
    const mouse = (type, x, y, extra) => c.send('Input.dispatchMouseEvent', Object.assign({ type, x, y }, extra || {}), s);
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); for (const k of ["cd-list-dock", "cd-list-dock-a7", "cd-ld-sel", "cd-tool-strip", "cd-tool-open"]) localStorage.removeItem(k); } } catch (e) {}' }, s);
    await c.send('Page.navigate', { url: A.base + '/' }, s);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 5', 30000);
    await until('window.ensBooted === true', 30000);
    await until('!!LD.dock && document.querySelectorAll("#ld-projs .ldp-row[data-ldp-proj]").length >= 5', 30000);
    await ev('(() => { const m = document.querySelector("#ld-projs [data-ldp-more=proj][aria-expanded=false]"); if (m) m.click(); return 0; })()');
    await sleep(400);
    for (const k of ['Atlas', 'Long']) { await ev(`pinToggle(${JSON.stringify(ID[k])}), 0`); await until('PIN.busy === 0', 8000); await sleep(200); }
    await until(`document.querySelectorAll('#ld-projs .ldp-hand').length === 2`, 8000);
    await sleep(400);
    // Every frame, what the eye sees of the item and what is under the pointer.
    await ev(`window.HOVER_REC = (pid, x, y, ms) => new Promise(done => {
      const ids = new WeakMap(); let nid = 0; const idOf = e => { if (!e) return 0; if (!ids.has(e)) ids.set(e, ++nid); return ids.get(e); };
      const cs = (e, k) => e ? getComputedStyle(e)[k] : '-';
      const frames = [], t0 = performance.now();
      const tick = () => {
        const it = document.querySelector('#ld-projs [data-pin-row="' + CSS.escape(pid) + '"]');
        const row = it && it.querySelector('.ldp-row');
        const copy = document.querySelector('body > .row-expanded.ldp-expanded');
        const under = document.elementFromPoint(x, y);
        const f = [
          'item#' + idOf(it), 'row#' + idOf(row), 'h:' + (it ? Math.round(it.getBoundingClientRect().height) : '-'), 'hover:' + (it ? it.matches(':hover') : '-'),
          'pinb:' + cs(it && it.querySelector('.ldp-pinb'), 'opacity'),
          'dots:' + cs(it && it.querySelector('.ldp-dots'), 'opacity'),
          'mark:' + cs(it && it.querySelector('.ldp-pinmark'), 'visibility'),
          'grip:' + cs(it && it.querySelector('.ldp-grip'), 'display'),
          'ico:' + cs(it && it.querySelector('.ldp-row > .ldp-ico, .ldp-row > .ptile'), 'display'),
          'copy#' + idOf(copy),
          'under:' + (under ? (under.closest('.row-expanded') ? 'copy' : (under.closest('.ldp-pinb') ? 'pinb' : under.closest('.ldp-dots') ? 'dots' : under.closest('.ldp-row') ? 'row' : under.tagName)) : '-'),
        ].join(' ');
        frames.push(f);
        if (performance.now() - t0 < ms) requestAnimationFrame(tick);
        else {
          const changes = []; for (let i = 1; i < frames.length; i++) if (frames[i] !== frames[i - 1]) changes.push(i + ': ' + frames[i - 1] + ' -> ' + frames[i]);
          done({ frames: frames.length, first: frames[0], changes: changes.length, sample: changes.slice(0, 6) });
        }
      };
      requestAnimationFrame(tick);
    }), 0`);
    // `dy`: where on the row, from its middle: 'top' and 'bottom' are 2 px inside its edges at rest.
    const rest = async (pid, sel, dy) => {
      await mouse('mouseMoved', 700, 600); await sleep(300);
      const p = await ev(`(() => { const it = document.querySelector('#ld-projs [data-pin-row=' + JSON.stringify(${JSON.stringify(pid)}) + ']'); const e = it && it.querySelector(${JSON.stringify(sel)}); if (!e) return null; e.scrollIntoView({ block: 'nearest' }); const r = e.getBoundingClientRect(), ri = it.getBoundingClientRect();
        const y = ${JSON.stringify(dy || 'mid')} === 'top' ? ri.top + 2 : ${JSON.stringify(dy || 'mid')} === 'bottom' ? ri.bottom - 2 : r.top + r.height / 2;
        return { x: Math.round(r.left + Math.min(r.width / 2, 40)), y: Math.round(y), restH: Math.round(ri.height) }; })()`);
      if (!p) return { missing: sel };
      // a person's approach: a few moves onto the spot, then the pointer rests
      for (let i = 4; i >= 0; i--) { await mouse('mouseMoved', p.x + i * 6, p.y); await sleep(16); }
      await sleep(600);   // the cut name's copy rises (320 ms) and settles
      return Object.assign({ restH: p.restH }, await ev(`HOVER_REC(${JSON.stringify(pid)}, ${p.x}, ${p.y}, ${A.restMs})`));
    };
    const R = out.rest = {};
    for (const [k, pinned] of [['Atlas', 1], ['Long', 1], ['Harbor', 0], ['Long2', 0]])
      for (const [w, sel, dy] of [['row', '.ldp-name'], ['rowTop', '.ldp-name', 'top'], ['rowBottom', '.ldp-name', 'bottom'],
        ['pin', '.ldp-pinb'], ['pinBottom', '.ldp-pinb', 'bottom'], ['dots', '.ldp-dots'], ['dotsBottom', '.ldp-dots', 'bottom']])
        R[k + '/' + w] = await rest(ID[k], sel, dy);
    out.pinned = await ev(`[...document.querySelectorAll('#ld-projs .ldp-hand')].map(e => e.getAttribute('data-pin-row'))`);
  } finally { try { ch.kill(); } catch (e) {} }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class PinnedRowHoverIsSteady(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pinhover-", ignore_cleanup_errors=True)
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
        ids = {}
        for name in NAMES + [LONG, LONG2]:
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            ids["Long" if name == LONG else "Long2" if name == LONG2 else name] = proj["id"]
            home = Path(proj.get("home") or proj["path"])
            po = chatroom.create_room(f"{name} PO", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
            dashboard.assign_session_project(po["id"], proj["id"])
            ok, why = dashboard.set_project_po(proj["id"], po["id"])
            assert ok, why
            chatroom.post_message(po["id"], "claude", "All on track.", to="user")
        cls.ids = ids
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        script = base / "pinhover_cdp.js"
        # A mouse and hover, as a computer's (headless Chrome reports neither): a cut name's copy shows.
        head = STRIP_JS.split("async function main()")[0].replace(
            "'--window-size=1728,1117', 'about:blank'",
            "'--window-size=1728,1117', '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', 'about:blank'")
        assert "blink-settings" in head
        script.write_text(head + MAIN, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "restMs": REST_MS,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "ids": ids}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=300)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("PINHOVER_DUMP"):
            print(json.dumps(cls.got, indent=1), file=sys.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_both_pinned(self):
        self.assertEqual(sorted(self.got["pinned"]), sorted([self.ids["Atlas"], self.ids["Long"]]), self.got)

    def _steady(self, key):
        r = self.got["rest"][key]
        self.assertNotIn("missing", r, r)
        self.assertGreater(r["frames"], 20, r)
        self.assertEqual(r["changes"], 0, json.dumps(r, indent=1))
        # the row is as tall hovered as at rest (a pinned row's grip took 5 px off it)
        self.assertIn(f"h:{r['restH']} ", r["first"], r)
        return r

    def test_pinned_short_name_is_steady(self):
        for w in WHERE:
            with self.subTest(w):
                self._steady(f"Atlas/{w}")

    def test_cut_names_raise_their_copy(self):
        # the long names are cut: resting on them raises the whole-name copy (#337), which stays up
        for k in ("Long", "Long2"):
            with self.subTest(k):
                r = self.got["rest"][f"{k}/row"]
                self.assertIn("under:copy", r["first"], r)
                self.assertNotIn("copy#0", r["first"], r)

    def test_pinned_cut_name_is_steady(self):
        for w in WHERE:
            with self.subTest(w):
                self._steady(f"Long/{w}")

    def test_unpinned_rows_are_steady(self):
        for k in ("Harbor", "Long2"):
            for w in WHERE:
                with self.subTest(f"{k}/{w}"):
                    self._steady(f"{k}/{w}")

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
