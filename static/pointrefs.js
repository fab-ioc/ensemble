// Point mentions share TaskCard's appearance and interaction, and Your asks' jumps.
const PointRefs = (() => {
  const blank = s => s.replace(/[^\n]/g, ' ');
  function readable(text) {
    // Keep offsets while excluding Markdown code, links and path/URL tokens.
    let fence = null;
    return String(text ?? '').split('\n').map(line => {
      const f = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if (fence) {
        if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !f[2].trim()) fence = null;
        return blank(line);
      }
      if (f) { fence = f[1]; return blank(line); }
      if (/^(?: {4}|\t)/.test(line)) return blank(line);
      return line;
    }).join('\n').replace(/(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)/g, blank)
      .replace(/\[[^\]]*\]\([^)]*\)/g, blank)
      .replace(/\S*(?:[\/\\]|:\/\/|www\.|mailto:)\S*/g, blank);
  }
  function refsIn(text, ctx) {
    if (!ctx) return [];
    const plain = readable(text), out = [];
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
      if (r.project === '!ambiguous') continue;
      out.push({ id: m[2], start, end, ...r });
    }
    return out;
  }
  function create(o) {
    const cache = new Map();
    function info(id, project) {
      const key = project + ':' + id, old = cache.get(key);
      if (old && (old.pending || Date.now() - old.at < 60000)) return old.value;
      cache.set(key, { pending: true, value: null });
      fetch('/api/point/ref?ref=' + encodeURIComponent(id) + '&project=' + encodeURIComponent(project) + '&room=' + encodeURIComponent(o.room))
        .then(r => r.status === 404 ? null : r.ok ? r.json() : Promise.reject(new Error('point lookup failed')))
        .then(value => { cache.set(key, { at: Date.now(), value }); o.changed(); })
        .catch(() => { cache.set(key, { at: Date.now() - 45000, value: null }); });
      return null;
    }
    function chip(r, ctx) {
      const p = info(r.id, r.project);
      // A weaker project mention with the same point is ambiguous: attach nothing.
      for (const pid of r.others) {
        const other = info(r.id, pid), c = cache.get(pid + ':' + r.id);
        if (!c || c.pending || other) return null;
      }
      if (!p) return null;
      const text = String(p.text || '').split('\n')[0];
      const title = text.length > 140 ? text.slice(0, 139) + '…' : text;
      const stage = { open: 'open', planned: 'in progress', delivered: 'ready for check', answered: 'ready for check', acked: 'acknowledged' }[p.stage] || p.stage;
      const when = p.createdAt ? new Date(p.createdAt * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '';
      const href = '/session?room=' + encodeURIComponent(p.roomId) + '&msg=' + encodeURIComponent(p.mid) + '&part=pt:' + p.id;
      return TaskCard.chipHtml({ roomId: p.roomId, ref: p.id, label: p.id, title, project: p.project,
        inProject: p.projectId === ctx.own, agents: [] }, { href, point: { room: p.roomId, msg: p.mid, part: 'pt:' + p.id }, state: [stage, when].filter(Boolean).join(' · ') });
    }
    function replace(text, ctx, plain = s => s, park = s => s) {
      let out = '', at = 0;
      for (const r of refsIn(text, ctx)) {
        const h = chip(r, ctx);
        if (!h) continue;
        out += plain(text.slice(at, r.start)) + park(h); at = r.end;
      }
      return out + plain(text.slice(at));
    }
    return { replace };
  }
  return { refsIn, readable, create };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = PointRefs;
