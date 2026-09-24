"""The Files panel of a documents project's Workspace tab: drop, Add files,
folders, move, rename and delete (index.html's "Files panel" block).

* the pure parts, run in Node (skipped without Node): the upload queue (at
  most three out, a file over 50 MB refused before it is sent, what each hub
  answer does to a file, Retry), the batch's answer to "already there"
  (asked once, "apply to all", Keep both counting on), the walk of a dropped
  folder (paths, batches of entries, empty folders, unreadable files, what
  Finder leaves behind), the folders offered by Move to, and paths after a move;
* the tree rows get a menu and dragging only in a documents project's
  Workspace, not in a code project or a task's Workspace;
* "Task folders": hidden by default, shown with their files when checked (in
  the tree, Go to file and a text search), remembered by the browser; .history
  and _linked never show; a task's folder opens but is not renamed, moved,
  deleted or dropped on;
* Recent changes says who and what: "sam · uploaded".
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def js_function(name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", INDEX, re.M)
    if not m:
        return re.search(rf"^const {re.escape(name)} = .*$", INDEX, re.M).group(0)
    i = INDEX.index("{", m.end())
    depth, j = 0, i
    while True:
        depth += (INDEX[j] == "{") - (INDEX[j] == "}")
        j += 1
        if depth == 0:
            return INDEX[m.start():j]


def pure_block() -> str:
    i = INDEX.index("// ---- Files panel, pure: begin")
    return INDEX[i:INDEX.index("// ---- Files panel, pure: end", i)]


DEPS = ["esc", "wsNorm", "wsSame", "wsJoin", "wsFmtSize", "wsRowsHtml", "wsCtxKey", "isDocsProject", "wsDocsProjectOf",
        "projectById", "registeredProjects", "wsHidden", "wsProjectForRow", "wsTaskFolder", "histWho", "HIST_DID", "histDid",
        "histRel", "wsfRoot", "wsFetchDir"]
# The Files panel's own wiring that runs without a page.
WIRING = ["histDelPlace", "DOCS_FP", "docsFp", "docsMine", "docsDeletedRows", "docsAsk", "docsAskShow", "docsUpPaint", "docsUpBar",
          "docsApartOf"]

PAGE = r"""
const out = {};
let api = async () => { throw new Error('no hub'); };
const UNASSIGNED_ID = '__unassigned__';
let ALL_ROWS = [], WS_DIR_GEN = 0;
const PROJECTS = { projects: [
  { id: 'p-docs', name: 'Motors', kind: 'documents', registered: true, path: 'C:\\P\\Motors', home: 'C:\\P\\Motors', sessions: [] },
  { id: 'p-code', name: 'Opten', kind: 'code', registered: true, path: 'C:\\P\\Opten', home: 'C:\\P\\Opten', sessions: [] },
] };
%(deps)s
%(block)s
%(wiring)s

// ---- the queue: three out at a time, in order; too large refused up front
const batch = { answer: '' };
const f = (size) => ({ size });
const items = [upItem(batch, f(10), 'a.jpg'), upItem(batch, f(20), 'b.jpg'), upItem(batch, f(60 * 1024 * 1024), 'ads/big.mov'),
               upItem(batch, f(30), 'c.jpg'), upItem(batch, f(40), 'd.jpg'), upItem(batch, null, 'e.jpg')];
out.big = [items[2].state, items[2].final, items[2].err];
out.unreadable = [items[5].state, items[5].final, items[5].err];
out.first = upToStart(items, 3).map(x => x.name);
upToStart(items, 3).forEach(x => { x.state = 'sending'; });
out.full = upToStart(items, 3).map(x => x.name);
items[0].loaded = 5; out.pct = [upPct(items[0]), upPct(items[3])];
upAfter(items[0], 200, { path: 'a.jpg', size: 10 });
out.afterOne = upToStart(items, 3).map(x => x.name);
out.doneState = [items[0].state, upPct(items[0])];
upAfter(items[1], 500, { error: 'boom', message: 'The disk is full.' });
out.failed = [items[1].state, items[1].final, items[1].err];
out.retry = [upRetry(items[1]), items[1].state, upRetry(items[2]), items[2].state];
upAfter(items[3], 0, null);
out.offline = [items[3].err, items[3].final];
upAfter(items[4], 413, { error: 'too_large', message: 'Files over 50 MB are not accepted.' });
out.refused = [items[4].final, items[4].err, upRetry(items[4])];
out.line = upLine(items);
out.shown = upShown(items).map(x => x.name);
out.rowFailed = upRowHtml(items[5]);
out.rowSending = upRowHtml(Object.assign(upItem(batch, f(100), 'x/y.pdf'), { state: 'sending', loaded: 50 }));
out.sig = upRowSig(items[1]) !== upRowSig(Object.assign({}, items[1], { loaded: 3 })) ? 'progress changes it' : 'same';

