import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from easyeda_assist.drawing_api import ROTATION_CONVENTION
from easyeda_assist.drawing_circuit import CONTRACT, audit_circuit, audit_script, inspect_library, operation_script, verify_reply, verify_relocation
from easyeda_assist.drawing_execution import approve_plan, execute_plan, preflight
from easyeda_assist.drawing_source import wire_segments
from easyeda_assist.storage import digest, write_new
from test_drawing_execution import HARNESS, library_record, reply, snapshot
from test_project import design


def line(kind, rid, value):
    return json.dumps({"type": kind, "ticket": 1, "id": rid}) + "||" + json.dumps(value) + "|\n"


def library(number_key="Pin Number", duplicate=False):
    log = line("DOCHEAD", "header", {"docType": "SYMBOL", "uuid": "sym", "client": "c"})
    log += line("PART", "part", {"title": "", "BBOX": [-10, -5, 10, 5]})
    log += line("PIN", "pin", {"partId": "part", "x": 10, "y": 5})
    log += line("ATTR", "number", {"parentId": "pin", "key": number_key, "value": "1"})
    if duplicate:
        log += line("ATTR", "number2", {"parentId": "pin", "key": "NUMBER", "value": "2"})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("test.elibu", log)
        archive.writestr("device2.json", json.dumps({"devices": {"dev": {"path": "lib", "symbol_type": 2, "attributes": {"Symbol": "sym"}}}}))
    raw = stream.getvalue()
    return {"library_uuid": "lib", "device_uuid": "dev", "file_size": len(raw), "archive_base64": base64.b64encode(raw).decode()}


def source(*entries):
    return line("DOCHEAD", "header", {"docType": "SCH_PAGE", "uuid": "d"}) + "".join(entries)


