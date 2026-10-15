"""A question card shows a link as a chip, never its bare URL (#238).

session.html's "Card text" block (cardTextHtml), the shared hubUrlWords and
askCardHtml run in Node, with static/taskcard.js and static/pointrefs.js,
against a stand-in hub:

* a link to a message or card written with any host (localhost, 127.0.0.1,
  a tailnet name, https) is the balloon's chip in the question, an option,
  its note and the comment; until the hub has the message, a link named for
  what it opens; never the URL as text;
* a link (bare or [words](url)) right after the point chip naming the same
  place is dropped, with its "(card: …)"; so is a link back to the card
  itself; a link to another card or point of the same message stays;
* an option's answer button holds no link: its label's links follow it;
* a file view link is its file's link; an external URL stays a link as
  written; text that only looks like a URL stays text;
* a row that is itself a link (Needs you, Open questions) says the link in
  words (hubUrlWords).

The card in a browser: tests/test_card_text_page.py. Skipped without Node.
"""
from __future__ import annotations

import re
import shutil
import unittest
from pathlib import Path

from tests.test_task_refs import ATTACH, SESSION, TASKCARD, block, const, fn, node

ROOT = Path(__file__).resolve().parent.parent
POINTREFS = (ROOT / "static" / "pointrefs.js").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")

