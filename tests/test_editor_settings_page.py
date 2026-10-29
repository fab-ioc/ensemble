"""#286 in a real page: Settings › Editor.

In headless Chrome over CDP, against a hub in a thread (the hub, the page and
the Chrome launch are tests/test_settings_themes_page.py's), with IntelliJ
IDEA and VS Code the only editors "installed" (a fake finder):

* the Editor tab lists Automatic, the editors found, and Other command…,
  and says under the list what Automatic opens;
* choosing IntelliJ IDEA saves it on the hub at once, the note says every
  project opens in it, and the IDE action reads "Open in IntelliJ IDEA";
* Other command… shows the command box, and a command typed there is saved
  with it; Automatic goes back.

Skipped without Node or Chrome.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402
from tests import test_settings_themes_page as browser  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!PREFS && !!EDITORS_INFO', 30000);
    const READ = `(async () => {
      const s = await fetch('/api/settings').then(r => r.json());
      const sel = document.getElementById('pref-editor'), sec = document.getElementById('settings-editor');
      const ide = SessionActions.sessionActions({ kind: 'raw', sessionId: 'x', cwd: 'C:/x' }, actionEnv());
      const all = [].concat(...ide.groups, ide.primary ? [ide.primary] : []);
      const item = all.find(i => i && i.id === 'ide');
      return { hub: { editor: s.editor, editorCommand: s.editorCommand },
               shown: !sec.hidden && sec.getClientRects().length > 0,
               tab: !!document.getElementById('sp-tab-editor'),
               tabText: (document.getElementById('sp-tab-editor') || {}).textContent || '',
               value: sel.value, disabled: sel.disabled,
               options: [...sel.options].map(o => [o.value, o.textContent]),
               label: document.querySelector('label[for="pref-editor"]').textContent,
               cmdShown: !document.getElementById('pref-editor-cmd-row').hidden,
               cmdFocused: document.activeElement === document.getElementById('pref-editor-cmd'),
               note: document.getElementById('pref-editor-note').textContent,
               ide: item ? item.label : null, hubMachine: onHubMachine() }; })()`;
    await evalIn("openSettings('editor'); 0");
    await until("!document.getElementById('settings-editor').hidden && !document.getElementById('pref-editor').disabled");
    out.before = await evalIn(READ);
    const choose = (v) => evalIn(`(() => { const s = document.getElementById('pref-editor'); s.focus(); s.value = ${JSON.stringify(v)}; s.dispatchEvent(new Event('change', { bubbles: true })); return 0; })()`);
    await choose('idea');
    await until(`fetch('/api/settings').then(r => r.json()).then(s => s.editor === 'idea')`);
    await evalIn("document.getElementById('pref-editor').blur(); 0");
    await until(`EDITORS_INFO.editor === 'idea' && PLATFORM.editorName === 'IntelliJ IDEA'`);
    await sleep(100);
    out.idea = await evalIn(READ);
    await choose('custom');
    await sleep(200);
    out.customAsked = await evalIn(READ);
    await evalIn(`(() => { const i = document.getElementById('pref-editor-cmd'); i.value = 'subl'; i.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); return 0; })()`);
    await until(`EDITORS_INFO.editor === 'custom' && EDITORS_INFO.editorCommand === 'subl'`);
    await sleep(100);
    out.custom = await evalIn(READ);
    await choose('');
    await until(`EDITORS_INFO.editor === ''`);
    await evalIn("document.getElementById('pref-editor').blur(); 0");
    await sleep(200);
    out.automatic = await evalIn(READ);
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""

FOUND = {"idea": r"C:\fake\idea\bin\idea64.exe", "code": r"C:\fake\Code\Code.exe"}


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class EditorInSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b = dashboard.BACKEND
        b.__dict__.pop("_editor_found", None)
        cls.addClassCleanup(lambda: b.__dict__.pop("_editor_found", None))
        with mock.patch.object(browser, "CDP_JS", CDP_JS), \
                mock.patch.object(type(b), "_locate_editor", lambda _s, eid: FOUND.get(eid)), \
                mock.patch.object(type(b), "_locate_command", lambda _s, cmd: None), \
                mock.patch.object(type(b), "load_editor_map", lambda _s: {"java": "idea", "python": "pycharm", "default": "code"}):
            browser.SettingsScrollsAndThemeSubmenu.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.SettingsScrollsAndThemeSubmenu.tearDownClass.__func__(cls)

    def test_the_editor_tab_offers_what_is_installed(self):
        g = self.got["before"]
        self.assertTrue(g["tab"] and g["shown"], g)
        self.assertIn("Editor", g["tabText"])
        self.assertEqual(g["label"], "Open in editor opens")
        self.assertEqual(g["options"], [["", "Automatic (from each repo\u2019s language)"],
                                        ["idea", "IntelliJ IDEA"], ["code", "VS Code"],
                                        ["custom", "Other command\u2026"]])
        self.assertEqual((g["value"], g["hub"]["editor"]), ("", ""))
        # Python's PyCharm is missing: IntelliJ IDEA, the first found, opens it.
        self.assertIn("IntelliJ IDEA for Java and Python", g["note"])
        self.assertIn("VS Code for everything else", g["note"])
        self.assertTrue(g["hubMachine"])
        self.assertEqual(g["ide"], "Open in editor", "it depends on the repo")

    def test_choosing_an_editor_saves_it_and_names_it(self):
        g = self.got["idea"]
        self.assertEqual(g["hub"]["editor"], "idea")
        self.assertEqual(g["value"], "idea")
        self.assertEqual(g["note"], "Every project opens in IntelliJ IDEA.")
        self.assertEqual(g["ide"], "Open in IntelliJ IDEA")

    def test_other_command(self):
        asked = self.got["customAsked"]
        self.assertTrue(asked["cmdShown"] and asked["cmdFocused"], asked)
        self.assertEqual(asked["hub"]["editor"], "idea", "nothing saved before a command is typed")
        g = self.got["custom"]
        self.assertEqual(g["hub"], {"editor": "custom", "editorCommand": "subl"})
        self.assertTrue(g["cmdShown"])
        self.assertIn("subl was not found on the hub's computer", g["note"])
        self.assertEqual(g["ide"], "Open in subl")

    def test_back_to_automatic(self):
        g = self.got["automatic"]
        self.assertEqual(g["hub"]["editor"], "")
        self.assertFalse(g["cmdShown"])
        self.assertEqual(g["ide"], "Open in editor")


if __name__ == "__main__":
    unittest.main()
