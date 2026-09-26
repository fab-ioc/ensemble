"""One set of session actions for the task panel, the list rows and the
popped-out session window (static/actions.js, docs/session-actions.md).

* the matrix: for each kind of session and state, the primary action and the
  More menu's groups, in order, and why an action that is off is off;
* parity: the dashboard's row and the pop-out's room, for the same task, give
  the same bar, and both pages draw it with the shared renderer and styles;
* Make PO is always in the menu, and whether it is on is the hub's answer
  (row.makePo) — a row without one is "not known", never "yes";
* popups (the More menu, the colour picker) open beside or under their
  trigger, flip when they do not fit and stay inside the window, at a desktop
  size and at 360px.

The model runs in Node and is skipped without it.
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
NODE = shutil.which("node")
ACTIONS = (ROOT / "static" / "actions.js").read_text(encoding="utf-8")
INDEX = (ROOT / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
SESSION = (ROOT / "session.html").read_text(encoding="utf-8").replace("\r\n", "\n")


def fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {name}\(", src, re.M)
    return src[m.start():src.index("\n}\n", m.start()) + 3]


def run_node(body: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "t.cjs"
        script.write_text(ACTIONS + "\n" + body, encoding="utf-8")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-3000:])
    return json.loads(out.stdout)


MATRIX_JS = r"""
const A = SessionActions;
const env = hub => ({ hub, features: { focus: true, themes: true, send: true, geometry: true },
                      terminalName: 'Windows Terminal', fileManagerName: 'Explorer' });
