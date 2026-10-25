"""A PO chat left open through an idle period and session rotation (#267).

The CDP run uses the actual session page and hub endpoints. Its fixture is
shared with #266, while its browser actions and assertions are independent.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import unittest

from tests import chrome_profile
from tests import test_po_chat_rotation_sequence as rotation

NODE, CHROME = rotation.NODE, rotation.CHROME


OVERNIGHT_JS = rotation.CDP_JS.split("async function main() {")[0] + r"""
async function main() {
  const { ch, ws } = await launch();
  const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    const { targetId } = await c.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
    await c.send('Page.enable', {}, sessionId);
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
    const shownExpr = mid => `(() => {
      const mid = ${JSON.stringify(mid)}, box = document.getElementById('msgs');
      if (!box) return false;
      const key = GAP_OF_MID.get(mid) || GROUP_OF_MID.get(mid);
      return [...box.querySelectorAll('[data-mid]')].some(e => e.dataset.mid === mid)
        || !!key && [...box.querySelectorAll('.msg-fold[data-group]')]
          .some(e => e.dataset.group === key && e.dataset.seen === key + '|' + mid);
    })()`;
    const latest = async reportNo => {
      const start = Date.now();
      let seen = '';
      while (Date.now() - start < 16000) {
        const d = await hub('/api/room/channel?id=' + A.room);
        const mid = d.latestMid || d.items?.at(-1)?.mid || '';
        seen = mid;
        if (mid.startsWith('fresh:') && JSON.stringify(d.items || []).includes('Overnight report ' + reportNo)) return mid;
        await sleep(200);
      }
      throw new Error('hub did not index report ' + reportNo + '; latest=' + seen);
    };
    await c.send('Page.navigate', { url: A.base + '/session?room=' + A.room }, sessionId);
    await until('typeof THR_DRAWN !== "undefined" && THR_DRAWN && document.querySelectorAll("#msgs .msg[data-mid]").length > 0', 30000);
    await hub('/__test/report', {});
    await sleep(2500);
    out.selected = await evalIn(`(() => {
      const n = document.querySelector('#msgs .msg .text.md');
      if (!n) return false;
      const r = document.createRange(); r.selectNodeContents(n);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); return !!s.toString().trim();
    })()`);
    // Leave the page untouched while new work arrives; rotate without reload.
    await sleep(5500);
    await hub('/__test/rotate', {});
    await hub('/__test/report', {});
    const mid = await latest(2);
    out.payload = await until(`THR_VIEW?.items?.some(t => JSON.stringify(t).includes('Overnight report 2'))`, 15000).then(() => true).catch(() => false);
    out.overnight = await until(shownExpr(mid), 9000).then(() => true).catch(() => false);
    out.mid = mid;
    out.held = await evalIn('DRAW_HELD');
    const beforeNoticeRepairs = (await hub('/api/health')).chatRepairs || 0;
    const notice = await hub('/__test/notice', {});
    out.noticeFold = await until(`!![...document.querySelectorAll('#msgs .msg-fold[data-seen]')]
      .find(e => e.dataset.seen.endsWith('|${notice.id}'))`, 9000).then(() => true).catch(() => false);
    out.foldAfterNotice = await evalIn(`typeof thrMidDrawn === 'function' && thrMidDrawn(${JSON.stringify(mid)})`);
    await sleep(4500);
    out.falseRepairs = ((await hub('/api/health')).chatRepairs || 0) - beforeNoticeRepairs;
    await evalIn(`(() => { const i = document.getElementById('input'); i.value = 'ASK1 after rotation';
      i.dispatchEvent(new Event('input')); document.getElementById('send').click(); return true; })()`);
    out.sent = await until(`document.getElementById('msgs').textContent.includes('ASK1 after rotation')`, 1500).then(() => true).catch(() => false);
    // Let the send's own state updates finish before damaging the DOM.
    await sleep(4500);
    await until('!THR_FETCHING', 5000);
    const repairMid = await evalIn('THR_VIEW.latestMid || THR_VIEW.items.at(-1)?.mid');
    out.repairMid = repairMid;
    // Keep ordinary redraws from restoring the deliberately removed row before
    // the timed guard gets a chance. Its forced reread is still allowed.
    await evalIn(`window.__ordinaryDraw = renderBubbles;
      renderBubbles = (items, opt = {}) => { if (opt.force) return window.__ordinaryDraw(items, opt); }; true`);
    // A DOM loss with the same channel signature must trigger a full reread.
    const removedRow = await evalIn(`(() => {
      const mid = ${JSON.stringify(repairMid)};
      const key = GAP_OF_MID.get(mid) || GROUP_OF_MID.get(mid);
      const row = [...document.querySelectorAll('#msgs [data-mid]')].find(e => e.dataset.mid === mid)
        || [...document.querySelectorAll('#msgs .msg-fold[data-group]')].find(e => e.dataset.group === key);
      const state = row ? {seen:row.dataset.seen || '', group:row.dataset.group || '', mid:row.dataset.mid || ''} : null;
      if (row) row.remove(); return state;
    })()`);
    out.removed = !!removedRow;
    out.repaired = await until(`(() => {
      const row = ${JSON.stringify(removedRow)};
      return !!row && !![...document.querySelectorAll('#msgs [data-mid], #msgs .msg-fold[data-seen]')]
        .find(e => row.mid ? e.dataset.mid === row.mid : e.dataset.group === row.group && e.dataset.seen === row.seen);
    })()`, 11000).then(() => true).catch(() => false);
    await evalIn('renderBubbles = window.__ordinaryDraw; delete window.__ordinaryDraw; true');
    out.repairCount = 0;
    const repairStart = Date.now();
    while (!out.repairCount && Date.now() - repairStart < 3000) {
      out.repairCount = (await hub('/api/health')).chatRepairs || 0;
      if (!out.repairCount) await sleep(150);
    }
    // The side thread stays open while another report lands in the channel.
    const sendMid = await until(`THR_VIEW?.items?.find(t => JSON.stringify(t).includes('ASK1 after rotation'))?.mid`, 9000)
      .catch(() => null);
    out.answerMid = sendMid;
    await evalIn(`openThread('', ${JSON.stringify(sendMid || mid)}); true`);
    out.side = await until(`!!document.querySelector('#ts-frame')?.contentWindow?.document?.querySelector('#msgs .msg[data-mid]')`, 12000).then(() => true).catch(() => false);
    out.answered = await until(`document.querySelector('#ts-frame')?.contentWindow?.document?.querySelector('#msgs')?.textContent.includes('Here it is, answer 1.')`, 12000).then(() => true).catch(() => false);
    await hub('/__test/report', {});
    const sideMid = await latest(3);
    out.sideChannel = await until(shownExpr(sideMid), 12000).then(() => true).catch(() => false);
    out.sideMid = sideMid;
    const genBefore = await evalIn('THR_GEN');
    for (let i = 0; i < 6; i++) await hub('/__test/report', {});
    const capMid = await latest(9);
    out.capped = await until(shownExpr(capMid), 16000).then(() => true).catch(() => false);
    out.capAfter = await evalIn('THR_AFTER');
    out.capResets = (await evalIn('THR_GEN')) - genBefore;
    // Render a Work log in the real side frame, then advance its hub view
    // without drawing. Its old summary must not satisfy the guard.
    out.staleWorkLog = await evalIn(`(() => {
      const w = document.querySelector('#ts-frame')?.contentWindow;
      if (!w) return null;
      return w.eval('(() => {'
        + ' if (typeof thrMidDrawn !== "function") return null;'
        + ' renderBubbles([{id:"probe:old",from:"assistant",text:"Old work note",ts:Date.now()/1000,workLog:true}], {force:true});'
        + ' const rowMid = document.querySelector("#msgs .thr-work")?.dataset.lastMid || "";'
        + ' THR_VIEW = {items:[{mid:"probe:new",workLog:true}]};'
        + ' return {rowMid, shown:thrMidDrawn("probe:new")}; })()');
    })()`);
    await until('!THR_FETCHING', 5000);
    const beforeFail = (await hub('/api/health')).chatRepairs || 0;
    out.failedRepair = await evalIn(`(async () => {
      if (typeof thrRepair !== 'function') return null;
      const old = renderThreaded, oldLatest = THR_LATEST_MID;
      THR_LATEST_MID = 'probe:missing';
      THR_MISSING = {mid:'probe:missing', at:Date.now()};
      renderThreaded = async () => {};
      try { await thrRepair(); return {attempts:THR_REPAIR_FAIL?.attempts || 0,
        backoff:THR_REPAIR_FAIL?.until > Date.now()}; }
      finally { renderThreaded = old; THR_LATEST_MID = oldLatest; THR_MISSING = null; THR_REPAIR_FAIL = null; }
    })()`);
    out.failedCount = ((await hub('/api/health')).chatRepairs || 0) - beforeFail;
    await c.send('Target.closeTarget', { targetId });
  } finally {
    try { await c.send('Browser.close'); } catch (e) {}
    ch.kill();
  }
  console.log(JSON.stringify(out));
}
main().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(NODE and CHROME, "node and Chrome are needed")
class PoChatOvernight(unittest.TestCase):
    setUpClass = rotation.PoChatRotationSequence.__dict__["setUpClass"]
    tearDownClass = rotation.PoChatRotationSequence.__dict__["tearDownClass"]
    _append = rotation.PoChatRotationSequence.__dict__["_append"]
    _po_room = rotation.PoChatRotationSequence.__dict__["_po_room"]
    _rotate = rotation.PoChatRotationSequence.__dict__["_rotate"]

    @classmethod
    def _chrome(cls):
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}", "room": cls.room}
        script = Path(cls.tmp.name) / "overnight_cdp.js"
        script.write_text(OVERNIGHT_JS, encoding="utf-8")
        with rotation.mock.patch.object(rotation.threads, "CHANNEL_LIMIT", 45):
            run = subprocess.run([NODE, str(script), json.dumps(args)], capture_output=True,
                                 encoding="utf-8", timeout=180)
        assert run.returncode == 0, run.stderr[-4000:]
        got = json.loads(run.stdout.strip().splitlines()[-1])
        if os.environ.get("ENS_DEBUG"):
            Path(os.environ["ENS_DEBUG"]).write_text(json.dumps(got, indent=1), encoding="utf-8")
        return got

    def test_overnight_rotation_draws_latest_with_selection(self):
        self.assertTrue(self.got["selected"])
        self.assertTrue(self.got["payload"], self.got)
        self.assertTrue(self.got["overnight"], self.got)
        self.assertTrue(self.got["noticeFold"], self.got)
        self.assertTrue(self.got["foldAfterNotice"], self.got)
        self.assertEqual(self.got["falseRepairs"], 0, self.got)
        self.assertTrue(self.got["sent"], self.got)
        self.assertTrue(self.got["answered"], self.got)

    def test_missing_latest_repairs_and_is_counted(self):
        self.assertTrue(self.got["removed"], self.got)
        self.assertTrue(self.got["repaired"], self.got)
        self.assertGreaterEqual(self.got["repairCount"], 1, self.got)

    def test_side_frame_does_not_hide_new_channel_report(self):
        self.assertTrue(self.got["side"], self.got)
        self.assertTrue(self.got["sideChannel"], self.got)
        self.assertTrue(self.got["capped"], self.got)
        self.assertEqual(self.got["capAfter"], 0, self.got)
        self.assertGreater(self.got["capResets"], 0, self.got)
        self.assertEqual(self.got["staleWorkLog"], {"rowMid": "probe:old", "shown": False}, self.got)

    def test_failed_repair_backs_off_without_increasing_success_count(self):
        self.assertEqual(self.got["failedRepair"], {"attempts": 1, "backoff": True}, self.got)
        self.assertEqual(self.got["failedCount"], 0, self.got)