// ---- "already there": asked once for the batch
const b1 = { answer: '' }, b2 = { answer: '' };
const q = [upItem(b1, f(1), 'ads/p.jpg'), upItem(b1, f(1), 'ads/q.jpg'), upItem(b1, f(1), 'ads/r.jpg'), upItem(b2, f(1), 's.jpg')];
q.forEach(x => { x.state = 'sending'; });
out.ask1 = upAfter(q[0], 409, { error: 'exists', message: 'x' });
out.ask2 = upAfter(q[1], 409, { error: 'exists' });
out.asking = upAsking(q).name;
upAnswer(q, q[0], 'keep', true);
out.keepAll = [q[0].state, q[0].path, q[1].state, q[1].path, b1.answer];
out.laterSameBatch = [upAfter(q[2], 409, { error: 'exists' }), q[2].path];
out.otherBatchStillAsks = upAfter(q[3], 409, { error: 'exists' });
// Keep both: name-2 is taken too, so name-3, without asking again.
q[0].state = 'sending';
out.keepCounts = [upAfter(q[0], 409, { error: 'exists' }), q[0].path];
upAnswer(q, q[3], 'replace', false);
out.replaceOne = [q[3].state, q[3].overwrite, b2.answer];
const b3 = { answer: '' }, s1 = upItem(b3, f(1), 't.txt'), s2 = upItem(b3, f(1), 'u.txt');
s1.state = s2.state = 'asking';
upAnswer([s1, s2], s1, 'skip', false);
out.skipOne = [s1.state, s2.state, b3.answer];
upAnswer([s1, s2], s2, 'skip', true);
out.skipAll = [s2.state, b3.answer, upShown([s1, s2]).length];
// Replace, and the hub still says it is there (a folder of that name): final,
// never sent again, even with "apply to all".
const b4 = { answer: '' }, r1 = upItem(b4, f(1), 'Leasing'), r2 = upItem(b4, f(1), 'Ads');
r1.state = r2.state = 'sending';
upAfter(r1, 409, { error: 'exists' }); upAfter(r2, 409, { error: 'exists' });
upAnswer([r1, r2], r1, 'replace', true);
out.replaceAll = [r1.state, r1.overwrite, r2.state, r2.overwrite];
r1.state = 'sending';
out.replaceAgain = [upAfter(r1, 409, { error: 'exists', message: 'A folder named “Leasing” is already there.' }), r1.final, r1.err,
                    upToStart([r1], 3).length, upRetry(r1)];
r2.state = 'sending';
out.replaceAgainNoMessage = [upAfter(r2, 409, { error: 'exists' }), r2.final, r2.err];
// Keep both after Replace does not overwrite name-2, and counts on.
const k1 = upItem({ answer: '' }, f(1), 'car.png');
upApply(k1, 'replace');
upApply(k1, 'keep');
out.keepAfterReplace = [k1.overwrite, k1.path];
k1.state = 'sending';
out.keepAfterReplaceNext = [upAfter(k1, 409, { error: 'exists' }), k1.path, k1.overwrite];
upApply(k1, 'replace'); upApply(k1, 'skip');
out.skipClears = [k1.state, k1.overwrite];

// Two names typed at once: one ending does not free the tree for the other.
const hv = {};
const relA = docsHold(hv), relB = docsHold(hv);
out.hold = [hv.hold, relA(), hv.hold, relA(), hv.hold, relB(), hv.hold];
const relC = docsHold(hv); docsHoldReset(hv); const relD = docsHold(hv);
out.holdRemount = [relC(), hv.hold, relD(), hv.hold];

// Putting a deleted folder back.
const heldL = { dirs: ['Leasing/2026', 'Leasing/2026/Q1', 'Leasing/Empty'], files: ['Leasing/offer.pdf', 'Leasing/2026/march.pdf', 'Leasing/nokept.pdf'], complete: true };
const delRows = [{ path: 'Leasing/offer.pdf', rev: 'r1', from: 'r1' }, { path: 'Leasing/2026/march.pdf', rev: 'r1', from: 'r1' },
                 { path: 'Leasing2/x.pdf', rev: 'r1', from: 'r1' }, { path: 'Leasing/offer.pdf', rev: 'r0', from: 'r0' }];
const pl = docsRestorePlan(delRows, 'Leasing', heldL, 'r1');
out.plan = [pl.rows.map(d => d.path + '@' + d.from), pl.mkdirs, pl.missing, pl.sure];
// A file deleted from the folder before is not brought back with it.
const boxRows = [{ path: 'Box/current.txt', rev: 'bbb222' }, { path: 'Box/sub/deep.txt', rev: 'bbb222' }, { path: 'Box/old.txt', rev: 'aaa111' },
                 { path: 'Box/sub/older.txt', rev: 'aaa111' }];
const boxHeld = { dirs: ['Box/sub'], files: ['Box/current.txt', 'Box/sub/deep.txt'], unread: [], complete: true };
const boxPart = { dirs: ['Box/sub'], files: ['Box/current.txt'], unread: ['Box/sub'], complete: false };
const paths = p => p.rows.map(d => d.path);
out.planRev = [paths(docsRestorePlan(boxRows, 'Box', boxHeld, 'bbb222')), paths(docsRestorePlan(boxRows, 'Box', boxHeld, 'bbb2')),
               paths(docsRestorePlan(boxRows, 'Box', boxHeld, '')), paths(docsRestorePlan(boxRows, 'Box', boxPart, 'bbb222')),
               paths(docsRestorePlan(boxRows, 'Box', boxPart, '')), docsRestorePlan(boxRows, 'Box', boxPart, 'bbb222').sure,
               docsRestorePlan(boxRows, 'Box', boxHeld, 'bbb222').sure];