const bare = { hub: false, features: {}, terminalName: 'Terminal', fileManagerName: 'Finder' };
const ok = { ok: true, code: '' };
const states = {
  rawHistory: { kind: 'raw', sessionId: 's1', cwd: 'C:\\w', agent: 'claude', makePo: ok },
  rawNoCwd: { kind: 'raw', sessionId: 's2', cwd: '', makePo: { ok: false, code: 'no_cwd', reason: 'No folder.' } },
  rawLive: { kind: 'raw', sessionId: 's3', cwd: 'C:\\w', pid: 42, live: true,
             makePo: { ok: false, code: 'live', reason: 'Open in a terminal.', fix: 'Close it there first.' } },
  rawArchived: { kind: 'raw', sessionId: 's4', cwd: 'C:\\w', archived: true, makePo: ok },
  taskOne: { kind: 'room', sessionId: 'room-1', roomId: 'room-1', cwd: 'C:\\t', members: [{ agent: 'claude' }], makePo: ok },
  taskRunning: { kind: 'room', sessionId: 'room-2', roomId: 'room-2', cwd: 'C:\\t', live: true, status: 'active',
                 makePo: { ok: false, code: 'other_project', reason: 'Already a task in a project.' } },
  taskPaused: { kind: 'room', sessionId: 'room-3', roomId: 'room-3', cwd: 'C:\\t', live: true, status: 'paused' },
  draft: { kind: 'room', sessionId: 'room-4', roomId: 'room-4', cwd: 'C:\\t', draft: true,
           makePo: { ok: false, code: 'draft', reason: 'Not started yet.' } },
  po: { kind: 'room', sessionId: 'room-5', roomId: 'room-5', cwd: 'C:\\t', makePo: { ok: false, code: 'is_po', reason: 'Already a PO.' } },
  noCwdTask: { kind: 'room', sessionId: 'room-6', roomId: 'room-6', cwd: '' },
  resuming: { kind: 'room', sessionId: 'room-7', roomId: 'room-7', cwd: 'C:\\t', resuming: true },
  orphan: { kind: 'orphan', sessionId: 'grp-1', makePo: { ok: false, code: 'orphan', reason: 'Its record is gone.' } },
  past: { kind: 'past', sessionId: 'old-1', cwd: 'C:\\w', agent: 'claude', makePo: ok },
};
const shape = m => ({
  primary: m.primary && [m.primary.id, m.primary.label, !!m.primary.disabled, m.primary.reason || '', m.primary.variant || ''],
  groups: m.groups.map(g => g.map(i => i.id)),
  off: Object.fromEntries(m.groups.flat().filter(i => i.disabled).map(i => [i.id, i.reason])),
  items: Object.fromEntries(m.groups.flat().map(i => [i.id, i])),
});
const out = {};
for (const [k, s] of Object.entries(states)) {
  out[k] = { hub: shape(A.sessionActions(s, env(true))), away: shape(A.sessionActions(s, env(false))),
             bare: shape(A.sessionActions(s, bare)), html: A.actionBarHtml(A.sessionActions(s, env(true))) };
}
out.empty = A.actionBarHtml({ primary: null, groups: [] });
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class TheMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run_node(MATRIX_JS)

    def test_primary_action(self):
        p = {k: v["hub"]["primary"] for k, v in self.o.items() if isinstance(v, dict)}
        self.assertEqual(p["rawHistory"][:2], ["open", "Open"])
        self.assertEqual(p["rawLive"][:2], ["focus", "Focus"])
        self.assertIsNone(self.o["rawLive"]["away"]["primary"], "no Focus from another computer")
        self.assertEqual(p["taskOne"][:2], ["resume", "Resume"])
        self.assertEqual(p["taskRunning"][:2], ["end", "End"])
        self.assertEqual(p["taskRunning"][4], "default", "End keeps the session: not the primary colour")
        self.assertEqual(p["draft"][:2], ["start", "Start"])
        self.assertEqual(p["orphan"][:2], ["open", "Open"])
        self.assertEqual(p["resuming"][2:4], [True, "Starting up…"])

    def test_groups_and_their_order(self):
        g = lambda k, e="hub": self.o[k][e]["groups"]
        self.assertEqual(g("rawHistory"), [["terminal"], ["rename", "auto", "moveproj", "makepo", "archive"],
                                           ["finder", "ide", "colours"], ["delete"]])
        self.assertEqual(g("rawLive"), [["send"], ["rename", "auto", "moveproj", "makepo"],
                                        ["finder", "ide", "colours"], ["close"]])
        self.assertEqual(g("rawLive", "away"), [["focus", "send"], ["rename", "auto", "moveproj", "makepo"],
                                                ["finder", "ide", "colours"], ["close"]])
        self.assertEqual(g("taskRunning"), [["terminals", "pause"], ["rename", "auto", "agents", "moveproj", "makepo"],
                                            ["finder", "ide", "colours", "chat-colours"], ["delete"]])
        self.assertEqual(g("taskRunning", "away")[0], ["terminals", "terms-hide", "pause"])
        self.assertEqual(g("taskOne"), [["rename", "auto", "agents", "moveproj", "makepo"],
                                        ["finder", "ide", "colours", "chat-colours"], ["delete"]])
        self.assertEqual(g("draft"), g("taskOne"))
        self.assertEqual(g("orphan"), [["makepo"], ["delete"]])
        # A seat's past conversation is read only: its folder, nothing that
        # would continue, move or remove it.
        self.assertEqual(g("past"), [["finder"]])
        self.assertIsNone(self.o["past"]["hub"]["primary"])
        # Destructive last, and red.
        for k in ("rawHistory", "rawLive", "taskOne", "taskRunning", "orphan"):
            last = self.o[k]["hub"]["items"][g(k)[-1][0]]
            self.assertTrue(last["danger"], k)
        self.assertIn('<div class="am-sep" role="separator"></div><button type="button" role="menuitem" class="am-item danger',
                      self.o["taskOne"]["html"])

    def test_what_is_off_says_why(self):
        off = lambda k, e="hub": self.o[k][e]["off"]
        self.assertEqual(off("taskRunning")["agents"], "Running: end it first, then reassign.")
        self.assertEqual(off("taskRunning")["delete"], "Running: end it first.")
        self.assertEqual(off("rawLive", "away")["focus"], "Only on the hub’s own screen: this computer cannot raise its windows.")
        self.assertEqual(off("rawLive", "bare")["send"], "Typing into a terminal session is not supported on this system.")
        self.assertEqual(off("noCwdTask")["finder"], "No working folder is recorded for it.")
        self.assertEqual(off("noCwdTask")["ide"], "No working folder is recorded for it.")
        self.assertEqual(off("noCwdTask")["colours"], "No working folder is recorded for it.")
        self.assertEqual(off("rawNoCwd")["terminal"], "No working folder is recorded for it.")
        self.assertEqual(off("taskOne", "bare")["colours"], "Terminal here has no colour schemes.")
        self.assertEqual(off("taskOne", "bare")["chat-colours"], "Terminal here has no colour schemes.")
        # Every item that is off has a reason, and every item has words.
        for k, v in self.o.items():
            if not isinstance(v, dict):
                continue
            for e in ("hub", "away", "bare"):
                for i in v[e]["items"].values():
                    self.assertTrue(i["label"], (k, i["id"]))
                    if i.get("disabled"):
                        self.assertTrue(i["reason"], (k, e, i["id"]))
                    else:
                        self.assertTrue(i["title"], (k, e, i["id"]))

    def test_platform_words(self):
        items = lambda k, e: self.o[k][e]["items"]
        self.assertEqual(items("taskOne", "hub")["finder"]["label"], "Open in Explorer")
        self.assertEqual(items("taskOne", "away")["finder"]["label"], "Browse the folder")
        self.assertEqual(items("taskOne", "hub")["ide"]["label"], "Open in editor")
        self.assertEqual(items("taskOne", "away")["ide"]["label"], "Show in Workspace")
        self.assertEqual(items("rawHistory", "hub")["terminal"]["label"], "Open in a Windows Terminal window")
        self.assertEqual(items("rawHistory", "away")["terminal"]["label"], "Resume here with its terminal")
        self.assertEqual(items("taskPaused", "hub")["pause"]["label"], "Resume")

    def test_the_markup(self):
        h = self.o["taskRunning"]["html"]
        # A disabled item: aria-disabled, focusable, its reason visible, and no
        # action class, so no page handler can act on it.
        m = re.search(r'<button type="button" role="menuitem" class="am-item" data-act="agents"[^>]*>', h)
        self.assertTrue(m, h)
        self.assertIn('aria-disabled="true"', m.group(0))
        self.assertNotIn("agents-btn", m.group(0))
        self.assertNotIn(" disabled", m.group(0).replace("aria-disabled", ""))
        self.assertIn('<span class="am-why">Running: end it first, then reassign.</span>', h)
        # The More button and its menu.
        self.assertIn('class="am-btn am-more" aria-haspopup="menu" aria-expanded="false" aria-label="More actions"', h)
        self.assertIn('<div class="am-menu" role="menu" aria-label="More actions" hidden>', h)
        # The colour item keeps the menu open and says it opens a popup.
        self.assertRegex(h, r'class="am-item theme-dd-trigger" data-act="colours"[^>]*data-am-keep="1" aria-haspopup="dialog"')
        self.assertRegex(h, r'role="menuitemcheckbox" class="am-item chat-scheme-btn"[^>]*aria-checked="false"')
        # The primary action carries its handler's class.
        self.assertIn('class="am-btn am-primary room-end" data-act="end" data-room="room-2"', h)
        self.assertEqual(self.o["empty"], '<div class="am-bar"></div>')