class CircuitTests(unittest.TestCase):
    def test_baseline_global_flag_resolves_only_connected_unambiguous_net(self):
        from easyeda_assist.drawing_circuit import baseline_wire_nets, point_on
        from easyeda_assist.drawing_source import records
        base = source(line("WIRE", "w", {}),
            line("COMPONENT", "f", {"x": 20, "y": -10}),
            line("ATTR", "name", {"parentId": "f", "key": "Global Net Name", "value": "5V"}))
        self.assertEqual(baseline_wire_nets(records(base), "w", [0,10,20,10]), {"5V"})
        self.assertEqual(baseline_wire_nets(records(base), "w", [0,11,20,11]), set())
        conflict = base + line("ATTR", "net", {"parentId": "w", "key": "NET", "value": "GND"})
        self.assertEqual(baseline_wire_nets(records(conflict), "w", [0,10,20,10]), {"5V", "GND"})
        self.assertTrue(point_on((294.99999999999994, 10), ("H",10,295,320)))
        self.assertFalse(point_on((294.999, 10), ("H",10,295,320)))

    def test_explicit_designator_validation_and_page_bounds(self):
        params = {"library_uuid": "lib", "device_uuid": "dev", "sub_part_name": "", "x": 100, "y": 200,
            "unit": "10mil", "requested_api_rotation": 0, "expected_canvas_rotation": 0,
            "rotation_convention": ROTATION_CONVENTION, "mirror": False, "add_into_bom": True,
            "add_into_pcb": True, "expected_pins": [{"number": "1", "x": 110, "y": 195}],
            "library_evidence": {"path": "unused", "sha256": "0" * 64}, "designator": "C1"}
        op = {"operation": "place_component", "parameters": params}
        from easyeda_assist.drawing_circuit import validate_operation
        validate_operation(op)
        params["designator"] = "U?"
        with self.assertRaisesRegex(ValueError, "位号"):
            validate_operation(op)

    def test_library_pin_formats_rotation_and_source_y(self):
        for key in ("Pin Number", "NUMBER"):
            for rotation, xy in ((0, (110, 195)), (90, (95, 190)), (180, (90, 205)), (270, (105, 210))):
                p = {"library_uuid": "lib", "device_uuid": "dev", "sub_part_name": "", "x": 100, "y": 200,
                     "expected_canvas_rotation": rotation, "expected_pins": [{"number": "1", "x": xy[0], "y": xy[1]}]}
                with self.subTest(key=key, rotation=rotation):
                    self.assertEqual(inspect_library(library(key), p)["pins"], p["expected_pins"])
        with self.assertRaisesRegex(ValueError, "歧义"):
            inspect_library(library(duplicate=True), p)

    def test_wire_reversal_and_source_coordinate_readback(self):
        self.assertEqual(wire_segments([[0, 10, 10, 10], [20, 10, 10, 10]]), wire_segments([20, 10, 0, 10]))
        before = {**snapshot(), "source": source()}
        op = {"operation": "add_wire", "parameters": {"net": "N", "line": [0, 10, 20, 10]}}
        after = source(line("WIRE", "w1", {"dots": [[20, -10, 0, -10]]}), line("ATTR", "a1", {"parentId": "w1", "key": "NET", "value": "N"}))
        reply = {"status": "verified", "attempted": True, "primitive_id": "w1", "identity": before["identity"], "source": after,
                 "observed": {"primitive_id": "w1", "net": "N", "line": [20, 10, 0, 10]}}
        self.assertEqual(verify_reply(reply, before, op)["source"], after)
        reply["source"] = after.replace('-10', '10')
        with self.assertRaisesRegex(ValueError, "几何"):
            verify_reply(reply, before, op)

    def test_wire_source_line_group_readback(self):
        before = {**snapshot(), "source": source()}
        op = {"operation": "add_wire", "parameters": {"net": "N", "line": [[0, 0, 10, 0], [10, 0, 10, 10]]}}
        after = source(
            line("WIRE", "w1", {"zIndex": 1}),
            line("LINE", "l1", {"startX": 0, "startY": 0, "endX": 10, "endY": 0, "lineGroup": "w1"}),
            line("LINE", "l2", {"startX": 10, "startY": 0, "endX": 10, "endY": -10, "lineGroup": "w1"}),
            line("ATTR", "a1", {"parentId": "w1", "key": "NET", "value": "N"}))
        reply = {"status": "verified", "attempted": True, "primitive_id": "w1", "identity": before["identity"],
                 "source": after, "observed": {"primitive_id": "w1", "net": "N", "line": [0, 0, 10, 0, 10, 10]}}
        self.assertEqual(verify_reply(reply, before, op)["source"], after)

    def test_wire_merge_into_existing_same_net_group(self):
        before_source = source(
            line("WIRE", "w1", {"zIndex": 1}),
            line("LINE", "l1", {"startX": 0, "startY": 0, "endX": 10, "endY": 0, "lineGroup": "w1"}),
            line("ATTR", "a1", {"parentId": "w1", "key": "NET", "value": "GND", "zIndex": 2}))
        after_source = source(
            line("WIRE", "w1", {"zIndex": 1}),
            line("LINE", "l1", {"startX": 0, "startY": 0, "endX": 10, "endY": 0, "lineGroup": "w1"}),
            line("ATTR", "a1", {"parentId": "w1", "key": "NET", "value": "GND", "zIndex": 9}),
            line("LINE", "l2", {"startX": 10, "startY": 0, "endX": 10, "endY": -10, "lineGroup": "w1"}),
            line("ATTR", "a2", {"parentId": "w1", "key": "NET", "value": "GND", "zIndex": 10}))
        before = {**snapshot(), "source": before_source}
        op = {"operation": "add_wire", "parameters": {"net": "GND", "line": [10, 0, 10, 10]}}
        reply = {"status": "verified", "attempted": True, "primitive_id": "w1",
                 "identity": before["identity"], "source": after_source,
                 "observed": {"primitive_id": "w1", "net": "GND", "line": [0, 0, 10, 0, 10, 10]}}
        self.assertEqual(verify_reply(reply, before, op)["source"], after_source)

    def test_netflag_source_coordinate_readback(self):
        before = {**snapshot(), "source": source()}
        op = {"operation": "place_net_flag", "parameters": {"x": 100, "y": 200, "mirror": False, "expected_canvas_rotation": 0, "net": "GND"}}
        after = source(line("COMPONENT", "f1", {"x": 100, "y": -200, "rotation": 0, "isMirror": False}))
        reply = {"status": "verified", "attempted": True, "primitive_id": "f1", "identity": before["identity"], "source": after,
                 "observed": {"primitive_id": "f1", "x": 100, "y": 200, "mirror": False, "rotation": 0, "component_type": "netflag", "net": "GND"},
                 "pins": [{"primitive_id": "pin", "number": "1", "x": 100, "y": 200}]}
        verify_reply(reply, before, op)
        reply["source"] = after.replace('-200', '200')
        with self.assertRaisesRegex(ValueError, "坐标"):
            verify_reply(reply, before, op)

    def test_relocation_verifies_only_target_component_and_its_library_attributes(self):
        before_source = source(
            line("COMPONENT", "u1", {"x": 2200, "y": -1500, "rotation": 0, "isMirror": False}),
            line("ATTR", "name", {"parentId": "u1", "key": "Name", "value": None, "x": 2165, "y": -1470}),
            line("ATTR", "ref", {"parentId": "u1", "key": "Designator", "value": None, "x": 2165, "y": -1520}),
        )
        before = {**snapshot(), "source": before_source}
        after_source = source(
            line("COMPONENT", "u1", {"x": 585, "y": -412, "rotation": 0, "isMirror": False}),
            line("ATTR", "name", {"parentId": "u1", "key": "Name", "value": "={Manufacturer Part}", "x": 550, "y": -385}),
            line("ATTR", "ref", {"parentId": "u1", "key": "Designator", "value": "U1", "x": 550, "y": -435}),
            line("ATTR", "maker", {"parentId": "u1", "key": "Manufacturer", "value": "AMS"}),
        )
        reply = {"status": "verified", "attempted": True, "primitive_id": "u1",
                 "identity": before["identity"], "source": after_source,
                 "x": 585, "y": 412, "designator": "U1"}
        verified = verify_relocation(reply, before, "u1",
            {"x": 2200, "y": 1500, "designator": "U?"},
            {"x": 585, "y": 412, "designator": "U1"})
        self.assertEqual(verified["source"], after_source)
        reply["source"] = after_source + line("WIRE", "unrelated", {"dots": [0, 0, 10, 0]})
        with self.assertRaisesRegex(ValueError, "非目标器件属性"):
            verify_relocation(reply, before, "u1",
                {"x": 2200, "y": 1500, "designator": "U?"},
                {"x": 585, "y": 412, "designator": "U1"})

    def test_drc_rejects_unknown_or_inconsistent_results(self):
        before = {**snapshot(), "source": source()}
        plan = {"operations": [], "connectivity": [], "result_designators": {}}
        with patch("easyeda_assist.schematic.components_from_raw", return_value={}):
            base = {"identity": before["identity"], "source": before["source"], "raw_netlist": "{}", "drc": [], "drc_passed": True}
            self.assertTrue(audit_circuit(base, before, plan)["drc_passed"])
            for drc, passed in (([{"type": "warn"}], True), ([{"type": "unknown"}], False), (None, True)):
                with self.subTest(drc=drc), self.assertRaises(ValueError):
                    audit_circuit({**base, "drc": drc, "drc_passed": passed}, before, plan)
            self.assertFalse(audit_circuit({**base, "drc": [{"type": "warn"}], "drc_passed": False}, before, plan)["drc_passed"])


class CircuitExecutionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.before = {**snapshot(), "source": source()}
        saved = write_new(self.root / "source.json", self.before)
        archive = write_new(self.root / "library.json", library())
        self.project = design()
        self.project["drawing_policy"] = {"mode": "explicit_batch", "trigger": "explicit_user_request",
            "allowed_operations": ["place_component", "add_wire"], "require_batch_approval": True,
            "require_snapshot_binding": True}
        self.p = {"library_uuid": "lib", "device_uuid": "dev", "sub_part_name": "", "x": 100, "y": 200,
            "unit": "10mil", "requested_api_rotation": 0, "expected_canvas_rotation": 0,
            "rotation_convention": ROTATION_CONVENTION, "mirror": False, "add_into_bom": True,
            "add_into_pcb": True, "expected_pins": [{"number": "1", "x": 110, "y": 195}],
            "library_evidence": {"path": str(archive), "sha256": digest(archive)}}

        def operation(oid, kind, params):
            return {"id": oid, "operation": kind, "target": "fixture", "before": "absent", "after": "added",
                "expected_result": "exact readback", "parameters": params}

        self.plan = {"schema_version": 1, "project_id": self.project["id"], "created_at": "fixture",
            "request": "fixture circuit", "execution_contract": CONTRACT,
            "target": {"window_id": "w", "project_uuid": "p", "document_uuid": "d", "domain": "schematic",
                "snapshot_path": str(saved), "snapshot_sha256": digest(saved)},
            "operations": [operation("device", "place_component", self.p),
                operation("wire", "add_wire", {"unit": "10mil", "net": "N", "line": [110, 195, 130, 195]})],
            "connectivity": [{"component": "device", "pin": "1", "net": "N"}], "evidence": ["fixture"],
            "unresolved": [], "readback_plan": "source, pins, netlist and DRC", "failure_plan": "stop"}
        device_source = self.before["source"] + line("COMPONENT", "new-id", {"x": 100, "y": -200, "rotation": 0, "isMirror": False})
        device_source += line("ATTR", "dev-attr", {"parentId": "new-id", "key": "Device", "value": "local-dev"})
        device_source += line("ATTR", "ref-attr", {"parentId": "new-id", "key": "Designator", "value": "U1"})
        device_after = {**self.before, "source": device_source}
        device_reply = reply(self.before, {**self.p, "expected_project_device_uuid": "local-dev"})
        device_reply.update(source=device_source, project_evidence=library_record(device_after))
        device_reply["observed"].update(component_type="part", designator="U1")
        wire_source = device_source + line("WIRE", "wire-id", {"dots": [[110, -195, 130, -195]]})
        wire_source += line("ATTR", "net-attr", {"parentId": "wire-id", "key": "NET", "value": "N"})
        wire_reply = {"status": "verified", "attempted": True, "primitive_id": "wire-id",
            "identity": self.before["identity"], "source": wire_source,
            "observed": {"primitive_id": "wire-id", "line": [110, 195, 130, 195], "net": "N"}}
        self.bridge = Mock()
        self.bridge.drawing_components.return_value = {**self.before, "components": [], "truncated": False}
        self.bridge._circuit_operation.side_effect = [device_reply, wire_reply]
        self.audit = {"identity": self.before["identity"], "source": wire_source, "drc": [], "drc_passed": True,
            "raw_netlist": json.dumps({"version": "2.0.0", "components": {"c": {"props": {"Designator": "U1"},
                "pinInfoMap": {"p": {"number": "1", "net": "N"}}}}})}
        self.bridge.drawing_circuit_audit.return_value = self.audit

    def execute(self):
        checked, _ = preflight(self.project, self.plan)
        consent = approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture assent", "test")
        return execute_plan(self.project, self.plan, consent, lambda: self.bridge)

    def test_complete_circuit_and_final_netlist_release_lock(self):
        result = self.execute()
        self.assertEqual((result["status"], result["completed"]), ("verified", 2))
        self.assertFalse(Path(result["target_lock"]).exists())
        self.assertEqual(self.bridge._circuit_operation.call_count, 2)
        self.assertTrue((Path(result["run_dir"]) / "final-audit.json").exists())

    def test_wrong_final_net_preserves_lock(self):
        self.audit["raw_netlist"] = self.audit["raw_netlist"].replace('"net": "N"', '"net": "WRONG"')
        result = self.execute()
        self.assertEqual((result["status"], result["completed"]), ("verification_failed", 2))
        self.assertTrue(Path(result["target_lock"]).exists())

    def test_drc_warning_is_verification_failure(self):
        self.audit.update(drc=[{"type": "warn", "message": "fixture warning"}], drc_passed=False)
        result = self.execute()
        self.assertEqual(result["status"], "verification_failed")
        self.assertTrue(Path(result["target_lock"]).exists())

    def test_missing_live_components_blocks_before_write(self):
        del self.bridge.drawing_components.return_value["components"]
        result = self.execute()
        self.assertEqual((result["status"], result["completed"]), ("blocked", 0))
        self.bridge._circuit_operation.assert_not_called()

    def test_next_intent_storage_failure_keeps_partial_step(self):
        from easyeda_assist.drawing_execution import _durable_new
        def fail_second_intent(path, value):
            if path.name == "002-intent.json":
                raise OSError("disk full")
            return _durable_new(path, value)
        with patch("easyeda_assist.drawing_execution._durable_new", side_effect=fail_second_intent):
            result = self.execute()
        self.assertEqual((result["status"], result["completed"]), ("partial", 1))
        self.assertTrue(Path(result["target_lock"]).exists())
        self.assertEqual(self.bridge._circuit_operation.call_count, 1)

    def test_uncertain_write_stops_without_retry_or_audit(self):
        self.bridge._circuit_operation.side_effect = TimeoutError("request timed out")
        result = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(Path(result["target_lock"]).exists())
        self.assertEqual(self.bridge._circuit_operation.call_count, 1)
        self.bridge.drawing_circuit_audit.assert_not_called()


