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
// agents, project, inProject }. TaskCard.refs(o) is the resolver for the pages
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

  // Text's task references (#18, #ED-18, @codex@18), as task_numbers.py reads them.
  const TASK_REF_RE = /(?<![\w&\/#@.\\-])(?:@([A-Za-z][\w-]*)@|#)(?:([A-Za-z][A-Za-z0-9]{0,5})-)?(\d{1,6})(?![\w-])/g;
  // A bare #12 after one of these words is someone else's number ("PR #12",
  // "fixes #34", "[Image #3]": an agent's placeholder for a pasted image), as
  // task_numbers.NOT_TASK_BEFORE reads it; ED-12 and @codex@12 are tasks.
  const NOT_TASK_BEFORE_RE = /(?:^|[^\w])(?:pr|mr|pull request|issue|bug|ticket|resolve|resolved|finding|step|item|point|round|option|question|comment|commit|line|page|part|phase|rule|case|image)s?\.?[ \t]*$/i;
  const notTaskRef = (who, key, s, at) => !who && !key && NOT_TASK_BEFORE_RE.test(s.slice(Math.max(0, at - 40), at));

  // ---- The resolver: a number → the hub's answer ----------------------------
  // o: { room: this chat's room (the hub reads #18 in its project), changed() }.
  // info(key, no, project): the answer, or { state: 'pending' | 'gone' };
  // `project` reads a bare number in another project (a PO's message).
  function refs(o) {
    const cache = new Map();   // "#18", "ED-18", "#18@proj-…" → { state, at, ...the task }
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
    return { info, cache };
  }

  // ---- The chip ---------------------------------------------------------------
  // Its column, and what it is doing when that is not the calm default.
  const stateText = t => [t.workflowName, t.status && t.status !== 'not running' ? t.status : ''].filter(Boolean).join(' · ');
  // The chip's word: as written with a key (ED-7), else #27 in the project
  // shown and D-27 for another project's task.
  const word = (t, key) => (key || t.inProject === false) ? (t.ref || t.label) : (t.label || t.ref);
  const dotOf = t => t.status === 'running' ? 'run' : (t.status === 'waiting for you' || t.status === 'paused') ? 'wait' : '';
  // o: { key: the key as written, agent: the agent it opens at, href: the link
  // (the page's own; else this page's /session) }.
  function chipHtml(t, o = {}) {
    const id = o.agent || '';
    const href = o.href || ('/session?id=' + encodeURIComponent(t.roomId) + (id ? '&agent=' + encodeURIComponent(id) : ''));
    const ref = t.ref || t.label || '';
    const card = { ref: word(t, o.key), title: t.title || '', state: stateText(t),
                   agents: (t.agents || []).map(a => a.identity || a.agent).filter(Boolean),
                   project: t.inProject === false ? (t.project || '') : '', href, task: t.roomId || '', agent: id };
    const dot = dotOf(t);
    const label = `Task ${ref}: ${t.title || ''}` + (id ? ` at ${id}` : '') + (card.project ? ` (${card.project})` : '');
    return `<a class="task-chip${dot ? ' tc-' + dot : ''}" href="${esc(href)}" data-task="${esc(t.roomId || '')}"${id ? ` data-agent="${esc(id)}"` : ''}`
      + ` data-card="${esc(JSON.stringify(card))}" aria-label="${esc(label)}" aria-haspopup="dialog" aria-expanded="false">`
      + (dot ? '<span class="tc-dot" aria-hidden="true"></span>' : '')
      + `<span class="ref-who">${esc((id ? '@' + id + ' ' : '') + word(t, o.key))}</span></a>`;
  }

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
    el.setAttribute('aria-label', 'Task ' + c.ref);
    el.innerHTML = `<span class="tc-no">${esc(c.ref)}</span>`
      + (c.project ? `<span class="tc-proj">${esc(c.project)}</span>` : '')
      + `<a class="tc-title" href="${esc(c.href)}" data-task="${esc(c.task)}"${c.agent ? ` data-agent="${esc(c.agent)}"` : ''} title="${esc('Open task ' + c.ref)}">${esc(c.title)}</a>`
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
                openTask: o.open || (a => { w.location.href = a.href; }) };
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

  return { refs, chipHtml, stateText, init, TASK_REF_RE, NOT_TASK_BEFORE_RE, notTaskRef,
           open: (chip, doc) => { const S = stateOf(doc); if (S) openCard(S, chip); },
           close: doc => { const S = stateOf(doc); if (S) closeCard(S); }, el: doc => { const S = stateOf(doc); return S ? S.el : null; },
           CSS, esc };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TaskCard;
