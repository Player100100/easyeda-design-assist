from __future__ import annotations

from copy import deepcopy
import base64
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from easyeda_assist.bridge import BridgeClient, BridgeError
from easyeda_assist.cli import main
from easyeda_assist.drawing import review_drawing_plan
from easyeda_assist.drawing_api import (SNAPSHOT_QUERY, PROJECT_LIBRARY_QUERY, comparable_source, ROTATION_CONVENTION,
    component_pins_script, device_details_script, device_search_script, placement_script, validate_parameters)
from easyeda_assist.drawing_execution import approve_plan, execute_plan, preflight
from easyeda_assist.drawing_library import device_sources, verify_mapping, VERIFIED_EDITOR_VERSION
from easyeda_assist.project import object_digest
from easyeda_assist.storage import digest, read_json, write_new
from test_project import design


def snapshot():
    return {"schema_version": 1, "kind": "drawing-source", "captured_at": "fixture",
            "window_id": "w", "domain": "schematic", "source": "fixture-source",
            "identity": {"project_uuid": "p", "document_uuid": "d", "tab_id": "t", "document_type": 1}}


def parameters():
    return {"library_uuid": "lib", "device_uuid": "dev", "x": 100, "y": 200, "unit": "10mil",
            "sub_part_name": None, "requested_api_rotation": 90, "expected_canvas_rotation": 270,
            "rotation_convention": ROTATION_CONVENTION, "expected_project_device_uuid": "local-dev",
            "expected_pins": [{"number": "1", "x": 100, "y": 200}, {"number": "2", "x": 101, "y": 200}],
            "mapping_evidence": {"path": "fixture", "sha256": "0" * 64},
            "mirror": False, "add_into_bom": True, "add_into_pcb": True}