// No snapshot of the delete: an older deletion of the same path (Box/old.txt
// deleted, made again, deleted unrecorded) would come back, so nothing does.
const unrec = docsRestorePlan([{ path: 'Box/old.txt', rev: 'aaa111' }], 'Box', { dirs: [], files: ['Box/old.txt'], unread: [], complete: true }, '');
out.planUnrecorded = [unrec.rows.length, unrec.mkdirs, unrec.sure, unrec.unrecorded, docsRestorePlan(boxRows, 'Box', boxHeld, 'bbb222').unrecorded];
// A folder read whole that held only a file too big to keep is not "back, empty as it was".
const pbig = docsRestorePlan(delRows, 'Movies', { dirs: [], files: [], unread: [], unkept: 1, complete: true }, 'r1');
out.planUnkept = [pbig.rows.length, pbig.mkdirs, pbig.missing, pbig.sure];
// A row renamed while another name is typed: it and its folder's rows wait.
const node = ds => { const n = { dataset: ds, cls: [], attrs: {}, inert: false, draggable: true }; n.classList = { add: c => n.cls.push(c) }; n.setAttribute = (k, val) => { n.attrs[k] = val; }; return n; };
const rowA = node({ path: 'C:\\P\\M\\Leasing' });
const kA = node({ dir: 'C:\\P\\M\\Leasing' }), kA2 = node({ dir: 'C:\\P\\M\\Leasing\\2026' }), kB = node({ dir: 'C:\\P\\M\\Leasing2' });
docsSettle({ querySelectorAll: () => [kA, kA2, kB] }, rowA);
out.settle = [rowA.inert, rowA.draggable, rowA.cls, kA.inert, kA2.inert, kB.inert, rowA.attrs['aria-busy']];
const pe = docsRestorePlan(delRows, 'Empty', { dirs: [], files: [], complete: true }, 'r1');
out.planEmpty = [pe.rows.length, pe.mkdirs, pe.missing];
const pn = docsRestorePlan(delRows, 'Leasing', Object.assign({}, heldL, { complete: false }), 'r1');
out.planPartialWalk = [pn.missing, pn.sure];
const pu = docsRestorePlan(delRows, 'Leasing', null, 'r1');
out.planUnknown = [pu.rows.length, pu.mkdirs, pu.missing, pu.sure];
out.names = [upKeepName('ads/photo.jpg', 2), upKeepName('README', 2), upKeepName('.env', 3), upKeepName('a/b.tar.gz', 2)];
out.nameErr = ['', ' ', 'a/b', 'a\\b', '..', 'Leasing 2026.pdf'].map(upNameErr);

// ---- the walk of a dropped folder
const file = (name, fail) => ({ name, isFile: true, isDirectory: false, file: (ok, bad) => setTimeout(() => fail ? bad(new Error('no')) : ok({ name, size: name.length })) });
const dir = (name, kids, per) => ({ name, isFile: false, isDirectory: true, createReader: () => {
  let at = 0; per = per || 100;
  return { readEntries: (ok) => setTimeout(() => { const b = kids.slice(at, at + per); at += per; ok(b); }) };
} });
const many = Array.from({ length: 5 }, (_, i) => file(`p${i}.jpg`));
const dropped = [file('one.jpg'), dir('Leasing', [file('offer.pdf'), file('.DS_Store'), dir('2026', [file('march.pdf')]), dir('empty', []), file('bad.doc', true)]),
                 dir('Photos', many, 2)];

// The drop's entries, or null for the flat list.
out.entriesOk = upDropEntries({ items: [{ kind: 'file', webkitGetAsEntry: () => dropped[0] }, { kind: 'string' }] }).length;
out.entriesNone = upDropEntries({ items: [{ kind: 'file' }] });
out.entriesEmpty = upDropEntries({ items: [] });

// Move to: every folder but where it is, itself, inside it, and what is kept apart.
const files = ['README.md', 'Leasing/offer.pdf', 'Leasing/2026/march.pdf', 'Ads/BMW/x.jpg', 'sort_papers/notes.md', '_linked/chat.md'];
out.foldersFile = docsFolders(files, ['Empty', 'Leasing/Old'], 'Leasing/offer.pdf', false, new Set(['sort_papers']));
out.foldersDir = docsFolders(files, [], 'Leasing', true, new Set(['sort_papers']));
out.moveOk = [docsMoveOk({ rel: 'Leasing', dir: true }, 'Leasing/2026'), docsMoveOk({ rel: 'Leasing', dir: true }, 'Leasing'),
              docsMoveOk({ rel: 'Ads/x.jpg', dir: false }, 'Ads'), docsMoveOk({ rel: 'Ads/x.jpg', dir: false }, ''),
              docsMoveOk({ rel: 'Lease', dir: true }, 'Leasing'), docsMoveOk(null, '')];
out.moved = [docsMoved('C:\\P\\M\\Leasing\\a.pdf', 'C:\\P\\M\\Leasing', 'C:\\P\\M\\Old\\Leasing'),
             docsMoved('C:\\P\\M\\leasing', 'C:\\P\\M\\Leasing', 'C:\\P\\M\\Lease'),
             docsMoved('C:\\P\\M\\Leasing2\\a.pdf', 'C:\\P\\M\\Leasing', 'C:\\P\\M\\X')];

// The tree: menu and dragging on the documents Overview only.
const entries = [{ name: 'Leasing', type: 'dir' }, { name: 'README.md', type: 'file', size: 10 }];
const view = (ctx, root) => ({ ctx, roots: [{ path: root, kind: 'project', label: 'P' }], dirs: new Map([[root, { entries }]]),
                               open: new Set(), mark: '', sel: '', hide: new Set(), hideAllDirs: false, hideReady: true });
