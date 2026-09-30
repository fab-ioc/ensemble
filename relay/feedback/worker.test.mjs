import {test} from 'node:test';
import assert from 'node:assert/strict';
import {handle, FeedbackLimit, ipKey, MAX_BYTES} from './worker.mjs';

function fixture() {
  const counters = new Map();
  return {GITHUB_TOKEN: 'test-token', IP_HASH_SECRET: 'test-secret', FEEDBACK_REPO: 'fab-ioc/ensemble',
    LIMITS: {idFromName(name) { assert.match(name, /^[a-f0-9]{64}$/); return name; }, get(name) {
      if (!counters.has(name)) {
        const values = new Map(); let queue = Promise.resolve();
        const storage = {get: async k => values.get(k), put: async (k, v) => values.set(k, v), setAlarm: async () => {}, deleteAll: async () => values.clear(),
          transaction(fn) { const next = queue.then(() => fn(storage)); queue = next.catch(() => {}); return next; }};
        counters.set(name, new FeedbackLimit({storage}));
      }
      return counters.get(name);
    }}};
}
const payload = {repo: 'fab-ioc/ensemble', title: 'Bug', body: 'Description', kind: 'bug'};
const request = (data = payload, headers = {}) => new Request('https://relay/', {method: 'POST', headers: {'CF-Connecting-IP': '1.2.3.4', ...headers}, body: JSON.stringify(data)});
test('exact text and labels, credentials stay at GitHub', async () => {
  const env = fixture(); let sent;
  const r = await handle(request(), env, async (url, init) => { sent = {url, init}; return Response.json({html_url: 'https://github.com/fab-ioc/ensemble/issues/1'}); });
  assert.equal(r.status, 201);
  assert.deepEqual(JSON.parse(sent.init.body), {title: 'Bug', body: 'Description', labels: ['feedback', 'bug']});
  assert.equal(sent.init.headers.Authorization, 'Bearer test-token');
  assert.equal(sent.init.headers['CF-Connecting-IP'], undefined);
  assert.deepEqual(await r.json(), {url: 'https://github.com/fab-ioc/ensemble/issues/1'});
});
test('atomic cap: only three concurrent submissions per hourly hashed IP', async () => {
  const env = fixture(); let calls = 0;
  const results = await Promise.all(Array.from({length: 10}, () => handle(request(), env, async () => { calls++; return Response.json({html_url: 'https://github.com/fab-ioc/ensemble/issues/1'}); })));
  assert.equal(calls, 3); assert.equal(results.filter(r => r.status === 429).length, 7);
});
test('hash differs across secret, IP, and hourly windows', async () => {
  const hash = await ipKey('1.2.3.4', 'secret', 1);
  for (const args of [['1.2.3.4', 'secret', 2], ['1.2.3.5', 'secret', 1], ['1.2.3.4', 'other', 1]]) assert.notEqual(hash, await ipKey(...args));
});
test('size cap with and without length, UTF-8 body cap', async () => {
  assert.equal((await handle(request(payload, {'Content-Length': MAX_BYTES + 1}), fixture())).status, 413);
  assert.equal((await handle(request({...payload, body: 'x'.repeat(MAX_BYTES)}), fixture())).status, 413);
  assert.equal((await handle(request({...payload, body: 'é'.repeat(12001)}), fixture())).status, 400);
});
test('reject repo override, image data, bad kind, empty title without contacting GitHub', async () => {
  for (const data of [{...payload, repo: 'other/repo'}, {...payload, attachments: []}, {...payload, kind: 'other'}, {...payload, title: ''}, null]) {
    const r = await handle(request(data), fixture(), () => { throw Error('must not call'); }); assert.equal(r.status, 400);
  }
});
test('upstream refusal and timeout disclose no content or tokens', async () => {
  const r = await handle(request(), fixture(), async () => { throw Error('test-token private content'); });
  assert.equal(r.status, 502); assert.equal((await r.text()).includes('test-token'), false);
});
test('counter alarm deletes stored values', async () => {
  let deleted = false;
  await new FeedbackLimit({storage: {deleteAll: async () => { deleted = true; }}}).alarm();
  assert.equal(deleted, true);
});
