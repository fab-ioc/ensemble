"""Open in editor (#194, GitHub issue 15): what /api/repos/<sid> offers for
each kind of session the board holds today.

Before #194 the lookup only walked up from the session's working folder: a
PO whose folder is a ~/cs scratch folder found nothing ("no initiative
found"), and a task or PO folder under the projects folder found the
projects folder itself (a git repo of its own, for backups) instead of its
code. Fixtures live in a throwaway state; nothing of the real hub is read.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chatroom  # noqa: E402
import dashboard  # noqa: E402


def _repo(p: Path) -> Path:
    (p / ".git").mkdir(parents=True)
    return p


class ReposForSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-ide-", ignore_cleanup_errors=True)
        b = Path(cls.tmp.name)
        cls.base = b
        root = _repo(b / "EnsembleProjects")            # the projects folder is a repo (backups)
        code = _repo(b / "code" / "app")                 # a code project's own checkout
        (code / ".idea").mkdir()
        home = root / "App"
        task = home / "fix_it"
        _repo(task / "repo")                             # a worktree task
        (home / "app_po").mkdir(parents=True)            # a PO seat's folder, no repo
        notes = root / "Notes"                           # a documents project
        (notes / "write_letter").mkdir(parents=True)
        (b / "cs" / "07_po").mkdir(parents=True)         # an old PO's scratch folder
        loose = b / "loose"
        _repo(loose / "one"); _repo(loose / "two")       # a folder holding two repos
        (b / "plain").mkdir()
        (task / "notes").mkdir()                         # a task working in a subfolder with no repo
        mono = _repo(b / "mono")                         # a project whose code is a folder of a larger repo
        (mono / "packages" / "app" / "src").mkdir(parents=True)
        cls.p_mono = mono / "packages" / "app"
        cls.p = {"root": root, "code": code, "home": home, "task": task, "notes": notes, "loose": loose}

        projects = [
            {"id": "proj-app", "name": "App", "path": str(code), "home": str(home), "kind": "code",
             "poRoomId": "room-0000a001"},
            {"id": "proj-old", "name": "Old", "path": str(code), "home": str(b / "cs" / "07_po"),
             "kind": "code", "poRoomId": "room-0000a002"},
            {"id": "proj-notes", "name": "Notes", "path": str(notes), "home": str(notes), "kind": "documents",
             "poRoomId": "room-0000a003"},
            {"id": "proj-mono", "name": "Mono", "path": str(mono / "packages" / "app"), "home": str(root / "Mono"),
             "kind": "code", "poRoomId": "room-0000a004"},
            {"id": "proj-gone", "name": "Gone", "path": str(b / "code" / "gone"), "home": str(root / "Gone"),
             "kind": "code", "poRoomId": ""},
        ]
        links = {"room-0000a001": "proj-app", "room-0000a002": "proj-old", "room-0000a003": "proj-notes",
                 "room-0000b001": "proj-app", "room-0000b002": "proj-notes", "room-0000b003": "proj-gone",
                 "sess-linked-gone": "proj-app", "room-0000a004": "proj-mono", "room-0000b004": "proj-app",
                 "sess-mono": "proj-mono", "sess-mono-src": "proj-mono",
                 "room-0000b005": "proj-mono"}
        cwds = {"sess-loose": str(loose), "sess-plain": str(b / "plain"), "sess-gone": str(b / "nowhere"),
                "sess-past-task": str(task / "repo"), "sess-linked-gone": str(b / "nowhere2"), "sess-none": "",
                "sess-root": str(root), "sess-mono": str(mono / "packages" / "app"),
                "sess-mono-src": str(mono / "packages" / "app" / "src")}
        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
            mock.patch.object(chatroom, "ROOMS_DIR", b / "rooms"),
            mock.patch.object(dashboard, "load_projects", lambda: [dict(p) for p in projects]),
            mock.patch.object(dashboard, "load_session_projects", lambda: dict(links)),
            mock.patch.object(dashboard, "cwd_for_session", lambda sid: cwds.get(sid, "")),
            mock.patch.object(dashboard, "_read_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "resolve_editor_for_repo",
                              lambda p: ("java", "IntelliJ IDEA" if (p / ".idea").exists() else "Visual Studio Code", None)),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
        # Stopped newest first, so no fake outlives this class (#192).
        for p in patches:
            cls.addClassCleanup(p.stop)

        def room(rid, cwd, task_dir=""):
            r = chatroom.create_room(rid, [{"identity": "claude", "agent": "claude", "sessionId": rid + "-s"}])
            full = chatroom.get_room(r["id"], public=False)
            chatroom.delete_room(r["id"])
            full.update(id=rid, cwd=str(cwd), taskDir=str(task_dir))
            chatroom.update_room(full)

        room("room-0000a001", home / "app_po")                         # PO, folder in the project home
        room("room-0000a002", b / "cs" / "07_po")                      # PO, ~/cs scratch folder
        room("room-0000a003", notes)                                   # documents project's PO
        room("room-0000b001", task / "repo", task)                     # worktree task
        room("room-0000b002", notes / "write_letter", notes / "write_letter")   # documents task
        room("room-0000a004", b / "cs" / "07_po")                      # PO of a code folder inside a monorepo
        room("room-0000b004", task / "notes", task)                    # task working in a subfolder
        (root / "Mono" / "inplace_task").mkdir(parents=True)
        room("room-0000b005", mono / "packages" / "app" / "src", root / "Mono" / "inplace_task")  # in-place task
        room("room-0000b003", root / "Gone" / "t" / "repo", root / "Gone" / "t")  # every folder gone

        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.addClassCleanup(server.server_close)
        cls.addClassCleanup(server.shutdown)
        cls.url = f"http://127.0.0.1:{server.server_address[1]}/api/repos/"
        cls.addClassCleanup(cls.tmp.cleanup)

    def get(self, sid):
        with urllib.request.urlopen(self.url + sid, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def paths(self, sid):
        return [r["path"] for r in self.get(sid)["repos"]]

    def test_po_opens_the_projects_code_folder(self):
        # Its own folder is in the project home, inside the projects repo:
        # before, that repo (the projects folder) was offered.
        r = self.get("room-0000a001")
        self.assertEqual([x["path"] for x in r["repos"]], [str(self.p["code"])])
        self.assertEqual(r["repos"][0]["editor"], "IntelliJ IDEA")

    def test_po_in_a_scratch_folder_opens_the_code_folder(self):
        # The reported case: a ~/cs folder with no repo gave "no initiative found".
        self.assertEqual(self.paths("room-0000a002"), [str(self.p["code"])])

    def test_documents_po_opens_the_projects_folder(self):
        self.assertEqual(self.paths("room-0000a003"), [str(self.p["notes"])])

    def test_task_opens_its_worktree(self):
        self.assertEqual(self.paths("room-0000b001"), [str(self.p["task"] / "repo")])

    def test_documents_task_opens_its_task_folder(self):
        # Never the projects folder its walk up would reach.
        self.assertEqual(self.paths("room-0000b002"), [str(self.p["notes"] / "write_letter")])

    def test_past_conversation_of_a_task_opens_its_worktree(self):
        self.assertEqual(self.paths("sess-past-task"), [str(self.p["task"] / "repo")])

    def test_session_in_no_project_with_several_repos_gives_the_picker(self):
        self.assertEqual(self.paths("sess-loose"), [str(self.p["loose"] / "one"), str(self.p["loose"] / "two")])

    def test_session_in_no_project_without_a_repo_opens_its_folder(self):
        self.assertEqual(self.paths("sess-plain"), [str(self.base / "plain")])

    def test_session_whose_folder_is_gone_falls_back_to_its_projects_code(self):
        r = self.get("sess-linked-gone")
        self.assertEqual([x["path"] for x in r["repos"]], [str(self.p["code"])])
        self.assertEqual(r["lookedIn"][0], {"path": str(self.base / "nowhere2"), "exists": False})

    def test_nothing_to_open_says_where_it_looked(self):
        r = self.get("sess-gone")
        self.assertEqual(r, {"repos": [], "lookedIn": [{"path": str(self.base / "nowhere"), "exists": False}]})
        r = self.get("room-0000b003")
        gone = self.p["root"] / "Gone"
        self.assertEqual(r["repos"], [])
        self.assertEqual(r["lookedIn"], [{"path": str(gone / "t" / "repo"), "exists": False},
                                         {"path": str(gone / "t"), "exists": False},
                                         {"path": str(self.base / "code" / "gone"), "exists": False}])

    def test_task_in_a_subfolder_without_a_repo_still_opens_its_worktree(self):
        # Review 1: the plain subfolder is the last resort, after every other folder.
        self.assertEqual(self.paths("room-0000b004"), [str(self.p["task"] / "repo")])

    def test_projects_folder_is_never_offered(self):
        # Review 1: a session working in the projects folder itself (a backup repo).
        r = self.get("sess-root")
        self.assertEqual(r, {"repos": [], "lookedIn": [{"path": str(self.p["root"]), "exists": True}]})

    def test_po_opens_the_configured_code_folder_not_the_repo_around_it(self):
        # Review 1: a code folder inside a monorepo opens as configured.
        self.assertEqual(self.paths("room-0000a004"), [str(self.p_mono)])

    def test_session_in_the_code_folder_opens_it_not_the_repo_around_it(self):
        # Review 2: a session of the project working in its code folder, or below it.
        self.assertEqual(self.paths("sess-mono"), [str(self.p_mono)])
        self.assertEqual(self.paths("sess-mono-src"), [str(self.p_mono)])

    def test_in_place_task_below_the_code_folder_opens_it(self):
        # Review 3: its task folder does not hold its working folder, so the
        # code folder bounds the walk.
        self.assertEqual(self.paths("room-0000b005"), [str(self.p_mono)])

    def test_no_folder_known(self):
        self.assertEqual(self.get("sess-none"), {"repos": [], "lookedIn": []})
        self.assertEqual(self.get("room-0000ffff"), {"repos": [], "lookedIn": []})


NODE = shutil.which("node")

SESSION_IDE_JS = r"""
const out = [];
const ROOM = 'room-0000b001';
const onHubMachine = () => true, toDashboard = () => out.push('dashboard');
const actCwd = () => '/cwd';
const jpost = (url, body) => out.push(body.path + ' ' + body.app);
const alert = m => out.push('alert:' + m);
let REPLY;
globalThis.fetch = async () => ({ json: async () => REPLY });
const ACTS = { %s };
(async () => {
  for (const r of [{ repos: [{ path: '/code/app', editor: 'IntelliJ IDEA' }], lookedIn: [] },
                   { repos: [], lookedIn: [{ path: '/gone', exists: false }] },
                   [{ path: '/old', editor: 'code' }]]) {
    REPLY = r; await ACTS.ide();
  }
  console.log(JSON.stringify(out));
})();
"""


@unittest.skipUnless(NODE, "node is needed")
class SessionPageIde(unittest.TestCase):
    """The session page's Open in editor (session.html ACTS.ide) reads the
    {repos, lookedIn} reply: before, it read .length on the object and always
    opened the working folder instead."""

    def test_opens_the_first_repo_of_the_reply(self):
        src = (ROOT / "session.html").read_text(encoding="utf-8")
        m = re.search(r"^  async ide\(\) \{.*?^  \},", src, re.M | re.S)
        self.assertIsNotNone(m)
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "ide.js"
            js.write_text(SESSION_IDE_JS % m.group(0).strip().rstrip(","), encoding="utf-8")
            r = subprocess.run([NODE, str(js)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), ["/code/app IntelliJ IDEA", "/cwd default", "/old code"])


if __name__ == "__main__":
    unittest.main()