MAKEPO_JS = r"""
const A = SessionActions;
const env = { hub: true, features: { themes: true }, terminalName: 'T', fileManagerName: 'F' };
const item = (kind, makePo) => A.sessionActions({ kind, sessionId: 's', roomId: kind === 'room' ? 'room-1' : '', cwd: 'C:\\x', makePo }, env)
  .groups.flat().find(i => i.id === 'makepo');
const codes = ['live', 'recent', 'held', 'other_project', 'is_po', 'draft', 'agents', 'unsupported', 'orphan', 'no_cwd', 'has_po'];
const out = {
  codes: codes.map(c => item('room', { ok: false, code: c, reason: 'Because ' + c + '.', fix: 'Do ' + c + '.' })),
  raw: codes.map(c => item('raw', { ok: false, code: c, reason: 'Because ' + c + '.' })),
  orphan: item('orphan', { ok: false, code: 'orphan', reason: 'Gone.' }),
  missing: [item('room', undefined), item('raw', null), item('orphan', undefined), item('room', 'yes')],
  yes: [item('room', { ok: true, code: '' }), item('raw', { ok: true, code: 'recent', confirm: 'Written to 2 minutes ago.' })],
  noWords: item('room', { ok: false, code: 'held' }),
  html: A.actionBarHtml(A.sessionActions({ kind: 'raw', sessionId: 's9', cwd: 'C:\\x', makePo: { ok: true } }, env)),
  htmlOff: A.actionBarHtml(A.sessionActions({ kind: 'room', sessionId: 'room-9', roomId: 'room-9', cwd: 'C:\\x',
                           makePo: { ok: false, code: 'agents', reason: 'Has two agents.', fix: 'Remove one.' } }, env)),
};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed")
class MakePo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = run_node(MAKEPO_JS)

    def test_always_shown_with_the_hubs_reason(self):
        for c, it in zip(["live", "recent", "held", "other_project", "is_po", "draft", "agents", "unsupported",
                          "orphan", "no_cwd", "has_po"], self.o["codes"]):
            self.assertEqual(it["label"], "Make PO of a new project…")
            self.assertTrue(it["disabled"], c)
            self.assertEqual(it["reason"], f"Because {c}. Do {c}.", c)
        self.assertTrue(all(i["disabled"] for i in self.o["raw"]))
        self.assertEqual(self.o["orphan"]["reason"], "Gone.")
        self.assertEqual(self.o["noWords"]["reason"], "It cannot become a PO.")

    def test_a_row_without_an_answer_is_not_known(self):
        for it in self.o["missing"]:
            self.assertTrue(it["disabled"])
            self.assertEqual(it["reason"], "Not known yet whether it can become a PO: the hub has not said.")

    def test_on_only_when_the_hub_says_so(self):
        task, session = self.o["yes"]
        self.assertFalse(task.get("disabled"))
        self.assertEqual((task["cls"], task["data"]), ("makepo-btn", {"room": "room-1"}))
        self.assertFalse(session.get("disabled"))
        self.assertEqual(session["data"], {"sid": "s"})
        self.assertRegex(self.o["html"], r'class="am-item makepo-btn" data-act="makepo" data-sid="s9"')
        self.assertRegex(self.o["htmlOff"], r'class="am-item" data-act="makepo" title="Has two agents. Remove one." aria-disabled="true">'
                                             r'<span class="am-lbl">Make PO of a new project…</span><span class="am-why">Has two agents. Remove one.</span>')

    def test_the_pages_write_no_conditions_of_their_own(self):
        self.assertNotIn("makePoTaskOk", ACTIONS)
        self.assertIn("makePo: r.makePo", INDEX)
        self.assertIn("makePo: room.makePo", SESSION)
        # A pop-out's request is checked against the row's own answer again.
        self.assertIn("const item = SessionActions.makePoItem(actionState(row, row.isLive));", INDEX)


PARITY_JS = r"""
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const out = {};
function dock(row, platform, hub, schemeOn) {
  const PLATFORM = platform, onHubMachine = () => hub;
  const T = () => PLATFORM.terminalName || 'terminal', FM = () => PLATFORM.fileManagerName || 'file manager';
  const CHAT_SCHEME_ON = { [row.cwd]: schemeOn };
  %(index)s
  return actionsCell(row, row.isLive);
}
function popout(room, platform, hub, schemeOn, resuming) {
  const ROOM = room.id, ROOM_OBJ = room, SCHEME_ON = schemeOn, ROOM_RESUMING = resuming;
  const PLATFORM = platform, onHubMachine = () => hub;
  %(session)s
  return SessionActions.actionBarHtml(SessionActions.sessionActions(actionState(), actionEnv()));
}
const win = { terminalName: 'Windows Terminal', fileManagerName: 'Explorer', features: { focus: true, themes: 'launch-only', send: true } };
const mac = { terminalName: 'iTerm', fileManagerName: 'Finder', features: { focus: true, themes: true, geometry: true } };
const bare = { terminalName: 'Terminal', fileManagerName: 'Files', features: {} };
// Each room as /api/room gives it to the pop-out, and its row as /api/sessions
// gives it to the dashboard: both made by dashboard.py in the test (rooms, ROWS).
// makePo comes to the row with #87; until then it takes the room's.
const rooms = %(rooms)s, ROWS = %(rows)s;
const rowOf = room => ({ ...ROWS[room.id], makePo: room.makePo });
out.pairs = [];
for (const [name, room] of Object.entries(rooms))
  for (const [pn, pf] of Object.entries({ win, mac, bare }))
    for (const hub of [true, false])
      for (const on of [false, true])
        out.pairs.push([`${name}/${pn}/${hub ? 'hub' : 'away'}/${on ? 'scheme' : 'theme'}`,
                        dock(rowOf(room), pf, hub, on), popout(room, pf, hub, on, !!room.pending && room.pending.state !== 'failed')]);
