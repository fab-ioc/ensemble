"""A press on Send always ends (#251, GitHub issue 22).

Headless Chrome over CDP (tests/chrome_profile.py) at 1728 and 390 wide,
against a hub in a thread whose /api/room/resume is told, per room, to
answer, never answer, answer after 40 s, drop the connection once, or refuse
(403, 500); its /api/health can be held too, and /api/feedback/preview held
or dropped. Every press must end in a button that is not busy, with the text
kept and a hint that says what happened; a Retry after the hub took the
first attempt late must not post the message twice (its key, taken once).
Skipped without Node or Chrome. About two minutes: the give-up is 20 s.
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from tests import test_phone_layout as phone
from tests.test_phone_layout import chatroom, dashboard, test_middle

LANES: dict = {}        # roomId -> how its sends are answered
MODE = {"health": "ok", "feedback": "ok"}
DELIVERED: list = []    # (roomId, text) of every send the hub took
TRIES: dict = {}        # roomId -> how many times its sends reached the hub
RELEASE = threading.Event()     # lets every held request go on


def _drop(h):
    try:
        h.connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    h.connection.close()
    h.close_connection = True
    h._responded = True


_POST, _GET = dashboard.Handler.do_POST, dashboard.Handler.do_GET


def do_POST(self):
    p = urlparse(self.path).path
    if p == "/api/room/resume":
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        self.rfile = io.BytesIO(raw)    # read again by the handler
        self.close_connection = True    # the connection's own reader is set aside
        rid = json.loads(raw or b"{}").get("roomId")
        TRIES[rid] = TRIES.get(rid, 0) + 1
        lane = LANES.get(rid, "ok")
        if lane == "stall":
            RELEASE.wait(120)
        elif lane == "late40":
            RELEASE.wait(40)
        elif lane == "drop1":
            LANES[rid] = "ok"
            return _drop(self)
        elif lane == "drop":
            return _drop(self)
        elif lane in ("403", "500"):
            return self._send_json(int(lane), {"error": "page_only" if lane == "403" else "the hub broke"})
    if p == "/api/feedback/preview":
        if MODE["feedback"] == "stall":
            RELEASE.wait(120)
        elif MODE["feedback"] == "drop":
            return _drop(self)
    return _POST(self)


def do_GET(self):
    u = urlparse(self.path)
    if u.path == "/__ctl":
        for k, v in parse_qs(u.query).items():
            if k.startswith("room-"):
                LANES[k] = v[0]
            else:
                MODE[k] = v[0]
        return self._send_json(200, {"delivered": [t for _, t in DELIVERED], "tries": TRIES})
    if u.path == "/api/health" and MODE["health"] == "stall":
        RELEASE.wait(120)
    return _GET(self)


def _resume_room(self, room_full, text="", to="", key="", **kw):
    # Taken at once, as by a running team: no agent is started.
    if text:
        DELIVERED.append((room_full["id"], text))
    return {"resumed": [], "queued": 0, "delivered": 1 if text else 0}


JS = phone.HEAD + r"""
const STATE = `({busy: SEND_IN_FLIGHT, label: document.getElementById('send').textContent,
  disabled: document.getElementById('send').disabled, readOnly: document.getElementById('input').readOnly,
  inert: document.getElementById('compose').inert, hint: document.getElementById('hint').textContent,
  box: document.getElementById('input').value})`;
