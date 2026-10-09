"""#198: a click acts only on what was pressed. Real CDP pointer sequences in
the PO chat of an isolated hub, at 1728x1117 and 390x844.

The accident it reproduces (measured on the live PO chat, 10-09): with the
Workspace tool slid out beside the conversation, a press on empty space put the
tool back at once (#196), the conversation widened under the still pointer and
a quick answer came to sit where the pointer was; the next press of the same
double click landed on it and sent an answer nobody meant to send."""
import json
import unittest
from unittest import mock

from tests import test_phone_layout as phone

ASK = ("Checkpoints with undo per turn would be the biggest gain for us, then linking a task to its pull request. "
       "Native mobile apps would be a much larger job.\n\n"
       "Ask: Shall I start a task for undo per turn?\n"
       "- Yes, start it, high priority (recommended)\n"
       "- First a short study doc comparing it in depth\n"
       "- No, just noting it\n")

JS = phone.HEAD + r"""
const ASK = __ASK__;
async function main() {
  const {ch, ws} = await launch(); const c = new Cdp(ws); await c.open();
  const out = {};
  try {
    for (const [width, height, touch] of [[1728,1117,false],[390,844,true]]) {
      const R = out[width] = {};
      const {browserContextId}=await c.send('Target.createBrowserContext');
      const {targetId}=await c.send('Target.createTarget',{url:'about:blank',browserContextId});
      const {sessionId:s}=await c.send('Target.attachToTarget',{targetId,flatten:true});
      await c.send('Page.enable',{},s);
      await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:touch},s);
      await c.send('Emulation.setTouchEmulationEnabled',{enabled:touch,maxTouchPoints:5},s);
      const ev=async expression=>{const r=await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
      const until=async expression=>{for(let i=0;i<180;i++){try{if(await ev(expression))return;}catch{}await sleep(100);}throw Error('timeout '+expression);};
      await c.send('Page.navigate',{url:A.base+'/'},s);
      await until(`!!document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]')`);
      await ev(`document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]').click(); 0`);
      const F = `document.querySelector('#po-panel iframe.po-session:not([hidden])')`;
      await until(`!!${F}?.contentWindow?.eval('ROOM_OBJ')`);
      const inF = js => ev(`${F}.contentWindow.eval(${JSON.stringify(js)})`);
      await ev(`${F}.contentWindow.__ASK=${JSON.stringify(ASK)}; 0`);
      await inF(`tick=()=>{}; refresh=async()=>{}; window.__asked=[];
        const old=window.fetch;
        window.__undone=[];
        window.fetch=(url,opt)=>{if(String(url)==='/api/room/ask/undo' && opt?.method==='POST'){const b=JSON.parse(opt.body);window.__undone.push(b);const P=JSON.parse(JSON.stringify(window.EMBED_POINTS?.points||{}));if(P.asks&&P.asks[b.mid])delete P.asks[b.mid][b.n];return Promise.resolve(new Response(JSON.stringify({ok:true,withdrawn:true,points:P}),{status:200,headers:{'Content-Type':'application/json'}}));}if(String(url)==='/api/room/ask' && opt?.method==='POST'){const b=JSON.parse(opt.body);window.__asked.push(b);
          // The hub's reply settles the card: its points hold the answer.
          const P=JSON.parse(JSON.stringify(window.EMBED_POINTS?.points||{}));P.asks=P.asks||{};(P.asks[b.mid]=P.asks[b.mid]||{})[b.n]={option:b.option,comment:b.comment||'',question:''};
          return Promise.resolve(new Response(JSON.stringify({ok:true,points:P}),{status:200,headers:{'Content-Type':'application/json'}}));}return old(url,opt);};
        window.__who=ROOM_OBJ.participants.find(p=>p.kind==='agent').identity;
        // A card of quick answers, its own message id each time (an answered card is settled).
        window.__card=(mid, pre=0, extra=[], post=0)=>{const now=Date.now()/1000;
          const filler=Array.from({length:pre},(_, i)=>({id:'f'+i,from:i%2?'user':__who,text:'An earlier message '+i+'. '+'words '.repeat(60),ts:now-900+i}));
          const items=[...filler,{id:'u-'+mid,from:'user',text:'How do we compare?',ts:now-400},...extra,{id:mid,from:__who,text:__ASK,ts:now-300,asks:parseAsks(__ASK),askAudience:'user'},...Array.from({length:post},(_, i)=>({id:'p'+i,from:i%2?'user':__who,text:'A later message '+i+'. '+'words '.repeat(150),ts:now-200+i}))];
          ASK_LOCAL.clear(); window.__asked=[]; LAST_ITEMS=null; renderBubbles(items); return items;};
        0`);
      // The first option, in page coordinates.
      const opt = (n=0) => ev(`(() => {const f=${F},a=f.getBoundingClientRect(),o=f.contentDocument.querySelectorAll('.qa-opt')[${n}];if(!o) return null;const b=o.getBoundingClientRect();return {x:a.x+b.x+b.width/2,y:a.y+b.y+b.height/2,left:a.x+b.x,top:a.y+b.y,frameX:a.x,frameW:a.width};})()`);
      const kindAt = (x,y) => ev(`(() => {const f=${F},a=f.getBoundingClientRect(); if(${x}<a.x||${x}>a.right||${y}<a.y||${y}>a.bottom) return 'out'; const e=f.contentDocument.elementFromPoint(${x}-a.x,${y}-a.y); return !e?'':(e.closest('.qa-opt')?'OPT':e.closest('button,a,textarea,input,summary,[role=button]')?'ctl':'blank');})()`);
      const asked = () => inF('window.__asked.length');
      const press = async (x,y,n=1) => {
        if(touch) await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]},s);
        else await c.send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:n},s);
      };
      const release = async (x,y,n=1) => {
        if(touch) await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},s);
        else await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:n},s);
      };
      const click = async (x,y,n=1) => { await press(x,y,n); await release(x,y,n); };
      const moveTo = async (x,y) => { if(!touch) await c.send('Input.dispatchMouseEvent',{type:'mouseMoved',x,y},s); };

      if (!touch) {
        // (a) The accident: the tool out, a double click on empty space where
        // the first option comes to sit once the tool is back.
        await sleep(400); await inF(`__card('acc'); toLatest(); 0`);
        await ev(`PD.dock.setViewMode('workspace','unpinned'); PD.dock.openFly('workspace'); 0`);
        await sleep(600);
        await ev(`PD.dock.closeFly(); 0`); await sleep(300);
        const back = await opt();
        await ev(`PD.dock.openFly('workspace'); 0`); await sleep(600);
        R.accOpen = await opt();
        const P = {x:Math.round(back.x), y:Math.round(back.y)};
        R.accPointOpen = await kindAt(P.x,P.y);
        await moveTo(P.x,P.y); await sleep(450);
        await press(P.x,P.y,1);
        R.accAtPress = {fly: await ev('PD.dock.flyOpen() || ""'), opt: await opt()};
        await release(P.x,P.y,1);
        await sleep(150);
        R.accAfterRelease = {fly: await ev('PD.dock.flyOpen() || ""'), opt: await opt(), under: await kindAt(P.x,P.y)};
        await click(P.x,P.y,2);
        await sleep(250);
        R.accAsked = await asked();
        // A click meant for it, the pointer having stayed: it answers.
        await sleep(500);
        await click(P.x,P.y,1);
        await sleep(250);
        R.accDeliberate = await inF('window.__asked');

        // (b) The tool out, a click straight on an option: sent once; the tool
        // goes back after the click.
        await sleep(400); await inF(`__card('strip'); toLatest(); 0`);
        await ev(`PD.dock.openFly('workspace'); 0`); await sleep(600);
        const o = await opt(1);
        await moveTo(o.x,o.y); await sleep(450);
        await press(o.x,o.y);
        R.stripAtPress = {fly: await ev('PD.dock.flyOpen() || ""'), opt: await opt(1)};
        await release(o.x,o.y);
        await sleep(250);
        R.stripAfter = {fly: await ev('PD.dock.flyOpen() || ""'), asked: await inF('window.__asked')};

        // (c) The chat drawn again while the press is on, with messages put in
        // above (scrolled up, so it is anchored, not at the end): what is under
        // the pointer stays; the option answers once.
        await sleep(400); await inF(`window.__items=__card('redraw', 10, [], 8); const b=$('#msgs'); const o=b.querySelector('.qa-opt'); b.scrollTop+=o.getBoundingClientRect().top-b.getBoundingClientRect().top-b.clientHeight/2; STICK=nearEnd(b); 0`);
        await sleep(300);
        R.redrawStick = await inF('STICK');
        const r0 = await opt();
        await moveTo(r0.x,r0.y); await sleep(450);
        await press(r0.x,r0.y);
        await inF(`const now=Date.now()/1000; const add=[1,2,3].map(i=>({id:'new'+i,from:__who,text:'A new message '+i+'. '+'more '.repeat(50),ts:now-350+i})); const it=__items.slice(); it.splice(it.findIndex(m=>m.id==='redraw'),0,...add); renderBubbles(it); 0`);
        R.redrawDuring = {opt: await opt(), held: await inF('DRAW_HELD')};
        await release(r0.x,r0.y);
        await sleep(500);
        R.redrawAfter = {opt: await opt(), held: await inF('DRAW_HELD'), drawn: await inF(`!!document.querySelector('#msgs [data-mid="new1"]')`), asked: await inF('window.__asked'), r0};

        // The catch-up line going (markRead) above a card the pointer rests on.
        await sleep(400); await inF(`window.__items=__card('cu', 10, [], 8); const it=__items; CU_POINT={id:'u-cu',ts:it[10].ts}; CU_SNAP=null; const now=Date.now()/1000; it.splice(it.findIndex(m=>m.id==='cu'),0,...[1,2,3].map(i=>({id:'cn'+i,from:__who,to:'all',text:'A note for the team '+i+'. '+'more '.repeat(40),ts:now-350+i}))); renderBubbles(it); const b=$('#msgs'); const o=b.querySelector('.qa-opt'); b.scrollTop+=o.getBoundingClientRect().top-b.getBoundingClientRect().top-b.clientHeight/2; STICK=nearEnd(b); 0`);
        await sleep(300);
        R.cuLine = await inF(`!!document.querySelector('#msgs > .catchup')`); R.cuStick = await inF('STICK');
        const c0 = await opt();
        await moveTo(c0.x,c0.y); await sleep(100);
        await inF('markRead(); 0');
        R.cu = {before: c0, after: await opt(), line: await inF(`!!document.querySelector('#msgs > .catchup')`)};
      }

      // (d) An option that comes under a pointer standing still (it moves
      // away, the person presses on empty space, it comes back): the next
      // press within 400 ms does nothing; a press after that answers.
      // Scrolled up (not at the end), so the card does not move when its options go.
      await sleep(400); await inF(`__card('under', 4, [], 8); const b=$('#msgs'); const o=b.querySelector('.qa-opt'); b.scrollTop+=o.getBoundingClientRect().top-b.getBoundingClientRect().top-b.clientHeight/2; STICK=nearEnd(b); 0`);
      await sleep(300);
      R.underStick = await inF('STICK');
      const u = await opt();
      const Q = {x:Math.round(u.x), y:Math.round(u.y)};
      await moveTo(Q.x,Q.y); await sleep(450);
      await inF(`document.querySelector('#msgs .msg[data-mid="under"]').querySelector('.qa-opts').style.display='none'; 0`);
      await sleep(100);
      R.underAway = await kindAt(Q.x,Q.y);
      await click(Q.x,Q.y,1);
      await inF(`document.querySelector('#msgs .msg[data-mid="under"]').querySelector('.qa-opts').style.display=''; 0`);
      await sleep(120);
      R.underBack = await kindAt(Q.x,Q.y);
      await click(Q.x,Q.y,2);
      await sleep(250);
      R.underAsked = await asked();
      await sleep(500);
      await click(Q.x,Q.y,1);
      await sleep(250);
      R.underLater = await inF('window.__asked');

      // (e) The first click on a card that has been on screen: it answers.
      await sleep(400); await inF(`__card('first'); toLatest(); 0`);
      await sleep(500);
      const f = await opt(2);
      await moveTo(f.x,f.y); await sleep(100);
      await click(f.x,f.y);
      await sleep(250);
      R.first = await inF('window.__asked');
      // A key on a focused option is not a press: it answers.
      await sleep(400); await inF(`__card('key'); toLatest(); document.querySelector('.qa-opt').focus(); 0`);
      await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',text:String.fromCharCode(13),windowsVirtualKeyCode:13},s);
      await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},s);
      await sleep(250);
      R.key = await inF('window.__asked');
      // (f) Sent · Undo: offered after the answer, a click on it takes it back,
      // and it goes once its time is up.
      const undoAt = () => ev(`(() => {const f=${F},a=f.getBoundingClientRect(),u=f.contentDocument.querySelector('.qa-undo');if(!u) return null;const b=u.getBoundingClientRect();return {x:a.x+b.x+b.width/2,y:a.y+b.y+b.height/2,words:u.closest('.qa-done').querySelector('.qa-dw').textContent};})()`);
      await sleep(450);
      const ub = await undoAt();
      R.undoOffered = ub && ub.words;
      if (ub) { await moveTo(ub.x,ub.y); await sleep(100); await click(ub.x,ub.y); }
      await sleep(600);     // the redraw waits out the press's 300 ms hold
      R.undone = await inF('window.__undone');
      R.undoAfter = !!(await undoAt());
      await sleep(400); await inF(`__card('exp'); toLatest(); document.querySelector('.qa-opt').focus(); 0`);
      await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',text:String.fromCharCode(13),windowsVirtualKeyCode:13},s);
      await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},s);
      await sleep(250);
      R.expBefore = !!(await undoAt());
      await inF(`ASK_UNDO.forEach((v,k)=>ASK_UNDO.set(k,Date.now()-1)); renderBubbles(LAST_ITEMS); 0`);
      R.expAfter = !!(await undoAt());
      if (!touch) {
      // (g) The person's own wheel scroll brings an option under the still
      // pointer: a click right after it answers (moves the page makes are guarded).
      await sleep(400); await inF(`__card('wheel', 4, [], 8); const b=$('#msgs'); const o=b.querySelector('.qa-opt'); b.scrollTop+=o.getBoundingClientRect().top-b.getBoundingClientRect().top-b.clientHeight/2; 0`);
      await sleep(300);
      const w0 = await opt();
      const W = {x:Math.round(w0.x), y:Math.round(w0.y - 150)};
      await moveTo(W.x,W.y); await sleep(450);
      R.wheelBefore = await kindAt(W.x,W.y);
      await c.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:W.x,y:W.y,deltaX:0,deltaY:150},s);
      await sleep(200);
      const w1 = await opt();
      R.wheelUnder = await kindAt(W.x,W.y);
      const WX = R.wheelUnder === 'OPT' ? W : {x:Math.round(w1.x), y:Math.round(w1.y)};
      await click(WX.x,WX.y);
      await sleep(250);
      R.wheelAsked = await inF('window.__asked');
      }
      // (h) A header button that starts or stops, come under a still pointer:
      // nothing within 400 ms, a click later acts.
      await sleep(400); await inF('LAST_ITEMS=null; renderBubbles([]); 0'); await sleep(100);
      const hb = await inF(`(() => {window.__hdr=0; const h=document.createElement('button'); h.type='button'; h.className='am-btn am-primary'; h.dataset.act='zz-test'; h.textContent='Start'; h.style.cssText='position:fixed;left:40px;top:300px;width:90px;height:32px;z-index:99';
        h.addEventListener('click',()=>{window.__hdr++;}); h.style.display='none'; document.body.appendChild(h); window.__hb=h; return 1;})()`);
      const fr = await ev(`(() => {const a=${F}.getBoundingClientRect(); return {x:Math.round(a.x+85), y:Math.round(a.y+316)};})()`);
      // A finger has no hover: where it last touched is where it is (a double tap).
      R.hdrFirstOn = await kindAt(fr.x,fr.y);
      await moveTo(fr.x,fr.y); if (touch) await click(fr.x,fr.y); await sleep(450);
      await inF(`__hb.style.display=''; 0`); await sleep(80);
      await click(fr.x,fr.y);
      await sleep(150);
      R.hdrEarly = await inF('window.__hdr');
      await sleep(500);
      await click(fr.x,fr.y);
      await sleep(150);
      R.hdrLater = await inF('window.__hdr');
      await inF('__hb.remove(); 0');
      await c.send('Target.closeTarget',{targetId});
      await c.send('Target.disposeBrowserContext',{browserContextId});
    }
  } finally {ch.kill();}
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack);process.exit(1)});
""".replace('__ASK__', json.dumps(ASK))


