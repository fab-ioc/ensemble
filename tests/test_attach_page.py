"""Images pasted or dropped into a chat box, on the page (static/attach.js and
its use in index.html), run in Node; skipped without Node:

* only PNG, JPEG, GIF and WebP files are taken from a paste or a drop, and a
  paste of text is left to the browser (no preventDefault);
* a pasted image is "yyyy-mm-dd HH.MM.SS screenshot.png" (.jpg, .gif, .webp);
* a box of images: a chip each while uploading, stored or refused, Remove,
  what a message carries ({room, name} of the stored ones), busy and failed;
  an image over 20 MB is refused without a request; images already stored
  (a comment being edited) come back as stored chips;
* a message's trailing "[image] <path>" lines are split off, and only those;
* a comment on a diff keeps its images: they are named in the message, part
  of what tells an edited comment from the one sent, and a malformed list is
  dropped from storage;
* a pasted image in the Files panel that meets a name already taken becomes
  "-2" without a question.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATTACH = (ROOT / "static" / "attach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def block(begin: str, end: str) -> str:
    i = INDEX.index(begin)
    return INDEX[i:INDEX.index(end, i)]


JS = r"""
const vm = require('vm');
const { attach, review, pure } = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {};
const fetches = [];
let reply = () => ({ ok: true, status: 200, json: async () => ({ ok: true, name: 'shot.png', path: 'C:\\t\\attachments\\shot.png', url: '/u/shot.png' }) });
const ctx = {
  console, setTimeout, clearTimeout,
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  fetch: async (url, o) => { fetches.push([url, o.headers, o.body && o.body.name]); return reply(url); },
  document: { addEventListener() {}, querySelector: () => null, querySelectorAll: () => [] },
  window: { addEventListener() {}, matchMedia: () => ({ matches: false }), getSelection: () => null },
  matchMedia: () => ({ matches: false }), performance: { now: () => 0 }, CSS: { escape: s => s },
  localStorage: { length: 0, key: () => null, getItem: () => null, setItem() {}, removeItem() {} },
  toast() {}, MOBILE_MQ: '', out,
};
vm.createContext(ctx);
vm.runInContext(attach + `
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
` + review + pure, ctx);
const run = src => vm.runInContext(src, ctx);