out.docsRows = wsRowsHtml(view({ kind: 'project', projectId: 'p-docs', docs: true }, 'C:\\P\\Motors'), 'C:\\P\\Motors');
out.docsWsTab = wsRowsHtml(view({ kind: 'project', projectId: 'p-docs' }, 'C:\\P\\Motors'), 'C:\\P\\Motors');
out.codeRows = wsRowsHtml(view({ kind: 'project', projectId: 'p-code' }, 'C:\\P\\Opten'), 'C:\\P\\Opten');

// ---- "Task folders"
const nm = html => [...html.matchAll(/class="nm">([^<]*)</g)].map(m => m[1]);
const mo = 'C:\\P\\Motors', x5 = mo + '\\selling_x5';
const topEntries = [{ name: 'Leasing', type: 'dir' }, { name: 'selling_x5', type: 'dir', task: true }, { name: '_linked', type: 'dir' },
                    { name: '.history', type: 'dir' }, { name: 'README.md', type: 'file', size: 3 }];
const tview = () => ({ ctx: { kind: 'project', projectId: 'p-docs', docs: true }, roots: [{ path: mo, kind: 'project', label: 'Motors' }],
  dirs: new Map([[mo, { entries: topEntries }], [x5, { entries: [{ name: 'ad.md', type: 'file', size: 4 }, { name: 'photos', type: 'dir' }] }],
                 [x5 + '\\photos', { entries: [{ name: 'front.jpg', type: 'file', size: 9 }] }]]),
  open: new Set([x5, x5 + '\\photos']), mark: '', sel: '', hide: new Set(), hideAllDirs: false, hideReady: true });
// No storage at all (a blocked one throws): unchecked, then kept for the page.
out.tasksDefault = [docsTasksShown(), nm(wsRowsHtml(tview(), mo))];
docsTasksSet(true);
const shownRows = wsRowsHtml(tview(), mo);
out.tasksShown = [docsTasksShown(), nm(shownRows)];
out.tasksHeld = [(shownRows.match(/data-task="1"/g) || []).length, (shownRows.match(/draggable="true"/g) || []).length,
                 (shownRows.match(/class="wse-more"/g) || []).length, /data-path="[^"]*photos" data-mt="0" data-task="1"/.test(shownRows),
                 /title="Open or see its history"/.test(shownRows)];
out.inTask = [docsInTask(tview(), x5), docsInTask(tview(), x5 + '\\photos\\deep'), docsInTask(tview(), mo + '\\Leasing'),
              docsInTask(tview(), mo), docsInTask(tview(), 'C:\\P\\Motors2\\selling_x5')];
// The browser's storage remembers it, across a reload of the page.
const store = {};
globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, val) => { store[k] = String(val); } };
docsTasksSet(false);
out.stored = [store[DOCS_TASKS_KEY], docsTasksShown()];
store[DOCS_TASKS_KEY] = '1'; DOCS_TASKS = false;          // a reload: nothing in memory, the box was left checked
out.remembered = docsTasksShown();
// A find filtered for the other setting (checked in another project, or in
// another copy of the page) is read again; a busy one and a code project's not.
const sv = tview();
out.findStale = [docsFindStale(sv, { tasks: true }), docsFindStale(sv, { tasks: false }), docsFindStale(sv, { busy: true }),
                 docsFindStale(sv, null), docsFindStale(sv, { error: 'x' }),
                 docsFindStale(Object.assign(tview(), { ctx: { kind: 'project', projectId: 'p-code' } }), {})];
// Go to file and a text search leave out what the tree leaves out.
const hiddenNames = docsApartNames(topEntries, false), shownNames = docsApartNames(topEntries, true);
out.apartNames = [[...hiddenNames].sort(), [...shownNames].sort()];
const listed = ['README.md', 'Leasing/offer.pdf', 'selling_x5/ad.md', 'selling_x5/photos/front.jpg', '_linked/room-1/chat.md', '.history/HEAD', 'selling_x5.md'];
out.gotoHidden = listed.filter(p => !docsApartPath(p, hiddenNames));
out.gotoShown = listed.filter(p => !docsApartPath(p, shownNames));
const found = { files: [{ path: 'Leasing/offer.pdf', matches: [{ line: 1, ranges: [[0, 3]], n: 2 }] },
                        { path: 'selling_x5/ad.md', matches: [{ line: 2, ranges: [[0, 3]], n: 1 }, { line: 9, ranges: [[1, 3]] }] }],
                matches: 4, truncated: false, filesSearched: 12 };
const fh = docsFindApart(found, hiddenNames);
out.searchHidden = [fh.files.map(f => f.path), fh.matches, fh.filesSearched, found.files.length];
out.searchShown = docsFindApart(found, shownNames) === found;

out.who = [histWho({ kind: 'user', name: 'sam', reason: 'upload' }), histDid({ kind: 'user', name: 'sam', reason: 'upload' }),
           histWho({ kind: 'you', label: 'you', reason: 'scan' }), histDid({ kind: 'you', reason: 'scan' }),
           histWho({ kind: 'task', label: 'Sort papers' }), histWho(null), histDid({ reason: 'delete' }), histDid({ reason: 'move' })];

