"""Physical branches have stricter old-wire identities than legacy merges."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_drawing_incremental
from test_drawing_circuit import line
from test_drawing_execution import library_record, reply
from easyeda_assist.drawing_branch import validate_branch, verify_branch_reply, audit_branch
from easyeda_assist.drawing_execution import preflight, approve_plan, execute_plan
from easyeda_assist.drawing_source import records
from easyeda_assist.storage import write_new, digest
from easyeda_assist.operations import BRANCH, SOURCE, ADAPTERS, operation_names


class BranchTests(unittest.TestCase):
    def setUp(self):
        # Reuse fixture construction without inheriting/rerunning the v4 suite.
        fixture = test_drawing_incremental.IncrementalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for name in ('root', 'warning', 'bundle', 'before', 'p', 'project', 'plan', 'finalsource', 'audit'):
            setattr(self, name, deepcopy(getattr(fixture, name)))
        old_wire = line('WIRE', 'old-wire', {}) + line('LINE', 'old-line', {
            'lineGroup': 'old-wire', 'startX': 80, 'startY': -100, 'endX': 140, 'endY': -100})
        old_wire += line('ATTR', 'old-net', {'parentId': 'old-wire', 'key': 'NET', 'value': 'N', 'zIndex': 3})
        self.before['source'] += old_wire
        self.bundle['audit']['source'] += old_wire
        from easyeda_assist.design_audit import source_digest
        self.bundle['audit']['source_sha256'] = source_digest(self.before['source'])
        self.bundle['inventory']['source'] += old_wire
        for query in self.bundle['geometry']:
            query['data']['source'] += old_wire
        self.plan.update(execution_contract=BRANCH,
            branch_anchors=[{'operation':'wire', 'primitive_id':'old-wire', 'net':'N', 'x':110, 'y':100}])
        self.plan['operations'][1]['parameters'].update(net='N', line=[110,100,110,195])
        self.plan['connectivity'][0]['net'] = 'N'
        bpath=write_new(self.root/'branch-baseline.json', self.bundle)
        spath=write_new(self.root/'branch-source.json', self.before)
        self.plan['incremental_baseline']={'path':str(bpath), 'sha256':digest(bpath)}
        self.plan['target'].update(snapshot_path=str(spath), snapshot_sha256=digest(spath))
        self.device_source=self.finalsource+old_wire
        self.finalsource=self.device_source+line('LINE','branch-line',{
            'lineGroup':'old-wire', 'startX':110, 'startY':-100, 'endX':110, 'endY':-195})
        raw=json.loads(self.audit['raw_netlist'])
        raw['components']['new']['pinInfoMap']['p']['net']='N'
        self.audit.update(source=self.finalsource, raw_netlist=json.dumps(raw))
        self.wire_reply={'status':'verified','attempted':True,'primitive_id':'old-wire','window_id':'w',
            'identity':self.before['identity'],'source':self.finalsource,
            'observed':{'primitive_id':'old-wire','net':'N','line':[[80,100,140,100],[110,100,110,195]]}}
        self.mapping={'device':'new-id', 'wire':'old-wire'}

    def audit_result(self, audit=None, mapping=None):
        audit=audit or self.audit
        return audit_branch(audit, {**self.before, 'source':audit['source']}, self.plan,
                            self.bundle, mapping or self.mapping)

    def test_nonempty_physical_branch_preflight_and_audit(self):
        checked,_=preflight(self.project,self.plan)
        self.assertEqual(checked['binding']['adapter'],BRANCH)
        self.assertEqual(checked['preview']['branch_anchors'],self.plan['branch_anchors'])
        result=self.audit_result()
        self.assertTrue(result['baseline_source_within_contract'])
        self.assertTrue(result['baseline_components_preserved'])
        self.assertTrue(result['connectivity_matches'])
        self.assertTrue(result['drc_unchanged'])
        self.assertFalse(result['engineering_verified'])
        self.assertFalse(result['drc_global_freshness_established'])

    def test_layout_review_and_route_metrics_do_not_change_the_bound_consent_preview(self):
        checked, _ = preflight(self.project, self.plan, for_execution=False)
        approved, _ = preflight(self.project, self.plan)
        self.assertEqual(checked['binding'], approved['binding'])
        self.assertEqual(checked['preview'], approved['preview'])
        self.assertNotIn('layout_review', checked['preview'])
        self.assertNotIn('layout_review', approved)
        self.assertTrue(checked['layout_review']['before'])
        self.assertTrue(checked['layout_review']['after'])
        self.assertEqual(checked['route_metrics']['total_length'], 95)

    def test_anchor_must_bind_exact_id_net_point_and_wire_operation(self):
        for field,value in [('primitive_id','missing'),('net','WRONG'),('x',109),('operation','other')]:
            plan=deepcopy(self.plan); plan['branch_anchors'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): validate_branch(plan,self.before)

    def test_path_cannot_enter_title_block_even_when_part_region_is_clear(self):
        from test_drawing_frame import add_frame
        add_frame(self.bundle,self.before)
        bp=write_new(self.root/'frame-branch-baseline.json',self.bundle)
        self.plan['incremental_baseline']={'path':str(bp),'sha256':digest(bp)}
        validate_branch(self.plan,self.before)
        self.plan['operations'][1]['parameters']['line']=[110,100,110,180,510,180,510,195,110,195]
        with self.assertRaisesRegex(ValueError,'标题栏'):validate_branch(self.plan,self.before)

    def test_anchor_must_be_endpoint_and_other_end_new_pin(self):
        for path in ([110,90,110,195],[110,90,110,100,120,100,120,195,110,195],
                     [110,100,110,190,120,190,120,195,110,195]):
            plan=deepcopy(self.plan); plan['operations'][1]['parameters']['line']=path
            if path[-2:]==[110,195] and path[:2]==[110,100]:
                # This route is valid: each endpoint still binds correctly.
                validate_branch(plan,self.before)
            else:
                with self.assertRaises(ValueError): validate_branch(plan,self.before)
        plan=deepcopy(self.plan); plan['operations'][1]['parameters']['line']=[110,100,110,194]
        with self.assertRaises(ValueError): validate_branch(plan,self.before)

    def test_no_overlap_with_old_wire_and_no_second_contact(self):
        for path in ([110,100,120,100,120,195,110,195],
                     [110,100,110,120,140,120,140,100,150,100,150,195,110,195]):
            plan=deepcopy(self.plan); plan['operations'][1]['parameters']['line']=path
            with self.assertRaises(ValueError): validate_branch(plan,self.before)

    def test_no_crossing_an_undeclared_old_line_even_same_net(self):
        extra=line('WIRE','other-wire',{})+line('LINE','other-line',{
            'lineGroup':'other-wire','startX':90,'startY':-130,'endX':130,'endY':-130})
        extra+=line('ATTR','other-net',{'parentId':'other-wire','key':'NET','value':'N'})
        before=deepcopy(self.before);before['source']+=extra
        bundle=deepcopy(self.bundle)
        from easyeda_assist.design_audit import source_digest
        bundle['audit']['source']+=extra;bundle['audit']['source_sha256']=source_digest(before['source'])
        bundle['inventory']['source']+=extra
        for q in bundle['geometry']:q['data']['source']+=extra
        path=write_new(self.root/'crossing.json',bundle)
        plan=deepcopy(self.plan);plan['incremental_baseline']={'path':str(path),'sha256':digest(path)}
        with self.assertRaisesRegex(ValueError,'未声明'):validate_branch(plan,before)

    def test_requires_component_region_and_full_old_baseline(self):
        plan=deepcopy(self.plan);plan['edit_region']=[0,0,80,80]
        with self.assertRaises(ValueError):validate_branch(plan,self.before)
        plan=deepcopy(self.plan);plan['base_wires']=[]
        with self.assertRaises(ValueError):validate_branch(plan,self.before)
        plan=deepcopy(self.plan);plan['operations'][0]['parameters']['add_into_pcb']=False
        with self.assertRaises(ValueError):validate_branch(plan,self.before)

    def test_step_checks_entire_getter_and_source_wire_coverage(self):
        before={**self.before,'source':self.device_source}
        result=verify_branch_reply(self.wire_reply,before,self.plan['operations'][1],self.plan)
        self.assertEqual(result['source'],self.finalsource)
        for update in ({'window_id':'wrong'}, {'primitive_id':'other'},
                       {'observed':{**self.wire_reply['observed'],'line':[110,100,110,195]}},
                       {'observed':{**self.wire_reply['observed'],'net':'WRONG'}}):
            with self.assertRaises(ValueError):
                verify_branch_reply({**self.wire_reply,**update},before,self.plan['operations'][1],self.plan)

    def test_only_target_net_zindex_change_is_allowed(self):
        changed=self.finalsource.replace('"zIndex": 3','"zIndex": 9')
        report=self.audit_result({**self.audit,'source':changed})
        self.assertTrue(report['connectivity_matches'])
        self.assertFalse(report['baseline_source_preserved'])
        self.assertTrue(report['baseline_source_within_contract'])
        for source in (self.finalsource.replace('"value": "N", "zIndex"','"value": "WRONG", "zIndex"'),
                       self.finalsource.replace('"x": 700','"x": 701'),
                       self.finalsource.replace('"endX": 140','"endX": 139'),
                       self.finalsource+line('COMPONENT','unplanned',{'x':1,'y':-1}),
                       self.finalsource+line('ATTR','relevance',{'parentId':'old-wire','key':'Relevance','value':'["unexpected"]'})):
            with self.subTest(source=source[-100:]):
                try: result=self.audit_result({**self.audit,'source':source})
                except ValueError:continue
                self.assertFalse(result['connectivity_matches'])

    def test_final_audit_rechecks_old_pins_properties_new_pins_and_drc(self):
        for kind in ('old-pin','old-prop','new-pin','drc-added','drc-removed'):
            audit=deepcopy(self.audit);raw=json.loads(audit['raw_netlist'])
            if kind=='old-pin':raw['components']['netlist-key']['pinInfoMap']['pin']['net']='WRONG'
            if kind=='old-prop':raw['components']['netlist-key']['props']['Name']='WRONG'
            if kind=='new-pin':raw['components']['new']['pinInfoMap']['p']['net']='WRONG'
            if kind=='drc-added':audit['drc']+=[{'type':'warn','rule':'NEW','primitives':[{'primitiveId':'new-id','sheet':'d'}]}]
            if kind=='drc-removed':audit['drc']=[]
            audit['raw_netlist']=json.dumps(raw)
            result=self.audit_result(audit)
            self.assertFalse(result['connectivity_matches'] and result['drc_passed'],kind)

    def test_final_audit_does_not_accept_missing_receipts_or_extra_geometry(self):
        with self.assertRaises(ValueError):self.audit_result(mapping={'device':'new-id'})
        source=self.finalsource+line('LINE','extra',{
            'lineGroup':'old-wire','startX':110,'startY':-195,'endX':120,'endY':-195})
        with self.assertRaises(ValueError):self.audit_result({**self.audit,'source':source})

    def test_exact_interior_anchor_split_is_allowed_but_no_other_rewrite(self):
        source=self.finalsource.replace('"endX": 140','"endX": 110')
        source+=line('LINE','split-remainder',{
            'lineGroup':'old-wire','startX':110,'startY':-100,'endX':140,'endY':-100})
        result=self.audit_result({**self.audit,'source':source})
        self.assertTrue(result['connectivity_matches'])
        self.assertEqual(result['split_original_lines'],['old-line'])
        before={**self.before,'source':self.device_source}
        r={**self.wire_reply,'source':source}
        r['observed']={**r['observed'],'line':[80,100,110,100,110,100,110,195,110,100,140,100]}
        verify_branch_reply(r,before,self.plan['operations'][1],self.plan)
        for bad in (source.replace('"endX": 140','"endX": 139'),
                    source.replace('"endX": 110, "endY": -100','"endX": 109, "endY": -100'),
                    source.replace('"ticket": 1, "id": "old-line"','"ticket": 2, "id": "old-line"')):
            with self.assertRaises(ValueError):self.audit_result({**self.audit,'source':bad})

    def test_getter_edges_do_not_invent_polyline_segments(self):
        from easyeda_assist.drawing_branch import getter_segments
        self.assertEqual(getter_segments([0,0,10,0,20,20,20,30]),(('H',0,0,10),('V',20,20,30)))
        for value in ([0,0,10,0,10,10],[[0,0,10,0,10,10]],[0,0,10,10]):
            with self.assertRaises(ValueError):getter_segments(value)

    def test_target_line_ids_may_reassign_only_exact_permitted_edges(self):
        # The editor may reuse an old LINE ID for a new branch edge, while
        # copying its former geometry into a new LINE. Envelopes/styles stay.
        source=self.finalsource.replace('"startX": 80, "startY": -100, "endX": 140, "endY": -100',
                                        '"startX": 110, "startY": -100, "endX": 110, "endY": -195')
        source=source.replace(line('LINE','branch-line',{
            'lineGroup':'old-wire','startX':110,'startY':-100,'endX':110,'endY':-195}), '')
        source+=line('LINE','left-half',{'lineGroup':'old-wire','startX':80,'startY':-100,'endX':110,'endY':-100})
        source+=line('LINE','right-half',{'lineGroup':'old-wire','startX':110,'startY':-100,'endX':140,'endY':-100})
        self.assertTrue(self.audit_result({**self.audit,'source':source})['connectivity_matches'])
        duplicate=source+line('LINE','duplicate',{'lineGroup':'old-wire','startX':80,'startY':-100,'endX':110,'endY':-100})
        with self.assertRaises(ValueError):self.audit_result({**self.audit,'source':duplicate})

    def test_target_group_touching_any_other_group_fails_before_write(self):
        extra=line('WIRE','overlap',{})+line('LINE','overlap-line',{
            'lineGroup':'overlap','startX':80,'startY':-100,'endX':90,'endY':-100})
        before=deepcopy(self.before);before['source']+=extra
        bundle=deepcopy(self.bundle)
        from easyeda_assist.design_audit import source_digest
        bundle['audit']['source']+=extra;bundle['audit']['source_sha256']=source_digest(before['source'])
        bundle['inventory']['source']+=extra
        for q in bundle['geometry']:q['data']['source']+=extra
        path=write_new(self.root/'overlap-baseline.json',bundle)
        plan=deepcopy(self.plan);plan['incremental_baseline']={'path':str(path),'sha256':digest(path)}
        with self.assertRaisesRegex(ValueError,'导线组'):validate_branch(plan,before)

    def test_inherit_net_is_only_enabled_for_v5_wire_calls(self):
        from easyeda_assist.operations import execute_once, INCREMENTAL
        from easyeda_assist.drawing_circuit import operation_script
        b=Mock();execute_once(BRANCH,b,self.before,self.plan['operations'][1])
        self.assertTrue(b._circuit_operation.call_args.kwargs['inherit_net'])
        execute_once(INCREMENTAL,b,self.before,self.plan['operations'][1])
        self.assertNotIn('inherit_net',b._circuit_operation.call_args.kwargs)
        script=operation_script(self.before,self.plan['operations'][1],editor_version='4.1.100',inherit_net=True)
        self.assertIn('input.inherit_net?undefined:p.net',script)
        with self.assertRaises(ValueError):operation_script(self.before,self.plan['operations'][0],editor_version='4.1.100',inherit_net=True)

    def execution_bridge(self):
        after={**self.before,'source':self.device_source}
        r=reply(self.before,{**self.p,'expected_project_device_uuid':'local-dev'})
        r.update(window_id='w',source=self.device_source,project_evidence=library_record(after))
        r['project_evidence']['project_info']['editorVersion']='4.1.100'
        r['observed'].update(component_type='part',designator='R1',unique_id='new-uid')
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_components.return_value=self.bundle['inventory'];b.design_query.return_value=self.bundle['audit']
        b._circuit_operation.side_effect=[r,self.wire_reply];b.drawing_circuit_audit.return_value=self.audit
        return b

    def test_full_executor_binds_all_receipts_and_prevents_replay(self):
        b=self.execution_bridge()
        checked,_=preflight(self.project,self.plan)
        consent=approve_plan(self.project,self.plan,checked['binding']['plan_sha256'],'fixture explicit branch delegation','test')
        result=execute_plan(self.project,self.plan,consent,lambda:b)
        self.assertEqual(result['status'],'verified',result)
        self.assertEqual(result['completed'],2)
        self.assertFalse(Path(result['target_lock']).exists())
        self.assertEqual(b._circuit_operation.call_count,2)
        self.assertEqual(b._circuit_operation.call_args.kwargs,{'editor_version':'4.1.100','inherit_net':True})
        with self.assertRaisesRegex(ValueError,'禁止重放'):execute_plan(self.project,self.plan,consent,lambda:b)

    def failed_prefix(self):
        # Two receipt-bearing writes out of the real three-step contract.
        plan=deepcopy(self.plan)
        plan['operations'].append({**deepcopy(plan['operations'][1]),'id':'pending-wire'})
        plan['branch_anchors'].append({'operation':'pending-wire','primitive_id':'unused','net':'unused','x':1,'y':1})
        # Build the prefix archive directly from executor-style immutable records;
        # full plan preflight is tested independently above.
        from easyeda_assist.project import object_digest
        from easyeda_assist.drawing_incremental import claim_lock
        sha=object_digest(plan);run=self.root/'drawing-executions'/sha
        write_new(run/'plan.json',plan)
        write_new(run/'result.json',{'status':'uncertain','completed':1,'total':3,'target_lock':str(self.root/'locks')})
        write_new(run/'claim.json',{'plan_sha256':sha,'adapter':BRANCH})
        write_new(run/'input-snapshot.json',self.before)
        write_new(run/'incremental-baseline.json',self.bundle)
        b=self.execution_bridge();device=b._circuit_operation.side_effect.__next__()
        states=[self.before,{**self.before,'source':self.device_source}]
        for i,(op,r,state) in enumerate(zip(plan['operations'][:2],[device,self.wire_reply],states),1):
            write_new(run/f'{i:03d}-reply.json',r)
            write_new(run/f'{i:03d}-intent.json',{'operation':op,'target':self.before['identity'],'source_sha256':object_digest(state['source'])})
        claim_lock(self.root/'locks',run,{'adapter':BRANCH})
        return run

    def test_independent_prefix_restore_requires_complete_observed_receipts(self):
        from easyeda_assist.drawing_branch_experiment import review_prefix,restore_branch_prefix
        run=self.failed_prefix()
        original,baseline,state,prefix,mapping=review_prefix(run)
        self.assertEqual(mapping,self.mapping)
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_snapshot.side_effect=[state,original,original,original]
        from easyeda_assist.design_audit import make_audit
        live=make_audit(state,self.audit,'0'*64)
        b.design_query.side_effect=[live,baseline['audit'],baseline['audit'],baseline['audit']]
        b._execute_fixed.return_value=({'status':'restoration_pending','attempted':True},'w')
        with patch('time.sleep'):
            result=restore_branch_prefix(run,b,'fixture explicit experiment delegation','test')
        self.assertEqual(result['status'],'original-baseline-restored')
        self.assertEqual(b._execute_fixed.call_count,1)
        self.assertFalse((self.root/'locks').exists())

    def test_prefix_restore_rejects_third_attempt_or_live_source_drift(self):
        from easyeda_assist.drawing_branch_experiment import review_prefix,restore_branch_prefix
        run=self.failed_prefix()
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_snapshot.return_value={**self.before,'source':self.finalsource+'unrelated drift'}
        with self.assertRaises(ValueError):restore_branch_prefix(run,b,'fixture delegation','test')
        b._execute_fixed.assert_not_called()
        write_new(run/'003-intent.json',{'attempted':False})
        with self.assertRaises(ValueError):review_prefix(run)

    def test_v5_isolated_failed_first_creation_has_same_strict_cleanup(self):
        from easyeda_assist.drawing_incremental_cleanup import cleanup_failed_creation
        from easyeda_assist.drawing_incremental import claim_lock
        from easyeda_assist.design_audit import make_audit
        run=self.root/'isolated-failed'
        claim_lock(self.root/'locks',run,{'adapter':BRANCH})
        write_new(run/'result.json',{'status':'uncertain','completed':0,'target_lock':str(self.root/'locks')})
        write_new(run/'plan.json',self.plan);write_new(run/'input-snapshot.json',self.before)
        write_new(run/'001-reply.json',{'primitive_id':'new-id'})
        write_new(run/'incremental-baseline.json',self.bundle)
        raw=json.loads(self.audit['raw_netlist']);raw['components']['new']['pinInfoMap']['p']['net']=''
        state={**self.before,'source':self.device_source}
        live=make_audit(state,{**self.audit,'source':self.device_source,'raw_netlist':json.dumps(raw)},'0'*64)
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_snapshot.side_effect=[state,self.before];b.design_query.side_effect=[live,self.bundle['audit']]
        b._execute_fixed.return_value=({'status':'removed','attempted':True},'w')
        result=cleanup_failed_creation(run,b,'fixture explicit delegation','test')
        self.assertEqual(result['status'],'original-baseline-restored')
        self.assertEqual(b._execute_fixed.call_count,1)

    def test_branch_failure_preserves_active_lock_and_never_retries(self):
        # A failed first creation must not proceed to a wire or release its claim.
        b=Mock();b.health.return_value={'pendingRequests':0}
        b.drawing_components.return_value=self.bundle['inventory'];b.design_query.return_value=self.bundle['audit']
        b._circuit_operation.return_value={'attempted':True,'status':'uncertain','error':'test failure'}
        checked,_=preflight(self.project,self.plan)
        consent=approve_plan(self.project,self.plan,checked['binding']['plan_sha256'],'fixture delegation','test')
        result=execute_plan(self.project,self.plan,consent,lambda:b)
        self.assertEqual(result['status'],'uncertain')
        self.assertEqual(b._circuit_operation.call_count,1)
        self.assertTrue((Path(result['target_lock'])/'incremental-active.json').exists())

    def test_v5_does_not_enable_source_or_old_object_editing(self):
        self.assertEqual(operation_names(BRANCH),{'place_component','add_wire'})
        self.assertFalse(ADAPTERS[SOURCE]['dispatch_enabled'])


if __name__=='__main__':unittest.main()
