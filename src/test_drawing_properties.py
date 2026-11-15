import json
import unittest

from easyeda_assist.drawing_properties import property_script, verify_property_patch
from test_drawing_execution import snapshot


def line(kind, rid, value):
    return json.dumps({"type":kind,"id":rid})+'||'+json.dumps(value)+'|\n'


def state(value="", x=10, other="keep"):
    s=snapshot()
    s["source"] = line("DOCHEAD","head",{"docType":"SCH_PAGE"})
    s["source"] += line("COMPONENT","c",{"x":x,"y":20})
    s["source"] += line("ATTR","d",{"parentId":"c","key":"Designator","value":"C3"})
    s["source"] += line("ATTR","v",{"parentId":"c","key":"Value","value":value})
    s["source"] += line("ATTR","o",{"parentId":"other","key":"Name","value":other})
    return s


class PropertyTests(unittest.TestCase):
    def test_only_named_value_changes(self):
        r=verify_property_patch(state(),state("470uF"),"c",{"Value":"470uF"})
        self.assertEqual(r["changed_attribute_ids"],["v"])

    def test_geometry_or_other_object_change_rejected(self):
        for after in (state("470uF",x=11),state("470uF",other="changed")):
            with self.assertRaisesRegex(ValueError,"方案外"):
                verify_property_patch(state(),after,"c",{"Value":"470uF"})

    def test_structure_change_rejected(self):
        after=state("470uF");after["source"]+=line("WIRE","w",{})
        with self.assertRaisesRegex(ValueError,"新增或删除"):
            verify_property_patch(state(),after,"c",{"Value":"470uF"})

    def test_unknown_and_nonexisting_properties_rejected(self):
        for patch in ({"NET":"GND"},{"Footprint":"anything"},{"Datasheet":"url"},{"Value":None}):
            with self.assertRaises(ValueError):property_script(state(),"c",patch)

    def test_unchanged_value_not_written(self):
        with self.assertRaisesRegex(ValueError,"无需写入"):
            property_script(state("470uF"),"c",{"Value":"470uF"})

    def test_fixed_script_encodes_text(self):
        script=property_script(state(),"c",{"Value":"\";throw new Error('oops');//"})
        self.assertTrue(script.startswith("const input=JSON.parse("))
        self.assertEqual(script.count("sch_PrimitiveComponent.modify("),1)


if __name__ == "__main__":unittest.main()