CIRCUIT_HARNESS = HARNESS.replace("const AsyncFunction=", r"""
let flag=false;
eda.sch_PrimitiveWire={
 create:async(...args)=>{calls.push({method:'wire-create',args});count++;last=args;currentSource+='+wire';
  if(o.create_throw)throw new Error('write interrupted');return {getState_PrimitiveId:()=> 'wire'};},
 get:async(id)=>{calls.push({method:'wire-get',id});return {getState_PrimitiveId:()=>id,
  getState_Net:()=>last[1],getState_Line:()=>last[0]};}
};
eda.sch_PrimitiveComponent.createNetFlag=async(...args)=>{
 calls.push({method:'flag-create',args});last=args;flag=true;count++;currentSource+='+flag';
 return {getState_PrimitiveId:()=> 'flag'};
};
const oldGet=eda.sch_PrimitiveComponent.get;
eda.sch_PrimitiveComponent.get=async(id)=>flag?{
 getState_PrimitiveId:()=>id,getState_X:()=>last[2],getState_Y:()=>last[3],getState_Rotation:()=>0,
 getState_Mirror:()=>false,getState_ComponentType:()=> 'netflag',getState_Net:()=>last[1]
}:oldGet(id);
eda.sch_PrimitiveComponent.getAllPinsByPrimitiveId=async()=>[{
 getState_PrimitiveId:()=> 'flag-pin',getState_PinNumber:()=> '1',getState_X:()=>last[2],getState_Y:()=>last[3]
}];
eda.sch_ManufactureData={getNetlistFile:async(...args)=>{
 calls.push({method:'netlist',args});return {size:2,text:async()=> '{}'};
}};
eda.sch_Drc={check:async(...args)=>{calls.push({method:'drc',args});
 if(o.audit_change)currentSource+='+edit';return o.boolean_drc?true:o.warn?[{type:'warn'}]:[];
}};
const AsyncFunction=""")
CIRCUIT_HARNESS = CIRCUIT_HARNESS.replace("new AsyncFunction('eda',input.script)(eda)",
    "new AsyncFunction('eda','setTimeout',input.script)(eda,(resolve)=>resolve())")