(async () => {
  // ---- what a paste or drop holds
  const file = (type, name, size) => ({ type, name, size: size || 10 });
  const item = f => ({ kind: 'file', type: f.type, getAsFile: () => f });
  ctx.dt = { items: [item(file('image/png', 'image.png')), { kind: 'string', type: 'text/plain' }, item(file('application/pdf', 'a.pdf')),
                     item(file('image/webp', 'w.webp')), item(file('image/svg+xml', 's.svg'))] };
  out.images = run('attImages(dt).map(f => f.name)');
  ctx.dt2 = { items: [], files: [file('image/gif', 'g.gif'), file('text/plain', 't.txt')] };
  out.filesOnly = run('attImages(dt2).map(f => f.name)');
  out.none = run('attImages(null).length');

  // ---- the paste handler: images only
  run(`
    const handlers = {};
    const field = { addEventListener: (k, fn) => { handlers[k] = fn; } };
    const added = [];
    attWire(field, null, { add: (files, pasted) => added.push([files.map(f => f.name), pasted]) });
    const ev = data => { const e = { clipboardData: data, prevented: false, preventDefault() { e.prevented = true; } }; return e; };
    const textPaste = ev({ items: [{ kind: 'string', type: 'text/plain' }] });
    handlers.paste(textPaste);
    const imgPaste = ev(dt);
    handlers.paste(imgPaste);
    out.paste = { textPrevented: textPaste.prevented, imgPrevented: imgPaste.prevented, added };
  `);

  // ---- names
  out.stamp = run(`[attStampName(new Date(2026, 8, 6, 7, 5, 3), 'image/png'), attStampName(new Date(2026, 11, 31, 23, 59, 59), 'image/jpeg'),
                   attStampName(new Date(2026, 0, 1, 0, 0, 0), 'image/gif'), attStampName(new Date(2026, 0, 1, 0, 0, 0), 'image/webp')]`);
  out.given = run(`[attGivenName({ name: 'image.png' }, false), attGivenName({ name: 'plan.png' }, false), attGivenName({ name: 'plan.png' }, true)]`);

  // ---- the box
  let changes = 0;
  ctx.onChange = () => { changes++; };
  run(`var box = attBox(() => 'room-1', onChange);`);
  run(`box.add([{ type: 'image/png', name: 'image.png', size: 10 }], true)`);
  out.whileUp = run(`({ busy: box.busy(), stored: box.stored(), html: box.html(esc) })`);
  await new Promise(r => setTimeout(r, 10));
  out.afterUp = run(`({ busy: box.busy(), stored: box.stored(), html: box.html(esc) })`);
  out.firstFetch = fetches[0];
  reply = () => ({ ok: false, status: 415, json: async () => ({ error: 'not_an_image', message: 'Only a PNG, JPEG, GIF or WebP image can be attached.' }) });
  run(`box.add([{ type: 'image/png', name: 'fake.png', size: 10 }], false)`);
  await new Promise(r => setTimeout(r, 10));
  out.failed = run(`({ failed: box.failed().map(x => x.err), stored: box.stored().length, html: box.html(esc) })`);
  const before = fetches.length;
  run(`box.add([{ type: 'image/png', name: 'huge.png', size: 21 * 1024 * 1024 }], false)`);
  await new Promise(r => setTimeout(r, 10));
  out.huge = { requests: fetches.length - before, err: run(`box.failed().slice(-1)[0].err`) };
  run(`box.list.filter(x => x.state === 'err').map(x => x.id).forEach(id => box.remove(id))`);
  out.afterRemove = run(`({ n: box.list.length, failed: box.failed().length })`);
  run(`box.clear()`);
  out.cleared = run(`box.list.length`);
  out.changes = changes;
  run(`var kept = attBox(() => 'room-2', () => {}); kept.keep([{ room: 'room-9', name: 'a.png' }, { name: 'no-room.png' }]);`);
  out.kept = run(`({ stored: kept.stored(), html: kept.html(esc) })`);
  ctx.noRoom = run(`attBox(() => '', () => {})`);
  run(`noRoom.add([{ type: 'image/png', name: 'x.png', size: 1 }], true)`);
  await new Promise(r => setTimeout(r, 10));
  out.noRoom = run(`noRoom.failed().map(x => x.err)`);

  // ---- a message's image lines
  out.split = run(`[attSplit('hi\\n\\n[image] C:\\\\a b\\\\x.png\\n[image] /t/y.png\\n'), attSplit('[image] x.png\\nwords'), attSplit('plain'), attSplit('[image] only.png')]`);
  out.own = run(`[attOwn('room-1', 'C:\\\\P\\\\t\\\\attachments\\\\a.png'), attOwn('room-1', '/s/attachments/room-1/a.png'),
                  attOwn('room-1', '/s/attachments/room-2/a.png'), attOwn('room-1', '/home/me/pics/a.png'), attOwn('', '/t/attachments/a.png')]`);
  out.thumbs = run(`attThumbsHtml('room-1', ['C:\\\\t\\\\attachments\\\\a "b".png'], esc)`);

  // ---- diff comments
  run(`var c1 = { cid: 'c1', root: 'R', file: 'a.py', rows: [{ k: 'add', n: 3, t: 'x = 1' }], span: 1, note: 'look', images: [{ room: 'room-1', name: 'shot.png' }, { room: 'room-1', name: 'b.png' }] };
       var c2 = { cid: 'c2', root: 'R', file: 'a.py', rows: [{ k: 'add', n: 4, t: 'y = 2' }], span: 1, note: '', images: [{ room: 'room-1', name: 'only.png' }] };`);
  out.message = run(`drMessage([c1, c2], '')`);
  out.valid = run(`[drValid(c1), drValid({ ...c1, images: undefined }), drValid({ ...c1, images: 'x' }), drValid({ ...c1, images: [{ room: 1 }] })]`);
  out.rev = run(`[drRev(c1) !== drRev({ ...c1, images: c1.images.slice(1) }), drRev({ ...c1, images: undefined }) === drRev({ ...c1, images: [] })]`);

  // ---- Files panel: a pasted name taken is numbered, never asked about
  run(`var b = { answer: '' }; var it = upItem(b, { size: 5 }, 'shots/2026-09-16 10.00.00 screenshot.png'); it.keep = 1;
       out.keepBoth = [upAfter(it, 409, { error: 'exists' }), it.path, upAfter(it, 409, { error: 'exists' }), it.path];`);

  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class AttachPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = {"attach": ATTACH,
                   "review": block("// ---- Diff review: begin", "// ---- Diff review: end"),
                   "pure": block("// ---- Files panel, pure: begin", "// ---- Files panel, pure: end")}
        out = subprocess.run([NODE, "-e", JS], input=json.dumps(payload), capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
        if out.returncode != 0:
            raise AssertionError(out.stderr)
        cls.r = json.loads(out.stdout)

    def test_only_images_are_taken(self):
        self.assertEqual(self.r["images"], ["image.png", "w.webp"])
        self.assertEqual(self.r["filesOnly"], ["g.gif"])
        self.assertEqual(self.r["none"], 0)

    def test_a_text_paste_is_left_alone(self):
        p = self.r["paste"]
        self.assertFalse(p["textPrevented"])
        self.assertTrue(p["imgPrevented"])
        self.assertEqual(p["added"], [[["image.png", "w.webp"], True]])

    def test_names(self):
        self.assertEqual(self.r["stamp"], ["2026-09-06 07.05.03 screenshot.png", "2026-12-31 23.59.59 screenshot.jpg",
                                           "2026-01-01 00.00.00 screenshot.gif", "2026-01-01 00.00.00 screenshot.webp"])
        self.assertEqual(self.r["given"], ["", "plan.png", ""])

    def test_the_box_uploads_and_shows_a_chip(self):
        up, done = self.r["whileUp"], self.r["afterUp"]
        self.assertTrue(up["busy"])
        self.assertEqual(up["stored"], [])
        self.assertIn("Uploading…", up["html"])
        self.assertIn("data-att-x=", up["html"])
        self.assertFalse(done["busy"])
        self.assertEqual(done["stored"], [{"room": "room-1", "name": "shot.png"}])
        self.assertIn('src="/u/shot.png"', done["html"])
        url, headers, _name = self.r["firstFetch"]
        self.assertEqual(url, "/api/room/attachment?room=room-1")
        self.assertEqual(headers["Content-Type"], "image/png")

    def test_a_refused_or_too_large_image_is_marked_and_can_be_removed(self):
        f = self.r["failed"]
        self.assertEqual(f["failed"], ["Only a PNG, JPEG, GIF or WebP image can be attached."])
        self.assertEqual(f["stored"], 1)
        self.assertIn("att-chip err", f["html"])
        self.assertIn("Not uploaded", f["html"])
        self.assertEqual(self.r["huge"]["requests"], 0)
        self.assertIn("at most 20 MB", self.r["huge"]["err"])
        self.assertEqual(self.r["afterRemove"], {"n": 1, "failed": 0})
        self.assertEqual(self.r["cleared"], 0)
        self.assertGreaterEqual(self.r["changes"], 6)
        self.assertEqual(len(self.r["noRoom"]), 1)

    def test_stored_images_come_back_as_chips(self):
        k = self.r["kept"]
        self.assertEqual(k["stored"], [{"room": "room-9", "name": "a.png"}])
        self.assertIn("/api/room/attachment?room=room-9&amp;name=a.png", k["html"])

    def test_image_lines_are_split_off_the_end_only(self):
        full, middle, plain, only = self.r["split"]
        self.assertEqual(full, {"words": "hi", "paths": ["C:\\a b\\x.png", "/t/y.png"]})
        self.assertEqual(middle["paths"], [])
        self.assertEqual(plain, {"words": "plain", "paths": []})
        self.assertEqual(only, {"words": "", "paths": ["only.png"]})
        self.assertEqual(self.r["own"], [True, True, False, False, False])
        self.assertIn('href="/api/room/attachment?room=room-1&amp;name=a%20%22b%22.png"', self.r["thumbs"])
        self.assertNotIn('"b".png"', self.r["thumbs"])

    def test_a_diff_comment_keeps_its_images(self):
        m = self.r["message"]
        self.assertIn("look\n\n(attached: shot.png, b.png)", m)
        self.assertIn("```\n\n(attached: only.png)", m)
        self.assertEqual(self.r["valid"], [True, True, False, False])
        self.assertEqual(self.r["rev"], [True, True])

    def test_a_pasted_file_whose_name_is_taken_is_numbered(self):
        self.assertEqual(self.r["keepBoth"], ["queued", "shots/2026-09-16 10.00.00 screenshot-2.png",
                                              "queued", "shots/2026-09-16 10.00.00 screenshot-3.png"])

    def test_the_pages_load_the_shared_script(self):
        for page in (INDEX, SESSION):
            self.assertIn('<script src="/static/attach.js"></script>', page)
        self.assertIn("attachments: images", INDEX)
        self.assertIn("keepBoth: true", INDEX)


if __name__ == "__main__":
    unittest.main()
