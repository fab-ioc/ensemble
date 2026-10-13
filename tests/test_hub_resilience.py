"""Browser queue behavior while the hub socket is briefly unavailable."""
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node is required for browser script checks")
class HubResilienceTest(unittest.TestCase):
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
  dispatchEvent() {}
};
const document = { body: { appendChild() {} }, getElementById() { return null; },
  createElement() { return { style: {}, setAttribute() {} }; }, addEventListener() {} };
const context = { window, document, Event: class {}, setTimeout() { return 1; } };
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
  dispatchEvent(e) { events.push(e.type); }
};
const context = { window, document, Event: class { constructor(type) { this.type = type; } },
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
