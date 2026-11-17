"""#384 (CEO point P371): documents are visual first.

* The rule is in the skill (the task owner's Documents section, the PO's
  documents paragraph, planning) and in every hub prompt that says what a
  document is: the owner's launch briefing and the solo note (DOCUMENTS_NOTE)
  and the PO's charter. It reads as a file in Documents, never a claude.ai
  Artifact (P171 stays).
* The file view shows an ``.html`` document from the Documents folder as the
  page, in a frame sandboxed without scripts, and its Source view as text
  (Chrome, on an isolated hub). With ENSEMBLE_SHOTS set it saves the view;
  VISUAL_FIRST_PAGE names another page to show instead of the fixture.
"""
import json
import os
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import dashboard
import file_refs
from tests import chrome_profile
from tests.test_task_numbers import Hub
from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, NODE, CHROME

SKILL = (Path(__file__).resolve().parent.parent / "skills" / "ensemble" / "SKILL.md").read_text(encoding="utf-8")


class ThePrompts(unittest.TestCase):
    def assert_rule(self, text, where):
        for words in ("Prefer a visual", "HTML page, one self-contained static `.html` file in the Documents folder",
                      "inline SVG", "no scripts, no external loads, light theme, readable at phone width",
                      "is Markdown (`.md`)", "A plan, whenever one is asked for, comes with such a page",
                      "never replace it", "ONE side-by-side PNG"):
            self.assertIn(words, text, f"{where}: {words}")

    def test_the_owner_note_names_both_kinds_and_keeps_p171(self):
        n = dashboard.DOCUMENTS_NOTE
        self.assert_rule(n, "DOCUMENTS_NOTE")
        self.assertIn("`#<task no> <short title>.html` (a page, for anything that can be shown) or `.md` (text)", n)
        self.assertIn(dashboard.NO_WEB_ARTIFACT_NOTE, n, "never claude.ai, as before")
        self.assertLess(n.index("Prefer a visual"), n.index("Never publish"))
        self.assertNotIn("Artifact tool", dashboard.VISUAL_FIRST_NOTE, "nothing that reads as publishing")

    def test_the_owner_briefing_and_the_solo_note_carry_it(self):
        brief = dashboard.collab_briefing("claude", "engineer", [{"identity": "codex", "role": "reviewer"}], "Do it")
        self.assertIn(dashboard.DOCUMENTS_NOTE, brief)
        self.assertIn(dashboard.DOCUMENTS_NOTE, dashboard.SOLO_REPORT_NOTE)
        rev = dashboard.collab_briefing("codex", "reviewer", [{"identity": "claude", "role": "engineer"}], "Do it")
        self.assertNotIn(dashboard.VISUAL_FIRST_NOTE, rev, "a reviewer writes no documents")

    def test_the_po_charter_carries_it(self):
        for kind in ("code", "documents"):
            with self.subTest(kind), mock.patch.object(dashboard, "project_home", lambda p, create=False: "/h"):
                ch = dashboard.made_po_charter({"id": "p1", "name": "P", "kind": kind, "path": "/c"})
                self.assert_rule(ch, kind)
                self.assertIn(dashboard.NO_WEB_ARTIFACT_NOTE, ch)

    def test_the_skill(self):
        i = SKILL.index("**Visual first: an HTML page for what can be shown, Markdown for text**")
        rule = SKILL[i:SKILL.index("**Never publish to claude.ai**", i)]
        for words in ("Prefer a visual", "**HTML page**", "`#<no> <short title>.html`", "inline SVG for diagrams",
                      "no scripts, no external loads", "light theme, readable at phone width",
                      "never a claude.ai Artifact", "**Markdown** (`.md`)", "**A plan, whenever one is asked for**",
                      "it never replaces it", "by its name", "ONE side-by-side PNG on the ask card"):
            self.assertIn(words, rule, words)
        self.assertIn("`#<no> <short title>.html` for a page or `.md` for text (see *Visual\nfirst* below)", SKILL)
        self.assertIn("as `PO <short title>.html` for anything that\ncan be shown", SKILL, "the PO's own documents")
        plan = SKILL[SKILL.index("## Planning work for a project"):SKILL.index("## Writing a good spec")]
        self.assertIn("with a visual page in the Documents folder", plan)
        self.assertIn("**Never publish to claude.ai** (the CEO's rule, P171", SKILL, "P171 is unchanged")


