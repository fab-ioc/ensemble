// No request/content logging. GitHub credentials stay in Worker secrets.
export const MAX_BYTES = 32000;
const reply = (status, data) => Response.json(data, {status, headers: {'Cache-Control': 'no-store'}});

export async function readLimited(request) {
  if (Number(request.headers.get('content-length')) > MAX_BYTES) throw new Error('size');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('json');
  const chunks = []; let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BYTES) { await reader.cancel(); throw new Error('size'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const part of chunks) { bytes.set(part, offset); offset += part.length; }
  return JSON.parse(new TextDecoder().decode(bytes));
}

export async function ipKey(ip, secret, hour) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const bytes = await crypto.subtle.sign('HMAC', key, encoder.encode(`${hour}:${ip}`));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}

// One durable counter per hourly HMAC. Transaction prevents concurrent bypass;
// alarm clears the count. Neither raw IP nor content enters durable storage.
export class FeedbackLimit {
  constructor(state) { this.storage = state.storage; }
  async fetch() {
    const allowed = await this.storage.transaction(async tx => {
      const count = (await tx.get('count')) || 0;
      if (count >= 3) return false;
      await tx.put('count', count + 1);
      await tx.setAlarm(Date.now() + 2 * 3600000);
      return true;
    });
    return reply(allowed ? 200 : 429, {allowed});
  }
  async alarm() { await this.storage.deleteAll(); }
}

export async function handle(request, env, fetchGitHub = fetch) {
  if (request.method !== 'POST') return reply(405, {error: 'POST required'});
  if (!env.GITHUB_TOKEN || !env.IP_HASH_SECRET || !env.FEEDBACK_REPO || !env.LIMITS) return reply(503, {error: 'Relay not configured'});
  // Only trust Cloudflare's ingress header, never X-Forwarded-For supplied by a client.
  const ip = request.headers.get('CF-Connecting-IP');
  if (!ip) return reply(400, {error: 'Missing ingress address'});
  const hash = await ipKey(ip, env.IP_HASH_SECRET, Math.floor(Date.now() / 3600000));
  const limit = await env.LIMITS.get(env.LIMITS.idFromName(hash)).fetch('https://limit/');
  if (limit.status !== 200) return reply(429, {error: 'Three requests per hour', retryAfter: 3600});
  let data;
  try { data = await readLimited(request); }
  catch (e) { return reply(e.message === 'size' ? 413 : 400, {error: 'Invalid or oversized feedback'}); }
  if (!data || typeof data.repo !== 'string' || data.repo.toLowerCase() !== env.FEEDBACK_REPO.toLowerCase() || !['bug', 'idea'].includes(data.kind) ||
      typeof data.title !== 'string' || !data.title.trim() || data.title.length > 200 ||
      typeof data.body !== 'string' || !data.body.trim() || new TextEncoder().encode(data.body).length > 24000 ||
      Object.keys(data).some(k => !['repo', 'kind', 'title', 'body'].includes(k))) return reply(400, {error: 'Invalid feedback'});
  try {
    const response = await fetchGitHub(`https://api.github.com/repos/${env.FEEDBACK_REPO}/issues`, {
      method: 'POST', redirect: 'manual', signal: AbortSignal.timeout(20000),
      headers: {'Authorization': `Bearer ${env.GITHUB_TOKEN}`, 'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json', 'User-Agent': 'Ensemble-feedback', 'X-GitHub-Api-Version': '2022-11-28'},
      body: JSON.stringify({title: data.title, body: data.body, labels: ['feedback', data.kind]})
    });
    if (!response.ok) return reply(502, {error: 'GitHub refused the issue'});
    const result = await response.json();
    const escapedRepo = env.FEEDBACK_REPO.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (typeof result.html_url !== 'string' || !new RegExp(`^https://github\\.com/${escapedRepo}/issues/[0-9]+$`, 'i').test(result.html_url)) return reply(502, {error: 'Invalid GitHub reply'});
    return reply(201, {url: result.html_url});
  } catch { return reply(502, {error: 'Could not confirm GitHub delivery'}); }
}

export default {async fetch(request, env) {
  try { return await handle(request, env); }
  catch { return reply(503, {error: 'Relay unavailable'}); }
}};