(async () => {
  // The find's folders: from the tree's listing, or read when the tree has none;
  // none for a code project's Workspace.
  const reads0 = [];
  api = async url => { reads0.push(url); return { entries: topEntries }; };
  const av = tview();
  store[DOCS_TASKS_KEY] = '0';
  out.apartOf = [[...(await docsApartOf(av))].sort(), reads0.length];
  av.dirs.delete(mo); store[DOCS_TASKS_KEY] = '1';
  out.apartOfRead = [[...(await docsApartOf(av))].sort(), reads0.length, av.dirs.has(mo)];
  out.apartOfCode = await docsApartOf(Object.assign(tview(), { ctx: { kind: 'project', projectId: 'p-code' } }));
  api = async () => { throw new Error('no hub'); };

  const walked = await upWalk(dropped);
  out.walk = walked.map(x => x.dir ? x.rel + '/' : x.err ? '!' + x.rel : x.rel);
  out.walkFile = walked[0].file.name;

  // Every folder of the project for Move to, a collapsed empty one too.
  const fsTree = {
    '': [{ name: 'Leasing', type: 'dir' }, { name: 'Ads', type: 'dir' }, { name: 'sort_papers', type: 'dir', task: true }, { name: 'README.md', type: 'file', size: 3 }],
    'Leasing': [{ name: '2026', type: 'dir' }, { name: 'offer.pdf', type: 'file', size: 5 }, { name: '.DS_Store', type: 'file', size: 1 },
                { name: '~$offer.docx', type: 'file', size: 1 }, { name: '.a.pdf.x.upload.tmp', type: 'file', size: 1 }, { name: 'big.mov', type: 'file', size: UP_MAX + 1 }],
    'Leasing/2026': [{ name: 'Q1', type: 'dir' }], 'Leasing/2026/Q1': [], 'Ads': [], 'sort_papers': [{ name: 'x', type: 'dir' }],
  };
  const reads = [];
  const rd = rel => { reads.push(rel); return rel in fsTree ? Promise.resolve(fsTree[rel]) : Promise.reject(new Error('404')); };
  const w1 = await docsWalk(rd, '', { skip: (rel, e) => !upDir(rel) && e.task });
  out.walkAll = [w1.dirs, w1.files, w1.complete, reads.includes('sort_papers')];
  out.walkFolders = docsFolders([], w1.dirs, 'README.md', false, new Set());
  const w2 = await docsWalk(rd, '', { max: 2 });
  out.walkMax = [w2.dirs.length, w2.complete, w2.unread];
  const w3 = await docsWalk(r => r === 'Ads' ? Promise.reject(new Error('x')) : rd(r), '');
  out.walkFail = [w3.complete, w3.dirs.includes('Leasing/2026/Q1'), w3.unread];
  const w4 = await docsWalk(rd, 'Leasing');
  out.walkFolder = [w4.dirs, w4.files, w4.unkept];

  // The Deleted list is read to its end for a folder of more than a page.
  const all = Array.from({ length: 450 }, (_, i) => ({ rev: 'r' + (i %% 7), path: `Big/f${String(i).padStart(3, '0')}.txt`, from: 'p' }));
  const asked = [];
  api = async (url) => {
    asked.push(url);
    const m = /&after=([^&]+)/.exec(url), at = m ? decodeURIComponent(m[1]) : '';
    const i = at ? all.findIndex(x => `${x.rev}:${x.path}` === at) + 1 : 0;
    return { files: all.slice(i, i + 200), more: i + 200 < all.length };
  };
  const got = await docsDeletedRows('p-docs');
  out.pages = [got.length, asked.length, new Set(got.map(x => x.path)).size, docsRestorePlan(got, 'Big', null, 'r3').rows.map(d => d.path).pop()];
  asked.length = 0;
  const one = await docsDeletedRows('p-docs', d => d.path === 'Big/f005.txt');
  out.pagesStop = [one.length, asked.length];

  // A question or progress for Motors while another project is on screen
  // waits for Motors, and shows when it is back.
  const calls = [], focus = [];
  const box = { hidden: true, innerHTML: '', querySelector: () => ({ focus: () => focus.push(1) }) };
  const fel = { isConnected: true, dataset: { proj: 'p-other' }, querySelector: s => { calls.push(s); return s === '.dcs-ask' ? box : null; } };
  const answer = docsAsk(fel, 'p-docs', { name: 'car.png', folder: 'Motors', keep: 'car-2.png', many: true });
  docsUpPaint(fel, 'p-docs');
  docsUpBar(fel, 'p-docs', { id: 1 });
  out.otherProject = [calls.length, box.hidden, docsFp('p-docs').asks.length, docsFp('p-other').asks.length];
  fel.dataset.proj = 'p-docs';
  docsAskShow(fel, 'p-docs');
  out.backHere = [box.hidden, /car\.png/.test(box.innerHTML), /Apply to all/.test(box.innerHTML), focus.length];
  docsFp('p-docs').asks.shift().resolve({ choice: 'skip', all: false });
  out.answered = (await answer).choice;
  fel.isConnected = false; calls.length = 0;
  docsAskShow(fel, 'p-docs');
  out.gone = calls.length;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ThePureParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = PAGE % {"deps": "\n".join(js_function(n) for n in DEPS), "block": pure_block(),
                      "wiring": "\n".join(js_function(n) for n in WIRING)}
        r = subprocess.run([NODE, "-"], input=src, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_at_most_three_out_in_order(self):
        o = self.out
        self.assertEqual(o["first"], ["a.jpg", "b.jpg", "c.jpg"], "the file too large is never sent")
        self.assertEqual(o["full"], [])
        self.assertEqual(o["afterOne"], ["d.jpg"])
        self.assertEqual(o["pct"], [50, 0])
        self.assertEqual(o["doneState"], ["done", 100])

    def test_too_large_is_refused_before_sending_with_the_limit(self):
        state, final, err = self.out["big"]
        self.assertEqual((state, final), ("failed", True))
        self.assertEqual(err, "big.mov is 60 MB; files over 50 MB are not added.")
        self.assertEqual(self.out["unreadable"], ["failed", True, "e.jpg could not be read from this computer."])

    def test_a_failure_shows_the_hubs_sentence_and_can_be_retried(self):
        o = self.out
        self.assertEqual(o["failed"], ["failed", False, "The disk is full."])
        self.assertEqual(o["retry"], [True, "queued", False, "failed"])
        self.assertEqual(o["offline"], ["The hub could not be reached.", False])
        self.assertEqual(o["refused"], [True, "Files over 50 MB are not accepted.", False])
        self.assertEqual(o["line"], "Adding 1 file · 4 not added")
        self.assertEqual(o["shown"], ["b.jpg", "big.mov", "c.jpg", "d.jpg", "e.jpg"])
        self.assertIn('class="rm-btn subtle dcu-x"', o["rowFailed"])
        self.assertNotIn("dcu-retry", o["rowFailed"], "no Retry for what would be refused again")
        self.assertIn("could not be read", o["rowFailed"])
        self.assertIn('aria-valuenow="50"', o["rowSending"])
        self.assertIn(">y.pdf<", o["rowSending"])
        self.assertEqual(o["sig"], "same", "progress does not rewrite a row")

    def test_already_there_is_asked_once_per_batch(self):
        o = self.out
        self.assertEqual([o["ask1"], o["ask2"], o["asking"]], ["asking", "asking", "p.jpg"])
        self.assertEqual(o["keepAll"], ["queued", "ads/p-2.jpg", "queued", "ads/q-2.jpg", "keep"])
        self.assertEqual(o["laterSameBatch"], ["queued", "ads/r-2.jpg"])
        self.assertEqual(o["otherBatchStillAsks"], "asking")
        self.assertEqual(o["keepCounts"], ["queued", "ads/p-3.jpg"])
        self.assertEqual(o["replaceOne"], ["queued", True, ""])
        self.assertEqual(o["skipOne"], ["skipped", "asking", ""])
        self.assertEqual(o["skipAll"], ["skipped", "skip", 0])

    def test_replace_refused_again_is_final_and_keep_both_never_overwrites(self):
        o = self.out
        self.assertEqual(o["replaceAll"], ["queued", True, "queued", True])
        self.assertEqual(o["replaceAgain"], ["failed", True, "A folder named “Leasing” is already there.", 0, False],
                         "not queued again, so no endless loop")
        self.assertEqual(o["replaceAgainNoMessage"], ["failed", True, "Ads cannot replace what is there."])
        self.assertEqual(o["keepAfterReplace"], [False, "car-2.png"])
        self.assertEqual(o["keepAfterReplaceNext"], ["queued", "car-3.png", False])
        self.assertEqual(o["skipClears"], ["skipped", False])

    def test_one_name_typed_does_not_free_the_tree_for_another(self):
        self.assertEqual(self.out["hold"], [2, False, 1, False, 1, True, 0])
        self.assertEqual(self.out["holdRemount"], [False, 1, True, 0])
        self.assertEqual(self.out["settle"], [True, False, ["wse-wait"], True, True, False, "true"],
                         "the renamed row and its folder's rows wait; a sibling with a longer name does not")

    def test_move_to_reads_every_folder_empty_ones_too(self):
        o = self.out
        dirs, files, complete, read_task = o["walkAll"]
        self.assertEqual(dirs, ["Leasing", "Ads", "Leasing/2026", "Leasing/2026/Q1"])
        self.assertEqual(files, ["README.md", "Leasing/offer.pdf"], "only files the history keeps")
        self.assertTrue(complete)
        self.assertFalse(read_task, "a task folder is not walked")
        self.assertEqual(o["walkFolders"], ["Ads", "Leasing", "Leasing/2026", "Leasing/2026/Q1"])
        self.assertEqual(o["walkMax"], [2, False, ["sort_papers", "Leasing/2026"]])
        self.assertEqual(o["walkFail"], [False, True, ["Ads", "sort_papers/x"]])
        self.assertEqual(o["walkFolder"], [["Leasing/2026", "Leasing/2026/Q1"], ["Leasing/offer.pdf"], 1],
                         "big.mov counts as not kept; .DS_Store, ~$ and .tmp files do not")

    def test_a_folder_is_restored_whole_or_says_what_is_missing(self):
        o = self.out
        self.assertEqual(o["plan"], [["Leasing/offer.pdf@r1", "Leasing/2026/march.pdf@r1"], ["Leasing/2026/Q1", "Leasing/Empty"], 1, True])
        self.assertEqual(o["planEmpty"], [0, ["Empty"], 0], "an empty folder is made again")
        self.assertEqual(o["planPartialWalk"], [0, False], "a partial reading claims no count and is not sure")
        self.assertEqual(o["planUnknown"], [2, [], 0, False])
        by_rev, short_rev, no_rev, part_rev, part_no_rev, part_sure, full_sure = o["planRev"]
        self.assertEqual(by_rev, ["Box/current.txt", "Box/sub/deep.txt"], "only the rows this delete recorded")
        self.assertEqual(short_rev, by_rev)
        self.assertEqual(no_rev, [], "without the delete's snapshot nothing tells its rows from older ones")
        self.assertEqual(part_rev, by_rev)
        self.assertEqual(part_no_rev, [])
        self.assertEqual((part_sure, full_sure), (False, True))
        self.assertEqual(o["planUnrecorded"], [0, [], False, True, False],
                         "an older deletion of the same path is not put back when the delete was not recorded")
        self.assertEqual(o["planUnkept"], [0, ["Movies"], 1, True], "a file too big to keep counts as missing")
        self.assertEqual(o["pages"], [450, 3, 450, "Big/f444.txt"], "every page of the Deleted list")
        self.assertEqual(o["pagesStop"], [200, 1], "a file stops at the page that has it")

    def test_another_projects_questions_and_progress_wait_for_it(self):
        o = self.out
        self.assertEqual(o["otherProject"], [0, True, 1, 0])
        self.assertEqual(o["backHere"], [False, True, True, 1])
        self.assertEqual(o["answered"], "skip")
        self.assertEqual(o["gone"], 0)

    def test_names(self):
        self.assertEqual(self.out["names"], ["ads/photo-2.jpg", "README-2", ".env-3", "a/b.tar-2.gz"])
        self.assertEqual(self.out["nameErr"][0], "Type a name.")
        self.assertEqual(self.out["nameErr"][1], "Type a name.")
        self.assertIn("/", self.out["nameErr"][2])
        self.assertIn("/", self.out["nameErr"][3])
        self.assertIn("cannot be a name", self.out["nameErr"][4])
        self.assertEqual(self.out["nameErr"][5], "")

    def test_a_dropped_folder_keeps_its_structure(self):
        self.assertEqual(self.out["walk"], [
            "one.jpg", "Leasing/offer.pdf", "Leasing/2026/march.pdf", "Leasing/empty/", "!Leasing/bad.doc",
            "Photos/p0.jpg", "Photos/p1.jpg", "Photos/p2.jpg", "Photos/p3.jpg", "Photos/p4.jpg",
        ])
        self.assertEqual(self.out["walkFile"], "one.jpg")
        self.assertEqual(self.out["entriesOk"], 1)
        self.assertIsNone(self.out["entriesNone"], "no entries: the flat file list is used")
        self.assertIsNone(self.out["entriesEmpty"])

    def test_move_to_offers_the_other_folders(self):
        o = self.out
        self.assertEqual(o["foldersFile"], ["", "Ads", "Ads/BMW", "Empty", "Leasing/2026", "Leasing/Old"])
        self.assertEqual(o["foldersDir"], ["Ads", "Ads/BMW"])
        self.assertEqual(o["moveOk"], [False, False, False, True, True, False])
        self.assertEqual(o["moved"], ["C:\\P\\M\\Old\\Leasing\\a.pdf", "C:\\P\\M\\Lease", ""])

    def test_rows_have_a_menu_and_drag_only_in_a_documents_workspace(self):
        o = self.out
        self.assertEqual(o["docsRows"].count('class="wse-more"'), 2)
        self.assertEqual(o["docsRows"].count('draggable="true"'), 2)
        self.assertIn('aria-label="Actions for README.md"', o["docsRows"])
        for html in (o["docsWsTab"], o["codeRows"]):
            self.assertNotIn("wse-more", html)
            self.assertNotIn("draggable", html)

    def test_task_folders_are_hidden_until_checked_and_history_and_linked_never_show(self):
        o = self.out
        self.assertEqual(o["tasksDefault"], [False, ["Leasing", "README.md"]])
        self.assertEqual(o["tasksShown"], [True, ["Leasing", "selling_x5", "ad.md", "photos", "front.jpg", "README.md"]],
                         "the task's folder with the files it made there; _linked and .history still hidden")
        self.assertEqual(o["apartNames"], [[".history", "_linked", "selling_x5"], [".history", "_linked"]])

    def test_the_choice_is_remembered_by_the_browser(self):
        o = self.out
        self.assertEqual(o["stored"], ["0", False])
        self.assertTrue(o["remembered"], "a reload finds the box as it was left")

    def test_a_find_left_from_the_other_setting_is_read_again(self):
        self.assertEqual(self.out["findStale"], [False, True, False, False, True, False])
        self.assertIn("if (docsFindStale(v, f.list) || docsFindStale(v, f.res)) docsFindReset(f);", js_function("wsfEnsure"))
        lst = js_function("wsfList")
        self.assertIn("&& !docsFindStale(v, f.list))) return;", lst)
        self.assertEqual(lst.count("tasks: v.ctx.docs ? docsTasksShown() : undefined"), 2, "an error is kept for its setting too")
        self.assertIn("old.tasks = list.tasks;", lst)
        self.assertIn("if (v.ctx.docs) res.tasks = docsTasksShown();", js_function("wsfSearch"))
        # Another copy of the page: the Workspace on screen follows at once.
        i = INDEX.index("window.addEventListener('storage', e => {\n  if (e.key !== DOCS_TASKS_KEY) return;")
        listener = INDEX[i:INDEX.index("\n});", i)]
        self.assertIn("cb.checked = docsTasksShown();", listener)
        self.assertIn("docsTasksFollow(v);", listener)

    def test_a_tasks_folder_opens_but_is_its_tasks(self):
        held, draggable, menus, nested, tip = self.out["tasksHeld"]
        self.assertEqual(held, 4, "the task's folder and everything shown in it")
        self.assertEqual(draggable, 2, "only the project's own rows are dragged")
        self.assertEqual(menus, 6, "every row still opens, and a file shows its history")
        self.assertTrue(nested)
        self.assertTrue(tip)
        self.assertEqual(self.out["inTask"], [True, True, False, False, False])

    def test_go_to_file_and_text_search_leave_out_what_the_tree_does(self):
        o = self.out
        self.assertEqual(o["gotoHidden"], ["README.md", "Leasing/offer.pdf", "selling_x5.md"])
        self.assertEqual(o["gotoShown"], ["README.md", "Leasing/offer.pdf", "selling_x5/ad.md", "selling_x5/photos/front.jpg", "selling_x5.md"])
        self.assertEqual(o["searchHidden"], [["Leasing/offer.pdf"], 2, 12, 2], "the count is recounted, the hub's result untouched")
        self.assertTrue(o["searchShown"])
        self.assertEqual(o["apartOf"], [[".history", "_linked", "selling_x5"], 0])
        self.assertEqual(o["apartOfRead"], [[".history", "_linked"], 1, True], "the root is read when the tree has not")
        self.assertIsNone(o["apartOfCode"])

    def test_recent_changes_names_who_and_what(self):
        self.assertEqual(self.out["who"], ["sam", "uploaded", "you", "", "Sort papers", "you", "deleted", "moved"])


class TheMarkup(unittest.TestCase):
    def test_add_files_is_a_multiple_file_input_and_new_folder_asks_inline(self):
        i = INDEX.index("function docsPanelHtml(")
        panel = INDEX[i:INDEX.index("\n}\n", i)]
        self.assertIn('<input type="file" multiple class="dcs-pick">', panel)
        self.assertNotIn("accept=", panel, "a phone offers Photos and Files only without a filter")
        self.assertIn(">New folder<", panel)
        self.assertIn('<input type="checkbox" class="dcs-tasks-cb"', panel)
        self.assertIn("> Show task folders</label>", panel)
        self.assertIn('class="dcs-all dcs-tasks"', panel, "the box looks like the panel's other checkbox")
        wire = js_function("docsFilesWire")
        self.assertIn("tasks.onchange = () => docsTasksToggle(v, tasks.checked);", wire)
        # A drop over a task's folder is refused with a reason, never sent to the project's top.
        self.assertIn("if (t && t.closest('.wsp-tree .wse[data-task]')) return { held: true };", wire)
        self.assertIn("if (at.held) return;", wire)
        self.assertIn("nothing is added or moved here", wire)
        held = wire[wire.index("if (at.held) {"):wire.index("if (mv && (!at.row")]
        self.assertIn("e.dataTransfer.dropEffect = 'none';", held)
        self.assertNotIn("classList.add('over')", held)
        self.assertIn(".wse.dir:not([data-task])", js_function("docsTarget"), "nor where Add files and New folder put things")
        self.assertIn("v.mark = d.dataset.task ? '' : d.dataset.path;", wire, "selecting a task's folder selects no folder")
        self.assertIn("v.mark = docsInTask(v, c.abs) ? '' : c.abs;", wire)
        self.assertNotIn("leading its Overview", INDEX)
        menu = js_function("docsMenuOpen")
        self.assertIn("held ? '' : item('rename', 'Rename') + item('moveto', 'Move to…')", menu)
        self.assertIn("which its task looks after", menu)
        self.assertIn("const apart = v.ctx.docs ? await docsApartOf(v) : null;", js_function("wsfList"))
        self.assertIn("const apart = res.files && v.ctx.docs ? await docsApartOf(v) : null;", js_function("wsfSearch"))
        self.assertIn("if (apart) res = docsFindApart(res, apart);", js_function("wsfSearch"))
        block = INDEX[INDEX.index("// ---- Files panel: begin"):INDEX.index("// ---- Files panel: end")]
        self.assertNotRegex(block, r"\bprompt\(|\bconfirm\(|\balert\(")
        self.assertIn("webkitGetAsEntry", block)
        self.assertNotIn(":has(", block)

    def test_one_drop_target_menu_closes_on_any_scroll_phone_targets(self):
        block = INDEX[INDEX.index("// ---- Files panel: begin"):INDEX.index("// ---- Files panel: end")]
        self.assertIn("sec.classList.toggle('over', files && !at.row)", block, "the folder row, not the panel too")
        listen = js_function("docsDocListen")
        self.assertIn("document.addEventListener('scroll', away, { capture: true, passive: true })", listen)
        self.assertIn("pdOnResize(away)", listen, "a resize of the page's window or of a panel's own")
        self.assertNotIn("addEventListener('resize'", js_function("docsFilesWire"), "not one more listener per mount")
        self.assertNotIn("_asks", block)
        # A renamed row waiting for the repaint is not opened, menued or dragged.
        wire = js_function("docsFilesWire")
        self.assertIn("if (e.target.closest('.wse-wait')) return;", wire)
        self.assertIn("e.target.closest('.wse-edit, .wse-wait')", wire)
        self.assertIn(".wse[draggable=\"true\"]:not(.wse-wait)", wire)
        self.assertIn("e.target.closest('.wse-more, .wse-edit, .wse-wait')", js_function("wsMount"))
        phone = re.search(r"^\s*\.dcm \.ov-item, [^{]*\{ min-height: var\(--touch-min\); \}", INDEX, re.M)
        self.assertIsNotNone(phone)
        self.assertIn(".dcs-all", phone.group(0))

    def test_the_hub_contract(self):
        block = INDEX[INDEX.index("// ---- Files panel: begin"):INDEX.index("// ---- Files panel: end")]
        for path in ("/api/files/upload?project=", "/api/files/mkdir", "/api/files/move", "/api/files/delete"):
            self.assertIn(path, block)
        self.assertIn("&overwrite=1", block)
        self.assertIn("/api/history/restore", block)


if __name__ == "__main__":
    unittest.main()