PLAN = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plan</title>
<style>body{font:14px/1.45 system-ui,sans-serif;color:#1f2328;background:#fff;margin:16px}
h1{font-size:20px;margin:0 0 8px}svg{max-width:100%;height:auto}</style></head>
<body><h1>#384 Visual first: the plan</h1>
<svg viewBox="0 0 360 80" role="img" aria-label="Three steps"><rect x="4" y="20" width="100" height="40" rx="6" fill="#dafbe1" stroke="#1a7f37"/>
<text x="54" y="45" text-anchor="middle">Rule</text><rect x="130" y="20" width="100" height="40" rx="6" fill="#dafbe1" stroke="#1a7f37"/>
<text x="180" y="45" text-anchor="middle">File view</text><rect x="256" y="20" width="100" height="40" rx="6" fill="#fff8c5" stroke="#9a6700"/>
<text x="306" y="45" text-anchor="middle">Review</text></svg>
</body></html>
"""
# What a page's script would do, were it run: none of it may happen.
SCRIPT = ("<script>document.title = 'ran'; window.RAN = 1; "
          "document.body.appendChild(document.createElement('blink')).id = 'ran';</script>")

CDP_JS = TASK_CDP.split('// The chips in the conversation')[0] + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [width, height, mob] of [[1280, 800, false], [390, 844, true]]) {
      for (const [mode, url] of Object.entries(A.urls)) {
        const {targetId} = await c.send('Target.createTarget', {url: 'about:blank'});
        const {sessionId: s} = await c.send('Target.attachToTarget', {targetId, flatten: true});
        await c.send('Page.enable', {}, s);
        await c.send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: mob}, s);
        const ev = async e => { const r = await c.send('Runtime.evaluate', {expression: e, awaitPromise: true, returnByValue: true}, s);
          if (r.exceptionDetails) throw Error(e.slice(0, 80) + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
        const until = async (e, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { try { const v = await ev(e); if (v) return v; } catch (x) {} await sleep(150); } throw Error('timeout ' + e.slice(0, 200)); };
        await c.send('Page.navigate', {url: A.base + url}, s);
        await until(`document.readyState === 'complete' && (document.querySelector('main .code') || (() => { const f = document.querySelector('iframe.file');
          return f && f.contentDocument && f.contentDocument.readyState === 'complete' && f.contentDocument.querySelector('h1'); })())`);
        await sleep(400);
        out[width + ' ' + mode] = await ev(`(() => { const f = document.querySelector('iframe.file'), code = document.querySelector('main .code');
          const views = [...document.querySelectorAll('button')].filter(b => /^(Preview|Source)$/.test(b.textContent.trim()) && !b.hidden && b.offsetParent)
            .map(b => [b.textContent.trim(), b.getAttribute('aria-pressed') === 'true' || b.classList.contains('on')]);
          const r = {views, overflow: document.documentElement.scrollWidth > innerWidth};
          if (code) { r.source = code.textContent.includes('<script>document.title') && code.textContent.includes('<svg'); r.frame = !!f; return r; }
          const d = f.contentDocument;
          return {...r, sandbox: f.getAttribute('sandbox'), srcdoc: !!f.srcdoc, title: d.title, h1: d.querySelector('h1').textContent,
                  svg: d.querySelectorAll('svg rect').length, ran: !!d.getElementById('ran') || !!f.contentWindow.RAN}; })()`);
        if (A.shots && mode === 'page' && !mob) { const r = await c.send('Page.captureScreenshot', {format: 'png'}, s);
          fs.writeFileSync(path.join(A.shots, 'visual-first-fileview.png'), Buffer.from(r.data, 'base64')); }
        await c.send('Target.closeTarget', {targetId});
      }
    }
  } finally { try { await c.send('Browser.close'); } catch (e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e.stack || e); process.exit(1); });
'''


@unittest.skipUnless(NODE and CHROME, 'node and Chrome required')
class APageInTheFileView(Hub):
    def test_a_documents_page_is_shown_rendered_without_its_script(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        file_refs.forget_indexes(); self.addCleanup(file_refs.forget_indexes)
        docs = Path(dashboard.find_project(self.ed)['path']) / 'Documents'
        docs.mkdir(parents=True, exist_ok=True)
        other = os.environ.get('VISUAL_FIRST_PAGE', '')
        html = Path(other).read_text(encoding='utf-8') if other else PLAN
        page = docs / '#384 Visual first.html'
        page.write_text(html.replace('</body>', SCRIPT + '</body>', 1), encoding='utf-8')
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        at = '/fileview?path=' + quote(str(page), safe='')
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': 'http://127.0.0.1:' + str(server.server_port),
                'urls': {'page': at, 'source': at + '&st=' + quote('{"view":"source"}', safe='')}, 'shots': shots}
        script = base / 'visual_first_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(len(got), 4)
        for key, v in got.items():
            with self.subTest(key):
                self.assertFalse(v['overflow'], v)
                if key.endswith('page'):
                    self.assertEqual((v['sandbox'], v['srcdoc'], v['ran']), ('allow-same-origin', True, False),
                                     'rendered in a frame without scripts, and its script did not run')
                    self.assertNotEqual(v['title'], 'ran', v)
                    if not other:
                        self.assertEqual((v['title'], v['h1'], v['svg']), ('Plan', '#384 Visual first: the plan', 3), v)
                else:
                    self.assertEqual((v['source'], v['frame']), (True, False), 'Source shows the text, not the page')
                self.assertEqual([n for n, _ in v['views']], ['Preview', 'Source'], 'one click between the two')


if __name__ == '__main__':
    unittest.main()