console.log(JSON.stringify(out));
"""


def css_block(src: str) -> str:
    i = src.index("  /* ---- Session actions: begin shared block")
    j = src.index("  /* ---- Session actions: end shared block */", i)
    return src[i:j]


ONE = [{"kind": "human", "identity": "user"}, {"kind": "agent", "identity": "claude", "agent": "claude"}]
TWO = ONE + [{"kind": "agent", "identity": "codex", "agent": "codex"}]
# name: (the room's record, whether its agents run, a resume's state or None).
# makePo stands in for #87's answer, which only /api/room carries so far.
RECORDS = {
    "running": ({"cwd": "C:/t/a", "status": "active", "participants": ONE, "makePo": {"ok": True}}, True, None),
    "paused": ({"cwd": "C:/t/b", "status": "paused", "participants": TWO,
                "makePo": {"ok": False, "code": "agents", "reason": "Two agents."}}, True, None),
    "waiting": ({"cwd": "C:/t/h", "status": "waiting_human", "participants": ONE, "makePo": {"ok": True}}, True, None),
    "stopped": ({"cwd": "C:/t/c", "status": "active", "participants": ONE,
                 "makePo": {"ok": False, "code": "other_project", "reason": "In a project.", "fix": "Move it out first."}},
                False, None),
    "stoppedPaused": ({"cwd": "C:/t/k", "status": "paused", "participants": ONE, "makePo": {"ok": True}}, False, None),
    "resuming": ({"cwd": "C:/t/i", "status": "paused", "participants": ONE, "makePo": {"ok": True}}, False, "resuming"),
    "resumeFailed": ({"cwd": "C:/t/j", "status": "active", "participants": ONE, "makePo": {"ok": True}}, False, "failed"),
    "draft": ({"cwd": "C:/t/d", "launched": False, "status": "active", "participants": ONE,
               "makePo": {"ok": False, "code": "draft", "reason": "Not started."}}, False, None),
    "po": ({"cwd": "C:/t/e", "status": "active", "participants": ONE,
            "makePo": {"ok": False, "code": "is_po", "reason": "A PO already."}}, False, None),
    "noFolder": ({"cwd": "", "status": "active", "participants": ONE}, False, None),
    "unknown": ({"cwd": "C:/t/g", "status": "active", "participants": ONE}, True, None),
}


def served(tmp: str) -> tuple[dict, dict]:
    """Each room of RECORDS as /api/room serves it (the pop-out's input) and its
    row as /api/sessions serves it (the dashboard's), from dashboard.py."""
    import copy
    import sys
    from unittest import mock
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT))
    import dashboard
    from test_session_window import Bench
    bench = Bench(Path(tmp))
    resumes = {}
    for i, (name, (rec, running, resume)) in enumerate(RECORDS.items()):
        rid = f"room-{i}"
        bench.rooms.append({"id": rid, "title": name, "createdAt": 1, "updatedAt": 2, "messages": [],
                            "running": running, **copy.deepcopy(rec)})
        if resume:
            resumes[rid] = dashboard._Resume()
            if resume == "failed":
                resumes[rid].fail("no")
    live = mock.patch.object(dashboard, "_room_is_live", side_effect=lambda rm: bool(rm.get("running")))
    with mock.patch.dict(dashboard._RESUMES, resumes, clear=True):
        rows = {r["roomId"]: r for r in bench.load(50) if r.get("headless")}
        with live, mock.patch.object(dashboard, "_backfill_codex_session_ids"),                 mock.patch.object(dashboard.attention, "by_room", return_value={}):
            rooms = {rm["title"]: dashboard._annotate_room_liveness(copy.deepcopy(rm)) for rm in bench.rooms}
    return rooms, rows


