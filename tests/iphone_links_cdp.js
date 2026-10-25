// Called by test_iphone_links_cdp.py with an isolated hub and seeded profile.
const {spawn} = require('child_process');
const fs = require('fs');
const path = require('path');
const A = JSON.parse(process.argv[2]);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const safari = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1';
class Cdp {
  constructor(url) { this.url=url; this.id=0; this.waits=new Map(); }
  async open() { this.ws=new WebSocket(this.url); await new Promise((ok,no)=>{this.ws.onopen=ok;this.ws.onerror=no;}); this.ws.onmessage=e=>{const m=JSON.parse(e.data),w=this.waits.get(m.id);if(w){this.waits.delete(m.id);m.error?w.no(Error(m.error.message)):w.ok(m.result);}}; }
  send(method,params={},sessionId) { const id=++this.id;return new Promise((ok,no)=>{this.waits.set(id,{ok,no});this.ws.send(JSON.stringify({id,method,params,sessionId}));}); }
}
async function main() {
  const profile=fs.mkdtempSync(path.join(A.tmp,'chrome-'));
  fs.writeFileSync(path.join(profile,'Local State'),A.chromeLocalState);
  const ch=spawn(A.chrome,['--headless=new','--remote-debugging-port=0','--user-data-dir='+profile,'--no-first-run','--no-default-browser-check','--disable-gpu','about:blank'],{stdio:['ignore','ignore','pipe']});
  const ws=await new Promise((ok,no)=>{let buf='';ch.stderr.on('data',d=>{buf+=d;const m=buf.match(/DevTools listening on (ws:\S+)/);if(m)ok(m[1]);});ch.on('exit',n=>no(Error('Chrome exit '+n+' '+buf)));setTimeout(()=>no(Error('DevTools timeout '+buf)),20000);});
  const c=new Cdp(ws);await c.open();
  const rows=[];
  try {
    for(const [width,height,coarse] of [[440,956,true],[390,844,true],[1728,1117,false]]) {
      const {targetId}=await c.send('Target.createTarget',{url:'about:blank'});
      const {sessionId:s}=await c.send('Target.attachToTarget',{targetId,flatten:true});
      const send=(method,params={})=>c.send(method,params,s);
      await send('Page.enable');
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:coarse?3:1,mobile:coarse});
      await send('Emulation.setTouchEmulationEnabled',{enabled:coarse,maxTouchPoints:5});
      if(coarse)await send('Emulation.setUserAgentOverride',{userAgent:safari,platform:'iPhone'});
      const ev=async expression=>{const r=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails).slice(0,1000));return r.result.value;};
      const until=async (expression,ms=15000)=>{const end=Date.now()+ms;while(Date.now()<end){try{if(await ev(expression))return;}catch{}await sleep(100);}throw Error(`Timeout at ${width}: ${expression} ${await ev('document.body.innerText.slice(-500)')}`);};
      const visible=selector=>ev(`(() => {const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;const r=e.getBoundingClientRect(),b=document.querySelector('#msgs')?.getBoundingClientRect();const top=Math.max(0,r.top,b&&b.contains?0:0),bottom=Math.min(innerHeight,r.bottom);const x=Math.max(1,Math.min(innerWidth-2,r.left+Math.min(r.width/2,20))),y=top+Math.min(20,(bottom-top)/2);const h=document.elementFromPoint(x,y);return r.width>0&&bottom>top&&!!h&&(h===e||e.contains(h));})()`);
      const pointVisible=(mid,part)=>ev(`(() => {const box=document.querySelector('#msgs'),e=[...box.querySelectorAll('.msg[data-mid]')].find(x=>x.dataset.mid===${JSON.stringify(mid)}),t=${JSON.stringify(part)}&&e?.querySelector('.qa[data-ask="'+${JSON.stringify(mid+':0')}+'"]')||e;if(!t)return false;const r=t.getBoundingClientRect(),b=box.getBoundingClientRect(),x=r.left+Math.min(r.width/2,20),y=Math.max(r.top,b.top)+Math.min(20,(Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top))/2),h=document.elementFromPoint(x,y);return r.width>0&&r.top<b.bottom&&r.bottom>b.top&&!!h&&(h===t||t.contains(h));})()`);
      const key=async k=>{for(const type of ['keyDown','keyUp'])await send('Input.dispatchKeyEvent',{type,key:k,code:k,windowsVirtualKeyCode:k==='Escape'?27:13});};
      async function where(selector) {
        await ev(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',inline:'center'});0`);
        await sleep(500);
        if (!coarse) {
          const center=await ev(`(() => {const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.left+r.width/2,y:r.top+r.height/2};})()`);
          await send('Input.dispatchMouseEvent',{type:'mouseMoved',x:center.x,y:center.y});
        }
        const p=await ev(`(() => {const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,h=document.elementFromPoint(x,y);return{x,y,w:r.width,h:r.height,hit:!!h&&(h===e||e.contains(h)),hitEl:h?.outerHTML.slice(0,180),html:e.outerHTML.slice(0,180)};})()`);
        if(!p.hit||p.x<0||p.x>=width||p.y<0||p.y>=height)throw Error(`Covered ${width} ${selector}: ${JSON.stringify(p)}`);
        return p;
      }
      async function press(p) {
        if(coarse)await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:p.x,y:p.y,id:1}]});
        else await send('Input.dispatchMouseEvent',{type:'mousePressed',x:p.x,y:p.y,button:'left',clickCount:1});
      }
      async function release(p) {
        if(coarse)await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
        else await send('Input.dispatchMouseEvent',{type:'mouseReleased',x:p.x,y:p.y,button:'left',clickCount:1});
      }
      async function tap(selector,redraw=false) {
        const p=await where(selector);await press(p);
        let held=false;
        if(redraw){
          fs.writeFileSync(path.join(A.tmp,'poll-signal-'+width),'go');
          const deadline=Date.now()+10000;
          while(!fs.existsSync(path.join(A.tmp,'poll-done-'+width))&&Date.now()<deadline)await sleep(50);
          if(!fs.existsSync(path.join(A.tmp,'poll-done-'+width)))throw Error('Polling update was not posted');
          await ev('refresh()');
          held=await ev('!!PG.later');
        }
        await sleep(60);await release(p);await sleep(180);return{p,held};
      }
      await send('Page.navigate',{url:A.base+'/session?room='+A.room});
      const status=`#msgs .msg[data-mid="${A.status}"]`;
      const pointStatus=`#msgs .msg[data-mid="${A.pointStatus}"]`;
      await until(`!!document.querySelector('${status} .task-chip:not(.point-chip)') && !!document.querySelector('${pointStatus} .point-chip') && !!document.querySelector('${status} .ref-chip') && !!document.querySelector('#msgs .thr-open')`,30000);
      await sleep(500);
      if(coarse)await ev(`document.addEventListener('touchend',e=>e.preventDefault(),{capture:true,passive:false});0`);
      const o={size:[width,height],coarse:await ev("matchMedia('(pointer: coarse)').matches")};
      o.referenceDebug=await ev(`({total:ROOM_OBJ.messages?.length, hasCard:ROOM_OBJ.messages?.some(m=>m.id===${JSON.stringify(A.card)}), window:ROOM_OBJ.window, hasOlder:roomHasOlder()})`);
      o.referenceWasOlder=!o.referenceDebug.hasCard && o.referenceDebug.hasOlder;
      const referenceTap=await tap(status+' .ref-chip');
      o.referenceSize=[referenceTap.p.w,referenceTap.p.h];
      await until(`LANDED && LANDED.mid === ${JSON.stringify(A.card)}`);
      o.referenceTarget=await pointVisible(A.card,'ask:0');
      const pointTap=await tap(pointStatus+' .point-chip');
      o.pointSize=[pointTap.p.w,pointTap.p.h];
      o.pointCard=await visible('.task-card .tc-title');
      await tap('.task-card .tc-title');
      await until(`LANDED && LANDED.mid === ${JSON.stringify(A.source)}`);
      o.pointTarget=await pointVisible(A.source,'');
      if(coarse){await tap('.phone-compose-tools > summary');await tap('[data-phone-action="threads-view"]');}
      else await tap('#threads-view');
      await until(`document.querySelector('#threads-view').getAttribute('aria-pressed')==='true' && !!document.querySelector('#msgs .thr-card.thr-open')`);
      o.threadList=await visible('#msgs .thr-card.thr-open');
      await tap('#msgs .thr-card.thr-open');
      await until(`!document.querySelector('#thr-side').hidden && !!document.querySelector('#ts-frame').contentDocument?.querySelector('#msgs .msg')`);
      o.threadRow=await visible('#thr-side');
      await tap(coarse?'#ts-back':'#ts-close');
      if(coarse){await tap('.phone-compose-tools > summary');await tap('[data-phone-action="threads-view"]');}
      else await tap('#threads-view');
      await until(`document.querySelector('#threads-view').getAttribute('aria-pressed')==='false' && !!document.querySelector('${status} .task-chip:not(.point-chip)')`);
      await tap('#msgs .thr-open');
      await until(`!document.querySelector('#thr-side').hidden && !!document.querySelector('#ts-frame').contentDocument?.querySelector('#msgs .msg')`);
      o.thread=await visible('#thr-side');
      await tap(coarse?'#ts-back':'#ts-close');
      const replyTap=await tap(status+' .thr-reply');
      o.replySize=[replyTap.p.w,replyTap.p.h];
      await until(`!document.querySelector('#thr-side').hidden && !!document.querySelector('#ts-frame').contentDocument?.querySelector('#msgs .msg')`);
      o.replyButton=await visible('#thr-side');
      await tap(coarse?'#ts-back':'#ts-close');
      o.reactionTargets=await ev(`(() => {
        const m=document.querySelector(${JSON.stringify(status)}), rs=SelBar.REACTIONS.slice(0,2);
        const by=new Map([['reaction-check',rs.map(r=>({r,mid:${JSON.stringify(A.source)},quote:'test'}))]]);
        m.insertAdjacentHTML('beforeend',reactRowHtml({id:'reaction-check'},by));
        const links=[...m.querySelectorAll('.react-row a.react-chip')];
        links[0]?.scrollIntoView({block:'center'});
        const rects=links.map(a=>a.getBoundingClientRect());
        return links.map((a,i)=>{const r=rects[i],x=r.left+r.width/2,y=r.top+r.height/2,h=document.elementFromPoint(x,y);return{w:r.width,h:r.height,hit:h===a||a.contains(h),gap:i?Math.max(r.left-rects[i-1].right,r.top-rects[i-1].bottom):0,pseudo:getComputedStyle(a,'::after').content};});
      })()`);
      const taskTap=await tap(status+' .task-chip:not(.point-chip)');
      o.taskSize=[taskTap.p.w,taskTap.p.h];
      o.taskCard=await visible('.task-card .tc-title');
      await key('Escape');
      await until(`!document.querySelector('.task-card')`);
      const redraw=await tap(status+' .task-chip:not(.point-chip)',true);
      o.drawHeld=redraw.held;
      o.redrawCard=await visible('.task-card .tc-title');
      await until(`(ROOM_OBJ.messages || []).some(m=>m.text.includes('Polling update during touch ${width}.'))`);
      o.pollApplied=true;
      await tap('.task-card .tc-title');
      await until(`typeof ROOM==='string' && ROOM===${JSON.stringify(A.task)} && !!document.querySelector('#msgs')`);
      o.taskOpen=await visible('#msgs');
      rows.push(o);
      await c.send('Target.closeTarget',{targetId});
    }
  } finally {try{await c.send('Browser.close');}catch{}ch.kill();}
  console.log(JSON.stringify(rows));
}
main().catch(e=>{console.error(e.stack||e);process.exit(1);});
