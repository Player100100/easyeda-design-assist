"""Target-bound preparation for Agent_test_stm32 PCB completion.

Fixed API programs only. Writes use a separately archived concrete work order;
the historical Agent_test experiment authorization does not apply here.
"""
from __future__ import annotations

import argparse
import base64
import json
import hashlib
import math
from copy import deepcopy
from pathlib import Path

from .bridge import discover
from .storage import initialize,new_id,write_new,external
from .pcb import validate_operation,verify_operation,drc_leaves,same_source,source_records,same_record,make_snapshot


def verify_additions(before,after,operations,steps):
    """Project each actual new record for the existing per-primitive verifier.

    Full old source/order/config/pin preservation is checked on the actual final
    batch, independently of these projections. Intermediate DRC is not a pass.
    """
    if not after['complete'] or len(operations)!=len(steps):raise ValueError('Incomplete additions batch')
    old,new=source_records(before['source']),source_records(after['source'])
    if any(k not in new or not same_record(v,new[k]) for k,v in old.items()):raise ValueError('Batch modified existing PCB source')
    if list(old)!=[k for k in new if k in old]:raise ValueError('Batch reordered existing PCB source')
    ids={s['created_id'] for s in steps}
    if len(ids)!=len(steps):raise ValueError('Duplicate created primitive identity')
    added={k:v for k,v in new.items() if k not in old}
    if len(added)!=len(ids) or {v[0]['id'] for v in added.values()}!=ids:raise ValueError('Unexpected new PCB records')
    for category,rows in before['collections'].items():
        final={v['id']:v for v in after['collections'][category]}
        if any(final.get(v['id'])!=v for v in rows):raise ValueError('Changed existing primitive getter: '+category)
        extra=set(final)-{v['id'] for v in rows}
        if extra!={s['created_id'] for s in steps if s['category']==category}:raise ValueError('Unexpected added getter objects')
    reports=[]
    for op,step in zip(operations,steps):
        if step['operation_id']!=op['id']:raise ValueError('Operation order differs')
        env,payload=next(v for v in added.values() if v[0]['id']==step['created_id'])
        projected=deepcopy(before)
        projected['source']=projected['source'].rstrip()
        if not projected['source'].endswith('|'):projected['source']+='|'
        projected['source']+='\n'+json.dumps(env)+'||'+json.dumps(payload)+'|'
        projected['source_sha256']=hashlib.sha256(projected['source'].encode()).hexdigest()
        row=next(v for v in after['collections'][step['category']] if v['id']==step['created_id'])
        if row['properties']!=step['properties']:raise ValueError('Immediate/final getter mismatch')
        projected['collections'][step['category']].append(row)
        reports.append(verify_operation(before,projected,op,step['created_id'],{'component_ids':[]},defer_drc=True))
    strip_length=lambda s:[{k:v for k,v in row.items() if k!='length'} for row in s['outputs']['nets']['value']]
    if strip_length(before)!=strip_length(after):raise ValueError('Batch changed net identity/color')
    for name,value in before['outputs'].items():
        if name not in ('drc','netlist','nets') and value!=after['outputs'].get(name):raise ValueError('Batch changed PCB configuration: '+name)
    return {'verified_additions':len(reports),'old_source_preserved':True,'drc_deferred':True,
            'final_drc_count':len(drc_leaves(after['outputs']['drc']['value']))}


