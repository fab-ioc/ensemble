"""Real CDP pointer sequences against an isolated dashboard hub."""
import json
import unittest
from unittest import mock

from tests import test_phone_layout as phone


JS = phone.HEAD + r"""
async function main() {
  const {ch, ws} = await launch(); const c = new Cdp(ws); await c.open();
  const out = [];
  try {
    for (const [width, height, touch] of [[1280,800,false],[1728,1117,false],[390,844,true]]) {
      for (const kind of ['task','po']) {
        const {targetId} = await c.send('Target.createTarget',{url:'about:blank'});
        const {sessionId:s} = await c.send('Target.attachToTarget',{targetId,flatten:true});
        await c.send('Page.enable',{},s);
        await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:touch},s);
        await c.send('Emulation.setTouchEmulationEnabled',{enabled:touch,maxTouchPoints:5},s);
        if(width===1728 && kind==='po') await c.send('Page.addScriptToEvaluateOnNewDocument',{source:"(()=>{const native=CSS.supports.bind(CSS);CSS.supports=(p,v)=>p==='field-sizing'&&v==='content'?false:native(p,v);})();"},s);
        const ev = async expression => {const r=await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
        const until = async expression => {for(let i=0;i<150;i++){try{if(await ev(expression))return;}catch{}await sleep(100);}throw Error('timeout '+expression);};
        await c.send('Page.navigate',{url:A.base+'/session?room='+encodeURIComponent(kind==='task'?A.task:A.po)},s);
        await until("!!document.getElementById('input') && !!ROOM_OBJ && !document.getElementById('send').disabled");
        await ev(`(() => {
          window.__refresh=refresh; tick=()=>{}; refresh=async()=>{};
          window.__sent=[]; window.__asked=[]; window.__trace=[];
          const old=window.fetch;
          window.fetch=(url,opt)=>{
            if(String(url)==='/api/room/resume' && opt && opt.method==='POST') {
              window.__sent.push(JSON.parse(opt.body));
              return Promise.resolve(new Response(JSON.stringify({send:null}),{status:200,headers:{'Content-Type':'application/json'}}));
            }
            if(String(url)==='/api/room/ask' && opt && opt.method==='POST') {
              window.__asked.push(JSON.parse(opt.body));
              return Promise.resolve(new Response(JSON.stringify({}),{status:200,headers:{'Content-Type':'application/json'}}));
            }
            return old(url,opt);
          };
          const b=document.getElementById('send');
          for(const t of ['pointerdown','mousedown','mouseup','click']) document.addEventListener(t,e=>{
            const r=b.getBoundingClientRect();
            window.__trace.push({type:t,target:e.target.id||e.target.className||e.target.tagName,x:e.clientX,y:e.clientY,buttonY:r.y,buttonH:r.height,inputH:document.getElementById('input').getBoundingClientRect().height,focus:document.activeElement.id});
          },true);
          document.getElementById('input').focus();
        })()`);
        await c.send('Input.insertText',{text:'First line\nSecond line\nThird line'},s);
        const before=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect(),i=document.getElementById('input').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2,inputH:i.height,buttonY:b.y};})()`);
        const blurred=await ev(`(() => {const i=document.getElementById('input');i.blur();const b=document.getElementById('send').getBoundingClientRect(),h=i.getBoundingClientRect().height;i.focus();return {inputH:h,buttonY:b.y};})()`);
        await c.send('Input.dispatchMouseEvent',{type:'mousePressed',x:before.x,y:before.y,button:'left',clickCount:1},s);
        const pressed=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect(),i=document.getElementById('input').getBoundingClientRect();return {buttonY:b.y,inputH:i.height,under:document.elementFromPoint(${before.x},${before.y})?.id||''};})()`);
        await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:before.x,y:before.y,button:'left',clickCount:1},s);
        await sleep(80);
        out.push({width,kind,before,blurred,pressed,fallback:await ev('SEND_SIZE_FALLBACK'),trace:await ev('window.__trace'),sent:await ev('window.__sent'),hint:await ev("document.getElementById('hint').textContent")});
        if(width===390 && kind==='task') {
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Tap line one\nTap line two'},s);
          const tap=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
          await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...tap,id:1}]},s);
          await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},s);
          await sleep(80);
          out.push({kind:'touch-tap',sent:await ev('window.__sent')});
        }
        if(width===1280 && kind==='task') {
          const pressClick = async selector => {
            const p=await ev(`(() => {const b=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
            await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1},s);
            await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1},s);
            await sleep(80);
          };
          // A full room refresh between the two halves of the physical click.
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Poll line one\nPoll line two\nPoll line three'},s);
          const p=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
          await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1},s);
          await ev('window.__refresh()');
          await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1},s);
          await sleep(80);
          out.push({kind:'poll',sent:await ev('window.__sent')});
          await ev("edAddLast(); const ta=document.querySelector('.ed-text'); ta.value='Question one\\nQuestion two'; ta.dispatchEvent(new Event('input',{bubbles:true})); ta.focus(); 0");
          await pressClick('#send');
          out.push({kind:'points',sent:await ev('window.__sent')});
          await ev("(()=>{renderBubbles([{id:'qa1',from:'codex',text:'Ask: What should happen?'}]); const ta=document.querySelector('.qa-cm textarea'); ta.value='Please proceed.'; ta.dispatchEvent(new Event('input',{bubbles:true})); document.querySelector('.qa-send').scrollIntoView();})()");
          await pressClick('.qa-send');
          out.push({kind:'quick-answer',asked:await ev('window.__asked')});
          await ev(`(() => {
            window.__asked=[];
            renderBubbles([{id:'qa-ime',from:'codex',text:'Ask: Your composed answer?'}]);
            const ta=document.querySelector('.qa-cm textarea');
            ta.value='Composed'; ta.dispatchEvent(new Event('input',{bubbles:true}));
            ta.focus(); ta.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
          })()`);
          await pressClick('.qa-send');
          const qaImeBefore=await ev('window.__asked.length');
          await ev(`(() => {
            const ta=document.querySelector('.qa-cm textarea');
            ta.value='Composed final answer';
            ta.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));
            ta.dispatchEvent(new Event('input',{bubbles:true}));
          })()`);
          await sleep(80);
          out.push({kind:'quick-answer-ime',before:qaImeBefore,asked:await ev('window.__asked')});
          await ev("ROOM_SENDS=[{key:'retry-test',text:'Please retry',state:'failed',error:'offline'}]; renderBubbles(LAST_ITEMS || []); document.querySelector('.send-retry').scrollIntoView(); 0");
          await pressClick('.send-retry');
          out.push({kind:'retry',sent:await ev('window.__sent')});
          await ev("edClear(); document.getElementById('input').blur(); 0");
          await pressClick('#send');
          out.push({kind:'empty',hint:await ev("document.getElementById('hint').textContent")});
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Selection line one\nSelection line two'},s);
          await ev(`(() => {
            renderBubbles([{id:'selection-case',from:'codex',text:'Select these words for a comment.'}]);
            const n=document.createTreeWalker(document.querySelector('#msgs .msg[data-mid="selection-case"] .text'),NodeFilter.SHOW_TEXT).nextNode();
            const r=document.createRange();r.setStart(n,0);r.setEnd(n,Math.min(6,n.textContent.length));
            const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
            document.getElementById('input').blur();
            document.dispatchEvent(new Event('selectionchange'));
          })()`);
          await sleep(380);
          const selectionOpen=await ev('selShown()');
          await pressClick('#send');
          out.push({kind:'selection-bar',open:selectionOpen,sent:await ev('window.__sent')});
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Chip line one\nChip line two'},s);
          const cardOpen=await ev(`(() => {
            const host=document.querySelector('#msgs');
            host.insertAdjacentHTML('beforeend',TaskCard.chipHtml({ref:'#1',label:'#1',roomId:ROOM,title:'A task',agents:[]}));
            TaskCard.open(host.querySelector('.task-chip:last-child'),document);
            return !!document.querySelector('.task-card');
          })()`);
          await pressClick('#send');
          out.push({kind:'chip-card',open:cardOpen,sent:await ev('window.__sent')});
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Upload waiting'},s);
          await ev('ATT._busyBefore=ATT.busy; ATT.busy=()=>true; 0');
          await pressClick('#send');
          out.push({kind:'upload-wait',hint:await ev("document.getElementById('hint').textContent"),sent:await ev('window.__sent')});
          await ev('ATT.busy=ATT._busyBefore; 0');
          await ev("edClear(); document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'Keyboard line one\nKeyboard line two'},s);
          await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:2},s);
          await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:2},s);
          await sleep(80);
          out.push({kind:'ctrl-enter',sent:await ev('window.__sent')});
          await ev("document.getElementById('input').focus()");
          await c.send('Input.insertText',{text:'Command line one\nCommand line two'},s);
          await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:4},s);
          await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,modifiers:4},s);
          await sleep(80);
          out.push({kind:'cmd-enter',sent:await ev('window.__sent')});
          await ev(`(() => {const old=window.fetch; window.__oldFetch=old; window.__blocked=[];
            window.fetch=(url,opt)=>{if(String(url)==='/api/room/resume' && opt?.method==='POST'){
              window.__blocked.push(JSON.parse(opt.body));return new Promise(resolve=>window.__resolveSend=resolve);}
              return old(url,opt);};
            document.getElementById('input').focus();
          })()`);
          await c.send('Input.insertText',{text:'Only one request\nEven after two presses'},s);
          await pressClick('#send');
          await pressClick('#send');
          await ev("document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'This edit must be blocked'},s);
          await pressClick('#send');
          const blocked=await ev(`({count:window.__blocked.length,hint:document.getElementById('hint').textContent,
            draft:document.getElementById('input').value,readOnly:document.getElementById('input').readOnly,
            disabled:document.getElementById('send').disabled,inert:document.getElementById('compose').inert})`);
          await ev("window.__resolveSend(new Response(JSON.stringify({send:null}),{status:200,headers:{'Content-Type':'application/json'}})); 0");
          await sleep(80);
          out.push({kind:'in-flight',...blocked});
          await ev("window.fetch=window.__oldFetch; document.getElementById('input').focus(); document.getElementById('input').dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); 0");
          await c.send('Input.insertText',{text:'Composed line one\nComposed line two'},s);
          await pressClick('#send');
          const composingBefore=await ev('window.__sent.length');
          await ev("document.getElementById('input').dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})); 0");
          await sleep(80);
          out.push({kind:'ime',before:composingBefore,sent:await ev('window.__sent')});
          await ev("edClear(); document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'Drag source'},s);
          const drag=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
          await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...drag,button:'left',clickCount:1},s);
          await sleep(80);
          await c.send('Input.dispatchMouseEvent',{type:'mouseMoved',x:2,y:2,button:'left',buttons:1},s);
          await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:2,y:2,button:'left',clickCount:1},s);
          await ev("document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'Keyboard after drag cancel'},s);
          await ev("document.getElementById('send').click(); 0");
          await sleep(80);
          out.push({kind:'drag-cancel-keyboard',sent:(await ev('window.__sent')).slice(-2),press:await ev('SEND_PRESS')});
        }
        if(width===390 && kind==='task') {
          await ev("edClear(); document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'Cancelled touch source'},s);
          const cancel=await ev(`(() => {const b=document.getElementById('send').getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2};})()`);
          await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...cancel,id:7}]},s);
          await sleep(80);
          await c.send('Input.dispatchTouchEvent',{type:'touchCancel',touchPoints:[]},s);
          await ev("document.getElementById('input').focus(); 0");
          await c.send('Input.insertText',{text:'Keyboard after touch cancel'},s);
          await ev("document.getElementById('send').click(); 0");
          await sleep(80);
          out.push({kind:'touch-cancel-keyboard',sent:(await ev('window.__sent')).slice(-2),press:await ev('SEND_PRESS')});
        }
        await c.send('Target.closeTarget',{targetId});
      }
    }
    // The same session page when the dashboard owns its frame, including a
    // separate Dock chat panel. A click inside the frame also wakes the Dock's
    // outside-click handler through chat-clicked.
    for (const [width,height,touch] of [[1280,800,false],[1728,1117,false],[390,844,true]]) {
      const {browserContextId}=await c.send('Target.createBrowserContext');
      const {targetId}=await c.send('Target.createTarget',{url:'about:blank',browserContextId});
      const {sessionId:s}=await c.send('Target.attachToTarget',{targetId,flatten:true});
      await c.send('Page.enable',{},s);
      await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:touch},s);
      await c.send('Emulation.setTouchEmulationEnabled',{enabled:touch,maxTouchPoints:5},s);
      const ev=async expression=>{const r=await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
      const until=async expression=>{for(let i=0;i<180;i++){try{if(await ev(expression))return;}catch{}await sleep(100);}throw Error('timeout '+expression);};
      await c.send('Page.navigate',{url:A.base+'/'},s);
      await until(`typeof ALL_ROWS!=='undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.task)})`);
      const frameSend=async (label,selector,setup,tap=false) => {
        await until(`!!document.querySelector(${JSON.stringify(selector)})?.contentWindow?.eval('ROOM_OBJ') && !document.querySelector(${JSON.stringify(selector)}).contentDocument.getElementById('send').disabled`);
        await ev(`(() => {const w=document.querySelector(${JSON.stringify(selector)}).contentWindow;
          w.eval(\`tick=()=>{}; refresh=async()=>{}; window.__sent=[]; window.__trace=[];
            for(const t of ['pointerdown','mousedown','mouseup','click']) document.addEventListener(t,e=>window.__trace.push([t,e.target.id||e.target.className||e.target.tagName,e.clientX,e.clientY]),true);
            const old=window.fetch;
            window.fetch=(url,opt)=>{if(String(url)==='/api/room/resume' && opt?.method==='POST'){
              window.__sent.push(JSON.parse(opt.body));return Promise.resolve(new Response(JSON.stringify({send:null}),{status:200,headers:{'Content-Type':'application/json'}}));}
              return old(url,opt);};\`);
          w.document.getElementById('input').focus();
        })()`);
        await c.send('Input.insertText',{text:'Embedded line one\nEmbedded line two\nEmbedded line three'},s);
        await sleep(400);
        if(setup) await setup();
        const flyBefore=await ev('PD.dock?.flyOpen() || ""');
        const p=await ev(`(() => {const f=document.querySelector(${JSON.stringify(selector)}),a=f.getBoundingClientRect(),b=f.contentDocument.getElementById('send').getBoundingClientRect();return {x:a.x+b.x+b.width/2,y:a.y+b.y+b.height/2,inputH:f.contentDocument.getElementById('input').getBoundingClientRect().height,frame:{x:a.x,y:a.y,w:a.width,h:a.height}};})()`);
        if(tap) await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:p.x,y:p.y,id:1}]},s);
        else await c.send('Input.dispatchMouseEvent',{type:'mousePressed',x:p.x,y:p.y,button:'left',clickCount:1},s);
        const pressed=await ev(`(() => {const f=document.querySelector(${JSON.stringify(selector)}),a=f.getBoundingClientRect(),b=f.contentDocument.getElementById('send').getBoundingClientRect();return {frameX:a.x,frameW:a.width,buttonX:a.x+b.x,under:f.contentDocument.elementFromPoint(${p.x}-a.x,${p.y}-a.y)?.id||''};})()`);
        if(tap) await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},s);
        else await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:p.x,y:p.y,button:'left',clickCount:1},s);
        await sleep(80);
        out.push({kind:label,width,inputH:p.inputH,flyBefore,flyAfter:await ev('PD.dock?.flyOpen() || ""'),point:{x:p.x,y:p.y},frame:p.frame,pressed,hit:await ev(`document.elementFromPoint(${p.x},${p.y})?.className||''`),sameHit:await ev(`document.elementFromPoint(${p.x},${p.y})===document.querySelector(${JSON.stringify(selector)})`),innerHit:await ev(`document.querySelector(${JSON.stringify(selector)}).contentDocument.elementFromPoint(${p.x}-document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().x,${p.y}-document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().y)?.id||''`),trace:await ev(`document.querySelector(${JSON.stringify(selector)}).contentWindow.__trace`),sent:await ev(`document.querySelector(${JSON.stringify(selector)}).contentWindow.__sent`)});
      };
      const popSend=async (id,selector,label) => {
        const r=await c.send('Runtime.evaluate',{expression:`PD.dock.popOut(${JSON.stringify(id)})`,returnByValue:true,userGesture:true},s);
        if(!r.result.value) throw Error(label+' pop-out refused');
        await until(`!!PD.dock.popWindow(${JSON.stringify(id)})?.document.querySelector(${JSON.stringify(selector)})`);
        let target=null;
        for(let i=0;i<100 && !target;i++) {
          const info=await c.send('Target.getTargets');
          target=info.targetInfos.find(t=>t.url.includes('/static/dock/src/popout.html'));
          if(!target) await sleep(100);
        }
        if(!target) throw Error(label+' pop-out target missing');
        const {sessionId:pop}=await c.send('Target.attachToTarget',{targetId:target.targetId,flatten:true});
        await c.send('Page.enable',{},pop);
        await c.send('Page.bringToFront',{},pop);
        const pe=async expression=>{const r=await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},pop);if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
        await pe(`(() => {const w=document.querySelector(${JSON.stringify(selector)}).contentWindow;
          w.eval(\`tick=()=>{}; refresh=async()=>{}; window.__sent=[]; const old=window.fetch;
            window.fetch=(url,opt)=>{if(String(url)==='/api/room/resume' && opt?.method==='POST'){
              window.__sent.push(JSON.parse(opt.body));return Promise.resolve(new Response(JSON.stringify({send:null}),{status:200,headers:{'Content-Type':'application/json'}}));}
              return old(url,opt);};\`);
          w.document.getElementById('input').focus();
        })()`);
        await c.send('Input.insertText',{text:'Pop-out line one\nPop-out line two\nPop-out line three'},pop);
        await sleep(400);
        const p=await pe(`(() => {const f=document.querySelector(${JSON.stringify(selector)}),a=f.getBoundingClientRect(),b=f.contentDocument.getElementById('send').getBoundingClientRect();return {x:a.x+b.x+b.width/2,y:a.y+b.y+b.height/2,inputH:f.contentDocument.getElementById('input').getBoundingClientRect().height};})()`);
        await c.send('Input.dispatchMouseEvent',{type:'mousePressed',x:p.x,y:p.y,button:'left',clickCount:1},pop);
        await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:p.x,y:p.y,button:'left',clickCount:1},pop);
        await sleep(80);
        out.push({kind:label,width,inputH:p.inputH,sent:await pe(`document.querySelector(${JSON.stringify(selector)}).contentWindow.__sent`)});
        await ev(`PD.dock.popWindow(${JSON.stringify(id)}).close(); 0`);
        await until(`!PD.dock.isOut(${JSON.stringify(id)})`);
      };
      await ev(`if(!SELECTED_SID) document.querySelector('#sw-list .sw-row[data-room="${A.task}"]').click(); 0`);
      await frameSend('docked-task','#detail-panel iframe.dp-session');
      if(touch) await frameSend('tap-task','#detail-panel iframe.dp-session',null,true);
      if(!touch) {
        if(width===1280) await frameSend('strip-tool','#detail-panel iframe.dp-session',async()=>{
          await ev("PD.dock.setViewMode('changes','unpinned'); PD.dock.openFly('changes'); 0");
          await sleep(250);
        });
        await ev(`pdChatOpen(${JSON.stringify(A.task)}); PD.dock.reveal('chat:'+${JSON.stringify(A.task)}); 0`);
        await frameSend('dock-panel',`#po-dock .pd-chat-x iframe.dp-session[data-room="${A.task}"]`);
        if(width===1280) await popSend('chat:'+A.task,`iframe.dp-session[data-room="${A.task}"]`,'pop-out-task');
      }
      await ev(`document.getElementById('bar-back').click(); 0`);
      await ev(`document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]').click(); 0`);
      await frameSend('docked-po','#po-panel iframe.po-session:not([hidden])');
      if(touch) await frameSend('tap-po','#po-panel iframe.po-session:not([hidden])',null,true);
      if(width===1280) await popSend('po-chat','iframe.po-session.pd-own','pop-out-po');
      if(width===1280) {
        const indexPoint=await ev(`(() => {
          const host=document.createElement('div'); host.id='index-send-probe';
          host.innerHTML=SessionActions.actionBarHtml(SessionActions.sessionActions(
            {kind:'raw',sessionId:'raw-probe',pid:4242,agent:'codex',live:true},
            {hub:true,features:{send:true,focus:true,themes:true,geometry:true},terminalName:'terminal',fileManagerName:'files'}));
          document.body.appendChild(host);
          window.__indexSendCalls=[]; window.__indexPrompted=0;
          window.__indexOldApi=api; window.__indexOldPrompt=prompt;
          window.api=async(path,opt)=>{window.__indexSendCalls.push({path,body:JSON.parse(opt.body)});return {result:'ok'};};
          window.prompt=()=>{window.__indexPrompted++;return 'First click from the dashboard';};
          host.querySelector('.am-more').click();
          const b=document.querySelector('.am-menu:not([hidden]) .send-btn'), r=b.getBoundingClientRect();
          window.__indexTrace=[];
          for(const t of ['pointerdown','mousedown','mouseup','click']) document.addEventListener(t,e=>{
            if(e.target.closest?.('.send-btn')===b) window.__indexTrace.push({type:t,x:e.clientX,y:e.clientY});
          },true);
          return {x:r.x+r.width/2,y:r.y+r.height/2};
        })()`);
        await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...indexPoint,button:'left',clickCount:1},s);
        await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...indexPoint,button:'left',clickCount:1},s);
        await sleep(80);
        out.push({kind:'index-send',width,prompted:await ev('window.__indexPrompted'),calls:await ev('window.__indexSendCalls'),trace:await ev('window.__indexTrace')});
        await ev(`(() => {SessionActions.closeMenu(); window.api=window.__indexOldApi; window.prompt=window.__indexOldPrompt; document.getElementById('index-send-probe').remove();})()`);
      }
      await c.send('Target.closeTarget',{targetId});
      await c.send('Target.disposeBrowserContext',{browserContextId});
    }
  } finally {ch.kill();}
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack);process.exit(1)});
"""


