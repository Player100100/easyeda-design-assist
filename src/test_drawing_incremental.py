"""Occupied-page edits must preserve the entire original circuit and warnings."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess
from unittest.mock import Mock, patch

from easyeda_assist.drawing_api import ROTATION_CONVENTION
from easyeda_assist.drawing_execution import preflight, approve_plan, execute_plan
from easyeda_assist.drawing_incremental import (validate_incremental, audit_incremental,
    validate_baseline, verify_incremental_reply, claim_lock, release_lock, verify_lock, ACTIVE_CLAIM,
    settle_created_component)
from easyeda_assist.drawing_incremental_cleanup import cleanup_failed_creation, restore_experiment_baseline
from easyeda_assist.source_trial import lock_inventory
from easyeda_assist.storage import write_new, digest, new_id, read_json
from easyeda_assist.drawing_source import records
from easyeda_assist.design_audit import make_audit
from easyeda_assist.operations import INCREMENTAL
from test_source_validation import evidence
from test_drawing_circuit import line, library, CIRCUIT_HARNESS
from test_drawing_execution import snapshot, library_record, reply
from test_project import design


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR":folder.name})
        env.start(); self.addCleanup(env.stop)
        self.warning = {"type":"warn","rule":"OLD","primitives":[{"primitiveId":"c","sheet":"d"}]}
        query, audit = evidence(x=700,errors=[self.warning])
        query['data']['outputs']['pin_geometry'] = {"status":"returned","value":[{
            "pinNumber":"1","x":710,"y":20,"bodyBBox":{"minX":690,"minY":10,"maxX":705,"maxY":30}}]}
        inventory = {**snapshot(),"source":audit['source'],"components":[{
            "primitive_id":"c","component_type":"part","x":700,"y":20}],"truncated":False,"total":1}
        self.bundle = {"schema_version":1,"kind":"incremental-baseline","audit":audit,
                       "inventory":inventory,"geometry":[query]}
        self.before = {**snapshot(),"source":audit['source']}
        basepath = write_new(self.root/'baseline.json',self.bundle)
        sourcepath = write_new(self.root/'source.json',self.before)
        archive = write_new(self.root/'library.json',library())
        self.p = {"library_uuid":"lib","device_uuid":"dev","sub_part_name":"","x":100,"y":200,
                  "unit":"10mil","requested_api_rotation":0,"expected_canvas_rotation":0,
                  "rotation_convention":ROTATION_CONVENTION,"mirror":False,"add_into_bom":True,"add_into_pcb":True,
                  "expected_pins":[{"number":"1","x":110,"y":195}],
                  "library_evidence":{"path":str(archive),"sha256":digest(archive)}}
        def op(oid,kind,p):
            return {"id":oid,"operation":kind,"target":"experiment","before":"absent","after":"added",
                    "expected_result":"preserve original circuit","parameters":p}
        self.project = design()
        self.project['drawing_policy'] = {"mode":"explicit_batch","trigger":"explicit_user_request",
            "allowed_operations":["place_component","add_wire"],"require_batch_approval":True,"require_snapshot_binding":True}
        self.plan = {"schema_version":2,"interaction":"collaborative","origin":{"task":None,"decisions":[]},
            "project_id":self.project['id'],"created_at":"fixture","request":"add isolated circuit on occupied page",
            "execution_contract":INCREMENTAL,"incremental_baseline":{"path":str(basepath),"sha256":digest(basepath)},
            "edit_region":[50,150,200,250],"target":{"window_id":"w","project_uuid":"p","document_uuid":"d",
                "domain":"schematic","snapshot_path":str(sourcepath),"snapshot_sha256":digest(sourcepath)},
            "operations":[op('device','place_component',self.p),op('wire','add_wire',{"unit":"10mil","net":"NEW","line":[110,195,130,195]})],
            "connectivity":[{"component":"device","pin":"1","net":"NEW"}],"evidence":["fixture"],"unresolved":[],
            "readback_plan":"compare all source records, full old netlist, exact new pins and detailed DRC","failure_plan":"stop"}
        self.finalsource = self.before['source'] + line('COMPONENT','new-id',{'x':100,'y':-200,'rotation':0,'isMirror':False})
        for i,(k,v) in enumerate({'Unique ID':'new-uid','Designator':'R1','Device':'local-dev'}.items()):
            self.finalsource += line('ATTR','newattr'+str(i),{'parentId':'new-id','key':k,'value':v})
        raw = json.loads(audit['raw_netlist'])
        raw['components']['new'] = {'props':{'Unique ID':'new-uid','Designator':'R1','Device':'local-dev'},
                                    'pinInfoMap':{'p':{'number':'1','net':'NEW'}}}
        self.audit = {'identity':self.before['identity'],'window_id':'w','source':self.finalsource,
                      'editor_version':'4.1.100','raw_netlist':json.dumps(raw),'drc':[self.warning],'drc_passed':False}

    def check(self, value=None):
        audit = value or self.audit
        return audit_incremental(audit,{**self.before,'source':audit['source']},self.plan,self.bundle,{'device':'new-id'})

    def test_nonempty_page_and_preexisting_warning_pass_when_preserved(self):
        report,_ = preflight(self.project,self.plan)
        self.assertEqual(report['preview']['baseline_component_count'],1)
        result = self.check()
        self.assertTrue(result['connectivity_matches'])
        self.assertTrue(result['drc_unchanged'])
        self.assertFalse(result['engineering_verified'])
        self.assertEqual(result['baseline_drc_count'],1)

    def test_original_source_edit_or_removal_is_detected(self):
        audit = {**self.audit,'source':self.audit['source'].replace('"x": 700','"x": 701')}
        result = self.check(audit)
        self.assertFalse(result['baseline_source_preserved'])
        self.assertFalse(result['connectivity_matches'])

    def test_old_connectivity_and_properties_are_both_protected(self):
        for kind in ('props','pins','removed'):
            raw=json.loads(self.audit['raw_netlist'])
            old=raw['components']['netlist-key']
            if kind=='props': old['props']['Name']='changed'
            elif kind=='pins': old['pinInfoMap']['pin']['net']='NEW'
            else: del raw['components']['netlist-key']
            result=self.check({**self.audit,'raw_netlist':json.dumps(raw)})
            self.assertFalse(result['baseline_components_preserved'])

    def test_drc_added_removed_changed_or_unmatched_never_pass(self):
        for errors in ([],[self.warning,{'type':'warn','rule':'NEW','primitives':[{'primitiveId':'new-id','sheet':'d'}]}],
                       [{**self.warning,'type':'error'}],[{'type':'warn'}]):
            self.assertFalse(self.check({**self.audit,'drc':errors})['drc_passed'])

    def test_wrong_new_net_or_extra_component_fails(self):
        raw=json.loads(self.audit['raw_netlist']); raw['components']['new']['pinInfoMap']['p']['net']='N'
        self.assertFalse(self.check({**self.audit,'raw_netlist':json.dumps(raw)})['connectivity_matches'])
        raw=json.loads(self.audit['raw_netlist']); raw['components']['extra']={'props':{'Unique ID':'extra'},'pinInfoMap':{}}
        self.assertFalse(self.check({**self.audit,'raw_netlist':json.dumps(raw)})['connectivity_matches'])

    def test_geometry_identity_tamper_and_baseline_bytes_are_rejected(self):
        broken=deepcopy(self.bundle); broken['geometry'][0]['data']['window_id']='other'
        with self.assertRaises(ValueError): validate_baseline(broken)
        path=Path(self.plan['incremental_baseline']['path']); path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'摘要'): preflight(self.project,self.plan)

    def test_region_or_net_cannot_touch_original_circuit(self):
        plan=deepcopy(self.plan); plan['edit_region']=[680,0,760,250]
        with self.assertRaisesRegex(ValueError,'已有'): validate_incremental(plan,self.before)
        plan=deepcopy(self.plan); plan['operations'][1]['parameters']['net']='N'; plan['connectivity'][0]['net']='N'
        with self.assertRaisesRegex(ValueError,'网络名称'): validate_incremental(plan,self.before)

    def test_historical_lock_review_preserves_bytes_and_does_not_release_others(self):
        lock=self.root/'locks'; run=self.root/'run'
        old=write_new(lock/'owner.json',{'status':'uncertain'}); original=old.read_bytes()
        with self.assertRaises(ValueError): claim_lock(lock,run,{},None)
        claim,prior=claim_lock(lock,run,{},lock_inventory(lock)[1])
        release_lock(lock,claim,prior)
        self.assertEqual(old.read_bytes(),original)
        self.assertFalse((lock/ACTIVE_CLAIM).exists())

    def test_concurrent_lock_change_prevents_next_write(self):
        lock=self.root/'locks'; claim,prior=claim_lock(lock,self.root/'run',{})
        write_new(lock/'other.json',{'concurrent':True})
        with self.assertRaises(ValueError): verify_lock(lock,claim,prior)
        self.assertTrue((lock/ACTIVE_CLAIM).exists())

    def test_wrong_window_or_merge_into_old_wire_is_rejected(self):
        for r in ({'window_id':'other'},{'window_id':'w','primitive_id':'c'}):
            with self.assertRaises(ValueError): verify_incremental_reply(r,self.before,self.plan['operations'][1])

    def test_cleanup_only_accepts_known_first_step_isolated_probe(self):
        run=self.root/'failed'; lock=self.root/'locks'
        claim,prior=claim_lock(lock,run,{})
        write_new(run/'result.json',{'status':'uncertain','completed':0,'target_lock':str(lock)})
        write_new(run/'plan.json',self.plan)
        write_new(run/'input-snapshot.json',self.before)
        write_new(run/'001-reply.json',{'primitive_id':'new-id'})
        write_new(run/'incremental-baseline.json',self.bundle)
        # The known probe has no wired pins and is the only added object.
        raw=json.loads(self.audit['raw_netlist']); raw['components']['new']['pinInfoMap']['p']['net']=''
        from easyeda_assist.design_audit import make_audit
        observed=make_audit({**self.before,'source':self.finalsource},{**self.audit,'raw_netlist':json.dumps(raw)},'0'*64)
        b=Mock(); b.health.return_value={'pendingRequests':0}
        b.drawing_snapshot.side_effect=[{**self.before,'source':self.finalsource},self.before]
        b.design_query.side_effect=[observed,self.bundle['audit']]
        b._execute_fixed.return_value=({'status':'removed','attempted':True},'w')
        result=cleanup_failed_creation(run,b,'fixture cleanup delegation','test')
        self.assertEqual(result['status'],'original-baseline-restored')
        self.assertFalse(lock.exists())
        self.assertEqual(b._execute_fixed.call_count,1)

    def test_cleanup_blocks_connected_or_unrelated_original_object(self):
        run=self.root/'failed'; lock=self.root/'locks'
        claim_lock(lock,run,{})
        write_new(run/'result.json',{'status':'uncertain','completed':0,'target_lock':str(lock)})
        write_new(run/'plan.json',self.plan); write_new(run/'input-snapshot.json',self.before)
        write_new(run/'001-reply.json',{'primitive_id':'c'})
        b=Mock(); b.health.return_value={'pendingRequests':0}; b.drawing_snapshot.return_value=self.before
        with self.assertRaises(ValueError): cleanup_failed_creation(run,b,'fixture','test')
        b._execute_fixed.assert_not_called()

    def failed_module_fixture(self):
        from easyeda_assist.design_audit import make_audit
        run=self.root/new_id('failed-module'); lock=self.root/new_id('locks')
        claim_lock(lock,run,{})
        finalsource=self.finalsource+line('WIRE','wire-id',{'dots':[[110,-195,130,-195]]})
        steps=[{'id':op['id'],'status':'verified','primitive_id':pid}
               for op,pid in zip(self.plan['operations'],('new-id','wire-id'))]
        write_new(run/'result.json',{'status':'verification_failed','completed':2,'steps':steps,'target_lock':str(lock)})
        write_new(run/'plan.json',self.plan);write_new(run/'input-snapshot.json',self.before)
        write_new(run/'incremental-baseline.json',self.bundle)
        write_new(run/'002-snapshot.json',{**self.before,'source':finalsource})
        raw={**self.audit,'source':finalsource}
        write_new(run/'final-audit-raw.json',raw)
        live=make_audit({**self.before,'source':finalsource},raw,'0'*64)
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_snapshot.side_effect=[{**self.before,'source':finalsource},self.before]
        b.design_query.side_effect=[live,self.bundle['audit']]
        b._execute_fixed.side_effect=[({'status':'removed','identity':self.before['identity'],'source':source},'w')
                                     for source in (self.finalsource,self.before['source'])]
        return run,lock,b


    def test_archived_baseline_restore_uses_one_write_and_three_complete_observations(self):
        run,lock,b=self.failed_module_fixture()
        b._execute_fixed.side_effect=None
        b._execute_fixed.return_value=({'status':'restoration_pending','attempted':True},'w')
        final=json.loads((run/'002-snapshot.json').read_text())
        b.drawing_snapshot.side_effect=[final]+[self.before]*3
        live=make_audit(
            final,{**self.audit,'source':final['source']},'0'*64)
        b.design_query.side_effect=[live]+[self.bundle['audit']]*3
        with patch('time.sleep'):
            result=restore_experiment_baseline(run,b,'delegated experiments','explicit exact-baseline cleanup')
        self.assertEqual(result['status'],'original-baseline-restored')
        self.assertEqual(b._execute_fixed.call_count,1)
        self.assertFalse(lock.exists())

    def test_baseline_restore_never_replaces_changed_original_or_unknown_addition(self):
        for mutated in (self.finalsource.replace('"x": 700','"x": 701'),
                        self.finalsource+line('WIRE','unrelated',{})):
            with self.subTest(mutated=mutated[-50:]):
                # Unique fixtures live in separate subdirectories.
                run,lock,b=self.failed_module_fixture()
                b.drawing_snapshot.side_effect=[{**self.before,'source':mutated}]
                with self.assertRaisesRegex(ValueError,'原电路或实验记录集合'):
                    restore_experiment_baseline(run,b,'delegation','test')
                b._execute_fixed.assert_not_called()

    def test_baseline_restore_rejects_exported_original_property_drift(self):
        run,lock,b=self.failed_module_fixture()
        final=read_json(run/'002-snapshot.json')
        raw={**self.audit,'source':final['source']};netlist=json.loads(raw['raw_netlist'])
        key=next(iter(json.loads(self.bundle['audit']['raw_netlist'])['components']))
        netlist['components'][key]['props']['Name']='unrelated edit'
        raw['raw_netlist']=json.dumps(netlist)
        b.design_query.side_effect=[make_audit(final,raw,'0'*64)]
        with self.assertRaisesRegex(ValueError,'原电路网表变化'):
            restore_experiment_baseline(run,b,'delegation','test')
        b._execute_fixed.assert_not_called();self.assertTrue(lock.exists())

    def test_baseline_restore_keeps_claim_when_write_is_unconfirmed(self):
        run,lock,b=self.failed_module_fixture()
        b._execute_fixed.side_effect=None
        b._execute_fixed.return_value=({'status':'uncertain','attempted':True},'w')
        with self.assertRaisesRegex(ValueError,'结果未知'):
            restore_experiment_baseline(run,b,'delegation','test')
        self.assertEqual(b._execute_fixed.call_count,1)
        self.assertTrue(lock.exists())

    @unittest.skipUnless(shutil.which('node'),'Node required for fixed runtime')
    def test_new_version_create_returns_receipt_before_lazy_identity_is_ready(self):
        from easyeda_assist.drawing_circuit import operation_script
        harness=CIRCUIT_HARNESS.replace("'3.2.149.88089769'","'4.1.100'")
        harness=harness.replace('getState_PrimitiveId:()=>o.invalid_id',
            "getState_ComponentType:()=> 'part',getState_Net:()=>null,getState_Designator:()=> 'R1',getState_UniqueId:()=>o.no_uid?null:'uid',getState_PrimitiveId:()=>o.invalid_id")
        script=operation_script(snapshot(),self.plan['operations'][0],editor_version='4.1.100')
        for options,status in (({},'created_pending_identity'),({'wrong_version':True},'blocked'),({'no_uid':True},'created_pending_identity')):
            process=subprocess.run([shutil.which('node'),'-e',harness],input=json.dumps({'script':script,'source':'fixture-source','options':options}),text=True,encoding='utf-8',capture_output=True,check=True,timeout=10)
            r=json.loads(process.stdout)
            self.assertEqual(r['result']['status'],status,r)
            self.assertEqual(sum(c['method']=='create' for c in r['calls']),0 if status=='blocked' else 1)
            self.assertEqual(sum(c['method']=='netlist' for c in r['calls']),0)

    def test_lazy_identity_settles_across_separate_reads_without_recreating(self):
        from easyeda_assist.bridge import BridgeError
        b=Mock()
        b.design_query.side_effect=[BridgeError('source changed during export')]+[self.bundle['audit']]*3
        b.drawing_snapshot.return_value=self.before
        o={'primitive_id':'new-id','component':{'libraryUuid':'lib','uuid':'local-dev'},'unique_id':'new-uid','designator':'R1'}
        data={**self.before,'observed':o,'pins':[]}
        b._read_fixed.return_value=(data,'w')
        creation={'attempted':True,'status':'created_pending_identity','primitive_id':'new-id','window_id':'w'}
        with patch('time.sleep'):
            result=settle_created_component(b,self.before,self.plan['operations'][0],creation)
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['creation_reply'],creation)
        self.assertEqual(b._read_fixed.call_count,3)
        b._execute_fixed.assert_not_called()

    def test_missing_lazy_identity_remains_uncertain_and_never_recreates(self):
        b=Mock();b.design_query.return_value=self.bundle['audit'];b.drawing_snapshot.return_value=self.before
        b._read_fixed.return_value=({**self.before,'observed':{'designator':'R?','unique_id':''},'pins':[]},'w')
        creation={'attempted':True,'status':'created_pending_identity','primitive_id':'new-id','window_id':'w'}
        with patch('time.sleep'):
            result=settle_created_component(b,self.before,self.plan['operations'][0],creation)
        self.assertEqual(result['status'],'uncertain');self.assertEqual(b._read_fixed.call_count,8)
        b._execute_fixed.assert_not_called()

    def test_bridge_confirms_pending_identity_and_preserves_receipt_on_read_failure(self):
        from easyeda_assist.bridge import BridgeClient, BridgeError
        creation={'attempted':True,'status':'created_pending_identity','primitive_id':'new-id'}
        confirmed={**creation,'status':'verified','source':self.finalsource,'creation_reply':creation}
        for observation in (confirmed, {**creation,'status':'uncertain'}, BridgeError('read failed')):
            with self.subTest(observation=type(observation).__name__):
                b=BridgeClient(49620)
                with patch('easyeda_assist.drawing_circuit.library_evidence',return_value=(None,'expected')), \
                     patch('easyeda_assist.drawing_circuit.inspect_library',return_value='expected'), \
                     patch.object(b,'drawing_device_archive'), \
                     patch.object(b,'_execute_fixed',return_value=(deepcopy(creation),'w')) as create, \
                     patch.object(b,'drawing_project_info',return_value={'source':self.finalsource}) as project, \
                     patch('easyeda_assist.drawing_incremental.settle_created_component') as settle:
                    if isinstance(observation,Exception):
                        settle.side_effect=observation
                    else:
                        settle.return_value=deepcopy(observation)
                    result=b._circuit_operation(self.before,self.plan['operations'][0],editor_version='4.1.100')
                    self.assertEqual(create.call_count,1)
                    self.assertEqual(settle.call_count,1)
                    self.assertEqual(settle.call_args.args[-1],{**creation,'window_id':'w'})
                    self.assertEqual(result['primitive_id'],'new-id')
                    self.assertEqual(result['attempted'],True)
                    self.assertEqual(project.call_count,1 if result['status']=='verified' else 0)
                    if isinstance(observation,Exception):
                        self.assertEqual(result['status'],'uncertain')
                        self.assertEqual(result['creation_reply'],{**creation,'window_id':'w'})

    def test_full_executor_uses_new_version_and_releases_only_own_claim(self):
        deviceafter={**self.before,'source':self.finalsource}
        r=reply(self.before,{**self.p,'expected_project_device_uuid':'local-dev'})
        r.update(window_id='w',source=self.finalsource,project_evidence=library_record(deviceafter))
        r['project_evidence']['project_info']['editorVersion']='4.1.100'
        r['observed'].update(component_type='part',designator='R1',unique_id='new-uid')
        finalsource=self.finalsource+line('WIRE','wire-id',{'dots':[[110,-195,130,-195]]})+line('ATTR','netattr',{'parentId':'wire-id','key':'NET','value':'NEW'})
        wire={'status':'verified','attempted':True,'primitive_id':'wire-id','window_id':'w','identity':self.before['identity'],
              'source':finalsource,'observed':{'primitive_id':'wire-id','net':'NEW','line':[110,195,130,195]}}
        b=Mock(); b.health.return_value={'pendingRequests':0}
        b.drawing_components.return_value=self.bundle['inventory']; b.design_query.return_value=self.bundle['audit']
        b._circuit_operation.side_effect=[r,wire]; b.drawing_circuit_audit.return_value={**self.audit,'source':finalsource}
        checked,_=preflight(self.project,self.plan)
        consent=approve_plan(self.project,self.plan,checked['binding']['plan_sha256'],'fixture scope approval','test')
        result=execute_plan(self.project,self.plan,consent,lambda:b)
        self.assertEqual(result['status'],'verified',result)
        self.assertEqual(result['completed'],2)
        self.assertFalse(Path(result['target_lock']).exists())
        self.assertEqual(b._circuit_operation.call_args.kwargs,{'editor_version':'4.1.100'})
        with self.assertRaisesRegex(ValueError,'禁止重放'): execute_plan(self.project,self.plan,consent,lambda:b)


if __name__=='__main__': unittest.main()