def verify_normalized_additions(before,after,operations):
    """Exact line coverage under native collinear splits, standard library only.

    Not enabled in the generic executor. No union across nets/layers/widths.
    Junction tolerance is 0.051mil, based on the observed 0.1mil pad getter
    quantization; it does not relax clearance or the final detailed CAD DRC.
    Other source records, via identities, pads and all configuration stay exact.
    """
    if not after['complete']:raise ValueError('Incomplete normalized batch')
    old,new=source_records(before['source']),source_records(after['source'])
    for k,v in old.items():
        if k[0]=='LINE':continue
        if k not in new or not same_record(v,new[k]):raise ValueError('Normalized batch changed non-line source')
    for k,v in new.items():
        if k not in old and k[0] not in ('LINE','VIA'):raise ValueError('Unexpected normalized primitive')
    expected=[]
    for row in before['collections']['Line']:
        q=row['properties'];expected.append((q['Net'],q['Layer'],q['LineWidth'],(q['StartX'],q['StartY']),(q['EndX'],q['EndY'])))
    for op in operations:
        q=op['parameters']
        if op['operation']=='add_line':expected.append((q['net'],before['layer_ids'][q['layer']],q['width'],q['start'],q['end']))
    actual=[]
    for row in after['collections']['Line']:
        q=row['properties'];actual.append((q['Net'],q['Layer'],q['LineWidth'],(q['StartX'],q['StartY']),(q['EndX'],q['EndY'])))
        records=[v for k,v in new.items() if k[0]=='LINE' and v[0].get('id')==row['id']]
        if len(records)!=1:raise ValueError('Line source identity missing')
        payload=records[0][1]
        if payload.get('netName')!=q['Net'] or payload.get('layerId')!=q['Layer']:
            raise ValueError('Line source net/layer differs from getter')
        if any(abs(payload[k]-q[j])>.000050001 for k,j in [('startX','StartX'),('startY','StartY'),('endX','EndX'),('endY','EndY'),('width','LineWidth')]):
            raise ValueError('Line source geometry differs from getter')
    def covered(seg,candidates):
        net,layer,width,a,b=seg;dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if length<0.051:return True
        intervals=[]
        for n,l,w,c,d in candidates:
            # A previously placed wider power trunk may already cover a
            # proposed narrow neck.  Accept only width >= requested; never
            # accept a narrower track or a different net/layer.
            if n!=net or l!=layer or w+1e-7<width:continue
            tc=((c[0]-a[0])*dx+(c[1]-a[1])*dy)/length
            td=((d[0]-a[0])*dx+(d[1]-a[1])*dy)/length
            ts=sorted((tc,td))
            lo,hi=max(0,ts[0]),min(length,ts[1])
            if hi<lo or abs(td-tc)<1e-12:continue
            samples=[(c[0]+(d[0]-c[0])*(t-tc)/(td-tc),c[1]+(d[1]-c[1])*(t-tc)/(td-tc)) for t in (lo,hi)]
            if max(abs(dx*(p[1]-a[1])-dy*(p[0]-a[0]))/length for p in samples)>.051:continue
            if ts[1]>=-.051 and ts[0]<=length+.051:intervals.append(ts)
        end=0
        for lo,hi in sorted(intervals):
            if lo>end+.051:break
            end=max(end,hi)
        return end>=length-.051
    missing=[s for s in expected if not covered(s,actual)]
    extra=[s for s in actual if not covered(s,expected)]
    if missing or extra:
        raise ValueError('Native normalized line coverage differs: '+str({'missing':missing,'extra':extra}))
    oldvias={r['id']:r for r in before['collections']['Via']};newvias={r['id']:r for r in after['collections']['Via']}
    if any(newvias.get(k)!=v for k,v in oldvias.items()):raise ValueError('Changed existing via')
    expvia=[o['parameters'] for o in operations if o['operation']=='add_via']
    added=[v for k,v in newvias.items() if k not in oldvias]
    if len(added)!=len(expvia):raise ValueError('Added via count mismatch')
    for q in expvia:
        matches=[v for v in added if v['properties']['Net']==q['net'] and all(abs(v['properties'][k]-q[j])<1e-8 for k,j in [('X','x'),('Y','y'),('HoleDiameter','hole'),('Diameter','diameter')])]
        if len(matches)!=1:raise ValueError('Added via geometry mismatch')
    for kind in before['collections']:
        if kind not in ('Line','Via') and before['collections'][kind]!=after['collections'][kind]:raise ValueError('Changed non-route getter')
    for name in ('layers','footprints','stackup','origin','rules','net_rules','net_classes','differential_pairs','equal_length_groups','board'):
        if before['outputs'][name]!=after['outputs'][name]:raise ValueError('Changed configuration')
    return {'verified_explicit_operations':len(operations),'normalized_line_coverage':True,'drc_deferred':True,'final_drc_count':len(drc_leaves(after['outputs']['drc']['value']))}

