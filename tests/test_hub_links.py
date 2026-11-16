"""An Ensemble address is never shown; a link is a clickable name (P358).

hub_links.names turns the hub's own addresses in plain text (the Inbox's
rows, a notification) into the names of what they open; another website's
address stays as written. The hub's prompts and the ensemble skill tell the
agents to write such links as [a name](the reference), never bare, and the
open-cards brief writes its cards that way.
"""
from __future__ import annotations

import ast
import re
import socket
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import asks  # noqa: E402
import chatroom  # noqa: E402
import dashboard  # noqa: E402
import hub_links  # noqa: E402
import inbox  # noqa: E402
import notify  # noqa: E402

ROOM = "room-0a1b2c3d"
TASK = "room-9f8e7d6c"
BASE = "http://127.0.0.1:8765"
CARD = f"{BASE}/session?room={ROOM}&msg=abc123def456&part=ask:1"
MSG = f"{BASE}/session?room={ROOM}&msg=abc123def456"
TASK_URL = f"http://localhost:8765/session?room={TASK}"
FILE = "http://127.0.0.1:8765/fileview?path=C%3A%5CProjects%5CIcons%5Clinks.png"
NIX_FILE = "http://127.0.0.1:8765/fileview?path=%2Fhome%2Fsam%2FDocuments%2Fplan.md&line=3"
QUESTIONS = ["Which feed?", "Project icons: A or B?"]
LONG_Q = "Should the replay keep every tick of the 189 DTE chain, or the one-minute bars the back-test already uses for its other runs?"
BARE = re.compile(r"127\.0\.0\.1|localhost:|fileview\?|session\?room=")


class _Hub:
    """The dashboard as hub_links reads it: a room's card questions and a task's number and title."""

    def __init__(self, questions=None, rooms=None):
        self.questions = questions or {}
        rooms = rooms or {}
        self.chatroom = types.SimpleNamespace(get_room=lambda rid, copy_room=True: rooms.get(rid))

    def resolve_message_ref(self, room, msg):
        qs = self.questions.get((room, msg))
        return None if qs is None else {"roomId": room, "id": msg, **({"questions": qs} if qs else {})}


def _hub(**kw):
    return mock.patch.object(hub_links, "_d", _Hub(**kw))


FULL = _Hub(questions={(ROOM, "abc123def456"): QUESTIONS, (ROOM, "long00000000"): [LONG_Q]},
            rooms={TASK: {"id": TASK, "no": 18, "title": "Billing export"}, ROOM: {"id": ROOM, "title": "PO"}})


# This machine is "box" on its tailnet (tailscale says box.tail1234.ts.net),
# and the hub has not been reached at any address yet.
TS = "box.tail1234.ts.net"


def _reset():
    hub_links._SEEN.clear()
    hub_links._TS_NAMES.clear()
    hub_links._TS_NAMES.add(TS)

_OWN = mock.patch.object(hub_links, "_own_names", lambda: frozenset({"box", "box.lan", socket.gethostname().lower()}))


def setUpModule():
    _OWN.start()
    _reset()


def tearDownModule():
    _OWN.stop()
    hub_links._SEEN.clear()
    hub_links._TS_NAMES.clear()


