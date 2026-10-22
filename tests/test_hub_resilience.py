"""Browser queue behavior while the hub socket is briefly unavailable."""
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node is required for browser script checks")
class HubResilienceTest(unittest.TestCase):
    def test_uncertain_send_explains_manual_check_and_retry(self):
        wording = "May not have been delivered - check the chat, then Retry."
        for file in ("index.html", "static/hub-resilience.js"):
            self.assertIn(wording, (ROOT / file).read_text(encoding="utf-8"))

    def test_queued_terminal_send_lost_reply_is_not_flushed_twice(self):
        code = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let up = false, sends = 0;
const window = {
  fetch: async (path) => {
    if (path === '/api/send') { sends++; throw new TypeError('reply lost after injection'); }
    return { ok: up, status: up ? 200 : 503 };
  },
  addEventListener() {},
  dispatchEvent() {}
};
const document = { body: { appendChild() {} }, getElementById() { return null; },
  createElement() { return { style: {}, setAttribute() {} }; }, addEventListener() {} };
const context = { window, document, AbortSignal, AbortController, DOMException, Event: class {}, setTimeout() { return 1; } };
vm.runInNewContext(fs.readFileSync('static/hub-resilience.js', 'utf8'), context);
(async () => {
  await assert.rejects(window.fetch('/api/sessions'));
  const send = window.fetch('/api/send', { method: 'POST' });
  const rejected = assert.rejects(send, /May not have been delivered/);
  assert.strictEqual(window.HubResilience.queued, 1);
  up = true;
  await window.HubResilience.probe();
  await rejected;
  assert.strictEqual(sends, 1);
  assert.strictEqual(window.HubResilience.queued, 0);
  await window.HubResilience.probe();
  assert.strictEqual(sends, 1);
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([shutil.which("node"), "-e", code], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ambiguous_terminal_send_is_never_replayed(self):
        code = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let up = false, sends = 0;
const window = {
  fetch: async (path) => {
    if (path === '/api/send') { sends++; throw new TypeError('reply lost'); }
    return { ok: up, status: up ? 200 : 503 };
  },
  addEventListener() {},
  dispatchEvent() {}
};
const document = { body: { appendChild() {} }, getElementById() { return null; },
  createElement() { return { style: {}, setAttribute() {} }; }, addEventListener() {} };
const context = { window, document, AbortSignal, AbortController, DOMException, Event: class {}, setTimeout() { return 1; } };
vm.runInNewContext(fs.readFileSync('static/hub-resilience.js', 'utf8'), context);
(async () => {
  await assert.rejects(window.fetch('/api/send', { method: 'POST' }));
  assert.strictEqual(window.HubResilience.queued, 0);
  up = true;
  await window.HubResilience.probe();
  assert.strictEqual(sends, 1);
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([shutil.which("node"), "-e", code], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_queued_posts_flush_in_order_after_reconnect(self):
        code = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let up = false;
const calls = [], events = [];
const status = { hidden: true, style: {}, setAttribute() {} };
const document = {
  body: { appendChild(el) { assert.strictEqual(el, status); } },
  getElementById(id) { return id === 'hub-connection-line' && status.textContent ? status : null; },
  createElement() { return status; },
  addEventListener() {}
};
const window = {
  fetch: async (path) => {
    calls.push(path);
    if (!up) throw new TypeError('Failed to fetch');
    if (up === 'unavailable') return { ok: false, status: 503, path };
    return { ok: true, path };
  },
  addEventListener() {},
  dispatchEvent(e) { events.push(e.type); }
};
const context = { window, document, AbortSignal, AbortController, DOMException, Event: class { constructor(type) { this.type = type; } },
  setTimeout() { return 1; } };
vm.runInNewContext(fs.readFileSync('static/hub-resilience.js', 'utf8'), context);
(async () => {
  await assert.rejects(window.fetch('/api/sessions'));
  assert.strictEqual(window.HubResilience.offline, true);
  assert.strictEqual(status.hidden, false);
  const first = window.fetch('/api/room/resume', { method: 'POST' });
  const second = window.fetch('/api/room/ask', { method: 'POST' });
  const third = window.fetch('/api/send', { method: 'POST' });
  assert.strictEqual(window.HubResilience.queued, 3);
  up = 'unavailable';
  await window.HubResilience.probe();
  assert.strictEqual(window.HubResilience.queued, 3);
  assert.strictEqual(window.HubResilience.offline, true);
  up = true;
  await window.HubResilience.probe();
  assert.deepStrictEqual((await Promise.all([first, second, third])).map(r => r.path),
    ['/api/room/resume', '/api/room/ask', '/api/send']);
  assert.deepStrictEqual(calls.slice(-4), ['/api/platform', '/api/room/resume', '/api/room/ask', '/api/send']);
  assert.strictEqual(status.hidden, true);
  assert.deepStrictEqual(events, ['hub:offline', 'hub:online']);
})().catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([shutil.which("node"), "-e", code], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)


def _node(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("node"), "-e", code], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")


# The page as the script needs it, its fetch the test's own (#251).
PAGE = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const listeners = {};
const document = { body: { appendChild() {} }, getElementById() { return null; },
  createElement() { return { style: {}, setAttribute() {} }; },
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); } };
const window = { fetch: null, dispatchEvent() {},
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); } };
const context = { window, document, AbortSignal, AbortController, DOMException, Event: class {},
  setTimeout, clearTimeout, Date };
const load = f => { window.fetch = f; vm.runInNewContext(fs.readFileSync('static/hub-resilience.js', 'utf8'), context); };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for browser script checks")
class SendJsonTest(unittest.TestCase):
    """sendJson, every send-like button's request (#251): it always settles."""

    def run_node(self, body: str):
        # The real setTimeout keeps the probe loop alive, so exit explicitly;
        # node also exits by itself on an awaited promise nothing will settle,
        # so the body's end is checked for.
        result = _node(PAGE + "(async () => {\n" + body + "\n})().then(() => { console.log('#done'); process.exit(0); },"
                       " e => { console.error(e); process.exit(1); });")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#done", result.stdout, "an awaited promise was never settled")

    def test_an_answer_is_read(self):
        self.run_node(r"""
load(async (path, init) => json(200, { ok: true, got: JSON.parse(init.body) }));
const r = await window.sendJson('/api/room/resume', { text: 'hi' });
// Objects made in the page's context: compare their JSON.
assert.strictEqual(JSON.stringify(r), JSON.stringify({ ok: true, status: 200, data: { ok: true, got: { text: 'hi' } } }));
const bad = await window.sendJson('/api/room/resume', {}, {});
assert.strictEqual(bad.ok, true);
load(async () => json(500, { error: 'boom' }));
assert.strictEqual(JSON.stringify(await window.sendJson('/api/room/resume', {})),
  JSON.stringify({ ok: false, status: 500, data: { error: 'boom' } }));
""")

    def test_a_request_that_never_answers_is_given_up(self):
        # Its fetch ignores the signal, as a held send did: the deadline still ends it.
        self.run_node(r"""
load(() => new Promise(() => {}));
const t0 = Date.now();
await assert.rejects(window.sendJson('/api/room/resume', {}, { ms: 150 }),
  e => e.timeout === true && e.network === false && e.status === 0);
assert.ok(Date.now() - t0 < 1000);
""")

    def test_an_answer_whose_body_never_ends_is_given_up(self):
        self.run_node(r"""
load(async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) }));
await assert.rejects(window.sendJson('/api/feedback/preview', {}, { ms: 150 }), e => e.timeout === true);
""")

    def test_no_connection_is_said_as_such(self):
        self.run_node(r"""
load(async () => { throw new TypeError('Failed to fetch'); });
await assert.rejects(window.sendJson('/api/feedback/preview', {}), e => e.network === true && e.timeout === false);
""")

    def test_a_page_shown_again_gives_up_a_send_past_its_deadline(self):
        self.run_node(r"""
load(() => new Promise(() => {}));
const p = window.sendJson('/api/room/resume', {}, { ms: 60000 });
const now = Date.now;
Date.now = () => now() + 61000;
for (const f of listeners.visibilitychange) f();
await assert.rejects(p, e => e.timeout === true);
""")

    def test_a_held_send_leaves_the_queue_with_its_caller(self):
        # Offline (the hub went away): the send is held; given up, it is gone,
        # and the hub's return does not send it behind the page's back.
        self.run_node(r"""
let up = false, sends = 0;
load(async (path) => {
  if (path === '/api/room/resume') { sends++; if (!up) throw new TypeError('Failed to fetch'); return json(200, {}); }
  if (!up) throw new TypeError('Failed to fetch');
  return json(200, {});
});
await assert.rejects(window.fetch('/api/sessions'));
assert.strictEqual(window.HubResilience.offline, true);
await assert.rejects(window.sendJson('/api/room/resume', {}, { ms: 100 }), e => e.timeout === true);
assert.strictEqual(window.HubResilience.queued, 0);
up = true;
await window.HubResilience.probe();
assert.strictEqual(sends, 0);
""")

    def test_a_send_lost_while_the_hub_answers_is_not_replayed_for_ever(self):
        # #251 measured 140 replays a second: every answered poll flushed it again.
        self.run_node(r"""
let sends = 0;
load(async (path) => {
  if (path === '/api/room/resume') { sends++; throw new TypeError('Failed to fetch'); }
  return json(200, {});
});
const given = assert.rejects(window.sendJson('/api/room/resume', {}, { ms: 5000 }), e => e.network === true);
for (let i = 0; i < 10; i++) { await window.fetch('/api/room'); await new Promise(r => setTimeout(r, 5)); }
await given;
assert.strictEqual(sends, 4);       // the first try and three replays
assert.strictEqual(window.HubResilience.queued, 0);
""")

    def test_a_send_given_up_mid_replay_takes_out_only_itself(self):
        # Its caller gave up while its replay was answered 503: the next held
        # send stays queued and is delivered (review 1 of #251).
        self.run_node(r"""
let up = false, resumes = 0;
const ctl = new AbortController();
load(async (path) => {
  if (!up) throw new TypeError('Failed to fetch');
  if (path === '/api/send') { ctl.abort(); return json(503, {}); }
  if (path === '/api/room/resume') resumes++;
  return { ...json(200, {}), url: path };
});
await assert.rejects(window.fetch('/api/sessions'));
const send = assert.rejects(window.fetch('/api/send', { method: 'POST', signal: ctl.signal }));
const resume = window.fetch('/api/room/resume', { method: 'POST' });
assert.strictEqual(window.HubResilience.queued, 2);
up = true;
await window.HubResilience.probe();
await send;
await window.HubResilience.probe();
assert.strictEqual((await resume).status, 200);
assert.strictEqual(resumes, 1);
assert.strictEqual(window.HubResilience.queued, 0);
""")

    def test_the_hubs_health_check_gives_up(self):
        self.run_node(r"""
const seen = [];
load(async (path, init) => { if (path === '/api/health') { seen.push(!!(init && init.signal)); return new Promise(() => {}); } throw new TypeError('x'); });
await assert.rejects(window.fetch('/api/sessions'));
window.HubResilience.probe();
await new Promise(r => setTimeout(r, 20));
assert.deepStrictEqual(seen.slice(0, 1), [true]);
""")
