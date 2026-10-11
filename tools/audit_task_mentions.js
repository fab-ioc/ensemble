// Every way the chats name a task, counted, and whether the pages link it
// (#202). Read-only: reads ~/.ensemble/projects.json and rooms/*.json.
//
//   node tools/audit_task_mentions.js [--json] [--examples N] [--state DIR]
//
// "Linked" is what static/taskcard.js (the file in this checkout) draws as a
// chip: refsIn on the prose (code spans and blocks are blanked), and, since
// #202, a code span that is exactly a reference and a room id. A mention is
// counted only when it names a task that exists (a number the project has, a
// key's number, a room file); a number that is not a task is counted apart.
'use strict';
const fs = require('fs'), path = require('path'), os = require('os');
const TaskCard = require(path.join(__dirname, '..', 'static', 'taskcard.js'));

const args = process.argv.slice(2);
const opt = n => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : null; };
const STATE = opt('--state') || path.join(os.homedir(), '.ensemble');
const EXAMPLES = +(opt('--examples') || 3);

const projects = JSON.parse(fs.readFileSync(path.join(STATE, 'projects.json'), 'utf8'));
const byKey = new Map(projects.filter(p => p.key).map(p => [String(p.key).toUpperCase(), p.id]));
const rooms = new Map();          // id → { no, project, title, messages }
for (const f of fs.readdirSync(path.join(STATE, 'rooms'))) {
  if (!/^room-[0-9a-f]+\.json$/.test(f)) continue;
  try { rooms.set(f.slice(0, -5), JSON.parse(fs.readFileSync(path.join(STATE, 'rooms', f), 'utf8'))); } catch (e) {}
}
const byNo = new Map();          // "proj|no" → room id
for (const [id, r] of rooms) {
  const pid = r.noProjectId || r.projectId || '';
  if (r.no && pid) byNo.set(pid + '|' + r.no, id);
  for (const p of r.previousNos || []) if (p && p.no && p.projectId && !byNo.has(p.projectId + '|' + p.no)) byNo.set(p.projectId + '|' + p.no, id);
}
const ctxFor = own => ({ projects: projects.map(p => ({ id: p.id, key: p.key, name: p.name,
  aliases: [p.name, p.key, String(p.name || '').split(/\s+/)[0]].filter(Boolean) })), own, nouns: [] });
const taskOf = (key, no, own) => byNo.get((key ? byKey.get(key) || '?' : own) + '|' + no) || '';

// form → { linked, unlinked, examples }
const forms = new Map();
function count(form, linked, text, at, len) {
  let f = forms.get(form);
  if (!f) forms.set(form, f = { linked: 0, unlinked: 0, examples: [] });
  f[linked ? 'linked' : 'unlinked']++;
  if (f.examples.length < EXAMPLES) f.examples.push(text.slice(Math.max(0, at - 30), at + len + 30).replace(/\s+/g, ' '));
}
const linkedHere = typeof TaskCard.codeRef === 'function';   // #202: code spans, room ids, words, header titles

