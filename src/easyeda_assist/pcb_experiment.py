"""Fixed, explicitly delegated live experiments on the Agent_test PCB only.

This is independent of normal PCB dispatch and never fabricates plan consent.
Every write requires a saved prospective case and records the actual delegation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .bridge import discover
from .storage import digest, initialize, new_id, read_json, write_new, external
from .pcb import compare, validate_snapshot, verify_operation, source_records, same_record, number, text
from .pcb_execution import _durable

PROJECT = "2128f4e84aa04c0fb3ef359b97f317ff"
PCB = "0c028076d905ee4e"
SCHEMATIC = "e6cf5d532bd1b867"

_TARGET = """
const project=await eda.dmt_Project.getCurrentProjectInfo();
if(!project||project.uuid!==__PROJECT__||project.friendlyName!=='Agent_test')throw new Error('实验只允许Agent_test指定工程');
""".replace("__PROJECT__", json.dumps(PROJECT))

INVENTORY = _TARGET + """
return {project,document:await eda.dmt_SelectControl.getCurrentDocumentInfo(),
 boards:await eda.dmt_Board.getAllBoardsInfo(),pcbs:await eda.dmt_Pcb.getAllPcbsInfo(),
 editor_version:eda.sys_Environment.getEditorCurrentVersion()};
"""


def open_script(document_uuid):
    if document_uuid not in {PCB, SCHEMATIC}:
        raise ValueError("只允许Agent_test现有原理图或PCB")
    return _TARGET + "const uuid=" + json.dumps(document_uuid) + ";" + """
const boards=await eda.dmt_Board.getAllBoardsInfo();
if(!boards.some(b=>b.pcb?.uuid===uuid||b.schematic?.page?.some(p=>p.uuid===uuid)))throw new Error('目标不属于实验板卡');
const tab=await eda.dmt_EditorControl.openDocument(uuid);
if(!tab)throw new Error('无法打开实验文档');
const activated=await eda.dmt_EditorControl.activateDocument(tab);
return {tab,activated,document:await eda.dmt_SelectControl.getCurrentDocumentInfo()};
"""


def run_case(path, bridge):
    case = read_json(path)
    if case.get('kind') != 'pcb-delegated-trial' or not case.get('user_message') or not case.get('reference'):
        raise ValueError('缺少真实测试委托及前瞻实验方案')
    baseline = external(Path(case['baseline']['path']))
    if digest(baseline) != case['baseline']['sha256']:
        raise ValueError('实验基线已变化')
    before = validate_snapshot(read_json(baseline))
    if before['identity']['project_uuid'] != PROJECT or before['identity']['document_uuid'] != PCB:
        raise ValueError('实验目标不是指定Agent_test PCB1')
    if before['identity']['editor_version'] != '4.1.100':
        raise ValueError('实验只适用于4.1.100')
    folder = initialize() / 'pcb-live-tests' / '2026-11-22-Agent_test' / digest(path)
    folder.mkdir(parents=True, exist_ok=False)
    _durable(folder / 'case.json', case)
    _durable(folder / 'intent.json', {'case_sha256': digest(path), 'baseline':case['baseline'],
                                     'operation':case.get('operation'), 'action':case['action']})
    try:
        if case['action'] == 'import-empty-pcb':
            if any(before['collections'].get(k) for k in before['collections']):
                raise ValueError('同步实验仅允许空PCB')
            from .pcb_api import _preamble
            script = _TARGET + _preamble() + 'const expected=' + json.dumps(before['source']) + ';' + """
const identity=await pcbIdentity();
if(identity.document_uuid!==__PCB__||identity.editor_version!=='4.1.100')throw new Error('同步目标不匹配');
if(comparablePcb(expected)!==comparablePcb(await eda.sys_FileManager.getDocumentSource()))throw new Error('空板基线变化');
const imported=await eda.pcb_Document.importChanges(__SCHEMATIC__);
return {imported,identity};
""".replace('__PCB__', json.dumps(PCB)).replace('__SCHEMATIC__', json.dumps('79296b3cd9864c55'))
            reply, _ = bridge._execute_fixed(script, before['window_id'])
        elif case['action'] == 'restore-layout':
            positions=case['positions']
            if not isinstance(positions,list) or len(positions)!=17 or len({x.get('primitive_id') for x in positions})!=17:
                raise ValueError('位置恢复只允许已归档的17个测试器件')
            for item in positions:
                if set(item)!={'primitive_id','x','y','rotation'} or not all(number(item[k]) for k in ('x','y','rotation')):
                    raise ValueError('恢复位置数据无效')
            from .pcb_api import _preamble
            script=_TARGET+_preamble()+'const positions='+json.dumps(positions)+';const expected='+json.dumps(before['source'])+';'+"""
const identity=await pcbIdentity();
if(identity.document_uuid!==__PCB__||identity.editor_version!=='4.1.100'||comparablePcb(expected)!==comparablePcb(await eda.sys_FileManager.getDocumentSource()))throw new Error('恢复测试基线不一致');
const completed=[];
for(const p of positions){
 if(!equal(identity,await pcbIdentity()))throw new Error('测试文档变化');
 const item=await eda.pcb_PrimitiveComponent.get(p.primitive_id);if(!item||item.getState_PrimitiveLock())throw new Error('器件不存在或被锁定');
 const pending=item.toAsync();pending.setState_X(p.x);pending.setState_Y(p.y);pending.setState_Rotation(p.rotation);await pending.done();completed.push(p.primitive_id);
}
return {identity,completed};
""".replace('__PCB__',json.dumps(PCB))
            reply,_=bridge._execute_fixed(script,before['window_id'])
        elif case['action'] == 'configure-pour':
            p=case['parameters']
            if set(p) != {'primitive_id','name','width'} or not number(p['width'],positive=True) or not text(p['name']):
                raise ValueError('铜区属性实验参数无效')
            from .pcb_api import _preamble
            script=_TARGET+_preamble()+'const p='+json.dumps(p)+';const expected='+json.dumps(before['source'])+';'+"""
