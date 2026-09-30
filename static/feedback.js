/* Feedback is kept only in this page's memory. Preview is a server snapshot;
   editing anything invalidates it, and Send sends only its opaque id. */
(() => {
  const el = id => document.getElementById(id);
  const dialog = document.createElement('dialog');
  dialog.id = 'feedback-dialog';
  dialog.setAttribute('aria-labelledby', 'feedback-heading');
  dialog.innerHTML = `
    <h2 id="feedback-heading">Send feedback</h2>
    <form id="feedback-form">
      <label>Kind<select id="feedback-kind"><option value="bug">Bug</option><option value="idea">Idea</option></select></label>
      <label>Title<input id="feedback-title" maxlength="200" required></label>
      <label>Description<textarea id="feedback-description" maxlength="20000" required placeholder="What happened, or what would help? Paste screenshots here."></textarea></label>
      <div id="feedback-images"></div>
      <p class="feedback-hint">Images stay in this dialog. Named submitters can download and attach them on GitHub after sending; anonymous posts omit images.</p>
      <label class="feedback-check"><input type="checkbox" id="feedback-technical" checked>Include technical details</label>
      <label class="feedback-check"><input type="checkbox" id="feedback-anonymous">Send anonymously</label>
      <label id="feedback-name-label">Your name (for named relay feedback)<input id="feedback-name" maxlength="100" autocomplete="name"></label>
      <p class="feedback-hint">Screenshots and free text can still reveal who you are.</p>
      <div class="feedback-actions"><button class="po-btn primary" type="submit" id="feedback-preview-button">Preview</button><button class="po-btn def" type="button" id="feedback-close">Close</button></div>
    </form>
    <section id="feedback-preview" aria-label="Exactly what will be posted" hidden>
      <strong>Exactly what will be posted</strong><p id="feedback-route"></p>
      <strong id="feedback-post-title"></strong><pre id="feedback-post-body"></pre>
      <p>Attachments: none.</p>
      <button type="button" class="po-btn primary" id="feedback-send">Send</button>
    </section>
    <p id="feedback-status" role="status"></p>`;
  document.body.append(dialog);
  let draft = null, busy = false, revision = 0, completed = false;
  const images = [];
  const status = text => { el('feedback-status').textContent = text; };
  const invalidate = () => {
    revision++; draft = null; completed = false; el('feedback-preview').hidden = true;
    el('feedback-preview-button').className = 'po-btn primary';
    el('feedback-preview-button').textContent = 'Preview';
    el('feedback-name-label').hidden = el('feedback-anonymous').checked;
    status('');
  };
  function sending(value) {
    busy = value;
    for (const field of dialog.querySelectorAll('input, textarea, select, button')) field.disabled = value;
  }
  async function post(path, data) {
    const r = await fetch('/api/feedback/' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Feedback request failed. Your text is kept.');
    return d;
  }
  function link(url, text) {
    const a = document.createElement('a'); a.href = url; a.textContent = text;
    a.target = '_blank'; a.rel = 'noopener noreferrer'; el('feedback-status').append(' ', a);
  }
  window.feedbackOpen = () => {
    el('settings-panel').hidden = true;
    if (!dialog.open) dialog.showModal();
    el('feedback-title').focus();
  };
  el('feedback-close').onclick = () => dialog.close();
  dialog.addEventListener('cancel', e => { e.stopPropagation(); if (busy) e.preventDefault(); });
  dialog.addEventListener('keydown', e => { if (e.key === 'Escape') e.stopPropagation(); });
  el('feedback-form').addEventListener('input', invalidate);
  el('feedback-description').addEventListener('paste', e => {
    const files = attImages(e.clipboardData);
    if (!files.length) return;
    e.preventDefault(); invalidate();
    for (const file of files) {
      if (images.length >= 5 || file.size > 20 * 1024 * 1024) { status('Keep up to five images, at most 20 MB each.'); break; }
      const item = {url: URL.createObjectURL(file)}; images.push(item);
      const figure = document.createElement('figure'), img = document.createElement('img');
      img.src = item.url; img.alt = 'Screenshot kept locally'; figure.append(img);
      const a = document.createElement('a'); a.href = item.url;
      a.download = 'screenshot.' + ({'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[file.type] || 'png');
      a.textContent = 'Download'; figure.append(a);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'po-btn def'; button.textContent = 'Remove';
      button.onclick = () => { URL.revokeObjectURL(item.url); images.splice(images.indexOf(item), 1); figure.remove(); invalidate(); };
      figure.append(button); el('feedback-images').append(figure);
    }
  });
  el('feedback-form').onsubmit = async e => {
    e.preventDefault(); if (busy) return;
    const current = ++revision; sending(true); status('Preparing preview…');
    try {
      const ua = navigator.userAgent;
      const result = await post('preview', {kind: el('feedback-kind').value, title: el('feedback-title').value,
        description: el('feedback-description').value, name: el('feedback-name').value,
        anonymous: el('feedback-anonymous').checked, technical: el('feedback-technical').checked,
        browser: /Firefox/.test(ua) ? 'Firefox' : /Edg/.test(ua) ? 'Edge' : /Chrome/.test(ua) ? 'Chrome' : /Safari/.test(ua) ? 'Safari' : 'Other',
        page: location.pathname, width: innerWidth});
      if (current !== revision) return;
      draft = result;
      el('feedback-post-title').textContent = result.title; el('feedback-post-body').textContent = result.body;
      el('feedback-route').textContent = `Repository: ${result.repo}. Requested labels: feedback, ${result.kind}. ` +
        (result.route === 'gh' ? `Posted as GitHub user ${result.login}.` : result.route === 'relay' ?
          (result.anonymous ? 'Posted anonymously through the relay.' : 'Posted through the relay with your typed name.') :
          'No relay configured. GitHub requires login and posts under your account. This is not anonymous.') +
        (result.route !== 'relay' ? ' GitHub may omit labels without repository permission; the kind is included in the body.' : '');
      el('feedback-send').textContent = result.route === 'browser' ? 'Continue to GitHub options' : 'Send';
      el('feedback-send').hidden = false; el('feedback-preview').hidden = false;
      el('feedback-preview-button').className = 'po-btn def';
      el('feedback-preview-button').textContent = 'Preview again';
      status('Review the text above before sending. Editing a field requires a new preview.');
    } catch (e) { status(e.message || 'Could not prepare preview. Your text is kept.'); }
    finally { sending(false); }
  };
  el('feedback-send').onclick = async () => {
    if (!draft || busy || completed) return;
    sending(true); status('Sending…');
    try {
      const result = await post('send', {id: draft.id});
      if (result.ok) {
        completed = true; el('feedback-send').hidden = true;
        status(images.length && !draft.anonymous ? 'Issue created. Download the screenshots above, then drag them onto the GitHub issue.' : 'Issue created.');
        link(result.url, 'Open issue');
        if (result.warning) el('feedback-status').append(' ' + result.warning);
      } else {
        status(result.message || 'Feedback failed. Your text is kept.');
        if (result.fallback) link(result.url, 'Open GitHub (not anonymous)');
      }
    } catch { status('Could not confirm delivery. Check the repository before retrying; your text is kept.'); }
    finally { sending(false); }
  };
  fetch('/api/settings').then(r => r.json()).then(s => {
    el('feedback-repo').value = s.feedbackRepo || 'fab-ioc/ensemble';
    el('feedback-relay').value = s.feedbackRelayUrl || '';
  }).catch(() => {});
  window.feedbackSaveSettings = async () => {
    const repo = el('feedback-repo').value.trim(), relay = el('feedback-relay').value.trim();
    try {
      const r = await fetch('/api/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({feedbackRepo: repo, feedbackRelayUrl: relay})});
      const d = await r.json();
      if (!r.ok || d.feedbackRepo !== repo || d.feedbackRelayUrl !== relay) throw new Error('Use owner/repository and an HTTPS relay URL without credentials or query parameters.');
      el('feedback-settings-status').textContent = 'Saved.'; invalidate();
    } catch (e) { el('feedback-settings-status').textContent = e.message || 'Settings could not be saved.'; }
  };
})();
