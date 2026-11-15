import copy
import unittest

from easyeda_assist.drawing_layout import parameters, verify_translation
from test_drawing_properties import line
from test_drawing_execution import snapshot


def state(x=10, y=-20, name='U2', outside=30):
    s = snapshot()
    s['source'] = line('DOCHEAD','head',{'docType':'SCH_PAGE'})
    s['source'] += line('COMPONENT','c',{'partId':'part','x':x,'y':y})
    s['source'] += line('ATTR','a',{'parentId':'c','key':'Designator','value':name,'x':x,'y':y-5})
    s['source'] += line('ATTR','supplier',{'parentId':'c','key':'Supplier Part','value':'C116713'})
    s['source'] += line('COMPONENT','other',{'partId':'other','x':outside,'y':-40})
    return s


class LayoutTests(unittest.TestCase):
    def test_api_y_is_opposite_source_y(self):
        self.assertEqual(parameters(state(),'c',0,430)['y'],450)
        self.assertTrue(verify_translation(state(),state(y=-450),'c',0,430)['outside_records_unchanged'])

    def test_other_part_and_metadata_and_wrong_direction_rejected(self):
        for after in (state(y=-450,outside=31),state(y=-450,name='U3'),state(y=410)):
            with self.assertRaises(ValueError): verify_translation(state(),after,'c',0,430)

    def test_invalid_coordinates_and_missing_target(self):
        for dx,dy in ((float('nan'),1),(0,0),(True,2)):
            with self.assertRaises(ValueError): parameters(state(),'c',dx,dy)
        with self.assertRaises(ValueError): parameters(state(),'missing',0,430)

    def test_wire_cannot_rename_net_or_change_another_wire(self):
        before=state()
        before['source']+=line('WIRE','w',{})+line('LINE','l',{'lineGroup':'w','startX':0,'startY':0,'endX':10,'endY':0})
        before['source']+=line('ATTR','net',{'parentId':'w','key':'NET','value':'GND'})
        after=copy.deepcopy(before)
        after['source']=after['source'].replace('"startY": 0','"startY": -430').replace('"endY": 0','"endY": -430')
        verify_translation(before,after,'w',0,430)
        after['source']=after['source'].replace('"GND"','"5V"')
        with self.assertRaises(ValueError): verify_translation(before,after,'w',0,430)


if __name__=='__main__': unittest.main()