@unittest.skipUnless(phone.NODE and phone.CHROME, 'needs Node and Chrome')
class SendFirstClick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(phone, 'CDP_JS', JS):
            phone.ThePhone.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        phone.ThePhone.tearDownClass.__func__(cls)

    def test_one_click_sends_once(self):
        for case in self.got:
            if case['kind'] in ('poll', 'points', 'quick-answer', 'quick-answer-ime', 'retry', 'empty', 'selection-bar', 'chip-card', 'upload-wait', 'touch-tap', 'ctrl-enter', 'cmd-enter', 'in-flight', 'ime', 'drag-cancel-keyboard', 'touch-cancel-keyboard', 'index-send'):
                continue
            with self.subTest(width=case['width'], kind=case['kind']):
                self.assertGreater(case.get('before', {}).get('inputH', case.get('inputH', 0)), 44, case)
                self.assertEqual(len(case['sent']), 1, case)
                if 'blurred' in case:
                    self.assertEqual(case['before']['inputH'], case['blurred']['inputH'], case)
                    self.assertEqual(case['before']['buttonY'], case['blurred']['buttonY'], case)
                    if case['width'] == 1728 and case['kind'] == 'po':
                        self.assertTrue(case['fallback'], case)
                if case['kind'] == 'strip-tool':
                    self.assertEqual(case['flyBefore'], 'changes', case)
                    self.assertEqual(case['flyAfter'], '', case)

    def test_other_send_controls(self):
        cases = {c['kind']: c for c in self.got if c['kind'] in ('poll', 'points', 'quick-answer', 'quick-answer-ime', 'retry', 'empty', 'selection-bar', 'chip-card', 'upload-wait', 'touch-tap', 'ctrl-enter', 'cmd-enter', 'in-flight', 'ime', 'drag-cancel-keyboard', 'touch-cancel-keyboard', 'index-send')}
        self.assertEqual(len(cases['poll']['sent']), 2, cases['poll'])
        self.assertEqual(len(cases['points']['sent']), 3, cases['points'])
        self.assertIn('## Points', cases['points']['sent'][-1]['text'])
        self.assertEqual(len(cases['quick-answer']['asked']), 1, cases['quick-answer'])
        self.assertEqual(cases['quick-answer-ime']['before'], 0, cases['quick-answer-ime'])
        self.assertEqual(len(cases['quick-answer-ime']['asked']), 1, cases['quick-answer-ime'])
        self.assertEqual(cases['quick-answer-ime']['asked'][0]['comment'], 'Composed final answer', cases['quick-answer-ime'])
        self.assertEqual(cases['retry']['sent'][-1]['key'], 'retry-test')
        self.assertIn('Write a message', cases['empty']['hint'])
        self.assertTrue(cases['selection-bar']['open'], cases['selection-bar'])
        self.assertEqual(len(cases['selection-bar']['sent']), 5, cases['selection-bar'])
        self.assertTrue(cases['chip-card']['open'], cases['chip-card'])
        self.assertEqual(len(cases['chip-card']['sent']), 6, cases['chip-card'])
        self.assertIn('finish uploading', cases['upload-wait']['hint'])
        self.assertEqual(len(cases['upload-wait']['sent']), 6, cases['upload-wait'])
        self.assertEqual(len(cases['touch-tap']['sent']), 2, cases['touch-tap'])
        self.assertEqual(len(cases['ctrl-enter']['sent']), 7, cases['ctrl-enter'])
        self.assertEqual(len(cases['cmd-enter']['sent']), 8, cases['cmd-enter'])
        self.assertEqual(cases['in-flight']['count'], 1, cases['in-flight'])
        self.assertIn('Sending', cases['in-flight']['hint'])
        self.assertTrue(cases['in-flight']['readOnly'] and cases['in-flight']['disabled'] and cases['in-flight']['inert'], cases['in-flight'])
        self.assertNotIn('This edit must be blocked', cases['in-flight']['draft'])
        self.assertEqual(cases['ime']['before'], 8, cases['ime'])
        self.assertEqual(len(cases['ime']['sent']), 9, cases['ime'])
        for kind in ('drag-cancel-keyboard', 'touch-cancel-keyboard'):
            self.assertEqual(len(cases[kind]['sent']), 2, cases[kind])
            self.assertIn('cancel', cases[kind]['sent'][-1]['text'].lower(), cases[kind])
            self.assertIsNone(cases[kind]['press'], cases[kind])
        self.assertEqual(cases['index-send']['prompted'], 1, cases['index-send'])
        self.assertEqual(cases['index-send']['calls'], [{'path': '/api/send', 'body': {'pid': 4242, 'text': 'First click from the dashboard'}}], cases['index-send'])
        self.assertEqual([e['type'] for e in cases['index-send']['trace']], ['pointerdown', 'mousedown', 'mouseup', 'click'], cases['index-send'])
