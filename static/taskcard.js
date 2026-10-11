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
// is on the chip or the card, and a click keeps it. TaskCard.anchors gives
// the same card to elements that are not chips (a left bar's task numbers,
// #201): their click stays the page's, the card comes on a rest, on keyboard
// focus or on a long press, and survives the page drawing them again
// (TaskCard.refresh).
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
  const LONG = 500;         // a long press on a touch screen (an anchor's card)

  // Text's task references (#18, #ED-18, @codex@18, and ED-18 on its own with
  // its key in capitals), as task_numbers.py reads them: the agent, the key
  // after # or @, the key of the bare form, the number.
  // A number never starts with 0 (S-03 is a rule, not a task). After a slash
  // only in a list of numbers (#140/#142), never in a path (Documents/#30).
  const TASK_REF_RE = /(?<![\w&#@.\\-])(?:(?:@([A-Za-z][\w-]*)@|#)(?:([A-Za-z][A-Za-z0-9]{0,5})-)?|([A-Z][A-Z0-9]{0,5})-)(?!0)(\d{1,6})(?![\w-])/g;
  // The whole list before it must be numbers from its start: not Documents/#140/#142.
  const LIST_ITEM_RE = /#[1-9]\d{0,5}\/$/, LIST_START_RE = /[^\w\/\\#@.&-]/;
  function slashListOk(plain, at) {     // walks back over the whole list (task_numbers._slash_list_ok)
    let i = at;
    while (i && plain[i - 1] === '/') {
      const m = LIST_ITEM_RE.exec(plain.slice(Math.max(0, i - 8), i));
      if (!m) return false;
      i -= m[0].length;
    }
    return i === 0 || LIST_START_RE.test(plain[i - 1]);
  }
  // "task 70" written as a word: the number is the reference (#202).
  const TASK_WORD_RE = /(?<![\w#@-])(?:[Tt]ask|TASK)[ \t]+(?!0)(\d{1,6})(?![\w-]|[.,]\d)/g;
  // A task's room id, not in a path, a URL's query or a file name (#202).
  const ROOM_REF_RE = /(?<![\w\/\\=.#@&?-])room-[0-9a-f]{8}(?![\w-]|\.\w)/g;
  // The hub writes "task #200 *its title*": the title is read with the number.
  // A Markdown link as the pages read it (session.html's LINK_RE, one level
  // of parentheses in the target): its words are read, its target is not.
  const MD_LINK_RE = /(\[[^\[\]\n]+\]\()((?:[^()\n]|\([^()\n]*\))+)\)/g;
  const TITLE_AFTER_RE = /^[ \t]+\*([^*\n]+)\*/;
  const TASK_BEFORE_RE = /(?:^|[^\w])task[ \t]+$/i;
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
  // gives them. Without a ctx a bare number's project is '' (the hub's
  // default). For the pages only (#202; the hub reads what agents are given
  // as before): o.words, "task 70" as the reference 70; o.rooms, room ids as
  // { token, room, how: 'room' }; o.titles, the italic title after "task
  // #200" as r.title { text, end }.
  function refsIn(text, ctx, o = {}) {
    const plain = withoutCode(String(text ?? ''));
    const byKey = new Map(((ctx && ctx.projects) || []).filter(p => p.key).map(p => [String(p.key).toUpperCase(), p.id]));
    const out = [];
    const numbered = r => {
      if (r.key) { r.project = byKey.get(r.key) || ''; r.how = 'key'; }
      else if (ctx) Object.assign(r, readProject(plain, r.start, r.end, ctx));
      if (o.titles && TASK_BEFORE_RE.test(plain.slice(Math.max(0, r.start - 12), r.start))) {
        const t = TITLE_AFTER_RE.exec(plain.slice(r.end));
        if (t) r.title = { text: t[1], end: r.end + t[0].length };
      }
      out.push(r);
    };
    for (const m of plain.matchAll(TASK_REF_RE)) {
      const who = m[1] || '', key = (m[2] || m[3] || '').toUpperCase(), no = +m[4], at = m.index;
      if (plain[at - 1] === '/' && !(m[0][0] === '#' && slashListOk(plain, at))) continue;
      if (notTaskRef(who, key, plain, at)) continue;
      if (m[3] && ctx && !byKey.has(key)) continue;    // UTF-8, ISO-8601: a bare key no project has is not a task
      numbered({ token: m[0], who, key, no, start: at, end: at + m[0].length, project: '', how: '', others: [] });
    }
    if (o.words) for (const m of plain.matchAll(TASK_WORD_RE)) {
      const at = m.index + m[0].length - m[1].length;
      numbered({ token: m[1], who: '', key: '', no: +m[1], start: at, end: at + m[1].length, project: '', how: '', others: [] });
    }
    // A Markdown link's target ([open](room-…)) is the link's, not a mention.
    if (o.rooms) for (const m of plain.replace(MD_LINK_RE, (m, words, target) => words + ' '.repeat(target.length) + ')').matchAll(ROOM_REF_RE)) {
      out.push({ token: m[0], room: m[0], who: '', key: '', no: 0, start: m.index, end: m.index + m[0].length, project: '', how: 'room', others: [] });
    }
    return out.sort((a, b) => a.start - b.start);
  }
  // A code span that is a task reference and nothing else (`#201`, `ED-201`,
  // `room-…`), read as refsIn reads it (#202); anything else in a code span
  // stays code. A colour is not a task: `#111`, `#000000`.
  function codeRef(body, ctx) {
    const t = String(body ?? '').trim();
    if (/^#(?:(\d)\1\1|(\d)\2{5}|\d{6})$/.test(t)) return null;
    const rs = refsIn(t, ctx, { rooms: true });
    return rs.length === 1 && rs[0].start === 0 && rs[0].end === t.length ? rs[0] : null;
  }
  // The text with each reference fn(ref) answers for replaced by its answer
  // (null: left as written); the words between go through `plain` when given
  // (a page escaping them for HTML: the references are read in the raw text).
  // Room ids and titles are read (o, refsIn's); a title whose chip took it
  // (chipHtml with { title: r.title }) goes with the chip.
  function replaceRefs(text, ctx, fn, plain = s => s, o = { rooms: true, titles: true, words: true }) {
    const s = String(text ?? '');
    let out = '', at = 0;
    for (const r of refsIn(s, ctx, o)) {
      if (r.start < at) continue;
      const h = fn(r);
      if (h == null) continue;
      out += plain(s.slice(at, r.start)) + h;
      at = r.title && r.title.taken ? r.title.end : r.end;
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
    // A room id is asked as it is (room-…); the hub says 404 for a deleted task.
    function info(key, no, project, room) {
      const ref = room || (key ? key.toUpperCase() + '-' + no : '#' + no);
      const k = ref + (project && !key && !room ? '@' + project : '');
      if (room) project = '';
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
      if (r.room) return { t: info('', 0, '', r.room), assumed: false };
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
  // The card's data for a task (what a chip carries in data-card); o as
  // chipHtml's, and `why`: a line under it saying why the task waits.
  function cardOf(t, o = {}) {
    const id = o.agent || '';
    const href = o.href || ('/session?id=' + encodeURIComponent(t.roomId) + (id ? '&agent=' + encodeURIComponent(id) : ''));
    const card = { ref: word(t, o.key), title: t.title || '', state: stateText(t),
                   agents: (t.agents || []).map(a => a.identity || a.agent).filter(Boolean),
                   project: (t.inProject === false || o.assumed) ? (t.project || '') : '', href, task: t.roomId || '', agent: id };
    if (o.assumed) card.assumed = true;
    if (o.point) { card.point = o.point; card.state = o.state || ''; }
    if (o.why) card.why = String(o.why);
    return card;
  }
  function chipHtml(t, o = {}) {
    const id = o.agent || '';
    const ref = t.ref || t.label || '';
    const card = cardOf(t, o), href = card.href;
    const dot = dotOf(t);
    const label = `${o.point ? 'Point' : 'Task'} ${ref}: ${t.title || ''}` + (id ? ` at ${id}` : '') + (card.project ? ` (${card.assumed ? 'assumed ' : ''}${card.project})` : '');
    const attrs = `href="${esc(href)}" ${o.point ? pointAttrs(o.point) : `data-task="${esc(t.roomId || '')}"`}${id ? ` data-agent="${esc(id)}"` : ''}`
      + ` data-card="${esc(JSON.stringify(card))}" aria-label="${esc(label)}" aria-haspopup="dialog" aria-expanded="false"`;
    // The title the text quotes after the number (refsIn's r.title, "task #200
    // *its title*"): a link to the same task, opening the same card (#202).
    let title = '';
    if (o.title && o.title.text) {
      o.title.taken = true;
      title = `${o.title.space || ' '}<a class="task-title-ref" ${attrs}><em>${esc(o.title.text)}</em></a>`;
    }
    return `<a class="task-chip${o.point ? ' point-chip' : ''}${dot ? ' tc-' + dot : ''}" ${attrs}>`
      + (dot ? '<span class="tc-dot" aria-hidden="true"></span>' : '')
      + `<span class="ref-who">${esc((id ? '@' + id + ' ' : '') + word(t, o.key))}</span></a>` + title;
  }
  // A room id the hub has no task for (deleted): its words as written, with a
  // card that says so, and no link to follow (#202).
  function goneHtml(r) {
    const ref = r.token || r.room || '';
    const card = { ref, title: '', state: '(deleted)', agents: [], project: '', href: '', task: '', agent: '', gone: true };
    return `<a class="task-chip tc-gone" role="button" tabindex="0" data-card="${esc(JSON.stringify(card))}"`
      + ` aria-label="${esc('Task ' + ref + ' (deleted)')}" aria-haspopup="dialog" aria-expanded="false"><span class="ref-who">${esc(ref)}</span></a>`;
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
/* A deleted task's room id: the words as written, a quiet underline for its card. */
.msg .text.md a.task-chip.tc-gone, a.task-chip.tc-gone { display:inline; padding:0; background:none; border:0; border-radius:0; font:inherit; color:var(--fg-muted);
  text-decoration:underline dotted; text-underline-offset:2px; white-space:normal; user-select:text; -webkit-user-select:text; }
/* The title a header quotes after the number: a link to the same task, the same card. */
.msg .text.md a.task-title-ref, a.task-title-ref { color:var(--link); text-decoration:none; cursor:pointer; }
a.task-title-ref:hover, a.task-title-ref[aria-expanded="true"] { text-decoration:underline; }
a.task-title-ref:focus-visible, a.task-chip.tc-gone:focus-visible { outline:none; box-shadow:0 0 0 2px var(--surface), 0 0 0 4px var(--focus-ring); border-radius:var(--r-100); }
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
.task-card.tc-has-why { flex-wrap:wrap; row-gap:0; }
.task-card .tc-why { order:10; flex:1 1 100%; min-width:0; white-space:normal; color:var(--fg-muted); }
/* Beside a list (a left bar's task): a notch pointing at the row it is for. */
.task-card.tc-beside::before { content:''; position:absolute; top:calc(var(--tc-notch, 50%) - 5px); width:8px; height:8px; box-sizing:border-box;
  background:var(--surface-overlay); border:1px solid var(--border); transform:rotate(45deg); }
.task-card.tc-beside.tc-right::before { left:-5px; border-top-color:transparent; border-right-color:transparent; }
.task-card.tc-beside.tc-left::before { right:-5px; border-bottom-color:transparent; border-left-color:transparent; }
@media (pointer: coarse) {
  /* A chip keeps its look in the line of text and takes a finger's height with an invisible margin. */
  .msg .text.md a.task-chip, a.task-chip { overflow:visible; }
  a.task-chip::after { content:''; position:absolute; left:0; right:0; top:-14px; bottom:-14px; }
  /* Not a deleted id: it is words that may wrap, and the margin would cover the chips beside it. */
  a.task-chip.tc-gone { position:static; }
  a.task-chip.tc-gone::after { content:none; }
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
    S.el = null; S.chip = null; S.hover = false; S.spec = null; S.focus = false; S.card = '';
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
  // Beside the list the anchor is in (spec.beside(a)), its notch at the
  // anchor's middle: right of the list, else left of it, else (no room on
  // either side: a phone's list fills the screen) under or over the anchor as
  // a chip's. It never covers the anchor and moves nothing in the list.
  const BESIDE_MIN = 240;
  function placeBeside(S, el, a, list) {
    const w = S.doc.defaultView, m = 8, gap = 8, r = a.getBoundingClientRect(), L = list.getBoundingClientRect();
    const right = w.innerWidth - m - (L.right + gap), leftRoom = L.left - gap - m;
    el.classList.remove('tc-beside', 'tc-right', 'tc-left');
    el.style.maxWidth = '';
    const side = right >= BESIDE_MIN ? 'right' : leftRoom >= BESIDE_MIN ? 'left' : '';
    if (!side) { place(S, el, a); return; }
    el.classList.add('tc-beside', 'tc-' + side);
    el.style.maxWidth = Math.min(720, side === 'right' ? right : leftRoom) + 'px';
    el.style.left = '0px'; el.style.top = '0px';
    const cw = el.offsetWidth, ch = el.offsetHeight, mid = r.top + r.height / 2;
    const top = Math.max(m, Math.min(mid - ch / 2, w.innerHeight - m - ch));
    el.style.left = (side === 'right' ? L.right + gap : L.left - gap - cw) + 'px';
    el.style.top = top + 'px';
    el.style.setProperty('--tc-notch', Math.max(8, Math.min(ch - 8, mid - top)) + 'px');
  }
  // own: the anchor's spec opens the task (its title is not a page's data-task link).
  function cardHtml(c, own) {
    return `<span class="tc-no">${esc(c.ref)}</span>`
      + (c.project ? `<span class="tc-proj${c.assumed ? ' tc-assumed' : ''}"${c.assumed ? ' title="The sentence names more than one project: this one was assumed"' : ''}>${c.assumed ? 'assumed ' : ''}${esc(c.project)}</span>` : '')
      + (c.gone ? '' : `<a class="tc-title" href="${esc(c.href)}" ${c.point ? pointAttrs(c.point) : own ? '' : `data-task="${esc(c.task)}"`}${c.agent ? ` data-agent="${esc(c.agent)}"` : ''} title="${esc((c.point ? 'Jump to point ' : 'Open task ') + c.ref)}">${esc(c.title)}</a>`)
      + (c.state ? `<span class="tc-state">${esc(c.state)}</span>` : '')
      + (c.agents && c.agents.length ? `<span class="tc-agents">${esc(c.agents.join(' · '))}</span>` : '')
      + (c.why ? `<span class="tc-why">${esc(c.why)}</span>` : '');
  }
  // A chip's card is in its data-card; an anchor's, what its spec says now.
  function dataOf(chip, spec) {
    if (spec) { try { return spec.card(chip) || null; } catch (e) { return null; } }
    try { return JSON.parse(chip.dataset.card || ''); } catch (e) { return null; }
  }
  function fill(S, el, c, spec) {
    el.setAttribute('aria-label', (c.point ? 'Point ' : 'Task ') + c.ref);
    el.classList.toggle('tc-has-why', !!c.why);
    el.innerHTML = cardHtml(c, !!(spec && spec.open));
    S.card = JSON.stringify(c);
  }
  function openCard(S, chip, hover, spec) {
    const c = dataOf(chip, spec);
    if (!c) return;
    closeCard(S);
    const el = S.doc.createElement('div');
    el.className = 'task-card';
    el.setAttribute('role', 'dialog');
    fill(S, el, c, spec);
    S.doc.body.appendChild(el);
    const list = spec && spec.beside ? spec.beside(chip) : null;
    if (list) placeBeside(S, el, chip, list); else place(S, el, chip);
    chip.setAttribute('aria-expanded', 'true');
    S.el = el; S.chip = chip; S.at = Date.now(); S.hover = !!hover; S.spec = spec || null;
    S.rect = rectKey(chip);
  }
  const rectKey = a => { const r = a.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round).join(','); };
  // The anchor of an open card's task in the list as it is drawn now (a
  // redraw replaced the element): the one whose spec.key is the same.
  function sameAnchor(S, spec, a) {
    if (a.isConnected) return a;
    const k = spec.key(a);
    return [...S.doc.querySelectorAll(spec.selector)].find(x => spec.key(x) === k) || null;
  }
  // After the page drew its list again: an anchor's card stays open, on the
  // anchor drawn in its place, its content the task's as it is now; placed
  // again only when the anchor moved. Gone with the anchor.
  function refreshCard(S) {
    if (!S.el || !S.spec) return;
    const a = sameAnchor(S, S.spec, S.chip);
    if (!a || !a.getClientRects().length) { closeCard(S); return; }   // gone, or its list hidden
    if (a !== S.chip) { S.chip = a; a.setAttribute('aria-expanded', 'true'); }
    const c = dataOf(a, S.spec);
    if (!c) { closeCard(S); return; }
    const changed = JSON.stringify(c) !== S.card;
    if (changed) fill(S, S.el, c, S.spec);
    const rk = rectKey(a);
    if (rk !== S.rect || changed) {
      S.rect = rk;
      const list = S.spec.beside ? S.spec.beside(a) : null;
      if (list) placeBeside(S, S.el, a, list); else place(S, S.el, a);
    }
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
    const S = { doc, el: null, chip: null, at: 0, hover: false, timer: 0, leave: 0, spec: null, specs: [], focus: false, card: '',
                press: null, pressed: null, quiet: null,
                openTask: a => a.classList.contains('tc-gone') ? null : a.dataset.point && o.openPoint ? o.openPoint(a) : (o.open || (a => { w.location.href = a.href; }))(a) };
    STATES.set(doc, S);
    const chipOf = ev => (ev.target && ev.target.closest) ? ev.target.closest('a.task-chip, a.task-title-ref') : null;
    // An element given to anchors(): [the element, its spec], or null.
    const anchorOf = ev => {
      if (!ev.target || !ev.target.closest) return null;
      for (const sp of S.specs) { const x = ev.target.closest(sp.selector); if (x) return [x, sp]; }
      return null;
    };
    const inCard = ev => !!(S.el && ev.target && ev.target.closest && ev.target.closest('.task-card'));
    const fine = () => { try { return w.matchMedia('(hover: hover) and (pointer: fine)').matches; } catch (e) { return false; } };
    doc.addEventListener('click', ev => {
      // A long press opened an anchor's card: the click it ends with is not a tap.
      if (S.pressed) {
        const an = anchorOf(ev), was = S.pressed;
        S.pressed = null;
        if (an && an[0] === was) { ev.preventDefault(); ev.stopImmediatePropagation(); return; }
      }
      const a = chipOf(ev);
      if (!a) {
        if (inCard(ev) && ev.target.closest('a')) {
          // An anchor's card: its title opens the task as the anchor does.
          const plain = ev.button === 0 && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey;
          const sp = S.spec, at = S.chip;
          if (sp && sp.open && plain) { ev.preventDefault(); ev.stopImmediatePropagation(); closeCard(S); sp.open(at); return; }
          closeCard(S);   // the title: the page opens the task
        }
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
      // A press is a click on its way, never a rest: no card comes after it.
      clearTimeout(S.timer); S.timer = 0;
      S.pressed = null;
      endPress();
      // A long press on an anchor, on a touch screen: its card, not its task.
      const an = ev.pointerType === 'touch' ? anchorOf(ev) : null;
      if (!an) return;
      const [x, sp] = an;
      S.press = { x: ev.clientX, y: ev.clientY, id: ev.pointerId, timer: setTimeout(() => {
        S.press = null;
        if (!x.isConnected) return;
        openCard(S, x, false, sp);
        if (S.el) S.pressed = x;
      }, LONG) };
    }, true);
    const endPress = () => { if (S.press) { clearTimeout(S.press.timer); S.press = null; } };
    doc.addEventListener('pointermove', ev => {
      if (S.press && ev.pointerId === S.press.id && Math.hypot(ev.clientX - S.press.x, ev.clientY - S.press.y) > 10) endPress();
    }, true);
    for (const t of ['pointerup', 'pointercancel']) doc.addEventListener(t, endPress, true);
    // Its long press is not the phone's menu for a link or a word.
    doc.addEventListener('contextmenu', ev => {
      const an = anchorOf(ev);
      if (an && (S.press || S.pressed === an[0])) ev.preventDefault();
    }, true);
    doc.addEventListener('keydown', ev => {
      // A deleted task's words have no link, so Enter does not click them: the card.
      const g = ev.key === 'Enter' && ev.target && ev.target.closest ? ev.target.closest('a.tc-gone') : null;
      if (g) { ev.preventDefault(); if (S.el && S.chip === g) closeCard(S); else openCard(S, g); return; }
      if (ev.key !== 'Escape' || !S.el) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const chip = S.chip;
      closeCard(S);
      // The focus goes back without opening the card again.
      S.quiet = chip;
      try { chip.focus({ preventScroll: true }); } catch (e) {}
      S.quiet = null;
    }, true);
    // An anchor reached with the keyboard (Tab, the arrow keys): its card
    // after the same moment as a rest; it goes when the focus leaves for
    // anything but the card or the same task's anchor drawn again.
    doc.addEventListener('focusin', ev => {
      const an = anchorOf(ev);
      if (!an || inCard(ev)) return;
      const [x, sp] = an;
      if (S.quiet === x) return;
      if (S.el && S.spec === sp && sp.key(S.chip) === sp.key(x)) {   // the same task, drawn again
        if (S.chip !== x) { S.chip = x; x.setAttribute('aria-expanded', 'true'); }
        return;
      }
      let kb = false;
      try { kb = x.matches(':focus-visible'); } catch (e) {}
      if (!kb) return;
      clearTimeout(S.timer);
      S.timer = setTimeout(() => {
        S.timer = 0;
        if (doc.activeElement === x && !(S.el && !S.hover && !S.focus)) { openCard(S, x, false, sp); S.focus = !!S.el; }
      }, HOVER_IN);
    }, true);
    doc.addEventListener('focusout', ev => {
      const an = anchorOf(ev) || (inCard(ev) && S.spec ? [S.chip, S.spec] : null);
      if (!an) return;
      const [x, sp] = an;
      setTimeout(() => {
        if (!S.el || !S.focus || S.spec !== sp) return;
        const act = doc.activeElement, now = act && act.closest ? act.closest(sp.selector) : null;
        if ((now && sp.key(now) === sp.key(x)) || (act && S.el.contains(act))) return;
        closeCard(S);
      }, 0);
    }, true);
    doc.addEventListener('scroll', ev => { if (S.el && !inCard(ev)) closeCard(S); }, true);
    w.addEventListener('resize', () => closeCard(S));
    w.addEventListener('blur', () => closeCard(S));
    // Hover, on a computer: the card after a moment; it stays while the pointer is on the chip or the card.
    doc.addEventListener('pointerover', ev => {
      if (ev.pointerType === 'touch' || !fine()) return;
      const an = chipOf(ev) ? [chipOf(ev), null] : anchorOf(ev);
      const a = an && an[0], sp = an && an[1];
      // Back on the open card's chip, or on the card: it stays. (Another chip
      // does not keep it: the card would be left open with the pointer gone.)
      if ((a && S.chip === a) || inCard(ev)) { clearTimeout(S.leave); S.leave = 0; }
      if (a && !(S.el && S.chip === a) && !S.timer) {
        S.timer = setTimeout(() => {
          S.timer = 0;
          // An anchor the page drew again meanwhile: the one in its place, if the pointer is on it.
          const cur = sp ? sameAnchor(S, sp, a) : a;
          if (!cur || !cur.isConnected || (cur !== a && !cur.matches(':hover'))) return;
          if (!(S.el && !S.hover && !S.focus)) openCard(S, cur, true, sp);   // never over a card that was clicked open
        }, HOVER_IN);
      }
    }, true);
    doc.addEventListener('pointerout', ev => {
      if (ev.pointerType === 'touch') return;
      const an = chipOf(ev) ? [chipOf(ev)] : anchorOf(ev);
      const a = an && an[0], to = ev.relatedTarget;
      if (a && S.timer && !(to && a.contains(to))) { clearTimeout(S.timer); S.timer = 0; }
      if (S.el && S.hover && (a === S.chip || inCard(ev))) {
        if (to && (S.el.contains(to) || (S.chip && S.chip.contains(to)))) return;
        clearTimeout(S.leave);
        S.leave = setTimeout(() => { if (S.hover) closeCard(S); }, HOVER_OUT);
      }
    }, true);
    return S;
  }

  // The card for elements that are not chips (#201: a left bar's task
  // numbers), on a document init has set up. A click on one stays the page's
  // (it opens the task); the card comes on a rest of the pointer, on keyboard
  // focus after the same moment, or on a long press on a touch screen, and
  // goes as a chip's hover card goes (Esc, a press elsewhere, scrolling, the
  // pointer away). o: { doc, selector: what an anchor matches, key(el): which
  // task it names (the same across redraws), card(el): its card's data
  // (cardOf; null: no card), open(el): what its card's title does (as a click
  // on the anchor), beside(el): the list to stand beside (else under it) }.
  // Once per selector and document.
  function anchors(o) {
    const S = stateOf(o.doc);
    if (!S) throw new Error('TaskCard.init the document first');
    const sp = { selector: o.selector, key: o.key, card: o.card, open: o.open || null, beside: o.beside || null };
    const i = S.specs.findIndex(x => x.selector === sp.selector);
    if (i >= 0) S.specs[i] = sp; else S.specs.push(sp);
    return sp;
  }

  return { refs, chipHtml, goneHtml, cardOf, stateText, init, anchors, TASK_REF_RE, NOT_TASK_BEFORE_RE, notTaskRef, refsIn, codeRef, replaceRefs, readProject,
           refresh: doc => { const S = stateOf(doc); if (S) refreshCard(S); },
           open: (chip, doc) => { const S = stateOf(doc); if (S) openCard(S, chip); },
           close: doc => { const S = stateOf(doc); if (S) closeCard(S); }, el: doc => { const S = stateOf(doc); return S ? S.el : null; },
           CSS, esc };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = TaskCard;
