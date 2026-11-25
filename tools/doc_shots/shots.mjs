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

async function capture(name, where, { width = 1440, height = 900, run, crop } = {}) {
  await page.size(width, height, width < 600);
  await page.go(url(where), 2400);
  if (run) { await page.eval(run); await sleep(900); }
  const check = await page.eval(`({theme: document.documentElement.getAttribute('data-theme'),
    visible: document.body.innerText.slice(0, 150000), title: document.title,
    values: [...document.querySelectorAll('input,textarea')].map(x => x.value).join(' '),
    attrs: [...document.querySelectorAll('[href],[src],[title],[aria-label],[data-path]')]
      .flatMap(x => ['href','src','title','aria-label','data-path'].map(a => x.getAttribute(a) || ''))
      .join(' ').slice(0, 150000)})`);
  if (check.theme !== 'paper') throw new Error(`${name}: theme is ${check.theme}`);
  const leaked = new RegExp(['epyc', 'fab' + 'io', '/home/' + 'fab', 'tail' + '4f', 'token' + '='].join('|'), 'i')
    .exec([check.visible, check.title, check.values, check.attrs].join(' '));
  if (leaked) throw new Error(`${name}: private text ${leaked[0]}`);
  const file = path.join(out, `${name}.png`);
  const box = crop && await page.box(crop);
  if (crop && !box) throw new Error(`${name}: missing crop target ${crop}`);
  await page.shot(file, box && {
    x: Math.max(0, Math.floor(box.x - 12)), y: Math.max(0, Math.floor(box.y - 12)),
    width: Math.min(width - Math.max(0, Math.floor(box.x - 12)), Math.ceil(box.width + 24)),
    height: Math.min(height - Math.max(0, Math.floor(box.y - 12)), Math.ceil(box.height + 24)) });
  shots.push({ name, page: where, width, height, title: check.title });
}

try {
  await capture('overview', '/');
  await capture('board', '/', { run: `PD.dock.reveal('board'); setViewMode('board'); document.querySelector('.pd-board-max')?.click()` });
  await capture('inbox', `/?inbox=${data.project}`);
  await capture('po-chat', `/session?room=${data.po}&msg=${data.ask}&part=ask:0`);
  await capture('task-session', `/session?room=${data.active}&msg=${data.progress}`,
    { run: `document.querySelector('#team-all')?.click()` });
  await capture('task-terminal', `/session?room=${data.active}&msg=${data.progress}&agent=claude`,
    { run: `document.querySelector('#agentcol .agent .ah')?.click()` });
  await capture('file-viewer', `/fileview?path=${encodeURIComponent(data.file)}&line=5`);
  await capture('settings-themes', '/', { run: 'openSettings("appearance")' });
  await capture('settings-notifications', '/', { run: 'openSettings("notify")' });
  await capture('settings-backup', '/', { run: 'openSettings("backup")' });
  await capture('usage-plan', '/', { run: `document.querySelector('#usage-chip').click()`, crop: '#usage-tray' });
  await capture('board-phone', '/', { width: 390, height: 844,
    run: `PD.dock.reveal('board'); setViewMode('board')` });
  await capture('task-session-phone', `/session?room=${data.active}&msg=${data.progress}`,
    { width: 390, height: 844 });
  await capture('inbox-phone', `/?inbox=${data.project}`, { width: 390, height: 844 });
  await capture('po-chat-phone', `/session?room=${data.po}&msg=${data.ask}&part=ask:0`, { width: 390, height: 844 });
} finally {
  page.close();
  await browser.close();
}
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(shots, null, 2) + '\n');
