"""Check topic navigation on copies of three PO rooms in headless Chrome.

Reads the installed transcripts but copies every mutable hub record to a
temporary state directory. The server uses an ephemeral port and never starts
the live dashboard. Run: py tools/audit_topics_real_cdp.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chatroom
import dashboard
import threads
from tests import chrome_profile
from tests.test_page_update import CHROME

ROOMS = {"Backtesting": ("room-3f303104", "P725"),
         "Single app": ("room-3f303104", "P683"),
         "X5 ad": ("room-e325b967", "P145"),
         "Ensemble general": ("room-8d56cd21", "")}
SCRIPT = chrome_profile.JS + r"""
const {spawn} = require('child_process');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const chrome = spawn(A.chrome, ['--headless=new','--remote-debugging-port=0',
    '--user-data-dir='+chromeProfile(A),'--no-first-run','--disable-gpu','about:blank'],
    {stdio:['ignore','ignore','pipe']});
  const ws = await new Promise((res,rej) => { let log=''; chrome.stderr.on('data', b => {
    log += b; const m=log.match(/DevTools listening on (ws:\S+)/); if(m) res(m[1]);
  }); chrome.on('exit', n => rej(Error('Chrome exited '+n))); setTimeout(()=>rej(Error('No CDP '+log)),20000); });
  const sock=new WebSocket(ws), pending=new Map(); let seq=0;
  await new Promise((res,rej)=>{sock.onopen=res;sock.onerror=rej;});
  sock.onmessage=e=>{const m=JSON.parse(e.data), p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p[1](Error(m.error.message)):p[0](m.result);}};
  const send=(method,params={},sessionId)=>new Promise((res,rej)=>{const id=++seq;pending.set(id,[res,rej]);sock.send(JSON.stringify({id,method,params,sessionId}));});
  const results=[];
  try {
    for(const cas of A.cases) for(const [width,height,touch] of A.sizes)
      for(const theme of A.themes) {
        const {targetId}=await send('Target.createTarget',{url:'about:blank'});
        const {sessionId:s}=await send('Target.attachToTarget',{targetId,flatten:true});
        await send('Page.enable',{},s);
        await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:touch},s);
        if(touch) await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5},s);
        const ev=async code=>{const r=await send('Runtime.evaluate',{expression:code,awaitPromise:true,returnByValue:true},s);
          if(r.exceptionDetails) throw Error(code.slice(0,90)+' '+JSON.stringify(r.exceptionDetails));return r.result.value;};
        const until=async(code,label)=>{for(let i=0;i<450;i++){try{const v=await ev(code);if(v)return v;}catch(e){}await sleep(200);}throw Error('timeout '+label);};
        await send('Page.navigate',{url:A.base+'/'},s);
        await until('typeof SELECTED_PROJECT !== "undefined"', 'boot');
        const started=Date.now();
        await ev(`SELECTED_PROJECT=${JSON.stringify(cas.project)};PROJECT_TAB='tasks';renderRows();document.documentElement.dataset.theme=${JSON.stringify(theme)};0`);
        await until(`(() => {const f=PD.els?.['po-chat']?.querySelector('iframe.po-session');
          try{return f?.dataset.room===${JSON.stringify(cas.room)} && f.contentWindow.eval('CHAT_DRAWN && THR_DRAWN');}catch(e){return false;}})()`, 'PO chat '+cas.name);
        const chatMs=Date.now()-started;
        if(A.openOnly) {
          results.push({name:cas.name,width,height,theme,chatMs});
          await send('Target.closeTarget',{targetId});
          continue;
        }
        const target = `TOPICS.data?.topics?.find(t=>${cas.anchor ? `t.threads.includes(${JSON.stringify(cas.anchor)})` : `t.threadCount>=2 && t.latest?.mid`})`;
        await until(`TOPICS.room===${JSON.stringify(cas.room)} && ${target}`, 'topic rows '+cas.name);
        await ev(`document.documentElement.dataset.theme=${JSON.stringify(theme)};0`);
        const openMs=Date.now()-started;
        const before=await ev(`(() => {const t=${target};
          const rail=PD.els.topics.getBoundingClientRect(), chips=PO_PANEL.querySelector('.topic-chips').getBoundingClientRect();
          return {id:t.id,name:t.name,mid:t.latest?.mid||'',thread:t.latest?.thread||'',count:t.threadCount,
            railWidth:rail.width,chipWidth:chips.width,phone:PD.dock.narrow(),
            scrollX:document.documentElement.scrollWidth-innerWidth,
            theme:document.documentElement.dataset.theme};})()`);
        if(!before.mid) throw Error('No PO answer for '+cas.name);
        const clicked=await ev(`(() => {const id=${JSON.stringify(before.id)};
          const root=${touch?"PO_PANEL.querySelector('.topic-chips')":"PD.els.topics"};
          const b=[...root.querySelectorAll('[data-topic-open]')].find(e=>e.dataset.topicOpen===id);
          if(!b) return false;b.click();return true;})()`);
        if(!clicked) throw Error('No topic control for '+cas.name);
        const result=await until(`(() => {const mid=${JSON.stringify(before.mid)};
          const find=d=>{if(!d)return null;const el=[...d.querySelectorAll('#msgs .msg[data-mid]')].find(x=>x.dataset.mid===mid);
            if(el){const box=el.closest('#msgs'),a=el.getBoundingClientRect(),b=box.getBoundingClientRect();
              if(a.bottom>b.top && a.top<b.bottom)return {visible:true,landed:el.classList.contains('landed')||!!el.querySelector('.landed')};}
            for(const f of d.querySelectorAll('iframe')){try{const x=find(f.contentDocument);if(x)return x;}catch(e){}}
            return null;};return find(document);})()`, 'answer '+cas.name);
        const after=await ev(`({selected:TOPICS.filter.get(${JSON.stringify(cas.room)})?.id||'',
          chip:PO_PANEL.querySelector('.topic-chips .on')?.textContent||'',
          rail:PD.els.topics.querySelectorAll('.topic-row').length,
          scrollX:document.documentElement.scrollWidth-innerWidth})`);
        let picker = null;
        if (A.pickerOnly) {
          await ev(`(() => {window.prompt=()=>{throw Error('native prompt used')};
            const row=[...PD.els.topics.querySelectorAll('.topic-row')].find(x=>x.querySelector('[data-topic-open]')?.dataset.topicOpen===${JSON.stringify(before.id)});
            row.querySelector('.topic-menu summary').click();row.querySelector('[data-topic-action="rename"]').click();
            const input=row.querySelector('[data-topic-name]');input.value='Topic audit';row.querySelector('[data-topic-save]').click();return true;})()`);
          await until(`TOPICS.data?.topics?.some(t=>t.id===${JSON.stringify(before.id)} && t.name==='Topic audit')`, 'rename');
          await ev(`(() => {const row=[...PD.els.topics.querySelectorAll('.topic-row')].find(x=>x.querySelector('[data-topic-open]')?.dataset.topicOpen===${JSON.stringify(before.id)});
            row.querySelector('.topic-menu summary').click();row.querySelector('[data-topic-action="archive"]').click();return true;})()`);
          await until(`TOPICS.data?.archived?.some(t=>t.id===${JSON.stringify(before.id)})`, 'archive');
          await ev(`(() => {const d=PD.els.topics.querySelector('.topic-restore');d.querySelector('summary').click();
            d.querySelector('[data-topic-restore-id]').value=${JSON.stringify(before.id)};d.querySelector('[data-topic-restore]').click();return true;})()`);
          await until(`TOPICS.data?.topics?.some(t=>t.id===${JSON.stringify(before.id)} && t.name==='Topic audit')`, 'restore');
          picker = {renamed:true,archived:true,restored:true};
        }
        results.push({name:cas.name,width,height,theme,chatMs,openMs,before,after,result,picker});
        await send('Target.closeTarget',{targetId});
      }
    console.log(JSON.stringify(results));
  } finally {await send('Browser.close').catch(()=>{});chrome.kill();}
}
main().catch(e=>{console.error(e.stack||String(e));process.exit(1);});
"""


def main():
    if not CHROME or not shutil.which("node"):
        raise SystemExit("Chrome and Node are needed")
    source = Path.home() / ".ensemble"
    with tempfile.TemporaryDirectory(prefix="ed246-topics-cdp-", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        state = base / "state"
        state.mkdir()
        for name in ("projects.json", "settings.json", "session_projects.json", "labels.json"):
            p = source / name
            if p.exists():
                shutil.copy2(p, state / name)
        for sub in ("rooms", "points", "threads", "sends", "input-provenance"):
            (state / sub).mkdir()
            for rid in {rid for rid, _ in ROOMS.values()}:
                for ext in (".json", ".jsonl"):
                    p = source / sub / (rid + ext)
                    if p.exists():
                        shutil.copy2(p, state / sub / p.name)
        patches = [mock.patch.object(dashboard, "DASHBOARD_DIR", state),
                   mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
                   mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
                   mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
                   mock.patch.object(dashboard, "LABELS_FILE", state / "labels.json"),
                   mock.patch.object(chatroom, "ROOMS_DIR", state / "rooms"),
                   mock.patch.object(dashboard, "load_live", lambda: []),
                   mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
                   mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
                   mock.patch.dict(os.environ, {"CODEX_HOME": str(base / "codex")})]
        for p in patches:
            p.start()
        server = None
        try:
            threads.forget()
            server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
            server.daemon_threads = True
            server.handle_error = lambda *a: None
            threading.Thread(target=server.serve_forever, daemon=True).start()
            projects = {p["poRoomId"]: p for p in dashboard.load_projects() if p.get("poRoomId") in {rid for rid, _ in ROOMS.values()}}
            open_only = "--open-only" in sys.argv
            picker_only = "--picker-only" in sys.argv
            cases = [{"name": name, "room": rid, "anchor": anchor, "project": projects[rid]["id"]}
                     for name, (rid, anchor) in ROOMS.items()
                     if not (open_only or picker_only) or name == "Backtesting"]
            script = base / "audit.js"
            script.write_text(SCRIPT, encoding="utf-8")
            args = {**chrome_profile.node_args(), "tmp": tmp,
                    "base": f"http://127.0.0.1:{server.server_address[1]}", "cases": cases,
                    "openOnly": open_only, "pickerOnly": picker_only,
                    "sizes": [[1728, 1117, False]] if (open_only or picker_only) else [[1728, 1117, False], [440, 956, True]],
                    "themes": ["light"] if (open_only or picker_only) else ["light", "dark"]}
            out = subprocess.run([shutil.which("node"), str(script), json.dumps(args)],
                                 capture_output=True, encoding="utf-8", timeout=900)
            if out.returncode:
                raise RuntimeError(out.stderr[-4000:])
            for row in json.loads(out.stdout.strip().splitlines()[-1]):
                if open_only:
                    print(json.dumps(row, ensure_ascii=False))
                    continue
                if not row["result"]["visible"] or not row["result"]["landed"]:
                    raise AssertionError(f"answer did not land: {row}")
                if row["after"]["selected"] != row["before"]["id"]:
                    raise AssertionError(f"topic filter not selected: {row}")
                if row["before"]["scrollX"] or row["after"]["scrollX"]:
                    raise AssertionError(f"horizontal overflow: {row}")
                if row["width"] == 440 and not row["before"]["chipWidth"]:
                    raise AssertionError(f"phone topic chips are hidden: {row}")
                if picker_only and row.get("picker") != {"renamed": True, "archived": True, "restored": True}:
                    raise AssertionError(f"topic picker round trip failed: {row}")
                print(json.dumps(row, ensure_ascii=False))
        finally:
            if server:
                server.shutdown()
                server.server_close()
            for p in reversed(patches):
                p.stop()


if __name__ == "__main__":
    main()
