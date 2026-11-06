"""Desktop CDP measurements using #167's isolated hub and conversation fixtures."""
import os
from tests import test_phone_focus as focus


class LaptopFocus(focus.PhoneFocus):
    SCRIPT = (focus.JS
              .replace('[360,390,430,768,1280,1440]', '[768,1280,1440,1728]')
              .replace('const height = 844;', 'const height = ({768:800,1280:800,1440:900,1728:1117})[width];')
              .replace('if(!baseline && width<768)', 'if(!baseline)')
              .replace('length:6', 'length:20')
              .replace("id:'focus-ask',from:", "id:'focus-ask',answers:['P1'],from:")
              .replace("const highlighted=marked.classList", "if(!marked)throw Error('missing fixture '+location.href+' '+w.location.href+' '+w.eval('JSON.stringify(LAST_ITEMS)')); const highlighted=marked.classList")
              .replace("d.querySelector('.copy-link').focus()", "d.getElementById('input').focus()")
              .replace("document.getElementById('chatbar').hidden=false;", "document.getElementById('hint').textContent=''; document.getElementById('chatbar').hidden=false;")
              .replace("r.attention={state:'blocked'", "r.attention={state:'waiting_for_you'")
              .replace('const top=document.querySelector', "r.lastAgent='**Report — completed** Reviewed D:/work/projects/security_audit/evidence/plans/codex/LOCKDOWN-PLAN.md and the next steps. Full evidence below.'; const top=document.querySelector")
              .replace('renderBubbles(fixtureItems);', "POINTS=pointMaps({delivered:1,items:[{id:'P1',mid:'focus-1',text:'Confirm the next step',state:'delivered',answers:[{mid:'focus-ask',said:'Ready to check'}]}]}); LAST_ITEMS=fixtureItems; renderBubbles(fixtureItems);")
              .replace('          g.actions=await ev', r"""
          g.desktop=await ev(`(() => {
          const f=document.querySelector('${sel}'),d=f.contentDocument,w=f.contentWindow;
          const b=e=>e.getBoundingClientRect(),m=d.getElementById('msgs'),cd=${CF}.contentDocument,input=cd.getElementById('input'),send=cd.getElementById('send');
          const overlap=()=>[...d.querySelectorAll('#ask-line,#chatbar,#to-latest,.catchup')].some(e=>{
            if(!b(e).height || e.closest('#msgs')) return false;
            return [...d.querySelectorAll('#msgs .msg')].some(msg=>{const r=b(msg),q=b(e),v=b(m);return Math.max(r.top,v.top,q.top)<Math.min(r.bottom,v.bottom,q.bottom) && Math.max(r.left,q.left)<Math.min(r.right,q.right);});
          });
          const overlaps=[];m.scrollTop=0;overlaps.push(overlap());m.scrollTop=m.scrollHeight;overlaps.push(overlap());input.focus();overlaps.push(overlap());input.blur();
          const header=${kind!=='po'}?document.querySelector('#detail-panel .dp-top'):null;
          const fresh=document.querySelector('#detail-panel .dp-summary-phone summary');
          // A docked task's header does not name it again: its tab above does (#261).
          const elements=header?[...header.querySelectorAll('.dp-meta,.dp-actions')]:[...document.querySelectorAll('#po-panel .po-name,#po-panel .po-sub')];
          const titleShown=!!header && !!header.querySelector('h2') && b(header.querySelector('h2')).width>0;
          return {overlaps,titleShown,scrolls:m.scrollHeight>m.clientHeight,
            composerOneRow:Math.abs((b(input).top+b(input).bottom)/2-(b(send).top+b(send).bottom)/2)<1,
            header:elements.map(e=>({y:b(e).top,h:b(e).height,w:b(e).width})),
            rendered:!header || !!fresh.querySelector('strong'),
            shortPath:!header || fresh.querySelector('.file-link')?.textContent==='LOCKDOWN-PLAN.md',
            fullPath:!header || fresh.querySelector('.file-link')?.getAttribute('href').includes('security_audit'),
            };
        })()`);
          g.actions=await ev
""")
              .replace('out.push(g);', r"""
        if(!baseline && kind!=='po') g.headerStates=await ev(`(() => {
          const r=rowBySid(SELECTED_SID), saved={...r}, attention=ATTENTION_BY_ROOM.get(r.roomId), out=[];
          const top=document.querySelector('#detail-panel .dp-top');
          const b=e=>e.getBoundingClientRect();
          ATTENTION_BY_ROOM.delete(r.roomId);
          for(const state of ['gone','working','po','you']) for(const age of [20,87*60,9*86400,400*86400]) for(const priority of [1,2,3,4,5]) {
            r.isLive=state!=='gone';r.status=state==='working'?'busy':state==='gone'?'stopped':'idle';r.priority=priority;
            r.attention=state==='gone'?{state:'agent_gone'}:state==='you'?{state:'waiting_for_you'}:null;
            r.waitingOnPo=state==='po'?{kind:'question',since:1}:null;r.updatedAt=Date.now()/1000-age;
            top.innerHTML=detailHead(r,true)+detailMeta(r,r.isLive,true,Date.now()/1000)+detailActions(r,r.isLive)+detailSummary(r,true);
            // Docked, the header does not name the task: its tab does (#261).
            const nodes=[...top.querySelector('.dp-meta').children,top.querySelector('.dp-actions')].filter(e=>b(e).width>0);
            const rects=nodes.map(b), frame=b(top);
            out.push({state,age,priority,titleShown:b(top.querySelector('h2')).width>0,
              fits:rects.every(x=>x.left>=frame.left && x.right<=frame.right),
              overlap:rects.some((x,i)=>rects.slice(i+1).some(y=>Math.max(x.left,y.left)<Math.min(x.right,y.right)-.5 && Math.max(x.top,y.top)<Math.min(x.bottom,y.bottom)-.5)),
              oneLine:Math.max(...rects.map(x=>(x.top+x.bottom)/2))-Math.min(...rects.map(x=>(x.top+x.bottom)/2))<1});
          }
          Object.assign(r,saved);if(attention)ATTENTION_BY_ROOM.set(r.roomId,attention);
          top.innerHTML=detailHead(r,true)+detailMeta(r,r.isLive,true,Date.now()/1000)+detailActions(r,r.isLive)+detailSummary(r,true);fitLiveChat();
          return out;
        })()`);
        if(!baseline && A.shots && width===768 && kind!=='po') {
          await ev(`(() => {
            const r={...rowBySid(SELECTED_SID),isLive:false,status:'stopped',attention:{state:'agent_gone'},waitingOnPo:null,updatedAt:Date.now()/1000-87*60};
            document.querySelector('#detail-panel .dp-top').innerHTML=detailHead(r,true)+detailMeta(r,false,true,Date.now()/1000)+detailActions(r,false)+detailSummary(r,true);
          })()`);
          const shot=await c.send('Page.captureScreenshot',{format:'png'},s);
          fs.writeFileSync(path.join(A.shots,`${kind}-stopped-${width}-${theme}.png`),Buffer.from(shot.data,'base64'));
        }
        out.push(g);
"""))

    def test_conversation_geometry(self):
        if os.environ.get('ENSEMBLE_FOCUS_BEFORE'):
            return
        for g in self.got:
            with self.subTest(width=g['width'], theme=g['theme'], kind=g['kind']):
                # The shared message box under the chats (f0a5f87) is taller than the
                # chat's own box was (a "To:" line over an 80px-min frame), on purpose:
                # the PO set the bars to that design (#312), 70% at 1728 and 55% at 768.
                self.assertGreaterEqual(g['share'], .70 if g['width'] == 1728 else .55)
                self.assertLessEqual(g['scrollW'], g['width'])
                self.assertFalse(g['overlap'])
                self.assertFalse(g['balloonOverflow'])
                self.assertTrue(g['selected'])
                a = g['actions']
                self.assertTrue(a['highlighted'] and a['cleared'] and a['jumped'])
                self.assertEqual(a['overlaps'], [False, False, False])
                self.assertIn('inset', a['ring'])
                self.assertGreater(a['expanded'], 44)
                self.assertLessEqual(a['expanded'], 88)
                self.assertLessEqual(a['focusedBottom'], a['frameHeight'] + 1)
                self.assertTrue(a['filter'])
                self.assertLess(len(a['placeholder']), 30)
                self.assertTrue(all(g['menuDismiss'].values()), g['menuDismiss'])
                desktop = g['desktop']
                self.assertEqual(desktop['overlaps'], [False, False, False])
                self.assertTrue(desktop['scrolls'] and desktop['composerOneRow'])
                self.assertTrue(desktop['rendered'] and desktop['shortPath'] and desktop['fullPath'])
                if desktop['header']:
                    centers = [e['y'] + e['h']/2 for e in desktop['header']]
                    self.assertLess(max(centers) - min(centers), 1)
                    self.assertTrue(all(e['w'] > 0 for e in desktop['header']))
                self.assertFalse(desktop['titleShown'], 'the tab names a docked task, not its header (#261)')
                if g['kind'] != 'po':
                    for case in g['headerStates']:
                        self.assertFalse(case['titleShown'], case)
                        self.assertTrue(case['fits'] and case['oneLine'] and not case['overlap'], case)
                    # Desktop keeps End beside the metadata; phone puts it in the menu.
                    self.assertGreater(g['report']['after'], g['report']['before'])
                    self.assertTrue(g['report']['same'] and g['report']['held'] and g['report']['updated'], g['report'])
                    self.assertGreater(g['report']['scroll'], 0)
                    self.assertFalse(g['report']['initialOpen'])