class Parity(unittest.TestCase):
    def test_both_pages_load_the_shared_script_and_use_its_renderer(self):
        for name, src in (("index.html", INDEX), ("session.html", SESSION)):
            self.assertEqual(src.count('<script src="/static/actions.js"></script>'), 1, name)
            self.assertIn("SessionActions.actionBarHtml(SessionActions.sessionActions(", src, name)
            # Loaded before the page's own script, so its click handler runs first.
            self.assertLess(src.index('<script src="/static/actions.js"></script>'), src.index("<script>\n", src.index("</head>") - 20000), name)

    def test_the_styles_are_the_same_in_both_pages(self):
        self.assertEqual(css_block(SESSION), css_block(INDEX))
        block = re.sub(r"/\*[\s\S]*?\*/", "", css_block(INDEX))
        self.assertNotRegex(block, r"#[0-9a-fA-F]{3,6}\b", "tokens only")
        self.assertNotIn("opacity", block)
        self.assertIn("var(--touch-min)", block)
        self.assertIn("var(--focus-ring)", block)

    def test_the_old_button_walls_are_gone(self):
        for gone in ('id="rename"', 'id="autolabel"', 'id="explorer"', 'id="ide"', 'id="theme"', 'id="end"',
                     'id="resume"', 'id="pause"'):
            self.assertNotIn(gone, SESSION)
        for gone in ("function detailOverflow(", "detailHeadOverflow", 'class="dp-ov-btn"'):
            self.assertNotIn(gone, INDEX)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_docked_and_popped_out_show_the_same_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            rooms, rows = served(tmp)
        self.assertEqual(rows[rooms["paused"]["id"]]["status"], "idle", "the list's status is its activity dot")
        body = PARITY_JS % {
            "rooms": json.dumps(rooms), "rows": json.dumps(rows),
            "index": "\n".join(fn(INDEX, n) for n in ("actionState", "actionEnv", "actionsCell")),
            "session": "\n".join(fn(SESSION, n) for n in ("actionState", "actionEnv")),
        }
        pairs = run_node(body)["pairs"]
        self.assertEqual(len(pairs), len(RECORDS) * 3 * 2 * 2)
        for name, docked, popped in pairs:
            self.assertEqual(docked, popped, name)
        by = {n: d for n, d, _ in pairs}
        self.assertIn('data-act="end"', by["running/win/hub/theme"])
        self.assertIn('data-act="start"', by["draft/mac/away/theme"])
        self.assertIn("In a project. Move it out first.", by["stopped/win/hub/theme"])
        self.assertIn("Not known yet whether it can become a PO", by["unknown/win/hub/theme"])
        self.assertIn('aria-checked="true"', by["running/mac/hub/scheme"])
        # The lifecycle the bar shows is the room's, not the list's dot.
        self.assertRegex(by["paused/win/hub/theme"], r'data-act="pause" data-room="[^"]+" data-status="paused"[^>]*><span class="am-lbl">Resume<')
        self.assertRegex(by["running/win/hub/theme"], r'data-status="active"[^>]*><span class="am-lbl">Pause<')
        self.assertNotIn('data-act="pause"', by["stoppedPaused/win/hub/theme"])
        # A resume under way: its primary action is off in both, until it fails.
        self.assertIn("Starting up…", by["resuming/win/hub/theme"])
        self.assertNotIn("Starting up…", by["resumeFailed/win/hub/theme"])