class Origins(unittest.TestCase):
    """Only this hub's own addresses are named (reviews 1 and 2): its port on
    this machine's loopback, names or tailnet name, and an address it is known
    to answer at: a page opened there, its bind, what tailscale serve gives it.
    A host a request merely claims is never one."""

    def tearDown(self):
        _reset()

    def test_the_same_short_name_in_another_tailnet_is_another_machine(self):
        # Review 3: box.other-tailnet.ts.net is not this box; neither a link to
        # it nor a proxy header naming it makes it the hub.
        for u in (f"http://box.other-tailnet.ts.net:8765/?task={TASK}", f"https://box.other-tailnet.ts.net/?task={TASK}",
                  f"https://box.other-tailnet.ts.net:9443/session?room={TASK}"):
            with self.subTest(url=u):
                self.assertIsNone(hub_links.target(u))
        self.assertFalse(hub_links.own_host("box.other-tailnet.ts.net"))
        hub_links.seen("box.other-tailnet.ts.net:9443", "https")
        hub_links.seen("box.other-tailnet.ts.net:8765", local=("127.0.0.1", 8765))
        self.assertEqual(hub_links.origins()["seen"], [])
        self.assertIsNone(hub_links.target(f"https://box.other-tailnet.ts.net:9443/session?room={TASK}"))
        # Without tailscale's word, this machine's own tailnet name is not guessed either.
        hub_links._TS_NAMES.clear()
        self.assertIsNone(hub_links.target(f"http://{TS}:8765/?task={TASK}"))

    def test_the_hubs_port_on_this_machine(self):
        for u in ("http://127.0.0.1:8765/", "http://localhost:8765/", "http://[::1]:8765/", "http://box:8765/",
                  "http://box.lan:8765/", "http://box.tail1234.ts.net:8765/"):
            with self.subTest(url=u):
                self.assertIsNotNone(hub_links.target(u))

    def test_a_resource_on_another_port_or_machine_is_another_website(self):
        paths = (f"/session?room={ROOM}&msg=abc123def456&part=ask:0", f"/session?room={TASK}",
                 f"/?task={TASK}", "/fileview?path=%2Fx%2Fplan.md", "/")
        hosts = ("http://localhost:5173", "http://127.0.0.1:3000", "http://[::1]:5173", "http://box:5173",
                 "http://localhost", "https://other.tail1234.ts.net", "http://other.tail1234.ts.net:8765",
                 "http://box.tail1234.ts.net:3000",
                 # another service on this machine's tailnet name, until tailscale says it is the hub (review 2)
                 "https://box.tail1234.ts.net", "https://box.tail1234.ts.net:5173",
                 # a tailnet or LAN address the hub is not known to listen on: another machine's
                 "http://100.101.102.103:8765", "http://192.168.1.5:8765")
        for h in hosts:
            for path in paths:
                with self.subTest(url=h + path):
                    self.assertIsNone(hub_links.target(h + path))
                    with mock.patch.object(hub_links, "_d", FULL):
                        self.assertEqual(hub_links.names(f"see {h}{path} now"), f"see {h}{path} now")

    def test_a_proxy_on_this_machine_the_hub_was_opened_through(self):
        u = f"http://127.0.0.1:9443/session?room={TASK}"
        self.assertIsNone(hub_links.target(u))
        hub_links.seen("127.0.0.1:9443")
        self.assertEqual(hub_links.target(u), {"task": TASK})
        hub_links.seen("box.tail1234.ts.net:8443", "https")
        self.assertEqual(hub_links.target(f"https://box.tail1234.ts.net:8443/?task={TASK}"), {"task": TASK})
        self.assertIsNone(hub_links.target(f"https://box.tail1234.ts.net/?task={TASK}"))
        self.assertIn("[::1]:8765", (hub_links.seen("[::1]:8765"), hub_links.origins())[1]["seen"])

    def test_a_proxy_header_naming_another_machine_is_not_noted(self):
        # A token holder's X-Forwarded-Host (review 2): it names another machine.
        for host in ("other.tail1234.ts.net", "evil.example:8765", "example.com:443", "100.101.102.103:8765",
                     "192.168.1.5:8765"):
            with self.subTest(host=host):
                hub_links.seen(host, "https")
                hub_links.seen(host)
                self.assertEqual(hub_links.origins()["seen"], [])
        self.assertIsNone(hub_links.target(f"https://other.tail1234.ts.net/session?room={TASK}"))

    def test_a_direct_request_counts_on_the_socket_it_came_in_on(self):
        lan = f"http://192.168.1.5:8765/?task={TASK}"
        hub_links.seen("192.168.1.9:8765", local=("192.168.1.5", 8765))     # not the address it came in on
        hub_links.seen("box:5173", local=("127.0.0.1", 8765))               # not the port it came in on
        hub_links.seen("other.tail1234.ts.net:8765", local=("100.64.1.2", 8765))
        self.assertEqual(hub_links.origins()["seen"], [])
        hub_links.seen("192.168.1.5:8765", local=("::ffff:192.168.1.5", 8765))
        self.assertEqual(hub_links.target(lan), {"task": TASK})
        self.assertIsNone(hub_links.target(f"http://192.168.1.5:3000/?task={TASK}"))

    def test_the_address_the_hub_listens_on(self):
        # A LAN or tailnet bind (review 2): _announce_remote.
        hub_links.listens("192.168.1.5:8765")
        hub_links.listens("[fd7a:115c::5]:8765")
        self.assertEqual(hub_links.target(f"http://192.168.1.5:8765/?task={TASK}"), {"task": TASK})
        self.assertEqual(hub_links.target(f"http://[fd7a:115c::5]:8765/?task={TASK}"), {"task": TASK})
        self.assertIsNone(hub_links.target(f"http://192.168.1.6:8765/?task={TASK}"))
        hub_links.listens("0.0.0.0:8765")
        self.assertNotIn("0.0.0.0:8765", hub_links.origins()["seen"])

    def test_what_tailscale_says_serves_the_hub(self):
        status = {"Self": {"DNSName": "ensemble-hub.tail1234.ts.net."}}
        serve = {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
                 "Web": {"ensemble-hub.tail1234.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}},
                         "ensemble-hub.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:5173"}}}}}
        hub_links.learn_tailscale(status, serve)
        at = "https://ensemble-hub.tail1234.ts.net"
        self.assertEqual(hub_links.target(f"{at}:8443/?task={TASK}"), {"task": TASK})
        self.assertIsNone(hub_links.target(f"{at}/?task={TASK}"), "443 serves another program")
        self.assertEqual(hub_links.target(f"http://ensemble-hub.tail1234.ts.net:8765/?task={TASK}"), {"task": TASK})
        self.assertIn("ensemble-hub.tail1234.ts.net", hub_links.origins()["names"])
        # Its tailnet name, from a proxy here, is now this machine's.
        hub_links.seen("ensemble-hub.tail1234.ts.net:9443", "https")
        self.assertEqual(hub_links.target(f"{at}:9443/?task={TASK}"), {"task": TASK})
        hub_links.learn_tailscale(None, {"Web": {"x:1": None, "y": {"Handlers": {"/": {"Proxy": "localhost:8765"}}}}})

    def test_bare_ipv6_loopback_addresses_are_named(self):
        url = f"http://[::1]:8765/session?room={ROOM}&msg=abc123def456&part=ask:1"
        with mock.patch.object(hub_links, "_d", FULL):
            self.assertEqual(hub_links.names(f"Open {url}."), "Open Project icons: A or B?.")
            self.assertEqual(notify.clip(f"Open {url}", 120), "Open Project icons: A or B?")
            self.assertEqual(hub_links.names("Up at http://[::1]:5173/ now"), "Up at http://[::1]:5173/ now")


