"""Every task named in a chat links to it, and the hub names tasks by number (#202, P164, P166).

The pages (static/taskcard.js, in Node):

* a room id in prose or as a whole code span is read as that task
  (refsIn { rooms }); one in a path, a URL's query or a file name is not;
* "task 70" written as a word is the reference 70 (refsIn { words });
* "#140/#142" is a list of two tasks; "Documents/#30" is a path; a number
  never starts with 0 ("S-03" is a rule id);
* a code span that is exactly a reference (`#201`, `ED-201`, `room-...`) is
  a chip (codeRef); a colour (`#111`, `#000000`) or a longer span stays code;
* the italic title after "task #200" goes with the chip (replaceRefs,
  chipHtml { title }): a link to the same task with the same card;
* a deleted task's room id (goneHtml) has no link, a card saying "(deleted)".

task_numbers.py reads numbers the same way (leading zero, slash).

The hub: every header it writes about a task (a report, a review verdict, a
rotation update, a stalled or silent owner, a message not taken in, the
reviewer's brief, the digest's facts, the board brief) names the task as #N:
no room id, and no title on a line without its number (P166).

The chips in a browser: tests/test_task_chip_page.py. Skipped without Node.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import board_brief  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import digest  # noqa: E402
import ensemble_tools  # noqa: E402
import rotation  # noqa: E402
import sends  # noqa: E402
import stall  # noqa: E402
import task_numbers as tn  # noqa: E402
import test_task_numbers as tnt  # noqa: E402

TASKCARD = (ROOT / "static" / "taskcard.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")
ROOM_ID = re.compile(r"room-[0-9a-f]{8}")

PAGE_JS = r"""
const toks = (s, o) => TaskCard.refsIn(s, null, o).map(r => r.room || ((r.key ? r.key + '-' : '#') + r.no));
const code = s => { const r = TaskCard.codeRef(s, null); return r ? (r.room || (r.key ? r.key + '-' : '#') + r.no) : null; };
const mark = r => { if (r.title) r.title.taken = true; return '[' + (r.room || r.no) + ']'; };
const t = { roomId: 'room-1a2b3c4d', no: 200, label: '#200', ref: 'ED-200', title: 'Fast list', status: 'running', agents: [] };
console.log(JSON.stringify({
  prose: toks('room-1a2b3c4d is done, see (room-0badf00d).', { rooms: true }),
  notRooms: toks('/x/room-1a2b3c4d/y, /session?id=room-1a2b3c4d, room-1a2b3c4d.json, room-1a2b3c4dz', { rooms: true }),
  roomsOff: toks('room-1a2b3c4d', {}),
  words: toks('Task 70 and task 71, task 7.5, TASK 9, mytask 3, task 01', { words: true }),
  wordsOff: toks('task 70', {}),
  mdTarget: toks('[open](room-1a2b3c4d) and [room-0badf00d](/session?id=room-0badf00d)', { rooms: true }),
  slash: toks('#140/#142 and Documents/#30 and a/#31', {}),
  slashPath: toks('Documents/#140/#142 and x/#5/#6/#7', {}),
  slashChain: toks('#140/#142/#143', {}),
  slashLongPath: toks('Documents/' + '#123456/'.repeat(12) + '#123456', {}),
  slashLongList: toks(Array.from({ length: 25 }, (_, i) => '#' + (i + 1)).join('/'), {}).length,
  mdNested: toks('[open](https://example.test/(foo)room-1a2b3c4d) and literal ](room-0badf00d)', { rooms: true }),
  zero: toks('S-03 and #07 and ED-0 and ED-10', {}),
  code: ['#201', 'ED-201', 'room-1a2b3c4d', ' #9 ', '#111', '#000000', '#123456', 'git log #201', 'S-03', '#0'].map(code),
  title: TaskCard.replaceRefs('**update** — report from task #200 *Fast list* (claude): done', null, mark),
  noTitle: TaskCard.replaceRefs('see #200 *not a title*', null, mark),
  word: TaskCard.replaceRefs('as task 70 said', null, mark),
  chip: TaskCard.chipHtml(t, { title: { text: 'Fast <list>' } }),
  gone: TaskCard.goneHtml({ token: 'room-0badf00d', room: 'room-0badf00d' }),
}));
"""


def node(code: str) -> dict:
    res = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node is not installed")
class PageReads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = node(TASKCARD + "\n" + PAGE_JS)

    def test_room_ids(self):
        self.assertEqual(self.r["prose"], ["room-1a2b3c4d", "room-0badf00d"])
        self.assertEqual(self.r["notRooms"], [], "a path, a URL's query, a file name, a longer word")
        self.assertEqual(self.r["roomsOff"], [], "the hub's reading (no options) is as before")
        self.assertEqual(self.r["mdTarget"], ["room-0badf00d"], "a Markdown link's target is the link's, its words are read")
        self.assertEqual(self.r["mdNested"], ["room-0badf00d"],
                         "a target with parentheses is the link's too; '](' with no link is prose")

    def test_task_as_a_word(self):
        self.assertEqual(self.r["words"], ["#70", "#71", "#9"], "not 7.5, not inside a word, not 01")
        self.assertEqual(self.r["wordsOff"], [])
        self.assertEqual(self.r["word"], "as task [70] said", "the number is the chip, 'task' stays text")

    def test_lists_paths_and_leading_zeros(self):
        self.assertEqual(self.r["slash"], ["#140", "#142"], "Documents/#30 and a/#31 are paths")
        self.assertEqual(self.r["slashPath"], [], "a list after a path is still the path")
        self.assertEqual(self.r["slashChain"], ["#140", "#142", "#143"])
        self.assertEqual(self.r["slashLongPath"], [], "a long list after a path is still the path")
        self.assertEqual(self.r["slashLongList"], 25, "a long list is read whole")
        self.assertEqual(self.r["zero"], ["ED-10"])

    def test_code_spans(self):
        self.assertEqual(self.r["code"], ["#201", "ED-201", "room-1a2b3c4d", "#9", None, None, None, None, None, None])

    def test_the_title_after_the_number(self):
        self.assertEqual(self.r["title"], "**update** — report from task [200] (claude): done", "the chip takes the title")
        self.assertEqual(self.r["noTitle"], "see [200] *not a title*", "only after the word task")
        chip = self.r["chip"]
        first, title = re.findall(r'<a class="([^"]+)" href="([^"]+)"', chip)
        self.assertEqual(first, ("task-chip tc-run", "/session?id=room-1a2b3c4d"))
        self.assertEqual(title, ("task-title-ref", "/session?id=room-1a2b3c4d"))
        cards = re.findall(r'data-card="([^"]+)"', chip)
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0], cards[1], "the same card")
        self.assertIn("<em>Fast &lt;list&gt;</em></a>", chip)

    def test_a_deleted_task(self):
        gone = self.r["gone"]
        self.assertIn('class="task-chip tc-gone"', gone)
        self.assertNotIn("href=", gone, "nothing to follow")
        self.assertIn('role="button" tabindex="0"', gone)
        self.assertIn("(deleted)", gone)
        self.assertIn('<span class="ref-who">room-0badf00d</span>', gone, "the words as written")


SUMMARY_JS = r"""
const taskRefCtx = () => null, taskRefHome = () => '';
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
const roomRefHtml = r => r.room === 'room-1a2b3c4d' ? '[chip ' + r.room + ']' : null;
const taskLinkHtml = (id, h) => '<a class="task-link" data-task="' + id + '">' + h + '</a>';
const taskRefLinkHtml = (who, key, no) => '[#' + no + ']';
console.log(JSON.stringify(taskChipsIn('See #5, `#6`, `git log #7`, room-1a2b3c4d, `room-0badf00d` <b>', '')));
"""


@unittest.skipUnless(NODE, "node is not installed")
class NeedsYouSummary(unittest.TestCase):
    """index.html's taskChipsIn (the Needs-you and ask summaries) reads code
    spans and room ids as the chat does."""

    def test_code_spans_and_pending_rooms(self):
        import test_task_refs as ttr
        out = node(TASKCARD + "\n" + ttr.fn(ttr.INDEX, "taskChipsIn") + SUMMARY_JS)
        self.assertEqual(out, 'See [#5], [#6], `git log #7`, [chip room-1a2b3c4d], '
                              '<a class="task-link" data-task="room-0badf00d">room-0badf00d</a> &lt;b>',
                         "a whole-reference code span is the chip, a longer one stays code, a room the hub "
                         "has not answered for is the link roomRefsPatch turns into its chip")


class HubReads(unittest.TestCase):
    def test_the_same_numbers_as_the_page(self):
        refs = lambda s: [(r.get("key") or "#") + str(r["no"]) for r in tn.all_text_refs(s)]
        self.assertEqual(refs("#140/#142 and Documents/#30 and a/#31"), ["#140", "#142"])
        self.assertEqual(refs("Documents/#140/#142 and x/#5/#6/#7"), [])
        self.assertEqual(refs("#140/#142/#143"), ["#140", "#142", "#143"])
        self.assertEqual(refs("Documents/" + "#123456/" * 12 + "#123456"), [])
        self.assertEqual(len(refs("/".join(f"#{i}" for i in range(1, 26)))), 25)
        self.assertEqual(refs("S-03 and #07"), [])
        self.assertEqual(refs("task 70 and room-1a2b3c4d"), [], "words and room ids are the pages' only")


class HubWrites(tnt.Hub):
    """What the hub writes about a task names it by number, never by room id."""

    def setUp(self):
        super().setUp()
        self.po = self.room("PO", self.ed)
        dashboard.set_project_po(self.ed, self.po)
        self.a = self.room("Alpha", self.ed, numbered=True)
        self.b = self.room("Beta", self.ed, numbered=True)
        self.posted = []
        real = chatroom.post_report

        def post(room_id, sender, to, text, extra=None, wake=True):
            self.posted.append(text)
            return real(room_id, sender, to, text, extra, wake=wake)
        for p in (mock.patch.object(chatroom, "post_report", post),
                  mock.patch.object(chatroom, "is_on_mention", return_value=True),
                  mock.patch.object(dashboard, "_room_is_live", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.handler = SimpleNamespace(_resume_room=lambda r: None, _ring_recipients=lambda *a: [],
                                       _ring_report=lambda *a: [])

    def po_texts(self) -> list[str]:
        room = chatroom.get_room(self.po, public=False)
        return [m.get("text", "") for m in room.get("messages", [])]

    def no_room_id(self, text: str):
        """No room id in the text, and no task's title on a line without its
        number (P166: a task is named as #N, its title after it at most)."""
        self.assertIsNone(ROOM_ID.search(text), text)
        for title, no in (("Alpha", "#1"), ("Beta", "#2")):
            for line in text.splitlines():
                if title in line:
                    self.assertRegex(line, rf"{no}(?!\d)", f"{title} named without {no}: {line}")

    def test_a_report(self):
        for kind in ("update", "completed", "blocked", "question"):
            with self.subTest(kind=kind):
                text, err = ensemble_tools.call("ensemble_report", {"kind": kind, "text": "Done here."},
                                                self.b, "claude", self.handler)
                self.assertFalse(err, text)
                last = self.po_texts()[-1]
                self.assertIn("report from task #2 *Beta* (claude)", last)
                self.no_room_id(last)

    def test_a_review_verdict_and_the_reviewers_brief(self):
        full = chatroom.get_room(self.b, public=False)
        chatroom.participant(full, "codex")["review"] = {"n": 1, "askedBy": "claude", "branch": "sess/b",
                                                         "head": "abc1234", "startedAt": 1.0}
        chatroom.update_room(full)
        full = chatroom.get_room(self.b, public=False)
        ctx = {"room": full, "identity": "codex", "part": chatroom.participant(full, "codex"), "projectId": self.ed}
        ensemble_tools._review_done(ctx, {"verdict": "approve", "summary": "fine", "findings": "- ok"}, self.handler)
        last = self.po_texts()[-1]
        self.assertIn("of task #2 *Beta* (by codex", last)
        self.no_room_id(last)
        with mock.patch.object(dashboard, "review_log_path", lambda r: "REVIEW-LOG.md"):
            brief = dashboard.review_brief(full, chatroom.participant(full, "codex"),
                                           {"id": "m", "from": "claude", "text": "review it"}, 1, {}, "")
        self.assertIn("the reviewer on task #2 *Beta*.", brief)
        self.assertIn(dashboard.TASK_NAME_NOTE, brief)
        self.no_room_id(brief)

    def test_a_rotation_update(self):
        room = chatroom.get_room(self.b, public=False)
        rec = {"tokens": 210000, "threshold": 200000, "fromSessionId": "sid-old"}
        self.assertTrue(rotation._report_to_po(room, "claude", rec, "after its turn"))
        self.assertIn("report from task #2 *Beta* (claude)", self.posted[-1])
        self.no_room_id(self.posted[-1])
        prompt = rotation.task_first_prompt(room, "sid-old", 210000, Path("TASK-HANDOVER.md"), True)
        self.assertIn("taking over task #2 *Beta* from", prompt)
        self.assertIn("taskId=#2 ", prompt)
        self.no_room_id(prompt)

    def test_a_stalled_owner(self):
        room = chatroom.get_room(self.b, public=False)
        now = time.time()
        self.assertTrue(stall._post_po(room, {"idleSince": now - 900, "nudgedAt": now - 300}, now))
        self.assertTrue(self.posted[-1].startswith("**#2 stalled since"), self.posted[-1])
        self.assertIn("`ensemble_get_task #2`", self.posted[-1])
        self.no_room_id(self.posted[-1])
        self.assertIn("**#2 stalled since", self.posted[-1])
        self.assertIn("** — Beta\n", self.posted[-1], "the title after the bold number")
        loose = chatroom.get_room(self.room("No number", self.ed), public=False)
        self.assertEqual(dashboard.task_word(loose), "*No number*", "a task without a number by its title")
        self.assertEqual(dashboard.task_arg(loose), loose["id"], "its room id only as a tool argument")
        self.assertTrue(stall._post_po(loose, {"idleSince": now - 900, "nudgedAt": now - 300}, now))
        head = self.posted[-1].split("\n")[0]
        self.assertTrue(head.startswith("**No number stalled since"), head)
        self.assertTrue(head.endswith("**"), "no italics inside the bold, no title after it again")

    def test_a_task_with_no_title(self):
        bare = chatroom.get_room(self.room("", self.ed, numbered=True), public=False)
        bare["title"] = ""
        self.assertEqual(dashboard.task_named(bare), "task #3", "no empty *…*")
        loose = chatroom.get_room(self.room("x", self.ed), public=False)
        loose["title"] = ""
        self.assertEqual(dashboard.task_named(loose), "a task", "never its room id")
        text = digest.plain_facts({"id": self.ed, "name": "ED"}, [],
                                  [{"id": loose["id"], "label": "", "title": "", "what": ["no longer in this project"],
                                    "finished": False}], 0)
        self.assertIn("- a task with no number or title: no longer in this project", text)
        self.no_room_id(text)

    def test_a_message_not_taken_in(self):
        sends._tell(chatroom.get_room(self.b, public=False), [{"text": "Please merge."}])
        last = self.po_texts()[-1]
        self.assertIn("A message the person sent to task #2 *Beta* was not taken in", last)
        self.no_room_id(last)

    def test_the_digest_and_the_board_brief(self):
        facts = [digest._task_facts(chatroom.get_room(r), {}, {}, time.time()) for r in (self.a, self.b)]
        text = digest.plain_facts({"id": self.ed, "name": "ED"}, facts,
                                  [{"id": self.b, "label": "#2", "title": "Beta", "what": ["new commits"],
                                    "finished": False}], 0)
        self.assertIn("- #2 Beta: new commits", text)
        self.no_room_id(text)
        brief = board_brief.build(dashboard, dashboard.find_project(self.ed), now=time.time())
        self.assertIn("#1 Alpha", brief)
        self.assertIn("#2 Beta", brief)
        self.no_room_id(brief)

    def test_names(self):
        room = chatroom.get_room(self.b)
        self.assertEqual(dashboard.task_named(room), "task #2 *Beta*")
        self.assertEqual(dashboard.task_named(room, "Other"), "task #2 *Other*")
        self.assertEqual((dashboard.task_word(room), dashboard.task_arg(room)), ("#2", "#2"))


class Prompts(unittest.TestCase):
    def test_agents_are_told_to_name_tasks_by_number(self):
        note = dashboard.TASK_NAME_NOTE
        self.assertIn("#18", note)
        self.assertIn("ED-18", note)
        self.assertIn("room id", note)
        skill = (ROOT / "skills" / "ensemble" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Name tasks by number", skill)


if __name__ == "__main__":
    unittest.main()
