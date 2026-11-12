"""Execute the shipped JavaScript against a read-only EDA double.

Node is optional for users running the Python CLI, required for this integration
test group. No test contacts or modifies a real EDA document.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from easyeda_assist.bridge import CONTEXT_QUERY, MAX_SELECTED_DETAILS
from easyeda_assist.contracts import validate_context


HARNESS = r"""
const fs=require('fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const options=input.options;
let documentReads=0;let selectionReads=0;const calls=[];
const primitives=(options.items||[]).map(item=>new Proxy({},{
 get(target,name){
  if(name==='getState_PrimitiveId')return ()=>item.id;
  if(name==='getState_PrimitiveType')return ()=>item.type;
  if(typeof name==='string'&&name.startsWith('getState_'))return ()=>{
   const field=name.slice(9);calls.push(item.type+'.'+field);
   if(item.errors?.includes(field))throw new Error('denied');
   return item.state?.[field];
  };
  throw new Error('Unexpected primitive method '+String(name));
 }
}));
const selection={
 async getAllSelectedPrimitives_PrimitiveId(){
  selectionReads++;
  if(selectionReads>1&&options.after_ids)return options.after_ids;
  return options.ids||(options.items||[]).map(item=>item.id);
 },
 async getAllSelectedPrimitives(){
  if(options.unavailable)throw new Error('beta API unavailable');
  return primitives;
 }
};
const documentType=options.domain==='pcb'?3:1;
const eda={
 dmt_Project:{async getCurrentProjectInfo(){return {uuid:'project-1'};}},
 dmt_SelectControl:{async getCurrentDocumentInfo(){
  documentReads++;
  return {uuid:options.changed_document&&documentReads>1?'other':'doc-1',
          tabId:'tab-1',documentType,parentProjectUuid:'project-1'};
 }},
 sch_SelectControl:options.domain==='pcb'?{}:selection,
 pcb_SelectControl:options.domain==='pcb'?selection:{}
};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('eda',input.query)(eda).then(
 result=>process.stdout.write(JSON.stringify({result,calls})),
 error=>process.stdout.write(JSON.stringify({error:String(error),calls}))
);
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for bridge JavaScript integration tests")
class QueryRuntimeTests(unittest.TestCase):
    def run_query(self, **options):
        process = subprocess.run(
            [shutil.which("node"), "-e", HARNESS],
            input=json.dumps({"query": CONTEXT_QUERY, "options": options}),
            text=True, encoding="utf-8", capture_output=True, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def assert_valid(self, result):
        validate_context({
            "schema_version": 2, "source": "fixture", "captured_at": "test",
            "limitations": ["test double"], **result,
        })

    def test_component_preserves_mpn_custom_properties_and_missing_fields(self):
        reply = self.run_query(items=[{
            "id": "c1", "type": "Component",
            "state": {"Designator": "U1", "ManufacturerId": "TEST-MPN",
                      "OtherProperty": {"Datasheet": "https://example.invalid/test.pdf"}},
        }])
        result = reply["result"]
        self.assert_valid(result)
        item = result["selected_primitives"][0]
        self.assertEqual(item["properties"]["ManufacturerId"], "TEST-MPN")
        self.assertEqual(item["properties"]["OtherProperty"]["Datasheet"], "https://example.invalid/test.pdf")
        self.assertIn("Footprint", item["missing_fields"])
        self.assertFalse(item["read_errors"])
        self.assertNotIn("Component.Layer", reply["calls"])

    def test_pcb_line_via_and_pour_use_their_own_documented_getters(self):
        reply = self.run_query(domain="pcb", items=[
            {"id": "l1", "type": "Line", "state": {"Net": "GND", "LineWidth": 12, "Layer": 1}},
            {"id": "v1", "type": "Via", "state": {"Diameter": 24, "HoleDiameter": 12}},
            {"id": "p1", "type": "Pour", "state": {"PourFillMethod": "solid", "Layer": 2}},
        ])
        result = reply["result"]
        self.assert_valid(result)
        items = result["selected_primitives"]
        self.assertEqual(items[0]["properties"]["LineWidth"], 12)
        self.assertEqual(items[1]["properties"]["HoleDiameter"], 12)
        self.assertEqual(items[2]["properties"]["PourFillMethod"], "solid")
        self.assertNotIn("Via.Layer", reply["calls"])
        self.assertNotIn("Pour.Designator", reply["calls"])

    def test_unsupported_type_keeps_identity_and_calls_no_other_getters(self):
        for primitive_type in ("Rectangle", "toString", "__proto__"):
            with self.subTest(primitive_type=primitive_type):
                reply = self.run_query(items=[{"id": "r1", "type": primitive_type}])
                self.assert_valid(reply["result"])
                self.assertFalse(reply["result"]["selected_primitives"][0]["supported"])
                self.assertEqual(reply["calls"], [])

    def test_property_error_is_distinct_from_undefined(self):
        reply = self.run_query(items=[{
            "id": "w1", "type": "Wire", "errors": ["Net"], "state": {"LineWidth": None},
        }])
        item = reply["result"]["selected_primitives"][0]
        self.assert_valid(reply["result"])
        self.assertIn("Net: Error: denied", item["read_errors"])
        self.assertIn("Line", item["missing_fields"])
        self.assertIsNone(item["properties"]["LineWidth"])
        self.assertNotIn("Net", item["properties"])

    def test_beta_failure_preserves_ids_without_partial_details(self):
        result = self.run_query(unavailable=True, ids=["c1"])["result"]
        self.assert_valid(result)
        self.assertEqual(result["selected_ids"], ["c1"])
        self.assertEqual(result["selected_primitives"], [])
        self.assertIsNone(result["selected_total"])
        self.assertEqual(result["selection_details_status"], "unavailable")

    def test_id_mismatch_discards_details(self):
        result = self.run_query(ids=["expected"], items=[{"id": "other", "type": "Wire"}])["result"]
        self.assert_valid(result)
        self.assertEqual(result["selection_details_status"], "id_mismatch")
        self.assertEqual(result["selected_primitives"], [])

    def test_changed_selection_or_document_rejects_entire_capture(self):
        for options in ({"ids": ["c1"], "after_ids": ["other"]}, {"changed_document": True}):
            with self.subTest(options=options):
                reply = self.run_query(**options)
                self.assertIn("变化", reply["error"])
                self.assertNotIn("result", reply)

    def test_truncation_preserves_full_selection_and_caps_details(self):
        items = [{"id": f"w{index}", "type": "Wire"} for index in range(MAX_SELECTED_DETAILS + 3)]
        result = self.run_query(items=items, ids=[item["id"] for item in reversed(items)])["result"]
        self.assert_valid(result)
        self.assertEqual(len(result["selected_primitives"]), MAX_SELECTED_DETAILS)
        self.assertEqual(len(result["selected_ids"]), MAX_SELECTED_DETAILS + 3)
        self.assertEqual(result["selection_details_status"], "truncated")

    def test_empty_selection_is_valid_without_inventing_components(self):
        result = self.run_query()["result"]
        self.assert_valid(result)
        self.assertEqual(result["selected_primitives"], [])
        self.assertEqual(result["selection_details_status"], "captured")