class OwnHost(unittest.TestCase):
    def test_this_machines_names(self):
        for h in ("127.0.0.1", "127.3.4.5", "localhost", "LOCALHOST", "[::1]", "::1", "box", "box.lan",
                  "box.tail1234.ts.net", "box.tail1234.ts.net."):
            with self.subTest(host=h):
                self.assertTrue(hub_links.own_host(h))

    def test_other_hosts(self):
        for h in ("", "github.com", "example.com", "100.64.0.1", "100.100.12.34", "10.0.0.5", "192.168.1.2",
                  "other.tail1234.ts.net", "ts.net.example.com", "localhost.example.com", "box.example.com"):
            with self.subTest(host=h):
                self.assertFalse(hub_links.own_host(h))


class Name(unittest.TestCase):
    def test_each_kind_of_address(self):
        with mock.patch.object(hub_links, "_d", FULL):
            self.assertEqual(hub_links.name(CARD), "Project icons: A or B?")
            self.assertEqual(hub_links.name(CARD.replace("ask:1", "ask:0")), "Which feed?")
            self.assertEqual(hub_links.name(MSG), "the message")
            self.assertEqual(hub_links.name(TASK_URL), "#18 Billing export")
            self.assertEqual(hub_links.name(f"{BASE}/?task={TASK}"), "#18 Billing export")
            self.assertEqual(hub_links.name(f"{BASE}/session?id={TASK}"), "#18 Billing export")
            self.assertEqual(hub_links.name(f"{BASE}/session?room={ROOM}"), "PO", "a room without a number: its title")
            self.assertEqual(hub_links.name(f"{BASE}/session?room={ROOM}&thread=t-1"), "the thread")
            self.assertEqual(hub_links.name(FILE), "links.png")
            self.assertEqual(hub_links.name(NIX_FILE), "plan.md")
            self.assertEqual(hub_links.name("http://127.0.0.1:8765/fileview?path=%2Fhome%2Fsam%2FDocuments%2F"), "Documents")
            self.assertEqual(hub_links.name(f"{BASE}/"), "Ensemble")
            self.assertEqual(hub_links.name("http://box.tail1234.ts.net:8765/"), "Ensemble")
            self.assertEqual(hub_links.name("http://box.tail1234.ts.net:8765/session?room=" + TASK), "#18 Billing export")

    def test_a_long_question_is_clipped(self):
        with mock.patch.object(hub_links, "_d", FULL):
            got = hub_links.name(f"{BASE}/session?room={ROOM}&msg=long00000000&part=ask:0")
        self.assertLessEqual(len(got), hub_links.QUESTION_MAX)
        self.assertGreater(len(got), hub_links.QUESTION_MAX - 5)
        self.assertTrue(got.endswith("…"))
        self.assertTrue(LONG_Q.startswith(got[:-1].rstrip()))

    def test_unresolved_things_read_as_generic_words(self):
        with _hub():
            self.assertEqual(hub_links.name(CARD), "the question card", "no such message")
            self.assertEqual(hub_links.name(TASK_URL), "the task", "no such room")
        with _hub(questions={(ROOM, "abc123def456"): QUESTIONS}):
            self.assertEqual(hub_links.name(CARD.replace("ask:1", "ask:7")), "the question card", "no such card")
        with _hub(questions={(ROOM, "abc123def456"): []}):
            self.assertEqual(hub_links.name(CARD), "the question card", "the message asks nothing")

        class Broken(_Hub):
            def resolve_message_ref(self, room, msg):
                raise OSError("disk")
        with mock.patch.object(hub_links, "_d", Broken()):
            self.assertEqual(hub_links.name(CARD), "the question card")
        with mock.patch.object(hub_links, "_d", None):
            self.assertEqual(hub_links.name(CARD), "the question card")
            self.assertEqual(hub_links.name(TASK_URL), "the task")

    def test_other_websites_are_not_the_hubs(self):
        for u in ("https://github.com/acme/x/issues/1", f"https://example.com/session?room={ROOM}&msg=abc&part=ask:0",
                  "https://example.com/fileview?path=%2Fetc%2Fpasswd", "ftp://127.0.0.1/x", "not a url",
                  # A dev server on this machine is another website: only the hub's port is the hub's page.
                  "http://localhost:5173/", "http://127.0.0.1:3000/app?x=1", "http://box.tail1234.ts.net:3000/"):
            with self.subTest(url=u):
                self.assertIsNone(hub_links.name(u))
                self.assertIsNone(hub_links.target(u))


