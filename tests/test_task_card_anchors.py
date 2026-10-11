"""#201: TaskCard's API for cards on elements that are not chips, in Node.

* ``TaskCard.cardOf(t, o)`` is the data a chip carries in ``data-card`` (the
  chip's own is unchanged), with ``why`` when given;
* ``TaskCard.anchors(o)`` needs a document ``init`` set up, keeps one spec per
  selector, and ``TaskCard.refresh`` is a no-op with no card open;
* a long press on an anchor (touch) opens its card after 500 ms and the click
  it ends with does not reach the page; a short tap does; the card's title
  opens the task through the spec, not the page's link;
* the left bar's ``swTkCard`` builds a task number's card from the board: the
  number, the title as the list writes it, the column, the agents, why it
  needs you; a task past the rows from its ask; nothing for a task the board
  does not hold.

The DOM is a small stand-in: listeners by type, elements with ``closest`` and
``dataset``. Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
TASKCARD = (ROOT / "static" / "taskcard.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n}\n", i) + 3]


JS = r"""
%(taskcard)s
const log = {};
const t = { roomId: 'r7', no: 7, label: '#7', ref: 'MO-7', title: 'Brakes', status: 'idle', workflowName: 'In review',
            agents: [{ identity: 'claude' }, { identity: 'codex' }], project: 'Motors', inProject: true };
log.cardOf = TaskCard.cardOf(t, { href: '/?task=r7', why: 'blocked: logged out' });
const chip = TaskCard.chipHtml(t, { href: '/?task=r7' });
log.chipCard = JSON.parse(chip.match(/data-card="([^"]*)"/)[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
log.chipHasWhy = 'why' in log.chipCard;

// A stand-in document.
class El {
  constructor(tag, cls, parent) { this.tagName = tag; this.className = cls || ''; this.dataset = {}; this.attrs = {}; this.parent = parent || null; this.children = []; this.style = { setProperty() {} }; this.isConnected = true; this._html = ''; if (parent) parent.children.push(this); }
  get classList() { const el = this; return { add: (...c) => { el.className = [...new Set(el.className.split(' ').concat(c))].join(' ').trim(); }, remove: (...c) => { el.className = el.className.split(' ').filter(x => !c.includes(x)).join(' '); },
    toggle: (c, on) => { if (on) this.classList.add(c); else this.classList.remove(c); }, contains: c => el.className.split(' ').includes(c) }; }
  matches(sel) { return sel.split(',').some(s => { s = s.trim(); if (/^[a-z]+$/.test(s)) return this.tagName === s.toUpperCase(); const m = s.match(/\.([\w-]+)(?:\[data-room\])?$/); return m && this.className.split(' ').includes(m[1]) && (!/\[data-room\]/.test(s) || 'room' in this.dataset); }); }
  closest(sel) { for (let e = this; e; e = e.parent) if (e.matches && e.matches(sel)) return e; return null; }
  contains(x) { for (let e = x; e; e = e.parent) if (e === this) return true; return false; }
  setAttribute(k, v) { this.attrs[k] = String(v); } getAttribute(k) { return this.attrs[k] ?? null; } hasAttribute(k) { return k in this.attrs; }
  getBoundingClientRect() { return { left: 160, top: 200, right: 190, bottom: 220, width: 30, height: 20 }; }
  getClientRects() { return [1]; }
  get offsetWidth() { return 400; } get offsetHeight() { return 30; }
  set innerHTML(h) { this._html = h; this.children = []; const m = h.match(/<a class="tc-title"[^>]*>/); if (m) { const a = new El('A', 'tc-title', this); a.attrs.html = m[0]; } }
  get innerHTML() { return this._html; }
  remove() { this.isConnected = false; }
  focus() {}
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
}
const L = {};
const win = { innerWidth: 1728, innerHeight: 1117, matchMedia: () => ({ matches: false }), addEventListener() {}, location: { href: '' } };
const body = new El('BODY'), list = new El('DIV', 'sw-list', body);
const doc = { defaultView: win, head: new El('HEAD'), body, activeElement: null,
  createElement: tag => new El(tag.toUpperCase()), addEventListener: (t, f) => { (L[t] = L[t] || []).push(f); },
  querySelectorAll: sel => list.children.filter(e => e.matches(sel)) };
win.document = doc;
const fire = (type, target, more = {}) => { const ev = { type, target, button: 0, detail: 1, defaultPrevented: false, stopped: false,
  preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...more };
  for (const f of L[type] || []) { if (ev.stopped) break; f(ev); } return ev; };
try { TaskCard.anchors({ doc, selector: '.sw-tk[data-room]' }); log.noInit = 'no error'; } catch (e) { log.noInit = e.message; }
TaskCard.init({ doc });
TaskCard.refresh(doc);   // nothing open: nothing happens
const opened = [];
const spec = { doc, selector: '.sw-tk[data-room]', key: el => el.dataset.room, card: el => TaskCard.cardOf({ ...t, roomId: el.dataset.room }, { href: '/?task=' + el.dataset.room }),
               open: el => opened.push(el.dataset.room), beside: () => list };
