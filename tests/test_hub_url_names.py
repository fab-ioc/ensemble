"""#296: a URL an agent writes to one of this hub's own things shows the
thing's name and opens from the page's own origin, whatever address it names.

The forms are the ones measured in the chats (11-04): Markdown links and bare
URLs to /fileview?path= at 127.0.0.1, localhost or the tailnet address, with
%5C or %2F slashes; links to a message or card (/session?room=…&msg=…); a
task (/?task=room-…); a template copied literally ("?path=<URL-encoded …>").
External URLs stay as written.

The shared link block runs in Node (as tests/test_links.py runs it), from the
hub machine and from another computer on the tailnet. The Chrome check posts
one balloon with every form to an isolated hub, opens it under a host name
that is not loopback (another computer, as Chrome sees it) and reads each
link's words and href off the page.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from tests.test_links import NODE, PAGES, shared_block

JS = r"""
globalThis.location = new URL(process.argv[1]);
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
%s
HUB_PORT = '8765';   // what /api/config says
const render = t => {
  if (t.length > 1 && t.startsWith('`') && t.endsWith('`')) return codeSpanHtml(t.slice(1, -1));
  const keep = [];
  return unlinkify(linkify(esc(t), keep), keep);
};
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(cases.map(render)));
"""

WIN = r"C:\Users\me\Projects\Trading\Documents\#12 Plan.md"
ENC = quote(WIN, safe="")                      # C%3A%5CUsers%5C…
ENC_SLASH = quote(WIN.replace("\\", "/"), safe="")   # C%3A%2FUsers%2F…
REPORT = quote(r"C:\Users\me\notes\report.md", safe="")
ROOM = "room-1a2b3c4d"
TAILNET = "https://hub.tail1234.ts.net:9443"
ORIGINS = {"hub machine": "http://127.0.0.1:8765/session?room=" + ROOM,
           "another computer": TAILNET + "/session?room=" + ROOM}


def anchors(html: str) -> list[tuple[str, str, str]]:
    """(href, words, title) of each <a> in the html, entities undone."""
    import re
    un = lambda s: s.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    out = []
    for m in re.finditer(r'<a ([^>]*)>(.*?)</a>', html):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        words = re.sub(r"<[^>]+>", "", m.group(2))
        out.append((un(attrs.get("href", "")), un(words), un(attrs.get("title", ""))))
    return out


@unittest.skipUnless(NODE, "node is not installed")
class HubUrlNames(unittest.TestCase):
    def render(self, origin: str, cases: list[str]) -> list[str]:
        out = subprocess.run([NODE, "-e", JS % shared_block(PAGES["session.html"]), origin], input=json.dumps(cases),
                             capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def both(self, cases):
        return {where: dict(zip(cases, self.render(o, cases))) for where, o in ORIGINS.items()}

    def one_link(self, html, case):
        a = anchors(html)
        self.assertEqual(len(a), 1, (case, html))
        return a[0]

    def test_a_file_view_at_any_hub_address_is_the_file_by_name(self):
        bare = [f"see http://127.0.0.1:8765/fileview?path={ENC} now",
                f"see http://localhost:8765/fileview?path={ENC} now",
                f"see http://127.0.0.1:8765/fileview?path={ENC_SLASH} now",
                f"see http://127.0.0.1:8765/fileview?path={ENC_SLASH.replace('%2F', '%2f')} now",
                f"see {TAILNET}/fileview?path={ENC} now",
                f"see http://100.101.2.3:8765/fileview?path={ENC} now",
                f"see http://127.0.0.1:9443/fileview?path={ENC} now",     # a proxy's port on this machine
                f"see [http://127.0.0.1:8765/fileview?path={ENC}](http://127.0.0.1:8765/fileview?path={ENC}) now"]
        for where, r in self.both(bare).items():
            for case in bare:
                with self.subTest(where=where, case=case):
                    href, words, title = self.one_link(r[case], case)
                    self.assertEqual(words, "#12 Plan.md")
                    self.assertTrue(href.startswith("/fileview?path="), href)
                    self.assertIn(title.replace("/", "\\"), (WIN,))
                    self.assertIn('class="file-link"', r[case])
                    self.assertTrue(r[case].startswith("see ") and r[case].endswith(" now"), r[case])

    def test_a_markdown_link_keeps_its_words_and_gets_this_origin(self):
        cases = [f"[the plan](http://127.0.0.1:8765/fileview?path={ENC})",
                 f"[the plan](http://localhost:8765/fileview?path={ENC_SLASH})",
                 f"[the plan]({TAILNET}/fileview?path={ENC})",
                 f"[the plan](localhost:8765/fileview?path={ENC})"]
        for where, r in self.both(cases).items():
            for case in cases:
                with self.subTest(where=where, case=case):
                    href, words, _ = self.one_link(r[case], case)
                    self.assertEqual(words, "the plan")
                    self.assertTrue(href.startswith("/fileview?path="), href)

    def test_a_line_and_a_folder(self):
        cases = [f"at http://127.0.0.1:8765/fileview?path={quote('C:/x/run.py', safe='')}&line=40.",
                 f"in http://127.0.0.1:8765/fileview?path={quote('C:\\x\\Documents\\', safe='')} folder",
                 f"`http://127.0.0.1:8765/fileview?path={ENC}`"]
        for where, r in self.both(cases).items():
            with self.subTest(where=where):
                href, words, _ = self.one_link(r[cases[0]], cases[0])
                self.assertEqual(words, "run.py")
                self.assertIn("&line=40", href)
                self.assertTrue(r[cases[0]].endswith("</a>."), r[cases[0]])   # the full stop is the sentence's
                self.assertEqual(self.one_link(r[cases[1]], cases[1])[1], "Documents")
                # a code span keeps reading as code, with the name in it
                self.assertIn('<code class="ic">#12 Plan.md</code>', r[cases[2]])

    def test_messages_cards_and_tasks_by_what_they_are(self):
        cases = [f"see http://127.0.0.1:8765/session?room={ROOM}&msg=m-1 now",
                 f"see http://127.0.0.1:8765/session?room={ROOM}&msg=m-1&part=ask:0 now",
                 f"[Question 1](http://127.0.0.1:8765/session?room={ROOM}&msg=m-1&part=ask:0)",
                 f"see {TAILNET}/session?room={ROOM}&msg=m-1 now",
                 f"see http://127.0.0.1:8765/?task={ROOM} now",
                 f"see {TAILNET}/?task={ROOM} now"]
        words = ["the message", "the question card", "Question 1", "the message", "the task", "the task"]
        for where, r in self.both(cases).items():
            for case, want in zip(cases, words):
                with self.subTest(where=where, case=case):
                    href, got, _ = self.one_link(r[case], case)
                    self.assertEqual(got, want)
                    self.assertTrue(href.startswith("/session?room=" + ROOM) or href == "/?task=" + ROOM, href)

    def test_a_crafted_file_name_is_text_not_markup(self):
        evil = r'C:\x\a"><img src=x onerror=alert(1)>&b.md'
        enc = quote(evil, safe="")
        cases = [f"see http://127.0.0.1:8765/fileview?path={enc} now",
                 f"`http://127.0.0.1:8765/fileview?path={enc}`",
                 f"[http://x](http://127.0.0.1:8765/fileview?path={enc})",
                 f"see http://127.0.0.1:8765/fileview?path={quote('C:/x/a&lt;b&quot;.md', safe='')} now"]
        for where, r in self.both(cases).items():
            for case in cases:
                with self.subTest(where=where, case=case):
                    html = r[case]
                    self.assertNotIn("<img", html)
                    self.assertNotIn('"><', html.replace('"><a', '').replace('"><code', ''))
                    for href, _, title in anchors(html):
                        self.assertTrue(href.startswith("/fileview?path="), href)

    def test_only_this_hubs_addresses_are_rewritten(self):
        # A message, task or file URL is Ensemble's only on an address this hub
        # answers on; the same shape on any other host is someone else's page.
        ext = [f"see https://example.com/session?room={ROOM}&msg=m-1 now",
               f"see https://example.com/session?room={ROOM}&msg=m-1&part=ask:0 now",
               f"see https://example.com/?task={ROOM} now",
               f"see https://example.com/fileview?path={ENC} now"]
        for where, r in self.both(ext).items():
            for case in ext:
                with self.subTest(where=where, case=case):
                    href, words, _ = self.one_link(r[case], case)
                    url = case[4:-4]
                    self.assertEqual((href, words), (url, url))
                    self.assertNotIn("msg-link", r[case])
                    self.assertNotIn("task-link", r[case])

    def test_a_template_copied_literally_is_text(self):
        case = "open it (http://127.0.0.1:8765/fileview?path=<URL-encoded absolute path>) here"
        for where, r in self.both([case]).items():
            with self.subTest(where=where):
                self.assertEqual(anchors(r[case]), [])
                self.assertNotIn("<a", r[case])

    def test_external_urls_stay_as_written(self):
        cases = ["see https://example.com/fileview?path=C%3A%5Cx.md now",
                 "see https://github.com/org/repo/pull/12 now",
                 "[docs](https://docs.python.org/3/library/re.html)"]
        for where, r in self.both(cases).items():
            with self.subTest(where=where):
                self.assertEqual(anchors(r[cases[0]]), [("https://example.com/fileview?path=C%3A%5Cx.md",
                                                         "https://example.com/fileview?path=C%3A%5Cx.md", "")])
                self.assertEqual(anchors(r[cases[1]])[0][:2], ("https://github.com/org/repo/pull/12",) * 2)
                self.assertEqual(anchors(r[cases[2]])[0][:2], ("https://docs.python.org/3/library/re.html", "docs"))


try:
    import chatroom
    import dashboard
    from tests import chrome_profile
    from tests.test_task_numbers import Hub
    from tests.test_task_ref_project_page import CDP_JS as TASK_CDP, CHROME
except Exception:   # the Chrome check needs the hub's modules; the Node checks above do not
    Hub, CHROME = unittest.TestCase, None

# Another computer, as Chrome sees it: a host name that is not loopback.
MAC_HOST = "hub-mac.test"

CDP_JS = (TASK_CDP.split('// The chips in the conversation')[0] if CHROME else '').replace(
    "'--disable-gpu',", "'--disable-gpu', '--host-resolver-rules=MAP " + MAC_HOST + " 127.0.0.1',") + r'''
async function main() {
  const {ch, ws} = await launch(), c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [width, height] of [[1728, 1117], [390, 844]]) {
      const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
      const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
      const send = (m, p) => c.send(m, p, sessionId);
      await send('Page.enable', {});
      await send('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor:1, mobile: width === 390});
      const ev = async expression => {
        const r = await send('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;
      };
      const until = async (expr, n=160) => { for (let i=0;i<n;i++) { try { if(await ev(expr)) return; } catch (e) {} await sleep(150); } throw Error('timeout '+expr+' '+await ev('document.body.innerText.slice(-1600)')); };
      // Signed in as another computer is (ED-341): the hub swaps ?token= for its cookie.
      await send('Page.navigate', {url:A.base+'/session?room='+A.room+'&token='+A.token});
      // drawn once the message chip and the task chip have their names
      await until(`(() => { const b = [...document.querySelectorAll('#msgs .bubble, #msgs .msg')].find(x => x.textContent.includes('LINKS:'));
        return b && b.querySelector('a.ref-chip') && b.querySelector('a.task-chip'); })()`);
      await sleep(300);
      out[width] = await ev(`(() => { const b = [...document.querySelectorAll('#msgs .bubble, #msgs .msg')].find(x => x.textContent.includes('LINKS:'));
        return { origin: location.origin, text: b.innerText, overflow: document.documentElement.scrollWidth > innerWidth,
          links: [...b.querySelectorAll('a')].map(a => ({ words: a.textContent.trim(), href: a.href, cls: a.className, title: a.title })) }; })()`);
      if (A.shots) { const r = await send('Page.captureScreenshot', {format:'png'}); fs.writeFileSync(path.join(A.shots, 'hub-url-names-' + width + '.png'), Buffer.from(r.data, 'base64')); }
      await c.send('Target.closeTarget', {targetId});
    }
  } finally { try {await c.send('Browser.close');} catch(e) {} ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
'''


@unittest.skipUnless(NODE and CHROME, "node and Chrome required")
class HubUrlNamesInChrome(Hub):
    def test_every_form_shows_a_name_and_opens_from_this_origin(self):
        base = Path(self.tmp.name)
        for attr, val in [('load_live', lambda: []), ('_read_agent_session_files', lambda *a, **k: [])]:
            p = mock.patch.object(dashboard, attr, val); p.start(); self.addCleanup(p.stop)
        for attr, val in [('_agent_peer', lambda h: ''), ('log_message', lambda *a, **k: None)]:
            p = mock.patch.object(dashboard.Handler, attr, val); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(dashboard, 'SETTINGS_FILE', base / 'settings.json'); p.start(); self.addCleanup(p.stop)
        # Another computer needs the token, as on every hub (ED-341).
        p = mock.patch.object(dashboard, 'ACCESS_TOKEN', 'token-for-the-test'); p.start(); self.addCleanup(p.stop)
        rid = self.room('Task chat', self.ed)
        other = self.room('Another task', self.ed, numbered=True)
        first = chatroom.post_message(rid, 'claude', 'Plan is ready for a look.', to='user')['message']['id']
        server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
        server.daemon_threads = True
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_port
        hub = f"http://127.0.0.1:{port}"
        text = "\n".join([
            "LINKS:",
            f"- md: [the plan](http://127.0.0.1:8765/fileview?path={ENC})",
            f"- bare: http://localhost:8765/fileview?path={ENC_SLASH}",
            f"- tailnet: {TAILNET}/fileview?path={REPORT}",
            f"- message: {hub}/session?room={rid}&msg={first}",
            f"- task: {hub}/?task={other}",
            "- template: (http://127.0.0.1:8765/fileview?path=<URL-encoded absolute path>)",
            "- external: https://example.com/docs?a=1",
        ])
        chatroom.post_message(rid, 'claude', text, to='user')
        shots = os.environ.get('ENSEMBLE_SHOTS', '')
        if shots: Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), 'tmp': str(base), 'base': f"http://{MAC_HOST}:{port}", 'token': 'token-for-the-test', 'room': rid, 'shots': shots}
        script = base / 'hub_url_names_cdp.js'; script.write_text(CDP_JS, encoding='utf-8')
        r = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding='utf-8', timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        task_no = self.no(other)
        self.assertTrue(task_no)
        for width, row in got.items():
            with self.subTest(width=width):
                origin = row['origin']
                self.assertEqual(origin, f"http://{MAC_HOST}:{port}")
                links = {l['words']: l for l in row['links']}
                for words in ('the plan', '#12 Plan.md', 'report.md'):
                    self.assertIn(words, links, row['links'])
                    self.assertTrue(links[words]['href'].startswith(origin + '/fileview?path='), links[words])
                    self.assertIn('file-link', links[words]['cls'])
                self.assertIn(links['#12 Plan.md']['title'], (WIN.replace('\\', '/'), 'This file does not exist yet'))
                chip = [l for l in row['links'] if 'ref-chip' in l['cls']]
                self.assertEqual(len(chip), 1, row['links'])
                self.assertTrue(chip[0]['href'].startswith(f"{origin}/session?room={rid}&msg="), chip[0])
                task = [l for l in row['links'] if 'task-chip' in l['cls']]
                self.assertEqual(len(task), 1, row['links'])
                self.assertIn(f"#{task_no}", task[0]['words'])
                self.assertTrue(all(l['href'].startswith(origin) or l['href'] == 'https://example.com/docs?a=1'
                                    for l in row['links'] if l['href']), row['links'])
                for bad in ('127.0.0.1', 'localhost', 'ts.net', 'fileview?path=C', 'session?room='):
                    self.assertNotIn(bad, row['text'].split('template:')[0], bad)
                self.assertIn('https://example.com/docs?a=1', row['text'])
                self.assertFalse(row['overflow'])


if __name__ == "__main__":
    unittest.main()