const identity=await pcbIdentity();
if(identity.document_uuid!==__PCB__||identity.editor_version!=='4.1.100'||comparablePcb(expected)!==comparablePcb(await eda.sys_FileManager.getDocumentSource()))throw new Error('铜区实验基线不一致');
const item=await eda.pcb_PrimitivePour.get(p.primitive_id);if(!item)throw new Error('铜区不存在');
const pending=item.toAsync();pending.setState_LineWidth(p.width);pending.setState_PourName(p.name);await pending.done();
return {identity,primitive_id:p.primitive_id};
""".replace('__PCB__',json.dumps(PCB))
            reply,_=bridge._execute_fixed(script,before['window_id'])
        elif case['action'] == 'operation':
            reply = bridge.pcb_operation(before, case['operation'])
        else:
            raise ValueError('未知固定实验动作')
        _durable(folder / 'reply.json', reply)
        after = bridge.pcb_capture(before['window_id'])
        _durable(folder / 'after.json', after)
        if case['action'] == 'restore-layout':
            actual={c['id']:c['properties'] for c in after['collections']['Component']}
            if any(any(not number(actual[p['primitive_id']].get(k)) or not math.isclose(actual[p['primitive_id']][k],p[v],rel_tol=0,abs_tol=1e-8)
                       for k,v in [('X','x'),('Y','y'),('Rotation','rotation')]) for p in positions):
                raise ValueError('恢复位置回读不一致')
            result=verify_operation(before,after,{'operation':'auto_place','parameters':{}},None,case['scope'])
        elif case['action'] == 'configure-pour':
            old,new=source_records(before['source']),source_records(after['source'])
            if set(old)!=set(new):raise ValueError('铜区属性实验改变了对象集合')
            for k in old:
                if old[k][0].get('type')=='POUR' and old[k][0].get('id')==p['primitive_id']:
                    value=dict(new[k][1]);value['width']=old[k][1]['width'];value['name']=old[k][1]['name']
                    if not same_record(old[k],(new[k][0],value)):raise ValueError('铜区实验出现额外属性变化')
                    if new[k][1].get('width')!=p['width'] or new[k][1].get('name')!=p['name']:raise ValueError('铜区属性未按请求更新')
                elif not same_record(old[k],new[k]):raise ValueError('铜区实验改变了范围外对象')
            result=compare(before,after)
            if result['new_drc']:raise ValueError('铜区实验新增DRC')
        elif case['action'] == 'operation':
            if reply.get('status') != 'observed':
                raise ValueError('操作未完成：' + str(reply.get('error')))
            if case['operation']['operation'] in {'auto_place','route_nets'}:
                if not isinstance(reply.get('engine_result'),dict) or reply['engine_result'].get('success') is not True:
                    raise ValueError('原生布局/布线引擎未报告完成，已保留真实结果')
            result = verify_operation(before, after, case['operation'], reply.get('created_id'), case['scope'])
        else:
            if not after['collections'].get('Component'):
                result = {'status':'awaiting_client_apply', 'import_api_return':reply.get('imported'),
                          'component_count':0, 'reason':'importChanges只打开待应用的导入信息对话框'}
                _durable(folder / 'verification.json', result)
                return {**result, 'run_dir':str(folder),'after_path':str(folder / 'after.json')}
            result = compare(before, after)
        _durable(folder / 'verification.json', result)
        return {'status':'verified','run_dir':str(folder),'after_path':str(folder / 'after.json'),
                'summary':result,'engine_result':reply.get('engine_result')}
    except Exception as exc:
        _durable(folder / 'failure.json', {'status':'uncertain','error':str(exc)})
        return {'status':'uncertain','run_dir':str(folder),'error':str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "open-pcb", "open-schematic", "capture", "run-case"))
    parser.add_argument("--window-id")
    parser.add_argument("--case", type=Path)
    args = parser.parse_args(argv)
    bridge = discover()
    folder = initialize() / "pcb-live-tests" / "2026-11-22-Agent_test"
    if args.action == 'run-case':
        if not args.case:
            raise ValueError('需要--case具体前瞻实验方案')
        data = run_case(args.case, bridge)
    elif args.action == "capture":
        data = bridge.pcb_capture(args.window_id)
        if data["identity"]["project_uuid"] != PROJECT or data["identity"]["document_uuid"] != PCB:
            raise ValueError("捕获目标不是Agent_test PCB1")
        data = {"snapshot_path": str(write_new(folder / (new_id("capture") + ".json"), data)),
                "complete": data["complete"], "read_errors": data["read_errors"],
                "outputs": {k: v.get("error", v["status"]) for k,v in data["outputs"].items()},
                "counts": {k:len(v) for k,v in data["collections"].items()}, "layer_ids": data["layer_ids"]}
    else:
        script = INVENTORY if args.action == "inventory" else open_script(PCB if args.action == "open-pcb" else SCHEMATIC)
        data, window = bridge._execute_fixed(script, args.window_id)
        path = write_new(folder / (new_id(args.action) + ".json"), {"window_id": window, **data})
        data = {"evidence_path": str(path), **data}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
