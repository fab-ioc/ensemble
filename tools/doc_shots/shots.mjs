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

async function capture(name, where, { width = 1440, height = 900, run, afterLoad, crop, requireVisible, expected } = {}) {
  await page.size(width, height, width < 600);
  await page.go(url(where), 2400);
  if (afterLoad) await afterLoad();
  if (run) { await page.eval(run); await sleep(900); }
  if (requireVisible) await page.until(`(() => { const el = document.querySelector(${JSON.stringify(requireVisible)});
    return !!el && el.getBoundingClientRect().height > 80; })()`);
  const check = await page.eval(`({theme: document.documentElement.getAttribute('data-theme'),
    visible: document.body.innerText.slice(0, 150000), title: document.title,
    values: [...document.querySelectorAll('input,textarea')].map(x => x.value).join(' '),
    attrs: [...document.querySelectorAll('[href],[src],[title],[aria-label],[data-path]')]
      .flatMap(x => ['href','src','title','aria-label','data-path'].map(a => x.getAttribute(a) || ''))
      .join(' ').slice(0, 150000)})`);
  if (check.theme !== 'paper') throw new Error(`${name}: theme is ${check.theme}`);
  if (expected && !check.visible.includes(expected)) throw new Error(`${name}: missing ${expected}`);
  const owner = String.fromCharCode(102, 97, 98);
  const leaked = new RegExp(['epyc', owner + 'io', '/home/' + owner, 'tail' + '4f', 'token' + '='].join('|'), 'i')
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

const wizardStep = index => `(async () => {
  await SetupWizard.rerun();
  for (let i = 0; i < ${index}; i++) {
    document.querySelector('#setup-wizard [data-wz-action="skip"]').click();
    await new Promise(resolve => setTimeout(resolve, 120));
  }
  return document.querySelector('#setup-wizard #wz-title')?.textContent;
})()`;

const passageComment = `(async () => {
  const t = [...document.querySelectorAll('#msgs .msg .text.md')]
    .find(e => e.textContent.includes('The search function is in place'));
  if (!t) throw Error('demo message missing');
  t.scrollIntoView({block:'center'});
  const walker = document.createTreeWalker(t, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const start = node.nodeValue.indexOf('search function');
    if (start >= 0) { getSelection().setBaseAndExtent(node, start, node, start + 15); break; }
  }
  if (!node) throw Error('demo passage missing');
  await new Promise(resolve => setTimeout(resolve, 350));
  const button = document.querySelector('.sel-bar:not([hidden]) .sel-cmt:not([hidden])');
  if (!button) throw Error('passage comment control missing');
  button.click();
  await new Promise(resolve => setTimeout(resolve, 200));
  const input = document.querySelector('.cmt-composer textarea');
  if (!input) throw Error('passage composer missing');
  input.value = 'Please check the empty ingredient case too.';
  input.dispatchEvent(new InputEvent('input', {bubbles:true}));
  document.querySelector('.cmt-composer .cmt-add').click();
  return true;
})()`;

try {
  await capture('overview', '/', { requireVisible: '.ibx-row' });
  await capture('board', '/', { run: `PD.dock.reveal('board'); setViewMode('board'); document.querySelector('.pd-board-max')?.click()` });
  await capture('inbox', `/?inbox=${data.project}`);
  await capture('po-chat', `/session?room=${data.po}&msg=${data.ask}&part=ask:0`);
  await capture('task-session', `/session?room=${data.active}&msg=${data.progress}`,
    { run: `document.querySelector('#team-all')?.click()` });
  await capture('task-terminal', `/session?room=${data.active}&msg=${data.progress}&agent=claude`,
    { requireVisible: '#agentcol .agent.open .term' });
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
  await capture('setup-welcome', '/', { run: wizardStep(0), expected: 'One hub for your AI team' });
  await capture('setup-board-word', '/', { run: wizardStep(2), expected: 'What do you call a board?' });
  await capture('setup-theme', '/', { run: wizardStep(6), expected: 'How should Ensemble look?' });
  await capture('setup-welcome-phone', '/', { width: 390, height: 844, run: wizardStep(0), expected: 'One hub for your AI team' });
  await capture('passage-review-comment', `/session?room=${data.active}&msg=${data.progress}`,
    { run: passageComment, expected: 'Please check the empty ingredient case too.' });
  await capture('file-changed-notice', `/fileview?path=${encodeURIComponent(data.file)}&line=5`, {
    afterLoad: async () => {
      fs.appendFileSync(data.file, '\n# Demo change waiting to be reloaded.\n');
      const time = Date.now() / 1000 + 2;
      fs.utimesSync(data.file, time, time);
    },
    run: `(async () => {
      ensUpd.lastInput = Date.now();
      fvChg.checkedAt = 0;
      await fvChg.tick();
      const notice = document.getElementById('fv-chg');
      if (notice.hidden) throw Error('file change was not detected: ' + JSON.stringify({seen:fvChg.seen, now:await fvChg.version(), pending:fvChg.pending}));
      return notice.textContent;
    })()`,
    expected: 'This file changed' });
} finally {
  page.close();
  await browser.close();
}
fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify(shots, null, 2) + '\n');