JS = r"""
globalThis.location = new URL('http://127.0.0.1:8765/session?room=room-0000c0de');
const ROOM = 'room-0000c0de';
const esc = s => (s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fileHref = (p, line) => '/fileview?path=' + encodeURIComponent(p) + (line ? '&line=' + line : '');
// The balloon's chip, for the messages the hub knows.
const KNOWN = new Set(['msg0aaa0001', 'his00000215', 'card0000215']);
const refChipHtml = ref => KNOWN.has(ref.msg)
  ? `<a class="ref-chip${ref.part.startsWith('ask:') ? ' card-jump' : ''}" href="/session?room=${ref.room}&amp;msg=${ref.msg}" data-ref-room="${ref.room}" data-ref-msg="${ref.msg}" data-ref-part="${ref.part}"><span class="ref-arrow">↗</span><span class="ref-who">chip ${ref.msg}</span></a>`
  : null;
const refChanged = () => {};
const PROJECTS_CTX = { own: 'proj-ed', nouns: ['project', 'projects'], projects: [{ id: 'proj-ed', key: 'ED', name: 'Ensemble Dashboard', aliases: ['Ensemble'] }] };
const POINT = { id: 'P215', text: 'should the test set-up let you choose', stage: 'open', roomId: 'room-0000c0de', mid: 'his00000215',
  project: 'Ensemble Dashboard', projectId: 'proj-ed', createdAt: 1, card: { mid: 'card0000215', n: 0, open: true, question: 'Which set-up?' } };
function fetch(url) {
  if (url.startsWith('/api/task/projects')) return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(PROJECTS_CTX) });
  if (url.startsWith('/api/point/ref') && url.includes('ref=P215')) return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(POINT) });
  return Promise.resolve({ status: 404, ok: false, json: () => Promise.resolve({}) });
}
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };
const MOD_ENTER = 'Ctrl+Enter';
const askUndoLeft = () => 0;
const chatStamp = () => '';
%s
const T = {
  localhost: 'Which one? http://localhost:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001&part=ask:0',
  loopback: 'Which one? (see http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001)',
  tailnet: 'Which one? http://hubbox:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001.',
  https: 'Which one? https://hub.example.net/session?room=room-1a2b3c4d&msg=msg0aaa0001&part=ask:1',
  pending: 'Which one? http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=unknown00001&part=ask:0',
  pointMsg: 'Read http://127.0.0.1:8765/session?room=room-0000c0de&msg=his00000215&part=pt:P215 first',
  afterPoint: 'should the TEST set-up let you choose the profile (P215)? http://127.0.0.1:8765/session?room=room-0000c0de&msg=his00000215&part=pt:P215',
  afterPointCard: 'Which set-up (P215)? (card: https://hub.example.net/session?room=room-0000c0de&msg=card0000215&part=ask:0)',
  self: 'What do you want to do with the old data? (card: http://127.0.0.1:8765/session?room=room-0000c0de&msg=selfcard0001&part=ask:0)',
  selfBare: 'Which one? http://127.0.0.1:8765/session?room=room-0000c0de&msg=selfcard0001&part=ask:0',
  fileview: 'Build the hedge in the preview http://127.0.0.1:8765/fileview?path=C%%3A%%5CDocs%%5Chedge.html?',
  mdLink: 'Build it as in the [preview](http://127.0.0.1:8765/fileview?path=C%%3A%%5CDocs%%5Chedge.html)?',
  task: 'Pick from http://127.0.0.1:8765/session?room=room-1a2b3c4d',
  external: 'See https://github.com/fab-ioc/ensemble/issues/20, then answer.',
  looksLike: 'Is hub.example.net/session?room=x a link, or http//nope, or www dot example?',
  html: 'A <b>bold</b> & "quoted" question?',
  twoAsks: 'Which: [the first](http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001&part=ask:0) http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001&part=ask:1',
  otherPoint: 'Which set-up (P215)? http://127.0.0.1:8765/session?room=room-0000c0de&msg=his00000215&part=pt:P216',
  mdAfterPoint: 'Which set-up (P215)? [card](https://hub.example.net/session?room=room-0000c0de&msg=his00000215&part=pt:P215)',
  selfOther: 'And this one? http://127.0.0.1:8765/session?room=room-0000c0de&msg=selfcard0001&part=ask:1',
  otherRoom: 'Read http://localhost:8765/session?room=room-1a2b3c4d&msg=selfcard0001&part=ask:0',
};
(async () => {
  // The project list, then the point: each asked on a draw, drawn again once the hub answers.
  for (let i = 0; i < 2; i++) { for (const t of Object.values(T)) cardTextHtml(t, 'selfcard0001', 0); await settle(); }
  const out = {};
  for (const [k, t] of Object.entries(T)) out[k] = cardTextHtml(t, 'selfcard0001', 0);
  const m = { id: 'selfcard0001', from: 'claude', text: '' };
  const a = { n: 0, question: T.afterPoint, kind: 'decision', recommended: 0, options: [
    { label: 'Use the old one', detail: 'as in http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001', recommended: true },
    { label: 'See https://hub.example.net/session?room=room-1a2b3c4d&msg=msg0aaa0001', detail: '', recommended: false }] };
  out.card = askCardHtml(m, a, 1, {});
  out.answered = askCardHtml(m, a, 1, { asks: { selfcard0001: { 0: { option: 'Use the old one', comment: 'like https://hub.example.net/session?room=room-1a2b3c4d&msg=msg0aaa0001', at: 1 } } } });
  out.words = {
    self: hubUrlWords(T.self, 'selfcard0001'),
    selfBare: hubUrlWords(T.selfBare, 'selfcard0001'),
    selfAsk0: hubUrlWords(T.selfBare, 'selfcard0001', 0),
    selfOther: hubUrlWords(T.selfOther, 'selfcard0001', 0),
    otherRoom: hubUrlWords(T.otherRoom, 'selfcard0001', 0),
    otherRoomOf: hubUrlWords(T.selfBare, { room: 'room-1a2b3c4d', msg: 'selfcard0001' }, 0),
    button: [
      'Keep the default, see http://localhost:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001',
      'Keep it (card: https://hub.example.net/session?room=room-0000c0de&msg=selfcard0001&part=ask:0)',
      'Or this (card: https://hub.example.net/session?room=room-1a2b3c4d&msg=msg0aaa0001&part=ask:0)',
      'As in [the notes](http://127.0.0.1:8765/session?room=room-1a2b3c4d&msg=msg0aaa0001)',
      'See https://hub.example.net/session?room=room-1a2b3c4d&msg=msg0aaa0001',
      'Wait and see',
      'Open hedge.html: http://127.0.0.1:8765/fileview?path=C%%3A%%5CDocs%%5Chedge.html',
      'Use (P215)',
      'See https://example.org/docs',
      'P215',
    ].map(t => { const x = cardText(t, 'selfcard0001', 0); return [x.words, x.links.length]; }),
    other: hubUrlWords(T.localhost, 'selfcard0001'),
    file: hubUrlWords(T.fileview, ''),
    external: hubUrlWords(T.external, ''),
  };
  console.log(JSON.stringify(out));
})();
"""


def text_of(h: str) -> str:
    """What the card shows: its HTML with the tags taken out."""
    return re.sub(r"<[^>]+>", "", h)


