"""Execute real fixed Source scripts against a delayed EDA double, never CAD."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

from easyeda_assist.cli import main
from easyeda_assist.drawing_execution import preflight, approve_plan, execute_plan
from easyeda_assist.operations import ADAPTERS, SOURCE
from easyeda_assist.queries import read_query, source_query_script
from easyeda_assist.source_properties import operation_script, verify_reply, _fixed_property_script
from easyeda_assist.storage import write_new, digest
from test_drawing_properties import state
from test_project import design


def parameters():
    return {"primitive_id": "c", "patch": {"Value": "470uF"},
            "source_target": {"projectId": "p", "schematicName": "S", "pageName": "P"}}


def source_reply():
    original = {"primitiveId": "c", "x": 10, "y": 20, "rotation": 0, "pins": [],
                "properties": {"Designator": "C3", "Value": "", "Supplier Part": "keep"}}
    observed = deepcopy(original)
    observed["properties"]["Value"] = "470uF"
    return {"status": "verified", "attempted": True, "primitive_id": "c", "stable_reads": 3,
            "source_target": parameters()["source_target"], "identity": state()["identity"],
            "source": state("470uF")["source"], "original": original, "observed": observed}


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = design()
        self.project["drawing_policy"] = {"mode": "explicit_batch", "trigger": "explicit_user_request",
            "allowed_operations": ["set_property"], "require_batch_approval": True, "require_snapshot_binding": True}
        snapshot = write_new(self.root / "snapshot.json", state())
        self.plan = {"schema_version": 2, "interaction": "collaborative", "origin": {"task": None, "decisions": []},
            "project_id": self.project["id"], "created_at": "fixture", "request": "fixture Source property",
            "execution_contract": SOURCE, "target": {"window_id": "w", "project_uuid": "p", "document_uuid": "d",
            "domain": "schematic", "snapshot_path": str(snapshot), "snapshot_sha256": digest(snapshot)},
            "operations": [{"id": "o1", "operation": "set_property", "target": "C3",
            "before": "Value empty", "after": "Value 470uF", "expected_result": "only Value changes",
            "parameters": parameters()}], "evidence": ["fixture"], "unresolved": [],
            "readback_plan": "complete Source properties and strict source delta", "failure_plan": "stop, retain lock"}

    def test_unverified_adapter_can_preview_but_never_approve_or_dispatch(self):
        checked, _ = preflight(self.project, self.plan, for_execution=False)
        self.assertFalse(checked["preview"]["capability"]["dispatch_enabled"])
        with self.assertRaisesRegex(ValueError, "真实 CAD 写入验证"):
            approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture", "test")
        with self.assertRaisesRegex(ValueError, "真实 CAD 写入验证"):
            operation_script(state(), parameters())
        self.assertFalse((self.root / "drawing-approvals").exists())

    def test_source_uses_common_journal_and_locks_when_enabled_by_test_only(self):
        with patch.dict(ADAPTERS[SOURCE], {"dispatch_enabled": True}):
            checked, _ = preflight(self.project, self.plan)
            consent = approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture", "test")
            bridge = Mock()
            bridge._source_properties.return_value = source_reply()
            result = execute_plan(self.project, self.plan, consent, lambda: bridge)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["completed"], 1)
        self.assertTrue((Path(result["run_dir"]) / "001-intent.json").is_file())
        self.assertFalse(Path(result["target_lock"]).exists())
        bridge._place_component.assert_not_called()

    def test_source_timeout_is_not_retried_and_retains_target_lock(self):
        with patch.dict(ADAPTERS[SOURCE], {"dispatch_enabled": True}):
            checked, _ = preflight(self.project, self.plan)
            consent = approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture", "test")
            bridge = Mock()
            bridge._source_properties.side_effect = TimeoutError("late response")
            result = execute_plan(self.project, self.plan, consent, lambda: bridge)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(Path(result["target_lock"]).exists())
        self.assertEqual(bridge._source_properties.call_count, 1)

    def test_readback_rejects_attribute_loss_geometry_change_or_stale_source(self):
        verify_reply(source_reply(), state(), parameters())
        for mutate in (lambda r: r["observed"]["properties"].pop("Supplier Part"),
                       lambda r: r["observed"].update(x=11),
                       lambda r: r.update(source=state()["source"]),
                       lambda r: r.update(stable_reads=1),
                       lambda r: r.update(source_target={**parameters()["source_target"], "pageName": "renamed"})):
            reply = source_reply()
            mutate(reply)
            with self.assertRaises(ValueError):
                verify_reply(reply, state(), parameters())

    def test_snapshot_and_source_mapping_must_match_and_only_values_can_change(self):
        for params in ({**parameters(), "source_target": {**parameters()["source_target"], "projectId": "other"}},
                       {**parameters(), "patch": {"Footprint": "unsafe"}},
                       {**parameters(), "code": "return anything"}):
            self.plan["operations"][0]["parameters"] = params
            with self.assertRaises(ValueError):
                preflight(self.project, self.plan, for_execution=False)

    def test_cli_prepares_source_without_claiming_support(self):
        project = write_new(self.root / "project.json", self.project)
        plan = write_new(self.root / "plan.json", self.plan)
        output = io.StringIO()
        with redirect_stdout(output), patch("easyeda_assist.cli.discover", side_effect=AssertionError("no live calls")):
            self.assertEqual(main(["prepare-drawing-execution", str(project), str(plan)]), 0)
        self.assertFalse(json.loads(output.getvalue())["capability"]["dispatch_enabled"])


HARNESS = r"""
const fs=require('fs');const input=JSON.parse(fs.readFileSync(0,'utf8'));
const options=input.options||{};const calls=[];let writes=0,reads=0;
const original={primitiveId:'c',x:10,y:20,properties:{Designator:'C3',Value:'',Supplier:'keep'},pins:[]};
function updated(){return writes>0&&reads>=(options.delay||2);}
const eda={
 dmt_Project:{async getCurrentProjectInfo(){return {uuid:'p'};}},
 dmt_SelectControl:{async getCurrentDocumentInfo(){return {uuid:options.changed_target&&writes?'other':'d',tabId:'t',documentType:1,parentProjectUuid:'p'};}},
 dmt_Schematic:{async getCurrentSchematicInfo(){return {uuid:'s',name:'S'};},
  async getCurrentSchematicPageInfo(){return {uuid:'d',parentSchematicUuid:'s',name:options.renamed?'renamed':'P'};}},
 sys_FileManager:{async getDocumentSource(){return updated()&&!options.stale_source?input.after:input.before;}},
 sys_Environment:{getEditorCurrentVersion(){return options.version||'4.1.100';}},
 sch_SourcePrimitiveComponent:{async get(){
  reads++;
  if(options.read_error)return {ok:false,error:{code:'NOT_IMPLEMENTED'}};
  return {ok:true,value:updated()?{...original,properties:{...original.properties,Value:'470uF'}}:original};
 },async update(args){writes++;calls.push(args);if(options.throw_after_write)throw new Error('response lost');return {ok:true,value:{primitiveId:'c'}};}},
 sch_PrimitiveAttribute:{async get(id){return {
  getState_PrimitiveId(){return options.attribute_mismatch?'wrong':id;},getState_ParentPrimitiveId(){return 'c';},
  getState_Key(){return 'Value';},getState_Value(){return '';}
 };},async modify(id,patch){writes++;calls.push({id,patch,backend:'canvas-attribute'});
  if(options.throw_after_write)throw new Error('response lost');return {}; }},
 sch_SourcePin:{async getGeometry(){return {ok:true,value:[]};}},
 sch_SourceDocument:{async getFunctionGroups(){return {ok:true,value:[]};}}
};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('eda','setTimeout',input.script)(eda,fn=>fn()).then(
 result=>process.stdout.write(JSON.stringify({result,calls,reads})),
 error=>process.stdout.write(JSON.stringify({error:String(error),calls,reads})));
