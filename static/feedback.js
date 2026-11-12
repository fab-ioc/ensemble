/* Feedback is kept only in this page's memory. Preview is a server snapshot;
   editing anything invalidates it, and Send sends only its opaque id.
   A send that worked (or a prefilled GitHub page that was opened) shows the
   done state with one primary Close, and the next open starts blank. Closed
   without sending, the text stays as a labelled unsent draft (#332). */
(() => {
  const el = id => document.getElementById(id);
  const dialog = document.createElement('dialog');
  dialog.id = 'feedback-dialog';
  dialog.setAttribute('aria-labelledby', 'feedback-heading');
  dialog.innerHTML = `
    <h2 id="feedback-heading">Send feedback</h2>
    <form id="feedback-form">
      <p class="feedback-draft" id="feedback-draft" hidden>Unsent draft from earlier. <button type="button" class="po-btn def" id="feedback-clear">Clear</button></p>
      <label>Kind<select id="feedback-kind"><option value="bug">Bug</option><option value="idea">Idea</option></select></label>
      <label>Title<input id="feedback-title" maxlength="200" required autocomplete="off" data-bwignore data-1p-ignore data-lpignore="true"></label>
      <label>Description<textarea id="feedback-description" maxlength="20000" required placeholder="What happened, or what would help? Paste screenshots here."></textarea></label>
      <div id="feedback-images"></div>
      <p class="feedback-hint">Images stay in this dialog. Named submitters can download and attach them on GitHub after sending; anonymous posts omit images.</p>
      <label class="feedback-check"><input type="checkbox" id="feedback-technical" checked>Include technical details</label>
      <label class="feedback-check"><input type="checkbox" id="feedback-anonymous">Send anonymously</label>
      <label id="feedback-name-label">Your name (for named relay feedback)<input id="feedback-name" maxlength="100" autocomplete="off" data-bwignore data-1p-ignore data-lpignore="true"></label>
      <p class="feedback-hint">Screenshots and free text can still reveal who you are.</p>
      <div class="feedback-actions"><button class="po-btn primary" type="submit" id="feedback-preview-button">Preview</button><button class="po-btn def" type="button" id="feedback-close">Close</button></div>
    </form>
    <section id="feedback-preview" aria-label="Exactly what will be posted" hidden>
      <strong>Exactly what will be posted</strong><p id="feedback-route"></p>
      <strong id="feedback-post-title"></strong><pre id="feedback-post-body"></pre>
      <p>Attachments: none.</p>
      <button type="button" class="po-btn primary" id="feedback-send">Send</button>
    </section>
    <p id="feedback-status" role="status"></p>
    <section id="feedback-done" aria-labelledby="feedback-done-heading" hidden>
      <div class="feedback-done-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M7.5 12.5l3 3 6-6.5"/></svg>
        <h2 id="feedback-done-heading"></h2></div>
      <p id="feedback-done-what"></p>
      <div class="feedback-sent"><span class="feedback-sent-label" id="feedback-sent-label"></span>
        <div class="feedback-sent-title"><span class="feedback-kind" id="feedback-sent-kind"></span><span id="feedback-sent-title"></span></div>
        <pre id="feedback-sent-body" tabindex="0"></pre></div>
      <p class="feedback-hint" id="feedback-done-hint" hidden></p>
      <div class="feedback-actions"><button type="button" class="po-btn primary" id="feedback-done-close">Close</button></div>
    </section>`;
  document.body.append(dialog);
  let draft = null, busy = false, revision = 0, completed = false, done = false;
  const images = [];
  const status = (text, error = false) => {
    el('feedback-status').textContent = text; el('feedback-status').className = error ? 'feedback-error' : '';
  };
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
  // Given up after 20 s, a send after 45 s (its relay or GitHub may take 25 s):
  // the dialog's busy state always ends (sendJson, static/hub-resilience.js).
  async function post(path, data, ms) {
    const r = await sendJson('/api/feedback/' + path, data, {ms});
    const d = r.data || {};
    if (!r.ok) throw new Error(d.error || `Feedback request failed (error ${r.status}). Your text is kept.`);
    return d;
  }
  function anchor(url, text) {
    const a = document.createElement('a'); a.href = url; a.textContent = text;
    a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  }
  const hasText = () => !!(el('feedback-title').value || el('feedback-description').value || images.length);
  // Blank again: title, text, images, kind and preview. Name and the two
  // checkboxes are the sender's preferences and stay.
  function reset() {
    for (const item of images) URL.revokeObjectURL(item.url);
    images.length = 0; el('feedback-images').replaceChildren();
    el('feedback-title').value = ''; el('feedback-description').value = ''; el('feedback-kind').value = 'bug';
    done = false; el('feedback-done').hidden = true; el('feedback-draft').hidden = true;
    el('feedback-form').hidden = false; el('feedback-heading').hidden = false; el('feedback-status').hidden = false;
    dialog.setAttribute('aria-labelledby', 'feedback-heading');
    invalidate();
  }
  // opened: the prefilled GitHub page (url) was opened; else the issue at result.url exists.
  function showDone(url, opened) {
    const sent = draft;
    done = true; completed = true;
    el('feedback-form').hidden = true; el('feedback-preview').hidden = true;
    el('feedback-heading').hidden = true; el('feedback-status').hidden = true;
    el('feedback-done').hidden = false; dialog.setAttribute('aria-labelledby', 'feedback-done-heading');
    el('feedback-done-heading').textContent = opened ? 'Opened on GitHub' : 'Feedback sent';
    el('feedback-sent-label').textContent = opened ? 'What was filled in' : 'What was sent';
    el('feedback-sent-kind').textContent = sent.kind === 'idea' ? 'Idea' : 'Bug';
    el('feedback-sent-title').textContent = sent.title; el('feedback-sent-body').textContent = sent.body;
    const what = el('feedback-done-what'), hint = el('feedback-done-hint');
    hint.replaceChildren(); hint.hidden = true;
    // The screenshots never leave this page: their links stay until Close.
    const downloads = where => {
      const many = images.length > 1;
      hint.append(`Download the screenshot${many ? 's' : ''} (`);
      images.forEach((item, i) => {
        const a = document.createElement('a'); a.href = item.url; a.download = item.name.replace('.', many ? `-${i + 1}.` : '.'); a.textContent = a.download;
        hint.append(...(i ? [', ', a] : [a]));
      });
      hint.append(`), then drag ${many ? 'them' : 'it'} onto the GitHub ${where}.`); hint.hidden = false;
    };
    if (opened) {
      if (images.length) { downloads('page'); hint.append(' '); }
      what.textContent = "GitHub's new-issue page opened in a new tab with your text filled in. Submit it there to finish; it posts under your GitHub account and is not anonymous.";
      hint.append('Tab closed by mistake? ', anchor(url, 'Open the GitHub page again'), '.'); hint.hidden = false;
    } else {
      const number = (/\/issues\/(\d+)$/.exec(url) || [])[1];
      what.replaceChildren('Issue ', anchor(url, number ? '#' + number : 'on GitHub'), ` was created in ${sent.repo}, ` +
        (sent.route === 'gh' ? `posted as GitHub user ${sent.login}.` : sent.anonymous ? 'posted anonymously through the relay.' : 'posted through the relay with your typed name.'));
      if (images.length && !sent.anonymous) downloads('issue');
      if (sent.warning) { hint.append(hint.hidden ? '' : ' ', sent.warning); hint.hidden = false; }
    }
    el('feedback-done-close').focus();
  }
  // Opened from Settings, the focus comes back to what opened Settings (or the
  // avatar) when the dialog closes, however it closes.
  let returnTo = null;
  window.feedbackOpen = () => {
    const from = document.activeElement;
    let back = null;
    if (window.closeSettings) back = window.closeSettings(false); else el('settings-panel').hidden = true;
    if (!dialog.open) {
      returnTo = back || from;
      if (done) reset();   // reopened before the close event of the done state ran
      el('feedback-draft').hidden = !hasText();
      dialog.showModal();
    }
    (done ? el('feedback-done-close') : el('feedback-title')).focus();
  };
  el('feedback-close').onclick = () => dialog.close();
  el('feedback-done-close').onclick = () => dialog.close();
  el('feedback-clear').onclick = () => { reset(); el('feedback-title').focus(); };
  // The close event is queued: a reopen may already have happened.
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    if (done) reset();
    const b = returnTo; returnTo = null;
    const t = b && b.isConnected && b.getClientRects().length && b !== document.body ? b : el('me-btn');
    if (t) t.focus();
  });
  dialog.addEventListener('cancel', e => { e.stopPropagation(); if (busy) e.preventDefault(); });
  dialog.addEventListener('keydown', e => { if (e.key === 'Escape') e.stopPropagation(); });
  el('feedback-form').addEventListener('input', invalidate);
  el('feedback-description').addEventListener('paste', e => {
    const files = attImages(e.clipboardData);
    if (!files.length) return;
    e.preventDefault(); invalidate();
    for (const file of files) {
      if (images.length >= 5 || file.size > 20 * 1024 * 1024) { status('Keep up to five images, at most 20 MB each.'); break; }
      const item = {url: URL.createObjectURL(file), name: 'screenshot.' + ({'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}[file.type] || 'png')};
      images.push(item);
      const figure = document.createElement('figure'), img = document.createElement('img');
      img.src = item.url; img.alt = 'Screenshot kept locally'; figure.append(img);
      const a = document.createElement('a'); a.href = item.url;
      a.download = item.name;
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
    } catch (e) {
      if (current !== revision) return;
      status(e.timeout ? 'The preview did not come back within 20 seconds. Your text is kept: press Preview again.'
        : e.network ? 'Could not reach Ensemble. Your text is kept: press Preview again.'
        : e.message || 'Could not prepare preview. Your text is kept.', true);
    }
    finally { sending(false); }
  };
  el('feedback-send').onclick = async () => {
    if (!draft || busy || completed) return;
    sending(true); status('Sending…');
    try {
      const result = await post('send', {id: draft.id}, 45000);
      if (result.ok) {
        sending(false); draft.warning = result.warning; showDone(result.url, false); return;
      }
      if (result.fallback) {
        // The text stays for a retry; opening the prefilled page finishes there.
        // unsure: the relay may have created the issue, so this is not "Not sent".
        status(`${result.unsure ? 'Could not confirm delivery.' : 'Not sent.'} ${result.reason} Your text is kept: ` + (draft.route === 'browser' ? '' : 'press Send to retry, or '), true);
        const a = anchor(result.url, 'open GitHub (not anonymous)');
        a.addEventListener('click', () => setTimeout(() => showDone(result.url, true)));
        el('feedback-status').append(a, draft.route === 'browser' ? ' to finish there.' : '.');
      } else status('Not sent. ' + (result.message || 'Feedback failed. Your text is kept.'), true);
    } catch { status('Could not confirm delivery. Check the repository before retrying; your text is kept.', true); }
    finally { if (!done) sending(false); }
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
