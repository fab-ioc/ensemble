"""A task in no project shows its files (#144, the CEO's P102).

A task made with Create and no project runs in a folder of its own under ~/cs
and has no task folder; a past terminal conversation, and a task brought in
from one, runs wherever it was started. Their Workspace (the Files tool, a
phone's Files tab) shows that folder with the components a project's task
has: the tree, the file view, Go to file, Text search, and Changes when it is
a git checkout.

The hub's side (TheHubSide): the file APIs read a folder the task list shows
and nothing outside it; a home folder or a whole drive is too wide to be one
session's folder.

The page (ThePage), in headless Chrome over CDP against a hub in a thread, at
1440 and on a phone (360, 390, 430):

* a task in no project with a .md, a .py and an .html: its tree, each file in
  the file view (Markdown rendered, code highlighted, HTML in its sandbox), Go
  to file, Text search and Changes;
* a task brought in from a terminal conversation, and a past conversation
  (read only), show the folder they ran in;
* an empty folder, a folder that is gone, no folder at all and a folder too
  wide each say so in one line;
* a file in a window of its own (the CEO's P103): the button on the tab in
  front, Shift+Enter on the tab and a file row's menu open the file view as a
  sized window without the browser's bars, as the tab was left (view, line,
  match) and in the hub's theme; one window per file, several files at once;
  from a docked Files tool and from a project's task too; not on a phone.

TheWindowOverTheTailnet: that window is the hub's own page, so the browser's
token cookie opens it and nothing about the token is in its address.

Screenshots go to $ENSEMBLE_SHOTS when it is set. The page tests are skipped
without Node or Chrome.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402
from tests import chrome_profile, test_tool_strip  # noqa: E402
from tests import test_app_install as app_install  # noqa: E402
from tests.test_middle import CHROME, NODE  # noqa: E402

INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
GIT = shutil.which("git")


def patches(base: Path) -> list:
    """A hub whose state, projects, ~/cs, transcripts and home are under ``base``."""
    state = base / "state"
    for d in (state, base / "EnsembleProjects", base / "transcripts", base / "cs", base / "home"):
        d.mkdir(parents=True, exist_ok=True)
    return [
        mock.patch.object(dashboard, "HOME", base / "home"),
        mock.patch.object(dashboard, "PROJECTS_ROOT", base / "EnsembleProjects"),
        mock.patch.object(dashboard, "DASHBOARD_DIR", state),
        mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
        mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
        mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
        mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
        mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
        mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
        mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
        mock.patch.object(dashboard, "load_live", lambda: []),
        mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
        mock.patch.object(dashboard, "_SESSION_FOLDERS", {}),
        mock.patch.object(dashboard, "_SESSION_FOLDER_REAL", {}),
        mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
    ]


def put(path: Path, text: str) -> None:
    """A file with exactly these bytes (no newline translation)."""
    path.write_bytes(text.encode("utf-8"))


def loose_task(title: str) -> dict:
    """A task made with Create and no project, started once."""
    ok, room, err = dashboard.create_task(title, "Write it up.", "", [{"agent": "claude"}])
    assert ok, err
    room["launched"] = True
    chatroom.update_room(room)
    chatroom.post_message(room["id"], "user", "Go on.")
    return chatroom.get_room(room["id"], public=False)


def adopted_task(title: str, cwd: Path | str) -> dict:
    """A task brought in from a terminal conversation: it runs where that was started."""
    room = chatroom.create_room(title, [{"identity": "claude", "agent": "claude", "cwd": str(cwd)}])
    full = chatroom.get_room(room["id"], public=False)
    full.update({"cwd": str(cwd), "mode": "solo", "adopted": True})
    chatroom.update_room(full)
    chatroom.post_message(room["id"], "user", "Carry on.")
    return full


def past_conversation(base: Path, sid: str, cwd: Path) -> None:
    """A terminal conversation's transcript, started in ``cwd``."""
    d = base / "transcripts" / "C--elsewhere"
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "cwd": str(cwd), "sessionId": sid, "timestamp": "2026-09-29T10:00:00Z",
              "message": {"role": "user", "content": "Look at the old numbers"}},
             {"type": "assistant", "cwd": str(cwd), "sessionId": sid, "timestamp": "2026-09-29T10:00:05Z",
              "message": {"role": "assistant", "content": [{"type": "text", "text": "They are in notes.md."}]}}]
    (d / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def listed() -> list[dict]:
    """The task list as the page reads it, loaded afresh."""
    dashboard.invalidate_session_listing()
    return dashboard.load_sessions(300)


def link_dir(target: Path, link: Path) -> bool:
    """``link`` as a folder that is really ``target``; False where neither a
    symbolic link nor a junction can be made."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return True
        except OSError:
            pass
    return False


class TheHubSide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ens-loose-", ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        self.patches = patches(self.base)
        for p in self.patches:
            p.start()
        dashboard.invalidate_session_listing()
        self.else_ = self.base / "elsewhere"
        self.else_.mkdir()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        dashboard.invalidate_session_listing()
        self.tmp.cleanup()

    def names(self, path) -> list[str]:
        code, res = dashboard.list_dir(str(path))
        self.assertEqual(code, 200, res)
        return [e["name"] for e in res["entries"]]

    def refused(self, path):
        for what, (code, res) in (("dir", dashboard.list_dir(str(path))), ("file", dashboard.read_workspace_file(str(path))),
                                  ("files", dashboard.ws_files(str(path))), ("roots", dashboard.git_roots(str(path))),
                                  ("status", dashboard.git_status(str(path)))):
            self.assertEqual((code, res.get("error")), (403, "path_not_allowed"), f"{what} of {path}")

    def test_a_task_made_without_a_project_has_a_folder_but_no_task_folder(self):
        room = loose_task("Write the report")
        cwd = Path(room["cwd"])
        self.assertEqual(cwd.parent, self.base / "cs", "it runs in a folder of its own under ~/cs")
        self.assertEqual(room.get("taskDir"), "", "and has no task folder")
        self.assertEqual(room.get("projectId"), "")
        row = next(r for r in listed() if r.get("roomId") == room["id"])
        self.assertEqual((row["cwd"], row["taskDir"]), (str(cwd), ""))
        self.assertNotIn("folderWide", row)
        group = next(g for g in dashboard.build_projects()["projects"] if any(s.get("roomId") == room["id"] for s in g["sessions"]))
        self.assertEqual((group["id"], group["registered"]), ("__unassigned__", False), "the page finds it in Unassigned")

    def test_its_files_are_read_where_it_runs(self):
        cwd = Path(loose_task("Write the report")["cwd"])
        put(cwd / "README.md", "# Report\n")
        (cwd / "notes").mkdir()
        put(cwd / "notes" / "deep.txt", "deep\n")
        self.assertIn("README.md", self.names(cwd))
        self.assertEqual(self.names(cwd / "notes"), ["deep.txt"])
        code, res = dashboard.read_workspace_file(str(cwd / "README.md"))
        self.assertEqual((code, res.get("text")), (200, "# Report\n"))
        code, res = dashboard.ws_files(str(cwd))
        self.assertEqual(code, 200, res)
        self.assertIn("notes/deep.txt", res["files"])

    def test_dots_do_not_walk_out_of_it(self):
        cwd = Path(loose_task("Write the report")["cwd"])
        put(self.base / "secret.txt", "no\n")
        listed()
        self.refused(cwd / ".." / "..")
        self.refused(cwd / ".." / ".." / "secret.txt")
        self.refused(cwd / "notes" / ".." / ".." / ".." / "state")
        self.assertIsNone(dashboard.resolve_file_at("w/" + "/".join(str(self.base / "secret.txt").replace("\\", "/").split("/")))
                          if os.name == "nt" else dashboard.resolve_file_at("p" + str(self.base / "secret.txt")))

    def test_a_task_brought_in_from_a_conversation_is_read_once_it_is_listed(self):
        work = self.else_ / "work"
        (work / "src").mkdir(parents=True)
        put(work / "plan.md", "# Plan\n")
        put(work / "src" / "tool.py", "print('hi')\n")
        room = adopted_task("Old work", work)
        self.refused(work)                       # no sessions load yet: nothing says it is a session's folder
        row = next(r for r in listed() if r.get("roomId") == room["id"])
        self.assertNotIn("folderWide", row)
        self.assertEqual(self.names(work), ["src", "plan.md"])
        self.assertEqual(self.names(work / "src"), ["tool.py"])
        self.assertEqual(dashboard.read_workspace_file(str(work / "plan.md"))[1].get("text"), "# Plan\n")
        self.assertEqual(sorted(dashboard.ws_files(str(work))[1]["files"]), ["plan.md", "src/tool.py"])
        self.assertEqual(dashboard.git_roots(str(work))[0], 200)
        code, res = dashboard.ws_search(str(work), "hi")
        self.assertEqual(code, 200, res)
        self.assertEqual([f["path"] for f in res["files"]], ["src/tool.py"])
        # A rendered page's own picture or stylesheet comes from its folder too.
        tail = ("w/" + str(work / "plan.md").replace("\\", "/")) if os.name == "nt" else "p" + str(work / "plan.md")
        self.assertEqual(dashboard.resolve_file_at(tail), (work / "plan.md").resolve())

    def test_nothing_outside_its_folder_is_read(self):
        work = self.else_ / "work"
        work.mkdir()
        (self.else_ / "work2").mkdir()            # a name that only starts the same
        put(self.else_ / "work2" / "x.txt", "x\n")
        put(self.else_ / "secret.txt", "no\n")
        adopted_task("Old work", work)
        listed()
        self.assertEqual(dashboard.list_dir(str(work))[0], 200)
        for out in (self.else_, work / "..", work / ".." / "secret.txt", self.else_ / "work2", self.else_ / "work2" / "x.txt",
                    work / ".." / "work2", self.base, self.base / "state"):
            self.refused(out)
        self.assertEqual(dashboard.list_dir(str(work))[1]["parent"], "", "no way up is offered")

    def test_a_link_out_of_its_folder_is_not_followed(self):
        work = self.else_ / "work"
        work.mkdir()
        private = self.else_ / "private"
        private.mkdir()
        put(private / "key.txt", "no\n")
        if not link_dir(private, work / "out"):
            self.skipTest("no links here")
        adopted_task("Old work", work)
        listed()
        self.assertEqual(dashboard.list_dir(str(work))[0], 200)
        self.refused(work / "out")
        self.refused(work / "out" / "key.txt")

    def test_a_past_conversation_s_folder_is_read_too(self):
        old = self.else_ / "old"
        old.mkdir()
        put(old / "notes.md", "# Old numbers\n")
        past_conversation(self.base, "11111111-2222-4333-8444-555555555555", old)
        self.refused(old)
        row = next(r for r in listed() if r["sessionId"] == "11111111-2222-4333-8444-555555555555")
        self.assertEqual(row["cwd"], str(old))
        self.assertFalse(row.get("headless"), "a conversation, not a task")
        self.assertEqual(self.names(old), ["notes.md"])
        self.refused(self.else_)

    def test_a_home_folder_or_a_drive_is_too_wide(self):
        home = self.base / "home"
        (home / "sub").mkdir()
        room = adopted_task("Started at home", home)
        above = adopted_task("Started above home", self.base)
        inside = adopted_task("Started inside home", home / "sub")
        rows = {r.get("roomId"): r for r in listed()}
        self.assertIs(rows[room["id"]].get("folderWide"), True, "the row says why it shows no files")
        self.assertIs(rows[above["id"]].get("folderWide"), True)
        self.assertNotIn("folderWide", rows[inside["id"]])
        self.refused(home)
        self.refused(self.base)
        self.assertEqual(dashboard.list_dir(str(home / "sub"))[0], 200, "a folder inside home is one session's own")
        self.assertEqual(dashboard._session_folder_real(os.path.abspath(os.sep)), "", "a whole drive")
        self.assertEqual(dashboard._session_folder_real(str(home.parent)), "", "above home")
        self.assertEqual(dashboard._session_folder_real("relative/folder"), "" if not os.path.isabs(os.path.realpath("relative/folder")) else
                         dashboard._session_folder_real(os.path.realpath("relative/folder")))

    def test_a_folder_is_read_only_while_its_session_is_listed(self):
        work = self.else_ / "work"
        work.mkdir()
        room = adopted_task("Old work", work)
        listed()
        self.assertEqual(dashboard.list_dir(str(work))[0], 200)
        chatroom.delete_room(room["id"])
        listed()
        self.refused(work)

    def test_a_project_s_task_is_as_before(self):
        ok, proj, _ = dashboard.register_project("Motors")
        self.assertTrue(ok, proj)
        ok, room, err = dashboard.create_task("Quiet brakes", "spec", proj["id"], [{"agent": "claude"}])
        self.assertTrue(ok, err)
        row = next(r for r in listed() if r.get("roomId") == room["id"])
        self.assertTrue(row["taskDir"])
        self.assertNotIn("folderWide", row)
        self.assertEqual(dashboard.list_dir(row["taskDir"])[0], 200)

    def test_a_folder_is_resolved_once_a_minute_not_on_every_load(self):
        work = self.else_ / "work"
        work.mkdir()
        adopted_task("Old work", work)
        real = dashboard._session_folder_real
        asked = []
        with mock.patch.object(dashboard, "_session_folder_real", lambda f: (asked.append(f), real(f))[1]):
            listed()
            first = len(asked)
            listed()
            listed()
            self.assertGreater(first, 0)
            self.assertEqual(len(asked), first, "the task list loads every two seconds: nothing is resolved again")
            with mock.patch.object(dashboard, "_SESSION_FOLDER_TTL", 0.0):
                listed()
            self.assertEqual(len(asked), 2 * first, "and again once it is old")
        self.assertEqual(dashboard.list_dir(str(work))[0], 200)

    def test_nothing_is_written_through_a_session_s_folder(self):
        # The file operations (upload, mkdir, move, delete) are a documents
        # project's: a session's folder gives them no way in.
        work = self.else_ / "work"
        work.mkdir()
        adopted_task("Old work", work)
        listed()
        with self.assertRaises(dashboard.FileOpRefused) as e:
            dashboard.files_target("")
        self.assertEqual(e.exception.status, 404)


class ThePageCode(unittest.TestCase):
    """What the page does with a row in no project (static checks)."""

    def test_the_tree_is_the_one_component(self):
        roots = INDEX[INDEX.index("function wsRoots(ctx) {"):INDEX.index("// At task scope the project root is filtered")]
        self.assertIn("if (wsLoose(r)) {", roots)
        self.assertIn("push(wsLooseFolder(r), r.headless ? 'This task' : 'This conversation', 'task');", roots)
        self.assertEqual(INDEX.count("function wsMount("), 1, "one tree and viewer, no second copy")
        self.assertEqual(INDEX.count("function wsPanelHtml("), 1)

    def test_a_documents_project_s_row_menu_offers_the_window_too(self):
        menu = INDEX[INDEX.index("function docsMenuOpen(menu, row, rect) {"):INDEX.index("function docsMenuPlace(menu, r) {")]
        self.assertIn("(dir || isPhone() ? '' : item('window', 'Open in new window'))", menu, "a file, and not on a phone")
        self.assertIn("else if (act === 'window') wsOpenWindow(v, c.abs);", INDEX)

    def test_a_phone_hides_the_tab_s_window_button(self):
        phone = INDEX[INDEX.index("/* File tabs: a finger-sized tab and close target"):INDEX.index("/* The path above the file: every crumb")]
        self.assertIn(".wst-win { display: none; }", phone)
        self.assertIn("if (!path || isPhone()) return null;", INDEX)
        self.assertIn("const f = !v.ctx.docs && !isPhone() && e.target.closest('.wse.file[data-path]');", INDEX)

    def test_no_folder_says_why(self):
        self.assertIn("const why = issueEmpty(wsNoFolderWhy(r));", INDEX)
        why = INDEX[INDEX.index("function wsNoFolderWhy(r) {"):INDEX.index("// The roots this context is allowed to see")]
        for line in ("too wide to show as its own folder.", "has no folder.", "'Loading…'"):
            self.assertIn(line, why)


# The launcher (through chrome_profile) and the CDP client, as test_tool_strip has them.
class TheWindowOverTheTailnet(unittest.TestCase):
    """A file's own window (P103) is the hub's /fileview page, asked for by the
    browser that already holds the token's cookie: over the tailnet it opens
    signed in, with no token in its address."""

    def test_the_window_opens_with_the_cookie_and_not_without(self):
        token = app_install.TOKEN
        remote = {**app_install.TheGate.PROXIED, **app_install.TheGate.NAV}
        file = quote(r"C:\work\a b.md", safe="")
        path = "/fileview?path=" + file + "&room=room-1&st=" + quote('{"view":"source","marks":{"source":3}}', safe="")
        with mock.patch.object(dashboard, "ACCESS_TOKEN", token), mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None):
            hub = app_install.Hub()
            try:
                code, h, body = hub.get(path, {**remote, "Cookie": f"ensemble_token={token}"})
                self.assertEqual(code, 200)
                self.assertIn(b'<main id="main">', body, "the file view's page")
                code, h, body = hub.get(path, remote)
                self.assertEqual(code, 401, "another browser is asked for the token")
                self.assertIn(b'name="token"', body)
                self.assertEqual(hub.get("/api/ws/file?path=" + file, app_install.TheGate.PROXIED)[0], 401, "and so is what it reads")
            finally:
                hub.close()

    def test_the_page_puts_no_token_in_the_window_s_address(self):
        win = INDEX[INDEX.index("function wsOpenWindow(v, path) {"):INDEX.index("// A file row's menu in a Workspace")]
        self.assertIn("const url = wsFileUrl(v, path) + (t ? '&st=' + encodeURIComponent(JSON.stringify(t.st)) : '');", win)
        self.assertNotIn("token", win)
        self.assertNotIn("token", INDEX[INDEX.index("function wsFileUrl(v, path) {"):INDEX.index("const wsTabName")])


CDP_JS = test_tool_strip.CDP_JS[:test_tool_strip.CDP_JS.index("// The strip, the tool open beside the middle")] + r"""
// The Files pane of the open task, wherever it is (the tool strip's Files, a phone's Files tab).
const FILES = `(() => {
  const box = e => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height), r: Math.round(b.right), b: Math.round(b.bottom) }; };
  const vis = e => !!e && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0 && getComputedStyle(e).visibility !== 'hidden';
  const pane = document.getElementById('detail-panel')._panes.workspace, wsp = pane.querySelector('.wsp');
  const v = WS_VIEWS.get('task:' + SELECTED_SID);
  const small = sel => [...pane.querySelectorAll(sel)].filter(vis).map(e => [sel, e.textContent.trim().slice(0, 24), Math.round(e.getBoundingClientRect().width), Math.round(e.getBoundingClientRect().height)])
    .filter(([, , w, h]) => w < 44 - 0.5 || h < 44 - 0.5);
  return { vw: innerWidth, vh: innerHeight, scrollW: document.documentElement.scrollWidth, scrollH: document.documentElement.scrollHeight,
    pane: box(pane), paneShown: vis(pane), mounted: !!wsp, empty: (pane.querySelector('.dp-empty') || {}).textContent || '',
    roots: [...pane.querySelectorAll('.wse.wsroot')].map(e => [e.querySelector('.nm').textContent, e.querySelector('.wsroot-kind').textContent, e.title]),
    files: [...pane.querySelectorAll('.wsp-tree .wse.file .nm')].map(e => e.textContent),
    dirs: [...pane.querySelectorAll('.wsp-tree .wse.dir:not(.wsroot) .nm')].map(e => e.textContent),
    notes: [...pane.querySelectorAll('.wsp-tree .wse.none')].map(e => e.textContent),
    noteBoxes: [...pane.querySelectorAll('.wsp-tree .wse.none')].map(e => [Math.round(e.getBoundingClientRect().width), e.scrollWidth, Math.round(e.parentElement.getBoundingClientRect().width)]),
    tabs: [...pane.querySelectorAll('.wst-tab')].map(e => [e.querySelector('.wst-nm').textContent, e.classList.contains('on')]),
    wins: [...pane.querySelectorAll('.wst-tab')].map(e => { const w = e.querySelector('.wst-win'); return [e.querySelector('.wst-nm').textContent, !!w, vis(w), w ? w.title : '', w ? box(w) : null, box(e.querySelector('.wst-x'))]; }),
    crumbs: [...pane.querySelectorAll('.wsc-list li')].map(e => e.textContent.trim()),
    placeholder: wsp ? wsp.querySelector('.wsf-q').placeholder : '',
    results: [...pane.querySelectorAll('.wsf-res .wsr')].map(e => e.textContent.replace(/\\s+/g, ' ').trim()),
    state: wsp ? wsp.querySelector('.wsf-state').textContent : '',
    treePane: wsp ? wsp.classList.contains('ws-tree') : null,
    tree: wsp ? box(wsp.querySelector('.wsp-side')) : null, view: wsp ? box(wsp.querySelector('.wsp-view')) : null,
    treeShown: wsp ? vis(wsp.querySelector('.wsp-side')) : null, viewShown: wsp ? vis(wsp.querySelector('.wsp-view')) : null,
    frame: wsp ? box(wsp.querySelector('iframe.wsp-frame.on')) : null,
    rootsOf: v ? v.roots.map(r => [r.label, r.kind]) : null,
    qFont: wsp ? parseFloat(getComputedStyle(wsp.querySelector('.wsf-q')).fontSize) : 0,
    small: [].concat(small('.wsp-tree .wse:not(.none)'), small('.wsf-mode button'), small('.wsf-q'), small('.wst-tab'), small('.wsp-bar button'), small('.wsf-res .wsr')),
    trail: [...document.querySelectorAll('#bar-crumbs [data-crumb]')].filter(vis).map(e => [e.dataset.crumb, e.textContent]) };
})()`;
// The file showing, as its viewer drew it.
const VIEWER = `(() => {
  const pane = document.getElementById('detail-panel')._panes.workspace;
  const f = pane.querySelector('iframe.wsp-frame.on'); if (!f) return null;
  const d = f.contentDocument, main = d.getElementById('main');
  const page = main.querySelector('iframe');
  return { url: f.contentWindow.location.pathname + f.contentWindow.location.search, title: d.title,
    h1: (main.querySelector('h1') || {}).textContent || '', bold: !!main.querySelector('strong'),
    tokens: [...new Set([...main.querySelectorAll('[class^="tk-"], [class*=" tk-"]')].map(e => e.className))].sort(),
    lines: main.querySelectorAll('[data-n]').length,
    page: page ? { sandbox: page.getAttribute('sandbox'), srcdoc: (page.getAttribute('srcdoc') || '').length > 0,
      h1: (() => { try { return page.contentDocument.querySelector('h1').textContent; } catch (e) { return 'unreadable'; } })(),
      ran: (() => { try { return page.contentDocument.title; } catch (e) { return 'unreadable'; } })() } : null,
    err: (main.querySelector('.err') || {}).textContent || '', scrollW: d.documentElement.scrollWidth, vw: f.contentWindow.innerWidth };
})()`;
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const page = async (w, h, mobile) => {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
    await c.send('Runtime.enable', {}, sessionId);
    await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: mobile ? 3 : 1, mobile: !!mobile }, sessionId);
    if (mobile) await c.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }, sessionId);
    const evalIn = async (expr) => { const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId); if (r.exceptionDetails) throw new Error(expr.slice(0, 120) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600)); return r.result.value; };
    const until = async (expr, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = await evalIn(expr); } catch (e) {} if (v) return v; await sleep(150); } throw new Error('timeout: ' + expr); };
    const shot = async (name) => { if (!A.shots) return; const r = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId); fs.writeFileSync(path.join(A.shots, name + '.png'), Buffer.from(r.data, 'base64')); };
    const click = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 }, sessionId);
    };
    const type = text => c.send('Input.insertText', { text }, sessionId);
    const key = async (k, code, vk) => { for (const t of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type: t, key: k, code, windowsVirtualKeyCode: vk }, sessionId); };
    await c.send('Page.addScriptToEvaluateOnNewDocument', { source: 'window.ensBootOpen = false;' }, sessionId);
    await c.send('Page.navigate', { url: A.base + '/' }, sessionId);
    await until('typeof PROJECTS !== "undefined" && !!PROJECTS && PROJECTS.projects.length > 1 && ALL_ROWS.some(r => r.roomId === ' + JSON.stringify(A.loose) + ')', 30000);
    await until('window.ensBooted === true', 30000);
    try { await evalIn(`Object.keys(localStorage).filter(k => k.startsWith('cd-ws:') || ['cd-tool-strip', 'cd-tool-open', 'cd-phone-tabs'].includes(k)).forEach(k => localStorage.removeItem(k)); 0`); } catch (e) {}
    // What the page asks window.open for, and the windows it gets.
    await evalIn(`window.__wo = []; (() => { const o = window.open.bind(window); window.open = (u, n, f) => { const w = o(u, n, f); window.__wo.push([u, n, f, !!w]); return w; }; })(); 0`);
    const rclick = async (sel) => {
      const [x, y] = await evalIn(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) throw new Error('no ' + ${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x, y, button: 'right', clickCount: 1 }, sessionId);
    };
    const skey = async (k, code, vk) => { for (const t of ['keyDown', 'keyUp']) await c.send('Input.dispatchKeyEvent', { type: t, key: k, code, windowsVirtualKeyCode: vk, modifiers: 8 }, sessionId); };
    return { evalIn, until, shot, click, rclick, type, key, skey, sessionId, targetId, close: () => c.send('Target.closeTarget', { targetId }) };
  };
  const PANE = `document.getElementById('detail-panel')._panes.workspace`;
  // The file windows open now, and one of them as its page drew it.
  const wins = async () => (await c.send('Target.getTargets')).targetInfos.filter(t => t.type === 'page' && t.url.includes('/fileview?'));
  const WIN = `(() => {
    const main = document.getElementById('main'), page = main.querySelector('iframe');
    if (document.readyState !== 'complete' || main.querySelector(':scope > .err')) return null;
    return { name: window.name, path: new URLSearchParams(location.search).get('path'), st: new URLSearchParams(location.search).get('st'),
      room: new URLSearchParams(location.search).get('room'), top: window.parent === window, opener: !!window.opener,
      w: innerWidth, h: innerHeight, outer: [outerWidth, outerHeight], bars: [menubar.visible, toolbar.visible, locationbar.visible],
      theme: document.documentElement.dataset.theme || '', title: document.title,
      h1: (main.querySelector('h1') || {}).textContent || '', tokens: main.querySelectorAll('[class^="tk-"], [class*=" tk-"]').length,
      marked: [...main.querySelectorAll('.ln-row.is-marked')].map(e => e.dataset.n), hit: (main.querySelector('mark.fv-hit') || {}).textContent || '',
      view: (main.querySelector('.ln-row') ? 'source' : 'preview'),
      page: page ? { sandbox: page.getAttribute('sandbox'), h1: (() => { try { return page.contentDocument.querySelector('h1').textContent; } catch (e) { return 'unreadable'; } })() } : null };
  })()`;
  const inWin = async (t, ms = 20000) => {
    const { sessionId } = await c.send('Target.attachToTarget', { targetId: t.targetId, flatten: true });
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      try { const r = await c.send('Runtime.evaluate', { expression: WIN, returnByValue: true }, sessionId); if (r.result && r.result.value) return { ...r.result.value, sessionId }; } catch (e) {}
      await sleep(200);
    }
    throw new Error('the window did not draw: ' + t.url);
  };
  // The window a click or a key opened: the one that was not there before.
  const opened = async (before, ms = 10000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) {
      const now = await wins(), t = now.find(x => !before.some(b => b.targetId === x.targetId));
      if (t) return t;
      await sleep(150);
    }
    return null;
  };
  const tool = id => `#po-dock .dk-strip-btn[data-dk-auto="${id}"]`;
  const tab = name => `[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].find(e => e.textContent.trim().startsWith(${JSON.stringify(name)}))`;
  const open = async (p, sid) => {
    await p.evalIn(`openDetail(${JSON.stringify(sid)}); 0`);
    await p.until(`document.body.classList.contains("dp-docked") && SELECTED_SID === ${JSON.stringify(sid)} && document.getElementById('detail-panel').dataset.shell === ${JSON.stringify(sid)}`, 20000);
    await sleep(300);
  };
  // Files on screen: the strip's tool on a desktop, the Files tab on a phone.
  const files = async (p, phone) => {
    if (phone) await p.evalIn(`${tab('Files')}.click(); 0`);
    else if (await p.evalIn('PD.dock.flyOpen()') !== 'workspace') await p.click(tool('workspace'));
    await p.until(`(() => { const x = ${PANE}; return !!x && x.getBoundingClientRect().width > 0 && (!!x.querySelector('.wsp') || !!x.querySelector('.dp-empty')); })()`, 15000);
  };
  const file = name => `${PANE}.querySelector('.wsp-tree .wse.file[data-path$=${JSON.stringify(JSON.stringify(name)).slice(1, -1)}]')`;
  const shown = (p, name) => p.until(`(() => { const f = ${PANE}.querySelector('iframe.wsp-frame.on'); try {
    return !!f && f.contentDocument.readyState === 'complete' && f.contentWindow.location.pathname === '/fileview'
      && decodeURIComponent(f.contentWindow.location.search).includes(${JSON.stringify(name)}) && !f.contentDocument.querySelector('#main > .err'); } catch (e) { return false; } })()`, 20000);
  const view = async (p, name) => {
    await p.until(`!!${file(name)}`, 15000);
    await p.evalIn(`${file(name)}.click(); 0`);
    await shown(p, name); await sleep(500);
    return { files: await p.evalIn(FILES), viewer: await p.evalIn(VIEWER) };
  };
  try {
    // ---- a desktop
    {
      const p = await page(1440, 900);
      await open(p, A.loose);
      out.before = await p.evalIn(`({ strip: [...document.querySelectorAll('#po-dock .dk-strip-right .dk-strip-btn')].map(b => b.getAttribute('aria-label')), docked: document.body.classList.contains('dp-docked') })`);
      await files(p);
      await p.until(`!!${file('README.md')}`, 15000);
      await sleep(300);
      out.tree = await p.evalIn(FILES);
      await p.shot('loose-1440-files');
      out.md = await view(p, 'README.md');
      await p.shot('loose-1440-md');
      out.py = await view(p, 'tool.py');
      await p.shot('loose-1440-py');
      out.html = await view(p, 'page.html');
      await p.shot('loose-1440-html');
      // A folder opens in place.
      await p.evalIn(`${PANE}.querySelector('.wsp-tree .wse.dir:not(.wsroot)[data-path$="notes"]').click(); 0`);
      await p.until(`!!${file('deep.txt')}`, 10000);
      out.folder = await p.evalIn(FILES);
      // Go to file.
      await p.click('#po-dock .wsf-q'); await sleep(150);
      await p.type('tool');
      await p.until(`${PANE}.querySelectorAll('.wsf-res .wsr').length > 0`, 15000); await sleep(300);
      out.goto = await p.evalIn(FILES);
      await p.shot('loose-1440-goto');
      await p.key('Enter', 'Enter', 13);
      await shown(p, 'tool.py'); await sleep(300);
      out.gotoOpened = await p.evalIn(FILES);
      // Text search.
      await p.evalIn(`${PANE}.querySelector('.wsf-mode button[data-mode="text"]').click(); 0`);
      await p.click('#po-dock .wsf-q'); await sleep(150);
      await p.evalIn(`(() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); })(); 0`);
      await p.type('squeal');
      await p.until(`${PANE}.querySelectorAll('.wsf-res .wsr').length > 1`, 20000); await sleep(300);
      out.text = await p.evalIn(FILES);
      await p.shot('loose-1440-text');
      await p.evalIn(`(() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); ${PANE}.querySelector('.wsf-mode button[data-mode="files"]').click(); })(); 0`);
      // ---- a file in a window of its own (P103)
      {
        const W = out.windows = {};
        // The hub's theme, so the window shows it is the viewer's own.
        await p.evalIn(`(async () => { localStorage.setItem('cd-theme', 'dark'); await cdTheme.put({ theme: 'dark' }); cdTheme.apply(); })()`);
        W.tabs = (await p.evalIn(FILES)).wins;
        // Its size and place on a large screen, a small one, and with no place known.
        W.feat = await p.evalIn(`[wsWinFeatures({ screen: { availWidth: 2560, availHeight: 1440 }, screenX: 100, screenY: 50 }, 0),
          wsWinFeatures({ screen: { availWidth: 2560, availHeight: 1440 }, screenX: 100, screenY: 50 }, 2),
          wsWinFeatures({ screen: { availWidth: 900, availHeight: 700 }, screenX: -1600, screenY: 0 }, 0), wsWinFeatures({}, 0),
          wsWinName('C:\\\\a\\\\b.md') === wsWinName('c:/a/b.md'), wsWinName('C:\\\\a\\\\b.md') !== wsWinName('C:\\\\a\\\\c.md')]`);
        // The tab in front (tool.py, left by Go to file): its button, a real click.
        let before = await wins();
        await p.click('#po-dock .wst-tab.on .wst-win');
        let t = await opened(before);
        W.py = t ? await inWin(t) : null;
        W.asked = await p.evalIn('window.__wo.slice()');
        if (A.shots && W.py) { const r = await c.send('Page.captureScreenshot', { format: 'png' }, W.py.sessionId); fs.writeFileSync(path.join(A.shots, 'window-py.png'), Buffer.from(r.data, 'base64')); }
        // A search result opens README.md at its line; Shift+Enter on its tab: a second window, at that line.
        await p.evalIn(`${PANE}.querySelector('.wsf-mode button[data-mode="text"]').click(); (() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = 'squeal'; q.dispatchEvent(new Event('input', { bubbles: true })); })(); 0`);
        await p.until(`${PANE}.querySelectorAll('.wsf-res .wsr').length > 1`, 20000); await sleep(300);
        await p.evalIn(`[...${PANE}.querySelectorAll('.wsf-res .wsr')].find(e => e.textContent.includes('brakes **squeal**')).click(); 0`);
        await shown(p, 'README.md'); await sleep(600);
        await p.evalIn(`${PANE}.querySelector('.wst-tab.on').focus(); 0`);
        before = await wins();
        await p.skey('Enter', 'Enter', 13);
        t = await opened(before);
        W.md = t ? await inWin(t) : null;
        W.two = (await wins()).length;
        // The same file asked for again: its window, not a third.
        before = await wins();
        await p.click('#po-dock .wst-tab.on .wst-win'); await sleep(800);
        W.again = { count: (await wins()).length, names: (await p.evalIn('window.__wo.map(a => a[1])')) };
        await p.evalIn(`(() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); ${PANE}.querySelector('.wsf-mode button[data-mode="files"]').click(); })(); 0`);
        await sleep(300);
        // A file row's menu (a right click): Open, Open in new window.
        await p.until(`!!${file('page.html')}`, 10000);
        await p.rclick('#po-dock .wsp-tree .wse.file[data-path$="page.html"]'); await sleep(300);
        W.menu = await p.evalIn(`(() => { const m = ${PANE}.querySelector('.dcm'); if (!m) return null; const b = m.getBoundingClientRect();
          return { head: m.querySelector('.dcm-head').textContent, items: [...m.querySelectorAll('.dcm-item')].map(e => e.textContent), role: m.getAttribute('role'),
            inView: b.left >= 0 && b.top >= 0 && b.right <= innerWidth && b.bottom <= innerHeight, focus: document.activeElement.textContent, fly: PD.dock.flyOpen() }; })()`);
        await p.shot('window-row-menu');
        before = await wins();
        await p.click('#po-dock .dcm .dcm-item[data-act="window"]');
        t = await opened(before);
        W.html = t ? await inWin(t) : null;
        W.menuGone = await p.evalIn(`!${PANE}.querySelector('.dcm')`);
        W.three = (await wins()).length;
        // Escape closes the menu without opening anything.
        await p.rclick('#po-dock .wsp-tree .wse.file[data-path$="page.html"]'); await sleep(200);
        await p.key('Escape', 'Escape', 27); await sleep(200);
        W.escaped = await p.evalIn(`({ gone: !${PANE}.querySelector('.dcm'), fly: PD.dock.flyOpen() })`);
        // Files docked beside the conversation (Dock Pinned): the same button.
        await p.click('#po-dock .dk-flyout.open [data-dk-act="menu"]');
        await p.click('.dk-menu.dk-options [data-dk-sub="mode"]');
        await p.click('.dk-menu.dk-submenu [data-dk-menu="mode:pinned"]'); await sleep(600);
        await p.evalIn(`${file('deep.txt')}.click(); 0`);
        await shown(p, 'deep.txt'); await sleep(400);
        before = await wins();
        await p.click('#po-dock .wst-tab.on .wst-win');
        t = await opened(before);
        W.pinned = { mode: await p.evalIn(`PD.dock.viewMode('workspace')`), win: t ? await inWin(t) : null };
        // A blocked window says so.
        W.blocked = await p.evalIn(`(() => { const o = window.open; window.open = () => null; let r; try { r = wsOpenWindow(WS_VIEWS.get('task:' + SELECTED_SID), WS_VIEWS.get('task:' + SELECTED_SID).sel); } finally { window.open = o; }
          return { got: r, toast: ((document.getElementById("status") || {}).textContent || "") }; })()`);
        for (const x of await wins()) await c.send('Target.closeTarget', { targetId: x.targetId });
        await p.evalIn(`PD.dock.reset(); 0`); await sleep(400);
        await p.evalIn(`(async () => { localStorage.setItem('cd-theme', 'light'); await cdTheme.put({ theme: 'light' }); cdTheme.apply(); })()`);
      }
      // Changes: the folder is a git checkout.
      if (A.git) {
        await p.click(tool('changes'));
        await p.until(`(() => { const x = document.getElementById('detail-panel')._panes.changes; return x.querySelectorAll('.tch-files .chf[data-file]').length > 0; })()`, 20000);
        await sleep(300);
        out.changes = await p.evalIn(`(() => { const x = document.getElementById('detail-panel')._panes.changes;
          return { files: [...x.querySelectorAll('.tch-files .chf[data-file]')].map(e => [e.querySelector('.chst').textContent, e.dataset.file]), head: x.querySelector('.tch-head').textContent.replace(/\\s+/g, ' ').trim(),
            fly: PD.dock.flyOpen(), scrollW: document.documentElement.scrollWidth, vw: innerWidth }; })()`);
        await p.evalIn(`document.getElementById('detail-panel')._panes.changes.querySelector('.tch-files .chf[data-file="tool.py"]').click(); 0`);
        await p.until(`!!document.getElementById('detail-panel')._panes.changes.querySelector('.tch-diff .dr-line, .tch-diff [data-n], .tch-diff [data-o]')`, 15000).catch(() => null);
        out.diff = await p.evalIn(`document.getElementById('detail-panel')._panes.changes.querySelector('.tch-diff').innerText.slice(0, 400)`);
        await p.shot('loose-1440-changes');
      }
      // The other folders: each opened, its Files read.
      out.others = {};
      for (const k of ['empty', 'gone', 'none', 'wide', 'adopted', 'past']) {
        await open(p, A[k]);
        await files(p);
        if (k === 'adopted' || k === 'past') await p.until(`!!${file(k === 'past' ? 'notes.md' : 'plan.md')}`, 15000);
        else if (k === 'empty' || k === 'gone') await p.until(`${PANE}.querySelectorAll('.wsp-tree .wse.none').length > 0 && !${PANE}.querySelector('.wsp-tree .wse.none').textContent.includes('loading')`, 15000);
        await sleep(400);
        out.others[k] = await p.evalIn(FILES);
        await p.shot('loose-1440-' + k);
      }
      // A past conversation's file opens in the same viewer, with no chat to comment into.
      out.pastFile = await view(p, 'notes.md');
      // A project's task is as before: its folder, then its project's.
      await open(p, A.projTask);
      await files(p);
      await p.until(`${PANE}.querySelectorAll('.wse.wsroot').length > 1`, 15000); await sleep(300);
      out.projTask = await p.evalIn(FILES);
      {
        await p.evalIn(`${file('task.json')}.click(); 0`);
        await shown(p, 'task.json'); await sleep(400);
        const before = await wins();
        await p.click('#po-dock .wst-tab.on .wst-win');
        const t = await opened(before);
        out.projWindow = t ? await inWin(t) : null;
        for (const x of await wins()) await c.send('Target.closeTarget', { targetId: x.targetId });
      }
      await p.close();
    }
    // ---- a phone
    for (const [w, h] of [[360, 740], [390, 844], [430, 932]]) {
      const p = await page(w, h, true);
      const o = out['phone' + w] = {};
      await p.evalIn(`(() => { const d = document.querySelector('#sw-list details[data-group="unassigned"]'); if (d) d.open = true; return 0; })()`);
      await sleep(200);
      await p.evalIn(`document.querySelector('#sw-list [data-group="unassigned"] .sw-row[data-room="${A.loose}"]').click(); 0`);
      await p.until('document.body.classList.contains("dp-docked") && !!document.querySelector("#detail-panel iframe.dp-session") && document.querySelectorAll("#po-dock .dk-tab").length > 0', 30000);
      await sleep(500);
      o.tabs = await p.evalIn(`[...document.querySelectorAll('#po-dock .dk-stack .dk-tab')].filter(e => e.getBoundingClientRect().width > 0).map(e => e.textContent.trim())`);
      await files(p, true);
      await p.until(`!!${file('README.md')}`, 15000); await sleep(400);
      o.tree = await p.evalIn(FILES);
      await p.shot(`loose-${w}-files`);
      o.md = await view(p, 'README.md');
      await p.shot(`loose-${w}-md`);
      // No window of its own on a phone: no button, no menu, and the function declines.
      o.noWindow = await p.evalIn(`(() => { const v = WS_VIEWS.get('task:' + SELECTED_SID);
        const row = ${PANE}.querySelector('.wsp-tree .wse.file');
        row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 50, clientY: 300 }));
        return { got: wsOpenWindow(v, v.sel), asked: window.__wo.length, menu: !!${PANE}.querySelector('.dcm'), own: !!${PANE}.querySelector('.wsp-own') && ${PANE}.querySelector('.wsp-own').getBoundingClientRect().width > 0 }; })()`);
      // Files, first in the path bar: back to the tree; then the code and the page.
      await p.evalIn(`${PANE}.querySelector('.wsp-files').click(); 0`); await sleep(300);
      o.back = await p.evalIn(FILES);
      o.py = await view(p, 'tool.py');
      await p.shot(`loose-${w}-py`);
      await p.evalIn(`${PANE}.querySelector('.wsp-files').click(); 0`); await sleep(300);
      o.html = await view(p, 'page.html');
      await p.shot(`loose-${w}-html`);
      if (w === 390) {
        // Go to file and Text search, from the tree's pane.
        await p.evalIn(`${PANE}.querySelector('.wsp-files').click(); 0`); await sleep(300);
        await p.evalIn(`(() => { const q = ${PANE}.querySelector('.wsf-q'); q.focus(); q.value = 'tool'; q.dispatchEvent(new Event('input', { bubbles: true })); })(); 0`);
        await p.until(`${PANE}.querySelectorAll('.wsf-res .wsr').length > 0`, 15000); await sleep(300);
        o.goto = await p.evalIn(FILES);
        await p.shot('loose-390-goto');
        await p.evalIn(`${PANE}.querySelector('.wsf-mode button[data-mode="text"]').click(); (() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = 'squeal'; q.dispatchEvent(new Event('input', { bubbles: true })); })(); 0`);
        await p.until(`${PANE}.querySelectorAll('.wsf-res .wsr').length > 1`, 20000); await sleep(300);
        o.text = await p.evalIn(FILES);
        await p.shot('loose-390-text');
        await p.evalIn(`(() => { const q = ${PANE}.querySelector('.wsf-q'); q.value = ''; q.dispatchEvent(new Event('input', { bubbles: true })); ${PANE}.querySelector('.wsf-mode button[data-mode="files"]').click(); })(); 0`);
        if (A.git) {
          await p.evalIn(`${tab('Changes')}.click(); 0`);
          await p.until(`document.getElementById('detail-panel')._panes.changes.querySelectorAll('.tch-files .chf[data-file]').length > 0`, 20000);
          await sleep(300);
          o.changes = await p.evalIn(`(() => { const x = document.getElementById('detail-panel')._panes.changes;
            return { files: [...x.querySelectorAll('.tch-files .chf[data-file]')].map(e => e.dataset.file), scrollW: document.documentElement.scrollWidth, vw: innerWidth }; })()`);
          await p.shot('loose-390-changes');
        }
        o.others = {};
        for (const k of ['empty', 'gone', 'none', 'wide', 'past']) {
          await open(p, A[k]);
          await files(p, true);
          if (k === 'past') await p.until(`!!${file('notes.md')}`, 15000);
          else if (k === 'empty' || k === 'gone') await p.until(`${PANE}.querySelectorAll('.wsp-tree .wse.none').length > 0 && !${PANE}.querySelector('.wsp-tree .wse.none').textContent.includes('loading')`, 15000);
          await sleep(400);
          o.others[k] = await p.evalIn(FILES);
          await p.shot('loose-390-' + k);
        }
      }
      await p.close();
    }
  } finally {
    try { ch.kill(); } catch (e) {}
  }
  out.consoleErrors = c.errors;
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def git(cwd: Path, *args: str) -> None:
    out = subprocess.run([GIT, "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false", *args],
                         capture_output=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr


@unittest.skipUnless(NODE and CHROME, "needs Node and Chrome")
class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-loose-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        cls.patches = patches(base) + [
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in cls.patches:
            p.start()
        dashboard.invalidate_session_listing()
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        ok, ptask, err = dashboard.create_task("Quiet brakes", "spec", proj["id"], [{"agent": "claude"}])
        assert ok, err
        ptask["launched"] = True
        chatroom.update_room(ptask)
        chatroom.post_message(ptask["id"], "user", "Hello")
        # The task in no project, with a few files; its folder is a git checkout
        # with one file changed and one new since its commit.
        loose = loose_task("Write the report")
        cwd = Path(loose["cwd"])
        put(cwd / "README.md", "# The report\n\nWhy the brakes **squeal** on a long descent.\n\n- pads\n- discs\n")
        put(cwd / "tool.py", "import sys\n\n\ndef main(argv):\n    \"\"\"Print the brakes that squeal.\"\"\"\n    return 0\n")
        put(cwd / "page.html", "<!doctype html><html><head><title>before</title></head><body><h1>Brake chart</h1>"
                               "<script>document.title = 'ran';</script></body></html>\n")
        (cwd / "notes").mkdir()
        put(cwd / "notes" / "deep.txt", "deep notes\n")
        (cwd / "claude").mkdir()            # where its one agent runs
        put(cwd / "claude" / "report.md", "# What the agent wrote\n")
        cls.git = bool(GIT)
        if GIT:
            git(cwd, "init", "-q")
            git(cwd, "add", "README.md", "tool.py")
            git(cwd, "commit", "-q", "-m", "first")
            put(cwd / "tool.py", "import sys\n\n\ndef main(argv):\n    \"\"\"Print the brakes that squeal.\"\"\"\n    print('squeal')\n    return 0\n")
        empty = loose_task("An empty one")
        for x in Path(empty["cwd"]).iterdir():
            shutil.rmtree(x) if x.is_dir() else x.unlink()
        gone = loose_task("A gone one")
        shutil.rmtree(gone["cwd"])
        none = adopted_task("No folder at all", "")
        wide = adopted_task("Started at home", base / "home")
        work = base / "elsewhere" / "work"
        work.mkdir(parents=True)
        put(work / "plan.md", "# Plan\n")
        adopted = adopted_task("Old work", work)
        old = base / "elsewhere" / "old"
        old.mkdir()
        put(old / "notes.md", "# Old numbers\n")
        cls.past = "11111111-2222-4333-8444-555555555555"
        past_conversation(base, cls.past, old)
        cls.cwd, cls.old, cls.work, cls.home = cwd, old, work, base / "home"
        cls.loose, cls.ptask = loose["id"], ptask["id"]
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page closed mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        shots = os.environ.get("ENSEMBLE_SHOTS", "")
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name, "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "loose": loose["id"], "empty": empty["id"], "gone": gone["id"], "none": none["id"], "wide": wide["id"],
                "adopted": adopted["id"], "past": cls.past, "projTask": ptask["id"], "git": cls.git, "shots": shots}
        script = base / "loose_cdp.js"
        script.write_text(CDP_JS, encoding="utf-8")
        out = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True, encoding="utf-8", timeout=500)
        assert out.returncode == 0, out.stderr[-4000:]
        cls.got = json.loads(out.stdout.strip().splitlines()[-1])
        dump = os.environ.get("ENSEMBLE_DUMP", "")
        if dump:
            Path(dump).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        dashboard.invalidate_session_listing()
        cls.tmp.cleanup()

    PHONES = (360, 390, 430)

    def no_scroll(self, g, what):
        self.assertLessEqual(g["scrollW"], g["vw"], f"{what}: no sideways scroll")

    def test_nothing_went_wrong_in_the_page(self):
        self.assertEqual(self.got["consoleErrors"], [])

    # ---- a desktop

    def test_the_task_has_the_tool_strip_with_files(self):
        self.assertEqual(self.got["before"], {"strip": ["Your asks", "Changes", "Files", "Board", "Spec"], "docked": True})

    def test_files_shows_the_folder_it_runs_in(self):
        g = self.got["tree"]
        self.assertTrue(g["mounted"] and g["paneShown"])
        self.assertEqual(g["roots"], [["This task", "task", str(self.cwd)]], "one root: its own folder")
        for name in ("README.md", "tool.py", "page.html"):
            self.assertIn(name, g["files"])
        self.assertIn("notes", g["dirs"])
        self.assertIn("report.md", g["files"], "its one agent's folder is open too: that is where it writes")
        self.assertEqual(g["notes"], [], "no line saying there is nothing")
        self.assertEqual(g["placeholder"], "Go to file in this task")
        self.assertEqual(g["trail"][-1], ["tool", "Files"])
        self.no_scroll(g, "Files at 1440")
        self.assertIn("deep.txt", self.got["folder"]["files"], "a folder opens in place")

    def test_markdown_is_rendered(self):
        g = self.got["md"]
        self.assertEqual(g["files"]["tabs"], [["README.md", True]])
        self.assertEqual(g["files"]["crumbs"], ["This task", "README.md"])
        v = g["viewer"]
        self.assertEqual((v["h1"], v["bold"], v["err"]), ("The report", True, ""))
        self.assertIn(f"room={self.loose}", v["url"], "comments go to this task's chat")
        self.assertLessEqual(v["scrollW"], v["vw"])

    def test_code_is_highlighted(self):
        v = self.got["py"]["viewer"]
        self.assertEqual(v["lines"], 7)
        for tk in ("tk-kw", "tk-str", "tk-fn"):
            self.assertIn(tk, v["tokens"])

    def test_html_shows_in_its_sandbox(self):
        v = self.got["html"]["viewer"]
        self.assertEqual(v["page"], {"sandbox": "allow-same-origin", "srcdoc": True, "h1": "Brake chart", "ran": "before"},
                         "rendered, and its script did not run")
        self.assertEqual(self.got["html"]["files"]["trail"][-1], ["file", "page.html"])

    def test_go_to_file_finds_and_opens(self):
        g = self.got["goto"]
        self.assertEqual((g["results"], g["state"]), (["tool.py"], "1 file."))
        self.assertIn(["tool.py", True], self.got["gotoOpened"]["tabs"])

    def test_text_search_finds_the_lines(self):
        g = self.got["text"]
        self.assertEqual(g["placeholder"], "Search in files in this task")
        self.assertEqual(g["state"], "3 matches in 2 files." if self.git else "2 matches in 2 files.")
        self.assertTrue(any("brakes **squeal**" in r for r in g["results"]), g["results"])
        self.no_scroll(g, "Text search at 1440")

    @unittest.skipUnless(GIT, "needs git")
    def test_changes_lists_its_checkout(self):
        g = self.got["changes"]
        self.assertIn(["M", "tool.py"], g["files"])
        self.assertIn(["??", "page.html"], g["files"])
        self.assertIn("uncommitted on", g["head"])
        self.assertIn("print('squeal')", self.got["diff"])
        self.assertLessEqual(g["scrollW"], g["vw"])

    def test_an_empty_folder_says_so(self):
        for g in (self.got["others"]["empty"], self.got["phone390"]["others"]["empty"]):
            self.assertEqual(g["roots"][0][:2], ["This task", "task"])
            self.assertEqual(g["notes"], ["No files in this task's folder yet."])
            self.assertEqual(g["files"], [])

    def test_a_folder_that_is_gone_says_so(self):
        for g in (self.got["others"]["gone"], self.got["phone390"]["others"]["gone"]):
            self.assertEqual(g["notes"], ["No files to show: this task's folder is no longer on the hub."])
            w, scroll, _ = g["noteBoxes"][0]
            self.assertLessEqual(scroll, w, "the line wraps in the tree's column; nothing is cut")
            self.no_scroll(g, "a gone folder")

    def test_no_folder_says_so(self):
        for g in (self.got["others"]["none"], self.got["phone390"]["others"]["none"]):
            self.assertFalse(g["mounted"])
            self.assertEqual(g["empty"], "No files to show: this task has no folder.")

    def test_a_folder_too_wide_says_why(self):
        for g in (self.got["others"]["wide"], self.got["phone390"]["others"]["wide"]):
            self.assertFalse(g["mounted"])
            self.assertTrue(g["empty"].startswith("No files to show: this task ran in "), g["empty"])
            self.assertTrue(g["empty"].endswith("a home folder or a whole drive, which is too wide to show as its own folder."), g["empty"])
            self.no_scroll(g, "a wide folder")

    def test_a_task_brought_in_from_a_conversation_shows_where_it_runs(self):
        g = self.got["others"]["adopted"]
        self.assertEqual(g["roots"], [["This task", "task", str(self.work)]])
        self.assertEqual(g["files"], ["plan.md"])

    def test_a_past_conversation_shows_its_folder_read_only(self):
        for g in (self.got["others"]["past"], self.got["phone390"]["others"]["past"]):
            self.assertEqual(g["roots"], [["This conversation", "task", str(self.old)]])
            self.assertEqual(g["files"], ["notes.md"])
            self.assertEqual(g["placeholder"], "Go to file in this conversation")
        v = self.got["pastFile"]["viewer"]
        self.assertEqual(v["h1"], "Old numbers")
        self.assertNotIn("room=", v["url"], "no chat to send a comment to")

    def test_a_project_s_task_is_as_before(self):
        g = self.got["projTask"]
        self.assertEqual([r[:2] for r in g["roots"]], [["This task", "task"], ["Motors", "project"]])
        self.assertIn("task.json", g["files"])

    # ---- a phone

    def test_a_phone_has_files_among_its_tabs(self):
        for w in self.PHONES:
            with self.subTest(w=w):
                o = self.got[f"phone{w}"]
                self.assertEqual(o["tabs"], ["Chat", "Your asks", "Changes", "Files", "Board", "Spec"])
                g = o["tree"]
                self.assertEqual(g["roots"], [["This task", "task", str(self.cwd)]])
                for name in ("README.md", "tool.py", "page.html"):
                    self.assertIn(name, g["files"])
                self.assertTrue(g["treePane"] and g["treeShown"] and not g["viewShown"], "the tree alone, no file open")
                self.assertEqual(g["small"], [], "every row and control is finger-sized")
                self.assertEqual(g["qFont"], 16, "no zoom on focus")
                self.no_scroll(g, f"Files at {w}")

    def test_a_phone_shows_each_file_whole_width(self):
        for w in self.PHONES:
            o = self.got[f"phone{w}"]
            for k in ("md", "py", "html"):
                with self.subTest(w=w, file=k):
                    g, v = o[k]["files"], o[k]["viewer"]
                    self.assertTrue(g["viewShown"] and not g["treeShown"], "one pane at a time: the file")
                    self.assertGreaterEqual(g["frame"]["w"], w - 20)
                    self.assertEqual(g["small"], [])
                    self.assertLessEqual(v["scrollW"], v["vw"], "the file does not scroll sideways")
                    self.no_scroll(g, f"{k} at {w}")
            self.assertEqual(o["md"]["viewer"]["h1"], "The report")
            self.assertIn("tk-kw", o["py"]["viewer"]["tokens"])
            self.assertEqual(o["html"]["viewer"]["page"]["h1"], "Brake chart")
            self.assertEqual(o["html"]["viewer"]["page"]["ran"], "before")
            self.assertTrue(o["back"]["treeShown"] and not o["back"]["viewShown"], "Files, in the path bar, goes back to the tree")

    def test_a_phone_finds_files_and_text(self):
        o = self.got["phone390"]
        self.assertEqual(o["goto"]["results"], ["tool.py"])
        self.assertEqual(o["goto"]["small"], [])
        self.assertTrue(any("brakes **squeal**" in r for r in o["text"]["results"]))
        self.assertEqual(o["text"]["small"], [])
        self.no_scroll(o["text"], "Text search at 390")
        if self.git:
            self.assertIn("tool.py", o["changes"]["files"])
            self.assertLessEqual(o["changes"]["scrollW"], o["changes"]["vw"])

    # ---- a file in a window of its own (P103)

    def test_the_tab_in_front_has_the_window_button(self):
        tabs = {t[0]: t for t in self.got["windows"]["tabs"]}
        name, has, shown, title, box, x = tabs["tool.py"]
        self.assertTrue(has and shown)
        self.assertEqual(title, "Open in new window (Shift+Enter)")
        self.assertEqual((box["w"], box["h"]), (x["w"], x["h"]), "the same box as its close")
        self.assertLessEqual(box["r"], x["x"], "before the close, not over it")
        for other in ("README.md", "page.html"):
            self.assertFalse(tabs[other][1], "only the tab in front")

    def test_the_button_opens_the_file_view_in_a_sized_window(self):
        w = self.got["windows"]
        url, name, feat, got = w["asked"][0]
        self.assertTrue(url.startswith("/fileview?path="))
        self.assertTrue(got, "a window came back")
        self.assertRegex(feat, r"^popup=yes,width=\d+,height=\d+,left=-?\d+,top=-?\d+$")
        g = w["py"]
        self.assertEqual((g["title"], g["path"]), ("tool.py", str(self.cwd / "tool.py")))
        self.assertTrue(g["top"] and g["opener"], "a window of its own, not a frame")
        self.assertEqual(g["bars"], [False, False, False], "no browser bars: it opened as a popup")
        self.assertGreater(g["tokens"], 0, "the same rendering: highlighted")
        self.assertEqual(g["room"], self.loose, "its comments still go to the task")
        self.assertEqual(g["theme"], "dark", "the viewer's own theme, the hub's")

    def test_the_window_s_size_and_place(self):
        big, third, small, bare, same, differ = self.got["windows"]["feat"]
        self.assertEqual(big, "popup=yes,width=960,height=820,left=180,top=110")
        self.assertEqual(third, "popup=yes,width=960,height=820,left=228,top=158", "each new one a step down and right")
        self.assertEqual(small, "popup=yes,width=860,height=660,left=-1520,top=60", "no larger than the screen; a screen to the left keeps its place")
        self.assertEqual(bare, "popup=yes,width=960,height=820")
        self.assertTrue(same and differ, "one name per file, however its path is written")

    def test_shift_enter_opens_the_tab_as_it_was_left(self):
        g = self.got["windows"]["md"]
        self.assertEqual(g["title"], "README.md")
        self.assertEqual((g["view"], g["marked"], g["hit"]), ("source", ["3"], "squeal"), "the same view, line and match")
        self.assertEqual(self.got["windows"]["two"], 2, "several at once")

    def test_the_same_file_again_is_the_same_window(self):
        a = self.got["windows"]["again"]
        self.assertEqual(a["count"], 2)
        self.assertEqual(a["names"][1], a["names"][2])
        self.assertNotEqual(a["names"][0], a["names"][1])

    def test_a_file_row_s_menu_opens_it_in_a_window(self):
        w = self.got["windows"]
        self.assertEqual(w["menu"], {"head": "page.html", "items": ["Open", "Open in new window"], "role": "menu", "inView": True,
                                     "focus": "Open", "fly": "workspace"})
        self.assertTrue(w["menuGone"])
        self.assertEqual(w["html"]["page"], {"sandbox": "allow-same-origin", "h1": "Brake chart"})
        self.assertEqual(w["three"], 3)
        self.assertEqual(w["escaped"], {"gone": True, "fly": "workspace"}, "Escape closes the menu, not the tool")

    def test_a_docked_files_tool_and_a_project_s_task_open_windows_too(self):
        p = self.got["windows"]["pinned"]
        self.assertEqual((p["mode"], p["win"]["title"]), ("pinned", "deep.txt"))
        g = self.got["projWindow"]
        self.assertEqual((g["title"], g["room"], g["bars"]), ("task.json", self.ptask, [False, False, False]))
        self.assertEqual(g["theme"], "light")

    def test_a_blocked_window_says_so(self):
        b = self.got["windows"]["blocked"]
        self.assertIsNone(b["got"])
        self.assertIn("The browser blocked the window", b["toast"])

    def test_a_phone_has_no_window_control(self):
        for w in self.PHONES:
            with self.subTest(w=w):
                o = self.got[f"phone{w}"]
                self.assertEqual(o["noWindow"], {"got": None, "asked": 0, "menu": False, "own": True},
                                 "no window, no menu; the path bar's link to a browser tab stays")
                for name, has, shown, *_ in o["md"]["files"]["wins"]:
                    self.assertFalse(shown, "the tab's button is not shown")


if __name__ == "__main__":
    unittest.main()