"""


@unittest.skipUnless(shutil.which("node"), "Node required for fixed Source script tests")
class SourceRuntimeTests(unittest.TestCase):
    def run_script(self, script, **options):
        result = subprocess.run([shutil.which("node"), "-e", HARNESS], input=json.dumps({
            "script": script, "before": state()["source"], "after": state("470uF")["source"], "options": options}),
            text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def script(self):
        with patch.dict(ADAPTERS[SOURCE], {"dispatch_enabled": True}):
            return operation_script(state(), parameters())

    def test_delayed_expected_state_requires_three_stable_reads_and_one_write(self):
        result = self.run_script(self.script(), delay=5)
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["result"]["stable_reads"], 3)
        self.assertGreaterEqual(result["reads"], 7)
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["calls"][0], {**parameters()["source_target"], "primitiveId": "c", "properties": {"Value": "470uF"}})

    def test_canvas_attribute_path_binds_attribute_identity_and_never_uses_source_update(self):
        script = _fixed_property_script(state(), parameters(), "canvas-attribute")
        result = self.run_script(script, delay=4)
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["result"]["backend"], "canvas-attribute")
        self.assertEqual(result["calls"], [{"id": "v", "patch": {"value": "470uF"}, "backend": "canvas-attribute"}])
        self.assertNotIn("sch_SourcePrimitiveComponent.update(", script)
        for options in ({"attribute_mismatch": True}, {"version": "unknown"}):
            blocked = self.run_script(script, **options)
            self.assertEqual(blocked["result"]["status"], "blocked")
            self.assertEqual(blocked["calls"], [])

    def test_canvas_lost_response_or_stale_source_never_retries_or_falls_back(self):
        script = _fixed_property_script(state(), parameters(), "canvas-attribute")
        for options in ({"throw_after_write": True}, {"stale_source": True}):
            result = self.run_script(script, **options)
            self.assertEqual(result["result"]["status"], "uncertain")
            self.assertEqual(len(result["calls"]), 1)

    def test_stable_old_source_never_counts_as_success_and_write_not_repeated(self):
        result = self.run_script(self.script(), stale_source=True)
        self.assertEqual(result["result"]["status"], "uncertain")
        self.assertEqual(len(result["calls"]), 1)

    def test_wrong_version_renamed_page_or_unreadable_component_blocks_before_write(self):
        for options in ({"version": "new"}, {"renamed": True}, {"read_error": True}):
            result = self.run_script(self.script(), **options)
            self.assertEqual(result["result"]["status"], "blocked")
            self.assertEqual(result["calls"], [])

    def test_lost_response_or_target_change_after_write_retains_uncertainty(self):
        for options in ({"throw_after_write": True}, {"changed_target": True}):
            result = self.run_script(self.script(), **options)
            self.assertEqual(result["result"]["status"], "uncertain")
            self.assertEqual(len(result["calls"]), 1)

    def test_source_queries_are_read_only_and_preserve_missing_outputs(self):
        script = source_query_script("source-component", "c")
        for options in ({}, {"read_error": True}):
            result = self.run_script(script, **options)
            self.assertNotIn("error", result)
            self.assertEqual(result["calls"], [])
            bridge = Mock()
            bridge._read_fixed.return_value = (result["result"], "w")
            record = read_query(bridge, "source-component", "w", "c")
            self.assertEqual(record["provenance"]["editor_version"], "4.1.100")
            self.assertEqual("component" in record["missing_fields"], bool(options))

    def test_source_query_rejects_mismatched_returned_component(self):
        result = self.run_script(source_query_script("source-component", "c"))["result"]
        result["outputs"]["component"]["value"]["primitiveId"] = "different"
        bridge = Mock()
        bridge._read_fixed.return_value = (result, "w")
        with self.assertRaisesRegex(ValueError, "不同的器件身份"):
            read_query(bridge, "source-component", "w", "c")


if __name__ == "__main__":
    unittest.main()
