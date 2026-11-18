"""#389 (GitHub issue 55): a PO shows its Roadmap where a task shows its Spec.

In headless Chrome over CDP, against the hub test_tool_strip.py starts
(Motors: a PO and a task with a spec; no ROADMAP.md at first):

* the fifth tool reads Roadmap while the PO is in front (title, aria-label,
  tooltip, its own icon) and Spec while a task is; a PO has no Spec;
* the Roadmap panel with no ROADMAP.md: plain words and Write the roadmap;
* ROADMAP.md written or changed on disk shows in the open panel within a few
  seconds, as the Spec follows a spec change;
* Edit: the text above its preview, "editing · unsaved changes", Save writes
  the file;
* a task in front hides the roadmap and shows its spec; closed, the Roadmap is
  back;
* Files › Documents › Roadmap opens the tool, never a Files tab;
* a phone's last tab reads Roadmap or Spec likewise.

Screenshots go to $ENSEMBLE_SHOTS when it is set. Skipped without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

from tests import chrome_profile
from tests.test_tool_strip import CDP_JS as STRIP_JS, CHROME, NODE, start_hub

# The strip test's launcher, CDP client and page helper; our own main().
HEAD = STRIP_JS[:STRIP_JS.index("async function main() {")]

CDP_JS = HEAD + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.task) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    return { evalIn, until, shot, click, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const go = p => p.evalIn(`(() => { try { ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs', 'cd-ws-panels', 'cd-chat-panels'].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    SELECTED_PROJECT = ${JSON.stringify(A.proj)}; PROJECT_TAB = 'tasks'; renderRows(); return 0; })()`);
  const poReady = p => p.until('document.body.classList.contains("po-dock") && !!PD.dock && !!document.querySelector("#po-dock .dk-strip-btn")', 30000);
  const sid = `ALL_ROWS.find(r => r.roomId === ${JSON.stringify(A.task)}).sessionId`;
  const tool = '#po-dock .dk-strip-btn[data-dk-auto="spec"]';
  const SLOT = `(() => { const b = document.querySelector('${tool}'), rm = document.getElementById('rm-panel');
    const shown = s => { const e = b && b.querySelector(s); return !!e && getComputedStyle(e).display !== 'none'; };
    return { title: PD.dock.title('spec'), label: b ? b.getAttribute('aria-label') : '', tip: b ? b.title : '',
      roadmapIcon: shown('.ti-roadmap'), specIcon: shown('.ti-spec'),
      rmInSpec: !!rm && rm.parentNode === PD.els.spec, rmShown: !!rm && getComputedStyle(rm).display !== 'none',
      tool: !!rm && rm.classList.contains('rm-tool'), text: PD.els.spec.innerText.slice(0, 600),
      crumbs: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].map(e => e.textContent) }; })()`;
  try {
    const p = await page(1440, 900);
    await go(p); await poReady(p); await sleep(500);
    out.po = await p.evalIn(SLOT);
    const file = await p.evalIn(`api('/api/roadmap?project=' + encodeURIComponent(${JSON.stringify(A.proj)})).then(j => j.path)`);
    out.file = file;
    // The tool open, no ROADMAP.md yet.
    await p.click(tool);
    await p.until('PD.dock.flyOpen() === "spec" && /No roadmap yet/.test(PD.els.spec.innerText)', 15000);
    out.empty = { ...(await p.evalIn(SLOT)), write: await p.evalIn(`!!PD.els.spec.querySelector('#rm-panel .rm-empty button[data-rm="edit"].primary')`),
      edit: await p.evalIn(`!!PD.els.spec.querySelector('#rm-panel .rm-acts button')`) };
    await p.shot('roadmap-1440-empty');
    // Written on disk (as the PO does through MCP): the open panel follows.
    let t0 = Date.now();
    fs.writeFileSync(file, '# Motors roadmap\n\n## Now\n\n- **Quiet brakes** first.\n', 'utf-8');
    await p.until('/Quiet brakes/.test(PD.els.spec.innerText)', 15000);
    out.followMs = Date.now() - t0;
    out.view = await p.evalIn(`(() => { const rm = document.getElementById('rm-panel');
      return { meta: rm.querySelector('.rm-meta').textContent, file: rm.querySelector('.rm-file').textContent, path: !!rm.querySelector('.rm-path'),
        block: !!rm.querySelector('.rm-doc.dp-spec strong'), edit: [...rm.querySelectorAll('.rm-acts button')].map(b => b.textContent),
        pdScroll: getComputedStyle(PD.els.spec).overflowY, rmH: Math.round(rm.querySelector('.rm').getBoundingClientRect().height) }; })()`);
    await p.shot('roadmap-1440-view');
    t0 = Date.now();
    fs.writeFileSync(file, '# Motors roadmap\n\n## Now\n\n- **Second version** on disk.\n', 'utf-8');
    await p.until('/Second version/.test(PD.els.spec.innerText)', 15000);
    out.followMs2 = Date.now() - t0;
    // Edit: the text above its preview; a change is unsaved until Save.
    await p.click('#rm-panel button[data-rm="edit"]');
    await p.until('!!document.querySelector("#rm-panel textarea.rm-src")');
    out.editing = await p.evalIn(`(() => { const rm = document.getElementById('rm-panel'), ta = rm.querySelector('.rm-src'), pl = rm.querySelector('.rm-plabel'), doc = rm.querySelector('.rm-doc');
      return { meta: rm.querySelector('.rm-meta').textContent, acts: [...rm.querySelectorAll('.rm-acts button')].map(b => b.textContent), label: pl.textContent,
        stacked: ta.getBoundingClientRect().bottom <= pl.getBoundingClientRect().top && pl.getBoundingClientRect().bottom <= doc.getBoundingClientRect().top,
        within: ta.getBoundingClientRect().right <= PD.els.spec.getBoundingClientRect().right, taH: Math.round(ta.getBoundingClientRect().height) }; })()`);
    await p.evalIn(`(() => { const ta = document.querySelector('#rm-panel .rm-src'); ta.value += '- Then the **drawing**.\\n'; ta.dispatchEvent(new Event('input')); return 0; })()`);
    await p.until('/drawing/.test(document.querySelector("#rm-panel .rm-doc").innerText)', 5000);
    out.dirty = await p.evalIn(`(() => { const rm = document.getElementById('rm-panel'); return { meta: rm.querySelector('.rm-meta').textContent, acts: [...rm.querySelectorAll('.rm-acts button')].map(b => b.textContent) }; })()`);
    await p.shot('roadmap-1440-edit');
    await p.click('#rm-panel button[data-rm="save"]');
    await p.until('!document.querySelector("#rm-panel textarea.rm-src") && /drawing/.test(PD.els.spec.innerText)', 10000);
    out.saved = fs.readFileSync(file, 'utf-8');
    // A task in front: its Spec, the roadmap out of sight; closed, the Roadmap again.
    await p.evalIn(`openDetail(${sid}); 0`);
    await p.until('document.body.classList.contains("dp-docked") && document.body.classList.contains("pd-task-front")', 20000);
    if (await p.evalIn('PD.dock.flyOpen()') !== 'spec') await p.click(tool);
    await p.until('/Quiet brakes on a long descent/.test(PD.els.spec.innerText)', 15000);
    out.task = await p.evalIn(SLOT);
    await p.shot('roadmap-1440-task-spec');
    await p.evalIn('closeDetail(); 0');
    await p.until('!document.body.classList.contains("pd-task-front")', 10000);
    await sleep(300);
    out.back = await p.evalIn(SLOT);
    // Files › Documents › Roadmap opens the tool.
    await p.evalIn('PD.dock.closeFly(); 0'); await sleep(200);
    await p.click('#po-dock .dk-strip-btn[data-dk-auto="workspace"]');
    await p.until('!!PD.els.workspace.querySelector(".wse.doc[data-roadmap]")', 20000);
    await p.evalIn('PD.els.workspace.querySelector(".wse.doc[data-roadmap]").click(); 0');
    await p.until('PD.dock.flyOpen() === "spec"', 10000);
    out.docsRow = { fly: await p.evalIn('PD.dock.flyOpen()'), tabs: await p.evalIn('PD.els.workspace.querySelectorAll(".wst-tab").length'),
      shown: (await p.evalIn(SLOT)).rmShown };
    // An old link to the roadmap's path (Go to file, a chat link) routes the same way.
    await p.evalIn('PD.dock.closeFly(); 0'); await sleep(200);
    await p.evalIn(`(() => { const v = [...WS_VIEWS.values()].find(x => x.ctx.kind === 'project' && x.ctx.projectId === ${JSON.stringify(A.proj)}); wsOpenTabAt(v, ${JSON.stringify(file)}); return 0; })()`);
    await p.until('PD.dock.flyOpen() === "spec"', 10000);
    out.link = { fly: await p.evalIn('PD.dock.flyOpen()'), tabs: await p.evalIn('PD.els.workspace.querySelectorAll(".wst-tab").length') };
    await p.close();
    // A phone: the last tab reads Roadmap for the PO, Spec for a task.
    const q = await page(390, 844, true);
    await go(q); await q.until('document.body.classList.contains("po-dock") && !!PD.dock', 30000); await sleep(600);
    const TABS = `[...document.querySelectorAll('#po-dock .dk-tab')].map(t => [t.dataset.dkTab, t.textContent.trim()])`;
    out.phonePo = await q.evalIn(TABS);
    await q.shot('roadmap-390-po');
    await q.evalIn(`openDetail(${sid}); 0`);
    await q.until('document.body.classList.contains("pd-task-front")', 20000); await sleep(400);
    out.phoneTask = await q.evalIn(TABS);
    await q.shot('roadmap-390-task');
    await q.close();
  } finally {
    out.errors = c.errors.map(e => JSON.stringify(e).slice(0, 300));
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class TheRoadmapTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_hub(cls, prefix="ens-roadmap-")
        base = Path(cls.tmp.name)
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "proj": cls.proj, "task": cls.task, "shots": shots}
        script = base / "roadmap_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=400)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_the_po_s_fifth_tool_is_its_roadmap(self):
        po = self.got["po"]
        self.assertEqual((po["title"], po["label"], po["tip"]), ("Roadmap", "Roadmap", "Roadmap"))
        self.assertTrue(po["roadmapIcon"], "the route-to-a-flag icon")
        self.assertFalse(po["specIcon"], "not the Spec's")
        self.assertTrue(po["rmInSpec"] and po["tool"], "the roadmap lives in that panel, in its tool form")
        self.assertNotIn("A PO has no spec", po["text"])

    def test_no_roadmap_yet_is_plain_words_and_one_button(self):
        e = self.got["empty"]
        self.assertIn("No roadmap yet", e["text"])
        self.assertIn("you can write or change it here", e["text"])
        self.assertNotIn("The file is created", e["text"])
        self.assertTrue(e["write"], "Write the roadmap, primary")
        self.assertFalse(e["edit"], "no Edit for a file that is not there")

    def test_the_open_panel_follows_roadmap_md(self):
        self.assertLess(self.got["followMs"], 8000)
        self.assertLess(self.got["followMs2"], 8000)
        v = self.got["view"]
        self.assertEqual(v["file"], "ROADMAP.md")
        self.assertRegex(v["meta"], r"^saved .+ ago$")
        self.assertFalse(v["path"], "no path in the quiet line")
        self.assertTrue(v["block"], "rendered in the Spec's block")
        self.assertEqual(v["edit"], ["Edit"])
        self.assertEqual(v["pdScroll"], "auto", "the panel scrolls, as the Spec's does")

    def test_edit_stacks_the_text_over_its_preview_and_saves(self):
        e = self.got["editing"]
        self.assertEqual(e["meta"], "editing")
        self.assertEqual(e["acts"], ["Cancel", "Save"])
        self.assertTrue(e["label"].startswith("Preview"))
        self.assertTrue(e["stacked"] and e["within"], e)
        self.assertGreaterEqual(e["taH"], 240)
        d = self.got["dirty"]
        self.assertEqual(d["meta"], "editing · unsaved changes")
        self.assertEqual(d["acts"], ["Discard changes", "Save"])
        self.assertIn("- Then the **drawing**.", self.got["saved"])

    def test_a_task_in_front_shows_its_spec_then_the_roadmap_again(self):
        t = self.got["task"]
        self.assertEqual((t["title"], t["label"]), ("Spec", "Spec"))
        self.assertTrue(t["specIcon"] and not t["roadmapIcon"])
        self.assertFalse(t["rmShown"], "the roadmap out of sight")
        self.assertNotIn("Motors roadmap", t["text"])
        b = self.got["back"]
        self.assertEqual((b["title"], b["label"]), ("Roadmap", "Roadmap"))
        self.assertTrue(b["rmShown"] and b["rmInSpec"])

    def test_documents_and_old_links_open_the_tool(self):
        self.assertEqual(self.got["docsRow"], {"fly": "spec", "tabs": 0, "shown": True})
        self.assertEqual(self.got["link"], {"fly": "spec", "tabs": 0})

    def test_a_phone_s_last_tab_reads_roadmap_or_spec(self):
        po = dict(self.got["phonePo"])
        task = dict(self.got["phoneTask"])
        self.assertEqual(po.get("spec"), "Roadmap", self.got["phonePo"])
        self.assertEqual(task.get("spec"), "Spec", self.got["phoneTask"])

    def test_no_page_errors(self):
        self.assertEqual(self.got["errors"], [])


if __name__ == "__main__":
    unittest.main()
