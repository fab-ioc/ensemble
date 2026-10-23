// Point mentions share TaskCard's appearance and interaction, and Needs you's jumps.
const PointRefs = (() => {
  const blank = s => s.replace(/[^\n]/g, ' ');
  function readable(text) {
    // Keep offsets while excluding Markdown code, links and path/URL tokens.
    let fence = null, listIndent = null;
    return String(text ?? '').split('\n').map(line => {
      const f = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if (fence) {
        if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !f[2].trim()) fence = null;
        return blank(line);
      }
      if (f) { fence = f[1]; return blank(line); }
      const list = line.match(/^(\s*)(?:[-*+]|\d+[.)])\s+/);
      const indent = (line.match(/^\s*/)[0] || '').replace(/\t/g, '    ').length;
      // Four spaces may introduce a nested list or an indented code block.
      // Only a list already in progress makes its nested marker prose.
      if (indent >= 4 && (!list || listIndent === null || indent > listIndent + 4)) return blank(line);
      if (list) listIndent = indent;
      else if (line.trim()) listIndent = null;
      return line;
    }).join('\n').replace(/(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)/g, blank)
      .replace(/\[[^\]]*\]\([^)]*\)/g, blank)
      .replace(/\S+/g, token => /[\/\\]|www\.|mailto:/.test(token) ? blank(token) : token);
  }
  function refsIn(text, ctx) {
    if (!ctx) return [];
    const plain = readable(text).replace(/(?<![\p{L}\p{N}_])(_{1,2})(?=\S)([^_\n]+?)\1(?![\p{L}\p{N}_])/gu,
      (_, marks, body) => ' '.repeat(marks.length) + body + ' '.repeat(marks.length)), out = [];
    // Include keys as aliases, preserving duplicate names as ambiguous.
    const owners = new Map();
    for (const p of ctx.projects || []) for (const a of [...(p.aliases || []), p.key, p.name].filter(Boolean)) {
      const k = a.toLowerCase();
      if (!owners.has(k)) owners.set(k, new Set());
      owners.get(k).add(p.id);
    }
    const pc = { ...ctx, _res: undefined, projects: [{ id: '', aliases: [] }] };
    pc.projects = [...owners].map(([alias, ids]) => ({ id: ids.size === 1 ? [...ids][0] : '!ambiguous', aliases: [alias] }));
    // Strong Unicode boundaries: P2P, MP3, file.P23, P23.txt and paths stay text.
    for (const m of plain.matchAll(/(?<![\p{L}\p{N}_@#./\\-])(?:([A-Z][A-Z0-9]{0,5})-)?(P[1-9]\d{0,5}[a-z]?)(?![\p{L}\p{N}_@/\\-]|\.[\p{L}\p{N}_])/gu)) {
      const start = m.index, end = start + m[0].length;
      const context = plain.replace(/\*\*|__/g, '  ');
      const r = TaskCard.readProject(context, start, end, pc);
      if (m[1]) {
        const projects = (ctx.projects || []).filter(p => p.key === m[1]);
        if (projects.length !== 1) continue;
        Object.assign(r, { project: projects[0].id, how: 'key', others: [] });
      }
      if (r.project === '!ambiguous' || r.others.includes('!ambiguous')) continue;
      // "the P515 card", "P515 card", "card P515": the question card (#206).
      const card = /^[ \t]*card\b/i.test(context.slice(end)) || /\bcard[ \t]+$/i.test(context.slice(Math.max(0, start - 12), start));
      out.push({ id: m[2], start, end, ...r, ...(card ? { card: true } : {}) });
    }
    return out;
  }
  // What the hub said of each point is kept for the page's life and asked
  // again only after refresh() (the chat's points changed) or AGE_MS (a
  // point of another chat): the points a draw needs are asked in one
  // request per project (/api/point/refs), never one per chip (#265: a PO
  // chat's hundreds of chips asked each a minute, and queued its sends).
  const AGE_MS = 300000, RETRY_MS = 15000, BATCH = 100;
  function create(o) {
    const cache = new Map();      // "project:Pn" → { at, value, pending?, stale?, gen? }
    const queue = new Map();      // project → Set of point ids to ask
    let timer = null, gen = 0;    // gen: refresh() calls so far
    function flush() {
      timer = null;
      for (const [project, set] of queue) {
        const ids = [...set];
        for (let i = 0; i < ids.length; i += BATCH) ask(project, ids.slice(i, i + BATCH));
      }
      queue.clear();
    }
    function ask(project, ids) {
      fetch('/api/point/refs?ids=' + encodeURIComponent(ids.join(',')) + '&project=' + encodeURIComponent(project) + '&room=' + encodeURIComponent(o.room))
        .then(r => r.ok ? r.json() : Promise.reject(new Error('point lookup failed')))
        .then(d => {
          let changed = false;
          for (const id of ids) {
            const key = project + ':' + id, old = cache.get(key), value = (d && d.points && d.points[id]) || null;
            // Asked before the points last changed: shown, and asked again next draw.
            const stale = !!old && old.gen !== gen;
            if (!old || stale || JSON.stringify(old.value) !== JSON.stringify(value)) changed = true;
            cache.set(key, { at: Date.now(), value, stale });
          }
          if (changed) o.changed();
        })
        // The hub did not answer: what it said before (or text), asked again in a while.
        .catch(() => { for (const id of ids) { const c = cache.get(project + ':' + id); if (c) Object.assign(c, { pending: false, stale: false, at: Date.now() - AGE_MS + RETRY_MS }); } });
    }
    function info(id, project) {
      const key = project + ':' + id, old = cache.get(key);
      if (old && (old.pending || (!old.stale && Date.now() - old.at < AGE_MS))) return old.value;
      cache.set(key, { at: old ? old.at : 0, value: old ? old.value : null, pending: true, gen });
      if (!queue.has(project)) queue.set(project, new Set());
      queue.get(project).add(id);
      if (!timer) timer = Promise.resolve().then(flush);   // after this draw: its chips in one request
      return old ? old.value : null;
    }
    // The chat's points changed: each is asked again when next drawn.
    // One asked meanwhile is asked again once its answer is in (ask).
    function refresh() { gen++; for (const c of cache.values()) if (!c.pending) c.stale = true; }
    function chip(r, ctx) {
      const p = info(r.id, r.project);
      // A weaker project mention with the same point is ambiguous: attach nothing.
      for (const pid of r.others) {
        const other = info(r.id, pid), c = cache.get(pid + ':' + r.id);
        if (!c || (c.pending && !c.at) || other) return null;
      }
      if (!p) return null;
      const text = String(p.text || '').split('\n')[0];
      const title = text.length > 140 ? text.slice(0, 139) + '…' : text;
      const stage = { open: 'open', planned: 'in progress', delivered: 'ready for check', answered: 'ready for check', acked: 'acknowledged' }[p.stage] || p.stage;
      const when = p.createdAt ? new Date(p.createdAt * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
      const his = { room: p.roomId, msg: p.mid, part: 'pt:' + p.id };
      // The question card waiting on the person for this point, or named as
      // "the Pn card": the chip lands on it, and its card offers both (#206).
      const c = p.card && (p.card.open || r.card) ? { room: p.roomId, msg: p.card.mid, part: 'ask:' + p.card.n } : null;
      const at = c || his;
      const href = '/session?room=' + encodeURIComponent(at.room) + '&msg=' + encodeURIComponent(at.msg) + '&part=' + at.part;
      const links = c ? [{ text: p.card.open ? 'Answer the question ↓' : 'The question ↓', title: p.card.question || '', point: c },
                         { text: "The point's message", title: 'The message that raised ' + p.id, point: his }] : null;
      const h = TaskCard.chipHtml({ roomId: p.roomId, ref: p.id, label: p.id, title, project: p.project,
        inProject: p.projectId === ctx.own, agents: [] }, { href, point: at, links, state: [stage, when].filter(Boolean).join(' · ') });
      // What the chip opens: a link right after it to either is the same place (#238).
      return { h, at: [his, c].filter(Boolean) };
    }
    function replace(text, ctx, plain = s => s, park = s => s) {
      let out = '', at = 0;
      for (const r of refsIn(text, ctx)) {
        const x = chip(r, ctx);
        if (!x) continue;
        out += plain(text.slice(at, r.start)) + park(x.h, x.at); at = r.end;
      }
      return out + plain(text.slice(at));
    }
    return { replace, refresh };
  }
  return { refsIn, readable, create };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = PointRefs;
