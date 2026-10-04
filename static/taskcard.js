// Task chips and the task card: one component for every page (#152).
//
// A task named by its number in text is a chip showing only the number — #27
// in the project shown, D-27 when it is another project's task — with a dot
// while the task runs. One click (a tap, or Enter) opens the card in place,
// over the text around it, never moving it: the number, the project when it
// is not the one shown, the full title (a link to the task), its column and
// what it is doing, its agents. A double click, or Enter again, opens the
// task; the page says how (TaskCard.init({ open })). Esc, a click elsewhere,
// scrolling or leaving the window closes the card. On a computer, resting the
// pointer on a chip opens the card after a moment; it stays while the pointer
// is on the chip or the card, and a click keeps it.
//
// TaskCard.chipHtml(t, o) draws the chip for a task the hub (/api/task/ref)
// or the board described: { roomId, label, ref, title, workflowName, status,
// agents, project, inProject }. TaskCard.refsIn(text, ctx) finds a text's
// references and says which project each bare number is read in (#156: a
// name right before it); TaskCard.refs(o) is the resolver for the pages
// without a board: it asks the hub once per number (and project), keeps the
// answer a minute, and has the page draw again (o.changed) when it comes.
// Design: skills/ensemble-design/SKILL.md, "Task chip and card".
// tests/test_task_refs.py and tests/test_task_chip_page.py run this file in
// Node (no document) and in headless Chrome.
const TaskCard = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const TTL = 60000;        // what the hub said is kept a minute
  const DOUBLE = 500;       // the second click of a double click, within this of the first
  const HOVER_IN = 500, HOVER_OUT = 300;

  // Text's task references (#18, #ED-18, @codex@18, and ED-18 on its own with
  // its key in capitals), as task_numbers.py reads them: the agent, the key
  // after # or @, the key of the bare form, the number.
  const TASK_REF_RE = /(?<![\w&\/#@.\\-])(?:(?:@([A-Za-z][\w-]*)@|#)(?:([A-Za-z][A-Za-z0-9]{0,5})-)?|([A-Z][A-Z0-9]{0,5})-)(\d{1,6})(?![\w-])/g;
  // A bare #12 after one of these words is someone else's number ("PR #12",
  // "fixes #34", "[Image #3]": an agent's placeholder for a pasted image), as
  // task_numbers.NOT_TASK_BEFORE reads it; ED-12 and @codex@12 are tasks.
  const NOT_TASK_BEFORE_RE = /(?:^|[^\w])(?:pr|mr|pull request|issue|bug|ticket|resolve|resolved|finding|step|item|point|round|option|question|comment|commit|line|page|part|phase|rule|case|image)s?\.?[ \t]*$/i;
  const notTaskRef = (who, key, s, at) => !who && !key && NOT_TASK_BEFORE_RE.test(s.slice(Math.max(0, at - 40), at));

  // ---- Which project a bare number is read in (#156) -----------------------
  // As task_numbers.read_project reads it on the hub (tests/test_task_refs.py
  // runs both on the same texts): a bare #27 right after a project's name —
  // "Dock #27", "Dock's #27", "the Dock project's #27", "in Dock: #27",
  // "Dock task #27", "Dock released #27", "Dock v0.11.0 (#27)", "Dock v0.11.0
  // with #27" — is that project's (how 'name'); else the context's own
  // (how ''). `others` are the other projects its sentence names before it
  // ("Answered Ensemble about #11"): too weak to read the number there, but
  // when one of them has the number too the number is ambiguous — the chip
  // keeps the own project and its card says it was assumed. ctx: { projects:
  // [{ id, key, name, aliases }], own, nouns } from /api/task/projects
  // (refs(o).ctx()), or the board's list.
  const SENTENCE_END_RE = /[.!?]+(?=\s|$)|\n[ \t]*\n|^[ \t]*#{1,6}[ \t][^\n]*|^[ \t]*(?:[-*+]|\d{1,3}[.)]|>|\|)(?=\s|$)|\|/gm;
  const NOUNS = ['project', 'board'];
  const POSS = "(?:['’]s)?", VERSION = '(?:\\s+[*_]*v?\\d+(?:\\.\\d+)+[*_]*)?', LINK = '(?:\\s+(?:\\w{2,}ed|with|as|task))?';
  const rxEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const blank = m => m.replace(/[^\n]/g, ' ');
  // The text with its code blocks and code spans blanked, the same length.
  const withoutCode = s => s.replace(/```[\s\S]*?(?:```|$)/g, blank).replace(/`[^`\n]*`/g, blank);
  function nameRes(ctx) {
    if (ctx._res !== undefined) return ctx._res;
    const by = new Map();
    for (const p of ctx.projects || []) for (const a of p.aliases || []) by.set(String(a).toLowerCase(), p.id);
    if (!by.size) return (ctx._res = null);
    const alts = [...by.keys()].sort((a, b) => b.length - a.length).map(rxEsc).join('|');
    const nouns = [...new Set([...NOUNS, ...(ctx.nouns || [])])].filter(Boolean).sort((a, b) => b.length - a.length).map(rxEsc).join('|');
    return (ctx._res = { by, names: new RegExp(`(?<![\\w-])(${alts})(?![\\w-])`, 'gi'),
      before: new RegExp(`(?<![\\w-])(${alts})${POSS}(?:\\s+(?:${nouns}))?${POSS}${VERSION}${LINK}(?:\\s*[:,–—-])?\\s*\\(?\\s*$`, 'i') });
  }
  // What the sentence holding `start` says before it.
  function sentenceBefore(plain, start) {
    let a = 0;
    for (const m of plain.slice(0, start).matchAll(SENTENCE_END_RE)) a = m.index + m[0].length;
    return plain.slice(a, start);
  }
  function readProject(plain, start, end, ctx) {
    const R = nameRes(ctx), own = ctx.own || '';
    if (!R) return { project: own, how: '', others: [] };
    const said = sentenceBefore(plain, start);
    const m = R.before.exec(said.slice(-80));
    if (m) return { project: R.by.get(m[1].toLowerCase()), how: 'name', others: [] };
    const others = [];
    for (const n of said.matchAll(R.names)) {
      const pid = R.by.get(n[1].toLowerCase());
      if (pid !== own && !others.includes(pid)) others.push(pid);
    }
    return { project: own, how: '', others };
  }
  // The task references in a text, in order, each mention once: { token, who,
  // key, no, start, end, project, how, others } as task_numbers.all_text_refs
  // gives them. Without a ctx a bare number's project is '' (the hub's default).
  function refsIn(text, ctx) {
    const plain = withoutCode(String(text ?? ''));
    const byKey = new Map(((ctx && ctx.projects) || []).filter(p => p.key).map(p => [String(p.key).toUpperCase(), p.id]));
    const out = [];
    for (const m of plain.matchAll(TASK_REF_RE)) {
      const who = m[1] || '', key = (m[2] || m[3] || '').toUpperCase(), no = +m[4], at = m.index;
      if (notTaskRef(who, key, plain, at)) continue;
      if (m[3] && ctx && !byKey.has(key)) continue;    // UTF-8, ISO-8601: a bare key no project has is not a task
      const r = { token: m[0], who, key, no, start: at, end: at + m[0].length, project: '', how: '', others: [] };
      if (key) { r.project = byKey.get(key) || ''; r.how = 'key'; }
      else if (ctx) Object.assign(r, readProject(plain, r.start, r.end, ctx));
      out.push(r);
    }
    return out;
  }
  // The text with each reference fn(ref) answers for replaced by its answer
  // (null: left as written); the words between go through `plain` when given
  // (a page escaping them for HTML: the references are read in the raw text).
  function replaceRefs(text, ctx, fn, plain = s => s) {
    const s = String(text ?? '');
    let out = '', at = 0;
    for (const r of refsIn(s, ctx)) {
      const h = fn(r);
      if (h == null) continue;
      out += plain(s.slice(at, r.start)) + h;
      at = r.end;
    }
    return out + plain(s.slice(at));
  }

  // ---- The resolver: a number → the hub's answer ----------------------------
  // o: { room: this chat's room (the hub reads #18 in its project), changed() }.
  // info(key, no, project): the answer, or { state: 'pending' | 'gone' };
  // `project` reads a bare number in another project (a PO's message, or one
  // a name near the number says). ctx(own): the context refsIn reads a text
  // in — the hub's project list (/api/task/projects, asked once and kept a
  // while; the page draws again when it comes), with `own` the project a
  // bare number is read in by default (this chat's unless given).
  const PROJECTS_TTL = 300000;
  function refs(o) {
    const cache = new Map();   // "#18", "ED-18", "#18@proj-…" → { state, at, ...the task }
    let projects = null, projectsAt = 0, loading = false;
    const ctxs = new Map();
    function loadProjects() {
      loading = true;
      fetch('/api/task/projects' + (o.room ? '?room=' + encodeURIComponent(o.room) : ''))
        .then(res => res.ok ? res.json() : Promise.reject(new Error('error ' + res.status)))
        .then(d => { projects = d && Array.isArray(d.projects) ? d : { projects: [], own: '', nouns: [] }; projectsAt = Date.now(); ctxs.clear(); loading = false; o.changed(); })
        .catch(() => { projectsAt = Date.now() - PROJECTS_TTL + 15000; loading = false; });   // asked again in a while
    }
    function ctx(own) {
      if (!loading && (!projects || Date.now() - projectsAt > PROJECTS_TTL)) loadProjects();
      if (!projects) return null;
      const k = own || '';
      let c = ctxs.get(k);
      if (!c) ctxs.set(k, c = { projects: projects.projects, own: k || projects.own || '', nouns: projects.nouns || [] });
      return c;
    }
    function info(key, no, project) {
      const ref = key ? key.toUpperCase() + '-' + no : '#' + no;
      const k = ref + (project && !key ? '@' + project : '');
      let r = cache.get(k);
      if (r && (r.state === 'pending' || Date.now() - r.at < TTL)) return r;
      if (r) r.at = Date.now();      // asked again once; the old answer shows meanwhile
      else cache.set(k, r = { state: 'pending' });
      const was = r.state === 'pending' ? '' : JSON.stringify(r);
      fetch('/api/task/ref?ref=' + encodeURIComponent(ref) + (o.room ? '&room=' + encodeURIComponent(o.room) : '')
            + (project && !key ? '&project=' + encodeURIComponent(project) : ''))
        .then(res => res.status === 404 ? { gone: true } : res.ok ? res.json() : Promise.reject(new Error('error ' + res.status)))
        .then(d => {
          const now = d && !d.gone && d.roomId ? Object.assign({ state: 'ok' }, d) : { state: 'gone' };
          const changed = JSON.stringify(Object.assign({}, now, { at: r.at })) !== was;
          cache.set(k, Object.assign(now, { at: Date.now() }));
          if (changed) o.changed();
        })
        // The hub did not answer: what it said before (or text) for now, asked again in a while.
        .catch(() => setTimeout(() => { if (r.state === 'pending') cache.delete(k); o.changed(); }, 15000));
      return r;
    }
    // The hub's answer for one of refsIn's references, read where its text
    // says: a keyed number as written; a bare number named another project's
    // in that project, and in `own` (the project bare numbers are read in: ''
    // this chat's, a PO message's sender's) when the named project has no
    // such task; else in `own`. `assumed` when another project its sentence
    // names before it has the number too: the card says which was assumed.
    function resolve(r, own) {
      if (r.key) return { t: info(r.key, r.no, ''), assumed: false };
      const c = ctx(own), ownId = c ? c.own : '';
      const ask = pid => info('', r.no, pid && pid !== ownId ? pid : (own || ''));
      if (r.how === 'name' && r.project && r.project !== ownId) {
        const t = ask(r.project);
        if (t.state !== 'gone') return { t, assumed: false };
      }
      return { t: ask(ownId), assumed: (r.others || []).some(pid => ask(pid).state === 'ok') };
    }
    return { info, cache, ctx, resolve };
  }

  // ---- The chip ---------------------------------------------------------------
  // Its column, and what it is doing when that is not the calm default.
  const stateText = t => [t.workflowName, t.status && t.status !== 'not running' ? t.status : ''].filter(Boolean).join(' · ');
  // The chip's word: as written with a key (ED-7), else #27 in the project
  // shown and D-27 for another project's task.
  const word = (t, key) => (key || t.inProject === false) ? (t.ref || t.label) : (t.label || t.ref);
  const dotOf = t => t.status === 'running' ? 'run' : (t.status === 'waiting for you' || t.status === 'paused') ? 'wait' : '';
  // o: { key: the key as written, agent: the agent it opens at, href: the link
  // (the page's own; else this page's /session), assumed: the number's
  // sentence names two projects and this one was assumed (the card says so) }.
  function chipHtml(t, o = {}) {
    const id = o.agent || '';
    const href = o.href || ('/session?id=' + encodeURIComponent(t.roomId) + (id ? '&agent=' + encodeURIComponent(id) : ''));
    const ref = t.ref || t.label || '';
    const card = { ref: word(t, o.key), title: t.title || '', state: stateText(t),
                   agents: (t.agents || []).map(a => a.identity || a.agent).filter(Boolean),
                   project: (t.inProject === false || o.assumed) ? (t.project || '') : '', href, task: t.roomId || '', agent: id };
    if (o.assumed) card.assumed = true;
    if (o.point) { card.point = o.point; card.state = o.state || ''; }
    const dot = dotOf(t);
    const label = `${o.point ? 'Point' : 'Task'} ${ref}: ${t.title || ''}` + (id ? ` at ${id}` : '') + (card.project ? ` (${card.assumed ? 'assumed ' : ''}${card.project})` : '');
    return `<a class="task-chip${o.point ? ' point-chip' : ''}${dot ? ' tc-' + dot : ''}" href="${esc(href)}" ${o.point ? pointAttrs(o.point) : `data-task="${esc(t.roomId || '')}"`}${id ? ` data-agent="${esc(id)}"` : ''}`
      + ` data-card="${esc(JSON.stringify(card))}" aria-label="${esc(label)}" aria-haspopup="dialog" aria-expanded="false">`
      + (dot ? '<span class="tc-dot" aria-hidden="true"></span>' : '')
      + `<span class="ref-who">${esc((id ? '@' + id + ' ' : '') + word(t, o.key))}</span></a>`;
  }

  const pointAttrs = p => `data-point="true" data-ref-room="${esc(p.room)}" data-ref-msg="${esc(p.msg)}" data-ref-part="${esc(p.part)}"`;

  // ---- The card ---------------------------------------------------------------
  const CSS = `
.msg .text.md a.task-chip, a.task-chip { display:inline-flex; align-items:center; gap:var(--s-100); vertical-align:baseline; box-sizing:border-box;
  padding:0 var(--s-100); background:var(--surface-sunken); color:var(--fg); border:1px solid var(--border); border-radius:var(--r-100);
  font-size:var(--fs-200); line-height:16px; font-weight:500; font-variant-numeric:tabular-nums; text-decoration:none; white-space:nowrap;
  cursor:pointer; user-select:none; -webkit-user-select:none; position:relative; }
a.task-chip:hover, a.task-chip[aria-expanded="true"] { background:var(--hover); }
a.task-chip:focus-visible { outline:none; box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--focus-ring); }
a.task-chip .ref-who { flex:0 0 auto; }
a.task-chip .tc-dot { flex:0 0 auto; width:6px; height:6px; border-radius:50%; background:var(--c-success-bold, var(--fg-muted)); }
a.task-chip.tc-wait .tc-dot { background:var(--c-warning-bold, var(--fg-muted)); }
.task-card { position:fixed; z-index:1200; display:flex; align-items:baseline; gap:var(--s-200); box-sizing:border-box;
  max-width:min(720px, 100vw - 16px); padding:var(--s-100) var(--s-200); background:var(--surface-overlay); color:var(--fg);
  border:1px solid var(--border); border-radius:var(--r-200); box-shadow:var(--e-200); font-size:var(--fs-200); line-height:20px; white-space:nowrap; }
.task-card .tc-no { flex:0 0 auto; font-weight:600; font-variant-numeric:tabular-nums; }
.task-card .tc-proj { flex:0 0 auto; color:var(--fg-muted); font-weight:500; }
.task-card .tc-title { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; color:var(--link); text-decoration:none; }
.task-card .tc-title:hover { text-decoration:underline; }
.task-card .tc-title:focus-visible { outline:none; box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--focus-ring); border-radius:var(--r-100); }
.task-card .tc-state, .task-card .tc-agents { flex:0 0 auto; color:var(--fg-muted); }
@media (pointer: coarse) {
  /* A chip keeps its look in the line of text and takes a finger's height with an invisible margin. */
  .msg .text.md a.task-chip, a.task-chip { overflow:visible; }
  a.task-chip::after { content:''; position:absolute; left:0; right:0; top:-14px; bottom:-14px; }
  /* The card wraps: the number, project and state on the first line, the full title under them. */
  .task-card { flex-wrap:wrap; white-space:normal; row-gap:0; font-size:var(--fs-300, var(--fs-200)); }
  .task-card .tc-title { order:9; flex:1 1 100%; white-space:normal; overflow:visible; min-height:var(--touch-min, 44px); display:flex; align-items:center; }
}`;

  const STATES = new WeakMap();   // document → its card's state
  function stateOf(doc) { return STATES.get(doc || document) || null; }
  function closeCard(S) {
    clearTimeout(S.timer); S.timer = 0;
    clearTimeout(S.leave); S.leave = 0;
    if (!S.el) return;
    S.el.remove();
    if (S.chip) S.chip.setAttribute('aria-expanded', 'false');
    S.el = null; S.chip = null; S.hover = false;
  }
  function place(S, el, chip) {
    const w = S.doc.defaultView, r = chip.getBoundingClientRect(), m = 8, gap = 4;
    el.style.left = '0px'; el.style.top = '0px';
    const cw = el.offsetWidth, ch = el.offsetHeight;
    const left = Math.max(m, Math.min(r.left, w.innerWidth - m - cw));
    let top = r.bottom + gap;
    if (top + ch > w.innerHeight - m && r.top - gap - ch >= m) top = r.top - gap - ch;
    el.style.left = left + 'px'; el.style.top = top + 'px';
  }
  function openCard(S, chip, hover) {
    let c = null;
    try { c = JSON.parse(chip.dataset.card || ''); } catch (e) { c = null; }
    if (!c) return;
    closeCard(S);
    const el = S.doc.createElement('div');
    el.className = 'task-card';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', (c.point ? 'Point ' : 'Task ') + c.ref);
    el.innerHTML = `<span class="tc-no">${esc(c.ref)}</span>`
      + (c.project ? `<span class="tc-proj${c.assumed ? ' tc-assumed' : ''}"${c.assumed ? ' title="The sentence names more than one project: this one was assumed"' : ''}>${c.assumed ? 'assumed ' : ''}${esc(c.project)}</span>` : '')
      + `<a class="tc-title" href="${esc(c.href)}" ${c.point ? pointAttrs(c.point) : `data-task="${esc(c.task)}"`}${c.agent ? ` data-agent="${esc(c.agent)}"` : ''} title="${esc((c.point ? 'Jump to point ' : 'Open task ') + c.ref)}">${esc(c.title)}</a>`
      + (c.state ? `<span class="tc-state">${esc(c.state)}</span>` : '')
      + (c.agents && c.agents.length ? `<span class="tc-agents">${esc(c.agents.join(' · '))}</span>` : '');
    S.doc.body.appendChild(el);
    place(S, el, chip);
    chip.setAttribute('aria-expanded', 'true');
    S.el = el; S.chip = chip; S.at = Date.now(); S.hover = !!hover;
  }

  // o: { doc: the document (this one), open(a): open the task a chip names }.
  function init(o = {}) {
    const doc = o.doc || document;
    if (STATES.has(doc)) return STATES.get(doc);
    const w = doc.defaultView;
    const st = doc.createElement('style');
    st.id = 'task-card-style';
    st.textContent = CSS;
    doc.head.appendChild(st);
    const S = { doc, el: null, chip: null, at: 0, hover: false, timer: 0, leave: 0,
                openTask: a => a.dataset.point && o.openPoint ? o.openPoint(a) : (o.open || (a => { w.location.href = a.href; }))(a) };
    STATES.set(doc, S);
    const chipOf = ev => (ev.target && ev.target.closest) ? ev.target.closest('a.task-chip') : null;
    const inCard = ev => !!(S.el && ev.target && ev.target.closest && ev.target.closest('.task-card'));
    const fine = () => { try { return w.matchMedia('(hover: hover) and (pointer: fine)').matches; } catch (e) { return false; } };
    doc.addEventListener('click', ev => {
      const a = chipOf(ev);
      if (!a) {
        if (inCard(ev) && ev.target.closest('a')) closeCard(S);   // the title: the page opens the task
        return;
      }
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;   // with a modifier it is a link
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const same = !!(S.el && S.chip === a);
      if (ev.detail === 0) {           // Enter: the card, then the task
        if (same) { closeCard(S); S.openTask(a); } else openCard(S, a);
        return;
      }
      if (same && (S.hover || Date.now() - S.at < DOUBLE)) { S.hover = false; return; }   // a hover card is kept; a double click's second click
      if (same) closeCard(S); else openCard(S, a);
    }, true);
    doc.addEventListener('dblclick', ev => {
      const a = chipOf(ev);
      if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      if (a.dataset.point && w.matchMedia('(pointer: coarse)').matches) return;
      closeCard(S);
      S.openTask(a);
    }, true);
    doc.addEventListener('pointerdown', ev => {
      if (S.el && !inCard(ev) && chipOf(ev) !== S.chip) closeCard(S);
    }, true);
    doc.addEventListener('keydown', ev => {
      if (ev.key !== 'Escape' || !S.el) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const chip = S.chip;
      closeCard(S);
      try { chip.focus({ preventScroll: true }); } catch (e) {}
    }, true);
    doc.addEventListener('scroll', ev => { if (S.el && !inCard(ev)) closeCard(S); }, true);
    w.addEventListener('resize', () => closeCard(S));
    w.addEventListener('blur', () => closeCard(S));
    // Hover, on a computer: the card after a moment; it stays while the pointer is on the chip or the card.
    doc.addEventListener('pointerover', ev => {
      if (ev.pointerType === 'touch' || !fine()) return;
      const a = chipOf(ev);
      // Back on the open card's chip, or on the card: it stays. (Another chip
      // does not keep it: the card would be left open with the pointer gone.)
      if ((a && S.chip === a) || inCard(ev)) { clearTimeout(S.leave); S.leave = 0; }
      if (a && !(S.el && S.chip === a) && !S.timer) {
        S.timer = setTimeout(() => {
          S.timer = 0;
          if (a.isConnected && !(S.el && !S.hover)) openCard(S, a, true);   // never over a card that was clicked open
        }, HOVER_IN);
      }
    }, true);
    doc.addEventListener('pointerout', ev => {
      if (ev.pointerType === 'touch') return;
      const a = chipOf(ev), to = ev.relatedTarget;
      if (a && S.timer && !(to && a.contains(to))) { clearTimeout(S.timer); S.timer = 0; }
      if (S.el && S.hover && (a === S.chip || inCard(ev))) {
        if (to && (S.el.contains(to) || (S.chip && S.chip.contains(to)))) return;
        clearTimeout(S.leave);
        S.leave = setTimeout(() => { if (S.hover) closeCard(S); }, HOVER_OUT);
      }
    }, true);
    return S;
  }

  return { refs, chipHtml, stateText, init, TASK_REF_RE, NOT_TASK_BEFORE_RE, notTaskRef, refsIn, replaceRefs, readProject,
           open: (chip, doc) => { const S = stateOf(doc); if (S) openCard(S, chip); },
           close: doc => { const S = stateOf(doc); if (S) closeCard(S); }, el: doc => { const S = stateOf(doc); return S ? S.el : null; },
           CSS, esc };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TaskCard;