@unittest.skipUnless(NODE, "node is not installed")
class PopupPlace(unittest.TestCase):
    JS = r"""
const P = SessionActions.popupPlace;
const r = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h });
const desk = { width: 1280, height: 800 }, phone = { width: 360, height: 640 };
const menu = { width: 240, height: 300 }, picker = { width: 280, height: 420 };
console.log(JSON.stringify({
  deskBelow: P(r(900, 100, 80, 32), menu, desk, { align: 'end' }),
  deskBottom: P(r(900, 740, 80, 32), menu, desk, { align: 'end' }),
  deskRight: P(r(1250, 100, 30, 32), menu, desk),
  deskBeside: P(r(600, 300, 240, 32), picker, desk, { side: 'beside' }),
  deskBesideLeft: P(r(1030, 300, 240, 32), picker, desk, { side: 'beside' }),
  deskBesideBottom: P(r(700, 760, 240, 32), picker, desk, { side: 'beside' }),
  phoneBelow: P(r(200, 60, 100, 44), menu, phone, { align: 'end' }),
  phoneBottom: P(r(200, 590, 100, 44), menu, phone, { align: 'end' }),
  phoneBeside: P(r(60, 100, 240, 44), picker, phone, { side: 'beside' }),
  phoneBesideLow: P(r(60, 560, 240, 44), picker, phone, { side: 'beside' }),
  phoneWide: P(r(0, 100, 50, 44), { width: 500, height: 200 }, phone),
  scrolledAway: P(r(900, -200, 80, 32), menu, desk, { align: 'end' }),
  scrolledBelow: P(r(900, 900, 80, 32), menu, desk, { align: 'end' }),
  tooTall: P(r(100, 300, 80, 32), { width: 200, height: 2000 }, desk),
  // A phone with its keyboard up: 300px of the 640 left, scrolled 120px down.
  keyboard: P(r(60, 300, 240, 44), picker, { width: 360, height: 300, left: 0, top: 120 }, { side: 'beside' }),
  zoomed: P(r(400, 200, 80, 32), menu, { width: 400, height: 500, left: 300, top: 100 }, { align: 'end' }),
}));
"""

    @classmethod
    def setUpClass(cls):
        cls.o = run_node(cls.JS)

    def inside(self, p, size, vp, margin=8):
        w = p["width"] or size[0]
        h = p["maxHeight"] or size[1]
        self.assertGreaterEqual(p["left"], margin, p)
        self.assertGreaterEqual(p["top"], margin, p)
        self.assertLessEqual(p["left"] + w, vp[0] - margin, p)
        self.assertLessEqual(p["top"] + h, vp[1] - margin, p)

    def test_desktop(self):
        o, desk, menu, picker = self.o, (1280, 800), (240, 300), (280, 420)
        self.assertEqual((o["deskBelow"]["placement"], o["deskBelow"]["top"], o["deskBelow"]["left"]), ("below", 136, 740),
                         "under the trigger, right edges lined up")
        self.assertEqual((o["deskBottom"]["placement"], o["deskBottom"]["top"]), ("above", 436), "flipped above at the bottom edge")
        self.assertEqual(o["deskRight"]["left"], 1280 - 8 - 240, "kept off the right edge")
        self.assertEqual((o["deskBeside"]["placement"], o["deskBeside"]["left"], o["deskBeside"]["top"]), ("right", 844, 300),
                         "the picker opens beside its item")
        self.assertEqual((o["deskBesideLeft"]["placement"], o["deskBesideLeft"]["left"]), ("left", 1030 - 4 - 280))
        self.assertEqual(o["deskBesideBottom"]["top"], 800 - 8 - 420, "beside an item near the bottom it moves up")
        for k, size in (("deskBelow", menu), ("deskBottom", menu), ("deskRight", menu), ("deskBeside", picker),
                        ("deskBesideLeft", picker), ("deskBesideBottom", picker)):
            with self.subTest(k):
                self.inside(o[k], size, desk)

    def test_phone(self):
        o, phone, menu, picker = self.o, (360, 640), (240, 300), (280, 420)
        self.assertEqual(o["phoneBelow"]["placement"], "below")
        self.assertEqual(o["phoneBottom"]["placement"], "above")
        self.assertEqual(o["phoneBeside"]["placement"], "below", "no room beside at 360px: under the item")
        self.assertEqual(o["phoneBesideLow"]["placement"], "above")
        self.assertEqual(o["phoneWide"]["width"], 360 - 16, "wider than the screen: made to fit")
        for k, size in (("phoneBelow", menu), ("phoneBottom", menu), ("phoneBeside", picker), ("phoneBesideLow", picker),
                        ("phoneWide", (500, 200))):
            with self.subTest(k):
                self.inside(o[k], size, phone)

    def test_scrolled_and_too_tall(self):
        o, desk = self.o, (1280, 800)
        # Its trigger scrolled out of view: the popup still stays in the window.
        self.inside(o["scrolledAway"], (240, 300), desk)
        self.inside(o["scrolledBelow"], (240, 300), desk)
        # Taller than either side: it gets the room there is and scrolls.
        self.assertTrue(o["tooTall"]["maxHeight"])
        self.inside(o["tooTall"], (200, 2000), desk)

    def test_the_visible_part_of_a_phone(self):
        # The keyboard leaves y 120..420 on screen: the picker stays in it.
        k = self.o["keyboard"]
        self.assertGreaterEqual(k["top"], 120 + 8, k)
        self.assertLessEqual(k["top"] + (k["maxHeight"] or 420), 420 - 8, k)
        self.assertTrue(k["maxHeight"], "taller than what is left: it scrolls")
        # Zoomed in and panned: x 300..700 and y 100..600 are on screen.
        z = self.o["zoomed"]
        self.assertEqual((z["placement"], z["left"], z["top"]), ("below", 300 + 8, 236), "kept off the visible left edge")
        self.assertGreaterEqual(z["left"], 300 + 8, z)