@unittest.skipUnless(phone.NODE and phone.CHROME, 'needs Node and Chrome')
class ClickMoved(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(phone, 'CDP_JS', JS):
            phone.ThePhone.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        phone.ThePhone.tearDownClass.__func__(cls)

    def test_the_accident_sends_nothing(self):
        g = self.got['1728']
        self.assertEqual(g['accPointOpen'], 'blank', g)                 # empty space while the tool is out
        self.assertEqual(g['accAtPress']['fly'], 'workspace', g)        # the press itself closes nothing
        self.assertEqual(g['accAtPress']['opt']['left'], g['accOpen']['left'], g)
        self.assertEqual(g['accAfterRelease']['fly'], '', g)            # the click put the tool back
        self.assertEqual(g['accAfterRelease']['under'], 'OPT', g)       # the option is now under the pointer
        self.assertEqual(g['accAsked'], 0, g)                           # the second press of the double click: nothing
        self.assertEqual(len(g['accDeliberate']), 1, g)                 # a click meant for it later answers

    def test_strip_tool_click_answers_once(self):
        g = self.got['1728']
        self.assertEqual(g['stripAtPress']['fly'], 'workspace', g)
        self.assertEqual(g['stripAfter']['fly'], '', g)
        self.assertEqual([a['option'] for a in g['stripAfter']['asked']], ['First a short study doc comparing it in depth'], g)

    def test_redraw_during_a_press(self):
        g = self.got['1728']
        self.assertFalse(g['redrawStick'], g)
        self.assertTrue(g['redrawDuring']['held'], g)
        r0 = g['redrawAfter']['r0']
        for k in ('during', 'after'):
            o = g['redrawDuring' if k == 'during' else 'redrawAfter']['opt']
            with self.subTest(k=k):
                self.assertLessEqual(abs(o['top'] - r0['top']), 1, g)
                self.assertLessEqual(abs(o['left'] - r0['left']), 1, g)
        self.assertTrue(g['redrawAfter']['drawn'], g)
        self.assertFalse(g['redrawAfter']['held'], g)
        self.assertEqual(len(g['redrawAfter']['asked']), 1, g)

    def test_catch_up_line_going_keeps_the_card_in_place(self):
        g = self.got['1728']
        self.assertTrue(g['cuLine'], g)
        self.assertFalse(g['cuStick'], g)
        self.assertFalse(g['cu']['line'], g)
        self.assertLessEqual(abs(g['cu']['after']['top'] - g['cu']['before']['top']), 1, g)

    def test_option_come_under_a_still_pointer(self):
        for w in ('1728', '390'):
            g = self.got[w]
            with self.subTest(w=w):
                self.assertFalse(g['underStick'], g)
                self.assertNotEqual(g['underAway'], 'OPT', g)
                self.assertEqual(g['underBack'], 'OPT', g)
                self.assertEqual(g['underAsked'], 0, g)
                self.assertEqual(len(g['underLater']), 1, g)

    def test_first_click_and_key_still_answer(self):
        for w in ('1728', '390'):
            g = self.got[w]
            with self.subTest(w=w):
                self.assertEqual([a['option'] for a in g['first']], ['No, just noting it'], g)
                self.assertEqual(len(g['key']), 1, g)


    def test_sent_undo(self):
        for w in ('1728', '390'):
            g = self.got[w]
            with self.subTest(w=w):
                self.assertEqual(g['undoOffered'], 'Sent:', g)
                self.assertEqual([(u['mid'], u['n']) for u in g['undone']], [('key', 0)], g)
                self.assertFalse(g['undoAfter'], g)
                self.assertTrue(g['expBefore'], g)
                self.assertFalse(g['expAfter'], g)


    def test_own_scroll_then_click_answers(self):
        g = self.got['1728']
        self.assertNotEqual(g['wheelBefore'], 'OPT', g)
        self.assertEqual(g['wheelUnder'], 'OPT', g)     # the wheel brought it under the pointer
        self.assertEqual([a['mid'] for a in g['wheelAsked']], ['wheel'], g)

    def test_header_button_come_under_the_pointer(self):
        for w in ('1728', '390'):
            g = self.got[w]
            with self.subTest(w=w):
                self.assertNotIn(g['hdrFirstOn'], ('OPT', 'ctl'), g)
                self.assertEqual((g['hdrEarly'], g['hdrLater']), (0, 1), g)


if __name__ == '__main__':
    unittest.main()
