"""Desktop CDP measurements using #167's isolated hub and conversation fixtures."""
import os
from tests import test_phone_focus as focus


class LaptopFocus(focus.PhoneFocus):
    SCRIPT = (focus.JS
              .replace('[360,390,430,768,1280,1440]', '[768,1280,1440,1728]')
              .replace('const height = 844;', 'const height = ({768:800,1280:800,1440:900,1728:1117})[width];')
              .replace('if(!baseline && width<768)', 'if(!baseline)')
              .replace('length:6', 'length:20')
              .replace("d.querySelector('.copy-link').focus()", "d.getElementById('input').focus()")
              .replace("document.getElementById('chatbar').hidden=false;", "document.getElementById('hint').textContent=''; document.getElementById('activity').hidden=false; document.getElementById('activity').innerHTML='<span class=act>codex idle</span>'; document.getElementById('chatbar').hidden=false;")
              .replace("r.attention={state:'blocked'", "r.attention={state:'waiting_for_you'")
              .replace('const top=document.querySelector', "r.lastAgent='**Report — completed** Reviewed C:/Users/fabio/cs/41_cyber_sec/codex/LOCKDOWN-PLAN.md and the next steps. Full evidence below.'; const top=document.querySelector")
              .replace('renderBubbles(fixtureItems);', "POINTS=pointMaps({delivered:1,items:[{id:'P1',mid:'focus-1',text:'Confirm the next step',state:'delivered',answers:[{mid:'focus-ask',said:'Ready to check'}]}]}); showPointsLine(); LAST_ITEMS=fixtureItems; renderBubbles(fixtureItems);")
              .replace('if(!baseline) {\n          g.actions', r"""
        if(!baseline) g.desktop=await ev(`(() => {
          const f=document.querySelector('${sel}'),d=f.contentDocument,w=f.contentWindow;
          const b=e=>e.getBoundingClientRect(),m=d.getElementById('msgs'),input=d.getElementById('input'),send=d.getElementById('send');
          const points=d.getElementById('points-line');
          const filterRow=Math.abs(b(points).top-b(d.getElementById('chatbar')).top)<1;
          points.querySelector('.pt-sum').click();
          const expanded=!d.getElementById('points-list').hidden;
          const jump=points.querySelector('[data-ref-msg="focus-1"]');
          if(jump) jump.click();
          const target=d.querySelector('[data-mid="focus-1"]');
          const pointJump=!!jump && !!target && b(target).top>=b(m).top-1 && b(target).top<b(m).bottom;
          points.querySelector('.pt-sum').click();
          const overlap=()=>[...d.querySelectorAll('#ask-line,#points-line,#chatbar,#to-latest,.catchup')].some(e=>{
            if(!b(e).height || e.closest('#msgs')) return false;
            return [...d.querySelectorAll('#msgs .msg')].some(msg=>{const r=b(msg),q=b(e),v=b(m);return Math.max(r.top,v.top,q.top)<Math.min(r.bottom,v.bottom,q.bottom) && Math.max(r.left,q.left)<Math.min(r.right,q.right);});
          });
          const overlaps=[];m.scrollTop=0;overlaps.push(overlap());m.scrollTop=m.scrollHeight;overlaps.push(overlap());input.focus();overlaps.push(overlap());input.blur();
          const header=${kind!=='po'}?document.querySelector('#detail-panel .dp-top'):null;
          const fresh=document.querySelector('#detail-panel .dp-summary-phone summary');
          const elements=header?[...header.querySelectorAll('h2,.dp-meta,.dp-actions')]:[...document.querySelectorAll('#po-panel .po-name,#po-panel .po-sub')];
          return {expanded,pointJump,overlaps,scrolls:m.scrollHeight>m.clientHeight,
            composerOneRow:Math.abs((b(input).top+b(input).bottom)/2-(b(send).top+b(send).bottom)/2)<1,
            header:elements.map(e=>({y:b(e).top,h:b(e).height,w:b(e).width})),
            rendered:!header || !!fresh.querySelector('strong'),
            shortPath:!header || fresh.querySelector('.file-link')?.textContent==='LOCKDOWN-PLAN.md',
            fullPath:!header || fresh.querySelector('.file-link')?.getAttribute('href').includes('41_cyber_sec'),
            filterRow};
        })()`);
        if(!baseline) {
          g.actions
"""))

    def test_conversation_geometry(self):
        if os.environ.get('ENSEMBLE_FOCUS_BEFORE'):
            return
        for g in self.got:
            with self.subTest(width=g['width'], theme=g['theme'], kind=g['kind']):
                self.assertGreaterEqual(g['share'], .75 if g['width'] == 1728 else .60)
                self.assertLessEqual(g['scrollW'], g['width'])
                self.assertFalse(g['overlap'])
                self.assertFalse(g['balloonOverflow'])
                self.assertTrue(g['selected'])
                a = g['actions']
                self.assertTrue(a['highlighted'] and a['cleared'] and a['jumped'])
                self.assertEqual(a['overlaps'], [False, False, False])
                self.assertIn('inset', a['ring'])
                self.assertGreaterEqual(a['expanded'], 88)
                self.assertLessEqual(a['focusedBottom'], a['frameHeight'] + 1)
                self.assertTrue(a['filter'])
                self.assertLess(len(a['placeholder']), 30)
                self.assertTrue(all(g['menuDismiss'].values()), g['menuDismiss'])
                desktop = g['desktop']
                self.assertTrue(desktop['expanded'] and desktop['pointJump'])
                self.assertEqual(desktop['overlaps'], [False, False, False])
                self.assertTrue(desktop['scrolls'] and desktop['composerOneRow'] and desktop['filterRow'])
                self.assertTrue(desktop['rendered'] and desktop['shortPath'] and desktop['fullPath'])
                if g['width'] >= 1280:
                    centers = [e['y'] + e['h']/2 for e in desktop['header']]
                    self.assertLess(max(centers) - min(centers), 1)
                    self.assertTrue(all(e['w'] > 0 for e in desktop['header']))
                if g['kind'] != 'po':
                    # Desktop keeps End beside the metadata; phone puts it in the menu.
                    self.assertGreater(g['report']['after'], g['report']['before'])
                    self.assertTrue(g['report']['same'] and g['report']['held'] and g['report']['updated'], g['report'])
                    self.assertGreater(g['report']['scroll'], 0)
                    self.assertFalse(g['report']['initialOpen'])