class Names(unittest.TestCase):
    def names(self, text):
        with mock.patch.object(hub_links, "_d", FULL):
            return hub_links.names(text)

    def test_bare_addresses_read_as_names(self):
        self.assertEqual(self.names(f"Open {CARD} now"), "Open Project icons: A or B? now")
        self.assertEqual(self.names(f"See {MSG}"), "See the message")
        self.assertEqual(self.names(f"Picked up in {TASK_URL}"), "Picked up in #18 Billing export")
        self.assertEqual(self.names(f"The picture: {FILE}"), "The picture: links.png")

    def test_a_sentences_punctuation_stays(self):
        self.assertEqual(self.names(f"See {FILE}."), "See links.png.")
        self.assertEqual(self.names(f"Done ({TASK_URL}), next."), "Done (#18 Billing export), next.")
        self.assertEqual(self.names(f"Answer {CARD}, please!"), "Answer Project icons: A or B?, please!")
        self.assertEqual(self.names(f"{MSG}?"), "the message?")

    def test_a_markdown_link_reads_as_its_words(self):
        self.assertEqual(self.names(f"Answer [the icons card]({CARD}) today"), "Answer the icons card today")
        self.assertEqual(self.names(f"[links.png](<{FILE}>)"), "links.png")
        self.assertEqual(self.names(f"[#18]({TASK_URL})."), "#18.")
        # Empty words: the address is still named, never shown.
        self.assertNotRegex(self.names(f"[]({CARD})"), BARE)

    def test_angle_brackets_and_code_spans(self):
        self.assertEqual(self.names(f"Open <{CARD}>"), "Open Project icons: A or B?")
        self.assertEqual(self.names(f"Open `{FILE}`"), "Open links.png")
        self.assertEqual(self.names(f"Open ` {TASK_URL} `."), "Open #18 Billing export.")

    def test_other_websites_stay_as_written(self):
        for text in ("Filed https://github.com/acme/x/issues/1.",
                     f"Mirror: https://example.com/session?room={ROOM}&msg=abc123def456&part=ask:0",
                     "[the issue](https://github.com/acme/x/issues/1)",
                     "<https://github.com/acme/x/issues/1>",
                     "`https://example.com/fileview?path=%2Fx`",
                     "The dev server is up at http://localhost:5173/.",
                     "no address here", ""):
            with self.subTest(text=text):
                self.assertEqual(self.names(text), text)

    def test_a_mixed_line(self):
        got = self.names(f"Re [the card]({CARD}): see {FILE} and https://github.com/acme/x/issues/1 in {TASK_URL}.")
        self.assertEqual(got, "Re the card: see links.png and https://github.com/acme/x/issues/1 in #18 Billing export.")

    def test_none_and_non_text(self):
        self.assertEqual(hub_links.names(None), "")
        self.assertEqual(hub_links.names(12), "12")