class Wiring(unittest.TestCase):
    """The pages hand every item to something that acts on it."""

    def test_the_popout_acts_on_every_item_the_model_can_offer(self):
        ids = set(re.findall(r"\bid: '([\w-]+)'", ACTIONS))
        acts = SESSION[SESSION.index("const ACTS = {"):SESSION.index("\n};\n", SESSION.index("const ACTS = {"))]
        room_only_raw = {"focus", "send", "terminal", "archive", "close", "open"}   # raw sessions and orphans: no pop-out
        for i in ids - room_only_raw:
            self.assertRegex(acts, rf"(?m)^\s+(?:async )?'?{re.escape(i)}'?(?:\(|:)", i)

    def test_the_dashboard_has_a_handler_for_every_action_class(self):
        for cls in set(re.findall(r"cls: '([\w-]+)'", ACTIONS)):
            self.assertIn(f"closest('.{cls}')", INDEX, cls)

    def test_the_popout_sends_dialogs_to_the_dashboard(self):
        self.assertIn("host.postMessage({ type: 'session-action', roomId: ROOM, act }, location.origin);", SESSION)
        self.assertIn("window.open('/?task=' + encodeURIComponent(ROOM) + '&act=' + encodeURIComponent(act), '_blank');", SESSION)
        self.assertIn("if (d && d.type === 'session-action' && d.roomId && ev.origin === location.origin) {", INDEX)
        self.assertIn("const TASK_ACTS = ['agents', 'moveproj', 'makepo', 'delete', 'workspace'];", INDEX)

    def test_the_colour_picker_is_anchored_to_its_item(self):
        self.assertIn("menu._am = SessionActions.child(menu, trigger);", INDEX)
        self.assertIn("menu._am = SessionActions.child(menu, trigger);", SESSION)
        self.assertNotIn("const top = rect.bottom + 4;", INDEX)


if __name__ == "__main__":
    unittest.main()
