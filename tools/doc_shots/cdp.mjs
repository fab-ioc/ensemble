// A small Chrome DevTools Protocol client for the documentation shots: one
// headless Chrome with its own new profile, one tab, and the few calls the
// shot list needs (navigate, evaluate, click, type, screenshot).
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

export const sleep = ms => new Promise(r => setTimeout(r, ms));

export async function launch({ chrome, port, width = 1440, height = 900 }) {
  const occupied = await new Promise(resolve => {
    const s = net.connect({ host: '127.0.0.1', port });
    s.once('connect', () => { s.destroy(); resolve(true); });
    s.once('error', () => { s.destroy(); resolve(false); });
  });
  if (occupied) throw new Error(`Chrome debug port ${port} is in use; choose --chrome-port`);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'ens-doc-chrome-'));
  // The seed tests/chrome_profile.py writes (no blank-password probe on Windows).
  fs.writeFileSync(path.join(prof, 'Local State'), JSON.stringify({ password_manager: {
    os_password_blank: false, os_password_last_changed: String((BigInt(Date.now()) + 11644473600000n) * 1000n) } }));
  const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
    '--force-color-profile=srgb', `--window-size=${width},${height}`, 'about:blank'], { stdio: 'ignore' });
  let version;
  for (let i = 0; i < 100 && !version; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); if (r.ok) version = await r.json(); } catch {}
    if (!version) await sleep(200);
  }
  if (!version || proc.exitCode !== null) {
    proc.kill(); fs.rmSync(prof, { recursive: true, force: true });
    throw new Error(`the demo Chrome did not answer on ${port}`);
  }
  const close = async () => { proc.kill(); await sleep(400); fs.rmSync(prof, { recursive: true, force: true }); };
  return { port, close };
}

export async function tab(port) {
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    else if (m.method) events.push(m);
  });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const i = ++id; pending.set(i, m => m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result));
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send('Page.enable');
  await send('Runtime.enable');
  const page = {
    send, events,
    async size(width, height, mobile = false) {
      await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
      if (mobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true });
      else await send('Emulation.setTouchEmulationEnabled', { enabled: false });
    },
    async go(url, settle = 2500) {
      await send('Page.navigate', { url });
      await page.until('document.readyState === "complete"', 20000);
      await sleep(settle);
    },
    async eval(expr) {
      const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(`eval failed: ${expr.slice(0, 160)}\n${JSON.stringify(r.exceptionDetails).slice(0, 600)}`);
      return r.result.value;
    },
    async until(expr, ms = 15000) {
      const t = Date.now();
      while (Date.now() - t < ms) {
        try { if (await page.eval(`!!(${expr})`)) return; } catch {}
        await sleep(150);
      }
      throw new Error(`timed out waiting for ${expr.slice(0, 200)}`);
    },
    // The box of the first element a selector finds, in CSS pixels.
    async box(sel) {
      return page.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)});
        if (!e) return null; const r = e.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`);
    },
    async click(sel) {
      const b = await page.box(sel);
      if (!b) throw new Error(`no element ${sel}`);
      const x = b.x + b.width / 2, y = b.y + b.height / 2;
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased'])
        await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
    },
    async type(text) { await send('Input.insertText', { text }); },
    async shot(file, clip) {
      const p = { format: 'png', captureBeyondViewport: false };
      if (clip) p.clip = { ...clip, scale: 1 };
      const r = await send('Page.captureScreenshot', p);
      fs.writeFileSync(file, Buffer.from(r.data, 'base64'));
    },
    close() { ws.close(); },
  };
  return page;
}