def library_record(before, device="dev", library="lib", tail="", log=None):
    stream = io.BytesIO()
    if log is None:
        log = ('{"type":"DOCHEAD"}||' + json.dumps({"docType": "DEVICE", "uuid": "local-dev", "client": "c"}) + '|\n' +
               '{"type":"META","ticket":1}||' + json.dumps({"source": device + "|" + library}) + '|\n' + tail)
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("project.epru", log)
    archive = stream.getvalue()
    return {"identity": before["identity"], "window_id": before["window_id"], "source": before["source"],
            "project_info": {"editorVersion": VERIFIED_EDITOR_VERSION},
            "file_size": len(archive), "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "archive_base64": base64.b64encode(archive).decode("ascii")}


def doc_source(**changes):
    header = {"docType": "SCH_PAGE", "uuid": "d", "client": "client", "updateTime": 1,
              "version": "1", "editVersion": "editor-version", **changes}
    return '{"type":"DOCHEAD"}||' + json.dumps(header) + '|\nprimitive-content\n'


def reply(before, p):
    after = {**before, "source": before["source"] + "+placed"}
    return {"status": "verified", "attempted": True, "primitive_id": "new-id",
            "observed": {"primitive_id": "new-id", **{k: p[k] for k in (
                "library_uuid", "x", "y", "mirror", "sub_part_name", "add_into_bom", "add_into_pcb")},
                "device_uuid": p["expected_project_device_uuid"], "rotation": p["expected_canvas_rotation"]},
            "pins": [{"primitive_id": "pin-" + pin["number"], **pin} for pin in p["expected_pins"]],
            "project_evidence": library_record(after), "identity": after["identity"], "source": after["source"]}


class ExecutionTests(unittest.TestCase):
    def test_header_normalization_keeps_design_and_unknown_fields(self):
        original = doc_source()
        self.assertEqual(comparable_source(original), comparable_source(doc_source(client="other", updateTime=2, version="2")))
        for changed in (doc_source(uuid="other"), doc_source(editVersion="other"),
                        doc_source(unknown="new"), original + "changed-primitive"):
            self.assertNotEqual(comparable_source(original), comparable_source(changed))
        for invalid in (doc_source(docType="PCB"), '{"type":"DOCHEAD"}||broken|\n'):
            with self.assertRaises(ValueError):
                comparable_source(invalid)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": self.folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = design()
        self.project["drawing_policy"] = {"mode": "explicit_batch", "trigger": "explicit_user_request",
                "allowed_operations": ["place_component", "add_wire"], "require_batch_approval": True,
                "require_snapshot_binding": True}
        self.source_path = write_new(self.root / "snapshot.json", snapshot())
        evidence_record = library_record(snapshot())
        evidence_path = write_new(self.root / "library.json", evidence_record)
        self.plan = {"schema_version": 1, "project_id": self.project["id"], "request": "fixture placement",
                "created_at": "fixture", "target": {"window_id": "w", "project_uuid": "p", "document_uuid": "d",
                "domain": "schematic", "snapshot_path": str(self.source_path), "snapshot_sha256": digest(self.source_path)},
                "operations": [{"id": "o1", "operation": "place_component", "target": "new device",
                "before": "absent", "after": "fixture device at (100,200) 10mil", "expected_result": "placed without wiring",
                "parameters": parameters()}], "evidence": ["fixture"], "unresolved": [],
                "readback_plan": "fresh get and compare", "failure_plan": "stop and retain scene", "approval": None}
        self.plan["operations"][0]["parameters"]["mapping_evidence"] = {
            "path": str(evidence_path), "sha256": digest(evidence_path)}

    def approve(self):
        sha = review_drawing_plan(self.project, self.plan)["plan_sha256"]
        return approve_plan(self.project, self.plan, sha, "测试认可此具体方案", "fixture-message-1")

    def test_mapping_requires_exact_device_source_pair(self):
        evidence = read_json(Path(self.plan["operations"][0]["parameters"]["mapping_evidence"]["path"]))
        proof = verify_mapping(evidence, snapshot(), self.plan["operations"][0]["parameters"])
        self.assertEqual(proof["project_device_uuid"], "local-dev")
        evidence["archive_base64"] = library_record(snapshot(), device="different")["archive_base64"]
        evidence["archive_sha256"] = hashlib.sha256(base64.b64decode(evidence["archive_base64"])).hexdigest()
        evidence["file_size"] = len(base64.b64decode(evidence["archive_base64"]))
        with self.assertRaisesRegex(ValueError, "META.source"):
            verify_mapping(evidence, snapshot(), self.plan["operations"][0]["parameters"])

    def test_mapping_evidence_path_and_project_snapshot_must_match(self):
        params = self.plan["operations"][0]["parameters"]
        with patch("easyeda_assist.drawing_library.external", side_effect=lambda path: path):
            original = Path(params["mapping_evidence"]["path"])
            params["mapping_evidence"]["sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "哈希"):
                from easyeda_assist.drawing_library import load_mapping_evidence
                load_mapping_evidence(snapshot(), params)
            params["mapping_evidence"]["sha256"] = digest(original)
            with self.assertRaisesRegex(ValueError, "目标窗口"):
                verify_mapping(library_record({**snapshot(), "window_id": "other"}), snapshot(), params)

    def test_rotation_contract_is_explicit_and_fail_closed(self):
        params = parameters()
        for rotation, expected in ((0, 0), (90, 270), (180, 180), (270, 90)):
            params["requested_api_rotation"] = rotation
            params["expected_canvas_rotation"] = expected
            validate_parameters(params)
        params["expected_canvas_rotation"] = 270
        with self.assertRaisesRegex(ValueError, "换算"):
            validate_parameters(params)

    def test_device_metadata_log_winners_deletions_and_conflicts(self):
        base = ('{"type":"DOCHEAD"}||{"docType":"DEVICE","uuid":"local-dev","client":"c"}|\n'
                '{"type":"META","ticket":1}||{"source":"dev|lib"}|\n')
        for tail, expected in (
                ('{"type":"META","ticket":2}||{"source":"updated|lib"}|\n', "updated|lib"),
                ('{"type":"META","ticket":0}||{"source":"old|lib"}|\n', "dev|lib"),
                ('{"type":"META","ticket":2}||""\n', None),
                ('{"type":"META","ticket":2}|||\n', None),
                ('{"type":"DELETE_DOC","ticket":2}||{"isDelete":true}|\n', None),
                ('{"type":"DOCHEAD"}||{"docType":"SYMBOL","uuid":"local-dev","client":"c"}|\n'
                 '{"type":"META","ticket":9}||{"source":"wrong|lib"}|\n', "dev|lib"),
                ('{"type":"DOCHEAD"}||{"docType":"DEVICE","uuid":"local-dev","client":"a"}|\n'
                 '{"type":"META","ticket":1}||{"source":"tie-winner|lib"}|\n', "tie-winner|lib")):
            with self.subTest(tail=tail):
                encoded = library_record(snapshot(), log=base + tail)["archive_base64"]
                self.assertEqual(device_sources(encoded).get("local-dev"), expected)
        with self.assertRaisesRegex(ValueError, "同序冲突"):
            device_sources(library_record(snapshot(), log=base +
                '{"type":"META","ticket":1}||{"source":"other|lib"}|\n')["archive_base64"])
        for encoded in ("not-base64", base64.b64encode(b"not a zip").decode("ascii")):
            with self.assertRaises(ValueError):
                device_sources(encoded)

    def test_mapping_rejects_missing_source_wrong_library_or_snapshot_and_version(self):
        params = self.plan["operations"][0]["parameters"]
        for mutation in (
            lambda r: r.update(archive_base64=library_record(snapshot(), library="wrong")["archive_base64"]),
            lambda r: r["project_info"].update(editorVersion="new-version"),
            lambda r: r.update(source="different"),
            lambda r: r["identity"].update(project_uuid="another-project")):
            evidence = library_record(snapshot())
            mutation(evidence)
            evidence.pop("file_size", None)
            with self.assertRaises(ValueError):
                verify_mapping(evidence, snapshot(), params)

    def test_python_postcheck_requires_provenance_rotation_and_pin_positions(self):
        from easyeda_assist.drawing_execution import _verified_snapshot
        params = self.plan["operations"][0]["parameters"]
        for mutation in (
                lambda r: r["observed"].update(device_uuid="dev"),
                lambda r: r["observed"].update(rotation=90),
                lambda r: r.pop("project_evidence"),
                lambda r: r["pins"][0].update(x=True),
                lambda r: r["pins"][0].update(number="2"),
                lambda r: r["pins"][0].update(primitive_id="pin-2")):
            r = reply(snapshot(), params)
            mutation(r)
            with self.assertRaises(ValueError):
                _verified_snapshot(r, snapshot(), params)

    def test_bridge_checks_live_provenance_before_write_and_archives_postcheck_failure(self):
        bridge = BridgeClient(49620)
        params = self.plan["operations"][0]["parameters"]
        with patch.object(bridge, "drawing_project_info", return_value=library_record(snapshot(), device="wrong")), \
                patch.object(bridge, "_execute_fixed") as write:
            result = bridge._place_component(snapshot(), params)
            self.assertEqual((result["status"], result["attempted"]), ("blocked", False))
            write.assert_not_called()
        r = reply(snapshot(), params)
        bad = library_record({**snapshot(), "source": r["source"]}, device="wrong")
        with patch.object(bridge, "drawing_project_info", side_effect=[library_record(snapshot()), bad]), \
                patch.object(bridge, "_execute_fixed", return_value=(r, "w")) as write:
            observed = bridge._place_component(snapshot(), params)
            self.assertEqual(observed["status"], "uncertain")
            self.assertEqual(observed["project_evidence"], bad)
            self.assertEqual(write.call_count, 1)

    def bridge(self):
        bridge = Mock()
        bridge._place_component.side_effect = reply
        return bridge

    def test_offline_preview_and_consent_never_connect(self):
        with patch("easyeda_assist.cli.discover", side_effect=AssertionError("must remain offline")):
            project_path = write_new(self.root / "project.json", self.project)
            plan_path = write_new(self.root / "plan.json", self.plan)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["prepare-drawing-execution", str(project_path), str(plan_path)]), 0)
            preview = json.loads(output.getvalue())
            self.assertEqual(preview["operations"][0]["parameters"], self.plan["operations"][0]["parameters"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["approve-drawing-plan", str(project_path), str(plan_path),
                    "--shown-plan-sha256", preview["plan_sha256"], "--user-message", "认可测试方案",
                    "--reference", "fixture"]), 0)
        self.assertFalse((self.root / "drawing-executions").exists())

    def test_success_archives_inputs_and_chains_source_then_blocks_replay(self):
        self.plan["operations"].append({**deepcopy(self.plan["operations"][0]), "id": "o2"})
        approval = self.approve()
        bridge = self.bridge()
        result = execute_plan(self.project, self.plan, approval, lambda: bridge)
        self.assertEqual((result["status"], result["completed"]), ("verified", 2))
        run = Path(result["run_dir"])
        self.assertEqual(read_json(run / "plan.json"), self.plan)
        self.assertEqual(read_json(run / "project.json"), self.project)
        self.assertEqual(read_json(run / "001-mapping-input.json"), read_json(Path(
            self.plan["operations"][0]["parameters"]["mapping_evidence"]["path"])))
        self.assertEqual(read_json(run / "approval.json"), read_json(approval))
        self.assertEqual(read_json(run / "002-intent.json")["source_sha256"], object_digest("fixture-source+placed"))
        self.assertEqual(read_json(run / "002-snapshot.json")["source"], "fixture-source+placed+placed")
        self.assertFalse(Path(result["target_lock"]).exists())
        with self.assertRaisesRegex(ValueError, "禁止重放"):
            execute_plan(self.project, self.plan, self.approve(), lambda: self.fail("no network on replay"))

    def test_parameter_unknown_keys_units_nonfinite_and_boolean_numbers_rejected(self):
        for key, value in (("unit", "mil"), ("x", True), ("x", float("inf")), ("requested_api_rotation", False),
                           ("mirror", "false"), ("device_uuid", ""), ("sub_part_name", ""), ("script", "delete()")):
            p = parameters()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_parameters(p)

    def test_unsupported_operation_and_snapshot_tampering_fail_before_network(self):
        approval = self.approve()
        for mutate in (lambda: self.plan["operations"][0].update(operation="add_wire"),
                       lambda: self.source_path.write_text("{}", encoding="utf-8")):
            original = deepcopy(self.plan)
            mutate()
            with self.assertRaises(ValueError):
                execute_plan(self.project, self.plan, approval, lambda: self.fail("must not connect"))
            self.plan = original

    def test_consent_invalidated_by_project_policy_or_plan_changes(self):
        approval = self.approve()
        for obj, key, value in ((self.project, "goal", "revised scope"),
                                (self.plan["operations"][0]["parameters"], "x", 101),
                                (self.project["drawing_policy"], "allowed_operations", ["place_component"])):
            previous = obj[key]
            obj[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "认可记录已失效"):
                execute_plan(self.project, self.plan, approval, lambda: self.fail("must not connect"))
            obj[key] = previous
        with self.assertRaisesRegex(ValueError, "已展示"):
            approve_plan(self.project, self.plan, "0" * 64, "agree", "fixture")

    def test_plan_approval_field_does_not_authorize(self):
        self.plan["approval"] = {"approved": True}
        fake = write_new(self.root / "fake.json", self.plan["approval"])
        with self.assertRaisesRegex(ValueError, "独立"):
            execute_plan(self.project, self.plan, fake, lambda: self.fail("must not connect"))

    def test_timeout_preserves_lock_and_intent_and_stops_following_steps(self):
        self.plan["operations"].append({**deepcopy(self.plan["operations"][0]), "id": "o2"})
        approval = self.approve()
        bridge = self.bridge()
        bridge._place_component.side_effect = [reply(snapshot(), parameters()), BridgeError("timeout")]
        result = execute_plan(self.project, self.plan, approval, lambda: bridge)
        self.assertEqual((result["status"], result["completed"]), ("uncertain", 1))
        self.assertTrue(Path(result["target_lock"]).exists())
        self.assertTrue((Path(result["run_dir"]) / "002-intent.json").exists())
        self.plan["request"] = "new attempt on same target"
        with self.assertRaisesRegex(ValueError, "状态未知"):
            execute_plan(self.project, self.plan, self.approve(), lambda: self.fail("must not connect"))

    def test_readback_mismatch_stops_and_retains_lock(self):
        bridge = self.bridge()
        wrong = reply(snapshot(), parameters())
        wrong["observed"]["x"] = 500
        bridge._place_component.side_effect = None
        bridge._place_component.return_value = wrong
        result = execute_plan(self.project, self.plan, self.approve(), lambda: bridge)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["completed"], 0)
        self.assertTrue(Path(result["target_lock"]).exists())

    def test_stale_source_block_is_recorded_without_uncertain_lock(self):
        bridge = self.bridge()
        bridge._place_component.side_effect = None
        bridge._place_component.return_value = {"status": "blocked", "attempted": False, "error": "source changed"}
        result = execute_plan(self.project, self.plan, self.approve(), lambda: bridge)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(Path(result["target_lock"]).exists())

    def test_later_block_preserves_partial_result_and_lock(self):
        self.plan["operations"].append({**deepcopy(self.plan["operations"][0]), "id": "o2"})
        bridge = self.bridge()
        bridge._place_component.side_effect = [reply(snapshot(), parameters()),
            {"status": "blocked", "attempted": False, "error": "source changed"}]
        result = execute_plan(self.project, self.plan, self.approve(), lambda: bridge)
        self.assertEqual((result["status"], result["completed"]), ("partial", 1))
        self.assertTrue(Path(result["target_lock"]).exists())
        self.assertEqual(result["steps"][1]["status"], "partial")

    def test_intent_storage_failure_prevents_write(self):
        from easyeda_assist.drawing_execution import _durable_new
        approval = self.approve()
        bridge = self.bridge()

        def failing(path, value):
            if path.name.endswith("-intent.json"):
                raise OSError("disk full")
            return _durable_new(path, value)

        with patch("easyeda_assist.drawing_execution._durable_new", side_effect=failing):
            result = execute_plan(self.project, self.plan, approval, lambda: bridge)
        self.assertEqual(result["status"], "blocked")
        bridge._place_component.assert_not_called()

    def test_boolean_readback_cannot_masquerade_as_numeric_position(self):
        self.plan["operations"][0]["parameters"]["x"] = 1
        bridge = self.bridge()
        wrong = reply(snapshot(), self.plan["operations"][0]["parameters"])
        wrong["observed"]["x"] = True
        bridge._place_component.side_effect = None
        bridge._place_component.return_value = wrong
        result = execute_plan(self.project, self.plan, self.approve(), lambda: bridge)
        self.assertEqual(result["status"], "uncertain")

    def test_transport_pins_window_and_never_retries_request(self):
        bridge = BridgeClient(49620)
        with patch.object(bridge, "drawing_project_info", return_value=library_record(snapshot())), patch.object(bridge, "_request", side_effect=[
            {"service": "easyeda-bridge"}, {"windows": [{"windowId": "w", "connected": True}]},
            BridgeError("timeout")]) as request:
            with self.assertRaises(BridgeError):
                bridge._place_component(snapshot(), parameters())
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.args[1]["windowId"], "w")