PROJECT='cd574b3852b5453ba3c7fbb57d3b4d7a'
SCHEMATIC='286bc2ef702eb55e'
PAGES={'power':'37542addd6f7ee91','mcu':'60bc446fb524c460'}
TARGET="""
const project=await eda.dmt_Project.getCurrentProjectInfo();
if(!project||project.uuid!==__PROJECT__||project.friendlyName!=='Agent_test_stm32')throw new Error('目标不是Agent_test_stm32');
""".replace('__PROJECT__',json.dumps(PROJECT))

CREATE_SCRIPT=TARGET+"""
const boards=await eda.dmt_Board.getAllBoardsInfo();
const board=boards.find(b=>b.name==='STM32_Control');
if(!board||board.schematic?.uuid!==__SCHEMATIC__||board.pcb)throw new Error('新建PCB前板卡归属不符或已有PCB');
const uuid=await eda.dmt_Pcb.createPcb('STM32_Control');
if(!uuid)throw new Error('新建PCB未返回UUID');
return {project_uuid:project.uuid,pcb_uuid:uuid};
""".replace('__SCHEMATIC__',json.dumps(SCHEMATIC))


def pcb_target_script(pcb_uuid):
    return TARGET+'const pcbUuid='+json.dumps(pcb_uuid)+';'+"""
const boards=await eda.dmt_Board.getAllBoardsInfo();
const board=boards.find(b=>b.name==='STM32_Control');
if(board?.schematic?.uuid!==__SCHEMATIC__||board.pcb?.uuid!==pcbUuid)throw new Error('PCB/原理图归属不符');
let doc=await eda.dmt_SelectControl.getCurrentDocumentInfo();
if(doc?.uuid!==pcbUuid||doc.documentType!==3){
 const tab=await eda.dmt_EditorControl.openDocument(pcbUuid);
 if(!tab||!await eda.dmt_EditorControl.activateDocument(tab))throw new Error('无法聚焦PCB');
 doc=await eda.dmt_SelectControl.getCurrentDocumentInfo();
}
if(doc?.uuid!==pcbUuid||doc.documentType!==3)throw new Error('PCB焦点不符');
""".replace('__SCHEMATIC__',json.dumps(SCHEMATIC))


