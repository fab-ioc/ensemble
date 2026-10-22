"""The room poll never silently loses a changed message."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from unittest.mock import patch

import chatroom
import dashboard


class RoomDeltaHttpTests(unittest.TestCase):
    def test_full_delta_and_refetch_after_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(chatroom, "ROOMS_DIR", Path(tmp)), \
                patch.object(dashboard, "_annotate_room_liveness", side_effect=lambda room, **_: room), \
                patch.object(dashboard, "checkpoint_list", return_value=[]), \
                patch.object(dashboard, "room_po_id", return_value=""), \
                patch.object(dashboard, "make_po_verdict", return_value={}), \
                patch.object(dashboard, "make_po_answer", return_value={}):
            chatroom._INDEX.clear()
            chatroom._CHANGES.clear()
            room = {"id": "room-12345678", "title": "test", "messages": [],
                    "participants": [], "updatedAt": 1}
            chatroom._write(room)
            server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}/api/room?id={room['id']}"

            def get(url):
                with urlopen(url, timeout=5) as response:
                    return json.loads(response.read())

            full = get(base)
            self.assertEqual(full["messages"], [])
            room["messages"].append({"id": "m1", "text": "first"})
            chatroom._write(room)
            delta = get(base + "&cursor=" + quote(full["revision"])
                        + "&statusSig=" + quote(full["statusSig"]))
            self.assertTrue(delta["delta"])
            self.assertEqual(delta["messages"], room["messages"])
            room["messages"].clear()
            chatroom._write(room, removed=1)
            self.assertTrue(get(base + "&cursor=" + quote(delta["revision"]))["refetch"])

    def test_a_changed_status_comes_as_a_patch_of_what_changed(self):
        rows = [{"id": "P%d" % i, "text": "x" * 300} for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(chatroom, "ROOMS_DIR", Path(tmp)), \
                patch.object(dashboard, "_annotate_room_liveness", side_effect=lambda room, **_: room), \
                patch.object(dashboard, "checkpoint_list", return_value=[]), \
                patch.object(dashboard, "room_po_id", return_value=""), \
                patch.object(dashboard, "make_po_verdict", return_value={}), \
                patch.object(dashboard, "make_po_answer", return_value={}):
            chatroom._INDEX.clear()
            chatroom._CHANGES.clear()
            room = {"id": "room-1234abcd", "title": "test", "messages": [], "updatedAt": 1,
                    "participants": [{"identity": "claude", "kind": "agent", "rotations": [
                        {"n": 1, "at": 5, "fromSessionId": "a", "toSessionId": "b", "tokens": 9,
                         "threshold": 8, "handover": "C:/x/PO-HANDOVER.md", "asked": True}]}],
                    "points": {"items": rows, "open": 1}}
            chatroom._write(room)
            server = dashboard.HubServer(("127.0.0.1", 0), dashboard.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_port}/api/room?id={room['id']}"

            def get(url):
                with urlopen(url, timeout=5) as response:
                    return response.read()

            full = json.loads(get(base))
            self.assertEqual(full["participants"][0]["rotations"],
                             [{"n": 1, "at": 5, "fromSessionId": "a", "toSessionId": "b", "tokens": 9, "threshold": 8}],
                             "a rotation without the handover's path")
            room["points"] = {"items": [{"id": "P50", "text": "new"}] + rows[:49], "open": 2}
            room["title"] = "renamed"
            room["messages"].append({"id": "m1", "text": "first"})
            chatroom._write(room)
            raw = get(base + "&cursor=" + quote(full["revision"]) + "&statusSig=" + quote(full["statusSig"]))
            delta = json.loads(raw)
            self.assertNotIn("room", delta)
            self.assertLess(len(raw), 1500, "only what changed")
            self.assertEqual(delta["patch"]["set"]["title"], "renamed")
            pts = delta["patch"]["in"]["points"]
            self.assertEqual(pts["set"], {"open": 2})
            self.assertEqual(list(pts["in"]["items"]["rows"]), ["P50"])
            # A statusSig the hub does not keep: the whole status, as before.
            stale = json.loads(get(base + "&cursor=" + quote(full["revision"]) + "&statusSig=unknown"))
            self.assertEqual(stale["room"]["title"], "renamed")
            self.assertNotIn("patch", stale)

    @unittest.skipUnless(shutil.which("node"), "needs Node")
    def test_the_page_applies_a_patch_to_the_status_it_holds(self):
        old = {"title": "a", "gone": 1, "points": {"items": [{"id": "P1", "t": 1}, {"id": "P2", "t": 2}], "open": 1},
               "participants": [{"identity": "c", "x": 1}], "ask": {"q": 1}}
        new = {"title": "b", "points": {"items": [{"id": "P3", "t": 3}, {"id": "P2", "t": 2}, {"id": "P1", "t": 9}],
                                        "open": 2},
               "participants": [{"identity": "c", "x": 2}], "ask": None, "added": [1, 2]}
        p = dashboard.status_patch(old, new)
        self.assertNotIn("P2", p["in"]["points"]["in"]["items"]["rows"], "an unchanged row is not sent")
        page = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8")
        i = page.index("function applyStatusPatch(")
        source = page[i:page.index("async function refreshRoom()", i)]
        js = source + ";const A = JSON.parse(require('fs').readFileSync(0, 'utf8'));" \
            "console.log(JSON.stringify(applyStatusPatch(A.old, A.patch)));"
        r = subprocess.run([shutil.which("node"), "-e", js], input=json.dumps({"old": old, "patch": p}),
                           text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), new)

    def test_a_change_python_calls_equal_is_still_sent(self):
        # 1 == True == 1.0 in Python, not in the JSON the sig is made of.
        p = dashboard.status_patch({"a": 1, "b": 0, "c": 1, "rows": [{"id": 1, "x": 1}, {"id": 2, "x": 0}]},
                                   {"a": True, "b": False, "c": 1.0, "rows": [{"id": 1, "x": True}, {"id": 2, "x": 0}]})
        self.assertEqual(p["set"], {"a": True, "b": False, "c": 1.0})
        self.assertEqual(p["in"]["rows"]["rows"], {"1": {"id": 1, "x": True}})

    @unittest.skipUnless(shutil.which("node"), "needs Node")
    def test_local_render_edits_do_not_change_delta_cursor_cache(self):
        page = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8")
        source = page[page.index("let REFRESH_BUSY = false"):page.index("function refreshShow(room,")]
        js = r"""
