import copy
import unittest
from easyeda_assist.drawing_flags import removal_parameters, verify_ground_removal
from test_drawing_execution import snapshot
from test_drawing_properties import line


def fixture(visible=True, net='GND', x=0):
    s=snapshot()
    s['source']=line('DOCHEAD','head',{'docType':'SCH_PAGE'})
    s['source']+=line('COMPONENT','f',{'x':x,'y':0})
    for key,value in [('Global Net Name',net),('Add into BOM','no'),('Convert to PCB','no')]:
        s['source']+=line('ATTR',key,{'parentId':'f','key':key,'value':value})
    s['source']+=line('COMPONENT','other',{'x':50,'y':50})
    s['source']+=line('ATTR','global',{'parentId':'other','key':'Global Net Name','value':'GND'})
    s['source']+=line('WIRE','w',{})
    s['source']+=line('LINE','l',{'lineGroup':'w','startX':0,'startY':0,'endX':10,'endY':0})
    s['source']+=line('ATTR','label',{'parentId':'w','key':'NET','value':'GND','valueVisible':visible})
    return s


class GroundRepairTests(unittest.TestCase):
    def test_only_redundant_ground_with_visible_wire_name(self):
        self.assertEqual(removal_parameters(fixture(),'f','w')['flag_id'],'f')
        for s in (fixture(False),fixture(net='5V'),fixture(x=5)):
            with self.assertRaises(ValueError): removal_parameters(s,'f','w')

    def test_delete_exact_symbol_and_owned_attributes(self):
        from easyeda_assist.drawing_source import records
        before=fixture(); after=copy.deepcopy(before)
        after['source']=''.join(json_line(e, rid, v) for rid,(e,v) in records(before['source']).items()
                                if rid!='f' and v.get('parentId')!='f')
        self.assertTrue(verify_ground_removal(before,after,'f','w')['all_wires_parts_and_remaining_values_unchanged'])
        after['source']=after['source'].replace('"endX": 10','"endX": 11')
        with self.assertRaises(ValueError): verify_ground_removal(before,after,'f','w')


def json_line(env, rid, value):
    import json
    return json.dumps({"type":env['type'],"id":env.get('id',rid)})+'||'+json.dumps(value)+'|\n'


if __name__=='__main__': unittest.main()
