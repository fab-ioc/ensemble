"""An ask reference pasted into a balloon renders as a card link."""
import shutil
import unittest

from tests.test_task_refs import SESSION, fn, node


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class CardLinks(unittest.TestCase):
    def test_reference_becomes_chip_targeting_the_card(self):
        source = "\n".join([fn(SESSION, "refOfUrl"), fn(SESSION, "refChipHtml")])
        result = node(r"""
const esc = s => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
const refInfo = () => ({state:'ok'});
const refPreview = () => 'Which path?';
const refLabel = () => 'PO · 10/13 08:00';
const refHref = (room, msg, part) => '/session?room=' + room + '&msg=' + msg + (part ? '&part=' + part : '');
%s
const ref = refOfUrl('http://hub:8765/session?room=room-1234abcd&msg=abc123&part=ask:0');
console.log(JSON.stringify({ref, html:refChipHtml(ref)}));
""" % source)
        self.assertEqual(result['ref']['part'], 'ask:0')
        self.assertIn('class="ref-chip card-jump"', result['html'])
        self.assertIn('data-ref-part="ask:0"', result['html'])
        self.assertIn('part=ask:0', result['html'])
        self.assertIn('Question · PO', result['html'])
