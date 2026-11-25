// Capture the fictional demo through the same pages a reader opens.
import fs from 'node:fs';
import path from 'node:path';
import { launch, tab, sleep } from './cdp.mjs';

const [base, json, out, chrome, debugPort] = process.argv.slice(2);
if (!base || !json || !out || !chrome || !debugPort) throw new Error('missing shot arguments');
const data = JSON.parse(fs.readFileSync(json, 'utf8'));
fs.mkdirSync(out, { recursive: true });
const browser = await launch({ chrome, port: Number(debugPort) });
const page = await tab(Number(debugPort));
const url = (where) => `${base}${where}`;
const shots = [];

async function capture(name, where, { width = 1440, height = 900, run } = {}) {
  await page.size(width, height, width < 600);
  await page.go(url(where), 2400);
  if (run) { await page.eval(run); await sleep(900); }
  const check = await page.eval(`({theme: document.documentElement.getAttribute('data-theme'),
    body: document.body.innerText.slice(0, 100000), title: document.title})`);
  if (check.theme !== 'paper') throw new Error(`${name}: theme is ${check.theme}`);
  const leaked = new RegExp(['epyc', 'fab' + 'io', '/home/' + 'fab', 'tail' + '4f', 'token' + '='].join('|'), 'i').exec(check.body);
  if (leaked) throw new Error(`${name}: private text ${leaked[0]}`);
  const file = path.join(out, `${name}.png`);
  await page.shot(file);
  shots.push({ name, page: where, width, height, title: check.title });
}

try {
  await capture('overview', '/');
  await capture('board', '/', { run: `PD.dock.reveal('board'); setViewMode('board'); document.querySelector('.pd-board-max')?.click()` });
  await capture('inbox', `/?inbox=${data.project}`);
  await capture('po-chat', `/session?id=${data.po}`);
  await capture('task-session', `/session?id=${data.active}`);
  await capture('task-terminal', `/session?id=${data.active}&agent=claude`);
  await capture('file-viewer', `/fileview?path=${encodeURIComponent(data.file)}&line=5`);
  await capture('settings-themes', '/', { run: 'openSettings("appearance")' });
  await capture('settings-notifications', '/', { run: 'openSettings("notify")' });
  await capture('settings-backup', '/', { run: 'openSettings("backup")' });
  await capture('board-phone', `/?task=${data.active}`, { width: 390, height: 844 });
  await capture('inbox-phone', `/?inbox=${data.project}`, { width: 390, height: 844 });
  await capture('po-chat-phone', `/session?id=${data.po}`, { width: 390, height: 844 });
} finally {
  page.close();
  await browser.close();
}
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(shots, null, 2) + '\n');