HARNESS = r"""
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
const o=input.options||{};let reads=0;let sourceReads=0;let count=0;let last;const calls=[];
let currentSource=input.source;
const component=()=>({
 getState_PrimitiveId:()=>o.invalid_id?'':'id-'+count,
 getState_Component:()=>({libraryUuid:o.wrong_library?'other':last[0].libraryUuid,uuid:o.wrong_device?'other':'local-dev'}),
 getState_SubPartName:()=>last[3],getState_X:()=>o.wrong_readback?999:last[1],getState_Y:()=>last[2],
 getState_Rotation:()=>o.wrong_rotation?last[4]:((360-last[4])%360),getState_Mirror:()=>last[5],getState_AddIntoBom:()=>last[6],getState_AddIntoPcb:()=>last[7]
});
const eda={
 sys_Environment:{getEditorCurrentVersion:()=>o.wrong_version?'other':'3.2.149.88089769'},
 dmt_Project:{getCurrentProjectInfo:async()=>({uuid:'p'})},
 dmt_SelectControl:{getCurrentDocumentInfo:async()=>{
  reads++;return {uuid:(o.changed_target&&reads>1)||o.wrong_target||(o.target_after_write&&count>0)?'other':'d',tabId:'t',documentType:1,parentProjectUuid:'p'};
 }},
 sys_FileManager:{getDocumentSource:async()=>{
  sourceReads++;let value=currentSource;
  if(o.volatile_header){
   const end=value.indexOf('\n');const line=value.slice(0,end);const separator=line.indexOf('||');
   const header=JSON.parse(line.slice(separator+2,-1));
   Object.assign(header,{client:'client-'+sourceReads,updateTime:sourceReads,version:String(sourceReads)});
   value=line.slice(0,separator+2)+JSON.stringify(header)+'|'+value.slice(end);
  }
  return o.unstable&&sourceReads%2===0?value+'edit':value;
 }},
 lib_Device:{search:async(...args)=>{calls.push({method:'search',args});return [];},
  get:async(...args)=>{calls.push({method:'device-get',args});return {subPartNames:['part.1']};}},
 sch_PrimitiveComponent:{
  create:async(...args)=>{calls.push({method:'create',args});last=args;count++;
   if(!o.source_unchanged)currentSource+='+placed';if(o.create_throw)throw new Error('create interrupted');return o.undefined_create?undefined:component();},
  get:async(id)=>{calls.push({method:'get',id});return o.get_missing?undefined:o.pin_query?
   {getState_PrimitiveId:()=>id,getState_Designator:()=> 'U1'}:component();},
  getAllPinsByPrimitiveId:async(id)=>{
   calls.push({method:'pins',id});if(o.pin_change)currentSource+='+edited';if(o.pins_missing)return undefined;
   return Array.from({length:o.many_pins?501:2},(_,i)=>({getState_PrimitiveId:()=> 'pin-'+(o.duplicate_pin_id?0:i),
    getState_PinNumber:()=>String(o.duplicate_pin_number?1:i+1),getState_PinName:()=> 'signal-'+i,
    getState_X:()=>o.invalid_pin?NaN:100+i,getState_Y:()=>200,getState_Rotation:()=>90,
    getState_PinLength:()=>10,getState_NoConnected:()=>undefined}));
  }
 }
};
eda.sys_FileManager.getProjectFile=async(...args)=>{
 calls.push({method:'project-file',args});
 return o.no_project_file?undefined:{size:o.oversized_project?52428801:3,name:'fixture.epro2',
 arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer};
};
eda.sys_FileManager.extractProjectInfo=async()=>{
 calls.push({method:'project-info'});if(o.project_change)currentSource+='edit';
 return {editorVersion:'3.2.149.88089769'};
};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('eda',input.script)(eda).then(
 result=>process.stdout.write(JSON.stringify({result,calls})),
 error=>process.stdout.write(JSON.stringify({error:String(error),calls})));
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for fixed drawing script integration tests")
class DrawingRuntimeTests(unittest.TestCase):
    def run_script(self, script=None, source="fixture-source", **options):
        process = subprocess.run([shutil.which("node"), "-e", HARNESS], input=json.dumps({
            "script": script or placement_script(snapshot(), parameters()), "source": source, "options": options}),
            text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_success_uses_exact_parameters_and_fresh_get(self):
        result = self.run_script()
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["calls"], [
            {"method": "create", "args": [{"libraryUuid": "lib", "uuid": "dev"}, 100, 200, None, 90, False, True, True]},
            {"method": "get", "id": "id-1"}, {"method": "pins", "id": "id-1"}])
        self.assertEqual(result["result"]["source"], "fixture-source+placed")

    def test_quarter_turns_forward_request_and_check_converted_readback(self):
        for requested, expected in ((0, 0), (90, 270), (180, 180), (270, 90)):
            p = parameters()
            p.update(requested_api_rotation=requested, expected_canvas_rotation=expected)
            result = self.run_script(script=placement_script(snapshot(), p))
            self.assertEqual(result["result"]["status"], "verified")
            self.assertEqual(result["result"]["observed"]["rotation"], expected)
            self.assertEqual(result["calls"][0]["args"][4], requested)

    def test_project_export_is_bounded_read_only_and_checks_source(self):
        result = self.run_script(script=PROJECT_LIBRARY_QUERY)
        self.assertEqual(result["result"]["archive_base64"], "AQID")
        self.assertEqual(result["calls"], [
            {"method": "project-file", "args": [None, None, "epro2"]}, {"method": "project-info"}])
        for option in ("no_project_file", "oversized_project", "project_change"):
            result = self.run_script(script=PROJECT_LIBRARY_QUERY, **{option: True})
            self.assertIn("error", result)
            self.assertFalse(any(c["method"] == "create" for c in result["calls"]))

    def test_identity_or_source_changes_block_before_create(self):
        for options in ({"wrong_target": True}, {"changed_target": True}, {"unstable": True}, {"source": "edited"}, {"wrong_version": True}):
            with self.subTest(options=options):
                result = self.run_script(**options)
                self.assertEqual(result["result"]["status"], "blocked")
                self.assertFalse(result["calls"])

    def test_write_or_readback_failure_reports_uncertain(self):
        for key in ("undefined_create", "create_throw", "get_missing", "wrong_readback", "invalid_id", "target_after_write", "source_unchanged",
                    "wrong_rotation", "wrong_device", "wrong_library", "pins_missing", "invalid_pin",
                    "duplicate_pin_id", "duplicate_pin_number", "many_pins"):
            with self.subTest(key=key):
                result = self.run_script(**{key: True})
                self.assertEqual(result["result"]["status"], "uncertain")
                self.assertEqual(sum(c["method"] == "create" for c in result["calls"]), 1)

    def test_user_strings_stay_data(self):
        p = parameters()
        p["device_uuid"] = "');throw new Error('injected');//\u2028"
        result = self.run_script(script=placement_script(snapshot(), p))
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["calls"][0]["args"][0]["uuid"], p["device_uuid"])

    def test_snapshot_reads_source_twice_without_writes(self):
        result = self.run_script(script=SNAPSHOT_QUERY)
        self.assertEqual(result["result"]["source"], "fixture-source")
        self.assertFalse(result["calls"])
        self.assertIn("error", self.run_script(script=SNAPSHOT_QUERY, unstable=True))

    def test_export_header_churn_is_allowed_but_never_proves_a_write(self):
        source = doc_source()
        captured = self.run_script(script=SNAPSHOT_QUERY, source=source, volatile_header=True)
        self.assertEqual(comparable_source(captured["result"]["source"]), comparable_source(source))
        before = snapshot()
        before["source"] = source
        script = placement_script(before, parameters())
        success = self.run_script(script=script, source=source, volatile_header=True)
        self.assertEqual(success["result"]["status"], "verified")
        unchanged = self.run_script(script=script, source=source, volatile_header=True, source_unchanged=True)
        self.assertEqual(unchanged["result"]["status"], "uncertain")
        for changed in (doc_source(uuid="other"), doc_source(editVersion="other"),
                        doc_source(unknown="new"), source + "edited"):
            with self.subTest(changed=changed):
                result = self.run_script(script=script, source=changed, volatile_header=True)
                self.assertEqual(result["result"]["status"], "blocked")
                self.assertFalse(result["calls"])
        self.assertIn("error", self.run_script(script=SNAPSHOT_QUERY, source=source, volatile_header=True, unstable=True))

    def test_library_queries_pass_identifiers_as_data(self):
        keyword = "');throw new Error('injected');//"
        result = self.run_script(script=device_search_script(keyword))
        self.assertEqual(result["calls"], [{"method": "search", "args": [keyword, None, None, None, 10, 1]}])
        result = self.run_script(script=device_details_script("lib", keyword))
        self.assertEqual(result["calls"], [{"method": "device-get", "args": [keyword, "lib"]}])
        self.assertEqual(result["result"]["item"]["subPartNames"], ["part.1"])

    def test_pin_query_keeps_identifier_as_data_and_never_writes(self):
        target = "');throw new Error('injected');//"
        result = self.run_script(script=component_pins_script(target), pin_query=True)
        self.assertEqual(result["calls"], [{"method": "get", "id": target}, {"method": "pins", "id": target}])
        self.assertEqual(result["result"]["pins"][0]["number"], "1")
        self.assertEqual(result["result"]["total"], 2)
        self.assertFalse(result["result"]["truncated"])

    def test_pin_query_rejects_missing_changed_or_invalid_data(self):
        for option in ("get_missing", "pins_missing", "pin_change", "invalid_pin", "unstable"):
            with self.subTest(option=option):
                result = self.run_script(script=component_pins_script("e2"), pin_query=True, **{option: True})
                self.assertIn("error", result)
                self.assertFalse(any(c["method"] == "create" for c in result["calls"]))

    def test_pin_query_explicitly_reports_truncation(self):
        result = self.run_script(script=component_pins_script("e2"), pin_query=True, many_pins=True)["result"]
        self.assertTrue(result["truncated"])
        self.assertEqual((result["total"], len(result["pins"])), (501, 500))


if __name__ == "__main__":
    unittest.main()
