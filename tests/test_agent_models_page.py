"""#145 in a real page: Settings' Agent models rows, on a desktop and at 390 px.

In headless Chrome over CDP, against a hub in a thread (the hub, the page and
the Chrome launch are tests/test_settings_themes_page.py's):

* the section has a select for Claude's model, Codex's model and Codex's
  reasoning effort, each with its label; the options are the agent's own
  default as it is now, then what the agent offers;
* every select is inside the panel and the screen, nothing scrolls sideways,
  and on a phone each is finger-sized with 16 px text; its text and the note
  under it read in every theme;
* choosing a model saves it on the hub at once, and the plan chip's Codex line
  and the pool marked in use follow it; an effort the new model does not take
  goes back to Codex's own, and says so; a model the agent does not offer is
  refused, the hub's reason shown and the select put back;
* the old free-text "Default model" field is gone, and its setting is
  Claude's model.

Skipped without Node or Chrome; launches go through tests/chrome_profile.py.
With ENSEMBLE_SHOTS set to a folder, a picture of each width is left there.
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

import agent_models  # noqa: E402
import usage  # noqa: E402
from tests import test_settings_themes_page as browser  # noqa: E402
from tests.test_agent_models import CACHE, _pool_source  # noqa: E402
from tests.test_top_bar import CDP_JS as TOP_BAR_JS, CHROME, NODE  # noqa: E402

THEMES = ["light", "dark", "dim", "paper", "contrast", "fjord"]

CDP_JS = TOP_BAR_JS[:TOP_BAR_JS.index("// What the bar shows")] + r"""
const THEMES = """ + str(THEMES) + r""";
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [name, w, h, mobile] of [['desktop', 1280, 800, false], ['phone', 390, 844, true]]) {
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
      if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
      const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
      const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
      const saved = () => evalIn(`fetch('/api/settings').then(r => r.json()).then(s => s.agentModels)`);
      const pick = (id, value) => evalIn(`(() => { const s = document.getElementById('${id}');
        if (![...s.options].some(o => o.value === '${value}')) s.add(new Option('${value}', '${value}'));
        s.value = '${value}'; s.dispatchEvent(new Event('change', { bubbles: true })); })(); 0`);
      const toastText = () => evalIn(`(() => { const t = document.querySelector('#status .msg'); return t ? t.textContent : ''; })()`);
      await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
      await until('typeof PROJECTS !== "undefined" && !!PROJECTS && !!AGENT_MODELS && !!PREFS', 30000);
      const o = out[name] = { vw: w, vh: h };
      await evalIn('document.getElementById("me-btn").click(); 0');
      await evalIn('[...document.querySelectorAll("#me-menu .me-item")].find(b => /^Settings/.test(b.textContent)).click(); 0');
      await until('!document.getElementById("settings-panel").hidden && !document.getElementById("pref-model-codex").disabled');
      const MEASURE = `(() => { const p = document.getElementById('settings-panel'), sec = document.getElementById('settings-models');
        sec.scrollIntoView({ block: 'start' });
        const pr = p.getBoundingClientRect();
        const rows = [...sec.querySelectorAll('select')].map(s => { const r = s.getBoundingClientRect(), cs = getComputedStyle(s), l = sec.querySelector('label[for="' + s.id + '"]');
          return { id: s.id, label: l ? l.textContent : '', labelAbove: !!l && l.getBoundingClientRect().bottom <= r.top + 1,
                   l: r.left, r: r.right, t: r.top, b: r.bottom, h: r.height, font: parseFloat(cs.fontSize), family: cs.fontFamily,
                   fg: cs.color, bg: cs.backgroundColor, disabled: s.disabled, value: s.value,
                   options: [...s.options].map(x => [x.value, x.textContent]) }; });
        const note = document.getElementById('pref-model-codex-note'), hint = sec.querySelector('p.cfg-hint:not([id])');
        return { heading: sec.querySelector('h4').textContent, headings: [...p.querySelectorAll('h4')].map(e => e.textContent),
                 panel: { l: pr.left, r: pr.right, t: pr.top, b: pr.bottom, scroll: p.scrollWidth, client: p.clientWidth },
                 rows, note: note.hidden ? '' : note.textContent, hint: hint.textContent,
                 claudeNote: document.getElementById('pref-model-claude-note').textContent,
                 noteFg: getComputedStyle(note).color, panelBg: getComputedStyle(p).backgroundColor,
                 oldField: !!document.getElementById('pref-default-model'),
                 presets: !!document.getElementById('model-presets') }; })()`;
      o.first = await evalIn(MEASURE);
      o.themes = [];
      for (const theme of THEMES) {
        await evalIn(`document.documentElement.dataset.theme = '${theme}'; 0`);
        const m = await evalIn(MEASURE);
        o.themes.push({ theme, pairs: m.rows.map(r => [r.fg, r.bg]).concat([[m.noteFg, m.panelBg]]) });
      }
      await evalIn(`document.documentElement.dataset.theme = 'light'; 0`);
      if (A.shots) { const shot = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, 'agent-models-' + name + '.png'), Buffer.from(shot.data, 'base64')); }
      // Codex: the reserve's model. Saved at once; the chip and its tray follow.
      o.chipBefore = await evalIn(`usageSourceHtml(USAGE.sources.find(s => s.source === 'codex')).replace(/\\s+/g, ' ')`);
      await pick('pref-model-codex', 'gpt-reserve');
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.agentModels.codex.model === 'gpt-reserve')`);
      await until(`/reserve pool/.test(document.getElementById('pref-model-codex-note').textContent)`);
      await until(`(USAGE.sources.find(s => s.source === 'codex').poolInUse || {}).from === 'settings'`);
      o.reserve = await evalIn(`({ note: document.getElementById('pref-model-codex-note').textContent,
        select: document.getElementById('pref-model-codex').value,
        efforts: [...document.getElementById('pref-effort-codex').options].map(x => x.value),
        tray: usageSourceHtml(USAGE.sources.find(s => s.source === 'codex')).replace(/\\s+/g, ' '),
        chip: document.getElementById('usage-chip-body').textContent.replace(/\\s+/g, ' '),
        alarm: document.getElementById('usage-chip').classList.contains('alarm') })`);
      // An effort, then a model that does not take it: back to Codex's own, and said.
      await pick('pref-effort-codex', 'max');
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.agentModels.codex.effort === 'max')`);
      await until(`PREFS.agentModels.codex.effort === 'max'`);
      // ... chosen while the lists are still being written again after the effort.
      await pick('pref-model-codex', 'gpt-5.5');
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.agentModels.codex.model === 'gpt-5.5')`);
      await until(`document.getElementById('pref-effort-codex').value === '' && !document.getElementById('pref-model-codex').disabled`);
      o.effortBack = { saved: await saved(), toast: await toastText(),
        efforts: await evalIn(`[...document.getElementById('pref-effort-codex').options].map(x => x.value)`) };
      // A model Codex does not offer: refused, the reason shown, the select put back.
      await pick('pref-model-codex', 'gpt-7-nova');
      await until(`document.getElementById('pref-model-codex').value === 'gpt-5.5' && !document.getElementById('pref-model-codex').disabled`);
      o.refused = { saved: await saved(), toast: await toastText(),
        offered: await evalIn(`[...document.getElementById('pref-model-codex').options].some(x => x.value === 'gpt-7-nova')`) };
      // Claude, and the older name of the same setting.
      await pick('pref-model-claude', 'opus');
      await until(`fetch('/api/settings').then(r => r.json()).then(s => s.agentModels.claude.model === 'opus')`);
      await until(`PREFS.defaultModel === 'opus' && document.getElementById('pref-model-claude').value === 'opus'`);
      o.claude = await evalIn(`fetch('/api/settings').then(r => r.json()).then(s => ({ both: [s.defaultModel, s.agentModels.claude.model] }))`);
      o.after = await evalIn(MEASURE);
      // Back to nothing chosen for the next width.
      await evalIn(`fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ agentModels: { claude: { model: '' }, codex: { model: '', effort: '' } } }) }).then(r => r.status)`);
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


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class AgentModelsInSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models = [{"id": m["slug"], "name": m["display_name"], "description": m["description"],
                   "efforts": [e["effort"] for e in m["supported_reasoning_levels"]],
                   "defaultEffort": m["default_reasoning_level"],
                   "pool": "reserve" if m["slug"] == "gpt-reserve" else None}
                  for m in sorted(CACHE["models"], key=lambda m: m["priority"])
                  if m["visibility"] == "list" or m["slug"] == "gpt-reserve"]
        source = usage._mark_pool_in_use(_pool_source(), "gpt-6-astra", "")
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        script = CDP_JS.replace("A.shots", repr(shots.replace("\\", "/")) if shots else "''")
        with mock.patch.object(browser, "CDP_JS", script), \
                mock.patch.object(agent_models, "codex_models", side_effect=lambda path=None: [dict(m) for m in models]), \
                mock.patch.object(agent_models, "codex_own", return_value={"model": "gpt-6-astra", "effort": "high"}), \
                mock.patch.object(agent_models, "claude_own", return_value={"model": "claude-fable-5-1[1m]"}), \
                mock.patch.dict(usage._STATE, {"state": "ready", "checkedAt": 1.0, "sources": {"codex": source}}):
            browser.SettingsScrollsAndThemeSubmenu.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.SettingsScrollsAndThemeSubmenu.tearDownClass.__func__(cls)

    def test_three_labelled_selects_with_what_each_agent_offers(self):
        for name, g in self.got.items():
            with self.subTest(name):
                first = g["first"]
                self.assertEqual(first["heading"], "Agent models")
                rows = {r["id"]: r for r in first["rows"]}
                self.assertEqual([(r["id"], r["label"]) for r in first["rows"]], [
                    ("pref-model-claude", "Claude model"), ("pref-model-codex", "Codex model"),
                    ("pref-effort-codex", "Codex reasoning effort")])
                self.assertTrue(all(r["labelAbove"] and not r["disabled"] and r["value"] == ""
                                    for r in first["rows"]), first["rows"])
                self.assertEqual(rows["pref-model-claude"]["options"], [
                    ["", "Claude’s own default (currently claude-fable-5-1[1m])"],
                    ["fable", "fable"], ["opus", "opus"], ["sonnet", "sonnet"], ["haiku", "haiku"]])
                codex = rows["pref-model-codex"]["options"]
                self.assertEqual(codex[0], ["", "Codex’s own default (currently gpt-6-astra)"])
                self.assertEqual([v for v, _ in codex[1:]],
                                 ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-reserve", "gpt-5.5"])
                self.assertIn(["gpt-reserve", "gpt-reserve — spends the reserve pool"], codex)
                self.assertIn(["gpt-6-sol", "gpt-6-sol — GPT-6-Sol in a line"], codex)
                self.assertEqual(rows["pref-effort-codex"]["options"], [
                    ["", "Codex’s own (currently high)"], *[[e, e] for e in
                                                            ("low", "medium", "high", "xhigh", "max", "ultra")]])
                # What runs is also said in words: a phone's select cuts a long choice short.
                self.assertEqual(first["claudeNote"], "Claude agents run on claude-fable-5-1[1m].")
                self.assertIn("gpt-6-astra and draw on the main pool", first["note"])
                self.assertIn("still starts on gpt-5.6-sol", first["note"])
                self.assertIn("A seat that names a model keeps it.", first["hint"])
                self.assertIn("never changed", first["hint"])

    def test_the_old_free_text_field_is_gone_and_its_suggestions_kept(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertFalse(g["first"]["oldField"])
                self.assertTrue(g["first"]["presets"], "the New session dialog still suggests models")
                self.assertEqual(g["first"]["headings"][-1], "This instance")
                self.assertLess(g["first"]["headings"].index("Agent models"),
                                g["first"]["headings"].index("Preferences"))

    def test_the_rows_fit_the_panel_and_the_screen(self):
        for name, g in self.got.items():
            for when in ("first", "after"):
                with self.subTest(name, when=when):
                    m = g[when]
                    panel = m["panel"]
                    self.assertGreaterEqual(panel["l"], 0)
                    self.assertLessEqual(panel["r"], g["vw"])
                    self.assertLessEqual(panel["b"], g["vh"])
                    self.assertLessEqual(panel["scroll"], panel["client"] + 1, "nothing scrolls sideways")
                    for r in m["rows"]:
                        self.assertGreaterEqual(r["l"], panel["l"], r)
                        self.assertLessEqual(r["r"], panel["r"], r)
                        self.assertGreater(r["r"] - r["l"], 200, r)
                        self.assertIn("mono", r["family"].lower())
                        if name == "phone":
                            self.assertGreaterEqual(r["h"], 44, "finger-sized")
                            self.assertGreaterEqual(r["font"], 16, "a phone does not zoom into it")
                        else:
                            self.assertEqual(r["h"], 32)
                            self.assertEqual(r["font"], 12)
        phone = self.got["phone"]["first"]
        self.assertGreater(phone["rows"][0]["r"] - phone["rows"][0]["l"], 300, "the width of a phone's panel")

    def test_the_selects_and_the_note_read_in_every_theme(self):
        for name, g in self.got.items():
            self.assertEqual([t["theme"] for t in g["themes"]], THEMES)
            for t in g["themes"]:
                for fg, bg in t["pairs"]:
                    with self.subTest(name, theme=t["theme"], fg=fg, bg=bg):
                        a, b = sorted([_luminance(fg), _luminance(bg)])
                        self.assertGreaterEqual((b + .05) / (a + .05), 4.5)

    def test_choosing_codex_s_model_saves_it_and_the_plan_chip_follows(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertIn("Codex sessions currently use the main pool "
                              "(model gpt-6-astra, Codex’s own default).", g["chipBefore"])
                r = g["reserve"]
                self.assertEqual(r["select"], "gpt-reserve")
                self.assertEqual(r["note"], "Codex agents run on gpt-reserve and draw on the reserve pool.")
                self.assertEqual(r["efforts"], ["", "low", "medium", "high", "xhigh", "max"])
                self.assertIn("Codex sessions currently use the reserve pool "
                              "(model gpt-reserve, chosen in Settings).", r["tray"])
                self.assertRegex(r["tray"], r'reserve pool</span> <span class="usage-pool-use">in use')
                self.assertIn("reserve", r["chip"])
                self.assertIn("3%", r["chip"])
                self.assertNotIn("97%", r["chip"], "the spent main pool is not the one in use")
                self.assertFalse(r["alarm"])

    def test_an_effort_the_new_model_does_not_take_goes_back_and_says_so(self):
        for name, g in self.got.items():
            with self.subTest(name):
                e = g["effortBack"]
                self.assertEqual(e["saved"]["codex"], {"model": "gpt-5.5", "effort": ""})
                self.assertEqual(e["efforts"], ["", "low", "medium", "high", "xhigh"])
                self.assertIn("Reasoning effort is back to Codex’s own", e["toast"])
                self.assertIn("gpt-5.5 does not take “max”", e["toast"])

    def test_a_model_the_agent_does_not_offer_is_refused_and_the_select_put_back(self):
        for name, g in self.got.items():
            with self.subTest(name):
                r = g["refused"]
                self.assertEqual(r["saved"]["codex"]["model"], "gpt-5.5")
                self.assertEqual(r["toast"], "Not saved: Codex offers no model “gpt-7-nova”.")
                self.assertFalse(r["offered"])

    def test_claude_s_model_is_the_old_default_model_setting(self):
        for name, g in self.got.items():
            with self.subTest(name):
                self.assertEqual(g["claude"]["both"], ["opus", "opus"])
                rows = {r["id"]: r for r in g["after"]["rows"]}
                self.assertEqual(rows["pref-model-claude"]["value"], "opus")
                self.assertEqual(g["after"]["claudeNote"], "Claude agents run on opus.")
                self.assertEqual(rows["pref-model-codex"]["value"], "gpt-5.5")


if __name__ == "__main__":
    unittest.main()
