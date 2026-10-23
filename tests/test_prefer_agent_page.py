"""#250 in a real page: Settings › Agent models › Prefer, at 1728 and 390 px,
light and dark.

In headless Chrome over CDP, against a hub in a thread (the hub, the page and
the Chrome launch are tests/test_settings_themes_page.py's):

* the Prefer row heads the section: three buttons, Claude / Codex / Balanced,
  Balanced pressed on a fresh hub, inside the panel and the screen, finger-sized
  on a phone, their text readable in light and dark;
* a click saves the choice on the hub at once and the usage reading follows:
  the preferred kind is underlined on the plan chip and named in its tray, and
  the week's pace lines are not shown while a kind is preferred.

Skipped without Node or Chrome. With ENSEMBLE_SHOTS set to a folder, a picture
of each width and theme is left there.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import usage  # noqa: E402
from tests import test_settings_themes_page as browser  # noqa: E402
from tests.test_agent_models import _pool_source  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [name, w, h, mobile] of [['desktop', 1728, 1000, false], ['phone', 390, 844, true]]) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
      if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!PREFS && !!USAGE && !!USAGE.pace', 30000);
      const o = out[name] = { vw: w, vh: h, themes: {} };
      await evalIn('document.getElementById("me-btn").click(); 0');
      await evalIn('[...document.querySelectorAll("#me-menu .me-item")].find(b => /^Settings/.test(b.textContent)).click(); 0');
      await until('!document.getElementById("settings-panel").hidden');
      const MEASURE = `(() => { openSettings('models'); const p = document.querySelector('#settings-panel .sp-dlg'), sec = document.getElementById('settings-models');
        sec.scrollIntoView({ block: 'start' });
        const seg = document.getElementById('pref-prefer'), pr = p.getBoundingClientRect();
        const firstRow = sec.querySelector('.pref-row');
        return { first: firstRow.contains(seg), label: document.getElementById(seg.getAttribute('aria-labelledby')).textContent,
                 panel: { l: pr.left, r: pr.right, scroll: p.scrollWidth, client: p.clientWidth }, panelBg: getComputedStyle(p).backgroundColor,
                 buttons: [...seg.querySelectorAll('button')].map(b => { const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
                   return { val: b.dataset.val, text: b.textContent, active: b.classList.contains('active'), pressed: b.getAttribute('aria-pressed'),
                            l: r.left, r: r.right, h: r.height, fg: cs.color, bg: cs.backgroundColor }; }),
                 note: document.getElementById('pref-prefer-note').textContent }; })()`;
      for (const theme of ['light', 'dark']) {
        await evalIn(`document.documentElement.dataset.theme = '${theme}'; 0`);
        o.themes[theme] = await evalIn(MEASURE);
        if (A.shots) { const shot = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, 'prefer-' + name + '-' + theme + '.png'), Buffer.from(shot.data, 'base64')); }
      }
      await evalIn(`document.documentElement.dataset.theme = 'light'; 0`);
      const click = (val) => evalIn(`document.querySelector('#pref-prefer button[data-val="${val}"]').click(); 0`);
      const hub = () => evalIn(`fetch('/api/settings').then(r => r.json()).then(s => s.preferAgent)`);
      const shown = () => evalIn(`(() => { const codex = USAGE.sources.find(s => s.source === 'codex');
        return { prefer: USAGE.pace.prefer, active: document.querySelector('#pref-prefer .active').dataset.val,
                 tray: usageSourceHtml(codex).replace(/\\s+/g, ' '), chip: usageChipPart(codex),
                 pace: usagePaceOf(codex) }; })()`);
      await click('codex');
      await until(`USAGE.pace.prefer === 'codex'`);
      o.codex = { hub: await hub(), ...(await shown()) };
      await click('claude');
      await until(`USAGE.pace.prefer === 'claude'`);
      o.claude = { hub: await hub(), ...(await shown()) };
      await click('balanced');
      await until(`USAGE.pace.prefer === ''`);
      o.balanced = { hub: await hub(), ...(await shown()) };
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _luminance(rgb: str) -> float:
    channels = [int(x) / 255 for x in re.findall(r"\d+", rgb)[:3]]
    channels = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
    return sum(x * w for x, w in zip(channels, [.2126, .7152, .0722]))


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + .05) / (lb + .05)


def _opaque(bg: str, panel: str) -> str:
    """A transparent button shows the panel behind it."""
    return panel if bg in ("rgba(0, 0, 0, 0)", "transparent") else bg


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class PreferInSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = usage._mark_pool_in_use(_pool_source(), "gpt-6-astra", "")
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        script = CDP_JS.replace("A.shots", repr(shots.replace("\\", "/")) if shots else "''")
        with mock.patch.object(browser, "CDP_JS", script), \
                mock.patch.dict(usage._STATE, {"state": "ready", "checkedAt": 1.0,
                                               "sources": {"codex": source}}):
            browser.SettingsScrollsAndThemeSubmenu.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.SettingsScrollsAndThemeSubmenu.tearDownClass.__func__(cls)

    def test_the_row_heads_the_section_and_fits(self):
        for name, g in self.got.items():
            for theme, m in g["themes"].items():
                with self.subTest(name, theme=theme):
                    self.assertTrue(m["first"])
                    self.assertEqual(m["label"], "Prefer")
                    self.assertEqual([(b["val"], b["text"]) for b in m["buttons"]],
                                     [("claude", "Claude"), ("codex", "Codex"),
                                      ("balanced", "Balanced")])
                    self.assertEqual([b["active"] for b in m["buttons"]], [False, False, True])
                    self.assertEqual(m["buttons"][2]["pressed"], "true")
                    self.assertLessEqual(m["panel"]["scroll"], m["panel"]["client"] + 1)
                    for b in m["buttons"]:
                        self.assertGreaterEqual(b["l"], m["panel"]["l"], b)
                        self.assertLessEqual(b["r"], min(m["panel"]["r"], g["vw"]), b)
                        if name == "phone":
                            self.assertGreaterEqual(b["h"], 44, "finger-sized")
                        self.assertGreaterEqual(
                            _contrast(b["fg"], _opaque(b["bg"], m["panelBg"])), 4.5, b)
                    self.assertIn("used only while it is spent", m["note"])

    def test_a_click_saves_and_the_usage_reading_follows(self):
        for name, g in self.got.items():
            with self.subTest(name):
                codex, claude, balanced = g["codex"], g["claude"], g["balanced"]
                self.assertEqual((codex["hub"], codex["active"]), ("codex", "codex"))
                self.assertIn("preferred by Settings", codex["tray"])
                self.assertIn('class="usage-chip-pref"', codex["chip"])
                self.assertIsNone(codex["pace"])
                self.assertEqual((claude["hub"], claude["active"]), ("claude", "claude"))
                self.assertNotIn("preferred by Settings", claude["tray"])
                self.assertNotIn("usage-chip-pref", claude["chip"])
                self.assertEqual((balanced["hub"], balanced["active"]), ("balanced", "balanced"))
                self.assertNotIn("preferred by Settings", balanced["tray"])


if __name__ == "__main__":
    unittest.main()