class PlainTextPlaces(unittest.TestCase):
    """The Inbox's rows and a notification's line show names, never an address."""
    TEXTS = [f"Waiting on {CARD}", f"Picture: {FILE}.", f"Moved to {TASK_URL}", f"[this one]({MSG}) again",
             f"Open `{NIX_FILE}`", f"<{CARD}>"]

    def test_notification_lines(self):
        with mock.patch.object(hub_links, "_d", FULL):
            got = [notify.clip(t, 120) for t in self.TEXTS]
        self.assertEqual(got[0], "Waiting on Project icons: A or B?")
        self.assertEqual(got[1], "Picture: links.png.")
        self.assertEqual(got[2], "Moved to #18 Billing export")
        self.assertEqual(got[3], "this one again")
        self.assertEqual(got[4], "Open plan.md")
        for t in got:
            with self.subTest(line=t):
                self.assertNotRegex(t, BARE)

    def test_inbox_rows(self):
        with mock.patch.object(hub_links, "_d", FULL):
            got = [inbox._line(t) for t in self.TEXTS]
            clipped = inbox._line(f"Waiting on {CARD} " + "word " * 60, 40)
        self.assertEqual(got[0], "Waiting on Project icons: A or B?")
        self.assertEqual(got[1], "Picture: links.png.")
        self.assertEqual(got[2], "Moved to #18 Billing export")
        self.assertEqual(got[3], "this one again")
        for t in got + [clipped]:
            with self.subTest(line=t):
                self.assertNotRegex(t, BARE)
        self.assertLessEqual(len(clipped), 40)
        self.assertTrue(clipped.startswith("Waiting on Project icons"))
        with mock.patch.object(hub_links, "_d", FULL):
            self.assertEqual(inbox._line("Filed https://github.com/acme/x/issues/1"), "Filed https://github.com/acme/x/issues/1")