def open_page_script(name):
    return TARGET+'const id='+json.dumps(PAGES[name])+';'+"""
const pages=await eda.dmt_Schematic.getAllSchematicPagesInfo();
if(!pages.some(p=>p.uuid===id&&p.parentSchematicUuid===__SCHEMATIC__))throw new Error('目标原理图页不属于STM32_Control');
const tab=await eda.dmt_EditorControl.openDocument(id);
if(!tab||!await eda.dmt_EditorControl.activateDocument(tab))throw new Error('无法聚焦目标原理图');
return {document:await eda.dmt_SelectControl.getCurrentDocumentInfo()};
""".replace('__SCHEMATIC__',json.dumps(SCHEMATIC))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inventory','power','mcu','archive','create','import','pcb','batch','additions','pour','rebuild','route','route-batch','repair-delete','route-status','four-layers','save','save-export','manufacturing'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--nets',nargs='+')
    parser.add_argument('--job-id')
    parser.add_argument('--run-dir',type=Path,help='External evidence directory for a separately archived STM32 test run')
    args=parser.parse_args(argv)
    bridge=discover();root=external(args.run_dir) if args.run_dir else initialize()/'pcb-completion/2026-11-22'
    if args.action=='manufacturing':
        from .pcb_api import export_script
        before=bridge.pcb_capture()
        if before['identity']['project_uuid']!=PROJECT or not before['complete'] or drc_leaves(before['outputs']['drc']['value']):raise ValueError('制造导出前需要本工程完整零DRC PCB')
        reply,window=bridge._read_fixed(export_script(include_ipc=False),before['window_id'])
        path=write_new(root/(new_id('manufacturing-raw')+'.json'),reply)
        if not reply.get('stable') or not same_source(before['source'],reply['source']):raise ValueError('制造导出期间PCB变化')
        delivery=root/'delivery';delivery.mkdir(exist_ok=True)
        manifest={'identity':reply['identity'],'raw_evidence':str(path),'files':{},'ipc_d356':'API returned null/ipcFileName error; not included'}
        for key,item in reply['files'].items():
            data=base64.b64decode(item['base64'],validate=True)
            if len(data)!=item['size']:raise ValueError('制造文件字节长度不符')
            suffix='.zip' if key=='gerber' else '.csv';out=delivery/(key+suffix)
            with out.open('xb') as f:f.write(data)
            manifest['files'][key]={'path':str(out),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'original_name':item['name']}
        write_new(root/'final-manufacturing.json',manifest);print(json.dumps(manifest,ensure_ascii=False,indent=2));return 0
    if args.action in ('save','save-export'):
        from .pcb_api import _preamble
        from .pcb_execution import export_files
        target=json.loads((root/'created-pcb.json').read_text('utf8'))
        before=bridge.pcb_capture()
        if not before.get('complete') or drc_leaves(before['outputs']['drc']['value']):raise ValueError('Final save/export requires complete snapshot and zero detailed DRC')
        write_new(root/(new_id('final-before-save')+'.json'),before)
        code=pcb_target_script(target['pcb_uuid'])+_preamble()+"const baseline=await pcbRead(true);function count(v){if(Array.isArray(v))return v.reduce((a,x)=>a+count(x),0);if(v&&typeof v==='object'){if(Array.isArray(v.list))return count(v.list);return 1;}return 0;}if(!baseline.complete||count(baseline.outputs.drc.value))throw new Error('保存前DRC未清零');return {saved:await eda.pcb_Document.save()};"
        reply,window=bridge._execute_fixed(code,before['window_id']);write_new(root/(new_id('final-save-reply')+'.json'),reply)
        if reply.get('saved') is not True:raise ValueError('PCB save not confirmed')
        final=bridge.pcb_capture(window)
        if not final.get('complete') or drc_leaves(final['outputs']['drc']['value']):raise ValueError('Saved PCB final DRC not zero')
        path=write_new(root/(new_id('final-pcb')+'.json'),final)
        if not same_source(before['source'],final['source']):raise ValueError('Save changed PCB geometry; inspect final source')
        if args.action=='save':
            print(json.dumps({'saved':True,'snapshot_path':str(path)},ensure_ascii=False,indent=2));return 0
        exported=export_files(bridge,window);write_new(root/'final-manufacturing.json',exported)
        print(json.dumps({'saved':True,'snapshot_path':str(path),'manufacturing':exported},ensure_ascii=False,indent=2));return 0
    if args.action=='four-layers':
        target=json.loads((root/'created-pcb.json').read_text('utf8'))
        write_new(root/'four-layer-work-order.json',{'user_constraint':'双层板或者四层板，10cm*10cm以内','decision':'Four copper layers within existing 95x85mm outline; preserve power/crystal/decoupling paths, add ordinary signal channels on inner layers; finish test PCB connectivity/DRC','scope':'Same STM32_Control PCB, no schematic edits'})
        before=bridge.pcb_capture();write_new(root/'four-layer-before.json',before)
        reply,window=bridge._execute_fixed(pcb_target_script(target['pcb_uuid'])+"return {changed:await eda.pcb_Layer.setTheNumberOfCopperLayers(4)};",before['window_id'])
        write_new(root/'four-layer-reply.json',reply)
        after=bridge.pcb_capture(window);path=write_new(root/'four-layer-after.json',after)
        if reply.get('changed') is not True or not after['complete']:raise ValueError('Four-layer change not verified')
        old,new=source_records(before['source']),source_records(after['source'])
        if before['collections']!=after['collections']:raise ValueError('Layer change altered geometry')
        if any(k not in new or not same_record(v,new[k]) for k,v in old.items() if k[0]!='LAYER'):raise ValueError('Layer change altered non-layer source')
        print(json.dumps({'snapshot_path':str(path),'layers':after['layer_ids']},ensure_ascii=False));return 0
    if args.action=='route-status':
        if not args.job_id or not args.job_id.startswith('stm32-route-'):raise ValueError('Expected STM32 route job ID')
        reply,window=bridge._read_fixed(TARGET+'const state=eda.sys_Storage.getExtensionUserConfig('+json.dumps(args.job_id)+');if(!state)throw new Error("路由状态不存在");return {state};')
        path=write_new(root/(new_id('route-status')+'.json'),reply)
        print(json.dumps({'path':str(path),**reply},ensure_ascii=False,indent=2));return 0
    if args.action=='repair-delete':
        from .pcb_api import repair_delete_script
        if not args.plan:raise ValueError('Concrete repair plan required')
        plan=json.loads(args.plan.read_text('utf8'));target=json.loads((root/'created-pcb.json').read_text('utf8'))
        if plan['project_uuid']!=PROJECT or plan['pcb_uuid']!=target['pcb_uuid']:raise ValueError('Repair target mismatch')
        before=json.loads(Path(plan['baseline']['path']).read_text('utf8'))
        if hashlib.sha256(Path(plan['baseline']['path']).read_bytes()).hexdigest()!=plan['baseline']['sha256']:raise ValueError('Repair baseline hash mismatch')
        batch=root/'batches'/plan['id'];write_new(batch/'claim.json',plan)
        reply,window=bridge._execute_fixed(repair_delete_script(before,plan['removals']),before['window_id'])
        write_new(batch/'reply.json',reply)
        if reply.get('status')!='observed':raise ValueError('Repair deletion uncertain; read actual state')
        after=make_snapshot(reply['after'],window,bridge.port)
        old,new=source_records(before['source']),source_records(after['source']);ids={i for rows in plan['removals'].values() for i in rows}
        def removed(k,v):
            return v[0].get('id') in ids or k[0]=='POURED' and any(i in str(v[0].get('id')) for i in ids)
        if any(k not in new or not same_record(v,new[k]) for k,v in old.items() if not removed(k,v)) or set(new)-set(old):raise ValueError('Repair deletion changed unplanned source')
        for kind,rows in before['collections'].items():
            if [x for x in rows if x['id'] not in plan['removals'].get(kind,[])]!=after['collections'][kind]:raise ValueError('Repair deletion changed unplanned getters: '+kind)
        path=write_new(batch/'after.json',after);print(json.dumps({'snapshot_path':str(path),'removed':len(ids)},ensure_ascii=False));return 0
    if args.action in ('route','route-batch'):
        from .pcb_api import native_route_script
        before=bridge.pcb_capture()
        if not before.get('complete'):raise ValueError('需要稳定完整PCB快照')
        route_nets=args.nets or ['NRST','PB2','DBUS_BASE','LED_3V3','CC1','CC2','PB10','PB1','PB0','PA15','PA12','PA11','PA9','PA8','PA7','PA6','PA5','PA4','PA3','PA2']
        known={n['net'] for n in before['outputs']['nets']['value']}
        if not set(route_nets)<=known or set(route_nets)&{'GND','SW12','SW5','OSC_IN','OSC_OUT','VIN24','VIN24_RAW','VIN24_FUSED','V12'}:raise ValueError('网络不在本次普通信号/低电流电源补线范围')
        if args.action=='route-batch' and (not args.nets or set(route_nets)&{'V3V3','V5','USB5','BUCK5','VDDA'}):raise ValueError('协同批次只允许显式的普通信号网络')
        write_new(root/(new_id('native-route-intent')+'.json'),{'before':before,'nets':route_nets,'power_assumptions':'3V3 0.15A, 5V 0.5A; native default widths require final review'})
        job_id=new_id('stm32-route')
        reply,window=bridge._execute_fixed(native_route_script(before,route_nets,job_id,together=args.action=='route-batch'),before['window_id'])
        path=write_new(root/(new_id('ordinary-route-reply')+'.json'),reply)
        if reply.get('status')!='started':raise ValueError('Uncertain route; inspect '+str(path))
        print(json.dumps({'reply_path':str(path),**reply},ensure_ascii=False,indent=2));return 0
    if args.action=='rebuild':
        from .pcb_api import rebuild_pours_script
        before=bridge.pcb_capture()
        ids=[x['id'] for x in before['collections']['Pour'] if x['properties'].get('Net')=='GND']
        if not before.get('complete') or len(ids)!=2:raise ValueError('需要两个已稳定的GND铜区')
        reply,window=bridge._execute_fixed(rebuild_pours_script(before,ids),before['window_id'])
        path=write_new(root/(new_id('ground-rebuild-reply')+'.json'),reply)
        if reply.get('status')!='observed':raise ValueError('Uncertain copper rebuild; inspect '+str(path))
        after=make_snapshot(reply['after'],window,bridge.port);after_path=write_new(root/(new_id('ground-rebuild-after')+'.json'),after)
        print(json.dumps({'status':'observed','reply_path':str(path),'snapshot_path':str(after_path),'poured':len(after['collections']['Poured']),'fill':len(after['collections']['Fill']),'drc':len(drc_leaves(after['outputs']['drc']['value']))},ensure_ascii=False,indent=2));return 0
    if args.action=='pour':
        from .pcb_api import pours_script
        target=json.loads((root/'created-pcb.json').read_text('utf8'))
        before=bridge.pcb_capture()
        before_path=write_new(root/(new_id('ground-pour-before')+'.json'),before)
        if not before.get('complete'):raise ValueError('Latest PCB baseline incomplete')
        polygon=[0,0,'L',round(95/.0254,4),0,round(95/.0254,4),round(85/.0254,4),0,round(85/.0254,4),0,0]
        reply,window=bridge._execute_fixed(pours_script(before,polygon),before['window_id'])
        reply_path=write_new(root/(new_id('ground-pour-reply')+'.json'),reply)
        if reply.get('status')!='observed':raise ValueError('Uncertain ground pour; inspect raw reply before retry')
        after=make_snapshot(reply['after'],window,bridge.port)
        old,new=source_records(before['source']),source_records(after['source'])
        if any(k not in new or not same_record(v,new[k]) for k,v in old.items() if k[0] not in ('POURED','FILL')):raise ValueError('Ground pour changed existing PCB source')
        pours=[x for x in after['collections']['Pour'] if x['id'] not in {r['id'] for r in before['collections']['Pour']}]
        if len(pours)!=2 or any(x['properties'].get('Net')!='GND' for x in pours):raise ValueError('Ground pour readback mismatch')
        after_path=write_new(root/(new_id('ground-pour-after')+'.json'),after)
        print(json.dumps({'status':'observed','before_path':str(before_path),'reply_path':str(reply_path),'snapshot_path':str(after_path),'new_pours':[x['id'] for x in pours],'drc':len(drc_leaves(after['outputs']['drc']['value']))},ensure_ascii=False,indent=2));return 0
    if args.action in ('batch','additions'):
        if not args.plan:raise ValueError('batch requires a concrete plan file')
        plan=json.loads(args.plan.read_text('utf8'))
        target=json.loads((root/'created-pcb.json').read_text('utf8'))
        order=json.loads((root/'work-order.json').read_text('utf8'))
        if order.get('execution_mode')!='explicitly-delegated-test' or not order.get('experimental'):
            raise ValueError('This helper only executes the delegated STM32 toolchain test')
        if plan.get('project_uuid')!=PROJECT or plan.get('pcb_uuid')!=target['pcb_uuid']:
            raise ValueError('Plan target mismatch')
        baseline_path=Path(plan['baseline']['path'])
        if not baseline_path.is_absolute():raise ValueError('Baseline must be an absolute external path')
        baseline_bytes=baseline_path.read_bytes()
        if hashlib.sha256(baseline_bytes).hexdigest()!=plan['baseline']['sha256']:
            raise ValueError('Plan baseline file changed')
        expected=json.loads(baseline_bytes)
        for op in plan['operations']:validate_operation(op)
        batch=root/'batches'/plan['id']
        write_new(batch/'claim.json',{'plan_sha256':hashlib.sha256(args.plan.read_bytes()).hexdigest(),'plan':plan,'work_order':order})
        reply,window=bridge._execute_fixed(pcb_target_script(target['pcb_uuid'])+'return {document:doc};')
        before=bridge.pcb_capture(window)
        write_new(batch/'before.json',before)
        if not before['complete']:raise ValueError('Incomplete PCB baseline')
        if expected['identity']!=before['identity'] or not same_source(expected['source'],before['source']) or expected['collections']!=before['collections']:
            raise ValueError('Live PCB changed since concrete plan preparation')
        if args.action=='additions':
            from .pcb_api import additions_script
            bridge.timeout=max(bridge.timeout,180)
            reply,window=bridge._execute_fixed(additions_script(before,plan['operations']),window)
            write_new(batch/'reply.json',reply)
            if reply.get('status')!='observed':raise ValueError('Uncertain additions; inspect raw reply')
            after=make_snapshot(reply['after'],window,bridge.port)
            checked=verify_normalized_additions(before,after,plan['operations']) if plan.get('native_collinear_normalization') else verify_additions(before,after,plan['operations'],reply['steps'])
            write_new(batch/'verification.json',checked)
            path=write_new(batch/'after.json',after)
            print(json.dumps({'snapshot_path':str(path),**checked},ensure_ascii=False,indent=2))
            return 0
        for i,op in enumerate(plan['operations']):
            write_new(batch/(f'{i:04d}-intent.json'),op)
            reply=bridge.pcb_operation(before,op)
            write_new(batch/(f'{i:04d}-reply.json'),reply)
            if reply.get('status')!='observed':raise ValueError('Uncertain PCB write; inspect archived reply before continuing')
            after=reply['after']
            checked=verify_operation(before,after,op,reply.get('created_id'),{'component_ids':list(c['id'] for c in before['collections']['Component'])},defer_drc=True)
            write_new(batch/(f'{i:04d}-verification.json'),checked)
            before=after
            print(json.dumps({'step':i+1,'total':len(plan['operations']),'id':op['id'],'created_id':reply.get('created_id'),'drc':len(drc_leaves(before['outputs']['drc']['value']))}),flush=True)
        path=write_new(batch/'after.json',before)
        result={'snapshot_path':str(path),'operations':len(plan['operations']),'complete':before['complete']}
    elif args.action=='create':
        order={
            'project_uuid':PROJECT,'schematic_uuid':SCHEMATIC,
            'user_request':'完成目前我在嘉立创打开的这个工程的PCB绘制，原理图已经由本项目绘制完毕，试图完成PCB绘制',
            'user_scope':'作为工具链测试板，完成布局、连接和 DRC 闭环',
            'user_constraints':'双层板或者四层板，10cm*10cm以内',
            'execution_mode':'explicitly-delegated-test','experimental':True,
            'board':'STM32_Control','outline_mm':[95,85],'layers':2,
            'operations':['create_associated_pcb','import_schematic','place_53_components','add_outline','route_critical_paths','route_explicit_ordinary_nets','add_ground_vias_and_pours','repair_test_pcb_drc','save_pcb','export_manufacturing_and_project'],
            'preserve':['all_schematic_sources_and_nets','original_Board1_P1_PCB1'],
            'validation':['component/pad/net correspondence','placement_readback','full_source_diff','detailed_DRC','export_integrity'],
            'failure':'archive uncertain result and read actual state before any further write; never repeat creation',
            'basis':'Existing schematic handoff and current whole netlist; regulated24V,12V0.5A,5V0.5A,3V3_0.15A are design assumptions, not measured',
        }
        write_new(root/'work-order.json',order)
        reply,window=bridge._execute_fixed(CREATE_SCRIPT)
        path=write_new(root/'created-pcb.json',reply)
        result={'evidence_path':str(path),**reply}
    elif args.action in ('import','pcb'):
        target=json.loads((root/'created-pcb.json').read_text('utf8'))
        code=pcb_target_script(target['pcb_uuid'])
        if args.action=='import':
            code+="if((await eda.pcb_PrimitiveComponent.getAll()).length)throw new Error('PCB非空，拒绝重复导入');return {import_return:await eda.pcb_Document.importChanges("+json.dumps(SCHEMATIC)+")};"
        else:code+='return {document:doc};'
        reply,window=bridge._execute_fixed(code)
        path=write_new(root/(new_id(args.action+'-open')+'.json'),reply)
        snapshot=bridge.pcb_capture(window)
        spath=write_new(root/(new_id('pcb-snapshot')+'.json'),snapshot)
        result={'evidence_path':str(path),'snapshot_path':str(spath),'complete':snapshot['complete'],
                'counts':{k:len(v) for k,v in snapshot['collections'].items()},**reply}
    elif args.action in PAGES:
        reply,window=bridge._execute_fixed(open_page_script(args.action))
        path=write_new(root/(new_id(args.action+'-open')+'.json'),reply)
        snapshot=bridge.schematic(window)
        source=bridge.drawing_snapshot(window)
        spath=write_new(root/(new_id(args.action+'-netlist')+'.json'),snapshot)
        rpath=write_new(root/(new_id(args.action+'-source')+'.json'),source)
        result={'open_path':str(path),'netlist_path':str(spath),'source_path':str(rpath),
                'components':snapshot['component_count']}
    elif args.action=='archive':
        script=TARGET+"""
const file=await eda.sys_FileManager.getProjectFile('pcb-before',undefined,'epro2');
if(!file||file.size>60*1024*1024)throw new Error('完整工程归档失败');
const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
return {project_uuid:project.uuid,name:file.name,size:bytes.length,base64:btoa(binary)};
"""
        reply,window=bridge._read_fixed(script)
        path=write_new(root/(new_id('project-before')+'.json'),reply)
        raw=base64.b64decode(reply['base64'],validate=True)
        if len(raw)!=reply['size']:raise ValueError('工程文件字节长度不一致')
        archive=path.with_suffix('.epro2')
        with archive.open('xb') as f:f.write(raw)
        result={'evidence_path':str(path),'archive_path':str(archive),'bytes':len(raw)}
    else:
        reply,window=bridge._read_fixed(TARGET+"""
return {project,boards:await eda.dmt_Board.getAllBoardsInfo(),
 pcbs:await eda.dmt_Pcb.getAllPcbsInfo(),pages:await eda.dmt_Schematic.getAllSchematicPagesInfo()};
""")
        path=write_new(root/(new_id('inventory')+'.json'),reply)
        result={'evidence_path':str(path),'boards':[{'name':b['name'],'schematic':(b.get('schematic') or {}).get('uuid'),'pcb':b.get('pcb')} for b in reply['boards']]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0