async function main() {
  const {ch, ws} = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  const ctl = q => fetch(A.base + '/__ctl?' + q).then(r => r.json());
  const tab = async (width, url, ready) => {
    const height = width < 600 ? 844 : 1117, touch = width < 600;
    const {targetId} = await c.send('Target.createTarget',{url:'about:blank'});
    const {sessionId:s} = await c.send('Target.attachToTarget',{targetId,flatten:true});
    await c.send('Page.enable',{},s);
    await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:touch},s);
    const ev = async expression => {const r=await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until = async (expression, n = 200) => {for(let i=0;i<n;i++){try{if(await ev(expression))return;}catch{}await sleep(100);}throw Error('timeout '+expression);};
    await c.send('Page.navigate',{url:A.base+url},s);
    await until(ready);
    return {s, ev, until, close: () => c.send('Target.closeTarget',{targetId})};
  };
  // Type, press Send, watch until the button is free (or 40 s).
  const press = async (t, text) => {
    await t.ev("document.getElementById('input').focus()");
    if (text) await c.send('Input.insertText',{text},t.s);
    const t0 = Date.now();
    await t.ev("document.getElementById('send').click(); 0");
    const during = await t.ev(STATE);
    for (let i = 0; i < 400 && (await t.ev('SEND_IN_FLIGHT')); i++) await sleep(100);
    return {during, after: await t.ev(STATE), took: Date.now() - t0};
  };
  const chat = room => tab(A.width, '/session?room=' + encodeURIComponent(room),
    "!!document.getElementById('input') && !!ROOM_OBJ && !document.getElementById('send').disabled");
  const R = A.rooms;
  try {
    const w = A.width;
    // Each case in its own room and tab, at once: the 20 s give-ups overlap.
    await ctl(`${R.stall}=stall&${R.late}=late40&${R.drop}=drop1&${R.r403}=403&${R.r500}=500&${R.hidden}=stall`);
    const tabs = {};
    for (const k of ['stall','late','drop','r403','r500','hidden']) tabs[k] = await chat(R[k]);
    const runs = {};
    for (const k of ['stall','late','drop','r403','r500']) runs[k] = press(tabs[k], `Message ${k} at ${w}`);
    // A page put to sleep past the deadline: shown again, it gives the send up at once.
    runs.hidden = (async () => {
      const t = tabs.hidden;
      await t.ev("document.getElementById('input').focus()");
      await c.send('Input.insertText',{text:`Message hidden at ${w}`},t.s);
      await t.ev("document.getElementById('send').click(); 0");
      await sleep(1000);
      const during = await t.ev(STATE);
      await t.ev("(() => { const now = Date.now; Date.now = () => now() + 25000; document.dispatchEvent(new Event('visibilitychange')); })()");
      await sleep(500);
      return {during, after: await t.ev(STATE)};
    })();
    for (const k of Object.keys(runs)) out[k] = await runs[k];
    // The hub takes the stalled and the late attempts after the page gave them up;
    // Send again (Retry) then must not post them twice.
    await ctl(`${R.stall}=ok&${R.late}=ok`);
    await fetch(A.base + '/__release');
    for (let i = 0; i < 100; i++) {
      const d = (await ctl('')).delivered;
      if (d.includes(`Message stall at ${w}`) && d.includes(`Message late at ${w}`)) break;
      await sleep(200);
    }
    out.stallRetry = await press(tabs.stall, '');
    out.lateRetry = await press(tabs.late, '');
    out.delivered = (await ctl('')).delivered;
    for (const t of Object.values(tabs)) await t.close();
    // Connection dropped and the hub's health check never answers.
    // Every attempt dropped while the hub answers the rest, and its health
    // check never answers: given up, not sent again and again.
    await ctl(`health=stall&${R.drop}=drop`);
    const t = await chat(R.drop);
    const before = (await ctl('')).tries[R.drop] || 0;
    out.dropHealthStalls = await press(t, `Message dropped at ${w}`);
    out.dropHealthStalls.tries = ((await ctl('')).tries[R.drop] || 0) - before;
    await ctl('health=ok');
    await fetch(A.base + '/__release');
    await t.close();
    // Settings > Send feedback > Preview: held, then dropped.
    const idx = await tab(w, '/', "typeof window.feedbackOpen === 'function'");
    const FB = `({busy: document.getElementById('feedback-preview-button').disabled,
      label: document.getElementById('feedback-preview-button').textContent,
      status: document.getElementById('feedback-status').textContent,
      title: document.getElementById('feedback-title').value})`;
    const preview = async mode => {
      await ctl('feedback=' + mode);
      await idx.ev(`(() => { feedbackOpen(); const t = document.getElementById('feedback-title'), d = document.getElementById('feedback-description');
        t.value = 'Preview ${mode}'; d.value = 'What happened'; document.getElementById('feedback-anonymous').checked = true; t.dispatchEvent(new Event('input', {bubbles: true}));
        document.getElementById('feedback-preview-button').click(); })()`);
      await sleep(300);
      const during = await idx.ev(FB);
      const t0 = Date.now();
      for (let i = 0; i < 300 && (await idx.ev("document.getElementById('feedback-preview-button').disabled")); i++) await sleep(100);
      return {during, after: await idx.ev(FB), took: Date.now() - t0};
    };
    out.feedbackStall = await preview('stall');
    await fetch(A.base + '/__release');
    out.feedbackDrop = await preview('drop');
    await ctl('feedback=ok');
    out.feedbackOk = await preview('ok');
    await idx.close();
  } finally { ch.kill(); }
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack);process.exit(1)});
"""


@unittest.skipUnless(phone.NODE and phone.CHROME, "needs Node and Chrome")
class SendNeverSticks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-sticks-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        root = base / "EnsembleProjects"
        root.mkdir()
        (base / "transcripts").mkdir()
        (base / "cs").mkdir()

        def get(self, _orig=do_GET):
            if urlparse(self.path).path == "/__release":
                RELEASE.set()
                time.sleep(0.2)
                RELEASE.clear()
                return self._send_json(200, {})
            return _orig(self)

        patches = [
            mock.patch.object(dashboard, "PROJECTS_ROOT", root),
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
            mock.patch.object(dashboard.Handler, "_agent_peer", lambda h: ""),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
            mock.patch.object(dashboard.Handler, "do_POST", do_POST),
            mock.patch.object(dashboard.Handler, "do_GET", get),
            mock.patch.object(dashboard.Handler, "_resume_room", _resume_room),
            mock.patch.object(dashboard, "_send_left_behind", lambda *a: ""),
            mock.patch.object(dashboard, "_settle_asks", lambda *a: None),
            mock.patch("feedback.output", lambda *a, **k: ""),
            mock.patch.dict("feedback._login", {"at": 0.0, "login": ""}),
            mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")}),
        ]
        for p in patches:
            p.start()
            cls.addClassCleanup(p.stop)
        ok, proj, _ = dashboard.register_project("Motors")
        assert ok, proj
        home = Path(proj.get("home") or proj["path"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        cls.server.daemon_threads = True
        cls.server.handle_error = lambda *a: None   # a page that gave up mid-answer
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(RELEASE.set)
        cls.got = {}
        for width in (1728, 390):
            rooms = {}
            for k in ("stall", "late", "drop", "r403", "r500", "hidden"):
                r = chatroom.create_room(f"{k} {width}", [{"identity": "claude", "agent": "claude", "cwd": str(home)}])
                dashboard.assign_session_project(r["id"], proj["id"])
                rooms[k] = r["id"]
            args = {**test_middle.chrome_profile.node_args(), "tmp": cls.tmp.name,
                    "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "width": width, "rooms": rooms}
            script = base / f"sticks_{width}.js"
            script.write_text(JS, encoding="utf-8")
            out = subprocess.run([phone.NODE, str(script), json.dumps(args)], capture_output=True,
                                 encoding="utf-8", timeout=400)
            assert out.returncode == 0, out.stderr[-4000:]
            cls.got[width] = json.loads(out.stdout.strip().splitlines()[-1])
        if os.environ.get("ENSEMBLE_STICKS_OUT"):
            Path(os.environ["ENSEMBLE_STICKS_OUT"]).write_text(json.dumps(cls.got, indent=1), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        RELEASE.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def free(self, st, what):
        """The button is Send again, the box can be typed in."""
        self.assertFalse(st["busy"], what)
        self.assertEqual(st["label"], "Send", what)
        self.assertFalse(st["disabled"], what)
        self.assertFalse(st["readOnly"], what)
        self.assertFalse(st["inert"], what)

    def test_a_send_that_never_answers_gives_up_and_keeps_the_text(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                for k in ("stall", "late"):
                    r = g[k]
                    self.assertTrue(r["during"]["busy"], f"{k}: busy while it waits")
                    self.assertEqual(r["during"]["label"], "Sending…")
                    self.free(r["after"], k)
                    self.assertGreater(r["took"], 18000, f"{k}: given up at the deadline, not before")
                    self.assertLess(r["took"], 24000, f"{k}: given up at the deadline")
                    self.assertEqual(r["after"]["box"], f"Message {k} at {w}", f"{k}: the text stays")
                    self.assertIn("did not answer within 20 seconds", r["after"]["hint"])
                    self.assertIn("Send again, it is posted only once", r["after"]["hint"])

    def test_a_retry_after_a_late_success_posts_once(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                for k in ("stall", "late"):
                    text = f"Message {k} at {w}"
                    self.assertEqual(sum(d.startswith(text + "\n") for d in g["delivered"]), 1, f"{k}: taken once")
                    r = g[k + "Retry"]
                    self.free(r["after"], k)
                    self.assertEqual(r["after"]["box"], "", f"{k}: the retry counts as sent")
                    self.assertEqual(r["after"]["hint"], "")

    def test_a_dropped_connection_is_sent_again(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                r = g["drop"]
                self.free(r["after"], "drop")
                self.assertEqual(r["after"]["box"], "", "sent once the hub answered")
                self.assertLess(r["took"], 10000)
                self.assertEqual(sum(d.startswith(f"Message drop at {w}\n") for d in g["delivered"]), 1)

    def test_a_dropped_connection_with_no_way_back_gives_up(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                r = g["dropHealthStalls"]
                self.free(r["after"], "drop, health held")
                self.assertLess(r["took"], 24000)
                self.assertEqual(r["after"]["box"], f"Message dropped at {w}")
                self.assertTrue(r["after"]["hint"].startswith("Not confirmed: the hub could not be reached"), r["after"]["hint"])
                # The first try and three replays, each of which Chrome itself
                # tries up to three times: not thousands (measured before #251).
                self.assertLessEqual(r["tries"], 16)

    def test_a_refusal_says_why(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                for k, words in (("r403", "the hub refused it"), ("r500", "the hub broke")):
                    r = g[k]
                    self.free(r["after"], k)
                    self.assertLess(r["took"], 5000)
                    self.assertEqual(r["after"]["box"], f"Message {k} at {w}")
                    self.assertTrue(r["after"]["hint"].startswith("Not sent: " + words), r["after"]["hint"])

    def test_a_page_shown_again_gives_up_a_send_past_its_deadline(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                r = g["hidden"]
                self.assertTrue(r["during"]["busy"])
                self.free(r["after"], "shown again")
                self.assertEqual(r["after"]["box"], f"Message hidden at {w}")
                self.assertIn("did not answer within 20 seconds", r["after"]["hint"])

    def test_the_feedback_preview_never_sticks(self):
        for w, g in self.got.items():
            with self.subTest(w=w):
                s = g["feedbackStall"]
                self.assertTrue(s["during"]["busy"], "busy while it waits")
                self.assertFalse(s["after"]["busy"])
                self.assertLess(s["took"], 24000)
                self.assertIn("did not come back within 20 seconds", s["after"]["status"])
                self.assertEqual(s["after"]["title"], "Preview stall", "the text stays")
                d = g["feedbackDrop"]
                self.assertFalse(d["after"]["busy"])
                self.assertIn("Your text is kept", d["after"]["status"])
                ok = g["feedbackOk"]
                self.assertFalse(ok["after"]["busy"])
                self.assertEqual(ok["after"]["label"], "Preview again")


if __name__ == "__main__":
    unittest.main()
