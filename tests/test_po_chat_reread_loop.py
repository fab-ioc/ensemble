"""A PO chat whose newest item is a work-log entry in a thread (#273).

The opTen PO chat reread itself every minute, all night: the newest item
anywhere in the chat (``latestAnyMid``, #269) was a task report or a PO note
filed in a thread's Work log, which the chat only counts on the thread's
line, so the page never found it drawn and reread the chat again and again.
This drives the real page, at a desktop and a phone size: a report lands in
the Work log of the person's thread, and the page must not reread for it;
an item a reread still cannot show is reread once and counted ``unresolved``
once. Skipped without Node and Chrome.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import chrome_profile
from tests import test_po_chat_rotation_sequence as rotation

NODE, CHROME = rotation.NODE, rotation.CHROME
VIEWPORTS = [(1728, 1117), (390, 844)]
WATCH_MS = 14000        # the old guard reread at about 3 s, then 5 s later, ...

LOOP_JS = rotation.CDP_JS.split("async function main() {")[0] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  const catchups = async () => (await hub('/api/health')).chatCatchups || {};
  const delta = (a, b, k) => (b[k] || 0) - (a[k] || 0);
  try {
    for (const [width, height] of A.viewports) {
      const o = out[width + 'x' + height] = {};
      const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
      const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
      await c.send('Page.enable', {}, sessionId);
      await c.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 600 }, sessionId);
      await c.send('Emulation.setFocusEmulationEnabled', { enabled: true }, sessionId);
      const evalIn = async expr => {
        const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
        if (r.exceptionDetails) throw new Error(expr.slice(0, 140) + ' :: ' + JSON.stringify(r.exceptionDetails).slice(0, 600));
        return r.result.value;
      };
      const until = async (expr, ms = 16000) => {
        const start = Date.now();
        while (Date.now() - start < ms) {
          try { const v = await evalIn(expr); if (v) return v; } catch (e) {}
          await sleep(150);
        }
        throw new Error('timeout: ' + expr);
      };
      await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
      await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0', 30000);
      // The person asks about a task: a thread of its own, which claims the task.
      const task = '#' + (20 + A.viewports.findIndex(v => v[0] === width));
      const words = `How is ${task} going at ${width}?`;
      await evalIn(`(() => { const i = document.getElementById('input'); i.value = ${JSON.stringify(words)};
        i.dispatchEvent(new Event('input')); document.getElementById('send').click(); return true; })()`);
      await until(`document.getElementById('msgs').textContent.includes(${JSON.stringify(words)})`, 8000);
      await until(`!!THR_VIEW?.items?.find(t => (t.text || '').includes(${JSON.stringify(words)}))?.thr`, 15000);
      await sleep(2500);    // the PO's answer lands and is drawn
      await until('!THR_FETCHING', 5000);
      // The task's report lands in that thread's Work log.
      const before = await catchups();
      const gen = await evalIn('THR_GEN');
      const report = await hub('/__test/worklog', { task, words: `Review passed at ${width}.` });
      o.reportMid = report.mid;
      o.workLog = await until(`[...document.querySelectorAll('#msgs .thr-line .thr-count')]
        .some(e => /1 in Work log/.test(e.textContent))`, 12000).then(() => true).catch(() => false);
      o.latestAny = false;
      for (let i = 0; i < 20 && !o.latestAny; i++) {
        o.latestAny = (await hub('/api/room/channel?id=' + A.room + '&n=40')).latestAnyMid === report.mid;
        if (!o.latestAny) await sleep(250);
      }
      await sleep(A.watchMs);
      const after = await catchups();
      o.rereads = delta(before, after, 'thread-summary');
      o.resets = (await evalIn('THR_GEN')) - gen;
      o.threadOpens = delta(before, after, 'thread-open');
      // An item no reread can show, named by every poll: one reread,
      // counted unresolved once.
      await until('!THR_FETCHING', 5000);
      const probe = 'probe:never-' + width;
      const p0 = await catchups();
      await evalIn(`(() => { const real = thrAnyWatch; window.__real = real;
        thrAnyWatch = d => real({...d, latestAnyMid:${JSON.stringify(probe)}, latestAnyThread:'', latestAnyRole:'assistant'});
        thrAnyWatch(THR_VIEW); return true; })()`);
      await sleep(A.watchMs);
      await evalIn('thrAnyWatch = window.__real; true');
      const p1 = await catchups();
      o.probeRereads = delta(p0, p1, 'thread-summary');
      o.probeUnresolved = delta(p0, p1, 'unresolved');
      // One in a thread the newest page does not draw it in: one reread, its
      // thread opened beside the chat, and nothing more once that is closed.
      const tid = await evalIn(`(() => { if (TS) sideClose(); return THR_VIEW.items
        .find(t => (t.text || '').includes(${JSON.stringify(words)})).thr[0]; })()`);
      await until('!THR_FETCHING && !TS', 5000);
      const t0 = await catchups();
      await evalIn(`(() => { const real = thrAnyWatch; window.__real = real;
        thrAnyWatch = d => real({...d, latestAnyMid:'probe:thread-${width}', latestAnyThread:${JSON.stringify(tid)}, latestAnyRole:'assistant'});
        thrAnyWatch(THR_VIEW); return true; })()`);
      o.threadProbeOpened = await until(`TS?.tid === ${JSON.stringify(tid)}`, 10000).then(() => true).catch(() => false);
      await evalIn('sideClose(); true');
      await sleep(A.watchMs);
      await evalIn('thrAnyWatch = window.__real; true');
      const t1 = await catchups();
      o.threadProbeRereads = delta(t0, t1, 'thread-summary');
      o.threadProbeOpens = delta(t0, t1, 'thread-open');
      o.threadProbeReopened = await evalIn('!!TS');
      await c.send('Target.closeTarget', { targetId });
    }
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class PoChatRereadLoop(unittest.TestCase):
    tearDownClass = rotation.PoChatRotationSequence.__dict__["tearDownClass"]
    _append = rotation.PoChatRotationSequence.__dict__["_append"]
    _po_room = rotation.PoChatRotationSequence.__dict__["_po_room"]
    _rotate = rotation.PoChatRotationSequence.__dict__["_rotate"]

    @classmethod
    def setUpClass(cls):
        real = rotation.dashboard.Handler.do_POST

        def do_post(h):
            if h.path == "/__test/worklog":
                body = json.loads(h.rfile.read(int(h.headers.get("Content-Length", "0"))) or b"{}")
                sid = cls.current
                text = f"[report] completed from task 'Chart' ({body['task']}, claude): {body['words']}"
                cls._append(sid, rotation._turn(sid, "user", text, time.time()))
                return h._send_json(200, {"mid": f"{sid}:{len(cls.lines[sid]) - 1}"})
            return real(h)
        # Under the fixture's own handler, which passes on what it does not know.
        patch = mock.patch.object(rotation.dashboard.Handler, "do_POST", do_post)
        patch.start()
        cls.addClassCleanup(patch.stop)
        rotation.PoChatRotationSequence.setUpClass.__func__(cls)

    @classmethod
    def _chrome(cls):
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "room": cls.room,
                "viewports": VIEWPORTS, "watchMs": WATCH_MS}
        script = Path(cls.tmp.name) / "reread_loop_cdp.js"
        script.write_text(LOOP_JS, encoding="utf-8")
        run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True,
                             encoding="utf-8", timeout=300)
        assert run.returncode == 0, run.stderr[-4000:]
        got = json.loads(run.stdout.strip().splitlines()[-1])
        if os.environ.get("ENS_DEBUG"):
            Path(os.environ["ENS_DEBUG"]).write_text(json.dumps(got, indent=1), encoding="utf-8")
        return got

    def test_work_log_report_is_counted_on_its_thread_not_reread(self):
        for size, o in self.got.items():
            with self.subTest(size=size):
                self.assertTrue(o["workLog"], o)
                self.assertFalse(o["latestAny"], o)
                self.assertEqual(o["rereads"], 0, o)
                self.assertEqual(o["resets"], 0, o)
                self.assertEqual(o["threadOpens"], 0, o)

    def test_an_item_a_reread_cannot_show_is_reread_once(self):
        for size, o in self.got.items():
            with self.subTest(size=size):
                self.assertEqual(o["probeRereads"], 1, o)
                self.assertEqual(o["probeUnresolved"], 1, o)

    def test_a_thread_item_opens_its_thread_once(self):
        for size, o in self.got.items():
            with self.subTest(size=size):
                self.assertTrue(o["threadProbeOpened"], o)
                self.assertEqual(o["threadProbeRereads"], 1, o)
                self.assertEqual(o["threadProbeOpens"], 1, o)
                self.assertFalse(o["threadProbeReopened"], o)


if __name__ == "__main__":
    unittest.main()
