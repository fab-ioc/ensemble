/* What's new (#339): while an update is available, a dot on the user button
   and a "What's new" item in its menu; the item opens a dialog with what the
   update brings, in plain words (the hub's /api/update-check: merge subjects
   or release notes, never a task number or a hash), and Install now, Later,
   Skip this version. Skip is stored on the hub, so every device agrees. */
(() => {
  const el = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const BTN_LABEL = 'You — settings and preferences';
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9.2"/><path d="M12 16.5v-9M8 11l4-4 4 4"/></svg>';
  let info = null;

  // "3 Nov", with the year when it is not this one.
  function day(at) {
    if (!at) return '';
    const d = new Date(at * 1000), now = new Date();
    const opts = {day: 'numeric', month: 'short'};
    if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString('en-GB', opts);
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  function headline(i) {
    if (i.kind === 'release') return `Ensemble ${i.latestVersion} is available`;
    const n = i.updates || i.behind || 0;
    return n === 1 ? '1 update available' : `${n} updates available`;
  }
  function subline(i) {
    if (i.kind === 'release') {
      return `You have ${i.currentVersion}.` + (i.latestDate ? ` Released ${day(i.latestDate)}.` : '');
    }
    const have = i.currentDate ? `You have the version from ${day(i.currentDate)}.` : 'You have an earlier version.';
    return have + (i.latestDate ? ` The newest is from ${day(i.latestDate)}.` : '');
  }
  const showing = i => !!(i && i.available);
  const chipOn = i => showing(i) && !i.skipped;

  // The chip and the menu item, from the latest check.
  function render(next) {
    info = next || null;
    const btn = el('me-btn'), item = el('me-whatsnew');
    if (!btn || !item) return;
    let dot = btn.querySelector('.upd-dot');
    if (!dot) {
      dot = document.createElement('span'); dot.className = 'upd-dot'; dot.setAttribute('aria-hidden', 'true');
      btn.append(dot);
    }
    dot.hidden = !chipOn(info);
    btn.setAttribute('aria-label', chipOn(info)
      ? `${BTN_LABEL}. ${headline(info)}: open What's new from this menu` : BTN_LABEL);
    item.hidden = !showing(info);
    item.classList.toggle('quiet', showing(info) && !!info.skipped);
    item.querySelector('.me-upd-tag').textContent = info && info.skipped ? 'skipped' : 'Update available';
    if (dialog.open && showing(info)) paint();
    else if (dialog.open) dialog.close();
  }

  const dialog = document.createElement('dialog');
  dialog.id = 'wn-dialog';
  dialog.setAttribute('aria-labelledby', 'wn-heading');
  dialog.innerHTML = `
    <div class="wn-head">${ICON}<h2 id="wn-heading"></h2></div>
    <p class="wn-sub" id="wn-sub"></p>
    <div class="wn-box" id="wn-box"></div>
    <p class="wn-wait" id="wn-wait" hidden></p>
    <div class="wn-actions">
      <button class="po-btn primary" type="button" id="wn-install">Install now</button>
      <button class="po-btn def" type="button" id="wn-later">Later</button>
      <button class="po-btn def skip" type="button" id="wn-skip">Skip this version</button>
    </div>`;
  document.body.append(dialog);

  function listHtml(i) {
    if (i.kind === 'release') {
      const notes = i.notes || [];
      const many = notes.length > 1;
      const md = s => (window.mdToHtml ? window.mdToHtml(s) : `<p>${esc(s)}</p>`);
      const body = notes.map(n => (many ? `<h3 class="wn-ver">Ensemble ${esc(n.version)}${n.at ? ` · ${esc(day(n.at))}` : ''}</h3>` : '')
        + (n.body ? md(n.body) : '<p>No notes for this release.</p>')).join('');
      return `<div class="wn-label">${many ? 'Release notes, newest first' : 'Release notes'}</div>`
        + (body || `<p>${esc(i.latestMessage || 'No notes for this release.')}</p>`)
        + (i.more ? `<div class="wn-more">and ${plural(i.more, 'earlier release', 'earlier releases')}</div>` : '');
    }
    const items = i.items || [];
    if (!items.length) return `<div class="wn-label">What's new</div><p>${esc(i.latestMessage || '')}</p>`;
    return `<div class="wn-label">What's new, newest first</div><ol>`
      + items.map(x => `<li tabindex="0" aria-expanded="false" title="${esc(x.text)}"><span class="t">${esc(x.text)}</span></li>`).join('')
      + `</ol>${i.more ? `<div class="wn-more">and ${i.more} more</div>` : ''}`;
  }
  function paint() {
    el('wn-heading').textContent = headline(info);
    el('wn-sub').textContent = subline(info);
    const box = el('wn-box');
    box.classList.toggle('wn-md', info.kind === 'release');
    const html = listHtml(info);
    if (box.dataset.sig !== html) { box.dataset.sig = html; box.innerHTML = html; }
    el('wn-skip').hidden = !!info.skipped;
    paintWait(info.wait);
  }
  function paintWait(w) {
    const p = el('wn-wait');
    p.hidden = !(w && w.agents > 0);
    if (p.hidden) return;
    const limit = `(at most ${plural(w.minutes || 20, 'minute', 'minutes')}, set in Settings)`;
    // A built app knows only once the release is downloaded whether agents
    // must stop for it (the hub ?wait=1 answer says maybe).
    p.textContent = w.maybe
      ? `${plural(w.agents, 'agent is', 'agents are')} working. If this release changes how agents run, Install now waits until each reaches a safe point ${limit}, then updates and restarts; otherwise only the hub restarts and agents keep working.`
      : `${plural(w.agents, 'agent is', 'agents are')} working. Install now waits until each reaches a safe point ${limit}, then updates and restarts the hub.`;
  }
  const toggleRow = li => {
    const open = li.classList.toggle('open');
    li.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  el('wn-box').addEventListener('click', e => {
    const li = e.target.closest('#wn-box ol > li');
    if (li && !getSelection().toString()) toggleRow(li);
  });
  el('wn-box').addEventListener('keydown', e => {
    const li = e.target.closest('#wn-box ol > li');
    if (li && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleRow(li); }
  });

  let returnTo = null;
  async function open() {
    if (!showing(info)) return;
    returnTo = document.activeElement;
    paint();
    if (!dialog.open) dialog.showModal();
    el('wn-install').focus();
    // Who Install would wait for, read now (the hub's cached check: no fetch).
    try {
      const r = await fetch('/api/update-check?wait=1');
      const got = r.ok ? await r.json() : null;
      // A hub that restarted since this page's last poll has no check yet:
      // keep what the page shows and let the ordinary poll check now (the
      // dialog itself never makes the hub check).
      if (got && got.reason === 'not_checked') {
        if (typeof window.checkForUpdate === 'function') window.checkForUpdate();
      } else if (got && dialog.open) render(got);
    } catch {}
  }
  dialog.addEventListener('close', () => {
    const b = returnTo; returnTo = null;
    const t = b && b.isConnected && b.getClientRects().length && b !== document.body ? b : el('me-btn');
    if (t) t.focus();
  });
  // Esc is Later; it closes this dialog only.
  dialog.addEventListener('cancel', e => e.stopPropagation());
  dialog.addEventListener('keydown', e => { if (e.key === 'Escape') e.stopPropagation(); });
  el('wn-later').onclick = () => dialog.close();
  el('wn-install').onclick = () => {
    dialog.close();
    if (window.triggerUpdate) window.triggerUpdate();
  };
  el('wn-skip').onclick = async () => {
    const b = el('wn-skip');
    b.disabled = true;
    try {
      const r = await sendJson('/api/update-skip', {skip: true}, {ms: 15000});
      if (!r.ok) throw new Error((r.data && (r.data.message || r.data.error)) || `HTTP ${r.status}`);
      dialog.close();
      render(r.data);
    } catch (e) {
      if (window.toast) window.toast('Not skipped: ' + (e.message || 'the hub did not answer'), 6000, 'err');
    } finally { b.disabled = false; }
  };

  window.whatsNew = {render, open, get info() { return info; }};
})();
