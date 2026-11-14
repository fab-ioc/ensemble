"""#362: pinned projects in a real browser, against a hub in a thread with 12 projects.

Desktop 1440x900 with a mouse:

* the pin button of a left-list row pins it; the ⋯ menu's "Pin to the top" pins
  another; the switcher's pin button a third; each lands at the bottom of the
  Pinned group, and the hub has the same list;
* a pinned row dragged by the mouse to the top of the group moves there and
  opens no project; Escape mid-drag puts it back; a drag never leaves the group;
* ⋯ on the top pinned row has Move up disabled, and Move down moves it;
* a reload, and a second browser (its own storage), show the same order;
* the switcher lists Pinned in that order, Recent without them; a drag there
  reorders too; the pin button unpins.

Phone 390x844 with touch: the switcher pane's pin button and grip are 44px,
a finger on the grip drags a pinned row to the top, a tap on a pin pins, and
the cards page has the pinned first. No page errors anywhere.
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

NAMES = ["Atlas", "Harbor", "Orchard", "Lumen", "Quarry", "Juniper", "Beacon", "Cobalt", "Fjord",
         "Meadow", "Summit", "Tundra"]
# A name the left list cuts: its row raises a whole copy on hover (#337).
LONG = "A project with a name far too long for the left list"

MAIN = r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const ID = A.ids, NAME = Object.fromEntries(Object.entries(ID).map(([k, v]) => [v, k]));
  const page = async (phone, ctx) => {
    let browserContextId;
    if (ctx) browserContextId = (await c.send('Target.createBrowserContext')).browserContextId;
    const { targetId } = await c.send('Target.createTarget', browserContextId ? { url: 'about:blank', browserContextId } : { url: 'about:blank' });
    const { sessionId: s } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, s); await c.send('Runtime.enable', {}, s);
    if (phone) {
      await c.send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, s);
      await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, s);
    } else {
      await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, s);
    }
    const ev = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, s); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await ev(expr); } catch (e) {} if (v) return v; await sleep(120); } return null; };
    const at = (expr) => ev(`(() => { const e = (${expr}); if (!e) return null; const r = e.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, t: r.top, b: r.bottom, w: r.width, h: r.height }; })()`);
    const mouse = (type, x, y, extra) => c.send('Input.dispatchMouseEvent', Object.assign({ type, x, y }, extra || {}), s);
    const click = async (expr) => {
      const p = await at(expr); if (!p) throw new Error('nothing at ' + expr);
      await mouse('mouseMoved', p.x, p.y); await sleep(60);
      await mouse('mousePressed', p.x, p.y, { button: 'left', buttons: 1, clickCount: 1 });
      await mouse('mouseReleased', p.x, p.y, { button: 'left', buttons: 0, clickCount: 1 });
      return p;
    };
    // A press on `from`, moves in steps to y, then a release (or Escape first).
    const drag = async (from, toY, esc) => {
      const p = await at(from); if (!p) throw new Error('nothing at ' + from);
      await mouse('mouseMoved', p.x, p.y); await sleep(40);
      await mouse('mousePressed', p.x, p.y, { button: 'left', buttons: 1, clickCount: 1 });
      const n = 8;
      for (let i = 1; i <= n; i++) { await mouse('mouseMoved', p.x, p.y + (toY - p.y) * i / n, { button: 'left', buttons: 1 }); await sleep(30); }
      const mid = await ev('({ drag: !!PIN.drag, slot: document.querySelectorAll(".ldp-slot, .pm-slot").length, lift: document.querySelectorAll(".ldp-lifted, .pm-lifted").length })');
      if (esc) {
        await c.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
        await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 }, s);
      }
      await mouse('mouseReleased', p.x, toY, { button: 'left', buttons: 0, clickCount: 1 });
      return mid;
    };
    const touch = (type, x, y) => c.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] }, s);
    const tap = async (expr) => { const p = await at(expr); if (!p) throw new Error('nothing at ' + expr); await touch('touchStart', p.x, p.y); await sleep(40); await touch('touchEnd'); return p; };
    const tdrag = async (from, toY) => {
      const p = await at(from); if (!p) throw new Error('nothing at ' + from);
      await touch('touchStart', p.x, p.y); await sleep(60);
      for (let i = 1; i <= 8; i++) { await touch('touchMove', p.x, p.y + (toY - p.y) * i / 8); await sleep(30); }
      const mid = await ev('({ drag: !!PIN.drag, slot: document.querySelectorAll(".pm-slot").length })');
      await touch('touchEnd'); return mid;
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false; try { if (!sessionStorage.getItem("seeded")) { sessionStorage.setItem("seeded", "1"); for (const k of ["cd-list-dock", "cd-list-dock-a7", "cd-ld-sel", "cd-tool-strip", "cd-tool-open"]) localStorage.removeItem(k); } } catch (e) {}' }, s);
    const load = async () => {
      await c.send('Page.navigate', { url: A.base + '/' }, s);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 10', 30000);
      await until('window.ensBooted === true', 30000);
      if (!phone) {
        await until('!!LD.dock && document.querySelectorAll("#ld-projs .ldp-row[data-ldp-proj]").length >= 5', 30000);
        await ev('(() => { const m = document.querySelector("#ld-projs [data-ldp-more=proj][aria-expanded=false]"); if (m) m.click(); return 0; })()');
      }
      await ev('window.PICKS = []; const _pp = projPick; projPick = function (id) { PICKS.push(id); return _pp.apply(this, arguments); }; 0');
      await sleep(500);
    };
    await load();
    return { ev, until, at, click, drag, tap, tdrag, load, mouse,
      close: async () => { await c.send('Target.closeTarget', { targetId }); if (browserContextId) await c.send('Target.disposeBrowserContext', { browserContextId }); } };
  };
  const names = ids => (ids || []).map(id => NAME[id] || id);
  const L = `[...document.querySelectorAll('#ld-projs .ldp-pins .ldp-hand')].map(e => e.getAttribute('data-pin-row'))`;
  const SW = `[...document.querySelectorAll('#proj-menu .pm-pins .pm-pinrow')].map(e => e.getAttribute('data-pin-row'))`;
  const REC = `[...document.querySelectorAll('#proj-menu .pm-recent [data-pin-row]')].map(e => e.getAttribute('data-pin-row'))`;
  const HUB = `fetch('/api/projects').then(r => r.json()).then(j => j.summary.pinned)`;
  const settle = async p => { await p.until('PIN.busy === 0', 8000); await sleep(150); };
  const row = n => `document.querySelector('#ld-projs .ldp-row[data-ldp-proj="${ID[n]}"]')`;
  const hand = n => `document.querySelector('#ld-projs .ldp-hand[data-pin-row="${ID[n]}"] .ldp-row')`;
  try {
    const d = await page(false);
    const D = out.desk = {};
    D.start = names(await d.ev(L));
    D.restCount = await d.ev(`document.querySelectorAll('#ld-projs .ldp-item[data-pin-row]').length`);
    // 1. the row's pin button (shown on hover)
    const r = await d.at(row('Harbor')); await d.mouse('mouseMoved', r.x, r.y); await sleep(200);
    D.pinbOpacity = await d.ev(`getComputedStyle(document.querySelector('#ld-projs [data-ldp-pin="${ID.Harbor}"]')).opacity`);
    await d.click(`document.querySelector('#ld-projs [data-ldp-pin="${ID.Harbor}"]')`); await settle(d);
    D.afterPin1 = names(await d.ev(L)); D.hub1 = names(await d.ev(HUB));
    D.picks1 = await d.ev('PICKS.length');
    // 2. ⋯ → Pin to the top
    await d.click(`document.querySelector('#ld-projs [data-ldp-pmenu="${ID.Atlas}"]')`); await sleep(250);
    D.menuRest = await d.ev(`[...document.querySelectorAll('.dcm [data-act]')].map(b => b.textContent)`);
    await d.click(`document.querySelector('.dcm [data-act="pin"]')`); await settle(d);
    D.afterPin2 = names(await d.ev(L));
    // 3. the switcher's pin button
    await d.ev('(closeProjMenu(), openProjMenu(false), 0)'); await sleep(300);
    await d.click(`document.querySelector('#proj-menu .pm-row:not(.pm-pinrow) [data-pm-pin="${ID.Lumen}"]')`); await settle(d);
    D.switchOpen = await d.ev(`!document.getElementById('proj-menu').hidden`);
    D.afterPin3 = names(await d.ev(L)); D.sw3 = names(await d.ev(SW)); D.hub3 = names(await d.ev(HUB));
    D.recentHasPinned = (await d.ev(REC)).filter(id => [ID.Harbor, ID.Atlas, ID.Lumen].includes(id)).length;
    await d.ev('(closeProjMenu(), 0)'); await sleep(200);
    // 4. drag Lumen to the top of the group, by the mouse
    const top = await d.at(hand('Harbor'));
    D.dragMid = await d.drag(hand('Lumen'), top.t + 2); await settle(d);
    D.afterDrag = names(await d.ev(L)); D.hubDrag = names(await d.ev(HUB));
    D.picksDrag = await d.ev('PICKS.length'); D.selAfterDrag = await d.ev('SELECTED_PROJECT || ""');
    D.leftovers = await d.ev('document.querySelectorAll(".ldp-slot, .ldp-lifted, .ldp-gone").length + (document.body.classList.contains("pin-dragging") ? 1 : 0)');
    // 5. Escape mid-drag puts it back
    const t2 = await d.at(hand('Lumen'));
    D.escMid = await d.drag(hand('Atlas'), t2.t + 2, true); await settle(d);
    D.afterEsc = names(await d.ev(L)); D.picksEsc = await d.ev('PICKS.length');
    // 6. a drag far below the group stays inside it (the last place)
    const q = await d.at(row('Quarry'));
    await d.drag(hand('Lumen'), q.y + 200); await settle(d);
    D.afterFar = names(await d.ev(L));
    D.restAfterFar = await d.ev(`document.querySelectorAll('#ld-projs .ldp-item[data-pin-row]:not(.ldp-hand)').length`);
    // 7. ⋯ on the top one: Move up disabled, Move down moves it
    const first = (await d.ev(L))[0];
    await d.click(`document.querySelector('#ld-projs [data-ldp-pmenu="${first}"]')`); await sleep(250);
    D.menuTop = await d.ev(`[...document.querySelectorAll('.dcm [data-act]')].map(b => b.textContent + (b.disabled ? ':off' : ''))`);
    await d.click(`document.querySelector('.dcm [data-act="down"]')`); await settle(d);
    D.afterDown = names(await d.ev(L));
    // 8. a plain click on a pinned row still opens its project
    await d.click(hand('Atlas')); await sleep(300);
    D.clickOpens = names(await d.ev('PICKS'));
    // 9. reload keeps the order
    await d.load();
    D.afterReload = names(await d.ev(L));
    D.errorsSoFar = c.errors.length;
    // 10. a second browser, its own storage
    const d2 = await page(false, true);
    D.otherBrowser = names(await d2.ev(L));
    await d2.close();
    // 11. drag in the switcher: the last pinned to the top
    await d.ev('(closeProjMenu(), openProjMenu(false), 0)'); await sleep(300);
    const sw = await d.ev(SW);
    const swTop = await d.at(`document.querySelector('#proj-menu .pm-pinrow[data-pin-row="${sw[0]}"]')`);
    D.swMid = await d.drag(`document.querySelector('#proj-menu .pm-pinrow[data-pin-row="${sw[sw.length - 1]}"] .pm-name')`, swTop.t + 2); await settle(d);
    D.swAfterDrag = names(await d.ev(SW)); D.listAfterSwDrag = names(await d.ev(L));
    D.swStillOpen = await d.ev(`!document.getElementById('proj-menu').hidden`);
    D.picksSw = await d.ev('PICKS.length');
    // 12. the switcher's pin button unpins
    await d.click(`document.querySelector('#proj-menu .pm-pinrow [data-pm-pin="${ID.Harbor}"]')`); await settle(d);
    D.afterUnpin = names(await d.ev(SW)); D.hubUnpin = names(await d.ev(HUB));
    D.unpinFocus = await d.ev('document.activeElement && document.activeElement.getAttribute("data-pm-pin") || ""');
    await d.ev('(closeProjMenu(), 0)');
    D.listAfterUnpin = names(await d.ev(L));
    // 13. a cut name: its raised copy steps aside over the pin and ⋯, which take the click
    const LONG = 'Long';
    const lrow = await d.at(row(LONG));
    const pinAt = await d.at(`document.querySelector('#ld-projs [data-ldp-pin="${ID[LONG]}"]')`);
    const dotsAt = await d.at(`document.querySelector('#ld-projs [data-ldp-pmenu="${ID[LONG]}"]')`);
    const copyUp = 'document.querySelectorAll(".ldp-expanded.row-expanded").length';
    await d.mouse('mouseMoved', lrow.x - 40, lrow.y); await sleep(50); await d.mouse('mouseMoved', lrow.x - 30, lrow.y);
    D.longCopy = !!(await d.until(copyUp + ' > 0', 3000));
    await d.mouse('mouseMoved', pinAt.x - 4, pinAt.y); await sleep(50); await d.mouse('mouseMoved', pinAt.x, pinAt.y); await sleep(250);
    D.longOverPin = { copy: await d.ev(copyUp), opacity: await d.ev(`getComputedStyle(document.querySelector('#ld-projs [data-ldp-pin="${ID[LONG]}"]')).opacity`) };
    await d.mouse('mousePressed', pinAt.x, pinAt.y, { button: 'left', buttons: 1, clickCount: 1 });
    await d.mouse('mouseReleased', pinAt.x, pinAt.y, { button: 'left', buttons: 0, clickCount: 1 }); await settle(d);
    D.longPinned = names(await d.ev(L)); D.longPicks = await d.ev('PICKS.length');
    await d.ev(`fetch('/api/projects/pins', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projectId: ${JSON.stringify(ID[LONG])}, pinned: false }) }).then(r => r.status)`);
    await d.load();
    const lrow2 = await d.at(row(LONG)), dots2 = await d.at(`document.querySelector('#ld-projs [data-ldp-pmenu="${ID[LONG]}"]')`);
    await d.mouse('mouseMoved', lrow2.x - 40, lrow2.y); await sleep(50); await d.mouse('mouseMoved', lrow2.x - 30, lrow2.y);
    await d.until(copyUp + ' > 0', 3000);
    await d.mouse('mouseMoved', dots2.x - 4, dots2.y); await sleep(50); await d.mouse('mouseMoved', dots2.x, dots2.y); await sleep(250);
    await d.mouse('mousePressed', dots2.x, dots2.y, { button: 'left', buttons: 1, clickCount: 1 });
    await d.mouse('mouseReleased', dots2.x, dots2.y, { button: 'left', buttons: 0, clickCount: 1 }); await sleep(300);
    D.longMenu = await d.ev(`[...document.querySelectorAll('.dcm [data-act]')].map(b => b.textContent).slice(0, 2)`);
    D.longMenuPicks = await d.ev('PICKS.length');
    D.dotsAt = !!dotsAt;
    await d.ev('ldpMenuClose(), 0');
    D.listEnd = names(await d.ev(L));
    // 14. overlapping changes: the first post is held 700ms before it goes out,
    // so without one-at-a-time the second would reach the hub first
    await d.ev(`(() => { window.POSTHOLD = []; const f0 = window.fetch; window.fetch = function (u, o) {
      if (String(u).startsWith('/api/projects/pins') && POSTHOLD.length) { const ms = POSTHOLD.shift(); return new Promise(r => setTimeout(r, ms)).then(() => f0.call(this, u, o)); }
      if (String(u).startsWith('/api/projects?') && window.GETHOLD) { const ms = GETHOLD; GETHOLD = 0; return f0.call(this, u, o).then(r => new Promise(k => setTimeout(() => k(r), ms))); }
      return f0.call(this, u, o); }; return 0; })()`);
    const pins = async (...acts) => { await d.ev(`(POSTHOLD = [700, 0], ${acts.join(', ')}, 0)`); await settle(d); return { list: names(await d.ev(L)), hub: names(await d.ev(HUB)) }; };
    D.twoPins = await pins(`pinToggle(${JSON.stringify(ID.Harbor)})`, `pinToggle(${JSON.stringify(ID.Quarry)})`);
    D.pinUnpin = await pins(`pinToggle(${JSON.stringify(ID.Beacon)})`, `pinToggle(${JSON.stringify(ID.Beacon)})`);
    D.pinMove = await pins(`pinToggle(${JSON.stringify(ID.Cobalt)})`, `pinMove(${JSON.stringify(ID.Cobalt)}, -1)`);
    // 15. a projects GET that read the hub before a pin answers after it
    D.staleGet = await d.ev(`(async () => {
      GETHOLD = 900; const g = apiProjects();
      await new Promise(r => setTimeout(r, 250));
      pinToggle(${JSON.stringify(ID.Fjord)});
      await PIN.q; await new Promise(r => setTimeout(r, 50));
      const p = await g; const got = p.summary.pinned.slice();
      PROJECTS = p; pinRepaint();
      return { got, list: pinList() };
    })()`);
    D.staleGet = { got: names(D.staleGet.got), list: names(D.staleGet.list), now: names(await d.ev(L)) };
    for (const n of ['Harbor', 'Quarry', 'Cobalt', 'Fjord'])
      await d.ev(`fetch('/api/projects/pins', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ projectId: ${JSON.stringify(ID[n])}, pinned: false }) }).then(r => r.status)`);
    D.restored = names(await d.ev(HUB));
    await d.close();

    // Phone
    const P = out.phone = {};
    const ph = await page(true);
    await ph.ev('(openProjMenu(false), 0)'); await sleep(500);
    P.pane = await ph.ev(`document.body.classList.contains('ph-switch')`);
    P.sw = names(await ph.ev(SW));
    P.sizes = await ph.ev(`(() => { const b = document.querySelector('#proj-menu .pm-pinrow .pm-pinb'), g = document.querySelector('#proj-menu .pm-pinrow .pm-griph'); const R = e => e ? [Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height), getComputedStyle(e).opacity] : null; return { pin: R(b), grip: R(g), restPin: R(document.querySelector('#proj-menu .pm-row:not(.pm-pinrow) .pm-pinb')) }; })()`);
    const pw = await ph.ev(SW);
    const pTop = await ph.at(`document.querySelector('#proj-menu .pm-pinrow[data-pin-row="${pw[0]}"]')`);
    P.mid = await ph.tdrag(`document.querySelector('#proj-menu .pm-pinrow[data-pin-row="${pw[pw.length - 1]}"] .pm-griph')`, pTop.t + 2); await settle(ph);
    P.afterDrag = names(await ph.ev(SW)); P.hubDrag = names(await ph.ev(HUB));
    P.picks = await ph.ev('PICKS.length');
    // a finger on the row's name (not the grip) starts no drag
    P.nameMid = await ph.tdrag(`document.querySelector('#proj-menu .pm-pinrow[data-pin-row="${pw[0]}"] .pm-name')`, pTop.t + 120); await settle(ph);
    P.afterNameSwipe = names(await ph.ev(SW));
    await ph.ev('(closeProjMenu(), openProjMenu(false), 0)'); await sleep(400);
    await ph.ev(`document.querySelector('#proj-menu .pm-row:not(.pm-pinrow) [data-pm-pin="${ID.Quarry}"]').scrollIntoView({ block: 'center' }), 0`); await sleep(200);
    await ph.tap(`document.querySelector('#proj-menu .pm-row:not(.pm-pinrow) [data-pm-pin="${ID.Quarry}"]')`); await settle(ph);
    P.afterTap = names(await ph.ev(SW)); P.paneOpen = await ph.ev(`!document.getElementById('proj-menu').hidden`);
    await ph.ev('(closeProjMenu(), SELECTED_PROJECT = null, PH_PANE && phPane(""), renderRows(), 0)'); await sleep(400);
    P.cards = names(await ph.until(`(() => { const a = [...document.querySelectorAll('.proj-grid .proj[data-proj]')].map(e => e.dataset.proj); return a.length ? a : null; })()`, 8000)).slice(0, 4);
    P.scrollW = await ph.ev('document.documentElement.scrollWidth');
    await ph.close();
  } finally { try { ch.kill(); } catch (e) {} }
  out.errors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class PinnedProjectsInChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-pins-", ignore_cleanup_errors=True)
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
        for name in NAMES + [LONG]:
            ok, proj, _ = dashboard.register_project(name)
            assert ok, proj
            ids["Long" if name == LONG else name] = proj["id"]
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
        script = base / "pins_cdp.js"
        # A mouse and hover, as a computer's (headless Chrome reports neither): a cut name's copy shows.
        head = STRIP_JS.split("async function main()")[0].replace(
            "'--window-size=1728,1117', 'about:blank'",
            "'--window-size=1728,1117', '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4', 'about:blank'")
        assert "blink-settings" in head
        script.write_text(head + MAIN, encoding="utf-8")
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "ids": ids}
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=500)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_desktop_pin_three_ways(self):
        d = self.got["desk"]
        self.assertEqual(d["start"], [], d)
        self.assertEqual(d["pinbOpacity"], "1", d)
        self.assertEqual(d["afterPin1"], ["Harbor"], d)
        self.assertEqual(d["hub1"], ["Harbor"], d)
        self.assertEqual(d["picks1"], 0, d)
        self.assertIn("Pin to the top", d["menuRest"], d)
        self.assertNotIn("Move up", d["menuRest"], d)
        self.assertEqual(d["afterPin2"], ["Harbor", "Atlas"], d)
        self.assertTrue(d["switchOpen"], d)
        self.assertEqual(d["afterPin3"], ["Harbor", "Atlas", "Lumen"], d)
        self.assertEqual(d["sw3"], ["Harbor", "Atlas", "Lumen"], d)
        self.assertEqual(d["hub3"], ["Harbor", "Atlas", "Lumen"], d)
        self.assertEqual(d["recentHasPinned"], 0, d)

    def test_desktop_drag(self):
        d = self.got["desk"]
        self.assertEqual(d["dragMid"], {"drag": True, "slot": 1, "lift": 1}, d)
        self.assertEqual(d["afterDrag"], ["Lumen", "Harbor", "Atlas"], d)
        self.assertEqual(d["hubDrag"], ["Lumen", "Harbor", "Atlas"], d)
        self.assertEqual(d["picksDrag"], 0, d)
        self.assertEqual(d["leftovers"], 0, d)
        self.assertTrue(d["escMid"]["drag"], d)
        self.assertEqual(d["afterEsc"], ["Lumen", "Harbor", "Atlas"], d)
        self.assertEqual(d["picksEsc"], 0, d)
        self.assertEqual(d["afterFar"], ["Harbor", "Atlas", "Lumen"], d)
        self.assertEqual(d["restAfterFar"], len(self.ids) - 3, d)

    def test_cut_name_copy_lets_pin_and_dots_take_the_click(self):
        d = self.got["desk"]
        self.assertTrue(d["longCopy"], d)
        self.assertEqual(d["longOverPin"], {"copy": 0, "opacity": "1"}, d)
        self.assertEqual(d["longPinned"], ["Lumen", "Atlas", "Long"], d)
        self.assertEqual(d["longPicks"], 0, d)
        self.assertEqual(d["longMenu"], ["Open", "Pin to the top"], d)
        self.assertEqual(d["longMenuPicks"], 0, d)
        self.assertEqual(d["listEnd"], ["Lumen", "Atlas"], d)

    def test_desktop_menu_moves_and_click_opens(self):
        d = self.got["desk"]
        self.assertIn("Move up:off", d["menuTop"], d)
        self.assertIn("Move down", d["menuTop"], d)
        self.assertIn("Unpin", d["menuTop"], d)
        self.assertEqual(d["afterDown"], ["Atlas", "Harbor", "Lumen"], d)
        self.assertEqual(d["clickOpens"], ["Atlas"], d)

    def test_order_survives_reload_and_other_browser(self):
        d = self.got["desk"]
        self.assertEqual(d["afterReload"], ["Atlas", "Harbor", "Lumen"], d)
        self.assertEqual(d["otherBrowser"], ["Atlas", "Harbor", "Lumen"], d)

    def test_switcher_drag_and_unpin(self):
        d = self.got["desk"]
        self.assertTrue(d["swMid"]["drag"], d)
        self.assertEqual(d["swAfterDrag"], ["Lumen", "Atlas", "Harbor"], d)
        self.assertEqual(d["listAfterSwDrag"], ["Lumen", "Atlas", "Harbor"], d)
        self.assertTrue(d["swStillOpen"], d)
        self.assertEqual(d["picksSw"], 0, d)
        self.assertEqual(d["afterUnpin"], ["Lumen", "Atlas"], d)
        self.assertEqual(d["hubUnpin"], ["Lumen", "Atlas"], d)
        self.assertEqual(d["listAfterUnpin"], ["Lumen", "Atlas"], d)

    def test_overlapping_changes_land_in_order(self):
        d = self.got["desk"]
        self.assertEqual(d["twoPins"], {"list": ["Lumen", "Atlas", "Harbor", "Quarry"], "hub": ["Lumen", "Atlas", "Harbor", "Quarry"]}, d)
        self.assertEqual(d["pinUnpin"], {"list": ["Lumen", "Atlas", "Harbor", "Quarry"], "hub": ["Lumen", "Atlas", "Harbor", "Quarry"]}, d)
        want = ["Lumen", "Atlas", "Harbor", "Cobalt", "Quarry"]
        self.assertEqual(d["pinMove"], {"list": want, "hub": want}, d)
        self.assertEqual(d["restored"], ["Lumen", "Atlas"], d)

    def test_a_get_from_before_a_pin_does_not_undo_it(self):
        d = self.got["desk"]
        want = ["Lumen", "Atlas", "Harbor", "Cobalt", "Quarry", "Fjord"]
        self.assertEqual(d["staleGet"], {"got": want, "list": want, "now": want}, d)

    def test_phone(self):
        p = self.got["phone"]
        self.assertTrue(p["pane"], p)
        self.assertEqual(p["sw"], ["Lumen", "Atlas"], p)
        self.assertGreaterEqual(p["sizes"]["pin"][0], 44, p)
        self.assertGreaterEqual(p["sizes"]["pin"][1], 44, p)
        self.assertEqual(p["sizes"]["pin"][2], "1", p)
        self.assertEqual(p["sizes"]["restPin"][2], "1", p)
        self.assertGreaterEqual(p["sizes"]["grip"][0], 44, p)
        self.assertTrue(p["mid"]["drag"], p)
        self.assertEqual(p["afterDrag"], ["Atlas", "Lumen"], p)
        self.assertEqual(p["hubDrag"], ["Atlas", "Lumen"], p)
        self.assertEqual(p["picks"], 0, p)
        self.assertFalse(p["nameMid"]["drag"], p)
        self.assertEqual(p["afterNameSwipe"], ["Atlas", "Lumen"], p)
        self.assertEqual(p["afterTap"], ["Atlas", "Lumen", "Quarry"], p)
        self.assertTrue(p["paneOpen"], p)
        self.assertEqual(p["cards"][:3], ["Atlas", "Lumen", "Quarry"], p)
        self.assertLessEqual(p["scrollW"], 390, p)

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
