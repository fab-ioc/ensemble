"""Stop and Escape in a real Chrome chat at laptop and phone sizes."""
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import chatroom
import dashboard
from test_page_update import CHROME, NODE
from tests import chrome_profile


JS = chrome_profile.JS + r"""
const A=JSON.parse(process.argv[1]);
const {spawn}=require('child_process'); const fs=require('fs');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){
  const ch=spawn(A.chrome,['--headless=new','--remote-debugging-port=0','--user-data-dir='+chromeProfile(A),'--no-first-run','--no-default-browser-check','--disable-gpu','about:blank'],{stdio:['ignore','ignore','pipe']});
  const ws=await new Promise((res,rej)=>{let b='';ch.stderr.on('data',d=>{b+=d;let m=b.match(/DevTools listening on (ws:\S+)/);if(m)res(m[1]);});setTimeout(()=>rej(Error(b||'no CDP')),20000);});
  const sock=new WebSocket(ws), waits=new Map();let seq=0;
  await new Promise(res=>sock.onopen=res);
  sock.onmessage=e=>{const m=JSON.parse(e.data);if(m.method==='Runtime.exceptionThrown')console.error('PAGE ERROR '+JSON.stringify(m.params.exceptionDetails));if(m.id&&waits.has(m.id)){const w=waits.get(m.id);waits.delete(m.id);m.error?w.reject(Error(m.error.message)):w.resolve(m.result);}};
  const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;waits.set(id,{resolve,reject});sock.send(JSON.stringify({id,method,params,sessionId}));});
  const out={};
  try { for(const [W,H] of [[1728,1117],[390,844]]) for(const surface of ['popout','dock']) {
    const {targetId}=await send('Target.createTarget',{url:'about:blank'});
    const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
    await send('Page.enable',{},sessionId);
    await send('Runtime.enable',{},sessionId);
    await send('Emulation.setDeviceMetricsOverride',{width:W,height:H,deviceScaleFactor:1,mobile:W<500},sessionId);
    if(W<500)await send('Emulation.setTouchEmulationEnabled',{enabled:true},sessionId);
    const ev=async x=>{const r=await send('Runtime.evaluate',{expression:x,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
    const until=async x=>{for(let n=0;n<100;n++){try{if(await ev(x))return;}catch(e){}await sleep(100);}throw Error('timeout '+x);};
    const key=async()=>{await send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},sessionId);await send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},sessionId);};
    let baseline=null;
    if(A.baseline){
      const old=A.base+'/old-session?room='+encodeURIComponent(A.room);
      await send('Page.navigate',{url:surface==='dock'?A.base+'/stop-test-host?old=1&room='+encodeURIComponent(A.room):old},sessionId);
      const be=x=>surface==='dock'?ev('document.querySelector("iframe").contentWindow.eval('+JSON.stringify(x)+')'):ev(x);
      for(let n=0;n<150;n++){try{if(await be('!!document.querySelector("#compose")'))break;}catch(e){}await sleep(100);}
      baseline=await be('({stop:!!document.querySelector("#stop-wrap"),scroller:document.querySelector("#msgs").getBoundingClientRect().height})');
      const shot=await send('Page.captureScreenshot',{format:'png'},sessionId);
      fs.writeFileSync(A.shots+'/baseline-'+surface+'-'+W+'.png',Buffer.from(shot.data,'base64'));
    }
    const src=A.base+'/session?room='+encodeURIComponent(A.room);
    await send('Page.navigate',{url:surface==='dock'?A.base+'/stop-test-host?room='+encodeURIComponent(A.room):src},sessionId);
    const ve=x=>surface==='dock'?ev('document.querySelector("iframe").contentWindow.eval('+JSON.stringify(x)+')'):ev(x);
    const vu=async x=>{let last='';for(let n=0;n<300;n++){try{if(await ve(x))return;}catch(e){last=String(e)}await sleep(100);}let state='';try{state=JSON.stringify(await ev('({outer:location.href,src:document.querySelector("iframe")?.contentWindow?.location.href,ready:document.querySelector("iframe")?.contentDocument?.readyState,html:document.querySelector("iframe")?.contentDocument?.body?.innerHTML.slice(0,100)})'))}catch(e){}throw Error('timeout '+surface+'/'+W+' '+x+' '+last+' '+state);};
    await vu('typeof updateStopControl === "function" && !!ROOM_OBJ');
    await ve('window.__stops=[]; postOk=async (url, body)=>{ if(url.includes("interrupt")){window.__stops.push(body);return {ok:true,interrupt:{from:"user",identity:body.identity,ts:Date.now()/1000}};}return {ok:true};}; refresh=()=>{}; 0');
    const idle=await ve('document.querySelector("#stop-wrap").hidden');
    if(A.shots){const shot=await send('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(A.shots+'/before-'+surface+'-'+W+'.png',Buffer.from(shot.data,'base64'));}
    await ve('ROOM_LIVE=true; ROOM_OBJ.workingAgents=["claude"]; ROOM_AGENTS=[{identity:"claude",ptyId:"pty-test"}]; updateStopControl({},{}); 0');
    const busy=await ve('(() => {const b=document.querySelector("#stop-agent"),r=b.getBoundingClientRect();return {visible:!document.querySelector("#stop-wrap").hidden, rect:[r.left,r.top,r.right,r.bottom],scroller:document.querySelector("#msgs").getBoundingClientRect().height,coarse:matchMedia("(pointer: coarse)").matches};})()');
    if(A.shots){const shot=await send('Page.captureScreenshot',{format:'png'},sessionId);fs.writeFileSync(A.shots+'/after-'+surface+'-'+W+'.png',Buffer.from(shot.data,'base64'));}
    await ve('document.querySelector("#stop-agent").click(); 0');await vu('window.__stops.length===1');
    const clicked=await ve('({n:window.__stops.length,focus:document.activeElement.id,note:document.querySelector("#stop-note").textContent})');
    await key();const dedup=await ve('window.__stops.length');
    await sleep(1050);
    await ve('document.querySelector("#input").focus();document.querySelector("#input").value="draft";0');await key();
    const filled=await ve('({n:window.__stops.length,value:document.querySelector("#input").value})');
    await ve('window.__dlg=document.createElement("dialog");document.body.appendChild(window.__dlg);window.__dlg.showModal(); 0');await key();
    const dialog=await ve('({n:window.__stops.length,open:window.__dlg.open})');
    await ve('window.__dlg.remove(); 0');
    if(W<500){await ve('document.querySelector(".phone-compose-tools").open=true; 0');await key();}
    const menu=await ve('({n:window.__stops.length,open:document.querySelector(".phone-compose-tools").open})');
    await ve('document.querySelector("#input").value=""; document.querySelector("#input").blur(); 0');await key();await vu('window.__stops.length===2');
    const escaped=await ve('({n:window.__stops.length,focus:document.activeElement.id})');
    await ve('ROOM_LIVE=false; updateStopControl({},{}); 0');
    const stopped=await ve('document.querySelector("#stop-wrap").hidden');
    out[surface+'/'+W]={baseline,idle,busy,clicked,dedup,filled,dialog,menu,escaped,stopped};
    await send('Target.closeTarget',{targetId});
  }}finally{sock.close();ch.kill();}
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
"""