# What tells an agent to show a reference bare (the wording before P358).
BARE_RULES = re.compile(r"paste (?:its|the|a) (?:link|reference)|paste the matching reference|"
                        r"reportReference as it is|link alone|pasted bare", re.I)


def _literals(path: Path) -> list[str]:
    """Every string literal of a Python file, an f-string's constant parts joined."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values))
    return out


class Prompts(unittest.TestCase):
    def test_no_prompt_says_to_paste_a_reference_bare(self):
        texts = {"dashboard.ASK_NOTE": dashboard.ASK_NOTE, "dashboard.LINK_NOTE": dashboard.LINK_NOTE,
                 "dashboard.SOLO_REPORT_NOTE": dashboard.SOLO_REPORT_NOTE, "asks.ASK_AGAIN_RULE": asks.ASK_AGAIN_RULE}
        for name in ("ensemble_tools.py", "asks.py", "dashboard.py"):
            for i, s in enumerate(_literals(ROOT / name)):
                texts[f"{name} literal {i}"] = s
        texts["skills/ensemble/SKILL.md"] = (ROOT / "skills/ensemble/SKILL.md").read_text(encoding="utf-8")
        bad = {k: BARE_RULES.search(v).group(0) for k, v in texts.items() if BARE_RULES.search(v)}
        self.assertEqual(bad, {})

    def test_the_link_rule_reaches_every_ask_note(self):
        self.assertIn(dashboard.LINK_NOTE, dashboard.ASK_NOTE)
        self.assertIn(dashboard.ASK_NOTE, dashboard.SOLO_REPORT_NOTE)
        self.assertIn("clickable name", dashboard.LINK_NOTE)
        self.assertIn("never show it bare", dashboard.LINK_NOTE)
        self.assertIn("never show the link bare", asks.ASK_AGAIN_RULE)
        self.assertIn("[its question](", asks.ASK_AGAIN_RULE)

    def test_the_skill_says_links_are_names(self):
        skill = (ROOT / "skills/ensemble/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("clickable name", skill)


MD_LINK_LINE = re.compile(r"^- \[[^\[\]\n]+\]\(https?://[^\s()]+\) \(open \d+ (?:min|h|d)\)$")


class CardLine(unittest.TestCase):
    def test_a_card_line_is_a_markdown_link(self):
        c = {"question": "Which [broker] feed for the replay?", "mid": "abc123def456", "n": 1, "ts": 1000.0, "who": "claude"}
        line = asks._card_line(ROOM, c, 1000.0 + 3 * 3600, 160)
        self.assertEqual(line, f"[Which (broker) feed for the replay?]({asks.card_url(ROOM, 'abc123def456', 1)}) (open 3 h)")
        short = asks._card_line(ROOM, {**c, "question": LONG_Q}, 1060.0, 30)
        self.assertRegex(short, r"^\[[^\]]{30}\]\(http")
        self.assertIn("…](", short)
        self.assertTrue(short.endswith("(open 1 min)"))


try:
    import test_ask_repeats
except Exception:       # noqa: BLE001 — the brief's case needs its room helpers
    test_ask_repeats = None


@unittest.skipIf(test_ask_repeats is None, "tests/test_ask_repeats.py did not import")
class Brief(test_ask_repeats._Repeats if test_ask_repeats else unittest.TestCase):
    def test_the_brief_writes_each_open_card_as_a_markdown_link(self):
        rid = self.team_room()
        refs = [self.ask(rid, [{"question": f"Decide {t} now?", "yesno": True}], t)["references"][0]
                for t in ("retention", "backups")]
        block = asks.prompt_block(chatroom.get_room(rid))
        lines = block.split("\n")[1:]
        self.assertEqual(len(lines), 2)
        for line, ref, t in zip(lines, reversed(refs), ("backups", "retention")):
            with self.subTest(line=line):
                self.assertRegex(line, MD_LINK_LINE)
                self.assertIn(f"[Decide {t} now?]({ref})", line)
        self.assertNotRegex(block, r"“[^”]*” https?://", "never the old quoted question and a bare link")
        self.assertIn("[its question](its link)", block.split("\n")[0])


if __name__ == "__main__":
    unittest.main()