@unittest.skipUnless(NODE, "node is not installed")
class CardLinks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = "\n".join([
            TASKCARD, ATTACH, POINTREFS,
            block(SESSION, "// ---- Links in rendered text: begin shared block", "// ---- Links in rendered text: end shared block"),
            const(SESSION, "REF_URL_RE"), const(SESSION, "REF_A"), const(SESSION, "REF_MARK_RE"),
            fn(SESSION, "refOfUrl"),
            block(SESSION, "// ---- Task references: begin", "// ---- Task references: end"),
            const(SESSION, "POINT_REFS"),
            block(SESSION, "// ---- PO message names: begin", "// ---- PO message names: end"),
            block(SESSION, "// ---- Card text: begin", "// ---- Card text: end"),
            const(SESSION, "ASK_LOCAL"), fn(SESSION, "askAnswer"), fn(SESSION, "askCardHtml")])
        cls.r = node(JS % src)

    def assertNoUrl(self, h):
        self.assertNotIn("http", text_of(h), h)

    def test_a_message_link_on_any_host_is_the_chip(self):
        for k in ("localhost", "loopback", "tailnet", "https"):
            h = self.r[k]
            self.assertEqual(h.count('class="ref-chip'), 1, (k, h))
            self.assertIn('data-ref-room="room-1a2b3c4d" data-ref-msg="msg0aaa0001"', h)
            self.assertNoUrl(h)
            self.assertTrue(text_of(h).startswith("Which one? "), h)
        self.assertIn("ref-chip card-jump", self.r["localhost"], "a card's link lands on the card")
        self.assertTrue(self.r["tailnet"].endswith("</a>."), "the full stop is the sentence's")
        self.assertTrue(text_of(self.r["loopback"]).endswith("chip msg0aaa0001)"), self.r["loopback"])

    def test_until_the_hub_has_it_a_link_in_words(self):
        h = self.r["pending"]
        self.assertIn('class="msg-link card-jump"', h)
        self.assertIn(">the question card</a>", h)
        self.assertNoUrl(h)

    def test_a_link_after_the_point_chip_naming_the_same_place_is_dropped(self):
        for k in ("afterPoint", "afterPointCard"):
            h = self.r[k]
            self.assertIn("point-chip", h, h)
            self.assertNotIn("ref-chip", h, (k, h))
            self.assertNotIn("card:", h)
            self.assertNoUrl(h)
        self.assertTrue(text_of(self.r["afterPoint"]).endswith("(P215)?"), self.r["afterPoint"])
        self.assertTrue(text_of(self.r["afterPointCard"]).endswith("(P215)?"), self.r["afterPointCard"])

    def test_a_point_link_alone_is_its_chip(self):
        h = self.r["pointMsg"]
        self.assertIn('data-ref-msg="his00000215" data-ref-part="pt:P215"', h)
        self.assertNoUrl(h)
        self.assertTrue(text_of(h).startswith("Read ") and text_of(h).endswith(" first"), h)

    def test_a_link_back_to_this_card_goes(self):
        self.assertEqual(self.r["self"], "What do you want to do with the old data?")
        self.assertEqual(self.r["selfBare"], "Which one?")
        h = self.r["selfOther"]
        self.assertIn('data-ref-msg="selfcard0001"', h, "another card of the same message stays")
        self.assertIn(">the question card</a>", h)

    def test_a_message_of_the_same_id_in_another_room_is_not_this_card(self):
        h = self.r["otherRoom"]
        self.assertIn('data-ref-room="room-1a2b3c4d" data-ref-msg="selfcard0001"', h)
        self.assertNoUrl(h)
        self.assertEqual(self.r["words"]["otherRoom"], "Read the question card")
        self.assertEqual(self.r["words"]["otherRoomOf"], "Which one? the question card")

    def test_a_link_to_another_card_or_point_of_the_same_message_stays(self):
        h = self.r["twoAsks"]
        self.assertIn('data-ref-part="ask:0"', h)
        self.assertIn('data-ref-part="ask:1"', h)
        self.assertIn(">the first</a>", h)
        self.assertNoUrl(h)
        h = self.r["otherPoint"]
        self.assertIn("point-chip", h)
        self.assertIn('data-ref-msg="his00000215" data-ref-part="pt:P216"', h)
        self.assertNoUrl(h)

    def test_a_markdown_link_repeating_the_point_chip_is_dropped(self):
        h = self.r["mdAfterPoint"]
        self.assertIn("point-chip", h)
        self.assertNotIn("ref-chip", h)
        self.assertNotIn(">card</a>", h)
        self.assertTrue(text_of(h).endswith("(P215)?"), h)

    def test_a_file_view_and_a_task_are_links_in_words(self):
        h = self.r["fileview"]
        self.assertIn('class="file-link"', h)
        self.assertIn(">hedge.html</a>?", h)
        self.assertNoUrl(h)
        h = self.r["mdLink"]
        self.assertIn(">preview</a>?", h)
        self.assertNotIn("[", text_of(h))
        self.assertNoUrl(h)
        h = self.r["task"]
        self.assertIn('data-task="room-1a2b3c4d"', h)
        self.assertNoUrl(h)

    def test_an_external_url_stays_a_link_as_written(self):
        h = self.r["external"]
        self.assertIn('<a href="https://github.com/fab-ioc/ensemble/issues/20" target="_blank" rel="noopener">https://github.com/fab-ioc/ensemble/issues/20</a>, then answer.', h)

    def test_what_only_looks_like_a_url_stays_text(self):
        self.assertEqual(self.r["looksLike"], "Is hub.example.net/session?room=x a link, or http//nope, or www dot example?")
        self.assertEqual(self.r["html"], "A &lt;b&gt;bold&lt;/b&gt; &amp; &quot;quoted&quot; question?")

    def test_the_card_question_options_and_comment(self):
        h = self.r["card"]
        q = re.search(r'<span class="qa-qt">(.*?)</span><button', h).group(1)
        self.assertIn("point-chip", q)
        self.assertNoUrl(q)
        self.assertEqual(h.count('class="ref-chip'), 2, "the note's link and the label's link")
        self.assertNotRegex(h, r'<button(?:(?!</button>).)*<a[ >]', "no link inside a button")
        self.assertRegex(h, r'</button><span class="qa-oa"><a class="ref-chip', "the label's link follows its button")
        self.assertIn('<span class="qa-ol">See</span></button>', h)
        for part in re.findall(r'<span class="qa-o[lda]">(.*?)</span>(?:<span class="lz|</button>|</div>|<span class="qa-od)', h):
            self.assertNoUrl(part)
        self.assertNotIn("http", re.sub(r'(href|title|aria-label)="[^"]*"', "", text_of(h).replace("chip ", "")))
        self.assertNotRegex(h, r'(title|aria-label)="[^"]*http', "tooltips say the link in words too")
        done = self.r["answered"]
        dc = re.search(r'<span class="qa-dc">(.*?)</span></div></div>$', done).group(1)
        self.assertIn("ref-chip", dc)
        self.assertNoUrl(dc)

    def test_a_row_that_is_a_link_says_the_link_in_words(self):
        w = self.r["words"]
        self.assertEqual(w["self"], "What do you want to do with the old data?")
        self.assertEqual(w["selfBare"], "Which one?")
        self.assertEqual(w["selfAsk0"], "Which one?")
        self.assertEqual(w["selfOther"], "And this one? the question card")
        self.assertEqual(w["other"], "Which one? the question card")
        self.assertEqual(w["file"], "Build the hedge in the preview hedge.html?")
        self.assertEqual(w["external"], "See https://github.com/fab-ioc/ensemble/issues/20, then answer.")

    def test_an_answer_button_says_its_words_and_leaves_its_links_beside_it(self):
        self.assertEqual(self.r["words"]["button"], [
            ["Keep the default", 1], ["Keep it", 0], ["Or this", 1], ["As in", 1], ["See", 1], ["Wait and see", 0],
            ["Open hedge.html", 1], ["Use", 1], ["See", 1], ["", 1]])

    def test_every_place_a_card_shows_uses_them(self):
        self.assertIn('<span class="qa-qt">${cardTextHtml(a.question, m.id, a.n)}</span>', SESSION)
        self.assertIn('const label = cardText(o.label, m.id, a.n), words = cardWords(o.label, m.id, a.n);', SESSION)
        self.assertIn('<span class="qa-ol">${esc(label.words || \'Choose\')}</span>', SESSION)
        self.assertIn('<span class="qa-od">${cardTextHtml(o.detail, m.id, a.n)}</span>', SESSION)
        self.assertIn('<span class="qa-dc">${cardTextHtml(got.comment, m.id, a.n)}</span>', SESSION)
        self.assertIn("esc(hubUrlWords(c.question, c.mid))", SESSION, "Open questions")
        self.assertIn("hubUrlWords(foldLine(String(c.question || ''))", SESSION, "the chat's Needs you")
        self.assertIn("hubUrlWords(String(c.question || '').split('\\n')[0]", INDEX, "the PO screen's Needs you")


if __name__ == "__main__":
    unittest.main()
