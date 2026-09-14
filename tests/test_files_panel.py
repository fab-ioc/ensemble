"""The Files panel of a documents project's Overview: drop, Add files, folders,
move, rename and delete (index.html's "Files panel" block).

* the pure parts, run in Node (skipped without Node): the upload queue (at
  most three out, a file over 50 MB refused before it is sent, what each hub
  answer does to a file, Retry), the batch's answer to "already there"
  (asked once, "apply to all", Keep both counting on), the walk of a dropped
  folder (paths, batches of entries, empty folders, unreadable files, what
  Finder leaves behind), the folders offered by Move to, and paths after a move;
* the tree rows get a menu and dragging only on the documents Overview, not
  in a code project or a task's Workspace;
* Recent changes says who and what: "ceo · uploaded".
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
        "projectById", "registeredProjects", "wsHidden", "wsProjectForRow", "wsTaskFolder", "histWho", "HIST_DID", "histDid"]

PAGE = r"""
const out = {};
const UNASSIGNED_ID = '__unassigned__';
let ALL_ROWS = [];
const PROJECTS = { projects: [
  { id: 'p-docs', name: 'Motors', kind: 'documents', registered: true, path: 'C:\\P\\Motors', home: 'C:\\P\\Motors', sessions: [] },
  { id: 'p-code', name: 'Opten', kind: 'code', registered: true, path: 'C:\\P\\Opten', home: 'C:\\P\\Opten', sessions: [] },
] };
%(deps)s
%(block)s

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

out.who = [histWho({ kind: 'user', name: 'ceo', reason: 'upload' }), histDid({ kind: 'user', name: 'ceo', reason: 'upload' }),
           histWho({ kind: 'you', label: 'you', reason: 'scan' }), histDid({ kind: 'you', reason: 'scan' }),
           histWho({ kind: 'task', label: 'Sort papers' }), histWho(null), histDid({ reason: 'delete' }), histDid({ reason: 'move' })];

(async () => {
  const walked = await upWalk(dropped);
  out.walk = walked.map(x => x.dir ? x.rel + '/' : x.err ? '!' + x.rel : x.rel);
  out.walkFile = walked[0].file.name;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class ThePureParts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = PAGE % {"deps": "\n".join(js_function(n) for n in DEPS), "block": pure_block()}
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

    def test_rows_have_a_menu_and_drag_only_on_the_documents_overview(self):
        o = self.out
        self.assertEqual(o["docsRows"].count('class="wse-more"'), 2)
        self.assertEqual(o["docsRows"].count('draggable="true"'), 2)
        self.assertIn('aria-label="Actions for README.md"', o["docsRows"])
        for html in (o["docsWsTab"], o["codeRows"]):
            self.assertNotIn("wse-more", html)
            self.assertNotIn("draggable", html)

    def test_recent_changes_names_who_and_what(self):
        self.assertEqual(self.out["who"], ["ceo", "uploaded", "you", "", "Sort papers", "you", "deleted", "moved"])


class TheMarkup(unittest.TestCase):
    def test_add_files_is_a_multiple_file_input_and_new_folder_asks_inline(self):
        i = INDEX.index("function docsPanelHtml(")
        panel = INDEX[i:INDEX.index("\n}\n", i)]
        self.assertIn('<input type="file" multiple class="dcs-pick">', panel)
        self.assertNotIn("accept=", panel, "a phone offers Photos and Files only without a filter")
        self.assertIn(">New folder<", panel)
        block = INDEX[INDEX.index("// ---- Files panel: begin"):INDEX.index("// ---- Files panel: end")]
        self.assertNotRegex(block, r"\bprompt\(|\bconfirm\(|\balert\(")
        self.assertIn("webkitGetAsEntry", block)
        self.assertNotIn(":has(", block)

    def test_the_hub_contract(self):
        block = INDEX[INDEX.index("// ---- Files panel: begin"):INDEX.index("// ---- Files panel: end")]
        for path in ("/api/files/upload?project=", "/api/files/mkdir", "/api/files/move", "/api/files/delete"):
            self.assertIn(path, block)
        self.assertIn("&overwrite=1", block)
        self.assertIn("/api/history/restore", block)


if __name__ == "__main__":
    unittest.main()
