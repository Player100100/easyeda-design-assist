import copy
import unittest

from easyeda_assist.drawing_display import visibility_script, verify_visibility
from easyeda_assist.drawing_layout import translation_script
from test_drawing_layout import state
from test_drawing_properties import line


def fixture(visible=True, net='GND', outside=30):
    s = state(outside=outside)
    s['source'] += line('WIRE','w',{})
    s['source'] += line('LINE','wl',{'lineGroup':'w','startX':0,'startY':0,'endX':10,'endY':0})
    s['source'] += line('ATTR','label',{'parentId':'w','key':'NET','value':net,'valueVisible':visible})
    return s


class DisplayTests(unittest.TestCase):
    def test_visibility_only_preserves_geometry_and_net(self):
        self.assertTrue(verify_visibility(fixture(),fixture(False),'label',False)['geometry_values_and_nets_unchanged'])

    def test_net_or_outside_change_rejected(self):
        for after in (fixture(False,'5V_OUT'),fixture(False,outside=31)):
            with self.assertRaises(ValueError): verify_visibility(fixture(),after,'label',False)

    def test_invalid_or_noop_targets_rejected(self):
        for target,value in (('missing',False),('c',False),('label','false'),('label',True)):
            with self.assertRaises(ValueError): visibility_script(fixture(),target,value)

    def test_added_record_rejected(self):
        after=fixture(False)
        after['source']+=line('WIRE','unexpected',{})
        with self.assertRaises(ValueError): verify_visibility(fixture(),after,'label',False)

    def test_wire_translation_rejected_before_bridge(self):
        with self.assertRaisesRegex(ValueError,'禁止逐线平移'):
            translation_script(fixture(),'w',0,60)

    def test_display_code_does_not_edit_values_or_geometry(self):
        code=visibility_script(fixture(),'label',False)
        self.assertIn('{valueVisible:input.visible}',code)
        self.assertNotIn('sch_PrimitiveWire.modify',code)


if __name__ == '__main__': unittest.main()