const CODE_SPAN_RE = /`([^`\n]+)`/g;
const ROOM_RE = /(?<![\w-])room-[0-9a-f]{8}(?![\w-])/g;
const ROOM_TEST = /room-[0-9a-f]{8}/;   // an old header's room id: its chip since #202
const WORD_RE = /(?<![\w#-])([Tt]asks?|[Cc]ards?)[ \t]+(\d{1,4})(?![\w-])/g;
const POINT_RE = /(?<![\w-])P(\d{1,4}[a-z]?)(?![\w-])/g;
const HEADER_NO_NUMBER_RE = /report from task \*/;
const HEADER_TITLE_RE = /(?:of |from )?task (#\d+|[A-Z][A-Z0-9]{0,5}-\d+) \*[^*\n]+\*/g;
const BLOCK_RE = /```[\s\S]*?(?:```|$)/g;
const blank = m => m.replace(/[^\n]/g, ' ');

let messages = 0;
for (const [rid, room] of rooms) {
  const roomProject = room.noProjectId || room.projectId || '';
  for (const m of room.messages || []) {
    const text = String(m.text || '');
    if (!text) continue;
    messages++;
    const own = (m.kind === 'pomsg' && m.fromProjectId) || roomProject;
    const who = /^ensemble$|@room-/.test(m.from || '') || m.kind === 'report' || m.kind === 'notice' ? 'hub' : (m.from === 'user' ? 'person' : 'agent');
    const ctx = ctxFor(own);
    const noBlocks = text.replace(BLOCK_RE, blank);
    const prose = noBlocks.replace(CODE_SPAN_RE, blank);
    const tableLines = new Set();
    {
      const lines = noBlocks.split('\n');
      let off = 0;
      const starts = lines.map(l => { const s = off; off += l.length + 1; return s; });
      for (let i = 0; i + 1 < lines.length; i++) {
        if (lines[i].includes('|') && /^[\s|:-]+$/.test(lines[i + 1].trim()) && lines[i + 1].includes('-')) {
          for (let j = i; j < lines.length && lines[j].includes('|') && lines[j].trim(); j++) tableLines.add(starts[j]);
        }
      }
      var lineStart = at => { let s = 0; for (const x of starts) { if (x <= at) s = x; else break; } return s; };
    }
    // 1. What refsIn reads in prose (today's chips), by form.
    const covered = [];
    for (const r of TaskCard.refsIn(text, ctx)) {
      covered.push([r.start, r.end]);
      const t = r.key ? taskOf(r.key, r.no, own) : taskOf('', r.no, r.how === 'name' && r.project ? r.project : own) || taskOf('', r.no, own);
      if (!t) { count('number read as a reference, no such task (plain, right)', false, text, r.start, r.token.length); continue; }
      const inTable = tableLines.has(lineStart(r.start));
      const form = (r.who ? '@agent@N' : r.key ? 'KEY-N' : '#N') + (inTable ? ' in a table cell' : ' in prose');
      count(form, true, text, r.start, r.token.length);
    }
    // 2. #N / KEY-N in prose that refsIn passes over: why.
    for (const mm of prose.matchAll(/#(\d{1,6})(?!\d)|(?<![\w-])([A-Z][A-Z0-9]{0,5})-(\d{1,6})(?!\d)/g)) {
      const at = mm.index;
      if (covered.some(([a, b]) => at >= a && at < b)) continue;
      const key = mm[2] || '', no = +(mm[1] || mm[3]);
      if (key && !byKey.has(key)) continue;            // UTF-8, ISO-8601
      const t = taskOf(key, no, own);
      if (!key && TaskCard.notTaskRef('', '', prose, at)) {
        count(`#N after "PR", "step", "image"… (plain by design)${t ? ' — a task has that number' : ''}`, false, text, at, mm[0].length);
        continue;
      }
      if (!t) continue;
      const before = prose[at - 1] || '', after = prose[at + mm[0].length] || '';
      count(`${key ? 'KEY-N' : '#N'} next to "${before}…${after}"`, false, text, at, mm[0].length);
    }
    // 3. Code spans.
    for (const cm of noBlocks.matchAll(CODE_SPAN_RE)) {
      const body = cm[1].trim(), at = cm.index;
      let mm;
      if ((mm = /^(?:#|([A-Z][A-Z0-9]{0,5})-)(\d{1,6})$/.exec(body))) {
        const t = taskOf(mm[1] || '', +mm[2], own);
        if (t) count('`#N` / `KEY-N` as a whole code span', linkedHere && !!TaskCard.codeRef(body, ctx), text, at, cm[0].length);
        continue;
      }
      if (/^room-[0-9a-f]{8}$/.test(body)) {
        count(rooms.has(body) ? '`room-…` code span, a task' : '`room-…` code span, deleted task', linkedHere, text, at, cm[0].length);
        continue;
      }
      if (TaskCard.refsIn(body, null).length || ROOM_RE.test(body)) {
        ROOM_RE.lastIndex = 0;
        count('task number inside a longer code span (stays code)', false, text, at, cm[0].length);
      }
    }
    // 4. Room ids in prose.
    for (const rm of prose.matchAll(ROOM_RE)) {
      count(rooms.has(rm[0]) ? 'room-… in prose, a task' : 'room-… in prose, deleted task', linkedHere, text, rm.index, rm[0].length);
    }
    // 5. "task 201" as a word.
    const words = linkedHere ? TaskCard.refsIn(text, ctx, { words: true }).map(r => r.start) : [];
    for (const wm of prose.matchAll(WORD_RE)) {
      const at = wm.index + wm[0].length - wm[2].length;
      if (taskOf('', +wm[2], own)) count('"task 201" written as a word', words.includes(at), text, wm.index, wm[0].length);
    }
    // 6. Hub headers.
    if (who === 'hub' && HEADER_NO_NUMBER_RE.test(text)) count('hub header naming a task without its number', linkedHere && ROOM_TEST.test(text), text, text.search(HEADER_NO_NUMBER_RE), 30);
    for (const hm of text.matchAll(HEADER_TITLE_RE)) count('italic title after the number in a header', linkedHere, text, hm.index, hm[0].length);
    // 7. Points (P163): pointrefs.js links the ones the hub knows.
    for (const pm of prose.matchAll(POINT_RE)) count('point P163 (pointrefs.js, not counted as a task)', true, text, pm.index, pm[0].length);
  }
}

const rows = [...forms].sort((a, b) => (b[1].linked + b[1].unlinked) - (a[1].linked + a[1].unlinked));
if (args.includes('--json')) {
  console.log(JSON.stringify({ rooms: rooms.size, messages, forms: Object.fromEntries(rows) }, null, 1));
} else {
  console.log(`${rooms.size} rooms, ${messages} messages\n`);
  console.log('| Form | Count | Linked | Not linked | Example |\n|---|---:|---:|---:|---|');
  for (const [form, f] of rows) {
    console.log(`| ${form} | ${f.linked + f.unlinked} | ${f.linked} | ${f.unlinked} | ${(f.examples[0] || '').replace(/\|/g, '\\|').replace(/`/g, 'ˋ')} |`);
  }
}