const vm = require('vm'), fs = require('fs');
const context = vm.createContext({
  ROOM: 'room-12345678', SENDS_GEN: 0, structuredClone, ROOM_OBJ: null, ROOM_N: 150,
  ptTicket: () => 0,
  fetch: async () => ({ok: true, json: async () => ({delta: true, revision: 'rev2',
    statusSig: 'sig2', statusSame: true,
    messages: [{id: 'm1', text: 'server edit'}]})}),
  refreshShow: room => { room.messages[0].text = 'local edit'; room.openCards[0].label = 'local edit'; }
});
vm.runInContext(fs.readFileSync(0, 'utf8'), context);
vm.runInContext(`ROOM_LAST = {revision: 'rev1', statusSig: 'sig1', room: {
  revision: 'rev1', statusSig: 'sig1', messages: [{id: 'm1', text: 'old'}],
  openCards: [{label: 'server card'}]}}`, context);
vm.runInContext('refreshRoom()', context).then(() => {
  const cache = vm.runInContext('ROOM_LAST.room', context);
  if (cache.messages[0].text !== 'server edit' || cache.openCards[0].label !== 'server card')
    throw Error('render changed the cursor cache: ' + JSON.stringify(cache));
}).catch(e => { console.error(e); process.exitCode = 1; });
"""
        result = subprocess.run([shutil.which("node"), "-e", js], input=source,
                                text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
