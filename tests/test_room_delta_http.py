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
            chatroom._write(room)
            self.assertTrue(get(base + "&cursor=" + quote(delta["revision"]))["refetch"])

    @unittest.skipUnless(shutil.which("node"), "needs Node")
    def test_local_render_edits_do_not_change_delta_cursor_cache(self):
        page = (Path(__file__).resolve().parent.parent / "session.html").read_text(encoding="utf-8")
        source = page[page.index("let REFRESH_BUSY = false"):page.index("function refreshShow(room,")]
        js = r"""
const vm = require('vm'), fs = require('fs');
const context = vm.createContext({
  ROOM: 'room-12345678', SENDS_GEN: 0, structuredClone,
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
