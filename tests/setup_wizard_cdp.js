/* Browser walk for the first-run wizard. chromeProfile is prepended by the Python test. */
const {spawn} = require('child_process');
const fs = require('fs'), path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
class Cdp {
  constructor(url) { this.ws = new WebSocket(url); this.id = 0; this.pending = new Map();
    this.ready = new Promise((ok, bad) => { this.ws.onopen = ok; this.ws.onerror = bad; });
    this.ws.onmessage = ev => { const m = JSON.parse(ev.data); const p = this.pending.get(m.id);
      if (p) { this.pending.delete(m.id); m.error ? p.bad(Error(m.error.message)) : p.ok(m.result); } };
  }
  send(method, params = {}, sessionId) { const id = ++this.id;
    return new Promise((ok, bad) => { this.pending.set(id, {ok, bad}); this.ws.send(JSON.stringify({id, method, params, sessionId})); }); }
}
async function main() {
  const profile = chromeProfile(A);
  const ch = spawn(A.chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
    '--use-mock-keychain', '--password-store=basic', '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--hide-scrollbars', 'about:blank'], {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((ok, bad) => { let buf = '';
    ch.stderr.on('data', d => { buf += d; const m = buf.match(/DevTools listening on (ws:\S+)/); if (m) ok(m[1]); });
    ch.on('exit', c => bad(Error('Chrome exited ' + c + ': ' + buf)));
    setTimeout(() => bad(Error('Chrome did not start: ' + buf)), 20000);
  });
  const c = new Cdp(ws); await c.ready;
  const result = {screens:[], phone:[], errors:[]};
  try {
    const {targetId} = await c.send('Target.createTarget', {url:'about:blank'});
    const {sessionId} = await c.send('Target.attachToTarget', {targetId, flatten:true});
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    const ev = async expression => { const r = await c.send('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true}, sessionId);
      if (r.exceptionDetails) throw Error(expression.slice(0, 100) + ': ' + JSON.stringify(r.exceptionDetails).slice(0, 500));
      return r.result.value; };
    const until = async (expression, ms = 30000) => { const start = Date.now();
      while (Date.now() - start < ms) { try { if (await ev(expression)) return; } catch (_) {} await sleep(120); }
      throw Error('Timed out: ' + expression); };
    const go = async url => { await c.send('Page.navigate', {url}, sessionId); await until('window.ensBooted === true'); };
    const click = async selector => { await ev(`document.querySelector(${JSON.stringify(selector)}).click()`); await sleep(80); };
    const set = async (selector, value) => ev(`document.querySelector(${JSON.stringify(selector)}).value = ${JSON.stringify(value)}`);
    const shown = `!document.getElementById('setup-wizard').hidden`;
    const state = async () => ev(`(() => { const h = document.getElementById('setup-wizard'), d = h.querySelector('.wz-card'),
      r = d?.getBoundingClientRect(), controls = [...h.querySelectorAll('button,input,select')].filter(x => x.getClientRects().length);
      return {visible:!h.hidden,title:h.querySelector('h2')?.textContent,step:h.querySelector('.wz-overline')?.textContent,
        width:innerWidth,scroll:document.documentElement.scrollWidth,card:r && {left:r.left,right:r.right,top:r.top,bottom:r.bottom},
        short:controls.filter(x => {const t=x.type==='checkbox' ? x.closest('label') || x : x, b=t.getBoundingClientRect(); return b.width<44 || b.height<44}).map(x=>x.id || x.textContent.trim())}; })()`);
    const settings = async () => ev(`fetch('/api/settings').then(r => r.json())`);
    const shot = async name => { if (!A.shots) return;
      const r = await c.send('Page.captureScreenshot', {format:'png'}, sessionId);
      fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    await c.send('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false}, sessionId);
    await go(A.base + '/#needs');
    result.deepLinkBypassed = !(await ev(shown));
    await go(A.base + '/'); await until(shown);
    const steps = ['One hub for your AI team','Which coding agents are ready?','What do you call a board?',
      'Are these your plan sizes?','How should work be shared?','Where should products live?',
      'How should Ensemble look?','Get notifications here?','Use Ensemble on another device?',
      'Start when you sign in?','Start with a product?','You’re set'];
    for (let i=0; i<steps.length; i++) {
      const s = await state(); result.screens.push(s); await shot('wizard-desktop-' + String(i+1).padStart(2,'0'));
      if (s.title !== steps[i]) throw Error('Desktop screen ' + i + ': ' + JSON.stringify(s));
      if (i === steps.length-1) break;
      if (i === 2) await click('[data-wz-noun="Product"]');
      if (i === 3) { await set('#wz-claude-weight','20'); await set('#wz-codex-weight','5'); }
      if (i === 4) { await click('[data-wz-prefer="codex"]'); await click('#wz-pace'); }
      if (i === 5) await set('#wz-root', A.root);
      if (i === 6) await click('[data-wz-theme="paper"]');
      if (i === 7) await ev(`NT.sub = {test:true}; document.querySelector('#wz-notify').checked = true`);
      if (i === 8) await click('#wz-remote');
      if (i === 9) await click('#wz-autostart');
      const oldTitle = s.title;
      await click(i === 1 ? '[data-wz-action="skip"]' : '[data-wz-action="next"]');
      await until(`document.querySelector('#setup-wizard h2')?.textContent !== ${JSON.stringify(oldTitle)}`);
    }
    result.settings = await settings();
    await click('[data-wz-action="board"]'); await until(`document.getElementById('setup-wizard').hidden`);
    result.done = await ev(`fetch('/api/setup/status').then(r => r.json())`);
    await ev(`SetupWizard.rerun()`); await until(shown);
    await click('[data-wz-action="later"]'); await until(`document.getElementById('setup-wizard').hidden`);
    result.later = await ev(`fetch('/api/setup/status').then(r => r.json())`);

    await c.send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true}, sessionId);
    await c.send('Emulation.setTouchEmulationEnabled', {enabled:true,maxTouchPoints:5}, sessionId);
    await ev(`SetupWizard.rerun()`); await until(shown);
    for (let i=0; i<steps.length; i++) {
      const s = await state(); result.phone.push(s); await shot('wizard-phone-' + String(i+1).padStart(2,'0'));
      if (s.title !== steps[i]) throw Error('Phone screen ' + i + ': ' + JSON.stringify(s));
      if (i === steps.length-1) break;
      if (i === 4) await click('[data-wz-prefer="claude"]');
      await click('[data-wz-action="skip"]');
      await until(`document.querySelector('#setup-wizard h2')?.textContent !== ${JSON.stringify(s.title)}`);
    }
    result.afterPhoneSkip = (await settings()).preferAgent;
    await click('[data-wz-action="board"]'); await until(`document.getElementById('setup-wizard').hidden`);
    await ev(`SetupWizard.rerun()`); await until(shown);
    for (let i=0; i<10; i++) {
      const title = (await state()).title;
      await click('[data-wz-action="skip"]');
      await until(`document.querySelector('#setup-wizard h2')?.textContent !== ${JSON.stringify(title)}`);
    }
    await set('#wz-project-name', 'First product');
    await click('[data-wz-action="next"]'); await until(`document.querySelector('#setup-wizard h2')?.textContent === 'You’re set'`);
    await click('[data-wz-action="create"]'); await until(`document.getElementById('setup-wizard').hidden`);
    result.projectCreated = await ev(`fetch('/api/projects').then(r => r.json()).then(x => x.projects.some(p => p.name === 'First product'))`);
  } finally {
    try { await c.send('Browser.close'); } catch (_) {}
    try { ch.kill(); } catch (_) {}
  }
  console.log(JSON.stringify(result));
}
main().catch(e => { console.error(e.stack || e); process.exit(1); });
