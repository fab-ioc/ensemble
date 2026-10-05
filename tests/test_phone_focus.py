"""Phone focus geometry against the isolated hub used by test_phone_layout.

The browser fixture supplies a blocked report and a long open ask to the real
renderers. ENSEMBLE_FOCUS_BEFORE=1 records the original layout without assertions.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tests import test_phone_layout as phone

JS = phone.HEAD + r"""
async function main() {
  const {ch, ws} = await launch(); const c = new Cdp(ws); await c.open();
  const out = [];
  const baseline = !!process.env.ENSEMBLE_FOCUS_BEFORE;
  try {
    for (const width of [360,390,430,768,1280,1440]) for (const theme of ['light','dark','fjord']) {
      const height = 844;
      const {targetId} = await c.send('Target.createTarget',{url:'about:blank'});
      const {sessionId:s} = await c.send('Target.attachToTarget',{targetId,flatten:true});
      await c.send('Page.enable',{},s);
      await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<768},s);
      await c.send('Emulation.setTouchEmulationEnabled',{enabled:width<768,maxTouchPoints:5},s);
      const ev = async expression => {const r = await c.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},s); if(r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value;};
      const until = async ex => {for(let i=0;i<150;i++){try{if(await ev(ex))return;}catch{} await sleep(100);}throw Error('timeout '+ex);};
      await c.send('Page.navigate',{url:A.base+'/'},s);
      await until(`typeof ALL_ROWS !== 'undefined' && ALL_ROWS.some(r=>r.roomId===${JSON.stringify(A.task)})`);
      await ev(`document.documentElement.dataset.theme=${JSON.stringify(theme)}; document.querySelector('#sw-list .sw-row[data-room="${A.task}"]').click()`);
      for (const kind of ['task','po','unassigned']) {
        if(kind==='po') await ev(`document.getElementById('bar-back').click(); document.querySelector('#sw-list .sw-row[data-po="${A.proj}"]').click()`);
        if(kind==='unassigned') await ev(`document.getElementById('bar-back').click(); document.querySelector('#sw-list .sw-row[data-room="${A.loose}"]').click()`);
        const sel=kind!=='po'?'#detail-panel iframe.dp-session':'#po-panel iframe.po-session:not([hidden])';
        await until(`document.querySelector('${sel}')?.contentWindow.document.getElementById('input')`);
        await sleep(600);
        await ev(`(() => {
          const f=document.querySelector('${sel}'), w=f.contentWindow;
          w.eval(\`tick=()=>{}; ROOM_OBJ = {...ROOM_OBJ, openAsk:{id:'focus-ask',ts:Date.now()/1000,line:'Initial read-only review finished; account compromise and final hardening remain pending user inputs. Three genuine service emails arrived overnight. Was the July password change yours and do you recognize these devices?'}};
            SOLO_MODE=${kind!=='po'}; TEAM_ALL=false; CU_OPENED=true; STICK=false;
            const fixtureItems=[{id:'focus-ask',from:'codex',to:'user',ts:Date.now()/1000,text:'Initial read-only review finished.\\\\n\\\\nTo continue: **was the July password change yours; did you use either overnight link; do you recognize these devices?**\\\\n\\\\nPlease confirm the next step.'}, ...Array.from({length:6},(_,i)=>({id:'focus-'+i,from:i%2?'user':'codex',to:'user',ts:Date.now()/1000+i+10,text:'Conversation context '+i+' — reviewing the evidence and the next action.'}))];
            renderBubbles(fixtureItems); showAskLine(fixtureItems); composePlaceholder(false,true);
            document.getElementById('msgs').scrollTop=0;
            document.getElementById('chatbar').hidden=false;
            document.getElementById('chatbar-note').textContent='Your messages and the answers to you · no team activity';
          \`);
          w.document.documentElement.dataset.theme=${JSON.stringify(theme)};
          if(${kind!=='po'}) {
            const r=rowBySid(SELECTED_SID); r.workflow='inprogress'; r.isLive=true; r.status='idle'; r.lastAgent='**Report — blocked** Initial read-only review finished; account compromise and final hardening remain pending user inputs. Three genuine service emails arrived overnight.';
            r.attention={state:'blocked',reason:'Waiting for your answer'};
            const top=document.querySelector('#detail-panel .dp-top'); top.innerHTML=detailHead(r,true)+detailMeta(r,true,true,Date.now()/1000)+detailActions(r,true)+detailSummary(r,true);
            fitLiveChat();
          }
        })()`);
        await sleep(150);
        const g=await ev(`(() => {
          const f=document.querySelector('${sel}'),d=f.contentDocument,w=f.contentWindow;
          const box=e=>{const b=e.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height,b:b.bottom,r:b.right}};
          const visible=e=>!!e && e.getBoundingClientRect().width>0 && e.getBoundingClientRect().height>0 && getComputedStyle(e).visibility!=='hidden' && e.getBoundingClientRect().x < e.ownerDocument.defaultView.innerWidth && e.getBoundingClientRect().right > 0;
          const m=box(d.getElementById('msgs')), ask=box(d.getElementById('ask-line'));
          const controls=[...document.querySelectorAll('header button,.dp-meta button,#po-dock .dk-tab,#po-panel .po-head button,.dp-summary-phone summary'),...d.querySelectorAll('#compose button,#compose summary,#ask-line a,#ask-line summary,#team-all')].filter(visible);
          const tab=[...document.querySelectorAll('#po-dock .dk-tab.on')].find(visible);
          const selected=tab && getComputedStyle(tab).boxShadow!=='none';
          return {width:${width},theme:${JSON.stringify(theme)},kind:${JSON.stringify(kind)},messages:m,share:m.h/${height},ask,frame:box(f),selected,underTabs:tab && box(f).y>=box(tab).b,scrollW:Math.max(document.documentElement.scrollWidth,d.documentElement.scrollWidth),small:controls.filter(e=>{const b=box(e);return b.w<43.9||b.h<43.9}).map(e=>[e.id||e.className,box(e)]),overlap:ask.b>m.y+.5,balloonOverflow:[...d.querySelectorAll('#msgs .msg')].some(b=>b.scrollHeight>b.clientHeight+1),composer:box(d.getElementById('compose'))};
        })()`);
        if(A.shots) {const shot=await c.send('Page.captureScreenshot',{format:'png'},s);fs.writeFileSync(path.join(A.shots,`${kind}-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));}
        if(!baseline && width<768) {
          g.actions=await ev(`(() => {
            const f=document.querySelector('${sel}'),w=f.contentWindow,d=f.contentDocument;
            const box=d.getElementById('msgs'),ask=d.getElementById('ask-line');
            const hit=()=>{const r=box.getBoundingClientRect();return [...d.querySelectorAll('#msgs .msg')].some(b=>{const t=b.getBoundingClientRect();const y=Math.max(r.top,t.top)+1;return y<Math.min(r.bottom,t.bottom) && !b.contains(d.elementFromPoint(t.left+8,y));});};
            const marked=d.querySelector('.msg[data-mid="focus-ask"]');
            const highlighted=marked.classList.contains('needs-answer');
            const overlaps=[hit()]; box.scrollTop=box.scrollHeight; overlaps.push(hit());
            ask.querySelector('a').click();
            const jumped=marked.getBoundingClientRect().top>=box.getBoundingClientRect().top-1 && marked.getBoundingClientRect().top<box.getBoundingClientRect().bottom;
            overlaps.push(hit());
            const input=d.getElementById('input'); input.value='Line one\\nLine two\\nLine three'; input.dispatchEvent(new Event('input',{bubbles:true})); input.focus();
            const ring=w.getComputedStyle(d.getElementById('compose')).boxShadow;
            const focusedBottom=d.getElementById('compose').getBoundingClientRect().bottom;
            const expanded=input.getBoundingClientRect().height; input.blur(); input.value=''; input.dispatchEvent(new Event('input',{bubbles:true}));
            const tools=d.querySelector('.phone-compose-tools'); tools.open=true;
            const menuTargets=[...tools.querySelectorAll('button')].map(b=>[b.getBoundingClientRect().width,b.getBoundingClientRect().height]);
            tools.querySelector('[data-phone-action="team-all"]').click();
            const filter=w.eval('TEAM_ALL');
            w.eval('ROOM_OBJ.openAsk=null; showAskLine(LAST_ITEMS)');
            return {highlighted,jumped,overlaps,ring,expanded,focusedBottom,frameHeight:f.clientHeight,menuTargets,filter,cleared:!marked.classList.contains('needs-answer'),placeholder:input.placeholder,taskActions:${kind!=='po'}?pdMenuItems({}).map(i=>i.id):[]};
          })()`);
          const menuSetup=`(() => { const d=document.querySelector('${sel}').contentDocument; const menu=d.querySelector('.phone-compose-tools'); menu.querySelector('summary').focus(); menu.open=true; })()`;
          await ev(menuSetup);
          await c.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},s);
          await c.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27},s);
          g.menuDismiss=await ev(`(() => {const d=document.querySelector('${sel}').contentDocument,m=d.querySelector('.phone-compose-tools');return {escape:!m.open,focus:d.activeElement===m.querySelector('summary')};})()`);
          await ev(menuSetup);
          const point=await ev(`(() => {const f=document.querySelector('${sel}'),b=f.getBoundingClientRect(),i=f.contentDocument.getElementById('input').getBoundingClientRect();return {x:b.left+i.left+i.width/2,y:b.top+i.top+i.height/2};})()`);
          await c.send('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1},s);
          await c.send('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1},s);
          Object.assign(g.menuDismiss, await ev(`(() => {const d=document.querySelector('${sel}').contentDocument,m=d.querySelector('.phone-compose-tools');const outside=!m.open;d.getElementById('input').blur();m.open=true;d.querySelector('.copy-link').focus();return {outside,focusOut:!m.open};})()`));
          if(kind!=='po') {
            await sleep(80);
            g.report=await ev(`(() => {
              const r=rowBySid(SELECTED_SID);r.lastAgent='Long report. '+('Evidence and next steps. '.repeat(120));renderDetail();
              const report=document.querySelector('.dp-summary-phone'),body=report.querySelector('.dp-summary-t');
              const initialOpen=report.open;
              const before=report.getBoundingClientRect().height;report.querySelector('summary').click();
              const after=report.getBoundingClientRect().height;body.scrollTop=64;const scroll=body.scrollTop;
              r.status='busy';renderDetail();const same=document.querySelector('.dp-summary-phone')===report;
              const held=report.open && body.scrollTop===scroll;
              r.lastAgent+=' Updated report.';renderDetail();const updated=report.open && body.scrollTop===scroll && body.textContent.endsWith('Updated report.');
              // Leave it open: the next task must start with a fresh disclosure.
              return {before,after,same,held,updated,scroll,initialOpen};
            })()`);
            if(width===360) g.metaCases=await ev(`(() => {
              const r=rowBySid(SELECTED_SID),saved={...r},attention=ATTENTION_BY_ROOM.get(r.roomId),out=[];
              ATTENTION_BY_ROOM.delete(r.roomId);
              for(const waiting of ['po','you','gone']) for(const run of ['idle','busy','stopped']) for(const priority of [1,2,3,4,5]) {
                r.attention=waiting==='po'?null:{state:waiting==='you'?'waiting_for_you':'agent_gone'};
                r.waitingOnPo=waiting==='po'?{kind:'question',since:1}:null;r.isLive=run!=='stopped';r.status=run;r.priority=priority;
                document.querySelector('#detail-panel .dp-meta').outerHTML=detailMeta(r,r.isLive,true,Date.now()/1000);
                const meta=document.querySelector('#detail-panel .dp-meta'),b=meta.getBoundingClientRect();
                const children=[...meta.children].filter(e=>e.getBoundingClientRect().width);
                out.push({waiting,run,priority,fits:meta.scrollWidth<=meta.clientWidth && children.every(e=>e.getBoundingClientRect().right<=innerWidth),oneLine:children.every(e=>Math.abs((e.getBoundingClientRect().top+e.getBoundingClientRect().bottom)/2-(b.top+b.bottom)/2)<1)});
              }
              Object.assign(r,saved);if(attention)ATTENTION_BY_ROOM.set(r.roomId,attention);renderDetail();return out;
            })()`);
          }
        }
        out.push(g);
      }
      await c.send('Target.closeTarget',{targetId});
    }
  } finally {ch.kill();}
  if(A.shots) fs.writeFileSync(path.join(A.shots,'measurements.json'),JSON.stringify(out,null,2));
  console.log(JSON.stringify(out));
}
main().catch(e=>{console.error(e.stack);process.exit(1)});
"""

@unittest.skipUnless(phone.NODE and phone.CHROME, 'needs Node and Chrome')
class PhoneFocus(unittest.TestCase):
    SCRIPT = JS
    @classmethod
    def setUpClass(cls):
        # Serve baseline HTML from git through the same isolated hub. Assets and
        # APIs still come from this worktree; never touch the live hub or its state.
        ref = os.environ.get('ENSEMBLE_FOCUS_BASE_REF')
        if ref:
            original = phone.dashboard.Handler._send_file
            directory = tempfile.TemporaryDirectory(prefix='ens-focus-baseline-')
            cls.addClassCleanup(directory.cleanup)
            for name in ('index.html', 'session.html'):
                data = subprocess.check_output(['git', 'show', f'{ref}:{name}'], cwd=phone.ROOT)
                (Path(directory.name) / name).write_bytes(data)
            def serve(handler, path, *args, **kwargs):
                saved = Path(directory.name) / Path(path).name
                return original(handler, saved if saved.exists() else path, *args, **kwargs)
            patch = mock.patch.object(phone.dashboard.Handler, '_send_file', serve)
            patch.start()
            cls.addClassCleanup(patch.stop)
        with mock.patch.object(phone, 'CDP_JS', cls.SCRIPT):
            phone.ThePhone.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        phone.ThePhone.tearDownClass.__func__(cls)

    def test_conversation_geometry(self):
        if os.environ.get('ENSEMBLE_FOCUS_BEFORE'):
            return
        for g in self.got:
            with self.subTest(width=g['width'],theme=g['theme'],kind=g['kind']):
                if g['width'] >= 768:
                    self.assertGreater(g['frame']['h'], 0)
                    continue
                self.assertGreaterEqual(g['share'], .60)
                self.assertLessEqual(g['scrollW'], g['width'])
                self.assertFalse(g['overlap'])
                self.assertFalse(g['balloonOverflow'])
                self.assertTrue(g['selected'] and g['underTabs'])
                self.assertEqual(g['small'], [])
                a = g['actions']
                self.assertTrue(a['highlighted'] and a['cleared'])
                self.assertTrue(a['jumped'])
                self.assertEqual(a['overlaps'], [False, False, False])
                self.assertIn('inset', a['ring'])
                self.assertGreater(a['expanded'], 44)
                self.assertLessEqual(a['expanded'], 88)
                self.assertLessEqual(a['focusedBottom'], a['frameHeight'] + 1)
                self.assertTrue(all(w >= 44 and h >= 44 for w,h in a['menuTargets']))
                self.assertTrue(a['filter'])
                self.assertLess(len(a['placeholder']), 30)
                self.assertTrue(all(g['menuDismiss'].values()), g['menuDismiss'])
                if g['kind'] != 'po':
                    self.assertIn('end', a['taskActions'])
                    self.assertGreater(g['report']['after'], g['report']['before'])
                    self.assertTrue(g['report']['same'] and g['report']['held'] and g['report']['updated'],g['report'])
                    self.assertGreater(g['report']['scroll'], 0)
                    self.assertFalse(g['report']['initialOpen'])
                    for case in g.get('metaCases', []):
                        self.assertTrue(case['fits'] and case['oneLine'],case)