@unittest.skipUnless(shutil.which("node"), "Node required for fixed circuit script tests")
class CircuitRuntimeTests(unittest.TestCase):
    def run_script(self, script, **options):
        result = subprocess.run([shutil.which("node"), "-e", CIRCUIT_HARNESS], input=json.dumps({
            "script": script, "source": "fixture-source", "options": options}), text=True,
            encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def wire(self, net="N"):
        return operation_script(snapshot(), {"operation": "add_wire",
            "parameters": {"unit": "10mil", "net": net, "line": [0, 0, 20, 0]}})

    def test_wire_signature_data_and_single_write(self):
        net = "');throw new Error('injection');//"
        result = self.run_script(self.wire(net))
        self.assertEqual(result["result"]["status"], "verified")
        creates = [c for c in result["calls"] if c["method"] == "wire-create"]
        self.assertEqual(creates, [{"method": "wire-create", "args": [[0, 0, 20, 0], net]}])
        self.assertEqual(sum(c["method"] == "wire-get" for c in result["calls"]), 3)

    def test_wire_preconditions_block_and_write_timeout_never_retries(self):
        for options in ({"wrong_target": True}, {"wrong_version": True}, {"unstable": True}):
            result = self.run_script(self.wire(), **options)
            self.assertEqual(result["result"]["status"], "blocked")
            self.assertFalse(result["calls"])
        result = self.run_script(self.wire(), create_throw=True)
        self.assertEqual(result["result"]["status"], "uncertain")
        self.assertEqual(len(result["calls"]), 1)

    def test_net_flag_signature_and_pin_readback(self):
        params = {"unit": "10mil", "x": 100, "y": 200, "identification": "Ground", "net": "GND",
            "mirror": False, "requested_api_rotation": 0, "expected_canvas_rotation": 0,
            "rotation_convention": ROTATION_CONVENTION}
        result = self.run_script(operation_script(snapshot(), {"operation": "place_net_flag", "parameters": params}))
        self.assertEqual(result["result"]["status"], "verified")
        self.assertEqual(result["calls"], [{"method": "flag-create", "args": ["Ground", "GND", 100, 200, 0, False]}])
        self.assertEqual(result["result"]["pins"][0]["x"], 100)

    def test_audit_requires_detailed_drc_and_unchanged_source(self):
        script = audit_script(snapshot())
        result = self.run_script(script)
        self.assertTrue(result["result"]["drc_passed"])
        self.assertEqual(result["calls"][-1], {"method": "drc", "args": [True, False, True]})
        self.assertFalse(self.run_script(script, warn=True)["result"]["drc_passed"])
        for options in ({"boolean_drc": True}, {"audit_change": True}):
            self.assertIn("error", self.run_script(script, **options))