@unittest.skipUnless(NODE and CHROME, "Node and Chrome are needed")
class InChrome(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ens-interrupt-", ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        state = base / "state"
        state.mkdir()
        cls.patches = [
            mock.patch.object(chatroom, "ROOMS_DIR", base / "rooms"),
            mock.patch.object(dashboard, "DASHBOARD_DIR", state),
            mock.patch.object(dashboard, "SETTINGS_FILE", state / "settings.json"),
            mock.patch.object(dashboard, "PROJECTS_FILE", state / "projects.json"),
            mock.patch.object(dashboard, "SESSION_PROJECTS_FILE", state / "session_projects.json"),
            mock.patch.object(dashboard, "PROJECTS_ROOT", base / "projects"),
            mock.patch.object(dashboard, "PROJ_DIR", base / "transcripts"),
            mock.patch.object(dashboard, "CS_ROOT", base / "cs"),
            mock.patch.object(dashboard, "load_live", lambda: []),
            mock.patch.object(dashboard, "_read_agent_session_files", lambda *a, **k: []),
            mock.patch.object(dashboard, "_room_is_live", lambda room: False),
            mock.patch.object(dashboard.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)
        room = chatroom.create_room("Interrupt", [{"identity": "claude", "agent": "claude"}])
        cls.room = room["id"]
        class TestHandler(dashboard.Handler):
            def do_GET(self):
                if self.path.startswith('/stop-test-host'):
                    from urllib.parse import parse_qs, urlparse
                    q = parse_qs(urlparse(self.path).query)
                    rid = q['room'][0]
                    page = 'old-session' if q.get('old') else 'session'
                    html = (f'<html><meta name="viewport" content="width=device-width,initial-scale=1">'
                            f'<style>body{{margin:0;background:#e8ebf0}}'
                            f'iframe{{display:block;width:min(650px,100vw);height:100vh;border:0;background:white}}'
                            f'</style><iframe src="/{page}?room={rid}&embed=1"></iframe></html>').encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                    return
                if self.path.startswith('/old-session'):
                    data = old_page
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                super().do_GET()

        old_page = subprocess.check_output(['git', 'show', 'HEAD:session.html'])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        args = {**chrome_profile.node_args(), "tmp": cls.tmp.name,
                "base": f"http://127.0.0.1:{cls.server.server_address[1]}",
                "room": cls.room, "shots": os.environ.get("STOP_SCREENSHOT_DIR", ""),
                "baseline": bool(os.environ.get("STOP_SCREENSHOT_DIR"))}
        got = subprocess.run([NODE, "-e", JS, json.dumps(args)], capture_output=True,
                             encoding="utf-8", timeout=90)
        assert got.returncode == 0, got.stderr[-4000:]
        cls.results = json.loads(got.stdout.strip().splitlines()[-1])
        if os.environ.get("STOP_SCREENSHOT_DIR"):
            (Path(os.environ["STOP_SCREENSHOT_DIR"]) / "measurements.json").write_text(
                json.dumps(cls.results, indent=2), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def test_stop_and_escape(self):
        for surface in ('popout', 'dock'):
            for width in (1728, 390):
                with self.subTest(surface=surface, width=width):
                    r = self.results[f'{surface}/{width}']
                    self.assertTrue(r["idle"])
                    self.assertTrue(r["busy"]["visible"])
                    self.assertEqual(r["clicked"]["focus"], "input")
                    self.assertIn("Stopped by you", r["clicked"]["note"])
                    self.assertEqual(r["dedup"], 1)
                    self.assertEqual(r["filled"], {"n": 1, "value": "draft"})
                    self.assertEqual(r["dialog"], {"n": 1, "open": False})
                    self.assertEqual(r["menu"], {"n": 1, "open": False})
                    self.assertEqual(r["escaped"], {"n": 2, "focus": "input"})
                    self.assertTrue(r["stopped"])


if __name__ == "__main__":
    unittest.main()
