/* First-run setup. Each answer is the same Settings key, saved through PUT /api/settings. */
(function () {
  const ids = ['welcome', 'agents', 'noun', 'plans', 'lineup', 'folder', 'theme', 'notify', 'remote', 'autostart', 'project', 'done'];
  const themes = [['light','Light','#F7F8F9','#FFFFFF'],['dark','Dark','#1D2125','#22272B'],['dim','Dim','#242320','#2B2A27'],['paper','Paper','#F6F3EC','#FFFDF8'],['contrast','High contrast','#FFFFFF','#FFFFFF'],['fjord','Fjord','#2E3440','#3B4252'],['intellij-dark','IntelliJ Dark','#1E1F22','#2B2D30'],['system','Match system','#F7F8F9','#FFFFFF']];
  const state = { step: 0, settings: null, status: null, agents: null, usage: null, theme: '', noun: null, projectName: '', poAgent: '', busy: false, error: '' };
  const host = () => document.getElementById('setup-wizard');
  const escW = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const word = () => (state.noun || state.settings?.projectNoun || {one:'Project',many:'Projects'}).one;
  const plural = () => (state.noun || state.settings?.projectNoun || {one:'Project',many:'Projects'}).many;
  const label = (name, field, hint = '') => `<label class="wz-label" for="${field}">${name}</label>${hint ? `<div class="wz-muted">${hint}</div>` : ''}`;
  const choice = (value, text, group, selected) => `<button type="button" class="wz-choice" data-wz-${group}="${escW(value)}" aria-pressed="${selected ? 'true' : 'false'}">${escW(text)}</button>`;
  const note = s => `<div class="wz-note">${s}</div>`;
  const preview = title => `<div class="wz-preview"><strong>${title}</strong><div class="wz-preview-layout"><div class="wz-preview-side"><i></i><i></i><i></i></div><div class="wz-preview-main"><i></i><i></i><i></i></div></div></div>`;
  const pref = k => state.settings?.[k];
  const readyAgents = () => (state.agents?.agents || []).filter(a => a.ready);
  const body = () => {
    const step = ids[state.step];
    if (step === 'welcome') return `<h2>One hub for your AI team</h2><p>Run Claude Code and Codex as a team on your ${escW(plural().toLowerCase())}. A product owner agent plans each ${escW(word().toLowerCase())} into tasks, with an owner and reviewer for each task. Follow and steer the work from the board and chat, on any device.</p><p>Let’s make a few choices. You can change them in Settings later.</p>${note('<strong>About 3 minutes</strong><br>Skip any step. We’ll keep the defaults until you choose otherwise.')}${preview('Ensemble · Board')}`;
    if (step === 'agents') {
      const agents = state.agents?.agents || [];
      return `<h2>Which coding agents are ready?</h2><p>Ensemble checked this computer. One ready agent is enough to start.</p>${agents.map(a => `<div class="wz-row"><div><strong>${escW(a.name)}</strong><span class="wz-muted">${a.installed ? (a.signedIn === false ? 'Installed · sign in needed' : 'Installed · signed in') : 'Not installed'}</span></div><span class="${a.ready ? 'wz-muted' : 'wz-error'}">${a.ready ? 'Ready' : a.installed ? 'Sign in' : 'Install'}</span></div>${a.ready ? '' : note(a.installed ? `${escW(a.signIn || '')} · <button type="button" class="wz-skip" data-wz-check>Check again</button>` : `<a href="${escW(a.installUrl || '#')}" target="_blank" rel="noopener">Install ${escW(a.name)}</a> · <button type="button" class="wz-skip" data-wz-check>Check again</button>`)}`).join('') || '<p>Checking…</p>'}<p class="wz-muted">You can check them again in Settings › Coding agents.</p>`;
    }
    if (step === 'noun') {
      const n = state.noun || pref('projectNoun') || {one:'Project',many:'Projects'};
      const presets = [['Project','Projects'],['Epic','Epics'],['Initiative','Initiatives'],['Product','Products']];
      const custom = !presets.some(([one,many]) => n.one === one && n.many === many);
      return `<h2>What do you call a board?</h2><p>Choose the word that fits the work you organise. We’ll use it throughout Ensemble.</p><div class="wz-label">One board</div><div class="wz-choices">${presets.map(([one,many]) => choice(one,one,'noun',!custom && n.one === one)).join('')}${choice('other','My own word','noun',custom)}</div><div id="wz-noun-custom"${custom ? '' : ' hidden'}>${label('One','wz-noun-one')}<input id="wz-noun-one" value="${escW(custom ? n.one : '')}" maxlength="40">${label('Several','wz-noun-many')}<input id="wz-noun-many" value="${escW(custom ? n.many : '')}" maxlength="40"></div>${note('The next screens use your choice straight away. Change it later in Settings › Preferences › Call a board.')}`;
    }
    if (step === 'plans') {
      const plans = state.usage?.plans || {};
      const select = kind => {
        const k = `${kind}PlanWeight`, v = pref(k) ?? '', det = plans[kind] || {};
        const detected = det.label ? `${det.label}${det.weight == null ? '' : ` · ${det.weight}×`} detected` : 'Use detected plan';
        const weights = [...new Set([1,5,20,100,...(v !== '' ? [Number(v)] : [])])].sort((a,b) => a-b);
        return `${label(kind === 'claude' ? 'Claude plan' : 'Codex plan',`wz-${kind}-weight`)}<select id="wz-${kind}-weight"><option value=""${v === '' ? ' selected' : ''}>${escW(detected)}</option>${weights.map(n => `<option value="${n}"${Number(v) === n ? ' selected' : ''}>${n}× plan weight</option>`).join('')}</select>`;
      };
      return `<h2>Are these your plan sizes?</h2><p>Ensemble detects your plans and uses their sizes to share work across weekly limits.</p>${select('claude')}${select('codex')}${note('Leave detected unless your plan differs. These are the same weight overrides in Settings › Agent models.')}`;
    }
    if (step === 'lineup') return `<h2>How should work be shared?</h2><p>Choose how Ensemble starts task owners and reviewers.</p><div class="wz-label">Prefer an agent</div><div class="wz-choices">${['claude','codex','balanced'].map(x => choice(x,x[0].toUpperCase()+x.slice(1),'prefer',pref('preferAgent') === x)).join('')}</div><p class="wz-muted">Balanced uses each agent’s remaining room and weekly pace. A task can keep its own line-up.</p><label class="wz-check"><input type="checkbox" id="wz-pace"${pref('paceWeek') === false ? '' : ' checked'}> Pace each agent’s week</label>${note('Also in Settings › Agent models › Prefer and Weekly pacing.')}`;
    if (step === 'folder') {
      const hasProjects = typeof PROJECTS !== 'undefined' && !!PROJECTS?.projects?.length;
      const article = /^[aeiou]/i.test(word()) ? 'an' : 'a';
      return `<h2>Where should ${escW(plural().toLowerCase())} live?</h2><p>Ensemble makes one home for your ${escW(plural().toLowerCase())}, task folders and their knowledge.</p>${label(`${escW(plural())} folder`,'wz-root')}<input id="wz-root" value="${escW(pref('projectsRoot') || state.status?.projectsRoot || '')}"${hasProjects ? ' disabled' : ''}><p class="wz-muted">${hasProjects ? `An existing ${escW(plural().toLowerCase())} folder stays where it is.` : `Choose an empty folder before making ${article} ${escW(word().toLowerCase())}.`}</p>${note(`The location is also in Settings › ${escW(plural())} folder.`)}`;
    }
    if (step === 'theme') return `<h2>How should Ensemble look?</h2><p>Try a theme. The board preview changes at once; Next saves your choice.</p><div class="wz-label">Theme</div><div class="wz-choices">${themes.map(([id,name,bg,surface]) => `<button type="button" class="wz-choice wz-theme" data-wz-theme="${id}" aria-pressed="${state.theme === id}"><i style="--wz-bg:${bg};--wz-surface:${surface}"></i>${escW(name)}</button>`).join('')}</div>${preview(`Ensemble · ${escW(themes.find(x => x[0] === state.theme)?.[1] || 'Light')} preview`)}<p class="wz-muted">Accents and other look choices stay in Settings › Appearance.</p>`;
    if (step === 'notify') {
      const blocker = typeof ntBlocker === 'function' ? ntBlocker() : null;
      return `<h2>Get notifications here?</h2><p>Know when a task needs you, even when Ensemble is closed on this device.</p>${note(`<strong>${escW(typeof ntLabel === 'function' ? ntLabel() : 'This device')}</strong><br>${escW(blocker?.why || blocker?.tab || 'Your browser may ask for permission.')}`)}<label class="wz-check"><input type="checkbox" id="wz-notify"${typeof NT !== 'undefined' && NT.sub ? ' checked' : ''}${blocker ? ' disabled' : ''}> Notify me on this device</label><p class="wz-muted">Also in Settings › Notifications.</p>`;
    }
    if (step === 'remote') {
      const address = state.status?.tailnetAddress || '';
      const override = state.status?.bindOverride || '';
      return `<h2>Use Ensemble on another device?</h2><p>Open this hub from a device on the same tailnet, with its access token.</p><div class="wz-row"><div><strong>Tailnet</strong><span class="wz-muted">${address ? 'Address detected' : 'No address detected yet'}</span></div></div>${label('Address','wz-remote-address')}<input id="wz-remote-address" readonly value="${escW(address || 'Connect Tailscale to see your address')}"><label class="wz-check"><input type="checkbox" id="wz-remote"${pref('remoteAccessEnabled') ? ' checked' : ''}> Use Ensemble on my tailnet</label>${note(override ? `This launch uses a bind override (${escW(override)}), which takes priority over this saved choice. The choice applies at the next launch without an override.` : 'After the next hub launch, remote use is available on your tailnet. The access token remains in ~/.ensemble/access-token.txt on this computer. Manage this in Settings › Remote use.')}`;
    }
    if (step === 'autostart') return `<h2>Start when you sign in?</h2><p>Ensemble can open with your computer, ready when you need it.</p><label class="wz-check"><input type="checkbox" id="wz-autostart"${state.status?.autostart?.enabled ? ' checked' : ''}${state.status?.autostart?.supported ? '' : ' disabled'}> Start Ensemble at sign-in</label>${note(state.status?.autostart?.supported ? 'You can change this in Settings › Ensemble as an app.' : 'Available for an installed app on this computer. This installation keeps its current startup behavior.')}`;
    if (step === 'project') {
      const agents = readyAgents();
      return `<h2>Start with a ${escW(word().toLowerCase())}?</h2><p>Give the first board a name. Its PO helps plan and run the work with you.</p>${label(`${escW(word())} name · optional`,'wz-project-name')}<input id="wz-project-name" value="${escW(state.projectName)}" placeholder="e.g. My product">${label('PO agent','wz-po-agent')}<select id="wz-po-agent"><option value="">No PO for now</option>${agents.map(a => `<option value="${escW(a.key)}"${state.poAgent === a.key ? ' selected' : ''}>${escW(a.name)}</option>`).join('')}</select><p class="wz-muted">Agents ready on this computer are offered.</p>${note(`The ${escW(word().toLowerCase())} is created only when you choose Create ${escW(word().toLowerCase())} on the final screen. Skip keeps the board empty.`)}`;
    }
    return `<div class="wz-done"><div class="wz-done-mark">✓</div><h2>You’re set</h2><p>Your choices are saved in Settings. Run this setup again from there whenever you want.</p>${note(`<strong>Your workspace</strong><br>${escW(word())} boards · ${escW(themes.find(x => x[0] === state.theme)?.[1] || 'Light')} theme<br>${escW(pref('projectsRoot') || state.status?.projectsRoot || '')}`)}<p class="wz-muted">Open the board, or create and open your first ${escW(word().toLowerCase())}.</p></div>`;
  };
  function paint() {
    const el = host(); if (!el || el.hidden) return;
    const headingHadFocus = document.activeElement?.id === 'wz-title';
    const last = state.step === ids.length - 1, n = Math.min(state.step + 1, ids.length - 1);
    el.innerHTML = `<div class="wz-card" role="dialog" aria-modal="true" aria-labelledby="wz-title"><div class="wz-head"><span class="wz-brand">Ensemble</span><button type="button" class="wz-later" data-wz-action="later">Set up later</button></div><div class="wz-body"><div class="wz-overline">${last ? 'Finish' : `First run · ${n} of ${ids.length - 1}`}</div>${body().replace('<h2>', '<h2 id="wz-title" tabindex="-1">')}${state.error ? `<p class="wz-error" role="alert">${escW(state.error)}</p>` : ''}</div>${last ? '' : `<div class="wz-progress">${Array.from({length:ids.length-1},(_,i) => `<b class="${i < n ? 'active' : ''}"></b>`).join('')}</div>`}<div class="wz-foot">${state.step && !last ? '<button type="button" class="wz-default" data-wz-action="back">Back</button>' : ''}<span class="grow"></span>${last ? `<button type="button" class="wz-default" data-wz-action="board">Open board</button>${state.projectName.trim() ? `<button type="button" class="wz-primary" data-wz-action="create">Create ${escW(word().toLowerCase())} →</button>` : ''}` : `<button type="button" class="wz-skip" data-wz-action="skip">Skip</button><button type="button" class="wz-primary" data-wz-action="next">${state.step ? 'Next →' : 'Get started →'}</button>`}</div></div>`;
    el.querySelectorAll('button').forEach(b => { if (state.busy) b.disabled = true; });
    if (headingHadFocus) el.querySelector('#wz-title')?.focus();
  }
  function rememberInputs() {
    if (ids[state.step] === 'project') { state.projectName = host().querySelector('#wz-project-name')?.value || ''; state.poAgent = host().querySelector('#wz-po-agent')?.value || ''; }
    if (ids[state.step] === 'noun' && host().querySelector('#wz-noun-custom')?.hidden === false) state.noun = {one:host().querySelector('#wz-noun-one')?.value || '',many:host().querySelector('#wz-noun-many')?.value || ''};
  }
  async function put(body) {
    const r = await api('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    state.settings = r; PREFS = r; renderPrefs();
    return r;
  }
  function endPreview() { if (window.cdTheme) cdTheme.preview(null); }
  async function saveStep() {
    const step = ids[state.step], box = host();
    if (step === 'noun') {
      const custom = box.querySelector('#wz-noun-custom')?.hidden === false;
      const noun = custom ? {one:box.querySelector('#wz-noun-one')?.value.trim(),many:box.querySelector('#wz-noun-many')?.value.trim()} : state.noun || pref('projectNoun');
      if (!noun?.one || !noun?.many) throw Error('Give the word for one board and several boards.');
      const r = await put({projectNoun:noun});
      if (r.projectNoun.one !== noun.one || r.projectNoun.many !== noun.many) throw Error('That word could not be saved. Use letters, digits and spaces, up to 40 characters.');
      state.noun = r.projectNoun; setNoun(r.projectNoun); applyNouns();
    } else if (step === 'plans') await put({claudePlanWeight:box.querySelector('#wz-claude-weight').value, codexPlanWeight:box.querySelector('#wz-codex-weight').value});
    else if (step === 'lineup') await put({preferAgent:box.querySelector('[data-wz-prefer][aria-pressed=true]')?.dataset.wzPrefer || 'balanced', paceWeek:box.querySelector('#wz-pace').checked});
    else if (step === 'folder' && !box.querySelector('#wz-root').disabled) {
      const want = box.querySelector('#wz-root').value.trim(); const r = await put({projectsRoot:want});
      state.status.projectsRoot = r.projectsRoot || state.status.projectsRoot;
    } else if (step === 'theme') {
      const r = await put({theme:state.theme});
      try { localStorage.setItem('cd-theme', r.theme); } catch(e) {}
      endPreview(); cdTheme.apply();
    } else if (step === 'notify') {
      const want = box.querySelector('#wz-notify').checked;
      if (want && !NT.sub) { const msg = await ntTurnOn(); if (!msg.startsWith('On.')) throw Error(msg); }
      else if (!want && NT.sub) await ntTurnOff();
      else await ntSaveChoice(want);
    } else if (step === 'remote') await put({remoteAccessEnabled:box.querySelector('#wz-remote').checked});
    else if (step === 'autostart' && !box.querySelector('#wz-autostart').disabled) await put({startAtSignIn:box.querySelector('#wz-autostart').checked});
    else if (step === 'project') rememberInputs();
  }
  async function finish(create) {
    if (create && state.projectName.trim()) {
      const body = {name:state.projectName.trim(),kind:'code',path:''};
      const res = state.poAgent ? await api('/api/projects/po-from-session', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,fresh:state.poAgent})})
                                : await api('/api/projects/new', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      await put({setupWizardDone:true,setupWizardPending:false});
      host().hidden = true; endPreview();
      await refreshProjects(); await refresh({now:true});
      if (res.project?.id) openProjectPage(res.project.id);
    } else {
      await put({setupWizardDone:true,setupWizardPending:false});
      host().hidden = true; endPreview(); renderRows();
    }
  }
  async function advance(skip) {
    if (state.busy) return;
    state.busy = true; state.error = '';
    try {
      if (!skip) await saveStep();
      else {
        if (ids[state.step] === 'noun') state.noun = state.settings.projectNoun;
        if (ids[state.step] === 'theme') { endPreview(); state.theme = state.settings.theme || 'light'; }
        if (ids[state.step] === 'project') { state.projectName = ''; state.poAgent = ''; }
      }
      if (state.step < ids.length - 1) state.step++;
    } catch(e) { state.error = e.message || String(e); }
    state.busy = false; paint(); host()?.querySelector('#wz-title')?.focus();
  }
  async function open(status) {
    const el = host(); if (!el) return;
    state.status = status || await api('/api/setup/status');
    state.settings = await api('/api/settings');
    state.theme = state.settings.theme || cdTheme.cached() || 'light';
    state.noun = state.settings.projectNoun;
    state.step = 0; state.error = ''; state.busy = false; state.projectName = ''; state.poAgent = '';
    el.hidden = false; paint(); el.querySelector('#wz-title')?.focus();
    Promise.allSettled([api('/api/app/agents'),api('/api/usage')]).then(([a,u]) => {
      if (a.status === 'fulfilled') { state.agents = a.value; state.poAgent = readyAgents()[0]?.key || ''; }
      if (u.status === 'fulfilled') state.usage = u.value;
      if (!el.hidden) paint();
    });
  }
  async function rerun() { closeSettings(false); await open(await api('/api/setup/status')); }
  host()?.addEventListener('keydown', ev => {
    if (ev.key !== 'Tab') return;
    const items = [...host().querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]')]
      .filter(x => x.getClientRects().length);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (ev.shiftKey && (document.activeElement === first || document.activeElement.id === 'wz-title')) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    }
  });
  host()?.addEventListener('click', async ev => {
    const b = ev.target.closest('button'); if (!b || state.busy) return;
    if (b.dataset.wzNoun) {
      const preset = {Project:'Projects',Epic:'Epics',Initiative:'Initiatives',Product:'Products'};
      state.noun = b.dataset.wzNoun === 'other' ? {one:'',many:''} : {one:b.dataset.wzNoun,many:preset[b.dataset.wzNoun]}; paint(); return;
    }
    if (b.dataset.wzPrefer) {
      host().querySelectorAll('[data-wz-prefer]').forEach(choice =>
        choice.setAttribute('aria-pressed', String(choice === b)));
      return;
    }
    if (b.dataset.wzTheme) { state.theme = b.dataset.wzTheme; cdTheme.preview({theme:state.theme}); paint(); return; }
    if (b.hasAttribute('data-wz-check')) { state.agents = await api('/api/app/agents?refresh=1'); paint(); return; }
    const act = b.dataset.wzAction;
    if (act === 'back') { rememberInputs(); if (ids[state.step] === 'theme') endPreview(); state.step--; state.error = ''; paint(); host().querySelector('#wz-title')?.focus(); }
    else if (act === 'next' || act === 'skip') advance(act === 'skip');
    else if (act === 'later' || act === 'board' || act === 'create') {
      state.busy = true; state.error = ''; paint();
      try { await finish(act === 'create'); } catch(e) { state.error = e.message || String(e); state.busy = false; paint(); }
    }
  });
  document.getElementById('setup-run-again')?.addEventListener('click', rerun);
  window.SetupWizard = {open, rerun, start: async status => { if (status?.show) await open(status); }};
})();
