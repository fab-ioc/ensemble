"""Real Chrome: account/settings entries, snapshot editing, local images and
explicit non-anonymous fallback, plus iframe geometry in every theme."""
import unittest
from unittest.mock import patch

import feedback
from tests import test_settings_themes_page as browser
from tests.test_top_bar import CDP_JS as BASE_JS, NODE, CHROME


SCRIPT = BASE_JS[:BASE_JS.index('// What the bar shows')] + r"""
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  try {
    const {targetId} = await c.send('Target.createTarget', {url: A.base + '/'});
    const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten: true});
    const evaluate = async expression => {
      const r = await c.send('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true}, sessionId);
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
    };
    const until = async expression => { for (let i=0;i<200;i++) { try { if(await evaluate(expression)) return; } catch {} await sleep(100); } throw Error('timeout ' + expression); };
    await until('typeof feedbackOpen === "function"');
    const out = {geometry: []};
    for (const [width,height] of [[360,800],[430,932],[844,390],[899,700],[1280,900],[1440,900]]) {
      await evaluate(`document.querySelector('#test-frame')?.remove(); (()=>{const f=document.createElement('iframe'); f.id='test-frame'; f.src='/'; f.style.cssText='position:fixed;left:0;top:0;border:0;z-index:99999;width:${width}px;height:${height}px'; document.body.append(f)})()`);
      await until('document.querySelector("#test-frame").contentWindow.feedbackOpen');
      const inside = expression => evaluate(`document.querySelector('#test-frame').contentWindow.eval(${JSON.stringify(expression)})`);
      // Coarse-pointer landscape query is simulated only within the test frame.
      if (width === 844) await inside(`for(const sheet of document.styleSheets) { try { for(const rule of sheet.cssRules) if(rule.media && rule.conditionText.includes('pointer: coarse')) rule.media.mediaText=rule.conditionText.replaceAll('(pointer: coarse)', '(min-width: 0px)'); } catch {} }`);
      await inside(`document.getElementById('me-btn').click(); document.querySelector('[data-me="feedback"]').click();`);
      for (const theme of ['light','dark','dim','paper','contrast','fjord','intellij-dark']) {
        await inside(`document.documentElement.dataset.theme='${theme}'`);
        out.geometry.push(await inside(`(()=>{const d=document.getElementById('feedback-dialog'), r=d.getBoundingClientRect(), f=document.getElementById('feedback-title'), s=getComputedStyle(d), b=getComputedStyle(document.getElementById('feedback-preview-button')); return {width:innerWidth,height:innerHeight,theme:document.documentElement.dataset.theme,left:r.left,right:r.right,top:r.top,bottom:r.bottom,scroll:d.scrollWidth,client:d.clientWidth,font:parseFloat(getComputedStyle(f).fontSize),button:document.getElementById('feedback-preview-button').getBoundingClientRect().height,fg:s.color,bg:s.backgroundColor,bfg:b.color,bbg:b.backgroundColor,open:d.open}})()`));
      }
      if(width===360) {
        await inside(`document.getElementById('feedback-title').value='Alice bug'; document.getElementById('feedback-description').value='/'+'Us'+'ers/Alice/private'; document.getElementById('feedback-anonymous').checked=true; document.getElementById('feedback-anonymous').dispatchEvent(new Event('input',{bubbles:true}));
          const dt=new DataTransfer(); dt.items.add(new File(['image'], 'Alice-laptop.png', {type:'image/png'})); document.getElementById('feedback-description').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true}));
          document.getElementById('feedback-form').requestSubmit();`);
        await until(`!document.querySelector('#test-frame').contentDocument.getElementById('feedback-preview').hidden`);
        out.preview=await inside(`({title:document.getElementById('feedback-post-title').textContent,body:document.getElementById('feedback-post-body').textContent,route:document.getElementById('feedback-route').textContent,images:document.querySelectorAll('#feedback-images a[download]').length,nameHidden:getComputedStyle(document.getElementById('feedback-name-label')).display})`);
        await inside(`document.getElementById('feedback-send').click()`);
        await until(`document.querySelector('#test-frame').contentDocument.querySelector('#feedback-status a')`);
        out.fallback=await inside(`({text:document.getElementById('feedback-status').textContent,url:document.querySelector('#feedback-status a').href,description:document.getElementById('feedback-description').value})`);
        out.invalidated=await inside(`document.getElementById('feedback-title').dispatchEvent(new Event('input',{bubbles:true})); document.getElementById('feedback-preview').hidden`);
        out.settings=await inside(`document.getElementById('feedback-close').click(); document.getElementById('feedback-open').click(); document.getElementById('feedback-dialog').open`);
      }
    }
    console.log(JSON.stringify(out));
  } finally {c.ws.close(); ch.kill();}
}
main().then(()=>process.exit(0), e=>{console.error(e);process.exit(1)});
"""


@unittest.skipUnless(NODE and CHROME, 'needs Node and Chrome')
class FeedbackPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The default relay is live: a Send here must never reach it (it files real issues), so the relay is "down".
        offline = patch.object(feedback.urllib.request, 'build_opener', side_effect=feedback.urllib.error.URLError('test: no network'))
        with patch.object(browser, 'CDP_JS', SCRIPT), patch.object(feedback, 'output', return_value=''), patch.object(feedback, 'identities', return_value=['Alice']), offline:
            browser.SettingsScrollsAndThemeSubmenu.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser.SettingsScrollsAndThemeSubmenu.tearDownClass.__func__(cls)

    def test_preview_scrub_images_fallback_edit_and_entries(self):
        self.assertNotIn('Alice', self.got['preview']['title'] + self.got['preview']['body'])
        self.assertEqual(self.got['preview']['images'], 1)
        self.assertEqual(self.got['preview']['nameHidden'], 'none')
        self.assertIn('not anonymous', self.got['fallback']['text'])
        self.assertEqual(self.got['fallback']['description'], '/' + 'Us' + 'ers/Alice/private')
        self.assertTrue(self.got['invalidated'])
        self.assertTrue(self.got['settings'])

    def test_geometry_and_contrast(self):
        import re
        def luminance(rgb):
            channels = [int(x) / 255 for x in re.findall(r'\d+', rgb)[:3]]
            channels = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in channels]
            return sum(x * w for x, w in zip(channels, [.2126, .7152, .0722]))
        for row in self.got['geometry']:
            with self.subTest(width=row['width'], theme=row['theme']):
                self.assertTrue(row['open'])
                self.assertGreaterEqual(row['left'], 0)
                self.assertLessEqual(row['right'], row['width'])
                self.assertLessEqual(row['bottom'], row['height'])
                self.assertLessEqual(row['scroll'], row['client'] + 1)
                if row['width'] in (360, 430, 844):
                    self.assertGreaterEqual(row['font'], 16)
                    self.assertGreaterEqual(row['button'], 44)
                for fg, bg in [('fg', 'bg'), ('bfg', 'bbg')]:
                    a, b = sorted([luminance(row[fg]), luminance(row[bg])])
                    self.assertGreaterEqual((b + .05) / (a + .05), 4.5)