TaskCard.anchors(spec);
TaskCard.anchors({ ...spec });     // the same selector again: replaced, not added
const tk = new El('BUTTON', 'sw-tk', list); tk.dataset.room = 'r7';
// A short tap: the click reaches the page.
fire('pointerdown', tk, { pointerType: 'touch', clientX: 170, clientY: 210, pointerId: 1 });
fire('pointerup', tk, { pointerType: 'touch' });
log.tap = { card: !!TaskCard.el(doc), click: !fire('click', tk).stopped };
// A long press: the card after 500 ms, and its click stopped.
fire('pointerdown', tk, { pointerType: 'touch', clientX: 170, clientY: 210, pointerId: 1 });
setTimeout(() => {
  log.at400 = !!TaskCard.el(doc);
  setTimeout(() => {
    const k = TaskCard.el(doc);
    log.long = { card: !!k, expanded: tk.getAttribute('aria-expanded') };
    fire('pointerup', tk, { pointerType: 'touch' });
    const c = fire('click', tk);
    log.longClick = { stopped: c.stopped, prevented: c.defaultPrevented, card: !!TaskCard.el(doc) };
    log.titleLink = k ? k.children[0].attrs.html : '';
    // Redrawn: the number replaced by a new one for the same task; the card stays on it.
    tk.isConnected = false; list.children = list.children.filter(e => e !== tk);
    const tk2 = new El('BUTTON', 'sw-tk', list); tk2.dataset.room = 'r7';
    TaskCard.refresh(doc);
    log.redrawn = { same: TaskCard.el(doc) === k, expanded: tk2.getAttribute('aria-expanded') };
    // Its title opens the task through the spec.
    const tc = fire('click', k.children[0]);
    log.title = { opened, stopped: tc.stopped, card: !!TaskCard.el(doc) };
    // Open again, then the task leaves the list: the card goes.
    TaskCard.open(tk2, doc);
    list.children = []; tk2.isConnected = false;
    TaskCard.refresh(doc);
    log.gone = !!TaskCard.el(doc);
    console.log(JSON.stringify(log));
  }, 200);
}, 400);
"""

SW = r"""
%(taskcard)s
%(index)s
const ATTN_LABEL = { blocked: 'blocked', waiting_for_you: 'waiting for you' };
const row = { sessionId: 'r7', roomId: 'r7', no: 7, label: 'Brakes', headless: true, workflow: 'inreview', isLive: true, status: 'busy',
              members: [{ identity: 'claude', agent: 'claude' }, { identity: 'codex', agent: 'codex' }] };
const el = room => ({ dataset: { room } });
const attn = new Map([['r7', { roomId: 'r7', state: 'blocked', reason: 'logged out' }],
                      ['r-old', { roomId: 'r-old', state: 'waiting_for_you', reason: 'asked', ref: 'MO-40', title: 'Past the rows' }]]);
console.log(JSON.stringify({ card: swTkCard(el('r7'), [row], attn), old: swTkCard(el('r-old'), [row], attn), none: swTkCard(el('r9'), [row], attn) }));
"""


def node(src: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.cjs"
        p.write_text(src, encoding="utf-8")
        out = subprocess.run([NODE, str(p)], capture_output=True, encoding="utf-8", timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr[-3000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is not installed")
class Anchors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = node(JS % {"taskcard": TASKCARD})
        index = "\n".join([
            re.search(r"^const esc = .*$", INDEX, re.M).group(0),
            INDEX[INDEX.index("const WORKFLOW_COLS = "):INDEX.index("const DONE_AGE_DAYS")],
            fn(INDEX, "function workflowOf("), fn(INDEX, "function rowTitle("), fn(INDEX, "function boardTask("),
            fn(INDEX, "function swTkCard(")])
        cls.sw = node(SW % {"taskcard": TASKCARD, "index": index})

    def test_card_of_is_what_a_chip_carries(self):
        c = self.r["cardOf"]
        self.assertEqual(c["ref"], "#7")
        self.assertEqual(c["state"], "In review · idle")
        self.assertEqual(c["agents"], ["claude", "codex"])
        self.assertEqual(c["why"], "blocked: logged out")
        self.assertEqual({k: v for k, v in c.items() if k != "why"}, self.r["chipCard"])
        self.assertFalse(self.r["chipHasWhy"])

    def test_anchors_need_init(self):
        self.assertIn("init", self.r["noInit"])

    def test_a_tap_is_the_pages_click(self):
        self.assertEqual(self.r["tap"], {"card": False, "click": True})

    def test_a_long_press_opens_the_card_and_swallows_its_click(self):
        self.assertFalse(self.r["at400"])
        self.assertEqual(self.r["long"], {"card": True, "expanded": "true"})
        self.assertEqual(self.r["longClick"], {"stopped": True, "prevented": True, "card": True})
        # The title is no page link (data-task): the spec opens the task.
        self.assertNotIn("data-task", self.r["titleLink"])
        self.assertIn('href="/?task=r7"', self.r["titleLink"])

    def test_refresh_keeps_the_card_on_the_redrawn_anchor(self):
        self.assertEqual(self.r["redrawn"], {"same": True, "expanded": "true"})
        self.assertFalse(self.r["gone"])

    def test_the_title_opens_through_the_spec(self):
        self.assertEqual(self.r["title"], {"opened": ["r7"], "stopped": True, "card": False})

    def test_sw_tk_card_from_the_board(self):
        c = self.sw["card"]
        self.assertEqual((c["ref"], c["title"], c["state"], c["agents"], c["project"]),
                         ("#7", "Brakes", "In review · running", ["claude", "codex"], ""))
        self.assertEqual(c["why"], "blocked: logged out")
        self.assertEqual(c["href"], "/?task=r7")
        o = self.sw["old"]
        self.assertEqual((o["ref"], o["title"], o["why"], o["href"]), ("MO-40", "Past the rows", "waiting for you: asked", "/session?room=r-old"))
        self.assertIsNone(self.sw["none"])


if __name__ == "__main__":
    unittest.main()
