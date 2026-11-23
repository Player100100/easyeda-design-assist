"""PCB scope/source/consent tests; all fixtures are fictional, never live CAD."""
from copy import deepcopy
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from easyeda_assist.pcb import (make_snapshot, new_plan, preview, sha, source_records,
    validate_operation, verify_operation, inspect, workflow, INTENTS, comparable_source, compare,
    drc_identity, drc_leaves, polygon_signature, same_source)
from easyeda_assist.pcb_api import FIELDS, capture_script, operation_script, native_route_script, repair_delete_script
from easyeda_assist.pcb_execution import approve, execute, export_files
from easyeda_assist.storage import write_new, read_json
from easyeda_assist.cli import main
from easyeda_assist.pcb_stm32 import verify_additions,verify_normalized_additions


def source(records):
    return '\n'.join(json.dumps(e, sort_keys=True) + '||' + json.dumps(v, sort_keys=True) + '|'
                     for e, v in records)


def fixture():
    raw = {"identity": {"project_uuid": "p", "document_uuid": "b", "tab_id": "tab", "editor_version": "4.1.100"},
        "source": source([({"type": "DOCHEAD"}, {"docType":"PCB", "uuid":"b"}),
            ({"type":"CANVAS"}, {"unit":"mm"}),
            ({"type":"LAYER", "id":1}, {"layerType":"TOP"}),
            ({"type":"COMPONENT", "id":"c", "ticket":1}, {"positionX":100,"positionY":100,"angle":0}),
            ({"type":"PAD_NET","id":"c"}, {"padNum":"1","padNet":"N"}),
            ({"type":"PAD_NET","id":"c"}, {"padNum":"2","padNet":"GND"})]),
        "collections": {k: [] for k in FIELDS}, "outputs": {}, "layer_ids": {"TOP":1,"BOTTOM":2,"BOARD_OUTLINE":11},
        "complete": True, "read_errors": [], "api_unit":"mil", "coordinate_system":"data-origin"}
    comp = {"id":"c","kind":"Component", "properties":{"PrimitiveId":"c", "X":100,"Y":100,"Rotation":0,
        "Layer":1,"Designator":"U1","PrimitiveLock":False},
        "pads":[{"id":"c1","kind":"Pad","properties":{"PrimitiveId":"c1","PadNumber":"1","Net":"N","X":90,"Y":100}},
                {"id":"c2","kind":"Pad","properties":{"PrimitiveId":"c2","PadNumber":"2","Net":"GND","X":110,"Y":100}}]}
    raw["collections"]["Component"] = [comp]
    values = {"layers":[{"id":1,"locked":False,"layerStatus":1},{"id":2,"locked":False,"layerStatus":1},
                         {"id":11,"locked":False,"layerStatus":1}],
        "stackup":{"name":"fixture"},"rules":{"name":"fixture"},"net_rules":[],"net_classes":[],
        "differential_pairs":[],"equal_length_groups":[],"origin":{"offsetX":0,"offsetY":0},
        "nets":[{"net":"N"},{"net":"GND"}],"board":{"name":"board"},"drc":[],"netlist":"fixture","footprints":[]}
    raw["outputs"] = {k:{"status":"returned","value":v} for k,v in values.items()}
    return make_snapshot(raw,"w",49620)


def line_op(net="N"):
    return {"id":"op","operation":"add_line", "parameters":{"net":net,"layer":"TOP","start":[100,100],"end":[200,100],"width":10}, "rationale":"fixture route"}


def add_line(before, *, net="N", new_drc=False):
    after = deepcopy(before)
    env, payload = {"type":"LINE","id":"l"}, {"netName":net,"layerId":1,"startX":100,"startY":100,"endX":200,"endY":100,"width":10}
    after["source"] += '\n' + source([(env,payload)])
    after["source_sha256"] = hashlib.sha256(after["source"].encode()).hexdigest()
    after["collections"]["Line"] = [{"id":"l","kind":"Line","properties":{"PrimitiveId":"l","Net":net,"Layer":1,
        "StartX":100,"StartY":100,"EndX":200,"EndY":100,"LineWidth":10}}]
    if new_drc:
        after["outputs"]["drc"]["value"] = [{"rule":"clearance","ids":["l","c1"]}]
    return after


class PCBTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.before = fixture()
        self.snapshot_path = write_new(self.root/'pcb.json',self.before)
        raw = json.dumps({"version":"2.0.0","components":{"sch-c":{"props":{"Designator":"U1"},
            "pinInfoMap":{"p1":{"number":"1","net":"N"},"p2":{"number":"2","net":"GND"}}}}})
        self.schematic_path = write_new(self.root/'schematic.json',{"schema_version":1,"kind":"schematic-connectivity", "project":{"uuid":"p"},
            "raw_netlist":raw,"raw_sha256":hashlib.sha256(raw.encode()).hexdigest()})
        self.plan = read_json(new_plan(self.snapshot_path,self.schematic_path,"partial","fixture",["c"],["N","GND"]))
        self.plan.update(unresolved=[],operations=[line_op()])
        self.plan["constraints"] = {"unit":"mil","coordinate_system":"data-origin","board_region":[0,0,1000,1000],
            "calibration":{"editor_version":"4.1.100","document_uuid":"b","evidence":"fixture only"},
            "stackup_evidence":"fixture only","rules_evidence":"fixture only","min_via_hole":8,"min_annular_ring":4,
            "net_constraints":{n:{"kind":"ordinary","source":"fixture only","return_path":"fixture ground", "validation":"fixture DRC", "min_width":6}
                               for n in ["N","GND"]}}

    def test_all_four_intents_have_workflow_and_plan(self):
        for intent in INTENTS:
            self.assertTrue(workflow(intent)["steps"])
            p = read_json(new_plan(self.snapshot_path,self.schematic_path,intent,"fixture",["c"],["N"]))
            self.assertEqual(p["intent"],intent)
        with self.assertRaisesRegex(ValueError,"显式"):
            new_plan(self.snapshot_path,self.schematic_path,"partial","fixture")

    def test_experimental_intermediate_drc_is_reported_and_other_guards_remain(self):
        after=add_line(self.before,new_drc=True)
        with self.assertRaises(ValueError):
            verify_operation(self.before,after,line_op(),'l',self.plan['scope'])
        report=verify_operation(self.before,after,line_op(),'l',self.plan['scope'],defer_drc=True)
        self.assertTrue(report['drc_deferred'])
        self.assertTrue(report['new_drc'])
        with self.assertRaises(ValueError):
            verify_operation(self.before,add_line(self.before,net='GND'),line_op(),'l',self.plan['scope'],defer_drc=True)

    def test_batched_additions_protect_source_and_immediate_readback(self):
        after=add_line(self.before)
        steps=[{'operation_id':'op','created_id':'l','category':'Line','properties':after['collections']['Line'][0]['properties']}]
        self.assertTrue(verify_additions(self.before,after,[line_op()],steps)['old_source_preserved'])
        bad=deepcopy(after);bad['source']=bad['source'].replace('"positionX": 100','"positionX": 101')
        with self.assertRaisesRegex(ValueError,'existing'):
            verify_additions(self.before,bad,[line_op()],steps)
        bad=deepcopy(steps);bad[0]['properties']['Net']='GND'
        with self.assertRaisesRegex(ValueError,'getter mismatch'):
            verify_additions(self.before,after,[line_op()],bad)
        with self.assertRaises(ValueError):
            verify_additions(self.before,add_line(self.before,net='GND'),[line_op()],steps)

    def test_native_collinear_split_coverage_retains_net_layer_and_width(self):
        after=add_line(self.before)
        row=after['collections']['Line'][0]
        row['properties']['EndX']=150
        extra=deepcopy(row);extra['id']='l2';extra['properties'].update(PrimitiveId='l2',StartX=150,EndX=200)
        after['collections']['Line'].append(extra)
        after['source']=after['source'].replace('"endX": 200','"endX": 150')+'\n'+source([({'type':'LINE','id':'l2'},{'netName':'N','layerId':1,'startX':150,'startY':100,'endX':200,'endY':100,'width':10})])
        self.assertTrue(verify_normalized_additions(self.before,after,[line_op()])['normalized_line_coverage'])
        for key,value in [('Net','GND'),('Layer',2),('LineWidth',11),('EndY',101)]:
            bad=deepcopy(after);bad['collections']['Line'][1]['properties'][key]=value
            with self.assertRaises(ValueError):verify_normalized_additions(self.before,bad,[line_op()])

    def test_source_preserves_repeated_pad_mapping_and_numeric_layers(self):
        records = source_records(self.before["source"])
        self.assertEqual(sum(k[0]=='PAD_NET' for k in records),2)
        self.assertEqual(inspect(self.before,net="N")["endpoints"][0]["pad_id"],"c1")

    def test_live_drc_tree_compares_violations_not_group_counts_or_positions(self):
        leaf={'errorType':'Connection Error','ruleName':'Common','objs':['pad-1'],
              'globalIndex':'err1','pos':{'x':10,'y':20},'net':'N',
              'explanation':{'errData':{'globalIndex':'err1','position':{'x':10,'y':20},'errorType':'No Connection'}}}
        shifted=deepcopy(leaf);shifted['globalIndex']='err99';shifted['pos']['x']=30
        shifted['explanation']['errData']['globalIndex']='err99';shifted['explanation']['errData']['position']['x']=30
        self.assertEqual(drc_identity(leaf),drc_identity(shifted))
        shifted['net']='OTHER';self.assertNotEqual(drc_identity(leaf),drc_identity(shifted))
        self.assertEqual(drc_leaves([{'count':1,'list':[{'count':1,'list':[leaf]}]}]),[leaf])
        with self.assertRaises(ValueError):drc_leaves([{'count':1,'list':[]}])

    def test_clock_only_resequence_cannot_hide_payload_or_identity_changes(self):
        old=self.before['source'];new=old.replace('"ticket": 1','"ticket": 999')
        self.assertTrue(same_source(old,new))
        self.assertFalse(same_source(old,new.replace('"positionX": 100','"positionX": 101')))
        self.assertFalse(same_source(old,new.replace('"id": "c"','"id": "other"')))

    def test_polygon_boundary_accepts_reversal_but_rejects_different_geometry(self):
        a=[0,0,'L',100,0,100,100,0,100,0,0]
        b=[0,0,'L',0,100,100,100,100,0,0,0]
        self.assertEqual(polygon_signature(a),polygon_signature([b]))
        b[4]=99
        self.assertNotEqual(polygon_signature(a),polygon_signature(b))

    def test_auto_layout_blocks_a_board_with_existing_copper(self):
        current=add_line(self.before)
        path=write_new(self.root/'routed-board.json',current)
        from easyeda_assist.storage import digest
        self.plan['baseline']={'path':str(path),'sha256':digest(path)}
        self.plan['intent']='full';self.plan['operations']=[{'id':'a','operation':'auto_place','parameters':{},'rationale':'fixture'}]
        self.plan['constraints']['placement_review']='fixture'
        report,_=preview(self.plan)
        self.assertFalse(report['ready_for_approval'])
        self.assertTrue(any('不会带动已有铜线' in f for f in report['findings']))

    def test_pour_preflight_rejects_unfulfilled_live_parameters(self):
        self.plan['operations']=[{'id':'p','operation':'add_pour','parameters':{'net':'GND','layer':'TOP',
            'polygon':[10,10,'L',200,10,200,200,10,200,10,10],'name':'lowercase','priority':3,'width':10},'rationale':'fixture'}]
        report,_=preview(self.plan)
        self.assertFalse(report['ready_for_approval'])
        self.assertTrue(any('参数未兑现' in f for f in report['findings']))
        self.plan['operations'][0]['parameters'].update(name='FIXTURE',priority=1,width=0.2)
        self.assertTrue(preview(self.plan)[0]['ready_for_approval'])

    def test_pcb_source_component_uses_xy_and_rounds_to_four_decimals(self):
        before=deepcopy(self.before)
        before['source']=before['source'].replace('"positionX": 100','"x": 100').replace('"positionY": 100','"y": 100')
        before['source_sha256']=hashlib.sha256(before['source'].encode()).hexdigest()
        after=deepcopy(before)
        after['source']=after['source'].replace('"x": 100','"x": 150.1235')
        after['source_sha256']=hashlib.sha256(after['source'].encode()).hexdigest()
        after['collections']['Component'][0]['properties']['X']=150.123456
        op={'id':'m','operation':'move_component','parameters':{'primitive_id':'c','x':150.123456,'y':100,'rotation':0},'rationale':'fixture'}
        verify_operation(before,after,op,None,self.plan['scope'])
        after['collections']['Component'][0]['properties']['X']=150.124
        with self.assertRaises(ValueError):verify_operation(before,after,op,None,self.plan['scope'])

    def test_observed_header_volatility_ignores_only_three_fields(self):
        original = self.before['source']
        head, tail = original.split('\n', 1)
        env, raw = head.split('||', 1)
        payload = json.loads(raw[:-1])
        payload.update(client='export-client', updateTime=123, version='123')
        exported = env+'||'+json.dumps(payload)+'|\n'+tail
        self.assertEqual(comparable_source(original), comparable_source(exported))
        self.assertEqual(source_records(original), source_records(exported))
        payload['uuid'] = 'another-document'
        self.assertNotEqual(comparable_source(original), comparable_source(env+'||'+json.dumps(payload)+'|\n'+tail))
        self.assertNotEqual(comparable_source(original), comparable_source(exported.replace('"unit": "mm"','"unit": "mil"')))

    def test_final_record_may_omit_separator_but_middle_record_cannot(self):
        original = self.before['source']
        self.assertEqual(source_records(original), source_records(original[:-1]))
        with self.assertRaises(ValueError):
            source_records(original.replace('|\n', '\n', 1))

    def test_missing_stackup_needs_independent_evidence_for_write_approval(self):
        snapshot = deepcopy(self.before)
        snapshot['outputs']['stackup'] = {'status':'error','error':'Error: undefined on 4.1.100'}
        path=write_new(self.root/'no-stackup.json',snapshot)
        from easyeda_assist.storage import digest
        self.plan['baseline']={'path':str(path),'sha256':digest(path)}
        report,_=preview(self.plan)
        self.assertFalse(report['ready_for_approval'])
        self.assertTrue(any('层叠API不可用' in f for f in report['findings']))
        self.plan['constraints']['external_stackup_evidence']='fixture board manufacturer stackup document'
        self.assertTrue(preview(self.plan)[0]['ready_for_approval'])

    def test_live_header_changes_do_not_invalidate_bound_design(self):
        exported=deepcopy(self.before)
        head,tail=exported['source'].split('\n',1)
        env,raw=head.split('||',1)
        payload=json.loads(raw[:-1]);payload.update(client='second-export',updateTime=99,version='99')
        exported['source']=env+'||'+json.dumps(payload)+'|\n'+tail
        exported['source_sha256']=hashlib.sha256(exported['source'].encode()).hexdigest()
        report=compare(self.before,exported)
        self.assertTrue(report['raw_source_changed'])
        self.assertFalse(report['source_changed'])
        consent=approve(self.plan,preview(self.plan)[0]['plan_sha256'],'fixture','test')
        bridge=Mock();bridge.pcb_capture.return_value=exported
        bridge.pcb_operation.return_value={'status':'observed','attempted':True,'created_id':'l','after':add_line(exported)}
        self.assertEqual(execute(self.plan,consent,lambda:bridge,experimental=True)['status'],'verified')

    def test_preflight_and_advice_never_write(self):
        report,_ = preview(self.plan)
        self.assertTrue(report["ready_for_approval"],report["findings"])
        self.plan.update(intent="advice",operations=[],recommendations=["fixture return path"])
        report,_ = preview(self.plan)
        self.assertFalse(report["ready_for_approval"])
        self.plan["operations"] = [line_op()]
        with self.assertRaisesRegex(ValueError,"不能包含CAD"):
            preview(self.plan)

    def test_rejects_baseline_tamper_unknown_scripts_and_empty_router_nets(self):
        malformed = deepcopy(line_op()); malformed["code"] = "arbitrary"
        with self.assertRaisesRegex(ValueError,"不接受脚本"):
            validate_operation(malformed)
        with self.assertRaisesRegex(ValueError,"空"):
            validate_operation({"id":"r","operation":"route_nets","parameters":{"nets":[],"layers":["TOP"]},"rationale":"test"})
        self.snapshot_path.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,"已变化"):
            preview(self.plan)

    def test_rejects_scope_expansion_and_critical_autorouting(self):
        self.plan["scope"]["nets"] = ["GND"]
        with self.assertRaisesRegex(ValueError,"范围外"):
            preview(self.plan)
        self.plan["scope"]["nets"] = ["N","GND"]
        self.plan["operations"] = [{"id":"r","operation":"route_nets","parameters":{"nets":["N"],"layers":["TOP"]},"rationale":"test"}]
        self.plan["constraints"]["net_constraints"]["N"]["kind"] = "high-speed"
        with self.assertRaisesRegex(ValueError,"明确路径"):
            preview(self.plan)

    def test_rejects_partial_auto_layout_and_move_connected_outside_scope(self):
        self.plan["operations"] = [{"id":"p","operation":"auto_place","parameters":{},"rationale":"fixture"}]
        with self.assertRaisesRegex(ValueError,"整板"):
            preview(self.plan)
        self.plan["operations"] = [{"id":"p","operation":"move_component","parameters":{"primitive_id":"c","x":150,"y":100,"rotation":0},"rationale":"fixture"}]
        self.plan["scope"]["nets"] = ["N"]
        with self.assertRaisesRegex(ValueError,"范围外"):
            preview(self.plan)

    def test_rejects_missing_evidence_unresolved_and_bools(self):
        self.plan["constraints"]["calibration"] = {}
        self.plan["unresolved"] = ["missing"]
        report,_ = preview(self.plan)
        self.assertFalse(report["ready_for_approval"])
        op=line_op();op["parameters"]["width"]=True
        with self.assertRaises(ValueError): validate_operation(op)

    def test_schematic_pad_mismatch_blocks_approval(self):
        p = read_json(self.schematic_path)
        raw = json.loads(p["raw_netlist"]); raw["components"]["sch-c"]["pinInfoMap"]["p1"]["net"]="OTHER"
        p["raw_netlist"]=json.dumps(raw); p["raw_sha256"]=hashlib.sha256(p["raw_netlist"].encode()).hexdigest()
        altered=write_new(self.root/'other-sch.json',p)
        from easyeda_assist.storage import digest
        self.plan["schematic"]={"path":str(altered),"sha256":digest(altered)}
        report,_=preview(self.plan)
        self.assertFalse(report["ready_for_approval"])

    def test_strict_readback_accepts_single_line_rejects_unrelated_edit_and_drc(self):
        verify_operation(self.before,add_line(self.before),line_op(),"l",self.plan["scope"])
        for after in [add_line(self.before,new_drc=True),add_line(self.before,net="GND")]:
            with self.assertRaises(ValueError): verify_operation(self.before,after,line_op(),"l",self.plan["scope"])
        after=add_line(self.before);after["source"]=after["source"].replace('"unit": "mm"','"unit": "mil"')
        after["source_sha256"]=hashlib.sha256(after["source"].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,"方案外"):
            verify_operation(self.before,after,line_op(),"l",self.plan["scope"])

    def test_approval_is_bound_single_use_and_experimental_is_explicit(self):
        report,_=preview(self.plan)
        consent=approve(self.plan,report["plan_sha256"],"fixture consent","test reference")
        bridge=Mock();bridge.pcb_capture.return_value=self.before
        bridge.pcb_operation.return_value={"status":"observed","attempted":True,"created_id":"l","after":add_line(self.before)}
        with self.assertRaisesRegex(ValueError,"experimental"):
            execute(self.plan,consent,lambda:bridge)
        result=execute(self.plan,consent,lambda:bridge,experimental=True)
        self.assertEqual(result["status"],"verified",result)
        with self.assertRaises(FileExistsError):execute(self.plan,consent,lambda:bridge,experimental=True)
        self.assertEqual(bridge.pcb_operation.call_count,1)

    def test_timeout_is_not_retried_and_retains_target_lock(self):
        consent=approve(self.plan,preview(self.plan)[0]["plan_sha256"],"fixture","test")
        bridge=Mock();bridge.pcb_capture.return_value=self.before;bridge.pcb_operation.side_effect=TimeoutError("late")
        result=execute(self.plan,consent,lambda:bridge,experimental=True)
        self.assertEqual(result["status"],"uncertain")
        self.assertTrue(Path(result["target_lock"]).is_dir())
        self.assertEqual(bridge.pcb_operation.call_count,1)
        self.assertTrue((Path(result["run_dir"])/'001-intent.json').is_file())

    def test_changed_consent_and_live_rules_cannot_execute(self):
        consent=approve(self.plan,preview(self.plan)[0]["plan_sha256"],"fixture","test")
        altered=deepcopy(self.plan);altered['request']='different scope request'
        bridge=Mock()
        with self.assertRaisesRegex(ValueError,'失效'):
            execute(altered,consent,lambda:bridge,experimental=True)
        changed=deepcopy(self.before);changed['outputs']['rules']['value']={'name':'changed'}
        bridge.pcb_capture.return_value=changed
        result=execute(self.plan,consent,lambda:bridge,experimental=True)
        self.assertEqual(result['status'],'uncertain')
        bridge.pcb_operation.assert_not_called()

    def test_drc_failure_retains_receipt_and_does_not_run_second_operation(self):
        self.plan['operations'].append({**line_op(),'id':'second'})
        consent=approve(self.plan,preview(self.plan)[0]['plan_sha256'],'fixture','test')
        bridge=Mock();bridge.pcb_capture.return_value=self.before
        bridge.pcb_operation.return_value={'status':'observed','after':add_line(self.before,new_drc=True),'created_id':'l'}
        result=execute(self.plan,consent,lambda:bridge,experimental=True)
        self.assertEqual(result['status'],'uncertain')
        self.assertEqual(bridge.pcb_operation.call_count,1)
        self.assertTrue((Path(result['run_dir'])/'001-reply.json').is_file())

    def test_power_and_highspeed_rules_require_physical_analysis(self):
        for kind, key, fields in [('power','power_analysis',['operating_conditions','fault_and_protection','loss_and_thermal']),
                                  ('high-speed','signal_analysis',['edge_and_topology','impedance_and_timing','reference_transitions'])]:
            rule=self.plan['constraints']['net_constraints']['N'];rule['kind']=kind
            report,_=preview(self.plan)
            self.assertFalse(report['ready_for_approval'])
            rule[key]={k:'fixture evidence only' for k in fields}
            report,_=preview(self.plan)
            self.assertTrue(report['ready_for_approval'],report['findings'])

    def test_manufacture_export_uses_fixed_names_and_checks_bytes(self):
        files={k:{'name':'../../remote-name','size':3,'base64':base64.b64encode(b'abc').decode()}
               for k in ['gerber','bom','pick_and_place','ipc_d356']}
        bridge=Mock();bridge.pcb_export.return_value={'identity':self.before['identity'],'source':self.before['source'],'files':files}
        result=export_files(bridge,'w')
        for item in result['files'].values():
            self.assertEqual(Path(item['path']).read_bytes(),b'abc')
            self.assertTrue(Path(item['path']).resolve().is_relative_to(self.root))
        files['bom']['size']=4
        with self.assertRaisesRegex(ValueError,'长度'):
            export_files(bridge,'w')

    def test_cli_review_and_inspect(self):
        plan=write_new(self.root/'plan.json',self.plan)
        for argv in [["review-pcb-plan",str(plan)],["inspect-pcb",str(self.snapshot_path),"--net","N"]]:
            out=io.StringIO()
            with redirect_stdout(out): self.assertEqual(main(argv),0)
            self.assertIsInstance(json.loads(out.getvalue()),dict)

    @unittest.skipUnless(shutil.which('node'), 'Node.js needed for actual fixed JavaScript execution')
    def test_real_fixed_capture_and_line_program_against_api_double(self):
        # Run the actual generated programs, including enums, source stability,
        # full collections, pin reads and post-write DRC. This is not live CAD.
        js = r'''
const fs=require('fs');const input=JSON.parse(fs.readFileSync(0,'utf8'));
const state=input.state;const counters={line:0};
global.EPCB_LayerId={TOP:1,BOTTOM:2,BOARD_OUTLINE:11,MULTI:12};
global.EPCB_PrimitiveViaType={VIA:0};global.EPCB_PrimitivePourFillMethod={SOLID:'solid'};
global.EPCB_AutoRoutingExistingPrimitiveMode={KEEP:'keep'};
if(input.noGlobals)for(const name of ['EPCB_LayerId','EPCB_PrimitiveViaType','EPCB_PrimitivePourFillMethod','EPCB_AutoRoutingExistingPrimitiveMode'])delete global[name];
function primitive(row){return new Proxy({}, {get:(_,key)=>{
 if(String(key).startsWith('getState_'))return ()=>row.properties[String(key).slice(9)];
 if(key==='rebuildCopperRegion')return async()=>{
  const r={id:'filled',kind:'Poured',properties:{PrimitiveId:'filled',PourPrimitiveId:row.id,PourFills:[]}};
  state.collections.Poured.push(r);state.source+='\n'+JSON.stringify({type:'POURED',id:'filled'})+'||'+JSON.stringify({targetId:row.id,path:[]})+'|';return primitive(r);
 };
 if(key==='toAsync')return ()=>{const p={...row.properties};const a={
  setState_X:v=>p.X=v,setState_Y:v=>p.Y=v,setState_Rotation:v=>p.Rotation=v,
  done:async()=>{row.properties=p;state.source=state.source.split('\n').map(l=>{
   const [e,b]=l.split('||');const env=JSON.parse(e);if(env.type!=='COMPONENT'||env.id!==row.id)return l;
   const v=JSON.parse(b.slice(0,-1));v.positionX=p.X;v.positionY=p.Y;v.angle=p.Rotation;
   return JSON.stringify(env)+'||'+JSON.stringify(v)+'|';}).join('\n');return primitive(row);}};return a;};
 return undefined;
}});}
const eda={dmt_Project:{getCurrentProjectInfo:async()=>({uuid:'p'})},
 dmt_SelectControl:{getCurrentDocumentInfo:async()=>({uuid:'b',tabId:'tab',documentType:3,parentProjectUuid:'p'})},
 sys_Environment:{getEditorCurrentVersion:()=> '4.1.100'},
 sys_FileManager:{getDocumentSource:async()=>state.source,getDocumentFootprintSources:async()=>[]},
 pcb_Primitive:{getPrimitivesBBox:async()=>({minX:0,minY:0,maxX:200,maxY:200})},
 pcb_Document:{getCanvasOrigin:async()=>state.outputs.origin.value},
 dmt_Board:{getCurrentBoardInfo:async()=>state.outputs.board.value},
 pcb_Layer:{getAllLayers:async()=>state.outputs.layers.value,getCurrentPhysicalStackingConfiguration:async()=>input.noStackup?undefined:state.outputs.stackup.value},
 pcb_Net:{getAllNets:async()=>state.outputs.nets.value,getNetlist:async()=>state.outputs.netlist.value},
 pcb_Drc:{check:async()=>state.outputs.drc.value,getCurrentRuleConfiguration:async()=>state.outputs.rules.value,
 getNetRules:async()=>[],getAllNetClasses:async()=>[],getAllDifferentialPairs:async()=>[],getAllEqualLengthNetGroups:async()=>[]}};
for(const k of Object.keys(state.collections))eda['pcb_Primitive'+k]={getAll:async()=>state.collections[k].map(primitive)};
eda.pcb_PrimitiveComponent.get=async id=>primitive(state.collections.Component.find(r=>r.id===id));
eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId=async id=>state.collections.Component.find(r=>r.id===id).pads.map(primitive);
eda.pcb_MathPolygon={createPolygon:p=>({getSource:()=>p})};
function add(kind,id,values,payload){const row={id,kind,properties:{PrimitiveId:id,...values}};
 state.collections[kind].push(row);state.source+='\n'+JSON.stringify({type:{Via:'VIA',Pour:'POUR',Polyline:'POLY'}[kind],id})+'||'+JSON.stringify(payload)+'|';return primitive(row);}
eda.pcb_PrimitiveVia.create=async(net,x,y,hole,diameter,viaType)=>add('Via','v',{Net:net,X:x,Y:y,HoleDiameter:hole,Diameter:diameter,ViaType:viaType},{netName:net,centerX:x,centerY:y,holeDiameter:hole,viaDiameter:diameter,viaType});
eda.pcb_PrimitivePour.create=async(net,layer,polygon,method,silos,name,priority,width)=>add('Pour','pour',
 {Net:net,Layer:layer,ComplexPolygon:polygon,PourFillMethod:method,PreserveSilos:silos,PourName:name,PourPriority:priority,LineWidth:width},
 {netName:net,layerId:layer,path:polygon.getSource(),name,order:priority,width});
eda.pcb_PrimitivePour.get=async id=>primitive(state.collections.Pour.find(r=>r.id===id));
eda.pcb_PrimitivePolyline.create=async(net,layer,polygon,width)=>add('Polyline','outline',
 {Net:net,Layer:layer,Polygon:polygon,LineWidth:width},{netName:net,layerId:layer,path:polygon.getSource(),width});
eda.pcb_PrimitiveLine.create=async(net,layer,x1,y1,x2,y2,width)=>{
 counters.line++;state.source+='\n'+JSON.stringify({type:'LINE',id:'l'})+'||'+JSON.stringify({netName:net,layerId:layer,startX:x1,startY:y1,endX:x2,endY:y2,width})+'|';
 const row={id:'l',kind:'Line',properties:{PrimitiveId:'l',Net:net,Layer:layer,StartX:x1,StartY:y1,EndX:x2,EndY:y2,LineWidth:width}};
 state.collections.Line.push(row);return primitive(row);
};
eda.pcb_Document.autoRouting=async p=>{if(!Array.isArray(p.RoutingNets)||!p.RoutingNets.length||p.existingPrimitiveMode!=='keep')throw Error('router scope expanded');
 await eda.pcb_PrimitiveLine.create(p.RoutingNets[0],p.layers[0],100,100,200,100,10);return {success:true,totalNetsCount:1,successNetsCount:1,failedNets:[]};};
eda.pcb_Document.autoLayout=async()=>{const a=primitive(state.collections.Component[0]).toAsync();a.setState_X(150);await a.done();return {success:true,totalComponentsCount:1,successComponentsCount:1,failedComponents:[]};};
(async()=>{const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
 const captured=await new AsyncFunction('eda',input.capture)(eda);
 const result=input.operation?await new AsyncFunction('eda',input.operation)(eda):null;
 process.stdout.write(JSON.stringify({captured,result,counters}));
})().catch(e=>{process.stderr.write(String(e));process.exit(1)});
'''
        capture=capture_script(True)
        payload={"state":self.before,"capture":capture}
        run=subprocess.run(['node','-e',js],input=json.dumps(payload),text=True,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        data=json.loads(run.stdout)
        self.assertTrue(data['captured']['complete'])
        captured=make_snapshot(data['captured'],'w')
        # Gateway 4.1.100 has no SDK enum globals and no physical-stackup API.
        gateway={'state':self.before,'capture':capture,'noGlobals':True,'noStackup':True}
        run=subprocess.run(['node','-e',js],input=json.dumps(gateway),text=True,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        observed=json.loads(run.stdout)['captured']
        self.assertTrue(observed['complete'])
        self.assertFalse(observed['stackup_available'])
        self.assertEqual(observed['enum_source'],'documented-gateway-projection')
        self.assertEqual(observed['layer_ids']['TOP'],1)
        gateway_snapshot=make_snapshot(observed,'w')
        gateway['operation']=operation_script(gateway_snapshot,line_op())
        run=subprocess.run(['node','-e',js],input=json.dumps(gateway),text=True,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout)['result']
        self.assertEqual(result['status'],'observed',result)
        verify_operation(gateway_snapshot,make_snapshot(result['after'],'w'),line_op(),'l',self.plan['scope'])
        payload['operation']=operation_script(captured,line_op())
        run=subprocess.run(['node','-e',js],input=json.dumps(payload),text=True,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        data=json.loads(run.stdout)
        self.assertEqual(data['result']['status'],'observed',data['result'])
        self.assertEqual(data['counters']['line'],1)
        after=make_snapshot(data['result']['after'],'w')
        verify_operation(captured,after,line_op(),'l',self.plan['scope'])
        polygon=[10,10,'L',300,10,300,300,10,300,10,10]
        operations=[
            {'id':'m','operation':'move_component','parameters':{'primitive_id':'c','x':150,'y':100,'rotation':90},'rationale':'fixture'},
            {'id':'v','operation':'add_via','parameters':{'net':'N','x':200,'y':100,'hole':10,'diameter':20},'rationale':'fixture'},
            {'id':'p','operation':'add_pour','parameters':{'net':'GND','layer':'BOTTOM','polygon':polygon,'name':'fixture','priority':1,'width':10},'rationale':'fixture'},
            {'id':'o','operation':'add_outline','parameters':{'polygon':polygon,'width':10},'rationale':'fixture'},
            {'id':'r','operation':'route_nets','parameters':{'nets':['N'],'layers':['TOP']},'rationale':'fixture'},
            {'id':'a','operation':'auto_place','parameters':{},'rationale':'fixture'},
        ]
        for op in operations:
            payload['operation']=operation_script(captured,op)
            run=subprocess.run(['node','-e',js],input=json.dumps(payload),text=True,capture_output=True,encoding='utf-8')
            self.assertEqual(run.returncode,0,run.stderr)
            row=json.loads(run.stdout)['result']
            self.assertEqual(row['status'],'observed',(op,row))
            actual=make_snapshot(row['after'],'w')
            verify_operation(captured,actual,op,row['created_id'],self.plan['scope'],self.plan['constraints'])
            if op['operation']=='add_pour':
                rebuild={'id':'rebuild','operation':'rebuild_pour','parameters':{'primitive_id':'pour'},'rationale':'fixture'}
                payload2={'state':actual,'capture':capture,'operation':operation_script(actual,rebuild)}
                res=subprocess.run(['node','-e',js],input=json.dumps(payload2),text=True,capture_output=True,encoding='utf-8')
                self.assertEqual(res.returncode,0,res.stderr)
                rr=json.loads(res.stdout)['result'];self.assertEqual(rr['status'],'observed',rr)
                verify_operation(actual,make_snapshot(rr['after'],'w'),rebuild,rr['created_id'],self.plan['scope'])
        payload['operation']=operation_script(captured,line_op())
        payload['state']['source'] += '\n'+source([({'type':'PROP','id':'unrelated'},{'color':'red'})])
        run=subprocess.run(['node','-e',js],input=json.dumps(payload),text=True,capture_output=True,encoding='utf-8')
        data=json.loads(run.stdout)
        self.assertEqual(data['result']['status'],'blocked')
        self.assertEqual(data['counters']['line'],0)


class PCBRefactorGuards(unittest.TestCase):
    def test_native_batch_requires_explicit_distinct_bounded_nets(self):
        before=fixture()
        for bad in ([],['N','N'],[''],['N',None]):
            with self.subTest(nets=bad),self.assertRaises(ValueError):
                native_route_script(before,bad,'job',together=True)
        nets=['N'+str(i) for i in range(21)]
        with self.assertRaises(ValueError):native_route_script(before,nets,'job')
        native_route_script(before,nets,'job',together=True)
        with self.assertRaises(ValueError):native_route_script(before,['N']*81,'job',together=True)
        with self.assertRaises(ValueError):native_route_script(before,['N'],'job',together='yes')

    def test_pour_removal_requires_known_parent_and_enumerated_fill(self):
        before=fixture()
        before['collections']['Pour']=[{'id':'plane','kind':'Pour','properties':{'PrimitiveId':'plane'}}]
        before['collections']['Poured']=[{'id':'plane','kind':'Poured','properties':{'PrimitiveId':'plane','PourPrimitiveId':'plane'}}]
        with self.assertRaisesRegex(ValueError,'derived fills'):
            repair_delete_script(before,{'Pour':['plane']})
        with self.assertRaises(ValueError):repair_delete_script(before,{'Pour':['unknown']})
        with self.assertRaises(ValueError):repair_delete_script(before,{'Component':['c']})
        repair_delete_script(before,{'Poured':['plane'],'Pour':['plane']})


if __name__ == '__main__':
    unittest.main()
